"""Condition sources (PLAN.md §5.4): look for the cheapest source of each condition worth having,
instead of stumbling on one.

The other blocks score their moves with PoB what-ifs, which can't see a condition a move would
switch on: the resolver needs the move committed to find its evidence, so a source that does
nothing else never wins there. This block works from the other end. Each condition that is off
and has a possible source is valued with a what-if: its config option on, and any stat set it
allows (conditions.STAT_SET_NEEDS) chosen. For each condition worth at least MIN_GAIN, the
cheapest untried source of each kind is tried for real (commit, resolve, score):
- persistent: a persistent skill whose description is evidence, within spare Spirit (the one whose
  what-if scores best)
- passive: the nearest main-tree node with an evidence line, its path allocated; when no points are
  left, the weakest branches make room, and leftover points regrow
- affix: a slot's rare rebuilt around an evidence affix (the slot where that affix alone scores best)
- jewel: a rare jewel built around an evidence affix, in the allocated socket where it scores best
Supports get the same treatment inside the supports block (Gems.discover). For a condition read
from PoB's numbers (enough ignite chance), only sources whose what-if passes the resolver's test
are tried. The best trial that switched the condition on is kept if the build scores higher than
before. Nothing is assumed: a trial counts only once the resolver finds the evidence in the
committed build. Passives kept this way are protected from pruning (Tree.sources).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.conditions import CONDITIONS, EVIDENCE_FIELDS, STAT_SET_NEEDS, Condition, Conditions
from builder.search.context import GEM_LEVEL, GEM_QUALITY, MAX_GEM_GROUPS
from builder.search.flasks import is_flask_slot
from builder.search.gems import PERSISTENT_LABEL, Gems
from builder.search.jewels import JEWEL_BASES, Jewels
from builder.search.objective import number
from builder.search.rares import rare_text
from builder.search.state import EPSILON, Build

MIN_GAIN = 0.01  # what-if score gain (about 1% damage) that makes a condition worth a source
CONDITIONS_PER_ROUND = 3  # the most valuable conditions looked for per round
PASSIVE_REACH = 12  # farthest main-tree node (in points) considered as a source
STAT_SCREEN = 12  # nearest candidates screened with PoB's numbers, for conditions decided by them
NEEDED_BY_STAT_SETS = {var for _, var in STAT_SET_NEEDS if var}


@dataclass
class Trial:
    kind: str
    key: object
    label: str
    apply: Callable[[float], bool]  # commit it (by the deadline); False if it couldn't be done
    keep: Callable[[], None] = lambda: None  # after it's kept


class Sources:
    def __init__(self, build: Build, conditions: Conditions, gems: Gems, tree, gear, jewels: Jewels,
                 resolve: Callable[[], bool]) -> None:
        self.build = build
        self.ctx = build.ctx
        self.conditions = conditions
        self.gems = gems
        self.tree = tree
        self.gear = gear
        self.jewels = jewels
        self.resolve = resolve
        self.tried: set[tuple[str, str, object]] = set()  # (condition, kind, source) tried since the last kick
        self.kept: list[str] = []  # what the block added, for the result
        self._possible: dict[str, bool] = {}

    def forget(self) -> None:
        """After a kick the build is different enough to try everything again."""
        self.tried.clear()

    # Candidates ---------------------------------------------------------------------------------

    def sources(self, condition: Condition, text: str) -> bool:
        return self.conditions.could_source(condition, text)

    def required(self, condition: Condition) -> Callable:
        return lambda affix: any(self.sources(condition, line) for line in affix.lines)

    def possible(self, condition: Condition) -> bool:
        """Does the game data hold any source of a kind this block tries? (Fixed for a search.)"""
        if condition.var not in self._possible:
            ctx = self.ctx
            gems = (ctx.gems[n].get("description") or "" for n in ctx.persistent_candidates)
            lines = (line for node in ctx.data.tree["nodes"] if not node.get("ascendancy")
                     for line in node.get("stats") or [])
            affixes = (line for mod in self.gear.pools.mods + self.jewels.pools.mods
                       for key, line in mod.items() if key.isdigit())
            self._possible[condition.var] = any(self.sources(condition, text) for source in (gems, lines, affixes)
                                                for text in source)
        return self._possible[condition.var]

    def enabling(self, condition: Condition, method: str, cases: list[dict], **kwargs) -> list[bool]:
        """For a condition decided by PoB's numbers: which what-ifs pass the resolver's test."""
        if condition.stat is None or not cases:
            return [True] * len(cases)
        results = self.build.call(method, cases=cases, fields=EVIDENCE_FIELDS, fullDPS=self.build.objective.full,
                                  **kwargs)["results"]
        return [bool(condition.stat(stats)) for stats in results]

    def persistent_trials(self, condition: Condition) -> list[Trial]:
        build, gems = self.build, self.gems
        groups = build.groups()
        if len([g for g in groups if not g["fromItem"]]) >= MAX_GEM_GROUPS or not build.stats.get("Spirit"):
            return []
        present = {g["name"] for group in groups for g in group["gems"]}
        left = gems.budget_left()
        names = [n for n in self.ctx.persistent_candidates
                 if n not in present and gems.affordable(n, 0.0, left)
                 and self.sources(condition, self.ctx.gems[n].get("description") or "")]
        cases = [{"newGroup": [n], "level": GEM_LEVEL, "quality": GEM_QUALITY} for n in names]
        if not cases:
            return []
        floor = max(0, self.ctx.request.floors.spare_spirit or 0)
        scored = build.gem_deltas(cases, skip_ehp=False)
        passing = self.enabling(condition, "gemDeltas", cases, skipEHP=True)
        ranked = sorted(((score, name) for (score, stats), name, ok in zip(scored, names, passing)
                         if ok and isinstance(spirit := stats.get("SpiritUnreserved"), (int, float)) and spirit >= floor),
                        key=lambda x: -x[0])

        def apply(name: str) -> bool:
            build.set_group(gems=Gems.gem_list([name]), label=PERSISTENT_LABEL)
            return True

        return [Trial("persistent", name, name, lambda _, name=name: apply(name)) for _, name in ranked]

    def passive_trials(self, condition: Condition) -> list[Trial]:
        build, tree = self.build, self.tree
        forbidden = self.ctx.forbidden_node_ids
        nodes = [n for n in build.call("frontier", maxDistance=PASSIVE_REACH)["nodes"]
                 if n["id"] not in forbidden and not forbidden.intersection(n["path"])
                 and any(self.sources(condition, line) for line in self.ctx.nodes[n["id"]].get("stats") or [])]
        nodes.sort(key=lambda n: (n["distance"], n["id"]))
        if condition.stat is not None:
            nodes = [n for n in nodes if (condition.var, "passive", n["id"]) not in self.tried][:STAT_SCREEN]
            cases = [{"add": [n["id"]], "addPaths": True, "attribute": tree.attribute} for n in nodes]
            nodes = [n for n, ok in zip(nodes, self.enabling(condition, "nodeDeltas", cases)) if ok]

        def apply(node: dict, deadline: float) -> bool:
            path = set(node["path"])
            for _, branch in tree.weakest_branches():
                if tree.points_left() >= node["distance"]:
                    break
                if not branch & path:
                    tree.remove_branch(branch)
            if tree.points_left() < node["distance"]:
                return False
            build.allocate(add=[node["id"]], attribute=tree.attribute)
            tree.round += 1
            self.resolve()
            tree.grow(deadline)
            return True

        return [Trial("passive", n["id"], f"{n['name']} (passive, {n['distance']} points)",
                      lambda deadline, n=n: apply(n, deadline), lambda n=n: tree.sources.add(n["id"]))
                for n in nodes]

    def affix_trials(self, condition: Condition) -> list[Trial]:
        build, gear = self.build, self.gear
        required = self.required(condition)
        level = gear.pools.item_level
        estimates = []
        for slot in gear.slots:
            if slot in build.locked_slots or is_flask_slot(slot):
                continue
            base, affixes = None, []
            for option in gear.options(slot):
                if not option.unique:
                    base = self.ctx.bases_by_name[option.base]
                    affixes = [a for a in gear.pools.pool(base["name"]) if required(a)]
                    if affixes:
                        break
            if not affixes:
                continue
            raws = [rare_text(slot, base, level, [a]) for a in affixes]
            passing = self.enabling(condition, "itemDeltas", [{"slot": slot, "raw": r} for r in raws])
            scores = [score for (score, _), ok in zip(gear.deltas(slot, raws), passing) if ok]
            if scores:
                estimates.append((max(scores), slot))
        estimates.sort(key=lambda x: -x[0])

        def apply(slot: str, deadline: float) -> bool:
            rare = gear.build_rare(slot, deadline, required)
            if rare is None:
                return False
            gear.put(slot, rare)
            return True

        return [Trial("affix", slot, f"a rare {slot} with an evidence affix",
                      lambda deadline, slot=slot: apply(slot, deadline)) for _, slot in estimates]

    def jewel_trials(self, condition: Condition) -> list[Trial]:
        build, jewels = self.build, self.jewels
        required = self.required(condition)
        sockets = jewels.sockets()
        if not sockets or not any(required(a) for base in JEWEL_BASES for a in jewels.pools.pool(base)):
            return []

        def apply(deadline: float) -> bool:
            slots = [Jewels.slot(s) for s in sockets]
            jewel = jewels.build_rare(slots[0], deadline, required)
            if jewel is None:
                return False
            scored = build.item_deltas([{"slot": s, "raw": jewel.raw} for s in slots])
            build.equip(max(zip((s for s, _ in scored), slots), key=lambda x: x[0])[1], jewel)
            return True

        return [Trial("jewel", "rare", "a rare jewel with an evidence affix", apply)]

    def trials(self, condition: Condition) -> list[Trial]:
        """The cheapest untried source of each kind."""
        found = []
        for kind in (self.persistent_trials, self.passive_trials, self.affix_trials, self.jewel_trials):
            found += [t for t in kind(condition) if (condition.var, t.kind, t.key) not in self.tried][:1]
        return found

    # Rounds -------------------------------------------------------------------------------------

    def worth(self) -> list[tuple[float, Condition]]:
        """Conditions that are off, by what switching them on would gain (at least MIN_GAIN)."""
        build = self.build
        base = build.score
        found = []
        for condition in CONDITIONS:
            if condition.var in self.conditions.active or not self.possible(condition):
                continue
            value: object = True
            if condition.value_field:
                value = number(build.call("stats", fields=[condition.value_field]).get(condition.value_field))
                if value <= 0:
                    continue
            build.set_config({condition.var: value})
            score = build.score
            stat_set = condition.var in NEEDED_BY_STAT_SETS
            if stat_set:
                self.gems.choose_active_skill(settle=False, assume=(condition.var,))
                score = max(score, build.score)
            build.set_config({condition.var: False})
            if stat_set:
                self.gems.choose_active_skill()
            if score - base >= MIN_GAIN:
                found.append((score - base, condition))
        return sorted(found, key=lambda x: -x[0])

    def find(self, condition: Condition, deadline: float) -> bool:
        """Try the cheapest source of each kind; keep the best one that switched the condition on
        and gained."""
        build = self.build
        before = build.score
        start = build.snapshot()
        best = None
        for trial in self.trials(condition):
            if time.perf_counter() > deadline:
                break
            self.tried.add((condition.var, trial.kind, trial.key))
            if trial.apply(deadline):
                self.resolve()
                if (condition.var in self.conditions.active and build.score > before + EPSILON
                        and (best is None or build.score > best[0])):
                    best = (build.score, build.snapshot(), trial)
            build.restore(start)
            self.tree.round += 1
        if best is None:
            return False
        build.restore(best[1])
        self.tree.round += 1
        best[2].keep()
        self.resolve()
        self.kept.append(f"{condition.label}: {best[2].label}")
        return build.score > before + EPSILON

    def round(self, deadline: float) -> bool:
        improved = False
        for _, condition in self.worth()[:CONDITIONS_PER_ROUND]:
            if time.perf_counter() > deadline:
                break
            improved = self.find(condition, deadline) or improved
        return improved
