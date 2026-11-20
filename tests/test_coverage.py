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
    assert ("gem stat", "blazing_cluster_subsequent_targeting_radius") in kinds
    assert not any(b["source"] == "gem: Mana Tempest" for b in found)  # its stats are counted now
    assert not any("maximum Life" in b["line"] for b in found)


def test_parse_checked_entries_and_item_lines_the_search_does_not_correct():
    from builder.ledger import checked_parse

    assert checked_parse("Defend with 200% of Armour").id == "parse-defend-with-armour"
    # an entry limited to one item doesn't close the same text elsewhere
    assert checked_parse("(100-160)% increased Chaos Damage", "The Unborn Lich")
    assert checked_parse("(100-160)% increased Chaos Damage", "Some Other Staff") is None
    # the search applies ledger translations to rare, magic and normal items' lines and to runes (COV2,
    # builder/ledger/items.py) and, under PoB's Bonded switch, to Bonded rune lines (COV13), not to pools it never rolls
    # or Timeless passives
    assert coverage.not_applied("ledger", "x", "translated")[0] == "ledger"
    assert coverage.not_applied("ledger", "x", "translated", "Runes")[0] == "ledger"
    assert coverage.not_applied("ledger", "x", "translated", "Runes", bonded=True)[0] == "ledger"
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


def test_a_bonded_rune_lines_stand_in_is_marked_for_the_bridge_not_added_plain():
    """COV13: a Bonded line PoB can't parse that the ledger translates gets its stand-in as a LedgerBonded line of the
    rune's augment type (the bridge makes it a Bonded rune line, so it counts only under PoB's Bonded switch); before,
    it was dropped, and adding it plain would count it for every build (an overcount)."""
    from builder.ledger.items import ADDED, BONDED, _bases, corrected_item, unparsed_templates
    from builder.search.rares import with_runes

    if not unparsed_templates():
        pytest.skip("no coverage.json in the data dump")
    mace = next(n for n, b in _bases().items() if b["type"] == "Two Hand Mace" and not b.get("hidden"))
    rare = f"Rarity: Rare\nOptimizer Weapon 1\n{mace}\nItem Level: 82\nImplicits: 0\n+10 to Strength"
    hymn = with_runes(rare, ["Legacy of Hrimnor's Hymn"])  # Bonded: 15% chance ... an additional Aftershock
    # its own (non-Bonded) 25% chance gets the plain stand-in; the Bonded 15% only the marked one
    assert [l for l in hymn.splitlines() if l.startswith(ADDED)] == [
        ADDED + "Slam Skills have 25% more DPS", ADDED + BONDED + "Rune Slam Skills have 15% more DPS"]
    assert corrected_item(hymn) == hymn and with_runes(hymn, []) == rare


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
    # (a "no effect" record: Heartstopper's; Scarred Faith's recoup line, the example until COV27, is the recovery model's now)
    assert noop_status("Take 10% less Damage over Time if you've started taking Damage over Time in the past second",
                       missing, True)[:2] == ("ledger", "no-effect-dot-and-recoup-defences")
    assert noop_status("12% of Physical Damage prevented Recouped as Energy Shield per enemy Power", missing, True)[:2] ==         ("model", "model-prevented-recouped-as-es-per-power")
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
    # retired as "no effect": the Pinned uptime is modelled (A12-1); since COV42 the model-read entry says so, adding nothing
    assert [translate("5% increased Pin duration")[0].id, translate("5% increased Pin duration")[1]] == [
        "model-read-pin-duration", []]
    if not unparsed_templates():
        pytest.skip("no coverage.json in the data dump")
    boots = next(n for n, b in _bases().items() if b["type"] == "Boots" and not b.get("hidden"))
    rare = f"Rarity: Rare\nOptimizer Boots 1\n{boots}\nItem Level: 82\nImplicits: 0\n+10 to Strength"
    added = [l for l in with_runes(rare, ["Jiquani's Thesis"]).splitlines() if l.startswith(ADDED)]
    assert added == [ADDED + whole]


def test_poe1_leftover_unique_mods_are_unreachable_and_uncarried_ones_stay_open_labelled():
    """COV19 (the user, 13 Nov 2026: the Searching Eye jewel "is from PoE1, not PoE2"): a unique-only mod whose lines
    name a PoE1-only mechanic (POE1_ONLY_GROUPS: Abyss jewels, gem sockets on items, Rampage ...) and match no line of
    any PoB-PoE2 unique is "unreachable", out of the open counts and the ranking. A mod a PoB-PoE2 unique carries never
    is, and an uncarried mod in no group stays open with an UNVERIFIED label (it may be PoE2 content PoB lacks)."""
    from builder.data import data_dir

    carried = frozenset({coverage.template("Socketed Gems are Supported by Level 5 Pierce")})
    affixes = [{"pool": "Exclusive", "id": "EyeJewel", "line": "With a Searching Eye Jewel Socketed, Attacks Blind"},
               {"pool": "Exclusive", "id": "Rampage", "line": "Recover 20% of maximum Life on Rampage"},
               {"pool": "Exclusive", "id": "Rampage", "line": "Some other line"},
               {"pool": "Exclusive", "id": "Carried", "line": "Socketed Gems are Supported by Level 15 Pierce"},
               {"pool": "Exclusive", "id": "Ward", "line": "Recover 4% of maximum Runic Ward on reaching Maximum Rage"},
               {"pool": "Item", "id": "Rolled", "line": "2% increased Area of Effect per 25 Rampage Kills"}]
    assert coverage.poe1_leftover_mods(affixes, carried) == {"EyeJewel": "Abyss jewels", "Rampage": "Rampage",
                                                              "Ward": ""}
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    report = coverage.audit()
    exclusive = [r for r in report["records"] if r["source"].startswith("Exclusive ")]
    unreachable = [r for r in exclusive if r["status"] == "unreachable"]
    marked = [r for r in unreachable if r["reason"].startswith("a PoE1 leftover (")]
    # (the rest are GGG-marked unused, COV20: test_ggg_marked_dnt_unique_mods_are_unreachable_and_named_so)
    # (COV41) and RESEARCH30's by-id verdicts (R30-6 / R30-7: coverage.NOT_OBTAINABLE, "not league-obtainable: ...")
    assert len(marked) >= 150 and all(r in marked or r["reason"].startswith(("GGG-marked unused (DNT)",
                                                                              "not league-obtainable: "))
                                      for r in unreachable)
    carried = coverage.pob_unique_templates()
    by_mod: dict[str, list[str]] = {}
    for r in exclusive:
        by_mod.setdefault(r["source"].split()[1], []).append(r["line"])
    assert not any(coverage.template(line) in carried for r in marked for line in by_mod[r["source"].split()[1]])
    # (COV48: the generic "(UNVERIFIED)" rows are settled; the Orb of Sacrifice upgrades stay open, labelled UNVERIFIED)
    assert any(r["status"] == "none" and "Searching Eye" not in r["line"] and "UNVERIFIED" in r["reason"]
               for r in exclusive)
    assert not any("Eye Jewel Socketed" in r["line"] for r in exclusive if r["status"] == "none")
    assert not any(g["template"] == coverage.template("Recover 20% of maximum Life on Rampage")
                   for g in report["ranking"])


def test_recovery_lines_no_rate_reads_are_named_undercounts_and_modelled_ones_counted():
    """AUDIT27 A27-32 (COV20; the user's "every recovery source counts" rule): an open line recovery.EVENT_UNREAD names
    (the "when Stunned" passives) is a routed UNDERCOUNT for MECH-C; the three recovery.hit_income reads (MECHC14) are
    counted as model-read."""
    from builder.data import data_dir
    from builder.ledger.coverage import ignored_line_status

    assert coverage.unread_recovery("Recover 3% of maximum Life when you lose a Spirit Charge")
    # (MECHC33: "Life when you use a Mana Flask" read at the Mana flask's uses, recovery.mana_flask_income)
    assert not coverage.unread_recovery("Recover 3% of maximum Life when you use a Mana Flask")
    # (train 36: MECHC15's 1d9ba16 counts "Life when Stunned" at the boss's Light-Stunning hits: no longer unread)
    assert not coverage.unread_recovery("Regenerate 5% of maximum Life over 1 second when Stunned")
    for line in ("Recover (20-30)% of Missing Life before being Hit by an Enemy",
                 "Regenerate 2% of your Armour as Life over 1 second when you Block",
                 "Regenerate (6-8) Life over 1 second when you Cast a Spell"):
        assert not coverage.unread_recovery(line) and ignored_line_status(line, False)[:2] == (
            "model", "recovery-on-boss-hit-block-cast"), line
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    routed = [r for r in coverage.audit()["records"] if r.get("routed") == "MECH-C"]
    assert all(r["status"] == "none" and r["undercount"] and "UNDERCOUNT" in r["reason"] for r in routed)


def test_ggg_marked_dnt_unique_mods_are_unreachable_and_named_so():
    """COV20: a unique-only mod whose own game text starts "DNT" (GGG's "do not use" mark; DNT_UNUSED's evidence) is
    unreachable with its own reason, not a PoE1 leftover; the mechanics they name stay counted on other lines."""
    from builder.data import data_dir

    assert coverage.DNT_UNUSED.match("DNT-UNUSED Blasphemy has no Reservation")
    assert not coverage.DNT_UNUSED.match("Bleeding you inflict is Aggravated")
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    records = coverage.audit()["records"]
    marked = [r for r in records if r["reason"].startswith("GGG-marked unused (DNT)")]
    assert len(marked) >= 7 and all(r["status"] == "unreachable" and r["source"].startswith("Exclusive ")
                                    for r in marked)
    assert not any(r["line"].startswith("DNT") and r["status"] == "none" for r in records)


def test_fishing_lines_have_no_combat_effect_and_leave_the_open_list():
    """COV21 (the manager's ruling, 13 Nov): the fishing lines roll on PoE2's Fishing Rod (the affix mods spawn on the
    fishing_rod tag), so they're not "unreachable"; they touch no combat number, so the ledger's no-effect-fishing entry
    takes them out of the open list. A line naming fishing and something else stays open."""
    for line in ("(20-40)% increased Fishing Line Strength", "(15-30)% reduced Fishing Pool Consumption",
                 "20% increased Fishing Range", "(40-50)% reduced Quantity of Fish Caught",
                 "(50-60)% increased Rarity of Fish Caught", "Glows while in an Area containing a Unique Fish",
                 "Karui Stone Hook", "Rhoa Feather Lure", "Siren Worm Bait"):  # (COV40: the hooks and lures)
        assert coverage.ignored_line_status(line, False) == ("ledger", "no-effect-fishing",
                                                             "no effect on what's calculated"), line
    assert coverage.ignored_line_status("20% increased Fishing Range and Damage", False)[1] != "no-effect-fishing"


def test_cov21_mechc14_readers_are_credited_and_life_recharge_is_model_read():
    """COV21: Indigon's line (bridge setManaRegenOnly, flasks, recovery.NO_MANA) and "Lose all <kind> Charges on reaching
    maximum" (conditions.CHARGE_RESET_AT_MAX) count as model-read. COV23: Tetzlapokal's Desire's "Life Recharges" (AUDIT28
    A28-3) is read since SEARCH15 (bridge setLifeRecharge, objective.life_recharge), and so is MECHC17's "Regenerate N% of
    maximum Energy Shield over 1 second when Stunned" (recovery.STUNNED_ES): both credited, no routing left."""
    assert coverage.ignored_line_status("Mana Recovery other than Regeneration cannot Recover Mana", False)[:2] == (
        "model", "model-indigon-mana-regeneration-only")
    assert coverage.ignored_line_status("Lose all Power Charges on reaching maximum Power Charges", False)[:2] == (
        "model", "model-lose-all-charges-on-maximum")
    assert coverage.ignored_line_status("Lose all Power Charges on reaching maximum Frenzy Charges", False)[0] != "model"
    assert coverage.ignored_line_status("Life Recharges", False)[:2] == ("model", "model-life-recharges")
    assert coverage.ignored_line_status("Life Recharges instead of Energy Shield", False)[1] != "model-life-recharges"
    assert coverage.ignored_line_status("Regenerate 4% of maximum Energy Shield over 1 second when Stunned", False)[:2] == (
        "model", "model-energy-shield-when-stunned")
    assert not hasattr(coverage, "LIFE_RECHARGE")


def test_grand_spectrum_multiplier_has_a_setter_and_golem_mods_are_poe1_leftovers():
    """COV21: PoB's Item.lua sets Multiplier:GrandSpectrum with modLib.createMod(...) for each Grand Spectrum jewel, so
    the audit's setter scan finds it and PoE2's Grand Spectrum lines aren't "never-on"; unique-only mods naming Golems
    (no PoE2 player Golem: POE1_ONLY_GROUPS' evidence) are PoE1 leftovers."""
    from builder.ledger import parse_audit

    assert "Multiplier:GrandSpectrum" in parse_audit.calc_setters()["exact"]
    affixes = [{"pool": "Exclusive", "id": "MaximumGolemsUnique__1", "line": "+1 to maximum number of Summoned Golems"},
               {"pool": "Exclusive", "id": "Other", "line": "Gain 30 Mana per Grand Spectrum"}]
    found = coverage.poe1_leftover_mods(affixes, carried=frozenset())
    assert found == {"MaximumGolemsUnique__1": "golems", "Other": ""}


