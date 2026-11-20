"""Desecrated (Abyssal lord) affixes on rares (SEARCH30; plan scratch/SEARCH28/plan_unrolled_mods.md part 1, RESEARCH32
R-a desecrated-mod-rules.md and R-e pricing-desecrated-influence-rares.md).

What the game allows (R-a): one Desecrated modifier per item (0.5.0, HIGH); it takes a prefix or suffix slot (HIGH);
the lord mods are item level 65 (PoB ModVeiled: the Amanamu / Kurgal / Ulaman rows are level 65, MEDIUM); a bone of the
item's class adds it unrevealed (on a full side it also removes a random mod: the game's "Abyssalify" text, R33-22) and
the reveal offers three options of that side (see the price below); on Weapons and Jewellery
an Omen fixes the lord (Sovereign = Ulaman, Liege = Amanamu, Blackblooded = Kurgal, HIGH), on Armour none does; Omens of
Sinistral / Dextral Necromancy force a prefix / suffix (HIGH).

The pool: ctx.data.mods["Desecrated"] (PoB ModVeiled.lua, 388 rows), the rows whose id / modTags name a lord (198); the
others never come from a plain bone (RESEARCH34 R33-22, HIGH: their spawn tags; checked over every Domain-28 row):
jewel rows (Preserved Cranium on a rare Jewel), Breach rows (Altered Collarbone on jewellery), and unique / map rows. A row's lord is read from its id and modTags: some rows' weightKey names
another lord's tag (AbyssMod1HMaceAmanamuPrefix... has ulaman_mod 1 in its weightKey, amanamu_mod in its modTags); the
id, the affix name and the modTags agree, so they win (MEDIUM).

The price (R-e: no market price level found, UNVERIFIED) is THE CRAFT ROUTE at today's cached prices, the cheapest of
`routes` (RESEARCH34, 19 Nov: desecration-reveal-and-omen-stacking.md, desecration-nonlord-rows-and-rib-classes.md):
the ordinary affixes at the market model's price (the rare bought with that slot open) + per try a bone (the cheapest
Preserved / Ancient one of the class; a Gnawed bone caps at item level 64, below the lord mods) + the Necromancy Omen
for the side, optionally Omen of Abyssal Echoes (one reroll of the three), and on Weapons / Jewellery optionally the
lord's Omen (refused on Armour, Shields and Foci). A reveal's three options are drawn from the side's ORDINARY affixes
and every lord row, not lord rows only (R33-20, MEDIUM: "may include base modifiers"; the first model, 3 / lord rows,
was an OVERCOUNT and is now a row), each at weight 1 (LOW: real weights hidden); a lord Omen guarantees one option
from its lord (MEDIUM-LOW; with a Necromancy Omen three players saw it fail: R33-21 UNSETTLED, a row). Each failed try
also pays to clear the wrong mod: the cheaper of Omen of Light + Orb of Annulment and a fresh copy of the plain rare.
A missing price means no route. Ribs work on Shields and Foci (R33-24, MEDIUM-HIGH: game ItemClasses flag 5).

Two more routes (SEARCH31; RESEARCH34 R33-22, MEDIUM-HIGH), each priced the same way (the reveal drawn from the side's
ordinary rows + every Desecrated row that bone can give there, weight 1 each, LOW):
- "breach": an ALTERED COLLARBONE ("Desecrates a Rare Amulet, Ring or Belt with a chance for otherworldly modifiers",
  Genesis Tree output) gives the 36 Breach rows ("of Esh", "Tul's", "Storm Chaser's" ...: game Mods ids GenesisTree*,
  Domain 28, spawn tag breach_desecration, which no base carries, so the route adds it, on jewellery only; their ring /
  belt / amulet zero weights leave Amulet 4, Ring 16, Belt 16 rows, as poe2db's Otherworldly lists). Its reveal also
  counts the lord rows ("a chance for otherworldly modifiers": the share is UNVERIFIED, so the lord rows stay in the
  draw, the lower reading). No lord Omen (it fixes a lord, not a Breach row). The Necromancy Omen is taken to work on it
  (it is a Desecration: MEDIUM, UNVERIFIED for this bone).
- "cranium": a PRESERVED CRANIUM ("Desecrates a Jewel"; the one Cranium: checked game BaseItemTypes for "Cranium", one
  row, Metadata/Items/Currency/AbyssalBenchTicketJewel; poe.ninja's Abyss exchange lists only it) gives the jewel rows
  ("Lightless", "of the Abyss": spawn tags strjewel / dexjewel / intjewel and *_radius_jewel) on a rare jewel (jewels.py,
  the plain 2 + 2 shape). No lord Omen (Weapons / Jewellery only, 0.3.0c).
The unique-only rows (Heart of the Well, Undying Hate, the Unborn Lich, Grip of Kulemak) and the Waystone rows stay out
(R33-22, HIGH: their spawn tags are those items'). Not modelled, UNDERCOUNTS said here: Genesis Tree items carrying the
"Crafted" copies of the Breach rows (game Domain 1 rows at weight 0, so from no currency: an item source the rare price
model doesn't cover); an Altered Collarbone giving a LORD row (a Preserved Collarbone does that far cheaper); a Cranium on
a jewel of the boosted 3 + 1 shape.

OFF by default until a 3-run gate set measures it higher (the manager's rule): POE2_SEARCH_DESECRATED=1 turns the bone
route on, POE2_SEARCH_DESECRATED_BREACH=1 the Altered Collarbone and POE2_SEARCH_DESECRATED_JEWEL=1 the Preserved Cranium.
"""

