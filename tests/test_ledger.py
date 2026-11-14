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
    # a share of the fight (COV11): Spike Pit's line counts its full value x the boss's share in Jagged Ground
    spike = translate("Enemies in Jagged Ground you create take 10% increased Damage")
    assert spike is not None and spike[0].conditional
    assert spike[1] == ["share:JaggedGround Nearby Enemies have 10% increased Damage taken"]
    assert readable(spike[1][0]) == "Nearby Enemies have 10% increased Damage taken (x the share of the fight with JaggedGround)"


def test_conditions_and_wrong_lines_load():
    from builder.ledger import conditions, wrong

    assert {"GloryUsesPast6s", "GloryBuff6s", "JaggedGround"} <= {c["id"] for c in conditions()} and "SpellEcho" not in {c["id"] for c in conditions()}
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


def test_the_runic_ward_unlucky_rune_line_counts_at_the_ward_damaged_share_once():
    """COV14 (RESEARCH6's missing Unlucky lines): the Warding Rune of Bodyguards' Bonded "Damage of Enemies Hitting you is
    Unlucky if / your Runic Ward has been damaged Recently" (two lines in PoB's rune data). COV22 (MECHC15's switch): it
    counts at the share of the fight the Ward was damaged Recently (condition RunicWardDamaged, ward_damaged_recently:
    Runic Reprieve's blocked hits), never the always-on 6.67% of unlucky-enemy-damage; the 4% / 3% sit on the first
    half or the joined line, and the second half adds nothing, so the bonus counts once either way."""
    from builder.ledger import conditions, translate

    share = ["share:RunicWardDamaged 4% less Damage taken from Hits", "share:RunicWardDamaged 3% less Damage taken from Hits"]
    for line in ("Damage of Enemies Hitting you is Unlucky if",
                 "Damage of Enemies Hitting you is Unlucky if your Runic Ward has been damaged Recently"):
        entry, lines = translate(line)
        assert entry.id == "unlucky-enemy-damage-runic-ward" and lines == share, line
    entry, lines = translate("your Runic Ward has been damaged Recently")
    assert entry.id == "unlucky-enemy-damage-runic-ward-tail" and lines == []
    assert translate("Damage of Enemies Hitting you is Unlucky")[0].id == "unlucky-enemy-damage"
    assert any(c["id"] == "RunicWardDamaged" and c.get("ward_damaged_recently") for c in conditions())


def test_cov15_item_lines_translate_to_lines_pob_parses():
    """COV15 (the coverage report's open item templates): each translation is a line PoB parses for the same game
    wording (scratch/cov15 parse probe); the affix-count implicits are recorded as read by the rare generator."""
    from builder.ledger import model_entry

    cases = {
        "Minions have 40% increased Magnitude of Damaging Ailments":
            ["Minions have 40% increased Magnitude of Damaging Ailments you inflict"],
        "Lightning Damage from Hits also Contributes to Flammability and Ignite Magnitudes": ["Your Lightning Damage can Ignite"],
        "Cold Damage from Hits also Contributes to Flammability and Ignite Magnitudes": ["Your Cold Damage can Ignite"],
        "15% increased Mana Cost Efficiency of Spells": ["Spells have 15% increased Mana Cost Efficiency"],
        "35% increased Magnitude of Elemental Ailments you inflict with Spells":
            ["Spells have 35% increased Magnitude of Ignite",
             "Spells have 35% increased Magnitude of Non-Damaging Ailments you inflict"],
        "Minions' Strikes have Melee Splash": [],
    }
    for line, want in cases.items():
        assert translate(line)[1] == want, line
    for line in ("-1 Prefix Modifier allowed", "+2 Suffix Modifiers allowed"):
        assert model_entry(line).id == "items-affix-count-implicits", line
    # the "you inflict" form PoB already parses is left to PoB
    assert translate("20% increased Magnitude of Damaging Ailments you inflict") is None


def test_the_audit_leaves_debuff_lines_on_kills_and_freezes_open_as_the_uptime_model_does():
    """COV15 (the manager, 13 Nov): coverage.resolver_condition's event filter had backspace characters where its regex
    word boundaries belong (a Bash heredoc's mangling), so its "kill" and "frozen by" alternatives never matched and a
    debuff line on a kill or a Freeze, which conditions._debuff_source rates 0, would have counted as read. A word
    boundary, so "Skills" isn't a kill."""
    from pathlib import Path

    from builder.ledger.coverage import resolver_condition

    assert resolver_condition("Blind Enemies on Hit if you have Killed Recently") is None
    assert resolver_condition("Blind Enemies Frozen by you on Hit") is None
    assert resolver_condition("Blind Enemies on Hit with Skills").var == "conditionEnemyBlinded"
    assert resolver_condition("Blind Enemies on Hit").var == "conditionEnemyBlinded"
    root = Path(__file__).resolve().parents[1]
    for path in list((root / "builder" / "ledger").glob("*.py")) + list((root / "tests").glob("*.py")):
        assert chr(8) not in path.read_text(encoding="utf-8"), f"a backspace character in {path.name}"


