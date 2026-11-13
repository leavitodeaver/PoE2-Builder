"""PoB's answers, recorded once and replayed, so most test runs boot no PoB engine (PERF1, 12 Nov 2026).

Why: every test engine is a whole Path of Building in LuaJIT (hundreds of MB, seconds to boot, a processor busy
while it calculates), and most tests check OUR code with numbers PoB produced. The user shares this PC with two
other projects and wants PoE2's runs lighter "without losing accuracy". So nothing here may change an answer.

How an answer is keyed (everything that can change it):
- the engine's whole history: an answer is stored under a hash chain of EVERY call made on that engine since it
  booted, each with its exact JSON arguments (the bytes PoB would get). The build, the request, the ledger entries
  and the model assumptions all reach PoB only through those calls, so a change in any of them changes the chain;
- a fingerprint of the code that answers: engine/bridge.lua, engine/bootstrap.lua, builder/pob/engine.py and lua.py,
  the vendor PoB commit and every file under PoB's src/ and runtime/ (path, size, time changed). Recordings made
  under another fingerprint are never read (and are deleted). The bridge reads no file under data/ itself
  (checked 12 Nov 2026: no io.open / io.lines / loadfile / dofile in engine/bridge.lua, and bootstrap.lua opens
  PoB's own files only; data/ reaches PoB through call arguments).

A call the recording doesn't hold (a new test, a changed argument, a search that got further in its time) is never
guessed: the engine boots the real PoB, runs every call made so far on it again, checks each stored answer against
the real one (ReplayMismatch if they differ: loud, never a pass on a stale answer), answers the new call for real
and records it. After that it replays again where it can, and catches the real engine up only when it must.

Replay is opt-in for now: POE2_REPLAY_POB=1. Without it, or with POE2_REAL_POB=1 (the manager's merge trains; it
wins), everything runs against the real PoB: no stored answer is read; the recording is refreshed as a by-product.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import zstandard

from builder import ENGINE_DIR, POB_DIR, ROOT
from builder.pob.engine import PobEngine, PobError, gc_pause
from builder.pob.lua import LuaError

RECORDINGS = ROOT / "data" / "pob-recordings"
TIMING_KEYS = frozenset({"seconds"})  # the bridge's own stopwatch readings: never equal twice, never a build's number


class ReplayMismatch(AssertionError):
    """The real PoB gave a different answer from the recorded one for the same history: the recording was stale
    (something the fingerprint doesn't cover changed) or PoB isn't deterministic there. Tests before this one may
    have passed on the old answers: rerun with POE2_REAL_POB=1."""


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no")


def real_pob() -> bool:
    """Real PoB unless POE2_REPLAY_POB=1 asks for replay; POE2_REAL_POB=1 wins over it. Replay is opt-in for now
    (PERF1, 12 Nov 2026): the first replayed suite found PoB's sourceLines answer differing between two engines
    with the same history (ReplayMismatch, in test_engine_items after a clock-cut search), not yet explained."""
    return _on("POE2_REAL_POB") or not _on("POE2_REPLAY_POB")


_fingerprint: str | None = None


def fingerprint(pob_dir: Path = POB_DIR) -> str:
    """Hash of everything on disk that decides PoB's answer to a call (see the module's head)."""
    global _fingerprint
    if _fingerprint is not None and pob_dir == POB_DIR:
        return _fingerprint
    digest = hashlib.sha256()
    for path in (ENGINE_DIR / "bridge.lua", ENGINE_DIR / "bootstrap.lua", Path(__file__).with_name("engine.py"),
                 Path(__file__).with_name("lua.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    try:
        head = subprocess.run(["git", "-C", str(pob_dir), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    digest.update(b"pob " + head.encode() + b"\0")
    for part in ("src", "runtime"):
        base = pob_dir / part
        rows = []
        for folder, _, files in os.walk(base):
            for name in files:
                stat = os.stat(os.path.join(folder, name))
                rows.append(f"{os.path.relpath(os.path.join(folder, name), base)}|{stat.st_size}|{stat.st_mtime_ns}")
        digest.update("\n".join(sorted(rows)).encode("utf-8", "surrogateescape") + b"\0")
    digest.update(b"CI=" + os.environ.get("CI", "").encode() + b"\0")  # bootstrap.lua hands it to PoB
    # answers recorded under one collector pause are never replayed under another; the pause in force is part of the
    # fingerprint (unset = the default, so unset and POE2_LUA_GC_PAUSE=200 share their recordings)
    digest.update(b"GC_PAUSE=" + str(gc_pause()).encode() + b"\0")
    value = digest.hexdigest()[:20]
    if pob_dir == POB_DIR:
        _fingerprint = value
    return value


def difference(stored: Any, real: Any, path: str = "answer") -> str:
    """Where two answers first differ, in words (for ReplayMismatch: PERF1's first mismatch gave no way to tell a
    reordered list from a changed number)."""
    if isinstance(stored, dict) and isinstance(real, dict):
        if stored.keys() != real.keys():
            return f"{path}: keys differ (only recorded: {sorted(stored.keys() - real.keys())[:5]}, only real: {sorted(real.keys() - stored.keys())[:5]})"
        if list(stored) != list(real) and all(stored[key] == real[key] for key in stored):
            return f"{path}: the same keys and values, the keys in another order"
        for key in stored:
            if stored[key] != real[key]:
                return difference(stored[key], real[key], f"{path}.{key}")
    if isinstance(stored, list) and isinstance(real, list):
        if len(stored) != len(real):
            return f"{path}: {len(stored)} items recorded, {len(real)} real"
        texts = [sorted(json.dumps(item, sort_keys=True) for item in side) for side in (stored, real)]
        if texts[0] == texts[1]:
            first = next(index for index, (a, b) in enumerate(zip(stored, real)) if a != b)
            return f"{path}: the same {len(stored)} items in another order (first at [{first}]: {json.dumps(stored[first])[:160]} recorded, {json.dumps(real[first])[:160]} real)"
        for index, (a, b) in enumerate(zip(stored, real)):
            if a != b:
                return difference(a, b, f"{path}[{index}]")
    return f"{path}: {json.dumps(stored)[:200]} recorded, {json.dumps(real)[:200]} real"


def unordered(value: Any) -> str:
    """The value as text with every list's items sorted: equal for two answers that differ in order alone."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(key)}:{unordered(value[key])}" for key in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted(unordered(item) for item in value)) + "]"
    return json.dumps(value)


