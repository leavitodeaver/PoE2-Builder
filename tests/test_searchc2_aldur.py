"""SEARCHC2: the Aldur runes (Passion / Breath / Ire / Betrayal of Aldur) transform a rare weapon's elemental damage
modifiers, tier for tier (poe2db's 78-row table); PoB ignores the rune's line, so the search rewrites the lines."""
from __future__ import annotations

import faulthandler
import random

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.rares import (
    ALDUR_SET, Affix, AldurConverter, aldur_line, aldur_weapon, mod_lines, rare_text, roll, socket_count,
)


def test_aldur_lines_follow_poe2dbs_table():
    # added damage: per-element numbers, row for row (row 46 one-hand, row 56 two-hand, row 55 the top one-hand)
    assert aldur_line("Adds (1-2) to (3-5) Fire Damage", "Lightning") == "Adds 1 to (4-6) Lightning Damage"
    assert aldur_line("Adds 1 to (7-10) Lightning Damage", "Cold") == "Adds (2-3) to (4-6) Cold Damage"
    assert aldur_line("Adds (72-81) to (110-123) Cold Damage", "Chaos") == "Adds (83-101) to (137-157) Chaos Damage"
    assert aldur_line("Allies in your Presence deal (1-2) to (3-4) added Attack Fire Damage", "Lightning") == \
        "Allies in your Presence deal 1 to (5-7) added Attack Lightning Damage"
    # the same numbers, the element word changed
    assert aldur_line("(105-119)% increased Cold Damage", "Fire") == "(105-119)% increased Fire Damage"
    assert aldur_line("+(3-4) to Level of all Fire Spell Skills", "Lightning") == \
        "+(3-4) to Level of all Lightning Spell Skills"
    assert aldur_line("Gain (13-15)% of Damage as Extra Fire Damage", "Chaos") == \
        "Gain (13-15)% of Damage as Extra Chaos Damage"
    # the ailment suffixes: per-element numbers; no Chaos column (not converted: None, no guess)
    assert aldur_line("(31-40)% increased Freeze Buildup", "Fire") == "(51-60)% increased Flammability Magnitude"
    assert aldur_line("(51-60)% increased chance to Shock", "Chaos") is None
    # left alone: the target's own element, resistances, lines with no element
    assert aldur_line("Adds (1-2) to (3-5) Fire Damage", "Fire") == "Adds (1-2) to (3-5) Fire Damage"
    assert aldur_line("+(31-35)% to Fire Resistance", "Cold") == "+(31-35)% to Fire Resistance"
    assert aldur_line("(10-15)% increased Attack Speed", "Cold") == "(10-15)% increased Attack Speed"
    # an elemental damage line with no row (numbers off the table): None
    assert aldur_line("Adds (1-2) to (3-6) Fire Damage", "Cold") is None


@pytest.fixture(scope="module")
def data() -> GameData:
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")


WEAPON_TAGS = {"wand", "staff", "sceptre", "bow", "two_hand_weapon", "one_hand_weapon", "sword", "axe", "mace", "spear",
               "claw", "dagger", "flail", "crossbow", "talisman"}


def test_every_weapon_elemental_mod_has_a_row(data):
    """Every elemental line a weapon can roll (spawn weight > 0 on a weapon tag) converts under every rune, except the
    three ailment suffixes under Betrayal (poe2db's Chaos column is blank)."""
    missing = []
    for mod in data.mods["Item"]:
        weights = dict(zip(mod.get("weightKey") or [], mod.get("weightVal") or []))
        if mod.get("type") not in ("Prefix", "Suffix") or not any(weights.get(t, 0) > 0 for t in WEAPON_TAGS):
            continue
        for line in mod_lines(mod):
            for target in ("Fire", "Cold", "Lightning", "Chaos"):
                if aldur_line(line, target) is None:
                    missing.append((mod["id"], target))
    assert missing and all(t == "Chaos" and i.startswith(("Ignite", "Freeze", "Shock")) for i, t in missing), missing


def test_the_converter_rolls_at_the_pools_fraction_and_marks_the_id(data):
    class Ctx:
        pass
    ctx = Ctx()
    ctx.data = data
    mod = next(m for m in data.mods["Item"] if m["id"] == "LocalAddedFireDamage1")
    affix = Affix(mod["id"], "g", "Prefix", 1, tuple(roll(x, 0.5) for x in mod_lines(mod)))
    converter = AldurConverter(ctx, 0.5)
    out = converter.convert(affix, "Lightning")
    assert out.lines == (roll("Adds 1 to (4-6) Lightning Damage", 0.5),) and out.id.endswith("+Ire")
    assert converter.convert(affix, "Fire") is affix


