r"""Run a read-only probe through the probe server, or through a normal queue booking when the server can't take it
(PERF22, 24 Nov 2026). The answer is the same either way: the server runs the very command, in a fresh process, from
the same folder, with your POE2_* / PYTHON* / UV_* settings (tools/probe_server.py).

Command line (from your own folder; the same words as a normal probe booking, plus nothing):
    python tools/probe.py POE2T<n> <minutes> "<lane>: <what>" -- uv run python -X utf8 scratch/<job>/probe.py <args>
prints the probe's output and exits with its exit code. POE2T<n> is used only if the probe falls back to a booking.

From Python:
    from tools.probe import probe          (or importlib from this file's path)
    result = probe(["uv", "run", "python", "-X", "utf8", "scratch/x/probe.py", "18"], job="POE2T6599", minutes=12,
                   what="MECH-A: Maim probe")
    result.code, result.output, result.via   ("server POE2PS0007" or "booking POE2T6599")

Falls back to a normal booking (`tools/pc_queue.py run <job> test <minutes> ...`) when: no server is up; the only
servers are stopping (they take no probe in their last minutes) or gone; `gate=True` / --gate (a probe that needs 8 GB
or 6 engines); the server stopped the probe before it finished (its place ended). A server that is PAUSED (a measuring
window, the user in VR) is waited for, as a booking would be: it says so once.
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


def _say(text: str) -> None:
    print(f"probe: {text}", file=sys.stderr, flush=True)


def _forwarded() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper().startswith(ps.FORWARDED)}


def _server_can_take(spool: Path, minutes: float) -> str | None:
    """An accepting server with time left for the probe, else None."""
    for beat in ps.servers(spool):
        if beat.get("state") == "accepting" and float(beat.get("stop_at", 0)) - ps._now() > minutes * 60 + 60:
            return beat["job"]
    return None


def _booking(argv: list[str], job: str, minutes: int, what: str, gate: bool, cwd: Path) -> Result:
    if os.environ.get("POE2_PROBE_NO_BOOKING"):  # (the server's own tests: never a real booking)
        return Result(None, "", "booking refused (POE2_PROBE_NO_BOOKING)")
    command = [sys.executable, str(cwd / "tools" / "pc_queue.py"), "run", job, "test", str(minutes), f"PoE2 {what}"]
    if gate:
        command.append("--gate")
    command += ["--no-gpu", "--", *argv]
    started = time.time()
    out = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return Result(out.returncode, out.stdout + out.stderr, f"booking {job}", ran=time.time() - started)


def probe(argv: list[str], job: str, minutes: int = 12, what: str = "probe", gate: bool = False,
          cwd: str | Path | None = None, spool: Path | None = None, echo: bool = False) -> Result:
    cwd = Path(cwd or os.getcwd()).resolve()
    spool = Path(spool or ps.SPOOL)
    if gate:
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
    said_paused = False
    shown = 0
    while True:
        answer = ps._read_json(answer_path)
        if echo and log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            sys.stdout.write(text[shown:])
            sys.stdout.flush()
            shown = len(text)
        if answer:
            break
        if (spool / "inbox" / f"{rid}.json").exists():  # not claimed yet
            if _server_can_take(spool, 0) is None:
                beats = ps.servers(spool)
                if any(b.get("state") == "paused" for b in beats):
                    if not said_paused:
                        _say("the probe server is paused (a measuring window or a hold): waiting, as a booking would")
                        said_paused = True
                elif not any(b.get("state") in ("accepting", "paused") for b in beats):
                    try:
                        os.remove(spool / "inbox" / f"{rid}.json")  # take it back (a server may just have claimed it)
                    except OSError:
                        time.sleep(0.5)
                        continue
                    _say("the probe server stopped taking probes before this one started: booking it normally")
                    return _booking(argv, job, minutes, what, gate, cwd)
        elif not any(b.get("state") in ("accepting", "draining", "paused") for b in ps.servers(spool)) \
                and not answer_path.exists():
            time.sleep(2)  # the answer may be written in the server's last moment
            if not answer_path.exists():
                _say("the probe server ended while running this probe: booking it normally")
                return _booking(argv, job, minutes, what, gate, cwd)
        time.sleep(0.5)
    output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if echo:
        sys.stdout.write(output[shown:])
    if answer.get("error", "").startswith("stopped: the server") or answer.get("error", "").startswith("could not start"):
        _say(f"{answer['error']}: booking it normally")
        return _booking(argv, job, minutes, what, gate, cwd)
    if answer.get("error"):
        output += f"\nprobe: {answer['error']}\n"
    return Result(answer.get("code"), output, f"server {answer.get('server')}",
                  waited=float(answer.get("started", asked)) - asked, ran=float(answer.get("ended", 0)) - float(answer.get("started", 0)))


def main(argv: list[str]) -> int:
    if "--" not in argv or argv.index("--") < 3:
        print(__doc__)
        return 2
    at = argv.index("--")
    head, command = argv[:at], argv[at + 1:]
    gate = "--gate" in head
    head = [a for a in head if a not in ("--gate", "--no-gpu")]
    job, minutes, what = head[0], int(head[1]), head[2]
    result = probe(command, job=job, minutes=minutes, what=what, gate=gate, echo=True)
    if result.via.startswith("booking"):
        sys.stdout.write(result.output)
    _say(f"answered by {result.via}" + (f" (waited {result.waited:.0f} s, ran {result.ran:.0f} s)" if result.via.startswith("server") else ""))
    return result.code if isinstance(result.code, int) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
