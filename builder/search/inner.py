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
import os
import random
import re
import time
from typing import Any

from builder import clock
from builder.ledger.coverage import build_blind_spots
from builder.ledger.parse_audit import GATED_FIELDS
from builder.pob import codes
from builder.request.constraints import FACT_FIELDS, meta_socketed
from builder.request.schema import WeaponSwap
from builder.search.assumptions import Assumptions
from builder.search.conditions import (
    LOW_LIFE_LINE, LOW_LIFE_SHARE, MELEE_DISTANCE, STATIONARY_LINE, UPTIME, Conditions, distance_candidates,
)
from builder.search.context import GEM_LEVEL, GEM_QUALITY, Context, ItemOption, context_for
from builder.search.gear import Gear, SlotSpec, extra_ring_passive
from builder.search.gems import META_LABEL, Gems
from builder.search.jewels import Jewels
from builder.search import play_guide
from builder.search.models import GLORY_SKILLS, MARK_CARRIERS, MODEL_FIELDS
from builder.search.objective import (
    CLEAR_FIELDS, DARKNESS_FIELDS, RECOVERY_INPUTS, SUSTAIN_FIELDS, Objective, main_dps, number, shortfall,
    with_boss_recovery,
    with_life_es,
)
from builder.search.skeletons import Skeleton, is_button, offhand_non_unique, offhand_types, seed_skeleton, slot_bases, swap_skills
from builder.search.sources import Sources
from builder.search.state import EPSILON, Build, OutOfTime
from builder.search.tree import Tree
from builder.search.usage import Usage

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
        self.conditions = Conditions(self.build, self.skeleton.main_skill)
        self.play_notes: dict[str, dict] = {}  # notes from choose_stationary and weigh_life, weigh_uptimes, for the result
        self.uptime_rows: list[dict] = []  # weigh_uptimes' effects, for the play guide
        self.uptime_cache: dict[str, tuple[float, dict]] = {}  # var -> (DPS without it / with it, stats deltas) (follow_uptimes)
        self.uptime_floor: dict[frozenset, dict] = {}  # effects -> stats deltas with all of them off (floor_without)
        self.gems.conditions = self.conditions
        self.assumptions = Assumptions(self.build)
        self.build.reassume = self.assumptions.apply  # item what-ifs a model reads get a real trial (Build.item_deltas)
        self.gems.resolve = self.resolve
        self.gems.settle = self.settle
        self.gems.assumptions = self.assumptions
        self.tree.resolve = self.resolve
        self.tree.relevant_skill = self.assumptions.relevant
        self.tree.enforce = self.gear.enforce_restrictions
        self.tree.fill_granted = self.gems.fill_granted
        self.gear.fill_granted = self.gems.granted_meta_round
        self.gear.refill_main = self.gems.refill_main_supports
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
        self.resume_step: str | None = None
        self.resume_sweep = False
        self.tree_rebuilt = False  # M2: the line's tree rebuild was tried (carried in the line's state)
        self.current_step: str | None = None
        self.kick_kinds: dict[str, int] = {}
        self.kicked: set[str] = set()  # blocks a kick disturbed, swept fully on the next pass

    # Setup and state ---------------------------------------------------------------------------

    def setup(self, quests: bool = True) -> None:
        build, sk = self.build, self.skeleton
        build.phase = "setup"
        build.call("newBuild")
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
        self.tree.choose_default_attribute(sk.main_skill)
        self.problems += [f"required passive {n} isn't reachable" for n in self.tree.allocate_required(sk.class_name)]
        if kind is not None:
            self.adopt_granted_skill(source)
        self.resolve()
        self.gems.choose_active_skill()
        if quests:
            self.conditions.choose_quests()

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
        for group in build.groups():
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
        build.equipped = kept
        for slot, option in sorted(kept.items(), key=lambda x: -(x[1].price or 0.0)):
            if build.spent() <= ctx.budget + 1e-9:
                break
            if option.price:
                self.gear.put(slot, None)
                notes.append(f"took {option.name} out of {slot} (over the budget)")
        for name in dict.fromkeys(list(ctx.compiled.request.require.uniques)
                                  + ([ctx.request.weapon_unique] if ctx.request.weapon_unique else [])):
            held = next((s for s, o in build.equipped.items() if o.name == name), None)
            option = ctx.unique_option(name)
            slot = held or (self.gear.slot_for(option) if option else None)
            if slot is None:
                self.problems.append(f"required unique {name} fits no slot of this skeleton")
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
        self.problems += [f"required passive {n} isn't reachable" for n in self.tree.allocate_required(sk.class_name)]
        trimmed = 0
        while self.tree.points_left() < 0:
            ranked = self.tree.weakest_branches()
            if not ranked:
                break
            trimmed += len(ranked[0][1])
            self.tree.remove_branch(ranked[0][1])
        if trimmed:
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
            "extra_stats": dict(self.build.extra_stats),
            "infused": self.assumptions.infused, "infused_skipped": self.assumptions.infused_skipped,
            "problems": self.problems,
            "line": {"passes": self.line_passes, "step": self.resume_step, "sweep": self.resume_sweep,
                     "kicked": sorted(self.kicked), "rebuilt": self.gems.rebuilt,
                     "tree_rebuilt": self.tree_rebuilt} if PERSIST_LINES else None,
        }

    # The objective's per-build factors (not its requirement weights: an escalation raises those for the rest of the
    # search, and a restore must not undo it)
    SCORING_FIELDS = ("extra", "added_dps", "entry_scales", "press_scales", "life_factor", "uptime_factor", "floor_deltas")
    ASSUMPTION_FIELDS = ("mods", "active", "extra_groups", "stat_sets", "infused", "infused_skipped", "cooldown_fallback")

    def _scoring_state(self) -> dict:
        """What the score rests on outside PoB's XML, for Build.snapshot (Build.snapshot_state)."""
        objective, assumptions, conditions = self.build.objective, self.assumptions, self.conditions
        return copy.deepcopy({
            "objective": {k: getattr(objective, k) for k in self.SCORING_FIELDS},
            "assumptions": {k: getattr(assumptions, k) for k in self.ASSUMPTION_FIELDS},
            "uptimes": conditions.uptimes, "found_uptimes": getattr(conditions, "found_uptimes", None),
            "uptime_cache": self.uptime_cache, "uptime_floor": self.uptime_floor, "uptime_rows": self.uptime_rows,
            "play_notes": self.play_notes})

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

    def load(self, state: dict) -> None:
        self.build.call("loadXml", xml=state["xml"])
        self.correct_passives()
        self.build.equipped = dict(state["equipped"])
        self.build.locked_slots = set(state["locked"])
        self.build.objective.multipliers = dict(state.get("multipliers") or {})
        self.escalations = state.get("escalations", 0)
        self.assumptions.mods = list(state.get("assumed_mods") or [])
        self.assumptions.extra_groups = {k: v for k, v in state.get("extra_groups") or []}
        self.build.objective.extra = bool(self.assumptions.extra_groups)
        self.build.extra_stats = dict(state.get("extra_stats") or {})
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
        line = state.get("line") if PERSIST_LINES else None
        if line:
            self.line_passes, self.resume_step, self.resume_sweep = line["passes"], line["step"], line["sweep"]
            self.kicked.update(line.get("kicked") or [])
            self.gems.rebuilt = bool(line.get("rebuilt"))  # (the support rebuild: once per line, SEARCH_PLAN.md §6)
            self.tree_rebuilt = bool(line.get("tree_rebuilt"))
        self.gear.sync_flasks()

    def settle(self) -> bool:
        """Conditions and assumptions follow the build, and the part-time effects' weights follow them (follow_uptimes)."""
        changed = self.conditions.resolve()
        changed = self.assumptions.apply() or changed
        self.follow_uptimes()
        return changed

    def follow_uptimes(self) -> None:
        """Between conditions rounds, a commit or trial can switch on an effect kept up for part of the fight, or move an
        uptime. Its weight follows at once: each effect's ratio (DPS without it / with it) and floor deltas come from the
        last round (weigh_uptimes), and an effect the round didn't see gets its own one-off recalculation (cached until the
        next round), so a trial that raises an effect from 0% counts it at its share, not fully up (MECH31). The uptimes
        are the current ones; the ratios are the round's (an estimate between rounds; exact at finalize)."""
        objective, conditions = self.build.objective, self.conditions
        cache = self.uptime_cache
        live = {var: up for var, up in conditions.uptimes.items() if var in conditions.active}
        missing = [var for var in sorted(live) if var not in cache]
        if missing:
            on_stats = dict(self.build.stats)
            on = objective.dps(on_stats)
            if on <= 0:
                return
            for var in missing:
                cache[var] = _without(self, var, conditions.active[var], on_stats, on)[:2]
        factor = 1.0
        for var in sorted(live):
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
        candidates = distance_candidates(lines, conditions.main_types)
        before = conditions.distance
        if not candidates:
            conditions.distance = None
            if before is not None:
                self.settle()
            return before is not None
        natural = candidates[0]
        scores = {}
        for metres in candidates:
            conditions.distance = None if metres == natural else (metres, "")
            self.settle()
            scores[metres] = self.build.score
        best = max(candidates, key=lambda m: (scores[m], m == natural))
        tried = ", ".join(f"{m:g} m {scores[m]:,.0f}" for m in candidates)
        why = ("close range: the build leans into Presence or close-range lines" if best <= MELEE_DISTANCE
               else "beyond its \"further than\" lines") + f" (score at {tried}, defence floors included)"
        conditions.distance = None if best == natural else (best, why)
        self.settle()
        return conditions.distance != before

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

    def weigh_uptimes(self) -> None:
        """Effects the build keeps up for only part of the fight (conditions.uptimes: ailments, Onslaught, Arcane Surge,
        Armour Broken, Exposure, the debuffs) count at their real share (the user, 9 Nov 2026). With all of them on the
        DPS is D; one recalculation with effect c off gives r_c = D(c off) / D; the headline is D x the product of
        (u_c + (1 - u_c) x r_c) (objective.uptime_factor): n + 1 calculations, not 2^n, the effects taken as independent
        (labelled: effects that add into one pool, like Shock's and Intimidate's damage taken, interact a little). The
        floors are checked with all of them off at once (floor_without: one more recalculation), each one's time-weighted
        defence kept in its play note. Refreshed each conditions round and reused between them (as weigh_life); exact at finalize."""
        objective, conditions = self.build.objective, self.conditions
        objective.uptime_factor, objective.floor_deltas = 1.0, []
        self.uptime_rows, self.uptime_cache, self.uptime_floor = [], {}, {}
        for key in [k for k in self.play_notes if k.startswith("uptime:")]:
            del self.play_notes[key]
        if conditions.uptimes:
            on_stats = dict(self.build.stats)
            on = objective.dps(on_stats)
            factor = 1.0
            for var, up in sorted(conditions.uptimes.items()):
                info = conditions.active.get(var)
                if info is None or on <= 0:
                    continue
                ratio, deltas, off_stats = _without(self, var, info, on_stats, on)
                self.uptime_cache[var] = (ratio, deltas)
                share = up + (1.0 - up) * ratio
                factor *= share
                floors = [(p.label, number(on_stats.get(p.stat)), number(on_stats.get(p.stat)) + deltas[p.stat])
                          for p in objective.penalties if p.stat in deltas]
                defence = "; ".join(f"{label} {a:,.4g} with it, {b:,.4g} without, {up * a + (1 - up) * b:,.4g} "
                                    f"time-weighted (the floors are checked with "
                                    f"every part-time effect off)" for label, a, b in floors[:3])
                self.uptime_rows.append({"var": var, "label": info["label"], "uptime": up, "ratio": ratio, "factor": share,
                                         "defence": defence})
                self.play_notes[f"uptime:{var}"] = {
                    "label": f"{info['label']}: up {up:.0%} of the fight, counted at that share",
                    "value": f"x{share:.3f}",
                    "basis": f"{on:,.0f} DPS with it, {on * ratio:,.0f} without; the effects are weighed as independent "
                             f"of each other" + (f". Defences: {defence}" if defence else ""),
                    "confidence": "theory"}
            objective.uptime_factor = factor
            objective.floor_deltas = self.floor_without(
                {var: up for var, up in conditions.uptimes.items() if var in conditions.active}, on_stats)
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
            infos = {var: self.conditions.active[var] for var in names}
            self.uptime_floor[names] = _all_off(self, infos, stats)
        deltas = self.uptime_floor[names]
        return [deltas] if deltas else []

    def choose_play(self) -> bool:
        """The play-style choices and fight weights (play_guide's spec): distance, standing still, Low Life."""
        changed = self.choose_distance()
        changed = self.choose_stationary() or changed
        self.weigh_life()
        return changed

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
            ("ascendancy", self.tree.ascendancy_round, ()),
            ("supports", self.gems.optimize_supports, (deadline, 12, sweep or "supports" in self.kicked)),
            ("tree rebuild", self.tree_rebuild, (deadline,)),
            ("tree", self.tree.grow, (deadline,)),
            ("attributes", self.tree.attributes_round, ()),
            ("jewels", self.jewels.jewel_round, (deadline, sweep or "gear" in self.kicked)),
            ("conditions", self.conditions_round, ()),
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
        if self.skeleton.swap:
            steps.insert(3, ("weapon sets", self.weapon_set_round, ()))
        improved = False
        finished = True
        if resume is not None and resume in [phase for phase, _, _ in steps]:
            steps = steps[[phase for phase, _, _ in steps].index(resume):]
        for phase, fn, args in steps:
            if clock.now() > deadline:
                finished = False
                self.resume_step = phase  # (M1: the next task of this line starts here)
                break
            self.current_step = phase
            if phase in ("meta gems", "meta spells") and "meta" in self.kicked:
                pass  # a meta kick took the group out: the meta round re-chooses it now, not at the next sweep
            elif phase in ("ascendancy", "condition sources", "meta gems", "meta spells", "totems", "marks", "mirages", "roar") and not sweep:
                continue
            improved = self.step(phase, fn, *args) or improved
        self.current_step = None
        self.kicked.clear()
        self.passes_done += finished
        return improved

    def tree_rebuild(self, deadline: float) -> bool:
        """M2: once per line, from its second pass, clear the tree and regrow it against the build as it is now (the
        move Search.run makes at its second escalation); kept only if the build scores higher, else put back."""
        now = clock.now()
        if not TREE_REBUILD or self.tree_rebuilt or self.line_passes < 2 or deadline - now < TREE_REBUILD_MIN:
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
        lost = "a required passive had gone from the tree during the search; it was put back"
        if self.tree.ensure_required() and lost not in self.problems:
            self.problems.append(lost)
        self.resolve()

    def _ascend(self, deadline: float, start: dict | None, best: dict | None,
                escalate: bool) -> tuple[dict | None, dict | None]:
        """Search.run's loop: (best, start) as it leaves them; `self._kept` holds them for a stop (OutOfTime)."""
        self.deadline, self.boundary, self._kept = deadline, None, (best, start)
        self.stop_at = deadline + max(STOP_GRACE_MIN, STOP_GRACE_SHARE * max(0.0, deadline - clock.now()))
        last_short = float("inf")
        while clock.now() < deadline:
            improved = self.blocks(deadline)
            self.note_feasible()
            if clock.now() >= deadline:
                break
            objective = self.build.objective
            short = shortfall([{"amount": a, "hard": h} for _, a, h in objective.deficits(self.build.stats)])
            if escalate and short > 0 and short >= last_short - 1e-4 and objective.escalate(self.build.stats):
                self.escalations += 1
                start = None  # the search now trades score for requirements on purpose
                self._kept = (best, start)
                self.build.phase = "requirements"
                self.build.refresh()
                if best is not None:
                    best["score"] = objective.score(best["stats"])
                if self.escalations == 2:
                    self.tree.reset()
                self.kicked.update({"gear", "supports"})
                last_short = short
                continue
            last_short = short
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
            if build.objective.value(build.stats) <= before * (1 + 1e-9):
                build.set_group(index=group["index"], fullDPS=False)
                self.assumptions.rate_presses()

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
            "kicks": self.kicks, "kicks_kept": self.kicks_kept, "kick_kinds": dict(self.kick_kinds),
            "passes": self.passes, "passes_done": self.passes_done, "problems": self.problems, "reverts": build.reverts,
            "restore_drift": restore_drift(build),
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
    search.build.set_config({var: info.get("off", False)})
    search.assumptions.apply()
    off_stats = dict(search.build.stats)
    search.build.set_config({var: info["value"]})
    search.assumptions.apply()
    deltas = {k: number(v) - number(on_stats.get(k)) for k, v in off_stats.items()
              if isinstance(v, (int, float)) and number(v) != number(on_stats.get(k))}
    return max(objective.dps(off_stats), 0.0) / on, deltas, off_stats


