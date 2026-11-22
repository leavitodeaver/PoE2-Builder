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
