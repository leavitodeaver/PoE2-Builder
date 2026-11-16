"""Incinerate's and Ball Lightning's "Ignited Ground" stat sets: PoB scores their fire as Hits, the game's ground never
Hits (it Ignites at 50% Flammability), so the search never makes them the calculated set (MECH79;
conditions.NO_HIT_STAT_SETS; with Oil Grenade's / Oil Barrage's "Oil Ground" and Gas Arrow's "Poison Cloud"). Spear of Solaris's ground set, which PoB scores as Ignite only, stays a choice."""
from __future__ import annotations

import faulthandler
import math

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
def test_the_search_keeps_the_skills_own_cast_not_the_grounds_hits(engine, skill, label, monkeypatch):
    """POE2T3127 (before the fix): the search kept the ground set, Incinerate 2,666 DPS of Hits vs 273 for the channel,
    Ball Lightning 549 vs 166; POE2T3128: Oil Grenade's Oil Ground 45.5 vs 22.0. (Incinerate given a full tank of fuel
    here: the bare build has none, which would score every set 0.)"""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    monkeypatch.setattr(conditions, "incinerate_channel", lambda *a, **k: (1.0, 5.0, "test: a full tank"))
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


def test_incinerate_channels_only_the_fuel_its_builds_other_skills_buy():
    """conditions.incinerate_channel (MECH79): fuel only from Mana spent on other skills; none, 0."""
    share, channel, how = conditions.incinerate_channel(0.0, 2)
    assert share == 0.0 and "no other skill" in how
    # 100 Mana/s at 2 ms per Mana: 0.2 s of channelling per fight second, 5 s channels; with 20% of the fight already
    # taken for the presses, 0.2 / 0.8 of the main skill's time left
    share, channel, how = conditions.incinerate_channel(100.0, 2)
    assert share == pytest.approx(0.2) and channel == 5.0
    assert conditions.incinerate_channel(100.0, 2, busy=0.2)[0] == pytest.approx(0.25)
    assert conditions.incinerate_channel(100.0, 2, busy=1.0)[0] == 0.0
    assert conditions.incinerate_channel(1e6, 7)[0] == pytest.approx(1.0, abs=1e-3)


def test_compounding_multiplies_the_kth_ignite_of_a_channel_by_k():
    """conditions.compounding_factor (MECH79): one Ignite a channel is x1; more Ignites a channel compound."""
    assert conditions.compounding_factor(0.1, 5.0, 0.0, 4.0) == (1.0, 1)
    assert conditions.compounding_factor(0.0, 5.0, 0.0, 4.0) == (1.0, 0)
    # 2 Ignites/s over a 5 s channel, 4 s Ignites, a long gap: during the channel 1..10, then x10 for 4 s, then nothing
    factor, n = conditions.compounding_factor(2.0, 5.0, 20.0, 4.0)
    assert n == 10
    expected = (sum(k * 0.5 for k in range(1, 10)) + 10 * 0.5 + 10 * 3.5) / (5.0 + 3.5)  # (the 10th lands at 4.5 s)
    assert factor == pytest.approx(expected, rel=0.01)
    # recast at once: the last channel's x10 outlives the next channel's first Ignites (one live at a time)
    assert conditions.compounding_factor(2.0, 5.0, 0.0, 4.0)[0] > factor


def test_the_compounding_and_idle_lines_go_to_the_main_skill_only():
    from builder.search import models

    mods, notes = models.compound_ignite_mods("Incinerate", ("Incinerate", 3.0, "how"))
    idle = next(m for m in mods if m["line"] == models.INCINERATE_IDLE_LINE)
    assert idle["skill"] == "Incinerate" and idle["when"] == [{"pob": "IncinerateChannelling", "neg": True}]
    more = next(m for m in mods if m["line"] == models.COMPOUND_LINE)
    assert more["scale"] == pytest.approx(200.0) and more["skill"] == "Incinerate" and notes
    assert models.compound_ignite_mods("Flame Breath", ("Incinerate", 3.0, "how")) == ([], [])
    assert models.compound_ignite_mods("Flame Breath", ("Flame Breath", 1.0, "how")) == ([], [])


