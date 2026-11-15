"""Incinerate's and Ball Lightning's "Ignited Ground" stat sets: PoB scores their fire as Hits, the game's ground never
Hits (it Ignites at 50% Flammability), so the search never makes them the calculated set (MECH79;
conditions.NO_HIT_STAT_SETS; with Oil Grenade's / Oil Barrage's "Oil Ground" and Gas Arrow's "Poison Cloud"). Spear of Solaris's ground set, which PoB scores as Ignite only, stays a choice."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import conditions
from builder.search.objective import main_dps


class _Build:
    def __init__(self, skills):
        self.skills = skills

    def main_group(self):
        return {"skills": self.skills}


def _conditions(skills):
    c = conditions.Conditions.__new__(conditions.Conditions)
    c.build, c.active = _Build(skills), {}
    return c


def test_the_ground_set_is_never_calculated_on_incinerate_or_ball_lightning_but_is_on_solaris():
    for name, sets in (("Incinerate", ["Incinerate", "Ignited Ground"]),
                       ("Ball Lightning", ["Ball Lightning", "Fire-Infused", "Ignited Ground"])):
        c = _conditions([{"name": name, "statSets": sets}])
        assert not c.allows("Ignited Ground")
        assert c.allows(name)
    for name, label in (("Oil Grenade", "Oil Ground"), ("Oil Barrage", "Oil Ground"), ("Gas Arrow", "Poison Cloud")):
        assert not _conditions([{"name": name, "statSets": ["Base", label]}]).allows(label)
    # (Toxic Grenade's cloud wasn't probed: left a choice)
    assert _conditions([{"name": "Toxic Grenade", "statSets": ["Grenade", "Poison Cloud"]}]).allows("Poison Cloud")
    assert _conditions([{"name": "Spear of Solaris", "statSets": ["Impact", "Pulse", "Ignited Ground"]}]).allows(
        "Ignited Ground")


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


@pytest.mark.parametrize("skill,label", [("Incinerate", "Ignited Ground"), ("Ball Lightning", "Ignited Ground"),
                                         ("Oil Grenade", "Oil Ground")])
def test_the_search_keeps_the_skills_own_cast_not_the_grounds_hits(engine, skill, label):
    """POE2T3127 (before the fix): the search kept the ground set, Incinerate 2,666 DPS of Hits vs 273 for the channel,
    Ball Lightning 549 vs 166; POE2T3128: Oil Grenade's Oil Ground 45.5 vs 22.0."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    chosen = main_dps(build.stats)
    group = build.main_group()
    sets = group["skills"][0]["statSets"]
    build.set_group(index=group["index"], mainActiveSkill=1, statSet=sets.index(label) + 1)
    ground = main_dps(build.stats)
    others = []
    for i, name in enumerate(sets, 1):
        if name != label:
            build.set_group(index=group["index"], mainActiveSkill=1, statSet=i)
            search.settle()
            others.append(main_dps(build.stats))
    print("SETS", skill, chosen, others, ground)
    assert ground > max(others) > 0 or ground > chosen
    assert any(chosen == pytest.approx(o, rel=1e-6) for o in others)


BEIRA = "Creates Ignited Ground for 4 seconds when used, Igniting enemies as though dealing Fire damage equal to 500% of your maximum Life"
FURY = ("Drop Ignited Ground while moving, which lasts 8 seconds and Ignites as though dealing Fire Damage equal to 10% "
        "of your maximum Life")


def test_the_builds_ignited_ground_at_the_boss_from_its_fire_infused_casts_charm_and_trail():
    """conditions.ignited_ground (MECH79): each source at its own rate x duration, combined as independent; no source, 0."""
    near, far = {"_distance_choice": (1.0, "")}, {"_distance_choice": (8.0, "")}
    assert conditions.ignited_ground({}, [], near) == (0.0, "")
    share, how = conditions.ignited_ground({"Incinerate": (0.05, "x"), "Ball Lightning": (0.1, "y")}, [], far)
    assert share == pytest.approx(1 - (1 - 0.05 * 8) * (1 - 0.1 * 4)) and "not counted yet" in how
    # Beira's Anguish: at your feet, so only when you fight within its 2 m
    assert conditions.ignited_ground({}, [], near, {BEIRA: (0.05, "charm")})[0] == pytest.approx(0.05 * 4)
    share, how = conditions.ignited_ground({}, [], far, {BEIRA: (0.05, "charm")})
    assert share == 0.0 and "not counted" in how
    # Birth of Fury's trail: walk through the boss's spot (a play style), not when standing still or far away
    assert conditions.ignited_ground({}, [FURY], near)[0] == 1.0
    assert conditions.ignited_ground({}, [FURY], {**near, "_stationary": True})[0] == 0.0
    assert conditions.ignited_ground({}, [FURY], far)[0] == 0.0


def test_a_wind_skill_or_eye_of_winter_gains_fire_from_the_builds_ignited_ground():
    stats = {"_ignited_ground": (0.4, "Ball Lightning's ground")}
    assert conditions.element_gain_shares("Eye of Winter", stats, [])["Fire"][0] == pytest.approx(0.4)
    assert conditions.element_gain_shares("Twister", stats, [])["Fire"][0] == pytest.approx(0.4)


def test_beiras_anguish_makes_ignited_ground_at_the_charms_uses_on_a_real_build(engine):
    """The charm's line doesn't end "on use": _ignited_charms reads its uses itself (flasks.uses_per_second)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    charm = "\n".join(["Rarity: Unique", "Beira's Anguish", "Dousing Charm", "Item Level: 82", "Implicits: 1",
                       "Used when you become Ignited", "25% Chance to gain a Charge when you kill an enemy", BEIRA])
    from builder.search.context import ItemOption

    belt = "\n".join(["Rarity: Normal", "Rawhide Belt", "Item Level: 82", "Implicits: 2",
                      "25% increased Life Recovery from Flasks", "Has 1 Charm Slot"])
    for slot, raw, kind in (("Belt", belt, "Belt"), ("Charm 1", charm, "Charm")):
        lines = raw.split("\n")
        option = ItemOption(name=lines[1], base=lines[2 if kind == "Charm" else 1], type=kind, sub_type=None, raw=raw,
                            unique=kind == "Charm", price=None, confidence="ssf", level=1)
        build.equip(slot, option, active=True if kind == "Charm" else None)
    search.settle()
    share, how = build.ignited_ground_up
    print("BEIRA", share, how)
    assert "charm's ground" in how and "not in use" not in how
