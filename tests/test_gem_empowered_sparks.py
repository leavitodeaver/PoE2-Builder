"""GEM28: Empowered Sparks II through a real engine. PoB has no such gem; the bridge adds it from the game's rows
(engine/bridge.lua, Empowered Sparks II's block; RESEARCH36 R36-16: obtainable in 0.5): I's support and Empower skill,
its own Spark with 2 more projectiles at every level and the same damage. Boots one real PoB engine for the module and
needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def setup():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    engine = PobEngine()
    payload = make_payload(BuildRequest(main_skill="Spark"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    yield ctx, search.build
    if enabled:
        faulthandler.enable()


def spark_row(build, support: str) -> dict:
    """The triggered Spark's first stat set (setRates) in a side group of Siphoning Strike + `support`."""
    index = build.set_group(gems=[{"name": "Siphoning Strike", "level": 20, "quality": 0}, {"name": support}])["index"]
    try:
        group = next(g for g in build.groups() if g["index"] == index)
        names = [s["name"] for s in group.get("skills") or []]
        assert "Empowered Sparks" in names, names
        position = names.index("Empowered Sparks") + 1
        return (build.call("setRates", index=index, active=position)["sets"] or [{}])[0]
    finally:
        build.remove_group(index)


def test_empowered_sparks_two_is_offered_and_fires_two_more_sparks_than_one(setup):
    ctx, build = setup
    gem = ctx.gems.get("Empowered Sparks II")
    assert gem is not None and gem["support"] and gem["tier"] == 4 and not gem.get("fromItem")
    one, two = spark_row(build, "Empowered Sparks I"), spark_row(build, "Empowered Sparks II")
    assert one.get("ProjectileCount") and two.get("ProjectileCount") == pytest.approx(one["ProjectileCount"] + 2)
    assert two.get("AverageDamage") == pytest.approx(one.get("AverageDamage"))
