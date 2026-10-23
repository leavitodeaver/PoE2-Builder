"""Path of Building (PoE2) running headless inside this process, on PoB's own LuaJIT.

PoB reads its modules and data through relative paths, so creating an engine changes the
process working directory to PoB's src/ folder. Run one engine per process; the worker pool
gives each worker process its own.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from builder import ENGINE_DIR, POB_DIR
from builder.pob.lua import LuaError, LuaState


class PobError(RuntimeError):
    pass


class PobEngine:
    def __init__(self, pob_dir: Path = POB_DIR) -> None:
        src = (pob_dir / "src").resolve()
        started = time.perf_counter()
        os.chdir(src)
        self.lua = LuaState(pob_dir / "runtime")
        self._run_file(ENGINE_DIR / "bootstrap.lua", src.as_posix())
        self._run_file(ENGINE_DIR / "bridge.lua")
        self.boot_seconds = time.perf_counter() - started

    def call(self, method: str, **args: Any) -> Any:
        """Call bridge.<method>(args) and return its decoded JSON result."""
        try:
            ok, payload = self.lua.call("bridge_dispatch", method, json.dumps(args))
        except LuaError as error:
            raise PobError(f"{method}: {error}") from error
        if ok != b"true":
            raise PobError(f"{method}: {payload.decode('utf-8', 'replace')}")
        return json.loads(payload)

    def log_tail(self, lines: int = 30) -> str:
        try:
            (text,) = self.lua.run(
                "return table.concat(engineLog or {}, '\\n', math.max(1, #(engineLog or {}) - ...))",
                "log_tail",
                str(lines - 1),
            )
        except LuaError:
            return ""
        return text.decode("utf-8", "replace")

    def _run_file(self, path: Path, *args: str) -> None:
        try:
            self.lua.run(path.read_text(encoding="utf-8"), path.as_posix(), *args)
        except LuaError as error:
            raise PobError(f"{path.name}: {error}\n{self.log_tail()}") from error
