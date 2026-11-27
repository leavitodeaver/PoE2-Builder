"""The inner loop (PLAN.md §5.2), run inside a worker process next to its engine: one skeleton,
block coordinate ascent over gems, gear, ascendancy, tree, attributes, conditions, condition
sources and persistent skills, then kicks (iterated local search) until the time is up.

Worker tasks (called through EnginePool.run_tasks):
- `optimize_task`: start a skeleton from scratch, or continue from a saved state
- `screen_task`: quick main-skill DPS for many skills, to shortlist when no main skill is given
- `finalize_task`: Full DPS polish, final facts, PoB code, and a reload check of that code
"""

from __future__ import annotations

import copy
import math
import os
import random
import re
import time
from dataclasses import replace
from typing import Any, Callable

from builder import clock
from builder.ledger.coverage import build_blind_spots
from builder.ledger.parse_audit import GATED_FIELDS
from builder.pob import codes
from builder.request.constraints import FACT_FIELDS, meta_socketed
from builder.request.schema import WeaponSwap
from builder.search.assumptions import Assumptions, pressed_group
from builder.search.conditions import (
    LOW_LIFE_LINE, LOW_LIFE_SHARE, MELEE_DISTANCE, STATIONARY_LINE, UPTIME, Conditions, distance_candidates,
    moving_candidates,
)
from builder.search.context import GEM_LEVEL, GEM_QUALITY, Context, ItemOption, context_for
from builder.search.gear import CHARM_SLOTS, Gear, SlotSpec, extra_ring_passive, is_flask_slot, owned_option
from builder.search.gems import META_LABEL, Gems
from builder.search.jewels import Jewels

# (SEARCHD3) how far (passive points) the tree may go for a socket to hold a required unique Jewel when none is allocated: a
# search reach, not a game number (jewels.SOCKET_REACH, 4, is the score-driven socket trial's)
REQUIRED_SOCKET_REACH = 40
from builder.search import flasks, play_guide
from builder.search import gems as gems_module
from builder.search.models import GLORY_SKILLS, MARK_CARRIERS, MODEL_FIELDS, PRESS_CHOICES, press_choices, press_use_rate
from builder.search.models import press_action
from builder.search.objective import (
    CLEAR_FIELDS, DARKNESS_FIELDS, RECOVERY_INPUTS, SKILL_SLOTS, SKILL_SLOTS_POB, SUSTAIN_FIELDS, Objective, main_dps,
    number, shortfall,
    with_boss_recovery,
    with_life_es,
)
from builder.search.skeletons import Skeleton, is_button, offhand_non_unique, offhand_types, seed_skeleton, slot_bases, swap_skills
from builder.search.sources import Sources
from builder.search.state import EPSILON, Build, OutOfTime, model_valued_supports
from builder.search.tree import Tree
from builder.search.usage import Usage
from builder.search import unfinished  # (SEARCHD1 hook: the "Not finished" record)
from builder.search import required_items  # (SEARCHD3: required items before the skeleton's weapons)

KICK_TREE_POINTS = (6, 16)
SWEEP_EVERY = 3
# A full gear round on a build that has gear gets this share of the time left, and the next full round goes on from the
# slot it stopped at: on gate 4's 2b75b one full round took a whole 240 s focus task (all of round 2), so no support,
# tree or jewel step ran there (POE2T202). A fresh build's first round (nothing equipped) has no cap: without it the
# build has no DPS to search from
GEAR_SHARE = 0.4
# M1 (SEARCH_PLAN.md): a line carries its block pass across tasks (state "line"): the line's pass count, the step its
# last task stopped at, and whether that pass sweeps. Without it every task started a new pass with the full gear sweep
# (half of a 235 s focus task on 2b75b) and no task got past one pass (M0: passes_done 0 in all 15 tasks of POE2T730).
# POE2_SEARCH_M1=0 turns it off for an A/B run
PERSIST_LINES = os.environ.get("POE2_SEARCH_M1", "1") != "0"
# M2 (SEARCH_PLAN.md, method C): once per line, from the line's second pass on (its gear swept once), the passive tree is
# cleared (required passives stay) and regrown against the gear and supports it has now; kept only if the build scores
# higher. Why: the tree grows into its free points in the line's first pass, when the gear is half-chosen (2b75b: 1-86K
# DPS), and stayed frozen after that (tree time 0 s in 103 of 105 round-3 tasks, SEARCH_PLAN.md §2.2).
# POE2_SEARCH_M2=0 turns it off for an A/B run
TREE_REBUILD = os.environ.get("POE2_SEARCH_M2", "1") != "0"
TREE_REBUILD_MIN = 60.0  # seconds a task must have left to try it (a rebuild cut short only loses; finalize never has it)
TREE_REBUILD_SHARE = 0.4  # of the task's time left that the regrow may take
# The press rates the search offers a side skill with no cooldown, no duration and nothing else setting its use (MECH55;
# THE USER, 12 Nov, card: "Search picks the rate"): uses per second, 0 first (ties keep the lower rate). A choice adds no
# button (the skill is already one, within the cap) and its time is charged against the main skill (press_model's busy
# share in Full DPS, Objective.press_busy otherwise). Search.press_round; POE2_SEARCH_PRESS=0 turns the round off.
PRESS_RATES = (0.0, 0.25, 0.5, 1.0, 2.0)
# (SEARCH41; MECHB66's trace on example build 12, the manager 25 Nov 20:50: a DPS OVERCOUNT) A side skill the build keeps
# up for what it gives other skills, with no chosen press rate, cost the main skill no time: Thunderstorm on b12 (GEM15's
# Drenched model and MECHB65's overgrown_share read it "at PoB's rate") took 0 of Entangle's time, while a curse's casts
# are charged in its own model. Search.upkeep_round charges each such skill at the use its own data gives (models.
# press_use_rate with no choice: on its cooldown, else once per its duration), each use press_action's seconds (its cast
# time with the build's speed), in objective.upkeep_busy (Objective.busy: the main skill's share of the fight), but only
# where the score USES it (its group switched off lowers the score, as fit_buttons' item-skill probe): a skill nothing
# reads is a button nobody needs to press. Its rate is in objective.upkeep_rates for the models that read its uptime.
# Left out, because another model charges the cast time already (checked: every "1% more Attack and Cast Speed" time
# line in models.py / assumptions.py on d374dcae: curse_model, mark_model, roar_model, warcry_press_model, parry_model,
# raised_model, varashta_blessing_model, annihilation_model, trail_of_caltrops / void_illusion (dodge rolls),
# remnant_presses, Kaom's embodiment, heavy_stunned_self, living_bomb / untether mains; glory_busy; press_model for
# pressed Full DPS groups; press_busy for chosen rates; AND (SEARCH41 turn 2, after probe POE2T7559 charged b01's Barrage
# twice) the models that put a skill's cast inside their own factor or rate, found by a search of models.py /
# assumptions.py / conditions.py for "main skill's time", "cast in the cycle", "costs the main skill" and the like:
# barrage_model (its 0.7 s cast in the cycle), Inevitable Agony's press, repulsion_wave, the minion Commands (COMMANDS)
# and djinn Commands (DJINN_COMMANDS), Dark / Abyssal Pact's casts, Incinerate's channel; and every press a model put on
# build.item_presses): curses (AppliesCurse), Marks, Warcries, the named skills (upkeep_charged_elsewhere), the
# models' own presses, and persistent / triggered skills (not buttons). POE2_SEARCH_UPKEEP=0: off.
# (SEARCH42, AUDIT40 A40-52: SEARCH41 left totems out on an UNVERIFIED reading, the same overcount as Thunderstorm's) A
# side totem is charged too: no model charges its placement (checked: models.py / assumptions.py / conditions.py on
# e2a9f00e and on origin/main b9c941b2 and mech2 for "placement", "place_time", "TotemPlacement", "placing", "busy":
# totem_count_model and the "Totems other than Spell Totem" note say "placing them isn't taken from your time", the
# Splinter model's place_time is the totem's own cycle, totem_model charges a Spell Totem's charges, not time; the
# TotemPlacementSpeed lines are speed mods only; MECHB69's totem question still open with MECH-B). Its use: one totem
# kept placed (as totem_deaths and totem_count_model take each side totem skill: KeywordPopups Totem, the shared
# limit), re-placed as it expires: once per its totem duration (useRates totemDuration, PoB's TotemDuration with the
# build's modifiers), each placement the player's PoB TotemPlacementTime (useRates totemPlacementTime: the base
# placement time with the build's placement and action speed; CalcOffence 1918-1925). A main-group totem is the damage
# itself, never charged here; a side totem with no duration or placement time read: not charged, as before.
UPKEEP_PRESS = os.environ.get("POE2_SEARCH_UPKEEP", "1") != "0"
UPKEEP_TYPES_CHARGED = frozenset({"AppliesCurse", "Mark", "Warcry"})
UPKEEP_MOST = 0.9  # the most of the fight upkeep may take (as models.WARCRY_PRESS_MOST)
DISTANCE_SETTLES = 4  # (SEARCH41) settle rounds per boss-distance candidate at most, where the choice is final
# (finalize_task, fresh_score, gate4's fair scoring: Search.distance_settles); mid-search one settle, as before: the
# manager's 26 Nov bar (~2-3% of a pass) was missed (POE2T7561, b16: +35 s on a ~80 s conditions round)


def totem_row(row: dict) -> bool:
    """A useRates row whose skill the player places as a totem (the bridge read a TotemPlacementTime for it)."""
    return number(row.get("totemPlacementTime")) > 0


def upkeep_use(row: dict) -> tuple[float, str, float]:
    """(uses a second, how, the player's seconds a use) of a kept-up side skill (UPKEEP_PRESS's note): a totem placed
    once per its totem duration, each placement its TotemPlacementTime; else press_use_rate with no chosen rate (on its
    cooldown, else once per its duration) and press_action's seconds."""
    if totem_row(row):
        lasts, place = number(row.get("totemDuration")), number(row.get("totemPlacementTime"))
        if lasts <= 0:
            return 0.0, "no totem duration read: not charged", 0.0
        return 1.0 / lasts, f"once per its {lasts:.2f} s totem duration (one totem kept placed)", place
    uses, how, _ = press_use_rate(row, None)
    cooldown, lasts = number(row.get("cooldown")), number(row.get("duration"))
    if cooldown > 0 and lasts > cooldown and not row.get("projectile"):
        # (SEARCH44) kept up for its effect: once per its duration keeps it up; casting it again on its shorter cooldown
        # only refreshes it (press_use_rate charges a press skill on its cooldown). A projectile's duration is its
        # projectiles' life, not a thing kept up (MECHB39): left on its cooldown
        uses = min(uses, uses * cooldown / lasts)
        how = f"once per its {lasts:.2f} s duration (its {cooldown:.2f} s cooldown is shorter), to keep it up"
    return uses, how, press_action(row)


def upkeep_charged_elsewhere() -> frozenset[str]:
    """The skills whose cast time a model of their own charges (UPKEEP_PRESS's note)."""
    from builder.search import assumptions as a, conditions as c, models as m

    commands = {command for rows in m.COMMANDS.values() for command, *_ in rows} | {d.name for d in m.DJINN_COMMANDS}
    return frozenset({m.FEROCIOUS_ROAR, m.TRAIL_OF_CALTROPS, m.VOID_ILLUSION, m.VARASHTA, m.RAISE_SHIELD, m.ANNIHILATION,
                      m.BARRAGE, m.INEVITABLE_AGONY, m.REPULSION, *m.PACTS, c.INCINERATE,
                      *m.PARRY_SKILLS, *m.MARK_CARRIERS, *GLORY_SKILLS, *a.PRESSED_SOURCES, *commands})
PRESS_ROUND = os.environ.get("POE2_SEARCH_PRESS", "1") != "0"
# (SEARCH25, A36-14) a granted pressed skill tried in Full DPS on the finished build (Search.include_granted)
GRANTED_PRESS = os.environ.get("POE2_SEARCH_GRANTED_PRESS", "1") != "0"
# (SEARCH40; MECHB60's 25971950, the manager's 24 Nov 17:01 item 6) Twister consumes the Whirlwinds of the build's own
# Whirling Slash / Whirlwind Lance presses (models.twister_consume_model; no press, no gain), and nothing gave a Twister build
# such a group: a player who takes Twister plays to feed it (CLAUDE.md's rate rule), so the finished build tries each legal
# feeder as a side group (a skill slot and a button within the request's cap), its press rate chosen by the press round
# (real-scored rates, PRESS_RATES), kept only if the build scores higher; the play guide's press row gives the DPS without /
# with it. FEEDER_SECONDS: LOW, a time budget. POE2_SEARCH_FEEDER=0 turns it off
FEEDER_ROUND = os.environ.get("POE2_SEARCH_FEEDER", "1") != "0"
FEEDER_SECONDS = 60.0
FEEDER_LABEL = "Feeder (search)"
FILL_FINALIZE_RATIO = 1.5  # (SEARCH34) the open-socket fill's time, at most this x finalize's own (LOW, a time budget)
# (SEARCH35; SEARCH34's ranked list, item 5: the search stalls in the Uhtred's Augury set on 2b75b seeds 1 / 4, and the
# end-of-run fill fixes the supports but the gear and tree were chosen under the worse set) the same fill inside the
# search: a "socket fill" step after the supports step runs Gems.fill_open on the main group once per main-group set
# the line holds with an open socket, up to FILL_SEARCH_SECONDS (LOW, a time budget, no DPS assumption), so the rest of
# the line grows under the better set. OFF until a gate set measures it: POE2_SEARCH_FILL_IN_SEARCH=1 turns it on
FILL_IN_SEARCH = os.environ.get("POE2_SEARCH_FILL_IN_SEARCH", "0") == "1"
FILL_SEARCH_SECONDS = 60.0
# (SEARCH35; SEARCH34's ranked list, item 3) Steps that rarely pay: over SEARCH34's 210 2b75b gate tasks, persistent
# (3,344 s, a gain in 9 of 89 runs, summed +0.02), condition sources (3,078 s, 10 of 85, +1.9) and meta gems (2,848 s,
# 0 of 74) took ~25% of the search's time. With POE2_SEARCH_IDLE_SKIP=1 such a step that gained nothing in its last
# IDLE_AFTER runs on the line is skipped, except every IDLE_RETRY-th time it comes up and after a kick touched it (LOW,
# a time-sharing rule, no DPS assumption). OFF until a gate set measures it
IDLE_SKIP = os.environ.get("POE2_SEARCH_IDLE_SKIP", "0") == "1"
# (SEARCH35; the manager's 21:12, option (a): the final regrow in finalize ran on one worker while the others waited and
# measured lower, 2b75b 10,335 vs 38,066) Tree.final_regrow's partial clears in each line's LAST focus task, on every
# worker at once, in place of the line's M2 tree rebuild (gained in 0 of 97 runs over SEARCH34's gates): the task's
# search stops LINE_REGROW_SHARE of its time early and the line's tree is regrown on its near-final gear under the floors
# (weigh_uptimes first and after each variant, as finalize's). LOW, a time share. OFF until a gate pair measures it:
# POE2_SEARCH_LINE_REGROW=1 turns it on (and M2 off)
LINE_REGROW = os.environ.get("POE2_SEARCH_LINE_REGROW", "0") == "1"
# (SEARCH35) A cut gear round goes on first. On 2b75b (POE2T5561) each line's first task (60 s) cut its full gear round at
# Helmet (GEAR_SHARE), the line's next task resumed its pass at "supports", and the gear came round again only in the
# line's second pass: Gloves / Boots / Amulet / both Rings / Belt stayed empty from 78 s to 596 s of a 600 s run while
# ~90 s of jewels and ~90 s of supports a line were chosen without them (the last task's gear step then gained ln 27.9).
# With POE2_SEARCH_GEAR_FIRST=1, a task that resumes a pass while gear slots are empty and a full gear round was cut
# (gear.resume) runs that round on first, from the slot it stopped at, under GEAR_SHARE as a full round. OFF until a
# gate pair measures it
GEAR_FIRST_MODE = os.environ.get("POE2_SEARCH_GEAR_FIRST", "0")
GEAR_FIRST = GEAR_FIRST_MODE in ("1", "empty", "last", "last_empty")
# (SEARCH37, the manager's ruling on the 2b75e loss) POE2_SEARCH_GEAR_FIRST=empty: the narrow form. The round that goes on
# first works only the slots still empty (Gear.gear_round's `only`), and the cut full round keeps its place for the
# line's next gear step, so the resumed pass reaches supports (on 2b75e seed 1 the full form's round-2 tasks each ran a
# whole cut round, gear 97-101 s a task, and never reached supports: POE2T7423)
GEAR_FIRST_EMPTY = GEAR_FIRST_MODE == "empty"
# (SEARCH37) POE2_SEARCH_GEAR_FIRST=last: the gear goes LAST, and only when it must. The resumed pass runs as with gear-first
# off, but its steps stop GEAR_SHARE of the task early; if the pass ends before then, the line's next pass starts with
# its gear step anyway (nothing changes), and if not, the cut round goes on in the time kept back. Why: with gear-first
# off, 2b75b's round-2 tasks never got back to the gear step (passes 1, done 0, 6 slots empty at t 390: POE2T7410) while
# 2b75e's finished the pass and its next gear round filled every slot (passes 2, done 1: POE2T7422 / 7434), and both
# the full and the narrow forms lost 2b75e by ~x0.5 (POE2T7423 / 7435 / 7436) by running the gear before that pass
GEAR_FIRST_LAST = GEAR_FIRST_MODE in ("last", "last_empty")
# (SEARCH42; SPEED4's research/speed-starved-rounds.md, ranked items 2-3, the manager's 05:34) A TASK TAIL: each task keeps
# TASK_TAIL_SHARE of its time back from the pass's steps (they stop at that limit); if the pass hasn't reached the tail's
# steps by then, they run in the time kept back, once per task, and the line's pass then goes on where it stopped as
# before. Why: prune and regrow gains in 89-100% of its runs but runs 1.0-1.8 times per line per gate (passes finished
# per task 0.33-0.91); press rates gain in ~80% of runs on 2b75d / e and sit right after 'conditions', the commonest
# stop point (206 of 564 tasks on 2b75e). POE2_SEARCH_TASK_TAIL=prune / press / press,prune picks the steps (run in
# the pass's own order); empty: OFF, the passes as before. TASK_TAIL_SHARE: LOW, a search-effort setting
TASK_TAIL_PHASES = {"prune": "prune and regrow", "press": "press rates"}
TASK_TAIL = [TASK_TAIL_PHASES[w.strip()] for w in os.environ.get("POE2_SEARCH_TASK_TAIL", "").split(",")
             if w.strip() in TASK_TAIL_PHASES]
TASK_TAIL_SHARE = 0.15
# (SEARCH42; SPEED4's ranked item 4) The sweep-only steps (ascendancy, condition sources, meta gems / spells, totems,
# marks, mirages, roar) run once per line per gate, in pass 0 on a half-built build, and are never revisited: with
# POE2_SEARCH_LATE_SWEEP=1 finalize_task runs each once more on the finished build, after its polish, on its own time
# past finalize's (min(LATE_SWEEP_SECONDS, finalize's seconds): LOW, a search-effort setting). OFF until a gate set
LATE_SWEEP = os.environ.get("POE2_SEARCH_LATE_SWEEP", "0") == "1"
LATE_SWEEP_SECONDS = 30.0
SWEEP_ONLY = ("ascendancy", "condition sources", "meta gems", "meta spells", "totems", "marks", "mirages", "roar")
# (SEARCH40; SEARCH38's next try, its 13:55 report: on 2b75b `last`'s kept-back time went to the cut round from Body
# Armour and all 6 empty slots stayed empty, POE2T7449) POE2_SEARCH_GEAR_FIRST=last_empty: `last`, its kept-back time on
# the EMPTY slots only (the narrow form's `only`); the cut round keeps its place for the line's next gear step. OFF
# until a gate set
GEAR_FIRST_LAST_EMPTY = GEAR_FIRST_MODE == "last_empty"
LINE_REGROW_SHARE = 0.3
LINE_REGROW_CLEARS = ("weak:30", "weak:15", "weak:8")  # (the variants 2b75b kept in POE2T5552; "all" was refused)
IDLE_STEPS = ("persistent", "condition sources", "meta gems")
IDLE_AFTER = 1
IDLE_RETRY = 3
FINALIZE_SWEEP_MIN = 20.0  # finalize's polish sweeps supports on Full DPS first when it has this long (finalize_task)
# How far past its deadline a search may run before its build stops it at the next evaluation (Build.stop_at): a share
# of the task's time, at least STOP_GRACE_MIN seconds, so a step that watches the deadline itself finishes normally
STOP_GRACE_SHARE = 0.01
STOP_GRACE_MIN = 2.0
RESULT_FIELDS = sorted(set(FACT_FIELDS) | set(SUSTAIN_FIELDS) | set(MODEL_FIELDS) | set(DARKNESS_FIELDS) | set(CLEAR_FIELDS)
                       | set(RECOVERY_INPUTS) | {
    "TotalDPS", "TotalDotDPS", "Spirit", "TotalEHP", "Speed", "CritChance", "HitChance",
})


