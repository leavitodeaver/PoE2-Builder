"""Cultivated uniques' data (CULT1): builder/data/cultivated.json against the game files and PoB's unique list."""
import json
from pathlib import Path

import pytest

from builder.search import cultivated

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / "data" / "game-26476662" / "tables" / "Mods.json"
POB_UNIQUES = ROOT / "data" / "pob-bb52d6b368" / "uniques.json"


def test_the_48_uniques_poe2db_lists_are_all_in_pobs_unique_list():
    names = {u["name"] for u in json.loads(POB_UNIQUES.read_text(encoding="utf-8"))["uniques"]}
    found = cultivated.uniques()
    assert len(found) == 48
    assert set(found) <= names
    assert {"Atziri's Rule", "Saitha's Spear", "The Covenant", "Idle Hands", "Doryani's Prototype"} <= set(found)


@pytest.mark.skipif(not MODS.exists(), reason="needs the game-file export")
def test_every_outcome_is_a_game_cultivation_mod_with_the_games_ranges():
    rows = {r["Id"]: r for r in json.loads(MODS.read_text(encoding="utf-8"))}
    for name, unique in cultivated.uniques().items():
        assert unique["outcomes"], name
        for o in unique["outcomes"]:
            row = rows[o["mod"]]
            assert row["Id"].startswith("UniqueMutatedVaal") and row["GenerationType"] == 3, (name, o["mod"])
            assert [s[1:] for s in o["stats"]] == [row[f"Stat{i}Value"] for i in range(1, 9)
                                                    if row.get(f"Stat{i}") is not None and row.get(f"Stat{i}Value")]


def test_the_0_5_0_outcome_changes_are_in_the_data():
    def mods(name):
        return {o["mod"]: o for o in cultivated.uniques()[name]["outcomes"]}
    # 0.5.0: Shackles lost "Elemental Ailments other than Freeze ... Reflected to you", gained Damage per Curse on you
    assert "UniqueMutatedVaalDamagePerCurse" in mods("Shackles of the Wretched")
    assert "UniqueMutatedVaalReflectElementalAilmentsToSelf" not in mods("Shackles of the Wretched")
    # 0.5.0: The Vertex lost +2-4 to all Skills, gained +3-5 to Curse Skills
    assert mods("The Vertex")["UniqueMutatedVaalCurseGemLevel"]["stats"] == [["curse_skill_gem_level_+", 3, 5]]
    assert "UniqueMutatedVaalGlobalSkillGemLevel" not in mods("The Vertex")
    assert mods("Atziri's Splendour")["UniqueMutatedVaalAtziriSplendourEnergyShield1"]["stats"][0][1:] == [66, 100]
    conflict = cultivated.load()["_about"]["conflicts"][0]
    assert conflict["unique"] == "Hateforge" and "16-30" in conflict["patch_notes"]


def test_the_search_is_offered_only_outcomes_pob_scores_and_the_rest_stay_listed():
    spear = cultivated.outcomes("Saitha's Spear", offered=False)
    exposure = next(o for o in spear if o["mod"] == "UniqueMutatedVaalFireExposureOnHit")
    assert not exposure["pob_reads"]
    assert exposure not in cultivated.outcomes("Saitha's Spear")
    assert ("Saitha's Spear", "UniqueMutatedVaalFireExposureOnHit",
            ["(30-50)% chance to inflict Exposure on Hit"]) in cultivated.unread_outcomes()
    keystone = next(o for o in cultivated.outcomes("Flesh Crucible", offered=False)
                    if o["mod"] == "UniqueMutatedVaalRandomKeystoneFromTable")
    assert "not_offered" in keystone and keystone not in cultivated.outcomes("Flesh Crucible")
    assert all(o["pob_reads"] for name in cultivated.uniques() for o in cultivated.outcomes(name))


def test_replaceable_lines_and_unknown_uniques():
    assert len(cultivated.replaceable_lines("Saitha's Spear")) == 5
    assert all(line["replaceable"] for u in cultivated.uniques().values() for line in u["lines"])
    assert not cultivated.cultivatable("Headhunter") and cultivated.outcomes("Headhunter") == []
