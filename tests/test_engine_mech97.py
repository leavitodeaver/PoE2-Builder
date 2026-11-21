"""MECH97 (MECH-A), through a real engine: Way of the Stonefist's "All damage with Attacks Contributes to Electrocution
Buildup" (models.electrocute_attack_mods: PoB's <Type>CanElectrocute flags on Attacks) and its "N% increased Culling
Strike Threshold if you've dealt a Culling Strike Recently" (the ledger's share line at CulledRecently4s). Needs the
game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

BOW = "Rarity: Rare\nTest Bow\nCrude Bow\nItem Level: 82\nImplicits: 0\nAdds 1 to 10 Cold Damage"
GLOVES = "Rarity: Rare\nTest Gloves\nFists of Stone\nItem Level: 82\nImplicits: 0\n"


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_all_attack_damage_builds_electrocute_on_attacks_only(engine):
    """A bow's Lightning Arrow (an Attack) builds Electrocute from all its damage with the model's flags (PoB alone: none
    from its Cold, as Electrocute's ScalesFrom is empty); a Spell (Spark) is unchanged by them."""
    from builder.search import models

    mods, notes = models.electrocute_attack_mods(["All damage with Attacks Contributes to Electrocution Buildup"])
    assert len(mods) == 5 and notes

    def buildup(skill, with_mods):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("equip", slot="Weapon 1", fields=[], raw=BOW)
        engine.call("addSkillGroup", text=f"{skill} 20/0 1", main=True)
        engine.call("setAssumptions", mods=mods if with_mods else [], fields=[])
        return engine.call("stats", fields=["ElectrocuteBuildupAvg"])["ElectrocuteBuildupAvg"] or 0

    arrow = buildup("Lightning Arrow", False), buildup("Lightning Arrow", True)
    spark = buildup("Spark", False), buildup("Spark", True)
    print("PROBE electrocute", arrow, spark)
    engine.call("setAssumptions", mods=[], fields=[])
    assert arrow[1] > arrow[0]
    assert spark[1] == pytest.approx(spark[0])


def test_the_culled_recently_threshold_counts_at_its_share(engine):
    """The ledger's stand-in on the gloves, "LedgerShare:CulledRecently4s 35% increased Culling Strike Threshold": at
    share 0 PoB's CullMultiplier is as without it; at 100 it's the threshold raised 35% (CalcOffence 4074-4079)."""
    from builder.search import models

    def cull(share, lines=True):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("equip", slot="Weapon 1", fields=[], raw=BOW)
        engine.call("equip", slot="Gloves", fields=[], raw=GLOVES + (
            "LedgerShare:CulledRecently4s 35% increased Culling Strike Threshold" if lines else "+10 to Strength"))
        engine.call("addSkillGroup", text="Lightning Arrow 20/0 1\nCulling Strike I 1/0 1", main=True)
        engine.call("setAssumptions", mods=[models.raw_mod("Multiplier:LedgerShareCulledRecently4s", "BASE", share)],
                    fields=[])
        return engine.call("stats", fields=["CullMultiplier"])["CullMultiplier"]

    plain, off, full = cull(0, False), cull(0), cull(100)
    print("PROBE cull", plain, off, full)
    engine.call("setAssumptions", mods=[], fields=[])
    base = 100 - 100 / plain  # (the enemy's rarity threshold PoB took: CalcOffence 4074)
    assert base > 0
    assert off == pytest.approx(plain, rel=1e-6)
    assert full == pytest.approx(100 / (100 - base * 1.35), rel=1e-3)


def test_lucky_while_surrounded_holds_only_when_pob_counts_you_surrounded(engine):
    """Stonefist's "Attack Damage with Hits is Lucky while you are Surrounded" (models.lucky_surrounded_mods: PoB's
    LuckyHits under Condition:Surrounded, on Attacks): with "Require 4 fewer enemies to be Surrounded" and the boss at
    1 m (inside PoB's 3 m Surrounded radius) the bow's DPS rises; at 5 m, or without the fewer-enemies line, it
    doesn't (PoB's own CalcPerform 525-527)."""
    from builder.search import models

    mods, _ = models.lucky_surrounded_mods(["Attack Damage with Hits is Lucky while you are Surrounded"])

    def hit(with_mods, fewer=True, distance=10):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("equip", slot="Weapon 1", fields=[], raw=BOW)
        engine.call("equip", slot="Gloves", fields=[], raw=GLOVES + (
            "Require 4 fewer enemies to be Surrounded" if fewer else "+10 to Strength"))
        engine.call("addSkillGroup", text="Lightning Arrow 20/0 1", main=True)
        engine.call("setAssumptions", mods=mods if with_mods else [], fields=[])
        return engine.call("setConfig", vars={"enemyDistance": distance}, fields=["TotalDPS"])["stats"]["TotalDPS"]

    plain, lucky = hit(False), hit(True)
    far, alone = hit(True, distance=50), hit(True, fewer=False)
    print("PROBE lucky", plain, lucky, far, alone)
    engine.call("setAssumptions", mods=[], fields=[])
    assert lucky > plain
    assert far == pytest.approx(hit(False, distance=50), rel=1e-6)
    assert alone == pytest.approx(hit(False, fewer=False), rel=1e-6)


def test_the_lowest_resistance_stand_in_is_pobs_flag(engine):
    """The ledger's replacement for Stonefist's lowest-resistance line parses whole as PoB's
    ElementalDamageUsesLowestResistance (CalcOffence 4320)."""
    from builder.ledger import translate

    _, out = translate("Elemental Damage from your Hits is Resisted by the enemy's lowest Elemental Resistance")
    parsed = engine.call("parseLines", lines=out)["results"][0]
    assert parsed["supported"] and [(p["name"], p["type"]) for p in parsed["parsed"]] == [
        ("ElementalDamageUsesLowestResistance", "FLAG")]


def test_the_double_damage_stand_in_doubles_only_under_the_full_life_box(engine):
    """The ledger's replacement for "Deal Double Damage to Enemies that are on Full Life" parses whole under the enemy's
    FullLife; on the gloves, the DPS doubles with conditionEnemyFullLife on and is unchanged with it off."""
    from builder.ledger import translate

    _, out = translate("Deal Double Damage to Enemies that are on Full Life")
    parsed = engine.call("parseLines", lines=out)["results"][0]
    print("PROBE double parse", parsed)
    assert parsed["supported"]

    def dps(lines, box):
        engine.call("newBuild")
        engine.call("setLevel", level=90)
        engine.call("equip", slot="Weapon 1", fields=[], raw=BOW)
        engine.call("equip", slot="Gloves", fields=[], raw=GLOVES + lines)
        engine.call("addSkillGroup", text="Lightning Arrow 20/0 1", main=True)
        engine.call("setAssumptions", mods=[], fields=[])
        return engine.call("setConfig", vars={"conditionEnemyFullLife": box}, fields=["TotalDPS"])["stats"]["TotalDPS"]

    plain_on, plain_off = dps("+10 to Strength", True), dps("+10 to Strength", False)
    with_on, with_off = dps(out[0], True), dps(out[0], False)
    print("PROBE double", plain_on, plain_off, with_on, with_off)
    assert plain_off > 0
    assert plain_on == pytest.approx(plain_off, rel=1e-6)
    assert with_off == pytest.approx(plain_off, rel=1e-6)
    assert with_on == pytest.approx(2 * plain_on, rel=1e-3)


def test_full_life_fields_read_a_hits_chaos_share_and_ailment_chance(engine):
    """The fields Conditions._enemy_full_life reads exist in PoB's output for an Attack: a bow with added Chaos gives
    a Chaos share of the average hit between 0 and 1; the Bleed / Poison chances are numbers."""
    from builder.search import conditions

    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", fields=[], raw=BOW + "\nAdds 5 to 9 Chaos Damage")
    engine.call("addSkillGroup", text="Lightning Arrow 20/0 1", main=True)
    engine.call("setAssumptions", mods=[], fields=[])
    read = engine.call("stats", fields=conditions.FULL_LIFE_FIELDS)
    print("PROBE full life fields", read)
    hit = read.get("AverageHit") or read.get("MainHandAverageHit")
    chaos = read.get("ChaosHitAverage") or read.get("MainHandChaosHitAverage")
    assert hit and chaos and 0 < chaos / hit < 1
    assert read.get("Speed", 0) > 0
