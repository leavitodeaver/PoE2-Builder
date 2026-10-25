"""Shopping list, trade searches, rate limits, the rare price model and the test-in-game checklist."""

from __future__ import annotations

import io
import json
import math
import urllib.error

import pytest

from builder.data import GameData, rare_prices
from builder.data.trade import FETCH, SEARCH, RateLimited, RateLimiter, TradeClient, parse_buckets
from builder.report.calibrate import choose, listing_price
from builder.report.checklist import checklist
from builder.report.shopping import identify, ladder, ranked_affixes, shopping_list, stat_value, trade_filters, trade_link
from builder.search.rares import mod_lines, roll


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Headers(dict):
    def get(self, key, default=None):  # case-insensitive, like http.client's
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


SEARCH_HEADERS = Headers({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "5:10:60,15:60:300",
                          "X-Rate-Limit-Ip-State": "1:10:0,1:60:0"})


# Rate limits ---------------------------------------------------------------------------------

def test_parse_buckets() -> None:
    assert parse_buckets("5:10:60,15:60:300") == [(5, 10, 60), (15, 60, 300)]
    assert parse_buckets(None) == [] and parse_buckets("junk") == []


def test_limiter_keeps_a_request_spare_in_every_window() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    limiter.update(SEARCH, Headers({**SEARCH_HEADERS, "X-Rate-Limit-Ip-State": "0:10:0,0:60:0"}))
    assert limiter.rules[SEARCH] == [(5, 10, 60), (15, 60, 300)]
    for _ in range(4):  # 5 per 10 s, one left for the user's own trade-site tabs
        assert limiter.delay(SEARCH) <= 1.0
        clock.now += limiter.delay(SEARCH)
        limiter.record(SEARCH)
    first = limiter.history[SEARCH][0]
    # The oldest hit ages out a padded second after its window ends (arrival jitter)
    assert limiter.delay(SEARCH) == pytest.approx(first + 10 + 1.0 - clock.now)
    clock.now = first + 11.1
    assert limiter.delay(SEARCH) <= 1.0


def test_limiter_counts_hits_it_did_not_make_and_penalties() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    # The user's browser made four searches we didn't: the server's state says so
    limiter.update(SEARCH, Headers({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "5:10:60",
                                    "X-Rate-Limit-Ip-State": "4:10:0"}))
    assert limiter.delay(SEARCH) > 5
    limiter.update(FETCH, Headers({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "12:4:10",
                                   "X-Rate-Limit-Ip-State": "13:4:10"}))
    assert limiter.delay(FETCH) >= 10 - 1e-6  # locked out: wait the active penalty
    limiter.penalize(SEARCH, 120)
    assert limiter.delay(SEARCH) >= 120 - 1e-6


class FakeSite:
    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)


class Response(io.BytesIO):
    def __init__(self, payload) -> None:
        super().__init__(json.dumps(payload).encode())
        self.headers = SEARCH_HEADERS

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_search_is_cached_identified_and_rate_limited(tmp_path) -> None:
    clock = Clock()
    site = FakeSite([{"id": "abc", "total": 7, "result": ["i1", "i2"]}])
    client = TradeClient(tmp_path, RateLimiter(tmp_path / "limits.json", clock=clock), opener=site,
                         sleep=lambda s: setattr(clock, "now", clock.now + s))
    query = {"query": {"type": "Greater Robust Rune"}}
    first = client.search("Test League", query)
    assert first["url"].endswith("/trade2/search/poe2/Test%20League/abc") and not first["cached"]
    agent = site.requests[0].get_header("User-agent")
    assert agent.startswith("poe2-builder/") and "@" not in agent
    assert client.search("Test League", query)["cached"] and len(site.requests) == 1
    # A 429 is honored: the next request waits Retry-After, and callers that can't wait are told so
    site.answers.append(urllib.error.HTTPError("u", 429, "Too Many", Headers({"Retry-After": "60"}), None))
    with pytest.raises(RateLimited):
        client.search("Test League", {"query": {"type": "Other"}})
    with pytest.raises(RateLimited) as waited:
        client.search("Test League", {"query": {"type": "Third"}}, max_wait=5)
    assert waited.value.wait > 50 and len(site.requests) == 2  # refused without asking the site