def test_pob_reads_the_lines_as_ignite_magnitude_and_less_damage(engine):
    from builder.search import models

    out = engine.call("parseLines", lines=[models.COMPOUND_LINE, models.INCINERATE_IDLE_LINE])["results"]
    print("PARSE", out)
    assert all(r["supported"] for r in out)
    ignite, idle = out[0]["parsed"][0], out[1]["parsed"][0]
    assert ignite["name"] == "AilmentMagnitude" and ignite["type"] == "MORE" and ignite["keywordFlags"]
    assert idle["name"] == "Damage" and idle["type"] == "MORE" and idle["value"] == -100


def test_incinerate_without_fuel_scores_nothing_on_a_real_build(engine):
    """A bare Incinerate build spends no Mana on other skills: no fuel, the Channelling box off, its damage 0. (Fed by a
    pressed Fireball, POE2T3139: 0.5/s, 104 Mana, 2 ms a Mana: channelling 26% of the 40% the presses leave, 28.4 vs 273.)"""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Incinerate"))
    skeleton = enumerate_skeletons(Context(payload), ["Incinerate"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    print("FUEL", search.conditions.active.get(conditions.INCINERATE_CHANNEL_VAR), main_dps(build.stats))
    # (the skeleton's other groups, Spellslinger and Raise Shield, spend no Mana: POE2T3138)
    share, _, how = search.conditions._incinerate_fuel()
    assert share == 0.0 and "no other skill" in how
    assert not search.conditions.active.get(conditions.INCINERATE_CHANNEL_VAR)
    assert main_dps(build.stats) == pytest.approx(0.0, abs=1e-6)


# MECH80: Ignites that don't Hit (Radiant Grief's aura, your Ignited Ground) need the boss at 50% Flammability
GRIEF = "Enemies in your Presence are Ignited as though dealt 200 Base Fire Damage"
THRESHOLD = {"EnemyAilmentThreshold": 71303.0}  # (PoB's monster table at level 82)


def test_flammability_reach_is_the_poisson_tail_of_the_live_stacks():
    """1 application a second of 10% each, live 8 s: 8 stacks on average, 5 needed for 50%."""
    want = 1 - sum(math.exp(-8) * 8 ** k / math.factorial(k) for k in range(5))
    assert conditions.flammability_reach([("x", 1.0, 10.0)]) == pytest.approx(want)
    assert conditions.flammability_reach([("x", 1.0, 60.0)]) == pytest.approx(1 - math.exp(-8))  # (one stack is enough)
    assert conditions.flammability_reach([]) == 0.0


def test_an_aura_alone_never_lifts_a_boss_to_50_percent_but_a_fire_builds_hits_do():
    """Radiant Grief's 200 Fire is 0.06% a application on a level-82 boss: it Ignites only on the build's Flammability."""
    assert conditions.no_hit_ignite(THRESHOLD, 200.0, 8.0)[0] < 1e-6
    hitting = {**THRESHOLD, "Speed": 3.0, "IgniteChancePerHit": 20.0, "HitChance": 100.0}
    reach, how = conditions.no_hit_ignite(hitting, 200.0, 8.0)
    assert reach > 0.99 and "LOW" in how
    weak = {**THRESHOLD, "Speed": 1.0, "IgniteChancePerHit": 3.0}
    assert conditions.no_hit_ignite(weak, 200.0, 8.0)[0] < 0.01
    # (the other groups' Ignites count too: 2 hits/s at 25% a hit)
    others = {**THRESHOLD, "_ailment_others": {"Ignite": [("Fireball", 2.0, 0.25, 4.0)]}}
    assert conditions.no_hit_ignite(others, 200.0, 8.0)[0] > 0.99


def test_the_grounds_ignite_reads_its_as_though_damage_from_your_life():
    life = {**THRESHOLD, "Life": 3000.0}
    assert conditions._as_though_life(BEIRA, life) == pytest.approx(15000.0)
    assert conditions._as_though_life(FURY, life) == pytest.approx(300.0)
    assert conditions._as_though_life(GRIEF, life) is None
    # the build's Flammability Magnitude modifiers scale the source's own applications (the bridge's flammabilityMagnitude)
    raw = conditions.as_though_flammability(15000.0, life)
    assert raw == pytest.approx(15000 / 71303 * 20)
    boosted = {**life, "_flammability_mod": {"inc": 100.0, "more": 1.5}}
    assert conditions.as_though_flammability(15000.0, boosted) == pytest.approx(raw * 2 * 1.5)
    # Beira's 15,000 Fire: 4.2% a application, 8 in its 4 s: short of 50% alone most of the time
    share, how = conditions.ground_ignite([("the charm's ground", 0.3, 4.0, 15000.0)], life)
    assert 0.0 < share < 0.3 * 0.5 and "15000 Fire" in how
    # with a fire build's hits under it, the ground's share is the Ignited share
    hitting = {**life, "Speed": 3.0, "IgniteChancePerHit": 20.0}
    assert conditions.ground_ignite([("the charm's ground", 0.3, 4.0, 15000.0)], hitting)[0] == pytest.approx(0.3, 1e-3)
    # Incinerate's ground: its own damage isn't read, so only the build's Flammability lifts the boss
    _, how = conditions.ground_ignite([("Incinerate's ground", 0.5, 8.0, None)], life)
    assert "UNDERCOUNT" in how
    assert conditions.ground_ignite([], life) == (0.0, "")


def test_radiant_grief_and_birth_of_fury_dont_ignite_the_boss_on_a_build_without_fire(engine):
    """Rolling Slam (no Fire) with Radiant Grief and Birth of Fury: before MECH80 the aura counted the boss Ignited all the
    fight; now neither lifts it to 50% Flammability."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    helmet = "\n".join(["Rarity: Unique", "Radiant Grief", "Lace Hood", "Item Level: 82", "Implicits: 0", GRIEF])
    boots = "\n".join(["Rarity: Unique", "Birth of Fury", "Stone Greaves", "Item Level: 82", "Implicits: 0", FURY,
                       "50% increased Flammability Magnitude"])
    for slot, raw, kind in (("Helmet", helmet, "Helmet"), ("Boots", boots, "Boots")):
        lines = raw.split("\n")
        option = ItemOption(name=lines[1], base=lines[2], type=kind, sub_type=None, raw=raw, unique=True, price=None,
                            confidence="ssf", level=1)
        build.equip(slot, option)
    search.settle()
    share, how = (build.ailment_ground or {}).get("Ignite") or (0.0, "")
    print("GROUND IGNITE", share, how)
    mod = build.call("flammabilityMagnitude")
    print("FLAMMABILITY MOD", mod)
    assert mod["inc"] >= 50 and mod["more"] == pytest.approx(1.0)
    assert "50% Flammability" in how and "Ignited Ground Ignites the boss" in how
    assert "its own" in how.split("Ignited Ground Ignites the boss")[0]  # (the aura's own 200 Fire read)
    assert share < 0.05


# MECH80 (PATCH_CHECK L4-3): Electrocute is an Ailment and an Elemental Ailment (KeywordPopups; patch 0.2.0 / 0.2.0e)
AILING = "30% increased Damage with Hits against Enemies affected by Ailments"
ELEMENTAL = "25% increased Damage with Hits against Enemies affected by Elemental Ailments"
FEW = "20% increased Damage against Frozen, Shocked or Ignited Enemies"


def test_the_ailment_lists_count_electrocute_but_a_named_few_stay_as_written(engine):
    out = engine.call("parseLines", lines=[AILING, ELEMENTAL, FEW])["results"]
    print("PARSE", out)
    lists = [" ".join(m.get("varLists") or []) for r in out for m in r["parsed"][:1]]
    assert "Electrocuted" in lists[0] and "Poisoned" in lists[0]
    assert "Electrocuted" in lists[1] and "Poisoned" not in lists[1]
    assert "Shocked" in lists[2] and "Electrocuted" not in lists[2]


def test_an_electrocuted_boss_turns_on_the_lines_for_enemies_affected_by_ailments(engine):
    """A real build with the two lines on a ring: the boss Electrocuted and nothing else, the damage goes up."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    off = {f"conditionEnemy{a}": False for a in ("Frozen", "Chilled", "Shocked", "Ignited", "Poisoned", "Bleeding")}

    def gain() -> float:
        """TotalDPS with the boss Electrocuted (and no other ailment) over without, on the build as it stands."""
        before = build.call("getConfig")["input"]
        try:
            plain = build.call("setConfig", vars={**off, "conditionEnemyElectrocuted": False},
                               fields=["TotalDPS"])["stats"]["TotalDPS"]
            shocked = build.call("setConfig", vars={**off, "conditionEnemyElectrocuted": True},
                                 fields=["TotalDPS"])["stats"]["TotalDPS"]
        finally:
            build.call("setConfig", vars={k: before.get(k, False) for k in [*off, "conditionEnemyElectrocuted"]})
        return shocked / plain

    bare = gain()  # (other things on the build read Electrocuted too: POE2T3146 doubled without telling them apart)
    raw = "\n".join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0", AILING, ELEMENTAL])
    option = ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw, unique=False,
                        price=None, confidence="ssf", level=1)
    build.equip("Ring 1", option)
    search.settle()
    ringed = gain()
    print("ELECTROCUTED gain without the ring", bare, "with it", ringed)
    assert ringed > bare * 1.05


# MECH80 (PATCH_CHECK G2-4): Uruk's Smelting's Physical Damage taken, 5% a Full Break up to 20%, for the rest of the fight
def test_uruks_smelting_averages_its_permanent_steps_over_the_fight():
    from builder.search import models

    share, how = conditions.uruk_taken(5.0, 0.1, 120.0)  # (Full Breaks at 10, 20, 30, 40 s)
    assert share == pytest.approx(5 * (110 + 100 + 90 + 80) / 120) and "20%" in how
    assert conditions.uruk_taken(5.0, 0.01, 60.0)[0] == 0.0  # (no Full Break before the fight ends)
    assert conditions.uruk_taken(0.0, 1.0, 60.0) == (0.0, "")
    assert conditions.uruk_taken(15.0, 1.0, 100.0)[0] == pytest.approx((15 * 99 + 5 * 98) / 100)  # (the cap's last step)
    mods, notes = models.uruk_mods((12.5, "x"))
    assert mods[0]["line"] == models.URUK_LINE and mods[0]["scale"] == pytest.approx(12.5) and notes
    assert models.uruk_mods((0.0, "")) == ([], [])


def test_uruks_line_is_an_enemy_physical_damage_taken_and_the_support_is_read_on_a_real_build(engine):
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    parsed = engine.call("parseLines", lines=[models.URUK_LINE])["results"][0]
    print("URUK PARSE", parsed)
    assert parsed["supported"] and parsed["parsed"][0]["name"] == "EnemyModifier"
    assert parsed["parsed"][0]["value"] == "PhysicalDamageTaken"
    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    group = build.main_group()
    gems = [{"name": g["name"], "level": g.get("level", 20), "quality": g.get("quality", 0)} for g in group["gems"]]
    build.set_group(index=group["index"], gems=gems + [{"name": conditions.URUK, "level": 1, "quality": 0}])
    search.settle()
    taken = getattr(build, "uruk_taken", None)
    print("URUK", taken)
    assert taken is not None  # (this bare build never Fully Breaks the boss: (0.0, ""), POE2T3157)
    # the support's stat at its level and the fight's length, on this build, at a given Full Break rate
    share, how = search.conditions._uruk_taken(0.1)
    print("URUK at 0.1/s", share, how)
    assert "5% a Full Break up to 20%" in how and "Full DPS" in how and 0.0 <= share <= 20.0


# MECH80 (PATCH_CHECK MD-5): the boss Burning from the main skill's own Fire damage over time, not only from Ignite
def test_the_main_skills_own_fire_damage_over_time_sets_the_boss_burning():
    on, how, up = conditions._enemy_burning([], {"FireDot": 250.0, "_main_skill": "Flame Wall"})
    assert on and up == 1.0 and "250" in how
    assert conditions._enemy_burning([], {"FireDot": 0.0}) == (0.0, "")
    fuel = {"FireDot": 57.5, "_main_skill": conditions.INCINERATE, "_incinerate_fuel": (0.3, 5.0, "x")}
    assert conditions._enemy_burning([], fuel)[2] == pytest.approx(0.3)


def test_flame_wall_sets_the_boss_burning_on_a_real_build(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Flame Wall"))
    skeleton = enumerate_skeletons(Context(payload), ["Flame Wall"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    found = search.conditions.evidence()
    print("BURNING", found.get("conditionEnemyBurning"))
    assert found["conditionEnemyBurning"][0] is True and "Fire damage over time" in found["conditionEnemyBurning"][1]
    # PoB reads the box: an "against Burning Enemies" line moves the score only with it on
    from builder.search.context import ItemOption

    raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                        "30% increased Damage against Burning Enemies"])
    search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                            unique=False, price=None, confidence="ssf", level=1))
    before = search.build.call("getConfig")["input"]
    try:
        off = search.build.call("setConfig", vars={"conditionEnemyBurning": False, "conditionEnemyIgnited": False},
                                fields=["TotalDPS", "TotalDot"])["stats"]
        on = search.build.call("setConfig", vars={"conditionEnemyBurning": True, "conditionEnemyIgnited": False},
                               fields=["TotalDPS", "TotalDot"])["stats"]
    finally:
        search.build.call("setConfig", vars={k: before.get(k, False) for k in ("conditionEnemyBurning",
                                                                                "conditionEnemyIgnited")})
    print("BURNING off / on", off, on)
    assert sum(on.values()) > sum(off.values()) * 1.05


