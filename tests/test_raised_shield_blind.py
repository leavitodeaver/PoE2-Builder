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
