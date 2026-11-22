"""SEARCHB2 (SEARCH-B lane): the final tree regrow on the finished gear (Tree.final_regrow): a regrow is kept only if it
scores higher AND is no further from the request (the shortfall); the floors are repaired by penalty per point."""
from __future__ import annotations

import faulthandler
import random

import pytest

from builder import clock
from builder.search import tree as tree_module
from builder.search.tree import Candidate, Tree


class FakeObjective:
    def __init__(self, build):
        self.build = build

    def deficits(self, stats):
        short = stats.get("short", 0.0)
        return [("Physical max hit", short, False)] if short > 0 else []

    def penalty(self, stats):
        short = stats.get("short", 0.0)
        return 0.0 if short <= 0 else 3.0 + 6.0 * short


class FakeBuild:
    """A build whose state is (score, short); snapshot / restore copy it."""

    def __init__(self, score=10.0, short=0.0):
        self.score, self.stats = score, {"short": short}
        self.objective = FakeObjective(self)
        self.ctx = None
        self.restores = 0
        self.allocated: list[int] = []

    def snapshot(self):
        return {"score": self.score, "stats": dict(self.stats), "allocated": list(self.allocated)}

    def restore(self, snap):
        self.restores += 1
        self.score, self.stats, self.allocated = snap["score"], dict(snap["stats"]), list(snap["allocated"])


def make_tree(build) -> Tree:
    tree = Tree.__new__(Tree)
    tree.build, tree.rng, tree.candidates, tree.round = build, random.Random(0), {}, 0
    tree.allocations_since_full, tree.resolve = 0, None
    tree.points_left = lambda: 0
    return tree


def test_a_regrow_that_gains_damage_by_missing_a_floor_goes_back(monkeypatch):
    """G0b's controls (SEARCHB1): regrown from an empty tree, x1.52 / x2.50, but short on the Physical max hit floor.
    final_regrow keeps only a variant no further from the request, so that one is put back; one that gains with the
    floors met is kept."""
    build = FakeBuild(score=10.0, short=0.0)
    tree = make_tree(build)
    outcomes = {"all": (12.0, 0.2), "weak:30": (11.0, 0.0), "weak:15": (10.5, 0.0)}

    def variant(self, deadline, clear, target):
        assert target == 0.0  # (the floors are repaired to the best build's shortfall)
        build.score, build.stats = outcomes[clear][0], {"short": outcomes[clear][1]}

    monkeypatch.setattr(Tree, "regrow_variant", variant)
    monkeypatch.setattr(Tree, "swap_round", lambda self, deadline: False)
    rows = tree.final_regrow(float("inf"), ("all", "weak:30", "weak:15"))
    assert [r["kept"] for r in rows] == [False, True, False]  # (weak:15 scores under the kept weak:30)
    assert build.score == 11.0 and build.stats["short"] == 0.0 and build.restores == 2


def test_a_swap_that_gives_up_a_floor_is_put_back(monkeypatch):
    build = FakeBuild(score=10.0, short=0.0)
    tree = make_tree(build)
    monkeypatch.setattr(Tree, "regrow_variant", lambda self, d, c, t: None)

    def swap(self, deadline):
        build.score, build.stats = 13.0, {"short": 0.1}
        return True

    monkeypatch.setattr(Tree, "swap_round", swap)
    rows = tree.final_regrow(float("inf"), ())
    assert rows == [] and build.score == 10.0 and build.stats["short"] == 0.0


def test_floor_step_ranks_by_the_penalty_per_point_not_the_score(monkeypatch):
    """A damage target with the higher score and a floor target that cuts the penalty: floor_step takes the floor one."""
    build = FakeBuild(score=10.0, short=0.3)
    tree = make_tree(build)
    tree.points_left = lambda: 10
    tree.attribute = 1
    damage, floor = Candidate(1, (1,)), Candidate(2, (5, 2))
    monkeypatch.setattr(Tree, "frontier", lambda self, limit: [damage, floor])
    build.node_deltas = lambda cases: [(9.0, {"short": 0.3}), (8.0, {"short": 0.0})]
    calls = []
    build.allocate = lambda add, attribute: calls.append(add)
    monkeypatch.setattr(tree_module.clock, "now", lambda: 0.0)
    assert tree.floor_step(1.0) and calls == [[2]]


def test_each_variant_is_judged_after_the_uptimes_are_weighed_again(monkeypatch):
    """The floors read with the part-time effects off (objective.floor_deltas) are re-measured on each regrown tree
    (`reweigh`, the search's weigh_uptimes) before the variant is judged: here the reweigh finds the floor short."""
    build = FakeBuild(score=10.0, short=0.0)
    tree = make_tree(build)

    def variant(self, deadline, clear, target):
        build.score = 12.0

    def reweigh():
        build.stats = {"short": 0.1}

    monkeypatch.setattr(Tree, "regrow_variant", variant)
    monkeypatch.setattr(Tree, "swap_round", lambda self, deadline: False)
    rows = tree.final_regrow(float("inf"), ("all",), reweigh=reweigh)
    assert rows[0]["kept"] is False and build.score == 10.0 and build.stats["short"] == 0.0


