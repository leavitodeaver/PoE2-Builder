"""Desecrated (Abyssal lord) affixes on rares (SEARCH30; plan scratch/SEARCH28/plan_unrolled_mods.md part 1, RESEARCH32
R-a desecrated-mod-rules.md and R-e pricing-desecrated-influence-rares.md).

What the game allows (R-a): one Desecrated modifier per item (0.5.0, HIGH); it takes a prefix or suffix slot (HIGH);
the lord mods are item level 65 (PoB ModVeiled: the Amanamu / Kurgal / Ulaman rows are level 65, MEDIUM); a bone of the
item's class adds it unrevealed and the reveal offers three options (their make-up UNVERIFIED); on Weapons and Jewellery
an Omen fixes the lord (Sovereign = Ulaman, Liege = Amanamu, Blackblooded = Kurgal, HIGH), on Armour none does; Omens of
Sinistral / Dextral Necromancy force a prefix / suffix (HIGH).

The pool: ctx.data.mods["Desecrated"] (PoB ModVeiled.lua, 388 rows), the rows whose id / modTags name a lord (198); the
others (jewel and waystone rows, Lich / Breach-lord rows) are not offered: how a rare gets them is UNVERIFIED (an
UNDERCOUNT if a bone can give them, said here). A row's lord is read from its id and modTags: some rows' weightKey names
another lord's tag (AbyssMod1HMaceAmanamuPrefix... has ulaman_mod 1 in its weightKey, amanamu_mod in its modTags); the
id, the affix name and the modTags agree, so they win (MEDIUM).

The price (R-e: no market price level found, UNVERIFIED) is THE CRAFT ROUTE at today's cached prices (MEDIUM): the
ordinary affixes at the market model's price + per try a bone (the cheapest Preserved / Ancient one of the class; a
Gnawed bone caps at item level 64, below the lord mods) + the Necromancy Omen for the side + on Weapons / Jewellery the
lord's Omen; the chance one reveal offers the wanted mod p = min(1, 3 / n), n = the mods of that side the base can take
from that lord (Weapons / Jewellery) or from all three (Armour: no Omen picks the lord, the lower reading); expected
tries 1 / p, and each failed try also pays to clear the wrong mod: the cheaper of Omen of Light + Orb of Annulment and a
fresh copy of the plain rare. Omen of Abyssal Echoes (one re-roll of the reveal) is not used (the lower reading). Whether
two Omens work on one Desecration is UNVERIFIED. A missing price means no offer.

OFF by default until a 3-run gate set measures it higher (the manager's rule): POE2_SEARCH_DESECRATED=1 turns it on.
"""

from __future__ import annotations

import os
import re

LORDS = ("Amanamu", "Kurgal", "Ulaman")
LORD_OMEN = {"Ulaman": "Omen of the Sovereign", "Amanamu": "Omen of the Liege", "Kurgal": "Omen of the Blackblooded"}
SIDE_OMEN = {"Prefix": "Omen of Sinistral Necromancy", "Suffix": "Omen of Dextral Necromancy"}
CLEAR = ("Omen of Light", "Orb of Annulment")
REVEAL = 3  # options one reveal offers (0.3.0 notes: "one of three different options")
WEAPON_TYPES = {"Bow", "Crossbow", "Quiver", "Wand", "Staff", "Sceptre", "Spear", "Flail", "Talisman", "Claw", "Dagger",
                "One Hand Mace", "Two Hand Mace", "One Hand Sword", "Two Hand Sword", "One Hand Axe", "Two Hand Axe"}
# (Shields and Foci take a Rib as Armour: UNVERIFIED, poe2db's bone text says "Armour"; their rows' weightKeys still decide
# which mods they can take)
ARMOUR_TYPES = {"Helmet", "Body Armour", "Gloves", "Boots", "Shield", "Focus"}
JEWELLERY_TYPES = {"Ring", "Amulet", "Belt"}
BONES = {"weapon": ("Preserved Jawbone", "Ancient Jawbone"), "armour": ("Preserved Rib", "Ancient Rib"),
         "jewellery": ("Preserved Collarbone", "Ancient Collarbone")}
LORD = re.compile(r"(Amanamu|Kurgal|Ulaman)")


def enabled() -> bool:
    return os.environ.get("POE2_SEARCH_DESECRATED", "0") == "1"


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
    if item_type in JEWELLERY_TYPES:
        return "jewellery"
    if item_type in ARMOUR_TYPES:
        return "armour"
    if item_type in WEAPON_TYPES:
        return "weapon"
    return None


def prices_needed(item_type: str, lord_name: str, side: str) -> list[str]:
    """The fixed materials of one try (besides the bone)."""
    needed = [SIDE_OMEN[side]]
    if family(item_type) in ("weapon", "jewellery"):
        needed.append(LORD_OMEN[lord_name])
    return needed


def craft_price(item_type: str, market: float, affix, rivals: int, prices: dict[str, float]
                ) -> tuple[float | None, list[str]]:
    """The craft route's expected cost (divines) and its steps; None without a price. `affix` has .desecrated (its lord),
    .type and .lines; `rivals` = n, the mods of its side the base can take from its lord (Weapons / Jewellery) or from
    every lord (Armour), itself included."""
    kind = family(item_type)
    if kind is None or rivals < 1:
        return None, []
    bones = [(prices[b], b) for b in BONES[kind] if prices.get(b) is not None]
    fixed = prices_needed(item_type, affix.desecrated, affix.type)
    if not bones or any(prices.get(m) is None for m in fixed) or any(prices.get(m) is None for m in CLEAR):
        return None, []
    bone_price, bone = min(bones)
    per_try = bone_price + sum(prices[m] for m in fixed)
    p = min(1.0, REVEAL / rivals)
    tries = 1 / p
    clear = min(sum(prices[m] for m in CLEAR), market)
    total = market + tries * per_try + (tries - 1) * clear
    steps = [f"buy the rare with its other modifiers ({market:.3f} div, the rare price model)",
             f"each try: {bone} + {' + '.join(fixed)} ({per_try:.3f} div); a reveal offers {REVEAL} of {rivals} "
             f"{affix.desecrated if kind != 'armour' else 'lord'} {affix.type.lower()}es: about {tries:.1f} tries "
             f"(the reveal's make-up UNVERIFIED: MEDIUM-LOW)",
             f"each failed try clears the wrong mod: {clear:.3f} div (the cheaper of Omen of Light + Orb of Annulment "
             f"and a fresh copy of the rare)",
             f"reveal \"{'; '.join(affix.lines)}\" ({affix.desecrated}, {affix.type})"]
    return total, steps


MATERIALS = tuple(dict.fromkeys([b for pair in BONES.values() for b in pair] + list(LORD_OMEN.values())
                                + list(SIDE_OMEN.values()) + list(CLEAR)))


def payload_prices(prices) -> dict[str, float]:
    """The Desecration materials' poe.ninja exchange prices (divines); {} without price data."""
    if prices is None:
        return {}
    return {name: prices.exchange[name].divine for name in MATERIALS if name in prices.exchange}
