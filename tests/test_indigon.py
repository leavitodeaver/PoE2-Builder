"""COV32 (PATCH_CHECK MB-4): Indigon's "for each 200 total Mana (you have) Spent Recently" lines counted 0 (PoB's
Multiplier:ManaSpentRecently is only its config box). They are `share:` lines at ManaSpentPer200, the average whole
200s of Mana the main skill spent in the past 4 s (assumptions.mana_spent_stacks; the bridge's DERIVED.manaSpent)."""

from __future__ import annotations

import faulthandler

import pytest

from builder.ledger import wrong
from builder.search.assumptions import mana_spent_stacks

COST_LINE = "10% increased Cost of Skills for each 200 total Mana Spent Recently"
DAMAGE_LINE = "15% increased Spell damage for each 200 total Mana you have Spent Recently"


def test_the_stacks_come_from_the_main_skills_own_spending():
    assert mana_spent_stacks(0, 50, 0)[0] == 0  # (spends nothing)
    assert mana_spent_stacks(100, 0, 20)[0] == 0  # (no regeneration: a dry pool spends nothing)
    # 100 a second for 4 s = 400, a use of 20 moves the sum 390-410: 2 whole 200s half the time, 1 the other half
    assert mana_spent_stacks(100, 200, 20)[0] == pytest.approx(1.5)
    assert mana_spent_stacks(100, 200, 0)[0] == 2  # (exactly 400)
    assert mana_spent_stacks(100, 60, 0)[0] == 1  # (regeneration pays 60 a second: 240 in 4 s)
    assert mana_spent_stacks(150, 200, 10)[0] == 2.5  # (600 +-5: 2 below 600, 3 from it)
    assert wrong(COST_LINE)[1] == "LedgerShare:ManaSpentPer200 10% increased Cost of Skills"
    assert wrong("(10-15)% increased Spell damage for each 200 total Mana you have Spent Recently")[1] == \
        "LedgerShare:ManaSpentPer200 (10-15)% increased Spell damage"


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_lines_count_at_the_derived_stacks(engine):
    from builder.search import models

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Spark 20/0 1", main=True)

    def helmet(*lines):
        engine.call("equip", slot="Helmet", fields=[],
                    raw="\n".join(["Rarity: Unique", "Indigon", "Magus Tiara", "--------", "+100 to maximum Mana", *lines]))

    def stats(stacks):
        engine.call("setAssumptions", mods=[models.derived("ShareManaSpentPer200", "manaSpent", 100 * stacks, per=200)])
        return engine.call("stats", fields=["TotalDPS", "ManaCost"])

    helmet(COST_LINE, DAMAGE_LINE)  # (PoB's own parse: under a multiplier nothing sets, nothing)
    plain = stats(0)
    assert stats(2) == plain
    helmet(wrong(COST_LINE)[1], wrong(DAMAGE_LINE)[1])
    assert stats(0) == plain
    two = stats(2)
    assert two["TotalDPS"] > plain["TotalDPS"] * 1.2 and two["ManaCost"] > plain["ManaCost"]  # (+30% Spell damage, +20% cost)
    # the in-place stand-ins leave only their marker in sourceLines, which the condition's shown_for finds (before, the
    # replaced line was gone and the condition never switched on: The Lethal Draw's too); never the bare line
    import re

    from builder.ledger import conditions

    found = engine.call("sourceLines")
    texts = [e["text"] for e in found["lines"] if e["source"] == "item:Helmet"]
    assert found["shares"].count("LedgerShare:ManaSpentPer200") == 2
    assert not any("increased Spell damage" in t or "Cost of Skills" in t or "LedgerShare" in t for t in texts), texts
    shown = {c["id"]: c["shown_for"] for c in conditions() if c.get("shown_for")}
    assert any(re.search(shown["ManaSpentPer200"], t) for t in found["shares"])
    assert re.search(shown["LethalDrawCharged"], "LedgerShare:LethalDrawCharged")
    assert not re.search(shown["LethalDrawCharged"], "LedgerShare:LethalDrawChargedX")
