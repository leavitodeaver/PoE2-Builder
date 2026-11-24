"""COV55: the audit rows marked as read after Trains 131-132, each tied to the reader's own pattern or stat."""
from builder.ledger import coverage, translate
from builder.search import assumptions, models


def test_cov55_lines_are_read_by_their_models():
    final_echo = ("Final Echo of Cascadable Spells also Cascades to either side of the targeted Area "
                  "along a random axis")
    for line, entry_id, pattern in (
            ("Tempest Bells are destroyed after an additional 4 Hits", "model-read-cov55-tempest-bell-extra-hits",
             models.BELL_EXTRA_HITS.match),
            ("Companions in your Presence have Onslaught while you are Shapeshifted", "model-read-cov55-rallying-form",
             models.RALLYING_FORM.match),
            (final_echo, "model-read-cov55-mystic-avalanche", models.MYSTIC_AVALANCHE.search)):
        entry, out = translate(line)
        assert entry.id == entry_id and out == [] and entry.raw["model"], line
        assert pattern(line), line


def test_cov55_stats_are_read_and_scoped_to_their_skills():
    assert (models.MOV_SHARE_STAT, models.MOV_IMMUNE_STAT) == ("time_rip_damage_share_%", "rip_from_time_immunity_duration_ms")
    assert (models.BELL_LIMIT_STAT, models.BELL_LIMIT_BASE, models.BELL_WAVE_EVERY) == ("bell_hit_limit", 10.0, 0.3)
    assert callable(assumptions.Assumptions.limit_and_duration) and callable(assumptions.Assumptions.earthshatter_limit)
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("bell_hit_limit", "Tempest Bell", "model-read-cov55-tempest-bell-constants"),
            ("bell_shockwave_cooldown_ms", "Tempest Bell", "model-read-cov55-tempest-bell-constants"),
            ("time_rip_damage_share_%", "Moment of Vulnerability", "model-read-cov55-moment-of-vulnerability"),
            ("rip_from_time_immunity_duration_ms", "Moment of Vulnerability", "model-read-cov55-moment-of-vulnerability"),
            ("base_number_of_shield_walls_allowed", "Shield Wall", "model-read-cov55-gem38-limits"),
            ("base_number_of_frozen_locus_allowed", "Frozen Locus", "model-read-cov55-gem38-limits"),
            ("spike_slam_max_spikes", "Earthshatter", "model-read-cov55-gem38-limits"),
            ("cooldown_recovery_modifiers_also_apply_to_repeat_interval", "Align Fate", "model-read-cov55-align-fate-recovery")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), stat
    # (COV57: Hollow Focus's bell_hit_limit and Tempest Bell's ramp are read since TRAIN 140 (MECHB61): test_cov57)
    for stat, skill in (("bell_shockwave_cooldown_ms", "Hollow Focus"),
                        ("cooldown_recovery_modifiers_also_apply_to_repeat_interval", "Ghost Dance"),
                        ("cooldown_recovery_modifiers_also_apply_to_repeat_interval", "Time of Need"),
                        ("base_number_of_tempest_bells_allowed", "Tempest Bell")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat
