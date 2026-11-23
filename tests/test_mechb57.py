"""MECHB57: the mixed models' extra hits reach the hit readers (the manager's MECHB57 brief, item 1; an UNDERCOUNT for
their ailments): Aftershocks, Fissures and Spectral Volley's projectiles tag models.HIT_CUT_PER_USE."""
from __future__ import annotations

import pytest

from builder.search import conditions, models
from builder.search.models import Skill

SLAM = {"Attack", "Slam", "Melee", "Area"}
HYMN = ["Slam Skills you use yourself cause an additional Aftershock"]


def _sets(**dps):
    return [{"label": k.replace("_", " "), "TotalDPS": v} for k, v in dps.items()]


def test_aftershocks_are_hits_a_slam_for_the_readers():
    sunder = Skill("Sunder", group=1, attack=True)
    # Aftershock II alone: 1.35 hits a slam; the DPS more and the tag agree
    mods, _ = models.aftershock_model([], {}, sunder, SLAM, [{"name": "Aftershock II"}], set())
    assert models.hits_per_use_of(mods, skill="Sunder") == pytest.approx(1.35)
    assert models.hit_cut_of(mods, skill="Sunder") is None  # (no time cut: the slam is used as often)
    # the Hymn alone: the ledger's x2 already right for PoB, but the readers still need the 2 hits (a 0% carrier)
    mods, _ = models.aftershock_model(HYMN, {}, sunder, SLAM, [], {"Two Hand Mace"})
    assert models.hits_per_use_of(mods, skill="Sunder") == pytest.approx(2.0)
    assert sum(m["value"] for m in mods if m["name"] == "DPS") == pytest.approx(0.0)
    # Shock's uptime at 20% a hit, 1 slam a second, 4 s: the reader sees the slam's 2 hits as one use's chance
    stats = {"Speed": 1.0, "HitChance": 100.0, "ShockChancePerHit": 20.0, "ShockDuration": 4.0}
    before = conditions.ailment_uptime(stats, "Shock")
    after = conditions.ailment_uptime({**stats, "_hits_per_use": 2.0}, "Shock")
    assert after == pytest.approx(1 - 0.64 ** 4) and before == pytest.approx(1 - 0.8 ** 4)


def test_fissures_are_hits_a_use_with_molten_crashs_slam_beside_them():
    sunder = Skill("Sunder", 1, attack=True, melee=True)
    mods, _ = models.fissure_count_model(sunder, "Wave", _sets(Wave=100.0), {}, {models.FISSURE_EXTRA_STAT: 2}, [],
                                         0.4)
    assert models.hits_per_use_of(mods, skill="Sunder") == pytest.approx(3 * 2.5)  # 3 Fissures x 2.5 areas a wave
    crash = Skill("Molten Crash", 1, attack=True, melee=True)
    mods, _ = models.fissure_count_model(crash, "Molten Crash", _sets(Molten_Crash=400.0, Fissures=160.0), {},
                                         {models.FISSURE_EXTRA_STAT: 1}, [], 0.4,
                                         rage_paid={"share": 1.0, "asked": 5.0, "paid": 5.0})
    assert models.hits_per_use_of(mods, skill="Molten Crash") == pytest.approx(1 + 3)  # the slam + 3 Fissures


def test_spectral_volleys_projectiles_are_hits_spread_over_the_attacks():
    bow = Skill("Lightning Arrow", 1, attack=True, support_stats={models.SPECTRAL_OWN_STAT: -60,
                                                                  models.SPECTRAL_MORE_STAT: 20})
    mods, _ = models.spectral_volley_model(bow, ["Spectral Volley"], {"Speed": 2.0, "TotalDPS": 800.0,
                                                                     "ProjectileCount": 1})
    assert models.hits_per_use_of(mods, skill="Lightning Arrow") == pytest.approx(1 + 5 / 3 / 2.0)
    assert models.hit_cut_of(mods, skill="Lightning Arrow") is None


def test_focused_thrusts_spear_projectile_line_reads_the_boss_distance():
    """Item 4 (coverage section B): PoB drops the line for its condition; on a lone boss it is PoB's own "further than"
    tag on the boss distance (probes POE2T4888: the replacement parses whole; POE2T4895: the node 68.48 -> 89.62 TotalDPS
    at 5 m with it, as a ring carrying the replacement; 0 without it)."""
    from builder.ledger import translate

    line = "40% increased Projectile Damage with Spears while there are no Enemies within 3m"
    found = translate(line)
    assert found[0].id == "lines-mechb57-spear-projectiles-no-enemies-within"
    assert found[1] == ["40% increased Projectile Damage with Spears against enemies further than 3m"]


ALIGN_LEVEL = {models.ALIGN_INTERVAL_STAT: 9150, models.ALIGN_PER_SPELL_STAT: 25, models.ALIGN_MORE_STAT: 30}


def test_align_fate_scores_every_valid_spells_free_casts_and_the_main_spells_empower():
    """The manager 01:15 (RESEARCH49 R49-8): visages = (1 + 0.25 S) x Cooldown Recovery / 9.15 s, one of the S Spells at
    random each; the main Spell's free casts (more uses) and Empower on its 1 / S, the fillers' free casts at their
    hit."""
    spark = Skill("Spark", 1, spell=True)
    mods, notes = models.align_fate_model(spark, ["Fireball", "Spark", "Frostbolt"], ALIGN_LEVEL, 2.0, recovery=1.2,
                                          hits={"Fireball": 1000.0, "Frostbolt": 500.0})
    rate = 1.75 * 1.2 / 9.15
    each = rate / 3
    assert mods[0]["value"] == pytest.approx(30 * each / 2.0, abs=0.01) and mods[0]["hit_cut"] == 1.0
    assert mods[1][models.USES_RAISE] == pytest.approx(1 + each / 2.0, abs=1e-6)
    assert models.added_total(mods) == pytest.approx(each * 1500.0, rel=1e-3)
    assert models.hit_cut_of(mods, skill="Spark") == pytest.approx(1.0)  # (the Empower isn't hits)
    assert "S 1: 0.164 visages/s" in notes[0]["basis"]  # (1.25 x 1.2 / 9.15, the rate by S for SEARCH37)


def test_align_fate_on_an_attack_main_skill_counts_the_spells_free_casts():
    """The Oracle's Tempest Bell: no Empower or free cast of its own, only the valid Spells' visage casts (it read
    0)."""
    bell = Skill("Tempest Bell", 1, attack=True)
    mods, notes = models.align_fate_model(bell, ["Fireball"], ALIGN_LEVEL, 0.25, hits={"Fireball": 800.0})
    assert len(mods) == 1 and models.added_total(mods) == pytest.approx(1.25 / 9.15 * 800.0, rel=1e-3)
    assert "is an Attack" in notes[0]["basis"]
    assert models.align_fate_model(bell, [], ALIGN_LEVEL, 0.25)[0] == []
