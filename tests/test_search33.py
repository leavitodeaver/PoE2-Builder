"""SEARCH33: Build.restore keeps the config inputs a reload dropped (train 108), and the side groups' supports
(gems.side_groups / optimize_supports): only the main group, the meta group and a granted meta holding a spell were ever
given supports, so persistent buffs, Blasphemy curses and skills a notable or item grants stayed bare (2b75b's delivered
I2: Wildwood's Gifts, Blasphemy + Despair, Archon of Chayula)."""
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

def test_a_side_group_gets_its_supports_and_the_score_never_falls(ctx, engine, monkeypatch):
    from builder.search import gems as gems_mod
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    # (a hand-cast curse: Heightened Curse's what-if is +0.078 on it; in a Blasphemy group every support read 0.0,
    # probe POE2T5449, scratch/SEARCH33/d5449.log)
    side = build.set_group(gems=[{"name": "Elemental Weakness", "level": 20, "quality": 0}], label="test")["index"]
    found = gems.side_groups()
    assert side in [g["index"] for g in found]
    assert not any(g["main"] for g in found)
    called = []
    real = gems_mod.Gems.supports_round

    def spy(self, full_sweep=False, meta=False, granted=None):
        called.append(granted)
        return real(self, full_sweep=full_sweep, meta=meta, granted=granted)

    monkeypatch.setattr(gems_mod.Gems, "supports_round", spy)
    before = build.score
    gems.optimize_supports(float("inf"), rounds=2)
    assert side in called
    assert build.score >= before - 1e-9
    group = next(g for g in build.groups() if g["index"] == side)
    supports = [g["name"] for g in group["gems"] if g["support"]]
    assert supports, "a hand-cast Elemental Weakness on a Spark build: Heightened Curse raises the score"
    # a second step with nothing changed doesn't sweep an idle side group again (side_idle), but a kept group is retried
    called.clear()
    gems.optimize_supports(float("inf"), rounds=1)
    assert build.score >= before - 1e-9
