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
from builder.search import alloys
from builder.search.gear import (
    ASTRID, AUGMENT_EFFECT_MOD, HORROR, OMEN_SUFFIX, SERLE, augment_effect_enabled, augment_effect_source, serle_enabled, serle_loop,
)
from builder.search.rares import (
    AffixPools, Affix, aldur_implicit_row, catalysed_text, catalyst_matches, catalyst_quality, implicit_tags, rare_price, rare_text, socket_count,
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
    assert aldur_implicit_row("Arcing Quarterstaff", "Lightning") == ()
    assert aldur_implicit_row("Plain Wand", "Fire") == ()
    assert aldur_implicit_row("Striking Spear", "Fire") == ()  # (a hidden % of base damage: on none of the 78 rows)
    assert aldur_implicit_row("Energy Blade One Handed", "Fire") == ()  # (never offered)
    row = aldur_implicit_row("Crackling Quarterstaff", "Fire")
    assert len(row) == 1 and "flat Lightning" in row[0] and "UNVERIFIED" in row[0]


def test_the_flat_lightning_row_lists_exactly_what_the_search_offers(ctx):
    from builder.search.rares import ALDUR_FLAT_IMPLICIT
    flat = ("Energy Blade One Handed", "Energy Blade Two Handed", "Arcing Quarterstaff", "Bolting Quarterstaff",
            "Crackling Quarterstaff")
    offered = {o.base for options in ctx.item_options.values() for o in options if not o.unique and o.base in flat}
    assert offered == set(ALDUR_FLAT_IMPLICIT)


def test_catalyst_quality_reads_the_rings_own_maximum(ctx):
    by_name = ctx.bases_by_name
    assert catalyst_quality(by_name["Breach Ring"]) == 40 and catalyst_quality(by_name["Refined Breach Ring"]) == 45
    assert catalyst_quality(by_name["Lazuli Ring"]) == 20
    assert catalyst_quality({"implicit": "+7% to Maximum Quality"}) == 27  # (read from the line, no names)


def test_a_breach_ring_takes_40_percent_priced_by_catalyst_count(engine):
    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["catalyst_prices"] = {"Neural Catalyst": 0.03}
    base = ctx.bases_by_name["Breach Ring"]
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    mana = next(a for a in sorted(pool, key=lambda a: a.tier) if any(l.endswith("to maximum Mana") for l in a.lines))
    plain = rare_text("Ring 1", base, level, [mana])
    rolled = int(next(l for l in mana.lines if l.endswith("to maximum Mana")).split()[0].lstrip("+"))
    tagged = catalysed_text(plain, [mana], gear.catalyst_tags(), "Neural Catalyst", catalyst_quality(base))
    written = plain.replace(f"+{rolled} to maximum Mana", f"+{int(rolled * 1.4)} to maximum Mana")
    (_, read), (_, same) = gear.deltas("Ring 1", [tagged, written])
    print("PROBE breach ring", rolled, read["Mana"], same["Mana"])
    assert read["Mana"] == same["Mana"], "PoB takes 40% on a Breach Ring"
    option = ItemOption(base["name"], base["name"], "Ring", None, normal_item(base), False, 0.0, "found", 1)
    ctx.payload["catalyst_prices"] = {"Esh's Catalyst": 0.03}  # (a line this Spark build's score feels)
    light = next(a for a in sorted(pool, key=lambda a: a.tier) if any("increased Lightning Damage" in l for l in a.lines))
    current = gear.deltas("Ring 1", [rare_text("Ring 1", base, level, [light])])[0][0]
    gear.lam = 0.0
    out = gear.catalyst_trial("Ring 1", base, option, level, [light], current, 1e6, clock.now() + 300)
    print("PROBE breach trial", out and out.price, out and out.craft_route)
    assert out is not None and "CatalystQuality: 40" in out.raw and "x40: 40% quality" in out.craft_route[0]
    assert out.price == pytest.approx(rare_price("Ring 1", [light]) + 40 * 0.03)


def breach_case(engine, ring: str):
    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["catalyst_prices"] = {"Esh's Catalyst": 0.03}
    ctx.payload["breach_quality_prices"] = {"Essence of the Breach": 0.9941, "Vaal Catalysing Infuser": 4.54}
    ctx.influence_prices = {"Orb of Annulment": 0.2913, "Omen of Whittling": 13.94, "Chaos Orb": 0.09882}
    base = ctx.bases_by_name[ring]
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    light = next(a for a in sorted(pool, key=lambda a: a.tier) if any("increased Lightning Damage" in l for l in a.lines))
    option = ItemOption(base["name"], base["name"], "Ring", None, normal_item(base), False, 0.0, "found", 1)
    current = gear.deltas("Ring 1", [rare_text("Ring 1", base, level, [light])])[0][0]
    return ctx, gear, base, level, light, option, current


@pytest.mark.parametrize("ring, quality, scaled_row", [("Breach Ring", 60, False), ("Refined Breach Ring", 65, True)])
def test_the_essence_route_reaches_60_and_65_keeps_every_affix_and_is_priced(engine, ring, quality, scaled_row):
    ctx, gear, base, level, light, option, current = breach_case(engine, ring)
    gear.lam = 0.0
    out = gear.catalyst_trial("Ring 1", base, option, level, [light], current, 1e6, clock.now() + 300)
    print("PROBE breach route", ring, out and out.price, out and out.craft_route)
    assert out is not None and f"CatalystQuality: {quality}" in out.raw
    assert "Maximum Quality" not in "".join(out.affixes) and out.affixes == light.lines  # (the Essence's line is gone)
    materials = quality * 0.03 + 0.9941 + 3 * 0.2913
    assert out.price == pytest.approx(rare_price("Ring 1", [light]) + materials)
    route = " ".join(out.craft_route)
    assert "UNVERIFIED" in route and "Omen of Whittling + Chaos Orb" in route
    assert ("NOT counted: 75%" in route) == scaled_row
    rolled = next(l for l in light.lines if "increased Lightning Damage" in l)
    number = int(rolled.split("%")[0])
    written = rare_text("Ring 1", base, level, [light]).replace(f"{number}% increased Lightning Damage",
                                                                f"{int(number * (100 + quality) / 100)}% increased Lightning Damage")
    (scored, _), (same, _) = gear.deltas("Ring 1", [out.raw, written])
    assert scored == pytest.approx(same), "PoB reads the raised quality as the scaled line"


def test_a_high_budget_weight_keeps_the_cheaper_quality(engine):
    ctx, gear, base, level, light, option, current = breach_case(engine, "Breach Ring")
    ctx.payload["breach_quality_prices"] = {"Essence of the Breach": 1e6}  # (the route priced out)
    gear.lam = 0.001
    out = gear.catalyst_trial("Ring 1", base, option, level, [light], current, 1e9, clock.now() + 300)
    assert out is not None and "CatalystQuality: 40" in out.raw


def plain_ring_case(engine):
    ctx, gear, _, level, _, _, _ = breach_case(engine, "Breach Ring")
    base = ctx.bases_by_name["Pearl Ring"]
    pool = gear.pools.pool(base["name"])
    light = next(a for a in sorted(pool, key=lambda a: a.tier) if any("increased Lightning Damage" in l for l in a.lines))
    option = ItemOption(base["name"], base["name"], "Ring", None, normal_item(base), False, 0.0, "found", 1)
    current = gear.deltas("Ring 1", [rare_text("Ring 1", base, level, [light])])[0][0]
    return ctx, gear, base, level, light, option, current


def test_a_plain_ring_takes_the_essence_route_to_40_priced_and_read_by_pob(engine):
    ctx, gear, base, level, light, option, current = plain_ring_case(engine)
    gear.lam = 0.0
    out = gear.catalyst_trial("Ring 1", base, option, level, [light], current, 1e6, clock.now() + 300)
    print("PROBE plain ring", out and out.price, out and out.craft_route)
    assert out is not None and "CatalystQuality: 40" in out.raw and out.affixes == light.lines
    assert out.price == pytest.approx(rare_price("Ring 1", [light]) + 40 * 0.03 + 0.9941 + 3 * 0.2913)
    route = " ".join(out.craft_route)
    assert "Essence of the Breach" in route and "ring base's own price" not in route and "NOT counted: 75%" not in route
    rolled = next(l for l in light.lines if "increased Lightning Damage" in l)
    number = int(rolled.split("%")[0])
    written = rare_text("Ring 1", base, level, [light]).replace(f"{number}% increased Lightning Damage",
                                                                f"{int(number * 140 / 100)}% increased Lightning Damage")
    (scored, _), (same, _) = gear.deltas("Ring 1", [out.raw, written])
    assert scored == pytest.approx(same), "PoB reads 40% on a plain ring as the scaled line"


def test_a_plain_ring_stays_at_20_without_the_essence_price(engine):
    ctx, gear, base, level, light, option, current = plain_ring_case(engine)
    ctx.payload["breach_quality_prices"] = {}
    gear.lam = 0.0
    out = gear.catalyst_trial("Ring 1", base, option, level, [light], current, 1e6, clock.now() + 300)
    assert out is not None and "CatalystQuality: 20" in out.raw


def test_an_amulet_takes_the_essence_route_to_40(engine):
    ctx, gear, _, level, _, _, _ = breach_case(engine, "Breach Ring")
    base = next(b for b in ctx.data.bases if b["type"] == "Amulet" and b["name"] == "Gold Amulet")
    pool = gear.pools.pool(base["name"])
    tags = gear.catalyst_tags()
    ctx.payload["catalyst_prices"] = {"Sibilant Catalyst": 0.03}  # (amulets roll Spell Damage, a line Spark's score feels)
    line = next(a for a in sorted(pool, key=lambda a: a.tier) if catalyst_matches("Sibilant Catalyst", a, tags)
                and any("increased Spell Damage" in l for l in a.lines))
    option = ItemOption(base["name"], base["name"], "Amulet", None, normal_item(base), False, 0.0, "found", 1)
    current = gear.deltas("Amulet", [rare_text("Amulet", base, level, [line])])[0][0]
    gear.lam = 0.0
    out = gear.catalyst_trial("Amulet", base, option, level, [line], current, 1e6, clock.now() + 300)
    print("PROBE amulet", out and out.price, out and out.craft_route)
    assert out is not None and "CatalystQuality: 40" in out.raw and "3-mod amulet" in out.craft_route[0]
    assert out.price == pytest.approx(rare_price("Amulet", [line]) + 40 * 0.03 + 0.9941 + 3 * 0.2913)


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


def test_the_augment_effect_source_follows_the_base(engine):
    ctx, _ = spark_gear(engine, "Wand")
    pick = lambda kind: next(b for b in ctx.data.bases if b["type"] == kind)  # noqa: E731
    assert augment_effect_source(pick("Gloves")) == (HORROR, AUGMENT_EFFECT_MOD)
    assert augment_effect_source(pick("Boots")) == (HORROR, AUGMENT_EFFECT_MOD)
    assert augment_effect_source(pick("Wand")) == ("Sovereign Alloy", "AlloyEffectOfSocketedAugments1")
    assert augment_effect_source(pick("Ring")) is None and augment_effect_source(pick("Helmet")) is None


def test_the_horror_route_never_writes_a_fourth_suffix(engine, monkeypatch):
    """With three suffixes, an essence that removes a prefix must not leave 3 + the new one."""
    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["augment_effect_prices"] = {HORROR: 1.52}  # (no Omen priced: the route without one)
    base = next(b for b in ctx.data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    chosen: list = []
    for kind, count in (("Prefix", 1), ("Suffix", 3)):
        for _ in range(count):
            chosen.append(next(a for a in sorted(pool, key=lambda a: a.tier) if a.type == kind
                               and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen)))
    suffix_lines = {l for a in chosen if a.type == "Suffix" for l in a.lines}
    seen: list[str] = []
    real = gear.deltas
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: seen.extend(texts) or real(slot, texts))
    option = ItemOption(base["name"], base["name"], "Gloves", None, normal_item(base), False, 0.0, "found", 1)
    gear.augment_effect_trial("Gloves", base, option, level, gear.pools, chosen, 1e6, clock.now() + 300)
    with_line = [t for t in seen if "increased effect of Socketed Augment Items" in t]
    assert with_line, "the route was scored"
    for text in with_line:
        assert sum(line in suffix_lines for line in text.splitlines()) + 1 <= 3, text


