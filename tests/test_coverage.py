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
    # Trap Duration: PoB parses it, and no calculation asks for it (Presence Area was the example until COV6: PoB's
    # calcBuffRadius reads it through a name built from its argument)
    assert not coverage.name_read("TrapDuration", reads)


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
    assert coverage.unread(["TrapDuration"], reads) == ["TrapDuration"]
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
        [{"source": "item:Gloves", "text": "10% reduced Trap Duration"},
         {"source": "item:Ring 1", "text": "+30 to maximum Life"}],
        ["Mana Tempest", "Ember Fusillade"])
    kinds = {(b["kind"], b["line"]) for b in found}
    assert ("no-op", "10% reduced Trap Duration") in kinds
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
    assert two.splitlines()[-1] == ADDED + "Slam Skills have 20% more DPS"  # 10% + 10% chance, entry aftershock-chance
    one = with_runes(two, ["Ancient Rune of the Titan"])
    assert [l for l in one.splitlines() if l.startswith(ADDED)] == [ADDED + "Slam Skills have 10% more DPS"]
    assert with_runes(one, []) == rare and corrected_item(one) == one
    helmet = next(n for n, b in _bases().items() if b["type"] == "Helmet" and not b.get("hidden"))
    unique = f"Rarity: Unique\nCrown\n{helmet}\nImplicits: 1\n+10 to Armour\nTake 20% less Damage from Hits"
    keeper = with_runes(unique, ["Legacy of Keeper of the Arc"]).splitlines()
    assert keeper[keeper.index("Implicits: 2") + 1] == SWITCHED_OFF + "Take 20% less Damage over time"
    assert keeper[-2:] == [ADDED + "10% less Damage taken from Hits", ADDED + "10% less Damage over Time taken"]
    assert keeper.count(ADDED + "10% less Damage taken from Hits") == 1  # the unique's own line: corrected_raw's job
    assert with_runes("\n".join(keeper), []) == unique


def test_a_pob_wrong_entry_with_no_replacement_switches_the_item_line_off():
    """COV7 (FIX_QUEUE 1b): passives-wrong-arcane-surge-on-crit has no replacement line (conditions.arcane_surge_uptime
    reads the line); on item text it used to change nothing, so the (10-15)% glove affix kept PoB's Arcane Surge held
    whenever you crit. It is switched off now ({disabled}: PoB skips it, bridge.sourceLines still hands it to the models)."""
    from builder.ledger import wrong
    from builder.ledger.items import DISABLED, _bases, corrected_item, unparsed_templates

    if not unparsed_templates():
        pytest.skip("no coverage.json in the data dump")
    line = "13% chance to Gain Arcane Surge when you deal a Critical Hit"
    entry, replacement = wrong(line)
    assert entry.id == "passives-wrong-arcane-surge-on-crit" and replacement is None
    assert coverage.noop_status(line, ["x"], on_item=True)[0] in ("model", "ledger")  # the uptime model reads it
    gloves = next(n for n, b in _bases().items() if b["type"] == "Gloves" and not b.get("hidden"))
    rare = f"Rarity: Rare\nOptimizer Gloves\n{gloves}\nItem Level: 82\nImplicits: 0\n+10 to Strength\n{line}"
    fixed = corrected_item(rare)
    assert fixed.splitlines()[-1] == DISABLED + line and corrected_item(fixed) == fixed
    assert wrong("Take 40% less Damage over time")[1] == "20% less Damage over Time taken"  # a replacement still replaces


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
    # (a "no effect" record; was Blackflame's, retired by COV7 for COVD8's stack-based entry)
    assert noop_status("12% of Physical Damage prevented Recouped as Energy Shield per enemy Power", missing, True)[0] == "ledger"
    status, entry, _ = noop_status("Repeatable Spells have 20% chance to Repeat", missing, True)
    assert status == "none" and entry == "now-and-again-repeat"  # (a pob_gap translation isn't applied to parsed lines)
    assert noop_status("A line nothing knows", missing, True) == ("none", None, "parsed, but no calculation reads SomeName")


def test_calc_reads_follows_a_helper_that_builds_names_from_its_argument():
    """COVB1: CalcPerform's calcBuffRadius(actor, "Presence") queries "PresenceArea" through key .. "Area" in a local
    variable, so the audit listed every "increased Presence Area of Effect" line as a no-op."""
    from builder.ledger.coverage import _param_built_names, calc_reads, name_read

    text = 'local function f(actor, key)\n\tlocal a = key .. "Area"\n\treturn actor.modDB:Sum("INC", nil, a)\nend\nf(x, "Presence")\n'
    assert _param_built_names(text) == {"PresenceArea"}
    reads = calc_reads()
    assert name_read("PresenceArea", reads) and name_read("SurroundedArea", reads)


