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
from builder.pob import slots
from builder.pob.lua import LuaError, LuaState


class PobError(RuntimeError):
    pass


GC_PAUSE_VARIABLE = "POE2_LUA_GC_PAUSE"


def gc_pause() -> int | None:
    """The Lua collector's pause this process's engines are to run with, or None for PoB's own (PERF3, 13 Nov 2026).

    PoB sets `collectgarbage("setpause", 400)` (Launch.lua 20): its memory may grow to 4x what is alive before a
    collection starts, which trades memory for speed. POE2_LUA_GC_PAUSE=<100..400> sets another pause after boot
    (200 is Lua's own default): less memory per engine, more collector work. Unset or empty = nothing is touched,
    PoB's 400 stays. It decides only WHEN dead memory is freed: PERF2 read 7,920 bridge answers at 200 and at 150
    byte-identical to 400's (360 builds x 22 read-only methods), PoB has no finalizers and walks no weak table
    (PERF2.report.md section 2), and PERF3 ran the full suite under each. Pool workers inherit the variable.
    A value that isn't a whole number in 100..400 is an error, never a silent fallback."""
    text = os.environ.get(GC_PAUSE_VARIABLE, "").strip()
    if not text:
        return None
    try:
        pause = int(text)
    except ValueError:
        pause = -1
    if not 100 <= pause <= 400:
        raise PobError(f"{GC_PAUSE_VARIABLE}={text!r}: a whole number from 100 to 400, or unset for PoB's own 400")
    return pause


class PobEngine:
    def __init__(self, pob_dir: Path = POB_DIR) -> None:
        slots.take()  # inside the PC queue: one of PoE2's 6 LuaJIT slots for this process, or PoolFull
        pob_dir = pob_dir.resolve()  # before chdir, so relative paths mean what the caller meant
        src = pob_dir / "src"
        started = time.perf_counter()
        os.chdir(src)
        self.lua = LuaState(pob_dir / "runtime")
        self._run_file(ENGINE_DIR / "bootstrap.lua", src.as_posix())
        self._run_file(ENGINE_DIR / "bridge.lua")
        pause = gc_pause()
        if pause is not None:  # after boot: Launch.lua sets PoB's own 400 while it loads
            self.lua.run(f'collectgarbage("setpause", {pause})', "gc_pause")
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

    def close(self) -> None:
        """Give the Lua state's memory back (hundreds of MB). A process that boots engines one after another (the
        tests) must close each: nothing else frees one. Calls after this raise PobError."""
        self.lua.close()

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
