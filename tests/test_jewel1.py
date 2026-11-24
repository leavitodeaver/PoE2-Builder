"""JEWEL1: full crafted jewels (every affix slot filled; the 0.5 craft on both sides), jewel sockets valued in the tree
(behind POE2_SEARCH_JEWEL_SOCKETS, OFF by default) and Zarokh's Gift anointed with its Sinister socket filled
(builder/search/jewels.py, tree.py's socket valuation, anoints.py)."""

from __future__ import annotations

import dataclasses
import faulthandler
import re

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


def test_a_jewel_the_clock_cuts_off_still_fills_every_slot(search):
    """(JEWEL2, the empty-slot red flag: POE2T6363's Ruby held the effect line and 2 mods) With the deadline already past, no
    scored step runs, yet the greedy's full fill gives every slot an affordable affix; and a crafted shape short of its
    slots is no craft's output (shape_full)."""
    from types import SimpleNamespace

    from builder.search.jewels import SCREEN_KEEP, shape_full

    jewels = search.jewels
    slot = jewels.slot(jewels.sockets()[0])
    base = jewels.ctx.bases_by_name["Ruby"]
    ranked = jewels.ranked(slot, base, jewels.pools.pool("Ruby"))
    limits = {"Prefix": 2, "Suffix": 2}
    shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
    found = jewels.greedy(slot, base, shortlist, limits, [], float("inf"), clock.now() - 1.0, full=True)
    assert found is not None and shape_full(found[1], limits)
    mod = lambda kind, crafted=False: SimpleNamespace(type=kind, crafted=crafted)  # noqa: E731
    craft = {"Prefix": 3, "Suffix": 1}
    assert not shape_full([mod("Suffix", True), mod("Prefix"), mod("Prefix")], craft)  # (POE2T6363's Ruby)
    assert shape_full([mod("Suffix", True), mod("Prefix"), mod("Prefix"), mod("Prefix"), mod("Suffix")], craft)


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


def test_a_jewel_in_an_item_socket_is_not_in_your_tree():
    """(JEWEL2, the manager's 23 Nov ruling) "while you have a <X> and a <Y> socketed in your tree" reads tree sockets:
    of the bridge's facts items, Conditions._tree_jewels keeps slots "Jewel <node id>" and leaves out an item's socket."""
    from types import SimpleNamespace

    from builder.search.conditions import Conditions

    items = [{"slot": "Jewel 26196", "base": "Sapphire", "type": "Jewel"},
             {"slot": "Jewel 11184", "base": "Emerald", "type": "Jewel"},  # (a Sinister socket: read as in the tree)
             {"slot": "Body Armour Jewel Socket 1", "base": "Ruby", "type": "Jewel"},
             {"slot": "Ring 2 Jewel Socket 1", "base": "Ruby", "type": "Jewel"},
             {"slot": "Body Armour", "base": "Simple Robe", "type": "Body Armour"}]
    read = SimpleNamespace(build=SimpleNamespace(call=lambda *_, **__: {"items": items}))
    assert Conditions._tree_jewels(read) == ["Emerald", "Sapphire"]


def test_a_sinister_socket_counts_with_the_other_reading_shown():
    """(JEWEL2, the manager's 23 Nov ruling) A granted Sinister socket stays read as in your tree (UNVERIFIED): the line
    counts, and its note gives the other reading only where that reading would change the answer."""
    from types import SimpleNamespace

    from builder.search import conditions
    from builder.search.conditions import Conditions

    line = "Blind Enemies on Hit while you have a Ruby and a Sapphire socketed in your tree"
    _, ok, how = conditions.socketed_pair(line, {"_tree_jewels": ["Ruby", "Sapphire"], "_sinister_jewels": ["Sapphire"]})
    assert ok and "UNVERIFIED" in how and "Sapphire missing" in how
    _, ok, how = conditions.socketed_pair(line, {"_tree_jewels": ["Ruby", "Sapphire", "Sapphire"],
                                                 "_sinister_jewels": ["Sapphire"]})
    assert ok and "UNVERIFIED" not in how  # (a second Sapphire holds the line either way)
    _, ok, how = conditions.socketed_pair(line, {"_tree_jewels": ["Ruby", "Sapphire"]})
    assert ok and "UNVERIFIED" not in how

    answers = {"jewelSockets": {"sockets": [{"id": 11184, "nonUnique": True, "basicOnly": False},
                                            {"id": 26196, "nonUnique": False, "basicOnly": False},
                                            {"id": 39935, "nonUnique": True, "basicOnly": True}]},
               "facts": {"items": [{"slot": "Jewel 11184", "base": "Sapphire", "type": "Jewel"},
                                   {"slot": "Jewel 26196", "base": "Ruby", "type": "Jewel"},
                                   {"slot": "Jewel 39935", "base": "Emerald", "type": "Jewel"}]}}
    read = SimpleNamespace(build=SimpleNamespace(call=lambda method, **_: answers[method]))
    assert Conditions._sinister_jewels(read) == ["Sapphire"]