# Rare price model ------------------------------------------------------------------------------

def test_prior_is_the_old_estimate_and_never_zero() -> None:
    model = rare_prices.RarePriceModel()
    assert model.price("Helmet", [1]) == pytest.approx(0.08)
    assert model.price("Weapon 1", [1, 2]) == pytest.approx(0.02 * 2 * 4 * 1.8)
    assert model.price("Jewel 123", [5, 7]) == pytest.approx(0.02 * 1.05 * 1.05)
    assert model.confidence("Helmet") == "estimate (uncalibrated)"
    cheap = rare_prices.RarePriceModel({"base": {c: -50.0 for c in rare_prices.CATEGORIES},
                                        "tier": {str(t): -5.0 for t in rare_prices.TIERS}}, fitted_at=1.0)
    assert cheap.price("Ring 1", [1, 1, 1]) == rare_prices.MIN_PRICE > 0


def test_fit_recovers_prices_and_labels_thin_data() -> None:
    truth_base = {"helmet": math.log(0.5), "ring": math.log(2.0)}
    truth_tier = {1: math.log(6.0), 2: math.log(2.0), 3: math.log(1.2), 4: math.log(1.0)}
    samples = []
    for cat in truth_base:
        for tiers in ([1], [1, 1], [1, 2], [2, 2, 3], [3, 3], [1, 2, 3], [2], [1, 1, 2, 2], [3, 4], [4, 4, 1]):
            log_price = truth_base[cat] + sum(truth_tier[t] for t in tiers)
            samples.append({"category": cat, "tiers": tiers, "price": math.exp(log_price)})
    samples.append({"category": "belt", "tiers": [1], "price": 3.0})
    samples.append({"category": "boots", "tiers": [1, 1], "price": None})  # no listings: ignored
    model = rare_prices.fit(samples, "Test", now=1_800_000_000)
    assert model.price("Helmet", [1, 2]) == pytest.approx(0.5 * 6 * 2, rel=0.25)
    assert model.price("Ring 2", [1, 1]) == pytest.approx(2.0 * 36, rel=0.25)
    assert model.confidence("Helmet").startswith("calibrated ")
    assert model.confidence("Belt").startswith("estimate (thin data: 1 trade sample,")
    assert model.confidence("Boots").startswith("estimate (thin data: 0 trade samples")
    assert model.samples == {"helmet": 10, "ring": 10, "belt": 1}
    # The model survives the payload round trip to the workers
    again = rare_prices.RarePriceModel.from_payload(json.loads(json.dumps(model.payload())))
    assert again.price("Belt", [1, 2]) == pytest.approx(model.price("Belt", [1, 2]))


def test_stacked_top_tiers_cost_more_than_their_sum() -> None:
    # The market on 2026-10-24: one or two T1 affixes at the floor, four or more far above it
    samples = [{"category": "helmet", "tiers": t, "price": p, "listed": 100}
               for t, p in (([1, 2], 0.0014), ([1, 1], 0.002), ([1, 1, 2], 0.01), ([1, 1, 1], 0.4),
                            ([1, 1, 1, 1], 19.0), ([2, 2, 2], 0.0014), ([3, 3], 0.0014))]
    samples.append({"category": "helmet", "tiers": [1, 1, 1, 1, 1], "price": 9000.0, "listed": 1})
    model = rare_prices.fit(samples)
    assert model.describe()["stack_factor"] > 3
    assert model.price("Helmet", [1, 1, 1, 1]) > 50 * model.price("Helmet", [1, 1])
    # One listing at 9,000 divines barely moves it
    assert model.price("Helmet", [1, 1, 1, 1, 1]) < 9000.0 / 3


def test_thin_data_stays_near_the_prior() -> None:
    model = rare_prices.fit([{"category": "belt", "tiers": [1], "price": 500.0}])
    prior = rare_prices.RarePriceModel()
    assert prior.price("Belt", [1]) < model.price("Belt", [1]) < 500.0
    assert model.price("Amulet", [3]) == pytest.approx(prior.price("Amulet", [3]), rel=0.6)


# Shopping list ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def data() -> GameData:
    return GameData()


