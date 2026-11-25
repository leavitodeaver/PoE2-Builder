"""COV58 turn 5: TRAIN 149's readers, the manager's two already-read sets and the settled delay rows."""
from builder.ledger import coverage
from builder.search import assumptions, conditions, flasks, recovery


def test_cov58c_readers_are_marked():
    assert "attacks_chance_to_blind_on_hit_%" in conditions.BLIND_HIT_STATS
    assert {"recover_%_maximum_mana_on_cull", "active_skill_life_gained_on_cull"} <= set(recovery.CULL_STATS)
    assert flasks.DEVOUR_FLASK_STAT == "wyvern_devour_flask_charm_charge_gain_chance_%"
    assert assumptions.SIPHONING_PERCENT == 8.0  # (800 permyriad at every level)
    covered = coverage.stat_entries()
    expect = {("attacks_chance_to_blind_on_hit_%", "Twister"): "model-read-cov58c-blind-on-hit",
              ("attacks_chance_to_blind_on_hit_%", "Flash Grenade"): "model-read-cov58c-blind-on-hit",
              ("recover_%_maximum_mana_on_cull", "Killing Palm"): "model-read-cov58c-on-cull-recovery",
              ("recover_%_maximum_mana_on_cull", "Soul Drain"): "model-read-cov58c-on-cull-recovery",
              ("active_skill_life_gained_on_cull", "Reap"): "model-read-cov58c-on-cull-recovery",
              ("wyvern_devour_flask_charm_charge_gain_chance_%", "Devour"): "model-read-cov58c-corpse-flask-charges",
              ("support_grants_X_life_flask_charges_on_using_corpse", "Gorge"): "model-read-cov58c-corpse-flask-charges",
              ("support_grants_X_mana_flask_charges_on_using_corpse", "Gorge"): "model-read-cov58c-corpse-flask-charges",
              ("marked_enemies_leech_physical_attack_damage_taken_as_life_to_you_permyriad", "Mark of Siphoning II"):
              "model-read-cov58c-mark-of-siphoning",
              ("skill_grant_X_power_charges_against_normal_and_magic_monsters", "Killing Palm"):
              "model-read-cov58c-killing-palm-power-charges"}
    for (stat, skill), entry in expect.items():
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry), (stat, skill)
    for stat, skill in (("maximum_curse_zones_allowed", "Doedre's Undoing"),  # (rows only)
                        ("skill_grant_X_power_charges_against_rare_monsters", "Killing Palm")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat


def test_cov58c_settled_delays_are_decided_for_the_researched_skills_only():
    for stat, skill in (("wolf_pounce_mark_shockwave_delay_ms", "Pounce"), ("wolf_pounce_mark_shockwave_delay_ms", "Cross Slash"),
                        ("explosion_delay_ms", "Decompose"), ("aftershock_delay_ms", "Rampage")):
        entry = coverage.gem_stat_pattern_entry(stat, skill)
        assert entry is not None and entry.id == "gem-cov58c-delays-keep-the-rate", (stat, skill)
    for stat, skill in (("explosion_delay_ms", "Detonate Dead"), ("aftershock_delay_ms", "Furious Slam")):
        assert coverage.gem_stat_pattern_entry(stat, skill) is None, (stat, skill)