def test_a_stat_pob_maps_to_a_skill_flag_counts_as_read():
    """COVC2: skill_can_fire_arrows maps to skillFlag "arrow" and no modifier; the audit called it "maps to nothing"."""
    assert coverage.skill_flag_stats().get("skill_can_fire_arrows") == "arrow"


def test_conquered_passives_of_factions_no_jewel_conquers_for_are_unreachable_not_gaps():
    """RESEARCH23 #140 / COVB7: 0.5's only Timeless jewels are Heroic Tragedy (Kalguur) and Undying Hate (Abyssals), so
    the vaal / eternal / karui / maraketh / templar conquered passives in PoB's legion data can't be on a character:
    they're "unreachable", out of the not-counted totals and the ranking; kalguur and abyss lines stay gaps."""
    from builder.data import data_dir

    assert coverage.no_jewel_faction({"id": "vaal_keystone_1"}) and coverage.no_jewel_faction({"id": "templar_notable_3"})
    assert not coverage.no_jewel_faction({"id": "kalguur_notable_1"}) and not coverage.no_jewel_faction({"id": "abyss_1"})
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    report = coverage.audit()
    unreachable = [r for r in report["records"] if r["category"] == "timeless-no-jewel"]
    assert unreachable and all(r["status"] == "unreachable" and coverage.no_jewel_faction({"id": r["node_id"]})
                               for r in unreachable)
    assert not any(g["category"] == "timeless-no-jewel" for g in report["ranking"])
    assert not any(r["status"] == "none" and coverage.no_jewel_faction({"id": r.get("node_id", "")})
                   for r in report["records"] if r["category"].startswith("timeless"))


def test_an_empty_pob_wrong_switches_an_item_line_off_only_when_it_says_item_off():
    """The manager's ruling after train 13 (COV8): COV7's switch-off is opt-in. COVB7's flask-used entries have no
    replacement, but flask_used_model reads tree lines only, so on an item the line must stay PoB's (switching it off
    would count it nowhere); the Arcane Surge on crit entry says item_off, its uptime model reading the item line."""
    from builder.ledger import entries, wrong

    # (the Life one says item_off since COV11: flask_used_model reads item lines too)
    assert wrong("Regenerate 1.5% of maximum Life per Second if you've used a Life Flask in the past 10 seconds")[1] is None
    assert wrong("10% increased Cast Speed if you've used a Mana Flask Recently") is None
    assert wrong("10% chance to Gain Arcane Surge when you deal a Critical Hit")[1] is None
    for entry in entries():
        if entry.kind == "pob_wrong" and entry.raw.get("item_off"):
            assert not entry.raw.get("replace"), entry.id  # (item_off means something only with no replacement)


def test_jiquanis_thesis_boots_line_reaches_pob_whole_and_movement_speed_alone_is_no_effect():
    """AUDIT12 A12-2 (COV9): the Soul Core's two-line boots mod gets PoB's whole sentence (ModParser's
    MovementSpeedAppliesToEnergyShieldRecharge, read by CalcDefence), its second half is carried, and
    no-effect-movement-speed takes movement-only lines, not a two-line first half nor Attack / Cast Speed ones."""
    from builder.ledger import translate
    from builder.ledger.items import ADDED, _bases, unparsed_templates
    from builder.search.rares import with_runes

    whole = "Increases and Reductions to Movement Speed also apply to Energy Shield Recharge Rate"
    assert translate("Increases and Reductions to Movement Speed also")[1] == [whole]
    assert translate(" apply to Energy Shield Recharge Rate")[0].id == "items-jiquanis-thesis-second-line"
    assert translate("apply to Energy Shield Recharge Rate")[0].id == "uniques-waveshaper-second-line"
    assert translate("5% reduced Movement Speed Penalty from using Fire Skills while moving")[0].id == "no-effect-movement-speed"
    assert translate("(10-15)% increased Attack and Movement Speed while you have a Bestial Minion") is None
    assert translate("5% increased Pin duration") is None  # retired: the Pinned uptime is modelled (A12-1)
    if not unparsed_templates():
        pytest.skip("no coverage.json in the data dump")
    boots = next(n for n, b in _bases().items() if b["type"] == "Boots" and not b.get("hidden"))
    rare = f"Rarity: Rare\nOptimizer Boots 1\n{boots}\nItem Level: 82\nImplicits: 0\n+10 to Strength"
    added = [l for l in with_runes(rare, ["Jiquani's Thesis"]).splitlines() if l.startswith(ADDED)]
    assert added == [ADDED + whole]