def test_a_parsed_line_pob_reads_but_the_tool_doesnt_count_is_reported_not_applied():
    """COV27 (AUDIT33 A33-1): Shavronne's Satchel's "Life Recovery from Flasks also applies to Energy Shield" is parsed and
    its flag read (CalcPerform 1635's joined name, and the flask tooltip), so the audit called it "pob"; but PoB applies it
    only to a flask whose effect isn't removed at full Life, and the tool's flask model counts Life and Mana only. A
    `parsed_unused` entry reports it "none", routed to MECH-C, and adds nothing to PoB."""
    from builder.ledger import parsed_unused

    reads = coverage.calc_reads()
    satchel = "Life Recovery from Flasks also applies to Energy Shield"
    assert coverage.name_read("LifeFlaskAppliesToEnergyShield", reads)  # (why the audit called it counted)
    # (COV37) MECH-C reads it since MECHC23 (Assumptions.es_flask_income), so its parsed_unused entry is retired and the
    # audit says the model counts it
    assert parsed_unused(satchel) is None and parsed_unused(satchel + " during Effect") is None
    status, found, reason = coverage.noop_status(satchel, ["LifeFlaskAppliesToEnergyShield"], True)
    assert (status, found) == ("model", "model-read-es-from-life-flask")
    # (COV35) "Charms use no Charges" was the no-op example with an entry naming its owner; MECH-C reads it since
    # MECHC25 (flasks.CHARMS_FREE), so its parsed_unused entry is retired and the no-op is closed by the model
    status, found, reason = coverage.noop_status("Charms use no Charges", ["CharmsUseNoCharges"], True)
    assert (status, found) == ("model", "model-read-charms-free") and reason.startswith("parsed, but no calculation")
    assert parsed_unused("Life Recovery from Flasks also applies to Mana") is None


def test_cultivated_outcomes_are_live_and_the_no_carrier_ones_labelled_not_dropped():
    """A32-10 / CULT1 (COV27): the Vaal Cultivation Orb's outcome mods (UniqueMutatedVaal*) are core since 0.5.0, so they
    are live, never "a PoE1 leftover"; the 28 on no listed Cultivated unique and the 28 HandWraps ones (two different sets)
    are labelled "no current carrier (UNVERIFIED)" and keep their status."""
    assert "live" in coverage.cultivated_label("UniqueMutatedVaalFireExposureOnHit")  # Saitha's Spear's Exposure line
    assert coverage.cultivated_label("UniqueMutatedVaalReflectElementalAilmentsToSelf").startswith("no current carrier")
    assert coverage.cultivated_label("HandWrapsUniqueMutatedVaalChanceToBleed").startswith("no current carrier")
    assert coverage.cultivated_label("UniqueSomethingElse1") is None
    assert len(coverage.CULTIVATED_NO_CARRIER) == 28
    assert not any(coverage.HANDWRAPS_MUTATED.match(m) for m in coverage.CULTIVATED_NO_CARRIER)



def test_a_downside_pob_parses_but_nothing_reads_is_routed_as_an_overcount():
    """COV28 (AUDIT33 A33-2's scan, widened to wildcard reads): Fireflower's "Take 100 Fire Damage when you Ignite an
    Enemy" is parsed to EyeOfInnocenceSelfDamage, which no PoB code reads (the audit's "...Damage" wildcard matched it):
    a `direction: overcount` parsed_unused entry routes it as an OVERCOUNT, never an undercount."""
    from builder.data import data_dir
    from builder.ledger import parsed_unused

    line = "Take 100 Fire Damage when you Ignite an Enemy"
    entry = parsed_unused(line)
    assert entry and entry.id == "unused-fire-damage-taken-on-ignite" and entry.raw["direction"] == "overcount"
    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    records = [r for r in coverage.audit()["records"] if r["line"] == line]
    assert records
    # COV36: models.ignite_self_damage (MECHB25) reads it, so it counts as that model's (items-cov36.yaml); the overcount
    # route is coverage.py's for any parsed_unused line no model reads
    assert all(r["status"] == "model" and r["entry"] == "model-read-fireflower" for r in records)


def test_a_never_on_line_a_model_reads_is_counted_and_one_nothing_reads_stays_open():
    """COV35: the never-on check (a parsed line whose PoB condition nothing sets) now asks the models and the condition
    resolver, as the ignored and no-op paths do: "You gain Onslaught for 3 seconds on Kill" is parsed under
    Condition:KilledRecently, but conditions.onslaught_uptime rates it at the build's kills; "Melee Damage if you've dealt
    a Projectile Attack Hit in the past eight seconds" is models.projectile_hit_recently_model's. A never-on line no model
    reads stays open."""
    records = coverage.audit()["records"]
    never = {r["line"]: r for r in records if r["category"] == "never-on"}
    onslaught = [r for line, r in never.items() if line.startswith("You gain Onslaught for") and line.endswith("on Kill")]
    assert onslaught and all(r["status"] == "model" for r in onslaught)
    melee = [r for line, r in never.items() if "increased Melee Damage if you've dealt a Projectile Attack Hit" in line]
    assert melee and all(r["status"] == "model" for r in melee)
    # The open half, independent of any one line (COV42, the manager 06:15: a fixed example broke twice, in trains 76
    # and 77, each time a lane modelled it or R30-7 marked a copy unreachable): every never-on row still open is one no
    # model and no condition the resolver sets reads, and some are open (a line PoB parses under a condition nothing
    # sets always exists while PoB has Configuration-only conditions).
    from builder.ledger import model_entry

    open_ = [r for r in records if r["category"] == "never-on" and r["status"] == "none"]
    assert open_ and not [r["line"] for r in open_ if model_entry(r["line"]) or coverage.resolver_condition(r["line"])]
    assert all(r["status"] in ("model", "none", "ledger", "unreachable") for r in never.values())
    # (MECH86: conditions.conditionMoving sets PoB's box, so the "while moving" lines are no longer never-on)
    assert not [line for line in never if line.endswith("increased Mana Regeneration Rate while moving")]


def test_a_line_pobs_item_code_reads_is_pobs():
    """COV36: "This item gains bonuses from Socketed Items as though it was a Body Armour" isn't parsed by ModParser, but
    PoB's Item.lua reads it (probe POE2T2594, tests/test_as_though_body_armour.py); the Soul Core form too (POE2T2599)."""
    status, entry, reason = coverage.ignored_line_status(
        "This item gains bonuses from Socketed Items as though it was a Body Armour", False)
    assert status == "pob" and "Item.lua" in reason
    assert coverage.ignored_line_status("This item gains bonuses from Socketed Items as though it was Gloves", False)[0] == "pob"
    assert coverage.ignored_line_status(
        "This item gains bonuses from Socketed Soul Cores as though it was also a Helmet", False)[0] == "pob"


def test_a_multiplier_tag_with_an_actor_names_that_actors_multiplier():
    """COV36: Idolatry's "per Idol in your Equipment" parses with actor="player" on its Multiplier tag; the audit kept
    "player:" in the name, which no setter matches, so CalcSetup's IdolsInEquipment read "nothing sets it"."""
    from builder.ledger.parse_audit import needs

    assert needs([{"g": ["Multiplier=player:IdolsInEquipment"]}]) == ["Multiplier:IdolsInEquipment"]
    assert needs([{"g": ["Multiplier=enemy:ChillEffect"]}]) == ["enemy:Multiplier:ChillEffect"]
    assert needs([{"g": ["Multiplier=Rage"]}]) == ["Multiplier:Rage"]


def test_a_never_on_item_line_a_pob_wrong_entry_replaces_is_counted():
    """COV36: Indigon's "for each 200 total Mana Spent Recently" lines are parsed under Multiplier:ManaSpentRecently (only
    PoB's Configuration box sets it), but COV32's pob_wrong entries replace them on items with shares at the build's own
    Mana spent: the never-on check now asks wrong() on item lines, as noop_status does."""
    from builder.data import data_dir

    if not (data_dir() / "coverage.json").exists():
        pytest.skip("no coverage.json")
    rows = [r for r in coverage.audit()["records"] if r["category"] == "never-on"
            and r["line"].endswith("increased Cost of Skills for each 200 total Mana Spent Recently")]
    assert rows and all(r["status"] == "ledger" and r["entry"] == "wrong-indigon-cost-per-mana-spent" for r in rows)


def test_rampart_raptors_reloaded_recently_line_counts_as_the_models():
    """COV36: models.reloaded_recently_model (MECHB31) reads "... chance to not expend Ammunition if you've Reloaded
    Recently", in one line or broken after "not" (Rampart Raptor's PoB text)."""
    from builder.ledger import model_entry

    for gap in (" ", "\n"):
        line = f"Bolts fired by Crossbow Attacks have 100% chance to not{gap}expend Ammunition if you've Reloaded Recently"
        assert model_entry(line).id == "model-read-reloaded-recently-ammunition"
    # (COV37) Reusable Ammunition's first half, a line of its own in PoB's 0.5 tree (the model joins neighbours)
    assert model_entry("Bolts fired by Crossbow Attacks have 30% chance to not").id ==         "model-read-reloaded-recently-ammunition"


