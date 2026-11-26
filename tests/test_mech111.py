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


# Shock chance over the roll with Lucky damage (bridge shockChancePerHit, MECH111 items 2 and 5) -------------------------

def lucky_mean(k: float, lo: float, hi: float) -> float:
    """The mean of min(100, k x D) for D the better of two even rolls between lo and hi (density 2 (x - lo) / w^2)."""
    if hi <= lo:
        return min(100.0, k * lo)
    w, full = hi - lo, 100.0 / k
    b = min(hi, full)
    f = lambda x: k * (x ** 3 / 3 - lo * x ** 2 / 2)  # noqa: E731
    area = 2 * (f(b) - f(lo)) / w ** 2 if b > lo else 0.0
    return area + (100.0 * (1 - ((max(lo, full) - lo) / w) ** 2) if hi > max(lo, full) else 0.0)


def test_lucky_damage_weights_the_shock_roll_toward_its_top():
    import pytest

    pytest.importorskip("builder.pob.engine")
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    fields = ["ShockChanceOnHit", "ShockChanceOnCrit", "ShockChancePerHit", "CritChance", "ShockLightningMin",
              "ShockLightningMax", "LightningStoredHitMin", "LightningStoredHitMax", "LightningStoredCritMin",
              "LightningStoredCritMax"]
    engine = PobEngine()
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.call("groups")["groups"] if g["gems"] and g["gems"][0]["name"] == "Spark")
    gems = [{"name": n["name"], "level": n.get("level") or 20, "quality": n.get("quality") or 0} for n in group["gems"]]
    build.call("setAssumptions", mods=[models.raw_mod("LuckyHitsChance", "BASE", 100)], fields=[])
    checked = {False: 0, True: 0}
    for esh in (False, True):
        build.set_group(index=group["index"], gems=gems + ([{"name": "Esh's Prowess", "level": 1, "quality": 0}]
                                                            if esh else []), main=True)
        for level in (84, 20, 12, 9, 7, 5, 3, 1):
            s = build.call("setConfig", vars={"enemyLevel": level}, fields=fields)["stats"]
            lo, hi = s["ShockLightningMin"], s["ShockLightningMax"]
            if not 0 < s["ShockChanceOnHit"] < 100 or hi <= lo:
                continue
            k = s["ShockChanceOnHit"] / ((lo + hi) / 2)  # (Spark: no base chance, the formula through 0)
            scale = hi / s["LightningStoredHitMax"]
            clo, chi = s["LightningStoredCritMin"] * scale, s["LightningStoredCritMax"] * scale
            if esh:  # the maximum at 75% when Lucky (KeywordPopups BooleanDamageRoll)
                hit = 0.25 * min(100.0, k * lo) + 0.75 * min(100.0, k * hi)
                crit = 0.25 * min(100.0, k * clo) + 0.75 * min(100.0, k * chi)
            else:
                hit, crit = lucky_mean(k, lo, hi), lucky_mean(k, clo, chi)
            c = s["CritChance"] / 100
            assert s["ShockChancePerHit"] == pytest.approx(hit * (1 - c) + crit * c, rel=1e-6)
            checked[esh] += 1
    build.call("setAssumptions", mods=[], fields=[])
    build.call("setConfig", vars={"enemyLevel": 84}, fields=[])
    assert checked[False] and checked[True]
