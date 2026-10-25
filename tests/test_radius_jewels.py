"""Jewels that change the passive tree: allocation without a connection (Controlled Metamorphosis,
From Nothing), the tree's validity check, variants of tree-changing uniques, and the search
picking such jewels when they pay (builder/search/jewels.py, radius.py; engine/bridge.lua)."""

from __future__ import annotations

import faulthandler
import json
import time
from functools import cache

import pytest

from builder import ROOT
from builder.data import GameData
from builder.search.radius import compact, miscalculated, variants_of, with_variants

REFERENCE = ROOT / "benchmarks" / "reference"


@cache
def uniques() -> dict[str, dict]:
    try:
        data = GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return {u["name"]: u for u in data.uniques}


def raw(name: str) -> str:
    return uniques()[name]["raw"]


# Variants, from the uniques' own text ------------------------------------------------------------

def test_variant_selections_follow_the_uniques_text():
    darkness = variants_of(raw("Against the Darkness"))
    assert darkness.slots == ("Selected Variant", "Selected Alt Variant") and len(darkness.names) == 23
    megalomaniac = variants_of(raw("Megalomaniac"))
    assert len(megalomaniac.slots) == 3 and len(megalomaniac.usable) == len(megalomaniac.names)
    # PoB's "Everything (QoL Test Variant)" switches on every keystone: no such item drops
    nothing = variants_of(raw("From Nothing"))
    assert len(nothing.usable) == len(nothing.names) - 1
    assert all("Test Variant" not in nothing.names[i - 1] for i in nothing.usable)


def test_compact_text_keeps_only_the_chosen_lines_and_recounts_implicits():
    text = raw("Megalomaniac")
    variants = variants_of(text)
    lines = compact(text, (5, 6, 7)).split("\n")
    allocates = [line for line in lines if "Allocates" in line]
    assert allocates == [f"{{enchant}}Allocates {variants.names[i - 1]}" for i in (5, 6, 7)]
    assert "Implicits: 3" in lines and not any(line.startswith("Variant:") for line in lines)
    chosen = with_variants(text, variants, (5, 6, 7)).split("\n")
    assert {"Selected Variant: 5", "Selected Alt Variant: 6", "Selected Alt Variant Two: 7"} <= set(chosen)


def test_timeless_jewels_are_never_trusted_without_seed_tables():
    assert miscalculated({"conquers": True, "seedData": False})
    assert miscalculated({"conquers": True, "seedData": True}) is None
    assert miscalculated({"radius": {"label": "Small"}}) is None


# The bridge: what PoB allows, priced right, and checked ------------------------------------------

@pytest.fixture(scope="module")
def engine():
    uniques()
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def load_reference(engine, name: str = "2b75b") -> list[int]:
    from builder.pob import codes

    engine.call("loadXml", xml=codes.decode((REFERENCE / f"{name}.txt").read_text(encoding="utf-8").strip()))
    return [n["id"] for n in engine.call("allocatedNodes")["nodes"] if n["type"] == "Socket"]


def controlled_metamorphosis(ring: int = 7) -> str:
    text = raw("Controlled Metamorphosis")
    return with_variants(text, variants_of(text), (ring,))


def test_jewel_traits_come_from_pobs_parse(engine):
    traits = engine.call("jewelTraits", raws=[controlled_metamorphosis(), raw("From Nothing"), raw("Megalomaniac"),
                                              raw("Undying Hate")])["traits"]
    metamorphosis, nothing, megalomaniac, timeless = traits
    assert metamorphosis["unconnected"] and metamorphosis["radius"]["inner"] > 0  # a ring
    assert len(nothing["keystones"]) > 20 and nothing["radius"]["label"] == "Small"
    assert megalomaniac["grants"] == 3 and not megalomaniac.get("radius")
    assert timeless["conquers"] and miscalculated(timeless)


def test_a_jewel_freed_node_costs_one_point_and_passes_the_tree_check(engine):
    sockets = load_reference(engine)
    assert engine.call("treeCheck")["ok"]
    engine.call("equip", slot=f"Jewel {sockets[0]}", raw=controlled_metamorphosis())
    frontier = engine.call("frontier", maxDistance=1, maxDistanceByType={"Notable": 1})["nodes"]
    freed = [n for n in frontier if n.get("unconnected")]
    assert freed and all(n["path"] == [n["id"]] and n["distance"] == 1 for n in freed)
    # Far ones too: without the jewel, the same node is several points away
    target = freed[0]["id"]
    with_path, alone = engine.call("nodeDeltas", cases=[{"add": [target], "addPaths": True}, {"add": [target]}],
                                   fields=["Life", "FullDPS", "TotalEHP"])["results"]
    assert with_path == alone
    used = engine.call("pointBudget")["used"]
    engine.call("allocate", add=[target])
    assert engine.call("pointBudget")["used"] == used + 1
    check = engine.call("treeCheck")
    assert check["ok"] and target in check["unconnected"]
    jewels = engine.call("radiusJewels")["jewels"]
    held = next(j for j in jewels if j["socket"] == sockets[0])
    assert held["unconnected"] == [target] and held["allowsUnconnected"]
    # Taking the jewel out drops what the tree held through it (PoB prunes it)
    engine.call("equip", slot=f"Jewel {sockets[0]}", raw=None)
    assert target not in {n["id"] for n in engine.call("allocatedNodes")["nodes"]}
    assert engine.call("treeCheck")["ok"]


