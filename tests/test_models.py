"""Custom models for mechanics PoB ignores (builder/search/models.py), without an engine."""
from __future__ import annotations

from dataclasses import replace

import math

import pytest

from builder.ledger import model_entry, translate
from builder.search import boss_hits, models
from builder.search.models import Inputs, Skill


@pytest.fixture
def pob_cadence(monkeypatch):
    """"When hit" benefits at the test's own enemySkillTime (0.7 s if none): these tests check their model at a given
    cadence; since MECHC11 the models read bosses.benefit_skill_time (one hit per boss skill use), tested on its own."""
    from builder.search import bosses
    monkeypatch.setattr(bosses, "benefit_skill_time", lambda stats: float((stats or {}).get("enemySkillTime") or 0.7))


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
    # Seething Body (MECHC4): no recovery, but Archon only from High Infernal Flame (65%) to the reset: 35% of each 10 s
    # Flame cycle (2 x 1,000 Mana at 200/s), half effect
    assert value["1% more Elemental Damage with Spells"] == pytest.approx(12.5 * 0.35, rel=1e-3)


def test_seething_body_archon_follows_the_infernal_flame_cycle():
    """MECHC4 (RESEARCH12 18af): Archon from the first cast on High Infernal Flame after the recovery until Flame reaches
    its maximum (2 x maximum Mana, filled by skill costs) and resets; Archon Duration lines don't lengthen it. Before, 10 s
    on and 20 s off whatever the build spent: 33%, an overcount for fast spenders."""
    cycle = 2 * 2000 / 300  # 13.3 s
    share, on, off = models.flame_uptime(cycle, 0.0)
    # the 20 s recovery ends 6.7 s into the second cycle (Flame at 50%): Archon at 65% (8.7 s), until the reset
    assert off == pytest.approx(cycle + 0.65 * cycle) and on == pytest.approx(0.35 * cycle)
    assert share == pytest.approx(0.175, abs=1e-3)
    assert models.flame_uptime(40.0, 0.0)[0] == pytest.approx(0.35)  # slow spender: the recovery ends before High
    assert models.flame_uptime(15.0, 100.0)[2] == pytest.approx(10.0)  # 10 s recovery ends on High: Archon at once
    x = {"ArchonDuration": 100.0, "ArchonRecovery": 0.0, "ArchonEffect": 0.0, "ArchonCritChance": 0.0, "ArchonCritDamage": 0.0}
    assert models.archon_effects(x, 0.0, False, cycle)["uptime"] == pytest.approx(share)  # duration lines don't count
    sources = models.archon_sources({"Mana": 2000, "ManaPerSecondCost": 0}, set())
    assert sources[0].name == "Seething Body" and models.archon_effects(x, sources[0].wait, False, sources[0].flame)["uptime"] == 0


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


def test_a_skills_own_final_energy_penalty_scales_what_it_feeds_a_meta_skill():
    """active_skill_energy_generated_+%_final (MECHB4, COVC6): Incinerate's -98 (game files) leaves 2% of the Energy its
    crits earn Cast on Critical; PoB reads nothing of it. A gain that isn't the skill's own use (Cast on Dodge) is kept."""
    coc = Skill("Cast on Critical", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50, "LightningCritAverage": 50000,
             "EnemyAilmentThreshold": 100000}
    incinerate = Skill("Incinerate", group=1, spell=True, cast_time=0.25, energy_final=-98)
    mods, notes, _ = models.energy_model(Inputs(), stats, [incinerate, coc, comet], incinerate)
    rate = next(m["value"] for m in mods if m.get("key") == "triggerRate")
    assert rate == pytest.approx(0.02 * 10 * 1.57 / 120, rel=1e-3)
    assert "-98% final Energy" in notes[-1]["basis"] and "UNVERIFIED" in notes[-1]["basis"]
    dodge = Skill("Cast on Dodge", group=2, energy=True)
    plain = models.energy_model(Inputs(), {}, [SPARK, dodge, comet], SPARK)[0]
    penalised = models.energy_model(Inputs(), {}, [replace(SPARK, energy_final=-98), dodge, comet],
                                    replace(SPARK, energy_final=-98))[0]
    assert ([m["value"] for m in plain if m.get("key") == "triggerRate"]
            == [m["value"] for m in penalised if m.get("key") == "triggerRate"])


def test_an_invocation_never_triggers_a_spell_costing_more_than_its_store():
    """Every Invocation stores at most 500 Energy (generic_ongoing_trigger_maximum_energy, game files): a spell costing
    more (over 5 s base cast time) never triggers; the others in the group keep their rate."""
    elemental = Skill("Elemental Invocation", group=2, energy=True)
    slow = Skill("Slow Spell", group=2, spell=True, triggered=True, cast_time=6.0)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "ShockChanceOnHit": 50}
    mods, notes, _ = models.energy_model(Inputs(), stats, [SPARK, elemental, slow, comet], SPARK)
    rates = {m["skill"]: m["value"] for m in mods if m.get("key") == "triggerRate"}
    assert rates["Slow Spell"] == 0 and rates["Comet"] > 0
    assert any("never triggers" in n["basis"] for n in notes)


def test_unmodeled_metas_count_nothing_rather_than_a_self_cast():
    dodge = Skill("Feral Invocation", group=2, energy=True)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    mods, notes, groups = models.energy_model(Inputs(), {}, [SPARK, dodge, comet], SPARK)
    assert next(m["value"] for m in mods if m.get("key") == "triggerRate") == 0.0
    assert groups == set() and "not counted" in notes[0]["label"]


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
def test_the_other_metas_gain_energy_from_their_own_events(pob_cadence):
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    stats = {"Speed": 2, "HitChance": 100, "enemySkillTime": 0.5, "EvadeChance": 20, "EffectiveBlockChance": 50,
             "PhysicalHitAverage": 1000}

    def rate(meta: str, main: Skill) -> float:
        mods, notes, _ = models.energy_model(Inputs(), stats, [main, Skill(meta, group=2, energy=True), comet], main)
        return next(m["value"] for m in mods if m.get("key") == "triggerRate")

    # Cast on Block: 2 boss hits/s, the Arbiter's 13 of 20 blockable (7 red-flash: boss_hits, MECHB6), 80% not evaded, 50%
    # blocked = 0.52 blocks/s x 25 Energy, x1.57, over 120 Energy
    assert rate("Cast on Block", SPARK) == pytest.approx(2 * 0.65 * 0.8 * 0.5 * 25 * 1.57 / 120, rel=1e-3)
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
    # Energy on kills (MECHB9): none against a lone boss, and the guide says why; the boss preset's adds give some
    mods, notes, _ = models.energy_model(Inputs(), stats, [slam, Skill("Reaper's Invocation", group=2, energy=True), comet], slam)
    assert "no enemy dies before the boss" in str(notes)
    adds = {**stats, models.ADD_KILLS_STAT: 0.04}  # (one add per 25 s)
    assert models.energy_gain("Reaper's Invocation", adds, "Boneshatter", slam)[0] == pytest.approx(0.04 * 30)
    assert models.energy_gain("Reaper's Invocation", adds, "Spark", Skill("Spark", spell=True))[0] == 0.0


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


@pytest.mark.usefixtures("pob_cadence")
def test_iron_ward_store_fills_at_the_boss_hit_rate_after_mitigation():
    store = models.iron_ward_store(WARD, PINNACLE)
    fill = 4.8 * 979.475 * 0.5 / 0.7 * 7 / 24  # 480% of half of each 979 hit, a hit every 0.7 s, 7 of the Arbiter's 24 Physical
    assert store.fill == pytest.approx(fill)
    assert store.uses == pytest.approx(fill / 22886) and not store.use_capped  # released whenever full
    dodgy = models.iron_ward_store(WARD, {**PINNACLE, "EvadeChance": 50, "EffectiveBlockChance": 20})
    land = boss_hits.land_share({"EvadeChance": 50, "EffectiveBlockChance": 20})  # (MECHB6: red-flash hits land)
    assert dodgy.fill == pytest.approx(fill * land, rel=1e-6)  # evaded and blocked hits store nothing
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


def test_fluke_counts_the_middle_of_its_range_on_the_skills_the_meta_triggers():
    lo, hi = "support_randomise_lower_damage_+%_final", "support_randomise_higher_damage_+%_final"
    meta = models.Skill("Cast on Critical", group=4, energy=True, support_stats={lo: -40, hi: 60})
    spells = [models.Skill("Comet", group=4, spell=True, triggered=True),
              models.Skill("Frost Bomb", group=4, spell=True, triggered=True)]
    other = models.Skill("Fireball", group=5, spell=True, triggered=True)  # another group: not the meta's spell
    mods, notes = models.gem_stat_model([meta, *spells, other])
    # one 10% more line (not -20% and +30%, which would multiply to 4%) on each triggered spell, none on the meta
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [
        ("1% more Damage", 10, "Comet"), ("1% more Damage", 10, "Frost Bomb")]
    assert len(notes) == 1 and "Comet, Frost Bomb" in notes[0]["basis"] and notes[0]["confidence"] == "theory"
    # a meta with no spells in its group: nothing
    assert models.gem_stat_model([meta]) == ([], [])


def test_alignment_counts_at_a_match_share_with_its_play_note():
    # RESEARCH10 / the manager's revised ruling: one matched attack per 1.5 s (LOW) against the skill's attack rate
    stat = "support_called_shots_projectile_damage_+%_final_when_matching_direction"
    bow = models.Skill("Lightning Arrow", group=1, attack=True, support_stats={stat: 40})
    mods, notes = models.gem_stat_model([bow], stats={"Speed": 3.0}, main=bow)
    share = (1 / models.ALIGNMENT_MATCH_SECONDS) / 3.0
    assert [(m["line"], m.get("skill")) for m in mods] == [("1% more Projectile Damage", "Lightning Arrow")]
    assert mods[0]["scale"] == pytest.approx(40 * share, abs=0.01)  # ~x1.09, not x1.40
    assert "step round the boss" in notes[0]["play"] and "Without it" in notes[0]["play"]
    assert "UNVERIFIED" in notes[0]["basis"] and "LOW" in notes[0]["basis"]
    # a slow attacker matches every attack it can: the share stops at 100%
    mods, _ = models.gem_stat_model([bow], stats={"Speed": 0.5}, main=bow)
    assert mods[0]["scale"] == pytest.approx(40)
    # Alignment III's 4 Projectiles at the same share (an average count, as PoB counts chance-based Projectiles)
    three = models.Skill("Lightning Arrow", group=1, attack=True,
                         support_stats={"support_called_shots_additional_projectiles_when_matching_indicated_direction": 4})
    mods, _ = models.gem_stat_model([three], stats={"Speed": 3.0}, main=three)
    assert mods[0]["line"] == "Skills fire an additional Projectile" and mods[0]["scale"] == pytest.approx(4 * share, abs=0.01)


def test_cadence_stacks_from_the_use_rate_and_keeps_below_its_lockout():
    # COVE6: 8% more Attack Speed per use in the past 4 s; 6 uses in 4 s lock the skill for 10 s, so the player paces
    # at 5 uses per 4 s (1.25/s). Stacks n = 4r - 1 at the resulting rate r.
    stats = {"support_measured_speed_attack_speed_+%_final_per_stack": 8, "support_measured_speed_maximum_stacks": 6,
             "support_measured_speed_disabled_ms": 10000}
    axe = models.Skill("Boneshatter", group=1, attack=True, support_stats=stats)
    mods, notes = models.gem_stat_model([axe], stats={"Speed": 1.0}, main=axe)
    assert [(m["line"], m.get("skill")) for m in mods] == [("1% more Attack Speed", "Boneshatter")]
    assert mods[0]["scale"] == pytest.approx(25)  # 1/s rises to the 1.25/s pace (4 stacks would give 1.32)
    # a fast attacker is held to the pace: less, not more (PoB had no lockout: an overcount)
    fast, notes = models.gem_stat_model([axe], stats={"Speed": 2.0}, main=axe)
    assert fast[0]["scale"] == pytest.approx(-37.5) and "10 s locked" in notes[0]["play"]
    # a slow one: r = r0 (0.92 + 0.32 r)
    slow, _ = models.gem_stat_model([axe], stats={"Speed": 0.5}, main=axe)
    r = 0.92 * 0.5 / (1 - 0.32 * 0.5)
    assert slow[0]["scale"] == pytest.approx((r / 0.5 - 1) * 100, abs=0.01)
    # its own line, already in the stats it reads, is taken back out: the same answer again
    again, _ = models.gem_stat_model([axe], stats={"Speed": 1.25}, main=axe, previous=mods)
    assert again[0]["scale"] == pytest.approx(25)
    # a Sustained skill (the game: "will instead gain Attack speed periodically"): no pacing of its hit rate, no lockout
    shield = models.Skill("Resonating Shield", group=1, attack=True, support_stats=stats)
    mods, notes = models.gem_stat_model([shield], stats={"Speed": 4.0}, main=shield)
    assert mods == [] and "Sustained" in notes[0]["basis"] and notes[0]["value"] == "not counted"
    # not the main skill: no rate, so 0 and a note
    other = models.Skill("Boneshatter", group=2, attack=True, support_stats=stats)
    mods, notes = models.gem_stat_model([other], stats={"Speed": 2.0}, main=axe)
    assert mods == [] and "not the main skill" in notes[0]["basis"]


def test_ailment_supports_count_on_the_boss_conditions_and_cost_parts_wait():
    armament = "support_elemental_armament_attack_damage_+%_final_per_elemental_ailment_on_target"
    frost = "support_frostfire_ignite_effect_+%_final_vs_frozen_unique_enemies"
    axe = models.Skill("Boneshatter", group=1, attack=True, support_stats={armament: 12, frost: 25})
    lines = {(m["line"], m["scale"]) for m in models.gem_stat_model([axe])[0]}
    assert ("1% more Magnitude of Ignite against Frozen Enemies", 25) in lines
    for ailment in ("Ignited", "Chilled", "Frozen", "Shocked", "Electrocuted"):  # (KeywordPopups ElementalAilments)
        assert (f"1% more Attack Damage against {ailment} Enemies", 12) in lines
    # the game adds them (1 + 12% x n): with Chill and Shock boxes on, the two lines multiply to 1.24, not 1.12^2
    config = {"conditionEnemyChilled": True, "conditionEnemyShocked": True, "conditionEnemyFrozen": False}
    mods, notes = models.gem_stat_model([axe], config=config)
    each = [m["scale"] for m in mods if "Chilled" in m["line"]][0]
    assert (1 + each / 100) ** 2 == pytest.approx(1.24, rel=1e-4)
    assert {m["scale"] for m in mods if "Attack Damage" in m["line"]} == {each}
    armament_note = next(n for n in notes if n["entry"] == "support-elemental-armament-per-ailment")
    assert "(12)" in armament_note["basis"] and "2 of its conditions are on" in armament_note["basis"]
    all_five = {f"conditionEnemy{a}": True for a in ("Ignited", "Chilled", "Frozen", "Shocked", "Electrocuted")}
    each = [m["scale"] for m in models.gem_stat_model([axe], config=all_five)[0] if "Ignited" in m["line"]
            and "Attack" in m["line"]][0]
    assert (1 + each / 100) ** 5 == pytest.approx(1.6, rel=1e-4)  # (not 1.12^5 = 1.76)
    banner = models.Skill("War Banner", group=1, support_stats={"support_glory_required_+%_final": -50,
                                                                "support_pin_pin_duration_+%_final": 30})
    assert models.gem_stat_model([banner]) == ([], [])  # (read by the Glory and Pin models, not applied here)
    assert models.support_part_scale(banner, "glory_cost")[0] == {"GloryRequiredMorePercent": -50}
    assert models.support_part_scale(banner, "pin_duration")[0] == {"PinDurationMorePercent": 30}
    hex_ = models.Skill("Despair", group=2, spell=True,
                        support_stats={"support_consume_power_charge_to_gain_curse_duration_+%_final": 80})
    assert models.support_part_scale(hex_, "curse_duration")[0] == {"CurseDurationMorePercent": 80}


def test_practical_magic_counts_at_the_main_skills_heavy_stun_share_and_new_parts_wait():
    stats = {"support_stunning_magic_grant_spell_damage_+%_buff_on_heavy_stunning": 40,
             "support_stunning_magic_buff_duration_ms": 8000}
    axe = models.Skill("Boneshatter", group=1, attack=True, support_stats=stats)
    boss = {"HeavyStunBuildupAvg": 10, "Speed": 2, "HitChance": 100, "PhysicalHitAverage": 1000, "CritChance": 0}
    rate = models.heavy_stun_rate(boss)[0]
    mods, notes = models.gem_stat_model([axe], stats=boss, main=axe)
    assert mods[0]["line"] == "1% increased Spell Damage" and "skill" not in mods[0]  # (a buff on you: every Spell)
    assert mods[0]["scale"] == pytest.approx(40 * min(1.0, 8 * rate), rel=1e-4)
    assert models.gem_stat_model([axe], stats={}, main=axe)[0] == []  # (no Heavy Stuns, no buff)
    other = models.Skill("Boneshatter", group=2, attack=True, support_stats=stats)
    mods, notes = models.gem_stat_model([other], stats=boss, main=axe)
    assert mods == [] and "not the main skill" in notes[0]["basis"]
    # Lasting / Persistent Ground and Haemocrystals: parts for the ground and crystal models, no line today
    slam = models.Skill("Earthquake", group=3, attack=True, support_stats={
        "ground_effect_duration_+%": 50, "consume_bleed_to_create_X_haemocrystals": 4,
        "haemocrystals_X_additional_crystals_on_consuming_aggravated_bleed": 2,
        "support_haemocrystals_maximum_allowed_crystals": 20})
    assert models.gem_stat_model([slam]) == ([], [])
    assert models.support_part_scale(slam, "ground_duration")[0] == {"GroundDurationIncPercent": 50}
    assert models.support_part_scale(slam, "haemocrystals")[0] == {
        "HaemocrystalsPerConsume": 4, "HaemocrystalsExtraIfAggravated": 2, "HaemocrystalsMax": 20}


def test_practical_magic_on_a_stunning_attack_beside_the_main_spell_counts_from_its_own_group():
    # A13-3: the stunning attack's own group output (bridge useRates) at the press model's share of its nonstop uses
    stats = {"support_stunning_magic_grant_spell_damage_+%_buff_on_heavy_stunning": 40,
             "support_stunning_magic_buff_duration_ms": 8000}
    spell = models.Skill("Fireball", group=1, spell=True)
    axe = models.Skill("Boneshatter", group=2, attack=True, support_stats=stats)
    assert models.gem_stat_side_groups([spell, axe], spell) == [2]
    assert models.gem_stat_side_groups([spell, axe], axe) == []
    own = {"index": 2, "name": "Boneshatter", "speed": 2, "HeavyStunBuildupAvg": 10, "HitChance": 100,
           "PhysicalHitAverage": 1000, "CritChance": 0, "_press": 0.25}
    nonstop = models.heavy_stun_rate({**own, "Speed": 2})[0]
    mods, notes = models.gem_stat_model([spell, axe], stats={}, main=spell, groups={2: own})
    assert mods[0]["line"] == "1% increased Spell Damage" and "skill" not in mods[0]
    assert mods[0]["scale"] == pytest.approx(40 * min(1.0, 8 * nonstop * 0.25), rel=1e-4)
    assert "Use Boneshatter beside the main skill" in notes[0]["play"]
    # no press rate, or no stun fields from the bridge: not counted, said so
    assert models.gem_stat_model([spell, axe], main=spell, groups={2: {**own, "_press": None}})[0] == []
    mods, notes = models.gem_stat_model([spell, axe], main=spell, groups={2: {"index": 2, "speed": 2, "_press": 0.25}})
    assert mods == [] and "doesn't report its own group's Heavy Stun buildup" in notes[0]["basis"]


def test_upwelling_counts_at_the_share_below_full_mana_from_the_builds_spending():
    stat = "support_minion_damage_spirit_cost_minion_damage_+%_while_missing_mana"
    buff = models.Skill("Some Persistent Buff", group=2, support_stats={stat: 50})
    mods, notes = models.gem_stat_model([buff], stats={"ManaPerSecondCost": 10, "ManaRegenRecovery": 40})
    assert [(m["line"], m.get("skill")) for m in mods] == [("Minions deal 1% increased Damage", None)]
    assert mods[0]["scale"] == pytest.approx(50 * 0.25)
    assert models.gem_stat_model([buff], stats={"ManaPerSecondCost": 60, "ManaRegenRecovery": 40})[0][0]["scale"] == 50
    mods, notes = models.gem_stat_model([buff], stats={"ManaRegenRecovery": 40})
    assert mods == [] and "spends no Mana" in notes[0]["basis"]


def test_penalties_on_uncounted_parts_wait_for_the_model_that_counts_them():
    area, mimic = "support_spell_cascade_area_of_effect_+%_for_cascade", "skill_used_by_fractured_mimic_spell_damage_+%_final"
    offering = "cascadeable_offering_support_offering_casted_spell_damage_+%_final"
    spell = models.Skill("Ice Nova", group=1, spell=True, support_stats={area: -20, mimic: -50})
    bones = models.Skill("Bone Offering", group=2, spell=True, support_stats={offering: -50})
    # nothing today: the Cascades, Mimic casts and spells around Offerings aren't counted, so their penalties cut nothing
    assert models.gem_stat_model([spell, bones]) == ([], [])
    # the model that counts a part gets its multiplier and a note
    scale, notes = models.support_part_scale(spell, "cascade")
    assert scale == {"Area of Effect": pytest.approx(0.8)} and notes[0]["entry"] == "support-spell-cascade-cascade-area"
    assert models.support_part_scale(spell, "mimic")[0] == {"Damage": pytest.approx(0.5)}
    assert models.support_part_scale(bones, "offering")[0] == {"Damage": pytest.approx(0.5)}
    assert models.support_part_scale(bones, "mimic") == ({}, [])  # (the support isn't on it)


