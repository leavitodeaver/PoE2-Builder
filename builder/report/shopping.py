"""The shopping list (PLAN.md §1, §7): every item a result uses, what to look for, what it should
cost, and an official trade-site search for each, made only when the user clicks.

A rare's affixes are identified exactly: the optimizer writes each affix at a fixed roll for the
gear tier (builder/search/rares.py), so a mod whose rolled lines appear on the item is the one it
chose, and its rank in its group on that base is the tier. Each affix maps to trade stat ids
through the trade hashes PoB exports with every mod.

A rare's trade search starts strict and loosens only when nothing is listed: the exact base with
the modeled rolls, then any base of the item class at each affix's tier floor (weapons and jewels
keep their base: a weapon's damage and a jewel's mod pool depend on it), then all but one affix.

A unique whose variants the search chose (Megalomaniac's notables, From Nothing's keystone,
Controlled Metamorphosis's ring) is searched with a stat filter for each line those variants add,
matched to the trade site's stat list by text; its listings price that exact version
(builder/data/unique_versions.py keeps the price per version), where poe.ninja's price is for any.
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import Callable
from types import SimpleNamespace

from builder.data import GameData, rare_prices, unique_versions
from builder.data.trade import TradeClient
from builder.request.schema import GearTier
from builder.search.radius import TAG, variants_of
from builder.search.rares import EFFECT, JEWEL_CRAFTS, RANGE, TIER_RULES, boosted, conflicts, mod_lines, roll

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
SLOT_ORDER = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Ring 1", "Ring 2", "Ring 3", "Belt"]
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
_ODDS: dict[tuple, dict[str, tuple[float, float]]] = {}


def mod_odds(data: GameData, base_name: str, pool: str, item_level: int) -> dict[str, tuple[float, float]]:
    """Mod id -> (share of the spawn weight among all the base's affixes, share among its kind's),
    as builder.search.rares.AffixPools computes them for the crafting estimate."""
    key = (id(data), base_name, pool, item_level)
    if key not in _ODDS:
        base = next(b for b in data.bases if b["name"] == base_name)
        tags = set(base.get("tags") or [])
        weights, totals = {}, {"Prefix": 0, "Suffix": 0}
        for mod in data.mods.get(pool, []):
            if mod.get("type") not in ("Prefix", "Suffix") or mod.get("level", 0) > item_level:
                continue
            weight = next((v for k, v in zip(mod.get("weightKey") or [], mod.get("weightVal") or []) if k in tags), 0)
            if weight > 0:
                weights[mod["id"]] = (weight, mod["type"])
                totals[mod["type"]] += weight
        _ODDS[key] = {i: (w / max(1, sum(totals.values())), w / max(1, totals[t])) for i, (w, t) in weights.items()}
    return _ODDS[key]


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
    # A crafted 0.5 jewel (builder/search/rares.py JEWEL_CRAFTS): its effect line boosts one kind
    effect = next(((float(m.group(1)), m.group(2)[:-2]) for line in item["affixes"] if (m := EFFECT.search(line))), None)
    crafts = {mod_id for ids in JEWEL_CRAFTS.values() for mod_id in ids}
    candidates = list(ranked_affixes(data, item["base"], pool))
    if pool == "Jewel":
        candidates += [(m, 1) for m in data.mods.get("Jewel", []) if m["id"] in crafts]
    by_line: dict[str, list[tuple[dict, int, list[str]]]] = {}
    for mod, tier in candidates:
        lines = [roll(line, fraction) for line in mod_lines(mod)]
        if effect and mod["type"] == effect[1] and mod["id"] not in crafts:
            lines = [boosted(line, effect[0]) for line in lines]
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


# Unique versions -------------------------------------------------------------------------------

VALUE = re.compile(r"[+-]?(?:\(\s*-?\d+(?:\.\d+)?\s*-\s*-?\d+(?:\.\d+)?\s*\)|\d+(?:\.\d+)?)")
MARKUP = re.compile(r"\[(?:[^|\]]*\|)?([^\]]*)\]")  # the trade site's "[Resistances|Chaos Resistance]"
POB_TAG = re.compile(r"\{[^}]*\}")


def stat_key(text: str) -> str:
    """What a unique's line and its trade stat have in common: values become #, and signs, case
    and line breaks don't count ("+(2-3)% to Chaos Resistance" ~ "#% to Chaos Resistance")."""
    text = MARKUP.sub(r"\1", POB_TAG.sub("", text))
    return " ".join(VALUE.sub("#", text).replace("+#", "#").lower().split())


