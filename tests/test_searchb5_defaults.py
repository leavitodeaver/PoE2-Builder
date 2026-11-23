"""THE USER's new defaults (23 Nov; the manager's 01:10 and 01:30 notes):
- a movement speed floor, 25% increased by default (endgame requests; the request can lower it), PoB's MovementSpeedMod;
- "Survives the boss's hits" a floor by default (one ordinary hit of each type the request's boss deals), opt-out;
- no empty affix slots on a rare: the slots no scoring affix wanted are filled with utility (Gear.fill_utility)."""
from __future__ import annotations

from types import SimpleNamespace

from builder.data import GameData
from builder.request.constraints import MOVEMENT_STAT, compile_request, survive_floors
from builder.request.schema import BuildRequest
from builder.search.gear import Gear
from builder.search.objective import with_movement
from builder.search.rares import Affix

import pytest


@pytest.fixture(scope="module")
def data():
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")


def labels(request: BuildRequest, data) -> dict[str, float]:
    return {p.label: p.floor for p in compile_request(request, data).penalties}


def test_movement_speed_and_survive_are_floors_by_default(data):
    found = labels(BuildRequest(main_skill="Spark", level=90), data)
    assert found["Movement speed (% increased)"] == 25
    hits = survive_floors(BuildRequest(main_skill="Spark", level=90))
    assert hits and all(found[f"Survives the boss's {t.value.capitalize()} hit"] == hit for t, hit in hits.items())


def test_the_request_can_lower_or_drop_them(data):
    found = labels(BuildRequest(main_skill="Spark", level=90, floors={"movement_speed": 10, "survive_boss_hits": False}),
                   data)
    assert found["Movement speed (% increased)"] == 10 and not any(k.startswith("Survives") for k in found)
    assert "Movement speed (% increased)" not in labels(BuildRequest(main_skill="Spark", level=90,
                                                                     floors={"movement_speed": None}), data)
    assert "Movement speed (% increased)" not in labels(BuildRequest(main_skill="Spark", level=40), data)  # (campaign)


def test_an_explicit_max_hit_floor_above_the_boss_hit_wins(data):
    request = BuildRequest(main_skill="Spark", level=90, floors={"max_hit": {"physical": 50000}})
    found = labels(request, data)
    assert found["Physical max hit"] == 50000 and "Survives the boss's Physical hit" not in found


def test_movement_speed_is_read_net_of_penalties():
    assert with_movement({"MovementSpeedMod": 1.25})[MOVEMENT_STAT] == 25.0
    assert with_movement({"MovementSpeedMod": 0.97})[MOVEMENT_STAT] == -3.0  # (the Oracle build the user reviewed)
    assert MOVEMENT_STAT not in with_movement({})


def affix(id_: str, kind: str, line: str, group: str | None = None) -> Affix:
    return Affix(id_, group or id_, kind, 2, (line,), tiers=3)


def test_empty_affix_slots_are_filled_movement_speed_first_then_defences():
    chosen = [affix("lightning", "Suffix", "+44% to Lightning Resistance"),
              affix("chaos", "Suffix", "+26% to Chaos Resistance")]
    pool = [*chosen,
            affix("ms", "Prefix", "30% increased Movement Speed"),
            affix("life", "Prefix", "+100 to maximum Life"),
            affix("rarity", "Prefix", "20% increased Rarity of Items found"),
            affix("fire", "Suffix", "+40% to Fire Resistance"),
            affix("bad", "Prefix", "+10 to maximum Mana")]
    scores = {"ms": 1.0, "life": 1.2, "rarity": 1.0, "fire": 1.1, "bad": 0.9}  # (the item alone scores 1.0)

    def deltas(slot, raws):
        out = []
        for raw in raws:
            extra = [a for a in pool if a.lines[0] in raw and a not in chosen]
            out.append((1.0 + sum(scores[a.id] - 1.0 for a in extra), {}))
        return out

    pools = SimpleNamespace(pool=lambda name: pool, choices=lambda a, taken: [a])
    gear = SimpleNamespace(deltas=deltas, UTILITY_CLASSES=Gear.UTILITY_CLASSES)
    base = {"name": "Boots", "type": "Boots", "implicit": None}
    added = Gear.fill_utility(gear, "Boots", base, 82, pools, {"Prefix": 3, "Suffix": 3}, chosen, 1.0, 1e9, 1e18)
    names = [a.id for a in added]
    assert names[0] == "ms"  # movement speed first, though Life scores higher alone
    assert set(names) == {"ms", "life", "rarity", "fire"}  # every slot filled, nothing that lowers the score
    assert "bad" not in names
