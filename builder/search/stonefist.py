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


def transform(raw: str, mod_pools: dict) -> tuple[str, list[str]]:
    """A gloves item's text as Way of the Stonefist makes it: (text, lines kept untransformed). The header (rarity,
    name, base), quality, sockets and runes stay; the base becomes Fists of Stone; implicits become the base's
    per-level pair (a unique's from poe2db's text); explicits are transformed."""
    lines = raw.split("\n")
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
        head[base_at] = BASE
    granted = [line for line in implicits if line.startswith("Grants Skill:")]
    if unique is not None:
        new_implicits = unique["implicit"] if any(l.startswith("Has ") for l in unique["implicit"]) else \
            [l for l in unique["implicit"] if l.startswith("Grants Skill:")] + IMPLICITS
        new_explicits, kept = list(unique["explicit"]), []
    else:
        new_implicits = granted + IMPLICITS
        new_explicits, kept = transform_lines(explicits, mod_pools)
    text = head + [f"Implicits: {len(new_implicits)}", *new_implicits, *new_explicits]
    return "\n".join(text), kept
