"""MECHC69's tests (MECH-C): the boss walking off a pressed side Orb of Storms (AUDIT41 A41-33)."""
import math

import pytest

from builder.search import assumptions, bosses, models


class _Build:
    """A build whose pressed side Orb's cycle (build.orb_presses) gives `orbs` Orbs a second."""

    def __init__(self, orbs, duration=12.0, speed=1.25, boss=None):
        setattr(self, assumptions.ORB_PRESSES, {models.ORB_OF_STORMS: (orbs, "orb_cycle")})
        self.row = {"index": 3, "duration": duration, "speed": speed, "castTime": 0.8}
        if boss is not None:
            self.ctx = type("Ctx", (), {"request": type("Req", (), {"boss": boss})()})()

    def call(self, method, **kw):
        assert method == "useRates" and kw["indices"] == [3]
        return {"rates": [self.row]}


def test_a_side_orb_makes_fewer_remnants_when_the_boss_walks_out_of_its_reach(monkeypatch):
    """MECHC69 (AUDIT41 A41-33, a possible OVERCOUNT; the manager's reader rule): in MECH-B's side-Orb cycle each Orb
    ends at its maximum hits after `life` s; with the boss walked out of its 5.6 m reach it fires at no one and lives its
    duration. Orbs a second = 1 / (q life + (1 - q) D + its cast), q = bosses.still_there(R, life). Rows: the boss
    standing still (the cycle as is) and the recast row (UNVERIFIED)."""
    skill = {"name": models.ORB_OF_STORMS, "group": 3}
    action = models.press_action({"speed": 1.25, "castTime": 0.8})
    life = 3.0
    orbs = 1 / (life + action)
    for name in (bosses.DEFAULT, "Arbiter of Ash", "Xesht, We That Are One"):
        rate, how = assumptions.orb_remnants(_Build(orbs, boss=name), skill)
        q = bosses.still_there({"_boss": name}, models.ORB_REACH, life)[0]
        assert 0 < q < 1
        assert rate == pytest.approx(1 / (q * life + (1 - q) * 12.0 + action))
        assert rate < orbs and f"the boss standing still {orbs:.3f}" in how and "UNVERIFIED" in how
        k = -math.log(q) / life
        assert f"{1 / ((1 - q) / k + action):.3f}" in how  # (the recast row: more Orbs than standing still)
        assert 1 / ((1 - q) / k + action) > orbs
    # no boss on the request: the default preset
    assert assumptions.orb_remnants(_Build(orbs), skill)[0] == pytest.approx(
        assumptions.orb_remnants(_Build(orbs, boss=bosses.DEFAULT), skill)[0])
    # an Orb that lives out its duration already: the boss leaving changes nothing
    slow = 1 / (12.0 + action)
    assert assumptions.orb_remnants(_Build(slow), skill)[0] == pytest.approx(slow)
    # the switch off: the old reading
    monkeypatch.setattr(assumptions, "ORB_STAY", False)
    assert assumptions.orb_remnants(_Build(orbs), skill)[0] == orbs

