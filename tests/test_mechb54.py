"""MECHB54: MECH-B models that ADD hits a use tag them (MECH99's step 3, models.HIT_CUT_PER_USE above 1)."""
from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill


def per_use(mods: list[dict], skill: str) -> tuple[float | None, float | None]:
    """(time cut, hits a use) as the hit_cut hook reads them."""
    return models.hit_cut_of(mods, None, skill), models.hits_per_use_of(mods, None, skill)


def test_spell_cascade_areas_are_hits_a_use_not_time():
    main = Skill("Comet", group=1, spell=True, support_stats={models.CASCADES_PER_SIDE: 1})
    mods, _ = models.spell_cascade_model(main, {"AreaOfEffectRadius": 25}, 0.3, "a 0.3 m boss")
    assert per_use(mods, "Comet") == (None, pytest.approx(3.0))


def test_repeats_are_hits_a_use_and_a_longer_use():
    """Freezing Salvo's Repeats: 1 + r hits a use, each Repeat 5% longer (its time part, PoB's Speed doesn't hold it);
    Lioneye's Glare: its Speed MORE already holds the time, so hits only; Zarokh's Revolt: 1 + Seals broken."""
    salvo = models.Skill.of({"name": "Freezing Salvo", "attack": True, "group": 1})
    level = {"base_skill_seal_gain_interval_ms": 750, "base_maximum_seals_for_skill": 10,
             "skill_rapid_fire_repeats_per_broken_seal": 1}
    mods, _ = models.freezing_salvo_model(salvo, {"Speed": 0.1, "ProjectileCount": 1}, [], level)
    cut, hits = per_use(mods, "Freezing Salvo")
    assert hits == pytest.approx(11) and cut == pytest.approx(1 / 1.5)
    line = "Repeatable Attacks with this Bow Repeat +2 times if no enemies are in your Presence"
    mods, _ = models.lioneye_model([line], {"PresenceRadius": 40}, Skill("Lightning Arrow", group=1, attack=True), 5.0, True)
    assert per_use(mods, "Lightning Arrow") == (None, pytest.approx(3.0))
    stats = {"SealCooldown": 0.35, "SealMax": 5, "Cooldown": 10.032, "CastRate": 1.4285714}
    mods, _ = models.zarokh_model(Skill("Spark", group=1, spell=True), ["Zarokh's Revolt"], stats)
    assert per_use(mods, "Spark") == (None, pytest.approx(6.0))


def test_extra_projectiles_hitting_are_hits_a_use():
    # (Spark / Freezing Shards: GEM33's 6c44cfb6 tags group_exception_model, the manager 03:30: not tagged twice)
    bolt = Skill("Explosive Shot", group=1, attack=True)
    mods, _ = models.crossbow_sequence_model(bolt, {"ProjectileCount": 3})
    assert per_use(mods, "Explosive Shot") == (None, pytest.approx(3.0))  # (its attack time: PoB's SkillAttackTime)
    # (the readers: a use's chance from 3 hits, MECH99's hits_raise; the time readers keep the cap at 1)
    assert models.hits_raise({"_hits_per_use": 3.0}) == 3.0 and models.per_use_factor({"_hits_per_use": 3.0}) == 1.0


def test_a_main_skill_whose_damage_is_another_skills_says_so():
    """The user's check (POE2T4834): Barrage / Rain of Blades / the Heralds / Plague Bearer as the main skill alone score
    0; a note says why (their gem text), only when PoB gives them no damage of their own."""
    barrage = Skill("Barrage", group=1, spell=True)
    text = "Ready a volley of arrows or spears, Empowering your next Repeatable Bow or Projectile Spear Attack"
    notes = models.needs_another_skill_note(barrage, {"Buff", "EmpowersOtherSkill", "Spell"}, text, 0.0)
    assert notes and notes[0]["confidence"] == "HIGH" and "Empowers another skill" in notes[0]["basis"]
    assert text in notes[0]["basis"] and "require Barrage beside it" in notes[0]["play"]
    assert models.needs_another_skill_note(barrage, {"EmpowersOtherSkill"}, text, 12.0) == []  # (it has damage)
    assert models.needs_another_skill_note(Skill("Spark", group=1, spell=True), {"Spell"}, "", 0.0) == []
    herald = models.needs_another_skill_note(Skill("Herald of Ash", group=1), {"Herald", "Persistent"}, "x", 0.0)
    assert "is a Herald" in herald[0]["basis"]
    # (a minion skill's damage sits in Full DPS: no note, probe POE2T4836's Raging Spirits)
    assert models.needs_another_skill_note(Skill("Raging Spirits", group=1), {"Persistent"}, "x", 0.0, 96.5) == []
    # (a part of the gem chosen: Rain of Blades' Falling Blade, named by its gem)
    rain = models.needs_another_skill_note(Skill("Falling Blade", group=1), {"EmpowersOtherSkill"}, "x", 0.0, 0.0,
                                           "Rain of Blades")
    assert rain[0]["label"].startswith("Rain of Blades") and "its Falling Blade" in rain[0]["basis"]


def test_double_vision_piercing_bolts_blast_past_a_lone_boss():
    """RESEARCH46 R46-8: the blast goes off where the flight ends; a piercing bolt flies on past a lone boss: 0, the
    old count a LOW row. R46-7: one Surge a use (the headline unchanged for a bolt that stops)."""
    from builder.search import conditions
    texts = ["When you reload, triggers Gemini Surge to alternately", "gain 6 Cold Surges or 6 Fire Surges"]
    gemini, main = Skill("Gemini Surge", 3), Skill("Galvanic Shards", 1, attack=True)
    stats = {"FiringRate": 2.0, "BoltCount": 4, "ChanceToNotConsumeAmmo": 0, "Speed": 2.0}
    crossbow = {"Attack", "CrossbowAmmoSkill"}
    mods, notes, groups = models.double_vision_model(texts, stats, [gemini, main], main, crossbow, conditions.surge_rate)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(2.0) and groups == {3}
    assert "R46-7" in notes[0]["basis"]
    mods, notes, groups = models.double_vision_model(texts, {**stats, "PierceCount": 1}, [gemini, main], main, crossbow,
                                                     conditions.surge_rate)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == 0 and not groups
    assert "R46-8" in notes[0]["basis"] and "2.000 (LOW)" in notes[0]["basis"] and notes[0]["confidence"] == "MEDIUM"
