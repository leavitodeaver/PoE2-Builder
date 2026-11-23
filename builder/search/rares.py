"""Rare items and sockets (PLAN.md §5.2.3–4).

A rare is built affix by affix on a chosen base: every affix the base can roll (by its tags and the
mod's spawn weights), one per mod group, at most three prefixes and three suffixes, is scored with
PoB item what-ifs, and the best gain is added until nothing pays. The gear tier caps the item level,
the best affix tier allowed and how well affixes roll. Sockets take the best rune, Soul Core or
Idol for the item, priced from poe.ninja (unpriced ones are never treated as free).

Rare prices come from the rare price model (builder/data/rare_prices.py): the uncalibrated prior
until the user runs the trade-site calibration, then the fitted model, labelled per slot by how
much data it had. They always count against the budget.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any
from dataclasses import dataclass
from functools import cached_property

from builder.data import rare_prices
from builder.search import desecrated
from builder.ledger.items import corrected_item  # ledger translations on affix, implicit and rune lines (COV2)
from builder.request.schema import GearTier
from builder.search.context import Context, ItemOption, implicit_variant

# (SEARCHB7; RESEARCH R54-9, the manager's 14:00 23 Nov) PoB's mod groups are finer than the game's families for skill
# levels: "+ all Spell Skills" and "+ Fire Spell Skills" are two PoB groups but one game family, IncreaseSocketedGemLevel
# (Mods.Families, game 26650308), so one item can't roll both as ordinary suffixes. These are that family's PoB groups in
# the Item and Desecrated pools (tests/test_searchb7_mod_family.py rescans the tables). Outside it, in their own families:
# the Alloy hybrid AlloySpellLevelManaHybrid1 (family IncreasedMana) and HandWrapsGlobalMeleeSkillGemLevel2. Runes and
# corruption enchants aren't affixes. Live check (scratch/SEARCHB7/family_check3.py): on the 20 example builds' 260 rares
# the only pairs in this family are two wands holding that Alloy hybrid. Other families stay on PoB's groups: PoB splits
# 65 of the 390 rollable families and only this one is verified.
SKILL_LEVEL_FAMILY = "IncreaseSocketedGemLevel"
SKILL_LEVEL_GROUPS = frozenset({
    "EssenceAttackSkillLevel", "EssenceSpellSkillLevel", "GlobalSkillGemLevel", "MarkSkillGemLevels",
    *(f"GlobalIncrease{kind}SkillGemLevel{suffix}"
      for kind in ("ChaosSpell", "ColdSpell", "FireSpell", "LightningSpell", "Melee", "MinionSpell", "PhysicalSpell",
                   "Projectile", "Spell", "Trap")
      for suffix in ("", "Weapon")),
})


def exclusion_group(group: str) -> str:
    """The key "one modifier per group" holds an affix to: the game's family for skill levels, else PoB's group."""
    return SKILL_LEVEL_FAMILY if group in SKILL_LEVEL_GROUPS else group


# (item level, best affix tier allowed: 1 = top, roll within each range 0..1) per gear tier
TIER_RULES = {
    GearTier.LEAGUE_START: (68, 4, 0.3),
    GearTier.MID: (78, 2, 0.5),
    GearTier.ENDGAME: (82, 1, 0.7),
    GearTier.MIRROR: (86, 1, 1.0),
}
# The most tier-1 affixes (of groups with lower tiers) a rare people sell has, per gear tier; None
# is no cap. Rares with four tier-1 rolls trade well below mirror prices (the user, 2026-10-24; the
# calibration's instant-buyout searches for exact stat combinations undercounted them). League start
# and mid can't roll tier 1 at all.
BUY_RULES = {
    GearTier.LEAGUE_START: 0,
    GearTier.MID: 0,
    GearTier.ENDGAME: 4,
    GearTier.MIRROR: None,
}


def rare_rules_tier(budget: Any) -> GearTier:
    """Which TIER_RULES / BUY_RULES row a request's rares use (THE USER, 22 Nov ~23:50: "don't put any caps on budget, if
    I want to spend 1600div on a build, the tool should build for that. if I don't put a number in for the budget, it
    should make me the best possible build without looking at costs"). No gear tier from the request any more, except SSF
    (no prices: its rows stay the player's own pick, unchanged):
    - no number: costs ignored, the best rares (MIRROR: item level 86, top tiers, perfect rolls, any number of tier-1
      affixes);
    - a number: what the market sells, the price routes deciding the rest within the budget. ENDGAME's row there is
      MARKET AVAILABILITY, not a spending cap: a rare people sell carries at most 4 tier-1 affixes (the user's market rule,
      memory poe2-crafting-market-rules; more have no listings), rolls about 70% into each range (the rare price model
      prices tier counts, not rolls, so perfect rolls under a budget would be bought at a 70% roll's price), item level 82.
    `budget`: the request's Budget, or its dict (the shopping list's)."""
    get = budget.get if isinstance(budget, dict) else (lambda key, default=None: getattr(budget, key, default))
    if get("ssf"):
        return GearTier(get("gear_tier") or GearTier.ENDGAME)
    return GearTier.MIRROR if get("divines") is None else GearTier.ENDGAME
# Jewel crafting in 0.5 (the user, 2026-10-24; recheck at the 1.0 refresh): craft "+1 Prefix
# Modifier allowed" (a suffix) onto a jewel, add a third prefix, then replace the +1 with "(40-60)%
# increased Effect of Prefixes" (also a suffix). The finished jewel has three prefixes, one suffix
# and the effect line; the same with suffixes and the prefix-side crafts. PoB ignores the effect
# line, so the boosted values are written into the item, as the game shows them.
JEWEL_CRAFTS = {"Prefix": ("CraftedJewelAdditionalPrefixAllowed", "CraftedJewelPrefixEffect"),
                "Suffix": ("CraftedJewelAdditionalSuffixAllowed", "CraftedJewelSuffixEffect")}
EFFECT = re.compile(r"(\d+(?:\.\d+)?)% increased Effect of (Prefixes|Suffixes)")
MAGNITUDE = re.compile(r"(?<!per )(?<![\d.])(\d+(?:\.\d+)?)")
SCREEN_KEEP = 18  # affixes per prefix/suffix kept after scoring each alone
MAGIC_LIMIT = {"Prefix": 1, "Suffix": 1}
MAGIC_PRICE = 0.01  # divine per affix on a magic flask or charm
RUNE_KEEP = 6  # runes kept after scoring each in the first socket
RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")


def roll(text: str, fraction: float) -> str:
    """Fix every (low-high) range in a mod line at `fraction` of the way up."""
    def value(match: re.Match) -> str:
        low, high = float(match.group(1)), float(match.group(2))
        number = low + fraction * (high - low)
        if "." in match.group(1) or "." in match.group(2):
            return f"{number:.1f}"
        return str(int(round(number)))
    return RANGE.sub(value, text)


def boosted(line: str, effect: float) -> str:
    """A modifier line under "effect% increased Effect of Prefixes/Suffixes" (rounded down; a
    number after "per" is a step, not a magnitude, and stays)."""
    def value(match: re.Match) -> str:
        number = float(match.group(1)) * (1 + effect / 100)
        return f"{int(number * 10) / 10:.1f}" if "." in match.group(1) else str(int(number))
    return MAGNITUDE.sub(value, line)


def mod_lines(mod: dict) -> list[str]:
    return [mod[key] for key in sorted((k for k in mod if k.isdigit()), key=int)]


@dataclass(frozen=True)
class Affix:
    id: str
    group: str
    type: str  # Prefix / Suffix
    tier: int  # 1 = best tier of its group for this base
    lines: tuple[str, ...]
    tiers: int = 1  # tiers its group has on this base
    lower: Affix | None = None  # the same group one tier down (where a tier-1 cap may need it)
    crafted: bool = False  # a crafted jewel modifier (JEWEL_CRAFTS)
    odds: float = 0.0  # its share of the spawn weight among all the base's affixes (a Chaos Orb's chance)
    side_odds: float = 0.0  # its share among affixes of its own kind (once the other side is locked)
    ranged: bool = False  # it rolls within a range (Divine Orbs reroll it)
    adds: tuple[str, ...] = ()  # tags it gives the item ("no_cold_spell_mods" on a Fire Damage focus prefix)
    excluded_by: frozenset[str] = frozenset()  # tags with spawn weight 0 for it: an item carrying one can't roll it
    influence: str = ""  # (SEARCH30) the influence tag it spawns by ("marksman"): only with that influence rune socketed
    desecrated: str = ""  # (SEARCH30) a Desecrated affix's lord (builder/search/desecrated.py)
    level: int = 0  # (SEARCH32) its mod level (Mods.level): Omen of Whittling removes "the lowest level modifier"

    @property
    def top(self) -> bool:
        """A tier-1 affix of a group with lower tiers: the scarce kind on the trade site."""
        return self.tier == 1 and self.tiers > 1


class AffixPools:
    """Which affixes each base can roll at this request's gear tier."""

    def __init__(self, ctx: Context, source: str = "Item", route: str = "bone") -> None:
        """`source` is the modifier pool: "Item" for equipment, "Jewel" for jewels, "Desecrated" for Desecrated rows,
        of which `route` keeps one bone's (desecrated.route_of: "bone", "breach" or "cranium"; SEARCH31)."""
        self.ctx = ctx
        self.source = source
        self.route = route
        tier = rare_rules_tier(ctx.request.budget)  # (THE USER 22 Nov: no gear tier but SSF's; rare_rules_tier)
        self.item_level, self.best_tier, self.roll = TIER_RULES[tier]
        self.top_cap = BUY_RULES[tier]
        self._pools: dict[str, list[Affix]] = {}

    @cached_property
    def mods(self) -> list[dict]:
        return [m for m in self.ctx.data.mods.get(self.source, []) if m.get("type") in ("Prefix", "Suffix")]

    def pool(self, base_name: str, extra: tuple[str, ...] = ()) -> list[Affix]:
        """`extra`: tags a socketed influence rune adds (SEARCH30, influence_tag); their affixes are marked `influence`."""
        key = base_name if not extra else (base_name, tuple(sorted(extra)))
        if key not in self._pools:
            base = self.ctx.bases_by_name[base_name]
            tags = pool_tags(base) | set(extra)
            if self.source == "Desecrated" and self.route == "breach":
                # (SEARCH31) the Breach rows spawn by a tag no base carries: the Altered Collarbone's, jewellery only
                tags |= {desecrated.BREACH_TAG} if desecrated.family(base.get("type") or "") == "jewellery" else set()
            spawned: dict[str, str] = {}  # mod id -> the extra tag it spawns by
            groups: dict[tuple[str, str], list[dict]] = {}
            weights: dict[str, int] = {}  # spawn weight of each affix this base can roll at this item level
            totals = {"Prefix": 0, "Suffix": 0}
            for mod in self.mods:
                if self.source == "Desecrated" and desecrated.route_of(mod) != self.route:
                    continue  # (only this route's rows: desecrated.py)
                weight = 0
                for tag, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []):
                    if tag in tags:
                        weight = value
                        if tag in extra:
                            spawned[mod["id"]] = tag
                        break
                if weight > 0:
                    groups.setdefault((mod["group"], mod["type"]), []).append(mod)
                    if mod.get("level", 0) <= self.item_level:
                        weights[mod["id"]] = weight
                        totals[mod["type"]] += weight

            def odds(mod: dict) -> tuple[float, float]:
                weight = weights.get(mod["id"], 0)
                return weight / max(1, sum(totals.values())), weight / max(1, totals[mod["type"]])

            affixes = []
            for (group, kind), mods in groups.items():
                ranked = sorted(mods, key=lambda m: -m.get("level", 0))
                allowed = [(tier, mod) for tier, mod in enumerate(ranked, start=1)
                           if tier >= self.best_tier and mod.get("level", 0) <= self.item_level]
                if not allowed:
                    continue
                # The best tier allowed (lower tiers of a group never score higher), and the next one
                # down: cheaper, and the fallback when a rare can't take another tier-1 affix
                (tier, mod), lower = allowed[0], None
                if len(allowed) > 1:
                    lower = self.affix(allowed[1][1], group, kind, allowed[1][0], len(ranked), odds(allowed[1][1]))
                affix = self.affix(mod, group, kind, tier, len(ranked), odds(mod), lower)
                if self.source == "Desecrated":
                    lower = None if lower is None else dataclasses.replace(lower, desecrated=desecrated.label(allowed[1][1]))
                    affix = dataclasses.replace(affix, desecrated=desecrated.label(mod), lower=lower)
                if mod["id"] in spawned:
                    lower = None if lower is None else dataclasses.replace(lower, influence=spawned.get(lower.id, ""))
                    affix = dataclasses.replace(affix, influence=spawned[mod["id"]], lower=lower)
                affixes.append(affix)
            self._pools[key] = affixes
        return self._pools[key]

    def affix(self, mod: dict, group: str, kind: str, tier: int, tiers: int, odds: tuple[float, float],
              lower: Affix | None = None) -> Affix:
        return Affix(mod["id"], exclusion_group(group), kind, tier, tuple(roll(line, self.roll) for line in mod_lines(mod)), tiers, lower,
                     odds=odds[0], side_odds=odds[1], ranged=any(RANGE.search(line) for line in mod_lines(mod)),
                     adds=tuple(mod.get("tags") or ()), excluded_by=excluded_by(mod), level=int(mod.get("level") or 0))

    def choices(self, affix: Affix, chosen: list[Affix]) -> list[Affix]:
        """The tiers of the affix's group worth trying next: a tier-1 affix and its tier 2 (often
        nearly as strong for a fraction of the price, which the budget weight then decides), or only
        the tier 2 once the rare holds as many tier-1 affixes as rares people sell do. None when an
        affix already chosen forbids it: a caster weapon's spell-damage prefixes add tags such as
        "no_cold_spell_mods" that give the other elements' prefixes zero spawn weight, so a focus takes
        one element (a user's 2026-10-24 focus had fire, cold and lightning, which can't roll)."""
        if any(tag in affix.excluded_by for a in chosen for tag in a.adds):
            return []
        if not affix.top or affix.lower is None:
            return [affix]
        if self.top_cap is not None and sum(1 for a in chosen if a.top) >= self.top_cap:
            return [affix.lower]
        return [affix, affix.lower]

    def exalt_share(self, base_name: str, target: Affix, chosen: list[Affix]) -> float:
        """(SEARCHC3) One Exalted Orb with the side's Exaltation Omen on an item holding `chosen`: the chance it adds
        `target` at its tier or better. The side's rows this base rolls at this item level, less the groups the item
        already holds and rows its affixes' tags forbid. PoB's weights, which R33-23 found are 1 / 0 in the client: so
        equal weights per row (LOW; the real spawn weights can't be read from any source)."""
        key = (base_name, target.type)
        rows = self.__dict__.setdefault("_side_rows", {}).get(key)
        if rows is None:
            tags = pool_tags(self.ctx.bases_by_name[base_name])
            rows = []
            for mod in self.mods:
                if mod["type"] != target.type or mod.get("level", 0) > self.item_level:
                    continue
                weight = next((v for t, v in zip(mod.get("weightKey") or [], mod.get("weightVal") or []) if t in tags), 0)
                if weight > 0:
                    rows.append((mod["group"], int(mod.get("level") or 0), weight, excluded_by(mod)))
            self._side_rows[key] = rows
        taken = {a.group for a in chosen}
        forbidden = {tag for a in chosen for tag in a.adds}
        open_rows = [r for r in rows if r[0] not in taken and not forbidden & r[3]]
        total = sum(r[2] for r in open_rows)
        hit = sum(r[2] for r in open_rows if r[0] == target.group and r[1] >= target.level)
        return hit / total if total else 0.0

    def crafted(self, kind: str) -> tuple[Affix, float] | None:
        """For a jewel with three `kind` modifiers: the crafted one that boosts them, and the boost in
        percent (None if this game data lacks the crafts that make such a jewel)."""
        by_id = {m["id"]: m for m in self.mods}
        allowed, effect_mod = (by_id.get(mod_id) for mod_id in JEWEL_CRAFTS[kind])
        if self.source != "Jewel" or not allowed or not effect_mod:
            return None
        lines = tuple(roll(line, self.roll) for line in mod_lines(effect_mod))
        found = next((m for line in lines if (m := EFFECT.search(line))), None)
        if not found:
            return None
        return (Affix(effect_mod["id"], effect_mod["group"], effect_mod["type"], 1, lines, crafted=True, ranged=True),
                float(found.group(1)))


