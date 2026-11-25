"""MECHB64 (MECH-B): RESEARCH60 R60-1, Bonestorm's spike explosions with Pierce / Chain / Fork on the skill."""
import faulthandler

import pytest

from builder.search import models


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def test_pierce_chain_or_fork_on_bonestorm_moves_its_explosion_off_the_struck_boss():
    """R60-1: with Pierce, Chain or Fork on Bonestorm the spike explodes at the end of its flight: the headline counts
    the spikes alone (MEDIUM-LOW); the explosion on the struck boss is the row."""
    plain = [{"label": "Projectile", "TotalDPS": 1000.0}, {"label": "Explosion", "TotalDPS": 500.0}]
    assert models.bonestorm_flight_moves(plain[0]) == []
    for field, name in (("PierceCount", "Pierce"), ("ChainMax", "Chain"), ("ForkCountMax", "Fork")):
        sets = [{**plain[0], field: 1}, plain[1]]
        assert models.bonestorm_flight_moves(sets[0]) == [name]
        mods, notes = models.multi_set_model("Bonestorm", "Projectile", sets)
        assert mods == [] and name in notes[0]["basis"] and "x1.500" in notes[0]["basis"]
        assert notes[0]["confidence"].startswith("MEDIUM-LOW") and "R60-1" in notes[0]["basis"]
        mods, notes = models.multi_set_model("Bonestorm", "Explosion", sets)  # (PoB scoring the explosion: spikes only)
        assert mods[0]["value"] == pytest.approx(100.0) and notes[0]["value"] == "x2.000"
    mods, notes = models.multi_set_model("Bonestorm", "Projectile", plain)
    assert mods[0]["value"] == pytest.approx(50.0) and notes[0]["confidence"].startswith("MEDIUM (R60-1")


def test_a_bonestorm_feeder_with_pierce_counts_its_own_hit_and_shows_the_explosion_as_a_row():
    """R60-1 for Cast on Critical: "beside_off" (Pierce / Chain / Fork) adds no Energy; its reading is the row."""
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    spike = {"HitChance": 100.0, "CritChance": 50.0, "PhysicalCritAverage": 2000.0}
    blast = {"HitChance": 100.0, "CritChance": 50.0, "PhysicalCritAverage": 500.0}
    feeder = {"name": "Bonestorm", "uses": 0.0, "speed": 8.0, "why": "", "final": 0.0, "row": spike}
    alone, crits_alone, _ = models.energy_feed("crit", [feeder], stats)
    off, crits, how = models.energy_feed("crit", [{**feeder, "beside_off": ("Pierce", [blast])}], stats)
    assert off == pytest.approx(alone) and crits == pytest.approx(crits_alone)
    row = (alone + 8.0 * 0.5 * 20 * 0.5) / 8.0
    assert "Pierce on it moves the explosion" in how and f"{row:.2f} Energy a use" in how and "R60-1" in how


def test_energy_past_the_maximum_lost_is_a_row():
    """R60-1's open question (UNVERIFIED): the headline carries Energy past Cast on Critical's maximum; the row loses
    it, so a round takes the next whole number of crit hits (build 4's shape: 3.18 a round -> 4, x0.796)."""
    row = models.energy_overflow_row(11.038, 35.15, 11.038)
    assert "UNVERIFIED" in row and "4 hits a round" in row and "x0.796" in row
    assert models.energy_overflow_row(10.0, 30.0, 10.0) == ""  # (a whole 3 hits a round: nothing lost either way)
    assert models.energy_overflow_row(0.0, 30.0, 0.0) == ""


def test_skyfall_makes_six_comet_spawn_on_its_verisium_infused_share():
    """MECHB63's next (c): 3 a cast, 6 when the cast consumes a Verisium Infusion: 3 + 3 x the share of casts finding one
    (Assumptions.skyfall_infused); no share, 3 a cast as before."""
    plain, _ = models.skyfall_minion_model(0.05, "Glory", 12.0, 20.0, 0.0)
    half, notes = models.skyfall_minion_model(0.05, "Glory", 12.0, 20.0, 0.0, None, "", (0.5, "x"))
    assert plain == pytest.approx(3 * 0.05 * 20) and half == pytest.approx(4.5 * 0.05 * 20)
    assert "50% of casts Verisium-Infused" in notes[0]["basis"]
    capped, _ = models.skyfall_minion_model(1.0, "Glory", 12.0, 20.0, 0.0, None, "", (1.0, "x"))
    assert capped == pytest.approx(12.0)  # (the Limit binds: the share moves nothing)


def test_skyfall_shares_the_builds_verisium_infusions_with_the_other_consumers(engine):
    """Assumptions.skyfall_infused on real PoB: Powered by Verisium's Ward-paid uses make the Verisium Infusions (its
    count a cast from the gem), shared evenly with the main Fireball's casts (it consumes Fire Infusions)."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball"))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Skyfall 20/0 1")
    texts = [line["text"] for line in build.call("sourceLines")["lines"]]
    a = search.assumptions
    assert a.skyfall_infused(0.1, texts) == (0.0, "the build makes no Verisium Infusion")
    build.call("addSkillGroup", text=f"{assumptions.VERISIUM} 20/0 1")
    build.ward_sustain = {assumptions.VERISIUM: (1.0, 0.5)}  # (0.5 Ward-paid uses a second)
    count = assumptions.verisium_infusions(build)[0]
    share, how = a.skyfall_infused(0.1, texts)
    print(count, share, how)
    assert count > 0 and 0 < share < 1 and "Fireball" in how and "Skyfall's 0.100 casts/s" in how
    alone = assumptions.infusion_found(count, 0.1, 0.0, assumptions.infusion_cap(texts, "Fire", True)
                                       - assumptions.infusion_cap(texts, "Fire"))
    assert share < alone  # (Fireball's casts take their share)


def test_the_mana_cap_row_scales_the_main_skill_and_its_feeders_together():
    """MECHB64 turn 2 (the manager 07:43; R60-4 open): income over the main skill's and its meta feeders' Mana a second;
    a row only, and none when the build pays its costs."""
    feeder = {"name": "Bonestorm", "used": 9.0, "speed": 18.0, "row": {"manaPerSecond": 600.0}}
    row = models.mana_cap_row(390.0, "ManaRegenRecovery 390.0", "Bone Cage", 1000.0, [feeder], True)
    assert row["label"] == models.MANA_CAP_LABEL and "x0.300" in row["value"] and "x1 (the headline)" in row["value"]
    assert "1,300.0 Mana a second" in row["basis"] and "R60-4" in row["basis"] and "50% of the time" in row["basis"]
    assert models.mana_cap_row(2000.0, "", "Bone Cage", 1000.0, [feeder], True) is None
    assert models.mana_cap_row(10.0, "", "Bone Cage", 0.0, [], True) is None
    hand = models.mana_cap_row(100.0, "", "Spark", 400.0, [], False)
    assert "x0.250" in hand["value"] and "R60-4" not in hand["basis"]
