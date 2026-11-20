"""MECH93 (MECH-A): Creeping Chill's Chilled Ground at the build's own Power Charge spending on its Ice Crystal skill
(GEM25's routed UNDERCOUNT: nothing read the support before)."""
import pytest


def test_creeping_chill_reach_grows_to_the_boss():
    from builder.search import conditions as c

    assert c.creeping_chill_reach(1.0) == pytest.approx(6.0)  # inside 1.2 m from the start
    # 2.0 m: (2 / 1.2)^2 - 1 = 1.778 area growth, reached after 3.56 s: 2.44 s of 6 s
    assert c.creeping_chill_reach(2.0) == pytest.approx(6.0 - ((2.0 / 1.2) ** 2 - 1) / 0.5)
    assert c.creeping_chill_reach(2.4) == pytest.approx(0.0, abs=1e-9)  # the +300% cap: radius 2.4 m at 6 s
    assert c.creeping_chill_reach(5.0) == 0.0


def test_chilled_ground_counts_creeping_chill_where_it_is_made():
    from builder.search import conditions as c

    # Frost Wall: made at the target, one ground a use with a charge: 0.1/s x 6 s = 60% of the fight
    share, how = c.chilled_ground({}, [], set(), [("Frost Wall", 0.1, "Frost Wall: 0.10 of its 0.25 uses/s")])
    assert share == pytest.approx(0.6) and "Creeping Chill" in how and "LOW" in how
    # Grim Pillars at you, the boss 2 m away: only the ~2.44 s the ground reaches it
    stats = {"_distance_choice": (2.0, "chosen")}
    share, how = c.chilled_ground(stats, [], set(), [("Grim Pillars", 0.1, "Grim Pillars")])
    assert share == pytest.approx(0.1 * c.creeping_chill_reach(2.0))
    # no trigger (no Power Charge source): no ground, the reason kept
    share, how = c.chilled_ground({}, [], set(), [("Frost Wall", 0.0, "no Power Charge reaches it")])
    assert share == 0.0 and "no Power Charge" in how
    # nothing: as before
    assert c.chilled_ground({}, [], set()) == (0.0, "")