# An implicit that widens the base's mod pool (COV36; RESEARCH27's loreweave-grasping-mail.md, R27): Grasping Mail's
# "Can roll Ring Modifiers" is game Mods ItemCanAlsoRollRingMods, whose Tags are ring, genesis_tree_caster and
# genesis_tree_minion (Tags.json 2 / 1302 / 1303; build 26476662 and 26650308 agree). PoB keeps the line as implicit
# text only (its base tags: armour, body_armour, default, str_dex_int_armour), so the pool read only those and Grasping
# Mail rares never rolled a ring mod (adds to Attacks, cast speed, Mana, Accuracy, ...), a Genesis Tree caster mod
# (+1 to Level of all Spell Skills, spell damage) or a minion mod: an UNDERCOUNT. The tags join the base's; each mod's
# own spawn-weight order still decides (the first of its tags the item has: IncreasedEvasionRating lists body_armour 0
# before ring 1, so it stays out, as in the game). The only implicit of this kind in PoB's bases (checked: every
# base's implicit text for "Can roll" / "Modifiers": the four Grasping Mail rows; game Mods ids "CanAlsoRoll":
# ItemCanAlsoRollRingMods only).
IMPLICIT_POOL_TAGS = {"Can roll Ring Modifiers": ("ring", "genesis_tree_caster", "genesis_tree_minion")}


