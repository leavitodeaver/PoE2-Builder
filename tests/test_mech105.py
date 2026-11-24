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