def _all_off(search: Any, infos: dict[str, dict], on_stats: dict) -> dict:
    """One recalculation with every given part-time effect off, then back on: the stats that change (off - on)."""
    search.build.set_config({var: info.get("off", False) for var, info in infos.items()})
    search.assumptions.apply()
    off_stats = dict(search.build.stats)
    search.build.set_config({var: info["value"] for var, info in infos.items()})
    search.assumptions.apply()
    return {k: number(v) - number(on_stats.get(k)) for k, v in off_stats.items()
            if isinstance(v, (int, float)) and number(v) != number(on_stats.get(k))}


def optimize_task(engine: Any, report: Any, payload: dict, skeleton: dict, seconds: float, seed: int = 0,
                  state: dict | None = None, goal: str | None = None, full: bool = False) -> dict:
    """`goal` overrides the request's (a balanced run's boss-DPS searches); the result's `value` is
    on that goal, so the run ranks results on its own goal from `dps` and `clear`. `full`: scored on PoB's Full
    DPS, as the delivered build is, the damaging non-main groups included (the run's focus stage)."""
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
        start = search.build.snapshot()
    else:
        search.setup()
    if clock.now() > deadline:  # setup doesn't stop for the deadline: say so if it took the task's time
        search.overrun["setup"] = round(clock.now() - deadline, 2)
    from builder.pob.pool import Cancelled

    try:
        search.run(deadline, best=start or floor)
    except Cancelled:
        kept = start or floor
        if kept is not None and kept["score"] > search.build.score:
            search.build.restore(kept)
        search.keep_feasible()
    summary = search.summary()
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
    return {"boss": bosses.config_vars(name)}


