"""base_deal_no_damage on stat sets PoB scores with damage (COVC13; ledger/entries/gems-active-batch9.yaml)."""
from builder.search import models

ENTRY = "gem-active-base-set-deals-no-damage"
SETS = {"Conductive Runes": ("Conductive Runes", "Explosion"), "Spearfield": ("Spearfield", "Explosion"),
        "Rolling Slam": ("Rolling Slam", "Second Slam"), "Tempest Bell": ("Tempest Bell", "Impact")}


def test_the_base_set_deals_no_damage_and_the_damage_sets_keep_theirs():
    for name, (base, damaging) in SETS.items():
        skill = models.Skill(name, group=1, attack=True, level_stats={"base_deal_no_damage": 1})
        skill.stat_set = base
        mods, notes = models.gem_stat_model([skill])
        mine = [(m["line"], m["scale"], m.get("skill")) for m in mods if "less Damage" in m["line"]]
        assert mine == [("1% less Damage", 100, name)], name
        assert any(n["entry"] == ENTRY for n in notes)
        skill.stat_set = damaging  # (the explosion / slam / impact set: nothing taken off)
        mods, notes = models.gem_stat_model([skill])
        assert not [m for m in mods if "less Damage" in m["line"]], name
        assert any(n["entry"] == ENTRY and n["value"] == "not counted" for n in notes)


def test_a_skill_the_entry_does_not_list_is_untouched():
    skill = models.Skill("Boneshatter", group=1, attack=True, level_stats={"base_deal_no_damage": 1})
    skill.stat_set = "Boneshatter"
    assert not [n for n in models.gem_stat_model([skill])[1] if n["entry"] == ENTRY]