# MECH80 (COV35's routed find, probe POE2T2588): curse_model's templates must be lines PoB applies
def test_the_curse_templates_are_lines_pob_applies(engine):
    from builder.search import models

    texts = [template for _, template in models.CURSED_LINES]
    out = engine.call("parseLines", lines=texts)["results"]
    print("CURSE TEMPLATES", out)
    by = dict(zip(texts, out))
    chaos = by["Nearby Enemies have +1% to Chaos Resistance"]
    assert chaos["supported"] and chaos["parsed"][0]["name"] == "EnemyModifier"
    assert chaos["parsed"][0]["value"] == "ChaosResist"
    assert all(r["supported"] for r in out)


# MECH80 (RESEARCH26 R26-11 (c)): Berek's Grip makes a Whirlwind carry Shocked Ground's Shock onto the boss inside it
BEREKS_GRIP = "Wind Skills which can be boosted by Elemental Ground Surfaces count as being boosted by Shocked Ground"


def test_a_ring_boosted_whirlwind_shocks_the_boss_beside_your_own_ground():
    gains = {"Lightning": (1.0, "ring")}
    share, how = conditions.whirlwind_shock("Whirling Slash", [BEREKS_GRIP], gains)
    assert share == 1.0 and "MEDIUM-LOW" in how
    assert conditions.whirlwind_shock("Twister", [BEREKS_GRIP], gains) == (0.0, "")  # (not a Whirlwind skill)
    assert conditions.whirlwind_shock("Whirling Slash", [], gains) == (0.0, "")  # (no ring: its own ground already counts)
    pass_line = BEREKS_GRIP.replace("Shocked", "Chilled")
    assert conditions.whirlwind_shock("Whirlwind Lance", [pass_line], {"Cold": (1.0, "")}) == (0.0, "")  # (not modelled)
    both = {"_shocked_ground": (0.3, "ground"), "_whirlwind_shock": (0.5, "wind")}
    assert conditions._ground_shocked(both)[0] == pytest.approx(1 - 0.7 * 0.5)
    assert conditions.shock_share(both) == pytest.approx(1 - 0.7 * 0.5)  # (no hits: the two sources)
    assert conditions._ground_shocked({})[0] == 0.0