def test_spectral_volley_cuts_the_attacks_own_projectiles():
    own, spectral = ("support_spectral_arrows_damage_+%_final_with_non_spectral_projectiles",
                     "support_spectral_arrows_damage_+%_final_with_spectral_projectiles")
    bow = models.Skill("Lightning Arrow", group=1, attack=True, support_stats={own: -60, spectral: 20})
    mods, notes = models.gem_stat_model([bow])
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Damage", -60, "Lightning Arrow")]
    assert models.support_part_scale(bow, "spectral")[0] == {"Damage": pytest.approx(1.2)}


def test_echo_and_aftershock_parts_wait_for_their_models():
    # AUDIT6 A6-2 / A6-3: the Echoes and Tectonic Slams' Aftershock are written ready, and move nothing until counted
    cry = models.Skill("Infernal Cry", group=1, support_stats={
        "base_warcry_echo_X_times": 2, "warcry_echo_area_of_effect_+%_final_per_repeat": 100,
        "warcry_echo_delay_ms_from_support": 1300})
    assert models.gem_stat_model([cry]) == ([], [])
    scale, notes = models.support_part_scale(cry, "warcry_echo")
    assert scale == {"WarcryEchoes": 2, "EchoAreaMorePercentPerEcho": 100, "WarcryEchoDelayMs": 1300}
    assert len(notes) == 3 and notes[0]["value"].startswith("2 (WarcryEchoes)")
    slam = models.Skill("Boneshatter", group=1, attack=True, support_stats={
        "number_of_dual_cascade_aftershocks": 1, "aftershock_damage_+%_final_from_support_dual_cascade_aftershocks": -45,
        "aftershock_area_of_effect_+%_final_from_support_dual_cascade_aftershocks": -30})
    assert models.support_part_scale(slam, "tectonic_aftershock")[0] == {
        "TectonicAftershocks": 1, "Damage": pytest.approx(0.55), "Area of Effect": pytest.approx(0.7)}
    crash = models.Skill("Volcanic Fissure", group=1, attack=True, support_stats={
        "number_of_branching_fissures": 2, "branching_fissure_damage_+%_final": -40,
        "branching_fissure_area_of_effect_+%_final": -35})
    assert models.gem_stat_model([crash]) == ([], [])
    assert models.support_part_scale(crash, "branch_fissure")[0] == {
        "BranchingFissures": 2, "Damage": pytest.approx(0.6), "Area of Effect": pytest.approx(0.65)}


def test_slow_potency_counts_as_chill_magnitude_on_its_skill():
    spark = models.Skill("Frostbolt", group=1, spell=True, support_stats={"base_debuff_slow_magnitude_+%": 15})
    mods, notes = models.gem_stat_model([spark])
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [
        ("1% increased Magnitude of Chill you inflict", 15, "Frostbolt")]
    assert notes[0]["entry"] == "support-slow-potency-chill"


def test_long_fuse_counts_on_the_detonating_stat_sets_only():
    fuse = {"support_long_fuse_detonation_damage_+%_final": 35}
    grenade = models.Skill("Explosive Grenade", group=1, attack=True, support_stats=fuse)
    mods, notes = models.gem_stat_model([grenade])
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Damage", 35, "Explosive Grenade")]
    assert notes[0]["entry"] == "support-long-fuse-detonation-damage"
    spear = models.Skill("Explosive Spear", group=2, attack=True, support_stats=fuse)
    mods, notes = models.gem_stat_model([spear])  # (calculated stat set unknown: not counted, said so)
    assert mods == [] and "doesn't know which stat set" in notes[0]["basis"]
    spear.stat_set = "Spear"  # (the thrown hit isn't the Detonation)
    assert models.gem_stat_model([spear])[0] == []
    spear.stat_set = "Explosion"
    assert [m["scale"] for m in models.gem_stat_model([spear])[0]] == [35]
    other = models.Skill("Some Unlisted Skill", group=3, support_stats=fuse)
    assert models.gem_stat_model([other])[0] == []
    plain = models.Skill("Grenade", group=4, attack=True, support_stats=fuse)  # the crossbow's default Grenade (AUDIT17 A17-3)
    assert [m["scale"] for m in models.gem_stat_model([plain])[0]] == [35]


def test_steadfast_counts_while_channelling_the_main_skill_only():
    stats = {"support_steadfast_stun_threshold_+%_final_while_channelling": 50,
             "support_steadfast_ailment_threshold_+%_final_while_channelling": 100}
    channel = models.Skill("Incinerate", group=1, spell=True, support_stats=stats)
    mods, notes = models.gem_stat_model([channel], main=channel)
    assert sorted((m["line"], m["scale"], m.get("skill")) for m in mods) == [
        ("1% more Ailment Threshold while Channelling", 100, None), ("1% more Stun Threshold while Channelling", 50, None)]
    main = models.Skill("Lightning Arrow", group=2, attack=True)
    mods, notes = models.gem_stat_model([main, channel], main=main)  # (PoB's Channelling condition follows the main skill)
    assert mods == [] and all("not on the main skill" in n["basis"] for n in notes if "Steadfast" in n["label"])


def test_thrill_of_the_kill_is_fed_by_culled_adds_and_0_against_a_lone_boss():
    stats = {"support_thrill_of_the_kill_buff_grant_%_added_lightning_attack_damage": 25,
             "support_thrill_of_the_kill_buff_base_duration_ms": 8000, "support_thrill_of_the_kill_buff_shock_chance_+%": 40}
    attack = models.Skill("Lightning Arrow", group=1, attack=True, support_stats=stats)
    mods, notes = models.gem_stat_model([attack], main=attack, stats={})
    assert mods == [] and all("lone boss" in n["basis"] for n in notes if "Thrill" in n["label"]) and len(notes) == 2
    mods, notes = models.gem_stat_model([attack], main=attack, stats={models.CULLS_STAT: 1 / 25})  # (a cull per 25 s)
    assert sorted((m["line"], round(m["scale"], 6), m.get("skill")) for m in mods) == [
        ("1% increased chance to Shock", 12.8, None), ("Gain 1% of Damage as Extra Lightning Damage with Attacks", 8.0, None)]


def test_crazed_minions_count_from_the_builds_own_minion_kills():
    crazed = {"support_crazed_minions_minion_damage_+%_final_if_revived_recently": 30}
    zombies = models.Skill("Raise Zombie", group=1, support_stats=crazed,
                           summon={"persistent": True, "duration": 0, "limit": 4})
    mods, notes = models.gem_stat_model([zombies], main=zombies, stats={})
    assert mods == [] and "nothing in this build kills" in notes[0]["basis"]  # (no source: 0, no flat guess)
    arsonist = models.Skill(models.ARSONIST, group=2, summon={"persistent": True, "duration": 0, "limit": 1})
    mods, notes = models.gem_stat_model([zombies, arsonist], main=zombies, stats={})
    # (a kill every 3 s over 5 minions: 0.222/s, so each dies every 22.5 s, down 7.5 s, alive 15 s: 4/15 of it)
    assert [(m["line"], round(m["scale"], 6), m.get("skill")) for m in mods] == [("Minions deal 1% more Damage", 8.0, "Raise Zombie")]


def test_lockdown_pin_buildup_scales_with_the_boss_distance():
    lock = {"support_lockdown_distance_based_pin_damage_+%_final": 120}
    spear = models.Skill("Spear Throw", group=1, attack=True, support_stats=lock)
    for units, scale in [(20, 120.0), (70, 60.0), (150, None)]:
        mods, notes = models.gem_stat_model([spear], main=spear, stats={}, config={"enemyDistance": units})
        assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == (
            [("1% more Pin Buildup", scale, "Spear Throw")] if scale else [])
    mods, _ = models.gem_stat_model([spear], main=spear, stats={})  # (no distance set: ranged 5 m, 70%)
    assert [m["scale"] for m in mods] == [pytest.approx(84.0)]


def test_support_entries_list_every_stat_their_match_takes():
    from builder.ledger import gem_stat_entries

    for entry in gem_stat_entries():
        if entry.raw.get("support") and entry.replace:
            ids = entry.raw.get("support_stats") or []
            assert ids and all(entry.pattern.search(stat) for stat in ids), entry.id


def test_mana_tempest_empowers_only_mana_costing_spells_and_life_spent_drains_it():
    """MECHC3 (RESEARCH3, mana-tempest-lunar-blessing.md): "Empowers your Mana-costing Spells", so a main skill that costs no
    Mana gets 0; "30% of Mana and Life spent while in the storm" is added to its drain."""
    tempest = models.Skill("Mana Tempest", level_stats={"mana_tempest_mana_cost_%_to_add_to_cost_per_second": 30})
    up, why, _ = models.mana_tempest_uptime(tempest, {"Mana": 2000, "ManaRegenRecovery": 500, "ManaPerSecondCost": 0})
    assert up == 0.0 and "costs no Mana" in why
    mana_only = {"Mana": 2000, "ManaRegenRecovery": 120, "ManaPerSecondCost": 100}
    up_mana = models.mana_tempest_uptime(tempest, mana_only)[0]
    up_life, why, _ = models.mana_tempest_uptime(tempest, {**mana_only, "LifePerSecondCost": 100})
    # drain 20 + 30% of (100 + 100) = 80: 2000 Mana last 2000 / (100 + 80 - 120) = 33.3 s; refill 100 s
    assert up_life == pytest.approx(33.333 / 133.333, rel=1e-3) and up_life < up_mana and "100 Life" in why


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


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
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


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
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
    # MECHC3: the buff refreshes, it doesn't stack: at most 40% of the Life Flask's charges (60 charges: 24%)
    mods, notes = models.sanguine_model(passive, {"Life": 2000, "LifePerSecondCost": 400}, 60)  # 20% of Life/s: 50% uncapped
    assert effective(mods)[0][1] == 24.0 and "capped at 24%" in notes[0]["basis"]
    assert effective(models.sanguine_model(passive, {"Life": 2000, "LifePerSecondCost": 100}, 60)[0])[0][1] == 12.5
    mods, notes = models.sanguine_model(passive, {"Life": 2000, "LifePerSecondCost": 100}, 0)
    assert effective(mods)[0][1] == 0 and "no Life Flask" in notes[0]["basis"]


@pytest.mark.usefixtures("pob_cadence")
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
    land = boss_hits.land_share({"EvadeChance": 50, "EffectiveBlockChance": 50})  # (MECHB6: red-flash hits land)
    assert next(m["value"] for m in few if m["key"] == "triggerRate") == pytest.approx(land / 0.7, rel=1e-3)
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


def test_barrier_invocation_gains_energy_from_the_energy_shield_the_boss_takes(pob_cadence):
    stats = {"EnergyShield": 3000, "enemySkillTime": 0.5, "EvadeChance": 0, "EffectiveBlockChance": 50,
             "PhysicalTakenHit": 600, "FireTakenHit": 400, "EnergyShieldRegenRecovery": 1500}
    gain, basis = models.energy_gain("Barrier Invocation", stats, "Spark", Skill("Spark", spell=True))
    land = boss_hits.land_share(stats)  # (MECHB6: the Arbiter's red-flash hits can't be blocked)
    hit = 600 * 7 / 24 + 400 * 17 / 24  # (MECHB9: each type at its share of the Arbiter's hits, boss_hits.type_taken)
    assert gain == pytest.approx(2 * land * hit * 0.1) and "1 Energy per 10 lost" in basis
    assert models.energy_gain("Barrier Invocation", {**stats, "EnergyShield": 0}, "Spark")[0] == 0.0
    # Only what comes back between hits can be lost again: 300/s regenerated, a hit every second, recharge never starts
    # (2 s delay) -> 300 per hit. A bare 40 Energy Shield with no recovery gives nothing over a fight.
    slow = {**stats, "EnergyShieldRegenRecovery": 300, "EnergyShieldRecharge": 600, "EnergyShieldRechargeDelay": 2}
    assert models.energy_gain("Barrier Invocation", slow, "Spark")[0] == pytest.approx(300 * 0.1)
    gaps = {**slow, "enemySkillTime": 1.5}  # a hit lands every 1.5 / land s: regenerated + recharged after 2 s
    gap = 1.5 / land  # (up to the average hit, MECHB9)
    assert models.energy_gain("Barrier Invocation", gaps, "Spark")[0] == pytest.approx(
        min(hit, 300 * gap + 600 * max(0.0, gap - 2)) / gap * 0.1)
    bare = {"EnergyShield": 40, "enemySkillTime": 0.7, "EffectiveBlockChance": 25, "PhysicalTakenHit": 500,
            "EnergyShieldRecharge": 50, "EnergyShieldRechargeDelay": 2}
    assert models.energy_gain("Barrier Invocation", bare, "Boneshatter")[0] == 0.0
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    _, notes, _ = models.energy_model(Inputs(), {}, [SPARK, Skill("Cast on Dodge", group=2, energy=True), comet], SPARK)
    assert "4.6 m a roll" in notes[0]["basis"] and "up to you" in notes[0]["basis"]  # the guide says it's play-style


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
    # MECHB2: Sacrifice's 40% slower revival (PoB's MinionRevivalSpeed x0.6, which the bridge reports); MECHB5 (AUDIT15
    # A15-2): on the skill's own 12 s base: 12 / 0.6 = 20 s, with the constant's 7.5 / 0.6 = 12.5 s beside it
    slow = models.Skill("Raise Zombie", summon={"limit": 6, "duration": 0, "persistent": True, "undead": True,
                                                "reviveSpeed": 0.6, "reviveBase": 12})
    rate, how = models.minion_deaths([slow, sacrifice, main], main, {"Speed": 2.0})
    assert rate == pytest.approx(6 * 2 / (6 + 20 * 2)) and "12.5 s" in how and "base 12 s" in how and "20 s" in how
    # a skill with its own 12 s beside one without (the Infernal Hound, the djinns: 7.5 s): the count-weighted mean
    own = models.Skill("Raise Zombie", summon={"limit": 6, "duration": 0, "persistent": True, "undead": True,
                                               "reviveBase": 12})
    hound = models.Skill("Summon Infernal Hound", summon={"limit": 2, "duration": 0, "persistent": True})
    assert models.revive_seconds([own, hound])[0] == pytest.approx((6 * 12 + 2 * 7.5) / 8)
    assert models.revive_seconds([hound])[0] == 7.5
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
    # A skill gains Glory once per 0.5 s per monster (KeywordPopups Glory; MECHC2): uses come every 1/U s (MECHC11), so
    # after a gain the ceil(0.5 U)-th use can gain again, then each use with p = E / U
    p = stuns / 2.0
    gains = 2.0 / (1 + (1 - p) / p)
    # ... and none while an instance of the skill is up (MECHC4): Hammer of the Gods' 2.5 s, banners excepted
    hammer = models.glory_uses(["Hammer of the Gods"], stats, slam)
    assert hammer[0] == pytest.approx(1 / (2.5 + 1 / gains)) and "2.5 s instance" in hammer[1]
    assert models.glory_uses(["War Banner"], stats, slam)[0] == pytest.approx(2.0 * 20 / 200)  # every hit: one per 5 s
    fast = models.glory_uses(["War Banner"], {**stats, "Speed": 6.0}, slam)
    assert fast[0] == pytest.approx(2.0 * 20 / 200) and "one gain per 0.5 s" in fast[1]  # every third hit (was 1.7 s)
    odd = models.glory_uses(["War Banner"], {**stats, "Speed": 5.0}, slam)  # every third hit at 5/s: 0.6 s apart
    assert odd[0] == pytest.approx(5.0 / 3 * 20 / 200)
    assert models.glory_gains(1.0, 0.0) == pytest.approx(1 / 1.5)  # rhythm unknown: the random form
    assert models.glory_gains(0.5, 4.0) == pytest.approx(4.0 / (2 + 7))  # 1 use in 8 makes the event
    spell = models.Skill("Spark", spell=True)
    uses, how = models.glory_uses(["War Banner"], {**stats, "PhysicalHitAverage": 0, "LightningHitAverage": 1000}, spell)
    assert uses == 0 and "never" in how  # a spell makes no attack hits
    assert models.glory_uses(["Zerphi's Infamy"], {"LifePerSecondCost": 100}, slam)[0] == pytest.approx(5 / 50)
    assert models.glory_uses(["Skyfall"], stats, slam) == (0.0, "Skyfall: no source counted, never")
    assert models.glory_uses(["Spark"], stats, spell) == (0.0, "no Glory skill")


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
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
    assert scales["Slash"] == 0.0  # (nothing sets its use and the search chose no rate: 0, labelled; MECH55)
    assert "Triggered" not in scales
    assert notes[0]["confidence"] == "LOW" and "no source" in notes[0]["basis"]
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


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
def test_archon_of_the_blizzard_waits_for_a_gap_in_the_boss_s_landing_hits():
    """Energy Shield Recharge needs `delay` s with no damage: every landing boss hit restarts it (MECH46, FLAT_RATES A8:
    was a flat 2 x delay)."""
    import math
    # (Ignite-immune here: the damage over time the boss's hits leave is the next test's, MECHC6)
    base = {"EnergyShield": 1000, "EnergyShieldRechargeDelay": 4.0, "enemySkillTime": 0.7, "IgniteAvoidChance": 100}
    blizzard = lambda stats: next(s for s in models.archon_sources(stats, set()) if s.name == "Archon of the Blizzard")
    rate = 1 / 0.7
    assert blizzard(base).wait == pytest.approx(math.expm1(rate * 4.0) / rate)  # ~209 s: hit often, rarely recharges
    # (MECHC5: 7 of the Arbiter of Ash's 20 skills can't be evaded or blocked; Atziri's phase 1 has none)
    dodgy = dict(base, EvadeChance=50, EffectiveBlockChance=50)
    land = rate * (0.35 + 0.65 / 4)
    assert blizzard(dodgy).wait == pytest.approx(math.expm1(land * 4.0) / land)
    phase1 = dict(dodgy, _boss="Atziri, the Red Queen")  # (her Corrupted Blood: 1 of 28 hits leaves 3 s more, MECHC6)
    q = rate / 4
    assert blizzard(phase1).wait == pytest.approx((27 / 28 * math.exp(q * 4.0) + 1 / 28 * math.exp(q * 7.0) - 1) / q)
    assert blizzard(dict(base, EvadeChance=100, _boss="Atziri, the Red Queen")).wait == pytest.approx(4.0)  # never hit
    assert blizzard(dict(base, EvadeChance=100)).wait > 4.0  # the Arbiter's red-flash hits still land


def test_the_bosss_ignites_delay_energy_shield_recharge():
    """MECHC6 (RESEARCH21 #126): an Ignite from a landed Fire hit keeps removing Energy Shield for 4 s x your Ignite
    duration, so Recharge needs delay + 4 s after it; headline every landed Fire hit Ignites (LOW), less your Ignite
    avoidance, with the "none" figure in the text."""
    import math
    from builder.search import boss_hits

    stats = {"EnergyShield": 1000, "EnergyShieldRechargeDelay": 4.0, "enemySkillTime": 1.0, "EvadeChance": 100}
    land = 7 / 20  # (the Arbiter's red-flash hits)
    p = 17 / 24  # (his Fire hits)
    wait = (1 - p) * math.exp(land * 4.0) + p * math.exp(land * 8.0) - 1
    assert boss_hits.recharge_wait(stats, 4.0, land) == pytest.approx(wait / land)
    assert boss_hits.recharge_wait(stats, 4.0, land, ignite=False) == pytest.approx(math.expm1(land * 4.0) / land)
    half = boss_hits.recharge_wait({**stats, "IgniteAvoidChance": 50, "SelfIgniteDuration": 50}, 4.0, land)
    assert half == pytest.approx(((1 - p / 2) * math.exp(land * 4.0) + p / 2 * math.exp(land * 6.0) - 1) / land)
    text = next(s for s in models.archon_sources(stats, set()) if s.name == "Archon of the Blizzard").basis
    assert "Ignite on 71% of landed hits" in text and "if none did" in text and "grounds" in text  # (no threshold read)
    assert boss_hits.es_dot({"_boss": "Zarokh, the Temporal"}) == []  # (Physical only: no Fire hits)


def test_a_flask_or_charm_counts_whole_only_at_full_uptime():
    """MECHC7 (AUDIT19 A19-1): flasks.ACTIVE_UPTIME is the conditions' FULL_UPTIME (99.5%), not 90%."""
    from builder.search import conditions, flasks

    assert flasks.ACTIVE_UPTIME == conditions.FULL_UPTIME


