"""What a search knows besides its engine: the request compiled against game data, prices, and
the catalogs derived from both (which gems may be socketed, which items fit each slot at what
price, the passive graph).

The parent process sends plain data (`payload`); each process builds a `Context` from it once and
caches it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import time
from collections import deque
from dataclasses import dataclass
from functools import cached_property

from builder.data import GameData, liveness, rare_prices, unique_versions
from builder.data.rules import rules as game_rules
from builder.ledger import gap_list, item_lines, translate, wrong
from builder.request.constraints import PRESSED_PERSISTENT, CompiledRequest, compile_request
from builder.request.schema import BuildRequest, DefenceStyle, GearTier
from builder.search import anoints, granted_rolls

# Most a single unique may cost at each gear tier (divine), on top of the request's divine cap
TIER_UNIQUE_CAP = {GearTier.LEAGUE_START: 1.0, GearTier.MID: 20.0, GearTier.ENDGAME: 300.0, GearTier.MIRROR: math.inf}
SUPPORT_SLOTS = 5  # support sockets on a fully socketed skill gem
MAX_GEM_GROUPS = 9  # non-item gem groups the game allows
GEM_LEVEL = 20
GEM_QUALITY = 20
# PoB-legacy gems the current game still offers, each with its evidence (none since COV24; Context.gems offers them; bridge.supportCandidates
# returns legacy gems too and the search keeps those Context.allowed_gem allows). live.json's support weight can't tell a
# live support from a removed one ("live (inferred)" for every one of them, also Overextend, Font of Rage and Ricochet III,
# which the patch notes removed), but other game-file fields can (AUDIT30 A30-2, 14 Nov: a gem with no Gemcutting row,
# CraftingTypes [] and Tier 0, and no item, passive or quest source, can't be obtained; the gone gems below all match it). The
# second source is poe2db's current page (a "From" card only on an obtainable gem, AUDIT31), the wiki's item data
# (drop_enabled / removal_version, AUDIT31) and the patch notes. Why PoB marks a gem legacy: its own flag (Export/Skills/sup_*.txt "legacy = true"), set by hand for gems
# "no longer obtainable" (PoB PR #1935, from poe2wiki's Historical content and the 0.5.0 notes' "Can no longer be obtained")
# or "drop-disabled" (PR #2281, issue #2253; COV24). The gems PoB marks legacy (COV23 and COV24, checked: poe2db's current
# page of each; the 0.5.0 patch notes (kami-labs' full copy), the official 0.3.0 notes (pathofexile.com forum 3826682) and
# 0.4.0's disabled list (maxroll / game8 copies, via search); PoB's PRs #1935 / #2281 and issue #2253 (GitHub); poe2wiki's
# page of each, read through search-engine excerpts because poe2wiki turns bots away; pathofexile.com forum threads):
# - Infernal Legion III, Overextend, Shock Conduction II: gone. 0.5.0: "Can no longer be obtained" (poe2db's version
#   history and the 0.5.0 notes agree). PoB is right to leave them out.
# - Murderous Intent, Bloodlust, Commandment, Lasting Ground: drop-disabled, can't be engraved since 0.3.0 (a change the
#   0.3.0 notes don't list): poe2wiki's page of each says "drop disabled ... Can be only acquired in Early access standard
#   league from other players" with an undocumented 0.3.0 "can no longer be engraved" row; PoB's issue #2253 names Murderous
#   Intent and PR #2281 adds the other three; players on the forum (threads 3876247, Nov 2025: Murderous Intent "can no
#   longer cut any new gems"; 3978747, Jun 2026: Bloodlust missing from the Gemcutting menu). poe2db's pages show no mark,
#   but poe2db keeps deprecated entries unmarked (the user's note). So none is obtainable in a new league: an OVERCOUNT
#   while the search offered Murderous Intent (MECH60 / COV22 read it live from the game files, which can't tell, and
#   poe2db). Its Cull model (MECH63) stays for a PoB code that holds one.
# - Undermine: PoB's issue #2253 names it drop-disabled; no second source found (poe2wiki's page didn't come up; poe2db
#   shows no mark): UNVERIFIED either way. It stays out as before, and also because PoB scores it 0 (it maps neither
#   consume_maim_on_hit_to_break_%_armour nor cannot_inflict_maim, so the skill's own Maim stays on: an OVERCOUNT).
LIVE_LEGACY: dict[str, str] = {}
# Gems PoB offers as ordinary gems that the game removed or stopped giving, each with two sources that agree (the rule for
# removing anything): Context.gems leaves them out, so the search never picks them; any_gems still reads them in a PoB code
# that holds one. The game files still carry them (live.json "live (inferred)" by the support weight, as for those above).
GONE_GEMS = {
    # COV23 (checked: the official 0.3.0 notes, pathofexile.com forum 3826682: "Blood in the Eyes, Discombobulate, Unsteady
    # Tempo, Unbating, Unbending, Untouchable, and Unyielding can no longer be engraved in the Gemcutting menu, and existing
    # Gems will be deleted upon logging in"; poe2db's current page https://poe2db.tw/us/Untouchable quotes the same in its
    # version history, and no later version brings it back). PoB carries only Untouchable of the seven, not marked legacy.
    "Untouchable": "removed in 0.3.0 (the official 0.3.0 notes and poe2db's version history agree)",
    # COV24 (checked: poe2db's current page https://poe2db.tw/us/Shock_Siphon, version history "0.2.0: Shock Siphon can no
    # longer be engraved in the Gemcutting menu. Existing copies of this support will still function"; the same line quoted
    # from the 0.2.0 notes on pathofexile.com forum thread 3755262, Apr 2025, where players report they can't cut it; poe2wiki's
    # page, through search excerpts, gives the same 0.2.0 row; no later version brings it back). Existing copies still work,
    # so a PoB code holding one still reads it; a new character can't get one.
    "Shock Siphon": "can't be engraved since 0.2.0 (poe2db's version history and the 0.2.0 notes agree)",
    # COV25 (checked: the official 0.3.0 notes, pathofexile.com forum 3826682: "Living Bomb and Solar Orb have swapped
    # places." / "Living Bomb is now a tier 3 Elemental Skill Gem, while Solar Orb is an inherent skill to Pyrophyte
    # Staves"; poe2db's current page https://poe2db.tw/us/Solar_Orb: "From: Pyrophyte Staff", the same 0.3.0 row, later rows
    # bug fixes only). PoB still exports it as an ordinary gem (and Living Bomb as item-granted: the swap the other way, an
    # UNDERCOUNT COV26 fixes through ITEM_SKILL_GEMS). The skill stays reachable from its sources: the Pyrophyte Staff base and The Searing Touch
    # (Context.granted_by); only the free gem copy goes.
    "Solar Orb": "a Pyrophyte Staff's own skill since 0.3.0, not a gem (the 0.3.0 notes and poe2db agree)",
    # COV26, from AUDIT30 (A30-28) and AUDIT31 (A31-1, A31-20), RULE_AUDIT.md (checked by them: each has no Gemcutting row
    # (CraftingTypes [], Tier 0), is no lineage gem, and nothing in the game files grants it; the wiki's item data marks
    # each removed or drop-disabled, agreeing with the game files on all 23 offered gems it marks; plus the sources named).
    # Supports removed in 0.3.0 (the wiki's removal_version; Daze is PoB's name for Discombobulate, which the official
    # 0.3.0 notes delete; Ruthless: poe2wiki "removed in 0.3.0"; Reverberate: poe2wiki "Not in game"):
    **{name: "removed in 0.3.0 (the game files have no Gemcutting row or source; the wiki's item data agrees)" for name in (
        "Daze", "Ruthless", "Reverberate", "Charm Bounty", "Ancestral Aid", "Arms Length", "Unerring Power", "Wind Wave")},
    # Supports drop-disabled (the wiki's item data; poe2wiki "drop disabled ... Can only be acquired in Early Access standard
    # league from other players" for Lockdown, Ferocity, Freezefork, Flamepierce, Magnetic Remnants; forum thread 3830188,
    # Aug 2025: Freezefork, Flamepierce and Stormchain can't be engraved). Bounty I / II replaced the old Bounty gems in
    # 0.3.0 and drop: they stay.
    **{name: "drop-disabled (the game files have no Gemcutting row or source; the wiki's item data agrees)" for name in (
        "Lockdown", "Ferocity", "Freezefork", "Flamepierce", "Magnetic Remnants", "Stormchain", "Life Bounty",
        "Mana Bounty", "Auto Reload", "Dauntless", "Malady", "Rusted Spikes", "Spar")},
    # AUDIT31 A31-20: likely never released (no Gemcutting row or source in the game files; poe2db's page has no "From"
    # card, as on all 21 removed or drop-disabled gems above and on none of 15 sampled live supports; no wiki item; no
    # patch note 0.1.0-0.5.5f names either).
    **{name: "never obtainable (no Gemcutting row or source in the game files; poe2db shows no acquisition)" for name in (
        "Flame Pillar", "Relentless Rage")},
    # Skills whose FREE gem copy goes (as Solar Orb's); each stays reachable from what grants it (Context.granted_by):
    # Coiling Bolts (the wiki: "Granted as an inherent weapon skill from Liminal Coil"; game ItemInherentSkills on
    # FourWandUnique1), Cackling Companions (the wiki and poe2db: "From: Hysseg's Claw"), Detonate Minion (the Skeletal
    # Arsonist's own command, PoB's "Explosive Demise", DestructiveLinkSkeletonBombadierMinion in Skills/minion.lua; the
    # wiki has no gem for it): a free copy beside the minion double-counts it.
    "Coiling Bolts": "Liminal Coil's own skill, not a gem (the wiki and the game's ItemInherentSkills agree)",
    "Cackling Companions": "Hysseg's Claw's skill, not a gem (the wiki and poe2db agree)",
    "Detonate Minion": "the Skeletal Arsonist's own command, not a gem (PoB's minion skills and the wiki agree)",
}

# Gems an expansion mechanic gives, not Gemcutting (AUDIT30 A30-31, AUDIT31 A31-19: the 0.5 Runes of Aldur "runeforged"
# gems; the game's BaseItemTypes tag "runeforged", none cuttable; the 0.5.0 notes' Kalguuran Supports "crafted from
# remnants"; all drop-enabled in the wiki's item data). Offered: the mechanic is core since 0.5.5 (AUDIT32 A32-2; the
# 0.5.5 notes, "Runes of Aldur Moving to Core": "Farrow, Dannig, and Expedition content is now available in the Atlas as a
# core mechanic, including in Standard League", and "The Verisium Anvil is now unlocked by completing The Runeseeker
# quest in Act 4 outside of the Runes of Aldur League"; the current league is Forbidden Rites), so a new character in
# any league can get them.
LEAGUE_GEMS = {name: "runeforged (Runes of Aldur, core since 0.5.5)" for name in (
    "Animus Exchange", "Animus Splinters", "Bitter Dead", "Concussive Runes", "Conductive Runes", "Detonate Living",
    "Eternal March", "Explosive Transmutation", "Fist Of Kalguur", "Fragments Of The Past", "Frostflame Nova",
    "Grim Pillars", "Healing Runes", "Hollow Shell", "Leylines", "Powered by Verisium", "Rain of Blades", "Refutation",
    "Remnants of Kalguur", "Repulsion", "Runeforged Blades", "Runic Extraction", "Runic Infusion", "Runic Reprieve",
    "Scouring Flame", "Skyfall", "Triskelion Cascade", "Verisium Manifestations", "Voltaic Barrier", "Wardbound Minions")}

# Skills PoB marks item-granted (fromItem) that the game now gives as ordinary skill gems, each with two sources that agree:
# Context.gems offers them. (checked, COV26: of PoB's 89 fromItem active skills only Living Bomb carries a gem tier above 0
# (PoB Data/Gems.lua Tier = 3), and it has a game SkillGems row on BaseItemType Metadata/Items/Gem/SkillGemLivingBombPlayer;
# Context.granted_by finds no base, unique or passive granting it, so before this nothing offered it: an UNDERCOUNT. The
# official 0.3.0 notes, pathofexile.com forum 3826682: "Living Bomb and Solar Orb have swapped places." / "Living Bomb is
# now a tier 3 Elemental Skill Gem"; poe2db's current page https://poe2db.tw/us/Living_Bomb; COV25's research.md.)
ITEM_SKILL_GEMS = {
    "Living Bomb": "a tier 3 skill gem since 0.3.0 (the 0.3.0 notes, PoB's Tier = 3 and the game's SkillGems row agree)",
}

# Uniques PoB carries that a new character in the current league can't obtain (the user, 13 Nov 2026: "No, league-obtainable
# only"), each with two sources that agree: item_options and skill_source leave them out, so the search never offers one;
# a PoB code or a request that names one still prices it (unique_option). COV25, from PoB's "Source: No longer obtainable"
# tags (vendor PoB Data/Uniques) and the research in its report.
GONE_UNIQUES: dict[str, str] = {
    # The official 0.3.0 notes (pathofexile.com forum 3826682): "The following Uniques can no longer drop, and will be
    # reintroduced at a later date: Infernoclasp, Forbidden Gaze, Thunderstep, The Wailing Wall, The Fallen Formation,
    # Wylund's Stake, The Deepest Tower, and The Remembered Tales." poe2db's pages carry the same 0.3.0 row and "Limit: Drop
    # disabled" (Infernoclasp, Thunderstep, Wylund's Stake opened; none has a 0.4 or 0.5 row); PoB tags each "Source: No
    # longer obtainable". Not brought back (checked: the official 0.4.0 and 0.5.0 notes, forum 3883495 / 3932540, and
    # Fextralife's copy of 0.5.0-0.5.5, read for the eight names). A third source (AUDIT30 A30-1): the game's
    # UniqueStashLayout hides each in league stashes (ShowIfEmptyChallengeLeague and ...Standard False on 10 of 449 rows:
    # these seven, Merit of Service and two old Guiding Palm rows; the current Guiding Palm is obtainable, the user 14 Nov,
    # and its old rows are variants the variant rules leave out).
    **{name: "can no longer drop since 0.3.0 (the 0.3.0 notes and poe2db agree; not reintroduced through 0.5.5)" for name in (
        "Infernoclasp", "Forbidden Gaze", "Thunderstep", "The Wailing Wall", "The Fallen Formation", "Wylund's Stake",
        "The Deepest Tower")},
    # The official 0.5.0 notes (forum 3932540): "The Road Warrior Unique Body Armour can no longer be obtained."; poe2db marks
    # it drop disabled (search excerpt); PoB tags it "No longer obtainable". The game's UniqueStashLayout still shows it in
    # league stashes (row 3, ShowIfEmptyChallengeLeague True: the one source that disagrees; AUDIT30 A30-4).
    "The Road Warrior": "can no longer be obtained since 0.5.0 (the 0.5.0 notes and poe2db agree)",
    # COV26, AUDIT30 A30-1 / AUDIT31 A31-2: four sources agree: UniqueStashLayout hides it in league stashes; poe2db "Limit:
    # Drop disabled"; PoB "Source: No longer obtainable"; the wiki's item data drop_enabled false, removal_version 0.3.0 (a
    # poe2wiki excerpt saying it "can drop anywhere" is older text).
    "Merit of Service": "drop-disabled since 0.3.0 (the game's stash layout, poe2db, PoB and the wiki's item data agree)",
    # COV26, the user settled it (14 Nov, a poe2db screenshot, "Limit: [Drop disabled]": "poe2db is almost always
    # trustworthy and says drop disabled ... I'd bet on these being disabled"): poe2db Drop disabled, the wiki's item data,
    # PoB's "No longer obtainable" and the user. The game's UniqueStashLayout still shows it in league stashes (True / True:
    # the one source that disagrees).
    "Blessed Bonds": "drop-disabled (poe2db, the wiki's item data and PoB agree; the user, 14 Nov)",
}

ARMOUR_SLOTS = ["Helmet", "Body Armour", "Gloves", "Boots"]
JEWELLERY_SLOTS = ["Amulet", "Ring 1", "Ring 2", "Belt"]
SLOT_TYPE = {"Helmet": "Helmet", "Body Armour": "Body Armour", "Gloves": "Gloves", "Boots": "Boots",
             "Amulet": "Amulet", "Ring 1": "Ring", "Ring 2": "Ring", "Belt": "Belt"}
TWO_HANDED = {"Bow", "Crossbow", "Staff", "Two Hand Axe", "Two Hand Mace", "Two Hand Sword", "Talisman"}
DEFENCE_LAYERS = {
    DefenceStyle.ARMOUR: {"Armour"}, DefenceStyle.EVASION: {"Evasion"},
    DefenceStyle.ENERGY_SHIELD: {"EnergyShield"}, DefenceStyle.ARMOUR_EVASION: {"Armour", "Evasion"},
    DefenceStyle.ARMOUR_ENERGY_SHIELD: {"Armour", "EnergyShield"},
    DefenceStyle.EVASION_ENERGY_SHIELD: {"Evasion", "EnergyShield"},
    DefenceStyle.HYBRID: {"Armour", "Evasion", "EnergyShield"},
}


@dataclass(frozen=True)
class ItemOption:
    name: str  # the unique's name, or the base name for a normal item
    base: str
    type: str
    sub_type: str | None
    raw: str
    unique: bool
    price: float | None  # divine; None in SSF, where prices don't apply
    confidence: str  # price confidence: high / medium / low, "found" for normal bases, "ssf"
    level: int
    rare: bool = False
    affixes: tuple[str, ...] = ()  # a rare's modifier lines
    runes: tuple[str, ...] = ()  # what's in its sockets
    anoints: tuple[str, ...] = ()  # notables it allocates by instilling (builder/search/anoints.py)
    variants: tuple[str, ...] = ()  # a unique's chosen variants, when the search chose them (radius.py)
    granted_assumed: int | None = None  # a rolled granted level assumed, not priced (granted_rolls.ENDGAME_LEVEL)

    @property
    def granted_basis(self) -> str:
        """Where its rolled granted level comes from (models.granted_level_notes): "priced", "endgame" or "middle"."""
        return "priced" if granted_rolls.parse_label(self.variants) else "endgame" if self.granted_assumed else "middle"

    def describe(self) -> dict:
        described = {"name": self.name, "base": self.base, "type": self.type, "unique": self.unique, "rare": self.rare,
                     "price": self.price, "confidence": self.confidence, "affixes": list(self.affixes),
                     "runes": list(self.runes)}
        if self.anoints:
            described["anoints"] = list(self.anoints)
        if self.variants:
            described["variants"] = list(self.variants)
        if self.granted_assumed:
            described["granted_level"] = self.granted_assumed  # an endgame copy, assumed until priced (granted_versions)
        return described


VARIANT_LINE = re.compile(r"^\{variant:([\d,]+)\}(.*)$")


def implicit_variants(implicit: str | None) -> list[tuple[int, str]]:
    """A base's implicit variants (Absent Amulet: one of 7 granted skills, Cast on Critical, Archmage, ...), each with its
    line untagged."""
    return [(int(k), m.group(2)) for line in (implicit or "").splitlines() if (m := VARIANT_LINE.match(line))
            for k in m.group(1).split(",")]


def implicit_variant(implicit: str | None, variant: int | None = None) -> str:
    """The implicit with only `variant`'s line, untagged (none without one). PoB applies none of a variant base's
    granted skills while the text carries every {variant:n} line (probe, 8 Nov), and one variant is all an item has."""
    lines = []
    for line in (implicit or "").splitlines():
        m = VARIANT_LINE.match(line)
        if m is None:
            lines.append(line)
        elif variant is not None and str(variant) in m.group(1).split(","):
            lines.append(m.group(2))
    return "\n".join(lines)


def normal_item(base: dict) -> str:
    """A normal (white) base, with its implicit: PoB doesn't add implicits by itself."""
    from builder.search.rares import quality_lines  # (20% quality where currency raises it: rares.QUALITY_TYPES)

    implicit = implicit_variant(base.get("implicit"))
    if not implicit:
        return "\n".join(["Rarity: Normal", base["name"], *quality_lines(base)])
    lines = [re.sub(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)",
                    lambda m: str(int(round((float(m.group(1)) + float(m.group(2))) / 2))) if "." not in m.group(0)
                    else f"{(float(m.group(1)) + float(m.group(2))) / 2:.1f}", line) for line in implicit.splitlines()]
    from builder.ledger.items import corrected_item  # ledger translations on the implicit (COV2)

    return corrected_item("\n".join(["Rarity: Normal", base["name"], *quality_lines(base), f"Implicits: {len(lines)}",
                                      *lines]))


