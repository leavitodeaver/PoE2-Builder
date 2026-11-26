"""MECH111 (MECH-A): a Nova's reach (conditions.nova_reach, models.nova_reach_model)."""
from builder.search import conditions, models


def test_a_nova_around_you_reaches_its_radius_plus_the_boss():
    reach, how = conditions.nova_reach_of({"Spell", "Nova", "Area"}, 3.2, 0.3)
    assert abs(reach - 3.5) < 1e-9 and "around you" in how
    # not a Nova: no reach
    assert conditions.nova_reach_of({"Spell", "Area"}, 3.2, 0.3) == (None, "")
    # moved to the target (Astral Projection, Voltaxic Burst, Ice Nova on Frostbolts): no reach, said
    reach, how = conditions.nova_reach_of({"Spell", "Nova"}, 3.2, 0.3, moved="nova_skills_cast_at_target_location")
    assert reach is None and "target" in how
    # a totem's, a trap's, a mine's or a minion's Nova lands around that
    for types, totem in (({"Nova", "UsedByTotem"}, False), ({"Nova"}, True), ({"Nova", "Trapped"}, False),
                         ({"Nova", "RemoteMined"}, False), ({"Nova", "Minion"}, False)):
        reach, how = conditions.nova_reach_of(types, 3.2, 0.3, by_totem=totem)
        assert reach is None and how
    # no radius read: not limited (UNVERIFIED), never zeroed
    reach, how = conditions.nova_reach_of({"Nova"}, 0.0, 0.3)
    assert reach is None and "UNVERIFIED" in how


def test_the_natural_distance_stands_within_a_novas_reach():
    assert conditions.natural_distance({"Spell", "Nova"}, "Ice Nova", 3.55) == 3.5
    assert conditions.natural_distance({"Spell", "Nova"}, "Ice Nova", None) == conditions.RANGED_DISTANCE
    # a reach past the natural distance changes nothing
    assert conditions.natural_distance({"Spell", "Nova"}, "Ice Nova", 9.0) == conditions.RANGED_DISTANCE
    assert conditions.natural_distance({"Melee", "Nova"}, "x", 1.25) == 1.2
    # the boss distance PoB is set to (enemyDistance, 10 units a metre) follows it, said
    units, why = conditions._boss_distance([], {"_main_types": {"Spell", "Nova"}, "_nova_reach": (3.5, "reaches 3.5 m"),
                                                "PresenceRadius": 40})
    assert units == 35 and "reaches 3.5 m" in why
    # a distance the search chose stands (the model takes its hits off out of reach)
    units, _ = conditions._boss_distance([], {"_main_types": {"Spell", "Nova"}, "_nova_reach": (3.5, "x"),
                                              "_distance_choice": (4.0, "Presence edge")})
    assert units == 40


def test_a_nova_scores_no_hits_out_of_reach():
    main = models.Skill("Ice Nova", 1, spell=True)
    mods, notes = models.nova_reach_model(main, 3.5, "reaches 3.5 m", 5.0)
    assert [(m["name"], m["type"], m["value"]) for m in mods] == [("DPS", "MORE", -100)]
    assert "out of reach" in notes[0]["value"]
    mods, notes = models.nova_reach_model(main, 3.5, "reaches 3.5 m", 3.5)
    assert mods == [] and "in reach" in notes[0]["value"]
    # moved to the target: kept, said
    mods, notes = models.nova_reach_model(main, None, "lands at the target", 5.0)
    assert mods == [] and "kept" in notes[0]["value"]
    # not a Nova: nothing
    assert models.nova_reach_model(main, None, "", 5.0) == ([], [])
    assert models.nova_reach_model(main, 3.5, "x", 5.0, {models.NOVA_REACH_ENTRY}) == ([], [])

