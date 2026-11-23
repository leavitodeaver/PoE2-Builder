"""MECHB59: a Vivid Stampede Stag's landings Ignite as one burst, while each landing keeps its Flammability."""
import pytest

from builder.search import conditions as C
from builder.search import models as M


def _uptime(others):
    return C.ailment_uptime({"Speed": 0, "HitChance": 100, "IgniteChancePerHit": 0, "IgniteDuration": 4,
                             "_ailment_others": {"Ignite": others}}, "Ignite")


def test_stag_burst_chance_and_spread():
    assert M.stag_burst(0.2, 1.0) == pytest.approx((0.05, 1.0, 1.5))
    stags, each, spread = M.stag_burst(0.2, 0.5)
    assert (stags, each) == pytest.approx((0.05, 0.9375))
    assert 0 < spread < 1.5
    assert M.stag_burst(0.2, 0.0) == (0.05, 0.0, 0.0)


def test_a_stag_ignites_once_not_as_four_spread_hits():
    # one Stag each 20 s at a 100% chance: Ignited 4 s + the 1.5 s of landings, not 4 hits x 4 s of 20 s
    assert _uptime([(M.VIVID_STAMPEDE, 0.2, 1.0, 4.0)]) == pytest.approx(5.5 / 20)
    assert _uptime([("Another Skill", 0.2, 1.0, 4.0)]) == pytest.approx(0.8)


def test_each_landing_keeps_its_flammability():
    stats = {"Speed": 0, "HitChance": 100, "IgniteChancePerHit": 0,
             "_ailment_others": {"Ignite": [(M.VIVID_STAMPEDE, 0.2, 0.25, 4.0)]}}
    assert C.flammability_sources(stats) == [(M.VIVID_STAMPEDE, 0.2, 25.0)]
