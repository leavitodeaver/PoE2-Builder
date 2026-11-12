"""Unique items' ledger lines (COV-D, ledger/entries/uniques-*.yaml) through a real engine. Boots one PoB engine for
the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

FIELDS = ["TotalDPS", "IgniteDPS", "BleedDPS", "PoisonDPS", "FullDPS"]


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


def test_hrimnors_hymn_aftershock_doubles_slam_hits_not_ignite(engine):
    """Hrimnor's Hymn's extra Aftershock (uniques-hrimnors-hymn-aftershock): a second equal hit doubles the slam's hit
    DPS, but a second Ignite or Bleed of the same size doesn't stack (KeywordPopups Debuff), so they stay well under
    x2 (only the higher roll and uptime of more applications; AUDIT8 A8-1)."""
    payload = make_payload(BuildRequest(main_skill="Sunder"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    hymn = next(u for u in ctx.data.uniques if u["name"] == "Hrimnor's Hymn")
    stand_in = "Slam Skills have 100% more DPS"
    assert stand_in in ctx.corrected_raw(hymn["name"], hymn["raw"]).split("\n")

    def amulet(extra: list[str]) -> str:
        return "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                          "Adds 100 to 200 Fire Damage to Attacks", "Adds 100 to 200 Physical Damage to Attacks",
                          "+100% chance to Ignite", "25% chance to cause Bleeding on Hit", "100% chance to Poison on Hit",
                          *extra])

    build.call("equip", slot="Amulet", raw=amulet([]), fields=[])
    before = build.call("stats", fields=FIELDS)
    build.call("equip", slot="Amulet", raw=amulet([stand_in]), fields=[])
    after = build.call("stats", fields=FIELDS)
    assert before["IgniteDPS"] > 0 and before["PoisonDPS"] > 0
    assert after["TotalDPS"] == pytest.approx(2 * before["TotalDPS"], rel=0.01)
    assert after["IgniteDPS"] < 1.5 * before["IgniteDPS"]
    assert after["PoisonDPS"] < 1.5 * before["PoisonDPS"]  # one Poison at a time without an extra-stacks line
    assert after["FullDPS"] > 1.4 * before["FullDPS"]


def test_veilpiercer_hit_damage_in_presence_follows_the_boss_distance(engine):
    """Veilpiercer's "increased Damage with Hits against targets in your Presence" (uniques-veilpiercer-hit-damage-in-
    presence) reaches PoB gated on the boss standing inside the Presence radius (4 m by default)."""
    payload = make_payload(BuildRequest(main_skill="Sunder"))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    veil = next(u for u in ctx.data.uniques if u["name"] == "Veilpiercer")
    stand_in = next(line for line in ctx.corrected_raw(veil["name"], veil["raw"]).split("\n")
                    if "Rare or Unique Enemy is in your Presence" in line)
    assert "% increased Damage with Hits while a Rare or Unique Enemy" in stand_in
    parsed = build.call("parseLines", lines=["40% increased Damage with Hits while a Rare or Unique Enemy is in your Presence"])
    assert "Condition:EnemyInPresence" in parsed["results"][0]["parsed"][0]["tags"]

    def hits_at(metres: float, extra: list[str]) -> float:
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82",
                                                          "Implicits: 0", "Adds 100 to 200 Physical Damage to Attacks",
                                                          *extra]), fields=[])
        search.conditions.distance = (metres, "test")
        search.settle()
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    line = ["40% increased Damage with Hits while a Rare or Unique Enemy is in your Presence"]
    assert hits_at(2.0, line) > 1.1 * hits_at(2.0, [])
    assert hits_at(8.0, line) == pytest.approx(hits_at(8.0, []), rel=0.001)


def test_blackflame_withered_fire_taken_follows_the_withered_stacks(engine):
    """Blackflame's "Withered you inflict also increases Fire Damage taken" (uniques-blackflame-withered-fire-taken,
    AUDIT12 A12-4): +5% per Withered on the boss, from the stack count the condition resolver sets (PoB's
    multiplierWitheredStackCount), nothing at 0 stacks, +50% Fire at 10 (the same as a flat 50% Fire taken)."""
    from builder import ledger

    line = "Withered you inflict also increases Fire Damage taken"
    entry = next(e for e in ledger.entries() if e.id == "uniques-blackflame-withered-fire-taken")
    stand_in = entry.translate(line)
    assert stand_in == ["Enemies take 5% increased Elemental Damage from your Hits for each Withered you have "
                        "inflicted on them"]
    if not any(e.id == "blackflame-withered-fire" for e in ledger.entries()):  # COV-A's old no-effect entry retired
        assert ledger.translate(line)[0].id == entry.id

    payload = make_payload(BuildRequest(main_skill="Sunder"))
    skeleton = enumerate_skeletons(Context(payload), ["Sunder"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def dps(stacks: int, extra: list[str]) -> float:
        build.call("setConfig", vars={"multiplierWitheredStackCount": stacks}, fields=[])
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet",
                                                          "Item Level: 82", "Implicits: 0",
                                                          "Adds 100 to 200 Fire Damage to Attacks", *extra]), fields=[])
        return build.call("stats", fields=["TotalDPS"])["TotalDPS"]

    assert dps(0, stand_in) == pytest.approx(dps(0, []), rel=0.001)
    assert dps(10, stand_in) == pytest.approx(dps(10, ["Nearby Enemies take 50% increased Fire Damage"]), rel=0.001)
    assert dps(10, stand_in) > 1.2 * dps(10, [])
    build.call("setConfig", vars={"multiplierWitheredStackCount": False}, fields=[])


def test_hollow_mask_remnant_skills_reservation_efficiency_by_name(engine):
    """The Hollow Mask's "Reservation Efficiency of Remnant Skills" (uniques-hollow-mask-remnant-reservation-efficiency,
    RESEARCH19 #104): PoB applies none of the original line; the ledger's per-skill lines lower a Remnant skill's
    Spirit reservation (Grim Feast 30 -> 15 at 100%) and leave a skill of another name alone."""
    from builder import ledger

    line = "100% increased Reservation Efficiency of Remnant Skills"
    entry, lines = ledger.translate(line)
    assert entry.id == "uniques-hollow-mask-remnant-reservation-efficiency"
    assert "Grim Feast has 100% increased Reservation Efficiency" in lines

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build

    def reserved(extra: list[str]) -> float:
        build.call("equip", slot="Amulet", raw="\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet",
                                                          "Item Level: 82", "Implicits: 0",
                                                          "Grants Skill: Level 20 Grim Feast", *extra]), fields=[])
        stats = build.call("stats", fields=["Spirit", "SpiritUnreserved"])
        return stats["Spirit"] - stats["SpiritUnreserved"]

    base = reserved([])
    assert base > 0
    assert reserved([line]) == base  # PoB leaves the original line unapplied (the undercount fixed here)
    assert reserved(lines) == pytest.approx(base / 2, abs=1)
    assert reserved(["Mana Remnants has 100% increased Reservation Efficiency"]) == base
