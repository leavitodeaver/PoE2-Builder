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


# Aggravated Bleeding (MECH104; RESEARCH54 R54-1, the manager 13:37)
def _sim(hits, duration, chance, p, crit, k, rolled, n=120000, seed=11):
    """Even hits: each may Bleed (a crit's k x as strong) and be an event Aggravating the Bleeds already live."""
    import random

    rng, life, live, some, top = random.Random(seed), round(hits * duration), [], 0.0, 0.0
    for i in range(n):
        if rng.random() < p:
            for bleed in live:
                bleed[2] = True
        if rng.random() < chance:
            live.append([i + life, k if rng.random() < crit else 1.0, rng.random() < rolled])
        live = [b for b in live if b[0] > i]
        got = [b[1] for b in live if b[2]]
        some, top = some + bool(got), top + max(got, default=0.0)
    return some / n, top / n


@pytest.mark.parametrize("hits, duration, chance, p, crit, k, rolled", [
    (2, 5, 1.0, 0.1, 0.0, 1.0, 0.0), (2, 5, 1.0, 0.2, 0.0, 1.0, 0.0), (1, 10, 1.0, 0.05, 0.0, 1.0, 0.0),
    (2, 5, 0.5, 0.1, 0.0, 1.0, 0.0), (2, 5, 1.0, 0.1, 0.3, 3.0, 0.0), (4, 4, 0.4, 0.1, 0.2, 2.5, 0.0),
    (2, 5, 1.0, 0.0, 0.2, 2.5, 0.2), (2, 5, 1.0, 0.1, 0.2, 2.5, 0.1)])
def test_the_aggravated_share_and_size_match_a_discrete_fight(hits, duration, chance, p, crit, k, rolled):
    some, top = _sim(hits, duration, chance, p, crit, k, rolled)
    still = lambda u: models.aggravate_no_event(u, hits * p, hits)  # noqa: E731
    assert models.aggravated_top(still, duration, hits, chance, rolled) == pytest.approx(some, abs=0.03)
    assert models.aggravated_top(still, duration, hits, chance, rolled, crit, k) == pytest.approx(top, rel=0.05)


def test_one_event_aggravates_the_live_bleeds_together():
    # the manager's case: 10% at 2 hits/s on 5 s Bleeds: the independent form read 97.5%, the fight 61-65%
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "BleedDuration": 5.0, "BleedChancePerHit": 100}
    mods, notes = models.aggravate_model(["10% chance to Aggravate Bleeding on Hit"], stats, {"Attack"})
    assert 60 < mods[0]["scale"] < 63 and "together" in notes[0]["basis"]


def test_the_extra_is_sized_by_the_strongest_aggravated_bleed_with_the_full_size_as_a_row():
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 30, "BleedDuration": 5.0, "BleedChancePerHit": 100,
             "PhysicalHitAverage": 100.0, "PhysicalCritAverage": 300.0}
    mods, notes = models.aggravate_model(["10% chance to Aggravate Bleeding on Hit"], stats, {"Attack"})
    assert mods[0]["scale"] < 61.2  # (below the share of time some Bleed is Aggravated)
    assert "strongest AGGRAVATED" in notes[0]["basis"] and "full size" in notes[0]["basis"]


PERFORATION_LINE = "20% chance for Bleeding to be Aggravated when Inflicted against Enemies on Jagged Ground"
BLEEDER = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "BleedDuration": 5.0, "BleedChancePerHit": 100}


def test_perforation_rolls_per_bleed_at_the_builds_jagged_ground_share():
    assert models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(0.0, "none"))[0] == []
    mods, notes = models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(0.5, "Earthquake"))
    assert mods[0]["scale"] == pytest.approx(100 * (1 - 0.9 ** 10), abs=0.5)  # (10 live Bleeds, each 20% x 50%)
    assert "Perforation" in notes[0]["basis"] and "Earthquake" in notes[0]["basis"]


def test_perforation_is_not_counted_twice_beside_a_live_ledger_line(monkeypatch):
    from builder import ledger

    live = (type("E", (), {"raw": {}})(), ["share:JaggedGround 20% more Magnitude of Bleeding you inflict"])
    monkeypatch.setattr(ledger, "translate", lambda text: live)
    assert models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(1.0, "Earthquake")) == ([], [])


