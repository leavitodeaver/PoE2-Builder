"""Skills that gain damage as an element from an Elemental Ground in their path (PATCH_CHECK MA-2 / MA-6 / MA-7; MECH78):
Eye of Winter (and Orbs), Twister, Whirling Slash / Whirlwind Lance. PoB gates each gain on a box nothing set."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import conditions

GRIP = ["Wind Skills which can be boosted by Elemental Ground Surfaces count", "as being boosted by Shocked Ground"]
TAMING = ["Wind Skills which can be boosted by Elemental Ground Surfaces can be boosted by multiple Elemental Ground "
          "Surfaces", "Wind Skills which can be boosted by Elemental Ground Surfaces count",
          "as being boosted by Ignited, Shocked, and Chilled Ground"]


def test_eye_of_winter_takes_every_ground_and_orb_at_its_share():
    stats = {"_shocked_ground": (0.5, "Charged Mark"), "_chilled_ground": (0.2, "Frost Nexus")}
    out = conditions.element_gain_shares("Eye of Winter", stats, [], {"Lightning": (0.6, "Orb of Storms up 60%")})
    assert out["Lightning"][0] == pytest.approx(1 - 0.5 * 0.4)  # (ground or Orb, independent)
    assert out["Cold"][0] == pytest.approx(0.2)
    assert out["Fire"][0] == 0.0 and "isn't modelled" in out["Fire"][1]
    # nothing in its path: 0 for every element (no source, 0)
    assert all(v[0] == 0.0 for v in conditions.element_gain_shares("Eye of Winter", {}, [], {}).values())
    # the ring lines are for wind skills only
    assert conditions.element_gain_shares("Eye of Winter", {}, GRIP)["Lightning"][0] == 0.0


def test_a_wind_skill_holds_one_element_unless_the_taming_says_several():
    stats = {"_shocked_ground": (0.5, ""), "_chilled_ground": (0.4, "")}
    out = conditions.element_gain_shares("Twister", stats, [])
    assert out["Lightning"][0] == pytest.approx(1 - 0.5 * 0.6) and out["Cold"][0] == 0.0  # (the most-held element)
    assert "one element at a time" in out["Cold"][1]
    # Berek's Grip: always boosted by Shocked Ground, one element: Lightning all fight, nothing else
    out = conditions.element_gain_shares("Whirling Slash", stats, GRIP)
    assert out["Lightning"][0] == 1.0 and out["Cold"][0] == 0.0 and out["Fire"][0] == 0.0
    # The Taming: all three at once
    out = conditions.element_gain_shares("Whirlwind Lance", {}, TAMING)
    assert [out[e][0] for e in conditions.GROUND_ELEMENTS] == [1.0, 1.0, 1.0]


def test_the_box_is_the_main_skills_own():
    stats = {"_main_skill": "Twister", "_element_gain": {"Lightning": (0.5, "x")}}
    on, basis, up = conditions._element_gain("twister", "Lightning")([], stats)
    assert on and up == pytest.approx(0.5) and "Twister" in basis
    assert conditions._element_gain("eyeOfWinter", "Lightning")([], stats) == (0.0, "")
    stats = {"_main_skill": "Whirling Slash", "_element_gain": {"Cold": (1.0, "x")}}
    assert conditions._element_gain("bridgeConditionWhirlwindBuff", "Cold")([], stats)[0]
    vars_ = {c.var for c in conditions.CONDITIONS}
    assert {"eyeOfWinterCold", "twisterFire", "bridgeConditionWhirlwindBuffLightning"} <= vars_


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def _search(engine, skill: str, ascendancy: str):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill, ascendancies=[ascendancy]))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    return search


def test_eye_of_winter_through_an_orb_of_storms_gains_lightning(engine):
    """Eye of Winter beside Orb of Storms (an Orb: 0.2.0e): the Lightning box on at the Orb's uptime, worth damage."""
    search = _search(engine, "Eye of Winter", "Stormweaver")
    if "Orb of Storms" in {n["name"] for g in search.build.call("groups")["groups"] for n in g["gems"]}:
        pytest.skip("the skeleton already carries Orb of Storms")
    assert not search.conditions.active.get("eyeOfWinterLightning")
    search.build.call("addSkillGroup", text="Orb of Storms 20/0 1")
    search.settle()
    # (kept up all fight: one Orb recast as each ends, so the box is simply on, not a part-time row)
    assert search.conditions.active.get("eyeOfWinterLightning")
    assert "Orb of Storms" in search.conditions.active["eyeOfWinterLightning"]["evidence"]
    build = search.build
    on = build.call("stats", fields=["FullDPS"])["FullDPS"]
    off = build.call("setConfig", vars={"eyeOfWinterLightning": False}, fields=["FullDPS"])["stats"]["FullDPS"]
    print("EYE", on, off, search.conditions.active["eyeOfWinterLightning"])
    assert off < on


def test_a_whirlwind_boosted_by_bereks_grip_gains_lightning_without_shocking_the_boss(engine):
    """Berek's Grip (PoB doesn't parse it): Whirling Slash's Whirlwinds count as boosted by Shocked Ground all fight. The
    tool sets only the gain's PoB condition: the boss isn't made Shocked by it (PoB's own box would)."""
    search = _search(engine, "Whirling Slash", "Amazon")
    build = search.build
    ring = "\n".join(["Rarity: Unique", "Berek's Grip", "Two-Stone Ring", "Item Level: 82", "Implicits: 1",
                      "+12% to Cold and Lightning Resistances", *GRIP])
    build.call("equip", slot="Ring 1", raw=ring, fields=[])
    search.settle()
    assert search.conditions.active.get("bridgeConditionWhirlwindBuffLightning")
    on = build.call("stats", fields=["FullDPS"])["FullDPS"]
    off = build.call("setConfig", vars={"bridgeConditionWhirlwindBuffLightning": False}, fields=["FullDPS"])["stats"]
    enemy = build.call("setConfig", vars={"bridgeConditionWhirlwindBuffLightning": True},
                       fields=["FullDPS"])["stats"]
    print("WHIRL", on, off, enemy)
    assert off["FullDPS"] < on and enemy["FullDPS"] == pytest.approx(on)
    assert not search.conditions.active.get("conditionEnemyShocked")


