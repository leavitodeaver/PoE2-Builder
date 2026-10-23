"""The worker pool's task watchdog. Boots real engines (a few seconds each)."""

from __future__ import annotations

import time

from builder.pob.pool import EnginePool, WorkerError


def quiet_task(engine, report, seconds: float) -> str:
    """A task that reports nothing for `seconds`, like an engine call that never returns."""
    time.sleep(seconds)
    return "done"


def test_a_silent_worker_is_replaced_and_the_pool_keeps_working():
    with EnginePool(1, silence_limit=3.0) as pool:
        stuck, = pool.run_tasks([("test_pool:quiet_task", {"seconds": 60})])
        assert isinstance(stuck, WorkerError) and "silent" in str(stuck)
        assert pool.replaced == 1
        fine, = pool.run_tasks([("test_pool:quiet_task", {"seconds": 0})])
        assert fine == "done"
