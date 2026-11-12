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
    # Archon of the Storm: 5 s to spend 1,000 Mana at 200/s, regained a full 5 s after the recovery (the lower bound,
    # MECHC2: Mana spent during the Archon and its recovery not counted; was half, 2.5 s)
    expected = 25 * 10 / (10 + 20 + 5)
    assert scaled(mods, {}, {"ArchonMana"})["1% more Elemental Damage with Spells"] == pytest.approx(expected, rel=1e-3)
    assert notes and notes[0]["confidence"] == "theory" and "Archon of the Storm" in notes[0]["basis"]
    # The other reading, uncounted: the 5 s of spending runs through the 30 s of Archon and recovery, so no wait (10 / 30)
    other = next(n for n in notes if "not counted" in n["label"])
    assert other["value"] == 0 and "up 33% instead of 29%" in other["basis"]
    # Without a source passive no line counts, but allocating one (a what-if) switches its block on
    assert "1% more Elemental Damage with Spells" not in scaled(mods, {}, set())
    # Exhaust All Power in a what-if: its crit grant counts at the Archon's uptime and effect
    crit = scaled(mods, {"ArchonCritChance": 30.0}, {"ArchonMana"})["1% increased Critical Hit Chance"]
    assert crit == pytest.approx(30 * 10 / 35, rel=1e-3)


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
    assert lost == pytest.approx(-100 * 10 / 35, rel=1e-3)


# Meta-skill Energy -------------------------------------------------------------------------------


def test_gem_energy_from_level():
    # game files: 3% per level above 1 (Elemental Invocation round(3.6%)); no level reported: 20, assumed
    assert models.gem_energy("Thundergod's Wrath", 11) == 30.0
    assert models.gem_energy("Thundergod's Wrath", 20) == models.gem_energy("Thundergod's Wrath", None) == 57.0
    assert models.gem_energy("Cast on Critical", 40) == 117.0
    assert (models.gem_energy("Elemental Invocation", 2), models.gem_energy("Elemental Invocation", 20)) == (4.0, 68.0)
    stats = {"Speed": 2.0, "HitChance": 100}
    slam = Skill("Boneshatter", group=1, attack=True, melee=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill("Thundergod's Wrath", group=2, energy=True, level=11), comet], slam)
    rate = next(m["value"] for m in mods if m.get("key") == "triggerRate")
    assert rate == pytest.approx(2 * 60 * 1.30 / 120, rel=1e-4)  # Mjolner's level-11 Thundergod's Wrath
    assert "30% at level 11" in notes[0]["basis"]
    # Fire Spell on Hit (PoB: Fire Spell on Melee Hit) gains as Thundergod's Wrath (game files)
    mods, _, _ = models.energy_model(Inputs(), stats, [slam, Skill("Fire Spell on Melee Hit", group=2, energy=True, level=11), comet], slam)
    assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == pytest.approx(rate)


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


def test_spells_share_a_round_and_the_groups_energy_supports_count():
    """Slice 3: two spells in one meta group trigger once per round each, and a round costs both their Energy. Boundless
    Energy II (45% increased) and Energy Retention (35% chance to refund half: the game files' stats, which PoB doesn't
    read) count for the meta group they support only."""
    coc = Skill("Cast on Critical", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    spark = Skill("Spark", group=2, spell=True, triggered=True, cast_time=0.7)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50, "LightningCritAverage": 50000,
             "EnemyAilmentThreshold": 100000}
    main = Skill("Arc", group=1, spell=True, cast_time=0.8)
    rates = lambda mods: {m["skill"]: m["value"] for m in mods if m.get("key") == "triggerRate"}
    mods, _, _ = models.energy_model(Inputs(), stats, [main, coc, comet, spark], main)
    assert rates(mods) == {"Comet": pytest.approx(10 * 1.57 / 190, rel=1e-4), "Spark": pytest.approx(10 * 1.57 / 190, rel=1e-4)}
    supported = {2: ["Boundless Energy II", "Energy Retention"], 1: ["Boundless Energy I"]}
    mods, notes, _ = models.energy_model(Inputs(), stats, [main, coc, comet, spark], main, supported)
    assert rates(mods)["Comet"] == pytest.approx(10 * 2.02 / (190 * (1 - 0.35 / 2)), rel=1e-4)
    assert "Boundless Energy II, Energy Retention" in notes[0]["basis"] and "Boundless Energy I," not in notes[0]["basis"]
    elsewhere, _, _ = models.energy_model(Inputs(), stats, [main, coc, comet, spark], main, {1: ["Boundless Energy II"]})
    assert rates(elsewhere)["Comet"] == pytest.approx(10 * 1.57 / 190, rel=1e-4)


def test_midnight_zenith_cannot_feed_a_meta_skills_energy():
    """base_skill_cannot_generate_energy (Midnight Zenith, game files; COV2's open stat, MECH47): its use gains a meta
    skill no Energy, so the spells socketed in the meta trigger 0 times."""
    coc = Skill("Cast on Critical", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50}
    zenith = Skill("Midnight Zenith", group=1)
    mods, notes, _ = models.energy_model(Inputs(), stats, [zenith, coc, comet], zenith)
    assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == 0.0
    assert "cannot cause Meta Skills" in notes[0]["basis"]


def test_unmodeled_metas_count_nothing_rather_than_a_self_cast():
    dodge = Skill("Feral Invocation", group=2, energy=True)
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
    assert rate("Thundergod's Wrath", slam) == pytest.approx(2 * 60 * 1.57 / 120, rel=1e-4)
    assert rate("Thundergod's Wrath", SPARK) == 0.0
    # Spellslinger: 2 casts/s of a 0.7 s spell x 0.2 per 10 ms = 14 Energy each
    spark = Skill("Spark", group=1, spell=True, cast_time=0.7)
    assert rate("Spellslinger", spark) == pytest.approx(2 * 14 * 1.57 / 120, rel=1e-4)
    # Cast on Dodge: one roll per 2 s (the user's default) of 4.6 m (the game files) x 2 Energy per metre, x1.57, over 120
    assert rate("Cast on Dodge", slam) == pytest.approx(4.6 * 2 / 2 * 1.57 / 120, rel=1e-4)
    # ... and the build that counts on it loses the 0.75 s each roll takes from its main skill: 37.5% less speed
    mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill("Cast on Dodge", group=2, energy=True), comet], slam)
    speed = [m for m in mods if m.get("line") == "1% more Attack and Cast Speed"]
    assert len(speed) == 1 and speed[0]["scale"] == pytest.approx(-37.5) and "Time spent dodge rolling" in notes[-1]["label"]
    mods, _, _ = models.energy_model(Inputs(), stats, [slam, Skill("Cast on Block", group=2, energy=True), comet], slam)
    assert not [m for m in mods if m.get("line") == "1% more Attack and Cast Speed"]  # other builds aren't charged
    # Energy on kills: none on a boss, and the guide says why
    mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill("Reaper's Invocation", group=2, energy=True), comet], slam)
    assert "none on a single boss" in notes[0]["basis"]


def test_deprecated_metas_trigger_nothing_and_say_why():
    """Curse on Block, Cast on Melee Stun, Cast on Melee Kill: in the 0.5 gem data, flagged item-granted, but no unique,
    base, rune or passive grants them (the user found no Curse on Block in game, 7 Nov). A PoB code that has one gets no
    triggers from it, and the search can't pick one."""
    from builder.data import GameData

    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    slam = Skill("Boneshatter", group=1, attack=True, melee=True)
    stats = {"Speed": 2, "HitChance": 100, "enemySkillTime": 0.5, "EffectiveBlockChance": 50, "PhysicalHitAverage": 1000}
    for name in models.DEPRECATED:
        assert name not in models.META_GEMS
        mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill(name, group=2, energy=True), comet], slam)
        assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == 0.0
        assert "deprecated" in notes[0]["basis"]
    try:
        data = GameData()
    except FileNotFoundError:
        return
    granted = " ".join(str(part) for part in (data._part("uniques"), data._part("bases"), data._part("tree")))
    for name in models.DEPRECATED:
        assert f"Grants Skill: Level (1-20) {name}" not in granted and f"Grants Skill: {name}" not in granted
    for name in ("Cast on Block", "Thundergod's Wrath", "Spellslinger", "Cast on Charm Use"):  # current item sources
        assert f"Grants Skill: Level (1-20) {name}" in granted


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


def test_a_support_stat_reaches_the_skill_the_support_is_on_and_only_it():
    stat = "support_dual_cascade_aftershocks_attack_speed_+%_final"
    slam = models.Skill("Earthquake", group=1, attack=True, melee=True, support_stats={stat: -15})
    other = models.Skill("Boneshatter", group=2, attack=True, melee=True)  # no Tectonic Slams in its group
    mods, notes = models.gem_stat_model([other, slam])
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Attack Speed", -15, "Earthquake")]
    assert len(notes) == 1 and "Tectonic Slams" in notes[0]["basis"] and "on Earthquake" in notes[0]["basis"]
    # a second copy of the skill (another group, same name: PoB's SkillName tag is shared) is counted once
    assert len(models.gem_stat_model([slam, models.Skill("Earthquake", group=3, support_stats={stat: -15})])[0]) == 1
    # nothing without the support, nothing when the entry is forbidden
    assert models.gem_stat_model([other]) == ([], [])
    assert models.gem_stat_model([slam], {"support-tectonic-slams-attack-speed"}) == ([], [])
    # the bridge is asked for the support's stat, and a support's name isn't asked for as an active skill's level stats
    assert stat in models.gem_stat_support_stats() and "Tectonic Slams" not in models.gem_stat_skills()
    # an active skill's own level stat of that id isn't a support's (only supportStats count for support entries)
    assert models.gem_stat_model([models.Skill("Earthquake", level_stats={stat: -15})]) == ([], [])
    assert models.Skill.of({"name": "Earthquake", "supportStats": {stat: -15}}).support_stats == {stat: -15}


def test_support_entries_list_every_stat_their_match_takes():
    from builder.ledger import gem_stat_entries

    for entry in gem_stat_entries():
        if entry.raw.get("support") and entry.replace:
            ids = entry.raw.get("support_stats") or []
            assert ids and all(entry.pattern.search(stat) for stat in ids), entry.id


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


