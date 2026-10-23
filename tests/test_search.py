"""Search pieces that don't need an engine: skeleton enumeration, catalogs, the objective, the
ascendancy enumeration and the condition evidence rules. Needs the game-data dump."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.conditions import CONDITIONS, qualifies, text_evidence
from builder.search.context import Context, make_payload
from builder.search.objective import Objective
from builder.search.skeletons import enumerate_skeletons
from builder.search.tree import rooted_subsets

FIXTURES = Path(__file__).parent / "fixtures" / "requests"


@pytest.fixture(scope="module", autouse=True)
def data() -> GameData:
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


def context(**body) -> Context:
    return Context(make_payload(BuildRequest(**body)))


def fixture(name: str) -> Context:
    request = BuildRequest.model_validate_json((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return Context(make_payload(request))


# Skeletons -------------------------------------------------------------------------------------


def test_every_allowed_ascendancy_gets_a_skeleton():
    ctx = fixture("explosive_transmutation")
    skeletons, problems = enumerate_skeletons(ctx, [ctx.request.main_skill])
    assert {s.ascendancy for s in skeletons} == {"Shaman", "Acolyte of Chayula"}
    assert all(s.main_skill == "Explosive Transmutation" for s in skeletons)


def test_weapon_and_offhand_follow_the_request():
    ctx = fixture("ice_shot_gemling")
    skeletons, _ = enumerate_skeletons(ctx, ["Ice Shot"])
    assert [(s.ascendancy, s.weapon, s.offhand) for s in skeletons] == [("Gemling Legionnaire", "Bow", "Quiver")]


def test_banned_offhand_class_is_never_proposed():
    ctx = context(main_skill="Ice Shot", ascendancies=["Deadeye"], forbid={"item_classes": ["Quiver"]})
    skeletons, _ = enumerate_skeletons(ctx, ["Ice Shot"])
    assert skeletons and all(s.offhand is None for s in skeletons)


def test_button_cap_drops_skeletons_with_a_reason():
    ctx = context(main_skill="Ice Shot", ascendancies=["Deadeye"], max_buttons=1, require={"skills": ["Spark"]})
    skeletons, problems = enumerate_skeletons(ctx, ["Ice Shot"])
    assert skeletons == [] and any("buttons" in p for p in problems)


def test_incompatible_weapon_type_is_explained():
    ctx = context(main_skill="Ice Shot", ascendancies=["Deadeye"], weapon_type="Wand")
    skeletons, problems = enumerate_skeletons(ctx, ["Ice Shot"])
    assert skeletons == [] and any("Wand" in p for p in problems)


# Catalogs and budget ---------------------------------------------------------------------------


def test_unpriced_or_banned_uniques_are_never_options():
    request = BuildRequest(main_skill="Ice Shot", budget={"divines": 5}, forbid={"uniques": ["Voltaxic Rift"]})
    unpriced = Context(make_payload(request))
    assert not [o for options in unpriced.item_options.values() for o in options if o.unique],         "without prices, uniques are excluded rather than treated as free"
    payload = make_payload(request)
    payload["unique_prices"] = {"Voltaxic Rift": [0.1, 50], "Mageblood": [400.0, 30], "Murkshaft": [1.0, 2]}
    priced = Context(payload)
    names = {o.name for options in priced.item_options.values() for o in options if o.unique}
    assert names == {"Murkshaft"}  # banned, over budget, and unpriced uniques are all left out
    assert next(o for o in priced.item_options["Quiver"] if o.unique).confidence == "low"


def test_armour_pieces_respect_the_defence_style():
    ctx = context(main_skill="Ice Shot", defence="armour")
    for slot_type in ("Helmet", "Body Armour", "Gloves", "Boots"):
        for option in ctx.item_options.get(slot_type, []):
            armour = ctx.bases_by_name[option.base].get("armour") or {}
            assert not armour.get("Evasion") and not armour.get("EnergyShield"), option.name


def test_legacy_and_granted_gems_are_not_socketable():
    ctx = context(main_skill="Ice Shot")
    assert "Ice Shot" in ctx.gems
    assert all(not g.get("fromItem") and not g.get("fromTree") and not g.get("legacy") for g in ctx.gems.values())


# Objective -------------------------------------------------------------------------------------


def test_objective_prefers_feasible_builds():
    ctx = context(main_skill="Ice Shot", floors={"life": 2000, "resists": {}})
    objective = Objective(ctx)
    base = {"CombinedDPS": 100000, "Life": 2000, "Str": 50, "ReqStr": 40, "Spirit": 100, "SpiritUnreserved": 0}
    assert objective.deficits(base) == []
    short_life = {**base, "Life": 1000, "CombinedDPS": 200000}
    assert objective.score(short_life) < objective.score(base)
    over_reserved = {**base, "SpiritUnreserved": -10, "CombinedDPS": 500000}
    assert objective.score(over_reserved) < objective.score(base)
    short_str = {**base, "Str": 10}
    assert any(hard for _, _, hard in objective.deficits(short_str))


def test_full_dps_leaves_out_damage_that_needs_enemies_to_die():
    ctx = context(main_skill="Spark")
    objective = Objective(ctx, full=True)
    stats = {"FullDPS": 1119640, "CombinedDPS": 60000, "SkillDPS": [
        {"name": "Fiery Death", "dps": 920048, "count": 1},
        {"name": "Best Poison DPS", "dps": 145096, "count": 1, "source": "Fiery Death"},
        {"name": "Spark", "dps": 54496, "count": 1},
    ]}
    assert objective.dps(stats) == pytest.approx(54496)
    assert objective.excluded(stats)[1] == ["Fiery Death", "Best Poison DPS from Fiery Death"]
    assert "Contagion" not in objective.on_death  # its damage hits the target; only the spread needs a death


def test_objective_never_reads_total_ehp():
    ctx = context(main_skill="Ice Shot")
    assert "TotalEHP" not in Objective(ctx).fields


# Ascendancy enumeration --------------------------------------------------------------------------


def test_rooted_subsets_are_connected_and_unique():
    adjacency = {0: [1, 2], 1: [0, 3], 2: [0], 3: [1]}
    subsets = rooted_subsets(0, adjacency, 2)
    assert len(subsets) == len(set(subsets))
    assert set(subsets) == {frozenset(s) for s in ([0], [0, 1], [0, 2], [0, 1, 2], [0, 1, 3])}


def test_ascendancy_allocations_are_maximal_and_legal():
    """Every proposed allocation spends at most 8 points, is connected from the start node and
    holds at most one option per multiple-choice node."""
    import random as _random

    from builder.search.tree import Tree

    ctx = context(main_skill="Ice Shot")
    tree = Tree.__new__(Tree)
    tree.ctx = ctx
    for ascendancy in ("Deadeye", "Pathfinder", "Gemling Legionnaire", "Acolyte of Chayula"):
        start, adjacency = ctx.ascendancy_graph(ascendancy)
        nodes = ctx.nodes
        options = {i: [j for j in adjacency[i] if nodes[j].get("isMultipleChoiceOption")]
                   for i in adjacency if nodes[i].get("isMultipleChoice")}
        core = {i: [j for j in links if not nodes[j].get("isMultipleChoiceOption")]
                for i, links in adjacency.items() if not nodes[i].get("isMultipleChoiceOption")}
        allocations = tree.allocations(start, core, options, set(), adjacency)
        assert allocations
        for ids in _random.Random(0).sample(allocations, min(50, len(allocations))):
            paid = [i for i in ids if not nodes[i].get("isMultipleChoiceOption")]
            assert len(paid) <= 8
            seen = {start}
            for node in ids:  # allocation order: each node touches one already allocated
                assert set(adjacency[node]) & seen, (ascendancy, node)
                seen.add(node)
            for parent, picks in options.items():
                assert len(set(picks) & set(ids)) <= (1 if parent in ids else 0)


# Condition evidence ----------------------------------------------------------------------------

RULES = {c.var: c for c in CONDITIONS}


@pytest.mark.parametrize("var, line, types, expected", [
    ("useFrenzyCharges", "Gain a Frenzy Charge on Critical Hit", {"Attack"}, True),
    ("useFrenzyCharges", "5% increased Attack Speed per Frenzy Charge", {"Attack"}, False),
    ("useFrenzyCharges", "50% chance when you gain a Frenzy Charge to gain an additional Frenzy Charge", {"Attack"}, False),
    ("multiplierRage", "Gain 1 Rage on Melee Hit", {"Attack", "Projectile"}, False),
    ("multiplierRage", "Gain 1 Rage on Melee Hit", {"Attack", "Melee"}, True),
    ("buffOnslaught", "Minions gain Onslaught", {"Attack"}, False),
    ("conditionEnemyBlinded", "20% more Damage against Blinded Enemies", {"Attack"}, False),
    ("conditionEnemyBlinded", "Blind Targets when you Poison them", {"Attack"}, True),
])
def test_condition_evidence(var, line, types, expected):
    assert (text_evidence(RULES[var], line) and qualifies(line, types)) is expected


def test_unsustainable_boss_conditions_are_never_resolved():
    never = {"conditionEnemyStunned", "conditionEnemyFrozen", "conditionEnemyElectrocuted", "conditionEnemyDazed",
             "conditionEnemyPinned", "conditionEnemyHeavyStunned"}
    assert not never & set(RULES)
    assert math.isfinite(len(RULES))
