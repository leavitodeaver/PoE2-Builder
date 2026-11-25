"""Every test's PoB engine goes through here (PERF1, 12 Nov 2026): lighter test runs, same numbers.

A test that asks for `builder.pob.engine.PobEngine()` gets builder.pob.replay's stand-in: it answers from PoB's
recorded answers where the recording holds the engine's exact history (every call since boot, with its arguments,
under the same bridge.lua / PoB files), and boots the real PoB for anything else, checking the recorded answers
against it as it catches up. A test marked `own_pob_engine` gets a freshly booted engine in place of its module's
shared one (in both modes, so both see the same histories), and that engine is the real PoB in both modes, booted
before the test starts. See builder/pob/replay.py for what keys an answer and why a stale one can't pass.

    (default)           every answer from the real PoB; the recording is refreshed as a by-product
    POE2_REPLAY_POB=1   replay (opt-in until the sourceLines mismatch the first replayed suite found is explained)
    POE2_REAL_POB=1     the real PoB whatever else is set (the manager's merge trains)

Either way an engine is closed when the test or fixture that made it ends: before this, the ~17 engines a suite
boots in the pytest process all stayed alive to the end (several GB; POE2T929's MemoryError).

Tests that start their own PoB processes (EnginePool, a Run with workers, benchmarks' scripts) are untouched:
those are the real PoB always.

PARALLEL PARTS (PERF22, 24 Nov 2026; tools/suite.py): `pytest -n N --dist loadfile --no-loadscope-reorder` runs a
part on N worker processes (pytest-xdist), each test FILE whole on one worker, so a module's shared engine and its
call history are exactly what a serial run gives it. Three things here only act under xdist (a serial run is as before):
- the files are handed out longest first (tests/suite_durations.json, from a serial run's junit XML), so the longest
  file doesn't start last;
- a pool of PoB processes (EnginePool: the tests that run a search on workers) is started by one worker at a time
  (a lock file the run's workers share): every xdist worker holds one of the run's 6 LuaJIT slots
  (builder/pob/slots.py), and two pools at once could ask for more than the slots left, or get fewer workers than the
  same test gets in a serial run;
- each worker's replay summary line is printed by the controller, "[gw0] ...".
"""

from __future__ import annotations

import faulthandler

import pytest

from builder.pob import engine as engine_module
from builder.pob import replay

_RealEngine = engine_module.PobEngine

_stores: dict[str, replay.Store] = {}
_finished: list[replay.Store] = []  # modules that are over: saved, their answers dropped from memory, their counts kept
_fixtures_in_setup: list = []  # the fixtures being set up now, innermost last
_test_engines: list = []  # engines a test body made: closed when the test ends
_shared_engines: list = []  # stand-in engines a fixture made, still open
_current: dict = {"item": None, "forked": None}
_ran: dict[str, int] = {}  # module -> tests that ran to their teardown
_asked_real: dict[str, int] = {}  # test -> answers it needed from the real PoB (replay mode)
_failed: set[str] = set()  # modules with a test that didn't pass


def _boot():
    """A real engine. LuaJIT raises Lua errors as Windows exceptions that PoB's pcall catches; pytest's faulthandler
    would print each one as a "fatal exception" (the tests' own fixtures switch it off for the same reason)."""
    faulthandler.disable()
    return _RealEngine()


def _engine(*args, **kwargs):
    """What `from builder.pob.engine import PobEngine; PobEngine()` gives a test."""
    if args or kwargs:  # another PoB folder: not recorded
        made = _RealEngine(*args, **kwargs)
    else:
        item = _current["item"]
        name = item.path.stem if item is not None else "outside-tests"
        if name not in _stores:
            # a real-PoB run reads no stored answer, so it doesn't open the recording (PERF25: a big one ran Lua out of memory)
            _stores[name] = replay.Store(name, load=not replay.real_pob())
        # an own_pob_engine test's own engine is the real PoB from the start (its calls can't replay; PERF8)
        own = _current["forked"] is not None and not _fixtures_in_setup
        made = replay.ReplayEngine(_stores[name], boot=_boot, live_only=replay.real_pob() or own)
    if _fixtures_in_setup:
        _fixtures_in_setup[-1].addfinalizer(made.close)
        if isinstance(made, replay.ReplayEngine):
            _shared_engines.append(made)
            _fixtures_in_setup[-1].addfinalizer(lambda: _shared_engines.remove(made))
            if _current["forked"] is not None:  # made inside an own_pob_engine test: the others get it as new
                made.fork(live=True)
                _current["forked"].append(made)
    else:
        _test_engines.append(made)
    return made


