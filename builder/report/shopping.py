"""The shopping list (PLAN.md §1, §7): every item a result uses, what to look for, what it should
cost, and an official trade-site search for each, made only when the user clicks.

A rare's affixes are identified exactly: the optimizer writes each affix at a fixed roll for the
gear tier (builder/search/rares.py), so a mod whose rolled lines appear on the item is the one it
chose, and its rank in its group on that base is the tier. Each affix maps to trade stat ids
through the trade hashes PoB exports with every mod.

A rare's trade search starts strict and loosens only when nothing is listed: the exact base with
the modeled rolls, then any base of the item class at each affix's tier floor (weapons and jewels
keep their base: a weapon's damage and a jewel's mod pool depend on it), then all but one affix.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from builder.data import GameData, rare_prices
from builder.data.trade import TradeClient
from builder.request.schema import GearTier
from builder.search.rares import TIER_RULES, mod_lines, roll

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
SLOT_ORDER = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Ring 1", "Ring 2", "Belt"]
LINK_STATUS = "available"  # instant buyout and in-person sellers who are online
WEAPON_CATEGORY = {
    "Bow": "weapon.bow", "Crossbow": "weapon.crossbow", "Talisman": "weapon.talisman", "Wand": "weapon.wand",
    "Sceptre": "weapon.sceptre", "Dagger": "weapon.dagger", "Claw": "weapon.claw", "Spear": "weapon.spear",
    "Flail": "weapon.flail", "One Hand Sword": "weapon.onesword", "One Hand Axe": "weapon.oneaxe",
    "One Hand Mace": "weapon.onemace", "Two Hand Sword": "weapon.twosword", "Two Hand Axe": "weapon.twoaxe",
    "Two Hand Mace": "weapon.twomace", "Fishing Rod": "weapon.rod",
}
OTHER_CATEGORY = {
    "Shield": "armour.shield", "Focus": "armour.focus", "Buckler": "armour.buckler", "Quiver": "armour.quiver",
    "Helmet": "armour.helmet", "Body Armour": "armour.chest", "Gloves": "armour.gloves", "Boots": "armour.boots",
    "Amulet": "accessory.amulet", "Ring": "accessory.ring", "Belt": "accessory.belt", "Jewel": "jewel",
}
KEEP_BASE = {"weapon", "jewel"}  # categories whose searches always name the base
MAGIC_TYPES = {"Flask", "Charm"}  # magic items with their own affix pools, priced flat (not the rare model)
POOLS = {"Jewel": "Jewel", "Flask": "Flask", "Charm": "Charm"}  # affix pool by item type; "Item" otherwise


def trade_category(base: dict) -> str | None:
    kind = base["type"]
    if kind == "Staff":
        return "weapon.warstaff" if base.get("subType") == "Warstaff" else "weapon.staff"
    return WEAPON_CATEGORY.get(kind) or OTHER_CATEGORY.get(kind)


def slot_key(slot: str) -> tuple:
    if slot in SLOT_ORDER:
        return (SLOT_ORDER.index(slot), slot)
    return (len(SLOT_ORDER), slot)


# Affixes -------------------------------------------------------------------------------------

_RANKED: dict[tuple, list[tuple[dict, int]]] = {}


def ranked_affixes(data: GameData, base_name: str, pool: str) -> list[tuple[dict, int]]:
    """(mod, tier) for every affix the base can roll; tier 1 is its group's best for this base
    (the ranking builder.search.rares.AffixPools uses)."""
    key = (id(data), base_name, pool)
    if key in _RANKED:
        return _RANKED[key]
    base = next(b for b in data.bases if b["name"] == base_name)
    tags = set(base.get("tags") or [])
    groups: dict[tuple[str, str], list[dict]] = {}
    for mod in data.mods.get(pool, []):
        if mod.get("type") not in ("Prefix", "Suffix"):
            continue
        weight = 0
        for tag, value in zip(mod.get("weightKey") or [], mod.get("weightVal") or []):
            if tag in tags:
                weight = value
                break
        if weight > 0:
            groups.setdefault((mod["group"], mod["type"]), []).append(mod)
    ranked = [(mod, tier) for mods in groups.values()
              for tier, mod in enumerate(sorted(mods, key=lambda m: -m.get("level", 0)), start=1)]
    _RANKED[key] = ranked
    return ranked


def identify(item: dict, data: GameData, fraction: float) -> list[tuple[dict | None, int | None, list[str]]]:
    """(mod, tier, its lines) for each affix on an optimizer rare, in the item's order; lines no
    mod accounts for come back with mod None."""
    pool = POOLS.get(item["type"], "Item")
    if not any(b["name"] == item["base"] for b in data.bases):
        return [(None, None, [line]) for line in item["affixes"]]
    by_line: dict[str, list[tuple[dict, int, list[str]]]] = {}
    for mod, tier in ranked_affixes(data, item["base"], pool):
        lines = [roll(line, fraction) for line in mod_lines(mod)]
        if lines:
            by_line.setdefault(lines[0], []).append((mod, tier, lines))
    remaining = list(item["affixes"])
    found = []
    while remaining:
        line = remaining[0]
        options = [o for o in by_line.get(line, []) if all(remaining.count(l) >= o[2].count(l) for l in o[2])]
        if not options:
            found.append((None, None, [remaining.pop(0)]))
            continue
        mod, tier, lines = max(options, key=lambda o: (len(o[2]), -o[1]))
        for l in lines:
            remaining.remove(l)
        found.append((mod, tier, lines))
    return found


def stat_value(text: str, trade_text: str) -> float | None:
    """The number the trade site filters on: the single value, or the average of a '# to #'."""
    numbers = [float(n) for n in NUMBER.findall(text)]
    holes = trade_text.count("#")
    if not numbers or not holes:
        return None
    value = (numbers[0] + numbers[1]) / 2 if holes >= 2 and len(numbers) >= 2 else numbers[0]
    return round(value, 1) if value % 1 else int(value)


def trade_filters(mod: dict, fraction: float, stats: dict[str, dict]) -> list[dict]:
    """One trade filter per stat the mod grants: id, the modeled roll and the tier's floor."""
    filters = []
    for trade_hash, lines in (mod.get("tradeHashes") or {}).items():
        stat_id = f"explicit.stat_{trade_hash}"
        stat = stats.get(stat_id)
        if stat is None:
            continue
        text = " ".join(lines)
        invert = (" reduced " in f" {text} " and "increased" in stat["text"]) or (" less " in f" {text} " and "more" in stat["text"])
        modeled, floor = stat_value(roll(text, fraction), stat["text"]), stat_value(roll(text, 0.0), stat["text"])
        filters.append({"id": stat_id, "text": stat["text"], "roll": modeled, "floor": floor, "invert": invert})
    return filters