@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
def test_the_bosss_fire_hits_ignite_you_by_flammability_against_your_ailment_threshold():
    """MECHC7 (AUDIT21 A21-1): KeywordPopups Flammability: 1% Ignite chance per 5% of your Ailment Threshold dealt as Fire,
    stacking over 8 s, raised by the boss skill's increased Ignite chance; a CI build with no other Ailment Threshold
    source (threshold ~0) Ignites every time (A22-4: Energy Shield, Evasion, Dexterity and flat threshold lines raise it,
    and ignite_chance reads PoB's AilmentThreshold, so those builds are computed right)."""
    import math
    from builder.search import boss_hits

    stats = {"EnergyShield": 1000, "EnergyShieldRechargeDelay": 4.0, "enemySkillTime": 1.0, "EvadeChance": 100,
             "AilmentThreshold": 5000, "FireTakenHit": 1000, "Life": 10000}
    land = 7 / 20
    per_hit = 1000 / 5000 * 0.2 * (1 + 20 / 17 / 100)
    chance = per_hit * (1 + land * 17 / 24 * 8)
    assert boss_hits.ignite_chance(stats, land)[0] == pytest.approx(chance)
    p = 17 / 24 * chance
    wait = ((1 - p) * math.exp(land * 4.0) + p * math.exp(land * 8.0) - 1) / land
    assert boss_hits.recharge_wait(stats, 4.0, land) == pytest.approx(wait)
    assert boss_hits.ignite_chance({**stats, "AilmentThreshold": 0.5, "Life": 1}, land)[0] == 1.0  # (CI)
    no_read = {k: v for k, v in stats.items() if k != "AilmentThreshold"}
    assert boss_hits.ignite_chance(no_read, 0.0)[0] == pytest.approx(per_hit)  # (half of Life: 5,000)
    atziri = boss_hits.ignite_chance({**stats, "_boss": "Atziri, the Red Queen"}, 0.0)[0]
    assert atziri == pytest.approx(0.04 * (1 + 80 / 10 / 100))
    text = next(s for s in models.archon_sources(stats, set()) if s.name == "Archon of the Blizzard").basis
    assert "Ailment Threshold" in text and "if none did" in text


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
    # KeywordPopups Repeat: "Triggered skills, Instant skills, and Channelling skills cannot Repeat" (MECHB2, with COV6's
    # RepeatableSpell condition): no Repeat counted, so no Repeat-Echo to take back either
    assert models.repeat_echo_model(texts, both, set(), models.Skill("Spark", spell=True, triggered=True)) == ([], [])
    assert models.repeat_echo_model(texts, both | {"Instant"}, set(), models.Skill("Spark", spell=True)) == ([], [])
    assert models.repeat_echo_model(texts, both | {"Channel"}, set()) == ([], [])
    assert models.repeat_echo_model(texts, both, set(), models.Skill("Spark", spell=True))[0]


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
    # Life / Mana flask effects queue (RESEARCH A5-6; MECHC3, merged in MECHC4): capped by charges only, not 1 / effect
    assert flasks.uses_per_second({**flask, "chargesUsed": 1}, gain, "Ruby Flask", "Life")[0] == pytest.approx(1.0)
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
    (cooldown, else duration, else the search's chosen press rate, else 0: MECH56, the user's "search picks the rate"),
    not one use per 4 s or per 5 s."""
    from types import SimpleNamespace
    from builder.search import conditions
    assert conditions.skill_use_rate({"cooldown": 8.0, "speed": 1.0})[0] == pytest.approx(1 / 8)
    assert conditions.skill_use_rate({"duration": 2.0, "speed": 0.25})[0] == pytest.approx(0.25)  # (capped by its speed)
    each, why, low = conditions.skill_use_rate({"name": "Shout", "speed": 2.0})
    assert each == 0.0 and low and "0 presses" in why
    each, why, low = conditions.skill_use_rate({"name": "Shout", "speed": 2.0}, {"Shout": 0.5})
    assert each == pytest.approx(0.5) and not low and "the search chose" in why
    assert conditions.skill_use_rate({"name": "Shout", "speed": 0.2}, {"Shout": 0.5})[0] == pytest.approx(0.2)

    class Fake:
        objective = SimpleNamespace(press_choices={"Loner": 0.25})

        def call(self, name, indices=()):
            return {"rates": [{"index": 3, "cooldown": 10.0, "speed": 1.0}, {"index": 5, "name": "Pressed", "speed": 1.0}]}

    resolver = conditions.Conditions.__new__(conditions.Conditions)
    resolver.build = Fake()
    groups = [{"index": 3, "enabled": True, "gems": [{"name": "Charged Staff"}]},
              {"index": 4, "enabled": False, "gems": [{"name": "Other"}]},
              {"index": 5, "enabled": True, "gems": [{"name": "Pressed"}]}]
    assert resolver._group_uses(groups, {"Charged Staff", "Other", "Pressed"}) == {
        "Charged Staff": pytest.approx(0.1), "Pressed": 0.0}
    # (a skill no group rate covers: the chosen rate, else 0; before, one use per 5 s)
    assert resolver._chosen_use("Loner") == pytest.approx(0.25) and resolver._chosen_use("Other") == 0.0
    # Marks and other "you use" events read the chosen rate too, else 0
    gems = {"Mark X": {"skillTypes": ["Mark"]}}
    mark_groups = [{"index": 7, "enabled": True, "gems": [{"name": "Mark X"}]}]
    mark_rates = lambda idx: [{"index": 7, "name": "Mark X", "speed": 2.0}]
    text = ["Gain a Power Charge when a Mark activates"]
    assert conditions.event_rates(mark_groups, gems, "Spark", {"Spell"}, 2.0, mark_rates, text)["Mark"][0] == 0.0
    assert conditions.event_rates(mark_groups, gems, "Spark", {"Spell"}, 2.0, mark_rates, text,
                                  {"Mark X": 1.0})["Mark"][0] == pytest.approx(1.0)


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


def test_returning_projectiles_hit_a_lone_boss_again_only_after_piercing_it():
    """MECHB1 (FIX_QUEUE chain / fork / split / pierce / return): KeywordPopups Return; a returned projectile hits the boss
    again only if it pierced it on the way out (LOW), its after-pierce lines applied; Drillneck never returns off one boss."""
    proj = {"Spell", "Projectile", "Cold"}
    frost = models.Skill("Frostbolt", group=1, spell=True)
    gems = [{"name": "Frostbolt", "quality": 20}, {"name": "Breachlord's Amalgam"}, {"name": "Pierce I"}]
    mods, notes = models.return_model([], {"PierceCount": 1}, frost, proj, gems, set())
    assert mods == [models.line("1% more Damage", 100 * 0.8 * 1.2, skill="Frostbolt")]  # Pierce I -20%, quality +20%
    assert notes[0]["confidence"] == "LOW" and "UNVERIFIED" in notes[0]["basis"]
    mods, notes = models.return_model([], {"PierceCount": 0}, frost, proj, gems[:2], set())
    assert mods == [] and notes[0]["value"] == 0  # stops on the boss: no second hit
    mods, _ = models.return_model([], {"PierceCount": 0.5}, frost, proj, gems[:2], set())
    assert mods[0]["scale"] == pytest.approx(60.0)  # 50% pierce chance x quality 20%
    assert models.return_model([], {"PierceCount": 1}, frost, {"Spell", "Projectile", "Fire"}, gems[1:2], set()) == ([], [])
    arrow = models.Skill("Lightning Arrow", group=1, attack=True)
    bow = {"Attack", "Projectile", "Lightning"}
    cadiro = ["Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow"]
    mods, _ = models.return_model(cadiro, {"PierceCount": 0}, arrow, bow, [], {"Bow"})
    assert mods[0]["scale"] == pytest.approx(100 / 6, rel=1e-4)  # Reversing Arrows pierce all targets
    assert models.return_model(cadiro, {"PierceCount": 1}, arrow, bow, [], {"Crossbow"}) == ([], [])  # not arrows
    iron = ["Arrows Return if they have Pierced a target which had Fully Broken Armour"]
    mods, _ = models.return_model(iron, {"PierceCount": 1}, arrow, bow, [], {"Bow"})
    assert mods == [models.line("1% more Damage against Enemies with Fully Broken Armour", 100.0, skill="Lightning Arrow")]
    drill = ["Attack Projectiles Return if they Pierced at least 3 times"]
    mods, notes = models.return_model(drill, {"PierceCount": 5}, arrow, bow, [], {"Bow"})
    assert mods == [] and notes[0]["value"] == 0 and "once" in notes[0]["basis"]
    soaring = models.Skill(models.SOARING_MIDNIGHT, group=1, attack=True)
    mods, _ = models.return_model([], {"PierceCount": 3}, soaring, {"Attack", "Projectile"}, [], {"Shield"})
    assert mods[0]["scale"] == pytest.approx(100.0)


def test_hazards_set_off_in_their_first_half_second_deal_80_percent_less():
    """MECHB2 (FIX_QUEUE, COVC2): "-80% ... damage if destroyed within 0.5 seconds by something other than you" on seven
    player stat sets, which PoB maps to nothing. A hazard placed where the boss stands is set off at once (LOW); an
    arming time of 0.5 s or more, Controlled Hazard or Delayed Reaction keep it from being early; Ice Crystals and Shield
    Wall are taken as broken by you (no penalty); a rearmed trigger is taken as later, at full damage."""
    spearfield = models.Skill("Spearfield", attack=True, early_hazard={"kind": "hazard", "more": -80})
    mods, notes = models.hazard_early_model([spearfield], {})
    assert mods == [models.line("1% more Damage", -80.0, skill="Spearfield")]
    assert notes[0]["value"] == pytest.approx(0.2) and "x5.00" in notes[0]["play"]
    # rearm 30%: PoB x1.3, the game 0.2 + 0.3
    mods, _ = models.hazard_early_model([spearfield], {"HazardRearmChance": 30})
    assert mods[0]["scale"] == pytest.approx(100 * ((0.2 + 0.3) / 1.3 - 1), abs=1e-3)
    shards = models.Skill("Ice Shards", early_hazard={"kind": "hazard", "more": -80, "arming": 1500})
    brambles = models.Skill("Brambles", early_hazard={"kind": "hazard", "more": -80, "arming": 400})
    controlled = models.Skill("Grim Pillars", early_hazard={"kind": "hazard", "more": -80, "delay": 3,
                                                             "delayBy": "Controlled Hazard"})
    delayed = models.Skill("Spearfield", early_hazard={"kind": "hazard", "more": -80, "atEnd": "Delayed Reaction"})
    wall = models.Skill("Frost Wall", spell=True, early_hazard={"kind": "ice_crystals", "more": -80})
    for skill in (shards, controlled, delayed, wall):
        mods, notes = models.hazard_early_model([skill], {})
        assert mods == [] and notes[0]["value"] == 1
    assert models.hazard_early_model([brambles], {})[0][0]["scale"] == pytest.approx(-80.0)
    assert models.hazard_early_model([models.Skill("Glacial Bolt")], {}) == ([], [])  # its Projectile set: no stat
    assert models.hazard_early_model([spearfield], {}, {models.EARLY_HAZARD_ENTRY}) == ([], [])


def test_the_hazard_stat_sets_are_the_game_files():
    """The seven sets the model's comment names carry the stat at -80 (checked against the game files)."""
    import json
    from pathlib import Path

    tables = Path("data/game-26476662/tables")
    if not tables.exists():
        pytest.skip("no game files")
    stats = json.loads((tables / "Stats.json").read_text(encoding="utf-8"))
    ids = {i for i, r in enumerate(stats) if "first_500_ms_when_not_killed_by_self" in r["Id"]}
    sets = json.loads((tables / "GrantedEffectStatSets.json").read_text(encoding="utf-8"))
    found = {r["Id"]: v for r in sets for s, v in zip(r["ConstantStats"], r["ConstantStatsValues"]) if s in ids
             and r["Id"].endswith("Player")}
    assert found == {k: -80 for k in ("FrostWallPlayer", "SpearfieldPlayer", "GlacialBoltWallPlayer",
                                      "IceShardsShardPlayer", "GrimPillarsPlayer", "TriggeredBrambleslamPlayer",
                                      "ShieldWallPlayer")}


def test_cast_on_dodge_reads_the_builds_own_roll():
    """MECHB3 (COVD4): Cast on Dodge's Energy is 2 a metre over the build's own roll, not a fixed 4.6 m. Lioneye's Glare's
    "-1 metre ... if you've Dodge Rolled Recently" always holds for a build that rolls every 2 s, its "+2 metres ... if you
    haven't" never; "while Surrounded" needs 5 enemies (one with "Require 4 fewer"); movement speed scales the distance
    (LOW); "faster Dodge Roll" shortens the roll's time only."""
    plain = models.dodge_roll([], {})
    assert plain["metres"] == pytest.approx(4.6) and plain["seconds"] == pytest.approx(0.75)
    lioneye = models.dodge_roll(["+2 metres to Dodge Roll distance if you haven't Dodge Rolled Recently",
                                 "-1 metre to Dodge Roll distance if you've Dodge Rolled Recently"], {})
    assert lioneye["metres"] == pytest.approx(3.6) and "always rolled recently" in lioneye["basis"]
    gain, basis = models.energy_gain("Cast on Dodge", {"_dodge_roll": lioneye}, "Fireball")
    assert gain == pytest.approx(3.6 * 2 / models.DODGE_EVERY) and "3.6 m" in basis
    assert models.energy_gain("Cast on Dodge", {}, "Fireball")[0] == pytest.approx(4.6 * 2 / models.DODGE_EVERY)
    sigil = models.dodge_roll(["+1 metre to Dodge Roll distance", "30% faster Dodge Roll",
                               "+0.5 metres to Dodge Roll distance while Surrounded"], {"MovementSpeedMod": 1.25})
    assert sigil["base_metres"] == pytest.approx(5.6) and sigil["metres"] == pytest.approx(7.0)
    assert sigil["seconds"] == pytest.approx(0.75 / 1.3) and "Surround" in sigil["basis"]
    alone = models.dodge_roll(["+0.5 metres to Dodge Roll distance while Surrounded", "Require 4 fewer enemies to be Surrounded"], {})
    assert alone["metres"] == pytest.approx(5.1)
    assert models.dodge_roll(["+0.5 metres to Dodge Roll distance while Surrounded",
                              "Require 3 fewer enemies to be Surrounded"], {})["metres"] == pytest.approx(4.6)


