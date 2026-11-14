"""MECHC21 (MECHC20's Handover; an UNDERCOUNT: recovery.EVENT_UNREAD named it, 0): Invigorating Hate (PassiveSkills
rage27) "Consume all Rage when Shapeshifting to Human form to recover 1% of maximum life per Rage Consumed", at the
build's own shifts back to Human form (conditions.human_shifts), each consuming the Rage built since the last, so the Rage
held for Rage's own bonus falls too (conditions.rage_on_shift; no overcount of it)."""
from __future__ import annotations

from builder.search import conditions as c
from builder.search import recovery

HATE = "Consume all Rage when Shapeshifting to Human form to recover 1% of maximum life per Rage Consumed"
GAIN = "Gain 3 Rage on Melee Hit"
BEAR = {"Attack", "Melee", "Shapeshift", "Bear"}
STATS = {"Speed": 2.0, "HitChance": 100, "MaximumRage": 30, "_main_types": BEAR}


def test_shifts_back_from_the_build_s_presses():
    others = {"Warcry": ({"Warcry"}, 0.2), "Maul": ({"Shapeshift", "Bear"}, 1.0),
              "Iron Ward": ({"UsableWhileShapeshifted"}, 0.5)}
    rate, how = c.human_shifts(BEAR, others, 2.0)
    assert rate == 0.2 and "Warcry" in how and "Maul" not in how and "Iron Ward" not in how
    assert c.human_shifts(BEAR, {"Maul": ({"Shapeshift", "Bear"}, 1.0)}, 2.0)[0] == 0  # no shift back: 0
    human = c.human_shifts({"Attack", "Melee"}, {"Rampage": ({"Shapeshift", "Bear"}, 0.3)}, 2.0)
    assert human[0] == 0.3 and "next use back" in human[1]
    assert c.human_shifts({"Attack"}, {}, 2.0)[0] == 0


def test_each_shift_consumes_the_rage_built_since_the_last():
    steady = c.rage_held([GAIN], dict(STATS))[0]
    assert steady == 30
    stats = dict(STATS, _human_shifts=(0.25, "one shift every 4 s"))
    held, basis = c.rage_held([GAIN, HATE], stats)
    # 6 Rage/s from 0 for 4 s: 24 at each shift, 12 held on average (Rage's own bonus falls with it)
    assert held == 12 and "consumed on each shift" in basis
    assert stats["_charge_flow"]["Rage"][0] == 6.0
    none = dict(STATS)
    assert c.rage_held([GAIN, HATE], none)[0] == 30 and none["_charge_flow"]["Rage"][0] == 0  # no shifts read: 0


def test_life_per_rage_consumed():
    income = recovery.Income()
    recovery.spent_income(income, {"Life": 1000, "Mana": 100}, [HATE], rage=(6.0, "one shift every 4 s"))
    assert abs(income.pool("Life") - 60.0) < 1e-6  # 1% of 1,000 x 6 Rage/s
    assert "Life on Rage consumed when Shapeshifting" not in recovery.unread([HATE])
    assert recovery.RAGE_SHIFT in recovery.SPENT_LINES and recovery.MINION_REVIVED in recovery.SPENT_LINES


def test_rage_above_the_human_cap_while_shapeshifted_is_consumed_too():
    """MECHC24: '+N to maximum Rage while Shapeshifted' with a Human-form main skill: in the form until the main skill's
    next use (1 / Speed s), Rage climbs above the Human cap and the shift back consumes it; PoB's MaximumRage holds the
    line itself when the main skill is a form, so nothing is added then."""
    shifted = "+3 to maximum Rage while Shapeshifted"
    human = {"Speed": 2.0, "HitChance": 100, "MaximumRage": 30, "_main_types": {"Attack", "Melee"},
             "_human_shifts": (0.1, "one shift every 10 s")}
    plain = dict(human)
    c.rage_held([GAIN, HATE], plain)
    assert abs(plain["_charge_flow"]["Rage"][0] - 3.0) < 1e-9  # 30 a shift
    more = dict(human)
    held, _ = c.rage_held([GAIN, HATE, shifted, "+12 to maximum Rage while Shapeshifted"], more)
    # 6 Rage/s: 30 at the Human cap, then 0.5 s in the form: +3 more (under the +15), 33 a shift
    assert abs(more["_charge_flow"]["Rage"][0] - 3.3) < 1e-9 and "above the Human cap" in more["_charge_flow"]["Rage"][1]
    assert held == c.rage_held([GAIN, HATE], dict(human))[0]  # the Human-form main skill holds no more
    bear = dict(STATS, MaximumRage=30, _human_shifts=(0.1, "one shift every 10 s"))
    c.rage_held([GAIN, HATE, shifted], bear)
    assert abs(bear["_charge_flow"]["Rage"][0] - 3.0) < 1e-9  # a form main skill: PoB's MaximumRage holds it


def test_rageforged_two_spends_its_rage_on_seven_uses_in_ten():
    """MECHC24: Rageforged II's rage_skip_consume_chance_% 30 ('Supported Skills have 30% chance to gain the benefits of
    this Support without spending additional Rage'): 7 Rage a use on average; Rageforged I spends 10."""
    assert abs(c.RAGE_PER_USE["Rageforged II"] - 7) < 1e-9 and c.RAGE_PER_USE["Rageforged I"] == 10
