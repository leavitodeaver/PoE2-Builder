"""MECHB48: Fissures at the boss (R37-16) and Forge Hammer's Warcry shatter."""
from __future__ import annotations

import pytest

from builder.ledger import model_entry
from builder.search import models
from builder.search.models import Skill

CHANCE = "Skills which create Fissures have a 30% chance to create an additional Fissure"


def _sets(**dps):
    return [{"label": k.replace("_", " "), "TotalDPS": v} for k, v in dps.items()]


def test_the_chance_line_and_upheaval_add_fissures_on_a_line_skill():
    sunder = Skill("Sunder", 1, attack=True, melee=True)
    mods, notes = models.fissure_count_model(sunder, "Wave", _sets(Wave=100.0, Shockwave=40.0), {},
                                             {models.FISSURE_EXTRA_STAT: 2}, [CHANCE], 0.4)
    assert mods[0]["value"] == pytest.approx(100 * (3.3 - 1))  # 1 + 2 + 30%, all in melee
    assert notes[0]["entry"] == models.FISSURE_ENTRY and "x1.000" in notes[0]["basis"]  # (at range: the aimed one)
    assert model_entry(CHANCE).id == models.FISSURE_ENTRY


def test_the_chance_is_held_to_one_more_and_a_non_fissure_set_is_not_scaled():
    sunder = Skill("Sunder", 1, attack=True, melee=True)
    mods, _ = models.fissure_count_model(sunder, "Wave", _sets(Wave=100.0), {}, {}, [CHANCE] * 5, 0.4)
    assert mods[0]["value"] == pytest.approx(100.0)  # 150% held to one more
    mods, notes = models.fissure_count_model(sunder, "Shockwave", _sets(Wave=100.0, Shockwave=40.0), {}, {}, [CHANCE],
                                             0.4)
    assert not mods and notes[0]["value"] == "x1"
    assert models.fissure_count_model(sunder, "Wave", _sets(Wave=100.0), {}, {}, [], 0.4) == ([], [])


def test_fury_of_the_mountain_counts_its_random_arc():
    fotm = Skill("Fury of the Mountain", 1, attack=True, melee=True)
    mods, notes = models.fissure_count_model(fotm, "Fury of the Mountain", _sets(Fury_of_the_Mountain=100.0), {},
                                             {models.FISSURE_EXTRA_STAT: 1}, [], 0.4)
    assert mods[0]["value"] == pytest.approx(100.0)  # all at 1.5 m
    assert models.fissure_arc_share(0.4, 3.0) == pytest.approx(0.4636, rel=1e-3)  # R37-16's ~46%
    assert "46%" in notes[0]["basis"]


def test_forge_hammer_counts_its_spiral_only_on_the_shattered_share():
    hammer = Skill("Forge Hammer", 1, attack=True, melee=True)
    sets = _sets(Impact=60.0, Fissures=50.0)
    # half the hammers shattered: PoB's one Fissure set at 50 x 0.5 = 25 of 85; the game's 5 + 1 (Upheaval I)
    mods, notes = models.fissure_count_model(hammer, "Impact", sets, {models.FISSURE_BASE_STAT: 5},
                                             {models.FISSURE_EXTRA_STAT: 1}, [], 0.4, det_share=0.5)
    assert mods[0]["value"] == pytest.approx(100 * (25.0 / 85.0) * 5.0, abs=0.01)
    # no Warcry: nothing set off, nothing to scale
    mods, _ = models.fissure_count_model(hammer, "Impact", sets, {models.FISSURE_BASE_STAT: 5}, {}, [], 0.4,
                                         det_share=0.0)
    assert not mods
    # held to 10 a use
    mods, _ = models.fissure_count_model(hammer, "Fissures", sets, {models.FISSURE_BASE_STAT: 5},
                                         {models.FISSURE_EXTRA_STAT: 9}, [], 0.4, det_share=1.0)
    assert mods[0]["value"] == pytest.approx(100 * (50.0 / 110.0) * 9.0, abs=0.01)


def test_earthshatter_scales_only_its_slam_beside_the_spikes():
    quake = Skill("Earthshatter", 1, attack=True, melee=True)
    mods, _ = models.fissure_count_model(quake, "Slam", _sets(Slam=30.0, Explosion=120.0), {},
                                         {models.FISSURE_EXTRA_STAT: 1}, [], 0.4, det_share=0.25)
    assert mods[0]["value"] == pytest.approx(100 * 30.0 / 60.0)  # Slam 30 of 30 + 120 x 25%, two Fissures


