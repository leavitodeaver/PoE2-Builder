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