def test_item_lines_a_model_reads_count_as_the_models():
    """COV37: lines whose model was already live (its regex and caller checked) count as read, each by its own entry;
    the conditional dodge-roll lines the model rates 0 stay open."""
    from builder.ledger import model_entry

    cases = {
        "You can have two Companions of different types": "model-read-companion-limit",
        "You can have any number of Companions of different types": "model-read-companion-limit",
        "15% increased Damage per each different Companion in your Presence": "model-read-companion-per-type",
        "(40-50)% Surpassing Chance to gain a Puppet Master stack whenever you use a Command Skill": "model-read-puppet-master",
        "+(3-4) maximum stacks of Puppet Master": "model-read-puppet-master",
        "(20-30)% chance for Skills to retain 40% of Glory on use": "model-read-glory-lines",
        "(15-20)% increased Glory generation for Banner Skills": "model-read-glory-lines",
        "A random Skill that requires Glory generates 50% of its maximum Glory when your Marks Activate":
            "model-read-glory-lines",
        "Sealed Skills have +1 to maximum Seals": "model-read-seals",
        "Sealed Skills have (21-35)% increased Seal gain frequency": "model-read-seals",
        "10% increased Damage for each Hazard triggered Recently, up to 50%": "model-read-escalating-mayhem",
        "-1 metre to Dodge Roll distance if you've Dodge Rolled Recently": "model-read-dodge-distance-rolled-recently",
        "Recover 3% of maximum Runic Ward when one of your Reviving Minions is Killed":
            "model-read-ward-on-reviving-minion-killed",
        "Minions in your Presence have Onslaught while you are on Low Runic Ward": "model-read-bodyguards",
        "(10-18)% chance for Charms you use to not consume Charges": "model-read-charm-not-consume",
        "Life Recovery from Flasks also applies to Energy Shield": "model-read-es-from-life-flask",
        "Life Recovery from Flasks also applies to Energy Shield during Effect": "model-read-es-from-life-flask",
        "Leeches 1% of maximum Life when you Cast a Spell": "model-read-leech-on-spell-cast",
        "Recover 3% of maximum Life when one of your Minions is Revived": "model-read-life-on-minion-revived",
        "Recover (6-12)% of Maximum Life when you expend at least 10 Combo": "model-read-recover-on-combo-expended",
        "Lose all Tailwind when Hit": "model-read-lose-tailwind-when-hit",
        "Used when you become Ignited": "model-read-charm-triggers",
        "Used when you take Cold damage from a Hit": "model-read-charm-triggers",
        "Used when you are affected by a Slow": "model-read-charm-triggers",
        "Used when you release a skill with Perfect Timing": "model-read-charm-triggers",
        "Used when you start Bleeding": "model-read-charm-triggers",
        "Creates Consecrated Ground on use": "model-read-consecrated-ground-charm",
        "Eat a Soul when you Hit an enemy with an Open Weakness": "model-read-soul-eater-lines",
        "Eat a Soul when you Hit a Unique Enemy, no more than once every 0.5 seconds": "model-read-soul-eater-lines",
        "Reveal Weaknesses against Rare and Unique enemies": "model-read-soul-eater-lines",
        "Sacrifice (10-30)% of maximum Life to gain half that much Runic Ward when you Attack":
            "model-read-ward-sacrifice-on-attack",
        "Recover 2% of maximum Life when you Consume a corpse": "model-read-recover-on-corpse-consumed",
        "15% chance to create Chilled Ground when you Freeze an Enemy": "model-read-chilled-ground-on-freeze",
        "You lose all Endurance Charges on reaching maximum Endurance Charges": "model-lose-all-charges-on-maximum",
        "Enemies you Curse have -(8-5)% to Chaos Resistance": "model-read-enemies-you-curse-chaos-resistance",
        "Enemies you Curse have -5% to Chaos Resistance": "model-read-enemies-you-curse-chaos-resistance",
        # (COV38: MECH81's Critical Weakness, MECHC32's Redflare Shock, Coat of Red Bleed and charges not removed)
        "Critical Hits with Spells apply (1-3) Stack of Critical Weakness": "model-read-critical-weakness-lines",
        "Critical Hits with Spells apply 5 Stacks of Critical Weakness": "model-read-critical-weakness-lines",
        "Parrying applies 10 Stacks of Critical Weakness": "model-read-critical-weakness-lines",
        "Warcries inflict 3 Critical Weakness on Enemies": "model-read-critical-weakness-lines",
        "Shocks you when you reach maximum Power Charges": "model-read-shocks-you-on-maximum-charges",
        "25% chance to be inflicted with Bleeding when Hit": "model-read-inflicted-with-bleeding-when-hit",
        "Skills have (10-15)% chance to not remove Charges but still count as consuming them": "model-read-charges-not-removed",
        # (COV38, the manager 15:24 for MECH82: Beetlebite's line, once a no-effect entry; R26-34, MECH82's model)
        "Aggravate Bleeding on Enemies when they Enter your Presence": "model-read-presence-aggravate",
        # (COV38, the manager 15:24 for MECHC33: read at the Mana flask's uses since train 67)
        "Recover (8-10)% of maximum Life when you use a Mana Flask": "model-read-life-on-mana-flask-use",
        "Recover 2% of maximum Life when you use a Mana Flask": "model-read-life-on-mana-flask-use",
        # (COV38: MECHC33 widened KEEP_INFUSION to the item / tree wording; Snakepit's outcome)
        "Skills have (5-10)% chance to not remove Elemental Infusions but still count as consuming them":
            "model-read-keep-elemental-infusions",
        "Gain a Power Charge every Second if you haven't lost Power Charges Recently":
            "model-read-charge-every-second-if-not-lost",
        # (COV38: MECH83's ground Ignite damage, train 70)
        "Creates Ignited Ground for 4 seconds when used, Igniting enemies as though dealing Fire damage equal to 500% of "
        "your maximum Life": "model-read-ground-ignite-as-though-life",
        "Drop Ignited Ground while moving, which lasts 8 seconds and Ignites as though dealing Fire Damage equal to 10% of "
        "your maximum Life": "model-read-ground-ignite-as-though-life",
        # (COV38: MECHC34's bridge fix reads The Unleashed's ranged line, train 71)
        "(15-25)% of Damage taken from Hits bypasses Energy Shield if Energy Shield is below half": "model-read-es-bypass-below-half",
        "25% of Damage taken from Hits bypasses Energy Shield if Energy Shield is below half": "model-read-es-bypass-below-half",
        # (COV39: MECHC35's downside readers, train 72)
        "+25% chance to be Poisoned": "model-read-chance-to-be-ailed",
        "+(20-30)% chance to be Ignited": "model-read-chance-to-be-ailed",
        "+20% chance to be Shocked": "model-read-chance-to-be-ailed",
        "You take (8-15)% of damage from Blocked Hits with a raised Shield": "model-read-raised-shield-block-taken",
        "20% increased Attack Damage if you have been Heavy Stunned Recently": "model-read-stunned-recently-lines",
        "40% faster start of Energy Shield Recharge if you've been Stunned Recently": "model-read-stunned-recently-lines",
        "100% increased Stun Threshold for each time you've been Stunned Recently": "model-read-stunned-recently-lines",
        "Gain Overencumbrance for 4 seconds when you Dodge Roll": "model-read-overencumbrance-on-dodge",
        "Damage over Time cannot bypass your Energy Shield": "model-read-dot-cannot-bypass-es",
        # (COV40: COV39's drafts for MECH83 / MECH84's readers, train 73)
        "Grants Skill: Kelari's Deception": "model-read-djinn-command-grants",
        "Grants Skill: Kelari's Judgment": "model-read-djinn-command-grants",
        "Grants Skill: Ruzhan's Trap": "model-read-djinn-command-grants",
        "Grants Skill: Ruzhan's Reckoning": "model-read-djinn-command-grants",
        "Grants Skill: Ruzhan's Fury": "model-read-djinn-command-grants",
        "Grants Skill: Navira's Fracturing": "model-read-djinn-command-grants",
        "Curses you inflict are reflected back to you": "model-read-curses-reflected",
        "Your Hits cannot Stun enemies": "model-read-hits-cannot-stun",
        "Gain 1 Fragile Regrowth each second": "model-read-fragile-regrowth",
        "Maximum 10 Fragile Regrowth": "model-read-fragile-regrowth",
        "Lose all Fragile Regrowth when Hit": "model-read-fragile-regrowth",
        "Inflict Anaemia on Hit": "model-read-anaemia",
        "Anaemia allows +3 Corrupted Blood debuffs to be inflicted on enemies": "model-read-anaemia",
        "until you take no Damage to Life for 3 seconds": "model-read-widows-reign-window",
        "Has 4 Augment Sockets": "model-read-fixed-rune-sockets",
        "Has 3 Sockets": "model-read-fixed-rune-sockets",
    }
    from builder.search import assumptions, boss_hits, conditions, models, recovery
    for line in ("+25% chance to be Poisoned", "+20% chance to be Shocked"):
        assert boss_hits.SELF_AILMENT_CHANCE.match(line), line
    assert recovery.RAISED_BLOCK_TAKEN.match("You take 15% of damage from Blocked Hits with a raised Shield")
    assert recovery.OVERENCUMBRANCE.match("Gain Overencumbrance for 4 seconds when you Dodge Roll")
    # (COV40) each Command the entry names is one the model lists as granted; Navira's Oasis / Well aren't (they stay open)
    granted = {c.name for c in models.DJINN_COMMANDS if c.granted}
    for name in ("Kelari's Deception", "Kelari's Judgment", "Ruzhan's Trap", "Ruzhan's Reckoning", "Ruzhan's Fury",
                 "Navira's Fracturing"):
        assert name in granted, name
    for name in ("Navira's Oasis", "Navira's Well", "Queen's Procession"):
        assert name not in granted
        assert (found := model_entry(f"Grants Skill: {name}")) is None or found.id != "model-read-djinn-command-grants"
    assert conditions.CURSE_REFLECT.match("Curses you inflict are reflected back to you")
    assert conditions.CANNOT_STUN.match("Your Hits cannot Stun enemies")
    assert conditions.fragile_regrowth(["Gain 1 Fragile Regrowth each second", "Maximum 10 Fragile Regrowth"], {})[0] == 10
    assert models.ANAEMIA.search("Anaemia allows +3 Corrupted Blood debuffs to be inflicted on enemies")
    assert assumptions.LIFE_RESERVED.search("Life that would be lost by taking Damage is instead Reserved\n"
                                            "until you take no Damage to Life for 3 seconds")
    for line in ("20% increased Attack Damage if you have been Heavy Stunned Recently",
                 "40% faster start of Energy Shield Recharge if you've been Stunned Recently",
                 "100% increased Stun Threshold for each time you've been Stunned Recently"):
        assert recovery.STUNNED_RECENTLY.match(line), line
    # (PoB applies the "haven't been Stunned Recently" lines itself; the model only takes the stunned share back off them)
    found = model_entry("30% increased Stun Threshold if you haven't been Stunned Recently")
    assert found is None or found.id != "model-read-stunned-recently-lines"
    from builder.search import boss_hits, conditions, models
    assert models.CW_SPELL_CRIT.match("Critical Hits with Spells apply 2 Stack of Critical Weakness")
    assert conditions.SELF_SHOCK_ON_MAX.match("Shocks you when you reach maximum Power Charges")
    assert boss_hits.SELF_BLEED.match("25% chance to be inflicted with Bleeding when Hit")
    assert conditions.charges_kept_chance(["Skills have 12% chance to not remove Charges but still count as consuming them"]) == 0.12
    from builder.search import assumptions, cultivated
    assert assumptions.KEEP_INFUSION.search("Skills have 8% chance to not remove Elemental Infusions but still count as consuming them")
    archon = ("Skills have 45% chance to not remove Elemental Infusions but still count as consuming them if you've lost an "
              "Archon Buff in the past 6 seconds")
    assert (found := model_entry(archon)) is None or found.id != "model-read-keep-elemental-infusions"
    snakepit = [o for o in cultivated.outcomes("Snakepit") if o["mod"] == "UniqueMutatedVaalChanceToNotConsumeInfusion"]
    assert snakepit and cultivated.outcome_lines(snakepit[0])  # (offered now)
    redflare = [o for o in cultivated.outcomes("Redflare Conduit") if o["mod"] == "UniqueMutatedVaalGainPowerChargesNotLostRecently"]
    assert redflare and cultivated.outcome_lines(redflare[0])
    assert conditions.NOT_LOST_RECENTLY.search("Gain a Power Charge every Second if you haven't lost Power Charges Recently")
    for line, entry in cases.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
    # (COV45, the manager 18:15: Lioneye's "+2 metres ... if you haven't Dodge Rolled Recently" is read with MECH89's label)
    for line in ("20% increased Glory generation for Tribute", "Used when you become Cursed"):
        found = model_entry(line)
        assert found is None or not found.id.startswith("model-read-"), line


def test_debuff_lines_whose_event_the_model_rates_count_as_resolver_sources():
    """COV37: conditions._debuff_source rates Blind from your Poisons (MECH63), from the boss's Stuns on you (MECHC25) and
    from a raised shield (MECHC21), so the audit counts those lines as the resolver's; a kill-event Blind stays open."""
    for line in ("Blind Targets when you Poison them", "Blind Enemies when they Stun you",
                 "Blind Enemies 6 metres in front of you every 2 seconds while your Shield is raised"):
        found = coverage.resolver_condition(line)
        assert found is not None and found.var == "conditionEnemyBlinded", line
    assert coverage.resolver_condition("Blind Enemies when you Kill them") is None


def test_an_onslaught_line_on_a_named_event_counts_as_the_resolvers():
    """COV38: conditions._onslaught_source rates a Mark / Warcry / Totem / Flask event from the build (event_rates, 0
    without one), so the audit counts such a line; Companions' Onslaught isn't the player's and stays open."""
    found = coverage.resolver_condition("Gain Onslaught for 4 seconds when your Marks Activate")
    assert found is not None and found.var == "buffOnslaught"
    assert coverage.resolver_condition("Companions gain Onslaught for 4 seconds on Hitting your Marked targets") is None
    assert coverage.resolver_condition("You gain Onslaught for 20 seconds on using a Vaal Skill") is None


def test_the_animal_aspect_grants_have_no_effect():
    """COV38: PoE1's "Grants Level 20 Aspect of the Cat / Avian / Crab / Spider Skill" name no PoE2 skill (game
    26650308: no ActiveSkills / GrantedEffects / ModGrantedSkills row; poe2db only buff stubs): a no-effect entry."""
    from builder.ledger import translate
    for animal in ("Cat", "Avian", "Crab", "Spider"):
        found = translate(f"Grants Level 20 Aspect of the {animal} Skill")
        assert found and found[0].id == "no-effect-animal-aspect-grants" and not found[1], animal
    for line in ("When socketed into a Unique Kalguuran or Ezomyte item, destroys the item to create a Rune imbued with "
                 "that item's power", "Corrupting will always result in change"):
        found = translate(line)
        assert found and found[0].id == "no-effect-crafting-only-socketables" and not found[1], line
    # (COV38, the manager 22:08 from RESEARCH29 R29-2: no effect at the tool's 5 m, the base range assumed)
    found = translate("50% reduced Projectile Range")
    assert found and found[0].id == "no-effect-reduced-projectile-range-at-5m" and not found[1]


def test_a_freeze_cull_stat_is_damage_not_behaviour():
    """COV37 (GEM15): Arctic Howl's has_freeze_cull ("Freeze enemies that are Primed for Freeze") changes Freeze uptime."""
    assert coverage.stat_mechanic("has_freeze_cull") == "damage"
    assert not coverage.STAT_BEHAVIOUR.search("has_freeze_cull")


def test_a_plain_fork_chance_has_no_effect_on_the_lone_boss():
    """COV37: the hand wraps' "Projectiles have N% chance to Fork" reads as MECHB31's melee-hit form does (no boss hit)."""
    from builder.ledger import translate

    found = translate("Projectiles have (25-44)% chance to Fork")
    assert found is not None and found[0].id == "no-effect-projectile-fork-chance" and not found[1]


def test_legacy_variant_lines_are_unreachable(tmp_path):
    """COV40: a unique line every copy of which sits on "Pre 0.x" variants only is a legacy line no new item rolls
    (Bones of Ullr's pre-0.3.0 reduced Reservation Efficiency; patch 0.3.0); a template a current line also holds stays."""
    import json

    from builder.ledger.coverage import legacy_variant_templates, template

    raw = "\n".join(["Bones of Ullr", "Lattice Sandals", "Variant: Pre 0.1.1", "Variant: Pre 0.3.0", "Variant: Current",
                     "Implicits: 0",
                     "{variant:1,2}20% reduced Reservation Efficiency of Skills which create Undead Minions",
                     "{variant:3}(20-30)% increased Reservation Efficiency of Skills which create Undead Minions",
                     "{variant:1}+25 to maximum Life", "{variant:2,3}+(20-40) to maximum Life",
                     "(40-60)% increased Energy Shield"])
    (tmp_path / "uniques.json").write_text(json.dumps({"uniques": [{"name": "Bones of Ullr", "raw": raw}]}),
                                           encoding="utf-8")
    legacy = legacy_variant_templates(tmp_path)
    reduced = ("Bones of Ullr", template("20% reduced Reservation Efficiency of Skills which create Undead Minions"))
    assert legacy == {reduced: ["Pre 0.1.1", "Pre 0.3.0"]}
    assert legacy_variant_templates(tmp_path / "missing") == {}


