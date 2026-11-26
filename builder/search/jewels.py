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
import os
import re
import time
from collections.abc import Callable

from builder import clock
from builder.data import rare_prices, unique_versions
from builder.search import anoints, desecrated
from builder.search.context import ItemOption
from builder.search.radius import Variants, compact, miscalculated, tree_changing, unlocks, variants_of, with_variants
from builder.search.rares import (CATALYST_QUALITY, CATALYSTS, CATALYSTS_PER_20, MELANCHOLY_CRAFTS, RADIUS_CRAFT,
                                  RESIST_CRAFTS, SCREEN_KEEP, Affix, magic_text, roll,
                                  AffixPools, active_lines, boosted, catalysed_text, catalyst_matches, mod_lines,
                                  rare_price, rare_text, socket_count, unique_text, with_runes)
from builder.search.state import EPSILON, Build
from builder.search.tree import FULL_EVERY

# (COV50) the fields objective.main_dps reads, player and minion, for the radius-transform row
TRANSFORM_ROW_FIELDS = [prefix + field for prefix in ("", "Minion.") for field in (
    "CombinedDPS", "ShowAverage", "CullMultiplier", "ReservationDpsMultiplier", "TotalDPS", "AverageDamage", "TotalDotDPS")]

JEWEL_BASES = ("Ruby", "Emerald", "Sapphire")
RADIUS_BASES = ("Time-Lost Ruby", "Time-Lost Emerald", "Time-Lost Sapphire")  # rares with a radius (Small)
# (JEWEL4; RESEARCH59 R59-20) A RARE Time-Lost Diamond (its base takes all three colours' radius rows; AUDIT38 #220 is a live rare
# listing) made by the Reforging Bench from one Time-Lost Ruby, Emerald and Sapphire (MEDIUM-LOW on the route: one 0.5 guide and
# R43-6's plain Diamond recipe; MEDIUM that they exist live). Priced by that route: the jewel's own mods by the rare model plus the
# three Time-Lost rare inputs, each at the model's price for a plain rare jewel (no price = no offer). Plain 2 + 2 shape only.
# Behind POE2_SEARCH_TIMELOST_DIAMOND, OFF until gated: one more base is one more greedy fill in every full jewel round (the jewel
# steps already take 26-32% of a gate run's task time)
TIMELOST_DIAMOND = "Time-Lost Diamond"
DIAMOND_JEWEL = os.environ.get("POE2_SEARCH_TIMELOST_DIAMOND", "0") == "1"