# Answers PoB builds by walking a text-keyed table, whose order LuaJIT picks anew in every process (PERF2, 12 Nov
# 2026: PoB's LuaJIT 2.1 seeds its string hash per state; scratch/perf2/order.py, three processes, three orders of the
# same 17 slot names). Only what is proven both ways is listed: bridge.sourceLines fills `lines` inside
# `pairs(build.itemsTab.slots)` (engine/bridge.lua 3107-3112), and two real-PoB runs of test_engine_items.py with
# the same calls gave 38 sourceLines answers that differed, every one the same lines in another order (POE2T2607).
# Any other difference, in this answer or another, is still a ReplayMismatch.
# bridge.unsupportedInBuild fills `lines` the same way (`pairs(build.spec.allocNodes)` then
# `pairs(build.itemsTab.slots)`, engine/bridge.lua 3437-3458): two processes gave the same lines in another order for 99
# of 120 builds (PERF6, POE2T2621) and for 11 of 13 (PERF7, POE2T2635 / 2636), and never another line or value.
ORDER_FREE = {"call:sourceLines": ("ok", "lines"), "call:unsupportedInBuild": ("ok", "lines")}


def same_answer(kind: str, stored: Any, real: Any) -> bool:
    """Is the recorded answer one the real PoB could give now: equal, or (ORDER_FREE) equal but for one list's order."""
    if stored == real:
        return True
    path = ORDER_FREE.get(kind)
    if path is None:
        return False
    a, b = stored, real
    for key in path[:-1]:
        if not (isinstance(a, dict) and isinstance(b, dict) and key in a and key in b):
            return False
        if {k: v for k, v in a.items() if k != key} != {k: v for k, v in b.items() if k != key}:
            return False
        a, b = a[key], b[key]
    last = path[-1]
    if not (isinstance(a, dict) and isinstance(b, dict) and isinstance(a.get(last), list) and isinstance(b.get(last), list)):
        return False
    if {k: v for k, v in a.items() if k != last} != {k: v for k, v in b.items() if k != last}:
        return False
    return sorted(json.dumps(item, sort_keys=True) for item in a[last]) == sorted(json.dumps(item, sort_keys=True) for item in b[last])


def _key_order_free(arguments: str) -> str:
    """A call's arguments with every object's keys sorted, for the chain only (the real PoB still gets the caller's
    own bytes). Our Python sends the same setConfig vars in another key order from run to run (POE2T2607: call 533
    of test_engine_items.py, {"parryActive", "conditionEnemyIgnited"} one run and reversed the next), which made
    every later answer on that engine unreplayable. Key order can't change what the bridge reads from an object;
    it can change the order PoB walks the table made from it, which is the per-process variation above."""
    return json.dumps(json.loads(arguments), sort_keys=True)


trace: Callable[[str, str, str], None] | None = None  # tests/conftest.py's POE2_REPLAY_TRACE: every call, its arguments, its answer


def _strip_timings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_timings(item) for key, item in value.items() if key not in TIMING_KEYS}
    if isinstance(value, list):
        return [_strip_timings(item) for item in value]
    return value


