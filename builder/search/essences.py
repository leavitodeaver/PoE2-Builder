"""Perfect Essences on rares (SEARCHB1, G1; the manager's 09:16 approval, 21 Nov).

A Perfect Essence "remove[s] a random modifier and augments a Rare item with a new guaranteed modifier" (the 0.3.0
patch notes, Essence Rework: "Perfect Essences and Essences obtained through Corruption remove a random modifier and
augments a Rare item with a new guaranteed modifier"). Its modifiers are essence-only: every Essence* row in PoB's mod
data has spawn weight 0 ("default" 0), so rares.AffixPools, which keeps only rows a base can roll, never offered one
(checked: the 190 Item-pool rows whose id holds "Essence" in data/pob-*/mods.json all have weightVal [0]; no module of
builder/search made an essence item before this one: `grep -rn Essence` over rares.py, gear.py, alloys.py; second
source: the game's EssenceMods table, build 26650308, 26 rows with Essences.Perfect true, the same mod ids as below).

Offered as the Alloys are, by the same trial (gear.Gear.augment_trial) and the manager's Alloy ruling (18 Nov 20:25):
DPS at the EXPECTED value over which modifier is lost, the defence floors at the WORST outcome (the item equipped is
the worst outcome; its mean / worst DPS factor rides on objective.alloy_scale). The rules the Alloys keep as the
lower reading are kept here too: one augment (Alloy or Essence) per item; none beside an affix of its mod group; it
goes on a rare with a free slot of its own type (prefix / suffix), so whichever modifier is lost, it fits.

Classes: PoB's Data/Essence.lua (each Perfect Essence's mod per item class; its class names: "Warstaff" is PoB's
Staff with subType Warstaff). The game rows agree on the mod ids (two rows, 1H and 2H, where the table has two).
Values: PoB's mod rows. Perfect Essence of Battle: +2 to Level of all Attack Skills on one-hand martial weapons and
bows, +3 on two-hand ones and crossbows (PoB EssenceAttackSkillLevel1H1 / 2H1; the 0.5.0 notes: "+3 to Level of all
Attack Skills on Two Hand Melee Weapons or Crossbows (previously +5), and +2 ... on One Handed Melee Weapons or Bows
(previously +3)").

Left out (said here, not silently): Perfect Essence of the Infinite (its row has no Mod: OutcomeMods, one of three
attributes at random, and PoB has no EssenceDisplayAttributes5 row); the corrupted essences (Hysteria, Delirium, Horror,
Insanity: Hysteria's mods are natural affixes; the other three need their own models: a granted passive, rune effect,
two enchantments), and the non-Perfect essences (they add a natural affix: a crafting route, not a new modifier).

Price: the essence's poe.ninja exchange price on the run's league (Forbidden Rites: memory price-league) plus the rare
the trial keeps; no price, no offer. Two samples (scratch/SEARCHB1: the cache of 24 Oct and a fetch of 21 Nov) put every
Perfect Essence under 0.21 div (Battle 0.044 / 0.175, Sorcery 0.007 / 0.042): they differ up to x70 on thin volume, but
neither moves a build against a 100-200 div budget. OFF by default (POE2_SEARCH_ESSENCES=1 turns it on) until a set
measures it higher (the manager's 19 Nov 02:50 rule for search features).
"""
from __future__ import annotations

import os

ONE_HAND = ("One Hand Sword", "One Hand Axe", "One Hand Mace", "Spear", "Dagger", "Bow", "Flail")
TWO_HAND = ("Two Hand Sword", "Two Hand Axe", "Two Hand Mace", "Warstaff", "Crossbow", "Talisman")
MARTIAL = ONE_HAND + TWO_HAND

