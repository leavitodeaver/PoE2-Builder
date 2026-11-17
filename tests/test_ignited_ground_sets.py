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
    # (MECH83) its Ignite's damage: one entry for the charm's ground, as though dealing 500% of the build's Life
    life = build.call("stats", fields=["Life"])["Life"]
    found = build.ground_life_ignite
    print("BEIRA IGNITE", found)
    if share <= 0:  # (no charm uses on this bare build: no ground, so no Ignite either)
        assert found == []
    else:
        assert len(found) == 1 and found[0][0] == "the charm's ground"
        assert found[0][2] == pytest.approx(0.2 * 5.0 * life, rel=1e-3) and 0 <= found[0][1] <= share


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


# MECH82 (PATCH_CHECK G4-4 / G2-3): Oil Grenade's / Oil Barrage's lit Oil Ground Ignites the boss
def test_oil_ground_is_lit_by_detonators_ignites_or_your_ignited_ground():
    assert conditions.oil_lit_chance(0.0) == 0.0
    assert conditions.oil_lit_chance(1 / 6) == pytest.approx(1 - math.exp(-1))
    assert conditions.oil_lit_chance(0.0, 0.5) == pytest.approx(0.5)
    pieces: list = []
    found, how = conditions.oil_ground({"Oil Grenade": (0.25, 1000.0, "0.25 uses/s")}, [("a Detonator", 1.0)], 0.0,
                                       pieces)
    (name, lit, damage, oilings), = found
    assert name == "Oil Grenade" and damage == pytest.approx(2500.0)  # the set's Fire + the added 150% (MECH83, R28-12)
    assert lit == pytest.approx(1 - math.exp(-6.0))  # 6 s of Oil a 4 s use: up all fight, lit unless nothing comes
    assert oilings == pytest.approx(0.25 * (1 - math.exp(-6.0)))
    assert pieces == [("Oil Grenade's lit Oil Ground", lit, 6.0, 2500.0)] and "lit" in how
    # nothing lights it: no Ignite from it
    assert conditions.oil_ground({"Oil Barrage": (1.0, 1000.0, "")}, [], 0.0)[0] == []
    # half the uses: 3 s of Oil a 2 s gap... up 6 s x 0.125 = 75%
    found, _ = conditions.oil_ground({"Oil Barrage": (0.125, 1000.0, "")}, [], 1.0)
    assert found == [("Oil Barrage", pytest.approx(0.75), pytest.approx(2000.0), pytest.approx(0.125))]


def test_each_lit_oiling_ignites_the_oil_covered_boss_outright_then_the_ground_needs_50_percent():
    # (MECH83, R28-12) one lit oiling every 8 s, 4 s Ignites (evenly spaced uses, _covered): outright half the fight,
    # the rest of the lit time only at the 50% reach
    share, outright = conditions.oil_ignite_share(0.9, 0.125, 0.0, 4.0)
    assert outright == pytest.approx(0.5) and share == pytest.approx(0.5)
    share, _ = conditions.oil_ignite_share(0.9, 0.125, 0.5, 4.0)
    assert share == pytest.approx(0.5 + 0.4 * 0.5)
    assert conditions.oil_ignite_share(0.3, 0.25, 0.0, 4.0) == (pytest.approx(0.3), pytest.approx(0.3))  # (capped)
    assert conditions.oil_ignite_share(0.0, 0.25, 1.0, 4.0) == (0.0, 0.0)


def test_the_oil_ignite_adds_only_what_it_beats_the_builds_own_ignite_by():
    from builder.search import models

    added, notes = models.oil_ignite_model([("Oil Grenade", 0.5, 300.0, "how")], 20.0, 100.0)
    assert added == pytest.approx(0.5 * (300.0 * 0.8 - 100.0)) and "+70.0 DPS" in notes[0]["value"]
    assert "without it +0 DPS" in notes[0]["play"]
    assert models.oil_ignite_model([("Oil Grenade", 0.5, 300.0, "how")], 20.0, 500.0)[0] == 0.0
    assert models.oil_ignite_model(None, 20.0, 0.0) == (0.0, [])
    added, notes = models.oil_ignite_model([("Oil Grenade", 0.0, 300.0, "how", 0.6)], 20.0, 100.0)  # (never at 50%)
    assert added == 0.0 and "+84.0 DPS (60% of the fight)" in notes[0]["basis"]  # (the reading without the 50% rule)


