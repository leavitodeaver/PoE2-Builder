"""MECHB53: Raise Zombie's zombies from its Power Charge / corpse supply (R44-7)."""
from __future__ import annotations

import pytest

from builder.search import models


def test_raise_zombie_counts_three_a_power_charge_one_a_corpse():
    """R44-7: a raise paid by a Power Charge makes 3 zombies (the Empowered one + 2), by a corpse 1; alive = min(limit,
    made a second x the duration); a Power Charge spent first (headline), a corpse first a row."""
    count, how, confidence = models.zombie_alive_count(0.1, 0.05, 0.0, 10, 20)
    assert count == pytest.approx(3.0) and confidence == "MEDIUM"  # (0.05 x 3 x 20)
    count, how, confidence = models.zombie_alive_count(0.1, 0.05, 1.0, 10, 20)
    assert count == pytest.approx(4.0) and confidence == "LOW"  # (0.05 x 3 + 0.05) x 20
    assert "row, a corpse first: 2.00" in how  # (0.1 by corpses x 20)
    assert models.zombie_alive_count(1.0, 1.0, 0.0, 10, 20)[0] == pytest.approx(10.0)  # (the limit)
    count, how, _ = models.zombie_alive_count(0.1, 0.05, 0.0, 10, 20, 253)  # (the Empowered one's hits x3.53)
    assert count == pytest.approx(3.0 * (3 + 2.53) / 3) and "x3.53" in how


def test_raise_zombie_with_nothing_to_pay_a_raise_makes_none():
    """No Power Charge reaching it and no corpse: 0 zombies (CLAUDE.md: no source means 0), said."""
    count, how, _ = models.zombie_alive_count(0.05, 0.0, 0.0, 10, 20)
    assert count == 0 and "no flat rate" in how
    count, how, _ = models.zombie_alive_count(0.0, 1.0, 1.0, 10, 20)
    assert count == 0 and "aren't rated" in how


def test_while_channelling_lines_count_at_the_share_you_channel():
    """PoB counts "while Channelling" lines all fight for a channelled main skill, never beside a side channel: each is
    added again at (share - 1) / share of its value (the manager 12:05; MECH-C's channelling_share)."""
    texts = ["30% of Damage taken Recouped as Life while Channelling", "8% increased Armour, Evasion and Energy Shield "
             "while Channelling", "Unaffected by Chill while Channelling", "10% increased Damage"]
    mods, notes = models.channelling_lines_model(texts, 0.75, True, "a")
    by = {(m["name"], m["type"]): m["value"] for m in mods}
    assert by[("LifeRecoup", "BASE")] == pytest.approx(-7.5) and by[("Evasion", "INC")] == pytest.approx(-2.0)
    assert ("SelfChillEffect", "MORE") not in by and "can't be scaled back" in notes[0]["basis"]
    mods, notes = models.channelling_lines_model(texts, 0.25, False, "b")
    by = {(m["name"], m["type"]): m["value"] for m in mods}
    assert by[("LifeRecoup", "BASE")] == pytest.approx(7.5) and by[("SelfChillEffect", "MORE")] == pytest.approx(-25.0)
    assert models.channelling_lines_model(["10% increased Damage"], 0.5, True, "c") == ([], [])


def test_fist_of_kalguurs_boosted_slams_are_empowered():
    """Runically Boosted Slams are Empowered: the Empowered-only lines on the boosted share above what PoB / the other
    Empowering skills count (independent shares)."""
    texts = ["Empowered Attacks deal 30% increased Damage", "Empowered Attacks Gain 15% of Physical Damage as Extra Fire damage"]
    mods, notes = models.kalguur_empowered_model(texts, "Earthshatter", 0.5, 0.2, 0.0)
    assert mods[0]["line"] == "1% increased Attack Damage" and mods[0]["scale"] == pytest.approx(30 * 0.5 * 0.8)
    assert mods[1]["scale"] == pytest.approx(7.5) and "Fire" in mods[1]["line"]
    assert models.kalguur_empowered_model(["10% increased Damage"], "Earthshatter", 0.5, 0.0, 0.0) == ([], [])
    assert models.kalguur_empowered_model(texts, "Earthshatter", 0.0, 0.0, 0.0) == ([], [])


