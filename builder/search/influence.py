"""Influence-rune affixes on rares (SEARCH30; plan scratch/SEARCH28/plan_unrolled_mods.md part 2, RESEARCH32 R-b / R-e).

An influence rune (Vorana's Carnage, Kolr's Hunt, Katla's Gloom, Uhtred's Sidereus, Medved's Tending, Thrud's Might) has
one line, "Can roll <X> modifiers" (game stat warping_rune_add_item_tag_N); the influence affixes spawn only by that tag
(PoB ModItem / game Mods: weightKey "<x> 1, default 0", 115 rows: berserking 30, marksman 28, decay 23, chronomancy 17,
soul 9, destruction 8). So they roll only on an item with the rune socketed first (RESEARCH32 influence-rune-mods.md,
HIGH), and the rune is socket-bound: it can't be removed or replaced (SoulCores IsSocketBound, HIGH), never on jewellery,
uniques, Corrupted or Sanctified items (RuneCatalog.candidates already keeps those out).

Today's rare trade model has no feature for these affixes (R-e: no market price level found, UNVERIFIED), so the price
is THE CRAFT ROUTE at today's cached prices: the ordinary affixes at the market model's price (the rare bought) + the
rune + per influence affix the cheaper of Chaos spam and an Exalted Orb with the side's Exaltation Omen (an Annulment,
with or without the side's Annulment Omen, on a miss) (`routes`; RESEARCH34 R33-23, influence-rune-craft-route-and-
weights.md). Odds rest on equal weights: the real spawn weights can't be known from any source (LOW; league mods are
often rarer, so the real cost may be higher). Omens of Homogenising Exaltation are Standard-only since 0.5.0: no route.
One influence rune per item (UNVERIFIED in R-b; the lower reading). No priced route (or no rune price): no offer.

OFF by default until a 3-run gate set measures it higher (the manager's rule): POE2_SEARCH_INFLUENCE=1 turns it on.
"""

from __future__ import annotations

import os
import re

INFLUENCE_LINE = re.compile(r"^Can roll (\w+) modifiers$")
CHAOS = "Chaos Orb"
EXALT = "Exalted Orb"
ANNUL = "Orb of Annulment"
EXALTATION = {"Prefix": "Omen of Sinistral Exaltation", "Suffix": "Omen of Dextral Exaltation"}
ANNULMENT = {"Prefix": "Omen of Sinistral Annulment", "Suffix": "Omen of Dextral Annulment"}
MATERIALS = (CHAOS, EXALT, ANNUL, *EXALTATION.values(), *ANNULMENT.values())


def enabled() -> bool:
    return os.environ.get("POE2_SEARCH_INFLUENCE", "0") == "1"


def payload_prices(prices) -> dict[str, float]:
    """The crafting materials' poe.ninja exchange prices (divines); {} without price data."""
    if prices is None:
        return {}
    return {name: prices.exchange[name].divine for name in MATERIALS if name in prices.exchange}


def rune_tags(runes: list[dict]) -> dict[str, str]:
    """Rune name -> the influence tag its line adds ("Kolr's Hunt" -> "marksman"), from GameData.runes' own lines."""
    found = {}
    for rune in runes:
        for entry in (rune.get("slots") or {}).values():
            if not isinstance(entry, dict):
                continue
            match = INFLUENCE_LINE.match(str(entry.get("1") or "").strip())
            if match:
                found[rune["name"]] = match.group(1).lower()
                break
    return found


def routes(affix, others: list, market: float, prices: dict[str, float]) -> list[tuple[float, str]]:
    """(expected cost in divines, what it is) of each priced route to land `affix` (with .odds, .side_odds, .type) on a
    rare whose other modifiers are `others` (RESEARCH34 R33-23):
    - Chaos spam: 1 / odds Chaos Orbs (odds: its share of the widened pool on both sides);
    - an Exalted Orb with the side's Exaltation Omen into an open slot, 1 / side_odds tries; on a miss an Orb of
      Annulment, with the side's Annulment Omen (removes from that side) or without (from any side); with m other
      modifiers it can hit, it removes a wanted one m / (m + 1) of the time, counted as a fresh copy of the rare (LOW)."""
    found = []
    if affix.odds > 0 and prices.get(CHAOS) is not None:
        count = 1 / affix.odds
        found.append((count * prices[CHAOS], f"about {count:.0f} Chaos Orbs"))
    exalt = [prices.get(EXALT), prices.get(EXALTATION[affix.type])]
    if affix.side_odds > 0 and None not in exalt and prices.get(ANNUL) is not None:
        tries = 1 / affix.side_odds
        per_try = sum(exalt)
        same_side = sum(1 for a in others if a.type == affix.type)
        clears = [(prices[ANNUL], len(others), "an Orb of Annulment")]
        if prices.get(ANNULMENT[affix.type]) is not None:
            clears.append((prices[ANNUL] + prices[ANNULMENT[affix.type]], same_side,
                           f"an Orb of Annulment + {ANNULMENT[affix.type]}"))
        for clear, m, how in clears:
            miss = clear + m / (m + 1) * market
            found.append((tries * per_try + (tries - 1) * miss,
                          f"about {tries:.0f} Exalted Orbs + {EXALTATION[affix.type]} (each miss cleared by {how})"))
    return found


def craft_price(market: float, rune_price: float | None, prices: dict[str, float], affixes
                ) -> tuple[float | None, list[str]]:
    """The cheapest craft route's expected cost (divines) and its steps; None without a rune price or a priced route for
    some influence affix. `market`: the ordinary affixes' market price; `affixes`: the item's affixes (.influence marks
    the influence ones)."""
    if rune_price is None:
        return None, []
    steps = [f"buy the rare with its other modifiers ({market:.3f} div, the rare price model)",
             f"socket the rune ({rune_price:.3f} div; socket-bound, it stays)"]
    total = market + rune_price
    for affix in [a for a in affixes if a.influence]:
        found = routes(affix, [a for a in affixes if a is not affix], market, prices)
        if not found:
            return None, []
        cost, how = min(found)
        total += cost
        steps.append(f"roll \"{'; '.join(affix.lines)}\": {how}, {cost:.3f} div (the cheapest of {len(found)} routes; "
                     f"equal weights: LOW)")
    return total, steps
