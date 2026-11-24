"""JEWEL1: full crafted jewels (every affix slot filled; the 0.5 craft on both sides), jewel sockets valued in the tree
(behind POE2_SEARCH_JEWEL_SOCKETS, OFF by default) and Zarokh's Gift anointed with its Sinister socket filled
(builder/search/jewels.py, tree.py's socket valuation, anoints.py)."""

from __future__ import annotations

import dataclasses
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
