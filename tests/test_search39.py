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
