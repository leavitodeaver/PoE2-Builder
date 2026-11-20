"""SEARCH31: the Altered Collarbone (Breach rows on Amulets / Rings / Belts) and Preserved Cranium (jewel rows on rare
Jewels) Desecration routes (builder/search/desecrated.py; RESEARCH34 R33-22), both OFF by default."""
from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import desecrated
from builder.search.context import Context, make_payload


@pytest.fixture(scope="module")
def data():
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


@pytest.fixture(scope="module")
def ctx(data):
    return Context(make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices())))


@pytest.fixture(scope="module")
def engine(data):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_both_routes_are_off_by_default_and_their_switches_turn_them_on(monkeypatch):
    for name in ("POE2_SEARCH_DESECRATED_BREACH", "POE2_SEARCH_DESECRATED_JEWEL"):
        monkeypatch.delenv(name, raising=False)
    assert not desecrated.breach_enabled() and not desecrated.cranium_enabled()
    monkeypatch.setenv("POE2_SEARCH_DESECRATED_BREACH", "1")
    assert desecrated.breach_enabled() and not desecrated.cranium_enabled()
    monkeypatch.setenv("POE2_SEARCH_DESECRATED_JEWEL", "1")
    assert desecrated.cranium_enabled()


def test_each_row_is_given_to_the_bone_its_spawn_tag_names(data):
    rows = {m["id"]: m for m in data.mods["Desecrated"]}
    routes = {i: desecrated.route_of(m) for i, m in rows.items()}
    breach = [i for i, r in routes.items() if r == "breach"]
    assert len(breach) == 36 and all(i.startswith("GenesisTree") for i in breach)  # (RESEARCH34 R33-22: 36 Breach rows)
    assert routes["AbyssModJewelPrefixAbyssalWastingEffect"] == "cranium"
    assert routes["AbyssMod1HMaceAmanamuPrefixDamageAgainstFullyArmourBrokenEnemies"] == "bone"
    assert routes["UniqueHeartSuffixAilmentChance"] == ""  # (Heart of the Well's own rows: never from currency)
    assert desecrated.label(rows[breach[0]]) == "Breach"


def _base(ctx, kind):
    return max((b for b in ctx.data.bases if b["type"] == kind), key=lambda b: (b.get("req") or {}).get("level") or 0)


def test_the_breach_pool_holds_only_jewellerys_breach_rows(ctx):
    from builder.search.rares import AffixPools

    pools = AffixPools(ctx, "Desecrated", "breach")
    sizes = {}
    for kind in ("Amulet", "Ring", "Belt"):
        pool = pools.pool(_base(ctx, kind)["name"])
        assert pool and all(a.desecrated == "Breach" and a.id.startswith("GenesisTree") for a in pool)
        sizes[kind] = len(pool)
    assert sizes["Amulet"] < sizes["Ring"]  # (the game: Amulet 4 rows, Ring 16, Belt 16; groups can merge tiers)
    assert all(a.id.startswith("GenesisTreeRing") for a in pools.pool(_base(ctx, "Ring")["name"]))
    for kind in ("Gloves", "Jewel"):  # (the tag is added on jewellery only)
        assert pools.pool(_base(ctx, kind)["name"]) == []


def test_the_cranium_pool_holds_jewel_rows_on_jewels_only(ctx):
    from builder.search.rares import AffixPools

    pools = AffixPools(ctx, "Desecrated", "cranium")
    ruby = pools.pool("Ruby")
    assert ruby and all(a.desecrated == "Abyss jewel" for a in ruby)
    assert {a.type for a in ruby} == {"Prefix", "Suffix"}
    assert pools.pool(_base(ctx, "Ring")["name"]) == []
    # the plain bone pool is unchanged: lord rows only
    assert all(a.desecrated in desecrated.LORD_OMEN for a in AffixPools(ctx, "Desecrated").pool(_base(ctx, "Amulet")["name"]))


PRICES = {"Altered Collarbone": 5.43, "Preserved Cranium": 28.99, "Preserved Collarbone": 0.26,
          "Omen of the Blackblooded": 0.004, "Omen of Dextral Necromancy": 0.0045, "Omen of Sinistral Necromancy": 0.001,
          "Omen of Light": 10.66, "Orb of Annulment": 0.29, "Omen of Abyssal Echoes": 0.08}


def test_the_altered_collarbone_route():
    """The reveal draws 3 from the side's ordinary rows + the Breach and lord rows (N); no lord Omen; Echoes once."""
    affix = SimpleNamespace(desecrated="Breach", type="Suffix", lines=("Storm Chaser's line",))
    n = 40 + 20
    per = 5.43 + 0.0045
    p, p2 = 1 - (1 - 1 / n) ** 3, 1 - (1 - 1 / n) ** 6
    want = min(1.0 + per / p + (1 / p - 1) * 1.0, 1.0 + (per + 0.08) / p2 + (1 / p2 - 1) * 1.0)
    price, steps = desecrated.craft_price("Ring", 1.0, affix, 0, 20, 40, PRICES, "breach")
    assert price == pytest.approx(want)
    assert any("Altered Collarbone" in s for s in steps) and not any("Omen of the" in s for s in steps)
    assert desecrated.craft_price("Gloves", 1.0, affix, 0, 20, 40, PRICES, "breach")[0] is None
    assert desecrated.craft_price("Ring", 1.0, affix, 0, 20, 40, {**PRICES, "Altered Collarbone": None}, "breach")[0] is None


