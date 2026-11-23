"""Jewels (PLAN.md §5.2.4): rare and unique jewels in the passive tree's sockets.

Allocated sockets get the best jewel that scores: an affordable unique (each one once), or a rare
built affix by affix from the Jewel modifier pool on a Ruby, Emerald or Sapphire: two prefixes and
two suffixes, or (0.5 crafting) three of one kind, one of the other and the crafted line that
boosts the three (builder/search/rares.py JEWEL_CRAFTS). Full rounds also try one more socket near the tree: if no points are free, the
weakest branch makes room, the socket is filled and the leftover points regrown; the change is
kept only if the build ends better.

(JEWEL1, 23 Nov 2026) Every rare jewel is filled to its last affix slot (a filler that doesn't pay is not priced); the
tree can value a reachable socket at what a filled one adds (Tree.socket_value, behind POE2_SEARCH_JEWEL_SOCKETS); full
rounds try Zarokh's Gift anointed with its Sinister socket filled (zarokh_trial), a unique that allocates Sinister sockets
(Voices) with them filled (granting_trial), and items with jewel sockets (Tabula Rasa, three rings) with theirs filled
(item_sockets, item_socket_trial).

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
import re
import time
from collections.abc import Callable

from builder import clock
from builder.data import unique_versions
from builder.search import anoints, desecrated
from builder.search.context import ItemOption
from builder.search.radius import Variants, compact, miscalculated, tree_changing, unlocks, variants_of, with_variants
from builder.search.rares import (SCREEN_KEEP, Affix, AffixPools, active_lines, boosted, rare_price, rare_text, socket_count,
                                  unique_text, with_runes)
from builder.search.state import EPSILON, Build
from builder.search.tree import FULL_EVERY

# (COV50) the fields objective.main_dps reads, player and minion, for the radius-transform row
TRANSFORM_ROW_FIELDS = [prefix + field for prefix in ("", "Minion.") for field in (
    "CombinedDPS", "ShowAverage", "CullMultiplier", "ReservationDpsMultiplier", "TotalDPS", "AverageDamage", "TotalDotDPS")]

JEWEL_BASES = ("Ruby", "Emerald", "Sapphire")
RADIUS_BASES = ("Time-Lost Ruby", "Time-Lost Emerald", "Time-Lost Sapphire")  # rares with a radius (Small)
JEWEL_LIMIT = {"Prefix": 2, "Suffix": 2}
SOCKET_REACH = 4
# (SEARCH37; THE USER, 23 Nov, on the Oracle build's Megalomaniac budgeted at the feed's 0.10 div: "a megalomaniac with 3
# notables is pretty expensive usually ... I'm guessing a megalomaniac like that would run at least 100div"; and "an
# estimated price is fine when the utility is pricing the build at runtime, and then it can provide a trade search link
# afterward") A unique whose versions the search chooses (Jewels.version) costs, during the search: the price a Trade
# click or the versions job found for that exact version (unique_versions); else, for the uniques below, the user's
# labelled HIGH estimate (never the price feed's one any-version price: poe.ninja prices every Megalomaniac at ~0.1 div,
# the floor of junk copies, RESEARCH49 R49-10; live floors 23 Nov: Cooked 15-49 div, Marked for Sickness / Melting Flames
# a few chaos, Cooked + Marked for Sickness not listed). No trade call inside the search; the result's Trade button
# searches the exact roll (shopping.version_filters: one stat per line the version adds). Other such uniques (Against the
# Darkness) keep the feed's price, marked UNKNOWN in price_note: no data or user number yet (a question for the manager).
ROLLED_ESTIMATE = {"Megalomaniac": 100.0}
VARIANT_SCREEN = 24  # variants rescored for a unique's later selections (Megalomaniac has 874 each)
UNLOCK_TRIALS = 2  # socket and variant pairs grown for real per full round
UNLOCK_SCREENS = 12  # socket and variant pairs screened per full round (each socketed for real)
UNLOCK_POINTS = 8  # newly takeable nodes a screen counts, and points a trial may free for them
RADIUS_MODEL_ENTRY = "baryanic-leylines"  # the ledger entry for the bridge's larger Time-Lost radius model
FULL_JEWELS = True  # (JEWEL1) every rare jewel filled to its last affix slot (build_rare, greedy's `full`)
GRANTS_SOCKETS = re.compile(r"^(?:\{[^}]*\})*Allocates (\d+) Sinister Jewel sockets?", re.M | re.I)  # (JEWEL1) Voices
LAZY_JEWELS = True  # (JEWEL1, SPEED1's profile: the jewel step was 24% of task time) cached rankings + lazy greedy steps
LAZY_BATCH = 8  # affixes re-scored first in a lazy greedy step (doubling while a stale one could still win)
# (JEWEL1) Trade floors (divines) of versions the price feed lumps together, by Sinister sockets: RESEARCH52 R52-4
# (Forbidden Rites, 23 Nov 2026, instant buyout, LOW): Voices 2 sockets from 1,400 div, 3 sockets from 3 mirrors (the
# cheapest divine-priced at 14,500), 4 sockets 25-30 mirrors (taken at 25 x 4,833 div, the 3-socket's divine rate)
VERSION_FLOORS = {"Voices": {2: 1400.0, 3: 14500.0, 4: 120800.0}}
ZAROKH = "Zarokh's Gift"  # (JEWEL1) the anoint that grants a Sinister Jewel Socket (zarokh_trial)
# (JEWEL1) Items with jewel sockets ("Sockets: J ..."): PoB's slots "<item slot> Jewel Socket <n>" (ItemsTab.lua 217-262,
# on these item slots), a jewel counting while its parent item has that many jewel sockets (CalcSetup 1446-1453, scaled by
# the parent's "increased effect of Socketed Jewels"). Tabula Rasa (6), Eshtera's Path / Safrin's Resolve / Zaida's
# Longevity (1 each, one jewel type) in this game data; the tree sockets' code never filled them
ITEM_JEWEL_PARENTS = ("Weapon 1", "Weapon 2", "Helmet", "Gloves", "Body Armour", "Boots", "Belt", "Ring 1", "Ring 2",
                      "Ring 3")
ONLY_JEWEL = re.compile(r"^You can only Socket (Ruby|Emerald|Sapphire) Jewels in this item", re.I)
ITEM_SOCKET_TRIALS = 4  # (JEWEL1) jewel-socketed uniques tried for real per full round (a search budget)


def item_jewel_sockets(raw: str) -> tuple[int, tuple[str, ...]]:
    """(JEWEL1) An item's jewel sockets ("J" in its "Sockets:" line, PoB Item.lua 824) and the jewel bases they take."""
    lines = active_lines(raw)
    count = sum(line.split(":", 1)[1].split().count("J") for line in lines if line.startswith("Sockets:"))
    only = next((m.group(1) for line in lines if (m := ONLY_JEWEL.match(line.strip()))), None)
    return count, (only,) if only else JEWEL_BASES


