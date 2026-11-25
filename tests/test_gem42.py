"""GEM42: Combo's decay in models.vruun_combo_share (Culmination / Vruun's Inevitability; GEM41's find)."""

from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill

CULMINATION_I = {"skill_maximum_number_of_combo_stacks": 10, "base_combo_stacks_decay_delay_ms": 4000}


def _main(stats: dict | None = None) -> Skill:
    return Skill("Earthquake", 1, attack=True, melee=True, support_stats=dict(stats or CULMINATION_I))


def test_the_decay_stat_is_summed_by_the_bridge():
    assert models.COMBO_DECAY_STAT in models.gem_stat_support_stats()


def test_random_timing_matches_a_monte_carlo():
    # (GEM41's Monte Carlo, scratch/gem41/combo_decay.py; capped: scratch/gem42/check.py's 40,000 runs)
    assert models.combo_at_random_timing(0.2, 4.0, 10.0, 10) == pytest.approx(1.159, abs=0.02)
    assert models.combo_at_random_timing(3.0, 4.0, 5.0, 10) == pytest.approx(9.864, abs=0.05)
    assert models.combo_at_random_timing(1.0, 4.0, 6.0, 3) == pytest.approx(2.867, abs=0.02)
    assert models.combo_at_random_timing(0.0, 4.0, 6.0, 10) == 0.0


def test_the_chain_cap_only_binds_when_builders_cannot_strike_within_the_decay():
    assert models.combo_chain_cap([0.5], 4.0) is None
    assert models.combo_chain_cap([5.0, 5.0], 4.0) is None  # (staggered: a hit every 2.5 s)
    assert models.combo_chain_cap([8.0], 4.0) == 1.0
    assert models.combo_chain_cap([9.0, 9.0], 4.0) == 2.0


def test_fast_builders_keep_the_plain_combo_and_note_the_random_timing_reading():
    main = _main()
    skills = [main, Skill("Boneshatter", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Boneshatter", "speed": 2.0, "HitChance": 100, "_press": 0.1}}
    combo, why, play = models.vruun_combo_share(main, {"Speed": 0.2, "_groups": groups, "_skills": skills}, main)
    # 0.2 hits/s over 0.2 uses/s: 1 Combo pressed right before each use; at random times the 4 s decay cuts it
    assert combo == pytest.approx(1.0)
    assert "at random timing" in why and "Strike within 4 s" in play
    random = float(why.split("at random timing")[1].split(")")[1].split()[0].rstrip(")"))
    assert random < 1.0


def test_a_builder_slower_than_the_decay_holds_one_combo_a_use():
    main = _main()
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 0.125, "cooldown": 8.0, "HitChance": 100, "_press": 1.0}}
    combo, why, _ = models.vruun_combo_share(main, {"Speed": 0.04, "_groups": groups, "_skills": skills}, main)
    # 3.1 hits a use without the decay; a Strike every 8 s can't chain within 4 s: 1
    assert combo == pytest.approx(1.0)
    assert "at most 1 Combo a use" in why and "without the decay: 3.1" in why


def test_vruuns_six_second_decay_lets_the_same_builder_chain():
    main = _main({"skill_maximum_number_of_combo_stacks": 10, "base_combo_stacks_decay_delay_ms": 6000})
    skills = [main, Skill("Slow Strike", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Slow Strike", "speed": 0.2, "cooldown": 5.0, "HitChance": 100, "_press": 1.0}}
    combo, why, _ = models.vruun_combo_share(main, {"Speed": 0.05, "_groups": groups, "_skills": skills}, main)
    assert combo == pytest.approx(4.0) and "at most" not in why


def test_a_set_models_damage_steer_is_carried_in_its_mix_note():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0}, {"label": "Final Strike", "TotalDPS": 120.0},
            {"label": "Explosion", "TotalDPS": 300.0}]
    main = Skill("Rapid Assault", 1, attack=True, melee=True, stat_set="Explosion")
    mods, notes = models.detonated_set_model(main, "Explosion", sets, 1.0, 0.0, "no Detonator")
    assert mods and mods[0]["name"] == "Damage"  # (the steer: no Detonator, the Explosion held under the plain set)
    assert notes[0]["set_damage"] == pytest.approx(1 + mods[0]["value"] / 100)
    assert models.set_damage([], "Rapid Assault") == 1.0


def test_the_strongest_ailment_takes_the_mix_models_steer_out_once():
    """MECH107 (POE2T8800-8802): the steer F sits on every set row and on the scored set; the mixed Bleed is the sets'
    own, so the final line is the plain mix over PoB's scored Bleed without F."""
    steer = 0.4
    base = [{"label": "Plain", "BleedDPS": 30.0, "BleedStackPotential": 2.0, "Speed": 1.0, "DpsMultiplier": 1.0},
            {"label": "Explosion", "BleedDPS": 60.0, "BleedStackPotential": 2.0, "Speed": 1.0, "DpsMultiplier": 1.0}]
    rows = [{**s, "BleedDPS": s["BleedDPS"] * steer} for s in base]  # (PoB's rows under the steer)
    main = Skill("Rapid Assault", 1, attack=True, melee=True, stat_set="Explosion")
    mods, notes = models.strongest_ailment_model(main, rows, {"Plain": 1.0, "Explosion": 0.0}, 1.0, steer=steer)
    factor = 1 + mods[0]["value"] / 100
    # PoB's scored Bleed under the steer x this line = the Plain set's own Bleed (30), not 30 x F
    assert 60.0 * steer * factor == pytest.approx(30.0, rel=1e-3)
    assert "taken back out" in notes[0]["basis"]
    old, _ = models.strongest_ailment_model(main, rows, {"Plain": 1.0, "Explosion": 0.0}, 1.0)
    assert 60.0 * steer * (1 + old[0]["value"] / 100) == pytest.approx(30.0 * steer, rel=1e-3)  # (before: F twice)
