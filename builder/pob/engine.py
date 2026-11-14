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


BOOT_GC_VARIABLE = "POE2_BOOT_GC"


def boot_gc() -> bool:
    """Whether the collector keeps this process's own pace (gc_pause) while PoB loads, with one full collection at the
    end of the boot (PERF15, 14 Nov 2026): True unless POE2_BOOT_GC=0, which is the way back.

    Why: Launch.lua line 18 sets the pause to 400 for the load, so an engine's Lua heap climbed to ~250 MB during the
    boot though ~115 MB is in use at its end, and the process kept ~300 MB from Windows (POE2T2746-2747). How: before
    bootstrap.lua, collectgarbage is wrapped so a "setpause" from PoB is ignored; after the boot the real function is
    put back. When the collector runs changes no value PoB computes (no __gc in PoB or the bridge; their weak tables
    are keyed by live objects): the proof is the gate builds' fixed workload and a real suite, on and off."""
    text = os.environ.get(BOOT_GC_VARIABLE, "").strip().lower()
    if text in ("", "1", "on"):
        return True
    if text in ("0", "off"):
        return False
    raise PobError(f"{BOOT_GC_VARIABLE}={text!r}: unset or 1 (on), or 0 (off: PoB's own pause of 400 while it loads)")


BOOT_GC_BEFORE = """
local real = collectgarbage
BOOT_GC_REAL = real
real("setpause", %d)
function collectgarbage(what, ...)
	if what == "setpause" then
		return real("setpause", %d)  -- PoB's Launch.lua asks for 400 while it loads: keep ours (the old value is returned, as ever)
	end
	return real(what, ...)
end
"""
BOOT_GC_AFTER = """
collectgarbage = BOOT_GC_REAL
BOOT_GC_REAL = nil
collectgarbage("collect")
"""

READ_CACHE_VARIABLE = "POE2_READ_CACHE"
# Bridge methods that only read the loaded build (engine/bridge.lua: sourceLines walks the allocated passives, the
# equipped items' lines and the enabled gems; groups describes the socket groups; getConfig copies the Configuration
# tab's input; stats picks fields from the last calculation's output). None of them calculates or writes anything.
CACHED_READS = frozenset({"sourceLines", "groups", "getConfig", "stats"})
# Bridge methods that leave those four answers as they were (PERF14), so the kept answers outlive them. Their own
# answers are not kept (they rarely repeat: POE2T2709). What each does in engine/bridge.lua: activeSkills, cullSplit
# query the last calculation's environment; attributeNodes, allocatedNodes, conqueredNodes, frontier, pointBudget
# read the passive spec; equipped reads the items' text; useRates calculates each asked group in an environment of
# its own (calcs.initEnv "CALCULATOR", which sets no group's displaySkillList or mainActiveSkill: CalcSetup 2393-2432
# write those in "MAIN" / "CALCS" only) and puts build.mainSocketGroup and the group's mainActiveSkill back.
# PERF15: chargeGain queries the last calculation's modDB and output (ModStore's Sum / More / Override assign
# nothing: ModStore.lua 153-345), the flask and charm slots' "active" flags and calcLib.buildSkillInstanceStats (a new
# table of its own); supportCandidates asks calcLib.canGrantedEffectSupportActiveSkill about each gem (locals only).
# exportXml stays OUT on purpose: build:SaveDB is not a read, ItemsTab:Save rebuilds and re-parses every item's text
# (ItemsTab.lua 1219, item:BuildAndParseRaw), so the answers kept before it are dropped as before.
# The proof is POE2_READ_CACHE=check on a whole real suite and the gate builds, not the reading alone.
PASSING_READS = frozenset({"activeSkills", "useRates", "attributeNodes", "equipped", "allocatedNodes", "conqueredNodes",
                           "frontier", "pointBudget", "cullSplit", "chargeGain", "supportCandidates"})


def read_cache() -> str:
    """How this process's engines answer a repeated read (PERF13, 13 Nov 2026): "on" (the default), "off"
    (POE2_READ_CACHE=0: every call goes to PoB, as before) or "check" (POE2_READ_CACHE=check: a repeat is answered
    from the cache AND asked again, and a difference raises PobError: the equality check for this cache).

    Why: the custom models each ask PoB for the build's lines and groups themselves. In one timed gate search (2b75b,
    400 s, 4 workers, POE2T2707) sourceLines was asked 27,790 times (1.4 GB of JSON written by PoB) and groups 62,382
    times; 91 % and 95 % of those repeated a question already answered since the build last changed (POE2T2709).
    What is cached: the answer's bytes for (method, arguments) of the CACHED_READS, dropped at any other call
    (commits, what-ifs, every method not listed, and anything run through engine.lua directly) except the
    PASSING_READS, so an answer is only reused while nothing that could change it has touched PoB. Each caller still
    gets its own decoded copy."""
    text = os.environ.get(READ_CACHE_VARIABLE, "").strip().lower()
    if text in ("", "1", "on"):
        return "on"
    if text in ("0", "off"):
        return "off"
    if text == "check":
        return "check"
    raise PobError(f"{READ_CACHE_VARIABLE}={text!r}: unset or 1 (on), 0 (off) or check")


