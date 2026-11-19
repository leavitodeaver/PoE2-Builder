"""Alloys on rares (SEARCH29; SEARCH28's plan_unrolled_mods.md part 3; RESEARCH32's alloy-table.md, R32-6 R-c).

An Alloy "Removes a random modifier and augments a Rare item with a new guaranteed modifier" (poe2db's item text; the
0.5.0 notes). Its mods never roll naturally (every Alloy* row in PoB's ModItem.lua has spawn weight default 0: checked
by RESEARCH32, 61 entries), so rares.AffixPools never offers them. Obtainable in any league: the Runes of Aldur mechanic
is core since 0.5.5 (context.LEAGUE_GEMS' note cites the 0.5.5 notes); priced from poe.ninja's "Verisium" exchange.

THE MANAGER'S RULING (18 Nov 20:25) on the random lost modifier: DPS at the EXPECTED value (the mean over which
modifier is lost), the defence FLOORS at the WORST outcome (floors count only what's guaranteed). So the search equips
the worst outcome (PoB scores it: floors and everything else read it) and carries the mean's DPS as a factor,
ALLOY_FIELD = mean DPS over the outcomes / the equipped outcome's DPS, which Objective.dps multiplies in (Build.added_stats
puts the product over the equipped Alloy items in every result; an item what-if for a slot swaps that slot's factor).
The factor is measured when the rare is made (MEDIUM: it isn't re-measured as the rest of the build moves).
The result shows a typical outcome (the one whose DPS is nearest the mean) and the worst case.

Rules kept as the lower reading (RESEARCH32: UNVERIFIED): one Alloy per item; no Alloy mod beside an affix of its mod
family; the Alloy goes on a rare with a free slot of its own type (prefix / suffix: the row's own type, not its name:
Prismatic's glove penetration is a Prefix), so whichever modifier is lost, it fits. The three Alloy rows on no poe2db
Alloy page (AlloyManaNearbyAllyAttackSpeedHybrid1, AlloySpiritPresenceAreaOfEffectHybrid1, AlloyLocalWardIncreasePercent2)
are not offered (UNREACHABLE until a source shows them, UNVERIFIED), nor the HandWraps / Converted twins (other items).
Adaptive's Sceptre line (AlloyPuppetMasterChance1) is in the game's Mods but not in PoB's mod data (checked: GameData
mods["Item"] ids, build 26650308 with this PoB), so it is offered nowhere until PoB has it (an UNDERCOUNT, said here).
"""
from __future__ import annotations

ALLOY_FIELD = "AlloyDPSScale"  # (a search stat, not PoB's: Build.added_stats / Gear's item what-ifs set it)

MARTIAL = ("One Hand Sword", "One Hand Axe", "One Hand Mace", "Two Hand Sword", "Two Hand Axe", "Two Hand Mace", "Spear",
           "Bow", "Quarterstaff", "Dagger", "Crossbow", "Flail", "Talisman")
ALL_WEAPONS = MARTIAL + ("Sceptre", "Staff", "Wand")
ARMOUR = ("Helmet", "Body Armour", "Gloves", "Boots", "Shield", "Focus")

