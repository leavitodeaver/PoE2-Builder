"""COV58 turn 1: Twister's hit interval, Entangle's vine cap and Raise Shield's bash reach marked as read (readers found in the blind probe's ranking)."""
from builder.ledger import coverage
from builder.search import models, recovery


def test_cov58_readers_are_marked():
    assert recovery.TWISTER_INTERVAL == 0.66  # (twister_hit_interval_ms 660 at every level)
    assert models.FISSURE_VINES["Entangle"][1] == 3.0  # (3 vines / 1 s each)
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "twister_hit_interval_ms", "Twister") == (
        "model", "model-read-cov58-twister-hit-interval")
    for stat in ("entangle_vines_maximum_number_of_vines_allowed", "entangle_vine_duration_ms"):
        assert coverage.stat_covered(covered, stat, "Entangle") == ("model", "model-read-cov58-entangle-vine-cap")
    assert models.SHIELD_BASH_RANGE == 22.0  # (0.8 m unarmed + Raise Shield's melee_strike_bonus_attack_distance 14)
    assert coverage.stat_covered(covered, "melee_strike_bonus_attack_distance", "Raise Shield") == (
        "model", "model-read-cov58-raise-shield-bash-reach")
    assert coverage.stat_covered(covered, "melee_strike_bonus_attack_distance", "Parry") is None  # (not read for Parry)
    for stat in ("twister_hit_interval_ms", "entangle_vines_maximum_number_of_vines_allowed", "entangle_vine_duration_ms",
                 "melee_strike_bonus_attack_distance"):
        assert sum(len(v) for s, v in covered.items() if s == stat) == 1  # one entry per stat
    for stat in ("entangle_apply_vines_every_X_ms", "entangle_duration_speed_+%_when_wet"):  # (left open on purpose)
        assert coverage.stat_covered(covered, stat, "Entangle") is None