def test_guided_hand_gives_a_boosted_slam_30_percent_more_and_triggered_skills_nothing():
    """MECHB3 (RESEARCH11): KeywordPopups AncestralBoost: a boosted Slam has 30% more damage; Triggered Skills cannot be
    Ancestrally Boosted."""
    passive = models.Inputs(flags={"GuidedHand"})
    lines = [{"source": "passive:Guided Hand", "text": "Ancestrally Boosted Attacks deal 30% increased Damage"}]
    stats = {"Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000, "HeavyStunBuildupAvg": 10.0}
    slam = models.Skill("Earthquake", attack=True, melee=True)
    mods, notes = models.guided_hand_model(passive, lines, stats, slam, slam=True)
    more = mods[2]
    assert more["line"] == "1% more Damage" and more["per"] == "GuidedHand" and more["skill"] == "Earthquake"
    assert more["scale"] == pytest.approx(30 / models.UNIT) and "30% more" in notes[0]["value"]
    assert len(models.guided_hand_model(passive, lines, stats, slam)[0]) == 2  # a Strike: no more
    triggered = models.Skill("Earthquake", attack=True, melee=True, triggered=True)
    mods, notes = models.guided_hand_model(passive, lines, stats, triggered, slam=True)
    assert mods == [] and "cannot be Ancestrally Boosted" in notes[0]["basis"]


def test_a_channels_wind_up_is_labelled_not_guessed():
    """MECHB3: Oil Barrage's 350% channel start isn't counted (its length is the build's Power Charge supply): a note."""
    note, = models.channel_windup_note(models.Skill("Oil Barrage", attack=True))
    assert note["confidence"] == "UNVERIFIED" and "Power Charge" in note["basis"] and "too high" in note["basis"]
    assert models.channel_windup_note(models.Skill("Fireball", spell=True)) == []


def test_echoing_cry_counts_each_echo_of_the_warcry():
    """MECHB3 (AUDIT6 A6-2): a Warcry with Echoing Cry hits 1 + 2 times (each with the 40% less counted on the Warcry)."""
    cry = models.Skill("Seismic Cry", group=2)
    other = models.Skill("Infernal Cry", group=3)
    mods, notes = models.warcry_echo_model([cry, other], {2: ["Echoing Cry"], 3: []}, {"Seismic Cry", "Infernal Cry"})
    assert mods == [models.line("1% more Damage", 200, skill="Seismic Cry")]
    assert notes[0]["confidence"] == "UNVERIFIED" and "x3 hits" == notes[0]["value"] and "Without the Echoes" in notes[0]["basis"]

    def part(skill, name):
        assert name == "warcry_echo"
        return {"WarcryEchoes": 3.0, "WarcryEchoDelayMs": 1000.0}, [{"label": "part"}]
    mods, notes = models.warcry_echo_model([cry], {2: ["Echoing Cry"]}, {"Seismic Cry"}, part)
    assert mods[0]["scale"] == 300 and notes[0] == {"label": "part"}
    assert models.warcry_echo_model([cry], {2: ["Echoing Cry"]}, set()) == ([], [])


def test_rapid_shot_fires_only_part_of_the_fight_at_maximum_heat():
    """RESEARCH18 #94: 1 Heat per attack up to 45, 10 s lockout, decay 10/s from 4 s after the last gain. At 8 attacks/s
    with no spender: 45 / 8 = 5.6 s firing per 15.6 s, 36%. A spender taking 15 Heat per use at least as fast: no lockout."""
    main = Skill("Rapid Shot", group=1, attack=True)
    rapid = Skill("Rapid Shot", group=1, attack=True, level_stats={"maximum_number_of_crossbow_heat_stacks": 45,
                                                                 "skill_specific_stat_description_mode": 10000})
    mods, notes = models.heat_model(main, {"Speed": 8}, rapid, [])
    share = (45 / 8) / (45 / 8 + 10)
    assert mods[0]["scale"] == pytest.approx(-100 * (1 - share), abs=0.05) and share == pytest.approx(0.36, abs=0.01)
    mods, notes = models.heat_model(main, {"Speed": 8}, rapid, [("Explosive Grenade", 0.6, "on its cooldown")])
    assert mods == [] and "never reaches maximum" in notes[0]["label"]
    # a slower spender: net gain 8 - 15 x 0.2 = 5/s; the lockout drains 60 + 30 (capped at 45)
    mods, _ = models.heat_model(main, {"Speed": 8}, rapid, [("Explosive Grenade", 0.2, "on its cooldown")])
    assert mods[0]["scale"] == pytest.approx(-100 * (1 - 9 / 19), abs=0.05)
    assert models.heat_model(Skill("Ice Shot", group=1), {"Speed": 8}, rapid, []) == ([], [])


def test_a_pressed_cadence_skill_is_held_to_its_pace():
    """COVE6's open item: a pressed skill with Cadence is used at most 5 times in 4 s (CADENCE_PACE 1.25/s)."""
    rates = [{"index": 1, "name": "Main", "speed": 2.0},
             {"index": 2, "name": "Quick", "speed": 3.0, "cooldown": 0.5, "repeats": 1}]
    plain, _ = models.press_model(rates, 1, {2})
    paced, notes = models.press_model(rates, 1, {2}, {2: models.CADENCE_PACE})
    assert plain["Quick"] == pytest.approx(2.0 / 3.0) and paced["Quick"] == pytest.approx(1.25 / 3.0)
    assert "Cadence" in notes[0]["basis"]


def test_ancestral_bond_places_spell_totems_without_charges_paid_in_spirit():
    """AUDIT13 A13-1 / research #64: with Ancestral Bond a Spell Totem needs no charges (the build isn't zeroed), the
    limit doubles (2 -> 4 at gem level 20), and each totem past PoB's one reservation costs 75 Spirit the build has free."""
    main = Skill("Fireball", group=1, spell=True, cast_time=0.75)
    totem = Skill("Spell Totem", group=1)
    texts = ["Your Totem Limit is doubled", "No Charge requirement for placing Totems", "Totems reserve 75 Spirit each"]
    mods, notes = models.totem_model([main, totem], {"SpiritUnreserved": 160}, texts, {"Spell Totem": 20}, main)
    assert notes[0]["value"] == 3  # 1 + floor(160 / 75), under the doubled 4
    assert scaled(mods, {})["1% more Attack and Cast Speed"] == pytest.approx(200)
    assert "1% more Damage" not in scaled(mods, {})  # no charges consumed: no Power bonus, and not zeroed
    mods, notes = models.totem_model([main, totem], {"SpiritUnreserved": 500}, texts, {"Spell Totem": 20}, main)
    assert notes[0]["value"] == 4
    # without the keystone and no charges: no totem (unchanged)
    mods, notes = models.totem_model([main, totem], {}, [], {"Spell Totem": 20}, main)
    assert notes[0]["value"] == 0


def test_different_non_instant_skills_per_8_seconds():
    """MECHB4 (A7-2): the main spell plus another non-instant spell whose cooldown allows a use per 8 s count 2; a
    triggered, Persistent, instant or long-cooldown spell doesn't; attacks are counted apart."""
    main = Skill("Fireball", group=1, spell=True, cast_time=0.75)
    skills = [main, Skill("Frost Wall", group=2, spell=True, cast_time=0.5, cooldown=3.0),
              Skill("Comet", group=3, spell=True, triggered=True, cast_time=1.2),
              Skill("Herald", group=4, spell=True, cast_time=1.0),
              Skill("Flash", group=5, spell=True, cast_time=0.0),
              Skill("Slow Cooldown", group=6, spell=True, cast_time=1.0, cooldown=12.0),
              Skill("Boneshatter", group=7, attack=True, melee=True, cast_time=1.0)]
    mods, notes = models.different_skills_counts(skills, main, {"Herald"})
    values = {m.get("name"): m["value"] for m in mods}
    assert values["DifferentNonInstantSpells8s"] == 2 and values["DifferentNonInstantAttacks8s"] == 1
    assert values["Multiplier:NonInstantSpellCastRecently"] == 2
    # (MECHB6, A17-2) "Cast Recently" is 4 s: a 6 s cooldown spell counts per 8 s but not Recently
    skills.append(Skill("Six Second", group=8, spell=True, cast_time=0.8, cooldown=6.0))
    values = {m.get("name"): m["value"] for m in models.different_skills_counts(skills, main, {"Herald"})[0]}
    assert values["DifferentNonInstantSpells8s"] == 3 and values["Multiplier:NonInstantSpellCastRecently"] == 2


def test_a_concoction_main_skill_leaves_its_mana_flask_no_charges_for_recovery():
    """MECHC3: the throws take every charge the Mana Flask gains (flask_main's rate comes from them), so its recovery and
    effect uptime aren't counted from the same charges; a Life Flask and any other main skill are untouched."""
    from dataclasses import replace

    from builder.search.flasks import Charges, recovery_per_second, uptime
    flask = {"kind": "Flask", "chargesMax": 75, "chargesUsed": 15, "duration": 3, "manaTotal": 300, "lifeTotal": 300}
    gain = Charges(flask=3.0, mana_cost=500.0, taken_hit=500.0, boss_hits=1.0)
    assert recovery_per_second(flask, gain, "Mana") > 0 and uptime(flask, gain, "Mana") > 0
    thrown = replace(gain, main_skill="Explosive Concoction")
    assert recovery_per_second(flask, thrown, "Mana") == 0.0 and uptime(flask, thrown, "Mana") == 0.0
    assert recovery_per_second(flask, thrown, "Life") == recovery_per_second(flask, gain, "Life") > 0


def test_reverie_s_life_flasks_apply_constantly_and_are_never_used():
    """MECHC3: Reverie ("Cannot use Life Flasks", "Non-Unique Life Flasks apply their Effects constantly", "(40-60)% less
    Life Flask Recovery"; PoB parses none of them): a non-unique Life Flask's recovery is constant, less by the line, and
    "when you use a Life Flask" lines get no uses; flask uses otherwise come from the flask's own charges."""
    from dataclasses import replace

    from builder.search.flasks import Charges, recovery_per_second, uptime, uses_per_second
    life = {"kind": "Flask", "chargesMax": 60, "chargesUsed": 10, "duration": 4, "lifeTotal": 400}
    gain = Charges(flask=1.0)
    assert uses_per_second(life, gain, "", "Life")[0] == pytest.approx(0.1)  # (effects queue: not capped at 1 / 4 s)
    assert recovery_per_second(life, gain, "Life") == pytest.approx(400 * 0.1)
    reverie = replace(gain, life_flask_unusable=True, life_flask_constant=True, life_flask_less=50)
    assert uses_per_second(life, reverie, "", "Life")[0] == 0.0
    assert recovery_per_second(life, reverie, "Life") == pytest.approx(400 * 0.5 / 4) and uptime(life, reverie, "Life") == 1.0
    unique = {**life, "unique": True}
    assert recovery_per_second(unique, reverie, "Life") == 0.0 and uptime(unique, reverie, "Life") == 0.0


def test_a_life_or_mana_flask_effect_ends_when_the_pool_is_full():
    """MECHC4 (RESEARCH12 18ae): a Life flask's effect ends when Life is full, a Mana flask's when Mana is full, unless
    the flask says "Effect is not removed when Unreserved Life (Mana) is Filled" or Enduring Elixirs keeps Life flask
    effects. The share of the fight it can last is (Life lost - regeneration) / the flask's recovery per second; 0 with
    no loss (before: up whenever charges allowed, an overcount of "during Life / Mana Flask effect" lines)."""
    from dataclasses import replace

    from builder.search.flasks import Charges, full_pool_cap, uptime
    life = {"kind": "Flask", "chargesUsed": 10, "duration": 4, "lifeTotal": 800}  # 200 Life/s during its effect
    rich = Charges(flask=5.0)  # charges for it all fight (5/s x 4 s / 10)
    assert uptime(life, rich, "Life") == 0.0  # no hits, no Life costs: Life stays full
    hit = replace(rich, taken_hit=100.0, boss_hits=1.0, life_regen=50.0)  # 100 lost/s, 50 regenerated
    assert full_pool_cap(life, hit, "Life")[0] == pytest.approx(0.25) and uptime(life, hit, "Life") == pytest.approx(0.25)
    assert uptime(life, replace(hit, life_cost=300.0), "Life") == 1.0  # Life costs drain it: never full
    assert uptime(life, replace(hit, mom=50.0), "Life") == 0.0  # half the hits taken from Mana: regen covers the rest
    es = replace(hit, energy_shield=1000.0, es_regen=40.0)
    assert full_pool_cap(life, es, "Life")[0] == pytest.approx(0.05)  # (100 - 40 - 50) / 200
    assert uptime(life, replace(rich, life_effect_kept=True), "Life") == 1.0  # Enduring Elixirs
    assert uptime(life, rich, "Life", "Blood of the Warrior" + chr(10) + "Effect is not removed when Unreserved Life is Filled") == 1.0
    mana = {"kind": "Flask", "chargesUsed": 10, "duration": 4, "manaTotal": 400}  # 100 Mana/s
    assert uptime(mana, replace(rich, mana_cost=80.0, mana_regen=30.0), "Mana") == pytest.approx(0.5)
    assert uptime(mana, replace(rich, mana_cost=80.0), "Mana", "Effect is not removed when Unreserved Mana is Filled") == 1.0
    charm = {"kind": "Charm", "chargesUsed": 10, "duration": 4}
    assert full_pool_cap(charm, rich, None) is None


def test_a_main_skill_that_cannot_generate_frenzy_charges_feeds_no_frenzy_hit_line():
    """MECHC3 (FIX_QUEUE): Ferocity's skill_cannot_generate_frenzy_charges on the main skill: its hits and crits make no
    Frenzy Charges (before, counted: an overcount of Frenzy held and of Ferocity's share); other charges and the boss's
    events (Block) still count."""
    from builder.search import conditions as c
    stats = {"Speed": 3, "HitChance": 100, "CritChance": 50, "EffectiveBlockChance": 30, "_main_no_frenzy": c.NO_FRENZY_STAT}
    assert c._charge_rate("Gain a Frenzy Charge on Critical Hit", "Frenzy", stats)[0] == 0.0
    assert c._charge_rate("Gain a Frenzy Charge on Critical Hit", "Frenzy", {**stats, "_main_no_frenzy": None})[0] == 1.5
    assert c._charge_rate("Gain a Power Charge on Critical Hit", "Power", stats)[0] == 1.5
    assert c._charge_rate("20% chance to gain a Frenzy Charge when you Block", "Frenzy", stats)[0] > 0


def test_other_pressed_skills_share_the_builds_infusions():
    """MECHC4 (COVC4): another pressed skill with an Infused stat set of the same element takes Infusions from the same
    supply, so the main skill gets its share of uses (before, the whole supply)."""
    from types import SimpleNamespace

    from builder.search.assumptions import Assumptions

    gems = {"Fireball": {"skillTypes": ["Spell", "Projectile"], "support": False},
            "Arc": {"skillTypes": ["Spell", "Chains"], "support": False}}
    groups = {"mainSocketGroup": 1, "groups": [
        {"index": 1, "enabled": True, "fullDPS": True, "fromItem": False, "skills": [{"statSets": ["Fire-Infused"]}],
         "gems": [{"name": "Fireball", "enabled": True, "support": False}]},
        {"index": 2, "enabled": True, "fullDPS": False, "fromItem": False, "skills": [{"statSets": ["Fire-Infused Arc"]}],
         "gems": [{"name": "Arc", "enabled": True, "support": False}]}]}
    rates = [{"index": 1, "name": "Fireball", "speed": 2.0}, {"index": 2, "name": "Arc", "speed": 2.0, "cooldown": 2.0}]

    def call(name, **_):
        return groups if name == "groups" else {"rates": rates}

    found = Assumptions(SimpleNamespace(call=call, ctx=SimpleNamespace(gems=gems)))
    uses, how = found.other_infusion_users("Fire")
    assert uses == pytest.approx(0.5) and "Arc 0.50/s" in how  # one press per its 2 s cooldown
    assert found.other_infusion_users("Cold") == (0.0, "")


def test_red_flash_boss_hits_land_whatever_the_evade_and_block():
    """MECHC5 (RESEARCH15 #71): since 0.3.0 evasion works on every boss hit except red-flash skills, which can't be evaded,
    blocked or dodge-rolled; the Arbiter of Ash has 7 of its 20 damaging skills flagged (their share of hits: LOW)."""
    from builder.search import boss_hits
    from builder.search.objective import boss_recovery

    stats = {"EvadeChance": 100, "EffectiveBlockChance": 100, "enemySkillTime": 1.0, "_boss": "Arbiter of Ash"}
    assert boss_hits.land_share(stats) == pytest.approx(7 / 20)
    assert boss_hits.landing_hits(stats) == pytest.approx(0.35)
    assert boss_hits.block_share(stats) == 0.0  # (everything blockable was evaded first)
    assert boss_hits.block_share(stats, evaded_first=False) == pytest.approx(13 / 20)
    # the Bodach's Head Slam and Kulemak's Teleport Slam can be evaded, not blocked
    kulemak = {"EvadeChance": 0, "EffectiveBlockChance": 100, "_boss": "Vessel of Kulemak"}
    assert boss_hits.land_share(kulemak) == pytest.approx(2 / 9)
    assert boss_hits.land_share({"EvadeChance": 50, "_boss": "Vessel of Kulemak"}) == pytest.approx(0.5)
    assert boss_hits.land_share({}) == 1.0
    assert "35%" in boss_hits.note(stats)["value"]
    # the recovery check: a full-evasion build still takes the red-flash hits
    r = boss_recovery({**stats, "PhysicalTakenHit": 1000 * 24 / 7})  # (MECHC6: 7 of the Arbiter's 24 hits are Physical)
    assert r["incoming"] == pytest.approx(350)


def test_recovery_counts_deflection_and_shows_the_figure_without_it():
    """MECHC5 (RESEARCH16 #78): Deflection prevents 40% of a Deflected hit; PoB puts it in TakenHit only at 100% chance, so
    below that the recovery check takes its expected share, against red-flash hits too (LOW), and reports the share
    without it."""
    from builder.search.objective import boss_recovery

    base = {"enemySkillTime": 1.0, "PhysicalTakenHit": 1000 * 24 / 7, "LifeRegenRecovery": 400,  # (7 of 24 hits Physical)
            "_boss": "Arbiter of Ash"}
    r = boss_recovery({**base, "DeflectChance": 50, "DeflectEffect": 40})
    assert r["incoming"] == pytest.approx(800)
    assert r["share"] == pytest.approx(0.5) and r["no_deflect_share"] == pytest.approx(0.4)
    assert boss_recovery({**base, "DeflectChance": 100, "DeflectEffect": 40})["incoming"] == pytest.approx(1000)
    from builder.search.run import recovery_text
    assert "without Deflection 40%" in recovery_text(r)


def test_rage_loss_reads_the_builds_later_faster_and_slower_lines():
    """MECHC5 (RESEARCH17 #89, AUDIT11 A11-2): Anger Management's "200% faster start" makes the 4 s pause 1.33 s; "starts N
    seconds later" adds to it first; "N% slower / faster" scales the 5 Rage/s loss."""
    hold, loss, how = models.rage_loss(["200% faster start of inherent Rage loss"])
    assert hold == pytest.approx(4 / 3) and loss == 5.0 and "faster" in how
    hold, loss, _ = models.rage_loss(["Inherent Rage loss starts 1 second later", "Inherent Rage loss starts 3 seconds later",
                                      "Inherent loss of Rage is 25% slower", "Inherent loss of Rage is 15% slower"])
    assert hold == pytest.approx(8.0) and loss == pytest.approx(3.0)
    assert models.rage_loss([])[:2] == (4.0, 5.0)
    assert "Tribute" in models.rage_loss(["Inherent loss of Rage is 2% slower per 10 Tribute"])[2]
    # the roar's average Rage: a shorter pause keeps less
    assert models.rage_average(10, 30, 8.0, 4 / 3, 5.0) < models.rage_average(10, 30, 8.0)


@pytest.mark.usefixtures("pob_cadence")
def test_rage_held_loss_is_paused_by_landing_hits_at_random():
    """MECHC5: a landing boss hit restarts the pause too, so after a gain's own pause Rage is lost only while no hit landed
    in the last pause: e^(-hits x pause) of the rate. One hit per 4 s no longer holds the maximum."""
    from builder.search import conditions
    line = "Gain 10 Rage when Hit by an Enemy"
    stats = {"MaximumRage": 30, "enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 0}
    if conditions._rage_gain(line, stats, True) is None:
        pytest.skip("the line's wording isn't a Rage source here")
    many = conditions.rage_held([line], stats)[0]
    assert many == 30  # (a hit every second: gains every second, paused)
    few = conditions.rage_held([line], {**stats, "enemySkillTime": 8.0})[0]
    faster = conditions.rage_held([line, "200% faster start of inherent Rage loss"], {**stats, "enemySkillTime": 8.0})[0]
    assert 0 < faster <= few < 30


def test_spear_of_solaris_gains_no_glory_while_its_spear_pulses():
    """MECHC5 (AUDIT11 A11-1, RESEARCH17): the Ground Spear pulses 20 times 0.2 s apart; no Glory while it's up."""
    assert models.GLORY_INSTANCE["Spear of Solaris"] == 4.0
    assert models.GLORY_INSTANCE["Hammer of the Gods"] == 2.5


def test_concoction_throws_read_the_builds_charge_cost_lines_and_crit_charges():
    """MECHC5 (AUDIT14 A14-2, RESEARCH16): reduced / less Flask Charges used and the chance not to consume lower a throw's 5
    charges (MEDIUM); "Gain a Flask Charge when you deal a Critical Hit" adds the main skill's crits to every flask."""
    from builder.search.flasks import Charges, charge_use_rate, charges_per_second, charges_per_use, throw_cost
    assert throw_cost(Charges(), 5) == (5, "")
    cost, how = throw_cost(Charges(mana_used_inc=-20, mana_used_more=0.5, flask_not_consume=10), 5)
    assert cost == pytest.approx(5 * 0.8 * 0.5 * 0.9) and "MEDIUM" in how
    flask = {"kind": "Flask", "chargesMax": 75, "chargesUsed": 15, "duration": 3}
    gain = Charges(flask_on_crit=100, crit_rate=2.0)
    assert charges_per_second(flask, gain, "Mana") == pytest.approx(2.0)
    assert charge_use_rate(flask, gain, "Mana", 4)[0] == pytest.approx(0.5)
    assert charges_per_use(flask, Charges(flask_not_consume=20)) == pytest.approx(12)
    assert charges_per_use({"kind": "Charm", "chargesUsed": 20}, Charges(flask_not_consume=20)) == 20


def test_crown_of_thorns_life_loss_keeps_life_flask_effects_going():
    """MECHC5 (COVD7): "Lose 5 Life when you use a Skill" is Life lost per use of the main skill, so a Life flask's effect
    (which ends at full Life) lasts longer."""
    from builder.search.flasks import Charges, full_pool_cap
    flask = {"kind": "Flask", "lifeTotal": 1000, "duration": 2}
    base = full_pool_cap(flask, Charges(), "Life")[0]
    cap, how = full_pool_cap(flask, Charges(life_loss_per_use=5, main_rate=20), "Life")
    assert base == 0.0 and cap == pytest.approx(100 / 500) and "100/s" in how


def test_the_bridge_s_default_boss_is_the_presets_default():
    """MECH50: bridge.lua's BOSS_DEFAULT (resetConfig with no boss) equals bosses.config_vars(bosses.DEFAULT); since
    MECHD5 the default is the invented worst-case pinnacle (the user, 12 Nov 2026), its hits per type included; the
    presets still hold RESEARCH9's resistances for the Arbiter of Ash (Fire 45, Cold -15, Lightning 0, Chaos 0)."""
    import re
    from pathlib import Path

    from builder.search import bosses

    lua = (Path(__file__).resolve().parents[1] / "engine" / "bridge.lua").read_text(encoding="utf-8")
    table = re.search(r"local BOSS_DEFAULT = \{([^}]*)\}", lua).group(1)
    found = {k: float(v) for k, v in re.findall(r"(\w+) = (-?[\d.]+)", table)}
    assert found == bosses.config_vars(bosses.DEFAULT)
    assert {k: found[k] for k in ("enemyFireResist", "enemyColdResist", "enemyLightningResist", "enemyChaosResist",
                                  "enemySpeed")} == {"enemyFireResist": 45, "enemyColdResist": 45,
                                                     "enemyLightningResist": 45, "enemyChaosResist": 25, "enemySpeed": 510}
    assert bosses.DEFAULT == bosses.WORST and bosses.note()["label"] == "Boss: Pinnacle (worst case)"
    ash = bosses.config_vars("Arbiter of Ash")
    assert (ash["enemyFireResist"], ash["enemyColdResist"], ash["enemyLightningResist"]) == (45, -15, 0)
    # no real pinnacle has Chaos resistance; the worst case's 25 is the user's choice (12 Nov 2026)
    assert all(bosses.PRESETS[n].chaos == 0 for n in bosses.REAL) and len(bosses.REAL) == 12 == len(bosses.PRESETS) - 1


def test_repeatable_spell_is_off_for_triggered_instant_or_channelled_main_spells():
    """RESEARCH6: KeywordPopups Repeat says "Triggered skills, Instant skills, and Channelling skills cannot Repeat", but
    RepeatableSpell (Now and Again's 19% Repeat bonus) went on for any Spell + Multicastable main skill, also one a meta
    gem fires (PoB adds Triggered to it): an overcount."""
    from builder.search.assumptions import Assumptions

    gems = {"Spark": {"skillTypes": ["Spell", "Multicastable", "Projectile"]},
            "Ice Nova": {"skillTypes": ["Spell", "Multicastable", "Instant"]},
            "Cast on Critical": {"skillTypes": ["Meta"]}}

    def found(main, triggered, extra_types=()):
        class Ctx:
            all_gems = {**gems, main: {"skillTypes": [*gems[main]["skillTypes"], *extra_types]}}

        class Build:
            ctx = Ctx()

            def groups(self):
                return [{"index": 1, "gems": [{"name": main}]}]

            def main_group(self):
                return {"index": 1, "gems": [{"name": main}], "skills": [{"name": main}], "mainActiveSkill": 1}

        a = Assumptions.__new__(Assumptions)
        a.build = Build()
        return a.ledger_conditions([{"name": main, "disabled": False, "group": 1, "triggered": triggered}])[0]

    assert "RepeatableSpell" in found("Spark", triggered=False)
    assert "RepeatableSpell" not in found("Spark", triggered=True)  # in a meta gem's group
    assert "RepeatableSpell" not in found("Ice Nova", triggered=False)  # Instant (a guard: no such gem today)
    assert "RepeatableSpell" not in found("Spark", triggered=False, extra_types=["Channel"])


def test_aftershock_chances_add_up_and_their_ailments_land_together():
    """MECHB5 (COVD8, RESEARCH7): the game sums every Aftershock chance and rolls once (capped at one extra, UNVERIFIED);
    the Aftershock I-III supports PoB never reads count; with one Ignite slot the impact and its Aftershock apply it
    1 - (1-p)(1-Ap) times a slam, where PoB's DPS form counts 1 + A."""
    sunder = models.Skill("Sunder", group=1, attack=True)
    slam = {"Attack", "Slam", "Melee", "Area"}
    hymn = ["Slam Skills you use yourself cause an additional Aftershock"]
    stats = {"IgniteChanceOnHit": 50, "IgniteStacksMax": 1}
    mods, notes = models.aftershock_model(hymn, stats, sunder, slam, [], {"Two Hand Mace"})
    # Hymn alone: the ledger's "100% more DPS" is right for hits; Ignite x(2 - 0.5)/2
    assert mods == [models.raw_mod("EnemyIgniteDuration", "MORE", -25.0, "Sunder")]
    assert notes[0]["value"] == "100%" and "x0.750" == notes[1]["value"]
    # Hymn + Earthbreaker's 25% ("more Damage" form): capped at x2, not x2.5
    both = hymn + ["25% chance for Slam Skills you use yourself to cause an additional Aftershock"]
    mods, _ = models.aftershock_model(both, {}, sunder, slam, [], {"Two Hand Mace"})
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (2 / 2.5 - 1), "Sunder")]
    # Aftershock II alone (PoB reads nothing): +35% hits; Ignite at 100% chance gains no applications
    mods, _ = models.aftershock_model([], {"IgniteChanceOnHit": 100}, sunder, slam, [{"name": "Aftershock II"}], set())
    assert mods == [models.raw_mod("DPS", "MORE", 35.0, "Sunder"),
                    models.raw_mod("EnemyIgniteDuration", "MORE", 100 * (1 / 1.35 - 1), "Sunder")]
    # two Poison slots: both applications count
    mods, _ = models.aftershock_model(hymn, {"PoisonChanceOnHit": 50, "PoisonStacksMax": 2}, sunder, slam, [], set())
    assert mods == []
    # a triggered slam: "you use yourself" lines and supports don't count, so the ledger's x2 comes off
    fired = replace(sunder, triggered=True)
    mods, _ = models.aftershock_model(hymn, {}, fired, slam, [{"name": "Aftershock I"}], set())
    assert mods == [models.raw_mod("DPS", "MORE", -50.0, "Sunder")]
    # a mace line without a mace: nothing
    mace = ["20% chance for Mace Slam Skills you use yourself to cause an additional Aftershock"]
    assert models.aftershock_model(mace, {}, sunder, slam, [], {"Talisman"}) == ([], [])
    assert models.aftershock_model(hymn, {}, sunder, {"Attack", "Melee"}, [], set()) == ([], [])  # not a slam


def test_press_hook_caps_combo_walking_and_throw_rates():
    """MECHB5 (COVC6 / COVC7): one hook caps a pressed group at the rate the build feeds it: Combo (0 with no builder),
    walking 10 m before each use (the walk taken from the main skill), Mana Flask charges for a Concoction."""
    rates = [{"index": 1, "name": "Main", "speed": 2.0}, {"index": 2, "name": "Cry", "speed": 1.0, "cooldown": 4.0, "castTime": 1.0}]
    caps = {2: models.PressCap(0.2, "walking 10 m before each use", moving=2.6)}
    scales, notes = models.press_model(rates, 1, {2}, caps=caps)
    assert scales["Cry"] == pytest.approx(0.2)  # its 4 s cooldown would give 0.25/s; the walk holds it to 0.2
    assert scales["Main"] == pytest.approx(1 - 0.2 * 1.0 - 0.2 * 2.6)  # its uses and the walking before each
    assert "walking 10 m" in notes[0]["basis"]
    scales, _ = models.press_model(rates, 1, {2}, caps={2: models.PressCap(0.0, "Combo: no builder")})
    assert scales["Cry"] == 0.0 and scales.get("Main", 1.0) == pytest.approx(1.0)
    # a cap above the press rate changes nothing
    assert models.press_model(rates, 1, {2}, caps={2: models.PressCap(5.0, "x")})[0] == models.press_model(rates, 1, {2})[0]


