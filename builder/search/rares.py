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
from builder.request.schema import GearTier
from builder.search.context import Context, ItemOption

# (item level, best affix tier allowed: 1 = top, roll within each range 0..1) per gear tier
TIER_RULES = {
    GearTier.LEAGUE_START: (68, 4, 0.3),
    GearTier.MID: (78, 2, 0.5),
    GearTier.ENDGAME: (82, 1, 0.7),
    GearTier.MIRROR: (86, 1, 1.0),
}
# What a buyable rare looks like, per gear tier: the most tier-1 affixes (of groups with more than
# one tier) on one rare, and the most affixes on a rare jewel; None is no cap. Calibration on
# 2026-10-24 (PLAN.md Phase 5 findings) found one or two tier-1 affixes on thousands of listings,
# three on some, and the optimizer's 4x to 6x T1 rares and 4-affix jewels on none. League start and
# mid can't roll tier 1 at all.
BUY_RULES = {
    GearTier.LEAGUE_START: (0, 2),
    GearTier.MID: (0, 3),
    GearTier.ENDGAME: (2, 3),
    GearTier.MIRROR: (None, None),
}
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
        self.top_cap, self.jewel_cap = BUY_RULES[GearTier(ctx.request.budget.gear_tier)]
        self._pools: dict[str, list[Affix]] = {}

    @cached_property
    def mods(self) -> list[dict]:
        return [m for m in self.ctx.data.mods.get(self.source, []) if m.get("type") in ("Prefix", "Suffix")]

    def pool(self, base_name: str) -> list[Affix]:
        if base_name not in self._pools:
            tags = set(self.ctx.bases_by_name[base_name].get("tags") or [])
            groups: dict[tuple[str, str], list[dict]] = {}
            for mod in self.mods:
                weight = 0
                for key, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []):
                    if key in tags:
                        weight = value
                        break
                if weight > 0:
                    groups.setdefault((mod["group"], mod["type"]), []).append(mod)
            affixes = []
            for (group, kind), mods in groups.items():
                ranked = sorted(mods, key=lambda m: -m.get("level", 0))
                allowed = [(tier, mod) for tier, mod in enumerate(ranked, start=1)
                           if tier >= self.best_tier and mod.get("level", 0) <= self.item_level]
                if not allowed:
                    continue
                # The best tier allowed (lower tiers of a group never score higher), and the next one
                # down for when a buyable rare can't take another tier-1 affix
                (tier, mod), lower = allowed[0], None
                if len(allowed) > 1:
                    lower = self.affix(allowed[1][1], group, kind, allowed[1][0], len(ranked))
                affixes.append(self.affix(mod, group, kind, tier, len(ranked), lower))
            self._pools[base_name] = affixes
        return self._pools[base_name]

    def affix(self, mod: dict, group: str, kind: str, tier: int, tiers: int, lower: Affix | None = None) -> Affix:
        return Affix(mod["id"], group, kind, tier, tuple(roll(line, self.roll) for line in mod_lines(mod)), tiers, lower)

    def buyable(self, affix: Affix, chosen: list[Affix]) -> Affix | None:
        """The affix, or its group one tier down once the rare already holds as many tier-1 affixes
        as a rare people sell does (None when the group has no lower tier)."""
        if self.top_cap is None or not affix.top or sum(1 for a in chosen if a.top) < self.top_cap:
            return affix
        return affix.lower

    def full(self, chosen: list[Affix]) -> bool:
        """A rare jewel with as many affixes as buyable ones have."""
        return self.source == "Jewel" and self.jewel_cap is not None and len(chosen) >= self.jewel_cap


def implicit_lines(base: dict, fraction: float = 0.5) -> list[str]:
    implicit = base.get("implicit")
    return [roll(line, fraction) for line in implicit.splitlines()] if implicit else []


def rare_text(slot: str, base: dict, item_level: int, affixes: list[Affix], runes: list[str] = ()) -> str:
    lines = ["Rarity: Rare", f"Optimizer {slot}", base["name"], f"Item Level: {item_level}"]
    lines += socket_lines(runes)
    implicits = implicit_lines(base)
    lines += [f"Implicits: {len(implicits)}", *implicits]
    for affix in affixes:
        lines += affix.lines
    return "\n".join(lines)


def magic_text(slot: str, base: dict, item_level: int, affixes: list[Affix]) -> str:
    """A magic item (flasks and charms): one prefix and one suffix at most."""
    implicits = implicit_lines(base)
    lines = ["Rarity: Magic", base["name"], f"Item Level: {item_level}", f"Implicits: {len(implicits)}", *implicits]
    for affix in affixes:
        lines += affix.lines
    return "\n".join(lines)


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
    return "\n".join(lines[:at] + socket_lines(runes) + lines[at:])


def rare_price(slot: str, affixes: list[Affix]) -> float:
    """Estimated price in divines (never 0), from this process's rare price model."""
    return rare_prices.active().price(slot, affixes)


# Sockets ------------------------------------------------------------------------------------

def socket_count(base: dict, unique: bool) -> int:
    """Rune sockets an item gets without corruption: two on body armours and two-handers, one
    elsewhere on items that take sockets at all."""
    limit = base.get("socketLimit")
    if not limit:
        return 0
    return max(limit - 2, 0)


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

    def candidates(self, base: dict, unique: bool) -> list[str]:
        categories = rune_categories(base)
        names = []
        for rune in self.ctx.data.runes:
            if rune["name"] in self.ctx.compiled.filters.forbidden_augments:
                continue
            slots = rune["slots"]
            entries = [slots[c] for c in categories if c in slots]
            if not entries:
                continue
            if unique and not any(e.get("canSocketInUniqueItems") for e in entries):
                continue
            if any((e.get("levelReq") or 0) > self.ctx.level for e in entries):
                continue
            if self.price(rune["name"]) is not None:
                names.append(rune["name"])
        return names


__all__ = ["AffixPools", "Affix", "RuneCatalog", "rare_text", "rare_price", "with_runes", "socket_count",
           "implicit_lines", "TIER_RULES", "SCREEN_KEEP", "RUNE_KEEP", "ItemOption"]
