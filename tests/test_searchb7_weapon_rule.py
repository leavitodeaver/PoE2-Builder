"""SEARCHB7 (COV52 group A): a skill with no weapon requirement in the game can be held on the weapon a request names."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import CASTER_WEAPONS, WEAPON_BOUND_NO_TYPES, main_hands

TABLES = Path(__file__).resolve().parent.parent / "data" / "game-26650308" / "tables"


def rows(name: str) -> list[dict]:
    data = json.loads((TABLES / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["rows"]


def context(**request) -> Context:
    return Context(make_payload(BuildRequest(name="t", level=90, **request), PriceFeed().table(None, refresh=False)))


@pytest.mark.skipif(not TABLES.exists(), reason="game tables not extracted")
def test_the_bound_list_is_exactly_the_typeless_skills_with_a_game_requirement():
    active, effects = rows("ActiveSkills.json"), {e["Id"]: e for e in rows("GrantedEffects.json")}
    bound = set()
    for gem in GameData().gems:
        types = gem.get("skillTypes") or []
        if gem.get("support") or gem.get("legacy") or "Attack" in types or gem.get("weaponTypes"):
            continue
        effect = effects.get(gem.get("grantedEffectId"))
        if effect and effect.get("ActiveSkill") is not None and active[effect["ActiveSkill"]].get("WeaponRequirements") is not None:
            bound.add(gem["name"])
    assert bound == set(WEAPON_BOUND_NO_TYPES)


@pytest.mark.parametrize("skill, weapon", [("Frostbolt", "Bow"), ("Supporting Fire", "Two Hand Mace")])
def test_a_named_weapon_holds_a_skill_with_no_requirement(skill, weapon):
    ctx = context(main_skill=skill, weapon_type=weapon)
    assert main_hands(ctx, skill) == ([(weapon, None)], [])


def test_the_open_search_still_looks_at_caster_weapons():
    ctx = context(main_skill="Frostbolt")
    assert main_hands(ctx, "Frostbolt")[0] == CASTER_WEAPONS


def test_a_skill_with_a_requirement_is_still_refused():
    ctx = context(main_skill="Magma Barrier", weapon_type="Bow")
    mains, problems = main_hands(ctx, "Magma Barrier")
    assert mains == [] and "can't be used with a Bow" in problems[0]
    attack = context(main_skill="Earthshatter", weapon_type="Bow")
    assert main_hands(attack, "Earthshatter")[0] == []
