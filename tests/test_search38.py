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
    """(the manager, 23 Nov 15:45; ON by default since 26 Nov 04:50) POE2_SEARCH_ASCEND_UNLOCK=0 never calls
    unlock_trial; ON (the default) reaches it on an Oracle build, whose The Unseen Path unlocks the Oracle-only passives."""
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

    monkeypatch.setenv(Tree.SWITCH_UNLOCK, "0")
    assert Tree.unlock_enabled() is False
    snap = search.build.snapshot()
    search.tree.ascendancy_round(clock.now() + 600)
    assert calls == []

    search.build.restore(snap)
    monkeypatch.delenv(Tree.SWITCH_UNLOCK, raising=False)
    assert Tree.unlock_enabled() is True
    search.tree.ascendancy_round(clock.now() + 600)
    assert calls, "the Oracle build offers no unlocking notable"


def test_the_requests_extra_passive_points_join_the_budget(engine):
    """(R54-7, the manager's ruling: 0 to 2, default 0) The Martyr of the First Edict's and Olroth's Boon's points."""
    from pydantic import ValidationError

    from builder.request.schema import BuildRequest

    assert BuildRequest().extra_passive_points == 0
    assert BuildRequest(extra_passive_points=2).extra_passive_points == 2
    with pytest.raises(ValidationError):
        BuildRequest(extra_passive_points=3)

    engine.call("setExtraPoints", points=0)
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    plain = engine.call("pointBudget")["passives"]
    while engine.call("pointBudget")["used"] < plain:
        add_main_node(engine)
    add_main_node(engine)
    assert over(engine) == 1
    state = engine.call("exportXml")["xml"]
    try:
        engine.call("setExtraPoints", points=1)
        assert engine.call("pointBudget")["passives"] == plain + 1
        assert over(engine) == 0
        engine.call("loadXml", xml=state)  # (a task resumed from a saved state keeps them)
        assert engine.call("pointBudget")["passives"] == plain + 1 and over(engine) == 0
    finally:
        engine.call("setExtraPoints", points=0)
    assert engine.call("pointBudget")["passives"] == plain
    assert over(engine) == 1


@pytest.mark.parametrize("past, kept", [(2, True), (3, False)])
def test_an_import_up_to_two_points_over_keeps_its_tree(engine, past, kept):
    """(the manager, 23 Nov 20:12) Up to 2 points over: a real character's tree isn't trimmed, and the result says why.
    More than 2: trimmed as before, and said. The allowance is the seed line's alone."""
    from dataclasses import replace

    from builder.request.schema import BuildRequest
    from builder.search import objective
    from builder.search.context import make_payload
    from builder.search.inner import Search, seed_task
    from builder.search.skeletons import Skeleton

    engine.call("setExtraPoints", points=0)
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    budget = engine.call("pointBudget")["passives"]
    while engine.call("pointBudget")["used"] < budget + past:
        add_main_node(engine)
    code = codes.encode(engine.call("exportXml")["xml"])

    payload = make_payload(BuildRequest.model_validate({**row["request"], "seed_code": code}))
    found = seed_task(engine, None, payload)
    skeleton = found["skeleton"]
    said = [n for n in found["notes"] if "beyond the standard budget" in n]
    assert skeleton["import_points"] == (past if kept else 0)
    assert bool(said) is kept
    if kept:
        assert f"uses {past} passive point(s)" in said[0] and "Martyr" in said[0] and "Olroth" in said[0]

    search = Search(engine, None, payload, skeleton, 0)
    search.setup_seed(quests=False)
    used = search.build.call("pointBudget")["used"]
    trimmed = [n for n in search.seed_notes if "over a level" in n]
    if kept:
        assert used == budget + past and not trimmed
        assert search.build.stats[objective.POINTS_OVER] == 0
    else:
        assert used == budget and trimmed

    # The search's own lines keep the plain budget, on the same engine
    own = replace(Skeleton(**skeleton), seeded=False, import_points=0).as_dict()
    Search(engine, None, payload, own, 0)
    assert engine.call("pointBudget")["passives"] == budget


def test_pobs_too_many_points_warning_stands_only_past_the_extra_points(engine):
    """PoB's own limit (Build.lua: 99 + quest points) doesn't know the extra points."""
    engine.call("setExtraPoints", points=0)
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    engine.call("setLevel", level=100)
    budget = engine.call("pointBudget")["passives"]
    while engine.call("pointBudget")["used"] < budget + 1:
        add_main_node(engine)
    line = "too many passive points"
    assert any(line in w for w in engine.call("facts", fields=[])["warnings"])
    try:
        engine.call("setExtraPoints", points=1)
        assert not any(line in w for w in engine.call("facts", fields=[])["warnings"])
    finally:
        engine.call("setExtraPoints", points=0)


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


def test_the_meta_screen_tries_the_main_spell_inside_cast_on_critical(engine, monkeypatch):
    """(the manager's 17:21 from MECHB59) OFF changes nothing; ON, the main spell goes inside Cast on Critical with a skill
    the player uses as its feeder, and the trial reads a real trigger rate from that feeder."""
    from builder.request.schema import BuildRequest, Choices
    from builder.search import gems as gems_module
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    group = build.main_group()
    before = [g["name"] for g in group["gems"]]

    monkeypatch.setattr(gems_module, "META_MAIN", False)
    assert gems.meta_main_round(float("inf")) is False
    assert [g["name"] for g in build.main_group()["gems"]] == before

    monkeypatch.setattr(gems_module, "META_MAIN", True)
    assert not gems.has_feeder(group, [group])
    snap = build.snapshot()
    assert gems.apply_meta_main(group, "Cast on Critical", "Frostbolt")
    skills = {s["name"]: s for s in build.call("activeSkills")["skills"]}
    assert skills["Spark"]["triggered"] and skills["Spark"]["group"] == group["index"]
    assert not skills["Frostbolt"]["triggered"]  # (the feeder, used by the player)
    rates = [m["value"] for m in search.assumptions.mods if m.get("skill") == "Spark" and m.get("key") == "triggerRate"]
    # (MECHB59's feed gives a rate above 0; this bare build crits too little to reach META_MIN_RATE: probe POE2T7479 read
    # 5.4e-6 a second, so the trial rejects it, as it should)
    assert rates and max(rates) > 0
    build.restore(snap)
    search.resolve()
    assert gems.trial_meta_main(group, "Cast on Critical", "Frostbolt") is None
    assert [g["name"] for g in build.main_group()["gems"]] == before  # (the trial puts everything back)