ITEM_SOCKET = "Body Armour Jewel Socket 1"
ITEM_SOCKET_LINE = "37% increased Spell Damage"


def _item_socket_options(data):
    from builder.search.context import ItemOption

    tabula = next((u for u in data.uniques if u["name"] == "Tabula Rasa"), None)
    if tabula is None:
        pytest.skip("no Tabula Rasa in this game data")
    base = tabula["base"]
    raw = "\n".join(["Rarity: Rare", "Test Ruby", "Ruby", "Item Level: 82", "Implicits: 0", ITEM_SOCKET_LINE])
    return (ItemOption("Tabula Rasa", base, "Body Armour", None, tabula["raw"], True, 0.0, "test", 0),
            ItemOption(base, base, "Body Armour", None, f"Rarity: Normal\n{base}\n{base}", False, 0.0, "test", 0),
            ItemOption("Test Ruby", "Ruby", ITEM_SOCKET, None, raw, False, 0.0, "test", 0))


def _item_socket_reads(build) -> tuple[bool, bool]:
    """(the bridge's facts list the jewel, sourceLines holds its line)"""
    listed = any(i.get("slot") == ITEM_SOCKET for i in build.call("facts", fields=[])["items"])
    return listed, any(ITEM_SOCKET_LINE in line["text"] for line in build.call("sourceLines")["lines"])


def test_a_jewel_put_in_an_item_socket_reads_the_same_live_and_reloaded(search, data):
    """(JEWEL2, probe POE2T7937) Before, facts and sourceLines missed it until a reload while PoB's calculation counted it."""
    tabula, plain, jewel = _item_socket_options(data)
    build = search.build
    snap = build.snapshot()
    build.equip("Body Armour", plain)
    build.equip("Body Armour", tabula)
    empty = build.score
    build.equip(ITEM_SOCKET, jewel)
    live, live_score = _item_socket_reads(build), build.score
    build.restore(build.snapshot())
    reloaded, reloaded_score = _item_socket_reads(build), build.score
    build.restore(snap)
    assert live == (True, True) and reloaded == (True, True)
    assert live_score > empty and live_score == pytest.approx(reloaded_score, abs=1e-6)


def test_a_loaded_item_swapped_away_stops_its_jewels_lines_at_once(search, data):
    """(JEWEL2, probe POE2T7937) Before, a loaded Tabula's jewel stayed in facts and sourceLines after the swap, though
    PoB's calculation had dropped it: a model reading the line would count a jewel the build no longer has."""
    tabula, plain, jewel = _item_socket_options(data)
    build = search.build
    snap = build.snapshot()
    build.equip("Body Armour", plain)
    without = build.score
    build.equip("Body Armour", tabula)
    build.equip(ITEM_SOCKET, jewel)
    build.restore(build.snapshot())  # (the load is what marks the socket open)
    assert _item_socket_reads(build) == (True, True)
    with_jewel = build.score
    build.equip("Body Armour", plain)
    gone, gone_score = _item_socket_reads(build), build.score
    build.equip("Body Armour", tabula)  # the jewel is still in its slot: it counts again with its item
    back, back_score = _item_socket_reads(build), build.score
    build.restore(snap)
    assert gone == (False, False) and gone_score == pytest.approx(without, abs=1e-6)
    assert back == (True, True) and back_score == pytest.approx(with_jewel, abs=1e-6)


