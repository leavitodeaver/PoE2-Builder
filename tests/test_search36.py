"""SEARCH36: a Purity / Impurity group's supports (the manager's 03:05 22 Nov, RESEARCH46 R46-1): its skill's Cost &
Reservation multiplier is 0, so the Spirit its supports add is reserved as 0; gems.free_groups fills such a group even
with the side-group supports off, on every request (the user, 22 Nov: "that's not an exploit, that's just how the game is
built"), and free_notes gives the result a plain note."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices, Floors
from builder.search.context import Context, make_payload

SCEPTRE = "\n".join(["Rarity: Normal", "Shrine Sceptre", "Item Level: 82", "Implicits: 1",
                     "Grants Skill: Level 20 Purity of Fire"])


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def _search(engine, exploits: bool, sceptre: bool = True, spare: int | None = None):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = Context(make_payload(BuildRequest(main_skill="Spark", level=90, require=Choices(), allow_exploits=exploits,
                                                  floors=Floors(spare_spirit=spare))))
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    if sceptre:
        search.build.call("equip", slot="Weapon 1", raw=SCEPTRE, fields=[])
        search.build.refresh()
    return search


def test_a_purity_groups_supports_cost_no_spirit_and_it_is_filled_on_every_request(engine):
    search = _search(engine, True)
    build, gems = search.build, search.gems
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Purity of Fire")
    assert group["slot"] == "Weapon 1" and group["fromItem"]
    assert [x["reservationMultiplier"] for x in group["skills"]] == [0]
    assert [g["index"] for g in gems.free_groups()] == [group["index"]]
    assert (True, group["index"], "free") in gems.fill_targets()  # (the side-group supports are off by default)
    spirit = build.call("stats", fields=["SpiritUnreserved"])["SpiritUnreserved"]
    kept = [{"name": x["name"], "level": x["level"], "quality": x["quality"]} for x in group["gems"]]
    build.set_group(index=group["index"], gems=kept + [{"name": "Vitality II"}, {"name": "Precision II"}])
    after = build.call("stats", fields=["SpiritUnreserved"])["SpiritUnreserved"]
    group = next(g for g in build.groups() if g["index"] == group["index"])
    cost = group["skills"][0]["extraSpirit"]
    print("SpiritUnreserved", spirit, "->", after, "supports' Spirit before the multiplier", cost)
    assert after == spirit and cost > 0  # (PoB charges them nothing: the game's 0% multiplier)
    notes = gems.free_notes()
    assert notes == ["Purity of Fire: Vitality II, Precision II cost no Spirit (Purity / Impurity's 0% cost multiplier "
                     "makes supports in it free; recheck at 1.0)"], notes

    # control: a Stoic Sceptre's Discipline (multiplier 1) is charged the same two supports' Spirit
    build.call("equip", slot="Weapon 1", raw="\n".join(["Rarity: Normal", "Stoic Sceptre", "Item Level: 82",
                                                      "Implicits: 1", "Grants Skill: Level 20 Discipline"]), fields=[])
    build.refresh()
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Discipline")
    assert [x["reservationMultiplier"] for x in group["skills"]] == [1] and gems.free_groups() == []
    before = build.call("stats", fields=["SpiritUnreserved"])["SpiritUnreserved"]
    kept = [{"name": x["name"], "level": x["level"], "quality": x["quality"]} for x in group["gems"]]
    build.set_group(index=group["index"], gems=kept + [{"name": "Vitality II"}, {"name": "Precision II"}])
    charged = build.call("stats", fields=["SpiritUnreserved"])["SpiritUnreserved"]
    print("control Discipline: SpiritUnreserved", before, "->", charged)
    assert charged == before - cost

    plain = _search(engine, False)  # (allow_exploits off: no gate, it's the game's own rule)
    assert len(plain.gems.free_groups()) == 1
    assert any(kind == "free" for _, _, kind in plain.gems.fill_targets())

