"""SEARCH41 (the manager's held notes, 24-26 Nov: offensive overcounts first)."""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _order(source: str, first: str, then: str) -> bool:
    """`then` is called after the last `first` in `source`."""
    i = source.rfind(first)
    return i >= 0 and source.find(then, i) > i


def test_the_low_life_factor_is_measured_again_after_each_finished_state_press_round():
    """(LIFE_AFTER_PRESS; SPEED3's POE2T7883) weigh_life ran only inside choose_play, before the press round chose the
    rates: b15's factor stayed x1.0873 (true x1.0018). Every finished-state scoring now weighs it after its press round."""
    from builder.search import inner

    sys.path.insert(0, str(ROOT / "benchmarks"))
    import gate4

    for fn in (inner.fresh_score, inner.finalize_task, gate4._score_finished):
        src = inspect.getsource(fn)
        assert _order(src, 'press_round(float("inf"))', "weigh_life()"), fn.__name__
        # (and before the uptimes are weighed on it)
        tail = src[src.rfind('press_round(float("inf"))'):]
        assert tail.find("weigh_life()") < tail.find("weigh_uptimes()"), fn.__name__
    final = inspect.getsource(inner.Search.final_choices)
    assert _order(final, "self.choose_distance()", "self.weigh_life()")
    assert re.search(r"weigh_life\(\)[^\n]*\n\s*self\.weigh_uptimes\(\)", final)


def test_upkeep_time_is_part_of_the_main_skills_lost_time():
    """(UPKEEP_PRESS; MECHB66's b12) a kept-up side skill's casts come off the main skill with the other busy shares."""
    from types import SimpleNamespace
    from builder.search.objective import Objective

    o = SimpleNamespace(press_busy=0.1, glory_busy=0.05, upkeep_busy=0.164)
    assert abs(Objective.busy(o) - 0.314) < 1e-9
    assert Objective.busy(SimpleNamespace(press_busy=0.0, glory_busy=0.0)) == 0.0  # (an old objective: no field)


def test_upkeep_sync_charges_once_per_duration_casts_and_drops_skills_gone():
    """Thunderstorm kept up once per its 4.39 s duration, 0.72 s a cast: 16.4% of the fight (b12's case); a skill no
    longer in a side group stops costing time."""
    from types import SimpleNamespace
    from builder.search.inner import Search

    groups = [{"main": True, "enabled": True, "gems": [{"name": "Entangle"}]},
              {"main": False, "enabled": True, "gems": [{"name": "Thunderstorm"}, {"name": "Some Support", "support": True}]}]
    objective = SimpleNamespace(upkeep_busy=0.0, upkeep_rates={})
    search = object.__new__(Search)
    search.build = SimpleNamespace(objective=objective, groups=lambda: groups)
    search.upkeep_rows = {"Thunderstorm": {"uses": 1 / 4.39, "seconds": 0.72},
                          "Gone Skill": {"uses": 1.0, "seconds": 1.0}}
    assert search.upkeep_sync() is True
    assert abs(objective.upkeep_busy - 0.72 / 4.39) < 1e-6 and set(objective.upkeep_rates) == {"Thunderstorm"}
    assert search.upkeep_sync() is False  # (no change)
    groups.pop()
    assert search.upkeep_sync() is True and objective.upkeep_busy == 0.0 and objective.upkeep_rates == {}


def test_skills_whose_cast_time_another_model_charges_are_left_out_of_upkeep():
    from builder.search.inner import UPKEEP_TYPES_CHARGED, upkeep_charged_elsewhere

    names = upkeep_charged_elsewhere()
    assert {"Ferocious Roar", "Parry", "Raise Shield", "Pounce", "Snap", "Frost Bomb"} <= names
    assert "Thunderstorm" not in names
    assert {"AppliesCurse", "Mark", "Warcry"} <= UPKEEP_TYPES_CHARGED