def test_cov16_item_lines_translate():
    """COV16: the ailment-chance lines drop only the word PoB leaves over; Rune of Culmination's min / max rolls keep
    the average (counted as PoB already counts it)."""
    assert translate("(20-30)% increased chance to Poison")[1] == ["(20-30)% increased to Poison"]
    assert translate("15% increased chance to inflict Bleeding")[1] == ["15% increased to inflict Bleeding"]
    entry, lines = translate("Rolls only the minimum or maximum Damage value for Physical Damage")
    assert entry.id == "items-physical-rolls-minimum-or-maximum" and lines == []


def test_recovery_lines_the_audit_counts_are_the_lines_the_recovery_model_reads():
    """COV17: models-recovery.yaml tells the audit that MECHC12's recovery model (builder/search/recovery.py) counts these
    lines; each must be a line the model's own pattern reads (rolled, as bridge.sourceLines gives it), and the lines no
    rate reads yet (recovery.EVENT_UNREAD) must stay open (Indigon's restriction counts since MECHC14: COV21)."""
    from builder.ledger import model_entry
    from builder.ledger.coverage import ignored_line_status
    from builder.search import recovery

    read = {
        "Recover 4% of your maximum Life when an Enemy dies in your Presence": (recovery.PRESENCE_DEATH, "search"),
        "Recover 3% of Maximum Mana when you collect a Remnant": (recovery.REMNANT_PICKUP, "search"),
        "Recover 150 Life when Used": (recovery.CHARM_OWN, "match"),
        "Recover 20 Mana when Used": (recovery.CHARM_OWN, "match"),
        "Recover 5% of maximum Mana when a Charm is used": (recovery.CHARM_ANY, "search"),
        "Recover 2% of maximum Life and Mana when you use a Warcry": (recovery.WARCRY_BOTH, "search"),
        "Recover 4% of maximum Life when you use a Warcry": (recovery.WARCRY_LIFE, "search"),
        "Recover 50% of maximum Life when you Heavy Stun a Rare or Unique Enemy": (recovery.HEAVY_STUN_UNIQUE, "search"),
        "Recover 1% of maximum Life when you Ignite an Enemy": (recovery.IGNITE_PCT, "search"),
        "Recover 50 Life when you Ignite an Enemy": (recovery.IGNITE_FLAT, "search"),
        "Recover 3% of maximum Mana when you Shock an Enemy": (recovery.SHOCK_PCT, "search"),
    }
    for line, (pattern, how) in read.items():
        assert getattr(pattern, how)(line), line
        entry = model_entry(line)
        assert entry is not None and entry.raw["model"] == "recovery", line
        assert ignored_line_status(line, False)[0] == "model", line
    # the item's ranged form reaches the audit; the bridge gives the model the rolled one
    assert ignored_line_status("Recover (3-5)% of your maximum Mana when an Enemy dies in your Presence", False)[0] == "model"
    for line in ("Recover 5% of maximum Life for each Endurance Charge consumed", "Recover all Mana when Used",
                 "Recover 2% of maximum Mana when you consume a Power Charge"):
        assert model_entry(line) is None, line
    # (COV21) Indigon's restriction counts since MECHC14: the bridge zeroes PoB's Mana leech / on-hit / recoup outputs
    # and flasks.mana_regen_only its Mana flasks (models-read-cov21.yaml)
    assert model_entry("Mana Recovery other than Regeneration cannot Recover Mana").raw["model"] == "mana_regen_only"
    # Recoup stays recorded as no effect (recovery, not a checked defence)
    assert ignored_line_status("12% of Physical Damage prevented Recouped as Life", False)[:2] == \
        ("ledger", "no-effect-enemy-death-and-recoup")


def test_share_lines_have_an_item_form_and_other_conditional_lines_dont():
    """COV17: `share:` lines go on items as "LedgerShare:" stand-ins; flag:, if: and mult: lines stay passives only, and
    the audit says so. Seed of Cataclysm's crit-bonus loss now counts (an OVERCOUNT while PoB dropped it)."""
    from builder.ledger import item_lines
    from builder.ledger.coverage import ignored_line_status

    assert item_lines(["share:X 5% more Damage", "+5 to Strength"]) == ["LedgerShare:X 5% more Damage", "+5 to Strength"]
    seed = "5% reduced Critical Spell Damage Bonus per Critical Hit you've dealt with Spells Recently"
    entry, lines = translate(seed)
    assert entry.on_items and lines == ["share:SpellCritsRecently 5% reduced Critical Spell Damage Bonus"]
    assert ignored_line_status(seed, False)[0] == "ledger"
    flagged = [e for e in entries() if e.kind == "pob_gap" and any(re.match(r"^(flag|if|mult):", r) for r in e.replace)]
    assert flagged and not any(e.on_items for e in flagged)