def pool_tags(base: dict) -> set[str]:
    """The tags a base's affixes spawn by: its own, plus those an implicit adds (IMPLICIT_POOL_TAGS)."""
    tags = set(base.get("tags") or [])
    for line in (base.get("implicit") or "").splitlines():
        tags.update(IMPLICIT_POOL_TAGS.get(line.strip(), ()))
    return tags


def excluded_by(mod: dict) -> frozenset[str]:
    """Tags that give the mod zero spawn weight. Only tags other affixes add matter here: the base's
    own tags are applied when the pool is built."""
    return frozenset(key for key, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []) if value == 0)


def conflicts(mods: list[dict]) -> list[tuple[dict, dict]]:
    """(blocked, blocker) pairs: a mod that couldn't roll because another one's tags forbid it."""
    return [(mod, other) for mod in mods for other in mods
            if other is not mod and set(other.get("tags") or ()) & excluded_by(mod)]


def implicit_lines(base: dict, fraction: float = 0.5, item_level: int | None = None) -> list[str]:
    implicit = implicit_variant(base.get("implicit"))  # (build_rare puts a chosen variant's line in, untagged)
    return [inherent_skill_line(line, item_level) or roll(line, fraction) for line in implicit.splitlines()] if implicit else []


# A base's inherent skill ("Grants Skill: Level (1-20) Chaos Bolt" on a Withered Wand; PATCH_CHECK K3-2, COV35): its level
# is set by the item's item level, not rolled (poe2wiki Granted skills: "The level of the granted skill ... is determined
# by the item's item level"; the official forum, Jan 2025: an item level 84 wand gave level 19; 0.3.0: "Items with
# inherent Skills can now rarely drop with Skill levels up to level 20 in high-tier Maps"). No game table gives the item
# level -> skill level steps (the export has none: granted_rolls.py's note), so they come from trade listings of
# item-granted skills by item level (granted_rolls.py's 10-11 Nov samples: 54 -> 13, 65 -> 14, 67 -> 15, 68 -> 16,
# 76 -> 17, 79-81 -> 18, 82-84 -> 18 or 19) and the forum's 84 -> 19, 90 -> 20, taking the lower level where the
# samples differ (MEDIUM). Before, the middle of the range (10 or 11) at every gear tier: an UNDERCOUNT at endgame.
# PoB still caps the level at what the character meets (getGrantedSkillLevel, 0.5.4's rule; granted_rolls.py). A Perfect
# Flux ("Upgrades the Skills on an item to Level 20", poe2db) on a bought rare isn't offered (no price read for it).
INHERENT_SKILL_LEVELS = ((54, 13), (65, 14), (67, 15), (68, 16), (76, 17), (79, 18), (84, 19), (90, 20))
INHERENT_SKILL = re.compile(r"^Grants Skill: Level \((\d+)-(\d+)\) (.+)$")


def inherent_skill_level(item_level: int) -> int | None:
    """The inherent skill level an item of this item level carries (INHERENT_SKILL_LEVELS), None below the table."""
    return max((level for at, level in INHERENT_SKILL_LEVELS if item_level >= at), default=None)


def inherent_skill_line(line: str, item_level: int | None) -> str | None:
    """A base's "Grants Skill: Level (a-b) X" line at the item level's skill level (clamped to a-b), or None (roll it)."""
    m = INHERENT_SKILL.match(line.strip())
    level = inherent_skill_level(item_level) if m and item_level else None
    if level is None:
        return None
    return f"Grants Skill: Level {min(max(level, int(m.group(1))), int(m.group(2)))} {m.group(3)}"


# Item quality (COV6, PLANS2's plans/kw-quality.md; the manager's ruling 12 Nov ~09:00): an item text with no "Quality:"
# line gets 0% quality in PoB (Item.lua NormaliseQuality), but at the user's 100-200 divine budget 20% (the default
# maximum, KeywordPopups MaximumQuality) is standard and cheap: Blacksmith's Whetstone (weapons), Arcanist's Etcher,
# Armourer's Scrap (armour, shields, foci), Glassblower's Bauble (flasks). So generated items of these base types say
# "Quality: 20" (labelled in the result). PoB's bases with a quality value, less Charm: PoB's Item.lua says "charms
# cannot be modified by quality currency" and poe2db's Glassblower's Bauble "Improves the quality of a flask" only
# (checked: PoB Data/Bases quality keys; poe2db Glassblowers_Bauble, 12 Nov 2026). Rings, amulets (catalyst quality:
# held for the user), belts, quivers, jewels and limbs have no quality in PoB's bases.
QUALITY = 20
QUALITY_TYPES = frozenset({
    "One Hand Axe", "Two Hand Axe", "Body Armour", "Boots", "Bow", "Claw", "Crossbow", "Dagger", "Fishing Rod", "Flail",
    "Flask", "Focus", "Gloves", "Helmet", "One Hand Mace", "Two Hand Mace", "Sceptre", "Shield", "Spear", "Staff",
    "One Hand Sword", "Two Hand Sword", "Talisman", "TrapTool", "Wand"})


def quality_lines(base: dict) -> list[str]:
    """["Quality: 20"] for a base PoB gives quality and the player can raise with currency (QUALITY_TYPES), else []."""
    return [f"Quality: {QUALITY}"] if base.get("type") in QUALITY_TYPES else []


def rare_text(slot: str, base: dict, item_level: int, affixes: list[Affix], runes: list[str] = ()) -> str:
    lines = ["Rarity: Rare", f"Optimizer {slot}", base["name"], f"Item Level: {item_level}", *quality_lines(base)]
    if base.get("subType") == "Radius":
        # Time-Lost jewels: PoB applies "in Radius" lines only with a radius (Small unless a mod
        # upgrades it), and a parsed item without this line has none
        lines.append("Radius: Small")
    lines += socket_lines(runes)
    implicits = implicit_lines(base, item_level=item_level)
    lines += [f"Implicits: {len(implicits)}", *implicits]
    for affix in affixes:
        lines += affix.lines
    return corrected_item("\n".join(lines))


def magic_text(slot: str, base: dict, item_level: int, affixes: list[Affix]) -> str:
    """A magic item (flasks and charms): one prefix and one suffix at most."""
    implicits = implicit_lines(base, item_level=item_level)
    lines = ["Rarity: Magic", base["name"], f"Item Level: {item_level}", *quality_lines(base),
             f"Implicits: {len(implicits)}", *implicits]
    for affix in affixes:
        lines += affix.lines
    return corrected_item("\n".join(lines))


def magic_price(slot: str, affixes: list[Affix]) -> float:
    """Magic flasks and charms are cheap: a flat estimate per affix (uncalibrated)."""
    return MAGIC_PRICE * max(1, len(affixes))


def socket_lines(runes: list[str]) -> list[str]:
    if not runes:
        return []
    return ["Sockets: " + " ".join("S" for _ in runes)] + [f"Rune: {name}" for name in runes]


def with_runes(raw: str, runes: list[str]) -> str:
    """An item's text with its sockets filled (any old socket lines replaced)."""
    lines = [l for l in raw.splitlines() if not l.startswith(("Sockets:", "Rune:"))]
    at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
    if at is None:
        at = next((i + 1 for i, l in enumerate(lines) if l.startswith("Item Level:")), None)
    if at is None:  # header: [Rarity,] [name,] base
        at = 2 if not lines[0].startswith("Rarity:") or lines[0] == "Rarity: Normal" else 3
        at = min(at, len(lines))
    return corrected_item("\n".join(lines[:at] + socket_lines(runes) + lines[at:]))  # the runes' ledger corrections


def rare_price(slot: str, affixes: list[Affix]) -> float:
    """Estimated price in divines (never 0), from this process's rare price model."""
    return rare_prices.active().price(slot, affixes)



# Void Flux (SEARCHC1; THE USER 23 Nov: "buy some cheap boots with triple resistances, use a void flux on them, and get all
# the chaos resistance you'd need on one item, AND still get movement speed"). RESEARCH50 R50-4 (HIGH): "Transforms all
# Fire, Cold and Lightning Resistance modifiers on an item to equivalent Chaos resistance modifiers" (poe2db Void_Flux),
# magic or rare items, not corrupted ones, fractured mods untouched; converted mods coexist (trade: three "of Exile" on one
# pair of boots). The tier map is poe2db's "Mod Equivalencies" table (read 23 Nov, 15 rows; the game tables we extract
# hold no flux table): each elemental range -> the chaos range below, the roll kept at the same fraction of its range.
# Not in the table, so NOT converted (UNVERIFIED either way): "to all Elemental Resistances" and two-element hybrids.
FLUX_SINGLE = ((6, 10, 4, 7), (11, 15, 8, 11), (16, 20, 12, 15), (21, 25, 16, 19), (26, 30, 16, 19), (31, 35, 20, 23),
               (36, 40, 20, 23), (41, 45, 24, 27))
FLUX_HYBRID = ((13, 17, 24, 27),)  # "+(13-17)% to Fire and Chaos Resistances" -> "+(24-27)% to Chaos Resistance"
FLUX_NAMES = {"Chaos": "Void Flux", "Fire": "Blazing Flux", "Cold": "Chilling Flux", "Lightning": "Crackling Flux"}
_FLUX_RES = re.compile(r"^\+(\d+)% to (Fire|Cold|Lightning) Resistance$")
_FLUX_HYBRID = re.compile(r"^\+(\d+)% to (Fire|Cold|Lightning) and Chaos Resistances$")
_FLUX_MAX = re.compile(r"^\+(\d+)% to Maximum (Fire|Cold|Lightning) Resistance$")
_ELEMENTAL_RES = re.compile(r"\b(Fire|Cold|Lightning)\b.*Resistance", re.I)


def _flux_value(value: int, table) -> int | None:
    for lo, hi, out_lo, out_hi in table:
        if lo <= value <= hi:
            return round(out_lo + (value - lo) / max(hi - lo, 1) * (out_hi - out_lo))
    return None


def void_flux_line(line: str) -> str | None:
    """The line a Void Flux makes of an elemental resistance line, the line itself if it isn't one, or None when it is
    an elemental resistance line the table doesn't cover (the item then isn't fluxed: no guess)."""
    if m := _FLUX_RES.match(line):
        value = _flux_value(int(m.group(1)), FLUX_SINGLE)
        return None if value is None else f"+{value}% to Chaos Resistance"
    if m := _FLUX_HYBRID.match(line):
        value = _flux_value(int(m.group(1)), FLUX_HYBRID)
        return None if value is None else f"+{value}% to Chaos Resistance"
    if m := _FLUX_MAX.match(line):
        return f"+{m.group(1)}% to Maximum Chaos Resistance"
    return None if _ELEMENTAL_RES.search(line) else line


def flux_line(line: str, target: str) -> str | None:
    """(SEARCHC2) The line after the Flux of `target` ("Chaos": Void Flux, rares.void_flux_line; "Fire" / "Cold" /
    "Lightning": Blazing / Chilling / Crackling Flux, "Transforms all <A> and <B> Resistance modifiers on an item to
    equivalent <C> Resistance modifiers", poe2db, read 23 Nov through a helper: the SAME 15-row table on all three pages,
    whose Fire / Cold / Lightning columns hold the same numbers in every row, so an elemental Flux changes only the element
    word: single resistances, "+N% to <E> and Chaos Resistances", "+N% to Maximum <E> Resistance"). The line itself where
    the Flux leaves it; None for an elemental resistance line outside the table (a two-element "Fire and Cold" line, no row:
    the item isn't fluxed, no guess)."""
    if target == "Chaos":
        return void_flux_line(line)
    for pattern in (_FLUX_RES, _FLUX_HYBRID, _FLUX_MAX):
        if m := pattern.match(line):
            value = int(m.group(1))
            if pattern is _FLUX_RES and _flux_value(value, FLUX_SINGLE) is None:
                return None
            if pattern is _FLUX_HYBRID and _flux_value(value, FLUX_HYBRID) is None:
                return None
            return line[:m.start(2)] + target + line[m.end(2):]
    return None if _ELEMENTAL_RES.search(line) and not re.search(r"\ball Elemental\b", line) else line


def fluxed(affixes: list[Affix], target: str) -> list[Affix] | None:
    """The affixes after the Flux of `target` (lines converted, ids marked), or None if nothing changes or a line can't."""
    out, changed = [], False
    for affix in affixes:
        lines = [flux_line(line, target) for line in affix.lines]
        if any(line is None for line in lines):
            return None
        if tuple(lines) != affix.lines:
            changed = True
            affix = dataclasses.replace(affix, id=f"{affix.id}+{FLUX_NAMES[target].replace(' ', '')}",
                                        lines=tuple(lines))
        out.append(affix)
    return out if changed else None


def void_fluxed(affixes: list[Affix]) -> list[Affix] | None:
    """The affixes after a Void Flux (their lines converted, ids marked), or None if nothing converts or a line can't."""
    out, changed = [], False
    for affix in affixes:
        lines = [void_flux_line(line) for line in affix.lines]
        if any(line is None for line in lines):
            return None
        if tuple(lines) != affix.lines:
            changed = True
            affix = dataclasses.replace(affix, id=f"{affix.id}+VoidFlux", lines=tuple(lines))
        out.append(affix)
    return out if changed else None


def is_elemental_resistance(affix: Affix) -> bool:
    """A single-element resistance affix (what a Void Flux turns into chaos resistance)."""
    return bool(affix.lines) and all(_FLUX_RES.match(line) for line in affix.lines)


# The Aldur runes (SEARCHC2; THE USER 23 Nov: "the breath/ire/passion/betrayal of aldur augments that change a weapon's
# elemental types around"; RESEARCH51 R51-10, the catalogue's #1 DPS lever). Each is socket-bound, rare weapons only
# (PoB ModRunes: "weapon" / "caster" slots), and "Transforms all <A> and <B> modifiers on the item into equivalent <C>
# modifiers" (Passion: Cold + Lightning -> Fire; Breath: Fire + Lightning -> Cold; Ire: Fire + Cold -> Lightning;
# Betrayal: Fire, Cold and Lightning -> Chaos). PoB has NO parse for that line (no ModCache entry; R51-10 HIGH), so the
# search rewrites the weapon's lines before PoB scores it. Converted mods coexist (an item with added Fire, Cold and
# Lightning, each its own group, ends with three added-Lightning lines under Ire), which no plain rare can roll.
# The map is poe2db's "Mod Equivalencies" table on the four rune pages (read 23 Nov through a helper, 78 rows, the same
# table on all four pages): one row per equivalence, a column per element. Rows whose numbers are the same in every column
# (damage gained as extra, % increased, +spell levels, penetration, the Runic Ward rows) only change the element word;
# the added-damage rows (one-hand 46-55, two-hand 56-65) and the Allies rows (66-74) and the ailment rows (41-45) change
# numbers too, matched by the source column's exact ranges, the roll kept at the same fraction (rares.roll).
# NOT converted (no table row): resistances (no resistance rows; R51-10), lines without an element word, and the base's
# IMPLICIT (the rune text doesn't say; R52-5 leans "not converted", MEDIUM-LOW, like Void Flux: UNVERIFIED, so implicits
# stay as they are). A damage line with an element word that matches no row makes its affix UNAVAILABLE to the route (no
# guess); an ailment row has no Chaos column (blank on poe2db): such an affix isn't offered under Betrayal.
ALDUR_RUNES = {"Fire": "Passion of Aldur", "Cold": "Breath of Aldur", "Lightning": "Ire of Aldur",
               "Chaos": "Betrayal of Aldur"}
