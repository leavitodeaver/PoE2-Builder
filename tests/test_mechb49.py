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
    assert mods[0]["value"] == pytest.approx(100 * (5 - 1))  # the impact + 3 + Upheaval I's 1 (R39-5)
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
    assert build.objective.entry_scales == {}  # (R38-13: one hit a cast, PoB's own: nothing to scale; rows in the note)
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
    half = models.decompose_travel_note([line], 5.0, 2.0, 4.6, 0.5)  # (R38-18: a 4.6 m roll on half the attacks)
    assert "0.96 Decompose/s" in half[0]["basis"]
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


def test_forge_hammer_throws_and_recalls_without_a_warcry():
    hammer = Skill("Forge Hammer", 1, attack=True, melee=True)
    mods, notes = models.forge_hammer_loop_model(hammer, 0.125, 1.0, 8.0, 0.0)
    loop = 1 / (1 + 1 / 1.7)
    assert mods[0]["value"] == pytest.approx(100 * (loop / 0.125 - 1), abs=0.01)  # ~0.63 Impacts/s against PoB's 1 / 8 s
    assert "Throw Forge Hammer" in notes[0]["play"]
    mods, notes = models.forge_hammer_loop_model(hammer, 0.125, 1.0, 8.0, 0.2)  # (a Warcry: left lodged, rows only)
    assert mods == [] and notes[0]["value"] == "rows only" and "0.071/s (x0.57)" in notes[0]["basis"]  # (1 + 1 / 0.2 + 8 s)


def test_eye_of_winter_counts_the_shards_fired_through_the_boss():
    eye = Skill("Eye of Winter", 1, spell=True)
    stats = {"AverageDamage": 100.0, "Speed": 0.1, "TotalDPS": 100.0, "ProjectileCount": 30}  # (GEM19's x10 applied)
    mods, notes = models.eye_shards_model(eye, stats, 1.0, 3.0, 10.0)
    inside = 30 * 2 / 2.25
    side = models.eye_side_shards(30, 1.0, 3.0)
    assert 2 < side < 6  # (asin(R / x) / pi from 3 m in: ~17% near the boss, ~11% at 3 m)
    n = inside + 2 * side
    assert mods[0]["value"] == pytest.approx(100 * ((n + 9) / 10 - 1), abs=0.1)  # ((1 + 9) + (N - 1)) / (1 + 9)
    assert "aimed past the boss" in notes[0]["basis"] and notes[0]["confidence"] == "LOW"
    alone, _ = models.eye_shards_model(eye, {**stats, "TotalDPS": 10.0}, 1.0, 3.0, 1.0)  # (GEM19's line off)
    assert alone[0]["value"] == pytest.approx(100 * (n - 1), abs=0.1)


def test_echoing_cry_echoes_add_their_own_shield_waves():
    row = {"speed": 0.125, "warcryPower": 20}
    mods, notes = models.shield_wave_model(row, -50.0, 2.0, "main")  # (10 Power: 2 a cry, x3 with two echoes)
    assert mods[0]["value"] == pytest.approx(0.125 * 6)
    assert "refreshed it instead, 2 a use, 0.250/s" in notes[0]["basis"]


def test_a_chance_to_fire_projectiles_in_a_circle_is_not_a_circle_layout():
    chance = "8% chance for Spell Skills to fire 8 additional Projectiles in a circle"
    assert not models.SPELL_CIRCLE.search(chance) and models.SPELL_CIRCLE.search("Spells fire Projectiles in a circle")
    mods = [{"kind": "Raw", "name": "DPS", "type": "MORE", "value": 300}]
    assert models.bonestorm_circle([chance], mods, [{"value": "x4"}])[0] == mods  # (GEM19's channel kept)


def test_molten_crash_shows_rows_for_its_unsettled_base_count():
    crash = Skill("Molten Crash", 1, attack=True, melee=True)
    _, notes = models.fissure_count_model(crash, "Molten Crash", _sets(Molten_Crash=400.0, Fissures=160.0), {}, {}, [],
                                          0.4)
    assert "with a base of 2 a use" in notes[0]["basis"] and "x1.800" in notes[0]["basis"]  # (400 + 2 x 160) / 400
    assert "with a base of 8 a use" in notes[0]["basis"] and "x4.200" in notes[0]["basis"]  # (400 + 8 x 160), held to 8


class _CraterBuild:
    """A fake build: Leap Slam supported by Crater, the main skill, in Full DPS."""

    def __init__(self):
        self.objective = type("O", (), {"full": True, "entry_scales": {}})()

    def groups(self):
        return [{"index": 1, "label": "main", "enabled": True, "fullDPS": True,
                 "skills": [{"name": "Leap Slam"}, {"name": "Crater"}], "gems": [{"name": "Leap Slam"}, {"name": "Crater"}]}]

    def call(self, name, **kw):
        if name == "activeSkills":
            return {"skills": []}
        if name == "getConfig":
            return {"input": {}}
        assert name == "useRates" and kw["actives"] == [2]
        return {"rates": [{"name": "Crater", "speed": 1.0}]}


def test_crater_makes_three_fissures_each_landing():
    from builder.search.assumptions import Assumptions
    build = _CraterBuild()
    notes = Assumptions(build).crater_fissures(Skill("Leap Slam", 1, attack=True, melee=True), 0.8)
    assert build.objective.entry_scales == {"Crater": pytest.approx(2.4)}  # 0.8 landings x 3 over PoB's 1 a second
    assert "x1.600" in notes[0]["basis"]  # (the row: 2 a landing)
