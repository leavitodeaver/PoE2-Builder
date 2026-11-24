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


def test_a_kept_chance_reaches_the_tick_with_the_strict_reading_a_row():
    """(MECHC59, R58-6: the global kept line on the tick is now the headline; off the tick is the row.)"""
    stats = {**STATS, "_consumers": {"Power": {conditions.REGULATION: 0.1}}}
    lines = [POWER_ON_HIT, "Skills have 15% chance to not remove Charges but still count as consuming them"]
    conditions._charges_held("Power")(lines, stats)
    share, _text, _cadence, strict = stats["_regulation_up"]["Power"]
    assert strict is not None and strict <= share


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


def test_a_consumers_own_chance_not_to_remove_keeps_more_charges():
    """Perpetual Charge's 25% (charge_skip_consume_chance_% on the supported skill; conditions.OWN_KEPT_STATS) and
    Hollow Form's Power-only quality stat: fewer charges removed by that consumer, more held (an UNDERCOUNT before)."""
    lines = [POWER_ON_HIT]  # (0.2 gains/s at 2 hits/s)
    base = {**STATS, "_consumers": {"Power": {"Spark": 0.21}}}
    plain, _ = conditions._charges_held("Power")(lines, {**base})
    kept, how = conditions._charges_held("Power")(lines, {**base, "_own_kept": {"Power": {"Spark": 0.25}}})
    assert kept > plain and "Spark 25%" in how
    flicker = {**STATS, "_consumers": {"Power": {"Flicker Strike": 0.5}}, "_own_kept": {"Power": {"Flicker Strike": 0.9}}}
    _, capped = conditions._charges_held("Power")(lines, flicker)
    assert "Flicker Strike 75%" in capped
    assert set(conditions.OWN_KEPT_STATS["base_power_charge_skip_consume_chance_%"]) == {"Power"}


def test_siphon_elements_fire_and_lightning_remnants_come_from_ailments_on_a_bare_boss(monkeypatch):
    """Its Fire / Lightning chances are flat (not per Power) and need a non-Ignited / non-Shocked target
    (skill_stat_descriptions): the main skill's applications x e^(-applications x duration) (an UNDERCOUNT before: 0)."""
    from builder.search import assumptions
    monkeypatch.setattr(assumptions.models, "buildup_rate", lambda stats, ailment: 0.0)
    stats = {"Speed": 2.0, "HitChance": 100, "IgniteChancePerHit": 25, "IgniteDuration": 4.0, "ShockChancePerHit": 0}
    total, how, per = assumptions.siphon_freezes(stats, {"Fire": 0.07})
    fresh = 0.5 * math.exp(-0.5 * 4.0)
    assert per["Fire"] == pytest.approx(0.07 * fresh) and per["Lightning"] == 0 and per["Cold"] == 0
    assert total == pytest.approx(per["Fire"]) and "without one" in how
    supply, used = assumptions.infusion_supply({"Siphon Elements": {}}, [], set(), (total, how, per))
    assert supply["Fire"] == pytest.approx(per["Fire"]) and not any("aren't counted" in u for u in used)


def test_powered_by_verisium_infusions_come_from_its_ward_paid_uses():
    from builder.search import assumptions

    class B(SimpleNamespace):
        def call(self, name, **_):
            return {"skills": [{"name": assumptions.VERISIUM, "levelStats": {assumptions.VERISIUM_COUNT_STAT: 4}}]}

    rate, how = assumptions.verisium_infusions(B(ward_sustain={assumptions.VERISIUM: (0.2, 0.1, 155)}))
    assert rate == pytest.approx(0.4) and "4 a cast" in how
    assert assumptions.verisium_infusions(B(ward_sustain={}))[0] == 0
    supply, used = assumptions.infusion_supply({assumptions.VERISIUM: {}}, [], set(), verisium=(rate, how))
    assert supply["Any"] == pytest.approx(0.4) and not any("aren't counted" in u for u in used)


def test_a_random_extra_charge_from_the_other_kinds_gains_lands_here_a_third_of_the_time():
    """Charge Profusion II's random charge and the global random-extra line: a third to each kind (two thirds were lost)."""
    frenzy = "10% chance to gain a Frenzy Charge on Hit"
    stats = {**STATS, "FrenzyChargesMax": 3, "FrenzyChargesDuration": 15, "_profusion": {frenzy: (0.30, 0.15)}}
    cross, how = conditions.random_from_others([frenzy], "Power", stats)
    assert cross == pytest.approx(0.2 * 0.15 / 3, rel=0.01) and "Frenzy" in how
    held, basis = conditions._charges_held("Power")([frenzy], {**stats})
    assert held > 0 and "random extra" in basis
    assert conditions.random_from_others([frenzy], "Power", {**STATS})[0] == 0


def test_the_rampage_rage_box_says_its_free_window_as_a_play_row():
    stats = {"MaximumRage": 30, "_main_channel_rage": True, "_main_skill": "Rampage", "_rage_spend": {"Rampage": 5.0},
             "_channel_free": 2.5, "RageRegenRecovery": 6.0, "_main_share": 1.0, "Speed": 1.0, "HitChance": 100,
             "enemySkillTime": 1.0, "_events": {}, "_kills": (0.0, 0.0, "lone boss")}
    _, basis = conditions.rage_held([], stats)
    assert "first 2.5 s costs no Rage" in basis and "recast inside the free 2.5 s: 0.00/s spent" in basis
