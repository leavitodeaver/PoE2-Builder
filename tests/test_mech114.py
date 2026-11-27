"""MECH114: the boss walks out of the grounds made on it (MECHD9's reader rule, conditions.ground_stay)."""
import math

import pytest

from builder.search import bosses, conditions

STILL = {"_boss": "Pinnacle (worst case)"}


def test_ground_stay_is_the_reader_rule():
    # one ground per 10 s lasting 6 s: the window is its 6 s, the factor stay_share(R, 6)
    got, row, how = conditions.ground_stay(STILL, 2.0, 6.0, 0.1)
    assert got == pytest.approx(bosses.stay_share(STILL, 2.0, 6.0)[0])
    assert row == pytest.approx(bosses.stay_share(STILL, 2.0, 6.0, moves="fixed")[0])
    assert 0 < row < got < 1 and "inside" in how
    # recast every 1 s on the boss: the window is the recast interval
    assert conditions.ground_stay(STILL, 2.0, 6.0, 1.0)[0] == pytest.approx(bosses.stay_share(STILL, 2.0, 1.0)[0])
    # Frozen the first 2 s of a 6 s window: still those 2 s, then stay_share over the 4 s left
    held = conditions.ground_stay(STILL, 2.0, 6.0, 0.1, still=2.0)[0]
    assert held == pytest.approx((2.0 + 4.0 * bosses.stay_share(STILL, 2.0, 4.0)[0]) / 6.0)
    assert conditions.ground_stay(STILL, 2.0, 6.0, 0.1, still=9.0)[0] == 1.0
    # no duration / radius: unchanged
    assert conditions.ground_stay(STILL, 0.0, 6.0, 0.1)[0] == 1.0


def test_ground_stay_matches_the_exact_evenly_spaced_average():
    # grounds every c s, each made where the boss stands; the boss leaves the newest one at k a second: inside
    # (1 / c) * integral_0^min(T, c) e^(-k t) dt of the fight = _covered x ground_stay
    rate, metres, _ = bosses.relocations(STILL, gain=False)
    r = bosses.hit_radius(STILL)[0]
    k = rate * math.exp(-(2.0 + r) / metres)
    for c, t in ((0.5, 6.0), (3.0, 6.0), (10.0, 6.0)):
        exact = (1 - math.exp(-k * min(t, c))) / k / c
        assert conditions._covered(1 / c, 1.0, t) * conditions.ground_stay(STILL, 2.0, t, 1 / c)[0] == pytest.approx(exact)


def test_shocked_ground_walks_out_with_rows():
    stats = {**STILL, "Speed": 1.0, "HitChance": 100, "_sg_support": 4000, "CritChance": 100}
    share, how = conditions.shocked_ground(stats, [])
    still = conditions._covered(1.0, 1.0, 4.0)  # a ground every 1 s, 4 s each: always up standing still
    assert still == 1.0
    assert share == pytest.approx(conditions.ground_stay(stats, conditions.SHOCKED_GROUND_RADII[
        conditions.SHOCKED_GROUND_SUPPORT], 4.0, 1.0)[0])
    assert share < 1.0 and "Rows: the boss standing still in it 100%" in how


def test_jagged_and_oil_rows():
    share, how = conditions.jagged_ground("Furious Slam", {"Slam"}, set(), {**STILL, "Speed": 0.5,
                                                                          "AreaOfEffectRadius": 20}, rage=30)
    assert 0 < share < 1 and "standing still in it 100%" in how
    found, how = conditions.oil_ground({"Oil Grenade": (0.25, 1000.0, "")}, [("a Detonator", 1.0)], 0.0, None, STILL)
    still, _ = conditions.oil_ground({"Oil Grenade": (0.25, 1000.0, "")}, [("a Detonator", 1.0)], 0.0)
    assert found[0][1] < still[0][1] and "walks out" in how


# A40-1: a side Crushing Blow skill (Seismic Cry) Heavy Stuns the Primed boss at its own uses
STUN = {"HeavyStunBuildupAvg": 10.0, "Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000, "CritChance": 0,
        "_main_share": None, "_crushing": (False, "")}


def test_side_crushing_blow_primes_the_refill():
    from builder.search import models
    plain, _ = models.heavy_stun_rate(STUN)
    side, how = models.heavy_stun_rate({**STUN, "_primed_stun": (0.25, "Seismic Cry 0.250 uses/s")})
    assert side > plain and "Seismic Cry 0.250 uses/s: Heavy Stunned at the first use" in how
    d = models.bosses.of(STUN).heavy_stun_s
    refill = 1 / plain - d
    assert 1 / side - d == pytest.approx(models.primed_refill(refill, 0.25, models.PRIMED_UNIQUE))
    # a main skill already landing Crushing Blows: the side skill adds nothing
    crushing = {**STUN, "_crushing": (True, "Boneshatter")}
    assert models.heavy_stun_rate({**crushing, "_primed_stun": (0.25, "x")})[0] == models.heavy_stun_rate(crushing)[0]


def test_stunned_or_primed_share():
    from builder.search import models
    d = models.bosses.of(STUN).heavy_stun_s
    every = 1 / models.heavy_stun_rate(STUN)[0]
    share, _ = models.stunned_or_primed_share(STUN)
    assert share == pytest.approx((d + 0.3 * (every - d)) / every)
    assert share > models.heavy_stunned_share(STUN)[0]
    assert models.stunned_or_primed_share({**STUN, "HeavyStunBuildupAvg": 0})[0] == 0.0


def test_zone_curse_walks_out():
    from builder.search import models
    use = models.CurseUse("Despair", 1.0, "cast by hand", True, 7.4)
    got = models.curse_zone_walk(use, "Doedre's Undoing", {**STILL, "_curse_radii": {"Despair": 31}})
    assert got.zone == "Doedre's Undoing"
    # the casts' share stays (Doom Blast's expiries read it); the curse on the boss is share x stay
    assert got.share == 1.0 and got.on_boss < 1.0
    assert got.on_boss == pytest.approx(bosses.stay_share(STILL, 3.1, 7.4)[0])
    assert models.curse_share([got])[0] == pytest.approx(got.on_boss)
    assert "Rows: the boss standing still in it 100%" in got.how
    # no radius read: CURSE_ZONE_RADIUS
    assert models.curse_zone_walk(use, "Cursed Ground", STILL).on_boss == pytest.approx(got.on_boss)


def test_deep_venom_adds_the_poison_pob_leaves_out():
    from builder.search import models
    text = "Targets can be affected by +1 of your Poisons at the same time if you have at least 100 Tribute"
    mods, notes = models.deep_venom_model([text], 120.0)
    assert mods and mods[0]["line"] == "Targets can be affected by +1 of your Poisons at the same time"
    assert notes[0]["value"] == 1
    mods, notes = models.deep_venom_model([text], 40.0)
    assert not mods and notes[0]["value"] == 0