def test_heavy_stuns_come_from_the_hits_buildup_less_cold_at_the_game_files_threshold():
    """The boss's Heavy Stuns per second from PoB's per-hit buildup (POE2 9 Nov, MECH20): x9.38/12.35 for the game
    files' Arbiter of Ash threshold (the user's boss, 9 Nov), Cold's share taken out (the game's Cold text), +500% threshold for 16.5 s after each stun."""
    phys = {"HeavyStunBuildupAvg": 10.0, "Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000}
    stuns, basis = models.heavy_stun_rate(phys)
    t = 1 / (0.10 * 9.38 / 12.35 * 2.0)  # 6.58 s to fill at the plain threshold; 6t > 16.5 s
    assert stuns == pytest.approx(1 / (16.5 + (1 - 16.5 / (6 * t)) * t)) and "one every 20.3 s" in basis
    fast = {**phys, "HeavyStunBuildupAvg": 50.0}  # 6t = 7.9 s: within the +500% window
    assert models.heavy_stun_rate(fast)[0] == pytest.approx(0.50 * 9.38 / 12.35 * 2.0 / 6)
    # half the weighted hit Cold (Physical counts x1.5): Cold's share builds none
    mixed = {**phys, "PhysicalHitAverage": 1000, "ColdHitAverage": 1500}
    t = 1 / (0.10 * 0.5 * 9.38 / 12.35 * 2.0)
    assert models.heavy_stun_rate(mixed)[0] == pytest.approx(1 / (16.5 + (1 - 16.5 / (6 * t)) * t))
    assert models.heavy_stun_rate({**phys, "PhysicalHitAverage": 0, "ColdHitAverage": 1000})[0] == 0
    assert models.heavy_stun_rate({"Speed": 2.0, "HitChance": 100})[0] == 0


def test_guided_hand_boosts_the_attacks_after_a_heavy_stun():
    passive = models.Inputs(flags={"GuidedHand"})
    lines = [{"source": "passive:Guided Hand", "text": "Ancestrally Boosted Attacks deal 30% increased Damage"}]
    slam = models.Skill("Boneshatter", attack=True, melee=True)
    stats = {"Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000, "HeavyStunBuildupAvg": 10.0}
    mods, notes = models.guided_hand_model(passive, lines, stats, slam)
    stuns = models.heavy_stun_rate(stats)[0]  # one per 20.3 s against 2 attacks per second
    (text, value, when), = effective(mods)
    assert text == "1% increased Attack Damage" and value == 30 * stuns / 2.0 and when == [{"var": "GuidedHand"}]
    assert mods[1]["fn"] == "guidedHand" and mods[1]["value"] == round(stuns / 2.0 * models.UNIT)
    assert "20.3 s" in notes[0]["basis"]
    # no buildup (a Cold attack): nothing boosted
    cold = models.guided_hand_model(passive, lines, {**stats, "PhysicalHitAverage": 0, "ColdHitAverage": 1000}, slam)
    assert effective(cold[0])[0][1] == 0.0
    spell = models.Skill("Spark", spell=True)
    assert models.guided_hand_model(passive, lines, {"Speed": 2.0}, spell) == ([], [])
    assert models.guided_hand_model(models.Inputs(), lines, {"Speed": 2.0}, slam) == ([], [])


def test_the_bridge_works_guided_hands_share_out_as_the_model_does():
    """engine/bridge.lua's DERIVED.guidedHand, run on PoB's LuaJIT alone, against models.guided_hand_model."""
    import re as _re

    from builder import ENGINE_DIR, POB_DIR
    from builder.pob.lua import LuaState

    source = (ENGINE_DIR / "bridge.lua").read_text(encoding="utf-8")
    block = _re.search(r"local HEAVY_STUN_TYPES.*?\nend\n", source, _re.S).group(0)
    passive = models.Inputs(flags={"GuidedHand"})
    lines = [{"source": "passive:Guided Hand", "text": "Ancestrally Boosted Attacks deal 30% increased Damage"}]
    slam = models.Skill("Boneshatter", attack=True, melee=True)
    lua = LuaState(POB_DIR / "runtime")
    try:
        for stats in ({"Speed": 2.0, "HitChance": 90, "PhysicalHitAverage": 1000, "ColdHitAverage": 400,
                       "PhysicalCritAverage": 2500, "CritChance": 20, "HeavyStunBuildupAvg": 10.0},
                      {"Speed": 1.5, "HitChance": 100, "PhysicalHitAverage": 1000, "HeavyStunBuildupAvg": 60.0},
                      {"Speed": 1.5, "HitChance": 100, "ColdHitAverage": 1000, "HeavyStunBuildupAvg": 60.0}):
            derived = models.guided_hand_model(passive, lines, stats, slam)[0][1]
            out = ", ".join(f"{k} = {v}" for k, v in stats.items())
            args = ", ".join(f"{k} = {v!r}" for k, v in derived["args"].items())
            code = ("local DERIVED, DERIVED_UNIT = { }, 1000000\n"
                    "local function num(v) return type(v) == 'number' and v == v and v or 0 end\n"
                    f"{block}return tostring(DERIVED.guidedHand({{ {out} }}, {{ {args} }}))")
            assert int(float(lua.run(code, "guided")[0])) == derived["value"]
    finally:
        lua.close()


def test_sanguine_tides_gain_follows_the_life_spent():
    passive = models.Inputs(flags={"SanguineTides"})
    mods, notes = models.sanguine_model(passive, {"Life": 2000, "LifePerSecondCost": 100})  # 5% of Life per second
    assert effective(mods) == [("Gain 1% of Damage as Extra Physical Damage", 12.5, [{"var": "SanguineTides"}])]
    assert derived_fns(mods) == {"SanguineTides": "sanguine"}
    assert effective(models.sanguine_model(passive, {"Life": 2000})[0])[0][1] == 0
    assert models.sanguine_model(models.Inputs(), {"Life": 2000, "LifePerSecondCost": 100}) == ([], [])


def test_deadly_resolve_slashes_once_per_stage_when_the_boss_hits():
    shield = Skill("Resonating Shield", group=5, attack=True)
    slash = Skill("Deadly Resolve", group=5, attack=True, triggered=True)
    stats = {"enemySkillTime": 0.7, "EvadeChance": 4, "EffectiveBlockChance": 25}
    mods, notes = models.deadly_resolve_model([shield, slash], stats, shield)
    # 1/0.7 x 0.96 x 0.75 = 1.03 hits land per second; a stage per second of channelling caps it at 1
    assert next(m["value"] for m in mods if m["key"] == "triggerRate") == pytest.approx(1.0)
    assert "a stage per second" in notes[0]["basis"]
    # a build that blocks half and evades half: 0.36 hits/s
    few = models.deadly_resolve_model([shield, slash], {"enemySkillTime": 0.7, "EvadeChance": 50, "EffectiveBlockChance": 50}, shield)[0]
    assert next(m["value"] for m in few if m["key"] == "triggerRate") == pytest.approx(0.25 / 0.7, rel=1e-3)
    # not the main skill's group: not channelled through the fight
    other = Skill("Boneshatter", group=1, attack=True, melee=True)
    assert next(m["value"] for m in models.deadly_resolve_model([other, slash], stats, other)[0] if m["key"] == "triggerRate") == 0.0
    assert models.deadly_resolve_model([shield], stats, shield) == ([], [])


def test_cast_on_critical_reads_an_attacks_crits_from_its_main_hand():
    slam = Skill("Boneshatter", group=1, attack=True, melee=True)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50, "MainHand.PhysicalCritAverage": 50000,
             "EnemyAilmentThreshold": 100000}
    gain, basis = models.energy_gain("Cast on Critical", stats, "Boneshatter", slam)
    assert gain == pytest.approx(1 * 20 * 0.5) and "50,000 critical hit" in basis


def test_barrier_invocation_gains_energy_from_the_energy_shield_the_boss_takes():
    stats = {"EnergyShield": 3000, "enemySkillTime": 0.5, "EvadeChance": 0, "EffectiveBlockChance": 50,
             "PhysicalTakenHit": 600, "FireTakenHit": 400, "EnergyShieldRegenRecovery": 1500}
    gain, basis = models.energy_gain("Barrier Invocation", stats, "Spark", Skill("Spark", spell=True))
    assert gain == pytest.approx(1.0 * 1000 * 0.1) and "1 Energy per 10 lost" in basis  # 1 hit/s lands, 1,000 ES each
    assert models.energy_gain("Barrier Invocation", {**stats, "EnergyShield": 0}, "Spark")[0] == 0.0
    # Only what comes back between hits can be lost again: 300/s regenerated, a hit every second, recharge never starts
    # (2 s delay) -> 300 per hit. A bare 40 Energy Shield with no recovery gives nothing over a fight.
    slow = {**stats, "EnergyShieldRegenRecovery": 300, "EnergyShieldRecharge": 600, "EnergyShieldRechargeDelay": 2}
    assert models.energy_gain("Barrier Invocation", slow, "Spark")[0] == pytest.approx(300 * 0.1)
    gaps = {**slow, "enemySkillTime": 1.5}  # a hit lands every 3 s: 3 × 300 regenerated + 1 s × 600 recharged
    assert models.energy_gain("Barrier Invocation", gaps, "Spark")[0] == pytest.approx(1000 / 3 * 0.1)
    bare = {"EnergyShield": 40, "enemySkillTime": 0.7, "EffectiveBlockChance": 25, "PhysicalTakenHit": 500,
            "EnergyShieldRecharge": 50, "EnergyShieldRechargeDelay": 2}
    assert models.energy_gain("Barrier Invocation", bare, "Boneshatter")[0] == 0.0
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    _, notes, _ = models.energy_model(Inputs(), {}, [SPARK, Skill("Cast on Dodge", group=2, energy=True), comet], SPARK)
    assert "4.6 m each" in notes[0]["basis"] and "up to you" in notes[0]["basis"]  # the guide says it's play-style


def test_every_meta_gem_in_the_game_files_is_handled_or_says_why():
    """Each Meta gem the game files reach (or can't place yet) has an Energy model or a labelled reason: none is dropped
    silently (MECH1 had worked from a hand-kept list of six)."""
    import json
    from pathlib import Path

    exports = sorted((Path(__file__).resolve().parents[1] / "data").glob("game-*/live.json"))
    if not exports:
        pytest.skip("no game-file export; run `python -m builder.data.gamefiles`")
    gems = json.loads(exports[-1].read_text(encoding="utf-8"))["gems"]
    metas = [n for n, e in gems.items() if e["meta"] and (e["status"] in ("live", "live (inferred)", "unresolved")
                                                          or n in models.DEPRECATED)]
    assert len(metas) >= 25
    tables = (models.META_GEMS, models.NO_ENERGY_ON_A_BOSS, models.NOT_MODELED_YET, models.TOTEM_METAS, models.MIRAGE_METAS,
              models.OTHER_METAS, models.DEPRECATED)
    for name in metas:
        assert sum(name in table for table in tables) == 1, name
        modeled, why = models.meta_handling(name)
        assert modeled or why, name


def test_spell_totems_up_follow_the_charges_the_build_makes():
    """Slice 5: Spell Totem's main spell. 3 charges per totem, 8 s each (+20% per Endurance Charge), 10% more damage per
    Power Charge, 1/2/3 totems at gem levels 1/14/23 (game files). The totem's hits make no charges (assumed)."""
    totem = models.Skill("Spell Totem", group=1)
    fireball = models.Skill("Fireball", group=1, spell=True, cast_time=0.75)
    skills = [totem, fireball]
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "EnduranceChargesMax": 3}
    # a Power Charge every 2 s: 0.5/s x 8 s / 3 = 1.33 totems up; 30% more damage
    mods, notes = models.totem_model(skills, stats, ["Gain a Power Charge every 2 seconds"], {"Spell Totem": 20}, fireball)
    by_line = {m["line"]: m["scale"] for m in mods}
    assert by_line["1% more Attack and Cast Speed"] == pytest.approx(33.333, rel=1e-3)
    assert by_line["1% more Damage"] == pytest.approx(30.0)
    assert notes[0]["value"] == pytest.approx(1.33)
    # charges on your hits don't come from the totem's hits: no totem, no damage
    mods, notes = models.totem_model(skills, stats, ["Gain a Power Charge on Critical Hit"], {"Spell Totem": 20}, fireball)
    assert [(m["line"], m["scale"]) for m in mods] == [("1% more Damage", -100.0)]
    # Endurance Charges: longer totems, no damage; gem level 10 allows one
    mods, _ = models.totem_model(skills, stats, ["Gain an Endurance Charge every second"], {"Spell Totem": 10}, fireball)
    assert mods == []  # 1 up (8 x 1.6 s x 1/s / 3, capped at 1): what PoB counts
    mods, _ = models.totem_model(skills, stats, ["Gain an Endurance Charge every second"], {"Spell Totem": 23}, fireball)
    assert {m["line"]: m["scale"] for m in mods}["1% more Attack and Cast Speed"] == pytest.approx(200.0)
    assert models.totems_allowed(13) == 1 and models.totems_allowed(14) == 2 and models.totems_allowed(22) == 2
    # a spell outside a totem: nothing
    assert models.totem_model([fireball], stats, [], {}, fireball) == ([], [])


def test_a_totems_crits_feed_no_meta_energy():
    totem = models.Skill("Spell Totem", group=1)
    fireball = models.Skill("Fireball", group=1, spell=True, cast_time=0.75)
    meta = models.Skill("Cast on Critical", group=2, energy=True)
    comet = models.Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.0)
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50}
    inputs = models.read_inputs([])
    mods, notes, groups = models.energy_model(inputs, stats, [totem, fireball, meta, comet], fireball)
    assert not groups and "cast by a totem" in notes[0]["basis"]
    _, _, groups = models.energy_model(inputs, stats, [fireball, meta, comet], fireball)
    assert groups == {2}


def test_a_snipers_mark_adds_damage_to_one_crit_per_mark():
    """Slice 6: PoB keeps every Mark up and unconsumed. Sniper's Mark is consumed by the next crit (6 s cooldown, game
    files), so its extra crit damage lands on one crit per Mark; casting it (0.5 s) takes time from the main skill."""
    mark = models.Skill("Sniper's Mark", group=2, cast_time=0.5, cooldown=6.0)
    bow = models.Skill("Lightning Arrow", group=1, attack=True)
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50}  # 1 crit/s
    mods, notes = models.mark_model([bow, mark], stats, bow)
    by_line = {m["line"]: m for m in mods}
    assert by_line["1% more Effect of your Curses"]["scale"] == pytest.approx(100 * (1 / 6 - 1), rel=1e-3)
    assert by_line["1% more Effect of your Curses"]["skill"] == "Sniper's Mark"
    assert by_line["1% more Attack and Cast Speed"]["scale"] == pytest.approx(-100 * 0.5 / 6, rel=1e-3)
    assert notes[0]["value"] == pytest.approx(0.17)
    # MECH32: "Enemies you Mark take X% increased Damage" counts while the boss carries the Mark: applied every 6 s, consumed
    # by the next crit 1 s later, so marked 1/6 of the fight (PoB: always Marked)
    lines = [{"source": "tree:Marked for Sickness", "text": "Enemies you Mark take 20% increased Damage"},
             {"source": "item:Ring", "text": "45% increased Critical Hit Chance against Marked Enemies"}]
    mods, notes = models.mark_model([bow, mark], stats, bow, lines)
    by_line = {m["line"]: m for m in mods}
    assert by_line["Enemies you Mark take 1% increased Damage"]["scale"] == pytest.approx(-20 * 5 / 6, rel=1e-3)
    assert by_line["1% increased Critical Hit Chance against Marked Enemies"]["scale"] == pytest.approx(-45 * 5 / 6, rel=1e-3)
    assert "marked 17% of the fight" in notes[-1]["basis"]
    # a crit every 10 s: every crit finds a Mark
    mods, notes = models.mark_model([bow, mark], dict(stats, CritChance=5), bow)
    assert [m["line"] for m in mods] == ["1% more Attack and Cast Speed"] and notes[0]["value"] == 1.0
    # triggered by Called Shots every 5 s: no time lost, the 6 s cooldown still holds
    triggered = models.Skill("Sniper's Mark", group=2, cast_time=0.5, cooldown=6.0, triggered=True)
    mods, _ = models.mark_model([bow, models.Skill("Called Shots", group=2), triggered], stats, bow)
    assert [m["line"] for m in mods] == ["1% more Effect of your Curses"]


