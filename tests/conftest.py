"""Every test's PoB engine goes through here (PERF1, 12 Nov 2026): lighter test runs, same numbers.

A test that asks for `builder.pob.engine.PobEngine()` gets builder.pob.replay's stand-in: it answers from PoB's
recorded answers where the recording holds the engine's exact history (every call since boot, with its arguments,
under the same bridge.lua / PoB files), and boots the real PoB for anything else, checking the recorded answers
against it as it catches up. A test marked `own_pob_engine` gets a freshly booted engine in place of its module's
shared one (in both modes, so both see the same histories). See builder/pob/replay.py for what keys an answer and why a stale one can't pass.

    (default)           every answer from the real PoB; the recording is refreshed as a by-product
    POE2_REPLAY_POB=1   replay (opt-in until the sourceLines mismatch the first replayed suite found is explained)
    POE2_REAL_POB=1     the real PoB whatever else is set (the manager's merge trains)

Either way an engine is closed when the test or fixture that made it ends: before this, the ~17 engines a suite
boots in the pytest process all stayed alive to the end (several GB; POE2T929's MemoryError).

Tests that start their own PoB processes (EnginePool, a Run with workers, benchmarks' scripts) are untouched:
those are the real PoB always.
"""

from __future__ import annotations

import faulthandler

import pytest

from builder.pob import engine as engine_module
from builder.pob import replay

_RealEngine = engine_module.PobEngine

_stores: dict[str, replay.Store] = {}
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
            _stores[name] = replay.Store(name)
        made = replay.ReplayEngine(_stores[name], boot=_boot, live_only=replay.real_pob())
    if _fixtures_in_setup:
        _fixtures_in_setup[-1].addfinalizer(made.close)
        if isinstance(made, replay.ReplayEngine):
            _shared_engines.append(made)
            _fixtures_in_setup[-1].addfinalizer(lambda: _shared_engines.remove(made))
            if _current["forked"] is not None:  # made inside an own_pob_engine test: the others get it as new
                made.fork()
                _current["forked"].append(made)
    else:
        _test_engines.append(made)
    return made


def pytest_configure(config):
    config.addinivalue_line("markers", "own_pob_engine: this test's PoB calls differ from run to run (a search the clock cuts "
                            "off), so it gets a freshly booted engine in place of the one its module shares: the tests after "
                            "it keep an engine history that is the same every run, which is what lets their answers replay")
    engine_module.PobEngine = _engine


def pytest_unconfigure(config):
    engine_module.PobEngine = _RealEngine


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    _fixtures_in_setup.append(fixturedef)
    try:
        return (yield)
    finally:
        _fixtures_in_setup.pop()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _current["item"] = item
    _current["forked"] = None
    if item.get_closest_marker("own_pob_engine"):
        _current["forked"] = list(_shared_engines)
        for shared in _current["forked"]:
            shared.fork()
    before = sum(store.recorded for store in _stores.values())
    try:
        return (yield)
    finally:
        _current["item"] = None
        for shared in _current["forked"] or ():
            if shared in _shared_engines:  # still open: back to the history the module shares
                shared.join()
        _current["forked"] = None
        asked = sum(store.recorded for store in _stores.values()) - before
        if asked and not replay.real_pob():
            _asked_real[item.nodeid] = asked
        while _test_engines:
            _test_engines.pop().close()
        if nextitem is None or nextitem.path != item.path:
            _save(item.session, item.path.stem)


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


def pytest_sessionfinish(session):
    for name in list(_stores):
        _save(session, name)


def pytest_terminal_summary(terminalreporter):
    if _stores:
        terminalreporter.write_line(replay.summary(list(_stores.values())))
        for nodeid, asked in _asked_real.items():
            terminalreporter.write_line(f"  real PoB answered {asked} call(s) for {nodeid}")


@pytest.fixture
def arbiter_default(monkeypatch):
    """Score against the Arbiter of Ash where no boss is named, as before MECHD5 made the invented worst-case pinnacle
    the default (the user's decision, 12 Nov 2026): for tests whose expected numbers were worked out from the Arbiter's
    figures (its red-flash share, stun threshold, cadence, hit mix)."""
    from builder.search import bosses

    monkeypatch.setattr(bosses, "DEFAULT", "Arbiter of Ash")
