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
