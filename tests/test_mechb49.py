"""MECHB49: the Fissure counts GEM28 listed (the manager 21:38): Starborn Onslaught's ring, Molten Crash's Rage."""
from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill


def _sets(**dps):
    return [{"label": k.replace("_", " "), "TotalDPS": v} for k, v in dps.items()]


def test_starborn_onslaught_counts_its_ring_of_three_on_the_impact():
    star = Skill("Starborn Onslaught", 1, attack=True, melee=True)
    level = {models.FISSURE_BASE_STAT: 3, "number_of_branching_fissures": 2}
    mods, notes = models.fissure_count_model(star, "Impact", _sets(Impact=100.0, Star_Fall=50.0), level,
                                             {models.FISSURE_EXTRA_STAT: 1}, [], 0.4)
    assert mods[0]["value"] == pytest.approx(100 * (4 - 1))  # 3 + Upheaval I's 1, all under the boss
    assert notes[0]["confidence"] == "MEDIUM" and "branching" in notes[0]["basis"]
    mods, _ = models.fissure_count_model(star, "Star Fall", _sets(Impact=100.0, Star_Fall=50.0), level, {}, [], 0.4)
    assert not mods  # (the Star Fall isn't a Fissure)


def test_molten_crash_adds_a_fissure_on_the_uses_its_rage_pays():
    crash = Skill("Molten Crash", 1, attack=True, melee=True)
    sets = _sets(Fissures=100.0)
    kept = (5.0, "Molten Crash 5.00/s")
    mods, notes = models.fissure_count_model(crash, "Fissures", sets, {}, {}, [], 0.4, rage_spent=kept)
    assert mods[0]["value"] == pytest.approx(100.0) and "every use paid" in notes[0]["basis"]
    short = (2.5, "Molten Crash 5.00/s (asking 5.00/s, more than comes: 2.50/s)")
    mods, _ = models.fissure_count_model(crash, "Fissures", sets, {}, {}, [], 0.4, rage_spent=short)
    assert mods[0]["value"] == pytest.approx(50.0)
    assert models.fissure_count_model(crash, "Fissures", _sets(Fissures=100.0), {}, {}, [], 0.4)[0] == []  # (no Rage: one)
    other = (3.0, "Furious Slam 3.00/s")
    assert models.fissure_count_model(crash, "Fissures", sets, {}, {}, [], 0.4, rage_spent=other) == ([], [])


class _SideBuild:
    """A fake build: a Spark in a Full DPS side group beside a main Fireball."""

    def __init__(self, full=True):
        self.objective = type("O", (), {"full": full, "entry_scales": {}})()

    def groups(self):
        return [{"index": 1, "label": "main", "enabled": True, "fullDPS": True, "skills": [{"name": "Fireball"}]},
                {"index": 2, "label": "side", "enabled": True, "fullDPS": True, "skills": [{"name": "Spark"}]}]

    def call(self, name, **kw):
        if name == "activeSkills":
            return {"skills": []}
        assert name == "setRates" and kw["index"] == 2
        return {"sets": [{"label": "Spark", "TotalDPS": 50.0, "ProjectileCount": 8, "Duration": 2.0}]}


def test_a_side_spark_groups_entry_is_scaled_to_its_hits_a_cast():
    from builder.search.assumptions import Assumptions
    build = _SideBuild()
    a = Assumptions(build)
    skills = [{"name": "Spark", "group": 2, "spell": True, "statSet": "Spark"}]
    notes = a.side_sparks(skills, Skill("Fireball", 1, spell=True), set())
    assert build.objective.entry_scales == {"Spark": pytest.approx(3.0)}  # min(8, floor(2 / 0.66)), R36-5's 3
    assert "side group side" in notes[0]["label"] and notes[0]["confidence"] == "LOW"
    build = _SideBuild()
    assert Assumptions(build).side_sparks(skills, Skill("Spark", 2, spell=True), set()) == []  # (main Spark: its own mod)
    assert build.objective.entry_scales == {}
    build = _SideBuild(full=False)
    assert Assumptions(build).side_sparks(skills, None, set()) == []


