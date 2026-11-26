"""GEM35: every grenade of a throw (R46-12), Cluster Grenade's primaries, Run and Gun on grenades, the Infusion gate's
0 rows (R46-10 / R46-11), Verglas (R47-3)."""
from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill


def test_grenade_laterals_spread_over_a_total_arc():
    assert models.grenade_laterals(1, 30) == [0.0]
    three = models.grenade_laterals(3, 30)
    assert three[1] == pytest.approx(0.0) and three[2] == pytest.approx(4.5 * 0.2588, abs=0.01)  # (L sin 15 deg)
    assert max(models.grenade_laterals(7, 30)) == pytest.approx(three[2])  # (fixed: the arc doesn't grow)
    assert max(models.grenade_laterals(2, 50)) == pytest.approx(4.5 * 0.4226, abs=0.01)  # (Cluster's 50)


def test_grenade_hits_every_burst_on_the_boss():
    reach = 1.5 + 0.3  # (the basic grenade's 1.5 m on a 0.3 m boss: the outer one 1.16 m across, +-0.675 m along)
    for n in (1, 2, 3, 5):
        assert models.grenade_hits(n, 30, reach) == pytest.approx(n)
    assert models.grenade_hits(2.5, 30, reach) == pytest.approx(2.5)  # (a fraction mixes the whole counts)
    assert models.grenade_hits(3, 30, 1.0) < 3  # (a blast too small for the outer grenades)
    assert models.grenade_hits(3, 30, reach, offset=2.0) < 3  # (the throw landing short)


def test_grenade_count_model():
    eg = Skill("Explosive Grenade", 1, attack=True)
    sets = [{"label": "Explosive Grenade", "TotalDPS": 100.0, "ProjectileCount": 3, "AreaOfEffectRadius": 24}]
    mods, notes = models.grenade_count_model(eg, "Explosive Grenade", sets, {})
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(200.0)
    assert mods[0][models.HIT_CUT_PER_USE] == pytest.approx(3.0) and mods[0]["tag"] == models.GRENADE_ENTRY
    assert notes[0]["confidence"] == "MEDIUM-LOW" and "play" in notes[0] and "2.00 (LOW)" in notes[0]["basis"]
    one = [{"label": "Explosive Grenade", "TotalDPS": 100.0, "ProjectileCount": 1, "AreaOfEffectRadius": 24}]
    assert models.grenade_count_model(eg, "Explosive Grenade", one, {}) == ([], [])
    assert models.grenade_count_model(eg, "Explosive Grenade", sets, {}, {models.GRENADE_ENTRY}) == ([], [])
    oil = Skill("Oil Grenade", 1, attack=True)
    ground = [{"label": "Oil Ground", "TotalDPS": 50.0, "ProjectileCount": 2, "AreaOfEffectRadius": 26}]
    mods, notes = models.grenade_count_model(oil, "Oil Ground", ground, {})
    assert mods == [] and notes[0]["value"] == "x1" and "UNVERIFIED" in notes[0]["basis"]
    assert models.grenade_count_model(Skill("Spark", 1), "Spark", sets, {}) == ([], [])


def test_cluster_grenade_throw_of_several_primaries():
    sets = [{"label": "Primary Grenade", "TotalDPS": 10.89, "ProjectileCount": 1, "AreaOfEffectRadius": 25},
            {"label": "Mini Grenades", "TotalDPS": 7.24}]
    cg = Skill("Cluster Grenade", 1, attack=True)
    # (GEM47: a preset with no movement tally, so the geometry alone is tested; the boss walking off before
    # the bursts is test_gem47's)
    stats = {"AreaOfEffectMod": 1.0, "_boss": "The Bodach"}
    one, _ = models.cluster_grenade_model(cg, "Primary Grenade", sets, stats, fuse=2.0)
    two_sets = [{**sets[0], "ProjectileCount": 2}, sets[1]]
    two, notes = models.cluster_grenade_model(cg, "Primary Grenade", two_sets, stats, fuse=2.0)
    r = models.bosses.hit_radius(stats)[0]
    bursts, minis = models.cluster_throw(r, 1.0, models.CLUSTER_OFFSET, 1.0, 2, 2.5)
    lat = max(models.grenade_laterals(2, 50))  # (two primaries at +-25 deg, each its own minis at its own distance)
    away = (lat ** 2 + models.CLUSTER_OFFSET ** 2) ** 0.5
    assert bursts == pytest.approx(2.0) and minis == pytest.approx(2 * models.cluster_minis_on_boss(r, 1.0, away, 1.0))
    assert two[0]["value"] == pytest.approx(100 * ((2 * 10.89 + minis * 7.24) / 10.89 - 1), abs=0.01)
    assert two[0]["value"] > one[0]["value"] and "one primary fewer" in notes[0]["basis"]
    assert two[0][models.HIT_CUT_PER_USE] == pytest.approx(bursts + minis)


