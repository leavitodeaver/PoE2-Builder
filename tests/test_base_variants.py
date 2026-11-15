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


# --- COV33: what a copy costs (the manager, 15 Nov: "no price, no offer") ----------------------------------------------

def _table(uniques: dict, exchange: dict):
    from builder.data.prices import Price, PriceTable

    return PriceTable("test", {n: Price(n, v, None, "Verisium") for n, v in exchange.items()},
                      {name: [Price(name, v, n, "UniqueArmours", base, None, corrupted) for base, v, n, corrupted in lines]
                       for name, lines in uniques.items()}, 0.0)


MATS = {"Verisium": 0.0001, "Exceptional Verisium": 0.001, "Olroth's Crest of the Sun": 0.2,
        "Uhtred's Crest of the Chalice": 0.04, "Medved's Crest of the Circle": 0.002}


def test_a_copy_is_priced_at_the_plain_unique_plus_its_anvil_recipe():
    from builder.search.base_variants import copy_price

    t = _table({"Svalinn": [("Crucible Tower Shield", 2.0, 100, False)]}, MATS)
    # poe2db: "Svalinn Crucible Tower Shield, Exceptional Verisium x25, Olroth's Crest of the Sun x1"
    p = copy_price(t, "Svalinn", "Runemastered Crucible Tower Shield")
    assert p["divine"] == pytest.approx(2.0 + 25 * 0.001 + 0.2) and p["market"] is None
    assert "its own anvil recipe" in p["how"] and p["confidence"] == "high"
    # no unique recipe for the Runeforged one: the base's plain Runeforging (Crucible Tower Shield, Verisium x155)
    p = copy_price(t, "Svalinn", "Runeforged Crucible Tower Shield")
    assert p["divine"] == pytest.approx(2.0 + 155 * 0.0001) and "MEDIUM" in p["how"]


def test_a_listed_copy_costs_the_cheaper_of_its_own_listing_and_the_craft():
    from builder.search.base_variants import copy_price

    t = _table({"Svalinn": [("Crucible Tower Shield", 2.0, 100, False),
                            ("Runemastered Crucible Tower Shield", 0.3, 50, False),
                            ("Runemastered Crucible Tower Shield", 0.01, 3, True)]}, MATS)  # corrupted: not used
    p = copy_price(t, "Svalinn", "Runemastered Crucible Tower Shield")
    assert p["divine"] == 0.3 and p["market"] == 0.3 and p["craft"] == pytest.approx(2.225)


def test_no_price_means_no_offer():
    from builder.search.base_variants import copy_price

    # the plain unique isn't listed and nor is the copy: nothing to price it from
    assert copy_price(_table({}, MATS), "Svalinn", "Runemastered Crucible Tower Shield") is None
    # a material without a poe.ninja price: no craft route
    no_crest = {k: v for k, v in MATS.items() if k != "Olroth's Crest of the Sun"}
    t = _table({"Svalinn": [("Crucible Tower Shield", 2.0, 100, False)]}, no_crest)
    assert copy_price(t, "Svalinn", "Runemastered Crucible Tower Shield") is None
    # Headhunter has no anvil recipe for a Runemastered Heavy Belt (poe2db, 15 Nov) and no listing here
    t = _table({"Headhunter": [("Heavy Belt", 50.0, 100, False)]}, MATS)
    assert copy_price(t, "Headhunter", "Runemastered Heavy Belt") is None


def test_a_chained_recipe_adds_both_steps_and_accented_names_match():
    from builder.search.base_variants import copy_price, recipes

    by_unique, _ = recipes()
    # poe2db: Brynhand's Mark Wooden Club -> Runeforged (Verisium x205 + Medved's) -> Runemastered (x460 + Medved's)
    assert by_unique[("Brynhand's Mark", "Runemastered Wooden Club")][0]["from"] == "Runeforged Wooden Club"
    t = _table({"Brynhand's Mark": [("Wooden Club", 0.003, 50, False)]}, MATS)
    p = copy_price(t, "Brynhand's Mark", "Runemastered Wooden Club")
    assert p["divine"] == pytest.approx(0.003 + 665 * 0.0001 + 2 * 0.002)
    assert ("Mjolner", "Runemastered Torment Club") in by_unique  # poe2db's "Mjölner"


# --- COV33, H4-4: one copy per base row where the game's rows differ ---------------------------------------------------

