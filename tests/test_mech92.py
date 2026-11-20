"""MECH92 (MECH-A): Instability's "regain an equivalent amount of Volatility" and Volatility on Kill at the build's own
kills (PoB reads neither line)."""
import pytest

TURMOIL = ["Gain 1 Volatility on inflicting an Elemental Ailment", "Take no Damage from Volatility"]
INSTABILITY = "25% chance that when Volatility on you explodes, you regain an equivalent amount of Volatility"
FOUR = {"Speed": 4.0, "HitChance": 100.0, "IgniteChancePerHit": 100.0}
CHAOS = "Gain 1% of Damage as Extra Chaos Damage"


def test_volatility_refreshed_expected_count():
    from builder.search import models

    # no cap reached: 10 a window, regained 25%: 10 / (1 - 0.25)
    assert models.volatility_refreshed(10.0, 200.0, 0.25) == pytest.approx(10.0 / 0.75)
    # the cap after one regain: half the time 150, half the time (a regain first) 200
    assert models.volatility_refreshed(150.0, 200.0, 0.5) == pytest.approx(175.0)
    # no Instability: as before, min(cap, per window)
    assert models.volatility_refreshed(250.0, 200.0, 0.0) == pytest.approx(200.0)


def test_instability_raises_the_cycles_exploded_count():
    """4 Volatility a second: attack 30 s, 120 explode (x1.235); with Instability 25% a cycle explodes 0.75 x 120 +
    0.25 x 200 = 140 on average (x1.294), so +140 x 10 / 30 % of Damage as Chaos while attacking (40 before)."""
    from builder.search import models

    plain = {m["line"]: m["scale"] for m in models.volatility_model(TURMOIL, FOUR)[0]}
    mods, notes = models.volatility_model(TURMOIL + [INSTABILITY], FOUR)
    got = {m["line"]: m["scale"] for m in mods}
    assert plain[CHAOS] == pytest.approx(40.0)
    assert got[CHAOS] == pytest.approx(140.0 / 3, rel=1e-3)
    assert "Instability: 25%" in notes[0]["basis"] and "attack 30 s" in notes[0]["basis"]
    # Ancient Rune of Decay's 15% sums with it (40%)
    rune = "15% chance that when Volatility on you explodes, you regain an equivalent amount of Volatility"
    assert "Instability: 40%" in models.volatility_model(TURMOIL + [INSTABILITY, rune], FOUR)[1][0]["basis"]


def test_volatility_on_kill_alone_at_the_builds_kills():
    """Volatility on Kill without Inner Turmoil or crits: each kill's Volatility explodes by itself for 10 s of Volatile
    Power (1% of Damage as Chaos); 0 with no kills (a lone boss), said in a note."""
    from builder.search import models

    kills = (0.04, 0.04, "the King's adds")
    mods, notes = models.volatility_model(["10% chance to gain Volatility on Kill"], {}, kills=kills)
    assert {m["line"]: m["scale"] for m in mods} == {CHAOS: pytest.approx(0.04)}  # (0.004 a second x 10 s)
    assert notes[0]["label"] == "Volatility on Kill: Volatile Power*" and "the King's adds" in notes[0]["basis"]
    # Instability: 1 / 0.75 explosions a gain, the buff held 10 + 4 x 1/3 s
    mods = models.volatility_model(["10% chance to gain Volatility on Kill", INSTABILITY], {}, kills=kills)[0]
    assert mods[0]["scale"] == pytest.approx(0.004 * (10 + 4 / 3), rel=1e-3)
    # no kills: nothing, with a note
    mods, notes = models.volatility_model(["10% chance to gain Volatility on Kill"], {},
                                          kills=(0.0, 0.0, "a lone boss"))
    assert mods == [] and notes[0]["value"] == "0 (no kills)"
    # no Volatility line at all: nothing
    assert models.volatility_model([INSTABILITY], {}, kills=kills) == ([], [])


def test_volatility_on_kill_joins_the_cycles_rate():
    from builder.search import models

    kills = (0.5, 0.5, "test adds")
    notes = models.volatility_model(TURMOIL + ["10% chance to gain Volatility on Kill"], FOUR, kills=kills)[1]
    assert "4.05 Volatility a second" in notes[0]["basis"] and "Volatility on Kill (10% x 0.500 Kills/s" in notes[0][
        "basis"]


def test_debuffs_on_immobilise_and_after_unpinned(monkeypatch):
    """MECH92: Fearful Paralysis' Intimidate "when you Immobilise them" at the main skill's Freezes, Electrocutes, Pins
    and Heavy Stuns, and Lockdown's Maim "after becoming Unpinned" at its Pins (both 0 before)."""
    from builder.search import conditions, models

    asked = []

    def rate(stats, events):
        asked.append(set(events))
        return (0.1 if events == {"pin"} else 0.2), "test"

    monkeypatch.setattr(models, "immobilise_rate", rate)
    up, how = conditions._debuff_source("Enemies are Intimidated for 4 seconds when you Immobilise them",
                                        "conditionEnemyIntimidated", [], {})
    assert up == pytest.approx(0.8) and asked[-1] == {"freeze", "electrocute", "pin", "stun"} and "Immobilised" in how
    up, how = conditions._debuff_source("Enemies are Maimed for 4 seconds after becoming Unpinned",
                                        "conditionEnemyMaimed", [], {})
    assert up == pytest.approx(0.4) and asked[-1] == {"pin"} and "Pins ending" in how
