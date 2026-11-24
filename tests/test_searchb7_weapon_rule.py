"""SEARCHB7 (COV52 group A): a skill with no weapon requirement in the game can be held on the weapon a request names."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import (
    CASTER_WEAPONS, WEAPON_BOUND_NO_TYPES, enumerate_skeletons, main_hands, seed_skeleton,
)

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


def test_giants_blood_pair_only_when_a_request_names_it():
    named = context(main_skill="Earthshatter", ascendancies=["Titan"], weapon_type="Two Hand Mace", offhand="weapon")
    skeletons, problems = enumerate_skeletons(named, ["Earthshatter"])
    pair = [s for s in skeletons if s.weapon == "Two Hand Mace" and s.offhand == "Sceptre"]
    assert pair and all(s.requires_passive == "Giant's Blood" for s in pair) and not problems
    banned = context(main_skill="Earthshatter", ascendancies=["Titan"], weapon_type="Two Hand Mace", offhand="weapon",
                     forbid={"passives": ["Giant's Blood"]})
    assert not enumerate_skeletons(banned, ["Earthshatter"])[0]
    open_search = context(main_skill="Earthshatter", ascendancies=["Titan"])
    assert not any(s.requires_passive == "Giant's Blood" for s in enumerate_skeletons(open_search, ["Earthshatter"])[0])


def test_a_shield_skill_takes_a_named_main_hand_beside_its_shield():
    ctx = context(main_skill="Shield Wall", ascendancies=["Smith of Kitava"], weapon_type="Sceptre", offhand="shield")
    skeletons, problems = enumerate_skeletons(ctx, ["Shield Wall"])
    assert {(s.weapon, s.offhand) for s in skeletons} == {("Sceptre", "Shield")} and not problems
    no_shield = context(main_skill="Shield Wall", ascendancies=["Smith of Kitava"], weapon_type="Sceptre", offhand="focus")
    assert not enumerate_skeletons(no_shield, ["Shield Wall"])[0]


def facts(skill, weapon, off=None, swap=None, swap_off=None, passives=(), on_two=False):
    items = [{"slot": "Weapon 1", "type": weapon, "base": weapon, "name": weapon}]
    for slot, kind in (("Weapon 2", off), ("Weapon 1 Swap", swap), ("Weapon 2 Swap", swap_off)):
        if kind:
            items.append({"slot": slot, "type": kind, "base": kind, "name": kind})
    group = {"enabled": True, "main": True, "fromItem": False, "set1": not on_two,
             "gems": [{"name": skill, "support": False, "enabled": True}]}
    return {"ascendClassName": "Titan", "skillGroups": [group], "items": items, "passives": [{"name": p} for p in passives]}


def test_a_seed_keeps_its_own_legal_weapons():
    ctx = context(ascendancies=["Titan"])
    skeleton, notes = seed_skeleton(ctx, facts("Frostbolt", "Bow", "Quiver"))
    assert (skeleton.weapon, skeleton.offhand) == ("Bow", "Quiver") and not notes
    skeleton, notes = seed_skeleton(ctx, facts("Earthshatter", "Two Hand Mace", "Sceptre", passives=["Giant's Blood"]))
    assert (skeleton.weapon, skeleton.offhand, skeleton.requires_passive) == ("Two Hand Mace", "Sceptre", "Giant's Blood")
    skeleton, notes = seed_skeleton(ctx, facts("Earthshatter", "Two Hand Mace", "Sceptre"))  # (no keystone: not legal)
    assert skeleton.offhand != "Sceptre" and notes


def test_a_seed_whose_main_skill_is_on_weapon_set_two_keeps_both_sets():
    ctx = context(ascendancies=["Titan"])
    skeleton, notes = seed_skeleton(ctx, facts("Poisonburst Arrow", "Talisman", None, "Bow", "Quiver", on_two=True))
    assert (skeleton.weapon, skeleton.swap, skeleton.swap_weapon, skeleton.swap_offhand) == ("Talisman", True, "Bow", "Quiver")
    assert not notes
    skeleton, _ = seed_skeleton(ctx, facts("Crossbow Shot", "One Hand Mace", None, "Crossbow", on_two=True))
    assert skeleton is not None and skeleton.swap_weapon == "Crossbow"
    skeleton, found = seed_skeleton(ctx, facts("Crossbow Shot", "One Hand Mace"))  # (no crossbow held: still refused)
    assert skeleton is None and "default attack" in found[0]
