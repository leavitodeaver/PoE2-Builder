"""GEM47's models: Harbinger of Madness's apparitions (R65-6), Crushing Fear's adds row (A40-53), Sunder's Shockwave on a
lone boss (R65-5)."""
from builder.search import models


def test_harbinger_counts_an_apparition_per_melee_hit_on_the_boss():
    mods, notes = models.harbinger_model(231.5, 2.0, "Mace Strike")
    assert [m["name"] for m in mods] == ["AddedHarbinger"]
    assert abs(models.added_total(mods) - 463.0) < 0.01
    assert notes[0]["entry"] == models.HARBINGER_ENTRY and "+232" in notes[0]["basis"]  # (the one-a-second row)


def test_harbinger_without_melee_hits_adds_nothing_and_says_why():
    mods, notes = models.harbinger_model(231.5, 0.0, "the main skill Spark isn't a melee Attack")
    assert mods == [] and "isn't a melee Attack" in notes[0]["basis"] and notes[0]["value"] == "0 DPS"
    assert models.harbinger_model(231.5, 2.0, "x", {models.HARBINGER_ENTRY}) == ([], [])


def test_crushing_fears_adds_row_is_a_row_only():
    lone = models.crushing_fear_adds_row(0.0, 1.5, 100.0, "the main skill")
    assert len(lone) == 1 and lone[0]["value"].startswith("0")
    row = models.crushing_fear_adds_row(0.2, 1.5, 100.0, "the main skill")
    assert row[0]["value"].startswith("+22 DPS") and "not counted" in row[0]["value"]  # (0.2 a second x 100 x 1.1)
    assert models.crushing_fear_adds_row(5.0, 0.5, 100.0, "group 4, pressed")[0]["value"].startswith("+55 DPS")


def test_sunders_shockwave_takes_nothing_on_a_lone_boss():
    # (R65-5: the shockwave hits OTHER enemies; the line is tagged to the Shockwave set, so the Wave keeps its damage)
    skill = models.Skill("Sunder", group=1, attack=True, level_stats={"sunder_armor_break_extra_magnitude": 20})
    for scored in ("Wave", "Shockwave"):
        skill.stat_set = scored
        mods, notes = models.gem_stat_model([skill])
        assert [(m["line"], m["scale"], m.get("statSets")) for m in mods if "less" in m["line"]] == [
            ("1% less Damage", 100, ["Shockwave"])], scored
        assert any(n["entry"] == "gem-gem47-sunder-shockwave-other-enemies" for n in notes)


def test_earthshatters_spikes_set_off_later_miss_a_boss_that_moved():
    # (MECHD9's reader rule: the Explosion x the share of a set-off spike's wait the boss is still in its area)
    from builder.search import bosses
    assert models.detonated_wait(0.5, 6.0) == 2.0 and models.detonated_wait(0.1, 6.0) == 6.0
    assert models.detonated_wait(0.0, 6.0) == 0.0
    there, how = bosses.stay_share({"_boss": "Arbiter of Ash"}, 1.5, 2.0)
    assert 0 < there < 1 and "Arbiter of Ash" in how
    skill = models.Skill("Earthshatter", group=1, attack=True)
    skill.stat_set = "Slam"
    sets = [{"label": "Slam", "TotalDPS": 100.0}, {"label": "Explosion", "TotalDPS": 300.0}]
    still, _ = models.detonated_set_model(skill, "Slam", sets, 1.0, 1.0, "a Warcry a second")
    moved, notes = models.detonated_set_model(skill, "Slam", sets, 1.0, 1.0, "a Warcry a second",
                                              stay=(0.5, 0.8, "the boss moves"))
    factor = lambda mods: 1 + mods[0]["value"] / 100 if mods else 1.0
    assert factor(moved) < factor(still)
    assert "standing still" in notes[0]["basis"] and notes[0]["set_shares"]["Explosion"] < 1.0


def test_stay_rule_reads_parts_left_on_the_ground_not_parts_stuck_in_the_boss():
    # (Stormblast Bolts lodge in the ground; Rapid Assault's spearhead is "stuck in the target", so it moves with it)
    assert "Stormblast Bolts" in models.DETONATED_STAY and "Earthshatter" in models.DETONATED_STAY
    assert "Rapid Assault" not in models.DETONATED_STAY
