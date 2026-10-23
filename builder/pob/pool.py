"""A pool of worker processes, each running its own PobEngine.

Every worker holds the same loaded build (set with `broadcast`), so a batch of what-if cases can
be split across workers with `scatter`. Chunks go to whichever worker is free, and results come
back in the original case order.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


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
        method, kwargs = message
        try:
            conn.send(("ok", engine.call(method, **kwargs)))
        except Exception:
            conn.send(("error", traceback.format_exc()))


class WorkerError(RuntimeError):
    pass


class EnginePool:
    def __init__(self, workers: int | None = None, pob_dir: Path | None = None) -> None:
        """`pob_dir` selects a PoB checkout other than the pinned one (e.g. a release to compare)."""
        self.size = workers or default_workers()
        context = mp.get_context("spawn")
        self._conns: list[Connection] = []
        self._procs: list[mp.Process] = []
        started = time.perf_counter()
        pob_dir = pob_dir.resolve() if pob_dir else None
        for _ in range(self.size):
            parent, child = context.Pipe()
            proc = context.Process(target=_worker_main, args=(child, pob_dir), daemon=True)
            proc.start()
            child.close()
            self._conns.append(parent)
            self._procs.append(proc)
        self.boot_seconds = [self._receive(conn) for conn in self._conns]
        self.startup_seconds = time.perf_counter() - started

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