def test_oil_grenades_oil_ground_set_is_rated_and_read_on_a_real_build(engine):
    """MECH82: the bridge's useRates rates the Oil Ground set (statSets) of a side Oil Grenade, whose Fire hit (its 100%
    Physical converted) the lit ground's Ignite reads; the evidence passes the lit share and the Ignite to the model."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Explosive Grenade"))
    skeleton = enumerate_skeletons(Context(payload), ["Explosive Grenade"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = build.set_group(gems=[{"name": "Oil Grenade", "level": 20, "quality": 0}])
    search.settle()
    index = group["index"]
    grenade = build.call("useRates", indices=[index])["rates"][0]
    ground = build.call("useRates", indices=[index], statSets=[conditions.OIL_GROUND_SET])["rates"][0]
    again = build.call("useRates", indices=[index])["rates"][0]
    keys = ("name", "MainHand.FireHitAverage", "MainHand.PhysicalHitAverage")
    print("OIL sets", {k: grenade.get(k) for k in keys}, {k: ground.get(k) for k in keys}, sorted(ground))
    # (the ground set converts all its Physical to Fire; the grenade's own set deals no Fire)
    assert models.number(ground.get("MainHand.FireHitAverage")) > 0
    assert not models.number(ground.get("MainHand.PhysicalHitAverage"))
    assert models.number(grenade.get("MainHand.PhysicalHitAverage")) > 0
    assert again.get("MainHand.PhysicalHitAverage") == grenade.get("MainHand.PhysicalHitAverage")  # (put back)
    search.conditions.evidence()
    oil = getattr(build, "oil_ignite", None)
    print("OIL ignite", oil)
    assert oil and oil[0][0] == "Oil Grenade" and oil[0][2] > 0 and "Explosive Grenade's hits (a Detonator)" in oil[0][3]
    search.assumptions.apply()
    note = next(n for n in search.assumptions.active if "lit Oil Ground" in n["label"])
    print("OIL note", note)
    assert "only the strongest Ignite counts" in note["basis"]


# MECH82 (RESEARCH26 R26-34): Beetlebite Aggravates the live Bleeds each time the boss re-enters your Presence
def test_the_boss_reenters_your_presence_once_every_two_rolls_when_a_roll_leaves_it():
    from builder.search import models

    stats = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}  # (PoB's units: 10 a metre)
    rate, how = models.presence_reentries([], stats, 1.0)
    assert rate == pytest.approx(1 / (2 * models.DODGE_EVERY)) and "once every 4 s" in how
    assert models.presence_reentries([], stats, 5.0)[0] == 0.0  # (fighting outside the Presence)
    assert models.presence_reentries([], {**stats, "PresenceRadius": 80}, 1.0)[0] == 0.0  # (a 4.6 m roll stays inside)


def test_beetlebite_aggravates_bleeds_at_the_reentry_rate_for_any_main_skill():
    from builder.search import models

    line = "Aggravate Bleeding on Enemies when they Enter your Presence"
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "BleedDuration": 5.0}
    assert models.aggravate_model([line], stats, {"Attack"})[0] == []  # (no re-entry rate given: not read)
    entries = (0.25, "rolling out and back")
    own = models.aggravated_share(5.0, 0.25, None, 2.0)
    for types in ({"Attack"}, {"Spell"}):
        mods, notes = models.aggravate_model([line], stats, types, entries=entries)
        assert mods[0]["scale"] == pytest.approx(100 * own, rel=1e-3) and "Beetlebite" in notes[0]["basis"]
    mods, notes = models.aggravate_model([line], stats, {"Spell"}, entries=(0.0, "outside"))
    assert not any(m.get("scale") for m in mods) and "Beetlebite: outside" in notes[0]["basis"]


# MECH82 (the manager 09:09, COV37's find): the Chaos Resistance template adds the cursed share; the two templates PoB
# applies in full take off the uncursed share
def test_the_cursed_chaos_resistance_line_counts_at_the_cursed_share_with_its_own_sign():
    from builder.search import models

    lines = [{"text": "Enemies you Curse have -11% to Chaos Resistance"},
             {"text": "Enemies you Curse take 25% increased Damage"}]
    for share in (0.0, 0.5, 1.0):
        mods, notes = models.curse_model([models.CurseUse("Despair", share, "test")], [], None, lines)
        by_line = {m["line"]: m["scale"] for m in mods}
        chaos = by_line.get("Nearby Enemies have +1% to Chaos Resistance", 0.0)
        assert chaos == pytest.approx(-11.0 * share)  # (lower Chaos Resistance on the boss, never higher)
        assert by_line.get("Enemies you Curse take 1% increased Damage", 0.0) == pytest.approx(-25.0 * (1 - share))
        assert "-11% to Chaos Resistance" in notes[-1]["basis"]


def test_the_curse_templates_scale_with_the_cursed_share_on_a_real_build(engine):
    """MECH82: on a real Rolling Slam with added Chaos and Fire, each template's net effect in PoB is linear in the share:
    nothing at 0, all of it at 1 (the Chaos line through our template only; the other two through the build's own line,
    which PoB applies with its Cursed box on, less our uncursed part)."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    def build_with(*texts):
        payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
        skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                            "Adds 40 to 60 Chaos damage to Attacks", "Adds 40 to 60 Fire damage to Attacks", *texts])
        search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                                unique=False, price=None, confidence="ssf", level=1))
        search.settle()
        search.build.call("setConfig", vars={"conditionEnemyCursed": True})
        return search.build, [{"text": t} for t in texts]

    def scored(build, lines, share, field):
        mods = models.curse_model([models.CurseUse("Despair", share, "test")], [], None, lines)[0]
        return models.number(build.call("setAssumptions", mods=mods, fields=[field])["stats"].get(field))

    for texts, field in ((["Enemies you Curse have -40% to Chaos Resistance"], "TotalDPS"),
                         (["Enemies you Curse take 30% increased Damage"], "TotalDPS"),
                         (["50% increased duration of Ailments you inflict against Cursed Enemies"], "IgniteDuration")):
        build, lines = build_with(*texts)
        none = models.number(build.call("setAssumptions", mods=[], fields=[field])["stats"].get(field))
        if "take" in texts[0] or "duration" in texts[0]:  # (the build's own line, applied by PoB, minus it all)
            build.call("setConfig", vars={"conditionEnemyCursed": False})
            none = models.number(build.call("setAssumptions", mods=[], fields=[field])["stats"].get(field))
            build.call("setConfig", vars={"conditionEnemyCursed": True})
        at = {s: scored(build, lines, s, field) for s in (0.0, 0.5, 1.0)}
        print("CURSE", texts[0], field, none, at)
        assert at[0.0] == pytest.approx(none, rel=1e-3)
        assert at[1.0] > at[0.5] > at[0.0]
        assert at[1.0] - at[0.5] == pytest.approx(at[0.5] - at[0.0], rel=0.05)