def test_the_alloy_gives_a_weapon_the_augment_effect_at_its_middle_roll(engine):
    ctx, gear = spark_gear(engine, "Wand")
    ctx.alloy_prices = {"Sovereign Alloy": 2.0}
    ctx.payload["augment_effect_prices"] = {OMEN_SUFFIX: 0.43}
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    names = [n for n in gear.runes.candidates(base, False) if n not in gear.runes.socket_bound]
    plain_score = gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, [])])[0][0]
    scored = [s for s, _ in gear.deltas("Weapon 1", [rare_text("Weapon 1", base, level, [], runes=[n]) for n in names])]
    best_rune = max(zip(scored, names))
    print("PROBE alloy runes: best", best_rune, "plain", plain_score)
    chosen: list = []
    for kind in ("Prefix", "Suffix"):
        chosen.append(next(a for a in sorted(pool, key=lambda a: a.tier) if a.type == kind
                           and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen)))
    gear.lam = 0.0
    option = ItemOption(base["name"], base["name"], "Wand", None, normal_item(base), False, 0.0, "found", 1)
    out = gear.augment_effect_trial("Weapon 1", base, option, level, gear.pools, chosen, 1e6, clock.now() + 300)
    print("PROBE alloy trial", out and out.affixes, out and out.price, out and out.alloy_scale, out and out.alloy_report)
    assert best_rune[0] > plain_score + 1e-6, "a rune must score on this wand for the test to mean anything"
    assert out is not None and out.alloy == "Sovereign Alloy" and out.augment == "alloy"
    assert "25% increased effect of Socketed Augment Items" in out.raw
    assert out.price == pytest.approx(rare_price("Weapon 1", chosen) + 2.0)  # (no Omen on an Alloy)
    assert not any(OMEN_SUFFIX in row for row in out.alloy_report)
    assert any("lowest roll" in row for row in out.alloy_report)


