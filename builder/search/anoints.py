"""Anoints ("instilling", the game's word): a notable an item allocates for three Liquid Emotions.

Which items take how many (FIX_QUEUE gear item 1, the research manager-checked 11 Nov 2026):
- every amulet: 1;
- Strugglescream: 1 + "Can have 3 additional Instilled Modifiers" = 4 (UniqueMultipleAnointments1,
  local_item_can_have_x_additional_enchantments 3, checked in the game's Mods table);
- Twisted and Distorted Amulets (0.5 bases): 2, from the official 0.5.0 patch notes (MEDIUM). Re-instilling one:
  ASSUMED one random notable replaced (timesaver.gg's 0.5.4 guide; poe2wiki's Instilling page), though poe2wiki's
  Twisted Amulet page says both (RESEARCH26 R26-20, research/instilling-amulets-raven-r26.md; no official source, no
  later patch note: grep_notes "instil"). Either way the search can't craft a chosen pair onto one, so it is treated as
  bought already instilled, and the emotions' price is a floor (LOW-MEDIUM);
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
Sinister Jewel Socket) is anointed by the jewel search's own trial with the socket filled (JEWEL1: jewels.zarokh_trial).

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
# (SEARCH34: a Sinister socket that IS allocated, by an import or a request, gets a plain rare (jewels.sockets).) Zarokh's
# Gift was left out here while no trial filled the socket it grants; JEWEL1's Jewels.zarokh_trial now anoints it with
# the socket filled, and gear's own anoint trial then scores it with the jewel in place (PoB counts a jewel only while its
# socket node is allocated, CalcSetup 1264). Gear's screen still scores it with the socket empty, so it never adds it
# itself: the jewel trial does
LEFT_OUT: dict[str, str] = {}
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
    # No implicits: after the WHOLE header (JEWEL6, the Black Insignia bug), before the first mod line
    at = next((i + 1 for i, l in enumerate(lines) if l.startswith("Item Level:")), None)
    if at is None:
        at = 2 if not lines[0].startswith("Rarity:") or lines[0] == "Rarity: Normal" else 3
        at = min(at, len(lines))
    base = lines[at - 1] if at else ""
    while at < len(lines) and _header_line(lines[at], base):
        at += 1
    return "\n".join(lines[:at] + [f"Implicits: {len(notables)}"] + [f"{ANOINT_LINE}{n}" for n in notables] + lines[at:])


# (JEWEL6, 27 Nov: the Black Insignia bug, SEARCHC6 / SEARCHC7) PoB's item header lines ("<name>: <value>", Classes/Item.lua
# parseItemSpec, the names its ParseRaw reads) and a unique's "{base:N}<base name>" lines. A unique's text from PoB's
# catalogue holds them after its name ("Evasion", "Base Variant" x3, "Selected Base Variant", "{base:2}Runeforged Corsair
# Cap", "Quality", "LevelReq", "Implicits: 0"). gear.anointed strips the anoint first (strip: an item whose only implicit is
# its anoint loses its "Implicits:" line), and the re-anoint here then put "Implicits: 1" + the anoint right after the base
# line, so the whole header fell into the explicit section. PoB kept those lines as explicit mod lines when it wrote the
# item back, while its selection pre-pass (Item.lua 583-610, over every line) added that block's "Base Variant" lines to
# baseList and its "Rune:" line to the runes again on every re-equip of PoB's own text: the helmet grew by one triple and
# one Raven-Touched Shard each time (41 / 34 / 80 shards on 2b75e seed 3; The Vertex 22 on 2b75d, 12 Nov), until PoB's
# "exceeding augment limit" warning made the gate run infeasible.
HEADER_SPECS = frozenset((
    "Allow Duplicate Variants", "Armour", "Attacks per Second", "Base Variant", "Block chance", "Catalyst", "CatalystQuality",
    "Chance to Block", "Chaos Damage", "Charm Slots", "Cluster Jewel Node Count", "Cluster Jewel Skill", "Cold Damage",
    "Crafted", "Critical Hit Chance", "Critical Hit Range", "Elemental Damage", "Energy Shield", "Evasion Rating", "Evasion",
    "Fire Damage", "Has Alt Variant", "Has Alt Variant Two", "Has Alt Variant Three", "Has Alt Variant Four",
    "Has Alt Variant Five", "Has Variants", "Item Level", "League", "Level", "LevelReq", "Lightning Damage", "Limited to",
    "Note", "Physical Damage", "Prefix", "Quality", "Radius", "Reload Time", "Requires Class", "Requires Level", "Requires",
    "Rune", "Runic Ward", "Selected Alt Variant", "Selected Alt Variant Two", "Selected Alt Variant Three",
    "Selected Alt Variant Four", "Selected Alt Variant Five", "Selected Base Variant", "Selected Variant Group",
    "Selected Variant", "Selected Variants", "Selected Version", "Sockets", "Source", "Spirit", "Suffix", "Talisman Tier",
    "Unique ID", "Unreleased", "Upgrade", "Variant", "Version", "Ward", "Weapon Range"))
_SPEC = re.compile(r"^([A-Za-z ()]+:?): (.+)$")
_TAGGED = re.compile(r"^(?:\{[^}]*\})+(.+)$")


def _header_line(line: str, base: str) -> bool:
    """Is this an item header line (a PoB spec, or a tagged base-name line of the item's base)?"""
    if (m := _SPEC.match(line)) and m.group(1) in HEADER_SPECS:
        return True
    return bool(base) and bool(m := _TAGGED.match(line)) and m.group(1).strip().endswith(base.strip())
