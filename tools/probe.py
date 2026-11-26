r"""Run a read-only probe through the standing probe server, or through a normal queue booking when the server can't
take it (PERF22, 24-25 Nov 2026). The answer is the same either way: the server runs the very command, in a fresh
process, from the same folder, with your POE2_* / PYTHON* / UV_* settings (tools/probe_server.py).

Command line (from your own folder; the same words as a normal probe booking):
    python tools/probe.py POE2T<n> <minutes> "<lane>: <what>" -- uv run python -X utf8 scratch/<job>/probe.py <args>
prints the probe's output and exits with its exit code. POE2T<n> is used only if the probe falls back to a booking.

From Python:
    sys.path.insert(0, "tools"); from probe import probe
    result = probe(["uv", "run", "python", "-X", "utf8", "scratch/x/probe.py", "18"], job="POE2T6599", minutes=12,
                   what="MECH-A: Maim probe")
    result.code, result.output, result.via   ("server standing-1234" or "booking POE2T6599")

Falls back to a normal booking (`tools/pc_queue.py run <job> test <minutes> ...`) when: no server is up; the server is
HELD (a measuring window, MEASURING.lock, HOLD_poe2, the user in VR: the queue would hold it too) or stopping; it is
busy (BUSY_WAITING probes already waiting); `gate=True` / --gate (a probe that needs 8 GB or 6 engines); the server
stopped the probe (it ended, or the probe passed 2 GB or its own running minutes); or the probe ran short of the server's
engines or memory (PoolFull / MemoryError, the server's 4 GB and 6 slots being shared by 2 probes). A probe already
running when a hold starts is paused with the server and goes on after it, as a booked run would: the client waits
however long the hold lasts and never books it meanwhile (PERF23; a paused probe's minutes don't run).

NEVER TWICE (PERF23): once the server has taken the probe, this client books the queue only after the server's answer
says it ended the probe, or once the server's PROCESS that took it is gone (checked GONE_SECONDS apart; its job object
ends the probe with it). A heartbeat file it can't read, a stale heartbeat or a hold is never taken for an ended server
(before PERF23 one failed read did that: POE2T9501, POE2T9604 were booked while the server still ran them). If a server
lives but overruns its own stop, the client CANCELS the probe there (SPOOL\cancel\<id>) and books only after the
server confirms.

SMALL TARGETED TEST RUNS go the same way (the user, 25 Nov ~01:18):
    python tools/probe.py POE2T<n> <minutes> "<LANE>: <JOB> targeted" -- uv run python -m pytest -q -p no:cacheprovider <files>
    python tools/probe.py POE2T<n> <minutes> "<LANE>: <JOB> targeted" --tests <files> [--replay]    (the same, real PoB by default)
A test run qualifies when it names test files (not a folder), says 10 minutes or less, isn't run with -n, its files
took 10 minutes or less in the last serial suite (tests/suite_durations.json), test_engine_models.py isn't among them,
and its label doesn't say gate / train / calibration / suite part: otherwise it is booked normally, with the reason.
On the server it is stopped and booked normally if it passes 2 GB or its own minutes. Its junit XML is written next
to its log (the client prints the path); its exit code is the client's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import probe_server as ps  # noqa: E402


@dataclass
class Result:
    code: int | None
    output: str
    via: str
    waited: float = 0.0
    ran: float = 0.0
    junit: str | None = None  # a test run's junit XML (on the server: SPOOL\out\<id>.xml)
    peak_mb: float = 0.0


def _say(text: str) -> None:
    print(f"probe: {text}", file=sys.stderr, flush=True)


def _forwarded() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper().startswith(ps.FORWARDED)}


BUSY_WAITING = 6  # probes already waiting for the server: at this many, a normal booking is likely sooner


def _server_can_take(spool: Path, minutes: float) -> str | None:
    """A server taking probes now (not held, not stopping, not busy), with time left for the probe, else None."""
    for beat in ps.servers(spool):
        stop_at = beat.get("stop_at")
        room = stop_at is None or float(stop_at) - ps._now() > minutes * 60 + 60
        if beat.get("state") == "accepting" and room and int(beat.get("waiting", 0)) < BUSY_WAITING:
            return beat["job"]
    return None


FALLBACK_ERRORS = ("stopped: the server", "stopped: paused", "stopped: past", "could not start", "refused", "cancelled by its client")
GONE_SECONDS = 4.0  # (PERF23) the server's process found ended, and still ended this long after: it is gone
OVERDUE_SECONDS = 300.0  # running (not paused) past the server's own stop by this much with no answer: cancel it there
FALLBACK_OUTPUT = ("PoolFull", "MemoryError", "not enough memory")  # the server's 4 GB / 6 slots, shared by 2 probes


def _cancel(spool: Path, rid: str) -> None:
    """Ask the server to stop (or never start) this probe; it answers "cancelled by its client" (PERF23)."""
    try:
        (spool / "cancel").mkdir(parents=True, exist_ok=True)
        (spool / "cancel" / rid).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass


def _booking(argv: list[str], job: str, minutes: int, what: str, gate: bool, cwd: Path) -> Result:
    if os.environ.get("POE2_PROBE_NO_BOOKING"):  # (the server's own tests: never a real booking)
        return Result(None, "", "booking refused (POE2_PROBE_NO_BOOKING)")
    command = [sys.executable, str(cwd / "tools" / "pc_queue.py"), "run", job, "test", str(minutes), f"PoE2 {what}"]
    if gate:
        command.append("--gate")
    command += ["--no-gpu", "--", *argv]
    started = time.time()
    out = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
    return Result(out.returncode, out.stdout + out.stderr, f"booking {job}", ran=time.time() - started)


def probe(argv: list[str], job: str, minutes: int = 12, what: str = "probe", gate: bool = False,
          cwd: str | Path | None = None, spool: Path | None = None, echo: bool = False) -> Result:
    cwd = Path(cwd or os.getcwd()).resolve()
    spool = Path(spool or ps.SPOOL)
    if gate:
        return _booking(argv, job, minutes, what, gate, cwd)
    refused = ps.qualifies(list(argv), float(minutes), what, str(cwd))
    if refused:
        _say(f"not for the probe server: {refused}: booking it normally")
        return _booking(argv, job, minutes, what, gate, cwd)
    server = _server_can_take(spool, minutes)
    if server is None:
        _say("no probe server is taking probes now: booking it normally")
        return _booking(argv, job, minutes, what, gate, cwd)
    rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    asked = time.time()
    request = {"id": rid, "argv": list(argv), "cwd": str(cwd), "env": _forwarded(), "minutes": minutes,
               "label": what, "asked": asked, "fallback_job": job}
    ps._write_json(spool / "inbox" / f"{rid}.json", request)
    answer_path, log_path = spool / "out" / f"{rid}.json", spool / "out" / f"{rid}.log"
    watched = {int(b["pid"]) for b in ps.servers(spool) if b.get("state") not in ("gone", "unreadable") and b.get("pid")}
    said: set[str] = set()
    not_taking = 0  # polls in a row on which no readable server was accepting (an unclaimed probe)
    gone_since: float | None = None
    holder: int | None = None
    cancelled = False
    shown = 0

    def say_once(key: str, text: str) -> None:
        if key not in said:
            said.add(key)
            _say(text)

    while True:
        answer = ps._read_json(answer_path)
        if echo and log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            sys.stdout.write(text[shown:])
            sys.stdout.flush()
            shown = len(text)
        if answer:
            break
        beats = ps.servers(spool)
        mine = None
        for beat in beats:
            for job_info in beat.get("running") or []:
                if job_info.get("id") == rid:
                    holder, mine = int(beat["pid"]), {**job_info, "state": beat.get("state", "")}
        if (spool / "inbox" / f"{rid}.json").exists():  # not claimed yet
            readable = [b for b in beats if b.get("state") != "unreadable"]
            taking = any(b.get("state") == "accepting" for b in readable)
            not_taking = 0 if taking or len(readable) < len(beats) else not_taking + 1
            if not_taking >= 2:  # (PERF23) seen on two polls in a row, every heartbeat read: never one failed read
                why = next((b["state"] for b in beats if b.get("state", "").startswith("held")), "stopping or gone")
                try:
                    os.remove(spool / "inbox" / f"{rid}.json")  # take it back (a server may just have claimed it)
                except OSError:
                    time.sleep(0.5)
                    continue
                _say(f"the probe server can't start it now ({why}): booking it normally")
                return _booking(argv, job, minutes, what, gate, cwd)
            time.sleep(0.5)
            continue
        claimed = ps._read_json(spool / "claimed" / f"{rid}.json") or {}
        if claimed.get("claimed_by"):
            holder = int(claimed["claimed_by"])
        pids = {holder} if holder else watched
        if pids and not any(ps._alive(pid) for pid in pids):  # the holding server's PROCESS, never its heartbeat file
            gone_since = gone_since or time.time()
            if time.time() - gone_since >= GONE_SECONDS and not answer_path.exists():
                _cancel(spool, rid)  # (its job ended the probe with it; the cancel keeps any later server off it)
                _say("the probe server ended while running this probe (its process is gone): booking it normally")
                return _booking(argv, job, minutes, what, gate, cwd)
        else:
            gone_since = None
        if mine and mine["state"].startswith("held"):
            say_once("held", f"the probe server is {mine['state']}: this probe is paused, as a booking would be; "
                             "waiting for it (never booked meanwhile)")
        elif any(b.get("state") == "stuck" and int(b.get("pid", 0)) in pids for b in beats):
            say_once("stuck", "the probe server's heartbeat is stale but its process lives: still waiting for this probe")
        if mine and not mine.get("held") and not cancelled and mine.get("minutes") is not None:
            ran = time.time() - float(mine["since"]) - float(mine.get("paused_s") or 0)
            if ran > float(mine["minutes"]) * 60 * ps.SERVER_SLOWDOWN + OVERDUE_SECONDS:
                _cancel(spool, rid)
                cancelled = True
                _say("the probe is past the server's own stop and still running: cancelled it there; booking it normally "
                     "once the server confirms")
        time.sleep(0.5)
    output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if echo:
        sys.stdout.write(output[shown:])
    if answer.get("error", "").startswith(FALLBACK_ERRORS):
        _say(f"{answer['error']}: booking it normally")
        return _booking(argv, job, minutes, what, gate, cwd)
    if answer.get("code") not in (0, None) and any(word in output for word in FALLBACK_OUTPUT):
        _say("the probe ran short of the server's engines or memory: booking it normally")
        return _booking(argv, job, minutes, what, gate, cwd)
    if answer.get("error"):
        output += f"\nprobe: {answer['error']}\n"
    return Result(answer.get("code"), output, f"server {answer.get('server')}",
                  waited=float(answer.get("started", asked)) - asked, ran=float(answer.get("ended", 0)) - float(answer.get("started", 0)),
                  junit=answer.get("junit"), peak_mb=float(answer.get("peak_mb") or 0))


def main(argv: list[str]) -> int:
    if ("--" not in argv and "--tests" not in argv) or len(argv) < 4:
        print(__doc__)
        return 2
    at = argv.index("--") if "--" in argv else len(argv)
    head, command = argv[:at], argv[at + 1:]
    if "--tests" in head:  # the shortcut: python tools/probe.py POE2T<n> <minutes> "<what>" --tests <files> [--replay]
        at = head.index("--tests")
        files = [a for a in head[at + 1:] if a != "--replay"]
        os.environ.pop("POE2_REPLAY_POB" if "--replay" not in head else "POE2_REAL_POB", None)
        os.environ["POE2_REPLAY_POB" if "--replay" in head else "POE2_REAL_POB"] = "1"
        command = ["uv", "run", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", "-rfE", *files]
        head = head[:at]
    gate = "--gate" in head
    head = [a for a in head if a not in ("--gate", "--no-gpu")]
    job, minutes, what = head[0], int(head[1]), head[2]
    result = probe(command, job=job, minutes=minutes, what=what, gate=gate, echo=True)
    if result.via.startswith("booking"):
        sys.stdout.write(result.output)
    _say(f"answered by {result.via}" + (f" (waited {result.waited:.0f} s, ran {result.ran:.0f} s, peak {result.peak_mb:.0f} MB)"
                                         if result.via.startswith("server") else ""))
    if result.junit:
        _say(f"junit: {result.junit}")
    return result.code if isinstance(result.code, int) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
