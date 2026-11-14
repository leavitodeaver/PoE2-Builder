"""GEM3: High Velocity Rounds' Riven Armour (models.riven_armour_model): 5% of its pre-mitigation Physical hit added to
the main skill's later Attack hits while Riven Armour is up (KeywordPopups RivenArmour; patch 0.3.0)."""

from __future__ import annotations

import pytest

from builder.search import models


def skill(name: str, attack: bool) -> models.Skill:
    return models.Skill(name=name, group=1, attack=attack)


STATS = {"Speed": 2.0, "HitChance": 90.0}


def test_riven_adds_its_magnitude_to_each_attack_hit_while_up():
    dps, notes = models.riven_armour_model(10_000, 0.4, skill("Crossbow Shot", True), STATS, 0.8)
    # 5% of 10,000 = 500 per hit x 1.8 hits/s x 40% up x 0.8 mitigation
    assert dps == pytest.approx(500 * 1.8 * 0.4 * 0.8)
    assert notes[0]["confidence"] == "LOW" and "without it: 0" in notes[0]["play"]


def test_riven_needs_the_boss_fully_broken():
    dps, notes = models.riven_armour_model(10_000, 0.0, skill("Crossbow Shot", True), STATS, 1.0)
    assert dps == 0 and "Fully Broken" in notes[0]["basis"]


def test_riven_adds_to_attack_hits_only():
    dps, notes = models.riven_armour_model(10_000, 0.5, skill("Spark", False), STATS, 1.0)
    assert dps == 0 and "Attack" in notes[0]["basis"]


def test_riven_uptime_is_at_most_the_whole_fight():
    full, _ = models.riven_armour_model(10_000, 1.0, skill("Crossbow Shot", True), STATS, 1.0)
    over, _ = models.riven_armour_model(10_000, 3.0, skill("Crossbow Shot", True), STATS, 1.0)
    assert over == full == pytest.approx(500 * 1.8)
