"""GEM45: Triskelion Cascade in the score (models.triskelion_cascade_model; R61-16, R62-1, R62-2)."""
import math

import pytest

from builder.search import models

LVL20 = {models.TRISKELION_DAMAGE: -11, models.TRISKELION_AREA: -31, models.TRISKELION_CD_PCT: 300}
CASCADABLE = {"Spell", "Area", "Cascadable"}


def test_triskelion_boss_hits_follow_r62_geometry():
    # R62-1's simulation (scratch/research62/trisk3.py): a 2 m spell at level 20's 31% less area, an arm aimed at the
    # boss, boss hit radius 0.3 / 0.6 m: melee 2.0 / 2.0, 2 m 1.0 / 2.0, 4 m 1.6 / 1.8, 6 m 0.5 / 0.6
    r = 2.0 * math.sqrt(0.69)
    want = {1: (2.0, 2.0), 2: (1.0, 2.0), 4: (1.6, 1.8), 6: (0.5, 0.6)}
    for d, (a, b) in want.items():
        assert round(models.triskelion_hits(r, d, 0.3), 1) == a and round(models.triskelion_hits(r, d, 0.6), 1) == b
    assert len(models.triskelion_centres(1.0)) == 7 and len(models.triskelion_centres(1.0, per_arm=3)) == 10
    outer = models.triskelion_centres(1.0)[2]  # one more step on, turned 30 degrees: ~2.9 radii out
    assert math.hypot(*outer) == pytest.approx(2.90, abs=0.01)
    assert models.triskelion_hits(0.0, 1.0, 0.3) == 0.0


def test_triskelion_press_cycle():
    # a 1 s cast at 1 a second: cooldown 1 + 300% x 1 s = 4 s; its own 0.45 s cast and 1 Empowered cast; 2.55 normal
    f, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3)
    assert e == pytest.approx(0.25) and f == pytest.approx((0.89 * 3 + 2.55) / 4)
    f, _ = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, base_hits=3)  # Spell Cascade's 3 normal hits
    assert f == pytest.approx((0.89 + 2.55) / 4)
    f, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, empowers=3)  # Olroth: 3 Empowered casts
    assert e == pytest.approx(0.75) and f == pytest.approx((3 * 0.89 * 3 + 0.55) / 4)
    _, e = models.triskelion_cascade_factor(1.0, 1.0, 1.0, -11, 300, 3, ward_period=10.0)  # Runic Ward: every 10 s
    assert e == pytest.approx(0.1)
    assert models.triskelion_cascade_factor(1.0, 1.0, 0.0, -11, 300, 3) == (1.0, 0.0)


def test_triskelion_model_gain_close_loss_far_and_rows():
    comet = models.Skill("Comet", 1, spell=True)
    near_mods, near = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test")
    assert near_mods and near_mods[0]["name"] == "DPS" and near_mods[0]["value"] > 0
    assert near[0]["entry"] == models.TRISKELION_ENTRY and "random facing" in near[0]["basis"]
    assert "without it x1.000" in near[0]["play"]
    far_mods, far = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 8.0, 0.3, "test")
    assert far_mods == [] and "not pressed" in far[0]["value"]  # the pattern centres on you: a loss at range
    _, casc = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test", base_hits=3.0,
                                              cascade=True)
    assert "Spell Cascade's own areas only" in casc[0]["basis"] and "10 areas" in casc[0]["basis"]
    _, ap = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 8.0, 0.3, "test", at_target=True)
    assert "MOST DPS AT STAKE" in ap[0]["basis"]
    mods, broke = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test",
                                                  ward_cost=50.0, ward_spare=0.0)
    assert mods == [] and "no Runic Ward" in broke[0]["basis"]
    mods, slow = models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "test",
                                                 ward_cost=50.0, ward_spare=5.0)
    assert "every 10.0 s" in slow[0]["basis"] and mods[0]["value"] < near_mods[0]["value"]
    assert models.triskelion_cascade_model(comet, {"Spell"}, 1.0, LVL20, 2.0, 1.0, 0.3, "t") == ([], [])
    trig = models.Skill("Comet", 1, spell=True, triggered=True)
    mods, notes = models.triskelion_cascade_model(trig, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "t")
    assert mods == [] and "UNVERIFIED" in notes[0]["basis"]
    assert models.triskelion_cascade_model(comet, CASCADABLE, 1.0, LVL20, 2.0, 1.0, 0.3, "t",
                                           forbidden={models.TRISKELION_ENTRY}) == ([], [])


def test_earthquake_jagged_note_only_on_the_scored_aftershock():
    quake = models.Skill(models.EARTHQUAKE, 1, stat_set=models.EARTHQUAKE_SET)
    note = models.earthquake_jagged_note(quake, models.EARTHQUAKE_SET)
    assert note and note[0]["entry"] == models.EARTHQUAKE_JAGGED_ENTRY and "R63-1" in note[0]["basis"]
    assert models.earthquake_jagged_note(quake, "Impact") == []
    assert models.earthquake_jagged_note(models.Skill("Boneshatter", 1), models.EARTHQUAKE_SET) == []


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    eng = PobEngine()
    yield eng
    eng.close()
    if enabled:
        faulthandler.enable()


def test_earthquake_aftershock_without_pobs_jagged_ground_more(engine):
    # (GEM45, R63-1) the bridge mutes jagged_ground_effect_+% on the Aftershock set; PoB's own Damage MORE map, put back
    # in this process only, multiplies the Aftershock's hit (x2.915 on the probe skeleton) and leaves the Impact alone
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Earthquake"))
    s = Search(engine, None, payload, enumerate_skeletons(Context(payload), ["Earthquake"])[0][0].as_dict(), 0)
    s.setup(quests=False)
    index = s.build.main_group()["index"]

    def sets():
        return {x["label"]: x.get("AverageDamage") for x in engine.call("setRates", index=index, active=1)["sets"]}
    muted = sets()
    statmap = 'data.skills["EarthquakePlayer"].statSets[2].statMap'
    engine.lua.run('rawset(' + statmap + ', "jagged_ground_effect_+%", '
                   '{ { name = "Damage", type = "MORE", flags = 0, keywordFlags = 0 } })', "gem45_remap")
    try:
        pob = sets()
    finally:
        engine.lua.run('rawset(' + statmap + ', "jagged_ground_effect_+%", { })', "gem45_unmap")
    assert pob["Impact"] == pytest.approx(muted["Impact"])
    assert pob["Aftershock"] / muted["Aftershock"] > 1.9  # PoB's more, gone from our number
    assert sets()["Aftershock"] == pytest.approx(muted["Aftershock"])