def test_corrupted_blood_climbs_to_ten_when_each_stack_refreshes_all():
    from builder.search import models

    assert models.cry_stacks(0.5, 100.0) == (pytest.approx((sum(range(1, 11)) * 2 + 10 * 80) / 100), 2.5)
    assert models.cry_stacks(0.1, 100.0) == (0.5, 0.5)  # (a stack every 10 s runs out first: both rules agree)
    assert models.cry_stacks(0.5, None)[0] == 10.0  # (the fight's length unknown: the climb not counted)
    assert models.cry_stacks(0.5, 5.0)[0] == pytest.approx((1 * 2 + 2 * 2 + 3 * 1) / 5)
    assert models.cry_stacks(0.0, 100.0) == (0.0, 0.0)


# MECH80 (RESEARCH26 R26-14): the extra 100% is dealt while ANY live Bleed is Aggravated
def test_aggravated_extra_runs_while_any_live_bleed_is_aggravated():
    import math

    from builder.search import models

    crit = "20% chance to Aggravate Bleeding on targets you Critically Hit with Attacks"
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "BleedDuration": 5.0}
    own = models.aggravated_share(5.0, 2.0 * 0.1 * 0.2, None, 2.0)
    assert models.aggravate_model([crit], stats, {"Attack"})[0][0]["scale"] == pytest.approx(100 * own, rel=1e-3)  # (no chance read)
    mods, notes = models.aggravate_model([crit], {**stats, "BleedChancePerHit": 100}, {"Attack"})
    want = 1 - math.exp(-2.0 * 5.0 * own)  # (2 Bleeds a second, 5 s each)
    assert mods[0]["scale"] == pytest.approx(100 * want, rel=1e-3) and want > own
    assert notes[0]["confidence"] == "MEDIUM-LOW" and f"{own:.0%}" in notes[0]["play"]


