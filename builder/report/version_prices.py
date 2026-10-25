"""Unique version pricing (PLAN.md §7 "Unique versions"): a job the user starts from the web page.

poe.ninja prices a unique once, whatever its variants. The search chooses which keystone a From
Nothing frees passives around and which ring a Controlled Metamorphosis covers
(builder/search/jewels.py unlock_versions), so it needs each version's own price. This job searches
every usable version of each such jewel on the official trade site: one search, and one fetch of the
ten cheapest when any are listed, paced by the site's rate limits (builder/data/trade.py). Each
result goes into builder/data/unique_versions.py's table, the same one a Trade click fills. The
search then budgets each version at its trade price and skips versions nobody lists.

Megalomaniac (three of 874 notables) and Against the Darkness (two of 23 lines) have too many
versions for a table: the search keeps the feed's price for them, and a Trade click on the shopping
list prices the chosen version.
"""

from __future__ import annotations

import re
import threading
import time

from builder.data import GameData, unique_versions
from builder.data.trade import Cancelled, RateLimited, TradeClient, TradeError
from builder.report.shopping import LINK_STATUS, asking_prices, stat_index, unique_ladder, version_filters, version_priced
from builder.search.radius import variants_of

# The jewels whose one variant decides which passives the tree may take without a connection: the
# text PoB's parser turns into intuitiveLeapLike (Controlled Metamorphosis's ring) and
# fromNothingKeystones (From Nothing's keystone)
UNLOCK = re.compile(r"can be Allocated without being connected to your tree", re.IGNORECASE)
SAMPLE_MAX_AGE = 6 * 3600  # a rerun within this reuses the same searches instead of repeating them


def version_entries(data: GameData, stats: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """A shopping-list-like entry for every usable version of each jewel unique with one variant
    selection that frees passives from the connection rule, and the versions that can't be
    searched (a line with no trade stat)."""
    index = stat_index(stats)
    entries, unsearchable, seen = [], [], set()
    for unique in data.uniques:
        if unique["type"] != "Jewel" or unique["name"] in seen or not UNLOCK.search(unique["raw"]):
            continue
        variants = variants_of(unique["raw"])
        if variants is None or len(variants.slots) != 1:
            continue
        seen.add(unique["name"])
        for i in variants.usable:
            chosen = [variants.names[i - 1]]
            lines = version_filters(unique["raw"], chosen, index)
            if not lines or not all(line["id"] for line in lines):
                unsearchable.append(f"{unique['name']} ({chosen[0]})")
                continue
            entries.append({"kind": "unique", "name": unique["name"], "base": unique["base"], "variants": chosen,
                            "version_lines": lines})
    return entries, unsearchable


class VersionPricing:
    """One version pricing job at a time, in a background thread; `status` is what the page shows."""

    def __init__(self, client: TradeClient, feed, data: GameData) -> None:
        self.client = client
        self.feed = feed
        self.data = data
        self.status: dict = {"state": "idle"}
        self.cancel = False
        self.lock = threading.Lock()

    def planned(self) -> dict:
        """What a job would search (the trade stats as cached, no request): {searches, uniques}."""
        entries = version_entries(self.data, self.client.stats(offline=True))[0]
        return {"searches": len(entries), "uniques": list(dict.fromkeys(e["name"] for e in entries))}

    def start(self, league: str) -> dict:
        with self.lock:
            if self.status.get("state") == "running":
                raise RuntimeError("version pricing is already running")
            self.cancel = False
            self.status = {"state": "running", "league": league, "done": 0, "total": 0, "started": time.time(),
                           "message": "Choosing versions"}
        threading.Thread(target=self._run, args=(league,), daemon=True).start()
        return self.status

    def _wait(self, seconds: float) -> None:
        self.status["message"] = f"Waiting {seconds:.0f}s for the trade site's rate limit"

    def _run(self, league: str) -> None:
        status = self.status
        try:
            rates = self.feed.table(league, refresh=False).currency
            entries, unsearchable = version_entries(self.data, self.client.stats())
            status["total"] = len(entries)
            listed = unlisted = failed = 0
            for entry in entries:
                status["message"] = f"Searching: {entry['name']} ({entry['variants'][0]})"
                found = self.price(league, entry, rates)
                if found is None:
                    failed += 1
                elif found["listed"]:
                    listed += 1
                else:
                    unlisted += 1
                status["done"] += 1
            skipped = f"; {len(unsearchable)} can't be searched" if unsearchable else ""
            errors = f"; {failed} searches failed" if failed else ""
            status.update(state="done", finished=time.time(), unsearchable=unsearchable,
                          message=f"Priced {listed + unlisted} versions: {listed} listed, {unlisted} not listed"
                                  f"{skipped}{errors}")
        except Cancelled:
            status.update(state="cancelled", finished=time.time(),
                          message=f"Stopped after {status['done']} versions; those are kept")
        except Exception as error:  # reported on the page; what's already priced stays
            status.update(state="error", finished=time.time(), message=f"Stopped: {error}")

    def price(self, league: str, entry: dict, rates: dict[str, float]) -> dict | None:
        """Search one version, fetch its ten cheapest when listed, and keep the result; None when
        the trade site couldn't answer (nothing is kept, so the search uses the feed's price)."""
        label, query = unique_ladder(entry, LINK_STATUS)[0]
        wait = {"on_wait": self._wait, "cancel": lambda: self.cancel, "even": True}
        for attempt in range(3):
            try:
                answer = self.client.search(league, query, max_age=SAMPLE_MAX_AGE, **wait)
                asking = asking_prices(self.client, answer, rates, max_age=SAMPLE_MAX_AGE, **wait)
                break
            except RateLimited as error:  # the site asked for a pause: wait it out, then retry this version
                self.status["message"] = f"The trade site asked for a {error.wait:.0f}s pause; waiting"
            except Cancelled:
                raise
            except TradeError:
                return None
        else:
            return None
        tried = [{"matches": label, "total": answer["total"], "url": answer["url"]}]
        priced = version_priced(tried, {"asking": asking} if asking else {})
        return unique_versions.record(league, entry["name"], entry["variants"], priced, now=answer.get("fetched_at"))
