"""MECH98 (MECH-A), through a real engine: Window to Paradise's "Inflict Elemental Exposure to Enemies 3 metres in front
of you / for 4 seconds, every 0.25 seconds while raised" (an OVERCOUNT before: the boss Exposed all fight within 3 m,
the shield never raised) now needs the build's Raise Shield taps (models.raised_plan): with them the box is on and
Raise Shield is a press; with the taps off (the search's switch) the box is off. Needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.request.schema import BuildRequest
from builder.search import models
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

PARADISE = "\n".join(["Rarity: Unique", "Window to Paradise", "Barricade Tower Shield", "Implicits: 1",
                      "Grants Skill: Raise Shield", "80% increased Armour", "+80 to maximum Mana",
                      "+12% to all Elemental Resistances",
                      "Inflict Elemental Exposure to Enemies 3 metres in front of you",
                      "for 4 seconds, every 0.25 seconds while raised"])


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_window_to_paradise_exposes_the_boss_only_with_the_taps(engine):
    payload = make_payload(BuildRequest(main_skill="Boneshatter"))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    build.call("equip", slot="Weapon 2", raw=PARADISE, fields=[])
    build.set_config({"enemyDistance": 10})  # the boss at 1 m, within the line's 3 m
    search.assumptions.apply()
    found = search.conditions.evidence()
    fire = found.get("conditionEnemyFireExposure")
    presses = getattr(build, models.ITEM_PRESSES, None) or {}
    print("PROBE taps on", fire, presses)
    assert fire is not None and fire[0] is True and "tap Raise Shield" in fire[1]
    assert models.RAISE_SHIELD in presses and presses[models.RAISE_SHIELD][0] == pytest.approx(0.25)

    setattr(build.objective, models.RAISED_TAPS, False)
    try:
        search.assumptions.apply()
        found = search.conditions.evidence()
        fire = found.get("conditionEnemyFireExposure")
        presses = getattr(build, models.ITEM_PRESSES, None) or {}
        print("PROBE taps off", fire, presses)
        assert fire is not None and not fire[0] and "doesn't tap" in fire[1]
        assert models.RAISE_SHIELD not in presses
    finally:
        setattr(build.objective, models.RAISED_TAPS, True)