def test_researched_unobtainable_item_mods_are_unreachable():
    """COV40 (RESEARCH30 R30-1): the Essence-style "chance to Crush on Hit" mod has no PoE2 Essence, so its row is
    unreachable, with the evidence in its reason; Way of the Stonefist's HandWraps twins (R30-3) aren't listed."""
    from builder.ledger import coverage

    assert "CrushOnHitChanceEssence1" in coverage.NOT_OBTAINABLE
    assert not any(k.startswith("HandWraps") or k.startswith("Alloy") for k in coverage.NOT_OBTAINABLE)
    report = coverage.audit()
    crush = [r for r in report["records"] if r["source"] == "Item CrushOnHitChanceEssence1"]
    assert crush and all(r["status"] == "unreachable" and "RESEARCH30" in r["reason"] for r in crush)
    # (COV41, RESEARCH30 R30-4, the manager 00:03 18 Nov) six more PoE1 corrupted-Essence suffixes
    six = ("NearbyEnemiesChilledOnBlockEssence1", "ChanceToRecoverManaOnSkillUseEssence1",
           "SocketedGemsNonCurseAuraEffectEssence1", "SocketedAuraGemLevelsEssence1", "FireBurstOnHitEssence1",
           "SpiritMinionEssence1")
    assert all("R30-4" in coverage.NOT_OBTAINABLE[m] for m in six)
    rows = [r for r in report["records"] if r["source"] in {f"Item {m}" for m in six}]
    assert {r["source"].split()[1] for r in rows} == set(six)
    assert all(r["status"] == "unreachable" and "R30-4" in r["reason"] for r in rows)


def test_slot_names_built_by_concatenation_count_as_read():
    """COV41 (probe POE2T3858): PoB reads "EffectOfBonusesFrom" .. slot (CalcPerform 1491), and slot names have a
    space ("Ring 1"), so Ingenuity's "N% increased bonuses gained from left / right Equipped Ring" (EffectOfBonusesFromRing
    1 / 2) moves Life: 1460 -> 1491 with a +100 Life ring and 30%. The wildcard takes spaces, but a bare wildcard part
    alone still reads nothing (ActiveSkeletonLimit stays unread)."""
    from builder.ledger.coverage import calc_reads, name_read

    reads = calc_reads()
    assert name_read("EffectOfBonusesFromRing 1", reads) and name_read("EffectOfBonusesFromRing 2", reads)
    assert not name_read("ActiveSkeletonLimit", reads)


def test_harbinger_summon_mods_are_poe1_leftovers():
    """COV41: PoE1's Harbinger uniques' "Grants Summon [Greater] Harbinger of X Skill" (stat
    local_display_summon_harbinger_x_on_equip, on the 12 HarbingerSkillOnEquipUnique* Mods only) are PoE1 leftovers;
    PoE2's own Harbinger of Madness lines aren't."""
    from builder.ledger.coverage import poe1_leftover_mods

    affixes = [{"pool": "Exclusive", "id": "HarbingerSkillOnEquipUnique__1", "line": "Grants Summon Harbinger of the Arcane Skill"},
               {"pool": "Exclusive", "id": "HarbingerSkillOnEquipUnique2_6", "line": "Grants Summon Greater Harbinger of Brutality Skill"},
               {"pool": "Exclusive", "id": "Madness", "line": "Hits have 10% chance to spawn a Harbinger of Madness Orb"}]
    found = poe1_leftover_mods(affixes, carried=frozenset())
    assert found == {"HarbingerSkillOnEquipUnique__1": "Harbinger summons",
                     "HarbingerSkillOnEquipUnique2_6": "Harbinger summons", "Madness": ""}


def test_kalandras_touch_line_counts_as_read_by_pob():
    """COV41 (probe POE2T3865): PoB copies the opposite ring onto Kalandra's Touch by the item's name (CalcSetup 1637),
    though ModParser leaves "Reflects opposite Ring" unparsed: Life 1355 with a blank rare beside a +100 Life ring,
    1460 with Kalandra's Touch instead. The audit counts the line as PoB's on items, not on passives."""
    from builder.ledger.coverage import ignored_line_status

    assert ignored_line_status("Reflects opposite Ring", False)[0] == "pob"
    assert ignored_line_status("Reflects opposite Ring", True)[0] == "none"


def test_researched_unique_leftovers_are_unreachable_and_obtainable_ones_are_not():
    """COV41 (RESEARCH30 R30-6 / R30-7, the manager 01:50 18 Nov): the 329 unique-only and Harvest Mods ids research
    found unreachable are in NOT_OBTAINABLE with their family's evidence; the OBTAINABLE ids (Fists of Stone twins,
    Loreweave, The Master's Reach, the Grasping Mail / Breach Ring / Cleansing Charm implicits) and the 38 UNVERIFIED
    ones aren't, and the Exclusive pool's records are marked."""
    import json
    from pathlib import Path

    from builder.ledger import coverage

    data = json.loads((Path(coverage.__file__).with_name("unreachable_unique_mods.json")).read_text(encoding="utf-8"))
    assert len(data["mods"]) == 335  # (COV42: + R32-3's two DNT-UNUSED triggers, family R32-3-2; COV45: + R33-19's four)
    why = coverage.NOT_OBTAINABLE
    for mod in ("DamagePerPoisonOnSelfUnique__1_", "ChaosResistancePerPoisonOnSelfUnique__1",
                "MovementSpeedPerPoisonOnSelfUnique__1_", "LifeRegenerationWhileFrozenUnique__1"):
        assert why[mod].startswith("RESEARCH34 R33-19, MEDIUM-HIGH"), mod  # (PoE1 leftovers; the hand-wraps twins stay live)
    assert "R30-6" in why["HarvestAlternateWeaponQualityAreaOfEffect_"]
    assert "RePoE" in why["ActorSizeUniqueBeltDemigods1"] and "MEDIUM-HIGH" in why["ActorSizeUniqueBeltDemigods1"]
    for live in ("UniqueRecoupLifeOpenWeakness1", "HandWrapsUniqueRecoupLifeEnergyShieldOpenWeakness1",
                 "UniqueLoreweaveSekhemasResolveRuby1", "ItemCanAlsoRollRingMods", "RingImplicitMaximumQualityAdditional1",
                 "CharmImplicitUseOnCurse1", "GenerateEnduranceChargesForAlliesInPresence", "EssenceBreach"):
        assert live not in why, live
    assert not any(k.startswith("HandWraps") or k.startswith("Alloy") for k in why)
    report = coverage.audit()
    harvest = [r for r in report["records"] if r["source"] == "Exclusive HarvestAlternateWeaponQualityAreaOfEffect_"]
    # the unparsed line (PoB parses the other, "per 4% Quality", which the open counts never held)
    harvest = [r for r in harvest if r["line"] == "Quality does not increase Damage"]
    assert harvest and all(r["status"] == "unreachable" and "R30-6" in r["reason"] for r in harvest)


def test_r32_3_settled_unique_only_rows():
    """COV42 (RESEARCH32 R32-3): Prism of Belief's "+(1-3) to Level of all 0 Skills" (the game's placeholder for the
    skill name) is carried by the unique PoB builds per gem (Generated.lua, expanded in uniques.json), so it is PoB's;
    the two triggers granting "[DNT-UNUSED]" skills are unreachable; Rite of Passage's random Spirit stays open,
    its obtainability UNVERIFIED (R32-6 R-d corrected R32-3's HIGH: poe2db only); Demigod's Virtue's "Virtuous" is no effect."""
    assert coverage.placeholder_carried("+(1-3) to Level of all 0 Skills", frozenset({"# to Level of all Arc Skills"}))
    assert not coverage.placeholder_carried("+(1-3) to Level of all 0 Skills", frozenset({"# to Level of all Skills"}))
    assert not coverage.placeholder_carried("+1 to Level of all Spell Skills", frozenset({"# to Level of all Arc Skills"}))
    rows = {r["source"].split()[1]: r for r in coverage.audit()["records"]
            if r["category"] == "affix" and r["source"].startswith("Exclusive ")}
    assert rows["UniqueJewelSpecificSkillLevelBonus1"]["status"] == "pob"
    assert rows["UniqueJewelSpecificSkillLevelBonus2"]["status"] == "pob"
    for mod in ("UniqueTriggerGasCloudOnMainHandHit1", "UniqueTriggerDetonationOnOffHandHit1"):
        assert rows[mod]["status"] == "unreachable" and "DNT-UNUSED" in rows[mod]["reason"]
    rite = rows["UniqueCharmRandomPossess1"]  # (COV48: a carrier found, Rite of Passage on poe2db; still open: a model)
    assert rite["status"] == "none" and "a real carrier: Rite of Passage" in rite["reason"]
    assert "a PoE1 leftover" not in rite["reason"]
    assert rows["DemigodsVirtue1"]["status"] == "ledger"


def test_cov43_model_read_item_lines_are_the_models_own():
    """COV43: item lines a model already reads are model-read (the audit's "model", the search's real-trial re-score);
    each line is checked against the model's own pattern, so an entry can't outlive the model reading it. The lines a
    model reads without moving a number (a labelled 0, a skipped condition, a play row) stay open."""
    from builder.ledger import model_entry
    from builder.search import assumptions, boss_hits, conditions, flasks, models, recovery

    cases = {
        "Critical Hit chance is Lucky against Parried enemies": ("model-read-lucky-crit-against-parried", models.ULAMANS_GAZE),
        "Spear Skills inflict a Bloodstone Lance on Hit, up to a maximum of 30 on each target":
            ("model-read-bloodstone-lance-cap", models.SPITE_LANCE_LINE),
        "50% of Charges consumed by used Life Flasks are granted to your Charms":
            ("model-read-zerphi-charge-transfer", flasks.ZERPHI_TO_CHARM),
        "50% of Charges consumed by used Charms are granted to your Life Flasks":
            ("model-read-zerphi-charge-transfer", flasks.ZERPHI_TO_LIFE),
        "You cannot be Chilled for 6 seconds after being Chilled": ("model-read-chill-on-you", boss_hits.CHILL_IMMUNE_AFTER),
        "All Damage taken from Hits Contributes to Magnitude of Chill inflicted on you":
            ("model-read-chill-on-you", boss_hits.ALL_DAMAGE_CHILLS),
        "Grants up to your maximum Rage on use": ("model-read-rage-fill-on-use", conditions.RAGE_FILL_ON_USE),
        "Skills Cost Divinity instead of Mana or Life": ("model-read-divinity", recovery.DIVINITY_COST),
        "50% increased maximum Divinity": ("model-read-divinity", recovery.DIVINITY_INC),
        "20% reduced maximum Divinity per Corrupted Item Equipped": ("model-read-divinity", recovery.DIVINITY_PER_CORRUPTED),
        "Convert 100% of maximum Energy Shield to maximum Divinity": ("model-read-divinity", recovery.DIVINITY_CONVERT),
        "90% increased Chance to be afflicted by Ailments when Hit": ("model-read-afflicted-when-hit", boss_hits.SELF_AFFLICTED),
        "Poison you inflict is Reflected to you": ("model-read-drawbacks-on-you", recovery.REFLECT_POISON),
        "Take 50% of Mana Costs you pay for Skills as Physical Damage": ("model-read-drawbacks-on-you", recovery.COST_DAMAGE),
        "Inflicts a random Curse on you when your Totems die, ignoring Curse limit":
            ("model-read-drawbacks-on-you", recovery.SOUL_MANTLE),
        "Damage over Time bypasses your Energy Shield": ("model-read-drawbacks-on-you", assumptions.DOT_BYPASSES_ES_LINE),
        "Double Stun Threshold while Shield is Raised": ("model-read-raised-shield-stun-threshold", recovery.RAISED_DOUBLE),
        "Life Recovery from Flasks is instant": ("model-read-life-flask-instant", flasks.LIFE_FLASK_INSTANT),
        "Recovery from Life Flasks cannot be Instant": ("model-read-life-flask-instant", flasks.LIFE_NEVER_INSTANT),
        "If you would gain a Power Charge, Allies in your Presence gain that Charge instead":
            ("model-read-charges-to-allies", conditions.CHARGE_TO_ALLIES),
        "If you would gain a Charge, Allies in your Presence gain that Charge instead":
            ("model-read-charges-to-allies", conditions.CHARGE_TO_ALLIES),
        "Allies in your Presence share Charges with you": ("model-read-charges-to-allies", conditions.CHARGE_SHARE_BACK),
        "Using a Mana Flask grants Guard equal to 100% of Flask's recovery amount for 4 seconds":
            ("model-read-guard-sources", recovery.MANA_FLASK_GUARD),
        "Also grants 150 Guard": ("model-read-guard-sources", recovery.CHARM_GUARD),
        "Gain Maximum Energy Shield equal to 50% of total": ("model-read-emergent-vigour", recovery.VIGOUR_FIRST),
        "Strength Requirements of Equipped Armour Items": ("model-read-emergent-vigour", recovery.VIGOUR_SECOND),
        "Removes 15% of Mana Recovered from Life when used": ("model-read-flask-cross-cost", flasks.CROSS_COST),
        "Removes 15% of Life Recovered from Mana when used": ("model-read-flask-cross-cost", flasks.CROSS_COST),
    }
    for line, (entry, pattern) in cases.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.search(line), line
    # ("20% increased Guard gained" left this list in COV44: train 89's event_guard scales every Guard source)
    for line in ("Grants effect of Guided Meteoric Shrine",
                 "Create a Fragment of Divinity in your Presence every 4 seconds"):  # (Lioneye's +2 m: read, COV45)
        assert (found := model_entry(line)) is None or not found.id.startswith("model-read-"), line