def stat_index(stats: dict[str, dict]) -> dict[tuple[str, str], str]:
    """(stat kind, stat_key of its text) -> trade stat id. Stats with options (a notable, a
    keystone, a ring size) carry the option in the id: "enchant.stat_2954116742|34473" is
    "Allocates Spaghettification"."""
    index: dict[tuple[str, str], str] = {}
    for stat_id, stat in stats.items():
        index.setdefault((stat_id.split(".", 1)[0], stat_key(stat["text"])), stat_id)
    return index


def version_lines(raw: str, chosen) -> list[tuple[str, str]] | None:
    """(line, kind) for each line the chosen variants add to a unique; kind is the trade site's:
    "enchant" for {enchant} lines (Megalomaniac's notables are listed as enchantments), "implicit"
    or "explicit". None when the text doesn't have these variants."""
    variants = variants_of(raw)
    if variants is None or not chosen or any(name not in variants.names for name in chosen):
        return None
    picked = {variants.names.index(name) + 1 for name in chosen}
    found, implicits_left = [], 0
    for line in raw.split("\n"):
        if line.startswith("Implicits:"):
            implicits_left = int(line.split(":", 1)[1].strip() or 0)
            continue
        implicit = implicits_left > 0
        implicits_left -= 1 if implicit else 0
        match = TAG.search(line)
        if match and picked & {int(i) for i in match.group(1).split(",")}:
            text = line[:match.start()] + line[match.end():]
            kind = "enchant" if "{enchant}" in text else "implicit" if implicit else "explicit"
            found.append((POB_TAG.sub("", text).strip(), kind))
    return found


def version_filters(raw: str, chosen, index: dict[tuple[str, str], str]) -> list[dict]:
    """{text, id} for each line the chosen variants add: the trade stat to search it by, of the
    line's own kind, or None (the page says to check it by eye)."""
    return [{"text": text, "id": index.get((kind, stat_key(text)))} for text, kind in version_lines(raw, chosen) or []]


def version_entry(entry: dict, gear: dict, data: GameData, versions: dict | None, index: dict) -> None:
    """A unique whose variants the search chose: the lines to search it by, and the trade price of
    this exact version once a Trade click priced it (else the feed's, which is for any version)."""
    chosen = list(gear["variants"])
    entry["variant"] = ", ".join(chosen)
    entry["variants"] = chosen
    raw = next((u["raw"] for u in data.uniques if u["name"] == gear["name"] and version_lines(u["raw"], chosen)), None)
    entry["version_lines"] = version_filters(raw, chosen, index) if raw else []
    known = unique_versions.lookup(versions, gear["name"], chosen)
    feed = entry["feed_price"] = entry["price"]
    if known is None:
        entry["note"] = ("buy this version: the price shown is poe.ninja's for any version of it, which can be far "
                         "cheaper; Trade prices this exact version")
        return
    entry["version_price"] = known
    day = time.strftime("%Y-%m-%d", time.localtime(known["priced_at"]))
    if known.get("price") is not None:
        entry["price"] = known["price"]
        exact = "this version" if known.get("complete", True) else "this version's searchable lines"
        entry["confidence"] = f"trade, {exact} ({known['listed']} listed, {day})"
        entry["note"] = (f"Priced from the trade site's listings of {exact} on {day} (median of the "
                         f"{min(5, known.get('asks') or 5)} cheapest asks)"
                         + (f"; poe.ninja's {feed:.3g} div is for any version." if feed is not None else "."))
        return
    entry["confidence"] = "low: this version isn't listed"
    partial = ""
    if known.get("partial_listed"):
        asking = (f", asking from {known['partial_cheapest']:.3g} div" if known.get("partial_cheapest") is not None else "")
        partial = f"; {known['partial_listed']} listed with {known['partial']}{asking}"
    entry["note"] = (f"No listing of this exact version on {day}{partial}. The price shown is poe.ninja's for any "
                     "version: this one may cost far more, or not be for sale.")


# Shopping list ---------------------------------------------------------------------------------

