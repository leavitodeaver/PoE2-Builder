"""The inner loop (PLAN.md §5.2), run inside a worker process next to its engine: one skeleton,
block coordinate ascent over gems, gear, ascendancy, tree, attributes, conditions, condition
sources and persistent skills, then kicks (iterated local search) until the time is up.

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
from builder.request.constraints import FACT_FIELDS, meta_socketed
from builder.request.schema import WeaponSwap
from builder.search.assumptions import Assumptions
from builder.search.conditions import Conditions
from builder.search.context import GEM_LEVEL, GEM_QUALITY, Context, ItemOption, context_for
from builder.search.gear import Gear, SlotSpec, extra_ring_passive
from builder.search.gems import Gems
from builder.search.jewels import Jewels
from builder.search.models import MODEL_FIELDS
from builder.search.objective import CLEAR_FIELDS, DARKNESS_FIELDS, SUSTAIN_FIELDS, Objective, main_dps, shortfall
from builder.search.skeletons import Skeleton, is_button, offhand_types, seed_skeleton, swap_skills
from builder.search.sources import Sources
from builder.search.state import EPSILON, Build
from builder.search.tree import Tree
from builder.search.usage import Usage

KICK_TREE_POINTS = (6, 16)
SWEEP_EVERY = 3
RESULT_FIELDS = sorted(set(FACT_FIELDS) | set(SUSTAIN_FIELDS) | set(MODEL_FIELDS) | set(DARKNESS_FIELDS) | set(CLEAR_FIELDS) | {
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
        self.tree = Tree(self.build, self.rng)
        self.tree.class_name, self.tree.ascendancy = self.skeleton.class_name, self.skeleton.ascendancy
        weapon = SlotSpec(self.skeleton.weapon, self.skeleton.weapon_sub) if self.skeleton.weapon else None
        held = offhand_types(self.ctx, self.skeleton.weapon, self.skeleton.offhand)
        offhand = SlotSpec(held[0], None, held[1:]) if held else None
        sk = self.skeleton
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
        self.gems.conditions = self.conditions
        self.assumptions = Assumptions(self.build)
        self.gems.resolve = self.resolve
        self.gems.settle = self.settle
        self.gems.assumptions = self.assumptions
        self.tree.resolve = self.resolve
        self.tree.relevant_skill = self.assumptions.relevant
        self.gear.resolve = self.resolve
        self.usage = Usage(self.build, self.conditions, self.gear.flask_rate)
        self.gems.usage = self.usage
        self.sources = Sources(self.build, self.conditions, self.gems, self.tree, self.gear, self.jewels, self.resolve)
        self.build.after_load.append(self.assumptions.reinstall)
        self.build.after_load.append(self.conditions.sync)
        self.problems: list[str] = []
        self.seed_notes: list[str] = []  # what setup_seed took out of the PoB code, and why
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
        self.correct_passives()
        build.call("setClass", className=sk.class_name, ascendancy=sk.ascendancy)
        build.call("setLevel", level=self.ctx.level)
        kind, source = sk.granted_by.split(":", 1) if sk.granted_by else (None, None)
        if kind is None:
            build.set_group(gems=[{"name": sk.main_skill, "level": GEM_LEVEL, "quality": GEM_QUALITY}], main=True,
                            fullDPS=True)
        elif kind == "passive":
            self.tree.required_names.add(source)
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
        build.call("resetConfig")
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
            "shortlist": self.gems.shortlist, "gear_shortlist": self.gear.shortlist, "attribute": self.tree.attribute,
            "multipliers": dict(self.build.objective.multipliers), "assumed_mods": self.assumptions.mods,
            "extra_groups": [[k, v] for k, v in sorted(self.assumptions.extra_groups.items())],
            "required_names": sorted(self.tree.required_names), "sources": sorted(self.tree.sources),
            "extra_stats": dict(self.build.extra_stats),
            "problems": self.problems,
        }

    def load(self, state: dict) -> None:
        self.build.call("loadXml", xml=state["xml"])
        self.correct_passives()
        self.build.equipped = dict(state["equipped"])
        self.build.locked_slots = set(state["locked"])
        self.build.objective.multipliers = dict(state.get("multipliers") or {})
        self.assumptions.mods = list(state.get("assumed_mods") or [])
        self.assumptions.extra_groups = {k: v for k, v in state.get("extra_groups") or []}
        self.build.objective.extra = bool(self.assumptions.extra_groups)
        self.build.extra_stats = dict(state.get("extra_stats") or {})
        self.assumptions.reinstall()
        self.build.refresh()
        self.tree.required = set(state["required"])
        self.tree.required_names = set(state.get("required_names") or self.tree.required_names)
        self.tree.sources = set(state.get("sources") or [])
        self.tree.attribute = state["attribute"]
        self.conditions.active = dict(state["active"])
        self.gems.shortlist = state["shortlist"]
        self.gear.shortlist = dict(state.get("gear_shortlist") or {})
        self.problems = list(state["problems"])
        self.gear.sync_flasks()

    def settle(self) -> bool:
        """Conditions and assumptions follow the build."""
        changed = self.conditions.resolve()
        return self.assumptions.apply() or changed

    def resolve(self) -> bool:
        """Conditions and assumptions follow the build; if either changed (e.g. a Remnant or Frenzy
        Charge source arrived), the main group's calculated skill and stat set are chosen again."""
        changed = self.settle()
        if changed:
            self.gems.choose_active_skill()
        return changed

    def conditions_round(self) -> None:
        """Resolve, and choose the calculated skill and stat set again: which one scores best
        changes as the rest of the build grows (Explosive Spear's explosion overtakes its spear).
        Gems whose usage requirement lost its source go first (builder/search/usage.py)."""
        if self.gems.drop_unusable():
            return  # it resolved, which chose the stat set again
        if not self.resolve():
            self.gems.choose_active_skill()

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
            ("jewels", self.jewels.jewel_round, (deadline, sweep or "gear" in self.kicked)),
            ("conditions", self.conditions_round, ()),
            ("condition sources", self.sources.round, (deadline,)),
            ("persistent", self.gems.optimize_persistent, (deadline,)),
            ("prune and regrow", self.tree.swap_round, (deadline,)),
        ]
        if self.skeleton.swap:
            steps.insert(2, ("weapon sets", self.weapon_set_round, ()))
        improved = False
        for phase, fn, args in steps:
            if time.perf_counter() > deadline:
                break
            if phase in ("ascendancy", "condition sources") and not sweep:
                continue
            improved = self.step(phase, fn, *args) or improved
        self.kicked.clear()
        return improved

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
        kind = self.rng.choice(["tree", "tree", "tree", "supports", "gear"])
        self.kicked.add(kind)
        self.sources.forget()
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
        last_short = float("inf")
        while time.perf_counter() < deadline:
            improved = self.blocks(deadline)
            if time.perf_counter() >= deadline:
                break
            objective = self.build.objective
            short = shortfall([{"amount": a, "hard": h} for _, a, h in objective.deficits(self.build.stats)])
            if escalate and short > 0 and short >= last_short - 1e-4 and objective.escalate(self.build.stats):
                self.escalations += 1
                start = None  # the search now trades score for requirements on purpose
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
        self.resolve()

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
            "evals": build.evals, "commits": build.commits, "seconds": time.perf_counter() - build.started,
            "kicks": self.kicks, "kicks_kept": self.kicks_kept, "problems": self.problems, "reverts": build.reverts,
            "condition_sources": list(self.sources.kept),
            "escalations": self.escalations, "multipliers": dict(build.objective.multipliers),
            "timing": {k: {"seconds": round(v[0], 2), "evals": v[1], "gain": round(v[2], 4)} for k, v in self.timing.items()},
        }


