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


def test_commiserate_cancel_undoes_pobs_conditional_40_percent(engine):
    """PoB's Commiserate gives 40% more Ignite Magnitude while you're Ignited (sup_dex.lua 1382-1388); the model's
    x1/1.4 under the same condition takes it back, and its unconditioned share line adds 40% x the share."""
    from builder.search import models

    def ignite(box, mods):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("addSkillGroup", text="Fireball 20/0 1\nCommiserate 1/0 1", main=True)
        engine.call("setAssumptions", mods=mods, fields=[])
        return engine.call("setConfig", vars={"conditionIgnited": box}, fields=["IgniteDPS"])["stats"]["IgniteDPS"] or 0

    stats = {"Speed": 1.0, "HitChance": 100, "IgniteChancePerHit": 100, "IgniteDuration": 4.0}
    mods, _, _ = models.commiserate_model({models.COMMISERATE}, models.Skill("Fireball"), stats,
                                          {"Ignite": {"rate": 0.05, "seconds": 4.0}}, set())
    ignite_mods = [m for m in mods if m.get("keywordFlags") == ["Ignite"]]
    off, on = ignite(False, []), ignite(True, [])
    cancelled = ignite(True, ignite_mods[:1])
    share = ignite_mods[1]["value"] / 40
    added = ignite(False, ignite_mods[1:])
    print("PROBE commiserate", off, on, cancelled, added, share)
    engine.call("setAssumptions", mods=[], fields=[])
    assert off > 0 and on == pytest.approx(off * 1.4, rel=1e-3)
    # PoB rounds each mod store's MORE product to 2 decimals (ModDB.lua MoreInternal 241-246), and the model's cancel sits
    # in another store than Commiserate's 40% (x0.71 for x1/1.4): within 0.7% (probe POE2T5145: 0.08816 vs 0.08870)
    assert cancelled == pytest.approx(off, rel=7e-3)
    assert added == pytest.approx(off * (1 + 0.4 * share), rel=7e-3)
