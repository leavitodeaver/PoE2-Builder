"""MECH108: Biting Frost II's Chill on each Freeze the main skill consumes (conditions.consume_chill_share)."""
from builder.search import conditions, models


def _stats(**extra):
    # a non-Cold main skill (no Chill of its own) whose Freeze comes from buildup; Biting Frost II consumes it
    stats = {"Speed": 2.0, "HitChance": 100.0, "FreezeBuildupAvg": 10.0, "ChillChanceOnHit": 0.0,
             "_consumes": {"Freeze": "a support of it"}, conditions.FREEZE_CONSUME_CHILL: True}
    stats.update(extra)
    return stats


def test_consumed_freezes_chill_the_boss_at_their_period():
    stats = _stats()
    frozen, _ = models.immobilised_share(stats, "Freeze")
    assert frozen > 0
    last = min(models.immobilise_duration(stats, "Freeze"), 1 / 2.0)
    period = last / frozen
    share, how = conditions.consume_chill_share(stats)
    assert abs(share - min(1.0, conditions.CHILL_SECONDS / period)) < 1e-9
    assert 0 < share <= 1 and "Biting Frost II" in how and "UNVERIFIED" in how
    assert abs(conditions.chilled_share(stats) - share) < 1e-9  # (no other Chill source here)


def test_the_build_chill_duration_is_used():
    base = conditions.consume_chill_share(_stats())[0]
    shorter = conditions.consume_chill_share(_stats(ChillDuration=2.0))[0]
    assert shorter < base or base == 1.0


def test_no_stat_or_no_consumed_freeze_gives_nothing():
    assert conditions.consume_chill_share(_stats(**{conditions.FREEZE_CONSUME_CHILL: False}))[0] == 0.0
    assert conditions.consume_chill_share(_stats(_consumes={}))[0] == 0.0
    assert conditions.chilled_share(_stats(**{conditions.FREEZE_CONSUME_CHILL: False})) == 0.0


def test_no_freeze_buildup_gives_nothing_with_a_note():
    share, how = conditions.consume_chill_share(_stats(FreezeBuildupAvg=0.0))
    assert share == 0.0 and "nothing Freezes it" in how


def test_it_adds_to_the_main_skill_chill_as_independent():
    stats = _stats()
    own = 0.4
    consumed = conditions.consume_chill_share(stats)[0]
    real = conditions.chill_share
    try:
        conditions.chill_share = lambda s: own
        assert abs(conditions.chilled_share(stats) - (1 - (1 - own) * (1 - consumed))) < 1e-9
    finally:
        conditions.chill_share = real
