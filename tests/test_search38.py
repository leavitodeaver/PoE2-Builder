"""SEARCH38 (RESEARCH53 R53-3): the passive-point budget credits "Grants N Passive Skill Point(s)" on every calculation.
PoB sets output.ExtraPoints only in buildOutput's MAIN block, after perform, so PassivePointsOver (set in the wrapped
perform) read 0 granted points and a build holding such a node read N over, on what-ifs and on commits alike."""
from __future__ import annotations

import faulthandler
import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.pob import codes

ORACLE = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261123-073445.json"
PASSIVE_POINT = 11335  # Oracle's "Passive Point": Grants 1 Passive Skill Point (PassiveSkills.SkillPointsGranted 1)
OVER = "PassivePointsOver"


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


def add_main_node(engine) -> int:
    """Allocate one frontier node that costs one main-tree point, and return its id."""
    used = engine.call("pointBudget")["used"]
    for node in engine.call("frontier", maxDistance=1)["nodes"]:
        engine.call("allocate", add=[node["id"]], fields=[OVER])
        if engine.call("pointBudget")["used"] == used + 1:
            return node["id"]
        engine.call("allocate", remove=[node["id"]], fields=[OVER])
    raise AssertionError("no frontier node costs one main-tree point")


def over(engine) -> int:
    return engine.call("stats", fields=[OVER])[OVER]


def test_a_granted_passive_point_is_in_the_budget_on_what_ifs_and_commits(engine):
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    budget = engine.call("pointBudget")
    assert budget["used"] <= budget["passives"]
    while engine.call("pointBudget")["used"] < budget["passives"]:  # spend every point, so one more main node is over
        add_main_node(engine)
    assert over(engine) == 0

    main = add_main_node(engine)
    assert over(engine) == 1  # (one past the level's points, no granted point yet)
    engine.call("allocate", remove=[main], fields=[OVER])

    case = {"add": [PASSIVE_POINT, main], "addPaths": True}
    what_if = engine.call("nodeDeltas", cases=[case], fields=[OVER])["results"][0][OVER]

    engine.call("allocate", add=[PASSIVE_POINT], fields=[OVER])
    ascendancy_over = max(engine.call("pointBudget")["ascendancyUsed"] - 8, 0)
    assert over(engine) == ascendancy_over
    engine.call("allocate", add=[main], fields=[OVER])
    assert over(engine) == ascendancy_over  # (the granted point pays for the main node)
    assert what_if == ascendancy_over  # (a what-if reads the same as the commit)
    check = engine.call("treeCheck")
    assert (check["overBudget"], check["overAscendancy"]) == (0, ascendancy_over)

    add_main_node(engine)
    assert over(engine) == ascendancy_over + 1  # (one main node past the granted point)


def test_gate4_says_which_default_floors_a_reference_misses():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("gate4", root / "benchmarks" / "gate4.py")
    gate4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate4)

    met = gate4.default_floors_note(["Spirit"])  # (another floor is not a default floor)
    assert met["met"] and met["missed"] == [] and "like with like" in met["note"]
    short = ["Movement speed (% increased)", "Survives the boss's Fire hit", "Spirit"]
    missed = gate4.default_floors_note(short)
    assert not missed["met"] and missed["missed"] == short[:2] and "understates" in missed["note"]


def test_the_result_carries_why_items():
    # (a wiring check on Run.result's key-by-key copy: a whole search is too heavy for one key)
    source = (Path(__file__).resolve().parents[1] / "builder" / "search" / "run.py").read_text(encoding="utf-8")
    assert '"why_items": final.get("why_items") or {}' in source