def test_voltaic_and_freezing_marks_buff_after_the_bosss_ailment():
    """The buff (10 s) comes when the marked boss is Electrocuted / Frozen: at models.buildup_rate (PoB's buildup per hit x
    hits/s, the Arbiter of Ash's threshold; it was once per 30 s assumed); never if the main skill can't inflict it."""
    mark = models.Skill("Voltaic Mark", group=2, cast_time=0.5, cooldown=0.5)
    spell = models.Skill("Spark", group=1, spell=True)
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "ElectrocuteBuildupAvg": 2}
    every = 1 / models.buildup_rate(stats, "Electrocute")  # (about 47 s: 33 s to fill, x6 for the first 15 s)
    mods, notes = models.mark_model([spell, mark], stats, spell)
    by_line = {m["line"]: m["scale"] for m in mods}
    assert by_line["1% more Effect of your Curses"] == pytest.approx(100 * (10 / every - 1), rel=1e-3)
    assert by_line["1% more Attack and Cast Speed"] == pytest.approx(-100 * 0.5 / 8, rel=1e-3)  # recast every 8 s
    mods, notes = models.mark_model([spell, mark], dict(stats, ElectrocuteBuildupAvg=0), spell)
    assert {m["line"]: m["scale"] for m in mods}["1% more Effect of your Curses"] == pytest.approx(-100)
    assert notes[0]["value"] == 0
    # triggered by Pounce (cooldown 12 s assumed here): marked 8 s of every 12
    pounce = models.Skill("Pounce", group=2, attack=True, cooldown=12.0)
    triggered = models.Skill("Freezing Mark", group=2, cast_time=0.5, cooldown=0.5, triggered=True)
    mods, notes = models.mark_model([spell, pounce, triggered], dict(stats, FreezeBuildupAvg=5), spell)
    rate = models.buildup_rate(dict(stats, FreezeBuildupAvg=5), "Freeze")
    assert notes[0]["value"] == pytest.approx(round(min(1.0, 10 * rate) * 8 / 12, 2))
    assert models.mark_model([spell], stats, spell) == ([], [])


def test_the_skill_you_press_for_a_mark_takes_the_main_skills_time():
    """Pounce (pressed, on its cooldown) and Hand of Chayula (pressed when the Mark is needed) cost their use time;
    Called Shots is automatic."""
    bow = models.Skill("Lightning Arrow", group=1, attack=True)
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50}
    mark = models.Skill("Sniper's Mark", group=2, cast_time=0.5, cooldown=6.0, triggered=True)
    pounce = models.Skill("Pounce", group=2, attack=True, cast_time=1.0, cooldown=10.0)
    mods, notes = models.mark_model([bow, pounce, mark], stats, bow)
    by_line = {m["line"]: m["scale"] for m in mods}
    assert by_line["1% more Attack and Cast Speed"] == pytest.approx(-10.0)  # 1 s every 10 s
    assert by_line["1% more Effect of your Curses"] == pytest.approx(-90.0)  # one crit of the 10 in 10 s
    assert "Pounce" in notes[0]["basis"]
    assert notes[0]["full_dps"] == {"Pounce": pytest.approx(1.0)}  # (on its 10 s cooldown: PoB's own rate)
    hand = models.Skill("Hand of Chayula", group=2, attack=True, cast_time=0.6)
    mods, _ = models.mark_model([bow, hand, mark], stats, bow)
    assert {m["line"]: m["scale"] for m in mods}["1% more Attack and Cast Speed"] == pytest.approx(-10.0)  # 0.6 s / 6 s


def test_a_mark_gem_in_a_disabled_pounce_is_never_applied():
    """MECH35: with no Talisman PoB disables Pounce but still applies a Mark gem socketed in it, read as cast by you. The
    game never applies it: its effect and the "against Marked Enemies" lines count 0, and it takes no time to press."""
    spell = models.Skill("Ice Nova", group=1, spell=True, cast_time=0.8)
    pounce = models.Skill("Pounce", group=2, attack=True, cast_time=1.2, cooldown=5.0)
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 50, "FreezeBuildupAvg": 50}
    marked = [{"text": "Enemies you Mark take 10% increased Damage"}]
    for name in ("Freezing Mark", "Sniper's Mark", "Bloodhound's Mark"):
        mark = models.Skill(name, group=2, cast_time=0.5, cooldown=0.5)
        mods, notes = models.mark_model([spell, mark], stats, spell, marked, disabled=[pounce])
        by_line: dict[str, float] = {}
        for m in mods:
            by_line[m["line"]] = by_line.get(m["line"], 0) + m["scale"]
        assert by_line["1% more Effect of your Curses"] == -100, name
        assert by_line["Enemies you Mark take 1% increased Damage"] == -10, name
        assert "1% more Attack and Cast Speed" not in by_line, name
        assert "Pounce can't be used" in notes[0]["basis"], name
    # a usable Pounce elsewhere doesn't matter; a disabled skill in another group doesn't stop the Mark
    mark = models.Skill("Freezing Mark", group=3, cast_time=0.5, cooldown=0.5)
    mods, _ = models.mark_model([spell, mark], stats, spell, marked, disabled=[pounce])
    assert not any(m["line"] == "1% more Effect of your Curses" and m["scale"] == -100 for m in mods)


def test_predators_mark_counts_you_and_your_minions_near_the_boss():
    """MECH34: Pounce alone triggers Predator's Mark, 3% increased damage taken per enemy of the boss within 6 m, up to
    45% (game files). PoB counts only your minions at their limit, never you, and applies it even when Pounce can't be
    used. A Mark gem in Pounce replaces it."""
    spell = models.Skill("Ice Nova", group=1, spell=True, cast_time=0.8)
    pounce = models.Skill("Pounce", group=2, attack=True, cast_time=1.2, cooldown=5.0)
    predator = models.Skill("Predator's Mark", group=2, triggered=True)
    wolf = models.Skill("Summon Wolf", group=2, triggered=True, minion={"limit": 0})
    stats = {"Speed": 1.0, "HitChance": 100}

    def lines(found, metres):
        mods, notes = models.mark_model(found, stats, spell, [], metres)
        out: dict[str, float] = {}
        for m in mods:
            out[m["line"]] = out.get(m["line"], 0) + m["scale"]
        return out, notes

    by_line, notes = lines([spell, pounce, predator], 2.0)  # you within 6 m, no minions: 3%
    assert by_line["1% more Effect of your Curses"] == -100  # PoB's own value (0 here) out
    assert by_line["Enemies you Mark take 1% increased Damage"] == pytest.approx(3.0)
    assert by_line["1% more Attack and Cast Speed"] == pytest.approx(-100 * 1.2 / 8)  # recast as it runs out
    assert notes[0]["value"] == 3.0 and "PoB 0%" in notes[0]["basis"]
    by_line, _ = lines([spell, pounce, predator], 8.0)  # beyond 6 m: nothing
    assert "Enemies you Mark take 1% increased Damage" not in by_line
    zombies = models.Skill("Raise Zombie", group=3, spell=True, minion={"limit": 20})
    by_line, _ = lines([spell, pounce, predator, zombies], 2.0)  # capped at 45%
    assert by_line["Enemies you Mark take 1% increased Damage"] == pytest.approx(45.0)
    by_line, notes = lines([spell, predator, zombies], 2.0)  # Pounce disabled (wrong weapon): PoB's 45% taken out
    assert by_line == {"1% more Effect of your Curses": -100} and notes[0]["value"] == 0
    freezing = models.Skill("Freezing Mark", group=2, cast_time=0.5, cooldown=0.5, triggered=True)
    _, notes = lines([spell, pounce, predator, freezing], 2.0)
    assert notes[0]["label"].startswith("Freezing Mark")
    # MECH35 (the user, 11 Nov: "Count them, labelled"): Pounce wolves carried in at Summon Wolf's limit (6 at gem
    # level 20, game files), which PoB counts as 0; with a play note. No Pounce, no wolves.
    wolf6 = models.Skill("Summon Wolf", group=2, triggered=True, minion={"limit": 0},
                         level_stats={"number_of_wolves_allowed": 6})
    by_line, notes = lines([spell, pounce, predator, wolf6], 2.0)
    assert by_line["Enemies you Mark take 1% increased Damage"] == pytest.approx(3.0 * 7)
    assert "6 Pounce wolves*" in notes[0]["basis"] and "wolves alive" in notes[0]["play"]
    # MECH36 (the user, 11 Nov: "Yes, count it labelled", "Yes, they follow"): their own hits at the carried count
    # (PoB's Full DPS counts one Wolf Companion), and Pounce's slam at one per Mark (8 s) where PoB counts one per
    # cooldown (5 s): Full DPS entries counted x these (Objective.entry_scales)
    assert notes[0]["full_dps"] == {"Summon Wolf": 6, "Pounce": pytest.approx(5 / 8)}
    assert "MEDIUM" in notes[0]["basis"] and "they follow you" in notes[0]["basis"] and "counted once per 8.0 s" in notes[0]["basis"]
    _, notes = lines([spell, pounce, predator], 2.0)  # no wolves: the slam only
    assert notes[0]["full_dps"] == {"Pounce": pytest.approx(5 / 8)}
    # Cross Slash consumes the Mark (every hit): Pounce is recast on its cooldown (5 s), never sooner (MECH36: it went
    # under the cooldown), so marked 1 s of every 5 and the slam at PoB's own rate
    slash = models.Skill("Cross Slash", group=1, attack=True, melee=True)
    _, notes = models.mark_model([slash, pounce, predator], stats, slash, [], 2.0)
    assert notes[0]["full_dps"] == {"Pounce": pytest.approx(1.0)} and "held 1.0 s" in notes[0]["basis"]
    assert notes[0]["value"] == pytest.approx(round(3.0 * 1.0 / 5.0, 1))
    by_line, notes = lines([spell, pounce, predator, wolf6, zombies], 2.0)  # still capped at 45%
    assert by_line["Enemies you Mark take 1% increased Damage"] == pytest.approx(45.0)
    by_line, _ = lines([spell, pounce, predator, wolf], 8.0)  # no level stats: the level-1 limit, 4 (you beyond 6 m)
    assert by_line["Enemies you Mark take 1% increased Damage"] == pytest.approx(12.0)
    _, notes = lines([spell, predator, wolf6], 2.0)
    assert notes[0]["value"] == 0 and "play" not in notes[0] and "full_dps" not in notes[0]  # (Pounce can't be used)


def test_a_mirage_uses_its_attack_for_the_share_of_time_it_is_up():
    """Slice 7: one Mirage per 10 s (the spawn skills' cooldown, shared by both: game files), lasting 5000 / 4000 ms + 50
    per gem level and per quality %. PoB counts the socketed Attack as always on: scaled to the share. Mirage Archer needs
    one dodge roll (0.75 s) per Mirage unless the build already rolls for Cast on Dodge."""
    shot = models.Skill("Lightning Arrow", group=1, attack=True)
    archer, ice = models.Skill("Mirage Archer", group=2), models.Skill("Ice Shot", group=2, attack=True)
    mods, notes, groups = models.mirage_model([shot, archer, ice], shot, {"Mirage Archer": (20, 20)})
    assert groups == {2} and notes[0]["value"] == 0.7  # (6 s + 1 s of quality) / 10 s
    assert mods[0]["skill"] == "Ice Shot" and abs(mods[0]["scale"] + 30) < 1e-6
    assert len(mods) == 2 and "dodge rolling" in notes[1]["label"]
    assert len(models.mirage_model([shot, archer, ice], shot, {"Mirage Archer": (20, 20)}, rolling=True)[0]) == 1
    eye, rain = models.Skill("Mirage Deadeye", group=3), models.Skill("Rain of Arrows", group=3, attack=True)
    mods, notes, groups = models.mirage_model([shot, archer, ice, eye, rain], shot,
                                              {"Mirage Archer": (20, 0), "Mirage Deadeye": (20, 0)})
    assert groups == {3} and [n["value"] for n in notes] == [0.5, 0.0]  # Deadeye's takes the shared cooldown
    spell = models.Skill("Fireball", group=1, spell=True)
    assert models.mirage_model([spell, eye, rain], spell, {"Mirage Deadeye": (20, 0)})[2] == set()


