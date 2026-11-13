"""SEARCH13 (AUDIT3 #15, AUDIT26: an UNDERCOUNT for every high-avoidance Energy Shield build): the recovery floor counts
Energy Shield recharge at the share of the fight it runs between the boss's landed hits (objective.es_recharge)."""
import math

import pytest

from builder.search import boss_hits
from builder.search.objective import RECOVERY_INPUTS, boss_recovery, es_recharge
from builder.search.run import recovery_text

KING = "The King in the Mists"  # (no Fire hits, no Corrupted Blood: recharge waits the delay only)
TAKEN = {f"{t}TakenHit": 1000 for t in ("Physical", "Fire", "Cold", "Lightning", "Chaos")}


def king(evade: float, **extra) -> dict:
    return {"_boss": KING, "enemySkillTime": 0.7, "EvadeChance": evade, "EffectiveBlockChance": 0, **TAKEN,
            "EnergyShieldRechargeDelay": 4.0, **extra}


def test_recharge_counts_at_the_share_of_the_fight_between_landed_hits():
    stats = king(90, EnergyShieldRecharge=2000)
    without = boss_recovery(king(90))
    r = boss_recovery(stats)
    hits = boss_hits.land_share(stats) / 0.7
    share = math.exp(-hits * 4.0)  # (the chance a landed-hit gap outlasts the delay, per second of it)
    assert r["recharging"] == pytest.approx(share)
    assert r["parts"]["Energy Shield recharge"] == pytest.approx(min(2000 * share, r["incoming"]))
    assert r["share"] > without["share"] and "Energy Shield recharge" not in without["parts"]
    assert "recharge runs" in recovery_text(r)
    # a build that evades less gets recharge through less often; one with no recharge gets none
    assert boss_recovery(king(50, EnergyShieldRecharge=2000))["recharging"] < r["recharging"]
    assert es_recharge(king(90), hits)[0] == 0.0
    # it refills only what was lost: never more than the incoming damage
    huge = boss_recovery(king(90, EnergyShieldRecharge=1e9))
    assert huge["parts"]["Energy Shield recharge"] == pytest.approx(huge["incoming"])
    for field in ("EnergyShieldRecharge", "EnergyShieldRechargeDelay"):
        assert field in RECOVERY_INPUTS


def test_damage_over_time_a_hit_leaves_delays_recharge():
    """The Ignites the boss's Fire hits leave (boss_hits.es_dot) hold recharge off for their seconds too."""
    base = {"_boss": "Arbiter of Ash", "enemySkillTime": 0.7, "EvadeChance": 90, "EffectiveBlockChance": 0, **TAKEN,
            "EnergyShieldRecharge": 2000, "EnergyShieldRechargeDelay": 4.0, "AilmentThreshold": 2000}
    hits = boss_hits.land_share(base) / 0.7
    avoided = es_recharge({**base, "IgniteAvoidChance": 100}, hits)[1]
    assert avoided == pytest.approx(math.exp(-hits * 4.0))
    assert es_recharge(base, hits)[1] < avoided


def test_the_builds_own_damage_stops_or_restarts_recharge():
    """SEARCH15 (AUDIT28 A28-2, an OVERCOUNT): "Further loss of Energy Shield resets this delay": the build's own steady
    damage over time on Energy Shield stops recharge; Heartbound Loop's damage on each minion death restarts it as one
    more Poisson stream beside the boss's landed hits."""
    from builder.search.objective import own_loss, recharge_share

    clean = king(90, EnergyShieldRecharge=2000, EnergyShield=5000)
    hits = boss_hits.land_share(clean) / 0.7
    base = boss_recovery(clean)["recharging"]
    # a steady Chaos degen Energy Shield takes stops it; one that bypasses Energy Shield (Bleed, Poison) doesn't
    burning = boss_recovery({**clean, "ChaosBuildDegen": 50, "ChaosEnergyShieldBypass": 0})
    assert burning["recharging"] == 0.0 and "Energy Shield recharge" not in burning["parts"]
    assert burning["recharge_stopped"] == ["Energy Shield"] and "never starts" in recovery_text(burning)
    bypassed = boss_recovery({**clean, "ChaosBuildDegen": 50, "ChaosEnergyShieldBypass": 100})
    assert bypassed["recharging"] == pytest.approx(base) and not bypassed["recharge_stopped"]
    # Heartbound Loop: 300 per death at 0.5 deaths a second reaches PoB as 150 Physical degen, counted as events
    heart = {**clean, "PhysicalBuildDegen": 150, "PhysicalTakenDotMult": 1, "SelfDamagePerSecond": 150,
             "SelfDamageEvents": 0.5}
    assert own_loss(heart)["EnergyShield"] == pytest.approx(0.0)
    total = hits + 0.5
    expected = (hits + 0.5) * math.exp(-total * 4.0) / total
    assert boss_recovery(heart)["recharging"] == pytest.approx(expected)
    assert expected < base
    assert recharge_share(heart, 0.0, 4.0) == pytest.approx(math.exp(-0.5 * 4.0))
    # a steady degen beside the Heartbound damage still stops it
    assert boss_recovery({**heart, "PhysicalBuildDegen": 200})["recharging"] == 0.0


def test_life_recharge_counts_like_energy_shield_recharge():
    """SEARCH15 (AUDIT28 A28-3, an UNDERCOUNT): PoB's LifeRecharge (Eternal Youth; Tetzlapokal's Desire's "Life
    Recharges" through the bridge's setLifeRecharge) counts in the recovery floor at the same between-hits share, and the
    two recharges together refill at most the incoming damage."""
    stats = king(90, LifeRecharge=500, LifeRechargeDelay=4.0, Life=4000)
    r = boss_recovery(stats)
    hits = boss_hits.land_share(stats) / 0.7
    assert r["life_recharging"] == pytest.approx(math.exp(-hits * 4.0))
    assert r["parts"]["Life recharge"] == pytest.approx(min(500 * r["life_recharging"], r["incoming"]))
    assert "Life recharge runs" in recovery_text(r)
    both = boss_recovery({**stats, "EnergyShieldRecharge": 1e9, "LifeRecharge": 1e9, "EnergyShield": 100})
    assert both["parts"]["Energy Shield recharge"] + both["parts"]["Life recharge"] == pytest.approx(both["incoming"])
    # a steady Life loss of its own (no Energy Shield: all of it is Life's) stops it
    assert boss_recovery({**stats, "FireBuildDegen": 20})["life_recharging"] == 0.0
    for field in ("LifeRecharge", "LifeRechargeDelay", "ChaosBuildDegen", "ChaosEnergyShieldBypass"):
        assert field in RECOVERY_INPUTS