def radius_rare_bases() -> tuple[str, ...]:
    """(JEWEL4) The Time-Lost bases a rare is built on: RADIUS_BASES, plus the Diamond when its switch is on."""
    return RADIUS_BASES + ((TIMELOST_DIAMOND,) if DIAMOND_JEWEL else ())
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
# (JEWEL1; JEWEL2: RESEARCH53 R-J5) Trade floors (divines) of versions the price feed lumps together, by Sinister sockets
# (Forbidden Rites, 23 Nov 2026, instant buyout; listings MEDIUM, the mirror rate LOW): Voices 2 sockets from 1,418 div,
# 3 sockets from 3 mirrors (the cheapest divine-priced at 13,900), 4 sockets from 25 mirrors (at ~4,600 div a mirror)
VERSION_FLOORS = {"Voices": {2: 1418.0, 3: 13900.0, 4: 115000.0}}
ZAROKH = "Zarokh's Gift"  # (JEWEL1) the anoint that grants a Sinister Jewel Socket (zarokh_trial)
# (JEWEL2; RESEARCH53 R-J4) Cadigan's Epiphany, a gloves augment: "Destroys all Augment Sockets on the item to create a Jewel
# Socket" (ModRunes; limit 1, socket-bound, unique gloves allowed, never on Corrupted / Sanctified ones: rares.NOT_IN_SEALED).
# PoB has no parser line for it: gloves that carry it read "Sockets: J" (Item.lua 817-826), which is the form written here.
# 6 of the 20 live example builds wear it (R-J4). Sized on the gate references, POE2T7956
CADIGAN = "Cadigan's Epiphany"
CADIGAN_SLOT = "Gloves Jewel Socket 1"
SEALED = re.compile(r"^(?:Corrupted|Sanctified)\s*$", re.M)
# (JEWEL3; RESEARCH51 R51-11, the manager's 24 Nov order; DPS: gross x1.000-1.159 on the gate references, POE2T7956) Potent
# Liquid Melancholy on a basic jewel: "Removes a random modifer and Augments a Rare Basic Jewel with a new guaranteed Crafted
# modifier", fixed by the jewel's colour (PoB Data/LiquidEmotions.lua EndgameDistilledEmotion1: Ruby -> Debilitate, SAPPHIRE
# -> "Inflict Elemental Exposure on Hit while you have a Ruby and an Emerald socketed in your tree", Emerald -> Blind;
# RESEARCH51's table from poe2db agrees; the game tables we extract hold the emotion item, BaseItemTypes 1131, not the
# colour map). So the Exposure jewel is a Sapphire and needs a Ruby AND an Emerald in other tree sockets (it is neither).
# One crafted mod per jewel (R-J1, HIGH): no effect line beside it, so the shape is 2 prefixes + 1 suffix + the crafted
# suffix (the emotion removes a mod first; from the side it adds to when that side is full: RESEARCH59 R59-17, MEDIUM; R52-4). Conditions reads the
# line (MECH92's socketed_pair), so a plain what-if can't see it: exposure_trial scores it by a real trial (equip, settle)
EXPOSURE_CRAFT = MELANCHOLY_CRAFTS[0]
EXPOSURE_BASE = "Sapphire"
EXPOSURE_PAIR = ("Ruby", "Emerald")
EXPOSURE_SHAPE = {"Prefix": 2, "Suffix": 1}
MELANCHOLY = "Potent Liquid Melancholy"
# (LOW) Tries the route takes: the emotion removes a random modifier, so a jewel bought with the 3 chosen mods + any 4th
# suffix keeps the chosen suffix about half the time (it removes from the suffix side when that side is full: R59-17, MEDIUM); a lost try is a new
# jewel. Expected cost = EXPOSURE_TRIES x (that jewel + one emotion)
EXPOSURE_TRIES = 2.0
EXPOSURE_LINE = "Inflict Elemental Exposure on Hit while you have a Ruby and an Emerald socketed in your tree"
# (JEWEL3) exposure_trial in full jewel rounds. (JEWEL4) OFF by default since the --work gates of 25 Nov (2b75d, 3 seeds, the
# manager's ruling: flip it OFF if ON's median isn't higher): with it ON 238,291 median, OFF 238,291, the same DPS on every seed
# (POE2T8648 / 8650, 8651 / 8653, 8654 / 8656): the trial never kept its Sapphire there and only spent search time
EXPOSURE_JEWEL = os.environ.get("POE2_SEARCH_EXPOSURE_JEWEL", "0") == "1"
# (JEWEL3; RESEARCH57 R57-9) The 13 Refined catalysts put quality on a jewel: "Adds quality that enhances <X> modifiers on a
# jewel", "Replaces other quality types" (poe2db, 23 Nov; 0.5.0 / 0.5.2 notes; BaseItemTypes CurrencyJewelQuality*):
# one category per jewel, its lines x(1 + quality) (MEDIUM-HIGH, as the jewellery catalysts), up to 20% (MEDIUM: the
# general item maximum, no jewel source found), 1% a catalyst (UNVERIFIED, LOW: CATALYSTS_PER_20). PoB scales any item
# carrying "Catalyst:" lines by its lines' tags (Classes/Item.lua getCatalystScalar: no item-type check), so the jewel's
# text is scored as written (rares.catalysed_text). Where the 0.5 effect line also raises a line, the two are read as ADDING
# (the lower reading: the line written at effect + quality, not scaled again; multiplying them is the other reading,
# UNVERIFIED, named in the item's route). Exchange name -> rares.CATALYSTS key (same tags as the jewellery ones)
JEWEL_CATALYSTS = {"Refined " + name: name for name in CATALYSTS}
# (JEWEL3; RESEARCH53 R-J2 / R-J3, AUDIT38 F9) A Time-Lost rare's crafts are the Ancient emotions, one crafted mod per jewel:
# Ancient Potent Liquid Melancholy "Upgrades Radius to Very Large" (CraftedJewelRadiusExtraLargeSize, a PREFIX by the game's
# GenerationType, all four colours; PoB ModParser 5619 timeLostJewelRadiusOverride 4). Two shapes, both whole: 1 prefix +
# 2 suffixes + the craft (the emotion alone: it removes a random modifier and adds its own), and 1 prefix + 3 suffixes + the
# craft by the 0.5 route with Ancient Potent Liquid Contempt's "+1 Suffix Modifier allowed" first (the same steps as the
# effect craft: AUDIT38 F9 #222, a real listing of that shape; rare_prices.jewel_craft with the Ancient materials)
RADIUS_SHAPES = ({"Prefix": 1, "Suffix": 3}, {"Prefix": 1, "Suffix": 2})
ANCIENT_MELANCHOLY, ANCIENT_CONTEMPT = "Ancient Potent Liquid Melancholy", "Ancient Potent Liquid Contempt"
RADIUS_TRIES = 2.0  # (LOW) the lone emotion keeps the chosen mods about half the time: which one it removes is random
# (JEWEL4) Ancient Potent Liquid Ferocity's radius resistance (rares.RESIST_CRAFTS, a SUFFIX: the mirror of the radius craft):
# 3 prefixes + 1 suffix + the craft by the 0.5 route with Ancient Potent Liquid Contempt's "+1 Prefix Modifier allowed" first,
# and 2 prefixes + 1 suffix + the craft by the emotion alone (RADIUS_TRIES x (the jewel + the emotion), LOW). Defence: it
# frees gear affixes from resistances. OFF until gated
RESIST_SHAPES = ({"Prefix": 3, "Suffix": 1}, {"Prefix": 2, "Suffix": 1})
ANCIENT_FEROCITY = "Ancient Potent Liquid Ferocity"
RESIST_JEWEL = os.environ.get("POE2_SEARCH_RADIUS_RES", "0") == "1"
JEWEL_CATALYST = os.environ.get("POE2_SEARCH_JEWEL_CATALYST", "0") == "1"  # (JEWEL3) OFF until gated
RADIUS_JEWEL = os.environ.get("POE2_SEARCH_RADIUS_CRAFT", "0") == "1"  # (JEWEL3) the Very Large radius shapes; OFF until gated
# (JEWEL3; RESEARCH52 R52-4) The Adorned, a unique Diamond (Limited to 1; Current version): "(0-150)% increased Effect of Jewel
# Socket Passive Skills containing Corrupted Magic Jewels" (PoB ModParser 5644: corruptedMagicJewelIncEffect, so PoB scores
# it). Worth it only beside corrupted MAGIC jewels (1 prefix + 1 suffix, cheap) in the other sockets: a budget's option (with
# no budget, whole crafted rares give more per socket). Rolls offered at the trade floors R52-4 read (Forbidden Rites,
# 23 Nov 2026: 20 div at 100%+, 60 div at 130%+; MEDIUM-LOW), or the feed's price when higher. A Sinister socket takes no
# Jewel Socket effect (KeywordPopups: "modifiers to the effect of Jewel Sockets do not apply"): its jewel stays. The
# magic jewels are corrupted with a Vaal Orb assumed to leave them as they are (UNVERIFIED: a Vaal Orb's jewel outcomes),
# priced as a 2-mod jewel by the rare model + one Vaal Orb (when priced), x ADORNED_TRIES (LOW)
ADORNED = "The Adorned"
ADORNED_ROLLS = {100: 20.0, 130: 60.0}
ADORNED_TRIES = 2.0
ADORNED_JEWEL = os.environ.get("POE2_SEARCH_ADORNED", "0") == "1"  # (JEWEL3) adorned_trial; OFF until gated
MAGIC_JEWEL = {"Prefix": 1, "Suffix": 1}
# (JEWEL4; AUDIT38 F9 (b)) A Preserved Cranium's Desecrated line on the CRAFTED 3 + 1 + effect jewel, nothing removed: real
# listings hold 6 lines (#221 / #333: a basic Sapphire / Emerald with 2 + 2 natural mods, the effect craft and a Desecrated
# suffix; #219: a Time-Lost Sapphire with 4 suffixes, one Desecrated, beside the crafted line, nothing lost). MEDIUM-LOW: no
# source says when a Desecration on this jewel removes a modifier (poe2db's Desecrated Modifiers page: "If modifiers are full
# then a random modifier is also removed"; what "full" means for a crafted jewel is UNVERIFIED). A side holds at most 4
# (AUDIT38's proposed limit, #219). Needs POE2_SEARCH_DESECRATED_JEWEL=1 too (the Cranium trial itself); OFF until gated
CRANIUM_CRAFTED = os.environ.get("POE2_SEARCH_CRANIUM_CRAFTED", "0") == "1"
CRANIUM_SIDE_MAX = 4
ADORNED_RANGE = re.compile(r"\((\d+)-(\d+)\)% increased Effect of Jewel Socket Passive Skills")
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


