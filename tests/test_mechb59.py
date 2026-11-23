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


def _feeder(name="Arc", uses=0.0, speed=2.0, **row):
    return {"name": name, "uses": uses, "speed": speed, "why": "test", "final": 0.0,
            "row": {"HitChance": 100.0, "CritChance": 50.0, "LightningCritAverage": 500.0, **row}}


def test_a_self_used_skill_feeds_cast_on_critical():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    gain, crits, how = M.energy_feed("crit", [_feeder()], stats)
    # 2 uses/s x 50% crit x Power 20 x (500 / 1000 of the Ailment Threshold)
    assert (gain, crits) == pytest.approx((10.0, 1.0))
    assert "Arc" in how


def test_timed_feeders_leave_the_at_will_feeder_the_rest():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    gain, _, _ = M.energy_feed("crit", [_feeder("Timed", uses=0.5, speed=1.0), _feeder("Arc")], stats)
    # Timed: 0.5 x 5 Energy; it takes half the time, so Arc runs at 1 use/s x 5 Energy
    assert gain == pytest.approx(0.5 * 5.0 + 1.0 * 5.0)


def test_ailment_feed_counts_shocks_and_the_ignites_share():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    row = {"ShockChancePerHit": 25.0, "IgniteChancePerHit": 50.0, "IgniteDPS": 100.0, "IgniteDuration": 4.0}
    gain, _, _ = M.energy_feed("ailment", [_feeder(speed=1.0, **row)], stats)
    assert gain == pytest.approx(1.0 * (0.25 + 0.5 * 0.4) * 20.0)


def test_no_feeder_gives_no_energy():
    assert M.energy_feed("crit", [], {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")})[0] == 0.0