@pytest.mark.own_pob_engine  # (it loads its own Ritualist build: the module's Spark search keeps its engine)
def test_a_hidden_third_ring_and_its_jewel_are_not_read(engine, data):
    """(JEWEL2, probe POE2T7947) Ritualist: the search keeps a free ring in Ring 3 until Unfurled Finger ("+1 Ring Slot") is
    allocated, and PoB ignores that ring and a jewel in its socket. The bridge's readers now do too, and read them once
    the passive is taken."""
    from builder.search.context import ItemOption
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ring = next((u for u in data.uniques if u["name"] == "Eshtera's Path"), None)
    if ring is None:
        pytest.skip("no Eshtera's Path in this game data")
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Ritualist"], require=Choices()))
    found = Search(engine, None, payload, enumerate_skeletons(Context(payload), ["Spark"])[0][0].as_dict(), 0)
    found.setup(quests=False)
    build = found.build
    taken = lambda: {n["id"] for n in build.call("facts", fields=[])["passives"]}  # (allocatedNodes omits ascendancy nodes)
    if 34785 in taken():
        pytest.skip("this skeleton starts with Unfurled Finger")
    slot, line = "Ring 3 Jewel Socket 1", "37% increased Spell Damage"
    base = next((l.split()[4] for l in ring["raw"].splitlines() if l.startswith("You can only Socket")), "Sapphire")
    raw = "\n".join(["Rarity: Rare", "Test", base, "Item Level: 82", "Implicits: 0", line])

    def reads() -> tuple[bool, bool, bool]:
        items = {i["slot"] for i in build.call("facts", fields=[])["items"]}
        return "Ring 3" in items, slot in items, any(line in t["text"] for t in build.call("sourceLines")["lines"])

    before = build.score
    build.equip("Ring 3", ItemOption(ring["name"], ring["base"], "Ring", None, ring["raw"], True, 0.0, "test", 0))
    build.equip(slot, ItemOption("Test", base, slot, None, raw, False, 0.0, "test", 0))
    assert reads() == (False, False, False) and build.score == pytest.approx(before, abs=1e-6)
    build.restore(build.snapshot())
    assert reads() == (False, False, False)
    build.allocate(add=[34785])
    assert 34785 in taken()
    assert reads() == (True, True, True)


def test_a_what_if_replacing_an_item_that_holds_jewels_is_rescored(search, data):
    """(JEWEL2, on loan in state.py: Build._rescore_model_items) PoB drops a replaced item's jewels in a what-if, but the
    models keep their last values (POE2T7946: up to +0.086 ln too high), so such a candidate gets a real trial; one that
    replaces an item holding no jewel doesn't."""
    tabula, plain, jewel = _item_socket_options(data)
    build = search.build
    if build.reassume is None:
        pytest.skip("no model re-score on this build")
    snap = build.snapshot()
    build.equip("Body Armour", tabula)
    if getattr(build, "models_on_stats", False):
        build.restore(snap)
        pytest.skip("this build re-scores every item what-if already")

    def trials() -> int:
        was = build.rescored
        build.item_deltas([{"slot": "Body Armour", "raw": plain.raw}])
        return build.rescored - was

    empty = trials()  # (Tabula with no jewel in it)
    build.equip(ITEM_SOCKET, jewel)
    holding = trials()
    build.restore(snap)
    assert empty == 0 and holding == 1


def test_a_jewel_in_an_item_socket_is_priced_as_a_jewel():
    from builder.data.rare_prices import category

    assert category("Body Armour Jewel Socket 1") == "jewel" and category("Ring 2 Jewel Socket 1") == "jewel"
    assert category("Jewel 26725") == "jewel" and category("Ring 2") == "ring" and category("Body Armour") == "body"


