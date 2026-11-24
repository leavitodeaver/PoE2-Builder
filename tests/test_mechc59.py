"""MECHC59 (pure unit tests): Armour Break II / III's Endurance Charges credited to the skill that completes each Full
Break (conditions.ENDURANCE_SUPPORTS' note); Rampage's recast play when the held channel keeps no Rage
(conditions.CHANNEL_RECAST); Walking Calamity's Glory from Rage gained at maximum Rage (models.GLORY_FROM_RAGE)."""
import pytest

from builder.search import conditions

BREAK_TEXT = next(t for t, k in conditions.ENDURANCE_SUPPORT_TEXT.items() if k == "break")
BASE = {"Speed": 1.5, "HitChance": 100, "CritChance": 10, "enemySkillTime": 1.0, "_full_break_rate": 0.1}


def test_side_skill_armour_break_gains_at_its_share_of_the_breaks():
    """A side group with Armour Break II gains 15% x the Full Breaks x its share of the Armour broken (before: 0)."""
    stats = {**BASE, "_endurance_side_breaks": [(0.15, "Armour Break II", "Rain of Arrows")],
             "_break_others": [("Rain of Arrows", 300.0, "")], "_break_rate_all": 1200.0, "_break_side_scale": 1.0}
    rate, how = conditions._charge_rate(BREAK_TEXT, "Endurance", stats)
    assert rate == pytest.approx(0.15 * 0.1 * 0.25)
    assert "Rain of Arrows" in how and "25%" in how


def test_main_skill_armour_break_takes_only_the_rest_of_the_breaks():
    """The main skill's Armour Break III no longer takes the Full Breaks another group completes (an OVERCOUNT)."""
    stats = {**BASE, "_endurance_supports": {"break": (0.2, "Armour Break III")},
             "_break_others": [("Rain of Arrows", 300.0, "")], "_break_rate_all": 1200.0, "_break_side_scale": 2.0}
    rate, _ = conditions._charge_rate(BREAK_TEXT, "Endurance", stats)
    assert rate == pytest.approx(0.2 * 0.1 * 0.5)  # (the side's 300 x its 2.0 scale = half of 1,200)


def test_main_alone_and_no_split_keep_every_break():
    """With nothing else breaking, or no split (Ferocious Roar's path), the main skill keeps every Full Break."""
    on = {**BASE, "_endurance_supports": {"break": (0.2, "Armour Break III")}}
    assert conditions._charge_rate(BREAK_TEXT, "Endurance", on)[0] == pytest.approx(0.02)
    assert conditions._charge_rate(BREAK_TEXT, "Endurance", {**on, "_break_rate_all": 500.0})[0] == pytest.approx(0.02)
    side = {**BASE, "_endurance_side_breaks": [(0.15, "Armour Break II", "Rain of Arrows")]}
    rate, how = conditions._charge_rate(BREAK_TEXT, "Endurance", side)
    assert rate == 0 and "aren't split out" in how


def test_two_copies_of_the_shared_line_do_not_double_it():
    """II and III share the description: with two copies in the build's lines each carries half of the total."""
    stats = {**BASE, "_endurance_supports": {"break": (0.2, "Armour Break III")},
             "_endurance_side_breaks": [(0.15, "Armour Break II", "Rain of Arrows")],
             "_break_others": [("Rain of Arrows", 300.0, "")], "_break_rate_all": 1200.0,
             "_endurance_text_count": {"break": 2}}
    total = 0.2 * 0.1 * 0.75 + 0.15 * 0.1 * 0.25
    rate, how = conditions._charge_rate(BREAK_TEXT, "Endurance", stats)
    assert 2 * rate == pytest.approx(total)
    assert "held 2 times" in how


def test_nothing_breaks_reads_zero():
    stats = {**BASE, "_full_break_rate": 0.0, "_endurance_side_breaks": [(0.15, "Armour Break II", "Rain of Arrows")]}
    rate, how = conditions._charge_rate(BREAK_TEXT, "Endurance", stats)
    assert rate == 0 and "nothing Fully Breaks" in how


