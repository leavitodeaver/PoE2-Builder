"""MECH98 (MECH-A), engine-free tests."""
from __future__ import annotations

from builder.search import models
from builder.search.conditions import _exposure_uptime

PARADISE = ["Inflict Elemental Exposure to Enemies 3 metres in front of you",
            "for 4 seconds, every 0.25 seconds while raised"]  # (PoB's unique text, Uniques/shield.lua 135-136)
BASTION = "Blind Enemies 3 metres in front of you every 0.25 seconds while your Shield is raised"
SHIELD = models.Skill(models.RAISE_SHIELD, attack=True)
STRIKE = models.Skill("Strike", attack=True)


def _stats(plan, metres=1.0):
    return {"_raised_plan": plan, "_distance_choice": (metres, ""), "_main_types": {"Attack"}, "_main_skill": "Strike"}


def test_window_to_paradise_exposure_needs_the_taps():
    """PLANS6's OVERCOUNT: the boss got Exposure all fight within 3 m with the shield never raised. Now one tap per 4 s
    Exposure, each costing the main skill RAISE_TAP s and counted as a button press."""
    plan = models.raised_plan([SHIELD, STRIKE], PARADISE, 1.0)
    assert plan.exposure == 1.0 and plan.blind == 0.0 and plan.raised_exposure
    assert abs(plan.lost - 0.55 / 4) < 1e-9 and plan.taps == 0.25 and "Exposure" in plan.how
    on, how, *up = _exposure_uptime("Fire")(PARADISE, _stats(plan))
    assert on is True and up == [1.0] and "tap Raise Shield" in how
    # taps off (the search's switch, e.g. at the button cap): no Exposure and no time lost
    off = models.raised_plan([SHIELD, STRIKE], PARADISE, 1.0, taps=False)
    assert off.exposure == 0 and off.lost == 0 and off.taps == 0
    on, how, *up = _exposure_uptime("Fire")(PARADISE, _stats(off))
    assert not on and up == [0.0] and "doesn't tap" in how
    # the plan not read: 0, labelled (before: all fight)
    on, how, *_ = _exposure_uptime("Cold")(PARADISE, {"_distance_choice": (1.0, ""), "_main_types": {"Attack"}})
    assert not on and "weren't read" in how


def test_window_to_paradise_out_of_reach_or_without_a_shield():
    far = models.raised_plan([SHIELD], PARADISE, 5.0)
    assert far.exposure == 0 and far.taps == 0 and "3 m" in far.how
    none = models.raised_plan([STRIKE], PARADISE, 1.0)
    assert none.exposure == 0 and "no Raise Shield" in none.how
    # the first half alone (no "while raised" half) isn't a raised line
    assert not models.raised_lines(PARADISE[:1]) and models.raised_lines(PARADISE)


def test_one_tap_renews_blind_and_exposure():
    both = models.raised_plan([SHIELD], [BASTION, *PARADISE, "100% increased Blind Duration"], 1.0)
    # Blind lasts 8 s, Exposure 4 s: the shorter sets the period, one tap renews both
    assert both.blind == 1.0 and both.exposure == 1.0 and both.taps == 0.25 and abs(both.lost - 0.55 / 4) < 1e-9


def test_a_held_shield_needs_no_taps():
    held = models.raised_plan([SHIELD], PARADISE, 1.0, held="Resonating Shield")
    assert held.exposure == 1.0 and held.taps == 0 and held.lost == 0
    mods, notes = models.raised_model([SHIELD, STRIKE], PARADISE, 1.0, STRIKE)
    assert len(mods) == 1 and abs(mods[0]["scale"] + 100 * 0.55 / 4) < 0.01
    assert notes[0]["label"].startswith("Exposure while your Shield is raised")


SPARK = models.Skill("Spark")
FROST_MAGES = models.Skill("Summon Skeletal Frost Mage", 2, summon={"persistent": True, "limit": 2})
KELARI = models.Skill(models.DJINNS["Kelari"], 3)


def test_varashtas_blessing_counts_the_builds_commands_at_15_seconds():
    """R41-10: 10% more a different Command used in the last 15 s (the displayed text); the 8 s stat id a row. Commands
    the build doesn't press for their own damage are pressed once per max(cooldown, 15 s), each costing its cast."""
    gems = {"Spark", models.VARASHTA}
    # no Command at all: a note, no mods
    mods, notes, presses = models.varashta_blessing_model(gems, SPARK, [SPARK], [], {})
    assert mods == [] and presses == {} and "adds nothing" in notes[0]["play"]
    # not supported: nothing
    assert models.varashta_blessing_model({"Spark"}, SPARK, [SPARK, FROST_MAGES], [], {}) == ([], [], {})
    # the Frost Mages' Ice Bomb (8 s, 1.25 s cast) and Ice Armour (12 s, 1 s): each pressed every 15 s
    mods, notes, presses = models.varashta_blessing_model(gems, SPARK, [SPARK, FROST_MAGES], [], {})
    assert abs(mods[0]["scale"] - 20) < 1e-6 and abs(mods[1]["scale"] + 100 * (1.25 + 1.0) / 15) < 0.01
    assert set(presses) == {"Ice Bomb", "Ice Armour"} and abs(presses["Ice Bomb"][0] - 1 / 15) < 1e-9
    assert "8 s reading" in notes[0]["basis"]


def test_varashtas_blessing_reads_the_djinn_commands_already_pressed():
    gems = {"Spark", models.VARASHTA}
    # Kelari's Brutality pressed every 5 s for its own damage: counted at no cost; Deception / Judgment not granted
    mods, notes, presses = models.varashta_blessing_model(gems, SPARK, [SPARK, KELARI], [], {"Kelari's Brutality": 0.2})
    assert len(mods) == 1 and abs(mods[0]["scale"] - 10) < 1e-6 and presses == {}
    # granted Deception, not pressed for its own damage: pressed every 15 s, its 0.4 s cast charged
    texts = ["Grants Skill: Kelari's Deception"]
    mods, notes, presses = models.varashta_blessing_model(gems, SPARK, [SPARK, KELARI], texts, {"Kelari's Brutality": 0.2})
    assert abs(mods[0]["scale"] - 20) < 1e-6 and set(presses) == {"Kelari's Deception"}
    assert abs(mods[1]["scale"] + 100 * 0.4 / 15) < 0.01


HINDERING = "Debuffs inflicted by Hazards have 30% increased Slow Magnitude"


def test_hazard_slow_magnitude_on_a_hazards_chill_only():
    """Hindering Obstacles: +30% Slow Magnitude on the Chill of a Hazard main skill (Ice Shards, Permafrost Bolts); the
    Hazards' Maim slows movement only (no boss hit moves); not added beside Temporal Chains (the lower reading)."""
    mods, notes = models.hazard_slow_mods([HINDERING], "Ice Shards", {"Ice Shards"})
    assert mods == [models.raw_mod(models.SLOW_MAGNITUDE_MOD, "INC", 30.0)] and notes[0]["value"] == "+30%"
    mods, notes = models.hazard_slow_mods([HINDERING], "Caltrops", {"Caltrops"})
    assert mods == [] and "Movement Speed" in notes[0]["basis"]
    mods, notes = models.hazard_slow_mods([HINDERING], "Ice Shards", {"Ice Shards", "Temporal Chains"})
    assert mods == [] and "Temporal Chains" in notes[0]["basis"]
    assert models.hazard_slow_mods([], "Ice Shards", set()) == ([], [])