SETGROUP_VARIABLE = "POE2_SETGROUP"


def setgroup_mode() -> str:
    """How bridge.setGroup recalculates when a skill part or stat set is given (PERF14, 13 Nov 2026): "one" (the
    default: the part is written before the one recalculation), "two" (POE2_SETGROUP=two: recalculate, write,
    recalculate again, as before) or "check" (POE2_SETGROUP=check: one, then the two-step way, and PobError when
    PoB's whole output differs: the equality check for this shortcut)."""
    text = os.environ.get(SETGROUP_VARIABLE, "").strip().lower()
    if text in ("", "1", "one"):
        return "one"
    if text in ("2", "two", "check"):
        return "two" if text == "2" else text
    raise PobError(f"{SETGROUP_VARIABLE}={text!r}: unset or one, two or check")


CALCS_PASS_VARIABLE = "POE2_CALCS_PASS"


def calcs_pass_mode() -> str:
    """When PoB's second calculation pass (the Calcs tab's own, CalcsTab.lua 486) runs (PERF18, 14 Nov 2026): "now"
    (the default: on every recalculation, PoB untouched), "late" (POE2_CALCS_PASS=late: its setup at once, the rest
    when an export next reads its result; at once whenever a gem's Calcs-tab choice differs from its main one:
    engine/bridge.lua's last section) or "check" (POE2_CALCS_PASS=check: a "late" PoB and a "now" PoB side by side
    in this process, every call made on both, PobError when two answers differ: the equality check for "late",
    which sees whatever the pass leaves behind as soon as any answer shows it; twice the memory and time)."""
    text = os.environ.get(CALCS_PASS_VARIABLE, "").strip().lower()
    if text in ("", "0", "now"):
        return "now"
    if text in ("late", "check"):
        return text
    raise PobError(f"{CALCS_PASS_VARIABLE}={text!r}: unset or now, late or check")


