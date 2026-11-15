"""COV33 (H5-3 part 3): Open Weakness lines count at the boss's Open Weakness share: 45% of the fight when the build
reveals weaknesses (KeywordPopups RevealWeakness: the segments are 45% of the enemy's Life), 0 without a source.
The Master's Reach's recoup line (PoB part-parses it: nothing applied) and Predatory Instinct's 50% more (PoB's
condition is never set) through `share:OpenWeakness` stand-ins (ledger/entries/items-cov33.yaml)."""

from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.ledger import item_lines, translate, wrong
from builder.search.assumptions import Assumptions

RECOUP = "90% of damage taken from enemies with an Open Weakness Recouped as Life"
MORE = "50% more damage against enemies with an Open Weakness"
REVEAL = "Reveal Weaknesses against Rare and Unique enemies"


def _me(lines):
    found = {"lines": [{"text": t} for t in lines], "shares": []}
    me = SimpleNamespace(build=SimpleNamespace(call=lambda name, **_: found))
    me.has_line = lambda pattern: Assumptions.has_line(me, pattern)
    me.REVEAL_WEAKNESS, me.OPEN_WEAKNESS_SHARE = Assumptions.REVEAL_WEAKNESS, Assumptions.OPEN_WEAKNESS_SHARE
    return me


def test_the_lines_stand_in_at_the_open_weakness_share():
    assert translate(RECOUP)[1] == ["share:OpenWeakness 90% of Damage taken Recouped as Life"]
    # a pob_wrong entry (PoB parses the line): on an item it comes back in its item form
    assert wrong(MORE)[1] == "LedgerShare:OpenWeakness 50% more Damage"
    share, how = Assumptions.open_weakness_share(_me([RECOUP, REVEAL]))
    assert share == 0.45 and "45%" in how
    share, how = Assumptions.open_weakness_share(_me([RECOUP]))  # nothing reveals: 0
    assert share == 0 and "nothing reveals" in how


def test_predatory_instincts_parsed_line_is_replaced_on_the_tree_node():
    from builder.search.passive_models import wrong_tree_lines

    # PoB parses the 50% more under a condition the tool never sets: a pob_wrong entry swaps it for the share stand-in
    found = wrong_tree_lines([{"id": 1, "stats": [REVEAL, MORE]}])
    assert found == [{"id": 1, "entry": "passives-predatory-instinct-open-weakness-more",
                      "lines": ["share:OpenWeakness 50% more Damage"], "drop": MORE}]


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def _ring(engine, lines):
    engine.call("equip", slot="Ring 1", fields=[],
                raw="\n".join(["Rarity: Rare", "Probe Ring", "Gold Ring", "--------", *lines]))


def test_pob_counts_the_stand_ins_at_their_share(engine):
    from builder.search import models

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Fireball 20/0 1", main=True)

    def stats(share, fields):
        engine.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerShareOpenWeakness", "BASE", share)])
        return engine.call("stats", fields=fields)

    _ring(engine, [MORE])  # PoB's own conditional mod alone: never on
    plain = stats(0, ["TotalDPS"])["TotalDPS"]
    assert plain > 0 and stats(45, ["TotalDPS"])["TotalDPS"] == pytest.approx(plain)
    _ring(engine, [MORE, wrong(MORE)[1]])
    assert stats(0, ["TotalDPS"])["TotalDPS"] == pytest.approx(plain)
    assert stats(45, ["TotalDPS"])["TotalDPS"] == pytest.approx(plain * 1.225, rel=0.01)  # 1 + 45% x 50%
    _ring(engine, [RECOUP] + item_lines(translate(RECOUP)[1]))
    none, part = stats(0, ["LifeRecoup"]), stats(45, ["LifeRecoup"])
    print(none, part)
    assert (none.get("LifeRecoup") or 0) == 0
    assert part["LifeRecoup"] == pytest.approx(90 * 0.45, abs=1)