def test_a_triggered_spells_total_cast_time_addition_counts_double_in_its_energy_cost():
    """MECHB6 (AUDIT17 A17-1, overcount): the game's Energy definition counts "modifiers to Total use time" double, and
    Comet carries "+1 second to Total Cast Time" (total_cast_time_+_ms 1000, game files): base 1 s + 2 x 1 s = 300 Energy
    a trigger, not 100. A spell without one keeps 10 per 0.1 s; Spellslinger's gain from the cast spell counts it too."""
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.0, total_time_added=1.0)
    assert models.energy_cost(comet) == pytest.approx(300)
    assert models.energy_cost(Skill("Spark", cast_time=0.7)) == pytest.approx(70)
    assert Skill.of({"name": "Comet", "castTime": 1, "totalTimeAdded": 1}).total_time_added == 1.0
    coc = Skill("Cast on Critical", group=2, energy=True)
    stats = {"Speed": 2, "HitChance": 100, "CritChance": 50, "LightningCritAverage": 50000,
             "EnemyAilmentThreshold": 100000}
    main = Skill("Arc", group=1, spell=True, cast_time=0.8)
    rate = lambda skills: next(m["value"] for m in models.energy_model(Inputs(), stats, skills, main)[0]
                               if m.get("key") == "triggerRate")
    plain = rate([main, coc, replace(comet, total_time_added=0.0)])
    assert rate([main, coc, comet]) == pytest.approx(plain / 3, rel=1e-3)
    notes = models.energy_model(Inputs(), stats, [main, coc, comet], main)[1]
    assert "300 Energy" in notes[0]["basis"]
    # an Invocation can't store a spell costing more than 500: Crushing Earth 1 s + 2 x 1.5 s = 400 still fits
    crushing = Skill("Crushing Earth", group=2, spell=True, triggered=True, cast_time=1.0, total_time_added=1.5)
    assert models.energy_cost(crushing) == pytest.approx(400) and 400 < models.INVOCATION_MAX_ENERGY
    gain, how = models.energy_gain("Spellslinger", {"Speed": 1}, "Comet", comet)
    assert gain == pytest.approx(0.2 * 3.0 * 100) and "counted double" in how


def test_orb_of_storms_keeps_one_bolt_per_cast_and_says_the_rate_without_a_recast_bolt():
    """MECHB6 (COVC9): one bolt per cast holds only if recasting fires a bolt (UNVERIFIED): the headline keeps it, and
    the note gives the timer-only rate, 3 bolts per 12 s Orb plus the recast."""
    assert models.orb_of_storms_note(Skill("Spark"), {"Speed": 2}) == []
    note = models.orb_of_storms_note(Skill("Orb of Storms", spell=True), {"Speed": 2.0})[0]
    assert note["confidence"] == "UNVERIFIED" and "2.00/s" in note["value"]
    assert f"{3 / 12.5:.2f}/s" in note["value"]


def test_danse_macabre_needs_a_second_skeleton_per_offering_cast():
    """MECHB6 (RESEARCH22 #133): each Danse Macabre Offering uses 2 skeletons and gives nothing without the second; the
    build's skeletons revive 12 s after the last use, so 4 skeletons keep Pain Offering (6 s) up 12 s of every 18 s."""
    pain = Skill("Pain Offering", group=3, spell=True)
    supports = {3: ["Danse Macabre"]}
    one = Skill("Skeletal Warrior", group=1, summon={"limit": 1, "persistent": True, "duration": 0, "reviveBase": 12})
    mods, notes = models.danse_macabre_model([pain, one], supports)
    assert {m["value"] for m in mods} == {-100} and notes[0]["value"] == "0%"
    four = replace(one, summon={"limit": 4, "persistent": True, "duration": 0, "reviveBase": 12})
    mods, notes = models.danse_macabre_model([pain, four], supports)
    assert notes[0]["value"] == "67%" and all(m["value"] == pytest.approx(-100 / 3, abs=0.01) for m in mods)
    assert models.danse_macabre_model([pain, four], {3: []}) == ([], [])
    many = replace(one, summon={"limit": 8, "persistent": True, "duration": 0, "reviveBase": 12})
    assert models.danse_macabre_model([pain, many], supports)[1][0]["value"] == "80%"  # (each use resets the revive)
    quick = replace(one, summon={"limit": 2, "persistent": True, "duration": 0, "reviveBase": 6})
    mods, notes = models.danse_macabre_model([pain, quick], supports)
    assert mods == [] and notes[0]["value"] == "100%"


def test_coursing_current_is_noted_as_zero_on_a_lone_boss():
    assert models.coursing_current_note([Skill("Spark")]) == []
    assert models.coursing_current_note([Skill("Coursing Current", triggered=True)])[0]["value"] == 0


def test_culling_strike_ii_threshold_buff_is_fed_by_culled_adds_for_20_seconds():
    cs = {"support_culling_strike_threshold_+%_on_cull_for_seconds_from_code": 20}
    attack = models.Skill("Spear Throw", group=1, attack=True, support_stats=cs)
    mods, notes = models.gem_stat_model([attack], main=attack, stats={})
    assert mods == [] and any("lone boss" in n["basis"] for n in notes if "Culling Strike II" in n["label"])
    mods, _ = models.gem_stat_model([attack], main=attack, stats={models.CULLS_STAT: 1 / 40})  # (20 s of every 40)
    assert [(m["line"], round(m["scale"], 6), m.get("skill")) for m in mods] == [
        ("1% increased Culling Strike Threshold", 10.0, None)]


def test_profanity_needs_the_builds_own_corpses_and_is_0_without():
    for stats_, label in [({"support_chaos_damage_+%_final_if_corpse_consumed_on_use": 30,
                            "skill_consume_up_to_X_corpses_on_use": 1}, "Profanity I"),
                          ({"support_chaos_damage_+%_final_per_corpse_consumed_on_use": 5,
                            "skill_consume_up_to_X_corpses_on_use": 10}, "Profanity II")]:
        spell = models.Skill("Bone Cage", group=1, support_stats=stats_)
        mods, notes = models.gem_stat_model([spell], main=spell, stats={"Speed": 2})
        assert mods == [] and any("single boss" in n["basis"] for n in notes if label in n["label"])


def test_retaliate_waits_for_the_players_stun_bar_and_scales_with_it():
    ret = {"support_max_attack_damage_+%_final_from_current_poise": 40}
    strike = models.Skill("Falling Thunder", group=1, attack=True, support_stats=ret)
    mods, notes = models.gem_stat_model([strike], main=strike, stats={})
    assert mods == [] and "Stun Bar" in notes[0]["basis"]
    mods, _ = models.gem_stat_model([strike], main=strike, stats={models.STUN_BAR_STAT: 0.25})
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Attack Damage", 10.0, "Falling Thunder")]


def test_encroaching_ground_and_tremors_are_parts_for_their_models():
    ground = models.Skill("Flame Wall", group=1, support_stats={
        "support_ground_effect_area_of_effect_+%_final_per_second": 20,
        "support_ground_effect_area_of_effect_+%_final_per_second_max": 100})
    assert models.support_part_scale(ground, "ground_area")[0] == {"GroundAreaIncPercentPerSecond": 20,
                                                                   "GroundAreaIncPercentMax": 100}
    tremors = {"support_slam_chance_for_one_additional_aftershock_%": 30,
               "support_slam_chance_for_two_additional_aftershocks_%": 15,
               "support_slam_chance_for_three_additional_aftershocks_%": 10, "support_unstable_earth_damage_+%_final": -35}
    slam = models.Skill("Earthquake", group=1, attack=True, support_stats=tremors)
    assert models.support_part_scale(slam, "aftershocks")[0] == {"AftershockChanceOne": 30, "AftershockChanceTwo": 15,
                                                                 "AftershockChanceThree": 10}
    mods, _ = models.gem_stat_model([slam], main=slam, stats={})  # (the 35% less on every use: no part needed)
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Damage", -35.0, "Earthquake")]


def test_acrimony_moves_nothing_the_tool_rates():
    from builder.ledger import gem_stat_entries

    entry = next(e for e in gem_stat_entries() if e.id == "supports-acrimony-enemy-life-regeneration")
    assert not entry.replace and entry.pattern.search(
        "skill_enemies_affected_by_non_ailment_damage_over_time_life_regeneration_rate_+%")


def test_reverberate_and_aftershock_iii_raise_the_summed_aftershock_chance_by_attack_time():
    """MECHB7 (AUDIT19 A19-2): chance_to_aftershock_+%_per_250_ms_attack_time 15 (Aftershock III, Reverberate):
    "Supported Skills have 15% increased chance to cause Aftershocks for every quarter second of Attack time"."""
    sunder = models.Skill("Sunder", group=1, attack=True)
    slam = {"Attack", "Slam", "Melee", "Area"}
    gems = [{"name": "Aftershock I"}, {"name": "Reverberate"}]
    # 1 attack/s: 4 quarters x 15% = 60% increased: 25% -> 40%
    mods, notes = models.aftershock_model([], {"Speed": 1.0}, sunder, slam, gems, set())
    assert mods == [models.raw_mod("DPS", "MORE", 40.0, "Sunder")]
    assert "Reverberate" in notes[0]["basis"]
    # Reverberate alone gives no chance, so nothing
    assert models.aftershock_model([], {"Speed": 1.0}, sunder, slam, [{"name": "Reverberate"}], set()) == ([], [])
    # Aftershock III's own 15%, its 15% per quarter and Reverberate's: 15% x (1 + 8 x 30%) at 2 s per attack = 51%
    gems = [{"name": "Aftershock III"}, {"name": "Reverberate"}]
    mods, _ = models.aftershock_model([], {"Speed": 0.5}, sunder, slam, gems, set())
    assert mods == [models.raw_mod("DPS", "MORE", 15 * (1 + 8 * 0.30), "Sunder")]
    # a triggered slam: Aftershock III doesn't support it, Reverberate does (only the "Slam Skills" line's chance)
    fired = replace(sunder, triggered=True)
    any_slam = ["50% chance for Slam Skills to cause an additional Aftershock"]
    total, why = models.aftershock_chance(any_slam, gems, False, True, False, 1.0)
    assert total == pytest.approx(50 * 1.6) and "Reverberate" in why[-1]
    assert fired.triggered


def test_apocalypse_disasters_fire_a_third_each_while_its_buff_is_up():
    """MECHB7 (kw-unleash item 1, RESEARCH20 #114: PoB gives the three disasters 0 uses): one every 0.75 s while the
    12 s buff is up, a third each; Hailstorm's 9 bolts as 9 hits."""
    mods, notes = models.apocalypse_model(1 / 24, "test")  # up half the fight
    rates = {m["skill"]: m["value"] for m in mods if m["key"] == "triggerRate"}
    each = 0.5 / 0.75 / 3
    assert rates["Meteor"] == pytest.approx(each, rel=1e-4)
    assert rates["Crash Lightning"] == pytest.approx(each, rel=1e-4)
    assert rates["Hailstorm"] == pytest.approx(9 * each, rel=1e-4)
    assert all(m["value"] is True for m in mods if m["key"] == "triggered")
    assert notes[0]["confidence"] == "LOW"
    # kept up all fight, 20% increased frequency; only the sub-skills present get a rate
    mods, _ = models.apocalypse_model(1.0, "test", 20.0, {"Meteor"})
    assert [m["skill"] for m in mods] == ["Meteor", "Meteor"]
    assert mods[0]["value"] == pytest.approx(1.2 / 0.75 / 3, rel=1e-4)
    # no Glory: no disasters
    mods, _ = models.apocalypse_model(0.0, "test")
    assert all(m["value"] == 0 for m in mods if m["key"] == "triggerRate")


def test_boss_hits_per_damage_type_from_the_games_tables():
    """MECHC6 (the manager's ruling ~11:02; RESEARCH13 / RESEARCH14): one Arbiter of Ash hit = level 82 base 353.67 x 4.50
    x 0.65 x the level's more (Elemental x4, Chaos x2); without the 0.65 the harsher reading; the converted-only low bound
    has no level more. PoB gets each type the boss deals at its full size (the bridge's default equals config_vars), and
    sums of damage taken weight each type by its share of the boss's hits."""
    from builder import ENGINE_DIR
    from builder.search import boss_hits

    head = boss_hits.hit_sizes("Arbiter of Ash")
    assert head["Physical"] == pytest.approx(353.67 * 2.925) and head["Fire"] == pytest.approx(353.67 * 11.70)
    assert head["Cold"] == head["Lightning"] == head["Chaos"] == 0.0  # (RESEARCH13: Fire 17, Physical 7 skills)
    harsh = boss_hits.hit_sizes("Arbiter of Ash", "harsh")
    assert harsh["Fire"] == pytest.approx(353.67 * 18.0) and harsh["Physical"] == pytest.approx(353.67 * 4.5)
    assert boss_hits.hit_sizes("Arbiter of Ash", "low")["Fire"] == pytest.approx(head["Physical"])
    king = boss_hits.hit_sizes("The King in the Mists")
    assert king["Chaos"] == pytest.approx(353.67 * 1.88 * 0.65 * 2)
    assert boss_hits.type_shares()["Fire"] == pytest.approx(17 / 24)
    assert boss_hits.type_taken({"PhysicalTakenHit": 700, "FireTakenHit": 1700}) == pytest.approx(700 * 7 / 24 + 1700 * 17 / 24)
    # the bridge's resetConfig default is the Arbiter's config_vars
    source = (ENGINE_DIR / "bridge.lua").read_text(encoding="utf-8")
    block = source[source.index("local BOSS_HITS_DEFAULT"):source.index("}", source.index("local BOSS_HITS_DEFAULT"))]
    for var, value in boss_hits.config_vars(boss_hits.DEFAULT_BOSS).items():
        assert f"{var} = {value}" in block
    # the report row: survives, passes only at the headline, one hit kills
    tough = {"PhysicalMaximumHitTaken": "inf", "FireMaximumHitTaken": 7000}
    assert "survives every reading" in boss_hits.survival_text(tough)[1]
    middle = boss_hits.survival({"PhysicalMaximumHitTaken": 2000, "FireMaximumHitTaken": 5000})
    assert middle["headline_only"] and middle["fails"]["harsh"] == ["Fire"]
    assert "unique 35% less" in boss_hits.survival_text({"PhysicalMaximumHitTaken": 2000, "FireMaximumHitTaken": 5000})[1]
    weak = boss_hits.survival_text({"PhysicalMaximumHitTaken": 2000, "FireMaximumHitTaken": 3000})
    assert "one hit kills: Fire" in weak[1] and "low bound" in weak[1] and weak[2] is None
    assert "4,138" in boss_hits.hits_note()["value"]


def test_flasks_weight_the_damage_taken_per_type_by_the_bosss_hits():
    """MECHC6: the bridge sends each type's damage taken per hit; a flask's Life lost counts the average landed hit."""
    from builder.search.flasks import taken_per_hit

    assert taken_per_hit({"takenHit": 900, "takenByType": {"Physical": 700, "Fire": 1700, "Cold": 0}}) == pytest.approx(
        700 * 7 / 24 + 1700 * 17 / 24)
    assert taken_per_hit({"takenHit": 900}) == 900.0


def test_einhars_beastrite_strike_deals_nothing_to_a_boss_above_low_life():
    strike = models.Skill("Bone Splinter", group=1, attack=True, support_stats={"can_only_damage_low_life_enemies": 1})
    mods, notes = models.gem_stat_model([strike], main=strike, stats={})
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods] == [("1% more Damage", -100.0, "Bone Splinter")]
    assert "Low Life" in notes[0]["basis"] and "play" in notes[0]


@pytest.mark.usefixtures("pob_cadence")
def test_tuls_avalanche_echoes_once_per_seal_from_the_builds_blocks():
    tul = {"skill_echoes_per_seal_broken": 1, "base_maximum_seals_for_skill": 4}
    spell = models.Skill("Frost Bomb", group=1, spell=True, support_stats=tul)
    base = {"Speed": 2.0, "enemySkillTime": 1.0, "EvadeChance": 0, "EffectiveBlockChance": 50}
    mods, _ = models.gem_stat_model([spell], main=spell, stats=base)  # 0.5 blocks/s -> 0.25 Seals/s over 2 casts/s
    assert [(m["line"], round(m["scale"], 6), m.get("skill")) for m in mods] == [("1% more Damage", 12.5, "Frost Bomb")]
    mods, notes = models.gem_stat_model([spell], main=spell, stats={**base, "EffectiveBlockChance": 0})
    assert mods == [] and "Block" in notes[0]["basis"]
    side = models.Skill("Frost Bomb", group=2, spell=True, support_stats=tul)
    assert models.gem_stat_model([side], main=spell, stats=base)[0] == []
    other = models.Skill("Frost Bomb", group=1, spell=True, support_stats={**tul, "base_maximum_seals_for_skill": 7})
    assert models.gem_stat_model([other], main=other, stats=base)[0] == []  # (another Seal support: A13-4)


def test_witchtoads_explode_at_their_curse_areas_share_and_count_as_minions_dying():
    """MECHB8: Doedre's Undoing spawns a Witchtoad every 0.25 s in the cursed area; Dark Consequences explodes each one.
    PoB counts 4 a second whether or not an area is up; the model scales that by the curse's share."""
    despair = Skill("Despair", group=4, spell=True, cast_time=0.7)
    toad = Skill(models.DARK_CONSEQUENCES, group=4, spell=True, triggered=True, cast_time=1.0)
    by_hand = models.CurseUse("Despair", 1.0, "cast by hand", True)
    mods, notes, groups, died = models.toad_model([despair, toad], [by_hand])
    rate = next(m for m in mods if m["key"] == "triggerRate")
    assert rate["skill"] == models.DARK_CONSEQUENCES and rate["value"] == pytest.approx(1.0)
    assert groups == {4} and died == pytest.approx(4.0) and notes[0]["value"] == pytest.approx(4.0)
    meta = models.CurseUse("Despair", 0.25, "triggered")
    mods, _, groups, died = models.toad_model([despair, toad], [meta])
    assert next(m for m in mods if m["key"] == "triggerRate")["value"] == pytest.approx(0.25) and died == pytest.approx(1.0)
    mods, _, groups, died = models.toad_model([despair, toad], [])
    assert groups == set() and died == 0.0  # no curse applied: no area, no toads
    assert models.toad_model([despair], [by_hand]) == ([], [], set(), 0.0)


def test_minion_died_rate_sums_kills_toads_offerings_and_temporary_minions():
    """MECHB8 (#95): "when a Minion Dies" counts expired, despawned and used-up minions, not only Killed ones."""
    skeletons = Skill("Skeletal Warrior", group=2, summon={"persistent": True, "duration": 0, "limit": 4})
    offering = Skill("Pain Offering", group=3, spell=True)
    nothing, how = models.minion_died_rate([skeletons], None, {})
    assert nothing == 0.0 and "not counted" in how
    alone, _ = models.minion_died_rate([skeletons, offering], None, {})
    revive, _ = models.revive_seconds([skeletons])
    assert alone == pytest.approx(4 / max(4 * 6.0, 3 * 6.0 + revive))  # one skeleton a cast, 4 casts a wave
    danse, _ = models.minion_died_rate([skeletons, offering], None, {}, {3: [models.DANSE_MACABRE]})
    assert danse == pytest.approx(4 / max(2 * 6.0, 6.0 + revive))  # two a cast, 2 casts a wave
    toads, how = models.minion_died_rate([skeletons], None, {}, toads=4.0)
    assert toads == pytest.approx(4.0) and "Witchtoads" in how
    ward = Skill("Wardbound Minions", group=5, spell=True, triggered=True, minion={"duration": 15})
    temp, _ = models.minion_died_rate([ward], None, {}, rates={"Wardbound Minions": 0.2})
    assert temp == pytest.approx(0.2)


def test_totem_count_counts_each_totem_up_less_other_totem_skills():
    """MECHB8: PoB prices one totem; the damaging part (Vile Effusion) counts the summoner's Totem Limit, shared with the
    build's other totem skills (KeywordPopups Totem)."""
    effigy = Skill("Dark Effigy", group=1, totem_limit=3)
    effusion = Skill("Vile Effusion", group=1, spell=True)
    mods, notes = models.totem_count_model([effigy, effusion], effusion)
    assert mods[0]["skill"] == "Vile Effusion" and mods[0]["scale"] == pytest.approx(200.0) and notes[0]["value"] == 3
    shockwave = Skill("Shockwave Totem", group=2, totem_limit=3)
    mods, notes = models.totem_count_model([effigy, effusion, shockwave], effusion)
    assert mods[0]["scale"] == pytest.approx(100.0) and "Shockwave Totem" in notes[0]["basis"]
    assert models.totem_count_model([Skill("Dark Effigy", group=1, totem_limit=1), effusion], effusion) == ([], [])
    spell_totem = Skill("Spell Totem", group=1, totem_limit=2)
    assert models.totem_count_model([spell_totem, effusion], effusion) == ([], [])  # (totem_model's)


