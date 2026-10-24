"""Custom models for mechanics PoB ignores (builder/search/models.py), without an engine."""

from __future__ import annotations

import math

import pytest

from builder.ledger import model_entry, translate
from builder.search import models
from builder.search.models import Inputs, Skill


def scaled(mods: list[dict], inputs: dict[str, float], when_on: set[str] = frozenset()) -> dict[str, float]:
    """What PoB would make of model lines: per line text, the product of "more" factors (as a
    percentage) or the sum of "increased" values, for passive model inputs `inputs` and the ledger
    conditions in `when_on`."""
    def holds(mod: dict) -> bool:
        for condition in mod.get("when") or []:
            on = any(v in when_on for v in condition["any"]) if "any" in condition else condition["var"] in when_on
            if on == bool(condition.get("neg")):
                return False
        return True

    totals: dict[str, float] = {}
    for mod in mods:
        if mod["kind"] != "Line" or not holds(mod):
            continue
        value = mod["scale"] * (inputs.get(mod["per"], 0.0) if mod.get("per") else 1.0) + mod.get("base", 0.0)
        if " more " in mod["line"]:
            totals[mod["line"]] = (1 + totals.get(mod["line"], 0.0) / 100) * (1 + value / 100) * 100 - 100
        else:
            totals[mod["line"]] = totals.get(mod["line"], 0.0) + value
    return totals


SPARK = Skill("Spark", group=1, spell=True, cast_time=0.7)
STORM_STATS = {"Mana": 1000, "ManaPerSecondCost": 200, "Speed": 2, "HitChance": 100, "CritChance": 50}


# Elemental Archon ---------------------------------------------------------------------------------


def test_archon_uptime():
    assert models.archon_uptime(0, 0, 0) == pytest.approx(10 / 30)  # 10 s on, 20 s recovery
    assert models.archon_uptime(100, 0, 0) == pytest.approx(20 / 40)  # 100% increased duration
    assert models.archon_uptime(0, 100, 0) == pytest.approx(10 / 20)  # recovery expires twice as fast
    assert models.archon_uptime(0, -30, 0) == pytest.approx(10 / (10 + 20 / 0.7))  # 30% slower
    assert models.archon_uptime(0, 0, 5) == pytest.approx(10 / 35)  # regained 5 s after the recovery
    assert models.archon_uptime(0, 0, 0, no_recovery=True) == 1.0  # Dominion, regained at once
    assert models.archon_uptime(0, -100, 0) == 0.0


def test_more_lines_are_exact_at_the_build_and_follow_the_slope():
    def f(x: dict) -> float:  # 25% more × uptime, like Elemental Archon
        return 25 * models.archon_uptime(x["ArchonDuration"], x["ArchonRecovery"], 0)

    x0 = {"ArchonDuration": 30.0, "ArchonRecovery": 10.0}
    passive = {"ArchonDuration": 20.0, "ArchonRecovery": 10.0}  # the rest comes from items
    mods = models.more_lines("1% more Elemental Damage with Spells", f, x0, passive, ("ArchonDuration", "ArchonRecovery"))
    key = "1% more Elemental Damage with Spells"
    assert scaled(mods, passive)[key] == pytest.approx(f(x0), rel=1e-4)
    # one more Archon Duration passive (+15%): first order, close to the real value
    moved = scaled(mods, {**passive, "ArchonDuration": 35.0})[key]
    assert moved == pytest.approx(f({**x0, "ArchonDuration": 45.0}), rel=0.01)
    assert moved > f(x0)


def test_ledger_turns_archon_lines_into_model_inputs():
    assert translate("Archon recovery period expires 30% slower")[1] == ["mult:ArchonRecovery -30"]
    assert translate("15% increased Archon Buff duration")[1] == ["mult:ArchonDuration 15"]
    assert translate("Gain Elemental Archon after spending 100% of your Maximum Mana")[1] == ["flag:ArchonMana"]
    assert model_entry("Archon Buffs also grant 30% increased Critical Hit Chance").raw["model"] == "archon"
    inputs = models.read_inputs([
        {"source": "passive:Exhaust All Power", "text": "Archon recovery period expires 30% slower"},
        {"source": "passive:Exhaust All Power", "text": "Archon Buffs also grant 30% increased Critical Hit Chance"},
        {"source": "item:Belt", "text": "40% increased Archon Buff duration"},
        {"source": "passive:Archon of the Storm", "text": "Gain Elemental Archon after spending 100% of your Maximum Mana"},
        {"source": "passive:Something", "text": "10% increased Damage"},
    ])
    assert inputs.passive == {"ArchonRecovery": -30.0, "ArchonCritChance": 30.0}
    assert inputs.items == {"ArchonDuration": 40.0}
    assert inputs.flags == {"ArchonMana"} and inputs.total("ArchonDuration") == 40.0


