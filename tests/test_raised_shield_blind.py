"""MECHC21 (plans/term-blind-enemies.md G4 with term-raised.md; an UNDERCOUNT: 0 before): Bastion of Light's "Blind
Enemies 3 metres in front of you every 0.25 seconds while your Shield is raised" Blinds the boss while the build taps
Raise Shield once per Blind duration with the boss within 3 m; each tap costs the main skill RAISE_TAP s."""
from __future__ import annotations

from builder.search import models
from builder.search.conditions import _debuff_source

BASTION = "Blind Enemies 3 metres in front of you every 0.25 seconds while your Shield is raised"
SHIELD = models.Skill(models.RAISE_SHIELD, attack=True)
STRIKE = models.Skill("Strike", attack=True)


def test_taps_once_per_blind_within_reach():
    plan = models.raised_plan([SHIELD, STRIKE], [BASTION], 2.0)
    assert plan.blind == 1.0 and abs(plan.lost - 0.55 / 4) < 1e-9 and "Without the taps" in plan.how
    # 50% increased Blind Duration: one tap every 6 s
    longer = models.raised_plan([SHIELD], [BASTION, "50% increased Blind Duration"], 2.0)
    assert abs(longer.lost - 0.55 / 6) < 1e-9


def test_no_blind_out_of_reach_or_without_a_shield():
    far = models.raised_plan([SHIELD], [BASTION], 5.0)
    assert far.blind == 0 and far.lost == 0 and "3 m" in far.how
    none = models.raised_plan([STRIKE], [BASTION], 2.0)
    assert none.blind == 0 and "no Raise Shield" in none.how
    assert models.raised_plan([SHIELD], ["Blind Enemies on Hit"], 2.0).how == ""  # not a raised line


def test_model_costs_the_main_skill_its_taps():
    mods, notes = models.raised_model([SHIELD, STRIKE], [BASTION], 2.0, STRIKE)
    assert len(mods) == 1 and abs(mods[0]["scale"] + 100 * 0.55 / 4) < 0.01 and notes[0]["confidence"] == "LOW"
    assert models.raised_model([SHIELD], [BASTION], 5.0, STRIKE)[0] == []  # out of reach: no taps, no cost


def test_blinded_box_reads_the_plan():
    plan = models.raised_plan([SHIELD], [BASTION], 2.0)
    up, how = _debuff_source(BASTION, "conditionEnemyBlinded", [BASTION], {"_raised_plan": plan})
    assert up == 1.0 and "tap Raise Shield" in how
    assert _debuff_source(BASTION, "conditionEnemyBlinded", [BASTION], {})[0] == 0.0  # (before: 0, "not modelled")


def test_taps_are_presses_the_search_can_turn_off():
    # (THE USER, 14 Nov: an item-granted skill the build presses counts toward the button cap)
    plan = models.raised_plan([SHIELD], [BASTION], 2.0)
    assert plan.taps == 0.25 and "a button" in plan.how
    off = models.raised_plan([SHIELD], [BASTION], 2.0, taps=False)
    assert off.blind == 0 and off.lost == 0 and off.taps == 0 and "doesn't tap" in off.how
    assert models.raised_model([SHIELD], [BASTION], 2.0, STRIKE, taps=False)[0] == []


class _Objective:
    def __init__(self):
        self.dps_calls = []

    def dps(self, stats):
        return 900.0 if getattr(self, models.RAISED_TAPS, True) is False else 1000.0


class _Search:
    def __init__(self, presses):
        self.build = type("B", (), {})()
        self.build.objective, self.build.stats = _Objective(), {}
        setattr(self.build, models.ITEM_PRESSES, presses)
        self.settled = 0

    def settle(self):
        self.settled += 1


def test_play_guide_row_with_and_without_the_taps():
    from builder.search.play_guide import raised_row

    search = _Search({models.RAISE_SHIELD: (0.25, "tap Raise Shield once every 4 s")})
    row = raised_row(search, 1000.0)
    assert row["dps_with"] == 1000 and row["dps_without"] == 900 and "a button" in row["label"]
    assert models.raised_taps_on(search.build) and search.settled == 2  # put back
    assert raised_row(_Search({}), 1000.0) is None  # no taps: no row
