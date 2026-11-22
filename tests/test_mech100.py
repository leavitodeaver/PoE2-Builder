"""MECH100: a curse gem under level 18 can't curse a boss above its level cap (models.CURSE_LEVEL_STAT)."""
import pytest
from builder.search import assumptions, models

GEMS = {"Despair": {"skillTypes": ["AppliesCurse", "Spell"]}, "Fireball": {"skillTypes": ["Spell"]},
        "Elemental Weakness": {"skillTypes": ["AppliesCurse"]}}
GROUPS = [{"gems": [{"name": "Despair"}]}, {"gems": [{"name": "Fireball"}], "main": True}]


def _use(stats):
    return {u.name: u for u in models.curse_uses(GROUPS, GEMS, {"Speed": 1.0, "HitChance": 100, **stats}, 3.0)}


def test_a_curse_under_its_level_cap_never_reaches_a_higher_boss():
    capped = _use({"_curse_level_caps": {"Despair": 78.0}, "_boss_level": 82})["Despair"]
    assert capped.share == 0.0 and not capped.by_hand and "level 78" in capped.how
    mods, _ = models.curse_model([capped], [models.Skill("Despair", 1)], models.Skill("Fireball", 2))
    assert any("more Effect of your Curses" in str(m) for m in mods)
    assert not any("Attack and Cast Speed" in str(m) for m in mods)  # (never cast: no hand-cast time charged)


def test_no_cap_a_lower_boss_or_nothing_read_leaves_the_curse_as_before():
    before = _use({})["Despair"]
    for stats in ({"_curse_level_caps": {"Despair": 0.0}, "_boss_level": 82},   # gem level 18+
                  {"_curse_level_caps": {"Despair": 78.0}, "_boss_level": 75},  # a boss under the cap
                  {"_curse_level_caps": {"Despair": 78.0}},                     # no boss level read
                  {"_boss_level": 82}):                                         # no cap read
        assert _use(stats)["Despair"] == before


def test_the_caps_come_from_the_bridges_level_stats():
    stat = models.CURSE_LEVEL_STAT
    skills = [{"name": "Despair", "disabled": False, "levelStats": {stat: 74}},
              {"name": "Elemental Weakness", "disabled": False, "levelStats": {stat: 78}},
              {"name": "Elemental Weakness", "disabled": False, "levelStats": {stat: 0}},
              {"name": "Enfeeble", "disabled": True, "levelStats": {stat: 20}},
              {"name": "Fireball", "disabled": False, "levelStats": {}}]
    assert assumptions.curse_level_caps(skills) == {"Despair": 74.0, "Elemental Weakness": 0.0}
    assert models.curse_gem_names(GEMS) == ["Despair", "Elemental Weakness"]


def _groups(*gems_by_group, main=1):
    return [{"index": i, "enabled": True, "gems": [{"name": n, "support": s} for n, s in gems]}
            for i, gems in enumerate(gems_by_group, start=1)]


def test_neural_overload_or_voltaic_mark_electrocute_the_primed_boss():
    """MECH100 (plans/gs-neural-overload.md, an UNDERCOUNT): the boss Electrocutes on the first hit after its bar is Primed
    at 70% (Unique) with Neural Overload on the main skill or Voltaic Mark on it; without either, at the full bar."""
    spark = [("Spark", False)]
    assert models.electrocute_primer(_groups(spark + [("Neural Overload", True)]), 1, "Spark") == "Neural Overload"
    assert models.electrocute_primer(_groups(spark, [("Neural Overload", True), ("Arc", False)]), 1, "Spark") == ""
    assert models.electrocute_primer(_groups(spark, [("Voltaic Mark", False)]), 1, "Spark") == "Voltaic Mark"
    assert models.electrocute_primer(_groups(spark, [("Sniper's Mark", False)], [("Voltaic Mark", False)]), 1,
                                     "Spark") == ""  # (one Mark: mark_model's first)
    assert models.electrocute_primer(_groups(spark), 1, "Spark") == ""
    stats = {"Speed": 4.0, "HitChance": 100, "ElectrocuteBuildupAvg": 10.0}
    plain = models.immobilise_period(stats, "Electrocute")
    primed = models.immobilise_period({**stats, "_electrocute_primed": "Neural Overload"}, "Electrocute")
    refill = plain - models.immobilise_duration(stats, "Electrocute")
    assert refill > 0
    assert primed == pytest.approx(plain - refill + models.primed_hit_refill(refill, models.hits_per_second(stats)))
    assert primed < plain
    share, how = models.immobilised_share({**stats, "_electrocute_primed": "Voltaic Mark"}, "Electrocute")
    assert share > models.immobilised_share(stats, "Electrocute")[0] and "Voltaic Mark" in how
    assert models.immobilise_period({**stats, "_electrocute_primed": "Neural Overload"}, "Freeze") == \
        models.immobilise_period(stats, "Freeze")  # (Electrocute only)
