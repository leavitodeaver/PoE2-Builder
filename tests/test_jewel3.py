"""JEWEL3: the result's notes for jewel choices (builder/search/jewels.py), checked on fakes (no PoB)."""

from __future__ import annotations

from types import SimpleNamespace

from builder.search import jewels as module
from builder.search.context import ItemOption


class FakeObjective:
    """dps = the stats' "dps"; one request floor, the user's default 25% movement speed."""

    penalties = [SimpleNamespace(label="Movement speed", stat="MovementSpeedIncrease", floor=25.0)]

    def dps(self, stats: dict) -> float:
        return stats["dps"]

    def deficits(self, stats: dict) -> list:
        short = max(0.0, 25.0 - stats["MovementSpeedIncrease"]) / 25.0
        return [("Movement speed", short, False)] if short > 0 else []


def option(name: str, slot: str, runes: tuple = (), affixes: tuple = ()) -> ItemOption:
    return ItemOption(name, name, slot, None, "", False, 0.0, "t", 0, runes=runes, affixes=affixes)


def jewels_with(equipped: dict) -> module.Jewels:
    jewels = module.Jewels.__new__(module.Jewels)
    jewels.build = SimpleNamespace(objective=FakeObjective(), equipped=equipped)
    jewels.cadigan_kept = None
    return jewels


def test_cadigans_note_says_the_dps_loss_was_kept_for_the_movement_floor():
    """2b75e (POE2T7956): Cadigan's Epiphany kept at x0.950 DPS because its jewel meets the 25% movement floor; the
    note says so in plain words, with the floor's values."""
    gloves = option("Doom Grip", "Gloves", runes=(module.CADIGAN,))
    jewel = option("Jewel", "Jewel", affixes=("4% increased Movement Speed", "15% increased Cold Damage"))
    jewels = jewels_with({"Gloves": gloves, module.CADIGAN_SLOT: jewel})
    jewels.cadigan_kept = jewels.cadigan_record({"dps": 100.0, "MovementSpeedIncrease": 21.0},
                                                {"dps": 95.0, "MovementSpeedIncrease": 25.0})
    [note] = jewels.cadigan_notes()
    assert note["value"] == "x0.950"
    assert "kept to meet a floor" in note["label"]
    assert "Movement speed: met (21 -> 25, floor 25)" in note["basis"]
    assert "floor doing its job" in note["basis"] and "4% increased Movement Speed" in note["basis"]


def test_cadigans_note_for_a_dps_gain_and_none_without_it():
    gloves = option("Doom Grip", "Gloves", runes=(module.CADIGAN,))
    jewels = jewels_with({"Gloves": gloves})
    jewels.cadigan_kept = jewels.cadigan_record({"dps": 100.0, "MovementSpeedIncrease": 30.0},
                                                {"dps": 108.9, "MovementSpeedIncrease": 30.0})
    [note] = jewels.cadigan_notes()
    assert note["value"] == "x1.089" and "floor" not in note["label"]
    assert "adds more than the runes did" in note["basis"]
    # gloves without the augment (the search swapped them later): no note
    jewels.build.equipped["Gloves"] = option("Doom Grip", "Gloves", runes=("Iron Rune",))
    assert jewels.cadigan_notes() == []