def test_charged_staffs_shockwave_comes_on_each_quarterstaff_attack_while_buffed():
    """PoB never triggers Charged Shockwave: one a Quarterstaff Attack use while the buff is up, min(1, stacks)."""
    ft = models.Skill("Falling Thunder", 1, attack=True)
    mods, notes = models.charged_shockwave_model(ft, True, 2.0, 0.5, "a")
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(1.0)
    mods, _ = models.charged_shockwave_model(ft, True, 2.0, 3.0, "a")
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(2.0)
    mods, notes = models.charged_shockwave_model(ft, True, 2.0, 3.0, "a", hits=6.0)  # (R44-16: one a hit)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(6.0) and "one a use: 2" in notes[0]["basis"]
    mods, notes = models.charged_shockwave_model(ft, False, 2.0, 3.0, "a")
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == 0 and "isn't a Quarterstaff" in notes[0]["basis"]


def test_fist_of_kalguurs_rune_erupts_once_a_rune_life_at_most():
    """A rune a boosted hit, at most `limit` at a time each erupting after 4 s, its 0.5 s cooldown; row: replaced."""
    slam = models.Skill("Earthshatter", 1, attack=True)
    import math
    mods, notes = models.kalguur_rune_model(slam, 0.5, 2.0, 4.0, 1, 0.5)  # (R44-15: the oldest replaced)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(1.0 * math.exp(-4.0), rel=1e-3)
    assert "no new rune while the limit is full, 0.250" in notes[0]["basis"]
    mods, _ = models.kalguur_rune_model(slam, 0.1, 1.0, 4.0, 1, 0.5)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(0.1 * math.exp(-0.4), rel=1e-3)
    mods, _ = models.kalguur_rune_model(slam, 0.1, 1.0, 4.0, 2, 0.5)  # (two runes: fewer than 2 newer within 4 s)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(0.1 * math.exp(-0.4) * 1.4, rel=1e-3)
    assert "replaced" in notes[0]["basis"] and notes[0]["confidence"] == "LOW"
    assert models.kalguur_rune_model(slam, 0.0, 1.0, 4.0, 1, 0.5) == ([], [])


def test_double_vision_blasts_at_the_reloads_it_makes():
    """Reloads = FiringRate x (1 - keep) / bolts; alternately N Cold / N Fire (cap 10); one Surge a use; Fork: none."""
    from builder.search import conditions
    texts = ["When you reload, triggers Gemini Surge to alternately", "gain 6 Cold Surges or 6 Fire Surges"]
    gemini = models.Skill("Gemini Surge", 3)
    main = models.Skill("Galvanic Shards", 1, attack=True)
    stats = {"FiringRate": 2.0, "BoltCount": 4, "ChanceToNotConsumeAmmo": 0, "Speed": 2.0}
    mods, notes, groups = models.double_vision_model(texts, stats, [gemini, main], main, {"Projectile", "Attack"},
                                                     conditions.surge_rate)
    rate = {m["key"]: m["value"] for m in mods}["triggerRate"]
    assert rate == pytest.approx(min(2.0, 0.25 * 12)) and groups == {3}  # (0.5 reloads/s: 0.25 x (6 + 6) = 3/s)
    # (MECHB54, probe POE2T4826: a crossbow skill's gem types are CrossbowAmmoSkill, not Projectile; Melee: none)
    crossbow = {"Attack", "CrossbowAmmoSkill"}
    mods, _, _ = models.double_vision_model(texts, stats, [gemini, main], main, crossbow, conditions.surge_rate)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == pytest.approx(2.0)
    melee = models.Skill("Galvanic Shards", 1, attack=True, melee=True)
    mods, _, _ = models.double_vision_model(texts, stats, [gemini, melee], melee, crossbow, conditions.surge_rate)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == 0
    stats["ForkCountMax"] = 1
    mods, notes, groups = models.double_vision_model(texts, stats, [gemini, main], main, {"Projectile", "Attack"},
                                                     conditions.surge_rate)
    assert {m["key"]: m["value"] for m in mods}["triggerRate"] == 0 and "Fork" in notes[0]["basis"] and not groups
    assert models.double_vision_model(["10% increased Damage"], stats, [gemini, main], main, set(),
                                      conditions.surge_rate) == ([], [], set())
