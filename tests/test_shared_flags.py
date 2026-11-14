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


def _rows_search(raw: str, kept: bool) -> types.SimpleNamespace:
    penalties = [types.SimpleNamespace(label="Recovery", stat="BossRecoveryPercent"),
                 types.SimpleNamespace(label="Fire", stat="FireResist")]
    gear = types.SimpleNamespace(charges=types.SimpleNamespace(life_effect_kept=kept), flask_info=lambda raws: [{}])
    build = types.SimpleNamespace(objective=types.SimpleNamespace(penalties=penalties),
                                  equipped={"Flask 1": types.SimpleNamespace(raw=raw), "Flask 2": types.SimpleNamespace(raw=raw)})
    return types.SimpleNamespace(gear=gear, build=build)


def test_a_part_time_kept_at_full_flasks_rows_count_its_recovery_once(monkeypatch):
    """MECHC25's double count in weigh_uptimes' rows (SEARCH22): a part-time Life flask with Enduring Elixirs is in PoB's
    LifeRegenRecovery while its slot is on (its whole recovery, 306.7 a second in POE2T1700) and in ours
    (FlaskLifeRecoveryRate, charge-limited, 92) whatever the flag. The rows: with it, PoB's alone; without, none from it;
    time-weighted, ours alone (what the floors read). A floor ours doesn't move keeps the plain weighting."""
    from builder.search import objective

    monkeypatch.setattr(objective, "with_boss_recovery", lambda s: {**s, "BossRecoveryPercent": s["LifeRegenRecovery"]
                                                                    + s["FlaskLifeRecoveryRate"]})
    monkeypatch.setattr(flasks, "recovery_per_second", lambda info, gain, pool, raw="": 92.0)
    on = {"LifeRegenRecovery": 306.7, "FlaskLifeRecoveryRate": 92.0, "BossRecoveryPercent": 398.7, "FireResist": 80}
    off = {"LifeRegenRecovery": 0.0, "FlaskLifeRecoveryRate": 92.0, "BossRecoveryPercent": 92.0, "FireResist": 70}
    deltas = {k: off[k] - on[k] for k in on if off[k] != on[k]}
    var = f"{FLASK_VAR}Flask 1:Elixir"
    rows = inner._uptime_floor_rows(_rows_search("Ultimate Life Flask", kept=True), var, 0.3, on, off, deltas)
    assert rows[0] == ("Recovery", pytest.approx(306.7), pytest.approx(0.0), pytest.approx(92.0))
    assert rows[1] == ("Fire", 80, 70, pytest.approx(0.3 * 80 + 0.7 * 70))
    # (before: with it 398.7, without 92, time-weighted 0.3 x 398.7 + 0.7 x 92 = 184: PoB's whole and ours)
    plain = inner._uptime_floor_rows(_rows_search("Ultimate Life Flask", kept=False), var, 0.3, on, off, deltas)
    assert plain[0] == ("Recovery", 398.7, 92.0, pytest.approx(0.3 * 398.7 + 0.7 * 92.0))
    # Melting Maelstrom's own Mana line: read off the flask's text
    mana_deltas = {"ManaRegenRecovery": 18.5 - 121.8}
    assert inner._pob_counted_flask(_rows_search("Effect is not removed when Unreserved Mana is Filled", kept=False),
                                    f"{FLASK_VAR}Flask 2:Melting Maelstrom", mana_deltas) == (92.0, "FlaskManaRecoveryRate")
    assert inner._pob_counted_flask(_rows_search("Ultimate Mana Flask", kept=True),
                                    f"{FLASK_VAR}Flask 2:Mana", mana_deltas) == (0.0, "")


def test_recovery_stats_hands_each_flask_its_own_text(monkeypatch):
    """MECHC25's item for SEARCH (SEARCH22): Gear.recovery_stats passes the flask's text, so flasks.pob_counts_whole sees
    Melting Maelstrom's own "Effect is not removed when Unreserved Mana is Filled" and ours isn't added to PoB's."""
    from builder.search import gear as gear_module
    from builder.search.gear import Gear

    seen = []
    monkeypatch.setattr(gear_module, "recovery_per_second",
                        lambda info, gain, pool, raw="": seen.append((pool, raw)) or 1.0)
    gear = Gear.__new__(Gear)
    gear.charges = None
    gear.flask_info = lambda raws: [{"raw": raws[0]}]
    gear.build = types.SimpleNamespace(equipped={"Flask 2": types.SimpleNamespace(raw="Melting Maelstrom text")})
    gear.recovery_stats({"Flask 1": {"x": 1}}, {"Flask 1": "Life flask text"})
    assert sorted(seen) == [("Life", "Life flask text"), ("Mana", "Melting Maelstrom text")]
