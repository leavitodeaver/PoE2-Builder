"""SEARCHC1: never an empty rune socket (Gear.fill_sockets), Bonded lines marked inactive on display (report/bonded.py)."""
from __future__ import annotations

import faulthandler
import re
import random

import pytest

from builder import clock
from builder.data import GameData
from builder.report import bonded
from builder.report.checklist import checklist
from builder.report.shopping import shopping_list
from builder.request.schema import BuildRequest
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.rares import socket_count


@pytest.fixture(scope="module")
def data() -> GameData:
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")


BODY = """Rarity: RARE
Optimizer Body Armour
Austere Garb
Item Level: 82
Sockets: S S
Rune: Perfect Desert Rune
Rune: Perfect Desert Rune
Implicits: 3
{enchant}{rune}Bonded: +40 to maximum Life
{enchant}{rune}Bonded: +40 to maximum Mana
{enchant}{rune}+44% to Fire Resistance
+44% to Cold Resistance"""


def test_bonded_lines_are_marked_inactive_without_a_source(data):
    base = bonded.base_of(BODY, data)
    assert base and base["name"] == "Austere Garb"
    marked = bonded.mark_text(BODY, base, build_unlocked=False).splitlines()
    assert marked[8] == "{enchant}{rune}Bonded: +40 to maximum Life" + bonded.INACTIVE
    assert marked[9].endswith(bonded.INACTIVE)
    assert marked[10] == "{enchant}{rune}+44% to Fire Resistance"  # (the rune's own line counts: unmarked)
    # Shaman's Wisdom of the Maji unlocks them: unmarked; marking twice changes nothing
    assert bonded.mark_text(BODY, base, build_unlocked=True) == BODY
    once = bonded.mark_text(BODY, base, False)
    assert bonded.mark_text(once, base, False) == once


def test_a_fox_idol_unlocks_only_the_idols_in_its_body_armour(data):
    base = bonded.base_of(BODY, data)
    effects = bonded.rune_effects(["Fox Idol", "Rabbit Idol", "Perfect Body Rune"], base, build_unlocked=False)
    by_name = {e["name"]: e for e in effects}
    assert by_name["Rabbit Idol"]["bonded"] and all(b["active"] for b in by_name["Rabbit Idol"]["bonded"])
    assert by_name["Perfect Body Rune"]["bonded"] and not any(b["active"] for b in by_name["Perfect Body Rune"]["bonded"])
    gloves = next(b for b in data.bases if b["type"] == "Gloves")  # (the Fox Idol's line is a Body Armour one)
    assert not bonded.idol_unlock(["Fox Idol"], gloves)


def test_the_build_unlock_is_read_from_its_tree(data):
    maji = next(iter(bonded.node_ids(data)))
    assert bonded.build_unlocks(f'<Tree><Spec nodes="1,{maji},3"/></Tree>', data)
    assert not bonded.build_unlocks('<Tree><Spec nodes="1,2,3"/></Tree>', data)


def test_the_shopping_list_shows_each_runes_lines_and_marks_bonded_and_fillers(data):
    result = {"request": {"budget": {"divines": 10, "gear_tier": "endgame"}},
              "gear": {"Body Armour": {"name": "Rare Body Armour", "base": "Austere Garb", "type": "Body Armour",
                                       "unique": False, "rare": True, "price": 2.0, "confidence": "estimate",
                                       "affixes": [], "runes": ["Perfect Desert Rune", "Perfect Body Rune"],
                                       "filler_runes": [{"name": "Perfect Body Rune", "why": "defence"}]}}}
    shop = shopping_list(result, data, {"augments": {"Perfect Desert Rune": 0.1, "Perfect Body Rune": 0.2}, "gems": {},
                                        "league": "Test"}, {}, variants=lambda name: None)
    desert, body = shop["items"][0]["runes"]
    assert desert["lines"] == ["+22% to Fire Resistance"]
    assert [b["text"] for b in desert["bonded"]] == ["+20 to maximum Life" + bonded.INACTIVE,
                                                     "+20 to maximum Mana" + bonded.INACTIVE]
    assert "choice" not in desert and "defence" in body["choice"] and "player's choice" in body["choice"]
    rows = checklist(result)
    assert [r["title"] for r in rows if r["kind"] == "your choice"] == ["Body Armour: Perfect Body Rune (socket filler)"]


