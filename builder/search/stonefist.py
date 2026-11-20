"""Way of the Stonefist (COV34; the manager, 15 Nov 15:19): the Martial Artist notable transforms equipped gloves.

Tree text: "Gloves you equip have their Base Type transformed to Fists of Stone while equipped, and their Explicit
Modifiers are transformed into more powerful related Modifiers" + "Ignore Attribute Requirements to equip Gloves".
Neither PoB nor the tool did it (PoB has the Fists of Stone base and the HandWraps mods; Item.lua 727 only guards
their names), so a build with the notable was scored on plain gloves: build-dependent, an overcount on the test
skeletons (probe POE2T2570: Tempest Flurry Full DPS 189 -> 148, Total EHP 1329 -> 1202 with a typical rare pair).

The map (game files, build 26476662, data/game-26476662 Mods): 588 of the 598 "HandWraps*" mods are named "HandWraps" +
the regular mod's id (HandWrapsStrength1 <- Strength1); no link column in our export, so the name is the map
(UNVERIFIED that the game keeps no other table: to recheck in a newer export). PoB's mod pools carry the pairs with
their texts. A line is matched to its regular mod by its text with the numbers taken out, the tier whose range holds
its value; the HandWraps mod's lines are rolled at the same place in their ranges. A line with no pair stays as it is
(named in `unmatched`). Uniques: poe2db's 37 transformed texts (builder/data/stonefist_uniques.json).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

NOTABLE = "Way of the Stonefist"
BASE = "Fists of Stone"
# PoB Data/Bases/gloves.lua 2039 (the game's FourGlovesDexIntAscendancy implicits)
IMPLICITS = ["Has +3 to Evasion Rating per player level", "Has +1 to maximum Energy Shield per player level"]
UNIQUES_FILE = Path(__file__).resolve().parents[1] / "data" / "stonefist_uniques.json"
PREFIX = "HandWraps"
NUMBER = re.compile(r"\(?(-?\d+(?:\.\d+)?)(?:-(-?\d+(?:\.\d+)?))?\)?")


def template(line: str) -> str:
    """A line with its numbers and ranges as '#' (the key both texts share)."""
    return NUMBER.sub("#", line.strip())


def _numbers(line: str) -> list[tuple[float, float]]:
    """Each number or (low-high) range of a line, as (low, high)."""
    return [(float(a), float(b or a)) for a, b in NUMBER.findall(line.strip())]


def _lines(mod: dict) -> list[str]:
    return [mod[key] for key in sorted((k for k in mod if k.isdigit()), key=int)]


_PAIRS: dict[int, tuple[object, dict]] = {}


def pairs(mod_pools: dict) -> dict[tuple[str, ...], list[tuple[dict, dict]]]:
    """(the regular mod's line templates) -> [(regular mod, its HandWraps mod)], over every pool (cached per pools)."""
    cached = _PAIRS.get(id(mod_pools))
    if cached is not None and cached[0] is mod_pools:
        return cached[1]
    by_id = {m["id"]: m for mods in mod_pools.values() for m in (mods if isinstance(mods, list) else mods.values())}
    found: dict[tuple[str, ...], list[tuple[dict, dict]]] = {}
    for mod_id, mod in by_id.items():
        if mod_id.startswith(PREFIX) and mod_id[len(PREFIX):] in by_id:
            regular = by_id[mod_id[len(PREFIX):]]
            found.setdefault(tuple(template(line) for line in _lines(regular)), []).append((regular, mod))
    _PAIRS[id(mod_pools)] = (mod_pools, found)
    return found


def _place(lines: list[str], regular: dict) -> float | None:
    """Where the item's values sit in the regular mod's ranges (0-1), or None when a value is outside them."""
    places = []
    for have, mod_line in zip(lines, _lines(regular)):
        for value, (low, high) in zip([v for v, _ in _numbers(have)], _numbers(mod_line)):
            if not min(low, high) - 1e-9 <= value <= max(low, high) + 1e-9:
                return None
            if high != low:
                places.append((value - low) / (high - low))
    return sum(places) / len(places) if places else 0.5


def roll(text: str, fraction: float) -> str:
    """Every (low-high) range of a line fixed at `fraction` of the way up (rares.roll's rounding)."""
    def value(match: re.Match) -> str:
        low, high = float(match.group(1)), float(match.group(2))
        number = low + fraction * (high - low)
        return f"{number:.1f}" if "." in match.group(1) or "." in match.group(2) else str(int(round(number)))
    return re.sub(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)", value, text)


def _preference(mod: dict) -> int:
    """Which of several mods with the same text a rare's line is: one that rolls on gloves first (PoB's weightKey /
    weightVal), then any non-unique mod, then a unique's (whose texts are poe2db's anyway: unique_texts)."""
    weights = dict(zip(mod.get("weightKey") or [], mod.get("weightVal") or []))
    if weights.get("gloves", 0) > 0:
        return 0
    return 2 if mod["id"].startswith("Unique") else 1


def transform_lines(explicits: list[str], mod_pools: dict) -> tuple[list[str], list[str]]:
    """(the transformed explicit lines, the lines no pair matched, kept as they are)."""
    index = pairs(mod_pools)
    longest = max((len(k) for k in index), default=1)
    out, unmatched, i = [], [], 0
    while i < len(explicits):
        for size in range(min(longest, len(explicits) - i), 0, -1):
            chunk = explicits[i:i + size]
            options = index.get(tuple(template(line) for line in chunk))
            placed = [(_preference(regular), p, hw) for regular, hw in options or []
                      if (p := _place(chunk, regular)) is not None]
            if placed:
                _, fraction, hand_wraps = min(placed, key=lambda x: x[0])
                out += [roll(line, fraction) for line in _lines(hand_wraps)]
                i += size
                break
        else:
            out.append(explicits[i])
            unmatched.append(explicits[i])
            i += 1
    return out, unmatched


@lru_cache(maxsize=1)
def unique_texts() -> dict[str, dict]:
    """poe2db's transformed uniques by name: {"implicit": [...], "explicit": [...]}."""
    if not UNIQUES_FILE.exists():
        return {}
    return {u["name"]: u for u in json.loads(UNIQUES_FILE.read_text(encoding="utf-8"))["uniques"]}


def ledger_corrected(explicits: list[str], name: str) -> list[str]:
    """A transformed unique's explicit lines with the ledger's corrections (COV46). The unique's own corrections
    (context.corrected_raw) were made on its untransformed text, which the transform drops, so its hand-wraps lines got
    none: Painter's Servant's "Physical damage from Hits Contributes to ..." counted 0 (MECH91). As corrected_raw and
    ledger.items.corrected_item do: a line PoB can't parse gets its gap entry's stand-ins (added at the end), a line PoB
    gets wrong its pob_wrong replacement (or is switched off)."""
    from builder.ledger import item_lines, translate, wrong
    from builder.ledger.items import template as item_template, unparsed_templates

    unparsed = unparsed_templates()
    out, added = [], []
    for line in explicits:
        if item_template(line) in unparsed:
            if (translated := translate(line)) and translated[0].on_items:
                added += item_lines(translated[1])
        elif fixed := wrong(line, name):
            line = fixed[1] if fixed[1] is not None else "{disabled}" + line
        out.append(line)
    return out + added


def transform(raw: str, mod_pools: dict) -> tuple[str, list[str]]:
    """A gloves item's text as Way of the Stonefist makes it: (text, lines kept untransformed). The header (rarity,
    name, base), quality, sockets and runes stay; the base becomes Fists of Stone; implicits become the base's
    per-level pair (a unique's from poe2db's text); explicits are transformed."""
    from builder.ledger.items import ADDED, corrected_item

    # (COV46) the ledger's stand-ins stood for the untransformed lines: dropped here; the transformed text gets its
    # corrections again (a rare's by corrected_item at the end, a unique's in ledger_corrected)
    lines = [line for line in raw.split("\n") if not line.startswith(ADDED)]
    head_end = next((k for k, line in enumerate(lines) if line.startswith("Implicits:")), None)
    if head_end is None:
        return raw, []
    count = int(lines[head_end].split(":")[1])
    head, implicits, explicits = lines[:head_end], lines[head_end + 1:head_end + 1 + count], \
        [line for line in lines[head_end + 1 + count:] if line.strip()]
    unique = next((unique_texts()[line] for line in head if line in unique_texts()), None)
    rarity_at = 1 if head and head[0].startswith("Rarity:") else 0
    # the base line: the line after the name for a rare / unique (PoB's raw order)
    base_at = rarity_at + 1 if len(head) > rarity_at + 1 else None
    if base_at is not None:
        if head[base_at] == BASE:
            return raw, []  # already transformed (a restored or re-equipped item): never twice
        head[base_at] = BASE
    granted = [line for line in implicits if line.startswith("Grants Skill:")]
    if unique is not None:
        new_implicits = unique["implicit"] if any(l.startswith("Has ") for l in unique["implicit"]) else \
            [l for l in unique["implicit"] if l.startswith("Grants Skill:")] + IMPLICITS
        new_explicits, kept = ledger_corrected(list(unique["explicit"]), unique["name"]), []
    else:
        new_implicits = granted + IMPLICITS
        new_explicits, kept = transform_lines(explicits, mod_pools)
    text = "\n".join(head + [f"Implicits: {len(new_implicits)}", *new_implicits, *new_explicits])
    return (text if unique is not None else corrected_item(text)), kept


def node_id(tree: dict) -> int | None:
    """The notable's node id in the tool's tree data."""
    return next((n["id"] for n in tree.get("nodes") or [] if n.get("name") == NOTABLE and n.get("ascendancy")), None)


def item_kwargs(method: str, kwargs: dict, mod_pools: dict) -> dict:
    """An engine call's arguments with the Gloves item transformed: "equip" (slot Gloves, a raw) and the what-if
    methods' cases on Gloves. Other calls unchanged."""
    if method == "equip" and kwargs.get("slot") == "Gloves" and kwargs.get("raw"):
        return {**kwargs, "raw": transform(kwargs["raw"], mod_pools)[0]}
    if method == "itemDeltas" and any(c.get("slot") == "Gloves" and c.get("raw") for c in kwargs.get("cases") or []):
        cases = [{**c, "raw": transform(c["raw"], mod_pools)[0]} if c.get("slot") == "Gloves" and c.get("raw") else c
                 for c in kwargs["cases"]]
        return {**kwargs, "cases": cases}
    return kwargs

