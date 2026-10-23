"""Passive tree block (PLAN.md §5.2.2).

- Grow: every reachable target (small nodes nearby, notables and keystones further out) is scored
  as its whole path from the current tree in one what-if; the best gain per point is allocated.
  Values are kept between steps and only the top few and the changed paths are rechecked (lazy
  greedy), with a full recheck every few allocations.
- Steiner pairs: during full rechecks, pairs of strong targets are scored as the union of their
  paths, so two notables behind shared travel nodes can win together.
- Prune and regrow: branches that give least per point are removed and the points regrown; the
  change is kept only if the build ends better.
- Attributes: each allocated attribute node's Str/Dex/Int is a decision of its own.
- Ascendancy: every connected allocation of the ascendancy's points is enumerated and scored
  (with one option per multiple-choice node).

Forbidden passives never appear as targets or on a path; required passives are never removed.
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter, deque
from dataclasses import dataclass
from itertools import product

from builder.search.state import EPSILON, Build

SMALL_REACH = 3
FAR_REACH = 9
TOP_RECHECK = 8
FULL_EVERY = 12
PAIR_POOL = 10
ASCENDANCY_POINTS = 8
ASCENDANCY_CASES = 1500  # most allocations scored exactly in one round
STRENGTH, DEXTERITY, INTELLIGENCE = 1, 2, 3


@dataclass
class Candidate:
    id: int
    path: tuple[int, ...]
    value: float = -math.inf  # score gain per point when last evaluated
    gain: float = -math.inf
    stamp: int = -1  # evaluation round; values from older rounds are stale

    @property
    def cost(self) -> int:
        return len(self.path)


def rooted_subsets(root: int, adjacency: dict[int, list[int]], limit: int) -> list[frozenset[int]]:
    """Every connected node set containing `root` with at most `limit` nodes besides it, each
    once (extension-set enumeration)."""
    found: list[frozenset[int]] = []

    def extend(current: frozenset[int], extension: list[int], excluded: set[int]) -> None:
        found.append(current)
        if len(current) - 1 >= limit:
            return
        extension = list(extension)
        excluded = set(excluded)
        while extension:
            node = extension.pop()
            grown = extension + [n for n in adjacency[node]
                                 if n not in current and n not in excluded and n not in extension and n != node]
            extend(current | {node}, grown, excluded)
            excluded.add(node)

    extend(frozenset([root]), list(adjacency[root]), set())
    return found


class Tree:
    def __init__(self, build: Build, rng: random.Random) -> None:
        self.build = build
        self.ctx = build.ctx
        self.rng = rng
        self.attribute = STRENGTH
        self.candidates: dict[int, Candidate] = {}
        self.round = 0
        self.allocations_since_full = FULL_EVERY
        self.required: set[int] = set()

    # Setup -------------------------------------------------------------------------------------

    def choose_default_attribute(self, main_skill: str) -> None:
        gem = self.ctx.gems.get(main_skill) or {}
        reqs = {STRENGTH: gem.get("reqStr") or 0, DEXTERITY: gem.get("reqDex") or 0, INTELLIGENCE: gem.get("reqInt") or 0}
        self.attribute = max(reqs, key=lambda a: (reqs[a], -a))

    def allocate_required(self, class_name: str) -> list[str]:
        """Allocate required main-tree passives (with their paths). Returns names not found."""
        names = [n for n in self.ctx.compiled.request.require.passives]
        distances = self.ctx.main_tree_distances(class_name)
        missing = []
        for name in names:
            ids = [i for i, n in self.ctx.nodes.items() if n.get("name") == name and not n.get("ascendancy")]
            if not ids:
                continue  # an ascendancy passive: the ascendancy round handles it
            reachable = [i for i in ids if i in distances]
            if not reachable:
                missing.append(name)
                continue
            target = min(reachable, key=lambda i: distances[i])
            self.build.allocate(add=[target], attribute=self.attribute)
            self.required.add(target)
        # Their paths stay too: removing a branch that cuts a required node off is never proposed
        return missing

    # Grow --------------------------------------------------------------------------------------

    def points_left(self) -> int:
        budget = self.build.call("pointBudget")
        return budget["passives"] - budget["used"]

    def frontier(self, limit: int) -> list[Candidate]:
        reach = min(FAR_REACH, limit)
        nodes = self.build.call("frontier", maxDistance=min(SMALL_REACH, limit),
                                maxDistanceByType={"Notable": reach, "Keystone": reach})["nodes"]
        forbidden = self.ctx.forbidden_node_ids
        current: dict[int, Candidate] = {}
        for node in nodes:
            path = tuple(node["path"])
            if node["id"] in forbidden or forbidden.intersection(path) or len(path) > limit:
                continue
            known = self.candidates.get(node["id"])
            if known is None or known.path != path:
                known = Candidate(node["id"], path)
            current[node["id"]] = known
        self.candidates = current
        return list(current.values())

    def evaluate(self, candidates: list[Candidate]) -> None:
        cases = [{"add": [c.id], "addPaths": True, "attribute": self.attribute} for c in candidates]
        for candidate, (score, _) in zip(candidates, self.build.node_deltas(cases)):
            candidate.gain = score - self.build.score
            candidate.value = candidate.gain / max(candidate.cost, 1)
            candidate.stamp = self.round

    def best_pair(self, candidates: list[Candidate], left: int) -> tuple[float, list[int]] | None:
        pool = sorted((c for c in candidates if c.stamp == self.round), key=lambda c: -c.value)[:PAIR_POOL]
        cases, pairs = [], []
        for i, a in enumerate(pool):
            for b in pool[i + 1:]:
                cost = len(set(a.path) | set(b.path))
                if cost <= left and cost < a.cost + b.cost:  # only pairs that share travel nodes
                    cases.append({"add": [a.id, b.id], "addPaths": True, "attribute": self.attribute})
                    pairs.append(([a.id, b.id], cost))
        best = None
        for (score, _), (ids, cost) in zip(self.build.node_deltas(cases), pairs):
            value = (score - self.build.score) / cost
            if best is None or value > best[0]:
                best = (value, ids)
        return best

    def grow(self, deadline: float) -> bool:
        """Spend free points on the best value per point until none is left or nothing gains."""
        improved = False
        retried_full = False
        rejected: set[int] = set()  # targets whose commit lost what their what-if promised
        while time.perf_counter() < deadline:
            left = self.points_left()
            if left <= 0:
                break
            candidates = [c for c in self.frontier(left) if c.id not in rejected]
            if not candidates:
                break
            full = self.allocations_since_full >= FULL_EVERY
            if full:
                self.evaluate(candidates)
                self.allocations_since_full = 0
            else:
                self.evaluate([c for c in candidates if c.stamp < 0])
                stale = sorted((c for c in candidates if c.stamp != self.round), key=lambda c: -c.value)
                batch = TOP_RECHECK
                while stale:
                    self.evaluate(stale[:batch])
                    stale = stale[batch:]
                    fresh_best = max((c.value for c in candidates if c.stamp == self.round), default=-math.inf)
                    if not stale or fresh_best >= stale[0].value:
                        break
                    batch *= 2
            fresh = [c for c in candidates if c.stamp == self.round]
            best = max(fresh, key=lambda c: c.value)
            choice, value = [best.id], best.value
            if full:
                pair = self.best_pair(candidates, left)
                if pair and pair[0] > value:
                    value, choice = pair
            if value <= EPSILON:
                if full or retried_full:
                    break
                retried_full = True  # stale values may hide a gain; recheck everything once
                self.allocations_since_full = FULL_EVERY
                continue
            before = self.build.score
            allocated = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]}
            self.build.allocate(add=choice, attribute=self.attribute)
            self.round += 1
            self.allocations_since_full += 1
            if self.build.worse(before):
                added = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]} - allocated
                self.build.allocate(remove=sorted(added))
                for node_id in choice:
                    self.candidates.pop(node_id, None)
                rejected.update(choice)
                self.allocations_since_full = FULL_EVERY
                continue
            improved = improved or self.build.score > before + EPSILON
        return improved

    # Prune and regrow ---------------------------------------------------------------------------

    def protected(self) -> set[int]:
        return set(self.required)

    def branches(self) -> list[frozenset[int]]:
        protected = self.protected()
        seen: set[frozenset[int]] = set()
        branches = []
        for node in self.build.call("allocatedNodes")["nodes"]:
            branch = frozenset([node["id"], *node["depends"]])
            if branch & protected or branch in seen:
                continue
            seen.add(branch)
            branches.append(branch)
        return branches

    def weakest_branches(self) -> list[tuple[float, frozenset[int]]]:
        branches = self.branches()
        scored = self.build.node_deltas([{"remove": sorted(b)} for b in branches])
        ranked = [((self.build.score - score) / len(b), b) for (score, _), b in zip(scored, branches)]
        ranked.sort(key=lambda x: x[0])
        return ranked

    def swap_round(self, deadline: float, attempts: int = 4) -> bool:
        """Remove a weak branch and regrow its points; keep the change only if it pays."""
        for loss, branch in self.weakest_branches()[:attempts]:
            if time.perf_counter() > deadline:
                break
            snap = self.build.snapshot()
            before = self.build.score
            self.remove_branch(branch)
            self.grow(deadline)
            if self.build.score > before + EPSILON:
                return True
            self.build.restore(snap)
            self.round += 1
        return False

    def remove_branch(self, branch: frozenset[int]) -> None:
        self.build.allocate(remove=sorted(branch))
        self.round += 1
        self.allocations_since_full = FULL_EVERY

    def reset(self) -> None:
        """Clear every passive that can go (required ones and their paths stay), to regrow."""
        while True:
            branches = self.branches()
            if not branches:
                break
            self.build.allocate(remove=sorted(max(branches, key=len)))
        self.round += 1
        self.candidates.clear()
        self.allocations_since_full = FULL_EVERY

    def kick(self, size: int) -> None:
        """Remove random branches worth about `size` points (iterated local search)."""
        removed = 0
        branches = self.branches()
        self.rng.shuffle(branches)
        for branch in branches:
            if removed >= size:
                break
            if len(branch) <= size - removed + 3:
                self.build.allocate(remove=sorted(branch))
                removed += len(branch)
                branches = [b for b in branches if not (b & branch)]
        self.round += 1
        self.allocations_since_full = FULL_EVERY

    # Attribute nodes ---------------------------------------------------------------------------

    def attributes_round(self) -> bool:
        nodes = self.build.call("attributeNodes")["nodes"]
        if not nodes:
            return False
        cases, keys = [], []
        for node in nodes:
            for attribute in (STRENGTH, DEXTERITY, INTELLIGENCE):
                if attribute != node["attribute"]:
                    cases.append({"switch": [node["id"], attribute]})
                    keys.append((node["id"], attribute))
        best: dict[int, tuple[float, int]] = {}
        for (score, _), (node_id, attribute) in zip(self.build.node_deltas(cases), keys):
            gain = score - self.build.score
            if gain > EPSILON and gain > best.get(node_id, (0.0, 0))[0]:
                best[node_id] = (gain, attribute)
        chosen = {n["id"]: n["attribute"] for n in nodes}
        improved = False
        if best:
            before = self.build.score
            self.build.set_attributes({node_id: attribute for node_id, (_, attribute) in best.items()})
            if self.build.score < before - EPSILON:
                self.build.set_attributes({node_id: chosen[node_id] for node_id in best})
            else:
                improved = self.build.score > before + EPSILON
                chosen.update({node_id: attribute for node_id, (_, attribute) in best.items()})
        counts = Counter(a for a in chosen.values() if a)
        if counts:
            self.attribute = counts.most_common(1)[0][0]
        return improved

    # Ascendancy --------------------------------------------------------------------------------

    def ascendancy_round(self) -> bool:
        """Score every full allocation of the ascendancy points and keep the best. Large
        ascendancies are first narrowed to their strongest notables (each scored alone), then
        enumerated exactly within that set."""
        build = self.build
        facts = build.call("facts", fields=[])
        ascendancy = facts["ascendClassName"]
        start, adjacency = self.ctx.ascendancy_graph(ascendancy)
        if start is None:
            return False
        nodes = self.ctx.nodes
        current = sorted(p["id"] for p in facts["passives"]
                         if p.get("ascendancy") == ascendancy and p["type"] != "AscendClassStart")
        options = {i: [j for j in adjacency[i] if nodes[j].get("isMultipleChoiceOption")]
                   for i in adjacency if nodes[i].get("isMultipleChoice")}
        core = {i: [j for j in adjacency[i] if not nodes[j].get("isMultipleChoiceOption")]
                for i in adjacency if not nodes[i].get("isMultipleChoiceOption")}
        required = {i for i in adjacency if nodes[i].get("name") in set(self.ctx.compiled.request.require.passives)}

        allocations = self.allocations(start, core, options, required, adjacency)
        if len(allocations) > ASCENDANCY_CASES:
            ranked = self.rank_notables(start, core, options, required, adjacency, current)
            for keep in range(len(ranked), 0, -1):
                allowed = {start} | required
                for notable in ranked[:keep]:
                    allowed |= set(self.path_from(start, notable, core))
                for node in list(required):
                    allowed |= set(self.path_from(start, node, core))
                narrowed = {i: [j for j in core[i] if j in allowed] for i in core if i in allowed}
                allocations = self.allocations(start, narrowed, options, required, adjacency)
                if len(allocations) <= ASCENDANCY_CASES:
                    break
        if not allocations:
            return False
        scored = build.node_deltas([{"add": ids, "remove": current} for ids in allocations])
        best_score, best_ids = max(zip((s for s, _ in scored), allocations), key=lambda x: x[0])
        if best_score <= build.score + EPSILON or sorted(best_ids) == current:
            return False
        before = build.score
        build.allocate(remove=current, add=best_ids)
        self.round += 1
        if build.worse(before):
            build.allocate(remove=best_ids, add=self.bfs_order(start, set(current) | {start}, adjacency))
            return False
        return build.score > before + EPSILON

    def allocations(self, start: int, core: dict[int, list[int]], options: dict[int, list[int]],
                    required: set[int], adjacency: dict[int, list[int]]) -> list[list[int]]:
        """Every maximal connected allocation within `core` that holds the required nodes, with
        each multiple-choice node's options expanded, in allocation order."""
        subsets = [s for s in rooted_subsets(start, core, ASCENDANCY_POINTS) if required <= s]
        # Points are free here, so only allocations that can't take another node are worth scoring
        maximal = [s for s in subsets
                   if len(s) - 1 >= ASCENDANCY_POINTS or not any(n not in s for i in s for n in core[i])]
        allocations = []
        for subset in maximal or subsets:
            parents = [i for i in subset if options.get(i)]
            for picks in product(*(options[p] for p in parents)):
                allocations.append(self.bfs_order(start, subset | set(picks), adjacency))
        return allocations

    def rank_notables(self, start: int, core: dict[int, list[int]], options: dict[int, list[int]],
                      required: set[int], adjacency: dict[int, list[int]], current: list[int]) -> list[int]:
        """Notables by what each adds alone (with its path, best option), strongest first."""
        cases, keys = [], []
        for node in core:
            if node == start or self.ctx.nodes[node]["type"] != "Notable" or node in required:
                continue
            path = set(self.path_from(start, node, core))
            for pick in options.get(node) or [None]:
                members = path | required | ({pick} if pick else set())
                cases.append({"add": self.bfs_order(start, members, adjacency), "remove": current})
                keys.append(node)
        best: dict[int, float] = {}
        for (score, _), node in zip(self.build.node_deltas(cases), keys):
            best[node] = max(best.get(node, -math.inf), score)
        return sorted(best, key=lambda n: -best[n])

    @staticmethod
    def path_from(start: int, target: int, adjacency: dict[int, list[int]]) -> list[int]:
        """Shortest path from start to target (excluding start)."""
        parents = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == target:
                break
            for other in adjacency[node]:
                if other not in parents:
                    parents[other] = node
                    queue.append(other)
        if target not in parents:
            return []
        path = []
        while target != start:
            path.append(target)
            target = parents[target]
        return path[::-1]

    @staticmethod
    def bfs_order(start: int, members: set[int], adjacency: dict[int, list[int]]) -> list[int]:
        order, seen, queue = [], {start}, deque([start])
        while queue:
            node = queue.popleft()
            for other in adjacency[node]:
                if other in members and other not in seen:
                    seen.add(other)
                    order.append(other)
                    queue.append(other)
        return order
