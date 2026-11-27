"""Coverage audit: every mechanic source in the game data, and whether our numbers count it.

PoB's own gap list (gaps.json) only holds the passive and unique lines its parser admits it can't
read. This audit doesn't take its word for it. It covers:

  a. passive and unique lines PoB ignores (gaps.json);
  b. item affixes, runes / Soul Cores / Idols and jewel mods (coverage.json from the bridge's
     `auditCoverage`: each line through PoB's parser, ranges at their maximum);
  c. skill and support gem stats PoB never reads: every stat of every stat set (all levels share
     one stat list; quality and constant stats included) against the stat set's stat map, the
     test PoB's own skills tab uses to mark a stat unsupported;
  d. silent no-ops: lines PoB parses into modifier names that no calculation asks for. The
     calculation side is read from PoB's Lua source (Modules/Calc*.lua and the rest of Modules/,
     Classes/Mod*.lua, the skill data's functions): every name a modifier query (Sum, More, Flag,
     Override, List, Max, Tabulate, HasMod, calcLib.mod/val, ...) is called with, names built by
     concatenation counted as wildcards (word characters and spaces: slot names). This reads PoB's code as text for an audit only; no game
     data comes from it.

Every line gets one status: `pob` (counted by PoB), `ledger` (translated by a ledger entry,
including entries that record "no effect on what's calculated"), `model` (fed to a custom model)
or `none` (not counted). What isn't counted is ranked by impact: the kind of mechanic (damage,
defence, recovery and speed before flavour), the kind of source (keystones, ascendancy passives
and notables before small passives; popular uniques by poe.ninja listing counts, an economy
endpoint), and how many sources share the line.

    uv run python -m builder.ledger.coverage            # report from the last dump
    uv run python -m builder.ledger.coverage --refresh  # re-run the bridge part first (one engine)

Writes data/pob-<commit>/coverage-report.json and prints a summary.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from builder import ROOT
from builder.ledger import checked_parse, entries, model_entry, parse_audit, parsed_unused, play_row_entry, translate, wrong
from builder.search.recovery import EVENT_UNREAD

POB_SRC = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src"
BRIDGE = ROOT / "engine" / "bridge.lua"
QUERIES = ("Sum", "More", "Flag", "Override", "List", "Max", "Min", "Tabulate", "HasMod", "GetCondition",
           "GetMultiplier", "GetStat", "SumInternal", "MoreInternal", "FlagInternal", "ListInternal")
QUERY = re.compile(r"(?::(?:%s)|calcLib\.(?:mod|val|mods)|\bcalcLocal)\(" % "|".join(QUERIES))
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
CHAIN = re.compile(r'(?:"[^"]*"|[\w.\[\]]+)(?:\s*\.\.\s*(?:"[^"]*"|[\w.\[\]:()]+))+')
RANGE_TEXT = re.compile(r"\(-?\d+(?:\.\d+)?-\d+(?:\.\d+)?\)")
NUMBER = re.compile(r"[+-]?\(?-?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\)?")

STATUSES = ("pob", "ledger", "model", "none", "unreachable")
# Timeless jewel factions (PoB's legion node id prefix) that no 0.5 jewel conquers for: their conquered passives can't
# be on a character, so they're "unreachable", out of the not-counted totals and the ranking (COVB7; RESEARCH23 #140,
# checked: PoB's Data/Uniques/jewel.lua Timeless section holds Heroic Tragedy (Kalguur) and Undying Hate (Abyssals)
# only; poe2db Timeless_Jewel lists those two items only (its misc conquest mods for these five factions sit on no
# item); poe2wiki's Timeless jewel pages agree. Recheck when a patch adds a Timeless jewel.)
NO_JEWEL_FACTIONS = frozenset({"vaal", "eternal", "karui", "maraketh", "templar"})


def no_jewel_faction(line: dict) -> bool:
    """A conquered passive of a faction no 0.5 Timeless jewel conquers for (NO_JEWEL_FACTIONS)."""
    return str(line.get("id", "")).split("_")[0] in NO_JEWEL_FACTIONS


# PoE1 leftovers among PoB's unique-only mods (ModItemExclusive; COV19, the user 13 Nov 2026: "pretty sure that jewel is
# from PoE1, not PoE2"). PoB's mod pool and the game's Mods table both keep PoE1 uniques' mods that no PoE2 item
# carries. A unique-only mod is "unreachable" when one of its lines names a PoE1-only mechanic below AND none of its
# lines has the template of any line of any unique in PoB-PoE2's Data/Uniques (every file, every variant; checked again
# at each audit, so a PoB update that adds a carrier reopens it). Evidence for every group (COV19, 13 Nov 2026):
# - no game table points at any of these mod rows (every Mods-referencing column the dat-schema names: Essences,
#   EssenceMods, BaseItemTypes implicits, Characters, QuestRewards, GemEffects, Expedition2Runes, ModGrantedSkills,
#   Mods.ArchnemesisMinionMod), none has a spawn weight, all are GenerationType 3 (unique);
# - the group's keyword is on no PoE2 source: PoB-PoE2's unique lines, PoB's other item mod pools (affixes, runes,
#   jewels, desecrated, corruption, essences), passive and ascendancy lines (parsed and ignored), Timeless passives, base
#   implicits; game BaseItemTypes / ActiveSkills / PassiveSkills names hold only unrelated uses (Heist items, the
#   bear skill "Rampage", Atlas passives); KeywordPopups defines none of them (items hold Augments; support gems
#   socket into skill gems);
# - second source: poe2db's Unique_item page (all ~496 current uniques) holds none of these lines; its Jewels page lists
#   no Abyss jewel and no threshold jewel.
# Settled by COV21: fishing lines are "no effect" (ledger no-effect-fishing: they roll on the Fishing Rod), golems
# are a group below, and Grand Spectrum's lines are PoB's (Item.lua sets Multiplier:GrandSpectrum; the audit saw no
# setter before, parse_audit.calc_setters). Left OPEN (not marked; UNVERIFIED): Reflect (live PoE2 carriers:
# Bramblejack's "reflected to Attacker", Shackles of the Wretched's curse reflection, Vaal-mutated Hand Wraps' "Poison
# you inflict is Reflected to you"; PoB's ModItem keeps "Reflects N Physical Damage to Melee Attackers" Essence mods,
# whose PoE2 reach is unchecked), and the ~500 other
# unique-only mods no PoB-PoE2 unique carries: many are PoE2 content PoB lacks (Vaal-mutated uniques, Hand Wraps,
# Runic Ward lines), so "no PoB carrier" alone proves nothing: their reason says so. Recheck the groups when a patch
# adds uniques.
POE1_ONLY_GROUPS = {
    "Abyss jewels": r"Eye Jewel|\bAbyss Jewel",
    "Shaper / Elder items": r"\b(?:Shaper|Elder) Items?\b",
    "socket colours": r"\b(?:Red|Green|Blue|White) Sockets?\b|Socket Colour|socket of each colour",
    "gem sockets on items": r"Socketed (?:[A-Z][a-z]+(?:-[A-Z][a-z]+)? )*(?:Gems?|Spells|Bow Skill|Skills)\b"
                            r"|Gems can be Socketed|supported by Level \d+",
    "Rampage": r"Rampage",
    "Vaal Souls": r"Vaal Souls?\b|consumed Souls",
    "Heist, Bestiary and Synthesis": r"Heist|Lockdown|Rogue Equipment|Beasts to hunt|Synthesiser"
                                     r"|Bestial (?:Minion|Rhoa|Snake|Ursa)|Summoning Circle",
    "Searing Exarch / Eater of Worlds": r"Searing Exarch|Eater of Worlds",
    "attribute-threshold jewels": r"^With (?:at least )?\d+ (?:total )?(?:Strength|Dexterity|Intelligence)",
    "cosmetic effects": r"Footprints$|Hoofprints$|^Extra gore$|^Golden Radiance$|golden glow",
    # (COV21) PoE2 has no player Golem: no SkillGems / BaseItemTypes gem summons one (BaseItemTypes' only "Golem" is a
    # DNT quest item), PoB-PoE2's Data/Skills has no golem skill, no PoE2 tree node (PoB tree.json) or ascendancy names
    # one (PassiveSkills' "Golems" nodes golems1_-golems10 are in no PoB tree; no PassiveSkills row uses a golem stat),
    # KeywordPopups defines none, PoB-PoE2's affix / rune / jewel pools hold no golem line (ModScalability's templates
    # aren't a pool); the 24 Mods rows using a golem / Primordial stat are all GenerationType 3, no spawn weight, no
    # other table points at them (scratch/cov19/reach.json). The monster Skitter Golem (a spectre in PoB's spectre.lua)
    # carries none of these mods. Second source: poe2db's Unique_item page (both halves, 13 Nov) holds no "Golem" or
    # "Primordial"; poe2db's search for "golem" returns only that DNT quest item and Skitter Golem.
    "golems": r"\bGolems?\b",
    # (COV41) PoE1's Harbinger uniques' "Grants Summon [Greater] Harbinger of X Skill": their stat
    # local_display_summon_harbinger_x_on_equip is on the 12 HarbingerSkillOnEquipUnique* Mods only (GenerationType 3, no
    # spawn weight), ModGrantedSkills grants no skill from them, GrantedEffects holds no Summon Harbinger effect (its one
    # Harbinger is HarbingerOfMadnessPlayer, a different skill, which these lines don't name), PoB-PoE2 has no unique or
    # skill for them (checked: game 26650308 Stats / Mods / ModGrantedSkills / GrantedEffects; PoB Data/Uniques and
    # Data/Skills). Second source: poe2db's Unique_item page (434 uniques, 18 Nov) has no "Harbinger of" line; the
    # patch-notes archive has no "Harbinger" entry (no hit is not proof alone).
    "Harbinger summons": r"\bSummon (?:Greater )?Harbinger of\b",
}
_POE1_ONLY = [(name, re.compile(rx)) for name, rx in POE1_ONLY_GROUPS.items()]
# Unique-only mods GGG marks unused (COV20): their own game text starts "DNT-UNUSED" (or, for Obliterated, "DNT" and a
# placeholder number). Seven in game 26476662: Blasphemy has no Reservation, Gain Edict Declaration when you disable a
# rare monster mod, chance to disable a Rare's Modifier on hit, Break Armour from Physical Spell damage, Lightning
# against Bleeding enemies adds Electrocute buildup, Bleeding on Shocked enemies is Aggravated, "DNT Obliterated #233763".
# This marks these mods only, never the mechanics they name (other sources of Aggravated Bleeding or Electrocute
# buildup stay counted on their own lines). (checked 13 Nov, COV20: every line of coverage.json and gaps.json holding
# "DNT" is one of these seven Exclusive mods; each mod's GenerationType 3 (unique), no spawn weight; no PoB-PoE2 unique
# (Data/Uniques, every file and variant) has a line holding "DNT"; scratch/cov19/reach.py: no game table other than
# Mods points at six of them; the seventh, TheFlawedEdictUnique__1, is the only mod ModGrantedSkills grants
# TriggeredShockingRiftPlayer from, and no "Flawed Edict" name is in Words or UniqueStashLayout; second source: poe2db's
# unique lists (COV19, both halves) carry none of the seven lines, and poe2db has no The_Flawed_Edict page (404).)
DNT_UNUSED = re.compile(r"^DNT\b")
# Research that settled a DNT mod on its own (cited in its record's reason).
DNT_RESEARCHED = {
    "UniqueBleedingInflictedOnShockedEnemiesIsAggravated1": (
        "RESEARCH35 R35-3, MEDIUM-HIGH (research/bleed-on-shocked-aggravated-dnt.md): the game's English text is "
        "\"[DNT-UNUSED] ...\", no table refers to the mod (alone in its ModFamily), not in PoB's uniques or poe2db's "
        "Aggravated Bleeding entries, no patch-note hit; recheck at 1.0; "),
}

# Cultivated uniques (A32-10, COV27): the game's "UniqueMutatedVaal*" mods (Mods GenerationType 3) are the Vaal
# Cultivation Orb's outcomes, which replace 1-2 lines of a Corrupted Vaal unique. Fate of the Vaal moved to core in 0.5.0
# (patch-notes archive 0.5.0, "Fate of the Vaal Moving To Core"), so they're obtainable in a new league (AUDIT32 A32-10;
# the user chose to offer them, 14 Nov). No PoB-PoE2 unique carries them in its text, so the leftover pass below would
# call them "a PoE1 leftover or PoE2 content PoB lacks": they're live and labelled as Cultivated outcomes instead.
CULTIVATED = re.compile(r"^UniqueMutatedVaal")
# Of those, the 28 that sit on none of the 48 Cultivated uniques poe2db's orb page lists (CULT1's data,
# cultivated.json _about.outcome_mods_on_no_listed_unique): outcomes the 0.5.0 notes removed (Shackles' reflected
# ailments, The Vertex's +2-4 skill levels), duplicates and the two HiddenNonStat dummies. "No current carrier" rests on
# poe2db alone, which keeps old entries unmarked: UNVERIFIED, so they stay counted (status untouched), only labelled.
CULTIVATED_NO_CARRIER = frozenset({
    "UniqueMutatedVaalBaseSpirit", "UniqueMutatedVaalCriticalStrikeChanceIfNoCriticalStrikeDealtRecently",
    "UniqueMutatedVaalDamageLifeRecoup", "UniqueMutatedVaalDamageRemovedFromManaBeforeLifeWhileNotLowMana",
    "UniqueMutatedVaalDeflectDamageTaken", "UniqueMutatedVaalElementalExposureEffectOnHitWithMagnitude",
    "UniqueMutatedVaalGlobalFireGemLevel1", "UniqueMutatedVaalGlobalSkillGemLevel", "UniqueMutatedVaalGlobalSkillGemQuality",
    "UniqueMutatedVaalGoldFoundIncrease", "UniqueMutatedVaalHiddenNonStat", "UniqueMutatedVaalHiddenNonStat1",
    "UniqueMutatedVaalIgniteEffect", "UniqueMutatedVaalIncreasedAttackSpeed1", "UniqueMutatedVaalIncreasedLifePercent",
    "UniqueMutatedVaalLifeLeechAmount", "UniqueMutatedVaalLifeLeechAmount1", "UniqueMutatedVaalLocalCriticalStrikeChance",
    "UniqueMutatedVaalLocalEvasionRating1", "UniqueMutatedVaalLocalEvasionRating2", "UniqueMutatedVaalManaCostEfficiency1",
    "UniqueMutatedVaalMaximumLifeConvertedToEnergyShield1", "UniqueMutatedVaalMaximumLifeConvertedToEnergyShield2",
    "UniqueMutatedVaalMaximumLifeIncreasePercent1", "UniqueMutatedVaalMaximumLifeIncreasePercent2",
    "UniqueMutatedVaalPercentOfLeechIsInstant", "UniqueMutatedVaalPowerFrenzyOrEnduranceChargeOnKill",
    "UniqueMutatedVaalReflectElementalAilmentsToSelf"})
# The 28 "HandWrapsUniqueMutatedVaal*" mods (GenerationType 1 / 2, spawn weight 0; PoB ModItemExclusive): a different set
# from the 28 above (COV27: no id in common). None of their distinctive texts is among poe2db's cultivation outcomes and
# no PoE2 base is named "Hand Wraps" (AUDIT32 A32-10): no carrier found, UNVERIFIED (one source), so counted and labelled.
HANDWRAPS_MUTATED = re.compile(r"^HandWraps\w*MutatedVaal")


def cultivated_label(mod_id: str) -> str | None:
    """The audit's note for a Cultivated (Mutated Vaal) mod, or None for any other mod (A32-10, COV27)."""
    if mod_id in CULTIVATED_NO_CARRIER or HANDWRAPS_MUTATED.match(mod_id):
        return ("no current carrier (UNVERIFIED): a Mutated Vaal mod on none of the Cultivated uniques poe2db lists "
                "(one source; kept counted, not dropped)")
    if CULTIVATED.match(mod_id):
        return "a Cultivated outcome (Vaal Cultivation Orb, core since 0.5.0): live"
    return None


