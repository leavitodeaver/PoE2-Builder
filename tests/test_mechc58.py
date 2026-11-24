"""MECHC58: Charge Regulation's three bonuses at the share of the fight the build holds a charge of each kind (an
OVERCOUNT before: PoB's StatThreshold 1 gave the whole bonus whenever the resolver's count was >= 1, and nothing read the
tick; conditions.REGULATION's note, RESEARCH57 R57-1)."""
import math
from types import SimpleNamespace

import pytest

from builder.search import conditions
from builder.search.assumptions import Assumptions

STATS = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "enemySkillTime": 1.0, "_kills": (0.0, 0.0, "lone boss"),
         "_events": {}, "PowerChargesMax": 3, "PowerChargesDuration": 15}
POWER_ON_HIT = "10% chance to gain a Power Charge on Hit"


def test_without_a_tick_the_share_is_a_gain_within_the_duration():
    """No removal: >= 1 is held exactly while a gain came within the last 15 s (charges_with_refresh's rule)."""
    for gains in (0.02, 0.1, 0.5):
        share, mean = conditions.charge_held_share(gains, 0.0, math.inf, 3, 15)
        assert share == pytest.approx(1 - math.exp(-gains * 15), abs=2e-3)
        assert mean == pytest.approx(conditions.charges_with_refresh(gains, 15, 3), abs=0.02)


def test_the_tick_cuts_the_share_and_fast_gains_keep_it():
    slow, _ = conditions.charge_held_share(1 / 30, 0.0, 10, 3, 15)
    edge, _ = conditions.charge_held_share(0.1, 0.0, 10, 3, 15)
    fast, _ = conditions.charge_held_share(1.0, 0.0, 10, 3, 15)
    assert 0.12 < slow < 0.25  # (R57-1's steady one-a-30-s: 17%; random gains sometimes stack)
    assert slow < edge < fast and fast > 0.99
    assert conditions.charge_held_share(0.0, 0.0, 10, 3, 15) == (0.0, 0.0)
    assert conditions.charge_held_share(0.2, 0.5, 10, 3, 15)[0] < conditions.charge_held_share(0.2, 0.0, 10, 3, 15)[0]


def test_the_steady_rhythm_row_is_r57_1s_form():
    assert conditions.cadence_share(1 / 30, 0.0, 10, 3, 15) == pytest.approx(5 / 30)  # min(15, 0.5 x 10) / 30
    assert conditions.cadence_share(0.1, 0.0, 10, 3, 15) == 1.0  # a gain every tick
    assert conditions.cadence_share(1 / 30, 0.0, 10, 3, 15, extra=2.0) == pytest.approx(15 / 30)  # 3 a gain: the timer


def test_the_tick_joins_the_consumers_and_publishes_each_kinds_share():
    stats = {**STATS, "_consumers": {"Power": {conditions.REGULATION: 0.1}}}
    held, how = conditions._charges_held("Power")([POWER_ON_HIT], stats)
    share, text, cadence, kept = stats["_regulation_up"]["Power"]
    assert 0 < share <= 1 and kept is None and "Charge Regulation takes one every 10 s" in how
    plain = {**STATS}
    before, _ = conditions._charges_held("Power")([POWER_ON_HIT], plain)
    assert held <= before and "_regulation_up" not in plain


def test_a_kept_chance_is_a_row_on_the_tick_only():
    stats = {**STATS, "_consumers": {"Power": {conditions.REGULATION: 0.1}}}
    lines = [POWER_ON_HIT, "Skills have 15% chance to not remove Charges but still count as consuming them"]
    conditions._charges_held("Power")(lines, stats)
    share, _text, _cadence, kept = stats["_regulation_up"]["Power"]
    assert kept is not None and kept >= share


class _Build(SimpleNamespace):
    def call(self, name, **_):
        assert name == "getConfig"
        return {"input": self.config}


def _mods(config, ups, texts=()):
    build = _Build(regulation={"every": 10.0, "Frenzy": 25, "Power": 26, "Endurance": 20}, regulation_up=ups,
                   config=config)
    holder = Assumptions.__new__(Assumptions)
    holder.build = build
    return holder.regulation_mods(list(texts))


def test_each_bonus_is_scaled_to_its_share_from_pobs_reading():
    ups = {"Frenzy": (0.5, "f", 0.5, None), "Power": (0.25, "p", 0.2, None)}
    config = {"useFrenzyCharges": True, "overrideFrenzyCharges": 2, "usePowerCharges": True, "overridePowerCharges": 0.4}
    mods, notes = _mods(config, ups)
    speed = next(m for m in mods if m["name"] == "Speed")
    crit = next(m for m in mods if m["name"] == "CritChance")
    assert speed["type"] == "INC" and speed["value"] == pytest.approx(-12.5)  # PoB gave 25: 25 x (0.5 - 1)
    assert crit["type"] == "MORE" and crit["value"] == pytest.approx(26 * 0.25, abs=0.01)  # PoB gave none (0.4 held)
    assert not any(m["name"] == "Armour" for m in mods)  # no Endurance source, PoB's box off: 0 either way
    assert "Endurance" not in notes[0]["value"] and "0% of the fight" not in notes[0]["value"].split(";")[0]
    assert notes[0]["play"]


def test_a_full_share_beside_pobs_full_reading_adds_nothing_and_buff_effect_scales():
    ups = {kind: (1.0, "", 1.0, None) for kind in ("Frenzy", "Power", "Endurance")}
    config = {f"use{k}Charges": True for k in ("Frenzy", "Power", "Endurance")}
    assert _mods(config, ups)[0] == []
    mods, _ = _mods({}, {"Power": (0.5, "", 0.5, None)}, ["30% increased effect of Buffs on you"])
    crit = next(m for m in mods if m["name"] == "CritChance")
    assert crit["value"] == pytest.approx(26 * 1.3 * 0.5, abs=0.01)


def test_no_charge_regulation_no_mods():
    holder = Assumptions.__new__(Assumptions)
    holder.build = _Build(regulation=None, regulation_up={}, config={})
    assert holder.regulation_mods([]) == ([], [])
