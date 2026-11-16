"""MECH75: the boss's Ignited share counts the build's other groups' Ignites (conditions.ignite_others), not the main
skill's alone (MECH74's follow-up, an UNDERCOUNT: 0 where the main skill doesn't Ignite)."""
from __future__ import annotations

import math

import pytest

from builder.search import conditions as c
from builder.search import models

MAIN = {"Speed": 2.0, "HitChance": 100.0, "IgniteChancePerHit": 25.0, "IgniteDuration": 4.0}
NONE = {"Speed": 2.0, "HitChance": 100.0, "IgniteChancePerHit": 0.0}


def test_another_groups_ignite_counts_where_the_main_skill_has_none():
    # (MECH81: a hit's Ignite chance is all the live Flammability: 2% + 8 s x 1/s x 2% = 18% a hit)
    other = ("Fireball", 1.0, 0.02, 4.0)
    assert c.ailment_uptime(NONE, "Ignite") == 0.0
    share = c.ailment_uptime({**NONE, "_ailment_others": {"Ignite": [other]}}, "Ignite")
    assert share == pytest.approx(1 - 0.82 ** 4.0)
    # an "other" that never lands adds nothing
    assert c.ailment_uptime({**NONE, "_ailment_others": {"Ignite": [("x", 0.0, 0.5, 4.0)]}}, "Ignite") == 0.0


def test_the_sources_combine_as_independent():
    # (MECH81) both sources' Flammability stacks into each hit's chance: live 8 s x (2/s x 1% + 1/s x 2%) = 32%
    main = {**MAIN, "IgniteChancePerHit": 1.0}
    other = ("Fireball", 1.0, 0.02, 4.0)
    own = 1 - 0.83 ** (2.0 * 4.0)  # alone: 1% + 8 x 2 x 1% = 17% a hit
    both = c.ailment_uptime({**main, "_ailment_others": {"Ignite": [other]}}, "Ignite")
    assert c.ailment_uptime(main, "Ignite") == pytest.approx(own)
    assert both == pytest.approx(1 - 0.67 ** 8.0 * 0.66 ** 4.0)
    # other ailments read only their own sources
    assert c.ailment_uptime({**NONE, "_ailment_others": {"Ignite": [other]}}, "Poison") == 0.0


def test_a_consumer_waits_for_the_next_application_from_any_source():
    stats = {**MAIN, "_consumes": {"Ignite": "x"}}
    last = 1 / 2.0
    alone = last / (last + models.CONSUME_IMMUNE + 1 / (2.0 * 0.25))
    assert c.ailment_uptime(stats, "Ignite") == pytest.approx(alone)
    with_other = c.ailment_uptime({**stats, "_ailment_others": {"Ignite": [("F", 1.0, 0.5, 4.0)]}}, "Ignite")
    assert with_other == pytest.approx(last / (last + models.CONSUME_IMMUNE + 1 / (0.5 + 0.5)))
    # the main skill Consumes but doesn't Ignite: it lives on the other group's Ignites
    none = {**NONE, "_consumes": {"Ignite": "x"}, "_ailment_others": {"Ignite": [("F", 1.0, 0.5, 4.0)]}}
    assert c.ailment_uptime(none, "Ignite") == pytest.approx(last / (last + models.CONSUME_IMMUNE + 1 / 0.5))
    assert c.consumed_share(2.0, 0.25) == pytest.approx(alone)  # (unchanged without extra)


def test_a_side_consumer_cuts_the_combined_share():
    stats = {**NONE, "_side_consumes": {"Ignite": (1.0, "Snap", "MEDIUM")},
             "_ailment_others": {"Ignite": [("F", 1.0, 0.5, 4.0)]}}
    share = 1 - 0.5 ** 4.0
    assert c.ailment_uptime(stats, "Ignite") == pytest.approx(share / (1 + share * (models.CONSUME_IMMUNE + 2.0)))