class Context:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = BuildRequest.model_validate(payload["request"])
        self.data = GameData()
        self.unique_prices: dict[str, list] = payload.get("unique_prices") or {}
        self.gem_prices: dict[str, float] = payload.get("gem_prices") or {}
        self.augment_prices: dict[str, float] = payload.get("augment_prices") or {}
        self.emotion_prices: dict[str, float] = payload.get("emotion_prices") or {}  # Liquid Emotions, for anoints
        # Trade prices of specific versions of a unique (builder/data/unique_versions.py)
        self.unique_versions: dict[str, dict] = payload.get("unique_versions") or {}
        self.rare_model = rare_prices.RarePriceModel.from_payload(payload.get("rare_model"))
        self.rare_model.materials = dict(payload.get("craft_prices") or {})
        rare_prices.use(self.rare_model)

    @cached_property
    def compiled(self) -> CompiledRequest:
        return compile_request(self.request, self.data)

    @property
    def ssf(self) -> bool:
        return self.request.budget.ssf

    @property
    def level(self) -> int:
        return self.request.level

    @cached_property
    def price_cap(self) -> float:
        """Most any single purchase may cost."""
        cap = TIER_UNIQUE_CAP[GearTier(self.request.budget.gear_tier)]
        if self.request.budget.divines is not None and not self.ssf:
            cap = min(cap, self.request.budget.divines)
        return cap

    @property
    def budget(self) -> float:
        divines = self.request.budget.divines
        return math.inf if divines is None or self.ssf else divines

    # Gems -------------------------------------------------------------------------------------

    @cached_property
    def gems(self) -> dict[str, dict]:
        """Gems a player can socket at the requested level, by name. PoB's legacy gems are left out, except those
        LIVE_LEGACY names (checked live against the game files and a second source); GONE_GEMS are left out too. PoB's
        item-granted skills are left out, except the ITEM_SKILL_GEMS the game now gives as gems (Living Bomb)."""
        gems = {}
        for gem in self.data.gems:
            if (gem.get("fromItem") and gem["name"] not in ITEM_SKILL_GEMS) or gem.get("fromTree") \
                    or (gem.get("legacy") and gem["name"] not in LIVE_LEGACY) or gem["name"] in GONE_GEMS:
                continue
            if (gem.get("levelRequirement") or 0) > self.level:
                continue
            gems.setdefault(gem["name"], gem)
        return gems

    @cached_property
    def any_gems(self) -> dict[str, dict]:
        """Every gem by name, also those a passive or item grants (Hollow Form): for reading a group's skill types."""
        gems = dict(self.gems)
        for gem in self.data.gems:
            if not gem.get("legacy") or gem["name"] in LIVE_LEGACY:
                gems.setdefault(gem["name"], gem)
        return gems

    def gem_price(self, name: str) -> tuple[float | None, str] | None:
        """Price of a gem that costs real currency (lineage supports); None if it can't be bought
        within the caps. Ordinary gems are cheap and priced at 0 ("found")."""
        gem = self.gems.get(name)
        if gem is None:
            return None
        if not gem.get("lineage"):
            return 0.0, "found"
        if self.ssf:
            return None if self.request.budget.gear_tier == GearTier.LEAGUE_START else (None, "ssf")
        price = self.gem_prices.get(name)
        if price is None or price > self.price_cap:
            return None  # unpriced items are never treated as free
        return price, "exchange"

    @cached_property
    def all_gems(self) -> dict[str, dict]:
        """Every gem by name, including skills only passives or items grant, and also by PoB's baseTypeName where it
        differs: an item's "Grants Skill" line names the skill as the game does, which PoB's gem data keeps as
        baseTypeName (the Sceptres' "Skeletal Warrior Minion" is gem "Skeletal Warrior", The Last Lament's "Compose
        Requiem" gem "Requiem"; PoB's ModParser grants them by that name, gemIdLookup). A real gem name always wins,
        and templated names ("Companion: {0}") are skipped."""
        gems: dict[str, dict] = {}
        for gem in self.data.gems:
            gems.setdefault(gem["name"], gem)
        for gem in self.data.gems:
            alias = gem.get("baseTypeName")
            if alias and "{" not in alias:
                gems.setdefault(alias, gem)
        return gems

    @cached_property
    def granted_by(self) -> dict[str, list[tuple[str, str, str | None]]]:
        """Skill name -> [(kind, source name, ascendancy)] for skills a passive ("passive"), a
        unique ("unique") or every base of an item type ("base", the type: every Spear's implicit grants
        Spear Throw, which PoB gives any Spear main hand, CalcSetup's default attacks) grants. A skill
        only some bases of a type grant (a Withered Wand's Chaos Bolt; granting_bases names them) is listed as
        "bases" with the type: the gear search keeps that slot to those bases (inner.Search, SlotSpec.bases)."""
        grants: dict[str, list[tuple[str, str, str | None]]] = {}
        pattern = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$")
        for (kind_of, skill), names in sorted(self._base_grants.items()):
            whole = len(names) == sum(1 for b in self.data.bases if b["type"] == kind_of and not b.get("hidden"))
            grants.setdefault(skill, []).append(("base" if whole else "bases", kind_of, None))
        for node in self.data.tree["nodes"]:
            for line in node.get("stats") or []:
                if m := pattern.search(line):
                    grants.setdefault(m.group(1), []).append(("passive", node["name"], node.get("ascendancy")))
        for unique in self.data.uniques:
            for line in unique["raw"].splitlines():
                if m := pattern.search(re.sub(r"\{[^}]*\}", "", line)):
                    grants.setdefault(m.group(1), []).append(("unique", unique["name"], None))
        return grants

    @cached_property
    def _base_grants(self) -> dict[tuple[str, str], tuple[str, ...]]:
        """(item type, skill) -> the non-hidden bases of that type whose implicit grants the skill (Vendor PoB
        Data/Bases: "Grants Skill: Level (1-20) Chaos Bolt" on Withered / Critical / Frigid / Torture Wand)."""
        pattern = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$")
        found: dict[tuple[str, str], list[str]] = {}
        for base in self.data.bases:
            if base.get("hidden"):
                continue
            for line in (base.get("implicit") or "").splitlines():
                if not line.startswith("{") and (m := pattern.match(line)):
                    found.setdefault((base["type"], m.group(1)), []).append(base["name"])
        return {key: tuple(names) for key, names in found.items()}

    def granting_bases(self, skill: str, item_type: str) -> tuple[str, ...]:
        """The bases of `item_type` whose implicit grants `skill` (all of them for Spear Throw on a Spear)."""
        return self._base_grants.get((item_type, skill), ())

    def skill_source(self, skill: str) -> tuple[str, str, str | None] | None:
        """For a skill no gem gives, the first allowed passive or unique that grants it."""
        for kind, source, ascendancy in self.granted_by.get(skill, []):
            if kind == "passive" and source not in self.compiled.filters.forbidden_passives:
                return kind, source, ascendancy
            if kind == "unique" and source not in self.compiled.filters.forbidden_uniques and source not in GONE_UNIQUES:
                return kind, source, ascendancy
        return None

    def allowed_gem(self, name: str) -> bool:
        return name in self.gems and name not in self.compiled.filters.forbidden_gems

    @cached_property
    def persistent_candidates(self) -> list[str]:
        """Persistent skills (auras, heralds, buffs) that need no trigger setup: candidates for
        spare Spirit. Meta gems need a socketed skill to do anything and aren't proposed yet; buffs
        you press to release what they stored (Iron Ward) are buttons, not spare-Spirit fillers."""
        names = []
        for name, gem in self.gems.items():
            types = set(gem["skillTypes"])
            if gem["support"] or "Persistent" not in types or "Meta" in types or name in PRESSED_PERSISTENT:
                continue
            if self.allowed_gem(name) and self.gem_price(name) is not None:
                names.append(name)
        return sorted(names)

    # Items ------------------------------------------------------------------------------------

    @cached_property
    def bases_by_name(self) -> dict[str, dict]:
        return {b["name"]: b for b in self.data.bases}

    def _base_ok(self, base: dict, unique: bool = False) -> bool:
        """PoB hides a base only a unique uses (Forgotten Warden's Primal Markings, Loreweave's Ornate Ringmail): never a
        normal item, but its unique is (it's offered only with a market price, so only while it's in the game)."""
        req = base.get("req") or {}
        return ((unique or not base.get("hidden")) and (req.get("level") or 0) <= self.level
                and base["type"] not in self.compiled.filters.forbidden_item_classes)

    def _style_ok(self, base: dict | None) -> bool:
        """Armour pieces must only carry the defence layers the requested style allows."""
        style = DefenceStyle(self.request.defence)
        if style == DefenceStyle.ANY or base is None or base["type"] not in SLOT_TYPE.values():
            return True
        layers = {k for k, v in (base.get("armour") or {}).items() if k in ("Armour", "Evasion", "EnergyShield") and v}
        return layers <= DEFENCE_LAYERS[style]

    @cached_property
    def gaps(self) -> dict:
        """PoB's own list of passive and unique lines it doesn't calculate."""
        return gap_list(self.data.dir)

    @cached_property
    def passive_corrections(self) -> list[dict]:
        """Ledger translations for ignored passive lines, and replacements for tree lines PoB parses wrongly (`drop`:
        the line whose parsed modifiers the bridge removes): [{id, entry, lines[, drop]}] for the bridge."""
        found = []
        for gap in self.gaps.get("passives", []):
            if translated := translate(gap["line"]):
                found.append({"id": gap["id"], "entry": translated[0].id, "lines": translated[1]})
        from builder.search.passive_models import wrong_tree_lines  # (COV-B: tree lines PoB parses wrongly)
        return found + wrong_tree_lines(self.data.tree["nodes"])

    @cached_property
    def unique_gaps(self) -> dict[str, set[str]]:
        """Unique name -> its lines PoB ignores."""
        found: dict[str, set[str]] = {}
        for gap in self.gaps.get("uniques", []):
            found.setdefault(gap["unique"], set()).add(gap["line"])
        return found

    def corrected_raw(self, name: str, raw: str) -> str:
        """A unique's text with ledger corrections: lines standing in for the ones PoB ignores are
        added at the end, and lines PoB gets wrong are replaced in place. Each correction keeps its
        line's variant tags, so only the selected version of the unique counts."""
        ignored = self.unique_gaps.get(name, set())
        lines, added = [], []
        for line in raw.split("\n"):
            prefix, text = re.match(r"^((?:\{[^}]*\})*)(.*)$", line).groups()
            if text in ignored and (translated := translate(text)) and translated[0].on_items:
                # (`share:` lines as their item stand-ins since COV17: ledger.item_lines)
                added += [prefix + extra for extra in item_lines(translated[1])]
            elif fixed := wrong(text):
                line = prefix + (fixed[1] if fixed[1] is not None else "{disabled}" + text)
            lines.append(line)
        return "\n".join(self._with_quality(name, lines) + added)

    @cached_property
    def _unique_types(self) -> dict[str, tuple[str, str]]:
        return {u["name"]: (u.get("type") or "", u.get("base") or "") for u in self.data.uniques}

    def _with_quality(self, name: str, lines: list[str]) -> list[str]:
        """The manager's ruling (12 Nov ~12:55, on COV6's finding; COV7): an uncorrupted unique weapon, armour piece,
        shield, focus or flask (rares.QUALITY_TYPES) with no "Quality:" line gets Quality 20, as a generated item does:
        a player can raise it with currency, and PoB gives an item with no Quality line 0% (57 such uniques in the dump,
        12 Nov; the other 265 of those types already say Quality: 20). A corrupted unique keeps the quality its text
        gives (0 if none): currency can't change it. Placed after the base name line, before the implicits."""
        from builder.search.rares import QUALITY, QUALITY_TYPES

        kind, base = self._unique_types.get(name, ("", ""))
        bare = [re.sub(r"^(?:\{[^}]*\})*", "", l).strip() for l in lines]
        if kind not in QUALITY_TYPES or any(b.startswith("Quality:") or b == "Corrupted" for b in bare):
            return lines
        at = max((i for i, b in enumerate(bare[:20]) if base and b.endswith(base)), default=None)
        if at is None:
            return lines
        return lines[:at + 1] + [f"Quality: {QUALITY}"] + lines[at + 1:]

    def unique_price(self, name: str) -> tuple[float | None, str] | None:
        if self.ssf:
            return None, "ssf"
        entry = self.unique_prices.get(name)
        if not entry:
            return None
        divine, listings = entry
        if divine > self.price_cap:
            return None
        confidence = "unknown" if listings is None else "high" if listings >= 20 else "medium" if listings >= 5 else "low"
        return divine, confidence

    @cached_property
    def item_options(self) -> dict[str, list[ItemOption]]:
        """Every item the gear block may propose, by item type ("Bow", "Helmet", "Ring", ...):
        normal bases (always available) and affordable, allowed uniques."""
        options: dict[str, list[ItemOption]] = {}
        rules = game_rules()
        for base in self.data.bases:
            # Bases only uniques use (Golden Visage, ...) and weapon classes not in the game yet
            # are never normal, magic or rare items (builder/data/game_rules.yaml)
            if self._base_ok(base) and self._style_ok(base) and rules.normal_base_ok(base):
                options.setdefault(base["type"], []).append(ItemOption(
                    base["name"], base["name"], base["type"], base.get("subType"), normal_item(base), False,
                    0.0, "found", (base.get("req") or {}).get("level") or 0))
        forbidden = self.compiled.filters.forbidden_uniques
        floors = self.requested_floors
        for unique in self.data.uniques:
            base = self.bases_by_name.get(unique["base"])
            if unique["name"] in forbidden or unique["name"] in GONE_UNIQUES or base is None or not self._base_ok(base, unique=True) or not self._style_ok(base):
                continue
            if rules.blocked_floors(unique["raw"]) & floors:
                continue  # e.g. Veil of the Night under a resistance floor (game_rules.yaml floor_blockers)
            priced = self.unique_price(unique["name"])
            if priced is None:
                continue
            price, confidence = priced
            options.setdefault(base["type"], []).extend(self.granted_versions(ItemOption(
                unique["name"], base["name"], base["type"], base.get("subType"),
                self.corrected_raw(unique["name"], unique["raw"]), True,
                price, confidence, (base.get("req") or {}).get("level") or 0)))
        return options

    @cached_property
    def requested_floors(self) -> set[str]:
        """The request's floors above zero, named as game_rules.yaml's floor_blockers name them."""
        floors = self.request.floors
        named = {name for name in ("life", "energy_shield", "mana", "ward", "block", "evade", "spare_spirit")
                 if getattr(floors, name, None)}
        named |= {f"resists.{kind}" for kind, value in floors.resists.items() if value and value > 0}
        named |= {f"max_hit.{kind}" for kind, value in floors.max_hit.items() if value and value > 0}
        return named

    def unique_option(self, name: str) -> ItemOption | None:
        """A specific unique (required by the request), affordable or not."""
        for unique in self.data.uniques:
            if unique["name"] == name:
                base = self.bases_by_name.get(unique["base"])
                if base is None:
                    return None
                priced = self.unique_price(name) or (None, "over budget or unpriced")
                option = ItemOption(name, base["name"], base["type"], base.get("subType"),
                                    self.corrected_raw(name, unique["raw"]), True,
                                    priced[0], priced[1], (base.get("req") or {}).get("level") or 0)
                # Required: the cheapest level the trade site sells (granted_versions), else an endgame copy
                return min(self.granted_versions(option, capped=False), key=lambda o: (o.price or 0.0))
        return None

    def granted_versions(self, option: ItemOption, capped: bool = True) -> list[ItemOption]:
        """A unique granting a skill at a rolled level (granted_rolls.py): one option per level the Price unique versions
        job priced from trade listings (builder/report/version_prices.py), at that level and its price; levels nobody
        lists aren't offered. Until any level is priced, an endgame copy (granted_rolls.ENDGAME_LEVEL, at the feed's
        price for any level); in SSF the unique as PoB loads it, the middle roll. The guide and the shopping list
        label which (models.granted_level_notes)."""
        skills = granted_rolls.rolled(option.raw)
        if not skills or self.ssf:
            return [option]
        skill = skills[0][0]
        found = []
        for entry in self.unique_versions.values():
            parsed = granted_rolls.parse_label(entry.get("variants")) if entry.get("name") == option.name else None
            if parsed is None or parsed[1] != skill or not entry.get("listed") or entry.get("price") is None:
                continue
            if capped and entry["price"] > self.price_cap:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(entry["priced_at"]))
            found.append(dataclasses.replace(
                option, raw=granted_rolls.with_level(option.raw, parsed[0]), price=entry["price"],
                variants=(granted_rolls.label(*parsed),),
                confidence=f"trade, {skill} level {parsed[0]}+ ({entry['listed']} listed, {day})"))
        return sorted(found, key=lambda o: o.price) or [
            dataclasses.replace(option, raw=granted_rolls.with_level(option.raw, granted_rolls.ENDGAME_LEVEL),
                                granted_assumed=granted_rolls.ENDGAME_LEVEL)]

    # Passive tree -----------------------------------------------------------------------------

    @cached_property
    def nodes(self) -> dict[int, dict]:
        return {n["id"]: n for n in self.data.tree["nodes"]}

    @cached_property
    def class_of(self) -> dict[str, str]:
        """Ascendancy name -> class name."""
        return {a: c for c, ascendancies in self.data.classes.items() for a in ascendancies}

    @staticmethod
    def node_as(node: dict, class_name: str | None, ascendancy: str | None = None) -> dict:
        """A node as PoB shows it to this class: 59 start-area nodes become another passive for some
        classes or ascendancies (PassiveSpec takes options[class], else options[ascendancy]), e.g.
        node 30695 is Eagle Eye for a Huntress and Vile Wounds otherwise."""
        variants = node.get("variants") or {}
        return variants.get(class_name) or variants.get(ascendancy) or node

    def forbidden_nodes(self, class_name: str | None, ascendancy: str | None = None) -> set[int]:
        """Banned passives' nodes, by the names this class sees."""
        key = (class_name, ascendancy)
        cache = self.__dict__.setdefault("_forbidden_nodes", {})
        if key not in cache:
            names = self.compiled.filters.forbidden_passives
            cache[key] = {n["id"] for n in self.data.tree["nodes"]
                          if self.node_as(n, class_name, ascendancy).get("name") in names}
        return cache[key]

    def ascendancy_graph(self, ascendancy: str) -> tuple[int | None, dict[int, list[int]]]:
        """(start node, adjacency) of an ascendancy's own nodes. An alternate ascendancy with no
        nodes of its own (Abyssal Lich) uses the nodes of the one it shares a start node with:
        PoB switches those nodes to the alternate's stats when it's selected."""
        members = {i for i, n in self.nodes.items() if n.get("ascendancy") == ascendancy}
        if not members:
            start_node = next((a["startNode"] for c in self.data.tree["classes"] for a in c["ascendancies"]
                               if a["name"] == ascendancy), None)
            host = (self.nodes.get(start_node) or {}).get("ascendancy")
            if host and host != ascendancy:
                members = {i for i, n in self.nodes.items() if n.get("ascendancy") == host}
        start = next((i for i in members if self.nodes[i]["type"] == "AscendClassStart"), None)
        return start, {i: [j for j in self.nodes[i]["linked"] if j in members] for i in members}

    def main_tree_distances(self, class_name: str) -> dict[int, int]:
        """Hop distance of every main-tree node from the class start (for picking among
        same-named nodes)."""
        start = next(c["startNode"] for c in self.data.tree["classes"] if c["name"] == class_name)
        distances = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for other in self.nodes[node]["linked"]:
                if other not in distances and other in self.nodes and not self.nodes[other].get("ascendancy"):
                    distances[other] = distances[node] + 1
                    queue.append(other)
        return distances


