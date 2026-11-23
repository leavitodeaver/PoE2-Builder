"""SEARCHC3: two crafting routes behind OFF switches. Serle's Triumph (a fourth suffix, priced by its Exalt odds) and
Essence of Horror's "60% increased effect of Socketed Augment Items" on rare Gloves / Boots."""
from __future__ import annotations

import faulthandler
import random

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import influence
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.gear import (
    AUGMENT_EFFECT_MOD, HORROR, OMEN_SUFFIX, SERLE, augment_effect_enabled, serle_enabled,
)
from builder.search.rares import AffixPools, Affix, rare_price, rare_text, socket_count, with_runes

MATERIALS = {"Exalted Orb": 0.002, "Omen of Dextral Exaltation": 0.064, "Orb of Annulment": 0.29,
             "Omen of Dextral Annulment": 13.6, "Chaos Orb": 0.099}


@pytest.fixture(scope="module")
def ctx() -> Context:
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    return Context(make_payload(BuildRequest(main_skill="Spark")))


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


def spark_gear(engine, slot_type: str):
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text="Spark 20/0  1", main=True, fullDPS=True)
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec(slot_type), None, random.Random(0))
    return ctx, gear


def test_both_routes_are_off_by_default(monkeypatch):
    monkeypatch.delenv("POE2_SEARCH_SERLE", raising=False)
    monkeypatch.delenv("POE2_SEARCH_AUGMENT_EFFECT", raising=False)
    assert not serle_enabled() and not augment_effect_enabled()


def test_exalt_share_counts_the_tier_or_better_among_the_rows_still_open(ctx):
    pools = AffixPools(ctx)
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and pools.pool(b["name"]))
    suffixes = [a for a in pools.pool(base["name"]) if a.type == "Suffix"]
    top = next(a for a in suffixes if a.lower is not None)
    # a lower tier is hit by its own row and the better one: at least as likely
    assert 0 < pools.exalt_share(base["name"], top, []) <= pools.exalt_share(base["name"], top.lower, [])
    # taking another group off the table raises the share
    other = next(a for a in suffixes if a.group != top.group and not (set(a.adds) & top.excluded_by))
    assert pools.exalt_share(base["name"], top, [other]) > pools.exalt_share(base["name"], top, [])
    # a group the item holds can't roll again
    assert pools.exalt_share(base["name"], top, [top]) == 0


def test_the_price_counts_misses_through_the_exalt_loop():
    target = Affix("x", "g", "Suffix", 1, ("line",), side_odds=0.1)
    others = [Affix(str(i), f"o{i}", "Suffix", 1, ("o",)) for i in range(3)]
    found = [r for r in influence.routes(target, others, 10.0, MATERIALS) if "Exalted" in r[1]]
    tries, per_try = 10, MATERIALS["Exalted Orb"] + MATERIALS["Omen of Dextral Exaltation"]
    # the plain Annulment hits any of 3 others or the miss: 3 / 4 of the time a fresh copy (10 div)
    assert min(c for c, _ in found) == pytest.approx(tries * per_try + 9 * (0.29 + 0.75 * 10))


def test_serle_adds_a_scored_fourth_suffix_bound_and_priced_by_its_route(engine):
    """The probe: a Spark wand whose three suffixes do nothing for it. PoB counts a fourth suffix's line, and the trial
    binds the rune and prices the Exalt loop on top of the rare and the rune."""
    from builder.search.gear import affix_limits

    ctx, gear = spark_gear(engine, "Wand")
    ctx.influence_prices = dict(MATERIALS)
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    limits = affix_limits(base)
    suffixes = [a for a in pool if a.type == "Suffix"]
    scored = [s for s, _ in gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, [a]) for a in suffixes])]
    ranked = [a for _, a in sorted(zip(scored, suffixes), key=lambda x: x[0])]
    filler: list = []
    for a in ranked:
        if a.group not in {f.group for f in filler} and gear.pools.choices(a, filler):
            filler.append(a)
        if len(filler) == limits["Suffix"]:
            break
    current = gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, filler)])[0][0]
    option = ItemOption(base["name"], base["name"], "Wand", None, normal_item(base), False, 0.0, "found", 1)
    out = gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, filler, current, 1e6, clock.now() + 300)
    print("PROBE serle", [a.lines for a in filler], current, out and out.affixes, out and out.price, out and out.craft_route)
    assert out is not None, "a fourth suffix beats the wand's useless three"
    assert out.bound == (SERLE,) and out.runes == (SERLE,) and f"Rune: {SERLE}" in out.raw
    assert len(out.affixes) >= len(filler) + 1
    assert gear.deltas("Weapon 1", [out.raw])[0][0] > current + 1e-6, "PoB scores the fourth suffix"
    assert out.price > rare_price("Weapon 1", filler) + ctx.augment_prices[SERLE]
    assert "fourth suffix" in out.craft_route[0] and "LOW" in out.craft_route[0]
    # an open suffix slot needs no Serle's Triumph
    assert gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, filler[:-1], current, 1e6,
                            clock.now() + 300) is None


