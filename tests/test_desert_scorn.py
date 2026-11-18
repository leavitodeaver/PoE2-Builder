"""COV32 (the manager, 15 Nov 06:59; MECH76): Desert's Scorn's "Enemies standing on Chilled Ground take 25% increased
Fire Damage" (PoB leaves it unparsed) counts at the boss's share of the fight on the build's Chilled Ground, MECH-A's
build.chilled_ground_up, through the stand-in "Nearby Enemies take 25% increased Fire Damage" (a share: line)."""

from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.ledger import item_lines, translate
from builder.search.assumptions import Assumptions

LINE = "Enemies standing on Chilled Ground take 25% increased Fire Damage"


def test_the_line_reads_mech_as_chilled_ground_share():
    entry, lines = translate(LINE)
    assert lines == ["share:BossOnChilledGround Nearby Enemies take 25% increased Fire Damage"]
    me = SimpleNamespace(build=SimpleNamespace())
    assert Assumptions.chilled_ground_share(me)[0] == 0  # (before MECH76's value, or no source: 0)
    me.build.chilled_ground_up = (0.0, "")
    share, how = Assumptions.chilled_ground_share(me)
    assert share == 0 and "no Chilled Ground source" in how
    me.build.chilled_ground_up = (0.45, "Frost Nexus's ground under the boss")
    assert Assumptions.chilled_ground_share(me) == (0.45, "Frost Nexus's ground under the boss")
    me.build.chilled_ground_up = (1.7, "x")
    assert Assumptions.chilled_ground_share(me)[0] == 1.0


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_stand_in_raises_fire_damage_at_its_share(engine):
    from builder.search import models

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Fireball 20/0 1", main=True)
    stand_in = item_lines(translate(LINE)[1])[0]
    engine.call("equip", slot="Ring 1", fields=[],
                raw="\n".join(["Rarity: Rare", "Probe Ring", "Gold Ring", "--------", stand_in]))

    def dps(share):
        engine.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerShareBossOnChilledGround", "BASE", share)])
        return engine.call("stats", fields=["TotalDPS"])["TotalDPS"]

    none, half, full = dps(0), dps(50), dps(100)
    assert none > 0 and full > half > none
    assert half - none == pytest.approx((full - none) / 2, rel=0.05)


COLD_LINE = "Enemies standing on Ignited Ground take 25% increased Cold Damage"


def test_the_cold_line_reads_mech_as_ignited_ground_share():
    """COV39 (MECH85's note; MECH79's build.ignited_ground_up, set for COV-A): the notable's Ignited Ground / Cold half."""
    entry, lines = translate(COLD_LINE)
    assert lines == ["share:BossOnIgnitedGround Nearby Enemies take 25% increased Cold Damage"]
    me = SimpleNamespace(build=SimpleNamespace())
    assert Assumptions.ignited_ground_share(me)[0] == 0  # (before MECH79's value, or no source: 0)
    me.build.ignited_ground_up = (0.0, "")
    share, how = Assumptions.ignited_ground_share(me)
    assert share == 0 and "no Ignited Ground source" in how
    me.build.ignited_ground_up = (0.3, "Flame Wall's ground at the boss")
    assert Assumptions.ignited_ground_share(me) == (0.3, "Flame Wall's ground at the boss")
    me.build.ignited_ground_up = (1.4, "x")
    assert Assumptions.ignited_ground_share(me)[0] == 1.0


def test_the_cold_stand_in_raises_cold_damage_at_its_share(engine):
    from builder.search import models

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Frostbolt 20/0 1", main=True)
    stand_in = item_lines(translate(COLD_LINE)[1])[0]
    engine.call("equip", slot="Ring 1", fields=[],
                raw="\n".join(["Rarity: Rare", "Probe Ring", "Gold Ring", "--------", stand_in]))

    def dps(share):
        engine.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerShareBossOnIgnitedGround", "BASE", share)])
        return engine.call("stats", fields=["TotalDPS"])["TotalDPS"]

    none, half, full = dps(0), dps(50), dps(100)
    assert none > 0 and full > half > none
    assert half - none == pytest.approx((full - none) / 2, rel=0.05)