def _table_orders_out(value: Any) -> Any:
    """value with every text's runs of PoB's table-order lines sorted (replay.table_order_free: an item's "{base:N}"
    lines, a gem's StatSetIndex lines), which two Lua states write in different orders."""
    from builder.pob import replay
    if isinstance(value, dict):
        return {key: _table_orders_out(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_table_orders_out(item) for item in value]
    return replay.table_order_free(value) if isinstance(value, str) else value


class _Checked:
    """Two Lua states used as one (POE2_CALCS_PASS=check): everything runs on both, the "late" one first, and the
    answers must be the same (replay.same_answer: equal but for the bridge's stopwatch readings and the orders PoB
    picks anew in each state). Returns the "late" state's answer."""

    def __init__(self, late: LuaState, now: LuaState) -> None:
        self.late, self.now = late, now
        self.compared = self.unequal_bytes = 0

    @property
    def calls(self) -> int:
        return self.late.calls

    def run(self, code: str, chunk_name: str, *args: str | bytes) -> list[bytes]:
        if chunk_name == "log_tail":
            return self.late.run(code, chunk_name, *args)
        return self._both("run", chunk_name, code, chunk_name, *args)

    def call(self, function: str, *args: str | bytes) -> list[bytes]:
        return self._both("call", function, function, *args)

    def close(self) -> None:
        self.late.close()
        self.now.close()

    def _both(self, how: str, what: str, *args: str | bytes) -> list[bytes]:
        answers: list[Any] = []
        for state in (self.late, self.now):
            try:
                answers.append(getattr(state, how)(*args))
            except LuaError as error:
                answers.append(error)
        late, now = answers
        if isinstance(late, LuaError) or isinstance(now, LuaError):
            if isinstance(late, LuaError) and isinstance(now, LuaError):
                raise late
            raise PobError(f"{what}: one PoB raised and the other did not ({CALCS_PASS_VARIABLE}=check); "
                           f"put off: {late!r:.2000}; as it is: {now!r:.2000}")
        self.compared += 1
        if late != now:
            from builder.pob import replay  # (replay imports this module)
            self.unequal_bytes += 1
            bridge_call = how == "call" and what == "bridge_dispatch" and len(args) > 1
            method = str(args[1]) if bridge_call else what
            same = False
            if bridge_call and len(late) == len(now) == 2 and late[0] == now[0] == b"true":
                # ({"ok": answer}: the shape a recording keeps, which same_answer's order-free paths are written for)
                a, b = ({"ok": replay._strip_timings(json.loads(answer[1]))} for answer in (now, late))
                same = replay.same_answer(f"call:{method}", a, b)
                if not same:  # (an item's raw text holds the same table-order lines as an export: bridge.equipped)
                    same = _table_orders_out(a) == _table_orders_out(b)
                where = "" if same else replay.difference(a, b)
            else:
                where = f"put off: {late!r:.2000}; as it is: {now!r:.2000}"
            if not same:
                raise PobError(f"{method}: PoB with its second pass put off answers differently from PoB as it is "
                               f"({CALCS_PASS_VARIABLE}=check; the difference is from as it is to put off): {where}")
        return late


class PobEngine:
    def __init__(self, pob_dir: Path = POB_DIR) -> None:
        slots.take()  # inside the PC queue: one of PoE2's 6 LuaJIT slots for this process, or PoolFull
        pob_dir = pob_dir.resolve()  # before chdir, so relative paths mean what the caller meant
        src = pob_dir / "src"
        started = time.perf_counter()
        os.chdir(src)
        calcs = calcs_pass_mode()
        self.lua = self._boot(pob_dir, src, "now" if calcs == "now" else "late")
        if calcs == "check":
            self.lua = _Checked(self.lua, self._boot(pob_dir, src, "now"))  # type: ignore[assignment]
        self.boot_seconds = time.perf_counter() - started
        self._read_cache = read_cache()
        self._reads: dict[tuple[str, str], bytes] = {}  # (method, arguments) -> PoB's answer since the last other call
        self._reads_at = -1  # self.lua.calls after the last call made here: anything else run in Lua since drops them
        self.read_hits = 0

    def _boot(self, pob_dir: Path, src: Path, calcs: str) -> LuaState:
        """A Lua state with PoB and the bridge loaded; calcs: "now" or "late" (calcs_pass_mode)."""
        lua = LuaState(pob_dir / "runtime")
        tight = boot_gc()
        if tight:
            lua.run(BOOT_GC_BEFORE % (gc_pause(), gc_pause()), "boot_gc_before")
        self._run_file(lua, ENGINE_DIR / "bootstrap.lua", src.as_posix())
        self._run_file(lua, ENGINE_DIR / "bridge.lua")
        if tight:
            lua.run(BOOT_GC_AFTER, "boot_gc_after")
        # after boot: Launch.lua sets PoB's own 400 while it loads
        lua.run(f'collectgarbage("setpause", {gc_pause()})', "gc_pause")
        mode = setgroup_mode()
        if mode != "one":
            lua.run(f'BRIDGE_SETGROUP = "{mode}"', "setgroup_mode")
        if calcs != "now":
            lua.run(f'BRIDGE_CALCS_PASS = "{calcs}"', "calcs_pass_mode")
        return lua

    def call(self, method: str, **args: Any) -> Any:
        """Call bridge.<method>(args) and return its decoded JSON result. A repeated read (CACHED_READS) with nothing
        but reads (CACHED_READS, PASSING_READS) called in between is answered from the last answer's bytes (read_cache)."""
        text = json.dumps(args)
        if self._read_cache == "off":
            return json.loads(self._dispatch(method, text))
        if self.lua.calls != self._reads_at or (method not in CACHED_READS and method not in PASSING_READS):
            self._reads.clear()
        if method not in CACHED_READS:
            payload = self._dispatch(method, text)  # (a failed call leaves _reads_at behind: the next call drops)
            if method in PASSING_READS:
                self._reads_at = self.lua.calls
            return json.loads(payload)
        payload = self._reads.get((method, text))
        if payload is None:
            payload = self._reads[(method, text)] = self._dispatch(method, text)
        else:
            self.read_hits += 1
            if self._read_cache == "check":
                real = self._dispatch(method, text)
                if real != payload:
                    raise PobError(f"{method}: the read cache's answer differs from PoB's ({READ_CACHE_VARIABLE}=check); "
                                   f"cached: {payload[:2000]!r}; PoB's: {real[:2000]!r}")
        self._reads_at = self.lua.calls
        return json.loads(payload)

    def _dispatch(self, method: str, text: str) -> bytes:
        try:
            ok, payload = self.lua.call("bridge_dispatch", method, text)
        except LuaError as error:
            raise PobError(f"{method}: {error}") from error
        if ok != b"true":
            raise PobError(f"{method}: {payload.decode('utf-8', 'replace')}")
        return payload

    def close(self) -> None:
        """Give the Lua state's memory back (hundreds of MB). A process that boots engines one after another (the
        tests) must close each: nothing else frees one. Calls after this raise PobError."""
        self.lua.close()

    def log_tail(self, lines: int = 30, lua: LuaState | None = None) -> str:
        try:
            (text,) = (lua or self.lua).run(
                "return table.concat(engineLog or {}, '\\n', math.max(1, #(engineLog or {}) - ...))",
                "log_tail",
                str(lines - 1),
            )
        except LuaError:
            return ""
        return text.decode("utf-8", "replace")

    def _run_file(self, lua: LuaState, path: Path, *args: str) -> None:
        try:
            lua.run(path.read_text(encoding="utf-8"), path.as_posix(), *args)
        except LuaError as error:
            raise PobError(f"{path.name}: {error}\n{self.log_tail(lua=lua)}") from error
