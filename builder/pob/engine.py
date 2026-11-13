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
GC_PAUSE_DEFAULT = 200  # the user's decision, 13 Nov 2026 ("Yes, use 200"); POE2_LUA_GC_PAUSE=400 is the way back


def gc_pause() -> int:
    """The Lua collector's pause this process's engines run with: 200 unless POE2_LUA_GC_PAUSE says otherwise
    (PERF3's switch, 13 Nov 2026; 200 the default since PERF7, the user's decision the same day).

    PoB sets `collectgarbage("setpause", 400)` for its desktop program (Launch.lua 20): its memory may grow to 4x
    what is alive before a collection starts, which trades memory for a smoother window. We set the pause again after
    boot: 200 (Lua's own default) when the variable is unset or empty, else POE2_LUA_GC_PAUSE=<100..400>.
    **POE2_LUA_GC_PAUSE=400 is the way back to PoB's own.** Pool workers inherit the variable.
    It decides only WHEN dead memory is freed, never a number. The evidence:
    - PERF2 read 7,920 bridge answers at 200 and at 150 byte-identical to 400's in one engine (360 builds x 22
      read-only methods, one of which, flaskData, is a constant error text there: 21 real answers per build); PoB's
      own code has no finalizers and walks no weak table (PERF2.report.md section 2). PERF6 compared 400 against 200
      across two processes: 2,200 answers, every one identical but for exportXml's line order, which differs as much
      between two processes at 400 (PERF6.report.md section 6).
    - PERF3 ran the full suite on real PoB under each: unset (400) 674 passed, 200: 674 passed, 150: 674 passed, 0
      failed in each (POE2T2609); processor time 1,623 s at 400 against 1,055 s at 200.
    - PERF6's timed searches (gate build 2b75e, 400 s, 4 workers, order 400 / 200 / 200 / 400): 48,101 and 55,458
      builds scored at 400, 51,460 and 51,475 at 200 (the same speed within what four runs on a shared PC can show);
      all processes together peaked at 4.2-5.3 GB at 400 against 3.1-3.2 GB at 200 (POE2T2617-2620).
    A value that isn't a whole number in 100..400 is an error, never a silent fallback."""
    text = os.environ.get(GC_PAUSE_VARIABLE, "").strip()
    if not text:
        return GC_PAUSE_DEFAULT
    try:
        pause = int(text)
    except ValueError:
        pause = -1
    if not 100 <= pause <= 400:
        raise PobError(f"{GC_PAUSE_VARIABLE}={text!r}: a whole number from 100 to 400 (400 is PoB's own), or unset "
                       f"for {GC_PAUSE_DEFAULT}")
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
        # after boot: Launch.lua sets PoB's own 400 while it loads
        self.lua.run(f'collectgarbage("setpause", {gc_pause()})', "gc_pause")
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
