"""Trade prices for specific versions of a unique (PLAN.md "Radius jewels", Phase 5 findings).

poe.ninja has one price per unique: the cheapest copy, whatever its variants. A Megalomaniac
whose three notables the search chose, a From Nothing around one keystone or a Controlled
Metamorphosis of one ring size can cost far more, or not be listed at all. When the user clicks
Trade on such a shopping-list line (builder/report/shopping.py), or runs the Price unique versions
job (builder/report/version_prices.py: every From Nothing keystone and Controlled Metamorphosis
ring), the search for that exact version and what its listings ask are kept here per (unique,
variants). The shopping list prices that version from then on, with the date it was priced, and
the search budgets the job's versions at these prices (builder/search/jewels.py unlock_versions).
Nothing here asks the trade site.

Stored per league in data/prices/<league>/unique_versions.json.
"""

from __future__ import annotations

import json
import threading
import time

from builder.data import DATA_ROOT

FILE = "unique_versions.json"
MAX_AGE = 7 * 24 * 3600  # older prices are shown as stale and the feed's price is used again
_lock = threading.Lock()  # a Trade click can land while the job writes


def key(name: str, variants) -> str:
    """The same version whatever order its selections were made in (Megalomaniac's three
    notables, Against the Darkness's two lines are interchangeable)."""
    return " | ".join([name, *sorted(variants)])


def path(league: str | None):
    return DATA_ROOT / "prices" / (league or "") / FILE


def load(league: str | None) -> dict[str, dict]:
    file = path(league)
    if not league or not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8")).get("versions") or {}
    except (ValueError, OSError):
        return {}


def record(league: str, name: str, variants, priced: dict, now: float | None = None) -> dict:
    """Keep what one search for this version found: {listed, price, cheapest, url, matches, ...}."""
    entry = {"name": name, "variants": sorted(variants), "priced_at": now or time.time(), **priced}
    with _lock:
        versions = load(league)
        versions[key(name, variants)] = entry
        file = path(league)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"league": league, "versions": versions}, indent=1), encoding="utf-8")
    return entry


def lookup(versions: dict | None, name: str, variants, now: float | None = None) -> dict | None:
    """This version's last trade price, or None when it was never priced or is too old."""
    found = (versions or {}).get(key(name, variants))
    if not found or (now or time.time()) - found.get("priced_at", 0) > MAX_AGE:
        return None
    return found


def summary(versions: dict | None, now: float | None = None) -> dict[str, dict]:
    """Per unique: how many of its versions have a current price, how many are listed, and when the
    oldest of them was priced."""
    found: dict[str, dict] = {}
    for entry in (versions or {}).values():
        if lookup(versions, entry["name"], entry["variants"], now) is None:
            continue
        seen = found.setdefault(entry["name"], {"versions": 0, "listed": 0, "priced_at": entry["priced_at"]})
        seen["versions"] += 1
        seen["listed"] += 1 if entry.get("listed") else 0
        seen["priced_at"] = min(seen["priced_at"], entry["priced_at"])
    return found
