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


def test_lightning_rods_quality_chance_for_another_burst_is_more_damage_on_it():
    rod = models.Skill("Lightning Rod", group=1, attack=True,
                       level_stats={"lightning_rod_%_chance_for_additional_burst_on_landing": 20})
    mods, notes = models.gem_stat_model([rod], main=rod, stats={})
    line = [m for m in mods if m.get("skill") == "Lightning Rod" and m.get("line") == "1% more Damage"]
    assert line and line[0]["scale"] == pytest.approx(20.0)
    assert any(n.get("entry") == "model-gem49-lightning-rod-extra-burst" for n in notes)
    mods, _ = models.gem_stat_model([rod], forbidden={"model-gem49-lightning-rod-extra-burst"}, main=rod, stats={})
    assert not [m for m in mods if m.get("skill") == "Lightning Rod" and m.get("line") == "1% more Damage"]


def test_shattering_palms_explosion_comes_from_a_shattered_coat_not_its_own_cast_rate():
    palm = [{"label": "Melee", "AverageDamage": 49.056, "Speed": 0.68721765}]
    blast = [{"label": "Explosion", "AverageDamage": 165.5403288, "Speed": 1.036}]
    # (POE2T9792's skeleton: the Strike's 49 a hit is far under the 12,788 threshold: about one explosion a minute)
    mods, notes = models.shattering_palm_model("Freezing Explosion", palm, blast, 12788.0)
    melee = 49.056 * 0.68721765
    total = melee + 165.5403288 * 0.68721765 * 49.056 / 12788.0
    assert mods[0]["value"] == pytest.approx(100 * (total / (165.5403288 * 1.036) - 1), abs=0.01)
    assert "Freezing Explosion" in notes[0]["basis"]
    # (a build whose Strike passes the threshold: every Strike explodes; the Strike scored gains the explosions)
    mods, _ = models.shattering_palm_model("Shattering Palm", [{**palm[0], "AverageDamage": 20000.0}], blast, 12788.0)
    assert mods[0]["value"] == pytest.approx(100 * ((20000.0 + 165.5403288) / 20000.0 - 1), abs=0.01)
    assert models.shattering_palm_model("Freezing Explosion", palm, blast, 12788.0,
                                        {models.SHATTERING_PALM_ENTRY}) == ([], [])
