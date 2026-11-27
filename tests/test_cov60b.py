"""COV60 turn 3: TRAIN 151's readers (MECH110 / 111, MECHB66 / 67, MECHC64 / 65, GEM44 / 45), each checked against the
landed code on b9c941b2."""
from builder.ledger import coverage

TRAIN151_READ = {
    "model-read-cov60-mechb66-triggers": [
        ("triggered_by_battershout_support_%", "Battershout"), ("triggered_by_caltrops_support_%", "Caltrops"),
        ("caltrops_on_dodge_trigger_interval_%_dodge", "Trail of Caltrops"),
        ("chance_to_trigger_volcanic_eruption", "Volcanic Eruption"), ("triggered_by_mana_flare_support_%", "Mana Flare"),
        ("triggered_on_critical_strike_%", "Lightning Bolt"),
        ("deal_thorns_damage_twice_on_hit_for_X_hits_after_thorns_trigger", "Barbs III"),
        ("chance_to_trigger_fiery_death_on_ignited_enemy_death_%", "Fiery Death"),
        ("triggers_burning_runes_on_placing_ground_rune", "Burning Inscription")],
    "model-read-cov60-train151-readers": [
        ("tempest_bell_physical_damage_%_as_elemental_per_ailment", "Tempest Bell"),
        ("bell_ailment_threshold", "Tempest Bell"), ("unleash_static_field_number_of_pluses", "Crash Lightning"),
        ("ground_effects_cannot_trigger", "Persistent Ground II"),
        ("ice_nova_number_of_frost_bolts_to_cast_on", "Ice Nova"),
        ("nova_skills_cast_at_target_location", "Astral Projection"),
        ("lightning_damage_roll_always_min_or_max", "Esh's Prowess"),
        ("remnant_pickup_range_+%", "Magnetic Remnants"), ("base_remnant_duration_ms", "Into the Breach"),
        ("breach_flame_spawn_radius", "Into the Breach"), ("chance_to_retain_40%_of_glory_on_use_%", "Spear of Solaris"),
        ("seismic_cry_slam_skill_additional_aftershocks", "Seismic Cry"),
        ("support_relentless_rage_spending_X_rage_grants_rage_on_next_skill_use", "Relentless Rage")],
    "model-mech110-herald-of-plague-spread": [
        ("herald_of_agony_poison_spread_on_kill_radius_base_magnitude_to_grant", "Herald of Plague")],
}


def test_cov60b_train151_readers_are_marked():
    from builder.search import conditions, models, remnant_walk
    assert models.BELL_THRESHOLD_STAT == "bell_ailment_threshold" and models.CRASH_PULSES == 3.0
    assert models.PERSISTENT_GROUND_STAT == "ground_effects_cannot_trigger"
    assert conditions.NOVA_FROSTBOLT_STAT == "ice_nova_number_of_frost_bolts_to_cast_on"
    assert "nova_skills_cast_at_target_location" in conditions.NOVA_TARGET_STATS
    assert (remnant_walk.SPAWN_METRES, remnant_walk.REVEAL_METRES) == (8.5, 5.0)
    assert models.GLORY_RETAIN_STAT == "chance_to_retain_40%_of_glory_on_use_%"
    assert (conditions.RELENTLESS_SPEND, conditions.RELENTLESS_GAIN) == (30.0, 10.0)
    assert models.HERALD_OF_PLAGUE_ENTRY == "model-mech110-herald-of-plague-spread"
    covered = coverage.stat_entries()
    for entry, pairs in TRAIN151_READ.items():
        for stat, skill in pairs:
            assert coverage.stat_covered(covered, stat, skill) == ("model", entry), (stat, skill)


def test_cov60b_two_entries_name_the_audited_skills():
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "stomping_ground_trigger_on_footstep_%_chance", "Stomping Ground Shockwave") == (
        "model", "model-gem28-stomping-ground")
    assert coverage.stat_covered(covered, "triggered_by_fist_of_kalguur_support_%", "Fist of Kalguur") == (
        "model", "model-mechb53-kalguur-rune")


def test_cov60b_rows_left_open_stay_open():
    covered = coverage.stat_entries()
    # (COV62: permafrost_shard_limit left this list: MECHB69's ice_shards_limit_mods put it in the headline, marked in
    # items-cov62.yaml / test_cov62)
    for stat, skill in (("unleash_static_field_time_till_final_strike_ms", "Crash Lightning"),
                        ("wyvern_devour_empowered_flame_breath_rage_consumption_+%", "Flame Breath"),
                        ("number_of_triskelion_cascades_for_next_skill", "Triskelion Cascade"),
                        ("number_of_poison_cloud_allowed", "Decompose")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat


def test_cov60b_caltrops_generic_trigger_flag_is_read_by_its_support_model():
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "triggered_by_generic_ongoing_trigger", "Caltrops") == (
        "model", "model-gem28-caltrops-support")
    assert coverage.stat_covered(covered, "triggered_by_generic_ongoing_trigger", "SupportMetaCastOnCritPlayer") == (
        "model", "model-gem24-link-generic-ongoing-trigger")


def test_cov60b_mechb68_explosive_demise_life_share_is_marked():
    from builder.search import models
    assert models.EXPLOSIVE_DEMISE_LIFE_PCT == 8.0
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "arsonist_destructive_link_%_of_life_as_fire_damage", "Explosive Demise") == (
        "model", "model-read-cov60-explosive-demise")
    # (already held: Mystic Avalanche's side areas by COV55's entry)
    assert coverage.stat_covered(covered, "cascadable_spells_final_echo_also_cascades_to_sides", "Mystic Avalanche") in (
        None, ("model", "model-read-cov55-mystic-avalanche"))
