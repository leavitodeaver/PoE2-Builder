"""MECH105: Heart of Ice's Chill (RESEARCH55 R55-1), against the game's 30% floor and your Presence."""
import pytest

from builder.search import conditions
from builder.search.conditions import chilled_share, distance_candidates, heart_of_ice

READ = {"found": True, "level": 27, "min": 2346, "max": 3519, "inc": 690, "more": 1, "chillInc": 0, "chillMore": 1,
        "resist": 15}
STATS = {"EnemyAilmentThreshold": 71303, "PresenceRadius": 40, "_distance_choice": (4.0, "test")}


def test_no_skill_reads_nothing():
    assert heart_of_ice({"found": False}, [], STATS) == (0.0, "")
    assert heart_of_ice(None, [], STATS) == (0.0, "")


def test_over_the_floor_inside_presence_chills_and_shows_the_resistance_row():
    share, how = heart_of_ice(READ, [], STATS)
    assert share == 1.0
    assert "32.5% Chill" in how and "27.6%" in how and "UNVERIFIED" in how


def test_under_the_floor_does_not_chill():
    share, how = heart_of_ice({**READ, "inc": 450}, [], STATS)
    assert share == 0.0 and "floor" in how


def test_chill_magnitude_lifts_it_over_the_floor():
    assert heart_of_ice({**READ, "inc": 390}, [], STATS)[0] == 0.0
    assert heart_of_ice({**READ, "inc": 390, "chillInc": 50}, [], STATS)[0] == 1.0


def test_outside_presence_is_zero():
    share, how = heart_of_ice(READ, [], {**STATS, "_distance_choice": (5.0, "test")})
    assert share == 0.0 and "outside" in how


def test_the_aura_joins_the_chilled_share():
    assert chilled_share({"_heart_of_ice": (1.0, "")}) == pytest.approx(1.0)
    assert chilled_share({"_heart_of_ice": (0.0, "")}) == pytest.approx(0.0)
    assert chilled_share({"_heart_of_ice": (0.5, ""), "_chilled_ground": (0.5, "")}) == pytest.approx(0.75)


def test_icestorm_tries_the_presence_edge_and_others_do_not():
    assert 4.0 in distance_candidates([], {"Spell"}, presence_radius=4.0, skill="Icestorm")
    assert distance_candidates([], {"Spell"}, presence_radius=4.0, skill="Fireball") == []
    assert conditions.HEART_OF_ICE == "Heart of Ice"


def test_leap_ground_rate_needs_a_spear():
    from builder.search.conditions import leap_ground_rate
    rate, chance, _ = leap_ground_rate(0.5, 30, 0.0, "none")
    assert rate == 0.0 and chance == pytest.approx(0.3)
    rate, _, how = leap_ground_rate(0.5, 30, 3.0, "Storm Lance")
    assert rate == pytest.approx(0.5 * (1 - 2.718281828 ** -3), rel=1e-6) and "finding a Spear" in how


def test_leap_ground_joins_shocked_ground():
    from builder.search.conditions import shocked_ground
    share, how = shocked_ground({"Speed": 0, "_sg_leap": (0.5, 0.3, "test")}, [])
    assert share == pytest.approx(1 - 0.7 ** 3) and "Thunderous Leap" in how
    share, how = shocked_ground({"Speed": 0, "_sg_leap": (0.0, 0.3, "no Spear")}, [])
    assert share == 0.0 and "Thunderous Leap's ground (no Spear: 0)" in how
