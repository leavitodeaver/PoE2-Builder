"""MECH87 (MECH-A): Non-Critical Hit Recently and gained a Power Charge Recently from the build."""
import math

import pytest

from builder.search import conditions

STATS = {"CritChance": 30.0, "Speed": 2.0, "HitChance": 90.0}


def test_non_crit_recently_follows_the_main_skills_hits_and_crit_chance():
    line = ["20% increased Critical Damage Bonus if you've dealt a Non-Critical Hit Recently"]
    got = conditions._non_crit_recently(line, STATS)
    assert got[2] == pytest.approx(1 - 0.3 ** (4 * 1.8), rel=1e-6) and "1.80 hits a second" in got[1]
    slow = conditions._non_crit_recently(line, {**STATS, "Speed": 0.1, "CritChance": 90.0, "HitChance": 100.0})
    assert slow[2] == pytest.approx(1 - 0.9 ** 0.4, rel=1e-6)
    assert conditions._non_crit_recently(line, {**STATS, "CritChance": 100.0})[0] == 0.0  # (always crits: off)
    assert conditions._non_crit_recently(line, {**STATS, "Speed": 0.0})[0] == 0.0  # (no hits: off)
    assert conditions._non_crit_recently([], STATS) == (0.0, "")
    names = {c.var for c in conditions.CONDITIONS}
    assert "conditionNonCritRecently" in names and "conditionGainedPowerChargeRecently" in names


def test_gained_a_power_charge_recently_comes_from_the_builds_gains_only():
    cond = "10% increased Damage if you've gained a Power Charge Recently"
    source = "25% chance to gain a Power Charge on Critical Hit"
    got = conditions._gained_power_recently([cond, source], STATS)
    gains = 0.25 * 1.8 * 0.3
    assert got[2] == pytest.approx(1 - math.exp(-4 * gains), rel=1e-3) and "Critical Hit" in got[1]
    # The condition line itself is no source (charge model reads "gained a Power Charge" in it at a guessed rate):
    assert conditions._gained_power_recently([cond], STATS)[0] == 0.0
    assert conditions._gained_power_recently(
        [cond, "+10% to Critical Hit Chance if you haven't gained a Power Charge Recently"], STATS)[0] == 0.0
    assert conditions._gained_power_recently([source], STATS) == (0.0, "")