# MECH82 (the manager 09:09, COV37's find): the minions' Withered isn't sized by your own Withered Magnitude
def test_your_withered_magnitude_is_taken_off_when_only_your_minions_wither():
    minions = "Minions have 10% chance to inflict Withered on Hit"
    magnitude = ["20% increased Withered Magnitude", "5% reduced effect of Withered", "50% reduced effect of Withered on you"]
    mods, how = conditions.minion_wither_magnitude([minions, *magnitude], "Raise Zombie", {"Raise Zombie"})
    assert [(m["name"], m["value"]) for m in mods] == [("WitherEffect", -15.0)] and "only your minions" in how
    # a Withered source of your own: your Magnitude stays (labelled)
    mods, how = conditions.minion_wither_magnitude([minions, *magnitude], "Chaos Bolt", {"Chaos Bolt"})
    assert mods == [] and "stays" in how
    assert conditions.minion_wither_magnitude(magnitude, "Raise Zombie", set()) == ([], "")  # (no minion line)
    assert conditions.minion_wither_magnitude([minions], "Raise Zombie", set()) == ([], "")  # (no Magnitude line)


def test_pob_puts_your_withered_magnitude_on_the_minions_withered_and_the_mod_takes_it_off(engine):
    """MECH82 (COV37): on a real Rolling Slam with added Chaos and the boss at 10 Withered stacks, the minions' Withered line
    with "20% increased Withered Magnitude" scores higher than the line alone in PoB (your Magnitude on their Withered);
    minion_wither_magnitude's mod brings it back to the line alone."""
    from builder.request.schema import BuildRequest
    from builder.search import models
    from builder.search.context import Context, ItemOption, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    minions = "Minions have 10% chance to inflict Withered on Hit"

    def dps(texts, mods):
        payload = make_payload(BuildRequest(main_skill="Rolling Slam"))
        skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
        search = Search(engine, None, payload, skeleton.as_dict(), 0)
        search.setup(quests=False)
        raw = chr(10).join(["Rarity: Rare", "Test Loop", "Iron Ring", "Item Level: 82", "Implicits: 0",
                            "Adds 40 to 60 Chaos damage to Attacks", *texts])
        search.build.equip("Ring 1", ItemOption(name="Test Loop", base="Iron Ring", type="Ring", sub_type=None, raw=raw,
                                                unique=False, price=None, confidence="ssf", level=1))
        search.settle()
        search.build.call("setConfig", vars={"multiplierWitheredStackCount": 10})
        return models.number(search.build.call("setAssumptions", mods=mods, fields=["TotalDPS"])["stats"].get("TotalDPS"))

    alone = dps([minions], [])
    texts = [minions, "20% increased Withered Magnitude"]
    mods, how = conditions.minion_wither_magnitude(texts, "Rolling Slam", {"Rolling Slam"})
    boosted, fixed = dps(texts, []), dps(texts, mods)
    print("WITHER", alone, boosted, fixed, mods, how)
    assert boosted > alone * 1.001 and fixed == pytest.approx(alone, rel=1e-4)