def test_ignite_others_reads_each_other_groups_row_at_its_own_rate():
    groups = [{"index": 1, "gems": [{"name": "Boneshatter"}, {"name": "Fireball"}]},
              {"index": 2, "gems": [{"name": "Fireball"}]},
              {"index": 3, "gems": [{"name": "Flame Wall"}]},
              {"index": 4, "gems": [{"name": "Cast Fire"}]},
              {"index": 5, "gems": [{"name": "Ice Shot"}]},
              {"index": 6, "gems": [{"name": "Press Me"}]}, {"index": 7, "enabled": False, "gems": [{"name": "Off"}]}]
    row = {"HitChance": 80.0, "IgniteDPS": 100.0, "IgniteChancePerHit": 50.0, "IgniteDuration": 3.0}
    rows = {1: {**row, "name": "Boneshatter", "cooldown": 1.0},  # (the main group: left out)
            2: {**row, "name": "Fireball", "cooldown": 2.0},
            3: {**row, "name": "Flame Wall", "cooldown": 1.0},  # (not a hitter: left out)
            4: {**row, "name": "Cast Fire", "triggered": True, "speed": 1.5},
            5: {**row, "name": "Ice Shot", "cooldown": 1.0, "IgniteDPS": 0.0},  # (no Ignite)
            6: {**row, "name": "Press Me"},  # (no cooldown, duration or chosen rate: 0)
            7: {**row, "name": "Off", "cooldown": 1.0}}
    hitters = {"Fireball", "Cast Fire", "Ice Shot", "Press Me", "Off"}
    found, how = c.ignite_others(rows, groups, hitters, 1)
    assert found == [("Fireball", pytest.approx(0.4), 0.5, 3.0), ("Cast Fire", pytest.approx(1.2), 0.5, 3.0)]
    assert "Fireball's Ignites" in how and "PoB's trigger rate" in how
    found, _ = c.ignite_others(rows, groups, hitters, 1, {"Press Me": 2.0})
    assert ("Press Me", pytest.approx(1.6), 0.5, 3.0) in found
    assert c.ignite_others({}, groups, hitters, 1) == ([], "")


HEX = "Non-Cursed enemies you Curse have 5 Withered stacks inflicted on them"


def test_withering_hex_counts_only_curses_that_find_the_boss_uncursed():
    """MECH75: Withering Hex's 5 stacks at each curse application that finds the boss carrying none of your curses."""
    hand = models.CurseUse("Despair", 1.0, "cast by hand", True, 8.0)
    trig = models.CurseUse("Enfeeble", 0.5, "triggered", False, 4.0)  # (0.125 triggers/s)
    aura = models.CurseUse("Despair", 1.0, "an Aura (Blasphemy)")
    assert c.withering_hex_rate([])[0] == 0.0
    assert c.withering_hex_rate([hand])[0] == 0.0 and "recast before it runs out" in c.withering_hex_rate([hand])[1]
    assert c.withering_hex_rate([aura])[0] == 0.0
    assert c.withering_hex_rate([trig])[0] == pytest.approx(0.125)
    # beside another curse up half the fight, half its triggers find the boss uncursed
    other = models.CurseUse("Flammability", 0.5, "triggered", False, 4.0)
    assert c.withering_hex_rate([trig, other])[0] == pytest.approx(2 * 0.125 * 0.5)
    base = {"Speed": 1.0, "HitChance": 100.0}
    stacks = c._withered_stacks([HEX], {**base, "_curse_uses": [trig]})
    assert stacks[0] == float(int(5 * 0.125 * c.WITHER_DEFAULT_DURATION)) == 1.0  # (PoB reads whole stacks)
    assert "Non-Cursed enemies" in stacks[1]
    assert c._withered_stacks([HEX], {**base, "_curse_uses": [hand]})[0] == 0.0
    # PoB doesn't parse the line: its stacks need CanWither
    assert c.wither_source_flags([HEX], set())


