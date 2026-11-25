"""The outer loop (PLAN.md §5.1) and the final result.

1. Compile the request; enumerate every legal skeleton (shortlisting main skills by a quick DPS
   screen when the request names none).
2. Probe every skeleton across the worker pool; halve the field and continue the survivors until
   a few remain.
3. Spend the remaining time on the best K, several workers each (different seeds, kicks).
4. Finalize the best build on Full DPS, export it, reload the code to verify it, and check every
   requirement line by line. Anything failing makes the result infeasible, with the reason.
"""

from __future__ import annotations

import math
import os
import random
import re
from collections.abc import Callable
from typing import Any

from builder import clock
from builder.data import unique_versions
from builder.data.trade import TradeClient
from builder.pob import codes
from builder.pob.pool import EnginePool, WorkerError, default_workers
from builder.report.checklist import checklist
from builder.report.shopping import shopping_list
from builder.request.constraints import fmt
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.gear import OWNED  # (SEARCHB3: the user's own items, listed apart from the budget)
from builder.search.objective import (PACK_SIZE, SUSTAIN_FLOOR, SUSTAIN_LEFT_OUT, boss_recovery, goal_value, number, rank_key,
                                      shortfall, sustain)
from builder.search.skeletons import Skeleton, candidate_skills, enumerate_skeletons, weapon_archetypes
from builder.search import unfinished

FINALIZE_SHARE = 0.12
def sustain_only_note(failing: list[str]) -> str | None:
    """A build short only on skill-cost sustain says what that share leaves out, never a silent rejection (the user, 12
    Nov: "regen is not the only source of recovery, not by a longshot"; SEARCH9)."""
    if failing != ["Skill costs sustained"]:
        return None
    return ("the build is short only on skill-cost sustain, and that share leaves out " + SUSTAIN_LEFT_OUT
            + ": if the build has one of those, it may sustain its costs in play")


RECOVERY_NOTE = ("Life, Energy Shield and Mana regeneration, leech, on-hit recovery, flasks (at the rate a boss fight refills "
                 "them), recoup and gains on Block, Life / Mana from kills, Remnants, charms and events (at the build's own "
                 "rates), less skill costs, against the boss's damage per second after mitigation (PoB's boss "
                 "preset: its hits that land, after evade and block, which red-flash hits ignore; less Deflection, LOW against "
                 "red-flash hits). Mana counts only for damage Mind over Matter sends to "
                 "it; Energy Shield recharge at the share of the fight it runs between the boss's landed hits (each "
                 "restarts its delay, as does the damage over time a hit leaves and the build's own damage: its steady damage "
                 "over time stops it, Heartbound Loop's damage on each minion death restarts it; MEDIUM); Life recharge "
                 "(Eternal Youth, Tetzlapokal's Desire) at the same share")


def recovery_text(recovery: dict) -> str:
    if recovery["incoming"] <= 0:
        return "the boss's hits never land"
    parts = ", ".join(f"{pool} {value:,.0f}/s" for pool, value in recovery["parts"].items() if value > 0) or "none"
    text = f"{min(recovery['share'], 9.99):.0%} ({recovery['recovery']:,.0f}/s of {recovery['incoming']:,.0f}/s; {parts})"
    if recovery.get("recharging"):
        text += f"; Energy Shield recharge runs {recovery['recharging']:.0%} of the fight"
    if recovery.get("life_recharging"):
        text += f"; Life recharge runs {recovery['life_recharging']:.0%} of the fight"
    for pool in recovery.get("recharge_stopped") or []:  # (SEARCH15, A28-2: the build's own damage over time)
        text += f"; {pool} recharge never starts: the build's own damage over time keeps resetting its delay"
    if recovery.get("deflect"):  # (MECHC5: Deflection against red-flash hits is LOW, #78)
        text += f"; without Deflection {min(recovery['no_deflect_share'], 9.99):.0%}"
    if recovery.get("life_reserved_open") is not None:  # (MECHC32: Widow's Reign, objective.life_reserved_open)
        text += (f"; Life recovery counted {recovery['life_reserved_open']:.0%} of the fight (damage reserves Life until "
                 "a damage-free window: Widow's Reign)")
    if "biggest_share" in recovery:  # (MECHC32: the manager's 03:28 ruling, the biggest-hit row; boss_hits.biggest_reading)
        from builder.search.boss_hits import BIGGEST_LABEL
        text += f"; {BIGGEST_LABEL}:{min(recovery['biggest_share'], 9.99):.0%}"
    return text
FINALIZE_MAX = 60.0
# What finalize does after its polish (export, reload check, blind spots, facts): 3-10 s in the gate runs of 8 Nov, so
# the polish ends this much before the run's time is up and the whole run fits the time the user gave it
FINALIZE_WRAP = 10.0
# (SEARCH35; SEARCHB2's Tree.final_regrow, the manager's 17:48 "YOUR NEXT TOP ITEM": the search never regrew the tree on
# the finished gear, finalize turns the tree rebuild off; SEARCHB2's probe on the delivered builds, every floor met:
# 2b75d x1.39, 2b75b x2.40) finalize's last tree regrow gets its own time, taken from the search: FINAL_REGROW_SHARE of the
# run, at most FINAL_REGROW_MAX (SEARCHB2's probe: 178-240 s on real PoB). LOW, a time budget (on a 600 s gate: 150 s);
# a gate pair measures it. MEASURED LOWER, so OFF by default (POE2_SEARCH_FINAL_REGROW=1 turns it on): 2b75b seed 0, 600 s
# (the default run's length too), ON 10,335 vs OFF 38,066 (POE2T5552 / 5553): the regrow runs on one worker while the
# others wait, so its 150 s cost ~28% of the search's task time, the lines' last focus round (where the gear fills) was
# lost, and every line ended with 5-6 empty gear slots
FINAL_REGROW = os.environ.get("POE2_SEARCH_FINAL_REGROW", "0") == "1"
FINAL_REGROW_SHARE = 0.25
FINAL_REGROW_MAX = 240.0
FINAL_REGROW_MIN = 30.0  # (under this, a cleared tree can't grow back: a run under 120 s gets no regrow)


def joint_regrow_on() -> bool:
    """(SEARCH36; the manager's 11:50 22 Nov) SEARCHB3's joint move (Tree.joint_regrow, landing via SEARCHB4) switched on:
    POE2_SEARCH_JOINT_REGROW=1, read at call time by Tree.joint_enabled; False while that isn't on main. OFF by default.
    It takes finalize's regrow time as the final regrow does (FINAL_REGROW_SHARE of the run, at most FINAL_REGROW_MAX;
    each regrow step that is on gets that much)."""
    from builder.search.tree import Tree

    enabled = getattr(Tree, "joint_enabled", None)
    return callable(enabled) and hasattr(Tree, "joint_regrow") and bool(enabled())
MIN_PROBE = 15.0
MAX_PROBE = 60.0
SHORTLIST_SKILLS = 4
SCREEN_CHUNK = 6
SCREEN_SHARE = 0.25  # most of the run the main-skill screen may take
# Balanced mode: every BOSS_EVERY-th search of a skeleton maximizes boss DPS instead, so the
# hard-hitting builds a balanced search walks away from (attack and movement speed pay twice
# there) are still found. Each goal's searches continue from that goal's best state; the final
# pick ranks every result on the balanced value (PLAN.md §5.5).
BOSS_EVERY = 3
# The focus stage runs in rounds; after each but the last, a line far behind the best on its skeleton and goal starts
# again from the best line's state with a new seed. Gate 4, 8 Nov (POE2T188): a run's five lines ended at 24K, 29K,
# 414K, 414K and 582K, so two fifths of the run went into lines that could never win, and the result swung run to run
FOCUS_ROUNDS = 2
RESTART_BELOW = 0.5  # of the best line's value
# The focus stage scores PoB's Full DPS, as the delivered build is (PLAN.md, Phase 0: main-skill DPS for screening,
# Full DPS for finalists). On main-skill DPS, 2b75b's lines all ended on the same main group at 26-29K, and what
# finalize then measured (x2.4-x8.7 the reference) rested on groups the search never weighed (POE2T189, 8 Nov).
# OFF: one 2b75d run with it on gave x1.77 (lines within 30%, two of them stalled in the second round), against x2.91
# with the restarts alone (POE2T190-191); a Full DPS what-if costs about twice as much. Needs more runs per build
FOCUS_FULL_DPS = False
# (SPEED1, the manager 23 Nov 07:30: proposal 4) Only the LAST focus round scores Full DPS: a multi-fidelity schedule (the
# main skill's DPS, the cheap score, for the probe and round 1; the delivered score where the line ends). Finalize's
# Full DPS supports sweep alone moved 2b75d POE2T6320 by x2.6 and finalize adds x1.22 (median of 99 gate runs) over the
# search's last best, because the search picks supports on the main skill; with the last round on Full DPS that round's
# passes build on the supports the delivered score values. OFF by default (POE2_SEARCH_FULL_LAST=1)
FULL_LAST = os.environ.get("POE2_SEARCH_FULL_LAST", "0") == "1"


