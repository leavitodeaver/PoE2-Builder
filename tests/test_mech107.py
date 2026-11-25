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


KELARI_STATS = {"skill_specific_stat_description_mode": 2, "base_secondary_skill_effect_duration": 900}


def test_kelaris_commands_apply_their_stacks_at_their_press_rates():
    presses = {"Kelari's Brutality": (0.2, "x"), "Kelari's Judgment": (1 / 12, "x"), "Ruzhan's Brand": (0.3, "x"),
               "Kelari's Deception": (0.25, "x")}
    rows = dict((how.split(":")[0], rate) for how, rate in models.djinn_critical_weakness(presses, 0.3))
    assert set(rows) == {"Kelari's Brutality", "Kelari's Judgment", "Kelari's Deception"}  # (Ruzhan's carry none)
    assert rows["Kelari's Brutality"] == pytest.approx(0.2 * models.blades_on_boss(0.3) * 1.0)
    assert rows["Kelari's Judgment"] == pytest.approx(4.0 / 12)
    # a use every 4 s, within the clone's 4 s life: one clone explosion a use, the blast 1 + the clone 2
    assert rows["Kelari's Deception"] == pytest.approx(0.25 * (1 + 2 * models.clone_explosions(4.0)))
    assert models.djinn_critical_weakness({"Kelari's Judgment": (0.0, "x")}, 0.3) == []


def test_beetle_explosions_follow_the_builds_corpses():
    kelari = models.Skill("Kelari's Malediction", 1, corpses=True, level_stats=KELARI_STATS)
    assert models.kelari_beetle_explosions(kelari, {models.ADD_KILLS_STAT: 0.25})[0] == pytest.approx(0.5)
    assert models.kelari_beetle_explosions(kelari, {models.ADD_KILLS_STAT: 5.0})[0] == pytest.approx(2 / 0.9)
    assert models.kelari_beetle_explosions(kelari, {})[0] == 0.0  # (no corpse source: 0)


def test_on_hit_stacks_add_to_the_held_stacks():
    stacks, how = models.critical_weakness_stacks([], [], {}, None, 2.0, on_hit=[("Kelari's Judgment: x", 1.0)])
    assert stacks == pytest.approx(4.0) and "Kelari's Judgment" in how
    assert models.critical_weakness_stacks([], [], {}, None, 2.0, on_hit=[("a", 3.0), ("b", 3.0)])[0] == 20.0
    mods, _ = models.critical_weakness_mods((stacks, how))
    assert mods[0]["name"] == "SelfCritChance" and mods[0]["enemy"] and mods[0]["value"] == pytest.approx(2.0)
