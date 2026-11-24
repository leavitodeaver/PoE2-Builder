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


def test_the_ascendancy_unlock_trial_runs_only_behind_its_switch(engine, monkeypatch):
    """(the manager, 23 Nov 15:45) POE2_SEARCH_ASCEND_UNLOCK: OFF (the default) never calls unlock_trial; ON reaches it on
    an Oracle build, whose The Unseen Path unlocks the Oracle-only passives."""
    from builder import clock
    from builder.request.schema import BuildRequest
    from builder.search.context import make_payload
    from builder.search.inner import Search, seed_task
    from builder.search.tree import Tree

    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    request = BuildRequest.model_validate({**row["request"], "seed_code": row["code"]})
    payload = make_payload(request)
    search = Search(engine, None, payload, seed_task(engine, None, payload)["skeleton"], 0)
    search.setup_seed(quests=False)
    calls = []
    monkeypatch.setattr(Tree, "unlock_trial", lambda self, ids, *a: calls.append(ids) or False)

    monkeypatch.delenv(Tree.SWITCH_UNLOCK, raising=False)
    assert Tree.unlock_enabled() is False
    snap = search.build.snapshot()
    search.tree.ascendancy_round(clock.now() + 600)
    assert calls == []

    search.build.restore(snap)
    monkeypatch.setenv(Tree.SWITCH_UNLOCK, "1")
    search.tree.ascendancy_round(clock.now() + 600)
    assert calls, "the Oracle build offers no unlocking notable"


def test_the_requests_extra_passive_points_join_the_budget(engine):
    """(R54-7, the manager's ruling: 0 to 2, default 0) The Martyr of the First Edict's and Olroth's Boon's points."""
    from types import SimpleNamespace

    from pydantic import ValidationError

    from builder.request.schema import BuildRequest
    from builder.search.inner import boss_config

    assert BuildRequest().extra_passive_points == 0
    assert BuildRequest(extra_passive_points=2).extra_passive_points == 2
    with pytest.raises(ValidationError):
        BuildRequest(extra_passive_points=3)
    assert boss_config(SimpleNamespace(extra_passive_points=2)) == {"extraPassivePoints": 2}
    assert boss_config(SimpleNamespace(extra_passive_points=0)) == {}

    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    plain = engine.call("pointBudget")["passives"]
    while engine.call("pointBudget")["used"] < plain:
        add_main_node(engine)
    add_main_node(engine)
    assert over(engine) == 1
    try:
        engine.call("resetConfig", extraPassivePoints=1)
        assert engine.call("pointBudget")["passives"] == plain + 1
        assert over(engine) == 0
    finally:
        engine.call("resetConfig")  # (a request without the field puts the budget back)
    assert engine.call("pointBudget")["passives"] == plain
    assert over(engine) == 1


def test_the_build_reads_its_real_skill_slots(engine):
    """(SEARCHC3's request) 9, + an Unset Ring's implicit, + an anointed Augmented Flesh's 2; a group past them is a hard deficit for a
    search's own build, and not for an import or a reference, which is scored as it stands."""
    from types import SimpleNamespace

    from builder.request.schema import BuildRequest
    from builder.search import objective
    from builder.search.context import Context, gem_group_limit, make_payload

    fields = [objective.SKILL_SLOTS, objective.GEM_GROUPS]
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    assert engine.call("stats", fields=fields)[objective.SKILL_SLOTS] == 9
    assert engine.call("stats", fields=fields)[objective.GEM_GROUPS] >= 1

    # (Ring 2 replaces this build's Kalandra's Touch; in Ring 1 the Touch reflects the ring and PoB reads 11: POE2T7468)
    ring = "Rarity: RARE\nTest Band\nUnset Ring\nImplicits: 1\nGrants 1 additional Skill Slot"
    swapped = engine.call("itemDeltas", cases=[{"slot": "Ring 2", "raw": ring}], fields=fields)["results"][0]
    assert swapped[objective.SKILL_SLOTS] == 10

    # (Augmented Flesh is anoint-only: PassiveSkills.IsAnointmentOnly, no connections in the tree)
    amulet = "Rarity: RARE\nTest Charm\nGold Amulet\nImplicits: 0\nAllocates Augmented Flesh"
    flesh = engine.call("itemDeltas", cases=[{"slot": "Amulet", "raw": amulet}], fields=fields)["results"][0]
    assert flesh[objective.SKILL_SLOTS] == 11

    assert gem_group_limit(SimpleNamespace(stats={objective.SKILL_SLOTS: 11})) == 11
    assert gem_group_limit(SimpleNamespace(stats={})) == 9

    label = "Skill groups over the build's skill slots"
    request = BuildRequest.model_validate(row["request"])
    judge = objective.Objective(Context(make_payload(request)))
    stats = engine.call("stats", fields=sorted(judge.fields))
    assert (label, 1, True) in judge.deficits({**stats, objective.SKILL_SLOTS: 9, objective.GEM_GROUPS: 10})
    assert not [d for d in judge.deficits({**stats, objective.SKILL_SLOTS: 10, objective.GEM_GROUPS: 10}) if d[0] == label]
    judge.groups_enforced = False
    assert not [d for d in judge.deficits({**stats, objective.SKILL_SLOTS: 9, objective.GEM_GROUPS: 13}) if d[0] == label]
