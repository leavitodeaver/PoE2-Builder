"""MECHB71: RESEARCH68 R68-3 / R68-7 and AUDIT41 A41-10 (pure: no PoB engine)."""
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
