"""MECHB60: the two companion Idol lines (models.companion_runes_model)."""
import pytest

from builder.search import models as M

ONSLAUGHT = "Companions gain Onslaught for 4 seconds on Hitting your Marked targets"
RAGE = "Companions in your Presence gain 1 Rage on hit"
SPEED = "Companions have 1% increased Skill Speed"
MORE = "Minions deal 1% more Attack Damage"


def test_onslaught_follows_the_marked_share():
    mods, notes = M.companion_runes_model([ONSLAUGHT], 0.5, "test", 0.0, [("Wolf", 1.0)], 100.0)
    assert [(m["line"], m["scale"]) for m in mods] == [(SPEED, pytest.approx(10.0))]
    assert notes[0]["value"] == 0.5


def test_no_mark_or_no_hitting_companion_gives_no_onslaught():
    assert M.companion_runes_model([ONSLAUGHT], 0.0, "no Mark", 0.0, [("Wolf", 1.0)], 100.0)[0] == []
    assert M.companion_runes_model([ONSLAUGHT], 1.0, "test", 0.0, [("Wolf", 0.0)], 100.0)[0] == []
    assert M.companion_runes_model([ONSLAUGHT], 1.0, "test", 0.0, [], 100.0)[0] == []


def test_onslaught_does_not_stack_with_onslaught_already_counted():
    mods, _ = M.companion_runes_model([ONSLAUGHT], 0.5, "test", 0.5, [("Wolf", 1.0)], 100.0)
    assert mods[0]["scale"] == pytest.approx(5.0)  # (a union: 75% of the fight in all, 50% counted already)
    assert M.companion_runes_model([ONSLAUGHT], 1.0, "test", 1.0, [("Wolf", 1.0)], 100.0)[0] == []


def test_rage_climbs_at_one_hit_per_half_second_at_most():
    held, _ = M.companion_rage(1.0, 10.0, 60.0)  # (2 Rage a second counted: full after 15 s)
    assert held == pytest.approx(30 * (1 - 15 / 120))
    assert M.companion_rage(1.0, 2.0, 60.0)[0] == pytest.approx(held)


def test_rage_in_a_short_fight_never_reaches_the_maximum():
    assert M.companion_rage(1.0, 2.0, 10.0)[0] == pytest.approx(2 * 10 / 2)


def test_rage_from_hits_too_rare_to_keep_it():
    held, _ = M.companion_rage(1.0, 0.1, 60.0)  # (one hit every 10 s: held 4 s, then gone in 0.2 s)
    assert held == pytest.approx((4 + 0.1) / 10)
    assert M.companion_rage(1.0, 0.0, 60.0)[0] == 0.0


def test_rage_goes_on_each_companion_by_its_own_rate():
    mods, notes = M.companion_runes_model([RAGE], 0.0, "", 0.0, [("Wolf", 2.0), ("Bear", 0.0)], 60.0)
    assert [(m["line"], m.get("skill")) for m in mods] == [(MORE, "Wolf")]
    assert mods[0]["scale"] == pytest.approx(30 * (1 - 15 / 120))
    assert notes[0]["confidence"] == "LOW"


def test_other_lines_do_nothing():
    assert M.companion_runes_model(["Minions have Onslaught"], 1.0, "", 0.0, [("Wolf", 1.0)], 60.0) == ([], [])
