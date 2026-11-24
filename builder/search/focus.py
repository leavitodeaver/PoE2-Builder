"""Optimize one part of a build (SEARCHD1): one item slot, one skill group's supports, or the passive tree, with everything
else held exactly as the seed build has it; and the seed's own finalized result, the floor a Continue or focus run never
falls below.

THE USER, 24 Nov 2026 ~04:55: "there should also be options to continue working on the overall build, or to optimize a
single item or skill or the tree".

How a part is held: every step this module runs is checked against a fingerprint of the held parts (each item slot's
item, each skill group's gems, the allocated passives) taken before it; a step that changed a held part is undone
whole (`undone` in the result counts them). So "held" is checked, not assumed from what each round promises.

The rounds are the search's own (gear_round with its `only` slots, Jewels.fill, Gems' one-group support rounds, the tree's
grow / swap / ascendancy / attribute rounds), called here in a loop of passes until a pass gains nothing (the part is
done) or the time is up (the part isn't: an unfinished row says so). No kicks and no requirement raises: both reach
outside the part (a kick moves the tree or the gear, the second raise clears the tree).
"""

from __future__ import annotations

from typing import Any

from builder import clock
from builder.search import unfinished
from builder.search.state import EPSILON, OutOfTime

SLOT, GROUP, TREE = "slot", "group", "tree"
STOP_GRACE_MIN = 2.0  # (as inner.STOP_GRACE_MIN: the hard stop's grace past the deadline)
STOP_GRACE_SHARE = 0.01
MAX_PASSES = 50  # (a guard only: a pass that gains nothing ends the loop first)
# The part's own time at least, whatever loading and settling the seed took (POE2T8200: 2b75c's setup took all of a 25 s
# task on a loaded PC, so the part got no pass); a run that goes this far past its time says so (report "overtime")
MIN_PART_SECONDS = 20.0
TREE_REBUILD_SHARE = 0.4  # of the tree focus's time: one clear-and-regrow try, kept only if it scores higher


def fingerprint(search: Any) -> dict:
    """What a focus run holds: each slot's item text, each skill group's gems (name, level, quality, on), the passives."""
    build = search.build
    items = {slot: getattr(option, "raw", None) for slot, option in build.equipped.items() if option is not None}
    # (the player's own groups: one an item or passive grants comes and goes with that item or passive, the focused part)
    groups = {g["index"]: (tuple((x.get("name"), x.get("level"), x.get("quality")) for x in g["gems"]), g["enabled"])
              for g in build.groups() if not g.get("fromItem")}
    tree = frozenset(n["id"] for n in build.call("allocatedNodes")["nodes"])
    return {"items": items, "groups": groups, "tree": tree}


def held_changes(before: dict, after: dict, focus: dict, group_index: int | None = None) -> list[str]:
    """The held parts that differ between two fingerprints (the focused part's own changes are allowed)."""
    kind, slot = focus["kind"], focus.get("slot")
    changed = []
    for name in sorted(set(before["items"]) | set(after["items"])):
        if kind == SLOT and name == slot:
            continue
        if before["items"].get(name) != after["items"].get(name):
            changed.append(f"item in {name}")
    others = lambda groups: [v for i, v in sorted(groups.items()) if not (kind == GROUP and i == group_index)]
    if others(before["groups"]) != others(after["groups"]):  # (by order: a granted group in between may move indices)
        changed.append("skill groups")
    if kind != TREE and before["tree"] != after["tree"]:
        changed.append("passive tree")
    return changed


def group_index(search: Any, skill: str | None, index: int | None = None) -> int | None:
    """The focused group: by PoB's index if it still holds the skill, else the first group whose first gem is it."""
    groups = search.build.groups()
    if index is not None and any(g["index"] == index and g["gems"] and g["gems"][0]["name"] == skill for g in groups):
        return index
    return next((g["index"] for g in groups if g["gems"] and g["gems"][0]["name"] == skill), index if skill is None else None)


def part_label(focus: dict) -> str:
    if focus["kind"] == SLOT:
        return focus.get("slot") or "an item slot"
    if focus["kind"] == GROUP:
        return f"{focus.get('skill') or 'a skill'}'s group"
    return "the passive tree"


def focus_steps(search: Any, focus: dict, index: int | None) -> list[tuple[str, Any, tuple]]:
    """The rounds of one part, as (phase, function, arguments); `None` in the arguments is the deadline."""
    kind = focus["kind"]
    if kind == SLOT:
        slot = focus["slot"]
        if slot.startswith("Jewel "):
            return [("jewels", search.jewels.fill, (int(slot.split()[1]), None, True))]
        if " Jewel Socket " in slot:  # (an item's jewel socket: Jewels.item_sockets gives its bases)
            bases = dict(search.jewels.item_sockets()).get(slot)
            return [("jewels", search.jewels.fill_item_socket, (slot, bases, None))] if bases else []
        return [("gear", search.gear.gear_round, (None, True, [slot]))]
    if kind == GROUP:
        if index is None:
            return []
        main = search.build.main_group()
        if main is not None and main["index"] == index:
            return [("supports", search.gems.refill_main_supports, (None, 12)),
                    ("open sockets", search.gems.fill_open, (None, ("main",)))]
        return [("supports", search.gems.fill_granted_supports, (index, None, 12))]
    return [("ascendancy", search.tree.ascendancy_round, (None,)), ("tree", search.tree.grow, (None,)),
            ("attributes", lambda: search.tree.attributes_round(), ()),
            ("prune and regrow", search.tree.swap_round, (None,))]


def _call(fn: Any, args: tuple, deadline: float) -> Any:
    return fn(*(deadline if a is None else a for a in args))


