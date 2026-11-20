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
lord's Omen (refused on Armour, Shields and Foci).
THE DETERMINISTIC ROUTE (SEARCH31; the user, 19 Nov: "desecrated mods are relatively straightforward to craft and can be
made deterministically with enough currency"): try, reveal, and on a miss remove only the Desecrated mod (Omen of Light:
"your next Orb of Annulment will remove only Desecrated modifiers") or start on a fresh copy, until the wanted mod comes:
nothing else on the item is ever lost, so the cost is the expected number of tries. With a lord Omen ("will guarantee a
random <lord> modifier") the three options are that lord's rows on the forced side (poe2db: reveals "may include base
modifiers. Unless you use Omen to guarantee named modifiers"), p = min(1, 3 / n_lord) (MEDIUM; `named`); RESEARCH34's
reading (one option named, two from the whole side) and the Omen pair's reported failure (R33-21) are rows. Without a
lord Omen (Armour, Shields, Foci, the Altered Collarbone, the Cranium: no Omen names their rows) a reveal's options are
drawn from the side's ORDINARY affixes and every Desecrated row the bone gives, each at weight 1 (LOW: real weights
hidden; R33-20); "only Desecrated mods offered" is a row.
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


LORD_SHARE = 1.16  # lord options per un-Omened reveal (RESEARCH35 R35-14: 563 ring reveals, 85% one / 14% two / 1% three)


def plain_odds(route: str, n_lords: int, n_all: int) -> float:
    """One reveal without a lord Omen offering a wanted row. A plain bone (lord rows): at least one option is a lord row
    (R35-14 MEDIUM, and the user 20 Nov: "I don't know that I've ever seen a desecration offer only the standard mods"),
    LORD_SHARE lord options a reveal on average, so p ~ 1.16 / n (n = the lord rows the side can take). The Altered
    Collarbone / the Cranium: equal weights over the side's ordinary + Desecrated rows (R35-10, LOW)."""
    if route == "bone":
        return min(1.0, LORD_SHARE / n_lords) if n_lords > 0 else 0.0
    return offered(n_all, REVEAL)


def routes(item_type: str, affix, n_lord: int, n_lords: int, n_ordinary: int, route: str = "bone"
           ) -> list[tuple[str, list[str], float]]:
    """(name, fixed materials per try besides the bone, chance one try offers the wanted row). The Necromancy Omen fixes
    the side in every route (R35-9); Omen of Abyssal Echoes rerolls the three once: 1 - (1 - p)^2. With a lord Omen
    (Weapons / Jewellery, plain bones): `named`."""
    side = [SIDE_OMEN[affix.type]]
    n_all = n_ordinary + n_lords
    plain = plain_odds(route, n_lords, n_all)
    found = [("bone + Necromancy Omen", side, plain),
             ("bone + Necromancy Omen + Omen of Abyssal Echoes", side + [ECHOES], 1 - (1 - plain) ** 2)]
    if route == "bone" and family(item_type) in ("weapon", "jewellery") and n_lord > 0 and affix.desecrated in LORD_OMEN:
        omen = LORD_OMEN[affix.desecrated]
        guaranteed = named(n_lord, n_all)
        found += [(f"bone + Necromancy Omen + {omen}", side + [omen], guaranteed),
                  (f"bone + Necromancy Omen + {omen} + Omen of Abyssal Echoes", side + [omen, ECHOES],
                   1 - (1 - guaranteed) ** 2)]
    return found


