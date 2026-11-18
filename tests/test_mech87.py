"""MECH87 (MECH-A): Non-Critical Hit Recently and gained a Power Charge Recently from the build."""
import math

import pytest

from builder.search import conditions

STATS = {"CritChance": 30.0, "Speed": 2.0, "HitChance": 90.0}


def test_non_crit_recently_follows_the_main_skills_hits_and_crit_chance():
    line = ["20% increased Critical Damage Bonus if you've dealt a Non-Critical Hit Recently"]
    got = conditions._non_crit_recently(line, STATS)
    assert got[2] == pytest.approx(1 - 0.3 ** (4 * 1.8), rel=1e-6) and "1.80 hits a second" in got[1]
    slow = conditions._non_crit_recently(line, {**STATS, "Speed": 0.1, "CritChance": 90.0, "HitChance": 100.0})
    assert slow[2] == pytest.approx(1 - 0.9 ** 0.4, rel=1e-6)
    assert conditions._non_crit_recently(line, {**STATS, "CritChance": 100.0})[0] == 0.0  # (always crits: off)
    assert conditions._non_crit_recently(line, {**STATS, "Speed": 0.0})[0] == 0.0  # (no hits: off)
    assert conditions._non_crit_recently([], STATS) == (0.0, "")
    names = {c.var for c in conditions.CONDITIONS}
    assert "conditionNonCritRecently" in names and "conditionGainedPowerChargeRecently" in names


def test_gained_a_power_charge_recently_comes_from_the_builds_gains_only():
    cond = "10% increased Damage if you've gained a Power Charge Recently"
    source = "25% chance to gain a Power Charge on Critical Hit"
    got = conditions._gained_power_recently([cond, source], STATS)
    gains = 0.25 * 1.8 * 0.3
    assert got[2] == pytest.approx(1 - math.exp(-4 * gains), rel=1e-3) and "Critical Hit" in got[1]
    # The condition line itself is no source (charge model reads "gained a Power Charge" in it at a guessed rate):
    assert conditions._gained_power_recently([cond], STATS)[0] == 0.0
    assert conditions._gained_power_recently(
        [cond, "+10% to Critical Hit Chance if you haven't gained a Power Charge Recently"], STATS)[0] == 0.0
    assert conditions._gained_power_recently([source], STATS) == (0.0, "")


# The parry plan weighs a consuming main skill's own payoff (GEM19's routing) ------------------------------------------

def test_the_parry_plan_weighs_a_consumers_own_payoff():
    from builder.search import models

    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    stats = {"enemySkillTime": 0.7, "Speed": 2.0, "HitChance": 100}
    assert models.parry_plan([parry], stats, True, True).box == 0  # (the 50% more alone doesn't pay for the time)
    plan = models.parry_plan([parry], stats, True, True, payoff=2.0)
    assert plan.box > 0 and plan.lost > 0 and "own extra part" in plan.how
    assert models.parry_plan([parry], stats, True, False, payoff=2.0).how == models.parry_plan([parry], stats, True).how
    sets = [{"label": "Stab", "TotalDPS": 100.0}, {"label": "Explosion", "TotalDPS": 180.0}]
    assert models.parried_payoff("Fangs of Frost", sets) == pytest.approx(1.8)
    assert models.parried_payoff("Ice Strike", sets) == 0.0
    assert models.parried_payoff("Fangs of Frost", sets[1:]) == 0.0  # (no plain set scored)


# Other groups' buildup joins the main skill's on the one bar (GEM18 / GEM19's routing) ---------------------------------

def test_other_groups_buildup_fills_the_bosss_one_bar():
    from builder.search import models

    rows = {2: {"index": 2, "name": "Spark", "speed": 1.5, "triggered": True, "HitChance": 100,
                "ElectrocuteBuildupAvg": 4.0, "FreezeBuildupAvg": 0.0},
            1: {"index": 1, "name": "Ice Strike", "speed": 2.0, "triggered": True, "FreezeBuildupAvg": 9.0}}
    groups = [{"index": 1, "gems": [{"name": "Ice Strike"}]}, {"index": 2, "gems": [{"name": "Spark"}]}]
    others = conditions.buildup_others(rows, groups, {"Spark", "Ice Strike"}, 1)
    assert others["Electrocute"] == pytest.approx(0.06) and "Freeze" not in others and "Spark" in others["how"]
    assert conditions.buildup_others(rows, groups, {"Ice Strike"}, 1) == {}  # (the main group's own isn't added)
    stats = {"Speed": 2.0, "HitChance": 100, "ElectrocuteBuildupAvg": 0.0}
    assert models.immobilised_share(stats, "Electrocute")[0] == 0.0
    share, how = models.immobilised_share({**stats, "_buildup_others": others}, "Electrocute")
    assert share > 0 and "other skills' hits" in how
    assert models.others_fill({"_buildup_others": others}, "Electrocute") == pytest.approx(0.06)


# Ulaman's Gaze and Counterstancing from the parry plan ------------------------------------------------------------------