def test_viciousness_counts_the_one_boss_in_close_range():
    """PATCH_CHECK MB-16: PoB's "per enemy in close range" reads Multiplier:NearbyEnemies, which nothing set (0)."""
    assert conditions._nearby_enemies(["3% increased Attack Speed per Enemy in Close Range"], {})[0] == 1
    assert conditions._nearby_enemies(["10% increased Attack Speed"], {}) == (0, "")
    assert "multiplierNearbyEnemies" in {c.var for c in conditions.CONDITIONS}


def test_escalating_toxins_counts_the_main_skills_poisons_in_the_past_4_s():
    """PATCH_CHECK MB-12: Multiplier:PoisonAppliedRecently was never set (0)."""
    line = ["10% increased Poison Duration for each Poison you have inflicted Recently, up to a maximum of 100%"]
    count, how = conditions._poisons_recently(line, {"Speed": 2.0, "HitChance": 90.0, "PoisonChancePerHit": 50.0})
    assert count == pytest.approx(2.0 * 0.9 * 0.5 * 4) and "Poisons" in how
    assert conditions._poisons_recently(line, {"Speed": 2.0, "PoisonChancePerHit": 0.0})[0] == 0
    assert conditions._poisons_recently(["10% increased Poison Duration"], {"Speed": 2.0}) == (0, "")


def test_open_weakness_holds_45_percent_of_the_life_and_less_of_the_time():
    """PATCH_CHECK MC-1: 45% of the boss's Life, gone through faster: 50% more damage -> 0.3 / 0.85 of the time, and
    the weighted DPS is the fight's real average, 1 / (0.45 / on + 0.55 / off)."""
    ratio = 1 / 1.5
    share = conditions.open_weakness_share(ratio)
    assert share == pytest.approx(0.3 / 0.85)
    on, off = 1.5, 1.0
    assert on * (share + (1 - share) * ratio) == pytest.approx(1 / (0.45 / on + 0.55 / off))
    assert conditions.open_weakness_share(1.0) == pytest.approx(0.45)
    assert conditions._open_weakness(False)([], {}) == (0.0, "")
    far = {"_open_weakness": (0.35, "x"), "PresenceRadius": 40, "_distance_choice": (8.0, "standing back")}
    assert conditions._open_weakness(False)([], far)[0]
    assert conditions._open_weakness(True)([], far)[0] == 0.0


def test_predatory_instinct_sets_the_open_weakness_box_at_its_time_share(engine):
    search = _search(engine, "Whirling Slash", "Amazon")
    build = search.build
    assert not search.conditions.active.get("conditionEnemyHasOpenWeakness")
    build.call("allocate", add=[55796], remove=[], attribute=1, fields=[])  # Predatory Instinct
    search.settle()
    up = search.conditions.uptimes.get("conditionEnemyHasOpenWeakness", 0.0)
    print("OPEN", up, search.conditions.active.get("conditionEnemyHasOpenWeakness"))
    assert search.conditions.active.get("conditionEnemyHasOpenWeakness")
    assert 0.25 < up < 0.45  # (50% more inside: about 0.35)


def test_impending_doom_counts_a_hand_cast_curse_past_half_its_duration():
    """PATCH_CHECK MD-8: multiplierCurseExpiredOnEnemy was never set (0)."""
    from builder.search import models

    line = ["Your Curses have 20% increased Magnitudes if 50% of Curse Duration expired"]
    hand = models.CurseUse("Vulnerability", 0.9, "x", by_hand=True, duration=8.0)
    value, how, share = conditions._curse_expired(line, {"_curse_uses": [hand]})
    assert value == 50 and share == pytest.approx(0.45) and "Vulnerability" in how
    blast = models.CurseUse("Despair", 1.0, "x", by_hand=True, hexblast=True)
    on_hit = models.CurseUse("Enfeeble", 1.0, "x")
    assert conditions._curse_expired(line, {"_curse_uses": [blast, on_hit]})[0] == 0
    assert conditions._curse_expired(["20% increased Curse Magnitudes"], {"_curse_uses": [hand]}) == (0, "")


def test_warcries_debilitate_the_boss_for_a_second_per_warcry():
    """PATCH_CHECK MB-15: PoB's Debilitated box was never set (KeywordPopups: 10% less damage dealt, 1 s by default)."""
    stats = {"_events": {"Warcry": (0.25, "Infernal Cry every 4 s")}, "_main_types": {"Attack", "Melee"}}
    on, basis, up = conditions.debuff_uptime("conditionEnemyDebilitated")(["Warcries Debilitate Enemies"], stats)
    assert on and up == pytest.approx(0.25) and "Warcries/s" in basis
    assert conditions.debuff_uptime("conditionEnemyDebilitated")(["Warcries Debilitate Enemies"],
                                                                 {"_main_types": {"Attack"}})[2] == 0.0
    assert "conditionEnemyDebilitated" in {c.var for c in conditions.CONDITIONS}