def flux_case(engine):
    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["perfect_flux_price"] = 34.0
    base = ctx.bases_by_name["Voltaic Staff"]
    level = gear.pools.item_level
    raw = rare_text("Weapon 1", base, level, [])
    assert raw.splitlines()[-1].endswith("Grants Skill: Level 19 Lightning Bolt")  # (item level 86; a ledger tag leads it)
    option = ItemOption("Rare Staff", base["name"], base["type"], base.get("subType"), raw, False, 5.0, "x", 1, rare=True)
    return ctx, gear, option


def test_perfect_flux_sets_the_granted_skill_to_20_and_pob_reads_it(engine, monkeypatch):
    ctx, gear, option = flux_case(engine)
    gear.lam = 0.001
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: [(100.0 if "Level 20" in t else 1.0, {}) for t in texts])
    out = gear.perfect_flux_trial("Weapon 1", option, clock.now() + 60)
    monkeypatch.undo()
    assert out is not None and out.raw.splitlines()[-1].endswith("Grants Skill: Level 20 Lightning Bolt")
    assert out.raw.splitlines()[-1].startswith("{tags:")  # (the ledger tag stays on the line)
    assert out.price == pytest.approx(39.0) and "Perfect Flux (34 div)" in out.craft_route[-1]
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", raw=out.raw + "\n+200 to Intelligence", fields=[])
    found = [g.get("level") for group in engine.call("groups")["groups"] if group.get("fromItem")
             for g in group.get("gems") or [] if g.get("name") == "Lightning Bolt"]
    engine.call("equip", slot="Weapon 1", fields=[])
    print("PROBE perfect flux level", found)
    assert found == [20]