def run_focus(search: Any, focus: dict, deadline: float) -> dict:
    """Passes of the part's rounds until one gains nothing or the time is up; each step that changed a held part undone.
    The build never ends below where it started (score), nor further from its requirements."""
    build = search.build
    index = group_index(search, focus.get("skill"), focus.get("group")) if focus["kind"] == GROUP else None
    steps = focus_steps(search, focus, index)
    start = build.snapshot()
    start_short = search.shortfall(build.stats)
    report = {"kind": focus["kind"], "part": part_label(focus), "passes": 0, "converged": False, "undone": [],
              "group_index": index, "steps": [s[0] for s in steps]}
    if not steps:
        report["problem"] = f"{part_label(focus)} isn't in this build (nothing to optimize)"
        return report
    if focus["kind"] == TREE and deadline - clock.now() > 30.0:
        _tree_rebuild(search, focus, deadline, report)
    search.deadline, search.boundary = deadline, None
    search.stop_at = deadline + max(STOP_GRACE_MIN, STOP_GRACE_SHARE * max(0.0, deadline - clock.now()))
    try:
        while clock.now() < deadline and report["passes"] < MAX_PASSES:
            report["passes"] += 1
            gained = False
            for phase, fn, args in steps:
                if clock.now() >= deadline:
                    break
                before, held = build.snapshot(), fingerprint(search)
                search.current_step = phase
                kept = search.step(phase, lambda: _call(fn, args, deadline))
                if changed := held_changes(held, fingerprint(search), focus, index):
                    build.restore(before)
                    search.boundary = build.snapshot()
                    report["undone"].append({"step": phase, "changed": changed})
                    continue
                gained = kept or gained
            if not gained and clock.now() < deadline:
                report["converged"] = True
                break
    except OutOfTime:
        if search.boundary is not None:
            build.restore(search.boundary)
    finally:
        build.stop_at = search.stop_at = None
        search.deadline, search.boundary, search.current_step = None, None, None
    short = search.shortfall(build.stats)
    if short > start_short + 1e-6 or (short >= start_short - 1e-6 and build.score < start["score"] - EPSILON):
        build.restore(start)  # (the seed is the floor: never further from the request, never lower)
        report["kept_start"] = True
    if not report["converged"]:
        unfinished.note(search, "optimize one part", report["part"], report["passes"], None, "passes",
                        "time ran out while it was still improving; it keeps the best it reached")
    return report


def _tree_rebuild(search: Any, focus: dict, deadline: float, report: dict) -> None:
    """The tree focus's one clear-and-regrow (inner.tree_rebuild's move), on TREE_REBUILD_SHARE of its time; kept only if
    the build scores higher, no further from its requirements and with every held part (items, jewels, groups) as it was."""
    build = search.build
    snap, held, short = build.snapshot(), fingerprint(search), search.shortfall(build.stats)
    try:
        search.tree.reset()
        search.tree.grow(clock.now() + TREE_REBUILD_SHARE * max(0.0, deadline - clock.now()))
        search.tree.attributes_round()
        search.resolve()
    except OutOfTime:
        pass
    kept = (build.score > snap["score"] + EPSILON and search.shortfall(build.stats) <= short + 1e-6
            and not held_changes(held, fingerprint(search), focus))
    report["rebuild"] = {"kept": kept, "score": round(build.score, 4), "before": round(snap["score"], 4)}
    if not kept:
        build.restore(snap)
        search.tree.candidates.clear()


def lock_all(state: dict, search: Any) -> dict:
    """The state with every slot locked, for finalize (its empty-slot filler and anything else that respects locks)."""
    return {**state, "locked": sorted(set(state.get("locked") or []) | set(search.gear.slots) | set(search.build.equipped))}


def focus_task(engine: Any, report: Any, payload: dict, skeleton: dict, seconds: float, focus: dict, seed: int = 0) -> dict:
    """The seed build (the request's PoB code) with one part optimized for `seconds`: the search's summary, its state
    (every slot locked, for finalize), the seed's own state (the floor's finalize) and the focus report."""
    from builder.search.inner import Search, use_cpu_clock

    use_cpu_clock(payload, engine)
    deadline = clock.now() + seconds
    search = Search(engine, report, payload, skeleton, seed, full=True)
    search.setup_seed()
    search.settle_still()
    search.weigh_uptimes()
    seed_state = lock_all(search.state(), search)
    start_stats = dict(search.build.stats)
    setup = clock.now() - (deadline - seconds)
    overtime = max(0.0, clock.now() + MIN_PART_SECONDS - deadline)
    found = run_focus(search, focus, deadline + overtime)
    found["setup_seconds"], found["overtime"] = round(setup, 1), round(overtime, 1)
    search.weigh_uptimes()
    summary = search.summary()
    objective = search.build.objective
    summary["seed_start"] = {"dps": objective.dps(start_stats), "value": objective.value(start_stats),
                             "score": objective.score(start_stats),
                             "deficits": [label for label, _, _ in objective.deficits(start_stats)], "notes": search.seed_notes}
    return {**summary, "state": lock_all(search.state(), search), "seed_state": seed_state, "focus": found,
            "unfinished": unfinished.rows(search)}


def seed_finalize_task(engine: Any, report: Any, payload: dict, skeleton: dict, state: dict | None = None) -> dict:
    """The seed build finalized with no polish (finalize_task at 0 s: export, reload check, facts), the floor a Continue
    or focus run is compared with. `state`: a seed state a focus task made; None: made here from the request's code."""
    from builder.search.inner import Search, finalize_task

    if state is None:
        search = Search(engine, report, payload, skeleton, 0, full=True)
        search.setup_seed()
        search.settle_still()
        state = lock_all(search.state(), search)
    return finalize_task(engine, report, payload, skeleton, state, 0.0)
