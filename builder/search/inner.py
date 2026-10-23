"""The inner loop (PLAN.md §5.2), run inside a worker process next to its engine: one skeleton,
block coordinate ascent over gems, gear, ascendancy, tree, attributes, conditions and persistent
skills, then kicks (iterated local search) until the time is up.

Worker tasks (called through EnginePool.run_tasks):
- `optimize_task`: start a skeleton from scratch, or continue from a saved state
- `screen_task`: quick main-skill DPS for many skills, to shortlist when no main skill is given
- `finalize_task`: Full DPS polish, final facts, PoB code, and a reload check of that code
"""

from __future__ import annotations

import random
import time
from typing import Any

from builder.pob import codes
from builder.request.constraints import FACT_FIELDS
from builder.search.assumptions import Assumptions
from builder.search.conditions import Conditions
from builder.search.context import GEM_LEVEL, GEM_QUALITY, Context, ItemOption, context_for
from builder.search.gear import Gear, SlotSpec
from builder.search.gems import Gems
from builder.search.objective import Objective, main_dps, shortfall
from builder.search.skeletons import Skeleton
from builder.search.state import EPSILON, Build
from builder.search.tree import Tree

KICK_TREE_POINTS = (6, 16)
SWEEP_EVERY = 3
RESULT_FIELDS = sorted(set(FACT_FIELDS) | {
    "TotalDPS", "TotalDotDPS", "Spirit", "TotalEHP", "Speed", "CritChance", "HitChance",
})


