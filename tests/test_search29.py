"""SEARCH29 (MECHC40's two search-scoring inconsistencies; the manager's 18 Nov 17:55 ruling: "fix the scoring, don't
loosen the tests: commits and what-ifs must compute the same way, and the seed snapshot comes after settling").
(a) a committed build's added DPS (the bridge's LedgerAddedDPS) is summed from its own output as a what-if's is;
(b) Search.settle_still resolves until nothing moves, so a seed's start snapshot is a settled build."""
from __future__ import annotations

import faulthandler
import math
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import models
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.objective import ADDED_FIELD
from builder.search.skeletons import enumerate_skeletons


def test_settle_still_resolves_until_nothing_moves_and_stops_at_its_limit():
    moves = iter([True, True, True, False, True])
    search = SimpleNamespace(SETTLE_ROUNDS=Search.SETTLE_ROUNDS, resolve=lambda: next(moves))
    assert Search.settle_still(search) == 3  # (the fourth resolve moved nothing: stop there)
    search = SimpleNamespace(SETTLE_ROUNDS=4, resolve=lambda: True)  # (never still: bounded)
    assert Search.settle_still(search) == 4


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_committed_build_sums_its_added_sources_from_its_own_output_as_a_what_if_does(engine):
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert ADDED_FIELD in build.objective.fields
    # a field-following source (as Thorns' or the Gas Arrow cloud's) and a fixed one, beside the build's own models
    follow = models.added_input("Search29Follow", 0.0, field="TotalDPS", factor=0.25)
    fixed = models.added_input("Search29Fixed", 12.5)
    build.call("setAssumptions", mods=[*(search.assumptions.mods or []), follow, fixed], fields=[])
    committed = build.call("stats", fields=[ADDED_FIELD, "TotalDPS"])
    what_if = build.call("nodeDeltas", cases=[{}], fields=[ADDED_FIELD, "TotalDPS"])["results"][0]
    print("PROBE added", committed, what_if)
    assert committed["TotalDPS"] > 0
    assert committed[ADDED_FIELD] == what_if[ADDED_FIELD]  # (the same sum, from the same output)
    base = build.call("setAssumptions", mods=list(search.assumptions.mods or []), fields=[ADDED_FIELD])["stats"]
    build.call("setAssumptions", mods=[*(search.assumptions.mods or []), follow, fixed], fields=[])
    # (the follower read from the output in hundredths, not its stored 0; the build's own sources as before)
    assert committed[ADDED_FIELD] - number(base.get(ADDED_FIELD)) == pytest.approx(
        math.floor(committed["TotalDPS"] * 0.25 * 100 + 0.5) / 100 + 12.5, abs=1e-6)
    # the score reads it: committed and what-if score the same
    build.refresh()
    assert build.objective.dps(build.stats) == pytest.approx(build.objective.dps(
        {**build.stats, **build.node_deltas([{}])[0][1]}), rel=1e-12)
    # no added source: no field (Objective.dps falls back to added_dps, 0)
    build.call("setAssumptions", mods=[m for m in search.assumptions.mods or []
                                       if not (m.get("kind") == "Derived" and m.get("fn") in models.ADDED_FNS)], fields=[])
    assert ADDED_FIELD not in build.call("stats", fields=[ADDED_FIELD])
    assert ADDED_FIELD not in build.call("nodeDeltas", cases=[{}], fields=[ADDED_FIELD])["results"][0]


def number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
