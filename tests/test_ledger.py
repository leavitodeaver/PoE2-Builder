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
        assert entry.confidence
        assert entry.pattern is not None or entry.kind == "condition"


def test_translations_fill_numbers_and_arithmetic():
    entry = Entry("x", "x", "pob_gap", re.compile(r"^(\d+)% increased Thing per (\d+) Stuff$"),
                  ("{1}% increased Cast Speed", "{g1*2}% increased Damage", "+{g2/2} to Strength"), "theory", "proposed")
    assert entry.translate("10% increased Thing per 4 Stuff") == [
        "10% increased Cast Speed", "20% increased Damage", "+2 to Strength"]
    assert entry.translate("something else") is None


def test_no_effect_entries_translate_to_nothing():
    found = translate("30% increased Movement Speed")
    assert found is not None and found[1] == []


def test_rolled_ranges_stay_ranges():
    entry = Entry("x", "x", "pob_gap", re.compile(r"^(\(\d+-\d+\)|\d+)% chance to Thing$"),
                  ("{g1*2}% more Damage",), "theory", "proposed")
    assert entry.translate("(15-30)% chance to Thing") == ["(30-60)% more Damage"]
    assert entry.translate("22% chance to Thing") == ["44% more Damage"]


def test_conditional_corrections_read_as_conditions():
    from builder.ledger import readable

    found = translate("Deal up to 40% more Damage to Enemies based on their missing Concentration")
    assert found is not None and found[0].conditional
    assert [readable(line) for line in found[1]] == ["36% more Damage (with Witchbane)"]
    assert readable("flag:Witchbane") == "enables Witchbane"


def test_conditions_and_wrong_lines_load():
    from builder.ledger import conditions, wrong

    assert {"GlorySkill", "SpellEcho", "JaggedGround"} <= {c["id"] for c in conditions()}
    fixed = wrong("Take 40% less Damage over time")
    assert fixed is not None and fixed[1] == "20% less Damage over Time taken"