# MECH81 (PATCH_CHECK MD-2): the boss Isolated (no ally within 6 m) for Vorana's Siege, except while its adds are up
def test_the_boss_is_isolated_except_while_its_adds_are_up():
    from builder.search import bosses

    vorana = {"_gem_names": {conditions.VORANAS_SIEGE}}
    assert conditions._enemy_isolated([], {"_gem_names": set()}) == (0.0, "")
    alone = next(n for n, b in bosses.PRESETS.items() if not b.kills)
    on, how, up = conditions._enemy_isolated([], {**vorana, "_boss": alone})
    assert on is True and up == 1.0 and "no killable adds" in how
    king = next(n for n, b in bosses.PRESETS.items() if b.kills and sum(k.share for k in b.kills) < 1)
    share = 1 - sum(k.share for k in bosses.PRESETS[king].kills)
    assert conditions._enemy_isolated([], {**vorana, "_boss": king})[2] == pytest.approx(share)
    swarmed = next(n for n, b in bosses.PRESETS.items() if b.kills and sum(k.share for k in b.kills) >= 1)
    assert conditions._enemy_isolated([], {**vorana, "_boss": swarmed})[2] == 0.0


def test_voranas_siege_sets_the_isolated_box_and_pob_reads_it(engine):
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
    group = build.main_group()
    gems = [{"name": g["name"], "level": g.get("level", 20), "quality": g.get("quality", 0)} for g in group["gems"]]
    build.set_group(index=group["index"], gems=gems + [{"name": conditions.VORANAS_SIEGE, "level": 1, "quality": 0}])
    search.settle()
    found = search.conditions.evidence()
    print("ISOLATED", found.get("conditionEnemyIsolated"))
    assert found["conditionEnemyIsolated"][0] is True and "Isolated" in found["conditionEnemyIsolated"][1]
    before = build.call("getConfig")["input"]
    try:
        off = build.call("setConfig", vars={"conditionEnemyIsolated": False}, fields=["TotalDPS"])["stats"]
        on = build.call("setConfig", vars={"conditionEnemyIsolated": True}, fields=["TotalDPS"])["stats"]
    finally:
        build.call("setConfig", vars={"conditionEnemyIsolated": before.get("conditionEnemyIsolated", False)})
    print("ISOLATED off / on", off, on)
    assert on["TotalDPS"] > off["TotalDPS"] * 1.15  # the support's 20% more Hit damage


