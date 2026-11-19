"""SEARCH30: influence-rune affixes on rares (builder/search/influence.py; plan scratch/SEARCH28/plan_unrolled_mods.md part 2;
RESEARCH32 R-b: the rune goes in first and its tag lets the influence affixes roll; R-e: priced by the craft route)."""
from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import desecrated, influence
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


def test_influence_is_off_by_default_and_the_switch_turns_it_on(monkeypatch):
    monkeypatch.delenv("POE2_SEARCH_INFLUENCE", raising=False)
    assert not influence.enabled()
    monkeypatch.setenv("POE2_SEARCH_INFLUENCE", "1")
    assert influence.enabled()


def test_each_influence_rune_adds_its_tag_read_from_its_own_line(data):
    assert influence.rune_tags(data.runes) == {
        "Vorana's Carnage": "berserking", "Kolr's Hunt": "marksman", "Katla's Gloom": "decay",
        "Uhtred's Sidereus": "chronomancy", "Medved's Tending": "soul", "Thrud's Might": "destruction"}


def test_the_tag_widens_only_that_items_pool_and_marks_its_affixes(ctx):
    from builder.search.rares import AffixPools

    pools = AffixPools(ctx)
    gloves = next(b["name"] for b in ctx.data.bases if b["type"] == "Gloves" and "gloves" in (b.get("tags") or [])
                  and ((b.get("req") or {}).get("level") or 0) >= 45)
    plain = pools.pool(gloves)
    wide = pools.pool(gloves, ("marksman",))
    assert not any(a.influence for a in plain)
    marked = [a for a in wide if a.influence]
    assert marked and all(a.influence == "marksman" for a in marked)
    assert any("Projectile Skills" in line for a in marked for line in a.lines)
    assert {a.id for a in wide if not a.influence} == {a.id for a in plain}  # (the base's own pool is unchanged)
    assert pools.pool(gloves) is plain  # (cached apart)
    # the widened pool's odds share the spawn weight with the influence affixes (a Chaos Orb's chance)
    ordinary = next(a for a in plain if a.odds > 0)
    assert next(a for a in wide if a.id == ordinary.id).odds < ordinary.odds


def test_the_craft_route_price_and_no_price_no_offer():
    affix = SimpleNamespace(odds=0.02, lines=("+1 to Level of all Projectile Skills",))
    price, steps = influence.craft_price(0.1, 1.5, 0.1, [affix])
    assert price == pytest.approx(0.1 + 1.5 + 50 * 0.1) and len(steps) == 3
    assert influence.craft_price(0.1, None, 0.1, [affix])[0] is None  # (no rune price)
    assert influence.craft_price(0.1, 1.5, None, [affix])[0] is None  # (no Chaos Orb price)
    assert influence.craft_price(0.1, 1.5, None, [])[0] == pytest.approx(1.6)


def test_influence_runes_stay_off_jewellery(ctx):
    from builder.search.rares import RuneCatalog

    tags = influence.rune_tags(ctx.data.runes)
    catalog = RuneCatalog(ctx, {name: 0.1 for name in tags})
    ring = next(b for b in ctx.data.bases if b["type"] == "Ring")
    gloves = next(b for b in ctx.data.bases if b["type"] == "Gloves")
    assert not set(catalog.candidates(ring, False)) & set(tags)
    assert {"Kolr's Hunt", "Katla's Gloom"} <= set(catalog.candidates(gloves, False))
    assert not set(catalog.candidates(gloves, True)) & set(tags)  # (never on uniques: canSocketInUniqueItems false)


