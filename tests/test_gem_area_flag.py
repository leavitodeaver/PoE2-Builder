"""GEM2: stat sets the game marks is_area_damage get PoB's Area flag (engine/bridge.lua installAreaDamageFlag).

GEM1's probe POE2T2016 read x1.0 with "100% increased Area Damage" on Falling Thunder's Projectile set (the game marks it
is_area_damage; PoB's baseFlags had no area) and x2 on its Melee set (PoB's area flag): an undercount for any build with
Area Damage modifiers on such a set."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest

AREA = "100% increased Area Damage"


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


def test_area_damage_reaches_the_sets_the_game_marks_area_damage(engine):
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Falling Thunder", level=90, budget={"gear_tier": "endgame"},
                                        objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), ["Falling Thunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup()
    build = search.build
    group = build.call("setGroup", gems=[{"name": "Falling Thunder"}], main=True, fullDPS=True,
                       fields=["AverageHit"])["group"]
    skill = next(i for i, s in enumerate(group["skills"], 1) if s["name"] == "Falling Thunder")
    sets = group["skills"][skill - 1].get("statSets") or []

    def hit(label: str) -> float:
        index = sets.index(label) + 1
        return build.call("setGroup", index=group["index"], mainActiveSkill=skill, statSet=index,
                          fields=["AverageHit"])["stats"]["AverageHit"]

    labels = [s for s in ("Projectile", "Melee") if s in sets]
    assert "Projectile" in labels, sets
    off = {s: hit(s) for s in labels}
    build.call("setConfig", vars={"customMods": AREA})
    try:
        on = {s: hit(s) for s in labels}
    finally:
        build.call("setConfig", vars={"customMods": "nil"})
    assert off["Projectile"] > 0
    # before the flag x1.0; with it, Area Damage counts as on any area set (about x2 from 100% increased)
    assert on["Projectile"] / off["Projectile"] > 1.5
    if "Melee" in labels:  # (PoB's own area set, unchanged: the control)
        assert on["Melee"] / off["Melee"] > 1.5
