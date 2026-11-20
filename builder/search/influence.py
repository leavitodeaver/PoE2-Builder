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

SEARCH31 (the user, 19 Nov ~21:55: "Sample trade prices first"): 12 trade-site samples (Forbidden Rites, 19 Nov,
MARKET_SAMPLES) show these rares on sale in quantity, mostly at 0.02-1 div (uncorrupted 0.1-5; Medved's Life + Mana 2-20),
while the Chaos-spam route said 15-23 div. The user then asked (19 Nov ~22:12) for the DETERMINISTIC craft route,
and on seeing it (20 Nov ~03:00) ruled the HEADLINE is Chaos spam + a Fracturing Orb + Omens of Whittling (`chaos_price`;
"annulment orbs are way more expensive than chaos"); the Exalt loop (the 22:12 "basically the same method as desecrated runes with
a wider mod pool": `deterministic_price`, Exalted Orb + Exaltation Omen, Annulment on a miss, repeat) is a row, and the
samples a CROSS-CHECK row only (`market_price`:
the rare price model counting the influence affix as an ordinary affix, never below the cheapest real listings, for
runes the market supplies: every sampled mod LIQUID+ listings; Thrud's Might isn't).

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
MATERIALS = (CHAOS, EXALT, ANNUL, *EXALTATION.values(), *ANNULMENT.values(), "Fracturing Orb", "Omen of Whittling")


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


# (SEARCH31, the user 19 Nov ~21:55: "Sample trade prices first") Trade-site samples, Forbidden Rites, 19 Nov 2026 22:08:
# per rune, its sampled influence mods (tier 2, or 1 where it has one tier): (mod id, listings matching the stat on its
# item class, the median of the 5 cheapest instant-buyout listings that carry the rune AND the stat, divines). Most such
# listings are corrupted (Omen of Putrefaction outputs); the uncorrupted ones cost 0.1-5 div (Medved's Life + Mana 2-20).
# Log and listings: scratch/SEARCH31/infl_samples.log, infl_repriced.json.
MARKET_SAMPLES = {
    "Kolr's Hunt": [("MarksmanInfluenceChanceToPierce1", 2085, 0.0988), ("MarksmanInfluenceProjectileDamage2", 9649, 0.0988)],
    "Katla's Gloom": [("DecayInfluenceAilmentDuration1", 926, 0.0988), ("DecayInfluenceIgniteMagnitude1", 1898, 0.1854)],
    "Vorana's Carnage": [("BerserkInfluenceArmourBreakDuration1", 162, 0.0988),
                         ("BerserkInfluenceArmourBreakMagnitude2", 184, 0.2965)],
    "Uhtred's Sidereus": [("TimeInfluenceCharges1", 4744, 1.0), ("TimeInfluenceDebuffExpiry1", 1182, 0.0178)],
    "Medved's Tending": [("SoulInfluenceReducedAilmentDurationAgainstYou", 130, 1.0),
                         ("SoulInfluenceIncreasedLifeAndMana", 181, 5.0)],
    "Thrud's Might": [("DestructionInfluenceChaosModifierEffect", 1, 900.0), ("DestructionInfluenceColdModifierEffect", 0, None)],
}
LIQUID = 100  # listings every sampled mod of a rune needs before the market prices its rares (a judgement: MEDIUM-LOW)


def supplied(rune: str) -> bool:
    """Whether the trade site sells this rune's rares in quantity (every sampled mod listed LIQUID+ times)."""
    rows = MARKET_SAMPLES.get(rune)
    return bool(rows) and all(listed >= LIQUID for _, listed, _ in rows)


def market_price(rune: str, full: float, affixes) -> tuple[float | None, list[str]]:
    """The finished rare bought on the trade site (the rune comes with it): the rare price model counting each influence
    affix as an ordinary affix of its tier (`full`), never below the dearest sampled cheapest listing of its sampled mods
    (MEDIUM-LOW: 12 samples of the mod alone). None for a rune the market doesn't supply (Thrud's Might: 1 listing)."""
    if not supplied(rune):
        return None, []
    floor = max(price for _, _, price in MARKET_SAMPLES[rune] if price is not None)
    total = max(full, floor)
    lines = "; ".join(line for a in affixes if a.influence for line in a.lines)
    return total, [f"buy it finished on the trade site: {total:.3f} div (the rare price model counting \"{lines}\" as "
                   f"ordinary affixes, at least {floor:.3f} div, the cheapest real listings of {rune}'s sampled mods; "
                   f"samples of 19 Nov, MEDIUM-LOW)"]


def deterministic_price(market: float, rune_price: float | None, prices: dict[str, float], affixes
                        ) -> tuple[float | None, list[str]]:
    """(SEARCH31; the user, 19 Nov ~22:12: "figure out the deterministic crafting cost for the influence runes as well,
    since those are basically the same method as desecrated runes with a wider mod pool") The repeat-until-hit route: buy
    the rare with its other modifiers and the side open, socket the rune, then per try an Exalted Orb with the side's
    Exaltation Omen (one modifier from the side's widened pool: the ordinary ones + the rune's); on a miss an Orb of
    Annulment (with or without the side's Annulment Omen, the cheaper) removes it, or a wanted modifier m / (m + 1) of the
    time (then a fresh copy of the rare), and repeat. The odds per try are the affix's share of its side's pool (`routes`;
    equal weights for the rune's rows: LOW). Chaos spam is a row. None without a priced Exalt route."""
    if rune_price is None:
        return None, []
    total, steps = market + rune_price, []
    for affix in [a for a in affixes if a.influence]:
        others = [a for a in affixes if a is not affix]
        found = routes(affix, others, market, prices)
        exalt = [r for r in found if "Exalted" in r[1]]
        chaos = [r for r in found if "Chaos" in r[1]]
        if not exalt:
            return None, []
        cost, how = min(exalt)
        total += cost
        tries = 1 / affix.side_odds
        steps.append(f"roll \"{'; '.join(affix.lines)}\" ({affix.type.lower()}): {how}: about {tries:.0f} tries, one in "
                     f"{tries:.0f} {affix.type.lower()}es the item can roll with the rune in (LOW: equal weights), "
                     f"{cost:.3f} div")
        if chaos:
            steps.append(f"row: Chaos Orb spam instead (random, every modifier rerolled): {chaos[0][1]}, {chaos[0][0]:.3f} div")
    head = [f"1. buy the rare with its other modifiers and that side open ({market:.3f} div, the rare price model)",
            f"2. socket the rune first ({rune_price:.3f} div; socket-bound, it stays and lets its modifiers roll)",
            "3. each try: an Exalted Orb with the side's Exaltation Omen; on a miss an Orb of Annulment removes a mod "
            "(the miss, or a wanted one m / (m + 1) of the time: then a fresh copy), and repeat:"]
    return total, head + steps


FRACTURE = "Fracturing Orb"
WHITTLING = "Omen of Whittling"
FRACTURE_MODS = 4  # a Fracturing Orb needs a rare with at least 4 modifiers and picks one at random (poe2db): 1 in 4 at best


def chaos_price(market: float, rune_price: float | None, prices: dict[str, float], affixes
                ) -> tuple[float | None, list[str]]:
    """THE USER'S METHOD (20 Nov ~03:00): "you can just spam chaos orbs on an item until you get the mod you want ...
    once you get a mod you want, then people usually try and fracture that one to lock it in, or you can use omens of
    whittling to forcibly remove the lower rolls". With the rune socketed, spam Chaos Orbs ("Removes a random modifier and
    augments a Rare item with a new random modifier") until the first influence mod lands: 1 / odds Chaos Orbs (odds: its
    share of the widened pool, equal weights for the rune's rows, LOW). If the item needs anything else, lock it with a
    Fracturing Orb ("Fracture a random modifier on a rare item with at least 4 modifiers"): 1 in 4 at best, so about 4
    Orbs, and each miss locks a wrong mod: the item is spent, and 3 more spam runs on a fresh base with a fresh rune (LOW).
    Each further influence mod: Chaos Orbs each with an Omen of Whittling ("your next Chaos Orb will remove the lowest
    level modifier"), so the landed wanted mods stay (LOW: they are taken to be above the item's lowest level). The other
    modifiers: the market price of the rare with them (the rare price model, as every route). None without prices."""
    if rune_price is None or prices.get(CHAOS) is None:
        return None, []
    landed = sorted([a for a in affixes if a.influence], key=lambda a: -a.odds)
    if not landed or any(a.odds <= 0 for a in landed):
        return None, []
    chaos = prices[CHAOS]
    first = landed[0]
    spam = chaos / first.odds
    steps = [f"1. socket the rune in the base first ({rune_price:.3f} div; socket-bound)",
             f"2. spam Chaos Orbs until \"{'; '.join(first.lines)}\" lands: about {1 / first.odds:.0f} Chaos Orbs, "
             f"{spam:.3f} div (one in {1 / first.odds:.0f} of the mods the item can roll with the rune in; equal weights: LOW)"]
    total = rune_price + spam
    if len(affixes) > 1:
        if prices.get(FRACTURE) is None:
            return None, []
        lock = FRACTURE_MODS * prices[FRACTURE] + (FRACTURE_MODS - 1) * (spam + rune_price)
        total += lock
        steps.append(f"3. lock it with a Fracturing Orb (random among at least {FRACTURE_MODS} mods: about "
                     f"{FRACTURE_MODS} Orbs at {prices[FRACTURE]:.2f} div, each miss spends the item and redoes 1-2): "
                     f"{lock:.3f} div (LOW)")
    for affix in landed[1:]:
        if prices.get(WHITTLING) is None:
            return None, []
        extra = (chaos + prices[WHITTLING]) / affix.odds
        total += extra
        steps.append(f"then \"{'; '.join(affix.lines)}\": about {1 / affix.odds:.0f} Chaos Orbs each with an Omen of "
                     f"Whittling (the lowest-level mod goes, not the landed ones): {extra:.3f} div (LOW)")
    if len(affixes) > len(landed):
        total += market
        steps.append(f"the other modifiers: {market:.3f} div (the rare price model's price for them)")
    steps.append(f"in all {total:.3f} div (the user: \"crafting is pretty expensive either way\")")
    return total, steps


def price(rune: str, market: float, full: float, rune_price: float | None, prices: dict[str, float], affixes
          ) -> tuple[float | None, list[str], str]:
    """(price, steps, how): the user's Chaos-spam route (20 Nov ~03:00) the headline; the Exalt / Annulment loop a row;
    the trade samples a cross-check row (MARKET_SAMPLES, `market_price`)."""
    crafted, steps = chaos_price(market, rune_price, prices, affixes)
    if crafted is None:
        return None, [], ""
    looped, _ = deterministic_price(market, rune_price, prices, affixes)
    if looped is not None:
        steps = steps + [f"row: the Exalted Orb + Exaltation Omen loop with an Annulment on a miss instead: {looped:.3f} div"]
    bought, _ = market_price(rune, full, affixes)
    if bought is not None:
        steps = steps + [f"cross-check: the trade site sells such rares from about {bought:.3f} div (samples of 19 Nov; "
                         f"most listings corrupted Omen of Putrefaction outputs)"]
    else:
        steps = steps + [f"cross-check: the trade site barely lists {rune}'s rares (samples of 19 Nov)"]
    return crafted, steps, "craft route: influence rune + Chaos Orbs"
