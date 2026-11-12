"""Item lines (COV-A) whose reading depends on how PoB applies them, through a real engine: a charm's "during effect"
line and Maligaro's Virtuosity's crit lines. Boots one PoB engine for the module and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

HIT, DOT = "LightningTakenHitMult", "LightningTakenDotMult"
BELT = "\n".join(["Rarity: Rare", "Test Belt", "Plate Belt", "Item Level: 82", "Implicits: 2", "+160 to Armour",
                  "Has 3 Charm Slot"])
FLASK = "Rarity: Normal\nUltimate Life Flask"


@pytest.fixture(scope="module")
def engine():
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


def test_the_black_cats_unlucky_line_follows_the_charm_not_a_flask_and_cuts_hits_only(engine):
    """unlucky-enemy-lightning-charm: the stand-in drops "during effect" (PoB's tag for it is UsingFlask, which only a
    Life or Mana flask sets); a charm's lines join the build only while its slot is active. So the reduction is on with
    the charm on whether or not a flask is up, off with the charm switched off, and on Lightning Hits only (A10-2)."""
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    cat = next(u for u in ctx.data.uniques if u["name"] == "The Black Cat")
    corrected = ctx.corrected_raw(cat["name"], cat["raw"]).split("\n")
    assert "7% less Lightning Damage taken when Hit" in corrected  # one line: a charm keeps the larger of equal mods
    assert any(r["parsed"][0]["tags"] == [] and r["parsed"][0]["name"] == "LightningDamageTakenWhenHit"
               and r["parsed"][0]["value"] == -7
               for r in engine.call("parseLines", lines=["7% less Lightning Damage taken when Hit"])["results"])

    corrected = [line for line in corrected if "Unlucky" not in line]
    engine.call("newBuild")
    engine.call("equip", slot="Belt", raw=BELT, fields=[])
    engine.call("equip", slot="Flask 1", raw=None, active=False, fields=[])
    bare = engine.call("equip", slot="Charm 1", raw=None, active=False, fields=[HIT, DOT])["stats"]
    on = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=True, fields=[HIT, DOT])["stats"]
    assert on[HIT] == pytest.approx(bare[HIT] * 0.93, rel=1e-3)  # no flask up: still counted
    assert on[DOT] == pytest.approx(bare[DOT])  # Unlucky changes Hits only
    off = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=False, fields=[HIT])["stats"]
    assert off[HIT] == pytest.approx(bare[HIT])  # a charm switched off adds nothing
    engine.call("equip", slot="Flask 1", raw=FLASK, active=True, fields=[])
    with_flask = engine.call("equip", slot="Charm 1", raw="\n".join(corrected), active=True, fields=[HIT])["stats"]
    assert with_flask[HIT] == pytest.approx(on[HIT], rel=1e-3)  # and not counted twice with a flask up


def test_maligaros_critical_damage_bonus_is_a_bonus_override_never_crit_chance(engine):
    """COVC13's note: PoB's mod cache holds Maligaro's two lines joined as "Critical Hit Chance set to 250" (Item.lua
    1240-1251 joins a line it can't parse with the next one), but that reading keeps leftover text, so PoB never applies
    it (Item.lua: `not modLine.extra`). Alone, "Your Critical Damage Bonus is 250%" is CritMultiplier OVERRIDE 250 (a
    x3.5 crit multiplier). Checked on the whole glove: with the line, crit chance is what it is without it."""
    parsed = engine.call("parseLines", lines=["Your Critical Damage Bonus is 250%"])["results"][0]["parsed"]
    assert [(p["name"], p["type"], p["value"]) for p in parsed] == [("CritMultiplier", "OVERRIDE", 250)]
    payload = make_payload(BuildRequest(main_skill="Sunder"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    gloves = next(u for u in ctx.data.uniques if u["name"] == "Maligaro's Virtuosity")
    raw = ctx.corrected_raw(gloves["name"], gloves["raw"])
    bonus = "Your Critical Damage Bonus is 250%"
    lines = raw.split("\n")
    assert bonus in lines
    without = "\n".join(line for line in lines if line != bonus)
    fields = ["CritChance", "CritMultiplier"]
    alone = search.build.call("equip", slot="Gloves", raw=without, fields=fields)["stats"]
    worn = search.build.call("equip", slot="Gloves", raw=raw, fields=fields)["stats"]
    assert worn["CritMultiplier"] == pytest.approx(3.5)
    assert alone["CritMultiplier"] != pytest.approx(3.5)
    assert worn["CritChance"] == pytest.approx(alone["CritChance"], rel=1e-3) and worn["CritChance"] < 100


def test_spike_pit_is_increased_damage_taken_on_the_boss_not_more_damage(engine):
    """spike-pit: the notable's "Enemies in Jagged Ground you create take N% increased Damage" is a debuff on the enemy
    that adds to its other increased Damage taken (Shock and the like), not a separate "more" multiplier (FIX_QUEUE,
    MECH51's findings: a small overcount)."""
    from builder.ledger import entries

    spike = next(e for e in entries() if e.id == "spike-pit")
    line = spike.replace[0].split(" ", 1)[1].replace("{g1}", "8")
    parsed = engine.call("parseLines", lines=[line])["results"][0]
    assert parsed["supported"] and [(p["name"], p["type"], p["value"]) for p in parsed["parsed"]] == [
        ("EnemyModifier", "LIST", "DamageTaken")]


def test_a_share_line_counts_its_value_times_the_share_the_assumptions_set(engine):
    """The ledger's `share:` lines (COV11; spike-pit and aggravate-bleed-jagged-ground): the bridge counts a passive's
    line at value / 100 x Multiplier:LedgerShare<Name>, which the assumptions set to the share in whole percent. So the
    nested EnemyModifier line (Spike Pit's) and a plain line both scale linearly with the share: 0 adds nothing, 100
    the full value."""
    from builder.ledger import entries

    spike = next(e for e in entries() if e.id == "spike-pit")
    assert list(spike.replace) == ["share:JaggedGround Nearby Enemies have {g1}% increased Damage taken"]
    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    node = 3698  # Spike Pit, with its path (the search's own ledger correction, spike-pit's share line, rides along)
    build.call("allocate", add=[node], fields=[])
    lines = ["share:JaggedGround Nearby Enemies have 40% increased Damage taken", "share:JaggedGround 20% more Damage",
             "share:JaggedGround 30% increased maximum Life"]
    applied = build.call("applyPassiveCorrections", nodes=[{"id": node, "entry": "test-share", "lines": lines}])
    assert applied["applied"] == 3 and not applied["failed"]
    base = list(search.assumptions.mods)

    def stats(percent):
        share = [{"kind": "Raw", "name": "Multiplier:LedgerShareJaggedGround", "type": "BASE", "value": percent}]
        return build.call("setAssumptions", mods=base + share, fields=["TotalDPS", "Life"])["stats"]

    def dps(percent):  # hit DPS (TotalDPS): an Ignite's chance grows with the hit (Flammability), so not linear
        return stats(percent)["TotalDPS"]

    life = [stats(p)["Life"] for p in (0, 50, 100)]  # a plain INC line: exactly linear in the share
    assert life[2] > life[0] and life[1] == pytest.approx((life[0] + life[2]) / 2, abs=1)
    none, half, full = dps(0), dps(50), dps(100)
    assert build.call("setAssumptions", mods=base, fields=["TotalDPS"])["stats"]["TotalDPS"] == pytest.approx(none)
    assert full > none * 1.2  # at least the 20% more, and some of the 40% increased taken
    # (1 + a + c s) x (1 + 0.2 s) / (1 + a), c = 0.4 + Spike Pit's own 0.1: at s = 1/2 the product of the halves
    assert half == pytest.approx(none * ((full / none / 1.2 - 1) / 2 + 1) * 1.1, rel=5e-3)  # (Shock's size follows the hit too)


def test_the_coverage_audit_reads_a_two_line_stat_description_as_one_line_as_pob_does_on_an_item(engine):
    """auditCoverage (COV11; PLANS1's catalog finding): a mod whose stat description spans two lines is one line PoB
    counts on an item (Item.lua joins a line it can't parse with the next), so the audit records the joined line, not
    two halves it can't parse. Waveshaper's (UniqueManaRegenAppliesToRecharge1) is the example."""
    engine.call("newBuild")
    affixes = engine.call("auditCoverage")["affixes"]
    wave = [a for a in affixes if a["id"] == "UniqueManaRegenAppliesToRecharge1"]
    assert [(a["line"], a["supported"]) for a in wave] == [
        ("Increases and Reductions to Mana Regeneration Rate also apply to Energy Shield Recharge Rate", True)]
    assert "ManaRegenAppliesToEnergyShieldRecharge" in wave[0]["names"]
