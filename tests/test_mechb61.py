"""MECHB61: Tornado Shot counts only what is fired at it (models.tornado_fired_count)."""
import pytest

from builder.search import models as M

L20 = {"tornado_shot_number_of_hits_allowed": 8, "tornado_shot_projectile_damage_+%_final": -41,
       "base_skill_effect_duration": 15000}
ICE_SETS = [{"index": 1, "label": "Arrow", "ProjectileCount": 5.75}, {"index": 2, "label": "Shards", "ProjectileCount": 14.66}]


def _skill(name, **kw):
    return M.Skill.of({"name": name, "attack": True, "group": kw.pop("group", 1), **kw})


def _factor(count, speed=2.0, tornado_speed=1.5):
    uses = min(8 / count, 15 * speed)
    return count * 0.59 * (uses / speed) / (uses / speed + 1 / tornado_speed)


def test_ice_shots_shards_are_not_fired_at_the_tornado():
    """(the OVERCOUNT on example build 16) The Shards set's 14.66 counted 12 shards as arrows: only the Arrow set's 5.75
    are fired, so the Shards set's factor is the Arrow set's."""
    tornado = _skill("Tornado Shot", group=2)
    stats = {"ProjectileCount": 14.66, "Speed": 2.0}
    mods, notes = M.tornado_copy_model(_skill("Ice Shot", statSet="Shards"), {"Attack", "Projectile"}, stats, tornado,
                                       L20, 1.5, ICE_SETS, {"Bow"})
    assert mods[0]["scale"] == pytest.approx(100 * (_factor(5.75) - 1))
    assert "not fired at the Tornado" in notes[0]["basis"]
    arrow, _ = M.tornado_copy_model(_skill("Ice Shot", statSet="Arrow"), {"Attack", "Projectile"},
                                    {"ProjectileCount": 5.75, "Speed": 2.0}, tornado, L20, 1.5, ICE_SETS, {"Bow"})
    assert arrow[0]["scale"] == pytest.approx(mods[0]["scale"])


def test_a_variant_of_the_fired_set_keeps_its_own_count():
    assert M.tornado_fired_count("Frenzy-Charged", [{"label": "Projectile", "ProjectileCount": 1}], 4.0) == (4.0, "")
    assert M.tornado_fired_count("Arrow", ICE_SETS, 5.75) == (5.75, "")
    assert M.tornado_fired_count("Shards", [], 14.66) == (14.66, "")  # no sets read: unchanged
    assert M.tornado_fired_count("Shards", ICE_SETS, 14.66)[0] == 5.75


def test_a_projectile_that_is_no_arrow_spear_or_bolt_is_not_sucked_in():
    mods, notes = M.tornado_copy_model(_skill("Falling Thunder", statSet="Projectile"), {"Attack", "Projectile"},
                                       {"ProjectileCount": 4, "Speed": 2.0}, _skill("Tornado Shot", group=2), L20, 1.5,
                                       [], {"Staff"})
    assert mods == [] and notes[0]["value"] == 0


def _wardbound(limit):
    return M.Skill.of({"name": "Wardbound Minions", "group": 2, "triggered": True,
                       "minion": {"duration": 15, "limit": limit, "castTime": 1.0, "speed": 1.0}})


def test_the_archon_of_undeath_raises_a_binding_temporary_minion_limit():
    """(MECHB61, step 4) 1 use a second x 15 s = 15 minions, held to the limit 2; Archon of Undeath up half the fight
    doubles it: (2 + 4) / 2 = 3 on average. A limit that doesn't bind gains nothing."""
    spell = _wardbound(2)
    assert M.minion_count(spell, 1.0)[0] == 2
    assert M.minion_count(spell, 1.0, 4.0)[0] == 4
    mixed, how = M.undeath_limit_mix(lambda lim: M.minion_count(spell, 1.0, lim)[0], 2, (0.5, 2.0))
    assert mixed == pytest.approx(3.0) and "Archon of Undeath up 50%" in how
    loose = _wardbound(40)
    assert M.undeath_limit_mix(lambda lim: M.minion_count(loose, 1.0, lim)[0], 40, (0.5, 2.0)) == (15.0, "")


def test_tempest_bell_quality_ramp_lifts_its_shockwaves():
    """(MECHB61, COV55 row 14) "Shockwave deals 3% more Damage per Hit taken by Bell" at quality 20: 5 shockwaves a Bell
    average 1 + 3% x (5 - 1) / 2 = x1.06 of their damage."""
    builder = ("Killing Palm", 10.0, 100.0, True)
    plain, _ = M.tempest_bell_model(0.2, 1.0, builder, 100.0, 20.0, 6.0, [])
    ramped, notes = M.tempest_bell_model(0.2, 1.0, builder, 100.0, 20.0, 6.0, [], ramp=3.0)
    waves = min(10.0 * (1 - 0.2), 0.2 * 10)  # (8 Strikes a second, held to 10 a Bell x 0.2 Bells)
    assert waves / 0.2 == pytest.approx(10.0)
    assert ramped[0]["value"] == pytest.approx(round(100 * waves * 20.0 * (1 + 0.03 * 9 / 2) / (0.2 * 100.0), 4), rel=1e-3)
    assert plain[0]["value"] < ramped[0]["value"] and "3% more a Hit the Bell has taken" in notes[0]["basis"]
