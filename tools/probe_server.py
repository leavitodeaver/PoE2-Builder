r"""The probe server (PERF22, 24 Nov 2026): one standing PC-queue place that runs the lanes' read-only probes at once.

Why: in the 48 h to 24 Nov 08:26 the lanes ran 216 probes, a median 0.4 min of real work each, and each waited a
median 17 min for a queue place. The server holds ONE of PoE2's test places (a plain 4 GB booking, renewed before it
runs out) and runs every probe sent to it in a FRESH process with the probe's own command, folder and POE2_*
settings: the same command a normal booking would run, so the answer is the one a normal booking gives (nothing is
shared between two probes: no engine, no Python state). It only removes the wait.

    python tools/probe_server.py start            the keeper: books the server through the queue and renews it
    python tools/probe_server.py stop             no new probes; running ones finish; the keeper's waiting booking is cancelled
    python tools/probe_server.py status           what is up, what is running, what is waiting
    python tools/probe_server.py serve ...        (what the keeper's booking runs; not for lanes)

Lanes use tools/probe.py (one line from Python or the command line); it falls back to a normal queue booking
whenever the server is down, stopping, or the probe needs more than the server's place gives.

Limits: at most JOBS_AT_ONCE probes at once (2), each probe's processes inside the server's queue run (the clamp,
Below Normal, processors 2+, the booking's 4 GB and 24 processes), all of them sharing the run's 6 LuaJIT slots
(POE2_SLOT_SET, builder/pob/slots.py). A probe is stopped at its own minutes (default 12). The server stops taking
probes ACCEPT_MINUTES after it starts and ends by STOP_MINUTES, inside the queue's limit for a 20-minute booking
(stopped at 2 x 20 + 3 = 43 min of running). While a measure runs or the user is in VR, the queue pauses the server
like any PoE2 run: its heartbeat stops and clients wait (a normal booking would be paused too).

Spool (one folder for every lane): SPOOL\inbox\<id>.json (requests), SPOOL\claimed\, SPOOL\out\<id>.log and
<id>.json (the answer), SPOOL\servers\<job>.json (each server's heartbeat), SPOOL\keeper.json, SPOOL\STOP.
"""

from __future__ import annotations

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
JOBS_AT_ONCE = 2
ACCEPT_MINUTES = 30.0  # no new probe after this (a probe has 12 min by default: it ends before STOP_MINUTES)
STOP_MINUTES = 41.0  # every probe still running is stopped, the server ends (the queue stops a 20-min booking at 43)
RENEW_MINUTES = 18.0  # the keeper books the next server when the current one has run this long
BEAT_SECONDS = 2.0
STALE_SECONDS = 20.0  # a heartbeat older than this: the server is paused (a measure, a hold) or gone
FORWARDED = ("POE2_", "PYTHON", "UV_")  # the client's own settings that reach its probe (and only the client's)
BOOKING_MINUTES = 20


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


def _alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

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
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def probe_env(base: dict[str, str], wanted: dict[str, str], slot_set: str) -> dict[str, str]:
    """The probe's environment: the server's (the queue's run settings: PC_QUEUE_KIND, the real Python first on PATH)
    without any of the forwarded kinds of setting, then the client's own forwarded settings, then the slot set."""
    env = {k: v for k, v in base.items() if not k.upper().startswith(FORWARDED)}
    env.update({k: v for k, v in wanted.items() if k.upper().startswith(FORWARDED)})
    env["POE2_SLOT_SET"] = slot_set
    return env


def servers(spool: Path = SPOOL) -> list[dict]:
    """Every server heartbeat with its state: 'accepting', 'draining', 'paused' (stale heartbeat, process alive) or 'gone'."""
    found = []
    for path in sorted((spool / "servers").glob("*.json")):
        beat = _read_json(path)
        if not beat:
            continue
        if not _alive(int(beat.get("pid", 0))):
            beat["state"] = "gone"
        elif _now() - float(beat.get("beat", 0)) > STALE_SECONDS:
            beat["state"] = "paused"
        found.append(beat)
    return found