def test_perfect_flux_is_not_bought_when_the_level_moves_nothing(engine, monkeypatch):
    ctx, gear, option = flux_case(engine)
    gear.lam = 0.001
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: [(1.0, {}) for _ in texts])
    assert gear.perfect_flux_trial("Weapon 1", option, clock.now() + 60) is None


def test_perfect_flux_needs_a_price_and_a_granted_skill(engine):
    ctx, gear, option = flux_case(engine)
    ctx.payload["perfect_flux_price"] = None
    assert gear.perfect_flux_trial("Weapon 1", option, clock.now() + 60) is None
    ctx.payload["perfect_flux_price"] = 34.0
    ring = ctx.bases_by_name["Pearl Ring"]
    plain = ItemOption("Rare Ring", ring["name"], "Ring", None, rare_text("Ring 1", ring, 82, []), False, 1.0, "x", 1, rare=True)
    assert gear.perfect_flux_trial("Ring 1", plain, clock.now() + 60) is None


def test_build_rare_tries_perfect_flux_only_with_its_switch(engine, monkeypatch):
    ctx, gear = spark_gear(engine, "Wand")
    calls = []
    monkeypatch.setattr(gear, "perfect_flux_trial", lambda *a, **k: calls.append(a) or None)
    monkeypatch.delenv("POE2_SEARCH_PERFECT_FLUX", raising=False)
    assert gear.build_rare("Gloves", clock.now() + 120) is not None and not calls
    monkeypatch.setenv("POE2_SEARCH_PERFECT_FLUX", "1")
    assert gear.build_rare("Gloves", clock.now() + 120) is not None and len(calls) == 1


