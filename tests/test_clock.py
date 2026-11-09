"""The running clock leaves out the time the PC queue keeps a run suspended (builder/clock.py)."""
from __future__ import annotations

import pytest

from builder.clock import PAUSE_GAP, TICK, RunningClock


class Fake:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_a_suspension_is_left_out_and_running_time_is_not():
    fake = Fake()
    clock = RunningClock(read=fake, watch=False)
    start = clock.now()
    for _ in range(40):  # 10 s of running, ticking
        fake.t += TICK
        clock.tick()
    assert clock.now() - start == pytest.approx(10.0)
    fake.t += 180.0  # suspended for 3 minutes: no tick ran
    assert clock.now() - start == pytest.approx(10.0 + TICK)  # (seen at once, before the next tick)
    clock.tick()
    assert clock.now() - start == pytest.approx(10.0 + TICK)
    assert clock.paused() == pytest.approx(180.0 - TICK)
    fake.t += PAUSE_GAP * 0.9  # a slow tick, not a suspension
    clock.tick()
    assert clock.now() - start == pytest.approx(10.0 + TICK + PAUSE_GAP * 0.9)


def test_the_running_clock_never_goes_backwards():
    fake = Fake()
    clock = RunningClock(read=fake, watch=False)
    fake.t += 5.0
    seen = clock.now()
    clock.tick()  # the ticker counts the gap a little differently from now(): the clock holds
    assert clock.now() >= seen


def test_the_real_clock_runs():
    import time

    from builder import clock

    a = clock.now()
    time.sleep(0.05)
    assert 0.03 < clock.now() - a < 1.0


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows thread priorities")
def test_the_ticking_thread_runs_ahead_of_busy_work():
    """A starved ticking thread would read as a suspension and slow a worker's clock, so it runs time-critical."""
    import ctypes

    from builder import clock

    clock.now()  # starts the thread
    thread = clock._CLOCK._thread
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.GetThreadPriority.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenThread(0x0040, False, thread.native_id)  # THREAD_QUERY_INFORMATION
    try:
        for _ in range(40):  # the thread sets it as it starts
            if kernel32.GetThreadPriority(handle) == 15:
                break
            __import__("time").sleep(0.05)
        assert kernel32.GetThreadPriority(handle) == 15
    finally:
        kernel32.CloseHandle(handle)


def test_a_suspended_search_keeps_its_hard_stop_on_running_time(monkeypatch):
    """The hard stop (Build.stop_at, 646dcae) reads the running clock: a queue hold past it doesn't stop the search,
    running time past it does (POE2T223: tasks ran 976 s wall with 740 s left out, 236 s of search each)."""
    from builder import clock as clock_module
    from builder.search.state import Build, OutOfTime

    fake = Fake()
    running = RunningClock(read=fake, watch=False)
    monkeypatch.setattr(clock_module, "_CLOCK", running)
    build = object.__new__(Build)
    build.report, build.stop_at = None, clock_module.now() + 30.0
    for _ in range(80):  # 20 s of search
        fake.t += TICK
        running.tick()
    build.tick()
    fake.t += 600.0  # held for 10 minutes
    running.tick()
    build.tick()  # 20 s of running time: no stop
    for _ in range(48):  # 12 s more: past the stop
        fake.t += TICK
        running.tick()
    with pytest.raises(OutOfTime):
        build.tick()
