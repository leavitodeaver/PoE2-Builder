"""Timeless jewels (TIMELESS1): the game-table data, the expected conquered notable, and the bridge's replacement of
the conquered notables PoB keeps with their original stats (research timeless-jewels-t1.md)."""

from __future__ import annotations

import faulthandler
import json

import pytest

from builder import ROOT
from builder.search import timeless

REFERENCE = ROOT / "benchmarks" / "reference"


# The data (no PoB) ----------------------------------------------------------------------------------------------

def test_the_two_jewels_and_their_notables_come_from_the_game_tables():
    tragedy, hate = timeless.jewel(6), timeless.jewel(7)
    assert (tragedy["name"], tragedy["conqueror"], tragedy["seed"]) == ("Heroic Tragedy", "kalguur", [100, 8000])
    assert (hate["name"], hate["conqueror"], hate["seed"]) == ("Undying Hate", "abyss", [79, 30977])  # not PoB's 100-8000
    assert len(tragedy["notables"]) == 37 and len(hate["notables"]) == 30
    assert tragedy["notable_replacement_weight"] == hate["notable_replacement_weight"] == 100  # always replaced
    # the keystone follows the conqueror (patch 0.2.0: Olroth = Knightly Tenets, Medved = Circular Teachings, Vorana =
    # Black Scythe Training; PoB's conquerorList ids 1-3)
    assert [tragedy["keystones"][i]["name"] for i in "123"] == ["Black Scythe Training", "Circular Teachings",
                                                                 "Knightly Tenets"]
    assert len(hate["keystones"]) == 5
    assert [s["id"] for s in hate["small"]] == ["abyss_small_tribute"]
    assert [s["id"] for s in hate["attribute_additions"]] == ["abyss_attribute_tribute"]
    assert not tragedy["small"] and not tragedy["attribute_additions"]


def test_a_conquered_notable_is_a_known_lottery():
    for version, count, total in ((6, 37, 21250), (7, 29, 2900)):
        odds = timeless.notable_odds(version)
        assert len(odds) == count  # Undying Hate's Buried Animosity has weight 0: never drawn
        assert sum(o.chance for o in odds) == pytest.approx(1.0)
        weights = {n["id"]: n["weight"] for n in timeless.jewel(version)["notables"]}
        assert sum(weights.values()) == total
    scorched = next(o for o in timeless.notable_odds(6) if o.name == "Scorched Earth")
    assert scorched.chance == pytest.approx(1000 / 21250) and scorched.stats == {"fire_damage_+%": 40, "base_intelligence": 15}
    expected = timeless.expected_stats(6)
    assert expected["fire_damage_+%"] == pytest.approx(40 * 1000 / 21250)  # only Scorched Earth carries it
    assert expected["melee_damage_+%"] == pytest.approx(40 * 500 / 21250)


def test_the_data_file_matches_the_exported_game_tables():
    tables = sorted((ROOT / "data").glob("*/tables/AlternatePassiveSkills.json"))
    if not tables:
        pytest.skip("no exported timeless tables in this checkout (python -m builder.data.gamefiles)")
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_timeless", ROOT / "tools" / "build_timeless.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build(tables[-1].parent)  # also checks PoB's legion values against the game's
    saved = timeless.data()
    assert built["jewels"] == saved["jewels"]
    assert built["historic_additions"] == saved["historic_additions"]
    assert built["historic_overrides"] == saved["historic_overrides"]


def test_no_outcome_is_known_without_the_game_showing_it():
    assert timeless.observed() == {} and timeless.known(6, 1589, 3188) is None


# The bridge (real PoB) -------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def load(engine, name: str) -> None:
    from builder.pob import codes

    engine.call("loadXml", xml=codes.decode((REFERENCE / f"{name}.txt").read_text(encoding="utf-8").strip()))


FIELDS = ["FullDPS", "TotalEHP"]


def test_conquered_notables_take_the_expected_outcome_not_their_original_stats(engine):
    load(engine, "2b75c")  # Heroic Tragedy 5630 (Vorana), For the Jugular allocated in its radius
    info = engine.call("setTimeless")
    assert info["loaded"] and info["on"]
    allocated = [n for n in info["nodes"] if n["allocated"]]
    assert [n["id"] for n in allocated] == [20677] and allocated[0]["mode"] == "expected"
    assert allocated[0]["name"] == "Expected conquered notable"
    lines = next(n["lines"] for n in engine.call("conqueredNodes")["nodes"] if n["id"] == 20677)
    assert "seed effects unproven" in lines[0] and lines[1].startswith("Possible: Scorched Earth 4.7%")
    on = engine.call("stats", fields=FIELDS)
    off = engine.call("setTimeless", on=False)
    original = engine.call("stats", fields=FIELDS)
    engine.call("setTimeless", on=True)
    assert engine.call("stats", fields=FIELDS) == on
    # probe POE2T9205: 3,657,437 (For the Jugular's own stats) -> 3,534,840 expected
    assert on["FullDPS"] < original["FullDPS"] * 0.98
    assert not off["on"]


def test_the_best_and_worst_rows_bracket_the_expected_reading(engine):
    load(engine, "2b75c")
    before = engine.call("stats", fields=["FullDPS"])
    rows = engine.call("timelessRange", field="FullDPS")
    assert rows["allWorst"] <= rows["expected"] <= rows["allBest"]
    assert rows["expected"] == pytest.approx(before["FullDPS"])
    assert [r["id"] for r in rows["nodes"]] == [20677]
    assert engine.call("stats", fields=["FullDPS"]) == before  # the what-ifs put every node back


def test_a_build_without_a_timeless_jewel_is_untouched(engine):
    load(engine, "2b75b")
    assert engine.call("setTimeless")["nodes"] == []
