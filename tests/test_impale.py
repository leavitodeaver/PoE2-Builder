"""MECH76: Impale (PoB-PoE2 forces ImpaleChance 0; models.impale_model's note gives the game's rules)."""
from __future__ import annotations

import pytest

from builder.search import models


def _row(chance=100.0, crit_chance=None, critc=0.0, effect=1.0, count=1.0, extract=1.0, hit=1000.0, crit=2000.0,
         hits=2.0, speed=2.0):
    return {"LedgerImpaleChance": chance, "LedgerImpaleChanceCrit": chance if crit_chance is None else crit_chance,
            "LedgerImpaleEffect": effect, "LedgerImpaleCount": count, "LedgerImpaleExtract": extract,
            "AverageHit": 100.0, "TotalDPS": 100.0 * hits, "CritChance": critc, "PhysicalHitAverage": hit,
            "PhysicalCritAverage": crit, "speed": speed, "HitChance": 100.0}


def test_every_hit_extracts_the_last_hits_impale():
    dps, how = models.impale_model(_row())
    assert dps == pytest.approx(2.0 * 0.3 * 1000.0) and "Extracted" in how


def test_a_half_chance_extracts_half_the_hits_and_magnitude_scales():
    assert models.impale_model(_row(chance=50.0))[0] == pytest.approx(2.0 * 0.5 * 0.3 * 1000.0)
    assert models.impale_model(_row(effect=2.0))[0] == pytest.approx(2.0 * 0.3 * 2000.0)


def test_more_impales_than_extractions_are_capped_at_one_a_hit():
    assert models.impale_model(_row(count=3.0))[0] == pytest.approx(2.0 * 0.3 * 1000.0)


def test_a_skill_that_cannot_extract_gains_nothing_by_itself():
    dps, how = models.impale_model(_row(extract=0.0))
    assert dps == 0 and "can't Extract" in how
    assert models.impale_model(_row(chance=0.0)) == (0.0, "")


def test_crit_only_impales_store_the_crit_and_non_crits_extract():
    # "Critical Hits inflict Impale" + "Critical Hits cannot Extract Impale": the 25% crits Impale (2000 stored), the
    # 75% non-crits Extract: min(0.25, 0.75) a hit
    row = _row(chance=0.0, crit_chance=100.0, critc=25.0, extract=0.75)
    assert models.impale_model(row)[0] == pytest.approx(2.0 * 0.25 * 0.3 * 2000.0)


def test_the_hand_wrap_lines_become_modifiers():
    mods = models.impale_mods(["Critical Hits inflict Impale", "Critical Hits cannot Extract Impale"])
    assert {(m["name"], m.get("condition")) for m in mods} == {("ImpaleChance", "CriticalStrike"),
                                                             (models.IMPALE_NO_CRIT_FLAG, None)}
    assert models.impale_mods(["10% increased Damage"]) == []


def test_a_spells_impales_are_extracted_by_an_attack_beside_it():
    # main: a Spell Impaling 2 hits/s (can't Extract); beside it an Attack pressed once a second Extracting them
    spell = _row(extract=0.0)
    attack = _row(chance=0.0, extract=1.0, hits=1.0, speed=1.0)
    extra, how = models.impale_others(spell, [("Attack", 1.0, attack)])
    assert extra == pytest.approx(1.0 * 0.3 * 1000.0) and "Extracts 1.00/s" in how
    # the other way round: side skills' Impales fill the main Attack's spare Extractions, strongest first
    main = _row(chance=0.0, extract=1.0, hits=2.0)
    weak, strong = _row(hit=500.0, hits=1.0, speed=1.0, extract=0.0), _row(hit=3000.0, hits=1.0, speed=1.0, extract=0.0)
    extra, _ = models.impale_others(main, [("weak", 2.0, weak), ("strong", 1.0, strong)])
    # capacity 2/s: strong 1/s x 900, then 1 of weak's 2/s x 150
    assert extra == pytest.approx(1.0 * 900.0 + 1.0 * 150.0)


def test_the_main_skills_own_share_is_not_counted_twice():
    extra, _ = models.impale_others(_row(), [])
    assert extra == 0


def test_shattering_roar_joins_the_intimidated_box_as_one_more_source():
    """MECH76 (MECHB30's model, routed): the Skeletal Brutes' Intimidate share combines with the line sources."""
    from builder.search import conditions

    uptime = conditions.debuff_uptime("conditionEnemyIntimidated")
    on, basis, up = uptime([], {"_debuff_extra": {"conditionEnemyIntimidated": (0.5, "Shattering Roar (test)")}})
    assert up == pytest.approx(0.5) and "Shattering Roar" in basis
    assert uptime([], {})[0] == 0.0


