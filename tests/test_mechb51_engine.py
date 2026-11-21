"""MECHB51: Spectre / Tamed Companion step 3 (MECHB50's drafts): Assumptions.choose_monsters and bridge.syncMonsters."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search import models
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


def test_the_search_chooses_a_spectre_and_keeps_an_imported_one(engine):
    payload = make_payload(BuildRequest(main_skill="Raise Zombie", level=90))
    skeleton = enumerate_skeletons(Context(payload), ["Raise Zombie"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, assume = search.build, search.assumptions
    spectre = build.set_group(gems=[{"name": "Summon Spectre"}], fullDPS=True)["index"]
    before = build.call("stats", fields=["FullDPS"])["FullDPS"]
    assert assume.choose_monsters() is True
    skill = next(s for s in build.groups()[spectre - 1]["skills"] if s.get("id") == "SummonSpectrePlayer")
    assert skill["minion"] in models.monster_candidates("spectreList")
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] > before
    assert assume.monster_notes and assume.monster_notes[0]["confidence"] == "LOW"
    assert assume.choose_monsters() is False  # (nothing changed: no new choice, no new rating)
    unknown = next(m for m, v in models.monster_catalog().items() if v["source"] == "unknown")
    build.call("setMonster", index=spectre, id=unknown)  # (an imported build's monster of the 105)
    assert assume.choose_monsters() is False and "UNVERIFIED" in assume.monster_notes[0]["basis"]
    build.remove_group(spectre)
    assert build.call("syncMonsters")["changed"] is True  # (its monster leaves PoB's list)
