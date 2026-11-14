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
    assert dps == 0.0 and "never_ignite" in how and "UNDERCOUNT" in how


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