def test_wildwoods_gifts_blooms_give_flasks_and_charms_charges():
    """MECHC7 (#105): one Ancient Bloom per breach_flame_spawn_rate_ms, a third each kind (LOW-MEDIUM), 5 charges to every
    flask / charm of its kind; the Blooms' increased effect only in the note (UNVERIFIED that it scales the 5)."""
    from builder.search.flasks import Charges, bloom_charges, charge_use_rate, charges_per_second

    gain = Charges.from_bridge({"blooms": {"rate": 1000 / 1210, "life": 5, "mana": 5, "charm": 5, "effect": 100}})
    each = 1000 / 1210 / 3 * 5
    assert bloom_charges(gain, "Mana") == pytest.approx(each)
    assert bloom_charges(gain, "Mana", effect=True) == pytest.approx(2 * each)
    assert charges_per_second({"kind": "Flask"}, gain, "Mana") == pytest.approx(each)
    assert charges_per_second({"kind": "Charm"}, gain, None) == pytest.approx(each)
    rate, how = charge_use_rate({"kind": "Flask"}, gain, "Mana", 5)
    assert rate == pytest.approx(each / 5) and "Ancient Blooms" in how and "UNVERIFIED" in how
    assert bloom_charges(Charges.from_bridge({}), "Life") == 0.0

@pytest.mark.usefixtures("arbiter_default")  # (its numbers are the Arbiter of Ash's: MECHD5)
def test_boss_model_fields_mechd1():
    """MECHD1 (the boss model): one Monster Category per boss (RESEARCH23 #138; the King by form, #147), the King's 1.75
    bars, add kills per preset (AUDIT16 A16-2), the boss's own stun threshold (RESEARCH18 #91) and Heavy Stun duration
    (RESEARCH19 #98), Zarokh's Freeze / Electrocute immunity (AUDIT9 A9-7)."""
    from builder.search import bosses
    p = bosses.PRESETS
    assert p["Arbiter of Ash"].category == "Demon" and p["Olroth, Origin of the Fall"].category == "Undead"
    assert p["Zarokh, the Temporal"].category == "Construct" and p["The Raven Trickster"].category == "Humanoid"
    assert p["The King in the Mists"].category == {"Humanoid": 0.43, "Demon": 0.57}
    assert p["The Aberration"].category == "Demon" and p["Vessel of Kulemak"].poise == 81  # (MECHD2)
    assert p["The King in the Mists"].life == 6271939  # 1.75 x 3,583,965
    king, _ = bosses.kills_per_second("The King in the Mists")
    assert king == pytest.approx(0.43 / 25)
    assert bosses.kills_per_second("Atziri, the Red Queen")[0] == pytest.approx(1 / 45)
    assert bosses.kills_per_second()[0] == 0.0  # the Arbiter of Ash: no adds found
    assert bosses.kill_stats("Atziri, the Red Queen")[models.CULLS_STAT] == pytest.approx(1 / 45)
    # stun: the default's scale is the old Arbiter one; Olroth's lower threshold and longer stun
    stats = {"HeavyStunBuildupAvg": 10, "Speed": 2, "HitChance": 100, "PhysicalHitAverage": 100}
    assert models.ailment_scale(stats, "Freeze") == pytest.approx(models.AILMENT_BOSS)
    olroth = {**stats, "_boss": "Olroth, Origin of the Fall"}
    t = 1 / (0.10 * 938 / 703 * 2.0)
    every = models.repeat_every(t, 6.0, 16.5)
    assert models.heavy_stun_rate(olroth)[0] == pytest.approx(1 / every)
    assert models.heavy_stunned_share(olroth)[0] == pytest.approx(min(1.0, 6.066 / every))
    # Heavy Stunned share at a 2 s fill (A16-3): 6t = 12 s <= 16.5 s, so 4 / 12
    fast = {**stats, "HeavyStunBuildupAvg": 25 / (938 / 1235)}  # 25% of the bar per hit at 2 hits/s: 2 s
    assert models.heavy_stunned_share(fast)[0] == pytest.approx(4 / 12)
    zarokh = {"_boss": "Zarokh, the Temporal", "Speed": 2, "HitChance": 100, "FreezeBuildupAvg": 50,
              "ElectrocuteBuildupAvg": 50, "PinBuildupAvg": 50}
    assert models.buildup_rate(zarokh, "Freeze") == 0.0 and models.buildup_rate(zarokh, "Electrocute") == 0.0
    assert models.buildup_rate(zarokh, "Pin") == 0.0 or models.ailment_scale(zarokh, "Pin") == 1.0



def test_tremors_adds_its_expected_aftershocks_and_lands_ailments_once_per_slam():
    """MECHB9 (COVE10's parts): Tremors' 30 / 15 / 10% for 1 / 2 / 3 more Aftershocks is 0.9 more hits a slam, on top of
    the summed single chance; a one-slot Ignite lands at most once per slam's hits (one roll for Tremors, UNVERIFIED)."""
    tremors = {"support_slam_chance_for_one_additional_aftershock_%": 30,
               "support_slam_chance_for_two_additional_aftershocks_%": 15,
               "support_slam_chance_for_three_additional_aftershocks_%": 10}
    quake = models.Skill("Earthquake", group=1, attack=True, support_stats=tremors)
    slam = {"Attack", "Slam", "Melee", "Area"}
    mods, notes = models.aftershock_model([], {"IgniteChanceOnHit": 50, "IgniteStacksMax": 1}, quake, slam, [], set())
    miss = 0.45 + 0.30 * 0.5 + 0.15 * 0.25 + 0.10 * 0.125  # no Ignite from Tremors' extra hits
    real = (1 - 0.5 * miss) / 0.5
    assert mods == [models.raw_mod("DPS", "MORE", 90.0, "Earthquake"),
                    models.raw_mod("EnemyIgniteDuration", "MORE", round(100 * (real / 1.9 - 1), 3), "Earthquake")]
    assert "Tremors" in notes[0]["value"] and "UNVERIFIED" in notes[0]["basis"]
    # with Aftershock II's 35% as well: 1 + 0.35 + 0.9 hits
    mods, _ = models.aftershock_model([], {}, quake, slam, [{"name": "Aftershock II"}], set())
    assert mods == [models.raw_mod("DPS", "MORE", 125.0, "Earthquake")]


def test_kills_keep_enemies_apart_from_your_own_minions_killed():
    """MECHB9 (the ruling on A17-4): your own minions Killed count as Kills (LOW, with a without row), but never for a
    line that says "an enemy"; the boss preset's adds are the enemies (AddKillsPerSecond, MECH-D's)."""
    assert models.kill_rate({})[:2] == (0.0, 0.0) and models.kills_note(models.kill_rate({})) == []
    enemies, total, how = models.kill_rate({models.ADD_KILLS_STAT: 0.04}, 0.3, "Sacrifice")
    assert (enemies, total) == (0.04, pytest.approx(0.34)) and "A17-4" in how
    note = models.kills_note((enemies, total, how))[0]
    assert note["confidence"] == "LOW" and "without it 0.040/s" in note["play"]
    skeletons = Skill("Skeletal Warrior", group=2, summon={"persistent": True, "duration": 0, "limit": 4, "undead": True})
    bone = Skill("Bone Cage", group=1, spell=True, corpses=True)
    own, own_how = models.minion_deaths([skeletons, bone, Skill("Sacrifice", group=3)], bone, {"Speed": 1.0})
    assert own > 0 and models.kill_rate({}, own, own_how)[:2] == (0.0, pytest.approx(own))


def test_heartbound_loop_hurts_you_at_the_minion_death_rate():
    """MECHB9: "300 Physical Damage taken on Minion Death" at minion_died_rate's rate (any death, not only Killed); two
    rings add up; no deaths, no damage (with a note)."""
    lines = ["300 Physical Damage taken on Minion Death", "1000 Physical Damage taken on Minion Death", "Minions Revive 10% faster"]
    taken, notes = models.heartbound_damage(lines, (0.5, "Offerings"))
    assert taken == pytest.approx(650.0) and notes[0]["confidence"] == "LOW" and "Offerings" in notes[0]["basis"]
    taken, notes = models.heartbound_damage(lines[:1], (0.0, "nothing kills them"))
    assert taken == 0.0 and "costs no Life" in notes[0]["play"]
    assert models.heartbound_damage(lines[2:], (1.0, "")) == (0.0, [])


def test_pustule_cap_holds_the_explosion_to_one_a_fuse():
    """Poison Spores (MECHB10): one Pustule per enemy, bursting after PoB's Detonation Time (15 s base)."""
    from builder.search import models

    mods, notes = models.pustule_cap([{"speed": 1.2, "detonationTime": 15}])
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (1 / 18 - 1), "Poison Spores")] and notes[0]["value"] == "x0.056"
    mods, _ = models.pustule_cap([{"speed": 0.6, "detonationTime": 12}, {"speed": 0.6, "detonationTime": 15}])
    assert abs(mods[0]["value"] - 100 * (1 / 12 / 1.2 - 1)) < 0.01  # (two Spores groups share the boss's one Pustule)
    assert models.pustule_cap([{"speed": 0.05, "detonationTime": 15}]) == ([], [])  # (slower than the fuse: as PoB)
    assert models.pustule_cap([{"speed": 1.0}])[1][0]["basis"].count("PoB gave no Detonation Time") == 1
    found = [models.Skill("Poison Spores", 2, stat_set="Pustule Explosion"), models.Skill("Poison Spores", 3, stat_set="Toxic Pustule")]
    assert models.pustule_groups(found) == [2]


def test_toxic_domain_pustules_are_held_to_one_a_fuse_too():
    """Toxic Domain (AUDIT23 A23-1): "Each enemy can have up to 1 Pustule attached", 15 s fuse, like Poison Spores."""
    from builder.search import models

    found = [models.Skill("Toxic Domain", 4, stat_set="Pustule Explosion"), models.Skill("Poison Spores", 2, stat_set="Pustule Explosion")]
    assert models.pustule_groups(found, "Toxic Domain") == [4] and models.pustule_groups(found) == [2]
    mods, notes = models.pustule_cap([{"speed": 2.0, "detonationTime": 15}], "Toxic Domain")
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (1 / 30 - 1), "Toxic Domain")]
    assert notes[0]["label"].startswith("Toxic Domain")


def test_toxic_growth_bursts_at_most_its_pustule_limit_a_fuse():
    """Toxic Growth (MECHB14): 5 Pustules a cast under a Limit of 5 (a new one makes the oldest disappear), 15 s fuse:
    at most Limit / fuse bursts a second, the Limit from the bridge (quality and "+N to Limit") or the base 5."""
    from builder.search import models

    found = [models.Skill("Toxic Growth", 3, stat_set="Explosion"), models.Skill("Toxic Growth", 5, stat_set="Pustules")]
    assert models.pustule_groups(found, "Toxic Growth") == [3]
    mods, notes = models.pustule_cap([{"speed": 1.5, "detonationTime": 15}], "Toxic Growth")
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (5 / 15 / 1.5 - 1), "Toxic Growth")]
    assert notes[0]["value"] == "x0.222" and "Limit, base" in notes[0]["basis"] and "once every 15 s" in notes[0]["play"]
    mods, notes = models.pustule_cap([{"speed": 1.5, "detonationTime": 15, "pustuleLimit": 7}], "Toxic Growth")
    assert abs(mods[0]["value"] - 100 * (7 / 15 / 1.5 - 1)) < 0.01 and "keeps 7 Pustules" in notes[0]["basis"]
    assert models.pustule_cap([{"speed": 0.3, "detonationTime": 15}], "Toxic Growth") == ([], [])  # (under 5 / 15: as PoB)


def test_a_one_totem_support_keeps_one_totem_up():
    """Splinter Totem I / II, Greatwood II, Cirel's Cultivation: "Supported Skills can only have one active Totem"
    (number_of_totems_allowed_is_1, AUDIT23 A23-3): no extra totems counted, whatever the Totem Limit."""
    from builder.search import models

    hammer = models.Skill("Hammer of the Gods", 1, attack=True)
    totem = models.Skill("Totem", 1, totem_limit=3, support_stats={models.TOTEM_ONE_STAT: 1})
    assert models.totem_count_model([hammer, totem], hammer) == ([], [])
    totem.support_stats = {}
    assert models.totem_count_model([hammer, totem], hammer)[1][0]["value"] == 3


def test_cat_o_nine_tails_low_life_cap_in_max_hit():
    """MECHC8: with LowLifeRecoveryCap the pool loses 65% of Life (Darkness still added); PoB's value kept."""
    stats = {"LowLifeRecoveryCap": 1, "Life": 1000, "Darkness": 0, "PhysicalTotalHitPool": 2000,
             "PhysicalMaximumHitTaken": 4000}
    out = models.with_darkness(dict(stats))
    assert out["PhysicalMaximumHitTaken"] == pytest.approx(4000 * (2000 - 650) / 2000)
    assert out["PoBPhysicalMaximumHitTaken"] == 4000
    assert models.with_darkness({**stats, "LowLifeRecoveryCap": 0})["PhysicalMaximumHitTaken"] == 4000
    assert "Low Life" in models.low_life_cap_note(stats)["label"] and models.low_life_cap_note({}) is None


def test_on_kill_charges_rage_and_onslaught_read_the_builds_kills():
    """MECHC9, AUDIT23 A23-4: on-kill lines read models.kill_rate's (enemies/s, all/s): "enemy" lines the boss's adds only,
    plain "on Kill" lines your own minions Killed too; "Rare or Unique" 0 (labelled); a weapon named must be held. Before,
    every one read 0 ("nothing dies on a single boss")."""
    from builder.search import conditions

    stats = {"_kills": (0.02, 0.1, "adds and minions"), "_weapon_types": ["Two Hand Axe"]}
    rate, how = conditions._charge_rate("25% chance to gain a Power Charge when you kill an enemy", "Power", stats)
    assert rate == pytest.approx(0.25 * 0.02) and "enemies only" in how
    assert conditions._charge_rate("Gain a Frenzy Charge on Kill", "Frenzy", stats)[0] == pytest.approx(0.1)
    assert conditions._charge_rate("Gain a Frenzy Charge on Kill", "Frenzy", {})[0] == 0.0  # (kills not read)
    assert conditions.kill_events("when you kill a rare or unique enemy", stats)[0] == 0.0
    rage, events, _ = conditions._rage_gain("Gain 5 Rage on Kill", stats, True)
    assert (rage, events) == (pytest.approx(0.5), pytest.approx(0.1))
    up, _ = conditions._onslaught_source("You gain Onslaught for 4 seconds on Kill", [], stats)
    assert up == pytest.approx(1 - math.exp(-0.4))
    up, _ = conditions._onslaught_source("50% chance to gain Onslaught on Killing Blow with Axes", [], stats)
    assert up == pytest.approx(1 - math.exp(-0.2))
    no_axe = {**stats, "_weapon_types": ["Quarterstaff"]}
    assert conditions._onslaught_source("50% chance to gain Onslaught on Killing Blow with Axes", [], no_axe)[0] == 0.0
    lone = {"_kills": (0.0, 0.0, "no enemy dies before the boss")}
    assert conditions._onslaught_source("You gain Onslaught for 4 seconds on Kill", [], lone)[0] == 0.0


def test_flasks_and_charms_gain_charges_from_the_bosss_adds():
    """MECHC9, A23-4: KeywordPopups Flask / Charm: a Normal enemy gives half its Power in charges; the adds are taken as
    Normal with Power models.ADD_POWER, before "increased Charges gained". Charges.of reads the request's boss preset."""
    from builder.search import bosses
    from builder.search.flasks import Charges, charge_use_rate, charges_per_second

    flask = {"kind": "Flask", "chargesUsed": 10, "duration": 4, "lifeTotal": 800}
    charm = {"kind": "Charm", "chargesUsed": 20, "duration": 4}
    adds = Charges(add_kills=0.04, flask_gain_inc=50.0)
    assert charges_per_second(flask, adds, "Life") == pytest.approx(0.04 * 0.5 * models.ADD_POWER * 1.5)
    assert charges_per_second(charm, Charges(add_kills=0.04), None) == pytest.approx(0.02)
    assert charges_per_second(flask, Charges(), "Life") == 0.0
    assert "adds killed" in charge_use_rate(flask, adds, "Life", 5)[1]

    class Fake:
        class ctx:
            class request:
                boss = "The King in the Mists"

        def call(self, name, **_):
            return {}

    assert Charges.of(Fake()).add_kills == pytest.approx(bosses.kills_per_second("The King in the Mists")[0])
    assert bosses.kills_per_second("The King in the Mists")[0] > 0


def test_the_builds_own_damage_over_time_counts_in_recovery_and_the_life_flask():
    """MECHC9: PoB's TotalBuildDegen (Heartbound Loop's damage on Minion Death through its degen line) is incoming beside
    the boss's hits in boss_recovery, and Life lost in the Life flask's full-pool cap."""
    from builder.search.flasks import Charges, full_pool_cap
    from builder.search.objective import boss_recovery

    base = {"enemySkillTime": 1.0, "PhysicalTakenHit": 100 * 24 / 7, "_boss": "Arbiter of Ash", "LifeRegenRecovery": 50}
    plain, hurt = boss_recovery(base), boss_recovery({**base, "TotalBuildDegen": 100})
    assert hurt["incoming"] == pytest.approx(plain["incoming"] + 100) and hurt["share"] < plain["share"]
    assert hurt["own_degen"] == 100
    life = {"kind": "Flask", "chargesUsed": 10, "duration": 4, "lifeTotal": 800}  # 200 Life/s
    hit = Charges(flask=5.0, taken_hit=100.0, boss_hits=1.0, life_regen=50.0)
    assert full_pool_cap(life, hit, "Life")[0] == pytest.approx(0.25)
    capped, how = full_pool_cap(life, replace(hit, life_degen=100.0), "Life")
    assert capped == pytest.approx(0.75) and "own damage over time" in how


def test_jiquanis_thesis_first_half_gets_the_whole_line():
    """MECHC9, A12-2: a socketed Jiquani's Thesis reaches PoB as two half-lines; the model hands PoB the whole line (its
    own flag); Waveshaper's Mana Regeneration half and a build without the line get nothing."""
    halves = ["Increases and Reductions to Movement Speed also", " apply to Energy Shield Recharge Rate"]
    mods, notes = models.jiquani_model(halves)
    assert [m["line"] for m in mods] == [models.JIQUANI_LINE] and notes
    assert models.jiquani_model(["Increases and Reductions to Mana Regeneration Rate also"]) == ([], [])
    assert models.jiquani_model(["30% increased Movement Speed"]) == ([], [])


def test_every_boss_preset_has_a_labelled_hit_interval_slower_than_pobs_placeholder():
    """MECHD3 (AUDIT18 A18-2): bosses.HIT_EVERY covers every preset, reads the chosen boss from stats["_boss"], and
    every table estimate is slower than PoB's 0.7 s placeholder; the basis carries LOW and names the boss."""
    from builder.search import bosses
    assert set(bosses.HIT_EVERY) == set(bosses.PRESETS)
    assert all(0.7 < s < 5 for s in bosses.HIT_EVERY.values())
    every, basis = bosses.hit_interval()
    assert every == bosses.HIT_EVERY[bosses.DEFAULT] and "LOW" in basis and bosses.DEFAULT in basis
    assert bosses.hit_interval({"_boss": "The Bodach"})[0] == 1.27


def test_every_boss_preset_has_a_moving_ceiling_and_the_king_s_dashes_share_one_timer():
    """MECHD3 (RESEARCH22 #122, AUDIT18 A18-3): an upper bound per preset for the optional "boss moving" row; the
    King's four dashes on one 6 s timer, first form only (0.43 x 1/6 x 0.5 s)."""
    from builder.search import bosses
    assert set(bosses.MOVING_CEILING) == set(bosses.PRESETS)
    assert all(0 <= s < 1 for s in bosses.MOVING_CEILING.values())
    assert bosses.moving_ceiling({"_boss": "The King in the Mists"})[0] == pytest.approx(0.43 / 6 * 0.5, abs=0.001)
    assert "upper bound" in bosses.moving_ceiling()[1]


def test_two_part_time_flasks_share_the_using_flask_flag():
    """MECHC10: lines on PoB's UsingFlask / UsingCharm (any one active slot sets them) count at the share some slot is
    up, not whole, when two or more slots are part-time (weigh_uptimes switches one off at a time)."""
    from builder.search import flasks
    assert flasks.shared_flag_groups({"Flask 1": 0, "Charm 2": 0}) == {}
    assert flasks.shared_flag_groups({"Flask 1": 0, "Flask 2": 0, "Charm 1": 0, "Charm 3": 0}) == {
        "UsingFlask": ["Flask 1", "Flask 2"], "UsingCharm": ["Charm 1", "Charm 3"]}
    # Each flask alone adds nothing of its own; together they hold a +25% damage line (r = 0.8 with both off)
    factor, note = flasks.shared_flag_factor([0.5, 0.5], [1.0, 1.0], 0.8)
    # true: flag up 75%: 0.75 + 0.25 x 0.8 = 0.95; the product said 1.0
    assert factor == pytest.approx(0.95) and "75%" in note and "100%" in note
    # no shared line (the all-off case is the product of the singles): no change
    assert flasks.shared_flag_factor([0.3, 0.6], [0.9, 0.7], 0.63)[0] == pytest.approx(1.0)
    # exact against the four cases for two slots with their own lines and a shared one
    ua, ub, ra, rb, r0 = 0.4, 0.7, 0.9, 0.95, 0.7
    exact = ua * ub + ua * (1 - ub) * rb + (1 - ua) * ub * ra + (1 - ua) * (1 - ub) * r0
    product = (ua + (1 - ua) * ra) * (ub + (1 - ub) * rb)
    assert product * flasks.shared_flag_factor([ua, ub], [ra, rb], r0)[0] == pytest.approx(exact)


