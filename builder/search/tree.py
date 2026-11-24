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

import contextlib
import math
import os
import random
import re
from collections import Counter, deque
from collections.abc import Callable
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
# (SEARCH37; THE USER, 23 Nov, on the Oracle build: "ascendancies are very powerful ... it should either do that or figure
# out a way to use the nodes it chooses to take") Ascendancy notables whose whole worth is the main-tree passives they let
# the tree take: they score 0 alone, so the round never chose them. Which (checked: every ascendancy node's lines in PoB's
# TreeData 0_5 tree.json against "allocat|paths not taken|without being connected|unlock", and every unlockConstraint:
# only Oracle's 197 nodes hang on an ascendancy node; RESEARCH49 R49-9 for Oracle's two; Pathfinder's two by the tree's
# text and PoB's ModCache AlternateClassStart only, game files UNVERIFIED): Oracle's The Unseen Path ("Walk the Paths Not
# Taken": 197 Oracle-only nodes, PoB's unlockConstraint) and Entwined Realities (non-keystone passives in Medium radius of
# allocated keystones without a connection: PoB's AllocateFromNodeRadius, offered by bridge.frontier at one point), and
# Pathfinder's Path of the Sorceress / Path of the Warrior (PoB's AlternateClassStart). Each gets a real trial: the best
# allocation holding it, then the tree's prune-and-regrow for UNLOCK_SHARE of the step's time (at most UNLOCK_SECONDS),
# kept only if it ends higher than the same regrow on the best allocation without it (a fair control: the regrow alone
# may gain). LOW: the two time numbers.
UNLOCK_LINE = re.compile(r"Walk the Paths Not Taken|can be allocated without being connected to your tree"
                         r"|Can Allocate Passive(?: Skill)?s from the \w+'s starting point", re.IGNORECASE)
UNLOCK_SHARE = 0.25
UNLOCK_SECONDS = 90.0
TRAVELLER_OPTIONS = (4, 5, 6)


