"""COV56: gem stats a model on main holds as its own constant (each the same at every level), marked as read."""
from builder.ledger import coverage
from builder.search import conditions, models


def test_cov56_constant_readers_are_marked():
    assert models.GLORY_SKILLS["Spear of Solaris"][1]["shock"] == 1
    assert ("Spear of Solaris", "shock") in models.GLORY_FLAT_EVENTS
    assert models.GLORY_FROM_WARD["Skyfall"] == 1.0
    assert conditions.CALAMITY_REGEN == 5.0
    assert conditions.THUNDEROUS_LEAP_STAT == "thunderous_leap_%_chance_for_shocked_ground_when_detonating"
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("active_skill_hundred_times_generates_glory_per_shock", "Spear of Solaris",
             "model-read-cov56-spear-of-solaris-glory-shock"),
            ("active_skill_generates_%_of_ward_spent_as_glory", "Skyfall", "model-read-cov56-skyfall-ward-glory"),
            ("walking_calamity_base_rage_regeneration_per_minute", "Walking Calamity",
             "model-read-cov56-walking-calamity-rage-regen"),
            ("thunderous_leap_%_chance_for_shocked_ground_when_detonating", "Thunderous Leap",
             "model-read-cov56-thunderous-leap-ground")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), stat


def test_cov56_malice_critical_weakness_is_not_read_by_pob():
    """Malice's stat maps only to PoB's ApplyCriticalWeakness flag, which only shows an unticked Configuration box
    (coverage.CONFIG_BOX_ONLY): the audit keeps it open, not 'read by PoB'."""
    import pytest
    from builder.data import data_dir

    assert "ApplyCriticalWeakness" in coverage.CONFIG_BOX_ONLY
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    records = [r for r in coverage.audit()["records"]
               if r["category"] == "gem-stat" and r["line"] == "critical_chance_against_aura_apply_brittle_every_x_ms"]
    assert records and all(r["status"] == "none" and "Configuration box" in r["reason"] for r in records)
