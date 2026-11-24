"""Hotfix (the manager, 23 Nov 2026; found by SPEED2's gates POE2T7828 / 7829): `Gear.why_items` looked up candidates for every
equipped slot, Gear.slots has no jewel slots, and the lookup was outside its try, so any build wearing a jewel lost its whole
result at finalize (KeyError: 'Jewel 21984'; 0 DPS, no code). An explanation must never cost the run its build."""
from __future__ import annotations

import faulthandler
import random

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.inner import why_items_safe


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


def plain_option(base: dict) -> ItemOption:
    return ItemOption(name=base["name"], base=base["name"], type=base["type"], sub_type=None, raw=normal_item(base),
                      unique=False, price=0.0, confidence="found", level=1)


def test_a_jewel_in_the_build_does_not_stop_why_items_and_the_gear_keeps_its_rows(engine):
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    gear.put("Boots", plain_option(next(b for b in ctx.data.bases if b["type"] == "Boots")))
    jewel = plain_option(next(b for b in ctx.data.bases if b["type"] == "Jewel"))
    build.equipped["Jewel 21984"] = jewel
    assert "Jewel 21984" not in gear.slots, "the case that crashed: an equipped slot the gear rounds don't own"
    try:
        rows = gear.why_items()
    finally:
        del build.equipped["Jewel 21984"]
    assert rows["Boots"]["reason"], "the gear's own rows are still worked out"
    assert "Jewel 21984" not in rows


def test_an_explanation_that_fails_never_costs_the_build():
    class Broken:
        def why_items(self):
            raise KeyError("Jewel 21984")

    class Works:
        def why_items(self):
            return {"Boots": {"reason": "fine"}}

    assert why_items_safe(Works()) == ({"Boots": {"reason": "fine"}}, None)
    rows, error = why_items_safe(Broken())
    assert rows == {} and "KeyError" in error and "Jewel 21984" in error