def test_forge_hammer_shatters_on_warcries_only():
    assert "Forge Hammer" in models.DETONATED_SETS and models.DETONATED_BY["Forge Hammer"][0] == "Warcry"
    assert "Forge Hammer" not in models.DETONATORS_TOO and "Earthshatter" in models.DETONATORS_TOO
    hammer = Skill("Forge Hammer", 1, attack=True, melee=True)
    mods, notes = models.detonated_set_model(hammer, "Fissures", _sets(Impact=60.0, Fissures=50.0), 1.0, 0.0,
                                             "no Warcry in the build")
    assert notes[0]["entry"] == "model-mechb48-forge-hammer-warcry-shatter"
    assert mods  # (no Warcry: the Fissures set kept under the Impact so the search keeps Impact)


LAMENT = "Sacrifice 300 Life to not consume the last bolt when firing"
BOW = {"BoltCount": 6, "FiringRate": 3.0, "Speed": 2.0, "EffectiveReloadTime": 1.0, "ChanceToNotConsumeAmmo": 0,
       "Life": 3000}


def test_the_last_lament_fires_unbroken_for_the_share_its_life_recovery_pays():
    shot = Skill("Galvanic Shards", 1, attack=True)
    # 600 Life/s back less 150 from the boss's hits: 450 / (300 x 3) = 50% of the fight unbroken
    mods, notes = models.last_bolt_sacrifice_model(shot, [LAMENT], BOW, (600.0, 150.0, 0.0, 0.0, ["150 from hits"]))
    assert mods[0]["value"] == pytest.approx(100 * ((0.5 * 3 + 0.5 * 2) / 2 - 1), abs=0.01)
    assert notes[0]["entry"] == models.LAMENT_ENTRY and "x1.500" in notes[0]["basis"]
    # held to every reload skipped
    mods, _ = models.last_bolt_sacrifice_model(shot, [LAMENT], BOW, (5000.0, 0.0, 0.0, 0.0, []))
    assert mods[0]["value"] == pytest.approx(50.0, abs=0.01)


def test_the_last_lament_gives_nothing_without_life_or_a_clip():
    shot = Skill("Galvanic Shards", 1, attack=True)
    flows = (600.0, 0.0, 0.0, 0.0, [])
    assert not models.last_bolt_sacrifice_model(shot, [LAMENT], {**BOW, "ChaosInoculation": 1}, flows)[0]
    assert not models.last_bolt_sacrifice_model(shot, [LAMENT], {**BOW, "Life": 250}, flows)[0]
    assert not models.last_bolt_sacrifice_model(shot, [LAMENT], {**BOW, "EffectiveReloadTime": 0}, flows)[0]
    assert not models.last_bolt_sacrifice_model(shot, [LAMENT], BOW, (100.0, 200.0, 0.0, 0.0, []))[0]
    assert not models.last_bolt_sacrifice_model(shot, [], BOW, flows)[0]


BINDING = "Gain 1 Runic Binding on Hit with Spells, no more than once every 0.5 seconds"


def test_runic_bindings_cut_a_main_spells_damage_by_the_stacks_its_hits_keep():
    spark = Skill("Spark", 1, spell=True, cast_time=0.7)
    mods, notes = models.runic_binding_model(spark, [BINDING], {"Speed": 3.0, "HitChance": 100})
    assert mods[0]["line"] == "1% increased Spell Damage" and mods[0]["scale"] == pytest.approx(-100.0)  # 2/s cap: 10
    mods, _ = models.runic_binding_model(spark, [BINDING], {"Speed": 0.4, "HitChance": 100, "_main_share": 0.5})
    assert mods[0]["scale"] == pytest.approx(-20.0)  # 0.2 a second x 10 s = 2 stacks
    assert notes[0]["entry"] == models.BINDING_ENTRY


def test_runic_bindings_need_a_main_spell():
    slam = Skill("Boneshatter", 1, attack=True, melee=True)
    mods, notes = models.runic_binding_model(slam, [BINDING], {"Speed": 2.0, "HitChance": 100})
    assert not mods and notes[0]["value"] == 0
    assert models.runic_binding_model(slam, [], {"Speed": 2.0}) == ([], [])


RAGE_WARD = "Recover 4% of maximum Runic Ward on reaching Maximum Rage"


def test_ward_on_reaching_maximum_rage_counts_at_the_builds_reaches():
    income, parts, unread = models.ward_income(10.0, 1000.0, [RAGE_WARD], rage_reaches=(0.2, "every 5.0 s"))
    assert income == pytest.approx(10.0 + 0.04 * 1000 * 0.2) and not unread
    assert any("Maximum Rage" in p for p in parts)
    income, _, unread = models.ward_income(10.0, 1000.0, [RAGE_WARD])  # (no reach rate: named, not counted)
    assert income == pytest.approx(10.0) and unread == ["Runic Ward on reaching Maximum Rage"]
