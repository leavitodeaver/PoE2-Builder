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