def test_ferocious_roar_keeps_rage_on_average_and_breaks_armour_with_enough_per_hit():
    """Slice 7: a roar every 8 s gains 2 Rage per boss Power (40, capped at 30), held 4 s, then 5 lost per second: 25 on
    average (game files: 1% more Attack damage each). Two Empowered hits (20 Power / 8) Fully Break the boss's Armour if
    their Break covers it, for 12 s. A socketed Warcry replaces its own effects (PoB prices that as a support)."""
    assert models.rage_average(40, 30, 8.0) == 25.0
    assert models.rage_average(40, 30, 4.0) == 30.0
    assert models.rage_average(10, 30, 8.0) == pytest.approx((10 * 4 + 10 * 2 - 5 * 4 / 2) / 8)  # (gone after 2 s)
    slam = models.Skill("Furious Slam", group=1, attack=True)
    roar = models.Skill("Ferocious Roar", group=2, cast_time=1.1, cooldown=8.0,
                        level_stats={"bear_warcry_gain_X_centirage_per_enemy_power": 200,
                                     "warcry_grant_break_X_armour_on_hit": 6147})
    mods, notes = models.roar_model([slam, roar], {"MaximumRage": 30}, slam)
    assert [n["value"] for n in notes] == [25.0, round(1.1 / 8, 3)]
    assert mods[0]["line"] == "1% more Attack Damage" and mods[0]["scale"] == 25.0
    assert mods[1]["skill"] == "Furious Slam" and mods[1]["scale"] == pytest.approx(-100 * 1.1 / 8)
    assert len(models.roar_model([slam, roar], {"Rage": 30, "MaximumRage": 30}, slam)[0]) == 1  # (PoB counts Rage)
    assert models.roar_break(roar, 8063)[0] == 1.0 and models.roar_break(roar, 12295)[0] == 0.0
    shout = models.Skill("Infernal Cry", group=2, triggered=True)
    assert models.roar([slam, roar, shout]) == (roar, True) and models.roar_model([slam, roar, shout], {}, slam) == ([], [])
    assert models.roar([slam, models.Skill("Ferocious Roar", group=2, wrong_weapon=True)]) == (None, False)  # (no Talisman)


def test_skills_that_need_corpses_use_none_on_a_single_boss_whatever_triggers_them():
    detonate = models.Skill("Detonate Dead", group=2, spell=True, triggered=True, corpses=True)
    unearth = models.Skill("Unearth", group=3, spell=True, corpses=True)
    fireball = models.Skill("Fireball", group=2, spell=True, triggered=True)
    given = models.trigger_rate("Detonate Dead", 1.5) + models.trigger_rate("Fireball", 1.5)
    mods, notes = models.corpse_model([detonate, unearth, fireball], given)
    rates = {m["skill"]: m["value"] for m in mods if m["key"] == "triggerRate"}
    assert rates == {"Detonate Dead": 0, "Fireball": 1.5}  # the meta's rate replaced, the other spell's kept
    assert [n["label"] for n in notes] == ["Unearth: corpses", "Corpses per second for Detonate Dead"]
    ritual = models.Skill("Ritual Sacrifice", group=4, corpses=True)
    mods, notes = models.corpse_model([ritual], [])
    assert mods == [] and "20% of maximum Life" in notes[0]["basis"]  # its Life option needs no corpse
    sacrifice = models.Skill("Sacrifice")
    _, notes = models.corpse_model([detonate, sacrifice], [])
    assert "Sacrifice" in notes[0]["basis"] and notes[0]["confidence"] == "theory"
    assert models.corpse_model([fireball], given) == (given, [])


def test_hollow_form_images_follow_the_power_charges_the_build_makes():
    """Slice 8: Hollow Form's images use the socketed melee Attack; a use that consumes a Power Charge makes 2 more
    images (game files, poe2db). One image per use without one (assumed), which is PoB's number."""
    hollow = models.Skill("Hollow Form", group=2, attack=True, melee=True)
    strike = models.Skill("Falling Thunder", group=2, attack=True, melee=True)
    skills = [hollow, strike]
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3}
    # a Power Charge every 2 s against 2 uses a second: a quarter of uses make 3 images, 1.5 on average
    mods, notes = models.hollow_model(skills, stats, ["Gain a Power Charge every 2 seconds"], strike)
    assert [(m["line"], m["scale"]) for m in mods] == [("1% more Damage", pytest.approx(50.0))]
    assert notes[0]["value"] == pytest.approx(1.5)
    # an extra Power Charge on every gain (chance_to_gain_1_more_power_charge_% at 100): 1 per s, half the uses
    mods, _ = models.hollow_model(skills, stats, ["Gain a Power Charge every 2 seconds",
                                                  "Gain an additional Power Charge when you gain a Power Charge"], strike)
    assert mods[0]["scale"] == pytest.approx(100.0)
    # every use finds one (a charge on every crit at 50%, capped at all uses): 3 images
    mods, _ = models.hollow_model(skills, stats, ["Gain a Power Charge on Critical Hit"] * 3, strike)
    assert mods[0]["scale"] == pytest.approx(200.0)
    # no Power Charges, or no room for them: PoB's one image
    mods, notes = models.hollow_model(skills, stats, [], strike)
    assert mods == [] and notes[0]["value"] == 1
    assert models.hollow_model(skills, {**stats, "PowerChargesMax": 0}, ["Gain a Power Charge every 2 seconds"], strike)[0] == []
    # the Attack outside Hollow Form: nothing
    assert models.hollow_model([models.Skill("Hollow Form", group=1), strike], stats, [], strike) == ([], [])


def test_cast_on_charm_use_gains_energy_from_the_charm_charges_spent():
    """Slice 8 (MECH11): Bijouborne's Cast on Charm Use gains 2 Energy per charm charge used (game files). A charm is
    used as its generated charges allow, never again while its effect lasts, and never when it waits for a kill."""
    from builder.search.flasks import Charges, charm_charges_used

    gain = Charges(charm=1.0, boss_hits=1.0, boss_types=("Cold",))  # (a Frozen trigger needs the boss's Cold: MECHC2)
    charm = {"kind": "Charm", "chargesUsed": 20, "duration": 4.0, "gainBase": 0}
    # 1 charge/s pays for a use every 20 s (shorter than its 4 s effect): 1 charge spent per second
    spent, basis = charm_charges_used([(charm, "Rarity: Magic\nThawing Charm\nUsed when you become Frozen")], gain)
    assert spent == pytest.approx(1.0) and "Thawing Charm" in basis
    # 10 charges/s would pay for a use every 2 s, but the effect lasts 4 s: one use per 4 s, 5 charges/s
    assert charm_charges_used([(charm, "Rarity: Magic\nCharm")], Charges(charm=10.0))[0] == pytest.approx(5.0)
    # ... and never more often than its trigger: Frozen by the boss once per 10 s (LOW), so 20 charges per 10 s (MECHC2)
    assert charm_charges_used([(charm, "Rarity: Magic\nThawing Charm\nUsed when you become Frozen")],
                              Charges(charm=10.0, boss_hits=1.0, boss_types=("Cold",)))[0] == pytest.approx(2.0)
    assert charm_charges_used([(charm, "Rarity: Magic\nGolden Charm\nUsed when you Kill a Rare or Unique Enemy")], gain)[0] == 0
    energy, basis = models.energy_gain("Cast on Charm Use", {"_charm_charges": 1.5, "_charm_basis": "x"}, "Spark")
    assert energy == pytest.approx(3.0) and "2 Energy per charge" in basis
    assert models.energy_gain("Cast on Charm Use", {}, "Spark")[0] == 0.0


def test_cast_on_minion_death_gains_energy_only_from_minions_the_build_kills():
    """MECH45 (the user, 11 Nov: no flat "one dies every 10 s"): Cast on Minion Death gains 50 Energy per Persistent minion
    Killed (expired or despawned ones aren't Killed: KeywordPopups MinionDeath), at the rate the build's own sources kill
    them, capped by Reviving (7.5 s after the last death); nothing that kills them: 0, with a note."""
    zombies = models.Skill("Raise Zombie", summon={"limit": 6, "duration": 0, "persistent": True, "undead": True})
    spirits = models.Skill("Raging Spirits", summon={"limit": 20, "duration": 8, "persistent": False})
    main = models.Skill("Detonate Dead", group=1, spell=True, corpses=True)
    # minions, but nothing kills them: 0 and the note says so
    rate, how = models.minion_deaths([zombies, spirits, main], main, {"Speed": 2.0})
    assert rate == 0.0 and "nothing in this build kills its minions" in how
    # only temporary minions: none Persistent to be Killed
    assert models.minion_deaths([spirits, main], main, {"Speed": 2.0})[0] == 0.0
    # Sacrifice: each corpse use takes a zombie: 6 x 2 / (6 + 7.5 x 2) = 0.571/s
    sacrifice = models.Skill("Sacrifice")
    rate, how = models.minion_deaths([zombies, sacrifice, main], main, {"Speed": 2.0})
    assert rate == pytest.approx(6 * 2 / (6 + 7.5 * 2)) and "UNVERIFIED" in how
    energy, basis = models.energy_gain("Cast on Minion Death", {"_minion_deaths": (rate, how)}, "Detonate Dead")
    assert energy == pytest.approx(50 * rate) and "100% relative Power assumed" in basis
    assert models.energy_gain("Cast on Minion Death", {"_minion_deaths": (0.0, "x")}, "Spark")[0] == 0.0
    assert models.energy_gain("Cast on Minion Death", {}, "Spark")[0] == 0.0
    # Explosive Demise: one of the other minions every 3 s / cooldown recovery
    arsonist = models.Skill("Skeletal Arsonist Minion", cooldown_recovery=1.5,
                            summon={"limit": 2, "duration": 0, "persistent": True, "undead": True})
    spark = models.Skill("Spark", group=1, spell=True)
    rate, how = models.minion_deaths([zombies, arsonist, spark], spark, {"Speed": 2.0})
    kills = 1.5 / 3
    assert rate == pytest.approx(8 * kills / (8 + 7.5 * kills)) and "Explosive Demise" in how
    assert models.minion_deaths([arsonist, spark], spark, {"Speed": 2.0})[0] == 0.0  # (no other minion to detonate)
    assert "Cast on Minion Death" not in models.NO_ENERGY_ON_A_BOSS
    groups = [{"gems": [{"name": "Raise Zombie", "support": False, "enabled": True},
                        {"name": "Spark", "support": False, "enabled": True},
                        {"name": "Minion Instability", "support": True, "enabled": True}]}]
    gems = {"Raise Zombie": {"skillTypes": ["Minion", "CreatesMinion"]}, "Spark": {"skillTypes": ["Spell"]},
            "Minion Instability": {"skillTypes": ["Minion"]}}
    assert models.minion_skill_names(groups, gems) == ["Raise Zombie"]


def test_spirit_vessel_notes_name_the_copied_skills_and_the_assumptions():
    skills = [{"name": "Spirit Vessel", "group": 3, "disabled": False},
              {"name": "Cross Slash", "group": 3, "disabled": True}, {"name": "Maul", "group": 3, "disabled": True},
              {"name": "Boneshatter", "group": 1, "disabled": False}]
    notes = models.spirit_vessel_notes(skills)
    assert len(notes) == 1 and notes[0]["value"] == 2 and "Cross Slash, Maul" in notes[0]["label"]
    assert "ASSUMED" in notes[0]["basis"] and notes[0]["confidence"] == "theory"
    assert models.spirit_vessel_notes(skills[:1] + skills[3:]) == []  # an empty vessel does nothing: no note