def test_cov43_not_dodge_rolled_lines_share_the_mech88_condition():
    """COV43: the two "if you haven't Dodge Rolled Recently" rune lines are `share:` lines at NotDodgeRolledRecently (MECH88's
    condition kind not_dodge_rolled_recently), whose shown_for also finds the item stand-in (a `share:` line replaces its
    line on items, bridge sourceLines' "LedgerShare:<Name>")."""
    import re

    from builder.ledger import conditions, translate

    for line, plain in (("30% increased Armour if you haven't Dodge Rolled Recently", "30% increased Armour"),
                        ("15% increased Mana Cost Efficiency if you haven't Dodge Rolled Recently",
                         "15% increased Mana Cost Efficiency")):
        assert translate(line)[1] == [f"share:NotDodgeRolledRecently {plain}"]
    condition = next(c for c in conditions() if c["id"] == "NotDodgeRolledRecently")
    assert condition.get("not_dodge_rolled_recently")
    assert re.search(condition["shown_for"], "LedgerShare:NotDodgeRolledRecently")
    assert re.search(condition["shown_for"], "30% increased Armour if you haven't Dodge Rolled Recently")


def test_cov43_model_read_passive_lines_are_the_models_own():
    """COV43: passive-tree lines a model already reads are model-read (passives-cov43.yaml), each checked against the
    model's own pattern; the lines a model reads without moving a number stay open. (Grinning Immolation's line was
    open until MECH89 counted its burn: model-read since, test_mech89_reads_are_model_read.)"""
    from builder.ledger import model_entry
    from builder.search import conditions, models, recovery

    cases = {
        "Gain Power Charges instead of Frenzy Charges": ("model-read-charges-instead", conditions.CHARGE_INSTEAD),
        "There is no Limit on the number of Banners you can place": ("model-read-banner-limit", conditions.BANNER_UNLIMITED),
        "+1 to maximum number of placed Banners": ("model-read-banner-limit", conditions.BANNER_MORE),
        "40% increased Jagged Ground Duration": ("model-read-jagged-ground-duration", conditions.JAGGED_DURATION_LINE),
        "Overgrown Plant Skills Break 50% increased Armour": ("model-read-overgrown-break", conditions.BREAK_OVERGROWN),
        "Consume all Rage when Shapeshifting to Human form to recover 1% of maximum life per Rage Consumed":
            ("model-read-invigorating-hate", conditions.INVIGORATING_HATE),
        "The next Fire Spell you cast yourself after using a Warcry is Ancestrally Boosted":
            ("model-read-kaoms-blessing", models.KAOMS_BLESSING),
        "Skills gain 1 Glory every 2 seconds for each Rare or Unique monster in your Presence":
            ("model-read-glory-presence-banner-refund", models.GLORY_PRESENCE),
        "When a Banner expires, recover 15% of the Glory required for that Banner":
            ("model-read-glory-presence-banner-refund", models.GLORY_BANNER_REFUND),
        "200% faster start of inherent Rage loss": ("model-read-rage-loss", models.RAGE_START_RATE),
        "Inherent loss of Rage is 2% slower per 10 Tribute": ("model-read-rage-loss", models.RAGE_TRIBUTE),
        "15% increased Effect of Puppet Master": ("model-read-puppet-master-effect-duration", models.PUPPET_EFFECT),
        "25% increased Duration of each Puppet Master stack": ("model-read-puppet-master-effect-duration", models.PUPPET_DURATION),
        "Gain a stack of Jade every second": ("model-read-jade-heritage", recovery.JADE_LINE),
        "Sacrifice 5% of maximum Energy Shield when you Cast a Spell": ("model-read-sacrifice-es-on-cast", recovery.SACRIFICE_ES),
        "All Flames of Chayula that you manifest are Purple": ("model-read-chayula-flame-colour", recovery.FLAME_COLOUR),
        "Recover 3% of maximum Life when you create an Offering": ("model-read-recover-per-event", recovery.OFFERING_LIFE),
        "Recover 1% of maximum Life per Glory consumed": ("model-read-recover-per-event", recovery.GLORY_SPENT),
    }
    for line, (entry, pattern) in cases.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.search(line), line
    for line in ("Take 20% less Damage from Hits while Dodge Rolling",
                 "Allies in your Presence Regenerate 5 Rage per second if you have gained Rage Recently",
                 "-0.2 seconds to current Energy Shield Recharge delay per Combo expended when using Skills"):
        assert (found := model_entry(line)) is None or not found.id.startswith("model-read-"), line


def test_loreweave_jewel_socket_limits_are_handled_by_the_search():
    """COV43 (the manager 14:25, SEARCH28): Loreweave's "You can only Socket 1 <colour> Jewel in this item" lines are
    no-effect: the search offers only the plain Loreweave (its ring-effect variant lines dropped) and socket jewels only
    in the tree. The rings' own "You can only Socket <colour> Jewels in this item" is another line, untouched."""
    from builder.ledger import translate
    from builder.search import jewels, loreweave

    for colour in ("Emerald", "Ruby", "Sapphire"):
        found = translate(f"You can only Socket 1 {colour} Jewel in this item")
        assert found is not None and found[0].id == "no-effect-loreweave-jewel-socket-limits" and found[1] == []
        assert translate(f"You can only Socket {colour} Jewels in this item") is None
    raw = "Loreweave\nOrnate Ringmail\n{variant:3}You can only Socket 1 Ruby Jewel in this item\nVariant: Ruby"
    assert "Socket" not in loreweave.plain(raw)
    assert jewels.Jewels.slot(26725) == "Jewel 26725"


def test_passive_lines_handled_elsewhere_are_no_effect():
    """COV43: Runic Meridians' socket list (PoB builds the five rune slots from the first line's flag) and Smith's
    Masterwork's "Can only use a Normal Body Armour" (the search's SLOT_RESTRICTIONS) are no-effect entries; the
    restriction's own text is kept for the search (a passive translation adds lines, it never removes the original)."""
    from builder.ledger import translate
    from builder.search import gear

    for line in ("additional Rune-only sockets:", "1 Helmet socket", "2 Body Armour sockets", "1 Gloves socket",
                 "1 Boots socket"):
        found = translate(line)
        assert found is not None and found[0].id == "no-effect-runic-meridians-socket-list" and found[1] == [], line
    assert translate("Can tattoo Runes onto your body, gaining") is None  # (PoB's own flag line)
    found = translate("Can only use a Normal Body Armour")
    assert found is not None and found[0].id == "no-effect-smiths-masterwork-normal-body-armour" and found[1] == []
    assert gear.SLOT_RESTRICTIONS["Can only use a Normal Body Armour"] == ("Body Armour", "normal")


def test_way_of_the_stonefist_lines_are_the_transforms_own():
    """COV43: Way of the Stonefist's three lines are no-effect: the search's glove transform (stonefist.py) does them, and
    Fists of Stone has no attribute requirement (so "Ignore Attribute Requirements to equip Gloves" holds)."""
    from builder.ledger import translate
    from builder.search import stonefist

    for line in ("Gloves you equip have their Base Type transformed to Fists of Stone while equipped, and",
                 "their Explicit Modifiers are transformed into more powerful related Modifiers",
                 "Ignore Attribute Requirements to equip Gloves"):
        found = translate(line)
        assert found is not None and found[0].id == "no-effect-way-of-the-stonefist-lines" and found[1] == [], line
    assert stonefist.NOTABLE == "Way of the Stonefist" and stonefist.BASE == "Fists of Stone"
    found = translate("+5 to any Attribute")  # (the attribute node's placeholder: the bridge picks the attribute)
    assert found is not None and found[0].id == "no-effect-any-attribute-placeholder" and found[1] == []


def test_mech89_reads_are_model_read():
    """COV43 (MECH89, train 85): Corrosion's, Mark for Death's and Brittle Armour's Armour Break stats and Grinning
    Immolation's line (its burn now counted) are model-read; each stat is one the readers know."""
    from builder.ledger import coverage, model_entry
    from builder.search import conditions

    covered = coverage.stat_entries()
    for stat in ("armour_break_for_%_of_poison_damage_over_poison_duration", conditions.MARK_BREAK_STAT,
                 conditions.BRITTLE_BREAK_STAT):
        assert coverage.stat_covered(covered, stat, "Mark for Death II") == ("model", "model-mech89-conditional-and-poison-armour-break")
    assert "armour_break_for_%_of_poison_damage_over_poison_duration" in conditions.BREAK_STATS
    line = ("Become Ignited when you deal a Critical Hit, taking 15% of your maximum Life and Energy Shield as Fire Damage "
            "per second")
    assert (found := model_entry(line)) is not None and found.id == "model-read-grinning-immolation"
    assert conditions.GRINNING_BURN.search(line) and conditions.SELF_IGNITE_LINE.search(line)


def test_cov44_model_read_item_lines_are_the_models_own():
    """COV44: MECHC40's readers (f43a1167) make Shackles of the Wretched's Ignite / Shock windows and Lochtonial Caress's
    "Share Charges with Allies in your Presence" model-read, each tied to the model's own pattern. The Frozen window
    stays open: IMMUNE_AFTER matches it, but nothing reads it (the boss's Freeze on you is held back)."""
    from builder.ledger import model_entry
    from builder.search import boss_hits, conditions

    cases = {
        "You cannot be Ignited for 6 seconds after being Ignited": ("model-read-ignite-shock-immune-after", "Ignited"),
        "You cannot be Shocked for 6 seconds after being Shocked": ("model-read-ignite-shock-immune-after", "Shocked"),
    }
    for line, (entry, ailment) in cases.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert boss_hits.immune_after([line], ailment) == 6.0, line
    line = "Share Charges with Allies in your Presence"
    assert (found := model_entry(line)) is not None and found.id == "model-read-share-charges-with-allies"
    assert conditions.SHARE_TO_ALLIES.match(line)
    # (after train 89: MECHC40's boss Freeze on you reads the Frozen window too)
    frozen = "You cannot be Frozen for 6 seconds after being Frozen"
    assert (found := model_entry(frozen)) is not None and found.id == "model-read-ignite-shock-immune-after"
    assert boss_hits.immune_after([frozen], "Frozen") == 6.0


def test_cov44_guard_lines_are_the_models_own():
    """COV44 (MECHC40's a792074c, train 89): "increased Guard gained" scales every modelled Guard source (PoB sets none),
    and the Savage Hit, Warding Rune timer and Heavy Drinker Guard lines are event_guard's; each tied to its pattern."""
    from builder.ledger import model_entry
    from builder.search import recovery

    cases = {
        "20% increased Guard gained": ("model-read-guard-gained", recovery.GUARD_GAINED),
        "8% increased Guard gained": ("model-read-guard-gained", recovery.GUARD_GAINED),
        "Gain Guard equal to 10% of maximum Life for 4 seconds on taking Savage Hit":
            ("model-read-event-guard", recovery.SAVAGE_GUARD),
        "Every 4 seconds, gain Guard equal to 20% of maximum Runic Ward for 2 seconds":
            ("model-read-event-guard", recovery.WARD_TIMER_GUARD),
        "Life Flasks applied to you grant Guard for 4 seconds equal to 8% of the Life Recovery per Second they apply":
            ("model-read-heavy-drinker-guard", recovery.DRINKER_GUARD),
    }
    for line, (entry, pattern) in cases.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    assert recovery._guard_inc(["20% increased Guard gained", "8% increased Guard gained"]) == 28
    for line in ("When you stop Sprinting, gain Guard equal to 4% of maximum Life per second spent Sprinting, up to a "
                 "maximum of 20%, for 4 seconds",):  # (Duality's Combo Guard: model-read since train 90, MECHC41)
        assert (found := model_entry(line)) is None or not found.id.startswith("model-read-"), line


def test_necromantic_talisman_line_is_the_nodes_own():
    """COV44: Necromantic Talisman's line is a no-effect entry: PoB does the keystone by its node (39935) and
    bridge.sourceLines follows it (tests/test_engine_item_lines.py's Talisman case)."""
    from builder.ledger import translate

    found = translate("All bonuses from Equipped Amulet apply to your Minions instead of you")
    assert found is not None and found[0].id == "no-effect-necromantic-talisman-amulet-to-minions" and found[1] == []


