"""MECHC65: the boss's forced moves as a second way it re-enters your Presence (models.presence_reentries,
PRESENCE_FORCED's note); the Acolyte of Chayula's Choice nodes' +50% Remnant pickup range read by the walking model."""
import pytest

from builder.search import assumptions, models

XESHT = "Xesht, We That Are One"


def test_forced_moves_add_presence_entries_for_a_named_boss_only():
    near = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}
    # the worst-case pinnacle takes the real preset with the fewest forced moves: 0 today, so rolls only
    assert models.presence_reentries([], near, 2.0)[0] == pytest.approx(1 / (2 * models.DODGE_EVERY))
    forced = models.presence_forced_moves({**near, "_boss": XESHT})[0]
    assert forced > 0
    rate, how = models.presence_reentries([], {**near, "_boss": XESHT}, 2.0)
    assert rate == pytest.approx(1 / (2 * models.DODGE_EVERY) + forced / 2) and "forced moves" in how
    # a roll that can't leave a 9 m Presence: the forced moves alone bring the boss back in
    wide = {**near, "PresenceRadius": 90, "_boss": XESHT}
    assert models.presence_reentries([], wide, 2.0)[0] == pytest.approx(forced)
    # outside your Presence: never
    assert models.presence_reentries([], {**near, "_boss": XESHT}, 6.0)[0] == 0.0


def test_forced_moves_old_reading_switch(monkeypatch):
    monkeypatch.setattr(models, "PRESENCE_FORCED", False)
    wide = {"PresenceRadius": 90, "MovementSpeedMod": 1.0, "_boss": XESHT}
    assert models.presence_reentries([], wide, 2.0)[0] == 0.0


def test_choice_of_power_pickup_range_is_read_by_the_walking_model():
    # PassiveSkills AscendancyMonk3Notable6 / _2 / _3 (Choice of Power / Mana / Life): remnant_pickup_range_+% 50,
    # PoB's tree text "Remnants can be collected from 50% further away" (data/pob-*/tree.json nodes 58 / 1933 / 4225)
    texts = ["Remnants you create have 50% increased effect", "Remnants can be collected from 50% further away",
             "All Flames of Chayula that you manifest are Purple"]
    pickup, reveal, how = assumptions.remnant_reach(texts, [], "Into the Breach", 1.5)
    assert pickup == pytest.approx(2.25) and reveal == pytest.approx(7.5) and "+50%" in how


def test_relentless_rage_refunds_ten_per_thirty_spent_within_its_uses_and_the_rage_that_comes():
    from builder.search import conditions as c

    base = {"Speed": 2.0, "HitChance": 100, "MaximumRage": 30, "_main_types": {"Attack", "Melee"},
            "_rage_spend": {"Molten Crash": 9.0}}
    gain = ["Gain 3 Rage on Melee Hit"]  # 6 Rage/s against 9 asked
    plain = dict(base)
    c.rage_held(gain, plain)
    assert plain["_rage_spent_real"][0] == pytest.approx(6.0)
    fed = dict(base, _relentless_rage=(0.5, ("Molten Crash",)))
    _, basis = c.rage_held(gain, fed)
    # back = min(10 x 0.5, 9 / 3, 6 / 2) = 3: the spend paid rises from 6 to 9
    assert fed["_rage_spent_real"][0] == pytest.approx(9.0) and "Relentless Rage" in basis
    few = dict(base, _relentless_rage=(0.1, ("Molten Crash",)))  # one supported use every 10 s: 1 Rage/s back
    c.rage_held(gain, few)
    assert few["_rage_spent_real"][0] == pytest.approx(7.0)


def test_perfected_endurance_pays_on_the_supported_perfect_timing_uses_not_every_main_hit():
    from builder.search import conditions as c

    text = ("Supports skills which have a benefit for Perfect Timing. Supported Skills grant at least one Endurance "
            "Charge when Hitting an enemy with Perfect Timing.")
    st = {"Speed": 2.0, "HitChance": 100, "_main_types": {"Attack", "Melee"}}
    assert c._charge_rate(text, "Endurance", st)[0] == 0.0  # (no supported Perfect Timing skill used: before, 2/s)
    fed = dict(st, _perfected_endurance=(0.4, ("Perfect Strike",), 1))
    rate, how = c._charge_rate(text, "Endurance", fed)
    assert rate == pytest.approx(0.4) and "Perfect Strike" in how
    assert c._charge_rate(text, "Endurance", dict(st, _perfected_endurance=(0.4, ("Perfect Strike",), 2)))[0] == \
        pytest.approx(0.2)
    assert c._charge_rate(text, "Frenzy", fed) is None


def test_her_declaration_finds_an_ascendancy_persistent_buff_through_every_known_gem():
    # build 1 (probe POE2T9602): Virtuous Barrier (a Gemling ascendancy gem) and its granted Her Declaration are missing
    # from ctx.gems, so no Persistent Buff was found and the support read 0
    from types import SimpleNamespace

    from builder.search import conditions as c

    ctx = SimpleNamespace(gems={"Clarity II": {"support": True}}, any_gems={
        "Virtuous Barrier": {"support": False, "skillTypes": ["Buff", "Persistent"]},
        "Her Declaration": {"support": True, "skillTypes": []}, "Clarity II": {"support": True}})
    groups = [{"enabled": True, "gems": [{"name": "Virtuous Barrier"}, {"name": "Her Declaration"},
                                         {"name": "Clarity II"}]}]
    stats = {"PresenceRadius": 40, "MovementSpeedMod": 1.0}
    assert c.her_declaration(groups, ctx.gems, [], stats, 2.0) is None  # (the old lookup)
    up, how = c.her_declaration(groups, c.all_gems(ctx), [], stats, 2.0)
    assert up > 0 and "Virtuous Barrier" in how