class Search:
    def __init__(self, engine: Any, report: Any, payload: dict, skeleton: dict, seed: int, full: bool = False,
                 goal: str | None = None) -> None:
        self.ctx: Context = context_for(payload)
        self.skeleton = Skeleton(**skeleton)
        self.build = Build(engine, self.ctx, Objective(self.ctx, full, goal=goal), report, self.skeleton.label)
        # (SEARCH38) the point budget's extras, on this engine, for a fresh or a resumed task alike
        engine.call("setExtraPoints", points=self.ctx.request.extra_passive_points + self.skeleton.import_points,
                    weaponSetPoints=self.ctx.request.extra_weapon_set_points)  # (SEARCH39)
        self.rng = random.Random(seed)
        self.gems = Gems(self.build)
        self.gems.rng = self.rng
        self.tree = Tree(self.build, self.rng)
        self.tree.class_name, self.tree.ascendancy = self.skeleton.class_name, self.skeleton.ascendancy
        self.tree.checkpoint = self.checkpoint
        self.gems.checkpoint = self.checkpoint
        sk = self.skeleton
        # Skills only some weapon bases grant (a main skill "bases:<type>", a required one): those hands keep to them
        main_bases, off_bases = slot_bases(self.ctx, sk) or ((), ())
        weapon = SlotSpec(sk.weapon, sk.weapon_sub, bases=main_bases) if sk.weapon else None
        held = offhand_types(self.ctx, sk.weapon, sk.offhand)
        offhand = SlotSpec(held[0], None, held[1:], offhand_non_unique(sk.requires_passive, sk.offhand),
                           bases=off_bases) if held else None
        swap: tuple = (None, None)
        if sk.swap:
            held = offhand_types(self.ctx, sk.swap_weapon, sk.swap_offhand)
            swap = (SlotSpec(sk.swap_weapon, sk.swap_sub) if sk.swap_weapon else None,
                    SlotSpec(held[0], None, held[1:]) if held else None)
        self.gear = Gear(self.build, weapon, offhand, self.rng, swap)
        if extra_ring_passive(self.ctx, sk.ascendancy):
            self.gear.add_ring_slot()
        self.swap_locked = set(swap_skills(self.ctx, sk)) if sk.swap else set()  # skills only set 2 can use
        self.jewels = Jewels(self.build, self.tree, self.gear)
        self.gear.checkpoint = self.checkpoint  # (SEARCHD1 hook, F1: a gear round's kept slots survive a hard stop)
        self.jewels.checkpoint = self.checkpoint  # (SEARCHD1 hook, F1: a jewel round's filled sockets survive a hard stop)
        self.conditions = Conditions(self.build, self.skeleton.main_skill)
        self.play_notes: dict[str, dict] = {}  # notes from choose_stationary and weigh_life, weigh_uptimes, for the result
        self.uptime_rows: list[dict] = []  # weigh_uptimes' effects, for the play guide
        self.uptime_cache: dict[str, tuple[float, dict]] = {}  # var -> (DPS without it / with it, stats deltas) (follow_uptimes)
        self.uptime_floor: dict[frozenset, dict] = {}  # effects -> stats deltas with all of them off (floor_without)
        self.gems.conditions = self.conditions
        self.assumptions = Assumptions(self.build)
        self.build.reassume = self.assumptions.apply  # item what-ifs a model reads get a real trial (Build.item_deltas)
        # (SEARCHB5, on the manager's loan of the gem_deltas path) support what-ifs a model values get a real trial
        # (Build.gem_deltas, POE2_SEARCH_GEM_RESCORE, off by default)
        self.build.model_gem = model_valued_supports(self.ctx, self.conditions)
        self.build.gem_settle = self.settle
        self.build.judge_settle = self.resolve  # (JUDGE: the tree's keep / revert on the settled score)
        self.gems.resolve = self.resolve
        self.gems.settle = self.settle
        self.gems.assumptions = self.assumptions
        self.tree.resolve = self.resolve
        self.tree.relevant_skill = self.assumptions.relevant
        self.tree.enforce = self.gear.enforce_restrictions
        self.tree.fill_granted = self.gems.fill_granted
        self.gear.fill_granted = self.gems.granted_meta_round
        self.gear.refill_main = self.gems.refill_main_supports
        # (SEARCH36, for SEARCHB3's Shrine Sceptre trials, POE2_SEARCH_PURITY_TRIALS: an item group's supports filled
        # before the item is judged; Gear.fill_group is None, nothing filled, until gear.py reads it)
        self.gear.fill_group = self.gems.fill_granted_supports
        self.gear.fit_requirements = self.tree.fit_requirements
        self.gear.resolve = self.resolve
        self.gear.relevant_skill = self.assumptions.relevant
        self.usage = Usage(self.build, self.conditions, self.gear.flask_rate,  # (weapons: the Talisman rule, usage.py)
                           {1: sk.weapon, **({2: sk.swap_weapon} if sk.swap else {})})
        self.gems.usage = self.usage
        self.sources = Sources(self.build, self.conditions, self.gems, self.tree, self.gear, self.jewels, self.resolve)
        self.build.after_load.append(self.assumptions.reinstall)
        self.build.after_load.append(self.conditions.sync)
        self.build.snapshot_state = (self._scoring_state, self._put_scoring_state)
        self.problems: list[str] = []
        self.seed_notes: list[str] = []  # what setup_seed took out of the PoB code, and why
        self.kicks = 0
        self.kicks_kept = 0
        self.escalations = 0
        self.feasible: dict | None = None  # the best build seen that meets every requirement (keep_feasible)
        self.timing: dict[str, list[float]] = {}  # phase -> [seconds, evaluations, gain]
        self.deadline: float | None = None  # the running search's (Search.run)
        self.overrun: dict[str, float] = {}  # phase -> the most seconds a step of it ran past the deadline
        self.boundary: dict | None = None  # the build as the last step left it (a stop goes back to it)
        self.stop_at: float | None = None  # the hard stop the build gets once a step has finished (Build.stop_at)
        self.passes = 0
        self.passes_done = 0  # passes that ran every block before the deadline (passes counts those started)
        # The line's own pass (M1, PERSIST_LINES; carried in the state): passes over the line's life, the step the pass
        # goes on from (None: a new pass), and whether that pass sweeps
        self.line_passes = 0
        # The shortfall at the end of the line's last finished pass (Search._ascend's escalation compares the next one
        # with it). SEARCH16: it was local to one task, and a pass ended inside a task only about once (2b75b, POE2T2222:
        # 2 line passes in 600 s, every task stopped mid-pass), so no pass was ever compared with one before it:
        # escalations 0 in all 15 tasks, and the line kept trading its Physical max hit floor (27% short) for damage at
        # the first weight to the end
        self.line_short: float | None = None  # (None: no pass of the line has finished yet)
        self.resume_step: str | None = None
        self.resume_sweep = False
        self.press_rows: dict[str, dict] = {}  # (Search.press_round: each candidate's useRates row, its trial DPS)
        self.upkeep_rows: dict[str, dict] = {}  # (Search.upkeep_round: each kept-up side skill's row, uses, DPS without)
        self.upkeep_dropped: dict[str, dict] = {}  # (SEARCH44: kept-up skills switched off, their casts cost more)
        self.tree_rebuilt = False  # M2: the line's tree rebuild was tried (carried in the line's state)
        self.fill_seen: list[list] = []  # (SEARCH35, socket_fill_round: the main-group sets the line's fill has seen)
        self.skipped: dict[str, int] = {}  # (IDLE_SKIP: phase -> times this task skipped it, in the task's event)
        self.idle: dict[str, list[int]] = {}  # (SEARCH35, IDLE_SKIP: phase -> [runs in a row with no gain, times skipped])
        self.current_step: str | None = None
        self.kick_kinds: dict[str, int] = {}
        self.kicked: set[str] = set()  # blocks a kick disturbed, swept fully on the next pass

    # Setup and state ---------------------------------------------------------------------------

    def setup(self, quests: bool = True) -> None:
        build, sk = self.build, self.skeleton
        build.phase = "setup"
        build.call("newBuild")
        # The boss preset's resistances (the request's boss, else the bridge's default, bosses.DEFAULT's): a new build
        # had PoB's own configuration (its Pinnacle placeholder 50/50/50/0), only a seed code's resetConfig set them (SEARCH6)
        build.call("resetConfig", **boss_config(self.ctx.request))
        build.boss_stats = boss_stats(self.ctx.request)
        self.correct_passives()
        build.call("setClass", className=sk.class_name, ascendancy=sk.ascendancy)
        build.call("setLevel", level=self.ctx.level)
        kind, source = sk.granted_by.split(":", 1) if sk.granted_by else (None, None)
        if kind is None:
            build.set_group(gems=[{"name": sk.main_skill, "level": GEM_LEVEL, "quality": GEM_QUALITY}], main=True,
                            fullDPS=True)
        elif kind == "passive":
            self.tree.required_names.add(source)
        if sk.requires_passive:  # the passive the skeleton's off hand needs (Instruments of Power: Staff + Focus)
            self.tree.required_names.add(sk.requires_passive)
        granted_uniques = []
        required = [n for n in self.ctx.request.require.skills if n != sk.main_skill]
        # A required meta skill that gains Energy (Cast on Critical, an Invocation) gets the other
        # required spells socketed: it triggers them, fed by the main skill (models.energy_model)
        metas = [n for n in required if {"Meta", "GeneratesEnergy"} <= self.gem_types(n)]
        in_meta = meta_socketed(required, self.ctx.gems)
        socketed = [n for n in required if n in in_meta]
        for name in required:
            if name in socketed:
                continue
            if name in self.ctx.gems:
                names = [name] + (socketed if metas and name == metas[0] else [])
                build.set_group(gems=[{"name": n, "level": GEM_LEVEL, "quality": GEM_QUALITY} for n in names],
                                label="required")
            elif source := self.ctx.skill_source(name):
                # A skill only a passive or unique grants comes with its source
                if source[0] == "passive":
                    self.tree.required_names.add(source[1])
                else:
                    granted_uniques.append(source[1])
        for group in build.groups() if self.swap_locked else []:
            if group["gems"] and group["gems"][0]["name"] in self.swap_locked:
                build.set_group(index=group["index"], set1=False, set2=True)
        missing = self.gems.add_required_supports()
        self.problems += [f"required support {n} can't support {sk.main_skill}" for n in missing]
        self.problems += self.gear.setup(([source] if kind == "unique" else []) + granted_uniques)
        self.require_ring_passive()
        self.ensure_required_items()  # (SEARCHD3: a required Jewel into a socket; Gear.setup has no jewel slots)
        self.tree.choose_default_attribute(sk.main_skill)
        self.problems += [f"required passive {n} isn't reachable" for n in self.tree.allocate_required(sk.class_name)]
        if kind is not None:
            self.adopt_granted_skill(source)
        self.resolve()
        self.gems.choose_active_skill()
        if quests:
            self.conditions.choose_quests()

    def ensure_required_items(self, after_search: bool = False) -> bool:
        """(SEARCHD3: a required item is LOCKED) Every required unique worn: a gear one back in its slot if a round took it
        out, a Jewel into a passive-tree socket (place_required_jewel). Gear rounds already skip locked slots; the jewel
        rounds (jewels.py) have no lock, so this runs after setup and after every search (Search.run, beside
        Tree.ensure_required). True if one had to go back."""
        ctx, build = self.ctx, self.build
        owned = set(getattr(ctx.request, "owned_uniques", None) or [])
        worn = {o.name for o in build.equipped.values() if o is not None}
        names = list(ctx.compiled.request.require.uniques) + ([ctx.request.weapon_unique] if ctx.request.weapon_unique else [])
        back = False
        for name in dict.fromkeys(names):
            option = ctx.unique_option(name)
            if name in worn or option is None or name in ctx.compiled.filters.forbidden_uniques:
                continue
            option = owned_option(option) if name in owned else option
            if option.type == "Jewel":
                placed = self.place_required_jewel(name, option)
            else:
                slot = self.gear.slot_for(option)
                placed = slot is not None
                if placed:
                    self.gear.put(slot, option)
                    build.locked_slots.add(slot)
            back = back or (placed and after_search)
        if back:
            lost = "a required unique had gone during the search; it was put back"
            if lost not in self.problems:
                self.problems.append(lost)
            self.gear.settle()
        return back

    def place_required_jewel(self, name: str, option: ItemOption) -> bool:
        """A required unique Jewel in the allocated socket (one that takes uniques) where it scores best, else in the
        nearest free socket the tree allocates for it (REQUIRED_SOCKET_REACH); the slot locked and the socket kept like a
        required passive (Tree.required: its path is never pruned)."""
        build, jewels = self.build, self.jewels
        sockets = [s for s in jewels.sync() if not jewels.plain_only(s) and jewels.slot(s) not in build.locked_slots]
        if not sockets:
            nodes = build.call("frontier", maxDistance=REQUIRED_SOCKET_REACH, sockets="only")["nodes"]
            nodes = [n for n in nodes if not self.tree.forbidden.intersection(n["path"])]
            if not nodes:
                self.problems.append(f"required unique {name} is a Jewel, and no passive-tree socket is within "
                                     f"{REQUIRED_SOCKET_REACH} points that the request's banned passives leave open")
                return False
            target = min(nodes, key=lambda n: (n["distance"], n["id"]))
            build.allocate(add=[target["id"]], attribute=self.tree.attribute)
            sockets = [target["id"]]
        if len(sockets) > 1:
            scored = build.item_deltas([{"slot": jewels.slot(s), "raw": option.raw} for s in sockets])
            sockets = [max(zip((score for score, _ in scored), sockets), key=lambda x: x[0])[1]]
        slot = jewels.slot(sockets[0])
        self.gear.put(slot, option)
        build.locked_slots.add(slot)
        self.tree.required.add(sockets[0])
        return True

    def require_ring_passive(self) -> None:
        """(SEARCHD3) A required unique locked in Ring 3 needs the passive that adds the slot (PoB ignores the slot until it
        is allocated: Gear.add_ring_slot), so the tree allocates it as it does a required passive."""
        if "Ring 3" in self.build.locked_slots and (passive := extra_ring_passive(self.ctx, self.skeleton.ascendancy)):
            self.tree.required_names.add(passive)

    def setup_seed(self, quests: bool = True) -> None:
        """Start from the request's PoB code (PLAN.md §5.1, gate 4) and take out what the request
        doesn't allow: banned gems, uniques, item classes and passives; skill groups over the button
        cap; weapons the skeleton doesn't hold; the most expensive priced items while over budget;
        passives over the request level's points. Then add what it requires. Uniques are priced from
        the price table; the code's other items count as the user's own (no price)."""
        build, sk, ctx = self.build, self.skeleton, self.ctx
        build.phase = "seed"
        filters = ctx.compiled.filters
        build.call("loadXml", xml=codes.decode(ctx.request.seed_code))
        # The code's configuration isn't trusted: a new build's, then the resolver turns on what
        # the build sustains (the code's quest choices stay, choose_quests revisits them)
        build.call("resetConfig", **boss_config(ctx.request))
        build.boss_stats = boss_stats(ctx.request)
        self.correct_passives()
        build.call("setLevel", level=ctx.level)
        notes = self.seed_notes

        # Skill groups: banned gems out, the main skill's group main, buttons within the cap
        groups = build.groups()
        for group in reversed(groups):
            if group["fromItem"]:
                continue
            gems = [g for g in group["gems"] if g["name"] not in filters.forbidden_gems]
            if len(gems) == len(group["gems"]):
                continue
            notes.append(f"took {', '.join(g['name'] for g in group['gems'] if g not in gems)} out (banned)")
            if any(not g["support"] for g in gems):
                build.set_group(index=group["index"], gems=[{"name": g["name"], "level": g["level"],
                                                            "quality": g["quality"]} for g in gems])
            else:
                build.remove_group(group["index"])
        main = None
        # (SEARCH39; COV56's calibration, example build 7: Comet in a Cast on Critical group 3 and hand-cast in group 9,
        # the code's own main) the code's main group comes first when it holds the main skill, else the first that does
        for group in sorted(build.groups(), key=lambda g: not g.get("main")):
            for position, active in enumerate(group["skills"], start=1):
                if main is None and active["name"] == sk.main_skill:
                    main = group["index"]
                    build.set_group(index=main, main=True, fullDPS=True, mainActiveSkill=position)
        cap = ctx.request.max_buttons
        buttons = [g for g in build.groups() if g["enabled"] and not g["fromItem"] and g["gems"]
                   and not g["main"] and g["gems"][0]["name"] in ctx.gems and is_button(ctx.gems[g["gems"][0]["name"]])]
        for group in buttons[max(0, (cap or len(buttons) + 1) - 1):] if cap else []:
            build.set_group(index=group["index"], enabled=False)
            notes.append(f"switched {group['gems'][0]['name']} off (button cap {cap})")
        if not sk.swap:
            for group in build.groups():
                if not group["set1"]:
                    build.set_group(index=group["index"], set1=True, set2=True)

        # Items: what the skeleton holds and the request allows, priced where a price exists
        raws = build.call("equipped")["slots"]
        facts = build.call("facts", fields=[])
        kept: dict[str, ItemOption] = {}
        holds = {slot: spec.types for slot, spec in self.gear.slots.items() if slot.startswith("Weapon")}
        for item in facts["items"]:
            slot, raw = item["slot"], raws.get(item["slot"])
            unique = item["rarity"] in ("UNIQUE", "RELIC")
            banned = (unique and item["name"] in filters.forbidden_uniques) or item["type"] in filters.forbidden_item_classes
            if raw is None or banned or (slot.startswith("Weapon") and item["type"] not in holds.get(slot, ())):
                build.equip(slot, None)
                if raw is not None:
                    notes.append(f"took {item['name']} out of {slot} ({'banned' if banned else 'not this skeleton'})")
                continue
            price, confidence = 0.0, "yours (from your PoB code)"
            if unique:
                entry = ctx.unique_prices.get(item["name"])
                price, confidence = (entry[0], "from the price table") if entry else (None, "unpriced (from your PoB code)")
            base = ctx.bases_by_name.get(item["base"]) or {}
            kept[slot] = ItemOption(item["name"], item["base"], item["type"], base.get("subType"), raw, unique,
                                    None if ctx.ssf else price, "ssf" if ctx.ssf else confidence,
                                    (base.get("req") or {}).get("level") or 0, rare=item["rarity"] in ("RARE", "MAGIC"))
        # (SEARCH36; the manager's 08:05 22 Nov) the user's own items in a seed run too, as Gear.setup does for a new
        # build (SEARCHB3's owned-gear feature): a unique the code holds that the user owns (owned_uniques) at price 0,
        # then each pasted item (owned_items) in its slot, locked, before the budget trim below
        owned = set(getattr(ctx.request, "owned_uniques", None) or [])
        kept = {slot: owned_option(o) if o.unique and o.name in owned else o for slot, o in kept.items()}
        build.equipped = kept
        self.problems += self.gear.setup_owned()
        # (dearest first; equal prices by slot name, not PoB's per-process slot order: PERF12)
        for slot, option in sorted(kept.items(), key=lambda x: (-(x[1].price or 0.0), x[0])):
            if build.spent() <= ctx.budget + 1e-9:
                break
            if option.price:
                self.gear.put(slot, None)
                notes.append(f"took {option.name} out of {slot} (over the budget)")
        for name in dict.fromkeys(list(ctx.compiled.request.require.uniques)
                                  + ([ctx.request.weapon_unique] if ctx.request.weapon_unique else [])):
            held = next((s for s, o in build.equipped.items() if o.name == name), None)
            option = ctx.unique_option(name)
            if option is not None and name in owned:
                option = owned_option(option)  # (SEARCH36: the user owns it: price 0, as Gear.setup)
            if held is None and option is not None and option.type == "Jewel":
                continue  # (SEARCHD3: a passive-tree socket, ensure_required_items)
            slot = held or (self.gear.slot_for(option) if option else None)
            if slot is None:
                self.problems.append(required_items.no_slot(self.gear, name, option))  # (SEARCHD3)
                continue
            if held is None:
                self.gear.put(slot, option)
            build.locked_slots.add(slot)
        for slot in self.gear.slots:
            if (slot.startswith("Weapon") or slot == "Ring 3") and slot not in build.equipped:
                bases = [o for o in self.gear.options(slot) if not o.unique]
                if bases:
                    self.gear.put(slot, max(bases, key=lambda o: o.level))

        # Required skills the code lacks
        present = {g["name"] for group in build.groups() for g in group["gems"]}
        for name in ctx.request.require.skills:
            if name in ctx.gems and name not in present:
                build.set_group(gems=[{"name": name, "level": GEM_LEVEL, "quality": GEM_QUALITY}], label="required")
        for group in build.groups() if self.swap_locked else []:
            if group["gems"] and group["gems"][0]["name"] in self.swap_locked:
                build.set_group(index=group["index"], set1=False, set2=True)
        missing = self.gems.add_required_supports()
        self.problems += [f"required support {n} can't support {sk.main_skill}" for n in missing]

        # Passives: banned ones out, required ones in, then within the request level's points
        allocated = {n["id"] for n in build.call("allocatedNodes")["nodes"]}
        if banned := sorted(allocated & self.tree.forbidden):
            build.allocate(remove=banned)
            notes.append(f"removed {len(banned)} banned passive(s)")
        self.tree.choose_default_attribute(sk.main_skill)
        self.require_ring_passive()
        self.ensure_required_items()  # (SEARCHD3)
        self.problems += [f"required passive {n} isn't reachable" for n in self.tree.allocate_required(sk.class_name)]
        if trimmed := self.tree.trim_to_budget():
            notes.append(f"removed {trimmed} passive(s) over a level {ctx.level} character's points")
        self.resolve()
        self.gems.choose_active_skill()
        if quests:
            self.conditions.choose_quests()
        self.gear.sync_flasks()

    def gem_types(self, name: str) -> set[str]:
        return set((self.ctx.gems.get(name) or {}).get("skillTypes") or [])

    def correct_passives(self) -> None:
        """Ledger corrections for passive lines PoB ignores (once per engine; the tree is shared)."""
        if self.ctx.passive_corrections:
            self.build.call("applyPassiveCorrections", nodes=self.ctx.passive_corrections)

    def adopt_granted_skill(self, source: str) -> None:
        """Make the group of a skill a passive or unique grants the main group."""
        build, skill = self.build, self.skeleton.main_skill
        if not any(s["name"] == skill for g in build.groups() for s in g["skills"]):
            self.tree.ascendancy_round()  # an ascendancy passive arrives with the ascendancy allocation
        for group in build.groups():
            for position, active in enumerate(group["skills"], start=1):
                if active["name"] == skill:
                    build.set_group(index=group["index"], main=True, fullDPS=True, mainActiveSkill=position)
                    return
        self.problems.append(f"{skill} didn't appear after taking {source}")

    def state(self) -> dict:
        snap = self.build.snapshot()
        return {
            "xml": snap["xml"], "equipped": snap["equipped"], "locked": sorted(self.build.locked_slots),
            "required": sorted(self.tree.required), "active": self.conditions.active,
            "shortlist": self.gems.shortlist, "meta_shortlist": self.gems.meta_shortlist, "gear_shortlist": self.gear.shortlist, "attribute": self.tree.attribute,
            "gear_resume": self.gear.resume,
            "multipliers": dict(self.build.objective.multipliers), "assumed_mods": self.assumptions.mods,
            # (the raises so far: a line's next task must not count from 0, or each task's second raise clears the
            # tree again, as every task of gate 4's 2b75b runs did: lines at 15K with no supports, POE2T194-197)
            "escalations": self.escalations,
            "extra_groups": [[k, v] for k, v in sorted(self.assumptions.extra_groups.items())],
            "required_names": sorted(self.tree.required_names), "sources": sorted(self.tree.sources),
            "extra_stats": dict(self.build.extra_stats), "recovery_sources": dict(self.build.recovery_sources),
            "character_runes": [[k, list(v)] for k, v in sorted(self.build.character_runes.items())],
            "curse_lapse": sorted(getattr(self.build.objective, "curse_lapse", None) or ()),  # (choose_curse_lapse)
            "remnant_skip": bool(getattr(self.build.objective, "remnant_skip", False)),  # (choose_remnant_walk)
            "infused": self.assumptions.infused, "infused_skipped": self.assumptions.infused_skipped,
            "problems": self.problems,
            "unfinished": unfinished.rows(self.build),  # (SEARCHD1 hook: what a deadline cut, carried with the line)
            # (JEWEL4, SEARCHD1's gap) the jewels left short of their slots, still equipped: a later step or finalize
            # (a fresh Search) keeps their "Not finished" notes (Jewels.unfinished_notes -> finalize's assumptions, which
            # run.unfinished_rows joins to SEARCH-D's record by (step, slot): one row per jewel, never a second copy)
            "jewels_unfinished": {raw: dict(info) for raw, info in self.jewels.unfinished.items()
                                  if any(o is not None and o.raw == raw for o in self.build.equipped.values())},
            "press_choices": press_choices(self.build), "press_rows": copy.deepcopy(self.press_rows),
            "upkeep_rows": copy.deepcopy(self.upkeep_rows),
            "upkeep_dropped": copy.deepcopy(getattr(self, "upkeep_dropped", None) or {}),
            "line": {"passes": self.line_passes, "step": self.resume_step, "sweep": self.resume_sweep,
                     "short": self.line_short,
                     "kicked": sorted(self.kicked), "rebuilt": self.gems.rebuilt, "curses_tried": self.gems.curses_tried,
                     "tree_rebuilt": self.tree_rebuilt, "fill_seen": copy.deepcopy(self.fill_seen),
                     "idle": copy.deepcopy(self.idle)} if PERSIST_LINES else None,
        }

    # The objective's per-build factors (not its requirement weights: an escalation raises those for the rest of the
    # search, and a restore must not undo it)
    SCORING_FIELDS = ("extra", "added_dps", "added_parts", "entry_scales", "press_scales", "whole_scales", "cull_scale", "press_choices",
                      "press_busy", "glory_busy", "upkeep_busy", "upkeep_rates", "life_factor", "uptime_factor",
                      "floor_deltas")
    ASSUMPTION_FIELDS = ("mods", "active", "extra_groups", "stat_sets", "infused", "infused_skipped", "cooldown_fallback")

    def _scoring_state(self) -> dict:
        """What the score rests on outside PoB's XML, for Build.snapshot (Build.snapshot_state)."""
        objective, assumptions, conditions = self.build.objective, self.assumptions, self.conditions
        return copy.deepcopy({
            "objective": {k: getattr(objective, k) for k in self.SCORING_FIELDS},
            "assumptions": {k: getattr(assumptions, k) for k in self.ASSUMPTION_FIELDS},
            "uptimes": conditions.uptimes, "found_uptimes": getattr(conditions, "found_uptimes", None),
            "uptime_cache": self.uptime_cache, "uptime_floor": self.uptime_floor, "uptime_rows": self.uptime_rows,
            "play_notes": self.play_notes, "press_rows": self.press_rows, "upkeep_rows": self.upkeep_rows,
            "upkeep_dropped": getattr(self, "upkeep_dropped", None) or {}})

    def _put_scoring_state(self, saved: dict) -> None:
        saved = copy.deepcopy(saved)
        for k, v in saved["objective"].items():
            setattr(self.build.objective, k, v)
        for k, v in saved["assumptions"].items():
            setattr(self.assumptions, k, v)
        self.conditions.uptimes = saved["uptimes"]
        if saved["found_uptimes"] is not None:
            self.conditions.found_uptimes = saved["found_uptimes"]
        self.uptime_cache, self.uptime_floor = saved["uptime_cache"], saved["uptime_floor"]
        self.uptime_rows, self.play_notes = saved["uptime_rows"], saved["play_notes"]
        self.press_rows = saved.get("press_rows", {})
        self.upkeep_rows = saved.get("upkeep_rows", {})
        self.upkeep_dropped = saved.get("upkeep_dropped", {})

    def load(self, state: dict) -> None:
        self.build.call("loadXml", xml=state["xml"])
        self.correct_passives()
        self.build.equipped = dict(state["equipped"])
        self.build.locked_slots = set(state["locked"])
        unfinished.load(self.build, state.get("unfinished"))  # (SEARCHD1 hook: the line's rows go on with it)
        self.build.objective.multipliers = dict(state.get("multipliers") or {})
        self.escalations = state.get("escalations", 0)
        self.assumptions.mods = list(state.get("assumed_mods") or [])
        self.assumptions.extra_groups = {k: v for k, v in state.get("extra_groups") or []}
        self.build.objective.extra = bool(self.assumptions.extra_groups)
        self.build.extra_stats = dict(state.get("extra_stats") or {})
        self.build.recovery_sources = dict(state.get("recovery_sources") or {})
        self.build.character_runes = {k: (v[0], v[1]) for k, v in state.get("character_runes") or []}  # (XML: the slots)
        if state.get("curse_lapse"):  # (models.CURSE_LAPSE = "curse_lapse": choose_curse_lapse's choice)
            self.build.objective.curse_lapse = set(state["curse_lapse"])
        self.build.objective.remnant_skip = bool(state.get("remnant_skip"))  # (choose_remnant_walk's choice)
        self.assumptions.infused = state.get("infused")  # (the Infused set the XML calculates, at its share)
        self.assumptions.infused_skipped = dict(state.get("infused_skipped") or {})
        self.assumptions.reinstall()
        self.build.refresh()
        self.tree.required = set(state["required"])
        self.tree.required_names = set(state.get("required_names") or self.tree.required_names)
        self.tree.sources = set(state.get("sources") or [])
        self.tree.attribute = state["attribute"]
        self.conditions.active = dict(state["active"])
        self.gems.shortlist = state["shortlist"]
        self.gems.meta_shortlist = state.get("meta_shortlist")
        self.gear.shortlist = dict(state.get("gear_shortlist") or {})
        self.gear.resume = state.get("gear_resume")
        self.problems = list(state["problems"])
        self.jewels.unfinished.update({raw: dict(info) for raw, info in (state.get("jewels_unfinished") or {}).items()})  # (JEWEL4)
        setattr(self.build.objective, PRESS_CHOICES, dict(state.get("press_choices") or {}))
        self.press_rows = copy.deepcopy(state.get("press_rows") or {})
        self.upkeep_rows = copy.deepcopy(state.get("upkeep_rows") or {})
        self.upkeep_dropped = copy.deepcopy(state.get("upkeep_dropped") or {})
        if self.press_sync():
            self.build.score = self.build.objective.score(self.build.stats)
        line = state.get("line") if PERSIST_LINES else None
        if line:
            self.line_passes, self.resume_step, self.resume_sweep = line["passes"], line["step"], line["sweep"]
            self.line_short = line.get("short")
            self.kicked.update(line.get("kicked") or [])
            self.gems.rebuilt = bool(line.get("rebuilt"))  # (the support rebuild: once per line, SEARCH_PLAN.md §6)
            self.gems.curses_tried = bool(line.get("curses_tried"))  # (the seeded curse trial: once per line, SEARCH29)
            self.tree_rebuilt = bool(line.get("tree_rebuilt"))
            self.fill_seen = [list(k) for k in line.get("fill_seen") or []]
            self.idle = {k: list(v) for k, v in (line.get("idle") or {}).items()}
        self.gear.sync_flasks()

    SETTLE_ROUNDS = 10  # (settle_still: the boss's Freeze on you settles in 3-4, MECHC40's probe POE2T3739)

    def settle_still(self) -> int:
        """Resolve until the conditions and assumptions stop moving (at most SETTLE_ROUNDS), as the search's own end
        (run's last resolve) would: a seed's start snapshot is taken here, not mid-settle. Before SEARCH29 setup_seed's
        last steps (the active skill, quests, flasks) moved the build after its last resolve, so the start's score was read
        with the assumptions still moving (MECHC40: 2b75c's start -9.3493 against -9.3557 for the same build settled, the
        boss's Freeze INC taking 3-4 rounds). Returns the rounds that changed something."""
        rounds = 0
        while rounds < self.SETTLE_ROUNDS and self.resolve():
            rounds += 1
        return rounds

    def settle(self) -> bool:
        """Conditions and assumptions follow the build, and the part-time effects' weights follow them (follow_uptimes)."""
        pressed = self.press_sync()  # (before the models read the choices: a skill that left the build loses its own)
        changed = self.conditions.resolve()
        changed = self.assumptions.apply() or changed
        self.follow_uptimes()
        if pressed:
            self.build.score = self.build.objective.score(self.build.stats)
        return changed

    # Press rates (MECH55 / MECH56; THE USER, 12 Nov: "Search picks the rate") -----------------------------------------
    # A side skill with no cooldown and no duration (and Marks, "you use" events, side consumers, Rage spenders that read
    # the same choice) is pressed 0 times until the search chooses how often the player presses it: the search plays the
    # build to feed it, as CLAUDE.md's flat-rate rule asks, and pays for the time each press takes from the main skill.

    def press_candidates(self) -> dict[str, dict]:
        """{skill name: its bridge useRates row} for each button beside the main skill (Gems.buttons: within the cap
        already) whose use no cooldown or duration sets (models.press_use_rate reads 0 for it without a choice)."""
        build = self.build
        result = build.call("groups")
        main = result["mainSocketGroup"]
        buttons = set(self.gems.buttons())
        where = {g["index"]: [n["name"] for n in g.get("gems") or []
                              if n.get("enabled", True) and not n.get("support") and n["name"] in buttons]
                 for g in result["groups"] if g["index"] != main and g.get("enabled") is not False}
        where = {i: names for i, names in where.items() if names}
        if not where:
            return {}
        main_names = {n["name"] for g in result["groups"] if g["index"] == main for n in g.get("gems") or []
                      if n.get("enabled", True) and not n.get("support")}
        out: dict[str, dict] = {}
        for row in build.call("useRates", indices=sorted(where))["rates"]:
            if (number(row.get("cooldown")) > 0 or number(row.get("duration")) > 0 or number(row.get("speed")) <= 0
                    or row.get("index") not in where):
                continue
            label = str(row.get("name") or "")
            name = next((n for n in where[row["index"]]
                         if label == n or label.startswith(f"{n} ") or label.startswith(f"{n}:")), None)
            if name is not None and name not in main_names and name not in out:
                out[name] = dict(row)
        return out

    def press_sync(self) -> bool:
        """Keep the chosen press rates to skills still pressed in the build, and set the main skill's time they take that
        press_scales doesn't charge (Objective.press_busy: every choice outside Full DPS; in Full DPS, a chosen skill that
        isn't a pressed Full DPS group, e.g. one pressed for a side effect). Each press takes 1 / its speed, as press_model
        counts it (Cadence's pace and the build's feed caps aren't applied here: the time is overcounted a little for a
        skill they hold). True if either changed."""
        objective = self.build.objective
        choices = press_choices(self.build)
        if not choices and not getattr(objective, "press_busy", 0.0):
            return False
        groups = self.build.groups() if choices else []
        present = {n["name"]: g for g in groups if not g["main"] and g.get("enabled") is not False
                   for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")}
        kept = {name: rate for name, rate in choices.items() if name in present and name in self.press_rows}
        busy = 0.0
        for name, rate in kept.items():
            row = self.press_rows[name]["row"]
            if objective.full and pressed_group(present[name], self.build.ctx.gems,
                                                getattr(self.build.ctx, "all_gems", None)):  # (SEARCH25: A36-14)
                continue  # (press_model charges it: Assumptions.rate_presses)
            uses, _, _ = press_use_rate(row, rate)
            busy += uses / number(row.get("speed"))
        busy = round(busy, 9)
        changed = kept != choices or busy != getattr(objective, "press_busy", 0.0)
        setattr(objective, PRESS_CHOICES, kept)
        objective.press_busy = busy
        return self.upkeep_sync() or changed

    def upkeep_candidates(self) -> dict[str, dict]:
        """{skill: its useRates row} for each side button whose own data sets how often it is used (a cooldown, or a
        duration it is kept up for) and whose cast time no other model charges (UPKEEP_PRESS's note)."""
        build = self.build
        result = build.call("groups")
        main = result["mainSocketGroup"]
        buttons, choices = set(self.gems.buttons()), press_choices(build)
        # (a press a model counts and charges itself: models.ITEM_PRESSES, e.g. the Commands djinn_commands_model presses)
        own = set(getattr(build, gems_module.ITEM_PRESSES, None) or {})
        known = getattr(build.ctx, "any_gems", None) or {}
        granted = getattr(build.ctx, "all_gems", None)
        where: dict[int, list[str]] = {}
        main_names: set[str] = set()
        for group in result["groups"]:
            names = [n["name"] for n in group.get("gems") or [] if n.get("enabled", True) and not n.get("support")]
            if group["index"] == main:
                main_names |= set(names)
                continue
            if group.get("enabled") is False or (group.get("fullDPS") and pressed_group(group, build.ctx.gems, granted)):
                continue  # (off, or a pressed Full DPS group: press_model charges it)
            keep = [n for n in names if n in buttons and n not in choices and n not in own and n not in upkeep_charged_elsewhere()
                    and not set((known.get(n) or {}).get("skillTypes") or ()) & UPKEEP_TYPES_CHARGED]
            if keep:
                where[group["index"]] = keep
        if not where:
            return {}
        out: dict[str, dict] = {}
        for row in build.call("useRates", indices=sorted(where))["rates"]:
            if row.get("index") not in where or number(row.get("speed")) <= 0:
                continue
            if totem_row(row):
                if number(row.get("totemDuration")) <= 0:
                    continue  # (a totem with no duration read: no data sets how often it is placed)
            elif number(row.get("cooldown")) <= 0 and number(row.get("duration")) <= 0:
                continue  # (no data sets its use: the press round's choice, press_candidates)
            label = str(row.get("name") or "")
            name = next((n for n in where[row["index"]]
                         if label == n or label.startswith(f"{n} ") or label.startswith(f"{n}:")), None)
            if name is not None and name not in main_names and name not in out:
                out[name] = dict(row)
        return out

    def upkeep_sync(self) -> bool:
        """Charge the kept-up side skills upkeep_round found used (objective.upkeep_busy / upkeep_rates), dropping any no
        longer in a side group. True if the charge changed."""
        objective = self.build.objective
        self.upkeep_rows = getattr(self, "upkeep_rows", None) or {}  # (a bare Search, e.g. a test's stand-in)
        if not self.upkeep_rows and not getattr(objective, "upkeep_busy", 0.0):
            return False
        present = {n["name"] for g in self.build.groups() if not g["main"] and g.get("enabled") is not False
                   for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")}
        self.upkeep_rows = {k: v for k, v in self.upkeep_rows.items() if k in present}
        rates = {k: round(v["uses"], 9) for k, v in self.upkeep_rows.items()}
        busy = round(min(sum(v["uses"] * v["seconds"] for v in self.upkeep_rows.values()), UPKEEP_MOST), 9)
        changed = busy != getattr(objective, "upkeep_busy", 0.0) or rates != (getattr(objective, "upkeep_rates", None) or {})
        objective.upkeep_busy, objective.upkeep_rates = busy, rates
        return changed

    def upkeep_round(self) -> bool:
        """(UPKEEP_PRESS) Which kept-up side skills the build's score uses (each group switched off once and the score
        compared, as fit_buttons probes item skills), and their casts' time charged. True if the charge changed."""
        objective = self.build.objective
        before = (getattr(objective, "upkeep_busy", 0.0), dict(getattr(objective, "upkeep_rates", None) or {}))
        self.upkeep_rows, objective.upkeep_busy, objective.upkeep_rates = {}, 0.0, {}
        if not UPKEEP_PRESS:
            return before[0] != 0.0
        found = self.upkeep_candidates()
        build = self.build
        if found:
            base = self._rescore()
            for name, row in sorted(found.items()):
                build.set_group(index=row["index"], enabled=False)
                score, dps = self._rescore(), build.objective.dps(build.stats)
                build.set_group(index=row["index"], enabled=True)
                if score < base - EPSILON:
                    uses, how, seconds = upkeep_use(row)
                    if uses > 0 and seconds > 0:
                        self.upkeep_rows[name] = {"row": row, "uses": uses, "seconds": seconds, "how": how, "without": dps}
            self.upkeep_sync()
            self.upkeep_drop_losers()
            # (the guide's "without it": the skill off with the OTHER kept-up skills' casts still charged)
            if len(self.upkeep_rows) > 1:
                for name, info in list(self.upkeep_rows.items()):
                    self.upkeep_rows.pop(name)
                    self.upkeep_sync()
                    build.set_group(index=info["row"]["index"], enabled=False)
                    self._rescore()
                    info["without"] = build.objective.dps(build.stats)
                    build.set_group(index=info["row"]["index"], enabled=True)
                    self.upkeep_rows[name] = info
            self.upkeep_sync()
        self._rescore()
        for info in self.upkeep_rows.values():
            info["with"] = build.objective.dps(build.stats)
        return (objective.upkeep_busy, objective.upkeep_rates) != before

    def upkeep_drop_losers(self) -> list[str]:
        """(SEARCH44, the manager 27 Nov 13:43, COV62's POE2T6977-6980: an UNDERCOUNT) A kept-up skill is kept only when
        the build's score WITH its casts charged beats the score without the skill at all. upkeep_round's first test
        (its group off vs on, the casts not yet charged) only says the skill's effect is worth something; charged at its
        cooldown / duration rate it may cost more of the fight than it gives (2b75c: Disengage on its cooldown took 83%
        of the fight, 83,070 DPS vs 452,342 without it; b07: Spark kept up x0.803 of Spark off). Its use rate is the one
        its effect needs (upkeep_use: on its cooldown, once per its duration, a totem once per its totem duration): a
        lower rate would leave its effect counted at full uptime, an overcount, so the choice is that rate or 0. Each
        kept skill in turn: its group off (its casts uncharged) vs on (charged with the others); off wins unless charged
        beats it by over EPSILON, and then the group stays off (the player doesn't cast it: its effects and its button
        go), named in upkeep_dropped and the play guide. A skill the request requires is never switched off (it stays
        charged). Repeated until a pass drops nothing (one drop can make another skill worth its casts). Returns the
        skills dropped."""
        build = self.build
        request = getattr(getattr(build, "ctx", None), "request", None)
        required = set(getattr(getattr(request, "require", None), "skills", None) or ())
        self.upkeep_dropped = getattr(self, "upkeep_dropped", None) or {}
        dropped: list[str] = []
        changed = True
        while changed and self.upkeep_rows:
            changed = False
            for name in sorted(self.upkeep_rows):
                if name in required:
                    continue
                info = self.upkeep_rows[name]
                charged = self._rescore()
                charged_dps = build.objective.dps(build.stats)
                self.upkeep_rows.pop(name)
                self.upkeep_sync()
                build.set_group(index=info["row"]["index"], enabled=False)
                off = self._rescore()
                off_dps = build.objective.dps(build.stats)
                if charged > off + EPSILON:
                    build.set_group(index=info["row"]["index"], enabled=True)
                    self.upkeep_rows[name] = info
                    self.upkeep_sync()
                    continue
                self.upkeep_dropped[name] = {"how": info["how"], "uses": info["uses"], "seconds": info["seconds"],
                                             "with": charged_dps, "without": off_dps}
                dropped.append(name)
                changed = True
                break  # (the others are tested again against the build without it)
        self._rescore()
        return dropped

    def upkeep_guide_rows(self) -> list[dict]:
        """The play guide's "without it / with it" row for each kept-up side skill charged (CLAUDE.md's flat-rate rule)."""
        rows = []
        for name, info in sorted(self.upkeep_rows.items()):
            if "with" not in info:
                continue
            without, with_it = number(info.get("without")), number(info.get("with"))
            share = info["uses"] * info["seconds"]
            rows.append({
                "var": f"upkeep:{name}", "label": f"{name}: cast {info['how']}*", "kind": play_guide.PLAYER,
                "counted": True,
                "play": (f"cast {name} {info['how']} ({info['uses']:.3g} a second, {info['seconds']:.2f} s each: "
                         f"{share:.1%} of the fight not spent on the main skill): {with_it:,.0f} DPS with it, "
                         f"{without:,.0f} without it"),
                "dps_with": with_it, "dps_without": without,
                "change": (with_it - without) / without if without > 0 else 0.0, "confidence": "modelled"})
        for name, info in sorted((getattr(self, "upkeep_dropped", None) or {}).items()):  # (SEARCH44: switched off)
            if name in self.upkeep_rows:
                continue
            without, with_it = number(info.get("without")), number(info.get("with"))
            share = info["uses"] * info["seconds"]
            rows.append({
                "var": f"upkeep:{name}", "label": f"{name}: not cast*", "kind": play_guide.PLAYER, "counted": True,
                "play": (f"don't cast {name}: kept up {info['how']} its casts would take {share:.1%} of the fight from the "
                         f"main skill, for less than they give: {without:,.0f} DPS without it, {with_it:,.0f} with it "
                         f"(its group is switched off)"),
                "dps_with": without, "dps_without": with_it,
                "change": (without - with_it) / with_it if with_it > 0 else 0.0, "confidence": "modelled"})
        return rows

    def _press(self, name: str, rate: float) -> tuple[float, float]:
        """Set one skill's chosen press rate, settle, and return (score, DPS)."""
        build = self.build
        choices = press_choices(build)
        if rate > 0:
            choices[name] = rate
        else:
            choices.pop(name, None)
        setattr(build.objective, PRESS_CHOICES, choices)
        self.settle()
        build.score = build.objective.score(build.stats)
        return build.score, build.objective.dps(build.stats)

    def press_round(self, deadline: float) -> bool:
        """Choose how often the player presses each side skill with no cooldown or duration (PRESS_RATES), one skill at a
        time: each rate scored for real (settle: the models, conditions and Full DPS read the choice; the main skill
        loses the presses' time), the best kept, ties to the lower rate. The DPS with and without it go to the play guide
        (press_guide_rows). True if the score rose."""
        self.fit_buttons()
        if not PRESS_ROUND:
            return False
        build = self.build
        before = build.score
        found = self.press_candidates()
        self.press_rows = {name: {**self.press_rows.get(name, {}), "row": row} for name, row in found.items()}
        if self.press_sync():
            self.settle()
            build.score = build.objective.score(build.stats)
        for name in sorted(found):
            if clock.now() > deadline:
                break
            start = press_choices(build).get(name, 0.0)
            # (scored on Full DPS, a pressed group out of it joins it for the trials that press it: it was left out while
            # it was pressed 0 times, and include_in_full_dps only adds a group that already pays)
            group = next((g for g in build.groups() if g["index"] == found[name].get("index")), None)
            join = (group is not None and build.objective.full and not group["fullDPS"]
                    and pressed_group({**group, "fullDPS": True}, build.ctx.gems))
            trials = {PRESS_RATES[0]: self._press(name, PRESS_RATES[0])}
            if join:
                build.set_group(index=group["index"], fullDPS=True)
            for rate in PRESS_RATES[1:]:
                trials[rate] = self._press(name, rate)
            best = PRESS_RATES[0]
            for rate in PRESS_RATES[1:]:
                if trials[rate][0] > trials[best][0] + EPSILON:
                    best = rate
            if start in trials and trials[start][0] >= trials[best][0] - EPSILON:
                best = start  # (no change for a gain under EPSILON)
            if join and best == PRESS_RATES[0]:
                build.set_group(index=group["index"], fullDPS=False)
            if best != PRESS_RATES[-1] or (join and best == PRESS_RATES[0]):
                self._press(name, best)
            self.press_rows[name].update({"without": trials[PRESS_RATES[0]][1], "with": trials[best][1]})
        self.upkeep_round()  # (SEARCH41: the kept-up side skills' casts, after the chosen rates)
        return build.score > before + EPSILON

    def feeder_round(self, deadline: float) -> bool:
        """(SEARCH40; FEEDER_ROUND's comment) A Twister build's Whirlwind feeders: each legal one added as a side group,
        the press round choosing its rate for real, the best kept only if the build scores higher. True if one was kept."""
        from builder.search.context import gem_group_limit
        from builder.search.models import WHIRL_SKILLS
        from builder.search.recovery import TWISTER

        build, gems = self.build, self.gems
        if not FEEDER_ROUND or not PRESS_ROUND or self.skeleton.main_skill != TWISTER or clock.now() > deadline:
            return False
        groups = build.groups()
        present = {n["name"] for g in groups for n in g.get("gems") or []}
        if present & set(WHIRL_SKILLS):
            return False  # (a feeder is already there: the press round chooses its rate)
        if len([g for g in groups if not g.get("fromItem")]) >= gem_group_limit(build):
            return False  # (no skill slot left)
        cap = self.ctx.request.max_buttons
        feeders = [n for n in sorted(WHIRL_SKILLS) if n in self.ctx.gems and self.ctx.allowed_gem(n)
                   and self.ctx.gem_price(n) is not None and gems.affordable(n, 0.0, gems.budget_left()) and gems.usable(n)]
        before, snap = build.score, build.snapshot()  # (the snapshot carries the press choices and rows)
        best: tuple[float, str] | None = None
        self.feeder_log = []
        for name in feeders:
            if clock.now() > deadline:
                break
            build.set_group(gems=gems.persistent_gem(name), label=FEEDER_LABEL)
            self.resolve()
            if cap is not None and len(gems.buttons()) > cap:
                self.feeder_log.append({"skill": name, "kept": False, "why": "over the button cap"})
            else:
                self.press_round(deadline)
                rate = press_choices(build).get(name, 0.0)
                self.feeder_log.append({"skill": name, "rate": rate, "score": round(build.score - before, 4),
                                        "dps": round(build.objective.dps(build.stats), 1)})
                if rate > 0 and build.score > before + EPSILON and (best is None or build.score > best[0]):
                    best = (build.score, name)
            build.restore(snap)
        if best is None:
            return False
        build.set_group(gems=gems.persistent_gem(best[1]), label=FEEDER_LABEL)
        self.resolve()
        self.press_round(deadline + FEEDER_SECONDS)  # (its rate chosen again on the kept group: the guide's rows)
        if build.score <= before + EPSILON or press_choices(build).get(best[1], 0.0) <= 0:
            build.restore(snap)
            return False
        return True

    def spend_ascendancy(self) -> bool:
        """(SEARCH40) The user's rule, "every ascendancy node used or swapped": gate POE2T7528 (2b75d seed 2) delivered an
        Oracle with NO ascendancy notable (its best line never reached a sweep pass, where the ascendancy step runs:
        Search.blocks skips it otherwise). With fewer than tree.ASCENDANCY_POINTS allocated, finalize runs the ascendancy
        round once (every full allocation scored, the best kept; no unlock trial: it needs a deadline). Kept unless the
        score falls (then put back). True if more ascendancy points are spent."""
        from builder.search.tree import ASCENDANCY_POINTS

        build = self.build
        spent = build.call("summary").get("ascendancyAllocated", ASCENDANCY_POINTS)
        if spent >= ASCENDANCY_POINTS:
            return False
        before, snap = build.score, build.snapshot()
        self.step("ascendancy points", self.tree.ascendancy_round)
        if build.score < before - EPSILON:
            build.restore(snap)
            return False
        return build.call("summary").get("ascendancyAllocated", 0) > spent

    def _rescore(self) -> float:
        self.settle()
        self.build.score = self.build.objective.score(self.build.stats)
        return self.build.score

    def fit_buttons(self) -> bool:
        """THE USER (14 Nov, "Count it if used"): an item-granted skill the build presses counts toward the button cap; one
        it never uses doesn't (AUDIT31 A31-4, AUDIT32 A32-1). The main skill, a damage skill in Full DPS and a chosen press
        rate are pressed by definition (constraints.item_skill_used); an item skill outside them (a staff's Unleash, a
        Sigil of Power, a wand's Mana Drain, an enabled Raise Shield) is pressed when the build's score uses it: each such
        group is switched off once and the score compared (a drop over EPSILON: used, build.used_item_skills). Then, while
        the build has more buttons than the cap, the cheapest way out is taken, each tried for real: Bastion of Light's
        Raise Shield taps off (build.objective.raised_taps = False, MECHC21: its Blind goes to 0), a used item skill's
        group off, or an item damage skill out of Full DPS. With the taps off and a button to spare, the taps are tried
        back on and kept if the score rises. True if anything changed."""
        from builder.request.constraints import presses

        build = self.build
        known = getattr(build.ctx, "any_gems", None) or {}
        cap = getattr(getattr(build.ctx, "request", None), "max_buttons", None)
        objective = build.objective
        # (SEARCH44, THE USER 27 Nov: a flask drunk for its damage is a button: gems.damage_flask_buttons)
        damage_flasks = gems_module.damage_flask_buttons(getattr(getattr(self, "assumptions", None), "mods", None))
        if not any(g.get("fromItem") for g in build.groups()) and not (cap is not None and damage_flasks):
            return False  # (no item-granted group and no damage flask over a cap: nothing to probe or drop)
        before = build.score
        changed = False

        def item_skills(group: dict) -> list[str]:
            return [g["name"] for g in group.get("gems") or [] if g.get("enabled", True) and g["name"] in known
                    and not known[g["name"]]["support"] and "Meta" not in known[g["name"]]["skillTypes"]
                    and presses(known[g["name"]])]

        # 1. which item skills the score uses (probe each candidate group once)
        setattr(build, gems_module.USED_ITEM_SKILLS, set())
        pressed = gems_module.pressed_item_skills(build)
        candidates = [(g["index"], names) for g in build.groups()
                      if g.get("fromItem") and g.get("enabled") is not False and not g.get("main") and not g.get("fullDPS")
                      for names in [[n for n in item_skills(g) if n not in pressed]] if names]
        used: set[str] = set()
        if candidates:
            base = build.score
            for index, names in candidates:
                build.set_group(index=index, enabled=False)
                if self._rescore() < base - EPSILON:
                    used |= set(names)
                build.set_group(index=index, enabled=True)
            setattr(build, gems_module.USED_ITEM_SKILLS, used)
            self._rescore()
        if cap is None:
            return used != set() or abs(build.score - before) > EPSILON

        # 2. over the cap: the cheapest way out, tried for real, until it fits or nothing is left to drop
        def ways() -> list[tuple[str, Any, Any]]:
            out = []
            # (SEARCH44, THE USER 27 Nov: a damage flask is a button) a capped build may drop the flask's damage use: its
            # slot switched off and kept off (gear.CAPPED_FLASKS, Gear.counts), tried for real like every other way
            for slot in self.gear.damage_flask_slots() if damage_flasks else ():
                out.append((f"{slot} off (button cap)", lambda s=slot: self.gear.cap_flask(s, True),
                            lambda s=slot: self.gear.cap_flask(s, False)))
            if getattr(objective, "raised_taps", True) is not False and "Raise Shield" in {
                    n for n, e in (getattr(build, gems_module.ITEM_PRESSES, None) or {}).items()}:
                out.append(("Raise Shield taps off", lambda: setattr(objective, "raised_taps", False),
                            lambda: setattr(objective, "raised_taps", True)))
            for g in build.groups():
                if not g.get("fromItem") or g.get("enabled") is False or g.get("main"):
                    continue
                names = set(item_skills(g))
                index = g["index"]
                if names & set(getattr(build, gems_module.USED_ITEM_SKILLS, None) or ()):
                    out.append((f"group {index} off", lambda i=index: build.set_group(index=i, enabled=False),
                                lambda i=index: build.set_group(index=i, enabled=True)))
                elif names and g.get("fullDPS"):
                    out.append((f"group {index} out of Full DPS", lambda i=index: build.set_group(index=i, fullDPS=False),
                                lambda i=index: build.set_group(index=i, fullDPS=True)))
            return out

        count = len(self.gems.buttons())
        while count > cap:
            best = None
            for label, do, undo in ways():
                do()
                score = self._rescore()
                fits = len(self.gems.buttons())
                undo()
                if best is None or (max(fits, cap), -score) < (max(best[1], cap), -best[2]):
                    best = (do, fits, score, label)
            if best is None or best[1] >= count:
                self._rescore()
                break
            best[0]()
            self._rescore()
            count, changed = len(self.gems.buttons()), True
        # 3. taps back on when a button is spare
        if getattr(objective, "raised_taps", True) is False and len(self.gems.buttons()) < cap:
            score = build.score
            objective.raised_taps = True
            if self._rescore() <= score + EPSILON or len(self.gems.buttons()) > cap:
                objective.raised_taps = False
                self._rescore()
            else:
                changed = True
        return changed or used != set() or abs(build.score - before) > EPSILON

    def press_guide_rows(self) -> list[dict]:
        """The play guide's "without it / with it" row for each chosen press rate (CLAUDE.md's flat-rate rule: say how
        the build feeds it), from the last press round's trials (finalize runs one on the finished build)."""
        rows = []
        for name, info in sorted(self.press_rows.items()):
            rate = press_choices(self.build).get(name, 0.0)
            if rate <= 0 or "with" not in info:
                continue
            without, with_it = number(info.get("without")), number(info.get("with"))
            rows.append({
                "var": f"press:{name}", "label": f"{name}: pressed {rate:g} times a second*", "kind": play_guide.PLAYER,
                "counted": True,
                "play": (f"press {name} about {rate:g} times a second beside the main skill (no cooldown or duration sets "
                         f"its use, so the search chose this rate as a way to play the build; its presses' time is taken "
                         f"from the main skill): {with_it:,.0f} DPS with it, {without:,.0f} without it"),
                "dps_with": with_it, "dps_without": without,
                "change": (with_it - without) / without if without > 0 else 0.0, "confidence": "modelled"})
        return rows

    def follow_uptimes(self) -> None:
        """Between conditions rounds, a commit or trial can switch on an effect kept up for part of the fight, or move an
        uptime. Its weight follows at once: each effect's ratio (DPS without it / with it) and floor deltas come from the
        last round (weigh_uptimes), and an effect the round didn't see gets its own one-off recalculation (cached until the
        next round), so a trial that raises an effect from 0% counts it at its share, not fully up (MECH31). The uptimes
        are the current ones; the ratios are the round's (an estimate between rounds; exact at finalize)."""
        objective = self.build.objective
        cache = self.uptime_cache
        effects = _part_time(self)
        live = {var: up for var, (up, _) in effects.items()}
        missing = [var for var in sorted(live) if var not in cache]
        if missing:
            on_stats = dict(self.build.stats)
            on = objective.dps(on_stats)
            if on <= 0:
                return
            for var in missing:
                cache[var] = _without(self, var, effects[var][1], on_stats, on)[:2]
        factor = 1.0
        for var in sorted(live):
            if not effects[var][1].get("in_stats"):  # (a share already in PoB's stats: LEDGER_VAR)
                factor *= live[var] + (1.0 - live[var]) * cache[var][0]
        floor_deltas = self.floor_without(live, on_stats=None)
        if factor != objective.uptime_factor or floor_deltas != objective.floor_deltas:
            objective.uptime_factor, objective.floor_deltas = factor, floor_deltas
            self.build.score = objective.score(self.build.stats)

    def resolve(self) -> bool:
        """Conditions and assumptions follow the build; if either changed (e.g. a Remnant or Frenzy
        Charge source arrived), the main group's calculated skill and stat set are chosen again."""
        changed = self.settle()
        if changed and not self.gems.scouting:  # (a scouting trial's flip: no new choice, Gems.scouting)
            self.gems.choose_active_skill()
        return changed

    def choose_distance(self) -> bool:
        """The boss distance is the player's choice (the play guide's spec, item 4): a build with lines that depend on it
        (conditions.DISTANCE_LINE: Presence, close range, within / further than N m) stands where it scores best, the
        defence floors included (the score reads them at the same distance), else at its natural range. Returns True if
        the distance moved."""
        conditions = self.conditions
        lines = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        if any(g["name"] == "Blasphemy" and g.get("enabled", True) for grp in self.build.groups()
               if grp.get("enabled", True) for g in grp["gems"]):
            lines.append("Blasphemy: Curse Auras on enemies in your Presence (models.curse_uses)")
        # (SEARCH34, MECHC48's routed item) Overwhelming Presence's debuff is on only for a boss in your Presence
        # (assumptions: AuraEffect MORE -100 outside it), so a ranged build tries standing at the Presence edge for it too
        from builder.search.assumptions import OVERWHELMING_PRESENCE

        if any(g["name"] == OVERWHELMING_PRESENCE and g.get("enabled", True) for grp in self.build.groups()
               if grp.get("enabled", True) for g in grp["gems"]):
            lines.append("Overwhelming Presence: enemies in your Presence are easier to Stun and inflict Elemental "
                         "Ailments upon (assumptions' OverwhelmingOutside)")
        # (SEARCH39; MECH105's routed item, low priority) Heart of Ice "Chills all enemies in your Presence" (ActiveSkills
        # heart_of_ice): a build holding it, not only an Icestorm build (conditions.distance_candidates), tries the
        # Presence edge; the score decides
        if any(g["name"] == HEART_OF_ICE and g.get("enabled", True) for grp in self.build.groups()
               if grp.get("enabled", True) for g in grp["gems"]):
            lines.append("Heart of Ice: Chills all enemies in your Presence")
        # (SEARCH41; MECH106's routed item, the manager 24 Nov 17:24) Malice's Critical Weakness counts only while the boss
        # is in your Presence (MECH106's 5a13aec0, models.MALICE): a caster standing back with Malice tries the Presence
        # edge too (before: a 5 m caster with a 4 m Presence read 0 Critical Weakness and never tried 4 m)
        from builder.search.models import MALICE

        if any(g["name"] == MALICE and g.get("enabled", True) for grp in self.build.groups()
               if grp.get("enabled", True) for g in grp["gems"]):
            lines.append("Malice: Critical Weakness on enemies in your Presence")
        lines += support_distance_lines(self.build.groups())
        # (the Presence radius in metres: the Presence-edge trial for "in your Presence" lines and the just-beyond one for
        # "no enemies are in your Presence" lines, Lioneye's Glare; distance_candidates took 0 m before, so neither was
        # tried: the manager's routing of MECH88's leftover, SEARCH28)
        radius = number((self.build.call("stats", fields=["PresenceRadius"]) or {}).get("PresenceRadius")) / 10
        candidates = distance_candidates(lines, conditions.main_types, presence_radius=radius,
                                         skill=getattr(conditions, "main_skill", ""))
        before = conditions.distance
        if not candidates:
            conditions.distance = None
            if before is not None:
                self.settle()
            return before is not None
        natural = candidates[0]
        # (the others in order of distance, not of the bridge's sourceLines, which walks PoB's per-process order: two
        # "further than N m" distances that score the same keep the nearer one, every run; PERF12)
        candidates = [natural] + sorted(candidates[1:])
        scores, keys = {}, {}
        for metres in candidates:
            conditions.distance = None if metres == natural else (metres, "")
            # (SEARCH41; MECH108's POE2T8811 / 8816: a Presence build's Withered / Presence numbers keep moving over further
            # settles, so one settle could rank the distances on half-moved numbers) each candidate is settled to rest,
            # up to self.distance_settles rounds (DISTANCE_SETTLES where the choice is final, 1 mid-search)
            for _ in range(getattr(self, "distance_settles", 1)):
                if not self.settle():
                    break
            scores[metres] = self.build.objective.value(self.build.stats)
            # (SEARCH39: the choice compares damage, then the hard shortfall, then the rest: Objective.choice_key)
            keys[metres] = self.build.objective.choice_key(self.build.stats)
        best = max(candidates, key=lambda m: (keys[m], m == natural))
        tried = ", ".join(f"{m:g} m {scores[m]:,.0f}" for m in candidates)
        if best <= MELEE_DISTANCE:
            why = "close range: the build leans into Presence, close-range or support lines"
        elif radius > 0 and best == math.floor(radius * 10) / 10:
            why = f"at the edge of its {radius:.1f} m Presence: \"in your Presence\" lines, the boss as far as it reaches"
        elif radius > 0 and best == math.ceil(radius * 10) / 10 + 0.5:
            why = f"just beyond its {radius:.1f} m Presence: \"no enemies in your Presence\" lines"
        else:
            why = "beyond its \"further than\" lines"
        why += f" (DPS at {tried}; game limits first, then DPS against the defence floors)"
        conditions.distance = None if best == natural else (best, why)
        self.settle()
        return conditions.distance != before

    def final_choices(self) -> bool:
        """(SEARCH39, the manager's 24 Nov item 1) The main skill's stat set / part and the boss distance chosen once more
        in the FINISHED state, after the last uptime weighing: each was chosen mid-flow, where the floors' part-time
        deltas and the conditions were not yet the finished build's. Example build 16 (POE2T7491): chosen mid-flow, Ice
        Shot's Arrow set at 4 m (61.4M); in the finished state its Shards set at 2 m reads 131.2M and is short less on
        Physical max hit (0.095 vs 0.198), but nothing chose again. Kept only if the choice key (Objective.choice_key)
        rises and the shortfall (game limits x10 + request floors) is no worse; else everything is put back. Returns
        True if kept."""
        import copy

        build, objective = self.build, self.build.objective

        def shortfall_of(stats: dict) -> float:
            return round(sum(a * (10.0 if h else 1.0) for _, a, h in objective.deficits(stats) if h is not None), 6)

        before_key, before_short = objective.choice_key(build.stats), shortfall_of(build.stats)
        snap = build.snapshot()
        saved = [(owner, self.gems.plain_state(owner)) for owner in (self.conditions, self.assumptions) if owner]
        self.gems.choose_active_skill()
        self.choose_distance()
        self.weigh_life()  # (SEARCH41: the Low Life factor in the state just chosen, LIFE_AFTER_PRESS)
        self.weigh_uptimes()
        after_key = objective.choice_key(build.stats)
        higher = (after_key[:2] > before_key[:2]
                  or (after_key[:2] == before_key[:2] and after_key[2] > before_key[2] + EPSILON))
        if higher and shortfall_of(build.stats) <= before_short + 1e-6:
            return True
        for owner, state in saved:
            for k, v in (state or {}).items():
                setattr(owner, k, copy.deepcopy(v))
        build.restore(snap)
        return False

    def rolling_share(self) -> float:
        """The share of the fight the build spends dodge rolling, from the dodge-roll notes of the models (Cast on Dodge,
        Mirage Archer: models.DODGE_ROLL_SECONDS per roll)."""
        share = 0.0
        for note in self.assumptions.active:
            if note.get("label", "").startswith("Time spent dodge rolling") and (m := re.match(r"([\d.]+)%", str(note.get("value")))):
                share += float(m.group(1)) / 100
        return min(share, 1.0)

    def choose_stationary(self) -> bool:
        """Standing still is the player's choice (the play guide's spec, item 1a). A build with a line that depends on it
        (conditions.STATIONARY_LINE) stands still (the resolver's default, so a trial of a new stationary line sees it),
        unless moving scores better on the full score (mana sustain and the defence floors included: Ether Flow's
        regeneration is lower while stationary), or the build dodge rolls so much that standing still holds under UPTIME
        of the fight (noted). Returns True if the choice changed."""
        conditions, before = self.conditions, self.conditions.stationary_off
        self.play_notes.pop("stationary", None)
        conditions.rolling = self.rolling_share()
        lines = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        if not any(STATIONARY_LINE.search(t) for t in lines):
            conditions.stationary_off = None
        elif 1.0 - conditions.rolling < UPTIME:
            conditions.stationary_off = "dodge rolls"
            self.play_notes["stationary"] = {
                "label": "Standing still: not counted", "value": f"{1 - conditions.rolling:.0%} of the fight",
                "basis": f"the build dodge rolls {conditions.rolling:.0%} of the time (the models' dodge-roll time), so its "
                         f"stationary lines would hold under {UPTIME:.0%} of the fight", "confidence": "theory"}
        else:
            # (walking from the last round's choose_moving would switch standing still off in both arms of the trial;
            # choose_moving, right after, chooses again)
            conditions.moving = False
            conditions.stationary_off = None
            self.settle()
            still = self.build.score
            conditions.stationary_off = "moving scored better"
            self.settle()
            if self.build.score < still:
                conditions.stationary_off = None
                self.settle()
            return conditions.stationary_off != before
        if conditions.stationary_off != before:
            self.settle()
        return conditions.stationary_off != before

    def choose_moving(self) -> bool:
        """Walking while using skills is the player's choice (MECH86's conditionMoving; the manager's routing, SEARCH28): a
        build with a "while moving" line PoB reads under Condition:Moving (conditions.moving_candidates: Spray and Pray's
        damage against its Accuracy, Evasion or regeneration while moving) is scored standing and walking, all fight, and
        the higher full score kept (defence floors and sustain included); the play note gives both DPS. A build that
        choose_stationary already moves ("moving scored better") walks already: nothing to choose. Returns True if the
        choice changed."""
        conditions = self.conditions
        before = bool(getattr(conditions, "moving", False))
        self.play_notes.pop("moving", None)
        lines = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        if not moving_candidates(lines) or conditions.stationary_off == "moving scored better":
            if before:
                conditions.moving = False
                self.settle()
            return before
        conditions.moving = False
        self.settle()
        standing, standing_dps = self.build.score, self.build.objective.dps(self.build.stats)
        conditions.moving = True
        self.settle()
        walking, walking_dps = self.build.score, self.build.objective.dps(self.build.stats)
        if walking <= standing + EPSILON:
            conditions.moving = False
            self.settle()
        chosen = "walking" if conditions.moving else "standing"
        self.play_notes["moving"] = {
            "label": f"Walk while attacking or casting: {'yes' if conditions.moving else 'no'}",
            "value": f"{walking_dps if conditions.moving else standing_dps:,.0f} DPS",
            "basis": f"the build's \"while moving\" lines: {walking_dps:,.0f} DPS walking all fight, {standing_dps:,.0f} "
                     f"standing while using skills; {chosen} scores higher (full score, defence floors and sustain "
                     f"included); the movement-speed penalty while using skills isn't scored", "confidence": "theory"}
        return conditions.moving != before

    # (SEARCH41; SPEED3's POE2T7883, the manager 25 Nov 00:14) LIFE_AFTER_PRESS: weigh_life ran only inside choose_play,
    # BEFORE the press round chose the rates, and never again: example build 15's damage comes from its pressed
    # Incinerate, so finalize / fresh_score / gate4's fair scoring kept a factor measured on the unpressed build (x1.0873;
    # x1.0018 once measured on the pressed one: an OVERCOUNT of +8.5% on b15's first scoring, its whole -7.9% drift on a
    # second). So it is measured again after each finished-state press round and after final_choices' re-choice.
    def weigh_life(self) -> None:
        """The boss's Low Life is fight-decided (the spec, item 1c): its last 35% of life ("Low Life if it has 35% of its
        Maximum Life or less", KeywordPopups) takes 0.35 / (DPS on Low Life) of the time per unit of life, so the headline is
        the time-weighted 1 / (0.65 / D + 0.35 / D_low) (objective.life_factor). Full Life holds only for the opening hit:
        not weighted (labelled in the guide). One extra recalculation, only for a build with a Low Life line."""
        objective = self.build.objective
        self.play_notes.pop("low_life", None)
        lines = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        objective.life_factor = 1.0
        if any(LOW_LIFE_LINE.search(t) for t in lines):
            above = objective.dps(self.build.stats)
            self.build.set_config({"conditionEnemyLowLife": True})
            self.assumptions.apply()
            low = objective.dps(self.build.stats)
            self.build.set_config({"conditionEnemyLowLife": False})
            self.assumptions.apply()
            if above > 0 and low > 0 and abs(low - above) > 0.001 * above:
                objective.life_factor = 1.0 / (1.0 - LOW_LIFE_SHARE + LOW_LIFE_SHARE * above / low)
                self.play_notes["low_life"] = {
                    "label": "Boss on Low Life (its last 35% of life), weighted into the DPS",
                    "value": f"x{objective.life_factor:.3f}",
                    "basis": f"{above:,.0f} DPS above 35% of the boss's life, {low:,.0f} below; the time to kill weighs "
                             f"them 65% / 35% of its life", "confidence": "theory"}
        self.build.score = objective.score(self.build.stats)

    def part_time(self) -> dict[str, tuple[float, dict]]:
        """The effects on for only part of the fight: var -> (share, info) (_part_time)."""
        return _part_time(self)

    def weigh_uptimes(self) -> None:
        """Effects the build keeps up for only part of the fight (conditions.uptimes: ailments, Onslaught, Arcane Surge,
        Armour Broken, Exposure, the debuffs) count at their real share (the user, 9 Nov 2026). With all of them on the
        DPS is D; one recalculation with effect c off gives r_c = D(c off) / D; the headline is D x the product of
        (u_c + (1 - u_c) x r_c) (objective.uptime_factor): n + 1 calculations, not 2^n, the effects taken as independent
        (labelled: effects that add into one pool, like Shock's and Intimidate's damage taken, interact a little). The
        floors are checked with all of them off at once (floor_without: one more recalculation), each one's time-weighted
        defence kept in its play note. Refreshed each conditions round and reused between them (as weigh_life); exact at finalize."""
        objective = self.build.objective
        objective.uptime_factor, objective.floor_deltas = 1.0, []
        self.uptime_rows, self.uptime_cache, self.uptime_floor = [], {}, {}
        for key in [k for k in self.play_notes if k.startswith("uptime:")]:
            del self.play_notes[key]
        effects = _part_time(self)
        if effects:
            on_stats = dict(self.build.stats)
            on = objective.dps(on_stats)
            factor = 1.0
            for var, (up, info) in sorted(effects.items()):
                if on <= 0:
                    continue
                ratio, deltas, off_stats = _without(self, var, info, on_stats, on)
                self.uptime_cache[var] = (ratio, deltas)
                share = 1.0 if info.get("in_stats") else up + (1.0 - up) * ratio  # (LEDGER_VAR: in PoB's stats)
                factor *= share
                floors = _uptime_floor_rows(self, var, up, on_stats, off_stats, deltas)
                defence = "; ".join(f"{label} {a:,.4g} with it, {b:,.4g} without, {w:,.4g} "
                                    f"time-weighted (the floors are checked with "
                                    f"every part-time effect off)" for label, a, b, w in floors[:3])
                self.uptime_rows.append({"var": var, "label": info["label"], "uptime": up, "ratio": ratio, "factor": share,
                                         "defence": defence})
                self.play_notes[f"uptime:{var}"] = {
                    "label": f"{info['label']}: up {up:.0%} of the fight, counted at that share",
                    "value": f"x{share:.3f}",
                    "basis": f"{on:,.0f} DPS with it, {on * ratio:,.0f} without; the effects are weighed as independent "
                             f"of each other" + (f". Defences: {defence}" if defence else ""),
                    "confidence": "theory"}
            factor *= _weigh_shared_flags(self, effects, on_stats, on)
            objective.uptime_factor = factor
            objective.floor_deltas = self.floor_without({var: up for var, (up, _) in effects.items()}, on_stats)
        self.build.score = objective.score(self.build.stats)

    def floor_without(self, live: dict, on_stats: dict | None) -> list[dict]:
        """The stats that change with every part-time effect off at once (the floors count an effect only at full uptime,
        the user, 9 Nov 2026: one recalculation, not one per effect, so two effects that each carry part of a floor
        can't hide each other). Cached per set of effects until the next conditions round (trials flip effects on and
        off: a one-entry cache recalculated at every flip); one effect reuses its own deltas (uptime_cache)."""
        names = frozenset(live)
        if not names:
            return []
        if len(names) == 1 and next(iter(names)) in self.uptime_cache:
            deltas = self.uptime_cache[next(iter(names))][1]
            return [deltas] if deltas else []
        if names not in self.uptime_floor:
            stats = dict(self.build.stats) if on_stats is None else on_stats
            effects = _part_time(self)
            infos = {var: effects[var][1] for var in names if var in effects}
            self.uptime_floor[names] = _all_off(self, infos, stats)
        deltas = self.uptime_floor[names]
        return [deltas] if deltas else []

    def choose_play(self) -> bool:
        """The play-style choices and fight weights (play_guide's spec): distance, standing still, Low Life."""
        moving = getattr(self.conditions, "moving", False)  # (choose_stationary's trial sets it off; choose_moving chooses)
        changed = self.choose_distance()
        changed = self.choose_stationary() or changed
        changed = self.choose_moving() or changed or self.conditions.moving != moving
        changed = self.choose_curse_lapse() or changed
        changed = self.choose_remnant_walk() or changed
        self.weigh_life()
        return changed

    def choose_remnant_walk(self) -> bool:
        """(MECHC67, the manager 26 Nov 11:47; assumptions.TARGET_REMNANTS' note) For a moving build beyond reach of the
        Remnants its hits make at the boss, walking in for them (their recovery, for the walking time's DPS) or leaving
        them is a play strategy: both tried, the higher full score kept (defence floors included), ties to walking in;
        the play note gives both DPS. True if the choice changed."""
        from builder.search import assumptions as assumptions_module

        attr = getattr(assumptions_module, "TARGET_SKIP", None)
        if attr is None:
            return False
        objective = self.build.objective
        before = bool(getattr(objective, attr, False))
        self.play_notes.pop(attr, None)
        if not getattr(self.build, "target_walk_offered", False):
            if before:  # (no longer offered: back to the default)
                setattr(objective, attr, False)
                self.settle()
                return True
            return False
        kept, kept_dps = self.build.score, objective.dps(self.build.stats)
        setattr(objective, attr, not before)
        self.settle()
        trial, trial_dps = self.build.score, objective.dps(self.build.stats)
        better = trial > kept + EPSILON  # (ties keep the current choice)
        if not better:
            setattr(objective, attr, before)
            self.settle()
        chosen = (not before) if better else before
        walk_dps, skip_dps = (trial_dps, kept_dps) if before else (kept_dps, trial_dps)
        self.play_notes[attr] = {
            "label": ("Leave the Remnants made at the boss" if chosen else "Walk in for the Remnants made at the boss"),
            "value": f"{(skip_dps if chosen else walk_dps):,.0f} DPS",
            "basis": (f"walking in for them: {walk_dps:,.0f} DPS with their recovery; leaving them: {skip_dps:,.0f} DPS "
                      "without it (the search's choice on the full score, defence floors included; see 'Remnants made "
                      "at the boss: collected')"),
            "confidence": "theory"}
        return chosen != before

    def choose_curse_lapse(self) -> bool:
        """Letting a hand-cast curse run out before each recast is a play strategy (MECH75's model, models.CURSE_LAPSE;
        the manager, 15 Nov 01:01): with Withering Hex ("Non-Cursed enemies you Curse have 5 Withered stacks inflicted on
        them") each recast that finds the boss uncursed applies its stacks, against some of the curse's uptime. Each
        curse conditions.curse_lapse_candidates offers (none without Withering Hex) is tried played so and kept up, one at
        a time, and the higher full score kept (defence floors included); the play note says which and both DPS.
        Returns True if the choice changed. (Before MECH75's model is on the branch: nothing to choose.)"""
        from builder.search import conditions as conditions_module, models as models_module

        candidates_of = getattr(conditions_module, "curse_lapse_candidates", None)
        attr = getattr(models_module, "CURSE_LAPSE", None)
        if candidates_of is None or attr is None:
            return False
        objective = self.build.objective
        before = set(getattr(objective, attr, None) or ())
        self.play_notes.pop("curse_lapse", None)
        texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        names = candidates_of(self.build, texts)
        chosen = before & set(names)
        if chosen != before:  # (a curse no longer offered: Withering Hex or the curse is gone)
            setattr(objective, attr, set(chosen))
            self.settle()
        if not names:
            return chosen != before
        for name in sorted(names):
            kept = self.build.score
            trial = chosen ^ {name}
            setattr(objective, attr, set(trial))
            self.settle()
            if self.build.score > kept + EPSILON:
                chosen = trial
            else:
                setattr(objective, attr, set(chosen))
                self.settle()
        if chosen:
            played = objective.dps(self.build.stats)
            setattr(objective, attr, set())
            self.settle()
            kept_up = objective.dps(self.build.stats)
            setattr(objective, attr, set(chosen))
            self.settle()
            self.play_notes["curse_lapse"] = {
                "label": f"Let {', '.join(sorted(chosen))} run out before each recast",
                "value": f"{played:,.0f} DPS",
                "basis": f"Withering Hex: each recast lands on an uncursed boss and applies its Withered stacks; "
                         f"{played:,.0f} DPS played so, {kept_up:,.0f} keeping the curse up (the recast lands "
                         f"{getattr(models_module, 'CURSE_LAPSE_GAP', 0.5):g} s after it runs out: LOW, the player's timing)",
                "confidence": "theory"}
        return chosen != before

    def conditions_round(self) -> None:
        """A conditions round (_conditions_round), then the partial uptimes weighed (weigh_uptimes)."""
        self._conditions_round()
        self.weigh_uptimes()

    def _conditions_round(self) -> None:
        """Resolve, and choose the calculated skill and stat set again: which one scores best
        changes as the rest of the build grows (Explosive Spear's explosion overtakes its spear).
        Gems whose usage requirement lost its source go first (builder/search/usage.py)."""
        if self.choose_play():
            self.gems.choose_active_skill()
        if self.gems.drop_unusable():
            return  # it resolved, which chose the stat set again
        if not self.resolve():
            self.gems.choose_active_skill()

    # Blocks ------------------------------------------------------------------------------------

    def step(self, phase: str, fn, *args) -> bool:
        self.build.phase = phase
        before, evals, started = self.build.score, self.build.evals, clock.now()
        stopped = False
        try:
            fn(*args)
        except OutOfTime:
            stopped = True
            raise
        finally:
            now = clock.now()
            entry = self.timing.setdefault(phase, [0.0, 0, 0.0])
            entry[0] += now - started
            entry[1] += self.build.evals - evals
            # (a step the hard stop cut ends inside a trial: its score isn't the build's, which goes back to the last
            # boundary; counted, it showed as losses of ln 10-13 in steps that keep only gains, POE2T205)
            if not stopped:
                entry[2] += self.build.score - before
            if self.deadline is not None and now > self.deadline:
                self.overrun[phase] = round(max(self.overrun.get(phase, 0.0), now - max(started, self.deadline)), 2)
        if self.deadline is not None and (self.boundary is None or self.build.score != before):
            self.boundary = self.build.snapshot()
            # The hard stop is armed once a step has finished: before that there is no boundary to go back to, and a
            # stop inside a fresh build's first gear round (it takes longer than a 60 s probe) handed on a half-done
            # state; all five of a gate run's lines then stuck at 15K with no main group (POE2T194, x0.09)
            self.build.stop_at = self.stop_at
        return self.build.score > before + EPSILON

    def checkpoint(self) -> None:
        """A step's commit that leaves the build whole (the tree's kept passive): the hard stop goes back here, not to
        the step's start. A probe's tree step cut by the stop lost all it had allocated (POE2T225: 23 s, 1,740
        evaluations, gain 0; the five lines started round 1 at 7.8K and ended x0.46)."""
        if self.deadline is not None and self.build.stop_at is not None:
            self.boundary = self.build.snapshot()

    def blocks(self, deadline: float) -> bool:
        """One pass over every block. Full sweeps (every support in every slot, every item, every
        ascendancy allocation) run every SWEEP_EVERY passes or after a kick touched that block;
        passes in between rescore shortlists."""
        resume = self.resume_step if PERSIST_LINES else None
        if resume is not None:  # (M1: the line's pass goes on where its last task stopped)
            sweep = self.resume_sweep
        else:
            sweep = (self.line_passes if PERSIST_LINES else self.passes) % SWEEP_EVERY == 0
            self.line_passes += 1
        self.resume_step, self.resume_sweep = None, sweep
        self.passes += 1
        full_gear = sweep or "gear" in self.kicked
        gear_deadline = deadline
        if full_gear and any(item is not None for item in self.build.equipped.values()):
            now = clock.now()
            gear_deadline = min(deadline, now + GEAR_SHARE * max(0.0, deadline - now))
        steps = [
            ("gear", self.gear.gear_round, (gear_deadline, full_gear)),
            # (ascendancy before supports: an ascendancy can grant a Remnant source (that build's: Into the Breach), and supports
            # swept before it scored all alike; a line's one pass of focus round 1 then ended support-less, POE2T220)
            ("ascendancy", self.tree.ascendancy_round, (deadline,)),  # (SEARCH37: the unlock trials' time)
            ("supports", self.gems.optimize_supports, (deadline, 12, sweep or "supports" in self.kicked)),
            ("tree rebuild", self.tree_rebuild, (deadline,)),
            ("tree", self.tree.grow, (deadline,)),
            ("attributes", self.tree.attributes_round, ()),
            ("jewels", self.jewels.jewel_round, (deadline, sweep or "gear" in self.kicked)),
            ("conditions", self.conditions_round, ()),
            ("press rates", self.press_round, (deadline,)),
            ("condition sources", self.sources.round, (deadline,)),
            ("persistent", self.gems.optimize_persistent, (deadline,)),
            ("granted metas", self.gems.granted_meta_round, (deadline,)),
            ("meta gems", self.gems.meta_round, (deadline,)),
            ("meta spells", self.gems.meta_spell_round, (deadline,)),
            ("totems", self.gems.totem_round, (deadline,)),
            ("marks", self.gems.mark_round, (deadline,)),
            ("mirages", self.gems.mirage_round, (deadline,)),
            ("roar", self.gems.roar_round, (deadline,)),
            ("prune and regrow", self.tree.swap_round, (deadline,)),
        ]
        if FILL_IN_SEARCH:  # (SEARCH35: after the supports step; off, the steps are as before)
            steps.insert(3, ("socket fill", self.socket_fill_round, (deadline,)))
        if gems_module.META_MAIN:  # (SEARCH38: after the meta spells; off, the steps are as before)
            at = next(i for i, step in enumerate(steps) if step[0] == "meta spells") + 1
            steps.insert(at, ("meta main spell", self.gems.meta_main_round, (deadline,)))
        if self.skeleton.swap:
            steps.insert(3, ("weapon sets", self.weapon_set_round, ()))
        improved = False
        finished = True
        limit = deadline  # (the steps' own stop; GEAR_FIRST_LAST moves it earlier)
        if resume is not None and resume in [phase for phase, _, _ in steps]:
            steps = steps[[phase for phase, _, _ in steps].index(resume):]
            empty = self.gear_census()["empty"] if GEAR_FIRST and resume != "gear" and self.gear.resume is not None else []
            if empty and GEAR_FIRST_LAST:  # (SEARCH37: the steps stop early; the gear may get the time kept back)
                now = clock.now()
                limit = max(now, deadline - GEAR_SHARE * max(0.0, deadline - now))
                steps = [(phase, fn, tuple(limit if a is deadline else a for a in args)) for phase, fn, args in steps]
            elif empty:
                now = clock.now()
                cut = min(deadline, now + GEAR_SHARE * max(0.0, deadline - now))
                if GEAR_FIRST_EMPTY:  # (SEARCH37: only the empty slots; the cut round waits for the next gear step)
                    steps.insert(0, ("gear", self.gear.gear_round, (cut, True, list(empty))))
                else:  # (SEARCH35: the cut round goes on first)
                    steps.insert(0, ("gear", self.gear.gear_round, (cut, True)))
        gear_last = limit < deadline  # (GEAR_FIRST_LAST's kept-back time, before the task tail's)
        tail_at = None
        if TASK_TAIL and not getattr(self, "tail_done", True):
            start = getattr(self, "task_start", None)
            start = clock.now() if start is None else start
            tail_at = max(clock.now(), deadline - TASK_TAIL_SHARE * max(0.0, deadline - start))
            if tail_at < limit:
                cap = tail_at
                steps = [(phase, fn, tuple(min(a, cap) if isinstance(a, float) and a >= cap else a for a in args))
                         for phase, fn, args in steps]
                limit = tail_at
        ran: set[str] = set()
        for phase, fn, args in steps:
            if clock.now() > limit:
                finished = False
                self.resume_step = phase  # (M1: the next task of this line starts here)
                break
            self.current_step = phase
            if phase in ("meta gems", "meta spells") and "meta" in self.kicked:
                pass  # a meta kick took the group out: the meta round re-chooses it now, not at the next sweep
            elif phase in ("ascendancy", "condition sources", "meta gems", "meta spells", "totems", "marks", "mirages", "roar") and not sweep:
                continue
            if IDLE_SKIP and self.idle_skip(phase):
                continue
            ran.add(phase)
            gained = self.step(phase, fn, *args)
            if IDLE_SKIP and phase in IDLE_STEPS:
                entry = self.idle.setdefault(phase, [0, 0])
                entry[0] = 0 if gained else entry[0] + 1
            improved = gained or improved
        if tail_at is not None and not finished and clock.now() <= deadline:  # (TASK_TAIL: once per task)
            improved = self.task_tail(deadline, ran) or improved
        if gear_last and not finished and clock.now() <= deadline:  # (SEARCH37 GEAR_FIRST_LAST: the pass
            # didn't end in time, so it won't reach its gear step this task: the cut round goes on in the time kept back)
            self.current_step = "gear"
            only = (list(empty),) if GEAR_FIRST_LAST_EMPTY else ()  # (SEARCH40: last_empty, the empty slots alone)
            improved = self.step("gear", self.gear.gear_round, deadline, True, *only) or improved
        self.current_step = None
        self.kicked.clear()
        self.passes_done += finished
        return improved

    def task_tail(self, deadline: float, ran: set[str]) -> bool:
        """(TASK_TAIL) The tail's steps this pass didn't reach, in the time the task kept back; once per task. The line's
        pass still resumes where it stopped (resume_step), so they run again in their place when it gets there."""
        self.tail_done = True
        calls = {"press rates": (self.press_round, (deadline,)), "prune and regrow": (self.tree.swap_round, (deadline,))}
        improved = False
        for phase in TASK_TAIL:
            if phase in ran or clock.now() > deadline:
                continue
            self.current_step = None  # (a hard stop inside the tail keeps the pass's own resume_step)
            fn, args = calls[phase]
            improved = self.step(f"{phase} (tail)", fn, *args) or improved
        return improved

    def late_sweep(self, deadline: float) -> list[str]:
        """(LATE_SWEEP) The sweep-only steps once more on the finished build (finalize_task); the steps that gained."""
        calls = {"ascendancy": (self.tree.ascendancy_round, (deadline,)),
                 "condition sources": (self.sources.round, (deadline,)),
                 "meta gems": (self.gems.meta_round, (deadline,)), "meta spells": (self.gems.meta_spell_round, (deadline,)),
                 "totems": (self.gems.totem_round, (deadline,)), "marks": (self.gems.mark_round, (deadline,)),
                 "mirages": (self.gems.mirage_round, (deadline,)), "roar": (self.gems.roar_round, (deadline,))}
        gained = []
        for phase in SWEEP_ONLY:
            if clock.now() > deadline:
                break
            fn, args = calls[phase]
            if self.step(f"{phase} (late)", fn, *args):
                gained.append(phase)
        return gained

    def idle_skip(self, phase: str) -> bool:
        """(SEARCH35, IDLE_SKIP) Whether this pass skips a step that gained nothing in its last IDLE_AFTER runs on the
        line: skipped IDLE_RETRY - 1 times, then run again; a kick that touched it runs it now."""
        if not IDLE_SKIP or phase not in IDLE_STEPS:
            return False
        entry = self.idle.setdefault(phase, [0, 0])
        if entry[0] < IDLE_AFTER or phase in self.kicked or ("meta" in self.kicked and phase == "meta gems"):
            return False
        entry[1] += 1
        if entry[1] % IDLE_RETRY == 0:
            return False
        self.skipped[phase] = self.skipped.get(phase, 0) + 1
        return True

    def line_regrow(self, deadline: float) -> bool:
        """(SEARCH35, LINE_REGROW) The line's last task: its tree regrown on its gear under the floors (Tree.final_regrow's
        partial clears); kept only if no further from the request and higher (final_regrow's rule)."""
        self.weigh_uptimes()
        before = self.build.score
        self.line_regrow_rows = self.tree.final_regrow(deadline, clears=LINE_REGROW_CLEARS, reweigh=self.weigh_uptimes)
        return self.build.score > before + EPSILON

    def socket_fill_round(self, deadline: float) -> bool:
        """(SEARCH35, FILL_IN_SEARCH) The open-socket fill (Gems.fill_open, the main group) inside the search, once per
        main-group set the line holds with an open socket (fill_seen), the set it leaves counted as seen too."""
        if not FILL_IN_SEARCH:
            return False
        main = self.build.main_group()
        if main is None or not main.get("enabled", True):
            return False
        key = [main["index"], *(g["name"] for g in main["gems"])]
        if key in self.fill_seen or self.gems.socket_use(main, False)[2] <= 0:
            return False
        self.fill_seen.append(key)
        improved = self.gems.fill_open(min(deadline, clock.now() + FILL_SEARCH_SECONDS), kinds=("main",))
        after = self.build.main_group()
        if after is not None and (done := [after["index"], *(g["name"] for g in after["gems"])]) not in self.fill_seen:
            self.fill_seen.append(done)
        return improved

    def tree_rebuild(self, deadline: float) -> bool:
        """M2: once per line, from its second pass, clear the tree and regrow it against the build as it is now (the
        move Search.run makes at its second escalation); kept only if the build scores higher, else put back."""
        now = clock.now()
        if not TREE_REBUILD or LINE_REGROW or self.tree_rebuilt or self.line_passes < 2 or deadline - now < TREE_REBUILD_MIN:
            return False
        self.tree_rebuilt = True
        build = self.build
        snap, before = build.snapshot(), build.score
        self.tree.reset()
        self.tree.grow(min(deadline, now + TREE_REBUILD_SHARE * (deadline - now)))
        self.tree.attributes_round()
        self.resolve()  # (a cleared passive may have been a condition's source)
        if build.score > before + EPSILON:
            return True
        build.restore(snap)
        self.tree.candidates.clear()  # (what-ifs ranked on the cleared tree)
        self.tree.round += 1
        return False

    def weapon_set_round(self) -> None:
        """Which skill groups weapon set 2 uses. Each other active group is tried on set 2 alone (PoB
        then calculates it with set 2's weapons and carries its buffs to the main skill) and stays
        there if the build scores better. Required skills set 1 can't use stay on set 2. Persistent
        skills never go there: their Spirit and buffs would come from the set not in use (the
        disabled-weapon-set grant, removed in 0.5.5). When the request makes weapon swap a must, the
        group that costs least goes there if none pays."""
        build = self.build
        must = WeaponSwap(self.ctx.request.weapon_swap) == WeaponSwap.MUST
        trials: list[tuple[float, int]] = []
        for group in build.groups():
            if not self.swappable(group):
                continue
            alone = not group["set1"]
            if alone and must and len(self.on_set_two()) == 1:
                continue  # the one skill that makes this a weapon-swap build stays there
            before = build.score
            build.set_group(index=group["index"], set1=alone, set2=True)
            if build.score > before + EPSILON:
                continue
            if not alone:
                trials.append((build.score - before, group["index"]))
            build.set_group(index=group["index"], set1=not alone, set2=True)
        if must and trials and not self.on_set_two():
            build.set_group(index=max(trials)[1], set1=False, set2=True)

    def swappable(self, group: dict) -> bool:
        if group["main"] or group["fromItem"] or not group["enabled"] or not group["gems"]:
            return False
        first = group["gems"][0]
        if first["support"] or first["name"] in self.swap_locked:
            return False
        return "Persistent" not in self.gem_types(first["name"])

    def on_set_two(self) -> list[str]:
        return [g["gems"][0]["name"] for g in self.build.groups()
                if g["enabled"] and not g["fromItem"] and g["gems"] and not g["set1"]]

    def gear_census(self) -> dict:
        """Where the gear stands at a task's end (SEARCH20: the 14 Nov 2b75d gate's best build, 15,518, wore no boots,
        gloves, rings or belt): the gear slots still empty (flask and charm slots aside), the slot the next full gear
        round starts at, the divines spent and the budget weight."""
        empty = [s for s in self.gear.slots if not is_flask_slot(s) and s not in CHARM_SLOTS
                 and self.build.equipped.get(s) is None]
        return {"empty": empty, "resume": self.gear.resume, "spent": round(self.build.spent(), 1),
                "lam": self.gear.lam}

    def kick(self) -> None:
        self.kicks += 1
        self.build.phase = "kick"
        kind = self.rng.choice(["tree", "tree", "tree", "supports", "gear"] + (["meta"] if self.meta_kickable() else []))
        self.kicked.add(kind)
        self.kick_kinds[kind] = self.kick_kinds.get(kind, 0) + 1
        self.sources.forget()
        if kind == "meta":
            self.kick_meta()
        elif kind == "tree":
            self.tree.kick(self.rng.randint(*KICK_TREE_POINTS))
        elif kind == "gear":
            self.gear.kick()
        else:
            group = self.build.main_group()
            if group is None or group["fromItem"]:
                return
            names = [g["name"] for g in group["gems"]]
            removable = [i for i, n in enumerate(names) if i > 0 and n not in self.gems.required_supports]
            if removable:
                del names[self.rng.choice(removable)]
                self.build.set_group(index=group["index"], gems=Gems.gem_list(names))
            self.gems.shortlist = None  # force a full sweep afterwards

    def meta_groups(self) -> list[dict]:
        """Meta groups a kick may take out: the meta round's own, or a seed's whose meta isn't required. Never one an
        item or notable grants (gear and the ascendancy round own those)."""
        from builder.search import models

        required = set(self.ctx.request.require.skills)
        return [g for g in self.build.groups() if g["enabled"] and not g["fromItem"] and g["gems"]
                and (g["label"] == META_LABEL or g["gems"][0]["name"] in models.META_GEMS)
                and g["gems"][0]["name"] not in required]

    def meta_kickable(self) -> bool:
        return bool(self.meta_groups())

    def kick_meta(self) -> None:
        """Slice 9: the meta round keeps the first meta group it finds for the rest of the search (it adds one only when
        the build has none), so a meta chosen early, before the gear and tree that feed another meta's Energy, was
        never revisited, nor was a seed's. This kick takes the group out; the meta steps then run in the same pass and
        choose again for the build as it is now (the run keeps the best build seen, so a worse choice is undone)."""
        for group in sorted(self.meta_groups(), key=lambda g: -g["index"]):
            self.build.remove_group(group["index"])
        self.gems.meta_shortlist = None

    def run(self, deadline: float, best: dict | None = None, escalate: bool = True,
            feasible_first: bool = False) -> None:
        """Block coordinate ascent until nothing improves, then kick; keep the best build seen.

        Requirements come first: after any pass that ends with a requirement still short and no
        closer than the pass before, its weight goes up (Objective.escalate); on the second raise
        the passive tree is cleared and regrown under the new weights, since a tree grown for damage
        rarely walks over to, say, the Mana nodes one swap at a time. `best` is a snapshot the result
        must not fall below (a seed's starting point). Nor does it end below the state it started from
        (a pass the deadline cuts short can stop inside a block), unless requirements were raised on
        the way: that trade of score for requirements is on purpose, and undoing it at every deadline
        kept gate 4's searches from ever reaching their resistance floors. `escalate` False keeps the
        weights as they are (finalize's short polish, where clearing the tree can only hurt).
        `feasible_first` (finalize): the score lets a build trade a small shortfall for enough damage,
        but the delivered build never ends further from feasible than it started."""
        start = self.build.snapshot()
        self.note_feasible()
        try:
            best, start = self._ascend(deadline, start, best, escalate)
        except OutOfTime:
            # Stopped inside a step, whose own bookkeeping (what's equipped, a trial under way) may not match the
            # engine: back to where the last step left the build, then the usual best-of below
            self.build.restore(self.boundary)  # (armed only once there is one)
            best, start = self._kept
            if self.current_step is not None:  # (M1: the line's next task runs the stopped step again)
                self.resume_step, self.current_step = self.current_step, None
        finally:
            self.build.stop_at = self.stop_at = None
            self.deadline, self.boundary = None, None
        kept = [snap for snap in (best, start) if snap is not None]
        if kept:
            top = max(kept, key=lambda snap: snap["score"])
            if top["score"] > self.build.score + EPSILON:
                self.build.restore(top)
        if feasible_first and kept:
            short = self.shortfall(self.build.stats)
            closest = min(kept, key=lambda snap: (round(self.shortfall(snap["stats"]), 6), -snap["score"]))
            if self.shortfall(closest["stats"]) < short - 1e-6:
                self.build.restore(closest)
        self.keep_feasible()
        self.ensure_required_items(after_search=True)  # (SEARCHD3: a required unique a round took out goes back)
        lost = "a required passive had gone from the tree during the search; it was put back"
        if self.tree.ensure_required():
            if lost not in self.problems:
                self.problems.append(lost)
            # (its path back may cost more points than are free: SEARCH8, Defect C)
            self.tree.trim_to_budget()
        self.resolve()

    def _ascend(self, deadline: float, start: dict | None, best: dict | None,
                escalate: bool) -> tuple[dict | None, dict | None]:
        """Search.run's loop: (best, start) as it leaves them; `self._kept` holds them for a stop (OutOfTime)."""
        self.deadline, self.boundary, self._kept = deadline, None, (best, start)
        self.task_start, self.tail_done = clock.now(), False  # (TASK_TAIL: the task's kept-back time, once a task)
        self.stop_at = deadline + max(STOP_GRACE_MIN, STOP_GRACE_SHARE * max(0.0, deadline - clock.now()))
        # (the line's last finished pass, carried across its tasks: Search.line_short; a task of its own without M1)
        last_short = self.line_short if PERSIST_LINES and self.line_short is not None else float("inf")
        while clock.now() < deadline:
            improved = self.blocks(deadline)
            self.note_feasible()
            if self.resume_step is not None:  # (stopped mid-pass, Search.blocks: its next task goes on with it and
                break                         # compares at its end)
            objective = self.build.objective
            short = shortfall([{"amount": a, "hard": h} for _, a, h in objective.deficits(self.build.stats)])
            # (a pass that finishes at the deadline is still compared: else a line whose pass takes about a task never
            # raises a weight; the raise costs one recalculation. Past the deadline nothing else runs: no tree reset
            # (M2's tree rebuild regrows the tree once per line), no kick)
            late = clock.now() >= deadline
            if escalate and short > 0 and short >= last_short - 1e-4 and objective.escalate(self.build.stats):
                self.escalations += 1
                start = None  # the search now trades score for requirements on purpose
                self._kept = (best, start)
                self.build.phase = "requirements"
                self.build.refresh()
                if best is not None:
                    best["score"] = objective.score(best["stats"])
                if self.escalations == 2 and not late:
                    self.tree.reset()
                self.kicked.update({"gear", "supports"})
                last_short = self.line_short = short
                continue
            last_short = self.line_short = short
            if late:
                break
            if improved:
                continue
            if best is None or self.build.score > best["score"] + EPSILON:
                if best is not None:
                    self.kicks_kept += 1
                best = self.build.snapshot()
                self._kept = (best, start)
            elif self.build.score < best["score"]:
                self.build.restore(best)
            self.kick()
        return best, start

    def include_in_full_dps(self) -> None:
        """Damaging non-main groups join Full DPS where they raise the goal (PoB counts only groups flagged for it)."""
        build = self.build
        self.play_notes.pop("pounce_hit", None)
        self.assumptions.rate_presses()  # (a seed's pressed groups may already be in Full DPS)
        for group in build.groups():
            if group["main"] or group["fromItem"] or not group["enabled"]:
                continue
            # Pounce is pressed once per Mark (about every 8 s), never nonstop, which is how Full DPS would count its slam:
            # it joins only once the Mark model has scaled its entry to that rate (Objective.entry_scales, MECH36), which
            # also brings in its carried wolves' hits; otherwise it's left out, labelled
            scales = getattr(build.objective, "entry_scales", None) or {}
            if (any(g["name"] in MARK_CARRIERS for g in group.get("gems") or [])
                    and not any(name in scales for name in MARK_CARRIERS)):
                self.play_notes["pounce_hit"] = {
                    "label": "Pounce's own hit: not counted*", "value": "0",
                    "basis": "the Mark model didn't rate Pounce's use here (another Mark is the one counted, or Pounce "
                             "can't be used), and PoB's Full DPS would count its slam as used nonstop; left out with its "
                             "wolves' hits (an undercount)",
                    "confidence": "theory"}
                if group["fullDPS"]:  # (a seed's PoB code may have it on)
                    build.set_group(index=group["index"], fullDPS=False)
                continue
            if group["fullDPS"]:
                continue
            before = build.objective.value(build.stats)
            build.set_group(index=group["index"], fullDPS=True)
            # a pressed skill counts at its real use, and takes its time from the main skill (MECH37: PoB counts it
            # used nonstop beside a nonstop main skill)
            self.assumptions.rate_presses()
            self.press_sync()  # (a chosen press rate is charged by press_model now, not Objective.press_busy)
            if build.objective.value(build.stats) <= before * (1 + 1e-9):
                build.set_group(index=group["index"], fullDPS=False)
                self.assumptions.rate_presses()
                self.press_sync()

    def include_granted(self) -> bool:
        """(SEARCH25: AUDIT36 A36-14's safer form, an UNDERCOUNT) An item- or tree-granted skill the player presses
        beside the main skill (a Voltaic Staff's Lightning Bolt, a spear's Spear Throw) never joins Full DPS in the
        rounds (include_in_full_dps skips granted groups), so it counted 0. SEARCH23's A36-14 let it join in the rounds
        and offered it in every press round: gate POE2T2371 on 2b75c came out 100,077, floors short, against 361,003
        with all floors met without it (POE2T2372, same draws), so it was dropped. Now it is judged only on the finished
        build, here, once (finalize_task): each granted group pressed_group calls pressed goes into Full DPS, the press
        round rates it (fit_buttons first: a granted skill in Full DPS is a button, constraints.item_skill_used, so it
        comes out again over the request's cap), and it stays only if the build scores higher and meets every floor it
        met before; else the build goes back (by hand: groups' flags, scoring state, buttons probe, taps). POE2_SEARCH_GRANTED_PRESS=0 turns it off. True if one stayed."""
        build = self.build
        granted = getattr(build.ctx, "all_gems", None)
        if not GRANTED_PRESS or not build.objective.full or not granted:
            return False
        kept = False
        for group in build.groups():
            if group["main"] or not group["fromItem"] or not group["enabled"] or group["fullDPS"]:
                continue
            if not pressed_group({**group, "fullDPS": True}, build.ctx.gems, granted):
                continue
            # (put back by hand, not by a snapshot: the finished build's exported XML failed to reload in PoB in the test
            # build, Item.lua's slotModList nil, POE2T2388; so the groups' flags, the scoring state, the buttons probe's
            # findings and the raised-shield taps go back as they were)
            flags = {g["index"]: (g["enabled"], g["fullDPS"]) for g in build.groups()}
            saved, score, short = self._scoring_state(), build.score, self.shortfall(build.stats)
            own = own_damage(build.call("stats", fields=["SkillDPS"]).get("SkillDPS"))
            used = set(getattr(build, gems_module.USED_ITEM_SKILLS, None) or ())
            taps = getattr(build.objective, "raised_taps", None)
            build.set_group(index=group["index"], fullDPS=True)
            self.assumptions.rate_presses()
            self.press_sync()
            self.press_round(float("inf"))
            self._rescore()
            joined = next((g["fullDPS"] for g in build.groups() if g["index"] == group["index"]), False)
            # (SEARCH27, POE2T4260: its own damage must rise, not only PoB's "Best Poison / Ignite DPS" pick. The Acrid
            # Wand's Decompose joined 2b75b's build for +8.7% (Best Poison DPS 2,144) while models' corpse note counted it
            # 0 uses a second on a lone boss: an OVERCOUNT)
            adds_own = rises(own, own_damage(build.call("stats", fields=["SkillDPS"]).get("SkillDPS")))
            if joined and adds_own and build.score > score + EPSILON and self.shortfall(build.stats) <= short + 1e-9:
                kept = True
                continue
            for g in build.groups():
                if g["index"] in flags and (g["enabled"], g["fullDPS"]) != flags[g["index"]]:
                    enabled, full = flags[g["index"]]
                    build.set_group(index=g["index"], enabled=enabled, fullDPS=full)
            self._put_scoring_state(saved)
            setattr(build, gems_module.USED_ITEM_SKILLS, used)
            if taps is not None:
                build.objective.raised_taps = taps
            self.assumptions.rate_presses()
            self.press_sync()
            self._rescore()
        return kept

    def note_feasible(self) -> None:
        """Remember the build if it meets every requirement and beats the best such build on the goal."""
        stats = self.build.stats
        if self.shortfall(stats) > 0:
            return
        value = self.build.objective.value(stats)
        if self.feasible is None or value > self.build.objective.value(self.feasible["stats"]) * (1 + 1e-9):
            self.feasible = self.build.snapshot()

    def keep_feasible(self) -> None:
        """A build that ends short of a requirement goes back to the best one seen that met them all. The score
        trades a small shortfall for enough damage, and the weights it trades with change as requirements escalate,
        so the best-scoring build can be short while the search passed through a feasible one (gate 4, 2b75c
        delivered 5 Cold resistance short). A requirement must never be broken for damage."""
        if self.feasible is not None and self.shortfall(self.build.stats) > 0:
            self.build.restore(self.feasible)

    def shortfall(self, stats: dict) -> float:
        """How far a build is from meeting every requirement (objective.shortfall)."""
        return shortfall([{"amount": a, "hard": h} for _, a, h in self.build.objective.deficits(stats)])

    # Results -----------------------------------------------------------------------------------

    def summary(self) -> dict:
        build = self.build
        return {
            "skeleton": self.skeleton.as_dict(), "label": self.skeleton.label, "score": build.score,
            "dps": build.objective.dps(build.stats), "main_dps": main_dps(build.stats),
            "value": build.objective.value(build.stats), "clear": build.objective.clear(build.stats)["score"],
            "goal": build.objective.goal.value,
            "deficits": [{"label": label, "amount": amount, "hard": hard}
                         for label, amount, hard in build.objective.deficits(build.stats)],
            "evals": build.evals, "commits": build.commits, "seconds": clock.now() - build.started,
            "kicks": self.kicks, "kicks_kept": self.kicks_kept, "kick_kinds": dict(self.kick_kinds), "skipped": dict(self.skipped),
            "passes": self.passes, "passes_done": self.passes_done, "problems": self.problems, "reverts": build.reverts,
            "restore_drift": restore_drift(build), "over_budget": build.over_budget, "gear": self.gear_census(),
            "line": {"passes": self.line_passes, "resume": self.resume_step},  # (M1: the line's pass, where it goes on)
            "condition_sources": list(self.sources.kept),
            "escalations": self.escalations, "multipliers": dict(build.objective.multipliers),
            "overrun": dict(self.overrun), "support_census": dict(self.gems.census),
            "main_gems": [g.get("name") for g in (build.main_group() or {}).get("gems", []) if g.get("name")],
            "timing": {k: {"seconds": round(v[0], 2), "evals": v[1], "gain": round(v[2], 4)} for k, v in self.timing.items()},
        }


def _without(search: Any, var: str, info: dict, on_stats: dict, on: float) -> tuple[float, dict, dict]:
    """One recalculation with a part-time effect off, then back on: (DPS without it / with it, the stats it changes
    (off - on), the stats without it). (Search.weigh_uptimes and follow_uptimes)"""
    objective = search.build.objective
    _switch(search, {var: info.get("off", False)})
    search.assumptions.apply()
    off_stats = dict(search.build.stats)
    _switch(search, {var: info["value"]})
    search.assumptions.apply()
    deltas = {k: number(v) - number(on_stats.get(k)) for k, v in off_stats.items()
              if isinstance(v, (int, float)) and number(v) != number(on_stats.get(k))}
    return max(objective.dps(off_stats), 0.0) / on, deltas, off_stats


# Support gems whose stats scale with the distance from you to the enemy, by their game text (gem_stat_descriptions.csd,
# build 26476662): choose_distance reads only the build's item and passive lines (sourceLines), so a ranged build with
# Lockdown was never tried at 2 m (COVE9). Found by scratch/search5/distance_supports.py: every English stat text in
# gem_stat_descriptions.csd naming a distance (32 stats) mapped to support gems through GrantedEffectStatSets /
# GrantedEffectStatSetsPerLevel and PoB's gems.json (24 supports). The other 19 of them measure distances between
# enemies, minions, Remnants or objects, or Knockback, not you to the boss (checked: their texts, listed in the script's
# output; second source: poe2db's Lockdown, Close Combat and Longshot pages give the same texts; UNVERIFIED that no
# later patch added another: rerun the script on a new game build).
SUPPORT_DISTANCE_TEXT = {
    "Lockdown": "Supported Skills have more Pin Buildup against enemies within 2 metres of you, scaling to no change "
                "at 12 metres",
    "Close Combat I": "Supported Skills deal more Attack Damage to enemies within 1 metre of you, scaling down to no "
                      "bonus at 3.5 metres or further",
    "Longshot I": "Supported Skills deal more Attack Damage to enemies further than 7 metres from you, scaling down to "
                  "no bonus at 3.5 metres or closer",
}
SUPPORT_DISTANCE_TEXT["Close Combat II"] = SUPPORT_DISTANCE_TEXT["Close Combat I"]
SUPPORT_DISTANCE_TEXT["Longshot II"] = SUPPORT_DISTANCE_TEXT["Longshot I"]


BEST_AILMENT = re.compile(r"^Best \w+ DPS$")  # (PoB's Full DPS ailment rows: the best skill's Ignite / Poison / Bleed)


def own_damage(entries: list[dict] | None) -> dict[str, float]:
    """PoB's SkillDPS rows a skill deals itself (name -> DPS above 0): not the "Best Ignite / Poison / Bleed DPS" rows,
    which take the best ailment of any Full DPS skill whatever that skill's own uses (SEARCH27)."""
    return {e.get("name") or "": float(e.get("dps") or 0.0) for e in entries or []
            if not BEST_AILMENT.match(e.get("name") or "") and float(e.get("dps") or 0.0) > 0}


def rises(before: dict[str, float], after: dict[str, float]) -> bool:
    """Whether some own-damage row is above its value before (a new row counts from 0)."""
    return any(dps > before.get(name, 0.0) * (1 + 1e-9) + 1e-9 for name, dps in after.items())


def support_distance_lines(groups: list[dict]) -> list[str]:
    """The distance texts (SUPPORT_DISTANCE_TEXT) of the support gems socketed in the build's enabled groups."""
    names = {g["name"] for grp in groups if grp.get("enabled", True) for g in grp["gems"]
             if g.get("enabled", True) and g.get("support")}
    return [f"{name}: {SUPPORT_DISTANCE_TEXT[name]}" for name in sorted(names) if name in SUPPORT_DISTANCE_TEXT]


FLASK_VAR = "flaskSlot:"  # a part-time flask or charm effect: "flaskSlot:<slot>:<item name>" (Search.part_time)


def _weigh_shared_flags(search: Any, effects: dict[str, tuple[float, dict]], on_stats: dict, on: float) -> float:
    """PoB sets UsingFlask while any flask slot is active and UsingCharm while any charm slot is (MECHC10), so with
    two part-time flasks (or charms) each one-off case above keeps the flag up, and the lines on it ("during any
    Flask Effect", "while you have an active Charm") counted whole. One more recalculation per such group, with the
    whole group off, puts the all-off case back (flasks.shared_flag_factor, which labels the slots as independent):
    the multiplier on weigh_uptimes' product, each slot's play note naming it."""
    if on <= 0:
        return 1.0
    slots = {var[len(FLASK_VAR):].split(":", 1)[0]: var for var in effects if var.startswith(FLASK_VAR)}
    factor = 1.0
    for flag, group in flasks.shared_flag_groups(slots).items():
        names = [slots[slot] for slot in group]
        if any(var not in search.uptime_cache for var in names):
            continue
        deltas = _all_off(search, {var: effects[var][1] for var in names}, on_stats)
        off = {**on_stats, **{k: number(on_stats.get(k)) + d for k, d in deltas.items()}}
        none_ratio = max(search.build.objective.dps(off), 0.0) / on
        share, note = flasks.shared_flag_factor([effects[var][0] for var in names],
                                                [search.uptime_cache[var][0] for var in names], none_ratio)
        factor *= share
        for var in names:
            row = search.play_notes.get(f"uptime:{var}")
            if row is not None and note:
                row["basis"] += f". {flag}, shared with {len(names) - 1} other slot(s): {note}, x{share:.3f} on "                                     f"their product"
    return factor


# A ledger condition the fight holds for part of it (Assumptions.ledger_part_time: Adaptive Skin's elements the boss hit
# you with Recently, AUDIT25 A25-48): counted at its share inside PoB's stats (Multiplier:LedgerShare<id>, its `share:`
# lines), so its weighing factor is 1 ("in_stats": the share isn't counted twice); _switch turns it off through
# Assumptions.ledger_switch, and floor_without reads the floors with it off (they count an effect only at full uptime).
LEDGER_VAR = "ledger:"


def _part_time(search: Any) -> dict[str, tuple[float, dict]]:
    """The effects on for only part of the fight: var -> (share, info). The conditions' (conditions.uptimes, the active
    ones) and the flask and charm slots up between gear.FLASK_ON (7%) and gear.FLASK_WHOLE (99.5%) of the fight
    (Gear.part_time, FLASK_VAR; MECHC5's spec), switched through _switch. (A search without gear: the conditions' only.)"""
    conditions = search.conditions
    out = {var: (up, conditions.active[var]) for var, up in conditions.uptimes.items() if var in conditions.active}
    gear = getattr(search, "gear", None)
    for slot, (up, name) in (gear.part_time() if gear is not None else {}).items():
        out[f"{FLASK_VAR}{slot}:{name}"] = (up, {"label": f"{name} ({slot})", "value": True, "off": False})
    for name, up in sorted((getattr(getattr(search, "assumptions", None), "ledger_part_time", None) or {}).items()):
        if up > 0:
            out[f"{LEDGER_VAR}{name}"] = (up, {"label": f"{name} (a ledger condition, counted at its share)",
                                               "value": True, "off": False, "in_stats": True})
    return out


def _uptime_floor_rows(search: Any, var: str, up: float, on_stats: dict, off_stats: dict,
                       deltas: dict) -> list[tuple[str, float, float, float]]:
    """weigh_uptimes' defence rows for one part-time effect: (floor label, with it, without, time-weighted), the
    time-weighted figure u x with + (1 - u) x without. A flask slot PoB counts in its own regeneration while it's up
    (kept at full: Enduring Elixirs' Life flask, Melting Maelstrom's own Mana line; flasks.pob_counts_whole's note,
    MECHC25) holds PoB's whole flask recovery AND ours (Gear.recovery_stats, which doesn't follow the slot's flag) with
    it on, and ours alone with it off, so that weighting counted its recovery twice (MECHC25; SEARCH22). Its rows are:
    with it, PoB's alone (ours taken off); without, none from it (ours taken off too); time-weighted, ours alone (its
    recovery at the rate its charges allow: what the floors read) where ours moves the floor, else the weighting."""
    objective = search.build.objective
    plain = [(p.label, number(on_stats.get(p.stat)), number(on_stats.get(p.stat)) + deltas[p.stat])
             for p in objective.penalties if p.stat in deltas]
    ours, field = _pob_counted_flask(search, var, deltas)
    if ours <= 0:
        return [(label, a, b, up * a + (1 - up) * b) for label, a, b in plain]
    from builder.search.objective import with_boss_recovery, with_life_es

    def less_ours(stats: dict) -> dict:
        return with_life_es(with_boss_recovery({**stats, field: number(stats.get(field)) - ours}))

    up_stats, down_stats = less_ours(on_stats), less_ours(off_stats)
    rows = []
    for p in objective.penalties:
        if p.stat not in deltas:
            continue
        a, c, b = number(up_stats.get(p.stat)), number(down_stats.get(p.stat)), number(off_stats.get(p.stat))
        rows.append((p.label, a, c, b if abs(b - c) > 1e-9 else up * a + (1 - up) * c))
    return rows


def _pob_counted_flask(search: Any, var: str, deltas: dict) -> tuple[float, str]:
    """(our recovery a second, its field) for a part-time Life / Mana flask slot (FLASK_VAR) whose recovery PoB also adds
    to its own regeneration while the slot is on: the effect kept when the pool fills (the build's Enduring Elixirs
    flag for Life, flasks.Charges.life_effect_kept; the flask's own Mana line, flasks.KEPT_AT_FULL) and the slot's on /
    off recalculation moving that pool's RegenRecovery (measured, not read off the text alone). (0.0, "") otherwise."""
    gear = getattr(search, "gear", None)
    if gear is None or not var.startswith(FLASK_VAR):
        return 0.0, ""
    slot = var[len(FLASK_VAR):].split(":", 1)[0]
    pool, option = flasks.slot_pool(slot), search.build.equipped.get(slot)
    if pool not in flasks.RECOVERY_FIELDS or option is None or number(deltas.get(f"{pool}RegenRecovery")) >= 0:
        return 0.0, ""
    charges = getattr(gear, "charges", None)
    kept = (getattr(charges, "life_effect_kept", False) if pool == "Life"
            else flasks.KEPT_AT_FULL["Mana"] in (option.raw or "").lower())
    if not kept or charges is None:
        return 0.0, ""
    ours = flasks.recovery_per_second(gear.flask_info([option.raw])[0], charges, pool, option.raw)
    return ours, flasks.RECOVERY_FIELDS[pool]


def _switch(search: Any, values: dict[str, Any]) -> None:
    """Set part-time effects on or off: PoB config vars, and flask / charm slots (FLASK_VAR) through the slot's active
    flag (the item stays equipped; its recovery, Gear.recovery_stats, doesn't depend on the flag: _uptime_floor_rows)."""
    config = {}
    for var, value in values.items():
        if var.startswith(LEDGER_VAR):
            switch = getattr(search.assumptions, "ledger_switch", None)
            if switch is None:
                switch = search.assumptions.ledger_switch = {}
            switch[var[len(LEDGER_VAR):]] = bool(value)
        elif var.startswith(FLASK_VAR):
            slot = var[len(FLASK_VAR):].split(":", 1)[0]
            option = search.build.equipped.get(slot)
            if option is not None:
                search.build.equip(slot, option, active=bool(value))
        else:
            config[var] = value
    if config:
        search.build.set_config(config)


def _all_off(search: Any, infos: dict[str, dict], on_stats: dict) -> dict:
    """One recalculation with every given part-time effect off, then back on: the stats that change (off - on).
    The order the effects are switched in (a frozenset's, floor_without) can't change the result: each config var writes
    its own key and bridge.setConfig rebuilds the mod list once after all of them; each flask slot and ledger switch is
    its own key; and the stats are read after the last recalculation (SEARCH17, PERF12's open question)."""
    _switch(search, {var: info.get("off", False) for var, info in infos.items()})
    search.assumptions.apply()
    off_stats = dict(search.build.stats)
    _switch(search, {var: info["value"] for var, info in infos.items()})
    search.assumptions.apply()
    return {k: number(v) - number(on_stats.get(k)) for k, v in off_stats.items()
            if isinstance(v, (int, float)) and number(v) != number(on_stats.get(k))}


# A gate that compares code, not PC load (SEARCH19; the manager's 14 Nov 02:40 item): with the payload's "cpu_clock" set
# (Run's cpu_clock, gate4 --cpu), a worker's search time is its own processor time (time.process_time: the worker's Python
# and its in-process PoB, builder.pob.lua), so a busy PC stretches the run's wall time but not the work each task does.
# The search's deadlines and hard stop all read builder.clock.now, which this swaps in the worker process only.
CPU_CLOCK = "cpu_clock"

# (SPEED2, on loan from SEARCH; the manager's 10:40 ruling, 23 Nov; OFF by default: POE2_SEARCH_WORK_CLOCK=1, gate4 --work)
# Why two gates on one seed and one commit differ: a processor second buys a different amount of work on a loaded PC, so
# each task stops at a different point (2b75d: 118,027 and 97,593, SEARCHB6's gate-6327 / 6331; SPEED1 report 6b). On this
# clock a task's time is a fixed cost per engine call that reached Lua, so the same calls stop at the same point.
# The costs are each method's mean processor seconds per call (per what-if case for the delta methods), measured in
# --cpu 600 gates on 2b75b, c, d, e (seed 1, default switches; POE2T7842-7845, SPEED2's scratch fit_costs.py) and scaled by
# 1.024 so the Python time between calls is spread over them. The first table (two hooked profiles) charged 1.5-2.4x
# too much, unevenly (2b75d: supports got 2,127 evaluations against 27,911 on --cpu). LOW: they set how far a gate goes and
# each step's share, not any score.
WORK_CLOCK = os.environ.get("POE2_SEARCH_WORK_CLOCK", "0") == "1"
WORK_COST = {"setAssumptions": 0.0905, "setGroup": 0.089, "equip": 0.0672, "setConfig": 0.089, "useRates": 0.0071,
             "loadXml": 0.2912, "allocate": 0.0974, "monsterRates": 0.5919, "activeSkills": 0.00059,
             "removeGroup": 0.0885, "sourceLines": 0.0013, "minionSkillHit": 0.0127, "exportXml": 0.0284,
             "stats": 0.00013, "facts": 0.0018, "groups": 0.00047, "equipped": 0.0033, "frontier": 0.0086,
             "getConfig": 0.0002, "setAttributes": 0.0691, "setMonster": 0.0922, "syncMonsters": 0.0172,
             "attributeNodes": 0.00024, "setRates": 0.0079, "resetConfig": 0.087, "allocatedNodes": 0.0013,
             "jewelTraits": 0.0312, "newBuild": 0.0856, "applyPassiveCorrections": 0.0167, "setClass": 0.0488,
             "conqueredNodes": 0.00078, "chargeGain": 0.00069, "shockMagnitude": 0.00012, "setLevel": 0.0336,
             "gemRequirements": 0.00079, "flaskData": 0.0048, "supportCandidates": 0.0012, "pointBudget": 0.00012,
             "usageRules": 0.0067, "radiusJewels": 0.00051, "radiusModel": 0.00077, "cullSplit": 3e-05,
             "jewelSockets": 0.00031, "questOptions": 0.00057, "characterRunes": 0.00047, "summary": 0.00067,
             "unsupportedInBuild": 0.0, "cursesInflicted": 0.0}
WORK_COST_PER_CASE = {"itemDeltas": 0.0086, "nodeDeltas": 0.008, "gemDeltas": 0.0074}
WORK_COST_OTHER = 0.004751  # (any other method that reaches Lua: the median call)
WORK_READ = 0.0001  # (each clock reading, so a loop that calls no engine still reaches its deadline)


class WorkClock:
    """A worker's search time counted in work done, not seconds passed."""

    def __init__(self) -> None:
        self.spent = 0.0

    def now(self) -> float:
        self.spent += WORK_READ
        return self.spent

    def charge(self, method: str, cases: int) -> None:
        self.spent += (WORK_COST_PER_CASE[method] * cases if method in WORK_COST_PER_CASE
                       else WORK_COST.get(method, WORK_COST_OTHER))

    def watch(self, engine: Any) -> None:
        real = engine.call

        def call(method: str, **args: Any) -> Any:
            before = engine.lua.calls
            try:
                return real(method, **args)
            finally:
                if engine.lua.calls != before:  # (a read the engine's cache answered costs nothing)
                    self.charge(method, len(args.get("cases") or ()))

        engine.call = call


def use_cpu_clock(payload: dict, engine: Any = None) -> None:
    if not payload.get(CPU_CLOCK):
        return
    if WORK_CLOCK and engine is not None:
        if not isinstance(getattr(clock.now, "__self__", None), WorkClock):
            work = WorkClock()
            work.watch(engine)
            clock.now = work.now
        return
    if clock.now is not time.process_time:
        clock.now = time.process_time


def optimize_task(engine: Any, report: Any, payload: dict, skeleton: dict, seconds: float, seed: int = 0,
                  state: dict | None = None, goal: str | None = None, full: bool = False, curses: bool = False,
                  last: bool = False) -> dict:
    """`goal` overrides the request's (a balanced run's boss-DPS searches); the result's `value` is
    on that goal, so the run ranks results on its own goal from `dps` and `clear`. `full`: scored on PoB's Full
    DPS, as the delivered build is, the damaging non-main groups included (the run's focus stage)."""
    use_cpu_clock(payload, engine)
    deadline = clock.now() + seconds
    wall, left_out = time.perf_counter(), clock.paused()
    search = Search(engine, report, payload, skeleton, seed, goal=goal, full=full)
    start = floor = None
    if state is not None:
        search.load(state)
        if full:
            search.include_in_full_dps()
        if state.get("restarted"):
            # A line restarted from the best line's state (run.restart_behind) starts with a kick from its own rng:
            # the search is deterministic from a state until its first kick, which a 235 s round seldom reaches, so
            # copies did the same work (2b75b POE2T649 and POE2T654: all five round-3 tasks the same evals and DPS).
            # It never ends below the state it was given
            floor = search.build.snapshot()
            search.kick()
    elif search.skeleton.seeded:
        # The search never ends below the PoB code it started from (PLAN.md §8, gate 4)
        search.setup_seed()
        search.settle_still()  # (SEARCH29: the start is the settled build, MECHC40's item (b))
        start = search.build.snapshot()
    else:
        search.setup()
    if clock.now() > deadline:  # setup doesn't stop for the deadline: say so if it took the task's time
        search.overrun["setup"] = round(clock.now() - deadline, 2)
    from builder.pob.pool import Cancelled

    try:
        if curses:  # (SEARCH29, the manager 19 Nov 06:50: the seeded curse trial once per RUN: run.py flags one task)
            search.gems.curse_task = True
            search.step("curses", search.gems.curse_round, deadline)
        regrow = LINE_REGROW and last  # (SEARCH35: the line's last focus task; run.py's `last`)
        search.run(deadline - (LINE_REGROW_SHARE * seconds if regrow else 0.0), best=start or floor)
        if regrow:
            search.step("line regrow", search.line_regrow, deadline)
    except Cancelled:
        kept = start or floor
        if kept is not None and kept["score"] > search.build.score:
            search.build.restore(kept)
        search.keep_feasible()
    summary = search.summary()
    if getattr(search, "line_regrow_rows", None) is not None:
        summary["line_regrow"] = search.line_regrow_rows
    # The task's wall time and what its clock left out as suspended (the queue's holds), to check the budget held
    summary["wall"] = round(time.perf_counter() - wall, 2)
    summary["ran"] = [round(time.time() - summary["wall"], 2), round(time.time(), 2)]  # when, across processes
    summary["left_out"] = round(clock.paused() - left_out, 2)
    if start is not None:
        objective = search.build.objective
        summary["seed_start"] = {"dps": objective.dps(start["stats"]), "value": objective.value(start["stats"]),
                                 "score": objective.score(start["stats"]),
                                 "deficits": [label for label, _, _ in objective.deficits(start["stats"])],
                                 "notes": search.seed_notes}
    return {**summary, "state": search.state()}


def boss_config(request: Any) -> dict:
    """resetConfig's `boss` argument when the request names a boss (MECH50's presets, builder/search/bosses.py): {} for
    none, and while bosses.py isn't in this tree, so the bridge writes its own default (the Arbiter of Ash once 95eceb1
    lands, PoB's otherwise)."""
    name = getattr(request, "boss", None)
    if not name:
        return {}
    try:
        from builder.search import bosses
    except ImportError:
        return {}
    from builder.search import boss_hits  # (its hits per damage type, MECHC6; resetConfig's default is the default's)

    return {"boss": bosses.config_vars(name), "bossHits": boss_hits.config_vars(name)}


def boss_stats(request: Any) -> dict:
    """The request's boss for the models that read it from the stats (boss_hits.boss_name's `_boss`: its hits per
    damage type, red-flash share): {} when it names none (they use the default preset)."""
    name = getattr(request, "boss", None)
    return {"_boss": name} if name else {}


MAX_EXTRA_POINTS = 2  # (R54-7: the Martyr of the First Edict's and Olroth's Boon's, one each)


def seed_task(engine: Any, report: Any, payload: dict) -> dict:
    """The skeleton of the request's PoB code, or why it can't be one."""
    ctx = context_for(payload)
    extra = ctx.request.extra_passive_points
    engine.call("setExtraPoints", points=extra, weaponSetPoints=ctx.request.extra_weapon_set_points)
    engine.call("loadXml", xml=codes.decode(ctx.request.seed_code))
    facts = engine.call("facts", fields=[])
    skeleton, notes = seed_skeleton(ctx, facts)
    if skeleton is not None:  # (the manager 23 Nov 20:12: trimming a real character's tree makes it another build)
        budget = engine.call("pointBudget")
        over = budget["used"] - budget["passives"]
        if over > 0 and extra + over <= MAX_EXTRA_POINTS:
            skeleton = replace(skeleton, import_points=over)
            notes.append(f"uses {extra + over} passive point(s) beyond the standard budget (the Martyr of the First "
                         "Edict / Olroth's Boon, if this character has them)")
    return {"skeleton": skeleton.as_dict() if skeleton else None, "notes": notes}


def screen_task(engine: Any, report: Any, payload: dict, skeletons: list[dict], seconds_each: float = 3.0) -> list[dict]:
    """Main-skill DPS with a starting weapon and greedy supports, no tree: a cheap shortlist."""
    use_cpu_clock(payload, engine)
    from builder.pob.pool import Cancelled

    results = []
    for skeleton in skeletons:
        search = Search(engine, report, payload, skeleton, 0)
        try:
            main = search.skeleton.main_skill
            blocked = [] if main == search.ctx.request.main_skill else search.usage.blockers(main)
            if main in GLORY_SKILLS:  # (its uses are rated from the build's Glory: Assumptions.glory_main)
                blocked = [b for b in blocked if "glory" not in b.lower()]
            if blocked:  # PoB would calculate it at will; only a request that names it gets it anyway
                results.append({"skeleton": skeleton, "dps": 0.0, "value": 0.0, "score": float("-inf"),
                                "error": "can't be used at will: " + "; ".join(blocked)})
                continue
            search.setup(quests=False)
            search.gear.gear_round(clock.now() + seconds_each)
            search.gems.optimize_supports(clock.now() + seconds_each, rounds=4)
            build = search.build
            results.append({"skeleton": skeleton, "dps": main_dps(build.stats), "value": build.objective.value(build.stats),
                            "score": build.score})
        except Cancelled:
            break
        except Exception as error:  # a skill PoB can't set up is simply not shortlisted
            results.append({"skeleton": skeleton, "dps": 0.0, "value": 0.0, "score": float("-inf"), "error": str(error)[:300]})
    return results


def restore_drift(build: Build) -> dict:
    """Restores and how far their scores came back from their snapshots' (Build.restore): drifted counts those off by
    more than EPSILON, sum and worst in ln units."""
    drifts = build.drifts
    return {"restores": build.restores, "drifted": len(drifts), "sum": round(sum(drifts), 4),
            "worst": round(min(drifts, key=lambda d: (d > 0, -abs(d))), 4) if drifts else 0.0}


REAPPLY_ROUNDS = 2  # settles of the finished, reloaded build
REAPPLY_TOLERANCE = 0.01  # a DPS move above 1% from the same state applied again is flagged


# (SEARCH41; COV59's find via the manager 25 Nov 21:30, an OVERCOUNT in the gate reference) Flask recovery and flask /
# charm effects reach the score only through Gear.sync_flasks, which reads the search's own item record (build.equipped)
# that only setup_seed and the gear rounds fill. A build loaded from a code and scored straight away (gate4's
# fair_reference and refloor, fresh_score) had an empty record: its flasks and charms counted as PoB loaded them, on
# even where the flask model gives them no charges in the fight (COV59's calibration: b4 7.42M -> 7.23M once its 0-charge
# flasks and charms were off). adopt_code_items fills the record from the code's own items, as setup_seed's item loop
# does but without its bans, prices or budget (the build is scored as it stands), and syncs the flasks.
# POE2_FAIR_FLASKS=0: the old path (no record, no sync), for an A/B reading.
def adopt_code_items(search: "Search") -> list[str]:
    """Fill search.build.equipped from the loaded code's items and sync the flasks (see above). The slots adopted."""
    if os.environ.get("POE2_FAIR_FLASKS", "1") == "0":
        return []
    build, ctx = search.build, search.ctx
    raws = build.call("equipped")["slots"]
    kept: dict[str, ItemOption] = {}
    for item in build.call("facts", fields=[])["items"]:
        raw = raws.get(item["slot"])
        if raw is None:
            continue
        base = ctx.bases_by_name.get(item["base"]) or {}
        kept[item["slot"]] = ItemOption(item["name"], item["base"], item["type"], base.get("subType"), raw,
                                        item["rarity"] in ("UNIQUE", "RELIC"), 0.0, "yours (from the code)",
                                        (base.get("req") or {}).get("level") or 0, rare=item["rarity"] in ("RARE", "MAGIC"))
    build.equipped = kept
    search.gear.sync_flasks()
    return sorted(kept)


def fresh_score(engine: Any, payload: dict, skeleton: dict, xml: str) -> float:
    """The exported build scored from scratch the search's way, in a new Search on this engine: its code loaded, the boss
    preset's config, then the steps finalize_task runs on a finished build (gate4.fair_reference's, which scores the
    references the same way). Nothing of the finished search's state (its assumption mods, conditions, rates) is kept."""
    fresh = Search(engine, None, payload, skeleton, 0, full=True)
    fresh.distance_settles = DISTANCE_SETTLES
    build = fresh.build
    build.call("loadXml", xml=xml)
    build.call("resetConfig", **boss_config(fresh.ctx.request))
    build.boss_stats = boss_stats(fresh.ctx.request)
    build.refresh()
    adopt_code_items(fresh)  # (SEARCH41: the flasks as the search scores them)
    for _ in range(4):
        if not fresh.settle():
            break
    fresh.conditions_round()
    fresh.choose_play()
    fresh.resolve()
    fresh.press_round(float("inf"))
    fresh.weigh_life()  # (SEARCH41: LIFE_AFTER_PRESS)
    fresh.weigh_uptimes()
    if fresh.gems.refresh_infused():
        fresh.weigh_uptimes()
    return build.objective.dps(build.stats)


def restore_config(build: Any, before: dict) -> list[str]:
    """(SEARCH33) Put back the config inputs a code reload dropped or changed (PoB exports an input at its own default as
    a Placeholder, which loads as unset): the names restored."""
    now = build.call("getConfig")["input"]
    lost = {k: v for k, v in before.items() if now.get(k) != v}
    if lost:
        build.set_config(lost)
    return sorted(lost)


def reapply_drift(search: Any, fresh: Callable[[], float] | None = None) -> dict:
    """(SEARCH32; the manager's 09:35 ruling, 20 Nov) Does the delivered number hold when the same build is applied again?
    (1) the finished, reloaded build settled REAPPLY_ROUNDS more times (Search.settle) with nothing changed; (2) `fresh`:
    its exported code scored from scratch (fresh_score). Either moving more than REAPPLY_TOLERANCE off the delivered DPS is
    flagged: a model fed on its own last output or on state the search carried. 2b75b, Explosive Transmutation with The
    Covenant's Life Remnants as its only Remnant source (its rate read from the explosion's own last rate, 1 / (3 + 1 / r)
    per apply): searched 12,219, delivered 207, the same code scored fresh 28,581 (gates POE2T5406 / 5408, probes
    POE2T5412-5413, 5421). The delivered numbers are left as they are (this runs last)."""
    build = search.build
    delivered = build.objective.dps(build.stats)
    after, again = [], None
    try:
        for _ in range(REAPPLY_ROUNDS):
            search.settle()
            after.append(build.objective.dps(build.stats))
        if fresh is not None:
            again = fresh()
    except Exception as error:  # (a check, never a reason to lose the finished build)
        return {"delivered": delivered, "after": after, "fresh": again, "flagged": False, "error": str(error)[:200]}
    seen = after + ([again] if again is not None else [])
    worst = max((abs(d / delivered - 1) if delivered > 0 else (1.0 if d > 0 else 0.0) for d in seen), default=0.0)
    return {"delivered": delivered, "after": after, "fresh": again, "worst": worst, "flagged": worst > REAPPLY_TOLERANCE}


def why_items_safe(gear: Any) -> tuple[dict, str | None]:
    """(hotfix 23 Nov) The per-slot "why this item" table and any error making it: an explanation never costs the run
    its build (SPEED2's POE2T7828: a KeyError here delivered 0 DPS and no code)."""
    try:
        return gear.why_items(), None
    except Exception as error:
        return {}, f"{type(error).__name__}: {error}"


def polish_finished(search: "Search", seconds: float, trace: list) -> bool:
    """(SEARCHB10's hook, landed by SEARCH42 from branch searchb10-hooked 096af924; POE2_SEARCH_POLISH, OFF by default)
    The finished build's anoint / rune polish over every equipped slot (Gear.polish): the full gear round's per-slot
    work reaches only its first 1-2 slots in a gate (SPEED4, POE2T7890), and 2b75b's delivered Amulet had no anoint
    (Lasting Trauma x1.126, POE2T7898). Its own time past finalize's (gear.polish_seconds), the uptimes weighed first
    and after each kept change; finalize_task calls it after the final regrow, before the open sockets. A tree without
    SEARCH-B's Gear.polish (80c088c9) or with the switch off: nothing runs. True if it ran."""
    from builder.search import gear as gear_module

    polish_on, polish_seconds = getattr(gear_module, "polish_on", None), getattr(gear_module, "polish_seconds", None)
    if polish_on is None or polish_seconds is None or not polish_on():
        return False
    try:
        search.weigh_uptimes()
        search.gear.polish(clock.now() + polish_seconds(seconds), reweigh=search.weigh_uptimes)
        trace.append(["gear polish", round(search.build.score, 4)])
    except Cancelled:
        pass
    return True


def finalize_task(engine: Any, report: Any, payload: dict, skeleton: dict, state: dict, seconds: float,
                  regrow: float = 0.0) -> dict:
    """Switch to Full DPS, include damaging non-main groups in it, polish briefly, then export
    the build and reload the exported code to prove it reproduces the same numbers."""
    from builder.pob.pool import Cancelled

    use_cpu_clock(payload, engine)
    deadline = clock.now() + seconds
    search = Search(engine, report, payload, skeleton, 1, full=True)
    search.distance_settles = DISTANCE_SETTLES  # (the finished build: its distance settled to rest)
    search.load(state)
    search.resume_step, search.line_passes = None, 0  # (its polish starts a sweep pass, as before M1)
    search.tree_rebuilt = True  # (M2: no tree rebuild in finalize's short polish)
    build = search.build
    build.phase = "finalize"
    # The score after each finalize step (ln DPS-like), in the result: 2b75e's delivered build has come out 8-17% under
    # the best searching line in every gate run (2.60M -> 2.15M, POE2T650), and this tells a polish loss from a truer
    # number (Full DPS, exact uptimes)
    trace: list[list] = [["loaded", round(build.score, 4)]]
    search.include_in_full_dps()
    # The press rates on Full DPS (a pressed group's own damage counts now), then the groups again: one with no chosen
    # rate added nothing and stayed out
    if search.step("press rates", search.press_round, deadline):
        search.include_in_full_dps()
    trace.append(["full dps", round(build.score, 4)])
    # A loaded state has no uptime weighting yet (objective.uptime_factor 1.0): 2b75e (POE2T653) loaded at 14.43 against
    # its searched 14.27, so the Full DPS supports sweep below ranked supports with every partial-uptime effect at full
    # uptime. Weigh them first; the polish's conditions rounds refresh them as before
    search.weigh_uptimes()
    trace.append(["weighed", round(build.score, 4)])
    # The search's support shortlists were ranked on the main skill's DPS, which never counts a skill a support grants
    # (Deadly Resolve's slash): with them, finalize spent its polish on shortlist moves and its sweep came too late or
    # not at all. 2b75d, 8 Nov: the same build went 264,520 -> 575,512 on Close Combat II + Deadly Resolve when the
    # sweep ran (POE2T207) and stayed at 264,520 when it didn't (POE2T231-232). So supports are swept on Full DPS first.
    search.gems.shortlist = search.gems.meta_shortlist = None
    search.gems.granted_shortlists.clear()
    try:
        search.conditions.choose_quests()
        if deadline - clock.now() >= FINALIZE_SWEEP_MIN:  # (a sweep isn't cut by the deadline: 12-25 s on gate 4)
            before = build.snapshot()
            search.step("supports", search.gems.optimize_supports, deadline, 12, True)
            # Never below where it started: the Full DPS sweep's kept moves ended 2-3% under it in four gate runs
            # (2b75b POE2T654/656/658/659: 13.2314 -> 13.2085, 13.0386 -> 13.0177, 13.2299 -> 13.2008, 13.3295 ->
            # 13.3112), and the polish didn't win it back in three
            if build.score < before["score"] - EPSILON:
                build.restore(before)  # (the snapshot carries the search's scoring state: Build.snapshot_state)
                trace.append(["supports restored", round(before["score"], 4)])
            trace.append(["supports", round(build.score, 4)])
        search.run(deadline, escalate=False, feasible_first=True)
        trace.append(["polish", round(build.score, 4)])
    except Cancelled:
        pass
    if LATE_SWEEP:  # (SEARCH42, OFF by default: the sweep-only steps once more on the finished build)
        try:
            search.weigh_uptimes()
            gained = search.late_sweep(clock.now() + min(LATE_SWEEP_SECONDS, seconds))
            trace.append(["late sweep" + (f" ({', '.join(gained)})" if gained else ""), round(build.score, 4)])
        except Cancelled:
            pass
    # (SEARCH40) a delivered build never keeps unspent ascendancy points: after the polish (whose passes may spend them),
    # before the final regrow (which then regrows on the spent allocation)
    try:
        if search.spend_ascendancy():
            trace.append(["ascendancy points", round(build.score, 4)])
    except Cancelled:
        pass
    # (SEARCH35; SEARCHB2's Tree.final_regrow, the manager's 17:48) the tree regrown on the finished gear, the floors
    # never given up: the uptimes weighed first (the floors' part-time deltas belong to the tree they were measured on:
    # SEARCHB1's regrow probe read every regrow short on Physical max hit without it), and after each variant. Its own
    # time (run.py FINAL_REGROW_SHARE, `regrow`), not finalize's polish
    regrow_rows: list[dict] = []
    from builder.search.run import FINAL_REGROW, joint_regrow_on

    joint = joint_regrow_on()
    share = regrow / max(1, int(joint) + int(FINAL_REGROW))  # (run.plan gives each regrow step that is on its time)
    # (SEARCH36; the manager's 11:50, SEARCHB3's joint move via SEARCHB4, its exact call) the tree and the gear regrown
    # together under the floors, just before the final regrow; OFF by default (POE2_SEARCH_JOINT_REGROW=1); its row has
    # final_regrow's keys and joins the same result list
    if joint and share > 0:
        try:
            search.weigh_uptimes()
            search.step("joint regrow", lambda d: regrow_rows.append(
                search.tree.joint_regrow(d, search.gear.gear_round, reweigh=search.weigh_uptimes)), clock.now() + share)
            trace.append(["joint regrow", round(build.score, 4)])
        except Cancelled:
            pass
    if FINAL_REGROW and share > 0:
        try:
            search.weigh_uptimes()
            regrow_rows += search.tree.final_regrow(clock.now() + share, reweigh=search.weigh_uptimes)
            trace.append(["final regrow", round(build.score, 4)])
        except Cancelled:
            pass
    polish_finished(search, seconds, trace)  # (SEARCHB10's hook: POE2_SEARCH_POLISH, OFF by default)
    # (SEARCH34; the user, 20 Nov: an empty support socket is a red flag to justify by score) every open socket of the
    # MAIN group gets real trials of its legal supports, and a support needing an exact count (Uhtred's Augury) the group
    # without it filled; empty stays only when it scores higher (Gems.fill_open). Its own FILL_SECONDS past whatever is
    # left (the manager's 06:58: "up to about 90 s extra" for the main group's fill), at most 1.5 x finalize's own time (a
    # short run's finalize stays short: a 600 s gate's is 60 s, so 90); what it can't try is said in the result
    try:
        if search.step("open sockets", search.gems.fill_open,
                       clock.now() + min(gems_module.FILL_SECONDS, FILL_FINALIZE_RATIO * seconds)):
            trace.append(["open sockets", round(build.score, 4)])
    except Cancelled:
        pass
    search.choose_play()
    trace.append(["play", round(build.score, 4)])
    search.resolve()
    trace.append(["resolve", round(build.score, 4)])
    if search.feeder_round(clock.now() + FEEDER_SECONDS):  # (SEARCH40: a Twister build's Whirlwind feeder press)
        trace.append(["feeder press", round(build.score, 4)])
    search.press_round(float("inf"))  # (the finished build's rates and their play guide rows)
    trace.append(["press rates", round(build.score, 4)])
    if search.include_granted():  # (SEARCH25, A36-14: a granted pressed skill, judged on the finished build only)
        trace.append(["granted presses", round(build.score, 4)])
    search.weigh_life()  # (SEARCH41: the Low Life factor on the pressed build, LIFE_AFTER_PRESS)
    search.weigh_uptimes()  # exact for the finished build
    trace.append(["uptimes", round(build.score, 4)])
    if search.final_choices():  # (SEARCH39: the stat set and the distance chosen again in the finished state)
        trace.append(["choices", round(build.score, 4)])
    # (the user, 14 Nov: an empty slot gets the cheapest item that adds defence or movement speed, score, floors and
    # budget no worse: Gear.fill_empty)
    if search.gear.fill_empty(search.weigh_uptimes):
        trace.append(["fillers", round(build.score, 4)])
    # The Infused factor read again in the finished build's state, after the last step that moves the conditions (the
    # manager's routing of MECH88's leftover, SEARCH28: measured where the skill was chosen, it went stale through the
    # play choices, presses and uptime weighing; 2b75e 0.273 -> 0.314), then the uptimes weighed on it
    if search.gems.refresh_infused():
        search.weigh_uptimes()
        trace.append(["infused", round(build.score, 4)])

    search.gear.sync_flasks()
    search.gems.meta_candidates()  # the Meta gems the search can't propose yet, with why, for the result
    play = play_guide.guide(search)  # "How to play it": the DPS without each condition the player or fight decides
    play += search.press_guide_rows()  # (and each chosen press rate, without it / with it)
    play += search.upkeep_guide_rows()  # (SEARCH41: and each kept-up side skill's casts, without it / with it)
    play += search.gear.filler_rows()  # (and each filler: no item raised the score in its slot)
    open_sockets = search.gems.open_sockets()  # (SEARCH34: each open support socket and why, a warning)
    play += search.gems.open_socket_rows(open_sockets)
    facts = build.call("facts", fields=RESULT_FIELDS)
    xml = build.call("exportXml")["xml"]
    code = codes.encode(xml)
    groups = build.groups()
    conditions = search.conditions.describe()
    quests = search.conditions.quests()
    equipped = dict(build.equipped)
    spent = build.spent()
    summary = search.summary()

    # Reload the exported code in this engine and compare every stat (the assumptions aren't in
    # the code, so they're put back first, exactly as the search had them)
    assumptions = list(search.assumptions.active)
    config_before = dict(build.call("getConfig")["input"])
    build.call("loadXml", xml=codes.decode(code))
    # What PoB itself warns about in the code, without the search's assumptions (a skill on weapon
    # set 2 can be flagged only on a fresh load), joins the live build's warnings for the checks
    plain = build.call("facts", fields=[])["warnings"]
    search.assumptions.reinstall()
    reloaded = build.call("facts", fields=RESULT_FIELDS)
    mismatched = [k for k in RESULT_FIELDS if facts["stats"].get(k) != reloaded["stats"].get(k)]
    facts["warnings"] = list(dict.fromkeys([*facts["warnings"], *reloaded["warnings"], *plain]))
    # the item-granted skills the build presses, for the button cap (constraints.item_skill_used; Search.fit_buttons)
    facts["pressedSkills"] = sorted(gems_module.pressed_item_skills(build))
    # (SEARCH44, THE USER 27 Nov) and the flasks it drinks for their damage (Melting Maelstrom's pulses): buttons too
    facts["damageFlasks"] = gems_module.damage_flask_buttons(getattr(search.assumptions, "mods", None))
    # Life / Mana recovery from kills, Remnants, charms and events (MECHC12), flask recovery, for the sustain and
    # recovery checks, and the request's boss (boss_hits.boss_name)
    facts["stats"].update(build.added_stats())
    with_life_es(with_boss_recovery(facts["stats"]))  # recovery against the boss, for the (optional) recovery floor and the report
    build.objective.prepare(facts["stats"])  # what the checks see: Darkness in maximum hit taken
    excluded, excluded_names = build.objective.excluded(build.stats)
    blind_spots = build.call("unsupportedInBuild")["lines"]
    # and what the coverage audit finds beyond PoB's own gap list: no-op lines and unread gem stats
    blind_spots += build_blind_spots(build.call("sourceLines")["lines"],
                                     [g["name"] for group in groups for g in group["gems"] if g.get("name")],
                                     config_on=set(search.conditions.active),
                                     stats=build.call("stats", fields=GATED_FIELDS))
    # The tree as the exported code has it, checked against the game's allocation rules (bridge
    # treeCheck): every passive reachable from the class start or through a socketed jewel
    tree_check = reloaded["treeCheck"]
    assumptions += search.jewels.radius_notes()  # the larger Time-Lost radius (custom model)
    assumptions += list(search.play_notes.values())  # standing still not counted, the Low Life weighting
    assumptions += skill_slot_notes(build.call("stats", fields=[SKILL_SLOTS, SKILL_SLOTS_POB]))  # (SEARCH39, R56-14)
    why_items, why_items_error = why_items_safe(search.gear)
    result = {
        **summary, "facts": facts, "code": code, "groups": groups, "conditions": conditions, "quests": quests,
        "play_guide": play, "finalize_trace": trace, "finalize_drift": restore_drift(build), "over_budget": build.over_budget,
        "floor_deltas": [dict(d) for d in build.objective.floor_deltas],  # the floors' part-time effects off (run.result)
        "added_dps": build.objective.added_report(),  # (each source of damage computed outside PoB, SEARCH14)
        "pob_full_dps": facts["stats"].get("FullDPS"), "excluded_dps": excluded, "excluded_from": excluded_names,
        "clear_estimate": build.objective.clear(build.stats),
        "blind_spots": blind_spots,
        "gear": {slot: option.describe() for slot, option in equipped.items()}, "spent": spent,
        "why_items": why_items, "why_items_error": why_items_error,  # (SEARCHC1: what each item does for the build)
        "assumptions": assumptions, "flasks": search.gear.flask_report(),
        "verified": not mismatched, "verify_mismatch": mismatched,
        "tree_check": tree_check, "radius_jewels": reloaded["radiusJewels"], "left_out": {**search.jewels.left_out, **search.gems.left_out},
        "open_sockets": open_sockets, "final_regrow": regrow_rows,
        "free_supports": search.gems.free_notes(),  # (SEARCH36: a Purity / Impurity group's free supports, a plain note)
        "unfinished": unfinished.rows(build),  # (SEARCHD1 hook: the delivered build's rows, run.unfinished_rows)
    }
    # The search's config inputs the reload dropped come back first, so the check below re-settles the build as delivered
    # (SEARCH33, probe POE2T5455): PoB writes an input equal to its own default as a Placeholder, so the chosen "Boss at
    # 2 m" (20, PoB's default) came back unset, the curse model read the 5 m fallback, Blasphemy's Despair left the boss,
    # and 8 of 9 combined-set gates were flagged UNSETTLED at exactly the play guide's "without" number (A0 22,607 ->
    # 18,722, its row "Boss at 2 m (at 5 m instead)")
    result["reload_restored_config"] = restore_config(build, config_before)
    # Last, after everything above has read the build: the same state applied again (SEARCH32, the manager's 09:35 ruling)
    result["reapply_drift"] = reapply_drift(search, lambda: fresh_score(engine, payload, skeleton, codes.decode(code)))
    return result


HEART_OF_ICE = "Heart of Ice"  # (SEARCH39: a sceptre's aura, ActiveSkills heart_of_ice; Search.choose_distance)


def skill_slot_notes(stats: dict) -> list[dict]:
    """(SEARCH39; RESEARCH56 R56-14) PoB's skill-slot reading as a row where the count differs from it: Kalandra's Touch
    copying an Unset Ring's Skill Slot, which players report the game doesn't do."""
    ours, pob = number(stats.get(SKILL_SLOTS)), number(stats.get(SKILL_SLOTS_POB))
    if pob <= 0 or ours == pob:
        return []
    return [{"label": "Skill slots", "value": f"{ours:g} (PoB reads {pob:g})",
             "basis": "Kalandra's Touch copies the other ring's lines but not an Unset Ring's Skill Slot (forum reports "
                      "3970030, 3974217, 3787840; RESEARCH56 R56-14, MEDIUM-HIGH; no GGG statement): the count leaves "
                      f"the copy out. On PoB's reading the build would have {pob:g} slots",
             "confidence": "MEDIUM-HIGH"}]


__all__ = ["Search", "optimize_task", "screen_task", "finalize_task", "ItemOption"]