def test_a_hand_cast_curse_let_run_out_feeds_withering_hex():
    """MECH75 (AUDIT36 A36-2): the search may play a hand-cast curse to run out before each recast (models.CURSE_LAPSE):
    up D / (D + gap) of the fight, and every cast finds the boss uncursed, 1 / (D + gap) a second."""
    groups = [{"index": 1, "gems": [{"name": "Despair"}]}]
    gems = {"Despair": {"skillTypes": ["AppliesCurse", "Spell"]}}
    kept = models.curse_uses(groups, gems, {}, 2.0)
    assert kept[0].share == 1.0 and kept[0].by_hand and not kept[0].lapse
    assert c.withering_hex_rate(kept)[0] == 0.0
    lapsed = models.curse_uses(groups, gems, {"_curse_lapse": {"Despair"}}, 2.0)
    d, gap = models.CURSE_DURATION, models.CURSE_LAPSE_GAP
    assert lapsed[0].lapse and lapsed[0].share == pytest.approx(d / (d + gap))
    each, how = c.withering_hex_rate(lapsed)
    assert each == pytest.approx(1 / (d + gap)) and "let run out" in how
    stacks = c._withered_stacks([HEX], {"Speed": 1.0, "HitChance": 100.0, "_curse_uses": lapsed})
    assert stacks[0] == float(int(5 / (d + gap) * c.WITHER_DEFAULT_DURATION))


GRIEF = "Enemies in your Presence are Ignited as though dealt 200 Base Fire Damage"


def test_radiant_griefs_presence_ignite_keeps_the_boss_ignited_inside_your_presence():
    """MECH75 (PATCH_CHECK H2-2): the boss Ignited all the fight inside your Presence, 0 outside; it combines with the
    other sources, and a consumer finds it reapplied after the 1 s immunity, as a ground."""
    near = {"PresenceRadius": 40, "_distance_choice": (2.0, "chosen")}
    far = {"PresenceRadius": 40, "_distance_choice": (6.0, "chosen")}
    assert c.presence_ignite([GRIEF], near)[0] == 1.0 and "inside" in c.presence_ignite([GRIEF], near)[1]
    assert c.presence_ignite([GRIEF], far)[0] == 0.0 and "outside" in c.presence_ignite([GRIEF], far)[1]
    assert c.presence_ignite([], near) == (0.0, "")
    aura = {"Ignite": (1.0, "aura")}
    assert c.ailment_uptime({**NONE, "_ailment_ground": aura}, "Ignite") == pytest.approx(1.0)
    half = {"Ignite": (0.5, "aura")}
    own = c.ailment_uptime(MAIN, "Ignite")
    assert c.ailment_uptime({**MAIN, "_ailment_ground": half}, "Ignite") == pytest.approx(1 - (1 - own) * 0.5)
    consumed = {**MAIN, "_consumes": {"Ignite": "x"}, "_ailment_ground": aura}
    last = 1 / 2.0
    assert c.ailment_uptime(consumed, "Ignite") == pytest.approx(last / (last + models.CONSUME_IMMUNE))


def test_bleed_consumers_are_read_by_their_text_and_stats():
    """MECH75: Blood Hunt (by type) and the Haemocrystals support (consume_bleed_to_create_X_haemocrystals) consume
    Bleeding; the Heralds that explode on a kill name no Consume."""
    hunt = ("Lunge at a target and skewers them. Hitting an enemy that has suffered Blood Loss will cause a blood explosion "
            "and also Consume Bleeding. This skill cannot be Ancestrally Boosted.")
    haemo = ("Supports Attack Skills you use yourself which can cause Damaging Hits. Supported Skills Consume Bleeding on "
             "Hit to create explosive blood-filled crystals, but cannot themselves inflict Bleeding.")
    herald = "While active, killing an enemy with Blood Loss will cause a bloody explosion that deals Physical Attack damage"
    assert c.CONSUME_TEXT.search(hunt) and c.CONSUME_TEXT.search(haemo) and not c.CONSUME_TEXT.search(herald)
    assert "consume_bleed_to_create_X_haemocrystals" in c.CONSUME_STATS["Bleed"]
    assert c.SIDE_CONSUMED_BOXES["conditionEnemyBleeding"] == "Bleed"
    bleed = {"Speed": 2.0, "HitChance": 100.0, "BleedChancePerHit": 50.0, "BleedDuration": 5.0}
    last = 1 / 2.0
    assert c.ailment_uptime({**bleed, "_consumes": {"Bleed": "Blood Hunt"}}, "Bleed") == pytest.approx(
        last / (last + models.CONSUME_IMMUNE + 1 / (2.0 * 0.5)))
    scale, how = c.consumed_ignite_scale(1.0, 2.0, 0.5, 5.0, True)
    assert scale == pytest.approx(last / (last + models.CONSUME_IMMUNE + 1.0))