def test_the_preserved_cranium_route():
    affix = SimpleNamespace(desecrated="Abyss jewel", type="Suffix", lines=("a Lightless line",))
    price, steps = desecrated.craft_price("Jewel", 0.1, affix, 0, 3, 21, PRICES, "cranium")
    n = 24
    p2 = 1 - (1 - 1 / n) ** 6
    p = 1 - (1 - 1 / n) ** 3
    want = min(0.1 + (28.99 + 0.0045) / p + (1 / p - 1) * 0.1, 0.1 + (28.99 + 0.0845) / p2 + (1 / p2 - 1) * 0.1)
    assert price == pytest.approx(want) and any("Preserved Cranium" in s for s in steps)
    assert desecrated.craft_price("Jewel", 0.1, affix, 0, 3, 21, PRICES, "bone")[0] is None  # (no plain bone on a jewel)
    assert desecrated.craft_price("Ring", 0.1, affix, 0, 3, 21, PRICES, "cranium")[0] is None


def test_the_payload_carries_both_new_bones():
    price = SimpleNamespace(divine=1.0)
    prices = SimpleNamespace(exchange={"Altered Collarbone": price, "Preserved Cranium": price, "Chaos Orb": price})
    assert set(desecrated.payload_prices(prices)) == {"Altered Collarbone", "Preserved Cranium"}


def test_the_breach_and_cranium_trials_offer_one_desecrated_affix(engine, monkeypatch):
    from builder import clock
    from builder.search.gear import affix_limits
    from builder.search.inner import Search
    from builder.search.jewels import JEWEL_LIMIT
    from builder.search.rares import rare_text
    from builder.search.skeletons import enumerate_skeletons

    monkeypatch.setenv("POE2_SEARCH_DESECRATED", "0")  # (the plain bone route is on by default: this tests the Breach one)
    monkeypatch.delenv("POE2_SEARCH_DESECRATED_BREACH", raising=False)
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    gear = search.gear
    gear.lam = 0.0
    search.ctx.desecration_prices = {name: 0.001 for name in desecrated.MATERIALS}
    ring = gear.build_rare("Ring 1", clock.now() + 120)
    if ring is None:
        pytest.skip("no rare ring on this build")
    base = search.ctx.bases_by_name[ring.base]
    pools, level = gear.pools, gear.pools.item_level
    pool = pools.pool(base["name"])
    empty = gear.deltas("Ring 1", [rare_text("Ring 1", base, level, [])])[0][0]
    scores = gear.deltas("Ring 1", [rare_text("Ring 1", base, level, [a]) for a in pool])
    worthless = [a for (s, _), a in zip(scores, pool) if abs(s - empty) < 1e-9]
    if not worthless:
        pytest.skip("no worthless ring modifier on this build")
    monkeypatch.setenv("POE2_SEARCH_DESECRATED_BREACH", "1")
    option = gear.desecrated_trial("Ring 1", base, ring, level, pools, affix_limits(base), [worthless[0]], empty, 1e9,
                                   clock.now() + 180)
    print("PROBE breach", option.affixes if option else None, option.price if option else None,
          option.craft_route if option else None)
    breach_lines = {l for a in gear.desecrated_pools("breach").pool(base["name"]) for l in a.lines}
    if option is None:
        pytest.skip("no Breach ring row scores on Spark")
    assert option.craft_route and "Desecrat" in option.confidence
    assert sum(1 for line in option.affixes if line in breach_lines) == 1
    assert any("Altered Collarbone" in s for s in option.craft_route)
    # The jewel: a Preserved Cranium row on the plain 2 + 2 rare, at near-free materials
    monkeypatch.setenv("POE2_SEARCH_DESECRATED_JEWEL", "1")
    jewels = search.jewels
    jbase = search.ctx.bases_by_name["Sapphire"]
    jpool = jewels.pools.pool("Sapphire")
    sockets = jewels.sockets()
    if not sockets:  # (as test_radius_jewels' fixture: the nearest socket, allocated)
        near = engine.call("frontier", maxDistance=12, sockets="only")["nodes"]
        if near:
            search.build.allocate(add=[min(near, key=lambda n: n["distance"])["id"]])
        sockets = jewels.sync()
    if not sockets:
        pytest.skip("no allocated jewel socket on this skeleton")
    slot = jewels.slot(sockets[0])
    jempty = search.build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", jbase, level, [])}])[0][0]
    found = jewels.cranium_trial(slot, jbase, [], jempty, 1e9, clock.now() + 180)
    print("PROBE cranium", found[1].affixes if found else None, found[1].price if found else None)
    if found is None:
        pytest.skip("no Cranium jewel row scores on Spark")
    value, option = found
    assert value > jempty and option.craft_route and any("Preserved Cranium" in s for s in option.craft_route)
    cranium_lines = {l for a in jewels.cranium_pools.pool("Sapphire") for l in a.lines}
    assert sum(1 for line in option.affixes if line in cranium_lines) == 1
    assert len(option.affixes) <= sum(JEWEL_LIMIT.values()) * 2 and jpool


