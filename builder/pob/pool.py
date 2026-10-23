"""A pool of worker processes, each running its own PobEngine.

Every worker holds the same loaded build (set with `broadcast`), so a batch of what-if cases can
be split across workers with `scatter`. Chunks go to whichever worker is free, and results come
back in the original case order.

Workers can also run whole Python tasks next to their engine (`run_tasks`): a task is a function
`fn(engine, report, **kwargs)` named "module:function". Running the search loop inside the worker
avoids a pipe round trip per PoB call. `report(payload)` streams progress to the parent and raises
`Cancelled` once the parent has asked the task to stop.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import os
import time
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

CANCEL = "cancel"
# Watchdog for tasks: a worker over this much memory, or silent this long, is replaced and its
# task reported as failed. Healthy workers sit at 0.5-1 GB and report about once a second.
WORKER_MEMORY_LIMIT = 4 * 1024**3
TASK_SILENCE_LIMIT = 180.0


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


class Cancelled(Exception):
    """Raised inside a task when the parent cancels it; tasks catch it to return what they have."""


def _run_task(conn: Connection, engine: Any, name: str, kwargs: dict) -> Any:
    module_name, function_name = name.split(":")
    function = getattr(importlib.import_module(module_name), function_name)
    cancelled = False

    def report(payload: Any) -> None:
        nonlocal cancelled
        while conn.poll():
            if conn.recv() == CANCEL:
                cancelled = True
        if payload is not None:
            conn.send(("progress", payload))
        if cancelled:
            raise Cancelled()

    return function(engine, report, **kwargs)


def _worker_main(conn: Connection, pob_dir: Path | None) -> None:
    # Imported here so the parent process never boots an engine
    from builder.pob.engine import PobEngine

    try:
        engine = PobEngine(pob_dir) if pob_dir else PobEngine()
        conn.send(("ready", engine.boot_seconds))
    except Exception:
        conn.send(("error", traceback.format_exc()))
        return
    while True:
        message = conn.recv()
        if message is None:
            return
        if message == CANCEL:  # arrived after its task finished
            continue
        method, kwargs = message
        try:
            if method.startswith("task:"):
                conn.send(("ok", _run_task(conn, engine, method[5:], kwargs)))
            else:
                conn.send(("ok", engine.call(method, **kwargs)))
        except Exception:
            conn.send(("error", traceback.format_exc()))


class WorkerError(RuntimeError):
    pass


def process_memory(pid: int) -> int:
    """Working set of a process in bytes (Windows; 0 if it can't be read)."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        ok = psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize if ok else 0
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


class EnginePool:
    def __init__(self, workers: int | None = None, pob_dir: Path | None = None,
                 memory_limit: int = WORKER_MEMORY_LIMIT, silence_limit: float = TASK_SILENCE_LIMIT) -> None:
        """`pob_dir` selects a PoB checkout other than the pinned one (e.g. a release to compare).
        The limits are the task watchdog's (see WORKER_MEMORY_LIMIT)."""
        self.memory_limit = memory_limit
        self.silence_limit = silence_limit
        self.size = workers or default_workers()
        self._context = mp.get_context("spawn")
        self._pob_dir = pob_dir.resolve() if pob_dir else None
        self._conns: list[Connection] = []
        self._procs: list[mp.Process] = []
        self.replaced = 0  # workers the watchdog replaced
        started = time.perf_counter()
        for _ in range(self.size):
            conn, proc = self._spawn()
            self._conns.append(conn)
            self._procs.append(proc)
        self.boot_seconds = [self._receive(conn) for conn in self._conns]
        self.startup_seconds = time.perf_counter() - started

    def _spawn(self) -> tuple[Connection, mp.Process]:
        parent, child = self._context.Pipe()
        proc = self._context.Process(target=_worker_main, args=(child, self._pob_dir), daemon=True)
        proc.start()
        child.close()
        return parent, proc

    def _replace(self, conn: Connection) -> Connection:
        """Kill a worker and boot a fresh one in its place; returns the new connection."""
        index = self._conns.index(conn)
        self._procs[index].kill()
        self._procs[index].join(timeout=5)
        conn.close()
        new_conn, proc = self._spawn()
        self._receive(new_conn)
        self._conns[index], self._procs[index] = new_conn, proc
        self.replaced += 1
        return new_conn

    def broadcast(self, method: str, **kwargs: Any) -> list[Any]:
        """Run the same call on every worker (e.g. load a build) and return every result."""
        for conn in self._conns:
            conn.send((method, kwargs))
        return [self._receive(conn) for conn in self._conns]

    def scatter(self, method: str, cases: list[Any], chunk_size: int = 32, **kwargs: Any) -> list[Any]:
        """Split `cases` across workers as calls to `method(cases=chunk, **kwargs)`.

        The method must return {"results": [...]} with one entry per case.
        """
        chunks = [cases[i:i + chunk_size] for i in range(0, len(cases), chunk_size)]
        results: list[list[Any] | None] = [None] * len(chunks)
        pending = list(range(len(chunks)))
        busy: dict[Connection, int] = {}

        def dispatch(conn: Connection) -> None:
            index = pending.pop(0)
            conn.send((method, {**kwargs, "cases": chunks[index]}))
            busy[conn] = index

        for conn in self._conns:
            if pending:
                dispatch(conn)
        while busy:
            for conn in wait(list(busy)):
                index = busy.pop(conn)
                results[index] = self._receive(conn)["results"]
                if pending:
                    dispatch(conn)
        return [result for chunk in results for result in chunk]

    def run_tasks(
        self,
        tasks: list[tuple[str, dict]],
        on_progress: Callable[[int, Any], None] | None = None,
        on_result: Callable[[int, Any], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[Any]:
        """Run Python tasks ("module:function", kwargs) on whichever worker is free.

        Returns results in task order; a task that raised gives a WorkerError in its place, so one
        failure doesn't lose the others. `should_cancel` is polled about twice a second; once it
        returns True, running tasks are asked to stop and queued ones are skipped (None).
        """
        results: list[Any] = [None] * len(tasks)
        pending = list(range(len(tasks)))
        busy: dict[Connection, int] = {}
        heard: dict[Connection, float] = {}
        cancelling = False
        last_check = 0.0

        def dispatch(conn: Connection) -> None:
            index = pending.pop(0)
            name, kwargs = tasks[index]
            conn.send(("task:" + name, kwargs))
            busy[conn] = index
            heard[conn] = time.perf_counter()

        def finish(conn: Connection, result: Any) -> None:
            index = busy.pop(conn)
            heard.pop(conn, None)
            results[index] = result
            if on_result:
                on_result(index, result)

        for conn in self._conns:
            if pending:
                dispatch(conn)
        while busy:
            if should_cancel and not cancelling and should_cancel():
                cancelling = True
                pending.clear()
                for conn in busy:
                    conn.send(CANCEL)
            for conn in wait(list(busy), timeout=0.5):
                try:
                    status, payload = conn.recv()
                except (EOFError, OSError):
                    finish(conn, WorkerError("the worker process died"))
                    conn = self._replace(conn)
                else:
                    heard[conn] = time.perf_counter()
                    if status == "progress":
                        if on_progress:
                            on_progress(busy[conn], payload)
                        continue
                    finish(conn, payload if status == "ok" else WorkerError(payload))
                if pending and not cancelling:
                    dispatch(conn)
            now = time.perf_counter()
            if now - last_check >= 2.0:
                last_check = now
                for conn in list(busy):
                    proc = self._procs[self._conns.index(conn)]
                    memory = process_memory(proc.pid)
                    silent = now - heard[conn]
                    if memory > self.memory_limit or silent > self.silence_limit:
                        reason = (f"worker used {memory / 1024**3:.1f} GB" if memory > self.memory_limit
                                  else f"worker silent for {silent:.0f}s")
                        finish(conn, WorkerError(f"{reason}; replaced it (runaway engine, see PLAN.md Phase 2 findings)"))
                        fresh = self._replace(conn)
                        if pending and not cancelling:
                            dispatch(fresh)
        return results

    def close(self) -> None:
        for conn in self._conns:
            try:
                conn.send(None)
            except (BrokenPipeError, OSError):
                pass
        for proc in self._procs:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()

    def __enter__(self) -> EnginePool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _receive(conn: Connection) -> Any:
        status, payload = conn.recv()
        if status == "error":
            raise WorkerError(payload)
        return payload