def test_the_effect_line_is_not_applied_a_second_time(search):
    """(RESEARCH51 R51-11, the manager's priority zero) The search writes the boosted values into the jewel and keeps the
    "N% increased Effect of Prefixes" line; PoB applies that line only to a "Crafted" / advanced-copy item's prefix- or
    suffix-tagged lines (Item.lua 1649-1650, 1686), which our text has none of: the line must change nothing."""
    from builder.search.rares import AffixPools, rare_text

    jewels, build = search.jewels, search.build
    slot = jewels.slot(jewels.sockets()[0])
    pools = AffixPools(search.ctx, "Jewel")
    base = search.ctx.bases_by_name["Sapphire"]
    prefixes = [a for a in pools.pool("Sapphire") if a.type == "Prefix"][:3]
    effect, _ = pools.crafted("Prefix")
    without, with_line = build.item_deltas([{"slot": slot, "raw": rare_text("Jewel", base, pools.item_level, chosen)}
                                            for chosen in (prefixes, prefixes + [effect])])
    assert with_line[0] == pytest.approx(without[0], abs=1e-9)


def test_a_helmet_takes_zarokhs_gift_through_a_raven_touched_shard(search, monkeypatch):
    from builder.search import anoints as anoint_module

    build, gear, jewels = search.build, search.gear, search.jewels
    # (this test's request has no price data, so the rune catalog offers nothing: the shard is offered here)
    monkeypatch.setattr(gear.runes, "candidates", lambda *a, **k: [anoint_module.SHARD])
    monkeypatch.setattr(gear.runes, "price", lambda name: 0.5)
    snap = build.snapshot()
    helmet = gear.build_rare("Helmet", clock.now() + 300)
    if helmet is None:
        pytest.skip("no rare helmet on this build")
    build.equip("Helmet", helmet)
    shard = jewels.shard_helmet("Helmet", build.equipped["Helmet"])
    if shard is None:
        build.restore(snap)
        pytest.skip("this helmet takes no Raven-Touched Shard")
    assert anoint_module.SHARD in shard.runes and anoint_module.capacity(shard) == 1
    build.restore(snap)


def test_cadigans_epiphany_turns_the_gloves_sockets_into_a_filled_jewel_socket(search, monkeypatch):
    """(JEWEL2) Cadigan's Epiphany: the gloves lose their augment sockets and runes, PoB reads one jewel socket
    ("Sockets: J"), the trial fills it with a plain rare and keeps it for a real gain; Corrupted gloves can't take it."""
    from builder.search import jewels as module

    build, gear, jewels = search.build, search.gear, search.jewels
    # (this test's request has no price data, so the rune catalog offers nothing: the augment is offered here)
    monkeypatch.setattr(gear.runes, "candidates", lambda *a, **k: [module.CADIGAN])
    monkeypatch.setattr(gear.runes, "price", lambda name: 1.0)
    snap = build.snapshot()
    gloves = gear.build_rare("Gloves", clock.now() + 300)
    if gloves is None:
        pytest.skip("no rare gloves on this build")
    build.equip("Gloves", gloves)
    trial = jewels.cadigan_gloves(build.equipped["Gloves"])
    if trial is None:
        build.restore(snap)
        pytest.skip("these gloves have no augment socket")
    assert trial.runes == (module.CADIGAN,) and "Rune:" not in trial.raw
    assert module.item_jewel_sockets(trial.raw)[0] == 1
    assert jewels.cadigan_gloves(dataclasses.replace(trial, runes=(), raw=trial.raw.replace("Sockets: J", ""))
                                 ) is not None  # (bare gloves with a socket count from their base still qualify)
    sealed = dataclasses.replace(build.equipped["Gloves"], raw=build.equipped["Gloves"].raw + "\nCorrupted")
    assert jewels.cadigan_gloves(sealed) is None
    before = build.score
    kept = jewels.cadigan_trial(clock.now() + 300)
    print("PROBE cadigan", kept, before, "->", build.score, build.equipped.get(module.CADIGAN_SLOT))
    if kept:
        assert build.score > before and module.CADIGAN in build.equipped["Gloves"].runes
        assert build.equipped.get(module.CADIGAN_SLOT) is not None
    build.restore(snap)