def test_run_and_gun_on_a_grenade_goes_to_projectile_count():
    eg = Skill("Explosive Grenade", 1, attack=True)
    texts = ["Projectile Attacks have a 20% chance to fire two additional Projectiles while moving"]
    mods, notes = models.run_and_gun_model(texts, {"ProjectileCount": 1}, eg, {"UsableWhileMoving"}, False)
    assert mods[0]["name"] == "TwoAdditionalProjectilesChance" and mods[0]["value"] == 20
    mods, notes = models.run_and_gun_model(texts, {"ProjectileCount": 1}, eg, {"UsableWhileMoving"}, True)
    assert mods == [] and notes[0]["value"] == 0


def test_infused_gate_zero_rows():
    fb = Skill("Fireball", 1, stat_set="Projectile")
    sets = [{"label": "Projectile", "TotalDPS": 90.0}, {"label": "Firebolts", "TotalDPS": 60.0},
            {"label": "Explosion", "TotalDPS": 40.0}]
    _, notes = models.gated_set_model(fb, "Projectile", sets, 0.5, "test")
    assert "R46-10" in notes[0]["basis"] and "no firebolt hits the boss: 90 DPS" in notes[0]["basis"]
    _, none = models.gated_set_model(fb, "Projectile", sets, 0.0, "test")
    assert "R46-10" not in (none[0]["basis"] if none else "")


def _floor_sum(lam, life):
    import math
    return sum(math.exp(-lam) * lam ** k / math.factorial(k) * math.floor(k * life / 2000) for k in range(80))


def test_verglas_model():
    fb = Skill("Frostbolt", 1)
    mods, notes = models.verglas_model(fb, ["Verglas"], 0.2, 12714.0, "its cooldown", "Frostbolt's hits", 0.0, 0.0, "")
    want = _floor_sum(1.2, 12714.0)  # (GEM36, R48-6: E[floor(K x 12,714 / 2,000)], K ~ Poisson(0.2 x 6 s))
    assert mods[0]["name"] == "DamageGainAsCold" and mods[0]["value"] == pytest.approx(want, abs=1e-3)
    assert "each crystal floored on its own +7.2%" in notes[0]["basis"]  # (GEM35's old headline, now a row)
    assert "9 segments +" in notes[0]["basis"]  # (R48-7 / R49-4's rows)
    assert mods[0]["tag"] == models.VERGLAS_ENTRY and notes[0]["confidence"] == "MEDIUM-LOW"
    assert "+6.0%" in notes[0]["basis"]  # (the one-crystal row: 6% while one was destroyed, up all fight)
    broken_by_none, notes = models.verglas_model(fb, ["Verglas"], 0.2, 12714.0, "", "", 0.0, 0.0, "")
    assert broken_by_none == [] and notes[0]["value"] == "+0.0%"
    locus, _ = models.verglas_model(fb, ["Verglas"], 0.0, 0.0, "", "", 0.5, 9536.0, "test")
    assert locus[0]["value"] == pytest.approx(_floor_sum(3.0, 9536.0), abs=1e-3)
    assert models.verglas_model(fb, [], 0.2, 12714.0, "", "x", 0.0, 0.0, "") == ([], [])
    assert models.verglas_model(fb, ["Verglas"], 0.2, 12714.0, "", "x", 0.0, 0.0, "", {models.VERGLAS_ENTRY}) == ([], [])


def test_valako_threshold_halves_the_arcs_buildup():
    v = Skill(models.VALAKO_SKILL, 1)
    mods, notes = models.valako_threshold_model(v)
    assert mods[0]["name"] == "EnemyElectrocuteBuildup" and mods[0]["value"] == pytest.approx(-50.0)
    assert notes[0]["confidence"] == "LOW" and "UNVERIFIED" in notes[0]["basis"]
    assert models.valako_threshold_model(Skill("Spark", 1)) == ([], [])
    assert models.valako_threshold_model(v, {models.VALAKO_THRESHOLD_ENTRY}) == ([], [])


