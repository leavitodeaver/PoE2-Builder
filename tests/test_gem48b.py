"""GEM48: a Poison cloud's applications and cover (RESEARCH67 R67-1); Moonbeam lands only if the boss is still there."""
import pytest

from builder.search import models


def test_a_cloud_poisons_on_landing_then_every_2_02_seconds_while_it_lives():
    assert models.cloud_applications(4.0) == 2.0  # (the third would come at ~4.04 s, after it ends)
    assert models.cloud_applications(7.02) == 4.0  # (THE USER's clip: 4 a cloud)
    assert models.cloud_applications(6.0) == 3.0  # (Gas Grenade's base 6 s)
    assert models.cloud_applications(0.0) == 0.0


def test_an_early_explosion_keeps_the_landing_poison_and_the_boss_walking_out_slows_the_rest():
    assert models.cloud_applications(4.0, up=1.0) == 1.0  # (Ignited at landing: one Poison, then it blows up)
    ends = 0.5
    assert models.cloud_applications(4.0, ends) == pytest.approx(1 + 2.718281828 ** (-ends * 2.02))
    leave = 0.1  # (the boss walks out 0.1 times a second: each later Poison needs it still inside)
    assert models.cloud_applications(4.0, leave=leave) == pytest.approx(1 + 2.718281828 ** (-leave * 2.02))
    assert models.cloud_applications(4.0, ends, leave=leave) == pytest.approx(1 + 2.718281828 ** (-(ends + leave) * 2.02))


def test_a_clouds_poisons_cover_two_seconds_each():
    assert models.cloud_cover(7.02) == pytest.approx(8.0)  # (R67: 8.0 s against its 7.02 s life)
    assert models.cloud_cover(4.0) == pytest.approx(4.0)


def test_a_moonbeam_lands_only_if_the_boss_is_still_in_its_spot():
    still, _ = models.moonbeam_model(0.98, 100.0, 2.0, 1.0, "up")
    dps, notes = models.moonbeam_model(0.98, 100.0, 2.0, 1.0, "up", there=(0.9, 1.0, "its 1.6 m spot: 90% still inside"))
    assert dps == pytest.approx(still * 0.9) and "x0.90" in notes[0]["basis"] and "standing still 98.0" in notes[0]["basis"]


def test_accelerated_growths_explosion_hits_only_a_boss_still_at_the_plant():
    part = [{"source": "Vine Arrow", "uses": 2.0, "blast": 100.0}]
    still, _ = models.accelerated_growth_model(part, 0.5, ["test"])
    dps, notes = models.accelerated_growth_model(part, 0.5, ["test"], there=(0.8, 1.0, "80% still inside"))
    assert still == pytest.approx(100.0) and dps == pytest.approx(80.0)
    assert "x 80% the boss still there" in notes[0]["basis"] and "standing still 100.0 DPS" in notes[0]["basis"]


def test_storm_lances_lodged_zaps_count_only_while_the_boss_stays_in_reach():
    main = models.Skill("Storm Lance", group=1, attack=True)
    sets = [{"label": "Projectile", "TotalDPS": 30.0}, {"label": "Lodged Spear", "TotalDPS": 50.0},
            {"label": "Detonation", "TotalDPS": 100.0}]
    _, still = models.detonated_set_model(main, "Lodged Spear", sets, 1.0, 0.0, "none")
    mods, notes = models.detonated_set_model(main, "Lodged Spear", sets, 1.0, 0.0, "none",
                                             lodged=(0.8, 1.0, "its 3.0 m reach: 80%"))
    assert "Lodged Spear x 80%" in notes[0]["basis"] and notes[0]["value"] == "x0.800"
    assert notes[0]["set_shares"]["Lodged Spear"] == 0.8 and still[0]["value"] == "x1.000"