def test_the_aldur_runes_are_never_a_plain_socket_pick(data):
    from builder.search.rares import RuneCatalog
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    catalog = RuneCatalog(ctx, {r["name"]: 0.01 for r in ctx.data.runes})
    wand = next(b for b in ctx.data.bases if b["type"] == "Wand")
    assert aldur_weapon(wand) and not aldur_weapon(next(b for b in ctx.data.bases if b["type"] == "Focus"))
    names = catalog.candidates(wand, False)
    assert names and not set(names) & ALDUR_SET


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_pob_ignores_the_rune_line_and_scores_the_rewritten_weapon(engine, monkeypatch):
    """The probe: a Spark (lightning spell) build's wand with fire lines. PoB scores the wand the same with or without
    Ire of Aldur socketed (it ignores the transform), and higher once the lines are rewritten to lightning; then the
    route (switched on) on a fresh build picks a rune and binds it in the socket."""
    from builder.search.gear import Gear, SlotSpec, affix_limits
    from builder.search.objective import Objective
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Spark 20/0  1", main=True, fullDPS=True)  # (a build with Spark's damage)
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and socket_count(b, False) >= 1)
    level = gear.pools.item_level
    fire = [a for a in gear.pools.pool(base["name"]) if any("Fire Spell Skills" in x or "increased Fire Damage" in x
                                                            for x in a.lines)]
    assert fire, "a wand rolls fire spell levels / fire damage"
    pick = []
    for a in sorted(fire, key=lambda a: a.tier):
        if a.group not in {p.group for p in pick} and len(pick) < 2:
            pick.append(a)
    converter = AldurConverter(ctx, gear.pools.roll)
    converted = [converter.convert(a, "Lightning") for a in pick]
    plain, with_rune, rewritten = (s for s, _ in gear.deltas("Weapon 1", [
        rare_text("Weapon 1", base, level, pick), rare_text("Weapon 1", base, level, pick, runes=["Ire of Aldur"]),
        rare_text("Weapon 1", base, level, converted, runes=["Ire of Aldur"])]))
    print("PROBE aldur", [a.lines for a in pick], [a.lines for a in converted], plain, with_rune, rewritten)
    assert abs(plain - with_rune) < 1e-6, "PoB ignores the transform line (and the inactive Bonded line)"
    assert rewritten > plain + 1e-6, "the rewritten lightning lines score for Spark"

    monkeypatch.setenv("POE2_SEARCH_ALDUR", "1")
    option = ItemOption(base["name"], base["name"], "Wand", None, normal_item(base), False, 0.0, "found", 1)
    current = gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, [])])[0][0]
    shortlist = sorted(gear.pools.pool(base["name"]), key=lambda a: a.tier)[:12]
    out = gear.aldur_trial("Weapon 1", base, option, level, gear.pools, affix_limits(base), shortlist, [], current,
                           1e6, clock.now() + 300)
    print("PROBE trial", out and out.runes, out and out.affixes, out and out.price, out and out.craft_route)
    if out is None:
        pytest.skip("the route didn't beat a plain (empty) rare on a fresh Spark build")
    assert out.bound == out.runes and out.runes[0] in ALDUR_SET
    assert out.craft_route and out.craft_route[0].startswith(out.runes[0])


def test_the_idol_unlock_is_read_from_the_items_own_lines(data):
    """R52's 20 builds: Morior Invictus / Hyrri's Ire with Idols whose Bonded lines count. Neither unique carries the line
    itself (PoB Uniques/body.lua; game Mods: local_idols_gain_additional_socketable_mods is on no Mods row, SoulCoreStats
    row 235 alone, the Fox Idol's body row), so it comes from a socketed Fox Idol; an export may list it as an item line:
    the display reads the line wherever it is."""
    from builder.report import bonded
    base = bonded.base_of("Rarity: UNIQUE\nMorior Invictus\nGrand Regalia", data)
    assert base and base["type"] == "Body Armour"
    pairs = bonded.bonded_rune_lines(["Hawk Idol"], base)
    assert pairs and pairs[0][0] == "Idol", pairs
    raw = ("Rarity: UNIQUE\nMorior Invictus\nGrand Regalia\nRune: Hawk Idol\n"
           "{rune}Idols socketed in this item gain the benefits of their Bonded modifiers\n"
           f"{{enchant}}{{rune}}Bonded: {pairs[0][1]}")
    assert bonded.INACTIVE not in bonded.mark_text(raw, base, build_unlocked=False)
    plain = raw.replace("{rune}Idols socketed in this item gain the benefits of their Bonded modifiers\n", "")
    assert bonded.INACTIVE in bonded.mark_text(plain, base, build_unlocked=False)


def test_catalysed_text_writes_the_catalyst_and_each_lines_tags():
    from builder.search.rares import catalysed_text
    a = Affix("LightningDamagePercent3", "g", "Prefix", 1, ("20% increased Lightning Damage",))
    b = Affix("IncreasedLife1", "h", "Prefix", 1, ("+15 to maximum Life",))
    text = "Rarity: Rare\nOptimizer Ring 1\nRuby Ring\nItem Level: 82\nImplicits: 1\n+20% to Fire Resistance\n" \
           "20% increased Lightning Damage\n+15 to maximum Life"
    tags = {"LightningDamagePercent3": ("elemental_damage", "damage", "elemental", "lightning"),
            "IncreasedLife1": ("resource", "life")}
    out = catalysed_text(text, [a, b], tags, "Esh's Catalyst").split("\n")
    assert out[4:6] == ["Catalyst: Esh's", "CatalystQuality: 20"]
    assert out[7] == "+20% to Fire Resistance"  # (the implicit: untagged)
    assert out[8] == "{tags:elemental_damage,damage,elemental,lightning}20% increased Lightning Damage"
    assert out[9] == "{tags:resource,life}+15 to maximum Life"


