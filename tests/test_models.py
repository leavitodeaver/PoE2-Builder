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


def test_the_other_metas_gain_energy_from_their_own_events():
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "enemySkillTime": 0.5, "EvadeChance": 20, "EffectiveBlockChance": 50,
             "PhysicalHitAverage": 1000}

    def rate(meta: str, main: Skill) -> float:
        mods, notes, _ = models.energy_model(Inputs(), stats, [main, Skill(meta, group=2, energy=True), comet], main)
        return next(m["value"] for m in mods if m.get("key") == "triggerRate")

    # Cast on Block: 2 boss hits/s, 80% not evaded, 50% blocked = 0.8 blocks/s x 25 Energy, x1.57, over 120 Energy
    assert rate("Cast on Block", SPARK) == pytest.approx(0.8 * 25 * 1.57 / 120, rel=1e-4)
    # Thundergod's Wrath: 2 melee hits/s x 3 per Power x 20; nothing from a spell
    slam = Skill("Boneshatter", group=1, attack=True, melee=True)
    assert rate("Thundergod's Wrath", slam) == pytest.approx(2 * 60 * 1.72 / 120, rel=1e-4)
    assert rate("Thundergod's Wrath", SPARK) == 0.0
    # Spellslinger: 2 casts/s of a 0.7 s spell x 0.2 per 10 ms = 14 Energy each
    spark = Skill("Spark", group=1, spell=True, cast_time=0.7)
    assert rate("Spellslinger", spark) == pytest.approx(2 * 14 * 1.72 / 120, rel=1e-4)
    # Cast on Melee Stun: one Heavy Stun per 30 s (Physical melee) x 200 Energy, no increased Energy on the gem
    assert rate("Cast on Melee Stun", slam) == pytest.approx(200 / 30 / 120, rel=1e-4)
    # Energy on kills: none on a boss, and the guide says why
    mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill("Cast on Melee Kill", group=2, energy=True), comet], slam)
    assert "none on a single boss" in notes[0]["basis"]


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


# Iron Ward ---------------------------------------------------------------------------------------

WARD_STATS = {models.STORE_CAP: 22886, models.STORE_GAIN: 480}
WARD = Skill("Iron Ward", group=1, cast_time=1.0, level_stats=WARD_STATS)
NOVA = Skill("Iron Ward", group=1, cast_time=1.0, level_stats={**WARD_STATS, models.STORE_AREA: 2})
PINNACLE = {"enemySkillTime": 0.7, "PhysicalEnemyDamage": 979.475, "PhysicalDamageReduction": 50,
            "PhysicalDamageReductionMax": 90, "Armour": 9795, "BasePhysicalDamageReductionWhenHit": 0}


def test_iron_ward_store_fills_at_the_boss_hit_rate_after_mitigation():
    store = models.iron_ward_store(WARD, PINNACLE)
    fill = 4.8 * 979.475 * 0.5 / 0.7  # 480% of half of each 979 hit, a hit every 0.7 s
    assert store.fill == pytest.approx(fill)
    assert store.uses == pytest.approx(fill / 22886) and not store.use_capped  # released whenever full
    dodgy = models.iron_ward_store(WARD, {**PINNACLE, "EvadeChance": 50, "EffectiveBlockChance": 20})
    assert dodgy.fill == pytest.approx(fill * 0.5 * 0.8)  # evaded and blocked hits store nothing
    flooded = models.iron_ward_store(WARD, {**PINNACLE, "PhysicalEnemyDamage": 50000})
    assert flooded.use_capped and flooded.uses == pytest.approx(1.0)  # at most one use per 1 s use time
    assert models.iron_ward_store(WARD, {**PINNACLE, "enemySkillTime": 0}).uses == 0.0


def test_iron_ward_nova_gets_the_full_store_as_base_damage_and_its_use_rate():
    mods, notes = models.iron_ward_model([WARD, NOVA], PINNACLE)
    data = {m["key"]: m["value"] for m in mods if m["kind"] == "SkillData"}
    assert data["PhysicalMin"] == data["PhysicalMax"] == 22886
    uses = models.iron_ward_store(WARD, PINNACLE).uses
    assert data["timeOverride"] == pytest.approx(1 / uses, rel=1e-4)
    area = next(m for m in mods if m["kind"] == "Line" and m["line"] == "1% more Area of Effect")
    assert area["scale"] == 200 and area["skill"] == "Iron Ward"  # 2% per % stored, released full
    assert notes[0]["confidence"] == "theory" and "refills in" in notes[0]["basis"]


