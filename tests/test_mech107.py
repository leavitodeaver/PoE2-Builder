"""MECH107: Critical Weakness sits ON THE BOSS (PoB's enemy SelfCritChance BASE, as its own config box), so the minions'
hits read it too; the player's hits read it exactly as the old player CritChance BASE mod."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import models


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_critical_weakness_mod_is_on_the_enemy():
    mods, notes = models.critical_weakness_mods((8.0, "x"))
    assert mods == [models.raw_mod("SelfCritChance", "BASE", 4.0, enemy=True)] and mods[0]["enemy"] is True
    assert notes and "Critical Hit Chance" in notes[0]["value"]
    assert "enemy" not in models.raw_mod("CritChance", "BASE", 4.0)


def crit(engine, mods: list[dict], field: str) -> float:
    engine.call("setAssumptions", mods=mods, fields=[])
    return models.number(engine.call("stats", fields=[field]).get(field))


def test_an_enemy_mod_reaches_the_players_hits_as_the_old_player_mod(engine):
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("setGroup", gems=[{"name": "Fireball", "level": 20, "quality": 0}], main=True, fullDPS=True, fields=[])
    try:
        base = crit(engine, [], "CritChance")
        player = crit(engine, [models.raw_mod("CritChance", "BASE", 4.0)], "CritChance")
        enemy = crit(engine, [models.raw_mod("SelfCritChance", "BASE", 4.0, enemy=True)], "CritChance")
        print("PROBE player crit: none", base, "player mod", player, "enemy mod", enemy)
        assert player > base and enemy == pytest.approx(player)
    finally:
        engine.call("setAssumptions", mods=[], fields=[])
        engine.call("newBuild")


def test_an_enemy_mod_reaches_the_minions_hits_where_the_player_mod_does_not(engine):
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("setGroup", gems=[{"name": "Skeletal Warrior", "level": 20, "quality": 0}], main=True, fullDPS=True,
                fields=[])
    try:
        base = crit(engine, [], "Minion.CritChance")
        player = crit(engine, [models.raw_mod("CritChance", "BASE", 4.0)], "Minion.CritChance")
        enemy = crit(engine, [models.raw_mod("SelfCritChance", "BASE", 4.0, enemy=True)], "Minion.CritChance")
        print("PROBE minion crit: none", base, "player mod", player, "enemy mod", enemy)
        assert player == pytest.approx(base) and enemy > base
    finally:
        engine.call("setAssumptions", mods=[], fields=[])
        engine.call("newBuild")
