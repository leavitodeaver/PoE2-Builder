"""SEARCH42 (the manager's brief, 26 Nov: AUDIT40 A40-52, a side totem's placement charged in UPKEEP_PRESS)."""
from types import SimpleNamespace


def test_a_side_totem_is_charged_once_per_its_totem_duration_at_its_placement_time():
    """Shockwave Totem kept placed (one totem), re-placed as it expires: once per its 8 s totem duration, 0.6 s of the
    player's time a placement (PoB's TotemPlacementTime), whatever the totem's own Shockwave Slam rate is."""
    from builder.search.inner import upkeep_use

    row = {"name": "Shockwave Totem", "speed": 1.4, "castRate": 1.4, "totemDuration": 8.0, "totemPlacementTime": 0.6}
    uses, how, seconds = upkeep_use(row)
    assert abs(uses - 1 / 8.0) < 1e-12 and seconds == 0.6 and "totem duration" in how
    # (no totem duration read: no data sets how often it is placed, not charged)
    assert upkeep_use({**row, "totemDuration": None})[0] == 0.0
    # (not a totem: press_use_rate / press_action as before, e.g. Thunderstorm once per its duration)
    uses, _, seconds = upkeep_use({"name": "Thunderstorm", "speed": 1.389, "castRate": 1.389, "duration": 4.39})
    assert abs(uses - 1 / 4.39) < 1e-9 and abs(seconds - 1 / 1.389) < 1e-9


def test_totems_are_no_longer_left_out_of_upkeep():
    """(A40-52) Totem / SummonsTotem skills were skipped on an unverified 'another model charges them'."""
    from builder.search.inner import UPKEEP_TYPES_CHARGED

    assert not {"Totem", "SummonsTotem"} & UPKEEP_TYPES_CHARGED
    assert {"AppliesCurse", "Mark", "Warcry"} <= UPKEEP_TYPES_CHARGED


def _candidates(rows, main_index=1):
    from builder.search.inner import Search

    groups = [{"index": 1, "gems": [{"name": "Earthquake"}]},
              {"index": 2, "gems": [{"name": "Shockwave Totem"}]},
              {"index": 3, "gems": [{"name": "Dark Effigy"}]}]
    types = {"Shockwave Totem": ["Totem", "SummonsTotem", "Duration"], "Dark Effigy": ["Totem", "SummonsTotem"],
             "Earthquake": ["Attack"]}

    def call(what, **kw):
        if what == "groups":
            return {"mainSocketGroup": main_index, "groups": groups}
        assert what == "useRates"
        return {"rates": [r for r in rows if r["index"] in kw["indices"]]}

    search = object.__new__(Search)
    search.build = SimpleNamespace(call=call, objective=SimpleNamespace(),
                                   ctx=SimpleNamespace(any_gems={n: {"skillTypes": t} for n, t in types.items()},
                                                       all_gems=None))
    search.gems = SimpleNamespace(buttons=lambda: ["Earthquake", "Shockwave Totem", "Dark Effigy"])
    return search.upkeep_candidates()


def test_upkeep_candidates_take_a_side_totem_with_a_duration_and_placement_time():
    rows = [{"index": 2, "name": "Shockwave Totem", "speed": 1.2, "totemDuration": 8.0, "totemPlacementTime": 0.6},
            {"index": 3, "name": "Dark Effigy", "speed": 0.9, "totemPlacementTime": 0.5}]  # (no duration read)
    assert set(_candidates(rows)) == {"Shockwave Totem"}
    # (the main group's totem is the damage itself: never charged)
    rows[0]["index"] = 1
    assert _candidates(rows, main_index=1) == {}


def test_searchb10_polish_hook_runs_only_with_its_switch(monkeypatch):
    """(SEARCHB10's hook, 096af924) finalize_task calls the polish after the final regrow and before the open sockets;
    with POE2_SEARCH_POLISH off, or a tree without Gear.polish, nothing runs."""
    import inspect
    from builder.search import gear as gear_module, inner

    src = inspect.getsource(inner.finalize_task)
    assert src.rfind('"final regrow"') < src.find("polish_finished(") < src.find('"open sockets"')
    calls = []
    search = SimpleNamespace(weigh_uptimes=lambda: calls.append("weigh"), build=SimpleNamespace(score=1.0),
                             gear=SimpleNamespace(polish=lambda deadline, reweigh: calls.append("polish")))
    trace = []
    monkeypatch.delattr(gear_module, "polish_on", raising=False)
    assert inner.polish_finished(search, 60.0, trace) is False and calls == [] and trace == []
    monkeypatch.setattr(gear_module, "polish_on", lambda: False, raising=False)
    monkeypatch.setattr(gear_module, "polish_seconds", lambda s: min(60.0, s), raising=False)
    assert inner.polish_finished(search, 60.0, trace) is False and calls == [] and trace == []
    monkeypatch.setattr(gear_module, "polish_on", lambda: True, raising=False)
    assert inner.polish_finished(search, 60.0, trace) is True
    assert calls == ["weigh", "polish"] and trace == [["gear polish", 1.0]]


