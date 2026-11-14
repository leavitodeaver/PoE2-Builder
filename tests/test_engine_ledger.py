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


def test_the_electrocution_lines_pob_misspells_count_only_through_their_ledger_replacement(engine):
    """COV27 (AUDIT33 A33-2): PoB parses "Lightning damage from Hits Contributes to Electrocution Buildup" (Kitoko's
    Current) into LightningCanElectrocution, which no calculation reads (canDoAilment asks for ...CanElectrocute): the
    line builds no Electrocute. The ledger's pob_wrong replacement (PoB's own "Electrocute" wording) does."""
    from builder.ledger import wrong

    line = "Lightning damage from Hits Contributes to Electrocution Buildup"
    entry, fixed = wrong(line)
    assert entry.id == "wrong-lightning-hits-electrocution-buildup"

    def buildup(lines):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("addSkillGroup", text="Spark 20/0 1", main=True)
        if lines:
            engine.call("equip", slot="Ring 1", fields=[],
                        raw="Rarity: Rare\nProbe Ring\nAmethyst Ring\n--------\n" + "\n".join(lines))
        return engine.call("stats", fields=["ElectrocuteBuildupAvg"]).get("ElectrocuteBuildupAvg") or 0

    plain = buildup([])
    assert buildup([line]) == plain  # PoB's own parse: nothing
    assert buildup([fixed]) > plain  # the replacement: Lightning hits build Electrocute
    assert wrong("All damage with this Weapon causes Electrocution buildup")[1] == \
        "All damage with this Weapon causes Electrocute buildup"

