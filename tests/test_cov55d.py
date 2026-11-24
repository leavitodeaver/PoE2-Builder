"""COV55 turn 4: rows a model on main already reads (Daze duration, The Natural Order, Pyromantic Pact's cost, Elemental Storm)."""
import re

from builder.ledger import coverage, translate
from builder.search import models, recovery


def test_cov55_already_read_rows_are_marked():
    daze = re.compile(models.DURATION_LINE.format(name="Daze"), re.I)
    for line, entry_id, check in (
            ("25% increased Daze Duration", "model-read-cov55-daze-duration", daze.search),
            ("Tame Beast can capture Unique Beasts", "model-read-cov55-natural-order-unique-beasts", None),
            ("Can have up to one Unique Tamed Beast summoned", "model-read-cov55-natural-order-unique-beasts", None),
            ("Gain Infernal Flame instead of spending Mana for Skill costs", "model-read-cov55-pyromantic-pact-cost",
             recovery.PACT_NO_MANA.search)):
        entry, out = translate(line)
        assert entry.id == entry_id and out == [] and entry.raw["model"], line
        assert check is None or check(line), line
    assert models.NATURAL_ORDER == "The Natural Order" and models.STORM_SKILL == "Elemental Storm"
    assert translate("Unique Tamed Beasts are Possessed by random Azmeri Spirits, changing every 20 seconds") is None
    assert translate("Lose all Infernal Flame on reaching maximum Infernal Flame") is None
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "is_triggerable_elemental_storm", "Elemental Storm") == (
        "model", "model-read-cov55-elemental-storm-trigger")