def test_item_lines_a_model_already_reads_are_counted_by_the_audit():
    """COV17: item lines a model reads that the audit called uncounted: the Remnant double-pickup affix (the item wording
    of DUPLICATE_PICKUP's stat), Dodge Roll distance / speed (models.dodge_roll), Runic Ward per charm used / on kill
    (models.ward_income), and Onslaught sources conditions._onslaught_source rates from the build's events. A Vaal
    skill's Onslaught (rated 0) and a conditional Dodge Roll wording stay open."""
    from builder.ledger.coverage import ignored_line_status
    from builder.search import assumptions, conditions, models

    read = {"(10-12)% chance for Remnants you pick up to count as picking up an additional Remnant": assumptions.DUPLICATE_PICKUP,
            "+(0.3-0.4) metres to Dodge Roll distance": models.DODGE_DISTANCE, "(20-30)% faster Dodge Roll": models.DODGE_SPEED,
            "Recover (32-45) Runic Ward when a Charm is used": models.WARD_ON_CHARM,
            "Recover 1% of maximum Runic Ward on Kill": models.WARD_ON_KILL,
            "Gain Onslaught for 3 seconds when Hit": conditions.ONSLAUGHT_SOURCE}
    for line, pattern in read.items():
        rolled = re.sub(r"\((-?[\d.]+)-(-?[\d.]+)\)", r"\1", line)
        assert pattern.search(rolled), line
        assert ignored_line_status(line, False)[0] == "model", line
    for line in ("You gain Onslaught for 20 seconds on using a Vaal Skill",
                 "+2 metres to Dodge Roll distance if you haven't Dodge Rolled Recently"):
        assert ignored_line_status(line, False)[0] == "none", line
    # a socketed-jewel condition the Onslaught model doesn't check stays open
    assert ignored_line_status("With a Searching Eye Jewel Socketed, Attacks have 25% chance to grant Onslaught On Kill",
                               False)[0] == "none"


def test_flask_lines_the_bridge_reads_are_counted_by_the_audit():
    """COV17: Reverie's and Crown of Thorns' lines the bridge's flask data reads (engine/bridge.lua: lifeFlaskUnusable,
    lifeFlaskConstant, lifeFlaskLess, lifeLossPerUse), and that the bridge still reads them with its own patterns."""
    from pathlib import Path

    from builder.ledger.coverage import ignored_line_status

    bridge = (Path(__file__).resolve().parents[1] / "engine" / "bridge.lua").read_text(encoding="utf-8")
    for line, lua in {"Cannot use Life Flasks": "cannot use life flasks",
                      "Non-Unique Life Flasks apply their Effects constantly": "life flasks apply their effects constantly",
                      "(40-60)% less Life Flask Recovery": "less life flask recovery",
                      "Lose 5 Life when you use a Skill": "life when you use a skill"}.items():
        assert lua in bridge, lua
        assert ignored_line_status(line, False)[:1] == ("model",), line


def test_cov18_item_lines_a_model_already_reads_are_counted_by_the_audit():
    """COV18: Divine Charge lines (conditions._divinity), Hateforge's Rage reset and Blood of the Warrior's Rage lines
    (conditions.rage_held) and Steal Charges (conditions._charges_held, 0 against a boss) are read by their patterns
    and counted by the audit. Redflare Conduit's "Lose all Power Charges on reaching maximum" stays open: the charges
    held don't read it (an OVERCOUNT routed to MECH-C)."""
    from builder.ledger.coverage import ignored_line_status
    from builder.search import conditions

    read = {"+10 to maximum Divine Charges": conditions.DIVINE_MAX,
            "Gain a Divine Charge on Hit": conditions.DIVINE_ON_HIT,
            "Lose all Divine Charges when you gain Divinity": conditions.DIVINE_LOSE,
            "Lose all Rage on reaching Maximum Rage": conditions.RAGE_RESET_AT_MAX,
            "No Inherent loss of Rage during effect": conditions.RAGE_NO_LOSS,
            "Gain (3-5) Rage when Hit by an Enemy during effect": conditions.RAGE_SOURCE,
            "10% chance to Steal Power, Frenzy, and Endurance Charges on Hit": conditions.STEAL_CHARGES}
    for line, pattern in read.items():
        rolled = re.sub(r"\((-?[\d.]+)-(-?[\d.]+)\)", r"\1", line)
        assert pattern.search(rolled), line
        assert ignored_line_status(line, False)[0] == "model", line
    # (COV21) read since MECHC14 (conditions.CHARGE_RESET_AT_MAX / charges_with_reset; models-read-cov21.yaml)
    assert conditions.CHARGE_RESET_AT_MAX.search("Lose all Power Charges on reaching maximum Power Charges")
    assert ignored_line_status("Lose all Power Charges on reaching maximum Power Charges", False)[0] == "model"


