"""COV54: the four flat-rate Aggravate entries are retired; the build's own Aggravate model takes their lines."""
import pytest

from builder import ledger

RETIRED = ("aggravate-bleed-on-hit", "aggravate-bleed-on-crit", "aggravate-bleed-always-on-crit", "aggravate-bleed-empowered")


@pytest.mark.parametrize("line,model", [
    ("40% chance to Aggravate Bleeding on Hit", "aggravate-bleed-on-attack-hit-model"),
    ("8% chance to Aggravate Bleeding on targets you Hit with Attacks", "aggravate-bleed-on-attack-hit-model"),
    ("(10-20)% chance to Aggravate Bleeding on Hit", "aggravate-bleed-on-attack-hit-model"),
    ("25% chance to Aggravate Bleeding on targets you Critically Hit with Attacks", "aggravate-bleed-on-attack-crit-model"),
    ("Aggravate Bleeding on targets you Critically Hit with Attacks", "aggravate-bleed-on-attack-crit-model"),
    ("20% chance to Aggravate Bleeding on targets you Hit with Empowered Attacks", "aggravate-bleed-empowered-model"),
])
def test_the_aggravate_model_takes_the_lines_of_the_retired_flat_rate_entries(line, model):
    entry, lines = ledger.translate(line)
    assert entry.id == model and lines == []  # the model counts it; the ledger adds no line of its own
    assert ledger.model_entry(line).id == model


def test_the_flat_rate_entries_can_no_longer_apply():
    assert not {e.id for e in ledger.entries()} & set(RETIRED)
