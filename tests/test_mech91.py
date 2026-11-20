"""MECH91 (MECH-A): Saitha's Spear's conditional "is Aggravated" lines (the Shocked share; the other extreme as a row
for a condition not modelled); the Volatility cycle's choice weighing Growing Peril."""
import pytest


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


SAITHA = ["Aggravating any Bleeding with this Weapon also Aggravates all Ignites on the target",
          "30% chance to Aggravate Bleeding on Hit"]
STATS = {"Speed": 3.0, "HitChance": 100.0, "BleedChancePerHit": 20.0, "IgniteDuration": 4.0, "IgniteDPS": 100.0,
         "IgniteChancePerHit": 10.0}


def test_saitha_on_shocked_enemies_takes_the_shocked_share():
    from builder.search import models

    shocked = models.saitha_ignite_mods(SAITHA + ["Bleeding you inflict on Shocked enemies is Aggravated"], STATS,
                                        "Spear Throw", True, {"Shocked": 0.5})
    half = models.saitha_ignite_mods(SAITHA, {**STATS, "BleedChancePerHit": 10.0}, "Spear Throw", True)
    assert shocked[0][0]["scale"] == pytest.approx(half[0][0]["scale"])
    assert "Shocked 50%" in shocked[1][0]["basis"] and "Row (the other extreme" not in shocked[1][0]["basis"]


def test_saitha_unmodelled_condition_shows_both_readings():
    """The brief (MECH91): a condition the tool doesn't model takes 0 (the higher reading, the spear on every Bleed) and
    the note carries the other extreme (always on: the line never fires, x1.00)."""
    from builder.search import models

    lines = SAITHA + ["Bleeding you inflict on Blinded enemies is Aggravated"]
    mods, notes = models.saitha_ignite_mods(lines, STATS, "Spear Throw", True, {"Cursed": 1.0})
    plain = models.saitha_ignite_mods(SAITHA, STATS, "Spear Throw", True)
    assert mods[0]["scale"] == pytest.approx(plain[0][0]["scale"])
    assert "not modelled: 0, UNVERIFIED" in notes[0]["basis"]
    assert "Row (the other extreme" in notes[0]["basis"] and notes[0]["basis"].endswith("x1.00")
    assert "x1.00 if the boss is always" in notes[0]["play"]


def test_growing_peril_weighs_in_the_volatility_cycles_choice():
    """The brief (MECH91): Growing Peril on the Volatility held, weighed by the build's Chaos DoT share, can keep the
    build attacking without a break (the cap held all fight) where Volatile Power alone would play the cycle."""
    from builder.search import models

    lines = ["Gain 1 Volatility on inflicting an Elemental Ailment", "Take no Damage from Volatility",
             "1% increased Chaos Damage over Time per Volatility"]
    fast = {"Speed": 10.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0}
    # no Chaos DoT: the cycle as before (attack 20 s, x1.67)
    assert "attack 20 s" in models.volatility_model(lines, {**fast, "CombinedDPS": 100.0})[1][0]["basis"]
    # all Poison at a 4 s delay: the cycle still pays (attack 30 s: (10 x 3 + 20) / 34 x (1 + 133 / 100) = 3.43 > 3.0)
    poison = {**fast, "CombinedDPS": 100.0, "PoisonDPS": 100.0}
    assert models.chaos_dot_share(poison) == pytest.approx(1.0)
    assert "attack 30 s" in models.volatility_model(lines, poison)[1][0]["basis"]
    # a 12 s delay: Volatile Power alone still plays it (attack 20 s: (10 x 3 + 10) / 32 = 1.25), but with all Poison
    # 200 held all fight (x3.0) beats the cycle's best (under 2.8): the choice flips
    lines.append("200% increased Volatility Explosion delay")
    assert "attack 20 s" in models.volatility_model(lines, {**fast, "CombinedDPS": 100.0})[1][0]["basis"]
    mods, notes = models.volatility_model(lines, poison)
    assert {m["line"]: m["scale"] for m in mods} == {"1% increased Chaos Damage over Time": 200.0}
    assert "not played" in notes[0]["value"] and "100% of its damage" in notes[0]["basis"]
    # the skill's own DoT counts by its Chaos part: half of a 40 TotalDotDPS of 100 is 20%
    assert models.chaos_dot_share({"CombinedDPS": 100.0, "TotalDotDPS": 40.0, "ChaosDot": 5.0,
                                   "FireDot": 5.0}) == pytest.approx(0.2)
    assert models.chaos_dot_share({}) == 0.0


