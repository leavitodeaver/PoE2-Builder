r"""The probe server (PERF22, 24-25 Nov 2026): runs the lanes' read-only probes at once, OUTSIDE the PC queue.

Why: the lanes ran ~260 probe bookings in ~2.5 days, a median 0.5 min of running each, after a median 15.3 min wait
for a queue place. The user, 25 Nov ~01:10: "if it's that light, keep it going at all times outside the queue".

    python tools/probe_server.py start     the standing server and its keeper (each restarts the other if it dies)
    python tools/probe_server.py stop      no new probes; running ones finish (12 min at most); both end
    python tools/probe_server.py status    what is up, held or running, what waits
    python tools/probe_server.py serve     (what the keeper starts; not for lanes)

Lanes use tools/probe.py (one line from Python or the command line); it books a normal queue place whenever the
server is down, held, busy, or the probe needs more than the server gives.

EXACTLY A BOOKING'S ANSWER: every probe runs in a FRESH process, with the probe's own command line, folder and the
sender's POE2_* / PYTHON* / UV_* settings (the server's own are removed first), and PC_QUEUE_KIND=test as inside a
booking (so builder/pob/slots.py limits its engines the same way). Nothing is shared between two probes.

THE OTHER PROJECTS' SAFETY (the user's condition):
- At most JOBS_AT_ONCE (2) probes at once, all in ONE Windows job object of the server's own: 4 GB of committed memory
  and 24 processes for the server and all its probes together, a hard cap of CPU_PERCENT (25 %) of the processor,
  Below Normal, never on the user's processors 0 and 1 (the limits a PoE2 queue run has). A server started from a
  manager's session is not held back by worker_clamp.py (it leaves managers' sessions alone), so the server sets all of
  this itself; if the job can't be made, it still sets its own priority and processors, which its probes inherit.
  The job kills every probe if the server dies (no orphans).
- One set of 6 LuaJIT slots for all its probes (POE2_SLOT_SET): a lane's suite never loses a slot to a probe.
- HELD whenever the queue would hold or pause a PoE2 run, read with pc_queue.py's OWN checks (imported, never changed):
  `_user_holds("poe2", "test", no_gpu=True)` (HOLD_poe2, USER_IN_GAME, the user's CompanionVR game, SteamVR) and
  `_window_open(...)` (a measuring window open); and also while Beastking's MEASURING.lock exists (pc_queue.MEASURING).
  Held: no probe starts, and running probes are SUSPENDED (every process of them), as the queue pauses a PoE2 run;
  they go on GRACE_SECONDS (30, the queue's GAME_GRACE_S) after the hold ends. While CompanionVR's gpu_test.lock exists
  no probe starts (the queue starts nothing then either). A probe held longer than its own minutes is stopped and its
  client books it normally.

Spool (one folder for every lane): SPOOL\inbox\<id>.json (requests), SPOOL\claimed\, SPOOL\out\<id>.log and
<id>.json (the answer), SPOOL\servers\<name>.json (the heartbeat), SPOOL\keeper.json, SPOOL\server.log (the log),
SPOOL\STOP (written by `stop`).
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPOOL = Path(os.environ.get("POE2_PROBE_SPOOL") or r"C:\PROJECTS\PoE2 Builder\scratch\probe_server")
PC_QUEUE = Path(r"C:\PROJECTS\PCQueue\pc_queue.py")
JOBS_AT_ONCE = 2
MEMORY_MB = 4096  # the server and its probes together (a plain PoE2 queue run's limit)
PROCESSES = 24  # likewise
CPU_PERCENT = 25  # a hard cap on the whole job (4 of the 16 logical processors' worth)
GRACE_SECONDS = float(os.environ.get("POE2_PROBE_HOLD_GRACE") or 30)  # the queue's GAME_GRACE_S
BEAT_SECONDS = 2.0
STALE_SECONDS = 20.0  # a heartbeat older than this: the server is gone or stuck
HOLD_POLL_SECONDS = 3.0
FORWARDED = ("POE2_", "PYTHON", "UV_")  # the client's own settings that reach its probe (and only the client's)
CREATE_NO_WINDOW, DETACHED, NEW_GROUP, BREAKAWAY = 0x08000000, 0x00000008, 0x00000200, 0x01000000


def _now() -> float:
    return time.time()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _log(spool: Path, text: str) -> None:
    try:
        with open(spool / "server.log", "a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {text}\n")
    except OSError:
        pass


def _alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return code.value == 259  # STILL_ACTIVE


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _detached(command: list[str], log: Path) -> subprocess.Popen:
    """A hidden process that outlives its starter (away from the starter's job where Windows allows it)."""
    out = open(log, "a", encoding="utf-8")
    flags = (DETACHED | NEW_GROUP | CREATE_NO_WINDOW) if os.name == "nt" else 0
    away = BREAKAWAY if os.name == "nt" and not os.environ.get("POE2_PROBE_NO_BREAKAWAY") else 0  # (tests stay in their job)
    try:
        return subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags | away, close_fds=True)
    except OSError:  # the starter's job allows no breakaway
        return subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags, close_fds=True)