def test_unbound_uptime_from_the_builds_ailments():
    """MECHC10: Unbound Fury 1 per Ignite / Shock / Chill, 6 per Freeze / Electrocute (KeywordPopups UnboundFury);
    100 to use; the cooldown counted from the buff's end (the lower reading; the other as the second figure)."""
    from builder.search import conditions

    stats = {"Speed": 4.0, "HitChance": 100.0, "ShockChancePerHit": 50.0, "ChillChanceOnHit": 0.0}
    up, how, alt = models.unbound_uptime(stats, 9.9, False)
    # 2 Fury/s: 50 s to fill; cycle 9.9 + 50
    assert up == pytest.approx(9.9 / 59.9) and alt == pytest.approx(9.9 / 59.9) and "50.0 s" in how
    fast = {**stats, "ShockChancePerHit": 100.0, "ChillChanceOnHit": 100.0, "Speed": 10.0,
            "ChillHitDamage": 1000.0, "ChillCritDamage": 1500.0, "EnemyAilmentThreshold": 1000.0}  # a 100% Chill
    up, _, alt = models.unbound_uptime(fast, 9.9, False)  # 20 Fury/s: 5 s, under the cooldown
    assert up == pytest.approx(9.9 / 21.9) and alt == pytest.approx(9.9 / 14.9)
    # AUDIT25 A25-3 (MECHC11): a Chill under the game's 30% floor earns no Fury (PoB Chills from 1%)
    weak = {**fast, "ChillHitDamage": 200.0, "ChillCritDamage": 300.0}  # 20% / 30%: crits only, at 25% crit chance
    _, how, alt = models.unbound_uptime({**weak, "CritChance": 25.0}, 9.9, False)  # 10 + 2.5 Fury/s: 8 s
    assert alt == pytest.approx(9.9 / (9.9 + 100 / 12.5)) and "30% Chill floor" in how
    assert models.unbound_uptime({**weak, "CritChance": 0.0}, 9.9, False)[2] == pytest.approx(9.9 / 19.9)  # Shock only
    # with the box on, the 40% more Elemental Damage comes out of the magnitude: 1000 -> 714, still over 30%
    on = models.unbound_uptime({**fast, "ShockChancePerHit": 180.0, "ChillChanceOnHit": 100.0}, 9.9, True)
    assert on[2] == pytest.approx(9.9 / 14.9)
    # taken with the box on: the 80% more chance comes back out
    assert models.unbound_uptime({**stats, "ShockChancePerHit": 90.0}, 9.9, True)[0] == pytest.approx(up_ := 9.9 / 59.9)
    assert models.unbound_uptime({"Speed": 4.0, "HitChance": 100.0}, 9.9, False)[0] == 0
    assert conditions._unbound(["Some other line"], stats) == (0.0, "")
    box = conditions._unbound(["Grants Skill: Unbound Avatar"], {**fast, "_unbound": (9.9, False)})
    assert box[0] is True and box[2] == pytest.approx(9.9 / 21.9) and box[4][0] == pytest.approx(9.9 / 14.9)
    assert [m["when"] for m in models.unbound_model(["Grants Skill: Unbound Avatar"])[0]] == [[{"pob": "Unbound"}]] * 4


def test_a_one_totem_support_holds_pobs_ailment_stacks_to_one_totem():
    """AUDIT24 A24-3 (MECHB11): PoB multiplies a totem skill's ailment stacks by its Totem Limit (CalcOffence 5264); a
    one-totem support's skill gets ActiveTotemLimit BASE (1 - limit), tagged so the bridge's next read leaves it out."""
    from builder.search import models

    slam = models.Skill("Shockwave Slam", 1, attack=True, ailment_totems={"limit": 3},
                        support_stats={models.TOTEM_ONE_STAT: 1})
    mods, notes = models.totem_count_model([slam], slam)
    assert mods == [models.raw_mod("ActiveTotemLimit", "BASE", -2, "Shockwave Slam", tag=models.TOTEM_ONE_TAG)]
    assert mods[0]["tag"] == "OneTotem" and notes[0]["value"] == 1 and "UNVERIFIED" not in notes[0]["basis"]
    slam.support_stats = {models.TOTEM_ONE_STAT: 1, models.TOTEM_SPLINTER_STAT: 2}
    assert "UNVERIFIED" in models.totem_count_model([slam], slam)[1][0]["basis"]  # (splinters: labelled, not counted)
    slam.ailment_totems = {"limit": 1}
    assert models.totem_count_model([slam], slam) == ([], [])  # (already one)
    slam.ailment_totems = {"limit": 3, "override": 2}
    assert models.totem_count_model([slam], slam) == ([], [])  # (the Configuration tab's count wins in PoB)
    # Spell Totem with a one-totem support: one totem, whatever the gem level allows
    totem = models.Skill("Spell Totem", group=1)
    fireball = models.Skill("Fireball", group=1, spell=True, cast_time=0.75, support_stats={models.TOTEM_ONE_STAT: 1})
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "EnduranceChargesMax": 3}
    mods, _ = models.totem_model([totem, fireball], stats, ["Gain an Endurance Charge every second"],
                                 {"Spell Totem": 23}, fireball)
    assert mods == []


def test_minion_pact_kills_a_minion_each_use_and_holds_its_bonus_to_uses_that_find_one():
    """AUDIT23 A23-2 (MECHB11), AUDIT25 A25-2 (MECHB12): Minion Pact removes 100% / 150% of YOUR maximum Life from a nearby
    minion on use; a minion its summoner brings to 0 Life is Killed (KeywordPopups Sacrifice / MinionDeath): one Killed
    per ceil(minion Life / Life removed) uses, capped by revival; none counted when no minion's Life is read. Its 30% more
    is held to the uses that find a minion."""
    from builder.search import models

    zombies = models.Skill("Raise Zombie", summon={"limit": 6, "duration": 0, "persistent": True, "undead": True})
    main = models.Skill("Spark", group=1, spell=True)
    stats = {"Speed": 2.0, "Life": 2000, "_minion_lives": {"Raise Zombie": 1500}}
    assert models.minion_deaths([zombies, main], main, stats)[0] == 0.0  # (without it: nothing kills them)
    assert models.minion_pact_model([zombies, main], main, stats) == ([], [])
    main.support_stats = {models.MINION_PACT_STAT: 100, "support_minion_pact_damage_+%_final": 30}
    rate, how = models.minion_deaths([zombies, main], main, stats)  # (2,000 removed >= 1,500: one use a kill)
    assert rate == pytest.approx(6 * 2 / (6 + 7.5 * 2)) and "Minion Pact" in how and "Without it: 0.00" in how
    mods, notes = models.minion_pact_model([zombies, main], main, stats)
    share = 6 / (6 + 7.5 * 2)  # (a minion is there for this share of uses)
    assert mods == [models.raw_mod("Damage", "MORE", 100 * ((1 + 0.3 * share) / 1.3 - 1), "Spark")]
    assert notes[0]["confidence"].startswith("MEDIUM-LOW")
    # a sturdier minion (5,000 Life against 2,000 removed) dies every 3 uses
    sturdy = {**stats, "_minion_lives": {"Raise Zombie": 5000}}
    rate, how = models.minion_deaths([zombies, main], main, sturdy)
    assert rate == pytest.approx(6 * (2 / 3) / (6 + 7.5 * (2 / 3))) and "every 3 uses" in how
    # no minion Life read: no Kills counted (a note, not a flat guess)
    rate, how = models.minion_deaths([zombies, main], main, {"Speed": 2.0, "Life": 2000})
    assert rate == 0.0 and "no minion's Life" in how
    # no minions at all: PoB gives no bonus either (MinionPresenceCount 0): nothing to cut; temporary minions feed it too
    assert models.minion_pact_model([main], main, stats) == ([], [])
    spirits = models.Skill("Raging Spirits", summon={"limit": 20, "duration": 8, "persistent": False})
    assert models.minion_pact_model([zombies, spirits, main], main, stats) == ([], [])


def test_the_boss_presets_killed_adds_supply_corpses():
    """AUDIT24 A24-1 (MECHB11): a preset's killed adds (AddKillsPerSecond: the King in the Mists, Atziri) are counted
    as one corpse each (LOW, UNVERIFIED whether they leave one), split between the corpse users; a lone boss: 0."""
    detonate = models.Skill("Detonate Dead", group=2, spell=True, triggered=True, corpses=True)
    volatile = models.Skill("Volatile Dead", group=3, spell=True, corpses=True)
    stats = {models.ADD_KILLS_STAT: 0.2, "_boss": "Atziri"}
    assert models.corpse_supply(set(), stats) == pytest.approx(0.2) and models.corpse_supply(set()) == 0.0
    mods, notes = models.corpse_model([detonate, volatile], [], stats)
    rates = {m["skill"]: m["value"] for m in mods if m["key"] == "triggerRate"}
    assert rates == {"Detonate Dead": pytest.approx(0.1), "Volatile Dead": pytest.approx(0.1)}
    assert "UNVERIFIED" in notes[0]["basis"] and notes[0]["confidence"].startswith("LOW")
    mods, notes = models.corpse_model([detonate], [], {})
    assert mods == models.trigger_rate("Detonate Dead", 0) and "no killable adds" in notes[0]["basis"]
    # Profanity's bonus: the main skill's share of uses that find a corpse
    spark = models.Skill("Spark", group=1, spell=True)
    share, why, _ = models.corpse_consumed_share(spark, {**stats, "Speed": 2.0}, spark)
    assert share == pytest.approx(0.1) and "UNVERIFIED" in why

def test_defences_read_the_faster_boss_cadence_and_only_when_hit_benefits_the_slower():
    """MECHD4 (AUDIT25 A25-7, the manager 12 Nov 16:50): PoB's enemySpeed (every defence / recovery reading) is the faster
    of PoB's 0.7 s and HITS_EVERY (combo parts counted as hits, an upper bound); benefit_skill_time rescales the scored
    enemySkillTime to HIT_EVERY (one hit per use, the fewest) for "when hit" benefit counts, never faster."""
    from builder.search import bosses

    for name in bosses.PRESETS:
        assert bosses.HITS_EVERY[name] <= bosses.HIT_EVERY[name]
        assert bosses.config_vars(name)["enemySpeed"] == round(min(0.7, bosses.HITS_EVERY[name]) * 1000) <= 700
    assert bosses.config_vars("The Bodach")["enemySpeed"] == 600
    # the Arbiter, Slowed so PoB reads 0.9 s: benefits read 0.9 x 1.78 / 0.7 (MECHD5: its melee strikes counted)
    ash = {"_boss": "Arbiter of Ash"}
    assert bosses.benefit_skill_time({**ash, "enemySkillTime": 0.9}) == pytest.approx(0.9 * 1.78 / 0.7)
    assert bosses.benefit_skill_time({"enemySkillTime": 0.6, "_boss": "The Bodach"}) == pytest.approx(0.6 * 1.27 / 0.6)
    assert bosses.benefit_skill_time(ash) == pytest.approx(1.78)
    # the worst case (the default since MECHD5): defences at 0.51 s, benefits at the slowest per-use rate, 2.10 s
    assert bosses.benefit_skill_time({}) == pytest.approx(2.10)


def test_melee_uses_count_their_animation_strikes_in_both_cadence_bounds():
    """MECHD5: a melee use counts its animation's strikes (the .ao Melee section's damage patterns) in both bounds, so
    the bosses with multi-strike melee read faster than one hit per use: the Arbiter of Ash 2.25 -> 1.78 s (benefits),
    1.10 -> 1.00 s (the upper bound); the Red Queen's upper bound 0.55 -> 0.51 s reaches PoB's enemySpeed (defences
    harder); spell-only bosses (Xesht, Zarokh, the Raven, the Bodach, Kulemak) and the King don't move."""
    from builder.search import bosses

    assert bosses.HIT_EVERY["Arbiter of Ash"] == 1.78 and bosses.HITS_EVERY["Arbiter of Ash"] == 1.00
    assert bosses.HIT_EVERY["Olroth, Origin of the Fall"] == 1.43 and bosses.HITS_EVERY["Olroth, Origin of the Fall"] == 0.94
    assert bosses.HIT_EVERY["Atziri, the Fell Serpent"] == bosses.HITS_EVERY["Atziri, the Fell Serpent"] == 1.13
    assert bosses.config_vars("Atziri, the Red Queen")["enemySpeed"] == 510
    assert bosses.config_vars("Arbiter of Ash")["enemySpeed"] == 700  # PoB's 0.7 s is still the faster
    for name, (lo, hi) in {"Xesht, We That Are One": (1.25, 0.91), "The Bodach": (1.27, 0.60),
                           "The King in the Mists": (1.53, 1.43), "Vessel of Kulemak": (1.12, 0.91)}.items():
        assert (bosses.HIT_EVERY[name], bosses.HITS_EVERY[name]) == (lo, hi)


def test_the_default_boss_is_an_invented_worst_case_pinnacle():
    """MECHD5 (the user's decision, 12 Nov 2026): the default boss takes, per stat, the value worst for the player among
    the 12 real presets, within reason (no immunities; the Arbiter of Ash's Life), each value's source named."""
    from builder.search import boss_hits, bosses

    w = bosses.PRESETS[bosses.DEFAULT]
    real = [bosses.PRESETS[n] for n in bosses.REAL]
    assert (w.fire, w.cold, w.lightning, w.chaos) == (45, 45, 45, 25)  # (Chaos 25: the user's choice)
    assert bosses.config_vars()["enemyChaosResist"] == 25 and "the user's choice" in w.note
    assert (w.fire, w.cold, w.lightning) == (max(b.fire for b in real), max(b.cold for b in real),
                                             max(b.lightning for b in real))
    assert w.poise == max(b.poise for b in real) == 2275 and w.heavy_stun_s == 4.0
    assert w.es_from_life == 33 and bosses.cull_threshold(w.name) == min(bosses.cull_threshold(n) for n in bosses.REAL)
    assert w.immune == frozenset() and w.kills == () and bosses.kills_per_second(w.name)[0] == 0
    assert w.category is None and w.life == bosses.PRESETS["Arbiter of Ash"].life
    assert bosses.HITS_EVERY[w.name] == min(bosses.HITS_EVERY[n] for n in bosses.REAL) == 0.51
    assert bosses.HIT_EVERY[w.name] == max(bosses.HIT_EVERY[n] for n in bosses.REAL)
    # hits: each type at the biggest any real preset deals; PoB gets them through config_vars
    sizes = bosses.worst_hit_sizes()
    for t in boss_hits.TYPES:
        assert sizes[t] == max(boss_hits.hit_sizes(n)[t] for n in bosses.REAL) > 0
    cv = bosses.config_vars()
    assert cv["enemyColdDamage"] == round(sizes["Cold"]) and cv["enemyPhysicalDamage"] == round(sizes["Physical"])
    assert sum(bosses.worst_type_shares().values()) == pytest.approx(1.0)
    # the unavoidable shares boss_hits reads: the highest of each (Xesht 4 / 11, Kulemak 2 / 9)
    assert boss_hits.shares({}) == pytest.approx((4 / 11, 2 / 9))
    # a named boss's own hits now reach PoB (the bridge gave every boss the Arbiter's before)
    assert bosses.config_vars("Vessel of Kulemak")["enemyLightningDamage"] == boss_hits.config_vars("Vessel of Kulemak")[
        "enemyLightningDamage"] > 0
    assert "Bodach" in w.note and "Kulemak" in w.note and "worst case" in bosses.note()["basis"]


def test_culling_takes_5_percent_of_the_boss_s_life_not_of_its_energy_shield():
    """MECHD4 (A22-5): PoB's CullMultiplier treats the whole pool as Life; Energy Shield (f% of base Life converted, x5
    rarity against Life's x29) isn't Culled, so the threshold PoB is given is 5% x Life / (Life + ES)."""
    from builder.search import bosses

    assert bosses.cull_threshold("Arbiter of Ash") == 5.0  # (no ES)
    atziri = bosses.cull_threshold("Atziri, the Red Queen")  # 33%: ES = 5 x 0.33 / (29 x 0.67) of Life
    assert atziri == pytest.approx(5 * 29 * 0.67 / (29 * 0.67 + 5 * 0.33), rel=1e-3)
    assert all(0 < bosses.cull_threshold(n) <= 5 for n in bosses.PRESETS)
    assert "Culling Strike takes 4.6086% of the pool" in bosses.note("Atziri, the Red Queen")["basis"]


def test_the_boss_ignites_you_for_a_share_of_the_fight():
    """MECHD4 (MECH53's note): the boss's landed Fire hits x the Flammability chance (boss_hits.ignite_chance) x
    (1 - avoidance), 4 s each, as a Poisson stream; 0 for a boss with no Fire hits or full avoidance; a slower cadence
    gives the smaller uptime."""
    from builder.search import bosses

    s = {"enemySkillTime": 0.7, "Life": 5000, "AilmentThreshold": 2500, "FireTakenHit": 3000, "_boss": "Arbiter of Ash"}
    share, how = bosses.self_ignite_uptime(s)  # (read at benefit_skill_time: 1.78 s for the Arbiter)
    assert 0 < share < 1 and "Ignited" in how and "0.56 landed hits/s" in how
    assert bosses.self_ignite_uptime({**s, "enemySkillTime": 0.35})[0] > share
    assert bosses.self_ignite_uptime({**s, "IgniteAvoidChance": 100})[0] == 0
    assert bosses.self_ignite_uptime({**s, "_boss": "Zarokh, the Temporal"})[0] == 0



def test_doom_blast_fires_once_each_time_its_curse_expires():
    """MECHB12: Impending Doom triggers Doom Blast "when Curses from Supported Skills expire"; PoB scored it at its own
    cast speed whenever its group was in Full DPS. A curse cast by hand expires once a cast; one with infinite Duration
    never; a triggered one can't be supported (0)."""
    despair = Skill("Despair", group=4, spell=True, cast_time=0.7)
    blast = Skill(models.DOOM_BLAST, group=4, spell=True, triggered=True, cast_time=1.0)
    by_hand = models.CurseUse("Despair", 1.0, "cast by hand every 7.4 s", True)
    mods, notes, groups = models.doom_blast_model([despair, blast], [by_hand])
    rate = next(m for m in mods if m["key"] == "triggerRate")
    assert rate["skill"] == models.DOOM_BLAST and rate["value"] == pytest.approx(1 / models.CURSE_DURATION, abs=1e-3)
    assert groups == {4} and notes[0]["confidence"] == "MEDIUM"
    forever = models.CurseUse("Despair", 1.0, "cast by hand once: 'Curses you inflict have infinite Duration'")
    mods, notes, groups = models.doom_blast_model([despair, blast], [forever])
    assert next(m for m in mods if m["key"] == "triggerRate")["value"] == 0 and groups == set()
    meta = models.CurseUse("Despair", 0.5, "triggered by Cast on Crit 0.1 times/s")
    mods, notes, groups = models.doom_blast_model([despair, blast], [meta])
    assert next(m for m in mods if m["key"] == "triggerRate")["value"] == 0 and "excludes Triggered" in notes[0]["basis"]
    assert models.doom_blast_model([despair], [by_hand]) == ([], [], set())


def test_a_curses_own_duration_sets_its_uptime_recast_time_and_doom_blasts():
    """AUDIT25 A25-41 (MECHB12): a curse lasts PoB's duration with the build's Curse Duration modifiers (bridge
    activeSkills curseDuration, the stats' _curse_durations), not the level-20 base 7.4 s: a triggered curse's uptime, a
    hand-cast curse's recast cost and Doom Blast's expiries all read it; with none read, the base stays."""
    gems = {"Despair": {"skillTypes": ["Spell", "AppliesCurse"]}, "Cast on Critical": {"skillTypes": ["Meta"]}}
    meta = [{"enabled": True, "gems": [{"name": "Cast on Critical", "support": False}, {"name": "Despair", "support": False}]}]
    longer = {"_curse_durations": {"Despair": 11.1}}
    (use,) = models.curse_uses(meta, gems, longer, 8.0, [], {"Despair": 0.05})
    assert use.share == pytest.approx(0.05 * 11.1) and use.duration == 11.1 and "PoB's" in use.how
    hand = [{"enabled": True, "gems": [{"name": "Despair", "support": False}]}]
    (use,) = models.curse_uses(hand, gems, longer, 8.0)
    assert use.by_hand and use.duration == 11.1
    despair = Skill("Despair", group=4, spell=True, cast_time=0.7)
    main = Skill("Spark", group=1, spell=True)
    mods, notes = models.curse_model([use], [despair, main], main)
    speed = next(m for m in mods if "Attack and Cast Speed" in str(m))
    assert "every 11.1 s" in notes[0]["basis"] and speed is not None
    blast = Skill(models.DOOM_BLAST, group=4, spell=True, triggered=True, cast_time=1.0)
    mods, _, _ = models.doom_blast_model([despair, blast], [use])
    assert next(m for m in mods if m["key"] == "triggerRate")["value"] == pytest.approx(1 / 11.1, abs=1e-3)
    (base,) = models.curse_uses(hand, gems, {}, 8.0)
    assert base.duration == models.CURSE_DURATION and "level-20 base" in base.how



