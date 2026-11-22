"""MECH71: His Winnowing Flame's Grisly Pyres (The Unborn Lich; PoB holds no damage for them): each use that finds the
boss Ignited makes one pillar dealing 30% of the Ignite's expected remaining damage (game files; patch 0.5.0)."""
from __future__ import annotations

import pytest

from builder.search import models


def test_a_pyre_is_30_percent_of_the_ignites_remaining_damage_at_the_uses_that_find_it():
    # an Ignite every 0.5 s (4 s long): on average 0.25 s old, 3.75 s left
    dps, how = models.grisly_pyre_model(uses=1.0, ignited=0.8, ignite_dps=1000.0, duration=4.0, applications=2.0)
    assert dps == pytest.approx(1.0 * 0.8 * 0.30 * 1000.0 * 3.75)
    assert "Grisly Pyre" in how and "Aggravated" in how


def test_a_rarely_reapplied_ignite_has_half_its_duration_left():
    dps = models.grisly_pyre_model(uses=0.5, ignited=0.4, ignite_dps=1000.0, duration=4.0, applications=0.1)[0]
    assert dps == pytest.approx(0.5 * 0.4 * 0.30 * 1000.0 * 2.0)


def test_no_use_or_no_ignite_makes_no_pyres():
    assert models.grisly_pyre_model(0.0, 1.0, 1000.0, 4.0, 2.0)[0] == 0.0
    dps, how = models.grisly_pyre_model(1.0, 0.0, 0.0, 4.0, 0.0)
    assert dps == 0.0 and "never_ignite" in how


def test_grisly_pyre_wiring_reads_the_skills_group_rate_and_the_ignited_share():
    """assumptions.grisly_pyre: a pressed His Winnowing Flame at its group's rate, the main skill's Ignite."""
    from types import SimpleNamespace

    from builder.search import assumptions

    class Build:
        ailment_uptimes = {"Ignited": 0.5}
        ctx = SimpleNamespace(gems={})

        def groups(self):
            return [{"index": 1, "enabled": True, "skills": [{"name": "Fireball"}]},
                    {"index": 2, "enabled": True, "skills": [{"name": "His Winnowing Flame"}]}]

        def call(self, what, **kw):
            if what == "useRates":
                return {"rates": [{"index": 2, "speed": 1.0, "cooldown": 2.0}]}
            if what == "stats":
                return {"Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0, "IgniteDPS": 1000.0,
                        "IgniteDuration": 4.0, "IgniteChancePerHit": 50.0}
            if what == "getConfig":
                return {"input": {}}
            return {}

    build = Build()
    build.press_choices = {}
    dps, notes = assumptions.grisly_pyre(build, models.Skill.of({"name": "Fireball"}))
    uses = assumptions.conditions_mod.skill_use_rate({"index": 2, "speed": 1.0, "cooldown": 2.0}, {})[0]
    applications = models.hits_per_second({"Speed": 2.0, "HitChance": 100.0, "CritChance": 0.0}) * 0.5
    remaining = 4.0 - min(1 / applications, 4.0) / 2
    assert uses > 0 and dps == pytest.approx(uses * 0.5 * 0.30 * 1000.0 * remaining)
    assert notes and notes[0]["label"].startswith("Grisly Pyre")
    # no His Winnowing Flame in the build: nothing, no note
    build.groups = lambda: [{"index": 1, "enabled": True, "skills": [{"name": "Fireball"}]}]
    assert assumptions.grisly_pyre(build, models.Skill.of({"name": "Fireball"})) == (0.0, [])


def test_other_ignite_share_is_cut_by_the_consumption():
    """MECH74: models.grisly_pyre_other_ignite: 1 - (1 - c)^(h x D) alone, then side_consumed's cut at the uses."""
    alone = 1 - (1 - 0.5) ** (2.0 * 4.0)
    share, how = models.grisly_pyre_other_ignite(uses=1.0, hits=2.0, chance=0.5, duration=4.0)
    assert share == pytest.approx(alone / (1 + alone * 1.0 * (models.CONSUME_IMMUNE + 1.0)))
    assert models.grisly_pyre_other_ignite(0.0, 2.0, 0.5, 4.0)[0] == pytest.approx(alone)
    assert models.grisly_pyre_other_ignite(1.0, 2.0, 0.0, 4.0)[0] == 0.0
    assert "Consumes" in how


def test_other_ignite_share_from_slow_hits_is_not_always_on():
    """MECHB54: conditions.periodic_absent (MECH99, evenly spaced hits): one hit every 10 s at 100%, a 4 s Ignite: the
    boss is Ignited 40% of the fight by it alone, not 100% (the old (1 - c)^(h x D))."""
    assert models.grisly_pyre_other_ignite(0.0, 0.1, 1.0, 4.0)[0] == pytest.approx(0.4)
    share = models.grisly_pyre_other_ignite(1.0, 0.1, 1.0, 4.0)[0]
    assert share == pytest.approx(0.4 / (1 + 0.4 * 1.0 * (models.CONSUME_IMMUNE + 10.0)))


def test_grisly_pyre_main_case_reads_another_groups_ignite():
    """MECH74 (AUDIT35): His Winnowing Flame as the main skill (never Ignites) Consumes the strongest other group's
    Ignite, scored by useRates as the main skill, at that group's own rate."""
    from types import SimpleNamespace

    from builder.search import assumptions

    fire = {"index": 1, "active": 1, "cooldown": 0.5, "speed": 3.0, "HitChance": 100.0, "IgniteDPS": 2000.0,
            "IgniteDuration": 4.0, "IgniteChancePerHit": 50.0}
    weak = {"index": 3, "active": 1, "cooldown": 1.0, "speed": 3.0, "HitChance": 100.0, "IgniteDPS": 100.0,
            "IgniteDuration": 4.0, "IgniteChancePerHit": 50.0}
    flame = {"index": 2, "active": 1, "speed": 1.5}

    class Build:
        ailment_uptimes = {"Ignited": 0.0}
        ctx = SimpleNamespace(gems={})
        press_choices = {}

        def groups(self):
            return [{"index": 1, "enabled": True, "skills": [{"name": "Fireball"}]},
                    {"index": 2, "enabled": True, "skills": [{"name": "His Winnowing Flame"}]},
                    {"index": 3, "enabled": True, "skills": [{"name": "Weak Ignite"}]}]

        def call(self, what, **kw):
            if what == "useRates":
                return {"rates": [fire, flame, weak]}
            if what == "stats":  # His Winnowing Flame as main: no Ignite of its own
                return {"Speed": 1.5, "HitChance": 100.0, "CritChance": 0.0, "IgniteDPS": 0.0,
                        "IgniteChancePerHit": 0.0}
            if what == "getConfig":
                return {"input": {}}
            return {}

    build = Build()
    dps, notes = assumptions.grisly_pyre(build, models.Skill.of({"name": "His Winnowing Flame"}))
    hits, chance = 2.0, 0.5  # 1 / 0.5 s cooldown
    share = models.grisly_pyre_other_ignite(1.5, hits, chance, 4.0)[0]
    expected = models.grisly_pyre_model(1.5, share, 2000.0, 4.0, hits * chance)[0]
    assert dps > 0 and dps == pytest.approx(expected)
    assert "Fireball's Ignite" in notes[0]["basis"]
    # no other skill Ignites: 0, with its note
    fire["IgniteDPS"] = weak["IgniteDPS"] = 0.0
    dps, notes = assumptions.grisly_pyre(build, models.Skill.of({"name": "His Winnowing Flame"}))
    assert dps == 0.0 and "no skill of the build" in notes[0]["basis"]
