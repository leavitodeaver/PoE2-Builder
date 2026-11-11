"""Anoints ("instilling", the game's word): a notable an item allocates for three Liquid Emotions.

Which items take how many (FIX_QUEUE gear item 1, the research manager-checked 11 Nov 2026):
- every amulet: 1;
- Strugglescream: 1 + "Can have 3 additional Instilled Modifiers" = 4 (UniqueMultipleAnointments1,
  local_item_can_have_x_additional_enchantments 3, checked in the game's Mods table);
- Twisted and Distorted Amulets (0.5 bases): 2, from the official 0.5.0 patch notes (MEDIUM). How re-instilling them
  works is UNVERIFIED: they are treated as bought already instilled, at the emotions' price (LOW);
- an item with the "Raven-Touched" line (The Auspex, Horror's Flight: UniqueCanBeInstilled, local_item_can_be_instilled):
  1. Whether every copy has the line is UNVERIFIED: the line is in PoB's text of both, so the item what-if needs it;
- a helmet with a Raven-Touched Shard in an augment socket: 1 (Gear.shard_trial; runes.json: helmet only, levelReq 60,
  limit 1 per character; only on a helmet that is neither Corrupted nor Sanctified, from FIX_QUEUE's research, MEDIUM).
  An item that is already Raven-Touched gains no second anoint from a shard (UNVERIFIED; not tried, a possible
  undercount).
None on belts, rings, boots, shields or weapons (checked by the research: SoulCores' Raven-Touched Shard row has
CanSocketInJewellery False; UniqueCanBeInstilled is the only mod with local_item_can_be_instilled; manager-checked in
the game files, 11 Nov). PoB reads an "Allocates <notable>" line on any item as that passive granted (ModParser
"allocates (.+)", CalcSetup's granted passives), so the anoint is that line in the item's text.

Recipes: the 875 nodes PoB's 0.5 tree marks with a `recipe` (874 notables and Zarokh's Gift, a jewel socket; three
emotions each, all different combinations), 17 of
them anoint-only (not on the tree's graph: Bastion of Light, Paragon, Magnum Opus, ...). Source: PoB's
TreeData/<version>/tree.lua (MEDIUM: the game's BlightCraftingRecipes tables are added to the game-file export, but
Steam was updating the game on 11 Nov and the export refused, so they aren't cross-checked yet). Zarokh's Gift (a
Sinister Jewel Socket) is left out, labelled: the search puts no jewel in a socket an item grants.

Prices: each emotion's poe.ninja exchange price (Delirium overview), summed over the recipe. The "Ancient" emotions
(Metadata ...TimeLost) are not taken as substitutes (UNVERIFIED whether recipes accept them).
"""

from __future__ import annotations

import re
from functools import lru_cache

from builder import POB_DIR

# A recipe's short names, as PoB's tree writes them, and the currency item each one is (PoB's CurrencyNames.lua,
# Metadata/Items/Currency/DistilledEmotion1-10 and EndgameDistilledEmotion1-3)
EMOTIONS = {
    "Ire": "Diluted Liquid Ire", "Guilt": "Diluted Liquid Guilt", "Greed": "Diluted Liquid Greed",
    "Paranoia": "Liquid Paranoia", "Envy": "Liquid Envy", "Disgust": "Liquid Disgust", "Despair": "Liquid Despair",
    "Fear": "Concentrated Liquid Fear", "Suffering": "Concentrated Liquid Suffering",
    "Isolation": "Concentrated Liquid Isolation",
    "Melancholy": "Potent Liquid Melancholy", "Ferocity": "Potent Liquid Ferocity", "Contempt": "Potent Liquid Contempt",
}
LEFT_OUT = {"Zarokh's Gift": "grants a Sinister Jewel Socket; the search puts no jewel in a socket an item grants"}
TWO_ANOINT_BASES = {"Twisted Amulet", "Distorted Amulet"}  # 0.5.0 patch notes (MEDIUM)
# Tagged lines too: PoB's text of Strugglescream holds both variants' ({variant:1} 2, {variant:2} 3), and PoB takes the
# last variant (Current) when none is selected, so the last such line counts
ADDITIONAL = re.compile(r"^(?:\{[^}]*\})*Can have (\d+) additional Instilled Modifiers?\s*$", re.M)
RAVEN_TOUCHED = re.compile(r"^Raven-Touched\s*$", re.M)
SHARD = "Raven-Touched Shard"
SEALED = re.compile(r"^(?:Corrupted|Sanctified)\s*$", re.M)  # an item a shard can't go into
ANOINT_LINE = "{enchant}Allocates "
RECIPE = re.compile(r'recipe=\{\s*\[1\]="(\w+)",\s*\[2\]="(\w+)",\s*\[3\]="(\w+)"\s*\},[^}]*?\bskill=(\d+),')


@lru_cache(maxsize=4)
def recipes(tree_version: str) -> dict[int, tuple[str, str, str]]:
    """Node id -> its three emotions' short names, from PoB's tree file."""
    path = POB_DIR / "src" / "TreeData" / tree_version / "tree.lua"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return {int(m.group(4)): (m.group(1), m.group(2), m.group(3)) for m in RECIPE.finditer(text)}


def emotion_prices(prices) -> dict[str, float]:
    """Each emotion's divine price by item name (empty without price data)."""
    if prices is None:
        return {}
    return {name: prices.exchange[name].divine for name in EMOTIONS.values() if name in prices.exchange}


def cost(recipe: tuple[str, ...], prices: dict[str, float]) -> float | None:
    """The recipe's divines, or None when an emotion has no price."""
    total = 0.0
    for short in recipe:
        price = prices.get(EMOTIONS.get(short, ""))
        if price is None:
            return None
        total += price
    return total


def capacity(option, base_type: str | None = None) -> int:
    """How many anoints the item takes (see the module's docstring)."""
    if option.type == "Amulet":
        extra = ADDITIONAL.findall(option.raw)
        if extra:
            return 1 + int(extra[-1])
        return 2 if option.base in TWO_ANOINT_BASES else 1
    if RAVEN_TOUCHED.search(option.raw) or (option.type == "Helmet" and SHARD in option.runes):
        return 1
    return 0


def strip(raw: str) -> str:
    """The item's text without anoints."""
    return with_anoints(raw, [])


def with_anoints(raw: str, notables: list[str]) -> str:
    """The item's text with these anoints (any old ones replaced), as enchant lines counted among its implicits."""
    lines = raw.splitlines()
    at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
    if at is not None:
        count = int(lines[at].split(":", 1)[1])
        implicits = lines[at + 1:at + 1 + count]
        kept = [l for l in implicits if not l.startswith(ANOINT_LINE)]
        new = [f"{ANOINT_LINE}{name}" for name in notables] + kept
        rest = lines[at + 1 + count:]
        head = lines[:at]
        return "\n".join(head + ([f"Implicits: {len(new)}"] + new if new else []) + rest)
    if not notables:
        return raw
    # No implicits: after the header (as rares.with_runes places sockets) and any socket lines
    at = next((i + 1 for i, l in enumerate(lines) if l.startswith("Item Level:")), None)
    if at is None:
        at = 2 if not lines[0].startswith("Rarity:") or lines[0] == "Rarity: Normal" else 3
        at = min(at, len(lines))
    while at < len(lines) and lines[at].startswith(("Sockets:", "Rune:")):
        at += 1
    return "\n".join(lines[:at] + [f"Implicits: {len(notables)}"] + [f"{ANOINT_LINE}{n}" for n in notables] + lines[at:])
