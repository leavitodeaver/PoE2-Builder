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
