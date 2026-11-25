r"""The test suite as ONE queue booking, its files spread over several PoB processes (PERF22, 24 Nov 2026).

    python tools/suite.py run POE2T<n> "<lane>: <what>" [--part all|1|2|3] [--workers 3] [--replay] [-- <pytest targets>]
    python tools/suite.py compare <a.xml> [<a2.xml> ...] --against <b.xml> [<b2.xml> ...]
    python tools/suite.py durations <serial junit.xml> [...]        (refreshes tests/suite_durations.json)

`run` books the PC queue (tools/pc_queue.py) once and runs pytest-xdist inside the booking: `-n <workers> --dist loadfile
--no-loadscope-reorder`, so every test FILE runs whole on one worker process (its shared engine and call history are
the serial run's), the longest files first (tests/conftest.py, tests/suite_durations.json). Real PoB unless --replay.
The pytest log is scratch/suite/<id>.log, the junit XML scratch/suite/<id>.xml. It prints the last pytest line and
exits 0 only when that line has passes and no failure or error.

  --part all   the whole suite (the train's three parts in one booking)        default
  --part 1     tests/test_engine_models.py
  --part 2     test_engine_items, test_radius_jewels, test_replay
  --part 3     the rest
  --part 12    parts 1 and 2 together (with --part 3, the whole suite in two bookings: see "the 25-minute limit")
  -- targets   any pytest targets instead of a part (a lane's own test files)

Booking: 3 workers or more is booked --gate (8 GB, two of PoE2's four test places); 2 workers or fewer a plain place
(4 GB). See PERF22's report for the measurements behind the default.

The 25-minute limit: the queue stops a 20-minute booking at 43 minutes of running, but at 25 (its minutes plus 5) if
a measuring window opens while it runs past that. The whole suite on 4 workers ran 18-21 min (PERF22); on a slower
day, if a window stops it, run the two halves: `--part 12` (~12 min, test_engine_models bounds it) and `--part 3`
(~16 min, test_legality bounds it).

`compare` checks two runs gave the same result for every test: the same test ids, each passed / skipped (with its
reason) / failed / error alike. It prints the differences and exits 1 if there are any.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scratch" / "suite"
PART_1 = ["tests/test_engine_models.py"]
PART_2 = ["tests/test_engine_items.py", "tests/test_radius_jewels.py", "tests/test_replay.py"]
PARTS = {
    "all": ["tests"],
    "1": PART_1,
    "2": PART_2,
    "3": ["tests", *(f"--ignore={f}" for f in PART_1 + PART_2)],
    "12": PART_1 + PART_2,
}
MAX_WORKERS = 4  # each worker holds one of the run's 6 LuaJIT slots; a test's pool of 2 needs the other 2 (conftest's pool turns)
DEFAULT_WORKERS = 4
GATE_FROM = 3  # workers at which the run needs the --gate booking's 8 GB
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: nothing ever shows on the user's screen
LAST_LINE = re.compile(r"\d+ (passed|failed|error)")


def last_line(log: Path) -> str:
    """The combined SUITE line inner() prints (or, failing that, pytest's own last result line)."""
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    suite = [line for line in text if line.startswith("SUITE: ")]
    if suite:
        return suite[-1][len("SUITE: "):]
    lines = [line for line in text if LAST_LINE.search(line)]
    return lines[-1].strip("= ") if lines else ""


def clean(line: str) -> bool:
    return bool(re.search(r"\d+ passed", line)) and not re.search(r"failed|error", line)


def pytest_command(workers: int, xml: Path, targets: list[str], marks: str = "") -> list[str]:
    command = ["uv", "run", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", "-rfE",
               f"--junitxml={xml}"]
    if workers > 1:
        command += ["-n", str(workers), "--dist", "loadfile", "--no-loadscope-reorder"]
    if marks:
        command += ["-m", marks]
    return command + targets


COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)")


