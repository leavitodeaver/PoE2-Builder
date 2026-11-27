"""Timeless jewels (Heroic Tragedy, Undying Hate): what a conquered passive becomes (TIMELESS1).

Data: builder/data/timeless.json, built from the game's tables by tools/build_timeless.py (research
timeless-jewels-t1.md). What the game files fix (HIGH):
- the jewel stores only version, seed, conqueror (keystone) and radius; every replacement's numbers are fixed;
- a conquered keystone becomes the conqueror's keystone; Undying Hate's small passives become "+5 to Tribute" and
  its attribute passives gain "+3 to Tribute"; Heroic Tragedy leaves small passives alone;
- a conquered notable is ALWAYS replaced (NotableReplacementSpawnWeight 100) by one of the jewel's notables, drawn by
  their spawn weights: Heroic Tragedy 37 notables (1000 / 500 / 250, total 21,250), Undying Hate 30 (100 each,
  Buried Animosity 0, total 2,900).
WHICH notable a seed gives a node is not proven for PoE2 (TIMELESS1-2: PoE1's public draw matches neither PoE2
record on the web; PoB-PoE2's maintainer: "GGG have hidden the function server side"). So, until real outcomes
prove a draw (observed()), a conquered notable is scored at its EXPECTED outcome: every candidate's stats
weighted by its chance (the manager's ruling, 27 Nov 11:25), labelled LOW, with the best and worst candidates as
rows. A known outcome (observed()) replaces the expectation exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "timeless.json"
CONQUERORS = {"kalguur": 6, "abyss": 7}  # PoB's conqueredBy.conqueror.type -> AlternateTreeVersions row
EXPECTED_LABEL = ("seed effects unproven: the average over the possible replacement notables (TIMELESS1-2, LOW)")


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def jewel(version: int) -> dict:
    return data()["jewels"][str(version)]


@dataclass(frozen=True)
class Odds:
    id: str  # PoB's legion node id (= the game's AlternatePassiveSkills Id)
    name: str
    chance: float
    stats: dict


def notable_odds(version: int) -> list[Odds]:
    """Every notable a conquered notable can become, with its chance (spawn weight / total; weight-0 rows dropped:
    they can't be drawn)."""
    rows = [n for n in jewel(version)["notables"] if n["weight"] > 0]
    total = sum(n["weight"] for n in rows)
    return [Odds(n["id"], n["name"], n["weight"] / total, n["stats"]) for n in rows]


def expected_stats(version: int) -> dict[str, float]:
    """The expected stat values of a conquered notable (each candidate's stats times its chance, summed)."""
    out: dict[str, float] = {}
    for odds in notable_odds(version):
        for stat, value in odds.stats.items():
            out[stat] = out.get(stat, 0.0) + odds.chance * value
    return out


def observed() -> dict[tuple[int, int, int], str]:
    """Real outcomes, (version, seed, node id) -> the notable's legion id, from timeless.json's "observed" list. Only
    from the game itself (a character's skill_overrides from GGG's API, with the user's OK); the bridge reads the same
    list."""
    return {(o["version"], o["seed"], o["node"]): o["id"] for o in data().get("observed", [])}


def known(version: int, seed: int, node: int) -> str | None:
    """The notable a real jewel gives this node, if the game has shown it; else None."""
    return observed().get((version, seed, node))
