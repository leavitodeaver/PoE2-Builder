"""Gear block (PLAN.md §5.2.3–4): normal bases, uniques, rares built affix by affix, and the runes,
Soul Cores and Idols in their sockets; flasks and charms (normal, magic or unique) the same way, with
their slots switched on only while a boss fight sustains them (builder/search/flasks.py).

Each slot compares every affordable option with item what-ifs (bridge `itemDeltas`): normal bases
(found, not bought), allowed and priced uniques, and in full rounds a rare built for the slot
(builder/search/rares.py). The winner's sockets are then filled. Budget holds at the source: nothing
is proposed that doesn't fit the divine cap with everything else already bought, and unpriced items
are never treated as free. When the cap binds, a budget weight λ (score per divine) makes each
choice weigh its price, so the money goes where it buys the most (a Lagrangian, adjusted after every
full round). Required uniques are equipped first and locked; armour pieces only carry the defence
layers the requested style allows.
"""

from __future__ import annotations

import dataclasses
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.context import ARMOUR_SLOTS, JEWELLERY_SLOTS, SLOT_TYPE, ItemOption
from builder.search.flasks import (
    ACTIVE_UPTIME, CHARM_SLOTS, FLASK_SLOTS, RECOVERY_FIELDS, Charges, charges_per_second, is_flask_slot,
    recovery_per_second, slot_pool,
    uptime,
)
from builder.search.rares import (
    MAGIC_LIMIT, RUNE_KEEP, SCREEN_KEEP, Affix, AffixPools, RuneCatalog, magic_price, magic_text, rare_price, rare_text,
    socket_count, with_runes,
)
from builder.search.state import EPSILON, Build

BASES_PER_SUBTYPE = 3  # normal armour/jewellery bases kept per sub-type, highest level first
SHORTLIST = 6  # options per slot rescored between full rounds
AFFIX_LIMIT = {"Prefix": 3, "Suffix": 3}


@dataclass(frozen=True)
class SlotSpec:
    type: str
    sub_type: str | None = None  # "" means "no sub-type" (caster staves); None means any
    also: tuple[str, ...] = ()  # more item types the slot takes (an off-hand weapon: Sceptre, Dagger, ...)

    @property
    def types(self) -> tuple[str, ...]:
        return (self.type, *self.also)


def extra_ring_passive(ctx, ascendancy: str) -> str | None:
    """The ascendancy passive that adds a ring slot (Ritualist's Unfurled Finger), if any."""
    for node in ctx.data.tree["nodes"]:
        if node.get("ascendancy") == ascendancy and "+1 Ring Slot" in (node.get("stats") or []):
            return node.get("name")
    return None


