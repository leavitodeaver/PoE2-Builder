"""JEWEL1: full crafted jewels (every affix slot filled; the 0.5 craft on both sides), jewel sockets valued in the tree
(behind POE2_SEARCH_JEWEL_SOCKETS, OFF by default) and Zarokh's Gift anointed with its Sinister socket filled
(builder/search/jewels.py, tree.py's socket valuation, anoints.py)."""

from __future__ import annotations

import faulthandler

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import anoints
from builder.search.context import Context, make_payload
from builder.search.rares import EFFECT


@pytest.fixture(scope="module")
def data():
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


@pytest.fixture(scope="module")
def engine(data):
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


@pytest.fixture(scope="module")
def search(engine):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    found = Search(engine, None, payload, skeleton.as_dict(), 0)
    found.setup(quests=False)
    found.gear.lam = 0.0
    jewels = found.jewels
    if not jewels.sockets():
        near = engine.call("frontier", maxDistance=12, sockets="only")["nodes"]
        if near:
            found.build.allocate(add=[min(near, key=lambda n: n["distance"])["id"]])
        jewels.sync()
    if not jewels.sockets():
        pytest.skip("no allocated jewel socket on this skeleton")
    return found


def standard_and_effect(option) -> tuple[int, int]:
    effect = sum(1 for line in option.affixes if EFFECT.search(line))
    return len(option.affixes) - effect, effect


def test_a_rare_jewel_fills_every_affix_slot(search):
    from builder.search.jewels import JEWEL_BASES

    jewels = search.jewels
    slot = jewels.slot(jewels.sockets()[0])
    rare = jewels.build_rare(slot, clock.now() + 300, bases=JEWEL_BASES)
    assert rare is not None
    standard, effect = standard_and_effect(rare)
    print("PROBE jewel", rare.base, rare.affixes, rare.price)
    # four standard mods, plus the crafted effect line when the 0.5 craft won (it should: lam 0, no budget)
    assert standard == 4 and effect in (0, 1)


def test_the_old_greedy_stop_is_one_switch_away(search, monkeypatch):
    from builder.search import jewels as module

    monkeypatch.setattr(module, "FULL_JEWELS", False)
    jewels = search.jewels
    slot = jewels.slot(jewels.sockets()[0])
    rare = jewels.build_rare(slot, clock.now() + 300, bases=module.JEWEL_BASES)
    if rare is not None:
        assert standard_and_effect(rare)[0] <= 4


def test_jewel_sockets_in_the_tree_are_off_by_default(monkeypatch):
    from builder.search.tree import Tree

    monkeypatch.delenv("POE2_SEARCH_JEWEL_SOCKETS", raising=False)
    assert not Tree.jewel_sockets_enabled()
    monkeypatch.setenv("POE2_SEARCH_JEWEL_SOCKETS", "1")
    assert Tree.jewel_sockets_enabled()


def test_the_tree_sees_sockets_at_the_jewel_they_would_hold(search, monkeypatch):
    tree, jewels = search.tree, search.jewels
    assert tree.socket_value == jewels.socket_value and tree.fill_socket == jewels.fill_new_socket
    monkeypatch.delenv("POE2_SEARCH_JEWEL_SOCKETS", raising=False)
    assert not any(c.socket for c in tree.frontier(tree.points_left() or 10))
    monkeypatch.setenv("POE2_SEARCH_JEWEL_SOCKETS", "1")
    candidates = tree.frontier(30)
    sockets = [c for c in candidates if c.socket]
    if not sockets:
        pytest.skip("no free jewel socket within reach")
    jewels.rare = jewels.rare or jewels.build_rare(jewels.slot(jewels.sockets()[0]), clock.now() + 300,
                                                   bases=("Sapphire",))
    jewels.fill(jewels.sockets()[0], clock.now() + 300, rebuild=False)
    jewels._socket_value = None
    value = jewels.socket_value()
    print("PROBE socket value", value, "score", search.build.score)
    assert value >= 0.0
    tree.round += 1
    tree.evaluate(sockets[:3])
    plain = search.build.node_deltas([{"add": [c.id], "addPaths": True, "attribute": tree.attribute} for c in sockets[:3]])
    for c, (score, _) in zip(sockets[:3], plain):
        assert c.gain == pytest.approx(score - search.build.score + value)


def test_zarokhs_gift_is_no_longer_left_out():
    assert "Zarokh's Gift" not in anoints.LEFT_OUT


