"""Passive tree block (PLAN.md §5.2.2).

- Grow: every reachable target (small nodes nearby, notables and keystones further out) is scored
  as its whole path from the current tree in one what-if; the best gain per point is allocated.
  Values are kept between steps and only the top few and the changed paths are rechecked (lazy
  greedy), with a full recheck every few allocations.
- Steiner pairs: during full rechecks, pairs of strong targets are scored as the union of their
  paths, so two notables behind shared travel nodes can win together.
- Prune and regrow: branches that give least per point are removed and the points regrown; the
  change is kept only if the build ends better.
- Attributes: each allocated attribute node's Str/Dex/Int is a decision of its own; with
  Pathfinder's Traveller's Wisdom also 5% increased Damage, defences or Cost Efficiency.
- Ascendancy: every connected allocation of the ascendancy's points is enumerated and scored
  (with one option per multiple-choice node).

Forbidden passives never appear as targets or on a path; required passives are never removed.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, deque
from dataclasses import dataclass
from itertools import product

from builder import clock
from builder.search.state import EPSILON, Build

SMALL_REACH = 3
FAR_REACH = 9
TOP_RECHECK = 8
FULL_EVERY = 12
PAIR_POOL = 10
ASCENDANCY_POINTS = 8
# Meta skills ascendancy notables grant that the search fills (Tree.fill_granted): Deadeye's Called Shots (a Mark) and
# Mirage Deadeye (a ranged Attack), Smith of Kitava's Fire Spell on Hit (a Fire spell, gems.granted_meta_round), Martial
# Artist's Hollow Form (the main melee Attack moved into it, gems.hollow_round).
GRANTED_METAS = ("Called Shots", "Mirage Deadeye", "Fire Spell on Hit", "Hollow Form")
ASCENDANCY_CASES = 1500  # most allocations scored exactly in one round
STRENGTH, DEXTERITY, INTELLIGENCE = 1, 2, 3
GRANTS_SKILL = re.compile(r"Grants Skill: (?:Level \S+ )?(.+)$")
TRIALS = 2  # ascendancy allocations tried for real when they grant a skill an assumption uses
# Traveller's Wisdom: "Attribute Passive Skills can instead grant 5% increased Damage / Armour,
# Evasion and Energy Shield / Cost Efficiency" (bridge attribute options 4-6). It is worth nothing
# until the attribute nodes choose, so the ascendancy round tries it for real.
TRAVELLERS_WISDOM = "Traveller's Wisdom"
TRAVELLER_OPTIONS = (4, 5, 6)


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
    def restricting(self, adjacency: dict[int, list[int]]) -> dict[int, str]:
        """The nodes here whose lines restrict an equipment slot (gear.SLOT_RESTRICTIONS), with that slot."""
        from builder.search.gear import SLOT_RESTRICTIONS

        nodes, conquered = self.ctx.nodes, self.conquered_lines()
        return {i: SLOT_RESTRICTIONS[line][0] for i in adjacency
                for line in (conquered[i] if i in conquered else nodes.get(i, {}).get("stats") or [])
                if line in SLOT_RESTRICTIONS}

    def conquered_lines(self) -> dict[int, list[str]]:
        """The lines of the nodes a socketed Timeless jewel conquered (bridge conqueredNodes: PoB replaces a conquered
        keystone by the jewel's keystone, e.g. Dance with Death's "Can't use Helmets"; ctx.nodes has the raw tree's
        lines). Read once per commit."""
        commits = getattr(self.build, "commits", None)
        cached = self.__dict__.get("_conquered")
        if cached is None or cached[0] != commits:
            found = self.build.call("conqueredNodes").get("nodes") or []
            cached = self._conquered = (commits, {int(n["id"]): list(n.get("lines") or []) for n in found})
        return cached[1]

    def __init__(self, build: Build, rng: random.Random) -> None:
        self.build = build
        self.ctx = build.ctx
        self.rng = rng
        self.attribute = STRENGTH
        self.candidates: dict[int, Candidate] = {}
        self.round = 0
        self.allocations_since_full = FULL_EVERY
        self.required: set[int] = set()
        self.required_names: set[str] = set(self.ctx.compiled.request.require.passives)
        self.class_name: str | None = None  # the skeleton's, set by the search: names follow it (Context.node_as)
        self.ascendancy: str | None = None
        self.resolve = None  # re-resolves conditions and assumptions (set by the search)
        self.checkpoint = None  # marks a kept passive as where the hard stop goes back to (set by the search)
        self.relevant_skill = None  # does granting this skill change an assumption?
        # Takes off gear the build's passives no longer allow (Gear.enforce_restrictions; set by the search): an
        # ascendancy allocation with a restricting notable (Smith's Masterwork) is scored with its slot made legal
        self.enforce = None
        # Fills the group of a meta skill a notable grants (GRANTED_METAS) with what it uses; set by the search
        self.fill_granted = None
        # Nodes kept as a condition's source (builder/search/sources.py): a what-if removing one
        # can't see the condition go, so pruning leaves them alone until a reset or kick
        self.sources: set[int] = set()

    # Setup -------------------------------------------------------------------------------------

    def choose_default_attribute(self, main_skill: str) -> None:
        gem = self.ctx.gems.get(main_skill) or {}
        reqs = {STRENGTH: gem.get("reqStr") or 0, DEXTERITY: gem.get("reqDex") or 0, INTELLIGENCE: gem.get("reqInt") or 0}
        self.attribute = max(reqs, key=lambda a: (reqs[a], -a))

    @property
    def forbidden(self) -> set[int]:
        return self.ctx.forbidden_nodes(self.class_name, self.ascendancy)

    def allocate_required(self, class_name: str) -> list[str]:
        """Allocate required main-tree passives (with their paths), by the names this class sees.
        Returns names not found."""
        names = sorted(self.required_names)
        distances = self.ctx.main_tree_distances(class_name)
        missing = []
        for name in names:
            ids = [i for i, n in self.ctx.nodes.items()
                   if self.ctx.node_as(n, class_name, self.ascendancy).get("name") == name and not n.get("ascendancy")]
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

    def ensure_required(self) -> bool:
        """Put back a required main-tree passive the build no longer has (with its path). The search's deficits don't
        list required passives (only the delivered build's checks do), so one lost on the way went unseen: gate 4's
        2b75d delivered a build without its required Chaos Inoculation once each replica kept its own line (run.py).
        True if one had to go back."""
        allocated = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]}
        names = {self.ctx.node_as(self.ctx.nodes[i], self.class_name, self.ascendancy).get("name")
                 for i in allocated if i in self.ctx.nodes}
        wanted = {n for n in self.required_names
                  if any(not node.get("ascendancy") and node.get("name") == n for node in self.ctx.nodes.values())}
        if self.required <= allocated and wanted <= names:
            return False
        self.allocate_required(self.class_name)
        return True

    # Grow --------------------------------------------------------------------------------------

    def points_left(self) -> int:
        budget = self.build.call("pointBudget")
        return budget["passives"] - budget["used"]

    def frontier(self, limit: int) -> list[Candidate]:
        reach = min(FAR_REACH, limit)
        nodes = self.build.call("frontier", maxDistance=min(SMALL_REACH, limit),
                                maxDistanceByType={"Notable": reach, "Keystone": reach})["nodes"]
        forbidden = self.forbidden
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
        while clock.now() < deadline:
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
            # A node restricting an equipment slot (SLOT_RESTRICTIONS: a Timeless jewel's conquered Dance with Death,
            # "Can't use Helmets") was ranked by a what-if with today's helmet on: its commit is judged with the slot
            # made legal (Gear.enforce_restrictions), and a loss puts the build back, gear included (SEARCH6)
            restricting = (set(self.restricting({c.id for c in candidates} | set(self.conquered_lines()))) - allocated
                           if self.enforce is not None else set())  # (the frontier and conquered nodes: grow takes no ascendancy)
            snap = self.build.snapshot() if restricting else None
            self.build.allocate(add=choice, attribute=self.attribute)
            self.round += 1
            self.allocations_since_full += 1
            added = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]} - allocated if snap is not None else set()
            if restricting & added:
                self.enforce()
                if self.resolve is not None:
                    self.resolve()
                # (its real gain per point must also beat the next best candidate's what-if, which it was ranked over)
                runner_up = max((c.value for c in fresh if c.id not in choice), default=EPSILON)
                if self.build.worse(before) or (self.build.score - before) / max(len(added), 1) < runner_up:
                    self.build.restore(snap)
                    for node_id in choice:
                        self.candidates.pop(node_id, None)
                    rejected.update(choice)
                    self.allocations_since_full = FULL_EVERY
                    continue
            if self.build.worse(before):
                added = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]} - allocated
                self.build.allocate(remove=sorted(added))
                for node_id in choice:
                    self.candidates.pop(node_id, None)
                rejected.update(choice)
                self.allocations_since_full = FULL_EVERY
                continue
            improved = improved or self.build.score > before + EPSILON
            if self.checkpoint is not None:
                self.checkpoint()
        return improved

    # Prune and regrow ---------------------------------------------------------------------------

    def protected(self) -> set[int]:
        return set(self.required) | self.sources

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
            if clock.now() > deadline:
                break
            snap = self.build.snapshot()
            before = self.build.score
            self.remove_branch(branch)
            self.grow(deadline)
            if self.resolve is not None:
                self.resolve()  # the branch may have been a condition's source
            if self.build.score > before + EPSILON:
                return True
            self.build.restore(snap)
            self.round += 1
        return False

    def fit_requirements(self, deadline: float, attempts: int = 4) -> None:
        """New gear's attribute requirements: re-pick the attribute passives, then while one is still short trade the
        weakest branches for points grown under the deficit's penalty. For a gear trial (gear.granted_meta_trials),
        which keeps or restores the whole build itself, so nothing here is checked for gain."""
        self.attributes_round()
        for _ in range(attempts):
            if clock.now() > deadline or not any(label.endswith(" requirement") for label, _, hard
                                                 in self.build.objective.deficits(self.build.stats) if hard):
                return
            branches = self.weakest_branches()
            if not branches:
                return
            self.remove_branch(branches[0][1])
            self.grow(deadline)
            self.attributes_round()

    def remove_branch(self, branch: frozenset[int]) -> None:
        self.build.allocate(remove=sorted(branch))
        self.round += 1
        self.allocations_since_full = FULL_EVERY

    def reset(self) -> None:
        """Clear every passive that can go (required ones and their paths stay), to regrow."""
        self.sources.clear()
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
        self.sources.clear()
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

    def has_passive(self, name: str) -> bool:
        return any(p["name"] == name for p in self.build.call("facts", fields=[])["passives"])

    def attributes_round(self) -> bool:
        nodes = self.build.call("attributeNodes")["nodes"]
        if not nodes:
            return False
        options = (STRENGTH, DEXTERITY, INTELLIGENCE)
        if self.has_passive(TRAVELLERS_WISDOM):
            options += TRAVELLER_OPTIONS
        cases, keys = [], []
        for node in nodes:
            for attribute in options:
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
        # Each switch was scored alone; together they can break an attribute requirement (all 29
        # Dexterity passives taking Traveller's Damage at once), so fall back to the best half
        ranked = sorted(best.items(), key=lambda item: -item[1][0])
        while ranked:
            before = self.build.score
            self.build.set_attributes({node_id: attribute for node_id, (_, attribute) in ranked})
            if self.build.score >= before - EPSILON:
                improved = self.build.score > before + EPSILON
                chosen.update({node_id: attribute for node_id, (_, attribute) in ranked})
                break
            self.build.set_attributes({node_id: chosen[node_id] for node_id, _ in ranked})
            ranked = ranked[:len(ranked) // 2]
        counts = Counter(a for a in chosen.values() if a in (STRENGTH, DEXTERITY, INTELLIGENCE))
        if counts:  # new paths take the most common attribute (Traveller's choices aren't one)
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
        current = sorted(p["id"] for p in facts["passives"] if p["id"] in adjacency and p["id"] != start)
        options = {i: [j for j in adjacency[i] if nodes[j].get("isMultipleChoiceOption")]
                   for i in adjacency if nodes[i].get("isMultipleChoice")}
        core = {i: [j for j in adjacency[i] if not nodes[j].get("isMultipleChoiceOption")]
                for i in adjacency if not nodes[i].get("isMultipleChoiceOption")}
        required = {i for i in adjacency if nodes[i].get("name") in self.required_names}

        allocations = self.narrowed_allocations(start, core, options, required, adjacency, current)
        # A notable restricting an equipment slot (SLOT_RESTRICTIONS: Smith's Masterwork's "Can only use a Normal Body
        # Armour"): not offered when that slot holds a required item (unless the request requires the notable too)
        restricting = self.restricting(adjacency)
        locked = {i for i, slot in restricting.items() if slot in build.locked_slots and i not in required}
        allocations = [ids for ids in allocations if not locked & set(ids)]
        if not allocations:
            return False
        scored = list(build.node_deltas([{"add": ids, "remove": current} for ids in allocations]))
        previous = self.bfs_order(start, set(current) | {start}, adjacency)
        # ... and its what-if is scored with today's gear still on (a rare body armour: an overcount until the next gear
        # round swaps it): the best few such allocations are scored for real with the slot made legal instead
        held = {i for i in restricting if i in current}  # (already allocated: the gear obeys it already)
        if restricting and self.enforce is not None:
            fresh = [k for k, ids in enumerate(allocations) if (set(restricting) - held) & set(ids)]
            trial = sorted(fresh, key=lambda k: -scored[k][0])[:TRIALS]
            snap = build.snapshot()
            for k in trial:
                build.allocate(remove=current, add=allocations[k])
                self.enforce()
                if self.resolve is not None:
                    self.resolve()
                scored[k] = (build.score, scored[k][1])
                build.restore(snap)
            scored = [(-math.inf, x) if k in set(fresh) - set(trial) else (s, x) for k, (s, x) in enumerate(scored)]
        best_score, best_ids = max(zip((s for s, _ in scored), allocations), key=lambda x: x[0])
        # Nodes granting a skill an assumption depends on (e.g. a Remnant source) only show their
        # worth once the assumption updates: try the best few such allocations for real
        if self.resolve is not None and self.relevant_skill is not None:
            granting = {i for i in adjacency for line in (nodes[i].get("stats") or [])
                        if (m := GRANTS_SKILL.match(line)) and self.relevant_skill(m.group(1).strip())}
            if granting:
                ranked = sorted(((s, ids) for (s, _), ids in zip(scored, allocations) if granting & set(ids)),
                                key=lambda x: -x[0])
                for _, ids in ranked[:TRIALS]:
                    build.allocate(remove=current, add=ids)
                    self.resolve()
                    if build.score > best_score:
                        best_score, best_ids = build.score, ids
                    build.allocate(remove=ids, add=previous)
                    self.resolve()
        # A notable granting a meta skill adds nothing until its group holds what it uses (PLAN.md meta slice 8)
        if self.fill_granted is not None and not required - set(current) - {start}:
            for node in adjacency:
                skill = next((m.group(1).strip() for line in (nodes[node].get("stats") or [])
                              if (m := GRANTS_SKILL.match(line)) and m.group(1).strip() in GRANTED_METAS), None)
                if skill is None or node in current or node in best_ids:
                    continue
                if self.granted_trial(skill, max(best_score, build.score),
                                      self.narrowed_allocations(start, core, options, required | {node}, adjacency, current),
                                      current):
                    return True
        missing_required = bool(required - set(current) - {start})
        traveller = next((i for i in adjacency if nodes[i].get("name") == TRAVELLERS_WISDOM), None)
        if traveller is not None and traveller not in current and traveller not in best_ids:
            with_traveller = [(s, ids) for (s, _), ids in zip(scored, allocations) if traveller in ids]
            if with_traveller and not missing_required and self.traveller_trial(
                    max(with_traveller, key=lambda x: x[0])[1], current):
                return True
        if sorted(best_ids) == current or (best_score <= build.score + EPSILON and not missing_required):
            return False
        before = build.score
        restricts = self.enforce is not None and bool(set(restricting) & set(best_ids))
        snap = build.snapshot() if restricts else None
        build.allocate(remove=current, add=best_ids)
        if restricts:
            self.enforce()
        if self.resolve is not None:
            self.resolve()
        self.round += 1
        if not missing_required and build.worse(before) and snap is not None:
            build.restore(snap)  # (the gear the restriction took off comes back too)
            return False
        if not missing_required and build.worse(before):
            build.allocate(remove=best_ids, add=previous)
            if self.resolve is not None:
                self.resolve()
            return False
        return build.score > before + EPSILON or missing_required

    def narrowed_allocations(self, start: int, core: dict[int, list[int]], options: dict[int, list[int]],
                             required: set[int], adjacency: dict[int, list[int]], current: list[int]) -> list[list[int]]:
        """allocations(), narrowed to the strongest notables when there are more than ASCENDANCY_CASES."""
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
        return allocations

    def granted_trial(self, skill: str, beat: float, allocations: list[list[int]], current: list[int]) -> bool:
        """Take the best allocation with the notable granting this meta skill, fill the skill's group (fill_granted:
        the marks round for Called Shots, the mirages round for Mirage Deadeye), and keep it only if the build ends
        better than `beat` (the best allocation without it, or the build as it is)."""
        if not allocations:
            return False
        build = self.build
        ids = max(zip((s for s, _ in build.node_deltas([{"add": a, "remove": current} for a in allocations])),
                      allocations), key=lambda x: x[0])[1]
        snap = build.snapshot()
        build.allocate(remove=current, add=ids)
        if self.resolve is not None:
            self.resolve()
        self.fill_granted(skill)
        if build.score > beat + EPSILON:
            self.round += 1
            return True
        build.restore(snap)
        return False

    def traveller_trial(self, ids: list[int], current: list[int]) -> bool:
        """Take an ascendancy allocation with Traveller's Wisdom, let the attribute passives choose,
        and keep it only if the build ends better."""
        build = self.build
        snap = build.snapshot()
        before = build.score
        build.allocate(remove=current, add=ids)
        if self.resolve is not None:
            self.resolve()
        self.attributes_round()
        self.round += 1
        if build.score > before + EPSILON:
            return True
        build.restore(snap)
        return False

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