def test_lazy_steps_pick_what_the_full_scan_picks(search, monkeypatch):
    from builder.search import jewels as module

    jewels = search.jewels
    slot = jewels.slot(jewels.sockets()[0])
    picks = {}
    for lazy in (False, True):
        monkeypatch.setattr(module, "LAZY_JEWELS", lazy)
        jewels._ranked.clear()
        rare = jewels.build_rare(slot, clock.now() + 300, bases=("Sapphire",))
        picks[lazy] = (sorted(rare.affixes) if rare else None)
    print("PROBE lazy", picks)
    assert picks[True] == picks[False]


def test_voices_versions_cost_their_own_trade_floor(search, data):
    from builder.search.context import ItemOption
    from builder.search.jewels import VERSION_FLOORS, granted_sockets
    from builder.search.radius import variants_of, with_variants

    voices = next((u for u in data.uniques if u["name"] == "Voices"), None)
    if voices is None:
        pytest.skip("no Voices in this game data")
    variants = variants_of(voices["raw"])
    for i in variants.usable:
        raw = with_variants(voices["raw"], variants, (i,))
        option = ItemOption("Voices", "Sapphire", "Jewel", None, raw, True, 1370.0, "high", 0)
        priced = search.jewels.version_floor(option)
        assert priced.price == max(1370.0, VERSION_FLOORS["Voices"][granted_sockets(raw)])


def test_melancholys_exposure_sapphire_is_scored_by_a_real_trial(search):
    """(JEWEL3) Melancholy's Exposure: the crafted line goes on a Sapphire (PoB LiquidEmotions.lua) and needs a Ruby and an
    Emerald in other tree sockets. exposure_trial builds the Sapphire whole (2 prefixes + 1 suffix + the crafted suffix, no
    effect line), puts a Ruby and an Emerald where the tree lacks them, and scores it all by a real trial (Conditions reads
    the line); the same jewels without the line score lower, so the trial sees the Exposure."""
    from builder.search import jewels as module
    from builder.search.context import ItemOption

    build, jewels = search.build, search.jewels
    snap = build.snapshot()
    for _ in range(3):
        if len(jewels.sockets()) >= 3:
            break
        near = build.call("frontier", maxDistance=14, sockets="only")["nodes"]
        if not near:
            break
        build.allocate(add=[min(near, key=lambda n: n["distance"])["id"]])
        jewels.sync()
    if len(jewels.sockets()) < 3:
        build.restore(snap)
        pytest.skip("fewer than 3 jewel sockets within reach")
    for socket in jewels.sockets():
        build.equip(jewels.slot(socket), None)
    search.gear.settle()
    before = build.score
    assert jewels.exposure_trial(clock.now() + 600)
    record = jewels.exposure_kept
    sapphire = build.equipped[record["sapphire"]]
    print("PROBE exposure", before, "->", build.score, record, sapphire.affixes, sapphire.price)
    assert sapphire.base == "Sapphire" and module.EXPOSURE_LINE in sapphire.affixes
    assert not any(EFFECT.search(line) for line in sapphire.affixes)
    standard = [line for line in sapphire.affixes if line != module.EXPOSURE_LINE]
    assert len(standard) >= 3 and sapphire.craft_route  # (a mod's lines may be two: 3 affixes, never short)
    bases = sorted(o.base for slot, o in build.equipped.items() if slot.startswith("Jewel ") and o is not None)
    assert "Ruby" in bases and "Emerald" in bases and build.score > before
    kept = build.score
    stripped = ItemOption("Rare Jewel", "Sapphire", "Jewel", None, sapphire.raw.replace(module.EXPOSURE_LINE + "\n", "")
                          .replace("\n" + module.EXPOSURE_LINE, ""), False, 0.0, "t", 0, rare=True,
                          affixes=tuple(standard))
    build.equip(record["sapphire"], stripped)
    search.gear.settle()
    print("PROBE exposure without the line", build.score)
    assert build.score < kept - 1e-6
    build.restore(snap)


