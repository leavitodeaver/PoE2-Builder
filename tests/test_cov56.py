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
    # (COV57: MECH106's model reads it since TRAIN 142, so it is a model link now, still not "read by PoB")
    assert records and all(r["status"] == "model" for r in records)


def test_cov56_gem_stats_under_box_only_conditions_are_open():
    """Momentum's 40% more (Condition:Moved2m) and Pursuit's melee more (Condition:HitProjectileRecently) are set only by
    Configuration boxes the optimizer never ticks: open rows, not 'read by PoB'."""
    import pytest
    from builder.data import data_dir

    boxed = coverage.gem_stat_box_conditions()
    assert "support_momnetum_damage_+%_final_with_momentum" in boxed
    assert "support_advancing_assault_melee_damage_+%_final_if_projectile_attack_damage_hit_in_past_8_seconds" in boxed
    assert not any(s.startswith("spell_m") and "intelligence" in s for s in boxed)  # (Elemental Discharge: modelled boxes)
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    records = [r for r in coverage.audit()["records"] if r["category"] == "gem-stat" and r["line"] in boxed]
    # (COV57, TRAIN 144: Pursuit's are read by MECH106's pursuit_model since TRAIN 142, model links. GEM42, TRAIN 143's
    # first run: Momentum's 40% is read by GEM40's momentum_model too (model-gem40-momentum-walk-a-use: the build's own
    # walk during a use), so the pin went stale, not the entries. A box-only stat stays open unless a model entry claims
    # it.)
    from builder.ledger import entries
    modelled = {stat for e in entries() if e.raw.get("kind") == "model" for stat in e.raw.get("stats") or []}
    assert records and all(r["status"] == "none" or r["line"] in modelled for r in records)
    assert all(r["status"] == "none" for r in records if r["line"] not in modelled)
    assert all(r["status"] == "model" for r in records if "advancing_assault" in r["line"])


def test_cov56_box_flag_doesnt_make_a_line_live():
    """parse_audit.never_on: a box-only flag (BOX_FLAGS) beside a box-only condition doesn't count as unconditional."""
    from builder.ledger import parse_audit

    class Off:
        def status(self, need):
            return ("config-off", "a box")

    mods = [{"n": "EldritchEmpowerment", "t": "FLAG", "g": [], "v": True},
            {"n": "Damage", "t": "MORE", "g": ["Condition=EldritchEmpowermentSacrifice"], "v": 30}]
    assert parse_audit.never_on(mods, Off())
    mods[0]["n"] = "SomethingReal"
    assert not parse_audit.never_on(mods, Off())
    covered = coverage.stat_entries()
    assert coverage.model_entry("Spells for which this Sacrifice was fully made deal 30% more Damage").id == \
        "model-read-cov56-eldritch-empowerment-more"


def test_cov56_build_blind_spots_follow_the_audit():
    """A build's blind spots (coverage._build_index) list the box-only gem stats too, and an item-granted skill by name."""
    import pytest
    from builder.data import data_dir

    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    gems = coverage._build_index(str(data_dir()))["gems"]
    # (COV57: Malice's stat is read since TRAIN 142; GEM42: Momentum's left the blind spots once GEM40's momentum_model
    # read it. Any box-only gem stat no model entry claims is still a blind spot.)
    assert not any(s == "critical_chance_against_aura_apply_brittle_every_x_ms" for s, _ in gems.get("Malice", []))
    assert not any(s == "support_momnetum_damage_+%_final_with_momentum" for s, _ in gems.get("Momentum", []))
    from builder.ledger import entries
    modelled = {stat for e in entries() if e.raw.get("kind") == "model" for stat in e.raw.get("stats") or []}
    listed = {s for spots in gems.values() for s, _ in spots}
    assert not (listed & modelled & set(coverage.gem_stat_box_conditions()))
