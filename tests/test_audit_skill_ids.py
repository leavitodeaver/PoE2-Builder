"""COV32 (COV31's finding): the audit's skill sweep (bridge auditSkills) takes every active skill id once, not one gem per
name, so the castable Lightning Bolt, the one-handed Mace Strike and the off-hand Spear Stab are swept on their own, and
coverage.zero_skills tells shared names apart by id."""

from __future__ import annotations

import faulthandler
import json

import pytest

from builder.ledger.coverage import zero_skills


def test_zero_skills_tells_a_shared_name_apart_by_its_id(tmp_path):
    zero = {"CombinedDPS": 0, "TotalDPS": 0}
    sweep = [{"name": "Spear Stab", "skillId": "SpearStabPlayer", "stats": zero, "types": ["Attack", "Damage"],
              "weapon": None},
             {"name": "Spear Stab", "skillId": "SpearStabOffHandPlayer", "stats": {**zero, "weapon": "Spear"},
              "types": ["Attack", "Damage"]},
             {"name": "Spark", "skillId": "SparkPlayer", "stats": zero, "types": ["Spell", "Damage", "Projectile"]}]
    (tmp_path / "coverage.json").write_text(json.dumps({"skill_sweep": sweep}), encoding="utf-8")
    found = {row["name"]: row for row in zero_skills(tmp_path)}
    assert set(found) == {"Spear Stab (SpearStabOffHandPlayer)", "Spark"}  # (an attack with no weapon isn't checked)
    assert found["Spark"]["skillId"] == "SparkPlayer"


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_sweep_takes_every_skill_id_of_a_shared_name(engine):
    rows = engine.call("auditSkills", only=["Lightning Bolt", "Mace Strike", "Spear Stab"])["skills"]
    by_name: dict[str, set[str]] = {}
    for row in rows:
        assert not row.get("error"), row
        by_name.setdefault(row["name"], set()).add(row["skillId"])
    print(by_name)
    assert all(len(ids) >= 2 for ids in by_name.values()) and set(by_name) == {"Lightning Bolt", "Mace Strike",
                                                                                  "Spear Stab"}
    assert "LightningBoltPlayer" in by_name["Lightning Bolt"]