# Mod id -> (Alloy, item classes): RESEARCH32's scratch/research32/alloy_table.md main table (46 rows matched across
# poe2db's 13 Alloy pages, PoB ModItem.lua 1773-1822 and the game's Mods). "Shield" holds Bucklers (PoB has no Buckler
# type); "Quarterstaff" is PoB's Staff / Warstaff.
ALLOYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "AlloyMaximumRunicWard1": ("Runic Alloy", ("Ring",)),
    "AlloyMaximumRunicWardPercent1": ("Runic Alloy", ("Amulet",)),
    "AlloyRunicWardRechargeRate1": ("Runic Alloy", ("Belt",)),
    "AlloyDamageAsExtraFireTwoHandWhileMissingRunicWard1": ("Adaptive Alloy", ("Staff",)),
    "AlloyDamageAsExtraFireWhileMissingRunicWard1": ("Adaptive Alloy", ("Wand",)),
    "AlloyPuppetMasterChance1": ("Adaptive Alloy", ("Sceptre",)),
    "AlloyAttackSpeedIfMissingWardRecently1": ("Adaptive Alloy", ("Gloves",)),
    "AlloyRecoverRunicWardOnCharmUse1": ("Protective Alloy", ("Belt",)),
    "AlloyMaximumRunicWardWeapon1": ("Protective Alloy", ALL_WEAPONS),
    "AlloyRunicWardOnBlock1": ("Protective Alloy", ("Shield",)),
    "AlloyRemnantPickupRange1": ("Expansive Alloy", ("Gloves",)),
    "AlloyPresenceAreaOfEffect1": ("Expansive Alloy", ("Body Armour",)),
    "AlloyManaCostEfficiency1": ("Expansive Alloy", ("Helmet",)),
    "AlloyTemporaryMinionSkillLimit1": ("Expansive Alloy", ("Boots",)),
    "AlloyCastSpeedGloves1": ("Swift Alloy", ("Gloves",)),
    "AlloyAttackSpeedRing1": ("Swift Alloy", ("Ring",)),
    "AlloyFlaskChargesPerSecond1": ("Swift Alloy", ("Belt",)),
    "AlloyTotemPlacementSpeed1": ("Swift Alloy", ("Shield", "Focus")),
    "AlloyReducedSlowPotency1": ("Cyclonic Alloy", ("Body Armour",)),
    "AlloySkillEffectDuration1": ("Cyclonic Alloy", ("Boots",)),
    "AlloyDamagingAilmentDuration1": ("Cyclonic Alloy", ("Gloves",)),
    "AlloyArchonDuration1": ("Cyclonic Alloy", ("Helmet",)),
    "AlloyElementalPenetration1": ("Prismatic Alloy", ("Gloves",)),
    "AlloyAilmentMagnitude1": ("Prismatic Alloy", MARTIAL),
    "AlloyExposureEffect1": ("Prismatic Alloy", ("Staff", "Wand", "Focus")),
    "AlloyMinionDamagingAilmentMagnitude1": ("Prismatic Alloy", ("Sceptre",)),
    "AlloySpellAreaOfEffect1": ("Mystic Alloy", ("Helmet",)),
    "AlloyAttackAreaOfEffect1": ("Mystic Alloy", ("Gloves",)),
    "AlloySpiritOnBoots1": ("Mystic Alloy", ("Boots",)),
    "AlloyChanceToChain1": ("Mystic Alloy", ("Quiver",)),
    "AlloyMaximumElementalInfusions1": ("Mystic Alloy", ("Staff", "Wand", "Sceptre")),
    "AlloyEffectOfSocketedAugments1": ("Sovereign Alloy", ALL_WEAPONS),
    "AlloyLocalWardIncreasePercent1": ("Sovereign Alloy", ARMOUR),
    "AlloyEffectOfResistanceMods1": ("Sovereign Alloy", ("Ring", "Amulet", "Belt")),
    "AlloySpellLevelManaHybrid1": ("Celestial Alloy", ("Staff", "Wand")),
    "AlloyAccuracyAttackSpeedHybrid1": ("Celestial Alloy", MARTIAL),
    "AlloyCastSpeedDamageAsExtraColdHybrid1": ("Transcendent Alloy", ("Staff",)),
    "AlloyCastSpeedDamageAsExtraColdHybridOneHand1": ("Transcendent Alloy", ("Focus", "Wand")),  # (0.5.3: back on them)
    "AlloyAttributeIncreasedLocalPhysicalDamageHybrid1": ("Transcendent Alloy", MARTIAL),
    "AlloyNaturesArchon1": ("The Runebinder's Alloy", ("Staff",)),
    "AlloyElementalSkillLimit1": ("The Runebinder's Alloy", ("Wand",)),
    "AlloyPuppeteerStacks1": ("The Runebinder's Alloy", ("Sceptre",)),
    "AlloyBallistaLimit1": ("The Runebinder's Alloy", ("Crossbow",)),
    "AlloyMarkEffect": ("The Runebinder's Alloy", ("Bow",)),
    "AlloyRetainGlory1": ("The Runefather's Alloy", ("One Hand Mace", "Two Hand Mace")),
    "AlloyBellLimit1": ("The Runefather's Alloy", ("Quarterstaff",)),
    "AlloyMeleeStrikeRange1": ("The Runefather's Alloy", ("Spear",)),
    "AlloyLightningDamageIgnites1": ("The Runefather's Alloy", ("Talisman",)),
}


def item_class(base: dict) -> str:
    """The base's class as the table names it (PoB's Staff / Warstaff is a Quarterstaff)."""
    if base.get("type") == "Staff" and base.get("subType") == "Warstaff":
        return "Quarterstaff"
    return str(base.get("type") or "")


def rows_for(base: dict, item_level: int, mods: dict[str, dict]) -> list[tuple[str, dict]]:
    """(Alloy, its PoB mod row) for each Alloy the base's class takes at this item level (the mod's level <= it)."""
    kind = item_class(base)
    return [(alloy, mods[mod_id]) for mod_id, (alloy, classes) in ALLOYS.items()
            if kind in classes and mod_id in mods and mods[mod_id].get("level", 0) <= item_level]


def payload_prices(prices) -> dict[str, float]:
    """Each Alloy's divine price from poe.ninja's "Verisium" exchange (none: no offer)."""
    if prices is None:
        return {}
    names = {alloy for alloy, _ in ALLOYS.values()}
    return {name: price.divine for name, price in prices.exchange.items() if name in names and price.divine}


def mean_scale(outcome_dps: list[float], equipped: int) -> float:
    """The mean DPS over the outcomes / the equipped outcome's (1.0 when that reads no DPS)."""
    if not outcome_dps or outcome_dps[equipped] <= 0:
        return 1.0
    return (sum(outcome_dps) / len(outcome_dps)) / outcome_dps[equipped]


def typical(outcome_dps: list[float]) -> int:
    """The outcome whose DPS is nearest the mean (the result's 'typical outcome')."""
    mean = sum(outcome_dps) / len(outcome_dps)
    return min(range(len(outcome_dps)), key=lambda i: (abs(outcome_dps[i] - mean), i))


def alloy_key(raw: str) -> str:
    """An item text without its socket lines: the Alloy registry's key (Build.alloy_raws), so the rare keeps its factor
    once Gear.socketed fills its sockets."""
    return "\n".join(line for line in (raw or "").splitlines() if not line.startswith(("Sockets:", "Rune:")))


def enabled() -> bool:
    """POE2_SEARCH_ALLOYS=0 turns the Alloy trial off (a gate's baseline arm)."""
    import os

    return os.environ.get("POE2_SEARCH_ALLOYS", "1") != "0"