def test_volatility_on_critical_hit_without_inner_turmoil():
    """MECH91: the Volatility support's Volatility on Critical Hit plays the cycle too; without "Take no Damage from
    Volatility" the explosion (100 Physical a Volatility) is capped at what Life + Energy Shield survives."""
    from builder.search import models

    crits = {"Speed": 10.0, "HitChance": 100.0, "CritChance": 50.0, "Life": 5001.0, "EnergyShield": 0.0}
    # 5 a second (10 hits x 50% crit); cap 50 (5000 / 100): attack 10 s, 50 explode: (10 x 1.5) / 14 = 1.07
    mods, notes = models.volatility_model([], crits, support_crit=100.0)
    assert {m["line"]: m["scale"] for m in mods}["Gain 1% of Damage as Extra Chaos Damage"] == pytest.approx(50.0)
    assert "attack 10 s" in notes[0]["basis"] and "5000 Physical Damage to you" in notes[0]["basis"]
    # the same with Inner Turmoil's immunity: up to 200 (attack 30 s at 5 a second: 150; (10 x 2.5 + 20) / 34 = 1.32)
    immune = models.volatility_model(["Take no Damage from Volatility"], crits, support_crit=100.0)
    assert "attack 30 s" in immune[1][0]["basis"]
    # the unique's line alone, 40%: 2 a second
    line = models.volatility_model(["40% chance to grant Volatility on Critical Hit", "Take no Damage from Volatility"],
                                   crits)
    assert "2.00 Volatility a second" in line[1][0]["basis"]
    # no crit source and no Inner Turmoil: nothing
    assert models.volatility_model(["Take no Damage from Volatility"], crits) == ([], [])


def test_poison_duration_after_consuming_a_frenzy_charge():
    """MECH91 (PoB parses the line only in part): N% x the share of the fight a Frenzy Charge was consumed Recently."""
    from builder.search import models

    lines = ["35% increased Duration of Poisons you inflict when you've consumed a Frenzy Charge Recently"]
    mods, notes = models.frenzy_poison_duration_mods(lines, (0.125, "Bladestorm 0.13/s"))  # one every 8 s: half
    assert mods == [models.line("1% increased Poison Duration", 17.5)]
    assert models.frenzy_poison_duration_mods(lines, (1.0, "x"))[0][0]["scale"] == pytest.approx(35.0)
    assert models.frenzy_poison_duration_mods(lines, None)[0] == []
    assert models.frenzy_poison_duration_mods([], (1.0, "x")) == ([], [])


def test_volatility_taken_as_cold_raises_the_survival_cap():
    """Dream Fragments' "75% of Volatility Physical Damage Taken as Cold Damage" at 75% Cold resistance: 25 + 75 x 0.25
    = 43.75 a Volatility, so 5000 Life survives 114 (was 50)."""
    from builder.search import models

    crits = {"Speed": 10.0, "HitChance": 100.0, "CritChance": 50.0, "Life": 5001.0, "ColdResist": 75.0}
    notes = models.volatility_model(["75% of Volatility Physical Damage Taken as Cold Damage"], crits, 100.0)[1]
    assert "up to 114" in notes[0]["basis"] and "44 Damage to you" in notes[0]["basis"]


def test_the_volatility_supports_chance_is_read_on_a_real_build(engine):
    """MECH91 probe: the Volatility support in Fireball's group gives instability_on_critical_%_chance 100 through the
    bridge's supportStats (assumptions.volatility_support), and the model plays on PoB's own stats."""
    from types import SimpleNamespace

    from builder.request.schema import BuildRequest
    from builder.search import assumptions, models
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball"))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    main = SimpleNamespace(name="Fireball")
    assert assumptions.volatility_support(build, main) == 0.0
    group = build.main_group()
    gems = [{"name": g["name"], "level": g.get("level", 20), "quality": g.get("quality", 0)} for g in group["gems"]]
    build.set_group(index=group["index"], gems=gems + [{"name": "Volatility", "level": 1, "quality": 0}])
    search.settle()
    chance = assumptions.volatility_support(build, main)
    stats = build.call("stats", fields=models.VOLATILITY_STATS)
    mods, notes = models.volatility_model([], stats, chance)
    print("PROBE Volatility support", chance, {k: stats.get(k) for k in ("CritChance", "Speed", "Life")},
          [(m["line"], round(m["scale"], 1)) for m in mods], notes[0]["value"] if notes else None)
    assert chance == 100
    assert notes and "Volatility on Critical Hit" in notes[0]["basis"]