def test_supports_in_item_groups_count_as_used():
    """A lineage support once per character, item-granted groups included: gate 4's 2b75b held Rakiata's Flow both in its
    main group and in the Absent Amulet's Cast on Dodge group (PoB: too many Rakiata's Flow lineage supports)."""
    from builder.search.gems import Gems

    def group(index, from_item, active, *supports):
        return {"index": index, "fromItem": from_item, "gems": [{"name": active, "support": False}]
                + [{"name": n, "support": True} for n in supports]}

    groups = [group(1, False, "Explosive Transmutation", "Concentrated Area"),
              group(2, True, "Cast on Dodge", "Rakiata's Flow")]
    # (0.5's rule: only a lineage support, like Rakiata's Flow, and the global-benefit ones are limited; a normal one,
    # like Concentrated Area, may sit in every group)
    from types import SimpleNamespace

    gems = SimpleNamespace(copies_allowed=lambda name: 1 if name == "Rakiata's Flow" else None, ctx=SimpleNamespace(level=90))
    assert Gems.used_supports(gems, groups, 1) == {"Rakiata's Flow"}
    assert Gems.used_supports(gems, groups, 2) == set()


def test_explosive_transmutation_counts_every_remnant_source_the_build_has():
    """Every Remnant source the build has feeds the explosion (assumptions.REMNANT_SOURCES), not one: Lingering
    Illusion's copies, Grim Feast at the build's own minion kills, Storm's Recollection collecting each twice (the user's correction,
    8 Nov: Into the Breach is not the only source on a lone boss)."""
    from builder.search import assumptions
    from builder.search.assumptions import REMNANT_SOURCES, Assumptions

    def skill(name, **extra):
        return {"name": name, "disabled": False, **extra}

    def run(skills, lines=()):
        class Build:
            def call(self, method, **kw):
                return {"activeSkills": {"skills": skills}, "sourceLines": {"lines": [{"text": t} for t in lines]},
                        "stats": {}}[method]

            def main_group(self):
                return None

        a = Assumptions(Build())
        a.models = lambda skills: ([], [], {})
        a.ledger_conditions = lambda skills: ([], [])
        mods, notes = a.wanted()
        return next(m["value"] for m in mods if m.get("key") == "triggerRate"), notes

    explosion = skill("Transmutation Explosion", cooldown=0.0)
    breach, illusion = REMNANT_SOURCES["Into the Breach"][0], REMNANT_SOURCES["Lingering Illusion"][0]
    rate, notes = run([explosion, skill("Into the Breach"), skill("Lingering Illusion")])
    assert rate == pytest.approx(breach + illusion)
    assert "Lingering Illusion" in notes[-1]["basis"]
    rate, _ = run([explosion, skill("Grim Feast")])
    assert rate == 0.0  # no minions: no deaths to make Remnants
    zombies = skill("Raise Zombie", minions=True, summon={"limit": 6, "duration": 0, "persistent": True, "undead": True})
    rate, notes = run([explosion, skill("Grim Feast"), zombies])
    assert rate == 0.0 and "nothing in this build kills its minions" in notes[-1]["basis"]  # (MECH45: no flat 10 s)
    arsonist = skill("Skeletal Arsonist Minion", minions=True,
                     summon={"limit": 2, "duration": 0, "persistent": True, "undead": True})
    rate, _ = run([explosion, skill("Grim Feast"), zombies, arsonist])  # Explosive Demise every 3 s
    assert rate == pytest.approx(8 * (1 / 3) / (8 + 7.5 / 3))
    rate, notes = run([explosion, skill("Into the Breach")],
                      ["Remnants you create reappear once, 1 second after being collected"])
    assert rate == pytest.approx(2 * breach) and "twice" in notes[-1]["basis"]
    assert assumptions.REMNANT_SOURCES["Siphon Elements"][0] == 0.0  # listed, labelled, not counted yet
    # the user's rate (9 Nov): a dodge-roll copy destroyed every 5 s, 1.76 Power Charge Remnants each
    assert REMNANT_SOURCES["Lingering Illusion"][0] == pytest.approx(1.76 / 5)


def test_infusion_supply_counts_each_element_from_the_build_s_sources():
    """Elemental Infusions per second by element (assumptions.infusion_supply): Frost Bomb Cold with its cooldown
    recovery, Orb of Storms Lightning, Snap split between the ailments the build keeps up, Elemental Equilibrium's shift,
    extra Infusion Remnants and Infusions, another different Infusion, and timed Verisium Infusions (any element)."""
    from builder.search.assumptions import REMNANT_SOURCES, infusion_supply

    bomb, orb, snap = REMNANT_SOURCES["Frost Bomb"][0], REMNANT_SOURCES["Orb of Storms"][0], REMNANT_SOURCES["Snap"][0]
    present = {"Frost Bomb": {"cooldownRecovery": 1.5}, "Orb of Storms": {}, "Snap": {}, "Siphon Elements": {}}
    supply, used = infusion_supply(present, [], {"conditionEnemyIgnited", "conditionEnemyShocked"})
    assert supply["Cold"] == pytest.approx(bomb * 1.5)
    assert supply["Lightning"] == pytest.approx(orb + snap / 2) and supply["Fire"] == pytest.approx(snap / 2)
    assert any("Siphon Elements" in u and "undercount" in u for u in used)
    assert infusion_supply({"Snap": {}}, [], set())[0]["Fire"] == 0  # no ailment to consume
    # each ailment at its share of the fight (MECH36): Snap finds one when either is up (1 - 0.5 x 0.75), split by uptime
    part, used = infusion_supply({"Snap": {}}, [], {"conditionEnemyIgnited": 0.5, "conditionEnemyShocked": 0.25})
    assert part["Fire"] == pytest.approx(snap * 0.625 * 2 / 3) and part["Lightning"] == pytest.approx(snap * 0.625 / 3)
    assert "Fire 50% of the fight" in used[0] and "62% of its casts" in used[0]
    assert infusion_supply({"Snap": {}}, [], {"conditionEnemyIgnited": 0.0})[0]["Fire"] == 0
    lines = ["Create Lightning Infusion Remnants instead of Fire", "50% chance to spawn an additional Infusion Remnant",
             "20% chance to gain an additional Infusion", "40% chance when collecting an Elemental Infusion to gain another "
             "different Elemental Infusion", "Every 5 seconds, gain a Verisium Infusion"]
    supply, used = infusion_supply({"Living Bomb": {}}, lines, set(), bomb=(0.2, "living_bomb_rate"))
    fire = 0.2 * 1.5 * 1.2  # made as Lightning (Equilibrium)
    assert supply["Lightning"] == pytest.approx(fire)
    assert supply["Fire"] == pytest.approx(fire * 0.2) and supply["Cold"] == pytest.approx(fire * 0.2)
    assert supply["Any"] == pytest.approx(1.2 / 5)
    assert any("the user's word" in u for u in used)  # Refracted Infusion's random other element: the user's, 9 Nov


def test_remnant_rates_from_freezes_stuns_and_doubled_pickups(monkeypatch):
    """Game files, build 26476662: Remnants of Kalguur at most one per 3 s (ward_remnants_spawn_remnant_on_stun_cooldown_ms
    3000; uncapped it was an overcount); Siphon Elements a Cold Remnant per Freeze (5% per Power, a boss's 20 Power), the
    Freezes from models.buildup_rate; "chance for Remnants you pick up to count as picking up an additional Remnant"
    (created_remnants_have_%_chance_to_duplicate_pick_up_results) multiplies both the explosion's Remnants and Infusions."""
    from builder.search import assumptions
    from builder.search.assumptions import REMNANT_SOURCES, infusion_supply, kalguur_rate, siphon_freezes

    assert kalguur_rate(0.0) == 0.0
    assert kalguur_rate(0.01) == pytest.approx(0.005, rel=0.02)  # rare stuns: 50% of them
    assert kalguur_rate(100.0) < 1 / 3 and kalguur_rate(100.0) == pytest.approx(1 / 3, rel=0.01)  # the cap
    monkeypatch.setattr(assumptions.models, "buildup_rate", lambda stats, ailment: 0.2 if ailment == "Freeze" else 0.0)
    rate, how = siphon_freezes({})
    assert rate == pytest.approx(0.2) and "20 Power" in how
    supply, used = infusion_supply({"Siphon Elements": {}}, [], set(), (rate, how))
    assert supply["Cold"] == pytest.approx(0.2) and any("Fire and Lightning" in u for u in used)
    assert infusion_supply({}, [], set(), (rate, how))[0]["Cold"] == 0.0  # only with the skill
    lines = ["20% chance for Remnants you pick up to count as picking up an additional"]  # (PoB's line, split)
    supply, _ = infusion_supply({"Orb of Storms": {}}, lines, set())
    assert supply["Lightning"] == pytest.approx(REMNANT_SOURCES["Orb of Storms"][0] * 1.2)

    class Build:
        def call(self, method, **kw):
            return {"activeSkills": {"skills": [{"name": "Transmutation Explosion", "disabled": False, "cooldown": 0.0},
                                                {"name": "Siphon Elements", "disabled": False}]},
                    "sourceLines": {"lines": [{"text": "15% chance for Remnants you create to grant their effects twice"}]},
                    "stats": {}}[method]

        def main_group(self):
            return None

    a = assumptions.Assumptions(Build())
    a.models = lambda skills: ([], [], {})
    a.ledger_conditions = lambda skills: ([], [])
    mods, notes = a.wanted()
    assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == pytest.approx(0.2 * 1.15)
    assert "15% of pickups counting twice" in notes[-1]["basis"]


