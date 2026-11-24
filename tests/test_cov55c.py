"""COV55 turn 3: gem stats a model on main holds as its own constant or flag (each the same at every level)."""
from builder.ledger import coverage
from builder.search import assumptions, conditions, models


def test_cov55_constant_readers_are_marked():
    assert assumptions.WAR_BANNER_SPEED == 25.0 and assumptions.BLAZING_SECONDS == 5.0
    assert conditions.HER_DECLARATION_SECONDS == 4.0 and models.CHAYULA_MORE == 20.0
    assert conditions.CHARGED_MARK == "Charged Mark"
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("base_skill_buff_banner_attack_speed_+%_to_apply", "War Banner", "model-read-cov55-war-banner-speed"),
            ("support_blazing_crits_base_duration_ms", "Blazing Critical", "model-read-cov55-blazing-critical-duration"),
            ("support_mothers_declaration_intimidate_enemies_when_they_enter_your_presence_base_duration_ms", "Her Declaration",
             "model-read-cov55-her-declaration-duration"),
            ("archon_of_chayula_physical_and_chaos_damage_+%_final", "Archon of Chayula", "model-read-cov55-archon-of-chayula-more"),
            ("gain_archon_elemental_when_you_ignite_enemy_chance_%", "Arbiter's Ignition", "model-read-cov55-arbiters-ignition-chance"),
            ("support_trigger_shocked_ground_on_mark_activate", "Charged Mark", "model-read-cov55-charged-mark-ground")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), stat
