"""SEARCHD1: the "Not finished" record (builder/search/unfinished.py) and the run's rows (Run.unfinished_rows).

The user, 24 Nov 2026: "a jewel with 2 mods is better than an empty jewel slot. if the optimizer runs out of time when
it's actively working on something like that, it should leave a note to indicate that it wasn't finished"."""

from __future__ import annotations

from types import SimpleNamespace

from builder.request.schema import BuildRequest
from builder.search import unfinished
from builder.search.run import Run


def test_note_replaces_same_part_and_done_clears():
    build = SimpleNamespace()
    holder = SimpleNamespace(build=build)  # a round object (Jewels, Gear) records on its build
    unfinished.note(holder, "jewels", "Jewel 3", 1, 4, "mods")
    unfinished.note(holder, "jewels", "Jewel 3", 2, 4, "mods")  # the same part cut again: one row, the latest
    unfinished.note(build, "jewels", "Jewel 5", 3, 4, "mods")
    assert [(r["part"], r["done"]) for r in unfinished.rows(build)] == [("Jewel 3", 2), ("Jewel 5", 3)]
    unfinished.done(holder, "jewels", "Jewel 3")
    assert [r["part"] for r in unfinished.rows(build)] == ["Jewel 5"]
    unfinished.done(holder, "jewels")
    assert unfinished.rows(build) == []


def test_rows_travel_in_a_state_and_merge_keeps_the_last():
    a, b = SimpleNamespace(), SimpleNamespace()
    unfinished.note(a, "gear", "Boots", 2, 6, "affixes")
    unfinished.load(b, unfinished.rows(a))
    assert unfinished.rows(b) == unfinished.rows(a)
    later = [{"step": "gear", "part": "Boots", "done": 5, "of": 6, "unit": "affixes", "detail": ""}]
    merged = unfinished.merge(unfinished.rows(a), later)
    assert len(merged) == 1 and merged[0]["done"] == 5


def test_describe_says_how_far_and_continue():
    text = unfinished.describe({"step": "jewels", "part": "Jewel 3", "done": 2, "of": 4, "unit": "mods",
                                "detail": "the jewel keeps its 2 mods"})
    assert text == "Jewels, Jewel 3: 2 of 4 mods done. The jewel keeps its 2 mods. Continue can finish it."
    assert "Continue can finish it" in unfinished.describe({"step": "tree", "part": "passive tree"})


def _run(cancelled: bool = False) -> Run:
    run = Run.__new__(Run)  # (no engine: only unfinished_rows is exercised)
    run.unfinished = []
    run.should_cancel = lambda: cancelled
    run.best = None
    run.request = SimpleNamespace(focus=None)
    return run


def test_run_rows_from_stages_state_finalize_and_sockets():
    run = _run()
    unfinished.note(run, "skill screen", "main skill choice", 12, 40, "skill setups")
    run.best = {"line": {"resume": "jewels"}, "gear": {"empty": [], "resume": "Ring 2"},
                "state": {"unfinished": [{"step": "jewels", "part": "Jewel 3", "done": 2, "of": 4, "unit": "mods",
                                          "detail": ""}]}}
    final = {"line": {"resume": None},
             "unfinished": [{"step": "jewels", "part": "Jewel 3", "done": 3, "of": 4, "unit": "mods", "detail": ""}],
             "open_sockets": [{"skill": "Spark", "open": 2, "reasons": ["the fill's 90 s ran out: not tried supports X"]},
                              {"skill": "Arc", "open": 1, "reasons": ["no legal support helps: tried ..."]}]}
    rows = run.unfinished_rows(final)
    by = {(r["step"], r["part"]): r for r in rows}
    assert by[("skill screen", "main skill choice")]["done"] == 12
    assert "'jewels' step" in by[("search pass", "the whole build")]["text"]  # (finalize's own pass wasn't cut: the line's)
    assert ("gear round", "Ring 2 and the slots after it") in by
    assert by[("jewels", "Jewel 3")]["done"] == 3  # (finalize's row wins over the line's older one)
    assert ("supports", "Spark's group") in by and ("supports", "Arc's group") not in by
    assert all(r["text"].endswith("Continue can finish it.") for r in rows)


