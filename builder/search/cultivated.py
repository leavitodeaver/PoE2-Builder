"""Cultivated uniques: the Vaal Cultivation Orb's outcomes, per unique (CULT1; the user, 14 Nov 2026: "Yes, now").

The orb "Replaces up to 2 modifiers on a [Corrupted] [UniqueCulture|Vaal Unique]" (game files, Incursion2Crafting's
'Mutate Unique' row). Its outcomes are the game's 'UniqueMutatedVaal*' mods. builder/data/cultivated.json (built by
tools/build_cultivated.py, sources in its "_about") lists the 48 uniques poe2db names, each one's explicit lines (and
whether each matches a line the game lets the orb replace) and its outcomes with the game's ranges, PoB's text, and
whether PoB parses the outcome whole (data/pob-*/coverage.json).

What the data cannot say (UNVERIFIED, from the game client and poe2db both): outcome weights, the odds of one line
replaced against two, and which line goes (random). Pricing and offering (CULT, next) must label that.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "cultivated.json"


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def uniques() -> dict[str, dict]:
    return load()["uniques"]


def cultivatable(name: str) -> bool:
    return name in uniques()


def outcomes(name: str, offered: bool = True) -> list[dict]:
    """A unique's outcomes. offered=True keeps those the search can score as a fixed line: PoB reads them whole and
    nothing marks them 'not_offered'. The rest stay in the data (an outcome PoB doesn't read is a coverage gap for
    the ledger, not a dropped mechanic; Flesh Crucible's random keystone needs one variant per keystone)."""
    found = uniques().get(name, {}).get("outcomes", [])
    if not offered:
        return list(found)
    return [o for o in found if o["pob_reads"] and "not_offered" not in o]


def replaceable_lines(name: str) -> list[str]:
    """The unique's explicit lines (poe2db's text) the orb may replace, as matched to the game's OriginalMods."""
    return [line["text"] for line in uniques().get(name, {}).get("lines", []) if line["replaceable"]]


def unread_outcomes() -> list[tuple[str, str, list[str]]]:
    """(unique, outcome mod, its lines) for every outcome PoB does not parse whole: the ledger's open items."""
    return [(name, o["mod"], o["pob_unread_lines"] or o["lines"])
            for name, u in uniques().items() for o in u["outcomes"] if not o["pob_reads"]]
