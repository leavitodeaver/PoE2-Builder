"""COV32 (the manager, 15 Nov 08:42): a unique's Runeforged / Runemastered copies as their own texts
(builder/search/base_variants.py): "Selected Base Variant: N" and the base PoB uses for it."""

from __future__ import annotations

import faulthandler
import json

import pytest

from builder import ROOT
from builder.search.base_variants import base_variant_copies, base_variants

DATA = ROOT / "data" / "pob-bb52d6b368"


def _data():
    if not (DATA / "uniques.json").exists():
        pytest.skip("no PoB data dump")
    uniques = json.loads((DATA / "uniques.json").read_text(encoding="utf-8"))["uniques"]
    bases = {b["name"] for b in json.loads((DATA / "bases.json").read_text(encoding="utf-8"))["bases"]}
    return uniques, bases


def test_every_non_regular_base_version_becomes_a_copy():
    uniques, bases = _data()
    with_versions = [u for u in uniques if base_variants(u["raw"])]
    copies = {u["name"]: base_variant_copies(u, bases) for u in with_versions}
    assert len(with_versions) >= 250 and all(len(c) == len(base_variants(u["raw"])) - 1
                                             for u, c in zip(with_versions, copies.values()))
    svalinn = copies["Svalinn"]
    assert [(c["version"], c["base"]) for c in svalinn] == [("Runeforged", "Runeforged Crucible Tower Shield"),
                                                            ("Runemastered", "Runemastered Crucible Tower Shield")]
    assert "Selected Base Variant: 3" in svalinn[1]["raw"] and "Selected Base Variant: 1" not in svalinn[1]["raw"]


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_pob_scores_a_copy_on_its_upgraded_base(engine):
    uniques, bases = _data()
    svalinn = next(u for u in uniques if u["name"] == "Svalinn")
    engine.call("newBuild")
    engine.call("setLevel", level=90)

    def defences(raw):
        engine.call("equip", slot="Weapon 2", fields=[], raw=raw)
        return engine.call("stats", fields=["Armour", "Ward"])

    regular = defences(svalinn["raw"])
    copies = [defences(c["raw"]) for c in base_variant_copies(svalinn, bases)]
    print(regular, copies)
    assert all(c["Ward"] > regular["Ward"] for c in copies)  # (an upgraded armour gains Runic Ward, 0.5.0's note)
