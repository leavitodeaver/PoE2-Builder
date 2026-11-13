"""MECHC12: the worst-case default pinnacle (bosses.WORST) in boss_hits' tables, not the Arbiter of Ash's row."""

from __future__ import annotations

import pytest

from builder.search import boss_hits, bosses


def test_worst_case_reads_its_own_hits_red_flash_ignite_and_corrupted_blood():
    worst = bosses.WORST
    counts = boss_hits.hit_table(worst)[1]
    assert counts == bosses.WORST_HIT_COUNTS and all(counts[t] > 0 for t in boss_hits.TYPES)
    assert boss_hits.hit_sizes(worst) == pytest.approx(bosses.worst_hit_sizes())
    assert boss_hits.hit_sizes(worst, "harsh") == pytest.approx(bosses.worst_hit_sizes("harsh"))
    shares = boss_hits.type_shares(worst)
    assert sum(shares.values()) == pytest.approx(1.0) and shares["Cold"] > 0 and shares["Chaos"] > 0
    # red flash: the worst preset's shares, and the note names where they come from
    stats = {"_boss": worst}
    assert boss_hits.shares(stats) == pytest.approx((bosses.PRESETS[worst].unavoidable, bosses.PRESETS[worst].unblockable))
    assert "highest share of any real preset" in boss_hits.note(stats)["basis"]
    # Ignite: the most increased chance per Fire hit of any real preset (the Fell Serpent's 80 over 4 Fire skills)
    chance, how = boss_hits.ignite_chance({**stats, "AilmentThreshold": 1000, "FireTakenHit": 1000})
    assert "+20.0% increased" in how and chance == pytest.approx(0.2 * 1.2)
    # Corrupted Blood: the real preset with the largest share (one of the Fell Serpent's 11 damaging skills, 3 s)
    assert boss_hits.corrupted_blood(worst) == pytest.approx((1 / 11, 3.0))
    assert ("Corrupted Blood" in [w for _, _, w in boss_hits.es_dot({**stats, "IgniteAvoidChance": 100})])
    # a real preset is unchanged
    assert boss_hits.hit_table("Arbiter of Ash")[1] == {"Fire": 17, "Physical": 7}
    assert boss_hits.corrupted_blood("Arbiter of Ash") is None