def test_describe_carries_the_fillers():
    option = ItemOption("x", "Austere Garb", "Body Armour", None, "Rarity: Normal\nAustere Garb", False, 0.0, "found", 1,
                        runes=("Perfect Body Rune",), filler_runes=(("Perfect Body Rune", "defence"),))
    assert option.describe()["filler_runes"] == [{"name": "Perfect Body Rune", "why": "defence"}]


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


def test_a_socket_no_rune_scores_for_is_filled_and_the_score_holds(engine, monkeypatch):
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:  # (every augment priced, so the catalogue offers them all)
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Boots" and socket_count(b, False) >= 1)
    boots = ItemOption(name=base["name"], base=base["name"], type="Boots", sub_type=None, raw=normal_item(base),
                       unique=False, price=0.0, confidence="found", level=1)
    gear.put("Boots", boots)
    monkeypatch.setenv("POE2_SEARCH_FILL_SOCKETS", "1")
    before = build.item_deltas([{"slot": "Boots", "raw": boots.raw}])[0][0]
    filled = gear.socketed("Boots", boots, clock.now() + 600)
    print("PROBE fill", filled.runes, filled.filler_runes, before)
    assert len(filled.runes) == socket_count(base, False), "every socket holds an augment"
    after = build.item_deltas([{"slot": "Boots", "raw": filled.raw}])[0][0]
    assert after >= before - 1e-6, "a filler never lowers the score"
    for name, why in filled.filler_runes:
        assert name in filled.runes
        assert why in ("movement speed", "defence", "resistance") or why.startswith("quality of life")
    scored = [n for n in filled.runes if n not in {name for name, _ in filled.filler_runes}]
    # Switched off: the old behaviour, a socket nothing scored for stays empty
    monkeypatch.setenv("POE2_SEARCH_FILL_SOCKETS", "0")
    plain = gear.socketed("Boots", boots, clock.now() + 600)
    assert not plain.filler_runes and list(plain.runes) == scored
    # (SEARCHC1 item 4) why each item: with vs a plain base, in words
    gear.put("Boots", filled)
    rows = gear.why_items()
    print("PROBE why", rows.get("Boots"))
    boots_row = rows["Boots"]
    assert boots_row["item"] == boots.name and boots_row["reason"]
    assert "dps_with" in boots_row and "dps_without" in boots_row and isinstance(boots_row["floors"], list)
    if filled.filler_runes:
        assert "player's choice" in boots_row["reason"]


def test_the_genesis_tree_jewellery_bases_have_one_socket_of_their_armour_type(data):
    from builder.search.rares import rune_categories

    bases = {b["name"]: b for b in data.bases}
    for name, kind in (("Corona Amulet", "helmet"), ("Grasping Ring", "gloves"), ("Stalking Belt", "boots")):
        assert socket_count(bases[name], False) == 1
        assert rune_categories(bases[name]) == {"armour", kind}
    plain_amulet = next(b for b in data.bases if b["type"] == "Amulet" and "Socketed" not in (b.get("implicit") or ""))
    assert socket_count(plain_amulet, False) == 0 and rune_categories(plain_amulet) == {"amulet"}


def test_pob_counts_a_rune_in_a_corona_amulet(engine):
    from builder.search.objective import Objective
    from builder.search.rares import with_runes
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    amulet = normal_item(ctx.bases_by_name["Corona Amulet"])
    (_, bare), (_, socketed) = build.item_deltas([{"slot": "Amulet", "raw": amulet},
                                                  {"slot": "Amulet", "raw": with_runes(amulet, ["Perfect Body Rune"])}])
    print("PROBE corona", bare.get("Life"), socketed.get("Life"))
    assert socketed["Life"] > bare["Life"], "the socket's rune counts (as a Helmet's)"


