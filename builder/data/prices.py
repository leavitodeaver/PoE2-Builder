"""Prices from poe.ninja's documented PoE2 economy API, cached locally. Values are in divine orbs.

Only the economy endpoints are used: poe.ninja doesn't allow third-party use of its build or
character data. Upstream overviews refresh about hourly, so the cache refreshes at most hourly,
sends ETags, and spaces requests out. Set POE2_BUILDER_CONTACT to add a contact to the
User-Agent, as poe.ninja asks.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from builder.data import DATA_ROOT

API = "https://poe.ninja/poe2/api/economy"
EXCHANGE_TYPES = [
    "Currency", "Fragments", "Abyss", "UncutGems", "LineageSupportGems", "Essences", "SoulCores",
    "Idols", "Runes", "Ritual", "Expedition", "Delirium", "Breach", "Verisium",
]
UNIQUE_TYPES = ["UniqueWeapons", "UniqueArmours", "UniqueAccessories", "UniqueFlasks", "UniqueCharms", "UniqueJewels"]
MAX_AGE_SECONDS = 3600
LEAGUES_MAX_AGE_SECONDS = 24 * 3600
REQUEST_SPACING_SECONDS = 0.5
# The league the search prices on when a request names none (the manager, 18 Nov 14:50): an explicit setting, not
# poe.ninja's first listed league. default_league() took the first non-hardcore, non-Standard league poe.ninja listed,
# and poe.ninja lists the event league "Forbidden Rites" before the challenge league "Runes of Aldur", so builds were
# priced on that economy without anyone choosing it. The manager's ruling (18 Nov 17:50): the default stays "Forbidden
# Rites" (the 0.5.5 event league; today's behaviour: the only local price cache, and the rare price model was fitted on
# it, 24 Oct), now as an explicit setting said in every result; "Runes of Aldur" (the 0.5.0 challenge league) has no
# local prices or fitted model yet. The user decides which to use. POE2_LEAGUE overrides it (the page and a request's
# `league` field choose per run).
DEFAULT_LEAGUE = "Forbidden Rites"
RARE_MODEL_FILE = "rare_model.json"  # (builder/data/rare_prices.MODEL_FILE: the rare price model fitted on a league)


def configured_league() -> str:
    """The league a request without one is priced on: POE2_LEAGUE, else DEFAULT_LEAGUE."""
    return os.environ.get("POE2_LEAGUE", "").strip() or DEFAULT_LEAGUE


def confidence(listings: int | None) -> str:
    if listings is None:
        return "unknown"
    return "high" if listings >= 20 else "medium" if listings >= 5 else "low"


@dataclass(frozen=True)
class Price:
    name: str
    divine: float
    listings: int | None
    category: str
    base: str | None = None
    variant: str | None = None
    corrupted: bool = False

    @property
    def confidence(self) -> str:
        return confidence(self.listings)


class PriceTable:
    def __init__(self, league: str, exchange: dict[str, Price], uniques: dict[str, list[Price]], fetched_at: float,
                 currency: dict[str, float] | None = None, warnings: list[str] | None = None) -> None:
        self.league = league
        self.warnings = list(warnings or [])  # what the result says about these prices (PriceFeed.league_warnings)
        self.exchange = exchange
        self.uniques = uniques
        self.fetched_at = fetched_at
        # Divines per unit by poe.ninja's currency id, which is the trade site's ("exalted", "chaos", ...)
        self.currency = currency or {}

    def unique(self, name: str) -> Price | None:
        """The most-listed uncorrupted copy: a representative price, not the cheapest outlier."""
        lines = self.uniques.get(name) or []
        candidates = [p for p in lines if not p.corrupted] or lines
        return max(candidates, key=lambda p: p.listings or 0, default=None)

    def item(self, name: str) -> Price | None:
        return self.exchange.get(name)


class PriceFeed:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or DATA_ROOT / "prices"
        contact = os.environ.get("POE2_BUILDER_CONTACT")
        self.user_agent = "poe2-builder/0.1 (personal build optimizer; single-user local tool" + (f"; {contact})" if contact else ")")

    # Network ------------------------------------------------------------------------------

    def _fetch(self, url: str, cache_file: Path, max_age: float, force: bool) -> tuple[dict | list, str]:
        """Return (payload, status) where status is 'cached', 'not-modified' or 'fetched'."""
        cached = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
        if cached and not force and time.time() - cached["fetched_at"] < max_age:
            return cached["payload"], "cached"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                etag = response.headers.get("ETag")
                status = "fetched"
        except urllib.error.HTTPError as error:
            if error.code != 304 or not cached:
                raise
            payload, etag, status = cached["payload"], cached.get("etag"), "not-modified"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "etag": etag, "url": url, "payload": payload}), encoding="utf-8")
        return payload, status

    def leagues(self, force: bool = False) -> list[dict]:
        payload, _ = self._fetch(f"{API}/leagues", self.cache_dir / "leagues.json", LEAGUES_MAX_AGE_SECONDS, force)
        return payload

    def default_league(self) -> str:
        """The configured league (configured_league): never poe.ninja's first listed one, which was an event league."""
        return configured_league()

    def league_warnings(self, league: str, priced: bool) -> list[str]:
        """What a result priced on `league` must say: the league missing from poe.ninja's list, no local prices for it
        (`priced` False: nothing cached), or no rare price model fitted on it (the default parameters price rares)."""
        out = []
        try:  # (the cached list only: pricing a run makes no request of its own)
            cached = json.loads((self.cache_dir / "leagues.json").read_text(encoding="utf-8"))
            listed = [entry["id"] for entry in cached["payload"]]
        except (OSError, ValueError, KeyError, TypeError):
            listed = None
        if listed is None:
            out.append(f"poe.ninja's league list isn't cached: '{league}' not checked")
        elif league not in listed:
            out.append(f"'{league}' isn't in poe.ninja's league list ({', '.join(listed)}): its prices may be missing "
                       "or stale")
        if not priced:
            out.append(f"no local poe.ninja prices for '{league}': uniques, gems and currency are unpriced (fetch them: "
                       f"python -m builder.data prices \"{league}\")")
        if not (self.cache_dir / league / RARE_MODEL_FILE).exists():
            out.append(f"the rare price model isn't fitted on '{league}': rares are priced with its default parameters")
        return out

    def refresh(self, league: str, force: bool = False) -> dict[str, str]:
        statuses = {}
        quoted = urllib.parse.quote(league)
        for kind, types, path in (
            ("exchange", EXCHANGE_TYPES, "exchange/current/overview"),
            ("uniques", UNIQUE_TYPES, "stash/current/item/overview"),
        ):
            for type_name in types:
                url = f"{API}/{path}?league={quoted}&type={type_name}"
                cache_file = self.cache_dir / league / kind / f"{type_name}.json"
                try:
                    _, status = self._fetch(url, cache_file, MAX_AGE_SECONDS, force)
                except (urllib.error.URLError, TimeoutError) as error:
                    status = f"failed ({error}); {'using cache' if cache_file.exists() else 'no cache'}"
                statuses[type_name] = status
                if status in ("fetched", "not-modified"):
                    time.sleep(REQUEST_SPACING_SECONDS)
        return statuses

    # Reading -------------------------------------------------------------------------------

    def table(self, league: str | None = None, refresh: bool = True) -> PriceTable:
        league = league or self.default_league()
        if refresh:
            self.refresh(league)
        exchange: dict[str, Price] = {}
        uniques: dict[str, list[Price]] = {}
        currency: dict[str, float] = {"divine": 1.0}
        oldest = time.time()
        for type_name in EXCHANGE_TYPES:
            cache_file = self.cache_dir / league / "exchange" / f"{type_name}.json"
            if not cache_file.exists():
                continue
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            oldest = min(oldest, cached["fetched_at"])
            payload = cached["payload"]
            names = {item["id"]: item["name"] for item in payload.get("items", [])}
            for line in payload.get("lines", []):
                name = names.get(line["id"], line["id"])
                exchange[name] = Price(name, line["primaryValue"], None, type_name)
                if type_name == "Currency":
                    currency[line["id"]] = line["primaryValue"]
        for type_name in UNIQUE_TYPES:
            cache_file = self.cache_dir / league / "uniques" / f"{type_name}.json"
            if not cache_file.exists():
                continue
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            oldest = min(oldest, cached["fetched_at"])
            for line in cached["payload"].get("lines", []):
                price = Price(line["name"], line["primaryValue"], line.get("listingCount"), type_name,
                              line.get("baseType"), line.get("variant"), bool(line.get("corrupted")))
                uniques.setdefault(price.name, []).append(price)
        warnings = self.league_warnings(league, bool(exchange or uniques))
        return PriceTable(league, exchange, uniques, oldest, currency, warnings)
