"""MECHB67's model checks (MECH-B)."""
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


def _search(engine, main, **extra):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=main, **extra))
    skeleton = enumerate_skeletons(Context(payload), [main])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_ice_shards_headline_stays_x1_and_rows_read_movement_and_the_limit():
    """R61-11: a standing boss sets each shard off at arming (no bonus); the boss's movement and the 16-shard limit
    are rows only."""
    shards = models.Skill("Ice Shards", 1, attack=True)
    note = models.ice_shards_note(shards, 12.0, (0.225, "dashes"))[0]
    assert note["value"] == "x1.000" and "t 2 s x5.0 (3.5 s after landing)" in note["basis"]
    assert "the boss moving (dashes)" in note["basis"] and "x0.889" in note["basis"]
    quiet = models.ice_shards_note(shards, 5.0, (0.0, "none"))[0]["basis"]
    assert "boss moving" not in quiet and "shard limit" not in quiet


def test_bell_ailments_count_from_the_next_hit_and_the_applying_hit_is_a_row():
    """R63-3: each type on the Bell adds its extra from the next Hit after it lands; a sure Shock on a Bell's 4
    shockwaves is on 3 of them (the applying Hit's own a row: all 4)."""
    ailments = {"threshold": 7186, "chance": {"Shock": 100.0, "Ignite": 0.0, "Chill": 50.0},
                "lasts": {"Shock": 4.0, "Chill": 2.0}, "extra": {"Lightning": 10.0, "Cold": 10.0, "Fire": 10.0}}
    head, row, words = models.bell_ailment_bonus(ailments, 4.0, 0.5)
    chill = (0 + 0.5 + 0.75 + 0.875) / 4
    assert head == pytest.approx(10 * 3 / 4 + 10 * chill)
    assert row > head and "Lightning (Shock 100%" in words and "Fire" not in words
    assert models.bell_ailment_bonus(None, 4.0, 0.5) == (0.0, 0.0, "")


def test_crash_lightning_counts_three_pulses_and_its_final_strike():
    """GEM44 / the manager 20:06: a Crash Lightning trigger is 3 pulses then a Final Strike, written in the calculated
    set's units; with its sets unread, one hit as before."""
    assert models.crash_lightning_hits(100.0, 300.0, "Crash Lightning")[:2] == (pytest.approx(6.0), 3.0)
    assert models.crash_lightning_hits(100.0, 300.0, "Final Strike")[0] == pytest.approx(2.0)
    assert models.crash_lightning_hits(0.0, 300.0, "Final Strike")[0] == 1.0
    mods, notes = models.apocalypse_model(1 / 24, "test", crash=(100.0, 300.0, "Crash Lightning"))
    rates = {m["skill"]: m["value"] for m in mods if m["key"] == "triggerRate"}
    assert rates["Crash Lightning"] == pytest.approx(6 * 0.5 / 0.75 / 3, rel=1e-4)
    assert "3 pulses of 100 and a Final Strike of 300" in notes[0]["basis"]


def test_the_bridge_rates_ailments_against_a_given_threshold_and_a_set_with_gained_damage(engine):
    """MECHB67 (R63-3): useRates' ailmentThreshold rates a skill's Shock against that threshold (a lower one, a higher
    chance); setRates' gainAs re-scores a set with N% of Physical as extra of each named type."""
    build = _search(engine, "Spark", ascendancies=["Stormweaver"]).build
    group = build.main_group()["index"]
    boss = build.call("useRates", indices=[group])["rates"][0]
    low = build.call("useRates", indices=[group], ailmentThreshold=500)["rates"][0]
    assert "ailmentChances" not in boss
    assert low["ailmentChances"]["Shock"] > 0 and low["ailmentChances"]["ShockDuration"] > 0
    got = build.call("setRates", index=group, gainAs={"label": "", "percent": 30, "types": ["Fire", "Cold"]})["sets"]
    assert all("gainAs" not in x or x["label"] == "" for x in got)  # (only the named label)
    first = got[0]
    named = build.call("setRates", index=group, gainAs={"label": first["label"], "percent": 30,
                                                          "types": ["Fire", "Cold"]})["sets"][0]
    assert set(named["gainAs"]) == {"Fire", "Cold"} and named["gainAs"]["Fire"] >= named["AverageDamage"] - 1e-6


def test_apocalypses_crash_lightning_reads_its_pulses_and_final_strike_from_pob(engine):
    """MECHB67: the Apocalypse note names Crash Lightning's two PoB sets (3 pulses and the Final Strike)."""
    from builder.request.schema import Choices

    search = _search(engine, "Volcano", ascendancies=["Shaman"], require=Choices(skills=["Apocalypse"]))
    build = search.build
    build.call("allocate", add=[28745], fields=[])  # Bringer of the Apocalypse: "Grants Skill: Apocalypse"
    search.assumptions.apply()
    note = next(a for a in search.assumptions.active if a.get("label") == "Apocalypse's disasters per second")
    assert "3 pulses of" in note["basis"] and "Final Strike of" in note["basis"]