def test_the_swaps_end_when_a_swap_gains_too_little(monkeypatch):
    """POE2T6221 (2b75d): the swaps ran to the deadline on gains under 0.01%. A swap gaining under SWAP_MIN_GAIN is kept
    (it is higher) and ends the swaps."""
    build = FakeBuild(score=10.0, short=0.0)
    tree = make_tree(build)
    monkeypatch.setattr(Tree, "regrow_variant", lambda self, d, c, t: None)
    gains = iter([0.01, 0.0001, 0.01])

    def swap(self, deadline):
        build.score += next(gains)
        return True

    monkeypatch.setattr(Tree, "swap_round", swap)
    rows = tree.final_regrow(float("inf"), ())
    assert len(rows) == 1 and abs(build.score - 10.0101) < 1e-9


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def test_a_final_regrow_on_a_real_build_never_ends_lower_or_shorter(engine):
    """Spark, its tree grown: a short final_regrow (the weakest 8 points cleared and regrown, then the swaps) ends with
    a score no lower and a shortfall no larger, with the tree inside the level's points."""
    from builder.request.schema import BuildRequest, Choices
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    tree = search.tree
    tree.grow(clock.now() + 30)
    search.weigh_uptimes()  # (final_regrow judges each try after the same reweigh)
    before, short = search.build.score, tree.shortfall()
    rows = tree.final_regrow(clock.now() + 40, ("weak:8",), reweigh=search.weigh_uptimes)
    print("PROBE final_regrow", before, short, rows)
    assert rows and rows[0]["clear"] == "weak:8"
    assert search.build.score >= before - 1e-6 and tree.shortfall() <= short + 1e-6 and tree.points_left() >= 0


# The joint tree + gear move (Tree.joint_regrow; SEARCHB3, SEARCHB2's design Step 2(c)) -----------------------------

class WeighingObjective:
    """deficits: a soft floor ("short") and a hard limit ("over"); penalty from _weight, as Objective's."""

    def deficits(self, stats):
        found = []
        if stats.get("short", 0.0) > 0:
            found.append(("Physical max hit", stats["short"], False))
        if stats.get("over", 0.0) > 0:
            found.append(("Spirit", stats["over"], True))
        return found

    def _weight(self, deficits):
        return sum((30.0 if hard else 6.0) * amount for _, amount, hard in deficits)

    def penalty(self, stats):
        return self._weight(self.deficits(stats))


def test_offence_only_drops_the_soft_floors_keeps_the_hard_limits_and_puts_the_score_back():
    build = FakeBuild()
    build.objective = WeighingObjective()
    refreshed = []
    build.refresh = lambda: refreshed.append(build.objective.penalty({"short": 1.0, "over": 0.1}))
    tree = make_tree(build)
    with tree.offence_only():
        assert build.objective.penalty({"short": 1.0, "over": 0.1}) == pytest.approx(3.0)  # (the Spirit only)
    assert build.objective.penalty({"short": 1.0, "over": 0.1}) == pytest.approx(9.0)
    assert refreshed == [pytest.approx(3.0), pytest.approx(9.0)] and "penalty" not in vars(build.objective)


def _joint_tree(monkeypatch, after_gear):
    """A tree whose clear / grow / repair only record; the gear round sets the build to `after_gear` (score, short)."""
    build = FakeBuild(score=10.0, short=0.0)
    build.objective = WeighingObjective()
    build.refresh = lambda: None
    tree = make_tree(build)
    calls = []
    monkeypatch.setattr(Tree, "clear_variant", lambda self, clear: calls.append(("clear", clear)))
    monkeypatch.setattr(Tree, "grow", lambda self, deadline: calls.append(
        ("grow", build.objective.penalty({"short": 1.0}))))
    monkeypatch.setattr(Tree, "repair_floors", lambda self, deadline, target: calls.append(("repair", target)))
    monkeypatch.setattr(Tree, "attributes_round", lambda self: False)

    def gear_round(deadline, full):
        calls.append(("gear", full))
        build.score, build.stats = after_gear[0], {"short": after_gear[1]}
        return True

    return tree, build, calls, gear_round


def test_a_joint_regrow_grows_for_damage_then_gears_and_is_kept_when_higher_with_the_floors_met(monkeypatch):
    tree, build, calls, gear_round = _joint_tree(monkeypatch, (12.0, 0.0))
    row = tree.joint_regrow(float("inf"), gear_round, "weak:30")
    assert calls == [("clear", "weak:30"), ("grow", 0.0), ("gear", True), ("repair", 0.0), ("grow", 6.0)]
    assert row["kept"] and row["gain"] == pytest.approx(2.0) and build.score == 12.0 and build.restores == 0


def test_a_joint_regrow_the_gear_cant_bring_back_to_the_floors_goes_back(monkeypatch):
    tree, build, calls, gear_round = _joint_tree(monkeypatch, (14.0, 0.3))  # (higher, but short on a floor)
    row = tree.joint_regrow(float("inf"), gear_round)
    assert not row["kept"] and build.restores == 1 and build.score == 10.0 and build.stats["short"] == 0.0


def test_a_joint_regrows_gear_round_gets_its_share_of_the_time(monkeypatch):
    tree, build, calls, _ = _joint_tree(monkeypatch, (9.0, 0.0))
    now = clock.now()
    seen = []
    tree.joint_regrow(now + 100.0, lambda deadline, full: seen.append(deadline - now) or False)
    assert seen and seen[0] <= tree.JOINT_GEAR_SHARE * 100.0 + 1.0 and build.restores == 1


def test_the_joint_regrow_switch_is_off_by_default_and_read_at_call_time(monkeypatch):
    """POE2_SEARCH_JOINT_REGROW (SEARCHB4; the manager, 22 Nov: land it OFF, the call site is SEARCH's)."""
    monkeypatch.delenv(Tree.SWITCH_JOINT, raising=False)
    assert Tree.SWITCH_JOINT == "POE2_SEARCH_JOINT_REGROW"
    assert not Tree.joint_enabled()
    monkeypatch.setenv(Tree.SWITCH_JOINT, "1")
    assert Tree.joint_enabled()
    monkeypatch.setenv(Tree.SWITCH_JOINT, "0")
    assert not Tree.joint_enabled()