STATS = {"explicit.stat_3299347043": {"text": "+# to maximum Life", "type": "explicit"},
         "explicit.stat_4220027924": {"text": "#% to Cold Resistance", "type": "explicit"},
         "explicit.stat_1754445556": {"text": "Adds # to # Lightning damage to Attacks", "type": "explicit"}}


def pick(data: GameData, base: str, group: str, tier: int, pool: str = "Item") -> dict:
    return next(m for m, t in ranked_affixes(data, base, pool) if m["group"] == group and t == tier)


def test_trade_values() -> None:
    assert stat_value("+44% to Cold Resistance", "#% to Cold Resistance") == 44
    assert stat_value("Adds 3 to 68 Lightning damage to Attacks", "Adds # to # Lightning damage to Attacks") == 35.5
    assert stat_value("Bow Attacks fire an additional Arrow", "Bow Attacks fire an additional Arrow") is None


def test_identify_rare_affixes_and_build_searches(data) -> None:
    life, cold = pick(data, "Absent Amulet", "IncreasedLife", 2), pick(data, "Absent Amulet", "ColdResistance", 1)
    lines = [roll(l, 0.7) for m in (life, cold) for l in mod_lines(m)]
    item = {"name": "Rare Amulet", "base": "Absent Amulet", "type": "Amulet", "rare": True, "unique": False,
            "price": 1.5, "confidence": "estimate (uncalibrated)", "affixes": lines + ["Made-up line"],
            "runes": []}
    found = identify(item, data, 0.7)
    assert [(m and m["id"], t) for m, t, _ in found] == [(life["id"], 2), (cold["id"], 1), (None, None)]
    filters = trade_filters(cold, 0.7, STATS)
    low = int(mod_lines(cold)[0].split("(")[1].split("-")[0])
    assert filters[0]["id"] == "explicit.stat_4220027924" and filters[0]["floor"] == low
    assert filters[0]["roll"] >= filters[0]["floor"]

    result = {"request": {"budget": {"divines": 10, "gear_tier": "endgame"}},
              "gear": {"Amulet": item, "Ring 1": {"name": "Blackflame", "base": "Amethyst Ring", "type": "Ring",
                                                  "unique": True, "rare": False, "price": 0.2, "confidence": "high",
                                                  "affixes": [], "runes": []},
                       "Helmet": {"name": "Golden Visage", "base": "Golden Visage", "type": "Helmet", "unique": False,
                                  "rare": False, "price": 1.25, "confidence": "found", "affixes": [],
                                  "runes": ["Greater Robust Rune"]}},
              "groups": [{"gems": [{"name": "Some Lineage", "support": True}]}]}
    shop = shopping_list(result, data, {"augments": {"Greater Robust Rune": 1.25}, "gems": {"Some Lineage": 0.5},
                                        "league": "Test"}, STATS, variants=lambda name: None)
    assert [i["slot"] for i in shop["items"]] == ["Helmet", "Amulet", "Ring 1", "Gem"]
    helmet, amulet = shop["items"][0], shop["items"][1]
    assert helmet["price"] == 0 and helmet["runes"][0]["price"] == 1.25
    assert shop["total"] == pytest.approx(1.5 + 0.2 + 1.25 + 0.5)
    assert amulet["category"] == "accessory.amulet" and amulet["confidence"] == "estimate (uncalibrated)"
    assert [a["tier"] for a in amulet["affixes"]] == [2, 1, None]

    steps = ladder(amulet)
    assert [label for label, _ in steps][0] == "exact base, modeled rolls"
    strict, loose = steps[0][1]["query"], steps[1][1]["query"]
    assert strict["type"] == "Absent Amulet" and "type" not in loose
    assert strict["filters"]["type_filters"]["filters"]["category"]["option"] == "accessory.amulet"
    ids = {f["id"] for f in loose["stats"][0]["filters"]}
    assert "explicit.stat_4220027924" in ids
    unique = ladder(shop["items"][2])[0][1]["query"]
    assert unique["name"] == "Blackflame" and unique["type"] == "Amethyst Ring"
    assert ladder(helmet, "rune:Greater Robust Rune")[0][1]["query"]["type"] == "Greater Robust Rune"