class Gear:
    def __init__(self, build: Build, weapon: SlotSpec | None, offhand: SlotSpec | None, rng: random.Random,
                 swap: tuple[SlotSpec | None, SlotSpec | None] = (None, None)) -> None:
        """`swap` is weapon set 2's main hand and off hand (weapon swap), if the skeleton has one."""
        self.build = build
        self.ctx = build.ctx
        self.rng = rng
        self.slots: dict[str, SlotSpec] = {}
        for slot, spec in zip(("Weapon 1", "Weapon 2", "Weapon 1 Swap", "Weapon 2 Swap"), (weapon, offhand, *swap)):
            if spec:
                self.slots[slot] = spec
        for slot in ARMOUR_SLOTS + JEWELLERY_SLOTS:
            self.slots[slot] = SlotSpec(SLOT_TYPE[slot])
        # After the belt, which sets how many charms count
        for slot, pool in FLASK_SLOTS.items():
            self.slots[slot] = SlotSpec("Flask", pool)
        for slot in CHARM_SLOTS:
            self.slots[slot] = SlotSpec("Charm")
        self._options: dict[str, list[ItemOption]] = {}
        self.shortlist: dict[str, list[str]] = {}
        self.pools = AffixPools(self.ctx)
        self.runes = RuneCatalog(self.ctx, self.ctx.augment_prices)
        self.rares: dict[str, ItemOption] = {}  # the last rare built for each slot
        self.lam = 0.0  # budget weight: score per divine
        self.magic_pools = {kind: AffixPools(self.ctx, kind) for kind in ("Flask", "Charm")}
        self.charges = Charges()  # charge generation of the committed build (sync_flasks)
        self._flask_info: dict[str, dict] = {}  # item text -> PoB's flask/charm numbers
        self.resolve = None  # re-resolves conditions after a commit (set by the search)

    def flask_rate(self, pool: str) -> tuple[float, float]:
        """(charges generated per second, maximum charges) of the equipped flask for this pool, for
        supports that consume its charges on use (builder/search/usage.py)."""
        for slot, slot_pool_name in FLASK_SLOTS.items():
            option = self.build.equipped.get(slot)
            if slot_pool_name == pool and option is not None:
                info = self.flask_info([option.raw])[0]
                return charges_per_second(info, self.charges, pool), float(info.get("chargesMax") or 0)
        return 0.0, 0.0

    def settle(self) -> None:
        """After a commit: an item's what-if can't see a condition it switches on or off."""
        if self.resolve is not None:
            self.resolve()

    # Options ---------------------------------------------------------------------------------

    def options(self, slot: str) -> list[ItemOption]:
        if slot not in self._options:
            spec = self.slots[slot]
            options = [o for t in spec.types for o in self.ctx.item_options.get(t, [])]
            if spec.sub_type is not None:
                options = [o for o in options if (o.sub_type or "") == spec.sub_type]
            if not slot.startswith("Weapon") and spec.type != "Charm":
                # Normal armour and jewellery bases differ little within a sub-type: keep the top few
                bases: dict[str | None, list[ItemOption]] = {}
                for option in sorted((o for o in options if not o.unique), key=lambda o: -o.level):
                    bases.setdefault(option.sub_type, []).append(option)
                kept = [o for group in bases.values() for o in group[:BASES_PER_SUBTYPE]]
                options = kept + [o for o in options if o.unique]
            self._options[slot] = options
        return self._options[slot]

    def add_ring_slot(self) -> None:
        """A third ring slot (from an ascendancy passive). PoB ignores it until the passive is
        allocated, and the passive is worth nothing to the ascendancy round with the slot empty,
        so the slot starts with a free normal ring; the gear round upgrades it once it counts."""
        slots = list(self.slots.items())
        at = next(i for i, (slot, _) in enumerate(slots) if slot == "Ring 2") + 1
        self.slots = dict(slots[:at] + [("Ring 3", SlotSpec("Ring"))] + slots[at:])

    def setup(self, extra_uniques: list[str] = ()) -> list[str]:
        """Equip required uniques (locked) and a starting base in each weapon slot; the gear round
        fills the rest by score. Returns problems (a required unique that fits no slot)."""
        build = self.build
        problems = []
        wanted = list(self.ctx.compiled.request.require.uniques) + list(extra_uniques)
        if self.ctx.request.weapon_unique:
            wanted.insert(0, self.ctx.request.weapon_unique)
        for name in dict.fromkeys(wanted):
            option = self.ctx.unique_option(name)
            slot = self.slot_for(option) if option else None
            if slot is None:
                problems.append(f"required unique {name} fits no slot of this skeleton")
                continue
            self.put(slot, option)
            build.locked_slots.add(slot)
        for slot in self.slots:
            if slot in build.locked_slots or not (slot.startswith("Weapon") or slot == "Ring 3"):
                continue
            bases = [o for o in self.options(slot) if not o.unique]
            if bases:
                build.equip(slot, max(bases, key=lambda o: o.level))
        return problems

    def slot_for(self, option: ItemOption) -> str | None:
        for slot, spec in self.slots.items():
            if slot in self.build.locked_slots or option.type not in spec.types:
                continue
            if spec.sub_type is not None and (option.sub_type or "") != spec.sub_type:
                continue
            return slot
        return None

    def left_for(self, slot: str) -> float:
        """Divines this slot may spend: the budget minus everything else already bought."""
        current = self.build.equipped.get(slot)
        return self.ctx.budget - self.build.spent() + ((current.price or 0.0) if current else 0.0)

    def candidates(self, slot: str) -> list[ItemOption]:
        build = self.build
        current = build.equipped.get(slot)
        left = self.left_for(slot)
        others = {o.name for s, o in build.equipped.items() if s != slot and o.unique}
        return [o for o in self.options(slot)
                if (o.price or 0.0) <= left + 1e-9 and o != current and not (o.unique and o.name in others)]

    def adjusted(self, score: float, option: ItemOption | None) -> float:
        return score - self.lam * ((option.price or 0.0) if option else 0.0)

    # What-ifs and commits ----------------------------------------------------------------------

    def deltas(self, slot: str, raws: list[str]) -> list[tuple[float, dict]]:
        """Item what-ifs for one slot; a flask or charm is scored with its slot on only if a boss
        fight sustains its effect, and with the flask recovery it would bring."""
        cases = [{"slot": slot, "raw": raw} for raw in raws]
        if is_flask_slot(slot):
            for case, raw, info in zip(cases, raws, self.flask_info(raws)):
                case["active"] = self.counts(slot, info, raw)
                case["extra"] = self.recovery_stats({slot: info})
        return self.build.item_deltas(cases)

    def put(self, slot: str, option: ItemOption | None) -> float:
        """Equip (or empty) a slot; a flask or charm slot is switched on only if its effect is sustained."""
        if not is_flask_slot(slot):
            return self.build.equip(slot, option)
        info = self.flask_info([option.raw])[0] if option else {}
        self.build.extra_stats = self.recovery_stats({slot: info})
        return self.build.equip(slot, option, active=option is not None and self.counts(slot, info, option.raw))

    # Flasks and charms -----------------------------------------------------------------------

    def flask_info(self, raws: list[str]) -> list[dict]:
        missing = [raw for raw in dict.fromkeys(raws) if raw not in self._flask_info]
        if missing:
            for raw, info in zip(missing, self.build.call("flaskData", raws=missing)["results"]):
                self._flask_info[raw] = info
        return [self._flask_info[raw] for raw in raws]

    def counts(self, slot: str, info: dict, raw: str) -> bool:
        """Whether PoB may count the item's effect: generated charges keep it up in a boss fight."""
        return uptime(info, self.charges, slot_pool(slot), raw) >= ACTIVE_UPTIME

    def recovery_stats(self, replace: dict[str, dict] | None = None) -> dict:
        """Flask recovery per second (the sustain floor's flask share), with `replace`d slots."""
        replace = replace or {}
        totals = {field: 0.0 for field in RECOVERY_FIELDS.values()}
        for slot, pool in FLASK_SLOTS.items():
            if slot in replace:
                info = replace[slot]
            else:
                option = self.build.equipped.get(slot)
                info = self.flask_info([option.raw])[0] if option else None
            if info:
                totals[RECOVERY_FIELDS[pool]] += recovery_per_second(info, self.charges, pool)
        return totals

    def sync_flasks(self) -> None:
        """After the build changed elsewhere (passives, other gear): re-read charge generation, switch
        flask and charm slots on or off to match it, and refresh flask recovery."""
        build = self.build
        data = build.call("chargeGain")
        self.charges = Charges.from_bridge(data)
        changed = False
        for slot in [*FLASK_SLOTS, *CHARM_SLOTS]:
            option = build.equipped.get(slot)
            if option is not None and self.counts(slot, self.flask_info([option.raw])[0], option.raw) != data["active"].get(slot):
                build.equip(slot, option, active=not data["active"].get(slot))
                changed = True
        extra = self.recovery_stats()
        if changed or extra != build.extra_stats:
            build.extra_stats = extra
            build.refresh()

    def flask_report(self) -> list[dict]:
        """Each equipped flask and charm: uptime in a boss fight, whether its effect counted, recovery."""
        rows = []
        for slot in [*FLASK_SLOTS, *CHARM_SLOTS]:
            option = self.build.equipped.get(slot)
            if option is None:
                continue
            info, pool = self.flask_info([option.raw])[0], slot_pool(slot)
            rows.append({"slot": slot, "name": option.name, "pool": pool,
                         "uptime": uptime(info, self.charges, pool, option.raw),
                         "counted": self.counts(slot, info, option.raw),
                         "recovery": recovery_per_second(info, self.charges, pool) if pool else 0.0})
        return rows

    # Rares -----------------------------------------------------------------------------------

    def build_rare(self, slot: str, deadline: float, required: Callable[[Affix], bool] | None = None) -> ItemOption | None:
        """The best rare for this slot within its budget, affix by affix (for a flask or charm, the
        best magic item: one prefix and one suffix). With `required`, the rare starts from the best
        affix that passes it (a condition's source, builder/search/sources.py), on the best base
        that can roll one."""
        bases = [o for o in self.options(slot) if not o.unique]
        if not bases or time.perf_counter() > deadline:
            return None
        magic = is_flask_slot(slot)
        scored = self.deltas(slot, [o.raw for o in bases])
        base = None
        for _, option in sorted(zip((s for s, _ in scored), bases), key=lambda x: -x[0]):
            candidate = self.ctx.bases_by_name[option.base]
            pools = self.magic_pools[candidate["type"]] if magic else self.pools
            if required is None or any(required(a) for a in pools.pool(candidate["name"])):
                base_option, base = option, candidate
                break
        if base is None:
            return None
        limits = MAGIC_LIMIT if magic else AFFIX_LIMIT
        text, price_of = (magic_text, magic_price) if magic else (rare_text, rare_price)
        pool = pools.pool(base["name"])
        if not pool:
            return None
        left = self.left_for(slot)
        level = pools.item_level

        # Score each affix alone, keep the best few of each kind
        scored = self.deltas(slot, [text(slot, base, level, [a]) for a in pool])
        ranked = sorted(zip((s for s, _ in scored), pool), key=lambda x: -x[0])
        shortlist = []
        for kind in limits:
            shortlist += [a for _, a in ranked if a.type == kind][:SCREEN_KEEP]

        chosen: list = []
        if required is not None:
            forced = next(a for _, a in ranked if required(a))
            if price_of(slot, [forced]) > left + 1e-9 and forced.lower is not None:
                forced = forced.lower
            if price_of(slot, [forced]) > left + 1e-9:
                return None
            chosen.append(forced)
        current = self.deltas(slot, [text(slot, base, level, chosen)])[0][0]
        while time.perf_counter() < deadline:
            groups = {a.group for a in chosen}
            counts = {kind: sum(1 for a in chosen if a.type == kind) for kind in limits}
            candidates = (c for a in shortlist if a.group not in groups and counts[a.type] < limits[a.type]
                          for c in pools.choices(a, chosen))
            options = [a for a in candidates if price_of(slot, chosen + [a]) <= left + 1e-9]
            if not options:
                break
            scored = self.deltas(slot, [text(slot, base, level, chosen + [a]) for a in options])
            best_value, best = max(((s - self.lam * price_of(slot, chosen + [a]), a)
                                    for (s, _), a in zip(scored, options)), key=lambda x: x[0])
            if best_value <= current - self.lam * price_of(slot, chosen) + EPSILON:
                break
            chosen.append(best)
            current = best_value + self.lam * price_of(slot, chosen)
        if not chosen:
            return None
        price = None if self.ctx.ssf else price_of(slot, chosen)
        confidence = "ssf" if self.ctx.ssf else "estimate (magic)" if magic else "estimate (uncalibrated)"
        return ItemOption(f"{'Magic' if magic else 'Rare'} {base['type']}", base["name"], base["type"],
                          base.get("subType"), text(slot, base, level, chosen), False, price, confidence,
                          base_option.level, rare=True, affixes=tuple(line for a in chosen for line in a.lines))

    def socketed(self, slot: str, option: ItemOption, deadline: float) -> ItemOption:
        """The option with its sockets filled by the runes that score best."""
        build = self.build
        base = self.ctx.bases_by_name.get(option.base)
        count = socket_count(base, option.unique) if base else 0
        if count == 0 or time.perf_counter() > deadline:
            return option
        raw = with_runes(option.raw, [])
        left = self.left_for(slot) - (option.price or 0.0)
        names = [n for n in self.runes.candidates(base, option.unique) if (self.runes.price(n) or 0.0) <= left]
        if not names:
            return option
        scored = build.item_deltas([{"slot": slot, "raw": with_runes(raw, [n])} for n in names])
        top = [n for _, n in sorted(zip((s for s, _ in scored), names), key=lambda x: -x[0])[:RUNE_KEEP]]
        chosen: list[str] = []
        current = build.item_deltas([{"slot": slot, "raw": raw}])[0][0]
        used: dict[str, int] = {}  # per-character augment limits, across every other item
        for other_slot, other in build.equipped.items():
            if other_slot != slot:
                for name in other.runes:
                    if limit := self.runes.limit(name):
                        used[limit[0]] = used.get(limit[0], 0) + 1

        def within_limit(name: str) -> bool:
            limit = self.runes.limit(name)
            if not limit:
                return True
            taken = used.get(limit[0], 0) + sum(1 for n in chosen if (self.runes.limit(n) or ("",))[0] == limit[0])
            return taken < limit[1]

        top = [n for n in top if within_limit(n)]
        for _ in range(count):
            spend = sum(self.runes.price(n) or 0.0 for n in chosen)
            options = [n for n in top if spend + (self.runes.price(n) or 0.0) <= left + 1e-9 and within_limit(n)]
            if not options:
                break
            scored = build.item_deltas([{"slot": slot, "raw": with_runes(raw, chosen + [n])} for n in options])
            best_value, best = max(((s - self.lam * (self.runes.price(n) or 0.0), n)
                                    for (s, _), n in zip(scored, options)), key=lambda x: x[0])
            if best_value <= current + EPSILON:
                break
            chosen.append(best)
            current = best_value + self.lam * (self.runes.price(best) or 0.0)
        if not chosen:
            return option
        price = option.price if option.price is None else option.price + sum(self.runes.price(n) or 0.0 for n in chosen)
        return dataclasses.replace(option, raw=with_runes(raw, chosen), runes=tuple(chosen), price=price)

    # Rounds ----------------------------------------------------------------------------------

    def gear_round(self, deadline: float, full: bool = True) -> bool:
        """Best affordable item per slot. A full round scores every option, builds a rare and
        fills sockets, and remembers the top few per slot; other rounds rescore only those."""
        build = self.build
        improved = False
        self.sync_flasks()
        for slot in self.slots:
            if slot in build.locked_slots or time.perf_counter() > deadline:
                continue
            if slot in CHARM_SLOTS and CHARM_SLOTS.index(slot) >= self.charges.charm_limit:
                if build.equipped.get(slot) is not None:  # the belt changed: a charm PoB no longer counts
                    self.put(slot, None)
                continue
            options = self.candidates(slot)
            if not full and slot in self.shortlist:
                shortlisted = set(self.shortlist[slot])
                options = [o for o in options if o.name in shortlisted]
            if full:
                rare = self.build_rare(slot, deadline)
                if rare is not None:
                    self.rares[slot] = rare
            rare = self.rares.get(slot)
            if rare is not None and rare != build.equipped.get(slot) and (rare.price or 0.0) <= self.left_for(slot) + 1e-9:
                options.append(rare)
            scored = self.deltas(slot, [o.raw for o in options])
            current = build.equipped.get(slot)
            current_value = self.adjusted(build.score, current)
            ranked = sorted(((self.adjusted(s, o), o) for (s, _), o in zip(scored, options)), key=lambda x: -x[0])
            if full or slot not in self.shortlist:
                self.shortlist[slot] = [o.name for _, o in ranked[:SHORTLIST] if not o.rare]
            choice = None
            if ranked and ranked[0][0] > current_value + EPSILON:
                choice = ranked[0][1]
            if full:
                # Sockets, on the new choice or on what's there now
                target = choice or current
                if target is not None and not target.runes:
                    filled = self.socketed(slot, target, deadline)
                    if filled is not target:
                        choice = filled
            if choice is None:
                continue
            before, previous = build.score, current
            self.put(slot, choice)
            self.settle()
            if self.adjusted(build.score, choice) < self.adjusted(before, previous) - EPSILON and build.worse(before):
                self.put(slot, previous)
                self.settle()
                continue
            improved = improved or build.score > before + EPSILON
        if full:
            self.update_lambda()
        return improved

    def update_lambda(self) -> None:
        """Raise the budget weight while spending presses on the cap; relax it when there's room."""
        budget = self.ctx.budget
        if budget == float("inf"):
            return
        spent = self.build.spent()
        if spent > 0.9 * budget:
            self.lam = max(self.lam * 2.0, 0.02)
        elif spent < 0.5 * budget:
            self.lam = self.lam / 2.0 if self.lam > 0.005 else 0.0

    def kick(self) -> None:
        slots = [s for s in self.slots if s not in self.build.locked_slots]
        if not slots:
            return
        slot = self.rng.choice(slots)
        options = self.candidates(slot)
        if options:
            self.put(slot, self.rng.choice(options))