def horror_full_case(engine):
    ctx, gear = spark_gear(engine, "Wand")
    ctx.payload["augment_effect_prices"] = {HORROR: 1.52}
    base = next(b for b in ctx.data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    chosen: list = []
    for kind, count in (("Prefix", 1), ("Suffix", 3)):
        for _ in range(count):
            chosen.append(next(a for a in sorted(pool, key=lambda a: a.tier) if a.type == kind
                               and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen)))
    option = ItemOption(base["name"], base["name"], "Gloves", None, normal_item(base), False, 0.0, "found", 1)
    return ctx, gear, base, level, chosen, option


def test_horror_with_serle_keeps_all_three_suffixes_beside_the_line(engine, monkeypatch):
    ctx, gear, base, level, chosen, option = horror_full_case(engine)
    monkeypatch.setenv("POE2_SEARCH_SERLE", "1")
    suffix_lines = {l for a in chosen if a.type == "Suffix" for l in a.lines}
    seen: list[str] = []
    real = gear.deltas
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: seen.extend(texts) or real(slot, texts))
    gear.augment_effect_trial("Gloves", base, option, level, gear.pools, chosen, 1e6, clock.now() + 300)
    paired = [t for t in seen if SERLE in t and "increased effect of Socketed Augment Items" in t]
    assert paired, "the pair was scored"
    assert any(sum(l in suffix_lines for l in t.splitlines()) == 3 for t in paired), "a lost prefix keeps all three"
    assert all(sum(l in suffix_lines for l in t.splitlines()) + 1 <= 4 for t in paired)
    alone = [t for t in seen if SERLE not in t and "increased effect of Socketed Augment Items" in t]
    assert all(sum(l in suffix_lines for l in t.splitlines()) + 1 <= 3 for t in alone)


def test_horror_without_the_serle_switch_never_tries_the_pair(engine, monkeypatch):
    ctx, gear, base, level, chosen, option = horror_full_case(engine)
    monkeypatch.delenv("POE2_SEARCH_SERLE", raising=False)
    seen: list[str] = []
    real = gear.deltas
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: seen.extend(texts) or real(slot, texts))
    gear.augment_effect_trial("Gloves", base, option, level, gear.pools, chosen, 1e6, clock.now() + 300)
    assert seen and not any(SERLE in t for t in seen)


