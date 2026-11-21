"""MECHB49: Spectre / Tamed Companion step 1, bridge.monsterRates and bridge.setMonster (probe POE2T4728,
scratch/mechb49/p4728c.log)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

BLOOD_ELEMENTAL = "Metadata/Monsters/LiquidElementals/LiquidElementalBlood"  # (PoB Data/Spectres.lua, recommendedSpectre)
SERPENT_SHAMAN = "Metadata/Monsters/SerpentClanMonster/SerpentClanCaster"
SILVERFIST = "Metadata/Monsters/Quadrilla/QuadrillaBossMinion1"  # (Mighty Silverfist, recommendedBeast)


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


def test_the_bridge_rates_spectres_and_companions_and_keeps_the_one_chosen(engine):
    payload = make_payload(BuildRequest(main_skill="Raise Zombie", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Raise Zombie"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    spectre = build.set_group(gems=[{"name": "Summon Spectre"}], fullDPS=True)["index"]
    before = build.call("stats", fields=["FullDPS"])["FullDPS"]
    got = build.call("monsterRates", index=spectre, ids=[BLOOD_ELEMENTAL, "no such id"])
    assert got["kind"] == "spectreList"
    blood, missing = got["rates"]
    assert blood["CombinedDPS"] > 0 and blood["spiritFlat"] > 0 and missing["reason"] == "no such monster"
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(before)  # (the list and choice put back)
    one = build.call("monsterRates", index=spectre, ids=[BLOOD_ELEMENTAL], skills=True)["rates"][0]
    assert one["skills"] and one["skills"][0]["TotalDPS"] > 0
    assert build.call("setMonster", index=spectre, id=BLOOD_ELEMENTAL)["list"] == [BLOOD_ELEMENTAL]
    chosen = build.call("stats", fields=["FullDPS"])["FullDPS"]
    assert chosen == pytest.approx(before + blood["CombinedDPS"], rel=1e-6)
    # (a second choice replaces the first in the list: no stale monster's buffs, CalcPerform 2064)
    assert build.call("setMonster", index=spectre, id=SERPENT_SHAMAN)["list"] == [SERPENT_SHAMAN]
    build.call("setMonster", index=spectre, id=BLOOD_ELEMENTAL)
    xml = build.call("exportXml")["xml"]
    build.call("loadXml", xml=xml)  # (a snapshot / restore keeps the list and the gem's choice)
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(chosen, rel=1e-6)
    beast = build.set_group(gems=[{"name": "Tamed Companion"}], fullDPS=True)["index"]
    silverfist = build.call("monsterRates", index=beast, ids=[SILVERFIST])["rates"][0]
    assert silverfist["CombinedDPS"] > 0 and silverfist["spiritPercent"] > 0
    assert build.call("monsterRates", index=1, ids=[SILVERFIST]).get("reason")  # (not a Spectre / Companion gem)
