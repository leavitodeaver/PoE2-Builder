"""GEM40: COV56's calibration overcounts (O3, example build 13 Earthshatter): the slam's Aftershocks repeat the slam's
impact, not the spikes a Warcry or a Detonator shatters (KeywordPopups Aftershock)."""
import pytest

from builder.search import models
from builder.search.models import Skill


SETS = [{"label": "Slam", "TotalDPS": 100.0}, {"label": "Explosion", "TotalDPS": 360.0}]


def test_aftershock_taken_out_of_the_shattered_spikes():
    es = Skill("Earthshatter", 1, attack=True)
    # (2.8 slams a second, 0.162 Warcries a second: 6 spikes lying, 34.7% of the slams' spikes shattered)
    base, _ = models.detonated_set_model(es, "Slam", SETS, 2.8, 0.162, "a Warcry")
    s = models.detonated_share("Earthshatter", 2.8, 0.162)
    assert base[0]["value"] == pytest.approx(100 * ((100 + 360 * s) / 100 - 1), abs=0.01)
    # (a 25% Aftershock: its DPS line x1.25 sits on both rows; the Explosion's own is 360 / 1.25)
    cut, notes = models.detonated_set_model(es, "Slam", SETS, 2.8, 0.162, "a Warcry", aftershock=1.25)
    assert cut[0]["value"] == pytest.approx(100 * ((100 + 360 / 1.25 * s) / 100 - 1), abs=0.01)
    assert cut[0]["value"] < base[0]["value"]
    assert "Aftershocks" in notes[0]["basis"] and "the same damage as the initial impact" in notes[0]["basis"]
    # (no Aftershock, or a value under 1: no change)
    same, notes1 = models.detonated_set_model(es, "Slam", SETS, 2.8, 0.162, "a Warcry", aftershock=1.0)
    assert same[0]["value"] == pytest.approx(base[0]["value"]) and "Aftershocks" not in notes1[0]["basis"]


def test_aftershock_split_with_spikes_and_on_the_explosion_scored():
    es = Skill("Earthshatter", 1, attack=True)
    s = models.detonated_share("Earthshatter", 2.8, 0.162)
    standing = models.spike_standing("Earthshatter", 2.8, 0.162)
    mods, _ = models.detonated_set_model(es, "Explosion", SETS, 2.8, 0.162, "a Warcry", per_spike=6.0,
                                         aftershock=1.25)
    want = (100 + 360 / 1.25 * s) * (1 + 0.06 * standing) / 360
    assert mods[0]["value"] == pytest.approx(100 * (want - 1), abs=0.01)


def test_devour_main_is_a_far_use_and_side_eats_near():
    devour, palm = Skill("Devour", 1, attack=True), Skill("Killing Palm", 2, attack=True)
    mods, notes = models.devour_target_model([devour, palm], devour, 1.7)
    assert mods[0]["name"] == "Speed" and mods[0]["value"] == pytest.approx(20.0)
    assert "far" in notes[0]["label"] and "0" in notes[0]["basis"]
    side, side_notes = models.devour_target_model([devour, palm], palm, 1.7)
    assert side[0]["value"] == pytest.approx(-15.0) and "near" in side_notes[0]["value"]
    # (a very slow Devour: the far use's +20% outweighs the leap's +0.5 s)
    assert not models.devour_side_near(0.5)
    slow, _ = models.devour_target_model([devour, palm], palm, 0.5)
    assert slow[0]["value"] == pytest.approx(100 * (1 / (1 / 1.2 + 0.5 * 0.5) - 1), abs=0.01)
    assert models.devour_target_model([palm], palm, 1.7) == ([], [])


def test_momentum_from_the_walk_during_one_use():
    bolt = Skill("Frostbolt", 1, attack=False)
    moving = {"UsableWhileMoving", "Projectile", "Spell"}
    # (0.343 casts a second, +25% movement speed, -70% while casting: 3.7 x 1.25 x 0.3 x 2.92 s = 4.05 m)
    mods, notes = models.momentum_model(bolt, moving, ["Momentum"], {"Speed": 0.343, "MovementSpeedMod": 1.25}, -70)
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(40.0)
    assert "4.05 m" in notes[0]["basis"] and "Without it x1" in notes[0]["play"]
    # (a fast use doesn't reach 2 m: nothing)
    fast, fast_notes = models.momentum_model(bolt, moving, ["Momentum"], {"Speed": 2.0, "MovementSpeedMod": 1.0}, -70)
    assert fast == [] and "under" in fast_notes[0]["basis"]
    # (a skill that stops you, standing still, or no Momentum: nothing)
    assert models.momentum_model(bolt, {"Spell"}, ["Momentum"], {"Speed": 0.3}, 0)[0] == []
    assert models.momentum_model(bolt, moving, ["Momentum"], {"Speed": 0.3}, 0, standing=True)[0] == []
    assert models.momentum_model(bolt, moving, [], {"Speed": 0.3}, 0) == ([], [])


def test_momentum_on_a_channelled_charge():
    ram = Skill("Rampage", 1, attack=True)
    mods, notes = models.momentum_model(ram, {"Channel", "Travel", "Attack"}, ["Momentum"],
                                        {"Speed": 5.0, "MovementSpeedMod": 1.0}, 0, channel=3.65)
    share = 1 - 2 / (3.7 * 3.65)
    assert mods[0]["value"] == pytest.approx(40 * share, abs=0.01) and "channel" in notes[0]["basis"]
    # (a channel that doesn't move you: PoB's 0)
    assert models.momentum_model(ram, {"Channel", "UsableWhileMoving"}, ["Momentum"], {"Speed": 5.0}, 0)[0] == []


def test_storm_limit_rows_only_above_one():
    assert models.storm_limit_row("Firestorm", 1, 1.0, 6.0) == []
    rows = models.storm_limit_row("Firestorm", 2, 0.25, 6.0)
    assert rows and "1.50 storms" in rows[0]["value"] and "row" in rows[0]["label"]
    assert "x2 the Orb" in models.storm_limit_row("Galvanic Field", 2, 0.0, 0.0)[0]["value"]