def test_iron_ward_lines_let_armour_what_ifs_see_the_faster_store():
    mods, _ = models.iron_ward_model([WARD, NOVA], PINNACLE)
    by_stat = {m["stat"]: m for m in mods if m.get("stat")}

    def factor(stats: dict) -> float:  # the "more Damage" lines PoB would apply
        product = 1.0
        for stat, mod in by_stat.items():
            product *= 1 + (mod["scale"] * stats[stat] + mod["base"]) / 100
        return product

    assert factor(PINNACLE) == pytest.approx(1.0, rel=1e-4)  # exact at the current build (lines rounded)
    hit, armour = PINNACLE["PhysicalEnemyDamage"], PINNACLE["Armour"]
    more = {**PINNACLE, "Armour": armour + 200}
    exact = (armour + 200) / (armour + 200 + 10 * hit) / (armour / (armour + 10 * hit))
    assert factor(more) == pytest.approx(exact, rel=2e-3)  # novas per second follow the % prevented
    flat = {**PINNACLE, "BasePhysicalDamageReductionWhenHit": 5}
    assert factor(flat) == pytest.approx(55 / 50, rel=1e-4)
    capped = {**PINNACLE, "PhysicalDamageReduction": 90}
    assert not [m for m in models.iron_ward_model([WARD, NOVA], capped)[0] if m.get("stat")]


def test_iron_ward_model_switches_off_through_its_ledger_entry():
    mods, notes = models.iron_ward_model([WARD, NOVA], PINNACLE, enabled=False)
    assert mods == [] and notes[0]["value"] == "not counted" and models.IRON_WARD_ENTRY in notes[0]["basis"]
    assert models.iron_ward_model([SPARK], PINNACLE) == ([], [])
    from builder.ledger import entries
    entry = next(e for e in entries() if e.id == models.IRON_WARD_ENTRY)
    assert entry.raw["model"] == "iron_ward" and entry.raw["test_in_game"] and entry.confidence == "theory"


def effective(mods: list[dict]) -> list[tuple]:
    """(line, value, when) of each model line, its bridge input (models.derived) applied: what PoB counts for the build."""
    values = {m["name"]: m["value"] for m in mods if m["kind"] == "Derived"}
    return [(m["line"], pytest.approx(m["scale"] * values[m["per"]] + m["base"] if "per" in m else m["scale"], rel=1e-3),
             m.get("when")) for m in mods if m["kind"] == "Line"]


def derived_fns(mods: list[dict]) -> dict[str, str]:
    return {m["name"]: m["fn"] for m in mods if m["kind"] == "Derived"}


def test_gem_stats_pob_never_reads_become_scaled_lines():
    tempest = models.Skill("Mana Tempest", level_stats={
        "non_skill_base_all_damage_%_to_gain_as_lightning_with_spells_from_buff": 78,
        "mana_tempest_empowered_spells_split_to_X_additional_targets": 3})
    # regeneration covers the spell and the storm's drain (1% of 2000 + 30% of 100): up all the time
    rich = {"Mana": 2000, "ManaRegenRecovery": 200, "ManaPerSecondCost": 100}
    mods, notes = models.gem_stat_model([models.Skill("Spark"), tempest], stats=rich)
    assert effective(mods) == [("Gain 1% of Damage as Extra Lightning Damage with Spells", 78.0, None)]
    assert derived_fns(mods) == {"UptimeManaTempest": "manaTempest"}  # the bridge works the uptime out again per what-if
    assert len(notes) == 1 and notes[0]["confidence"] == "theory" and "uptime 100%" in notes[0]["basis"] and notes[0]["play"]
    # it can't keep it up: 2000 Mana last 2000/(100 + 50 - 120) = 66.7 s, then 2000/(120 - 100) = 100 s to refill
    mods, notes = models.gem_stat_model([tempest], stats={"Mana": 2000, "ManaRegenRecovery": 120, "ManaPerSecondCost": 100})
    assert effective(mods)[0][1] == pytest.approx(78 * 66.667 / 166.667, rel=1e-3)
    # switched off by forbidding the entry, and nothing without the skill or with no Mana
    assert models.gem_stat_model([tempest], {"gem-mana-tempest-lightning"}, rich) == ([], [])
    assert models.gem_stat_model([models.Skill("Spark")], stats=rich) == ([], [])
    assert effective(models.gem_stat_model([tempest], stats={})[0])[0][1] == 0  # no Mana: 0 until a what-if gives some
    assert "Mana Tempest" in models.gem_stat_skills()


def test_lunar_blessing_uptime_from_rage_and_cooldown():
    skill = models.Skill("Lunar Blessing", cooldown=22.0, cooldown_recovery=1.0,
                         level_stats={"base_skill_effect_duration": 5000, "skill_lunar_blessing_extend_buff_by_X_ms_per_rage": 200})
    up, why, play = models.lunar_blessing_uptime(skill, {"MaximumRage": 30})
    assert up == pytest.approx(11 / 22) and "30 Rage" in why and play
    assert models.lunar_blessing_uptime(skill, {"MaximumRage": 100})[0] == 1.0  # 25 s outlasts its cooldown