@lru_cache(maxsize=1)
def pob_unique_templates(src: Path = POB_SRC) -> frozenset[str]:
    """The template of every line of every unique in PoB-PoE2's Data/Uniques (tags like {variant:1} stripped)."""
    tag = re.compile(r"\{[^}]*\}")
    found = set()
    for path in (src / "Data" / "Uniques").rglob("*.lua"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = tag.sub("", line).strip()
            if line:
                found.add(template(line))
    return frozenset(found)


# (COV42, RESEARCH32 R32-3) The game's text for a unique-only mod can hold a placeholder where the item names a skill:
# Prism of Belief's UniqueJewelSpecificSkillLevelBonus1 / 2 read "+(1-3) to Level of all 0 Skills", while PoB builds the
# unique with one variant per skill gem (Data/Uniques/Special/Generated.lua 91-104: "+(1-3) to Level of all <gem>
# Skills"), so its template never matched. The placeholder "all # Skills" matches any capitalised skill name there.
# (COV42, RESEARCH32 R32-3, research/unique-only-open38.md) unique-only mods on no PoB-PoE2 unique that ARE live: open
# rows (PoB lacks them), no longer labelled "a PoE1 leftover or PoE2 content PoB lacks (UNVERIFIED)"
R32_3_OBTAINABLE = {
    # (COV43, the manager 18:15: RESEARCH32 R32-6 R-d corrects R32-3's HIGH, research/uniques16-and-rite-variant.md)
    "UniqueCharmRandomPossess1": ("obtainability UNVERIFIED (RESEARCH32 R32-6 R-d, correcting R32-3's HIGH; MEDIUM-LOW "
                                  "that it can drop): Rite of Passage's variant \"Possessed by a random Spirit for 20 "
                                  "seconds on use\" is listed on poe2db only (no date or note; poe2db keeps deprecated "
                                  "entries unmarked), left out of poe2wiki's page, and named in no patch note; the mod is "
                                  "real (PoB ModItemExclusive 2405). PoB's Rite of Passage has the nine named Spirits "
                                  "only (and parses no possession line), so the search can't offer this one"),
    "DemigodsVirtue1": ("Demigod's Virtue's \"Virtuous\" (RESEARCH32 R32-3, HIGH: no gameplay effect; a race reward, "
                        "drop disabled); not in PoB-PoE2's uniques"),
}
# (COV48, the manager 14:45: the "carrier unverified" rows) Carriers found for unique-only ("Exclusive") mods no PoB-PoE2
# unique lists: a base's implicit (game 26650308 BaseItemTypes.Implicit_Mods holds the mod: checked by scanning every
# exported table's mod columns for the mod's index, scratch/cov48/carrier.py), or a unique gloves line Way of the
# Stonefist transforms (poe2db's texts, builder/data/stonefist_uniques.json; builder/search/stonefist.py). Real carriers:
# the row stays open for its lane's model; the reason names the carrier instead of "UNVERIFIED".
_BASE_CARRIER = "a real carrier: the {} base's implicit (game 26650308 BaseItemTypes {}.Implicit_Mods); {}"
_STONEFIST_CARRIER = ("a real carrier: {}'s line as Way of the Stonefist transforms it (poe2db's transformed text, "
                      "builder/data/stonefist_uniques.json; stonefist.py applies it); a model in {}'s topic")
R32_3_OBTAINABLE.update({
    "CharmImplicitUseOnCurse1": _BASE_CARRIER.format("Cleansing Charm", "FourCharm13",
                                                     "flasks.py reads it: none against a pinnacle boss (RESEARCH #33)"),
    "FlailImplicitIgnoreBlock1": _BASE_CARRIER.format("Stone Flail", "FourFlail7",
                                                      "no pinnacle preset can Block (RESEARCH37 R37-2): ledger entry "
                                                      "items-cov49-unblockable-no-effect"),
    "ItemCanHaveBaseAndCatalystQuality": _BASE_CARRIER.format("Grasping Mail", "FourBodyStrDexInt3",
                                                              "catalysts: the search applies none (SEARCH)"),
    "RingImplicitMaximumQualityAdditional1": _BASE_CARRIER.format("Breach Ring", "FourRingBreach",
                                                                  "catalyst quality: the search applies none (SEARCH)"),
    "RingImplicitMaximumQualityAdditional2": _BASE_CARRIER.format("Refined Breach Ring", "FourRingBreach2",
                                                                  "catalyst quality: the search applies none (SEARCH)"),
    "TwistedEmpyreanVerisiumImplicitAdditionalFissures1": _BASE_CARRIER.format(
        "Runemastered Aberrant Sledge (a Verisium unique's)", "FourTwoHandMaceUnique1VerisiumUnique2", "Fissures: MECH-B"),
    "WaistgateVerisiumImplicitRunicWardCanOverflow1": _BASE_CARRIER.format(
        "Runemastered Heavy Belt (a Verisium unique's)", "FourBelt9VerisiumUnique3", "Runic Ward: MECH-C"),
    "UniqueRandomKeystoneFromTable1": ("a real carrier: Flesh Crucible (Diamond; Atziri's Vault), \"(1-33)\" is its "
                                       "random keystone's table index, which PoB models as one variant per keystone "
                                       "(Data/Uniques/Special/Generated.lua fleshCrucible): no effect of its own"),
    # (COV48's helper, poe2db 20 Nov: Rite of Passage's "Local Rite of Passage Modifiers /10" lists it beside the nine
    # named Spirits, no legacy tag; 0.5.0 fixed and buffed the player's Azmeri possessions; MEDIUM; roll weights unseen)
    "UniqueCharmRandomPossess1": ("a real carrier: Rite of Passage (Golden Charm) can roll \"Possessed by a random "
                                  "Spirit for 20 seconds on use\" (poe2db's current modifier list, no legacy tag; patch "
                                  "0.5.0; MEDIUM; supersedes R32-6 R-d's MEDIUM-LOW). PoB's Rite of Passage has only the "
                                  "nine named Spirits, so the search can't offer this variant: a charm model, MECH-C"),
    # (The three Orb of Sacrifice "Corrupted Upgrade" mods: settled by RESEARCH37 R37-1, now in NOT_OBTAINABLE; COV49)
    "UniqueRecoupLifeOpenWeakness1": ("a real carrier: The Master's Reach (poe2db; builder/data/stonefist_uniques.json "
                                      "lists the unique's lines); a recovery model in MECH-C's topic"),
    **{mod: _STONEFIST_CARRIER.format(unique, lane) for mod, unique, lane in (
        ("HandWrapsUniqueImmobiliseIncreasedDamageTaken1", "Sine Aequo", "MECH-C"),
        ("HandWrapsUniqueGainArmourEqualToStrength1", "Facebreaker", "MECH-B"),
        ("HandWrapsUniqueIncreasedAttackSpeed8", "Deathblow", "MECH-A"),
        ("HandWrapsUniqueRecoupLifeEnergyShieldOpenWeakness1", "The Master's Reach", "MECH-C"),
        ("HandWrapsUniqueLightningDamageCanElectrocute1", "Kitoko's Current", "MECH-A"),
        ("HandWrapsUniqueIncreasedPhysicalDamagePercent1", "Empire's Grasp", "MECH-A"),
        ("HandWrapsUniqueDecimatingStrike1", "Aerisvane's Wings", "MECH-A"),
        ("HandWrapsUniqueStrengthSatisfiesAllWeaponRequirements1", "Jarngreipr", "SEARCH (attribute requirements)"),
        ("HandWrapsUniqueElementalPenetrationBelowZero1", "Leopold's Applause", "MECH-A"),
        ("HandWrapsUniqueGainFearIncarnateOnCulling1", "Horror's Flight", "MECH-C"),
        ("HandWrapsUniqueGainChargesOnMaximumRage1", "Hateforge", "MECH-C"),
        ("HandWrapsUniqueNoManaRegenIfNotCritRecently1", "Nightscale", "MECH-C"))},
})


def dumped_unique_templates(data_dir: Path) -> frozenset[str]:
    """The template of every line of every unique in the dumped uniques.json (generated uniques expanded)."""
    path = data_dir / "uniques.json"
    if not path.exists():
        return frozenset()
    tag = re.compile(r"\{[^}]*\}")
    return frozenset(template(tag.sub("", line).strip()) for item in json.loads(path.read_text(encoding="utf-8"))["uniques"]
                     for line in item["raw"].splitlines() if tag.sub("", line).strip())


def placeholder_carried(text: str, carried: frozenset[str]) -> bool:
    """Whether a line whose skill name is the game's placeholder ("all 0 Skills") is carried with a real skill name."""
    t = template(text)
    if "all # Skills" not in t:
        return False
    rx = re.compile("^" + re.escape(t).replace(re.escape("all # Skills"), r"all [A-Z][\w' -]* Skills") + "$")
    return any(rx.match(c) for c in carried)


def poe1_leftover_mods(affixes: list[dict], carried: frozenset[str] | None = None) -> dict[str, str]:
    """Unique-only mods (pool "Exclusive") no PoB-PoE2 unique carries, by mod id -> the POE1_ONLY_GROUPS name that
    marks it a PoE1 leftover, or "" when none does (then whether a PoE2 source carries it is UNVERIFIED)."""
    carried = pob_unique_templates() if carried is None else carried
    lines: dict[str, list[str]] = defaultdict(list)
    for affix in affixes:
        if affix["pool"] == "Exclusive" and affix.get("line"):
            lines[affix["id"]].append(affix["line"])
    out = {}
    for mod, texts in lines.items():
        if any(template(t) in carried or placeholder_carried(t, carried) for t in texts):
            continue
        out[mod] = ""  # carried by no PoB-PoE2 unique, no PoE1-only group: stays open, labelled UNVERIFIED
        for name, rx in _POE1_ONLY:
            if any(rx.search(t) for t in texts):
                out[mod] = name
                break
    return out


# What PoB's calculations read -------------------------------------------------------------------

def unread_recovery(line: str) -> str | None:
    """The recovery.EVENT_UNREAD name this line is (a recovery line no rate reads yet), or None."""
    return next((name for name, pattern in EVENT_UNREAD.items() if pattern.search(line)), None)




def _strip_comments(text: str) -> str:
    text = re.sub(r"--\[(=*)\[.*?\]\1\]", "", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


def _call_args(text: str, start: int) -> str:
    depth, i = 1, start
    while i < len(text) and depth:
        c = text[i]
        if c == '"':
            j = text.find('"', i + 1)
            while j > 0 and text[j - 1] == "\\":
                j = text.find('"', j + 1)
            i = j if j > 0 else len(text)
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    return text[start:i - 1]


@lru_cache(maxsize=1)
def _param_built_names(text: str) -> set[str]:
    """Names a local helper builds from a parameter and the literal its callers pass: CalcPerform's
    `local function calcBuffRadius(actor, key)` queries key .. "Radius" / key .. "Area" through local variables, called
    as calcBuffRadius(actor, "Presence") (COVB1: PresenceArea / SurroundedArea lines were listed as no-ops)."""
    names: set[str] = set()
    for m in re.finditer(r"local function (\w+)\(([^)]*)\)(.*?)\n(?:end|\S)", text, re.S):
        name, params, body = m.group(1), [p.strip() for p in m.group(2).split(",")], m.group(3)
        suffixes = {param: re.findall(r'\b%s\s*\.\.\s*"(\w+)"' % re.escape(param), body) for param in params if param}
        if not any(suffixes.values()) or not QUERY.search(body):
            continue
        for call in re.finditer(r"(?<![\w.:])%s\(([^()]*)\)" % re.escape(name), text):
            args = [a.strip() for a in call.group(1).split(",")]
            for param, arg in zip(params, args):
                if suffixes.get(param) and (lit := re.fullmatch(r'"(\w+)"', arg)):
                    names.update(lit.group(1) + suffix for suffix in suffixes[param])
    return names


# Stats PoB reads by their modifier's tag, not its name (COV45, MECHB41's warcry stats): SkillStatMap 2958-2960 maps
# warcry_count_power_from_enemies to flag UsesWarcryPower under { type = "GlobalEffect", effectType = "Warcry" }. Only the
# Configuration page reads the name (ConfigOptions 726-727, ifFlag), but CalcActiveSkill 1053-1080 makes the skill's
# Warcry buff from any GlobalEffect-tagged modifier, and CalcPerform 2197-2284 gives that buff its Power from the enemies
# (WarcryPower, the boss's 20), its Empowered attacks (Num<Name>Empowers) and its uptime. Arctic Howl, whose stat sets lack
# the stat, gets no Warcry buff and 0 Empowers in PoB (GEM14 / MECHB33's find, models.py's Arctic Howl note). PROBE
# POE2T3939 (COV45, the manager's 16:00 ask; scratch/cov45/probe_warcry.py, probe3939.log): Infernal Cry alone, Warrior
# 90: with the stat PoB builds the buff "Infernal Cry: Warcry"; with the stat renamed to a dummy in PoB's skill data, no
# buff at all. (checked: every SkillStatMap entry holding a GlobalEffect-tagged flag: this one.)
BUFF_MAKING_STATS = {
    "warcry_count_power_from_enemies": (
        "PoB makes the skill's Warcry buff from it (its GlobalEffect Warcry tag, CalcActiveSkill 1053-1080) and gives that "
        "buff Power from the enemies, Empowers and uptime (CalcPerform 2197-2284); probe POE2T3939: Infernal Cry with it has "
        "the Warcry buff, without it none"),
}
# (COV56, the calibration's build 6) Names PoB maps a gem stat to that only show a Configuration box. Malice's
# critical_chance_against_aura_apply_brittle_every_x_ms ("Applies Critical Weakness to enemies every {0} seconds", PoB's
# malice.lua stat description; "brittle" is the game files' id for Critical Weakness) maps to flag ApplyCriticalWeakness
# (act_int.lua MalicePlayer statMap). Its only readers: CalcPerform 2592-2593 (copies the skill's flag to the player)
# and ConfigOptions 1908-1912 (ifFlag shows "Is the enemy Critically Weak"; the +0.5% crit a stack sits under
# Condition:ApplyCriticalWeakness, which only that box sets; no default). The audit called it read because ConfigOptions
# is among calc_reads' files. Checked both ways: grep of "ApplyCriticalWeakness" over PoB's Modules, Classes and Data
# (vendor PathOfBuilding-PoE2): those two places and the flag's writers (ModParser 4636-4640, the statMap). Other
# ifFlag-gated names are NOT listed: several are read another way (UsesWarcryPower by its tag, above; the ailments'
# "CanStack" and "inflict" flags by concatenation, CalcOffence 5230-5637), so each needs its own check.
# (COV56, the manager's 09:13 item 2) Conditions a gem stat's statMap puts its modifier under that only a Configuration box
# sets, which the optimizer never ticks: the stat's name is read, but under a condition that never holds, so nothing is
# counted. The coverage dump gives a gem stat's mod names, not their tags, so gem_stat_box_conditions reads the tags from
# PoB's skill files. Checked both ways (grep over PoB's Modules, Classes and Data, ModCache aside; and builder/ + bridge.lua
# for the box names): Moved2m is set only by ConfigOptions 499-502 (Momentum's "Moved 2m during Skill use?", ifFlag
# CanPerformSkillWhileMoving); HitProjectileRecently only by ConfigOptions 1146-1147 (conditionHitProjectileRecently) and
# the ModParser tag for item lines (which models.projectile_hit_recently_model reads by their text, not by the condition).
# NOT listed: conditions a model sets or replaces (Elemental Discharge's Freeze / Ignite / ShockConsumed boxes,
# models.DISCHARGE_BOXES; the Whirlwind buff, bridgeConditionWhirlwindBuff; Innervate's KilledShockedLast3Seconds, which
# assumptions' crit-uptime model replaces), enemy-side ones the generic check misreads (Enfeeble's Unique) and skill-part
# ones (ReturningProjectile): scratch/cov56/gemcond.py lists the 25 candidates.
GEM_BOX_CONDITIONS = {
    "Moved2m": "Momentum's \"Moved 2m during Skill use?\" box (ConfigOptions 499-502)",
    "HitProjectileRecently": "\"Have you Hit with a Proj. Att. Recently?\" (ConfigOptions 1146-1147)",
}


@lru_cache(maxsize=1)
def gem_stat_box_conditions(src: Path = POB_SRC) -> dict[str, str]:
    """{gem stat: the box} for stats whose every statMap modifier needs a GEM_BOX_CONDITIONS condition (PoB's skill files)."""
    found: dict[str, str] = {}
    for path in [src / "Data" / "SkillStatMap.lua", *sorted((src / "Data" / "Skills").glob("*.lua"))]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'\["([^"]+)"\]\s*=\s*\{(.*?)\n\t*\},', text, re.S):
            mods = re.findall(r'(?:mod|flag|skill)\((.*?)\)\s*,?\s*$', m.group(2), re.M)
            needs = [set(re.findall(r'type = "Condition", var = "(\w+)"', x)) & set(GEM_BOX_CONDITIONS) for x in mods]
            if mods and all(needs):
                found[m.group(1)] = GEM_BOX_CONDITIONS[sorted(needs[0])[0]]
    return found


CONFIG_BOX_ONLY = {
    "ApplyCriticalWeakness": (
        "PoB only shows its Configuration box from it (ConfigOptions 1908-1912, ifFlag; no default): nothing counts the "
        "Critical Weakness unless the box is ticked"),
}


# Mods with no league carrier found, not shown unobtainable (UNVERIFIED): a label only, nothing dropped (CLAUDE.md rule 5)
NO_KNOWN_CARRIER = {
    "UniqueTwoHandedWeaponLightningStunMultiplier1": (
        "no league character can be shown to get it (RESEARCH34 R34-6, UNVERIFIED: one unique mod in the game data, the "
        "0.3.0 Abyss block, not a PoE1 leftover (RePoE); on no PoB or poe2db unique; probably an unreleased item). If a "
        "carrier appears: the game scales only the Lightning part of the buildup (MEDIUM-HIGH), PoB the whole hit "
        "(Condition:LightningHasDamage, CalcOffence 4491): an OVERCOUNT on mixed hits to fix then"),
}

@lru_cache(maxsize=1)
def skill_flag_stats(src: Path = POB_SRC) -> dict[str, str]:
    """Stats PoB maps to a skill flag and no modifier (SkillStatMap.lua, the skills' own statMaps): `skill_can_fire_arrows`
    sets skillFlag "arrow", which CalcActiveSkill turns into KeywordFlag.Arrow (COVC2). The audit called those "maps to
    nothing" because the map holds no modifier; a flag map counts as read."""
    found: dict[str, str] = {}
    files = [src / "Data" / "SkillStatMap.lua", *sorted((src / "Data" / "Skills").glob("*.lua"))]
    for path in files:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            for stat, flag in re.findall(r'\["([^"]+)"\]\s*=\s*\{\s*skillFlag\s*=\s*"(\w+)"', text):
                found[stat] = flag
    return found


UNPROBED_LIMITS = {"HyenaLimit"}  # (COV51: calc_reads' minion-limit note)


def calc_reads(src: Path = POB_SRC) -> dict:
    """Names PoB's calculations query: exact names, wildcard patterns from concatenations, and every
    string literal and skillData key in the code (for condition and skill-data checks)."""
    files = [p for p in (src / "Modules").glob("*.lua") if p.name != "ModParser.lua"]
    files += list((src / "Classes").glob("*.lua"))
    files += list((src / "Data" / "Skills").glob("*.lua")) + [src / "Data" / "Minions.lua", src / "Data" / "Misc.lua"]
    exact: set[str] = set()
    patterns: set[str] = set()
    literals: set[str] = set()
    skill_data: set[str] = set()
    for path in files:
        if not path.exists():
            continue
        text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        literals.update(STRING.findall(text))
        skill_data.update(re.findall(r"skillData\.(\w+)", text))
        skill_data.update(re.findall(r'skillData\["(\w+)"\]', text))
        for m in QUERY.finditer(text):
            args = _call_args(text, m.end())
            for chain in CHAIN.findall(args):
                parts = re.split(r"\s*\.\.\s*", chain)
                # a concatenated part may be a slot name with a space ("EffectOfBonusesFrom" .. slot reads
                # "EffectOfBonusesFromRing 1", CalcPerform 1491; COV41, probe POE2T3858: Ingenuity's left / right ring
                # line moves Life), so a wildcard takes spaces too
                glob = "".join(re.escape(p[1:-1]) if p.startswith('"') else r"[\w ]*" for p in parts)
                if glob.replace(r"[\w ]*", ""):  # not a bare wildcard
                    patterns.add(glob)
            exact.update(STRING.findall(args))
        exact.update(_param_built_names(text))
        # (COV51, PLANS7's gs-triage-behaviour.md rows 58-60) a minion's limit name is read through a variable:
        # Data/Minions.lua gives each minion `limit = "ActiveZombieLimit"` (HyenaLimit, ActiveUnearthBoneConstructLimit,
        # ...), and CalcOffence 1420 / CalcPerform 1352 read calcLib.val(skillModList, minionData.limit): the
        # Raise Zombie / Unearth / Cackling Companions limit stats (SkillStatMap 2576 / 2600 / 2618) were listed as read
        # by nothing (probe POE2T5211: Raise Zombie's ActiveMinionLimit 10 and Unearth's 20 are the gems' stats). Not
        # HyenaLimit: probe POE2T5212 found no hyena minion to read it on (Cackling Companions' main skill "Companion:
        # {0}", no ActiveMinionLimit, with or without a spear), so "PoB reads it" stays unshown and its row open
        if path.name == "Minions.lua":
            exact.update(set(re.findall(r'\blimit\s*=\s*"(\w+)"', text)) - UNPROBED_LIMITS)
    # The tool's own engine reads some names PoB's calculations don't (charm and flask charge gain for the
    # uptime of builder/search/flasks.py, ...): through the same queries, or its local helpers base("X") / inc("X")
    # (COV1, 12 Nov 2026: "Charm Charges gained" lines were listed as no-ops though the charm uptime reads them)
    # The helpers read one type each (bridge.lua: base("X") sums BASE, inc("X") INC): a name only they read counts for
    # those types only ("20% more Charm Charges gained" is a MORE nothing applies; COV1's no-op list, COV4)
    helper_types: dict[str, set[str]] = {}
    if BRIDGE.exists() and src == POB_SRC:
        text = _strip_comments(BRIDGE.read_text(encoding="utf-8", errors="replace"))
        for m in QUERY.finditer(text):
            exact.update(STRING.findall(_call_args(text, m.end())))
        for helper, name in re.findall(r'(?<![\w.:])(base|inc|more|flag)\("(\w+)"\)', text):
            helper_types.setdefault(name, set()).add(helper.upper())
    bridge_types = {name: types for name, types in helper_types.items()
                    if name not in exact and not (patterns and re.match("^(?:%s)$" % "|".join(sorted(patterns)), name))}
    exact.update(helper_types)
    # Conditions and multipliers are read through tags on other modifiers (var = "Name"), which the
    # parser writes too
    parser = _strip_comments((src / "Modules" / "ModParser.lua").read_text(encoding="utf-8", errors="replace"))
    tag_vars = set(re.findall(r'var\s*=\s*"([^"]+)"', parser))
    for blob in re.findall(r"varList\s*=\s*\{([^}]*)\}", parser):
        tag_vars.update(STRING.findall(blob))
    for path in files:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            tag_vars.update(re.findall(r'var\s*=\s*"([^"]+)"', text))
    config = src / "Modules" / "ConfigOptions.lua"
    if config.exists():
        tag_vars.update(re.findall(r'"Condition:(\w+)"', config.read_text(encoding="utf-8", errors="replace")))
    return {"exact": exact, "patterns": re.compile("^(?:%s)$" % "|".join(sorted(patterns))) if patterns else None,
            "literals": literals, "skill_data": skill_data, "tag_vars": tag_vars, "bridge_types": bridge_types}


def name_read(name: str, reads: dict | None = None) -> bool:
    """Does any calculation ask for this modifier name? `Outer>Inner` names (a modifier wrapped for
    an enemy or a minion) need both."""
    reads = reads or calc_reads()
    if ">" in name:
        return all(name_read(part, reads) for part in name.split(">", 1))
    if name.startswith("SkillData:"):
        return name.split(":", 1)[1] in reads["skill_data"]
    if ":" in name:  # Condition:X, Multiplier:X, ... read through tags or by name
        kind, var = name.split(":", 1)
        return var in reads["tag_vars"] or name in reads["exact"] or name in reads["literals"] or \
            bool(reads["patterns"] and reads["patterns"].match(name))
    return name in reads["exact"] or bool(reads["patterns"] and reads["patterns"].match(name))


def typed_names(line: dict, reads: dict) -> list[str]:
    """A parsed line's names, with a name only the bridge's typed helpers read (calc_reads' bridge_types) marked
    "Name (TYPE)" when every one of the line's mods of that name has another type: unread() then lists it."""
    names = list(line.get("names") or [])
    typed = reads.get("bridge_types") or {}
    mods = line.get("mods") or []
    out = []
    for name in names:
        kinds = {m.get("t") for m in mods if m.get("n") == name}
        if name in typed and kinds and not kinds & typed[name]:
            out.append(f"{name} ({'/'.join(sorted(k or '?' for k in kinds))})")
        else:
            out.append(name)
    return out


def unread(names: list[str] | dict, reads: dict | None = None) -> list[str]:
    """The names no calculation reads, when that's all of them (a line also writing a marker
    nothing reads, like "+10 to Strength and Dexterity"'s StrDex, still counts through the rest)."""
    names = list(names.values()) if isinstance(names, dict) else names or []
    missing = sorted({n for n in names if not name_read(n, reads)})
    return missing if names and len(missing) == len(set(names)) else []


# What a line is about, and how much it matters ---------------------------------------------------

# A line is "flavour" (moves none of the tool's numbers: a boss's DPS, EHP, recovery or speed) only when it is
# recognised as such: loot, gold, experience, light radius, fishing, movement speed, item labels. Anything else the
# patterns don't recognise is "unclassified": it may move a build and nobody has read it yet, so it stays in the open
# list. Until 12 Nov 2026 the fallback was "flavour"; a sample of the 1,647 "flavour" templates of the 8 Nov report
# found most of them real (Grants Skill lines, Chain / Fork, Crushing Blows, conversion and trigger-energy gem stats,
# charges, Rage, Volatility, stun, slow, charm charges; COV1's report has the counts).
MECHANICS = (
    ("damage", r"damage|critical|penetrat|crit\b|ignite|shock|bleed|poison|freeze|chill|electrocute|"
               r"exposure|withered|wither|curse|projectile|area of effect|skill effect|accuracy|"
               r"impale|culling|cull\b|overwhelm|power charge|frenzy charge|onslaught|archon|infusion|"
               r"spell|attack|minion|totem|strike|combo|glory|empower|maximum hit|deal|"
               r"\bchain|\bfork|pierce|grants? (skill|level|summon|effect)|trigger|surge|volatility|\brage\b|"
               r"charges?\b|\bpower\b|remnant|infernal flame|crushing blow|weakness|energy|banner|valour|herald|"
               r"golem|spectre|skeleton|zombie|companion|beast|shrine|divinity|madness|lance|kill|shapeshift|slam|"
               r"grenade|crossbow|bolt|spear|arrow|bell|mine|trap|explosive|fear incarnate|mark\b|puppet|plant|"
               r"overgrow|jade|adaptation|tailwind|rhythm|fervour|haunt|abyssal|wasting|surpassing|mage's legacy|"
               r"perfect timing|on hit|hits\b|weapon"),
    ("defence", r"armour|evasion|energy shield|resist|block|damage taken|maximum life|life\b|"
                r"\bward\b|deflect|suppress|endurance charge|avoid|ailment|stun|reduction|"
                r"spirit|reservation|mana\b|darkness|guard|unaffected|dodge|parry|aegis|smoke cloud|phasing|"
                r"slow|maim|blind|debilitat|intimidat|pinn|pin\b|immobilis|regrowth|knock|frozen|thorns|consecrated"),
    ("recovery", r"regenerat|leech|recover|recoup|flask|charm|life on|mana on|refill|restore|on use|used when"),
    ("speed", r"attack speed|cast speed|skill speed|cooldown|duration|warcry speed|reload|use speed|detonation"),
    ("behaviour", r"socket|jewel|modifier|attribute|strength|dexterity|intelligence|level of|passive|"
                  r"ascendancy|keystone|equip|requirement|corpse|conquered|limit|maximum number|ring\b|"
                  r"gloves|boots|helmet|shield|instilled|transformed"),
)
MECHANIC_WEIGHT = {"damage": 4.0, "defence": 3.0, "speed": 3.0, "recovery": 2.5, "behaviour": 1.5,
                   "unclassified": 1.5, "flavour": 0.1}
FLAVOUR = re.compile(r"movement speed|rarity|quantity|light radius|experience|pickup|cosmetic|display|"
                     r"visual|currency|gold\b|fish|footprints|^corrupted$|^mirrored$|^historic$|"
                     r"^raven-touched$|^version:|selected alt variant|^maximum quality|can be modified while|"
                     r"inventory slots|chest which adds", re.I)
SOURCE_WEIGHT = {"keystone": 6.0, "ascendancy": 5.0, "notable": 3.0, "jewel socket": 1.0, "small": 0.5,
                 "unique": 1.0, "affix": 2.0, "rune": 2.0, "jewel": 1.5, "corruption": 1.0, "desecrated": 1.5,
                 "exclusive": 0.3, "flask": 1.0, "charm": 1.0, "limb": 0.5,
                 "active gem": 3.0, "support gem": 2.0, "granted skill": 1.0, "hidden": 0.2}
POOLS = {"Item": "affix", "Runes": "rune", "Jewel": "jewel", "Corruption": "corruption", "Desecrated": "desecrated",
         "Exclusive": "exclusive", "Flask": "flask", "Charm": "charm", "IncursionLimb": "limb"}
DISPLAY_STAT = re.compile(r"(^|_)(display|quality_display|visual|cosmetic|hidden|virtual|"
                          r"skill_is_|is_area_damage|base_deal_no_damage|active_skill_base|level_)|"
                          r"_is_gem$|_display_|global_|_art_|_sound|_vfx|show_average_damage|damage_prediction|"
                          r"hit_effect_index", re.I)


def mechanic(text: str) -> str:
    if FLAVOUR.search(text) and not re.search(r"damage|critical|resist|armour|evasion|energy shield|life|"
                                             r"attack|cast speed|skill speed", text, re.I):
        return "flavour"
    if re.search(r"recover|regenerat|leech|recoup", text, re.I):
        return "recovery"
    if re.search(r"taken|on you\b|you take|^take |prevented|hits against you|enemies hitting you|resistances?\b|"
                 r"armour|evasion|energy shield|block|guard|maximum life|deflect|avoid", text, re.I):
        return "defence"
    for name, pattern in MECHANICS:
        if re.search(pattern, text, re.I):
            return name
    return "unclassified"


# Gem stats that only shape how a skill looks or is aimed: art, sound, animation, indicators, auto-targeting.
FLAVOUR_STAT = re.compile(r"art_variation|_variation$|animation|statset_index|_sound|_vfx|indicator|autotarget|"
                          r"_angle|angle_allowance|ease_in|ease_out|pushiness|turn_duration|visual|recoil|"
                          r"do_not_face|face_target|warp_speed|show_|_art_|knockback|movement_speed|"
                          r"pickup_range|step_distance", re.I)
# ...unless the stat also names something that moves the numbers (a display pattern matched on the way past it)
NOT_FLAVOUR_STAT = re.compile(r"damage(?!_prediction)|life|mana|lose_|glory|instant|not_considered|weakness|"
                              r"per_.*knockback|area_of_effect", re.I)
STAT_BEHAVIOUR = re.compile(r"projectile_(uses|spread|maximum|minimum|distance|ballistic|speed)|spread|angle|offset|"
                            r"animation|channel_|turn_duration|check_for_targets|base_is_projectile|modular|show_|"
                            r"contact_|collide|variation|maintain_|precise_movement|range|action_distance|queue|"
                            r"_delay_ms|spacing|number_of_.*_allowed|cannot_|can_be_|_override$|statset_index|"
                            r"is_|_index|limitation|specifier|movement_speed|stun|knockback|show_|prediction|_distance|"
                            r"repeat_interval|remnant_duration|_debuff_duration_ms|attack_duration_ms|ground_effect_area|"
                            r"radius|area_of_effect", re.I)
# (COV37, GEM15's find: has_freeze_cull, Arctic Howl's "Freeze enemies that are Primed for Freeze", moves Freeze uptime, so
# it's "damage" through STAT_KINDS' "freeze", not behaviour: taken out of STAT_BEHAVIOUR, where it never applied anyway)
STAT_KINDS = (
    ("damage", r"damage|crit|penetrat|exposure|_as_|wither|shock|ignite|poison|bleed|impale|freeze|chill|"
               r"electrocut|cull|additional_projectile|number_of_projectiles|chain|repeat|echo|_final|"
               r"convert|conversion|physical|fire|cold|lightning|chaos|glory|energy|trigger|magnitude|"
               r"effect_\+%|empower|power_charge|frenzy|charge|rage|infusion|stage|maim|herald|consume|"
               r"interval_ms|usages|jagged|ground"),
    ("defence", r"armour|evasion|energy_shield|block|resist|damage_taken|maximum_life|ward|guard|stun|blind|"
                r"slow|dodge|parry"),
    ("recovery", r"leech|regen|recover|recoup|life_gain|mana_gain|remnant|orb"),
    ("speed", r"speed|cooldown|duration"),
)


# Gem behaviour that decides how many hits a use makes on a boss, or how often it's used: counts of projectiles, chains,
# forks, pierces, splits, repeats, beams, waves, totems, minions, corpses, stacks, explosions, and cooldowns
BEHAVIOUR_DAMAGE = re.compile(r"number_of|projectiles?_nova|projectile_count|additional_projectile|chain|fork|pierce|split|"
                              r"hits?_allowed|hit_limit|repeat|_allowed|maximum_number|cooldown(?!_recovery_modifiers)|totem|"
                              r"corpse|stacks|explosion|fissure|beams?\b|waves?\b|pustule|_per_second|per_minute", re.I)


def stat_mechanic(stat: str) -> str:
    """What a gem stat is about, from its id. "flavour" only for display stats and stats that shape how a skill
    looks or is aimed (art, animation, indicators, auto-targeting); a stat about where or how a skill lands (radius,
    range, "cannot_...", overrides) is "behaviour", and one no pattern recognises is "unclassified", not flavour."""
    if stat.endswith("_is_gem") or re.search(r"^(display|quality_display)_|_display_|show_average|cursor_targeting|^base_deal_no_damage", stat) \
            or (DISPLAY_STAT.search(stat) or FLAVOUR_STAT.search(stat)) and not NOT_FLAVOUR_STAT.search(stat):
        return "flavour"
    if BEHAVIOUR_DAMAGE.search(stat):  # how many hits a use makes, or how often: it changes damage on a boss
        return "behaviour"
    for name, pattern in STAT_KINDS:
        if re.search(pattern, stat, re.I):
            return name
    if STAT_BEHAVIOUR.search(stat):
        return "behaviour"
    return "unclassified"


def template(text: str) -> str:
    return NUMBER.sub("#", text).strip()


# (COV63; NINJA1's gap list via the manager 27 Nov 15:22: Zarokh's Gift on 14 of THE USER's 21 builds read as `none`)
# A jewel-socket node's own "stat" is the socket's label, not a modifier: Zarokh's Gift (node 11184, the only
# Socket-type line in gaps.json: checked, gaps.json passives by type Normal 499 / Notable 364 / Keystone 21 / Socket 1)
# carries "Sinister Jewel Socket", which ModCache leaves as text, but PoB's tree makes the node itself a socket
# (TreeData 0_5 tree.json: isJewelSocket, sinister, aliasPassiveSocket DeliriumAnoint_ZarokhsGift_; ItemsTab.lua 2651:
# a Sinister socket takes non-unique jewels), and the jewel in it is counted: probe POE2T6993 (scratch/cov63/
# sinister_probe.py, example build 1): emptying "Jewel 11184" (a rare with 7% increased Attack Speed) moves TotalDPS
# 7,274,670.6 -> 6,543,469.7 and Speed 4.608 -> 4.490. So the label line is PoB's: the socket is read as a socket.
SOCKET_LABEL = re.compile(r"^(?:Sinister )?Jewel Socket$", re.I)
SOCKET_LABEL_WHY = ("the node is a jewel socket in PoB's tree (isJewelSocket; this line is its label): a jewel in it is "
                    "counted (probe POE2T6993)")


def socket_label_status(line: str, kind: str) -> tuple[str, str | None, str] | None:
    """("pob", None, why) for a jewel-socket node's label line (SOCKET_LABEL's note), else None."""
    if kind == "jewel socket" and SOCKET_LABEL.match(line.strip()):
        return "pob", None, SOCKET_LABEL_WHY
    return None


def passive_kind(gap: dict) -> str:
    if gap.get("ascendancy"):
        return "ascendancy"
    return {"Keystone": "keystone", "Notable": "notable", "Socket": "jewel socket"}.get(gap.get("type"), "small")


def unique_popularity(league: str | None = None) -> dict[str, float]:
    """Source weight per unique from poe.ninja's economy cache (listings and price; never fetched here)."""
    from builder.data.prices import PriceFeed

    root = ROOT / "data" / "prices"
    leagues = sorted((p for p in root.iterdir() if (p / "uniques").is_dir()), key=lambda p: p.stat().st_mtime) \
        if root.exists() else []
    if not leagues:
        return {}
    table = PriceFeed(root).table(league or leagues[-1].name, refresh=False)
    weights = {}
    for name in table.uniques:
        price = table.unique(name)
        listings = (price.listings or 0) if price else 0
        value = (price.divine or 0) if price else 0
        weights[name] = 1.0 + math.log10(1 + listings) + (1.0 if value >= 1 else 0.0)
    return weights


# Statuses -----------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def cached_templates(src: Path = POB_SRC) -> dict[str, bool]:
    """PoB's ModCache (its parse of every tree and unique line it has seen), by number template:
    True if some line of that template parses."""
    found: dict[str, bool] = {}
    path = src / "Data" / "ModCache.lua"
    if path.exists():
        for m in re.finditer(r'^c\["(.*)"\]=\{(.*)\}$', path.read_text(encoding="utf-8"), re.M):
            parsed = not m.group(2).startswith("nil") and m.group(2).endswith(",nil")
            key = template(m.group(1))
            found[key] = found.get(key, False) or parsed
    return found


# Lines ModParser ignores but PoB's Classes/Item.lua reads from the item text itself (COV36), each with its probe:
# - "This item gains bonuses from Socketed Items as though it was <type>" (Darkness Enthroned's five variants, The Bringer
#   of Rain, Grasping Ring): Item.lua 1279 captures <type> into socketedAugmentTypeOverride and GetSocketedAugmentTypes
#   applies the augments' <type> lines. Probe POE2T2594 (tests/test_as_though_body_armour.py): The Bringer of Rain takes
#   Amanamu's Gaze's body armour line (more Armour than without the line), Darkness Enthroned (a belt) Desert Rune's.
# - "This item gains bonuses from Socketed Soul Cores as though it was also <type>" (Atziri's Splendour's four variants,
#   their Vaal Cultivated outcomes): Item.lua 1280 captures <type> into socketedSoulCoreType and 1527 applies a Soul
#   Core's <type> line too. Probe POE2T2599 (tests/test_as_though_soul_core.py): Atziri's Splendour (Helmet) with a Soul
#   Core of Zalatl, whose only line is its helmet "5% increased maximum Mana": Mana 444 with the line, 424 without, 424
#   with no core.
ITEM_TEXT_READS = [
    (re.compile(r"^This item gains bonuses from Socketed Items as though it was a? ?.+$", re.I),
     "PoB's Item.lua reads it from the item text (socketedAugmentTypeOverride, Item.lua 1279; probe POE2T2594)"),
    (re.compile(r"^This item gains bonuses from Socketed Soul Cores as though it was also a? ?.+$", re.I),
     "PoB's Item.lua reads it from the item text (socketedSoulCoreType, Item.lua 1280 / 1527; probe POE2T2599)"),
    # (COV41) Kalandra's Touch's "Reflects opposite Ring": ModParser leaves it unparsed (display only, 3467), but
    # CalcSetup 1637-1650 copies the other ring's mods onto it by the item's name (and CalcPerform 1493 / ModStore 810
    # swap ring-slot lookups). Probe POE2T3865 (scratch/cov41/probe_kalandra.py): a +100 Life / Mana rare in Ring 1, a
    # blank rare in Ring 2: Life 1355; Kalandra's Touch in Ring 2: Life 1460 (the ring counted twice).
    (re.compile(r"^Reflects opposite Ring$", re.I),
     "PoB copies the opposite ring onto Kalandra's Touch by the item's name (CalcSetup 1637; probe POE2T3865)"),
]


def ignored_line_status(line: str, on_passive: bool) -> tuple[str, str | None, str]:
    """Status of a line PoB's parser ignores: a ledger translation, a custom model, or nothing."""
    if RANGE_TEXT.search(line) and cached_templates().get(template(line)):
        # A unique's roll range like "+(-40-40)%" fails at its middle roll ("0%"); rolled, PoB parses it
        return "pob", None, "PoB parses the rolled line (only the range text fails)"
    if not on_passive and (read := next((why for rx, why in ITEM_TEXT_READS if rx.match(line.strip())), None)):
        return "pob", None, read
    if entry := model_entry(line):
        return "model", entry.id, f"fed to the {entry.raw['model']} model"
    if translated := translate(line):
        entry, lines = translated
        if not entry.on_items and not on_passive:
            # (a `share:` line has an item form since COV17: ledger.item_lines; flag:, if: and mult: lines don't)
            return "none", entry.id, "ledger entry is conditional, which applies on passives only"
        if not lines and entry.raw.get("absorbed_by"):
            return "ledger", entry.id, "absorbed by an assumption: " + entry.raw["absorbed_by"]
        return "ledger", entry.id, "no effect on what's calculated" if not lines else "translated"
    if condition := resolver_condition(line):
        return "model", f"condition:{condition.var}", f"a source of {condition.label} for the condition resolver"
    if entry := play_row_entry(line):
        return "model", entry.id, f"read by the {entry.raw['model']} play row (0 in the headline: part-time)"
    return "none", None, "PoB ignores the line"


def noop_status(line: str, missing: list[str], on_item: bool) -> tuple[str, str | None, str]:
    """Status of a line PoB parses into names no calculation reads (a no-op; COVD1, 12 Nov 2026: no ledger route could
    close one, the audit added them as "none" unconditionally). What closes one, and only where it really applies:
    - a custom model or the condition resolver reads the line (bridge.sourceLines gives every line, parsed or not);
    - a `pob_wrong` entry replaces the line with one PoB calculates: applied on items (uniques' text, rares' affixes
      and implicits, runes: context.corrected_raw, ledger/items.corrected_item), not on passives or Timeless passives;
    - a ledger entry that records "no effect" (no replace lines): nothing to apply.
    A `pob_gap` translation isn't applied to a line PoB parses (only to lines it ignores), so it doesn't close one."""
    unread_why = "parsed, but no calculation reads " + ", ".join(missing)
    if entry := model_entry(line):
        return "model", entry.id, f"{unread_why}; fed to the {entry.raw['model']} model"
    if condition := resolver_condition(line):
        return "model", f"condition:{condition.var}", f"{unread_why}; a source of {condition.label} for the condition resolver"
    if fixed := wrong(line):
        if on_item and fixed[1] is None:
            return "ledger", fixed[0].id, f"{unread_why}; switched off on items, a model reads it (pob_wrong)"
        if on_item:
            return "ledger", fixed[0].id, f"{unread_why}; replaced by a line PoB calculates (pob_wrong)"
        return "none", fixed[0].id, f"{unread_why}; a pob_wrong entry replaces it, but only on items, not passives"
    if translated := translate(line):
        entry, lines = translated
        if not lines:
            return "ledger", entry.id, f"{unread_why}; recorded as no effect on what's calculated"
        return "none", entry.id, f"{unread_why}; a ledger entry translates it, but translations apply only to lines PoB ignores"
    if unused := parsed_unused(line):
        return "none", unused.id, f"{unread_why}; {unused.title} (routed to {unused.raw.get('owner', '?')})"
    if entry := play_row_entry(line):
        return "model", entry.id, f"{unread_why}; read by the {entry.raw['model']} play row (0 in the headline: part-time)"
    return "none", None, unread_why


def unused_status(line: str, entry) -> tuple[str, str | None, str]:
    """Status of a line PoB parses and whose names are read, but which a `parsed_unused` entry records as moving none of
    the tool's numbers (COV27; ledger/__init__'s notes): not counted, unless a model now reads the line."""
    if model := model_entry(line):
        return "model", model.id, f"fed to the {model.raw['model']} model"
    return "none", entry.id, (f"parsed and read by PoB, but not applied where the tool counts it: {entry.title} "
                              f"(routed to {entry.raw.get('owner', '?')})")


APPLIED_POOLS = {"Item", "Jewel", "Flask", "Charm", "Runes", "implicit"}


def not_applied(status: str, entry: str | None, reason: str, pool: str = "Item", bonded: bool = False,
                unique_templates: frozenset[str] = frozenset(), line: str = "") -> tuple[str, str | None, str]:
    """Whether the search applies a ledger translation of this item line (COV1's finding, 12 Nov 2026: until then
    only passive lines and unique items' text got them, and the audit counted 64 item lines covered that no build got).
    Since COV2 (builder/ledger/items.py) the search applies them to rare, magic and normal items' affix and implicit
    lines and to socketed runes (`pool` "Item", "Jewel", "Flask", "Charm", "Runes", "implicit"); since COV13 a rune's
    or Idol's Bonded line too, under PoB's own Bonded switch (items.py: it counts only with a Bonded source, Wisdom of
    the Maji or an Idol in an item that lets Idols use their Bonded lines; `bonded` is kept for callers). Still not
    applied: corruption and limb mods (the search never rolls them), desecrated ones (rolled only with SEARCH's
    switches on, OFF by default), a unique's own mod
    ("Exclusive": applied through the unique's text when PoB lists the line among the unique's ignored ones, so it
    is counted there), and Timeless jewels' passives ("legion"). An entry recording "no effect" needs nothing applied."""
    if status != "ledger" or reason != "translated":
        return status, entry, reason
    if pool in APPLIED_POOLS:
        return status, entry, reason
    if pool == "Exclusive" and template(line) in unique_templates:
        return status, entry, "translated in the unique's own text (its unique record)"
    if pool == "Exclusive" and template(line) in stonefist_templates():
        # (COV46) a hand-wraps line of a unique Way of the Stonefist transforms (poe2db's text, builder/search/
        # stonefist.py): its translation is applied there (stonefist.ledger_corrected)
        return status, entry, "translated in Way of the Stonefist's transformed unique text (stonefist.ledger_corrected)"
    why = {"Exclusive": "a unique's mod PoB doesn't list among the unique's ignored lines",
           "legion": "a Timeless jewel's passive (the tree side)",
           # (COV47) since train 99 (SEARCH30 / 31, builder/search/desecrated.py) the search can roll one Desecrated affix
           # on a rare, through rares.py's corrected_item (so the translation applies), but only with its switches on
           "Desecrated": ("the search rolls Desecrated mods only with POE2_SEARCH_DESECRATED / _BREACH / _JEWEL on (all "
                          "OFF by default, desecrated.py; with them on, the translation applies)")
           }.get(pool, f"the search never rolls {pool} mods")
    return "none", entry, f"{NOT_APPLIED_WHY}{why}"


NOT_APPLIED_WHY = "a ledger entry translates it, but the search doesn't apply it: "


@lru_cache(maxsize=1)
def stonefist_templates() -> frozenset[str]:
    """Templates of the explicit lines of the uniques Way of the Stonefist transforms (poe2db's Fists of Stone text)."""
    from builder.search import stonefist

    return frozenset(template(line) for unique in stonefist.unique_texts().values() for line in unique["explicit"])


@lru_cache(maxsize=1)
def cultivated_scored_mods() -> frozenset[str]:
    """Mod ids of the Cultivated outcomes the search offers on some cultivatable unique (cultivated.outcomes: not
    marked not_offered, and every line PoB doesn't read has an item stand-in, cultivated.outcome_lines)."""
    from builder.search import cultivated

    return frozenset(o["mod"] for name in cultivated.uniques() for o in cultivated.outcomes(name))


def resolver_condition(line: str):
    """The condition resolver (builder/search/conditions.py) reads every line of the build as
    evidence, ignored lines included: one that grants Onslaught or a charge switches it on."""
    from builder.search import conditions as c

    for condition in c.CONDITIONS:
        positive, negative = c._PATTERNS[condition.var]
        if any(p.search(line) for p in positive) and not any(p.search(line) for p in negative):
            return condition
    # The sources Conditions.could_source rates by an uptime model rather than a text rule (COV5; before, the audit called
    # every Maim / Blind / Hinder / Exposure / Arcane Surge / Armour Break line uncounted): the line at its low roll, and
    # not a debuff line whose event the model rates 0 (a kill, a Heavy Stun, ...: _debuff_source), which stays open
    rolled = re.sub(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)", r"\1", line)
    for condition in c.CONDITIONS:
        var = condition.var
        if var in c.DEBUFFS and (m := c._debuff_pattern(var).search(rolled)) and not c.DEBUFF_NOT_SOURCE.search(rolled):
            # (COV48, train 102's b9c1c49d) the crafted jewels' "while you have a <X> and a <Y> socketed in your tree":
            # conditions._debuff_source strips the clause (socketed_pair) and rates the rest when both are socketed, so
            # the audit reads the rest the same way
            if (pair := getattr(c, "SOCKETED_PAIR", None)) and pair.search(rolled):
                if not (m := c._debuff_pattern(var).search(pair.sub("", rolled))):
                    continue
            when = (m.group(2) if m.group(2) is not None else f"{m.group(3)} {m.group(4)}").lower()
            # (the events _debuff_source rates: your hits and crits, the boss's hits on you or your Blocks, your
            # Presence, your curses, Full Breaks; any other wording it rates 0, so the line stays open here). (COV37) And
            # the events it has rated since: your Poisons / Ignites / Shocks (WHEN_YOU_AILMENT, MECH63's Murkshaft line),
            # the boss's Stuns on you (MECHC25's Blinding Flash), a raised shield's Blind (MECHC21's Bastion of Light), and
            # a Warcry-subject line with no "when" (MECH78)
            # (COV47) and the two event kinds conditions._debuff_source rates since MECH85 / MECH92, with its own exclusions:
            # your Heavy Stuns of the boss ("Enemies you Heavy Stun (while Shapeshifted) are Intimidated", models.
            # heavy_stun_rate) and your Immobilises / Pins ending (Fearful Paralysis, Lockdown; models.immobilise_rate)
            if "heavy stun" in when and not re.search(r"\bkill|immobilis|burning|socketed|\bfrozen by\b", when):
                return condition
            if ("immobilis" in when or "unpinned" in when) and not re.search(r"\bkill|shapeshift|burning|socketed", when):
                return condition
            if not re.search(r"\bkill|heavy stun|immobilis|shapeshift|burning|socketed|\bfrozen by\b", when) and (
                    re.search(r"presence|you curse|fully (?:armour )?break|block|hit you|critical|on hit|stun you", when)
                    or c.WHEN_YOU_AILMENT.search(when)
                    # (COV48, train 102's b9c1c49d) Frightening Shield's Debilitate rides Bastion of Light's taps
                    or ("shield is raised" in when and var in ("conditionEnemyBlinded", "conditionEnemyDebilitated"))
                    or (not when.strip() and c.WARCRIES_SUBJECT.search(rolled))):
                return condition
        elif var.endswith("Exposure") and (m := c.EXPOSURE_SOURCE.search(rolled)) and                 m.group(2).lower() in ("elemental", "", var[len("conditionEnemy"):-len("Exposure")].lower()):
            # (COV29: plain "Exposure" is all three elements, as conditions._exposure_uptime reads it since MECH69)
            return condition
        elif var == "buffArcaneSurge" and c.ARCANE_SURGE_SOURCE.search(rolled) and not c.ARCANE_SURGE_NOT_SOURCE.search(rolled):
            return condition
        elif var == "buffOnslaught" and (m := c.ONSLAUGHT_SOURCE.search(rolled)) and \
                not c.ONSLAUGHT_NOT_SOURCE.search(rolled) and (
                    not m.group(2).strip() or re.search(r"\bkill|culling strike|minion|during effect|maximum frenzy|"
                                                        r"\bhit\b|critical", m.group(2), re.I)
                    or c.FLASK_EVENT.search(m.group(2)) or any(p.search(m.group(2)) for p in c.EVENT_WORDS.values())) and \
                not re.search(r"vaal skill", m.group(2), re.I) and not re.match(r"with an? .+ socketed", rolled, re.I):
            # (COV17: conditions._onslaught_source rates these from the build's events; a Vaal skill's line it rates 0,
            # a wording it has no event for gets its flat default (LOW), and a "With a ... Jewel Socketed" line's
            # condition it doesn't check, so those stay open here. COV38: a Warcry, Totem, Mark or Flask event too, which
            # _named_event rates from the build's own skills and flasks (event_rates, MECH48), 0 without one)
            return condition
        elif var == "conditionEnemyArmourBroken" and c.ARMOUR_BREAK_LINE.search(rolled) and                not c.ARMOUR_BREAK_NOT_SOURCE.search(rolled):
            return condition
    return None


def stat_entries() -> dict[str, list[tuple[str, str, frozenset | None]]]:
    """Gem stats a ledger entry covers: `stats` on a model entry (fed to the model), or a
    `gem_stat` entry (`no effect on what's calculated` with an empty replace). An entry's `skill` (a name or a list)
    limits it to those skills (a `model` entry with `stats`, ledger/entries/gem-stats-energy.yaml: the skills whose stat the model reads)."""
    covered: dict[str, list[tuple[str, str, frozenset | None]]] = {}
    for entry in entries():
        skills = entry.raw.get("skill")
        skills = frozenset([skills] if isinstance(skills, str) else skills) if skills else None
        for stat in entry.raw.get("stats") or ():
            covered.setdefault(stat, []).append(("model" if entry.raw.get("model") else "ledger", entry.id, skills))
    return covered


def stat_covered(covered: dict, stat: str, skill: str) -> tuple[str, str] | None:
    """(status, entry id) of the entry in `stat_entries()` covering this skill's stat, if any."""
    for status, entry, skills in covered.get(stat, ()):
        if skills is None or skill in skills:
            return status, entry
    return None


def gem_stat_pattern_entry(stat: str, skill: str | None = None):
    for entry in entries():
        if (entry.kind == "gem_stat" and entry.pattern is not None and entry.pattern.search(stat)
                and (skill is None or not entry.raw.get("skill") or skill in (
                    [entry.raw["skill"]] if isinstance(entry.raw["skill"], str) else entry.raw["skill"]))):
            return entry
    return None



# Item-pool mods (PoB "Item": the essence-style mods) that no league character can get, each settled by research with
# its evidence (COV40; memory poe1-leftovers.md: check that a PoE2 source carries a line before counting it as work).
# Not a rule for every "...Essence1" mod: a mod id is listed only after research (Way of the Stonefist's HandWraps twins
# of Essence mods, e.g., have no EssenceMods row of their own yet are obtainable, RESEARCH30 R30-3).
NOT_OBTAINABLE = {
    # (COV49, the manager 16:18: RESEARCH37 R37-1, MEDIUM-HIGH, research/orb-of-sacrifice-blank-upgrades.md) the Orb of
    # Sacrifice "Corrupted Upgrade" mods with no item-class weight: the orb only upgrades a Corrupted Enchantment an
    # item already has (patch 0.5.4; latest 0.5.4b, --history "Orb of Sacrifice"), and no base corruption enchant
    # carries these stats (checked there: every game 26650308 Mods row with each stat id; every table's mod columns,
    # COV48's carrier.py; poe2db's unique lists): recheck at 1.0 (a new base enchant would make them live)
    **{mod: (f"an Orb of Sacrifice upgrade of no existing Corrupted Enchantment ({what}; RESEARCH37 R37-1, MEDIUM-HIGH; "
             "checked there: every game 26650308 Mods row with its stat, every table's mod columns, poe2db's unique "
             "lists; patch 0.5.4 / 0.5.4b: the orb upgrades an enchantment the item already has; recheck at 1.0)")
       for mod, what in (("CorruptionUpgradeAdditionalChainChance", "additional Chain chance"),
                         ("CorruptionUpgradeAdditionalFissureChance", "additional Fissure chance"),
                         ("CorruptionUpgradeDeflectDamageTakenRecoupedAsLife", "Deflected-hit damage Recouped as Life"))},
    # (COV51, the manager 23:25 / 00:40 from MECHC48) Ab Aeterno's old downside: patch 0.5.0 (the newest entry, --history
    # "Ab Aeterno": 0.4.0 changed only its defences) "The Ab Aeterno Unique boots no longer grant 10% less Movement and
    # Skill Speed per Dodge Roll in the past 20 seconds ... Existing versions ... will not receive any other changes";
    # PoB's Uniques/boots.lua 268 carries the line on {variant:1,2} (Pre 0.4.0 / Pre 0.5.0) only, so its unique record
    # is already unreachable (the legacy-variant rule); this is the same line's Exclusive mod row. (checked: the stat
    # unique_movement_speed_and_skill_speed_-%_final_per_number_of_times_dodge_rolled_in_past_20_seconds over every game
    # 26650308 table with a stat column, Mods / PassiveSkills / GrantedEffectStatSets / SoulCoreStats included: this one
    # Mods row, GenerationType 3; no other PoB-PoE2 unique holds the line)
    "UniqueSpeedPerDodgeRoll20Seconds1": (
        "Ab Aeterno's pre-0.5.0 downside (patch 0.5.0: \"no longer grant 10% less Movement and Skill Speed per Dodge Roll "
        "in the past 20 seconds\"; PoB's boots.lua gives it to the Pre 0.4.0 / Pre 0.5.0 variants only; its stat is on "
        "this one Mods row, game 26650308): a legacy item only (COV51)"),
    "CrushOnHitChanceEssence1": (
        "\"(15-25)% chance to Crush on Hit\" has no PoE2 Essence behind it (RESEARCH30 R30-1, MEDIUM-HIGH; checked "
        "there: no EssenceMods row (Mod, DisplayMod or OutcomeMods) points at it, spawn weight 0 in game Mods and PoB "
        "ModItem, it sits among PoE1-style Essence leftovers; poe2db Crush / Crushed list no Essence; game 26650308)"),
    # (COV41, RESEARCH30 R30-4, the manager 00:03 18 Nov; research/essence-leftover-mods.md) six more level-63 "of the
    # Essence" suffixes matching PoE1's corrupted-Essence mods. Checked there in game 26650308: no table references them
    # (EssenceMods Mod / DisplayMod / OutcomeMods over all 95 Essences rows incl. the corrupted four, Abyss, Breach and the
    # 13 Alloys; BaseItemTypes.Implicit_Mods; Essences.MonsterMod; ModGrantedSkills), no HandWraps twin, spawn weight 0, no
    # other obtainable Mods row with their stats; poe2db's current Essence page shows none. Omens unchecked (not in the
    # exported tables, UNVERIFIED).
    **{mod: (f"\"{text}\" has no PoE2 Essence behind it (RESEARCH30 R30-4, {conf}; checked there: no game-table "
             "reference (EssenceMods Mod / DisplayMod / OutcomeMods over all 95 Essences rows incl. Perfect, the "
             "corrupted four, Abyss, Breach and the 13 Alloys; BaseItemTypes implicits; Essences.MonsterMod), no "
             f"HandWraps twin, spawn weight 0; poe2db's current Essence page lacks it; a PoE1 corrupted-Essence mod{extra}; "
             "Omens unchecked, UNVERIFIED; game 26650308)")
       for mod, text, conf, extra in (
           ("NearbyEnemiesChilledOnBlockEssence1", "Chill Nearby Enemies when you Block", "MEDIUM-HIGH", ""),
           ("ChanceToRecoverManaOnSkillUseEssence1", "10% chance to Recover 10% of maximum Mana when you use a Skill",
            "MEDIUM-HIGH", ""),
           ("SocketedGemsNonCurseAuraEffectEssence1", "Socketed Non-Curse Aura Gems have 20% increased Aura Effect", "HIGH",
            "; PoE2 item sockets hold augments, not skill gems"),
           ("SocketedAuraGemLevelsEssence1", "+2 to Level of Socketed Aura Gems", "HIGH",
            "; PoE2 item sockets hold augments, not skill gems"),
           ("FireBurstOnHitEssence1", "Cast Level 20 Fire Burst on Hit", "HIGH",
            "; no player Fire Burst skill in ActiveSkills / GrantedEffects"),
           ("SpiritMinionEssence1", "+3 to maximum number of Spectral Spirits", "HIGH",
            "; no Spectral Spirits skill in ActiveSkills / GrantedEffects"))},
    # (COV48, the manager 14:45) Essence display rows: the text an Essence shows for "+N to Strength, Dexterity or
    # Intelligence", never an item's line: the item gets one of the row's OutcomeMods, a single-attribute mod (checked:
    # game 26650308 EssenceMods 87-90 DisplayMod 14308 / 14309 / 14310 / 14312 with OutcomeMods; EssenceDisplayAttributes4
    # (14311) referenced by no exported table's mod column, scratch/cov48/carrier.py; spawn weight 0, unique generation)
    **{mod: (f"an Essence's display text, never an item's line: the item gets one of EssenceMods {row}'s OutcomeMods, "
             "a single-attribute mod (game 26650308; checked: every exported table's mod columns, scratch/cov48/carrier.py)")
       for mod, row in (("EssenceDisplayAttributes1", "87"), ("EssenceDisplayAttributes2", "88"),
                        ("EssenceDisplayAttributes3", "89"), ("EssenceDisplayAttributes5", "90"))},
    "EssenceDisplayAttributes4": ("an Essence display row no Essence uses (checked: game 26650308, no EssenceMods row or "
                                  "other exported table's mod column references it, scratch/cov48/carrier.py; spawn weight 0)"),
    # (COV48, the manager 14:45: the "carrier unverified" unique-only mods) No league carrier today. Checked (COV48's
    # research helper, 20 Nov; scratch/cov48/carrier.py): poe2db's current unique lists (Weapon / Armour / Other /
    # Cultivated), its Fists of Stone page (37), the 31 pages of 1.0's new and changed uniques (poe2db "Deferred"), the
    # patch-notes archive 0.1.0-0.5.5f (no hit is not proof), game 26650308 Mods (spawn weight 0, unique generation; the
    # other mods sharing each stat), PoB-PoE2's unique texts; the search never rolls or offers them (spawn weight 0; on no
    # PoB unique), so no overcount path. Recheck at 1.0 (unrevealed uniques may use some).
    **{mod: f"no league carrier today: {why} ({conf}; checked: poe2db current unique lists incl. Cultivated and Fists of "
            "Stone, the 31 1.0 preview pages, patch notes 0.1.0-0.5.5f, game 26650308 Mods; COV48, 20 Nov; recheck at 1.0)"
       for mod, why, conf in (
           ("FireDamagePercentPerArmourBreakUnique__1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("IncreasedManaLeechRateUnique__1", "on no unique; its stat lives on only as HandWrapsManaLeech1-5", "HIGH"),
           ("MaximumMinionCountUnique__1__", "no Spectres line on any unique (domain 11), a PoE1 leftover", "HIGH"),
           ("MaximumMinionCountUnique__2", "no Spectres line on any unique, a PoE1 leftover", "HIGH"),
           ("ReducedProjectileDamageTakenUniqueAmulet12", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueAttackMinLightningDamage1", "Mind of the Council carries only the maximum line", "HIGH"),
           ("UniqueAttackSpeedPerOvercappedBlock1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueAttackerTakesLightningDamage1", "on no unique, a PoE1 leftover", "HIGH"),
           ("UniqueCanActiveBlockAllDirections1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueChaosDamageMaximumLife1", "Plaguefinger's (1-3)% line is another id (HandWrapsUniqueMutatedVaal...)",
            "HIGH"),
           ("UniqueConsumeFrenzyChargeAdditionalProjectile1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueDamageCannotBypassEnergyShield1", "its stat names Shavronne's Wrappings, which PoE2 doesn't have", "HIGH"),
           ("UniqueDexterityInherentBonusChange1", "no \"Inherent bonus of\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueIntelligenceInherentBonusChange1", "no \"Inherent bonus of\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueStrengthInherentBonusChange1", "no \"Inherent bonus of\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueElementalDamageFromBlockedHits1", "the shields' blocked-hit lines are other mods", "MEDIUM-HIGH"),
           ("UniqueHitsTreatColdResistance1", "no \"Hits are Resisted by\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueHitsTreatFireResistance1", "no \"Hits are Resisted by\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueHitsTreatLightningResistance1", "no \"Hits are Resisted by\" line on any unique", "MEDIUM-HIGH"),
           ("UniqueIgnoreStrengthRequirementsWeapons1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueLocalFreezeOnFullLife1", "only \"Heavy Stuns Enemies that are on Full Life\" exists, another mod",
            "MEDIUM-HIGH"),
           ("UniqueMinionAddedColdDamageMaximumLife1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueMinionLifeTiedToOwner1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueMutatedVaalReflectElementalAilmentsToSelf", "removed in 0.5.0 (\"Shackles of the Wretched can no "
            "longer roll Elemental Ailments other than Freeze you inflict are Reflected to you\"; poe2db's Cultivated "
            "Shackles list lacks it; pre-0.5.0 items may keep it)", "HIGH"),
           ("UniqueNonChannellingAttackLightningDamage1", "Mind of the Council's lines are \"Attacks ...\", other mods",
            "HIGH"),
           ("UniqueNonChannellingAttackManaCost1", "Mind of the Council's \"Attacks cost an additional 6%\" is another id",
            "HIGH"),
           ("UniqueNonChannellingSpellsDoubleManaAndCrit1", "Rathpith Globe's Non-Channelling lines differ", "MEDIUM-HIGH"),
           ("UniquePhysicalDamageTakenUnmetRequirements1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           ("UniqueReflectCurses1", "no \"Curse Reflection\" line on any unique, a PoE1 leftover", "HIGH"),
           ("UniqueTotemReflectFireDamage1", "on no current or 1.0 unique", "MEDIUM-HIGH"),
           # 1.0 preview only (poe2db "Deferred" tab; 1.0 hasn't started on 20 Nov): obtainable once 1.0 is live
           ("ColdDamageIgnitesUnique__1", "only on Blueflame Bracers' 1.0 rework (poe2db Deferred), not the live item",
            "HIGH"),
           ("UniqueMinionsExplodeAsPercentPhysicalOnDeath1", "only on The Watcher in the Dark, a 1.0 unique (poe2db "
            "Deferred)", "HIGH"))},
    # (COV48, RESEARCH36 R36-8b, the manager 13:50 20 Nov; research/infusion-stack-timer-and-frenzy-bleed-unique.md)
    "FrenzyChargeOnHitWhileBleedingUnique__1": (
        "\"Gain a Frenzy Charge on Hit while Bleeding\" is on no PoE2 unique, a PoE1 leftover (RESEARCH36 R36-8b, "
        "MEDIUM-HIGH; checked there: Mods 10032 (unique generation, stat gain_frenzy_charge_on_hit_while_bleeding) among "
        "PoE1 unique leftovers, no client table references it, on no PoB-PoE2 unique (ModCache parses it only in part), "
        "poe2db's current Bleeding / Frenzy_Charges pages list no item with it; the patch-notes archive: no hit, not proof; "
        "game 26650308)"),
}


def _researched_unique_leftovers() -> dict[str, str]:
    """(COV41, RESEARCH30 R30-6 / R30-7, the manager 01:50 18 Nov) unique-only and Harvest Mods ids no league character
    can get, by evidence family (builder/ledger/unreachable_unique_mods.json, built from research/unique-only-mods-by-id.md:
    329 ids; 284 MEDIUM-HIGH "same id and stats in PoE1 (RePoE); on no PoB-PoE2 / poe2db unique; stats used nowhere live
    in PoE2", 15 MEDIUM older-PoE1-stat ids, 22 MEDIUM whose stat has live PoE2 uses elsewhere, 6 Harvest weapon enchants,
    2 GGG-marked UNUSED). The 38 UNVERIFIED ids stay open."""
    data = json.loads(Path(__file__).with_name("unreachable_unique_mods.json").read_text(encoding="utf-8"))
    out = {}
    for mod, (family, *uses) in data["mods"].items():
        f = data["families"][family]
        evidence = f["evidence"].replace("N live", f"{uses[0]} live") if uses else f["evidence"]
        who = f.get("who") or ("RESEARCH32" if family.startswith("R32") else "RESEARCH30")  # (COV42: R32-3's DNT-UNUSED triggers)
        out[mod] = f"{who} {family.rsplit('-', 1)[0] if family.count('-') > 1 else family}, {f['confidence']}: {evidence}"
    return out


for _mod, _why in _researched_unique_leftovers().items():
    NOT_OBTAINABLE.setdefault(_mod, _why)

def legacy_variant_templates(data_dir: Path) -> dict[tuple[str, str], list[str]]:
    """(unique, line template) -> the old variants it is on, for lines every copy of which (on that unique) is tagged
    only with "Pre 0.x" variants in PoB's raw text (uniques.json); COV40, see audit's "Legacy unique variants"."""
    path = data_dir / "uniques.json"
    if not path.exists():
        return {}
    tagged: dict[tuple[str, str], list[list[str] | None]] = {}
    for item in json.loads(path.read_text(encoding="utf-8"))["uniques"]:
        lines = item["raw"].splitlines()
        variants = [line[9:] for line in lines if line.startswith("Variant: ")]
        for raw_line in lines:
            m = re.match(r"^((?:\{[^}]*\})*)(.*)$", raw_line)
            text = m.group(2).strip()
            if not text:
                continue
            tag = re.search(r"\{variant:([\d,]+)\}", m.group(1))
            names = ([variants[int(v) - 1] if 0 < int(v) <= len(variants) else "?" for v in tag.group(1).split(",")]
                     if tag else None)
            tagged.setdefault((item["name"], template(text)), []).append(names)
    return {key: sorted({n for names in seen for n in names}) for key, seen in tagged.items()
            if all(names and all(n.startswith("Pre ") for n in names) for names in seen)}


def audit(data_dir: Path | None = None, league: str | None = None) -> dict:
    from builder.data import data_dir as default_dir

    data_dir = data_dir or default_dir()
    gaps = json.loads((data_dir / "gaps.json").read_text(encoding="utf-8"))
    coverage_path = data_dir / "coverage.json"
    raw = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else None
    reads = calc_reads()
    popularity = unique_popularity(league)
    records: list[dict] = []

    def add(category, source, kind, text, status, entry, reason, weight, group=None, **extra):
        records.append({"category": category, "source": source, "source_kind": kind, "line": text, "status": status,
                        "entry": entry, "reason": reason, "mechanic": extra.pop("mechanic", None) or mechanic(text),
                        "weight": weight, "template": template(text), "group": group or source, **extra})

    def parsed_noop(text: str, line: dict, on_item: bool):
        """A parsed line's no-op status: names nothing reads (noop_status), or a `parsed_unused` entry (COV27); else None."""
        if missing := unread(typed_names(line, reads), reads):
            return noop_status(text, missing, on_item)
        if unused := parsed_unused(text):
            return unused_status(text, unused)
        return None

    # a. lines PoB admits it ignores
    for gap in gaps["passives"]:
        kind = passive_kind(gap)
        status, entry, reason = socket_label_status(gap["line"], kind) or ignored_line_status(gap["line"], True)
        add("passive", gap["node"], kind, gap["line"], status, entry, reason, SOURCE_WEIGHT[kind], node_id=gap["id"],
            ascendancy=gap.get("ascendancy"))
    for gap in gaps["uniques"]:
        status, entry, reason = ignored_line_status(gap["line"], False)
        add("unique", gap["unique"], "unique", gap["line"], status, entry, reason,
            popularity.get(gap["unique"], SOURCE_WEIGHT["unique"]))

    if raw:
        unique_templates = frozenset(template(gap["line"]) for gap in gaps["uniques"])
        # b. affixes, runes, jewel mods
        for affix in raw["affixes"]:
            kind = POOLS.get(affix["pool"], "affix")
            text = affix["line"]
            if not text:
                continue
            if affix["supported"]:
                noop = parsed_noop(text, affix, affix["pool"] in APPLIED_POOLS)
                status, entry, reason = noop or ("pob", None, "")
                category = "affix-noop" if noop else "affix"
            else:
                category = "affix"
                status, entry, reason = not_applied(*ignored_line_status(text, False), affix["pool"],
                                                    bool(affix.get("bonded")), unique_templates, text)
            group = f"{affix['pool']}:{re.sub(r'\d+$', '', affix['id'])}"
            add(category, f"{affix['pool']} {affix['id']}" + (f" ({affix['slot']})" if affix.get("slot") else ""),
                kind, text, status, entry, reason, SOURCE_WEIGHT[kind], group=group)
        # c. gem stats
        covered = stat_entries()
        for skill in raw["skills"]:
            gems = skill.get("gems") or []
            if isinstance(gems, dict):
                gems = list(gems.values())
            if not gems and not skill["id"].endswith(("Player", "PlayerTwo", "PlayerThree")):
                continue  # a monster's skill
            kind = ("support gem" if skill.get("support") else "active gem") if gems else \
                "hidden" if skill.get("hidden") else "granted skill"
            source = f"{skill['name']} ({skill['id']})"
            for set_ in skill.get("sets") or []:
                stats = set_.get("stats") or []
                if isinstance(stats, dict):
                    stats = list(stats.values())
                for stat in stats:
                    name = stat["stat"]
                    names = stat.get("names") or []
                    if found_entry := stat_covered(covered, name, skill["name"]):
                        status, entry = found_entry
                        reason = "fed to a custom model" if status == "model" else "ledger entry"
                    elif found := gem_stat_pattern_entry(name, skill["name"]):
                        status, entry = "ledger", found.id
                        reason = "translated" if found.replace else "no effect on what's calculated"
                    elif not stat.get("mapped"):
                        status, entry, reason = "none", None, "PoB never reads this stat"
                    elif not names and (flag := skill_flag_stats().get(name)):
                        status, entry, reason = "pob", None, f"PoB sets the skill flag {flag!r} from it"
                    elif not names:
                        status, entry, reason = "none", None, "PoB maps the stat to nothing"
                    elif names and all(n in CONFIG_BOX_ONLY for n in names):
                        status, entry, reason = "none", None, CONFIG_BOX_ONLY[names[0]]
                    elif box := gem_stat_box_conditions().get(name):
                        status, entry, reason = "none", None, (f"PoB counts it only under a condition {box} sets, which "
                                                               "the optimizer never ticks (GEM_BOX_CONDITIONS)")
                    elif name in BUFF_MAKING_STATS:
                        status, entry, reason = "pob", None, BUFF_MAKING_STATS[name]
                    elif missing := unread(names, reads):
                        status, entry, reason = "none", None, "mapped, but no calculation reads " + ", ".join(missing)
                    else:
                        status, entry, reason = "pob", None, ""
                    add("gem-stat", source, kind, name, status, entry, reason, SOURCE_WEIGHT[kind],
                        group=skill["id"], mechanic=stat_mechanic(name), stat_set=set_.get("label"), gems=gems)
        # d. passive and unique lines PoB parses into names nothing reads
        for line in raw["passives"]:
            if noop := parsed_noop(line["line"], line, False):
                kind = passive_kind(line)
                add("passive-noop", line["node"], kind, line["line"], *noop, SOURCE_WEIGHT[kind], node_id=line["id"])
        for line in raw["uniques"]:
            if noop := parsed_noop(line["line"], line, True):
                add("unique-noop", line["unique"], "unique", line["line"], *noop,
                    popularity.get(line["unique"], SOURCE_WEIGHT["unique"]))
        # unique lines of variants the item parse didn't select, and Timeless jewels' passives: the same checks
        for line in raw.get("unique_variants", []):
            weight = popularity.get(line["unique"], SOURCE_WEIGHT["unique"])
            if not line["supported"]:
                status, entry, reason = ignored_line_status(line["line"], False)
                add("unique", line["unique"], "unique", line["line"], status, entry, reason, weight, variant=line.get("variants"))
            elif noop := parsed_noop(line["line"], line, True):
                add("unique-noop", line["unique"], "unique", line["line"], *noop, weight)
        for line in raw.get("base_implicits", []):  # base items' implicit lines
            if not line["supported"]:
                status, entry, reason = not_applied(*ignored_line_status(line["line"], False), "implicit")
                add("base-implicit", line["base"], "affix", line["line"], status, entry, reason, SOURCE_WEIGHT["affix"])
            elif noop := parsed_noop(line["line"], line, True):
                add("base-implicit-noop", line["base"], "affix", line["line"], *noop, SOURCE_WEIGHT["affix"])
        for line in raw.get("legion", []):
            kind = "notable" if line.get("notable") or line.get("kind") == "nodes" else "small"
            if no_jewel_faction(line):
                add("timeless-no-jewel", line["node"], kind, line["line"], "unreachable", None,
                    "no 0.5 jewel conquers for this faction (RESEARCH23 #140)", SOURCE_WEIGHT[kind] * 0.5,
                    node_id=line["id"])
            elif not line["supported"]:
                status, entry, reason = not_applied(*ignored_line_status(line["line"], False), "legion")
                add("timeless", line["node"], kind, line["line"], status, entry,
                    reason + " (a Timeless jewel's passive)", SOURCE_WEIGHT[kind] * 0.5)
            elif noop := parsed_noop(line["line"], line, False):
                add("timeless-noop", line["node"], kind, line["line"], *noop, SOURCE_WEIGHT[kind] * 0.5)
        # f. parsed lines whose condition nothing can switch on; g. parsed lines whose parse lost something
        setters = parse_audit.Setters(raw)
        parsed = ([("passive", l, passive_kind(l), SOURCE_WEIGHT[passive_kind(l)], l["node"]) for l in raw["passives"]]
                  + [("unique", l, "unique", popularity.get(l["unique"], 1.0), l["unique"])
                     for l in [*raw["uniques"], *[v for v in raw.get("unique_variants", []) if v["supported"]]]]
                  + [("affix", l, POOLS.get(l["pool"], "affix"), SOURCE_WEIGHT[POOLS.get(l["pool"], "affix")],
                      f"{l['pool']} {l['id']}") for l in raw["affixes"] if l["supported"]]
                  + [("timeless", l, "small", 0.5, l["node"]) for l in raw.get("legion", [])
                     if l["supported"] and not no_jewel_faction(l)])
        for source_type, line, kind, weight, source in parsed:
            mods = line.get("mods") or []
            if blocked := parse_audit.never_on(mods, setters):
                why = "parsed, but its condition can't switch on: " + "; ".join(f"{need} ({detail})" for need, _, detail in blocked)
                # (COV35) a model or the condition resolver may count the line itself (bridge.sourceLines gives it every
                # line, parsed or not), as ignored_line_status / noop_status already ask: "You gain Onslaught for 3
                # seconds on Kill" is parsed under Condition:KilledRecently, which nothing sets, but conditions.
                # onslaught_uptime rates it at the build's kills and sets PoB's Onslaught box itself
                if model := model_entry(line["line"]):
                    status, entry, why = "model", model.id, f"{why}; fed to the {model.raw['model']} model"
                elif model := play_row_entry(line["line"]):
                    status, entry, why = ("model", model.id, f"{why}; read by the {model.raw['model']} play row "
                                          "(0 in the headline: part-time)")
                elif condition := resolver_condition(line["line"]):
                    status, entry, why = ("model", f"condition:{condition.var}",
                                          f"{why}; a source of {condition.label} for the condition resolver")
                elif source_type in ("unique", "affix") and (fixed := wrong(line["line"], line.get("unique"))):
                    # (COV36) a pob_wrong entry replaces the line on items (context.corrected_raw, items.corrected_item),
                    # as noop_status already asks: Indigon's "for each 200 total Mana Spent Recently" lines (PoB's
                    # Multiplier:ManaSpentRecently only by its Configuration box) are COV32's shares at the build's own
                    # Mana spent (uniques-cov32.yaml)
                    status, entry = "ledger", fixed[0].id
                    why += ("; switched off on items, a model reads it (pob_wrong)" if fixed[1] is None
                            else "; replaced on items by a line PoB calculates (pob_wrong)")
                else:
                    status, entry = "none", None
                add("never-on", source, kind, line["line"], status, entry, why, weight, of=source_type,
                    needs=[need for need, _, _ in blocked], config_only=all(st == "config-off" for _, st, _ in blocked))
            for finding in parse_audit.lost(line["line"], mods):
                checked = checked_parse(line["line"], line.get("unique") or line.get("node"))
                add("misparse", source, kind, line["line"], "ledger" if checked else "none", checked and checked.id,
                    f"PoB's parse read by hand and right: {checked.title}" if checked else
                    f"PoB counts it, but its parse looks wrong ({finding['kind']}: {finding['detail']})",
                    weight * (1.5 if finding["inflates"] else 1.0), of=source_type, finding=finding["kind"],
                    inflates=finding["inflates"])
        # PoE1 leftovers among the unique-only mods: out of the open work (POE1_ONLY_GROUPS); GGG-marked unused ones
        # (DNT_UNUSED) first
        # (COV42, R32-3) with the uniques PoB builds in code (Data/Uniques/Special/Generated.lua: Prism of Belief,
        # Megalomaniac, ...) as uniques.json holds them expanded: the source file has them only as Lua strings
        carried = pob_unique_templates() | dumped_unique_templates(data_dir)
        leftovers = poe1_leftover_mods(raw["affixes"], carried)
        for record in records:
            if (record["status"] == "none" and record["source"].startswith("Exclusive ")
                    and placeholder_carried(record["line"], carried)):
                record["status"] = "pob"
                record["reason"] = ("carried by a unique PoB builds with one variant per skill (Prism of Belief, "
                                    "Generated.lua 91-104: \"+(1-3) to Level of all <gem> Skills\", parsed as GemProperty "
                                    "level, ModCache 629); the game's text holds a placeholder for the skill name "
                                    "(RESEARCH32 R32-3, HIGH; was: " + record["reason"] + ")")
                continue
            if record["source"].startswith(("Exclusive ", "Item ")) and (
                    label := cultivated_label(record["source"].split()[1])):
                # (A32-10, COV27: Cultivated outcomes are live, never a "PoE1 leftover"; the no-carrier ones are labelled)
                record["cultivated"] = "no carrier" if label.startswith("no current") else "live"
                mod_id = record["source"].split()[1]
                if (record["status"] == "none" and record["reason"].startswith(NOT_APPLIED_WHY)
                        and mod_id in cultivated_scored_mods()):
                    # (COV45, from MECH90's catalog note) the search's Cultivation trial gives an outcome's lines the
                    # same ledger stand-ins (cultivated.outcome_lines), so a translated outcome line is applied there
                    record["status"] = "ledger"
                    record["reason"] = ("translated in the Cultivated outcome (cultivated.outcome_lines, the gear "
                                        "round's Cultivation trial); " + label)
                    continue
                if record["status"] != "pob":
                    record["reason"] += "; " + label
                continue
            if (record["status"] == "none" and record["source"].startswith("Exclusive ")
                    and DNT_UNUSED.match(record["line"])):
                record["status"] = "unreachable"
                record["reason"] = ("GGG-marked unused (DNT): the game's own text for this unique-only mod starts "
                                    "\"DNT\" and no PoE2 item carries it (DNT_UNUSED's evidence, COV20); "
                                    + DNT_RESEARCHED.get(record["source"].split()[1], "") + "was: " + record["reason"])
                continue
            if record["status"] == "none" and record["source"].startswith("Exclusive "):
                group = leftovers.get(record["source"].split()[1])
                if group:
                    record["status"] = "unreachable"
                    record["reason"] = (f"a PoE1 leftover ({group}): no PoE2 unique carries this mod "
                                        "(POE1_ONLY_GROUPS' evidence, COV19); was: " + record["reason"])
                elif group == "" and (settled := R32_3_OBTAINABLE.get(record["source"].split()[1])):
                    record["reason"] += "; " + settled
                elif group == "":
                    record["reason"] += ("; no PoB-PoE2 unique carries this mod: a PoE1 leftover or PoE2 content PoB "
                                         "lacks (UNVERIFIED)")
    # Legacy unique variants (COV40): PoB's item parse and gap audit load every variant's lines, so a line only an old
    # "Pre 0.x" variant carries (Bones of Ullr's pre-0.3.0 "20% reduced Reservation Efficiency of Skills which create
    # Undead Minions"; patch 0.3.0: "Existing items will have reduced Reservation Efficiency but can be updated with a
    # Divine Orb") ranked as open work, though no new item rolls it: the search offers only what a new league character
    # can get (the user, 13 Nov 2026) and PoB equips a unique's current variant. builder/data's unique_variant_lines
    # already leaves these variants out. Such a line is "unreachable" when every line of that unique with its template
    # is tagged only with "Pre " variants (uniques.json raw; a template any current or untagged line also holds stays).
    legacy = legacy_variant_templates(data_dir)
    for record in records:
        if (record["status"] == "none" and record["source_kind"] == "unique"
                and (record["source"], record["template"]) in legacy):
            record["status"] = "unreachable"
            record["reason"] = (f"a legacy variant's line ({', '.join(legacy[record['source'], record['template']])} "
                                "only): no new item carries it (league-obtainable only; PoB equips the current "
                                "variant); was: " + record["reason"])
    # Mods research found no league carrier for, but couldn't show unobtainable (COV45, R34-6): labelled, status kept
    for record in records:
        if why := NO_KNOWN_CARRIER.get(record["source"].split()[-1]):
            record["no_known_carrier"] = True
            record["reason"] = (record["reason"] + "; " if record["reason"] else "") + why
    # Item mods no league character can get (COV40), each settled by research, by mod id (NOT_OBTAINABLE)
    for record in records:
        # (COV48) a "model" row too: a resolver reading a line no character can carry isn't work either (R36-8b's Frenzy
        # line, which the condition resolver reads as a Frenzy source)
        if (record["status"] in ("none", "model") and record["source"].startswith(("Item ", "Exclusive "))
                and (why := NOT_OBTAINABLE.get(record["source"].split()[1]))):
            record["status"] = "unreachable"
            record["reason"] = f"not league-obtainable: {why}; was: " + record["reason"]
    # Recovery lines no rate reads yet (recovery.EVENT_UNREAD, MECH-C's list; AUDIT27 A27-32, COV20): named as routed
    # undercounts, so no recovery line is dropped silently (the user's every-recovery-source rule)
    for record in records:
        if record["status"] == "none" and (name := unread_recovery(record["line"])):
            record["routed"] = "MECH-C"
            record["undercount"] = True
            record["reason"] += (f"; a recovery source no rate reads yet ({name}, recovery.EVENT_UNREAD): an UNDERCOUNT "
                                 "in the sustain share and the recovery floor, routed to MECH-C")
    # Lines a `parsed_unused` entry records (COV27): routed to its owner as an undercount, as above
    # (an entry with `direction: overcount`, COV28, records a downside nothing counts: an OVERCOUNT, not an undercount)
    unused_owner = {e.id: e.raw.get("owner") for e in entries() if e.kind == "parsed_unused"}
    overcounts = {e.id for e in entries() if e.kind == "parsed_unused" and e.raw.get("direction") == "overcount"}
    for record in records:
        if record["status"] == "none" and record["entry"] in unused_owner:
            record["routed"] = unused_owner[record["entry"]]
            if record["entry"] in overcounts:
                record["overcount"] = True
                if "OVERCOUNT" not in record["reason"]:
                    record["reason"] += f"; an OVERCOUNT (a downside nothing counts), routed to {record['routed']}"
                continue
            record["undercount"] = True
            if "UNDERCOUNT" not in record["reason"]:
                record["reason"] += f"; an UNDERCOUNT, routed to {record['routed']}"
    # e. skills that calculate 0 alone on a plain character (data/pob-*/skill_zero.json, scratch/zero_skills.py)
    for skill in zero_skills(data_dir):
        add("skill-zero", skill["name"], "active gem", skill["name"], skill["status"], skill.get("entry"), skill["reason"],
            SOURCE_WEIGHT["active gem"], mechanic="damage")

    # (COV29; PERF23's item 2) coverage.json's lists come in an order that changes from one PoB process to the next, and
    # a ranked row's example is the first record met, as are the catalog's lines: sorted, two regenerations give the
    # same text. The totals never depended on it (PERF23: summarize equal across two dumps).
    records.sort(key=record_order)
    return {"data_dir": str(data_dir), "bridge_part": bool(raw), "records": records,
            "summary": summarize(records), "ranking": rank(records)}


def record_order(record: dict) -> tuple[str, ...]:
    """A fixed order for the audit's records, independent of the order PoB's dump lists them in."""
    return tuple(str(record.get(k, "")) for k in ("category", "template", "line", "source", "status", "entry", "reason"))


# Skills our own models give damage, or an assumption gives a trigger rate (builder/search/models.py, assumptions.py)
MODELED_SKILLS = {"Iron Ward": "iron-ward-store", "Elemental Expression": "elemental-expression-trigger",
                  "Transmutation Explosion": "assumption:remnants",
                  # (COV45, train 94: MECHB42's bae8fb78: engine/bridge.lua adds the Manifestation minion and its attack,
                  # models.verisium_model the number alive from the main Attack's hits and the Runic Ward paid)
                  "Verisium Manifestations": "model:verisium",
                  # (COV46, MECHB44 23:28) models.tempest_caller_model (MECHB37)
                  "Elemental Storm": "model-read-tempest-caller-elemental-storm",
                  # (COV46, train 97: MECHB44's bridge raven minion and models.mist_raven_model; The Auspex, level 15 by
                  # R35-7 in granted_rolls.KNOWN_LEVELS)
                  "Mist Raven": "model-mechb44-mist-raven",
                  # (COV47) MECHB44's runic_tempering_model (train 98): the backlash, your Thorns hit per plated hit
                  "Runic Tempering": "model-mechb44-runic-tempering",
                  # (COV47) GEM26's Repulsion Wave (train 100): the detonation's Physical wave; the curse itself deals none
                  "Repulsion": "model-gem26-repulsion-wave",
                  # (COV48) The audit's sweep scores a skill's first stat set; these deal their damage in another set or
                  # skill of the same gem, which the search scores and a model on main rates (game 26650308
                  # GrantedEffects / GrantedEffectStatSets, per set: scratch/cov47/zero_sets2.py; PoB Gems.lua the same):
                  # FrostflameNovaPlayer, FrozenLocusPlayer, ElementalSunderingPlayer carry base_deal_no_damage and their
                  # FrostflameNovaColdHit / FrozenLocusExplode / ElementalSundering{Cold,Fire,Lightning} sets deal it.
                  # (COV53, after SEARCH35's fc0f9efa, train 121: the bridge now skips a base_deal_no_damage SET in Full
                  # DPS and the chooser never picks one; the sweep's reading is unchanged, as it reads any of
                  # DAMAGE_STATS and the rule only wraps Full DPS's skill count, so these reasons still hold.)
                  "Frostflame Nova": "model-gem8-frostflame-volatile-ice",
                  "Frozen Locus": "model-gem20-frozen-locus-crystal-breaks",
                  "Elemental Sundering": "model-gem17-elemental-sundering-ailments",
                  # PoB fires it once per Cooldown whatever you take, or never beside a main skill; valako_model rates it
                  "Valako's Charge": "model-read-valakos-charge-lightning-taken",
                  # PoB has no calc for its recorded-damage stat; inevitable_agony_model gives 25% of the main hit DPS
                  "Inevitable Agony": "model-gem13-inevitable-agony-read",
                  # totems: the summon (no damage of its own, though its set has no marker) and its attack a second
                  # granted effect of the gem (ShockwaveTotemQuakePlayer, DarkEffigyProjectilePlayer: Gems.lua
                  # additionalGrantedEffectId1), which PoB scores and totem_count_model scales by the totems up
                  "Shockwave Totem": "model:totem_count",
                  "Dark Effigy": "model-mechb43-dark-effigy-volley",
                  # bridge.lua installSpiritVessel: its companion uses copies of the socketed Bear / Werewolf / Wyvern
                  # skills (0 with none socketed, as the sweep has it); the search fills its group (gems.granted_meta_round)
                  "Spirit Vessel": "bridge:spirit-vessel"}
# Skills whose 0 in PoB is right: they deal no damage of their own (COV46, from MECHB44 23:28). (checked: game 26650308
# GrantedEffectStatSets: SacrificePlayer and EternalMarchPlayer carry base_deal_no_damage; SummonSkeletalClericsPlayer
# carries display_statset_no_hit_damage and its minion's skills are HealSkeletonClericMinion /
# ResurrectSkeletonClericMinion; PoB's other.lua / act_int.lua the same stats, Minions.lua Skeletal Cleric: Heal,
# Resurrect, DoLiterallyNothing.) What they do for other skills (a sacrificed or revived minion) is the models' business.
NO_OWN_DAMAGE_SKILLS = {
    "Eternal March": "its stat set carries base_deal_no_damage (game GrantedEffectStatSets EternalMarchPlayer; PoB other.lua)",
    "Sacrifice": "its stat set carries base_deal_no_damage (game GrantedEffectStatSets SacrificePlayer; PoB act_int.lua)",
    "Skeletal Cleric": ("its summon's stat set shows no hit damage (display_statset_no_hit_damage) and its minion only "
                        "heals and resurrects (HealSkeletonClericMinion, ResurrectSkeletonClericMinion; PoB Minions.lua)"),
}
# (COV47) Every stat set of these skills' player effect carries base_deal_no_damage or display_statset_no_hit_damage
# (checked per stat set, not merged: game 26650308 GrantedEffects -> GrantedEffectStatSets, scratch/cov47/zero_sets2.py;
# second source: the same stat in PoB's Data/Skills entry of each; the game's ActiveSkills descriptions agree: curses,
# buffs, a meta trigger, totems whose bolts are their own skills). Not here, though they carry it on one set: Elemental
# Sundering, Frostflame Nova, Frozen Locus (their other sets deal damage); Fortifying Cry and Infernal Cry (no hit damage
# of their own, but the Shield Waves / Combust they cause aren't all counted yet: left open so the row isn't hidden).
_NO_DAMAGE_SET = "every stat set of its player effect carries {} (game GrantedEffectStatSets {}; PoB Data/Skills the same)"
NO_OWN_DAMAGE_SKILLS.update({
    "Despair": "a curse: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "DespairPlayer"),
    "Elemental Weakness": "a curse: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "ElementalWeaknessPlayer"),
    "Enfeeble": "a curse: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "EnfeeblePlayer"),
    "Temporal Chains": "a curse: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "TemporalChainsPlayer"),
    "Vulnerability": "a curse: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "VulnerabilityPlayer"),
    "Artillery Ballista": ("the totem: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "ArtilleryBallistaPlayer")
                           + "; its bolts are their own skill, which PoB scores and models.totem_count_model scales"),
    "Siege Ballista": ("the totem: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "SiegeBallistaPlayer")
                       + "; its bolts are their own skill, which PoB scores and models.totem_count_model scales"),
    "Cast on Minion Death": ("a meta trigger: " + _NO_DAMAGE_SET.format("base_deal_no_damage", "MetaCastOnMinionDeathPlayer")
                             + "; the spells it triggers are their own skills"),
    "Grim Feast": _NO_DAMAGE_SET.format("base_deal_no_damage", "GrimFeastPlayer"),
    "Tame Beast": _NO_DAMAGE_SET.format("base_deal_no_damage", "TameBeastPlayer"),
    "Bone Offering": _NO_DAMAGE_SET.format("display_statset_no_hit_damage", "BoneOfferingPlayer"),
    "Pain Offering": _NO_DAMAGE_SET.format("display_statset_no_hit_damage", "PainOfferingPlayer"),
    "Soul Offering": _NO_DAMAGE_SET.format("display_statset_no_hit_damage", "SoulOfferingPlayer"),
    "Navira, the Last Mirage": ("restorative spells on Command: "
                                + _NO_DAMAGE_SET.format("display_statset_no_hit_damage", "SummonWaterDjinnPlayer")),
})
DAMAGE_STATS = ("CombinedDPS", "TotalDPS", "TotalDotDPS", "FullDPS", "AverageDamage")


def zero_skills(data_dir: Path) -> list[dict]:
    """Audit item (e): active skills that calculate no damage alone on a plain level-90 character (bridge auditSkills, in
    coverage.json). Each attack is calculated with a plain weapon of the first kind it allows, and a minion skill by its
    minion's damage; buffs, auras, curses and meta gems deal none of their own and aren't listed. An attack with no weapon
    kind of its own (it uses whatever you hold) isn't checked."""
    path = data_dir / "coverage.json"
    if not path.exists():
        return []
    sweep = json.loads(path.read_text(encoding="utf-8")).get("skill_sweep") or []
    # (COV32) one row per active skill id (bridge auditSkills): a name two ids share (Lightning Bolt, Mace Strike, Spear
    # Stab, Unleash) is told apart by its id
    shared = Counter(skill["name"] for skill in sweep)
    found = []
    for skill in sweep:
        name = skill["name"] if shared[skill["name"]] < 2 or not skill.get("skillId")             else f"{skill['name']} ({skill['skillId']})"
        if skill.get("error"):
            found.append({"name": name, "skillId": skill.get("skillId"), "status": "none",
                          "reason": "PoB failed to calculate it: " + skill["error"][:120]})
            continue
        stats, types = skill.get("stats") or {}, set(skill.get("types") or [])
        if any((stats.get(k) or 0) > 0 for k in (*DAMAGE_STATS, "MinionDPS")):
            continue
        minion = bool(types & {"Minion", "CreatesMinion"})
        if ("Attack" in types and not stats.get("weapon") and not minion) \
                or (not minion and types & {"Aura", "Buff", "Persistent", "HasReservation", "Meta"}) \
                or not (minion or types & {"Damage", "DamageOverTime", "Projectile", "AreaSpell", "Nova", "SummonsTotem",
                                           "SummonsAttackTotem", "Attack"}):
            continue
        if why := NO_OWN_DAMAGE_SKILLS.get(skill["name"]):
            found.append({"name": name, "skillId": skill.get("skillId"), "status": "pob", "entry": None,
                          "reason": "0 in PoB is right: no damage of its own: " + why})
        elif entry := MODELED_SKILLS.get(skill["name"]):
            found.append({"name": name, "skillId": skill.get("skillId"), "status": "model", "entry": entry,
                          "reason": "0 in PoB alone; a custom model or assumption gives it damage"})
        else:
            found.append({"name": name, "skillId": skill.get("skillId"), "status": "none",
                          "reason": "a damaging skill that calculates 0 on its own in PoB (types: %s)" % ", ".join(sorted(types)[:8])})
    return found


def summarize(records: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for record in records:
        row = summary.setdefault(record["category"], {s: 0 for s in STATUSES})
        row[record["status"]] += 1
        if record["status"] == "ledger" and record["reason"].startswith("no effect"):
            row["ledger_no_effect"] = row.get("ledger_no_effect", 0) + 1
        if record["status"] == "none":
            by_kind = row.setdefault("none_by_source_kind", Counter())
            by_kind[record["source_kind"]] += 1
            by_mech = row.setdefault("none_by_mechanic", Counter())
            by_mech[record["mechanic"]] += 1
    for row in summary.values():
        for key in ("none_by_source_kind", "none_by_mechanic"):
            if key in row:
                row[key] = dict(row[key].most_common())
    return summary


def rank(records: list[dict], top: int | None = None) -> list[dict]:
    """What isn't counted, grouped by line template and ranked by impact."""
    groups: dict[tuple, dict] = {}
    for record in records:
        if record["status"] != "none":
            continue
        key = (record["category"].split("-")[0] if record["category"] != "gem-stat" else "gem-stat", record["template"])
        group = groups.setdefault(key, {"category": record["category"], "template": record["template"],
                                        "mechanic": record["mechanic"], "sources": {}, "reason": record["reason"],
                                        "example": record["line"]})
        group["sources"][record["group"]] = max(group["sources"].get(record["group"], 0), record["weight"])
        group.setdefault("source_kinds", Counter())[record["source_kind"]] += 1
        group.setdefault("names", [])
        if len(group["names"]) < 6 and record["source"] not in group["names"]:
            group["names"].append(record["source"])
    ranked = []
    for group in groups.values():
        weights = sorted(group["sources"].values(), reverse=True)
        # the strongest source counts in full, each further source a little less
        spread = sum(w / (1 + 0.5 * i) for i, w in enumerate(weights))
        score = MECHANIC_WEIGHT[group["mechanic"]] * spread
        ranked.append({"score": round(score, 2), "category": group["category"], "mechanic": group["mechanic"],
                       "template": group["template"], "sources": len(weights),
                       "source_kinds": dict(group["source_kinds"]), "examples": group["names"],
                       "reason": group["reason"], "example": group["example"]})
    ranked.sort(key=lambda g: -g["score"])
    return ranked[:top] if top else ranked


def print_summary(report: dict, top: int = 40) -> None:
    print(f"coverage audit of {report['data_dir']}" + ("" if report["bridge_part"] else
          " (no coverage.json: run with --refresh for affixes, gem stats and no-ops)"))
    print(f"{'category':14} {'pob':>7} {'ledger':>7} {'(no eff.)':>9} {'model':>7} {'none':>7} {'unreach.':>8}")
    totals = Counter()
    for category, row in report["summary"].items():
        print(f"{category:14} {row['pob']:7} {row['ledger']:7} {row.get('ledger_no_effect', 0):9} {row['model']:7} "
              f"{row['none']:7} {row['unreachable']:8}")
        totals.update({s: row[s] for s in STATUSES})
        totals["no_effect"] += row.get("ledger_no_effect", 0)
    print(f"{'total':14} {totals['pob']:7} {totals['ledger']:7} {totals['no_effect']:9} {totals['model']:7} "
          f"{totals['none']:7} {totals['unreachable']:8}")
    print("(ledger includes entries recording no effect on what's calculated; model includes the condition resolver)")
    for category, row in report["summary"].items():
        if row.get("none_by_source_kind"):
            print(f"  {category} not counted by source: {row['none_by_source_kind']}")
            print(f"  {category} not counted by mechanic: {row['none_by_mechanic']}")
    print(f"\ntop {top} not counted, by impact:")
    for i, group in enumerate(report["ranking"][:top], 1):
        print(f"{i:3}. [{group['score']:6.1f}] {group['category']:12} {group['mechanic']:8} {group['template'][:90]}"
              f"  ({group['sources']} sources: {', '.join(group['examples'][:3])})")


AREAS = (
    ("A. Keystones and ascendancy passives PoB ignores",
     lambda g: g["category"] == "passive" and bool(set(g["source_kinds"]) & {"keystone", "ascendancy"})),
    ("B. Notables and small passives PoB ignores", lambda g: g["category"] == "passive"),
    ("C. Uniques PoB ignores (popular first, by poe.ninja listings)", lambda g: g["category"] == "unique"),
    ("D. Gem stats PoB never reads", lambda g: g["category"] == "gem-stat"),
    ("E. Affixes, runes and jewels PoB ignores", lambda g: g["category"] == "affix"),
    ("F. Lines whose condition never switches on (item f)", lambda g: g["category"] == "never-on"),
    ("G. Lines PoB parses but lost part of (item g; check each by hand)", lambda g: g["category"] == "misparse"),
    ("H. Parsed into nothing a calculation reads", lambda g: g["category"].endswith("-noop")),
    ("I. Damaging skills that calculate 0 alone (item e)", lambda g: g["category"] == "skill-zero"),
    ("J. Timeless jewel passives", lambda g: g["category"].startswith("timeless")),
)


def areas_markdown(report: dict, top: int = 25) -> str:
    """The ranked open list split by area (each can go to a different worker), flavour left out."""
    used, out = set(), ["# Coverage audit: what nothing counts, by area", "",
                        "Score = mechanic weight x sources, strongest first. "
                        "From `python -m builder.ledger.coverage --areas FILE`.", ""]
    for title, test in AREAS:
        rows = [(i, g) for i, g in enumerate(report["ranking"]) if i not in used and g["mechanic"] != "flavour" and test(g)]
        used.update(i for i, _ in rows)
        out.append(f"## {title} ({len(rows)} open)")
        out += [f"- [{g['score']:.1f}] {g['mechanic']}: `{g['template'][:110]}` ({g['sources']} sources: "
                f"{', '.join(g['examples'][:3])})" for _, g in rows[:top]]
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    from builder.data import data_dir

    if "--refresh" in argv:
        from builder.data import dump_coverage

        dump_coverage()
    report = audit()
    out = data_dir() / "coverage-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    print_summary(report, top=int(argv[argv.index("--top") + 1]) if "--top" in argv else 40)
    print(f"\nwrote {out}")
    if "--areas" in argv:
        areas = Path(argv[argv.index("--areas") + 1])
        areas.write_text(areas_markdown(report), encoding="utf-8")
        print(f"wrote {areas}")


if __name__ == "__main__":
    main(sys.argv[1:])


# The blind spots of one build (the result's "not modeled" list) --------------------------------

@lru_cache(maxsize=2)
def _build_index(data_dir: str) -> dict:
    """From the bridge part of the audit: the templates of lines PoB parses into names nothing
    reads, and each gem's stats PoB never reads (damage, defence, recovery or speed ones)."""
    path = Path(data_dir) / "coverage.json"
    if not path.exists():
        return {"noops": {}, "gems": {}, "mods": {}, "setters": None}
    raw = json.loads(path.read_text(encoding="utf-8"))
    reads = calc_reads()
    noops: dict[str, list[str]] = {}
    mods: dict[str, list[dict]] = {}  # template -> a parse of it, for the build's own condition checks
    for line in [*raw["passives"], *raw["uniques"], *raw["affixes"], *raw.get("unique_variants", []), *raw.get("legion", [])]:
        if line.get("supported", True) and (missing := unread(typed_names(line, reads), reads)):
            noops.setdefault(template(line["line"]), missing)
        if line.get("supported", True) and line.get("mods"):
            mods.setdefault(template(line["line"]), line["mods"])
    covered = stat_entries()
    gems: dict[str, list[tuple[str, str]]] = {}
    for skill in raw["skills"]:
        names = skill.get("gems") or []
        names = list(names.values()) if isinstance(names, dict) else names
        if not names and skill["id"].endswith(("Player", "PlayerTwo", "PlayerThree")):
            names = [skill["name"]]  # (COV56) an item-granted skill (Malice) is listed by its own name in a build
        for set_ in skill.get("sets") or []:
            stats = set_.get("stats") or []
            for stat in list(stats.values()) if isinstance(stats, dict) else stats:
                name = stat["stat"]
                if stat_mechanic(name) in ("flavour", "unclassified") or stat_covered(covered, name, skill["name"]) or gem_stat_pattern_entry(name, skill["name"]):
                    continue
                boxed = bool(stat.get("names") and all(n in CONFIG_BOX_ONLY for n in stat["names"])) or \
                    name in gem_stat_box_conditions()  # (COV56: the audit's own two rules)
                if stat.get("mapped") and stat.get("names") and not unread(stat["names"], reads) and not boxed:
                    continue
                reason = "PoB never reads this gem stat" if not stat.get("mapped") else \
                    "PoB counts this gem stat only behind a Configuration box nothing ticks" if boxed else \
                    "PoB maps this gem stat to nothing a calculation reads"
                for gem in names:
                    entry = gems.setdefault(gem, [])
                    if (name, reason) not in entry:
                        entry.append((name, reason))
    gems = {gem: sorted(stats) for gem, stats in sorted(gems.items())}  # (COV29: the dump's order varies; PERF23)
    return {"noops": noops, "gems": gems, "mods": mods, "setters": parse_audit.Setters(raw)}


def _off_in_build(mods: list[dict], setters, own: set[str], config_on: set[str]) -> list[str]:
    """For a line of a build: its needs this build doesn't meet, when every modifier of the line has one."""
    off_all, checked = [], 0
    for mod in mods:
        needs = [n for n in parse_audit.needs([mod]) if "{" not in n]
        if not needs:
            return []  # a part of the line counts unconditionally
        checked += 1
        off = []
        for need in needs:
            status, detail = setters.status(need)
            if status == "calc":
                continue  # PoB decides it from the build itself
            if any(alt in own for alt in setters.alternatives(need)):
                continue  # a line of this build sets it
            if any(o in config_on or setters.options[o]["default"] for o in setters.options_for(need)):
                continue  # a Configuration option that's on for this build
            off.append(f"{need.split(':')[-1]} is on (" + ("nothing in this build sets it" if status in ("line", "never")
                                                           else detail.replace("which the optimizer turns on",
                                                                               "off for this build")) + ")")
        if not off:
            return []
        off_all += [o for o in off if o not in off_all]
    return off_all if checked else []


def build_blind_spots(source_lines: list[dict], gem_names: list[str], data_dir: Path | None = None,
                      config_on: set[str] | None = None, stats: dict | None = None) -> list[dict]:
    """What a build has that nothing counts beyond the lines PoB's parser ignores (bridge
    `unsupportedInBuild`): lines PoB parses into modifiers no calculation reads; lines whose condition is off in this
    build (no line of it sets the condition, or only a Configuration option the optimizer has off: Guided Hand's
    "Ancestrally Boosted Attacks" without a source of Ancestral Boost); and stats of its gems PoB never reads. Each comes
    with the reason, for the result's "not modeled" list. config_on: the Configuration options on for this build (the
    condition resolver's)."""
    from builder.data import data_dir as default_dir

    index = _build_index(str(data_dir or default_dir()))
    found = []
    build_mods = [{"mods": index["mods"].get(template(line["text"])) or []} for line in source_lines]
    own = parse_audit.line_setters(build_mods)
    setters = index.get("setters")
    for line in source_lines:
        if not line["source"].startswith(("passive:", "item:")):
            continue
        where = line["source"].replace(":", ": ", 1)
        if missing := index["noops"].get(template(line["text"])):
            found.append({"source": where, "line": line["text"], "kind": "no-op",
                          "reason": "PoB parses this line, but no calculation reads " + ", ".join(missing)})
        elif (need := parse_audit.gated(index["mods"].get(template(line["text"])) or [], stats or {})) and stats is not None:
            found.append({"source": where, "line": line["text"], "kind": "condition off",
                          "reason": f"PoB counts this line only while {need}; this build has none"})
        elif setters is not None and (off := _off_in_build(index["mods"].get(template(line["text"])) or [], setters, own,
                                                             config_on or set())):
            found.append({"source": where, "line": line["text"], "kind": "condition off",
                          "reason": "PoB counts this line only while " + "; ".join(off)})
    for gem in dict.fromkeys(gem_names):
        for stat, reason in index["gems"].get(gem, []):
            found.append({"source": f"gem: {gem}", "line": stat, "kind": "gem stat", "reason": reason})
    return found
