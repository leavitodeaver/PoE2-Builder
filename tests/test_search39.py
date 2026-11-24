"""SEARCH39 (the manager's 24 Nov items): the choices between ways to play one build (the boss distance, the main skill's
stat set and part) compare any damage first, then the game limits short, then DPS against the floors at their base
weight (Objective.choice_key); the finished build makes them once more (Search.final_choices: example build 16's Arrow
pick was made mid-flow, POE2T7491); gate4's reference on weapon set 2; the extra weapon-set points; Kalandra's Touch and
an Unset Ring's skill slot; Heart of Ice's Presence line."""
from __future__ import annotations

import math
from types import SimpleNamespace

from builder.search.objective import Objective


def objective(multipliers=None):
    o = SimpleNamespace(multipliers=multipliers or {}, deficits=lambda s: s["d"], value=lambda s: s["v"])
    o._weight = lambda d: Objective._weight(o, d)
    o.penalty = lambda s: o._weight(o.deficits(s))
    return o


POINTS = "Passive points over the level's budget"


def key(o, stats):
    return Objective.choice_key(o, stats)


def test_with_unescalated_floors_the_choice_orders_as_the_score():
    o = objective()
    options = [{"v": 1e6, "d": [("Energy shield", 0.10, False)]}, {"v": 2e6, "d": [("Energy shield", 0.12, False)]},
               {"v": 5e5, "d": []}, {"v": 9e5, "d": [(POINTS, 1.0, True)]}]
    by_score = sorted(range(4), key=lambda i: Objective.score(o, options[i]))
    by_key = sorted(range(4), key=lambda i: key(o, options[i]))
    assert by_score == by_key
    assert key(o, options[2])[2] == Objective.score(o, options[2])


def test_an_escalated_sliver_of_a_floor_no_longer_outweighs_the_damage():
    # (an illustration: the same game limits short in both, the floors escalated to 64x, a sliver apart)
    o = objective({"Energy shield": 64.0, "Physical max hit": 64.0})
    same = [(POINTS, 1.0, True), ("Spirit", 0.4709, True)]
    arrow = {"v": 52.0e6, "d": same + [("Energy shield", 0.1599, False), ("Physical max hit", 0.2025, False)]}
    shards = {"v": 87.1e6, "d": same + [("Energy shield", 0.1615, False), ("Physical max hit", 0.2025, False)]}
    assert Objective.score(o, arrow) > Objective.score(o, shards)  # (the old choice: Arrow, on the escalated floor)
    assert key(o, shards) > key(o, arrow)  # (the new: DPS against the floors at their base weight)
    met = {"v": 52.0e6, "d": same}  # (meeting a floor still counts: e^3 = 20x DPS)
    assert key(o, met) > key(o, shards)


def test_a_tiny_hard_difference_no_longer_outweighs_the_damage():
    o = objective({POINTS: 64.0, "Spirit": 64.0})
    arrow = {"v": 61.4e6, "d": [(POINTS, 2.0, True), ("Spirit", 0.10000, True)]}
    shards = {"v": 61.5e6, "d": [(POINTS, 2.0, True), ("Spirit", 0.10005, True)]}
    assert Objective.score(o, arrow) > Objective.score(o, shards)
    assert key(o, shards) > key(o, arrow)  # (the same limits to 1e-4: the damage decides)


def test_a_smaller_hard_shortfall_wins_whatever_the_damage():
    o = objective()
    short = {"v": 1e9, "d": [(POINTS, 1.0, True)]}
    met = {"v": 1.0, "d": []}
    assert key(o, met) > key(o, short)


def test_a_build_with_no_damage_never_wins_a_choice():
    o = objective()
    none = {"v": 0.0, "d": []}
    weak = {"v": 10.0, "d": [(POINTS, 5.0, True), ("Fire resistance", 1.0, False)]}
    nan = {"v": math.nan, "d": []}
    assert key(o, weak) > key(o, none) and key(o, weak) > key(o, nan)


def test_a_reference_whose_main_skill_is_on_weapon_set_2_takes_the_swap_weapons():
    """(item 3; SEARCHB7: example builds 3 and 11 were refused) reference_request read the weapon from Weapon 1 even when
    the main group is on weapon set 2 only (set1 False)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
    import gate4

    def facts(set1):
        return {"className": "Ranger", "ascendClassName": "Deadeye", "level": 90, "stats": {"Life": 3000},
                "items": [{"slot": "Weapon 1", "type": "Two Hand Mace"}, {"slot": "Weapon 1 Swap", "type": "Bow"},
                          {"slot": "Weapon 2 Swap", "type": "Quiver"}],
                "skillGroups": [{"main": True, "set1": set1, "set2": True, "enabled": True,
                                 "gems": [{"name": "Lightning Arrow", "enabled": True, "support": False}]}]}

    on_two = gate4.reference_request("t", facts(False), [])
    assert on_two.weapon_type == "Bow" and on_two.offhand == "quiver"
    on_one = gate4.reference_request("t", facts(True), [])
    assert on_one.weapon_type == "Two Hand Mace" and on_one.offhand == "none"


# ---- item 4 (the manager's ruling 24 Nov 01:59; RESEARCH56 R56-13 / R56-14), real PoB ----
import faulthandler  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

ORACLE = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261123-073445.json"


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_kalandras_touch_doesnt_copy_an_unset_rings_skill_slot(engine):
    """This Oracle build's Ring 2 is Kalandra's Touch: an Unset Ring in Ring 1 is copied by PoB (11 slots, POE2T7468),
    not by the game (R56-14): 10 counted, PoB's 11 kept as its own field and a result row."""
    from builder.pob import codes
    from builder.search import objective
    from builder.search.inner import skill_slot_notes

    fields = [objective.SKILL_SLOTS, objective.SKILL_SLOTS_POB]
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    assert "Kalandra's Touch" in (engine.call("equipped")["slots"].get("Ring 2") or "")
    ring = "Rarity: RARE\nTest Band\nUnset Ring\nImplicits: 1\nGrants 1 additional Skill Slot"
    one, two = engine.call("itemDeltas", cases=[{"slot": "Ring 1", "raw": ring}, {"slot": "Ring 2", "raw": ring}],
                           fields=fields)["results"]
    assert (one[objective.SKILL_SLOTS], one[objective.SKILL_SLOTS_POB]) == (10, 11)
    assert (two[objective.SKILL_SLOTS], two[objective.SKILL_SLOTS_POB]) == (10, 10)  # (the Touch replaced: nothing copied)
    notes = skill_slot_notes(one)
    assert len(notes) == 1 and notes[0]["value"] == "10 (PoB reads 11)" and not skill_slot_notes(two)