def test_a_refined_catalyst_raises_a_jewels_lines_and_is_priced(search, monkeypatch):
    """(JEWEL3, POE2_SEARCH_JEWEL_CATALYST=1; R57-9) A rare jewel tries 20% quality of each Refined catalyst whose tag one
    of its affixes carries: PoB scales the tagged lines (no item-type check in getCatalystScalar), the jewel keeps the best
    when it pays, priced as the jewel + 20 catalysts, its lines as bought (no quality) for the trade search. The catalysed
    jewel scores above the same jewel without the quality."""
    from builder.search import jewels as module

    jewels, build = search.jewels, search.build
    monkeypatch.setattr(module, "JEWEL_CATALYST", True)
    monkeypatch.setitem(search.ctx.payload, "jewel_catalyst_prices", {n: 0.05 for n in module.JEWEL_CATALYSTS})
    jewels._ranked.clear()
    slot = jewels.slot(jewels.sockets()[0])
    rare = jewels.build_rare(slot, clock.now() + 300, bases=module.JEWEL_BASES)
    print("PROBE catalyst", rare.base if rare else None, rare.raw if rare else None, rare.craft_route if rare else None)
    assert rare is not None and "CatalystQuality: 20" in rare.raw and rare.craft_route
    assert "x20" in rare.craft_route[0] and "Refined" in rare.craft_route[0]
    plain = "\n".join(["Rarity: Rare", "Optimizer Jewel", rare.base, f"Item Level: {jewels.pools.item_level}",
                       "Implicits: 0", *rare.affixes])
    (with_quality, _), (without, _) = build.item_deltas([{"slot": slot, "raw": rare.raw}, {"slot": slot, "raw": plain}])
    print("PROBE catalyst scores", with_quality, without)
    assert with_quality > without
    if any(EFFECT.search(line) for line in rare.affixes) and "adding" in rare.craft_route[0]:
        assert "multiply" in rare.craft_route[0]  # (the other reading named)
    jewels._ranked.clear()


def test_the_very_large_radius_craft_makes_a_whole_time_lost_rare(search, monkeypatch):
    """(JEWEL3, POE2_SEARCH_RADIUS_CRAFT=1; R-J2 / R-J3, AUDIT38 F9) Ancient Potent Liquid Melancholy's "Upgrades Radius to
    Very Large" on a Time-Lost rare: the 1 prefix + 3 suffixes shape is whole, priced by the Ancient emotions' route, written
    "Radius: Very Large"; PoB reaches at least as many passives with it as the same jewel at Small."""
    from builder.search import jewels as module
    from builder.search.rares import rare_text

    jewels, build = search.jewels, search.build
    monkeypatch.setattr(module, "RADIUS_JEWEL", True)
    monkeypatch.setitem(search.ctx.payload, "ancient_emotion_prices",
                        {module.ANCIENT_MELANCHOLY: 0.2, module.ANCIENT_CONTEMPT: 0.3})
    slot = next((jewels.slot(s) for s in jewels.sockets() if not jewels.plain_only(s)), None)
    if slot is None:
        pytest.skip("no socket that takes a radius jewel")
    radius = jewels.radius_affix()
    assert radius is not None and radius.type == "Prefix"
    base = search.ctx.bases_by_name["Time-Lost Sapphire"]
    ranked = jewels.ranked(slot, base, jewels.pools.pool(base["name"]))
    limits = module.RADIUS_SHAPES[0]
    shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:module.SCREEN_KEEP]]
    found = jewels.greedy(slot, base, shortlist, limits, [radius], 1e9, clock.now() + 300, full=True, free=(radius,))
    assert found and module.shape_full(found[1], limits)
    price, option = jewels.radius_rare(base, limits, found)
    print("PROBE radius", option.affixes, price, option.craft_route)
    assert "Radius: Very Large" in option.raw and "Upgrades Radius to Very Large" in option.raw
    assert price > 0 and "Ancient Potent Liquid Contempt" in option.craft_route[0]
    snap = build.snapshot()
    reached = {}
    for size, raw in (("very large", option.raw), ("small", rare_text("Jewel", base, jewels.pools.item_level,
                                                                       [a for a in found[1] if not a.crafted]))):
        build.equip(slot, dataclasses.replace(option, raw=raw))
        jewel = next(j for j in build.call("radiusJewels")["jewels"] if f"Jewel {j['socket']}" == slot)
        reached[size] = len(jewel.get("affected") or [])
    build.restore(snap)
    print("PROBE radius reached", reached)
    assert reached["very large"] >= reached["small"]
    picked = jewels.build_rare(slot, clock.now() + 300, bases=module.RADIUS_BASES)
    print("PROBE radius picked", picked.affixes if picked else None, picked.price if picked else None)


