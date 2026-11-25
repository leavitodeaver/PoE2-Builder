"""GEM44: Flame Breath's Empowered breaths (models.flame_breath_empower_model)."""
import pytest

from builder.search import models


def test_flame_breath_empowered_share_from_power_charge_gains():
    fb = models.Skill(models.FLAME_BREATH, 1)
    mods, notes = models.flame_breath_empower_model(fb, 10.0, 0.048, "a crit ring", [])
    gain = next(m for m in mods if m["name"] == "DamageGainAsLightning")
    assert gain["type"] == "BASE" and gain["value"] == pytest.approx(50 * 0.48, abs=0.01)  # 0.048/s x 10 s
    assert notes[0]["confidence"] == "MEDIUM" and "48% of breaths" in notes[0]["label"]
    mods, notes = models.flame_breath_empower_model(fb, 10.0, 0.5, "every 2 s", ["Falling Thunder"])
    assert mods[0]["value"] == pytest.approx(50.0) and "Falling Thunder" in notes[0]["basis"]  # capped at every breath
    mods, notes = models.flame_breath_empower_model(fb, 10.0, 0.0, "", [])
    assert mods == [] and notes[0]["value"] == "+0%"  # no Power Charge source: nothing, said
    assert models.flame_breath_empower_model(models.Skill("Rampage", 1), 10.0, 1.0, "", []) == ([], [])
    assert models.flame_breath_empower_model(fb, 10.0, 1.0, "", [], forbidden={models.FLAME_BREATH_EMPOWER_ENTRY}) == ([], [])
