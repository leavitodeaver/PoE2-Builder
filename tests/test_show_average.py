"""ShowAverage skills (Sunder, Earthquake): PoB's CombinedDPS for them is the damage per use, so the search's main DPS
puts it per second (objective.per_second, the bridge's ShowAverage flag; SEARCH7, COVD6). Boots one PoB engine for the
module and needs the game-data dump."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import objective
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

FIELDS = ["CombinedDPS", "Minion.CombinedDPS", *objective.AVERAGE_FIELDS, "FullDPS", "Speed"]


def test_per_second_puts_a_per_use_combined_dps_per_second():
    stats = {"ShowAverage": 1, "CombinedDPS": 1500.0, "AverageDamage": 1000.0, "TotalDPS": 2000.0, "TotalDotDPS": 300.0,
             "CullMultiplier": 1.1, "ReservationDpsMultiplier": 1.0}
    # (1000 + 500 impale) x 1.1 per use -> + 1.1 x (2000 - 1000 + 300)
    assert objective.per_second(stats) == pytest.approx(1500 + 1.1 * 1300)
    assert objective.main_dps(stats) == pytest.approx(1500 + 1.1 * 1300)
    assert objective.main_dps({**stats, "ShowAverage": 0}) == 1500.0  # already per second: as PoB gives it
    assert objective.main_dps({**stats, "CullMultiplier": 0}) == pytest.approx(1500 + 1300)  # no cull reported: x1
    assert objective.main_dps({"ShowAverage": 1, "CombinedDPS": 0, "TotalDPS": 50}) == 0.0  # a disabled skill
    minion = {f"Minion.{k}": v for k, v in stats.items()}
    assert objective.main_dps({"CombinedDPS": 100.0, **minion}) == pytest.approx(1500 + 1.1 * 1300)
    assert set(objective.AVERAGE_FIELDS) <= set(objective.BASE_FIELDS)


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


def _build(engine, skill: str):
    payload = make_payload(BuildRequest(main_skill=skill))
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    return search.build


def _amulet(extra: list[str]) -> str:
    return "\n".join(["Rarity: Rare", "Test Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
                      "Adds 100 to 200 Physical Damage to Attacks", *extra])


@pytest.mark.parametrize("skill", ["Sunder", "Earthquake"])
def test_a_slams_main_dps_sees_attack_speed(engine, skill):
    """PoB shows the slam per use (CombinedDPS = AverageDamage): 30% more attack speed moves CombinedDPS by nothing,
    but main_dps by the speed, as Full DPS does."""
    build = _build(engine, skill)
    build.call("equip", slot="Amulet", raw=_amulet([]), fields=[])
    before = build.call("stats", fields=FIELDS)
    build.call("equip", slot="Amulet", raw=_amulet(["30% increased Attack Speed"]), fields=[])
    after = build.call("stats", fields=FIELDS)
    assert before["ShowAverage"] == 1 and before["AverageDamage"] > 0
    assert after["CombinedDPS"] == pytest.approx(before["CombinedDPS"], rel=1e-6)  # what screening read before
    speed = after["Speed"] / before["Speed"]
    assert speed > 1.1
    assert objective.main_dps(after) / objective.main_dps(before) == pytest.approx(speed, rel=0.02)
    # per use x uses a second: under one use a second (Sunder, probe POE2T758: 815.5 per use, 456.7 a second) the
    # per-use number was an OVERCOUNT, over it an undercount
    assert objective.main_dps(before) == pytest.approx(before["AverageDamage"] * before["Speed"], rel=0.05)
    assert objective.main_dps(before) == pytest.approx(before["FullDPS"], rel=0.05)  # per second, like Full DPS


def test_a_skill_shown_per_second_is_left_as_pob_gives_it(engine):
    build = _build(engine, "Spark")
    stats = build.call("stats", fields=FIELDS)
    assert stats["ShowAverage"] == 0 and stats["CombinedDPS"] > 0
    assert objective.main_dps(stats) == stats["CombinedDPS"]