class _Any:
    """Any attribute is a do-nothing callable (the steps' functions; the patched step never calls them)."""

    def __getattr__(self, name):
        return lambda *a, **k: False


def _pass_search(monkeypatch, tail, step_seconds=10.0):
    from builder import clock
    from builder.search import inner

    now = [0.0]
    monkeypatch.setattr(clock, "now", lambda: now[0])
    monkeypatch.setattr(inner, "TASK_TAIL", tail)
    calls = []

    def step(self, phase, fn, *args):
        calls.append((phase, args))
        now[0] += step_seconds
        return False
    monkeypatch.setattr(inner.Search, "step", step)
    search = object.__new__(inner.Search)
    for name in ("gear", "tree", "gems", "jewels", "sources"):
        setattr(search, name, _Any())
    search.build = SimpleNamespace(equipped={})
    search.skeleton = SimpleNamespace(swap=None)
    search.kicked, search.passes, search.passes_done, search.line_passes = set(), 0, 0, 1  # (not a sweep pass)
    search.resume_step = search.current_step = None
    search.task_start, search.tail_done = 0.0, False
    return search, calls, now


def test_task_tail_off_runs_the_pass_as_before(monkeypatch):
    search, calls, _ = _pass_search(monkeypatch, [])
    search.blocks(100.0)
    phases = [c[0] for c in calls]
    assert not any("(tail)" in p for p in phases)
    assert phases[:3] == ["gear", "supports", "tree rebuild"] and "ascendancy" not in phases  # (not a sweep)
    assert dict(calls)["jewels"][0] == 100.0  # (the steps keep the task's deadline)


def test_task_tail_runs_the_unreached_steps_in_the_time_kept_back_once(monkeypatch):
    """A 100 s task keeps 15 s back: the steps stop at 85 s; press rates ran (70-80 s), prune and regrow didn't: it runs
    in the tail, and the line's pass resumes where it stopped."""
    search, calls, now = _pass_search(monkeypatch, ["press rates", "prune and regrow"])
    search.blocks(100.0)
    phases = [c[0] for c in calls]
    assert phases[-1] == "prune and regrow (tail)" and "press rates (tail)" not in phases
    assert "press rates" in phases and "prune and regrow" not in phases
    assert search.resume_step == "granted metas" and search.tail_done
    assert dict(calls)["jewels"][0] == 85.0  # (the steps stop at the tail's limit)
    assert dict(calls)["prune and regrow (tail)"][0] == 100.0
    # (once per task: the next pass in the same task runs as with the tail off)
    calls.clear()
    now[0] = 0.0
    search.resume_step = None
    search.blocks(100.0)
    assert not any("(tail)" in c[0] for c in calls) and dict(calls)["jewels"][0] == 100.0


def test_late_sweep_reruns_the_sweep_only_steps_in_order_until_its_deadline(monkeypatch):
    from builder.search import inner

    search, calls, now = _pass_search(monkeypatch, [])
    assert inner.LATE_SWEEP is False  # (OFF by default)
    assert search.late_sweep(1000.0) == []
    assert [c[0] for c in calls] == [f"{p} (late)" for p in inner.SWEEP_ONLY]
    calls.clear()
    now[0] = 0.0
    search.late_sweep(25.0)  # (10 s a step: three run, 0-30 s)
    assert [c[0] for c in calls] == [f"{p} (late)" for p in inner.SWEEP_ONLY[:3]]
    src = __import__("inspect").getsource(inner.finalize_task)
    assert src.find('trace.append(["polish"') < src.find("search.late_sweep(") < src.find("spend_ascendancy")


def test_judge_flags_a_case_that_moves_a_model_input():
    """(JUDGE, Phase 1 piece 1.1) on / off of any input; a rate / chance / cost input past JUDGE_TOL; a damage-size input
    (it scales with every damage support) only when it turns on or off."""
    from builder.search.state import JUDGE_TOL, judge_inputs_moved

    base = {"Speed": 1.0, "IgniteDPS": 100.0, "ManaPerSecondCost": 10.0, "BleedDPS": 0.0, "HitChance": 100}
    fields = list(base)
    assert judge_inputs_moved(base, dict(base), fields) == []
    assert judge_inputs_moved(base, {**base, "Speed": 1.0 + JUDGE_TOL / 2}, fields) == []
    assert judge_inputs_moved(base, {**base, "Speed": 1.1}, fields) == ["Speed"]
    assert judge_inputs_moved(base, {**base, "IgniteDPS": 180.0}, fields) == []  # (a damage size: not on its own)
    assert judge_inputs_moved(base, {**base, "IgniteDPS": 0.0}, fields) == ["IgniteDPS"]  # (turned off: Elemental Focus)
    assert judge_inputs_moved(base, {**base, "BleedDPS": 5.0}, fields) == ["BleedDPS"]  # (turned on: Bleed II)
    assert judge_inputs_moved(base, {**base, "ManaPerSecondCost": 12.0}, fields) == ["ManaPerSecondCost"]