def test_the_tree_check_fails_loudly_on_a_passive_nothing_reaches(engine):
    load_reference(engine)
    far = next(n for n in engine.call("frontier", maxDistance=12, maxDistanceByType={"Notable": 12})["nodes"]
               if n["distance"] >= 8 and n["type"] == "Notable")
    # PoB itself never allocates it (nor keeps it through a code load): force it, as a bug would
    engine.lua.run(f"local n = build.spec.nodes[{far['id']}]; n.alloc = true; build.spec.allocNodes[{far['id']}] = n",
                   "force")
    check = engine.call("treeCheck")
    assert not check["ok"] and [b["id"] for b in check["broken"]] == [far["id"]]
    pruned = engine.call("treeCheck", prune=True)
    assert pruned["pruned"] and engine.call("treeCheck")["ok"]


def test_compact_text_scores_like_the_full_unique(engine):
    sockets = load_reference(engine)
    text = raw("Against the Darkness")
    variants = variants_of(text)
    slot = f"Jewel {sockets[0]}"
    cases = [{"slot": slot, "raw": compact(text, (3, 5))}, {"slot": slot, "raw": with_variants(text, variants, (3, 5))}]
    short, full = engine.call("itemDeltas", cases=cases, fields=["FullDPS", "Life", "TotalEHP"])["results"]
    assert short == full


def test_facts_carry_the_radius_jewels_for_the_result_tree(engine):
    load_reference(engine, "2b75a")  # the user's build with Heroic Tragedy
    facts = engine.call("facts", fields=[])
    tragedy = next(j for j in facts["radiusJewels"] if j["name"] == "Heroic Tragedy")
    assert tragedy["radius"]["label"] == "Very Large" and tragedy["affected"] and tragedy["conquers"]
    assert facts["treeCheck"]["ok"]


# The search -------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def grown(engine):
    """An Ice Shot search with its tree grown and the price feed's uniques on offer."""
    from builder.data.prices import PriceFeed
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    try:
        table = PriceFeed().table(refresh=False)
    except Exception as error:  # no cached prices: nothing to buy
        pytest.skip(f"no price data ({error})")
    request = BuildRequest(main_skill="Ice Shot", budget={"divines": 50})
    payload = {**make_payload(request, table), "unique_versions": {}}  # the feed's prices only
    ctx = Context(payload)
    names = {o.name for o in ctx.item_options.get("Jewel", [])}
    if not {"From Nothing", "Megalomaniac"} <= names:
        pytest.skip("From Nothing or Megalomaniac isn't priced")
    skeleton = enumerate_skeletons(ctx, ["Ice Shot"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.tree.grow(time.perf_counter() + 60)
    return search


def test_the_search_takes_what_a_jewel_unlocks_when_it_pays(grown, monkeypatch):
    import builder.search.jewels as jewels

    monkeypatch.setattr(jewels, "UNLOCK_SCREENS", 200)  # every socket and variant in one round
    build = grown.build
    before = build.score
    assert grown.jewels.unlock_round(time.perf_counter() + 300)
    assert build.score > before
    check = build.call("treeCheck")
    assert check["ok"] and check["unconnected"]
    held = [j for j in build.call("radiusJewels")["jewels"] if j["unconnected"]]
    assert held and held[0]["allowsUnconnected"]
    option = build.equipped[f"Jewel {held[0]['socket']}"]
    assert option.variants and option.confidence == "low"  # the chosen version, priced as any version


def test_megalomaniac_gets_the_notables_that_score_best(grown):
    jewels = grown.jewels
    socket = jewels.sync()[0]
    slot = jewels.slot(socket)
    option = next(o for o in jewels.uniques(slot) if o.name == "Megalomaniac")
    chosen = jewels.version(slot, option, time.perf_counter() + 120)
    assert len(chosen.variants) == 3 and len(set(chosen.variants)) == 3
    default, best = grown.build.item_deltas([{"slot": slot, "raw": option.raw}, {"slot": slot, "raw": chosen.raw}])
    assert best[0] > default[0]
    # A banned passive is never granted either
    banned = grown.build.ctx.compiled.filters.forbidden_passives
    banned.add(chosen.variants[0])
    try:
        jewels.full_rounds += 1  # rescored on the next full round
        assert chosen.variants[0] not in jewels.version(slot, option, time.perf_counter() + 120).variants
    finally:
        banned.discard(chosen.variants[0])


def test_priced_versions_cost_their_trade_price_and_unlisted_ones_are_not_offered():
    from types import SimpleNamespace

    from builder.data import unique_versions
    from builder.search.context import ItemOption
    from builder.search.jewels import Jewels

    raw = next(u["raw"] for u in GameData().uniques if u["name"] == "From Nothing")
    option = ItemOption("From Nothing", "Diamond", "Jewel", None, raw, True, 0.097, "high", 0)
    now = time.time()
    table = {unique_versions.key("From Nothing", ["Hollow Palm Technique"]):
             {"name": "From Nothing", "variants": ["Hollow Palm Technique"], "listed": 86, "price": 29.0, "priced_at": now},
             unique_versions.key("From Nothing", ["Iron Reflexes"]):
             {"name": "From Nothing", "variants": ["Iron Reflexes"], "listed": 0, "price": None, "priced_at": now}}
    jewels = Jewels.__new__(Jewels)
    jewels.ctx = SimpleNamespace(ssf=False, unique_versions=table)
    versions = {v.variants[0]: v for v in jewels.unlock_versions(option)}
    assert versions["Hollow Palm Technique"].price == 29.0
    assert versions["Hollow Palm Technique"].confidence.startswith("trade, this version (86 listed, ")
    assert "Iron Reflexes" not in versions  # nobody sells it
    assert versions["Chaos Inoculation"].price == 0.097 and versions["Chaos Inoculation"].confidence == "low"
    assert len(versions) == len(variants_of(raw).usable) - 1
    # Self-found: prices don't apply, and any version can drop
    jewels.ctx = SimpleNamespace(ssf=True, unique_versions=table)
    assert len(jewels.unlock_versions(option)) == len(variants_of(raw).usable)