class Search:
    def __init__(self, engine: Any, report: Any, payload: dict, skeleton: dict, seed: int, full: bool = False) -> None:
        self.ctx: Context = context_for(payload)
        self.skeleton = Skeleton(**skeleton)
        self.build = Build(engine, self.ctx, Objective(self.ctx, full), report, self.skeleton.label)
        self.rng = random.Random(seed)
        self.gems = Gems(self.build)
        self.tree = Tree(self.build, self.rng)
        weapon = SlotSpec(self.skeleton.weapon, self.skeleton.weapon_sub) if self.skeleton.weapon else None
        offhand = SlotSpec(self.skeleton.offhand) if self.skeleton.offhand else None
        self.gear = Gear(self.build, weapon, offhand, self.rng)
        self.conditions = Conditions(self.build, self.skeleton.main_skill)
        self.gems.conditions = self.conditions
        self.assumptions = Assumptions(self.build)
        self.gems.resolve = self.resolve
        self.gems.assumptions = self.assumptions
        self.tree.resolve = self.resolve
        self.tree.relevant_skill = self.assumptions.relevant
        self.build.after_load.append(self.assumptions.reinstall)
        self.problems: list[str] = []
        self.kicks = 0
        self.kicks_kept = 0
        self.escalations = 0
        self.timing: dict[str, list[float]] = {}  # phase -> [seconds, evaluations, gain]
        self.passes = 0
        self.kicked: set[str] = set()  # blocks a kick disturbed, swept fully on the next pass

    # Setup and state ---------------------------------------------------------------------------

    def setup(self, quests: bool = True) -> None:
        build, sk = self.build, self.skeleton
        build.phase = "setup"
        build.call("newBuild")
        build.call("setClass", className=sk.class_name, ascendancy=sk.ascendancy)
        build.call("setLevel", level=self.ctx.level)
        kind, source = sk.granted_by.split(":", 1) if sk.granted_by else (None, None)
        if kind is None:
            build.set_group(gems=[{"name": sk.main_skill, "level": GEM_LEVEL, "quality": GEM_QUALITY}], main=True,
                            fullDPS=True)
        elif kind == "passive":
            self.tree.required_names.add(source)
        for name in self.ctx.request.require.skills:
            if name != sk.main_skill:
                build.set_group(gems=[{"name": name, "level": GEM_LEVEL, "quality": GEM_QUALITY}], label="required")
        missing = self.gems.add_required_supports()
        self.problems += [f"required support {n} can't support {sk.main_skill}" for n in missing]
        self.problems += self.gear.setup([source] if kind == "unique" else [])
        self.tree.choose_default_attribute(sk.main_skill)
        self.problems += [f"required passive {n} isn't reachable" for n in self.tree.allocate_required(sk.class_name)]
        if kind is not None:
            self.adopt_granted_skill(source)
        self.resolve()
        self.gems.choose_active_skill()
        if quests:
            self.conditions.choose_quests()

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
            "shortlist": self.gems.shortlist, "gear_shortlist": self.gear.shortlist, "attribute": self.tree.attribute,
            "multipliers": dict(self.build.objective.multipliers), "assumed_mods": self.assumptions.mods,
            "required_names": sorted(self.tree.required_names),
            "problems": self.problems,
        }

    def load(self, state: dict) -> None:
        self.build.call("loadXml", xml=state["xml"])
        self.build.equipped = dict(state["equipped"])
        self.build.locked_slots = set(state["locked"])
        self.build.objective.multipliers = dict(state.get("multipliers") or {})
        self.assumptions.mods = list(state.get("assumed_mods") or [])
        self.assumptions.reinstall()
        self.build.refresh()
        self.tree.required = set(state["required"])
        self.tree.required_names = set(state.get("required_names") or self.tree.required_names)
        self.tree.attribute = state["attribute"]
        self.conditions.active = dict(state["active"])
        self.gems.shortlist = state["shortlist"]
        self.gear.shortlist = dict(state.get("gear_shortlist") or {})
        self.problems = list(state["problems"])

    def resolve(self) -> bool:
        """Conditions and assumptions follow the build; if an assumption changed (e.g. a Remnant
        source arrived), the main group's calculated skill is chosen again."""
        changed = self.conditions.resolve()
        if self.assumptions.apply():
            self.gems.choose_active_skill()
            changed = True
        return changed

    # Blocks ------------------------------------------------------------------------------------

    def step(self, phase: str, fn, *args) -> bool:
        self.build.phase = phase
        before, evals, started = self.build.score, self.build.evals, time.perf_counter()
        fn(*args)
        entry = self.timing.setdefault(phase, [0.0, 0, 0.0])
        entry[0] += time.perf_counter() - started
        entry[1] += self.build.evals - evals
        entry[2] += self.build.score - before
        return self.build.score > before + EPSILON

    def blocks(self, deadline: float) -> bool:
        """One pass over every block. Full sweeps (every support in every slot, every item, every
        ascendancy allocation) run every SWEEP_EVERY passes or after a kick touched that block;
        passes in between rescore shortlists."""
        sweep = self.passes % SWEEP_EVERY == 0
        self.passes += 1
        steps = [
            ("gear", self.gear.gear_round, (deadline, sweep or "gear" in self.kicked)),
            ("supports", self.gems.optimize_supports, (deadline, 12, sweep or "supports" in self.kicked)),
            ("ascendancy", self.tree.ascendancy_round, ()),
            ("tree", self.tree.grow, (deadline,)),
            ("attributes", self.tree.attributes_round, ()),
            ("conditions", self.resolve, ()),
            ("persistent", self.gems.optimize_persistent, (deadline,)),
            ("prune and regrow", self.tree.swap_round, (deadline,)),
        ]
        improved = False
        for phase, fn, args in steps:
            if time.perf_counter() > deadline:
                break
            if phase == "ascendancy" and not sweep:
                continue
            improved = self.step(phase, fn, *args) or improved
        self.kicked.clear()
        return improved

    def kick(self) -> None:
        self.kicks += 1
        self.build.phase = "kick"
        kind = self.rng.choice(["tree", "tree", "tree", "supports", "gear"])
        self.kicked.add(kind)
        if kind == "tree":
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

    def run(self, deadline: float) -> None:
        """Block coordinate ascent until nothing improves, then kick; keep the best build seen.

        Requirements come first: after any pass that ends with a requirement still short and no
        closer than the pass before, its weight goes up (Objective.escalate); on the second raise
        the passive tree is cleared and regrown under the new weights, since a tree grown for damage
        rarely walks over to, say, the Mana nodes one swap at a time."""
        best = None
        last_short = float("inf")
        while time.perf_counter() < deadline:
            improved = self.blocks(deadline)
            if time.perf_counter() >= deadline:
                break
            objective = self.build.objective
            short = shortfall([{"amount": a, "hard": h} for _, a, h in objective.deficits(self.build.stats)])
            if short > 0 and short >= last_short - 1e-4 and objective.escalate(self.build.stats):
                self.escalations += 1
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
            elif self.build.score < best["score"]:
                self.build.restore(best)
            self.kick()
        if best is not None and best["score"] > self.build.score:
            self.build.restore(best)
        self.resolve()

    # Results -----------------------------------------------------------------------------------

    def summary(self) -> dict:
        build = self.build
        return {
            "skeleton": self.skeleton.as_dict(), "label": self.skeleton.label, "score": build.score,
            "dps": build.objective.dps(build.stats), "main_dps": main_dps(build.stats),
            "deficits": [{"label": label, "amount": amount, "hard": hard}
                         for label, amount, hard in build.objective.deficits(build.stats)],
            "evals": build.evals, "commits": build.commits, "seconds": time.perf_counter() - build.started,
            "kicks": self.kicks, "kicks_kept": self.kicks_kept, "problems": self.problems, "reverts": build.reverts,
            "escalations": self.escalations, "multipliers": dict(build.objective.multipliers),
            "timing": {k: {"seconds": round(v[0], 2), "evals": v[1], "gain": round(v[2], 4)} for k, v in self.timing.items()},
        }