def test_the_extra_weapon_set_points_raise_each_sets_allowance(engine):
    """A request field of its own (Olroth's Boon's Weapon Set point), 0 to 2, default 0: it lowers a weapon set's over-
    allowance count and leaves the main budget alone. (The tree as SEARCH38's Weapon Master probe POE2T7465 built it:
    a level 100 Witchhunter with 30 main passives on weapon set 1 read 6 over its allowance.)"""
    from pydantic import ValidationError

    from builder.pob import codes
    from builder.request.schema import BuildRequest

    assert BuildRequest().extra_weapon_set_points == 0
    with pytest.raises(ValidationError):
        BuildRequest(extra_weapon_set_points=3)
    engine.call("setExtraPoints", points=0, weaponSetPoints=0)
    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    engine.call("setClass", className="Mercenary", ascendancy="Witchhunter")
    engine.call("setLevel", level=100)

    def allocated():
        return {n["id"] if isinstance(n, dict) else int(n) for n in engine.call("allocatedNodes")["nodes"]}

    mains, tried = [], set()
    while len(mains) < 30:
        used, before = engine.call("pointBudget")["used"], allocated()
        options = [n for n in engine.call("frontier", maxDistance=4)["nodes"] if n["id"] not in tried]
        assert options
        node = min(options, key=lambda n: (n["distance"], n["id"]))
        tried.add(node["id"])
        engine.call("allocate", add=[node["id"]], fields=[])
        if engine.call("pointBudget")["used"] > used:
            mains += sorted(allocated() - before)
        else:
            engine.call("allocate", remove=sorted(allocated() - before), fields=[])
    xml = engine.call("exportXml")["xml"]
    match = re.search(r"<Spec\b[^>]*?(/?)>", xml)
    sets = f'<WeaponSet1 nodes="{",".join(map(str, mains[:30]))}"/>'
    xml = (xml[:match.start()] + match.group(0)[:-2] + ">" + sets + "</Spec>" + xml[match.end():] if match.group(1)
           else xml[:match.end()] + sets + xml[match.end():])
    engine.call("loadXml", xml=xml)
    passives = engine.call("pointBudget")["passives"]
    before = engine.call("stats", fields=["PassivePointsOver"])["PassivePointsOver"]
    assert before >= 2  # (POE2T7465: 6)
    try:
        engine.call("setExtraPoints", points=0, weaponSetPoints=2)
        assert engine.call("stats", fields=["PassivePointsOver"])["PassivePointsOver"] == before - 2
        assert engine.call("pointBudget")["passives"] == passives
    finally:
        engine.call("setExtraPoints", points=0, weaponSetPoints=0)
    assert engine.call("stats", fields=["PassivePointsOver"])["PassivePointsOver"] == before

def test_the_finished_build_chooses_its_stat_set_and_distance_again():
    """(item 1, POE2T7491) Build 16's Arrow set at 4 m was chosen mid-flow; in the finished state Shards at 2 m scores
    higher on every count. final_choices keeps a better choice and puts a worse one back."""
    from builder.search.gems import Gems
    from builder.search.inner import Search

    def stand_in(after):
        state = {"stats": {"k": (True, -1.0, 17.9), "d": [("Physical max hit", 0.198, False)]}}
        restored = []
        class Build:
            objective = SimpleNamespace(choice_key=lambda s: s["k"], deficits=lambda s: s["d"])
            stats = property(lambda self: state["stats"])

            def snapshot(self):
                return dict(state["stats"])

            def restore(self, snap):
                restored.append(snap)
                state["stats"] = snap
        build = Build()
        conditions = SimpleNamespace(distance=(4.0, "edge"))

        def choose():
            state["stats"] = after
            conditions.distance = (2.0, "close")
        search = SimpleNamespace(build=build, conditions=conditions, assumptions=None,
                                 gems=SimpleNamespace(choose_active_skill=choose, plain_state=lambda o: Gems.plain_state(o)),
                                 choose_distance=lambda: False, weigh_uptimes=lambda: None)
        return search, restored

    better = {"k": (True, -1.0, 18.7), "d": [("Physical max hit", 0.095, False)]}
    search, restored = stand_in(better)
    assert Search.final_choices(search) is True and not restored and search.conditions.distance[0] == 2.0
    worse_floor = {"k": (True, -1.0, 18.7), "d": [("Physical max hit", 0.30, False)]}  # (more DPS, a worse shortfall)
    search, restored = stand_in(worse_floor)
    assert Search.final_choices(search) is False and restored and search.conditions.distance[0] == 4.0
    lower = {"k": (True, -1.0, 17.0), "d": []}
    search, restored = stand_in(lower)
    assert Search.final_choices(search) is False and restored
