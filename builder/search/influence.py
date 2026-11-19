"""Influence-rune affixes on rares (SEARCH30; plan scratch/SEARCH28/plan_unrolled_mods.md part 2, RESEARCH32 R-b / R-e).

An influence rune (Vorana's Carnage, Kolr's Hunt, Katla's Gloom, Uhtred's Sidereus, Medved's Tending, Thrud's Might) has
one line, "Can roll <X> modifiers" (game stat warping_rune_add_item_tag_N); the influence affixes spawn only by that tag
(PoB ModItem / game Mods: weightKey "<x> 1, default 0", 115 rows: berserking 30, marksman 28, decay 23, chronomancy 17,
soul 9, destruction 8). So they roll only on an item with the rune socketed first (RESEARCH32 influence-rune-mods.md,
HIGH), and the rune is socket-bound: it can't be removed or replaced (SoulCores IsSocketBound, HIGH), never on jewellery,
uniques, Corrupted or Sanctified items (RuneCatalog.candidates already keeps those out).

Today's rare trade model has no feature for these affixes (R-e: no market price level found, UNVERIFIED), so the price
is THE CRAFT ROUTE at today's cached prices (MEDIUM): the ordinary affixes at the market model's price (the rare bought)
+ the rune + per influence affix 1 / odds Chaos Orbs (its share of the widened pool's spawn weight, as
rare_prices.jewel_craft counts a Chaos spam; weights are normalised to 1 in PoB and the game export alike, so equal
weights: the real weights are UNVERIFIED). One influence rune per item (UNVERIFIED in R-b; the lower reading). A missing
price (rune or Chaos Orb) means no offer.

OFF by default until a 3-run gate set measures it higher (the manager's rule): POE2_SEARCH_INFLUENCE=1 turns it on.
"""

from __future__ import annotations

import os
import re

INFLUENCE_LINE = re.compile(r"^Can roll (\w+) modifiers$")
CHAOS = "Chaos Orb"


def enabled() -> bool:
    return os.environ.get("POE2_SEARCH_INFLUENCE", "0") == "1"


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


def craft_price(market: float, rune_price: float | None, chaos: float | None, affixes) -> tuple[float | None, list[str]]:
    """The craft route's expected cost (divines) and its steps as text; None without a rune or Chaos Orb price.
    `market`: the ordinary affixes' market price; `affixes`: the influence affixes (each with .odds and .lines)."""
    if rune_price is None or (affixes and chaos is None):
        return None, []
    steps = [f"buy the rare with its other modifiers ({market:.3f} div, the rare price model)",
             f"socket the rune ({rune_price:.3f} div; socket-bound, it stays)"]
    total = market + rune_price
    for affix in affixes:
        if affix.odds <= 0:
            return None, []
        count = 1 / affix.odds
        total += count * chaos
        steps.append(f"roll \"{'; '.join(affix.lines)}\": about {count:.0f} Chaos Orbs ({count * chaos:.3f} div; 1 in "
                     f"{count:.0f} of the widened pool, equal weights: MEDIUM)")
    return total, steps
