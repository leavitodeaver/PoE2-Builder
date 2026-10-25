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
ATTRIBUTES = {"str": "Str", "dex": "Dex", "int": "Int"}


def highest_usable_level(levels: list[dict], attributes: dict[str, float], character_level: int) -> int | None:
    """The highest gem level whose character level and Str/Dex/Int requirements (bridge
    `gemRequirements`) the build meets, or None. A level-20 Elemental Conflux needs character level
    90 and 157 Intelligence; offering only level 20 made every Spirit skill a requirement deficit."""
    best = None
    for entry in levels:
        if entry["levelRequirement"] <= character_level and all(
                entry[key] <= attributes.get(key, 0.0) for key in ATTRIBUTES):
            best = entry["level"] if best is None else max(best, entry["level"])
    return best
SHORTLIST = 40
DISCOVERY_TRIALS = 3  # supports tried for real per round because they could turn a condition on


class Gems:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.ctx = build.ctx
        self.required_supports = set(self.ctx.compiled.request.require.supports)
        self.shortlist: list[str] | None = None  # best supports at the last full sweep
        self.conditions = None  # the condition resolver, when supports should be tried with it
        self.assumptions = None
        self.resolve = None  # re-resolves conditions and assumptions after a trial commit
        self.settle = None  # the same without choosing the calculated skill again (choose_active_skill)
        self._requirements: dict[str, list[dict]] = {}  # gem -> requirements per level (gemRequirements)

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
        if group is None or group["fromItem"]:
            return sorted(self.required_supports)
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
        group = next((g for g in groups if g["main"]), None)
        if group is None or group["fromItem"]:
            return False  # a skill a passive or item grants: its group isn't the player's to fill
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
        if self.resolve is not None:
            self.resolve()  # a condition the support turns on can also change the stat set chosen
        elif self.conditions is not None:
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

    @staticmethod
    def active_options(group: dict) -> list[tuple[int, int | None, int | None, str | None]]:
        """(skill, skill part, stat set, the stat set's label) for every choice in the group."""
        options = []
        for i, skill in enumerate(group["skills"], start=1):
            parts = list(range(1, len(skill["parts"]) + 1)) or [None]
            labels = skill.get("statSets") or []
            stat_sets = list(range(1, len(labels) + 1)) or [None]
            options += [(i, part, s, labels[s - 1] if s else None) for part in parts for s in stat_sets]
        return options

    def choose_active_skill(self, settle: bool = True, assume: tuple[str, ...] = ()) -> None:
        """Pick which of the main group's skills, skill parts and stat sets PoB calculates (e.g. the
        triggered skill a persistent gem grants, or Explosive Spear's explosion). Each option is
        scored with conditions and assumptions settled for it, since a stat set can be a condition's
        source (the Frenzy-Charged explosion ignites). A stat set that needs a condition
        (conditions.STAT_SET_NEEDS) is a choice only while the resolver has evidence for it, or
        while a what-if `assume`s it (settle=False leaves the config as it is)."""
        build = self.build
        group = build.main_group()
        if group is None:
            return
        options = self.active_options(group)
        if len(options) <= 1:
            return
        settle_fn = self.settle if settle else None
        best = None
        for active, part, stat_set, label in options:
            if self.conditions is not None and not self.conditions.allows(label, assume):
                continue
            build.set_group(index=group["index"], mainActiveSkill=active, skillPart=part, statSet=stat_set)
            if settle_fn is not None:
                settle_fn()
            if best is None or build.score > best[0]:
                best = (build.score, active, part, stat_set)
        if best is None:
            best = (None, *options[0][:3])
        build.set_group(index=group["index"], mainActiveSkill=best[1], skillPart=best[2], statSet=best[3])
        if settle_fn is not None:
            settle_fn()

    # Persistent skills -------------------------------------------------------------------------

    def usable_level(self, name: str) -> int | None:
        """The highest level of this gem the build's attributes and character level allow."""
        if name not in self._requirements:
            self._requirements.update(self.build.call("gemRequirements", names=[name], maxLevel=GEM_LEVEL)["gems"])
        stats = self.build.stats
        attributes = {key: float(stats.get(field) or 0.0) for key, field in ATTRIBUTES.items()}
        return highest_usable_level(self._requirements[name], attributes, self.ctx.level)

    def persistent_gem(self, name: str) -> list[dict]:
        return [{"name": name, "level": self.usable_level(name) or GEM_LEVEL, "quality": GEM_QUALITY}]

    def persistent_round(self) -> bool:
        """Add the best persistent skill that fits in spare Spirit, raise one to a higher gem level
        the attributes now allow, or drop one that doesn't pay. Each is offered at the highest gem
        level the build can use."""
        build = self.build
        groups = build.groups()
        own = [g for g in groups if g["label"] == PERSISTENT_LABEL]
        player_groups = [g for g in groups if not g["fromItem"]]
        present = {g["name"] for group in groups for g in group["gems"]}
        spare_floor = max(0, self.ctx.request.floors.spare_spirit or 0)
        left = self.budget_left()

        best = None
        # No Spirit at all (Embrace the Darkness: "You have no Spirit"): nothing new can fit
        if len(player_groups) < MAX_GEM_GROUPS and build.stats.get("Spirit"):
            names = [n for n in self.ctx.persistent_candidates if n not in present and self.affordable(n, 0.0, left)]
            missing = [n for n in names if n not in self._requirements]
            if missing:
                self._requirements.update(build.call("gemRequirements", names=missing, maxLevel=GEM_LEVEL)["gems"])
            names = [n for n in names if self.usable_level(n)]
            scored = build.gem_deltas([{"newGroup": [n], "level": self.usable_level(n), "quality": GEM_QUALITY}
                                       for n in names], skip_ehp=False)
            for (score, stats), name in zip(scored, names):
                spirit = stats.get("SpiritUnreserved")
                if not (isinstance(spirit, (int, float)) and spirit >= spare_floor):
                    continue
                if self.assumptions is not None and self.assumptions.relevant(name):
                    score = self.trial_new_group(name)  # its effect only shows once assumptions update
                if score > build.score + EPSILON and (best is None or score > best[0]):
                    best = (score, "add", name)
        if own:
            scored = build.gem_deltas([{"group": g["index"], "slot": 1, "remove": True} for g in own], skip_ehp=False)
            for (score, _), group in zip(scored, own):
                if score > build.score + EPSILON and (best is None or score > best[0]):
                    best = (score, "remove", group["index"])
        if best is None:
            return self.relevel_persistent(own)
        before = build.score
        if best[1] == "add":
            added = build.set_group(gems=self.persistent_gem(best[2]), label=PERSISTENT_LABEL)
            if self.resolve is not None:
                self.resolve()
            if build.worse(before):
                build.remove_group(added["index"])
                if self.resolve is not None:
                    self.resolve()
                return False
        else:
            removed = next(g for g in own if g["index"] == best[2])
            build.remove_group(best[2])
            if self.resolve is not None:
                self.resolve()  # its what-if can't see a condition it was the source of go
            if build.worse(before):
                build.set_group(gems=[{"name": g["name"], "level": g["level"], "quality": g["quality"]}
                                      for g in removed["gems"]], label=PERSISTENT_LABEL)
                if self.resolve is not None:
                    self.resolve()
                return False
        return build.score > before + EPSILON

    def relevel_persistent(self, own: list[dict]) -> bool:
        """Raise the persistent skills the search added to the highest gem level the attributes
        allow now (they went in at a lower one when the attributes were short)."""
        build = self.build
        improved = False
        for group in own:
            gem = group["gems"][0]
            level = self.usable_level(gem["name"])
            if not level or level <= gem["level"]:
                continue
            before = build.score
            build.set_group(index=group["index"], gems=[{"name": gem["name"], "level": level, "quality": gem["quality"]}])
            if self.resolve is not None:
                self.resolve()
            if build.worse(before):
                build.set_group(index=group["index"], gems=[{"name": gem["name"], "level": gem["level"],
                                                            "quality": gem["quality"]}])
                if self.resolve is not None:
                    self.resolve()
                continue
            improved = improved or build.score > before + EPSILON
        return improved

    def trial_new_group(self, name: str) -> float:
        """Score adding a persistent skill for real: commit, resolve, read, undo."""
        build = self.build
        added = build.set_group(gems=self.persistent_gem(name), label=PERSISTENT_LABEL)
        if self.resolve is not None:
            self.resolve()
        score = build.score
        build.remove_group(added["index"])
        if self.resolve is not None:
            self.resolve()
        return score

    def optimize_persistent(self, deadline: float, rounds: int = 6) -> bool:
        import time

        improved = False
        for _ in range(rounds):
            if time.perf_counter() > deadline or not self.persistent_round():
                break
            improved = True
        return improved
