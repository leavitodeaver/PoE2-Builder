"""SEARCH28: the Infused factor read again after the flow's last state change; the distance trial gets the Presence
radius."""
from types import SimpleNamespace

from builder.search.gems import Gems
from builder.search.inner import Search


def test_refresh_infused_measures_the_chosen_infused_set_again_in_the_finished_state():
    calls = []
    infused = {"label": "Cold-Infused", "factor": 0.273, "share": 0.025, "basis": "", "skill": 1,
               "plain": [None, 1], "set": [None, 2]}

    def measure(group, best, plain, settle_fn):
        calls.append((best[1:4], plain))
        best[4]["factor"] = 0.314  # (what the finished build's state reads)

    group = {"index": 3, "mainActiveSkill": 1}
    gems = SimpleNamespace(build=SimpleNamespace(main_group=lambda: group), assumptions=SimpleNamespace(infused=infused),
                           settle=lambda: True, _infused_in_scored_state=measure)
    assert Gems.refresh_infused(gems) is True
    assert calls == [((1, None, 2), (None, 1))] and infused["factor"] == 0.314
    # nothing to do: no Infused set chosen, another skill calculated, or a state saved before the casts were kept
    gems.assumptions.infused = None
    assert Gems.refresh_infused(gems) is False
    gems.assumptions.infused = {**infused, "skill": 2}
    assert Gems.refresh_infused(gems) is False
    gems.assumptions.infused = {k: v for k, v in infused.items() if k not in ("plain", "set")}
    assert Gems.refresh_infused(gems) is False
    assert len(calls) == 1


def _distance_search(lines, radius, scores):
    """A Search stand-in for choose_distance: the score at each distance from `scores` (metres -> score)."""
    conditions = SimpleNamespace(main_types={"Spell"}, main_skill="", distance=None)
    build = SimpleNamespace(score=0.0, groups=lambda: [])

    def call(name, **kw):
        if name == "sourceLines":
            return {"lines": [{"text": t} for t in lines]}
        assert name == "stats" and kw["fields"] == ["PresenceRadius"]
        return {"PresenceRadius": radius * 10}

    def settle():
        build.score = scores.get(conditions.distance[0] if conditions.distance else 5.0, 0.0)
        return True

    build.call = call
    return SimpleNamespace(conditions=conditions, build=build, settle=settle)


def test_choose_distance_tries_just_beyond_the_presence_for_lioneyes_glare():
    lion = "100% more Accuracy Rating if no enemies are in your Presence"
    search = _distance_search([lion], 4.0, {5.0: 10.0, 2.0: 5.0, 4.5: 12.0})
    assert Search.choose_distance(search) is True
    metres, why = search.conditions.distance
    assert metres == 4.5 and "just beyond its 4.0 m Presence" in why


def test_choose_distance_tries_the_presence_edge_for_in_your_presence_lines():
    line = "Enemies in your Presence have -10% to Fire Resistance"
    search = _distance_search([line], 4.47, {5.0: 10.0, 2.0: 9.0, 4.4: 11.0})
    assert Search.choose_distance(search) is True
    metres, why = search.conditions.distance
    assert metres == 4.4 and "edge of its 4.5 m Presence" in why


def _moving_search(lines, standing, walking, stationary_off=None, moving=False):
    conditions = SimpleNamespace(moving=moving, stationary_off=stationary_off)
    build = SimpleNamespace(score=0.0, stats={})
    build.call = lambda name, **kw: {"lines": [{"text": t} for t in lines]}
    build.objective = SimpleNamespace(dps=lambda stats: build.score * 10)

    def settle():
        build.score = walking if (conditions.moving or conditions.stationary_off == "moving scored better") else standing
        return True

    return SimpleNamespace(conditions=conditions, build=build, settle=settle, play_notes={})


def test_choose_moving_walks_only_when_the_while_moving_lines_score_higher():
    spray = "30% increased Attack Damage while moving"
    search = _moving_search([spray], standing=10.0, walking=12.0)
    assert Search.choose_moving(search) is True and search.conditions.moving is True
    assert search.build.score == 12.0 and "120 DPS walking" in search.play_notes["moving"]["basis"]
    search = _moving_search([spray, "-20% to Accuracy Rating while moving"], standing=10.0, walking=9.0)
    assert Search.choose_moving(search) is False and search.conditions.moving is False and search.build.score == 10.0
    # no line read under Condition:Moving: nothing to choose, a walking build stops walking
    search = _moving_search(["10% reduced Movement Speed Penalty from using Skills while moving"], 10.0, 12.0, moving=True)
    assert Search.choose_moving(search) is True and search.conditions.moving is False
    # choose_stationary already walks the build ("moving scored better"): no second trial
    search = _moving_search([spray, "stand still"], 10.0, 12.0, stationary_off="moving scored better")
    assert Search.choose_moving(search) is False and "moving" not in search.play_notes


def test_totem_skeletons_hold_the_weapons_their_gem_lists():
    """MECHB37: the totems are no Attacks, so they got caster weapons (Wand / Sceptre / Staff) and scored 0."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.skeletons import enumerate_skeletons

    for skill, allowed in (("Artillery Ballista", {"Crossbow"}), ("Mortar Cannon", {"Crossbow"}),
                           ("Ancestral Warrior Totem", {"One Hand Mace", "Two Hand Mace"})):
        skeletons, problems = enumerate_skeletons(Context(make_payload(BuildRequest(main_skill=skill))), [skill])
        assert skeletons and not problems
        assert {s.weapon for s in skeletons} <= allowed, skill
    # a caster spell keeps its caster weapons
    skeletons, _ = enumerate_skeletons(Context(make_payload(BuildRequest(main_skill="Ice Nova"))), ["Ice Nova"])
    assert {s.weapon for s in skeletons} <= {"Wand", "Sceptre", "Staff"}
