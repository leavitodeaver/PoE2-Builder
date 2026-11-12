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
        assert entry.pattern is not None or entry.kind == "condition" or (entry.kind == "model" and entry.raw.get("model"))


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

    # (No Mercy's flat "if:Witchbane 36% more Damage" is retired: passive_models.no_mercy_model works it out, COVB4)
    found = translate("Deal up to 40% more Damage to Enemies based on their missing Concentration")
    assert found is not None and found[0].conditional
    assert [readable(line) for line in found[1]] == ["enables NoMercy"]
    assert readable("if:Witchbane 36% more Damage") == "36% more Damage (with Witchbane)"
    assert readable("flag:Witchbane") == "enables Witchbane"


def test_conditions_and_wrong_lines_load():
    from builder.ledger import conditions, wrong

    assert {"GlorySkill", "SpellEcho", "JaggedGround"} <= {c["id"] for c in conditions()}
    fixed = wrong("Take 40% less Damage over time")
    assert fixed is not None and fixed[1] == "20% less Damage over Time taken"


def test_an_entry_matches_a_rolled_range_at_both_ends():
    """A line as the audit sees it, "(16-23)% chance ...", against a pattern with a plain-number group: matched at the
    range's two ends and translated as a range (COV5; before, 17 entries missed every ranged line)."""
    from builder.ledger import model_entry, translate

    entry, lines = translate("(16-23)% chance for Mace Slam Skills you use yourself to cause an additional Aftershock")
    assert entry.id == "aftershock-chance-mace"
    assert lines == ["Slam Skills have (16-23)% more DPS while wielding a Mace"]
    assert translate("16% chance for Mace Slam Skills you use yourself to cause an additional Aftershock")[1] == \
        ["Slam Skills have 16% more DPS while wielding a Mace"]
    assert model_entry("Projectile Attacks have a (10-18)% chance to fire two additional Projectiles while moving") is None
    assert translate("Projectile Attacks have a (10-18)% chance to fire two additional Projectiles while moving")[0].id \
        == "no-effect-run-and-gun"


def test_the_audit_counts_debuff_lines_the_uptime_models_rate():
    """Maim / Blind / Intimidate lines on your hits, crits or Blocks feed conditions.debuff_uptime; wordings it rates 0
    (a kill, a Stun on you) or that aren't sources stay open (COV5)."""
    from builder.ledger.coverage import resolver_condition

    assert resolver_condition("(10-15)% chance to Maim on Hit").var == "conditionEnemyMaimed"
    assert resolver_condition("Intimidate Enemies on Block for 8 seconds").var == "conditionEnemyIntimidated"
    assert resolver_condition("Break Armour equal to (2-4)% of Physical Damage dealt").var == "conditionEnemyArmourBroken"
    for line in ("Blind does not affect your Light Radius", "Blind Enemies when they Stun you",
                 "Socketed Gems are supported by Level 10 Blind"):
        assert resolver_condition(line) is None, line