# Shopping list ---------------------------------------------------------------------------------

def shopping_list(result: dict, data: GameData, prices: dict, stats: dict[str, dict] | None = None,
                  variants: Callable[[str], str | None] | None = None) -> dict:
    """`prices`: {"augments": {name: divine}, "gems": {name: divine}, "league", "rare_model"};
    `stats`: trade stat ids (TradeClient.stats) for the trade filters."""
    request = result.get("request") or {}
    budget = request.get("budget") or {}
    fraction = TIER_RULES[GearTier(budget.get("gear_tier") or GearTier.ENDGAME)][2]
    model = rare_prices.RarePriceModel.from_payload(prices.get("rare_model"))
    augments = prices.get("augments") or {}
    stats = stats or {}
    bases = {b["name"]: b for b in data.bases}
    items = []
    for slot, gear in sorted((result.get("gear") or {}).items(), key=lambda kv: slot_key(kv[0])):
        base = bases.get(gear["base"]) or {"type": gear["type"], "name": gear["base"]}
        runes = [{"name": name, "price": augments.get(name), "confidence": "poe.ninja exchange"}
                 for name in gear.get("runes") or []]
        rune_cost = sum(r["price"] or 0.0 for r in runes)
        price = gear.get("price")
        if price is not None:
            price = max(price - rune_cost, rare_prices.MIN_PRICE if gear.get("rare") else 0.0)
        kind = ("unique" if gear.get("unique") else "magic" if gear["type"] in MAGIC_TYPES and gear.get("affixes")
                else "rare" if gear.get("rare") else "normal")
        entry = {"slot": slot, "kind": kind, "name": gear["name"], "base": gear["base"], "type": gear["type"],
                 "category": trade_category(base), "price": price, "confidence": gear.get("confidence"),
                 "runes": runes, "affixes": []}
        if kind == "unique":
            entry["variant"] = variants(gear["name"]) if variants else None
        elif kind in ("rare", "magic"):
            if kind == "rare":
                entry["model_category"] = rare_prices.category(slot)
                if gear.get("price") is not None:
                    entry["confidence"] = model.confidence(slot)
            for mod, tier, lines in identify(gear, data, fraction):
                affix = {"text": " / ".join(lines), "tier": tier, "searchable": False}
                if mod is not None:
                    affix.update(mod=mod["id"], type=mod["type"], level=mod.get("level", 0),
                                 range=" / ".join(mod_lines(mod)), trade=trade_filters(mod, fraction, stats))
                    affix["searchable"] = bool(affix["trade"])
                entry["affixes"].append(affix)
        else:
            entry["note"] = "a white base: any copy from drops or a vendor works"
        items.append(entry)
    gems = []
    for group in result.get("groups") or []:
        for gem in group.get("gems") or []:
            price = (prices.get("gems") or {}).get(gem["name"])
            if gem.get("support") and price is not None and all(g["name"] != gem["name"] for g in gems):
                gems.append({"slot": "Gem", "kind": "gem", "name": gem["name"], "price": price,
                             "confidence": "poe.ninja exchange", "runes": [], "affixes": []})
    total = sum((i["price"] or 0.0) + sum(r["price"] or 0.0 for r in i["runes"]) for i in items)
    total += sum(g["price"] for g in gems)
    return {"league": prices.get("league"), "items": items + gems, "total": round(total, 4),
            "budget": budget.get("divines"), "ssf": bool(budget.get("ssf")),
            "rare_prices": model.describe()}


