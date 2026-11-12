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


def test_a_name_only_the_bridge_helpers_read_counts_for_their_type_only():
    """bridge.lua's inc("CharmChargesGained") sums INC: Ritualist's "20% more Charm Charges gained" (a MORE) is applied
    by nothing, so the audit lists it; the INC lines stay read (COV1's no-op list)."""
    reads = coverage.calc_reads()
    assert reads["bridge_types"].get("CharmChargesGained") == {"INC"}
    more = {"names": ["CharmChargesGained"], "mods": [{"n": "CharmChargesGained", "t": "MORE"}]}
    assert coverage.unread(coverage.typed_names(more, reads), reads) == ["CharmChargesGained (MORE)"]
    inc = {"names": ["CharmChargesGained"], "mods": [{"n": "CharmChargesGained", "t": "INC"}]}
    assert coverage.unread(coverage.typed_names(inc, reads), reads) == []
    assert coverage.typed_names({"names": ["Life"], "mods": [{"n": "Life", "t": "MORE"}]}, reads) == ["Life"]


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
    # the search applies ledger translations to rare, magic and normal items' lines and to runes (COV2,
    # builder/ledger/items.py), not to Bonded rune lines, pools it never rolls or Timeless passives
    assert coverage.not_applied("ledger", "x", "translated")[0] == "ledger"
    assert coverage.not_applied("ledger", "x", "translated", "Runes")[0] == "ledger"
    assert coverage.not_applied("ledger", "x", "translated", "Runes", bonded=True)[0] == "none"
    assert coverage.not_applied("ledger", "x", "translated", "Desecrated")[0] == "none"
    assert coverage.not_applied("ledger", "x", "translated", "legion")[0] == "none"
    assert coverage.not_applied("ledger", "x", "translated", "Exclusive", line="Take 4% less Damage")[0] == "none"
    assert coverage.not_applied("ledger", "x", "translated", "Exclusive", unique_templates=frozenset({"Take #% less Damage"}),
                                line="Take 4% less Damage")[0] == "ledger"
    assert coverage.not_applied("ledger", "x", "no effect on what's calculated", "legion")[0] == "ledger"


def test_ledger_translations_reach_rune_and_affix_lines():
    """A rune's line PoB can't parse gets the ledger's stand-in, summed over runes as PoB sums them; a rune's line a
    `pob_wrong` entry corrects is switched off and replaced; re-socketing starts over; a unique's own lines are left to
    corrected_raw."""
    from builder.ledger.items import ADDED, SWITCHED_OFF, _bases, corrected_item, unparsed_templates
    from builder.search.rares import with_runes

    if not unparsed_templates():
        pytest.skip("no coverage.json in the data dump")
    mace = next(n for n, b in _bases().items() if b["type"] == "Two Hand Mace" and not b.get("hidden"))
    rare = f"Rarity: Rare\nOptimizer Weapon 1\n{mace}\nItem Level: 82\nImplicits: 0\n+10 to Strength"
    two = with_runes(rare, ["Ancient Rune of the Titan"] * 2)
    assert two.splitlines()[-1] == ADDED + "Slam Skills deal 20% more Damage"  # 10% + 10% chance, entry aftershock-chance
    one = with_runes(two, ["Ancient Rune of the Titan"])
    assert [l for l in one.splitlines() if l.startswith(ADDED)] == [ADDED + "Slam Skills deal 10% more Damage"]
    assert with_runes(one, []) == rare and corrected_item(one) == one
    helmet = next(n for n, b in _bases().items() if b["type"] == "Helmet" and not b.get("hidden"))
    unique = f"Rarity: Unique\nCrown\n{helmet}\nImplicits: 1\n+10 to Armour\nTake 20% less Damage from Hits"
    keeper = with_runes(unique, ["Legacy of Keeper of the Arc"]).splitlines()
    assert keeper[keeper.index("Implicits: 2") + 1] == SWITCHED_OFF + "Take 20% less Damage over time"
    assert keeper[-2:] == [ADDED + "10% less Damage taken from Hits", ADDED + "10% less Damage over Time taken"]
    assert keeper.count(ADDED + "10% less Damage taken from Hits") == 1  # the unique's own line: corrected_raw's job
    assert with_runes("\n".join(keeper), []) == unique


def test_the_tools_own_engine_reads_count_as_reads():
    # the charm uptime (builder/search/flasks.py) reads CharmChargesGained through the bridge's inc("...") helper
    assert coverage.name_read("CharmChargesGained", coverage.calc_reads())


def test_model_stats_entries_cover_a_stat_for_their_skills_only():
    """`model` entries with `stats` (ledger/entries/gem-stats-energy.yaml): the Energy model reads energy_generated_+% for the
    meta skills it prices, not for Feral Invocation (models.NOT_MODELED_YET), so that one stays open."""
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, "energy_generated_+%", "Cast on Critical") == ("model", "model-stats-energy-gain")
    assert coverage.stat_covered(covered, "energy_generated_+%", "Feral Invocation") is None
    assert coverage.stat_covered(covered, "generic_ongoing_trigger_1_maximum_energy_per_Xms_total_cast_time",
                                 "SupportMetaCastOnCritPlayer")[0] == "model"
    assert coverage.stat_covered(covered, "generic_ongoing_trigger_1_maximum_energy_per_Xms_total_cast_time",
                                 "SupportReapersInvocationPlayer") is None
    from builder.search import models

    named = {n for e in ("model-stats-energy-gain", "model-stats-energy-sources")
             for n in next(x for x in __import__("builder.ledger", fromlist=["entries"]).entries() if x.id == e).raw["skill"]}
    assert named - {"Boundless Energy I", "Boundless Energy II"} <= set(models.META_GEMS)  # only skills the model prices


def test_a_noop_line_is_closed_only_by_what_really_applies_to_it():
    """COVD1: the audit added every no-op line (parsed, nothing reads it) as "none", so no ledger route could close one.
    A custom model reading it, a pob_wrong replacement on an item, or a "no effect" record closes it now; a pob_gap
    translation (applied only to lines PoB ignores) and a pob_wrong on a passive don't."""
    from builder.ledger.coverage import noop_status

    missing = ["SomeName"]
    assert noop_status("Take 20% less Damage over time", missing, True)[:2] == ("ledger", "keeper-of-the-arc-dot")
    status, entry, reason = noop_status("Take 20% less Damage over time", missing, False)
    assert status == "none" and entry == "keeper-of-the-arc-dot" and "only on items" in reason
    assert noop_status("40% increased Archon Buff duration", missing, False)[:2] == ("model", "archon-duration")
    assert noop_status("Withered you inflict also increases Fire Damage taken", missing, True)[0] == "ledger"
    status, entry, _ = noop_status("Repeatable Spells have 20% chance to Repeat", missing, True)
    assert status == "none" and entry == "now-and-again-repeat"  # (a pob_gap translation isn't applied to parsed lines)
    assert noop_status("A line nothing knows", missing, True) == ("none", None, "parsed, but no calculation reads SomeName")
