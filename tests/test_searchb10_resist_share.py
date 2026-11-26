"""SEARCHB10: POE2_SEARCH_RESIST_SWAP's round gets its own share of a full gear round's time.

Before, resist_swap_round ran after gear_round's slot loop on the round's deadline, and at gate budgets every full round
ran out inside that loop, so the round tried no pair (gates POE2T8421 = 8406 and 8416 = 8415, task by task). Pure: no PoB.
"""
from types import SimpleNamespace

import pytest

from builder.search import gear


def test_split_off_keeps_one_deadline(monkeypatch):
    monkeypatch.delenv(gear.SWITCH_RESIST_SWAP, raising=False)
    assert gear.resist_swap_split(100.0, 200.0) == (200.0, 200.0)
    monkeypatch.setenv(gear.SWITCH_RESIST_SWAP, "0")
    assert gear.resist_swap_split(100.0, 200.0) == (200.0, 200.0)


def test_split_on_leaves_the_share(monkeypatch):
    monkeypatch.setenv(gear.SWITCH_RESIST_SWAP, "1")
    loop, swap = gear.resist_swap_split(100.0, 200.0)
    assert swap == 200.0
    assert loop == pytest.approx(100.0 + (1.0 - gear.RESIST_SWAP_SHARE) * 100.0)
    assert gear.resist_swap_split(300.0, 200.0) == (300.0, 200.0)  # (a spent round: the loop gets nothing)


def fake_gear(calls):
    build = SimpleNamespace(locked_slots=set(), equipped={})
    return SimpleNamespace(
        build=build, slots=["Helmet"], resume=None, rare_tried=set(), _anoint_screen=None, fill_granted=None,
        sync_flasks=lambda: None,
        assumption_trials=lambda d: calls.setdefault("trials", d) and False,
        character_rune_round=lambda d: calls.setdefault("runes", d) and False,
        resist_swap_round=lambda d: calls.setdefault("swap", d) and [] or [],
        update_lambda=lambda: None)


@pytest.mark.parametrize("switch", ["0", "1"])
def test_a_cut_slot_loop_leaves_the_swap_round_its_time(monkeypatch, switch):
    """The round starts at t=0 with 100 s; the slot loop's first check sees t=80 (cut when ON: 80 > 70; not when OFF,
    but the fake slot then reads as cut anyway once t passes 100). ON: the swap round still gets the round's own end."""
    monkeypatch.setenv(gear.SWITCH_RESIST_SWAP, switch)
    monkeypatch.delenv(gear.SWITCH_QUICK_FILL, raising=False)
    monkeypatch.delenv(gear.SWITCH_PURITY, raising=False)
    times = iter([0.0, 0.0, 80.0 if switch == "1" else 120.0] + [120.0] * 20)
    monkeypatch.setattr(gear.clock, "now", lambda: next(times))
    calls = {}
    g = fake_gear(calls)
    assert gear.Gear.gear_round(g, 100.0) is False
    assert g.resume == "Helmet"  # (the loop was cut at its first slot)
    if switch == "1":
        assert calls["swap"] == pytest.approx(150.0)  # (the loop overran to t=120: its share counts from there, tail_ends)
        assert calls["runes"] == pytest.approx(70.0)
        assert g.resist_swap_rows == []
    else:
        assert "swap" not in calls  # (OFF: no swap round, and the rest as before)
        assert calls["runes"] == 100.0


# POE2_SEARCH_ROUND_SHARE (the manager, 26 Nov 01:33): the same share for ALL the round-level work after the slot loop
def test_round_split_off_keeps_one_deadline(monkeypatch):
    monkeypatch.delenv(gear.SWITCH_RESIST_SWAP, raising=False)
    monkeypatch.delenv(gear.SWITCH_ROUND_SHARE, raising=False)
    assert gear.round_split(100.0, 200.0) == (200.0, 200.0, 200.0)
    monkeypatch.setenv(gear.SWITCH_RESIST_SWAP, "1")  # (the swap round's share alone, as before)
    loop, tail, swap = gear.round_split(100.0, 200.0)
    assert loop == tail == pytest.approx(100.0 + (1.0 - gear.RESIST_SWAP_SHARE) * 100.0) and swap == 200.0


def test_round_split_on_gives_all_round_level_work_the_rounds_end(monkeypatch):
    monkeypatch.delenv(gear.SWITCH_RESIST_SWAP, raising=False)
    monkeypatch.setenv(gear.SWITCH_ROUND_SHARE, "1")
    loop, tail, swap = gear.round_split(100.0, 200.0)
    assert loop == pytest.approx(100.0 + (1.0 - gear.ROUND_SHARE) * 100.0) and tail == swap == 200.0


def test_a_cut_slot_loop_leaves_the_round_level_work_its_time(monkeypatch, tmp_path):
    monkeypatch.delenv(gear.SWITCH_RESIST_SWAP, raising=False)
    monkeypatch.delenv(gear.SWITCH_QUICK_FILL, raising=False)
    monkeypatch.delenv(gear.SWITCH_PURITY, raising=False)
    monkeypatch.setenv(gear.SWITCH_ROUND_SHARE, "1")
    log = tmp_path / "rounds.jsonl"
    monkeypatch.setenv(gear.SWITCH_ROUND_LOG, str(log))
    times = iter([0.0, 0.0, 80.0] + [80.0] * 20)
    monkeypatch.setattr(gear.clock, "now", lambda: next(times))
    calls = {}
    g = fake_gear(calls)
    g.fill_granted = lambda d: None
    g.granted_meta_trials = lambda d: calls.setdefault("granted", d) and False
    gear.Gear.gear_round(g, 100.0)
    assert g.resume == "Helmet" and calls["runes"] == pytest.approx(110.0) and calls["granted"] == pytest.approx(110.0)
    import json
    row = json.loads(log.read_text().splitlines()[0])
    assert row["cut"] and row["share"] and row["left"] == pytest.approx(30.0)


def test_tail_ends_survive_a_slot_loop_overrun(monkeypatch):
    """POE2T8446: the slot loop ran up to ~9.5 s past its deadline; the tail's share counts from its own start."""
    monkeypatch.delenv(gear.SWITCH_RESIST_SWAP, raising=False)
    monkeypatch.delenv(gear.SWITCH_ROUND_SHARE, raising=False)
    assert gear.tail_ends(0.0, 10.0, 13.0, 10.0, 10.0) == (10.0, 10.0)  # (off: unchanged, nothing left)
    monkeypatch.setenv(gear.SWITCH_RESIST_SWAP, "1")
    assert gear.tail_ends(0.0, 10.0, 13.0, 7.0, 10.0) == (7.0, pytest.approx(16.0))
    monkeypatch.setenv(gear.SWITCH_ROUND_SHARE, "1")
    assert gear.tail_ends(0.0, 10.0, 13.0, 10.0, 10.0) == (pytest.approx(16.0), pytest.approx(16.0))
    assert gear.tail_ends(0.0, 10.0, 5.0, 10.0, 10.0) == (10.0, 10.0)  # (no overrun: the round's own end stands)
