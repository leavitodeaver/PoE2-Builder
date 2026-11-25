"""SEARCH40 (the manager's 24 Nov 17:02 items)."""
from __future__ import annotations

import faulthandler
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builder.request.schema import DefenceStyle
from builder.search.objective import Objective, reservation_short

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


def limits(stats):
    o = SimpleNamespace(penalties=[], points_slack=0, groups_enforced=False, style=DefenceStyle.ANY)
    return [d for d in Objective._deficits(o, stats) if d[2] is True]


def test_all_life_reserved_is_a_hard_limit():
    found = reservation_short({"Life": 1000, "LifeUnreserved": 0, "Mana": 200, "ManaUnreserved": 50})
    assert found == [("Unreserved Life below 1", 0.001, True)]
    assert ("Unreserved Life below 1", 0.001, True) in limits({"Life": 1000, "LifeUnreserved": 0})


def test_life_reserved_past_the_pool_reads_further_short():
    (label, amount, hard), = reservation_short({"Life": 1000, "LifeUnreserved": -199})
    assert label == "Unreserved Life below 1" and hard is True and abs(amount - 0.2) < 1e-9


def test_negative_unreserved_mana_is_a_hard_limit_and_zero_mana_left_is_not():
    assert reservation_short({"Mana": 400, "ManaUnreserved": -100}) == [("Unreserved Mana negative", 0.25, True)]
    assert reservation_short({"Mana": 400, "ManaUnreserved": 0}) == []  # (PoB's test is "negative")


def test_one_life_left_or_no_field_is_no_limit():
    assert reservation_short({"Life": 1, "LifeUnreserved": 1}) == []  # (Chaos Inoculation: 1 Life, nothing reserved)
    assert reservation_short({"Life": 3000, "LifeUnreserved": 1}) == []
    assert reservation_short({"Life": 3000}) == []  # (a stand-in or a what-if without the field)
    assert limits({"Life": 3000, "LifeUnreserved": 2000, "Mana": 100, "ManaUnreserved": 10}) == []


def test_a_bear_builds_skills_have_their_types():
    """(Fix 3; GEM39) Maul and Molten Crash: PoB marks them fromItem, so Context.gems leaves them out; their types come
    from any_gems now, so a reader keyed on Bear / Slam / Area sees them (before: [])."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload, skill_types_of

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    assert "Maul" not in ctx.gems and "Molten Crash" not in ctx.gems
    assert {"Bear", "Attack", "Area", "Melee"} <= set(ctx.skill_types("Maul"))
    assert {"Bear", "Slam", "ConsumesRage"} <= set(ctx.skill_types("Molten Crash"))
    assert "Spell" in ctx.skill_types("Spark") and ctx.skill_types(None) == [] and ctx.skill_types("No Such Skill") == []
    stand_in = SimpleNamespace(gems={"Spark": {"skillTypes": ["Spell"]}})  # (a test's stand-in context: gems only)
    assert skill_types_of(stand_in, "Spark") == ["Spell"] and skill_types_of(stand_in, "Maul") == []


def test_no_assumption_reads_a_skills_types_from_the_socketable_gems_alone():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "builder" / "search" / "assumptions.py").read_text(encoding="utf-8")
    assert not re.search(r'build\.ctx\.gems\.get\((?:[^()]|\([^()]*\))*\)(?: or \{\}\))?\.get\("skillTypes"', text)