# Influence runes at trade-site prices (SEARCH31; builder/search/influence.py MARKET_SAMPLES) -------------------------------

def test_the_users_chaos_spam_route_is_the_headline():
    """The user, 20 Nov ~03:00: spam Chaos Orbs until the mod lands, then keep it ("fracture that one to lock it in, or
    you can use omens of whittling"), priced by RESEARCH35 R35-17: protect the landed mod per try (Whittling while it sits
    above the item's lowest level; the side Erasure Omen while it sits on the other side), fracture at 1 / N only where
    nothing protects, the fracture route a row; the Exalt / Annulment loop a row; the trade samples a cross-check row."""
    from builder.search import influence

    assert not influence.supplied("Thrud's Might")
    a1 = SimpleNamespace(influence="decay", lines=("Ignite",), odds=1 / 200, side_odds=1 / 100, type="Prefix", level=75)
    a2 = SimpleNamespace(influence="decay", lines=("Duration",), odds=1 / 250, side_odds=1 / 120, type="Suffix", level=75)
    plain = SimpleNamespace(influence="", lines=("Life",), odds=0.01, side_odds=0.02, type="Prefix", level=30)
    prices = {influence.CHAOS: 0.1, influence.EXALT: 0.0015, influence.ANNUL: 0.29, "Omen of Sinistral Exaltation": 0.15,
              "Omen of Dextral Exaltation": 0.06, "Fracturing Orb": 9.87, "Omen of Whittling": 13.94,
              "Omen of Sinistral Erasure": 20.35, "Omen of Dextral Erasure": 13.58}
    price, steps, how = influence.price("Katla's Gloom", 0.05, 0.08, 0.4, prices, [a1])
    assert how.endswith("Chaos Orbs") and price == pytest.approx(0.4 + 200 * 0.1)  # (alone: nothing to keep safe)
    assert any(s.startswith("row: the Exalted Orb") for s in steps) and any(s.startswith("cross-check") for s in steps)
    # the other mod made with the level-75 influence mod kept safe by Whittling (one Omen, LOW) + its market price
    price, _, _ = influence.price("Katla's Gloom", 0.05, 0.08, 0.4, prices, [a1, plain])
    assert price == pytest.approx(0.4 + 20 + 0.05 + 13.94)
    # a second influence mod on the other side: Dextral Erasure + Chaos (1 in 120) beats Whittling + Chaos (1 in 250)
    price, steps, _ = influence.price("Katla's Gloom", 0.05, 0.08, 0.4, prices, [a1, a2])
    assert price == pytest.approx(0.4 + 20 + 120 * (0.1 + 13.58))
    fractured = 0.4 + 20 + 4 * 9.87 + 3 * 20.4 + 250 * 0.1  # (the row: fracture at 1 in 4, a miss redoes the item)
    assert any(s.startswith("row: lock each landed mod with a Fracturing Orb") and f"{fractured:.3f}" in s for s in steps)
    # same side, and the landed mod the item's lowest level (45): nothing protects it, so it's fractured first
    low = SimpleNamespace(influence="decay", lines=("Low",), odds=1 / 200, side_odds=1 / 100, type="Prefix", level=45)
    a3 = SimpleNamespace(influence="decay", lines=("Other",), odds=1 / 300, side_odds=1 / 150, type="Prefix", level=75)
    price, steps, _ = influence.price("Katla's Gloom", 0.05, 0.08, 0.4, prices, [low, a3])
    assert price == pytest.approx(0.4 + 20 + (4 * 9.87 + 3 * 20.4) + 300 * 0.1)
    assert any("Fracturing Orb (1 in 4" in s for s in steps)
    assert influence.price("Katla's Gloom", 0.05, 0.08, 0.4, {}, [a1])[0] is None


def test_time_lost_jewels_draw_only_their_own_cranium_rows(ctx):
    """RESEARCH35 R35-11: a Time-Lost jewel takes only its 12 radius-jewel Desecrated rows, never the regular ones."""
    from builder.search.rares import AffixPools

    pools = AffixPools(ctx, "Desecrated", "cranium")
    for name in ("Time-Lost Ruby", "Time-Lost Emerald", "Time-Lost Sapphire"):
        pool = pools.pool(name)
        assert pool and all("RadiusJewel" in a.id for a in pool)
    assert not any("RadiusJewel" in a.id for a in pools.pool("Ruby"))