def test_a_filler_only_item_is_filled_again_without_paying_twice(engine, monkeypatch):
    from builder.search.gear import Gear, SlotSpec, only_fillers
    from builder.search.objective import Objective
    from builder.search.state import Build

    monkeypatch.setenv("POE2_SEARCH_FILL_SOCKETS", "1")
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Boots" and socket_count(b, False) >= 1)
    boots = ItemOption(name=base["name"], base=base["name"], type="Boots", sub_type=None, raw=normal_item(base),
                       unique=False, price=0.0, confidence="found", level=1)
    filled = gear.socketed("Boots", boots, clock.now() + 600)
    if not only_fillers(filled):
        pytest.skip("a rune scored on these boots: nothing to refill")
    again = gear.socketed("Boots", filled, clock.now() + 600)
    assert again.price == pytest.approx(filled.price) and len(again.runes) == len(filled.runes)
    # no time left: the item comes back as it was, fillers kept
    assert gear.socketed("Boots", filled, clock.now() - 1) is filled


def test_the_extra_socket_routes_are_tried_only_when_switched_on(engine, monkeypatch):
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    ctx.payload["cultivation_prices"] = {"orb": 1.0, "vaal": 0.01}
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Wand" and (b.get("socketLimit") or 0) == 3)
    wand = ItemOption(name=base["name"], base=base["name"], type="Wand", sub_type=None, raw=normal_item(base),
                      unique=False, price=0.0, confidence="found", level=1)
    gear.put("Weapon 1", wand)
    monkeypatch.setenv("POE2_SEARCH_EXTRA_SOCKETS", "0")
    plain = gear.socketed("Weapon 1", wand, clock.now() + 600)
    assert plain.sockets == 0 and len(plain.runes) <= 1
    monkeypatch.setenv("POE2_SEARCH_EXTRA_SOCKETS", "1")
    more = gear.socketed("Weapon 1", wand, clock.now() + 600)
    print("PROBE extra", more.sockets, more.runes, more.price, more.craft_route)
    plain_score = build.item_deltas([{"slot": "Weapon 1", "raw": plain.raw}])[0][0]
    more_score = build.item_deltas([{"slot": "Weapon 1", "raw": more.raw}])[0][0]
    assert more_score >= plain_score - 1e-6
    if more.sockets:
        assert more.sockets in (2, 3) and len(more.runes) == more.sockets
        assert any(step.startswith("Sockets (") for step in more.craft_route)
        assert (more.sockets == 3) == ("Corrupted" in more.raw.splitlines())
        assert more.describe()["sockets"] == more.sockets


class _StubRunes:
    def __init__(self, prices, limits=None):
        self.prices, self.limits = prices, limits or {}

    def price(self, name):
        return self.prices.get(name, 0.0)

    def limit(self, name):
        return self.limits.get(name)


class _StubBuild:
    """item_deltas: every case scores `score`; its stats from `stats_for(raw)`."""
    def __init__(self, score, stats_for):
        self.score_value, self.stats_for = score, stats_for

    def item_deltas(self, cases):
        return [(self.score_value(case["raw"]), self.stats_for(case["raw"])) for case in cases]


def _stub_gear(prices, score, stats_for, limits=None):
    from builder.search.gear import Gear

    gear = Gear.__new__(Gear)
    gear.runes = _StubRunes(prices, limits)
    gear.build = _StubBuild(score, stats_for)
    return gear


@pytest.fixture(scope="module")
def boots_base(data):
    return next(b for b in data.bases if b["name"] == "Daggerfoot Shoes")


def _runes_in(raw):
    return [line[6:] for line in raw.splitlines() if line.startswith("Rune: ")]


