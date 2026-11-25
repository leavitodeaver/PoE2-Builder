"""MECHB63's tests: a Warcry's own cast time off the main skill (GEM40's find, an OVERCOUNT)."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import models


def test_warcry_press_model_takes_each_cast_off_the_main_skill():
    mods, notes = models.warcry_press_model(
        [("Infernal Cry", 0.125, "its 8.00 s cooldown", 0.8), ("Seismic Cry", 0.0, "0 presses", 0.8)], "Boneshatter")
    assert len(mods) == 1 and mods[0]["scale"] == pytest.approx(-10.0) and mods[0]["skill"] == "Boneshatter"
    assert "Infernal Cry" in notes[0]["basis"] and "Seismic Cry" not in notes[0]["basis"]
    assert notes[0]["value"] == "x0.900"
    assert models.warcry_press_model([("Infernal Cry", 0.125, "", 0.8)], None) == ([], [])
    assert models.warcry_press_model([], "Boneshatter") == ([], [])
    capped, _ = models.warcry_press_model([("A", 2.0, "", 1.0)], "Boneshatter")
    assert capped[0]["scale"] == pytest.approx(-100 * models.WARCRY_PRESS_MOST)


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


def test_a_warcry_on_its_cooldown_takes_its_cast_time_from_the_main_skill(engine):
    """Infernal Cry beside Boneshatter, not in Full DPS: each use on its cooldown takes its cast time (castRate) from
    Boneshatter; in Full DPS press_model charges it instead, and nothing here."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Infernal Cry 20/0 1")
    group = next(g for g in build.groups() if any(x["name"] == "Infernal Cry" for x in g["gems"]))
    build.set_group(index=group["index"], fullDPS=False)
    raw = [s for s in build.call("activeSkills")["skills"] if not s.get("disabled")]
    found = [models.Skill.of(s) for s in raw]
    main = next(s for s in found if s.name == "Boneshatter")
    row = build.call("useRates", indices=[group["index"]])["rates"][0]
    mods, notes = search.assumptions.warcry_presses(found, main)
    print(row, mods, notes)
    expected = (number := models.number)(row.get("repeats") or 1) / number(row["cooldown"]) / number(row["castRate"])
    assert number(row["cooldown"]) > 0 and number(row["castRate"]) > 0
    assert len(mods) == 1 and mods[0]["skill"] == "Boneshatter"
    assert -mods[0]["scale"] == pytest.approx(100 * expected, abs=1e-3)  # (line() rounds to 3 decimals)
    assert notes[0]["label"].startswith("Time casting Warcries")
    build.set_group(index=group["index"], fullDPS=True)  # (pressed in Full DPS: press_model's)
    assert search.assumptions.warcry_presses(found, main) == ([], [])


def test_bonestorm_one_use_does_its_spike_and_its_explosion():
    """RESEARCH59 R59-4: each spike that hits the boss explodes on it (MEDIUM-LOW): the use's two sets summed."""
    sets = [{"label": "Projectile", "TotalDPS": 1000.0}, {"label": "Explosion", "TotalDPS": 500.0}]
    mods, notes = models.multi_set_model("Bonestorm", "Projectile", sets)
    assert mods[0]["value"] == pytest.approx(50.0) and "R59-4" in notes[0]["basis"]
    assert notes[0]["confidence"].startswith("MEDIUM-LOW")
    mods, _ = models.multi_set_model("Bonestorm", "Explosion", sets)
    assert mods[0]["value"] == pytest.approx(200.0)
    slam = [{"label": "First Slam", "TotalDPS": 1.0}, {"label": "Second Slam", "TotalDPS": 2.0}]
    assert "1.5 m" in models.multi_set_model("Rolling Slam", "Second Slam", slam)[1][0]["basis"]


def test_a_bonestorm_feeder_crits_with_its_explosion_too():
    """R59-4 for Cast on Critical (O4: Energy per Critical Hit): the explosion's own crit roll beside the spike's."""
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    spike = {"HitChance": 100.0, "CritChance": 50.0, "PhysicalCritAverage": 2000.0}
    blast = {"HitChance": 100.0, "CritChance": 20.0, "PhysicalCritAverage": 500.0}
    feeder = {"name": "Bonestorm", "uses": 0.0, "speed": 8.0, "why": "", "final": 0.0, "row": spike}
    alone, crits_alone, _ = models.energy_feed("crit", [feeder], stats)
    both, crits, how = models.energy_feed("crit", [{**feeder, "beside": [blast]}], stats)
    assert crits_alone == pytest.approx(4.0) and crits == pytest.approx(4.0 + 1.6)
    assert both == pytest.approx(alone + 8.0 * 0.2 * 20 * 0.5) and "R59-4" in how


def test_ancestral_cry_is_charged_at_its_glory_when_nothing_else_charges_it(engine):
    """The Glory-paid Ancestral Cry: charged here only when Kaom's embodiment didn't (kaom False) and Glory presses
    charged nothing, at the fewer of its Glory-paid uses and its use rate."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Boneshatter", ascendancies=["Titan"]))
    skeleton = enumerate_skeletons(Context(payload), ["Boneshatter"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    search.setup(quests=False)
    build = search.build
    build.call("addSkillGroup", text="Ancestral Cry 20/0 1")
    group = next(g for g in build.groups() if any(x["name"] == "Ancestral Cry" for x in g["gems"]))
    build.set_group(index=group["index"], fullDPS=False)
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s.get("disabled")]
    main = next(s for s in found if s.name == "Boneshatter")
    a = search.assumptions
    a.glory_caps = lambda: {"Ancestral Cry": (0.05, "test Glory")}
    build.objective.glory_busy = 0.0
    assert a.warcry_presses(found, main, kaom=True) == ([], [])
    mods, notes = a.warcry_presses(found, main, kaom=False)
    print(mods, notes)
    assert len(mods) == 1 and "Ancestral Cry 0.050/s (its Glory: test Glory)" in notes[0]["basis"]
    build.objective.glory_busy = 0.1  # (Glory presses charged something: maybe this Cry, so not again)
    assert a.warcry_presses(found, main, kaom=False) == ([], [])


def test_rampage_pays_its_default_stance_time_once_a_channel():
    """RESEARCH59 R59-5 (MEDIUM-LOW): Rampage's +0.5 s 'if not standing' once a channel (its 3.65 s minimum), not on
    every slam PoB times as a use; every slam paying it is the row."""
    ramp = models.Skill("Rampage", 1, attack=True, melee=True)
    rate = {"speed": 2.0, "speedMod": 1.0, "TotalDPS": 2000.0}  # (0.5 s a slam from standing)
    slam_mods, _ = models.bear_stance_model(ramp, 2.0, rate=rate)
    mods, notes = models.bear_stance_model(ramp, 2.0, rate=rate, channel=3.65)
    assert slam_mods[0]["value"] == pytest.approx(0.5)
    assert mods[0]["value"] == pytest.approx(0.5 * 0.5 / 3.65, abs=1e-5)  # (a slam's share of one 0.5 s a channel)
    assert "R59-5" in notes[0]["basis"] and "every slam pays it" in notes[0]["basis"]
    assert notes[0]["confidence"].startswith("MEDIUM-LOW")
    fs = models.Skill("Furious Slam", 1, attack=True, melee=True)  # (not a channel: unchanged)
    assert models.bear_stance_model(fs, 2.0, rate=rate)[0][0]["value"] == pytest.approx(0.4)