def test_bridge_set_conditions_count_as_set():
    """COV44: a condition the bridge sets by name (`conditions["X"] =` or `NewMod("Condition:X", ...`: MECH90's
    helmetConditions, Tolerant Equipment's two Helmet conditions) counts as set by the calculations, so its lines leave
    the never-on list; a name the bridge only reads, or a prefix it concatenates, doesn't."""
    from builder.ledger import parse_audit

    exact = parse_audit.calc_setters()["exact"]
    assert {"Condition:HelmetArmourHigherThanEvasion", "Condition:HelmetEvasionHigherThanArmour"} <= exact
    assert "Condition:Frozen" not in exact  # (a Configuration option's, which the optimizer leaves off)
    assert "Condition:Ledger" not in exact  # (the ledger's "Condition:Ledger" .. name: a prefix)


def test_rending_apex_final_strike_break_is_model_read():
    """COV44 (MECH89's d502bdc4, train 88): Rending Apex's Final Strike Armour Break stat is model-read, and is the stat
    the reader takes."""
    from builder.ledger import coverage
    from builder.search import conditions

    stat = "final_strike_attack_damage_%_dealt_as_armour_break"
    assert conditions.FINAL_BREAK_STAT == stat
    assert coverage.stat_covered(coverage.stat_entries(), stat, "Rending Apex") == (
        "model", "model-mech89-rending-apex-final-strike-break")


def test_mechc40_guard_stats_are_model_read():
    """COV44 (MECHC40's a792074c, train 89): Fortifying Cry's and Vanguard's Guard stats the event-Guard model reads are
    model-read; Vanguard's plain-Stun stat, which it leaves out, stays open."""
    from builder.ledger import coverage
    from builder.search import recovery

    covered = coverage.stat_entries()
    for stat in (recovery.FORTIFY_GUARD_STAT, "shielding_cry_grant_X_guard_per_5_mp_mp_cap"):
        assert coverage.stat_covered(covered, stat, "Fortifying Cry") == ("model", "model-mechc40-fortifying-cry-guard")
    for skill in recovery.VANGUARD:
        for stat in ("gain_X_guard_on_heavy_stun_per_10_strength", "guard_on_stun_guard_duration_ms"):
            assert coverage.stat_covered(covered, stat, skill) == ("model", "model-mechc40-vanguard-heavy-stun-guard")
        assert coverage.stat_covered(covered, "gain_X_guard_on_stun_per_10_strength", skill) is None
    assert recovery.FORTIFY_POWER_CAP == 20 and recovery.VANGUARD == {"Vanguard I": (4.0, 2.0), "Vanguard II": (6.0, 2.0)}


def test_mechc41_item_guard_lines_are_model_read():
    """COV44 (MECHC41's 233c4576, train 90): Duality's, Olroth's Resolve's and Decree of Flight's Guard lines are
    event_guard's (model-read), each tied to its pattern."""
    from builder.ledger import model_entry
    from builder.search import recovery

    cases = {
        "Gain 750 Guard for 0.5 seconds per Combo expended when using Skills": recovery.COMBO_GUARD,
        "Gain 250 Guard for 0.5 seconds per Combo expended when using Skills": recovery.COMBO_GUARD,
        "Gain Guard equal to Current Runic Ward for 10 seconds when Effect ends": recovery.WARD_END_GUARD,
        "Gain Guard equal to 15% of missing Energy Shield for 4 seconds when you Dodge Roll": recovery.DECREE_GUARD,
        "Maximum amount of Guard is based on maximum Energy Shield instead": recovery.DECREE_CAP,
    }
    for line, pattern in cases.items():
        assert (found := model_entry(line)) is not None and found.id == "model-read-mechc41-item-guard", line
        assert pattern.match(line), line


def test_train91_reads_are_model_read():
    """COV44 (train 91): MECH90's Presence-Recently and Emergent Instinct readers, MECHC41's Intricate Sigils, Citaqualotl's
    Thesis, Nature's Exchange and Helbrym's Hide readers are model-read; Helbrym's Rage stat (not counted) stays open."""
    from builder.ledger import coverage, model_entry
    from builder.search import conditions, flasks, recovery

    lines = {
        "50% increased Critical Damage Bonus against Enemies that have exited your Presence Recently":
            ("model-read-train91-passive-lines", conditions.PRESENCE_RECENTLY),
        "12% increased Critical Hit Chance against Enemies that have entered your Presence Recently":
            ("model-read-train91-passive-lines", conditions.PRESENCE_RECENTLY),
        "20% more Charm Charges gained": ("model-read-train91-passive-lines", flasks.CHARM_GAIN_MORE),
        "Targets that are Blinded, Maimed, and Bleeding cannot Evade your Hits":
            ("model-read-train91-item-lines", conditions.EMERGENT_LINE),
        "Sacrifice 10% of maximum Life to gain that much Guard when you Dodge Roll":
            ("model-read-train91-item-lines", recovery.CITAQUALOTL_GUARD),
    }
    for line, (entry, pattern) in lines.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    covered = coverage.stat_entries()
    assert coverage.stat_covered(covered, recovery.NATURES_GUARD_STAT, "Nature's Exchange") == (
        "model", "model-mechc41-natures-exchange-guard")
    assert "consume_enemy_freeze_to_trigger_morrigan_ward" in conditions.CONSUME_STATS["Freeze"]
    for stat in ("support_hide_of_helbrym_gain_guard_from_%_of_damage_taken", "support_hide_of_helbrym_guard_duration_ms",
                 "support_hide_of_helbrym_soak_duration_ms"):
        assert coverage.stat_covered(covered, stat, "Helbrym's Hide") == ("model", "model-mechc41-helbryms-hide-guard")
    assert coverage.stat_covered(covered, "support_hide_of_helbrym_gain_rage_from_%_of_damage_taken", "Helbrym's Hide") is None
    assert (recovery.HELBRYM_SHARE, recovery.HELBRYM_SOAK, recovery.HELBRYM_SECONDS) == (0.5, 2.0, 4.0)


def test_damage_type_conditions_count_as_set():
    """COV45 (from MECH90): CalcOffence sets Condition:<Type>HasDamage for every type a hit deals (4491) and
    <Type>IsHighestDamageType (4501), both over its dmgTypeList: "with Lightning Damage" lines are not never-on."""
    from builder.ledger import parse_audit

    exact = parse_audit.calc_setters()["exact"]
    for kind in ("Physical", "Lightning", "Cold", "Fire", "Chaos"):
        assert f"Condition:{kind}HasDamage" in exact and f"Condition:{kind}IsHighestDamageType" in exact
    assert "Condition:HasDamage" not in exact


def test_cultivated_outcome_translations_count_where_the_trial_applies_them():
    """COV45 (from MECH90's catalog note): a live Cultivated outcome whose unread line has an item stand-in is offered by
    the search's Cultivation trial with that stand-in (cultivated.outcome_lines), so the audit counts it; a Desecrated
    copy of the same line (never rolled by the search) stays open."""
    from builder.ledger import coverage
    from builder.search import cultivated

    offered = coverage.cultivated_scored_mods()
    assert {"UniqueMutatedVaalChanceToBleed", "UniqueMutatedVaalAftershockChance",
            "UniqueMutatedVaalMaximumRagePerGlorySkillUsed"} <= offered
    bleed = next(o for o in cultivated.outcomes("Atziri's Acuity") if o["mod"] == "UniqueMutatedVaalChanceToBleed")
    assert "(30-50)% increased to inflict Bleeding" in cultivated.outcome_lines(bleed)
    records = {r["source"]: r for r in coverage.audit()["records"]
               if r["source"] in ("Exclusive UniqueMutatedVaalChanceToBleed", "Desecrated AbyssModGlovesUlamanSuffixBleedChance")}
    assert records["Exclusive UniqueMutatedVaalChanceToBleed"]["status"] == "ledger"
    assert records["Desecrated AbyssModGlovesUlamanSuffixBleedChance"]["status"] == "none"


