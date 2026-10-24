"""Rare price calibration (PLAN.md §7): a job the user starts from the web page.

It prices a small, fixed set of representative affix packages on the official trade site, one
search and one fetch each, at the pace the site's rate limits allow (builder/data/trade.py), then
fits the rare price model (builder/data/rare_prices.py) and stores it with the fit date and the
samples it used. Packages come from the rares the latest optimizer result asked for (what the
optimizer actually buys) and a generic set per slot (life, resistances, damage, speed).

A package's price is the median of the five cheapest instant-buyout listings that have every one
of its affixes at or above its tier's floor: what buying that package costs today. Searches with
no listings are kept in the report but not fitted.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path

from builder.data import DATA_ROOT, GameData, rare_prices
from builder.data.trade import Cancelled, RateLimited, TradeClient, TradeError
from builder.report.shopping import ladder, ranked_affixes, shopping_list, trade_category, trade_filters

MAX_SEARCHES = 24
LISTINGS = 10  # fetched per search (one fetch request)
CHEAPEST = 5  # the price is the median of this many cheapest
STATUS = "securable"  # instant buyout: listed prices are real prices
SAMPLE_MAX_AGE = 6 * 3600  # a rerun within this reuses the same searches instead of repeating them
# (slot, base type or base name, affix groups per package); groups a base can't roll are skipped,
# so a renamed group after a patch costs a sample, not the job
GENERIC = [
    ("Helmet", "Helmet", [["IncreasedLife", "ColdResistance", "FireResistance"], ["IncreasedLife", "LightningResistance"]]),
    ("Body Armour", "Body Armour", [["IncreasedLife", "BaseSpirit"], ["IncreasedLife", "FireResistance", "ColdResistance"]]),
    ("Gloves", "Gloves", [["IncreasedLife", "IncreasedAttackSpeed"], ["PhysicalDamage", "IncreasedLife", "LightningResistance"]]),
    ("Boots", "Boots", [["MovementVelocity", "IncreasedLife"], ["MovementVelocity", "IncreasedLife", "FireResistance", "ColdResistance"]]),
    ("Amulet", "Amulet", [["IncreasedLife", "CriticalStrikeMultiplier"], ["AllAttributes", "IncreasedLife", "ColdResistance"]]),
    ("Ring 1", "Ring", [["IncreasedLife", "FireResistance", "LightningResistance"], ["PhysicalDamage", "IncreasedAccuracy"]]),
    ("Belt", "Belt", [["IncreasedLife", "ColdResistance", "LightningResistance"], ["IncreasedLife", "StunThreshold"]]),
    ("Weapon 1", "Bow", [["LocalPhysicalDamagePercent", "LocalPhysicalDamage"],
                         ["LocalPhysicalDamagePercent", "LocalIncreasedAttackSpeed", "LocalCriticalStrikeMultiplier"]]),
    ("Weapon 2", "Shield", [["IncreasedLife", "FireResistance"], ["IncreasedLife", "AllResistances", "StunThreshold"]]),
    ("Jewel", "Emerald", [["AttackCriticalStrikeMultiplier", "IncreasedAttackSpeed"],
                          ["AttackDamage", "AttackCriticalStrikeChance", "IncreasedAccuracyPercent"]]),
]
# Tier of each affix in a package, by position; varied so the fit can tell tiers apart
PATTERNS = [[2, 2, 2, 2], [1, 2, 3, 3], [1, 1, 2, 2], [3, 3, 3, 3]]


def generic_packages(data: GameData, stats: dict) -> list[dict]:
    packages = []
    for slot_index, (slot, kind, group_lists) in enumerate(GENERIC):
        bases = [b for b in data.bases if b["type"] == kind] or [b for b in data.bases if b["name"] == kind]
        if not bases:
            continue
        base = max(bases, key=lambda b: (b.get("req") or {}).get("level") or 0)
        pool = "Jewel" if base["type"] == "Jewel" else "Item"
        by_group: dict[str, list[tuple[dict, int]]] = {}
        for mod, tier in ranked_affixes(data, base["name"], pool):
            by_group.setdefault(mod["group"], []).append((mod, tier))
        for k, groups in enumerate(group_lists):
            pattern = PATTERNS[(slot_index + k) % len(PATTERNS)]
            affixes = []
            for i, group in enumerate(groups):
                mods = sorted(by_group.get(group) or [], key=lambda m: m[1])
                if not mods:
                    continue
                mod, tier = mods[min(pattern[i % len(pattern)], len(mods)) - 1]
                filters = trade_filters(mod, 0.5, stats)
                if filters:
                    affixes.append({"text": " / ".join(mod_text(mod)), "tier": tier, "searchable": True, "trade": filters})
            if len(affixes) >= 2:
                packages.append((k, slot_index, package(slot, base, affixes, "generic")))
    # Every slot's first package before any slot's second, so a short run still covers every slot
    return [p for _, _, p in sorted(packages, key=lambda x: (x[0], x[1]))]


def mod_text(mod: dict) -> list[str]:
    return [mod[key] for key in sorted((k for k in mod if k.isdigit()), key=int)]


def package(slot: str, base: dict, affixes: list[dict], source: str) -> dict:
    return {"slot": slot, "kind": "rare", "name": f"Rare {base['type']}", "base": base["name"], "type": base["type"],
            "category": trade_category(base), "model_category": rare_prices.category(slot), "affixes": affixes,
            "source": source}


def result_packages(data: GameData, stats: dict, results_dir: Path | None = None) -> list[dict]:
    """The rares in the newest saved optimizer result that has any."""
    folder = results_dir or DATA_ROOT / "results"
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not any(g.get("rare") for g in (result.get("gear") or {}).values()):
            continue
        shopping = shopping_list(result, data, {}, stats)
        found = []
        for entry in shopping["items"]:
            if entry["kind"] == "rare" and sum(1 for a in entry["affixes"] if a.get("searchable")) >= 1:
                found.append({**entry, "source": f"result {path.name}"})
        return found
    return []


def choose(packages: list[dict], limit: int, per_category: int = 2) -> list[dict]:
    """Distinct packages, alternating result rares (at most `per_category` per slot category) and
    generic ones, at most `limit`."""
    seen, chosen, taken = set(), [], {}
    from_results = [p for p in packages if p["source"] != "generic"]
    generic = [p for p in packages if p["source"] == "generic"]
    order = []
    for i in range(max(len(from_results), len(generic))):
        order += from_results[i:i + 1] + generic[i:i + 1]
    for p in order:
        key = (p["model_category"], tuple(sorted((f["id"], f["floor"]) for a in p["affixes"] for f in a.get("trade") or [])))
        if key in seen or (p["source"] != "generic" and taken.get(p["model_category"], 0) >= per_category):
            continue
        seen.add(key)
        if p["source"] != "generic":
            taken[p["model_category"]] = taken.get(p["model_category"], 0) + 1
        chosen.append(p)
        if len(chosen) >= limit:
            break
    return chosen


def listing_price(listing: dict, rates: dict[str, float]) -> float | None:
    price = (listing.get("listing") or {}).get("price") or {}
    rate = rates.get(price.get("currency"))
    if rate is None or not price.get("amount"):
        return None
    return float(price["amount"]) * rate


class Calibration:
    """One calibration job at a time, in a background thread; `status` is what the page shows."""

    def __init__(self, client: TradeClient, feed, data: GameData) -> None:
        self.client = client
        self.feed = feed
        self.data = data
        self.status: dict = {"state": "idle"}
        self.cancel = False
        self.lock = threading.Lock()

    def start(self, league: str, limit: int = MAX_SEARCHES) -> dict:
        with self.lock:
            if self.status.get("state") == "running":
                raise RuntimeError("a calibration is already running")
            self.cancel = False
            self.status = {"state": "running", "league": league, "done": 0, "total": 0, "started": time.time(),
                           "message": "Choosing packages"}
        threading.Thread(target=self._run, args=(league, limit), daemon=True).start()
        return self.status

    def _wait(self, seconds: float) -> None:
        self.status["message"] = f"Waiting {seconds:.0f}s for the trade site's rate limit"

    def _run(self, league: str, limit: int) -> None:
        status = self.status
        try:
            rates = self.feed.table(league, refresh=False).currency
            stats = self.client.stats()
            packages = choose(result_packages(self.data, stats) + generic_packages(self.data, stats), limit)
            status["total"] = len(packages)
            samples = []
            for p in packages:
                status["message"] = f"Searching: {p['slot']} with {len(p['affixes'])} affixes"
                samples.append(self.price(league, p, rates))
                status["done"] += 1
            model = rare_prices.fit(samples, league)
            model.save({"samples_detail": samples})
            status.update(state="done", finished=time.time(), model=model.describe(),
                          message=f"Fitted on {model.describe()['total_samples']} priced packages "
                                  f"of {len(samples)} searched", samples=samples)
        except Cancelled:
            status.update(state="cancelled", finished=time.time(), message="Cancelled; the model wasn't changed")
        except Exception as error:  # reported on the page; the old model stays
            status.update(state="error", finished=time.time(), message=f"Stopped: {error}")

    def price(self, league: str, p: dict, rates: dict[str, float]) -> dict:
        label, query = ladder(p, status=STATUS)[1]  # each affix at its tier's floor
        wait = {"on_wait": self._wait, "cancel": lambda: self.cancel}
        sample = {"slot": p["slot"], "category": p["model_category"], "base": p["base"], "source": p["source"],
                  "tiers": [a["tier"] for a in p["affixes"] if a.get("searchable")],
                  "affixes": [a["text"] for a in p["affixes"] if a.get("searchable")], "price": None}
        for attempt in range(3):
            try:
                answer = self.client.search(league, query, max_age=SAMPLE_MAX_AGE, **wait)
                break
            except RateLimited as error:  # the site asked for a pause: wait it out, then retry this package
                self.status["message"] = f"The trade site asked for a {error.wait:.0f}s pause; waiting"
            except Cancelled:
                raise
            except TradeError as error:
                return {**sample, "error": str(error)}
        else:
            return {**sample, "error": "rate limited three times"}
        sample.update(listed=answer["total"], url=answer["url"])
        if not answer["total"]:
            return sample
        listings = self.client.fetch(answer["result"][:LISTINGS], answer["id"], max_age=SAMPLE_MAX_AGE, **wait)
        prices = sorted(x for x in (listing_price(l, rates) for l in listings) if x)
        if prices:
            sample.update(price=statistics.median(prices[:CHEAPEST]), cheapest=prices[:CHEAPEST])
        return sample
