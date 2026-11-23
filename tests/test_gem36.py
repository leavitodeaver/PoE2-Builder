"""GEM36: Midnight Zenith's Ice Crystals feed Verglas (a third source beside Frost Wall and Frozen Locus)."""
from __future__ import annotations

import math

import pytest

from builder.search import models
from builder.search.models import Skill

LEVEL = {models.MIDNIGHT_PETALS_STAT: 4, models.MIDNIGHT_NEED_STAT: 10, models.MIDNIGHT_PER_STAT: 5,
         models.MIDNIGHT_LIMIT_STAT: 10, models.MIDNIGHT_LIFE_STAT: 4768}


def test_midnight_breaks_matches_midnight_zenith_model():
    """The crystals destroyed a second are midnight_zenith_model's explosions on the same inputs (its DPS / its hit)."""
    for args in [(0.5, 2.0, 2500.0, 0.0), (2.0, 2.0, 5000.0, 0.0), (0.5, 0.87, 150.0, 0.0), (0.5, 0.0, 0.0, 0.5),
                 (0.5, 0.0, 2500.0, 0.0)]:
        crits, area, hit, breakers = args
        rate, life, how = models.midnight_breaks(crits, area, hit, breakers, LEVEL, 10.0, 0.0)
        _, notes = models.midnight_zenith_model(crits, "x", area, hit, breakers, "b", 300.0, LEVEL, 10.0, 0.0)
        assert rate * 300.0 == pytest.approx(notes[0]["value"], abs=0.1), args
        assert life == 4768.0 and "made a second" in how


def test_verglas_reads_zenith_crystals():
    fb = Skill("Frostbolt", 1)
    # 1 crystal a second of 4,768 Life: floor(4,768 / 2,000) = 2% each, 6 s summed: +12%
    mods, notes = models.verglas_model(fb, ["Verglas"], 0.0, 0.0, "", "", 0.0, 0.0, "",
                                       others=((models.MIDNIGHT_ZENITH, 1.0, 4768.0, "test"),))
    want = sum(math.exp(-6) * 6 ** k / math.factorial(k) * math.floor(k * 4768 / 2000) for k in range(80))
    assert mods[0]["value"] == pytest.approx(want, abs=1e-3) and "Midnight Zenith: 1.00 crystals" in notes[0]["basis"]
    assert "each crystal floored on its own +12.0%" in notes[0]["basis"]
    assert "Midnight Star" not in notes[0]["basis"]
    # beside a Frost Wall: the two add
    both, _ = models.verglas_model(fb, ["Verglas"], 0.2, 12714.0, "cd", "x", 0.0, 0.0, "",
                                   others=((models.MIDNIGHT_ZENITH, 1.0, 4768.0, "test"),))
    assert both[0]["value"] > want  # (the two add, into one floored total)
    # crystals that only expire give nothing
    none, notes = models.verglas_model(fb, ["Verglas"], 0.0, 0.0, "", "", 0.0, 0.0, "",
                                       others=((models.MIDNIGHT_ZENITH, 0.0, 4768.0, "test"),))
    assert none == [] and notes[0]["value"] == "+0.0%"


def test_lightning_conduit_bolts_reach_the_hit_readers():
    """(MECH100's hit-raise list) the extra bolts are tagged as hits a cast: 1 + bolts x share."""
    lc = Skill(name=models.LIGHTNING_CONDUIT)
    lc.level_stats = {models.CONDUIT_BOLTS_STAT: 2}
    mods, _ = models.lightning_conduit_model(lc, 0.5, "test")
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(2.0)
    assert models.hits_per_use_of(mods, None, models.LIGHTNING_CONDUIT) == pytest.approx(2.0)
    assert models.hit_cut_of(mods, None, models.LIGHTNING_CONDUIT) is None  # (not a time cut: uses kept)


def test_stronger_hits_carry_the_strongest_ignite():
    sv = Skill(name="Lightning Arrow", attack=True)
    assert models.spectral_strength(sv) == pytest.approx(3.0)  # (1.2 / 0.4)
    stats = {"IgniteDPS": 100.0, "IgniteDuration": 4.0, "IgniteChancePerHit": 50.0, "IgniteStacksMax": 1,
             "BleedDPS": 0.0}
    # a volley every 3 s of 5 hits at 50%: 1/3 x (1 - 0.5^5) x 4 > 1 -> up 100%: x3
    mods, notes = models.stronger_hits_ailment_model(sv, stats, "Spectral Volley", 3.0, 1 / 3, 5, "t")
    assert len(mods) == 1 and mods[0]["value"] == pytest.approx(200.0) and mods[0]["keywordFlags"] == ["Ignite"]
    # Align Fate: 0.1 Empowered casts a second, 1 hit at 50%, 4 s: up 20%, x(1 + 0.3 x 0.2)
    mods, _ = models.stronger_hits_ailment_model(sv, stats, "Align Fate", 1.3, 0.1, 1, "t")
    assert mods[0]["value"] == pytest.approx(6.0)
    stacking = {**stats, "IgniteStacksMax": 3}
    assert models.stronger_hits_ailment_model(sv, stacking, "x", 3.0, 1.0, 1, "t") == ([], [])
    assert models.stronger_hits_ailment_model(sv, stats, "x", 3.0, 1.0, 1, "t", {models.STRONGER_ENTRY}) == ([], [])


def test_verglas_glacial_bolt_walls_and_rows():
    """(R48-8) Glacial Bolt's walls as a wall source (one segment a wall headline; R48-7's whole-wall rows)."""
    gb = Skill("Glacial Bolt", 1, attack=True)
    mods, notes = models.verglas_model(gb, ["Verglas"], 0.0, 0.0, "", "", 0.0, 0.0, "",
                                       others=((models.GLACIAL_BOLT, 0.5, 12714.0, "test", True),))
    want = sum(math.exp(-3) * 3 ** k / math.factorial(k) * math.floor(k * 12714 / 2000) for k in range(80))
    assert mods[0]["value"] == pytest.approx(want, abs=1e-3)
    assert "5 segments +" in notes[0]["basis"] and "9 segments +" in notes[0]["basis"]
    assert "Glacial Bolt's wall" not in notes[0]["basis"]  # (no longer "not counted")


def test_gas_grenade_cloud_life():
    assert models.gas_cloud_life(0.5, *models.GAS_GRENADE_CLOUD["Gas Grenade"]) == pytest.approx(6.0)
    assert models.gas_cloud_life(2.0, *models.GAS_GRENADE_CLOUD["Gas Grenade"]) == pytest.approx(3.0)
    assert models.gas_cloud_life(2.0) == pytest.approx(3.0)  # (Gas Arrow's default 6 / 4 s, unchanged)