class Server:
    def __init__(self, job: str, spool: Path, jobs_at_once: int, accept_minutes: float, stop_minutes: float) -> None:
        self.job, self.spool = job, spool
        self.jobs_at_once = jobs_at_once
        self.started = _now()
        self.accept_until = self.started + accept_minutes * 60
        self.stop_at = self.started + stop_minutes * 60
        self.running: dict[str, dict] = {}
        self.done = 0
        self.slot_set = f"probe-server-{job}-{os.getpid()}"
        self.beat_path = spool / "servers" / f"{job}.json"
        for name in ("inbox", "claimed", "out", "servers"):
            (spool / name).mkdir(parents=True, exist_ok=True)

    def state(self) -> str:
        if (self.spool / "STOP").exists() or _now() >= self.accept_until or self._newer_server():
            return "draining"
        return "accepting"

    def _newer_server(self) -> bool:
        """A server the keeper booked after this one is up: let it take the new probes."""
        for beat in servers(self.spool):
            if beat.get("job") != self.job and beat.get("state") == "accepting" and float(beat.get("started", 0)) > self.started:
                return True
        return False

    def beat(self) -> None:
        _write_json(self.beat_path, {
            "job": self.job, "pid": os.getpid(), "started": self.started, "beat": _now(), "state": self.state(),
            "accept_until": self.accept_until, "stop_at": self.stop_at, "jobs_at_once": self.jobs_at_once,
            "running": [{"id": rid, "label": job["request"].get("label", ""), "since": job["since"]} for rid, job in self.running.items()],
            "done": self.done})

    def _claim(self) -> dict | None:
        waiting = []
        for path in (self.spool / "inbox").glob("*.json"):
            request = _read_json(path)
            if request:
                waiting.append((float(request.get("asked", 0)), path, request))
        for _, path, request in sorted(waiting, key=lambda row: row[0]):
            claimed = self.spool / "claimed" / path.name
            try:
                os.replace(path, claimed)  # atomic: one server gets it
            except OSError:
                continue
            return request
        return None

    def _start(self, request: dict) -> None:
        rid = request["id"]
        out = self.spool / "out"
        log = open(out / f"{rid}.log", "w", encoding="utf-8", errors="replace")
        argv = list(request["argv"])
        found = shutil.which(argv[0]) if argv else None
        if found:
            argv[0] = found
        env = probe_env(dict(os.environ), request.get("env", {}), self.slot_set)
        since = _now()
        try:
            proc = subprocess.Popen(argv, cwd=request["cwd"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=0x08000000 if os.name == "nt" else 0)
        except OSError as error:
            log.write(f"probe_server: could not start {argv!r}: {error}\n")
            log.close()
            self._finish(rid, request, None, since, f"could not start: {error}")
            return
        limit = since + float(request.get("minutes", 12)) * 60
        self.running[rid] = {"request": request, "proc": proc, "log": log, "since": since, "limit": limit}

    def _finish(self, rid: str, request: dict, code: int | None, since: float, error: str = "") -> None:
        _write_json(self.spool / "out" / f"{rid}.json", {
            "id": rid, "code": code, "error": error, "server": self.job, "asked": request.get("asked"),
            "started": since, "ended": _now(), "label": request.get("label", "")})
        try:
            (self.spool / "claimed" / f"{rid}.json").unlink()
        except OSError:
            pass
        self.done += 1

    def _reap(self) -> None:
        for rid, job in list(self.running.items()):
            code = job["proc"].poll()
            error = ""
            if code is None:
                if _now() > job["limit"] or _now() > self.stop_at:
                    _kill_tree(job["proc"].pid)
                    job["proc"].wait(30)
                    code = job["proc"].returncode
                    error = ("stopped: the probe's own minutes ran out" if _now() > job["limit"]
                             else "stopped: the server's place ended (the client books it normally)")
                else:
                    continue
            job["log"].close()
            del self.running[rid]
            self._finish(rid, job["request"], code, job["since"], error)

    def serve(self) -> int:
        stop = threading.Event()

        def beating() -> None:
            while not stop.is_set():
                try:
                    self.beat()
                except OSError:
                    pass
                stop.wait(BEAT_SECONDS)

        threading.Thread(target=beating, daemon=True).start()
        try:
            while True:
                self._reap()
                state = self.state()
                if state == "accepting":
                    while len(self.running) < self.jobs_at_once:
                        request = self._claim()
                        if request is None:
                            break
                        self._start(request)
                elif not self.running:
                    break
                if _now() > self.stop_at and not self.running:
                    break
                time.sleep(0.25)
        finally:
            stop.set()
            for rid, job in list(self.running.items()):
                _kill_tree(job["proc"].pid)
                job["log"].close()
                self._finish(rid, job["request"], None, job["since"], "stopped: the server ended")
            self.running.clear()
            try:
                self.beat_path.unlink()
            except OSError:
                pass
        return 0


ID_PREFIX = os.environ.get("POE2_PROBE_ID_PREFIX") or "POE2PS"  # the keeper's queue ids (the manager may give a block)


def _next_job(spool: Path) -> str:
    """The keeper's queue ids: POE2PS<n>, counted in SPOOL\\keeper_ids.txt (never reused)."""
    path = spool / "keeper_ids.txt"
    try:
        n = int(path.read_text(encoding="utf-8").strip() or 0) + 1
    except (OSError, ValueError):
        n = 1
    path.write_text(str(n), encoding="utf-8")
    return f"{ID_PREFIX}{n:04d}"


def keep(spool: Path, overlap: bool) -> int:
    """Book the server, and book the next one RENEW_MINUTES into each (so one is up when the last stops taking probes),
    until SPOOL\\STOP appears. Each booking is a plain PoE2 test place (one place, 4 GB)."""
    spool.mkdir(parents=True, exist_ok=True)
    bookings: list[tuple[str, subprocess.Popen, float]] = []
    queue = [sys.executable, str(ROOT / "tools" / "pc_queue.py")]

    def book() -> None:
        job = _next_job(spool)
        what = (f"PoE2 PERF: probe server {job} (runs the lanes' read-only probes at once; one place, renewed; "
                f"tools/probe_server.py); not a gate")
        command = queue + ["run", job, "test", str(BOOKING_MINUTES), what, "--no-gpu", "--",
                           sys.executable, str(Path(__file__).resolve()), "serve", "--job", job, "--spool", str(spool)]
        log = open(spool / f"keeper-{job}.log", "w", encoding="utf-8")
        bookings.append((job, subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=0x08000000 if os.name == "nt" else 0), _now()))

    _write_json(spool / "keeper.json", {"pid": os.getpid(), "started": _now(), "overlap": overlap})
    book()
    while not (spool / "STOP").exists():
        bookings = [b for b in bookings if b[1].poll() is None]
        up = {beat["job"]: beat for beat in servers(spool) if beat.get("state") in ("accepting", "paused", "draining")}
        newest = bookings[-1] if bookings else None
        if newest is None:
            book()
        elif overlap and len(bookings) == 1 and newest[0] in up:
            ran = _now() - float(up[newest[0]]["started"])
            if ran >= RENEW_MINUTES * 60 or up[newest[0]].get("state") == "draining":
                book()
        _write_json(spool / "keeper.json", {"pid": os.getpid(), "started": _now(), "overlap": overlap,
                                            "bookings": [b[0] for b in bookings]})
        time.sleep(5)
    for job, proc, _ in bookings:
        if job not in {beat["job"] for beat in servers(spool)}:  # still waiting for a place: leave the line
            subprocess.run(queue + ["cancel", job], capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    for job, proc, _ in bookings:
        try:
            proc.wait(STOP_MINUTES * 60)
        except subprocess.TimeoutExpired:
            pass
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
    serve.add_argument("--job", default=f"local-{uuid.uuid4().hex[:6]}")
    serve.add_argument("--spool", default=str(SPOOL))
    serve.add_argument("--jobs", type=int, default=JOBS_AT_ONCE)
    serve.add_argument("--accept-minutes", type=float, default=ACCEPT_MINUTES)
    serve.add_argument("--stop-minutes", type=float, default=STOP_MINUTES)
    start = sub.add_parser("start")
    start.add_argument("--spool", default=str(SPOOL))
    start.add_argument("--no-overlap", action="store_true", help="book the next server only when the last has ended")
    keeper = sub.add_parser("keep")
    keeper.add_argument("--spool", default=str(SPOOL))
    keeper.add_argument("--no-overlap", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("--spool", default=str(SPOOL))
    status = sub.add_parser("status")
    status.add_argument("--spool", default=str(SPOOL))
    args = parser.parse_args(argv)
    spool = Path(args.spool)
    if args.what == "serve":
        if not os.environ.get("PC_QUEUE_KIND"):
            print("probe_server: serve runs inside a queue booking (the keeper books it); PC_QUEUE_KIND is not set", file=sys.stderr)
        return Server(args.job, spool, args.jobs, args.accept_minutes, args.stop_minutes).serve()
    if args.what == "keep":
        return keep(spool, overlap=not args.no_overlap)
    if args.what == "start":
        keeper_info = _read_json(spool / "keeper.json")
        if keeper_info and _alive(int(keeper_info.get("pid", 0))):
            print(f"probe_server: the keeper is already running (pid {keeper_info['pid']})")
            return 0
        try:
            (spool / "STOP").unlink()
        except OSError:
            pass
        spool.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), "keep", "--spool", str(spool)]
        if args.no_overlap:
            command.append("--no-overlap")
        flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0  # DETACHED, NEW_GROUP, NO_WINDOW
        log = open(spool / "keeper.log", "a", encoding="utf-8")
        proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags, close_fds=True)
        print(f"probe_server: keeper started (pid {proc.pid}); it books the server through the queue; "
              f"`python tools/probe_server.py status` shows when it is up")
        return 0
    if args.what == "stop":
        spool.mkdir(parents=True, exist_ok=True)
        (spool / "STOP").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        print("probe_server: STOP written: no new probes; running ones finish; the keeper cancels a waiting booking and ends")
        return 0
    if args.what == "status":
        keeper_info = _read_json(spool / "keeper.json")
        print(f"keeper: {'running, pid %s, bookings %s' % (keeper_info['pid'], keeper_info.get('bookings')) if keeper_info and _alive(int(keeper_info.get('pid', 0))) else 'not running'}"
              f"{'; STOP is set' if (spool / 'STOP').exists() else ''}")
        for beat in servers(spool):
            left = (float(beat["accept_until"]) - _now()) / 60
            print(f"server {beat['job']}: {beat['state']}, pid {beat['pid']}, takes probes for {max(0.0, left):.1f} more min, "
                  f"{len(beat.get('running', []))} running, {beat.get('done', 0)} done")
            for job in beat.get("running", []):
                print(f"  running {job['id']} {job['label']} ({(_now() - job['since']) / 60:.1f} min)")
        waiting = sorted((spool / "inbox").glob("*.json")) if (spool / "inbox").exists() else []
        print(f"waiting: {len(waiting)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
