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
from builder.search import unfinished  # (SEARCHD1 hook: the "Not finished" record)
from builder.search.context import ARMOUR_SLOTS, JEWELLERY_SLOTS, SLOT_TYPE, ItemOption, implicit_variant, implicit_variants
from builder.search.flasks import (
    CHARM_SLOTS, FLASK_SLOTS, RECOVERY_FIELDS, Charges, charges_per_second, is_flask_slot,
    recovery_per_second, slot_pool,
    uptime,
)
from builder.search.rares import (
    MAGIC_LIMIT, RUNE_KEEP, SCREEN_KEEP, Affix, AffixPools, RuneCatalog, magic_price, magic_text, rare_price, rare_text,
    NOT_IN_SEALED, exclusion_group, is_elemental_resistance, socket_count, unique_text, with_runes, FLUX_NAMES, fluxed,
    ALDUR_RUNES, AldurConverter, aldur_weapon, CATALYSTS, CATALYST_TYPES, CATALYSTS_PER_20, CATALYST_QUALITY,
    catalyst_matches, catalysed_text, catalyst_quality, implicit_tags, aldur_implicit_row, mod_lines, roll,
    INHERENT_SKILL,
)
from builder.search.objective import NO_DAMAGE_OFFSET
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


OWNED = "owned"  # an ItemOption's confidence for the user's own item: price 0, never counted in the budget


def owned_option(option: ItemOption) -> ItemOption:
    """A required unique the user owns (request.owned_uniques): the same item at price 0; its market price is read
    again for the report (run.py)."""
    return dataclasses.replace(option, price=0.0, confidence=OWNED)


def is_normal(option: ItemOption) -> bool:
    """A Normal-rarity item (a plain base, with or without augments in its sockets)."""
    first = option.raw.split("\n", 1)[0].strip()
    return not option.unique and not option.rare and (first == "Rarity: Normal" or not first.startswith("Rarity:"))


RESIST_SWAP_ATTEMPTS = 4  # slot pairs one resist_swap_round tries (two rare builds each; LOW, a search-effort setting)
# The slots resist_swap_round moves resistances into, in order (LOW: SEARCHB1's references put theirs on boots, shield
# and helmet; Weapon 1, the damage slot, never takes them)
RESIST_RECEIVERS = ("Boots", "Helmet", "Body Armour", "Weapon 2", "Belt", "Gloves", "Amulet", "Ring 1", "Ring 2", "Ring 3")
SWITCH_QUICK_FILL = "POE2_SEARCH_QUICK_FILL"  # "1": a full round's quick pick for every empty slot first (OFF by default)
SWITCH_RESIST_SWAP = "POE2_SEARCH_RESIST_SWAP"  # "1": gear_round's full rounds try resist_swap_round (OFF by default)
# (SEARCHB10) Of a full round's time, the share its slot loop leaves for resist_swap_round when the switch is on (LOW, a
# search-effort setting: SEARCHB2's probe POE2T6234 spent 21 s on 4 pairs). Without it the round never ran in a gate:
# at gate budgets every full round runs out inside its slot loop (each task of POE2T8410 / 8412 / 8416 / 8421 ended with
# gear.resume set), so resist_swap_round came after the deadline and tried no pair (8421 = 8406 and 8416 = 8415 task by
# task: the same DPS, gear seconds and evaluations).
RESIST_SWAP_SHARE = 0.3


def resist_swap_split(now: float, deadline: float) -> tuple[float, float]:
    """(the slot loop's deadline, resist_swap_round's) for a full gear round starting at `now`: with
    POE2_SEARCH_RESIST_SWAP=1 the slot loop stops RESIST_SWAP_SHARE of the round's time early, so the swap round gets
    that time even when the loop is cut; otherwise both are `deadline`, as before."""
    if os.environ.get(SWITCH_RESIST_SWAP, "0") != "1":
        return deadline, deadline
    return now + (1.0 - RESIST_SWAP_SHARE) * max(0.0, deadline - now), deadline
# (SEARCHC1) "0": a socket no rune scored for stays empty, as before (Gear.fill_sockets; ON by default: THE USER 23 Nov,
# never an empty rune socket; a filler never lowers the score)
SWITCH_FILL_SOCKETS = "POE2_SEARCH_FILL_SOCKETS"


def fill_sockets_enabled() -> bool:
    return os.environ.get(SWITCH_FILL_SOCKETS, "1") != "0"


# (SEARCHC1; THE USER 23 Nov: "it's usually within a budget to go for gear with more sockets") "1": a rare or normal item
# also tries one more socket than an Artificer's Orb gives (a natural drop) and two more (that copy corrupted), each priced
# by its route (Gear.extra_socket_trial). OFF by default until gated
SWITCH_EXTRA_SOCKETS = "POE2_SEARCH_EXTRA_SOCKETS"
# The natural extra socket's price (RESEARCH51 R51-5, 16 paired trade searches, LOW-MEDIUM; the manager 05:40): mostly a
# FLAT premium (+0.1 div common mods, +0.3-1 div good boots / gloves / triple-res bodies, +1-2.5 div better mod sets; the
# Oracle staff x1.7): max(the item + NATURAL_SOCKET_FLAT, the item x NATURAL_SOCKET_PREMIUM)
NATURAL_SOCKET_PREMIUM = 2.0
NATURAL_SOCKET_FLAT = 1.0
EXTRA_SOCKET_FLOOR = 0.01
# A Vaal Orb's chance to add the socket (MEDIUM-LOW, R50-1 3: four outcomes about equally likely, wiki and guide summaries,
# no file gives the weights); one corruption per item, so a miss costs the copy (the upper bound: no resale counted)
CORRUPT_SOCKET_CHANCE = 0.25


# (SEARCHC1) "1": a rare also tries the Void Flux route (Gear.flux_trial). OFF by default until gated
SWITCH_FLUX = "POE2_SEARCH_FLUX"


def flux_enabled() -> bool:
    return os.environ.get(SWITCH_FLUX, "0") == "1"


# (SEARCHC2) "1": a rare weapon also tries the Aldur rune routes (Gear.aldur_trial: Passion / Breath / Ire / Betrayal of
# Aldur turn every elemental damage modifier into the target element, tier for tier). OFF by default until gated
SWITCH_ALDUR = "POE2_SEARCH_ALDUR"


def aldur_enabled() -> bool:
    return os.environ.get(SWITCH_ALDUR, "0") == "1"


# (SEARCHC2) "1": a rare Ring / Amulet also tries catalyst quality (Gear.catalyst_trial). OFF by default until gated
SWITCH_CATALYST = "POE2_SEARCH_CATALYST"


def catalyst_enabled() -> bool:
    return os.environ.get(SWITCH_CATALYST, "0") == "1"


# (SEARCHC3; R55-12) The Breach Ring quality route: game mod EssenceBreach, local_maximum_quality_+ 20, a Level 1 prefix
BREACH_ESSENCE = "Essence of the Breach"
BREACH_ESSENCE_QUALITY = 20
BREACH_ANNULS = 3  # Orbs of Annulment to take its line off a 3-mod ring, the most it needs (R55-12)
BREACH_INFUSER = "Vaal Catalysing Infuser"  # (75%: a labelled row only, its step and corruption chance UNVERIFIED)


# (SEARCHC3) "1": a rare with a full suffix side also tries Serle's Triumph and a fourth suffix (Gear.serle_trial). OFF
# by default until gated
SWITCH_SERLE = "POE2_SEARCH_SERLE"
SERLE = "Serle's Triumph"
# The orbs that can fill its slot and their minimum modifier level (R50 / R55-6, poe2wiki via search: MEDIUM)
SERLE_EXALTS = (("Exalted Orb", 0), ("Greater Exalted Orb", 35), ("Perfect Exalted Orb", 50))


def serle_loop(share: float, per_try: float, others: list, market: float, prices: dict) -> tuple[float, str] | None:
    """The expected cost of landing one chosen suffix at `share` a try, `per_try` div a try (Gear.serle_trial): a miss is
    cleared by an Orb of Annulment, plain (any of the item's modifiers) or with Omen of Dextral Annulment (a suffix only),
    the cheaper; with m others it can hit, it takes a wanted one m / (m + 1) of the time: a fresh copy (`market`)."""
    annul = prices.get(influence.ANNUL)
    if share <= 0 or annul is None:
        return None
    tries = 1 / share
    clears = [(annul, len(others), "an Orb of Annulment")]
    omen = prices.get(influence.ANNULMENT["Suffix"])
    if omen is not None:
        clears.append((annul + omen, sum(1 for a in others if a.type == "Suffix"),
                       f"an Orb of Annulment + {influence.ANNULMENT['Suffix']}"))
    return min((tries * per_try + (tries - 1) * (clear + m / (m + 1) * market), f"each miss cleared by {how}")
               for clear, m, how in clears)


def serle_enabled() -> bool:
    return os.environ.get(SWITCH_SERLE, "0") == "1"


# (SEARCHC3; THE USER 23 Nov: "craft your own with the '60% increased augment' mod") "1": rare Gloves / Boots also try
# Essence of Horror's "60% increased effect of Socketed Augment Items" (Gear.augment_effect_trial). OFF by default until
# gated. The mod's game id; Gloves and Boots only (checked by R50-5: game EssenceMods' one row for this mod; poe2db's
# Essence of Horror page; the 0.4.0 note; trade 23 Nov: 0 helmets / body armours listed with the stat)
SWITCH_AUGMENT_EFFECT = "POE2_SEARCH_AUGMENT_EFFECT"
AUGMENT_EFFECT_MOD = "EssenceLocalRuneAndSoulCoreEffect1"
AUGMENT_EFFECT_TYPES = ("Gloves", "Boots")
HORROR = "Essence of Horror"
OMEN_SUFFIX = essences.OMENS["Suffix"]


# (SEARCHC4) The same stat on weapons: Sovereign Alloy's suffix, "(20-30)% increased effect of Socketed Augment Items"
# (game Mods AlloyEffectOfSocketedAugments1, local_socketed_items_effect_+%, family 3894 like Horror's; its classes are
# alloys.ALLOYS'). Its roll can't be chosen, so it is scored at the middle (25, MEDIUM) with rows at 20 and 30.
AUGMENT_EFFECT_ALLOY_MOD = "AlloyEffectOfSocketedAugments1"
AUGMENT_EFFECT_ALLOY_ROLL = 0.5


# (SEARCHC4; R51-10 #5) "1": a rare whose base grants a skill also tries Perfect Flux ("Upgrades the Skills on an item to
# Level 20", game id CurrencyUpgradeInherentTo20, drop level 75). OFF by default until gated. Uniques with a granted skill
# are priced by their listed level (granted_rolls.py) and are not offered this route yet (an undercount, said here).
SWITCH_PERFECT_FLUX = "POE2_SEARCH_PERFECT_FLUX"
PERFECT_FLUX = "Perfect Flux"
PERFECT_FLUX_LEVEL = 20
GRANTED_SKILL_LINE = re.compile(r"^((?:\{[^}]*\})*)Grants Skill: Level (\d+) (.+)$", re.M)  # (a ledger tag may lead it)


# (SEARCHC4; R51-10 #4, R52-5) "1": a rare also tries Astrid's Creativity ("Can have 1 additional Crafted Modifier") with
# two Alloys. An item holds one crafted modifier (0.5.0 notes), and the 0.5.0 notes call the Alloys' lines "crafted", so
# the rune is what lets a second Alloy line stay. OFF by default until gated.
SWITCH_ASTRID = "POE2_SEARCH_ASTRID"
ASTRID = "Astrid's Creativity"
ASTRID_KEEP = 3  # Alloy lines (best by a what-if with nothing lost) whose pairs are scored: a search-effort setting


def astrid_enabled() -> bool:
    return os.environ.get(SWITCH_ASTRID, "0") == "1"


def perfect_flux_enabled() -> bool:
    return os.environ.get(SWITCH_PERFECT_FLUX, "0") == "1"


def augment_effect_enabled() -> bool:
    return os.environ.get(SWITCH_AUGMENT_EFFECT, "0") == "1"


def augment_effect_source(base: dict) -> tuple[str, str] | None:
    """(the material, its mod id) that gives this base the augment-effect suffix; None when neither takes it."""
    if base["type"] in AUGMENT_EFFECT_TYPES:
        return HORROR, AUGMENT_EFFECT_MOD
    row = alloys.ALLOYS.get(AUGMENT_EFFECT_ALLOY_MOD)
    return (row[0], AUGMENT_EFFECT_ALLOY_MOD) if row and alloys.item_class(base) in row[1] else None


# Seconds Gear.fill_empty gives each filler item's socket fill (LOW: a search-effort setting; a fill is 1-3 what-if calls)
FILL_EMPTY_SOCKET_SECONDS = 20.0


def extra_sockets_enabled() -> bool:
    return os.environ.get(SWITCH_EXTRA_SOCKETS, "0") == "1"


def only_fillers(option: ItemOption | None) -> bool:
    """(SEARCHC1) every rune it holds past its bound ones is a filler (Gear.fill_sockets), none chosen by score."""
    return bool(option is not None and option.filler_runes
                and len(option.runes) - len(option.bound) == len(option.filler_runes))


