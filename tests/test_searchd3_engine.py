"""SEARCHD3 on PoB: a required unique sits in the skeleton the chooser gives (THE USER's Liminal Coil in the Wand main hand),
a required unique Jewel goes into a passive-tree socket, and a required item a round takes out goes back (it is LOCKED)."""

from __future__ import annotations

import faulthandler

import pytest

from builder import clock
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import enumerate_skeletons

THEIRS = dict(level=95, class_name="Ranger", ascendancies=["Pathfinder"], main_skill="Detonate Living",
              require={"uniques": ["Shankgonne", "Crown of Eyes", "Liminal Coil"]}, weapon_swap="may")


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


def search_for(engine, **fields):
    from builder.search.inner import Search

    payload = make_payload(BuildRequest(**{**THEIRS, **fields}))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, [ctx.request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def worn(search) -> dict[str, str]:
    return {o.name: slot for slot, o in search.build.equipped.items() if o is not None}


def test_the_users_three_uniques_are_worn_and_locked(engine):
    search = search_for(engine)
    print(search.skeleton.label, worn(search), search.problems)
    held = worn(search)
    assert held.get("Liminal Coil") == "Weapon 1" and held.get("Shankgonne") == "Boots"
    assert held.get("Crown of Eyes") == "Helmet"
    assert {"Weapon 1", "Boots", "Helmet"} <= search.build.locked_slots
    assert not [p for p in search.problems if "Liminal Coil" in p]
    facts = search.build.call("facts", fields=[])
    names = {i["name"] for i in facts["items"] if i["rarity"] == "UNIQUE"}
    assert {"Liminal Coil", "Shankgonne", "Crown of Eyes"} <= names  # (PoB holds them, not only our record)


def test_a_required_jewel_goes_into_a_socket_and_stays(engine):
    search = search_for(engine, require={"uniques": ["Controlled Metamorphosis"]})
    held = worn(search)
    print(held, search.problems)
    slot = held.get("Controlled Metamorphosis")
    assert slot is not None and slot.startswith("Jewel ") and slot in search.build.locked_slots
    socket = int(slot.split()[1])
    assert socket in search.tree.required
    # a jewel round has no lock of its own (jewels.py): what it takes out, the search puts back
    search.build.equip(slot, None)
    assert search.ensure_required_items(after_search=True)
    assert worn(search).get("Controlled Metamorphosis", "").startswith("Jewel ")
    assert any("put back" in p for p in search.problems)


def test_a_short_search_keeps_every_required_unique(engine):
    search = search_for(engine)
    search.run(clock.now() + 20.0)
    held = worn(search)
    print(held, search.problems)
    assert {"Liminal Coil", "Shankgonne", "Crown of Eyes"} <= set(held)