def replica_goals(goal: str, count: int) -> list[str | None]:
    """The goal of each of a skeleton's `count` searches: None keeps the request's."""
    if goal != "balanced":
        return [None] * count
    return ["boss" if r % BOSS_EVERY == 1 else None for r in range(count)]


def _score(entry: Any) -> float:
    return entry["score"] if isinstance(entry, dict) and "score" in entry else -math.inf



def floors_at_full_uptime(checks: list[dict], penalties: list, stats: dict, floor_deltas: list[dict]) -> None:
    """The request's floors count an effect only at full uptime (the user's rule): finalize's facts are read with every
    part-time effect on (conditions, flasks, ledger shares), so each floor is checked on the worst of those stats and the
    stats with the part-time effects off (floor_deltas, Search.floor_without: the deltas the search's own deficits read).
    A floor met only with a part-time effect on fails here, as it scores short in the search."""
    if not floor_deltas:
        return
    for penalty in penalties:
        on = number(stats.get(penalty.stat))
        worst = min([on, *(on + number(d.get(penalty.stat)) for d in floor_deltas if penalty.stat in d)])
        if worst >= on:
            continue
        requested = f"≥ {fmt(penalty.floor)}"
        for check in checks:
            if check["label"] == penalty.label and check["requested"] == requested:
                check.update(delivered=f"{fmt(worst)} with part-time effects off ({check['delivered']} with them on)",
                             ok=worst >= penalty.floor,
                             note="floors count an effect only at full uptime: checked with the effects the build keeps "
                                  "up for part of the fight off")
                break


def same_state_text(xml: str | None) -> str | None:
    """(SPEED2, on loan; the work clock only, POE2_SEARCH_WORK_CLOCK=1, read here because gate4 --work sets it after this
    module is imported) A state's XML as Run.diverge compares it. PoB writes an item's alternative bases in an order LuaJIT
    picks anew in each process (PERF16; replay.table_order_free), so two workers' copies of one build can read as different
    states, and whether a duplicate line gets its kick then changes from run to run (2b75d POE2T7818-7820: the same seed
    kicked different round-1 lines). Off the work clock the comparison stays as it was."""
    if xml and os.environ.get("POE2_SEARCH_WORK_CLOCK", "0") == "1":
        from builder.pob.replay import table_order_free

        return table_order_free(xml)
    return xml


SEED_STRIDE = 1_000_003  # (Run.seed_base: well past every task seed a run makes on its own)


