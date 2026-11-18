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


def test_main_time_share_takes_the_press_scale_and_the_busy_share():
    """GEM22: objective.dps adds the added sources whole, so a source tied to the main skill's hits (Riven Armour,
    Broken Stance, Gas Arrow's cloud) follows the main skill's share of the fight: press scale x (1 - busy)."""
    from types import SimpleNamespace

    from builder.search.assumptions import main_time_share
    from builder.search.objective import Objective

    objective = SimpleNamespace(press_scales={"Rapid Shot": 0.8, "Other": 0.1}, press_busy=0.25, glory_busy=0.0)
    objective.busy = lambda: Objective.busy(objective)
    build = SimpleNamespace(objective=objective)
    main = SimpleNamespace(name="Rapid Shot")
    assert abs(main_time_share(build, main) - 0.8 * 0.75) < 1e-9
    assert abs(main_time_share(build, SimpleNamespace(name="Spark")) - 0.75) < 1e-9
    assert abs(main_time_share(build, None) - 0.75) < 1e-9
    objective.press_busy = 0.0
    assert main_time_share(build, SimpleNamespace(name="Spark")) == 1.0
