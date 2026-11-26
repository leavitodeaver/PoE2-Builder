"""GEM46: a line for some of a skill's stat sets is tagged to those sets (engine/bridge.lua's BridgeStatSet<label>)."""
import pytest

from builder.search import models


def test_a_stat_set_line_carries_its_sets():
    mod = models.line("1% less Damage", 100, skill="Tempest Bell", sets=["Tempest Bell"])
    assert mod["statSets"] == ["Tempest Bell"] and mod["skill"] == "Tempest Bell"
    assert "statSets" not in models.line("1% less Damage", 100, skill="Tempest Bell")


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


def test_the_base_sets_less_damage_reaches_the_base_set_only(engine):
    # (GEM46, MECHB67's POE2T9071) COVC13's "base set deals no damage" 100% less, tagged to the skill's NAME, zeroed
    # Tempest Bell's Impact / Shockwave whenever setRates scored them with the group on its base set (build 20). Tagged
    # to the base set, the damaging sets keep PoB's numbers.
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Tempest Bell"))
    s = Search(engine, None, payload, enumerate_skeletons(Context(payload), ["Tempest Bell"])[0][0].as_dict(), 0)
    s.setup(quests=False)
    index = s.build.main_group()["index"]
    s.build.set_group(index=index, mainActiveSkill=1, statSet=1)  # (the group on its base set, "Tempest Bell")

    def sets(mods):
        engine.call("setAssumptions", mods=mods)
        return {x["label"]: x.get("AverageDamage") or 0 for x in engine.call("setRates", index=index, active=1)["sets"]}
    plain = sets([])
    assert plain["Impact"] > 0 and plain["Tempest Bell"] > 0  # (PoB scores the base set too: the overcount)
    by_name = sets([models.line("1% less Damage", 100, skill="Tempest Bell")])
    assert by_name["Impact"] == 0 and by_name["Tempest Bell"] == 0  # (the old tag: every set zeroed)
    tagged = sets([models.line("1% less Damage", 100, skill="Tempest Bell", sets=["Tempest Bell"])])
    assert tagged["Tempest Bell"] == 0
    for label in ("Impact", "Shockwave"):
        assert tagged[label] == pytest.approx(plain[label]), label
    # (two sets listed: each moves as under a by-name line, the base set not at all; PoB's Impact falls x0.419 under a
    # 50% less, by name too: scratch/gem46/probe_bell.log)
    half = sets([models.line("1% less Damage", 50, skill="Tempest Bell")])
    two = sets([models.line("1% less Damage", 50, skill="Tempest Bell", sets=["Impact", "Shockwave"])])
    assert two["Tempest Bell"] == pytest.approx(plain["Tempest Bell"]) and half["Tempest Bell"] < plain["Tempest Bell"]
    for label in ("Impact", "Shockwave"):
        assert two[label] == pytest.approx(half[label]) and two[label] < plain[label], label
    engine.call("setAssumptions", mods=[])


def _plague(cap=15257.0, gain=4.0):
    buff = models.Skill("Plague Bearer", group=3, level_stats={models.PLAGUE_GAIN: gain, models.PLAGUE_CAP: cap,
                                                               "base_deal_no_damage": 1})
    nova = models.Skill("Plague Bearer", group=3, cast_time=1.0, level_stats={models.PLAGUE_AREA: 6})
    return buff, nova


def test_plague_bearer_released_full_at_the_fill_rate():
    buff, nova = _plague()
    mods, notes = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=1.8, distance=2.0, boss_radius=1.0)
    data = {m["key"]: m["value"] for m in mods if m["kind"] == "SkillData"}
    assert data["PhysicalMin"] == data["PhysicalMax"] == 15257
    assert data["cooldown"] == pytest.approx(15257 / 400, rel=1e-4)  # (4% of 10,000 a second: full every 38 s)
    nova.cooldown_recovery = 1.25  # (PoB divides the cooldown by it: those lines don't fill the store)
    mods, _ = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=1.8, distance=2.0, boss_radius=1.0)
    assert {m["key"]: m["value"] for m in mods if m["kind"] == "SkillData"}["cooldown"] == pytest.approx(15257 / 400 * 1.25, rel=1e-4)
    assert not any("group" in m for m in mods)  # (by name: a group's SkillData misses Full DPS, POE2T9716)
    assert [(m["line"], m["scale"]) for m in mods if m["kind"] == "Line"] == [("1% more Area of Effect", 600)]
    assert notes[0]["entry"] == models.PLAGUE_ENTRY and "38.1 s" in notes[0]["basis"]


def test_plague_bearer_capped_by_its_cast_time():
    buff, nova = _plague(cap=100.0)
    mods, _ = models.plague_bearer_model(buff, nova, 1e6, group=3, radius=1.8, distance=1.0)
    assert {m["key"]: m["value"] for m in mods if m["kind"] == "SkillData"}["cooldown"] == pytest.approx(1.0)