class Run:
    regrow_seconds = 0.0  # (plan: finalize's final tree regrow time)

    def __init__(self, request: BuildRequest, prices: Any = None, workers: int | None = None,
                 seconds: float | None = None, on_event: Callable[[dict], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None, cpu_clock: bool = False,
                 seed_base: int = 0) -> None:
        self.request = request
        self.price_table = prices
        self.payload = make_payload(request, prices)
        # A gate that compares code, not PC load (SEARCH19; gate4 --cpu): each worker's task time is its own processor
        # time (inner.use_cpu_clock), and the run's own clock is the task time it has handed out (each wave adds its
        # longest task's seconds), so the plan's shares and the phases are the same on a busy PC as on a quiet one; the
        # wall time grows instead. The result says so (cpu_clock, wall_seconds).
        self.cpu_clock = cpu_clock
        self.handed_out = 0.0
        # Which draws the run's tasks take (SEARCH20; gate4 --seed-base): every task seed moves by SEED_STRIDE x seed_base,
        # so a gate repeated on seed bases 0, 1, 2 takes three independent sets of draws, and the same bases on two commits
        # take the same ones (0: the seeds the search has always used)
        self.seed_base = seed_base
        if cpu_clock:
            self.payload["cpu_clock"] = True  # (inner.CPU_CLOCK)
        self.ctx = Context(self.payload)
        self.workers = workers or default_workers()
        self.seconds = seconds if seconds is not None else request.effort_minutes * 60.0
        self.on_event = on_event or (lambda event: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.started = clock.now()
        self.notes: list[str] = []
        # (the manager, 18 Nov 14:50) which league's prices the result rests on, and what's wrong with them, said in it
        league = getattr(prices, "league", None)
        self.notes.append(f"Priced on: {league} (poe.ninja)" if league else "Priced on: no price data (uniques, gems and "
                          "currency unpriced)")
        self.notes += [f"Prices: {warning}." for warning in getattr(prices, "warnings", None) or []]
        if extra := getattr(self.request, "extra_passive_points", 0):  # (SEARCH38, R54-7)
            self.notes.append(f"Passive points: {extra} extra counted in the main budget, as you asked (the Martyr of the "
                              "First Edict gives one, most likely a plain point: MEDIUM-LOW; a new character has none).")
        if weapon := getattr(self.request, "extra_weapon_set_points", 0):  # (SEARCH39, RESEARCH56 R56-13)
            self.notes.append(f"Weapon-set passive points: {weapon} extra counted in each weapon set's allowance, as you "
                              "asked (Olroth's Boon grants one Weapon Set Passive Skill Point: HIGH; a new character has "
                              "none).")
        self.probes: dict[str, dict] = {}  # skeleton label -> latest result
        self.live: dict[int, dict] = {}  # task index -> latest progress
        self.evals_done = 0
        self.seed_start: dict | None = None  # the PoB code's own numbers under this request
        self.best: dict | None = None
        # (SEARCHD1, the user 24 Nov: partial beats empty, and say what wasn't finished) the run's own stages the deadline
        # cut (builder/search/unfinished.py); the rounds' rows come in the best line's state and finalize's result
        self.unfinished: list[dict] = []

    def task_seed(self, seed: int) -> int:
        """A task's seed in this run's draw set (seed_base)."""
        return seed + SEED_STRIDE * getattr(self, "seed_base", 0)

    # Events ------------------------------------------------------------------------------------

    def emit(self, kind: str, **data: Any) -> None:
        self.on_event({"kind": kind, "t": round(clock.now() - self.started, 2), **data})

    def elapsed(self) -> float:
        return self.handed_out if getattr(self, "cpu_clock", False) else clock.now() - self.started

    def hand_out(self, tasks: list[tuple[str, dict]], key: str = "seconds") -> None:
        """(cpu_clock) The run's clock moves by a wave's longest task time once the wave is done."""
        if getattr(self, "cpu_clock", False):
            self.handed_out += max((number(t[1].get(key)) for t in tasks), default=0.0)

    def remaining(self) -> float:
        return self.seconds - self.elapsed()

    def _progress(self) -> Callable[[int, Any], None]:
        last = [0.0]

        def on_progress(index: int, payload: dict) -> None:
            self.live[index] = payload
            now = clock.now()
            if now - last[0] >= 1.0:
                last[0] = now
                rate = sum(p.get("evals_per_second", 0.0) for p in self.live.values())
                # Searches may run on different goals: the leader is the best on the request's
                top = max(self.live.values(), key=lambda p: (not p["deficits"], self.goal_of(p)))
                self.emit("progress", evals_per_second=round(rate), tasks=len(self.live), leader=top["skeleton"],
                          leader_dps=top["dps"], leader_value=self.goal_of(top), phase=top["phase"],
                          deficits=top["deficits"],
                          evals=self.evals_done + sum(p.get("evals", 0) for p in self.live.values()))
        return on_progress

    def _record(self, results: list[Any]) -> list[dict]:
        good = []
        for result in results:
            if isinstance(result, WorkerError):
                self.notes.append(f"a search task failed: {str(result).strip().splitlines()[-1][:300]}")
                continue
            if result is None:
                continue
            self.evals_done += result.get("evals", 0)
            # Every search's result, for comparing a run's lines: where it ended, its main group, the steps that ran
            # past the task's deadline (the hard stop caps them), its wall time, the time its clock left out, each
            # phase's seconds, evaluations and gain, its requirement raises and its reverts; the requirements
            # its build still misses (empty: it met every floor) and their weighted shortfall
            self.emit("task", skeleton=result["label"], dps=result["dps"], value=result.get("value"),
                      deficits=[d["label"] for d in result["deficits"]],
                      shortfall=round(shortfall(result["deficits"]), 6),
                      main=result.get("main_gems"), phases=result.get("overrun", {}),
                      wall=result.get("wall"), left_out=result.get("left_out"), ran=result.get("ran"),
                      timing=result.get("timing"), escalations=result.get("escalations"),
                      reverts=result.get("reverts"), supports=result.get("support_census"),
                      # Block passes started and finished (all blocks before the deadline), and kicks by kind: whether
                      # a task's local search got past its first pass (SEARCH_PLAN.md §2: on 2b75b none did)
                      passes=result.get("passes"), passes_done=result.get("passes_done"), kicks=result.get("kicks"),
                      kicks_kept=result.get("kicks_kept"), kick_kinds=result.get("kick_kinds"),
                      # restores and how far their scores came back from the snapshots' (inner.restore_drift)
                      restore_drift=result.get("restore_drift"), line=result.get("line"),
                      over_budget=result.get("over_budget"),  # (SEARCH8: where the tree first went over its points)
                      gear=result.get("gear"))  # (SEARCH20: empty gear slots, where the gear round resumes, spent)
            if result.get("seed_start") and self.seed_start is None:
                self.seed_start = result["seed_start"]
            # Ranked on the request's goal, whichever the search had; its own goal picks the state
            # its line of searches continues from
            result["search_value"] = result.get("value", result["dps"])
            result["value"] = self.goal_of(result)
            label = result["label"]
            if label not in self.probes or rank_key(result) < rank_key(self.probes[label]):
                self.probes[label] = result
            if self.best is None or rank_key(result) < rank_key(self.best):
                self.best = result
                self.emit("best", skeleton=label, dps=result["dps"], clear=result.get("clear"),
                          value=result.get("value"), score=result["score"],
                          deficits=[d["label"] for d in result["deficits"]])
            good.append(result)
        self.live.clear()
        return good

    @staticmethod
    def boss_share(goals: list[str | None]) -> str:
        boss = goals.count("boss")
        return f" ({boss} of them on boss DPS)" if boss else ""

    def lineage(self, result: dict) -> tuple[str, str]:
        return result["label"], result.get("goal") or self.request.objective

    def keep_states(self, states: dict, results: list[dict]) -> None:
        """Where each (skeleton, goal) line of searches continues: the request's goal from the best
        result on it, any other goal from its own searches' best on that goal."""
        best: dict[tuple[str, str], tuple[tuple[float, float], dict]] = {}
        for result in results:
            short = round(shortfall(result["deficits"]), 6)
            for key, value in {self.lineage(result): result["search_value"],
                               (result["label"], self.request.objective): result["value"]}.items():
                if key not in best or (short, -value) < best[key][0]:
                    best[key] = ((short, -value), result)
        for key, (_, result) in best.items():
            states[key] = result["state"]

    def restart_behind(self, lines: dict, tasks: list[tuple[str, dict]], raw: list[Any]) -> int:
        """Lines far behind the best line on their skeleton and goal (infeasible while it isn't, or under RESTART_BELOW
        of its value) continue from that line's state; the best line goes on as it is. Returns how many restarted."""
        ended: dict[tuple[str, str], list[tuple[tuple[float, float], int, dict]]] = {}
        count: dict[str, int] = {}
        for (_, args), result in zip(tasks, raw):
            label = Skeleton(**args["skeleton"]).label
            replica = count.get(label, 0)
            count[label] = replica + 1
            if isinstance(result, dict) and result.get("state") is not None:
                value = result.get("search_value", result.get("value", result["dps"]))
                key = (round(shortfall(result["deficits"]), 6), -value)
                ended.setdefault((label, args.get("goal") or ""), []).append((key, replica, result))
        restarted = 0
        for (label, goal), entries in ended.items():
            entries.sort(key=lambda e: e[0])
            (top_short, top_value), _, top = entries[0]
            for (short, value), replica, _ in entries[1:]:
                if short > top_short or -value < RESTART_BELOW * -top_value:
                    lines[(label, replica, goal)] = {**top["state"], "restarted": True}  # (kicked first: optimize_task)
                    restarted += 1
        if restarted:
            self.emit("status", message=f"{restarted} search(es) far behind the best continue from it")
        return restarted

    @staticmethod
    def diverge(tasks: list[tuple[str, dict]]) -> int:
        """A focus task starting from the same state as an earlier one on its skeleton and goal is marked to kick first
        (optimize_task): the search is deterministic from a state until its first kick, which a 235 s round seldom
        reaches, so such tasks repeated each other whatever their seeds (2b75b POE2T656: five round-2 tasks, two
        outcomes; POE2T649: one). Returns how many were marked."""
        seen: set = set()
        marked = 0
        for _, args in tasks:
            state = args.get("state")
            if not state:
                continue
            key = (Skeleton(**args["skeleton"]).label, args.get("goal") or "", same_state_text(state.get("xml")))
            if key in seen and not state.get("restarted"):
                args["state"] = {**state, "restarted": True}
                marked += 1
            seen.add(key)
        return marked

    @staticmethod
    def keep_lines(lines: dict, tasks: list[tuple[str, dict]], raw: list[Any]) -> None:
        """Each finished task's state, as where its (skeleton, replica, goal) line continues (results come in task
        order; replicas count from 0 within a skeleton, in the order the tasks list them)."""
        count: dict[str, int] = {}
        for (_, args), result in zip(tasks, raw):
            label = Skeleton(**args["skeleton"]).label
            replica = count.get(label, 0)
            count[label] = replica + 1
            if isinstance(result, dict) and result.get("state") is not None:
                lines[(label, replica, args.get("goal") or "")] = result["state"]

    def line_state(self, lines: dict, states: dict, label: str, replica: int, goal: str | None) -> dict | None:
        """Where a replica's search continues: its own line, if it has one on this goal, else the skeleton's best."""
        return lines.get((label, replica, goal or "")) or self.state_for(states, label, goal)

    def state_for(self, states: dict, label: str, goal: str | None) -> dict | None:
        return states.get((label, goal or self.request.objective)) or states.get((label, self.request.objective))

    def goal_of(self, result: dict) -> float:
        """A result's (or progress report's) value on the request's goal."""
        return goal_value(self.request.objective, result["dps"], result.get("clear") or 0.0)

    def _tasks(self, pool: EnginePool, tasks: list[tuple[str, dict]]) -> list[Any]:
        raw = pool.run_tasks(tasks, on_progress=self._progress(), should_cancel=self.should_cancel)
        self.hand_out(tasks)
        return raw

    # Stages ------------------------------------------------------------------------------------

    def skeletons(self, pool: EnginePool) -> tuple[list[Skeleton], list[str]]:
        if self.request.main_skill:
            return enumerate_skeletons(self.ctx, [self.request.main_skill])
        skills = candidate_skills(self.ctx)
        self.emit("status", message=f"No main skill given: screening {len(skills)} skills")
        ascendancy = self.ctx.compiled.filters.ascendancies[0] if self.ctx.compiled.filters.ascendancies else None
        screen = []
        for skill in skills:
            archetypes, _ = weapon_archetypes(self.ctx, skill)
            for weapon, sub, offhand in archetypes[:2]:
                if ascendancy:
                    screen.append(Skeleton(self.ctx.class_of[ascendancy], ascendancy, skill, weapon, sub, offhand))
        random.Random(0).shuffle(screen)  # a time-capped screen shouldn't favour early names
        chunks = [screen[i:i + SCREEN_CHUNK] for i in range(0, len(screen), SCREEN_CHUNK)]
        deadline = self.elapsed() + SCREEN_SHARE * self.seconds
        results = pool.run_tasks([("builder.search.inner:screen_task",
                                   {"payload": self.payload, "skeletons": [s.as_dict() for s in chunk]})
                                  for chunk in chunks],
                                 should_cancel=lambda: self.should_cancel() or self.elapsed() > deadline)
        if self.cpu_clock:  # (each chunk's screen takes its seconds_each per setup; the share is what the plan gave it)
            self.handed_out += SCREEN_SHARE * self.seconds
        screened = sum(len(chunk) for chunk in results if isinstance(chunk, list))
        if screened < len(screen):
            self.notes.append(f"The skill screen covered {screened} of {len(screen)} skill setups in its time share; "
                              "a longer search time screens them all.")
            unfinished.note(self, "skill screen", "main skill choice", screened, len(screen), "skill setups",
                            "the main skill was chosen from the setups screened in time")
        ranked: dict[str, float] = {}
        for chunk in results:
            if isinstance(chunk, list):
                for entry in chunk:
                    name = entry["skeleton"]["main_skill"]
                    ranked[name] = max(ranked.get(name, 0.0), entry.get("value", entry["dps"]))
        shortlist = [name for name, value in sorted(ranked.items(), key=lambda x: -x[1]) if value > 0][:SHORTLIST_SKILLS]
        self.emit("status", message="Shortlisted main skills: " + (", ".join(shortlist) or "none"))
        if not shortlist:
            return [], ["no allowed skill deals damage in PoB with the allowed weapons"]
        return enumerate_skeletons(self.ctx, shortlist)

    def resolve_seed(self) -> None:
        """The request's PoB code, or the code behind a pasted share link; dropped with a note if
        it's neither."""
        code = self.request.seed_code.strip()
        try:
            codes.decode(code)
        except Exception:
            try:
                from builder.web.app import resolve_code

                code = resolve_code(code)
                codes.decode(code)
            except Exception as error:
                self.notes.append(f"Your PoB code couldn't be read ({str(error)[:120]}); the search started from scratch.")
                code = None
        self.payload["request"]["seed_code"] = code

    def with_seed(self, pool: EnginePool, skeletons: list[Skeleton]) -> list[Skeleton]:
        """Add the PoB code's skeleton (PLAN.md §5.1): its search starts from the code and never
        ends below it."""
        found = pool.run_tasks([("builder.search.inner:seed_task", {"payload": self.payload})])[0]
        if isinstance(found, WorkerError):
            self.notes.append(f"Your PoB code couldn't be loaded ({str(found).strip().splitlines()[-1][:200]}); "
                              "the search started from scratch.")
            return skeletons
        if found["skeleton"] is None:
            self.notes.append("The search didn't start from your PoB code: " + "; ".join(found["notes"]) + ".")
            return skeletons
        seed = Skeleton(**found["skeleton"])
        self.notes += [f"Your PoB code: {note}." for note in found["notes"]]
        self.emit("status", message=f"Starting one line of searches from your PoB code ({seed.label})")
        return [seed] + [s for s in skeletons if s.label != seed.label]

    def plan(self, count: int) -> tuple[float, float]:
        """(probe seconds, finalize seconds) for `count` skeletons in the time left."""
        finalize = min(FINALIZE_MAX, max(FINALIZE_SHARE * self.seconds, FINALIZE_WRAP + 5.0))
        steps = int(FINAL_REGROW) + int(joint_regrow_on())  # (SEARCH36: the joint move's time, as the final regrow's)
        regrow = min(FINAL_REGROW_MAX, FINAL_REGROW_SHARE * self.seconds)
        self.regrow_seconds = steps * regrow if regrow >= FINAL_REGROW_MIN else 0.0  # (a short run: no regrow)
        finalize += self.regrow_seconds  # (finalize's polish, then the final tree regrow: finalize_task's `regrow`)
        search = max(self.remaining() - finalize, 10.0)
        waves = math.ceil(count / self.workers)
        probe = min(MAX_PROBE, max(MIN_PROBE, 0.4 * search / waves))
        return probe, finalize

    def execute(self) -> dict:
        if self.ctx.compiled.errors:
            return self.result_invalid()
        if self.request.seed_code:
            self.resolve_seed()
        if getattr(self.request, "focus", None) is not None:  # (SEARCHD1: optimize one part of the seed build)
            return self.execute_focus()
        if self.request.objective != "boss":
            goal = ("clear speed" if self.request.objective == "mapping"
                    else "the geometric mean of boss DPS and clear speed")
            self.notes.append(f"* This result maximizes {goal}. PoB doesn't model clear speed, so it is an estimate "
                              "(PLAN.md §5.5): monster life cleared per second in a typical map pack, from the main "
                              "skill's damage, area, projectiles, chain, pierce and fork, its use speed and movement "
                              "speed. Treat clear numbers as approximate (confidence: low).")
        self.emit("status", message=f"Starting {self.workers} PoB workers")
        with EnginePool(self.workers) as pool:
            self.emit("status", message=f"{pool.size} workers ready in {pool.startup_seconds:.1f}s")
            # The plan is on the workers the pool has: inside the PC queue it gets only the LuaJIT slots still free (gate
            # 4 boots an engine of its own first, so 5 of 6), and 6 searches on 5 workers ran in two waves, which doubled
            # every stage: a 600 s gate run took about 966 s (8 Nov)
            self.workers = pool.size
            skeletons, problems = self.skeletons(pool)
            if self.payload["request"].get("seed_code"):
                skeletons = self.with_seed(pool, skeletons)
            self.emit("skeletons", count=len(skeletons), labels=[s.label for s in skeletons], problems=problems)
            if not skeletons:
                return self.result_infeasible(problems)
            probe, finalize = self.plan(len(skeletons))

            # Probes, then successive halving while there are more survivors than workers
            states: dict[tuple[str, str], dict] = {}  # (skeleton, goal) -> the state its searches continue from
            # (skeleton, replica, goal) -> where that replica's own line of searches is. Each replica continues its own
            # line, so a run keeps several independent searches to the end: gate 4 showed every replica restarting from
            # the one best probe state, which was nearly unset (2b75b: 218 DPS after the probe), so the whole run took
            # that one state's basin, and the result swung run to run (x0.21-x13.03 on the same code)
            lines: dict[tuple[str, int, str], dict] = {}
            alive = skeletons
            round_index = 0
            while alive and not self.should_cancel():
                # With fewer skeletons than workers, each gets several searches (different seeds)
                replicas = max(1, self.workers // len(alive))
                goals = replica_goals(self.request.objective, replicas)
                self.emit("status", message=f"Probing {len(alive)} skeleton(s) for {probe:.0f}s"
                                            + (f", {replicas} searches each" if replicas > 1 else "")
                                            + self.boss_share(goals))
                tasks = [("builder.search.inner:optimize_task", {
                    "payload": self.payload, "skeleton": s.as_dict(), "seconds": probe,
                    "seed": self.task_seed(round_index * 1000 + 31 * r + i),
                    "state": self.line_state(lines, states, s.label, r, goals[r]),
                    "goal": goals[r]})
                    for i, s in enumerate(alive) for r in range(replicas)]
                best_of: dict[str, dict] = {}
                raw = self._tasks(pool, tasks)
                self.keep_lines(lines, tasks, raw)
                results = self._record(raw)
                for result in results:
                    if result["label"] not in best_of or rank_key(result) < rank_key(best_of[result["label"]]):
                        best_of[result["label"]] = result
                self.keep_states(states, results)
                ranked = sorted(best_of.values(), key=rank_key)
                for r in ranked:
                    self.emit("probe", skeleton=r["label"], dps=r["dps"], clear=r.get("clear"), value=r.get("value"),
                              score=r["score"], deficits=[d["label"] for d in r["deficits"]])
                keep = max(1, min(len(ranked), max(self.workers // 3, 1)))
                if len(ranked) <= max(keep, self.workers) or self.remaining() < finalize + 2 * probe:
                    alive = [Skeleton(**r["skeleton"]) for r in ranked[:keep]]
                    break
                alive = [Skeleton(**r["skeleton"]) for r in ranked[: math.ceil(len(ranked) / 2)]]
                round_index += 1

            # Focus: the best K skeletons share every worker until it's time to finalize
            focus_seconds = self.remaining() - finalize
            if alive and focus_seconds > 5 and not self.should_cancel():
                replicas = max(1, self.workers // len(alive))
                goals = replica_goals(self.request.objective, replicas)
                self.emit("status", message=f"Improving the best {len(alive)} skeleton(s) with {replicas} search(es) each "
                                            f"for {focus_seconds:.0f}s" + self.boss_share(goals))
                end = self.elapsed() + focus_seconds
                for focus_round in range(FOCUS_ROUNDS):
                    seconds = (end - self.elapsed()) / (FOCUS_ROUNDS - focus_round)
                    if seconds <= 5 or self.should_cancel():
                        unfinished.note(self, "search rounds", "the best skeletons", focus_round, FOCUS_ROUNDS, "rounds",
                                        "the last improvement round" + ("s" if FOCUS_ROUNDS - focus_round > 1 else "")
                                        + " didn't run")
                        break
                    tasks = [("builder.search.inner:optimize_task", {
                        "payload": self.payload, "skeleton": s.as_dict(), "seconds": seconds,
                        "seed": self.task_seed(7919 * (r + 1) + i + 104729 * focus_round),
                        "state": self.line_state(lines, states, s.label, r, goals[r]), "goal": goals[r],
                        "full": FOCUS_FULL_DPS or (FULL_LAST and focus_round == FOCUS_ROUNDS - 1),
                        # (SEARCH29: the seeded curse trial, gems.CURSE_SEEDS, once per run: the first focus task only)
                        "curses": focus_round == 0 and i == 0 and r == 0,
                        "last": focus_round == FOCUS_ROUNDS - 1})  # (SEARCH35: inner.LINE_REGROW in the line's last task)
                        for i, s in enumerate(alive) for r in range(replicas)]
                    self.diverge(tasks)
                    raw = self._tasks(pool, tasks)
                    self.keep_lines(lines, tasks, raw)
                    results = self._record(raw)
                    self.keep_states(states, results)
                    if focus_round < FOCUS_ROUNDS - 1:
                        self.restart_behind(lines, tasks, raw)

            if self.best is None:
                return self.result_infeasible(problems + ["every search task failed"])
            best = self.best
            self.emit("status", message=f"Finalizing {best['label']} on Full DPS")
            final = seed_final = None
            # (SEARCHD1) a seeded run's seed (a pasted code, Continue), finalized beside the best on another worker: the floor
            # it never falls below (the manager, 24 Nov 13:48: "The user should never get back a build worse than the one
            # they gave")
            floor = self.floor_task() if attempt_floor(self) else None
            finalize_started = clock.now()
            for attempt in range(2):  # a worker the watchdog replaced gets one retry
                base = finalize - self.regrow_seconds  # (the polish's own share: plan)
                quick = attempt or self.should_cancel()
                regrow = 0.0 if quick else min(self.regrow_seconds, max(0.0, self.remaining() - base))
                polish = 5.0 if quick else max(5.0, min(base, self.remaining() - regrow) - FINALIZE_WRAP)
                finals = pool.run_tasks([("builder.search.inner:finalize_task", {
                    "payload": self.payload, "skeleton": best["skeleton"], "state": best["state"], "seconds": polish,
                    "regrow": regrow})] + ([floor] if floor and attempt == 0 else []),
                    on_progress=self._progress())
                final = finals[0]
                if len(finals) > 1:
                    seed_final = finals[1]
                if self.cpu_clock:
                    self.handed_out += polish + regrow
                if not isinstance(final, WorkerError):
                    break
                self.notes.append(f"finalizing failed once: {str(final).strip().splitlines()[-1][:200]}")
            if pool.replaced:
                self.notes.append(f"{pool.replaced} PoB worker(s) ran away and were replaced; their "
                                  "searches were lost, the rest continued.")
            self.finalize_wall = round(clock.now() - finalize_started, 1)  # (SEARCHD1: the floor's cost, measured)
            final = self.at_least_seed(final, seed_final)
            if isinstance(final, WorkerError):
                return self.result_infeasible(problems + [f"finalizing failed: {str(final).strip().splitlines()[-1]}"])
            self.evals_done += final.get("evals", 0)
            # The last "best" line is the delivered build: finalize measures PoB's Full DPS and polishes,
            # while searching measured the main skill, so the two can differ
            self.emit("best", skeleton=final["label"], dps=final["dps"], clear=final.get("clear"),
                      value=self.goal_of(final), score=final["score"],
                      deficits=[d["label"] for d in final["deficits"]], final=True,
                      trace=final.get("finalize_trace"), searched=best.get("score"),
                      drift=final.get("finalize_drift"), over_budget=final.get("over_budget"),
                      reapply=final.get("reapply_drift"))
            unsettled = final.get("reapply_drift") or {}
            if unsettled.get("flagged"):  # (SEARCH32: a model feeding on its own last output; inner.reapply_drift)
                self.notes.append(
                    f"UNSETTLED: the delivered DPS ({unsettled['delivered']:,.0f}) moves when the same build is settled "
                    f"again ({', '.join(f'{d:,.0f}' for d in unsettled['after'])}) or when its code is scored from "
                    f"scratch ({unsettled['fresh'] or 0:,.0f}): a model reads its own last output or state the search "
                    "carried, so this number depends on how the build was reached; treat it as uncertain")
            return self.result(final, problems)

    # Continue and optimize one part (SEARCHD1; the user, 24 Nov: "options to continue working on the overall build, or to
    # optimize a single item or skill or the tree") ---------------------------------------------------------------------

    def floor_task(self) -> tuple[str, dict] | None:
        """The seed build finalized with no polish (focus.seed_finalize_task), for a run with an origin (Continue)."""
        skeleton = next((r["skeleton"] for r in self.probes.values() if r["skeleton"].get("seeded")), None)
        if skeleton is None:
            return None
        return ("builder.search.focus:seed_finalize_task", {"payload": self.payload, "skeleton": skeleton})

    def floor_key(self, final: dict) -> tuple[float, float]:
        return rank_key({"dps": final["dps"], "value": self.goal_of(final), "deficits": final["deficits"]})

    def at_least_seed(self, final: Any, seed_final: Any) -> Any:
        """(SEARCHD1: the seed is the floor) The seed build as delivered if it ranks above the search's (fewer requirements
        short, then the goal): a Continue or focus run never returns less than the build it started from, scored now."""
        if seed_final is None:
            return final
        if isinstance(seed_final, WorkerError):
            self.notes.append("The starting build couldn't be finalized for the never-worse check "
                              f"({str(seed_final).strip().splitlines()[-1][:200]}); the search's result stands.")
            return final
        self.seed_final = seed_final
        kept = isinstance(final, WorkerError) or self.floor_key(seed_final) < self.floor_key(final)
        chosen = seed_final if kept else final
        self.floor_check = {"finalize_wall": getattr(self, "finalize_wall", None),
                            "seed_finalize_seconds": round(number(seed_final.get("seconds")), 1),
                            "seed_dps": seed_final["dps"], "searched_dps": None if isinstance(final, WorkerError)
                            else final["dps"], "kept_seed": kept, "seed_key": list(self.floor_key(seed_final)),
                            "delivered_key": list(self.floor_key(chosen))}
        if kept:
            self.notes.append(f"The search found nothing better than the build it started from in this time "
                              f"({seed_final['dps']:,.0f} DPS, scored here under this request), so that build is delivered "
                              "as it was (a run never returns a build worse than the one it was given). More time, or "
                              "Optimize one part, may still improve it.")
            seed_final["floor_kept"] = True
            return seed_final
        focus = getattr(self.request, "focus", None) is not None
        if not isinstance(final, WorkerError) and (not focus or number(final["dps"]) < number(seed_final["dps"])):
            # (SEARCHD2; the manager, 24 Nov 20:06, after POE2T8212 delivered a 15,086-DPS build over a 501,578-DPS seed
            # that missed more requirements: the result says plainly why another build than the one given comes back. A
            # focus run shows its own before / after, so it gets the note only when its DPS fell: TRAIN 143's POE2T5753,
            # a Gloves focus delivered 481,942 against the seed's 501,578, ranked higher by the requirements.)
            self.notes.append(replaced_seed_note(seed_final, final))
        return final

    def execute_focus(self) -> dict:
        """Optimize one part of the seed build (focus.focus_task), everything else held; the seed finalized beside it is
        the floor; the result says the part's before / after and the whole build's DPS before / after."""
        from builder.search.focus import part_label

        focus = self.request.focus.model_dump(mode="json")
        if not self.payload["request"].get("seed_code"):
            return self.result_infeasible(["optimizing one part needs a build to start from, and its PoB code couldn't "
                                           "be read"])
        self.emit("status", message=f"Optimizing {part_label(focus)} only; everything else is held as it is")
        with EnginePool(self.workers) as pool:
            self.workers = pool.size
            skeletons = self.with_seed(pool, [])
            if not skeletons:
                return self.result_infeasible(["the build couldn't be loaded to start from: " + "; ".join(self.notes[-1:])])
            seed = skeletons[0]
            wrap = min(FINALIZE_MAX, max(FOCUS_WRAP, FINALIZE_SHARE * self.seconds))
            seconds = max(5.0, self.remaining() - wrap)
            replicas = self.workers if focus["kind"] == "tree" else 1  # (the tree's rounds draw: several draws, the best)
            tasks = [("builder.search.focus:focus_task", {"payload": self.payload, "skeleton": seed.as_dict(),
                                                          "seconds": seconds, "focus": focus, "seed": self.task_seed(r)})
                     for r in range(replicas)]
            results = self._record(self._tasks(pool, tasks))
            if not results or self.best is None:
                return self.result_infeasible(["the one-part search failed: " + "; ".join(self.notes[-1:])])
            best = self.best
            self.emit("status", message=f"Finalizing {best['label']}")
            finals = pool.run_tasks([
                ("builder.search.inner:finalize_task", {"payload": self.payload, "skeleton": best["skeleton"],
                                                         "state": best["state"], "seconds": 0.0}),
                ("builder.search.focus:seed_finalize_task", {"payload": self.payload, "skeleton": best["skeleton"],
                                                            "state": best["seed_state"]})], on_progress=self._progress())
            final = self.at_least_seed(finals[0], finals[1])
            if isinstance(final, WorkerError):
                return self.result_infeasible([f"finalizing failed: {str(final).strip().splitlines()[-1]}"])
            self.evals_done += final.get("evals", 0)
            self.emit("best", skeleton=final["label"], dps=final["dps"], clear=final.get("clear"),
                      value=self.goal_of(final), score=final["score"],
                      deficits=[d["label"] for d in final["deficits"]], final=True)
            report = best.get("focus") or {}
            final["unfinished"] = (final.get("unfinished") or []) + (best.get("unfinished") or [])
            if report.get("problem"):
                self.notes.insert(0, f"Nothing was optimized: {report['problem']}.")
            if report.get("overtime"):
                self.notes.append(f"Loading the build took {report['setup_seconds']:g} s of the time, so the part still got "
                                  f"its minimum time: the run went {report['overtime']:g} s past the minutes asked.")
            result = self.result(final, [])
            result["focus"] = focus_report(focus, report, getattr(self, "seed_final", None), final, self.ctx)
            if result["focus"]["held_changed"]:
                result["notes"].insert(0, "WARNING: a part this run should have held changed in the finished build: "
                                       + ", ".join(result["focus"]["held_changed"]))
            return result

    # Results -----------------------------------------------------------------------------------

    def _base(self, status: str) -> dict:
        elapsed = self.elapsed()
        return {
            "status": status, "request": self.request.model_dump(mode="json"), "notes": self.notes,
            "priced_on": getattr(self.price_table, "league", None),
            "price_warnings": list(getattr(self.price_table, "warnings", None) or []),
            "seconds": round(elapsed, 1), "evaluations": self.evals_done,
            "wall_seconds": round(clock.now() - self.started, 1), "cpu_clock": getattr(self, "cpu_clock", False),
            "evals_per_second": round(self.evals_done / elapsed) if elapsed > 0 else 0,
            "workers": self.workers,
            "probes": sorted(({"skeleton": label, "dps": r["dps"], "clear": r.get("clear"),
                               "value": r.get("value", r["dps"]), "score": r["score"],
                               "deficits": [d["label"] for d in r["deficits"]]}
                              for label, r in self.probes.items()), key=lambda p: (len(p["deficits"]) > 0, -p["value"])),
        }

    def result_invalid(self) -> dict:
        result = self._base("invalid")
        result["feasible"] = False
        result["reasons"] = [f"{i.field}: {i.message}" for i in self.ctx.compiled.errors]
        return result

    def result_infeasible(self, problems: list[str]) -> dict:
        result = self._base("infeasible")
        result["feasible"] = False
        result["reasons"] = problems or ["no legal build found"]
        return result

    def result(self, final: dict, problems: list[str]) -> dict:
        facts = final["facts"]
        checks = [c.__dict__ for c in self.ctx.compiled.check(facts)]
        spent = final["spent"]
        budget = self.ctx.budget
        owned = []  # (SEARCHB3, the user 22 Nov) the user's own items: listed apart, never in the budget (gear.OWNED)
        for slot, gear in final["gear"].items():
            if gear.get("confidence") == OWNED:
                entry = self.ctx.unique_prices.get(gear["name"]) if gear.get("unique") else None
                gear["owned"], gear["market_price"] = True, (round(entry[0], 2) if entry else None)
                owned.append({"slot": slot, "name": gear["name"], "base": gear["base"], "market_price": gear["market_price"],
                              "note": "owned, not counted in the budget; " + (
                                  f"market price {gear['market_price']} divine (poe.ninja, for information)" if entry
                                  else "not priced" if not gear.get("unique") else "no market price found")})
                continue
            if gear.get("rare") and gear.get("price") is not None and gear["type"] not in ("Flask", "Charm"):
                gear["confidence"] = self.ctx.rare_model.confidence(slot)  # labels follow the price model
        for check in checks:
            if check["label"] == "Budget":
                low = [g["name"] for g in final["gear"].values() if g["confidence"] in ("low", "unknown")]
                check.update(delivered=f"{spent:.2f} divine", ok=spent <= budget + 1e-9,
                             note="poe.ninja prices" + (f"; low-confidence: {', '.join(low)}" if low else "")
                             + (f"; your own items not counted: {', '.join(o['name'] for o in owned)}" if owned else ""))
        # The sustain and recovery rows read the worse of the build as it is and the build with its part-time effects
        # off (floor_deltas, as the search's deficits and floors_at_full_uptime do; SEARCH22): with a part-time Enduring
        # Elixirs Life flask (or Melting Maelstrom) the stats as they are hold PoB's whole flask recovery plus ours
        # (flasks.pob_counts_whole's note, MECHC25), counted twice; with the slot off ours alone is its time-weighted share
        cases = [facts["stats"], *({**facts["stats"], **{k: number(facts["stats"].get(k)) + d for k, d in deltas.items()}}
                                   for deltas in final.get("floor_deltas") or [])]
        share, pool = min((sustain(case) for case in cases), key=lambda found: found[0])
        checks.append({"label": "Skill costs sustained", "requested": f"≥ {SUSTAIN_FLOOR:.0%} by recovery",
                       "delivered": "no costs to pay" if pool is None and share >= 1 else f"{min(share, 9.99):.0%}"
                       + (f" of {pool}" if pool else ""), "ok": share >= SUSTAIN_FLOOR,
                       "note": "regeneration, leech, on-hit recovery, flasks (at the rate charge generation "
                               "refills them in a boss fight), Recoup of the boss's hits and gains on Block, Life / Mana from "
                               "kills, Remnants, charms and events at the build's own rates, against the per-second "
                               "cost. Not counted yet (so the share may be low): " + SUSTAIN_LEFT_OUT})
        if self.request.floors.recovery is None:  # always reported; a requirement only when the request sets one
            recovery = min((boss_recovery(case) for case in cases), key=lambda found: found["share"])
            checks.append({"label": "Recovery vs boss damage %", "requested": "not required",
                           "delivered": recovery_text(recovery), "ok": None, "note": RECOVERY_NOTE})
        else:
            for check in checks:
                if check["label"] == "Recovery vs boss damage %":
                    check.update(delivered=recovery_text(min((boss_recovery(case) for case in cases),
                                                             key=lambda found: found["share"])), note=RECOVERY_NOTE)
        floors_at_full_uptime(checks, self.ctx.compiled.penalties, facts["stats"], final.get("floor_deltas") or [])
        failing = [c for c in checks if c["ok"] is False]
        reasons = [f"{c['label']}: requested {c['requested']}, delivered {c['delivered']}" for c in failing]
        if note := sustain_only_note([c["label"] for c in failing]):
            reasons.append(note)
        reasons += final.get("problems") or []
        if final["dps"] <= 0:
            # Nothing to maximize: PoB doesn't model this skill's damage (PLAN.md §10)
            reasons.append(f"PoB calculates no damage for {facts.get('mainSkill') or 'the main skill'} with any setup "
                           "tried, so its damage isn't modeled yet; it needs a ledger entry (Phase 4)")
        if not final["verified"]:
            reasons.append("the exported PoB code didn't reload to the same numbers: " + ", ".join(final["verify_mismatch"]))
        broken = (final.get("tree_check") or {}).get("broken") or []
        if broken:
            # Never expected: the search only allocates what PoB can path to; said loudly if it happens
            reasons.append("the passive tree breaks the game's allocation rules: "
                           + ", ".join(f"{b['name']} ({b['id']})" for b in broken[:8])
                           + (f" and {len(broken) - 8} more" if len(broken) > 8 else "")
                           + " can't be reached from the class start or through a socketed jewel")
        for jewel in final.get("radius_jewels") or []:
            if jewel.get("unconnected"):
                self.notes.append(f"{jewel['name']} (Jewel {jewel['socket']}) lets the tree take "
                                  f"{len(jewel['unconnected'])} passive{'s' if len(jewel['unconnected']) > 1 else ''} "
                                  "without a connection; taking the jewel out drops them")
        for o in final.get("open_sockets") or []:  # (SEARCH34: an open support socket is a red flag, said with its reason)
            self.notes.append(f"WARNING: {o['skill']}'s group ({o['kind']}) has {o['open']} empty support socket"
                              f"{'s' if o['open'] > 1 else ''}: " + "; ".join(o["reasons"]))
        left_out = final.get("left_out") or {}
        for reason in dict.fromkeys(left_out.values()):
            names = sorted(name for name, why in left_out.items() if why == reason)
            self.notes.append(f"Not proposed: {', '.join(names)}. {reason[0].upper()}{reason[1:]}.")
        if final.get("excluded_dps"):
            self.notes.append(f"PoB's Full DPS ({final['pob_full_dps']:,.0f}) also counts {final['excluded_dps']:,.0f} from "
                              f"effects that need enemies to die ({', '.join(final['excluded_from'])}); a single boss "
                              "never triggers them, so they're left out of the boss DPS above (the clear estimate "
                              "counts them).")
        if self.seed_start:
            start = self.seed_start
            taken = "; ".join(start["notes"])
            self.notes.append(f"Your PoB code measured {start['dps']:,.0f} boss DPS here, with only the conditions "
                              "the build sustains and the request applied" + (f" ({taken})" if taken else "")
                              + f". Its line of searches never ends below that; this result: {final['dps']:,.0f}.")
        for flask in final.get("flasks") or []:
            if flask.get("part_time"):  # (Search.weigh_uptimes: MECHC5's flask share-of-fight spec)
                self.notes.append(f"{flask['slot']} ({flask['name']}): its effect is up {flask['uptime']:.0%} of a boss "
                                  "fight, and the DPS above counts it at that share; the code leaves its slot switched "
                                  "on, so PoB itself counts it whole")
            if not flask["counted"]:
                why = (flask.get("full_pool") or "")  # (MECHC4: Life / Mana filling up ends it, not only its charges)
                why = (f"{why[0].upper()}{why[1:]}" if why else
                       f"Charge generation keeps its effect up {flask['uptime']:.0%} of a boss fight (no kills refill it)")
                self.notes.append(f"{flask['slot']} ({flask['name']}): {why}, so its effect isn't "
                                  "counted and its slot is left switched off in the code"
                                  + (f"; its {flask['pool']} recovery counts at {flask['recovery']:,.0f}/s"
                                     if flask["pool"] and flask["recovery"] > 0 else ""))
        self.notes += final.get("free_supports") or []  # (SEARCH36: plain notes, no asterisk: the game's own rule)
        for assumption in final.get("assumptions") or []:
            self.notes.append(f"* {assumption['label']}: {assumption['value']} (assumed, since PoB doesn't model it: "
                              f"{assumption['basis']}; confidence: {assumption['confidence']}). The PoB code doesn't "
                              "carry this, so PoB itself shows different numbers there.")
        if reasons:
            # Said plainly, before anything else: no build met every requirement
            self.notes.insert(0, f"No build that meets every requirement was found in this search; this is the closest "
                                 f"one ({len(reasons)} requirement{'s' if len(reasons) > 1 else ''} not met, listed below). The cheapest "
                                 "relaxations say what to change; more search time can also help.")
        result = self._base("ok" if not reasons else "infeasible")
        result.update({
            "feasible": not reasons,
            "reasons": reasons,
            "relaxations": relaxations(failing),
            "skeleton": final["skeleton"], "label": final["label"],
            "summary": {k: facts[k] for k in ("className", "ascendClassName", "level", "mainSkill", "allocated",
                                               "ascendancyAllocated")},
            "dps": {"full": final["dps"], "main": final["main_dps"], "pob_full": final.get("pob_full_dps"),
                    "excluded": final.get("excluded_dps"), "excluded_from": final.get("excluded_from"),
                    "added": final.get("added_dps") or {}},  # (damage computed outside PoB, by source: SEARCH14)
            # What was maximized, and the clear estimate beside boss DPS (PLAN.md §5.5)
            "objective": {"goal": self.request.objective, "value": final.get("value", final["dps"])},
            "clear": {**(final.get("clear_estimate") or {}), "approximate": True,
                      "basis": f"monster life cleared per second in a map pack of {PACK_SIZE} (PLAN.md §5.5); "
                               "PoB doesn't model clear speed"},
            "stats": facts["stats"], "checks": checks, "code": final["code"], "verified": final["verified"],
            "gear": final["gear"], "spent": spent, "owned": owned, "groups": final["groups"], "conditions": final["conditions"],
            "play_guide": final.get("play_guide") or [],
            "quests": final["quests"], "warnings": facts["warnings"], "skeleton_problems": problems,
            "assumptions": final.get("assumptions") or [], "flasks": final.get("flasks") or [],
            "seed": self.seed_start,
            "blind_spots": annotate_blind_spots(final.get("blind_spots") or []),
            "timing": final.get("timing"), "escalations": final.get("escalations"),
            "requirement_weights": final.get("multipliers"),
            "radius_jewels": final.get("radius_jewels") or [], "tree_check": final.get("tree_check"),
            "reapply_drift": final.get("reapply_drift"),  # (SEARCH32: inner.reapply_drift)
            "open_sockets": final.get("open_sockets") or [],  # (SEARCH34: Gems.open_sockets)
            "why_items": final.get("why_items") or {},  # (SEARCHC1: Gear.why_items, the per-slot "why this item")
            "final_regrow": final.get("final_regrow") or [],  # (SEARCH35: Tree.final_regrow's tries, what it kept)
            "unfinished": self.unfinished_rows(final),  # (SEARCHD1: the "Not finished" section, unfinished.py)
            "floor": getattr(self, "floor_check", None),  # (SEARCHD1: a Continue / focus run against its seed)
        })
        result["shopping"] = shopping_list(result, self.ctx.data, self.report_prices(), TradeClient().stats(offline=True),
                                           self.variant)
        result["checklist"] = checklist(result)
        return result

    def unfinished_rows(self, final: dict) -> list[dict]:
        """(SEARCHD1) What the time budget cut off in the delivered build, each row with its plain line (`text`): the run's
        own stages, the delivered line's state and finalize's rows (the rounds' unfinished.note), where the line's last
        pass and its gear round stopped, and support sockets finalize's fill ran out of time on."""
        best = self.best or {}
        state = best.get("state") or {}
        found = [r for r in self.unfinished]
        if self.should_cancel():
            found.append({"step": "search", "part": "the whole build", "done": None, "of": None, "unit": "",
                          "detail": "the search was stopped before its time was up; this is its best build so far"})
        resume = (final.get("line") or {}).get("resume") or (best.get("line") or {}).get("resume")
        if resume:
            found.append({"step": "search pass", "part": "the whole build", "done": None, "of": None, "unit": "",
                          "detail": f"the last pass over the build stopped at its '{resume}' step when time ran out, so "
                                    "the steps after it didn't run again on this build"})
        gear_resume = (best.get("gear") or {}).get("resume") if isinstance(best.get("gear"), dict) else None
        if gear_resume:
            found.append({"step": "gear round", "part": f"{gear_resume} and the slots after it", "done": None, "of": None,
                          "unit": "", "detail": "the last full gear round ran out of time before these slots; each kept "
                                                "the item it had"})
        focus = getattr(self.request, "focus", None)
        for o in final.get("open_sockets") or []:
            if focus is not None and (focus.kind != "group" or focus.skill != o.get("skill")):
                continue  # (a focus run's finalize has no fill time: only the focused group's sockets are its business)
            if any("ran out" in reason for reason in o.get("reasons") or []):
                found.append({"step": "supports", "part": f"{o['skill']}'s group", "done": None, "of": None, "unit": "",
                              "detail": f"{o['open']} support socket{'s' if o['open'] > 1 else ''} left open: the fill's "
                                        "time ran out before it tried everything"})
        # (the manager, 24 Nov 13:48: bridge JEWEL3's short-jewel notes, Jewels.unfinished_notes in 54427fd1, which reach
        # the result as assumptions labelled "Not finished: the <base> jewel in <slot> fills N of its M affix slots")
        for note in final.get("assumptions") or []:
            found_jewel = re.match(r"Not finished: the (.+) jewel in (.+) fills (\d+) of its (\d+) affix slots",
                                   str(note.get("label") or ""))
            if found_jewel:
                base, slot, filled, slots = found_jewel.groups()
                found.append({"step": "jewels", "part": slot, "done": int(filled), "of": int(slots), "unit": "affix slots",
                              "detail": f"the {base} jewel is kept as it is; " + str(note.get("basis") or "").split(
                                  ". A part-filled")[0].replace("It stopped because ", "it stopped because ")})
        rows = unfinished.merge(found, state.get("unfinished"), final.get("unfinished"))
        return [{**row, "text": unfinished.describe(row)} for row in rows]

    def report_prices(self) -> dict:
        return {"augments": self.ctx.augment_prices, "gems": self.ctx.gem_prices, "league": self.payload["league"],
                "emotions": self.ctx.emotion_prices,
                "rare_model": self.payload["rare_model"], "materials": self.payload.get("craft_prices"),
                "unique_versions": unique_versions.load(self.payload["league"])}

    def variant(self, name: str) -> str | None:
        price = self.price_table.unique(name) if self.price_table is not None else None
        return price.variant if price else None


MODEL_NAMES = {"archon": "Elemental Archon", "energy": "meta-skill Energy", "darkness": "Darkness",
               "traveller": "Traveller's Wisdom", "elemental_expression": "Elemental Expression",
               "leylines": "larger Time-Lost radius", "harmony": "Harmony Within",
               "resist_rings": "resistances per equipped item", "maelstrom": "Melting Maelstrom's pulses",
               "guided_hand": "Guided Hand's Ancestral Boost", "sanguine": "Sanguine Tides",
               "deadly_resolve": "Deadly Resolve's slashes"}
# Display names only, for the models the ledger names that MODEL_NAMES doesn't list (COVB4's three, passive_models.py,
# and others). MODEL_NAMES is also the list tests/test_engine_whatif.py checks for what-if coverage; these haven't been
# checked there yet (SEARCH4 routes it), so they stay out of it rather than claim a coverage nobody verified
MODEL_LABELS = {"no_mercy": "No Mercy's Hit share", "rhythm_of_ice": "Rhythm of Ice's echoed casts",
               "different_skills": "different skills used recently", "arcane_surge": "Arcane Surge's uptime",
               "charges": "charge generation", "debuff_uptimes": "debuff uptimes", "gem_stat": "gem stats PoB never reads",
               "glory": "Glory", "infusion_supply": "Infusion supply", "iron_ward": "Iron Ward",
               "marks": "Mark limit", "parry": "Parry", "rage": "Rage", "remnant_supply": "Remnant supply",
               "shocked_ground": "Shocked Ground", "usage": "skill usage", "wither": "Wither stacks"}


def model_name(model: str) -> str:
    """A custom model's name in the result (a model MODEL_NAMES doesn't name yet: its id with spaces)."""
    return MODEL_NAMES.get(model) or MODEL_LABELS.get(model) or model.replace("_", " ")


def annotate_blind_spots(lines: list[dict]) -> list[dict]:
    """Mark the ignored lines a ledger entry stands in for, and those a custom model reads."""
    from builder.ledger import model_entry, readable, translate

    annotated = []
    for line in lines:
        translated = translate(line["line"])
        if entry := model_entry(line["line"]):
            # builder/search/models.py: its effect is in the result's assumptions
            model = model_name(entry.raw["model"])
            line = {**line, "modeled_by": entry.raw["model"], "corrected_by": entry.id,
                    "as": [f"part of the {model} model (see the assumptions)"], "confidence": entry.confidence}
        # (a line off the passive tree shows its translation only where the ledger applies it on items: unconditional
        # lines and, since COV17, share: lines; flag:, if: and mult: lines apply on passives only. Before, share: lines
        # on items were shown as not applied: SEARCH13)
        elif translated and (translated[0].on_items or line["source"].startswith("passive:")):
            entry, replacement = translated
            line = {**line, "corrected_by": entry.id, "as": [readable(r) for r in replacement],
                    "confidence": entry.confidence}
            if not replacement and entry.raw.get("absorbed_by"):  # counted through an assumption already at its maximum
                line["as"] = [f"already counted at its maximum by an assumption: {entry.raw['absorbed_by']}"]
        annotated.append(line)
    return annotated


FOCUS_WRAP = 25.0  # (SEARCHD1) a focus run's finalize time: export, reload check, facts, the seed's floor beside it


# (SEARCHD1; the manager, 24 Nov 13:48: the floor for every seeded run) POE2_SEARCH_SEED_FLOOR=0 turns it off, for measuring
# its cost only
SEED_FLOOR = os.environ.get("POE2_SEARCH_SEED_FLOOR", "1") != "0"


def attempt_floor(run: Run) -> bool:
    """(SEARCHD1) Every run with a readable seed code (a pasted build, Continue) finalizes its seed beside the best: the
    floor it never falls below."""
    return SEED_FLOOR and bool(run.payload["request"].get("seed_code"))


def short_text(deficits: list[dict]) -> str:
    """(SEARCHD2) The requirements a build misses, in plain words: each by its label, a request floor with how far short
    (a share of the floor), a game limit and the default survive floor (soft, ranked after the rest) said as such."""
    parts = []
    for d in deficits:
        if d.get("hard"):
            parts.append(f"{d['label']} (a game limit)")
        elif d.get("hard") is None:
            parts.append(f"{d['label']} (the default survive floor, {number(d.get('amount')):.0%} short)")
        else:
            parts.append(f"{d['label']} ({number(d.get('amount')):.0%} short)")
    return "; ".join(parts) if parts else "none"


def replaced_seed_note(seed: dict, final: dict) -> str:
    """(SEARCHD2) Why the search's build was delivered instead of the pasted one: builds rank by how far they are short of
    the request's requirements first (game limits count ten times a floor; the soft survive floor not at all), then the
    request's goal (objective.rank_key), so a build that misses less wins even at lower DPS."""
    seed_short, final_short = shortfall(seed["deficits"]), shortfall(final["deficits"])
    seed_dps, final_dps = number(seed["dps"]), number(final["dps"])
    head = (f"The build you gave was {seed_short:.3g} short of the requirements; this one is {final_short:.3g} short, "
            f"at {final_dps:,.0f} DPS instead of {seed_dps:,.0f}.")
    if round(final_short, 6) < round(seed_short, 6):
        why = ("It is delivered instead because builds rank by how far they miss the requirements first (a game limit "
               "counts ten times a request floor) and on the request's goal (DPS, for a boss run) only after that")
        if final_dps < seed_dps:
            why += ", even at lower DPS"
        why += "."
    else:
        why = ("It is delivered instead because it misses the requirements no more and ranks higher on the default survive "
               "floor and the request's goal together.")
    return (f"{head} {why} Short in the build you gave: {short_text(seed['deficits'])}. Short in this one: "
            f"{short_text(final['deficits'])}.")


def tree_nodes(code: str | None) -> set[int]:
    """The allocated passives in a PoB code (its Spec's nodes)."""
    if not code:
        return set()
    match = re.search(r'<Spec\b[^>]*\bnodes="([^"]*)"', codes.decode(code))
    return {int(n) for n in match.group(1).split(",") if n.strip().isdigit()} if match else set()


def _gear_view(gear: dict | None) -> dict | None:
    if not gear:
        return None
    return {"name": gear.get("name"), "base": gear.get("base"), "affixes": gear.get("affixes") or [],
            "runes": gear.get("runes") or [], "price": gear.get("price")}


def _group_view(groups: list[dict] | None, index: int | None, skill: str | None) -> list[str] | None:
    group = next((g for g in groups or [] if g.get("index") == index), None) or next(
        (g for g in groups or [] if g.get("gems") and g["gems"][0].get("name") == skill), None)
    return [g.get("name") for g in group["gems"]] if group else None


def _gem_lists(groups: list[dict] | None, skip: int | None = None) -> list:
    """The player's own groups' gems, in order (an item- or passive-granted group comes with its item or passive)."""
    return [[x.get("name") for x in g.get("gems") or []] for g in groups or [] if not g.get("fromItem")
            and g.get("index") != skip]


def focus_report(focus: dict, report: dict, seed_final: dict | None, final: dict, ctx: Any) -> dict:
    """(SEARCHD1) The part's before / after, the whole build's DPS before / after, and any held part that differs between
    the seed as finalized and the delivered build (expected: none)."""
    before = seed_final or {}
    out = {**focus, **{k: report.get(k) for k in ("part", "passes", "converged", "undone", "rebuild", "kept_start",
                                                   "problem", "setup_seconds", "overtime")},
           "dps_before": before.get("dps"), "dps_after": final.get("dps"), "held_changed": []}
    kind = focus["kind"]
    index = (report.get("group_index") or focus.get("group")) if kind == "group" else None
    if kind == "slot":
        out["before"] = _gear_view((before.get("gear") or {}).get(focus["slot"]))
        out["after"] = _gear_view((final.get("gear") or {}).get(focus["slot"]))
    elif kind == "group":
        out["before"] = _group_view(before.get("groups"), index, focus.get("skill"))
        out["after"] = _group_view(final.get("groups"), index, focus.get("skill"))
    old, new = tree_nodes(before.get("code")), tree_nodes(final.get("code"))
    if kind == "tree":
        names = {n["id"]: n.get("name") for n in (getattr(getattr(ctx, "data", None), "tree", None) or {}).get("nodes", [])}
        out["before"], out["after"] = {"passives": len(old)}, {"passives": len(new)}
        out["added"] = sorted(names.get(i) or str(i) for i in new - old)
        out["removed"] = sorted(names.get(i) or str(i) for i in old - new)
    if not before:
        return out
    if kind != "tree" and old != new:
        out["held_changed"].append("passive tree")
    gear_a, gear_b = before.get("gear") or {}, final.get("gear") or {}
    for slot in sorted(set(gear_a) | set(gear_b)):
        if not (kind == "slot" and slot == focus["slot"]) and _gear_view(gear_a.get(slot)) != _gear_view(gear_b.get(slot)):
            out["held_changed"].append(f"item in {slot}")
    if _gem_lists(before.get("groups"), index) != _gem_lists(final.get("groups"), index):
        out["held_changed"].append("skill groups")
    return out


def relaxations(failing: list[dict]) -> list[str]:
    """The smallest change to the request that the delivered build would satisfy."""
    hints = []
    for check in failing:
        label, delivered = check["label"], check["delivered"]
        if label == "DPS gate":
            hints.append(f"lower the DPS gate to {delivered} (rares arrive in Phase 3)")
        elif check["requested"].startswith("≥"):
            hints.append(f"lower the {label.lower()} floor to {delivered}, the most found while meeting the other "
                         "requirements with uniques and passives (rare gear, Phase 3, can add more)")
        elif label == "Buttons":
            hints.append(f"allow {delivered.split()[0]} buttons")
        elif label == "Budget":
            hints.append(f"raise the budget to {delivered}")
        else:
            hints.append(f"relax '{label}' (delivered: {delivered})")
    return hints


def optimize(request: BuildRequest, **kwargs: Any) -> dict:
    """Run the optimizer on a request; see Run for the keyword arguments."""
    return Run(request, **kwargs).execute()
