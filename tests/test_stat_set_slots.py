"""One stat set, two stats, one slot (PERF21): PoB merges a stat set's stats in a table order LuaJIT picks anew in each
process and keeps the last value written to a skillData key, so a set whose map lets two stats write one key answers
differently from one PoB process to the next (Earthquake's Aftershock: AreaOfEffectRadius 18 or 30). The bridge leaves
each such slot one writer, the game's (engine/bridge.lua, STAT_SET_SLOT_FIXES)."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
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


def test_no_stat_set_lets_two_stats_write_one_slot(engine):
    """Every stat set PoB ships, every gem level, quality 0 and 20. A set listed here after a PoB upgrade needs the
    game's answer found (which stat is the slot's) and an entry in the bridge's STAT_SET_SLOT_FIXES."""
    answer = engine.call("statSetSlots")
    assert answer["sets"] > 1500  # (1,617 on PoB bb52d6b: the scan saw the data)
    assert not answer["slots"], answer["slots"]


def test_earthquakes_aftershock_has_the_games_radius(engine):
    """3 metres at gem level 20 (patch notes 0.3.0: "a radius of 2.4-3 metres at Gem levels 1-20"; the game's stat
    sets: 18 + 12), in every PoB process; the Jagged Ground's 1.8 metres is the tertiary radius."""
    payload = make_payload(BuildRequest(main_skill="Earthquake"))
    search = Search(engine, None, payload, enumerate_skeletons(Context(payload), ["Earthquake"])[0][0].as_dict(), 0)
    search.setup(quests=False)
    groups = engine.call("groups")["groups"]
    index = next(i for i, group in enumerate(groups, 1) if any(gem.get("name") == "Earthquake" for gem in group.get("gems", [])))
    level = next(gem["level"] for gem in groups[index - 1]["gems"] if gem.get("name") == "Earthquake")
    own = {1: 6, 20: 12}.get(level)  # (GrantedEffectStatSetsPerLevel, EarthquakeAftershockPlayer)
    assert own is not None, level
    engine.call("setGroup", index=index, mainActiveSkill=1, statSet=2)
    stats = engine.call("stats", fields=["AreaOfEffectRadius", "AreaOfEffectRadiusTertiary"])
    assert stats["AreaOfEffectRadius"] == 18 + own
    assert stats["AreaOfEffectRadiusTertiary"] == 18