def test_the_three_uniques_get_one_copy_per_row():
    from builder.search.base_variants import offered_copies

    uniques, bases = _data()
    by = {u["name"]: u for u in uniques}
    rows = {n: [(c["base"], c.get("row"), c["anvil"]) for c in offered_copies(by[n], bases)]
            for n in ("Svalinn", "Eyes of the Runefather", "The Unleashed", "Geofri's Sanctuary")}
    assert [r[1] for r in rows["Svalinn"]] == [None, "Runic Ward 250", "Armour 100, Runic Ward 100",
                                               "Evasion 148, Runic Ward 35", "Armour 162, Runic Ward 35"]
    # Eyes: the Buckler row isn't offered; PoB's kept row has no recipe (anvil False)
    assert rows["Eyes of the Runefather"] == [("Runemastered Venerable Defender", "Armour 203", True),
                                              ("Runemastered Venerable Defender", "Energy Shield 135", True),
                                              ("Runemastered Venerable Defender", "Armour 112, Evasion 102", False)]
    assert len(rows["The Unleashed"]) == 3 and rows["Geofri's Sanctuary"] == [("Runemastered Revered Vestments", None, True)]
    ward = next(c for c in offered_copies(by["Svalinn"], bases) if c.get("row") == "Armour 100, Runic Ward 100")["raw"]
    assert "Mana before Life" not in ward and "Implicits: 3" in ward
    assert "{range:0.5}Recover (15-25) Runic Ward when you Block" in ward
    assert "LedgerMod:Armour BASE -62" in ward and "LedgerMod:Ward BASE 65" in ward


def test_pob_scores_each_row(engine):
    from builder.search.base_variants import offered_copies

    uniques, bases = _data()
    by = {u["name"]: u for u in uniques}
    engine.call("newBuild")
    engine.call("setLevel", level=90)

    def stats(raw, slot, fields):
        engine.call("equip", slot=slot, fields=[], raw=raw)
        return engine.call("stats", fields=fields)

    sval = {c.get("row"): stats(c["raw"], "Weapon 2", ["Armour", "Evasion", "Ward"])
            for c in offered_copies(by["Svalinn"], bases)}
    print(sval)
    kept = sval["Armour 162, Runic Ward 35"]
    assert sval["Runic Ward 250"]["Armour"] < kept["Armour"] and sval["Runic Ward 250"]["Ward"] > kept["Ward"]
    assert sval["Evasion 148, Runic Ward 35"]["Evasion"] > kept["Evasion"]
    assert sval["Evasion 148, Runic Ward 35"]["Ward"] == kept["Ward"]
    # Armour 100 vs 162 under the same (200-300)% increase: about 100/162 of the kept row's armour
    assert sval["Armour 100, Runic Ward 100"]["Armour"] == pytest.approx(kept["Armour"] * 100 / 162, rel=0.03)
    unl = {c.get("row"): stats(c["raw"], "Body Armour", ["Armour", "EnergyShield", "Ward"])
           for c in offered_copies(by["The Unleashed"], bases)}
    print(unl)
    assert unl["Arcane Surge"]["Armour"] > unl["Runic Ward 301"]["Armour"]
    assert unl["Energy Shield Recharge"]["EnergyShield"] > unl["Arcane Surge"]["EnergyShield"] > unl["Runic Ward 301"]["EnergyShield"]
    assert unl["Runic Ward 301"]["Ward"] > unl["Arcane Surge"]["Ward"]


def test_a_row_without_a_recipe_is_priced_from_its_listing_only():
    from builder.search.base_variants import copy_price

    t = _table({"Eyes of the Runefather": [("Venerable Defender", 0.001, 100, False)]}, MATS)
    base = "Runemastered Venerable Defender"
    assert copy_price(t, "Eyes of the Runefather", base, row="Armour 203")["craft"] is not None
    assert copy_price(t, "Eyes of the Runefather", base, anvil=False, row="Armour 112, Evasion 102") is None
    t = _table({"Eyes of the Runefather": [("Venerable Defender", 0.001, 100, False), (base, 0.004, 300, False)]}, MATS)
    p = copy_price(t, "Eyes of the Runefather", base, anvil=False, row="Armour 112, Evasion 102")
    assert p["divine"] == 0.004 and p["craft"] is None and "check it's the Armour 112, Evasion 102 one" in p["how"]
