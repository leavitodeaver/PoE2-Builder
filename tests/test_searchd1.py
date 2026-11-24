"""SEARCHD1: the "Not finished" record (builder/search/unfinished.py) and the run's rows (Run.unfinished_rows).

The user, 24 Nov 2026: "a jewel with 2 mods is better than an empty jewel slot. if the optimizer runs out of time when
it's actively working on something like that, it should leave a note to indicate that it wasn't finished"."""

from __future__ import annotations

from types import SimpleNamespace

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
    assert ("gear", "Ring 2 and the slots after it") in by
    assert by[("jewels", "Jewel 3")]["done"] == 3  # (finalize's row wins over the line's older one)
    assert ("supports", "Spark's group") in by and ("supports", "Arc's group") not in by
    assert all(r["text"].endswith("Continue can finish it.") for r in rows)


def test_run_rows_empty_when_nothing_was_cut_and_cancel_is_said():
    assert _run().unfinished_rows({}) == []
    rows = _run(cancelled=True).unfinished_rows({})
    assert [r["step"] for r in rows] == ["search"]