class _HammerBuild:
    """A fake build: Forge Hammer in a Full DPS side group, its chosen set `chosen`, no Warcry."""

    def __init__(self, chosen):
        self.chosen = chosen
        self.objective = type("O", (), {"full": True, "entry_scales": {}})()
        self.ctx = type("C", (), {"gems": {}})()

    def groups(self):
        return [{"index": 1, "label": "main", "enabled": True, "fullDPS": True, "skills": [{"name": "Boneshatter"}],
                 "gems": [{"name": "Boneshatter"}]},
                {"index": 2, "label": "side", "enabled": True, "fullDPS": True, "skills": [{"name": "Forge Hammer"}],
                 "gems": [{"name": "Forge Hammer"}]}]

    def call(self, name, **kw):
        if name == "activeSkills":
            return {"skills": []}
        if name == "groups":
            return {"groups": self.groups()}
        if name == "useRates":
            return {"rates": [{"name": "Forge Hammer", "speed": 1.0}]}
        if name == "getConfig":
            return {"input": {}}
        assert name == "setRates"
        return {"sets": [{"label": "Impact", "TotalDPS": 20.0}, {"label": "Fissures", "TotalDPS": 40.0}],
                "chosen": self.chosen}


def test_a_side_forge_hammer_without_a_warcry_keeps_only_its_impact():
    from builder.search.assumptions import Assumptions
    build = _HammerBuild("Fissures")
    notes = Assumptions(build).side_forge_hammer(Skill("Boneshatter", 1, attack=True), "Boneshatter", set(), 1.0)
    assert build.objective.entry_scales == {"Forge Hammer": pytest.approx(0.5)}  # (20 + 0 x 40) / 40
    assert "x0.500" == notes[0]["value"]
    build = _HammerBuild("Impact")
    assert Assumptions(build).side_forge_hammer(Skill("Boneshatter", 1, attack=True), "Boneshatter", set(), 1.0) == []


def test_corpsewades_travel_trigger_adds_nothing_to_the_headline_and_a_row_held_to_corpses():
    from builder.ledger import model_entry
    line = "Trigger Decompose every 1.2 metres travelled"
    notes = models.decompose_travel_note([line], 0.5, 2.0, 3.0)
    assert notes[0]["value"] == "0 in the headline" and notes[0]["confidence"] == "LOW"
    assert "1.25 Decompose/s" in notes[0]["basis"] and "0.50/s" in notes[0]["basis"]  # (3 / 1.2 / 2, held to 0.5)
    assert models.decompose_travel_note(["Grants Skill: Level 20 Decompose"], 0.5, 2.0, 3.0) == []
    assert model_entry(line).id == models.DECOMPOSE_ENTRY


def test_molten_crash_counts_its_slam_and_its_fissures_each_use():
    crash = Skill("Molten Crash", 1, attack=True, melee=True)
    sets = _sets(Molten_Crash=400.0, Fissures=160.0)
    mods, notes = models.fissure_count_model(crash, "Molten Crash", sets, {}, {}, [], 0.4)
    assert mods[0]["value"] == pytest.approx(40.0)  # (400 + 1 x 160) / 400, PoB scoring the slam alone
    assert "UNDERCOUNT" in notes[0]["basis"]
    mods, _ = models.fissure_count_model(crash, "Molten Crash", sets, {}, {models.FISSURE_EXTRA_STAT: 1}, [], 0.4,
                                         rage_spent=(5.0, "Molten Crash 5.00/s"))
    assert mods[0]["value"] == pytest.approx(100 * ((400 + 3 * 160) / 400 - 1))  # 1 + Upheaval I + Rage


def test_melee_reflect_deals_its_share_of_the_physical_hits_the_boss_lands_in_melee():
    from builder.ledger import model_entry
    line = "250% of Melee Physical Damage taken reflected to Attacker"
    stats = {"PhysicalTakenHit": 2000.0}
    dps, notes = models.melee_reflect_model([line], stats, "Arbiter of Ash", 2.0, 0.5, 0.0)
    assert dps == pytest.approx(5000 * 0.25 * 4 / 34)  # 250% of 2,000 on 0.25 landed hits/s x Arbiter's 4/34 melee
    assert notes[0]["confidence"] == "LOW" and model_entry(line).id == models.MELEE_REFLECT_ENTRY
    armoured, _ = models.melee_reflect_model([line], stats, "Arbiter of Ash", 2.0, 0.5, 10000.0)
    assert armoured == pytest.approx(dps * (1 - 10000 / (10000 + 50000)))
    assert models.melee_reflect_model([line], stats, models.THORNS_WORST, 2.0, 0.5, 0.0)[0] == 0.0  # (Zarokh: no Melee)
    assert models.melee_reflect_model(["+10 to Strength"], stats, "Arbiter of Ash", 2.0, 0.5, 0.0) == (0.0, [])
