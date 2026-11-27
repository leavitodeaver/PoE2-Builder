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


def test_chilled_ground_counts_creeping_chill_where_it_is_made(monkeypatch):
    # (MECH114: the boss standing still in the ground, the reading this test pins; the walk-out: test_mech114.py)
    monkeypatch.setattr("builder.search.conditions.ground_stay", lambda *a, **k: (1.0, 1.0, ""))
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


def test_mecha_readers_take_the_main_skill_at_its_share_of_the_fight():
    """MECH93 (the manager 07:25; MECHC45's list, an OVERCOUNT for builds pressing side skills): with
    stats["_main_share"] 0.5 each MECH-A reader equals itself at half PoB's Speed (so a reader inside another, or a
    MECH-C reader, doesn't scale twice); without a share, nothing changes."""
    from builder.search import conditions as c
    from builder.search import models

    base = {"Speed": 0.2, "HitChance": 100.0, "CritChance": 20.0, "IgniteChancePerHit": 30.0, "ShockChancePerHit": 25.0,
            "PoisonChancePerHit": 40.0, "ChillChanceOnHit": 100.0, "FreezeBuildupAvg": 8.0, "ElectrocuteBuildupAvg": 5.0,
            "ColdHitAverage": 100.0}
    half = {**base, "Speed": 0.1}
    shared = {**base, "_main_share": 0.5}
    assert models.at_main_share(base) is base
    assert models.at_main_share(shared)["Speed"] == pytest.approx(0.1) and models.at_main_share(shared)["_main_share"] is None
    for ailment in ("Ignite", "Poison", "Shock"):
        assert c.ailment_uptime(shared, ailment) == pytest.approx(c.ailment_uptime(half, ailment))
        assert c.ailment_uptime(shared, ailment) < c.ailment_uptime(base, ailment)
        assert c.applied_recently(shared, ailment)[0] == pytest.approx(c.applied_recently(half, ailment)[0])
    assert c.shock_share(shared) == pytest.approx(c.shock_share(half))
    assert c.chill_share(shared) == pytest.approx(c.chill_share(half))
    assert c.chilled_share(shared) == pytest.approx(c.chilled_share(half))
    for kind in ("hit", "crit", "ignite", "shock"):
        assert c._exposure_events(kind, shared)[0] == pytest.approx(c._exposure_events(kind, half)[0])
    for ailment in ("Freeze", "Electrocute"):
        assert models.immobilise_period(shared, ailment) == pytest.approx(models.immobilise_period(half, ailment))
        assert models.immobilised_share(shared, ailment)[0] == pytest.approx(models.immobilised_share(half, ailment)[0])
        assert models.buildup_rate(shared, ailment) == pytest.approx(models.buildup_rate(half, ailment))
    # volatility_model: Inner Turmoil's gains at the main skill's hits, at its share
    turmoil = ["Gain 1 Volatility on inflicting an Elemental Ailment", "Take no Damage from Volatility"]
    got = models.volatility_model(turmoil, {**base, "_main_share": 0.5})[1][0]["basis"]
    assert got == models.volatility_model(turmoil, half)[1][0]["basis"]


def test_repulsion_is_on_the_boss_only_until_the_next_hit():
    """MECH93 (GEM26's two OVERCOUNTS; R35-25): any Hit removes Repulsion, so each wave holds (1 - e^(-hD)) / h;
    cast by hand a wave every hold + 0.5 s cast + 1.5 s delay; no Runic Ward: never cast; under Blasphemy the 1 s block
    + the delay; another curse unchanged."""
    import math

    from builder.search import models

    gems = {"Repulsion": {"skillTypes": ["Spell", "AppliesCurse"], "castTime": 0.5},
            "Vulnerability": {"skillTypes": ["Spell", "AppliesCurse"]},
            "Blasphemy": {"skillTypes": ["Aura", "Meta"]}}
    hand = [{"enabled": True, "gems": [{"name": "Repulsion"}]}, {"enabled": True, "gems": [{"name": "Vulnerability"}]}]
    stats = {"Speed": 2.0, "HitChance": 100.0, "Ward": 500.0, "PresenceRadius": 40}
    got = {u.name: u for u in models.curse_uses(hand, gems, stats, 2.0)}
    d = models.CURSE_DURATION
    hold = (1 - math.exp(-2.0 * d)) / 2.0
    assert got["Repulsion"].share == pytest.approx(hold / (hold + 0.5 + 1.5))
    assert got["Repulsion"].by_hand and got["Repulsion"].duration == pytest.approx(hold + 2.0)
    assert got["Vulnerability"].share == 1.0
    # pressed half the fight: half the hits, a longer hold
    half = {u.name: u for u in models.curse_uses(hand, gems, {**stats, "_main_share": 0.5}, 2.0)}
    assert half["Repulsion"].share > got["Repulsion"].share
    # no Runic Ward: never cast
    none = {u.name: u for u in models.curse_uses(hand, gems, {**stats, "Ward": 0.0}, 2.0)}
    assert none["Repulsion"].share == 0.0 and not none["Repulsion"].by_hand and "Runic Ward" in none["Repulsion"].how
    # under Blasphemy, inside the Presence: reapplied after the block and the delay, no Ward needed
    aura = [{"enabled": True, "gems": [{"name": "Repulsion"}, {"name": "Blasphemy", "support": True}]}]
    up = models.curse_uses(aura, gems, {**stats, "Ward": 0.0}, 2.0)[0]
    assert up.share == pytest.approx(hold / (hold + 1.0 + 1.5)) and not up.by_hand
    # no hits read: the wave lasts its duration
    idle = {u.name: u for u in models.curse_uses(hand, gems, {**stats, "Speed": 0.0}, 2.0)}
    assert idle["Repulsion"].share == pytest.approx(d / (d + 2.0))


def test_the_stun_rates_take_the_main_skill_at_its_share_of_the_fight():
    """MECH93 (the manager 08:10: the rest of the full-Speed check): the boss's Heavy and Light Stuns from the main
    skill's hits at its share of the fight (as the other readers, fb0acb80)."""
    from builder.search import models

    base = {"Speed": 0.5, "HitChance": 100.0, "CritChance": 10.0, "HeavyStunBuildupAvg": 10.0,
            "PhysicalHitAverage": 2000.0, "PhysicalCritAverage": 3000.0, "_main_types": {"Melee", "Attack"}}
    half, shared = {**base, "Speed": 0.25}, {**base, "_main_share": 0.5}
    assert models.heavy_stun_rate(shared)[0] == pytest.approx(models.heavy_stun_rate(half)[0])
    assert models.heavy_stun_rate(shared)[0] < models.heavy_stun_rate(base)[0]
    assert models.light_stun_rate(shared, True)[0] == pytest.approx(models.light_stun_rate(half, True)[0])
