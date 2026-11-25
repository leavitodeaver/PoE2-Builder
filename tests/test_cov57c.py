"""COV57 turn 3: TRAIN 142's readers (Malice, Pursuit) marked as read."""
from builder.ledger import coverage
from builder.search import models


def test_cov57c_train142_readers_are_marked():
    assert models.MALICE_STAT == "critical_chance_against_aura_apply_brittle_every_x_ms"
    assert set(models.PURSUIT_STATS.values()) == {8.0, 2.0}
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, models.MALICE_STAT, "Malice") == (
        "model", "model-read-cov57c-malice-critical-weakness")
    for stat in models.PURSUIT_STATS:
        for skill in ("Pursuit I", "Pursuit II", "Pursuit III"):
            assert coverage.stat_covered(covered, stat, skill) == ("model", "model-read-cov57c-pursuit-melee-more"), stat
    # Momentum's box-only stat stays open (no reader)
    assert coverage.stat_covered(covered, "support_momnetum_damage_+%_final_with_momentum", "Momentum") is None