def test_calibration_helpers() -> None:
    rates = {"divine": 1.0, "exalted": 0.002}
    assert listing_price({"listing": {"price": {"amount": 50, "currency": "exalted"}}}, rates) == pytest.approx(0.1)
    assert listing_price({"listing": {"price": {"amount": 1, "currency": "unknown-orb"}}}, rates) is None
    package = {"model_category": "ring", "affixes": [{"trade": [{"id": "a", "floor": 1}]}], "source": "generic"}
    assert len(choose([package, dict(package), {**package, "source": "result x"}], 10)) == 1


def test_checklist_covers_every_asterisk_and_unmodeled_line() -> None:
    result = {
        "assumptions": [{"label": "Remnants per second", "value": 2.5, "basis": "audit", "confidence": "low"}],
        "blind_spots": [{"source": "passive:Foo", "line": "Something PoB ignores"},
                        {"source": "unique:Bar", "line": "Counted line", "corrected_by": "x", "as": ["10% more Damage"],
                         "confidence": "medium"},
                        {"source": "unique:Baz", "line": "No effect here", "corrected_by": "y", "as": []}],
        "notes": ["* Remnants per second: 2.5 (assumed ...)", "* This result maximizes clear speed. PoB doesn't..."],
        "reasons": [],
    }
    kinds = [c["kind"] for c in checklist(result)]
    assert kinds == ["assumption", "not modeled", "corrected *", "estimate *"]


# Buyable rares ---------------------------------------------------------------------------------

def pools_for(tier: str, source: str = "Item"):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.rares import AffixPools

    return AffixPools(Context(make_payload(BuildRequest(main_skill="Ice Shot", budget={"gear_tier": tier}))), source)


def test_endgame_rares_take_four_tier_one_affixes_then_tier_two() -> None:
    pools = pools_for("endgame")
    tops = [a for a in pools.pool("Biostatic Ring") if a.top]
    assert len(tops) >= 5 and all(a.lower and a.lower.tier == 2 and a.lower.group == a.group for a in tops)
    assert pools.choices(tops[3], tops[:3]) == [tops[3], tops[3].lower]  # both tiers, the budget decides
    fifth = tops[4]
    assert pools.choices(fifth, tops[:4]) == [fifth.lower]  # the fifth tier-1 pick drops a tier
    assert not fifth.lower.top and pools.choices(fifth.lower, tops[:4]) == [fifth.lower]
    mirror = pools_for("mirror")
    assert mirror.top_cap is None and mirror.choices(fifth, tops[:4]) == [fifth, fifth.lower]


def test_boosted_lines() -> None:
    from builder.search.rares import boosted

    assert boosted("17% increased Critical Damage Bonus", 54) == "26% increased Critical Damage Bonus"
    assert boosted("Adds 3 to 68 Lightning damage", 50) == "Adds 4 to 102 Lightning damage"
    assert boosted("1% increased Damage per 10 Strength", 50) == "1% increased Damage per 10 Strength"
    assert boosted("+4.2% to Critical Hit Chance", 50) == "+6.3% to Critical Hit Chance"


def test_crafted_jewels_have_four_mods_and_the_effect_line(data) -> None:
    from builder.search.rares import boosted, rare_text

    jewels = pools_for("endgame", "Jewel")
    effect_affix, effect = jewels.crafted("Prefix")
    assert effect_affix.crafted and effect_affix.type == "Suffix" and 40 <= effect <= 60
    assert "Effect of Prefixes" in effect_affix.lines[0]
    assert jewels.crafted("Suffix")[0].type == "Prefix"
    pool = jewels.pool("Emerald")
    prefixes = [a for a in pool if a.type == "Prefix"][:3]
    suffix = next(a for a in pool if a.type == "Suffix")
    lifted = [type(a)(a.id, a.group, a.type, a.tier, tuple(boosted(l, effect) for l in a.lines)) for a in prefixes]
    chosen = lifted + [suffix, effect_affix]
    # Single-tier jewel mods aren't scarce tier 1s: no stacking premium on a five-line jewel
    model = rare_prices.RarePriceModel.load("Forbidden Rites")
    assert model.price("Jewel 1", chosen) < 50 * model.price("Jewel 1", chosen[:2])
    # The shopping list recognises the boosted mods and the crafted line, and says how to craft it
    base = next(b for b in data.bases if b["name"] == "Emerald")
    raw = rare_text("Jewel", base, 82, chosen)
    assert raw.count("\n") >= 5
    item = {"name": "Rare Jewel", "base": "Emerald", "type": "Jewel", "rare": True, "unique": False, "price": 1.0,
            "confidence": "estimate", "affixes": [l for a in chosen for l in a.lines], "runes": []}
    shop = shopping_list({"request": {"budget": {"gear_tier": "endgame"}}, "gear": {"Jewel 1": item}}, data, {}, STATS)
    affixes = shop["items"][0]["affixes"]
    assert [a.get("boosted") is not None for a in affixes] == [True, True, True, False, False]
    assert affixes[-1].get("crafted") and all(a.get("tier") == 1 for a in affixes[:4])
    assert "replace the +1" in shop["items"][0]["note"]


