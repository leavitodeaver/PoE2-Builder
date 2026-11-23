"""SEARCHB5 (the manager's ruling 22 Nov: SEARCHB4's plan, on a loan of state.py's gem_deltas path): support what-ifs a
model values get a real trial.

A gem what-if reads only a support's PoB part: _scored adds the build's CURRENT model output to every case, so a support
valued by our models (GEM's gem_stat supports, condition sources, the meta Energy supports) reads +0 there (S2: Cadence
and Blazing Critical +0 by what-if, about +5% for real; Her Declaration +0 and +8.5%; POE2T6304 / 6295), and one the
models cut reads too high (Fist of Kalguur +0.90 vs +0.025). With POE2_SEARCH_GEM_RESCORE=1, Build.gem_deltas gives the
best few support cases by what-if and the model-valued ones a real trial (group set, settled, scored, put back).
"""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import state
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def spark(engine) -> Search:
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def gems_of(build, index: int) -> list[tuple]:
    group = next(g for g in build.groups() if g["index"] == index)
    return [(g["name"], g["level"], g["quality"], g["enabled"]) for g in group["gems"]]


def real_score(search: Search, index: int, case: dict) -> float:
    """The case committed for real (the same gems as the trial), settled and scored; then taken back."""
    build = search.build
    group = next(g for g in build.groups() if g["index"] == index)
    snap = build.snapshot()
    build.call("setGroup", index=index, gems=build._trial_gems(group, case), fields=[])
    search.settle()
    build.refresh()
    score = build.score
    build.restore(snap)
    return score


def test_gem_rescore(engine, monkeypatch):
    search = spark(engine)
    build = search.build
    assert build.model_gem is not None and build.gem_settle is not None  # (the hooks Search sets)
    main = build.main_group()
    index = main["index"]
    offered = [n for n in search.gems.candidates(index, build.groups()) if n not in {g["name"] for g in main["gems"]}]
    assert len(offered) >= 8
    # Two supports in, so there is something to take out
    build.set_group(index=index, gems=[*state.Build._group_gems(main), {"name": offered[0]}, {"name": offered[1]}])
    search.settle()
    build.refresh()
    before, gems_before = build.score, gems_of(build, index)
    slot = len(gems_before) + 1
    cases = [{"group": index, "slot": slot, "gem": n} for n in offered[2:8]]
    cases.append({"group": index, "slot": slot - 1, "remove": True})  # (offered[1] out)
    flagged = offered[7]

    # Off (the default): plain what-ifs, no trial
    monkeypatch.delenv(state.GEM_RESCORE_SWITCH, raising=False)
    off = [s for s, _ in build.gem_deltas(cases)]
    assert build.gem_rescored == 0

    # On, with only `flagged` model-valued and one best-what-if trial: those two (and the removal if it ranks best)
    monkeypatch.setenv(state.GEM_RESCORE_SWITCH, "1")
    monkeypatch.setattr(state, "RESCORE_GEMS", 1)
    build.model_gem = lambda name: name in (flagged, offered[1])
    on = [s for s, _ in build.gem_deltas(cases)]
    tried = {row["gem"]: row for row in build.gem_rescore_log}
    assert flagged in tried and tried[flagged]["model"] and "real" in tried[flagged]
    assert offered[1] in tried and tried[offered[1]]["remove"]  # (a model-valued support's removal gets one too)
    assert build.gem_rescored == len(tried) <= 3
    # Untried cases keep their what-if
    for i, case in enumerate(cases):
        name = case.get("gem") or offered[1]
        if name not in tried:
            assert on[i] == pytest.approx(off[i])
    # The build is where it was: the same gems, the same score
    assert gems_of(build, index) == gems_before
    assert build.score == pytest.approx(before, abs=1e-6)
    # A trial's score is the real (committed, settled) one
    i = next(i for i, c in enumerate(cases) if c.get("gem") == flagged)
    assert on[i] == pytest.approx(real_score(search, index, cases[i]), abs=1e-6)
    j = len(cases) - 1
    assert on[j] == pytest.approx(real_score(search, index, cases[j]), abs=1e-6)
    assert build.score == pytest.approx(before, abs=1e-6)


def test_model_valued_supports_names_the_modelled_ones():
    """GEM's gem_stat supports and the support-name constants the models read are in the net; a plain one isn't."""
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    ctx = Context(payload)
    valued = state.model_valued_supports(ctx)
    for name in ("Cadence", "Her Declaration", "Combo Finisher I", "Boundless Energy I"):
        if name in ctx.gems:
            assert valued(name), name
    assert not valued("Heft")