def test_train92_reads_and_play_rows():
    """COV45 (train 92; the manager's 19 Nov ask from MECHC42): Last Stand, Deafening Cries and Lycosidae are model-read
    by the models that feed the headline; Bulwark and the sprint lines are read by play rows only: the audit closes them
    (play_row_entry) but model_entry leaves them out, so the search doesn't re-score items for them. The sprint row's
    named-only lines stay open."""
    from builder.ledger import model_entry, play_row_entry
    from builder.search import assumptions, flasks, models, recovery

    read = {
        "25% increased Attack Damage while you have no Life Flask uses left": ("model-read-mechc42-last-stand", flasks.NO_LIFE_USES),
        "8% increased Damage for each time you've Warcried Recently": ("model-read-mechc42-deafening-cries", assumptions.WARCRIED_EACH),
        "Blocking Damage Poisons the Enemy as though dealing 200 Base Chaos Damage":
            ("model-read-mech90-lycosidae-block-poison", models.BLOCK_POISON),
    }
    for line, (entry, pattern) in read.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    rows = {
        "Take 30% less Damage from Hits while Dodge Rolling": ("play-row-bulwark-rolling-less", recovery.ROLLING_LESS),
        "25% increased Movement Speed while Sprinting": ("play-row-sprint-lines", recovery.SPRINT_SPEED),
        "100% increased Evasion Rating while Sprinting": ("play-row-sprint-lines", recovery.SPRINT_EVASION),
        "Lose 5% of maximum Life per second while Sprinting": ("play-row-sprint-lines", recovery.SPRINT_LIFE_LOSS),
        "When you stop Sprinting, gain Guard equal to 4% of maximum Life per second spent Sprinting, up to a maximum of "
        "40%, for 4 seconds": ("play-row-sprint-lines", recovery.SPRINT_GUARD),
    }
    for line, (entry, pattern) in rows.items():
        assert model_entry(line) is None, line
        assert (found := play_row_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    assert not models.item_read_by_models("Take 30% less Damage from Hits while Dodge Rolling", [])
    for line in ("Cannot be Heavy Stunned while Sprinting", "Your speed is Unaffected by Slows while Sprinting",
                 "50% increased Runic Ward Regeneration Rate while Sprinting"):
        assert model_entry(line) is None and play_row_entry(line) is None, line


def test_warcry_power_stat_is_read_by_its_buff_tag():
    """COV45 (MECHB41's warcry stats): warcry_count_power_from_enemies maps to a flag only the Configuration page names,
    but its GlobalEffect Warcry tag makes PoB's Warcry buff (Power, Empowers, uptime): counted as PoB's."""
    from builder.ledger import coverage

    assert "warcry_count_power_from_enemies" in coverage.BUFF_MAKING_STATS
    stat_map = (coverage.POB_SRC / "Data" / "SkillStatMap.lua").read_text(encoding="utf-8", errors="replace")
    assert 'flag("UsesWarcryPower", { type = "GlobalEffect", effectType = "Warcry" })' in stat_map


def test_explicit_modifier_magnitudes_raise_the_items_tagged_lines():
    """COV45 (the manager 16:00, 19 Nov): Thrud's "N% increased Explicit <type> Modifier magnitudes" raises the item's own
    explicit lines whose mod carries the tag, as stand-in copies of their extra (rounded down); other lines, implicits and
    the line itself are untouched; the line is model-read."""
    from builder.ledger import items, model_entry

    raw = "\n".join(["Rarity: Rare", "Optimizer Weapon", "Expert Dualstring Bow", "Item Level: 82", "Implicits: 1",
                     "+1.5% to Critical Hit Chance", "+1.5% to Critical Hit Chance", "Adds 10 to 21 Fire Damage",
                     "10% increased Attack Speed", "25% increased Explicit Critical Modifier magnitudes",
                     "20% increased Explicit Fire Modifier magnitudes"])
    assert items.magnitude_lines(raw.split("\n")) == ["+0.37% to Critical Hit Chance", "Adds 2 to 4 Fire Damage"]
    assert items.magnitude_lines(raw.replace("25% increased Explicit Critical Modifier magnitudes", "")
                                 .replace("20% increased Explicit Fire Modifier magnitudes", "").split("\n")) == []
    # R34-9 (COV46): a line the game's scalability data marks unscalable is not raised; "+3 to Level" truncates to no
    # change at a reachable magnitude.
    lines = raw.split("\n")
    lines[lines.index("+1.5% to Critical Hit Chance") + 1] = "Socketed Gems have 10% Critical Hit Chance"
    assert items.magnitude_lines(lines) == ["Adds 2 to 4 Fire Damage"]  # the implicit crit line is never raised
    lines = raw.replace("Adds 10 to 21 Fire Damage", "+3 to Level of all Fire Spell Skills").replace("20%", "8%").split("\n")
    assert items.magnitude_lines(lines) == ["+0.37% to Critical Hit Chance"]
    assert model_entry("25% increased Explicit Critical Modifier magnitudes").id == "model-read-explicit-modifier-magnitudes"
    assert model_entry("(3-5)% increased Cast Speed for each different Non-Instant Spell you've Cast Recently").id == (
        "model-read-mechb4-non-instant-spells-recently")


def test_lightning_stun_buildup_line_is_labelled_without_a_known_carrier():
    """COV45 (the manager 18:15, R34-6): "(50-100)% more Stun Buildup with Lightning Damage" has no league carrier that
    research could show (UNVERIFIED, not a PoE1 leftover): labelled so, status kept."""
    from builder.ledger import coverage

    records = [r for r in coverage.audit()["records"]
               if r["source"] == "Exclusive UniqueTwoHandedWeaponLightningStunMultiplier1"]
    assert records and all(r.get("no_known_carrier") and "R34-6" in r["reason"] for r in records)
    assert not any(r["reason"].startswith("a PoE1 leftover") or r["status"] == "unreachable" for r in records)


def test_train93_reads_and_play_rows():
    """COV45 turn 3 (train 93; the manager 18:15): MECH90's Engineered Blaze, Inner Turmoil's Volatility and Saitha's Spear,
    MECHC42's Killed Recently lines, Frozen regen and Mana lost on Kill are model-read by their headline models; Armour
    while Frozen / Bleeding, the raised-shield max Block and Immaterial are play rows (no item re-scoring); Growing Peril
    and Precise Volatility were not counted by the model then (model-read since COV47); the Energy Shield on-Kill twin stays open."""
    from builder.ledger import model_entry, play_row_entry
    from builder.search import models, recovery

    read = {
        "4% increased Area of Effect for Attacks per Enemy you've Ignited in the last 8 seconds, up to 16%":
            ("model-read-mech90-engineered-blaze", models.ENGINEERED_BLAZE),
        "Gain 1 Volatility on inflicting an Elemental Ailment": ("model-read-mech90-inner-turmoil-volatility", models.INNER_TURMOIL),
        "Maximum Volatility is 30": ("model-read-mech90-inner-turmoil-volatility", models.VOLATILITY_MAX),
        "15% reduced Volatility Explosion delay": ("model-read-mech90-inner-turmoil-volatility", models.VOLATILITY_DELAY),
        "Aggravating any Bleeding with this Weapon also Aggravates all Ignites on the target":
            ("model-read-mech90-saithas-spear", models.SAITHA_LINE),
        "+2 metres to Dodge Roll distance if you haven't Dodge Rolled Recently":
            ("model-read-mech89-conditional-dodge-roll-distance", models.DODGE_DISTANCE),
        "8% increased Attack Speed if you've killed Recently": ("model-read-mechc42-killed-recently", recovery.KILLED_RECENTLY),
        "Regenerate 10% of maximum Life per second while Frozen": ("model-read-mechc42-frozen-regen", recovery.FROZEN_REGEN),
        "Lose 1% of maximum Mana on Kill": ("model-read-mechc42-mana-lost-on-kill", recovery.KILL_LOSS),
    }
    for line, (entry, pattern) in read.items():
        assert (found := model_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    rows = {
        "+500 to Armour while Frozen": ("play-row-mechc42-armour-while-frozen-or-bleeding", recovery.FROZEN_ARMOUR),
        "30% increased Armour while Bleeding": ("play-row-mechc42-armour-while-frozen-or-bleeding", recovery.BLEEDING_ARMOUR),
        "+5% to maximum Block chance if you've Blocked with a raised Shield Recently":
            ("play-row-mechc42-raised-shield-max-block", recovery.RAISED_MAX_BLOCK),
        "50% increased Evasion Rating if Energy Shield Recharge has started in the past 2 seconds":
            ("play-row-mechc42-immaterial", recovery.RECHARGE_STARTED_EVASION),
    }
    for line, (entry, pattern) in rows.items():
        assert model_entry(line) is None and (found := play_row_entry(line)) is not None and found.id == entry, line
        assert pattern.match(line), line
    # (COV47) Growing Peril and Precise Volatility are counted since MECH91 / MECH92 (volatility_model emits "1% increased
    # Chaos Damage over Time" x Volatility held and "1% increased Critical Hit Chance" x the powered share), so they're
    # model-read now (items-cov47.yaml); the Energy Shield on-Kill twin stays open
    for line in ("Lose 1% of maximum Energy Shield on Kill",):
        assert model_entry(line) is None and play_row_entry(line) is None, line
    for line in ("1% increased Chaos Damage over Time per Volatility",
                 "Volatile Power also grants 1% increased Critical Hit chance per Volatility exploded"):
        assert model_entry(line).raw.get("model") == "volatility", line


def test_train94_reads():
    """COV45 (train 94, MECHC43's 96ec2ef8): skill_can_add_multiple_charges_per_action is model-read (charge lines joined
    one a use without it); Damage per Poison on you and Life-spent regen are model-read; Chaos Resistance per Poison on you
    is a play row; the Movement Speed twin stays open."""
    from builder.ledger import coverage, model_entry, play_row_entry
    from builder.search import conditions, recovery

    assert conditions.MULTI_CHARGE_STAT == "skill_can_add_multiple_charges_per_action"
    assert coverage.stat_covered(coverage.stat_entries(), conditions.MULTI_CHARGE_STAT, "Orb of Storms") == (
        "model", "model-mechc43-multiple-charges-per-action")
    # (COV46: R33-17 settled HIGH by the user's in-game tests, 20 Nov 2026 ~03:45 / ~04:00)
    from builder.ledger import entries
    assert next(e for e in entries() if e.id == "model-mechc43-multiple-charges-per-action").raw["confidence"] == "HIGH"
    for line, entry, pattern in (
            ("15% increased Damage for each Poison on you up to a maximum of 75%", "model-read-mechc43-damage-per-poison-on-you",
             recovery.PER_POISON_DAMAGE),
            ("Regenerate 1 Life per second per 10 Life spent in the past 4 seconds", "model-read-mechc43-life-spent-regen",
             recovery.LIFE_SPENT_REGEN)):
        assert (found := model_entry(line)) is not None and found.id == entry and pattern.match(line), line
    line = "+12% to Chaos Resistance per Poison on you"
    assert model_entry(line) is None and play_row_entry(line).id == "play-row-mechc43-chaos-resistance-per-poison-on-you"
    assert recovery.PER_POISON_CHAOS.match(line)
    speed = "17% increased Movement Speed for each Poison on you up to a maximum of 50%"
    assert model_entry(speed) is None and play_row_entry(speed) is None


def test_verisium_manifestations_is_a_modelled_skill():
    """COV45 (train 94, MECHB42's bae8fb78): Verisium Manifestations, 0 in PoB alone before, is scored by the bridge's
    minion and models.verisium_model."""
    from builder.ledger import coverage
    from builder.search import models

    assert coverage.MODELED_SKILLS["Verisium Manifestations"] == "model:verisium" and models.VERISIUM == "Verisium Manifestations"


def test_cov46_stonefist_unique_lines_and_idol_of_egrin_are_counted():
    """COV46: a hand-wraps line of a unique Way of the Stonefist transforms is applied there (stonefist.ledger_corrected),
    so Painter's Servant's two halves are "ledger", not "the search doesn't apply it" / "no unique carries it"; Idol of
    Egrin's curse-zone delay is the bridge's CurseDelay parse (MECH90, train 95), absorbed with an empty replacement."""
    from builder.ledger import coverage, translate

    records = [r for r in coverage.audit()["records"]
               if r["source"] == "Exclusive HandWrapsUniqueElementalDamageConvertToFire1"]
    assert len(records) == 2 and all(r["status"] == "ledger" for r in records)
    assert any("Stonefist" in r["reason"] for r in records if r["line"].startswith("Physical damage from Hits"))
    assert translate("Curse zones erupt after 20% reduced delay")[1] == []
    assert translate("Curse zones erupt after 20% reduced delay")[0].raw.get("absorbed_by")


def test_cov46_a_config_box_whose_placeholder_pob_applies_is_not_never_on():
    """COV46 (the manager 23:35, from MECHB44's Full Salvo find): PoB applies a number box's defaultPlaceholderState
    while its input is unset (ConfigTab BuildModList), so the Full Salvo / Demolitionist lines read 1 ammo / grenade type,
    not 0: not never-on. A box whose mods need another condition box (Stunned Recently) is not counted as applied."""
    from builder.ledger.parse_audit import Setters, config_options

    options = config_options()
    assert options["multiplierDifferentGrenadeFired"]["placeholder"] == 1
    assert options["multiplierWitheredStackCountSelf"]["placeholder"] == 15
    assert options["multiplierStunnedRecently"]["placeholder"] is None  # gated on Condition:StunnedRecently
    assert options["demonFormStacks"]["placeholder"] is None  # gated on Condition:DemonForm
    setters = Setters({})
    kind, detail = setters.status("Multiplier:DifferentAmmoFired")
    assert kind == "config-on" and "placeholder 1" in detail
    assert setters.status("Multiplier:StunnedRecently")[0] != "config-on"


def test_cov46_r35_3_and_mechb44_skill_rows():
    """COV46: R35-3's evidence on the DNT Bleed-on-Shocked mod (unreachable); Elemental Storm a modelled skill; Eternal
    March, Sacrifice and Skeletal Cleric deal no damage of their own (game stat sets), so PoB's 0 is right."""
    from builder.ledger import coverage

    records = coverage.audit()["records"]
    dnt = [r for r in records if r["source"] == "Exclusive UniqueBleedingInflictedOnShockedEnemiesIsAggravated1"]
    assert dnt and all(r["status"] == "unreachable" and "R35-3" in r["reason"] for r in dnt)
    zero = {r["line"]: r for r in records if r["category"] == "skill-zero"}
    for name in ("Eternal March", "Sacrifice", "Skeletal Cleric"):
        assert name not in zero or zero[name]["status"] == "pob", name
    assert "Elemental Storm" not in zero or zero["Elemental Storm"]["status"] == "model"


def test_cov46_mech91_readers_are_model_read_and_their_cultivated_outcomes_offered():
    """COV46 (train 96, MECH91's cb67ea7c): the Vaal Cultivation outcomes "chance to grant Volatility on Critical Hit",
    "Volatility Physical Damage Taken as Cold" and "Poison duration after a Frenzy Charge" are model-read through empty
    pob_gap translations, so the search's Cultivation trial offers them (cultivated.outcome_lines needs a translation for
    every line PoB doesn't read); the Volatility support's stat is model-read."""
    from builder.ledger import coverage, model_entry, translate
    from builder.search import cultivated

    for line, entry in (("40% chance to grant Volatility on Critical Hit", "model-read-mech91-volatility-on-critical-hit"),
                        ("75% of Volatility Physical Damage Taken as Cold Damage", "model-read-mech91-volatility-taken-as-cold"),
                        ("35% increased Duration of Poisons you inflict when you've consumed a Frenzy Charge Recently",
                         "model-read-mech91-poison-duration-after-frenzy")):
        assert model_entry(line).id == entry and translate(line)[1] == []
    offered = {o["mod"] for name in cultivated.uniques() for o in cultivated.outcomes(name)}
    for mod in ("UniqueMutatedVaalVolatilityOnCritChance", "UniqueMutatedVaalVolatilityDamageTakenAsCold",
                "UniqueMutatedVaalPoisonDurationIfConsumedFrenzy"):
        assert any(o.startswith(mod) for o in offered), mod
    assert coverage.stat_covered(coverage.stat_entries(), "instability_on_critical_%_chance", "Volatility")[0] == "model"


def test_cov46_train97_readers_and_no_double_count_of_the_main_spell():
    """COV46 (train 97, MECHB44): the "Cast Speed for each different Spell" lines are read by
    models.different_spells_cast_speed, which counts the main spell too, so the old flat stand-in is gone (it counted the
    main spell twice: an OVERCOUNT); Unstable Bond's line is model-read; Mist Raven is a modelled skill."""
    from builder.ledger import coverage, model_entry, translate

    line = "6% increased Cast Speed for each different Spell you've Cast in the last eight seconds"
    assert translate(line)[1] == [] and model_entry(line).raw["model"] == "different_spells_cast_speed"
    bond = "Gain 3 Volatility when an Allied Persistent Reviving Minion is Killed"
    assert model_entry(bond).id == "model-read-mechb44-unstable-bond" and translate(bond)[1] == []
    assert coverage.MODELED_SKILLS["Mist Raven"] == "model-mechb44-mist-raven"


def test_cov47_train98_and_99_readers_are_model_read():
    """COV47: Serle's Grit's per-plate Thorns (MECHB44's runic_tempering_model, train 98) and MECH92's Volatility lines
    (train 99) are model-read through empty pob_gap translations; the audit's copy of the condition resolver's events
    counts your Heavy Stuns (MECH85) and Immobilises / Pins ending (MECH92) as rated, as conditions._debuff_source does."""
    from builder.ledger import coverage, model_entry, translate

    for line, entry in (("45 to 90 added Physical Thorns damage per Runic Plate", "model-read-cov47-runic-plate-thorns"),
                        ("25% chance that when Volatility on you explodes, you regain an equivalent amount of Volatility",
                         "model-read-mech92-volatility-regain"),
                        ("3% chance to gain Volatility on Kill", "model-read-mech92-volatility-on-kill")):
        assert model_entry(line).id == entry and translate(line)[1] == []
    for line, var in (("Enemies are Intimidated for 4 seconds when you Immobilise them", "conditionEnemyIntimidated"),
                      ("Enemies are Maimed for 4 seconds after becoming Unpinned", "conditionEnemyMaimed"),
                      ("Enemies you Heavy Stun while Shapeshifted are Intimidated for 6 seconds", "conditionEnemyIntimidated")):
        assert coverage.resolver_condition(line).var == var, line
    # a kill event the resolver rates 0 stays open
    assert coverage.resolver_condition("Enemies you Kill while Shapeshifted are Intimidated for 4 seconds") is None


def test_cov47_ancestral_bond_has_no_stand_in_the_totem_models_double_again():
    """COV47 (an OVERCOUNT, probe POE2T3978): the ledger's old "+1 to maximum number of Summoned Totems" stand-in for
    "Your Totem Limit is doubled" reached PoB's Totem Limit, which the bridge reads as one of the build's "+N Totems"
    adds, so models.bond_totems doubled it again ((2 + 1) x 2 = 6 Spell Totems, not 4). The line is model-read only."""
    from builder.ledger import model_entry, translate
    from builder.search import models

    entry, lines = translate("Your Totem Limit is doubled")
    assert entry.id == "ancestral-bond" and lines == []
    assert model_entry("Your Totem Limit is doubled").id == "ancestral-bond"
    assert models.bond_totems(2, 0, True)[0] == 4


def test_cov47_skills_with_no_damage_of_their_own_and_newly_modelled_skills():
    """COV47: skills every stat set of whose player effect carries base_deal_no_damage or display_statset_no_hit_damage
    (game GrantedEffectStatSets, per set; PoB's Data/Skills the same) are "pob" (0 is right); Runic Tempering (MECHB44)
    and Repulsion (GEM26's wave) are modelled. A skill with one damaging set (Frostflame Nova) stays out, and the
    warcries whose triggered damage isn't all counted (Fortifying Cry, Infernal Cry) stay open."""
    from builder.ledger import coverage

    assert {"Despair", "Temporal Chains", "Artillery Ballista", "Cast on Minion Death", "Soul Offering"} <= set(
        coverage.NO_OWN_DAMAGE_SKILLS)
    assert coverage.MODELED_SKILLS["Runic Tempering"] == "model-mechb44-runic-tempering"
    assert coverage.MODELED_SKILLS["Repulsion"] == "model-gem26-repulsion-wave"
    for name in ("Frostflame Nova", "Frozen Locus", "Elemental Sundering", "Fortifying Cry", "Infernal Cry"):
        assert name not in coverage.NO_OWN_DAMAGE_SKILLS, name
    for name in ("Fortifying Cry", "Infernal Cry"):
        assert name not in coverage.MODELED_SKILLS, name


def test_cov48_skills_whose_damage_another_set_or_a_model_gives():
    """COV48: the sweep scores a skill's first stat set; Frostflame Nova, Frozen Locus and Elemental Sundering deal theirs
    in another set, the totems in a second granted effect, Valako's Charge / Inevitable Agony through their models, Spirit
    Vessel through its socketed skills: each names the ledger entry (or bridge / model) that rates it, and each entry
    exists. Summon Spectre and Tamed Companion stay open (the search picks no monster for them)."""
    from builder.ledger import coverage, entries

    ids = {e.id for e in entries()}
    for name in ("Frostflame Nova", "Frozen Locus", "Elemental Sundering", "Valako's Charge", "Inevitable Agony",
                 "Dark Effigy"):
        entry = coverage.MODELED_SKILLS[name]
        assert entry in ids, (name, entry)
    assert coverage.MODELED_SKILLS["Shockwave Totem"] == "model:totem_count"
    assert coverage.MODELED_SKILLS["Spirit Vessel"] == "bridge:spirit-vessel"
    for name in ("Summon Spectre", "Tamed Companion"):
        assert name not in coverage.MODELED_SKILLS and name not in coverage.NO_OWN_DAMAGE_SKILLS, name


def test_cov47_volatility_passives_and_answered_call_are_model_read():
    """COV47: passive lines models on main read (volatility_model's Precise Volatility, Growing Peril, Volatility when
    Stunned; ancestral_spirits_model's Answered Call trigger) are model-read through empty pob_gap entries."""
    from builder.ledger import model_entry, translate

    for line, entry in (
            ("Volatile Power also grants 2% increased Critical Hit chance per Volatility exploded", "model-read-cov47-precise-volatility"),
            ("4% increased Chaos Damage over Time per Volatility", "model-read-cov47-growing-peril"),
            ("10% chance to gain Volatility when you are Stunned", "model-read-cov47-volatility-when-stunned"),
            ("Trigger Ancestral Spirits when you Summon a Totem", "model-read-cov47-answered-call-trigger")):
        assert model_entry(line).id == entry and translate(line)[1] == [], line


def test_cov48_glory_not_consume_is_model_read():
    """COV48: Everlasting Glory's "chance to not consume Glory" (models.glory_lines / glory_plan) is model-read through an
    empty pob_gap entry. The Ordained's Fragment of Divinity line stays open (COV43's rule: the model keys on Righteous
    Descent at a fixed 4 s, no pattern of its own reads the line)."""
    from builder.ledger import model_entry, translate
    from builder.search import models

    entry, out = translate("Skills have a 15% chance to not consume Glory")
    assert entry.id == "model-read-cov48-glory-not-consume" and out == [] and entry.raw["model"]
    assert models.GLORY_NOT_CONSUME.match("Skills have a 15% chance to not consume Glory")
    assert models.glory_lines(["Skills have a 15% chance to not consume Glory"])["not_consume"] == pytest.approx(0.15)
    found = model_entry("Create a Fragment of Divinity in your Presence every 4 seconds")
    assert found is None or not found.id.startswith("model-read-")


def test_cov48_train102_readers_crafted_jewels_frightening_shield_spectral_fire():
    """COV48: train 102's MECH-A readers. conditions._debuff_source strips the crafted jewels' "while you have a <X> and a
    <Y> socketed in your tree" (SOCKETED_PAIR) and rates Frightening Shield's raised-shield Debilitate, so the audit
    reads both as the resolver's; a kill-event line with the clause stays open. Fairgraves' Curse's Spectral Fire line
    is model-read (models.SPECTRAL_FIRE)."""
    from builder.ledger import translate
    from builder.search import conditions, models

    cases = {"Blind Enemies on Hit while you have a Ruby and a Sapphire socketed in your tree": "conditionEnemyBlinded",
             "Debilitate Enemies on Hit while you have an Emerald and a Sapphire socketed in your tree":
                 "conditionEnemyDebilitated",
             "Apply Debilitate to Enemies 30 Metres in front of you while your Shield is raised": "conditionEnemyDebilitated"}
    for line, var in cases.items():
        found = coverage.resolver_condition(line)
        assert found is not None and found.var == var, line
    assert conditions.SOCKETED_PAIR.search(next(iter(cases)))
    assert coverage.resolver_condition("Blind Enemies when they Kill you while you have a Ruby and a Sapphire socketed "
                                       "in your tree") is None
    entry, out = translate("Attack Hits inflict Spectral Fire for 8 seconds")
    assert entry.id == "model-read-mecha-spectral-fire" and out == []
    assert models.SPECTRAL_FIRE.match("Attack Hits inflict Spectral Fire for 8 seconds")


def test_cov48_mech93_readers_are_model_read_and_the_spell_magnitude_stand_in_is_gone():
    """COV48 (the manager 09:50, MECH93's train-102 readers): the Genesis Tree "N% increased Magnitude of Elemental
    Ailments you inflict with Spells" is model-read with no stand-in (MECH93's spell_ailment_magnitude_mods reads the
    item's own text, so COV15's stand-ins counted it twice on search-built items); Calloused Marrow is model-read
    (VOLATILITY_TAKEN); Creeping Chill's and Repulsion's read stats are the models', the unread ones stay open."""
    from builder.ledger import translate
    from builder.search import conditions, models

    line = "(30-40)% increased Magnitude of Elemental Ailments you inflict with Spells"
    entry, out = translate(line)
    assert entry.id == "items-spell-elemental-ailment-magnitude" and out == [] and entry.raw["model"]
    assert models.SPELL_AILMENT_MAGNITUDE.match("35% increased Magnitude of Elemental Ailments you inflict with Spells")
    marrow = "40% reduced Damage taken from Volatility if you have at least 100 Tribute"
    entry, out = translate(marrow)
    assert entry.id == "model-read-mech93-calloused-marrow" and out == [] and models.VOLATILITY_TAKEN.match(marrow)
    assert conditions.CREEPING_CHILL == "Creeping Chill" and models.REPULSION == "Repulsion"
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("support_creeping_chill_consume_X_power_charges_to_trigger_on_ice_crystals", "Creeping Chill",
             "model-mech93-creeping-chill"),
            ("active_skill_ground_effect_area_of_effect_+%_final_per_second", "Creeping Chill", "model-mech93-creeping-chill"),
            ("triggered_by_primary_skill", "Repulsion Wave", "model-mech93-repulsion-use"),
            ("base_deal_no_damage", "Repulsion", "model-mech93-repulsion-use")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), (stat, skill)
    for stat, skill in (("support_creeping_chill_cold_damage_equal_to_%_ice_crystal_maximum_life", "Creeping Chill"),
                        ("curse_repulsion_applies_X_fragility", "Repulsion")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat


def test_cov48_train103_reactive_growth_reads_the_bosses_elemental_mix_and_infusion_caps_are_model_read():
    """COV48 (the manager 11:45; COV47's routed FLAT assumption): Reactive Growth's line counts N x the matching
    Adaptations of each element, MECH-C's boss_hits.adaptations_held for the scored boss (conditions ReactiveGrowth<E>,
    kind `adaptations`), not one per element whatever the boss deals; the Infusion maximum lines are model-read
    (assumptions.infusion_cap, MECHC45)."""
    from builder.ledger import conditions, translate
    from builder.search import assumptions, boss_hits, bosses

    entry, out = translate("9% less Damage taken of each Elemental Damage Type per matching Adaptation")
    assert entry.id == "reactive-growth"
    assert out == [f"share:ReactiveGrowth{e} 9% less {e} Damage taken" for e in ("Fire", "Cold", "Lightning")]
    assert {c["id"]: c["adaptations"] for c in conditions() if c.get("adaptations")} == {
        f"ReactiveGrowth{e}": e for e in ("Fire", "Cold", "Lightning")}
    assert boss_hits.adaptations_held("Arbiter of Ash") == {"Fire": 3.0, "Cold": 0.0, "Lightning": 0.0}
    assert sum(boss_hits.adaptations_held("Zarokh, the Temporal").values()) == 0
    worst = boss_hits.adaptations_held(bosses.WORST)
    assert sum(worst.values()) == pytest.approx(3.0) and round(worst["Fire"], 2) == 1.12
    for line in ("+1 to maximum number of Elemental Infusions", "+1 to maximum Fire Infusions"):
        entry, out = translate(line)
        assert entry.id == "model-read-mechc45-infusion-cap" and out == [], line
        assert assumptions.INFUSION_CAP_ALL.match(line) or assumptions.INFUSION_CAP_ONE.match(line), line


def test_cov48_r36_8b_frenzy_on_hit_while_bleeding_is_unreachable():
    """COV48 (the manager 13:50; RESEARCH36 R36-8b, MEDIUM-HIGH): "Gain a Frenzy Charge on Hit while Bleeding" is on no
    PoE2 unique (a PoE1 leftover), so its Exclusive mod is not league-obtainable; NOT_OBTAINABLE now relabels a "model"
    row too (the resolver reads the line as a Frenzy source), not only a "none" one."""
    why = coverage.NOT_OBTAINABLE["FrenzyChargeOnHitWhileBleedingUnique__1"]
    assert "R36-8b" in why and "PoE1 leftover" in why


def test_cov48_grasping_mail_ring_pool_line_is_the_rare_generators():
    """COV48: "Can roll Ring Modifiers" is model-read: the rare generator's own pool rule reads it."""
    from builder.ledger import translate
    from builder.search import rares

    entry, out = translate("Can roll Ring Modifiers")
    assert entry.id == "model-read-cov48-grasping-mail-ring-pool" and out == []
    assert "Can roll Ring Modifiers" in rares.IMPLICIT_POOL_TAGS
    assert translate("Catalysts can be applied to this item") is None


def test_cov48_carrier_unverified_rows_settled():
    """COV48 (the manager 14:45): the "carrier unverified" unique-only rows. Real carriers (base implicits in
    BaseItemTypes, Way of the Stonefist forms, Rite of Passage) say so and stay open; the Orb of Sacrifice upgrades stay
    open as UNVERIFIED; mods with no league carrier today (incl. Essence display rows and 1.0-only lines) are
    NOT_OBTAINABLE with their evidence."""
    for mod in ("FlailImplicitIgnoreBlock1", "HandWrapsUniqueDecimatingStrike1", "UniqueCharmRandomPossess1"):
        assert coverage.R32_3_OBTAINABLE[mod].startswith("a real carrier"), mod
        assert mod not in coverage.NOT_OBTAINABLE, mod
    assert coverage.R32_3_OBTAINABLE["CorruptionUpgradeAdditionalChainChance"].startswith("UNVERIFIED")
    assert "CorruptionUpgradeAdditionalChainChance" not in coverage.NOT_OBTAINABLE
    for mod in ("UniqueReflectCurses1", "EssenceDisplayAttributes4", "ColdDamageIgnitesUnique__1",
                "UniqueMutatedVaalReflectElementalAilmentsToSelf"):
        assert "checked" in coverage.NOT_OBTAINABLE[mod], mod