_cache: dict[str, Context] = {}


def payload_key(payload: dict) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def context_for(payload: dict) -> Context:
    """The process-wide Context for a payload (game data loads once per worker)."""
    key = payload_key(payload)
    if key not in _cache:
        _cache.clear()
        _cache[key] = Context(payload)
    return _cache[key]


def make_payload(request: BuildRequest, prices=None) -> dict:
    """Plain data for workers: the request and the prices it may spend on (`prices` is a
    builder.data.prices.PriceTable, or None when there's no price data)."""
    unique_prices: dict[str, list] = {}
    gem_prices: dict[str, float] = {}
    augment_prices: dict[str, float] = {}
    if prices is not None:
        for name in prices.uniques:
            price = prices.unique(name)
            if price is not None:
                unique_prices[name] = [price.divine, price.listings]
                # PoB names some uniques without the accent the trade feed has (Mjolner, Mjölner)
                unique_prices.setdefault(liveness.plain(name), [price.divine, price.listings])
        for name, price in prices.exchange.items():
            if price.category == "LineageSupportGems":
                gem_prices[name] = price.divine
            elif price.category in ("Runes", "SoulCores", "Idols"):
                augment_prices[name] = price.divine
    league = getattr(prices, "league", None)
    return {
        "request": request.model_dump(mode="json"),
        "unique_prices": unique_prices,
        "gem_prices": gem_prices,
        "augment_prices": augment_prices,
        "emotion_prices": anoints.emotion_prices(prices),
        "league": league,
        "rare_model": rare_prices.RarePriceModel.load(league).payload(),
        "craft_prices": craft_prices(prices),
        "unique_versions": version_prices(league),
    }


def version_prices(league: str | None) -> dict[str, dict]:
    """The trade prices of unique versions the search may budget with: current ones only, and only
    what it reads (whether listed, the price, when)."""
    versions = unique_versions.load(league)
    return {key: {"name": v["name"], "variants": v["variants"], "listed": v.get("listed", 0), "price": v.get("price"),
                  "priced_at": v["priced_at"]}
            for key, v in versions.items() if unique_versions.lookup(versions, v["name"], v["variants"]) is not None}


def craft_prices(prices) -> dict[str, float]:
    """Jewel crafting materials from poe.ninja (empty without price data: jewels then use the
    market model alone)."""
    if prices is None:
        return {}
    found = {name: prices.exchange[name].divine for name in rare_prices.CRAFT_MATERIALS if name in prices.exchange}
    return found if len(found) == len(rare_prices.CRAFT_MATERIALS) else {}
