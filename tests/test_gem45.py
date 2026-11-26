"""GEM45: Triskelion Cascade in the score (models.triskelion_cascade_model; R61-16, R62-1, R62-2)."""
import math

import pytest

from builder.search import models

LVL20 = {models.TRISKELION_DAMAGE: -11, models.TRISKELION_AREA: -31, models.TRISKELION_CD_PCT: 300}
CASCADABLE = {"Spell", "Area", "Cascadable"}


def test_triskelion_boss_hits_follow_r62_geometry():
    # R62-1's simulation (scratch/research62/trisk3.py): a 2 m spell at level 20's 31% less area, an arm aimed at the
    # boss, boss hit radius 0.3 / 0.6 m: melee 2.0 / 2.0, 2 m 1.0 / 2.0, 4 m 1.6 / 1.8, 6 m 0.5 / 0.6
    r = 2.0 * math.sqrt(0.69)
    want = {1: (2.0, 2.0), 2: (1.0, 2.0), 4: (1.6, 1.8), 6: (0.5, 0.6)}
    for d, (a, b) in want.items():
        assert round(models.triskelion_hits(r, d, 0.3), 1) == a and round(models.triskelion_hits(r, d, 0.6), 1) == b
    assert len(models.triskelion_centres(1.0)) == 7 and len(models.triskelion_centres(1.0, per_arm=3)) == 10
    outer = models.triskelion_centres(1.0)[2]  # one more step on, turned 30 degrees: ~2.9 radii out
    assert math.hypot(*outer) == pytest.approx(2.90, abs=0.01)
    assert models.triskelion_hits(0.0, 1.0, 0.3) == 0.0


def test_triskelion_press_cycle():
    # a 1 s cast at 1 a second: cooldown 1 + 300% x 1 s = 4 s; its own 0.45 s cast and 1 Empowered cast; 2.55 normal
    f, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3)
    assert e == pytest.approx(0.25) and f == pytest.approx((0.89 * 3 + 2.55) / 4)
    f, _ = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, base_hits=3)  # Spell Cascade's 3 normal hits
    assert f == pytest.approx((0.89 + 2.55) / 4)
    f, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, empowers=3)  # Olroth: 3 Empowered casts
    assert e == pytest.approx(0.75) and f == pytest.approx((3 * 0.89 * 3 + 0.55) / 4)
    _, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, ward_period=10.0)  # Runic Ward: every 10 s
    assert e == pytest.approx(0.1)
    assert models.triskelion_cascade_factor(1.0, 1.0, 0.0, -11, 300, 3) == (1.0, 0.0)


def test_triskelion_model_gain_close_loss_far_and_rows():
    comet = models.Skill("Comet", 1, spell=True)
    near_mods, near = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test")
    assert near_mods and near_mods[0]["name"] == "DPS" and near_mods[0]["value"] > 0
    assert near[0]["entry"] == models.TRISKELION_ENTRY and "random facing" in near[0]["basis"]
    assert "without it x1.000" in near[0]["play"]
    far_mods, far = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 8.0, 0.3, "test")
    assert far_mods == [] and "not pressed" in far[0]["value"]  # the pattern centres on you: a loss at range
    _, casc = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test", base_hits=3.0,
                                              cascade=True)
    assert "Spell Cascade's own areas only" in casc[0]["basis"] and "10 areas" in casc[0]["basis"]
    _, ap = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 8.0, 0.3, "test", at_target=True)
    assert "MOST DPS AT STAKE" in ap[0]["basis"]
    mods, broke = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test",
                                                  ward_cost=50.0, ward_spare=0.0)
    assert mods == [] and "no Runic Ward" in broke[0]["basis"]
    mods, slow = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test",
                                                 ward_cost=50.0, ward_spare=5.0)
    assert "every 10.0 s" in slow[0]["basis"] and mods[0]["value"] < near_mods[0]["value"]
    assert models.triskelion_cascade_model(comet, {"Spell"}, 1.0, LVL20, 2.0, 1.0, 0.3, "t") == ([], [])
    trig = models.Skill("Comet", 1, spell=True, triggered=True)
    mods, notes = models.triskelion_cascade_model(trig, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "t")
    assert mods == [] and "UNVERIFIED" in notes[0]["basis"]
    assert models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "t",
                                           forbidden={models.TRISKELION_ENTRY}) == ([], [])