def counts(line: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for number, kind in COUNT.findall(line):
        kind = "error" if kind.startswith("error") else kind
        found[kind] = found.get(kind, 0) + int(number)
    return found


def inner(argv: list[str]) -> int:
    """Inside the booking: the parallel pytest on every test but the serial_only ones, then (with more than one
    worker) a serial pytest on the serial_only ones; one combined result line at the end."""
    import argparse
    import time

    # (the manager, 25 Nov 11:45, MECHB64's find) the booking points stdout at a file in the PC's code page (cp1252); a test
    # printing a character it can't encode crashed this loop's sys.stdout.write at the end of POE2T9022, so no SUITE line was
    # printed and a train chain would read the run as stopped. Write UTF-8, replacing what can't be written.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

    at = argv.index("--")
    argv, targets = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(prog="suite.py inner")
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--xml", required=True)
    args = parser.parse_args(argv)
    xml = Path(args.xml)
    phases = [("all", args.workers, xml, "")]
    if args.workers > 1:
        phases = [("parallel", args.workers, xml, "not serial_only"),
                  ("serial_only", 1, xml.with_name(xml.stem + "-serial.xml"), "serial_only")]
    total: dict[str, int] = {}
    seconds, bad = [], False
    for name, workers, path, marks in phases:
        started = time.time()
        out = subprocess.Popen(pytest_command(workers, path, targets, marks), cwd=ROOT, stdout=subprocess.PIPE, creationflags=NO_WINDOW,
                               stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        lines = []
        for line in out.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            lines.append(line)
        code = out.wait()
        seconds.append((name, time.time() - started))
        result = next((line for line in reversed(lines) if LAST_LINE.search(line) or "no tests ran" in line), "")
        print(f"suite.py: {name}: exit {code}: {result.strip(' =' + chr(10))}", flush=True)
        if code == 5 and name == "serial_only":  # no serial_only test among these targets
            continue
        bad = bad or code != 0
        for kind, number in counts(result).items():
            total[kind] = total.get(kind, 0) + number
    words = ", ".join(f"{total[k]} {k}" for k in ("failed", "passed", "skipped", "error", "xfailed", "xpassed") if total.get(k))
    if bad and not (total.get("failed") or total.get("error")):
        words += ", 1 error (a pytest run ended badly: see above)"
    print(f"SUITE: {words} in {sum(s for _, s in seconds):.1f}s (" + " + ".join(f"{n} {s:.1f}s" for n, s in seconds) + ")", flush=True)
    return 1 if bad else 0


def run(argv: list[str]) -> int:
    import argparse

    targets: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, targets = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(prog="suite.py run")
    parser.add_argument("job", help="the queue id, POE2T<n>")
    parser.add_argument("what", help='the booking label, e.g. "MECH-A: MECH107 targeted tests"')
    parser.add_argument("--part", default="all", choices=sorted(PARTS))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--replay", action="store_true", help="replayed PoB answers (POE2_REPLAY_POB=1); default real PoB")
    parser.add_argument("--minutes", type=int, default=20, help="the booking's minutes (20 at most; stopped at x2+3)")
    parser.add_argument("--gate", action="store_true", help="book 8 GB even below %d workers" % GATE_FROM)
    args = parser.parse_args(argv)
    workers = max(1, min(MAX_WORKERS, args.workers))
    OUT.mkdir(parents=True, exist_ok=True)
    log, xml = OUT / f"{args.job}.log", OUT / f"{args.job}.xml"
    env = dict(os.environ)
    env.pop("POE2_REAL_POB" if args.replay else "POE2_REPLAY_POB", None)
    env["POE2_REPLAY_POB" if args.replay else "POE2_REAL_POB"] = "1"
    what = f"PoE2 {args.what} ({'replay' if args.replay else 'real'} PoB, {workers} worker(s), suite.py)"
    booking = [sys.executable, str(ROOT / "tools" / "pc_queue.py"), "run", args.job, "test", str(args.minutes), what]
    if args.gate or workers >= GATE_FROM:
        booking.append("--gate")
    booking += ["--no-gpu", "--", "python", str(ROOT / "tools" / "suite.py"), "inner", "--workers", str(workers),
                "--xml", str(xml), "--", *(targets or PARTS[args.part])]
    with open(log, "w", encoding="utf-8") as out:
        code = subprocess.call(booking, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    line = last_line(log)
    xmls = [str(xml)] + ([str(xml.with_name(xml.stem + "-serial.xml"))] if xml.with_name(xml.stem + "-serial.xml").exists() else [])
    print(f"{args.job}: {line or '(no result line: see ' + str(log) + ')'}  [exit {code}; log {log}; junit {' '.join(xmls)}]")
    return 0 if clean(line) and code == 0 else 1


def outcomes(paths: list[str]) -> dict[str, str]:
    """test id -> 'passed' / 'skipped: <reason>' / 'failed' / 'error' (a test with a teardown error counts both)."""
    found: dict[str, str] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            test = f"{case.get('classname')}::{case.get('name')}"
            kinds = []
            for child in case:
                if child.tag == "skipped":
                    kinds.append("skipped: " + (child.get("message") or "").strip())
                elif child.tag in ("failure", "error"):
                    kinds.append("failed" if child.tag == "failure" else "error")
            result = " + ".join(kinds) or "passed"
            found[test] = found[test] + " + " + result if test in found else result
    return found


def compare(argv: list[str]) -> int:
    if "--against" not in argv:
        sys.exit("usage: suite.py compare <a.xml> ... --against <b.xml> ...")
    at = argv.index("--against")
    a, b = outcomes(argv[:at]), outcomes(argv[at + 1:])
    only_a, only_b = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
    differ = sorted(t for t in a.keys() & b.keys() if a[t] != b[t])
    tally = lambda d: {k: sum(1 for v in d.values() if v.split(":")[0] == k) for k in ("passed", "skipped", "failed", "error")}
    print(f"A: {len(a)} tests {tally(a)}")
    print(f"B: {len(b)} tests {tally(b)}")
    for test in only_a:
        print(f"only in A: {test} ({a[test]})")
    for test in only_b:
        print(f"only in B: {test} ({b[test]})")
    for test in differ:
        print(f"differs: {test}: A {a[test]!r}, B {b[test]!r}")
    same = not (only_a or only_b or differ)
    print("IDENTICAL: every test id and result the same" if same else
          f"NOT IDENTICAL: {len(only_a)} only in A, {len(only_b)} only in B, {len(differ)} with another result")
    return 0 if same else 1


def durations(argv: list[str]) -> int:
    """Each test file's seconds in serial junit XML(s) (setup + call + teardown of its tests: its module engines' boots
    included), into tests/suite_durations.json, which conftest reads to hand out the longest files first."""
    files: dict[str, float] = {}
    for path in argv:
        for case in ET.parse(path).getroot().iter("testcase"):
            parts = (case.get("classname") or "").split(".")  # tests.test_x or tests.test_x.TestClass
            name = (parts[1] if len(parts) > 1 and parts[0] == "tests" else parts[0]) + ".py"
            files[name] = files.get(name, 0.0) + float(case.get("time") or 0.0)
    target = ROOT / "tests" / "suite_durations.json"
    target.write_text(json.dumps({"from": [Path(p).name for p in argv], "files": {k: round(v, 1) for k, v in sorted(files.items())}},
                                 indent=1) + "\n", encoding="utf-8")
    print(f"{target}: {len(files)} files, {sum(files.values()):.0f} s in all")
    return 0


def main() -> int:
    commands = {"run": run, "inner": inner, "compare": compare, "durations": durations}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(__doc__)
        return 2
    return commands[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
