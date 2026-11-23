"""SEARCHC3: two crafting routes behind OFF switches. Serle's Triumph (a fourth suffix, priced by its Exalt odds) and
Essence of Horror's "60% increased effect of Socketed Augment Items" on rare Gloves / Boots."""
from __future__ import annotations

import faulthandler
import random

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.gear import (
    AUGMENT_EFFECT_MOD, HORROR, OMEN_SUFFIX, SERLE, augment_effect_enabled, serle_enabled, serle_loop,
)
from builder.search.rares import (
    AffixPools, Affix, aldur_implicit_row, catalysed_text, implicit_tags, rare_price, rare_text, socket_count,
    with_runes,
)

# The real cached exchange prices (Forbidden Rites, read 23 Nov through PriceFeed.table(refresh=False))
SERLE_PRICE = 3.56
MATERIALS = {"Exalted Orb": 0.001483, "Omen of Dextral Exaltation": 0.06361, "Orb of Annulment": 0.2913,
             "Omen of Dextral Annulment": 13.61, "Chaos Orb": 0.099}
ORBS = {"Exalted Orb": 0.001483, "Greater Exalted Orb": 0.027, "Perfect Exalted Orb": 2.4}


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


def test_the_targeted_loop_prices_misses_and_leaves_the_omen_out_when_asked():
    others = [Affix(str(i), f"o{i}", "Suffix", 1, ("o",)) for i in range(3)]
    orb, omen = MATERIALS["Exalted Orb"], MATERIALS["Omen of Dextral Exaltation"]
    # 10 tries; the plain Annulment hits any of 3 others or the miss: 3 / 4 of the time a fresh copy (10 div)
    plain, _ = serle_loop(0.1, orb, others, 10.0, MATERIALS)
    assert plain == pytest.approx(10 * orb + 9 * (0.2913 + 0.75 * 10))
    with_omen, _ = serle_loop(0.1, orb + omen, others, 10.0, MATERIALS)
    assert with_omen - plain == pytest.approx(10 * omen)
    assert serle_loop(0.0, orb, others, 10.0, MATERIALS) is None


def test_a_higher_orb_narrows_the_outcomes(ctx):
    pools = AffixPools(ctx)
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and pools.pool(b["name"]))
    every = pools.side_outcomes(base["name"], "Suffix", [])
    perfect = pools.side_outcomes(base["name"], "Suffix", [], 50)
    assert 0 < len(perfect) < len(every)
    # every group still has a row: a group with none at the minimum keeps its best
    assert {a.group for a, _ in perfect} == {a.group for a, _ in every}
    assert all(a.type == "Suffix" for a, _ in every)


def serle_setup(engine, prefixes: int):
    from builder.search.gear import affix_limits

    ctx, gear = spark_gear(engine, "Wand")
    ctx.augment_prices[SERLE] = SERLE_PRICE
    ctx.influence_prices = dict(MATERIALS)
    ctx.payload["serle_prices"] = dict(ORBS)
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    limits = affix_limits(base)
    chosen: list = []
    for kind, count in (("Prefix", prefixes), ("Suffix", limits["Suffix"])):
        side = [a for a in pool if a.type == kind]
        scored = [s for s, _ in gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, [a]) for a in side])]
        taken = 0
        for _, a in sorted(zip(scored, side), key=lambda x: x[0]):  # (the weakest: the fourth suffix has room to pay)
            if taken < count and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen):
                chosen.append(a)
                taken += 1
    current = gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, chosen)])[0][0]
    option = ItemOption(base["name"], base["name"], "Wand", None, normal_item(base), False, 0.0, "found", 1)
    return ctx, gear, base, level, limits, chosen, current, option


def only_serle(gear) -> None:
    real = gear.runes.candidates
    gear.runes.candidates = lambda *a, **k: [n for n in real(*a, **k) if n == SERLE]


def test_serle_random_fill_with_full_prefixes_costs_the_rune_and_one_orb(engine):
    """R55-6's route at the real prices: 3 + 3, the rune, one orb, no Omen; DPS at the mean over the outcomes."""
    ctx, gear, base, level, limits, chosen, current, option = serle_setup(engine, 3)
    gear.lam = 1e-4  # (a budget weight)
    against = gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, chosen, current, 1e6, clock.now() + 300)
    print("PROBE serle against the best ordinary rune:", against and against.confidence, against and against.price)
    only_serle(gear)  # (the route itself: no ordinary rune competing for the socket)
    ctx.influence_prices = {k: v for k, v in MATERIALS.items() if k != "Orb of Annulment"}  # (no targeted loop priced)
    gear.lam = 0.0
    out = gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, chosen, current, 1e6, clock.now() + 300)
    bought = rare_price("Weapon 1", chosen)
    print("PROBE serle fill", bought, out and out.price, out and out.confidence, out and out.alloy_scale,
          out and out.craft_route, out and out.alloy_report)
    assert out is not None and out.bound == (SERLE,)
    assert "random fill" in out.confidence and out.augment == "serle_fill" and out.alloy == SERLE
    assert out.price - bought - SERLE_PRICE in (pytest.approx(ORBS["Exalted Orb"]), pytest.approx(ORBS["Greater Exalted Orb"]),
                                                pytest.approx(ORBS["Perfect Exalted Orb"]))
    assert "no Omen of Dextral Exaltation is priced" in out.craft_route[0] and "LOW" in out.craft_route[0]
    assert out.alloy_scale >= 1.0  # (the item shown is the worst outcome; the mean can only be at or above it)


