"""Ledger entries: loading, matching and translating lines PoB ignores."""

from __future__ import annotations

import re

from builder.ledger import ACTIVE, Entry, entries, translate


def test_every_entry_loads_with_a_valid_pattern_and_status():
    found = entries()
    assert found, "ledger/entries should hold at least one entry"
    assert len({e.id for e in found}) == len(found)
    for entry in found:
        assert entry.status in ACTIVE
        assert entry.pattern is not None and entry.confidence


def test_translations_fill_numbers_and_arithmetic():
    entry = Entry("x", "x", "pob_gap", re.compile(r"^(\d+)% increased Thing per (\d+) Stuff$"),
                  ("{1}% increased Cast Speed", "{g1*2}% increased Damage", "+{g2/2} to Strength"), "theory", "proposed")
    assert entry.translate("10% increased Thing per 4 Stuff") == [
        "10% increased Cast Speed", "20% increased Damage", "+2 to Strength"]
    assert entry.translate("something else") is None


def test_no_effect_entries_translate_to_nothing():
    found = translate("30% increased Movement Speed")
    assert found is not None and found[1] == []
