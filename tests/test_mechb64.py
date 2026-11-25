"""MECHB64 (MECH-B): RESEARCH60 R60-1, Bonestorm's spike explosions with Pierce / Chain / Fork on the skill."""
import pytest

from builder.search import models


def test_pierce_chain_or_fork_on_bonestorm_moves_its_explosion_off_the_struck_boss():
    """R60-1: with Pierce, Chain or Fork on Bonestorm the spike explodes at the end of its flight: the headline counts
    the spikes alone (MEDIUM-LOW); the explosion on the struck boss is the row."""
    plain = [{"label": "Projectile", "TotalDPS": 1000.0}, {"label": "Explosion", "TotalDPS": 500.0}]
    assert models.bonestorm_flight_moves(plain[0]) == []
    for field, name in (("PierceCount", "Pierce"), ("ChainMax", "Chain"), ("ForkCountMax", "Fork")):
        sets = [{**plain[0], field: 1}, plain[1]]
        assert models.bonestorm_flight_moves(sets[0]) == [name]
        mods, notes = models.multi_set_model("Bonestorm", "Projectile", sets)
        assert mods == [] and name in notes[0]["basis"] and "x1.500" in notes[0]["basis"]
        assert notes[0]["confidence"].startswith("MEDIUM-LOW") and "R60-1" in notes[0]["basis"]
        mods, notes = models.multi_set_model("Bonestorm", "Explosion", sets)  # (PoB scoring the explosion: spikes only)
        assert mods[0]["value"] == pytest.approx(100.0) and notes[0]["value"] == "x2.000"
    mods, notes = models.multi_set_model("Bonestorm", "Projectile", plain)
    assert mods[0]["value"] == pytest.approx(50.0) and notes[0]["confidence"].startswith("MEDIUM (R60-1")


def test_a_bonestorm_feeder_with_pierce_counts_its_own_hit_and_shows_the_explosion_as_a_row():
    """R60-1 for Cast on Critical: "beside_off" (Pierce / Chain / Fork) adds no Energy; its reading is the row."""
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    spike = {"HitChance": 100.0, "CritChance": 50.0, "PhysicalCritAverage": 2000.0}
    blast = {"HitChance": 100.0, "CritChance": 50.0, "PhysicalCritAverage": 500.0}
    feeder = {"name": "Bonestorm", "uses": 0.0, "speed": 8.0, "why": "", "final": 0.0, "row": spike}
    alone, crits_alone, _ = models.energy_feed("crit", [feeder], stats)
    off, crits, how = models.energy_feed("crit", [{**feeder, "beside_off": ("Pierce", [blast])}], stats)
    assert off == pytest.approx(alone) and crits == pytest.approx(crits_alone)
    row = (alone + 8.0 * 0.5 * 20 * 0.5) / 8.0
    assert "Pierce on it moves the explosion" in how and f"{row:.2f} Energy a use" in how and "R60-1" in how


def test_energy_past_the_maximum_lost_is_a_row():
    """R60-1's open question (UNVERIFIED): the headline carries Energy past Cast on Critical's maximum; the row loses
    it, so a round takes the next whole number of crit hits (build 4's shape: 3.18 a round -> 4, x0.796)."""
    row = models.energy_overflow_row(11.038, 35.15, 11.038)
    assert "UNVERIFIED" in row and "4 hits a round" in row and "x0.796" in row
    assert models.energy_overflow_row(10.0, 30.0, 10.0) == ""  # (a whole 3 hits a round: nothing lost either way)
    assert models.energy_overflow_row(0.0, 30.0, 0.0) == ""

