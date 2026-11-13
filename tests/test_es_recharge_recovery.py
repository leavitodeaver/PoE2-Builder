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
