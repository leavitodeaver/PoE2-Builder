"""COV59: the five delay rows settled by RESEARCH62 R62-4 / R62-5 (the manager, 25 Nov 18:37)."""
from builder.ledger import coverage


def test_cov59_r62_delays_are_decided():
    for stat, skill in (("explosion_delay_ms", "Detonate Dead"), ("explosion_delay_ms", "Voltaic Fulmination"),
                        ("explosion_delay_ms", "Fiery Reclamation"), ("aftershock_delay_ms", "Furious Slam"),
                        ("aftershock_delay_ms", "Supercharged Slam")):
        entry = coverage.gem_stat_pattern_entry(stat, skill)
        assert entry is not None and entry.id in ("gem-cov58c-delays-keep-the-rate", "gem-cov59-delays-keep-the-rate"), \
            (stat, skill)
    # (the entry names only the researched skills: an unresearched carrier stays open)
    assert coverage.gem_stat_pattern_entry("explosion_delay_ms", "Some Other Skill") is None


GEM44_READ = {  # (COV59 turn 4: GEM44's offense_split2.txt rows, each checked against its reader)
    "model-read-cov59-trigger-and-charge-supports": [
        ("chance_%_to_gain_power_charges_per_combo_spent", "Ailith's Chimes"),
        ("supported_offerings_grant_you_a_powercharge_every_x_ms", "Guatelitzi's Ablation"),
        ("runic_boosted_attack_damage_%_gained_as_physical", "Fist Of Kalguur"),
        ("support_hide_of_helbrym_gain_rage_from_%_of_damage_taken", "Helbrym's Hide"),
        ("consume_armour_break_to_trigger_rune_forged_blades", "Runeforged Blades")],
    "model-read-cov59-ailment-swaps-and-aggravate": [
        ("base_bleed_chance_is_poison_chance_instead", "Malady"), ("base_poison_chance_is_bleed_chance_instead", "Malady"),
        ("support_rupture_aggravate_bleeding_on_hitting_heavy_stunned_enemy", "Rupture"),
        ("aggravate_bleeding_on_pin_chance_%", "Rusted Spikes")],
    "model-read-cov59-skill-models": [
        ("permafrost_shard_bonus_damage_delay_ms", "Ice Shards"),
        ("support_bone_shrapnel_physical_damage_equal_to_%_monster_life", "Bone Shrapnel Explosion"),
        ("skill_has_empowered_spark_support", "Empowered Sparks I"),
        ("ancestral_cry_triggers_shockwave_every_X_units_travelled", "Ancestral Cry"),
        ("pillar_of_flame_consume_ignite_to_create_X_pillars", "Flame Pillar")],
    "model-read-cov59-stun-and-ground": [
        ("runic_fortress_stun_threshold_+%_final", "Refutation"), ("stun_threshold_+%_final_while_performing_action", "Parry"),
        ("support_holy_descent_consecrated_ground_base_duration_ms", "Holy Descent")],
}


def test_cov59_gem44_readers_are_marked_and_the_unread_stay_open():
    from builder.search import assumptions, conditions, models, recovery
    assert (conditions.AILITH_CHANCE, conditions.GUATELITZI_EVERY) == (0.25, 4.0)
    assert (models.ICE_SHARDS_RAMP, models.SHRAPNEL_PERCENT, assumptions.VOLCANIC_STEP_METRES) == (2.0, 25.0, 1.2)
    assert (recovery.REFUTATION_THRESHOLD, recovery.PARRY_STUN_THRESHOLD, recovery.HELBRYM_RAGE_SHARE) == (0.5, 0.6, 0.03)
    assert "Frenzied Riposte" in models.PARRY_CONSUMERS
    covered = coverage.stat_entries()
    for entry, pairs in GEM44_READ.items():
        for stat, skill in pairs:
            assert coverage.stat_covered(covered, stat, skill) == ("model", entry), (stat, skill)
    for stat, skill in (("frost_wall_damage_taken_+%_final_from_enemies", "Frozen Locus"),  # (left open on purpose)
                        ("wolf_warcry_buff_freeze_buildup_+%", "Arctic Howl"),
                        ("crushing_blow_vs_ignited_and_fully_armour_broken", "Syzygy"),
                        ("cannot_inflict_maim", "Undermine")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat
