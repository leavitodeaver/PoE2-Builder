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

import re
from dataclasses import dataclass
from functools import cached_property

from builder.data import rare_prices
from builder.ledger.items import corrected_item  # ledger translations on affix, implicit and rune lines (COV2)
from builder.request.schema import GearTier
from builder.search.context import Context, ItemOption, implicit_variant

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

    @property
    def top(self) -> bool:
        """A tier-1 affix of a group with lower tiers: the scarce kind on the trade site."""
        return self.tier == 1 and self.tiers > 1


class AffixPools:
    """Which affixes each base can roll at this request's gear tier."""

    def __init__(self, ctx: Context, source: str = "Item") -> None:
        """`source` is the modifier pool: "Item" for equipment, "Jewel" for jewels."""
        self.ctx = ctx
        self.source = source
        self.item_level, self.best_tier, self.roll = TIER_RULES[GearTier(ctx.request.budget.gear_tier)]
        self.top_cap = BUY_RULES[GearTier(ctx.request.budget.gear_tier)]
        self._pools: dict[str, list[Affix]] = {}

    @cached_property
    def mods(self) -> list[dict]:
        return [m for m in self.ctx.data.mods.get(self.source, []) if m.get("type") in ("Prefix", "Suffix")]

    def pool(self, base_name: str) -> list[Affix]:
        if base_name not in self._pools:
            tags = set(self.ctx.bases_by_name[base_name].get("tags") or [])
            groups: dict[tuple[str, str], list[dict]] = {}
            weights: dict[str, int] = {}  # spawn weight of each affix this base can roll at this item level
            totals = {"Prefix": 0, "Suffix": 0}
            for mod in self.mods:
                weight = 0
                for key, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []):
                    if key in tags:
                        weight = value
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
                affixes.append(self.affix(mod, group, kind, tier, len(ranked), odds(mod), lower))
            self._pools[base_name] = affixes
        return self._pools[base_name]

    def affix(self, mod: dict, group: str, kind: str, tier: int, tiers: int, odds: tuple[float, float],
              lower: Affix | None = None) -> Affix:
        return Affix(mod["id"], group, kind, tier, tuple(roll(line, self.roll) for line in mod_lines(mod)), tiers, lower,
                     odds=odds[0], side_odds=odds[1], ranged=any(RANGE.search(line) for line in mod_lines(mod)),
                     adds=tuple(mod.get("tags") or ()), excluded_by=excluded_by(mod))

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


def excluded_by(mod: dict) -> frozenset[str]:
    """Tags that give the mod zero spawn weight. Only tags other affixes add matter here: the base's
    own tags are applied when the pool is built."""
    return frozenset(key for key, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []) if value == 0)


def conflicts(mods: list[dict]) -> list[tuple[dict, dict]]:
    """(blocked, blocker) pairs: a mod that couldn't roll because another one's tags forbid it."""
    return [(mod, other) for mod in mods for other in mods
            if other is not mod and set(other.get("tags") or ()) & excluded_by(mod)]


def implicit_lines(base: dict, fraction: float = 0.5) -> list[str]:
    implicit = implicit_variant(base.get("implicit"))  # (build_rare puts a chosen variant's line in, untagged)
    return [roll(line, fraction) for line in implicit.splitlines()] if implicit else []


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
    implicits = implicit_lines(base)
    lines += [f"Implicits: {len(implicits)}", *implicits]
    for affix in affixes:
        lines += affix.lines
    return corrected_item("\n".join(lines))


def magic_text(slot: str, base: dict, item_level: int, affixes: list[Affix]) -> str:
    """A magic item (flasks and charms): one prefix and one suffix at most."""
    implicits = implicit_lines(base)
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


# Sockets ------------------------------------------------------------------------------------

def socket_count(base: dict, unique: bool, raw: str | None = None) -> int:
    """Rune sockets an item gets without corruption: two on body armours and two-handers, one
    elsewhere on items that take sockets at all. A unique's own fixed sockets (`raw`, below) win."""
    if raw is not None and (own := own_sockets(base, raw)) is not None:
        return own
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