def test_a_corrupted_socket_keeps_the_horror_items_dps_factor_and_says_the_order(engine):
    from builder.search import alloys

    ctx, gear, base, level, chosen, _ = horror_full_case(engine)
    ctx.payload["cultivation_prices"] = {"vaal": 0.01}
    raw = rare_text("Gloves", base, level, chosen)
    filled = ItemOption("Rare Gloves", base["name"], "Gloves", None, raw, False, 5.0, "x", 1, rare=True,
                        alloy=HORROR, alloy_scale=1.1, augment="essence", craft_route=("route",))
    out = gear.extra_socket_trial("Gloves", filled, base, socket_count(base, False), clock.now() + 120)
    key = alloys.alloy_key(with_runes(raw, []).rstrip() + "\nCorrupted")
    assert gear.build.alloy_raws.get(key) == 1.1, "the corrupted copy's what-if keeps the mean-DPS factor"
    if "Corrupted" in out.raw:
        assert any(f"Use the {HORROR} BEFORE the Vaal Orb" in row for row in out.craft_route)


def test_a_bound_rune_kept_out_of_corrupted_items_still_gets_the_corrupted_socket_route(engine, monkeypatch):
    """(SEARCHC7) Serle's Triumph (NOT_IN_SEALED, socket-bound) socketed before the Vaal Orb stays (0.5.4 blocks only
    socketing INTO a Corrupted item; legality reads the route's order so): the corrupted route is tried, its note says the
    order, and the copy keeps the bound rune first. Before, any such bound rune skipped the route (an UNDERCOUNT)."""
    ctx, gear, base, level, chosen, _ = horror_full_case(engine)
    ctx.payload["cultivation_prices"] = {"vaal": 0.01}
    raw = with_runes(rare_text("Gloves", base, level, chosen), [SERLE])
    filled = ItemOption("Rare Gloves", base["name"], "Gloves", None, raw, False, 5.0, "x", 1, rare=True,
                        runes=(SERLE,), bound=(SERLE,), craft_route=("route",))
    tried: list = []
    real = gear.socketed
    monkeypatch.setattr(gear, "socketed", lambda slot, option, *a, **k: tried.append(option) or real(slot, option, *a, **k))
    gear.extra_socket_trial("Gloves", filled, base, socket_count(base, False), clock.now() + 120)
    corrupted = [o for o in tried if "Corrupted" in o.raw]
    assert corrupted, "the corrupted route was tried"
    assert all(o.runes[:1] == (SERLE,) for o in corrupted)
    assert any(f"Socket {SERLE} BEFORE the Vaal Orb" in row for o in corrupted for row in o.craft_route)


