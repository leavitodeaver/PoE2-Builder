"""The official PoE2 trade API (pathofexile.com/trade2), used politely and only on request.

Two things use it, both started by the user: a trade link for one shopping-list item (one search,
a few at most when a strict search finds nothing), and the rare price calibration job (a small,
fixed number of searches and fetches). Nothing here polls or runs on a timer.

Rate limits follow GGG's headers: every response carries X-Rate-Limit-Rules, the limits per rule
(X-Rate-Limit-Ip: "hits:period:penalty,...") and the current state (X-Rate-Limit-Ip-State). Each
request waits until it fits under every window with one request to spare (for the trade-site tabs
the user has open, which count against the same limits), requests are spaced at least a second
apart, a 429's Retry-After is honored, and the request history is kept on disk so a restart
doesn't forget it. The User-Agent names the tool; set POE2_BUILDER_CONTACT to add a contact.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from builder.data import DATA_ROOT

HOST = "https://www.pathofexile.com"
API = f"{HOST}/api/trade2"
SEARCH = "trade-search-request-limit"
FETCH = "trade-fetch-request-limit"
DATA = "trade-data"
# Until the first response says otherwise (values seen 2026-10-24); penalties in seconds
DEFAULT_RULES = {
    SEARCH: [(5, 10, 60), (15, 60, 300), (30, 300, 1800), (600, 21600, 3600)],
    FETCH: [(12, 4, 10), (16, 12, 300), (50, 300, 300), (1000, 21600, 1800)],
    DATA: [(1, 5, 0)],
}
MARGIN = 1  # requests per window left for the user's own trade-site tabs
MIN_SPACING = 1.0  # seconds between any two requests of one policy
# A hit counts as aged out only this long after its window ends. The site times a request when it
# arrives, not when we send it: without a pad, network jitter at a window's edge let three quick
# searches land inside a 60 s window the limiter thought had moved on (a 10-minute lockout,
# 2026-10-24).
def age_out_pad(period: float) -> float:
    return max(1.0, 0.05 * period)
STATS_MAX_AGE = 7 * 24 * 3600
VENDORED_STATS = Path(__file__).resolve().parents[2] / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data" / "TradeSiteStats.lua"


def user_agent() -> str:
    contact = os.environ.get("POE2_BUILDER_CONTACT")
    return "poe2-builder/0.1 (personal build optimizer; single-user local tool" + (f"; {contact})" if contact else ")")


class TradeError(RuntimeError):
    pass


class RateLimited(TradeError):
    """The trade site's limits need a wait longer than the caller allows."""

    def __init__(self, wait: float) -> None:
        super().__init__(f"the trade site's rate limit needs a {wait:.0f}s wait")
        self.wait = wait


class Cancelled(TradeError):
    pass


