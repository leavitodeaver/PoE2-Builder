"""Running time: a clock that leaves out the time this process spent suspended.

The PC queue suspends every process of a run while the user plays, and the time paused doesn't count against the run's
minutes (C:\\PROJECTS\\PCQueue\\README.md, PoE2's line). Search budgets and the pool's silence watchdog read this clock, so
a held run keeps its search time, and the pool doesn't take its own workers for runaway engines when a hold ends (a hold
over TASK_SILENCE_LIMIT used to replace every busy worker). A daemon thread ticks every TICK seconds; a gap of more than
PAUSE_GAP seconds between two ticks is a suspension (every thread of the process stood still), and all but one tick of
it is left out. Python code never holds the thread off that long (the interpreter switches threads every 5 ms), and the
engine's calculations run in their own processes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

TICK = 0.25
PAUSE_GAP = 2.0


class RunningClock:
    def __init__(self, read: Callable[[], float] = time.perf_counter, watch: bool = True) -> None:
        self._read = read
        self._lock = threading.Lock()
        self._last = read()  # the last tick
        self._paused = 0.0
        self._given = float("-inf")  # (never goes backwards)
        self._watch = watch
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        with self._lock:
            current = self._read()
            gap = current - self._last
            if gap > PAUSE_GAP:
                self._paused += gap - TICK
            self._last = current

    def paused(self) -> float:
        """Seconds left out so far, a suspension that just ended included."""
        with self._lock:
            gap = self._read() - self._last
            return self._paused + (gap - TICK if gap > PAUSE_GAP else 0.0)

    def now(self) -> float:
        """Like time.perf_counter(), without the time suspended."""
        if self._watch and (self._thread is None or not self._thread.is_alive()):
            self._start()
        with self._lock:
            current = self._read()
            gap = current - self._last
            value = current - self._paused - (gap - TICK if gap > PAUSE_GAP else 0.0)
            self._given = max(self._given, value)
            return self._given

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._last = self._read()
            self._thread = threading.Thread(target=self._run, name="running-clock", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        _raise_thread_priority()
        while True:
            time.sleep(TICK)
            self.tick()


def _raise_thread_priority() -> None:
    """The ticking thread runs ahead of everything else on the computer. PoB's workers run below normal priority, and a
    tick that waited over PAUSE_GAP for a processor busy with other work would count as a suspension and slow the
    worker's clock (suspected for gate 4's overruns on 8 Nov, which had another cause; kept as a safeguard). It only
    sleeps and ticks, so the priority costs nothing."""
    import os

    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        THREAD_PRIORITY_TIME_CRITICAL = 15
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)
    except Exception:
        pass


_CLOCK = RunningClock()


def now() -> float:
    return _CLOCK.now()


def paused() -> float:
    """Seconds this process's clock has left out as suspended so far."""
    return _CLOCK.paused()