class _OneOnly:
    """A byte-range lock on SPOOL\\<name>.lock for the life of this process (Windows lets it go if the process dies):
    one keeper and one server at a time, whoever starts them."""

    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None

    def take(self) -> bool:
        import msvcrt

        self.handle = open(self.path, "a+b")
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False


def probe_env(base: dict[str, str], wanted: dict[str, str], slot_set: str) -> dict[str, str]:
    """The probe's environment: the server's without any forwarded kind of setting or queue setting, then the client's
    own forwarded settings, then what a queue booking gives a run (PC_QUEUE_KIND=test) and the server's slot set."""
    env = {k: v for k, v in base.items() if not k.upper().startswith(FORWARDED) and not k.upper().startswith("PC_QUEUE_")}
    env.update({k: v for k, v in wanted.items() if k.upper().startswith(FORWARDED)})
    env["PC_QUEUE_KIND"] = "test"
    env["POE2_SLOT_SET"] = slot_set
    return env


def servers(spool: Path = SPOOL) -> list[dict]:
    """Every server heartbeat; its "state" is the server's own ('accepting', 'held: <why>', 'draining'), or 'gone'
    (process ended) or 'stuck' (heartbeat stale while the process lives)."""
    found = []
    for path in sorted((spool / "servers").glob("*.json")):
        beat = _read_json(path)
        if not beat:
            continue
        if not _alive(int(beat.get("pid", 0))):
            beat["state"] = "gone"
        elif _now() - float(beat.get("beat", 0)) > STALE_SECONDS:
            beat["state"] = "stuck"
        found.append(beat)
    return found


# ---- what the queue would do: pc_queue.py's own checks ----

class Holds:
    """pc_queue.py loaded as a module (as tools/pc_queue.py loads it), its checks called read-only. PC_QUEUE_DIR, if set
    for the server, moves its state and hold files to that folder (pc_queue's own proof switch: the server's tests)."""

    def __init__(self) -> None:
        spec = importlib.util.spec_from_file_location("pc_queue_shared_probe", PC_QUEUE)
        self.q = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.q)
        self.me = {"pid": -1, "no_gpu": True, "project": "poe2", "kind": "test"}

    def why_held(self) -> str | None:
        """Why a PoE2 run would be held or paused now, or None."""
        why = self.q._user_holds("poe2", "test", True)
        if why:
            return why
        if self.q._window_open(self.me):
            return "a measuring window is open"
        if os.path.exists(self.q.MEASURING):
            return "MEASURING.lock is held"
        return None

    def why_no_start(self) -> str | None:
        """Why no new probe starts now (a hold, or CompanionVR's gpu_test.lock: the queue starts nothing then)."""
        return self.why_held() or ("gpu_test.lock is held" if os.path.exists(self.q.GPU_LOCK) else None)


# ---- the server's own Windows job, and pausing a probe's processes ----

class _Limits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Limits), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _CpuRate(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


BELOW_NORMAL = 0x4000


def _off_users_processors(mask: int) -> int:
    kept = mask & ~0x3
    return kept or mask


def confine_self() -> dict:
    """Put this process (and so every probe it starts) in a job of its own with the limits above; whatever the job can't
    do, set on the process itself. Returns what was set."""
    done = {"job": False, "priority": False, "processors": False}
    if os.name != "nt":
        return done
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    me = kernel32.GetCurrentProcess()
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    kernel32.GetProcessAffinityMask(me, ctypes.byref(process), ctypes.byref(system))
    affinity = _off_users_processors(process.value)
    done["processors"] = bool(kernel32.SetProcessAffinityMask(me, affinity))
    done["priority"] = bool(kernel32.SetPriorityClass(me, BELOW_NORMAL))
    job = kernel32.CreateJobObjectW(None, None)
    if job:
        limits = _ExtendedLimits()
        basic = limits.BasicLimitInformation
        # JOB_MEMORY 0x200, ACTIVE_PROCESS 0x8, AFFINITY 0x10, PRIORITY_CLASS 0x20, KILL_ON_JOB_CLOSE 0x2000
        basic.LimitFlags = 0x200 | 0x8 | 0x10 | 0x20 | 0x2000
        basic.ActiveProcessLimit = PROCESSES
        basic.Affinity = affinity
        basic.PriorityClass = BELOW_NORMAL
        limits.JobMemoryLimit = MEMORY_MB * 1024 * 1024
        ok = kernel32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(limits), ctypes.sizeof(limits))
        rate = _CpuRate(0x1 | 0x4, CPU_PERCENT * 100)  # ENABLE | HARD_CAP, in 1/100 of a percent
        done["cpu_cap"] = bool(kernel32.SetInformationJobObject(ctypes.c_void_p(job), 15, ctypes.byref(rate), ctypes.sizeof(rate)))
        if ok and kernel32.AssignProcessToJobObject(ctypes.c_void_p(job), me):
            done["job"] = True
            confine_self.job = job  # keep the handle for the process's life (closing it would kill the probes)
        else:
            done["job_error"] = ctypes.get_last_error()
    return done


