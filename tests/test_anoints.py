"""Anoints (builder/search/anoints.py): recipes, how many an item takes, the item text, prices, and through a real
engine that PoB counts the "Allocates" line and the gear step picks a notable that gains."""

from __future__ import annotations

import dataclasses
import faulthandler
import random

import pytest

from builder import clock
from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import anoints
from builder.search.context import Context, ItemOption, make_payload, normal_item
from builder.search.gear import Gear, SlotSpec
from builder.search.objective import Objective
from builder.search.state import Build


def option(name: str, base: str, item_type: str, raw: str, unique: bool = False) -> ItemOption:
    return ItemOption(name=name, base=base, type=item_type, sub_type=None, raw=raw, unique=unique, price=1.0,
                      confidence="high", level=1)


def test_recipes_cover_every_anointable_notable():
    recipes = anoints.recipes("0_5")
    assert len(recipes) == 875  # 874 three-emotion recipes on notables plus Zarokh's Gift (left out, labelled)
    assert len(set(recipes.values())) == len(recipes)  # every combination is a different notable
    assert {e for r in recipes.values() for e in r} == set(anoints.EMOTIONS)
    nodes = {n["id"]: n for n in GameData().tree["nodes"]}
    assert {nodes[i]["type"] for i in recipes} == {"Notable", "Socket"}
    assert [nodes[i]["name"] for i in recipes if nodes[i]["type"] == "Socket"] == ["Zarokh's Gift"]
    names = {nodes[i]["name"] for i in recipes}
    assert {"Bastion of Light", "Paragon", "Magnum Opus", "Zarokh's Gift"} <= names  # anoint-only ones are in


def test_capacity_per_item():
    data = GameData()
    unique = {u["name"]: u["raw"] for u in data.uniques}
    assert anoints.capacity(option("Strugglescream", "Stellar Amulet", "Amulet",
                                   unique["Strugglescream"], True)) == 4
    assert anoints.capacity(option("x", "Stellar Amulet", "Amulet", "Rarity: Normal\nStellar Amulet")) == 1
    assert anoints.capacity(option("x", "Twisted Amulet", "Amulet", "Rarity: Normal\nTwisted Amulet")) == 2
    assert anoints.capacity(option("The Auspex", "Exquisite Vest", "Body Armour", unique["The Auspex"], True)) == 1
    assert anoints.capacity(option("Horror's Flight", "Engraved Bracers", "Gloves", unique["Horror's Flight"], True)) == 1
    assert anoints.capacity(option("x", "Gold Ring", "Ring", "Rarity: Normal\nGold Ring")) == 0
    assert anoints.capacity(option("x", "Engraved Bracers", "Gloves", "Rarity: Normal\nEngraved Bracers")) == 0


def test_item_text_round_trip():
    plain = "Rarity: Normal\nGold Amulet"
    one = anoints.with_anoints(plain, ["Paragon"])
    assert one.splitlines() == ["Rarity: Normal", "Gold Amulet", "Implicits: 1", "{enchant}Allocates Paragon"]
    assert anoints.strip(one) == plain
    with_implicit = "Rarity: Normal\nGold Amulet\nImplicits: 1\n12% increased Rarity of Items found\nfoo"
    two = anoints.with_anoints(with_implicit, ["A", "B"])
    assert two.splitlines()[2:6] == ["Implicits: 3", "{enchant}Allocates A", "{enchant}Allocates B",
                                     "12% increased Rarity of Items found"]
    assert anoints.with_anoints(two, ["C"]).splitlines()[2:5] == ["Implicits: 2", "{enchant}Allocates C",
                                                                  "12% increased Rarity of Items found"]
    assert anoints.strip(two) == with_implicit


def test_cost_sums_the_three_emotions():
    prices = {"Diluted Liquid Ire": 0.1, "Liquid Envy": 0.2, "Potent Liquid Contempt": 3.0}
    assert anoints.cost(("Ire", "Envy", "Contempt"), prices) == pytest.approx(3.3)
    assert anoints.cost(("Ire", "Envy", "Fear"), prices) is None


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


