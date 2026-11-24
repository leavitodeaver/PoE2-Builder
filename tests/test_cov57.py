"""COV57: TRAIN 140's readers of open gem-stat rows, marked as read (each checked against the code that names it)."""
from builder.ledger import coverage
from builder.search import assumptions, conditions, models


def test_cov57_train140_readers_are_marked():
    assert conditions.REGULATION_EVERY_STAT == "consume_frenzy_power_and_endurance_charge_every_x_ms"
    assert "base_power_charge_skip_consume_chance_%" in conditions.OWN_KEPT_STATS
    assert "charge_skip_consume_chance_%" in conditions.OWN_KEPT_STATS
    assert conditions.MAIM_STAT == "maim_on_hit_%"
    assert assumptions.VERISIUM_COUNT_STAT == "verisium_infusion_skill_number_of_verisium_infusion_to_grant"
    assert {v[0] for v in assumptions.SIPHON_NON_AILED.values()} == {
        "infusion_remnants_%_chance_to_spawn_fire_infusion_on_igniting_an_enemy",
        "infusion_remnants_%_chance_to_spawn_lightning_infusion_on_shocking_an_enemy"}
    assert models.BELL_RAMP_STAT == "tempest_bell_damage_+%_final_per_time_hit"
    assert (models.VOLCANO_SPRAY_EVERY, models.VOLCANO_SLAM_LIMIT) == (0.5, 10.0)
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("consume_frenzy_power_and_endurance_charge_every_x_ms", "Charge Regulation",
             "model-read-cov57-charge-regulation-tick"),
            ("charge_skip_consume_chance_%", "Perpetual Charge", "model-read-cov57-own-kept-charges"),
            ("base_power_charge_skip_consume_chance_%", "Hollow Form", "model-read-cov57-own-kept-charges"),
            ("infusion_remnants_%_chance_to_spawn_fire_infusion_on_igniting_an_enemy", "Siphon Elements",
             "model-read-cov57-siphon-elements-fire-lightning"),
            ("verisium_infusion_skill_number_of_verisium_infusion_to_grant", "Powered by Verisium",
             "model-read-cov57-powered-by-verisium-count"),
            ("maim_on_hit_%", "Maim", "model-read-cov57-maim-support"),
            ("tempest_bell_damage_+%_final_per_time_hit", "Tempest Bell", "model-read-cov57-tempest-bell-ramp"),
            ("bell_hit_limit", "Hollow Focus", "model-read-cov57-hollow-focus-bell-limit"),
            ("volcano_fires_projectiles_every_x_ms", "Volcano", "model-read-cov57-volcano-sprays"),
            ("curse_apply_as_aura", "SupportBlasphemyPlayer", "model-read-cov57-blasphemy-support-aura"),
            ("curse_supported_by_blasphemy", "SupportBlasphemyPlayer", "model-read-cov57-blasphemy-support-aura")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), stat
    # left open: Decompose's cloud Limit has no reader
    assert coverage.stat_covered(covered, "number_of_poison_cloud_allowed", "Decompose") is None


def test_cov57_voltaic_fulmination_cannot_electrocute_is_no_effect():
    found = coverage.gem_stat_pattern_entry("cannot_electrocute", "Voltaic Fulmination")
    assert found is not None and found.id == "gem-cov57-voltaic-fulmination-cannot-electrocute" and not found.replace
    assert coverage.gem_stat_pattern_entry("cannot_electrocute", "Spark") is None