def _descendants(root: int) -> list[int]:
    """root and every process below it (a Toolhelp snapshot)."""
    if os.name != "nt":
        return [root]

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                    ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    children: dict[int, list[int]] = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    ok = kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
    while ok:
        children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
        ok = kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    kernel32.CloseHandle(ctypes.c_void_p(snap))
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in found:
            continue
        found.append(pid)
        todo.extend(children.get(pid, []))
    return found


def _suspend(pids: list[int], resume: bool) -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    ntdll = ctypes.WinDLL("ntdll")
    call = ntdll.NtResumeProcess if resume else ntdll.NtSuspendProcess
    for pid in pids:
        handle = kernel32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
        if handle:
            call(ctypes.c_void_p(handle))
            kernel32.CloseHandle(ctypes.c_void_p(handle))


# ---- the server ----

class Server:
    def __init__(self, name: str, spool: Path, jobs_at_once: int = JOBS_AT_ONCE, holds: Holds | None = None,
                 minutes: float | None = None) -> None:
        self.name, self.spool = name, spool
        self.jobs_at_once = jobs_at_once
        self.started = _now()
        self.end_at = self.started + minutes * 60 if minutes else None  # (tests: a server that ends by itself)
        self.running: dict[str, dict] = {}
        self.done = 0
        self.slot_set = f"probe-server-{name}"
        self.beat_path = spool / "servers" / f"{name}.json"
        self.holds = holds
        self.held: str | None = None  # why running probes are suspended now
        self.no_start: str | None = None  # why no probe starts now
        self.clear_since: float | None = None
        self.confined: dict = {}
        for folder in ("inbox", "claimed", "out", "servers"):
            (spool / folder).mkdir(parents=True, exist_ok=True)

    def state(self) -> str:
        if (self.spool / "STOP").exists() or (self.end_at and _now() >= self.end_at):
            return "draining"
        if self.held or self.no_start:
            return f"held: {self.held or self.no_start}"
        return "accepting"

    def beat(self) -> None:
        _write_json(self.beat_path, {
            "job": self.name, "pid": os.getpid(), "started": self.started, "beat": _now(), "state": self.state(),
            "stop_at": self.end_at, "jobs_at_once": self.jobs_at_once, "confined": self.confined,
            "waiting": len(list((self.spool / "inbox").glob("*.json"))),
            "running": [{"id": rid, "label": job["request"].get("label", ""), "since": job["since"],
                         "paused_s": round(job["paused"] + ((_now() - job["paused_at"]) if job["paused_at"] else 0), 1)}
                        for rid, job in self.running.items()],
            "done": self.done})

    def _claim(self) -> dict | None:
        waiting = []
        for path in (self.spool / "inbox").glob("*.json"):
            request = _read_json(path)
            if request:
                waiting.append((float(request.get("asked", 0)), path, request))
        for _, path, request in sorted(waiting, key=lambda row: row[0]):
            try:
                os.replace(path, self.spool / "claimed" / path.name)  # atomic: one claimer gets it
            except OSError:
                continue
            return request
        return None

    def _start(self, request: dict) -> None:
        rid = request["id"]
        log = open(self.spool / "out" / f"{rid}.log", "w", encoding="utf-8", errors="replace")
        argv = list(request["argv"])
        found = shutil.which(argv[0], path=request.get("path")) or shutil.which(argv[0]) if argv else None
        if found:
            argv[0] = found
        env = probe_env(dict(os.environ), request.get("env", {}), self.slot_set)
        since = _now()
        try:
            proc = subprocess.Popen(argv, cwd=request["cwd"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            log.write(f"probe_server: could not start {argv!r}: {error}\n")
            log.close()
            self._finish(rid, request, None, since, f"could not start: {error}")
            return
        _log(self.spool, f"probe {rid} started: {request.get('label', '')!r} in {request['cwd']}")
        self.running[rid] = {"request": request, "proc": proc, "log": log, "since": since, "paused": 0.0, "paused_at": None, "suspended": [],
                             "minutes": float(request.get("minutes", 12))}

    def _finish(self, rid: str, request: dict, code: int | None, since: float, error: str = "") -> None:
        _write_json(self.spool / "out" / f"{rid}.json", {
            "id": rid, "code": code, "error": error, "server": self.name, "asked": request.get("asked"),
            "started": since, "ended": _now(), "label": request.get("label", "")})
        try:
            (self.spool / "claimed" / f"{rid}.json").unlink()
        except OSError:
            pass
        self.done += 1
        _log(self.spool, f"probe {rid} ended: exit {code}{'; ' + error if error else ''}")

    def _pause(self, job: dict, resume: bool) -> None:
        """Suspend a probe's processes once (Windows counts suspensions: each needs its own resume), or resume them."""
        if resume and job["paused_at"]:
            _suspend(job["suspended"], True)
            job["paused"] += _now() - job["paused_at"]
            job["paused_at"], job["suspended"] = None, []
        elif not resume and not job["paused_at"]:
            job["suspended"] = _descendants(job["proc"].pid)
            _suspend(job["suspended"], False)
            job["paused_at"] = _now()

    def _watch_holds(self) -> None:
        if self.holds is None:
            return
        held = self.holds.why_held()
        no_start = self.holds.why_no_start()
        if held:
            self.clear_since = None
            if not self.held:
                _log(self.spool, f"HELD ({held}): no new probe; {len(self.running)} running probe(s) paused")
            self.held = held
            for job in self.running.values():
                self._pause(job, resume=False)
        elif self.held:
            self.clear_since = self.clear_since or _now()
            if _now() - self.clear_since >= GRACE_SECONDS:
                _log(self.spool, f"hold over ({self.held}): {len(self.running)} probe(s) go on")
                self.held = None
                for job in self.running.values():
                    self._pause(job, resume=True)
        if no_start != self.no_start and not held:
            _log(self.spool, f"no new probe: {no_start}" if no_start else "probes may start again")
        self.no_start = None if held or self.held else no_start

    def _reap(self) -> None:
        for rid, job in list(self.running.items()):
            code = job["proc"].poll()
            error = ""
            if code is None:
                paused = job["paused"] + ((_now() - job["paused_at"]) if job["paused_at"] else 0)
                ran = _now() - job["since"] - paused
                limit = job["minutes"] * 60
                if ran > limit:
                    error = "stopped: the probe's own minutes ran out"
                elif paused > limit:
                    error = "stopped: paused past the probe's own minutes by a hold (the client books it normally)"
                else:
                    continue
                self._pause(job, resume=True)  # (resume before ending it)
                _kill_tree(job["proc"].pid)
                try:
                    job["proc"].wait(30)
                except subprocess.TimeoutExpired:
                    pass
                code = job["proc"].returncode
            job["log"].close()
            del self.running[rid]
            self._finish(rid, job["request"], code, job["since"], error)

    def serve(self, keeper: bool) -> int:
        stop = threading.Event()

        def beating() -> None:
            while not stop.is_set():
                try:
                    self.beat()
                except OSError:
                    pass
                stop.wait(BEAT_SECONDS)

        threading.Thread(target=beating, daemon=True).start()
        _log(self.spool, f"server {self.name} up: {self.confined}")
        next_hold, next_keeper = 0.0, _now() + 10
        try:
            while True:
                if _now() >= next_hold:
                    next_hold = _now() + HOLD_POLL_SECONDS
                    try:
                        self._watch_holds()
                    except Exception as error:  # a check that fails holds the server (never runs blind)
                        self.held = f"the hold check failed: {error!r}"
                        _log(self.spool, f"HELD: {self.held}")
                        for job in self.running.values():
                            self._pause(job, resume=False)
                self._reap()
                if self.state() == "accepting":
                    while len(self.running) < self.jobs_at_once:
                        request = self._claim()
                        if request is None:
                            break
                        self._start(request)
                elif self.state() == "draining" and not self.running:
                    break
                if keeper and _now() >= next_keeper and not (self.spool / "STOP").exists():
                    next_keeper = _now() + 10
                    info = _read_json(self.spool / "keeper.json")
                    if not info or not _alive(int(info.get("pid", 0))):
                        _log(self.spool, "the keeper is gone: starting it again")
                        _detached([sys.executable, str(Path(__file__).resolve()), "keep", "--spool", str(self.spool)],
                                  self.spool / "keeper.out")
                time.sleep(0.25)
        finally:
            stop.set()
            for rid, job in list(self.running.items()):
                self._pause(job, resume=True)
                _kill_tree(job["proc"].pid)
                job["log"].close()
                self._finish(rid, job["request"], None, job["since"], "stopped: the server ended (the client books it normally)")
            self.running.clear()
            try:
                self.beat_path.unlink()
            except OSError:
                pass
            _log(self.spool, f"server {self.name} down")
        return 0


def keep(spool: Path) -> int:
    """Start the server, and start it again whenever it is gone, until SPOOL\\STOP appears."""
    only = _OneOnly(spool / "keeper.lock")
    if not only.take():
        return 0  # another keeper is up
    _write_json(spool / "keeper.json", {"pid": os.getpid(), "started": _now()})
    _log(spool, "keeper up")
    while not (spool / "STOP").exists():
        beats = servers(spool)
        for beat in beats:
            if beat.get("state") == "gone":  # a server that died: its heartbeat file goes
                try:
                    (spool / "servers" / f"{beat['job']}.json").unlink()
                except OSError:
                    pass
        up = [b for b in beats if b.get("state") != "gone"]
        if not up:
            _log(spool, "no server up: starting one")
            _detached([sys.executable, str(Path(__file__).resolve()), "serve", "--spool", str(spool)], spool / "server.out")
            time.sleep(15)  # (its first heartbeat)
            continue
        time.sleep(5)
    _log(spool, "keeper down (STOP)")
    try:
        (spool / "keeper.json").unlink()
    except OSError:
        pass
    return 0


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="probe_server.py")
    sub = parser.add_subparsers(dest="what", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--spool", default=str(SPOOL))
    serve.add_argument("--jobs", type=int, default=JOBS_AT_ONCE)
    serve.add_argument("--minutes", type=float, default=None, help="(tests) end by itself after this long")
    serve.add_argument("--no-holds", action="store_true", help="(tests only) don't read the queue's holds")
    serve.add_argument("--no-keeper", action="store_true", help="(tests) don't start a keeper")
    for name in ("start", "keep", "stop", "status"):
        sub.add_parser(name).add_argument("--spool", default=str(SPOOL))
    args = parser.parse_args(argv)
    spool = Path(args.spool)
    spool.mkdir(parents=True, exist_ok=True)
    if args.what == "serve":
        only = _OneOnly(spool / "server.lock")
        if not only.take():
            print("probe_server: a server is already up on this spool", file=sys.stderr)
            return 0
        server = Server(f"standing-{os.getpid()}", spool, args.jobs, None if args.no_holds else Holds(), args.minutes)
        server.confined = confine_self()
        return server.serve(keeper=not args.no_keeper)
    if args.what == "keep":
        return keep(spool)
    if args.what == "start":
        try:
            (spool / "STOP").unlink()
        except OSError:
            pass
        info = _read_json(spool / "keeper.json")
        if info and _alive(int(info.get("pid", 0))):
            print(f"probe_server: already running (keeper pid {info['pid']})")
            return 0
        proc = _detached([sys.executable, str(Path(__file__).resolve()), "keep", "--spool", str(spool)], spool / "keeper.out")
        print(f"probe_server: keeper started (pid {proc.pid}); it starts the server; log {spool / 'server.log'}")
        return 0
    if args.what == "stop":
        (spool / "STOP").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        print("probe_server: STOP written: no new probes; running ones finish; the server and keeper end")
        return 0
    if args.what == "status":
        info = _read_json(spool / "keeper.json")
        keeper_up = bool(info and _alive(int(info.get("pid", 0))))
        print(f"keeper: {'running, pid %s' % info['pid'] if keeper_up else 'not running'}{'; STOP is set' if (spool / 'STOP').exists() else ''}")
        for beat in servers(spool):
            print(f"server {beat['job']}: {beat['state']}, pid {beat['pid']}, up {(_now() - float(beat['started'])) / 60:.0f} min, "
                  f"{len(beat.get('running', []))} running, {beat.get('waiting', 0)} waiting, {beat.get('done', 0)} done; "
                  f"confined {beat.get('confined')}")
            for job in beat.get("running", []):
                print(f"  running {job['id']} {job['label']} ({(_now() - job['since']) / 60:.1f} min, paused {job['paused_s']} s)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