RAMPAGE = {"MaximumRage": 30, "_main_channel_rage": True, "_main_skill": "Rampage", "_rage_spend": {"Rampage": 5.0},
           "_channel_free": 2.5, "_channel_min": 3.65, "_channel_gap": 0.5, "RageRegenRecovery": 3.0,
           "_main_share": 1.0, "Speed": 1.0, "HitChance": 100, "enemySkillTime": 1.0, "_events": {},
           "_kills": (0.0, 0.0, "lone boss")}


def test_recast_cycle_spends_only_past_the_free_window_and_regenerates_in_the_gaps():
    spend, back, how = conditions.recast_cycle(5.0, 2.5, 3.65, 0.5, 3.0)
    assert spend == pytest.approx(5.0 * 1.15 / 4.15)
    assert back == pytest.approx(3.0 * 0.5 / 4.15)
    assert "recast play" in how


def test_a_rage_limited_held_rampage_takes_the_recast_play_as_the_headline():
    """Held, Rampage spends 5 Rage/s with regeneration stopped in the channel: none held (before, the headline). The
    recast play spends 1.39/s against 0.36/s regenerated in the gaps: still short here, so none held, but said; with
    3 Rage/s gained elsewhere it keeps Rage, where the held channel didn't."""
    _, basis = conditions.rage_held([], dict(RAMPAGE))
    assert "headline, recast play" in basis and "row, the channel held" in basis
    fed = {**RAMPAGE, "RageRegenRecovery": 0.0}
    line = "Gain 3 Rage when Hit by an Enemy"
    held_cycle, basis = conditions.rage_held([line], fed)
    held_hold, _ = conditions.rage_held([line], {**fed, "_channel_min": 0.0})
    assert held_hold == 0 and "headline, recast play" in basis
    assert held_cycle >= held_hold


def test_flame_breath_and_a_sustained_channel_keep_the_held_reading():
    rich = {**RAMPAGE, "RageRegenRecovery": 0.0, "_rage_spend": {"Rampage": 0.5},
            "_raging_cry": [("Ferocious Roar", 0.125, 16, "test")]}  # (a Warcry's gain: outside the channel)
    _, basis = conditions.rage_held([], rich)
    assert "headline, recast play" not in basis
    breath = {**RAMPAGE, "_main_skill": "Flame Breath", "_rage_spend": {"Flame Breath": 6.0}}
    _, basis = conditions.rage_held([], breath)
    assert "headline, recast play" not in basis


def test_a_regenerating_rampage_keeps_its_rage_under_the_recast_play():
    """12 Rage/s regenerated, none of it during the channel (R55-8): held, none kept; recast, 1.45/s back in the gaps
    against 1.39/s spent, so the maximum (regeneration never decays)."""
    held, basis = conditions.rage_held([], {**RAMPAGE, "RageRegenRecovery": 12.0})
    assert held == 30 and "row, the channel held: 0.00 gained against 5.00 spent, none held" in basis
    assert conditions.rage_held([], {**RAMPAGE, "RageRegenRecovery": 12.0, "_channel_min": 0.0})[0] == 0


def test_rage_at_max_is_the_surplus_while_rage_sits_at_its_maximum():
    """R58-5: Walking Calamity's Glory is the Rage gained while already at maximum Rage."""
    assert conditions.rage_at_max(30, 30, 2.5, 1.0, [])[:2] == (2.5, 1.0)
    assert conditions.rage_at_max(12, 30, 2.5, 1.0, [])[0] == 0  # (below the maximum)
    assert conditions.rage_at_max(20, 30, 40 / 8, 1 / 8, [])[0] == pytest.approx(10 / 8)  # (a 40 Rage Roar, max 30)
    assert conditions.rage_at_max(30, 30, 2.5, 1.0, ["Lose all Rage on reaching Maximum Rage"])[0] == 0
    assert conditions.rage_at_max(0, 30, 0.0, 0.0, [])[0] == 0