def test_run_rows_empty_when_nothing_was_cut_and_cancel_is_said():
    assert _run().unfinished_rows({}) == []
    rows = _run(cancelled=True).unfinished_rows({})
    assert [r["step"] for r in rows] == ["search"]


def _floor_run() -> Run:
    run = _run()
    run.notes = []
    run.request = SimpleNamespace(objective="boss", focus=None)
    return run


def test_the_seed_is_the_floor():
    """A Continue / focus run's seed, finalized beside it, is delivered when the search's build ranks lower (fewer
    requirements short first, then the goal); else the search's build stands."""
    from builder.pob.pool import WorkerError

    run = _floor_run()
    seed = {"dps": 100.0, "clear": 0.0, "deficits": []}
    worse = {"dps": 90.0, "clear": 0.0, "deficits": []}
    assert run.at_least_seed(worse, dict(seed))["dps"] == 100.0 and run.floor_check["kept_seed"]
    assert run.at_least_seed({"dps": 120.0, "clear": 0.0, "deficits": []}, dict(seed))["dps"] == 120.0
    assert not run.floor_check["kept_seed"]
    short = {"dps": 500.0, "clear": 0.0, "deficits": [{"label": "Life", "amount": 0.2, "hard": True}]}
    assert run.at_least_seed(short, dict(seed))["dps"] == 100.0  # (more DPS but short of a floor the seed met)
    assert run.at_least_seed(WorkerError("boom"), dict(seed))["dps"] == 100.0
    assert run.at_least_seed(worse, None) is worse  # (no origin: no floor)


def test_a_replaced_seed_says_why():
    """(SEARCHD2; the manager, 24 Nov 20:06, after POE2T8212: a 15,086-DPS build delivered over a 501,578-DPS pasted
    build that missed more requirements) When the search's build is delivered instead of the seed, a note says how far
    each is short, which requirements, and both DPS; none when the seed is kept or for a focus run."""
    run = _floor_run()
    seed = {"dps": 501578.0, "clear": 0.0, "deficits": [
        {"label": "Fire Resistance", "amount": 0.5, "hard": False},
        {"label": "Str requirement", "amount": 0.3, "hard": True},
        {"label": "Survive pinnacle hits", "amount": 0.4, "hard": None}]}
    lower = {"dps": 15086.0, "clear": 0.0, "deficits": [{"label": "Fire Resistance", "amount": 0.7, "hard": False}]}
    assert run.at_least_seed(lower, dict(seed))["dps"] == 15086.0
    note = run.notes[-1]
    assert note.startswith("The build you gave was 3.5 short of the requirements; this one is 0.7 short, at 15,086 DPS "
                           "instead of 501,578.")
    assert "even at lower DPS" in note
    assert ("Short in the build you gave: Fire Resistance (50% short); Str requirement (a game limit); Survive pinnacle "
            "hits (the default survive floor, 40% short).") in note
    assert note.endswith("Short in this one: Fire Resistance (70% short).")
    # Same shortfall, more DPS: no "even at lower DPS"; nothing short says "none"
    run.notes = []
    run.at_least_seed({"dps": 600000.0, "clear": 0.0, "deficits": []}, {"dps": 501578.0, "clear": 0.0, "deficits": []})
    assert "misses the requirements no more" in run.notes[-1] and "Short in this one: none." in run.notes[-1]
    # The seed kept: only the floor's own note; a focus run: no note (it has its before / after)
    run.notes = []
    run.at_least_seed({"dps": 90.0, "clear": 0.0, "deficits": []}, {"dps": 100.0, "clear": 0.0, "deficits": []})
    assert len(run.notes) == 1 and run.notes[0].startswith("The search found nothing better")
    run.notes = []
    run.request = SimpleNamespace(objective="boss", focus=SimpleNamespace(kind="tree"))
    run.at_least_seed(lower, dict(seed))
    assert run.notes == []