# MECH81 (PATCH_CHECK G2-2): Stormfire, a Shocked boss takes 20% of the Ignite's damage again as Lightning
def test_stormfire_adds_its_share_of_ignite_as_lightning_while_the_main_skill_shocks():
    from builder.search import models

    more, how = conditions.stormfire_more(20, 0.5, 45, 0)  # the Arbiter of Ash: 45% Fire, 0% Lightning
    assert more == pytest.approx(20 * 0.5 / 0.55) and "Lightning" in how
    assert conditions.stormfire_more(20, 0.0, 45, 0)[0] == 0.0
    assert conditions.stormfire_more(0, 1.0, 45, 0) == (0.0, "")
    mods, notes = models.stormfire_mods("Fireball", (10.0, "x"))
    assert mods[0]["line"] == models.COMPOUND_LINE and mods[0]["scale"] == 10.0 and notes[0]["basis"] == "x"
    assert models.stormfire_mods("Fireball", (0.0, "")) == ([], [])


def test_stormfire_is_read_on_a_real_build(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball"))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    group = build.main_group()
    gems = [{"name": g["name"], "level": g.get("level", 20), "quality": g.get("quality", 0)} for g in group["gems"]]
    build.set_group(index=group["index"], gems=gems + [{"name": conditions.STORMFIRE, "level": 1, "quality": 0}])
    search.settle()
    raw = build.call("activeSkills", levelStats=["Fireball"], supportStats=[conditions.STORMFIRE_STAT])
    main = next(s for s in raw["skills"] if s["name"] == "Fireball" and not s.get("disabled"))
    print("STORMFIRE stat", main.get("supportStats"), getattr(build, "stormfire", None))
    assert (main.get("supportStats") or {}).get(conditions.STORMFIRE_STAT) == 20
    search.conditions.evidence()
    assert getattr(build, "stormfire", None) is not None


# MECH81 (PATCH_CHECK K1-1): Unravelling, Chaos damage also builds one element's ailment, a third of the fight each
def test_unravelling_sets_each_element_a_third_of_the_fight_only_when_granted():
    on, how, up = conditions._unravelling("Cold")(["Grants Unravelling"], {})
    assert on is True and up == pytest.approx(1 / 3) and "Freeze" in how
    assert conditions._unravelling("Lightning")(["Grants Onslaught"], {}) == (0.0, "")
    assert {c.var for c in conditions.CONDITIONS} >= {f"bridgeCondition{e}Unravel" for e in ("Cold", "Fire", "Lightning")}


def test_unravelling_lets_chaos_damage_ignite_in_pob(engine):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                        "Grants Unravelling", "Adds 200 to 300 Chaos damage to Attacks"])
    search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                            unique=False, price=None, confidence="ssf", level=1))
    search.settle()
    found = search.conditions.evidence()
    print("UNRAVEL", {k: v for k, v in found.items() if "Unravel" in k})
    assert all(f"bridgeCondition{e}Unravel" in found for e in ("Cold", "Fire", "Lightning"))
    names = ["bridgeConditionColdUnravel", "bridgeConditionFireUnravel", "bridgeConditionLightningUnravel"]
    before = search.build.call("getConfig")["input"]
    try:
        off = search.build.call("setConfig", vars={n: False for n in names}, fields=["IgniteChancePerHit"])["stats"]
        fire = search.build.call("setConfig", vars={"bridgeConditionFireUnravel": True},
                                 fields=["IgniteChancePerHit"])["stats"]
    finally:
        search.build.call("setConfig", vars={n: before.get(n, False) for n in names})
    print("UNRAVEL ignite chance off / Fire", off, fire)
    assert fire.get("IgniteChancePerHit", 0) > off.get("IgniteChancePerHit", 0)  # (PoB leaves out a 0)


