"""Every ledger replacement line through PoB's own parser: a line PoB can't parse would do nothing
(or stop an assumption from installing). Boots one PoB engine."""

from __future__ import annotations

import faulthandler
import re

import pytest

from builder.ledger import SYNTAX, entries


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def replacement_lines() -> list[tuple[str, str]]:
    found = []
    for entry in entries():
        if entry.kind not in ("pob_gap", "pob_wrong", "gem_stat"):
            continue
        for template in entry.replace:
            m = SYNTAX.match(template)
            if m and m.group(1) in ("flag", "mult"):
                continue
            text = m.group(3) if m else template
            # numbers from the matched line: any value parses the same, so fill templates with 10
            found.append((entry.id, re.sub(r"\{[^{}]+\}", "10", text)))
    return found


def test_every_replacement_line_parses(engine):
    lines = replacement_lines()
    results = engine.call("parseLines", lines=[text for _, text in lines])["results"]
    failed = [(entry, r["line"], r.get("extra")) for (entry, _), r in zip(lines, results) if not r["supported"]]
    assert not failed, failed
