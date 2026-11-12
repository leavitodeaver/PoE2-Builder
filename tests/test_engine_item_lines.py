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
    line = spike.replace[0].split(" ", 1)[1].replace("{g1*0.4}", "8")
    parsed = engine.call("parseLines", lines=[line])["results"][0]
    assert parsed["supported"] and [(p["name"], p["type"], p["value"]) for p in parsed["parsed"]] == [
        ("EnemyModifier", "LIST", "DamageTaken")]
