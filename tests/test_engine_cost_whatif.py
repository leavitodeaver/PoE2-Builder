"""SEARCH9 (the ET probe): PoB lists the skills whose cost a pool can't pay only in its MAIN-mode output (Calcs.lua 533),
so the committed build has WardCostWarningList and a what-if (CALCULATOR mode) never does. objective.cost_short reads
the main skill's own cost against its pool, which both have. On MECHC8's delivered 2b75b (Runic Ward 0, MECHB12)."""
from __future__ import annotations

import faulthandler
import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.pob import codes
from builder.search import objective

ET = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261112-152004.json"
FIELDS = sorted(set(objective.COST_WARNINGS) | set(objective.MAIN_COST_FIELDS))


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


def test_a_what_if_sees_the_runic_ward_cost_the_committed_build_lists(engine):
    row = json.loads(ET.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))
    committed = engine.call("stats", fields=FIELDS)
    what_if = engine.call("nodeDeltas", cases=[{}], fields=FIELDS)["results"][0]
    print("committed", committed)
    print("what-if", what_if)
    print("main skill", engine.call("facts", fields=[]).get("mainSkill"))
    assert committed.get("WardCostWarningList") == 1
    # (CALCULATOR mode: PoB never fills the lists; the bridge's setCostShort does, from the committed costs)
    assert what_if.get("WardCostWarningList") == 1
    assert objective.cost_short(committed, "WardCostWarningList") == 1
    assert objective.cost_short(what_if, "WardCostWarningList") == 1
    # A what-if that gives Runic Ward pays it: an amulet with +1000 to maximum Runic Ward (ModCache: Ward BASE)
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                        "+1000 to maximum Runic Ward"])
    paid = engine.call("itemDeltas", cases=[{"slot": "Amulet", "raw": amulet}], fields=FIELDS)["results"][0]
    print("with Ward", paid)
    assert paid.get("Ward", 0) > 0
    assert objective.cost_short(paid, "WardCostWarningList") == 0