def test_cov20_cull_on_block_and_ironbound_are_counted_and_cadiros_arrows_stay_open():
    """COV20: Doomgate's Cull on Block (assumptions.cull_on_block) and Ironbound's Return (models.return_model) are read
    by their patterns and counted by the audit; Cadiro's Gambit, read for all six arrow kinds since MECHB22's
    cadiro_model, is a model line too (COV26, models-read-cov26.yaml; it stayed open while only its Reversing arrows were
    read)."""
    from builder.ledger.coverage import ignored_line_status
    from builder.search import conditions, models

    read = {"Enemies are Culled on Block": conditions.CULL_ON_BLOCK_LINE,
            "Arrows Return if they have Pierced a target which had Fully Broken Armour": models.IRONBOUND_RETURN}
    for line, pattern in read.items():
        assert pattern.search(line), line
        assert ignored_line_status(line, False)[0] == "model", line
    cadiro = "Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow"
    assert models.CADIRO_ARROWS.search(cadiro) and ignored_line_status(cadiro, False)[0] == "model"


def test_raw_mod_lines_have_an_item_form_and_apply_on_items():
    """COV25: a `mod:` line (a raw PoB modifier, for a line no PoB text carries) goes on items as "LedgerMod:<Name>
    <TYPE> <value>"; it isn't conditional, so it applies on items and bonded lines; Rune of Reach's reduced Remnant effect
    is one, and the audit counts it as the ledger's."""
    from builder.ledger import RAW_MOD, item_lines
    from builder.ledger.coverage import ignored_line_status

    assert item_lines(["mod:RemnantEffect INC -25", "+5 to Strength"]) == ["LedgerMod:RemnantEffect INC -25", "+5 to Strength"]
    assert not RAW_MOD.match("mod:RemnantEffect LESS -25") and not RAW_MOD.match("mod:RemnantEffect INC (15-25)")
    line = "Remnants you create have 25% reduced effect"
    entry, lines = translate(line)
    assert lines == ["mod:RemnantEffect INC -25"] and entry.on_items and not entry.conditional
    assert ignored_line_status(line, False)[0] == "ledger"


def test_model_entry_is_memoised_and_answers_as_the_plain_lookup_does():
    """COV26 (PERF14): model_entry is cached for the process; a cached answer is the same Entry the plain lookup finds,
    for lines with and without a model, and asking again is a cache hit returning the very same object."""
    from builder.ledger import entries, model_entry

    plain = model_entry.__wrapped__
    lines = ["Archon Buffs also grant 30% increased Critical Hit Chance", "-1 Prefix Modifier allowed",
             "Mana Recovery other than Regeneration cannot Recover Mana", "+10 to Strength", "not a line at all"]
    for line in lines:
        first = model_entry(line)
        hits = model_entry.cache_info().hits
        assert model_entry(line) is first and model_entry.cache_info().hits == hits + 1, line
        assert first is plain(line), line
    assert model_entry("+10 to Strength") is None
    assert all(e is f for e, f in zip(entries(), entries()))  # the ledger is loaded once: nothing to invalidate


def test_thorns_and_cadiro_lines_feed_their_models():
    """COV26 (MECHB23 / MECHC21's hand-backs): the Thorns lines models.thorns_model reads and Cadiro's Gambit's arrow line
    models.cadiro_model reads are model lines to the audit (and to item_read_by_models), adding nothing to PoB."""
    from builder.ledger import model_entry
    from builder.search import models

    cases = {"(15-25)% chance to deal your Thorns Damage to Enemies you Hit with Melee Attacks": "thorns",
             "Deal your Thorns damage to enemies you Critically Hit with Melee Attacks": "thorns",
             "Thorns can Retaliate against all Hits": "thorns",
             "Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow": "cadiro"}
    for line, model in cases.items():
        entry = model_entry(line)
        assert entry.raw["model"] == model and entry.translate(line) == [], line
    assert models.THORNS_ON_HIT.search("20% chance to deal your Thorns Damage to Enemies you Hit with Melee Attacks")
    assert models.CADIRO_ARROWS.search("Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow")
    assert model_entry("Deal your Thorns Damage to Enemies you Stun with Melee Attacks") is None  # (not counted yet)