def test_the_continue_route_builds_the_request_from_the_saved_result(tmp_path, monkeypatch):
    """POST /api/runs/continue: the saved result's request, its code as the seed, the minutes chosen, the origin, and a
    focus when one part is asked for; a result without a code can't be continued."""
    import json

    import pytest
    from fastapi import HTTPException

    from builder.web import app as app_module

    started = []
    monkeypatch.setattr(app_module, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(app_module.runs, "start", lambda request, request_id=None: started.append(request)
                        or SimpleNamespace(view=lambda: {"id": 7}))
    saved = {"request": BuildRequest(name="Spear", main_skill="Explosive Spear").model_dump(mode="json"),
             "code": "eNoLAAAAAA", "dps": {"full": 1234.0}, "label": "Huntress", "skeleton": {"main_skill": "Spark"}}
    (tmp_path / "r1.json").write_text(json.dumps(saved), encoding="utf-8")
    (tmp_path / "r2.json").write_text(json.dumps({**saved, "code": None}), encoding="utf-8")
    monkeypatch.setattr(app_module, "compile_request", lambda *a: SimpleNamespace(issues=[]))
    post = lambda **body: app_module.continue_run(app_module.ContinueBody(**body))
    assert post(result="r1.json", minutes=3) == {"id": 7}
    request = started[-1]
    assert request.seed_code == "eNoLAAAAAA" and request.effort_minutes == 3 and request.focus is None
    assert request.origin.kind == "continue" and request.origin.dps == 1234.0 and request.name == "Spear (continued)"
    assert request.main_skill == "Explosive Spear"  # (the request's own; one naming none gets the result's)
    (tmp_path / "r3.json").write_text(json.dumps({**saved, "request": BuildRequest().model_dump(mode="json")}), encoding="utf-8")
    post(result="r3.json")
    assert started[-1].main_skill == "Spark"
    post(result="r1.json", minutes=2, focus={"kind": "slot", "slot": "Boots"})
    request = started[-1]
    assert request.focus.slot == "Boots" and request.origin.kind == "focus" and request.name == "Spear (one part: Boots)"
    for body, code in (({"result": "r2.json"}, 422), ({"result": "nope.json"}, 404), ({"result": "r1.json", "minutes": 0}, 422)):
        with pytest.raises(HTTPException) as caught:
            post(**body)
        assert caught.value.status_code == code


def test_every_seeded_run_gets_the_floor(monkeypatch):
    """(the manager, 24 Nov 13:48) a pasted build code, not only Continue: the run finalizes the seed beside its best."""
    from builder.search import run as run_module

    seeded = SimpleNamespace(payload={"request": {"seed_code": "eNoL"}}, request=SimpleNamespace(origin=None))
    fresh = SimpleNamespace(payload={"request": {"seed_code": None}}, request=SimpleNamespace(origin=None))
    assert run_module.attempt_floor(seeded) and not run_module.attempt_floor(fresh)
    monkeypatch.setattr(run_module, "SEED_FLOOR", False)
    assert not run_module.attempt_floor(seeded)


def test_jewel3s_short_jewel_notes_join_the_record():
    """JEWEL3's Jewels.unfinished_notes (54427fd1) reach the result as assumptions; they become "Not finished" rows."""
    run = _run()
    final = {"assumptions": [{"label": "Not finished: the Ruby jewel in Jewel 26725 fills 2 of its 4 affix slots",
                              "value": "2/4", "basis": "It stopped because the search's time for it ran out first. A "
                              "part-filled jewel beats an empty socket (the user, 24 Nov 2026), so it is kept as it is; "
                              "a Continue run can finish it", "confidence": "HIGH"}]}
    rows = run.unfinished_rows(final)
    assert [(r["step"], r["part"], r["done"], r["of"]) for r in rows] == [("jewels", "Jewel 26725", 2, 4)]
    assert "time for it ran out" in rows[0]["text"] and rows[0]["text"].endswith("Continue can finish it.")