ALDUR_SET = frozenset(ALDUR_RUNES.values())
ALDUR_ELEMENTS = ("Fire", "Cold", "Lightning", "Chaos")
# Families whose numbers are the same in every column (poe2db rows 1-40, 76-78): the allowed source ranges
ALDUR_SAME = {
    "extra": {(13, 15), (16, 18), (19, 21), (22, 24), (25, 27), (28, 30), (26, 30), (31, 36), (37, 42), (43, 48),
              (49, 54), (55, 60), (15, 20), (25, 33)},
    "increased": {(25, 34), (35, 44), (45, 54), (55, 64), (65, 74), (75, 89), (90, 104), (105, 119), (50, 68), (69, 88),
                  (89, 108), (109, 128), (129, 148), (149, 188), (189, 208), (209, 238)},
    "level": {(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (3, 4), (5, 6), (7, 7)},
    "penetration": {(15, 25)},
    "runic_ward": {(21, 26), (42, 52)},
}
# Rows with per-element numbers: ((low lo, low hi), (high lo, high hi)) per column Fire, Cold, Lightning, Chaos
_R = lambda a, b, c, d: ((a, b), (c, d))  # noqa: E731
ALDUR_ADDS = (  # rows 46-65: "Adds (a-b) to (c-d) <E> Damage" (one-hand 46-55, two-hand 56-65)
    (_R(1, 2, 3, 5), _R(1, 2, 3, 4), _R(1, 1, 4, 6), _R(1, 3, 3, 5)),
    (_R(4, 6, 7, 10), _R(3, 5, 6, 9), _R(1, 1, 13, 19), _R(3, 5, 7, 11)),
    (_R(7, 11, 13, 19), _R(6, 9, 10, 16), _R(1, 2, 20, 30), _R(5, 11, 13, 19)),
    (_R(13, 19, 21, 29), _R(11, 15, 17, 24), _R(1, 2, 36, 52), _R(11, 19, 23, 29)),
    (_R(20, 24, 32, 37), _R(17, 20, 26, 32), _R(1, 3, 55, 60), _R(19, 23, 31, 37)),
    (_R(25, 33, 38, 54), _R(22, 29, 34, 44), _R(1, 4, 63, 82), _R(23, 31, 41, 53)),
    (_R(35, 44, 56, 71), _R(31, 38, 47, 59), _R(1, 6, 85, 107), _R(31, 43, 59, 71)),
    (_R(47, 59, 74, 97), _R(40, 53, 62, 80), _R(1, 8, 111, 152), _R(43, 59, 73, 97)),
    (_R(62, 85, 101, 129), _R(55, 69, 83, 106), _R(1, 10, 157, 196), _R(59, 83, 101, 131)),
    (_R(88, 101, 133, 154), _R(72, 81, 110, 123), _R(1, 12, 202, 234), _R(83, 101, 137, 157)),
    (_R(2, 4, 5, 7), _R(2, 3, 4, 6), _R(1, 1, 7, 10), _R(2, 3, 5, 7)),
    (_R(6, 9, 10, 16), _R(5, 8, 9, 14), _R(1, 2, 19, 27), _R(5, 9, 11, 17)),
    (_R(11, 17, 19, 28), _R(10, 14, 15, 23), _R(1, 3, 31, 43), _R(11, 17, 19, 29)),
    (_R(19, 27, 30, 42), _R(16, 23, 25, 35), _R(1, 4, 53, 76), _R(19, 29, 31, 41)),
    (_R(30, 37, 45, 56), _R(25, 30, 38, 46), _R(1, 4, 80, 88), _R(31, 37, 43, 53)),
    (_R(39, 53, 59, 80), _R(32, 43, 49, 66), _R(1, 6, 93, 122), _R(41, 53, 59, 79)),
    (_R(56, 70, 84, 107), _R(46, 57, 70, 88), _R(1, 8, 128, 162), _R(59, 71, 83, 107)),
    (_R(73, 97, 112, 149), _R(60, 80, 92, 121), _R(1, 13, 168, 231), _R(73, 97, 113, 149)),
    (_R(102, 130, 155, 198), _R(84, 107, 126, 161), _R(1, 16, 239, 300), _R(101, 131, 151, 197)),
    (_R(135, 156, 205, 236), _R(112, 124, 168, 189), _R(1, 19, 310, 358), _R(137, 157, 199, 239)),
)
ALDUR_ALLIES = (  # rows 66-74: "Allies in your Presence deal (a-b) to (c-d) added Attack <E> Damage"
    (_R(1, 2, 3, 4), _R(1, 2, 3, 4), _R(1, 1, 5, 7), _R(1, 2, 3, 5)),
    (_R(3, 5, 6, 9), _R(3, 4, 5, 8), _R(1, 1, 10, 15), _R(3, 5, 6, 7)),
    (_R(6, 8, 10, 13), _R(5, 6, 9, 11), _R(1, 1, 16, 22), _R(6, 7, 11, 13)),
    (_R(9, 11, 14, 17), _R(7, 8, 12, 14), _R(1, 1, 23, 27), _R(8, 11, 14, 17)),
    (_R(12, 13, 18, 20), _R(9, 10, 15, 17), _R(1, 1, 28, 32), _R(12, 13, 19, 23)),
    (_R(14, 16, 21, 26), _R(11, 13, 18, 21), _R(1, 2, 33, 40), _R(14, 17, 21, 29)),
    (_R(17, 19, 27, 30), _R(14, 15, 22, 24), _R(1, 2, 41, 47), _R(17, 19, 30, 31)),
    (_R(20, 24, 31, 38), _R(16, 20, 25, 31), _R(1, 3, 48, 59), _R(20, 23, 32, 37)),
    (_R(25, 29, 39, 45), _R(21, 24, 32, 37), _R(1, 4, 60, 71), _R(24, 29, 38, 47)),
)
del _R
# Rows 41-45: the caster suffixes, Fire "increased Flammability Magnitude", Cold "increased Freeze Buildup", Lightning
# "increased chance to Shock"; the Chaos column is blank (no equivalent: not converted under Betrayal, no guess)
ALDUR_AILMENT_TEXT = {"Fire": "increased Flammability Magnitude", "Cold": "increased Freeze Buildup",
                      "Lightning": "increased chance to Shock"}
ALDUR_AILMENT = ((51, 60, 31, 40, 51, 60), (61, 70, 41, 50, 61, 70), (71, 80, 51, 60, 71, 80),
                 (81, 90, 61, 70, 81, 90), (91, 100, 71, 80, 91, 100))
_NUM = r"(\d+|\(\d+-\d+\))"
_ALDUR_ADDS = re.compile(rf"^Adds {_NUM} to {_NUM} (Fire|Cold|Lightning) Damage$")
_ALDUR_ALLIES = re.compile(rf"^Allies in your Presence deal {_NUM} to {_NUM} added Attack (Fire|Cold|Lightning) Damage$")
_ALDUR_AILMENT = re.compile(r"^\((\d+)-(\d+)\)% (increased Flammability Magnitude|increased Freeze Buildup|"
                            r"increased chance to Shock)$")
_ALDUR_SAME = (
    ("extra", re.compile(rf"^Gain {_NUM}% of Damage as Extra (Fire|Cold|Lightning) Damage$")),
    ("runic_ward", re.compile(rf"^Gain {_NUM}% of Damage as Extra (Fire|Cold|Lightning) Damage while you are missing "
                              r"Runic Ward$")),
    ("increased", re.compile(rf"^{_NUM}% increased (Fire|Cold|Lightning) Damage$")),
    ("level", re.compile(rf"^\+{_NUM} to Level of all (Fire|Cold|Lightning) Spell Skills$")),
    ("penetration", re.compile(rf"^Damage Penetrates {_NUM}% (Fire|Cold|Lightning) Resistance$")),
)
_ALDUR_ELEMENT = re.compile(r"\b(Fire|Cold|Lightning)\b")


def _aldur_span(token: str) -> tuple[int, int]:
    if token.startswith("("):
        lo, hi = token[1:-1].split("-")
        return int(lo), int(hi)
    return int(token), int(token)


def _aldur_token(span: tuple[int, int]) -> str:
    return str(span[0]) if span[0] == span[1] else f"({span[0]}-{span[1]})"


def aldur_line(line: str, target: str) -> str | None:
    """The line in its RANGE form (the mod's "(a-b)" text) after the Aldur rune of `target` (Fire / Cold / Lightning /
    Chaos), the line itself where the rune leaves it (no element word, the target's own element, resistances), or None
    for an elemental damage line poe2db's table has no row for (its affix then isn't offered: no guess)."""
    if m := _ALDUR_AILMENT.match(line):  # (no element word: the ailment names the element)
        source = next(e for e, t in ALDUR_AILMENT_TEXT.items() if t == m.group(3))
        if source == target:
            return line
        if target == "Chaos":
            return None
        span, col, out = (int(m.group(1)), int(m.group(2))), ("Fire", "Cold", "Lightning").index(source), \
            ("Fire", "Cold", "Lightning").index(target)
        row = next((r for r in ALDUR_AILMENT if (r[2 * col], r[2 * col + 1]) == span), None)
        return None if row is None else f"({row[2 * out]}-{row[2 * out + 1]})% {ALDUR_AILMENT_TEXT[target]}"
    found = _ALDUR_ELEMENT.search(line)
    if found is None or found.group(1) == target:
        return line
    if m := _ALDUR_ADDS.match(line) or _ALDUR_ALLIES.match(line):
        source, rows = m.group(3), ALDUR_ADDS if line.startswith("Adds") else ALDUR_ALLIES
        spans = (_aldur_span(m.group(1)), _aldur_span(m.group(2)))
        col, out = ALDUR_ELEMENTS.index(source), ALDUR_ELEMENTS.index(target)
        row = next((r for r in rows if r[col] == spans), None)
        if row is None:
            return None
        low, high = row[out]
        return line[:m.start(1)] + _aldur_token(low) + line[m.end(1):m.start(2)] + _aldur_token(high) + \
            line[m.end(2):m.start(3)] + target + line[m.end(3):]
    for family, pattern in _ALDUR_SAME:
        if (m := pattern.match(line)) and _aldur_span(m.group(1)) in ALDUR_SAME[family]:
            return line[:m.start(2)] + target + line[m.end(2):]
    if "Resistance" in line and "Penetrates" not in line:  # (resistances: no rows; Void Flux's job)
        return line
    return None if "Damage" in line else line


# Catalysts (SEARCHC2; RESEARCH51 R51-10 #3, R52-5): quality on a Ring or Amulet that raises its modifiers carrying the
# catalyst's tag, x(100 + quality)% (PoB Classes/Item.lua getCatalystScalar: the tag lists below are PoB's catalystTags;
# the game file text says only "<kind> modifiers": PoB's tags taken, MEDIUM). PoB applies it only to lines that carry their
# mod's tags ("{tags:...}" prefixes) on an item with "Catalyst:" / "CatalystQuality:" lines, which the search's text had
# neither of: catalysed_text writes both. 20% quality = CATALYSTS_PER_20 catalysts (R52-5: no per-use number anywhere;
# 1% each at item level 75+, LOW-MEDIUM). The +20% maximum (40%: Essence of the Breach / a Breach Ring) isn't offered
# yet (the Essence removes a random modifier: an odds route).
CATALYSTS = {  # exchange name -> (PoB's name, the tags it raises)
    "Flesh Catalyst": ("Flesh", ("life",)), "Neural Catalyst": ("Neural", ("mana",)),
    "Carapace Catalyst": ("Carapace", ("defences", "armour", "evasion", "energyshield")),
    "Uul-Netol's Catalyst": ("Uul-Netol's", ("physical",)), "Xoph's Catalyst": ("Xoph's", ("fire",)),
    "Tul's Catalyst": ("Tul's", ("cold",)), "Esh's Catalyst": ("Esh's", ("lightning",)),
    "Chayula's Catalyst": ("Chayula's", ("chaos",)), "Reaver Catalyst": ("Reaver", ("attack",)),
    "Sibilant Catalyst": ("Sibilant", ("caster",)), "Skittering Catalyst": ("Skittering", ("speed",)),
    "Adaptive Catalyst": ("Adaptive", ("attribute",)), "Necrotic Catalyst": ("Necrotic", ("minion",)),
}
CATALYST_TYPES = frozenset({"Ring", "Amulet"})
CATALYST_QUALITY = 20
CATALYSTS_PER_20 = 20  # (LOW-MEDIUM, R52-5)


def catalyst_matches(catalyst: str, affix: Affix, tags_by_id: dict[str, tuple[str, ...]]) -> bool:
    return bool(set(CATALYSTS[catalyst][1]) & set(tags_by_id.get(affix.id.split("+")[0], ())))


def catalysed_text(text: str, affixes: list[Affix], tags_by_id: dict[str, tuple[str, ...]], catalyst: str,
                   quality: int = CATALYST_QUALITY) -> str:
    """An item text (rare_text) with `quality`% of the catalyst: the Catalyst lines after "Item Level:" and each affix
    line prefixed with its mod's tags, so PoB scales the matching ones (the affix lines are found after "Implicits:")."""
    lines = text.split("\n")
    at = next(i for i, l in enumerate(lines) if l.startswith("Item Level:")) + 1
    lines[at:at] = [f"Catalyst: {CATALYSTS[catalyst][0]}", f"CatalystQuality: {quality}"]
    start = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), at)
    start += 1 + (int(lines[start].split(":", 1)[1]) if lines[start].startswith("Implicits:") else 0)
    done: set[int] = set()
    for affix in affixes:
        tags = tags_by_id.get(affix.id.split("+")[0])
        if not tags:
            continue
        for line in affix.lines:
            i = next((j for j in range(start, len(lines)) if j not in done and lines[j] == line), None)
            if i is not None:
                lines[i] = "{tags:" + ",".join(tags) + "}" + line
                done.add(i)
    return "\n".join(lines)


