"""GEM4: Electromagnetism's Shocking Rift needs 3 Shocked enemies within 4 m, so against a boss preset its own stats move
nothing (ledger gem4-electromagnetism-rift-no-boss-trigger); the support's consume stats stay open (MECH-A's)."""

from __future__ import annotations

from builder.ledger import entries
from builder.ledger.coverage import gem_stat_pattern_entry
from builder.search import bosses

ENTRY = "gem4-electromagnetism-rift-no-boss-trigger"
RIFT = ("shocking_rift_damage_+%_final_per_pulse_performed", "shocking_rift_speed_+%_per_shock_consumed",
        "shocking_rift_hinder_ms", "shocking_rift_pulse_interval_ms", "shocking_rift_number_of_pulses",
        "number_of_shocking_rifts_allowed", "triggered_by_shocking_rift_support_%")
SUPPORT = ("consume_shock_of_X_enemies_to_trigger_shocking_rift",
           "try_consuming_shock_to_trigger_shocking_rift_in_radius_on_shocking_enemy")


def test_the_rift_stats_are_closed_with_no_lines():
    entry = next(e for e in entries() if e.id == ENTRY)
    assert entry.kind == "gem_stat" and not entry.replace
    for stat in RIFT:
        assert getattr(gem_stat_pattern_entry(stat, "Electromagnetism"), "id", None) == ENTRY, stat


def test_the_support_consume_stats_and_other_skills_stay_open():
    for stat in SUPPORT:
        found = gem_stat_pattern_entry(stat, "Electromagnetism")
        assert found is None or found.id != ENTRY, stat
    assert getattr(gem_stat_pattern_entry(RIFT[0], "Spark"), "id", None) != ENTRY


def test_no_preset_brings_three_shocked_enemies():
    # the entry's reading: at most the boss and one add at a time (one add every 25 s or more, killed as it comes)
    for boss in bosses.PRESETS.values():
        for kills in getattr(boss, "kills", ()) or ():
            assert kills.every >= 20.0, (boss.name, kills)