def test_mirror_loop_shatters_every_mirror():
    # (GEM35, R47-2: the Frostbolt loop's mirror side) with returning, always-piercing copies every mirror shatters
    mirror = Skill(name="Mirror of Refraction", cooldown_recovery=1.0,
                   level_stats={models.REPEAT_STAT: 2000, models.MIRROR_COPIES_STAT: 9})
    fb = Skill(name="Frostbolt", spell=True)
    slow = {"ProjectileCount": 1, "Speed": 0.2}  # (0.2 casts a second: fewer than the 0.5 mirrors)
    plain, _ = models.mirror_of_refraction_model([mirror, fb], fb, slow, {"enemyDistance": 50})
    looped, notes = models.mirror_of_refraction_model([mirror, fb], fb, slow, {"enemyDistance": 50}, loop="test")
    assert looped[0]["value"] == pytest.approx(plain[0]["value"] * 0.5 / 0.2, abs=0.01) and "it holds 100% of the fight" in notes[0]["basis"]
    assert "chaining into one more mirror" not in notes[0]["basis"]
    gems = [{"name": "Frostbolt"}, {"name": models.BREACHLORDS_AMALGAM}]
    assert models.mirror_loop_source(fb, {"Cold", "Projectile"}, gems)
    assert not models.mirror_loop_source(fb, {"Cold", "Triggered"}, gems)
    assert not models.mirror_loop_source(fb, {"Cold"}, gems[:1])


def test_mirror_loop_broken_by_the_boss_forced_moves():
    # (GEM44, R60-9) each cast restarts the loop, the boss's forced moves break it: u = (1 - e^(-m T)) / (m T)
    import math
    assert models.mirror_loop_up(0.5, 0.0) == 1.0 and models.mirror_loop_up(0.0, 0.1) == 0.0
    assert models.mirror_loop_up(0.5, 0.1) == pytest.approx((1 - math.exp(-0.2)) / 0.2)
    mirror = Skill(name="Mirror of Refraction", cooldown_recovery=1.0,
                   level_stats={models.REPEAT_STAT: 2000, models.MIRROR_COPIES_STAT: 9})
    fb = Skill(name="Frostbolt", spell=True)
    slow = {"ProjectileCount": 1, "Speed": 0.2}
    plain, _ = models.mirror_of_refraction_model([mirror, fb], fb, slow, {"enemyDistance": 50})
    always, _ = models.mirror_of_refraction_model([mirror, fb], fb, slow, {"enemyDistance": 50}, loop="test")
    broken, notes = models.mirror_of_refraction_model([mirror, fb], fb, slow, {"enemyDistance": 50}, loop="test",
                                                      moves=(0.1, "a test boss"), dodges=0.5)
    u = models.mirror_loop_up(0.2, 0.1)
    gain_always, gain_plain = always[0]["value"], plain[0]["value"]  # (each a % more: 0.5 vs 0.2 shatters a second)
    assert broken[0]["value"] == pytest.approx(u * gain_always + (1 - u) * gain_plain, abs=0.01)
    assert gain_plain < broken[0]["value"] < gain_always
    basis = notes[0]["basis"]
    assert f"it holds {u:.0%} of the fight" in basis and "a test boss" in basis and "loop always on" in basis
    assert "every dodge roll breaking it too" in basis and "no loop x" in basis


def test_gas_grenade_cloud_explodes_only_when_detonated():
    # (GEM35: probe POE2T7224, the search scored the Explosion with nothing to set it off)
    gas = Skill("Gas Grenade", 1, attack=True)
    sets = [{"label": "Impact", "TotalDPS": 18.6}, {"label": "Poison Cloud", "TotalDPS": 101.9},
            {"label": "Explosion", "TotalDPS": 153.9}]
    mods, notes = models.detonated_set_model(gas, "Explosion", sets, 0.4, 0.0, "no Detonator")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] < 0  # (steered down to the cloud's DoT)
    assert "clouds" in notes[0]["basis"] or "set it off" in notes[0]["label"]
    assert "Ignited or Burning inside the cloud" in notes[0]["basis"]  # (GEM36, R48-5)
    lit, _ = models.detonated_set_model(gas, "Explosion", sets, 0.4, 0.0, "the boss Ignited", share=0.5)
    assert lit[0]["name"] == "DPS" and lit[0]["value"] == pytest.approx(100 * ((101.9 + 153.9 * 0.5) / 153.9 - 1), abs=1e-3)
    fed, _ = models.detonated_set_model(gas, "Explosion", sets, 0.4, 0.5, "a Detonator")
    assert fed[0]["value"] > mods[0]["value"]
