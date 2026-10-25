"""Jewels (PLAN.md §5.2.4): rare and unique jewels in the passive tree's sockets.

Allocated sockets get the best jewel that scores: an affordable unique (each one once), or a rare
built affix by affix from the Jewel modifier pool on a Ruby, Emerald or Sapphire: two prefixes and
two suffixes, or (0.5 crafting) three of one kind, one of the other and the crafted line that
boosts the three (builder/search/rares.py JEWEL_CRAFTS). Full rounds also try one more socket near the tree: if no points are free, the
weakest branch makes room, the socket is filled and the leftover points regrown; the change is
kept only if the build ends better.

Jewels that change the tree (PLAN.md "Radius jewels") are classified by PoB's parse of them
(builder/search/radius.py), never by name:
- Time-Lost rares ("Small/Notable Passive Skills in Radius also grant ...") are built like other
  rares and scored in the socket they go in, so the nodes in their radius count.
- Uniques with a radius or a granted passive (Against the Darkness, Megalomaniac) get their best
  variants, each scored by a what-if in the socket.
- Jewels that let the tree take nodes without a connection (Controlled Metamorphosis's radius,
  From Nothing's keystone) are tried for real in full rounds: each socket and variant is screened
  by what the newly takeable nodes would give, then the best are socketed, the weakest branches
  make room and the tree regrows; kept only if the build ends better.
- Jewels PoB miscalculates (timeless jewels: no seed tables) are never proposed.
- A passive that enlarges Time-Lost radii (Baryanic Leylines, a custom model in the bridge) is
  worth nothing without a Time-Lost jewel, and a Time-Lost jewel is scored without it: full rounds
  try the pair (radius_trial).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

from builder.search.context import ItemOption
from builder.search.radius import Variants, compact, miscalculated, tree_changing, unlocks, variants_of, with_variants
from builder.search.rares import SCREEN_KEEP, Affix, AffixPools, boosted, rare_price, rare_text
from builder.search.state import EPSILON, Build
from builder.search.tree import FULL_EVERY

JEWEL_BASES = ("Ruby", "Emerald", "Sapphire")
RADIUS_BASES = ("Time-Lost Ruby", "Time-Lost Emerald", "Time-Lost Sapphire")  # rares with a radius (Small)
JEWEL_LIMIT = {"Prefix": 2, "Suffix": 2}
SOCKET_REACH = 4
VARIANT_SCREEN = 24  # variants rescored for a unique's later selections (Megalomaniac has 874 each)
UNLOCK_TRIALS = 2  # socket and variant pairs grown for real per full round
UNLOCK_SCREENS = 12  # socket and variant pairs screened per full round (each socketed for real)
UNLOCK_POINTS = 8  # newly takeable nodes a screen counts, and points a trial may free for them
RADIUS_MODEL_ENTRY = "baryanic-leylines"  # the ledger entry for the bridge's larger Time-Lost radius model


class Jewels:
    def __init__(self, build: Build, tree, gear) -> None:
        self.build = build
        self.ctx = build.ctx
        self.tree = tree
        self.gear = gear
        self.pools = AffixPools(self.ctx, "Jewel")
        self.rare: ItemOption | None = None  # the last rare jewel built (copies may fill several sockets)
        self._traits: dict[str, dict] = {}  # item text -> what PoB says it does to the tree
        # (unique, socket or None when the socket doesn't matter) -> (full round, the unique at its
        # best variants then): rescored once per full round
        self.versions: dict[tuple[str, str | None], tuple[int, ItemOption]] = {}
        self.full_rounds = 0
        self.left_out: dict[str, str] = {}  # unique -> why it's never proposed (for the result)
        self.screened: set[tuple[str, int, tuple[str, ...]]] = set()  # unlock screens since the last full cycle
        # The larger Time-Lost radius model is on unless the request forbids its ledger entry
        build.call("radiusModel", enabled=RADIUS_MODEL_ENTRY not in self.ctx.request.forbid_ledger_entries)

    @staticmethod
    def slot(node_id: int) -> str:
        return f"Jewel {node_id}"

    def sockets(self) -> list[int]:
        return [n["id"] for n in self.build.call("allocatedNodes")["nodes"] if n["type"] == "Socket"]

    def sync(self) -> list[int]:
        """Forget jewels whose socket the tree no longer has; returns the allocated sockets."""
        sockets = self.sockets()
        live = {self.slot(i) for i in sockets}
        for slot in [s for s in self.build.equipped if s.startswith("Jewel ") and s not in live]:
            self.build.equip(slot, None)
        return sockets

    def traits(self, options: list[ItemOption]) -> list[dict]:
        missing = list(dict.fromkeys(o.raw for o in options if o.raw not in self._traits))
        if missing:
            for raw, traits in zip(missing, self.build.call("jewelTraits", raws=missing)["traits"]):
                self._traits[raw] = traits
        return [self._traits[o.raw] for o in options]

    def uniques(self, slot: str) -> list[ItemOption]:
        others = {o.name for s, o in self.build.equipped.items() if s != slot and o.unique}
        left = self.gear.left_for(slot)
        options = [o for o in self.ctx.item_options.get("Jewel", [])
                   if o.unique and o.name not in others and (o.price or 0.0) <= left + 1e-9]
        kept = []
        for option, traits in zip(options, self.traits(options)):
            if reason := miscalculated(traits):
                self.left_out[option.name] = reason
            else:
                kept.append(option)
        return kept

    # Tree-changing uniques at their best variants ------------------------------------------------

    def version(self, slot: str, option: ItemOption, deadline: float) -> ItemOption:
        """A unique with a radius or a granted passive at the variants that score best (in this
        socket when it has a radius). Others, and jewels whose worth is what they let the tree
        take (unlock_round), stay as they are."""
        traits = self.traits([option])[0]
        variants = variants_of(option.raw)
        if variants is None or not tree_changing(traits) or unlocks(traits):
            return option
        key = (option.name, slot if traits.get("radius") else None)
        if key not in self.versions or self.versions[key][0] != self.full_rounds:
            self.versions[key] = (self.full_rounds, self.best_version(slot, option, variants, deadline))
        return self.versions[key][1]

    def best_version(self, slot: str, option: ItemOption, variants: Variants, deadline: float) -> ItemOption:
        """Choose each selection in turn with the others held, among the variants a real item can
        have, all different; after the first, only the first's best few are rescored."""
        # A variant that grants a banned passive ("Allocates <name>") is never chosen
        banned = self.ctx.compiled.filters.forbidden_passives
        usable = [i for i in variants.usable
                  if not any(line.split("Allocates ", 1)[1].strip() in banned
                             for line in variants.lines[i - 1] if "Allocates " in line)]
        if len(usable) < len(variants.slots):
            return option
        choice: list[int] = []
        for i in variants.selected:
            choice.append(i if i in usable and i not in choice else next(u for u in usable if u not in choice))
        ranked: list[int] | None = None
        for k in range(len(choice)):
            if time.perf_counter() > deadline:
                break
            others = set(choice[:k] + choice[k + 1:])
            pool = usable if ranked is None else ranked[:VARIANT_SCREEN] + [choice[k]]
            pool = [i for i in dict.fromkeys(pool) if i not in others]
            cases = [tuple(choice[:k] + [i] + choice[k + 1:]) for i in pool]
            scored = self.build.item_deltas([{"slot": slot, "raw": compact(option.raw, c)} for c in cases])
            order = sorted(zip((score for score, _ in scored), pool), key=lambda x: -x[0])
            if ranked is None:
                ranked = [i for _, i in order]
            choice[k] = order[0][1]
        if tuple(choice) == variants.selected:
            return option
        # The price feed has one price for every version of a unique: the result says so
        return dataclasses.replace(option, raw=with_variants(option.raw, variants, tuple(choice)),
                                   variants=variants.label(tuple(choice)), confidence="low")

    def build_rare(self, slot: str, deadline: float, required: Callable[[Affix], bool] | None = None,
                   bases: tuple[str, ...] = JEWEL_BASES + RADIUS_BASES) -> ItemOption | None:
        """The best rare jewel for this socket within its budget, on one of `bases`. With `required`,
        it starts from the best affix that passes it (a condition's source, builder/search/sources.py),
        with the plain two-and-two shape."""
        left = self.gear.left_for(slot)
        level = self.pools.item_level
        best: tuple[float, ItemOption] | None = None
        for base_name in bases:
            base = self.ctx.bases_by_name.get(base_name)
            pool = self.pools.pool(base_name) if base else []
            if not pool or time.perf_counter() > deadline:
                continue
            scored = self.build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, [a])} for a in pool])
            ranked = [a for _, a in sorted(zip((s for s, _ in scored), pool), key=lambda x: -x[0])]
            shapes = [(JEWEL_LIMIT, [], None)]
            if required is not None:
                forced = next((a for a in ranked if required(a)), None)
                if forced is None:
                    continue
                shapes = [(JEWEL_LIMIT, [forced], None)]
            # The 0.5 jewel craft is for the plain jewels; Time-Lost ones keep the plain shape
            for kind in JEWEL_LIMIT if required is None and base_name in JEWEL_BASES else ():
                if crafted := self.pools.crafted(kind):
                    other = next(k for k in JEWEL_LIMIT if k != kind)
                    shapes.append(({kind: 3, other: 1}, [crafted[0]], (kind, crafted[1])))
            for limits, fixed, boost in shapes:
                shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
                if boost:
                    shortlist = [dataclasses.replace(a, lines=tuple(boosted(l, boost[1]) for l in a.lines))
                                 if a.type == boost[0] else a for a in shortlist]
                found = self.greedy(slot, base, shortlist, limits, fixed, left, deadline, required is not None)
                if found and (best is None or found[0] > best[0]):
                    value, chosen = found
                    price = None if self.ctx.ssf else rare_price("Jewel", chosen)
                    best = (value, ItemOption(
                        "Rare Jewel", base_name, "Jewel", None, rare_text("Jewel", base, level, chosen), False, price,
                        "ssf" if self.ctx.ssf else "estimate (uncalibrated)", 0, rare=True,
                        affixes=tuple(line for a in chosen for line in a.lines)))
        return best[1] if best else None

    def greedy(self, slot: str, base: dict, shortlist: list, limits: dict[str, int], fixed: list, left: float,
               deadline: float, fixed_only: bool = False) -> tuple[float, list] | None:
        """Add affixes to `fixed` while one pays: (score less the price's weight, affixes), or None
        when nothing beyond `fixed` fits the budget or pays (unless `fixed_only` allows that)."""
        build, lam, level = self.build, self.gear.lam, self.pools.item_level
        chosen = list(fixed)
        if rare_price("Jewel", chosen) > left + 1e-9:
            return None
        current = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen)}])[0][0]
        current -= lam * rare_price("Jewel", chosen)
        while time.perf_counter() < deadline:
            groups = {a.group for a in chosen}
            counts = {k: sum(1 for a in chosen if a.type == k and not a.crafted) for k in limits}
            candidates = (c for a in shortlist if a.group not in groups and counts[a.type] < limits[a.type]
                          for c in self.pools.choices(a, chosen))
            options = [a for a in candidates if rare_price("Jewel", chosen + [a]) <= left + 1e-9]
            if not options:
                break
            scored = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen + [a])}
                                        for a in options])
            value, affix = max(((s - lam * rare_price("Jewel", chosen + [a]), a)
                                for (s, _), a in zip(scored, options)), key=lambda x: x[0])
            if value <= current + EPSILON:
                break
            chosen.append(affix)
            current = value
        return (current, chosen) if len(chosen) > len(fixed) or (fixed_only and fixed) else None

    def fill(self, socket: int, deadline: float, rebuild: bool) -> bool:
        build = self.build
        slot = self.slot(socket)
        if rebuild or self.rare is None:
            rare = self.build_rare(slot, deadline)
            if rare is not None:
                self.rare = rare
        current = build.equipped.get(slot)
        options = [self.version(slot, o, deadline) for o in self.uniques(slot)]
        options = [o for o in options if o != current]
        if self.rare is not None and self.rare != current and (self.rare.price or 0.0) <= self.gear.left_for(slot) + 1e-9:
            options.append(self.rare)
        if not options:
            return False
        scored = build.item_deltas([{"slot": slot, "raw": o.raw} for o in options])
        value, best = max(((self.gear.adjusted(s, o), o) for (s, _), o in zip(scored, options)), key=lambda x: x[0])
        if value <= self.gear.adjusted(build.score, current) + EPSILON:
            return False
        # Taking out a jewel the tree holds nodes through drops those nodes (PoB prunes them): the
        # what-if still counted them, and only a reload brings them back
        snap = build.snapshot() if self.holds(socket) else None
        before = build.score
        build.equip(slot, best)
        self.gear.settle()
        if build.worse(before):
            if snap is not None:
                build.restore(snap)
            else:
                build.equip(slot, current)
                self.gear.settle()
            return False
        return True

    def radius_notes(self) -> list[dict]:
        """The larger Time-Lost radius, for the result's assumptions (marked with an asterisk)."""
        model = self.build.call("radiusModel")
        if model["increase"] <= 0:
            return []
        allocated = {n["id"] for n in self.build.call("facts", fields=[])["passives"]}
        source = ", ".join(p["name"] for p in model["passives"] if p["id"] in allocated)
        notes = []
        for jewel in self.build.call("radiusJewels")["jewels"]:
            if enlarged := jewel.get("enlarged"):
                reached = set(enlarged["extra"]) & set(jewel["affected"])
                notes.append({
                    "label": f"{source}: {jewel['name']} (Jewel {jewel['socket']}) reaches {enlarged['percent']}% "
                             "further (custom model), allocated passives it adds",
                    "value": len(reached),
                    "basis": f"PoB ignores \"Non-Unique Time-Lost Jewels have {enlarged['percent']}% increased "
                             "radius\"; the jewel's \"in Radius\" lines also apply to the passives within its "
                             f"radius made {enlarged['percent']}% longer, by PoB's own distance rule. Assumed: the "
                             "increase lengthens the radius rather than its area",
                    "confidence": "theory"})
        return notes

    def holds(self, socket: int) -> bool:
        """Whether the tree holds nodes only through this socket's jewel."""
        return any(j["socket"] == socket and j["unconnected"] for j in self.build.call("radiusJewels")["jewels"])

    def jewel_round(self, deadline: float, full: bool = True) -> bool:
        improved = False
        self.full_rounds += 1 if full else 0
        sockets = self.sync()
        for index, socket in enumerate(sockets):
            if time.perf_counter() > deadline:
                break
            improved = self.fill(socket, deadline, rebuild=full and index == 0) or improved
        if full and time.perf_counter() < deadline:
            improved = self.try_new_socket(deadline) or improved
        if full and time.perf_counter() < deadline:
            improved = self.unlock_round(deadline) or improved
        if full and time.perf_counter() < deadline:
            improved = self.radius_trial(deadline) or improved
        return improved

    # A larger Time-Lost radius (Baryanic Leylines*) ---------------------------------------------

    def radius_trial(self, deadline: float) -> bool:
        """Take the ascendancy's best allocation with a passive that enlarges Time-Lost radii (the
        ascendancy round with it required), rebuild the jewels with the larger radius in effect,
        regrow; keep the pair only if the build ends better. Every other full round, while the
        ascendancy has such a passive and doesn't hold it."""
        build, tree = self.build, self.tree
        model = build.call("radiusModel")
        ascendancy = build.call("summary")["ascendClassName"]
        names = {p["name"] for p in model["passives"] if p["ascendancy"] == ascendancy} - tree.required_names
        if not model["enabled"] or model["increase"] > 0 or not names or self.full_rounds % 2:
            return False
        sockets = self.sync()
        near = None
        if not sockets:  # the nearest free socket, its path taken from the weakest branches
            forbidden = tree.forbidden
            options = [n for n in build.call("frontier", maxDistance=SOCKET_REACH, sockets="only")["nodes"]
                       if not forbidden.intersection(n["path"])]
            if not options:
                return False
            near = min(options, key=lambda n: (n["distance"], n["id"]))
        snap = build.snapshot()
        before = build.score
        tree.required_names |= names
        try:
            tree.ascendancy_round()
        finally:
            tree.required_names -= names
        if build.call("radiusModel")["increase"] <= 0:
            build.restore(snap)
            return False
        if near is not None:
            for _, branch in tree.weakest_branches():
                if tree.points_left() >= near["distance"]:
                    break
                if not branch & set(near["path"]):
                    tree.remove_branch(branch)
            if tree.points_left() < near["distance"]:
                build.restore(snap)
                return False
            build.allocate(add=[near["id"]], attribute=tree.attribute)
            sockets = [near["id"]]
        # A Time-Lost rare built for each socket with the larger radius in effect (its worth
        # depends on where it sits), offered beside the uniques
        for socket in sockets:
            if time.perf_counter() > deadline:
                break
            rare = self.build_rare(self.slot(socket), deadline, bases=RADIUS_BASES)
            if rare is not None:
                self.rare = rare
                self.fill(socket, deadline, rebuild=False)
        tree.grow(deadline)
        # Kept only for the pair: a jewel the larger radius reaches further, and a better build
        enlarged = any(j.get("enlarged") for j in build.call("radiusJewels")["jewels"])
        if enlarged and build.score > before + EPSILON:
            return True
        build.restore(snap)
        tree.round += 1
        return False

    # Jewels that let the tree take nodes without a connection -----------------------------------

    def unlock_round(self, deadline: float) -> bool:
        """Screen each such jewel, in each variant a real item can have, in each socket it could
        go in: an allocated socket the tree holds nothing through (giving up what's in it), or the
        nearest free socket, its path allocated first. When the socket doesn't matter (From
        Nothing's keystone) one socket is enough: an empty one if there is one. A screen sockets
        the jewel for real and scores each node it newly lets the tree take, alone at one point;
        the best screens are then tried for real (try_unlock)."""
        build, tree = self.build, self.tree
        forbidden = tree.forbidden
        paths = {s: () for s in self.sync() if not self.holds(s)}  # socket -> the path it still needs
        near = [n for n in build.call("frontier", maxDistance=SOCKET_REACH, sockets="only")["nodes"]
                if not forbidden.intersection(n["path"])]
        if near:
            target = min(near, key=lambda n: (n["distance"], n["id"]))
            paths[target["id"]] = tuple(target["path"])
        if not paths:
            return False
        options = [o for o in self.uniques(self.slot(next(iter(paths)))) if unlocks(self.traits([o])[0])]
        if not options:
            return False
        empty = [s for s, path in paths.items() if not path and self.slot(s) not in build.equipped]
        anywhere = empty[:1] or [s for s, path in paths.items() if path][:1] or [next(iter(paths))]
        pairs = [(socket, version) for option in options
                 for socket in (list(paths) if self.traits([option])[0].get("unconnected") else anywhere)
                 if (option.price or 0.0) <= self.gear.left_for(self.slot(socket)) + 1e-9
                 for version in self.unlock_versions(option)]
        # Every pair once before any again: a full round screens a share of them (each is socketed
        # for real, about a quarter of a second), so From Nothing's 33 keystones take a few rounds
        fresh = [p for p in pairs if (p[1].name, p[0], p[1].variants) not in self.screened]
        if len(fresh) < min(UNLOCK_SCREENS, len(pairs)):
            self.screened.clear()
            fresh = pairs
        tree.rng.shuffle(fresh)
        batch = sorted(fresh[:UNLOCK_SCREENS], key=lambda p: bool(paths[p[0]]))  # the new socket's last
        snap = build.snapshot()
        before = base = build.score
        opened_socket = None
        screened: list[tuple[float, int, ItemOption, int]] = []
        for socket, version in batch:
            if time.perf_counter() > deadline:
                break
            self.screened.add((version.name, socket, version.variants))
            if paths[socket] and opened_socket != socket:
                build.restore(snap)  # nothing screened before stays socketed
                build.allocate(add=[socket], attribute=tree.attribute)  # the socket with its path
                opened_socket, base = socket, build.score
            build.equip(self.slot(socket), version)
            opened = [n for n in build.call("frontier", maxDistance=1)["nodes"]
                      if n.get("unconnected") and n["id"] not in forbidden]
            cases = [{"add": [n["id"]], "addPaths": True, "attribute": tree.attribute} for n in opened]
            gains = sorted((score - build.score for score, _ in build.node_deltas(cases)), reverse=True)
            gains = [g for g in gains if g > EPSILON][:UNLOCK_POINTS]
            if gains:
                # What socketing it costs (Controlled Metamorphosis's resistances, the jewel it
                # replaces) plus what its best newly takeable nodes would add
                screened.append((build.score - (base if paths[socket] else before) + sum(gains), socket, version, len(gains)))
        build.restore(snap)
        screened.sort(key=lambda x: -x[0])
        for potential, socket, version, wanted in screened[:UNLOCK_TRIALS]:
            if potential <= EPSILON or time.perf_counter() > deadline:
                break
            if self.try_unlock(socket, version, wanted, paths[socket], deadline):
                return True
        return False

    def unlock_versions(self, option: ItemOption) -> list[ItemOption]:
        """Each variant a real item can have (a ring size, a keystone), or the jewel as it is."""
        variants = variants_of(option.raw)
        if variants is None or len(variants.slots) != 1:
            return [option]
        return [dataclasses.replace(option, raw=with_variants(option.raw, variants, (i,)),
                                    variants=variants.label((i,)), confidence="low") for i in variants.usable]

    def try_unlock(self, socket: int, version: ItemOption, wanted: int, path: tuple[int, ...], deadline: float) -> bool:
        """Free points from the weakest branches (never the socket's own) for the socket's path and
        the nodes the jewel opens, socket it, regrow; keep it only if the build ends better."""
        build, tree = self.build, self.tree
        snap = build.snapshot()
        before = build.score
        for _, branch in tree.weakest_branches():
            if tree.points_left() >= wanted + len(path):
                break
            if socket not in branch and not branch & set(path):
                tree.remove_branch(branch)
        if path:
            if tree.points_left() < len(path):
                build.restore(snap)
                return False
            build.allocate(add=[socket], attribute=tree.attribute)
        build.equip(self.slot(socket), version)
        self.gear.settle()
        tree.round += 1
        tree.allocations_since_full = FULL_EVERY
        tree.grow(deadline)
        if build.score > before + EPSILON:
            return True
        build.restore(snap)
        tree.round += 1
        return False

    def try_new_socket(self, deadline: float) -> bool:
        """Take the nearest free socket (making room if needed) and keep it only if it pays."""
        build, tree = self.build, self.tree
        forbidden = self.tree.forbidden
        nodes = build.call("frontier", maxDistance=SOCKET_REACH, sockets="only")["nodes"]
        nodes = [n for n in nodes if not forbidden.intersection(n["path"])]
        if not nodes:
            return False
        target = min(nodes, key=lambda n: (n["distance"], n["id"]))
        snap = build.snapshot()
        before = build.score
        for _, branch in tree.weakest_branches():
            if tree.points_left() >= target["distance"]:
                break
            if not branch & set(target["path"]):
                tree.remove_branch(branch)
        if tree.points_left() < target["distance"]:
            build.restore(snap)
            return False
        build.allocate(add=[target["id"]], attribute=tree.attribute)
        tree.round += 1
        self.fill(target["id"], deadline, rebuild=True)
        tree.grow(deadline)
        if build.score > before + EPSILON:
            return True
        build.restore(snap)
        tree.round += 1
        return False
