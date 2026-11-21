"""SEARCHB1 (SEARCH-B lane): Perfect Essences on rares (G1; builder/search/essences.py), offered as the Alloys are:
a random modifier lost, a guaranteed essence-only one added, DPS at the mean over the outcomes, floors at the worst."""
from __future__ import annotations

import faulthandler

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_essence_rows_follow_the_item_class_and_the_mods_level():
    from builder.search import essences

    mods = {m["id"]: m for m in GameData().mods.get("Item", []) if m["id"] in essences.ESSENCES}
    # every Perfect Essence row the table names is a mod PoB has (Infinite, which has no Mod row, is left out)
    assert set(essences.ESSENCES) == set(mods), set(essences.ESSENCES) - set(mods)
    mace = {"type": "One Hand Mace"}
    assert sorted(e for e, _ in essences.rows_for(mace, 82, mods)) == [
        "Perfect Essence of Abrasion", "Perfect Essence of Battle", "Perfect Essence of Electricity",
        "Perfect Essence of Flames", "Perfect Essence of Haste", "Perfect Essence of Ice"]
    assert essences.rows_for(mace, 71, mods) == []  # (every Perfect Essence mod is level 72)
    battle = dict(essences.rows_for(mace, 82, mods))["Perfect Essence of Battle"]
    assert battle["1"] == "+2 to Level of all Attack Skills" and battle["type"] == "Suffix"  # (0.5.0: +2 one-handed)
    two = dict(essences.rows_for({"type": "Two Hand Mace"}, 82, mods))["Perfect Essence of Battle"]
    assert two["1"] == "+3 to Level of all Attack Skills"
    warstaff = {"type": "Staff", "subType": "Warstaff"}
    assert essences.item_class(warstaff) == "Warstaff" and "Perfect Essence of Battle" in dict(
        essences.rows_for(warstaff, 82, mods))
    wand = dict(essences.rows_for({"type": "Wand"}, 82, mods))
    assert wand["Perfect Essence of Sorcery"]["1"] == "+3 to Level of all Spell Skills"
    assert "Perfect Essence of Battle" not in wand
    # its group is its own: it sits beside the natural "+N to Level of all Spell Skills" affix
    assert wand["Perfect Essence of Sorcery"]["group"] == "EssenceSpellSkillLevel"
    assert essences.rows_for({"type": "Staff"}, 82, mods)[0][1]["1"] in (
        "+5 to Level of all Spell Skills", "(28-32)% increased Mana Cost Efficiency")


def test_essences_are_off_unless_switched_on(monkeypatch):
    from builder.search import essences

    monkeypatch.delenv("POE2_SEARCH_ESSENCES", raising=False)
    assert not essences.enabled()
    monkeypatch.setenv("POE2_SEARCH_ESSENCES", "1")
    assert essences.enabled()


def test_a_wand_rare_takes_perfect_essence_of_sorcery_for_a_spell(engine, monkeypatch):
    """Spark on a rare wand: with Sorcery priced, the essence trial offers it (+3 to Level of all Spell Skills) at its
    mean-outcome DPS, the worst outcome equipped; with no price, no offer."""
    from builder.search import essences
    from builder.search.gear import affix_limits
    from builder.search.rares import rare_text

    monkeypatch.setenv("POE2_SEARCH_ESSENCES", "1")
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gear = search.build, search.gear
    wand = next((b for b in search.ctx.data.bases if b["type"] == "Wand" and not b.get("hidden")
                 and (b.get("req") or {}).get("level", 0) <= search.ctx.level), None)
    slot = "Weapon 1"
    gear.lam = 0.0
    pools, level = gear.pools, gear.pools.item_level
    pool = pools.pool(wand["name"])
    # a plain rare of a spell-damage prefix and a modifier worth nothing here: the essence loses one of the two at random,
    # so its outcomes are (worthless + Sorcery) and (spell damage + Sorcery). Probe POE2T6207: on one modifier alone the
    # trial rightly offers nothing (its one outcome loses the 48% Spell Damage: 103 DPS against the plain rare's 109)
    spell = next(a for a in pool if "increased Spell Damage" in " ".join(a.lines) and a.type == "Prefix")
    empty = gear.deltas(slot, [rare_text(slot, wand, level, [])])[0][0]
    groups = {m["group"] for _, m in essences.rows_for(wand, level, gear.essence_mods)} | {spell.group}
    scores = gear.deltas(slot, [rare_text(slot, wand, level, [a]) for a in pool])
    worthless = [a for (s, _), a in zip(scores, pool) if abs(s - empty) < 1e-9 and a.group not in groups]
    if not worthless:
        pytest.skip("no worthless wand modifier on this build")
    chosen = [spell, worthless[0]]
    current = gear.deltas(slot, [rare_text(slot, wand, level, chosen)])[0][0]
    plain_option = next(o for o in search.ctx.item_options["Wand"] if not o.unique and o.base == wand["name"])
    search.ctx.essence_prices = {}
    assert gear.essence_trial(slot, wand, plain_option, level, pools, affix_limits(wand), chosen, current, 1e9,
                              clock.now() + 120) is None  # (no price: no offer)
    search.ctx.essence_prices = {name: 0.01 for name, _ in essences.ESSENCES.values()}
    option = gear.essence_trial(slot, wand, plain_option, level, pools, affix_limits(wand), chosen, current, 1e9,
                                clock.now() + 120)
    print("PROBE essence", option and (option.alloy, option.alloy_scale, option.alloy_report, option.price))
    assert option is not None and option.alloy == "Perfect Essence of Sorcery" and option.augment == "essence"
    assert "+3 to Level of all Spell Skills" in option.raw
    assert option.describe()["essence"]["name"] == "Perfect Essence of Sorcery" and "alloy" not in option.describe()
    assert option.alloy_scale > 1.0  # (the worst outcome, the spell damage lost, is equipped; the mean is above it)
    assert "Worst case" in option.alloy_report[2] and "increased Spell Damage" in option.alloy_report[2]
    assert build.alloy_raws  # (its DPS factor is registered as an Alloy's is)