def test_creeping_chill_triggers_share_the_power_charge_flow():
    """The supported crystal skill's triggers: the Power Charges consumed a second (gains capped by the spends), split
    over the Power consumers by uses, at most its own uses; none without a Power Charge source."""
    from builder.search import conditions as c

    class Build:
        def call(self, name, **_):
            assert name == "groups"
            return {"groups": [{"index": 1, "gems": [{"name": "Frost Wall"}, {"name": "Creeping Chill"}]},
                               {"index": 2, "gems": [{"name": "Frozen Locus"}]}]}  # (no support: not counted)

    class Fake:
        build = Build()
        _creeping_chill_skills = c.Conditions._creeping_chill_skills
        _creeping_chill = c.Conditions._creeping_chill

    fake = Fake()
    assert fake._creeping_chill_skills() == ["Frost Wall"]
    stats = {"_consumers": {"Power": {"Frost Wall": 0.25}}, "PowerChargesMax": 3, "PowerChargesDuration": 15,
             "Speed": 1.0, "HitChance": 100.0, "CritChance": 0.0}
    # one Power Charge every 10 s, all spent by Frost Wall (0.25 uses/s wanted): 0.1 triggers/s
    got = fake._creeping_chill(["Gain a Power Charge every 10 seconds"], stats)
    assert got[0][1] == pytest.approx(0.1)
    # Power Charges beyond its uses: at most one trigger a use
    assert fake._creeping_chill(["Gain a Power Charge on Hit"], stats)[0][1] == pytest.approx(0.25)
    assert got[0][0] == "Frost Wall" and "Power Charge" in got[0][2]
    none = fake._creeping_chill([], stats)
    assert none[0][1] == 0.0 and "no Power Charge" in none[0][2]


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_creeping_chill_on_frost_wall_makes_chilled_ground_from_power_charges(engine):
    """MECH93 probe: Frost Wall (main) with Creeping Chill and a Power Charge line: Frost Wall joins the Power Charge
    consumers (the gem data's static types lack the support's SkillConsumesPowerChargesOnUse) and the evidence's
    Chilled Ground share carries Creeping Chill; without a Power Charge line: none."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Frost Wall"))
    skeleton = enumerate_skeletons(Context(payload), ["Frost Wall"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    main = build.main_group()
    old = [{"name": g["name"], "level": g["level"], "quality": g["quality"]} for g in main["gems"]
           if g["name"] != "Creeping Chill"]
    build.set_group(index=main["index"], gems=[*old, {"name": "Creeping Chill", "level": 1, "quality": 0}])
    search.conditions.evidence()
    bare = build.chilled_ground_up
    amulet = chr(10).join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                           "Gain a Power Charge every 2 seconds"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    search.conditions.evidence()
    got = build.chilled_ground_up
    print("PROBE creeping chill", bare, got)
    assert bare[0] == 0.0
    assert got[0] > 0.0 and "Creeping Chill on Frost Wall" in got[1]


def test_calloused_marrow_raises_the_volatility_survival_cap():
    """MECH93: "40% reduced Damage taken from Volatility if you have at least 100 Tribute" (Calloused Marrow) after
    Armour: 10000 Armour, 5000 Life, one hit of 100 x k: 60 k^2 / (10 + k) <= 5000 -> 92 (58 without it); unreduced
    60 k -> 83 (50 without); under 100 Tribute: as without, said."""
    from builder.search import models

    marrow = "40% reduced Damage taken from Volatility if you have at least 100 Tribute"
    crits = {"Speed": 10.0, "HitChance": 100.0, "CritChance": 50.0, "Life": 5001.0, "Armour": 10000.0,
             "PhysicalDamageReductionMax": 90.0}
    basis = models.volatility_model([marrow], {**crits, "Tribute": 120.0}, 100.0)[1][0]["basis"]
    assert "up to 92 " in basis and "-40% Damage taken from Volatility (120 Tribute)" in basis
    assert "the cap 83 with unreduced" in basis
    low = models.volatility_model([marrow], {**crits, "Tribute": 40.0}, 100.0)[1][0]["basis"]
    assert "up to 58 " in low and "not applied: 40 Tribute, under 100" in low


@pytest.mark.parametrize("skill, field", [("Fireball", "IgniteDPS"), ("Spark", "ShockEffectMod")])
def test_spell_elemental_ailment_magnitude_reaches_pob(engine, skill, field):
    """MECH93 probe: the Genesis Tree belt's "35% increased Magnitude of Elemental Ailments you inflict with Spells"
    (PoB: parsed only in part, 0) given as AilmentMagnitude (Ignite + Spell, MatchAll) / EnemyShockMagnitude /
    EnemyChillMagnitude (Spell): a spell's Ignite and Shock rise."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    search.assumptions.apply()
    before = build.call("stats", fields=[field])[field]
    belt = chr(10).join(["Rarity: Rare", "Test Belt", "Heavy Belt", "Item Level: 82", "Implicits: 0",
                         "35% increased Magnitude of Elemental Ailments you inflict with Spells"])
    build.call("equip", slot="Belt", raw=belt, fields=[])
    search.assumptions.apply()
    after = build.call("stats", fields=[field])[field]
    print("PROBE spell ailment magnitude", skill, field, before, after)
    assert after > before * 1.2


def test_the_chilled_condition_counts_your_chilled_ground():
    """MECH93: the boss Chilled = the hits' Chill or your Chilled Ground under it, independent; "Chilled" lines read the
    same; the hit-only share (chill_share) is unchanged."""
    from builder.search import conditions as c

    stats = {"ChillChanceOnHit": 100.0, "Speed": 0.1, "HitChance": 100.0, "ChillDuration": 2.0}
    hits = c.chill_share(stats)
    grounded = {**stats, "_chilled_ground": (0.5, "Creeping Chill")}
    assert c.chill_share(grounded) == pytest.approx(hits)
    assert c.chilled_share(grounded) == pytest.approx(1 - (1 - hits) * 0.5)
    assert c.target_ailment_share(grounded, "chilled") == pytest.approx(c.chilled_share(grounded))
    assert c.chilled_share({"_chilled_ground": (0.4, "")}) == pytest.approx(0.4)  # (no Chill from hits)
