"""Which passives a request can require: ascendancy passives need their ascendancy, and the nodes near a
class start that some classes get a different version of (Vile Wounds is Eagle Eye for a Huntress)."""

from __future__ import annotations

import pytest

from builder.data import GameData
from builder.request.constraints import ascendancy_groups, compile_request, passive_names, passive_rules
from builder.request.schema import BuildRequest


@pytest.fixture(scope="module")
def data() -> GameData:
    try:
        game = GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    if not any(n.get("variants") for n in game.tree["nodes"]):
        pytest.skip("the tree dump predates class variants; run `uv run python -m builder.data dump`")
    return game


def errors(request: BuildRequest, data: GameData) -> list[str]:
    return [f"{i.field}: {i.message}" for i in compile_request(request, data).issues if i.level == "error"]


def request(**fields) -> BuildRequest:
    require = {"passives": fields.pop("require", [])}
    return BuildRequest.model_validate({"require": require, **fields})


def test_ascendancy_passive_needs_its_ascendancy(data):
    # A Gemling Legionnaire notable, required by a request for another ascendancy or class
    assert errors(request(require=["Advanced Thaumaturgy"], ascendancies=["Gemling Legionnaire"]), data) == []
    assert errors(request(require=["Advanced Thaumaturgy"]), data) == []  # any ascendancy: fine
    assert errors(request(require=["Advanced Thaumaturgy"], class_name="Mercenary"), data) == []
    wrong = errors(request(require=["Advanced Thaumaturgy"], ascendancies=["Tactician"]), data)
    assert len(wrong) == 1 and "Gemling Legionnaire" in wrong[0] and wrong[0].startswith("require.passives")
    assert errors(request(require=["Advanced Thaumaturgy"], class_name="Witch"), data)
    # Banning one is harmless
    assert errors(BuildRequest.model_validate({"forbid": {"passives": ["Advanced Thaumaturgy"]}, "class_name": "Witch"}), data) == []


def test_alternate_ascendancy_shares_its_twin_nodes(data):
    assert "Abyssal Lich" in ascendancy_groups(data)["Lich"]
    rule = passive_rules(data)["Incessant Cacophony"]
    assert set(rule["ascendancies"]) == {"Lich", "Abyssal Lich"}
    assert errors(request(require=["Incessant Cacophony"], ascendancies=["Abyssal Lich"]), data) == []
    assert errors(request(require=["Incessant Cacophony"], ascendancies=["Lich"]), data) == []
    assert errors(request(require=["Incessant Cacophony"], ascendancies=["Infernalist"]), data)


def test_class_versions_of_a_node_resolve_by_class(data):
    # PoB gives a Huntress Eagle Eye where every other class has Vile Wounds
    names = passive_names(data)
    assert "Vile Wounds" in names and "Reflex Action" in names
    assert errors(request(require=["Vile Wounds"], class_name="Ranger"), data) == []
    huntress = errors(request(require=["Vile Wounds"], class_name="Huntress"), data)
    assert len(huntress) == 1 and "Eagle Eye" in huntress[0]
    assert errors(request(require=["Reflex Action"], class_name="Huntress"), data) == []
    assert errors(request(require=["Reflex Action"], class_name="Warrior"), data)
    # Ascendancies decide the class when none is given
    assert errors(request(require=["Reflex Action"], ascendancies=["Amazon"]), data) == []
    assert errors(request(require=["Reflex Action"], ascendancies=["Deadeye"]), data)


def test_no_class_asked_for_narrows_the_search_to_classes_that_have_it(data):
    plain = compile_request(request(), data).filters.ascendancies
    only = compile_request(request(require=["Reflex Action"]), data).filters.ascendancies
    assert set(only) == set(data.classes["Huntress"]) and len(only) < len(plain)
    # A name every class has, or one an ascendancy request already settles, narrows nothing
    assert compile_request(request(require=["Mind Over Matter"]), data).filters.ascendancies == plain
    assert compile_request(request(require=["Reflex Action"], ascendancies=["Amazon"]), data).filters.ascendancies == ["Amazon"]
    # A name two nodes share (Eagle Eye: a Huntress version and another class's own node) is available to every class
    assert "Eagle Eye" not in passive_rules(data)
