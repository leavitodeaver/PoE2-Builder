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