def test_plague_bearer_nothing_stored_or_out_of_reach_deals_nothing():
    buff, nova = _plague()
    mods, notes = models.plague_bearer_model(buff, nova, 0.0, group=3, radius=1.8, distance=1.0)
    assert {m["value"] for m in mods} == {0.0} and "nothing stored" in notes[0]["value"]
    # (1.8 m x sqrt(7) = 4.76 m released full, + a 1 m hit radius: a boss at 6 m is missed, at 5.5 m hit)
    mods, notes = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=1.8, distance=6.0, boss_radius=1.0)
    assert {m["value"] for m in mods} == {0.0} and "out of reach" in notes[0]["value"]
    mods, _ = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=1.8, distance=5.5, boss_radius=1.0)
    assert any(m.get("key") == "PhysicalMin" and m["value"] == 15257 for m in mods)
    mods, _ = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=1.8, distance=6.0, boss_radius=1.0,
                                         at_target=True)  # (Astral Projection: at the target)
    assert any(m.get("key") == "PhysicalMin" and m["value"] == 15257 for m in mods)
    # (the area line already in PoB's radius: not counted twice)
    mods, _ = models.plague_bearer_model(buff, nova, 10_000.0, group=3, radius=4.76, area_in=True, distance=5.5,
                                         boss_radius=1.0)
    assert any(m.get("key") == "PhysicalMin" and m["value"] == 15257 for m in mods)
    _, notes = models.plague_bearer_model(buff, nova, 10_000.0, forbidden={models.PLAGUE_ENTRY})
    assert notes[0]["value"] == "not counted"


def test_vine_plant_counts_half_a_cloud_poison_per_plant_while_a_cloud_covers_it():
    # (R63-2: 50% of ONE cloud Poison's DPS, a plant by the boss, the cloud over it 80% of the fight)
    mods, notes = models.vine_plant_model(-50, 1, 0.25, 8.0, [("Gas Arrow", 1000.0, 0.8)], poison_limit=3)
    assert models.added_total(mods) == pytest.approx(0.5 * 1000 * 1 * 0.8)
    assert notes[0]["entry"] == models.VINE_ENTRY and "+1,200" in notes[0]["basis"]  # (the Poison-limit row: x3)
    # (Q20: -40, 60%; two plants a Limit of 2 kept up by 0.25 presses a second of 8 s)
    mods, _ = models.vine_plant_model(-40, 2, 0.25, 8.0, [("Gas Grenade", 1000.0, 1.5)])
    assert models.added_total(mods) == pytest.approx(0.6 * 1000 * 2 * 1.0)
    # (fewer presses than the Limit allows: plants a second x 8 s)
    mods, _ = models.vine_plant_model(-50, 3, 0.125, 8.0, [("Gas Arrow", 1000.0, 1.0)])
    assert models.added_total(mods) == pytest.approx(0.5 * 1000 * 1.0)


def test_vine_plant_without_a_cloud_or_a_plant_deals_nothing():
    mods, notes = models.vine_plant_model(-50, 1, 0.25, 8.0, [])
    assert mods == [] and notes[0]["value"] == "0" and "no Poison Cloud" in notes[0]["basis"]
    mods, notes = models.vine_plant_model(-50, 1, 0.0, 8.0, [("Gas Arrow", 1000.0, 1.0)])
    assert mods == [] and "no plant" in notes[0]["basis"]
    assert models.vine_plant_model(-50, 1, 0.25, 8.0, [("Gas Arrow", 1000.0, 1.0)], forbidden={models.VINE_ENTRY}) == ([], [])


def test_flame_pillar_is_grisly_pyres_shape_at_25_percent():
    # (an Ignite of 1,000/s lasting 4 s, applied every 2 s: 3 s left on average; 1 use a second finding it 50% of the time)
    pyre, _ = models.grisly_pyre_model(1.0, 0.5, 1000.0, 4.0, 0.5)
    pillar, how = models.grisly_pyre_model(1.0, 0.5, 1000.0, 4.0, 0.5, share=models.FLAME_PILLAR_SHARE,
                                           source="Boneshatter", pillar=models.FLAME_PILLAR)
    assert pyre == pytest.approx(1.0 * 0.5 * 0.30 * 1000 * 3.0)
    assert pillar == pytest.approx(1.0 * 0.5 * 0.25 * 1000 * 3.0) and "1 Flame Pillar of 25%" in how
    _, how = models.grisly_pyre_model(0.0, 0.5, 1000.0, 4.0, 0.5, share=0.25, source="Boneshatter",
                                      pillar=models.FLAME_PILLAR)
    assert "Boneshatter isn't used" in how