from __future__ import annotations

import os
import re

LORDS = ("Amanamu", "Kurgal", "Ulaman")
LORD_OMEN = {"Ulaman": "Omen of the Sovereign", "Amanamu": "Omen of the Liege", "Kurgal": "Omen of the Blackblooded"}
SIDE_OMEN = {"Prefix": "Omen of Sinistral Necromancy", "Suffix": "Omen of Dextral Necromancy"}
CLEAR = ("Omen of Light", "Orb of Annulment")
ECHOES = "Omen of Abyssal Echoes"
REVEAL = 3  # options one reveal offers (0.3.0 notes: "one of three different options")
WEAPON_TYPES = {"Bow", "Crossbow", "Quiver", "Wand", "Staff", "Sceptre", "Spear", "Flail", "Talisman", "Claw", "Dagger",
                "One Hand Mace", "Two Hand Mace", "One Hand Sword", "Two Hand Sword", "One Hand Axe", "Two Hand Axe"}
# (Shields and Foci take a Rib as Armour: MEDIUM-HIGH, RESEARCH34 R33-24: the game's ItemClasses give them the armour
# flag 5; their rows' weightKeys decide which mods they can take)
ARMOUR_TYPES = {"Helmet", "Body Armour", "Gloves", "Boots", "Shield", "Focus"}
JEWELLERY_TYPES = {"Ring", "Amulet", "Belt"}
BONES = {"weapon": ("Preserved Jawbone", "Ancient Jawbone"), "armour": ("Preserved Rib", "Ancient Rib"),
         "jewellery": ("Preserved Collarbone", "Ancient Collarbone")}
ALTERED = "Altered Collarbone"  # (SEARCH31) the Breach rows, on Amulets / Rings / Belts
CRANIUM = "Preserved Cranium"  # (SEARCH31) the jewel rows, on rare Jewels
BREACH_TAG = "breach_desecration"
JEWEL_TAGS = {"strjewel", "dexjewel", "intjewel", "str_radius_jewel", "dex_radius_jewel", "int_radius_jewel"}
ROUTE_LABEL = {"breach": "Breach", "cranium": "Abyss jewel"}
LORD = re.compile(r"(Amanamu|Kurgal|Ulaman)")


def enabled() -> bool:
    return os.environ.get("POE2_SEARCH_DESECRATED", "0") == "1"


def breach_enabled() -> bool:
    return os.environ.get("POE2_SEARCH_DESECRATED_BREACH", "0") == "1"


def cranium_enabled() -> bool:
    return os.environ.get("POE2_SEARCH_DESECRATED_JEWEL", "0") == "1"


def route_of(mod: dict) -> str:
    """Which bone gives a Desecrated row: "bone" (a lord row: a Jawbone / Rib / Collarbone), "breach" (an Altered
    Collarbone), "cranium" (a Preserved Cranium), or "" (unique / Waystone rows, never from currency: R33-22)."""
    if lord(mod):
        return "bone"
    spawn = {t for t, v in zip(mod.get("weightKey") or [], mod.get("weightVal") or []) if v > 0}
    if BREACH_TAG in spawn:
        return "breach"
    if spawn & JEWEL_TAGS:
        return "cranium"
    return ""


def label(mod: dict) -> str:
    """Affix.desecrated: the lord's name for a bone row, else the route's label."""
    return lord(mod) or ROUTE_LABEL.get(route_of(mod), "")


def lord(mod: dict) -> str:
    """The lord a Desecrated row belongs to ("" for none), from its id, else its modTags."""
    found = LORD.search(mod.get("id") or "")
    if found:
        return found.group(1)
    for tag in mod.get("modTags") or []:
        for name in LORDS:
            if tag == f"{name.lower()}_mod":
                return name
    return ""


def family(item_type: str) -> str | None:
    if item_type == "Jewel":
        return "jewel"
    if item_type in JEWELLERY_TYPES:
        return "jewellery"
    if item_type in ARMOUR_TYPES:
        return "armour"
    if item_type in WEAPON_TYPES:
        return "weapon"
    return None


def offered(pool_size: int, draws: int) -> float:
    """The chance a wanted row is among `draws` options drawn from `pool_size` rows at equal weight (LOW: real weights hidden)."""
    return 1 - (1 - 1 / pool_size) ** draws if pool_size > 0 else 0.0