def test_the_filler_classes_in_order_and_never_a_lower_score(boots_base):
    BOOTS = boots_base
    names = ["Farrul's Rune of the Chase", "Perfect Body Rune", "Lesser Body Rune", "Perfect Storm Rune",
             "Soul Core of Topotante", "Kurgal's Gaze", "Greater Rune of Alacrity"]
    prices = {n: 0.1 for n in names} | {"Lesser Body Rune": 0.01}

    def stats(raw):
        runes = _runes_in(raw)
        return {"Life": 1000 + 75 * runes.count("Perfect Body Rune") + 70 * runes.count("Lesser Body Rune"),
                "LightningResist": 75, "FireResist": 75}

    def score(raw):  # (the Chase rune lowers the score here: it must never be picked)
        return 1.0 - 0.5 * ("Farrul's Rune of the Chase" in _runes_in(raw))

    gear = _stub_gear(prices, score, stats, limits={"Kurgal's Gaze": ("AncientAugment", 1)})
    raw = "Rarity: Normal\nDaggerfoot Shoes"
    chosen: list[str] = []
    fillers = gear.fill_sockets("Boots", BOOTS, raw, chosen, 0, 2, 1.0, 10.0, names, lambda n: True, float("inf"))
    # defence first (movement speed lowered the score): the cheapest within 10% of the best Life gain, twice
    assert fillers == [("Lesser Body Rune", "defence"), ("Lesser Body Rune", "defence")] and chosen == ["Lesser Body Rune"] * 2
    # no defence on offer: quality of life in the user's order (debuffs on you before the cheapest), no Ancient
    gear = _stub_gear(prices, lambda raw: 1.0, lambda raw: {"Life": 1000}, limits={"Kurgal's Gaze": ("AncientAugment", 1)})
    chosen = []
    fillers = gear.fill_sockets("Boots", BOOTS, raw, chosen, 0, 1, 1.0, 10.0,
                                ["Kurgal's Gaze", "Soul Core of Topotante", "Greater Rune of Alacrity"], lambda n: True,
                                float("inf"))
    assert fillers == [("Greater Rune of Alacrity", "quality of life: curses and debuffs on you")]
    # unaffordable: the socket stays empty
    chosen = []
    assert gear.fill_sockets("Boots", BOOTS, raw, chosen, 0, 1, 1.0, 0.05, ["Greater Rune of Alacrity"], lambda n: True,
                             float("inf")) == [] and chosen == []


def test_movement_speed_comes_first_when_it_doesnt_lower_the_score(boots_base):
    BOOTS = boots_base
    gear = _stub_gear({}, lambda raw: 1.0, lambda raw: {"Life": 1000 + 75 * _runes_in(raw).count("Perfect Body Rune")})
    chosen: list[str] = []
    fillers = gear.fill_sockets("Boots", BOOTS, "Rarity: Normal\nDaggerfoot Shoes", chosen, 0, 1, 1.0, 10.0,
                                ["Perfect Body Rune", "Farrul's Rune of the Chase"], lambda n: True, float("inf"))
    assert fillers == [("Farrul's Rune of the Chase", "movement speed")]


def test_one_emergent_rune_may_fill_a_socket_but_no_other_ancient(boots_base):
    """The manager's ruling (23 Nov 05:00): a filler MAY be one Emergent rune when the build has no Ancient Augment."""
    ancient = {"Emergent Vigour": ("AncientAugment", 1), "Kurgal's Gaze": ("AncientAugment", 1)}
    gear = _stub_gear({"Emergent Vigour": 2.0, "Kurgal's Gaze": 0.01}, lambda raw: 1.0, lambda raw: {"Life": 1000},
                      limits=ancient)
    raw = "Rarity: Normal\nDaggerfoot Shoes"
    chosen: list[str] = []
    fillers = gear.fill_sockets("Boots", boots_base, raw, chosen, 0, 1, 1.0, 10.0, ["Kurgal's Gaze", "Emergent Vigour"],
                                lambda n: True, float("inf"))
    assert fillers == [("Emergent Vigour", "quality of life: an Emergent augment (the build's one Ancient Augment)")]
    # the build already holds an Ancient Augment: the limit check says no, so nothing (never a second one)
    chosen = []
    assert gear.fill_sockets("Boots", boots_base, raw, chosen, 0, 1, 1.0, 10.0, ["Emergent Vigour"],
                             lambda n: n not in ancient, float("inf")) == []


def test_a_scoring_pick_evicts_a_filler_holding_its_limit():
    from builder.search.gear import Gear

    class Build:
        def __init__(self):
            self.equipped = {}

        def equip(self, slot, option):
            self.equipped[slot] = option
            return 1.0

    gear = Gear.__new__(Gear)
    gear.runes = _StubRunes({"Emergent Vigour": 2.0, "Kurgal's Gaze": 1.0},
                            {"Emergent Vigour": ("AncientAugment", 1), "Kurgal's Gaze": ("AncientAugment", 1)})
    gear.build = Build()
    helmet = ItemOption("h", "Iron Greathelm", "Helmet", None, "Rarity: Normal\nIron Greathelm\nSockets: S\nRune: Emergent Vigour",
                        False, 2.5, "found", 1, runes=("Emergent Vigour",),
                        filler_runes=(("Emergent Vigour", "quality of life"),))
    gear.build.equipped["Helmet"] = helmet
    boots = ItemOption("b", "Daggerfoot Shoes", "Boots", None, "Rarity: Normal\nDaggerfoot Shoes\nSockets: S\nRune: Kurgal's Gaze",
                       False, 1.0, "found", 1, runes=("Kurgal's Gaze",))
    gear.build.equipped["Boots"] = boots
    gear.evict_fillers("Boots", boots, 1.0)
    left = gear.build.equipped["Helmet"]
    assert left.runes == () and left.filler_runes == () and left.price == pytest.approx(0.5)
    assert "Emergent Vigour" not in left.raw and gear.build.equipped["Boots"] is boots


