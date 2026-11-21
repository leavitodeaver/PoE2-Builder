"""MECHB50: minions alive dealing damage (PoB scores one a skill; probe POE2T4749)."""
from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill


def test_persistent_minions_count_their_limit_and_wolf_pack_its_wolves():
    sk = Skill("Skeletal Warrior Minion", 2, summon={"limit": 3, "desired": 2, "persistent": True, "duration": 0})
    assert models.minion_alive_count(sk)[0] == 3
    sk = Skill("Skeletal Warrior Minion", 2, summon={"limit": None, "desired": 2, "persistent": True, "duration": 0})
    assert models.minion_alive_count(sk)[0] == 2
    wolves = Skill("Wolf Pack", 3, summon={"limit": 1, "persistent": True, "duration": 0})
    count, how, confidence = models.minion_alive_count(wolves, 5)
    assert count == 5 and "5 wolves" in how and confidence.startswith("HIGH")
    zombie = Skill("Raise Zombie", 1, summon={"limit": 10, "persistent": False, "duration": 20})
    count, how, confidence = models.minion_alive_count(zombie)
    assert count == 1 and confidence == "UNVERIFIED" and "corpse" in how  # (its supply: not counted here)
    assert models.minion_alive_count(Skill("Unearth", 1, summon={"limit": 10, "persistent": True}))[2] == "-"


def test_only_the_hits_multiply():
    assert models.minion_count_scale(4, 100.0, 100.0) == pytest.approx(4.0)
    assert models.minion_count_scale(4, 60.0, 100.0) == pytest.approx((60 * 4 + 40) / 100)  # (the Ignite once)
    assert models.minion_count_scale(1, 60.0, 100.0) == 1.0


def test_bear_skills_take_their_default_stance_attack_time():
    mods, notes = models.bear_stance_model(Skill("Furious Slam", 1, attack=True, melee=True), 1.0)
    assert mods[0]["name"] == "Speed" and mods[0]["type"] == "BASE" and mods[0]["value"] == pytest.approx(0.4)
    assert notes[0]["confidence"] == "MEDIUM" and "x0.714" in notes[0]["basis"]
    assert models.bear_stance_model(Skill("Boneshatter", 1, attack=True, melee=True), 1.0) == ([], [])


def test_a_perfect_release_set_is_named_as_a_play_choice():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0}, {"label": "Perfect Strike", "TotalDPS": 250.0}]
    note, = models.perfect_timing_note(Skill("Perfect Strike", 1, attack=True, melee=True), "Perfect Strike", sets)
    assert note["confidence"] == "MEDIUM" and "x0.400" in note["basis"] and "play" in note
    assert models.perfect_timing_note(Skill("Perfect Strike", 1, attack=True), "Normal Strikes", sets) == []


def test_volcano_channels_to_its_last_stage():
    volcano = Skill("Volcano", 1, spell=True)
    sets = [{"label": "Eruption", "AverageDamage": 100.0}, {"label": "Projectiles", "AverageDamage": 20.0}]
    mods, notes = models.volcano_channel_model(volcano, "Eruption", sets)
    assert mods[0]["value"] == pytest.approx(100 * (5.5 / 5 - 1), abs=0.01)  # (3 stages: 1 + 4.5 over 5 cast times)
    assert "a tap (no stage) x0.500" in notes[0]["basis"] and "x1.167" in notes[0]["basis"]
    assert models.volcano_channel_model(volcano, "Projectiles", sets) == ([], [])
    assert models.channel_windup_model(volcano, 1.0, 4.0, 2.0, 0.5) == ([], [])  # (left to the Volcano model)