def test_glory_skills_are_used_as_often_as_the_builds_own_glory_pays_for():
    """Game files (GrantedEffectStatSets, 9 Nov): Hammer of the Gods costs 20 Glory and gains 100% of the boss's Power
    (20) per Heavy Stun; a Banner costs 200 and gains 20 per attack hit; Zerphi's Infamy turns 5% of Life spent into
    Glory against 50 per use. The ledger's GlorySkill lines (one use per 6 s) count only at that rate or faster."""
    slam = models.Skill("Boneshatter", attack=True, melee=True)
    stats = {"Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000, "HeavyStunBuildupAvg": 10.0}
    stuns = models.heavy_stun_rate(stats)[0]
    # A skill gains Glory once per 0.5 s per monster (KeywordPopups Glory; MECHC2): E events/s give E / (1 + 0.5 E) gains
    assert models.glory_uses(["Hammer of the Gods"], stats, slam)[0] == pytest.approx(stuns / (1 + 0.5 * stuns))
    assert models.glory_uses(["War Banner"], stats, slam)[0] == pytest.approx(2.0 / 2.0 * 20 / 200)  # one per 10 s
    fast = models.glory_uses(["War Banner"], {**stats, "Speed": 6.0}, slam)
    assert fast[0] == pytest.approx(6.0 / 4.0 * 20 / 200) and "one gain per 0.5 s" in fast[1]  # was one per 1.7 s
    assert fast[0] < 2.0 * 20 / 200  # never more than two gains a second
    spell = models.Skill("Spark", spell=True)
    uses, how = models.glory_uses(["War Banner"], {**stats, "PhysicalHitAverage": 0, "LightningHitAverage": 1000}, spell)
    assert uses == 0 and "never" in how  # a spell makes no attack hits
    assert models.glory_uses(["Zerphi's Infamy"], {"LifePerSecondCost": 100}, slam)[0] == pytest.approx(5 / 50)
    assert models.glory_uses(["Skyfall"], stats, slam) == (0.0, "Skyfall: no source counted, never")
    assert models.glory_uses(["Spark"], stats, spell) == (0.0, "no Glory skill")


def test_freezes_electrocutes_and_stuns_of_the_boss_are_rated_from_the_build_not_one_per_30_s():
    """Charges on freeze / electrocute / pin / stun and Voltaic / Freezing Mark's buff took one such event per 30 s
    (assumed). Now Freeze and Electrocute come from PoB's buildup per hit x hits/s at the Arbiter of Ash's threshold,
    stuns from heavy_stun_rate; Pins from PoB's Pin buildup (MECH46)."""
    from builder.search.conditions import _charge_rate

    stats = {"Speed": 2.0, "HitChance": 100, "FreezeBuildupAvg": 10.0, "ColdHitAverage": 100.0}
    freeze = models.buildup_rate(stats, "Freeze")
    t = 1 / (2.0 * 0.10 * models.AILMENT_BOSS)  # (6.6 s to fill; x6 = 39.5 s, past the 14 s window)
    # 4 s Frozen (buildup paused: the user, 11 Nov), then the x6 bar for the rest of the 14 s window
    assert freeze == pytest.approx(1 / (4.0 + 10.0 + (1 - 10.0 / (6 * t)) * t))
    fast = dict(stats, Speed=40.0)  # (0.33 s to fill: x6 = 2 s, inside the window)
    assert models.buildup_rate(fast, "Freeze") == pytest.approx(1 / (4.0 + 6 / (40.0 * 0.10 * models.AILMENT_BOSS)))
    rate, how = _charge_rate("50% chance to gain a Power Charge when you Freeze an enemy", "Power", stats)
    assert rate == pytest.approx(0.5 * freeze) and "Freeze" in how
    rate, how = _charge_rate("Gain a Frenzy Charge when you Pin an enemy", "Frenzy", stats)
    assert rate == 0.0  # no Pin source: no Pins (MECH46, FLAT_RATES A2: was one per 30 s for every build)
    pinning = dict(stats, PinBuildupAvg=10.0)  # (3 s Pinned, x6 for 13 s after each)
    rate, how = _charge_rate("Gain a Frenzy Charge when you Pin an enemy", "Frenzy", pinning)
    assert rate == pytest.approx(1 / (3.0 + 10.0 + (1 - 10.0 / (6 * t)) * t)) and "Pin" in how
    rate, _ = _charge_rate("Gain an Endurance Charge when you Heavy Stun an enemy", "Endurance", stats)
    assert rate == 0.0  # (all Cold: builds no Heavy Stun)
    mark = Skill(name="Freezing Mark", cast_time=0.5)
    _, notes = models.mark_model([mark], stats, None)
    uptime = next(n for n in notes if n["label"] == "Freezing Mark: buff uptime")
    assert f"once per {1 / freeze:.1f} s" in uptime["basis"]


def test_a_curse_is_up_by_how_the_build_applies_it():
    """MECH33: cast by hand it is recast each 7.4 s (game files) and costs the main skill its cast time; through
    Blasphemy it is up while the boss stands in your Presence; PoB counts every curse always up."""
    gems = {"Elemental Weakness": {"skillTypes": ["Spell", "AppliesCurse"]}, "Blasphemy": {"skillTypes": ["Aura"]},
            "Cast on Critical": {"skillTypes": ["Meta"]}, "Temporal Chains": {"skillTypes": ["AppliesCurse"]}}
    stats = {"PresenceRadius": 40, "Speed": 2.0, "HitChance": 100}
    hand = [{"enabled": True, "gems": [{"name": "Elemental Weakness", "support": False}]}]
    aura = [{"enabled": True, "gems": [{"name": "Temporal Chains", "support": False},
                                       {"name": "Blasphemy", "support": True}]}]
    (use,) = models.curse_uses(hand, gems, stats, 8.0)
    assert use.share == 1.0 and use.by_hand
    (once,) = models.curse_uses(hand, gems, stats, 8.0, ["Curses you inflict have infinite Duration"])
    assert once.share == 1.0 and not once.by_hand  # cast once, no recasts (MECH42)
    assert models.curse_uses(aura, gems, stats, 4.0)[0].share == 1.0  # inside 4 m of Presence
    assert models.curse_uses(aura, gems, stats, 8.0)[0].share == 0.0  # outside it
    assert models.curse_uses([{**aura[0], "enabled": False}], gems, stats, 4.0) == []
    meta = [{"gems": [{"name": "Cast on Critical", "support": False}, {"name": "Elemental Weakness", "support": False}]}]
    assert "triggered" in models.curse_uses(meta, gems, stats, 8.0)[0].how
    (hit,) = models.curse_uses([], gems, stats, 8.0, ["Curse Non-Cursed Enemies with Enfeeble on Hit"])
    assert hit.name == "Enfeeble" and hit.share == 1.0

    bow = models.Skill("Lightning Arrow", group=1, attack=True)
    weakness = models.Skill("Elemental Weakness", group=2, spell=True, cast_time=0.7)
    lines = [{"text": "Enemies you Curse take 10% increased Damage"}]
    mods, notes = models.curse_model([use], [bow, weakness], bow, lines)
    by_line = {m["line"]: m["scale"] for m in mods}
    assert by_line["1% more Attack and Cast Speed"] == pytest.approx(-100 * 0.7 / 7.4, rel=1e-3)
    assert "Enemies you Curse take 1% increased Damage" not in by_line  # cursed all fight
    chains = models.Skill("Temporal Chains", group=3, spell=True)
    outside = models.curse_uses(aura, gems, stats, 8.0)
    mods, notes = models.curse_model(outside, [bow, chains], bow, lines)
    by_line = {m["line"]: m["scale"] for m in mods}
    assert by_line["1% more Effect of your Curses"] == pytest.approx(-100.0)
    assert by_line["Enemies you Curse take 1% increased Damage"] == pytest.approx(-10.0)
    assert "1% more Attack and Cast Speed" not in by_line  # an Aura isn't cast
    # a Mark alone: PoB sets the enemy Cursed, but nothing curses it
    mods, notes = models.curse_model([], [bow], bow, lines)
    assert {m["line"]: m["scale"] for m in mods}["Enemies you Curse take 1% increased Damage"] == pytest.approx(-10.0)


# MECH37: a pressed non-main skill in Full DPS counts at its real use ------------------------------------------------

def test_press_model_counts_a_pressed_skill_at_its_cooldown_and_shrinks_the_main_skill():
    rates = [{"index": 1, "name": "Main", "speed": 2.0, "repeats": 1, "castTime": 1.0},
             {"index": 2, "name": "Bomb", "speed": 0.25, "cooldown": 4.0, "repeats": 1, "castTime": 0.5}]
    scales, notes = models.press_model(rates, 1, {2})
    # PoB's speed is already capped at the cooldown: the skill keeps PoB's count; its uses take 0.5 s each (its base
    # cast time, as the cap hides its own speed) every 4 s from the main skill
    assert scales["Bomb"] == pytest.approx(1.0)
    assert scales["Main"] == pytest.approx(1 - 0.25 * 0.5)
    assert all(n["label"].endswith("*") for n in notes)


def test_press_model_counts_a_kept_up_skill_once_per_its_duration():
    rates = [{"index": 1, "name": "Main", "speed": 2.0}, {"index": 3, "name": "Debuff", "speed": 1.6, "duration": 8.0}]
    scales, notes = models.press_model(rates, 1, {3})
    assert scales["Debuff"] == pytest.approx(1 / 8 / 1.6)
    assert scales["Main"] == pytest.approx(1 - 1 / 8 / 1.6)  # (one use every 8 s, 1/1.6 s each)
    assert notes[0]["confidence"] == "MEDIUM"


def test_press_model_falls_back_to_a_labelled_guess_and_leaves_unpressed_groups():
    rates = [{"index": 1, "name": "Main", "speed": 2.0}, {"index": 2, "name": "Slash", "speed": 1.0},
             {"index": 4, "name": "Triggered", "speed": 3.0}]
    scales, notes = models.press_model(rates, 1, {2})
    assert scales["Slash"] == pytest.approx(1 / models.PRESS_EVERY)
    assert "Triggered" not in scales
    assert notes[0]["confidence"] == "LOW" and "guess" in notes[0]["basis"]
    assert models.press_model(rates, 1, set()) == ({}, [])


def test_pressed_group_tells_pressed_from_triggered_totem_and_mark_carrier_groups():
    from builder.search.assumptions import pressed_group
    gems = {"Frost Bomb": {"skillTypes": ["Spell", "Cooldown"], "support": False},
            "Cast on Critical": {"skillTypes": ["Meta", "GeneratesEnergy", "Persistent"], "support": False},
            "Spell Totem": {"skillTypes": ["Meta", "SummonsTotem"], "support": False},
            "Pounce": {"skillTypes": ["Meta", "Attack", "Slam"], "support": False},
            "Herald of Ice": {"skillTypes": ["Persistent", "Buff"], "support": False}}

    def group(*names, full=True, item=False):
        return {"enabled": True, "fullDPS": full, "fromItem": item,
                "gems": [{"name": n, "enabled": True, "support": False} for n in names]}

    assert pressed_group(group("Frost Bomb"), gems)
    assert not pressed_group(group("Frost Bomb", full=False), gems)
    assert not pressed_group(group("Frost Bomb", item=True), gems)
    assert not pressed_group(group("Cast on Critical", "Frost Bomb"), gems)
    assert not pressed_group(group("Spell Totem", "Frost Bomb"), gems)
    assert not pressed_group(group("Pounce"), gems)
    assert not pressed_group(group("Herald of Ice"), gems)


def test_press_model_takes_the_uncapped_use_time_and_scales_ailments_by_the_stacks_the_presses_keep_up():
    """MECH37 turn 2: where the cooldown caps PoB's speed, a press takes PoB's own use time (Time/HitTime); a pressed
    skill's ailment entries (its source) count at min(stacks x real/nonstop, the most that count), as PoB counts them,
    and the main skill's ailments shrink with its time."""
    rates = [{"index": 1, "name": "Main", "speed": 2.0, "stackPotential": {"Bleed": 3.0}},
             {"index": 2, "name": "Bomb", "speed": 0.25, "cooldown": 4.0, "castTime": 1.0, "time": 0.4,
              "stackPotential": {"Ignite": 0.5, "Poison": 4.0}},
             {"index": 3, "name": "Debuff", "speed": 1.0, "duration": 5.0, "stackPotential": {"Ignite": 2.0}}]
    scales, notes = models.press_model(rates, 1, {2, 3})
    assert scales["Bomb"] == pytest.approx(1.0)
    assert scales["Best Ignite DPS from Bomb"] == pytest.approx(1.0)  # (already at its cooldown in PoB)
    assert scales["Debuff"] == pytest.approx(0.2)
    assert scales["Best Ignite DPS from Debuff"] == pytest.approx(0.4 / 1.0)  # (2 x 0.2 = 0.4 of the most, from 1)
    assert scales["Best Burning Ground DPS from Debuff"] == scales["Best Ignite DPS from Debuff"]
    busy = 0.25 * 0.4 + 0.2 * 1.0
    assert scales["Main"] == pytest.approx(1 - busy)
    assert scales["Best Bleed DPS from Main"] == pytest.approx(1.0)  # (3 x 0.7 still over the most that count)
    assert "Ignite x0.400" in notes[1]["basis"]
    assert models.press_key("Best Ignite DPS", "Bomb") == "Best Ignite DPS from Bomb"
    assert models.press_key("Bomb", None) == "Bomb"


def test_electrocuting_arrows_rod_is_off_from_each_electrocute_until_the_next_hit():
    """MECH40 (RULE_AUDIT 13): the Rod's stats count the share of the fight a Rod is attached."""
    ea = Skill("Electrocuting Arrow", attack=True, cast_time=0.8)
    other = Skill("Lightning Arrow", attack=True)
    none = {"Speed": 2.0, "HitChance": 100, "ElectrocuteBuildupAvg": 0}
    assert models.rod_model([ea], none, ea) == ([], models.rod_model([ea], none, ea)[1])  # no Electrocutes: always on
    stats = {"Speed": 2.0, "HitChance": 100, "ElectrocuteBuildupAvg": 50}
    rate = models.buildup_rate(stats, "Electrocute")
    assert rate > 0
    share, _ = models.rod_share(ea, stats, ea)  # main skill: the next hit 0.25 s later on average
    assert share == pytest.approx(1 - rate * 0.25)
    share2, how = models.rod_share(ea, stats, other)  # pressed: 0.5 s + its 0.8 s use
    assert share2 == pytest.approx(1 - rate * 1.3) and "pressed" in how
    mods, notes = models.rod_model([ea, other], stats, other)
    assert {(m["name"], m["skill"]) for m in mods} == {("BuffEffect", "Electrocuting Arrow"), ("DebuffEffect", "Electrocuting Arrow")}
    assert all(m["value"] == pytest.approx(-100 * (1 - share2), abs=1e-3) for m in mods)
    assert notes[0]["value"] == pytest.approx(share2, abs=1e-3)
    assert models.rod_model([other], stats, other) == ([], [])


def test_freezes_give_the_elemental_ailment_metas_energy():
    """RULE_AUDIT #2 (MECH41): game files, MetaCastOnElementalAilmentPlayer / MetaElementalInvocationPlayer: 1 Energy per
    Power per Shock or Ignite, 10 per Freeze (centienergy 1000); the boss's Freezes at buildup_rate (before: none)."""
    stats = {"Speed": 2.0, "HitChance": 100, "FreezeBuildupAvg": 10.0}
    for meta in ("Cast on Elemental Ailment", "Elemental Invocation"):
        gain, how = models.energy_gain(meta, stats, "Frostbolt")
        assert gain == pytest.approx(models.buildup_rate(stats, "Freeze") * 10 * models.BOSS_POWER) and "freezes" in how
    assert models.energy_gain("Cast on Elemental Ailment", {**stats, "FreezeBuildupAvg": 0}, "Frostbolt")[0] == 0


def test_run_and_gun_counts_for_shotgun_and_sequence_fired_skills_while_moving():
    """KeywordPopups Projectile: one projectile in the group hits each target unless otherwise specified; the
    exceptions from GrantedEffectStatSets (projectiles_can_shotgun, projectiles_crossbow_barrage, projectiles_barrage)."""
    line = ["Projectile Attacks have a 10% chance to fire two additional Projectiles while moving"]
    moving = {"Attack", "Projectile", "UsableWhileMoving"}
    galvanic = models.Skill("Galvanic Shards", group=1, attack=True)
    mods, notes = models.run_and_gun_model(line, {"ProjectileCount": 8}, galvanic, moving, False)
    assert mods[0]["scale"] == pytest.approx(2.5) and "8 projectiles" in notes[0]["basis"]  # 2 x 10% over 8
    rapid = models.Skill("Rapid Shot", group=1, attack=True)
    mods, _ = models.run_and_gun_model(line + line, {"ProjectileCount": 1}, rapid, moving, False)
    assert mods == [models.raw_mod("TwoAdditionalProjectilesChance", "BASE", 20.0, "Rapid Shot")]
    assert models.run_and_gun_model(line, {}, galvanic, moving, True)[0] == []  # standing still
    assert models.run_and_gun_model(line, {}, galvanic, {"Attack", "Projectile"}, False)[0] == []  # not used while moving
    arrow = models.Skill("Lightning Arrow", group=1, attack=True)
    mods, notes = models.run_and_gun_model(line, {}, arrow, moving, False)
    assert mods == [] and "can't hit one target together" in notes[0]["basis"]
    assert models.run_and_gun_model([], {}, galvanic, moving, False) == ([], [])


def test_hit_remnants_need_their_condition_and_the_main_skill_s_hits():
    """Sanguine Revelry, Mana Remnants and Life Remnants make one per cooldown + the wait for a qualifying hit, not their
    full 1/cooldown whenever the skill is there (MECH46, FLAT_RATES A1)."""
    from builder.search.assumptions import hit_remnant_rate
    assert hit_remnant_rate("Life Remnants", {"Speed": 2.0, "HitChance": 100})[0] == pytest.approx(1 / (3 + 0.5))
    assert hit_remnant_rate("Life Remnants", {})[0] == 0.0  # no hits, no Remnants
    assert hit_remnant_rate("Sanguine Revelry", {"Speed": 2.0, "HitChance": 100})[0] == 0.0  # no Bleed on the boss
    bleed = {"Speed": 2.0, "HitChance": 100, "BleedChancePerHit": 100, "BleedDuration": 5}
    assert hit_remnant_rate("Sanguine Revelry", bleed)[0] == pytest.approx(1 / (2 + 0.5))
    assert hit_remnant_rate("Mana Remnants", {**bleed, "CritChance": 50})[0] == 0.0  # crits, but no Elemental Ailment
    ignite = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "IgniteChancePerHit": 100, "IgniteDuration": 4}
    rate, how = hit_remnant_rate("Mana Remnants", ignite)
    assert rate == pytest.approx(1 / (2 + 1 / (2 * 0.5))) and "Ignited 100%" in how


