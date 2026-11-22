"""MECH100: a curse gem under level 18 can't curse a boss above its level cap (models.CURSE_LEVEL_STAT)."""
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
