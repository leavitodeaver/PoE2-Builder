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


def test_an_item_granted_spirit_group_is_switched_off_when_that_scores_higher(engine, monkeypatch):
    """SEARCH36 (the manager's 02:45 22 Nov, R45-6b): PoB reserves an item-granted Herald's Spirit whenever its group is
    enabled, the game only while the player switches it on. With a spare-Spirit floor of 100 (the build has 100, no
    quests), the Herald a body armour grants breaks the floor; item_spirit_off (POE2_SEARCH_ITEM_SPIRIT_OFF=1) switches
    its group off."""
    from builder.search import gems as gems_module

    search = _search(engine, False, sceptre=False, spare=100)
    build, gems = search.build, search.gems
    # (a PoE2 base, the line an implicit as on The Coming Calamity: "Simple Robe" is no PoE2 base, POE2T5591 / 5593)
    armour = "\n".join(["Rarity: Rare", "Probe Coat", "Heroic Armour", "Item Level: 82", "Implicits: 1",
                        "Grants Skill: Level 20 Herald of Ice"])
    build.call("equip", slot="Body Armour", raw=armour, fields=[])
    build.refresh()
    group = next(g for g in build.groups() if g["gems"] and g["gems"][0]["name"] == "Herald of Ice")
    assert group["fromItem"] and group["slot"] == "Body Armour"
    assert group["skills"][0]["spiritReserved"] > 0, group["skills"]
    print("Spirit", build.call("stats", fields=["Spirit", "SpiritUnreserved"]), "score", build.score)
    monkeypatch.setattr(gems_module, "ITEM_SPIRIT_OFF", False)
    assert gems.optimize_persistent(float("inf"), rounds=0) is False  # (off by default)
    assert next(g for g in build.groups() if g["index"] == group["index"])["enabled"]
    assert gems.item_spirit_off(float("inf")) is True
    assert not next(g for g in build.groups() if g["index"] == group["index"])["enabled"]
    assert gems.item_spirit_offs == [group["index"]]


def test_a_curse_whose_rescore_gains_nothing_is_taken_back_and_frees_its_spirit():
    """SEARCH36 (the manager's 08:45 22 Nov, MECH100's probe POE2T6419): the persistent step's Blasphemy trial credited a
    curse with a small what-if gain before its gem-level cap was read; the full rescore gave it exactly nothing, and the
    group stayed and took spare Spirit. Now an added group is kept only if the rescore is higher."""
    from types import SimpleNamespace

    from builder.search.gems import BLASPHEMY_LABEL, Gems

    calls = []

    class FakeBuild:
        score = -65.212
        stats = {"Spirit": 100}
        reverts: dict = {}

        def groups(self):
            return []

        def gem_deltas(self, cases, skip_ehp=False):
            return []

        def set_group(self, **kw):
            calls.append(("add", kw["label"]))
            return {"index": 5}

        def remove_group(self, index):
            calls.append(("remove", index))

        def worse(self, before):
            return self.score < before - 1e-9

    gems = Gems.__new__(Gems)
    gems.build = FakeBuild()
    gems.ctx = SimpleNamespace(request=SimpleNamespace(floors=SimpleNamespace(spare_spirit=None)), persistent_candidates=[])
    gems.resolve, gems.assumptions = None, None
    gems.budget_left = lambda: 10.0
    gems.blasphemy_option = lambda present, left, floor: (-65.016, "Despair")  # (the what-if's small gain)
    gems.blasphemy_gems = lambda name: [{"name": "Blasphemy"}, {"name": name}]
    gems.relevel_added = lambda: False
    assert gems.persistent_round() is False
    assert calls == [("add", BLASPHEMY_LABEL), ("remove", 5)], calls


def test_a_seed_run_honours_the_users_own_items(engine):
    """SEARCH36 (the manager's 08:05 22 Nov, from SEARCHB3's owned-gear feature): a run started from a PoB code (setup_seed)
    takes the user's own items too: a unique the code holds that the user owns costs 0 (gear.OWNED), and a pasted item
    goes in its slot, locked, at no cost, as on a new build (Gear.setup)."""
    from builder.pob import codes
    from builder.search.gear import OWNED
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ring = "\n".join(["Rarity: RARE", "Woe Loop", "Amethyst Ring", "Item Level: 80", "+30% to Cold Resistance"])
    first = BuildRequest(main_skill="Boneshatter", weapon_unique="Brain Rattler")
    ctx = Context(make_payload(first))
    skeleton = enumerate_skeletons(ctx, ["Boneshatter"])[0][0]
    made = Search(engine, None, make_payload(first), skeleton.as_dict(), 0)
    made.setup(quests=False)
    assert made.build.equipped["Weapon 1"].name == "Brain Rattler"
    code = codes.encode(made.build.call("exportXml")["xml"])

    seeded = BuildRequest(main_skill="Boneshatter", weapon_unique="Brain Rattler", owned_uniques=["Brain Rattler"],
                          owned_items=[{"text": ring, "slot": "Ring 2"}], seed_code=code)
    search = Search(engine, None, make_payload(seeded), skeleton.as_dict(), 0)
    search.setup_seed(quests=False)
    build = search.build
    held = build.equipped["Weapon 1"]
    assert held.name == "Brain Rattler" and held.price == 0.0 and held.confidence == OWNED
    pasted = build.equipped["Ring 2"]
    assert pasted.name == "Woe Loop" and pasted.price == 0.0 and pasted.confidence == OWNED
    assert {"Weapon 1", "Ring 2"} <= build.locked_slots
