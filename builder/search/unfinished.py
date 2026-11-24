"""What the search's time budget cut off, carried into the result as its "Not finished" section (SEARCHD1).

THE USER, 24 Nov 2026 ~04:55: "a jewel with 2 mods is better than an empty jewel slot. if the optimizer runs out of time
when it's actively working on something like that, it should leave a note to indicate that it wasn't finished, and there
should also be options to continue working on the overall build, or to optimize a single item or skill or the tree".

So a step the deadline cuts keeps its best partial (never drops it for nothing) and calls `note` here; a later pass that
finishes the same part calls `done`. The rows live on the Build (an attribute this module owns, `unfinished`), travel in
the search's state (`rows` / `load`) and end up in the result, where the page shows them with a Continue button.

A row: {"step": the search step ("jewels", "gear", "tree", "supports", ...), "part": the slot / socket / skill group /
"passive tree" it was working on, "done": how far it got (a number), "of": out of how many (None: open-ended), "unit":
what done / of count ("mods", "slots", "points", ...), "detail": one plain sentence}.
"""

from __future__ import annotations

from typing import Any

ATTRIBUTE = "unfinished"
CONTINUE_HINT = "Continue can finish it"


def _rows(target: Any) -> list[dict]:
    """The row list of a Build, or of anything holding one (`.build`: a Search, Gear, Jewels, Tree, Gems)."""
    build = getattr(target, "build", target)
    rows = getattr(build, ATTRIBUTE, None)
    if rows is None:
        rows = []
        setattr(build, ATTRIBUTE, rows)
    return rows


def note(target: Any, step: str, part: str, done: float | None = None, of: float | None = None, unit: str = "",
         detail: str = "") -> dict:
    """Record that the deadline cut `step` while it worked on `part` (one row per step and part: a later cut of the same
    part replaces it). Call it where the cut keeps its partial: the row says what the build got and what it didn't."""
    rows = _rows(target)
    row = {"step": step, "part": part, "done": done, "of": of, "unit": unit, "detail": detail}
    rows[:] = [r for r in rows if (r["step"], r["part"]) != (step, part)]
    rows.append(row)
    return row


def done(target: Any, step: str, part: str | None = None) -> None:
    """A later pass finished `part` of `step` (None: every part of it): its row goes."""
    rows = _rows(target)
    rows[:] = [r for r in rows if not (r["step"] == step and (part is None or r["part"] == part))]


def rows(target: Any) -> list[dict]:
    """A copy of the rows, for the search's state and its result."""
    return [dict(r) for r in _rows(target)]


def load(target: Any, saved: list[dict] | None) -> None:
    """The rows a line's state carried (a task resuming the line)."""
    _rows(target)[:] = [dict(r) for r in saved or []]


def merge(*lists: list[dict] | None) -> list[dict]:
    """Rows from several places (a line's state, finalize, the run's own stages), one per step and part, the last kept."""
    out: dict[tuple[str, str], dict] = {}
    for found in lists:
        for row in found or []:
            out[(row["step"], row["part"])] = dict(row)
    return list(out.values())


def describe(row: dict) -> str:
    """One plain line for a row: "Jewels, Jewel 3: 2 of 4 mods. <detail> Continue can finish it."."""
    how = ""
    if row.get("done") is not None:
        count = f"{row['done']:g}" + (f" of {row['of']:g}" if row.get("of") is not None else "")
        how = f": {count}{' ' + row['unit'] if row.get('unit') else ''} done"
    text = (row.get("detail") or "").rstrip(".")
    detail = f" {text[0].upper()}{text[1:]}." if text else ""
    return f"{row['step'][0].upper()}{row['step'][1:]}, {row['part']}{how}.{detail} {CONTINUE_HINT}."