def named(n_lord: int, n_all: int) -> float:
    """With a lord Omen ONE option is a random row of that lord on the forced side and the other two are normal draws from
    the side's ordinary + lord rows (THE USER, in game 20 Nov: "only one is guaranteed ... I got two amanamu mod choices
    using an omen of the liege on a wand"; overrides R35-13's all-three): p = 1 - (1 - 1/n_lord)(1 - 1/N)^2 (MEDIUM)."""
    if n_lord <= 0:
        return 0.0
    return 1 - (1 - 1 / n_lord) * (1 - 1 / max(1, n_all)) ** (REVEAL - 1)


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
    for name, fixed, p in routes(item_type, affix, n_lord, n_lords, n_ordinary, route):
        if p <= 0 or any(prices.get(m) is None for m in fixed):
            continue
        tries = 1 / p
        per_try = bone_price + sum(prices[m] for m in fixed)
        priced.append((market + tries * per_try + (tries - 1) * clear, name, tries, per_try))
    if not priced:
        return None, []
    total, name, tries, per_try = min(priced)

    def cost(p: float) -> float:
        return market + per_try / p + (1 / p - 1) * clear if p > 0 else float("inf")

    side = affix.type.lower()
    omened = "Omen of the" in name
    echoes = ECHOES in name
    clear_how = "Omen of Light + Orb of Annulment" if clear < market else "a fresh copy of the rare instead"
    n_all = n_ordinary + n_lords
    twice = (lambda p: 1 - (1 - p) ** 2) if echoes else (lambda p: p)
    if omened:
        odds = (f"the lord Omen makes one option a {affix.desecrated} {side} ({n_lord} the item can take) and the other two "
                f"normal draws from the side's {n_ordinary} ordinary + {n_lords} lord {side}es, so one reveal offers the "
                f"wanted one {named(n_lord, n_all):.0%} of the time" + (", more with the Echoes reroll" if echoes else "")
                + " (MEDIUM: the user's in-game check, 20 Nov)")
    elif route == "bone":
        odds = (f"no lord Omen here (they are 'Weapon or Jewellery' only), but a reveal offers at least one lord {side} and "
                f"{LORD_SHARE} on average (RESEARCH35 R35-14; the user's experience): {n_lords} lord {side}es the item can "
                f"take, so one reveal offers the wanted one {plain_odds(route, n_lords, n_all):.0%} of the time (MEDIUM)")
    else:
        odds = (f"no Omen names these rows, so the three options are drawn from the side's {n_ordinary} ordinary + "
                f"{n_lords} Desecrated {side}es at equal weight (LOW: RESEARCH35 R35-10)")
    steps = [f"1. buy the rare with its {side} slot open ({market:.3f} div, the rare price model; a Desecration on a full "
             f"side also removes a random mod)",
             f"2. each try ({per_try:.3f} div): {name.replace('bone', bone)}, then reveal at the Well of Souls; {odds}",
             f"3. on a miss, remove the wrong Desecrated mod with {clear_how} ({clear:.3f} div; Omen of Light: 'your next "
             f"Orb of Annulment will remove only Desecrated modifiers'), and repeat from 2: about {tries:.1f} tries, "
             f"{total:.3f} div in all",
             f"4. pick \"{'; '.join(affix.lines)}\" ({affix.desecrated}, {affix.type})"]
    if omened:
        steps.append("row: the lord Omen with a Necromancy Omen failed its guarantee in three 0.3-era player reports "
                     f"(R33-21, UNSETTLED): then a try is a plain one ({cost(twice(plain_odds(route, n_lords, n_all))):.3f} div)")
    if route == "bone":
        steps.append(f"row: if a reveal could offer only ordinary mods (equal weights over all {n_all}): "
                     f"{cost(twice(offered(n_all, REVEAL))):.3f} div")
    only = min(1.0, REVEAL / max(1, n_lords))
    steps.append(f"row: if reveals offered only Desecrated mods: {cost(twice(only)):.3f} div")
    return total, steps


MATERIALS = tuple(dict.fromkeys([b for pair in BONES.values() for b in pair] + [ALTERED, CRANIUM]
                                + list(LORD_OMEN.values())
                                + list(SIDE_OMEN.values()) + list(CLEAR) + [ECHOES]))


def payload_prices(prices) -> dict[str, float]:
    """The Desecration materials' poe.ninja exchange prices (divines); {} without price data."""
    if prices is None:
        return {}
    return {name: prices.exchange[name].divine for name in MATERIALS if name in prices.exchange}
