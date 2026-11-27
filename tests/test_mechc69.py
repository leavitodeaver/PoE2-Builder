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


def test_charges_lapse_and_rebuild_while_a_named_boss_is_off_the_field(monkeypatch):
    """MECHC69 (MECHC68's leftover, low; an OVERCOUNT against those bosses): an off-field phase at least as long as the
    charge duration drops every charge, and they rebuild at the climb rate: count^2 / (2 climb) charge-seconds lost a
    lapse over the on-field fight; once a fight for a Life-gated phase, one per 60 s of fight for Zarokh's Trial."""
    from builder.search import conditions

    king = {"_boss": "The King in the Mists", "_fight_on": 30.0}
    held, how = conditions.charge_lapse(king, 3.0, 1.5, 15.0)
    assert held == pytest.approx(3.0 - 3.0 * 3.0 / (2 * 1.5) / 30.0)
    assert "38 s" in how and "the boss never leaving: 3.00" in how and "once a fight" in how
    # Zarokh: the k-th Trial between 50 k and 70 k s; none in a fight under 50 s
    assert conditions.events_by(40.0, (50.0, 70.0)) == 0.0
    assert conditions.events_by(60.0, (50.0, 70.0)) == pytest.approx(0.5)
    assert conditions.events_by(130.0, (50.0, 70.0)) == pytest.approx(1.0 + 0.75)
    assert conditions.charge_lapse({"_boss": "Zarokh, the Temporal", "_fight_on": 40.0}, 3.0, 1.5, 15.0)[0] == 3.0
    z, how = conditions.charge_lapse({"_boss": "Zarokh, the Temporal", "_fight_on": 120.0}, 3.0, 1.5, 15.0)
    # (14.5 s against 15 s: it lapses when the last gain came 0.5 s+ before it left, e^(-0.5 x gains))
    lapses = math.exp(-0.5 * 1.5) * conditions.events_by(120.0, (50.0, 70.0))
    assert z == pytest.approx(3.0 - lapses * 3.0 * 3.0 / (2 * 1.5) / 120.0) and "one per 50-70 s" in how
    # more gains a second: the last one more often within 0.5 s of leaving, a lapse less often
    assert conditions.charge_lapse({"_boss": "Zarokh, the Temporal", "_fight_on": 120.0}, 3.0, 1.5, 15.0, 6.0)[0] > z
    # the Fell Serpent's 10 s against a 15 s duration: e^(-5 x 1.5), under 0.1%: nothing lapses; an 8 s duration lapses
    fell = {"_boss": "Atziri, the Fell Serpent", "_fight_on": 30.0}
    assert conditions.charge_lapse(fell, 3.0, 1.5, 15.0)[0] == 3.0
    assert conditions.charge_lapse(fell, 3.0, 1.5, 8.0)[0] < 3.0
    # Kulemak's breaks have no length: nothing lapses (said)
    k, how = conditions.charge_lapse({"_boss": "Vessel of Kulemak", "_fight_on": 30.0}, 3.0, 1.5, 15.0)
    assert k == 3.0 and "unknown length" in how
    # a named boss with no off-field data: unchanged, nothing said (the default boss: MECHC70's test below)
    assert conditions.charge_lapse({"_boss": "Arbiter of Ash", "_fight_on": 30.0}, 3.0, 1.5, 15.0) == (3.0, "")
    monkeypatch.setattr(conditions, "WORST_LAPSE", False)  # (MECHC69's reading of the worst case: no lapse)
    assert conditions.charge_lapse({"_fight_on": 30.0}, 3.0, 1.5, 15.0) == (3.0, "")
    monkeypatch.setattr(conditions, "WORST_LAPSE", True)
    # never below 0; no fight length: unchanged
    assert conditions.charge_lapse({**king, "_fight_on": 0.5}, 3.0, 1.5, 15.0)[0] == 0.0
    assert conditions.charge_lapse({**king, "_fight_on": 0.0}, 3.0, 1.5, 15.0)[0] == 3.0
    monkeypatch.setattr(conditions, "CHARGE_LAPSE", False)
    assert conditions.charge_lapse(king, 3.0, 1.5, 15.0) == (3.0, "")


