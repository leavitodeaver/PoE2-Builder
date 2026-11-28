"""MECH117: Blessing of the Moon, "8% increased Skill Effect Duration per Enemy you've Frozen in the last 8 seconds, up
to 40%" (DASH1 / COV62 POE2T6970: PoB counts none), read from the boss's Freeze rate."""
import pytest

from builder.search import models

MOON = "8% increased Skill Effect Duration per Enemy you've Frozen in the last 8 seconds, up to 40%"


def test_no_line_no_mod():
    assert models.frozen_count_duration_mods(["10% increased Skill Effect Duration"], (0.2, "x")) == ([], [])


def test_boss_frozen_part_of_the_window():
    # Frozen once every 10 s: Frozen in the past 8 s 80% of the fight -> 8% x 0.8
    mods, notes = models.frozen_count_duration_mods([MOON], (0.1, "test"))
    assert mods[0]["name"] == "Duration" and mods[0]["type"] == "INC"
    assert mods[0]["value"] == pytest.approx(6.4)
    assert "(the same here)" in notes[0]["basis"] and "without" in notes[0]["play"]


def test_one_enemy_counts_once_with_the_each_freeze_row():
    # Frozen every 2 s: the boss counts once (8%); the each-Freeze reading 4 Freezes = 32%, a row only
    mods, notes = models.frozen_count_duration_mods([MOON], (0.5, "test"))
    assert mods[0]["value"] == pytest.approx(8.0)
    assert "+32.0%" in notes[0]["basis"] and "UNVERIFIED" in notes[0]["basis"]


def test_each_freeze_row_capped():
    _, notes = models.frozen_count_duration_mods([MOON], (2.0, "test"))
    assert "+40.0%" in notes[0]["basis"]


def test_no_freeze_source_is_zero_named_and_unread_is_not_zeroed_silently():
    assert models.frozen_count_duration_mods([MOON], (0.0, "none"))[0] == []
    mods, notes = models.frozen_count_duration_mods([MOON], None)
    assert mods == [] and "not read" in notes[0]["value"]


def test_two_copies_of_one_curse_scale_it_once_by_their_union():
    """Two groups applying Despair: PoB keeps one copy; it is scaled once, to the share the boss carries either
    (before: once per copy, the mods adding: two 50% copies zeroed it)."""
    skill = models.Skill("Despair")
    uses = [models.CurseUse("Despair", 0.5, "hand"), models.CurseUse("Despair", 0.5, "meta")]
    mods, notes = models.curse_model(uses, [skill], None)
    scales = [m for m in mods if "Effect of your Curses" in str(m)]
    assert len(scales) == 1 and scales[0]["scale"] == pytest.approx(-25.0)
    assert all("2 copies" in n["basis"] for n in notes[:2])
    full = models.curse_model([models.CurseUse("Despair", 1.0, "aura"), models.CurseUse("Despair", 0.3, "meta")],
                              [skill], None)[0]
    assert not [m for m in full if "Effect of your Curses" in str(m)]
    one = models.curse_model([models.CurseUse("Despair", 0.4, "meta")], [skill], None)[0]
    assert one[0]["scale"] == pytest.approx(-60.0)


def test_crushing_impacts_makes_every_side_hit_a_crushing_blow():
    """A41-34: with "Your Hits are Crushing Blows" every other hitting group's hits Heavy Stun the Primed boss; a named
    Crushing Blow skill among those rows is counted once."""
    from types import SimpleNamespace

    from builder.search import conditions

    groups = {"groups": [{"enabled": True, "gems": [{"name": "Seismic Cry"}]},
                         {"enabled": True, "gems": [{"name": "Frost Bomb"}]}]}
    fake = SimpleNamespace(build=SimpleNamespace(call=lambda *a, **k: groups), main_skill="Ice Strike",
                           _group_uses=lambda g, names: {"Seismic Cry": 0.25}, _chosen_use=lambda name: 0.0)
    rows = [{"name": "Frost Bomb", "hits": 0.5}, {"name": "Seismic Cry", "hits": 0.25}]
    stats = {"_side_rows": rows}
    rate, how = conditions.Conditions._primed_stun(fake, stats, True)
    assert rate == pytest.approx(0.75) and "Crushing Impacts" in how
    rate, _ = conditions.Conditions._primed_stun(fake, stats, False)
    assert rate == pytest.approx(0.25)


def test_storm_lance_ground_radius_takes_the_builds_area():
    from builder.search import conditions

    assert conditions.shocked_ground_radius("Storm Lance", {"AreaOfEffectMod": 1.44}) == pytest.approx(3.6)
    assert conditions.shocked_ground_radius("Storm Lance", {}) == pytest.approx(3.0)
    assert conditions.shocked_ground_radius("Lightning Warp", {"AreaOfEffectMod": 1.44}) == pytest.approx(3.0)


def test_two_copies_are_labelled_low_for_the_unread_weaker_copy():
    """AUDIT42 A42-9: the weaker copy's strength isn't computed, so the copies' notes are LOW and say so."""
    uses = [models.CurseUse("Despair", 0.5, "hand"), models.CurseUse("Despair", 0.5, "meta")]
    _, notes = models.curse_model(uses, [models.Skill("Despair")], None)
    assert all(n["confidence"] == "LOW" and "weaker copy" in n["play"] for n in notes[:2])
    _, one = models.curse_model([models.CurseUse("Despair", 0.5, "hand")], [models.Skill("Despair")], None)
    assert one[0]["confidence"] == "theory" and "play" not in one[0]
