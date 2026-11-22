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


def test_a_tagged_uses_raise_reaches_the_readers_past_the_time_cuts_clamp():
    """MECH100 (the manager 12:00, MECHB55's three: Explosive Rhythm, the Last Lament's last bolt, Forge Hammer's loop):
    a "DPS" MORE tagged USES_RAISE is more uses a second, each one hit: never in build.main_hit_cut (clamped to [0, 1]),
    but on build.main_uses_raise, and at_main_share raises the main skill's Speed by it for every reader."""
    from types import SimpleNamespace

    raise3 = {"name": "DPS", "type": "MORE", "value": 200.0, "skill": "Forge Hammer", models.USES_RAISE: 3.0}
    cut = {"name": "DPS", "type": "MORE", "value": -50.0, "skill": "Forge Hammer"}
    assert models.hit_cut_of([raise3]) is None and models.hit_cut_of([raise3, cut]) == 0.5
    assert models.uses_raise_of([raise3, cut]) == 3.0 and models.uses_raise_of([cut]) is None
    assert models.uses_raise_of([raise3], skill="Other") is None
    build = SimpleNamespace()
    assumptions.hit_cut(build, [raise3, cut], "Forge Hammer")
    assert build.main_hit_cut == 0.5 and build.main_uses_raise == 3.0 and build.main_hits_per_use is None
    stats = {"Speed": 2.0, "HitChance": 100, "_main_share": 0.5}
    assert models.at_main_share({**stats, "_uses_raise": 3.0})["Speed"] == pytest.approx(3.0)
    assert models.at_main_share({**stats, "_uses_raise": 3.0}, per_use=False)["Speed"] == pytest.approx(3.0)
    assert models.at_main_share({**stats, "_uses_raise": None})["Speed"] == pytest.approx(1.0)
    assert models.at_main_share({"Speed": 2.0, "_uses_raise": 0.5})["Speed"] == 2.0  # (a raise, never a cut)
    assert models.at_main_share({"Speed": 2.0})["Speed"] == 2.0  # (untouched: no share, no raise)
    once = models.at_main_share({**stats, "_uses_raise": 3.0})
    assert models.at_main_share(once)["Speed"] == once["Speed"]  # (applied once)
    shock = {"Speed": 1.0, "HitChance": 100, "ElectrocuteBuildupAvg": 5.0}
    assert models.immobilise_period({**shock, "_uses_raise": 3.0}, "Electrocute") < \
        models.immobilise_period(shock, "Electrocute")


def test_a_main_whirlwind_carries_the_ignited_grounds_debuff_8_s_past_it():
    """MECH100 (the manager 12:00, RESEARCH47 R47-2): Whirling Slash's Whirlwind over the build's own Ignited Ground keeps
    the ground's debuff on the boss 8 s past each ground (KeywordPopups Whirlwind), at the main skill's share; other main
    skills, and no ground, change nothing (the no-ground "infinite" claim is not modelled)."""
    from builder.search import conditions

    pieces = [("Birth of Fury's trail", 0.25, 8.0, 500.0), ("none", 0.0, 4.0, None)]
    out, how = conditions.whirlwind_carry(pieces, "Whirling Slash", 1.0)
    assert out[0][1] == pytest.approx(0.5) and out[1] == pieces[1] and "8 s past" in how
    assert conditions.whirlwind_carry(pieces, "Whirling Slash", 0.5)[0][0][1] == pytest.approx(0.375)
    assert conditions.whirlwind_carry([("Incinerate", 0.8, 8.0, None)], "Whirlwind Lance", 1.0)[0][0][1] == 1.0
    assert conditions.whirlwind_carry(pieces, "Spark", 1.0) == (pieces, "")
    assert conditions.whirlwind_carry([("none", 0.0, 4.0, None)], "Whirling Slash", 1.0)[1] == ""
