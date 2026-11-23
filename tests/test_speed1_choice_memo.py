"""SPEED1 (proposal 3, the manager 23 Nov 07:30): Gems.choose_active_skill remembered by the state it was asked in
(gems.CHOICE_MEMO, POE2_SEARCH_CHOICE_MEMO): off by default; on, a state asked again gets the remembered end; "check"
runs the choice anyway and counts agreements. Boots one PoB engine for the module."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import gems as gems_module
from builder.search.context import Context, make_payload


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    engine = PobEngine()
    yield engine
    engine.close() if hasattr(engine, "close") else None
    if enabled:
        faulthandler.enable()


def two_skill_search(engine):
    """Spark with Mana Flare in its group (two calculated-skill options) and Frost Bomb beside it (an Infused set)."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    build.set_group(index=main["index"], gems=[{"name": "Spark", "level": 20, "quality": 0},
                                               {"name": "Mana Flare", "level": 20, "quality": 0}])
    build.set_group(gems=[{"name": "Frost Bomb", "level": 20, "quality": 0}])
    return search


def choice(build) -> tuple:
    group = build.main_group()
    return group["mainActiveSkill"], *((k.get("part"), k.get("statSet")) for k in group["skills"])


def test_off_by_default_nothing_is_remembered(engine, monkeypatch):
    monkeypatch.setattr(gems_module, "CHOICE_MEMO", "0")
    search = two_skill_search(engine)
    search.gems.choose_active_skill()
    assert not search.gems.__dict__.get("_choices") and "choice_memo" not in search.gems.__dict__


def repeat_until_hit(build, gems, start, rounds: int = 4) -> int:
    """Restore `start` and choose again until the memo answers (the resolver's own state settles over the first
    choices: `active` gained a condition across one in a probe); returns the round that hit, or 0."""
    for round_ in range(1, rounds + 1):
        hits = gems.choice_memo["hits"] if "choice_memo" in gems.__dict__ else 0
        build.restore(start)
        gems.choose_active_skill()
        if gems.choice_memo["hits"] > hits:
            return round_
    return 0


def test_a_state_asked_again_gets_the_first_choices_end(engine, monkeypatch):
    """The same state asked again (a trial taken back): the memo restores the remembered end, the same choice and score
    a real choice gives from that state, without trying every option again."""
    monkeypatch.setattr(gems_module, "CHOICE_MEMO", "check")
    search = two_skill_search(engine)
    build, gems = search.build, search.gems
    start = build.snapshot()
    gems.choose_active_skill()
    assert repeat_until_hit(build, gems, start) > 0
    real_choice, real_score = choice(build), build.score  # (check mode ran the choice for real)
    monkeypatch.setattr(gems_module, "CHOICE_MEMO", "1")
    tried = []
    real = gems._choose_active_skill
    monkeypatch.setattr(gems, "_choose_active_skill", lambda *a, **k: (tried.append(1), real(*a, **k))[1])
    hits = gems.choice_memo["hits"]
    build.restore(start)
    gems.choose_active_skill()
    assert gems.choice_memo["hits"] == hits + 1 and not tried
    assert choice(build) == real_choice and build.score == pytest.approx(real_score, abs=1e-6)


def test_check_mode_runs_the_choice_and_counts_agreement(engine, monkeypatch):
    monkeypatch.setattr(gems_module, "CHOICE_MEMO", "check")
    search = two_skill_search(engine)
    build, gems = search.build, search.gems
    start = build.snapshot()
    gems.choose_active_skill()
    assert repeat_until_hit(build, gems, start) > 0
    assert gems.choice_memo["agree"] == gems.choice_memo["hits"] >= 1 and gems.choice_memo["disagree"] == 0
    # A remembered end that differs from what the choice gives is counted and kept for reading
    for snap, _ in gems._choices.values():
        snap["choice"] = (99, 99, 99)
    build.restore(start)
    gems.choose_active_skill()
    assert gems.choice_memo["disagree"] == 1 and gems.choice_memo["differences"][0]["was"] == repr((99, 99, 99))


def test_the_key_moves_with_the_objectives_weights(engine, monkeypatch):
    """An escalation raises a requirement's weight: the best choice can change, so it is a different state."""
    monkeypatch.setattr(gems_module, "CHOICE_MEMO", "1")
    search = two_skill_search(engine)
    gems, objective = search.gems, search.build.objective
    before = gems.choice_key(True, ())
    assert before is not None and gems.choice_key(True, ()) == before
    objective.multipliers = {**(getattr(objective, "multipliers", None) or {}), "speed1-test": 2.0}
    assert gems.choice_key(True, ()) != before
    assert gems.choice_key(False, ()) != gems.choice_key(True, ())


def test_the_key_reads_any_dict_keys_and_sets_in_a_fixed_order():
    """POE2T7805: the scoring state's uptime_floor has frozenset keys, which json.dumps refuses (the memo's check mode
    stopped a gate run's reference scoring)."""
    from builder.search.gems import canonical
    import json

    a = {"uptime_floor": {frozenset({"b", "a"}): 1.0, frozenset({"c"}): 2.0}, "s": {3, 1, 2}, "t": (1, 2)}
    b = {"t": (1, 2), "s": {2, 3, 1}, "uptime_floor": {frozenset({"c"}): 2.0, frozenset({"a", "b"}): 1.0}}
    assert json.dumps(canonical(a)) == json.dumps(canonical(b))
    assert json.dumps(canonical({**a, "t": (2, 1)})) != json.dumps(canonical(a))


# Proposal 4 (run.FULL_LAST): only the last focus round scores Full DPS -------------------------------------------------


class Stop(Exception):
    pass


class FakePool:
    """Stands in for the engine pool: every search task returns a fixed result, and records its `full` flag; finalize
    stops the run (what it does is not under test)."""

    def __init__(self, workers: int) -> None:
        self.size, self.startup_seconds, self.replaced = workers, 0.0, 0
        self.full: list[tuple[float, bool]] = []

    def __enter__(self):
        FakePool.last = self
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def run_tasks(self, tasks, on_progress=None, should_cancel=None):
        out = []
        for name, args in tasks:
            if name.endswith("finalize_task"):
                raise Stop()
            self.full.append((args["seconds"], bool(args.get("full"))))
            out.append({"label": "x", "skeleton": args["skeleton"], "dps": 10.0, "value": 10.0, "score": 1.0,
                        "deficits": [], "evals": 1, "state": {"xml": "<x/>"}})
        return out


@pytest.mark.parametrize("switch", [False, True])
def test_full_dps_only_in_the_last_focus_round(monkeypatch, switch):
    from builder.search import run as run_module

    monkeypatch.setattr(run_module, "EnginePool", FakePool)
    monkeypatch.setattr(run_module, "FULL_LAST", switch)
    monkeypatch.setattr(run_module, "FOCUS_FULL_DPS", False)
    monkeypatch.setattr(run_module.Run, "skeletons", lambda self, pool: (
        __import__("builder.search.skeletons", fromlist=["enumerate_skeletons"]).enumerate_skeletons(
            self.ctx, ["Spark"])[0][:1], []))
    run = run_module.Run(BuildRequest(main_skill="Spark"), workers=2, seconds=600, cpu_clock=True)
    with pytest.raises(Stop):
        run.execute()
    flags = [full for _, full in FakePool.last.full]
    probes, focus = flags[:2], flags[2:]
    assert probes == [False, False] and len(focus) == 2 * run_module.FOCUS_ROUNDS
    assert focus[-2:] == [switch, switch] and not any(focus[:-2])

