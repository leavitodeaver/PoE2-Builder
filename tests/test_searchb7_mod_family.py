"""SEARCHB7 (RESEARCH R54-9): every ordinary skill-level suffix is one game family, so one rare holds at most one."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, GearTier
from builder.search import rares
from builder.search.rares import SKILL_LEVEL_FAMILY, SKILL_LEVEL_GROUPS, AffixPools, exclusion_group

TABLES = Path(__file__).resolve().parent.parent / "data" / "game-26650308" / "tables"


def rows(name: str) -> list[dict]:
    data = json.loads((TABLES / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["rows"]


@pytest.mark.skipif(not TABLES.exists(), reason="game tables not extracted")
def test_the_groups_are_exactly_the_familys_in_the_pools_the_search_rolls():
    families = rows("ModFamily.json")
    family_of = {m["Id"]: {families[f]["Id"] for f in m.get("Families") or []} for m in rows("Mods.json")}
    data = GameData()
    pool = [m for source in ("Item", "Desecrated") for m in data.mods[source] if m.get("type") in ("Prefix", "Suffix")]
    assert {m["group"] for m in pool if SKILL_LEVEL_FAMILY in family_of.get(m["id"], ())} == set(SKILL_LEVEL_GROUPS)
    assert all(SKILL_LEVEL_FAMILY in family_of[m["id"]] for m in pool if m["group"] in SKILL_LEVEL_GROUPS)


def test_only_skill_levels_change_their_key():
    assert exclusion_group("GlobalIncreaseSpellSkillGemLevelWeapon") == exclusion_group(
        "GlobalIncreaseFireSpellSkillGemLevelWeapon") == SKILL_LEVEL_FAMILY
    for kept in ("ManaSpellLevelHybrid", "GlobalSkillGemQualityMeleeLevel", "IncreasedLife", "FireResistance"):
        assert exclusion_group(kept) == kept


def test_a_wands_pool_keeps_both_lines_but_one_key():
    data = GameData()
    ctx = SimpleNamespace(data=data, bases_by_name={b["name"]: b for b in data.bases},
                          request=BuildRequest(name="t", budget={"gear_tier": GearTier.ENDGAME}))
    pool = AffixPools(ctx).pool("Withered Wand")
    level = [a for a in pool if a.type == "Suffix" and any("Level of all" in line and "Spell Skills" in line for line in a.lines)]
    texts = " ".join(line for a in level for line in a.lines)
    assert "Chaos Spell" in texts and "Level of all Spell Skills" in texts  # both stay offered, as alternatives
    assert {a.group for a in level} == {rares.SKILL_LEVEL_FAMILY}
    assert all(a.lower is None or a.lower.group == a.group for a in level)