def test_blood_hunt_model_takes_pct_of_the_blood_loss():
    """MECH75: pct x (another skill's Bleed x the consumed share + the Blood Loss lines' share of Physical hits)."""
    dps, how = models.blood_hunt_model(25.0, 1000.0, 0.5, 2000.0, 0.1)
    assert dps == pytest.approx(0.25 * (500 + 200)) and "Life Loss" in how
    assert models.blood_hunt_model(25.0, 0.0, 1.0, 2000.0, 0.0)[0] == 0.0
    assert models.HIT_BLOOD_LOSS.match("10% of Physical damage dealt by your Hits causes Blood Loss")


def test_overcharge_reflects_the_supported_skills_shocks_onto_you():
    """MECH75 (PATCH_CHECK L4-1): each Shock an Overcharge-supported skill inflicts Shocks you for 4 s, less your Shock
    avoidance; the main skill's from its stats, another group's from its useRates row at its own rate."""
    groups = {"mainSocketGroup": 1, "groups": [
        {"index": 1, "gems": [{"name": "Spark"}, {"name": "Overcharge"}]},
        {"index": 2, "gems": [{"name": "Lightning Bolt"}, {"name": "Overcharge"}]},
        {"index": 3, "gems": [{"name": "Fireball"}]}]}
    stats = {"Speed": 2.0, "HitChance": 100.0, "ShockChancePerHit": 25.0}
    rows = {2: {"name": "Lightning Bolt", "cooldown": 2.0, "HitChance": 100.0, "ShockChancePerHit": 50.0},
            3: {"name": "Fireball", "cooldown": 1.0, "ShockChancePerHit": 100.0}}
    found = c.overcharge_sources(groups, "Spark", stats, rows)
    assert found == [("Spark", pytest.approx(0.5)), ("Lightning Bolt", pytest.approx(0.25))]
    on, how, up = c._self_shocked([], {"_overcharge": found})
    assert up == pytest.approx(1 - math.exp(-0.75 * c.SELF_SHOCK_SECONDS)) and "Overcharge" in how
    half = c._self_shocked([], {"_overcharge": found, "ShockAvoidChance": 50})[2]
    assert half == pytest.approx(1 - math.exp(-0.375 * c.SELF_SHOCK_SECONDS))
    assert c._self_shocked([], {"_overcharge": found, "ShockAvoidChance": 100})[0] == 0.0
    assert c._self_shocked([], {}) == (0.0, "")


def test_stoicism_averages_its_ramp_between_resets():
    """MECH75 (Mirror sweep MA-8): N% more a second up to 20%, reset by each dodge roll / Travel skill use: averaged."""
    from builder.search import assumptions as a

    assert a.stoicism_average(1, 5) == pytest.approx(2.5)  # (Stoicism I, a roll every 5 s)
    assert a.stoicism_average(2, 2) == pytest.approx(2.0)  # (II, a roll every 2 s)
    assert a.stoicism_average(2, 20) == pytest.approx(20 - 400 / 80)  # (capped for the last 10 s of each 20 s)
    assert a.stoicism_average(1, 0) == 0.0
    assert a.ROLL_RELIANT.search("Trigger a skill when you Dodge Roll") and not a.ROLL_RELIANT.search("+1 to Strength")
