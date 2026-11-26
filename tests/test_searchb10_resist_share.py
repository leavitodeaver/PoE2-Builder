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
        assert calls["swap"] == 100.0  # (the round's end, not the loop's 70)
        assert calls["runes"] == pytest.approx(70.0)
        assert g.resist_swap_rows == []
    else:
        assert "swap" not in calls  # (OFF: no swap round, and the rest as before)
        assert calls["runes"] == 100.0
