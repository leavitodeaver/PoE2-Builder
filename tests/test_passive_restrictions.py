"""Equipment restrictions and augment counts from passive lines PoB ignores or never feeds (COV-B, COVB3).

Smith's Masterwork "Can only use a Normal Body Armour" and Dance with Death "Can't use Helmets": the gear step
offers and keeps only what the build may use (gear.SLOT_RESTRICTIONS). Idolatry's "per Idol" / "per non-Idol
Augment in your Equipment" lines: the bridge counts the equipment's augments (setAugmentCounts)."""

from __future__ import annotations

import faulthandler
import random

import pytest

from builder.search.context import ItemOption
from builder.search.gear import Gear, SlotSpec, is_normal


def item(name: str, raw: str, item_type: str = "Body Armour", unique: bool = False, rare: bool = False,
         price: float = 0.0) -> ItemOption:
    return ItemOption(name=name, base="Base", type=item_type, sub_type=None, raw=raw, unique=unique, price=price,
                      confidence="found", level=1, rare=rare)


NORMAL = item("Plain Vest", "Rarity: Normal\nPlain Vest")
NORMAL_RUNED = item("Runed Vest", "Rarity: Normal\nRuned Vest\nSockets: S\nRune: Desert Rune")
RARE = item("Optimizer Body Armour", "Rarity: Rare\nOptimizer Body Armour\nPlain Vest", rare=True)
UNIQUE = item("Some Unique", "Rarity: Unique\nSome Unique\nPlain Vest", unique=True, price=1.0)
HELMET = item("Plain Cap", "Rarity: Normal\nPlain Cap", item_type="Helmet")


class FakeCtx:
    budget = float("inf")


