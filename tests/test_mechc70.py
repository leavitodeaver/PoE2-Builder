"""MECHC70: the default (worst-case) boss's charge lapse reads the harshest named preset's off-field phase (AUDIT41
A41-43). Pure: no PoB."""
import pytest

from builder.search import bosses, conditions


def test_the_worst_case_boss_drops_charges_as_the_harshest_preset_does(monkeypatch):
    """A lapse is a harm, so the worst case (no off-field entry of its own) takes the named preset whose off-field phase
    leaves the fewest charges held, labelled, with the boss never leaving the field as its row."""
    assert bosses.of({}).name == bosses.WORST and not bosses.ABSENT.get(bosses.WORST)
    short = {"_fight_on": 30.0}
    held, how = conditions.charge_lapse(short, 3.0, 1.5, 15.0)
    king, _ = conditions.charge_lapse({**short, "_boss": "The King in the Mists"}, 3.0, 1.5, 15.0)
    # a 30 s fight: no Trial of Time yet; the King's 38 s maze (and the Bodach's 45 s) once a fight drop all three
    assert held == pytest.approx(king) == pytest.approx(3.0 - 3.0 * 3.0 / (2 * 1.5) / 30.0)
    assert "harshest preset's" in how and "A41-43" in how and "the boss never leaving: 3.00" in how
    # a long fight: whichever preset leaves the fewest held (every named preset read, the lowest kept)
    long = {"_fight_on": 400.0}
    worst, _ = conditions.charge_lapse(long, 3.0, 1.5, 15.0)
    each = [conditions.charge_lapse({**long, "_boss": n}, 3.0, 1.5, 15.0)[0] for n in bosses.ABSENT]
    assert worst == pytest.approx(min(each)) and worst < 3.0
    # a charge duration longer than every phase: nothing drops, nothing said; no fight length yet: unchanged
    assert conditions.charge_lapse(short, 3.0, 1.5, 60.0) == (3.0, "")
    assert conditions.charge_lapse({}, 3.0, 1.5, 15.0) == (3.0, "")
    # the switch: MECHC69's reading (no lapse for the worst case)
    monkeypatch.setattr(conditions, "WORST_LAPSE", False)
    assert conditions.charge_lapse(short, 3.0, 1.5, 15.0) == (3.0, "")