class AldurConverter:
    """Each pool affix's lines after an Aldur rune, from the mod's range text (ctx.data.mods), rolled at the pools'
    fraction like the affix itself."""

    def __init__(self, ctx: Context, fraction: float) -> None:
        self.fraction = fraction
        self.ranges = {m["id"]: mod_lines(m) for m in ctx.data.mods.get("Item", [])}
        self._memo: dict[tuple[str, str], Affix | None] = {}

    def convert(self, affix: Affix, target: str) -> Affix | None:
        """The affix after the rune (id marked), the affix itself when the rune leaves it as it is, or None when a line
        can't be converted (or the mod's range text is unknown and a line has an element word)."""
        key = (affix.id, target)
        if key not in self._memo:
            ranges = self.ranges.get(affix.id)
            if ranges is None or len(ranges) != len(affix.lines):
                self._memo[key] = affix if not any(_ALDUR_ELEMENT.search(x) for x in affix.lines) else None
            else:
                lines = [aldur_line(x, target) for x in ranges]
                if any(x is None for x in lines):
                    self._memo[key] = None
                elif lines == ranges:
                    self._memo[key] = affix
                else:
                    self._memo[key] = dataclasses.replace(
                        affix, id=f"{affix.id}+{ALDUR_RUNES[target].split()[0]}", lower=None,
                        lines=tuple(roll(x, self.fraction) for x in lines))
        return self._memo[key]


def aldur_weapon(base: dict) -> bool:
    """A weapon an Aldur rune can go in (rare weapons only: martial and caster weapons; not a Shield / Focus / Quiver,
    whose rune categories include "caster" for other augments)."""
    return base["type"] not in ("Shield", "Focus", "Quiver", "Amulet", "Ring", "Belt", "Helmet", "Body Armour", "Gloves",
                                "Boots", "Jewel", "Flask", "Charm") and bool(rune_categories(base) & {"weapon", "caster"})


# Sockets ------------------------------------------------------------------------------------

# A base whose implicit fixes its sockets (COV40; an UNDERCOUNT before: the socketLimit - 2 rule gave Kalguuran Forgehammer
# and Runeforged Kalguuran Forgehammer 1 rune socket, though the base "Has 3 Sockets"; PoB parses the implicit but no
# calculation reads it). The carriers (checked by stat, the manager 23:00: game 26650308 Stats local_has_X_sockets is on
# two Mods, MaceImplicitHasXSockets and KeeperOfTheArcVerisiumImplicit3Sockets1; BaseItemTypes.Implicit_Mods puts them
# on Kalguuran Forgehammer, the four Runeforged Kalguuran Forgehammer rows FourOneHandMace14Verisium1-4 and Runemastered
# Spiritbone Crown FourHelmetStrInt6VerisiumUnique1; second source: poe2db Kalguuran_Forgehammer "Has 3 Sockets", drop
# level 47, and Keeper_of_the_Arc's Runeforging "/3" rows). The crown's row is no row of PoB's (it keeps another of the
# three under one key), so it reaches socket_count as a copy's implicit line (base_variants.ROWS "Keeper of the Arc").
BASE_SOCKETS = re.compile(r"^Has (\d+) Sockets?$", re.M)


def socket_count(base: dict, unique: bool, raw: str | None = None) -> int:
    """Rune sockets an item gets without corruption: two on body armours and two-handers, one
    elsewhere on items that take sockets at all. A unique's own fixed sockets (`raw`, below) win."""
    if raw is not None and (own := own_sockets(base, raw)) is not None:
        return own
    if raw is not None and (m := next((m for line in active_lines(raw) if (m := BASE_SOCKETS.match(line.strip()))),
                                      None)):
        return int(m.group(1))  # (a copy's row implicit: Runemastered Keeper of the Arc's "Has 3 Sockets" row)
    if m := BASE_SOCKETS.search(base.get("implicit") or ""):
        return int(m.group(1))
    if base.get("type") in ("Amulet", "Ring", "Belt") and any(
            AS_THOUGH.match(line.strip()) for line in (base.get("implicit") or "").splitlines()):
        # (SEARCHC1; RESEARCH50 R50-1 5, MEDIUM-HIGH: trade 1,468 / 406 / 78 one-socket listings, none with 2; poe2wiki "1
        # Augment Socket (Hidden)"; game Mods AmuletImplicitHelmetSocket1 / RingImplicitGloveSocket /
        # BeltImplicitBootsSocket1) the bases Corona Amulet, Grasping Ring and Stalking Belt (where they drop: UNVERIFIED,
        # R50-1's turn-3 correction; no other non-unique amulet / ring / belt has one, by trade) have one augment
        # socket, which PoB's base data doesn't give (no socketLimit), so it counted 0: an UNDERCOUNT. Its augment counts
        # as a Helmet's / Gloves' / Boots' (augment_types)
        return 1
    limit = base.get("socketLimit")
    if not limit:
        return 0
    return max(limit - 2, 0)


# A unique's own socket rules (COV28; AUDIT33 A33-2's scan: PoB parses these lines into the flags SocketedRunesOnly,
# SocketedSoulCoresOnly and NoSockets and reads them only in ItemsTab's socket menu, ItemsTab.lua 2290-2303, so a filler
# that ignores them puts any augment in). Game stats local_can_only_socket_runes / _soul_cores / local_has_no_sockets
# (Mods UniqueOnlySocketRunes1, UniqueOnlySocketSoulCores1, HasNoSockets).
ONLY_AUGMENT = {"Only Runes can be Socketed in this item": "Rune",
                "Only Soul Cores can be Socketed in this item": "SoulCore"}
TAGS = re.compile(r"^(?:\{[^}]*\})*")
# Uniques whose fixed sockets are augment sockets, each checked on poe2db (14 Nov 2026: "Has N Augment Sockets
# (Hidden)", Serle's Grit "Has 3 Sockets") against PoB's text ("Sockets:" line or "Has N Augment Sockets"). Not a rule for
# every "Sockets:" line: Tabula Rasa's 6 are "Jewel Sockets (Hidden)" (poe2db), and the rings' (Eshtera's Path, Safrin's
# Resolve, Zaida's Longevity) hold jewels. Greymake's and The Bringer of Rain's 4 are their current variants' (PoB:
# {variant:2} / {variant:3}, the variant selected).
AUGMENT_SOCKETS = {"Atziri's Splendour": 6, "Runeseeker's Call": 5, "Morior Invictus": 4, "Darkness Enthroned": 2,
                   "Serle's Grit": 3, "Greymake": 4, "The Bringer of Rain": 4}
# "This item gains bonuses from Socketed Items as though it was a <type>" (Darkness Enthroned, The Bringer of Rain: every
# augment counts for that type) and "... from Socketed Soul Cores as though it was also <type>" (Atziri's Splendour: Soul
# Cores count for that type too). PoB reads the text itself (Classes/Item.lua 1279 / 1280; GetSocketedAugmentTypes 2387
# returns "armour", <type>; 1527 applies a Soul Core's <type> line), so the filler must offer what those types take.
AS_THOUGH = re.compile(r"^This item gains bonuses from Socketed Items as though it was a? ?(.+)$", re.I)
AS_THOUGH_CORE = re.compile(r"^This item gains bonuses from Socketed Soul Cores as though it was also a? ?(.+)$", re.I)
VARIANT_TAG = re.compile(r"\{(variant|group|base):([\d,]+)\}")


def active_lines(raw: str) -> list[str]:
    """The item text's lines PoB applies, tags stripped: PoB's variant rule (Classes/Item.lua CheckModLineVariant) for
    "{variant:..}", "{group:..}" and "{base:..}" tags, with PoB's default (the last variant) when none is selected."""
    lines = raw.splitlines()
    variants = sum(1 for l in lines if l.startswith("Variant:"))
    selected = next((int(l.split(":")[1]) for l in lines if l.startswith("Selected Variant:")), variants or None)
    groups = dict(tuple(int(x) for x in l.split(":")[1].split("=")) for l in lines
                  if l.startswith("Selected Variant Group:"))
    base = next((int(l.split(":")[1]) for l in lines if l.startswith("Selected Base Variant:")), None)
    found = []
    for line in lines:
        tags = {k: {int(x) for x in v.split(",")} for k, v in VARIANT_TAG.findall(TAGS.match(line).group(0))}
        if "base" in tags and base is not None and base not in tags["base"]:
            continue
        if "group" in tags:
            if not any(groups.get(g) in tags.get("variant", set()) for g in tags["group"]):
                continue
        elif "variant" in tags and selected is not None and selected not in tags["variant"]:
            continue
        found.append(TAGS.sub("", line))
    return found