def optimize_task(engine: Any, report: Any, payload: dict, skeleton: dict, seconds: float, seed: int = 0,
                  state: dict | None = None, goal: str | None = None) -> dict:
    """`goal` overrides the request's (a balanced run's boss-DPS searches); the result's `value` is
    on that goal, so the run ranks results on its own goal from `dps` and `clear`."""
    deadline = time.perf_counter() + seconds
    search = Search(engine, report, payload, skeleton, seed, goal=goal)
    start = None
    if state is not None:
        search.load(state)
    elif search.skeleton.seeded:
        # The search never ends below the PoB code it started from (PLAN.md §8, gate 4)
        search.setup_seed()
        start = search.build.snapshot()
    else:
        search.setup()
    from builder.pob.pool import Cancelled

    try:
        search.run(deadline, best=start)
    except Cancelled:
        if start is not None and start["score"] > search.build.score:
            search.build.restore(start)
    summary = search.summary()
    if start is not None:
        objective = search.build.objective
        summary["seed_start"] = {"dps": objective.dps(start["stats"]), "value": objective.value(start["stats"]),
                                 "score": objective.score(start["stats"]),
                                 "deficits": [label for label, _, _ in objective.deficits(start["stats"])],
                                 "notes": search.seed_notes}
    return {**summary, "state": search.state()}


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
            if blocked:  # PoB would calculate it at will; only a request that names it gets it anyway
                results.append({"skeleton": skeleton, "dps": 0.0, "value": 0.0, "score": float("-inf"),
                                "error": "can't be used at will: " + "; ".join(blocked)})
                continue
            search.setup(quests=False)
            search.gear.gear_round(time.perf_counter() + seconds_each)
            search.gems.optimize_supports(time.perf_counter() + seconds_each, rounds=4)
            build = search.build
            results.append({"skeleton": skeleton, "dps": main_dps(build.stats), "value": build.objective.value(build.stats),
                            "score": build.score})
        except Cancelled:
            break
        except Exception as error:  # a skill PoB can't set up is simply not shortlisted
            results.append({"skeleton": skeleton, "dps": 0.0, "value": 0.0, "score": float("-inf"), "error": str(error)[:300]})
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
        before = build.objective.value(build.stats)
        build.set_group(index=group["index"], fullDPS=True)
        if build.objective.value(build.stats) <= before * (1 + 1e-9):
            build.set_group(index=group["index"], fullDPS=False)
    try:
        search.conditions.choose_quests()
        search.run(deadline, escalate=False, feasible_first=True)
    except Cancelled:
        pass
    search.resolve()

    search.gear.sync_flasks()
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
    build.objective.prepare(facts["stats"])  # what the checks see: Darkness in maximum hit taken
    excluded, excluded_names = build.objective.excluded(build.stats)
    blind_spots = build.call("unsupportedInBuild")["lines"]
    # The tree as the exported code has it, checked against the game's allocation rules (bridge
    # treeCheck): every passive reachable from the class start or through a socketed jewel
    tree_check = reloaded["treeCheck"]
    return {
        **summary, "facts": facts, "code": code, "groups": groups, "conditions": conditions, "quests": quests,
        "pob_full_dps": facts["stats"].get("FullDPS"), "excluded_dps": excluded, "excluded_from": excluded_names,
        "clear_estimate": build.objective.clear(build.stats),
        "blind_spots": blind_spots,
        "gear": {slot: option.describe() for slot, option in equipped.items()}, "spent": spent,
        "assumptions": assumptions, "flasks": search.gear.flask_report(),
        "verified": not mismatched, "verify_mismatch": mismatched,
        "tree_check": tree_check, "radius_jewels": reloaded["radiusJewels"], "left_out": dict(search.jewels.left_out),
    }


__all__ = ["Search", "optimize_task", "screen_task", "finalize_task", "ItemOption"]