def routes(item_type: str, affix, n_lord: int, n_lords: int, n_ordinary: int) -> list[tuple[str, list[str], float]]:
    """(name, fixed materials per try besides the bone, chance one try offers the wanted row), RESEARCH34 R33-20 / 21:
    a reveal's 3 options are drawn from the side's ORDINARY affixes plus every lord row (N = n_ordinary + n_lords), each at
    weight 1 (LOW); a lord Omen (Weapons / Jewellery only) guarantees one option from its lord (p = 1/n_lord + 2/N,
    MEDIUM-LOW); Omen of Abyssal Echoes rerolls the three once. The Necromancy Omen fixes the side in every route."""
    side = [SIDE_OMEN[affix.type]]
    n_all = n_ordinary + n_lords
    plain = offered(n_all, REVEAL)
    found = [("bone + Necromancy Omen", side, plain),
             ("bone + Necromancy Omen + Omen of Abyssal Echoes", side + [ECHOES], offered(n_all, 2 * REVEAL))]
    if family(item_type) in ("weapon", "jewellery") and n_lord > 0 and affix.desecrated in LORD_OMEN:
        omen = LORD_OMEN[affix.desecrated]
        guaranteed = min(1.0, 1 / n_lord + (REVEAL - 1) / max(1, n_all))
        found += [(f"bone + Necromancy Omen + {omen}", side + [omen], guaranteed),
                  (f"bone + Necromancy Omen + {omen} + Omen of Abyssal Echoes", side + [omen, ECHOES],
                   1 - (1 - guaranteed) * (1 - plain))]
    return found


def bones_for(kind: str | None, route: str) -> tuple[str, ...]:
    """The bones that Desecrate an item of `kind` by `route`."""
    if route == "breach":
        return (ALTERED,) if kind == "jewellery" else ()
    if route == "cranium":
        return (CRANIUM,) if kind == "jewel" else ()
    return BONES.get(kind or "", ())


def craft_price(item_type: str, market: float, affix, n_lord: int, n_lords: int, n_ordinary: int,
                prices: dict[str, float], route: str = "bone") -> tuple[float | None, list[str]]:
    """The cheapest craft route's expected cost (divines) and its steps; None without a priced route. `affix` has
    .desecrated (its lord), .type and .lines; n_lord: its lord's rows on its side for this base; n_lords: every lord's
    rows on its side (for "breach" / "cranium": every Desecrated row that bone can give on its side); n_ordinary: the
    base's ordinary rows on its side. `route`: "bone", "breach" (Altered Collarbone) or "cranium" (Preserved Cranium). A failed try leaves a wrong Desecrated mod, cleared
    at the cheaper of Omen of Light + Orb of Annulment and a fresh copy of the plain rare. A Desecration on a full side
    removes a random mod (R33-22): the search buys the rare with that slot open instead (`market` is its price)."""
    kind = family(item_type)
    if kind is None or n_lords < 1:
        return None, []
    bones = [(prices[b], b) for b in bones_for(kind, route) if prices.get(b) is not None]
    if not bones or any(prices.get(m) is None for m in CLEAR):
        return None, []
    bone_price, bone = min(bones)
    clear = min(sum(prices[m] for m in CLEAR), market)
    priced = []
    for name, fixed, p in routes(item_type, affix, n_lord, n_lords, n_ordinary):
        if p <= 0 or any(prices.get(m) is None for m in fixed):
            continue
        tries = 1 / p
        per_try = bone_price + sum(prices[m] for m in fixed)
        priced.append((market + tries * per_try + (tries - 1) * clear, name, tries, per_try))
    if not priced:
        return None, []
    total, name, tries, per_try = min(priced)
    lord_only = min(1.0, REVEAL / max(1, n_lord if kind in ("weapon", "jewellery") and route == "bone" else n_lords))
    steps = [f"buy the rare with that slot open ({market:.3f} div, the rare price model)",
             f"each try: {name.replace('bone', bone)} ({per_try:.3f} div); about {tries:.1f} tries: a reveal's 3 options "
             f"drawn from {n_ordinary} ordinary + {n_lords} Desecrated {affix.type.lower()}es at equal weight (RESEARCH34 R33-20: "
             f"LOW / MEDIUM-LOW)",
             f"each failed try clears the wrong mod: {clear:.3f} div (the cheaper of Omen of Light + Orb of Annulment "
             f"and a fresh copy of the rare)",
             f"reveal \"{'; '.join(affix.lines)}\" ({affix.desecrated}, {affix.type})",
             f"row: if reveals offered only Desecrated mods, one try would succeed {lord_only:.0%} of the time (the old "
             f"reading; RESEARCH34 R33-22's headline for the Altered Collarbone)"]
    if "Omen of the" in name:
        steps.append("row: the lord Omen with a Necromancy Omen failed its guarantee in three player reports (R33-21, "
                     "UNSETTLED): then the try is a plain one")
    return total, steps


MATERIALS = tuple(dict.fromkeys([b for pair in BONES.values() for b in pair] + [ALTERED, CRANIUM]
                                + list(LORD_OMEN.values())
                                + list(SIDE_OMEN.values()) + list(CLEAR) + [ECHOES]))


def payload_prices(prices) -> dict[str, float]:
    """The Desecration materials' poe.ninja exchange prices (divines); {} without price data."""
    if prices is None:
        return {}
    return {name: prices.exchange[name].divine for name in MATERIALS if name in prices.exchange}
