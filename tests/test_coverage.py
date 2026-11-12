"""The coverage audit (builder/ledger/coverage.py): what PoB's calculations read, and statuses."""

import pytest

from builder.ledger import coverage

pytestmark = pytest.mark.skipif(not coverage.POB_SRC.exists(), reason="needs the pinned PoB checkout")


def test_names_calculations_read():
    reads = coverage.calc_reads()
    for name in ("Damage", "Life", "CritChance", "MovementSpeed", "PhysicalMin", "FireDamageConvertToCold",
                 "EvasionAndEnergyShield", "StrRequirement"):  # the last two only through Item.lua's calcLocal
        assert coverage.name_read(name, reads), name
    assert coverage.name_read("Condition:LedgerWitchbane", reads) or coverage.name_read("Multiplier:PowerCharge", reads)
    assert coverage.name_read("EnemyModifier>FireResist", reads)
    assert coverage.name_read("SkillData:duration", reads)
    # Increased Presence Area: PoB parses it, and no calculation asks for it
    assert not coverage.name_read("PresenceArea", reads)


def test_a_line_is_a_noop_only_when_nothing_it_writes_is_read():
    reads = coverage.calc_reads()
    assert coverage.unread(["Str", "Dex", "StrDex"], reads) == []  # StrDex is only a marker
    assert coverage.unread(["PresenceArea"], reads) == ["PresenceArea"]
    assert coverage.unread([], reads) == []


def test_ignored_line_statuses():
    assert coverage.ignored_line_status("10% increased Movement Speed while Sprinting", True)[0] == "ledger"
    assert coverage.ignored_line_status("Gain Elemental Archon after spending 100% of your Maximum Mana", True)[0] == "model"
    status, entry, reason = coverage.ignored_line_status("A line no entry covers at all", True)
    assert (status, entry) == ("none", None)


def test_mechanic_and_template():
    assert coverage.mechanic("20% increased Spell Damage") == "damage"
    assert coverage.mechanic("Take 10% less Damage from Hits") == "defence"
    assert coverage.mechanic("Recover 2% of maximum Life on Kill") == "recovery"
    assert coverage.mechanic("10% increased Light Radius") == "flavour"
    assert coverage.stat_mechanic("quality_display_warding_steel_is_gem") == "flavour"
    # flavour only when recognised as such; a line no pattern knows stays in the open list (COV1, 12 Nov 2026:
    # most of the old fallback "flavour" moved builds)
    assert coverage.mechanic("Corrupted") == "flavour"
    assert coverage.mechanic("Your Hits are Crushing Blows") != "flavour"
    assert coverage.mechanic("Grants Skill: Ruzhan's Reckoning") != "flavour"
    assert coverage.mechanic("Fork an additional time") != "flavour"
    assert coverage.mechanic("A line no pattern knows") == "unclassified"
    assert coverage.stat_mechanic("ground_oil_art_variation") == "flavour"
    assert coverage.stat_mechanic("ancestral_cry_non_skill_physical_to_fire_convert_%") == "damage"
    assert coverage.stat_mechanic("lose_%_of_life_es_and_mana_when_skill_is_used") != "flavour"
    assert coverage.stat_mechanic("some_stat_nobody_knows") == "unclassified"
    assert coverage.template("Adds (8-12) to 20 Fire Damage") == "Adds # to # Fire Damage"


def test_ranking_prefers_strong_and_shared_sources():
    def record(source, kind, weight, text="10% more Damage", mechanic="damage"):
        return {"category": "passive", "source": source, "source_kind": kind, "line": text, "status": "none",
                "reason": "", "mechanic": mechanic, "weight": weight, "template": coverage.template(text), "group": source}

    ranked = coverage.rank([
        record("Small", "small", 0.5, "1% more Damage per thing"),
        record("Keystone", "keystone", 6.0, "Hits Ignore something"),
        record("A", "notable", 3.0), record("B", "notable", 3.0),
        record("Flavour", "keystone", 6.0, "10% increased Light Radius", "flavour"),
    ])
    order = [g["template"] for g in ranked]
    assert order[0] == "Hits Ignore something"  # a keystone first
    assert order[1] == "#% more Damage" and ranked[1]["sources"] == 2  # then a line two notables share
    assert order.index("#% more Damage per thing") < order.index("#% increased Light Radius")  # flavour last


def test_build_blind_spots_list_noops_and_unread_gem_stats():
    from builder.data import data_dir

    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json; run `uv run python -m builder.ledger.coverage --refresh`")
    found = coverage.build_blind_spots(
        [{"source": "passive:Presence Area", "text": "10% increased Presence Area of Effect"},
         {"source": "item:Ring 1", "text": "+30 to maximum Life"}],
        ["Mana Tempest", "Ember Fusillade"])
    kinds = {(b["kind"], b["line"]) for b in found}
    assert ("no-op", "10% increased Presence Area of Effect") in kinds
    assert ("gem stat", "ember_fusillade_damage_+%_final_per_ember_fired") in kinds
    assert not any(b["source"] == "gem: Mana Tempest" for b in found)  # its stats are counted now
    assert not any("maximum Life" in b["line"] for b in found)


def test_parse_checked_entries_and_item_lines_the_search_does_not_correct():
    from builder.ledger import checked_parse

    assert checked_parse("Defend with 200% of Armour").id == "parse-defend-with-armour"
    # an entry limited to one item doesn't close the same text elsewhere
    assert checked_parse("(100-160)% increased Chaos Damage", "The Unborn Lich")
    assert checked_parse("(100-160)% increased Chaos Damage", "Some Other Staff") is None
    # a ledger translation of an affix line isn't applied to items by the search: still not counted
    assert coverage.not_applied("ledger", "x", "translated")[0] == "none"
    assert coverage.not_applied("ledger", "x", "no effect on what's calculated")[0] == "ledger"


def test_the_tools_own_engine_reads_count_as_reads():
    # the charm uptime (builder/search/flasks.py) reads CharmChargesGained through the bridge's inc("...") helper
    assert coverage.name_read("CharmChargesGained", coverage.calc_reads())