# MECH82 (the manager 10:02, GEM15's routing): Arctic Howl Freezes a unique boss once it is Primed at 70% buildup
def test_arctic_howl_freezes_the_boss_at_the_first_howl_after_70_percent():
    from builder.search import models

    assert models.primed_refill(10.0, 0.0) == 10.0  # (no Howl: the full bar)
    # Howls every 10 s, the bar's last 30% takes 3 s: a Howl lands in it 30% of the time, 1.5 s early on average
    assert models.primed_refill(10.0, 0.1) == pytest.approx(10.0 - 0.1 * 3.0 * 3.0 / 2)  # (9.55 s)
    assert models.primed_refill(10.0, 1.0) == pytest.approx(7.0 + 0.5)  # (a Howl every 1 s: at 70% + half a second)
    assert models.primed_refill(10.0, 100.0) == pytest.approx(7.0, abs=0.02)  # (Howling all the time: at 70%)


# MECH82 (the manager 10:02, GEM16's routing): "Overgrown Plant Skills Break 50% increased Armour" at the Overgrown share
def test_overgrown_plants_break_more_armour_at_their_overgrown_share():
    line = "Overgrown Plant Skills Break 50% increased Armour"
    scale, how = conditions.break_amount_scale([line, "Break 20% increased Armour"], {"_overgrown": (0.4, "storm")})
    assert scale == pytest.approx(1 + (20 + 50 * 0.4) / 100) and "Overgrown Plants" in how
    scale, how = conditions.break_amount_scale([line], {})  # (no share read: 0, labelled)
    assert scale == 1.0 and "x 0%" in how


# MECH83 (MECH82's Handover step 3): Wither Away's Withered on entering your Presence and Her Declaration's Intimidate,
# both at models.presence_reentries' rate (the build's rolls out of and back into its Presence)
def test_wither_away_withers_an_unwithered_boss_at_the_presence_reentry_rate():
    line = "Unwithered enemies are Withered for 8 seconds when they enter your Presence"
    near = {"PresenceRadius": 40, "MovementSpeedMod": 1.0, "Speed": 2.0, "HitChance": 100, "_distance_choice": (1.0, "t")}
    got = conditions._withered_stacks([line], near)
    share = conditions.wither_entry_share(0.25, 8.0)  # one entry every 4 s, 8 s a stack
    assert share == pytest.approx(2 / 3)
    assert got[0] == 1.0 and got[2] == pytest.approx(share) and got[3] is False and "Unwithered" in got[1]
    far = {**near, "_distance_choice": (6.0, "t")}  # (outside the 4 m Presence: never enters)
    assert conditions._withered_stacks([line], far)[0] == 0.0
    # with 4 stacks from a chance line (0.25 x 4 hits/s x 4 s), the entry adds its share only while those leave none
    quill = "Spells have a 25% chance to inflict Withered for 4 seconds on Hit"
    spell = {**near, "Speed": 4.0, "_main_types": {"Spell"}}
    alone = conditions._withered_stacks([quill], spell)[0]
    both = conditions._withered_stacks([quill, line], spell)[0]
    assert alone == 4.0 and both == float(int(4.0 + share * math.exp(-4.0)))