class FakeBuild:
    """Just what the gear step's restriction code touches: lines, commits, equip and item what-ifs."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.commits = 0
        self.equipped: dict[str, ItemOption] = {}
        self.locked_slots: set[str] = set()
        self.score = 0.0
        self.ctx = FakeCtx()

    def _build_texts(self) -> list[str]:
        return self.lines

    def spent(self) -> float:
        return 0.0

    def equip(self, slot: str, option: ItemOption | None, active: bool | None = None) -> float:
        if option is None:
            self.equipped.pop(slot, None)
        else:
            self.equipped[slot] = option
        self.commits += 1
        return self.score

    def item_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        # a runed vest scores best, then the plain one: the enforcement must pick by score
        return [(2.0 if "Rune:" in case["raw"] else 1.0, {}) for case in cases]


def gear_for(lines: list[str]) -> tuple[Gear, FakeBuild]:
    build = FakeBuild(lines)
    gear = Gear.__new__(Gear)
    gear.build, gear.ctx, gear.resolve = build, build.ctx, None
    gear.slots = {"Body Armour": SlotSpec("Body Armour"), "Helmet": SlotSpec("Helmet")}
    gear._options = {"Body Armour": [NORMAL, NORMAL_RUNED, RARE, UNIQUE], "Helmet": [HELMET]}
    gear.rares = {"Body Armour": RARE}
    return gear, build


def test_is_normal():
    assert is_normal(NORMAL) and is_normal(NORMAL_RUNED)
    assert not is_normal(RARE) and not is_normal(UNIQUE)
    assert not is_normal(item("Magic", "Rarity: Magic\nMagic Vest"))


def test_no_restriction_without_the_line():
    gear, _ = gear_for(["+200 to Armour for each Connected Notable Passive Skill Allocated"])
    assert gear.restriction("Body Armour") is None and gear.restriction("Helmet") is None
    assert gear.options("Body Armour") == [NORMAL, NORMAL_RUNED, RARE, UNIQUE]


def test_smiths_masterwork_offers_and_keeps_only_a_normal_body_armour():
    gear, build = gear_for(["Can only use a Normal Body Armour"])
    assert gear.restriction("Body Armour") == "normal" and gear.restriction("Helmet") is None
    assert gear.options("Body Armour") == [NORMAL, NORMAL_RUNED]
    assert gear.build_rare("Body Armour", deadline=float("inf")) is None
    assert gear.allowed("Body Armour", NORMAL_RUNED) and not gear.allowed("Body Armour", RARE)
    # Allocated while a rare is worn: the rare comes off for the best-scoring Normal one, and the cached rare goes
    build.equipped["Body Armour"] = RARE
    notes = gear.enforce_restrictions()
    assert build.equipped["Body Armour"] is NORMAL_RUNED and "Body Armour" not in gear.rares and notes
    # Unallocated again (the line gone, a new commit): everything is offered again
    build.lines, build.commits = [], build.commits + 1
    assert gear.options("Body Armour") == [NORMAL, NORMAL_RUNED, RARE, UNIQUE]


def test_a_required_unique_body_armour_is_left_and_reported():
    gear, build = gear_for(["Can only use a Normal Body Armour"])
    build.equipped["Body Armour"] = UNIQUE
    build.locked_slots.add("Body Armour")
    notes = gear.enforce_restrictions()
    assert build.equipped["Body Armour"] is UNIQUE and notes and "required" in notes[0]


def test_dance_with_death_takes_the_helmet_off():
    gear, build = gear_for(["Can't use Helmets", "Your Critical Hit Chance is Lucky"])
    assert gear.restriction("Helmet") == "empty"
    assert gear.options("Helmet") == [] and gear.build_rare("Helmet", deadline=float("inf")) is None
    build.equipped["Helmet"] = HELMET
    gear.enforce_restrictions()
    assert "Helmet" not in build.equipped


# Idolatry (engine) ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def test_idol_and_non_idol_augments_are_counted_for_idolatrys_lines(engine):
    from builder.data import GameData
    from builder.search.context import normal_item
    from builder.search.rares import socket_count, with_runes

    data = GameData()
    # Body armours have 2 augment sockets, gloves 1 (and Idol of Sirrius is a gloves Idol)
    body = next(b for b in data.bases if b["type"] == "Body Armour" and socket_count(b, False) >= 2)
    hands = next(b for b in data.bases if b["type"] == "Gloves" and socket_count(b, False) >= 1)
    fields = ["LightningResist", "ColdResist", "Spirit"]
    engine.call("newBuild")
    # The lines Idolatry and the Carved Majesty rune have, on an amulet (PoB parses both into its never-set multipliers)
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Stellar Amulet", "Implicits: 0",
                        "-4% to all Elemental Resistances per non-Idol Augment in your Equipment",
                        "+3 to Spirit per Idol socketed in your Equipment"])
    engine.call("equip", slot="Amulet", raw=amulet, fields=fields)

    def text(base: dict, runes: list[str]) -> str:
        return with_runes(normal_item(base), runes) if runes else normal_item(base)

    def wear(slot: str, base: dict, runes: list[str]) -> dict:
        return engine.call("equip", slot=slot, raw=text(base, runes), fields=fields)["stats"]

    wear("Gloves", hands, [])
    none = wear("Body Armour", body, [])
    # Desert Rune is fire resistance on armour, Glacial Rune cold: lightning moves by the penalty alone
    two_runes = wear("Body Armour", body, ["Desert Rune", "Glacial Rune"])
    assert none["LightningResist"] - two_runes["LightningResist"] == pytest.approx(8)
    assert two_runes["Spirit"] == pytest.approx(none["Spirit"])
    with_idol = wear("Gloves", hands, ["Idol of Sirrius"])
    assert with_idol["Spirit"] - none["Spirit"] == pytest.approx(3)
    assert with_idol["LightningResist"] == pytest.approx(two_runes["LightningResist"])  # an Idol isn't non-Idol
    # Item what-ifs see the count too (it's set on every calculation): the runes out of the body armour
    result = engine.call("itemDeltas", cases=[{"slot": "Body Armour", "raw": text(body, [])}], fields=fields)["results"][0]
    result = result.get("stats", result)
    assert result["LightningResist"] == pytest.approx(none["LightningResist"])
    assert result["Spirit"] - none["Spirit"] == pytest.approx(3)