def shopping_list(result: dict, data: GameData, prices: dict, stats: dict[str, dict] | None = None,
                  variants: Callable[[str], str | None] | None = None) -> dict:
    """`prices`: {"augments": {name: divine}, "gems": {name: divine}, "league", "rare_model",
    "unique_versions" (builder/data/unique_versions.py)}; `stats`: trade stat ids
    (TradeClient.stats) for the trade filters."""
    request = result.get("request") or {}
    budget = request.get("budget") or {}
    item_level, _, fraction = TIER_RULES[GearTier(budget.get("gear_tier") or GearTier.ENDGAME)]
    model = rare_prices.RarePriceModel.from_payload(prices.get("rare_model"))
    model.materials = dict(prices.get("materials") or {})
    augments = prices.get("augments") or {}
    stats = stats or {}
    index = None  # trade stats by text, for uniques' chosen variants
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
            if gear.get("variants"):
                # The search chose these (builder/search/jewels.py); the feed's price is for any version
                index = index if index is not None else stat_index(stats)
                version_entry(entry, gear, data, prices.get("unique_versions"), index)

        elif kind in ("rare", "magic"):
            if kind == "rare":
                entry["model_category"] = rare_prices.category(slot)
                if gear.get("price") is not None:
                    entry["confidence"] = model.confidence(slot)
            crafted_ids = {mod_id for ids in JEWEL_CRAFTS.values() for mod_id in ids}
            effect = next(((m.group(1), m.group(2)) for line in gear["affixes"] if (m := EFFECT.search(line))), None)
            mods = []  # (affix entry, mod) for the crafting estimate
            for mod, tier, lines in identify(gear, data, fraction):
                affix = {"text": " / ".join(lines), "tier": tier, "searchable": False}
                if mod is not None:
                    mods.append((affix, mod))
                if mod is not None and mod["id"] in crafted_ids:
                    # A finished jewel's search asks for the effect line too
                    affix.update(mod=mod["id"], type=mod["type"], crafted=True, tier=None,
                                 trade=trade_filters(mod, fraction, stats))
                    affix["searchable"] = bool(affix["trade"])
                elif mod is not None:
                    affix.update(mod=mod["id"], type=mod["type"], level=mod.get("level", 0),
                                 range=" / ".join(mod_lines(mod)), trade=trade_filters(mod, fraction, stats))
                    affix["searchable"] = bool(affix["trade"])
                    if effect and mod["type"] == effect[1][:-2]:
                        affix["boosted"] = effect[0]  # values shown include it; the search uses the unboosted ones
                entry["affixes"].append(affix)
            if effect:
                kind_name = effect[1][:-2].lower()
                entry["note"] = (f"Crafted (0.5): craft \"+1 {effect[1][:-2]} Modifier allowed\", add a third {kind_name}, then "
                                 f"replace the +1 with \"{effect[0]}% increased Effect of {effect[1]}\". The values shown "
                                 f"include that boost.")
            clashes = conflicts([mod for _, mod in mods])
            if clashes:
                # Built before the rare builder respected these tags: no such item exists
                blocked, blocker = clashes[0]
                entry["impossible"] = (f"This combination can't exist: \"{mod_lines(blocker)[0]}\" stops "
                                       f"\"{mod_lines(blocked)[0]}\" from rolling on the same item. Rerun the "
                                       "optimizer; it now keeps such mods apart.")
            if gear["type"] == "Jewel" and kind == "rare" and gear.get("price") is not None:
                jewel_prices(entry, slot, mods, model, data, item_level, fraction)
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


def jewel_prices(entry: dict, slot: str, mods: list[tuple[dict, dict]], model, data: GameData, item_level: int,
                 fraction: float) -> None:
    """A rare jewel's two prices: the market model's, and crafting it from a bought jewel (base
    plus materials, as a range), and which one the budget uses."""
    odds = mod_odds(data, entry["base"], "Jewel", item_level)
    affixes = [SimpleNamespace(type=mod["type"], crafted=bool(affix.get("crafted")),
                               lines=[roll(line, fraction) for line in mod_lines(mod)],
                               ranged=any(RANGE.search(line) for line in mod_lines(mod)),
                               odds=odds.get(mod["id"], (0.0, 0.0))[0], side_odds=odds.get(mod["id"], (0.0, 0.0))[1],
                               tier=affix.get("tier") or 1, top=False, index=i)
               for i, (affix, mod) in enumerate(mods)]
    priced = model.jewel(slot, affixes, fraction)
    entry["market_estimate"] = priced["market"]
    entry["basis"] = priced["basis"]
    entry["price"] = priced["price"]
    if priced["basis"] == "crafting":
        entry["confidence"] = "crafting estimate (rough: chaos spam and 50/50s vary)"
    craft = priced["craft"]
    if craft:
        keep = [a for a in affixes if " / ".join(a.lines) in craft["base"]["mods"]]
        craft["base"]["affixes"] = [mods[a.index][0] for a in keep]
        entry["craft"] = craft


