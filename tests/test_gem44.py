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


def test_triskelion_cascade_frame_rows_only_until_r61_10():
    # (GEM44) a 1 s cast at 1 cast a second, pressed once a second: cooldown 1 + 300% x 1 s = 4 s -> 0.25 Empowered/s
    f1, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 1)
    assert e == pytest.approx(0.25) and f1 == pytest.approx(1 + 0.25 * (0.89 - 1))  # one area: a small LOSS
    f7, _ = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 7)
    assert f7 == pytest.approx(1 + 0.25 * (0.89 * 7 - 1))
    assert models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 0)[0] == pytest.approx(0.75)  # cast range: lost
    main = models.Skill("Comet", 1)
    stats = {models.TRISKELION_DAMAGE: -11, models.TRISKELION_AREA: -31, models.TRISKELION_CD_PCT: 300}
    mods, notes = models.triskelion_cascade_model(main, {"Spell", "Cascadable"}, 1.0, 1.0, 1.0, stats)
    assert mods == [] and "not scored" in notes[0]["value"] and "R61-10" in notes[0]["basis"]
    assert "7 area(s) on the boss" in notes[0]["basis"]
    mods, _ = models.triskelion_cascade_model(main, {"Spell", "Cascadable"}, 1.0, 1.0, 1.0, stats, areas=7)
    assert mods[0]["value"] == pytest.approx(100 * (f7 - 1), abs=0.01)
    assert models.triskelion_cascade_model(main, {"Spell"}, 1.0, 1.0, 1.0, stats) == ([], [])  # not Cascadable


def test_wing_blast_shockwave_from_a_unique_boss_deals_50_percent_more(monkeypatch):
    # (GEM44) the Heavy Stunned boss (Unique) releases the shockwave: the Shockwave set x1.5, Boneshatter unchanged
    from tests.test_gem_description_mode import MAIN_STUN_STATS

    sets = [{"label": "Wing Blast", "TotalDPS": 58.3, "Speed": 1.0}, {"label": "Shockwave", "TotalDPS": 330.5, "Speed": 1.0}]
    wing = models.Skill("Wing Blast", 1)
    with_more = models.stun_set_model(wing, "Shockwave", sets, MAIN_STUN_STATS, 50.0, "test")
    monkeypatch.setattr(models, "STUN_WAVE_MORE", {})
    without = models.stun_set_model(wing, "Shockwave", sets, MAIN_STUN_STATS, 50.0, "test")
    f_with, f_without = (1 + with_more[0][0]["value"] / 100) * 330.5, (1 + without[0][0]["value"] / 100) * 330.5
    assert f_with - 58.3 == pytest.approx(1.5 * (f_without - 58.3), rel=1e-3)  # the shockwave part x1.5
    assert "Unique enemy" in with_more[1][0]["basis"] and "Unique enemy" not in without[1][0]["basis"]