def test_adaptive_skin_raises_only_the_maximums_of_the_elements_the_boss_hits_with(monkeypatch):
    """COV12 (an OVERCOUNT before): Adaptive Skin's "+N% to Maximum Resistances of each Elemental Damage Type you have
    been Hit with Recently" was '+N% to all Maximum Elemental Resistances'. Now one `share:BossHits<Type>` line per
    element. AUDIT25 A25-42 / A25-48: the share of the fight hit with the type Recently (4 s) is 1 - e^(-4 s x rate);
    PoB counts the line whole only at conditions.FULL_UPTIME (99.5%: the floors count only full uptime), else 0 with
    the share kept in ledger_part_time; a boss with no per-type rate in the tables is 0, not always up."""
    import math

    from builder.search import bosses, conditions
    from builder.search.assumptions import Assumptions

    entry, lines = translate("+5% to Maximum Resistances of each Elemental Damage Type you have been Hit with Recently")
    assert entry.id == "adaptive-skin" and lines == [
        "share:BossHitsFire +5% to Maximum Fire Resistance", "share:BossHitsCold +5% to Maximum Cold Resistance",
        "share:BossHitsLightning +5% to Maximum Lightning Resistance"]

    def run(boss):
        class Request:
            pass

        class Ctx:
            all_gems = {"Spark": {"skillTypes": ["Spell", "Projectile"]}}
            request = Request()

        Ctx.request.boss = boss

        class Build:
            ctx = Ctx()

            def groups(self):
                return [{"index": 1, "gems": [{"name": "Spark"}]}]

            def main_group(self):
                return {"index": 1, "gems": [{"name": "Spark"}], "skills": [{"name": "Spark"}], "mainActiveSkill": 1}

            def call(self, name, **_):
                assert name == "sourceLines"
                return {"lines": [{"text": "+5% to Maximum Resistances of each Elemental Damage Type you have been Hit "
                                           "with Recently"}]}

        a = Assumptions.__new__(Assumptions)
        a.build = Build()
        a.ledger_shares = {}
        found = {c for c in a.ledger_conditions([{"name": "Spark", "disabled": False, "group": 1}])[0]
                 if c.startswith("BossHits")}
        return found, {c: v for c, v in a.ledger_shares.items() if c.startswith("BossHits")}, a.ledger_part_time

    # Arbiter of Ash: Fire on 17 of 20 damaging skills, one use every 1.78 s (bosses.HIT_EVERY since MECHD5 counted each
    # melee use's animation strikes; 2.25 s before): Fire about every 2.1 s, 85% up: under 99.5%
    assert bosses.HIT_EVERY["Arbiter of Ash"] == 1.78
    found, shares, part = run("Arbiter of Ash")
    assert found == set() and shares == {} and set(part) == {"BossHitsFire"}  # Cold, Lightning: it deals none
    assert part["BossHitsFire"] == pytest.approx(1 - math.exp(-4 / (1.78 * 20 / 17)))
    # no boss in the request: the default boss, the worst-case pinnacle (MECHD5). It has no RED_FLASH row yet (MECH-C's
    # WORST hook in boss_hits, MECHC11), so its share is 0 with a "no per-type hit rate" note, not the Arbiter's
    assert run(None) == run(bosses.WORST)
    # Xesht: Fire on 2 of 11, one use every 1.25 s
    assert run("Xesht, We That Are One")[2]["BossHitsFire"] == pytest.approx(1 - math.exp(-4 / (1.25 * 11 / 2)))
    for boss, (_, counts) in boss_hits.HIT_TABLE.items():
        found, shares, part = run(boss)
        assert set(part) | found <= {f"BossHits{t}" for t in ("Fire", "Cold", "Lightning") if counts.get(t)}, boss
    # a boss with no per-type rate (not in HIT_EVERY / RED_FLASH): 0, not always up
    assert Assumptions.boss_hit_recently_share("The Trialmaster", "Fire")[0] == 0.0
    # at 99.5% or more (a Fire hit about every 0.75 s or faster) it counts whole in PoB
    monkeypatch.setitem(bosses.HIT_EVERY, "Arbiter of Ash", 0.6)
    found, shares, part = run("Arbiter of Ash")
    assert 1 - math.exp(-4 / (0.6 * 20 / 17)) >= conditions.FULL_UPTIME
    assert found == {"BossHitsFire"} and shares == {"BossHitsFire": 100} and part == {}


def test_a_skill_with_nothing_setting_its_use_is_pressed_at_the_searchs_rate_or_not_at_all():
    """MECH55 (THE USER, 12 Nov: "Search picks the rate"): a pressed skill with no cooldown and no duration counts 0
    presses until the search chooses a rate (build.objective.press_choices); with one, it's pressed at that rate and
    its time comes out of the main skill's share."""
    from types import SimpleNamespace

    rows = [{"index": 1, "name": "Fireball", "speed": 2.0, "castTime": 0.5},
            {"index": 2, "name": "Flame Wall", "speed": 2.0, "castTime": 0.5}]
    uses, how, confidence = models.press_use_rate(rows[1])
    assert uses == 0.0 and confidence == "LOW" and "no source" in how
    scales, _ = models.press_model(rows, 1, {2})
    assert scales["Flame Wall"] == 0.0 and scales.get("Fireball", 1.0) == 1.0
    build = SimpleNamespace(objective=SimpleNamespace(press_choices={"Flame Wall": 0.25}))
    scales, notes = models.press_model(rows, 1, {2}, choices=models.press_choices(build))
    assert scales["Flame Wall"] == pytest.approx(0.25 / 2.0)
    assert scales["Fireball"] == pytest.approx(1 - 0.25 * 0.5)  # (each press takes its 0.5 s from the main skill)
    assert "the search chose" in notes[0]["basis"]
    assert models.press_choices(SimpleNamespace()) == {}


def test_a_side_icestorm_ends_the_main_skills_chill():
    """MECH56 (a small OVERCOUNT before): Icestorm consumes Chill as well as Freeze ("[Chill] and [Freeze] on enemies in
    front of you are [Consume|Consumed]"; its firestorm_improved_bolts_per_chill_consumed stat), so a side Icestorm cuts
    the main skill's Chilled share like any side consumer: share / (1 + share x r x (1 s + the wait to reapply it))."""
    from builder.search import conditions, models
    assert conditions.CONSUME_STATS["Chill"] == ("firestorm_improved_bolts_per_chill_consumed",)
    assert conditions.CONSUME_TEXT.search("Chill and Freeze on enemies in front of you are Consumed to fuel the Icestorm")
    stats = {"ChillChanceOnHit": 100.0, "Speed": 2.0, "HitChance": 100.0, "ChillDuration": 2.0}
    whole = conditions.chill_share(stats)
    assert whole == pytest.approx(1.0)
    cut = conditions.chill_share({**stats, "_side_consumes": {"Chill": (0.5, "Icestorm", "MEDIUM")}})
    assert cut == pytest.approx(1.0 / (1.0 + 0.5 * (models.CONSUME_IMMUNE + 1.0 / 2.0)))
    # (the main skill consuming Chill itself: the side model leaves it alone)
    assert conditions.chill_share({**stats, "_consumes": {"Chill": "x"},
                                   "_side_consumes": {"Chill": (0.5, "Icestorm", "MEDIUM")}}) == pytest.approx(whole)


def test_ward_plan_holds_ward_cost_skills_to_what_runic_ward_regeneration_pays_for():
    """MECHB13 (SEARCH7's Explosive Transmutation finding, an OVERCOUNT): every Ward-cost skill shares the build's Runic
    Ward regeneration; each is held to its planned rate x min(1, regeneration / the Ward all of them would spend)."""
    uses = [models.WardUse("Transmutation Explosion", "triggered", 2.0, 30.0),
            models.WardUse("Frostflame Nova", "main", 1.0, 60.0)]
    share, notes = models.ward_plan(uses, 60.0, 1200.0)  # (they'd spend 120 Ward/s; 60/s regenerates)
    assert share == pytest.approx(0.5)
    assert notes[0]["value"] == "x0.500" and "without this cap" in notes[0]["basis"]
    assert models.ward_plan(uses, 0.0, 0.0)[0] == 0.0  # (no Runic Ward: none of them is used)
    assert models.ward_plan(uses, 500.0, 10000.0)[0] == 1.0
    assert models.ward_plan([], 0.0, 0.0) == (1.0, [])


def test_ward_income_counts_every_runic_ward_source_not_regeneration_alone():
    """MECHB13, the user's rule (12 Nov: "regen is not the only source of recovery, not by a longshot"): Remnants of
    Kalguur from Stuns (50% a Stun, at most one per 3 s) and kills, Ward on Block at the boss's hits, % on Kill; lines no
    rate reads yet are named."""
    kalguur = {"ward_remnants_gain_per_globe": 205, "ward_remnants_chance_to_spawn_remnant_on_stun_%": 50,
               "ward_remnants_spawn_remnant_on_stun_cooldown_ms": 3000,
               "ward_remnants_chance_to_spawn_orb_on_killing_enemy_%": 25}
    texts = ["Recover 1% of maximum Runic Ward on Kill", "Recover 4% of maximum Runic Ward on reaching Maximum Rage"]
    income, parts, unread = models.ward_income(10.0, 1000.0, texts, kalguur=kalguur, stuns=1.0, kills=(0.5, 0.5),
                                               on_block=20, block=50, every=1.0)
    remnants = 1 / (3 + 1 / 0.5) + 0.25 * 0.5
    assert income == pytest.approx(10 + remnants * 205 + 20 * 0.5 + 0.01 * 1000 * 0.5)
    assert len(parts) == 4 and unread == ["Runic Ward on reaching Maximum Rage"]
    assert models.ward_income(0.0, 0.0, [])[0] == 0.0
    share, notes = models.ward_plan([models.WardUse("Skyfall", "main", 1.0, 135.0)], 13.5, 270.0, parts, unread)
    assert share == pytest.approx(0.1) and "Remnants of Kalguur" in notes[0]["basis"]
    assert "Maximum Rage" in notes[0]["basis"]


def test_ward_income_rates_charm_uses_life_flask_recovery_and_life_sacrificed_on_attack():
    """MECHB14 (the user's rule: every recovery source at the rate the build feeds it): Ward a Charm use x the charms'
    uses a second; N% of the Life flask's average recovery (in place of PoB's always-running part); half the Life
    sacrificed on Attack, held to the Life the build recovers."""
    texts = ["Recover 40 Runic Ward when a Charm is used", "20% Life Recovery from Flasks also applies to Runic Ward",
             "Sacrifice 20% of maximum Life to gain half that much Runic Ward when you Attack"]
    income, parts, unread = models.ward_income(
        50.0, 1000.0, texts, charm_uses=0.1, charm_how="Thawing Charm", flask_life=100.0, pob_flask_ward=30.0,
        attacks=2.0, life=2000.0, life_recovery=300.0)
    assert income == pytest.approx((50 - 30) + 0.2 * 100 + 40 * 0.1 + 300 / 2)
    assert unread == [] and len(parts) == 4 and "held to the 300 Life/s" in parts[3]
    assert "less its always-running flask part" in parts[0]
    income, _, _ = models.ward_income(50.0, 1000.0, texts[2:], attacks=0.1, life=2000.0, life_recovery=300.0)
    assert income == pytest.approx(50 + 0.2 * 2000 * 0.1 / 2)  # (40 Life/s sacrificed: recovered in full)
    assert models.ward_income(50.0, 1000.0, texts[:1])[0] == 50.0  # (no charm used: nothing)


def test_living_bomb_reads_a_showaverage_main_skills_damage_per_second():
    """MECHB13 (SEARCH7's routing, train 21's ShowAverage): a ShowAverage main skill's CombinedDPS is one use's damage;
    the seed fills at its damage per second (objective.per_second: TotalDPS in place of the average)."""
    from builder.search.assumptions import living_bomb_rate

    stats = {"EnemyAilmentThreshold": 100000, "ShowAverage": 1, "CombinedDPS": 10000, "AverageDamage": 10000,
             "TotalDPS": 50000, "CullMultiplier": 1, "ReservationDpsMultiplier": 1}
    rate, how = living_bomb_rate(stats, 1.0)
    assert rate == pytest.approx(1 / 5) and "50,000 DPS" in how  # (per use, 10,000, would read one per 16 s)


def test_frostflame_nova_beside_the_main_skill_is_held_to_the_ignite_and_its_casts():
    """MECHB14: a Full DPS / pressed Frostflame Nova: one ice over all its groups, 2 hits/s x the Ignite share, at most
    2 x the Ignite's duration a cast."""
    from builder.search import models

    mods, notes = models.frostflame_plan([(1.2, 0.5)], 0.6, "Fireball 60%")
    assert mods[0]["value"] == pytest.approx(100 * (1.2 / 0.6 - 1)) and notes[0]["value"] == "1.20 hits/s"
    mods, notes = models.frostflame_plan([(1.2, 0.05)], 0.9, "Fireball 90%", ignite_duration=4.0)
    assert mods[0]["value"] == pytest.approx(100 * (2 * 4.0 - 1)) and "held by its casts" in notes[0]["basis"]
    mods, notes = models.frostflame_plan([(1.0, 1.0), (0.5, 1.0)], 0.5, "x")  # (two groups share the one ice)
    assert mods[0]["value"] == pytest.approx(100 * (1.0 / 1.5 - 1), abs=0.01) and "2 groups" in notes[0]["basis"]
    assert models.frostflame_plan([(0.0, 1.0)], 0.5, "x") == ([], [])


def test_frostflame_novas_ice_hits_twice_a_second_while_the_boss_is_ignited():
    """MECHB13 (AUDIT23 A23-1; game files: "Hits enemies every 0.5 seconds until affected Ignite expires", latching only
    onto Ignited enemies): 2 hits/s x the boss's Ignited share from every Full DPS group; 0 with no Ignite source."""
    rates = [{"index": 1, "name": "Frostflame Nova", "speed": 1.4, "stackPotential": {}},
             {"index": 2, "name": "Flame Wall", "speed": 1.0, "stackPotential": {"Ignite": 2.0}}]
    share, how = models.ignite_share(rates, {"Flame Wall": 0.5})
    assert share == pytest.approx(1 - math.exp(-1.0)) and "Flame Wall" in how
    mods, notes = models.frostflame_model(1.4, share, how)
    assert mods[0]["value"] == pytest.approx(100 * (2 * share / 1.4 - 1), abs=1e-3)
    none = models.ignite_share(rates[:1])
    assert none[0] == 0.0 and models.frostflame_model(1.4, *none)[0][0]["value"] == pytest.approx(-100)


@pytest.fixture
def pob_cadence(monkeypatch):
    """The meta Energy "when hit" readers at PoB's scored enemySkillTime, as these tests were written (MECHB13 switched
    them to bosses.benefit_skill_time, one hit per damaging use: test_when_hit_benefits_read_the_bosss_slower_cadence)."""
    from builder.search import bosses
    monkeypatch.setattr(bosses, "benefit_skill_time", lambda stats: float((stats or {}).get("enemySkillTime") or 0.7))


def test_when_hit_benefits_read_the_bosss_slower_cadence():
    """MECHB13 (MECHD4's ruling, AUDIT25 A25-7): meta Energy from Blocks and Ward on Block count the boss's hits at
    bosses.benefit_skill_time (one hit per damaging use: 2.25 s for the default boss against PoB's 0.7 s), never faster
    than the defence cadence. (Iron Ward, Deadly Resolve and Tul's Avalanche are MECH-C's: MECHC11.)"""
    from builder.search import bosses

    every = bosses.benefit_skill_time({"enemySkillTime": 0.7})
    assert every > 0.7
    stats = {"enemySkillTime": 0.7, "EvadeChance": 0, "EffectiveBlockChance": 50}
    gain, basis = models.energy_gain("Cast on Block", stats, "Spark")
    assert f"one every {every:g} s" in basis
    income = models.ward_income(0.0, 0.0, [], on_block=20, block=50, every=every)[0]
    assert income == pytest.approx(20 * 0.5 / every)


def test_light_stuns_count_when_a_hit_is_15_percent_of_the_bosss_life():
    """MECHB13 (the user's Kalguuran Remnants note via the manager, 18:20): bosses take Light Stuns (no stun-immunity mod
    on any pinnacle variety: research/boss-poise-stun-threshold.md); KeywordPopups LightStun: chance = hit / maximum Life,
    Physical x1.5, player Melee x1.5, 0 under 15%. Remnants of Kalguur read Heavy + Light Stuns (models.stun_rate)."""
    from builder.search import bosses

    life = bosses.of({}).life
    small = {"Speed": 2, "HitChance": 100, "FireHitAverage": life * 0.1}
    assert models.light_stun_rate(small)[0] == 0.0  # (10%: under the floor)
    assert models.light_stun_rate(small, melee=True)[0] == pytest.approx(2 * 0.15)  # (x1.5 Melee: 15%)
    big = {"Speed": 1, "HitChance": 100, "PhysicalHitAverage": life * 0.2}
    assert models.light_stun_rate(big)[0] == pytest.approx(0.3)  # (x1.5 Physical)
    total, how = models.stun_rate(big)
    assert total == pytest.approx(0.3) and "Light 0.300/s" in how




def test_escalation_counts_each_different_warcry_at_its_share_of_the_fight_used_recently():
    """COV13 (the user's rule on flat rates): Escalation's "N% increased Damage for each different Warcry you've used
    Recently" was a flat N/2 whenever any Warcry was used Recently. Now `share:WarcriesRecently`: the sum over the build's
    different Warcries of min(1, 4 s x uses per second) (Assumptions.warcries_recently, useRates); none: 0."""
    from builder.search.assumptions import Assumptions

    entry, lines = translate("10% increased Damage for each different Warcry you've used Recently")
    assert entry.id == "escalation" and lines == ["share:WarcriesRecently 10% increased Damage"]

    def run(gems, rates):
        class Ctx:
            all_gems = {"Spark": {"skillTypes": ["Spell"]}, "Cry A": {"skillTypes": ["Warcry"]},
                        "Cry B": {"skillTypes": ["Warcry", "Duration"]}}

        class Build:
            ctx = Ctx()

            def groups(self):
                return [{"index": 1, "gems": [{"name": "Spark"}]}] + [
                    {"index": i, "gems": [{"name": name}]} for i, name in enumerate(gems, start=2)]

            def main_group(self):
                return {"index": 1, "gems": [{"name": "Spark"}], "skills": [{"name": "Spark"}], "mainActiveSkill": 1}

            def call(self, name, **args):
                if name == "useRates":
                    return {"rates": [{"index": i, **rates[i]} for i in args["indices"]]}
                assert name == "sourceLines"
                return {"lines": [{"text": "10% increased Damage for each different Warcry you've used Recently"}]}

        a = Assumptions.__new__(Assumptions)
        a.build = Build()
        a.ledger_shares = {}
        found, notes = a.ledger_conditions([{"name": "Spark", "disabled": False, "group": 1}])
        return "WarcriesRecently" in found, a.ledger_shares.get("WarcriesRecently"), notes

    assert run([], {}) == (False, None, run([], {})[2])  # no Warcry: 0, nothing switched on
    # one on an 8 s cooldown: used in the past 4 s half of the fight
    assert run(["Cry A"], {2: {"cooldown": 8.0}})[:2] == (True, 50)
    # two different ones (8 s and 2 s cooldowns): 0.5 + 1 = 1.5 Warcries Recently on average
    assert run(["Cry A", "Cry B"], {2: {"cooldown": 8.0}, 3: {"cooldown": 2.0}})[:2] == (True, 150)
    # the same Warcry in two groups is one different Warcry (the higher share)
    assert run(["Cry A", "Cry A"], {2: {"cooldown": 8.0}, 3: {"cooldown": 4.0}})[:2] == (True, 100)


def test_rage_lost_on_crit_or_at_the_maximum_lowers_the_rage_held():
    """MECHC11 (COVE11's OVERCOUNTS): Prototype Seventeen loses 100% of maximum Rage on each crit of the supported main
    skill, so the Rage held averages (G / C)(1 - e^(-C M / G)); "Lose all Rage on reaching Maximum Rage" (two uniques,
    PoB parses nothing) holds M / 2 for a build that would reach the maximum, and leaves a lower level alone."""
    import math

    from builder.search import conditions
    stats = {"Speed": 4.0, "HitChance": 100, "CritChance": 25, "MaximumRage": 30, "_main_types": {"Attack", "Melee"},
             "enemySkillTime": 0.7}
    lines = ["Gain 3 Rage on Melee Hit"]
    full, how = conditions.rage_held(lines, stats)
    assert full == 30  # 6 Rage/s (2 gains/s): the loss paused, the maximum
    held, how = conditions.rage_held(lines, {**stats, "_main_rage_lost_on_crit": 100})
    expected = (6 / 1.0) * (1 - math.exp(-1.0 * 30 / 6))  # 1 crit/s
    assert held == float(int(expected)) and "Prototype Seventeen" in how
    assert conditions.rage_held(lines, {**stats, "CritChance": 0, "_main_rage_lost_on_crit": 100})[0] == 30  # no crits
    reset, how = conditions.rage_held([*lines, "Lose all Rage on reaching Maximum Rage"], stats)
    assert reset == 15 and "drops back" in how
    slow = {**stats, "Speed": 0.2, "enemySkillTime": 100.0}  # 3 Rage every 5 s, lost before the next: never at 30
    below = conditions.rage_held(lines, slow)[0]
    assert 0 < below < 30 and conditions.rage_held([*lines, "Lose all Rage on reaching Maximum Rage"], slow)[0] == below


def test_when_hit_benefits_read_one_hit_per_boss_skill_use():
    """MECHC11 (the manager, MECHD4): PoB's enemySkillTime stays at the faster defence cadence (0.7 s for the Arbiter of
    Ash), so "when hit" benefits read bosses.benefit_skill_time instead: one hit per damaging skill use, 2.25 s for the
    Arbiter (x3.2 fewer Rage gains when hit); defences keep enemySkillTime (conditions._hit_recently)."""
    from builder.search import bosses, conditions

    stats = {"enemySkillTime": 0.7, "_boss": "Arbiter of Ash", "EvadeChance": 0, "EffectiveBlockChance": 0,
             "_main_types": {"Attack"}}
    assert bosses.benefit_skill_time(stats) == pytest.approx(2.25)
    rage, events, how = conditions._rage_gain("Gain 5 Rage when Hit by an Enemy", stats, True)
    assert events == pytest.approx(1 / 2.25) and rage == pytest.approx(5 / 2.25)
    other = {**stats, "_boss": "Vessel of Kulemak"}
    assert conditions._rage_gain("Gain 5 Rage when Hit by an Enemy", other, True)[1] == pytest.approx(
        1 / bosses.benefit_skill_time(other))
    assert bosses.benefit_skill_time(other) > 0.7
