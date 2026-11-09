"""Gems block: the main skill's supports, then persistent skills under the Spirit budget.

Supports are scored with PoB's accelerated gem path (bridge `gemDeltas`). Hard limits hold at the
source: at most SUPPORT_SLOTS supports, each support gem used once per character, no two supports
of one family in a group (PoB would let one supersede the other), at most MAX_GEM_GROUPS non-item
groups, Spirit never over-reserved, and required supports never removed. Persistent skills are
not buttons, so they never touch the button cap.
"""

from __future__ import annotations

from builder import clock
from builder.search.context import GEM_LEVEL, GEM_QUALITY, MAX_GEM_GROUPS, SUPPORT_SLOTS
from builder.search.state import EPSILON, Build
from builder.search.tree import GRANTED_METAS

PERSISTENT_LABEL = "auto: persistent"
BLASPHEMY_LABEL = "auto: blasphemy"
BLASPHEMY = "Blasphemy"
META_LABEL = "auto: meta"
MARK_LABEL = "auto: mark"
MIRAGE_LABEL = "auto: mirage"
ROAR_LABEL = "auto: roar"
# Groups the search adds whole: their skill and meta gems are raised as the attributes grow (relevel_added)
ADDED_LABELS = (PERSISTENT_LABEL, BLASPHEMY_LABEL, META_LABEL, MARK_LABEL, MIRAGE_LABEL, ROAR_LABEL)
GRANTED_SUPPORT_ROUNDS = 6  # support changes a granted meta group gets right after its spell (fill_granted_supports)
GRANTED_FILL_SECONDS = 60.0  # time a granted meta's fill (fill_granted) may take within one ascendancy trial
MIRAGE_TRIALS = 3  # ranged Attacks per Mirage tried for real (ranked by what PoB says each adds to Full DPS)
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
        self.census: dict = {}  # the main group's last supports round (supports_round)
        self.meta_shortlist: list[str] | None = None  # the same for the meta group's
        self.granted_shortlists: dict[int, list[str]] = {}  # and for each item- or notable-granted meta group's
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
        # (an item's or notable's group counts too: the supports in it are the player's gems, socketed with the granted
        # skill; leaving them out let gate 4's 2b75b hold Rakiata's Flow in two groups, which PoB flags)
        return {g["name"] for group in groups if group["index"] != except_group
                for g in group["gems"] if g["support"]}

    def budget_left(self) -> float:
        return self.ctx.budget - self.build.spent()

    def affordable(self, name: str, freed: float, left: float) -> bool:
        priced = self.ctx.gem_price(name)
        return priced is not None and (priced[0] or 0.0) <= left + freed

    def blockers(self, name: str, stats: dict | None) -> list[str]:
        return [] if self.usage is None else [str(b) for b in self.usage.blockers(name, stats)]

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

    def supports_group(self, groups: list[dict], meta: bool, index: int | None = None) -> dict | None:
        """The group a supports round fills: the main skill's, or the meta group (its supports go on the triggered spell,
        by PoB's support rules through `supportCandidates`), or with `index` that granted meta group. None for any other
        group a passive or item grants."""
        if index is not None:
            return next((g for g in groups if g["index"] == index), None)
        group = next((g for g in groups if (g["label"] == META_LABEL if meta else g["main"])), None)
        return None if group is None or group["fromItem"] else group

    def _shortlist(self, meta: bool, index: int | None) -> list[str] | None:
        if index is not None:
            return self.granted_shortlists.get(index)
        return self.meta_shortlist if meta else self.shortlist

    def _set_shortlist(self, meta: bool, index: int | None, value: list[str] | None) -> None:
        if index is not None:
            if value is None:
                self.granted_shortlists.pop(index, None)
            else:
                self.granted_shortlists[index] = value
        elif meta:
            self.meta_shortlist = value
        else:
            self.shortlist = value

    def supports_round(self, full_sweep: bool = False, meta: bool = False, granted: int | None = None) -> bool:
        """Improve one group's supports by one change: the main skill's, with `meta` the meta group's, or with `granted`
        the meta group of that index an item or notable grants (the socketed spell's supports, as in the player's: the
        reference 2b75d has Boundless Energy II and three others in Mjolner's Thundergod's Wrath). A full sweep tries
        every candidate in every slot; otherwise only the weakest slot (or an empty one) is retried, against the
        shortlist from the last sweep (each group keeps its own)."""
        build = self.build
        groups = build.groups()
        meta = meta or granted is not None
        group = self.supports_group(groups, meta, granted)
        if group is None:
            return False  # none, or a skill a passive or item grants: its group isn't the player's to fill
        index = group["index"]
        names = [g["name"] for g in group["gems"]]
        first = next((i for i, g in enumerate(group["gems"]) if g["support"]), len(names))  # the actives come first
        supports = names[first:]
        required = set() if meta else self.required_supports
        shortlist = self._shortlist(meta, granted)
        stats = self.usage_stats()
        offered = self.candidates(index, groups)
        candidates = [c for c in offered if self.usable(c, stats)]
        if not full_sweep and shortlist is not None:
            candidates = [c for c in shortlist if c in set(candidates)]
        left = self.budget_left()

        # (a meta gem's spells take its sockets too: assumed, the cautious reading; see META_SPELLS_MAX. So does the main
        # spell in a totem meta)
        from builder.search.models import TOTEM_METAS

        totem = any(n in TOTEM_METAS for n in names)
        used = len(names) - 1 if meta or totem else len(supports)
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
        if not meta:
            # The main group's last round, for reading a run (the "task" event's `supports`): what each filter left and
            # the best what-if against the score now. Gate 4's 2b75b lines sat at 15,004 with no support (POE2T206)
            blocked = [c for c in offered if c not in set(candidates)]
            self.census = {"offered": len(offered), "usable": len(candidates), "cases": len(cases),
                           "slots": len(slots), "full": bool(full_sweep or shortlist is None),
                           "blocked": blocked[:6], "blockers": sorted({b for c in blocked[:20] for b in self.blockers(c, stats)})}
        if not cases:
            return False
        scored = build.gem_deltas(cases)
        if not meta:
            top = max(range(len(scored)), key=lambda i: scored[i][0])
            self.census.update(best=moves[top][1], best_gain=round(scored[top][0] - build.score, 4))
        if full_sweep or shortlist is None:
            ranked = sorted(zip(scored, moves), key=lambda x: -x[0][0])
            seen: list[str] = []
            for _, (_, name) in ranked:
                if name not in seen:
                    seen.append(name)
            # A sweep where no support changes the score ranks nothing: on gate 4's 2b75b the first sweep of a line ran
            # before the ascendancy step took Into the Breach (that build's Remnant source; assumptions.REMNANT_SOURCES lists them all), every support
            # scored exactly 0, and the shortlist kept was the alphabet's first names, so the line stayed at 15K with no
            # support until the next sweep (POE2T206, 216). Then no shortlist is kept, and the next round sweeps again
            spread = ranked[0][0][0] - ranked[-1][0][0] if ranked else 0.0
            self._set_shortlist(meta, granted, seen[:SHORTLIST] if spread > EPSILON else None)
        options = [(s, m) for (s, _), m in zip(scored, moves)]
        options += self.discover(index, names, options, group)
        if meta and full_sweep:
            options += self.energy_trials(index, names, options, group)
        # The what-if can't see a meta group's trigger rate (models.energy_model works it out after a resolve), so there
        # the next best moves get a real trial too when the first turns out worse
        before = build.score
        ranked = sorted(options, key=lambda x: -x[0])
        tries = [m for i, (s, m) in enumerate(ranked) if s > before + EPSILON and m not in (x for _, x in ranked[:i])]
        snap = None
        for move in tries[:META_TRIALS if meta else 1]:
            snap = snap or build.snapshot()
            self.commit(index, self.with_move(names, move), group)
            if not build.worse(before):
                return build.score > before + EPSILON
            self.take_back(index, names, group, before, snap)
        if tries:
            self._set_shortlist(meta, granted, None)
        return False

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

    def take_back(self, index: int, names: list[str], group: dict | None, before: float, snap: dict) -> None:
        """Undo a trial move. Setting the old gems again can leave the build below where it was (the main gem goes back
        in at GEM_LEVEL, conditions re-resolve with the trial's sources gone): then the snapshot from before the trial
        goes back, and the build's reverts count it under "support take-back"."""
        self.commit(index, names, group)
        if self.build.score < before - EPSILON:
            self.build.reverts["support take-back"] = self.build.reverts.get("support take-back", 0) + 1
            self.build.restore(snap)

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
        before, snap = self.build.score, None
        for _, move in sources[:DISCOVERY_TRIALS]:
            snap = snap or self.build.snapshot()
            self.commit(index, self.with_move(names, move), group)
            found.append((self.build.score, move))
            self.take_back(index, names, group, before, snap)
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
        found, before, snap = [], self.build.score, None
        for _, move in best.values():
            snap = snap or self.build.snapshot()
            self.commit(index, self.with_move(names, move), group)
            found.append((self.build.score, move))
            self.take_back(index, names, group, before, snap)
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
        full sweep of every candidate in every slot. The main group first, then the meta group's, then each granted
        meta group holding a spell."""

        improved = False
        granted = [g["index"] for g in self.filled_granted_groups()]
        for meta, index in [(False, None), (True, None), *((True, i) for i in granted)]:
            sweep = self._shortlist(meta, index) is None
            for _ in range(rounds):
                if clock.now() > deadline:
                    break
                if not self.supports_round(full_sweep=sweep, meta=meta, granted=index):
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
            curse = self.blasphemy_option(present, left, spare_floor)
            if curse is not None and curse[0] > build.score + EPSILON and (best is None or curse[0] > best[0]):
                best = (curse[0], "curse", curse[1])
        cursed = [g for g in groups if g["label"] == BLASPHEMY_LABEL]
        if own or cursed:
            # (a Blasphemy group goes as a whole: its Curse alone would be a button; disabling the Curse frees its Spirit)
            scored = build.gem_deltas([{"group": g["index"], "slot": 1, "remove": True} for g in own]
                                      + [{"group": g["index"], "slot": 2, "remove": True} for g in cursed], skip_ehp=False)
            for (score, _), group in zip(scored, own + cursed):
                if score > build.score + EPSILON and (best is None or score > best[0]):
                    best = (score, "remove", group["index"])
        if best is None:
            return self.relevel_added()
        before = build.score
        if best[1] in ("add", "curse"):
            gems, label = ((self.persistent_gem(best[2]), PERSISTENT_LABEL) if best[1] == "add"
                           else (self.blasphemy_gems(best[2]), BLASPHEMY_LABEL))
            added = build.set_group(gems=gems, label=label)
            if self.resolve is not None:
                self.resolve()
            if build.worse(before):
                build.remove_group(added["index"])
                if self.resolve is not None:
                    self.resolve()
                return False
        else:
            removed = next(g for g in own + cursed if g["index"] == best[2])
            build.remove_group(best[2])
            if self.resolve is not None:
                self.resolve()  # its what-if can't see a condition it was the source of go
            if build.worse(before):
                build.set_group(gems=[{"name": g["name"], "level": g["level"], "quality": g["quality"]}
                                      for g in removed["gems"]], label=removed["label"])
                if self.resolve is not None:
                    self.resolve()
                return False
        return build.score > before + EPSILON

    def curse_candidates(self, present: set[str], left: float) -> list[str]:
        """Curses (AppliesCurse spells) Blasphemy can turn into auras."""
        return [n for n, gem in self.ctx.gems.items() if not gem["support"] and "AppliesCurse" in gem["skillTypes"]
                and "Meta" not in gem["skillTypes"] and n not in present and self.ctx.allowed_gem(n)
                and self.ctx.gem_price(n) is not None and self.affordable(n, 0.0, left) and self.usable(n)
                and self.usable_level(n)]

    def blasphemy_gems(self, curse: str) -> list[dict]:
        return [{"name": BLASPHEMY, "level": self.usable_level(BLASPHEMY) or GEM_LEVEL, "quality": GEM_QUALITY},
                {"name": curse, "level": self.usable_level(curse) or GEM_LEVEL, "quality": GEM_QUALITY}]

    def blasphemy_option(self, present: set[str], left: float, spare_floor: float) -> tuple[float, str] | None:
        """Slice 7: the best Curse in Blasphemy, which PoB prices (60 Spirit per socketed Curse, the support's less Curse
        effect: -50% at gem level 1 to -41% at 20, as the game files): an aura, so no button and always on the boss.
        One Curse: an enemy carries one unless the build raises the limit. (score, curse) or None."""
        if BLASPHEMY in present or not self._offered(BLASPHEMY, left, None) or not self.usable_level(BLASPHEMY):
            return None
        curses = self.curse_candidates(present, left)
        if not curses:
            return None
        level = min(self.usable_level(BLASPHEMY), *(self.usable_level(n) for n in curses))
        scored = self.build.gem_deltas([{"newGroup": [BLASPHEMY, n], "level": level, "quality": GEM_QUALITY}
                                        for n in curses], skip_ehp=False)
        best = None
        for (score, stats), name in zip(scored, curses):
            spirit = stats.get("SpiritUnreserved")
            if isinstance(spirit, (int, float)) and spirit >= spare_floor and (best is None or score > best[0]):
                best = (score, name)
        if best is not None and self.assumptions is not None and self.assumptions.relevant(best[1]):
            added = self.build.set_group(gems=self.blasphemy_gems(best[1]), label=BLASPHEMY_LABEL)
            if self.resolve is not None:
                self.resolve()
            best = (self.build.score, best[1])
            self.build.remove_group(added["index"])
            if self.resolve is not None:
                self.resolve()
        return best

    def relevel_added(self) -> bool:
        """Raise every skill and meta gem the search added (persistent skills, Blasphemy and its Curse, meta gems and their
        spells, Marks, Mirages, the roar, a totem meta in the main group) to the highest gem level the attributes and
        character level allow now: each went in at the level usable then, and a Ferocious Roar added at level 5 on a bare
        build was too weak to break the boss's Armour. Supports keep theirs. One group at a time, kept if the build
        scores no worse and the spare Spirit floor still holds."""
        from builder.search import models

        build = self.build
        spare_floor = max(0, self.ctx.request.floors.spare_spirit or 0)
        improved = False
        for group in build.groups():
            if group["fromItem"]:
                if not (group["gems"] and (group["gems"][0]["name"] in GRANTED_METAS
                                           or group["gems"][0]["name"] in models.META_GEMS)):
                    continue
                raise_ = [i for i, g in enumerate(group["gems"]) if i and not g["support"]]  # (not the granted gem)
            elif group["label"] in ADDED_LABELS:
                raise_ = [i for i, g in enumerate(group["gems"]) if not g["support"]]
            elif group["main"]:
                raise_ = [i for i, g in enumerate(group["gems"]) if g["name"] in models.TOTEM_METAS]
            else:
                continue
            old = [{"name": g["name"], "level": g["level"], "quality": g["quality"], "enabled": g["enabled"]}
                   for g in group["gems"]]
            new = [dict(g) for g in old]
            for i in raise_:
                level = self.usable_level(new[i]["name"]) if new[i]["name"] in self.ctx.gems else None
                if level and level > new[i]["level"]:
                    new[i]["level"] = level
            if new == old:
                continue
            before, spirit_before = build.score, build.stats.get("SpiritUnreserved")
            build.set_group(index=group["index"], gems=new)
            if self.resolve is not None:
                self.resolve()
            spirit = build.stats.get("SpiritUnreserved")
            short = (isinstance(spirit, (int, float)) and isinstance(spirit_before, (int, float))
                     and spirit < spare_floor and spirit < spirit_before)
            if build.worse(before) or short:
                build.set_group(index=group["index"], gems=old)
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

        improved = False
        for _ in range(rounds):
            if clock.now() > deadline or not self.persistent_round():
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

        from builder.search import models

        build = self.build
        groups = build.groups()
        if any(g["label"] == META_LABEL for g in groups) or len([g for g in groups if not g["fromItem"]]) >= MAX_GEM_GROUPS:
            return False
        present = {g["name"] for group in groups for g in group["gems"]}
        skills = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        granted = {g["index"] for g in groups if g["fromItem"]}
        if any(s.energy and s.group not in granted for s in skills):
            return False  # the build has one already (required, or from a seed); an item's or notable's doesn't count
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
            gain = models.energy_gain(name, stats, main.name, main)[0] * (1 + models.gem_energy(name, None) / 100)
            if gain > 0:
                metas.append((gain, name))
        metas = [name for _, name in sorted(metas, reverse=True)[:META_GEMS_TRIED]]
        if not metas:
            return False
        spells = self.meta_spells(present, left, usage)
        before = build.score
        best: tuple[float, list[dict]] | None = None
        for meta in metas:
            if clock.now() > deadline:
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
                if clock.now() > deadline:
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

    def granted_meta_groups(self) -> list[dict]:
        """Groups an item grants holding only a meta skill with an Energy model (Mjolner's Thundergod's Wrath, Svalinn's
        Cast on Block, a Dueling Wand's Spellslinger), which trigger nothing until a spell is socketed in them, or
        Forgotten Warden's Spirit Vessel, whose companion does nothing until a shapeshift skill is."""
        from builder.search import models

        return [g for g in self.build.groups() if g["fromItem"] and g["enabled"] and g["gems"]
                and g["gems"][0]["name"] in models.GRANTED_FILLED and not any(not x["support"] for x in g["gems"][1:])]

    def filled_granted_groups(self) -> list[dict]:
        """Groups an item or notable grants whose Energy meta holds a spell: their supports are the player's to choose."""
        from builder.search import models

        return [g for g in self.build.groups() if g["fromItem"] and g["enabled"] and g["gems"]
                and g["gems"][0]["name"] in models.GRANTED_FILLED and any(not x["support"] for x in g["gems"][1:])]

    def refill_main_supports(self, deadline: float, rounds: int = GRANTED_SUPPORT_ROUNDS) -> bool:
        """A sweep of the main group's supports, then weakest-slot rounds, so an item taken off for the meta it granted
        (gear.granted_meta_trials' reverse trial) is judged with the main group refilled: 2b75b's main group was left 2-3
        supports while a rare Absent Amulet's Cast on Dodge held 4 (MECH15)."""
        improved, sweep = False, True
        for _ in range(rounds):
            if clock.now() > deadline or not self.supports_round(full_sweep=sweep):
                break
            improved, sweep = True, False
        return improved

    def fill_granted_supports(self, index: int, deadline: float, rounds: int = GRANTED_SUPPORT_ROUNDS) -> bool:
        """A sweep of supports for the granted group `index`, then weakest-slot rounds, so a unique tried for its
        granted meta (gear.granted_meta_trials) is judged with its spell supported."""
        self.granted_shortlists.pop(index, None)
        improved, sweep = False, True
        for _ in range(rounds):
            if clock.now() > deadline or not self.supports_round(full_sweep=sweep, granted=index):
                break
            improved, sweep = True, False
        return improved

    def granted_meta_round(self, deadline: float) -> bool:
        """Slice 8: a spell in each empty meta group an item grants, the best of the META_TRIALS spells PoB's what-if
        ranks first, kept if the build scores better (the trigger rate, models.energy_model, shows only after a resolve).
        The group counts in Full DPS while it holds a spell. Spirit Vessel's group gets its shapeshift skills instead
        (spirit_vessel_round)."""
        from builder.search import models

        build = self.build
        improved = False
        for group in self.granted_meta_groups():
            if clock.now() > deadline:
                break
            if group["gems"][0]["name"] == models.SPIRIT_VESSEL:
                improved = self.spirit_vessel_round(group, deadline) or improved
                continue
            spells = self.meta_spells({g["name"] for grp in build.groups() for g in grp["gems"]}, self.budget_left(),
                                      self.usage_stats())
            if not spells:
                break
            index, names = group["index"], [g["name"] for g in group["gems"]]
            before = build.score
            build.set_group(index=index, fullDPS=True)  # (first: PoB's what-if counts a spell in it only then)
            full = build.call("gemDeltas", cases=[{"group": index, "slot": len(names) + 1, "gem": n} for n in spells],
                              fields=["FullDPS"], fullDPS=True, skipEHP=True)["results"]
            ranked = sorted(zip((float(r.get("FullDPS") or 0) for r in full), spells), reverse=True)
            best: tuple[float, dict] | None = None
            for _, spell in ranked[:META_TRIALS]:
                if clock.now() > deadline:
                    break
                level = self.usable_level(spell)
                if level is None:
                    continue
                gem = {"name": spell, "level": level, "quality": GEM_QUALITY}
                score = self.trial_meta_spell(group, names, 2, gem)
                if score is not None and score > before + EPSILON and (best is None or score > best[0]):
                    best = (score, gem)
            if best is None:
                build.set_group(index=index, fullDPS=group["fullDPS"])
                continue
            self.set_meta_gems(group, names, 2, best[1])
            if build.worse(before):
                self.set_meta_gems(group, names)
                build.set_group(index=index, fullDPS=group["fullDPS"])
                continue
            self.fill_granted_supports(index, deadline)
            improved = improved or build.score > before + EPSILON
        return improved

    def spirit_vessel_round(self, group: dict, deadline: float) -> bool:
        """Spirit Vessel (Forgotten Warden): its companion uses copies of the Bear, Werewolf and Wyvern skills socketed
        with it, 20% more damage per different one (engine/bridge.lua installSpiritVessel), so they go in one at a time,
        the best of the META_TRIALS PoB's what-if ranks first, while the build scores better; then its supports. The
        socketed skills aren't yours to use (nor buttons: buttons()). The group counts in Full DPS while it holds one."""
        from builder.search import models

        build = self.build
        index, start, full = group["index"], build.score, group["fullDPS"]
        if models.SPIRIT_VESSEL in self.ctx.compiled.filters.forbidden_gems:
            return False
        build.set_group(index=index, fullDPS=True)  # (first: PoB's what-if counts the companion only then)
        added = False
        while clock.now() < deadline:
            group = next(g for g in build.groups() if g["index"] == index)
            gems = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in group["gems"]]
            at = next((i for i, g in enumerate(group["gems"]) if g["support"]), len(gems)) + 1  # after the skills
            present = {g["name"] for grp in build.groups() for g in grp["gems"]}
            left, usage = self.budget_left(), self.usage_stats()
            skills = [n for n, gem in self.ctx.gems.items() if not gem["support"] and n not in present
                      and models.SPIRIT_VESSEL_TYPES & set(gem["skillTypes"]) and "Meta" not in gem["skillTypes"]
                      and self._offered(n, left, usage)]
            if len(gems) - 1 >= SUPPORT_SLOTS or not skills:
                break
            before = build.score
            scored = build.gem_deltas([{"group": index, "slot": at, "gem": n} for n in skills])
            best: tuple[float, list[dict]] | None = None
            for _, name in sorted(zip((s for s, _ in scored), skills), reverse=True)[:META_TRIALS]:
                level = self.usable_level(name)
                if level is None or clock.now() > deadline:
                    continue
                trial = gems[:at - 1] + [{"name": name, "level": level, "quality": GEM_QUALITY}] + gems[at - 1:]
                self._set_vessel(index, trial)
                if build.score > before + EPSILON and (best is None or build.score > best[0]):
                    best = (build.score, trial)
                self._set_vessel(index, gems)
            if best is None:
                break
            self._set_vessel(index, best[1])
            if build.worse(before):
                self._set_vessel(index, gems)
                break
            added = True
        if not added:
            build.set_group(index=index, fullDPS=full)
            return False
        self.fill_granted_supports(index, deadline)
        return build.score > start + EPSILON

    def _set_vessel(self, index: int, gems: list[dict]) -> None:
        self.build.set_group(index=index, gems=gems)
        if self.resolve is not None:
            self.resolve()

    def meta_spell_round(self, deadline: float) -> bool:
        """One more spell in the meta group (slice 3). Every spell in the group triggers once per round and a round costs all
        their Energy, so a spell pays only if its damage per Energy beats what it takes from the others; that shows only
        after a resolve (models.energy_model), so the best META_TRIALS spells by PoB's what-if get a real trial."""

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
            if clock.now() > deadline:
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
        """Spells a meta skill can trigger that the build doesn't use yet (one that needs corpses only with a corpse
        supply: models.corpse_model)."""
        from builder.search import models

        return [n for n, gem in self.ctx.gems.items() if not gem["support"] and n not in present
                and {"Spell", "Triggerable"} <= set(gem["skillTypes"]) and not {"Meta", "Persistent"} & set(gem["skillTypes"])
                and not (models.CORPSE_TYPES & set(gem["skillTypes"]) and not models.corpse_supply(present))
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
            if "Meta" not in gem["skillTypes"] or not self.ctx.allowed_gem(name) or name in {*models.TOTEM_METAS, *models.MIRAGE_METAS}:
                continue  # (a totem meta is the totem round's, a Mirage the mirage round's)
            modeled, why = models.meta_handling(name)
            if not modeled:
                self.left_out[name] = why
            elif models.META_GEMS[name].get("invocation") and not room:
                self.left_out[name] = f"an Invocation is a button you press, and the build's {cap} buttons are all taken"
            else:
                self.left_out.pop(name, None)
                names.append(name)
        return names

    def totem_round(self, deadline: float) -> bool:
        """Socket the main spell in a totem meta (models.TOTEM_METAS: Spell Totem) and keep it if the build scores better,
        with models.totem_model pricing how many totems its charges keep up. You press the totem instead of the spell, so
        the buttons don't change. The totem's sockets hold the spell and its supports (assumed, as for every meta gem): the
        weakest support goes when all are taken."""

        from builder.search import models

        build = self.build
        group = build.main_group()
        if group is None or group["fromItem"] or clock.now() > deadline:
            return False
        names = [g["name"] for g in group["gems"]]
        if any(n in models.TOTEM_METAS for n in names):
            return False
        active = [g["name"] for g in group["gems"] if not g["support"]]
        if len(active) != 1 or active[0] not in self.ctx.gems:
            return False
        types = set(self.ctx.gems[active[0]]["skillTypes"])
        left, usage = self.budget_left(), self.usage_stats()
        before = build.score
        old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in group["gems"]]
        improved = False
        for totem in models.TOTEM_METAS:
            if totem not in self.ctx.gems or not self.ctx.allowed_gem(totem):
                continue
            # (Spell Totem's support part in the game data: a Totemable Spell, not a Cooldown, Triggered, Persistent, Meta or
            # conditional skill)
            if not {"Spell", "Totemable"} <= types or types & {"Cooldown", "Triggered", "Persistent", "Meta", "HasUsageCondition"}:
                self.left_out[totem] = f"{active[0]} isn't a Spell a totem can use (Totemable, no Cooldown)"
                continue
            level = self.usable_level(totem)
            if level is None or not self._offered(totem, left, usage):
                self.left_out[totem] = ("not usable: it needs the build to make Power or Endurance Charges (3 per totem), "
                                        "or it isn't affordable or its attributes aren't met")
                continue
            gems = [dict(g) for g in old]
            supports = [i for i, g in enumerate(group["gems"]) if g["support"] and g["name"] not in self.required_supports]
            if len(old) - 1 >= SUPPORT_SLOTS and supports:
                weakest = self.weakest_slot(group["index"], [i + 1 for i in supports])
                del gems[weakest - 1]
            build.set_group(index=group["index"], gems=[{"name": totem, "level": level, "quality": GEM_QUALITY}, *gems])
            if self.resolve is not None:
                self.resolve()
            if build.score > before + EPSILON:
                self.left_out.pop(totem, None)
                self.shortlist = None  # (the spell is the totem's now: its supports get a full sweep)
                improved = True
                break
            self.left_out[totem] = f"tried with {active[0]}: the build scored no better with it"
            build.set_group(index=group["index"], gems=old)
            if self.resolve is not None:
                self.resolve()
        return improved

    def mark_round(self, deadline: float) -> bool:
        """Add a Mark (slice 6) and keep it if the build scores better, models.mark_model pricing what consumption on a boss
        leaves of it: cast by you (a button), socketed in Pounce or Hand of Chayula (the meta is the button), or in a Called
        Shots group the build has (automatic; the Deadeye notable is tried by the ascendancy round, tree.granted_trial). Only Marks the
        model gives something on this build: Sniper's with crits, Voltaic / Freezing when the main skill can Electrocute /
        Freeze; Bloodhound's adds nothing PoB counts. Any button must fit the cap."""
        from builder.search import models

        build = self.build
        if clock.now() > deadline or self.build.main_group() is None:
            return False
        groups = [g for g in build.groups() if g["enabled"]]
        if any(g["name"] in models.MARKS for grp in groups for g in grp["gems"]):
            return False
        player_groups = [g for g in build.groups() if not g["fromItem"]]
        stats = build.call("stats", fields=["CritChance", "FreezeBuildupAvg", "ElectrocuteBuildupAvg"])
        gives = {"Sniper's Mark": models.number(stats.get("CritChance")) > 0,
                 **{m: models.number(stats.get(f"{a}BuildupAvg")) > 0 for m, a in models.MARK_BUFFS.items()}}
        left, usage = self.budget_left(), self.usage_stats()
        cap = self.ctx.request.max_buttons
        before = build.score
        # (PoB gives a group a notable grants a source, so it reads as fromItem)
        called = next((g for g in groups if g["gems"] and g["gems"][0]["name"] == "Called Shots"), None)
        best: tuple[float, dict] | None = None
        tried: list[str] = []
        for mark in models.MARKS:
            level = self.usable_level(mark)
            if not gives.get(mark):
                self.left_out[mark] = ("Bloodhound's Mark adds nothing PoB counts to the main skill" if mark not in gives
                                       else "the model gives it nothing on this build (no crits, or the main skill can't "
                                            "inflict its ailment)")
                continue
            if level is None or not self._offered(mark, left, usage):
                self.left_out[mark] = "not usable: not affordable, or its attributes aren't met"
                continue
            gem = {"name": mark, "level": level, "quality": GEM_QUALITY}
            placements: list[dict] = []
            if called is not None:
                placements.append({"index": called["index"],
                                   "gems": [{"name": g["name"], "level": g["level"], "quality": g["quality"]}
                                            for g in called["gems"]] + [gem]})
            if len(player_groups) < MAX_GEM_GROUPS:
                placements.append({"gems": [gem]})
                for meta in ("Pounce", "Hand of Chayula"):
                    meta_level = self.usable_level(meta)
                    if meta_level is not None and self._offered(meta, left, usage):
                        placements.append({"gems": [{"name": meta, "level": meta_level, "quality": GEM_QUALITY}, gem]})
            for place in placements:
                if clock.now() > deadline:
                    break
                score = self.trial_mark(place, cap)
                if score is not None and score > before + EPSILON and (best is None or score > best[0]):
                    best = (score, place)
            tried.append(mark)
        chosen = best[1]["gems"][-1]["name"] if best else None
        for mark in tried:
            if mark != chosen:
                self.left_out[mark] = "tried: the build scored no better with it (or it needed a button over the cap)"
        if best is None:
            return False
        place = best[1]
        if "index" in place:
            build.set_group(index=place["index"], gems=place["gems"])
        else:
            build.set_group(gems=place["gems"], label=MARK_LABEL)
        if self.resolve is not None:
            self.resolve()
        self.left_out.pop(place["gems"][-1]["name"], None)
        return build.score > before + EPSILON

    def mirage_round(self, deadline: float) -> bool:
        """Slice 7: a ranged Attack for a Mirage, kept if the build scores better, models.mirage_model pricing the share
        of time the Mirage is up (one per 10 s). In a Mirage Deadeye group the build has (the notable's; the ascendancy
        round tries it, tree.granted_trial), else in a new Mirage Archer group (60 Spirit, within the spare-Spirit floor; persistent, so no button).
        The Attacks are ones the build doesn't use, ranked by what PoB adds to Full DPS (it counts the Attack as always
        on, so only a ranking); the best MIRAGE_TRIALS get a real trial."""
        from builder.search import models

        build = self.build
        if clock.now() > deadline or build.main_group() is None:
            return False
        groups = [g for g in build.groups() if g["enabled"]]
        skills = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
        metas = {s.name: s for s in skills if s.name in models.MIRAGE_METAS}
        if any(s.attack and s.name not in models.MIRAGE_METAS and s.group == m.group for m in metas.values() for s in skills):
            return False  # a Mirage has its Attack already
        present = {g["name"] for grp in groups for g in grp["gems"]}
        left, usage = self.budget_left(), self.usage_stats()
        deadeye = next((g for g in groups if any(x["name"] == "Mirage Deadeye" for x in g["gems"])), None)
        if deadeye is None and (len([g for g in build.groups() if not g["fromItem"]]) >= MAX_GEM_GROUPS
                                or "Mirage Archer" in present or not self._offered("Mirage Archer", left, usage)
                                or self.usable_level("Mirage Archer") is None):
            return False
        meta = "Mirage Deadeye" if deadeye is not None else "Mirage Archer"
        attacks = [n for n, gem in self.ctx.gems.items() if not gem["support"] and n not in present
                   and "Attack" in gem["skillTypes"] and ({"RangedAttack", "CrossbowAmmoSkill"} & set(gem["skillTypes"]))
                   and not {"Meta", "Persistent", "Triggered"} & set(gem["skillTypes"]) and self._offered(n, left, usage)]
        if not attacks:
            return False
        spare_floor = max(0, self.ctx.request.floors.spare_spirit or 0)
        cases = [{"newGroup": [meta, n], "level": GEM_LEVEL, "quality": GEM_QUALITY, "fullDPS": True} for n in attacks]
        full = []
        for start in range(0, len(cases), 50):
            full += build.call("gemDeltas", cases=cases[start:start + 50], fields=["FullDPS", "SpiritUnreserved"],
                               fullDPS=True, skipEHP=True)["results"]
        base = models.number(build.stats.get("FullDPS"))
        ranked = sorted(((float(r.get("FullDPS") or 0), n) for r, n in zip(full, attacks)
                         if float(r.get("FullDPS") or 0) > base
                         and (deadeye is not None or models.number(r.get("SpiritUnreserved")) >= spare_floor)), reverse=True)
        before = build.score
        best: tuple[float, dict] | None = None
        for _, name in ranked[:MIRAGE_TRIALS]:
            if clock.now() > deadline:
                break
            level = self.usable_level(name)
            if level is None:
                continue
            gem = {"name": name, "level": level, "quality": GEM_QUALITY}
            if deadeye is not None:
                place = {"index": deadeye["index"], "gems": [{"name": g["name"], "level": g["level"], "quality": g["quality"]}
                                                             for g in deadeye["gems"]] + [gem]}
            else:
                place = {"gems": [{"name": meta, "level": self.usable_level(meta), "quality": GEM_QUALITY}, gem],
                         "label": MIRAGE_LABEL}
            score = self.trial_mark(place, self.ctx.request.max_buttons)
            if score is not None and score > before + EPSILON and (best is None or score > best[0]):
                best = (score, place)
        if best is None:
            self.left_out[meta] = "tried: no ranged Attack in a Mirage scored better (or none fits the Spirit left)"
            return False
        place = best[1]
        if "index" in place:
            build.set_group(index=place["index"], gems=place["gems"])
        else:
            build.set_group(gems=place["gems"], label=MIRAGE_LABEL)
        if self.resolve is not None:
            self.resolve()
        self.left_out.pop(meta, None)
        return build.score > before + EPSILON

    def fill_granted(self, skill: str) -> bool:
        """Fill the group of a meta skill a notable grants (tree.GRANTED_METAS) with what it uses: a Mark in Called Shots,
        a ranged Attack in Mirage Deadeye, a spell (with supports) in an Energy meta such as Fire Spell on Hit. For the
        ascendancy round's trial of the notable."""
        from builder.search import models

        deadline = clock.now() + GRANTED_FILL_SECONDS
        if skill == "Called Shots":
            return self.mark_round(deadline)
        if skill in models.META_GEMS:
            return self.granted_meta_round(deadline)
        if skill == models.HOLLOW_FORM:
            return self.hollow_round(deadline)
        return self.mirage_round(deadline)

    def hollow_round(self, deadline: float) -> bool:
        """Slice 8: move the main skill (a melee Attack) and its supports into the Hollow Form group the notable grants,
        so you channel Hollow Form and its images use it (models.hollow_model prices the images its Power Charges add),
        the old group switched off. Kept if the build scores better within the button cap; undone otherwise."""
        from builder.search import models

        build = self.build
        main = build.main_group()
        if clock.now() > deadline or main is None or main["fromItem"]:
            return False
        hollow = next((g for g in build.groups() if g["enabled"] and g["fromItem"]
                       and any(x["name"] == models.HOLLOW_FORM for x in g["gems"])), None)
        if hollow is None or len(hollow["gems"]) > 1:
            return False  # (no group, or it holds something already)
        attack = next((g for g in main["gems"] if g.get("enabled", True) and g["name"] in self.ctx.gems
                       and not self.ctx.gems[g["name"]]["support"]), None)
        types = set(self.ctx.gems[attack["name"]]["skillTypes"]) if attack else set()
        if attack is None or not {"Attack", "Melee"} <= types or types & models.HOLLOW_EXCLUDES:
            self.left_out[models.HOLLOW_FORM] = "the main skill isn't a melee Attack Hollow Form can use"
            return False
        before, snap = build.score, build.snapshot()
        moved = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]
                 if g.get("enabled", True)]
        keep = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in hollow["gems"]]
        build.set_group(index=main["index"], enabled=False)
        build.set_group(index=hollow["index"], gems=keep + moved, main=True, mainActiveSkill=2)
        if self.resolve is not None:
            self.resolve()
        cap = self.ctx.request.max_buttons
        if build.score <= before + EPSILON or (cap is not None and len(self.buttons()) > cap):
            build.restore(snap)
            if self.resolve is not None:
                self.resolve()
            self.left_out[models.HOLLOW_FORM] = "tried: the main skill in Hollow Form scored no better (or went over the cap)"
            return False
        self.left_out.pop(models.HOLLOW_FORM, None)
        return True

    def roar_round(self, deadline: float) -> bool:
        """Slice 7: Ferocious Roar on its own (its Rage, models.roar_model, and its Armour Break,
        conditions.Conditions.roar_evidence; PoB reads neither), kept if the build scores better and its button fits the
        cap. Only for an Attack main skill; PoB disables it without a Talisman. A Warcry socketed in it isn't proposed:
        the search doesn't use Warcries yet."""
        from builder.search import models

        build = self.build
        name = models.FEROCIOUS_ROAR
        if (clock.now() > deadline or build.main_group() is None or self.conditions is None
                or "Attack" not in self.conditions.main_types):
            return False
        if any(g["name"] == name for grp in build.groups() for g in grp["gems"]):
            return False
        if len([g for g in build.groups() if not g["fromItem"]]) >= MAX_GEM_GROUPS:
            return False
        level = self.usable_level(name)
        if level is None or not self._offered(name, self.budget_left(), self.usage_stats()):
            self.left_out[name] = "not usable: not affordable, or its attributes aren't met"
            return False
        before = build.score
        place = {"gems": [{"name": name, "level": level, "quality": GEM_QUALITY}], "label": ROAR_LABEL}
        score = self.trial_mark(place, self.ctx.request.max_buttons)
        if score is None or score <= before + EPSILON:
            self.left_out[name] = ("tried: its button doesn't fit the cap" if score is None
                                   else "tried: no gain (no Talisman, or its Rage and Armour Break don't pay for the time it takes)")
            return False
        build.set_group(gems=place["gems"], label=ROAR_LABEL)
        if self.resolve is not None:
            self.resolve()
        self.left_out.pop(name, None)
        return build.score > before + EPSILON

    def trial_mark(self, place: dict, cap: int | None) -> float | None:
        """Score a Mark placement for real (commit, resolve, read, undo); None if its buttons go over the cap."""
        build = self.build
        if "index" in place:
            old = next(g for g in build.groups() if g["index"] == place["index"])
            old_gems = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in old["gems"]]
            build.set_group(index=place["index"], gems=place["gems"])
        else:
            added = build.set_group(gems=place["gems"], label=place.get("label", MARK_LABEL))
        if self.resolve is not None:
            self.resolve()
        score = build.score if cap is None or len(self.buttons()) <= cap else None
        if "index" in place:
            build.set_group(index=place["index"], gems=old_gems)
        else:
            build.remove_group(added["index"])
        if self.resolve is not None:
            self.resolve()
        return score

    def buttons(self) -> list[str]:
        """The skills the build makes the player press (constraints.player_buttons' rule on the current groups): not
        persistent, meta-triggered, triggered or item-granted; an Invocation is one, and so is a pressed meta a passive
        or item grants (Hollow Form)."""
        from builder.request.constraints import presses

        names: list[str] = []
        for group in self.build.groups():
            if not group["enabled"]:
                continue
            known = self.ctx.any_gems
            gems = [known[g["name"]] for g in group["gems"] if g.get("enabled", True) and g["name"] in known]
            by_meta = any("Meta" in g["skillTypes"] for g in gems if not g["support"])
            used = len([g for g in gems if not g["support"]]) > 1  # (a granted meta with nothing in it isn't pressed)
            for gem in gems:
                if group["fromItem"] and not ("Meta" in gem["skillTypes"] and used):
                    continue
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