def test_rage_held_reports_the_rage_gained_at_maximum():
    stats = {**RAMPAGE, "_main_channel_rage": False, "_main_skill": "Walking Calamity", "_rage_spend": {},
             "RageRegenRecovery": 3.0}
    held, _ = conditions.rage_held([], stats)
    assert held == 30 and stats["_rage_at_max"][0] == pytest.approx(3.0)


def test_walking_calamity_builds_glory_from_rage_gained_at_maximum():
    """Before: no source, never used. Now one use per (10 s instance + 50 / Glory a second)."""
    from builder.search import models

    plan, how = models.glory_plan(["Walking Calamity"], {"_rage_at_max": (2.5, 1.0, "test"), "Speed": 1.0}, None, [], [])
    assert plan["Walking Calamity"][0] == pytest.approx(1 / (10 + 50 / 2.5)) and "while at maximum Rage" in how
    assert "1 Glory a gain event" in how  # (R58-5's low row: 1.0 Glory/s)
    plan, how = models.glory_plan(["Walking Calamity"], {"Speed": 1.0}, None, [], [])
    assert "Walking Calamity" not in plan and "never" in how


def test_charge_regulation_tick_takes_the_builds_chance_not_to_remove_charges():
    """R58-6 (MEDIUM-LOW): a global "chance to not remove Charges" line reaches the gem's tick, so its interval is
    C / (1 - p) in the headline where the build holds one; the strict reading (the line off the tick) is the row."""
    stats = {}
    count, how = conditions.regulation_held(stats, "Frenzy", 0.1, 0.0, 0.0, 1 / 12.4, 3, 15.0, 0.2)
    share = stats["_regulation_up"]["Frenzy"][0]
    plain = conditions.charge_held_share(0.1, 0.0, 12.4, 3, 15.0, 0.0)[0]
    kept = conditions.charge_held_share(0.1, 0.0, 12.4 / 0.8, 3, 15.0, 0.0)[0]
    assert share == pytest.approx(kept) and share > plain
    assert stats["_regulation_up"]["Frenzy"][3] == pytest.approx(plain)
    assert "every 15.5 s (every 12.4 s, 20% of them kept" in how and "kept off its tick" in how
    stats = {}
    conditions.regulation_held(stats, "Frenzy", 0.1, 0.0, 0.0, 1 / 12.4, 3, 15.0, 0.0)
    assert stats["_regulation_up"]["Frenzy"][0] == pytest.approx(plain) and stats["_regulation_up"]["Frenzy"][3] is None


def test_when_hit_rage_gains_stop_during_a_no_gain_channel():
    """O5 (example build 8): "Cannot gain Rage while using this Skill" blocks gains when Hit during the channel too; held
    all fight (share 1), Rampage gains none from them; the row says what they were."""
    fed = {**RAMPAGE, "RageRegenRecovery": 0.0, "_channel_min": 0.0}
    held, basis = conditions.rage_held(["Gain 3 Rage when Hit by an Enemy"], fed)
    assert held == 0 and "gained when Hit counted only outside the channelled main skill, 100% of the fight" in basis
    half = {**fed, "_main_share": 0.5}
    _, basis = conditions.rage_held(["Gain 3 Rage when Hit by an Enemy"], half)
    assert "0.36 Rage/s gained" in basis  # (0.73 x (1 - 0.5))
    walking = {**fed, "_main_channel_rage": False, "_main_skill": "Walking Calamity", "_rage_spend": {}}
    _, basis = conditions.rage_held(["Gain 3 Rage when Hit by an Enemy"], walking)
    assert "counted only outside the channelled" not in basis  # (not a no-gain channel: unchanged)