def test_her_declaration_intimidates_the_boss_on_each_presence_entry_beside_a_persistent_buff():
    gems = {"Her Declaration": {"support": True, "skillTypes": []},
            "Herald of Ice": {"support": False, "skillTypes": ["Buff", "Persistent", "Herald"]},
            "Spark": {"support": False, "skillTypes": ["Spell", "Projectile"]}}
    stats = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}
    on_buff = [{"gems": [{"name": "Herald of Ice"}, {"name": "Her Declaration"}]}]
    up, how = conditions.her_declaration(on_buff, gems, [], stats, 1.0)
    assert up == pytest.approx(1 - math.exp(-0.25 * 4.0)) and "Herald of Ice" in how
    longer = conditions.her_declaration(on_buff, gems, ["50% increased Intimidate Duration"], stats, 1.0)[0]
    assert longer == pytest.approx(1 - math.exp(-0.25 * 6.0))
    assert conditions.her_declaration(on_buff, gems, [], stats, 6.0)[0] == 0.0  # (the boss stays outside)
    on_spark = [{"gems": [{"name": "Spark"}, {"name": "Her Declaration"}]}]
    assert conditions.her_declaration(on_spark, gems, [], stats, 1.0) is None  # (not a Persistent Buff)
    off = [{"enabled": False, "gems": on_buff[0]["gems"]}]
    assert conditions.her_declaration(off, gems, [], stats, 1.0) is None