def test_resistance_rings_count_items_by_the_users_rule():
    lines = [{"source": "item:Ring 1", "text": "+7% to Cold and Lightning Resistances per Equipped Item with a Fire Resistance Modifier"},
             {"source": "item:Helmet", "text": "+30% to Fire Resistance"},
             {"source": "item:Body Armour", "text": "+12% to all Elemental Resistances"},
             {"source": "item:Gloves", "text": "+15% to Fire and Cold Resistances"},
             {"source": "item:Boots", "text": "+14% to Fire Resistance"},  # a socketed rune's line: also an item line of the Boots
             {"source": "item:Belt", "text": "+20% to Cold Resistance"},
             {"source": "item:Amulet", "text": "+5% to Maximum Fire Resistance"},
             {"source": "passive:Somewhere", "text": "+10% to Fire Resistance"}]
    mods, notes = models.resist_rings_model(lines)
    assert effective(mods) == [("+1% to Cold and Lightning Resistances", 28.0, None)]  # 4 items x 7
    assert mods[1] == {"kind": "Derived", "name": "ResistItemsFire", "fn": "resistItems", "value": 4, "args": {"element": "Fire"}}
    assert notes[0]["confidence"] == "verified_by_you" and "Boots" in notes[0]["basis"]
    assert models.resist_rings_model(lines[1:]) == ([], [])
    assert effective(models.resist_rings_model(lines[:1])[0])[0][1] == 0  # no items yet: what-ifs that add one see it


def test_harmony_within_counts_only_while_unreserved_mana_is_above_life():
    passive = models.Inputs(flags={"HarmonyWithin"})
    mods, notes = models.harmony_model(passive, {"ManaUnreserved": 3000, "Life": 2000})
    assert effective(mods) == [("1% of Damage is taken from Mana before Life", 100.0, [{"var": "HarmonyWithin"}])]
    assert notes[0]["confidence"] == "theory" and derived_fns(mods) == {"HarmonyWithin": "harmony"}
    mods, notes = models.harmony_model(passive, {"ManaUnreserved": 1500, "Life": 2000})
    assert effective(mods)[0][1] == 0 and notes[0]["value"] == "not counted"
    assert models.harmony_model(models.Inputs(), {"ManaUnreserved": 3000, "Life": 2000}) == ([], [])


def test_melting_maelstrom_pulses_are_the_mana_you_spend():
    line = {"source": "item:Flask 2", "text": "Every 3 seconds during Effect, deal 100% of Mana spent in those seconds as Chaos Damage to Enemies within 3 metres"}
    dps, notes = models.maelstrom_model([line], {"ManaPerSecondCost": 120})
    assert dps == pytest.approx(120.0) and notes[0]["confidence"] == "theory" and notes[0]["play"]
    assert models.maelstrom_model([line], {"ManaPerSecondCost": 120}, enemy_chaos_resist=25)[0] == pytest.approx(90.0)
    assert models.maelstrom_model([{**line, "source": "passive:X"}], {"ManaPerSecondCost": 120}) == (0.0, [])
    assert models.maelstrom_inputs([line], 120.0) == [{"kind": "Derived", "name": "Maelstrom", "fn": "maelstrom", "value": 12000,
                                                       "args": {"share": 1.0, "chaosResist": 0.0}}]


def test_guided_hand_boosts_the_attacks_after_a_heavy_stun():
    passive = models.Inputs(flags={"GuidedHand"})
    lines = [{"source": "passive:Guided Hand", "text": "Ancestrally Boosted Attacks deal 30% increased Damage"}]
    slam = models.Skill("Boneshatter", attack=True, melee=True)
    mods, notes = models.guided_hand_model(passive, lines, {"Speed": 2.0, "PhysicalHitAverage": 1000}, slam)
    # one Heavy Stun per 30 s against 2 attacks per second: 1/60 of attacks get 30% increased damage
    assert effective(mods) == [("1% increased Attack Damage", 0.5, [{"var": "GuidedHand"}])]
    assert mods[1]["fn"] == "guidedHand" and mods[1]["args"] == {"every": 30.0}
    assert "30 s" in notes[0]["basis"]
    spell = models.Skill("Spark", spell=True)
    assert models.guided_hand_model(passive, lines, {"Speed": 2.0}, spell) == ([], [])
    assert models.guided_hand_model(models.Inputs(), lines, {"Speed": 2.0}, slam) == ([], [])


def test_sanguine_tides_gain_follows_the_life_spent():
    passive = models.Inputs(flags={"SanguineTides"})
    mods, notes = models.sanguine_model(passive, {"Life": 2000, "LifePerSecondCost": 100})  # 5% of Life per second
    assert effective(mods) == [("Gain 1% of Damage as Extra Physical Damage", 12.5, [{"var": "SanguineTides"}])]
    assert derived_fns(mods) == {"SanguineTides": "sanguine"}
    assert effective(models.sanguine_model(passive, {"Life": 2000})[0])[0][1] == 0
    assert models.sanguine_model(models.Inputs(), {"Life": 2000, "LifePerSecondCost": 100}) == ([], [])