def test_critical_weakness_from_a_spell_crit_line_raises_crit_on_a_real_build(engine):
    """MECH81 (K3-1): a Spark build with the unique focus's "Critical Hits with Spells apply 3 Stacks of Critical
    Weakness" line (on a ring here: PoB doesn't parse it, the model reads the text) holds stacks from its own spell crits,
    and PoB's crit chance rises by 0.5% base a stack."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    before = models.number(search.build.call("stats", fields=["CritChance"]).get("CritChance"))
    raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                        "Critical Hits with Spells apply 3 Stacks of Critical Weakness"])
    search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                            unique=False, price=None, confidence="ssf", level=1))
    search.assumptions.apply()
    note = next(n for n in search.assumptions.active if n["label"].startswith("Critical Weakness on the boss"))
    after = models.number(search.build.call("stats", fields=["CritChance"]).get("CritChance"))
    print("CRITICAL WEAKNESS", note, before, after)
    assert "spell crit" in note["basis"] and after > before


def test_stacked_flammability_raises_a_low_chance_ignite_on_a_real_build(engine):
    """MECH81: Rolling Slam with a little added Fire: each hit's own Flammability is tiny, so PoB counts its Ignite at a
    small share (IgniteStackPotential); stacked Flammability raises it, as more Ignite Magnitude on the main skill."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                        "Adds 20 to 30 Fire damage to Attacks"])
    search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                            unique=False, price=None, confidence="ssf", level=1))
    search.settle()
    fields = ["IgniteChancePerHit", "IgniteStackPotential", "IgniteDPS"]
    pob = search.build.call("stats", fields=fields)
    scale = getattr(search.build, "flammability_scale", None)
    print("FLAMMABILITY", pob, scale)
    assert scale is not None and scale[0] > 1.0 and "Flammability stacks" in scale[1]
    # (settle already applied the assumptions: PoB's Ignite with the scale; without it, the scale switched off)
    note = next(n for n in search.assumptions.active if "stacked Flammability" in n["label"])
    search.build.flammability_scale = (1.0, "")
    search.assumptions.apply()
    without = search.build.call("stats", fields=fields)
    print("FLAMMABILITY with / without", note, pob, without)
    assert models.number(pob.get("IgniteDPS")) == pytest.approx(models.number(without.get("IgniteDPS")) * scale[0],
                                                                 rel=0.02)