@pytest.mark.parametrize("switch, method, slot", [("POE2_SEARCH_SERLE", "serle_trial", "Gloves"),
                                                  ("POE2_SEARCH_AUGMENT_EFFECT", "augment_effect_trial", "Gloves")])
def test_build_rare_reaches_a_route_only_with_its_switch(engine, monkeypatch, switch, method, slot):
    ctx, gear = spark_gear(engine, "Wand")
    for name in ("POE2_SEARCH_SERLE", "POE2_SEARCH_AUGMENT_EFFECT"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(gear, method, lambda *a, **k: calls.append(a) or None)
    assert gear.build_rare(slot, clock.now() + 120) is not None and not calls
    monkeypatch.setenv(switch, "1")
    assert gear.build_rare(slot, clock.now() + 120) is not None and len(calls) == 1


def test_pob_scales_a_socketed_rune_by_the_horror_line_rounded_down(engine):
    """R51-8's table through PoB: Perfect Storm Rune's 22% Lightning Resistance reads 35% (35.2 floored) with the line."""
    ctx, gear = spark_gear(engine, "Wand")
    base = next(b for b in ctx.data.bases if b["type"] == "Boots" and socket_count(b, False) >= 1
                and not b.get("implicit"))
    mod = next(m for m in ctx.data.mods["Item"] if m["id"] == AUGMENT_EFFECT_MOD)
    affix = gear.pools.affix(mod, mod["group"], mod["type"], 1, 1, (0.0, 0.0))
    level = gear.pools.item_level
    bare, rune, both = (st for _, st in gear.deltas("Boots", [
        rare_text("Boots", base, level, []),
        with_runes(rare_text("Boots", base, level, []), ["Perfect Storm Rune"]),
        with_runes(rare_text("Boots", base, level, [affix]), ["Perfect Storm Rune"])]))
    key = "LightningResistTotal" if "LightningResistTotal" in bare else "LightningResist"
    print("PROBE horror", key, bare.get(key), rune.get(key), both.get(key))
    assert rune[key] - bare[key] == pytest.approx(22)
    assert both[key] - bare[key] == pytest.approx(35)


def test_the_horror_route_scores_over_the_lost_modifier_and_prices_its_materials(engine):
    from builder.report import bonded  # noqa: F401  (the display path imports cleanly with the route's fields)

    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["augment_effect_prices"] = {HORROR: 1.52, OMEN_SUFFIX: 0.43}
    base = next(b for b in ctx.data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    names = [n for n in gear.runes.candidates(base, False) if n not in gear.runes.socket_bound]
    plain_score = gear.deltas("Gloves", [rare_text("Gloves", base, level, [])])[0][0]
    scored = [s for s, _ in gear.deltas("Gloves", [rare_text("Gloves", base, level, [], runes=[n]) for n in names])]
    best_rune = max(zip(scored, names))
    print("PROBE horror runes: best", best_rune, "plain", plain_score)
    chosen: list = []
    for kind in ("Prefix", "Suffix"):
        chosen.append(next(a for a in sorted(pool, key=lambda a: a.tier) if a.type == kind
                           and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen)))
    option = ItemOption(base["name"], base["name"], "Gloves", None, normal_item(base), False, 0.0, "found", 1)
    out = gear.augment_effect_trial("Gloves", base, option, level, gear.pools, chosen, 1e6, clock.now() + 300)
    print("PROBE horror trial", out and out.affixes, out and out.price, out and out.alloy_scale, out and out.alloy_report)
    if best_rune[0] <= plain_score + 1e-6:
        assert out is None, "no rune scores: x1.6 of nothing can't pay for the essence"
        pytest.skip("no rune raises this fresh Spark build's score in Gloves")
    assert out is not None and out.alloy == HORROR and out.augment == "essence"
    assert "60% increased effect of Socketed Augment Items" in out.raw
    bought = rare_price("Gloves", chosen)
    assert out.price in (pytest.approx(bought + 1.52), pytest.approx(bought + 1.52 + 0.43))
    assert out.craft_route and out.craft_route[0].startswith(HORROR)
