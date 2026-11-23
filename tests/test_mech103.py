"""MECH103: Rupture / Rusted Spikes Aggravate Bleeding over the build's own Heavy Stun / Pin cycle (models.aggravate_*)."""
import math

import pytest
from builder.search import bosses, models


def test_a_pin_every_10_s_aggravates_the_bleeds_live_at_it():
    # a 5 s Bleed inflicted at phase t is Aggravated from the next Pin: its own share (D - (P - t))+ / D averages 0.25
    own, live = models.aggravate_cycle_shares(5.0, 10.0, 0.0, 0.0, True, 0.0, None, 2.0, 10.0)
    assert own == pytest.approx(0.25, abs=0.01)
    # some live Bleed Aggravated from each Pin until the last Bleed before it ends: about half the cycle
    assert 0.44 < live <= 0.5  # (MECH104: the last Bleed before a Pin comes one hit, 0.5 s, before it: ~4.5 s of 10)


def test_rupture_aggravates_while_the_boss_is_heavy_stunned_not_all_fight():
    own, live = models.aggravate_cycle_shares(5.0, 20.0, 2.0, 5.0, False, 0.0, None, 5.0, 5.0)
    assert 0.25 < live < (2.0 + 5.0) / 20.0 + 0.01  # (from the stun's first hit to the last Aggravated Bleed's end)
    # far under the stationary form over the same own share (the Aggravated Bleeds bunch after each stun)
    assert live < 1 - math.exp(-5.0 * 5.0 * own) - 0.3
    # a stun once in 10,000 s: next to nothing (the sparse path)
    assert models.aggravate_cycle_shares(5.0, 10000.0, 2.0, 5.0, False, 0.0, None, 5.0, 5.0)[1] < 0.001


def test_the_stationary_lines_still_count_beside_a_cycle():
    still = models.aggravated_share(5.0, 0.5, None, 2.0)
    own, _ = models.aggravate_cycle_shares(5.0, 10000.0, 2.0, 5.0, False, 0.5, None, 2.0, 0.0)
    assert own == pytest.approx(still, abs=0.01)


def test_aggravate_cycles_read_the_builds_own_stun_and_pin(monkeypatch):
    monkeypatch.setattr(models, "heavy_stun_rate", lambda stats: (0.05, "20 s cycle"))
    monkeypatch.setattr(models, "immobilise_period", lambda stats, ailment: 8.0)
    stats = {"Speed": 2.0, "HitChance": 100.0, "PinBuildupAvg": 10.0}
    got = {c[0]: c for c in models.aggravate_cycles({models.RUPTURE, models.RUSTED_SPIKES}, stats)}
    assert got[models.RUPTURE][1:5] == (pytest.approx(20.0), bosses.of(stats).heavy_stun_s, pytest.approx(2.0), False)
    assert got[models.RUSTED_SPIKES][1:5] == (pytest.approx(8.0), 0.0, 0.0, True)
    # no stun, no Pin: period 0, nothing counted (no flat rate)
    monkeypatch.setattr(models, "heavy_stun_rate", lambda stats: (0.0, "none"))
    none = models.aggravate_cycles({models.RUPTURE, models.RUSTED_SPIKES}, {**stats, "PinBuildupAvg": 0.0})
    assert all(c[1] == 0.0 for c in none)
    mods, notes = models.aggravate_model([], {"Speed": 2.0, "HitChance": 100.0, "BleedDuration": 5.0}, {"Attack"},
                                         cycles=none)
    assert mods == [] and notes == []


def test_rupture_alone_adds_more_bleeding_damage_at_its_cycle():
    stats = {"Speed": 2.0, "HitChance": 100.0, "BleedDuration": 5.0, "BleedChancePerHit": 100.0}
    cycle = [(models.RUPTURE, 20.0, 2.0, 2.0, False, "stunned 2 s every 20 s")]
    mods, notes = models.aggravate_model([], stats, {"Attack"}, cycles=cycle)
    assert len(mods) == 1 and 0.2 < notes[0]["value"] < 0.36 and "Rupture" in notes[0]["basis"]
    # the higher of two cycles counts; the other is a row
    both = cycle + [(models.RUSTED_SPIKES, 1000.0, 0.0, 0.0, True, "a Pin every 1000 s")]
    _, notes2 = models.aggravate_model([], stats, {"Attack"}, cycles=both)
    assert notes2[0]["value"] == notes[0]["value"] and "not counted beside it" in notes2[0]["basis"]


def test_the_aggravate_line_is_the_one_pob_applies_to_bleeding():
    # MECH103 (probe POE2T6497): "1% more Damage with Bleeding" never reached PoB's Bleed; the Magnitude line x100 equals
    # PoB's own "Is the Bleed Aggravated?" box
    assert models.AGGRAVATE_LINE == "1% more Magnitude of Bleeding you inflict"
    stats = {"Speed": 2.0, "HitChance": 100.0, "BleedDuration": 5.0}
    mods, _ = models.aggravate_model(["40% chance to Aggravate Bleeding on Hit"], stats, {"Attack"})
    assert mods and all(m["line"] == models.AGGRAVATE_LINE for m in mods)