def test_ulamans_gaze_and_counterstancing_read_the_parry_plan():
    from builder.search import models

    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    bow = models.Skill("Lightning Arrow", attack=True)
    gaze = "Critical Hit chance is Lucky against Parried enemies"
    ranged = "Successfully Parrying a Melee Hit grants 40% increased Damage to your next Ranged Attack"
    melee = "Successfully Parrying a Projectile Hit grants 40% increased Damage to your next Melee Attack"
    stats = {"enemySkillTime": 0.7, "Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "RangedAttack"},
             "_texts": [gaze, ranged, melee]}
    mods, notes = models.parry_model([parry, bow], stats, bow)
    assert {"kind": "Raw", "name": "CritChanceLucky", "type": "FLAG", "value": 1.0, "condition": "ParryActive"} in mods
    inc = next(m for m in mods if m.get("line") == "1% increased Damage")
    assert 0 < inc["scale"] < 40 and inc["skill"] == "Lightning Arrow"
    assert not any("Projectile" in n["basis"] for n in notes)  # (the Melee half fits no Ranged main skill)
    plan = models.parry_plan([parry, bow], stats, True)
    assert plan.parries == pytest.approx(1 / models.PARRY_EVERY)
    spell = models.parry_model([parry], {**stats, "_main_types": {"Spell"}}, models.Skill("Spark", spell=True))
    assert not any(m.get("name") == "CritChanceLucky" for m in spell[0])  # (no Parry plan for a Spell: no Lucky)


# Styrn's Ferocity's Crush at its uptime (PoB Crushes the boss all fight from any group: probe POE2T4088) ---------------

def test_styrns_crush_is_scaled_to_the_supported_groups_hits():
    styrn = {"name": "Styrn's Ferocity"}
    groups = [{"index": 1, "gems": [{"name": "Shield Charge"}, styrn]}, {"index": 2, "gems": [{"name": "Mace Strike"}]}]
    assert conditions.styrn_crush(groups, {}, 1, None, [])[:2] == (1.0, 0.0)  # (on the main skill: always Crushed)
    side = [{"index": 1, "gems": [{"name": "Mace Strike"}]}, {"index": 2, "gems": [{"name": "Shield Charge"}, styrn]}]
    rows = {2: {"index": 2, "name": "Shield Charge", "speed": 0.05, "triggered": True, "HitChance": 100}}
    up, pdr, how = conditions.styrn_crush(side, rows, 1, None, [])
    assert up == pytest.approx(1 - math.exp(-8 * 0.05)) and pdr == pytest.approx(15 * (1 - up)) and "Shield Charge" in how
    assert conditions.styrn_crush(side, {}, 1, None, [])[:2] == (0.0, 15.0)  # (no hits: never Crushed)
    assert conditions.styrn_crush(side, {}, 1, None, ["Crushes Enemies on Hit"])[:2] == (1.0, 0.0)  # (a Forge Maul)
    assert conditions.styrn_crush(groups[1:], {}, 1, None, []) == (1.0, 0.0, "")
    assert "crushed_target_%_physical_damage_taken_as_armour_break" in conditions.BREAK_STATS


# RESEARCH30 R30-8's answers (the manager, 02:15) ---------------------------------------------------------------------

def test_dark_entropy_is_per_withered_stack():
    line = "Withered also causes enemies to deal 1% reduced Damage"
    mods, notes = conditions.dark_entropy_mods([line], 7)
    assert mods == [{"kind": "Line", "line": "Nearby Enemies deal 1% reduced Damage", "scale": 7.0}]
    assert "if it applies once: 1%" in notes[0]["basis"]
    assert conditions.dark_entropy_mods([line], 0)[0] == []
    assert conditions.dark_entropy_mods([line], 25)[0][0]["scale"] == 10.0  # (Withered stacks to 10)
    assert conditions.dark_entropy_mods([], 7) == ([], [])


def test_the_mark_blind_blinds_the_marked_boss_at_its_marked_share():
    line = "Enemies near Enemies you Mark are Blinded"
    stats = {"Speed": 2.0, "HitChance": 100, "enemySkillTime": 1.0, "_main_types": {"Attack"}, "_marked": (0.6, "t")}
    assert conditions.debuff_uptime("conditionEnemyBlinded")([line], stats)[2] == pytest.approx(0.6)
    assert conditions.debuff_uptime("conditionEnemyBlinded")([line], {**stats, "_marked": None})[2] == 0.0
    assert conditions.MARK_BLIND.match(line)


def test_crushing_blows_land_on_the_priming_hit():
    from builder.search import models

    assert models.primed_hit_refill(10.0, 2.0, same_hit=True) == pytest.approx(7.0)
    assert models.primed_hit_refill(10.0, 2.0) == pytest.approx(7.5)  # (the row: the next hit)


def test_a_reflected_ignite_keeps_the_inflicted_duration():
    line = ["Ignites you cause are reflected back to you"]
    stats = {"IgniteChancePerHit": 50.0, "Speed": 2.0, "HitChance": 100, "IgniteDuration": 8.0, "IgniteAvoidChance": 50,
             "SelfIgniteDuration": 50, "IgniteDPS": 100.0}
    up, how = conditions.reflected_ignite_share(line, stats)
    # 1 Ignite a second, half avoided: over the inflicted 8 s, 1 - 0.5^8; over your own on-you 2 s (the row), 1 - 0.5^2
    assert up == pytest.approx(1 - 0.5 ** 8) and "over 8.0 s" in how
    assert "if your own on-you duration applied (2.0 s): 75%" in how
