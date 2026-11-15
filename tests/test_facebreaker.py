"""COV32 (PATCH_CHECK MB-5 / R5): Facebreaker's "+3 to +4 per Boss's Face Broken" counts a finished campaign's 56
broken boss faces (bridge.resetConfig BOSS_FACES_BROKEN), not PoB's empty box (0 faces: 8-12 damage instead of
176-236). A loaded code's own count is kept. The note names the count and the 60-face row."""

from __future__ import annotations

import faulthandler

import pytest

from builder.search.assumptions import facebreaker_notes

LINE = "Has 8 to 12 Physical damage, +3 to +4 per Boss's Face Broken"
GLOVES = ("Rarity: Unique\nFacebreaker\nStocky Mitts\n--------\n" + LINE + "\n1% more Unarmed Damage per 5 Strength\n"
          "Can Attack as though using a One Handed Mace while both of your hand slots are empty\n"
          "Unarmed Attacks that would use an Equipped One Hand Mace's damage use this Item's damage")


def test_the_note_names_the_count_and_the_sixty_face_row():
    [note] = facebreaker_notes(["Facebreaker\nStocky Mitts\n" + LINE], 56)
    assert note["value"] == 56 and note["confidence"] == "theory"
    assert "176-236 at 56 faces" in note["basis"] and "LOW for 56 exactly" in note["basis"]
    assert "with 60 faces: 188-252 (+12-16 base damage, +6.8% of the item's damage)" in note["basis"]
    [fire] = facebreaker_notes(["Has 9 to 14 Fire damage, +3 to +5 per Boss's Face Broken"], 56)
    assert "Fire damage 177-294 at 56 faces" in fire["basis"] and "(+12-20 base damage" in fire["basis"]
    [own] = facebreaker_notes([LINE], 30)  # (a loaded character's own tracker count)
    assert own["value"] == 30 and own["confidence"] == "reported" and "The loaded build's own count" in own["basis"]
    assert facebreaker_notes(["Has 8 to 12 Physical damage"], 56) == []


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_reset_config_counts_56_faces_and_keeps_a_loaded_count(engine):
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    assert engine.call("resetConfig")["input"]["configBossFaceBroken"] == 56
    engine.call("equip", slot="Gloves", fields=[], raw=GLOVES)
    engine.call("addSkillGroup", text="Boneshatter 20/0 1", main=True)

    def dps(faces):
        engine.call("setConfig", vars={"configBossFaceBroken": faces}, fields=[])
        return engine.call("stats", fields=["TotalDPS"])["TotalDPS"]

    none, campaign = dps(False), dps(56)
    assert none > 0, "Boneshatter should use Facebreaker's damage unarmed"
    # (the base damage goes 8-12 -> 176-236, x20.6; the rest of the hit scales with it: a large gap, not a few %)
    assert campaign > 10 * none, (none, campaign)
    engine.call("setConfig", vars={"configBossFaceBroken": 30}, fields=[])
    assert engine.call("resetConfig")["input"]["configBossFaceBroken"] == 30  # (the code's own count stays)
