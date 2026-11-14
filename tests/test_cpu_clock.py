"""SEARCH19 (the manager's 14 Nov 02:40 item: gates that compare code, not PC load): gate4 --cpu runs the search on a
processor-time clock in each worker (inner.use_cpu_clock) and on the task time it has handed out in the run itself, so
the phases and their shares don't shrink on a busy PC."""
from __future__ import annotations

import time

from builder import clock
from builder.search import inner
from builder.search.run import Run


def test_a_worker_reads_its_processor_time_only_when_the_payload_asks(monkeypatch):
    monkeypatch.setattr(clock, "now", clock.now)  # (restored after the test)
    before = clock.now
    inner.use_cpu_clock({})
    assert clock.now is before
    inner.use_cpu_clock({inner.CPU_CLOCK: True})
    assert clock.now is time.process_time
    assert inner.CPU_CLOCK == "cpu_clock"  # (run.py sets the payload's key by name)


def test_the_runs_clock_is_the_task_time_handed_out():
    run = Run.__new__(Run)
    run.cpu_clock, run.handed_out = True, 0.0
    run.hand_out([("t", {"seconds": 5.0}), ("t", {"seconds": 7.0})])  # (a wave: its longest task)
    run.hand_out([("t", {"seconds": 3.0})])
    assert run.elapsed() == 10.0
    run.seconds = 30.0
    assert run.remaining() == 20.0
