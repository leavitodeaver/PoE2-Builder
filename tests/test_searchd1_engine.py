"""SEARCHD1 on real PoB: optimize one part (an item slot, a skill group, the tree) holds everything else exactly as the
seed has it and never ends below it; a Continue run and a one-part run never deliver less than their seed.

The user, 24 Nov 2026: "there should also be options to continue working on the overall build, or to optimize a single
item or skill or the tree"."""

from __future__ import annotations

import faulthandler
import re

import pytest

from builder import ROOT
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import make_payload

CODE = ROOT / "benchmarks" / "reference" / "2b75c.txt"


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def request(**extra) -> BuildRequest:
    return BuildRequest(class_name="Huntress", ascendancies=["Ritualist"], level=90, seed_code=CODE.read_text().strip(),
                        budget={"gear_tier": "endgame"}, objective="boss", **extra)


def tree_of(xml: str) -> set[str]:
    match = re.search(r'<Spec\b[^>]*\bnodes="([^"]*)"', xml)
    return set(match.group(1).split(",")) if match else set()


def groups_of(xml: str) -> list[list[str]]:
    return [re.findall(r'nameSpec="([^"]*)"', body) for body in re.findall(r"<Skill\b[^>]*>(.*?)</Skill>", xml, re.S)]


def items_of(state: dict) -> dict:
    return {slot: option.raw for slot, option in state["equipped"].items() if option is not None}


def held(result: dict, focus: dict) -> list[str]:
    """What changed between the seed's state and the focus task's, outside the focused part."""
    seed, after = result["seed_state"], result["state"]
    changed = []
    a, b = items_of(seed), items_of(after)
    changed += [f"item {s}" for s in sorted(set(a) | set(b)) if a.get(s) != b.get(s) and s != focus.get("slot")]
    if focus["kind"] != "tree" and tree_of(seed["xml"]) != tree_of(after["xml"]):
        changed.append("tree")
    ga, gb = groups_of(seed["xml"]), groups_of(after["xml"])
    if focus["kind"] != "group" and ga != gb:
        changed.append("groups")
    if focus["kind"] == "group":
        index = result["focus"]["group_index"]
        others = lambda gs: [g for i, g in enumerate(gs, start=1) if i != index]
        if others(ga) != others(gb):
            changed.append("other groups")
    return changed


@pytest.mark.own_pob_engine  # (a search's calls differ run to run)
@pytest.mark.parametrize("focus", [{"kind": "slot", "slot": "Gloves"}, {"kind": "group", "skill": "Explosive Spear"},
                                   {"kind": "tree"}], ids=["slot", "group", "tree"])
def test_one_part_moves_only_that_part_and_never_ends_below_the_seed(engine, focus):
    from builder.search.focus import focus_task
    from builder.search.inner import seed_task

    payload = make_payload(request(focus=focus))
    skeleton = seed_task(engine, None, payload)["skeleton"]
    result = focus_task(engine, None, payload, skeleton, 30.0, focus)
    report = result["focus"]
    assert not report.get("problem"), report
    assert result["score"] >= result["seed_start"]["score"] - 1e-6, (result["score"], result["seed_start"])
    assert held(result, focus) == [], (held(result, focus), report["undone"])
    assert report["passes"] >= 1
    # (finalize gets every slot locked: its empty-slot filler must not touch a held slot)
    assert set(result["state"]["locked"]) >= set(result["state"]["equipped"])
    if not report["converged"]:
        assert any(r["step"] == "optimize one part" for r in result["unfinished"])


def test_a_missing_part_says_so_and_changes_nothing(engine):
    from builder.search.focus import focus_task
    from builder.search.inner import seed_task

    focus = {"kind": "group", "skill": "Not A Skill In This Build"}
    payload = make_payload(request(focus=focus))
    skeleton = seed_task(engine, None, payload)["skeleton"]
    result = focus_task(engine, None, payload, skeleton, 5.0, focus)
    assert "isn't in this build" in result["focus"]["problem"]
    assert held(result, {"kind": "tree"}) == [] and tree_of(result["state"]["xml"]) == tree_of(result["seed_state"]["xml"])


@pytest.mark.own_pob_engine
def test_a_continued_run_never_returns_less_than_its_seed():
    """A whole Continue run (the pool, the search, finalize): its delivered build ranks at least as high as its seed as
    finalized beside it, and the floor's check ran (seed_final)."""
    from builder.search.run import Run

    run = Run(request(origin={"kind": "continue"}), workers=2, seconds=40)
    result = run.execute()
    assert result.get("code"), result.get("reasons")
    floor = result["floor"]
    assert floor is not None, result["notes"]  # (the seed was finalized beside the search's best)
    assert floor["delivered_key"] <= floor["seed_key"], floor  # (rank_key: smaller is better)
    if not floor["kept_seed"]:
        assert result["dps"]["full"] == floor["searched_dps"]


@pytest.mark.own_pob_engine
def test_a_one_part_run_delivers_before_and_after_and_holds_the_rest():
    from builder.search.run import Run

    run = Run(request(focus={"kind": "slot", "slot": "Gloves"}, origin={"kind": "focus"}), workers=2, seconds=45)
    result = run.execute()
    assert result.get("code"), result.get("reasons")
    focus = result["focus"]
    floor = result["floor"]
    print("one part, Gloves:", {k: focus.get(k) for k in ("dps_before", "dps_after", "passes", "undone", "overtime")},
          "floor:", floor, "why:", [n for n in result["notes"] if n.startswith("The build you gave was")])
    assert focus["held_changed"] == [], focus
    # What the code guarantees is RANK, not DPS (SEARCHD2; TRAIN 143's POE2T5753: 481,942 delivered against the seed's
    # 501,578): the part's moves are kept on the score, which counts the request's floors, and the floor delivers the
    # search's build when it ranks at least as high (objective.rank_key: fewer requirements short first, then the goal).
    # When the DPS falls the result says why in plain words.
    assert floor is not None and floor["delivered_key"] <= floor["seed_key"], floor
    if focus["dps_after"] < focus["dps_before"] - 1e-6:
        assert any(n.startswith("The build you gave was") for n in result["notes"]), result["notes"]
    assert "before" in focus and "after" in focus
