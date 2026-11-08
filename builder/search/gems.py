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
META_LABEL = "auto: meta"
META_TRIALS = 3  # spells per meta gem tried for real (trigger rates show only after a resolve)
META_GEMS_TRIED = 2  # meta gems tried, those the build feeds most Energy first
# A spell triggered less often than this can't pay through its own damage; what PoB counts of it besides (a buff it
# grants, at full) would be the only gain: such a meta group isn't kept (Profane Ritual at 0.0001 triggers/s gave +4%)
META_MIN_RATE = 1 / 30
# Spells per meta gem the search tries. A meta gem's spells and supports share its sockets (SUPPORT_SLOTS): assumed, the
# cautious reading (PoB doesn't limit it); three spells leave two sockets for supports.
META_SPELLS_MAX = 3
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
        self.meta_shortlist: list[str] | None = None  # the same for the meta group's
        self.conditions = None  # the condition resolver, when supports should be tried with it
        self.assumptions = None
        self.resolve = None  # re-resolves conditions and assumptions after a trial commit
        self.settle = None  # the same without choosing the calculated skill again (choose_active_skill)
        self._requirements: dict[str, list[dict]] = {}  # gem -> requirements per level (gemRequirements)
        self.usage = None  # usage requirements (builder/search/usage.py), set by the search
        self.left_out: dict[str, str] = {}  # meta gem -> why the search doesn't propose it (said in the result)

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

    def usable(self, name: str, stats: dict | None = None) -> bool:
        """Can the build use this gem at the rate PoB calculates (builder/search/usage.py)?"""
        return self.usage is None or self.usage.usable(name, stats)

    def usage_stats(self) -> dict | None:
        from builder.search.usage import FIELDS

        return self.build.call("stats", fields=FIELDS) if self.usage is not None else None

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

    def supports_group(self, groups: list[dict], meta: bool) -> dict | None:
        """The group a supports round fills: the main skill's, or the meta group (its supports go on the triggered spell,
        by PoB's support rules through `supportCandidates`). None for a group a passive or item grants."""
        group = next((g for g in groups if (g["label"] == META_LABEL if meta else g["main"])), None)
        return None if group is None or group["fromItem"] else group

    def supports_round(self, full_sweep: bool = False, meta: bool = False) -> bool:
        """Improve one group's supports by one change: the main skill's, or with `meta` the meta group's. A full sweep
        tries every candidate in every slot; otherwise only the weakest slot (or an empty one) is retried, against the
        shortlist from the last sweep (each group keeps its own)."""
        build = self.build
        groups = build.groups()
        group = self.supports_group(groups, meta)
        if group is None:
            return False  # none, or a skill a passive or item grants: its group isn't the player's to fill
        index = group["index"]
        names = [g["name"] for g in group["gems"]]
        first = next((i for i, g in enumerate(group["gems"]) if g["support"]), len(names))  # the actives come first
        supports = names[first:]
        required = set() if meta else self.required_supports
        shortlist = self.meta_shortlist if meta else self.shortlist
        stats = self.usage_stats()
        candidates = [c for c in self.candidates(index, groups) if self.usable(c, stats)]
        if not full_sweep and shortlist is not None:
            candidates = [c for c in shortlist if c in set(candidates)]
        left = self.budget_left()

        # (a meta gem's spells take its sockets too: assumed, the cautious reading; see META_SPELLS_MAX)
        used = len(names) - 1 if meta else len(supports)
        slots: list[int] = []
        if used < SUPPORT_SLOTS:
            slots.append(len(names) + 1)  # append into an empty socket
        replaceable = [i + first + 1 for i, name in enumerate(supports) if name not in required]
        if full_sweep or used >= SUPPORT_SLOTS:
            if full_sweep:
                slots += replaceable
            elif replaceable:
                slots.append(self.weakest_slot(index, replaceable))

        cases, moves = [], []
        for slot in slots:
            current = names[slot - 1] if slot <= len(names) else None
            others = [n for i, n in enumerate(supports) if i + first + 1 != slot]
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
        if full_sweep or shortlist is None:
            ranked = sorted(zip(scored, moves), key=lambda x: -x[0][0])
            seen: list[str] = []
            for _, (_, name) in ranked:
                if name not in seen:
                    seen.append(name)
            if meta:
                self.meta_shortlist = seen[:SHORTLIST]
            else:
                self.shortlist = seen[:SHORTLIST]
        options = [(s, m) for (s, _), m in zip(scored, moves)]
        options += self.discover(index, names, options, group)
        if meta and full_sweep:
            options += self.energy_trials(index, names, options, group)
        best_score, best_move = max(options, key=lambda x: x[0])
        if best_score <= build.score + EPSILON:
            return False
        before = build.score
        self.commit(index, self.with_move(names, best_move), group)
        if build.worse(before):
            self.commit(index, names, group)
            if meta:
                self.meta_shortlist = None
            else:
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

    def commit(self, index: int, names: list[str], group: dict | None = None) -> None:
        """Set a group's gems. The main group's skill goes in at GEM_LEVEL (gem_list); a meta group (`group` given, not
        the main one) keeps the levels its meta gem and spell were chosen at (usable_level)."""
        if group is None or group["main"]:
            gems = self.gem_list(names)
        else:
            kept = {g["name"]: {"name": g["name"], "level": g["level"], "quality": g["quality"]}
                    for g in group["gems"] if not g["support"]}
            gems = [kept.get(n) or {"name": n} for n in names]
        self.build.set_group(index=index, gems=gems)
        if self.resolve is not None:
            self.resolve()  # a condition the support turns on can also change the stat set chosen
        elif self.conditions is not None:
            self.conditions.resolve()

    def discover(self, index: int, names: list[str], options: list[tuple[float, tuple[int, str]]],
                 group: dict | None = None) -> list:
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
            self.commit(index, self.with_move(names, move), group)
            found.append((self.build.score, move))
            self.commit(index, names, group)
        return found

    def energy_trials(self, index: int, names: list[str], options: list[tuple[float, tuple[int, str]]],
                      group: dict) -> list:
        """A support that changes the meta skill's Energy (models.META_SUPPORTS: Boundless Energy, Energy Retention) adds
        nothing a what-if sees: the trigger rate is the Energy model's, which runs on a resolve. Each one's best move is
        tried for real."""
        from builder.search import models

        best: dict[str, tuple[float, tuple[int, str]]] = {}
        for score, move in options:
            if move[1] in models.META_SUPPORTS and (move[1] not in best or score > best[move[1]][0]):
                best[move[1]] = (score, move)
        found = []
        for _, move in best.values():
            self.commit(index, self.with_move(names, move), group)
            found.append((self.build.score, move))
            self.commit(index, names, group)
        return found

    def drop_unusable(self) -> bool:
        """Take out what the build can't use at the rate PoB calculates (builder/search/usage.py):
        a main-group support whose usage requirement lost its source (required supports stay), or a
        persistent skill gated the same way."""
        if self.usage is None:
            return False
        build = self.build
        changed = False
        group = build.main_group()
        if group is not None and not group["fromItem"]:
            names = [g["name"] for g in group["gems"]]
            stats = self.usage_stats()
            keep = names[:1] + [n for n in names[1:] if n in self.required_supports or self.usable(n, stats)]
            if len(keep) != len(names):
                self.commit(group["index"], keep)
                changed = True
        group = self.supports_group(build.groups(), meta=True)
        if group is not None:
            names = [g["name"] for g in group["gems"]]
            stats = self.usage_stats()
            keep = [g["name"] for g in group["gems"] if not g["support"] or self.usable(g["name"], stats)]
            if len(keep) != len(names):
                self.commit(group["index"], keep, group)
                changed = True
        while True:
            gated = next((g for g in build.groups() if g["label"] == PERSISTENT_LABEL and g["gems"]
                          and not self.usable(g["gems"][0]["name"])), None)
            if gated is None:
                break
            build.remove_group(gated["index"])
            changed = True
        if changed and self.resolve is not None:
            self.resolve()
        return changed

    def weakest_slot(self, index: int, slots: list[int]) -> int:
        """The support slot whose gem adds least (by disabling each one)."""
        scored = self.build.gem_deltas([{"group": index, "slot": s, "remove": True} for s in slots])
        return max(zip(scored, slots), key=lambda x: x[0][0])[1]

    def optimize_supports(self, deadline: float, rounds: int = 12, allow_sweep: bool = True) -> bool:
        """Weakest-slot moves against the shortlist; once those run out (and if allowed), one
        full sweep of every candidate in every slot. The main group first, then the meta group's."""
        import time

        improved = False
        for meta in (False, True):
            sweep = (self.meta_shortlist if meta else self.shortlist) is None
            for _ in range(rounds):
                if time.perf_counter() > deadline:
                    break
                if not self.supports_round(full_sweep=sweep, meta=meta):
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
            names = [n for n in self.ctx.persistent_candidates
                     if n not in present and self.affordable(n, 0.0, left) and self.usable(n)]
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

    # Meta gems the search picks by itself (PLAN.md "Meta-gem set-ups", first slice) -------------------------------

    def meta_round(self, deadline: float) -> bool:
        """Try a meta gem that gains Energy from what the build already does (crits, ailments, blocks, melee hits:
        models.META_GEMS), with one spell it triggers, and keep the best that pays. The meta gems the build feeds
        most Energy are screened first; their spells by what PoB says each adds to Full DPS in the group (a self-cast rate,
        so only a ranking); the best META_TRIALS spells of each get a real trial, since the trigger rate (models.energy_model)
        shows only after a resolve. The group's supports come after, in the supports step (optimize_supports, meta=True).
        More spells come in meta_spell_round. An Invocation is a button: offered only while the cap has room."""
        import time

        from builder.search import models

        build = self.build
        groups = build.groups()
        if any(g["label"] == META_LABEL for g in groups) or len([g for g in groups if not g["fromItem"]]) >= MAX_GEM_GROUPS:
            return False
        present = {g["name"] for group in groups for g in group["gems"]}
        skills = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        if any(s.energy for s in skills):
            return False  # the build has one already (required, granted, or from a seed)
        main_group = build.main_group()
        main = next((s for s in skills if main_group and s.group == main_group["index"] and not s.triggered), None)
        if main is None:
            return False
        stats = build.call("stats", fields=models.MODEL_FIELDS)
        left, usage = self.budget_left(), self.usage_stats()
        metas = []
        for name in self.meta_candidates():
            if name in present or not self._offered(name, left, usage):
                continue
            info = models.META_GEMS[name]
            gain = models.energy_gain(name, stats, main.name, main)[0] * (1 + info["energy"] / 100)
            if gain > 0:
                metas.append((gain, name))
        metas = [name for _, name in sorted(metas, reverse=True)[:META_GEMS_TRIED]]
        if not metas:
            return False
        spells = self.meta_spells(present, left, usage)
        before = build.score
        best: tuple[float, list[dict]] | None = None
        for meta in metas:
            if time.perf_counter() > deadline:
                break
            cases = [{"newGroup": [meta, n], "level": GEM_LEVEL, "quality": GEM_QUALITY, "fullDPS": True} for n in spells]
            if not cases:
                break
            full = []
            for start in range(0, len(cases), 50):
                full += build.call("gemDeltas", cases=cases[start:start + 50], fields=["FullDPS"], fullDPS=True,
                                   skipEHP=True)["results"]
            ranked = sorted(zip((float(r.get("FullDPS") or 0) for r in full), spells), reverse=True)
            for _, spell in ranked[:META_TRIALS]:
                if time.perf_counter() > deadline:
                    break
                levels = [self.usable_level(n) for n in (meta, spell)]
                if None in levels:
                    continue
                gems = [{"name": n, "level": level, "quality": GEM_QUALITY} for n, level in zip((meta, spell), levels)]
                score = self.trial_meta(gems)
                if score is not None and score > before + EPSILON and (best is None or score > best[0]):
                    best = (score, gems)
        if best is None:
            return False
        added = build.set_group(gems=best[1], label=META_LABEL, fullDPS=True)
        self.meta_shortlist = None  # a new group: its supports get a full sweep
        if self.resolve is not None:
            self.resolve()
        if build.worse(before):
            build.remove_group(added["index"])
            if self.resolve is not None:
                self.resolve()
            return False
        return build.score > before + EPSILON

    def meta_spell_round(self, deadline: float) -> bool:
        """One more spell in the meta group (slice 3). Every spell in the group triggers once per round and a round costs all
        their Energy, so a spell pays only if its damage per Energy beats what it takes from the others; that shows only
        after a resolve (models.energy_model), so the best META_TRIALS spells by PoB's what-if get a real trial."""
        import time

        build = self.build
        group = self.supports_group(build.groups(), meta=True)
        if group is None or len(group["gems"]) - 1 >= SUPPORT_SLOTS:
            return False
        actives = [g for g in group["gems"] if not g["support"]]
        if len(actives) - 1 >= META_SPELLS_MAX:
            return False
        present = {g["name"] for grp in build.groups() for g in grp["gems"]}
        spells = self.meta_spells(present, self.budget_left(), self.usage_stats())
        if not spells:
            return False
        index, names = group["index"], [g["name"] for g in group["gems"]]
        at = len(actives) + 1  # after the group's spells, before its supports
        scored = build.gem_deltas([{"group": index, "slot": len(names) + 1, "gem": n} for n in spells])
        ranked = sorted(zip((s for s, _ in scored), spells), reverse=True)
        before = build.score
        best: tuple[float, dict] | None = None
        for _, spell in ranked[:META_TRIALS]:
            if time.perf_counter() > deadline:
                break
            level = self.usable_level(spell)
            if level is None:
                continue
            gem = {"name": spell, "level": level, "quality": GEM_QUALITY}
            score = self.trial_meta_spell(group, names, at, gem)
            if score is not None and score > before + EPSILON and (best is None or score > best[0]):
                best = (score, gem)
        if best is None:
            return False
        self.set_meta_gems(group, names, at, best[1])
        if build.worse(before):
            self.set_meta_gems(group, names)
            return False
        return build.score > before + EPSILON

    def meta_spells(self, present: set[str], left: float, usage: dict | None) -> list[str]:
        """Spells a meta skill can trigger that the build doesn't use yet."""
        return [n for n, gem in self.ctx.gems.items() if not gem["support"] and n not in present
                and {"Spell", "Triggerable"} <= set(gem["skillTypes"]) and not {"Meta", "Persistent"} & set(gem["skillTypes"])
                and self._offered(n, left, usage)]

    def set_meta_gems(self, group: dict, names: list[str], at: int | None = None, spell: dict | None = None) -> None:
        """The meta group's gems as `names` (their levels kept), with `spell` inserted at position `at` (1-based)."""
        kept = {g["name"]: {"name": g["name"], "level": g["level"], "quality": g["quality"]}
                for g in group["gems"] if not g["support"]}
        gems = [kept.get(n) or {"name": n} for n in names]
        if spell is not None:
            gems.insert(at - 1, spell)
        self.build.set_group(index=group["index"], gems=gems)
        if self.resolve is not None:
            self.resolve()

    def trial_meta_spell(self, group: dict, names: list[str], at: int, spell: dict) -> float | None:
        """Score the meta group with one more spell for real, then put it back. None when the new spell isn't triggered at
        META_MIN_RATE or more (see trial_meta)."""
        build = self.build
        self.set_meta_gems(group, names, at, spell)
        rates = [m["value"] for m in (self.assumptions.mods if self.assumptions is not None else [])
                 if m.get("kind") == "SkillData" and m.get("skill") == spell["name"] and m.get("key") == "triggerRate"]
        triggered = any(s["name"] == spell["name"] and s.get("group") == group["index"] and s.get("triggered")
                        for s in build.call("activeSkills")["skills"])
        score = build.score if triggered and rates and max(rates) >= META_MIN_RATE else None
        self.set_meta_gems(group, names)
        return score

    def meta_candidates(self) -> list[str]:
        """Every Meta gem in the gem data, not a hand-kept list: those the meta round can propose (an Energy model; an
        Invocation only while the button cap has room, since you press it), while the others go into left_out with the
        reason, said in the result."""
        from builder.search import models

        cap = self.ctx.request.max_buttons
        room = cap is None or len(self.buttons()) < cap
        names = []
        for name, gem in self.ctx.gems.items():
            if "Meta" not in gem["skillTypes"] or not self.ctx.allowed_gem(name):
                continue
            modeled, why = models.meta_handling(name)
            if not modeled:
                self.left_out[name] = why
            elif models.META_GEMS[name].get("invocation") and not room:
                self.left_out[name] = f"an Invocation is a button you press, and the build's {cap} buttons are all taken"
            else:
                self.left_out.pop(name, None)
                names.append(name)
        return names

    def buttons(self) -> list[str]:
        """The skills the build makes the player press (constraints.player_buttons' rule on the current groups): not
        persistent, meta-triggered, triggered or item-granted; an Invocation is one."""
        from builder.request.constraints import presses

        names: list[str] = []
        for group in self.build.groups():
            if not group["enabled"] or group["fromItem"]:
                continue
            gems = [self.ctx.gems[g["name"]] for g in group["gems"] if g.get("enabled", True) and g["name"] in self.ctx.gems]
            by_meta = any("Meta" in g["skillTypes"] for g in gems if not g["support"])
            for gem in gems:
                if presses(gem) and not (by_meta and "Meta" not in gem["skillTypes"]) and gem["name"] not in names:
                    names.append(gem["name"])
        return names

    def trial_meta(self, gems: list[dict]) -> float | None:
        """Score a meta group for real: commit, resolve, read, undo. None when PoB doesn't let the meta trigger the spell
        (it would count the spell as cast by hand) or the Energy model gives it no triggers."""
        build = self.build
        added = build.set_group(gems=gems, label=META_LABEL, fullDPS=True)
        if self.resolve is not None:
            self.resolve()
        triggered = any(s["name"] == gems[1]["name"] and s.get("group") == added["index"] and s.get("triggered")
                        for s in build.call("activeSkills")["skills"])
        # (and the Energy model gives it a rate: a spell that never fires must not pay through anything PoB counts of it)
        rates = [m["value"] for m in (self.assumptions.mods if self.assumptions is not None else [])
                 if m.get("kind") == "SkillData" and m.get("skill") == gems[1]["name"] and m.get("key") == "triggerRate"]
        triggered = triggered and bool(rates) and max(rates) >= META_MIN_RATE
        score = build.score if triggered else None
        build.remove_group(added["index"])
        if self.resolve is not None:
            self.resolve()
        return score

    def _offered(self, name: str, left: float, usage: dict | None) -> bool:
        return name in self.ctx.gems and self.ctx.allowed_gem(name) and self.ctx.gem_price(name) is not None             and self.affordable(name, 0.0, left) and self.usable(name, usage)
