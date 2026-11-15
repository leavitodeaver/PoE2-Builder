"""Runeforged / Runemastered copies of uniques (COV32; the manager, 15 Nov 08:42: the copies' data is COV-A's).

Patch 0.5.0 (Runes of Aldur): "After unlocking Unique Verisium Runeforging in Act 3 you can upgrade the base type of
Unique Weapons and Armours that drop at levels lower than 55. Upgraded Unique Weapons will have higher damage, and
Upgraded Armours will have increased base defences as well as Runic Ward"; 0.5.5: "The Verisium Anvil is now unlocked by
completing The Runeseeker quest in Act 4 outside of the Runes of Aldur League" (league-obtainable). PoB's text for such a
unique lists "Base Variant: Regular Base / Runeforged / Runemastered", tags the base-name line and any base-specific line
with {base:N} (an implicit, "5% increased Movement Speed", a Runemastered-only mod) and selects one with "Selected Base
Variant: N" (1, the regular base, by default). Nothing in the tool set it, so every one of the 269 uniques with such
versions was scored on its regular base only (checked: git grep "Base Variant" over builder/: readers only).

base_variant_copies gives each non-regular version as its own text: the same text with "Selected Base Variant: N", and
the base PoB will use for it (the {base:N} base-name line). What it costs to make one (the Verisium Anvil's upgrade) the
tool doesn't know: offering them waits on that (see the report: the hook's design for SEARCH, and the price question).
"""

from __future__ import annotations

import re

BASE_VARIANT = re.compile(r"^Base Variant: (.+)$", re.M)
SELECTED = re.compile(r"^Selected Base Variant: \d+$", re.M)
TAGGED = re.compile(r"^((?:\{[^}]*\})*)(.*)$")
BASE_TAG = re.compile(r"\{base:([\d,]+)\}")


def base_variants(raw: str) -> list[str]:
    """The unique's base versions as PoB names them ("Regular Base", "Runeforged", "Runemastered"), in order (1-based)."""
    return BASE_VARIANT.findall(raw)


def variant_base(raw: str, index: int, base_names: set[str]) -> str | None:
    """The base PoB uses for version `index`: the {base:index}-tagged line that is a base name."""
    for line in raw.split("\n"):
        tags, text = TAGGED.match(line).groups()
        for m in BASE_TAG.finditer(tags):
            if index in {int(x) for x in m.group(1).split(",")} and text.strip() in base_names:
                return text.strip()
    return None


def select(raw: str, index: int) -> str:
    """The text with base version `index` selected."""
    if SELECTED.search(raw):
        return SELECTED.sub(f"Selected Base Variant: {index}", raw, count=1)
    lines = raw.split("\n")
    at = max(i for i, l in enumerate(lines) if l.startswith("Base Variant:")) + 1
    return "\n".join(lines[:at] + [f"Selected Base Variant: {index}"] + lines[at:])


def base_variant_copies(unique: dict, base_names: set[str]) -> list[dict]:
    """Each non-regular base version of a unique ({"name", "base", "raw"} as in uniques.json), as
    {"version": "Runeforged" / "Runemastered", "index": N, "base": its base name, "raw": the text with it selected}.
    A version whose base PoB doesn't have is left out (UNVERIFIED that any is: none found on 15 Nov)."""
    copies = []
    for index, label in enumerate(base_variants(unique["raw"]), start=1):
        if index == 1 or label == "Regular Base":
            continue
        base = variant_base(unique["raw"], index, base_names)
        if base is None:
            continue
        copies.append({"version": label, "index": index, "base": base, "raw": select(unique["raw"], index)})
    return copies
