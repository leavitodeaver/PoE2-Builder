"""A build loaded in one engine, as the search sees it: what-ifs, commits, snapshots, and the
current score. Everything else in the search talks to PoB through this class, which also counts
evaluations and streams progress.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from builder.search.context import Context, ItemOption
from builder.search.objective import CLEAR_FIELDS, EHP_FIELDS_PREFIXES, Objective

EPSILON = 1e-6  # smallest score gain worth committing
PROGRESS_INTERVAL = 1.0
DELTA_CHUNK = 150  # what-ifs per engine call


class Build:
    def __init__(self, engine: Any, ctx: Context, objective: Objective, report: Callable | None = None,
                 label: str = "") -> None:
        self.engine = engine
        self.ctx = ctx
        self.objective = objective
        self.report = report
        self.label = label
        self.stats: dict = {}
        self.score = -math.inf
        self.evals = 0
        self.commits = 0
        self.started = time.perf_counter()
        self.phase = "setup"
        self._last_report = 0.0
        # What the search put in each item slot, for prices and the result
        self.equipped: dict[str, ItemOption] = {}
        self.locked_slots: set[str] = set()
        self.reverts: dict[str, int] = {}  # phase -> commits undone because the score fell
        self.after_load: list = []  # called after every reload, before the score is refreshed
        # Stats PoB doesn't calculate, added to every result before scoring (flask recovery per
        # second, builder/search/flasks.py); what-if cases may override them with an "extra" entry
        self.extra_stats: dict = {}

    def worse(self, before: float) -> bool:
        """After a commit a what-if said would gain: True (and counted) if it lost instead."""
        if self.score < before - EPSILON:
            self.reverts[self.phase] = self.reverts.get(self.phase, 0) + 1
            return True
        return False

    # Engine access -----------------------------------------------------------------------------

    def call(self, method: str, **kwargs: Any) -> Any:
        return self.engine.call(method, **kwargs)

    def _committed(self, result: dict) -> float:
        self.commits += 1
        self.stats = self.objective.prepare({**result["stats"], **self.extra_stats})
        self.score = self.objective.score(self.stats)
        self.tick()
        return self.score

    def refresh(self) -> float:
        return self._committed({"stats": self.call("stats", fields=self.objective.fields)})

    def set_objective(self, objective: Objective) -> None:
        self.objective = objective
        self.refresh()

    def tick(self, force: bool = False) -> None:
        """Stream progress about once a second; also where a cancelled task stops."""
        if self.report is None:
            return
        now = time.perf_counter()
        if force or now - self._last_report >= PROGRESS_INTERVAL:
            self._last_report = now
            self.report(self.progress())
        else:
            self.report(None)

    def progress(self) -> dict:
        elapsed = time.perf_counter() - self.started
        return {
            "skeleton": self.label, "phase": self.phase, "score": self.score,
            "dps": self.objective.dps(self.stats), "clear": self.objective.clear(self.stats)["score"],
            "value": self.objective.value(self.stats), "evals": self.evals,
            "evals_per_second": self.evals / elapsed if elapsed > 0 else 0.0,
            "deficits": [label for label, _, _ in self.objective.deficits(self.stats)],
        }

    # What-ifs ----------------------------------------------------------------------------------

    def _scored(self, results: list[dict], fill_from_base: bool = False,
                extras: list[dict | None] | None = None) -> list[tuple[float, dict]]:
        self.evals += len(results)
        scored = []
        for index, stats in enumerate(results):
            stats.update(self.extra_stats)
            if extras and extras[index]:
                stats.update(extras[index])
            if fill_from_base:
                # A clear field the what-if lacks is gone (a support that stops chaining), not skipped
                for key, value in self.stats.items():
                    if (key not in stats and key not in CLEAR_FIELDS) or key.startswith(EHP_FIELDS_PREFIXES):
                        stats[key] = value
            self.objective.prepare(stats)
            scored.append((self.objective.score(stats), stats))
        self.tick()
        return scored

    def _deltas(self, method: str, cases: list[dict], fill_from_base: bool = False, **kwargs: Any) -> list[tuple[float, dict]]:
        """Run what-ifs in chunks, so progress (and cancellation) keeps flowing on slow builds."""
        scored: list[tuple[float, dict]] = []
        extras = [case.get("extra") for case in cases]
        cases = [{k: v for k, v in case.items() if k != "extra"} for case in cases]
        for start in range(0, len(cases), DELTA_CHUNK):
            result = self.call(method, cases=cases[start:start + DELTA_CHUNK], fields=self.objective.fields,
                               fullDPS=self.objective.full, **kwargs)
            scored += self._scored(result["results"], fill_from_base, extras[start:start + DELTA_CHUNK])
        return scored

    def node_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        return self._deltas("nodeDeltas", cases)

    def gem_deltas(self, cases: list[dict], skip_ehp: bool = True) -> list[tuple[float, dict]]:
        return self._deltas("gemDeltas", cases, fill_from_base=skip_ehp, skipEHP=skip_ehp)

    def item_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        return self._deltas("itemDeltas", cases)

    # Commits -----------------------------------------------------------------------------------

    def allocate(self, add: list[int] = (), remove: list[int] = (), attribute: int = 1) -> float:
        return self._committed(self.call("allocate", add=list(add), remove=list(remove), attribute=attribute,
                                         fields=self.objective.fields))

    def set_attributes(self, choices: dict[int, int]) -> float:
        return self._committed(self.call("setAttributes", choices={str(k): v for k, v in choices.items()},
                                         fields=self.objective.fields))

    def set_group(self, **kwargs: Any) -> dict:
        result = self.call("setGroup", fields=self.objective.fields, **kwargs)
        self._committed(result)
        return result["group"]

    def remove_group(self, index: int) -> float:
        return self._committed(self.call("removeGroup", index=index, fields=self.objective.fields))

    def equip(self, slot: str, option: ItemOption | None, active: bool | None = None) -> float:
        """`active` switches a flask or charm slot on or off (None leaves it as it is)."""
        extra = {} if active is None else {"active": active}
        result = self.call("equip", slot=slot, raw=option.raw if option else None, fields=self.objective.fields, **extra)
        if option is None:
            self.equipped.pop(slot, None)
        else:
            self.equipped[slot] = option
        return self._committed(result)

    def set_config(self, values: dict[str, Any]) -> float:
        return self._committed(self.call("setConfig", vars=values, fields=self.objective.fields))

    # Snapshots ---------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {"xml": self.call("exportXml")["xml"], "equipped": dict(self.equipped), "score": self.score,
                "stats": dict(self.stats), "extra_stats": dict(self.extra_stats)}

    def restore(self, snap: dict) -> None:
        self.call("loadXml", xml=snap["xml"])
        self.equipped = dict(snap["equipped"])
        self.extra_stats = dict(snap.get("extra_stats") or {})
        for hook in self.after_load:
            hook()
        self.refresh()

    # Facts -------------------------------------------------------------------------------------

    def groups(self) -> list[dict]:
        return self.call("groups")["groups"]

    def main_group(self) -> dict | None:
        result = self.call("groups")
        index = result["mainSocketGroup"]
        return result["groups"][index - 1] if 0 < index <= len(result["groups"]) else None

    def spent(self) -> float:
        """Divines spent on items and priced gems."""
        total = sum(option.price or 0.0 for option in self.equipped.values())
        for group in self.groups():
            for gem in group["gems"]:
                priced = self.ctx.gem_price(gem["name"]) if gem["support"] else None
                if priced:
                    total += priced[0] or 0.0
        return total