def optimize_task(engine: Any, report: Any, payload: dict, skeleton: dict, seconds: float, seed: int = 0,
                  state: dict | None = None) -> dict:
    deadline = time.perf_counter() + seconds
    search = Search(engine, report, payload, skeleton, seed)
    if state is None:
        search.setup()
    else:
        search.load(state)
    from builder.pob.pool import Cancelled

    try:
        search.run(deadline)
    except Cancelled:
        pass
    return {**search.summary(), "state": search.state()}


def screen_task(engine: Any, report: Any, payload: dict, skeletons: list[dict], seconds_each: float = 3.0) -> list[dict]:
    """Main-skill DPS with a starting weapon and greedy supports, no tree: a cheap shortlist."""
    from builder.pob.pool import Cancelled

    results = []
    for skeleton in skeletons:
        search = Search(engine, report, payload, skeleton, 0)
        try:
            search.setup(quests=False)
            search.gear.gear_round(time.perf_counter() + seconds_each)
            search.gems.optimize_supports(time.perf_counter() + seconds_each, rounds=4)
            results.append({"skeleton": skeleton, "dps": main_dps(search.build.stats), "score": search.build.score})
        except Cancelled:
            break
        except Exception as error:  # a skill PoB can't set up is simply not shortlisted
            results.append({"skeleton": skeleton, "dps": 0.0, "score": float("-inf"), "error": str(error)[:300]})
    return results


def finalize_task(engine: Any, report: Any, payload: dict, skeleton: dict, state: dict, seconds: float) -> dict:
    """Switch to Full DPS, include damaging non-main groups in it, polish briefly, then export
    the build and reload the exported code to prove it reproduces the same numbers."""
    from builder.pob.pool import Cancelled

    deadline = time.perf_counter() + seconds
    search = Search(engine, report, payload, skeleton, 1, full=True)
    search.load(state)
    build = search.build
    build.phase = "finalize"
    for group in build.groups():
        if group["main"] or group["fromItem"] or not group["enabled"] or group["fullDPS"]:
            continue
        before = build.objective.dps(build.stats)
        build.set_group(index=group["index"], fullDPS=True)
        if build.objective.dps(build.stats) <= before * (1 + 1e-9):
            build.set_group(index=group["index"], fullDPS=False)
    try:
        search.conditions.choose_quests()
        search.run(deadline)
    except Cancelled:
        pass
    search.resolve()

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
    search.assumptions.reinstall()
    reloaded = build.call("facts", fields=RESULT_FIELDS)
    mismatched = [k for k in RESULT_FIELDS if facts["stats"].get(k) != reloaded["stats"].get(k)]
    excluded, excluded_names = build.objective.excluded(build.stats)
    return {
        **summary, "facts": facts, "code": code, "groups": groups, "conditions": conditions, "quests": quests,
        "pob_full_dps": facts["stats"].get("FullDPS"), "excluded_dps": excluded, "excluded_from": excluded_names,
        "gear": {slot: option.describe() for slot, option in equipped.items()}, "spent": spent,
        "assumptions": assumptions,
        "verified": not mismatched, "verify_mismatch": mismatched,
    }


__all__ = ["Search", "optimize_task", "screen_task", "finalize_task", "ItemOption"]