def test_archon_model_counts_the_best_source_averaged_over_the_fight():
    inputs = Inputs(flags={"ArchonMana"})
    mods, notes = models.archon_model(inputs, STORM_STATS, SPARK, {"Spark"})
    # Archon of the Storm: 5 s to spend 1,000 Mana at 200/s, regained 2.5 s after the recovery
    expected = 25 * 10 / (10 + 20 + 2.5)
    assert scaled(mods, {}, {"ArchonMana"})["1% more Elemental Damage with Spells"] == pytest.approx(expected, rel=1e-3)
    assert notes and notes[0]["confidence"] == "theory" and "Archon of the Storm" in notes[0]["basis"]
    # Without a source passive no line counts, but allocating one (a what-if) switches its block on
    assert "1% more Elemental Damage with Spells" not in scaled(mods, {}, set())
    # Exhaust All Power in a what-if: its crit grant counts at the Archon's uptime and effect
    crit = scaled(mods, {"ArchonCritChance": 30.0}, {"ArchonMana"})["1% increased Critical Hit Chance"]
    assert crit == pytest.approx(30 * 10 / 32.5, rel=1e-3)


def test_dominion_trades_effect_for_no_recovery():
    inputs = Inputs(passive={"ArchonEffect": -50.0}, flags={"ArchonInfernal", "ArchonNoRecovery"})
    mods, _ = models.archon_model(inputs, STORM_STATS, SPARK, set())
    value = scaled(mods, {"ArchonEffect": -50.0}, {"ArchonInfernal", "ArchonNoRecovery"})
    assert value["1% more Elemental Damage with Spells"] == pytest.approx(12.5, rel=1e-3)  # always up, half effect


def test_archon_isnt_counted_for_attacks():
    attack = Skill("Ice Shot", group=1, attack=True)
    mods, notes = models.archon_model(Inputs(flags={"ArchonMana"}), STORM_STATS, attack, set())
    assert mods == [] and "not counted" in notes[0]["label"]


def test_archon_costs_spells_their_non_elemental_damage():
    stats = {**STORM_STATS, "ChaosHitAverage": 500, "FireHitAverage": 500}
    mods, _ = models.archon_model(Inputs(flags={"ArchonMana"}), stats, SPARK, set())
    lost = scaled(mods, {}, {"ArchonMana"})["1% more Chaos Damage with Spells"]
    assert lost == pytest.approx(-100 * 10 / 32.5, rel=1e-3)


# Meta-skill Energy -------------------------------------------------------------------------------


def test_energy_rate():
    x = {k: 0.0 for k in models.ENERGY_INPUTS}
    # Cast on Critical: 10 Energy/s, 57% increased at gem level 20, triggers at 70 Energy (Spark, 0.7 s)
    assert models.energy_rate("Cast on Critical", x, 10, 70, False, False) == pytest.approx(10 * 1.57 / 70)
    assert models.energy_rate("Cast on Critical", x, 10, 70, False, True) == pytest.approx(2 * 10 * 1.57 / 70)
    more = {**x, "MetaEnergy": 43.0, "MetaEnergyMore": 35.0, "MetaEnergyCrit": 25.0}
    assert models.energy_rate("Cast on Critical", more, 10, 70, True, False) == pytest.approx(10 * 2.25 * 1.35 / 70)
    assert models.energy_rate("Cast on Critical", more, 10, 70, False, False) == pytest.approx(10 * 2.0 * 1.35 / 70)
    refund = {**x, "EnergyRefund": 25.0}  # a quarter of rounds refund half: 12.5% cheaper
    assert models.energy_rate("Cast on Critical", refund, 10, 70, False, False) == pytest.approx(10 * 1.57 / (70 * 0.875))


