"""The user's own items (the user, 22 Nov: "when you select some piece of gear in the utility as a prerequisite for the
build, there needs to be an option to not include it in the budget, if you already have an expensive weapon or something
and want to build around it"; SEARCHB3): owned required uniques and pasted items are equipped, locked and never counted
in the budget."""
from __future__ import annotations

import faulthandler

import pytest
from pydantic import ValidationError

from builder.data import GameData
from builder.request.constraints import compile_request
from builder.request.owned import read_item
from builder.request.schema import BuildRequest

MAUL = """Item Class: Two Hand Maces
Rarity: Rare
Doom Crusher
Blacksmith Maul
--------
Item Level: 80
--------
Adds 20 to 40 Physical Damage
+25% to Fire Resistance"""

RING = """Rarity: RARE
Woe Loop
Amethyst Ring
Item Level: 80
+30% to Cold Resistance"""

SHIELD = """Rarity: Normal
Aegis Buckler"""


@pytest.fixture(scope="module")
def data():
    return GameData()


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()  # (LuaJIT's errors reach pytest's faulthandler as "fatal exceptions")
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


# Schema ------------------------------------------------------------------------------------------

def test_an_owned_unique_must_be_required_or_the_weapon_unique():
    BuildRequest(require={"uniques": ["Goldrim"]}, owned_uniques=["Goldrim"])
    BuildRequest(weapon_unique="Brain Rattler", owned_uniques=["Brain Rattler"])
    with pytest.raises(ValidationError, match="marked owned but isn't a required unique"):
        BuildRequest(owned_uniques=["Goldrim"])


def test_an_owned_items_slot_must_be_a_real_slot():
    BuildRequest(owned_items=[{"text": RING, "slot": "Ring 2"}])
    with pytest.raises(ValidationError, match="isn't one of"):
        BuildRequest(owned_items=[{"text": RING, "slot": "Finger"}])


# Reading a pasted item ---------------------------------------------------------------------------

def test_a_game_copied_two_handed_rare_reads_its_name_base_and_only_slot(data):
    read = read_item(MAUL, data)
    assert (read.error, read.rarity, read.name, read.base, read.type) == (None, "RARE", "Doom Crusher", "Blacksmith Maul",
                                                                          "Two Hand Mace")
    assert read.two_handed and read.slots == ["Weapon 1"] and read.slot == "Weapon 1"


def test_a_ring_must_say_its_slot(data):
    assert "choose its slot: Ring 1 or Ring 2" in read_item(RING, data).error
    assert read_item(RING, data, "Ring 2").slot == "Ring 2"
    assert "can't go in Helmet" in read_item(RING, data, "Helmet").error


def test_an_item_pob_cant_read_is_an_error_naming_why(data):
    assert "no 'Rarity:' line" in read_item("Doom Crusher\nBlacksmith Maul", data).error
    assert "no item base" in read_item("Rarity: Rare\nDoom Crusher\nNot A Real Maul", data).error
    assert "no unique named" in read_item("Rarity: Unique\nNobody's Hammer\nBlacksmith Maul", data).error
    assert read_item("Rarity: Unique\nBrain Rattler\nStudded Greatclub", data).error is None


# The request's rules -----------------------------------------------------------------------------

def _errors(data, **body) -> list[str]:
    return [i.message for i in compile_request(BuildRequest(**body), data).issues if i.level == "error"]


def test_a_pasted_item_the_request_rules_out_is_an_error_with_the_reason(data):
    assert any("asks for a Bow" in e for e in _errors(data, weapon_type="Bow", owned_items=[{"text": MAUL}]))
    assert any("no off hand" in e for e in _errors(data, offhand="none", owned_items=[{"text": SHIELD}]))
    assert any("holds no off hand" in e for e in _errors(data, owned_items=[{"text": MAUL}, {"text": SHIELD}]))
    assert any("already takes Ring 1" in e for e in _errors(
        data, owned_items=[{"text": RING, "slot": "Ring 1"}, {"text": RING, "slot": "Ring 1"}]))
    compiled = compile_request(BuildRequest(owned_items=[{"text": MAUL}]), data)
    assert not compiled.errors and [r.name for r in compiled.owned] == ["Doom Crusher"]