def test_a_chill_lasts_8_s_on_the_boss_or_pob_s_chill_duration():
    """KeywordPopups Chill: 2 s on players, 8 s on non-players by default (MECHC2, the manager's ruling; was 2 s). PoB's
    ChillDuration, with the build's Chill Duration lines, wins when it has one."""
    from builder.search.assumptions import hit_remnant_rate, CHILL_SECONDS
    assert CHILL_SECONDS == 8.0
    chill = {"Speed": 1.0, "HitChance": 100, "CritChance": 100, "ChillChanceOnHit": 10}
    up = 1 - 0.9 ** 8  # 1 hit/s at 10% for 8 s
    rate, how = hit_remnant_rate("Mana Remnants", chill)
    assert rate == pytest.approx(1 / (2 + 1 / up)) and "8 s" in how
    rate, how = hit_remnant_rate("Mana Remnants", {**chill, "ChillDuration": 12})
    assert rate == pytest.approx(1 / (2 + 1 / (1 - 0.9 ** 12))) and "12 s" in how


def test_archon_of_the_blizzard_waits_for_a_gap_in_the_boss_s_landing_hits():
    """Energy Shield Recharge needs `delay` s with no damage: every landing boss hit restarts it (MECH46, FLAT_RATES A8:
    was a flat 2 x delay)."""
    import math
    base = {"EnergyShield": 1000, "EnergyShieldRechargeDelay": 4.0, "enemySkillTime": 0.7}
    blizzard = lambda stats: next(s for s in models.archon_sources(stats, set()) if s.name == "Archon of the Blizzard")
    rate = 1 / 0.7
    assert blizzard(base).wait == pytest.approx(math.expm1(rate * 4.0) / rate)  # ~209 s: hit often, rarely recharges
    dodgy = dict(base, EvadeChance=50, EffectiveBlockChance=50)
    assert blizzard(dodgy).wait == pytest.approx(math.expm1(rate / 4 * 4.0) / (rate / 4))
    assert blizzard(dict(base, EvadeChance=100)).wait == pytest.approx(4.0)  # never hit: just the delay


def test_a_meta_triggered_curse_or_mark_is_up_at_the_meta_s_trigger_rate():
    """MECH46 (FLAT_RATES A5): a curse or Mark in an Energy meta is applied at the meta's trigger rate (energy_model),
    not always up."""
    gems = {"Elemental Weakness": {"skillTypes": ["Spell", "AppliesCurse"]}, "Cast on Critical": {"skillTypes": ["Meta"]}}
    meta = [{"enabled": True, "gems": [{"name": "Cast on Critical", "support": False},
                                       {"name": "Elemental Weakness", "support": False}]}]
    (use,) = models.curse_uses(meta, gems, {}, 8.0, [], {"Elemental Weakness": 0.05})
    assert use.share == pytest.approx(0.05 * models.CURSE_DURATION) and "0.050" in use.how
    (use,) = models.curse_uses(meta, gems, {}, 8.0, [], {"Elemental Weakness": 1.0})
    assert use.share == 1.0
    (use,) = models.curse_uses(meta, gems, {}, 8.0)  # no model's rate: the labelled assumption stays
    assert use.share == 1.0 and "LOW" in use.how
    mark = Skill(name="Sniper's Mark", group=1, triggered=True, cast_time=0.5)
    coc = Skill(name="Cast on Critical", group=1, energy=True)
    use = models.mark_use(mark, [mark, coc], rates={"Sniper's Mark": 0.25})
    assert use.period == pytest.approx(4.0) and not use.on_demand
    assert models.mark_use(mark, [mark, coc], rates={"Sniper's Mark": 0.0}).period == math.inf


def test_a_repeat_does_not_echo_but_its_use_still_does():
    """KeywordPopups Cascadable: "A Cascadable Spell that Repeats will not Cascade or Echo while Repeating" (MECH46,
    FLAT_RATES C8; MECH47 narrowed it to "while Repeating"): the use still Echoes, its Repeat doesn't, so the two ledger
    lines' product is corrected to 1 + .95(r + e)."""
    texts = ["Repeatable Spells have 20% chance to Repeat", "Cascadable Spells have 20% chance to Echo"]
    both = {"Spell", "Multicastable", "Cascadable"}
    mods, notes = models.repeat_echo_model(texts, both, set())
    want = (1 + 0.95 * (0.2 + 0.2)) / (1.19 * 1.19)  # x1.380 in all
    assert mods[0]["scale"] == pytest.approx(100 * (want - 1), abs=1e-3) and notes[0]["value"] == pytest.approx(want, abs=1e-4)
    assert models.repeat_echo_model(texts, {"Spell", "Cascadable"}, set()) == ([], [])  # can't Repeat: nothing to fix
    assert models.repeat_echo_model(texts[:1], both, set()) == ([], [])
    assert models.repeat_echo_model(texts, both, {"now-and-again-echo"}) == ([], [])


def test_snap_needs_an_ailment_and_its_presses_cost_the_main_skill_time():
    """MECH46 (FLAT_RATES A14): Snap's Remnants only when an Ignite, Shock or Freeze is on the boss to Consume; a non-main
    Snap / Frost Bomb pressed for its Remnants costs the main skill its cast time, in Full DPS or not."""
    from builder.search.assumptions import REMNANT_SOURCES, Assumptions, snap_found
    assert snap_found({"Speed": 2.0, "HitChance": 100})[0] == 0.0
    found, how = snap_found({"Speed": 2.0, "HitChance": 100, "IgniteChancePerHit": 100, "IgniteDuration": 4})
    assert found == pytest.approx(1.0) and "Ignited" in how

    class Ctx:
        gems = {}

    class Build:
        ctx = Ctx()

        def __init__(self, stats):
            self.stats = stats

        def call(self, method, **kw):
            return {"stats": self.stats}[method]

        def main_group(self):
            return {"index": 1}

        def groups(self):
            return []

    skills = [{"name": "Snap", "disabled": False, "group": 2, "castTime": 0.5, "cooldownRecovery": 1.0},
              {"name": "Frost Bomb", "disabled": False, "group": 3, "castTime": 1.0, "cooldownRecovery": 1.0}]
    a = Assumptions(Build({"Speed": 2.0, "HitChance": 100}))
    mods, notes = a.remnant_presses(skills, "Transmutation Explosion")
    busy = REMNANT_SOURCES["Frost Bomb"][0] * 1.0  # (no ailment: Snap isn't pressed)
    assert mods[0]["scale"] == pytest.approx(-100 * busy, rel=1e-4) and "Frost Bomb" in notes[0]["basis"]
    a = Assumptions(Build({"Speed": 2.0, "HitChance": 100, "IgniteChancePerHit": 100, "IgniteDuration": 4}))
    mods, _ = a.remnant_presses(skills, "Transmutation Explosion")
    assert mods[0]["scale"] == pytest.approx(-100 * (busy + REMNANT_SOURCES["Snap"][0] * 0.5), rel=1e-4)
    assert a.remnant_presses(skills[:1], "Snap") == ([], [])  # Snap as the main skill: no press cost of its own


def test_a_named_event_takes_the_builds_own_rate_not_the_4s_default():
    """FLAT_RATES A3 (MECH47): a line that names a Warcry / Totem / Mark event without its rate reads the build's own
    skills of that kind (conditions.event_rates): none in the build gives 0, not one per 4 s; a Warcry in a group runs
    on its cooldown; a main skill of that kind at its speed. Other wordings keep the 4 s default, labelled LOW."""
    from builder.search import conditions
    text = "Gain a Power Charge when you use a Warcry"
    gems = {"Infernal Cry": {"skillTypes": ["Warcry", "Cooldown"]}, "Spark": {"skillTypes": ["Spell"]}}
    groups = [{"index": 1, "enabled": True, "gems": [{"name": "Spark"}]}]
    calls = []
    rates = lambda idx: calls.append(idx) or [{"index": 2, "cooldown": 8.0, "speed": 1.2}]
    none = conditions.event_rates(groups, gems, "Spark", {"Spell"}, 2.0, rates, [text])
    assert none["Warcry"][0] == 0.0 and calls == []
    assert conditions._charge_rate(text, "Power", {"Speed": 2.0, "_events": none})[0] == 0.0
    groups.append({"index": 2, "enabled": True, "gems": [{"name": "Infernal Cry"}]})
    one = conditions.event_rates(groups, gems, "Spark", {"Spell"}, 2.0, rates, [text])
    assert one["Warcry"][0] == pytest.approx(1 / 8) and calls == [[2]]
    assert conditions._charge_rate(text, "Power", {"Speed": 2.0, "_events": one})[0] == pytest.approx(1 / 8)
    main = conditions.event_rates(groups, gems, "Infernal Cry", {"Warcry"}, 0.5, rates, [text])
    assert main["Warcry"][0] == 0.5
    other = conditions._charge_rate("Gain a Frenzy Charge when you use a Life Flask", "Frenzy", {"Speed": 2.0, "_events": one})
    assert other[0] == pytest.approx(0.25) and "assumed (LOW)" in other[1]  # (not a named event: the 4 s default)
    assert conditions.event_rates(groups, gems, "Spark", {"Spell"}, 2.0, rates, ["10% increased Damage"]) == {}