# Kelari's Deception's spaced chain (MECH104; RESEARCH54 R54-3)
@pytest.mark.parametrize("cooldown, mean", [(4.2, 3.65), (4.5, 3.34), (5.0, 2.95), (6.0, 2.25), (8.0, 1.75)])
def test_spacing_uses_inside_the_clones_life_keeps_the_chain(cooldown, mean):
    assert models.deception_chain(cooldown)[0] == pytest.approx(mean, abs=0.02)  # (R54-3's table)
    assert models.deception_factor(cooldown)[0] == pytest.approx(models.deception_chain(cooldown)[0])


def test_a_long_cooldown_falls_back_to_the_three_stored_uses():
    bonus, booms, _ = models.deception_chain(30.0)
    assert bonus == pytest.approx(1.5) and booms == pytest.approx(2 / 3)


def test_deception_shows_the_bunched_and_summoned_readings_and_says_how_to_play_it():
    command, hits = deception(damage=1000.0, cooldown=5.0, rate=2.0)
    hits[2]["AverageDamage"] = 300.0
    dps, _, notes, _ = models.djinn_commands_model([(command, hits)], 0.0)
    seq, _, both = models.deception_chain(5.0)
    assert dps == pytest.approx((1000 * seq + 300 * both) / 5.0)
    assert f"{(1000 + 300 * 2 / 3) * 1.5 / 5.0:,.1f} DPS" in notes[0]["basis"]  # (the bunched row)
    assert f"{(1000 + 300) * 4 / 5.0:,.1f} DPS" in notes[0]["basis"]  # (reading B, while Kelari is summoned)
    assert "UNVERIFIED" in notes[0]["basis"] and "just before each clone expires" in notes[0]["play"]


def test_his_grave_command_still_takes_no_companion_slot():
    assert "His Grave Command" in models.COMPANION_FREE and "Soul Crystal" in models.COMPANION_FREE["His Grave Command"]


# COV54's probe POE2T8010: a "more Magnitude of Bleeding" line multiplies PoB's own Aggravated bonus
@pytest.mark.parametrize("line", ["10% chance to Aggravate Bleeding on Hit",
                                  "Aggravate Bleeding on targets you Critically Hit with Attacks",
                                  "Attack Hits Aggravate any Bleeding on targets which is older than 4 seconds",
                                  PERFORATION_LINE])
def test_a_bleed_pob_already_aggravates_gains_nothing(line):
    assert models.aggravate_model([line], BLEEDER, {"Attack"}, moving=True, jagged=(1.0, "Earthquake"))[0] == []
    always = [line, "Bleeding you inflict is Aggravated"]
    assert models.aggravate_model(always, BLEEDER, {"Attack"}, jagged=(1.0, "Earthquake"))[0] == []


def test_a_cycle_source_adds_nothing_to_a_bleed_already_aggravated():
    cycle = [(models.RUPTURE, 20.0, 2.0, 2.0, False, "stunned 2 s every 20 s")]
    assert models.aggravate_model([], BLEEDER, {"Attack"}, moving=True, cycles=cycle)[0] == []


def test_two_aggravate_sources_join_into_one_share():
    hit, crit = "10% chance to Aggravate Bleeding on Hit", "50% chance to Aggravate Bleeding on targets you Critically Hit with Attacks"
    stats = {**BLEEDER, "CritChance": 20}
    alone = [models.aggravate_model([x], stats, {"Attack"})[0][0]["scale"] / 100 for x in (hit, crit)]
    both = models.aggravate_model([hit, crit], stats, {"Attack"})[0]
    assert len(both) == 1  # (one line, so nothing multiplies)
    assert max(alone) < both[0]["scale"] / 100 < min(1.0, sum(alone)) and both[0]["scale"] / 100 < (1 + alone[0]) * (1 + alone[1]) - 1


def test_perforation_shows_its_own_bleed_reading_beside_the_headline():
    _, notes = models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(1.0, "Earthquake"))
    assert "row 18s" in notes[0]["basis"] and "+20.0%" in notes[0]["basis"]


def test_the_dead_jagged_ground_ledger_entry_does_not_stop_perforation():
    from builder import ledger

    found = ledger.translate(PERFORATION_LINE)
    assert found is None or not any("Magnitude of Bleeding" in t for t in found[1])  # (COV54 dropped its live line)
    assert models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(1.0, "Earthquake"))[0]
