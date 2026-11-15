"""MECH75: the boss's Ignited share counts the build's other groups' Ignites (conditions.ignite_others), not the main
skill's alone (MECH74's follow-up, an UNDERCOUNT: 0 where the main skill doesn't Ignite)."""
from __future__ import annotations

import pytest

from builder.search import conditions as c
from builder.search import models

MAIN = {"Speed": 2.0, "HitChance": 100.0, "IgniteChancePerHit": 25.0, "IgniteDuration": 4.0}
NONE = {"Speed": 2.0, "HitChance": 100.0, "IgniteChancePerHit": 0.0}


def test_another_groups_ignite_counts_where_the_main_skill_has_none():
    other = ("Fireball", 1.0, 0.5, 4.0)
    assert c.ailment_uptime(NONE, "Ignite") == 0.0
    share = c.ailment_uptime({**NONE, "_ailment_others": {"Ignite": [other]}}, "Ignite")
    assert share == pytest.approx(1 - 0.5 ** 4.0)
    # an "other" that never lands adds nothing
    assert c.ailment_uptime({**NONE, "_ailment_others": {"Ignite": [("x", 0.0, 0.5, 4.0)]}}, "Ignite") == 0.0


def test_the_sources_combine_as_independent():
    other = ("Fireball", 1.0, 0.5, 4.0)
    own = 1 - 0.75 ** (2.0 * 4.0)
    both = c.ailment_uptime({**MAIN, "_ailment_others": {"Ignite": [other]}}, "Ignite")
    assert c.ailment_uptime(MAIN, "Ignite") == pytest.approx(own)
    assert both == pytest.approx(1 - (1 - own) * 0.5 ** 4.0)
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
