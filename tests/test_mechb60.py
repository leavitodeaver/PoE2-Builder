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
    assert notes[0]["confidence"] == "MEDIUM"  # (MECHB61, R57-15)


def test_other_lines_do_nothing():
    assert M.companion_runes_model(["Minions have Onslaught"], 1.0, "", 0.0, [("Wolf", 1.0)], 60.0) == ([], [])


def _skill(name, **level):
    return M.Skill.of({"name": name, "group": 1, "levelStats": level, "cooldownRecovery": 1.0})


def test_volcano_spray_rows_keep_the_headline():
    sets = [{"label": "Eruption", "AverageDamage": 100.0}, {"label": "Projectiles", "AverageDamage": 20.0}]
    main = _skill(M.VOLCANO)
    plain = M.volcano_channel_model(main, "Eruption", sets, cast_rate=0.5)
    fed = M.volcano_channel_model(main, "Eruption", sets, cast_rate=0.5, slams=(5.0, "Boneshatter"), lifetime=8.0)
    assert [m["value"] for m in plain[0]] == [m["value"] for m in fed[0]]
    head = float(plain[1][0]["value"][1:])
    basis = fed[1][0]["basis"]
    assert f"every 0.5 s x{head + 2 * 20 / 50:.3f}" in basis
    assert f"1.25 Slams a second on it (at most 10 a 8 s Volcano) x{head + 3.25 * 20 / 50:.3f}" in basis
    assert "no Slam skill" in plain[1][0]["basis"]


def test_hollow_focus_alloy_row():
    focus = _skill(M.HOLLOW_FOCUS, **{M.REPEAT_STAT: 2000})
    alloy = "Tempest Bells are destroyed after an additional 4 Hits"
    mods, note = M.hollow_focus_alloy_note([focus], [alloy], 2.0)
    # (MECHB61, R57-15: the headline) 0.5 bells a second x 5 Hits = 2.5, held to your 2 hits: x4
    assert note[0]["value"] == "x4.000" and note[0]["confidence"] == "MEDIUM"
    assert mods[0]["value"] == pytest.approx(300.0) and mods[0]["skill"] == M.HOLLOW_FOCUS
    assert M.hollow_focus_alloy_note([focus], [alloy], 2.0, {M.HOLLOW_ALLOY_ENTRY}) == ([], [])
    assert M.hollow_focus_alloy_note([focus], [], 2.0) == ([], [])
    assert M.hollow_focus_alloy_note([], [alloy], 2.0) == ([], [])


def _twister(**level):
    return M.Skill.of({"name": "Twister", "group": 1, "levelStats": level})


def test_twister_consume_lower_reading_and_rows():
    main = _twister(**{M.TWISTER_STAGE_STAT: 80})
    mods, notes = M.twister_consume_model(main, 1.0, 2.0, [("Whirling Slash", 3.0, 3)])
    # (MECHB61, R57-14) k = 3: 1 consume a cast, the cast's one hit (its group's) by the empowered twisters: x3.4
    assert notes[0]["value"] == "x3.400"
    assert mods[0]["value"] == pytest.approx(240.0)
    # the extras a new group: one more hit at x3.4, 2 x 3.4 = x6.8
    assert "one more hit a consume (k = 3) x6.800" in notes[0]["basis"]
    # 3 hits a cast near walls: the first at x3.4, the other 2 at (1 + 3) / (2 + 3) of the twisters: 1 + (2.4 + 2 x 2.4 x 0.8) / 3
    mods, notes = M.twister_consume_model(main, 1.0, 2.0, [("Whirling Slash", 3.0, 3)], hits=3.0)
    assert notes[0]["value"] == "x1.800"
    assert "x3.080" in notes[0]["basis"]


def test_twister_with_no_whirlwind_source_is_unchanged():
    main = _twister()
    assert M.twister_consume_model(main, 1.0, 2.0, [])[0] == []
    assert M.twister_consume_model(main, 1.0, 2.0, [("Whirling Slash", 0.0, 3)])[0] == []
    assert M.twister_consume_model(M.Skill.of({"name": "Spark", "group": 1}), 1.0, 2.0, [("Whirling Slash", 3.0, 3)]) == ([], [])