def test_pob_scales_a_catalysed_rings_matching_lines(engine, monkeypatch):
    """The catalyst probe: a Spark build's ring with % increased Lightning Damage scores higher with 20% Esh's
    (lightning) quality, the same with Flesh (life: no line of its tag); the route (switched on) picks Esh's."""
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.rares import catalysed_text
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    ctx.payload["catalyst_prices"] = {"Esh's Catalyst": 0.03, "Flesh Catalyst": 0.005}
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Spark 20/0  1", main=True, fullDPS=True)  # (a build with Spark's damage)
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Ring")
    level = gear.pools.item_level
    light = sorted((a for a in gear.pools.pool(base["name"]) if any("increased Lightning Damage" in x for x in a.lines)),
                   key=lambda a: a.tier)
    assert light, "a ring rolls % increased Lightning Damage"
    chosen = [light[0]]
    tags = gear.catalyst_tags()
    plain = rare_text("Ring 1", base, level, chosen)
    s_plain, s_esh, s_flesh = (s for s, _ in gear.deltas("Ring 1", [
        plain, catalysed_text(plain, chosen, tags, "Esh's Catalyst"), catalysed_text(plain, chosen, tags, "Flesh Catalyst")]))
    print("PROBE catalyst", chosen[0].lines, s_plain, s_esh, s_flesh)
    assert s_esh > s_plain + 1e-6, "PoB scales the tagged lightning line by the catalyst's quality"
    assert abs(s_flesh - s_plain) < 1e-6
    monkeypatch.setenv("POE2_SEARCH_CATALYST", "1")
    option = ItemOption(base["name"], base["name"], "Ring", None, normal_item(base), False, 0.0, "found", 1)
    out = gear.catalyst_trial("Ring 1", base, option, level, chosen, s_plain, 1e6, clock.now() + 300)
    print("PROBE catalyst trial", out and out.craft_route, out and out.price)
    assert out is not None and "Catalyst: Esh's" in out.raw and out.craft_route[0].startswith("Esh's Catalyst")


def test_a_filler_is_never_socket_bound():
    """(SEARCHC2) socket-bound augments (the Raven-Touched Shard, influence runes, ...) are never socket fillers: a
    filler is "the player's choice; swap it freely", and one of these can't be removed."""
    from builder.search.rares import RuneCatalog
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    catalog = RuneCatalog(ctx, {})
    assert {"Raven-Touched Shard", "Kolr's Hunt", "Serle's Triumph", "Ire of Aldur"} <= catalog.socket_bound
    assert "Iron Rune" not in catalog.socket_bound
    from builder.search.gear import Gear
    from tests.test_searchc1_sockets import _StubBuild, _StubRunes
    base = next(b for b in ctx.data.bases if b["name"] == "Daggerfoot Shoes")
    gear = Gear.__new__(Gear)
    gear.runes = _StubRunes({"Raven-Touched Shard": 0.001, "Lesser Body Rune": 0.01})
    gear.build = _StubBuild(lambda raw: 1.0, lambda raw: {"Life": 1000 + 70 * raw.count("Lesser Body Rune")})
    chosen: list[str] = []
    fillers = gear.fill_sockets("Boots", base, "Rarity: Normal\nDaggerfoot Shoes", chosen, 0, 1, 1.0, 10.0,
                                ["Raven-Touched Shard", "Lesser Body Rune"], lambda n: True, float("inf"))
    assert chosen == ["Lesser Body Rune"] and "Raven-Touched Shard" not in [n for n, _ in fillers]


def test_the_elemental_fluxes_change_only_the_element_word():
    from builder.search.rares import fluxed, flux_line
    assert flux_line("+44% to Fire Resistance", "Cold") == "+44% to Cold Resistance"
    assert flux_line("+15% to Lightning and Chaos Resistances", "Fire") == "+15% to Fire and Chaos Resistances"
    assert flux_line("+2% to Maximum Cold Resistance", "Lightning") == "+2% to Maximum Lightning Resistance"
    assert flux_line("+12% to Fire and Cold Resistances", "Lightning") is None  # (no row: not fluxed)
    assert flux_line("+44% to Fire Resistance", "Chaos") == "+26% to Chaos Resistance"  # (the Void Flux's table)
    res = [Affix("FireResist8", "a", "Suffix", 1, ("+44% to Fire Resistance",)),
           Affix("ColdResist8", "b", "Suffix", 1, ("+43% to Cold Resistance",))]
    out = fluxed(res, "Cold")
    assert [a.lines for a in out] == [("+44% to Cold Resistance",), ("+43% to Cold Resistance",)]
    assert out[0].id.endswith("+ChillingFlux") and out[1] is res[1]
