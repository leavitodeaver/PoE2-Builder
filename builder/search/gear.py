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
import math
import os
import random
import re
from collections.abc import Callable
from dataclasses import dataclass

from builder import clock
from builder.search import alloys, anoints, cultivated, desecrated, essences, influence
from builder.search.context import ARMOUR_SLOTS, JEWELLERY_SLOTS, SLOT_TYPE, ItemOption, implicit_variant, implicit_variants
from builder.search.flasks import (
    CHARM_SLOTS, FLASK_SLOTS, RECOVERY_FIELDS, Charges, charges_per_second, is_flask_slot,
    recovery_per_second, slot_pool,
    uptime,
)
from builder.search.rares import (
    MAGIC_LIMIT, RUNE_KEEP, SCREEN_KEEP, Affix, AffixPools, RuneCatalog, magic_price, magic_text, rare_price, rare_text,
    socket_count, unique_text, with_runes,
)
from builder.search.state import EPSILON, Build
from builder.search.conditions import FULL_UPTIME, SWITCH_ON

# A flask or charm slot is switched on from SWITCH_ON (7%) of a boss fight, the conditions' number (the user, 9 Nov:
# "if something isn't active at least 7% of the time, it's basically worthless"; AUDIT19's A19-7), and counts whole
# only from FULL_UPTIME (99.5%), like every other part-time effect; between the two it counts at its share (part_time,
# Search.weigh_uptimes; floors are checked with it off). Before, it counted whole from flasks.ACTIVE_UPTIME (90%):
# an overcount of up to x1.11 on what it adds, and a 90% charm met a defence floor (AUDIT19's A19-1, WRONG). One
# number each, no stay-on band: the conditions' 5% stay-on band needs the slot's previous state, which isn't kept here
FLASK_ON = SWITCH_ON
FLASK_WHOLE = FULL_UPTIME
from builder.search.models import GRANTED_FILLED

# A unique's line granting a meta skill with an Energy model (Mjolner: "Grants Skill: Level (1-20) Thundergod's Wrath"),
# or Forgotten Warden's Spirit Vessel
GRANTS_META = re.compile(r"Grants Skill: (?:Level \S+ )?(?:%s)\s*$" % "|".join(re.escape(n) for n in GRANTED_FILLED), re.M)

BASES_PER_SUBTYPE = 3  # normal armour/jewellery bases kept per sub-type, highest level first
SHORTLIST = 6  # options per slot rescored between full rounds
ANOINT_KEEP = 8  # notables per anoint the node screen passes to real item what-ifs (anointed)
TRIAL_SHARE = 0.5  # of a fresh full round's time the source-item trials may take (SEARCH21: they took all of it)
REQUIREMENT_FIT_SECONDS = 30.0  # tree re-routing a granted-meta unique's trial may take (fit_requirements)
# On since 11 Nov (the user: "Turn on, fix the search"; the variants are real items, so the search offers them). Off from
# 8 Nov (MECH15) to 11 Nov: then every 2b75b gate run kept Cast on Dodge with 2-3 supports left in its main group,
# caused by e4767b1's "each support once per character" rule, which 58f03f3 (9 Nov) dropped. GEAR4's on/off gates
# (11 Nov): 2b75b x7.15 on vs x5.86 off, 2b75e x22.38 vs x17.05, 2b75d x1.48 on vs x1.77/x2.24 off; 2b75d's x1.48 is
# also what its off-runs reach since 14571fb (MECH40: the round-2 support basin), so its loss isn't the trials alone.
# POE2_VARIANT_TRIALS=0 turns them off for a run.
VARIANT_META_TRIALS = os.environ.get("POE2_VARIANT_TRIALS", "1") != "0"
VARIANT_TRIALS = 2  # a variant base's non-meta variants (Archmage, a herald, ...) tried for real per slot and full round
AFFIX_LIMIT = {"Prefix": 3, "Suffix": 3}
# Equipment restrictions on the build's own lines (COV-B, COVB3). PoB ignores both lines (ModCache: no modifier), so
# without this a build taking the passive could wear what the game won't let it use:
#   Smith's Masterwork (Smith of Kitava): "Can only use a Normal Body Armour"
#   "Can't use Helmets": PoE1 data kept for safety, which no PoE2 build can carry. PoB-PoE2's LegionPassives.lua keeps
#   PoE1's Maraketh Timeless keystone Dance with Death (maraketh_keystone_2) with it, but PoE2 has no jewel that conquers
#   for the Maraketh, and PoE2's own Dance with Death (passive_keystone_dance_with_death) is "25% more Skill Speed while
#   Off Hand is empty and you have a One-Handed Martial Weapon equipped in your Main Hand", with no helmet line (checked:
#   PassiveSkills / KeywordPopups DanceWithDeath; coverage.NO_JEWEL_FACTIONS; poe2db Timeless_Jewel lists keystones only
#   for Heroic Tragedy and Undying Hate; AUDIT26 A26-4). Matched by line, so it moves no build.
# Matched by line, from every allocated passive and equipped item (Build sourceLines), not by passive name.
SLOT_RESTRICTIONS = {
    "Can only use a Normal Body Armour": ("Body Armour", "normal"),
    "Can't use Helmets": ("Helmet", "empty"),
}
GRANTED_SKILL = re.compile(r"^Grants Skill: (?:Level \S+ )?")
# Any line granting a skill, the skill's name in group 1 (a unique's raw keeps PoB's {variant:..}{range:..} tags in front)
GRANTED_NAME = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$", re.M)
# Switch for the source-item trials (assumption_trials): POE2_SOURCE_TRIALS=0 turns them off for a run
SOURCE_TRIALS = os.environ.get("POE2_SOURCE_TRIALS", "1") != "0"
AFFIX_ALLOWED = re.compile(r"^([+-]\d+) (Prefix|Suffix) Modifiers? allowed\s*$", re.M)


def affix_limits(base: dict, limits: dict[str, int] = AFFIX_LIMIT) -> dict[str, int]:
    """A rare's prefix and suffix limits on this base: its implicit's "-1 Prefix Modifier allowed" (Absent, Lament and
    Twisted Amulets), "+1 Prefix ..." (Dusk Ring), ... (an Absent Amulet's rare holds 2 and 2; the search had built 3 and 3)."""
    limits = dict(limits)
    for m in AFFIX_ALLOWED.finditer(base.get("implicit") or ""):
        limits[m.group(2)] = max(0, limits[m.group(2)] + int(m.group(1)))
    return limits


@dataclass(frozen=True)
class SlotSpec:
    type: str
    sub_type: str | None = None  # "" means "no sub-type" (caster staves); None means any
    also: tuple[str, ...] = ()  # more item types the slot takes (an off-hand weapon: Sceptre, Dagger, ...)
    # No uniques in this slot: Lord of the Wilds lets a Talisman hold "a non-Unique Sceptre" (game_rules.yaml
    # off_hand.passive_exceptions non_unique)
    non_unique: bool = False
    # Only these bases (non-unique): the main skill comes from a base's implicit only some bases of the type carry
    # (a Withered Wand's Chaos Bolt: skeletons "bases:<type>", Context.granting_bases)
    bases: tuple[str, ...] = ()

    @property
    def types(self) -> tuple[str, ...]:
        return (self.type, *self.also)


def extra_ring_passive(ctx, ascendancy: str) -> str | None:
    """The ascendancy passive that adds a ring slot (Ritualist's Unfurled Finger), if any."""
    for node in ctx.data.tree["nodes"]:
        if node.get("ascendancy") == ascendancy and "+1 Ring Slot" in (node.get("stats") or []):
            return node.get("name")
    return None


def is_normal(option: ItemOption) -> bool:
    """A Normal-rarity item (a plain base, with or without augments in its sockets)."""
    first = option.raw.split("\n", 1)[0].strip()
    return not option.unique and not option.rare and (first == "Rarity: Normal" or not first.startswith("Rarity:"))


RESIST_SWAP_ATTEMPTS = 4  # slot pairs one resist_swap_round tries (two rare builds each; LOW, a search-effort setting)
# The slots resist_swap_round moves resistances into, in order (LOW: SEARCHB1's references put theirs on boots, shield
# and helmet; Weapon 1, the damage slot, never takes them)
RESIST_RECEIVERS = ("Boots", "Helmet", "Body Armour", "Weapon 2", "Belt", "Gloves", "Amulet", "Ring 1", "Ring 2", "Ring 3")
SWITCH_RESIST_SWAP = "POE2_SEARCH_RESIST_SWAP"  # "1": gear_round's full rounds try resist_swap_round (OFF by default)
RESIST_LINE = re.compile(r"^\+?[\d.()\-]+%? to (.+?) Resistances?$")
RESIST_ELEMENTS = ("Fire", "Cold", "Lightning", "Chaos")


def resist_kinds(line: str) -> frozenset[str]:
    """The resistances an affix line raises ("+30% to Fire Resistance", "+12% to Fire and Cold Resistances", "+10% to
    all Elemental Resistances"); empty for anything else (a Maximum, a penetration, an enemy's)."""
    m = RESIST_LINE.match(line.strip())
    if not m or "Maximum" in line:
        return frozenset()
    named = m.group(1)
    if named == "all Elemental":
        return frozenset(RESIST_ELEMENTS[:3])
    words = set(named.replace(",", " ").split())
    if not words <= set(RESIST_ELEMENTS) | {"and"}:
        return frozenset()
    return frozenset(e for e in RESIST_ELEMENTS if e in words)


def explicit_lines(raw: str) -> list[str]:
    """An item text's explicit modifier lines: after its "Implicits: N" lines, the tagged ones ({rune}, {enchant}...)
    kept out."""
    lines = raw.splitlines()
    at = next((i for i, line in enumerate(lines) if line.startswith("Implicits:")), None)
    if at is None:
        return []
    count = int(lines[at].split(":")[1].strip() or 0)
    return [line for line in lines[at + 1 + count:] if line and not line.startswith("{")]


