"""Jewels (PLAN.md §5.2.4): rare and unique jewels in the passive tree's sockets.

Allocated sockets get the best jewel that scores: an affordable unique (each one once), or a rare
built affix by affix from the Jewel modifier pool on a Ruby, Emerald or Sapphire: two prefixes and
two suffixes, or (0.5 crafting) three of one kind, one of the other and the crafted line that
boosts the three (builder/search/rares.py JEWEL_CRAFTS). Full rounds also try one more socket near the tree: if no points are free, the
weakest branch makes room, the socket is filled and the leftover points regrown; the change is
kept only if the build ends better.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

from builder.search.context import ItemOption
from builder.search.rares import SCREEN_KEEP, Affix, AffixPools, boosted, rare_price, rare_text
from builder.search.state import EPSILON, Build

JEWEL_BASES = ("Ruby", "Emerald", "Sapphire")
JEWEL_LIMIT = {"Prefix": 2, "Suffix": 2}
SOCKET_REACH = 4


class Jewels:
    def __init__(self, build: Build, tree, gear) -> None:
        self.build = build
        self.ctx = build.ctx
        self.tree = tree
        self.gear = gear
        self.pools = AffixPools(self.ctx, "Jewel")
        self.rare: ItemOption | None = None  # the last rare jewel built (copies may fill several sockets)

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

    def uniques(self, slot: str) -> list[ItemOption]:
        others = {o.name for s, o in self.build.equipped.items() if s != slot and o.unique}
        left = self.gear.left_for(slot)
        return [o for o in self.ctx.item_options.get("Jewel", [])
                if o.unique and o.name not in others and (o.price or 0.0) <= left + 1e-9]

    def build_rare(self, slot: str, deadline: float, required: Callable[[Affix], bool] | None = None) -> ItemOption | None:
        """The best rare jewel for this socket within its budget. With `required`, it starts from the
        best affix that passes it (a condition's source, builder/search/sources.py), with the plain
        two-and-two shape."""
        left = self.gear.left_for(slot)
        level = self.pools.item_level
        best: tuple[float, ItemOption] | None = None
        for base_name in JEWEL_BASES:
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
            for kind in JEWEL_LIMIT if required is None else ():
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
        options = [o for o in self.uniques(slot) if o != current]
        if self.rare is not None and self.rare != current and (self.rare.price or 0.0) <= self.gear.left_for(slot) + 1e-9:
            options.append(self.rare)
        if not options:
            return False
        scored = build.item_deltas([{"slot": slot, "raw": o.raw} for o in options])
        value, best = max(((self.gear.adjusted(s, o), o) for (s, _), o in zip(scored, options)), key=lambda x: x[0])
        if value <= self.gear.adjusted(build.score, current) + EPSILON:
            return False
        before = build.score
        build.equip(slot, best)
        self.gear.settle()
        if build.worse(before):
            build.equip(slot, current)
            self.gear.settle()
            return False
        return True

    def jewel_round(self, deadline: float, full: bool = True) -> bool:
        improved = False
        sockets = self.sync()
        for index, socket in enumerate(sockets):
            if time.perf_counter() > deadline:
                break
            improved = self.fill(socket, deadline, rebuild=full and index == 0) or improved
        if full and time.perf_counter() < deadline:
            improved = self.try_new_socket(deadline) or improved
        return improved

    def try_new_socket(self, deadline: float) -> bool:
        """Take the nearest free socket (making room if needed) and keep it only if it pays."""
        build, tree = self.build, self.tree
        forbidden = self.ctx.forbidden_node_ids
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