class Store:
    """One file of recorded answers (chain hash -> answer), for one test module, under one fingerprint."""

    def __init__(self, name: str, folder: Path | None = None) -> None:
        self.root = folder or RECORDINGS
        self.folder = self.root / fingerprint()
        self.path = self.folder / f"{name}.json.zst"
        self.entries: dict[str, str] = {}
        self.used: set[str] = set()
        self.dirty = False
        self.hits = self.recorded = self.boots = 0
        if self.path.exists():
            try:
                # (a stream: save() writes one, whose frame carries no size for decompress() to go by. Read in pieces
                # (PERF9): decompressobj().decompress() of the whole file took 108 s for test_engine_models' 6.6 MB
                # -> 284 MB of answers, in real runs and replayed ones alike (POE2T2656's profile); in pieces, 0.2 s)
                pieces = []
                with open(self.path, "rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                    while piece := reader.read(1 << 24):
                        pieces.append(piece)
                self.entries = json.loads(b"".join(pieces))
                del pieces
            except (OSError, ValueError, zstandard.ZstdError):
                self.entries = {}  # unreadable: as good as absent, the real PoB answers and it's written again

    def get(self, chain: str) -> str | None:
        answer = self.entries.get(chain)
        if answer is not None:
            self.used.add(chain)
        return answer

    def put(self, chain: str, answer: str) -> None:
        self.used.add(chain)
        if self.entries.get(chain) != answer:
            self.entries[chain] = answer
            self.dirty = True

    def save(self, only_used: bool = False) -> None:
        """Write the file if anything changed. only_used (every test of the module ran): drop answers no call asked
        for, so histories that can't recur (a timed search's) don't pile up."""
        if only_used and len(self.used) != len(self.entries):
            self.entries = {chain: answer for chain, answer in self.entries.items() if chain in self.used}
            self.dirty = True
        if not self.dirty:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        for other in self.root.iterdir():
            if other.is_dir() and other != self.folder:  # another fingerprint's answers: stale for good
                for file in other.glob("*.json.zst*"):
                    file.unlink(missing_ok=True)
                try:
                    other.rmdir()
                except OSError:
                    pass
        temporary = self.path.with_suffix(f".zst.{os.getpid()}.tmp")
        # Written answer by answer (PERF6): one json.dumps of everything held a second and third copy of a module's
        # answers in memory while it was compressed (test_engine_items: +450 MB at its end, POE2T2615). Same JSON.
        with open(temporary, "wb") as raw, zstandard.ZstdCompressor(level=6).stream_writer(raw) as out:
            separator = b"{"
            for chain, answer in self.entries.items():
                out.write(separator + json.dumps(chain).encode("ascii") + b": " + json.dumps(answer).encode("utf-8"))
                separator = b", "
            out.write(b"}" if self.entries else b"{}")
        os.replace(temporary, self.path)
        self.dirty = False


class _Lua:
    """engine.lua.run(...) for the few tests that reach into Lua directly: recorded like any call."""

    def __init__(self, owner: ReplayEngine) -> None:
        self._owner = owner

    def run(self, code: str, chunk_name: str, *args: str | bytes) -> list[bytes]:
        texts = [arg.decode("utf-8") if isinstance(arg, bytes) else arg for arg in args]
        return self._owner._do("lua.run", json.dumps([code, chunk_name, *texts]))


class ReplayEngine:
    """Stands in for PobEngine: the same call / lua.run / log_tail, answered from the store where it holds the
    engine's exact history, by the real PoB otherwise (and always when `live_only`)."""

    def __init__(self, store: Store, boot: Callable[[], PobEngine] = PobEngine, live_only: bool = False) -> None:
        self._store, self._boot, self._live_only = store, boot, live_only
        self._chain = hashlib.sha256(b"PobEngine()").hexdigest()
        self._log: list[tuple[str, str, str] | None] = []  # every call so far: (kind, argument JSON, its chain hash); None once the real engine ran it
        self._engine: PobEngine | None = None
        self._synced = 0  # how many of _log the real engine has run
        self._closed = False
        self._parked: list[tuple] = []  # fork(): the shared history set aside meanwhile
        self.boot_seconds = 0.0
        self.lua = _Lua(self)
        os.chdir(POB_DIR.resolve() / "src")  # as PobEngine does: a test finds the same working directory either way
        if live_only:
            self._real()  # at once, as PobEngine() does: a boot that fails, fails where it always did

    # -- the PobEngine interface ------------------------------------------------------------
    def call(self, method: str, **args: Any) -> Any:
        return self._do("call:" + method, json.dumps(args))

    def log_tail(self, lines: int = 30) -> str:
        return self._engine.log_tail(lines) if self._engine else ""

    def close(self) -> None:
        self._closed = True
        while self._engine or self._parked:
            if self._engine:
                self._engine.close()
            self._engine = self._parked.pop()[2] if self._parked else None

    def fork(self, live: bool = False) -> None:
        """Until join(): behave as a newly booted engine (its own history, its own real PoB if it needs one). For a
        test whose calls differ run to run (a search cut off by the clock) on an engine other tests share: without
        this, no test after it on that engine could ever replay, their history never being the same twice.
        `live`: every answer until join() comes from the real PoB, booted here and now, as under POE2_REAL_POB=1
        (PERF8: such a test's calls can't replay anyway, and booting the real PoB at its first unrecorded call put
        the ~5 s boot and the catch-up inside the test's own timed step, PERF7's POE2T2643)."""
        self._parked.append((self._chain, self._log, self._engine, self._synced, self._live_only))
        self._chain, self._log, self._engine, self._synced = hashlib.sha256(b"PobEngine()").hexdigest(), [], None, 0
        self._live_only = self._live_only or live
        if self._live_only:
            self._real()

    def join(self) -> None:
        """End fork(): close its real engine and go back to the history before it."""
        if self._engine:
            self._engine.close()
        self._chain, self._log, self._engine, self._synced, self._live_only = self._parked.pop()

    # -- record / replay --------------------------------------------------------------------
    def _do(self, kind: str, arguments: str) -> Any:
        if self._closed:
            raise PobError(f"{kind}: this engine was closed when its test or fixture ended")
        self._chain = hashlib.sha256(f"{self._chain}\0{kind}\0{_key_order_free(arguments)}".encode("utf-8")).hexdigest()
        self._log.append((kind, arguments, self._chain))
        answer = None if self._live_only else self._store.get(self._chain)
        if answer is None:
            answer = self._catch_up()
        else:
            self._store.hits += 1
        if trace is not None:
            trace(kind, arguments, answer)
        return self._decode(answer)

    def _real(self) -> PobEngine:
        if self._engine is None:
            self._engine = self._boot()
            self.boot_seconds = self._engine.boot_seconds
            self._store.boots += 1
        return self._engine

    def _catch_up(self) -> str:
        """Run on the real PoB every call it hasn't seen, in order; returns the last one's answer. A call with a
        stored answer must get the same one again; the others are recorded."""
        engine = self._real()
        answer = ""
        while self._synced < len(self._log):
            kind, arguments, chain = self._log[self._synced]
            # The real engine has this call now and nothing reads it again: let its arguments go (whole builds' XML,
            # tens of thousands of calls on a module's shared engine, were kept for the engine's life before; PERF6)
            self._log[self._synced] = None
            self._synced += 1
            answer = self._ask(engine, kind, arguments)
            stored = None if self._live_only else self._store.entries.get(chain)
            self._store.put(chain, answer)
            if stored is None:
                self._store.recorded += 1
            elif not same_answer(kind, _strip_timings(json.loads(stored)), _strip_timings(json.loads(answer))):
                raise ReplayMismatch(
                    f"PoB's recorded answer to {kind} (call {self._synced} on this engine) differs from the real PoB's now, "
                    "with the same history and the same engine files: the recording was stale or PoB isn't deterministic "
                    "here. Tests that ran before this may have passed on old answers: rerun with POE2_REAL_POB=1. "
                    + difference(_strip_timings(json.loads(stored)), _strip_timings(json.loads(answer))))
        return answer

    @staticmethod
    def _ask(engine: PobEngine, kind: str, arguments: str) -> str:
        """The real answer, as the JSON text the store keeps (an error is an answer too: PoB keeps its state after one)."""
        try:
            if kind == "lua.run":
                code, chunk_name, *args = json.loads(arguments)
                results = engine.lua.run(code, chunk_name, *args)
                return json.dumps({"lua": [base64.b64encode(item).decode("ascii") for item in results]})
            return json.dumps({"ok": engine.call(kind.partition(":")[2], **json.loads(arguments))})
        except PobError as error:
            return json.dumps({"PobError": str(error)})
        except LuaError as error:
            return json.dumps({"LuaError": str(error)})

    @staticmethod
    def _decode(answer: str) -> Any:
        value = json.loads(answer)  # a fresh object each time: a test may change what it gets
        if "ok" in value:
            return value["ok"]
        if "lua" in value:
            return [base64.b64decode(item) for item in value["lua"]]
        if "PobError" in value:
            raise PobError(value["PobError"])
        raise LuaError(value["LuaError"])


def summary(stores: list[Store]) -> str:
    hits, recorded, boots = (sum(getattr(store, name) for store in stores) for name in ("hits", "recorded", "boots"))
    mode = "POE2_REAL_POB=1, every answer from the real PoB" if real_pob() else "replay"
    return f"PoB engines ({mode}): {hits} answers replayed, {recorded} recorded, {boots} real engine(s) booted in this process"