def granted_sockets(raw: str) -> int:
    """(JEWEL1) Sinister Jewel Sockets the item's active lines allocate (PoB's variant rule: rares.active_lines)."""
    return max((int(m.group(1)) for line in active_lines(raw) if (m := GRANTS_SOCKETS.match(line))), default=0)


class Jewels:
    def __init__(self, build: Build, tree, gear) -> None:
        self.build = build
        self.ctx = build.ctx
        self.tree = tree
        self.gear = gear
        self.pools = AffixPools(self.ctx, "Jewel")
        self.rare: ItemOption | None = None  # the last rare jewel built (copies may fill several sockets)
        self.plain_rare: ItemOption | None = None  # (SEARCH34) the same for sockets that take only a plain rare
        self.restricted: dict[int, dict] = {}  # (SEARCH34) socket -> bridge.jewelSockets' entry, for those
        self._traits: dict[str, dict] = {}  # item text -> what PoB says it does to the tree
        # (unique, socket or None when the socket doesn't matter) -> (full round, the unique at its
        # best variants then): rescored once per full round
        self.versions: dict[tuple[str, str | None], tuple[int, ItemOption]] = {}
        self.full_rounds = 0
        self.left_out: dict[str, str] = {}  # unique -> why it's never proposed (for the result)
        self.screened: set[tuple[str, int, tuple[str, ...]]] = set()  # unlock screens since the last full cycle
        # (JEWEL1) the tree values a reachable jewel socket at what a filled one adds (Tree.socket_value / fill_socket,
        # behind POE2_SEARCH_JEWEL_SOCKETS); cached per full jewel round: (full round, value)
        self._socket_value: tuple[int, float] | None = None
        self.item_rares: dict[tuple[int, tuple[str, ...]], ItemOption | None] = {}
        self._ranked: dict = {}  # (JEWEL1) base (and slot for radius bases) -> (full round, affixes best first alone)  # (JEWEL1) item jewel sockets' rares
        tree.socket_value = self.socket_value
        tree.fill_socket = self.fill_new_socket
        # The larger Time-Lost radius model is on unless the request forbids its ledger entry
        build.call("radiusModel", enabled=RADIUS_MODEL_ENTRY not in self.ctx.request.forbid_ledger_entries)

    @staticmethod
    def slot(node_id: int) -> str:
        return f"Jewel {node_id}"

    def sockets(self) -> list[int]:
        """Every allocated node holding a jewel slot (bridge.jewelSockets): the tree's sockets, an ascendancy notable's
        (Lich's Crystalline Phylactery) and the Sinister Jewel Sockets granted passives allocate (Zarokh's Gift). Only
        type "Socket" nodes of the tree search's own list were taken before, so the last two were never filled (SEARCH34;
        PLANS6 kw-basic-jewel, an UNDERCOUNT for Lich builds)."""
        found = self.build.call("jewelSockets")["sockets"]
        self.restricted = {s["id"]: s for s in found if s["basicOnly"] or s["nonUnique"] or s["noRadius"]}
        return [s["id"] for s in found]

    def plain_only(self, socket: int) -> bool:
        """A socket that takes no unique (Phylactery: basic non-unique jewels only, PoB ItemsTab.lua 2654-2659; a Sinister
        socket: non-unique, 2651) or gives a jewel no radius (noRadius): only a plain rare (JEWEL_BASES) goes in."""
        return socket in self.restricted

    def sync(self) -> list[int]:
        """Forget jewels whose socket the tree no longer has; returns the allocated sockets."""
        sockets = self.sockets()
        live = {self.slot(i) for i in sockets} | {slot for slot, _ in self.item_sockets()}
        for slot in [s for s in self.build.equipped
                     if (s.startswith("Jewel ") or " Jewel Socket " in s) and s not in live]:
            self.build.equip(slot, None)
        return sockets

    def item_sockets(self) -> list[tuple[str, tuple[str, ...]]]:
        """(JEWEL1) Every jewel socket the equipped items have: (PoB slot name, the jewel bases it takes)."""
        found = []
        for parent in ITEM_JEWEL_PARENTS:
            option = self.build.equipped.get(parent)
            if option is None or "J" not in option.raw:
                continue
            count, bases = item_jewel_sockets(option.raw)
            found += [(f"{parent} Jewel Socket {i}", bases) for i in range(1, count + 1)]
        return found

    def fill_item_socket(self, slot: str, bases: tuple[str, ...], deadline: float) -> bool:
        """(JEWEL1) The best plain rare for an item's jewel socket (built once per full round for each set of bases), in
        if it beats what is there. Uniques are not tried there (UNVERIFIED whether the game takes them: the lower reading)."""
        build, gear = self.build, self.gear
        key = (self.full_rounds, bases)
        if key not in self.item_rares:
            self.item_rares[key] = self.build_rare(slot, deadline, bases=bases)
        rare, current = self.item_rares[key], build.equipped.get(slot)
        if rare is None or rare == current or (rare.price or 0.0) > gear.left_for(slot) + 1e-9:
            return False
        score = build.item_deltas([{"slot": slot, "raw": rare.raw}])[0][0]
        if gear.adjusted(score, rare) <= gear.adjusted(build.score, current) + EPSILON:
            return False
        before = build.score
        build.equip(slot, rare)
        gear.settle()
        if build.worse(before):
            build.equip(slot, current)
            gear.settle()
            return False
        return True

    def item_socket_trial(self, deadline: float) -> bool:
        """(JEWEL1) A unique with jewel sockets (Tabula Rasa's six, the three one-socket rings) scores only its own lines
        in gear's what-if while its sockets are empty, so the jewels it could hold never counted. Here the best few by
        that what-if go into their slot (a ring into each ring slot), their sockets are filled with plain rares, and one
        is kept only if score less the budget weight x spend rises."""
        build, gear = self.build, self.gear
        trials = []
        for parent in ITEM_JEWEL_PARENTS:
            current = build.equipped.get(parent)
            if current is None and parent not in ("Body Armour", "Ring 1", "Ring 2"):
                continue
            for option in gear.candidates(parent):
                if not option.unique or "J" not in option.raw:
                    continue
                if item_jewel_sockets(option.raw)[0] == 0 or (option.price or 0.0) > gear.left_for(parent) + 1e-9:
                    continue
                trials.append((parent, option))
        if not trials:
            return False
        scored = build.item_deltas([{"slot": parent, "raw": option.raw} for parent, option in trials])
        ranked = sorted(zip((s for s, _ in scored), range(len(trials))), key=lambda x: -x[0])[:ITEM_SOCKET_TRIALS]
        for _, i in ranked:
            if clock.now() > deadline:
                break
            parent, option = trials[i]
            snap, before = build.snapshot(), build.score - gear.lam * build.spent()
            build.equip(parent, option)
            gear.settle()
            for slot, bases in self.item_sockets():
                if slot.startswith(parent + " "):
                    self.fill_item_socket(slot, bases, deadline)
            if build.score - gear.lam * build.spent() > before + EPSILON:
                return True
            build.restore(snap)
        return False

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
        return self.rolled_price(self.versions[key][1], variants)

    def rolled_price(self, option: ItemOption, variants: Variants) -> ItemOption:
        """(SEARCH37, ROLLED_ESTIMATE) This version's price for the search: its exact trade price if one was found, else
        the user's estimate for the unique, else the feed's any-version price marked UNKNOWN."""
        if self.ctx.ssf:
            return option
        chosen = option.variants or variants.label(variants.selected)
        priced = unique_versions.lookup(self.ctx.unique_versions, option.name, chosen)
        if priced is not None and priced.get("listed") and priced.get("price") is not None:
            day = time.strftime("%Y-%m-%d", time.localtime(priced["priced_at"]))
            return dataclasses.replace(option, price=priced["price"],
                                       confidence=f"trade, this version ({priced['listed']} listed, {day})")
        feed = option.price or 0.0
        estimate = ROLLED_ESTIMATE.get(option.name)
        if estimate is None:
            return dataclasses.replace(option, price_note=(
                f"price UNKNOWN for this version ({', '.join(chosen)}): the price feed's {feed:g} div is any version's "
                "floor; the Trade button prices this one"))
        searched = "not listed when last searched" if priced is not None else "not searched yet"
        return dataclasses.replace(option, price=max(estimate, feed), confidence="estimate (HIGH)", price_note=(
            f"estimate: at least {estimate:g} div for a {option.name} with {', '.join(chosen)} (the user's estimate, 23 "
            f"Nov; this version {searched}; the price feed's {feed:g} div is any copy's floor); the Trade button "
            "searches this exact roll"))

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
            if clock.now() > deadline:
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
            if not pool or clock.now() > deadline:
                continue
            ranked = self.ranked(slot, base, pool)
            forced = None
            if required is not None:
                forced = next((a for a in ranked if required(a)), None)
                if forced is None:
                    continue
            # (JEWEL1) Every jewel comes out whole: each shape is filled to its last slot (greedy's `full`), never stopped
            # where the next affix stops paying (the Oracle build of 22 Nov had two two-mod Emeralds). Plain jewels also
            # try the 0.5 craft on both sides (the user, 24 Oct / 23 Nov 2026: 4 chosen mods + the effect line on the side
            # that pays more); the plain two-and-two shape stays a candidate (cheaper under a budget, and the Preserved
            # Cranium trial builds on it). Time-Lost bases keep the plain shape (no craft named for them: UNVERIFIED)
            shapes = [(JEWEL_LIMIT, [forced] if forced else [], None)]
            for kind in JEWEL_LIMIT if base_name in JEWEL_BASES else ():
                if crafted := self.pools.crafted(kind):
                    other = next(k for k in JEWEL_LIMIT if k != kind)
                    shapes.append(({kind: 3, other: 1}, [crafted[0]] + ([forced] if forced else []), (kind, crafted[1])))
            for limits, fixed, boost in shapes:
                shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
                if boost:
                    shortlist = [dataclasses.replace(a, lines=tuple(boosted(l, boost[1]) for l in a.lines))
                                 if a.type == boost[0] else a for a in shortlist]
                    fixed = [dataclasses.replace(a, lines=tuple(boosted(l, boost[1]) for l in a.lines))
                             if a.type == boost[0] and not a.crafted else a for a in fixed]
                found = self.greedy(slot, base, shortlist, limits, fixed, left, deadline, required is not None,
                                    full=FULL_JEWELS)
                if found and (best is None or found[0] > best[0]):
                    value, chosen, priced = found
                    price = None if self.ctx.ssf else rare_price("Jewel", priced)
                    best = (value, ItemOption(
                        "Rare Jewel", base_name, "Jewel", None, rare_text("Jewel", base, level, chosen), False, price,
                        "ssf" if self.ctx.ssf else "estimate (uncalibrated)", 0, rare=True,
                        affixes=tuple(line for a in chosen for line in a.lines)))
                    if required is None and not boost and desecrated.cranium_enabled():
                        crafted = self.cranium_trial(slot, base, chosen, value, left, deadline)
                        if crafted is not None and crafted[0] > best[0]:
                            best = crafted
        return best[1] if best else None

    def ranked(self, slot: str, base: dict, pool: list) -> list:
        """The base's affixes best first, each scored alone in this slot. (JEWEL1, LAZY_JEWELS) Kept for the full jewel
        round: a plain jewel's ranking doesn't depend on the socket (no radius), a radius base's is kept per socket; the
        greedy re-scores every pick on the live build, so a slightly stale ranking only orders the shortlist."""
        key = base["name"] if base["name"] in JEWEL_BASES else (base["name"], slot)
        cached = self._ranked.get(key)
        if LAZY_JEWELS and cached is not None and cached[0] == self.full_rounds:
            return cached[1]
        level = self.pools.item_level
        scored = self.build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, [a])} for a in pool])
        ranked = [a for _, a in sorted(zip((s for s, _ in scored), pool), key=lambda x: -x[0])]
        self._ranked[key] = (self.full_rounds, ranked)
        return ranked

    CRANIUM_KEEP = 3  # jewel Desecrated affixes (by a screen what-if) fully tried (SEARCH31; a search budget)

    @property
    def cranium_pools(self) -> AffixPools:
        if self.__dict__.get("_cranium_pools") is None:
            self._cranium_pools = AffixPools(self.ctx, "Desecrated", "cranium")
        return self._cranium_pools

    def cranium_trial(self, slot: str, base: dict, chosen: list, value: float, left: float,
                      deadline: float) -> tuple[float, ItemOption] | None:
        """(SEARCH31) The plain-shape rare jewel `chosen` (score less price's weight: `value`) with one Desecrated jewel
        affix by a Preserved Cranium (desecrated.py "cranium"; RESEARCH34 R33-22, MEDIUM-HIGH), added where its side has
        room or in place of each affix of its side (a full side loses a random mod: the jewel is bought with that slot
        open), priced by the craft route; (value, option) if it beats `value`, else None."""
        build, lam, level, name = self.build, self.gear.lam, self.pools.item_level, base["name"]
        pool = self.cranium_pools.pool(name)
        groups = {a.group for a in chosen}
        fresh = [a for a in pool if a.group not in groups and a.type in JEWEL_LIMIT]
        if not fresh or clock.now() > deadline:
            return None
        ssf = self.ctx.ssf
        prices = self.ctx.desecration_prices if not ssf else {m: 0.0 for m in desecrated.MATERIALS}
        ordinary = {}  # the reveal's ordinary rows per side (equal weights, as gear.desecrated_trial)
        for a in self.pools.pool(name):
            if a.side_odds > 0:
                ordinary[a.type] = round(1 / a.side_odds)
        screen = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen + [a])} for a in fresh])
        top = [a for s, a in sorted(((s, a) for (s, _), a in zip(screen, fresh)), key=lambda x: -x[0])][:self.CRANIUM_KEEP]
        trials = []  # (affixes, the Desecrated affix, price, steps)
        for a in top:
            for c in self.cranium_pools.choices(a, chosen):
                if sum(1 for x in chosen if x.type == c.type) < JEWEL_LIMIT[c.type]:
                    variants = [list(chosen)]
                else:
                    variants = [[x for x in chosen if x is not out] for out in chosen if out.type == c.type]
                for kept in variants:
                    held = {x.group for x in kept}
                    n_side = sum(1 for x in pool if x.type == c.type and x.group not in held)
                    market = rare_price("Jewel", kept)
                    price, steps = desecrated.craft_price("Jewel", market, c, 0, n_side, ordinary.get(c.type, 0),
                                                          prices, "cranium")
                    if price is not None and price <= left + 1e-9:
                        trials.append((kept + [c], c, price, steps))
        if not trials or clock.now() > deadline:
            return None
        scored = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, t[0])} for t in trials])
        found, i = max(((s - lam * t[2], i) for i, ((s, _), t) in enumerate(zip(scored, trials))), key=lambda x: x[0])
        if found <= value + EPSILON:
            return None
        affixes, affix, price, steps = trials[i]
        note = (f"one Desecrated modifier ({affix.desecrated}, {affix.type}) by a Preserved Cranium; priced by the cheapest "
                f"repeat-until-hit craft route (LOW: no Omen names a jewel row; desecrated.py)")
        return found, ItemOption("Rare Jewel", name, "Jewel", None, rare_text("Jewel", base, level, affixes), False,
                                 None if ssf else price, "ssf" if ssf else "estimate (craft route: Desecration)", 0,
                                 rare=True, affixes=tuple(line for a in affixes for line in a.lines),
                                 craft_route=(note, *steps))

    def greedy(self, slot: str, base: dict, shortlist: list, limits: dict[str, int], fixed: list, left: float,
               deadline: float, fixed_only: bool = False, full: bool = False) -> tuple[float, list, list] | None:
        """Add affixes to `fixed` while one pays: (score less the price's weight, affixes, the affixes priced), or None
        when nothing beyond `fixed` fits the budget or pays (unless `fixed_only` allows that). With `full` (JEWEL1), go on
        to the shape's last slot even when no affix pays any more (the user, 23 Nov 2026: "having empty mods on an item is
        stupid and wasteful"; the manager 06:15: "fill all slots ... with the best-value mods, priced"): each further slot
        takes the affordable affix with the best score less its price's weight, priced like the rest.
        """
        build, lam, level = self.build, self.gear.lam, self.pools.item_level
        chosen, priced = list(fixed), list(fixed)
        if rare_price("Jewel", chosen) > left + 1e-9:
            return None
        current = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen)}])[0][0]
        current -= lam * rare_price("Jewel", priced)
        gains: dict = {}  # affix -> its gain when last scored (lazy steps)
        while clock.now() < deadline:
            groups = {a.group for a in chosen}
            counts = {k: sum(1 for a in chosen if a.type == k and not a.crafted) for k in limits}
            candidates = [c for a in shortlist if a.group not in groups and counts[a.type] < limits[a.type]
                          for c in self.pools.choices(a, chosen)]
            options = [a for a in candidates if rare_price("Jewel", priced + [a]) <= left + 1e-9]
            if not options:
                break  # (only the budget stops a full jewel)
            value, affix = self.best_step(slot, base, chosen, priced, options, current, gains)
            if value > current + EPSILON:
                chosen.append(affix)
                priced.append(affix)
                current = value
                continue
            if not full:
                break
            # a filler: the best value left, priced (it costs more than it adds, the least of the affordable ones)
            chosen.append(affix)
            priced.append(affix)
            current = value
        return (current, chosen, priced) if len(chosen) > len(fixed) or (fixed_only and fixed) else None

    def best_step(self, slot: str, base: dict, chosen: list, priced: list, options: list, current: float,
                  gains: dict) -> tuple[float, Affix]:
        """(value, affix) of the best next affix: score with it less the price's weight. (JEWEL1, LAZY_JEWELS) Lazy, as
        the tree's growth: the affixes whose last gain was highest are scored first, LAZY_BATCH at a time (doubling),
        until the best fresh value beats every unscored affix's last gain (gains shrink as a jewel fills: increased lines
        add, so the next copy of a stat is worth no more than the last). Affixes never scored go in the first batch."""
        build, lam, level = self.build, self.gear.lam, self.pools.item_level

        def score(batch: list) -> list[tuple[float, Affix]]:
            scored = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen + [a])}
                                        for a in batch])
            found = [(s - lam * rare_price("Jewel", priced + [a]), a) for (s, _), a in zip(scored, batch)]
            for value, a in found:
                gains[a] = value - current
            return found

        if not LAZY_JEWELS:
            return max(score(options), key=lambda x: x[0])
        fresh = [a for a in options if a not in gains]
        stale = sorted((a for a in options if a in gains), key=lambda a: -gains[a])
        batch = LAZY_BATCH
        found = score(fresh + stale[:batch])
        stale = stale[batch:]
        while stale and max(v for v, _ in found) - current < gains[stale[0]]:
            batch *= 2
            found += score(stale[:batch])
            stale = stale[batch:]
        return max(found, key=lambda x: x[0])

    def fill(self, socket: int, deadline: float, rebuild: bool) -> bool:
        build = self.build
        slot = self.slot(socket)
        current = build.equipped.get(slot)
        if self.plain_only(socket):
            # (SEARCH34) a plain rare built for this socket: no unique, no radius base
            if rebuild or self.plain_rare is None:
                rare = self.build_rare(slot, deadline, bases=JEWEL_BASES)
                if rare is not None:
                    self.plain_rare = rare
            options, rare = [], self.plain_rare
        else:
            if rebuild or self.rare is None:
                rare = self.build_rare(slot, deadline)
                if rare is not None:
                    self.rare = rare
            left = self.gear.left_for(slot)  # (SEARCH37: a chosen version's own price, ROLLED_ESTIMATE, may not fit)
            options = [v for v in (self.version(slot, o, deadline) for o in self.uniques(slot))
                       if (v.price or 0.0) <= left + 1e-9]
            options, rare = [o for o in options if o != current], self.rare
        if rare is not None and rare != current and (rare.price or 0.0) <= self.gear.left_for(slot) + 1e-9:
            options.append(rare)
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
        """The larger Time-Lost radius and the radius damage transforms, for the result's assumptions (asterisked)."""
        notes = self.transform_notes()
        model = self.build.call("radiusModel")
        if model["increase"] <= 0:
            return notes
        allocated = {n["id"] for n in self.build.call("facts", fields=[])["passives"]}
        source = ", ".join(p["name"] for p in model["passives"] if p["id"] in allocated)
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

    def transform_notes(self) -> list[dict]:
        """(COV49) A socketed jewel's "Increases and Reductions to <A> and <B> Damage in Radius are transformed to apply
        to <C> Damage" (engine/bridge.lua applyRadiusTransforms: PoB parses none of it)."""
        notes = []
        for jewel in self.build.call("radiusJewels")["jewels"]:
            for line in jewel.get("transforms") or []:
                notes.append({
                    "label": f"{jewel['name']} (Jewel {jewel['socket']}): {line.strip()} (custom model)",
                    "value": len(jewel.get("affected") or []),
                    "basis": "PoB parses none of the line; the allocated passives in the radius (the value) have those two "
                             "types' Damage increases and reductions moved to the third. UNVERIFIED readings (RESEARCH37 "
                             "R37-15 kept all three): 'Elemental Damage' lines stay, MEDIUM-LOW (the same result for a "
                             "build dealing only the third type; only a mixed-element build would differ); the jewels' "
                             "'also grant' lines on those passives move too, MEDIUM-LOW (the passives' own stats only: "
                             "the row below); two transforms on one passive move each increase once, MEDIUM (a move, "
                             "not a copy: nothing counts twice)",
                    "confidence": "theory"})
        if notes:
            notes.append(self.transform_base_only_row())
        return notes

    def transform_base_only_row(self) -> dict:
        """(COV50, R37-15 (b)) The row for the lower reading of the radius damage transforms: only the passives' own
        stats move, not the Time-Lost jewels' "also grant" lines on them (bridge.radiusTransformBaseOnly)."""
        from builder.search.objective import main_dps

        now = main_dps(self.build.call("stats", fields=TRANSFORM_ROW_FIELDS))
        base = main_dps(self.build.call("radiusTransformBaseOnly", fields=TRANSFORM_ROW_FIELDS)["stats"] or {})
        ratio = base / now if now > 0 else 1.0
        return {
            "label": "Radius damage transforms, row: the passives' own stats only (custom model)",
            "value": f"x{ratio:.3f}",
            "basis": (f"RESEARCH37 R37-15 (b)'s lower reading: the Time-Lost jewels' 'also grant' lines on the passives "
                      f"in radius keep their own type; PoB's main-skill DPS {base:,.1f} against {now:,.1f} with them "
                      "moved (the headline's reading, MEDIUM-LOW either way)"),
            "confidence": "theory"}

    def socket_value(self) -> float:
        """(JEWEL1) What one more filled jewel socket adds to the score: the most a rare jewel the build holds gives in its
        socket (the build with it, less the build with that socket's jewel stripped to a bare base), or, with none
        socketed yet, what the last rare built gives in an empty allocated socket; 0 with neither (no data: the tree then
        values sockets by their travel nodes only, as before). The last copy's worth stands for the next copy's (copies of
        "increased" lines add, so the next one is worth about as much: MEDIUM). Cached for each full jewel round."""
        if self._socket_value is not None and self._socket_value[0] == self.full_rounds:
            return self._socket_value[1]
        build, level = self.build, self.pools.item_level
        cases, empty = [], []
        for socket in self.sockets():
            slot = self.slot(socket)
            held = build.equipped.get(slot)
            if held is not None and held.rare and held.base in JEWEL_BASES:
                cases.append({"slot": slot, "raw": rare_text("Jewel", self.ctx.bases_by_name[held.base], level, [])})
            elif held is None and not self.plain_only(socket):
                empty.append(slot)
        value = 0.0
        if cases:
            value = max(0.0, build.score - min(score for score, _ in build.item_deltas(cases)))
        elif empty and self.rare is not None:
            value = max(0.0, build.item_deltas([{"slot": empty[0], "raw": self.rare.raw}])[0][0] - build.score)
        self._socket_value = (self.full_rounds, value)
        return value

    def fill_new_socket(self, socket: int, deadline: float) -> bool:
        """(JEWEL1) Fill a socket the tree just took for the jewel it would hold (Tree.grow)."""
        return self.fill(socket, deadline, rebuild=self.rare is None)

    def holds(self, socket: int) -> bool:
        """Whether the tree holds nodes only through this socket's jewel."""
        return any(j["socket"] == socket and j["unconnected"] for j in self.build.call("radiusJewels")["jewels"])

    def jewel_round(self, deadline: float, full: bool = True) -> bool:
        improved = False
        self.full_rounds += 1 if full else 0
        sockets = self.sync()
        for index, socket in enumerate(sockets):
            if clock.now() > deadline:
                break
            improved = self.fill(socket, deadline, rebuild=full and index == 0) or improved
        for slot, bases in self.item_sockets():
            if clock.now() > deadline:
                break
            improved = self.fill_item_socket(slot, bases, deadline) or improved
        if full and clock.now() < deadline:
            improved = self.item_socket_trial(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.try_new_socket(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.unlock_round(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.radius_trial(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.zarokh_trial(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.granting_trial(deadline) or improved
        return improved

    def shard_helmet(self, slot: str, option: ItemOption) -> ItemOption | None:
        """(JEWEL1) The helmet with a Raven-Touched Shard in a free socket or in place of its last rune, so it takes an
        anoint (anoints.capacity; RESEARCH52 R52-6: 9 of the user's 14 example builds with Zarokh's Gift anoint it on a
        helmet, 17 of 20 helmets carry the shard). Built as Gear.shard_trial builds it (its rules: no Corrupted /
        Sanctified helmet, a socket-bound rune never replaced, one shard per character, the rune catalog's price), or
        None when the helmet can't take one."""
        gear, ctx = self.gear, self.ctx
        if anoints.SHARD in option.runes or anoints.SEALED.search(option.raw):
            return None
        base = ctx.bases_by_name.get(option.base)
        count = socket_count(base, option.unique, unique_text(ctx, option)) if base else 0
        if count == 0 or anoints.SHARD not in gear.runes.candidates(base, option.unique, option.raw):
            return None
        limit = gear.runes.limit(anoints.SHARD)
        if limit and sum(1 for s, o in self.build.equipped.items() if s != slot and o is not None
                         for n in o.runes if (gear.runes.limit(n) or ("",))[0] == limit[0]) >= limit[1]:
            return None
        runes = list(option.runes)
        dropped = runes[count - 1:] if len(runes) >= count else []
        if any(n in option.bound for n in dropped):
            return None
        new = runes[:count - 1] + [anoints.SHARD] if dropped else runes + [anoints.SHARD]
        price = option.price
        if price is not None:
            price += (gear.runes.price(anoints.SHARD) or 0.0) - sum(gear.runes.price(n) or 0.0 for n in dropped)
        return dataclasses.replace(option, raw=with_runes(option.raw, new), runes=tuple(new), price=price)

    def version_floor(self, version: ItemOption) -> ItemOption:
        """(JEWEL1) A version the price feed can't tell apart priced at its own trade floor where one is known: Voices'
        3- and 4-socket copies cost far more than the feed's one price (its cheapest, the 2-socket). VERSION_FLOORS,
        RESEARCH52 R52-4 (trade, Forbidden Rites, 23 Nov, LOW). A version the Price unique versions job priced keeps that."""
        floors = VERSION_FLOORS.get(version.name)
        if self.ctx.ssf or not floors or (version.confidence or "").startswith("trade, this version"):
            return version
        floor = floors.get(granted_sockets(version.raw))
        if floor is None or (version.price or 0.0) >= floor:
            return version
        return dataclasses.replace(version, price=floor, confidence="low (this version's trade floor, RESEARCH52 23 Nov)")

    def weakest_socket(self) -> int | None:
        """(JEWEL1) The allocated socket that takes a unique and whose jewel gives least (an empty one first)."""
        build, level = self.build, self.pools.item_level
        open_ = [s for s in self.sockets() if not self.plain_only(s)]
        empty = [s for s in open_ if build.equipped.get(self.slot(s)) is None]
        if empty:
            return empty[0]
        held = [(s, build.equipped[self.slot(s)]) for s in open_]
        held = [(s, o) for s, o in held if o.rare and o.base in self.ctx.bases_by_name]
        if not held:
            return None
        scored = build.item_deltas([{"slot": self.slot(s), "raw": rare_text("Jewel", self.ctx.bases_by_name[o.base],
                                                                                   level, [])} for s, o in held])
        return max(zip((score for score, _ in scored), (s for s, _ in held)), key=lambda x: x[0])[1]

    def granting_trial(self, deadline: float) -> bool:
        """(JEWEL1) A unique jewel that allocates Sinister Jewel Sockets (Voices: "Allocates 2 / 3 / 4 Sinister Jewel
        sockets", a Sapphire, Limited to 1, Corrupted; PoB grants them, ModParser 5964 / PassiveSpec 1142-1166) is worth
        nothing in fill's what-if while those sockets are empty, so it was never chosen. Here each version goes into the
        weakest socket (empty first), its sockets are filled with plain rares (fill), and it is kept only if score less the
        budget weight x spend rises. The price feed has one price for every version (the cheapest listed): a 3- or
        4-socket copy costs more (LOW; a version the Price unique versions job priced costs its trade price)."""
        build, gear = self.build, self.gear
        socket = self.weakest_socket()
        if socket is None:
            return False
        slot = self.slot(socket)
        options = [o for o in self.uniques(slot) if GRANTS_SOCKETS.search(o.raw)]
        versions = [self.version_floor(v) for o in options for v in self.unlock_versions(o)]
        current = build.equipped.get(slot)
        versions = [v for v in versions if v != current and (v.price or 0.0) <= gear.left_for(slot) + 1e-9]
        # most sockets first: a version that adds more gives more, so the first kept is the best affordable one
        versions.sort(key=lambda v: -granted_sockets(v.raw))
        for version in versions:
            if clock.now() > deadline:
                break
            snap, before = build.snapshot(), build.score - gear.lam * build.spent()
            build.equip(slot, version)
            gear.settle()
            granted = [s for s in self.sync() if self.plain_only(s) and build.equipped.get(self.slot(s)) is None]
            for s in granted:
                self.fill(s, deadline, rebuild=self.plain_rare is None)
            if granted and build.score - gear.lam * build.spent() > before + EPSILON:
                return True
            build.restore(snap)
        return False

    # Zarokh's Gift (JEWEL1) -----------------------------------------------------------------------

    def zarokh_trial(self, deadline: float) -> bool:
        """(JEWEL1) Anoint Zarokh's Gift and fill the Sinister Jewel Socket it grants (the user, 23 Nov 2026: top builds
        take "a Voices sapphire and zarokh's gift anointed"). Gear's anoint trial scores the anoint with the socket empty
        (worth nothing), so it never chose it (anoints.LEFT_OUT before JEWEL1). Here, on each item that takes anoints, it
        goes into a free anoint place or in place of each anoint the item has; the socket it grants (PoB tree 0_5 node
        11184: isJewelSocket, sinister, noRadius; a non-unique jewel only, ItemsTab 2651) is filled with a plain rare
        (fill); kept only if the build ends better, the emotions' price counted. Once the anoint is on, gear's own anoint
        trial scores it with the jewel in place (PoB counts a jewel only while its socket is allocated, CalcSetup 1264),
        so later rounds keep or drop it on its real worth."""
        build, gear = self.build, self.gear
        recipes = gear.anoint_recipes()
        if ZAROKH not in recipes or ZAROKH in self.ctx.compiled.filters.forbidden_passives:
            return False
        if any(ZAROKH in o.anoints for o in build.equipped.values() if o is not None):
            return False
        cost = gear.anoint_cost(ZAROKH)
        trials = []  # (slot, the item with Zarokh's Gift anointed)
        for slot, option in list(build.equipped.items()):
            if option is None or slot.startswith("Jewel ") or " Jewel Socket " in slot:
                continue
            if slot == "Helmet" and anoints.capacity(option) == 0:
                option = self.shard_helmet(slot, option)  # (a Raven-Touched Shard makes it anointable)
                if option is None:
                    continue
            cap = anoints.capacity(option)
            if cap == 0:
                continue
            held = list(option.anoints)
            sets = [held + [ZAROKH]] if len(held) < cap else [held[:i] + [ZAROKH] + held[i + 1:] for i in range(len(held))]
            for names in sets:
                price = option.price
                if price is not None:
                    price += cost - sum(gear.anoint_cost(n) for n in held if n not in names)
                if (price or 0.0) > gear.left_for(slot) + 1e-9:
                    continue
                trials.append((slot, dataclasses.replace(option, raw=anoints.with_anoints(option.raw, names),
                                                         anoints=tuple(names), price=price)))
        improved = False
        for slot, option in trials:
            if clock.now() > deadline:
                break
            snap, before = build.snapshot(), build.score - gear.lam * build.spent()
            build.equip(slot, option)
            gear.settle()
            granted = [s for s in self.sync() if self.plain_only(s) and build.equipped.get(self.slot(s)) is None]
            for socket in granted:
                self.fill(socket, deadline, rebuild=self.plain_rare is None)
            if granted and build.score - gear.lam * build.spent() > before + EPSILON:
                improved = True
                break
            build.restore(snap)
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
        for socket in [s for s in sockets if not self.plain_only(s)]:  # (no radius jewel where it can't go or reach)
            if clock.now() > deadline:
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
        # socket -> the path it still needs (not a socket that takes no unique: plain_only)
        paths = {s: () for s in self.sync() if not self.holds(s) and not self.plain_only(s)}
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
                 for version in self.unlock_versions(option)
                 if (version.price or 0.0) <= self.gear.left_for(self.slot(socket)) + 1e-9]
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
        # (the screen opens a socket's path and takes nodes past a full tree: what try_unlock pays for with points it
        # frees first, so the screen's scores leave points over the budget out; SEARCH8)
        build.objective.points_slack = float("inf")
        try:
            self._screen_unlocks(batch, paths, snap, before, screened, deadline)
        finally:
            build.objective.points_slack = 0.0
        build.restore(snap)
        screened.sort(key=lambda x: -x[0])
        for potential, socket, version, wanted in screened[:UNLOCK_TRIALS]:
            if potential <= EPSILON or clock.now() > deadline:
                break
            if self.try_unlock(socket, version, wanted, paths[socket], deadline):
                return True
        return False

    def _screen_unlocks(self, batch: list, paths: dict, snap: dict, before: float, screened: list,
                        deadline: float) -> None:
        """unlock_round's screen: each (socket, version) socketed for real, its newly takeable nodes scored."""
        build, tree, forbidden = self.build, self.tree, self.tree.forbidden
        base, opened_socket = before, None
        for socket, version in batch:
            if clock.now() > deadline:
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

    def unlock_versions(self, option: ItemOption) -> list[ItemOption]:
        """Each variant a real item can have (a ring size, a keystone), or the jewel as it is. A
        version the Price unique versions job priced (builder/report/version_prices.py) costs its
        trade price, and one nobody lists isn't offered; the others cost the feed's price for any
        version, with low confidence."""
        variants = variants_of(option.raw)
        if variants is None or len(variants.slots) != 1:
            return [option]
        found = []
        for i in variants.usable:
            label = variants.label((i,))
            version = dataclasses.replace(option, raw=with_variants(option.raw, variants, (i,)), variants=label,
                                          confidence="low", price_note=(  # (SEARCH37: never silently the feed's price)
                                              f"price UNKNOWN for this version ({', '.join(label)}): the price feed's "
                                              f"{option.price or 0.0:g} div is any version's floor; the Price unique "
                                              "versions job or the Trade button prices this one"))
            priced = None if self.ctx.ssf else unique_versions.lookup(self.ctx.unique_versions, option.name, label)
            if priced is not None and not priced["listed"]:
                continue
            if priced is not None and priced.get("price") is not None:
                day = time.strftime("%Y-%m-%d", time.localtime(priced["priced_at"]))
                version = dataclasses.replace(version, price=priced["price"], price_note="",
                                              confidence=f"trade, this version ({priced['listed']} listed, {day})")
            found.append(version)
        return found

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
