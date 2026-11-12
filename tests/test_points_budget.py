"""Passive points over the level's budget are a hard limit (SEARCH8, Defect C): gate 2b75b's build of 12 Nov 15:34
(benchmarks/results/gate4-20261112-153420.json) held 135 passives against 116 points, and no search check saw it. The
bridge gives PassivePointsOver on every calculation (a what-if adding nodes too), the objective makes it a hard
deficit, and a seed over budget is trimmed. Boots one PoB engine for the module and needs the game-data dump."""

from __future__ import annotations

import faulthandler
import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.pob import codes
from builder.request.schema import BuildRequest
from builder.search import objective
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

OVER = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261112-153420.json"
LABEL = "Passive points over the level's budget"


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


def test_an_over_budget_tree_is_a_hard_deficit_and_a_seed_is_trimmed(engine):
    row = json.loads(OVER.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    budget = engine.call("pointBudget")
    assert (budget["used"], budget["passives"]) == (135, 116)
    assert engine.call("treeCheck")["overBudget"] == 19
    stats = engine.call("stats", fields=[objective.POINTS_OVER])
    assert stats[objective.POINTS_OVER] == 19
    ctx = Context(make_payload(BuildRequest.model_validate(row["request"])))
    deficits = objective.Objective(ctx).deficits(stats)
    assert (LABEL, 19, True) in deficits

    # The same code as a seed: setup trims it to the level's points, and the trimmed build has no deficit for points
    request = BuildRequest.model_validate({**row["request"], "seed_code": row["code"]})
    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), [request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert build.call("pointBudget")["used"] <= 116
    assert build.stats[objective.POINTS_OVER] == 0
    assert not [d for d in build.objective.deficits(build.stats) if d[0] == LABEL]
    assert any(trace.startswith("seed:") for trace in build.over_budget)  # (the seed's load went over: traced)

    # A what-if past the budget sees the points it would go over by
    left = search.tree.points_left()
    far = [n for n in build.call("frontier", maxDistance=left + 2)["nodes"] if n["distance"] == left + 2]
    assert far
    (_, what_if), = build.node_deltas([{"add": [far[0]["id"]], "addPaths": True}])
    assert what_if[objective.POINTS_OVER] == 2
