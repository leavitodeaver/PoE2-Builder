"""SEARCH40 (the manager's 24 Nov 17:02 items)."""
from __future__ import annotations

import faulthandler
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ORACLE = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261123-073445.json"


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def test_the_weapon_set_the_main_skill_doesnt_use_gives_the_models_no_lines(engine):
    """(Item 1; MECHC59's POE2T7139) A swap weapon's lines reach the models (sourceLines) only while the main skill's
    group uses that weapon set, as PoB's own calculation reads them (CalcSetup 1255): before, "+8 to Maximum Rage" on
    "Weapon 1 Swap" reached the Rage model of a build on weapon set 1."""
    from builder.pob import codes

    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))

    def swap_lines():
        return sorted(l["text"] for l in engine.call("sourceLines")["lines"] if l["source"].endswith("Swap"))

    def strength():
        return float(engine.call("stats", fields=["Str"])["Str"])

    plain = strength()
    engine.call("equip", slot="Weapon 1 Swap",
                raw="Rarity: RARE\nProbe Fang\nWithered Wand\n+100 to Strength\n+8 to Maximum Rage")
    assert swap_lines() == [] and strength() == plain  # (set 1: neither PoB nor the models read the swap wand)
    # (the mirror: a build whose main skill plays weapon set 2, Ice Shot on the swap bow (test_engine_items' fixture):
    # the swap weapons' lines are its own, set 1's wand gives none. Probe POE2T7513: PoB won't move the Oracle's main
    # group to set 2, its skill needs set 1's weapon)
    code = (Path(__file__).parent / "fixtures" / "codes" / "set2_ice_shot_with_mana.txt").read_text(encoding="utf-8")
    engine.call("loadXml", xml=codes.decode(code.strip()))
    set2 = next(g for g in engine.call("groups")["groups"] if (g["set1"], g["set2"]) == (False, True)
                and any(n["name"] == "Ice Shot" for n in g["gems"]))
    engine.call("setGroup", index=set2["index"], main=True)  # (the fixture's main group uses both sets: PoB plays set 1)
    groups = engine.call("groups")
    main = next(g for g in groups["groups"] if g["index"] == groups["mainSocketGroup"])
    assert main["index"] == set2["index"] and (main["set1"], main["set2"]) == (False, True)
    plain2 = strength()
    engine.call("equip", slot="Weapon 1 Swap", raw="Rarity: RARE\nProbe Bow\nCrude Bow\n+100 to Strength")
    sources = {l["source"] for l in engine.call("sourceLines")["lines"]}
    assert strength() == plain2 + 100  # (PoB reads the set-2 bow)
    assert "item:Weapon 1 Swap" in sources and not sources & {"item:Weapon 1", "item:Weapon 2"}, sorted(sources)
    plain2 = strength()
    engine.call("equip", slot="Weapon 1", raw="Rarity: RARE\nProbe Fang\nWithered Wand\n+100 to Strength\n+8 to Maximum Rage")
    assert strength() == plain2  # (PoB doesn't read set 1's wand)
    assert not {l["source"] for l in engine.call("sourceLines")["lines"]} & {"item:Weapon 1", "item:Weapon 2"}