# Trade searches ---------------------------------------------------------------------------------

def _query(stats: list[dict] | None = None, status: str = LINK_STATUS, **fields) -> dict:
    query = {"status": {"option": status}, "stats": stats or [{"type": "and", "filters": []}], **fields}
    return {"query": query, "sort": {"price": "asc"}}


def _type_filters(category: str | None, rarity: str) -> dict:
    filters = {"rarity": {"option": rarity}}
    if category:
        filters["category"] = {"option": category}
    return {"type_filters": {"filters": filters}}


def rare_stats(affixes: list[dict], level: str, slack: int = 0) -> list[dict]:
    filters = []
    for affix in affixes:
        for f in affix.get("trade") or []:
            value = f[level]
            if value is None:
                filters.append({"id": f["id"]})
            else:
                filters.append({"id": f["id"], "value": {"max": -value} if f.get("invert") else {"min": value}})
    if slack and len(filters) > slack:
        return [{"type": "count", "value": {"min": len(filters) - slack}, "filters": filters}]
    return [{"type": "and", "filters": filters}]


def ladder(entry: dict, part: str = "item", status: str = LINK_STATUS) -> list[tuple[str, dict]]:
    """Searches to try in order, each (what it matches, trade query)."""
    if part != "item":
        name = part.split(":", 1)[1]
        return [(f"{name}", _query(status=status, type=name))]
    kind, category = entry["kind"], entry.get("category")
    if kind == "unique":
        return [("this unique", _query(status=status, name=entry["name"], type=entry["base"],
                                       filters=_type_filters(None, "unique")))]
    if kind == "normal":
        return [("this base", _query(status=status, type=entry["base"], filters=_type_filters(None, "normal")))]
    if kind == "gem":
        return [("this gem", _query(status=status, type=entry["name"]))]
    affixes = [a for a in entry.get("affixes") or [] if a.get("searchable")]
    keep_base = entry.get("model_category") in KEEP_BASE or category == "jewel" or kind == "magic"
    loose = {"type": entry["base"]} if keep_base else {}
    steps = [("exact base, modeled rolls",
              _query(rare_stats(affixes, "roll"), status, type=entry["base"], filters=_type_filters(category, "nonunique"))),
             ("same tiers, any roll" + ("" if keep_base else ", any base"),
              _query(rare_stats(affixes, "floor"), status, filters=_type_filters(category, "nonunique"), **loose))]
    if len(affixes) > 2:
        steps.append(("all but one affix, any roll in tier",
                      _query(rare_stats(affixes, "floor", slack=1), status, filters=_type_filters(category, "nonunique"),
                             **loose)))
    return steps


def trade_link(client: TradeClient, league: str, entry: dict, part: str = "item", max_wait: float = 20.0) -> dict:
    """Run the searches for one shopping-list line until one finds listings; the trade-site URL."""
    tried = []
    answer = None
    for label, query in ladder(entry, part):
        answer = client.search(league, query, max_wait=max_wait)
        tried.append({"matches": label, "total": answer["total"]})
        if answer["total"] > 0:
            break
    return {"url": answer["url"], "total": answer["total"], "matches": tried[-1]["matches"], "tried": tried,
            "cached": answer.get("cached", False)}
