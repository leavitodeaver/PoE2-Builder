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
    assert translate("5% increased Pin duration") is None  # retired: the Pinned uptime is modelled (A12-1)
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
    assert len(marked) >= 150 and all(r in marked or r["reason"].startswith("GGG-marked unused (DNT)")
                                      for r in unreachable)
    carried = coverage.pob_unique_templates()
    by_mod: dict[str, list[str]] = {}
    for r in exclusive:
        by_mod.setdefault(r["source"].split()[1], []).append(r["line"])
    assert not any(coverage.template(line) in carried for r in marked for line in by_mod[r["source"].split()[1]])
    assert any(r["status"] == "none" and "Searching Eye" not in r["line"] and r["reason"].endswith("(UNVERIFIED)")
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
                 "(50-60)% increased Rarity of Fish Caught", "Glows while in an Area containing a Unique Fish"):
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
    moving = [r for line, r in never.items() if line.endswith("increased Mana Regeneration Rate while moving")]
    assert moving and all(r["status"] == "none" for r in moving)


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
    for line in ("+2 metres to Dodge Roll distance if you haven't Dodge Rolled Recently",
                 "20% increased Glory generation for Tribute", "Used when you become Cursed"):
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