# The search ----------------------------------------------------------------------------------------

def _search(engine, **body):
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    request = BuildRequest(main_skill="Boneshatter", **body)
    ctx = Context(make_payload(request))
    skeleton = enumerate_skeletons(ctx, ["Boneshatter"])[0][0]
    search = Search(engine, None, make_payload(request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search, skeleton


def test_a_pasted_rare_weapon_is_kept_in_its_slot_at_no_cost(engine):
    from builder.search.gear import OWNED

    search, skeleton = _search(engine, owned_items=[{"text": MAUL}])
    build, gear = search.build, search.gear
    assert skeleton.weapon == "Two Hand Mace"  # (the owned weapon fixes the main hand)
    held = build.equipped["Weapon 1"]
    assert held.name == "Doom Crusher" and held.price == 0.0 and held.confidence == OWNED
    assert "Weapon 1" in build.locked_slots
    items = {i["slot"]: i["name"] for i in build.call("facts", fields=[])["items"]}
    assert items["Weapon 1"] == "Doom Crusher"  # (PoB read the pasted text)
    spent = build.spent()
    gear.gear_round(__import__("builder").clock.now() + 30, full=True)
    assert build.equipped["Weapon 1"].name == "Doom Crusher"  # (no gear round replaces it)
    assert build.spent() >= spent - 1e-9 and all(o.price == 0.0 for s, o in build.equipped.items() if s == "Weapon 1")


def test_an_owned_required_unique_costs_nothing_in_the_budget(engine):
    from builder.search.gear import OWNED

    search, _ = _search(engine, weapon_unique="Brain Rattler", owned_uniques=["Brain Rattler"])
    held = search.build.equipped["Weapon 1"]
    assert held.name == "Brain Rattler" and held.price == 0.0 and held.confidence == OWNED
    paid, _ = _search(engine, weapon_unique="Brain Rattler")
    assert paid.build.equipped["Weapon 1"].confidence != OWNED


def test_the_budget_row_and_the_shopping_list_leave_owned_items_out(data):
    from builder.report.shopping import shopping_list

    gear = {"Weapon 1": {"name": "Doom Crusher", "base": "Blacksmith Maul", "type": "Two Hand Mace", "unique": False,
                         "rare": True, "price": 0.0, "confidence": "owned", "owned": True, "market_price": None,
                         "affixes": [], "runes": []},
            "Helmet": {"name": "Goldrim", "base": "Felt Cap", "type": "Helmet", "unique": True, "rare": False,
                       "price": 2.0, "confidence": "high", "affixes": [], "runes": []}}
    shopping = shopping_list({"gear": gear, "request": {}}, data, {})
    owned = next(i for i in shopping["items"] if i["slot"] == "Weapon 1")
    assert owned["kind"] == "owned" and owned["price"] == 0.0 and "not counted in the budget" in owned["note"]
    assert "(not priced)" in owned["note"] and shopping["total"] == pytest.approx(2.0)


# The page ------------------------------------------------------------------------------------------

def test_the_page_reads_a_pasted_item_and_has_every_element_its_script_asks_for():
    import pathlib
    import re

    from builder.web.app import OwnedBody, read_owned

    assert read_owned(OwnedBody(text=MAUL)) == {"rarity": "RARE", "name": "Doom Crusher", "base": "Blacksmith Maul",
                                                "type": "Two Hand Mace", "slots": ["Weapon 1"], "slot": "Weapon 1",
                                                "error": None}
    assert "choose its slot" in read_owned(OwnedBody(text=RING, slot="")).get("error")
    static = pathlib.Path(__file__).resolve().parents[1] / "builder" / "web" / "static"  # (other tests move the cwd)
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    asked = set(re.findall(r'\$\("([\w-]+)"\)', script))
    assert {"owned-text", "owned-slot", "owned-read", "owned-add", "owned-preview", "owned-list",
            "weapon_unique_owned"} <= asked
    missing = sorted(i for i in asked if f'id="{i}"' not in html)
    assert not missing, missing