def ladder(entry: dict, part: str = "item", status: str = LINK_STATUS) -> list[tuple[str, dict]]:
    """Searches to try in order, each (what it matches, trade query)."""
    if part == "base":  # the jewel to craft from: its two hardest mods, any roll within their tiers
        affixes = [a for a in entry["craft"]["base"]["affixes"] if a.get("searchable")]
        return [("a jewel to craft from", _query(rare_stats(affixes, "floor"), status, type=entry["base"],
                                                 filters=_type_filters(entry.get("category"), "nonunique")))]
    if part != "item":
        name = part.split(":", 1)[1]
        return [(f"{name}", _query(status=status, type=name))]
    kind, category = entry["kind"], entry.get("category")
    if kind == "unique":
        return unique_ladder(entry, status)
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


def unique_ladder(entry: dict, status: str = LINK_STATUS) -> list[tuple[str, dict]]:
    """A unique by name; with chosen variants, every line they add (any roll), then, for versions
    of several lines (Megalomaniac's three notables), all but one of them: shown, but never this
    version's price."""
    plain = {"name": entry["name"], "type": entry["base"], "filters": _type_filters(None, "unique")}
    lines = entry.get("version_lines") or []
    filters = [{"id": line["id"]} for line in lines if line.get("id")]
    if not filters:
        label = "this unique, any version (its chosen lines aren't searchable: check them by eye)" if lines else "this unique"
        return [(label, _query(status=status, **plain))]
    exact = "this version" if len(filters) == len(lines) else f"this version's {len(filters)} searchable lines of {len(lines)}"
    steps = [(exact, _query([{"type": "and", "filters": filters}], status, **plain))]
    if len(filters) > 1:
        steps.append((f"{len(filters) - 1} of its {len(filters)} chosen lines",
                      _query([{"type": "count", "value": {"min": len(filters) - 1}, "filters": filters}], status, **plain)))
    return steps


def trade_link(client: TradeClient, league: str, entry: dict, part: str = "item", max_wait: float = 20.0,
               rates: dict[str, float] | None = None) -> dict:
    """Run the searches for one shopping-list line until one finds listings; the trade-site URL, and
    (with currency rates) what the cheapest listings ask, from one fetch of the first ten. A unique
    with chosen variants also gets `version`: what this exact version costs (unique_versions)."""
    tried = []
    answer = None
    for label, query in ladder(entry, part):
        answer = client.search(league, query, max_wait=max_wait)
        tried.append({"matches": label, "total": answer["total"], "url": answer["url"]})
        if answer["total"] > 0:
            break
    link = {"url": answer["url"], "total": answer["total"], "matches": tried[-1]["matches"], "tried": tried,
            "cached": answer.get("cached", False)}
    if rates and answer["total"] and answer.get("result"):
        listings = client.fetch(answer["result"][:10], answer["id"], max_wait=max_wait)
        asked = sorted(x for x in (listing_divines(l, rates) for l in listings) if x)
        if asked:
            link["asking"] = {"cheapest": asked[0], "typical": statistics.median(asked[:5]), "listings": len(asked)}
    lines = entry.get("version_lines") or []
    if part == "item" and entry.get("kind") == "unique" and any(line.get("id") for line in lines):
        link["version"] = version_priced(tried, link, all(line.get("id") for line in lines))
    return link


def version_priced(tried: list[dict], link: dict, complete: bool = True) -> dict:
    """What a Trade click found for a unique's chosen version, as unique_versions keeps it: a price
    only from listings of the exact version (the first search), with what looser searches found.
    `complete`: every line the version adds was searched."""
    exact = tried[0]
    priced = {"listed": exact["total"], "price": None, "url": exact["url"], "matches": exact["matches"],
              "complete": complete}
    if exact["total"] and link.get("asking"):
        priced.update(price=link["asking"]["typical"], cheapest=link["asking"]["cheapest"],
                      asks=link["asking"]["listings"])
    elif not exact["total"] and len(tried) > 1:
        priced.update(partial=tried[-1]["matches"], partial_listed=tried[-1]["total"], partial_url=tried[-1]["url"])
        if tried[-1]["total"] and link.get("asking"):
            priced.update(partial_cheapest=link["asking"]["cheapest"], partial_typical=link["asking"]["typical"])
    return priced


def listing_divines(listing: dict, rates: dict[str, float]) -> float | None:
    price = (listing.get("listing") or {}).get("price") or {}
    rate = rates.get(price.get("currency"))
    return float(price["amount"]) * rate if rate is not None and price.get("amount") else None