def test_cast_on_critical_triggers_from_the_main_skills_crits():
    coc = Skill("Cast on Critical", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50, "LightningCritAverage": 50000,
             "EnemyAilmentThreshold": 100000}
    mods, notes, groups = models.energy_model(Inputs(), stats, [SPARK, coc, comet], SPARK)
    # 1 crit/s × 20 Power × 50% of the Ailment Threshold = 10 Energy/s, ×1.57, over 120 Energy
    rate = next(m["value"] for m in mods if m.get("key") == "triggerRate")
    assert rate == pytest.approx(10 * 1.57 / 120, rel=1e-4)
    assert groups == {2} and "Comet" in notes[0]["label"]
    assert scaled(mods, {})["1% more Damage"] == pytest.approx(-20)  # its spells deal 20% less
    # An Energy passive in a what-if: rate × (1 + 8 / 157)
    moved = scaled(mods, {"MetaEnergy": 8.0})["1% more Damage"]
    assert moved == pytest.approx((0.8 * (1 + 8 / 157) - 1) * 100, rel=1e-3)


def test_unmodeled_metas_count_nothing_rather_than_a_self_cast():
    dodge = Skill("Cast on Dodge", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    mods, notes, groups = models.energy_model(Inputs(), {}, [SPARK, dodge, comet], SPARK)
    assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == 0.0
    assert groups == set() and "not counted" in notes[0]["label"]


# Darkness ----------------------------------------------------------------------------------------


def test_darkness_joins_the_max_hit_pool_once():
    stats = {"Darkness": 1000, "PhysicalMaximumHitTaken": 4000, "PhysicalTotalHitPool": 2000,
             "FireMaximumHitTaken": "inf", "FireTotalHitPool": 2000}
    models.with_darkness(stats)
    assert stats["PhysicalMaximumHitTaken"] == pytest.approx(6000) and stats["PoBPhysicalMaximumHitTaken"] == 4000
    models.with_darkness(stats)  # idempotent
    assert stats["PhysicalMaximumHitTaken"] == pytest.approx(6000)
    assert stats["FireMaximumHitTaken"] == "inf"
    assert models.darkness_note(stats)["confidence"] == "reported"
    plain = {"Darkness": 0, "PhysicalMaximumHitTaken": 4000, "PhysicalTotalHitPool": 2000}
    assert models.with_darkness(dict(plain)) == plain


# Elemental Expression ----------------------------------------------------------------------------


def test_elemental_expression_rate():
    expression = Skill("Elemental Expression", group=3, spell=True, triggered=True, cooldown=0.25)
    melee = Skill("Falling Thunder", group=1, attack=True, melee=True)
    slow = {"Speed": 2, "HitChance": 90, "CritChance": 50}
    assert models.expression_rate(melee, slow, expression)[0] == pytest.approx(0.9)
    fast = {"Speed": 10, "HitChance": 100, "CritChance": 80}
    assert models.expression_rate(melee, fast, expression)[0] == pytest.approx(4.0)  # 0.25 s cooldown
    assert models.expression_rate(SPARK, fast, expression)[0] == 0.0
    mods, notes, groups = models.expression_model([melee, expression], {**slow, "Str": 50, "Dex": 150, "Int": 100}, melee)
    assert groups == {3: 4} and mods[0]["value"] == pytest.approx(0.9)  # Arcing Bolt, from the most Dexterity
    assert "Arcing Bolt" in notes[0]["basis"] and "50%" in notes[0]["basis"]


# Traveller's Wisdom ------------------------------------------------------------------------------


def test_travellers_wisdom_switches_its_condition_on():
    assert translate("Attribute Passive Skills can instead grant 5% increased Damage")[1] == ["flag:TravellersWisdom"]
    assert model_entry("Attribute Passive Skills can instead grant 5% increased Cost Efficiency").raw["model"] == "traveller"
    assert math.isfinite(len(models.MODEL_FIELDS))


def test_lines_a_model_reads_are_shown_as_modeled():
    from builder.search.run import annotate_blind_spots

    found = annotate_blind_spots([
        {"source": "passive: Archon of the Storm", "line": "Gain Elemental Archon after spending 100% of your Maximum Mana"},
        {"source": "Weapon 1: Seeing Stars", "line": "Energy Generation is doubled"},  # read from items too
        {"source": "passive: Somewhere", "line": "A line nothing knows"},
    ])
    assert [f.get("modeled_by") for f in found] == ["archon", "energy", None]
    assert "Elemental Archon" in found[0]["as"][0] and "corrected_by" not in found[2]
