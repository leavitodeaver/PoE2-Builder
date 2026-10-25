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
    ctx = Context(make_payload(request, table))
    names = {o.name for o in ctx.item_options.get("Jewel", [])}
    if not {"From Nothing", "Megalomaniac"} <= names:
        pytest.skip("From Nothing or Megalomaniac isn't priced")
    skeleton = enumerate_skeletons(ctx, ["Ice Shot"])[0][0]
    search = Search(engine, None, make_payload(request, table), skeleton.as_dict(), 0)
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


# The larger Time-Lost radius (Baryanic Leylines, custom model in the bridge) ----------------------

LEYLINES_PATH = [56783, 36891]  # Disciple of Varashta: the small passive, then Baryanic Leylines
LIFE_JEWEL = "\n".join(["Rarity: Rare", "Probe Jewel", "Time-Lost Ruby", "Item Level: 80", "Radius: Small",
                        "Implicits: 0", "Small Passive Skills in Radius also grant +8 to maximum Life"])


def test_the_model_reproduces_pobs_radius_sets_and_grows_them(engine):
    sockets = load_reference(engine)
    for socket in sockets:
        for index in range(1, 13):
            same = engine.call("enlargedRadius", socket=socket, index=index, percent=0)
            assert same["ids"] == same["base"], (socket, index)
    larger = engine.call("enlargedRadius", socket=sockets[0], index=1, percent=40)
    assert set(larger["base"]) < set(larger["ids"])
    model = engine.call("radiusModel")
    assert [(p["name"], p["percent"]) for p in model["passives"]] == [("Baryanic Leylines", 40)]


@pytest.fixture(scope="module")
def disciple(engine):
    """A Spark Disciple of Varashta with its tree grown and the nearest socket allocated, empty."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    request = BuildRequest(main_skill="Spark", ascendancies=["Disciple of Varashta"], budget={"divines": 50})
    ctx = Context(make_payload(request))
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.tree.grow(time.perf_counter() + 60)
    if not search.jewels.sync():
        near = engine.call("frontier", maxDistance=6, sockets="only")["nodes"]
        search.build.allocate(add=[min(near, key=lambda n: n["distance"])["id"]])
    return search


def test_leylines_enlarges_non_unique_time_lost_jewels_in_what_ifs_and_commits(disciple, engine):
    build = disciple.build
    snap = build.snapshot()
    slot = disciple.jewels.slot(disciple.jewels.sync()[0])
    try:
        engine.call("equip", slot=slot, raw=LIFE_JEWEL)
        base = engine.call("stats", fields=["Life"])["Life"]
        what_if = engine.call("nodeDeltas", cases=[{"add": LEYLINES_PATH}], fields=["Life"])["results"][0]["Life"]
        engine.call("allocate", add=LEYLINES_PATH)
        assert engine.call("stats", fields=["Life"])["Life"] == what_if > base
        assert engine.call("nodeDeltas", cases=[{"remove": [36891]}], fields=["Life"])["results"][0]["Life"] == base
        jewel = engine.call("radiusJewels")["jewels"][0]
        assert jewel["enlarged"]["percent"] == 40 and set(jewel["enlarged"]["extra"]) & set(jewel["affected"])
        # The same jewel's what-if in the socket sees the larger radius too
        assert engine.call("itemDeltas", cases=[{"slot": slot, "raw": LIFE_JEWEL}], fields=["Life"])["results"][0]["Life"] == what_if
        # Unique Time-Lost jewels keep their radius ("Non-Unique")
        darkness = with_variants(raw("Against the Darkness"), variants_of(raw("Against the Darkness")), (10, 12))
        engine.call("equip", slot=slot, raw=darkness)
        assert "enlarged" not in engine.call("radiusJewels")["jewels"][0]
        # A request that forbids the ledger entry switches the model off
        engine.call("equip", slot=slot, raw=LIFE_JEWEL)
        engine.call("radiusModel", enabled=False)
        assert engine.call("nodeDeltas", cases=[{"remove": [36891]}], fields=["Life"])["results"][0]["Life"] \
            == engine.call("stats", fields=["Life"])["Life"]
    finally:
        engine.call("radiusModel", enabled=True)
        build.restore(snap)


def test_the_search_pairs_leylines_with_a_time_lost_jewel(disciple, monkeypatch):
    from builder.search.context import ItemOption

    build, tree, jewels = disciple.build, disciple.tree, disciple.jewels
    tree.ascendancy_round()  # nothing in the socket yet: Leylines is worth nothing
    assert 36891 not in {p["id"] for p in build.call("facts", fields=[])["passives"]}
    strong = "\n".join(["Rarity: Rare", "Optimizer Jewel", "Time-Lost Ruby", "Item Level: 80", "Radius: Small",
                        "Implicits: 0", "Small Passive Skills in Radius also grant 40% increased Damage"])
    option = ItemOption("Rare Jewel", "Time-Lost Ruby", "Jewel", None, strong, False, 0.5, "estimate", 0, rare=True)
    monkeypatch.setattr(jewels, "build_rare", lambda slot, deadline, required=None, bases=(): option)
    monkeypatch.setattr(jewels, "uniques", lambda slot: [])
    jewels.full_rounds = 2
    assert jewels.radius_trial(time.perf_counter() + 120)
    assert 36891 in {p["id"] for p in build.call("facts", fields=[])["passives"]}
    assert option in build.equipped.values()
    # Leylines pulls its weight: taking it out would lose score
    without = build.node_deltas([{"remove": [36891]}])[0][0]
    assert without < build.score
    notes = jewels.radius_notes()
    assert notes and notes[0]["value"] > 0 and notes[0]["confidence"] == "theory"