def test_one_leech_instance_recovers_at_a_time(monkeypatch):
    """MECHC69 (POBR1 via the manager; KeywordPopups Leech "Only one instance of Leech for each resource can provide
    recovery at a time", patch 0.5.0): PoB's stacked rate A x h x s becomes A x min(h, s) = its uncapped sum /
    max(h, s), h = instances / PoB's duration, s = the instance rate / (2% of the pool); the instant part and the cap as
    before; the boss's resistance on top."""
    from builder.search import recovery

    base = {"LifeLeechInstantRate": 20.0, "LifeLeechInstances": 4.0, "LifeLeechInstanceRate": 50.0,
            "MaxLifeLeechRate": 500.0, "LifeRecoveryRateMod": 1.0, "LifeLeechDuration": 0.5, "Life": 2500.0}
    stats = {**base, "LifeLeechRate": 220.0}
    share, how = recovery.leech_single_share(stats, "Life")
    assert share == pytest.approx(1 / 8) and "one recovers at a time" in how  # (8 hits/s, speed x1)
    lost, single, _ = recovery.leech_cuts(stats, 0.0)["Life"]
    assert lost == pytest.approx(200 - 25) and single == pytest.approx(lost)
    keep = 1 - 0.5
    lost, single, _ = recovery.leech_cuts(stats, 0.5)["Life"]
    assert 220 - lost == pytest.approx(20 * keep + 25 * keep)
    assert single == pytest.approx((20 * keep + 200 * keep) - (20 * keep + 25 * keep))
    # slow hits (h < s): every instance runs whole: the stacked amount A x h, PoB's sum / s
    slow = {**stats, "LifeLeechInstances": 0.25, "LifeLeechRate": 20.0 + 12.5, "LifeLeechInstanceRate": 100.0}
    share, how = recovery.leech_single_share(slow, "Life")  # (h = 0.5/s, s = 2)
    assert share == pytest.approx(0.5) and "each runs whole" in how
    # no duration read, or PoB's rate not its stacked sum: unchanged
    assert recovery.leech_single_share({**stats, "LifeLeechDuration": 0}, "Life") is None
    assert recovery.leech_cuts({**base, "LifeLeechRate": 100.0}, 0.0) == {}
    monkeypatch.setattr(recovery, "ONE_LEECH", False)
    assert recovery.leech_cuts(stats, 0.0) == {}


def test_snaps_quality_adds_its_chance_of_an_additional_infusion_remnant(monkeypatch):
    """MECHC69 (GEM49's unread stat, an UNDERCOUNT): Snap's chance_%_to_spawn_another_infusion_remnant (0.5 a quality
    point; "{0}% chance to spawn an additional Infusion Remnant") adds to the source's additional-Remnant chance, as
    its supports' does."""
    stat = assumptions.SNAP_EXTRA_STAT

    class Build:
        def call(self, method, **kw):
            assert method == "activeSkills" and kw["levelStats"] == ["Snap"]
            return {"skills": [{"name": "Snap", "disabled": False, "levelStats": {stat: 10}}]}

    assert assumptions.snap_extra_chance(Build()) == pytest.approx(0.10)
    snap = {"name": "Snap", "disabled": False, "levelStats": {stat: 10}}
    ups = {"conditionEnemyShocked": 1.0}
    with_q, used = assumptions.infusion_supply({"Snap": snap}, [], ups, (0.0, ""), (0.0, ""), (0.0, ""))
    plain, _ = assumptions.infusion_supply({"Snap": {**snap, "levelStats": {}}}, [], ups, (0.0, ""), (0.0, ""),
                                           (0.0, ""))
    assert with_q["Lightning"] == pytest.approx(plain["Lightning"] * 1.10)
    assert any("its quality's 10.0%" in u for u in used)
    monkeypatch.setattr(assumptions, "SNAP_EXTRA", False)
    off, _ = assumptions.infusion_supply({"Snap": snap}, [], ups, (0.0, ""), (0.0, ""), (0.0, ""))
    assert off["Lightning"] == pytest.approx(plain["Lightning"])