@pytest.fixture(scope="module")
def engine(data):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_build_rare_offers_kolrs_gloves_when_a_marksman_affix_wins(engine, monkeypatch):
    from builder import clock
    from builder.data import rare_prices
    from builder.search.gear import affix_limits
    from builder.search.inner import Search
    from builder.search.rares import rare_text
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    gear = search.gear
    tags = gear.influence_tags
    for name in tags:
        gear.runes.prices[name] = 0.001
    monkeypatch.setitem(rare_prices.active().materials, influence.CHAOS, 0.0001)
    gear.lam = 0.0
    plain = gear.build_rare("Gloves", clock.now() + 120)
    assert plain is not None and not plain.bound  # (the switch is off: no trial)
    base = search.ctx.bases_by_name[plain.base]
    pools, level = gear.pools, gear.pools.item_level
    pool = pools.pool(base["name"])
    empty = gear.deltas("Gloves", [rare_text("Gloves", base, level, [])])[0][0]
    scores = gear.deltas("Gloves", [rare_text("Gloves", base, level, [a]) for a in pool])
    worthless = [a for (s, _), a in zip(scores, pool) if abs(s - empty) < 1e-9]
    if not worthless:
        pytest.skip("no worthless gloves modifier on this build")
    chosen = [worthless[0]]
    option = gear.influence_trial("Gloves", base, plain, level, pools, affix_limits(base), chosen, empty, 1e9,
                                  clock.now() + 180)
    print("PROBE influence", option.bound if option else None, option.affixes if option else None,
          option.price if option else None, option.craft_route if option else None)
    assert option is not None and option.bound and option.bound == option.runes
    assert tags[option.bound[0]] in ("marksman", "decay")  # (Kolr's Hunt or Katla's Gloom: the Gloves runes)
    assert f"Rune: {option.bound[0]}" in option.raw
    wide = pools.pool(base["name"], (tags[option.bound[0]],))
    influenced = {l for a in wide if a.influence for l in a.lines}
    assert any(line in influenced for line in option.affixes)
    assert option.describe()["bound_runes"] == list(option.bound) and option.craft_route
    # its one socket is the bound rune's: filling sockets keeps it and adds nothing
    assert gear.socketed("Gloves", option, clock.now() + 60) is option
    # Desecrated: a lord affix on an amulet in place of a worthless modifier, priced by the craft route
    search.ctx.desecration_prices = {name: 0.001 for name in desecrated.MATERIALS}
    amulet = gear.build_rare("Amulet", clock.now() + 120)
    if amulet is None:
        pytest.skip("no rare amulet on this build")
    abase = search.ctx.bases_by_name[amulet.base]
    apool = pools.pool(abase["name"])
    aempty = gear.deltas("Amulet", [rare_text("Amulet", abase, level, [])])[0][0]
    ascores = gear.deltas("Amulet", [rare_text("Amulet", abase, level, [a]) for a in apool])
    aworthless = [a for (s, _), a in zip(ascores, apool) if abs(s - aempty) < 1e-9]
    if not aworthless or gear.desecrated_pools.item_level < 65:
        pytest.skip("no worthless amulet modifier, or item level under 65")
    option = gear.desecrated_trial("Amulet", abase, amulet, level, pools, affix_limits(abase), [aworthless[0]], aempty,
                                   1e9, clock.now() + 180)
    print("PROBE desecrated", option.affixes if option else None, option.price if option else None,
          option.craft_route if option else None)
    assert option is not None and option.craft_route and "Desecrat" in option.confidence
    lord_lines = {l for a in gear.desecrated_pools.pool(abase["name"]) for l in a.lines}
    assert sum(1 for line in option.affixes if line in lord_lines) == 1


# Desecrated (lord) affixes (builder/search/desecrated.py) ------------------------------------------------------------------

def test_desecrated_is_off_by_default_and_the_switch_turns_it_on(monkeypatch):
    from builder.search import desecrated

    monkeypatch.delenv("POE2_SEARCH_DESECRATED", raising=False)
    assert not desecrated.enabled()
    monkeypatch.setenv("POE2_SEARCH_DESECRATED", "1")
    assert desecrated.enabled()


def test_a_rows_lord_is_read_from_its_id_and_tags_not_its_weight_keys(data):
    from builder.search import desecrated

    rows = {m["id"]: m for m in data.mods["Desecrated"]}
    quirk = rows["AbyssMod1HMaceAmanamuPrefixDamageAgainstFullyArmourBrokenEnemies"]
    assert "ulaman_mod" in quirk["weightKey"] and desecrated.lord(quirk) == "Amanamu"
    lords = [desecrated.lord(m) for m in rows.values() if m.get("type") in ("Prefix", "Suffix")]
    assert sum(1 for x in lords if x) >= 190 and set(lords) == {"", "Amanamu", "Kurgal", "Ulaman"}


def test_the_desecrated_pool_holds_lord_affixes_marked_with_their_lord(ctx):
    from builder.search.rares import AffixPools

    pools = AffixPools(ctx, "Desecrated")
    if pools.item_level < 65:
        pytest.skip("this gear tier's item level is under the lord mods' 65")
    amulet = max((b for b in ctx.data.bases if b["type"] == "Amulet"), key=lambda b: (b.get("req") or {}).get("level") or 0)
    pool = pools.pool(amulet["name"])
    assert pool and all(a.desecrated in ("Amanamu", "Kurgal", "Ulaman") for a in pool)
    assert any(a.desecrated == "Kurgal" and "Quality of all Skills" in a.lines[0] for a in pool)  # (2b75b's own suffix)


def test_the_desecration_craft_route():
    from builder.search import desecrated

    prices = {"Preserved Collarbone": 0.26, "Ancient Collarbone": 9.0, "Preserved Rib": 0.015, "Ancient Rib": 6.0,
              "Omen of the Blackblooded": 0.004, "Omen of Dextral Necromancy": 0.0045,
              "Omen of Sinistral Necromancy": 0.001, "Omen of Light": 10.66, "Orb of Annulment": 0.29}
    affix = SimpleNamespace(desecrated="Kurgal", type="Suffix", lines=("+(3-5)% to Quality of all Skills",))
    price, steps = desecrated.craft_price("Amulet", 1.0, affix, 6, prices)  # (3 of 6: two tries)
    assert price == pytest.approx(1.0 + 2 * (0.26 + 0.0045 + 0.004) + 1 * 1.0) and len(steps) == 4
    # Armour: no lord Omen; a failed try is cleared by a fresh rare when that's cheaper than Light + Annulment
    price, _ = desecrated.craft_price("Gloves", 0.5, affix, 3, prices)
    assert price == pytest.approx(0.5 + 0.015 + 0.0045)
    assert desecrated.craft_price("Amulet", 1.0, affix, 6, {**prices, "Omen of the Blackblooded": None})[0] is None
    assert desecrated.craft_price("Charm", 1.0, affix, 6, prices)[0] is None
