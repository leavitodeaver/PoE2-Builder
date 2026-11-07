"""The coverage audit's checks of what PoB parses (builder/ledger/parse_audit.py): conditions nothing sets (f) and lost parts (g)."""

import pytest

from builder.ledger import parse_audit as pa

pytestmark = pytest.mark.skipif(not pa.POB_SRC.exists(), reason="needs the pinned PoB checkout")


def mod(name, kind="INC", value=10, tags=(), flags=0, keywords=0, inner=None):
    return {"n": name, "t": kind, "v": value, "f": flags, "k": keywords, "g": list(tags), "i": inner}


def test_who_sets_a_condition():
    raw = {"passives": [{"line": "x", "mods": [mod("Condition:LedgerTestSet", "FLAG", True)]}], "skills": []}
    setters = pa.Setters(raw)
    assert setters.status("Condition:LedgerTestSet")[0] == "line"
    assert setters.status("Condition:CriticalStrike")[0] == "calc"  # per hit, in PoB's own calculation
    assert setters.status("Condition:BeenHitRecently")[0] == "config-on"  # the condition resolver turns it on
    assert setters.status("Condition:Sprinting")[0] == "config-off"
    assert setters.status("Condition:NobodyEverSetsThis")[0] == "never"
    assert setters.status("enemy:Condition:RareOrUnique")[0] == "config-on"  # the boss preset (enemyIsBoss) is on


def test_a_line_is_blocked_only_when_every_modifier_is():
    setters = pa.Setters({"passives": [], "skills": []})
    sprint = mod("MovementSpeed", tags=["Condition=Sprinting"])
    assert pa.never_on([sprint], setters)
    assert not pa.never_on([sprint, mod("MovementSpeed")], setters)  # part of it counts
    assert not pa.never_on([mod("Damage", tags=["Condition=CriticalStrike"])], setters)


def test_what_a_parse_lost():
    kinds = lambda text, mods: {x["kind"] for x in pa.lost(text, mods)}
    assert "more read as increased" in kinds("20% more Damage", [mod("Damage", "INC", 20)])
    assert "sign lost" in kinds("10% reduced Attack Speed", [mod("Speed", "INC", 10, flags=1)])
    assert not kinds("10% reduced Attack Speed", [mod("Speed", "INC", -10, flags=1)])
    assert "condition lost" in kinds("20% increased Damage while Fortified", [mod("Damage", "INC", 20)])
    assert "number lost" in kinds("25% chance to create Shocked Ground", [mod("ShockedGroundChance", "BASE", 20)])
    assert not kinds("(20-30)% increased Damage", [mod("Damage", "INC", 25)])  # a roll range, parsed at a roll
    assert "restriction lost" in kinds("20% increased Damage with Bows", [mod("Damage", "INC", 20)])
    assert not kinds("20% increased Damage with Bows", [mod("Damage", "INC", 20, tags=["Condition=UsingBow"])])


def test_gated_names_need_their_uptime():
    line = [mod("AncestralBoostDamage", "INC", 30)]
    assert pa.gated(line, {"BoostedAttackUptimeRatio": 0})
    assert not pa.gated(line, {"BoostedAttackUptimeRatio": 25})