# Jewel crafting cost ---------------------------------------------------------------------------

MATERIALS = {"Chaos Orb": 0.1, "Exalted Orb": 0.002, "Divine Orb": 1.0, "Potent Liquid Contempt": 2.5,
             "Potent Liquid Ferocity": 8.0, "Omen of Sinistral Erasure": 20.0, "Omen of Dextral Erasure": 14.0}


def craft_affix(kind: str, odds: float = 1 / 74, side: float = 1 / 44, crafted: bool = False):
    from types import SimpleNamespace

    return SimpleNamespace(type=kind, crafted=crafted, lines=[f"{kind} {odds:.4f}"], ranged=True, odds=odds,
                           side_odds=side, tier=1, top=False)


def test_jewel_craft_prices_the_base_and_each_material_step() -> None:
    suffixes = [craft_affix("Suffix", 1 / 70), craft_affix("Suffix", 1 / 80), craft_affix("Suffix", 1 / 74)]
    prefix, effect = craft_affix("Prefix", 1 / 74, 1 / 30), craft_affix("Prefix", crafted=True)
    craft = rare_prices.jewel_craft(suffixes + [prefix, effect], MATERIALS, lambda keep: 0.1, 0.7)
    items = {s["item"] for s in craft["steps"]}
    assert {"Potent Liquid Contempt", "Potent Liquid Ferocity", "Omen of Sinistral Erasure", "Chaos Orb"} <= items
    assert "Omen of Dextral Erasure" not in items  # boosted suffixes: the +1 line to erase is a prefix
    assert craft["base"]["mods"] == [suffixes[1].lines[0], suffixes[2].lines[0]]  # the two hardest suffixes
    assert craft["low"] < craft["total"] < craft["high"] and craft["total"] > 40
    assert craft["roll_divines"] == round(1 / 0.3 ** 5)
    assert rare_prices.jewel_craft(suffixes, {}, lambda keep: 0.1) is None  # no material prices
    plain = rare_prices.jewel_craft(suffixes[:2], MATERIALS, lambda keep: 0.1)
    assert plain["total"] == pytest.approx(0.1) and plain["steps"] == []  # two mods: just buy it
    # The search prices part-built jewels: the effect line before any mod of the kind it boosts
    assert rare_prices.jewel_craft([prefix, effect], MATERIALS, lambda keep: 0.1) is not None


def test_jewel_budget_uses_crafting_while_market_data_is_thin() -> None:
    affixes = [craft_affix("Suffix"), craft_affix("Suffix"), craft_affix("Prefix"), craft_affix("Prefix")]
    thin = rare_prices.RarePriceModel(materials=MATERIALS)
    priced = thin.jewel("Jewel 1", affixes)
    assert priced["basis"] == "crafting" and priced["price"] == pytest.approx(priced["craft"]["total"])
    assert thin.price("Jewel 1", affixes) == pytest.approx(priced["price"])
    calibrated = rare_prices.RarePriceModel(samples={"jewel": 5}, fitted_at=1.0, materials=MATERIALS)
    calibrated.params["base"]["jewel"] = -9.0  # a market far cheaper than crafting
    assert calibrated.jewel("Jewel 1", affixes)["basis"] == "market"
    assert rare_prices.RarePriceModel().jewel("Jewel 1", affixes)["basis"] == "market"  # no materials


