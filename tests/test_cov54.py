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


def test_a_djinn_command_with_its_own_row_is_not_also_called_not_counted():
    """Build 18 (MECH104): Kelari's Deception showed 7.2M DPS and "not counted". The model's real label silences the
    note, a zero row included (Navira's Fracturing off Chilled Ground); a Command with no row keeps the note."""
    from types import SimpleNamespace

    from builder.search import models
    from builder.search.assumptions import Assumptions

    command = next(c for c in models.DJINN_COMMANDS if c.name == "Kelari's Deception")
    _, _, rows, _ = models.djinn_commands_model([(command, {1: {"AverageDamage": 1000.0}, 2: {"AverageDamage": 500.0}})], 0.0)
    assert rows and rows[0]["value"] > 0
    zero = {"label": "Navira's Fracturing (a djinn Command, added to the boss DPS)*", "value": 0.0}
    lines = [{"source": "passive:Kelari's Deception", "text": "Grants Skill: Kelari's Deception"},
             {"source": "passive:Navira's Fracturing", "text": "Grants Skill: Navira's Fracturing"},
             {"source": "passive:Ruzhan's Fury", "text": "Grants Skill: Ruzhan's Fury"}]
    fake = SimpleNamespace(build=SimpleNamespace(call=lambda name, **_: {"lines": lines}))
    notes = Assumptions.unbuilt_grants(fake, [], rows + [zero])
    assert [n["label"] for n in notes] == ["Ruzhan's Fury: not counted"]