def test_chilled_ground_under_the_boss_from_its_freezes(monkeypatch):
    """MECH76 (for COV-A's Desert's Scorn): Frost Nexus 8 s a Freeze, the Invoker line 6 s, a 15% chance 3 s, at the
    boss's Freezes a second; nothing without a source."""
    from builder.search import conditions

    monkeypatch.setattr(models, "buildup_rate", lambda stats, ailment: 0.05 if ailment == "Freeze" else 0.0)
    share, how = conditions.chilled_ground({}, [], {"Frost Nexus"})
    assert share == pytest.approx(0.05 * 8.0) and "Frost Nexus" in how
    share, _ = conditions.chilled_ground({}, ["On Freezing Enemies create Chilled Ground",
                                              "15% chance to create Chilled Ground when you Freeze an Enemy"], set())
    assert share == pytest.approx(1 - (1 - 0.05 * 6.0) * (1 - 0.05 * 3.0 * 0.15))
    assert conditions.chilled_ground({}, ["10% increased Damage"], set()) == (0.0, "")


def test_crackling_palm_bolts_follow_the_main_skills_unarmed_melee_hits():
    """MECH76 (AUDIT35 A35-2 / AUDIT36): a bolt per Unarmed Melee hit, at most 5 every 0.5 s; 0 when not unarmed."""
    from builder.search import conditions

    stats = {"Speed": 4.0, "HitChance": 100.0}
    assert conditions.crackling_palm_rate(stats, {"LedgerUnarmedMelee": 1})[0] == pytest.approx(4.0)
    assert conditions.crackling_palm_rate({"Speed": 20.0, "HitChance": 100.0},
                                          {"LedgerUnarmedMelee": 1})[0] == pytest.approx(10.0)
    assert conditions.crackling_palm_rate(stats, {"LedgerUnarmedMelee": 0})[0] == 0.0


def test_each_boss_preset_has_a_melee_share_and_the_worst_case_takes_the_highest():
    """MECH76 (the manager 03:52, for MECH-C's Stuns on you: monster Melee 33% more)."""
    from builder.search import bosses

    real = [name for name in bosses.PRESETS if name != bosses.WORST]
    assert set(real) <= set(bosses.MELEE_SHARE)
    worst, how = bosses.melee_share({"_boss": bosses.WORST})
    assert worst == max(share for share, _ in bosses.MELEE_SHARE.values()) and "worst case" in how
    assert bosses.melee_share({"_boss": "Zarokh, the Temporal"})[0] == 0.0


def test_ailments_applied_recently_from_the_hits_in_the_past_four_seconds(monkeypatch):
    """MECH76 (PATCH_CHECK MC-6): "if you've Frozen / Chilled / Ignited / Shocked an Enemy Recently" boxes: the share of
    the fight with an application in the past 4 s; other groups' Ignites and an Igniting aura count; 0 with no source."""
    import math

    from builder.search import conditions

    stats = {"Speed": 2.0, "HitChance": 100.0, "IgniteChancePerHit": 25.0, "ShockChancePerHit": 0.0}
    share, _ = conditions.applied_recently(stats, "Ignite")
    assert share == pytest.approx(1 - 0.75 ** 8)
    assert conditions.applied_recently(stats, "Shock")[0] == 0.0
    others = {**stats, "_ailment_others": {"Ignite": [("Fireball", 1.0, 0.5, 4.0)]}}
    assert conditions.applied_recently(others, "Ignite")[0] == pytest.approx(1 - 0.75 ** 8 * 0.5 ** 4)
    aura = {"Speed": 0.0, "_ailment_ground": {"Ignite": (0.6, "Radiant Grief")}}
    assert conditions.applied_recently(aura, "Ignite")[0] == pytest.approx(0.6)
    monkeypatch.setattr(models, "buildup_rate", lambda s, a: 0.1)
    assert conditions.applied_recently({}, "Freeze")[0] == pytest.approx(1 - math.exp(-0.4))
    on, basis, up = conditions._applied_recently("conditionIgnitedEnemyRecently")([], stats)
    assert on and up == pytest.approx(share) and "past 4 s" in basis
    assert {c.var for c in conditions.CONDITIONS} >= set(conditions.APPLIED_RECENTLY)


def test_clash_counts_the_share_of_the_fight_the_boss_has_more_life_than_you():
    """MECH76 (PATCH_CHECK MA-13): a full-Life build gets ~0 (PoB's 20% less), a build at 35% unreserved Life 65%."""
    from builder.search import conditions

    names = {"_gem_names": {"Clash"}}
    assert conditions._clash([], {**names, "Life": 1000.0, "LifeUnreserved": 1000.0})[2] == pytest.approx(0.0)
    on, basis, up = conditions._clash([], {**names, "Life": 1000.0, "LifeUnreserved": 350.0})
    assert on and up == pytest.approx(0.65) and "35%" in basis
    assert conditions._clash([], {"Life": 1000.0, "LifeUnreserved": 350.0}) == (0.0, "")