def test_wither_away_raises_chaos_damage_on_a_real_build_only_with_the_boss_in_your_presence(engine):
    """(MECH83) PoB reads Wither Away's line as Condition:CanWither alone; the resolver's entry stack (WITHER_ENTRY) is
    what moves the Chaos damage, weighed by its share of the fight, and nothing when the boss stays outside."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Sunder"))
    skeleton = enumerate_skeletons(Context(payload), ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    line = "Unwithered enemies are Withered for 8 seconds when they enter your Presence"

    def dps(metres: float, extra: list[str]) -> float:
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet",
                                                          "Item Level: 82", "Implicits: 0",
                                                          "Adds 100 to 200 Chaos Damage to Attacks", *extra]), fields=[])
        search.conditions.distance = (metres, "test")
        search.settle()
        return main_dps(build.call("stats", fields=["TotalDPS", "CombinedDPS"]))

    assert dps(1.0, [line]) > 1.005 * dps(1.0, [])
    assert dps(8.0, [line]) == pytest.approx(dps(8.0, []), rel=0.001)


def test_the_ground_ignite_counts_where_it_is_the_strongest_ignite_up_and_only_over_your_own():
    from builder.search import models

    found = [("the trail", 0.5, 100.0, "trail"), ("the charm's ground", 0.4, 1000.0, "charm")]
    added, notes = models.ground_life_ignite_model(found, 50.0, 20.0)
    # the charm's 500 DPS after resistance, 40% of the fight; the trail's 50, where the charm's is off: 50% x 60%
    assert added == pytest.approx(0.4 * (500 - 20) + 0.5 * 0.6 * (50 - 20))
    assert notes[0]["label"].startswith("the charm's ground") and len(notes) == 2
    assert models.ground_life_ignite_model(found, 50.0, 600.0)[0] == 0.0  # (the build's own Ignite is stronger)
    assert models.ground_life_ignite_model([], 0.0, 0.0) == (0.0, [])


# MECH83 (FIX_QUEUE 75, PATCH_CHECK H4-2): the Disciple of Varashta's djinn Commands
def test_the_djinn_commands_come_from_the_djinns_and_the_granted_notables():
    from builder.search import models

    kelari = {models.DJINNS["Kelari"]}
    names = [c.name for c in models.djinn_commands_available(kelari, ["Grants Skill: Kelari's Deception"])]
    assert names == ["Kelari's Brutality", "Kelari's Deception"]
    ruzhan = [c.name for c in models.djinn_commands_available({models.DJINNS["Ruzhan"]}, [])]
    assert ruzhan == ["Ruzhan's Brand", "Ruzhan's Zealotry"]
    assert models.djinn_commands_available(set(), ["Grants Skill: Kelari's Deception"]) == []


def test_kelaris_deception_climbs_to_its_300_percent_cap_only_when_the_chain_holds():
    from builder.search import models

    assert models.deception_factor(4.0)[0] == 4.0  # (a use within the clone's 4 s: the chain never breaks, the cap)
    assert models.deception_factor(5.0)[0] == pytest.approx((1 + 1.5 + 2.0) / 3)  # (3 stored uses spent together)
    assert models.deception_factor(5.0, uses=9)[0] == pytest.approx((1 + 1.5 + 2 + 2.5 + 3 + 3.5 + 4 + 4 + 4) / 9)


def test_each_djinn_command_adds_its_hit_a_cooldown_less_the_presses_time():
    from builder.search import models

    by = {c.name: c for c in models.DJINN_COMMANDS}
    rows = [(by["Kelari's Brutality"], {2: {"AverageDamage": 1000.0, "Cooldown": 5.0}}),
            (by["Ruzhan's Reckoning"], {1: {"AverageDamage": 3000.0, "Cooldown": 12.0}, 2: {"AverageDamage": 3000.0}}),
            (by["Ruzhan's Zealotry"], {1: {"AverageDamage": 400.0, "Cooldown": 4.0}}),
            (by["Ruzhan's Trap"], {3: {"AverageDamage": 9999.0, "Cooldown": 8.0}}),
            (by["Navira's Fracturing"], {1: {"AverageDamage": 5000.0, "Cooldown": 5.0}})]
    dps, mods, notes, presses = models.djinn_commands_model(rows, 500.0, chilled=0.0)
    brutality = 1000 / 5 - 500 * 0.4 / 5
    reckoning = 6000 / 12 - 500 * 0.4 / 12
    zealotry = 400 / 4  # (no press: no cost)
    assert dps == pytest.approx(brutality + reckoning + zealotry)
    assert set(presses) == {"Kelari's Brutality", "Ruzhan's Reckoning"}
    assert mods and mods[0]["value"] == pytest.approx(100 * dps, abs=1)
    assert any("Ruzhan's Trap" in n["label"] and n["value"] == 0 for n in notes)
    with_ground = models.djinn_commands_model(rows, 500.0, chilled=0.5)[0]
    assert with_ground == pytest.approx(dps + 5000 * 0.5 / 5 - 500 * 0.4 / 5)
    # a net loss isn't pressed
    weak = models.djinn_commands_model([(by["Kelari's Brutality"], {2: {"AverageDamage": 10.0, "Cooldown": 5.0}})], 500.0)
    assert weak[0] == 0.0 and weak[3] == {}


def test_explosive_spears_frenzy_charged_throws_make_ignited_ground_at_their_rate():
    pieces = []
    share, how = conditions.ignited_ground({"Explosive Spear": (0.1, "0.10 of its 1.00 throws/s")}, [],
                                           {"_distance_choice": (1.0, "t")}, None, pieces)
    assert share == pytest.approx(conditions._covered(0.1, 1.0, 8.0)) and "Frenzy-Charged throws" in how
    assert pieces == [("Explosive Spear's ground", share, 8.0, None)]
    assert "Explosive Spear" not in conditions.IGNITED_NOT_COUNTED


def test_kelaris_commands_add_damage_on_a_real_disciple_build(engine):
    """(MECH83) Barya of Kelari and Kelari's Deception allocated: the bridge scores each Command with its minion skill and
    damage stat set, and Assumptions adds them (PoB alone scores 0: the djinn is never the main skill)."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions as amod
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Disciple of Varashta"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("allocate", add=[13289, 23265])  # (Barya of Kelari, Kelari's Deception)
    found = {s["name"] for g in build.groups() for s in g["skills"]}
    assert "Kelari, the Tainted Sands" in found
    group = next(g for g in build.groups() if any(s["name"] == "Kelari, the Tainted Sands" for s in g["skills"]))
    n = next(i for i, s in enumerate(group["skills"], 1) if s["name"] == "Kelari, the Tainted Sands")
    first = build.call("minionSkillHit", index=group["index"], active=n, skill="KnifeThrowSandDjinn", statSet=1)
    second = build.call("minionSkillHit", index=group["index"], active=n, skill="KnifeThrowSandDjinn", statSet=2)
    print("PROBE knives", first, second)
    assert second.get("statSet") == 2 and first.get("statSet") == 1
    dps, mods, notes = amod.djinn_commands(build, None)
    print("PROBE djinn", dps, notes)
    labels = " ".join(n["label"] for n in notes)
    assert "Kelari's Brutality" in labels and "Kelari's Deception" in labels and dps > 0


