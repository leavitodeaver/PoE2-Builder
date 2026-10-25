"""Naive baselines the optimizer must beat (PLAN.md Phase 2 exit criterion).

`greedy`: the same skeleton, supports appended one at a time by PoB's own DPS ranking (what the
GUI's sort-by-DPS gives you), the passive tree filled greedily by gain per point within four
nodes of the tree (the benchmarks/make_test_builds.py fill), ascendancy filled the same way,
normal bases only, and PoB's default configuration (no condition resolver, no quest choices).

`greedy_uniques`: the same plus one pass of the best affordable unique per slot.
"""

from __future__ import annotations

import time
from typing import Any

from builder.search.context import GEM_LEVEL, GEM_QUALITY, SUPPORT_SLOTS
from builder.search.inner import Search
from builder.search.objective import Objective, main_dps

REACH = 4


def greedy_fill(search: Search, ascendancy: bool) -> None:
    build = search.build
    key, limit = ("ascendancyAllocated", 8) if ascendancy else ("allocated", None)
    while True:
        budget = build.call("pointBudget")
        used = build.call("summary")[key]
        left = (limit if ascendancy else budget["passives"]) - used
        if left <= 0:
            return
        frontier = build.call("frontier", maxDistance=min(REACH, left), ascendancy=ascendancy)["nodes"]
        forbidden = search.tree.forbidden
        frontier = [n for n in frontier if not (forbidden & set(n["path"]))]
        if not frontier:
            return
        scored = build.node_deltas([{"add": [n["id"]], "addPaths": True, "attribute": search.tree.attribute} for n in frontier])
        gain, best = max(((score - build.score) / n["distance"], n["id"]) for (score, _), n in zip(scored, frontier))
        if gain <= 0 and not ascendancy:
            return
        build.allocate(add=[best], attribute=search.tree.attribute)


def greedy_supports(search: Search) -> None:
    build = search.build
    for _ in range(SUPPORT_SLOTS):
        groups = build.groups()
        group = next(g for g in groups if g["main"])
        names = [g["name"] for g in group["gems"]]
        if len(names) - 1 >= SUPPORT_SLOTS:
            return
        taken = set().union(*(search.gems.families(n) for n in names[1:])) if len(names) > 1 else set()
        candidates = [c for c in search.gems.candidates(group["index"], groups)
                      if c not in names and not (search.gems.families(c) & taken)]
        scored = build.gem_deltas([{"group": group["index"], "slot": len(names) + 1, "gem": c} for c in candidates])
        if not scored:
            return
        best_score, best = max(zip((s for s, _ in scored), candidates), key=lambda x: x[0])
        if best_score <= build.score:
            return
        build.set_group(index=group["index"], gems=[{"name": names[0], "level": GEM_LEVEL, "quality": GEM_QUALITY}]
                        + [{"name": n} for n in names[1:] + [best]])


def baseline_task(engine: Any, report: Any, payload: dict, skeleton: dict) -> dict:
    started = time.perf_counter()
    search = Search(engine, report, payload, skeleton, 0)
    build, sk = search.build, search.skeleton
    build.call("newBuild")
    build.call("setClass", className=sk.class_name, ascendancy=sk.ascendancy)
    build.call("setLevel", level=search.ctx.level)
    build.set_group(gems=[{"name": sk.main_skill, "level": GEM_LEVEL, "quality": GEM_QUALITY}], main=True, fullDPS=True)
    search.gems.add_required_supports()
    search.gear.setup()
    search.tree.choose_default_attribute(sk.main_skill)
    search.tree.allocate_required(sk.class_name)
    greedy_supports(search)
    greedy_fill(search, ascendancy=True)
    greedy_fill(search, ascendancy=False)
    result = {"skeleton": sk.as_dict(), "label": sk.label}

    def record(name: str) -> None:
        stats = build.call("stats", fields=Objective(search.ctx, full=True).fields)
        result[name] = {"main_dps": main_dps(stats), "full_dps": Objective(search.ctx, full=True).dps(stats),
                        "score": build.objective.score(stats), "deficits": [d[0] for d in build.objective.deficits(stats)]}

    record("greedy")
    search.gear.gear_round(time.perf_counter() + 600, full=True)
    record("greedy_uniques")
    result["seconds"] = time.perf_counter() - started
    result["evals"] = build.evals
    return result
