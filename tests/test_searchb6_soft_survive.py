"""SEARCHB6: the DEFAULT survive floor is SOFT (the manager's ruling, 23 Nov, on THE USER's "should still STRIVE to make
the build tanky enough to survive endgame maps and pinnacle fights", with an exception for builds that kill "faster than
they can retaliate"). The search strives for it (it costs the score as a floor does), but a build that can't meet it is
still delivered, with a plain warning naming each type it fails and by how much, never as infeasible. An EXPLICIT
max-hit floor in the request stays a requirement.

Before: a fresh Spark build scored -42.8 under the default floor (test_radius_jewels' disciple fixture, SEARCHB5's
POE2T6317) and every row it missed made the result infeasible."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.constraints import compile_request
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload
from builder.search.inner import RESULT_FIELDS, Search
from builder.search.objective import shortfall
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def data():
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


@pytest.fixture(scope="module")
def engine(data):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_default_survive_floor_is_soft_and_an_explicit_max_hit_floor_is_not(data):
    compiled = compile_request(BuildRequest(main_skill="Spark", level=90), data)
    soft = {p.label for p in compiled.soft_penalties}
    assert soft and all(label.startswith("Survives the boss's") for label in soft)
    assert not any(p.label.startswith("Survives") for p in compiled.penalties)  # (never a requirement)
    explicit = compile_request(BuildRequest(main_skill="Spark", level=90, floors={"max_hit": {"physical": 50000}}), data)
    assert any(p.label == "Physical max hit" for p in explicit.penalties)  # (the request's own: a requirement)
    assert "Survives the boss's Physical hit" not in {p.label for p in explicit.soft_penalties}
    off = compile_request(BuildRequest(main_skill="Spark", level=90, floors={"survive_boss_hits": False}), data)
    assert not off.soft_penalties


def spark(engine, **floors) -> Search:
    request = BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices(), level=90,
                           floors={"movement_speed": None, **floors})
    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_a_fresh_spark_build_short_of_the_boss_hits_is_delivered_with_a_warning(engine):
    search = spark(engine)
    build, objective = search.build, search.build.objective
    stats = build.stats
    soft = objective.soft_deficits(stats)
    assert soft, "a fresh level-90 Spark build can't take the Pinnacle's hits: the case this test is for"
    # It costs the score as a floor does (the search strives for it) ...
    assert objective._weight(soft) >= 3.0  # (FLOOR_OFFSET: e^3 = 20x DPS for being short at all)
    assert objective.penalty(stats) >= objective._weight([d for d in objective.deficits(stats) if d[2] is not None]) + 3.0
    # ... but it is no shortfall: nothing makes the build infeasible, keep_feasible never trades it away
    survive = [(label, hard) for label, _, hard in objective.deficits(stats) if label.startswith("Survives")]
    assert survive and all(hard is None for _, hard in survive)
    others = [{"amount": a, "hard": h} for label, a, h in objective.deficits(stats) if not label.startswith("Survives")]
    assert search.shortfall(stats) == pytest.approx(shortfall(others))
    # The delivered result's rows: run.py's reasons are the rows with ok False; the survive rows warn instead
    facts = build.call("facts", fields=RESULT_FIELDS)
    rows = [c.__dict__ for c in search.ctx.compiled.check(facts)]
    survive = [r for r in rows if r["label"].startswith("Survives")]
    assert survive and not any(r["ok"] is False for r in survive)
    warned = [r for r in survive if r["warning"]]
    assert warned and all(r["ok"] is None and "short" in r["warning"] and "WARNING" in r["warning"] for r in warned)
    for label, _, _ in soft:  # each type it fails is named, with how far short it is
        kind = label.removeprefix("Survives the boss's ").removesuffix(" hit")
        row = next(r for r in survive if r["label"] == label)
        assert f"{kind} max hit" in row["warning"] and "short, " in row["warning"] and "%" in row["warning"]


def test_glass_cannon_has_no_survive_rows_or_cost(engine):
    search = spark(engine, survive_boss_hits=False)
    assert not search.build.objective.soft_deficits(search.build.stats)
    facts = search.build.call("facts", fields=RESULT_FIELDS)
    rows = [c.__dict__ for c in search.ctx.compiled.check(facts)]
    assert not any(r["label"].startswith("Survives the boss's ") and r["label"].endswith(" hit") for r in rows)
    assert not any(r["warning"] for r in rows)


def test_the_checklist_names_the_warnings_and_the_utility_fills():
    from builder.report.checklist import checklist

    result = {"checks": [{"label": "Survives the boss's Fire hit", "ok": None, "note": "a default",
                          "warning": "WARNING: Fire max hit 4,200 of 8,993 (4,793 short, 53%)"},
                         {"label": "Life", "ok": True, "note": "", "warning": ""}],
              "gear": {"Boots": {"name": "Rare Boots", "utility_affixes": ["30% increased Movement Speed"]},
                       "Helmet": {"name": "Rare Helmet", "affixes": ["+100 to maximum Life"]}}}
    items = checklist(result)
    warned = [i for i in items if i["kind"] == "warning"]
    assert len(warned) == 1 and "4,793 short" in warned[0]["why"]
    filled = [i for i in items if i["kind"] == "utility affixes"]
    assert len(filled) == 1 and filled[0]["title"].startswith("Boots") and "Movement Speed" in filled[0]["why"]


def test_results_rank_feasibility_first_then_value_less_the_soft_floors_cost():
    from builder.search.objective import FLOOR_OFFSET, rank_key

    soft = {"label": "Survives the boss's Fire hit", "amount": 0.5, "hard": None}
    floor = {"label": "Fire resistance", "amount": 0.1, "hard": False}
    tanky = {"dps": 100_000, "value": 100_000, "deficits": []}
    glassy = {"dps": 150_000, "value": 150_000, "deficits": [soft]}  # (x1.5 DPS doesn't pay for e^6 of floor cost)
    huge = {"dps": 1e9, "value": 1e9, "deficits": [soft]}  # (kills "faster than they can retaliate")
    short = {"dps": 1e9, "value": 1e9, "deficits": [floor]}
    assert rank_key(tanky) < rank_key(glassy)
    assert rank_key(huge) < rank_key(tanky)
    assert rank_key(tanky) < rank_key(short) and rank_key(glassy) < rank_key(short)  # (soft: never infeasible)
    assert rank_key(tanky)[1] < rank_key({"dps": 90_000, "value": 90_000, "deficits": []})[1]  # (by value, as before)
    assert FLOOR_OFFSET == 3.0


def test_the_recovery_floor_stat_reads_the_same_share_without_the_report_rows(engine, monkeypatch):
    """(SEARCHB6) with_boss_recovery runs at every score; the report's biggest-hit and no-deflect readings it never read
    took ~28% of a profiled search (POE2T6325). The share is the same without them."""
    from builder.search import boss_hits
    from builder.search.objective import boss_recovery, with_boss_recovery

    search = spark(engine)
    stats = dict(search.build.call("facts", fields=RESULT_FIELDS)["stats"])
    full = boss_recovery(dict(stats))
    calls = []
    real = boss_hits.biggest_reading
    monkeypatch.setattr(boss_hits, "biggest_reading", lambda s: calls.append(1) or real(s))
    assert with_boss_recovery(dict(stats))["BossRecoveryPercent"] == min(full["share"] * 100, 1000.0)
    assert not calls
