"""PERF22's parallel suite parts (tools/suite.py, tests/conftest.py under pytest-xdist): no PoB engine here."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("suite_tool", ROOT / "tools" / "suite.py")
suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suite)

HOLDER = r"""
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("c", sys.argv[1])
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
turn = c._PoolTurn(sys.argv[2])
turn.take()
print("in", time.monotonic(), flush=True)
time.sleep(float(sys.argv[3]))
print("out", time.monotonic(), flush=True)
turn.give()
"""


@pytest.mark.spawns_processes
def test_two_workers_pools_take_turns(tmp_path):
    """Two processes of one run: the second's pool waits until the first's is closed."""
    run_id = f"unit-{time.time_ns()}"
    conftest = str(ROOT / "tests" / "conftest.py")
    first = subprocess.Popen([sys.executable, "-c", HOLDER, conftest, run_id, "2"], stdout=subprocess.PIPE, text=True, cwd=ROOT,
                             creationflags=0x08000000 if os.name == "nt" else 0)
    assert first.stdout.readline().startswith("in")
    second = subprocess.Popen([sys.executable, "-c", HOLDER, conftest, run_id, "0"], stdout=subprocess.PIPE, text=True, cwd=ROOT,
                             creationflags=0x08000000 if os.name == "nt" else 0)
    out_first = float(first.stdout.readline().split()[1])
    in_second = float(second.stdout.readline().split()[1])
    first.wait(30)
    second.wait(30)
    assert in_second >= out_first  # the second took its turn only after the first gave it back


def test_a_pool_inside_a_pool_does_not_wait_for_itself():
    import conftest

    turn = conftest._PoolTurn(f"unit-{time.time_ns()}")
    turn.take()
    turn.take()
    turn.give()
    assert turn.handle is not None  # still held by the outer pool
    turn.give()
    assert turn.handle is None


def _junit(path: Path, cases: list[tuple[str, str, str]]) -> str:
    rows = []
    for classname, name, result in cases:
        inner = {"passed": "", "failed": "<failure message='x'/>", "error": "<error message='x'/>"}.get(result)
        if inner is None:
            inner = f"<skipped message='{result.split(': ', 1)[1]}'/>"
        rows.append(f"<testcase classname='{classname}' name='{name}' time='1.5'>{inner}</testcase>")
    path.write_text("<testsuites><testsuite>" + "".join(rows) + "</testsuite></testsuites>", encoding="utf-8")
    return str(path)


def test_compare_finds_any_test_whose_result_differs(tmp_path, capsys):
    base = [("tests.test_a", "test_1", "passed"), ("tests.test_a", "test_2", "skipped: no PoB"),
            ("tests.test_b.TestX", "test_3", "passed")]
    a = _junit(tmp_path / "a.xml", base)
    assert suite.compare([a, "--against", _junit(tmp_path / "b.xml", list(reversed(base)))]) == 0
    assert "IDENTICAL" in capsys.readouterr().out
    changed = [base[0], ("tests.test_a", "test_2", "skipped: another reason"), ("tests.test_b.TestX", "test_3", "failed")]
    assert suite.compare([a, "--against", _junit(tmp_path / "c.xml", changed)]) == 1
    out = capsys.readouterr().out
    assert "differs: tests.test_a::test_2" in out and "differs: tests.test_b.TestX::test_3" in out
    assert suite.compare([a, "--against", _junit(tmp_path / "d.xml", base[:2])]) == 1
    assert "only in A: tests.test_b.TestX::test_3" in capsys.readouterr().out


def test_durations_add_up_each_files_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(suite, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    xml = _junit(tmp_path / "s.xml", [("tests.test_a", "t1", "passed"), ("tests.test_a", "t2", "passed"),
                                      ("tests.test_b.TestX", "t3", "skipped: r")])
    suite.durations([xml])
    files = json.loads((tmp_path / "tests" / "suite_durations.json").read_text(encoding="utf-8"))["files"]
    assert files == {"test_a.py": 3.0, "test_b.py": 1.5}


def test_the_booking_runs_whole_files_longest_first_and_gates_from_three_workers():
    command = suite.pytest_command(3, Path("x.xml"), ["tests"])
    assert command[command.index("-n") + 1] == "3" and "loadfile" in command and "--no-loadscope-reorder" in command
    assert "-n" not in suite.pytest_command(1, Path("x.xml"), ["tests"])
    assert suite.GATE_FROM == 3 and suite.MAX_WORKERS == 4
    assert suite.clean("2581 passed, 5 skipped in 900.1s") and not suite.clean("1 failed, 2580 passed")
    assert not suite.clean("2580 passed, 1 error")