def test_judge_is_off_by_default_and_only_then_widens_the_gem_what_if():
    from builder.search import state

    assert state.judge_enabled() is False
    assert "FullDPS" not in state.judge_fields() and "Speed" in state.judge_fields()
    seen = []
    build = object.__new__(state.Build)
    build._gem_trial = False
    build.objective = SimpleNamespace(fields=["CombinedDPS"], full=False)
    build.call = lambda method, **kw: seen.append(kw["fields"]) or {"results": []}
    build._scored = lambda results, fill, extras: []
    build._deltas("gemDeltas", [{"group": 1}])
    build._deltas("nodeDeltas", [{"node": 1}])
    assert seen == [["CombinedDPS"], ["CombinedDPS"]]


def test_judge_tries_the_top_by_what_if_and_every_flagged_case_on_our_score(monkeypatch):
    """Six support cases: the JUDGE_TOP best by what-if and the one flagged lower down get a real trial; the rest keep their
    what-if. The judge's row counts them."""
    from builder.search import state

    monkeypatch.setattr(state, "JUDGE_TOP", 2)
    monkeypatch.setenv(state.JUDGE_SWITCH, "1")
    group = {"index": 1, "gems": [{"name": "Spark", "support": False, "enabled": True, "level": 20, "quality": 0}]}
    names = ["S1", "S2", "S3", "S4", "S5", "S6"]
    cases = [{"group": 1, "slot": 2, "gem": n} for n in names]
    base = {"Speed": 1.0}
    scored = [(10.0 - i, {"Speed": 1.0}) for i in range(6)]
    scored[4] = (6.0, {"Speed": 2.0})  # (S5 moves a model input)
    tried = []
    build = object.__new__(state.Build)
    build.ctx = SimpleNamespace(gems={n: {"support": True} for n in names})
    build.groups = lambda: [group]
    build.started, build.judge_seconds, build.stopped, build.stop_at, build.phase = 0.0, 0.0, False, None, "supports"
    build.judge_log, build.gem_rescore_log, build.gem_rescored, build.gem_rescore_restores = [], [], 0, 0
    build.score = 0.0
    build.objective = SimpleNamespace(fields=[], prepare=lambda s: s, score=lambda s: 100.0)
    build.added_stats = lambda: {}
    build.snapshot = lambda: {}
    build.refresh = lambda: 0.0
    build.gem_settle = lambda: None

    def call(method, **kw):
        if method == "stats":
            return dict(base)
        if method == "setGroup" and len(kw["gems"]) > 1:
            tried.append(kw["gems"][1]["name"])
        return {}
    build.call = call
    monkeypatch.setattr(state.clock, "now", lambda: 1000.0)
    out = build._rescore_model_gems(cases, list(scored), judge=True)
    assert tried == ["S1", "S2", "S5"]
    assert [round(s, 1) for s, _ in out] == [100.0, 100.0, 8.0, 7.0, 100.0, 5.0]
    row = build.judge_log[-1]
    assert (row["cases"], row["flagged"], row["picks"], row["tried"], row["cut"]) == (6, 1, 3, 3, 0)
    assert row["moved"] == {"Speed": 1}


def test_judge_keeps_a_tree_commit_only_for_a_real_gain_on_the_settled_score(monkeypatch):
    """(JUDGE, the tree's keep / revert) a node whose what-if gained but our settled models value at 0 is reverted (2b75e's
    Marked for Sickness); a revert's removal is settled again; other phases and the switch off: Build.worse as before."""
    from builder.search import state

    build = object.__new__(state.Build)
    build.reverts, build.phase, build._gem_trial = {}, "tree", False
    build.judge_keeps = {"judged": 0, "reverted": 0, "seconds": 0.0}
    settled = {"score": 5.0}
    build.score = 5.2  # (the unsettled score after the commit: the frozen models)

    def settle():
        build.score = settled["score"]
    build.judge_settle = settle
    assert build.worse(5.0) is False  # (switch off: 5.2 isn't worse)
    monkeypatch.setenv(state.JUDGE_SWITCH, "1")
    assert build.worse(5.0) is True and build.score == 5.0  # (settled: no real gain -> reverted)
    settled["score"], build.score = 5.3, 5.2
    assert build.worse(5.0) is False and build.score == 5.3
    assert build.judge_keeps["judged"] == 2 and build.judge_keeps["reverted"] == 1 and build.reverts == {"tree": 1}
    build.phase, build.score = "jewels", 5.2
    assert build.worse(5.0) is False  # (not a judged phase)
    build.phase = "tree"
    calls = []
    build.call = lambda method, **kw: calls.append(method) or {}
    build._committed = lambda result: 4.0
    build.objective = SimpleNamespace(fields=[])
    settled["score"] = 4.5
    assert build.allocate(remove=[1]) == 4.5 and build.score == 4.5  # (the revert's removal settled)
    settled["score"] = 9.0
    build.allocate(add=[2])
    assert build.score == 4.5  # (an add isn't settled here: worse() judges it)
