"""MECH101: Archon of Chayula's Withered on Hit (conditions.CHAYULA_WITHER_STAT), at the Archon's share of the fight."""
import pytest
from builder.search import conditions as c, models

BASE = {"Speed": 2.0, "HitChance": 100.0}



def test_the_archons_share_takes_the_later_of_the_glory_refill_and_the_recovery_period():
    instance = models.GLORY_INSTANCE[models.ARCHON_OF_CHAYULA]
    assert instance == 10.0
    # Glory refills in 30 s, past the 20 s recovery: 10 / 40
    assert c.chayula_archon_share(1 / 40, 10.0, 20.0) == pytest.approx(0.25)
    # Glory refills in 5 s: the 20 s recovery period decides (10 / 30); without it (the row) 10 / 15
    assert c.chayula_archon_share(1 / 15, 10.0, 20.0) == pytest.approx(1 / 3)
    assert c.chayula_archon_share(1 / 15, 10.0, 0.0) == pytest.approx(2 / 3)
    # a longer Archon (duration lines) lasts longer; no Archons, nothing
    assert c.chayula_archon_share(1 / 15, 15.0, 20.0) == pytest.approx(15 / 35)
    assert c.chayula_archon_share(0.0, 10.0, 20.0) == 0.0


def test_the_archon_withers_the_boss_only_while_it_is_up():
    # 25% x 2 hits/s x 4 s = 2 stacks during the Archon
    full = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 1.0, 1.0, "test")})
    assert full[0] == 2.0 and "Archon of Chayula" in full[1]
    half = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 0.5, 0.75, "test")})
    assert half[0] == 1.0  # (the time average, 1.0 stacks; PoB's whole count)
    none = c._withered_stacks([], {**BASE, "_chayula_wither": (25.0, 4.0, 0.0, 0.0, "no Archons")})
    assert none[0] == 0.0
    assert c._withered_stacks([], dict(BASE))[0] == 0.0  # (no Archon: as before)


def test_the_archons_stacks_share_the_cap_of_ten_with_the_other_sources():
    lines = ["100% chance to inflict Withered for 4.5 seconds on Hit"]  # 2 hits/s x 4.5 s = 9 stacks
    alone = c._withered_stacks(lines, dict(BASE))
    assert alone[0] == 9.0
    # during the Archon min(10, 9 + 2) = 10, outside it 9: half the fight each, 9.5 on average (floored: 9)
    both = c._withered_stacks(lines, {**BASE, "_chayula_wither": (25.0, 4.0, 0.5, 1.0, "test")})
    assert both[0] == 9.0 and "9.5 stacks kept up" in both[1]
    whole = c._withered_stacks(lines, {**BASE, "_chayula_wither": (25.0, 4.0, 1.0, 1.0, "test")})
    assert whole[0] == 10.0


def test_the_archon_gives_pob_its_can_wither_flag():
    flag = [models.raw_mod("Condition:CanWither", "FLAG", 1)]
    assert c.wither_source_flags([], {"Attack"}, {models.ARCHON_OF_CHAYULA, "Quarterstaff Strike"}) == flag
    assert c.wither_source_flags([], {"Attack"}, {"Quarterstaff Strike"}) == []
    assert c.wither_source_flags([], {"Attack"}) == []