def test_the_gear_step_anoints_an_amulet_with_a_notable_pob_counts(engine):
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["name"] == "Stellar Amulet")
    amulet = ItemOption(name=base["name"], base=base["name"], type="Amulet", sub_type=None, raw=normal_item(base),
                        unique=False, price=0.0, confidence="found", level=1)
    gear.put("Amulet", amulet)
    before = build.score
    screened = gear.anoint_screen()
    assert screened, "a fresh Spark build gains from some notable"
    # An "Allocates" line on the item is worth what allocating the node is (the item what-if sees the granted passive)
    name = screened[0]
    node_id = gear.anoint_recipes()[name][0]
    by_node = build.node_deltas([{"add": [node_id]}])[0][0]
    by_item = build.item_deltas([{"slot": "Amulet", "raw": anoints.with_anoints(amulet.raw, [name])}])[0][0]
    assert by_item == pytest.approx(by_node, rel=0.01) and by_item > before
    chosen = gear.anointed("Amulet", amulet, clock.now() + 600)
    assert len(chosen.anoints) == 1 and f"{{enchant}}Allocates {chosen.anoints[0]}" in chosen.raw
    assert chosen.price == pytest.approx(gear.anoint_cost(chosen.anoints[0]))
    gear.put("Amulet", chosen)
    assert build.score > before
    # Chosen again for the same item, it keeps its anoint (not offered elsewhere, not dropped as "already granted")
    assert gear.anointed("Amulet", chosen, clock.now() + 600).anoints
    assert chosen.describe()["anoints"] == list(chosen.anoints)


def test_capacity_counts_a_raven_touched_shard_only_in_a_helmet():
    helmet = option("x", "Iron Greathelm", "Helmet", "Rarity: Normal\nIron Greathelm")
    assert anoints.capacity(helmet) == 0
    assert anoints.capacity(dataclasses.replace(helmet, runes=(anoints.SHARD,))) == 1
    gloves = option("x", "Engraved Bracers", "Gloves", "Rarity: Normal\nEngraved Bracers")
    assert anoints.capacity(dataclasses.replace(gloves, runes=(anoints.SHARD,))) == 0


def test_the_gear_step_puts_a_raven_touched_shard_in_a_helmet_with_its_anoint(engine, monkeypatch):
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    ctx.augment_prices[anoints.SHARD] = 0.5
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    base = next(b for b in ctx.data.bases if b["type"] == "Helmet" and (b.get("socketLimit") or 0) >= 3)
    helmet = ItemOption(name=base["name"], base=base["name"], type="Helmet", sub_type=None, raw=normal_item(base),
                        unique=False, price=0.0, confidence="found", level=1)
    gear.put("Helmet", helmet)
    deadline = clock.now() + 600
    chosen = gear.shard_trial("Helmet", helmet, deadline)
    assert chosen.runes == (anoints.SHARD,) and len(chosen.anoints) == 1
    assert "Rune: Raven-Touched Shard" in chosen.raw and f"{{enchant}}Allocates {chosen.anoints[0]}" in chosen.raw
    assert chosen.price == pytest.approx(0.5 + gear.anoint_cost(chosen.anoints[0]))
    before = build.score
    gear.put("Helmet", chosen)
    assert build.score > before
    gear.put("Helmet", helmet)
    # Not on a Corrupted or Sanctified helmet, not below level 60, and never a second shard
    for sealed in ("Corrupted", "Sanctified"):
        closed = dataclasses.replace(helmet, raw=f"{helmet.raw}\n{sealed}")
        assert gear.shard_trial("Helmet", closed, deadline) is closed
    assert ctx.level >= 60
    with monkeypatch.context() as m:
        m.setattr(Context, "level", property(lambda self: 59))
        assert gear.shard_trial("Helmet", helmet, deadline) is helmet
    assert gear.shard_trial("Helmet", chosen, deadline) is chosen
