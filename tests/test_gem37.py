"""GEM37: Hit and Run's row where the supported skill's own travel counts toward its 10 m (RESEARCH49 R49-6)."""
from __future__ import annotations

from builder.search.models import SHIELD_CHARGE_MIN_TRAVEL
from builder.search.usage import OWN_TRAVEL, Usage
from tests.test_usage import ComboBuild, FakeConditions, fresh_rules  # noqa: F401 (a fixture)


def _usage(skill: str, speed: float, movement: float = 1.0) -> Usage:
    rules = {"Hit and Run": {"addsUsageCondition": True, "stats": {"skill_unusable_until_moved_X_distance": 100}}}
    types = {"Hit and Run": [], skill: ["Attack", "Melee"]}
    build = ComboBuild(rules, types, [{"index": 0, "enabled": True, "gems": [
        {"name": skill, "support": False}, {"name": "Hit and Run", "support": True}]}], {skill: speed})
    build.stats = {"EffectiveMovementSpeedMod": movement}
    return Usage(build, FakeConditions())


def test_own_travel_row_walks_only_the_rest():
    """The headline (the full 10 m walked) is unchanged; the row walks 10 m minus the skill's own travel."""
    usage = _usage("Rake", 2.0, 1.2)
    walked = usage.travel_rate("Hit and Run")[0]
    assert abs(walked - 1 / (0.5 + 10 / (3.7 * 1.2))) < 1e-9  # (the headline, as before)
    uses, metres, skill, how = usage.own_travel_rate("Hit and Run")
    assert skill == "Rake" and metres == 5.0 and "base_melee_dash_range 50" in how
    assert abs(uses - 1 / (0.5 + 5 / (3.7 * 1.2))) < 1e-9 and uses > walked
    note = usage.play_note("Hit and Run")
    assert "if Rake's own travel counts toward the 10 m (UNVERIFIED, R49-6" in note and "walk 5 m" in note


def test_own_travel_absent_for_skills_without_travel():
    """A skill with no travel of its own (or one the table doesn't hold) has no row: the headline stands alone."""
    usage = _usage("Quarterstaff Strike", 2.0)
    assert usage.travel_rate("Hit and Run") is not None and usage.own_travel_rate("Hit and Run") is None
    assert "own travel" not in usage.play_note("Hit and Run")


def test_own_travel_table():
    """Read from the skills' stat sets: Vaulting Impact's 4.2 m, Rolling Slam's 2 m (the lower end of R49-6's 2-4 m),
    Shield Charge's minimum charge (its 80 is a % increase, not 8 m), Leap Slam's 3 m (LOW); none reaches 10 m."""
    assert OWN_TRAVEL["Vaulting Impact"][0] == 42 and OWN_TRAVEL["Rolling Slam"][0] == 20
    assert OWN_TRAVEL["Leap Slam"][0] == 30 and OWN_TRAVEL["Stampede"][0] == 45
    assert abs(OWN_TRAVEL["Shield Charge"][0] - 10 * SHIELD_CHARGE_MIN_TRAVEL) < 1e-9
    assert all(0 < d < 100 for d, _ in OWN_TRAVEL.values())


def test_shield_wall_one_hit_a_wall_and_kaoms_row():
    """Shield Wall: one hit a wall on the boss however many segments reach it (0.3.0, RESEARCH52 R52-1, HIGH: no segment
    rows); Kaom's Madness's extra Fissures a row only, (1 + extra) hits a wall (UNVERIFIED, LOW)."""
    from builder.search import models
    from builder.search.models import Skill
    sets = [{"label": "Shield Wall", "TotalDPS": 100.0, "AverageDamage": 100.0, "Speed": 1.0}]
    sw = Skill("Shield Wall", 1, attack=True)
    mods, notes = models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 0.5, 0.0, 100.0, "slams")
    basis = notes[0]["basis"]
    assert not hasattr(models, "SHIELD_WALL_SEGMENT_ROWS")
    assert "One hit a wall on the boss however many segments reach it" in basis and "Kaom" not in basis
    mods3, notes3 = models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 0.5, 0.0, 100.0, "slams", fissures=3)
    assert notes3[0]["value"] == notes[0]["value"] and mods3 == mods  # (the headline unchanged)
    head = float(notes[0]["value"][1:]) * 100
    row = float(notes3[0]["basis"].rsplit("x4, ", 1)[1].removesuffix(" DPS").replace(",", ""))
    assert "3 extra Fissure(s) (Kaom's Madness)" in notes3[0]["basis"] and abs(row - 4 * head) < 1


def test_r51_4_breakers_segments():
    """RESEARCH51 R51-4: Fragmentation Rounds ~2, Gelid Palm ~4, Embrace The Fall the whole wall (GEM36 took them at 1);
    the rows 1 / 3 / 5 / 9 cover R51-4's (1 / 3, 3 / 5, 5 / 9)."""
    from builder.search import models
    seg = models.BREAK_SEGMENTS
    assert seg["Fragmentation Rounds"] == 2.0 and seg["Gelid Palm"] == 4.0 and seg["Embrace The Fall"] == models.WALL_SEGMENTS
    assert set(models.ICE_BREAKERS) <= set(seg) and models.VERGLAS_SEGMENT_ROWS == (1, 3, 5, 9)
