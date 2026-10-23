"""Gems block: the main skill's supports, then persistent skills under the Spirit budget.

Supports are scored with PoB's accelerated gem path (bridge `gemDeltas`). Hard limits hold at the
source: at most SUPPORT_SLOTS supports, each support gem used once per character, no two supports
of one family in a group (PoB would let one supersede the other), at most MAX_GEM_GROUPS non-item
groups, Spirit never over-reserved, and required supports never removed. Persistent skills are
not buttons, so they never touch the button cap.
"""

from __future__ import annotations

from builder.search.context import GEM_LEVEL, GEM_QUALITY, MAX_GEM_GROUPS, SUPPORT_SLOTS
from builder.search.state import EPSILON, Build

PERSISTENT_LABEL = "auto: persistent"
SHORTLIST = 40
DISCOVERY_TRIALS = 3  # supports tried for real per round because they could turn a condition on


class Gems:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.ctx = build.ctx
        self.required_supports = set(self.ctx.compiled.request.require.supports)
        self.shortlist: list[str] | None = None  # best supports at the last full sweep
        self.conditions = None  # the condition resolver, when supports should be tried with it

    # Helpers ---------------------------------------------------------------------------------

    def families(self, name: str) -> set[str]:
        gem = self.ctx.gems.get(name) or {}
        return set(gem.get("families") or [])

    def used_supports(self, groups: list[dict], except_group: int) -> set[str]:
        return {g["name"] for group in groups if group["index"] != except_group and not group["fromItem"]
                for g in group["gems"] if g["support"]}

    def budget_left(self) -> float:
        return self.ctx.budget - self.build.spent()

    def affordable(self, name: str, freed: float, left: float) -> bool:
        priced = self.ctx.gem_price(name)
        return priced is not None and (priced[0] or 0.0) <= left + freed

    def gem_cost(self, name: str) -> float:
        priced = self.ctx.gem_price(name)
        return (priced[0] or 0.0) if priced else 0.0

    @staticmethod
    def gem_list(names: list[str]) -> list[dict]:
        main, *supports = names
        return [{"name": main, "level": GEM_LEVEL, "quality": GEM_QUALITY}] + [{"name": n} for n in supports]

    # Supports ----------------------------------------------------------------------------------

    def candidates(self, group_index: int, groups: list[dict]) -> list[str]:
        names = self.build.call("supportCandidates", group=group_index)["names"]
        used = self.used_supports(groups, group_index)
        return [n for n in names if self.ctx.allowed_gem(n) and n not in used and self.ctx.gem_price(n) is not None]

    def add_required_supports(self) -> list[str]:
        """Put required supports into the main group. Returns those that can't support it."""
        if not self.required_supports:
            return []
        group = self.build.main_group()
        names = [g["name"] for g in group["gems"]]
        compatible = set(self.candidates(group["index"], self.build.groups()))
        missing = []
        for name in sorted(self.required_supports):
            if name in names:
                continue
            if name in compatible and len(names) - 1 < SUPPORT_SLOTS:
                names.append(name)
            else:
                missing.append(name)
        if len(names) != len(group["gems"]):
            self.build.set_group(index=group["index"], gems=self.gem_list(names))
        return missing

    def supports_round(self, full_sweep: bool = False) -> bool:
        """Improve the main group's supports by one change. A full sweep tries every candidate in
        every slot; otherwise only the weakest slot (or an empty one) is retried, against the
        shortlist from the last sweep."""
        build = self.build
        groups = build.groups()
        group = next(g for g in groups if g["main"])
        index = group["index"]
        names = [g["name"] for g in group["gems"]]
        supports = names[1:]
        candidates = self.candidates(index, groups)
        if not full_sweep and self.shortlist is not None:
            candidates = [c for c in self.shortlist if c in set(candidates)]
        left = self.budget_left()

        slots: list[int] = []
        if len(supports) < SUPPORT_SLOTS:
            slots.append(len(names) + 1)  # append into an empty socket
        replaceable = [i + 2 for i, name in enumerate(supports) if name not in self.required_supports]
        if full_sweep or len(supports) >= SUPPORT_SLOTS:
            if full_sweep:
                slots += replaceable
            elif replaceable:
                slots.append(self.weakest_slot(index, replaceable))

        cases, moves = [], []
        for slot in slots:
            current = names[slot - 1] if slot <= len(names) else None
            others = [n for i, n in enumerate(supports) if i + 2 != slot]
            taken = set().union(*(self.families(n) for n in others)) if others else set()
            freed = self.gem_cost(current) if current else 0.0
            for candidate in candidates:
                if candidate in names or (self.families(candidate) & taken):
                    continue
                if not self.affordable(candidate, freed, left):
                    continue
                cases.append({"group": index, "slot": slot, "gem": candidate})
                moves.append((slot, candidate))
        if not cases:
            return False
        scored = build.gem_deltas(cases)
        if full_sweep or self.shortlist is None:
            ranked = sorted(zip(scored, moves), key=lambda x: -x[0][0])
            seen: list[str] = []
            for _, (_, name) in ranked:
                if name not in seen:
                    seen.append(name)
            self.shortlist = seen[:SHORTLIST]
        options = [(s, m) for (s, _), m in zip(scored, moves)]
        options += self.discover(index, names, options)
        best_score, best_move = max(options, key=lambda x: x[0])
        if best_score <= build.score + EPSILON:
            return False
        before = build.score
        self.commit(index, self.with_move(names, best_move))
        if build.worse(before):
            self.commit(index, names)
            self.shortlist = None
            return False
        return build.score > before + EPSILON

    @staticmethod
    def with_move(names: list[str], move: tuple[int, str]) -> list[str]:
        slot, name = move
        new = list(names)
        if slot > len(new):
            new.append(name)
        else:
            new[slot - 1] = name
        return new

    def commit(self, index: int, names: list[str]) -> None:
        self.build.set_group(index=index, gems=self.gem_list(names))
        if self.conditions is not None:
            self.conditions.resolve()

    def discover(self, index: int, names: list[str], options: list[tuple[float, tuple[int, str]]]) -> list:
        """A support that is the build's first source of a condition (exposure, onslaught, a
        charge, enough shock chance) is worth more than its what-if shows: the condition only
        turns on once the support is in. The strongest few such supports are tried for real."""
        if self.conditions is None:
            return []
        best: dict[str, tuple[float, tuple[int, str]]] = {}
        for score, move in options:
            if move[1] not in best or score > best[move[1]][0]:
                best[move[1]] = (score, move)
        sources = sorted(((s, m) for name, (s, m) in best.items()
                          if self.conditions.would_enable((self.ctx.gems.get(name) or {}).get("description") or "")),
                         key=lambda x: -x[0])
        found = []
        for _, move in sources[:DISCOVERY_TRIALS]:
            self.commit(index, self.with_move(names, move))
            found.append((self.build.score, move))
            self.commit(index, names)
        return found

    def weakest_slot(self, index: int, slots: list[int]) -> int:
        """The support slot whose gem adds least (by disabling each one)."""
        scored = self.build.gem_deltas([{"group": index, "slot": s, "remove": True} for s in slots])
        return max(zip(scored, slots), key=lambda x: x[0][0])[1]

    def optimize_supports(self, deadline: float, rounds: int = 12, allow_sweep: bool = True) -> bool:
        """Weakest-slot moves against the shortlist; once those run out (and if allowed), one
        full sweep of every candidate in every slot."""
        import time

        improved = False
        sweep = self.shortlist is None
        for _ in range(rounds):
            if time.perf_counter() > deadline:
                break
            if not self.supports_round(full_sweep=sweep):
                if sweep or not allow_sweep:
                    break
                sweep = True
                continue
            improved = True
            sweep = False
        return improved

    # Main active skill -------------------------------------------------------------------------

    def choose_active_skill(self) -> None:
        """Pick which of the main group's skills (and skill part) PoB calculates, e.g. the
        triggered skill a persistent gem grants."""
        build = self.build
        group = build.main_group()
        options = []
        for i, skill in enumerate(group["skills"], start=1):
            parts = list(range(1, len(skill["parts"]) + 1)) or [None]
            options += [(i, part) for part in parts]
        if len(options) <= 1:
            return
        best = None
        for active, part in options:
            build.set_group(index=group["index"], mainActiveSkill=active, skillPart=part)
            if best is None or build.score > best[0]:
                best = (build.score, active, part)
        build.set_group(index=group["index"], mainActiveSkill=best[1], skillPart=best[2])

    # Persistent skills -------------------------------------------------------------------------

    def persistent_round(self) -> bool:
        """Add the best persistent skill that fits in spare Spirit, or drop one that doesn't pay."""
        build = self.build
        groups = build.groups()
        own = [g for g in groups if g["label"] == PERSISTENT_LABEL]
        player_groups = [g for g in groups if not g["fromItem"]]
        present = {g["name"] for group in groups for g in group["gems"]}
        spare_floor = max(0, self.ctx.request.floors.spare_spirit or 0)
        left = self.budget_left()

        best = None
        if len(player_groups) < MAX_GEM_GROUPS:
            names = [n for n in self.ctx.persistent_candidates if n not in present and self.affordable(n, 0.0, left)]
            scored = build.gem_deltas([{"newGroup": [n], "level": GEM_LEVEL, "quality": GEM_QUALITY} for n in names],
                                      skip_ehp=False)
            for (score, stats), name in zip(scored, names):
                spirit = stats.get("SpiritUnreserved")
                if isinstance(spirit, (int, float)) and spirit >= spare_floor and score > build.score + EPSILON:
                    if best is None or score > best[0]:
                        best = (score, "add", name)
        if own:
            scored = build.gem_deltas([{"group": g["index"], "slot": 1, "remove": True} for g in own], skip_ehp=False)
            for (score, _), group in zip(scored, own):
                if score > build.score + EPSILON and (best is None or score > best[0]):
                    best = (score, "remove", group["index"])
        if best is None:
            return False
        before = build.score
        if best[1] == "add":
            added = build.set_group(gems=self.gem_list([best[2]]), label=PERSISTENT_LABEL)
            if build.worse(before):
                build.remove_group(added["index"])
                return False
        else:
            removed = next(g for g in own if g["index"] == best[2])
            build.remove_group(best[2])
            if build.worse(before):
                build.set_group(gems=self.gem_list([g["name"] for g in removed["gems"]]), label=PERSISTENT_LABEL)
                return False
        return build.score > before + EPSILON

    def optimize_persistent(self, deadline: float, rounds: int = 6) -> bool:
        import time

        improved = False
        for _ in range(rounds):
            if time.perf_counter() > deadline or not self.persistent_round():
                break
            improved = True
        return improved