def test_anaemia_raises_the_corrupted_blood_cap_while_the_main_skills_hits_keep_it_up():
    from builder.search import models

    line = "Inflict Anaemia on Hit\nAnaemia allows +3 Corrupted Blood debuffs to be inflicted on enemies"
    assert models.anaemia_cap([line], {"Speed": 2.0, "HitChance": 100})[0] == 3.0  # (hits every 0.5 s: always up)
    assert models.anaemia_cap([line], {"Speed": 0.125, "HitChance": 100})[0] == pytest.approx(1.5)  # (a hit per 8 s)
    assert models.anaemia_cap([], {"Speed": 2.0}) == (0.0, "")
    assert models.cry_stacks(1.0, None, 5.0, 13.0)[0] == 13.0  # (refresh-all climbs to the raised cap)


# MECH83 (R28-11 CD-12 / CD-13, the manager 16:11): two Shocks / two Chills at once from the build's own rate
def test_strike_twice_writes_the_shock_stacks_from_the_builds_shock_rate():
    line = ["Targets can be affected by two of your Shocks at the same time"]
    fn = conditions.ailment_stacks("Shock")
    assert fn([], {"Speed": 4.0, "ShockChancePerHit": 50}) == (0.0, "")  # (no line: the box untouched)
    m = 1.0 * 0.25 * 8.0  # 1 hit/s x 25% x 8 s = 2 live on average
    got = fn(line, {"Speed": 1.0, "HitChance": 100, "ShockChancePerHit": 25, "ShockDuration": 8.0})
    share = (1 - math.exp(-m) - m * math.exp(-m)) / (1 - math.exp(-m))
    assert got[0] == 2.0 and got[2] == pytest.approx(share) and got[3] == 1
    assert fn(line, {"Speed": 10.0, "HitChance": 100, "ShockChancePerHit": 100})[0] == 2.0  # (always two)
    assert fn(line, {"Speed": 4.0, "ShockChancePerHit": 100, "_consumes": {"Shock": 1}})[0] == 1.0
    assert conditions.ailment_stack_cap(["You can apply up to 3 Shocks to each Enemy"], "Shock") == 3
    assert conditions.ailment_stack_cap(line, "Chill") == 1
    assert conditions.stacked_mean(50.0, 3) == pytest.approx(3.0)


def test_heavy_snows_writes_the_chill_stacks_from_the_builds_chill_rate():
    line = ["Targets can be affected by two of your Chills at the same time"]
    got = conditions.ailment_stacks("Chill")(line, {"Speed": 0.25, "HitChance": 100, "ChillChanceOnHit": 100,
                                                    "ChillDuration": 8.0})
    m = 0.25 * 8.0
    assert got[0] == 2.0 and got[2] == pytest.approx((1 - math.exp(-m) - m * math.exp(-m)) / (1 - math.exp(-m)))


def test_pob_counts_a_second_shock_only_with_strike_twice_and_the_box_at_two(engine):
    """(MECH83, CD-12) PoB's ShockStacks box: with the line, 2 adds a second Shock's damage taken; without it, nothing."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def dps(stacks: int, extra: list[str]) -> float:
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet",
                                                          "Item Level: 82", "Implicits: 0", *extra]), fields=[])
        build.call("setConfig", vars={"conditionEnemyShocked": True, "ShockStacks": stacks}, fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    line = ["Targets can be affected by two of your Shocks at the same time"]
    print("PROBE stacks", dps(1, line), dps(2, line), dps(1, []), dps(2, []))
    assert dps(2, line) > 1.05 * dps(1, line)
    assert dps(2, []) == pytest.approx(dps(1, []), rel=1e-6)
