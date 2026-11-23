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


def test_shield_wall_segment_rows():
    """Shield Wall: the headline stays one explosion a wall (PoB's one a cast, the lower reading); rows at 3 / 5
    segments' explosions a wall scale the whole break value (UNVERIFIED: the segment count and stacking are in no file;
    the 0.5.0 one-hit line is Fortifying Cry's, not the segments')."""
    from builder.search import models
    from builder.search.models import Skill
    sets = [{"label": "Shield Wall", "TotalDPS": 100.0, "AverageDamage": 100.0, "Speed": 1.0}]
    sw = Skill("Shield Wall", 1, attack=True)
    mods, notes = models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 0.5, 0.0, 100.0, "slams")
    basis = notes[0]["basis"]
    assert models.SHIELD_WALL_SEGMENT_ROWS == (3, 5)
    assert "if 3 / 5 segments' explosions each hit it (UNVERIFIED" in basis
    head = float(notes[0]["value"][1:])
    rows = basis.rsplit("): ", 1)[1].removesuffix(" DPS").split(" / ")
    assert [round(float(r.replace(",", "")) / (head * 100), 1) for r in rows] == [3.0, 5.0]


def test_requiem_second_button_share(fresh_rules, monkeypatch):
    """Compose Requiem's play line names the fill's free share for a second button (an UNDERCOUNT row, text only:
    the other skill's DPS is the search's)."""
    monkeypatch.setattr(Usage, "talisman_blockers", lambda self, name: [])
    rules = {"Requiem": {"usageCondition": True, "stats": {
        "rapidshot_requiem_base_buff_count_requirement": 100, "rapidshot_requiem_number_of_stacks_per_minute": 200,
        "rapidshot_requiem_active_duration_ms": 12000}}}
    build = ComboBuild(rules, {"Requiem": ["Attack", "Projectile"]},
                       [{"index": 0, "enabled": True, "gems": [{"name": "Requiem", "support": False}]}], {"Requiem": 4.2})
    note = Usage(build, FakeConditions()).play_note("Requiem")
    assert "the other 71% of the fight (the fill) is free for another skill" in note and "x 0.71 would add" in note
