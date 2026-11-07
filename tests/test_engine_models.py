"""Custom models through a real engine (PoB alone vs PoB with the model). Boots one PoB engine for
the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

FIELDS = ["CombinedDPS", "PhysicalMinBase", "PhysicalDamageReduction", "Armour"]


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


def iron_ward(engine, forbid: list[str]) -> Search:
    payload = make_payload(BuildRequest(main_skill="Iron Ward", ascendancies=["Titan"], forbid_ledger_entries=forbid))
    skeleton = enumerate_skeletons(Context(payload), ["Iron Ward"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_iron_ward_nova_deals_its_store_and_armour_speeds_it_up(engine):
    off = iron_ward(engine, ["iron-ward-store"]).build
    assert off.call("stats", fields=FIELDS)["CombinedDPS"] == 0  # PoB alone: the nova has no base damage
    search = iron_ward(engine, [])
    build = search.build
    stats = build.call("stats", fields=FIELDS)
    assert stats["PhysicalMinBase"] == 22886 and stats["CombinedDPS"] > 0  # the gem's level 20 store
    assert build.main_group()["mainActiveSkill"] == 2  # the nova, not the buff
    ctx = build.ctx
    base = next(b for b in ctx.data.bases if b["type"] == "Body Armour" and not b.get("hidden")
                and (b.get("armour") or {}).get("Armour") and not (b.get("armour") or {}).get("Evasion")
                and not (b.get("armour") or {}).get("EnergyShield") and not b["name"].startswith("Runeforged"))
    plate = f"Rarity: Rare\nTest Plate\n{base['name']}\nItem Level: 82\nImplicits: 0\n+1500 to Armour"
    (_, what_if), = build.item_deltas([{"slot": "Body Armour", "raw": plate}])
    assert what_if["CombinedDPS"] > 2 * stats["CombinedDPS"]  # the what-if sees the faster store
    build.call("equip", slot="Body Armour", raw=plate, fields=[])
    search.resolve()
    after = build.call("stats", fields=FIELDS)
    assert after["PhysicalDamageReduction"] > stats["PhysicalDamageReduction"]
    assert after["CombinedDPS"] > 2 * stats["CombinedDPS"]


def spark_with(engine, forbid: list[str]) -> Search:
    from builder.request.schema import Choices

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"],
                                        require=Choices(skills=["Mana Tempest"]), forbid_ledger_entries=forbid))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search


def test_mana_tempest_gem_stat_reaches_pob(engine):
    """PoB never reads Mana Tempest's gain-as-Lightning stat; the gem_stat entry gives it to spells, at the uptime the build's
    own Mana allows (the bare skeleton can't even pay for its spell: 0%, and the guide says why)."""
    search = spark_with(engine, [])
    build = search.build
    bare = build.call("stats", fields=FIELDS)["CombinedDPS"]
    note = next(n for n in search.assumptions.active if n.get("entry") == "gem-mana-tempest-lightning")
    assert note["value"].startswith("not counted") and "regenerated" in note["basis"]
    amulet = "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0", "+1000 to maximum Mana",
                        "Regenerate 400 Mana per second"])
    build.call("equip", slot="Amulet", raw=amulet, fields=[])
    search.resolve()
    rich = build.call("stats", fields=FIELDS)["CombinedDPS"]
    note = next(n for n in search.assumptions.active if n.get("entry") == "gem-mana-tempest-lightning")
    assert "uptime 100%" in note["basis"] and rich > 1.5 * bare


def test_a_builds_coverage_blind_spots(engine):
    """What finalize_task adds to a result's not-modeled list, on a real build: the calls it makes, and gem stats PoB never reads."""
    from builder.ledger.coverage import build_blind_spots
    from builder.ledger.parse_audit import GATED_FIELDS

    search = spark_with(engine, [])
    build = search.build
    groups = build.groups()
    found = build_blind_spots(build.call("sourceLines")["lines"], [g["name"] for group in groups for g in group["gems"] if g.get("name")],
                              config_on=set(search.conditions.active), stats=build.call("stats", fields=GATED_FIELDS))
    assert isinstance(found, list) and all({"source", "line", "kind", "reason"} <= set(b) for b in found)
    assert not any(b["source"] == "gem: Mana Tempest" for b in found)  # its unread stat is counted by a gem_stat entry
