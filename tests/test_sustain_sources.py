"""Every recovery source counts in skill-cost sustain, not regeneration alone (the user, 12 Nov 2026 ~18:20: "regen is
not the only source of recovery, not by a longshot ... that applies for health and mana too"; SEARCH9)."""
import pytest

from builder.search import boss_hits
from builder.search.objective import (RECOVERY_INPUTS, SUSTAIN_FIELDS, SUSTAIN_LEFT_OUT, benefit_every, boss_recovery,
                                      boss_taken, hit_recovery_rates, sustain)
from builder.search.run import sustain_only_note

BOSS = {"enemySkillTime": 1.0, "PhysicalTakenHit": 1000.0, "FireTakenHit": 500.0, "_boss": "Arbiter of Ash"}


def _taken(stats):
    taken, _, _ = boss_taken(stats)  # (Recoup reads the defence pace, the hits whose damage it takes back: A25-60)
    return sum(taken.values()), taken


def test_sustain_counts_recoup_of_the_bosss_hits_typed_and_untyped():
    stats = {**BOSS, "ManaPerSecondCost": 1000.0, "ManaRegenRecovery": 50.0, "ManaRecoup": 10.0,
             "FireManaRecoup": 20.0}
    total, taken = _taken(stats)
    assert total > 0
    back = total * 0.10 + taken["Fire"] * 0.20
    assert hit_recovery_rates(stats)["Mana"] == pytest.approx(back)
    assert sustain(stats)[0] == pytest.approx((50.0 + back) / 1000.0)
    # without the recoup lines, regeneration alone
    assert sustain({**BOSS, "ManaPerSecondCost": 1000.0, "ManaRegenRecovery": 50.0})[0] == pytest.approx(0.05)


def test_recovery_reads_pobs_untyped_recoup_an_undercount_before():
    """PoB's "N% of Damage taken Recouped as Life" is output LifeRecoup (CalcDefence.lua 1857), no damage type in its
    name: boss_recovery read only the typed {Type}LifeRecoup."""
    plain = boss_recovery(BOSS)
    r = boss_recovery({**BOSS, "LifeRecoup": 10.0})
    assert plain["parts"]["Life"] == 0
    assert r["parts"]["Life"] == pytest.approx(r["incoming"] * 0.10)
    assert "LifeRecoup" in RECOVERY_INPUTS and "ManaRecoup" in RECOVERY_INPUTS


def test_gains_on_block_count_at_the_bosss_blocked_hits():
    stats = {**BOSS, "LifePerSecondCost": 400.0, "LifeOnBlock": 100.0, "EffectiveBlockChance": 50.0}
    every = benefit_every(stats)
    per_second = 100.0 * boss_hits.block_share(stats) / every
    assert 0 < per_second < 100.0  # (red-flash hits can't be Blocked)
    assert hit_recovery_rates(stats)["Life"] == pytest.approx(per_second)
    assert sustain(stats)[0] == pytest.approx(per_second / 400.0)
    assert boss_recovery(stats)["parts"]["Life"] == pytest.approx(0.0)  # (all of it pays the cost)
    assert boss_recovery({**stats, "LifePerSecondCost": 0})["parts"]["Life"] > 0
    assert {"LifeOnBlock", "ManaOnBlock", "LifeRecoup"} <= set(SUSTAIN_FIELDS)


def test_a_build_short_only_on_sustain_says_what_the_share_leaves_out():
    note = sustain_only_note(["Skill costs sustained"])
    assert note and SUSTAIN_LEFT_OUT in note and "on kill" in note
    assert sustain_only_note(["Skill costs sustained", "Life + Energy shield"]) is None
    assert sustain_only_note([]) is None


def test_a_what_if_sees_the_main_skills_cost_its_pool_cant_pay():
    """PoB lists unpayable costs only in its MAIN-mode output (Calcs.lua 533); what-ifs run in CALCULATOR mode. The main
    skill's own cost against its pool is in both (MECHB12's 2b75b: the Transmutation Explosion's Runic Ward cost)."""
    from builder.search.objective import cost_short

    assert cost_short({"WardCost": 16.0, "Ward": 0.0}, "WardCostWarningList") == 1.0
    assert cost_short({"WardCost": 16.0, "Ward": 300.0}, "WardCostWarningList") == 0.0
    assert cost_short({"WardCost": 16.0, "Ward": 300.0, "WardCostWarningList": 2}, "WardCostWarningList") == 2.0
    assert cost_short({"ManaCost": 50.0, "ManaUnreserved": 20.0}, "ManaCostWarningList") == 1.0
    assert cost_short({"ManaCost": 50.0, "ManaUnreserved": 20.0, "EnergyShield": 100.0,
                       "EnergyShieldProtectsMana": True}, "ManaCostWarningList") == 0.0
    # anything reserving Mana: PoB's raw-cost pass, so only its own list counts
    assert cost_short({"ManaCost": 50.0, "ManaUnreserved": 20.0, "ManaReservedPercent": 30.0}, "ManaCostWarningList") == 0.0


def test_a_what_if_never_inherits_the_committed_builds_cost_warnings():
    """fill_from_base copied WardCostWarningList into every what-if, so one that paid the cost still showed it."""
    from types import SimpleNamespace

    from builder.search.state import Build

    seen = []
    stub = SimpleNamespace(evals=0, extra_stats={}, boss_stats={}, tick=lambda: None,
                           stats={"WardCostWarningList": 1, "Life": 100.0},
                           objective=SimpleNamespace(prepare=lambda st: st, score=lambda st: seen.append(dict(st)) or 0.0))
    Build._scored(stub, [{"WardCost": 16.0, "Ward": 300.0}], fill_from_base=True)
    assert "WardCostWarningList" not in seen[0] and seen[0]["Life"] == 100.0


def test_recoup_reads_the_same_hits_as_the_damage_it_takes_back():
    """AUDIT25 A25-60: Recoup at the slower benefit pace recovered less than its line promises against the damage
    counted at the defence pace. 10% Life Recoup now returns 10% of what boss_recovery counts as taken."""
    stats = {**BOSS, "LifeRecoup": 10.0, "LifePerSecondCost": 1.0}
    assert benefit_every(stats) > stats["enemySkillTime"]  # (the two paces differ for this boss)
    assert hit_recovery_rates(stats)["Life"] == pytest.approx(boss_recovery({**stats, "LifePerSecondCost": 0})["incoming"] * 0.10)