SWITCH_PURITY = "POE2_SEARCH_PURITY_TRIALS"  # "1": full rounds try Shrine Sceptres for real (purity_trials; OFF by default)
# The skills whose Cost & Reservation Multiplier is 0%, so every support in their group reserves no Spirit (RESEARCH46
# R46-1, HIGH: GrantedEffectsPerLevel ReservationMultiplier 0 at all 40 levels on PurityOfFirePlayer, PurityOfIcePlayer,
# PurityOfLightningPlayer and ImpurityPlayer; checked by RESEARCH46 on every GrantedEffects row with per-level rows, 8,350
# effects, and the five forum threads, the newest 2026; NOT the other sceptre skills: Discipline, Malice, Heart of Ice,
# Fulmination and Skeletal Warrior are 100%). All four come only on Shrine Sceptres (ItemInherentSkills, R46-2)
FREE_SUPPORT_SKILLS = ("Purity of Fire", "Purity of Ice", "Purity of Lightning", "Impurity")
FREE_SUPPORT_LINE = re.compile(r"Grants Skill: (?:Level \S+ )?(%s)\s*$" % "|".join(FREE_SUPPORT_SKILLS), re.M)
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
    return sum(amount * (10.0 if hard else 1.0) for _, amount, hard in build.objective.deficits(build.stats)
               if hard is not None)  # (soft floors are no shortfall: objective.soft_cost's note)


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
    purity_rows: list  # (purity_trials) one row per Shrine Sceptre tried: before / after / kept
    fill_group = None  # fills an item-granted group's supports (gems.fill_granted_supports(index, deadline)); set by the search

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
        problems = self.setup_owned()
        wanted = list(self.ctx.compiled.request.require.uniques) + list(extra_uniques)
        if self.ctx.request.weapon_unique:
            wanted.insert(0, self.ctx.request.weapon_unique)
        pasted = {build.equipped[s].name for s in build.locked_slots if build.equipped.get(s) is not None}
        owned = set(getattr(self.ctx.request, "owned_uniques", None) or [])
        for name in dict.fromkeys(wanted):
            if name in pasted:
                continue  # (the user's own copy is in already)
            option = self.ctx.unique_option(name)
            slot = self.slot_for(option) if option else None
            if slot is None:
                problems.append(f"required unique {name} fits no slot of this skeleton")
                continue
            if name in owned:
                option = owned_option(option)
            self.put(slot, option)
            build.locked_slots.add(slot)
        for slot in self.slots:
            if slot in build.locked_slots or not (slot.startswith("Weapon") or slot == "Ring 3"):
                continue
            bases = [o for o in self.options(slot) if not o.unique]
            if bases:
                build.equip(slot, max(bases, key=lambda o: o.level))
        return problems

    def setup_owned(self) -> list[str]:
        """The user's own pasted items (request.owned_items, read by builder/request/owned.py at compile time): each
        equipped as pasted in its slot, LOCKED (no gear round replaces it) and priced 0, so the budget never counts it
        (OWNED). Returns problems: a slot this skeleton doesn't have, or a main / off hand it doesn't hold."""
        build, problems = self.build, []
        for read in getattr(self.ctx.compiled, "owned", None) or []:
            spec = self.slots.get(read.slot)
            if spec is None:
                problems.append(f"your {read.name} needs {read.slot}, which this skeleton doesn't have")
                continue
            if read.type not in spec.types or (spec.sub_type is not None and (read.sub_type or "") != spec.sub_type):
                problems.append(f"your {read.name} ({read.type}) doesn't fit this skeleton's {read.slot} "
                                f"({' / '.join(spec.types)})")
                continue
            option = ItemOption(read.name, read.base, read.type, read.sub_type, read.text, read.unique, 0.0, OWNED,
                                read.level, rare=read.rarity in ("RARE", "MAGIC"))
            self.put(read.slot, option)
            build.locked_slots.add(read.slot)
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
            penalty = max(objective.penalty(stats), objective.penalty(off_stats))
            out[i] = ((math.log1p(value) if value > 0.0 else -NO_DAMAGE_OFFSET) - penalty, stats)  # (Objective.score's rule)
        return out

    def put(self, slot: str, option: ItemOption | None) -> float:
        """Equip (or empty) a slot; a flask or charm slot is switched on only if its effect is sustained."""
        if not is_flask_slot(slot):
            result = self.build.equip(slot, option)
            if option is not None and len(option.runes) > len(option.filler_runes):
                result = self.evict_fillers(slot, option, result)
            return result
        info = self.flask_info([option.raw])[0] if option else {}
        self.build.extra_stats = self.recovery_stats({slot: info}, {slot: option.raw} if option else None)
        return self.build.equip(slot, option, active=option is not None and self.counts(slot, info, option.raw))

    def evict_fillers(self, slot: str, option: ItemOption, result: float) -> float:
        """(SEARCHC1; the manager's ruling, 23 Nov 05:00: a filler never holds a per-character limit a scoring pick
        wants) after `option` is equipped, each limit group its scoring runes take past the limit gives up fillers of
        that group in the other items (their sockets are re-filled while they hold only fillers: only_fillers)."""
        build = self.build
        scored = [n for n in option.runes if n not in {f for f, _ in option.filler_runes}]
        for group in {g[0] for n in scored if (g := self.runes.limit(n))}:
            cap = next(g[1] for n in scored if (g := self.runes.limit(n)) and g[0] == group)
            held = [(s, n) for s, o in build.equipped.items() if o is not None for n in o.runes
                    if (self.runes.limit(n) or ("",))[0] == group]
            over = len(held) - cap
            for other_slot, other in list(build.equipped.items()):
                if over <= 0:
                    break
                if other_slot == slot or other is None:
                    continue
                for name, why in list(other.filler_runes):
                    if over > 0 and (self.runes.limit(name) or ("",))[0] == group:
                        runes = list(other.runes)
                        runes.remove(name)
                        fillers = list(other.filler_runes)
                        fillers.remove((name, why))  # (one of them)
                        price = None if other.price is None else other.price - (self.runes.price(name) or 0.0)
                        other = dataclasses.replace(other, raw=with_runes(other.raw, runes), runes=tuple(runes),
                                                    filler_runes=tuple(fillers), price=price)
                        result = build.equip(other_slot, other)
                        over -= 1
        return result

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
        """_build_rare's item, with Perfect Flux on its granted skill when that pays (SEARCHC4, OFF by default)."""
        option = self._build_rare(slot, deadline, required, base_name, variant)
        if option is None or not perfect_flux_enabled():
            return option
        return self.perfect_flux_trial(slot, option, deadline) or option

    def perfect_flux_trial(self, slot: str, option: ItemOption, deadline: float) -> ItemOption | None:
        """(SEARCHC4, POE2_SEARCH_PERFECT_FLUX=1) The rare `option` with its base's granted skill at level 20, kept when
        the score after the Flux's price beats it. Deterministic. PoB scores a granted skill at the highest level the
        character meets (test_item_grants), so a character who can't use level 20 gains nothing and the Flux isn't bought."""
        if not option.rare or clock.now() > deadline:
            return None
        implicit = self.ctx.bases_by_name[option.base].get("implicit") or ""
        if not any(INHERENT_SKILL.match(re.sub(r"^(?:\{[^}]*\})*", "", line.strip())) for line in implicit.splitlines()):
            return None
        if not any(int(m.group(2)) < PERFECT_FLUX_LEVEL for m in GRANTED_SKILL_LINE.finditer(option.raw)):
            return None
        flux = 0.0 if self.ctx.ssf else self.ctx.payload.get("perfect_flux_price")
        if flux is None or (option.price or 0.0) + flux > self.left_for(slot) + 1e-9:
            return None
        raw = GRANTED_SKILL_LINE.sub(lambda m: f"{m.group(1)}Grants Skill: Level {PERFECT_FLUX_LEVEL} {m.group(3)}", option.raw)
        build = self.build
        key = alloys.alloy_key(raw)
        if option.alloy:  # (the item's DPS factor follows its new text, as in the what-if below)
            if getattr(build, "alloy_raws", None) is None:
                build.alloy_raws = {}
            build.alloy_raws[key] = option.alloy_scale
        (before, _), (after, _) = self.deltas(slot, [option.raw, raw])
        if after - self.lam * flux <= before + EPSILON:
            if option.alloy:
                build.alloy_raws.pop(key, None)
            return None
        note = (f"{PERFECT_FLUX} ({flux:.3g} div): sets the item's granted skill to level {PERFECT_FLUX_LEVEL} "
                f"(deterministic, R51-10). The character must meet that level's requirements")
        return dataclasses.replace(option, raw=raw, price=None if option.price is None else option.price + flux,
                                   craft_route=(*option.craft_route, note))

    def _build_rare(self, slot: str, deadline: float,
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
        cut = False  # (SEARCHD1 hook: True when the deadline, not the affixes, ended the fill)
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
        else:
            cut = True
        if not chosen:
            return None
        # (SEARCHD1 hook) the rare keeps the affixes it has either way; a cut one is said in the result, a finished one
        # clears its slot's row
        if cut and len(chosen) < sum(limits.values()):
            unfinished.note(self, "gear", slot, len(chosen), sum(limits.values()), "affixes",
                            "the item built for this slot stopped adding affixes when time ran out; the slot keeps the "
                            "best item found")
        else:
            unfinished.done(self, "gear", slot)
        utility: list = []
        if not magic:  # (SEARCHB5; THE USER 23 Nov: no empty affix slots)
            utility, current = self.fill_utility(slot, base, level, pools, limits, chosen, current, left, deadline)
            chosen = chosen + utility
        if not magic and aldur_enabled() and aldur_weapon(base):  # (SEARCHC2: an Aldur rune's element transform)
            transformed = self.aldur_trial(slot, base, base_option, level, pools, limits, shortlist, chosen, current,
                                           left, deadline)
            if transformed is not None:
                return transformed
        if not magic and flux_enabled():  # (SEARCHC1: the rare's elemental resistances turned to chaos by a Void Flux)
            fluxed = self.flux_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline)
            if fluxed is not None:
                return fluxed
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
        if not magic and catalyst_enabled() and base["type"] in CATALYST_TYPES:  # (SEARCHC2: catalyst quality)
            catalysed = self.catalyst_trial(slot, base, base_option, level, chosen, current, left, deadline)
            if catalysed is not None:
                return catalysed
        if not magic and augment_effect_enabled() and augment_effect_source(base):  # (SEARCHC3 / 4: Horror, the Alloy)
            effect = self.augment_effect_trial(slot, base, base_option, level, pools, chosen, left, deadline)
            if effect is not None:
                if serle_enabled() and SERLE not in effect.bound:  # (said where the pair wasn't the better route)
                    effect = dataclasses.replace(effect, craft_route=(*effect.craft_route, (
                        f"Not tried: {SERLE} on this item as well. The search takes the {effect.alloy} route first on "
                        f"this item, so a fourth suffix beside it is an open upgrade it hasn't priced")))
                return effect
        if not magic and serle_enabled():  # (SEARCHC3: Serle's Triumph and a fourth suffix)
            serle = self.serle_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline)
            if serle is not None:
                return serle
        if not magic and astrid_enabled():  # (SEARCHC4: Astrid's Creativity and two Alloys)
            astrid = self.astrid_trial(slot, base, base_option, level, pools, limits, chosen, current, left, deadline)
            if astrid is not None:
                return astrid
        price = None if self.ctx.ssf else price_of(slot, chosen)
        confidence = "ssf" if self.ctx.ssf else "estimate (magic)" if magic else "estimate (uncalibrated)"
        return ItemOption(f"{'Magic' if magic else 'Rare'} {base['type']}", base["name"], base["type"],
                          base.get("subType"), text(slot, base, level, chosen), False, price, confidence,
                          base_option.level, rare=True, affixes=tuple(line for a in chosen for line in a.lines),
                          utility=tuple(line for a in utility for line in a.lines))

    # (SEARCHB5; THE USER 23 Nov: "having empty mods on an item is stupid and wasteful") the utility classes an empty affix
    # slot is filled from, first match first: movement speed (the default 25% floor; boots roll it), the defences, the
    # resistances (they free other slots), then anything else the base rolls (quality of life)
    UTILITY_CLASSES = (
        ("movement speed", re.compile(r"increased Movement Speed", re.I)),
        ("defence", re.compile(r"maximum (Life|Energy Shield)|increased (Armour|Evasion|Energy Shield)|to (Armour|Evasion"
                               r" Rating)|Life Regeneration|Ward", re.I)),
        ("resistance", re.compile(r"Resistances?\b", re.I)),
        ("quality of life", re.compile(r".")),
    )

    def fill_utility(self, slot: str, base: dict, level: int, pools, limits: dict, chosen: list, current: float,
                     left: float, deadline: float) -> tuple[list, float]:
        """The affixes for every prefix / suffix slot the scoring affixes left empty (a bought rare has those slots
        filled anyway). The candidates the base rolls (no group taken, the tier-1 cap kept, within the budget at the rare
        model's price: a fuller item costs more there, x1.05 a low-tier affix, x4 a tier-1) are scored once, each alone
        on the item; then each empty slot in turn takes the first UTILITY_CLASSES class with any candidate that doesn't
        lower the item's score, its best by that what-if. One what-if call for the lot (a rare is built many times a run),
        and the full item is checked once: if the fills together lower its score, none is kept. Returns (the fills,
        the item's score with them: `current` when none is kept; SEARCHB6: build_rare scored it again before)."""
        if clock.now() > deadline:
            if len(chosen) < sum(limits.values()):  # (SEARCHD1 hook: the open affix slots stay open, said in the result)
                unfinished.note(self, "gear", slot, len(chosen), sum(limits.values()), "affixes",
                                "time ran out before its empty affix slots were filled; the item keeps the affixes it has")
            return [], current
        groups = {a.group for a in chosen}
        counts = {kind: sum(1 for a in chosen if a.type == kind) for kind in limits}
        if all(counts[kind] >= limits[kind] for kind in limits):
            return [], current
        options = [c for a in pools.pool(base["name"]) if counts.get(a.type, 0) < limits.get(a.type, 0)
                   and a.group not in groups and not a.influence and not a.desecrated and not a.crafted
                   for c in pools.choices(a, chosen) if rare_price(slot, chosen + [c]) <= left + 1e-9]
        if not options:
            return [], current
        scored = [s for s, _ in self.deltas(slot, [rare_text(slot, base, level, chosen + [a]) for a in options])]
        added: list = []
        while True:
            taken = chosen + added
            used = {a.group for a in taken}
            have = {kind: sum(1 for a in taken if a.type == kind) for kind in limits}
            open_ones = [(s, a) for s, a in zip(scored, options) if a.group not in used and have[a.type] < limits[a.type]
                         and s >= current - EPSILON and rare_price(slot, taken + [a]) <= left + 1e-9
                         and (not a.top or a in pools.choices(a, taken))]
            pick = None
            for _, pattern in self.UTILITY_CLASSES:
                fitting = [(s, a) for s, a in open_ones if any(pattern.search(line) for line in a.lines)]
                if fitting:
                    pick = max(fitting, key=lambda x: (x[0], -rare_price(slot, taken + [x[1]])))[1]
                    break
            if pick is None:
                break
            added.append(pick)
        if not added:
            return [], current
        full = self.deltas(slot, [rare_text(slot, base, level, chosen + added)])[0][0]
        return ([], current) if full < current - EPSILON else (added, full)

    def flux_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict, chosen: list,
                   current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC1, POE2_SEARCH_FLUX=1; THE USER 23 Nov: cheap triple-resistance boots + a Void Flux = the chaos
        resistance the build needs on one item, movement speed kept) the rare `chosen` (score `current`) bought with
        elemental resistance affixes and turned by a Void Flux (rares.void_fluxed: every Fire / Cold / Lightning
        resistance line to its chaos equivalent, poe2db's table). Tried: the rare as it is fluxed (if it has elemental
        resistances), and the rare with 1, 2, 3... of its open suffixes given the best elemental resistance affixes (a
        different element each, as bought) and then fluxed: converted chaos mods coexist (R50-4), which no plain rare can
        roll. Priced as the input rare (the rare price model) + 1 Void Flux (exchange); kept when it beats the plain rare
        after prices. Deterministic: no odds. Not for a Corrupted item (none is built here). (SEARCHC2) Blazing / Chilling /
        Crackling Flux the same way, each priced from the exchange (rares.flux_line)."""
        prices = self.ctx.payload.get("flux_prices") or {}
        if clock.now() > deadline:
            return None
        groups = {a.group for a in chosen}
        open_suffixes = limits.get("Suffix", 0) - sum(1 for a in chosen if a.type == "Suffix")
        extras = []  # the best affix of each elemental resistance group the base rolls, best tier first
        for affix in pools.pool(base["name"]):
            if is_elemental_resistance(affix) and affix.type == "Suffix" and affix.group not in groups:
                pick = next(iter(pools.choices(affix, chosen)), None)
                if pick is not None and pick.group not in {e.group for e in extras}:
                    extras.append(pick)
        trials = [list(chosen)] + [list(chosen) + extras[:k] for k in range(1, min(open_suffixes, len(extras)) + 1)]
        cases = []  # (target, flux price, bought, fluxed)
        # (SEARCHC2) the three elemental Fluxes too, behind the same switch: all of an item's other elemental
        # resistances into one element (poe2db: same numbers, the element word changed: rares.flux_line)
        for target, name in FLUX_NAMES.items():
            flux = 0.0 if self.ctx.ssf else prices.get(name)
            if flux is None:
                continue
            for bought in trials:
                out = fluxed(bought, target)
                if out is not None and rare_price(slot, bought) + flux <= left + 1e-9:
                    cases.append((target, flux, bought, out))
        if not cases:
            return None
        scored = self.deltas(slot, [rare_text(slot, base, level, out) for *_, out in cases])
        plain = current - self.lam * rare_price(slot, chosen)
        best = max(((s - self.lam * (rare_price(slot, bought) + flux), target, flux, bought, out)
                    for (s, _), (target, flux, bought, out) in zip(scored, cases)), key=lambda x: x[0])
        if best[0] <= plain + EPSILON:
            return None
        _, target, flux, bought, out = best
        name = FLUX_NAMES[target]
        price = None if self.ctx.ssf else rare_price(slot, bought) + flux
        into = ("every Fire / Cold / Lightning resistance into chaos resistance (poe2db's tier map, R50-4 HIGH)"
                if target == "Chaos" else f"every other elemental resistance into {target} resistance (same numbers; "
                                          "poe2db's table, SEARCHC2)")
        note = (f"{name}: buy the rare with {sum(1 for a in bought if is_elemental_resistance(a))} elemental "
                f"resistance affix(es) ({rare_price(slot, bought):.3g} div, the rare price model), then use a {name} "
                f"({flux:.3g} div) to turn {into} (deterministic). A Fractured modifier is not transformed (0.5.1 Hotfix 8): buy a copy with none among the lines it must change")
        return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                          rare_text(slot, base, level, out), False, price,
                          "ssf" if self.ctx.ssf else f"estimate (craft route: {name})", base_option.level, rare=True,
                          affixes=tuple(line for a in out for line in a.lines), craft_route=(note,))

    def catalyst_tags(self) -> dict[str, tuple[str, ...]]:
        if getattr(self, "_catalyst_tags", None) is None:
            self._catalyst_tags = {m["id"]: tuple(m.get("modTags") or ()) for m in self.ctx.data.mods.get("Item", [])}
        return self._catalyst_tags

    def catalyst_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, chosen: list, current: float,
                       left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC2, POE2_SEARCH_CATALYST=1; R51-10 #3) The rare Ring / Amulet `chosen` (score `current`) with its
        maximum quality (20%, or a Breach Ring's 40 / 45: rares.catalyst_quality) of one catalyst: every catalyst whose tag one of its affixes carries is scored (rares.catalysed_text:
        PoB scales the matching lines), priced as the rare + CATALYSTS_PER_20 per 20% x the catalyst (exchange); the best
        kept when it beats the plain rare after prices. Deterministic (a catalyst adds 1% quality each, R52-5)."""
        prices = self.ctx.payload.get("catalyst_prices") or {}
        if clock.now() > deadline or not chosen or (not prices and not self.ctx.ssf):
            return None
        tags = self.catalyst_tags()
        own = implicit_tags(base["name"])  # (SEARCHC3: a catalyst scales the implicit too, Lazuli Ring trade search)
        names = [n for n in CATALYSTS if (self.ctx.ssf or n in prices)
                 and (any(catalyst_matches(n, a, tags) for a in chosen)
                      or any(set(CATALYSTS[n][1]) & set(t) for t in own))]
        plain_text = rare_text(slot, base, level, chosen)
        top = catalyst_quality(base)  # (20, or the ring's own maximum: Breach Ring 40, Refined Breach Ring 45)
        # (SEARCHC3; R55-12, the manager 23 Nov 17:52) The Essence of the Breach route, +20 more: the quality stays after
        # the Essence's prefix is removed, so the finished item keeps all its affixes. (SEARCHC4, the manager's YES) Plain
        # Rings and Amulets too (40%; poe2db: the Essence works on Rings and Amulets), Breach Rings 60 / 65.
        # Materials: the Essence + BREACH_ANNULS Orbs of Annulment (the line off a 3-mod item, early: at most 3 tries)
        extra = (self.ctx.payload.get("breach_quality_prices") or {}).get(BREACH_ESSENCE)
        annul = self.ctx.influence_prices.get(influence.ANNUL)
        routes = [(top, 0.0)]
        if self.ctx.ssf or (extra is not None and annul is not None):
            routes.append((top + BREACH_ESSENCE_QUALITY, 0.0 if self.ctx.ssf else extra + BREACH_ANNULS * annul))
        cases = []
        for name in names:
            for quality, steps in routes:
                cost = 0.0 if self.ctx.ssf else CATALYSTS_PER_20 * quality / CATALYST_QUALITY * prices[name] + steps
                if rare_price(slot, chosen) + cost <= left + 1e-9:
                    cases.append((name, cost, quality))
        if not cases:
            return None
        scored = self.deltas(slot, [catalysed_text(plain_text, chosen, tags, n, q, own) for n, _, q in cases])
        plain = current - self.lam * rare_price(slot, chosen)
        # (ties go to the cheaper case: quality no line feels never wins on a tie)
        value, _, name, cost, quality = max(((s - self.lam * (rare_price(slot, chosen) + c), -c, n, c, q)
                                             for (s, _), (n, c, q) in zip(scored, cases)), key=lambda x: x[:2])
        if value <= plain + EPSILON:
            return None
        raised = sum(1 for a in chosen if catalyst_matches(name, a, tags)) \
            + sum(1 for t in own if set(CATALYSTS[name][1]) & set(t))
        count = CATALYSTS_PER_20 * quality // CATALYST_QUALITY
        note = (f"{name} x{count}: {quality}% quality raises the {raised} modifier"
                f"{'s' if raised != 1 else ''} with its tag ({'/'.join(CATALYSTS[name][1])}) by {quality}% "
                f"({cost:.3g} div; {CATALYSTS_PER_20} per {CATALYST_QUALITY}% is LOW-MEDIUM and UNVERIFIED, R52-5)")
        rows: tuple[str, ...] = ()
        if quality > top:
            infuser = (self.ctx.payload.get("breach_quality_prices") or {}).get(BREACH_INFUSER)
            whittle = [self.ctx.influence_prices.get(n) for n in ("Omen of Whittling", influence.CHAOS)]
            note += (f". Order: on a Rare {base['name']} with few modifiers, use {BREACH_ESSENCE} (its prefix raises the "
                     f"maximum by {BREACH_ESSENCE_QUALITY}), add the catalysts up to {quality}%, remove the Essence's line "
                     f"with Orbs of Annulment (at most {BREACH_ANNULS} on a 3-mod {base['type'].lower()}; the quality "
                     f"stays, R55-12 HIGH), then craft the rest, so no affix slot is lost. Don't use Omen of Catalysing "
                     f"Exaltation afterwards (it consumes the quality)")
            if top > CATALYST_QUALITY:
                note += ". The ring base's own price is UNVERIFIED (not on the exchange)"
            if None not in whittle:
                rows += (f"Row: removing the line from a finished ring instead: Omen of Whittling + Chaos Orb, "
                         f"{sum(whittle):.3g} div (MEDIUM)",)
            if quality == 65 and infuser is not None:
                rows += (f"Row, NOT counted: 75% with {BREACH_INFUSER} ({infuser:.3g} div each) while the Essence's line "
                         f"is still on; its step per use and its chance to corrupt the ring are UNVERIFIED",)
        price = None if self.ctx.ssf else rare_price(slot, chosen) + cost
        return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                          catalysed_text(plain_text, chosen, tags, name, quality, own), False, price,
                          "ssf" if self.ctx.ssf else "estimate (craft route: catalysts)", base_option.level, rare=True,
                          affixes=tuple(line for a in chosen for line in a.lines), craft_route=(note, *rows))

    ALDUR_KEEP = 6  # converted affixes of each kind (by a what-if alone) the Aldur route's greedy fill tries (a budget)

    def aldur_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict,
                    shortlist: list, chosen: list, current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC2, POE2_SEARCH_ALDUR=1; THE USER 23 Nov: "the breath/ire/passion/betrayal of aldur augments that change
        a weapon's elemental types around") The rare weapon built for an Aldur rune: affixes picked as they roll on the
        base (their own groups and spawn tags, so added Fire, Cold and Lightning may sit side by side), scored as the
        rune leaves them (rares.AldurConverter: poe2db's 78-row table, tier for tier; the implicit untouched, UNVERIFIED),
        the rune socket-bound in one socket. For each rune: the converted affixes screened alone, the best ALDUR_KEEP of
        each kind plus the plain shortlist's affixes the rune leaves alone, then a greedy fill while the score after
        prices rises. Priced as the rare bought with the SOURCE affixes (the rare price model) + the rune (exchange).
        Kept when it beats the plain rare with its best ordinary rune in that socket (the rune takes it for good). PoB
        ignores the rune's transform line (R51-10 HIGH), so this rewrite is what scores it; the rune's Bonded line
        counts only with an unlock (PoB's own switch)."""
        if clock.now() > deadline or socket_count(base, False) < 1:
            return None
        converters = self.__dict__.setdefault("_aldur_converters", {})
        if pools.roll not in converters:
            converters[pools.roll] = AldurConverter(self.ctx, pools.roll)
        converter = converters[pools.roll]
        used = self.runes_used(slot)
        pool = pools.pool(base["name"])
        ssf = self.ctx.ssf
        best = None  # (value, rune, bought, converted, price)
        for target, rune in ALDUR_RUNES.items():
            rune_price = 0.0 if ssf else self.runes.price(rune)
            if rune_price is None or clock.now() > deadline \
                    or rune in self.ctx.compiled.filters.forbidden_augments \
                    or ((limit := self.runes.limit(rune)) and used.get(limit[0], 0) >= limit[1]):
                continue
            convert = {a.id: converter.convert(a, target) for a in pool}
            changed = [a for a in pool if convert[a.id] is not None and convert[a.id] is not a]
            if not changed:
                continue
            screen = self.deltas(slot, [rare_text(slot, base, level, [convert[a.id]], runes=[rune]) for a in changed])
            ranked = [a for _, a in sorted(zip((s for s, _ in screen), changed), key=lambda x: -x[0])]
            trial = [a for kind in limits for a in [x for x in ranked if x.type == kind][:self.ALDUR_KEEP]]
            trial += [a for a in shortlist if convert.get(a.id) is a]  # (affixes the rune leaves as they are)

            def text(affixes, convert=convert, rune=rune):
                return rare_text(slot, base, level, [convert[a.id] for a in affixes], runes=[rune])

            def cost(affixes, rune_price=rune_price):
                return rare_price(slot, affixes) + rune_price

            picked: list = []
            value = None
            while clock.now() < deadline:
                groups = {a.group for a in picked}
                counts = {kind: sum(1 for a in picked if a.type == kind) for kind in limits}
                options = [c for a in trial if a.group not in groups and counts.get(a.type, 0) < limits.get(a.type, 0)
                           for c in pools.choices(a, picked) if convert.get(c.id) is not None
                           and cost(picked + [c]) <= left + 1e-9]
                if not options:
                    break
                scored = self.deltas(slot, [text(picked + [c]) for c in options])
                gain, pick = max(((s - self.lam * cost(picked + [c]), c) for (s, _), c in zip(scored, options)),
                                 key=lambda x: x[0])
                if value is not None and gain <= value + EPSILON:
                    break
                picked, value = picked + [pick], gain
            if value is not None and (best is None or value > best[0]):
                best = (value, rune, picked, [convert[a.id] for a in picked], cost(picked), target)
        if best is None:
            return None
        value, rune, bought, converted, price, target = best
        # (THE USER 23 Nov: no empty affix slots) SEARCHB5's fill on the SOURCE affixes (what the bought rare rolls),
        # each fill then converted like the rest; kept when the converted item with the rune scores no worse after prices
        rune_price = price - rare_price(slot, bought)
        source = self.deltas(slot, [rare_text(slot, base, level, bought)])[0][0]
        utility, _ = self.fill_utility(slot, base, level, pools, limits, bought, source, left - rune_price, deadline)
        utility = [a for a in utility if converters[pools.roll].convert(a, target) is not None]
        if utility and clock.now() < deadline:
            filled = converted + [converters[pools.roll].convert(a, target) for a in utility]
            s = self.deltas(slot, [rare_text(slot, base, level, filled, runes=[rune])])[0][0]
            filled_price = rare_price(slot, bought + utility) + rune_price
            if s - self.lam * filled_price >= value - EPSILON:
                bought, converted, price = bought + utility, filled, filled_price
                value = s - self.lam * filled_price
        plain = current - self.lam * rare_price(slot, chosen)
        others = [n for n in self.runes.candidates(base, False) if self.runes.price(n) is not None
                  and not ((limit := self.runes.limit(n)) and used.get(limit[0], 0) >= limit[1])
                  and rare_price(slot, chosen) + self.runes.price(n) <= left + 1e-9]
        if others and clock.now() < deadline:
            socketed = self.deltas(slot, [rare_text(slot, base, level, chosen, runes=[n]) for n in others])
            plain = max([plain] + [s - self.lam * (rare_price(slot, chosen) + self.runes.price(n))
                                   for (s, _), n in zip(socketed, others)])
        if value <= plain + EPSILON:
            return None
        moved = sum(1 for a, c in zip(bought, converted) if c is not a)
        note = (f"{rune}: buy the rare with these affixes ({rare_price(slot, bought):.3g} div, the rare price model; "
                f"{moved} of them in an element the rune transforms), then socket {rune} first "
                f"({0.0 if ssf else self.runes.price(rune):.3g} div, socket-bound): every such damage modifier becomes "
                f"its equivalent, tier for tier (deterministic; poe2db's 78-row table). Modifiers added after socketing "
                f"are not transformed. The base's implicit is assumed untouched (UNVERIFIED, R52-5, R55-4). A Fractured modifier is not transformed (0.5.1 Hotfix 8): buy a copy with none among the lines it must change")
        implicit = aldur_implicit_row(base["name"], target)
        return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                          rare_text(slot, base, level, converted, runes=[rune]), False, None if ssf else price,
                          "ssf" if ssf else f"estimate (craft route: {rune})", base_option.level, rare=True,
                          affixes=tuple(line for a in converted for line in a.lines), runes=(rune,), bound=(rune,),
                          craft_route=(note, *implicit))

    def serle_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict, chosen: list,
                    current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC3, POE2_SEARCH_SERLE=1; R51-10 #3, R52-5, R55-6; the manager 23 Nov 14:26) The rare with a full suffix
        side, Serle's Triumph ("+1 Suffix Modifier allowed", socket-bound, limit 1, rares only, never Corrupted /
        Sanctified) and a fourth suffix. Two routes, the score picks:
        - RANDOM FILL (R55-6): one Exalted, Greater or Perfect Exalted Orb, keep what lands. Valued as the Alloys are (the
          manager, 18 Nov): DPS at the mean over every suffix row still open, the floors at the worst (the item shown).
        - TARGETED: repeat until a chosen suffix at its tier or better lands; a miss is cleared by an Orb of Annulment
          (with or without Omen of Dextral Annulment, the cheaper), which takes a wanted modifier m / (m + 1) of the time,
          counted as a fresh copy of the rare with its rune.
        With the prefixes full the Exalt can only land on the open suffix, so no Omen of Dextral Exaltation is priced
        (R55-6: a reading of the affix rules, UNVERIFIED for this slot; the Omen version is a row). With a prefix open the
        Omen is priced in. Odds: equal weight per modifier row (LOW; R33-23). The rune costs the socket: kept only when it
        beats the plain rare with its best ordinary rune there, after prices."""
        suffixes = sum(1 for a in chosen if a.type == "Suffix")
        if clock.now() > deadline or socket_count(base, False) < 1 or not limits.get("Suffix") \
                or suffixes < limits["Suffix"] or SERLE in self.ctx.compiled.filters.forbidden_augments:
            return None
        ssf = self.ctx.ssf
        rune_price = 0.0 if ssf else self.runes.price(SERLE)
        used = self.runes_used(slot)
        limit = self.runes.limit(SERLE)
        names = self.runes.candidates(base, False)
        if rune_price is None or SERLE not in names or (limit and used.get(limit[0], 0) >= limit[1]):
            return None
        prices = {m: 0.0 for m in influence.MATERIALS} if ssf else self.ctx.influence_prices
        orbs = {n: 0.0 for n, _ in SERLE_EXALTS} if ssf else (self.ctx.payload.get("serle_prices") or {})
        exalts = [(n, floor, orbs[n]) for n, floor in SERLE_EXALTS if n in orbs]
        omen_price = prices.get(influence.EXALTATION["Suffix"])
        prefixes_full = sum(1 for a in chosen if a.type == "Prefix") >= limits.get("Prefix", 0)
        if not exalts or (not prefixes_full and omen_price is None):
            return None
        omen = 0.0 if prefixes_full else omen_price
        market = rare_price(slot, chosen) + rune_price
        build, objective = self.build, self.build.objective

        def text(extra) -> str:
            return rare_text(slot, base, level, chosen + [extra], runes=[SERLE])

        # What each open suffix row does: the top row of each group first; a group whose top row moves nothing has
        # lower tiers that move nothing either (lower tiers of a group never score higher: AffixPools.pool)
        rows = pools.side_outcomes(base["name"], "Suffix", chosen)
        if not rows:
            return None
        bare = self.deltas(slot, [rare_text(slot, base, level, chosen, runes=[SERLE])])[0][0]
        tops: dict[str, Affix] = {}
        for affix, _ in rows:
            if affix.group not in tops or affix.level > tops[affix.group].level:
                tops[affix.group] = affix
        heads = list(tops.values())
        read: dict[str, tuple[float, dict]] = {}
        for affix, (s, stats) in zip(heads, self.deltas(slot, [text(a) for a in heads])):
            read[affix.id] = (s, self.without_registered(text(affix), stats))
        moved = {a.group for a in heads if abs(read[a.id][0] - bare) > EPSILON}
        rest = [a for a, _ in rows if a.id not in read and a.group in moved]
        if rest and clock.now() < deadline:
            for affix, (s, stats) in zip(rest, self.deltas(slot, [text(a) for a in rest])):
                read[affix.id] = (s, self.without_registered(text(affix), stats))
        for affix, _ in rows:
            read.setdefault(affix.id, read[tops[affix.group].id])

        fill = None  # (value, orb, price, the worst row, scale, the rows, their DPS)
        for orb, floor, orb_price in exalts:
            price = market + orb_price + omen
            if price > left + 1e-9:
                continue
            landed = pools.side_outcomes(base["name"], "Suffix", chosen, floor)
            scored = [(objective.score(read[a.id][1]), n) for n, (a, _) in enumerate(landed)]
            worst = min(scored)[1]
            dps = [objective.dps(read[a.id][1]) for a, _ in landed]
            weights = [w for _, w in landed]
            mean = sum(d * w for d, w in zip(dps, weights)) / sum(weights)
            scale = mean / dps[worst] if dps[worst] > 0 else 1.0
            stats = dict(read[landed[worst][0].id][1])
            stats[alloys.ALLOY_FIELD] = build.alloy_product(slot) * scale
            value = objective.score(stats) - self.lam * price
            if fill is None or value > fill[0]:
                fill = (value, orb, price, landed[worst][0], scale, landed, dps)

        aimed = None  # (value, affix, price, orb, share, how, the Omen version's price)
        for group_affix in pools.pool(base["name"]):
            if group_affix.type != "Suffix" or group_affix.group in {a.group for a in chosen} or group_affix.influence \
                    or group_affix.desecrated or group_affix.crafted:
                continue
            for option in pools.choices(group_affix, chosen):
                if option.id not in read:
                    continue
                for orb, floor, orb_price in exalts:
                    share = pools.exalt_share(base["name"], option, chosen, floor)
                    found = serle_loop(share, orb_price + omen, chosen, market, prices)
                    if found is None or market + found[0] > left + 1e-9:
                        continue
                    value = read[option.id][0] - self.lam * (market + found[0])
                    if aimed is None or value > aimed[0]:
                        row = None if omen or omen_price is None else \
                            serle_loop(share, orb_price + omen_price, chosen, market, prices)
                        aimed = (value, option, market + found[0], orb, share, found[1], row and market + row[0])
        plain = current - self.lam * rare_price(slot, chosen)
        others = [n for n in names if n != SERLE and self.runes.price(n) is not None
                  and not ((lim := self.runes.limit(n)) and used.get(lim[0], 0) >= lim[1])
                  and rare_price(slot, chosen) + self.runes.price(n) <= left + 1e-9]
        if others and clock.now() < deadline:
            socketed = self.deltas(slot, [rare_text(slot, base, level, chosen, runes=[n]) for n in others])
            plain = max([plain] + [s - self.lam * (rare_price(slot, chosen) + self.runes.price(n))
                                   for (s, _), n in zip(socketed, others)])
        best_value = max([r[0] for r in (fill, aimed) if r is not None], default=None)
        if best_value is None or best_value <= plain + EPSILON:
            return None
        head = (f"{SERLE}: buy the rare at its full suffixes ({rare_price(slot, chosen):.3g} div, the rare price model), "
                f"socket {SERLE} ({rune_price:.3g} div, socket-bound: it takes this socket's rune), then ")
        tail = ("Odds: equal weight per modifier row (LOW). Do all this before any corruption: "
                f"{SERLE} can't go in a Corrupted or Sanctified item")
        side = ("the prefixes are full, so no Omen of Dextral Exaltation is priced (UNVERIFIED for this slot, R55-6)"
                if prefixes_full else f"a prefix is open, so Omen of Dextral Exaltation ({omen:.3g} div) is priced in")
        if aimed is not None and (fill is None or aimed[0] > fill[0]):
            _, affix, price, orb, share, how, row = aimed
            note = (f"{head}aim for \"{'; '.join(affix.lines)}\" or a better tier: {orb} each try, about {share:.1%} a try, "
                    f"{how}, {price - market:.3g} div expected; {side}. {tail}")
            rows_text = () if row is None else (f"Row: with Omen of Dextral Exaltation on every try: {row:.3g} div",)
            if fill is not None:
                rows_text += (f"Row: one {fill[1]} and keep what lands instead: {fill[2]:.3g} div",)
            affixes = chosen + [affix]
            return ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                              rare_text(slot, base, level, affixes, runes=[SERLE]), False, None if ssf else price,
                              "ssf" if ssf else f"estimate (craft route: {SERLE}, targeted)", base_option.level, rare=True,
                              affixes=tuple(line for a in affixes for line in a.lines), runes=(SERLE,), bound=(SERLE,),
                              craft_route=(note, *rows_text))
        _, orb, price, affix, scale, landed, dps = fill
        typical = alloys.typical(dps)
        note = (f"{head}one {orb} and keep what lands: {len(landed)} possible suffixes; {side}. The build shows the worst "
                f"outcome for the floors, DPS at the mean. {tail}")
        report = (f"{SERLE} + one {orb}: a random fourth suffix ({len(landed)} outcomes); the floors read the worst (shown), "
                  f"DPS the mean (x{scale:.3f} of the worst's) (the manager's ruling, 18 Nov)",
                  f"Typical outcome: \"{'; '.join(landed[typical][0].lines)}\" (DPS {dps[typical]:,.0f})",
                  f"Worst case: \"{'; '.join(affix.lines)}\"")
        rows_text = () if aimed is None else (f"Row: aim for \"{'; '.join(aimed[1].lines)}\" instead: {aimed[2]:.3g} div",)
        affixes = chosen + [affix]
        option = ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                            rare_text(slot, base, level, affixes, runes=[SERLE]), False, None if ssf else price,
                            "ssf" if ssf else f"estimate (craft route: {SERLE}, random fill)", base_option.level, rare=True,
                            affixes=tuple(line for a in affixes for line in a.lines), runes=(SERLE,), bound=(SERLE,),
                            craft_route=(note, *rows_text), alloy=SERLE, alloy_scale=scale, alloy_report=report,
                            augment="serle_fill")
        if not hasattr(build, "alloy_raws") or build.alloy_raws is None:
            build.alloy_raws = {}
        build.alloy_raws[alloys.alloy_key(option.raw)] = scale
        return option

    def augment_effect_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, chosen: list,
                             left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC3, POE2_SEARCH_AUGMENT_EFFECT=1; THE USER 23 Nov; R50-5, R51-8) Rare Gloves / Boots with Essence of
        Horror's suffix, "60% increased effect of Socketed Augment Items" (every number of every socketed augment line
        x1.6, floored; PoB scales them). The essence removes a random modifier first, or a random suffix with Omen of
        Dextral Crystallisation, so each is scored over which modifier is lost, by the Alloys' ruling (the manager, 18 Nov:
        DPS at the mean, the floors at the worst outcome; the item shown is the worst). The value is all in the runes, so
        both sides are scored with their sockets filled, and the route is kept when it beats the plain rare as filled,
        after prices. Priced as the rare + the essence (+ the Omen). (SEARCHC4) Weapons take the same suffix from Sovereign
        Alloy (20-30%: augment_effect_source). The new suffix must fit whichever modifier is lost: with the suffixes full,
        the route without an Omen starts from the rare made with its least useful suffix left out (alloys.py's rule)."""
        source = augment_effect_source(base)
        if source is None or clock.now() > deadline or socket_count(base, False) < 1 or not chosen:
            return None
        material, mod_id = source
        mod = next((m for m in self.ctx.data.mods.get("Item", []) if m["id"] == mod_id), None)
        if mod is None or mod.get("level", 0) > level or exclusion_group(mod["group"]) in {a.group for a in chosen}:
            return None
        ssf = self.ctx.ssf
        prices = self.ctx.payload.get("augment_effect_prices") or {}
        essence = 0.0 if ssf else (prices.get(HORROR) if material == HORROR else self.ctx.alloy_prices.get(material))
        room = affix_limits(base).get("Suffix", 0)
        if essence is None or room < 1:
            return None
        build = self.build
        objective = build.objective
        affix = pools.affix(mod, mod["group"], mod["type"], 1, 1, (0.0, 0.0))
        if material != HORROR:  # (the Alloy's roll can't be chosen: the middle, with rows at its two ends)
            affix = dataclasses.replace(affix, lines=tuple(roll(x, AUGMENT_EFFECT_ALLOY_ROLL) for x in mod_lines(mod)))
        plain = ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                           rare_text(slot, base, level, chosen), False, None if ssf else rare_price(slot, chosen),
                           "ssf" if ssf else "estimate (uncalibrated)", base_option.level, rare=True,
                           affixes=tuple(line for a in chosen for line in a.lines))
        plain_filled = self.socketed(slot, plain, deadline, trial=True)
        plain_value = self.adjusted(self.deltas(slot, [plain_filled.raw])[0][0], plain_filled)
        suffixes = [a for a in chosen if a.type == "Suffix"]
        open_rare = list(chosen)
        if len(suffixes) >= room:  # (no free suffix: a lost prefix would leave one suffix too many)
            cuts = [[a for a in chosen if a is not out] for out in suffixes]
            cut_scores = self.deltas(slot, [rare_text(slot, base, level, c) for c in cuts])
            open_rare = cuts[max(range(len(cuts)), key=lambda n: (cut_scores[n][0], -n))]
        trials = [(None, essence, open_rare, False)] if open_rare else []
        # (the Crystallisation Omens name Perfect and Corrupted Essences, so the Alloy route takes none)
        omen = None if material != HORROR else 0.0 if ssf else prices.get(OMEN_SUFFIX)
        if omen is not None and suffixes:
            trials.append(("Suffix", essence + omen, list(chosen), False))
        # (SEARCHC4; the manager's item 4) Serle's Triumph first: its extra suffix slot holds the new line, so a full rare
        # needs no suffix left out. It takes a socket for good, so the pair needs two: a copy that dropped with the extra
        # socket when the base's usual count is one, priced as extra_socket_trial's "natural" route. Whether the Essence
        # treats Serle's slot as open is a reading of the affix rules (UNVERIFIED), said in the route.
        serle_price = 0.0 if ssf else self.runes.price(SERLE)
        usual = socket_count(base, False)
        serle_limit = self.runes.limit(SERLE)
        pair_sockets = max(usual, 2)
        if serle_enabled() and len(suffixes) >= room and serle_price is not None \
                and pair_sockets <= (base.get("socketLimit") or 0) - 1 \
                and SERLE not in self.ctx.compiled.filters.forbidden_augments \
                and SERLE in self.runes.candidates(base, False) \
                and not (serle_limit and self.runes_used(slot).get(serle_limit[0], 0) >= serle_limit[1]):
            item = rare_price(slot, chosen) + essence
            premium = 0.0 if ssf or usual >= 2 else \
                max(item * NATURAL_SOCKET_PREMIUM, item + NATURAL_SOCKET_FLAT) - item
            trials.append((None, essence + serle_price + premium, list(chosen), True))
        best = None  # (value, option)
        for side, cost, kept, with_serle in trials:
            held = (SERLE,) if with_serle else ()
            bought = rare_price(slot, kept)
            price = bought + cost
            if price > left + 1e-9 or clock.now() > deadline:
                continue
            losable = [a for a in kept if side is None or a.type == side]
            outcomes = [[a for a in kept if a is not lost] + [affix] for lost in losable]
            texts = [rare_text(slot, base, level, o) for o in outcomes]
            # The runes for the worst outcome: found with the plain rare's runes, then refilled on it
            first = [with_runes(t, [*held, *plain_filled.runes][:pair_sockets if with_serle else None]) for t in texts]
            screened = [s for s, _ in self.deltas(slot, first)]
            at = min(range(len(first)), key=lambda n: (screened[n], n))
            shell = dataclasses.replace(plain, raw=texts[at], price=None if ssf else price, runes=held, bound=held,
                                        sockets=pair_sockets if with_serle else plain.sockets,
                                        affixes=tuple(line for a in outcomes[at] for line in a.lines))
            filled = self.socketed(slot, shell, deadline, trial=True)
            if len(filled.runes) <= len(held):  # (nothing for the line to raise but a rune with no number)
                continue
            with_them = [with_runes(t, list(filled.runes)) for t in texts]
            scored = [(objective.score(bare), bare) for bare in
                      (self.without_registered(t, stats) for t, (_, stats) in zip(with_them, self.deltas(slot, with_them)))]
            dps = [objective.dps(stats) for _, stats in scored]
            worst = min(range(len(scored)), key=lambda n: (scored[n][0], n))
            scale = alloys.mean_scale(dps, worst)
            stats = dict(scored[worst][1])
            stats[alloys.ALLOY_FIELD] = build.alloy_product(slot) * scale
            value = objective.score(stats) - self.lam * (filled.price or 0.0)
            if value <= plain_value + EPSILON or (best is not None and value <= best[0]):
                continue
            typical = alloys.typical(dps)
            lost = lambda n: "; ".join(losable[n].lines)  # noqa: E731
            how = f" with {OMEN_SUFFIX} (a suffix only)" if side else ""
            rolls: tuple[str, ...] = ()
            if material != HORROR:  # (the same outcome at the roll's two ends)
                ends = [dataclasses.replace(affix, lines=tuple(roll(x, r) for x in mod_lines(mod))) for r in (0.0, 1.0)]
                end_texts = [with_runes(rare_text(slot, base, level, [a for a in outcomes[worst] if a is not affix] + [e]),
                                        list(filled.runes)) for e in ends]
                end_dps = [objective.dps(self.without_registered(t, st))
                           for t, (_, st) in zip(end_texts, self.deltas(slot, end_texts))]
                rolls = (f"Row: the Alloy's roll is random; scored at the middle ({affix.lines[0]}). Worst outcome's DPS "
                         f"at the lowest roll {end_dps[0]:,.0f}, at the highest {end_dps[1]:,.0f} (MEDIUM)",)
            report = (f"{material}{how}: removes a random modifier ({len(losable)} outcomes) and adds "
                      f"\"{'; '.join(affix.lines)}\"; the floors read the worst outcome (shown in the build), DPS the mean "
                      f"over all {len(losable)} (x{scale:.3f} of the worst's) (the manager's ruling, 18 Nov)",
                      f"Typical outcome: loses \"{lost(typical)}\" (DPS {dps[typical]:,.0f})",
                      f"Worst case: loses \"{lost(worst)}\" (DPS {dps[worst]:,.0f}; mean {sum(dps) / len(dps):,.0f})",
                      *rolls)
            scaling = "x1.6" if material == HORROR else "raised by the Alloy's 20-30%"
            note = (f"{material}{how}: buy the rare ({bought:.3g} div, the rare price model), use the "
                    f"{'essence' if material == HORROR else 'Alloy'} ({cost:.3g} div with any Omen): every socketed "
                    f"augment's numbers {scaling}, rounded down (R51-8). Socket the runes after it. A +1 or a line with "
                    f"no number gains nothing")
            if with_serle:
                note += (f". {SERLE} goes in first (socket-bound, {serle_price:.3g} div) so its extra suffix slot holds the "
                         f"new line; this needs a {pair_sockets}-socket copy (priced in). Whether the {material} treats "
                         f"that slot as open is UNVERIFIED. Do all this before any corruption")
            best = (value, ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"),
                                      with_runes(texts[worst], list(held)) if with_serle else texts[worst],
                                      False, None if ssf else price,
                                      "ssf" if ssf else f"estimate (craft route: {material})", base_option.level,
                                      rare=True, affixes=tuple(line for a in outcomes[worst] for line in a.lines),
                                      alloy=material, alloy_scale=scale, alloy_report=report,
                                      augment="essence" if material == HORROR else "alloy", runes=held, bound=held,
                                      sockets=pair_sockets if with_serle else 0, craft_route=(note,)))
        if best is None:
            return None
        option = best[1]
        if not hasattr(build, "alloy_raws") or build.alloy_raws is None:
            build.alloy_raws = {}
        build.alloy_raws[alloys.alloy_key(option.raw)] = option.alloy_scale
        return option

    def astrid_trial(self, slot: str, base: dict, base_option: ItemOption, level: int, pools, limits: dict, chosen: list,
                     current: float, left: float, deadline: float) -> ItemOption | None:
        """(SEARCHC4, POE2_SEARCH_ASTRID=1) The rare with Astrid's Creativity in a socket and two Alloy lines. Each Alloy
        removes a random modifier, and the second may remove the first's line (the text says "a random modifier"; LOW,
        no source settles it), so every sequence of the two removals is an outcome. By the Alloys' ruling (the manager,
        18 Nov): DPS at the mean over the sequences, the floors at the worst (the item shown). Each line needs a free
        slot of its own side, so a full side starts from the rare with its least useful modifiers left out. Then the
        socket takes the build's best ordinary rune in Astrid's place (SEARCHC5; AUDIT38 F2: Astrid's isn't socket-bound,
        SoulCores IsSocketBound false, poe2db "can be replaced by other Augment items", and 8 listings hold both crafted
        lines with another augment socketed; before, the route kept Astrid's there, a LOW guess that cost the rune).
        Kept only when it beats the plain rare with its best ordinary rune there, after prices. Priced as the rare + the
        rune + both Alloys + the rune that replaces it."""
        if clock.now() > deadline or socket_count(base, False) < 1 or not chosen \
                or ASTRID in self.ctx.compiled.filters.forbidden_augments:
            return None
        ssf = self.ctx.ssf
        rune_price = 0.0 if ssf else self.runes.price(ASTRID)
        used = self.runes_used(slot)
        limit = self.runes.limit(ASTRID)
        names = self.runes.candidates(base, False)
        if rune_price is None or ASTRID not in names or (limit and used.get(limit[0], 0) >= limit[1]):
            return None
        groups = {a.group for a in chosen}
        rows = [(name, pools.affix(mod, mod["group"], mod["type"], 1, 1, (0.0, 0.0)), 0.0 if ssf else self.ctx.alloy_prices[name])
                for name, mod in alloys.rows_for(base, level, self.alloy_mods)
                if mod["type"] in limits and exclusion_group(mod["group"]) not in groups
                and (ssf or name in self.ctx.alloy_prices)]
        if len(rows) < 2:
            return None
        # (each line screened alone, an item the game can hold: chosen + a line can be one modifier over the limit)
        screen = self.deltas(slot, [rare_text(slot, base, level, [r[1]], runes=[ASTRID]) for r in rows])
        ranked = [r for _, _, r in sorted(((s, -n, r) for n, ((s, _), r) in enumerate(zip(screen, rows))),
                                          key=lambda x: x[:2], reverse=True)][:ASTRID_KEEP]
        plain = current - self.lam * rare_price(slot, chosen)
        others = [n for n in names if n != ASTRID and self.runes.price(n) is not None
                  and not ((lim := self.runes.limit(n)) and used.get(lim[0], 0) >= lim[1])
                  and rare_price(slot, chosen) + self.runes.price(n) <= left + 1e-9]
        swap: list[str] = []  # (the rune that replaces Astrid's: the best ordinary one on the plain rare)
        if others:
            socketed = self.deltas(slot, [rare_text(slot, base, level, chosen, runes=[n]) for n in others])
            values = [s - self.lam * (rare_price(slot, chosen) + self.runes.price(n)) for (s, _), n in zip(socketed, others)]
            at = max(range(len(others)), key=lambda n: (values[n], -n))
            swap = [others[at]]
            plain = max(plain, values[at])
        swap_price = sum(self.runes.price(n) or 0.0 for n in swap) if not ssf else 0.0
        build, objective = self.build, self.build.objective
        best = None  # (value, price, kept, first, second, worst outcome, scale, dps, the sequences' outcomes)
        for i, (name_a, first, cost_a) in enumerate(ranked):
            for name_b, second, cost_b in ranked[i + 1:]:
                if first.group == second.group or clock.now() > deadline:
                    continue
                kept = list(chosen)
                for side in {first.type, second.type}:
                    extra = sum(1 for a in (first, second) if a.type == side)
                    while sum(1 for a in kept if a.type == side) + extra > limits[side]:
                        cuts = [[a for a in kept if a is not out] for out in kept if out.type == side]
                        if not cuts:
                            break
                        cut_scores = self.deltas(slot, [rare_text(slot, base, level, c, runes=[ASTRID]) for c in cuts])
                        kept = cuts[max(range(len(cuts)), key=lambda n: (cut_scores[n][0], -n))]
                if len(kept) < 2 or any(sum(1 for a in kept if a.type == s)
                                        + sum(1 for a in (first, second) if a.type == s) > limits[s] for s in limits):
                    continue
                price = rare_price(slot, kept) + rune_price + cost_a + cost_b + swap_price
                if price > left + 1e-9:
                    continue
                sequences = []  # one outcome per (first removal, second removal): the second may take the first's line
                for lost in kept:
                    after = [a for a in kept if a is not lost] + [first]
                    sequences += [[a for a in after if a is not gone] + [second] for gone in after]
                unique: dict[tuple, list] = {}
                for outcome in sequences:
                    unique.setdefault(tuple(sorted(a.id for a in outcome)), outcome)
                keys = list(unique)
                texts = [rare_text(slot, base, level, unique[k], runes=swap) for k in keys]
                read = [self.without_registered(t, st) for t, (_, st) in zip(texts, self.deltas(slot, texts))]
                scores = [objective.score(st) for st in read]
                each = {k: objective.dps(st) for k, st in zip(keys, read)}
                dps = [each[tuple(sorted(a.id for a in o))] for o in sequences]
                at = min(range(len(keys)), key=lambda n: (scores[n], n))
                worst_dps = each[keys[at]]
                mean = sum(dps) / len(dps)
                scale = mean / worst_dps if worst_dps > 0 else 1.0
                stats = dict(read[at])
                stats[alloys.ALLOY_FIELD] = build.alloy_product(slot) * scale
                value = objective.score(stats) - self.lam * price
                if value > plain + EPSILON and (best is None or value > best[0]):
                    best = (value, price, kept, (name_a, first), (name_b, second), unique[keys[at]], scale, worst_dps, mean,
                            len(sequences))
        if best is None:
            return None
        _, price, kept, (name_a, first), (name_b, second), worst, scale, worst_dps, mean, count = best
        raw = rare_text(slot, base, level, worst, runes=swap)
        report = (f"{ASTRID} with {name_a} and {name_b}: each removes a random modifier ({count} sequences; the second may "
                  f"remove the first's line, LOW); the floors read the worst outcome (shown in the build), DPS the mean "
                  f"(x{scale:.3f} of the worst's) (the manager's ruling, 18 Nov)",
                  f"Worst outcome DPS {worst_dps:,.0f}; mean {mean:,.0f}",)
        in_place = (f"then socket {swap[0]} in its place ({swap_price:.3g} div; {ASTRID} isn't socket-bound: it is "
                    f"replaced, and both crafted lines stay, as on 8 listed items, AUDIT38 F2)" if swap else
                    f"then replace it with any augment (nothing here scores; {ASTRID} isn't socket-bound, AUDIT38 F2)")
        note = (f"{ASTRID}: buy the rare ({rare_price(slot, kept):.3g} div, the rare price model), socket {ASTRID} "
                f"({rune_price:.3g} div), use {name_a} (\"{'; '.join(first.lines)}\") and {name_b} "
                f"(\"{'; '.join(second.lines)}\"), {in_place}. That the rune allows two Alloy lines reads the 0.5.0 notes, "
                f"which call Alloy lines crafted (MEDIUM); the order and odds of the removals are LOW. Not on a Corrupted or "
                f"Sanctified item (0.5.4)")
        option = ItemOption(f"Rare {base['type']}", base["name"], base["type"], base.get("subType"), raw, False,
                            None if ssf else price, "ssf" if ssf else f"estimate (craft route: {ASTRID})", base_option.level,
                            rare=True, affixes=tuple(line for a in worst for line in a.lines), runes=tuple(swap),
                            alloy=ASTRID, alloy_scale=scale, alloy_report=report, augment="alloy", craft_route=(note,))
        if getattr(build, "alloy_raws", None) is None:
            build.alloy_raws = {}
        build.alloy_raws[alloys.alloy_key(raw)] = scale
        return option

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
            if (cost is None and not ssf) or exclusion_group(mod["group"]) in groups or mod["type"] not in limits:
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

    def socketed(self, slot: str, option: ItemOption, deadline: float, trial: bool = False) -> ItemOption:
        """The option with its sockets filled by the runes that score best (its socket-bound runes stay: SEARCH30).
        Its socket count: the option's own (`sockets`, an extra-socket route's) or the base's. With
        POE2_SEARCH_EXTRA_SOCKETS=1, then the extra-socket routes are tried (extra_socket_trial; `trial` is that call)."""
        build = self.build
        given = option  # (returned as it came when there's no time or nothing to fill)
        if only_fillers(option):  # (SEARCHC1) filled again from scratch: the build may want a scoring rune there now
            cost = sum(self.runes.price(n) or 0.0 for n, _ in option.filler_runes)
            option = dataclasses.replace(option, raw=with_runes(option.raw, list(option.bound)), runes=option.bound,
                                         filler_runes=(), price=None if option.price is None else option.price - cost)
        base = self.ctx.bases_by_name.get(option.base)
        count = option.sockets or (socket_count(base, option.unique, unique_text(self.ctx, option)) if base else 0)
        bound = list(option.bound)
        if count <= len(bound) or clock.now() > deadline:
            return given
        raw = with_runes(option.raw, bound)
        left = self.left_for(slot) - (option.price or 0.0)
        names = [n for n in self.runes.candidates(base, option.unique, option.raw)
                 if (self.runes.price(n) or 0.0) <= left]
        if not names:
            return given
        scored = build.item_deltas([{"slot": slot, "raw": with_runes(raw, bound + [n])} for n in names])
        top = [n for _, n in sorted(zip((s for s, _ in scored), names), key=lambda x: -x[0])[:RUNE_KEEP]]
        chosen: list[str] = list(bound)
        current = build.item_deltas([{"slot": slot, "raw": raw}])[0][0]
        used: dict[str, int] = {}  # per-character augment limits, across every other item
        scored_used: dict[str, int] = {}  # (SEARCHC1) the same without other items' fillers: a scoring pick may take
        # the limit a filler holds elsewhere (Gear.put then evicts that filler: the manager's ruling, 23 Nov 05:00)
        for other_slot, other in build.equipped.items():
            if other_slot != slot:
                fillers = [n for n, _ in other.filler_runes]
                for name in other.runes:
                    if limit := self.runes.limit(name):
                        used[limit[0]] = used.get(limit[0], 0) + 1
                        if name in fillers:
                            fillers.remove(name)
                        else:
                            scored_used[limit[0]] = scored_used.get(limit[0], 0) + 1

        def within(counts: dict[str, int], name: str) -> bool:
            limit = self.runes.limit(name)
            if not limit:
                return True
            taken = counts.get(limit[0], 0) + sum(1 for n in chosen if (self.runes.limit(n) or ("",))[0] == limit[0])
            return taken < limit[1]

        def within_limit(name: str) -> bool:  # (a scoring pick)
            return within(scored_used, name)

        def filler_within_limit(name: str) -> bool:  # (a filler: every augment in the build counts)
            return within(used, name)

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
        fillers: list[tuple[str, str]] = []
        if len(chosen) < count and fill_sockets_enabled():  # (SEARCHC1: never an empty socket; appends to `chosen`)
            fillers = self.fill_sockets(slot, base, raw, chosen, len(bound), count, current, left, names,
                                        filler_within_limit, deadline)
        if len(chosen) == len(bound):
            filled = option
        else:
            price = option.price if option.price is None else                 option.price + sum(self.runes.price(n) or 0.0 for n in chosen[len(bound):])
            filled = dataclasses.replace(option, raw=with_runes(raw, chosen), runes=tuple(chosen), price=price,
                                         filler_runes=tuple(fillers))
        if not trial and not option.sockets and extra_sockets_enabled():
            return self.extra_socket_trial(slot, filled, base, count, deadline)
        return filled

    # (SEARCHC1; THE USER 23 Nov ~01:15: "it's usually within a budget to go for gear with more sockets, which is a great
    # way to free up resistance on other pieces or add quality of life runes (reduced curse duration, item rarity, cost
    # efficiency, the 'emergent' augments, etc.)"; a delivered build with an empty rune socket is a defect) the classes a
    # socket no rune scored for is filled from, first match first, as SEARCHB5's affix fill (Gear.UTILITY_CLASSES):
    # movement speed (the default 25% floor), defence (the build's maximum hit taken), resistances, then quality of life
    # in the user's order. Each read from the candidate's what-if stats where PoB has the stat, else its own lines.
    FILL_QOL = (
        ("quality of life: curses and debuffs on you", re.compile(r"(?:Curses?|Debuffs?)\b.*\bon you\b", re.I)),
        ("quality of life: item rarity", re.compile(r"Rarity of Items", re.I)),
        ("quality of life: cost efficiency", re.compile(r"(?:Cost|Reservation) Efficiency", re.I)),
        ("quality of life: flask and charm sustain", re.compile(r"\b(?:Flasks?|Charms?)\b", re.I)),
    )
    # Never a filler (RESEARCH50 R50-2, HIGH: game SoulCores.Limit / SoulCoreLimits, PoB limitId): an Ancient Augment
    # (16 of them, the 4 Emergent Runes among them: ONE per character in total, "You may not have more than one Augment
    # of this type socketed at a time", so a filler would take the slot a scoring one elsewhere needs) and a Legacy rune
    # (one per character, made from a unique with Aldur's Legacy, not on the exchange)
    FILL_SKIP_GROUPS = frozenset({"AncientAugment", "AldursLegacyLimit1"})
    # (the manager's ruling, 23 Nov 05:00: the user named "the emergent augments" as quality-of-life ideas) the exception: a
    # filler MAY be ONE Emergent rune when the build holds no other Ancient Augment anywhere (fill_sockets' limit check
    # counts every augment, fillers included), never two; a scoring pick elsewhere still takes the slot (socketed ignores
    # other items' fillers for scoring picks, and Gear.put evicts the filler). The last quality-of-life class but one
    FILL_ANCIENT_OK = frozenset({"Emergent Instinct", "Emergent Possibility", "Emergent Protection", "Emergent Vigour"})
    FILL_MOVEMENT = re.compile(r"^\d+% increased Movement Speed$")
    FILL_HIT_TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
    FILL_RESISTS = ("FireResist", "ColdResist", "LightningResist", "ChaosResist")

    def fill_sockets(self, slot: str, base: dict, raw: str, chosen: list[str], bound: int, count: int, current: float,
                     left: float, names: list[str], within_limit: Callable[[str], bool],
                     deadline: float) -> list[tuple[str, str]]:
        """Augments for the sockets the scoring fill left empty, one socket at a time, appended to `chosen` (which
        `within_limit` reads, so the per-character limits hold); returns [(name, why)]. Every affordable candidate within
        its limit is scored on the item (one what-if call a socket); only those that don't lower the score qualify (a
        rune with a drawback the build feels stays out). Of those, the first class that has any (_fill_pick): movement
        speed, defence, resistance, then quality of life. None qualifies (nothing affordable, or every one lowers the
        score): the socket stays empty (the item's runes are fewer than its sockets)."""
        from builder.ledger.items import rune_lines

        build = self.build
        fillers: list[tuple[str, str]] = []
        names = [n for n in names if ((self.runes.limit(n) or ("",))[0] not in self.FILL_SKIP_GROUPS
                                      or n in self.FILL_ANCIENT_OK)
                 # (SEARCHC2) never a socket-bound augment: it can't be swapped ("the player's choice; swap it freely"
                 # would be false) and its effect is a craft step (a shard's anoint, an influence rune's affixes), not QoL
                 and n not in getattr(self.runes, "socket_bound", frozenset())]
        lines = {n: rune_lines([n], base) for n in names}
        before = build.item_deltas([{"slot": slot, "raw": with_runes(raw, chosen)}])[0][1]
        while len(chosen) < count and clock.now() <= deadline:
            spend = sum(self.runes.price(n) or 0.0 for n in chosen[bound:])
            options = [n for n in names if within_limit(n) and spend + (self.runes.price(n) or 0.0) <= left + 1e-9]
            if not options:
                break
            scored = build.item_deltas([{"slot": slot, "raw": with_runes(raw, chosen + [n])} for n in options])
            fit = [(stats, n) for (s, stats), n in zip(scored, options) if s >= current - EPSILON]
            pick = self._fill_pick(fit, before, lines)
            if pick is None:
                break
            chosen.append(pick[0])
            fillers.append(pick)
            before = next(stats for stats, n in fit if n == pick[0])
        if len(chosen) < count and clock.now() > deadline:  # (SEARCHD1 hook: the item keeps the runes it has)
            unfinished.note(self, "runes", slot, len(chosen), count, "sockets filled",
                            "time ran out before its empty rune sockets were tried")
        else:
            unfinished.done(self, "runes", slot)
        return fillers

    def extra_socket_trial(self, slot: str, filled: ItemOption, base: dict | None, count: int,
                           deadline: float) -> ItemOption:
        """(SEARCHC1, POE2_SEARCH_EXTRA_SOCKETS=1) the item with more sockets than an Artificer's Orb gives, if its runes
        pay for the route (RESEARCH50 R50-1, HIGH: PoB's socketLimit is the corrupted maximum; uncorrupted drops reach
        socketLimit - 1, patch 0.3.0 "a very low chance of generating with 3 Sockets in high tier Maps", trade 10,000+
        such rares; rares.socket_count gives socketLimit - 2):
        - "natural": socketLimit - 1, a copy that dropped with the extra socket, priced at the higher of the item x
          NATURAL_SOCKET_PREMIUM and the item + NATURAL_SOCKET_FLAT (R51-5, LOW-MEDIUM);
        - "corrupted": socketLimit, that copy Vaal-corrupted for the socket: (the copy + a Vaal Orb) / CORRUPT_SOCKET_CHANCE
          (MEDIUM-LOW odds; the upper bound, no resale of a miss). The item is Corrupted after: no rune NOT_IN_SEALED can
          go in (RuneCatalog.candidates reads the line), and none already chosen may be one (the route is skipped).
        Rares and normal items only (a unique's own sockets: own_sockets; its corruption is cultivated.py's); not in SSF
        (nothing is priced) nor for an unpriced item. Each route's sockets are filled from scratch (socketed with the
        route's count) and the route is kept when its score, less lam x its extra price, beats the item as filled."""
        if (base is None or filled.unique or self.ctx.ssf or filled.price is None or clock.now() > deadline
                or not base.get("socketLimit") or count != max(base["socketLimit"] - 2, 0) or count < 1):
            return filled
        build = self.build
        rune_cost = sum(self.runes.price(n) or 0.0 for n in filled.runes[len(filled.bound):])
        item_price = filled.price - rune_cost  # (the item alone, its bound runes in it)
        vaal = (self.ctx.payload.get("cultivation_prices") or {}).get("vaal")
        natural = max(item_price * NATURAL_SOCKET_PREMIUM, item_price + NATURAL_SOCKET_FLAT)
        routes = [("natural", base["socketLimit"] - 1, natural,
                   f"buy a copy that dropped with {base['socketLimit'] - 1} sockets (one more than an Artificer's Orb "
                   f"gives; priced at the item x{NATURAL_SOCKET_PREMIUM:g} or + {NATURAL_SOCKET_FLAT:g} div, the higher: "
                   f"R51-5's trade pairs, LOW-MEDIUM)")]
        if vaal is not None and not any(n in NOT_IN_SEALED for n in filled.bound):
            routes.append(("corrupted", base["socketLimit"], (natural + vaal) / CORRUPT_SOCKET_CHANCE,
                           f"that {base['socketLimit'] - 1}-socket copy corrupted with a Vaal Orb for one more socket "
                           f"(about {CORRUPT_SOCKET_CHANCE:.0%} a try, MEDIUM-LOW; a miss costs the copy: "
                           f"(copy + Vaal Orb) / {CORRUPT_SOCKET_CHANCE:g})"))
        best, best_value = filled, self.adjusted(build.item_deltas([{"slot": slot, "raw": filled.raw}])[0][0], filled)
        for route, sockets, price, note in routes:
            if clock.now() > deadline or price > self.left_for(slot) + 1e-9:
                continue
            raw = with_runes(filled.raw, list(filled.bound))
            if route == "corrupted":
                raw = raw.rstrip() + "\nCorrupted"
                if filled.alloy:  # (SEARCHC4) the "Corrupted" line changes the text its DPS factor is registered under
                    if getattr(build, "alloy_raws", None) is None:
                        build.alloy_raws = {}
                    build.alloy_raws[alloys.alloy_key(raw)] = filled.alloy_scale
                    note += f". Use the {filled.alloy} BEFORE the Vaal Orb: a Corrupted item can't be changed"
            bare = dataclasses.replace(filled, raw=raw, runes=filled.bound, filler_runes=(), price=price, sockets=sockets,
                                       craft_route=(*filled.craft_route, f"Sockets ({route}): {note}"))
            trial = self.socketed(slot, bare, deadline, trial=True)
            value = self.adjusted(build.item_deltas([{"slot": slot, "raw": trial.raw}])[0][0], trial)
            if value > best_value + EPSILON:
                best, best_value = trial, value
        return best

    def _fill_pick(self, fit: list[tuple[dict, str]], before: dict, lines: dict[str, list[str]]) -> tuple[str, str] | None:
        """(name, why) of the filler to use among `fit` (what-if stats, name), by FILL classes (fill_sockets)."""
        ancient = [n for _, n in fit if n in self.FILL_ANCIENT_OK]  # (only in their own class, next to last)
        fit = [(st, n) for st, n in fit if n not in self.FILL_ANCIENT_OK]
        if not fit and not ancient:
            return None
        price = lambda n: self.runes.price(n) or 0.0  # noqa: E731
        value = lambda stats, key: float(stats.get(key) or 0.0)  # noqa: E731
        if "MovementSpeedMod" in before:
            moving = [(value(st, "MovementSpeedMod"), n) for st, n in fit
                      if value(st, "MovementSpeedMod") > value(before, "MovementSpeedMod") + 1e-9]
        else:
            moving = [(1.0, n) for st, n in fit if any(self.FILL_MOVEMENT.match(l) for l in lines.get(n, []))]
        if moving:
            return max(moving, key=lambda x: (x[0], -price(x[1])))[1], "movement speed"

        def hits(st: dict) -> float:  # the maximum hit taken, summed over the five damage types
            return sum(value(st, f"{t}MaximumHitTaken") for t in self.FILL_HIT_TYPES)

        def pool(st: dict) -> float:
            return value(st, "Life") + value(st, "EnergyShield")
        gains = [(hits(st) - hits(before), pool(st) - pool(before), n) for st, n in fit]
        gains = [g for g in gains if g[0] > 1e-9 or g[1] > 1e-9]
        if gains:  # the cheapest within 10% of the best gain (maximum hit first, else Life + ES)
            at = 0 if max(g[0] for g in gains) > 1e-9 else 1
            best = max(g[at] for g in gains)
            near = [g for g in gains if g[at] >= 0.9 * best]
            return min(near, key=lambda g: (price(g[2]), -g[0], -g[1]))[2], "defence"
        resisting = [(st, n) for st, n in fit if any(value(st, k) > value(before, k) + 1e-9 for k in self.FILL_RESISTS)]
        if resisting:
            best = min(resisting, key=lambda x: (-sum(value(x[0], k) for k in self.FILL_RESISTS), price(x[1])))
            return best[1], "resistance"
        for why, pattern in self.FILL_QOL:
            hit = [n for _, n in fit if any(pattern.search(l) for l in lines.get(n, []))]
            if hit:
                return min(hit, key=price), why
        if ancient:
            return min(ancient, key=price), "quality of life: an Emergent augment (the build's one Ancient Augment)"
        return min((n for _, n in fit), key=price), "quality of life: the cheapest that doesn't lower the score"

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
        if (option is None or option.type != "Helmet" or clock.now() > deadline or anoints.capacity(option) > 0):
            return option
        trial = self.shard_version(slot, option)
        if trial is None or (trial.price is not None and trial.price > self.left_for(slot) + 1e-9):
            return option
        anointed = self.anointed(slot, trial, deadline)
        if not anointed.anoints:
            return option
        (old, _), (new_score, _) = self.deltas(slot, [option.raw, anointed.raw])
        return anointed if self.adjusted(new_score, anointed) > self.adjusted(old, option) + EPSILON else option

    def shard_version(self, slot: str, option: ItemOption) -> ItemOption | None:
        """(SEARCHB8, JEWEL2's ask) The item with a Raven-Touched Shard (anoints.SHARD) in a free socket or in place of its
        last rune (the greedy fill's weakest), priced with the shard's catalog price less the dropped rune's; None when it
        can't take one: it holds one already, it is Corrupted or Sanctified (anoints.SEALED), its base takes no shard
        (RuneCatalog.candidates: helmets, level 60), the character's shard limit is used elsewhere, or the rune it would
        replace is socket-bound (SEARCH30). Shared by shard_trial and Jewels.shard_helmet; the budget is the caller's."""
        if anoints.SHARD in option.runes or anoints.SEALED.search(option.raw):
            return None
        base = self.ctx.bases_by_name.get(option.base)
        count = socket_count(base, option.unique, unique_text(self.ctx, option)) if base else 0
        if count == 0 or anoints.SHARD not in self.runes.candidates(base, option.unique, option.raw):
            return None
        limit = self.runes.limit(anoints.SHARD)
        if limit and sum(1 for s, o in self.build.equipped.items() if s != slot and o is not None
                         for n in o.runes if (self.runes.limit(n) or ("",))[0] == limit[0]) >= limit[1]:
            return None
        runes = list(option.runes)
        dropped = runes[count - 1:] if len(runes) >= count else []
        if any(n in option.bound for n in dropped):  # (SEARCH30: a socket-bound influence rune is never replaced)
            return None
        new = runes[:count - 1] + [anoints.SHARD] if dropped else runes + [anoints.SHARD]
        price = option.price
        if price is not None:
            price += (self.runes.price(anoints.SHARD) or 0.0) - sum(self.runes.price(n) or 0.0 for n in dropped)
        return dataclasses.replace(option, raw=with_runes(option.raw, new), runes=tuple(new), price=price)

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

    def gear_round(self, deadline: float, full: bool = True, only: list[str] | None = None) -> bool:
        """Best affordable item per slot. A full round scores every option, builds a rare and
        fills sockets, and remembers the top few per slot; other rounds rescore only those.
        With `only` (SEARCH37, the narrow gear-first: the slots still empty when a task resumes its pass), the round
        does a full round's work on those slots alone and leaves the round's own state alone: the cut full round's
        resume slot, the source-item trials, the rune / meta / Purity trials after the slots and the lambda update all
        wait for the next full round (on 2b75e seed 1 a whole cut round, ~100 s a task, crowded out supports:
        POE2T7423)."""
        build = self.build
        improved = False
        self.sync_flasks()
        slots = list(self.slots)
        whole = full and only is None  # (the round-level work: only a full round over every slot)
        if only is not None:
            slots = [s for s in slots if s in only]
        if whole:  # a full round that ran out of time last task goes on from the slot it stopped at
            self._anoint_screen = None
            self.rare_tried.clear()
            # The source-item trials run when a full round starts, not again when a cut round goes on (they ran then),
            # and take at most TRIAL_SHARE of its time: each trial settles the build and may re-route the tree for up
            # to REQUIREMENT_FIT_SECONDS, and on 2b75d they took the whole share, so the slot loop never reached a
            # slot and six slots stayed empty through round 2 (SEARCH21)
            if self.resume is None:
                if os.environ.get(SWITCH_QUICK_FILL, "0") == "1":
                    improved = self.quick_fill(deadline) or improved
                now = clock.now()
                improved = self.assumption_trials(min(deadline, now + TRIAL_SHARE * max(0.0, deadline - now))) or improved
            start = slots.index(self.resume) if self.resume in slots else 0
            slots = slots[start:] + slots[:start]
            self.resume = None
        swap_deadline = deadline  # (SEARCHB10: resist_swap_round's own end, past a slot loop it would otherwise follow)
        if whole:
            deadline, swap_deadline = resist_swap_split(clock.now(), deadline)
        for slot in slots:
            if clock.now() > deadline:
                if whole and self.resume is None:
                    self.resume = slot
                    # (SEARCHD1 hook: each slot keeps its item; the next whole round starts here)
                    unfinished.note(self, "gear round", f"{slot} and the slots after it", detail=(
                        "the last full gear round ran out of time before these slots; each kept the item it had"))
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
                if target is not None and (tuple(target.runes) == tuple(target.bound)  # (bound runes: SEARCH30)
                                           or only_fillers(target)):  # (SEARCHC1: fillers are re-chosen every full round)
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
            if build.score > before + EPSILON and (checkpoint := getattr(self, "checkpoint", None)) is not None:
                checkpoint()  # (SEARCHD1 hook, F1: a hard stop later in the round keeps this slot's kept item)
        if whole:
            improved = self.character_rune_round(deadline) or improved
        if whole and os.environ.get(SWITCH_RESIST_SWAP, "0") == "1":
            self.resist_swap_rows = self.resist_swap_round(swap_deadline)  # (kept for probes: one row per pair tried)
            improved = any(row["kept"] for row in self.resist_swap_rows) or improved
        if whole and self.fill_granted is not None:
            improved = self.granted_meta_trials(deadline) or improved
        if whole and os.environ.get(SWITCH_PURITY, "0") == "1":
            improved = self.purity_trials(deadline) or improved
        if whole:
            self.update_lambda()
            if self.resume is None:  # (SEARCHD1 hook: a whole round that reached every slot)
                unfinished.done(self, "gear round")
        return improved

    # A quick pick for every empty slot before the deep work (SEARCHB2; the manager's 19:08 item) ----------------

    def quick_fill(self, deadline: float) -> bool:
        """Each empty gear slot gets the best affordable item of its options by one what-if batch (bases and uniques,
        no rare built), if it beats the slot empty. A full round runs the source-item trials first (up to TRIAL_SHARE
        of its time) and the slots after them in order, so on a short round the last slots stayed empty (SEARCH35: 4-6
        on 2b75b: Gloves, Boots, Amulet, Rings, Belt); probe POE2T6242: the trials took 63 s and 122 s of two 240 s
        rounds on a fresh 2b75b search. The deep per-slot work replaces these picks as it reaches them."""
        build = self.build
        improved = False
        for slot in self.slots:
            if (clock.now() > deadline or slot in build.locked_slots or build.equipped.get(slot) is not None
                    or is_flask_slot(slot) or slot in CHARM_SLOTS):
                continue
            options = [o for o in self.candidates(slot) if self.allowed(slot, o)]
            if not options:
                continue
            scored = self.deltas(slot, [o.raw for o in options])
            best_value, best = max(((self.adjusted(s, o), o) for (s, _), o in zip(scored, options)), key=lambda x: x[0])
            if best_value <= build.score + EPSILON:
                continue
            before = build.score
            self.put(slot, best)
            self.settle()
            if build.worse(before):
                self.put(slot, None)
                self.settle()
                continue
            improved = True
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
                    # (SEARCHC1, on loan: the manager 23 Nov 05:00) its sockets filled too, kept on the same checks
                    socketed = (self.socketed(slot, option, clock.now() + FILL_EMPTY_SOCKET_SECONDS)
                                if isinstance(option, ItemOption) else option)
                    if socketed is not option:
                        self.put(slot, socketed)
                        self.settle()
                        if reweigh is not None:
                            reweigh()
                        if (build.score >= before - EPSILON and build.spent() <= max(self.ctx.budget, spent) + 1e-9
                                and objective._weight(objective.deficits(build.stats)) <= weight + 1e-12):
                            option = socketed
                        else:
                            self.put(slot, option)
                            self.settle()
                            if reweigh is not None:
                                reweigh()
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

    WHY_DPS = 0.005  # (LOW, a wording threshold only) a DPS change under 0.5% is reported as "no DPS change"

    def why_items(self) -> dict[str, dict]:
        """(SEARCHC1; the user asked why The Vertex was chosen) for each equipped item, what it does for the build: the
        DPS and every floor with it and with the same base plain (no affixes, sockets or anoints: what the item's own
        lines and runes give), the best other candidate the gear rounds shortlisted for the slot, rescored now, and the
        main reason in words. Item what-ifs (PoB, plus the models' real-trial rescore where Build.item_deltas wires one):
        MEDIUM. Flasks and charms have their own report (flask_report). {slot: row}."""
        from builder.search.context import normal_item

        build = self.build
        objective = build.objective
        rows: dict[str, dict] = {}
        for slot, option in sorted(build.equipped.items()):
            if option is None or slot not in self.slots or is_flask_slot(slot) or slot in CHARM_SLOTS or option.raw is None:
                # (hotfix 23 Nov, SPEED2's POE2T7828: a jewel's slot isn't one of the gear rounds', so candidates() raised
                # KeyError 'Jewel N' and finalize lost the whole build)
                continue
            base = self.ctx.bases_by_name.get(option.base)
            plain = normal_item(base) if base else None
            others = [o for o in self.candidates(slot) if o.name in set(self.shortlist.get(slot) or ())
                      and o.name != option.name][:SHORTLIST]
            cases = [{"slot": slot, "raw": option.raw}] + ([{"slot": slot, "raw": plain}] if plain else [])
            cases += [{"slot": slot, "raw": o.raw} for o in others]
            try:
                scored = build.item_deltas(cases)
            except Exception as e:  # (never lose the result for its explanation)
                rows[slot] = {"item": option.name, "reason": f"not worked out ({type(e).__name__})"}
                continue
            (score, stats), rest = scored[0], scored[1:]
            row: dict = {"item": option.name, "base": option.base, "dps_with": round(objective.dps(stats), 1)}
            floors = []
            if plain:
                plain_score, plain_stats = rest[0]
                rest = rest[1:]
                row["without_is"] = f"a plain {option.base} (no affixes, sockets or anoints)"
                row["dps_without"] = round(objective.dps(plain_stats), 1)
                row["dps_change"] = (row["dps_with"] / row["dps_without"] - 1.0) if row["dps_without"] > 0 else None
                for penalty in objective.penalties:
                    have, lack = float(stats.get(penalty.stat) or 0.0), float(plain_stats.get(penalty.stat) or 0.0)
                    if abs(have - lack) > 1e-9:
                        floors.append({"label": penalty.label, "with": round(have, 2), "without": round(lack, 2),
                                       "floor": penalty.floor, "held": have >= penalty.floor > lack})
                row["floors"] = floors
            if rest:
                best = max(zip(rest, others), key=lambda x: self.adjusted(x[0][0], x[1]))
                (other_score, other_stats), other = best
                row["runner_up"] = {"name": other.name, "price": other.price, "dps": round(objective.dps(other_stats), 1),
                                    "score_vs_chosen": round(self.adjusted(other_score, other) - self.adjusted(score, option), 4)}
            held = [f for f in floors if f["held"]]
            change = row.get("dps_change")
            if held:
                reason = "holds the " + ", ".join(f"{f['label']} floor ({f['with']:g} with it, {f['without']:g} without, "
                                                  f"floor {f['floor']:g})" for f in held)
                if change is not None and abs(change) >= self.WHY_DPS:
                    reason += f"; {change:+.1%} DPS"
            elif change is not None and abs(change) >= self.WHY_DPS:
                reason = f"{change:+.1%} DPS over a plain {option.base}"
            elif floors:
                reason = "no DPS change; it raises " + ", ".join(f["label"] for f in floors) + " (each floor met either way)"
            else:
                reason = "no DPS or floor change over a plain base: a filler or a base pick (its price, its sockets)"
            if option.filler_runes:
                reason += "; its socket filler" + ("s" if len(option.filler_runes) > 1 else "") + " " + ", ".join(
                    f"{name} ({why})" for name, why in option.filler_runes) + " are the player's choice"
            row["reason"] = reason
            rows[slot] = row
        return rows

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

    def purity_trials(self, deadline: float) -> bool:
        """Shrine Sceptres, tried for real where a slot takes a Sceptre (the manager, 03:05; RESEARCH46): their Purity
        (or Impurity) has a 0% Cost & Reservation Multiplier, so every Spirit-costly support in its group is free, DPS
        ones included (Precision, Direstrike, Mysticism, Upwelling, Her Declaration). A plain what-if sees only the
        sceptre's own lines, its group empty, so its free supports never show and the slot's ranking never picks it.
        Each option: a rare built on each Shrine Sceptre base (one per Purity; a normal drop base, level 26) and each
        Purity unique the build can afford (the Guiding Palms, Sacred Flame, Palm of the Dreamer: candidates, priced as
        usual). Equipped, settled, the tree re-routed to its requirements (fit_requirements), its granted group's
        supports filled (fill_supports), kept if the build scores higher, else put back."""
        build = self.build
        improved = False
        if "purity_rows" not in vars(self):
            self.purity_rows = []
        for slot, spec in self.slots.items():
            if "Sceptre" not in spec.types or slot in build.locked_slots or clock.now() > deadline:
                continue
            current = build.equipped.get(slot)
            options = [o for o in self.candidates(slot) if o.unique and FREE_SUPPORT_LINE.search(o.raw)]
            for base in self.options(slot):
                if (base.unique or not FREE_SUPPORT_LINE.search(base.raw) or clock.now() > deadline
                        or (current is not None and not current.unique and current.base == base.base)):
                    continue
                options.append(self.build_rare(slot, deadline, base_name=base.base) or base)
            for option in options:
                if clock.now() > deadline or (current is not None and option.raw == current.raw):
                    continue
                if not self.allowed(slot, option) or (option.price or 0.0) > self.left_for(slot) + 1e-9:
                    continue
                snap, before = build.snapshot(), build.score
                self.put(slot, option)
                self.settle()
                if self.fit_requirements is not None:
                    self.fit_requirements(min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS))
                self.fill_supports(FREE_SUPPORT_LINE.search(option.raw).group(1), deadline)
                kept = build.score > before + EPSILON and self.adjusted(build.score, option) > self.adjusted(before, current)
                self.purity_rows.append({"slot": slot, "option": option.name, "base": option.base, "unique": option.unique,
                                         "before": round(before, 4), "after": round(build.score, 4), "kept": kept})
                if kept:
                    improved, current = True, option
                    continue
                build.restore(snap)
                self.settle()
        return improved

    def fill_supports(self, skill: str, deadline: float) -> bool:
        """The supports of each enabled group an item grants with `skill` first, filled by the search's support round
        (fill_group: gems.fill_granted_supports). Without the hook, nothing is filled (the trial judges the bare item)."""
        if self.fill_group is None:
            return False
        improved = False
        for group in self.build.groups():
            if (group["fromItem"] and group["enabled"] and group["gems"] and group["gems"][0]["name"] == skill
                    and clock.now() < deadline):
                improved = self.fill_group(group["index"], min(deadline, clock.now() + REQUIREMENT_FIT_SECONDS)) or improved
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