def test_trade_link_reports_what_listings_ask(tmp_path) -> None:
    class Client:
        def search(self, league, query, max_wait=None):
            return {"id": "q", "total": 12, "result": [f"i{n}" for n in range(12)], "url": "u", "cached": False}

        def fetch(self, ids, query_id, max_wait=None):
            return [{"listing": {"price": {"amount": n, "currency": "exalted"}}} for n in (30, 10, 20, 50, 40)]

    entry = {"kind": "unique", "name": "Blackflame", "base": "Amethyst Ring", "category": "accessory.ring"}
    link = trade_link(Client(), "Test", entry, rates={"exalted": 0.01})
    assert link["asking"] == {"cheapest": 0.1, "typical": 0.3, "listings": 5}
    assert "asking" not in trade_link(Client(), "Test", entry)  # no rates, no fetch


def test_jewel_entries_carry_the_crafting_breakdown_and_a_base_search(data) -> None:
    from builder.search.rares import boosted, rare_text

    jewels = pools_for("endgame", "Jewel")
    effect_affix, effect = jewels.crafted("Suffix")
    pool = jewels.pool("Emerald")
    suffixes = [a for a in pool if a.type == "Suffix"][:3]
    prefix = next(a for a in pool if a.type == "Prefix")
    lines = [boosted(l, effect) for a in suffixes for l in a.lines] + list(prefix.lines) + list(effect_affix.lines)
    item = {"name": "Rare Jewel", "base": "Emerald", "type": "Jewel", "rare": True, "unique": False, "price": 2.2,
            "confidence": "estimate", "affixes": lines, "runes": []}
    stats = {f"explicit.stat_{h}": {"text": "#% increased X", "type": "explicit"}
             for a in suffixes for m in data.mods["Jewel"] if m["id"] == a.id for h in m.get("tradeHashes") or {}}
    shop = shopping_list({"request": {"budget": {"gear_tier": "endgame", "divines": 20}}, "gear": {"Jewel 1": item}},
                         data, {"materials": MATERIALS}, stats)
    entry = shop["items"][0]
    assert entry["basis"] == "crafting" and entry["price"] > 20 and entry["craft"]["steps"]
    assert entry["price"] == pytest.approx(shop["total"])
    base_query = ladder(entry, "base")[0][1]["query"]
    assert base_query["type"] == "Emerald" and len(base_query["stats"][0]["filters"]) == 2


def test_a_focus_takes_one_spell_element(data) -> None:
    """The user's 2026-10-24 focus had fire, cold and lightning damage prefixes, which can't roll
    together: each adds tags that give the other elements zero spawn weight."""
    from builder.search.rares import conflicts

    pools = pools_for("endgame")
    pool = pools.pool("Antler Focus")
    fire = next(a for a in pool if a.group == "FireDamageWeaponPrefix")
    cold = next(a for a in pool if a.group == "ColdDamageWeaponPrefix")
    resist = next(a for a in pool if a.group == "ColdResistance")
    assert pools.choices(cold, [fire]) == [] and pools.choices(fire, [cold]) == []
    assert pools.choices(resist, [fire]) and pools.choices(fire, [resist])
    by_id = {m["id"]: m for m in data.mods["Item"]}
    impossible = [by_id[fire.id], by_id[cold.id], by_id[resist.id]]
    assert {(a["id"], b["id"]) for a, b in conflicts(impossible)} == {(fire.id, cold.id), (cold.id, fire.id)}
    assert conflicts([by_id[fire.id], by_id[resist.id]]) == []


def test_calibration_packages_use_bases_the_game_drops(data) -> None:
    from builder.data.rules import rules
    from builder.report.calibrate import generic_packages

    packages = generic_packages(data, {f"explicit.stat_{h}": {"text": "#", "type": "explicit"}
                                       for m in data.mods["Item"] + data.mods["Jewel"] for h in m.get("tradeHashes") or {}})
    bases = {b["name"]: b for b in data.bases}
    assert packages and all(rules().normal_base_ok(bases[p["base"]]) for p in packages)
