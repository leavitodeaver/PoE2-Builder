"""COV57 turn 5: TRAIN 144's readers marked as read."""
from builder.ledger import coverage
from builder.search import assumptions, models


def test_cov57d_train144_readers_are_marked():
    assert assumptions.EXTRA_REMNANT_STAT == "chance_%_to_create_additional_remnant"
    assert (models.CW_ON_HIT, models.CW_KELARI) == ("apply_X_stacks_of_critical_weakness_on_hit", 1.0)
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "chance_%_to_create_additional_remnant", "Harmonic Remnants II") == (
        "model", "model-read-cov57d-harmonic-remnants-extra")
    assert coverage.stat_covered(covered, models.CW_ON_HIT, "Kelari, the Tainted Sands") == (
        "model", "model-read-cov57d-kelari-critical-weakness-on-hit")
    # Lingering Illusion's own chance stays open (read at its level-20 value only)
    assert coverage.stat_covered(covered, "chance_%_to_create_additional_remnant", "") is None
