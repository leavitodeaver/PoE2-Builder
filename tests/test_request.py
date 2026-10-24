"""Request schema and constraint compiler tests. Needs the game-data dump (`python -m builder.data dump`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from builder import ROOT
from builder.data import GameData
from builder.pob import codes
from builder.request.constraints import FACT_FIELDS, compile_request, player_buttons
from builder.request.schema import BuildRequest

FIXTURES = Path(__file__).parent / "fixtures" / "requests"


@pytest.fixture(scope="module")
def data() -> GameData:
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


def load(name: str) -> BuildRequest:
    return BuildRequest.model_validate_json((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", [p.stem for p in sorted(FIXTURES.glob("*.json"))])
def test_custom_build_requests_compile_cleanly(data, name):
    compiled = compile_request(load(name), data)
    assert compiled.errors == []


def test_unknown_names_are_errors_with_suggestions(data):
    request = BuildRequest(forbid={"supports": ["Brutalty II"]}, main_skill="Ice Shoot")
    messages = [issue.message for issue in compile_request(request, data).errors]
    assert any("Brutalty II" in m and "Brutality II" in m for m in messages)
    assert any("Ice Shoot" in m and "Ice Shot" in m for m in messages)


def test_required_and_forbidden_together_is_rejected():
    with pytest.raises(ValidationError):
        BuildRequest(require={"skills": ["Spark"]}, forbid={"skills": ["Spark"]})


def test_balanced_is_the_default_goal_and_the_others_stay_selectable():
    assert BuildRequest().objective == "balanced"
    assert {BuildRequest(objective=o).objective for o in ("boss", "mapping", "balanced")} == {"boss", "mapping", "balanced"}


def test_ascendancy_must_belong_to_class(data):
    request = BuildRequest(class_name="Witch", ascendancies=["Titan"])
    assert any(issue.field == "ascendancies" for issue in compile_request(request, data).errors)


def test_mechanic_bans_expand_to_gems(data):
    filters = compile_request(load("little_minions"), data).filters
    for banned in ("Skeletal Warrior", "Wolf Pack", "Tamed Companion", "Summon Spectre"):
        assert banned in filters.forbidden_gems
    assert "Raise Zombie" not in filters.forbidden_gems


def test_button_count_skips_persistent_meta_and_triggered():
    def gem(name, *types, support=False):
        return {"name": name, "support": support, "enabled": True, "skillTypes": list(types), "tags": []}

    def group(*gems, from_item=False):
        return {"enabled": True, "fromItem": from_item, "main": False, "gems": list(gems)}

    facts = {"skillGroups": [
        group(gem("Ice Shot", "Triggerable"), gem("Longshot II", support=True)),
        group(gem("Barrage", "Buff", "Cooldown")),
        group(gem("Pinnacle of Power", "Buff", "Cooldown", "Instant")),
        group(gem("Purity of Fire", "Aura", "Persistent")),
        group(gem("Cast on Elemental Ailment", "Meta", "Persistent"), gem("Ball Lightning", "Triggerable")),
        group(gem("Spear Throw", "Attack"), from_item=True),
    ]}
    assert player_buttons(facts) == ["Ice Shot", "Barrage", "Pinnacle of Power"]


def test_invocations_are_pressed_but_their_spells_are_not():
    def gem(name, *types):
        return {"name": name, "support": False, "enabled": True, "skillTypes": list(types), "tags": []}

    facts = {"skillGroups": [
        {"enabled": True, "fromItem": False, "main": True, "gems": [gem("Spark", "Spell", "Triggerable")]},
        {"enabled": True, "fromItem": False, "main": False, "gems": [
            gem("Elemental Invocation", "Meta", "Persistent", "Invocation", "GeneratesEnergy"),
            gem("Comet", "Spell", "Triggerable")]},
        {"enabled": True, "fromItem": False, "main": False, "gems": [
            gem("Cast on Critical", "Meta", "Persistent", "GeneratesEnergy"), gem("Frost Bomb", "Spell", "Triggerable")]},
    ]}
    assert player_buttons(facts) == ["Spark", "Elemental Invocation"]


def test_skeletons_count_invocations_and_not_the_spells_a_meta_triggers(data):
    from builder.search.context import Context, make_payload
    from builder.search.skeletons import skill_problems

    def problems(**body):
        return skill_problems(Context(make_payload(BuildRequest(main_skill="Spark", **body))), "Spark")

    # Comet goes into Cast on Critical (inner.Search.setup), so only Spark is pressed
    assert problems(max_buttons=1, require={"skills": ["Cast on Critical", "Comet"]}) == []
    # An Invocation is pressed to release its Energy: Spark + Elemental Invocation is two
    found = problems(max_buttons=1, require={"skills": ["Elemental Invocation", "Comet"]})
    assert found and "Elemental Invocation" in found[0] and "Comet" not in found[0]
    assert problems(max_buttons=2, require={"skills": ["Elemental Invocation", "Comet"]}) == []


def test_checks_on_a_real_build(data):
    """The user's Acolyte build (reference 2b75b) against the Explosive Transmutation request."""
    from builder.pob.pool import EnginePool

    xml = codes.decode((ROOT / "benchmarks" / "reference" / "2b75b.txt").read_text(encoding="utf-8"))
    with EnginePool(1) as pool:
        pool.broadcast("loadXml", xml=xml)
        facts = pool.broadcast("facts", fields=FACT_FIELDS)[0]
    results = {r.label: r for r in compile_request(load("explosive_transmutation"), data).check(facts)}
    assert results["Ascendancy"].ok, results["Ascendancy"]
    assert results["Main skill"].ok, results["Main skill"]
    assert results["Fire resistance"].requested == "≥ 75"


def test_weapon_swap_check_needs_a_skill_on_set_two_and_gear_for_it(data):
    def facts(gear: bool, set_two: bool, from_item: bool = False) -> dict:
        groups = [{"enabled": True, "fromItem": False, "main": True, "set1": True, "set2": True,
                   "gems": [{"name": "Explosive Spear", "support": False, "enabled": True, "skillTypes": [], "tags": []}]}]
        if set_two:
            groups.append({"enabled": True, "fromItem": from_item, "main": False, "set1": False, "set2": True,
                           "gems": [{"name": "Ice Shot", "support": False, "enabled": True, "skillTypes": [], "tags": []}]})
        items = [{"slot": "Weapon 1", "type": "Spear", "name": "Spear"}]
        if gear:
            items.append({"slot": "Weapon 1 Swap", "type": "Bow", "name": "Bow"})
        return {"skillGroups": groups, "items": items, "stats": {}, "passives": [], "warnings": []}

    def swap(policy: str, f: dict):
        compiled = compile_request(BuildRequest(main_skill="Explosive Spear", weapon_swap=policy), data)
        return next(c for c in compiled.check(f) if c.label == "Weapon swap")

    assert swap("must", facts(True, True)).ok and "Ice Shot" in swap("must", facts(True, True)).delivered
    assert not swap("must", facts(True, False)).ok  # gear on set 2 with nothing using it isn't a swap
    assert not swap("must", facts(True, True, from_item=True)).ok  # a weapon's own attack doesn't count
    assert swap("never", facts(False, False)).ok and not swap("never", facts(True, False)).ok