class RateLimiter:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.rules: dict[str, list[tuple[int, int, int]]] = {k: list(v) for k, v in DEFAULT_RULES.items()}
        self.history: dict[str, list[float]] = {}
        self.blocked_until: dict[str, float] = {}
        if path and path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.rules.update({k: [tuple(r) for r in v] for k, v in saved.get("rules", {}).items()})
                self.history = {k: list(v) for k, v in saved.get("history", {}).items()}
                self.blocked_until = dict(saved.get("blocked_until", {}))
            except (ValueError, OSError):
                pass

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"rules": self.rules, "history": self.history,
                                             "blocked_until": self.blocked_until}), encoding="utf-8")

    def _recent(self, policy: str, now: float) -> list[float]:
        longest = max((period + age_out_pad(period) for _, period, _ in self.rules.get(policy, [])), default=60)
        hits = sorted(t for t in self.history.get(policy, []) if now - t < longest)
        self.history[policy] = hits
        return hits

    def delay(self, policy: str) -> float:
        """Seconds until a request under this policy fits every window."""
        now = self.clock()
        wait = max(0.0, self.blocked_until.get(policy, 0.0) - now)
        hits = self._recent(policy, now)
        if hits:
            wait = max(wait, MIN_SPACING - (now - hits[-1]))
        for count, period, _ in self.rules.get(policy, DEFAULT_RULES[DATA]):
            allowed = max(count - MARGIN, 1)
            window = period + age_out_pad(period)
            recent = [t for t in hits if now - t < window]
            if len(recent) >= allowed:
                # The window has room again once the oldest of the last `allowed` hits ages out
                wait = max(wait, recent[len(recent) - allowed] + window - now)
        return wait

    def record(self, policy: str) -> None:
        self.history.setdefault(policy, []).append(self.clock())
        self._save()

    def update(self, policy: str, headers) -> None:
        """Adopt the limits a response announced, and any hits it counted that we didn't make
        (the user's own trade-site tabs share the same IP limits)."""
        if headers is None:
            return
        names = [n.strip() for n in (headers.get("X-Rate-Limit-Rules") or "").split(",") if n.strip()]
        if not names:
            return
        now = self.clock()
        rules: list[tuple[int, int, int]] = []
        for name in names:
            limits = parse_buckets(headers.get(f"X-Rate-Limit-{name}"))
            rules += limits
            for hits, period, active in parse_buckets(headers.get(f"X-Rate-Limit-{name}-State")):
                if active > 0:
                    self.blocked_until[policy] = max(self.blocked_until.get(policy, 0.0), now + active)
                ours = sum(1 for t in self.history.get(policy, []) if now - t < period)
                if hits > ours:
                    self.history.setdefault(policy, []).extend([now] * (hits - ours))
        if rules:
            self.rules[policy] = rules
        self._save()

    def penalize(self, policy: str, seconds: float) -> None:
        self.blocked_until[policy] = max(self.blocked_until.get(policy, 0.0), self.clock() + seconds)
        self._save()


def parse_buckets(value: str | None) -> list[tuple[int, int, int]]:
    """'5:10:60,15:60:300' -> [(5, 10, 60), (15, 60, 300)]"""
    buckets = []
    for part in (value or "").split(","):
        numbers = part.strip().split(":")
        if len(numbers) == 3 and all(n.isdigit() for n in numbers):
            buckets.append(tuple(int(n) for n in numbers))
    return buckets


def site_url(league: str, query_id: str) -> str:
    """The trade-site page for a search."""
    return f"{HOST}/trade2/search/poe2/{urllib.parse.quote(league)}/{query_id}"