def shape_full(chosen: list, limits: dict[str, int]) -> bool:
    """(JEWEL2) Every slot of the shape holds an affix (crafted lines aside). The 0.5 craft only ever makes the full shape: it
    adds a third affix of the boosted kind while "+1 ... Modifier allowed" is on, then replaces that line with the effect
    line (the user's note, 24 Oct 2026; RESEARCH53 R53-2's route), so an effect line beside fewer affixes is no craft's output.
    (JEWEL3; THE USER 24 Nov) A jewel that is short anyway is kept, with a "not finished" note (Jewels.mark_unfinished)."""
    return all(sum(1 for a in chosen if a.type == kind and not a.crafted) == count for kind, count in limits.items())


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
        self.unfinished: dict[str, dict] = {}  # (JEWEL3) jewel text -> what its slots lack and why (unfinished_notes)
        self.cadigan_kept: dict | None = None  # (JEWEL3) cadigan_record of the last Cadigan's Epiphany kept, for the note
        self.exposure_kept: dict | None = None  # (JEWEL3) what exposure_trial last kept, for the note
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
        if it beats what is there. Uniques are not tried there: the game refuses them (MEDIUM-HIGH, RESEARCH53 R-J4; checked:
        official forum 3954796's two reports, Gamerant, all 20 live example builds' item-socket jewels are Rare; no game-file
        rule string was found in KeywordPopups, Stats or stat_descriptions)."""
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
                   bases: tuple[str, ...] | None = None) -> ItemOption | None:
        """The best rare jewel for this socket within its budget, on one of `bases`. With `required`,
        it starts from the best affix that passes it (a condition's source, builder/search/sources.py),
        with the plain two-and-two shape."""
        left = self.gear.left_for(slot)
        level = self.pools.item_level
        best: tuple[float, ItemOption] | None = None
        bases = JEWEL_BASES + radius_rare_bases() if bases is None else bases
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
            # Cranium trial builds on it). Time-Lost bases can't take the effect line (HIGH, RESEARCH53 R-J2 / R-J3; checked:
            # every Ancient row of PoB's generated LiquidEmotions.lua, none has CraftedJewel{Prefix,Suffix}Effect; poe2db).
            # Their own crafts, the Ancient emotions, are offered behind switches OFF until gated: the Very Large radius
            # (JEWEL3, POE2_SEARCH_RADIUS_CRAFT) and the radius resistance (JEWEL4, POE2_SEARCH_RADIUS_RES)
            shapes = [(JEWEL_LIMIT, [forced] if forced else [], None)]
            diamond = base_name == TIMELOST_DIAMOND  # (JEWEL4: the plain shape only, priced by the reforge route)
            for kind in JEWEL_LIMIT if base_name in JEWEL_BASES else ():
                if crafted := self.pools.crafted(kind):
                    other = next(k for k in JEWEL_LIMIT if k != kind)
                    shapes.append(({kind: 3, other: 1}, [crafted[0]] + ([forced] if forced else []), (kind, crafted[1])))
            radius = self.radius_affix() if base_name in RADIUS_BASES and RADIUS_JEWEL else None
            for limits in RADIUS_SHAPES if radius is not None else ():
                shapes.append((limits, [radius] + ([forced] if forced else []), "radius"))
            resist = self.resist_affix(base_name) if base_name in RADIUS_BASES and RESIST_JEWEL else None
            for limits in RESIST_SHAPES if resist is not None else ():
                shapes.append((limits, [resist] + ([forced] if forced else []), "resist"))
            for limits, fixed, boost in shapes:
                shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
                if boost in ("radius", "resist"):  # (JEWEL3 / JEWEL4) an Ancient emotion's craft: its own route's price
                    craft = radius if boost == "radius" else resist
                    found = self.greedy(slot, base, shortlist, limits, fixed, left, deadline, required is not None,
                                        full=True, free=(craft,))
                    option = self.radius_rare(base, limits, found, boost) if found else None
                    if option is not None and not shape_full(found[1], limits):  # (kept, with the note: THE USER 24 Nov)
                        self.mark_unfinished(option[1].raw, found[1], limits, deadline)
                    if option is not None and (option[0] <= left + 1e-9 or self.ctx.ssf):
                        value = found[0] + self.gear.lam * (rare_price("Jewel", found[2]) - (option[0] or 0.0))
                        if best is None or value > best[0]:
                            best = (value, option[1])
                    continue
                if boost:
                    shortlist = [dataclasses.replace(a, lines=tuple(boosted(l, boost[1]) for l in a.lines))
                                 if a.type == boost[0] else a for a in shortlist]
                    fixed = [dataclasses.replace(a, lines=tuple(boosted(l, boost[1]) for l in a.lines))
                             if a.type == boost[0] and not a.crafted else a for a in fixed]
                found = self.greedy(slot, base, shortlist, limits, fixed, left, deadline, required is not None,
                                    full=FULL_JEWELS)
                if found and FULL_JEWELS and not shape_full(found[1], limits):
                    # (JEWEL3; THE USER 24 Nov: "a jewel with 2 mods is better than an empty jewel slot. if the optimizer runs
                    # out of time when it's actively working on something like that, it should leave a note to indicate that
                    # it wasn't finished") A jewel short of its slots is kept, and the result says it isn't finished
                    self.mark_unfinished(rare_text("Jewel", base, level, found[1]), found[1], limits, deadline)
                catalysed = self.catalysed_jewel(slot, base, found[1], found[2], found[0], boost, pool, left) \
                    if found and JEWEL_CATALYST and not diamond else None
                if catalysed is not None and (best is None or catalysed[0] > best[0]):
                    best = catalysed
                    if rare_text("Jewel", base, level, found[1]) in self.unfinished:
                        self.unfinished[catalysed[1].raw] = self.unfinished[rare_text("Jewel", base, level, found[1])]
                elif found and (best is None or found[0] > best[0]):
                    value, chosen, priced = found
                    price = None if self.ctx.ssf else rare_price("Jewel", priced)
                    route = ()
                    if diamond:
                        inputs = rare_price("Jewel", [])
                        if not self.ctx.ssf and (inputs is None or price + 3 * inputs > left + 1e-9):
                            continue  # (no price, or over the budget: no offer)
                        if not self.ctx.ssf:
                            value -= self.gear.lam * 3 * inputs
                            price += 3 * inputs
                        route = (f"Reforged: one Time-Lost Ruby, Emerald and Sapphire at the Reforging Bench make a rare "
                                 f"Time-Lost Diamond (R59-20, MEDIUM-LOW on the route: one 0.5 guide; a live rare listing "
                                 f"exists, MEDIUM); priced as the three inputs at the rare model's plain-jewel price"
                                 + ("" if self.ctx.ssf else f" ({3 * inputs:.3g} div)") + " plus these mods",)
                        if best is not None and value <= best[0]:
                            continue
                    best = (value, ItemOption(
                        "Rare Jewel", base_name, "Jewel", None, rare_text("Jewel", base, level, chosen), False, price,
                        "ssf" if self.ctx.ssf else ("estimate (craft route: reforge)" if diamond
                                                    else "estimate (uncalibrated)"), 0, rare=True,
                        affixes=tuple(line for a in chosen for line in a.lines), craft_route=route))
                    if required is None and desecrated.cranium_enabled() and (not boost or CRANIUM_CRAFTED) \
                            and not diamond:
                        crafted = self.cranium_trial(slot, base, chosen, value, left, deadline, boost or None)
                        if crafted is not None and crafted[0] > best[0]:
                            best = crafted
        return best[1] if best else None

    def radius_affix(self) -> Affix | None:
        """(JEWEL3) The Ancient Potent Liquid Melancholy craft as an affix (None if this game data lacks it)."""
        mod = next((m for m in self.pools.mods if m["id"] == RADIUS_CRAFT), None)
        if mod is None:
            return None
        return Affix(mod["id"], mod["group"], mod["type"], 1, tuple(mod_lines(mod)), crafted=True)

    def resist_affix(self, base_name: str) -> Affix | None:
        """(JEWEL4) Ancient Potent Liquid Ferocity's radius resistance for this Time-Lost colour as a crafted affix, rolled
        as the request's tier rolls (None if this game data lacks it)."""
        mod = next((m for m in self.pools.mods if m["id"] == RESIST_CRAFTS.get(base_name)), None)
        if mod is None:
            return None
        return Affix(mod["id"], mod["group"], mod["type"], 1, tuple(roll(line, self.pools.roll) for line in mod_lines(mod)),
                     crafted=True, ranged=True)

    def radius_rare(self, base: dict, limits: dict[str, int], found: tuple,
                    craft: str = "radius") -> tuple[float | None, ItemOption] | None:
        """(JEWEL3) (price, the jewel) for a whole Very Large radius shape greedy filled: 1 prefix + 3 suffixes by the 0.5
        route (rare_prices.jewel_craft with Ancient Potent Liquid Contempt and Melancholy in place of the basic emotions),
        1 prefix + 2 suffixes by the emotion alone (RADIUS_TRIES x (the jewel + one Ancient Potent Liquid Melancholy)).
        (JEWEL4) craft "resist": Ancient Potent Liquid Ferocity's radius resistance, the mirror (a suffix craft: 3 prefixes
        by the 0.5 route with "+1 Prefix Modifier allowed", or 2 prefixes + 1 suffix by the emotion alone).
        None without the emotions' prices (outside SSF)."""
        _, chosen, priced = found
        prices = self.ctx.payload.get("ancient_emotion_prices") or {}
        level = self.pools.item_level
        emotion, third = (ANCIENT_MELANCHOLY, "Suffix") if craft == "radius" else (ANCIENT_FEROCITY, "Prefix")
        line = next((" / ".join(a.lines) for a in chosen if a.crafted), "")
        if self.ctx.ssf:
            price, route = None, f"the {emotion} craft (SSF: unpriced)"
        elif emotion not in prices or (limits[third] == 3 and ANCIENT_CONTEMPT not in prices):
            return None
        elif limits[third] == 3:
            materials = {**rare_prices.active().materials, "Potent Liquid Contempt": prices[ANCIENT_CONTEMPT],
                         "Potent Liquid Ferocity": prices[emotion]}
            made = rare_prices.jewel_craft(chosen, materials, lambda keep: rare_price("Jewel", keep))
            # (without the route's other materials priced (Chaos Orbs, the Erasure Omen): the jewel and both emotions,
            # about two tries, LOW)
            price = made["total"] if made is not None else RADIUS_TRIES * (
                rare_price("Jewel", priced) + prices[ANCIENT_CONTEMPT] + prices[emotion])
            route = (f"the 0.5 route with Ancient emotions: Ancient Potent Liquid Contempt's \"+1 {third} Modifier allowed\", "
                     f"a third {third.lower()}, the +1 line removed, then {emotion} (\"{line}\"); about {price:.3g} div (LOW, "
                     "as the effect craft's route"
                     + ("" if made is not None else "; the route's Chaos Orbs and Omen unpriced: the jewel and both "
                                                    "emotions, about two tries") + ")")
        else:
            price = RADIUS_TRIES * (rare_price("Jewel", priced) + prices[emotion])
            route = (f"buy the jewel with these 3 mods and any fourth, then {emotion} (it removes a random modifier and adds "
                     f"\"{line}\"); about two tries, {price:.3g} div (LOW)")
        return price, ItemOption("Rare Jewel", base["name"], "Jewel", None, rare_text("Jewel", base, level, chosen), False,
                                 price, "ssf" if self.ctx.ssf else "estimate (craft route: Ancient emotions)", 0, rare=True,
                                 affixes=tuple(line for a in chosen for line in a.lines),
                                 craft_route=(f"Crafted: {route}.",))

    @property
    def jewel_tags(self) -> dict[str, tuple[str, ...]]:
        """(JEWEL3) Each jewel mod's tags (game data's modTags), for the catalysts."""
        if self.__dict__.get("_jewel_tags") is None:
            self._jewel_tags = {m["id"]: tuple(m.get("modTags") or ()) for m in self.ctx.data.mods.get("Jewel", [])}
        return self._jewel_tags

    def catalysed_jewel(self, slot: str, base: dict, chosen: list, priced: list, value: float, boost, pool: list,
                        left: float) -> tuple[float, ItemOption] | None:
        """(JEWEL3, POE2_SEARCH_JEWEL_CATALYST=1; R57-9) This whole rare jewel (`chosen`, score less price's weight `value`)
        with 20% quality of one Refined catalyst: every catalyst whose tag one of its affixes carries is scored, priced as
        the jewel + CATALYSTS_PER_20 x the catalyst (exchange); the best kept when it beats the plain jewel after prices.
        A line the effect line also raises (`boost`: (kind, percent)) is written at effect + quality and not tagged (the
        lower, adding reading); the others are tagged for PoB's own scaling."""
        prices = self.ctx.payload.get("jewel_catalyst_prices") or {}
        tags, level, lam = self.jewel_tags, self.pools.item_level, self.gear.lam
        names = [n for n, plain in JEWEL_CATALYSTS.items() if (self.ctx.ssf or n in prices)
                 and any(not a.crafted and catalyst_matches(plain, a, tags) for a in chosen)]
        if not names:
            return None
        original = {a.id: a for a in pool}
        cases = []  # (exchange name, affixes as written, the text, cost)
        for name in names:
            plain = JEWEL_CATALYSTS[name]
            written, scaled = [], []
            for a in chosen:
                hit = not a.crafted and catalyst_matches(plain, a, tags)
                if hit and boost and a.type == boost[0]:
                    source = original.get(a.id, a)
                    written.append(dataclasses.replace(a, lines=tuple(boosted(line, boost[1] + CATALYST_QUALITY)
                                                                      for line in source.lines)))
                    continue
                written.append(a)
                if hit:
                    scaled.append(a)
            text = catalysed_text(rare_text("Jewel", base, level, written), scaled, tags, plain, CATALYST_QUALITY)
            cost = 0.0 if self.ctx.ssf else CATALYSTS_PER_20 * prices[name]
            if self.ctx.ssf or rare_price("Jewel", priced) + cost <= left + 1e-9:
                cases.append((name, written, text, cost))
        if not cases:
            return None
        scored = self.build.item_deltas([{"slot": slot, "raw": text} for _, _, text, _ in cases])
        found, _, i = max(((s - lam * (rare_price("Jewel", priced) + c[3]), -c[3], i)
                           for i, ((s, _), c) in enumerate(zip(scored, cases))), key=lambda x: x[:2])
        if found <= value + EPSILON:
            return None
        name, written, text, cost = cases[i]
        plain = JEWEL_CATALYSTS[name]
        raised = sum(1 for a in chosen if not a.crafted and catalyst_matches(plain, a, tags))
        note = (f"{name} x{CATALYSTS_PER_20}: {CATALYST_QUALITY}% quality raises the {raised} modifier"
                f"{'s' if raised != 1 else ''} with its tag ({'/'.join(CATALYSTS[plain][1])}) by {CATALYST_QUALITY}% "
                f"({cost:.3g} div; {CATALYSTS_PER_20} per {CATALYST_QUALITY}% is LOW and UNVERIFIED, R57-9; the jewel "
                "maximum of 20% is MEDIUM)")
        if boost and any(a.type == boost[0] and not a.crafted and catalyst_matches(plain, a, tags) for a in chosen):
            note += (f". The {boost[0].lower()}es it raises also take the effect line's {boost[1]:g}%: counted as adding "
                     f"(x{1 + (boost[1] + CATALYST_QUALITY) / 100:.2f}); if the two multiply (UNVERIFIED) they read "
                     f"x{(1 + boost[1] / 100) * (1 + CATALYST_QUALITY / 100):.2f}")
        price = None if self.ctx.ssf else rare_price("Jewel", priced) + cost
        return found, ItemOption("Rare Jewel", base["name"], "Jewel", None, text, False, price,
                                 "ssf" if self.ctx.ssf else "estimate (craft route: catalysts)", 0, rare=True,
                                 # (the lines as bought: the quality goes on after, so the trade search is the plain jewel's)
                                 affixes=tuple(line for a in chosen for line in a.lines), craft_route=(note,))

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
                      deadline: float, boost: tuple[str, float] | None = None) -> tuple[float, ItemOption] | None:
        """(SEARCH31) The plain-shape rare jewel `chosen` (score less price's weight: `value`) with one Desecrated jewel
        affix by a Preserved Cranium (desecrated.py "cranium"; RESEARCH34 R33-22, MEDIUM-HIGH), added where its side has
        room or in place of each affix of its side (a full side loses a random mod: the jewel is bought with that slot
        open), priced by the craft route; (value, option) if it beats `value`, else None.
        (JEWEL4, CRANIUM_CRAFTED; AUDIT38 F9 (b)) With `boost` ((kind, percent) of the effect line), `chosen` is the crafted
        3 + 1 + effect jewel: the Desecrated line goes on either side with nothing removed (6 lines, as real listings show;
        MEDIUM-LOW), a side at most CRANIUM_SIDE_MAX; one on the boosted side is written under the effect line too (a
        Desecrated suffix is a suffix; HIGH that the effect line reaches it: R59-21, measured on live listings #221 / #333; the
        order of the steps and whether "full" means a side or the whole jewel stay UNVERIFIED, R59-18); priced as the crafted
        jewel's route plus
        the Desecration's."""
        build, lam, level, name = self.build, self.gear.lam, self.pools.item_level, base["name"]
        pool = self.cranium_pools.pool(name)
        groups = {a.group for a in chosen}
        fresh = [a for a in pool if a.group not in groups and a.type in JEWEL_LIMIT]
        if boost:
            sides = {k: sum(1 for x in chosen if x.type == k and not x.crafted) for k in JEWEL_LIMIT}
            fresh = [a for a in fresh if sides[a.type] < CRANIUM_SIDE_MAX]

        def written(a):  # (JEWEL4) the line as it sits on the jewel: raised by the effect line on its side
            if boost and a.type == boost[0]:
                return dataclasses.replace(a, lines=tuple(boosted(line, boost[1]) for line in a.lines))
            return a

        if not fresh or clock.now() > deadline:
            return None
        ssf = self.ctx.ssf
        prices = self.ctx.desecration_prices if not ssf else {m: 0.0 for m in desecrated.MATERIALS}
        ordinary = {}  # the reveal's ordinary rows per side (equal weights, as gear.desecrated_trial)
        for a in self.pools.pool(name):
            if a.side_odds > 0:
                ordinary[a.type] = round(1 / a.side_odds)
        screen = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen + [written(a)])}
                                    for a in fresh])
        top = [a for s, a in sorted(((s, a) for (s, _), a in zip(screen, fresh)), key=lambda x: -x[0])][:self.CRANIUM_KEEP]
        trials = []  # (affixes, the Desecrated affix, price, steps)
        for a in top:
            for c in self.cranium_pools.choices(a, chosen):
                if boost or sum(1 for x in chosen if x.type == c.type) < JEWEL_LIMIT[c.type]:
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
                        if boost:  # (JEWEL4) step 1 is the 0.5 craft, and nothing is removed (MEDIUM-LOW)
                            steps = [f"1. craft the jewel by the 0.5 route ({market:.3f} div, its craft route's price); "
                                     "the Desecration adds a sixth line with nothing removed, as real 6-line listings "
                                     "show (MEDIUM-LOW, AUDIT38 F9 #219 / #221 / #333)", *steps[1:]]
                        trials.append((kept + [written(c)], c, price, steps))
        if not trials or clock.now() > deadline:
            return None
        scored = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, t[0])} for t in trials])
        found, i = max(((s - lam * t[2], i) for i, ((s, _), t) in enumerate(zip(scored, trials))), key=lambda x: x[0])
        if found <= value + EPSILON:
            return None
        affixes, affix, price, steps = trials[i]
        note = (f"one Desecrated modifier ({affix.desecrated}, {affix.type}) by a Preserved Cranium; priced by the cheapest "
                f"repeat-until-hit craft route (LOW: no Omen names a jewel row; desecrated.py)")
        if boost:
            note += (f", on the crafted 3 + 1 + effect jewel with nothing removed (MEDIUM-LOW: 6-line listings exist, AUDIT38 "
                     "F9; no source says when a Desecration here removes a modifier: if it removes one, this jewel can't "
                     "be made and the 5-line one stands)")
            if affix.type == boost[0]:
                note += (f"; the effect line's {boost[1]:g}% is counted on the Desecrated {affix.type.lower()} too "
                         "(HIGH: RESEARCH59 R59-21, measured on live listings AUDIT38 #221 / #333)")
        return found, ItemOption("Rare Jewel", name, "Jewel", None, rare_text("Jewel", base, level, affixes), False,
                                 None if ssf else price, "ssf" if ssf else "estimate (craft route: Desecration)", 0,
                                 rare=True, affixes=tuple(line for a in affixes for line in a.lines),
                                 craft_route=(note, *steps))

    def greedy(self, slot: str, base: dict, shortlist: list, limits: dict[str, int], fixed: list, left: float,
               deadline: float, fixed_only: bool = False, full: bool = False,
               free: tuple = ()) -> tuple[float, list, list] | None:
        """Add affixes to `fixed` while one pays: (score less the price's weight, affixes, the affixes priced), or None
        when nothing beyond `fixed` fits the budget or pays (unless `fixed_only` allows that). With `full` (JEWEL1), go on
        to the shape's last slot even when no affix pays any more (the user, 23 Nov 2026: "having empty mods on an item is
        stupid and wasteful"; the manager 06:15: "fill all slots ... with the best-value mods, priced"): each further slot
        takes the affordable affix with the best score less its price's weight, priced like the rest. `free` (JEWEL3):
        affixes of `fixed` left out of the market price (a crafted line its caller prices by its own route).
        """
        build, lam, level = self.build, self.gear.lam, self.pools.item_level
        chosen, priced = list(fixed), [a for a in fixed if a not in free]
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
        if full and clock.now() >= deadline:
            # (JEWEL2, the empty-slot red flag: SEARCHB7's POE2T6363 delivered a Ruby with the effect line and 2 mods) The
            # clock stops the scored steps, not the jewel: its open slots take the shortlist's best-ranked affordable affixes
            # unscored, and the finished jewel is scored once
            added = False
            for a in shortlist:
                counts = {k: sum(1 for c in chosen if c.type == k and not c.crafted) for k in limits}
                if a.group in {c.group for c in chosen} or counts[a.type] >= limits[a.type]:
                    continue
                pick = next((c for c in self.pools.choices(a, chosen)
                             if rare_price("Jewel", priced + [c]) <= left + 1e-9), None)
                if pick is not None:
                    chosen.append(pick)
                    priced.append(pick)
                    added = True
            if added:
                current = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, level, chosen)}])[0][0] \
                    - lam * rare_price("Jewel", priced)
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

    def mark_unfinished(self, raw: str, chosen: list, limits: dict[str, int], deadline: float) -> None:
        """(JEWEL3) Remember a jewel left short of its shape's slots (crafted lines aside) and why, for unfinished_notes."""
        filled = sum(1 for a in chosen if not a.crafted)
        why = ("the search's time for it ran out first" if clock.now() >= deadline
               else "no affix it could still add fitted (the budget, or none left that can roll beside these)")
        self.unfinished[raw] = {"filled": filled, "slots": sum(limits.values()), "why": why}

    def unfinished_notes(self) -> list[dict]:
        """(JEWEL3; THE USER 24 Nov) A "not finished" note for each delivered jewel short of its slots: which jewel, how many
        slots it fills, why it stopped, and that a Continue run can finish it (SEARCH-D's record takes these over)."""
        notes = []
        for slot, option in sorted(self.build.equipped.items()):
            info = self.unfinished.get(option.raw) if option is not None else None
            if info:
                notes.append({
                    "label": f"Not finished: the {option.base} jewel in {slot} fills {info['filled']} of its "
                             f"{info['slots']} affix slots",
                    "value": f"{info['filled']}/{info['slots']}",
                    "basis": f"It stopped because {info['why']}. A part-filled jewel beats an empty socket (the user, 24 Nov "
                             "2026), so it is kept as it is; a Continue run can finish it",
                    "confidence": "HIGH"})
        return notes

    def radius_notes(self) -> list[dict]:
        """The larger Time-Lost radius and the radius damage transforms, for the result's assumptions (asterisked), and
        (JEWEL3) Cadigan's Epiphany's and Melancholy's Exposure's notes (cadigan_notes, exposure_notes) and the "not
        finished" notes (unfinished_notes)."""
        notes = self.transform_notes() + self.cadigan_notes() + self.exposure_notes() + self.unfinished_notes()
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
            if self.fill(socket, deadline, rebuild=full and index == 0):
                improved = True
                if (checkpoint := getattr(self, "checkpoint", None)) is not None:
                    checkpoint()  # (SEARCHD1 hook, F1: a hard stop later in the round keeps this socket's jewel)
        for slot, bases in self.item_sockets():
            if clock.now() > deadline:
                break
            if self.fill_item_socket(slot, bases, deadline):
                improved = True
                if (checkpoint := getattr(self, "checkpoint", None)) is not None:
                    checkpoint()  # (SEARCHD1 hook, F1: as above, an item's jewel socket)
        if full and clock.now() < deadline:
            improved = self.item_socket_trial(deadline) or improved
        if full and clock.now() < deadline:
            improved = self.cadigan_trial(deadline) or improved
        if full and clock.now() < deadline and EXPOSURE_JEWEL:
            improved = self.exposure_trial(deadline) or improved
        if full and clock.now() < deadline and ADORNED_JEWEL:
            improved = self.adorned_trial(deadline) or improved
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

    def cadigan_gloves(self, option: ItemOption | None) -> ItemOption | None:
        """(JEWEL2) These gloves with Cadigan's Epiphany: their augment sockets and runes gone, one jewel socket in their
        place, priced as the gloves less their runes plus the augment. None when they can't take it (no augment socket,
        Corrupted / Sanctified, a socket-bound rune in them, a jewel socket already, or the catalog doesn't offer it)."""
        gear, ctx = self.gear, self.ctx
        if option is None or option.type != "Gloves" or CADIGAN in option.runes or SEALED.search(option.raw):
            return None
        base = ctx.bases_by_name.get(option.base)
        if base is None or socket_count(base, option.unique, unique_text(ctx, option)) == 0:
            return None
        if item_jewel_sockets(option.raw)[0] or any(n in option.bound for n in option.runes):
            return None
        if CADIGAN not in gear.runes.candidates(base, option.unique, option.raw):
            return None
        lines = with_runes(option.raw, []).splitlines()
        at = next((i for i, line in enumerate(lines) if line.startswith("Implicits:")), None)
        if at is None:
            at = next((i + 1 for i, line in enumerate(lines) if line.startswith("Item Level:")), len(lines))
        price = option.price
        if price is not None:
            price += (gear.runes.price(CADIGAN) or 0.0) - sum(gear.runes.price(n) or 0.0 for n in option.runes)
        return dataclasses.replace(option, raw="\n".join(lines[:at] + ["Sockets: J"] + lines[at:]), runes=(CADIGAN,),
                                   price=price)

    def cadigan_trial(self, deadline: float) -> bool:
        """(JEWEL2) The equipped gloves with Cadigan's Epiphany and their new jewel socket filled with a plain rare (item
        sockets take no unique jewel: R-J4), kept only if score less the budget weight x spend rises."""
        build, gear = self.build, self.gear
        trial = self.cadigan_gloves(build.equipped.get("Gloves"))
        if trial is None or (trial.price or 0.0) > gear.left_for("Gloves") + 1e-9:
            return False
        snap, before = build.snapshot(), build.score - gear.lam * build.spent()
        before_stats = dict(build.stats)
        build.equip("Gloves", trial)
        gear.settle()
        self.fill_item_socket(CADIGAN_SLOT, JEWEL_BASES, deadline)
        if build.score - gear.lam * build.spent() > before + EPSILON:
            self.cadigan_kept = self.cadigan_record(before_stats, build.stats)
            return True
        build.restore(snap)
        return False

    def cadigan_record(self, before: dict, after: dict) -> dict:
        """(JEWEL3) What Cadigan's Epiphany did when the search kept it, for the result's note: DPS after / before and
        each request floor or game limit whose shortfall it closed or opened (label -> (short before, short after), the
        normalized deficits of Objective.deficits; 0 = met), with the floor's stat values before and after."""
        from builder.search.objective import number

        objective = self.build.objective
        dps_before, dps_after = objective.dps(before), objective.dps(after)
        short_before = {label: amount for label, amount, _ in objective.deficits(before)}
        short_after = {label: amount for label, amount, _ in objective.deficits(after)}
        floors = {}
        for label in sorted(set(short_before) | set(short_after)):
            was, now = short_before.get(label, 0.0), short_after.get(label, 0.0)
            if abs(was - now) > 1e-9:
                penalty = next((p for p in objective.penalties if p.label == label), None)
                values = None if penalty is None else (number(before.get(penalty.stat)), number(after.get(penalty.stat)),
                                                       penalty.floor)
                floors[label] = (was, now, values)
        gloves, jewel = self.build.equipped.get("Gloves"), self.build.equipped.get(CADIGAN_SLOT)
        return {"ratio": dps_after / dps_before if dps_before > 0 else None, "floors": floors,
                "gloves": gloves.name if gloves is not None else "the gloves",
                "jewel": tuple(getattr(jewel, "affixes", None) or ())}

    def cadigan_notes(self) -> list[dict]:
        """(JEWEL3; the manager, 24 Nov 00:46) The result's note for Cadigan's Epiphany on the delivered gloves, in plain
        words: what it traded (the gloves' runes for a jewel socket), DPS after / before at the trial, and, when DPS fell,
        the floor it was kept for (2b75e, POE2T7956: x0.950 DPS for the user's default 25% movement speed floor)."""
        gloves = self.build.equipped.get("Gloves")
        record = getattr(self, "cadigan_kept", None)
        if gloves is None or CADIGAN not in gloves.runes or not record:
            return []
        ratio = record["ratio"]
        jewel = ", ".join(record["jewel"]) or "a rare jewel"
        met, worse = [], []
        for label, (was, now, values) in record["floors"].items():
            seen = "" if values is None else f" ({values[0]:g} -> {values[1]:g}, floor {values[2]:g})"
            if now < was:
                met.append(f"{label}: {'met' if now <= 0 else 'closer'}{seen}")
            else:
                worse.append(f"{label}: {'now short' if was <= 0 else 'further short'}{seen}")
        traded = ("The gloves' augment sockets and the runes in them were given up for one jewel socket, filled with "
                  f"{jewel}.")
        if ratio is not None and ratio < 1.0 - 1e-4:
            label = f"Cadigan's Epiphany on {record['gloves']}: DPS x{ratio:.3f}, kept to meet a floor"
            why = (f" That lowers DPS to x{ratio:.3f} of the gloves with their runes, but it "
                   + ("closes " + "; ".join(met) if met else "changes no floor that was short")
                   + ". The search ranks a build short of a request floor below one that meets it, so it took the DPS "
                     "loss: this is the floor doing its job (the default 25% movement speed floor is the user's). "
                     "Lower or remove that floor in the request to keep the runes and the DPS.")
        else:
            label = f"Cadigan's Epiphany on {record['gloves']}: DPS x{ratio:.3f}" if ratio is not None else (
                f"Cadigan's Epiphany on {record['gloves']}")
            why = " The jewel adds more than the runes did." + (" It also closes " + "; ".join(met) + "." if met else "")
        if worse:
            why += " It leaves short: " + "; ".join(worse) + "."
        return [{"label": label, "value": f"x{ratio:.3f}" if ratio is not None else "",
                 "basis": traded + why + " (DPS measured when the search tried it, on the build as it was then.)",
                 "confidence": "HIGH"}]

    # The Adorned (JEWEL3) --------------------------------------------------------------------------

    def adorned_versions(self) -> list[ItemOption]:
        """The Adorned at each offered roll (ADORNED_ROLLS), its Current version's lines with the roll written in."""
        found = next((o for o in self.ctx.item_options.get("Jewel", []) if o.unique and o.name == ADORNED), None)
        if found is None or ADORNED in {o.name for o in self.build.equipped.values() if o is not None}:
            return []
        variants = variants_of(found.raw)
        raw = found.raw
        if variants is not None and variants.usable:
            raw = compact(with_variants(found.raw, variants, (variants.usable[-1],) * len(variants.slots)),
                          (variants.usable[-1],) * len(variants.slots))
        out = []
        for roll, floor in ADORNED_ROLLS.items():
            text = ADORNED_RANGE.sub(f"{roll}% increased Effect of Jewel Socket Passive Skills", raw)
            if text == raw:
                continue
            price = None if self.ctx.ssf else max(found.price or 0.0, floor)
            out.append(dataclasses.replace(found, raw=text, price=price, price_note=(
                f"rolled {roll}%: the trade floor for {roll}%+ ({floor:g} div, R52-4, 23 Nov 2026; MEDIUM-LOW)")))
        return out

    def magic_jewel(self, slot: str, deadline: float) -> ItemOption | None:
        """(JEWEL3) The best corrupted magic jewel (1 prefix + 1 suffix, both filled) for this socket on a basic base."""
        best = None
        left = self.gear.left_for(slot) / ADORNED_TRIES
        vaal = 0.0 if self.ctx.ssf else ((self.ctx.payload.get("craft_prices") or {}).get("Vaal Orb") or 0.0)
        for name in JEWEL_BASES:
            base = self.ctx.bases_by_name.get(name)
            pool = self.pools.pool(name) if base else []
            if not pool or clock.now() > deadline:
                continue
            ranked = self.ranked(slot, base, pool)
            shortlist = [a for kind in MAGIC_JEWEL for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
            found = self.greedy(slot, base, shortlist, MAGIC_JEWEL, [], left - vaal, deadline, full=True)
            if not found:
                continue
            value, chosen, priced = found
            if best is None or value > best[0]:
                price = None if self.ctx.ssf else ADORNED_TRIES * (rare_price("Jewel", priced) + vaal)
                text = magic_text("Jewel", base, self.pools.item_level, chosen) + "\nCorrupted"
                if not shape_full(chosen, MAGIC_JEWEL):  # (kept, with the note: THE USER 24 Nov)
                    self.mark_unfinished(text, chosen, MAGIC_JEWEL, deadline)
                best = (value, ItemOption("Magic Jewel", name, "Jewel", None, text, False, price,
                                          "ssf" if self.ctx.ssf else "estimate (magic jewel + Vaal Orb, LOW)", 0,
                                          affixes=tuple(line for a in chosen for line in a.lines),
                                          craft_route=("A magic jewel with these two mods, corrupted with a Vaal Orb "
                                                       "(assumed to leave it as it is: UNVERIFIED; about two tries, "
                                                       "LOW)" + ("" if vaal else "; the Vaal Orb unpriced") + ".",)))
        return best[1] if best else None

    def adorned_trial(self, deadline: float) -> bool:
        """(JEWEL3, POE2_SEARCH_ADORNED=1) The Adorned in the weakest socket that takes a unique, corrupted magic jewels in
        every other normal tree socket (not Sinister, not one the tree holds nodes through), each roll tried; equipped
        together and settled (a real trial), kept only if score less the budget weight x spend rises."""
        build, gear = self.build, self.gear
        versions = self.adorned_versions()
        target = self.weakest_socket()
        if not versions or target is None:
            return False
        others = [s for s in self.sockets() if s != target and s not in self.restricted and not self.holds(s)]
        if len(others) < 2:
            return False
        magic = self.magic_jewel(self.slot(others[0]), deadline)
        if magic is None:
            return False
        improved = False
        for version in versions:
            if clock.now() > deadline:
                break
            plan = [(self.slot(target), version)] + [(self.slot(s), magic) for s in others]
            if any((o.price or 0.0) > gear.left_for(slot) + 1e-9 for slot, o in plan):
                continue
            snap, before = build.snapshot(), build.score - gear.lam * build.spent()
            for slot, option in plan:
                build.equip(slot, option)
            gear.settle()
            if build.score - gear.lam * build.spent() > before + EPSILON:
                improved = True
                continue  # (the next roll is tried against this one)
            build.restore(snap)
        return improved

    # Melancholy's Exposure (JEWEL3) ---------------------------------------------------------------

    def exposure_affix(self) -> Affix | None:
        """The crafted Exposure suffix as an affix (None if this game data lacks it)."""
        mod = next((m for m in self.pools.mods if m["id"] == EXPOSURE_CRAFT), None)
        if mod is None:
            return None
        return Affix(mod["id"], mod["group"], mod["type"], 1, tuple(mod_lines(mod)), crafted=True)

    def exposure_price(self, priced: list) -> float | None:
        """The Exposure jewel's expected cost: EXPOSURE_TRIES x (the jewel with its chosen mods, by the market / craft
        model, plus one Potent Liquid Melancholy from the exchange; 0 with no price for it). None in SSF."""
        if self.ctx.ssf:
            return None
        return EXPOSURE_TRIES * (rare_price("Jewel", priced) + (self.ctx.emotion_prices.get(MELANCHOLY) or 0.0))

    def exposure_rare(self, slot: str, deadline: float) -> ItemOption | None:
        """The best Sapphire for this socket with the crafted Exposure suffix: 2 prefixes + 1 suffix filled by greedy (full;
        the crafted line fixed and left out of the market price, exposure_price adding its route), within the slot's
        budget. None when it can't be made whole (the empty-slot red flag)."""
        base = self.ctx.bases_by_name.get(EXPOSURE_BASE)
        affix = self.exposure_affix()
        pool = self.pools.pool(EXPOSURE_BASE) if base else []
        if affix is None or not pool:
            return None
        left = self.gear.left_for(slot)
        if not self.ctx.ssf:  # (greedy prices one jewel: the route's tries and emotions come off the budget first)
            left = left / EXPOSURE_TRIES - (self.ctx.emotion_prices.get(MELANCHOLY) or 0.0)
        ranked = self.ranked(slot, base, pool)
        shortlist = [a for kind in EXPOSURE_SHAPE for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
        found = self.greedy(slot, base, shortlist, EXPOSURE_SHAPE, [affix], left, deadline, full=True, free=(affix,))
        if not found:
            return None
        if not shape_full(found[1], EXPOSURE_SHAPE):  # (kept, with the note: THE USER 24 Nov)
            self.mark_unfinished(rare_text("Jewel", base, self.pools.item_level, found[1]), found[1], EXPOSURE_SHAPE,
                                 deadline)
        _, chosen, priced = found
        route = (f"Buy a {EXPOSURE_BASE} with " + " + ".join(" / ".join(a.lines) for a in priced)
                 + " and any fourth suffix, then use a Potent Liquid Melancholy: it removes a random modifier and adds "
                 "the crafted Exposure suffix (fixed for a Sapphire). Priced at about two tries (LOW: if it removes the "
                 "chosen suffix, start again; which side it removes from is UNVERIFIED).",)
        return ItemOption("Rare Jewel", EXPOSURE_BASE, "Jewel", None,
                          rare_text("Jewel", base, self.pools.item_level, chosen), False, self.exposure_price(priced),
                          "ssf" if self.ctx.ssf else "estimate (craft route: Potent Liquid Melancholy)", 0, rare=True,
                          affixes=tuple(line for a in chosen for line in a.lines), craft_route=route)

    def exposure_plan(self, deadline: float) -> list[tuple[str, ItemOption]] | None:
        """Which tree sockets change for Melancholy's Exposure: the Exposure Sapphire in the weakest socket, and the best
        Ruby / Emerald rare (build_rare on that base) in the next weakest for each colour the tree lacks. Tree sockets
        only (Conditions._tree_jewels reads "Jewel <node>" slots, Sinister ones included); a socket the tree holds nodes
        through (holds) or the last tree jewel of a needed colour is never taken. None when there aren't enough
        sockets, or a jewel of the plan can't be made."""
        build, level = self.build, self.pools.item_level
        tree = {slot: o for slot, o in build.equipped.items() if slot.startswith("Jewel ") and o is not None}
        if any(EXPOSURE_CRAFT in (o.raw or "") or any(EXPOSURE_LINE in line for line in o.affixes) for o in tree.values()):
            return None  # (one is in already: the fills and gear rounds keep it on its worth)
        bases = [o.base for o in tree.values()]
        missing = [c for c in EXPOSURE_PAIR if c not in bases]
        sockets = [s for s in self.sockets() if not self.holds(s)]
        held = {s: build.equipped.get(self.slot(s)) for s in sockets}

        def sole(s: int) -> bool:  # the last tree jewel of a needed colour
            return held[s] is not None and held[s].base in EXPOSURE_PAIR and bases.count(held[s].base) == 1

        free = [s for s in sockets if not sole(s)]
        if len(free) < 1 + len(missing):
            return None
        filled = [s for s in free if held[s] is not None and held[s].base in self.ctx.bases_by_name]
        emptied = build.item_deltas([{"slot": self.slot(s), "raw": rare_text(
            "Jewel", self.ctx.bases_by_name[held[s].base], level, [])} for s in filled]) if filled else []
        scored = {s: v for s, (v, _) in zip(filled, emptied)}
        # weakest first: empty sockets, then those whose jewel adds least (the build's score with it emptied: highest)
        free.sort(key=lambda s: (held[s] is not None, -scored.get(s, -1e9)))
        target = self.slot(free[0])
        sapphire = self.exposure_rare(target, deadline)
        if sapphire is None:
            return None
        plan = [(target, sapphire)]
        for colour, socket in zip(missing, free[1:]):
            rare = self.build_rare(self.slot(socket), deadline, bases=(colour,))
            if rare is None or clock.now() > deadline:
                return None
            plan.append((self.slot(socket), rare))
        return plan

    def exposure_trial(self, deadline: float) -> bool:
        """(JEWEL3) Melancholy's Exposure: the plan's jewels equipped together and settled (Conditions reads the line,
        so this is a real trial, not a what-if), kept only if score less the budget weight x spend rises."""
        build, gear = self.build, self.gear
        if self.exposure_affix() is None:
            return False
        plan = self.exposure_plan(deadline)
        if not plan or any((o.price or 0.0) > gear.left_for(slot) + 1e-9 for slot, o in plan):
            return False
        before_stats = dict(build.stats)
        snap, before = build.snapshot(), build.score - gear.lam * build.spent()
        for slot, option in plan:
            build.equip(slot, option)
        gear.settle()
        if build.score - gear.lam * build.spent() > before + EPSILON:
            d0, d1 = build.objective.dps(before_stats), build.objective.dps(build.stats)
            self.exposure_kept = {"ratio": d1 / d0 if d0 > 0 else None, "slots": [slot for slot, _ in plan],
                                  "sapphire": plan[0][0]}
            return True
        build.restore(snap)
        return False

    def exposure_notes(self) -> list[dict]:
        """(JEWEL3) The result's note for a delivered Exposure Sapphire: what it needs kept socketed, DPS at its trial."""
        record = self.exposure_kept
        jewel = self.build.equipped.get(record["sapphire"]) if record else None
        if jewel is None or not any(EXPOSURE_LINE in line for line in jewel.affixes):
            return []
        ratio = record["ratio"]
        pair = [f"{o.base} ({slot})" for slot, o in self.build.equipped.items()
                if slot.startswith("Jewel ") and o is not None and o.base in EXPOSURE_PAIR]
        return [{"label": f"Melancholy's Exposure on the Sapphire in {record['sapphire']}"
                          + (f": DPS x{ratio:.3f} when the search took it" if ratio else "") + " (custom model)",
                 "value": f"x{ratio:.3f}" if ratio else "",
                 "basis": ("Its crafted line inflicts Elemental Exposure on Hit only while a Ruby and an Emerald are "
                           "socketed in the passive tree: keep " + (", ".join(pair) or "a Ruby and an Emerald")
                           + " there. PoB doesn't read the line; the search's conditions do. DPS measured with the jewels "
                           "changed together, on the build as it was then; the craft's price is LOW (about two tries)."),
                 "confidence": "MEDIUM"}]

    def shard_helmet(self, slot: str, option: ItemOption) -> ItemOption | None:
        """(JEWEL1) The helmet with a Raven-Touched Shard in a free socket or in place of its last rune, so it takes an
        anoint (anoints.capacity; RESEARCH52 R52-6: 9 of the user's 14 example builds with Zarokh's Gift anoint it on a
        helmet, 17 of 20 helmets carry the shard), or None when the helmet can't take one. (JEWEL5, SEARCHB8's ask) Built
        by Gear.shard_version, shard_trial's own builder (its rules: no Corrupted / Sanctified helmet, a socket-bound rune
        never replaced, one shard per character, the rune catalog's price), not by a copy of it."""
        return self.gear.shard_version(slot, option)

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
            rare = self.build_rare(self.slot(socket), deadline, bases=radius_rare_bases())
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
