"""COV60: TRAIN 150's readers (MECHB65's and MECHC63's "for COV-A" lists), each checked against the landed code."""
from builder.ledger import coverage

TRAIN150_READ = {
    "model-read-cov60-trigger-flags": [
        ("triggered_by_cross_slash", "SupportWolfPouncePlayer"),
        ("triggered_by_hand_of_chayula", "SupportHandOfChayulaPlayer"),
        ("triggered_by_infernal_cry", "Combust")],
    "model-read-cov60-minion-and-fissure": [
        ("infernal_legion_minion_burning_effect_radius", "Infernal Legion I"),
        ("infernal_legion_minion_burning_effect_radius", "Catha's Brilliance"),
        ("entangle_vines_additional_number_of_vines_allowed_when_wet", "Entangle")],
    "model-read-cov60-romira-and-verisium": [
        ("loyalty_%_of_redirected_damage_recouped_as_life_for_owner", "Romira's Requital"),
        ("verisium_infusion_duration_+%", "Powered by Verisium")],
}


def test_cov60_train150_readers_are_marked():
    from builder.search import assumptions, models, recovery
    # (the readers each entry names)
    assert models.LEGION_RADIUS_STAT == "infernal_legion_minion_burning_effect_radius"
    assert models.FISSURE_VINES["Entangle"] == ("Vines", 3.0, 6.0)
    assert "Pounce" in models.MARK_CARRIERS and callable(models.mark_use) and callable(models.side_trigger_keep)
    assert recovery.LOYALTY_RECOUP_STAT == "loyalty_%_of_redirected_damage_recouped_as_life_for_owner"
    assert assumptions.VERISIUM_DURATION_STAT == "verisium_infusion_duration_+%"
    covered = coverage.stat_entries()
    for entry, pairs in TRAIN150_READ.items():
        for stat, skill in pairs:
            assert coverage.stat_covered(covered, stat, skill) == ("model", entry), (stat, skill)


def test_cov60_entangle_expiry_decided_and_the_unread_stay_open():
    entry = coverage.gem_stat_pattern_entry("entangle_duration_speed_+%_when_wet", "Entangle")
    assert entry is not None and entry.id == "gem-cov60-entangle-slower-expiry"
    covered = coverage.stat_entries()
    for stat, skill in (("seismic_cry_slam_skill_additional_aftershocks", "Seismic Cry"),  # (no reader at d374dcae)
                        ("wyvern_devour_empowered_flame_breath_rage_consumption_+%", "Flame Breath")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat
        assert coverage.gem_stat_pattern_entry(stat, skill) is None, stat
