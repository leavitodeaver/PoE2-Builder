"""GEM49: a cloud's re-application step (AUDIT41 A41-14), the main Forge Hammer's set-off Fissures (DETONATED_STAY),
Thunderstorm's Drenched at SEARCH41's upkeep rate, and Rakiata's Flow's Exposure order (RESEARCH67 R67-2)."""
import pytest

from builder.search import models


def test_a_clouds_poison_step_is_the_longer_of_2_02_seconds_and_the_poison_duration():
    assert models.cloud_poison_step() == models.GAS_POISON_STEP == 2.02
    assert models.cloud_poison_step(2.0) == 2.02  # (the base 2.0 s Poison: R67-1's measured step)
    assert models.cloud_poison_step(4.0) == 4.0  # (re-Poisoned when the last one ends: the lower reading)


def test_a_longer_poison_duration_gives_fewer_applications_a_cloud_in_the_headline():
    assert models.cloud_applications(7.9) == 4.0  # (the fixed 2.02 s step: the row)
    assert models.cloud_applications(7.9, step=models.cloud_poison_step(4.0)) == 2.0  # (landing, then at 4 s)
    assert models.cloud_applications(4.0, step=models.cloud_poison_step(2.0)) == models.cloud_applications(4.0)


def test_the_main_forge_hammers_fissures_need_the_boss_still_there_when_a_warcry_sets_them_off():
    assert "Forge Hammer" in models.DETONATED_STAY and "Forge Hammer" in models.DETONATED_SETS


def test_drenched_reads_a_kept_up_thunderstorm_at_its_upkeep_rate():
    storm = models.Skill("Thunderstorm", group=2)
    main = models.Skill("Entangle", group=1)
    row = {"speed": 3.0, "duration": 4.0, "_press": None, "_upkeep": 0.125}
    mods, notes = models.drenched_model([storm, main], {}, main, {2: row})
    assert notes[0]["value"] == "50% uptime" and "kept up" in notes[0]["basis"]
    assert mods[0]["value"] == pytest.approx(-50.0)
    _, notes = models.drenched_model([storm, main], {}, main, {2: {**row, "_upkeep": None}})
    assert notes[0]["value"] == "100% uptime" and "at PoB's rate" in notes[0]["basis"]  # (no upkeep row: as before)
    _, notes = models.drenched_model([storm, main], {}, main, {2: {**row, "_press": 0.25}})
    assert "pressed 25%" in notes[0]["basis"]  # (a chosen press rate first)


def test_rakiatas_flow_exposure_order_carries_research67s_tag():
    note = models.frost_bomb_spot_note(100.0, 120.0, "away")
    assert "R67-2" in note["basis"] and "UNVERIFIED" not in note["basis"]
