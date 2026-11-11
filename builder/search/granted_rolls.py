"""The level of a skill a unique grants ("Grants Skill: Level (1-20) Thundergod's Wrath"), priced per level from the
official trade site (the user, 8 Nov 2026: "Needed roll, trade-priced").

Which uniques: every unique in PoB's export (data/pob-*/uniques.json) with a "Grants Skill: Level (a-b)" line, 62 in
build bb52d6b368, all (1-20). The game files back the list's skills, not its levels: ModGrantedSkills (65 mods) names
each unique's granted skill, but no exported table (55 of the schema's 1,550, ItemInherentSkills and Mods' stat and
GrantedEffectsPerLevel columns searched) holds a level or a range for them, and poe2db shows one fixed level per unique
(Mjolner 15, Forgotten Warden 16, Svalinn 14: the level of a gem of the unique's level requirement).

What sets it: the item's level, not a free roll (trade listings, 10 Nov 2026, Forbidden Rites): Mjolner at item level
79-81 grants level 18, at 82 and 84 level 19; every level-20 copy of 21 listed was corrupted (a Vaal implicit), asking
40-165 div against 1 chaos for level 18 or 19. So the cheapest copy is often a high level, and PoB's middle roll (11)
undercounts: once priced, the search offers each level the trade site sells at that level's price, and keeps the
middle roll, labelled, only until then.

A unique granting two skills at rolled levels (Dusk Vigil's Firebolt and Ember Fusillade, Earthbound) is priced by its
first one's level; the others are ASSUMED to follow the same item level, so they get the same level.
"""

from __future__ import annotations

import re

from builder.search import models

# The label a priced level is kept under in builder/data/unique_versions.py and carried as ItemOption.variants
LABEL = re.compile(r"^Grants Skill: Level (\d+)\+ (.+)$")
LISTED_LEVEL = r"Level (\d+) %s\s*$"


def rolled(raw: str) -> list[tuple[str, int, int]]:
    """(skill, low, high) for each 'Grants Skill: Level (low-high)' line with a range, in the text's order."""
    found = []
    for m in models.GRANTED_RANGE.finditer(raw):
        _, low, high, skill = models.granted_level(m)
        if low != high and skill not in {s for s, _, _ in found}:
            found.append((skill, low, high))
    return found


def label(level: int, skill: str) -> str:
    return f"Grants Skill: Level {level}+ {skill}"


def parse_label(variants) -> tuple[int, str] | None:
    """(level, skill) when a version's variants are a granted level (label()), else None."""
    if len(variants or ()) != 1:
        return None
    m = LABEL.match(variants[0])
    return (int(m.group(1)), m.group(2)) if m else None


def with_level(raw: str, level: int) -> str:
    """The unique's text with every rolled 'Grants Skill: Level (a-b)' line at `level` (clamped to its range), as a
    {range:} tag PoB loads (models.granted_level reads it back)."""
    def at(m: re.Match) -> str:
        low, high = int(m.group(2)), int(m.group(3))
        if low == high:
            return m.group(0)
        rank = (min(max(level, low), high) - low) / (high - low)
        line = m.group(0)
        if m.group(1) is not None:
            line = line.replace(f"{{range:{m.group(1)}}}", "", 1)
        return f"{{range:{rank:.4f}}}{line}"
    return models.GRANTED_RANGE.sub(at, raw)


def listed_level(item: dict, skill: str) -> int | None:
    """The granted level a trade listing's item shows for `skill` ("grantedSkills": [["Level 18 Thundergod's Wrath",
    25]]), or None."""
    pattern = re.compile(LISTED_LEVEL % re.escape(skill))
    for granted in item.get("grantedSkills") or []:
        for value in granted.get("values") or []:
            if m := pattern.search(str(value[0] if isinstance(value, (list, tuple)) else value)):
                return int(m.group(1))
    return None
