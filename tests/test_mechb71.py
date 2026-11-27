"""MECHB71: RESEARCH68 R68-3 / R68-7 and AUDIT41 A41-10 (pure: no PoB engine)."""
import pytest

from builder.search import bosses, models


def test_melee_projectiles_row_names_r68_3():
    """R68-3: a melee skill with no projectile of its own gains none (headline); the x2 row says nothing is behind it."""
    main = models.Skill(name="Boneshatter", attack=True)
    note = models.melee_projectiles_note(main, ["Melee Attacks fire 2 additional Projectiles"], [], 0)[0]
    assert note["value"] == "x1.000"
    assert "R68-3: no projectile defined, nothing behind it" in note["basis"] and "x2.000" in note["basis"]


def _sniper(own: float):
    cloud = {"PoisonDPS": 100, "PoisonStacksMax": 1, "PoisonStackPotential": 1, "PoisonDuration": 2, "Duration": 8}
    impact = {"AverageDamage": 600, "Cooldown": 6, "CastRate": 1}
    return models.sniper_gas_arrow_model(impact, cloud, {}, 0, "", False, 0, 0, 0, own)


def test_sniper_cloud_shared_is_the_headline_the_separate_sum_a_row():
    """R68-10 (the manager's ruling): with your own Poison on the boss, the cloud's Poison SHARES the slot (headline):
    your own 150 beats the cloud's 100, so the hit alone (100); against your own 40 it adds 60 (160); the separate sum
    (200) a row. Without your own Poison: 200 as before."""
    dps, _, notes = _sniper(150.0)
    assert abs(dps - 100.0) < 1e-6
    assert "SHARED" in notes[0]["basis"] and "200.0 DPS" in notes[0]["basis"].split("SHARED")[1]
    assert abs(_sniper(40.0)[0] - 160.0) < 1e-6
    dps0, _, notes0 = _sniper(0.0)
    assert abs(dps0 - 200.0) < 1e-6 and "SHARED" not in notes0[0]["basis"]


def test_untallied_named_boss_moves_fall_back_never_zero():
    """A41-10: a named boss with no video tally (The Bodach) reads the fewest tallied moves, capped by its own skills'
    upper bound, labelled; a tallied boss keeps its own tally."""
    untallied = [n for n in bosses.REAL if n not in bosses.RELOCATE_VIDEO]
    for name in untallied:
        rate, top, how = models.boss_moves({"_boss": name}, 0.5)
        if bosses.relocations({"_boss": name}, "skills")[0] > 0:
            assert rate > 0 and "A41-10" in how and rate <= top + 1e-9
    tallied = next(n for n in bosses.REAL if n in bosses.RELOCATE_VIDEO)
    assert models.boss_moves({"_boss": tallied})[0] == bosses.relocations({"_boss": tallied}, "headline", gain=True)[0]


SLAM_TYPES = {"Slam", "Attack", "Melee", "Area"}


def test_delayed_aftershocks_find_the_boss_still_there():
    """(turn 3, GEM49's ranking) Rampage's Aftershocks land 0.3 s late: its Aftershock hits x bosses.still_there(its
    radius, 0.3 s); a slam with no delay keeps them whole."""
    hymn = ["Slam Skills you use yourself cause an additional Aftershock"]
    stats = {"Speed": 1.0, "AreaOfEffectRadius": 15}
    still = bosses.still_there(stats, 1.5, models.AFTERSHOCK_DELAY["Rampage"])[0]
    assert 0 < still < 1
    rampage = models.Skill("Rampage", 1, attack=True, melee=True)
    mods, notes = models.aftershock_model(hymn, stats, rampage, SLAM_TYPES, [], {"Two Hand Mace"},
                                          translate=lambda t: None)
    dps = next(m for m in mods if m["name"] == "DPS")
    assert dps["value"] == pytest.approx(100 * still, abs=1e-3) and "0.3 s after the impact" in notes[0]["basis"]
    sunder = models.Skill("Sunder", 1, attack=True, melee=True)
    mods, _ = models.aftershock_model(hymn, stats, sunder, SLAM_TYPES, [], {"Two Hand Mace"}, translate=lambda t: None)
    assert next(m for m in mods if m["name"] == "DPS")["value"] == pytest.approx(100.0)


def test_seismic_cry_quality_raises_its_empowered_aftershocks():
    """(turn 3, b21) Seismic Cry's quality: its Empowered slams' Aftershocks deal 0.5% more a point (10% at 20)."""
    slam = models.Skill("Rolling Slam", 1, attack=True, melee=True)
    base, _ = models.aftershock_model([], {"Speed": 1.0}, slam, SLAM_TYPES, [], {"One Handed Mace"},
                                      translate=lambda t: None, empowered=(0.25, "a test"))
    more, notes = models.aftershock_model([], {"Speed": 1.0}, slam, SLAM_TYPES, [], {"One Handed Mace"},
                                          translate=lambda t: None, empowered=(0.25, "a test"),
                                          empowered_more=20 * models.SEISMIC_QUALITY_PER_POINT)
    v0 = next(m for m in base if m["name"] == "DPS")["value"]
    v1 = next(m for m in more if m["name"] == "DPS")["value"]
    assert v0 == pytest.approx(25.0) and v1 == pytest.approx(25.0 + 100 * 0.25 * 1.0 * 0.10, abs=1e-3)
    assert "Seismic Cry's quality" in notes[0]["basis"]


def test_toxic_growth_explosion_beside_its_pustules_set():
    """(turn 3, b11) Toxic Growth calculated at its Pustules set: its Explosion added at pustule_cap's share."""
    rates = [{"speed": 1.0, "pustuleLimit": 6, "detonationTime": 15}]
    added, notes = models.pustule_explosion_added(rates, 1000.0, 0.3, {"_boss": None})
    mods, _ = models.pustule_cap(rates, "Toxic Growth", 0.0, 0.3, {"_boss": None})
    share = 1 + next(m for m in mods if m["name"] == "DPS")["value"] / 100
    assert added == pytest.approx(1000.0 * share) and 0 < added < 1000.0 and notes
    assert models.pustule_explosion_added(rates, 0.0, 0.3, None) == (0.0, [])
