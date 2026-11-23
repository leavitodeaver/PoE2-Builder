"""MECH104: a djinn's Commands fit in the djinn's own time (one Command at a time: UNVERIFIED, the lower reading)."""
import pytest

from builder.search import models

BY = {c.name: c for c in models.DJINN_COMMANDS}


def deception(damage=1000.0, cooldown=1.58, rate=1.25):
    return BY["Kelari's Deception"], {1: {"AverageDamage": damage, "Cooldown": cooldown, "CastRate": rate},
                                      2: {"AverageDamage": 0.0, "CastRate": rate}}


def brutality(damage=100.0, cooldown=1.58, rate=1.25):
    return BY["Kelari's Brutality"], {2: {"AverageDamage": damage, "Cooldown": cooldown, "CastRate": rate}}


def test_a_use_takes_the_builds_own_cast_time_and_judgment_its_stages():
    assert models.djinn_use_seconds(*deception(rate=2.0)) == pytest.approx(0.5)
    assert models.djinn_use_seconds(BY["Kelari's Brutality"], {2: {}}) == pytest.approx(0.8)  # (PoB's base castTime)
    judgment = BY["Kelari's Judgment"]
    assert models.djinn_use_seconds(judgment, {1: {"CastRate": 4.0}}) == pytest.approx(models.JUDGMENT_STAGES / 4.0)


def test_commands_that_fit_the_djinns_time_are_not_cut():
    # at twice the base cast speed the two fill ~51%
    rows = [deception(rate=2.5), brutality(rate=2.5)]
    dps, _, notes, presses = models.djinn_commands_model(rows, 0.0)
    assert dps == pytest.approx(1000 * 4 / 1.58 + 100 * models.blades_on_boss(0.3) / 1.58)
    assert not any("UNVERIFIED" in n["basis"] for n in notes)
    assert presses["Kelari's Brutality"][0] == pytest.approx(1 / 1.58)


def test_commands_over_the_djinns_time_give_it_to_the_better_one_with_the_other_reading_as_a_row():
    rows = [brutality(), deception()]  # (0.8 s each every 1.58 s: 101%)
    dps, _, notes, presses = models.djinn_commands_model(rows, 0.0)
    left = 1 - 0.8 / 1.58  # Deception first, Brutality in what is left
    whole = 100 * models.blades_on_boss(0.3) / 1.58
    assert dps == pytest.approx(1000 * 4 / 1.58 + whole * left / (0.8 / 1.58))
    assert dps < 1000 * 4 / 1.58 + whole
    note = next(n for n in notes if n["label"].startswith("Kelari's Brutality"))
    assert "UNVERIFIED" in note["basis"] and f"{whole:,.1f} DPS" in note["basis"]
    assert presses["Kelari's Brutality"][0] < 1 / 1.58  # (the button cap sees the slower use)
    assert presses["Kelari's Deception"][0] == pytest.approx(1 / 1.58)


def test_each_djinn_has_its_own_time():
    fury = (BY["Ruzhan's Fury"], {2: {"AverageDamage": 100.0, "Cooldown": 2.0, "CastRate": 1 / 1.5}})
    rows = [deception(), brutality(rate=2.5), fury]  # (Kelari ~76%, Ruzhan 75%: neither over, 151% together)
    assert not any("UNVERIFIED" in n["basis"] for n in models.djinn_commands_model(rows, 0.0)[2])


def test_a_single_command_longer_than_its_cooldown_runs_at_its_cast_time():
    fury = (BY["Ruzhan's Fury"], {2: {"AverageDamage": 100.0, "Cooldown": 1.0, "CastRate": 0.5}})
    assert models.djinn_commands_model([fury], 0.0)[0] == pytest.approx(2 * 100 / 2.0)


def test_spacing_deception_past_the_clones_life_loses_its_sequence():
    # a 3 s cast on a 3.9 s cooldown beside a better Command: the model must weigh the lost x4, not scale it linearly
    slow = deception(damage=1000.0, cooldown=3.9, rate=1 / 3.0)
    big = brutality(damage=100000.0, cooldown=3.9, rate=1 / 3.0)
    dps, _, notes, _ = models.djinn_commands_model([slow, big], 0.0)
    whole = 100000.0 * models.blades_on_boss(0.3) / 3.9
    # Deception gets the 0.9 s left of each 3.9 s: one use every 13 s, past the clone's 4 s, so x1.5 and not x4
    assert dps == pytest.approx(whole + 1000 * 1.5 / 13.0)
