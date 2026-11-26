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
