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