def augment_types(raw: str | None) -> tuple[str | None, set[str]]:
    """(the type every augment counts for, or None; extra types Soul Cores count for) from the item's applied lines."""
    if not raw:
        return None, set()
    override, cores = None, set()
    for line in active_lines(raw):
        if m := AS_THOUGH.match(line):
            override = m.group(1).strip().lower()
        elif m := AS_THOUGH_CORE.match(line):
            cores.add(m.group(1).strip().lower())
    return override, cores


def _lines(raw: str) -> list[str]:
    return [TAGS.sub("", line) for line in raw.splitlines()]


def own_sockets(base: dict, raw: str) -> int | None:
    """A unique's fixed augment sockets (AUGMENT_SOCKETS), 0 on "Has no Sockets", else None (the base's count).
    Before COV28 the filler erased them and gave the base's count (Atziri's Splendour 2, Runeseeker's Call 1,
    Morior Invictus 2: an UNDERCOUNT)."""
    lines = _lines(raw)
    if "Has no Sockets" in lines:
        return 0
    name = lines[1] if len(lines) > 1 and lines[0].startswith("Rarity:") else lines[0] if lines else ""
    return AUGMENT_SOCKETS.get(name)


_UNIQUE_TEXTS: dict[int, dict[str, str]] = {}


def unique_text(ctx: Context, option: ItemOption) -> str | None:
    """A unique option's catalogue text, for its fixed sockets and socket rules (None for other items). Not the
    option's own text: once filled, that holds only as many sockets as runes were bought."""
    if not option.unique:
        return None
    texts = _UNIQUE_TEXTS.get(id(ctx.data))
    if texts is None:
        texts = _UNIQUE_TEXTS[id(ctx.data)] = {}
        for unique in ctx.data.uniques:
            texts.setdefault(unique["name"], unique["raw"])
    return texts.get(option.name)


def only_augment(raw: str | None) -> str | None:
    """The one augment type a unique takes ("Rune" / "SoulCore"), or None."""
    if not raw:
        return None
    return next((ONLY_AUGMENT[l] for l in _lines(raw) if l in ONLY_AUGMENT), None)


def rune_categories(base: dict) -> set[str]:
    kind = base["type"]
    sub = base.get("subType")
    if kind in ("Amulet", "Ring", "Belt"):  # (SEARCHC1: a Genesis Tree base's socket counts as its implicit's type)
        override = next((m.group(1).strip().lower() for line in (base.get("implicit") or "").splitlines()
                         if (m := AS_THOUGH.match(line.strip()))), None)
        return {"armour", override} if override else {kind.lower()}
    if kind in ("Helmet", "Body Armour", "Gloves", "Boots"):
        return {"armour", kind.lower()}
    if kind in ("Shield", "Focus"):
        return {"armour", kind.lower(), "buckler", "caster"}
    if kind in ("Wand", "Sceptre") or (kind == "Staff" and not sub):
        return {"caster", kind.lower(), "staff" if kind == "Staff" else kind.lower(), "martial weapon wand or staff"}
    name = "quarterstaff" if kind == "Staff" else kind.lower()
    return {"weapon", name, "martial weapon wand or staff"}


# Augments that can't go into a Corrupted or Sanctified item (H5-1, PATCH_CHECK; SEARCH22): the game files' SoulCores
# rows with CanSocketInCorruptedSanctified false (build 26476662: these 15 of 313 rows). Second source: patch 0.5.4
# (2026-06-25, no later line): "The following Augments can no longer be socketed into Augment Sockets on Corrupted or
# Sanctified items ...", naming nine of them (Astrid's, Katla's, Kolr's, Medved's, the Raven-Touched Shard, Serle's,
# Thrud's, Uhtred's, Vorana's); the game files add Cadigan's Epiphany and the five Aldur augments (both written down: the
# game files are taken). Before, a cultivated unique (Corrupted, cultivated.py) could get one in a socket (an OVERCOUNT).
# Only filling a socket of an item already sealed is blocked: one socketed before the Vaal Orb stays (the note's wording,
# "can no longer be socketed into"); the search doesn't try that order (socket, then corrupt and cultivate): LOW.
NOT_IN_SEALED = frozenset({
    "Raven-Touched Shard", "Passion of Aldur", "Breath of Aldur", "Ire of Aldur", "Betrayal of Aldur", "Serle's Triumph",
    "Cadigan's Epiphany", "Astrid's Creativity", "Uhtred's Sidereus", "Kolr's Hunt", "Vorana's Carnage", "Thrud's Might",
    "Medved's Tending", "Katla's Gloom", "Aldur's Legacy"})
SEALED_ITEM = re.compile(r"^(?:Corrupted|Sanctified)\s*$", re.M)


class RuneCatalog:
    def __init__(self, ctx: Context, prices: dict[str, float]) -> None:
        self.ctx = ctx
        self.prices = prices

    @cached_property
    def limits(self) -> dict[str, tuple[str, int]]:
        found = {}
        for rune in self.ctx.data.runes:
            for entry in rune["slots"].values():
                if isinstance(entry, dict) and entry.get("limit"):
                    found[rune["name"]] = (entry.get("limitId") or rune["name"], int(entry["limit"]))
                    break
        return found

    @cached_property
    def socket_bound(self) -> frozenset[str]:
        """(SEARCHC2) augments that can't be removed once socketed (PoB isSocketBound: 17 in runes.json, the Aldur
        runes, influence runes, Serle's / Astrid's kin, the Raven-Touched Shard, Atziri's Soul Cores, Cadigan's)"""
        return frozenset(r["name"] for r in self.ctx.data.runes
                         if any(isinstance(e, dict) and e.get("isSocketBound") for e in r["slots"].values()))

    def limit(self, name: str) -> tuple[str, int] | None:
        """(limit group, how many a character may use) for runes with a per-character limit."""
        return self.limits.get(name)

    def price(self, name: str) -> float | None:
        if self.ctx.ssf:
            return 0.0
        price = self.prices.get(name)
        return price if price is not None and price <= self.ctx.price_cap else None

    def candidates(self, base: dict, unique: bool, raw: str | None = None) -> list[str]:
        """Augments for the item's sockets. `raw`, the item's text, carries a unique's socket rules (only_augment,
        augment_types); jewellery takes only augments marked canSocketInJewellery (ItemsTab.lua 2303)."""
        override, cores = augment_types(raw)
        categories = {"armour", override} if override else rune_categories(base)
        only = only_augment(raw)  # "Only Runes / Soul Cores can be Socketed in this item"
        jewellery = base["type"] in ("Ring", "Amulet", "Belt")
        sealed = bool(raw and SEALED_ITEM.search(raw))  # (Corrupted or Sanctified: NOT_IN_SEALED stay out)
        names = []
        for rune in self.ctx.data.runes:
            if rune["name"] in self.ctx.compiled.filters.forbidden_augments or (sealed and rune["name"] in NOT_IN_SEALED):
                continue
            if rune["name"] in ALDUR_SET:  # (SEARCHC2: an Aldur rune only through Gear.aldur_trial, which rewrites the
                continue                   # weapon's lines; in a plain fill PoB would score its transform as nothing)
            slots = rune["slots"]
            entries = [slots[c] for c in categories if c in slots]
            entries += [slots[c] for c in cores - categories if c in slots and slots[c].get("type") == "SoulCore"]
            if not entries:
                continue
            if only and not any(e.get("type") == only for e in entries):
                continue
            if jewellery and not any(e.get("canSocketInJewellery") for e in entries):
                continue
            if unique and not any(e.get("canSocketInUniqueItems") for e in entries):
                continue
            if any((e.get("levelReq") or 0) > self.ctx.level for e in entries):
                continue
            if self.price(rune["name"]) is not None:
                names.append(rune["name"])
        return names


__all__ = ["AffixPools", "Affix", "RuneCatalog", "rare_text", "rare_price", "with_runes", "socket_count", "own_sockets", "only_augment", "unique_text", "active_lines", "augment_types", "boosted",
           "implicit_lines", "TIER_RULES", "SCREEN_KEEP", "RUNE_KEEP", "ItemOption"]