def test_zarokhs_gift_is_anointed_with_its_socket_filled(search, data):
    from builder.search.jewels import ZAROKH

    build, gear, jewels = search.build, search.gear, search.jewels
    if ZAROKH not in gear.anoint_recipes():
        pytest.skip("this tree has no Zarokh's Gift recipe")
    snap = build.snapshot()
    amulet = build.equipped.get("Amulet")
    if amulet is None:
        amulet = gear.build_rare("Amulet", clock.now() + 300)
        if amulet is not None:
            build.equip("Amulet", amulet)
    if amulet is None or anoints.capacity(amulet) == 0:
        build.restore(snap)
        pytest.skip("no anointable amulet on this build")
    before = build.score
    kept = jewels.zarokh_trial(clock.now() + 300)
    print("PROBE zarokh", kept, before, "->", build.score)
    if kept:
        assert ZAROKH in build.equipped["Amulet"].anoints and build.score > before
        granted = [s for s in jewels.sockets() if jewels.plain_only(s)]
        assert granted and all(build.equipped.get(jewels.slot(s)) is not None for s in granted)
    else:
        assert build.score == pytest.approx(before)
    build.restore(snap)


def test_voices_versions_read_their_socket_counts(data):
    from builder.search.jewels import granted_sockets
    from builder.search.radius import variants_of, with_variants

    voices = next((u for u in data.uniques if u["name"] == "Voices"), None)
    if voices is None:
        pytest.skip("no Voices in this game data")
    variants = variants_of(voices["raw"])
    assert [granted_sockets(with_variants(voices["raw"], variants, (i,))) for i in variants.usable] == [2, 3, 4]


def test_voices_is_socketed_with_its_sinister_sockets_filled(search):
    build, jewels = search.build, search.jewels
    snap = build.snapshot()
    before = build.score
    kept = jewels.granting_trial(clock.now() + 600)
    print("PROBE voices", kept, before, "->", build.score)
    if kept:
        assert any(o is not None and o.name == "Voices" for o in build.equipped.values())
        granted = [s for s in jewels.sockets() if jewels.plain_only(s)]
        assert len(granted) >= 2 and all(build.equipped.get(jewels.slot(s)) is not None for s in granted)
        assert build.score > before
    else:
        assert build.score == pytest.approx(before)
    build.restore(snap)


def test_item_jewel_sockets_are_read_with_their_jewel_type(data):
    from builder.search.jewels import JEWEL_BASES, item_jewel_sockets

    raws = {u["name"]: u["raw"] for u in data.uniques}
    if "Tabula Rasa" not in raws:
        pytest.skip("no Tabula Rasa in this game data")
    assert item_jewel_sockets(raws["Tabula Rasa"]) == (6, JEWEL_BASES)
    assert item_jewel_sockets(raws["Eshtera's Path"]) == (1, ("Sapphire",))
    assert item_jewel_sockets(raws["Safrin's Resolve"]) == (1, ("Ruby",))
    assert item_jewel_sockets(raws["Zaida's Longevity"]) == (1, ("Emerald",))


def test_a_jewel_in_an_item_socket_counts_only_while_the_item_holds_it(search, data):
    """PoB's slot "<item> Jewel Socket <n>" (CalcSetup 1446-1453): filled under Tabula Rasa, gone under a plain body."""
    from builder.search.context import ItemOption

    build, jewels = search.build, search.jewels
    tabula = next((u for u in data.uniques if u["name"] == "Tabula Rasa"), None)
    if tabula is None:
        pytest.skip("no Tabula Rasa in this game data")
    snap = build.snapshot()
    option = ItemOption("Tabula Rasa", tabula["base"], "Body Armour", None, tabula["raw"], True, 0.0, "test", 0)
    build.equip("Body Armour", option)
    sockets = jewels.item_sockets()
    assert [slot for slot, _ in sockets] == [f"Body Armour Jewel Socket {i}" for i in range(1, 7)]
    empty = build.score
    filled = jewels.fill_item_socket(sockets[0][0], sockets[0][1], clock.now() + 300)
    print("PROBE tabula socket", filled, empty, "->", build.score)
    if filled:
        assert build.score > empty
        jewels.sync()
        assert build.equipped.get(sockets[0][0]) is not None
    build.restore(snap)


def test_a_jewel_in_an_item_socket_is_priced_as_a_jewel():
    from builder.data.rare_prices import category

    assert category("Body Armour Jewel Socket 1") == "jewel" and category("Ring 2 Jewel Socket 1") == "jewel"
    assert category("Jewel 26725") == "jewel" and category("Ring 2") == "ring" and category("Body Armour") == "body"