def test_serle_targeted_with_no_budget_weight_lands_the_best_suffix(engine):
    ctx, gear, base, level, limits, chosen, current, option = serle_setup(engine, 3)
    gear.lam = 0.0
    out = gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, chosen, current, 1e6, clock.now() + 300)
    print("PROBE serle aimed", out and out.price, out and out.confidence, out and out.affixes[-1:], out and out.craft_route)
    assert out is not None and "targeted" in out.confidence
    assert gear.deltas("Weapon 1", [out.raw])[0][0] > current + 1e-6, "PoB scores the fourth suffix"
    assert any(r.startswith("Row: with Omen of Dextral Exaltation") for r in out.craft_route)
    assert any("keep what lands" in r for r in out.craft_route)
    # an open suffix slot needs no Serle's Triumph
    assert gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits,
                            [a for a in chosen if a is not next(x for x in chosen if x.type == "Suffix")], current, 1e6,
                            clock.now() + 300) is None


def test_serle_with_a_prefix_open_prices_the_omen(engine):
    ctx, gear, base, level, limits, chosen, current, option = serle_setup(engine, 1)
    gear.lam = 1e-4
    only_serle(gear)
    out = gear.serle_trial("Weapon 1", base, option, level, gear.pools, limits, chosen, current, 1e6, clock.now() + 300)
    print("PROBE serle open prefix", out and out.price, out and out.craft_route[:1])
    assert out is not None and "Omen of Dextral Exaltation" in out.craft_route[0] and "is priced in" in out.craft_route[0]


def test_horror_with_serle_says_the_pair_was_not_tried(engine, monkeypatch):
    ctx, gear = spark_gear(engine, "Wand")
    base = next(b for b in ctx.data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    made = ItemOption("Rare Gloves", base["name"], "Gloves", None, normal_item(base), False, 1.0, "x", 1, rare=True,
                      craft_route=("route",))
    monkeypatch.setattr(gear, "augment_effect_trial", lambda *a, **k: made)
    monkeypatch.setenv("POE2_SEARCH_AUGMENT_EFFECT", "1")
    monkeypatch.delenv("POE2_SEARCH_SERLE", raising=False)
    assert gear.build_rare("Gloves", clock.now() + 120).craft_route == ("route",)
    monkeypatch.setenv("POE2_SEARCH_SERLE", "1")
    route = gear.build_rare("Gloves", clock.now() + 120).craft_route
    assert len(route) == 2 and route[1].startswith(f"Not tried: {SERLE}")


def test_a_catalyst_scales_the_implicit_in_pob(engine):
    """The trade search of 23 Nov: a Lazuli Ring's Mana implicit scales with Mana quality (+42 at 40%, +34 at 20%)."""
    ctx, gear = spark_gear(engine, "Wand")
    base = next(b for b in ctx.data.bases if b["name"] == "Lazuli Ring")
    level = gear.pools.item_level
    assert implicit_tags("Lazuli Ring") == (("resource", "mana"),) and implicit_tags("Breach Ring") == ((),)
    plain = rare_text("Ring 1", base, level, [])
    (_, bare), (_, untagged), (_, tagged), (_, other) = gear.deltas("Ring 1", [
        plain, catalysed_text(plain, [], {}, "Neural Catalyst"),
        catalysed_text(plain, [], {}, "Neural Catalyst", implicit=implicit_tags("Lazuli Ring")),
        catalysed_text(plain, [], {}, "Flesh Catalyst", implicit=implicit_tags("Lazuli Ring"))])
    print("PROBE catalyst implicit", bare["Mana"], untagged["Mana"], tagged["Mana"], other["Mana"])
    assert untagged["Mana"] == bare["Mana"] and other["Mana"] == bare["Mana"]
    rolled = int(next(l for l in plain.splitlines() if l.endswith("to maximum Mana")).split()[0].lstrip("+"))
    print("PROBE catalyst implicit roll", rolled)
    scaled = int(rolled * 1.2)  # (the game's rounding, and PoB's: floorSymmetric)
    written = plain.replace(f"+{rolled} to maximum Mana", f"+{scaled} to maximum Mana")
    same = gear.deltas("Ring 1", [written])[0][1]
    print("PROBE catalyst implicit, the ring written at", scaled, "reads", same["Mana"])
    assert tagged["Mana"] == same["Mana"] and tagged["Mana"] > bare["Mana"]


def test_aldur_implicit_row_names_only_another_element():
    assert aldur_implicit_row("Striking Spear", "Lightning") == ()
    assert aldur_implicit_row("Plain Wand", "Fire") == ()
    row = aldur_implicit_row("Runemastered Torment Club", "Fire")
    assert len(row) == 1 and "Cold and Lightning" in row[0] and "UNVERIFIED" in row[0]


def test_the_flux_route_says_a_fractured_modifier_stays(engine):
    from builder.search.gear import affix_limits

    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["flux_prices"] = {"Void Flux": 0.127}
    base = next(b for b in ctx.data.bases if b["type"] == "Boots" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    resist = next(a for a in pool if a.type == "Suffix" and any("Fire Resistance" in x for x in a.lines))
    current = gear.deltas("Boots", [rare_text("Boots", base, level, [resist])])[0][0] - 1.0  # (so the route is kept)
    option = ItemOption(base["name"], base["name"], "Boots", None, normal_item(base), False, 0.0, "found", 1)
    out = gear.flux_trial("Boots", base, option, level, gear.pools, affix_limits(base), [resist], current, 1e6,
                          clock.now() + 300)
    assert out is not None and "A Fractured modifier is not transformed (0.5.1 Hotfix 8)" in out.craft_route[0]


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
