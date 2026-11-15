"""Way of the Stonefist's glove transform (COV34; builder/search/stonefist.py). No engine: the texts only. PoB scoring the
transformed text (the Fists of Stone base's per-level defences, the HandWraps lines) was probed: POE2T2563 / POE2T2570."""
from __future__ import annotations

import pytest

from builder.search import stonefist


@pytest.fixture(scope="module")
def data():
    from builder.data import GameData

    try:
        found = GameData()
        found.mods
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return found


RARE = ["Rarity: Rare", "Optimizer Gloves", "Gauze Wraps", "Item Level: 82", "Quality: 20", "Implicits: 0",
        "+207 to maximum Life", "+43% to Fire Resistance", "Adds 15 to 27 Physical Damage to Attacks",
        "15% increased Attack Speed", "96% increased Evasion and Energy Shield", "+33 to Intelligence"]


def test_a_rares_lines_become_their_hand_wraps_mods_at_the_same_roll(data):
    """Game Mods (build 26476662): HandWraps<id> for each regular mod id; PoB's pools carry both texts. The tier is the
    one whose range holds the value (15% Attack Speed is IncreasedAttackSpeed4, 14-16%, not a unique's 5-10%)."""
    text, kept = stonefist.transform("\n".join(RARE), data.mods)
    lines = text.split("\n")
    assert lines[:5] == ["Rarity: Rare", "Optimizer Gloves", "Fists of Stone", "Item Level: 82", "Quality: 20"]
    assert lines[5:8] == ["Implicits: 2", *stonefist.IMPLICITS]
    assert lines[8:] == ["17% less damage taken while on Low Life", "+3% to Maximum Fire Resistance",
                         "+38% to Fire Resistance", "Attacks Gain 22% of Damage as Extra Physical Damage",
                         "28% chance to gain Onslaught for 4 seconds on Hit",
                         "20% more Global Evasion Rating and Energy Shield", "36% increased Cooldown Recovery Rate"]
    assert kept == []


def test_a_line_with_no_pair_stays_and_is_named(data):
    text, kept = stonefist.transform("\n".join(RARE[:6] + ["Some line no mod has"]), data.mods)
    assert text.endswith("Some line no mod has") and kept == ["Some line no mod has"]


def test_a_unique_takes_poe2dbs_transformed_text(data):
    """poe2db's Fists of Stone page (37 uniques; builder/data/stonefist_uniques.json): The Master's Reach."""
    raw = next(u["raw"] for u in data.uniques if u["name"] == "The Master's Reach")
    text, kept = stonefist.transform(raw, data.mods)
    lines = text.split("\n")
    assert lines[:2] == ["The Master's Reach", "Fists of Stone"] and not kept
    assert "(55-65)% of damage taken from enemies with an Open Weakness Recouped as Life and Energy Shield" in lines
    assert "(200-300)% increased Armour and Energy Shield" not in lines
    assert len(stonefist.unique_texts()) == 37


class _Engine:
    """Records the engine calls; 'facts' answers whether the notable is allocated."""

    def __init__(self, allocated: bool):
        self.allocated, self.calls = allocated, []

    def call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == "facts":
            passives = [{"name": stonefist.NOTABLE, "ascendancy": "Martial Artist"}] if self.allocated else []
            return {"passives": passives}
        if method == "allocate":
            node = 39595
            if node in kwargs.get("add", []):
                self.allocated = True
            if node in kwargs.get("remove", []):
                self.allocated = False
        return {"stats": {}, "results": []}


def _build(data, allocated: bool):
    from types import SimpleNamespace

    from builder.search.state import Build

    engine = _Engine(allocated)
    return Build(engine, SimpleNamespace(data=data), objective=None), engine


def _sent(engine, method):
    return [kwargs for m, kwargs in engine.calls if m == method]


def test_the_search_sends_transformed_gloves_only_with_the_notable(data):
    """state.Build.call (the manager 15:45 allowed COV-A this hook in SEARCH's file): the Gloves item of an equip or a
    what-if goes to PoB transformed when the tree has Way of the Stonefist; other slots and builds without it as is."""
    from builder.search.context import ItemOption

    plain = "\n".join(RARE)
    build, engine = _build(data, allocated=True)
    build.call("equip", slot="Gloves", raw=plain, fields=[])
    build.call("equip", slot="Helmet", raw="x", fields=[])
    build.call("itemDeltas", cases=[{"slot": "Gloves", "raw": plain}, {"slot": "Boots", "raw": "y"}], fields=[])
    gloves, helmet = _sent(engine, "equip")
    assert "Fists of Stone" in gloves["raw"] and helmet["raw"] == "x"
    cases = _sent(engine, "itemDeltas")[0]["cases"]
    assert "Fists of Stone" in cases[0]["raw"] and cases[1]["raw"] == "y"
    assert len(_sent(engine, "facts")) == 1  # read once per tree change

    build, engine = _build(data, allocated=False)
    build.call("equip", slot="Gloves", raw=plain, fields=[])
    assert _sent(engine, "equip")[0]["raw"] == plain
    # allocating the notable re-equips the bought (plain) gloves, now transformed; removing it puts them back plain
    build.equipped["Gloves"] = ItemOption(name="Optimizer Gloves", base="Gauze Wraps", type="Gloves", sub_type=None,
                                          raw=plain, unique=False, price=0.0, confidence="high", level=1, rare=True)
    build.call("allocate", add=[39595], remove=[], fields=[])
    assert "Fists of Stone" in _sent(engine, "equip")[-1]["raw"]
    build.call("allocate", add=[], remove=[39595], fields=[])
    assert _sent(engine, "equip")[-1]["raw"] == plain
    build.call("allocate", add=[123], remove=[], fields=[])  # another node: nothing re-equipped
    assert len(_sent(engine, "equip")) == 3


def test_an_already_transformed_item_is_never_transformed_twice(data):
    once, _ = stonefist.transform("\n".join(RARE), data.mods)
    assert stonefist.transform(once, data.mods) == (once, [])