def astrid_case(engine):
    from builder.search.gear import affix_limits

    ctx, gear = spark_gear(engine, "Wand")
    base = next(b for b in ctx.data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    rows = alloys.rows_for(base, gear.pools.item_level, gear.alloy_mods)
    ctx.alloy_prices = {name: 0.5 for name, _ in rows}
    level, pool = gear.pools.item_level, gear.pools.pool(base["name"])
    chosen: list = []
    for kind in ("Prefix", "Suffix", "Suffix"):
        chosen.append(next(a for a in sorted(pool, key=lambda a: a.tier) if a.type == kind
                           and a.group not in {c.group for c in chosen} and gear.pools.choices(a, chosen)))
    option = ItemOption(base["name"], base["name"], "Gloves", None, normal_item(base), False, 0.0, "found", 1)
    return ctx, gear, base, level, affix_limits(base), chosen, option


def test_astrid_scores_two_alloys_over_every_removal_and_prices_all_of_it(engine, monkeypatch):
    ctx, gear, base, level, limits, chosen, option = astrid_case(engine)
    gear.lam = 0.0
    monkeypatch.setattr(gear.runes, "candidates", lambda *a, **k: [ASTRID])  # (no ordinary rune to beat: the route's fields)
    out = gear.astrid_trial("Gloves", base, option, level, gear.pools, limits, chosen, -1e9, 1e6, clock.now() + 300)
    print("PROBE astrid", out and out.price, out and out.alloy_report)
    # (SEARCHC5, AUDIT38 F2: Astrid's isn't socket-bound, so the route replaces it; here no ordinary rune scores)
    assert out is not None and out.alloy == ASTRID and out.bound == () and out.runes == () and ASTRID not in out.raw
    assert "sequences" in out.alloy_report[0] and "LOW" in out.craft_route[0] and "MEDIUM" in out.craft_route[0]
    assert ASTRID in out.craft_route[0] and "replace" in out.craft_route[0]
    kept_price = out.price - 0.5 - 0.5 - gear.runes.price(ASTRID)  # (the rare it started from, a full side cut or not)
    assert 0 < kept_price <= rare_price("Gloves", chosen) + 1e-9
    assert gear.build.alloy_raws[alloys.alloy_key(out.raw)] == out.alloy_scale


def test_astrid_is_replaced_by_the_best_ordinary_rune_and_priced(engine, monkeypatch):
    """(SEARCHC5, AUDIT38 F2) The socket Astrid's leaves takes the rune that scores best on the plain rare."""
    ctx, gear, base, level, limits, chosen, option = astrid_case(engine)
    gear.lam = 0.0
    names = [n for n in gear.runes.candidates(base, False) if n != ASTRID and gear.runes.price(n) is not None]
    assert names
    rune = names[0]
    monkeypatch.setattr(gear.runes, "candidates", lambda *a, **k: [ASTRID, rune])
    real = gear.deltas
    lines = {line for _, mod in alloys.rows_for(base, level, gear.alloy_mods)
             for line in gear.pools.affix(mod, mod["group"], mod["type"], 1, 1, (0.0, 0.0)).lines}

    def deltas(slot, texts):  # (the plain rare, with no Alloy line, scores nothing: the route wins)
        return [(s if any(x in t for x in lines) else -1e9, st) for (s, st), t in zip(real(slot, texts), texts)]

    monkeypatch.setattr(gear, "deltas", deltas)
    out = gear.astrid_trial("Gloves", base, option, level, gear.pools, limits, chosen, -1e9, 1e6, clock.now() + 300)
    assert out is not None, (rune, len(lines))
    assert out.runes == (rune,) and out.bound == () and f"Rune: {rune}" in out.raw and ASTRID not in out.raw
    assert rune in out.craft_route[0]
    kept_price = out.price - 0.5 - 0.5 - gear.runes.price(ASTRID) - gear.runes.price(rune)
    assert 0 < kept_price <= rare_price("Gloves", chosen) + 1e-9


def test_astrid_needs_two_priced_alloys(engine):
    ctx, gear, base, level, limits, chosen, option = astrid_case(engine)
    ctx.alloy_prices = dict(list(ctx.alloy_prices.items())[:0])
    assert gear.astrid_trial("Gloves", base, option, level, gear.pools, limits, chosen, -1e9, 1e6, clock.now() + 300) is None


def test_astrid_is_not_bought_when_it_scores_nothing(engine, monkeypatch):
    ctx, gear, base, level, limits, chosen, option = astrid_case(engine)
    gear.lam = 1.0
    monkeypatch.setattr(gear, "deltas", lambda slot, texts: [(0.0, {}) for _ in texts])
    assert gear.astrid_trial("Gloves", base, option, level, gear.pools, limits, chosen, 0.0, 1e6, clock.now() + 300) is None


def test_build_rare_tries_astrid_only_with_its_switch(engine, monkeypatch):
    ctx, gear = spark_gear(engine, "Wand")
    calls = []
    monkeypatch.setattr(gear, "astrid_trial", lambda *a, **k: calls.append(a) or None)
    monkeypatch.delenv("POE2_SEARCH_ASTRID", raising=False)
    assert gear.build_rare("Gloves", clock.now() + 120) is not None and not calls
    monkeypatch.setenv("POE2_SEARCH_ASTRID", "1")
    assert gear.build_rare("Gloves", clock.now() + 120) is not None and len(calls) == 1