# Mod id -> (Perfect Essence, item classes): PoB Data/Essence.lua's Perfect entries (Infinite left out: see above)
ESSENCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "EssenceIncreasedLifePercent1": ("Perfect Essence of the Body", ("Body Armour",)),
    "EssenceIncreasedManaPercent1": ("Perfect Essence of the Mind", ("Ring",)),
    "EssenceGlobalDefences1": ("Perfect Essence of Enhancement", ("Amulet",)),
    "EssenceDamageasExtraPhysical1": ("Perfect Essence of Abrasion", ONE_HAND),
    "EssenceDamageasExtraPhysical2H": ("Perfect Essence of Abrasion", TWO_HAND),
    "EssenceDamageasExtraFire1": ("Perfect Essence of Flames", ONE_HAND),
    "EssenceDamageasExtraFire2H": ("Perfect Essence of Flames", TWO_HAND),
    "EssenceDamageasExtraCold1": ("Perfect Essence of Ice", ONE_HAND),
    "EssenceDamageasExtraCold2H": ("Perfect Essence of Ice", TWO_HAND),
    "EssenceDamageasExtraLightning1": ("Perfect Essence of Electricity", ONE_HAND),
    "EssenceDamageasExtraLightning2H": ("Perfect Essence of Electricity", TWO_HAND),
    "EssencePhysicalDamageTakenAsChaos1": ("Perfect Essence of Ruin", ("Body Armour",)),
    "EssenceAttackSkillLevel1H1": ("Perfect Essence of Battle", ONE_HAND),
    "EssenceAttackSkillLevel2H1": ("Perfect Essence of Battle", TWO_HAND),
    "EssenceSpellSkillLevel1H1": ("Perfect Essence of Sorcery", ("Wand",)),
    "EssenceSpellSkillLevel2H1": ("Perfect Essence of Sorcery", ("Staff",)),
    "EssenceOnslaughtonKill1": ("Perfect Essence of Haste", MARTIAL),
    "EssenceReducedCriticalDamageAgainstYou1": ("Perfect Essence of Seeking", ("Body Armour",)),
    "EssenceFireRecoupLife1": ("Perfect Essence of Insulation", ("Belt",)),
    "EssenceColdRecoupLife1": ("Perfect Essence of Thawing", ("Helmet",)),
    "EssenceLightningRecoupLife1": ("Perfect Essence of Grounding", ("Gloves",)),
    "EssenceManaCostReduction": ("Perfect Essence of Alacrity", ("Focus", "Wand")),
    "EssenceManaCostReduction2H": ("Perfect Essence of Alacrity", ("Staff",)),
    "EssenceGoldDropped1": ("Perfect Essence of Opulence", ("Gloves",)),
    "EssenceAuraEffect1": ("Perfect Essence of Command", ("Sceptre",)),
}


# (R43-7, RESEARCH43) the Omens that make a Perfect Essence remove a prefix only / a suffix only: the game's
# BaseItemTypes OmenOnPerfectEssencePrefix = "Omen of Sinistral Crystallisation", ...Suffix = "Omen of Dextral
# Crystallisation" (build 26650308); priced from poe.ninja's exchange (Ritual), no price: not offered
OMENS = {"Prefix": "Omen of Sinistral Crystallisation", "Suffix": "Omen of Dextral Crystallisation"}


def item_class(base: dict) -> str:
    """The base's class as Essence.lua names it: PoB's Staff with subType Warstaff is a "Warstaff"."""
    if base.get("type") == "Staff" and base.get("subType") == "Warstaff":
        return "Warstaff"
    return str(base.get("type") or "")


def rows_for(base: dict, item_level: int, mods: dict[str, dict]) -> list[tuple[str, dict]]:
    """(Perfect Essence, its PoB mod row) for each essence the base's class takes at this item level."""
    kind = item_class(base)
    return [(essence, mods[mod_id]) for mod_id, (essence, classes) in ESSENCES.items()
            if kind in classes and mod_id in mods and mods[mod_id].get("level", 0) <= item_level]


def payload_prices(prices) -> dict[str, float]:
    """Each Perfect Essence's and Crystallisation Omen's divine price from poe.ninja's exchange (none: no offer)."""
    if prices is None:
        return {}
    names = {essence for essence, _ in ESSENCES.values()} | set(OMENS.values())
    return {name: price.divine for name, price in prices.exchange.items() if name in names and price.divine}


def enabled() -> bool:
    """POE2_SEARCH_ESSENCES=1 turns the Perfect Essence trial on; OFF by default until a set measures it higher."""
    return os.environ.get("POE2_SEARCH_ESSENCES", "0") == "1"