def resist_lines(raw: str) -> list[frozenset[str]]:
    """The resistances each explicit line of an item text raises, one entry a resistance line."""
    return [k for k in (resist_kinds(line) for line in explicit_lines(raw)) if k]


def resist_rule(kinds: frozenset[str]) -> Callable[[Affix], bool]:
    """An affix raising at least these resistances (a hybrid or all-elemental one counts for any it covers)."""
    return lambda affix: any(kinds <= resist_kinds(line) for line in affix.lines)


def requires(required) -> list:
    """build_rare's `required`: none, one rule, or a list of them."""
    if required is None:
        return []
    return list(required) if isinstance(required, (list, tuple)) else [required]


def is_rare_text(raw: str) -> bool:
    return any(line.strip() == "Rarity: RARE" for line in raw.splitlines()[:2])


def is_unique_text(raw: str) -> bool:
    return any(line.strip() == "Rarity: UNIQUE" for line in raw.splitlines()[:2])


def shortfall_of(build: Build) -> float:
    """objective.shortfall over the build's deficits (a game limit counts ten times a floor)."""
    return sum(amount * (10.0 if hard else 1.0) for _, amount, hard in build.objective.deficits(build.stats))


class Gear:
    def __init__(self, build: Build, weapon: SlotSpec | None, offhand: SlotSpec | None, rng: random.Random,
                 swap: tuple[SlotSpec | None, SlotSpec | None] = (None, None)) -> None:
        """`swap` is weapon set 2's main hand and off hand (weapon swap), if the skeleton has one."""
        self.build = build
        self.ctx = build.ctx
        self.rng = rng
        self.slots: dict[str, SlotSpec] = {}
        self.resume: str | None = None  # the slot a full round stopped at (Search.blocks caps its time)
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
        self.rare_tried: set[str] = set()  # empty slots a partial round built a rare for since the last full round
        self.variant_failed: dict[tuple[str, str, str], float] = {}  # (slot, base, variant line) -> score its trial failed at
        self.lam = 0.0  # budget weight: score per divine
        self.magic_pools = {kind: AffixPools(self.ctx, kind) for kind in ("Flask", "Charm")}
        self.charges = Charges()  # charge generation of the committed build (sync_flasks)
        self._flask_info: dict[str, dict] = {}  # item text -> PoB's flask/charm numbers
        self.resolve = None  # re-resolves conditions after a commit (set by the search)
        self._anoint_screen: list[str] | None = None  # this full round's anointable notables, best first

    fill_granted = None  # fills an item's granted meta group with a spell (gems.granted_meta_round); set by the search
    refill_main = None  # refills the main group's supports (gems.refill_main_supports); set by the search
    fit_requirements = None  # re-routes the tree to new gear's attribute requirements (tree.fit_requirements); set by the search
    relevant_skill = None  # would a granted skill change an assumption (Assumptions.relevant)? set by the search

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
            if spec.non_unique:
                options = [o for o in options if not o.unique]
            if spec.bases:
                options = [o for o in options if not o.unique and o.base in spec.bases]
            if not slot.startswith("Weapon") and spec.type != "Charm":
                # Normal armour and jewellery bases differ little within a sub-type: keep the top few
                # A variant base (Absent, Lament, Portent Amulet) is kept apart: as a plain item it carries no implicit
                # and fewer affixes, and it took all three amulet places (Solar's Spirit, Stellar's attributes never
                # offered). Its variants are tried in full rounds (granted_meta_trials).
                bases: dict[str | None, list[ItemOption]] = {}
                variant: list[ItemOption] = []
                for option in sorted((o for o in options if not o.unique), key=lambda o: -o.level):
                    if implicit_variants((self.ctx.bases_by_name.get(option.base) or {}).get("implicit")):
                        variant.append(option)
                    else:
                        bases.setdefault(option.sub_type, []).append(option)
                # An amulet's implicit differs in kind, not size (Solar's Spirit, Stellar's attributes, Gold's rarity),
                # and a level sort cut Solar (30) on a tie with Pearlescent: every amulet base is kept (about 19)
                keep = None if spec.type == "Amulet" else BASES_PER_SUBTYPE
                kept = variant + [o for group in bases.values() for o in group[:keep]]
                options = kept + [o for o in options if o.unique]
            self._options[slot] = options
        rule = self.restriction(slot)
        if rule is not None:
            return [o for o in self._options[slot] if self.allowed(slot, o, rule)]
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
            if slot in self.build.locked_slots or option.type not in spec.types or (spec.non_unique and option.unique)                     or (spec.bases and (option.unique or option.base not in spec.bases)):
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
        if not is_flask_slot(slot):
            return self.build.item_deltas(cases)
        shares = []
        for case, raw, info in zip(cases, raws, self.flask_info(raws)):
            shares.append(self.share(slot, info, raw))
            case["active"] = shares[-1] >= FLASK_ON
            case["extra"] = self.recovery_stats({slot: info}, {slot: raw})
        # An effect up only part of the fight: scored on and off and blended as weigh_uptimes counts it once equipped:
        # the value (DPS) at its share, u x on + (1 - u) x off, and the floors of the worse case (objective.deficits:
        # the build as it is or with its part-time effects off), so the floors never read the slot on alone. Before
        # (to SEARCH22), d_off + ln(u e^(d_on - d_off) + 1 - u) blended the on case's floors in at u: with a part-time
        # Enduring Elixirs Life flask (or Melting Maelstrom) the on case's recovery is PoB's whole flask recovery plus
        # ours (flasks.pob_counts_whole's note, MECHC25), so a short recovery floor read that doubled figure at its
        # share (a ranking OVERCOUNT). A ranking estimate: once equipped, Search.follow_uptimes weighs it exactly
        part = [i for i, up in enumerate(shares) if FLASK_ON <= up < FLASK_WHOLE]
        scored = self.build.item_deltas(cases + [{**cases[i], "active": False} for i in part])
        out = scored[:len(cases)]
        objective = self.build.objective
        for i, (_, off_stats) in zip(part, scored[len(cases):]):
            _, stats = out[i]
            up = shares[i]
            value = up * max(objective.value(stats), 0.0) + (1.0 - up) * max(objective.value(off_stats), 0.0)
            out[i] = (math.log1p(value) - max(objective.penalty(stats), objective.penalty(off_stats)), stats)
        return out

    def put(self, slot: str, option: ItemOption | None) -> float:
        """Equip (or empty) a slot; a flask or charm slot is switched on only if its effect is sustained."""
        if not is_flask_slot(slot):
            return self.build.equip(slot, option)
        info = self.flask_info([option.raw])[0] if option else {}
        self.build.extra_stats = self.recovery_stats({slot: info}, {slot: option.raw} if option else None)
        return self.build.equip(slot, option, active=option is not None and self.counts(slot, info, option.raw))

    # Restrictions from the build's own lines (SLOT_RESTRICTIONS; COV-B) ---------------------------

    def restriction(self, slot: str) -> str | None:
        """"normal" (only a Normal item), "empty" (nothing) or None, from the build's current lines. Read once per
        commit (Build._build_texts caches sourceLines by commit)."""
        commits = getattr(self.build, "commits", None)
        cached = self.__dict__.get("_restrictions")
        if cached is None or cached[0] != commits:
            texts = set(self.build._build_texts())
            rules: dict[str, str] = {}
            for line, (restricted, rule) in SLOT_RESTRICTIONS.items():
                if line in texts:
                    rules[restricted] = "empty" if rule == "empty" or rules.get(restricted) == "empty" else rule
            cached = self._restrictions = (commits, rules)
        return cached[1].get(slot)

    def allowed(self, slot: str, option: ItemOption | None, rule: str | None = None) -> bool:
        if option is None:
            return True
        rule = rule or self.restriction(slot)
        if rule == "empty":
            return False
        if rule == "normal":
            return is_normal(option)
        return True

    def enforce_restrictions(self) -> list[str]:
        """Take off what the build can't use now (a passive that restricts the slot was allocated): the best Normal
        item for a "normal" slot (its runes kept if it has them), nothing for an "empty" one. A locked slot (a
        required unique) is left as it is and reported. Returns what it changed or couldn't."""
        build = self.build
        notes = []
        for slot in list(self.slots):
            current = build.equipped.get(slot)
            rule = self.restriction(slot) if current is not None else None
            if rule is None or self.allowed(slot, current, rule):
                continue
            if slot in build.locked_slots:
                notes.append(f"{slot}: a required item the build's passives don't allow ({rule})")
                continue
            replacement = None
            if rule == "normal":
                bases = [o for o in self.options(slot) if (o.price or 0.0) <= self.left_for(slot) + 1e-9]
                if bases:
                    scored = self.deltas(slot, [o.raw for o in bases])
                    replacement = max(zip((s for s, _ in scored), bases), key=lambda x: x[0])[1]
            self.put(slot, replacement)
            self.rares.pop(slot, None)
            notes.append(f"{slot}: {current.name} off ({rule})")
        if notes:
            self.settle()
        return notes

    # Flasks and charms -----------------------------------------------------------------------

    def flask_info(self, raws: list[str]) -> list[dict]:
        missing = [raw for raw in dict.fromkeys(raws) if raw not in self._flask_info]
        if missing:
            for raw, info in zip(missing, self.build.call("flaskData", raws=missing)["results"]):
                self._flask_info[raw] = info
        return [self._flask_info[raw] for raw in raws]

    def share(self, slot: str, info: dict, raw: str) -> float:
        """The item's effect's share of a boss fight (flasks.uptime: generated charges, Life / Mana filling up)."""
        return uptime(info, self.charges, slot_pool(slot), raw)

    def counts(self, slot: str, info: dict, raw: str) -> bool:
        """Whether the slot is switched on: its effect is up at least FLASK_ON (7%) of a boss fight. Up from FLASK_WHOLE
        (99.5%) it counts whole; between the two it counts at its share of the fight (part_time, Search.weigh_uptimes:
        MECHC5's spec; AUDIT19's A19-1 / A19-7)."""
        return self.share(slot, info, raw) >= FLASK_ON

    def part_time(self) -> dict[str, tuple[float, str]]:
        """Equipped flask and charm slots switched on whose effect is up only part of the fight: slot -> (share, name)."""
        out = {}
        for slot in [*FLASK_SLOTS, *CHARM_SLOTS]:
            option = self.build.equipped.get(slot)
            if option is not None:
                up = self.share(slot, self.flask_info([option.raw])[0], option.raw)
                if FLASK_ON <= up < FLASK_WHOLE:
                    out[slot] = (up, option.name)
        return out

    def recovery_stats(self, replace: dict[str, dict] | None = None, raws: dict[str, str] | None = None) -> dict:
        """Flask recovery per second (the sustain floor's flask share), with `replace`d slots (`raws`: their texts).
        Each flask's own text goes to recovery_per_second (SEARCH22, MECHC25's item): its own "Effect is not removed
        when Unreserved Mana is Filled" (Melting Maelstrom) is how flasks.pob_counts_whole knows PoB counts that flask's
        whole recovery up the whole fight; without the text ours was added on top of PoB's (an OVERCOUNT of Mana
        recovery in the sustain and recovery floors; Enduring Elixirs' Life case reads the build's flag, not the text)."""
        replace, raws = replace or {}, raws or {}
        totals = {field: 0.0 for field in RECOVERY_FIELDS.values()}
        for slot, pool in FLASK_SLOTS.items():
            if slot in replace:
                info, raw = replace[slot], raws.get(slot, "")
            else:
                option = self.build.equipped.get(slot)
                info = self.flask_info([option.raw])[0] if option else None
                raw = option.raw if option else ""
            if info:
                totals[RECOVERY_FIELDS[pool]] += recovery_per_second(info, self.charges, pool, raw)
        return totals

    def sync_flasks(self) -> None:
        """After the build changed elsewhere (passives, other gear): re-read charge generation, switch
        flask and charm slots on or off to match it, and refresh flask recovery. First, an item the build's passives
        no longer let it use comes off (enforce_restrictions)."""
        self.enforce_restrictions()
        build = self.build
        # (Charges.of: with the request's boss preset's add kills, which give flasks and charms charges on the King /
        # Atziri presets, MECHC9; from_bridge alone counted none)
        self.charges = Charges.of(build)
        data = build.call("chargeGain")
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
            share = uptime(info, self.charges, pool, option.raw)
            # (MECHC34 via the manager 22:08: the row reads the flask's text as its score does, Gear.recovery_stats)
            rows.append({"slot": slot, "name": option.name, "pool": pool, "uptime": share,
                         "counted": self.counts(slot, info, option.raw),
                         "part_time": FLASK_ON <= share < FLASK_WHOLE,  # counted at its share (weigh_uptimes)
                         "recovery": recovery_per_second(info, self.charges, pool, option.raw) if pool else 0.0,
                         "full_pool": self.full_pool_reason(info, pool, option.raw, share)})
        return rows

    def full_pool_reason(self, info: dict, pool: str | None, raw: str, share: float) -> str | None:
        """How Life (Mana) filling up ends a flask's effect, when that, not its charges, sets its uptime (MECHC4's
        flasks.full_pool_cap; None while that isn't in this tree, or when the cap doesn't bind)."""
        from builder.search import flasks

        cap_of = getattr(flasks, "full_pool_cap", None)
        cap = cap_of(info, self.charges, pool, raw) if cap_of is not None else None
        return cap[1] if cap is not None and cap[0] < 1.0 and abs(cap[0] - share) < 1e-9 else None

    # Rares -----------------------------------------------------------------------------------

    def build_rare(self, slot: str, deadline: float,
                   required: Callable[[Affix], bool] | list[Callable[[Affix], bool]] | None = None,
                   base_name: str | None = None, variant: int | None = None) -> ItemOption | None:
        """The best rare for this slot within its budget, affix by affix (for a flask or charm, the
        best magic item: one prefix and one suffix). With `required`, the rare starts from the best
        affix that passes it (a condition's source, builder/search/sources.py), on the best base
        that can roll one; with a list of them, one affix for each, on a base that can roll them all. With `variant`, the base's implicit is that variant's (Absent Amulet's Cast on Critical)."""
        if self.restriction(slot) is not None:  # a rare isn't a Normal item; an unusable slot takes nothing
            return None
        bases = [o for o in self.options(slot) if not o.unique and (base_name is None or o.base == base_name)]
        if not bases or clock.now() > deadline:
            return None
        magic = is_flask_slot(slot)
        scored = self.deltas(slot, [o.raw for o in bases])
        base = None
        for _, option in sorted(zip((s for s, _ in scored), bases), key=lambda x: -x[0]):
            candidate = self.ctx.bases_by_name[option.base]
            pools = self.magic_pools[candidate["type"]] if magic else self.pools
            if required is None or all(any(r(a) for a in pools.pool(candidate["name"])) for r in requires(required)):
                base_option, base = option, candidate
                break
        if base is None:
            return None
        if variant is not None:
            base = {**base, "implicit": implicit_variant(base.get("implicit"), variant)}
        limits = MAGIC_LIMIT if magic else affix_limits(base)
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
        for rule in requires(required):  # (several for resist_swap_round: each its own affix, within the limits)
            groups = {a.group for a in chosen}
            counts = {kind: sum(1 for a in chosen if a.type == kind) for kind in limits}
            forced = next((a for _, a in ranked if rule(a) and a.group not in groups
                           and counts.get(a.type, 0) < limits.get(a.type, 0)), None)
            if forced is None:
                return None
            if price_of(slot, chosen + [forced]) > left + 1e-9 and forced.lower is not None:
                forced = forced.lower
            if price_of(slot, chosen + [forced]) > left + 1e-9:
                return None
            chosen.append(forced)
        current = self.deltas(slot, [text(slot, base, level, chosen)])[0][0]
        while clock.now() < deadline:
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
        if not magic and influence.enabled():  # (SEARCH30: an influence rune and the affixes it lets roll, influence.py)
            influenced = self.influence_trial(slot, base, base_option, level, pools, limits, chosen, current, left,
                                              deadline)
            if influenced is not None:
                return influenced
        if not magic and (desecrated.enabled() or desecrated.breach_enabled()):  # (SEARCH30 / 31: one Desecrated affix)
            desecrated_option = self.desecrated_trial(slot, base, base_option, level, pools, limits, chosen, current,
                                                      left, deadline)
            if desecrated_option is not None:
                return desecrated_option
        augmented = []  # (one augment an item: the Alloys' lower reading, kept for the Perfect Essences too)
        if not magic and alloys.enabled():  # (SEARCH29: an Alloy in place of a random modifier, builder/search/alloys.py)
            alloyed = self.alloy_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline)
            if alloyed is not None:
                augmented.append(alloyed)
        if not magic and essences.enabled():  # (SEARCHB1: a Perfect Essence, the same way, builder/search/essences.py)
            essenced = self.essence_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline)
            if essenced is not None:
                augmented.append(essenced)
        if len(augmented) > 1:  # (both beat the plain rare: the better after prices; each carries its DPS factor)
            scored = self.deltas(slot, [o.raw for o in augmented])
            return augmented[max(range(len(augmented)), key=lambda i: (self.adjusted(scored[i][0], augmented[i]), -i))]
        if augmented:
            return augmented[0]
        price = None if self.ctx.ssf else price_of(slot, chosen)
        confidence = "ssf" if self.ctx.ssf else "estimate (magic)" if magic else "estimate (uncalibrated)"
        return ItemOption(f"{'Magic' if magic else 'Rare'} {base['type']}", base["name"], base["type"],
                          base.get("subType"), text(slot, base, level, chosen), False, price, confidence,
                          base_option.level, rare=True, affixes=tuple(line for a in chosen for line in a.lines))

    ALLOY_KEEP = 2  # Alloy candidates (by a what-if with no modifier lost) whose outcomes are all scored

    @property
    def alloy_mods(self) -> dict[str, dict]:
        if getattr(self, "_alloy_mods", None) is None:
            self._alloy_mods = {m["id"]: m for m in self.ctx.data.mods.get("Item", []) if m["id"] in alloys.ALLOYS}
        return self._alloy_mods

    def alloy_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                    chosen: list, current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCH29) The rare with an Alloy, if it beats the plain rare `chosen` (score `current`) after prices. An Alloy
        removes a random modifier, so the rare it goes on (`kept`: `chosen`, or `chosen` less one modifier of the Alloy
        mod's own type when that type is full, so whichever is lost it fits) has len(kept) outcomes: kept - one + the
        Alloy mod. THE MANAGER'S RULING (18 Nov 20:25): DPS at the mean over the outcomes, the floors at the worst. So the
        item equipped is the worst outcome (the lowest full score) and its alloy_scale is the mean DPS / its DPS
        (alloys.ALLOY_FIELD). Price: the plain rare's (kept) + the Alloy's (no price: no offer; self-found: no price)."""
        return self.augment_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline,
                                  alloys.rows_for(base, level, self.alloy_mods), self.ctx.alloy_prices, "alloy")

    @property
    def essence_mods(self) -> dict[str, dict]:
        if getattr(self, "_essence_mods", None) is None:
            self._essence_mods = {m["id"]: m for m in self.ctx.data.mods.get("Item", []) if m["id"] in essences.ESSENCES}
        return self._essence_mods

    def essence_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                      chosen: list, current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCHB1, G1) The rare with a Perfect Essence (builder/search/essences.py): the Alloys' trial and ruling, as a
        Perfect Essence too removes a random modifier and adds a guaranteed one (the 0.3.0 notes)."""
        return self.augment_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline,
                                  essences.rows_for(base, level, self.essence_mods), self.ctx.essence_prices, "essence")

    def without_registered(self, raw: str, stats: dict) -> dict:
        """An item what-if's stats with the DPS factor state.item_deltas gave the item for being a registered augmented
        item (build.alloy_raws, or an equipped item with an augment, by alloy_keys' text) taken back out: the other
        slots' product stays. The same lookup item_deltas makes."""
        build = self.build
        raws = {**(getattr(build, "alloy_raws", None) or {}),
                **{alloys.alloy_key(o.raw): o.alloy_scale for o in (getattr(build, "equipped", None) or {}).values()
                   if getattr(o, "alloy", "")}}
        factor = raws.get(alloys.alloy_key(raw or ""), 1.0)
        if factor == 1.0 or not factor or alloys.ALLOY_FIELD not in stats:
            return stats
        return {**stats, alloys.ALLOY_FIELD: stats[alloys.ALLOY_FIELD] / factor}

    def augment_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                      chosen: list, current: float, left: float, deadline: float, rows: list, prices: dict,
                      kind: str) -> ItemOption | None:
        """An augment that removes a random modifier and adds a guaranteed one (an Alloy or a Perfect Essence: `kind`),
        tried on the plain rare `chosen` as alloy_trial says; `rows` are (its name, its PoB mod row), `prices` its
        divine prices by name."""
        if not rows or clock.now() > deadline:
            return None
        build, ssf = self.build, self.ctx.ssf
        objective = build.objective
        plain = current - self.lam * rare_price(slot, chosen)
        groups = {a.group for a in chosen}
        trials = []  # (alloy, its Affix, kept, price, the side an Omen limits the loss to (None: any modifier))
        for alloy, mod in rows:
            cost = None if ssf else prices.get(alloy)
            if (cost is None and not ssf) or mod["group"] in groups or mod["type"] not in limits:
                continue
            affix = pools.affix(mod, mod["group"], mod["type"], 1, 1, (0.0, 0.0))
            if sum(1 for a in chosen if a.type == mod["type"]) < limits[mod["type"]]:
                kepts = [list(chosen)]
            else:  # (a full side: a rare made with that slot open, one of its modifiers left out)
                kepts = [[a for a in chosen if a is not out] for out in chosen if out.type == mod["type"]]
            for kept in kepts:
                if not kept:
                    continue
                price = rare_price(slot, kept) + (cost or 0.0)
                if price <= left + 1e-9:
                    trials.append((alloy, affix, kept, price, None))
                if kind != "essence":
                    continue
                # (R43-7) Omen of Sinistral / Dextral Crystallisation: the essence removes a prefix only / a suffix only
                # (game ids OmenOnPerfectEssencePrefix / ...Suffix); each side with a modifier to lose, the Omen priced
                for side, omen in essences.OMENS.items():
                    omen_cost = None if ssf else prices.get(omen)
                    if (omen_cost is None and not ssf) or not any(a.type == side for a in kept):
                        continue
                    if price + (omen_cost or 0.0) <= left + 1e-9:
                        trials.append((alloy, affix, kept, price + (omen_cost or 0.0), side))
        if not trials:
            return None
        screened = self.deltas(slot, [rare_text(slot, base, level, kept + [affix]) for _, affix, kept, _, _ in trials])
        # (ties go to the cheaper trial: an Omen that changes nothing never wins on a tie)
        ranked = sorted(((s - self.lam * t[3], -t[3], -i) for i, ((s, _), t) in enumerate(zip(screened, trials))),
                        reverse=True)
        best = None  # (value, option)
        for upper, _, i in ranked[:self.ALLOY_KEEP]:
            i = -i
            if upper <= plain + EPSILON or clock.now() > deadline:
                break  # (losing a modifier never scores above keeping it: the screen is an upper bound, MEDIUM)
            alloy, affix, kept, price, side = trials[i]
            losable = [a for a in kept if side is None or a.type == side]
            outcomes = [[a for a in kept if a is not lost] + [affix] for lost in losable]
            texts = [rare_text(slot, base, level, o) for o in outcomes]
            # (SEARCHB1) an outcome whose text is an augmented item already registered (a rare rebuilt with the same
            # augment: build.alloy_raws, or the slot's equipped one) gets that item's DPS factor in its what-if
            # (state.item_deltas), which inflated the mean (an OVERCOUNT; POE2T6212: 128 DPS read for a 103 DPS
            # outcome): each outcome is read without it
            scored = [(objective.score(bare), bare) for bare in
                      (self.without_registered(text, stats) for text, (_, stats) in zip(texts, self.deltas(slot, texts)))]
            dps = [objective.dps(stats) for _, stats in scored]
            worst = min(range(len(scored)), key=lambda n: (scored[n][0], n))
            scale = alloys.mean_scale(dps, worst)
            stats = dict(scored[worst][1])
            stats[alloys.ALLOY_FIELD] = build.alloy_product(slot) * scale
            value = objective.score(stats) - self.lam * price
            if value > plain + EPSILON and (best is None or value > best[0]):
                typical = alloys.typical(dps)
                lost = lambda n: "; ".join(losable[n].lines)
                omen = f" with {essences.OMENS[side]} (a {side.lower()} only)" if side else ""
                report = (f"{alloy}{omen}: removes a random modifier ({len(losable)} outcomes) and adds "
                          f"\"{'; '.join(affix.lines)}\"; the floors read the worst outcome (shown in the build), DPS the "
                          f"mean over all {len(losable)} (x{scale:.3f} of the worst's) (the manager's ruling, 18 Nov)",
                          f"Typical outcome: loses \"{lost(typical)}\" (DPS {dps[typical]:,.0f})",
                          f"Worst case: loses \"{lost(worst)}\" (DPS {dps[worst]:,.0f}; mean {sum(dps) / len(dps):,.0f})")
                text = rare_text(slot, base, level, outcomes[worst])
                option = ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"), text, False,
                                    None if ssf else price,
                                    "ssf" if ssf else "estimate (uncalibrated) + " + ("Alloy" if kind == "alloy" else "Essence"),
                                    base_option.level, rare=True, affixes=tuple(l for a in outcomes[worst] for l in a.lines),
                                    alloy=alloy, alloy_scale=scale, alloy_report=report, augment=kind)
                best = (value, option)
        if best is None:
            return None
        option = best[1]
        if not hasattr(build, "alloy_raws") or build.alloy_raws is None:
            build.alloy_raws = {}
        build.alloy_raws[alloys.alloy_key(option.raw)] = option.alloy_scale
        return option

    INFLUENCE_KEEP = 2  # influence affixes (by a screen what-if) fully tried each round (SEARCH30; a search budget)

    @property
    def influence_tags(self) -> dict[str, str]:
        if getattr(self, "_influence_tags", None) is None:
            self._influence_tags = influence.rune_tags(self.ctx.data.runes)
        return self._influence_tags

    def runes_used(self, slot: str) -> dict[str, int]:
        """Per-character augment limits taken by the other equipped items: limit group -> count."""
        used: dict[str, int] = {}
        for other_slot, other in self.build.equipped.items():
            if other_slot != slot and other is not None:
                for name in other.runes:
                    if limit := self.runes.limit(name):
                        used[limit[0]] = used.get(limit[0], 0) + 1
        return used

    def influence_price(self, slot: str, affixes: list, rune: str, rune_price: float | None, prices: dict[str, float]
                        ) -> tuple[float | None, list[str], str]:
        """A rare with these affixes and an influence rune: (price, steps, how): the deterministic craft route, the trade
        samples a cross-check row (influence.price; SEARCH31, the user 19 Nov ~22:12)."""
        ordinary = [a for a in affixes if not a.influence]
        return influence.price(rune, rare_price(slot, ordinary), rare_price(slot, affixes), rune_price, prices, affixes)

    def influence_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                        chosen: list, current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCH30) The rare with an influence rune socketed first and the affixes its tag lets roll, if it beats the
        plain rare `chosen` (score `current`) after prices, the plain rare given its best rune in the socket the
        influence rune takes (both fill any other sockets alike later). Each round screens every influence affix the
        rare can still take (added, as if there were room), then tries the best INFLUENCE_KEEP added where its kind has
        room or in place of each modifier of its kind, priced by the craft route (influence.py); greedy while it gains."""
        if clock.now() > deadline or socket_count(base, False) < 1:
            return None
        tags = self.influence_tags
        used = self.runes_used(slot)
        runes = [n for n in self.runes.candidates(base, False) if n in tags
                 and not ((limit := self.runes.limit(n)) and used.get(limit[0], 0) >= limit[1])]
        if not runes:
            return None
        ssf = self.ctx.ssf
        prices = {m: 0.0 for m in influence.MATERIALS} if ssf else self.ctx.influence_prices
        plain = current - self.lam * rare_price(slot, chosen)
        best = None  # (value, rune, affixes, price, steps)
        for rune in runes:
            tag = tags[rune]
            wide = [a for a in pools.pool(base["name"], (tag,)) if a.influence == tag]
            rune_price = self.runes.price(rune)
            if not wide or rune_price is None:
                continue
            picked, value, priced = list(chosen), None, None
            while clock.now() < deadline:
                groups = {a.group for a in picked}
                fresh = [c for a in wide if a.group not in groups for c in pools.choices(a, picked)[:1]]
                if not fresh:
                    break
                floor = self.deltas(slot, [rare_text(slot, base, level, picked, runes=[rune])])[0][0]
                screen = self.deltas(slot, [rare_text(slot, base, level, picked + [a], runes=[rune]) for a in fresh])
                top = [a for s, a in sorted(((s, a) for (s, _), a in zip(screen, fresh)), key=lambda x: -x[0])
                       if s > floor + EPSILON][:self.INFLUENCE_KEEP]
                trials = []  # (affixes, price, steps, how)
                for a in top:
                    for c in pools.choices(a, picked):
                        if sum(1 for x in picked if x.type == c.type) < limits.get(c.type, 0):
                            variants = [picked + [c]]
                        else:
                            variants = [[x for x in picked if x is not out] + [c] for out in picked if out.type == c.type]
                        for v in variants:
                            price, steps, how = self.influence_price(slot, v, rune, rune_price, prices)
                            if price is not None and price <= left + 1e-9:
                                trials.append((v, price, steps, how))
                if not trials:
                    break
                scored = self.deltas(slot, [rare_text(slot, base, level, v, runes=[rune]) for v, *_ in trials])
                gain, i = max(((s - self.lam * t[1], i) for i, ((s, _), t) in enumerate(zip(scored, trials))),
                              key=lambda x: x[0])
                if gain <= (plain if value is None else value) + EPSILON:
                    break
                picked, value, priced = trials[i][0], gain, trials[i][1:]
            if value is not None and (best is None or value > best[0]):
                best = (value, rune, picked, *priced)
        if best is None:
            return None
        value, rune, picked, price, steps, how = best
        # The plain rare with its best ordinary rune in that socket (the influence rune takes it for good)
        others = [n for n in self.runes.candidates(base, False) if n not in tags and self.runes.price(n) is not None
                  and not ((limit := self.runes.limit(n)) and used.get(limit[0], 0) >= limit[1])
                  and rare_price(slot, chosen) + self.runes.price(n) <= left + 1e-9]
        if others and clock.now() < deadline:
            socketed = self.deltas(slot, [rare_text(slot, base, level, chosen, runes=[n]) for n in others])
            plain = max([plain] + [s - self.lam * (rare_price(slot, chosen) + self.runes.price(n))
                                   for (s, _), n in zip(socketed, others)])
        if value <= plain + EPSILON:
            return None
        names = [a for a in picked if a.influence]
        note = (f"{rune} socketed first (socket-bound) lets {len(names)} influence modifier{'s' if len(names) != 1 else ''}"
                f" roll; priced by the user's Chaos-spam route (the landed mod kept safe by Omens of Whittling / Erasure,"
                f" or fractured where nothing protects it: R35-17; LOW odds)")
        return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                          rare_text(slot, base, level, picked, runes=[rune]), False, None if ssf else price,
                          "ssf" if ssf else f"estimate ({how})", base_option.level,
                          rare=True, affixes=tuple(line for a in picked for line in a.lines), runes=(rune,),
                          bound=(rune,), craft_route=(note, *steps))

    DESECRATED_KEEP = 3  # Desecrated affixes (by a screen what-if) fully tried (SEARCH30; a search budget)

    def desecrated_pools(self, route: str = "bone") -> AffixPools:
        pools = self.__dict__.setdefault("_desecrated_pools", {})
        if route not in pools:
            pools[route] = AffixPools(self.ctx, "Desecrated", route)
        return pools[route]

    def desecrated_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                         chosen: list, current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCH30) The rare with one Desecrated affix, added where its side has room or in place of each modifier of
        its side, if it beats the plain rare `chosen` (score `current`) after the craft route's price (desecrated.py).
        Each switched-on route ("bone": a lord row; "breach": an Altered Collarbone's Breach row on jewellery, SEARCH31)
        screens its affixes added; its best DESECRATED_KEEP get every placement; the best of all wins."""
        kind = desecrated.family(base["type"])
        if kind is None or clock.now() > deadline:
            return None
        routes = [r for r, on in (("bone", desecrated.enabled()), ("breach", desecrated.breach_enabled())) if on]
        groups = {a.group for a in chosen}
        ssf = self.ctx.ssf
        prices = self.ctx.desecration_prices if not ssf else {m: 0.0 for m in desecrated.MATERIALS}

        # The reveal's draw (desecrated.routes, RESEARCH34 R33-20): the side's ordinary rows (equal weights: 1 / side_odds
        # of any of its affixes; groups already on the item are still counted, the lower reading) + the Desecrated rows the
        # bone can give whose group the item doesn't hold (an Altered Collarbone: its Breach rows and the lord rows, R33-22)
        ordinary = {}
        for a in pools.pool(base["name"]):
            if a.side_odds > 0:
                ordinary[a.type] = round(1 / a.side_odds)

        def counts(affix, kept, route) -> tuple[int, int, int]:
            held = {x.group for x in kept}
            drawn = self.desecrated_pools(route).pool(base["name"])
            if route == "breach":
                drawn = drawn + self.desecrated_pools("bone").pool(base["name"])
            side = [a for a in drawn if a.type == affix.type and a.group not in held]
            return (sum(1 for a in side if a.desecrated == affix.desecrated) if route == "bone" else 0, len(side),
                    ordinary.get(affix.type, 0))

        plain = current - self.lam * rare_price(slot, chosen)
        trials = []  # (affixes, the Desecrated affix, price, steps)
        for route in routes:
            fresh = [a for a in self.desecrated_pools(route).pool(base["name"]) if a.group not in groups and a.type in limits]
            if not fresh or clock.now() > deadline:
                continue
            screen = self.deltas(slot, [rare_text(slot, base, level, chosen + [a]) for a in fresh])
            top = [a for s, a in sorted(((s, a) for (s, _), a in zip(screen, fresh)), key=lambda x: -x[0])
                   if s > current + EPSILON][:self.DESECRATED_KEEP]
            for a in top:
                for c in self.desecrated_pools(route).choices(a, chosen):
                    if sum(1 for x in chosen if x.type == c.type) < limits[c.type]:
                        variants = [list(chosen)]
                    else:
                        variants = [[x for x in chosen if x is not out] for out in chosen if out.type == c.type]
                    for kept in variants:
                        market = rare_price(slot, kept)
                        price, steps = desecrated.craft_price(base["type"], market, c, *counts(c, kept, route), prices,
                                                              route)
                        if price is not None and price <= left + 1e-9:
                            trials.append((kept + [c], c, price, steps))
        if not trials or clock.now() > deadline:
            return None
        scored = self.deltas(slot, [rare_text(slot, base, level, t[0]) for t in trials])
        value, i = max(((s - self.lam * t[2], i) for i, ((s, _), t) in enumerate(zip(scored, trials))), key=lambda x: x[0])
        if value <= plain + EPSILON:
            return None
        affixes, affix, price, steps = trials[i]
        note = (f"one Desecrated modifier ({affix.desecrated}, {affix.type}); priced by the cheapest repeat-until-hit craft "
                f"route (MEDIUM with a lord Omen, LOW without: desecrated.py)")
        return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                          rare_text(slot, base, level, affixes), False, None if ssf else price,
                          "ssf" if ssf else "estimate (craft route: Desecration)", base_option.level, rare=True,
                          affixes=tuple(line for a in affixes for line in a.lines), craft_route=(note, *steps))

    def socketed(self, slot: str, option: ItemOption, deadline: float) -> ItemOption:
        """The option with its sockets filled by the runes that score best (its socket-bound runes stay: SEARCH30)."""
        build = self.build
        base = self.ctx.bases_by_name.get(option.base)
        count = socket_count(base, option.unique, unique_text(self.ctx, option)) if base else 0
        bound = list(option.bound)
        if count <= len(bound) or clock.now() > deadline:
            return option
        raw = with_runes(option.raw, bound)
        left = self.left_for(slot) - (option.price or 0.0)
        names = [n for n in self.runes.candidates(base, option.unique, option.raw)
                 if (self.runes.price(n) or 0.0) <= left]
        if not names:
            return option
        scored = build.item_deltas([{"slot": slot, "raw": with_runes(raw, bound + [n])} for n in names])
        top = [n for _, n in sorted(zip((s for s, _ in scored), names), key=lambda x: -x[0])[:RUNE_KEEP]]
        chosen: list[str] = list(bound)
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
        for _ in range(count - len(bound)):
            spend = sum(self.runes.price(n) or 0.0 for n in chosen[len(bound):])
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
        if len(chosen) == len(bound):
            return option
        price = option.price if option.price is None else             option.price + sum(self.runes.price(n) or 0.0 for n in chosen[len(bound):])
        return dataclasses.replace(option, raw=with_runes(raw, chosen), runes=tuple(chosen), price=price)

    # Anoints ---------------------------------------------------------------------------------

    def anoint_recipes(self) -> dict[str, tuple[int, tuple[str, str, str]]]:
        """Anointable notable name -> (node id, its three emotions)."""
        by_id = anoints.recipes(str(self.ctx.data.tree.get("treeVersion", "")))
        return {self.ctx.nodes[i]["name"]: (i, r) for i, r in by_id.items()
                if i in self.ctx.nodes and self.ctx.nodes[i]["name"] not in anoints.LEFT_OUT}

    def anoint_cost(self, name: str) -> float:
        """A notable's three emotions in divines (0 when unpriced: SSF, or no price data)."""
        entry = self.anoint_recipes().get(name)
        return (anoints.cost(entry[1], self.ctx.emotion_prices) or 0.0) if entry else 0.0

    def anoint_screen(self) -> list[str]:
        """Every anointable notable the tree hasn't allocated, best first by a node what-if (allocating it without a
        path is what an anoint does) less its price, keeping only gains: once per full round, shared by every
        anointable slot. A notable an equipped item already anoints screens at no gain; anointed() adds the slot's
        own back."""
        if self._anoint_screen is None:
            recipes = self.anoint_recipes()
            allocated = {n["id"] for n in self.build.call("allocatedNodes")["nodes"]}
            banned = self.ctx.compiled.filters.forbidden_passives
            keys = [(name, i) for name, (i, _) in recipes.items() if i not in allocated and name not in banned]
            scored = self.build.node_deltas([{"add": [node_id]} for _, node_id in keys])
            ranked = sorted(((s - self.lam * self.anoint_cost(name), name) for (s, _), (name, _) in zip(scored, keys)),
                            key=lambda x: -x[0])
            self._anoint_screen = [name for value, name in ranked if value > self.build.score + EPSILON]
        return self._anoint_screen

    def anointed(self, slot: str, option: ItemOption, deadline: float) -> ItemOption:
        """The option with the anoints that score best, chosen one at a time (Strugglescream takes 4); its old anoints
        are kept only if still best. Never a notable the tree allocated or another item anoints (a second grant of one
        passive gives nothing: UNVERIFIED for anoints, treated as no gain)."""
        cap = anoints.capacity(option)
        if cap == 0 or clock.now() > deadline or not self.anoint_recipes():
            return option
        build = self.build
        others = {n for s, o in build.equipped.items() if s != slot for n in o.anoints}
        pool = [name for name in option.anoints if name not in others]
        pool += [name for name in self.anoint_screen() if name not in others and name not in pool][:ANOINT_KEEP * cap]
        if not pool:
            return option
        raw = anoints.strip(option.raw)
        bare = option.price if option.price is None else option.price - sum(self.anoint_cost(n) for n in option.anoints)
        left = self.left_for(slot) - (bare or 0.0)
        chosen: list[str] = []
        current = build.item_deltas([{"slot": slot, "raw": raw}])[0][0]
        for _ in range(cap):
            spend = sum(self.anoint_cost(n) for n in chosen)
            options = [n for n in pool if n not in chosen and spend + self.anoint_cost(n) <= left + 1e-9]
            if not options or clock.now() > deadline:
                break
            scored = build.item_deltas([{"slot": slot, "raw": anoints.with_anoints(raw, chosen + [n])} for n in options])
            best_value, best = max(((s - self.lam * self.anoint_cost(n), n) for (s, _), n in zip(scored, options)),
                                   key=lambda x: x[0])
            if best_value <= current + EPSILON:
                break
            chosen.append(best)
            current = best_value + self.lam * self.anoint_cost(best)
        if tuple(chosen) == option.anoints:
            return option
        price = bare if bare is None else bare + sum(self.anoint_cost(n) for n in chosen)
        return dataclasses.replace(option, raw=anoints.with_anoints(raw, chosen), anoints=tuple(chosen), price=price)

    def shard_trial(self, slot: str, option: ItemOption | None, deadline: float) -> ItemOption | None:
        """A helmet with a Raven-Touched Shard (anoints.SHARD) in a socket, scored with the best anoint it allows.
        Alone the shard's "Raven-Touched" line scores nothing, so socketed() never picks it. It goes into a free
        socket or replaces the last rune chosen (the greedy fill's weakest), and is kept only if shard + anoint +
        their prices beat the helmet as it is. The catalog gives level 60 and its price (RuneCatalog.candidates);
        a Corrupted or Sanctified helmet takes none; one per character (its limit)."""
        if (option is None or option.type != "Helmet" or anoints.SHARD in option.runes or clock.now() > deadline
                or anoints.SEALED.search(option.raw) or anoints.capacity(option) > 0):
            return option
        base = self.ctx.bases_by_name.get(option.base)
        count = socket_count(base, option.unique, unique_text(self.ctx, option)) if base else 0
        if count == 0 or anoints.SHARD not in self.runes.candidates(base, option.unique, option.raw):
            return option
        limit = self.runes.limit(anoints.SHARD)
        if limit:
            used = sum(1 for s, o in self.build.equipped.items() if s != slot and o is not None
                       for n in o.runes if (self.runes.limit(n) or ("",))[0] == limit[0])
            if used >= limit[1]:
                return option
        runes = list(option.runes)
        dropped = runes[count - 1:] if len(runes) >= count else []
        if any(n in option.bound for n in dropped):  # (SEARCH30: a socket-bound influence rune is never replaced)
            return option
        new = runes[:count - 1] + [anoints.SHARD] if dropped else runes + [anoints.SHARD]
        price = option.price
        if price is not None:
            price += (self.runes.price(anoints.SHARD) or 0.0) - sum(self.runes.price(n) or 0.0 for n in dropped)
            if price > self.left_for(slot) + 1e-9:
                return option
        trial = dataclasses.replace(option, raw=with_runes(option.raw, new), runes=tuple(new), price=price)
        anointed = self.anointed(slot, trial, deadline)
        if not anointed.anoints:
            return option
        (old, _), (new_score, _) = self.deltas(slot, [option.raw, anointed.raw])
        return anointed if self.adjusted(new_score, anointed) > self.adjusted(old, option) + EPSILON else option

    def commit_anoints(self, slot: str, option: ItemOption, deadline: float) -> bool:
        """Re-anoint an item the round doesn't otherwise change (a required unique); kept only if it scores better."""
        anointed = self.anointed(slot, option, deadline)
        if anointed is option:
            return False
        before = self.build.score
        self.put(slot, anointed)
        self.settle()
        if self.adjusted(self.build.score, anointed) < self.adjusted(before, option) - EPSILON and self.build.worse(before):
            self.put(slot, option)
            self.settle()
            return False
        return self.build.score > before + EPSILON

    # Rounds ----------------------------------------------------------------------------------

    def gear_round(self, deadline: float, full: bool = True) -> bool:
        """Best affordable item per slot. A full round scores every option, builds a rare and
        fills sockets, and remembers the top few per slot; other rounds rescore only those."""
        build = self.build
        improved = False
        self.sync_flasks()
        slots = list(self.slots)
        if full:  # a full round that ran out of time last task goes on from the slot it stopped at
            self._anoint_screen = None
            self.rare_tried.clear()
            # The source-item trials run when a full round starts, not again when a cut round goes on (they ran then),
            # and take at most TRIAL_SHARE of its time: each trial settles the build and may re-route the tree for up
            # to REQUIREMENT_FIT_SECONDS, and on 2b75d they took the whole share, so the slot loop never reached a
            # slot and six slots stayed empty through round 2 (SEARCH21)
            if self.resume is None:
                now = clock.now()
                improved = self.assumption_trials(min(deadline, now + TRIAL_SHARE * max(0.0, deadline - now)))
            start = slots.index(self.resume) if self.resume in slots else 0
            slots = slots[start:] + slots[:start]
            self.resume = None
        for slot in slots:
            if clock.now() > deadline:
                if full and self.resume is None:
                    self.resume = slot
                continue
            if slot in build.locked_slots:
                if full and (locked := build.equipped.get(slot)) is not None:  # a required unique is still anointed
                    improved = self.commit_anoints(slot, locked, deadline) or improved
                continue
            if slot in CHARM_SLOTS and CHARM_SLOTS.index(slot) >= self.charges.charm_limit:
                if build.equipped.get(slot) is not None:  # the belt changed: a charm PoB no longer counts
                    self.put(slot, None)
                continue
            options = self.candidates(slot)
            if not full and slot in self.shortlist:
                shortlisted = set(self.shortlist[slot])
                options = [o for o in options if o.name in shortlisted]
            if full or (slot not in self.rares and slot not in self.rare_tried and build.equipped.get(slot) is None
                        and not is_flask_slot(slot) and slot not in CHARM_SLOTS):
                # (a partial round builds one for an empty gear slot no full round has reached yet: it rescored only
                # uniques and plain bases there, and gained 0 in all five round-2 lines of 2b75d, SEARCH21)
                self.rare_tried.add(slot)
                rare = self.build_rare(slot, deadline)
                if rare is not None:
                    self.rares[slot] = rare
            rare = self.rares.get(slot)
            if (rare is not None and rare != build.equipped.get(slot) and (rare.price or 0.0) <= self.left_for(slot) + 1e-9
                    and self.allowed(slot, rare)):
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
                if target is not None and tuple(target.runes) == tuple(target.bound):  # (bound runes: SEARCH30)
                    filled = self.socketed(slot, target, deadline)
                    if filled is not target:
                        choice = filled
                target = choice or current
                if target is not None:
                    anointed = self.anointed(slot, target, deadline)
                    if anointed is not target:
                        choice = anointed
                    target = choice or current
                    shard = self.shard_trial(slot, target, deadline)
                    if shard is not target:
                        choice = shard
                    target = choice or current  # CULT: a Vaal Cultivation Orb craft of this unique (cultivated.trial)
                    grown = cultivated.trial(self, slot, target, deadline)
                    if grown is not target:
                        choice = grown
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
            improved = self.character_rune_round(deadline) or improved
        if full and os.environ.get(SWITCH_RESIST_SWAP, "0") == "1":
            improved = any(row["kept"] for row in self.resist_swap_round(deadline)) or improved
        if full and self.fill_granted is not None:
            improved = self.granted_meta_trials(deadline) or improved
        if full:
            self.update_lambda()
        return improved

    # Resistances moved off an offence slot (SEARCHB2, SEARCHB1's G5) --------------------------------

    def resist_swap_round(self, deadline: float, attempts: int = 0) -> list[dict]:
        """A two-slot move one slot's rebuild can't make: with the resistance floors met only just, a rare holding
        resistances (2b75d's and 2b75b's gloves: three each) can't drop one for damage until another slot has the
        spare, and no slot rebuilds for a spare it doesn't need yet. So slot B (a rare, plain or empty gear slot) is
        rebuilt with its own resistance affixes and as many of rare A's as it holds forced in (build_rare's
        `required`, one affix each), then A is rebuilt with only the rest forced, free to take damage instead. Kept only if the score rises and the build is no further from the request (its
        shortfall), else everything goes back. At most `attempts` pairs (RESIST_SWAP_ATTEMPTS when 0): A by its
        resistance count, B in RESIST_RECEIVERS' order. Returns one row per pair tried."""
        build = self.build
        attempts = attempts or RESIST_SWAP_ATTEMPTS
        equipped = build.call("equipped")["slots"]
        gear_slots = [s for s in self.slots if not is_flask_slot(s) and s not in CHARM_SLOTS
                      and s not in build.locked_slots and self.restriction(s) is None]
        holders = sorted(((len(resist_lines(equipped.get(s) or "")), s) for s in gear_slots
                          if is_rare_text(equipped.get(s) or "") and resist_lines(equipped.get(s) or "")),
                         key=lambda x: -x[0])
        receivers = [s for s in gear_slots if not is_unique_text(equipped.get(s) or "") and s != "Weapon 1"]
        # B: the armour slots first, where SEARCHB1's references carry their resistances (G5: boots, shield, helmet)
        receivers.sort(key=lambda s: RESIST_RECEIVERS.index(s) if s in RESIST_RECEIVERS else len(RESIST_RECEIVERS))
        rows: list[dict] = []
        for _, a in holders:
            moving = resist_lines(equipped.get(a) or "")
            for b in receivers:
                if b == a or len(rows) >= attempts or clock.now() > deadline:
                    continue
                own = resist_lines(equipped.get(b) or "")
                # (B's own resistances' groups are taken: A's other kinds go first. POE2T6234: 2b75d's boots hold
                # Lightning, the gloves' first, so "Lightning first" placed nothing there)
                covered = frozenset().union(*own) if own else frozenset()
                moving = sorted(resist_lines(equipped.get(a) or ""), key=lambda k: k <= covered)
                snap, before, short = build.snapshot(), build.score, shortfall_of(build)
                rows.append({"from": a, "to": b, "kept": False})
                # B keeps its own resistances and takes as many of A's as its affixes hold (the most first; a rule
                # build_rare can't place, past the slot's suffixes or with no base rolling it, ends the list there)
                rare_b, moved = None, len(moving)
                while moved > 0 and rare_b is None and clock.now() < deadline:
                    rare_b = self.build_rare(b, deadline, required=[resist_rule(k) for k in own + moving[:moved]])
                    if rare_b is None:
                        moved -= 1
                if rare_b is None:
                    rows[-1]["why"] = "the slot holds none of them beside its own"
                    continue
                rows[-1]["moved"] = [sorted(k) for k in moving[:moved]]
                self.put(b, rare_b)
                self.settle()
                rare_a = self.build_rare(a, deadline, required=[resist_rule(k) for k in moving[moved:]] or None)
                if rare_a is not None:
                    self.put(a, rare_a)
                    self.settle()
                after = shortfall_of(build)
                rows[-1].update(score=round(build.score, 4), before=round(before, 4), shortfall=round(after, 4))
                if build.score > before + EPSILON and after <= short + EPSILON:
                    rows[-1]["kept"] = True
                    self.rares[b] = rare_b
                    if rare_a is not None:
                        self.rares[a] = rare_a
                    break
                build.restore(snap)
        return rows

    # Martial Artist's Runic Meridians (PATCH_CHECK H5-4, an UNDERCOUNT; SEARCH22): "Can tattoo Runes onto your body,
    # gaining additional Rune-only sockets: 1 Helmet socket, 2 Body Armour sockets, 1 Gloves socket, 1 Boots socket"
    # (PoB tree 0_5 node 39552; flag SocketRunesOnCharacter). PoB calculates the runes in its five character rune slots,
    # but nothing chose them, so they stayed empty. Each full round tries every rune PoB lists for a slot (its
    # canSocketInChakraSlots runes of that slot's type or "armour": the game files' SoulCores.CanSocketInMartialArtistSlots,
    # 199 of 313 rows) that the character's level allows, the request doesn't forbid, has a price within the budget and
    # keeps per-character augment limits, and keeps the best that raises the score. Without the passive, a rune left
    # in a slot is taken out (PoB doesn't count it, and it would cost).
    def character_rune_round(self, deadline: float) -> bool:
        build = self.build
        if not hasattr(build, "character_runes"):
            return False
        info = build.call("characterRunes")
        improved = False
        if not info.get("active"):
            for slot in list(build.character_runes):
                build.set_character_rune(slot, "None", None)
            return False
        forbidden = self.ctx.compiled.filters.forbidden_augments
        for entry in info.get("slots") or []:
            if clock.now() > deadline:
                break
            slot, current = entry["slot"], entry.get("rune") or "None"
            current_price = build.character_runes.get(slot, (None, 0.0))[1]
            left = self.ctx.budget - build.spent() + current_price
            used: dict[str, int] = {}  # per-character augment limits, across items and the other rune slots
            for other in build.equipped.values():
                for name in other.runes:
                    if limit := self.runes.limit(name):
                        used[limit[0]] = used.get(limit[0], 0) + 1
            for other_slot, (name, _) in build.character_runes.items():
                if other_slot != slot and (limit := self.runes.limit(name)):
                    used[limit[0]] = used.get(limit[0], 0) + 1
            names = []
            for rune in entry.get("runes") or []:
                name, price = rune["name"], self.runes.price(rune["name"])
                limit = self.runes.limit(name)
                if (name == current or name in forbidden or (rune.get("level") or 0) > self.ctx.level or price is None
                        or price > left + 1e-9 or (limit and used.get(limit[0], 0) >= limit[1])):
                    continue
                names.append(name)
            if not names:
                continue
            scored = build.character_rune_deltas([{"slot": slot, "rune": n} for n in names])
            current_value = build.score - self.lam * current_price
            best = max(((s - self.lam * (self.runes.price(n) or 0.0), n) for (s, _), n in zip(scored, names)),
                       key=lambda x: (x[0], x[1]))
            if best[0] <= current_value + EPSILON:
                continue
            before = build.score
            build.set_character_rune(slot, best[1], self.runes.price(best[1]))
            self.settle()
            if build.worse(before):
                build.set_character_rune(slot, current, current_price if current != "None" else None)
                self.settle()
                continue
            improved = improved or build.score > before + EPSILON
        return improved

    # The user's answer (14 Nov ~20:03, SEARCH22 item 3: "Fill with cheapest useful"): a slot no item raised the score in
    # stays empty (2b75d's Boots: every boots item scored exactly the empty slot's score once the floors were met, probes
    # POE2T2290-2291), which no player would wear. A last pass puts in each empty gear slot the cheapest item that adds
    # defence or movement speed (FILLER_GAINS), kept only if the score, every floor and the budget are no worse.
    # Flask and charm slots aren't filled (their uptime and charges change other slots' effects: not a free choice).
    FILLER_GAINS = ("EnergyShield", "Armour", "Evasion", "Life", "EffectiveMovementSpeedMod")

    def fill_empty(self, reweigh: Callable[[], None] | None = None) -> list[dict]:
        """The filler pass: returns {slot, name, price} for each slot filled (play_guide's filler rows, filler_rows).
        `reweigh` refreshes the part-time weighing after a put (Search.weigh_uptimes), so the check reads the exact score."""
        from builder.search.objective import number

        build, objective = self.build, self.build.objective
        filled = []
        for slot in self.slots:
            if build.equipped.get(slot) is not None or slot in build.locked_slots or is_flask_slot(slot):
                continue
            options = [o for o in self.candidates(slot) if self.allowed(slot, o)]
            if not options:
                continue
            base_stats, before, spent = dict(build.stats), build.score, build.spent()
            weight = objective._weight(objective.deficits(base_stats))
            useful = []
            for option, (score, stats) in zip(options, self.deltas(slot, [o.raw for o in options])):
                gains = [number(stats.get(f)) - number(base_stats.get(f)) for f in self.FILLER_GAINS]
                if score >= before - EPSILON and any(g > 1e-9 for g in gains)                         and objective._weight(objective.deficits(stats)) <= weight + 1e-12:
                    useful.append(((option.price or 0.0), -sum(g > 1e-9 for g in gains), option.name, option))
            for *_, option in sorted(useful, key=lambda u: u[:3]):
                self.put(slot, option)
                self.settle()
                if reweigh is not None:
                    reweigh()
                if (build.score >= before - EPSILON and build.spent() <= max(self.ctx.budget, spent) + 1e-9
                        and objective._weight(objective.deficits(build.stats)) <= weight + 1e-12):
                    filled.append({"slot": slot, "name": option.name, "price": option.price or 0.0})
                    break
                self.put(slot, None)  # (worse once committed: the next cheapest)
                self.settle()
                if reweigh is not None:
                    reweigh()
        self.fillers = filled
        return filled

    def filler_rows(self) -> list[dict]:
        """The play guide's row for each filler (fill_empty): no item raised the score in its slot."""
        from builder.search import play_guide

        dps = max(self.build.objective.dps(self.build.stats), 0.0)  # (a filler moves no DPS: the same both ways)
        return [{"var": f"filler:{f['slot']}", "label": f"{f['slot']}: {f['name']} is a filler", "kind": play_guide.BUILD,
                 "counted": False,
                 "play": (f"no item raised the score here; {f['name']} is the cheapest item ({f['price']:g} divine) that "
                          f"adds defence or movement speed, put in only because the score, every floor and the budget "
                          f"stay as they are: swap it for anything you like"),
                 "dps_with": dps, "dps_without": dps, "change": 0.0, "confidence": "theory"} for f in getattr(self, "fillers", [])]

    def assumption_trials(self, deadline: float) -> bool:
        """A unique (or a rare on a base whose implicit grants it: source_bases) granting a skill an assumption counts (Assumptions.relevant: a Remnant source while Explosive
        Transmutation's explosion is in the build, e.g. The Hollow Mask's Wildwood's Gifts) is tried for real: equipped,
        settled (the assumptions re-applied and the main group's calculated skill chosen again), kept if the build scores
        better. A plain item what-if sees neither: PoB makes an item's granted skill only in its main calculation (CalcSetup
        env.mode == "MAIN", variant_options' note), and the explosion's trigger rate is a modifier the assumptions set from the committed build (SEARCH3: the Shaman ET
        builds of POE2T733 scored 0 with The Hollow Mask in their options). Run first in a full round, so a 60 s probe
        reaches it before its skeleton is ranked."""
        if not SOURCE_TRIALS or self.relevant_skill is None:
            return False
        build = self.build
        improved = False
        for slot in self.slots:
            if slot in build.locked_slots or is_flask_slot(slot) or clock.now() > deadline:
                continue
            current = build.equipped.get(slot)
            for option in [o for o in self.candidates(slot) if o.unique] + self.source_bases(slot, current, deadline):
                if clock.now() > deadline:
                    continue
                granted = [m.group(1) for m in GRANTED_NAME.finditer(option.raw)]
                if not any(self.relevant_skill(name) for name in granted):
                    continue
                if current is not None and current.raw and any(name in current.raw for name in granted):
                    continue  # (the slot already holds an item granting it)
                snap, before = build.snapshot(), build.score
                self.put(slot, option)
                self.settle()
                if self.fit_requirements is not None:
                    self.fit_requirements(min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS))
                if build.score > before + EPSILON and self.adjusted(build.score, option) > self.adjusted(before, current):
                    improved, current = True, option
                    continue
                build.restore(snap)
                self.settle()
        return improved

    def source_bases(self, slot: str, current: ItemOption | None, deadline: float) -> list[ItemOption]:
        """For assumption_trials: a rare on each ordinary base whose implicit grants a skill an assumption counts (A12-5:
        Lament Amulet's Mana Remnants, Lingering Illusion and Siphon Elements variants), one per such variant, built for
        the slot. Its bare base scores nothing for the source, so the rare builder's own base ranking never picks it."""
        options = []
        for base in self.options(slot):
            if base.unique or clock.now() > deadline:
                continue
            implicit = (self.ctx.bases_by_name.get(base.base) or {}).get("implicit") or ""
            variants = implicit_variants(implicit) or [(None, implicit)]
            for variant, line in variants:
                names = [m.group(1) for m in GRANTED_NAME.finditer(line)]
                if not any(self.relevant_skill(n) for n in names) or clock.now() > deadline:
                    continue
                if current is not None and current.base == base.base and all(n in current.raw for n in names):
                    continue  # (that variant is on already)
                option = self.build_rare(slot, deadline, base_name=base.base, variant=variant)
                if option is not None and self.allowed(slot, option):
                    options.append(option)
        return options

    def granted_meta_trials(self, deadline: float) -> bool:
        """Slice 8: a unique granting a meta skill (GRANTS_META: Mjolner's Thundergod's Wrath, Svalinn's Cast on Block,
        Forgotten Warden's Spirit Vessel), or
        a rare on a base whose implicit grants one (a Dueling Wand's Spellslinger),
        scores only its other lines until a spell is socketed in its group, so each one the build can afford is tried for
        real: equipped, its group filled (fill_granted: gems.granted_meta_round), kept if the build scores better.
        The tree is re-routed to the unique's attribute requirements first (fit_requirements): on 2b75d a tree tuned to
        the old weapon left Mjolner 54-90% short of Strength or Intelligence, a hard deficit that sank every trial."""
        build = self.build
        improved = False
        for slot in self.slots:
            if slot in build.locked_slots or clock.now() > deadline:
                continue
            current = build.equipped.get(slot)
            if current is not None and GRANTS_META.search(current.raw) and self.reverse_meta_trial(slot, deadline):
                improved, current = True, build.equipped.get(slot)
            options = [o for o in self.candidates(slot) if o.unique]
            plain: dict[int, tuple[str, str, str]] = {}  # the non-meta variants' rares (variant_options) -> their key
            if VARIANT_META_TRIALS:
                for option, key in self.variant_options(slot, current, deadline):
                    options.append(option)
                    plain[id(option)] = key
            # A base whose implicit grants one (a Dueling Wand's Spellslinger): a rare on it, built for the slot. Its
            # bare base scores nothing for the meta, so the rare builder's own base ranking never picks it. A base
            # whose implicit is one of several variants (Absent Amulet: Cast on Elemental Ailment, on Critical, on
            # Dodge): a rare for each variant granting one. Its other variants (Archmage, Trinity, and Lament and
            # Portent Amulet's heralds, banners, Blink, Sacrifice, ...) come from variant_options.
            for base in self.options(slot):
                if base.unique or clock.now() > deadline:
                    continue
                implicit = (self.ctx.bases_by_name.get(base.base) or {}).get("implicit") or ""
                variants = implicit_variants(implicit)
                if variants and not VARIANT_META_TRIALS:
                    continue
                if variants:
                    for variant, line in variants:
                        if (GRANTS_META.search(line) and clock.now() < deadline
                                and not (current is not None and current.base == base.base and line in current.raw)):
                            if option := self.build_rare(slot, deadline, base_name=base.base, variant=variant):
                                options.append(option)
                elif GRANTS_META.search(base.raw) and not (current is not None and current.base == base.base):
                    options.append(self.build_rare(slot, deadline, base_name=base.base) or base)
            for option in options:
                if ((current is not None and (option.name == current.name if option.unique else option.raw == current.raw))
                        or not (GRANTS_META.search(option.raw) or id(option) in plain) or clock.now() > deadline):
                    continue
                snap = build.snapshot()
                before = build.score
                self.put(slot, option)
                self.settle()
                if self.fit_requirements is not None:
                    self.fit_requirements(min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS))
                self.fill_granted(deadline)
                if build.score > before + EPSILON:
                    improved, current = True, option
                    continue
                if id(option) in plain:
                    self.variant_failed[plain[id(option)]] = before
                build.restore(snap)
                self.settle()
        return improved

    def variant_options(self, slot: str, current: ItemOption | None, deadline: float) -> list[tuple[ItemOption, tuple[str, str, str]]]:
        """A rare for each of the VARIANT_TRIALS non-meta variants of the slot's variant bases (Absent Amulet's
        Archmage, Trinity, Rhoa Mount, Eternal Rage; Lament Amulet's 37: heralds, banners, Berserk, ...; Portent
        Amulet's Blink, Sacrifice, Mirage Archer, ...) whose skill, added to the build as a gem group at the item's
        assumed level (granted_rolls.ENDGAME_LEVEL), scores best above the build: what the granted skill adds. (A bare
        item's what-if can't rank them: PoB makes an item's granted-skill group only in its main calculation, CalcSetup
        env.mode == "MAIN", so a what-if never sees the skill.) A skill the build already uses, or a banned one, isn't
        offered (a second copy of a buff doesn't stack: an assumption, not checked for every skill). Every one is a
        socketable gem too (SkillGems, game files 26476662: all 51); the item's copy needs no gem group of the 9
        (MAX_GEM_GROUPS). A variant whose trial failed isn't tried again until the score has moved RETRY_MOVE. The
        what-if can't see a skill only a resolve or a custom model counts (Sacrifice's corpses): those rank low."""
        from builder.search.gems import RETRY_MOVE
        from builder.search.granted_rolls import ENDGAME_LEVEL

        build = self.build
        present = {g["name"] for group in build.groups() for g in group["gems"]}
        forbidden = self.ctx.compiled.filters.forbidden_gems
        cases: dict[str, tuple[str, int, tuple[str, str, str]]] = {}  # skill -> (base, variant, key), first base wins
        for base in self.options(slot):
            data = self.ctx.bases_by_name.get(base.base) or {}
            if base.unique:
                continue
            for variant, line in implicit_variants(data.get("implicit")):
                skill = GRANTED_SKILL.sub("", line).strip()
                key = (slot, base.base, line)
                if (GRANTS_META.search(line) or skill in present or skill in forbidden or skill in cases
                        or skill not in self.ctx.any_gems
                        or (current is not None and current.base == base.base and skill in current.raw)
                        or (key in self.variant_failed and abs(build.score - self.variant_failed[key]) <= RETRY_MOVE)):
                    continue
                cases[skill] = (base.base, variant, key)
        if not cases or clock.now() > deadline:
            return []
        skills = list(cases)
        scored = build.gem_deltas([{"newGroup": [n], "level": ENDGAME_LEVEL, "quality": 0} for n in skills],
                                  skip_ehp=False)
        ranked = sorted(((score, *cases[n]) for (score, _), n in zip(scored, skills) if score > build.score + EPSILON),
                        reverse=True)
        found = []
        for _, name, variant, key in ranked[:VARIANT_TRIALS]:
            if clock.now() > deadline:
                break
            if option := self.build_rare(slot, deadline, base_name=name, variant=variant):
                found.append((option, key))
        return found

    def reverse_meta_trial(self, slot: str, deadline: float) -> bool:
        """The reverse of a granted-meta trial: the slot's meta-granting item off for the best plain rare, the tree
        re-routed to it and the main group's supports refilled (refill_main), kept if the build scores better. A meta
        group kept early takes supports the main group then lacks (2b75b: a rare Absent Amulet's Cast on Dodge with 4
        supports, the main group left 2-3), and nothing else asked whether the build does better without it."""
        build = self.build
        option = self.build_rare(slot, deadline)
        if option is None or GRANTS_META.search(option.raw) or clock.now() > deadline:
            return False
        snap, before = build.snapshot(), build.score
        self.put(slot, option)
        self.settle()
        if self.fit_requirements is not None:
            self.fit_requirements(min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS))
        if self.refill_main is not None:
            self.refill_main(min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS))
        if build.score > before + EPSILON:
            return True
        build.restore(snap)
        self.settle()
        return False

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
