"""Gear block, uniques only for now (rares arrive in Phase 3).

Each slot holds a normal base (always available, found rather than bought) or an affordable,
allowed unique, scored with item what-ifs (bridge `itemDeltas`). Budget holds at the source: a
unique is proposed only if it fits the divine cap with everything else already bought, and only
if it has a price (unpriced items are never treated as free). Required uniques are equipped first
and locked; armour pieces only carry the defence layers the requested style allows.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from builder.search.context import ARMOUR_SLOTS, JEWELLERY_SLOTS, SLOT_TYPE, ItemOption
from builder.search.state import EPSILON, Build

BASES_PER_SUBTYPE = 3  # normal armour/jewellery bases kept per sub-type, highest level first
SHORTLIST = 6  # options per slot rescored between full rounds


@dataclass(frozen=True)
class SlotSpec:
    type: str
    sub_type: str | None = None  # "" means "no sub-type" (caster staves); None means any


class Gear:
    def __init__(self, build: Build, weapon: SlotSpec | None, offhand: SlotSpec | None, rng: random.Random) -> None:
        self.build = build
        self.ctx = build.ctx
        self.rng = rng
        self.slots: dict[str, SlotSpec] = {}
        if weapon:
            self.slots["Weapon 1"] = weapon
        if offhand:
            self.slots["Weapon 2"] = offhand
        for slot in ARMOUR_SLOTS + JEWELLERY_SLOTS:
            self.slots[slot] = SlotSpec(SLOT_TYPE[slot])
        self._options: dict[str, list[ItemOption]] = {}
        self.shortlist: dict[str, list[str]] = {}

    def options(self, slot: str) -> list[ItemOption]:
        if slot not in self._options:
            spec = self.slots[slot]
            options = list(self.ctx.item_options.get(spec.type, []))
            if spec.sub_type is not None:
                options = [o for o in options if (o.sub_type or "") == spec.sub_type]
            if not slot.startswith("Weapon"):
                # Normal armour and jewellery bases differ little within a sub-type: keep the top few
                bases: dict[str | None, list[ItemOption]] = {}
                for option in sorted((o for o in options if not o.unique), key=lambda o: -o.level):
                    bases.setdefault(option.sub_type, []).append(option)
                kept = [o for group in bases.values() for o in group[:BASES_PER_SUBTYPE]]
                options = kept + [o for o in options if o.unique]
            self._options[slot] = options
        return self._options[slot]

    def setup(self) -> list[str]:
        """Equip required uniques (locked) and a starting base in each weapon slot; the gear round
        fills the rest by score. Returns problems (a required unique that fits no slot)."""
        build = self.build
        problems = []
        wanted = list(self.ctx.compiled.request.require.uniques)
        if self.ctx.request.weapon_unique:
            wanted.insert(0, self.ctx.request.weapon_unique)
        for name in dict.fromkeys(wanted):
            option = self.ctx.unique_option(name)
            slot = self.slot_for(option) if option else None
            if slot is None:
                problems.append(f"required unique {name} fits no slot of this skeleton")
                continue
            build.equip(slot, option)
            build.locked_slots.add(slot)
        for slot in self.slots:
            if slot in build.locked_slots or not slot.startswith("Weapon"):
                continue
            bases = [o for o in self.options(slot) if not o.unique]
            if bases:
                build.equip(slot, max(bases, key=lambda o: o.level))
        return problems

    def slot_for(self, option: ItemOption) -> str | None:
        for slot, spec in self.slots.items():
            if slot in self.build.locked_slots or spec.type != option.type:
                continue
            if spec.sub_type is not None and (option.sub_type or "") != spec.sub_type:
                continue
            return slot
        return None

    def candidates(self, slot: str) -> list[ItemOption]:
        build = self.build
        current = build.equipped.get(slot)
        left = self.ctx.budget - build.spent() + ((current.price or 0.0) if current else 0.0)
        others = {o.name for s, o in build.equipped.items() if s != slot and o.unique}
        return [o for o in self.options(slot)
                if (o.price or 0.0) <= left + 1e-9 and o != current and not (o.unique and o.name in others)]

    def gear_round(self, deadline: float, full: bool = True) -> bool:
        """Best affordable item per slot. A full round scores every option and remembers the top
        few per slot; other rounds rescore only those."""
        build = self.build
        improved = False
        for slot in self.slots:
            if slot in build.locked_slots or time.perf_counter() > deadline:
                continue
            options = self.candidates(slot)
            if not full and slot in self.shortlist:
                shortlisted = set(self.shortlist[slot])
                options = [o for o in options if o.name in shortlisted]
            scored = build.item_deltas([{"slot": slot, "raw": o.raw} for o in options])
            if not scored:
                continue
            ranked = sorted(zip((s for s, _ in scored), options), key=lambda x: -x[0])
            if full or slot not in self.shortlist:
                self.shortlist[slot] = [o.name for _, o in ranked[:SHORTLIST]]
            best_score, best = ranked[0]
            if best_score > build.score + EPSILON:
                before, previous = build.score, build.equipped.get(slot)
                build.equip(slot, best)
                if build.worse(before):
                    build.equip(slot, previous)
                    continue
                improved = improved or build.score > before + EPSILON
        return improved

    def kick(self) -> None:
        slots = [s for s in self.slots if s not in self.build.locked_slots]
        if not slots:
            return
        slot = self.rng.choice(slots)
        options = self.candidates(slot)
        if options:
            self.build.equip(slot, self.rng.choice(options))
