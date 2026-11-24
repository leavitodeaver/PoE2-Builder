"""SEARCHD1's hooks on loan (the manager, 24 Nov 13:48): a deadline cut leaves a "Not finished" row and a finished pass
removes it, in gear.py (_build_rare, fill_utility, fill_sockets, gear_round's resume slot) and through inner.py's
state / load / finalize result. None of them changes a choice: the item keeps what it has either way."""

from __future__ import annotations

import faulthandler

import pytest

from builder import ROOT, clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import unfinished
from builder.search.context import make_payload

CODE = ROOT / "benchmarks" / "reference" / "2b75c.txt"


@pytest.fixture(scope="module")
def engine():
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


@pytest.fixture(scope="module")
def search(engine):
    from builder.search.inner import Search, seed_task

    payload = make_payload(BuildRequest(class_name="Huntress", ascendancies=["Ritualist"], level=90,
                                        seed_code=CODE.read_text().strip(), budget={"gear_tier": "endgame"},
                                        objective="boss"))
    found = Search(engine, None, payload, seed_task(engine, None, payload)["skeleton"], 0)
    found.setup_seed(quests=False)
    return found


def parts(search, step):
    return {r["part"]: r for r in unfinished.rows(search.build) if r["step"] == step}


def test_a_rare_cut_mid_fill_keeps_its_affixes_and_says_so(search, monkeypatch):
    gear = search.gear
    real, calls, late = clock.now, [0], [False]
    monkeypatch.setattr(clock, "now", lambda: float("inf") if late[0] else real())
    deltas = type(gear).deltas

    def counted(self, slot, raws):
        calls[0] += 1
        if calls[0] == 4:  # (bases, each affix alone, the forced start; the first greedy step's what-if: then the clock)
            late[0] = True
        return deltas(self, slot, raws)

    monkeypatch.setattr(type(gear), "deltas", counted)
    rare = gear._build_rare("Gloves", real() + 600)
    row = parts(search, "gear").get("Gloves")
    assert rare is not None and row is not None, (rare, unfinished.rows(search.build))
    assert 1 <= row["done"] < row["of"] and row["unit"] == "affixes"
    late[0], calls[0] = False, -10**9  # (a whole fill: the row goes)
    assert gear._build_rare("Gloves", real() + 600) is not None
    assert "Gloves" not in parts(search, "gear")


def test_rune_sockets_cut_say_so_and_a_finished_fill_clears_it(search):
    gear = search.gear
    slot, option = next((s, o) for s, o in search.build.equipped.items() if o is not None and not s.startswith("Jewel")
                        and not s.startswith("Flask") and not s.startswith("Charm"))
    base = search.ctx.bases_by_name.get(option.base) or {}
    gear.fill_sockets(slot, base, option.raw, [], 0, 2, search.build.score, 0.0, [], lambda n: True, clock.now() - 1)
    assert parts(search, "runes")[slot]["done"] == 0
    gear.fill_sockets(slot, base, option.raw, [], 0, 2, search.build.score, 0.0, [], lambda n: True, clock.now() + 60)
    assert slot not in parts(search, "runes")  # (finished: nothing qualified, which is not a cut)


def test_a_cut_gear_round_names_its_resume_slot_and_a_whole_round_clears_it(search, monkeypatch):
    gear = search.gear
    gear.resume = None
    gear.gear_round(clock.now() - 1, True)
    rows = parts(search, "gear round")
    assert gear.resume is not None and any(p.startswith(gear.resume) for p in rows), (gear.resume, rows)
    gear.resume = None
    monkeypatch.setattr(gear, "slots", {})  # (a whole round with nothing to reach: it finishes)
    gear.gear_round(clock.now() - 1, True)
    assert parts(search, "gear round") == {}


def test_the_rows_travel_in_the_lines_state(search):
    unfinished.note(search, "gear", "Boots", 2, 6, "affixes")
    state = search.state()
    assert any(r["part"] == "Boots" for r in state["unfinished"])
    unfinished.done(search, "gear", "Boots")
    search.load(state)
    assert "Boots" in parts(search, "gear")
    unfinished.done(search, "gear", "Boots")
