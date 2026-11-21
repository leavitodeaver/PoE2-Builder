"""SEARCH33: Build.restore keeps the config inputs a reload dropped (train 108 carries this test alone; the side-group
supports tests join this file when their commits land after the re-gate)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, make_payload


@pytest.fixture(scope="module")
def ctx():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return Context(make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices())))


@pytest.fixture(scope="module")
def engine(ctx):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_restore_keeps_a_config_input_at_pobs_own_default(ctx, engine):
    """SEARCH33 (POE2T5484-5486): the chosen "Boss at 2 m" is enemyDistance 20, PoB's default, which PoB exports as a
    Placeholder; Build.restore used to bring it back unset, so the search's distance choice was dropped after every
    taken-back trial. The snapshot keeps the config inputs and the restore puts back the ones the reload dropped."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.set_config({"enemyDistance": 20})
    assert build.call("getConfig")["input"].get("enemyDistance") == 20
    snap = build.snapshot()
    build.set_config({"enemyDistance": 50})
    build.restore(snap)
    assert build.call("getConfig")["input"].get("enemyDistance") == 20
