"""MECH70 (plans/term-dazed-enemies.md; an UNDERCOUNT: nothing set PoB's conditionEnemyDazed box, so "against Dazed
Enemies" lines counted 0): the Dazed box at the share of the fight the main skill's Daze sources keep the boss Dazed
(KeywordPopups Daze: 8 s), published as build.dazed_up for GEM5's Vaulting Impact Broken Stance."""
from __future__ import annotations

import pytest

from builder.search.conditions import DAZE_SECONDS, _dazed

HITS = {"Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0, "_main_types": {"Attack", "Melee"}}


def test_a_chance_line_dazes_at_its_share_of_the_main_skills_hits():
    on, basis, up = _dazed(["5% chance to Daze on Hit"], HITS)
    expected = 1 - 0.95 ** (2.0 * DAZE_SECONDS)
    assert up == pytest.approx(expected) and on is (expected >= 0.25) and "Dazed" in basis
    # Seeing Stars' "25% increased Daze Duration": 10 s per Daze
    longer = _dazed(["5% chance to Daze on Hit", "25% increased Daze Duration"], HITS)[2]
    assert longer == pytest.approx(1 - 0.95 ** (2.0 * DAZE_SECONDS * 1.25))
    assert _dazed(["Dazes on Hit"], HITS)[2] == pytest.approx(1.0)


def test_no_source_and_lines_that_only_read_daze():
    assert _dazed([], HITS) == (0.0, "")
    assert _dazed(["30% increased Damage against Dazed Enemies", "15% increased Magnitude of Daze"], HITS) == (0.0, "")


def test_a_weapons_own_line_needs_an_attack():
    spell = {**HITS, "_main_types": {"Spell"}, "_daze_weapon_lines": {"Dazes on Hit"}}
    assert _dazed(["Dazes on Hit"], spell)[2] == 0.0
    assert _dazed(["Dazes on Hit"], {**spell, "_main_types": {"Attack"}})[2] == pytest.approx(1.0)


def test_headshot_needs_a_projectile_past_six_metres():
    line = "25% chance to inflict Daze with Hits against Enemies further than 6m"
    bow = {**HITS, "_main_types": {"Attack", "Projectile"}}
    assert _dazed([line], {**bow, "_distance_choice": (8.0, "test")})[2] > 0.9
    assert _dazed([line], {**bow, "_distance_choice": (4.0, "test")})[2] == 0.0
    assert _dazed([line], {**HITS, "_distance_choice": (8.0, "test")})[2] == 0.0  # (not a Projectile skill)


def test_the_main_skills_own_chance_and_concussive_spells():
    own = _dazed([], {**HITS, "_daze": {"chance": 100.0}})
    assert own[2] == pytest.approx(1.0) and "own 100% chance" in own[1]
    spell = {**HITS, "_main_types": {"Spell"}, "CritChance": 10.0, "PhysicalCritAverage": 100.0,
             "_daze": {"concussive": True}}
    assert _dazed([], spell)[2] == pytest.approx(min(1.0, 2.0 * 0.1 * DAZE_SECONDS))
    assert _dazed([], {**spell, "PhysicalCritAverage": 0.0})[2] == 0.0  # (no Physical damage on its crits)


def test_a_main_skill_that_cannot_daze_dazes_nothing():
    """Vaulting Impact's sets hold cannot_daze (game files and PoB); its own hits apply no Daze."""
    on, basis = _dazed(["Dazes on Hit"], {**HITS, "_daze": {"cannot": True}})[:2]
    assert on is False and "cannot_daze" in basis



def test_a_dazed_boss_fills_its_stun_bar_faster():
    """KeywordPopups Daze: "a Dazed enemy will take 50% more Stun Buildup" (MECH70, turn 2). PoB applies the
    whole 50% while its Dazed box is on (the probe in test_engine_models.py), so heavy_stun_rate counts it at the share."""
    from builder.search import models

    stats = {"HeavyStunBuildupAvg": 2.0, "Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0, "PhysicalHitAverage": 100.0}
    plain, how = models.heavy_stun_rate(stats)
    dazed, dazed_how = models.heavy_stun_rate({**stats, "_dazed_share": 1.0})
    half = models.heavy_stun_rate({**stats, "_dazed_share": 0.5})[0]
    assert plain < half < dazed and "Dazed" not in how and "x1.50 for the boss Dazed 100%" in dazed_how
    assert models.heavy_stun_rate({**stats, "_dazed_share": 0.0}) == (plain, how)


def test_dazed_share_reads_the_published_share():
    from types import SimpleNamespace

    from builder.search import models

    assert models.dazed_share(SimpleNamespace()) == 0.0
    assert models.dazed_share(SimpleNamespace(dazed_up=(0.4, "basis"))) == 0.4


def test_other_skills_hits_daze_the_boss_at_their_use_rates():
    """MECH71: a global Daze line applies to every skill's hits, each other skill at its use rate (one hit per use)."""
    other = {"name": "Leap Slam", "types": ["Attack", "Melee", "Slam"], "uses": 0.5, "chance": 0.0, "cannot": False}
    both = _dazed(["10% chance to Daze on Hit"], {**HITS, "_daze_others": [other]})[2]
    main_only = 1 - 0.9 ** (2.0 * DAZE_SECONDS)
    assert both == pytest.approx(1 - (1 - main_only) * 0.9 ** (0.5 * DAZE_SECONDS))
    # its own chance (Wind Blast's 100) Dazes even with no line naming Daze
    wind = {**other, "name": "Wind Blast", "chance": 100.0}
    on, basis, up = _dazed([], {**HITS, "_daze_others": [wind]})
    assert up == pytest.approx(1.0) and "Wind Blast" in basis
    # a skill with cannot_daze, or one never used, adds nothing
    assert _dazed([], {**HITS, "_daze_others": [{**wind, "cannot": True}]}) == (0.0, "")
    assert _dazed([], {**HITS, "_daze_others": [{**wind, "uses": 0.0}]}) == (0.0, "")
    # a weapon's own line Dazes only with another skill that is an Attack
    spell = {**other, "name": "Spark", "types": ["Spell", "Damage", "Projectile"]}
    weapon = {**HITS, "_main_types": {"Spell"}, "_daze_weapon_lines": {"Dazes on Hit"}}
    assert _dazed(["Dazes on Hit"], {**weapon, "_daze_others": [spell]})[2] == 0.0
    assert _dazed(["Dazes on Hit"], {**weapon, "_daze_others": [other]})[2] == pytest.approx(1.0)


def test_a_main_skill_that_cannot_daze_still_finds_the_boss_dazed_by_other_skills():
    """Vaulting Impact (cannot_daze): GEM5's Broken Stance reads build.dazed_up, which other skills' Daze now feeds."""
    other = {"name": "Leap Slam", "types": ["Attack"], "uses": 0.25, "chance": 0.0, "cannot": False}
    on, basis, up = _dazed(["20% chance to Daze on Hit"], {**HITS, "_daze": {"cannot": True}, "_daze_others": [other]})
    assert up == pytest.approx(1 - 0.8 ** (0.25 * DAZE_SECONDS)) and "cannot_daze" in basis and "Leap Slam" in basis


def test_which_skills_hit_for_daze():
    from builder.search.conditions import daze_hitter

    assert daze_hitter({"skillTypes": ["Attack", "Melee"]})
    assert daze_hitter({"skillTypes": ["Spell", "Damage", "Projectile"]})
    assert not daze_hitter({"skillTypes": ["Spell", "DamageOverTime", "DegenOnlySpellDamage"]})  # Contagion
    assert not daze_hitter({"skillTypes": ["Spell", "Damage", "Wall", "CausesBurning"]})  # Flame Wall
    assert not daze_hitter({"skillTypes": ["CreatesMinion", "Minion"]})
    assert not daze_hitter({"skillTypes": ["Buff", "Persistent"]}) and not daze_hitter(None)
    assert not daze_hitter({"skillTypes": ["Attack"], "support": True})
    # (MECH73, A35-2) the bridge's hitsOnUse on the gem's effects decides it where present: Iron Ward's store doesn't hit,
    # its nova does; a damaging Warcry hits; Fortifying Cry doesn't
    ward = {"skillTypes": ["Buff", "Persistent"]}
    assert daze_hitter(ward, [{"hitsOnUse": False}, {"hitsOnUse": True}])
    assert daze_hitter({"skillTypes": ["Warcry", "Damage"]}, [{"hitsOnUse": True}])
    assert not daze_hitter({"skillTypes": ["Warcry", "Damage"]}, [{"hitsOnUse": False}])
    assert not daze_hitter({"skillTypes": ["Attack"], "support": True}, [{"hitsOnUse": True}])


def test_broken_stance_labels_the_dazed_share_it_blocks():
    """MECH73 (AUDIT35 A35-3): "Enemies with Broken Stance cannot be Dazed again" (KeywordPopups BrokenStance). With 10
    gained hits at 0.5/s the window is 20 s, up 20 / 22 of the fight; a Daze runs 8 s into it, so the boss is un-Dazed
    12 s of each window: the share is overstated by 20/22 x 12/20 = 54.5% (labelled, not taken off)."""
    from builder.search import models

    _, notes = models.broken_stance_model(10_000.0, 1.0, 0.5, 0.5, 1.0)
    assert "overstated by 54.5%" in notes[0]["basis"]
    _, fast = models.broken_stance_model(10_000.0, 1.0, 0.5, 5.0, 1.0)  # (a 2 s window: the Daze outlasts it)
    assert "overstated by 0.0%" in fast[0]["basis"]