@dataclass
class Candidate:
    id: int
    path: tuple[int, ...]
    value: float = -math.inf  # score gain per point when last evaluated
    gain: float = -math.inf
    stamp: int = -1  # evaluation round; values from older rounds are stale
    socket: bool = False  # (JEWEL1) the target is an empty jewel socket: its what-if adds Tree.socket_value

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
    socket_value: Callable[[], float] | None = None  # (JEWEL2: class defaults, for a Tree made without __init__)
    fill_socket: Callable[[int, float], bool] | None = None
    anchored: dict[int, set[int]] = {}  # (SEARCHB8: the same; only ever replaced, never changed in place)

    def restricting(self, adjacency: dict[int, list[int]]) -> dict[int, str]:
        """The nodes here whose lines restrict an equipment slot (gear.SLOT_RESTRICTIONS), with that slot."""
        from builder.search.gear import SLOT_RESTRICTIONS

        nodes, conquered = self.ctx.nodes, self.conquered_lines()
        return {i: SLOT_RESTRICTIONS[line][0] for i in adjacency
                for line in (conquered[i] if i in conquered else nodes.get(i, {}).get("stats") or [])
                if line in SLOT_RESTRICTIONS}

    def conquered_lines(self) -> dict[int, list[str]]:
        """The lines of the nodes a socketed Timeless jewel conquered (bridge conqueredNodes: PoB replaces a conquered
        keystone by the jewel's keystone; ctx.nodes has the raw tree's lines; "Can't use Helmets" is PoE1 Maraketh data no
        PoE2 jewel grants: gear.SLOT_RESTRICTIONS). Read once per commit."""
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
        # (JEWEL1, the tree's jewel-socket valuation, on loan from SEARCH-B) What one more filled jewel socket adds to the
        # score (Jewels.socket_value), and filling a socket the tree just took (Jewels.fill_new_socket); both set by Jewels.
        # A socket's what-if counts only its travel nodes (an empty socket gives nothing), so the tree never reached for
        # sockets it would fill: with POE2_SEARCH_JEWEL_SOCKETS on, sockets join the frontier at the notables' reach and
        # their what-if adds this value; a taken socket is filled at once and kept only if the build really gains
        self.socket_value: Callable[[], float] | None = None
        self.fill_socket: Callable[[int, float], bool] | None = None
        # (SEARCHB8) {allocated ascendancy node: the main-tree passives that hang off it alone} for the current
        # ascendancy round (path_anchored)
        self.anchored: dict[int, set[int]] = {}

    @staticmethod
    def jewel_sockets_enabled() -> bool:
        """(JEWEL1) Jewel sockets valued at the jewel they would hold, in grow: POE2_SEARCH_JEWEL_SOCKETS, OFF by default
        (read at call time, so a gate arm sets it per run)."""
        return os.environ.get("POE2_SEARCH_JEWEL_SOCKETS", "0") not in ("", "0", "false", "off")

    def sockets_valued(self) -> bool:
        return self.socket_value is not None and self.fill_socket is not None and self.jewel_sockets_enabled()

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
        by_type = {"Notable": reach, "Keystone": reach}
        sockets = self.sockets_valued()
        if sockets:
            by_type["Socket"] = reach
        nodes = self.build.call("frontier", maxDistance=min(SMALL_REACH, limit), maxDistanceByType=by_type,
                                **({"sockets": True} if sockets else {}))["nodes"]
        forbidden = self.forbidden
        current: dict[int, Candidate] = {}
        for node in nodes:
            path = tuple(node["path"])
            if node["id"] in forbidden or forbidden.intersection(path) or len(path) > limit:
                continue
            known = self.candidates.get(node["id"])
            if known is None or known.path != path:
                known = Candidate(node["id"], path, socket=sockets and node.get("type") == "Socket")
            current[node["id"]] = known
        self.candidates = current
        return list(current.values())

    def evaluate(self, candidates: list[Candidate]) -> None:
        cases = [{"add": [c.id], "addPaths": True, "attribute": self.attribute} for c in candidates]
        jewel = self.socket_value() if self.sockets_valued() and any(c.socket for c in candidates) else 0.0
        for candidate, (score, _) in zip(candidates, self.build.node_deltas(cases)):
            candidate.gain = score - self.build.score + (jewel if candidate.socket else 0.0)
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
            # A node restricting an equipment slot (SLOT_RESTRICTIONS: Smith of Kitava's Smith's Masterwork; the PoE1
            # "Can't use Helmets" no PoE2 jewel grants) was ranked by a what-if with today's helmet on: its commit is judged with the slot
            # made legal (Gear.enforce_restrictions), and a loss puts the build back, gear included (SEARCH6)
            restricting = (set(self.restricting({c.id for c in candidates} | set(self.conquered_lines()))) - allocated
                           if self.enforce is not None else set())  # (the frontier and conquered nodes: grow takes no ascendancy)
            snap = self.build.snapshot() if restricting else None
            socket_ids = [c.id for c in fresh if c.socket and c.id in choice] if self.sockets_valued() else []
            socket_snap = self.build.snapshot() if socket_ids else None
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
            if socket_snap is not None:
                # (JEWEL1) its value was the jewel it would hold: fill it now, and keep it only for a real gain
                for socket in socket_ids:
                    self.fill_socket(socket, deadline)
                if self.build.score <= before + EPSILON:
                    self.build.restore(socket_snap)
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

    def trim_to_budget(self) -> int:
        """Remove the weakest branches until the tree fits the level's points (points_left); the number of passives
        removed."""
        trimmed = 0
        while (need := -self.points_left()) > 0:
            ranked = self.weakest_branches()
            if not ranked:
                break
            # The weakest branches per point that fit in what's over, apart from each other, until it's covered (the
            # weakest per point alone can be a whole arm: SEARCH8's seed test lost 112 passives for 19 over); only
            # when none fits, the smallest
            removed: set[int] = set()
            for _, branch in ranked:
                if len(branch) <= need - len(removed) and not branch & removed:
                    removed |= branch
                    self.remove_branch(branch)
                if len(removed) >= need:
                    break
            if not removed:
                branch = min(ranked, key=lambda r: (len(r[1]), r[0]))[1]
                removed = set(branch)
                self.remove_branch(branch)
            trimmed += len(removed)
        return trimmed

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

    # Final regrow on the finished gear (SEARCHB2) -------------------------------------------------

    def shortfall(self) -> float:
        """How far the build is from meeting the request (objective.shortfall: a game limit counts ten times a floor)."""
        return sum(amount * (10.0 if hard else 1.0) for _, amount, hard in self.build.objective.deficits(self.build.stats)
                   if hard is not None)  # (soft floors are no shortfall: objective.soft_cost's note)

    def floor_step(self, deadline: float) -> bool:
        """Allocate the target (with its path) that cuts the build's penalty for the request's floors and game limits
        most per point, if any cuts it. Grow ranks by score per point, where one point that buys a few % of a floor is
        worth less than one that buys damage until the floor is nearly met (its FLOOR_OFFSET is paid only at zero): so
        from an empty tree it spends the points on damage and ends short (G0b's controls, SEARCHB1). This ranks by the
        penalty alone."""
        left = self.points_left()
        if left <= 0 or clock.now() > deadline:
            return False
        candidates = self.frontier(left)
        if not candidates:
            return False
        objective = self.build.objective
        before = objective.penalty(self.build.stats)
        cases = [{"add": [c.id], "addPaths": True, "attribute": self.attribute} for c in candidates]
        best, best_value = None, EPSILON
        for candidate, (_, stats) in zip(candidates, self.build.node_deltas(cases)):
            value = (before - objective.penalty(stats)) / max(candidate.cost, 1)
            if value > best_value:
                best, best_value = candidate, value
        if best is None:
            return False
        self.build.allocate(add=[best.id], attribute=self.attribute)
        self.round += 1
        self.allocations_since_full = FULL_EVERY
        return True

    def repair_floors(self, deadline: float, target: float, trades: int = 12) -> None:
        """Bring the shortfall back to `target` (the build's before a regrow): free points go to floor_step; with none
        left, the weakest branch (by score per point, so never one a floor leans on more than its damage) is traded
        for them, at most `trades` times."""
        for _ in range(trades * 4):
            if clock.now() > deadline or self.shortfall() <= target + EPSILON:
                return
            if self.floor_step(deadline):
                continue
            if trades <= 0:
                return
            trades -= 1
            branches = self.weakest_branches()
            if not branches:
                return
            self.remove_branch(branches[0][1])
            if not self.floor_step(deadline):
                return

    def regrow_variant(self, deadline: float, clear: str, target: float) -> None:
        """One regrow of the tree: `clear` "all" (Tree.reset) or "weak:<points>" (the weakest branches per point, about
        that many points), then grow, the floors repaired to `target`, grow again into what's left, and the attribute
        choices; the conditions re-resolved (a cleared passive may have been a condition's source)."""
        self.clear_variant(clear)
        self.grow(deadline)
        self.repair_floors(deadline, target)
        self.grow(deadline)
        self.attributes_round()
        if self.resolve is not None:
            self.resolve()

    def clear_variant(self, clear: str) -> None:
        """regrow_variant's clear: "all" (Tree.reset) or "weak:<points>" (the weakest branches per point, about that many
        points)."""
        if clear == "all":
            self.reset()
            return
        size = int(clear.split(":")[1])
        removed: set[int] = set()
        for _, branch in self.weakest_branches():
            if len(removed) >= size:
                break
            if not branch & removed and len(branch) <= size - len(removed) + 3:
                self.remove_branch(branch)
                removed |= branch
        self.candidates.clear()

    @contextlib.contextmanager
    def offence_only(self):
        """The build scored with the user's soft floors off (resistances, Life, max hit, the defence style) and the game's
        hard limits kept (the deficits Objective flags hard: attribute requirements, skill costs, passive points,
        Spirit), so a grow inside ranks passives by damage alone. The real score comes back after, and the candidates'
        values (measured on the other score) are dropped. A search move only: whatever it builds is judged on the
        real score (joint_regrow's keep rule), so no floor is relaxed in a delivered build."""
        objective = self.build.objective
        objective.penalty = lambda stats: objective._weight([d for d in objective.deficits(stats) if d[2]])
        try:
            self.build.refresh()
            self.candidates.clear()
            yield
        finally:
            del objective.penalty
            self.build.refresh()
            self.candidates.clear()
            self.round += 1
            self.allocations_since_full = FULL_EVERY

    SWITCH_JOINT = "POE2_SEARCH_JOINT_REGROW"  # "1": the search's call site runs joint_regrow (OFF by default)

    @classmethod
    def joint_enabled(cls) -> bool:
        """POE2_SEARCH_JOINT_REGROW=1 turns the joint tree + gear move on (OFF by default, until a gate pair measures
        it: the manager, 22 Nov). Read at call time, so a probe or a gate arm can set it per run."""
        return os.environ.get(cls.SWITCH_JOINT, "0") == "1"

    SWITCH_UNLOCK = "POE2_SEARCH_ASCEND_UNLOCK"  # "1": ascendancy_round runs unlock_trial (OFF by default)

    @classmethod
    def unlock_enabled(cls) -> bool:
        """POE2_SEARCH_ASCEND_UNLOCK=1 turns the ascendancy unlock trials on (OFF until a gate set of at least 3 seeds per
        arm measures them higher: the manager, 23 Nov 15:45). Read at call time, so a gate arm can set it per run."""
        return os.environ.get(cls.SWITCH_UNLOCK, "0") == "1"

    JOINT_GEAR_SHARE = 0.6  # of a joint_regrow's time its gear round may take (LOW, a search-effort setting)

    def joint_regrow(self, deadline: float, gear_round: Callable[[float, bool], bool], clear: str = "weak:30",
                     reweigh: Callable[[], None] | None = None) -> dict:
        """The joint tree + gear move (SEARCHB2's design, Step 2(c); SEARCHB1's G0b: the reference trees trimmed to the
        budget keep x2.03 / x2.09 but meet ES, Lightning resistance and Physical max hit with ITEMS; each block alone
        holds the other fixed, so with a floor miss costing >= 3.0 in the score neither takes the first step). One
        all-or-nothing trial from a snapshot:
        1. clear `clear` (clear_variant) and grow for damage (offence_only: the soft floors off, the hard limits on);
        2. a full gear round (`gear_round`, the search's Gear.gear_round) on that tree, on the real score, whose floor
           penalty now ranks the items that close the floors first, for up to JOINT_GEAR_SHARE of the time;
        3. the floors repaired back to the shortfall the build had before (repair_floors), grow, attributes, resolve;
        4. kept only if no further from the request (the shortfall) and scoring higher (final_regrow's rule).
        Returns {"clear", "score", "gain", "shortfall", "kept", "seconds"} like final_regrow's rows."""
        build = self.build
        started = clock.now()
        best_score, best_short = build.score, self.shortfall()
        snap = build.snapshot()
        self.clear_variant(clear)
        with self.offence_only():
            self.grow(deadline)
        now = clock.now()
        gear_round(min(deadline, now + self.JOINT_GEAR_SHARE * max(0.0, deadline - now)), True)
        self.repair_floors(deadline, best_short)
        self.grow(deadline)
        self.attributes_round()
        if self.resolve is not None:
            self.resolve()
        if reweigh is not None:
            reweigh()
        score, short = build.score, self.shortfall()
        kept = self.points_left() >= 0 and short <= best_short + EPSILON and score > best_score + EPSILON
        row = {"clear": f"joint {clear}", "score": round(score, 4), "gain": round(score - best_score, 4),
               "shortfall": round(short, 4), "kept": kept}
        if not kept:
            build.restore(snap)
            self.candidates.clear()
            self.round += 1
            self.allocations_since_full = FULL_EVERY
        row["seconds"] = round(clock.now() - started, 1)
        return row

    FINAL_CLEARS = ("all", "weak:30", "weak:15", "weak:8")
    SWAP_MIN_GAIN = 1e-3  # (ln units: 0.1% of the score's DPS) a final swap gaining less ends the swaps

    def final_regrow(self, deadline: float, clears: tuple[str, ...] = FINAL_CLEARS,
                     reweigh: Callable[[], None] | None = None) -> list[dict]:
        """A last regrow of the tree on the finished gear (SEARCHB1's G0b: the search's own grower, regrowing the
        delivered tree on the delivered gear, read x1.52 on 2b75d and x2.50 on 2b75b: the tree was grown against
        earlier gear and never rebuilt for the final one; but both regrows missed the Physical max hit floor).

        Each clear in `clears` is tried from the best build so far (regrow_variant). A variant is kept only if it is
        no further from the request than the best (the shortfall, the delivered results' rank: objective.rank_key) and
        scores higher; else the build goes back. Then the prune-and-regrow swaps run until none pays. The floors and the
        user's bans are never relaxed: a variant short on anything the best build meets is never kept.
        `reweigh` (the search's weigh_uptimes) is called after each variant, before it is judged: the floors are read
        with the part-time effects off (objective.floor_deltas), and those deltas belong to the tree they were measured
        on (SEARCHB1's G0b probe regrew before they were set and read every regrow as short on Phys max hit).
        Returns one row per try: {"clear", "score", "gain" (over the best before it), "shortfall", "kept", "seconds"
        (the try's clock time, its restore included: SEARCH35 sizes the clears by it)}."""
        build = self.build
        best_score, best_short = build.score, self.shortfall()
        rows: list[dict] = []
        for clear in clears:
            if clock.now() > deadline:
                break
            started = clock.now()
            snap = build.snapshot()
            self.regrow_variant(deadline, clear, best_short)
            if reweigh is not None:
                reweigh()
            score, short = build.score, self.shortfall()
            kept = (self.points_left() >= 0 and short <= best_short + EPSILON and score > best_score + EPSILON)
            rows.append({"clear": clear, "score": round(score, 4), "gain": round(score - best_score, 4),
                         "shortfall": round(short, 4), "kept": kept})
            if kept:
                best_score, best_short = score, short
            else:
                build.restore(snap)
                self.candidates.clear()
                self.round += 1
                self.allocations_since_full = FULL_EVERY
            rows[-1]["seconds"] = round(clock.now() - started, 1)
        while clock.now() < deadline:
            started = clock.now()
            snap = build.snapshot()
            if not self.swap_round(deadline):
                break
            if reweigh is not None:
                reweigh()
            if self.shortfall() > best_short + EPSILON or build.score < best_score + self.SWAP_MIN_GAIN:
                # (swap_round keeps any higher score: a floor given up for it goes back, and so does a gain too small
                # to be worth another round: probe POE2T6221, 2b75d, swapped to its deadline on gains under 0.01%)
                if build.score < best_score - EPSILON or self.shortfall() > best_short + EPSILON:
                    build.restore(snap)
                    self.candidates.clear()
                else:
                    best_score = build.score
                break
            rows.append({"clear": "swap", "score": round(build.score, 4), "gain": round(build.score - best_score, 4),
                         "shortfall": round(self.shortfall(), 4), "kept": True, "seconds": round(clock.now() - started, 1)})
            best_score = build.score
        return rows

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

    def ascendancy_round(self, deadline: float | None = None) -> bool:
        """Score every full allocation of the ascendancy points and keep the best. Large
        ascendancies are first narrowed to their strongest notables (each scored alone), then
        enumerated exactly within that set. With a deadline, a notable that unlocks main-tree passives (UNLOCK_LINE)
        gets a real trial with the tree regrown (unlock_trial)."""
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
        self.anchored = self.path_anchored(facts, adjacency)

        allocations = self.narrowed_allocations(start, core, options, required, adjacency, current)
        # A notable restricting an equipment slot (SLOT_RESTRICTIONS: Smith's Masterwork's "Can only use a Normal Body
        # Armour"): not offered when that slot holds a required item (unless the request requires the notable too)
        restricting = self.restricting(adjacency)
        locked = {i for i, slot in restricting.items() if slot in build.locked_slots and i not in required}
        allocations = [ids for ids in allocations if not locked & set(ids)]
        if not allocations:
            return False
        scored = list(build.node_deltas([{"add": ids, "remove": self.removal(current, ids)} for ids in allocations]))
        previous = self.bfs_order(start, set(current) | {start}, adjacency)
        # ... and its what-if is scored with today's gear still on (a rare body armour: an overcount until the next gear
        # round swaps it): the best few such allocations are scored for real with the slot made legal instead
        held = {i for i in restricting if i in current}  # (already allocated: the gear obeys it already)
        if restricting and self.enforce is not None:
            fresh = [k for k, ids in enumerate(allocations) if (set(restricting) - held) & set(ids)]
            trial = sorted(fresh, key=lambda k: -scored[k][0])[:TRIALS]
            snap = build.snapshot()
            for k in trial:
                self.swap(current, allocations[k])
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
                    snap = build.snapshot() if self.anchored else None
                    self.swap(current, ids)
                    self.resolve()
                    if build.score > best_score:
                        best_score, best_ids = build.score, ids
                    if snap is not None:
                        build.restore(snap)  # (a re-add would leave the passives under a Path node behind)
                        continue
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
        # (SEARCH37: notables whose worth is what they unlock; behind its switch, the manager 23 Nov 15:45)
        if deadline is not None and not missing_required and self.unlock_enabled():
            for node in sorted(adjacency):
                if node in current or node in best_ids or clock.now() > deadline:
                    continue
                if not any(UNLOCK_LINE.search(line) for line in (nodes[node].get("stats") or [])):
                    continue
                holding = self.narrowed_allocations(start, core, options, required | {node}, adjacency, current)
                holding = [ids for ids in holding if node in ids and not locked & set(ids)]
                if not holding:
                    continue
                ids = max(zip((s for s, _ in build.node_deltas([{"add": a, "remove": self.removal(current, a)}
                                                                for a in holding])),
                              holding), key=lambda x: x[0])[1]
                if self.unlock_trial(ids, best_ids, current, deadline):
                    return True
        if sorted(best_ids) == current or (best_score <= build.score + EPSILON and not missing_required):
            return False
        before = build.score
        restricts = self.enforce is not None and bool(set(restricting) & set(best_ids))
        snap = build.snapshot() if restricts or self.anchored else None
        self.swap(current, best_ids)
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

    def path_anchored(self, facts: dict, adjacency: dict[int, list[int]]) -> dict[int, set[int]]:
        """(SEARCHB8; PREVIEW3's lead, 23 Nov) {allocated ascendancy node: the allocated main-tree passives reachable only
        through it}. Pathfinder's Path of the Sorceress / Path of the Warrior ("Can Allocate Passive Skills from the
        <class>'s starting point") link straight onto main-tree passives, and PoB paths across that link
        (PassiveSpec.lua 1449), so its DeallocNode drops the passives hanging off such a node with it, while a what-if
        that removes the node alone (bridge.nodeDeltas' removeNodes) keeps them. Read from the tree's links: a passive
        stays if the class start or another such node still reaches it through allocated main-tree passives."""
        nodes = self.ctx.nodes
        held = {p["id"] for p in facts["passives"]}
        main = {i for i in held if i in nodes and not nodes[i].get("ascendancy")}
        links = {i: [j for j in nodes[i].get("linked") or () if j in main] for i in held if i in adjacency}
        links = {i: js for i, js in links.items() if js}
        if not links:
            return {}
        start = next((c["startNode"] for c in self.ctx.data.tree["classes"] if c["name"] == facts.get("className")), None)

        def reach(seeds: set[int]) -> set[int]:
            seen = {i for i in seeds if i in main}
            queue = deque(seen | ({start} if start in seeds else set()))
            while queue:
                for j in nodes.get(queue.popleft(), {}).get("linked") or ():
                    if j in main and j not in seen:
                        seen.add(j)
                        queue.append(j)
            return seen

        anchored = {}
        for node, ends in links.items():
            kept = reach({start, *(j for other, js in links.items() if other != node for j in js)})
            lost = reach(set(ends)) - kept
            if lost:
                anchored[node] = lost
        return anchored

    def removal(self, current: list[int], ids: list[int]) -> list[int]:
        """What a what-if moving the ascendancy from `current` to `ids` takes off: the current ascendancy nodes, and the
        main-tree passives hanging off any of them `ids` drops (path_anchored; the commit's DeallocNode drops those)."""
        gone = set().union(*(lost for node, lost in self.anchored.items() if node not in ids))
        return sorted(set(current) | gone) if gone else current

    def swap(self, current: list[int], ids: list[int]) -> None:
        """Commit the ascendancy allocation `ids` in place of `current`. With passives hanging off an ascendancy node
        (path_anchored), only the nodes that change move: taking every current node off would drop the passives under a
        Path node `ids` keeps (DeallocNode), and re-adding the node brings none of them back. The dropped nodes go first
        (bridge.allocate removes before it adds), through DeallocNode, which takes their passives with them: adding a
        sibling option first would take the old option off with PoB's DeallocSingleNode (PassiveSpec.lua 945-960),
        which leaves its passives allocated with no route. A kept node's route is kept whole: Pathfinder's ascendancy
        graph is a tree (24 nodes, 23 links, build 26650308's PoB tree), so each node has one route."""
        if not self.anchored:
            self.build.allocate(remove=current, add=ids)
            return
        self.build.allocate(remove=[i for i in current if i not in ids], add=[i for i in ids if i not in current])

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
        ids = max(zip((s for s, _ in build.node_deltas([{"add": a, "remove": self.removal(current, a)}
                                                        for a in allocations])),
                      allocations), key=lambda x: x[0])[1]
        snap = build.snapshot()
        self.swap(current, ids)
        if self.resolve is not None:
            self.resolve()
        self.fill_granted(skill)
        if build.score > beat + EPSILON:
            self.round += 1
            return True
        build.restore(snap)
        return False

    def unlock_trial(self, ids: list[int], control: list[int], current: list[int], deadline: float) -> bool:
        """(SEARCH37) Take the ascendancy allocation `ids` (it holds a notable that unlocks main-tree passives), regrow the
        tree for UNLOCK_SHARE of the time left (at most UNLOCK_SECONDS), and keep it only if it ends higher than the
        build did and than the same regrow on `control` (the best allocation without it); a control that ends higher
        than the build is kept instead (the regrow's own gain)."""
        build = self.build
        snap, before = build.snapshot(), build.score
        now = clock.now()
        spend = min(UNLOCK_SECONDS, UNLOCK_SHARE * max(0.0, deadline - now))
        if spend <= 0:
            return False
        self.swap(current, ids)
        if self.resolve is not None:
            self.resolve()
        self.regrow_until(now + spend)
        trial = build.score
        if trial <= before + EPSILON:
            build.restore(snap)
            return False
        won = build.snapshot()
        build.restore(snap)
        if sorted(control) != current:
            self.swap(current, control)
            if self.resolve is not None:
                self.resolve()
        self.regrow_until(clock.now() + spend)
        self.round += 1
        if build.score >= trial:
            if build.score > before + EPSILON:
                return True
            build.restore(snap)
            return False
        build.restore(won)
        return True

    def regrow_until(self, stop: float) -> None:
        """(SEARCH37, unlock_trial) Spend free points, then prune-and-regrow while it pays, until `stop`."""
        self.grow(stop)
        while clock.now() < stop and self.swap_round(stop):
            pass

    def traveller_trial(self, ids: list[int], current: list[int]) -> bool:
        """Take an ascendancy allocation with Traveller's Wisdom, let the attribute passives choose,
        and keep it only if the build ends better."""
        build = self.build
        snap = build.snapshot()
        before = build.score
        self.swap(current, ids)
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
