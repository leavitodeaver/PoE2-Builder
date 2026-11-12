"""inner._weigh_shared_flags (SEARCH8; MECHC10's flasks.shared_flag_factor): PoB keeps UsingFlask up while any flask
slot is active, so weigh_uptimes' one-slot-off cases never drop a line "during any Flask Effect" with two part-time
flasks; one more recalculation with both off puts the all-off case back. No engine."""

from __future__ import annotations

import types

import pytest

from builder.search import flasks, inner
from builder.search.inner import FLASK_VAR, Search


def _search(monkeypatch, none_dps: float):
    search = Search.__new__(Search)
    search.build = types.SimpleNamespace(objective=types.SimpleNamespace(dps=lambda stats: stats["FullDPS"]))
    calls = []

    def all_off(_search, infos, on_stats):
        calls.append(sorted(infos))
        return {"FullDPS": none_dps - on_stats["FullDPS"]}

    monkeypatch.setattr(inner, "_all_off", all_off)
    return search, calls


def test_two_part_time_flasks_put_the_flags_all_off_case_back(monkeypatch):
    # Flask 1 up 40%, Flask 2 up 50%; either alone off loses 10% (its own lines; the flag stays up through the other),
    # both off lose 40% (the flag's line too)
    a, b = f"{FLASK_VAR}Flask 1:Ruby Flask", f"{FLASK_VAR}Flask 2:Sapphire Flask"
    effects = {a: (0.4, {"label": "Ruby", "value": True, "off": False}),
               b: (0.5, {"label": "Sapphire", "value": True, "off": False}),
               "conditionOnslaught": (0.3, {"label": "Onslaught", "value": True, "off": False})}
    search, calls = _search(monkeypatch, none_dps=600.0)
    search.uptime_cache = {a: (0.9, {}), b: (0.9, {}), "conditionOnslaught": (0.8, {})}
    search.play_notes = {f"uptime:{a}": {"basis": "x"}, f"uptime:{b}": {"basis": "y"}}
    factor = inner._weigh_shared_flags(search, effects, {"FullDPS": 1000.0}, 1000.0)
    assert calls == [sorted([a, b])]  # (one recalculation for the group; Onslaught isn't in it)
    expect, _ = flasks.shared_flag_factor([0.4, 0.5], [0.9, 0.9], 0.6)
    assert factor == pytest.approx(expect)
    # Exact for two independent slots: the expected DPS over the four cases
    truth = 0.4 * 0.5 * 1 + 0.4 * 0.5 * 0.9 + 0.6 * 0.5 * 0.9 + 0.6 * 0.5 * 0.6
    assert (0.4 + 0.6 * 0.9) * (0.5 + 0.5 * 0.9) * factor == pytest.approx(truth)
    assert factor < 1  # (the overcount it removes)
    assert "UsingFlask" in search.play_notes[f"uptime:{a}"]["basis"]


def test_one_part_time_flask_or_a_flask_and_a_charm_need_no_group(monkeypatch):
    effects = {f"{FLASK_VAR}Flask 1:Ruby Flask": (0.4, {"label": "Ruby", "value": True, "off": False}),
               f"{FLASK_VAR}Charm 1:Thawing Charm": (0.3, {"label": "Thawing", "value": True, "off": False})}
    search, calls = _search(monkeypatch, none_dps=600.0)
    search.uptime_cache = {var: (0.9, {}) for var in effects}
    search.play_notes = {}
    assert inner._weigh_shared_flags(search, effects, {"FullDPS": 1000.0}, 1000.0) == 1.0
    assert calls == []


def test_sync_flasks_counts_the_boss_presets_add_kills():
    """gear.sync_flasks reads Charges.of (SEARCH8; MECHC9): The King in the Mists' adds give flasks and charms charges,
    which from_bridge alone left at 0."""
    from builder.search import bosses
    from builder.search.gear import Gear

    class StubBuild:
        ctx = types.SimpleNamespace(request=types.SimpleNamespace(boss="The King in the Mists"))
        equipped, extra_stats = {}, {}

        def call(self, method, **_):
            assert method == "chargeGain"
            return {"active": {}}

        def refresh(self):
            pass

    gear = Gear.__new__(Gear)
    gear.build = StubBuild()
    gear.enforce_restrictions = lambda: None
    gear.recovery_stats = lambda: {}
    gear.sync_flasks()
    expected = bosses.kill_stats("The King in the Mists")[bosses.KILLS_STAT]
    assert expected > 0 and gear.charges.add_kills == pytest.approx(expected)