class TradeClient:
    def __init__(self, cache_dir: Path | None = None, limiter: RateLimiter | None = None,
                 opener: Callable | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.cache_dir = cache_dir or DATA_ROOT / "trade"
        self.limiter = limiter or RateLimiter(self.cache_dir / "limits.json")
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.lock = threading.Lock()  # one request in flight at a time, whoever asks
        self.requests_made = 0

    def _request(self, policy: str, url: str, body: dict | None = None, max_wait: float | None = None,
                 on_wait: Callable[[float], None] | None = None, cancel: Callable[[], bool] | None = None) -> dict:
        # Wait without holding the lock, so a Trade click during a long calibration pause is told
        # how long to wait instead of hanging behind it
        while True:
            self.lock.acquire()
            delay = self.limiter.delay(policy)
            if delay <= 0:
                break
            self.lock.release()
            if max_wait is not None and delay > max_wait:
                raise RateLimited(delay)
            if on_wait:
                on_wait(delay)
            if cancel and cancel():
                raise Cancelled("cancelled")
            self.sleep(min(delay, 1.0))
        try:
            self.limiter.record(policy)
            self.requests_made += 1
            return self._send(policy, url, body)
        finally:
            self.lock.release()

    def _send(self, policy: str, url: str, body: dict | None) -> dict:
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self.opener(request, timeout=30) as response:
                self.limiter.update(policy, response.headers)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            self.limiter.update(policy, error.headers)
            if error.code == 429:
                retry = float((error.headers or {}).get("Retry-After") or 60)
                self.limiter.penalize(policy, retry)
                raise RateLimited(retry) from None
            try:
                message = json.loads(error.read().decode("utf-8")).get("error", {}).get("message")
            except (ValueError, AttributeError):
                message = None
            raise TradeError(f"trade site answered {error.code}: {message or error.reason}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise TradeError(f"couldn't reach the trade site ({error})") from None

    # Cache ----------------------------------------------------------------------------------

    def _cached(self, kind: str, key: str, max_age: float) -> dict | None:
        path = self.cache_dir / kind / f"{key}.json"
        if not path.exists():
            return None
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached if time.time() - cached["fetched_at"] < max_age else None

    def _store(self, kind: str, key: str, payload: dict) -> dict:
        path = self.cache_dir / kind / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), **payload}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    # API ------------------------------------------------------------------------------------

    def search(self, league: str, query: dict, max_age: float = 24 * 3600, **wait) -> dict:
        """{id, total, result (item ids, cheapest first when sorted by price), url, cached}."""
        key = hashlib.sha1(json.dumps([league, query], sort_keys=True).encode()).hexdigest()
        cached = self._cached("searches", key, max_age)
        if cached:
            return {**cached, "cached": True}
        answer = self._request(SEARCH, f"{API}/search/poe2/{urllib.parse.quote(league)}", body=query, **wait)
        if "id" not in answer:
            raise TradeError(f"unexpected search answer: {str(answer)[:200]}")
        stored = self._store("searches", key, {"league": league, "query": query, "id": answer["id"],
                                               "total": answer.get("total", 0), "result": answer.get("result", [])[:50],
                                               "url": site_url(league, answer["id"])})
        return {**stored, "cached": False}

    def fetch(self, ids: list[str], query_id: str, max_age: float = 6 * 3600, **wait) -> list[dict]:
        """Listings for up to ten item ids from one search."""
        ids = list(ids)[:10]
        if not ids:
            return []
        key = hashlib.sha1(json.dumps([query_id, ids]).encode()).hexdigest()
        cached = self._cached("fetches", key, max_age)
        if cached:
            return cached["result"]
        answer = self._request(FETCH, f"{API}/fetch/{','.join(ids)}?query={query_id}", **wait)
        result = [r for r in answer.get("result") or [] if r]
        self._store("fetches", key, {"result": result})
        return result

    def stats(self, offline: bool = False) -> dict[str, dict]:
        """Trade stat ids -> {text, type}: live from the API (cached a week), else PoB's copy.
        `offline` never asks the API (an optimizer run uses whatever is cached)."""
        path = self.cache_dir / "stats.json"
        cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if not offline and (not cached or time.time() - cached["fetched_at"] > STATS_MAX_AGE):
            try:
                answer = self._request(DATA, f"{API}/data/stats", max_wait=10)
                entries = {e["id"]: {"text": e["text"], "type": e.get("type")}
                           for group in answer.get("result", []) for e in group.get("entries", [])}
                if entries:
                    cached = self._store("", "stats", {"source": "api", "entries": entries})
            except TradeError:
                pass
        if cached:
            return cached["entries"]
        return vendored_stats()


def vendored_stats() -> dict[str, dict]:
    """The trade stat list PoB ships (Data/TradeSiteStats.lua, a dump of /api/trade2/data/stats)."""
    if not VENDORED_STATS.exists():
        return {}
    text = VENDORED_STATS.read_text(encoding="utf-8")
    pattern = re.compile(r'\["id"\] = "([^"]+)",\s*\["text"\] = "((?:[^"\\]|\\.)*)",\s*\["type"\] = "([^"]+)"')
    return {m.group(1): {"text": m.group(2).replace('\\"', '"').replace("\\n", "\n"), "type": m.group(3)}
            for m in pattern.finditer(text)}