def test_the_adorned_raises_corrupted_magic_jewels_in_a_real_trial(search, data, monkeypatch):
    """(JEWEL3, POE2_SEARCH_ADORNED=1; R52-4) The Adorned in the weakest socket and corrupted magic jewels (1 prefix + 1
    suffix, both filled) in the other normal sockets, scored together: PoB reads "increased Effect of Jewel Socket Passive
    Skills containing Corrupted Magic Jewels" (ModParser 5644), so taking The Adorned out again lowers the score."""
    from builder.search import jewels as module
    from builder.search.context import ItemOption

    raw = next((u["raw"] for u in data.uniques if u["name"] == module.ADORNED), None)
    if raw is None:
        pytest.skip("no The Adorned in this game data")
    build, jewels = search.build, search.jewels
    adorned = ItemOption(module.ADORNED, "Diamond", "Jewel", None, raw, True, 1.0, "high", 0)
    monkeypatch.setitem(search.ctx.item_options, "Jewel", [*search.ctx.item_options.get("Jewel", []), adorned])
    snap = build.snapshot()
    for _ in range(3):
        if len([s for s in jewels.sockets() if not jewels.plain_only(s)]) >= 3:
            break
        near = build.call("frontier", maxDistance=14, sockets="only")["nodes"]
        if not near:
            break
        build.allocate(add=[min(near, key=lambda n: n["distance"])["id"]])
        jewels.sync()
    normal = [s for s in jewels.sockets() if not jewels.plain_only(s)]
    if len(normal) < 3:
        build.restore(snap)
        pytest.skip("fewer than 3 normal jewel sockets within reach")
    for socket in jewels.sockets():
        build.equip(jewels.slot(socket), None)
    search.gear.settle()
    versions = jewels.adorned_versions()
    assert versions and all("Variant" not in v.raw for v in versions)
    assert {int(m.group(1)) for v in versions for m in [re.search(r"(\d+)% increased Effect of Jewel Socket", v.raw)]} \
        == set(module.ADORNED_ROLLS)
    before = build.score
    assert jewels.adorned_trial(clock.now() + 600)
    held = {slot: o for slot, o in build.equipped.items() if slot.startswith("Jewel ") and o is not None}
    where = next(slot for slot, o in held.items() if o.name == module.ADORNED)
    magic = [o for slot, o in held.items() if slot != where]
    print("PROBE adorned", before, "->", build.score, where, [o.affixes for o in magic], held[where].raw.splitlines()[-2:])
    assert magic and all(o.raw.startswith("Rarity: Magic") and o.raw.rstrip().endswith("Corrupted")
                         and len(o.affixes) >= 2 for o in magic)
    kept = build.score
    build.equip(where, None)
    search.gear.settle()
    print("PROBE adorned out", build.score)
    assert build.score < kept - 1e-6
    build.restore(snap)


