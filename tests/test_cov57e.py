"""COV57 turn 6: TRAIN 145's readers marked as read."""
from builder.ledger import coverage
from builder.search import conditions, recovery


def test_cov57e_train145_readers_are_marked():
    assert conditions.ENRAGED_WARCRY_RAGE == {"Enraged Warcry I": 20.0, "Enraged Warcry II": 15.0}
    assert recovery.GHOST_STOCK == 3
    covered = coverage.stat_entries()
    for skill in ("Enraged Warcry I", "Enraged Warcry II"):
        assert coverage.stat_covered(covered, "warcry_bypass_cooldown_spending_X_rage_instead_of_endurance_charge",
                                     skill) == ("model", "model-read-cov57e-enraged-warcry-rage-bypass")
    assert coverage.stat_covered(covered, "ghost_dance_max_stacks", "Ghost Dance") == (
        "model", "model-read-cov57e-ghost-dance-stock")
    assert sum(1 for s, e in covered.items() if s == "ghost_dance_max_stacks" for _ in e) == 1  # one entry per stat
