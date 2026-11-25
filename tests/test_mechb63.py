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