def test_a_jewel_left_short_is_kept_with_a_not_finished_note(search, monkeypatch):
    """(JEWEL3; THE USER 24 Nov 2026: "a jewel with 2 mods is better than an empty jewel slot. if the optimizer runs out of
    time when it's actively working on something like that, it should leave a note to indicate that it wasn't finished")
    A jewel the fill leaves short of its slots (here every shape stops after two affixes, as a clock cut would) is kept
    and goes into the socket, never left empty, and the result's notes say which jewel isn't finished and by how much."""
    jewels, build = search.jewels, search.build
    real = jewels.greedy

    def cut(slot, base, shortlist, limits, fixed, *args, **kwargs):
        found = real(slot, base, shortlist, limits, fixed, *args, **kwargs)
        if not found:
            return found
        value, chosen, priced = found
        short = list(fixed) + [a for a in chosen if a not in fixed and not a.crafted][:2]
        return value, short, [a for a in priced if a in short]

    monkeypatch.setattr(jewels, "greedy", cut)
    snap = build.snapshot()
    socket = jewels.sockets()[0]
    slot = jewels.slot(socket)
    build.equip(slot, None)
    jewels.unfinished.clear()
    assert jewels.fill(socket, clock.now() + 300, rebuild=True)
    held = build.equipped.get(slot)
    assert held is not None and held.raw in jewels.unfinished
    notes = [n for n in jewels.unfinished_notes() if slot in n["label"]]
    print("PROBE unfinished", held.affixes, notes)
    assert len(notes) == 1 and notes[0]["label"].startswith("Not finished") and "Continue" in notes[0]["basis"]
    info = jewels.unfinished[held.raw]
    assert info["filled"] < info["slots"] and notes[0]["value"] == f"{info['filled']}/{info['slots']}"
    build.restore(snap)


def test_a_desecrated_line_sits_on_the_crafted_jewel_with_nothing_removed(search, monkeypatch):
    """(JEWEL4; AUDIT38 F9 (b), MEDIUM-LOW) Real listings hold the crafted 3 + 1 + effect jewel PLUS a Desecrated line, 6 lines
    with nothing lost (#219 / #221 / #333). With the Cranium trial and POE2_SEARCH_CRANIUM_CRAFTED on, the Cranium trial on
    a crafted jewel keeps all five of its lines, adds one Desecrated line, and prices the crafted route plus the
    Desecration. OFF by default."""
    from builder.search import desecrated, jewels as jewels_mod
    from builder.search.jewels import JEWEL_LIMIT, SCREEN_KEEP
    from builder.search.rares import boosted, rare_price

    assert not jewels_mod.CRANIUM_CRAFTED  # (OFF until gated)
    monkeypatch.setenv("POE2_SEARCH_DESECRATED_JEWEL", "1")
    monkeypatch.setattr(jewels_mod, "CRANIUM_CRAFTED", True)
    jewels = search.jewels
    jewels.ctx.desecration_prices = {name: 0.001 for name in desecrated.MATERIALS}
    slot = jewels.slot(jewels.sockets()[0])
    base = jewels.ctx.bases_by_name["Sapphire"]
    ranked = jewels.ranked(slot, base, jewels.pools.pool("Sapphire"))
    effect, percent = jewels.pools.crafted("Suffix")
    limits = {"Suffix": 3, "Prefix": 1}
    shortlist = [a for kind in limits for a in [x for x in ranked if x.type == kind][:SCREEN_KEEP]]
    shortlist = [dataclasses.replace(a, lines=tuple(boosted(l, percent) for l in a.lines)) if a.type == "Suffix" else a
                 for a in shortlist]
    found = jewels.greedy(slot, base, shortlist, limits, [effect], 1e9, clock.now() + 300, full=True)
    assert found is not None
    _, chosen, priced = found
    assert len(chosen) == 5
    trial = jewels.cranium_trial(slot, base, chosen, float("-inf"), 1e9, clock.now() + 300, ("Suffix", percent))
    if trial is None:
        pytest.skip("no Cranium row on a Sapphire here")
    _, option = trial
    print("PROBE crafted cranium", option.affixes, option.price, option.craft_route)
    kept = [line for a in chosen for line in a.lines]
    assert option.affixes[:len(kept)] == tuple(kept)  # nothing removed
    cranium = {l for a in jewels.cranium_pools.pool("Sapphire") for l in a.lines}
    added = option.affixes[len(kept):]
    assert len(added) >= 1 and sum(1 for a in chosen if not a.crafted) == sum(JEWEL_LIMIT.values())  # 4 + effect + 1
    plain = [l for a in jewels.cranium_pools.pool("Sapphire") for l in a.lines]
    assert all(l in cranium or any(boosted(p, percent) == l for p in plain) for l in added)
    assert "MEDIUM-LOW" in option.craft_route[0] and "0.5 route" in option.craft_route[1]
    assert option.price > rare_price("Jewel", priced)