def _start_trace(folder: str) -> None:
    """POE2_REPLAY_TRACE=<folder>: one line per engine call (test, call, hashes of its arguments, its answer and its answer with
    every list sorted, the
    arguments' first characters) in <folder>/<module>.trace. Two runs' files side by side show the first call whose
    arguments differ (our code isn't the same run to run there) or whose answer does (PoB isn't)."""
    import hashlib
    import json
    import os

    folder = os.path.abspath(folder)  # an engine changes the working directory to PoB's src
    os.makedirs(folder, exist_ok=True)
    files: dict = {}

    def short(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def write(kind: str, arguments: str, answer: str) -> None:
        item = _current["item"]
        name = item.path.stem if item is not None else "outside-tests"
        if name not in files:
            files[name] = open(os.path.join(folder, name + ".trace"), "w", encoding="utf-8")
        timeless = replay._strip_timings(json.loads(answer))
        row = (item.name if item is not None else "-", kind, short(replay._key_order_free(arguments)), short(json.dumps(timeless, sort_keys=True)),
               short(replay.unordered(timeless)), arguments[:240])
        print(*row, sep=chr(9), file=files[name], flush=True)

    replay.trace = write


def _xdist_worker(config) -> bool:
    return hasattr(config, "workerinput")


class _PoolTurn:
    """Under xdist: one worker at a time has an EnginePool open (see the module's docstring). A Windows byte-range lock
    on a file named from the run's id (the same in every worker of one run); Windows lets it go if the worker dies.
    Counted per process, so a pool inside a pool doesn't wait for itself."""

    WAIT_LIMIT = 1800.0  # never hang a run: after 30 min waiting, go on (and say so) as a serial run would

    def __init__(self, run_id: str) -> None:
        import os
        import tempfile

        self.path = os.path.join(tempfile.gettempdir(), f"poe2-pytest-pools-{run_id}.lock")
        self.handle = None
        self.depth = 0

    def take(self) -> None:
        import msvcrt
        import time
        import warnings

        self.depth += 1
        if self.depth > 1:
            return
        self.handle = open(self.path, "a+b")
        end = time.monotonic() + self.WAIT_LIMIT
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() > end:
                    warnings.warn("conftest: waited 30 min for another xdist worker's PoB pool to close; starting this one anyway")
                    return
                time.sleep(0.2)

    def give(self, all_of_it: bool = False) -> None:
        import msvcrt

        if self.depth == 0:
            return
        self.depth = 0 if all_of_it else self.depth - 1
        if self.depth == 0 and self.handle is not None:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # (the 30-minute fall-through above took no lock)
            self.handle.close()
            self.handle = None


_pool_turn: _PoolTurn | None = None


def _pools_take_turns(run_id: str) -> None:
    """EnginePool.__init__ waits for the run's other workers' pools to close; close() lets the next one go."""
    global _pool_turn
    from builder.pob import pool as pool_module

    _pool_turn = turn = _PoolTurn(run_id)
    cls = pool_module.EnginePool
    real_init, real_close = cls.__init__, cls.close

    def __init__(self, *args, **kwargs):
        turn.take()
        self._poe2_pool_turn = True
        try:
            real_init(self, *args, **kwargs)
        except BaseException:
            self._poe2_pool_turn = False
            turn.give()
            raise

    def close(self):
        try:
            real_close(self)
        finally:
            if getattr(self, "_poe2_pool_turn", False):
                self._poe2_pool_turn = False
                turn.give()

    cls.__init__, cls.close = __init__, close


def pytest_collection_modifyitems(session, config, items):
    """Under xdist: whole files, longest first (stable inside a file, so each file's tests keep their order)."""
    if not _xdist_worker(config):
        return
    import json
    from pathlib import Path

    try:
        seconds = json.loads((Path(__file__).with_name("suite_durations.json")).read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return
    known = sorted(seconds.values())
    middle = known[len(known) // 2] if known else 0.0  # a file the list hasn't timed yet: a middling one
    items.sort(key=lambda item: -float(seconds.get(item.path.name, middle)))


def pytest_configure(config):
    if _xdist_worker(config):
        _pools_take_turns(str(config.workerinput.get("testrunuid", "x")))
    config.addinivalue_line("markers", "serial_only: this test starts so many processes that it can't share the run with "
                            "pytest-xdist's workers (the queue allows a run 24 processes): tools/suite.py runs it in a "
                            "second, serial pytest inside the same booking, after the parallel one")
    config.addinivalue_line("markers", "spawns_processes: this test starts processes of its own; under pytest-xdist it "
                            "takes the run's one turn for that (as an EnginePool does), so the run stays under 24")
    config.addinivalue_line("markers", "own_pob_engine: this test's PoB calls differ from run to run (a search the clock cuts "
                            "off), so it gets a freshly booted engine in place of the one its module shares: the tests after "
                            "it keep an engine history that is the same every run, which is what lets their answers replay")
    engine_module.PobEngine = _engine
    import os
    if os.environ.get("POE2_REPLAY_TRACE"):
        _start_trace(os.environ["POE2_REPLAY_TRACE"])


def pytest_unconfigure(config):
    engine_module.PobEngine = _RealEngine
    replay.trace = None


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    _fixtures_in_setup.append(fixturedef)
    try:
        return (yield)
    finally:
        _fixtures_in_setup.pop()


def _forget_pob_answers_kept_for_the_process() -> None:
    """Called when a test file starts. Our code asks PoB some things once per process and keeps the answer
    (builder.search.usage.usage_rules: the bridge's `usageRules`, static gem data). Whichever file's test came first
    made that call, so a file's engine history depended on the files run before it: recorded file by file, the 32nd
    call of test_engine_models.py was usageRules; in a whole-suite run test_engine_items.py had already asked, the
    call was never made, and none of the file's 144,000 answers could replay (PERF9, POE2T2660). Forgetting the kept
    answer here makes each file ask once, in both modes: PoB is asked the same static question again, so no number
    changes. (checked 13 Nov 2026: `global`, functools caches and module-level `_NAME = {} / None` in builder/search,
    builder/report, builder/data and builder/pob/engine.py; usage._RULES is the only one of those filled from an
    engine call. Answers kept on a class or on a shared Context were not searched for: UNVERIFIED. One would show
    as "real PoB answered N call(s)" lines for a file's ordinary tests in a replayed whole suite.)
    PERF10, real PoB, traced: with this in, test_engine_models.py's first test makes the same 997 calls and gets the
    same 997 answers alone (POE2T2672) and after test_usage.py and test_engine_items.py's usage-rule tests
    (POE2T2673); the whole real suite as one process passes with it (POE2T2675, 769 passed)."""
    from builder.search import usage

    usage._RULES = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _current.get("file") != item.path:
        _current["file"] = item.path
        _forget_pob_answers_kept_for_the_process()
    _current["item"] = item
    _current["forked"] = None
    turn = _pool_turn if _pool_turn is not None and item.get_closest_marker("spawns_processes") else None
    if turn is not None:
        turn.take()
    if item.get_closest_marker("own_pob_engine"):
        _current["forked"] = list(_shared_engines)
        for shared in _current["forked"]:
            # the real PoB, booted before the test starts: in replay it used to boot at the test's first unrecorded
            # call, inside its timed step (PERF7's POE2T2643: the two deadline tests failed in replay only)
            shared.fork(live=True)
    before = sum(store.recorded for store in [*_stores.values(), *_finished])
    try:
        return (yield)
    finally:
        if turn is not None:
            turn.give()
        _current["item"] = None
        for shared in _current["forked"] or ():
            if shared in _shared_engines:  # still open: back to the history the module shares
                shared.join()
        _current["forked"] = None
        asked = sum(store.recorded for store in [*_stores.values(), *_finished]) - before
        if asked and not replay.real_pob():
            _asked_real[item.nodeid] = asked
        while _test_engines:
            _test_engines.pop().close()
        if nextitem is None or nextitem.path != item.path:
            _save(item.session, item.path.stem)
            if _pool_turn is not None:  # a pool the file left open: let the other workers' pools go on (never a hang)
                _pool_turn.give(all_of_it=True)


def pytest_runtest_logreport(report):
    name = report.fspath.rsplit("/", 1)[-1].removesuffix(".py")
    if report.failed:
        _failed.add(name)
    if report.when == "teardown":
        _ran[name] = _ran.get(name, 0) + 1


def _save(session, name: str) -> None:
    store = _stores.get(name)
    if store is None:
        return
    # Drop answers nothing asked for only when the whole module ran and passed: a part run or a failed test asks
    # for less, and its answers are still good
    config = session.config
    whole = (not config.option.keyword and not config.option.markexpr and not any("::" in arg for arg in config.args)
             and name not in _failed and not session.shouldstop and not session.shouldfail
             and _ran.get(name, 0) == sum(1 for item in session.items if item.path.stem == name))
    store.save(only_used=whole)
    # The module is over and no open engine still writes to this store: let its answers go (PERF6: every module's
    # answers, ~300 MB of text in all, stayed in the pytest process to the end of a whole-suite run). A later
    # engine under the same name reads the file again.
    if not any(shared._store is store for shared in _shared_engines):
        store.drop()
        _finished.append(_stores.pop(name))


def _summary_lines() -> list[str]:
    if not (_stores or _finished):
        return []
    return [replay.summary([*_finished, *_stores.values()]),
            *(f"  real PoB answered {asked} call(s) for {nodeid}" for nodeid, asked in _asked_real.items())]


_from_workers: list[str] = []  # the controller of an xdist run: each worker's summary lines


def pytest_sessionfinish(session):
    for name in list(_stores):
        _save(session, name)
    if _xdist_worker(session.config):
        session.config.workeroutput["poe2_pob_summary"] = _summary_lines()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    worker = getattr(node, "gateway", None)
    name = getattr(worker, "id", "gw?")
    _from_workers.extend(f"[{name}] {line}" for line in getattr(node, "workeroutput", {}).get("poe2_pob_summary", []))


def pytest_terminal_summary(terminalreporter):
    for line in [*_summary_lines(), *_from_workers]:
        terminalreporter.write_line(line)


@pytest.fixture
def arbiter_default(monkeypatch):
    """Score against the Arbiter of Ash where no boss is named, as before MECHD5 made the invented worst-case pinnacle
    the default (the user's decision, 12 Nov 2026): for tests whose expected numbers were worked out from the Arbiter's
    figures (its red-flash share, stun threshold, cadence, hit mix)."""
    from builder.search import bosses

    monkeypatch.setattr(bosses, "DEFAULT", "Arbiter of Ash")