def test_fill_empty_sockets_the_items_it_adds(engine, monkeypatch):
    from builder.search.gear import Gear, SlotSpec
    from builder.search.objective import Objective
    from builder.search.state import Build

    monkeypatch.setenv("POE2_SEARCH_FILL_SOCKETS", "1")
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    for rune in ctx.data.runes:
        ctx.augment_prices.setdefault(rune["name"], 0.01)
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    gear.slots = {"Boots": gear.slots["Boots"]}  # (one slot, for time)
    build.refresh()
    filled = gear.fill_empty()
    print("PROBE fill_empty", filled, build.equipped.get("Boots") and build.equipped["Boots"].runes)
    if not filled:
        pytest.skip("no boots filler on a fresh Spark build")
    boots = build.equipped["Boots"]
    base = ctx.bases_by_name[boots.base]
    assert len(boots.runes) == socket_count(base, False), "the filler item's sockets are filled"


def test_void_flux_lines_follow_poe2dbs_table():
    from builder.search.rares import Affix, void_flux_line, void_fluxed

    assert void_flux_line("+44% to Fire Resistance") == "+26% to Chaos Resistance"  # (41-45 -> 24-27)
    assert void_flux_line("+28% to Lightning Resistance") == "+18% to Chaos Resistance"  # (26-30 -> 16-19)
    assert void_flux_line("+6% to Cold Resistance") == "+4% to Chaos Resistance"
    assert void_flux_line("+15% to Fire and Chaos Resistances") == "+26% to Chaos Resistance"
    assert void_flux_line("+2% to Maximum Cold Resistance") == "+2% to Maximum Chaos Resistance"
    assert void_flux_line("30% increased Movement Speed") == "30% increased Movement Speed"
    assert void_flux_line("+50% to Fire Resistance") is None  # (out of the table: no guess)
    assert void_flux_line("+12% to Fire and Cold Resistances") is None
    fire = Affix("FireResist8", "FireResistance", "Suffix", 1, ("+44% to Fire Resistance",))
    speed = Affix("MovementVelocity5", "MovementVelocity", "Prefix", 1, ("30% increased Movement Speed",))
    out = void_fluxed([speed, fire])
    assert out[0] is speed and out[1].lines == ("+26% to Chaos Resistance",) and out[1].id == "FireResist8+VoidFlux"
    assert void_fluxed([speed]) is None


def test_the_flux_trial_prices_the_route_and_turns_every_elemental_resistance(engine, monkeypatch):
    from builder.search.gear import Gear, SlotSpec, affix_limits
    from builder.search.objective import Objective
    from builder.search.rares import rare_price, rare_text
    from builder.search.state import Build

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    ctx.payload["flux_prices"] = {"Void Flux": 0.13}
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = ctx.bases_by_name["Daggerfoot Shoes"]
    level = gear.pools.item_level
    current = gear.deltas("Boots", [rare_text("Boots", base, level, [])])[0][0]
    option = ItemOption("Daggerfoot Shoes", "Daggerfoot Shoes", "Boots", None, normal_item(base), False, 0.0, "found", 1)
    fluxed = gear.flux_trial("Boots", base, option, level, gear.pools, affix_limits(base), [], current, 1e6,
                             clock.now() + 300)
    print("PROBE flux", fluxed and fluxed.affixes, fluxed and fluxed.price)
    if fluxed is None:
        pytest.skip("the route didn't beat a plain rare on a fresh Spark build")
    assert any("Chaos Resistance" in line for line in fluxed.affixes)
    assert not any(re.search(r"(Fire|Cold|Lightning) Resistance", line) for line in fluxed.affixes)
    assert fluxed.craft_route and fluxed.craft_route[0].startswith("Void Flux")