def test_flask_use_ignites_and_reaching_maximum_read_the_build_not_4s():
    """MECH48 (FLAT_RATES A3, rest): a flask's or charm's own "on use" line reads that item's uses (generated charges /
    charges per use, at most once per its effect; a kill-triggered charm 0); "when you use a Flask" the build's flasks;
    "if an Attack Ignites" the main Attack's hits x Ignite chance (0 for a Spell); "on reaching maximum Power Charges"
    0 while nothing spends them, gains / maximum when all are lost on reaching it."""
    from builder.search import conditions, flasks
    gain = flasks.Charges(flask=1.0)
    flask = {"kind": "Flask", "chargesUsed": 10, "duration": 2.0}
    assert flasks.uses_per_second(flask, gain, "Ruby Flask", "Life")[0] == pytest.approx(0.1)
    assert flasks.uses_per_second({**flask, "chargesUsed": 1}, gain, "Ruby Flask", "Life")[0] == pytest.approx(0.5)
    charm = {"kind": "Charm", "chargesUsed": 10, "duration": 2.0, "gainBase": 1.0}
    assert flasks.uses_per_second(charm, gain, "Golden Charm\nUsed when you Kill a Rare or Unique enemy", None)[0] == 0
    on_use = "Gain 1 Endurance Charge on use"
    stats = {"Speed": 2.0, "_item_uses": {on_use: (0.1, "Charm 1")}}
    assert conditions._charge_rate(on_use, "Endurance", stats)[0] == pytest.approx(0.1)
    three = "Gain 3 Power Charge on use"  # (three per use: the count was dropped before MECH48)
    assert conditions._charge_rate(three, "Power", {"Speed": 2.0, "_item_uses": {three: (0.1, "Charm 1")}})[0] == pytest.approx(0.3)
    events = {"Flask": (0.3, "two"), "LifeFlask": (0.1, "one"), "ManaFlask": (0.2, "one")}
    life = conditions._charge_rate("Gain a Frenzy Charge when you use a Life Flask", "Frenzy", {"Speed": 2.0, "_events": events})
    assert life[0] == pytest.approx(0.1)
    ignites = "Gain a Frenzy Charge if an Attack Ignites an Enemy"
    attack = {"Speed": 2.0, "HitChance": 50, "IgniteChancePerHit": 20, "_main_types": {"Attack"}}
    assert conditions._charge_rate(ignites, "Frenzy", attack)[0] == pytest.approx(0.2)
    assert conditions._charge_rate(ignites, "Frenzy", {**attack, "_main_types": {"Spell"}})[0] == 0.0
    reach = "Gain a Frenzy Charge on reaching Maximum Power Charges"
    held = {"Speed": 2.0, "PowerChargesMax": 4, "CritChance": 50, "_texts": [reach, "Gain a Power Charge on Critical Hit"]}
    assert conditions._charge_rate(reach, "Frenzy", held)[0] == 0.0  # (reached once, then held)
    lost = {**held, "_texts": held["_texts"] + ["Lose all Power Charges on reaching maximum Power Charges"]}
    assert conditions._charge_rate(reach, "Frenzy", lost)[0] == pytest.approx(1.0 / 4)  # (1 crit/s / 4)


def test_rage_on_melee_hit_needs_a_melee_main_skill():
    """MECH48 (catalog Melee Hit / Melee Axe Hit, 'PoB: wrong'): PoB drops the melee restriction of "Gain N Rage on Melee
    Hit"; our rate read the main skill's hits whatever it was. A Spell gains none; a Melee Axe Hit line needs an Axe."""
    from builder.search import conditions
    stats = {"Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "Melee"}, "_weapon": "One Handed Axe",
             "_weapon_types": ["One Handed Axe"]}
    assert conditions._rage_gain("Gain 2 Rage on Melee Hit", stats, True)[0] > 0
    assert conditions._rage_gain("Gain 2 Rage on Melee Hit", {**stats, "_main_types": {"Spell"}}, True)[0] == 0.0
    assert conditions._rage_gain("Gain 1 Rage on Melee Axe Hit", stats, True)[0] > 0
    assert conditions._rage_gain("Gain 1 Rage on Melee Axe Hit", {**stats, "_weapon": "Two Handed Mace", "_weapon_types": ["Two Handed Mace"]}, True)[0] == 0.0
    # (MECHC2: either slot counts: an Axe in Weapon 2 still gains it)
    assert conditions._rage_gain("Gain 1 Rage on Melee Axe Hit", {**stats, "_weapon": "Two Handed Mace",
                                 "_weapon_types": ["Two Handed Mace", "One Handed Axe"]}, True)[0] > 0


def test_a_charge_consumer_beside_the_main_skill_uses_its_groups_rate():
    """FLAT_RATES A4 (MECH48): a charge consumer or Rage spender beside the main skill is used at its own group's rate
    (cooldown, else duration, else PRESS_EVERY, LOW), not one use per 4 s."""
    from builder.search import conditions, models
    assert conditions.skill_use_rate({"cooldown": 8.0, "speed": 1.0})[0] == pytest.approx(1 / 8)
    assert conditions.skill_use_rate({"duration": 2.0, "speed": 0.25})[0] == pytest.approx(0.25)  # (capped by its speed)
    each, _, low = conditions.skill_use_rate({"speed": 2.0})
    assert each == pytest.approx(1 / models.PRESS_EVERY) and low

    class Fake:
        def call(self, name, indices=()):
            return {"rates": [{"index": 3, "cooldown": 10.0, "speed": 1.0}]}

    resolver = conditions.Conditions.__new__(conditions.Conditions)
    resolver.build = Fake()
    groups = [{"index": 3, "enabled": True, "gems": [{"name": "Charged Staff"}]},
              {"index": 4, "enabled": False, "gems": [{"name": "Other"}]}]
    assert resolver._group_uses(groups, {"Charged Staff", "Other"}) == {"Charged Staff": pytest.approx(0.1)}


def test_a_bucklers_granted_parry_is_recognised():
    """conditions.PARRY_GRANT held two backspace characters for its word boundaries (since 101ad14), so it never matched a
    Buckler's "Grants Skill: Parry" (MECH47)."""
    from builder.search import conditions
    assert conditions.PARRY_GRANT.search("Grants Skill: Level 20 Parry")
    assert not conditions.PARRY_GRANT.search("Grants Skill: Level 20 Parrying Stance")


def test_infusion_consumed_recently_from_the_builds_consumers_and_supply():
    """MECH49: "if you have consumed an Elemental Infusion Recently" (Empowering Infusions, Infused Spell Damage) was set
    by nothing (an undercount). Its box is on at 1 - e^(-consumed per second x 4 s), weighed by that share; no consumer
    or no supply: 0 with its reason."""
    from builder.search import conditions

    import math

    assert conditions._infusion_recently([], {}) == (0.0, "")
    assert conditions._infusion_recently([], {"_infusion_consumed": (0.0, "no skill of the build consumes Infusions: 0")}) \
        == (0.0, "no skill of the build consumes Infusions: 0")
    on, basis, up = conditions._infusion_recently([], {"_infusion_consumed": (0.25, "Frost Bomb")})
    assert on and up == pytest.approx(1 - math.exp(-1.0)) and "Frost Bomb" in basis and "LOW" in basis
    var = next(c for c in conditions.CONDITIONS if c.var == "conditionInfusionConsumedRecently")
    assert var.value_fn is conditions._infusion_recently
    assert conditions.INFUSION_CONSUMED.search("35% increased Spell Damage if you have consumed an Elemental Infusion Recently")


def test_infusion_consumers_match_the_game_files():
    """INFUSION_CONSUMERS is every player skill whose stat set holds active_skill_consumes_(a_)<element>_infusion
    (GrantedEffectStatSets), named as its ActiveSkills entry; skipped without the export."""
    from builder.search import conditions

    import json
    import re
    from pathlib import Path

    roots = sorted((Path(__file__).resolve().parents[1] / "data").glob("game-*/tables"))
    if not roots or not (roots[-1] / "GrantedEffectStatSets.json").exists():
        pytest.skip("no game-file export; run `python -m builder.data.gamefiles`")

    def rows(name):
        data = json.loads((roots[-1] / f"{name}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data["rows"]

    stats = rows("Stats")
    consume = {i: m.group(1).capitalize() for i, r in enumerate(stats)
               if (m := re.fullmatch(r"active_skill_consumes_(?:a_)?(\w+)_infusion", r["Id"]))}
    found = {}
    for r in rows("GrantedEffectStatSets"):
        if not r["Id"].endswith("Player"):
            continue
        ids = [x for k in ("ImplicitStats", "ConstantStats") for x in r.get(k) or [] if isinstance(x, int)]
        if els := {consume[i] for i in ids if i in consume}:
            found[r["Id"]] = els
    effects = {g["Id"]: g for g in rows("GrantedEffects")}
    actives = rows("ActiveSkills")
    named = {}
    for set_id, els in found.items():
        effect = effects.get(set_id)
        name = actives[effect["ActiveSkill"]]["DisplayedName"] if effect and isinstance(effect.get("ActiveSkill"), int) else None
        name = {"Empowered Sparks": "Spark"}.get(name, name)
        named.setdefault(name, set()).update(e for e in els if e != "Verisium")
    assert set(named) == set(conditions.INFUSION_CONSUMERS)
    for name, els in named.items():
        assert els == set(conditions.INFUSION_CONSUMERS[name]), name


def test_divinity_from_the_builds_divine_charge_gain():
    """MECH49: Divinity ("on reaching maximum Divine Charges") was set by nothing. 10 charges at 2 landed hits/s fill in
    5 s, inside Divinity's 10 s: always up; at 0.5/s they fill in 20 s: half the fight; no "Lose all" line: held at the
    maximum, reached once: 0."""
    from builder.search import conditions

    lines = ["+10 to maximum Divine Charges", "Gain a Divine Charge on Hit",
             "You gain Divinity for 10 seconds on reaching maximum Divine Charges",
             "Lose all Divine Charges when you gain Divinity"]
    assert conditions._divinity([], {"Speed": 2.0}) == (0.0, "")
    on, _, up = conditions._divinity(lines, {"Speed": 2.0, "HitChance": 100})
    assert on and up == 1.0
    assert conditions._divinity(lines, {"Speed": 1.0, "HitChance": 50})[2] == pytest.approx(0.5)
    assert conditions._divinity(lines[:3], {"Speed": 2.0})[0] == 0.0
    assert conditions._divinity(lines[1:], {"Speed": 2.0})[0] == 0.0


def test_a_granted_skill_pob_builds_nothing_for_is_noted_not_dropped():
    """MECH49: Dark Pact, Queen's Procession, His Dark Horizon, His Grave Command (no skill file in PoB) counted 0 in
    silence; now each granted one PoB built nothing for gets a labelled 'not counted' note."""
    from types import SimpleNamespace

    from builder.search.assumptions import Assumptions

    lines = [{"source": "item:Weapon 1", "text": "Grants Skill: Level (1-20) Dark Pact"},
             {"source": "passive:Devotion to the King", "text": "Grants Skill: Queen's Procession"},
             {"source": "item:Weapon 2", "text": "Grants Skill: Level 20 Parry"}]
    fake = SimpleNamespace(build=SimpleNamespace(call=lambda name, **_: {"lines": lines}))
    notes = Assumptions.unbuilt_grants(fake, [{"name": "Parry"}])
    assert [n["label"] for n in notes] == ["Dark Pact: not counted", "Queen's Procession: not counted"]
    assert all(n["value"] == 0 and "undercount" in n["basis"] for n in notes)


def test_living_bomb_explodes_when_the_build_deals_twice_the_boss_ailment_threshold():
    """MECHC1 (FLAT_RATES A11; before, one seed every 5 s for any build). LivingBombPlayer: one seed, 15 s, exploding at
    200% of the Ailment Threshold; a seed that runs out first is counted as exploding then (UNVERIFIED), not zeroed."""
    from builder.search.assumptions import living_bomb_rate

    rate, how = living_bomb_rate({"EnemyAilmentThreshold": 100000, "CombinedDPS": 50000}, 1.0)
    assert rate == pytest.approx(1 / 5) and "4.0 s" in how  # 1 s cast + 200,000 / 50,000
    slow, how = living_bomb_rate({"EnemyAilmentThreshold": 100000, "CombinedDPS": 5000}, 1.0)
    assert slow == pytest.approx(1 / 16) and "UNVERIFIED" in how
    assert living_bomb_rate({"EnemyAilmentThreshold": 100000, "CombinedDPS": 0}, 1.0)[0] == 0


def test_a_concoction_is_thrown_as_often_as_its_mana_flask_charges_allow():
    """Each Concoction throw consumes 5 Mana Flask charges (game files, flask_throw_base_charges_used, MECHC2); PoB throws
    at will. On a lone boss the flask refills only by generation: no flask or no generation, no throws."""
    from builder.search.flasks import THROW_CHARGES, Charges, charge_use_rate
    assert set(THROW_CHARGES.values()) == {5} and len(THROW_CHARGES) == 5
    flask = {"kind": "Flask", "chargesMax": 75, "chargesUsed": 15, "duration": 3}
    assert charge_use_rate(None, Charges(), "Mana", 5)[0] == 0.0
    rate, how = charge_use_rate(flask, Charges(), "Mana", 5)
    assert rate == 0.0 and "gains no charges" in how
    rate, how = charge_use_rate(flask, Charges(mana_flask=1.0, flask_gain_inc=50), "Mana", 5)
    assert rate == pytest.approx(1.5 / 5) and "1.50 charges/s" in how
