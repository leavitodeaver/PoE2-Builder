"""MECH99: Flame Breath's Compounding Ignite gets its cooldown gap (was "recast at once")."""
from builder.search import conditions


def test_flame_breath_cycle_reads_the_cooldown_gap():
    # No hit cut set: the channel is the free window, the rest of the 10 s cooldown the gap.
    channel, gap, how = conditions.flame_breath_cycle(4.0, 10.0, 7.26, None)
    assert channel == 4.0 and abs(gap - 6.0) < 1e-9 and "no hit cut" in how
    # MECH-B's cut x0.40 of the cycle: a 4 s channel in 10 s.
    channel, gap, _ = conditions.flame_breath_cycle(4.0, 10.0, 7.26, 0.4)
    assert abs(channel - 4.0) < 1e-9 and abs(gap - 6.0) < 1e-9
    # Rage paying more: a longer channel, a shorter gap.
    channel, gap, _ = conditions.flame_breath_cycle(4.0, 10.0, 7.26, 0.7)
    assert abs(channel - 7.0) < 1e-9 and abs(gap - 3.0) < 1e-9
    # No cooldown shown: the 10 s fallback; a cooldown under wind-up + window: the cycle is that.
    assert abs(sum(conditions.flame_breath_cycle(4.0, 0.0, 7.26, None)[:2]) - 10.0) < 1e-9
    channel, gap, _ = conditions.flame_breath_cycle(4.0, 2.0, 2.0, None)
    assert channel == 4.0 and abs(gap - 2.0) < 1e-9  # (4 attack times at 2 a second: 2 s wind-up)


def test_the_gap_lowers_flame_breaths_compounding():
    """Back-to-back channels let the previous channel's strongest Ignite run on into the next: an OVERCOUNT."""
    rate, duration = 7.26 * 0.3, 4.0
    back_to_back, _ = conditions.compounding_factor(rate, 4.0, 0.0, duration)
    with_gap, n = conditions.compounding_factor(rate, 4.0, 6.0, duration)
    assert n > 1 and 1.0 < with_gap < back_to_back


def test_flameblasts_release_rate_is_its_hit_cut_and_its_stages_are_not():
    """MECH99 (the manager 18:15): flameblast_model's DPS MORE is one release a cycle against PoB's Speed, a cut in hits
    the ailment readers must see (assumptions.hit_cut); its stages (Multiplier:FlameblastStage) scale each hit only."""
    from types import SimpleNamespace

    from builder.search import models
    from builder.search.assumptions import hit_cut
    from builder.search.models import Skill

    level = {"charged_blast_spell_damage_+%_final_per_stack": 75, "flameblast_maximum_stages": 10,
             "channel_skill_end_animation_duration_override_ms": 600, "base_minimum_channel_time_ms": 490}
    main = Skill("Flameblast", 1, spell=True, cooldown=10.0, level_stats=level)
    mods, _ = models.flameblast_model(main, {"CastRate": 4.8, "Speed": 4.8, "Cooldown": 0.1})
    build = SimpleNamespace(main_hit_cut=None)
    hit_cut(build, mods, main.name)
    assert abs(build.main_hit_cut - 1 / (10 / 4.8 + 0.7) / 4.8) < 1e-4  # (one release a 2.78 s cycle vs 4.8 a second)
    other = SimpleNamespace(main_hit_cut=None)
    hit_cut(other, mods, "Spark")
    assert other.main_hit_cut is None


def test_an_untagged_model_reads_exactly_as_before_the_per_use_split():
    """MECH99 (the manager 20:50): without a HIT_CUT_PER_USE mod nothing moves: no hits-a-use factor, the same time
    cut, the same at_main_share for hit and uses readers."""
    from types import SimpleNamespace

    from builder.search import models
    from builder.search.assumptions import hit_cut

    plain = [models.raw_mod("DPS", "MORE", -60, "Flame Breath")]
    assert models.hits_per_use_of(plain, None, "Flame Breath") is None
    build = SimpleNamespace(main_hit_cut=None, main_hits_per_use=None)
    hit_cut(build, plain, "Flame Breath")
    assert abs(build.main_hit_cut - 0.4) < 1e-12 and build.main_hits_per_use is None
    stats = {"Speed": 5.0, "HitChance": 80, "_main_share": 0.5}
    for per_use in (True, False):
        assert models.at_main_share(stats, per_use=per_use) == {"Speed": 2.5, "HitChance": 80, "_main_share": None,
                                                               "_hits_per_use": None}
    assert models.at_main_share({"Speed": 5.0}) == {"Speed": 5.0}  # (no share, no factor: the same dict)
    assert models.at_main_share({"Speed": 5.0, "_main_share": None, "_hits_per_use": None})["Speed"] == 5.0


def test_a_per_use_cut_reaches_the_hit_readers_only():
    from types import SimpleNamespace

    from builder.search import models
    from builder.search.assumptions import hit_cut

    bolts = [dict(models.raw_mod("DPS", "MORE", -72, "Hailstorm Rounds"), **{models.HIT_CUT_PER_USE: 0.28})]
    build = SimpleNamespace(main_hit_cut=None, main_hits_per_use=None)
    hit_cut(build, bolts, "Hailstorm Rounds")
    assert build.main_hit_cut is None and abs(build.main_hits_per_use - 0.28) < 1e-12  # (not a time cut)
    stats = {"Speed": 5.0, "HitChance": 100, "_main_share": 0.5, "_hits_per_use": build.main_hits_per_use}
    assert abs(models.at_main_share(stats)["Speed"] - 5.0 * 0.5 * 0.28) < 1e-12  # (hits: time x hits a use)
    assert abs(models.at_main_share(stats, per_use=False)["Speed"] - 2.5) < 1e-12  # (uses: time only)
    twice = models.at_main_share(models.at_main_share(stats))
    assert abs(twice["Speed"] - 0.7) < 1e-12  # (applied once)
    # a MIXED mod: its time part ("hit_cut") and its hits a use, each to its own factor
    mixed = [dict(models.raw_mod("DPS", "MORE", 50, "Volcano"), hit_cut=0.5, **{models.HIT_CUT_PER_USE: 0.6})]
    both = SimpleNamespace(main_hit_cut=None, main_hits_per_use=None)
    hit_cut(both, mixed, "Volcano")
    assert both.main_hit_cut == 0.5 and both.main_hits_per_use == 0.6
    # a raise (factor > 1) stays capped at 1 for now (the manager's step 3)
    assert models.per_use_factor({"_hits_per_use": 2.5}) == models.HITS_PER_USE_MAX == 1.0