def seed_task(engine: Any, report: Any, payload: dict) -> dict:
    """The skeleton of the request's PoB code, or why it can't be one."""
    ctx = context_for(payload)
    engine.call("loadXml", xml=codes.decode(ctx.request.seed_code))
    facts = engine.call("facts", fields=[])
    skeleton, notes = seed_skeleton(ctx, facts)
    return {"skeleton": skeleton.as_dict() if skeleton else None, "notes": notes}


def screen_task(engine: Any, report: Any, payload: dict, skeletons: list[dict], seconds_each: float = 3.0) -> list[dict]:
    """Main-skill DPS with a starting weapon and greedy supports, no tree: a cheap shortlist."""
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


def finalize_task(engine: Any, report: Any, payload: dict, skeleton: dict, state: dict, seconds: float) -> dict:
    """Switch to Full DPS, include damaging non-main groups in it, polish briefly, then export
    the build and reload the exported code to prove it reproduces the same numbers."""
    from builder.pob.pool import Cancelled

    deadline = clock.now() + seconds
    search = Search(engine, report, payload, skeleton, 1, full=True)
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
    search.choose_play()
    trace.append(["play", round(build.score, 4)])
    search.resolve()
    trace.append(["resolve", round(build.score, 4)])
    search.weigh_uptimes()  # exact for the finished build
    trace.append(["uptimes", round(build.score, 4)])

    search.gear.sync_flasks()
    search.gems.meta_candidates()  # the Meta gems the search can't propose yet, with why, for the result
    play = play_guide.guide(search)  # "How to play it": the DPS without each condition the player or fight decides
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
    build.call("loadXml", xml=codes.decode(code))
    # What PoB itself warns about in the code, without the search's assumptions (a skill on weapon
    # set 2 can be flagged only on a fresh load), joins the live build's warnings for the checks
    plain = build.call("facts", fields=[])["warnings"]
    search.assumptions.reinstall()
    reloaded = build.call("facts", fields=RESULT_FIELDS)
    mismatched = [k for k in RESULT_FIELDS if facts["stats"].get(k) != reloaded["stats"].get(k)]
    facts["warnings"] = list(dict.fromkeys([*facts["warnings"], *reloaded["warnings"], *plain]))
    facts["stats"].update(build.extra_stats)  # flask recovery, for the sustain check
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
    return {
        **summary, "facts": facts, "code": code, "groups": groups, "conditions": conditions, "quests": quests,
        "play_guide": play, "finalize_trace": trace, "finalize_drift": restore_drift(build),
        "pob_full_dps": facts["stats"].get("FullDPS"), "excluded_dps": excluded, "excluded_from": excluded_names,
        "clear_estimate": build.objective.clear(build.stats),
        "blind_spots": blind_spots,
        "gear": {slot: option.describe() for slot, option in equipped.items()}, "spent": spent,
        "assumptions": assumptions, "flasks": search.gear.flask_report(),
        "verified": not mismatched, "verify_mismatch": mismatched,
        "tree_check": tree_check, "radius_jewels": reloaded["radiusJewels"], "left_out": {**search.jewels.left_out, **search.gems.left_out},
    }


__all__ = ["Search", "optimize_task", "screen_task", "finalize_task", "ItemOption"]
