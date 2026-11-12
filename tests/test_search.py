"""Search pieces that don't need an engine: skeleton enumeration, catalogs, the objective, the
ascendancy enumeration and the condition evidence rules. Needs the game-data dump."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import models
from builder.search.conditions import CONDITIONS, Conditions, qualifies, stat_set_needs, text_evidence
from builder.search.context import Context, make_payload
from builder.search.objective import Objective
from builder.search.skeletons import (
    enumerate_skeletons, offhand_types, offhand_weapon_types, swap_skills,
)
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


def test_off_hand_skeletons_follow_the_games_rules_not_pobs():
    """builder/data/game_rules.yaml: a spear is never dual wielded; the only weapon beside it is a
    sceptre (PoB would also allow daggers, flails, axes, swords and maces there)."""
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon")
    skeletons, _ = enumerate_skeletons(ctx, ["Explosive Spear"])
    assert [(s.weapon, s.offhand) for s in skeletons] == [("Spear", "Sceptre")]
    assert skeletons[0].label.endswith("Spear + Sceptre")
    assert offhand_weapon_types(ctx, "Spear") == ["Sceptre"]
    assert offhand_weapon_types(ctx, "One Hand Mace") == ["Sceptre", "One Hand Mace"]
    assert offhand_weapon_types(ctx, "Wand") == ["Sceptre"] and offhand_weapon_types(ctx, "Bow") == []
    banned = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon",
                     forbid={"item_classes": ["Sceptre"]})
    skeletons, problems = enumerate_skeletons(banned, ["Explosive Spear"])
    assert skeletons == [] and any("off hand takes only Shield, Focus" in p for p in problems)


def test_a_passive_can_let_a_staff_hold_a_focus():
    """game_rules.yaml off_hand.passive_exceptions (RULE_AUDIT #7): Instruments of Power, a Disciple of Varashta notable,
    lets a caster Staff hold a Focus; the skeleton requires the passive. Other ascendancies, a banned passive or a
    request for no off hand get no such skeleton. Lord of the Wilds, a keystone, lets a Talisman hold a non-unique
    Sceptre for every ascendancy (its off-hand slot takes no unique: test_engine_items)."""
    ctx = context(main_skill="Fireball", offhand="focus", weapon_type="Staff")
    skeletons, problems = enumerate_skeletons(ctx, ["Fireball"])
    assert [(s.ascendancy, s.weapon, s.weapon_sub, s.offhand, s.requires_passive) for s in skeletons] == \
        [("Disciple of Varashta", "Staff", "", "Focus", "Instruments of Power")] and not problems
    assert skeletons[0].label.endswith("Staff + Focus (with Instruments of Power)")
    others = context(main_skill="Fireball", offhand="focus", weapon_type="Staff", ascendancies=["Stormweaver"])
    assert enumerate_skeletons(others, ["Fireball"])[0] == []
    banned = context(main_skill="Fireball", offhand="focus", weapon_type="Staff",
                     forbid={"passives": ["Instruments of Power"]})
    assert enumerate_skeletons(banned, ["Fireball"])[0] == []
    bare = context(main_skill="Fireball", offhand="none", weapon_type="Staff", ascendancies=["Disciple of Varashta"])
    assert [s.offhand for s in enumerate_skeletons(bare, ["Fireball"])[0]] == [None]
    quarterstaff = context(main_skill="Falling Thunder", offhand="any", ascendancies=["Disciple of Varashta"])
    assert not any(s.requires_passive for s in enumerate_skeletons(quarterstaff, ["Falling Thunder"])[0])
    assert not any(s.weapon == "Talisman" and s.offhand for s in enumerate_skeletons(context(), ["Fireball"])[0])
    wilds = context(main_skill="Furious Slam", ascendancies=["Shaman"], offhand="weapon")
    skeletons, problems = enumerate_skeletons(wilds, ["Furious Slam"])
    assert [(s.weapon, s.offhand, s.requires_passive) for s in skeletons] == \
        [("Talisman", "Sceptre", "Lord of the Wilds")] and not problems
    tame = context(main_skill="Furious Slam", ascendancies=["Shaman"], offhand="weapon",
                   forbid={"passives": ["Lord of the Wilds"]})
    skeletons, problems = enumerate_skeletons(tame, ["Furious Slam"])
    assert skeletons == [] and any("Talisman takes no off-hand weapon" in p for p in problems)


def test_spear_throw_comes_from_any_spear():
    """Every Spear's implicit grants Spear Throw (PoB gives it to any Spear main hand, CalcSetup's default attacks):
    besides the 10 unique spears that name it, a skeleton takes it from any Spear the gear search picks. Only a
    skill every base of a type grants is listed so (a Withered Wand's Chaos Bolt needs that base kept)."""
    ctx = context(main_skill="Spear Throw", ascendancies=["Amazon"])
    assert [g for g in ctx.granted_by["Spear Throw"] if g[0] == "base"] == [("base", "Spear", None)]
    assert not any(k == "base" for grants in ctx.granted_by.values() for k, _, _ in grants
                   if grants is not ctx.granted_by["Spear Throw"])
    skeletons, problems = enumerate_skeletons(ctx, ["Spear Throw"])
    base = [s for s in skeletons if s.granted_by == "base:Spear"]
    assert base and all(s.weapon == "Spear" for s in base) and not problems
    assert "Spear Throw (from any Spear)" in base[0].label
    assert any(s.granted_by.startswith("unique:") for s in skeletons)
    banned = context(main_skill="Spear Throw", ascendancies=["Amazon"], forbid={"item_classes": ["Spear"]})
    assert enumerate_skeletons(banned, ["Spear Throw"])[0] == []


def test_true_dual_wielding_is_two_one_handed_maces():
    ctx = context(main_skill="Boneshatter", ascendancies=["Titan"], offhand="weapon")
    skeletons, problems = enumerate_skeletons(ctx, ["Boneshatter"])
    assert {s.label.split(" · ")[-1] for s in skeletons} == {"One Hand Mace + Sceptre", "One Hand Mace + One Hand Mace"}
    assert any("Two Hand Mace takes no off-hand weapon" in p for p in problems)


def test_offhand_any_offers_every_legal_off_hand_and_weapon_classes_not_in_the_game_never():
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"])
    skeletons, _ = enumerate_skeletons(ctx, ["Explosive Spear"])
    assert {s.offhand for s in skeletons} == {"Shield", "Focus", "Sceptre"}
    ctx = context(main_skill="Ice Shot", ascendancies=["Deadeye"], offhand="weapon")
    skeletons, problems = enumerate_skeletons(ctx, ["Ice Shot"])
    assert skeletons == [] and any("off hand takes only Quiver" in p for p in problems)
    claws = context(main_skill="Ice Shot", ascendancies=["Deadeye"], weapon_type="Claw")
    assert any("Claws aren't in the game" in p for p in enumerate_skeletons(claws, ["Ice Shot"])[1])


def test_off_hand_slot_holds_the_skeletons_class():
    ctx = context(main_skill="Explosive Spear")
    assert offhand_types(ctx, "Spear", "Sceptre") == ("Sceptre",)
    assert offhand_types(ctx, "Spear", "Shield") == ("Shield",) and offhand_types(ctx, "Bow", None) == ()


def test_required_skills_set_one_cant_use_get_weapon_set_two():
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon",
                  require={"skills": ["Ice Shot"]})
    skeletons, problems = enumerate_skeletons(ctx, ["Explosive Spear"])
    assert [(s.swap, s.swap_weapon, s.swap_offhand) for s in skeletons] == [(True, "Bow", "Quiver")], problems
    assert skeletons[0].label.endswith("· swap: Bow + Quiver")
    assert swap_skills(ctx, skeletons[0]) == ["Ice Shot"]
    never = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon", weapon_swap="never",
                    require={"skills": ["Ice Shot"]})
    skeletons, problems = enumerate_skeletons(never, ["Explosive Spear"])
    assert not any(s.swap for s in skeletons) and any("forbids weapon swap" in p for p in problems)


def test_weapon_swap_must_copies_set_one_and_may_adds_nothing_by_itself():
    must = context(main_skill="Spark", ascendancies=["Stormweaver"], offhand="focus", weapon_swap="must")
    skeletons, _ = enumerate_skeletons(must, ["Spark"])
    assert skeletons and all(s.swap and (s.swap_weapon, s.swap_offhand) == (s.weapon, s.offhand) for s in skeletons)
    may = context(main_skill="Spark", ascendancies=["Stormweaver"], offhand="focus")
    assert not any(s.swap for s in enumerate_skeletons(may, ["Spark"])[0])
    # Spells work with any weapon, so a required spell never needs set 2
    spell = context(main_skill="Ice Shot", ascendancies=["Deadeye"], require={"skills": ["Spark"]})
    assert not any(s.swap for s in enumerate_skeletons(spell, ["Ice Shot"])[0])


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


def test_a_resistance_floor_above_the_maximum_counts_as_fully_short():
    """2b75e (POE2T294): a Rune of the Prism (+20% to all Elemental Resistances, -10% to all Maximum) held fire's maximum at
    70 under a 72 floor, and dropping it alone cost 20% resistance, so every single-item trial kept it. A floor the
    maximum can't reach now counts as fully short on top, so the build without the rune scores better."""
    ctx = context(main_skill="Ice Shot", floors={"life": 0, "resists": {"fire": 72}})
    objective = Objective(ctx)
    assert "MissingFireResist" in objective.fields
    base = {"CombinedDPS": 100000, "Spirit": 100, "SpiritUnreserved": 0}
    prism = {**base, "FireResist": 70, "MissingFireResist": 0}  # 74 total, capped at a 70 maximum
    plain = {**base, "FireResist": 54, "MissingFireResist": 26}  # 54 under an 80 maximum
    labels = [label for label, _, _ in objective.deficits(prism)]
    assert labels == ["Fire resistance", "Maximum Fire resistance"]
    assert [label for label, _, _ in objective.deficits(plain)] == ["Fire resistance"]
    assert objective.score(plain) > objective.score(prism)
    assert objective.deficits({**base, "FireResist": 75, "MissingFireResist": 5}) == []


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


def test_requirements_still_short_weigh_more_until_the_cap():
    from builder.search.objective import MAX_MULTIPLIER, rank_key

    ctx = context(main_skill="Ice Shot", floors={"mana": 4000, "resists": {}})
    objective = Objective(ctx)
    short = {"CombinedDPS": 60000, "Mana": 2000}
    before = objective.score(short)
    assert objective.escalate(short) and objective.multipliers["Mana"] > 1
    assert objective.score(short) < before
    while objective.escalate(short):
        pass
    assert objective.multipliers["Mana"] == MAX_MULTIPLIER
    # Ranking across searches ignores their weights: smaller shortfall first, then damage
    closer = {"deficits": [{"label": "Mana", "amount": 0.1, "hard": False}], "dps": 5000}
    farther = {"deficits": [{"label": "Mana", "amount": 0.5, "hard": False}], "dps": 60000}
    assert rank_key(closer) < rank_key(farther)


def test_objective_never_reads_total_ehp():
    ctx = context(main_skill="Ice Shot")
    assert "TotalEHP" not in Objective(ctx).fields


# Clear speed and the goals ----------------------------------------------------------------------


def test_clear_estimate_counts_reach_speed_and_movement():
    from builder.search.objective import PACK_SIZE, clear_estimate

    single = {"Speed": 2.0, "EffectiveMovementSpeedMod": 1.0}
    one = clear_estimate(single, 50000)
    assert one["targets"] == pytest.approx(1.0)
    # Every kind of reach adds targets, never more than a pack holds
    for extra in ({"AreaOfEffectRadius": 30}, {"ProjectileCount": 3}, {"ChainMax": 2}, {"PierceCount": 2},
                  {"ForkCountMax": 1}):
        wider = clear_estimate({**single, **extra}, 50000)
        assert 1.0 < wider["targets"] < PACK_SIZE and wider["score"] > one["score"]
    everything = clear_estimate({**single, "AreaOfEffectRadius": 60, "ProjectileCount": 10, "ChainMax": 5}, 50000)
    assert PACK_SIZE - 0.01 < everything["targets"] <= PACK_SIZE
    # Faster uses and faster walking clear faster
    assert clear_estimate({**single, "Speed": 4.0}, 50000)["score"] > one["score"]
    assert clear_estimate({**single, "EffectiveMovementSpeedMod": 1.3}, 50000)["score"] > one["score"]
    # While damage is the limit, clear is hit DPS x targets (the PLAN.md §5.5 formula)...
    instant = {"AreaOfEffectRadius": 30, "Speed": "inf", "EffectiveMovementSpeedMod": 1e9}
    weak = clear_estimate(instant, 1000)
    assert weak["score"] == pytest.approx(1000 * weak["targets"], rel=1e-6)
    # ...and once every use kills what it reaches, more damage hardly helps
    assert clear_estimate(single, 1e10)["score"] < 1.01 * clear_estimate(single, 1e9)["score"]
    assert clear_estimate(single, 0)["score"] == 0.0
    # A minion skill's reach and speed are the minion's
    minion = {"Minion.CombinedDPS": 5000, "Minion.AreaOfEffectRadius": 30, "Minion.Speed": 1.0, "Speed": 9.0}
    assert clear_estimate(minion, 5000)["targets"] > 1.0 and clear_estimate(minion, 5000)["uses_per_second"] == 1.0


def goals(full: bool = False, **body) -> dict[str, Objective]:
    return {goal: Objective(context(main_skill="Spark", objective=goal, **body), full=full)
            for goal in ("boss", "mapping", "balanced")}


def test_goal_is_boss_dps_clear_or_their_geometric_mean():
    from builder.search.objective import rank_key

    objectives = goals(floors={"resists": {}})
    pack = {"CombinedDPS": 100000, "AreaOfEffectRadius": 30, "Speed": 2.0, "EffectiveMovementSpeedMod": 1.2}
    clear = objectives["mapping"].clear(pack)["score"]
    assert objectives["boss"].value(pack) == pytest.approx(100000)
    assert objectives["mapping"].value(pack) == pytest.approx(clear)
    assert objectives["balanced"].value(pack) == pytest.approx(math.sqrt(100000 * clear))
    # Twice the damage on one target against a pack clearer: each goal prefers its own
    single = {"CombinedDPS": 200000, "Speed": 2.0, "EffectiveMovementSpeedMod": 1.2}
    assert objectives["boss"].score(single) > objectives["boss"].score(pack)
    assert objectives["mapping"].score(pack) > objectives["mapping"].score(single)
    # Movement speed is worth nothing to a boss score, something to balanced
    faster = {**pack, "EffectiveMovementSpeedMod": 1.5}
    assert objectives["boss"].score(faster) == objectives["boss"].score(pack)
    assert objectives["balanced"].score(faster) > objectives["balanced"].score(pack)
    # Results rank on the goal, not on boss DPS
    assert rank_key({"deficits": [], "dps": 100, "value": 500}) < rank_key({"deficits": [], "dps": 200, "value": 300})


def test_clearing_counts_damage_that_needs_enemies_to_die():
    objectives = goals(full=True)
    stats = {"FullDPS": 1000000, "CombinedDPS": 60000, "Speed": 2.0, "SkillDPS": [
        {"name": "Fiery Death", "dps": 940000, "count": 1},
        {"name": "Spark", "dps": 60000, "count": 1},
    ]}
    assert objectives["boss"].dps(stats) == pytest.approx(60000)
    assert objectives["mapping"].clear(stats)["damage"] == pytest.approx(1000000)
    # Neither half of balanced can be traded down to nothing: all damage needing enemies to die is
    # worth a lot when mapping, nothing in balanced (no boss damage at all)
    explosions = {**stats, "SkillDPS": [{"name": "Fiery Death", "dps": 1000000, "count": 1}]}
    assert objectives["mapping"].value(explosions) > 0 and objectives["balanced"].value(explosions) == 0.0


def test_balanced_runs_some_searches_on_boss_dps_and_ranks_them_all_on_balanced():
    from builder.search.objective import goal_value
    from builder.search.run import Run, replica_goals

    assert replica_goals("balanced", 1) == [None]
    assert replica_goals("balanced", 2) == [None, "boss"]
    assert replica_goals("balanced", 14).count("boss") == 5
    assert replica_goals("boss", 3) == replica_goals("mapping", 3) == [None] * 3
    # A boss-DPS search inside a balanced request scores boss DPS...
    ctx = context(main_skill="Lightning Arrow", objective="balanced")
    stats = {"CombinedDPS": 100000, "AreaOfEffectRadius": 30, "Speed": 2.0}
    assert Objective(ctx, goal="boss").value(stats) == pytest.approx(100000)
    assert Objective(ctx).value(stats) == pytest.approx(goal_value("balanced", 100000, Objective(ctx).clear(stats)["score"]))
    # ...and the run ranks every result on balanced, whatever value its search reported
    run = Run(BuildRequest(main_skill="Lightning Arrow", objective="balanced"), workers=2, seconds=10)
    result = {"label": "LA", "deficits": [], "score": 0.0, "evals": 1}
    boss_search = {**result, "goal": "boss", "dps": 814744, "clear": 251329, "value": 814744, "state": "hard hitter"}
    balanced_search = {**result, "goal": "balanced", "dps": 535133, "clear": 311727, "value": 408430, "state": "fast"}
    lopsided = {**result, "goal": "boss", "dps": 2000000, "clear": 10000, "value": 2000000, "state": "lopsided"}
    results = run._record([balanced_search, lopsided, boss_search])
    assert run.best is boss_search and boss_search["value"] == pytest.approx(math.sqrt(814744 * 251329))
    assert lopsided["value"] == pytest.approx(math.sqrt(2000000 * 10000))
    # Boss searches go on from the best boss build; balanced ones from the best on balanced
    states = {}
    run.keep_states(states, results)
    assert run.state_for(states, "LA", "boss") == "lopsided"
    assert run.state_for(states, "LA", None) == "hard hitter"
    # Each replica goes on from its own line; a replica without one (or a failed task) from the skeleton's best
    from builder.pob.pool import WorkerError
    from builder.search.skeletons import Skeleton

    skeleton = Skeleton("Ranger", "Deadeye", "Lightning Arrow", "Bow", None, "Quiver").as_dict()
    label = Skeleton(**skeleton).label
    tasks = [("x", {"skeleton": skeleton, "goal": g}) for g in (None, "boss", None)]
    lines = {}
    run.keep_lines(lines, tasks, [{"state": "first"}, {"state": "second"}, WorkerError("lost")])
    states = {(label, "balanced"): "best"}
    assert run.line_state(lines, states, label, 0, None) == "first"
    assert run.line_state(lines, states, label, 1, "boss") == "second"
    assert run.line_state(lines, states, label, 2, None) == "best"
    assert run.line_state(lines, states, label, 3, None) == "best"


def test_feasibility_penalties_are_the_same_for_every_goal():
    objectives = goals(floors={"life": 2000, "resists": {}})
    short = {"CombinedDPS": 100000, "Life": 1000, "Str": 10, "ReqStr": 40, "AreaOfEffectRadius": 20}
    penalties = {goal: o.penalty(short) for goal, o in objectives.items()}
    assert penalties["boss"] > 0 and len(set(penalties.values())) == 1


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
    ("useFrenzyCharges", "2% chance that if you would gain Frenzy Charges, you instead gain up to your maximum number "
                         "of Frenzy Charges", {"Attack"}, False),
    ("useFrenzyCharges", "Gain Frenzy Charges instead of Endurance Charges", {"Attack"}, False),
    ("usePowerCharges", "Gain Power Charges instead of Frenzy Charges", {"Attack"}, False),
    ("useFrenzyCharges", "While active, grants you a Frenzy Charge when you Freeze, Electrocute or Pin an enemy",
     {"Attack"}, True),
    ("buffOnslaught", "Minions gain Onslaught", {"Attack"}, False),
])
def test_condition_evidence(var, line, types, expected):
    assert (text_evidence(RULES[var], line) and qualifies(line, types)) is expected


def test_unsustainable_boss_conditions_are_never_resolved():
    # (Frozen and Electrocuted: at the share the boss carries them since MECH38, from the buildup models; Pinned likewise
    # since MECH46, from PoB's Pin buildup)
    never = {"conditionEnemyStunned", "conditionEnemyDazed", "conditionEnemyHeavyStunned"}
    assert not never & set(RULES)
    assert all(RULES[var].value_fn is not None
               for var in ("conditionEnemyFrozen", "conditionEnemyElectrocuted", "conditionEnemyPinned"))
    assert math.isfinite(len(RULES))


def test_stat_sets_that_consume_a_charge_need_its_source():
    conditions = Conditions.__new__(Conditions)
    conditions.active = {}
    assert stat_set_needs("Frenzy-Charged") == ["useFrenzyCharges"]
    assert conditions.allows("Explosion") and conditions.allows(None)
    assert not conditions.allows("Frenzy-Charged")
    assert conditions.allows("Frenzy-Charged", assume=("useFrenzyCharges",))
    assert not conditions.allows("Vs Frozen", assume=("useFrenzyCharges",))  # a boss fight never sustains freeze
    conditions.active = {"useFrenzyCharges": {"label": "Frenzy Charges", "value": True, "evidence": "gem:Combat Frenzy"}}
    assert conditions.allows("Frenzy-Charged")


# Rares and sockets -------------------------------------------------------------------------------


def test_ranges_roll_where_asked():
    from builder.search.rares import roll

    assert roll("+(110-114) to maximum Mana", 0.5) == "+112 to maximum Mana"
    assert roll("Adds (1-3) to (5-9) Fire Damage", 1.0) == "Adds 3 to 9 Fire Damage"
    assert roll("Regenerate (1.5-2.5)% of maximum Life per second", 0.5) == "Regenerate 2.0% of maximum Life per second"


def test_affix_pools_respect_tags_groups_and_tier_caps():
    from builder.search.rares import AffixPools

    endgame = AffixPools(context(main_skill="Ice Shot", budget={"gear_tier": "endgame"}))
    league = AffixPools(context(main_skill="Ice Shot", budget={"gear_tier": "league_start"}))
    ring = next(b["name"] for b in endgame.ctx.data.bases if b["type"] == "Ring" and not b.get("hidden"))
    pool = endgame.pool(ring)
    assert pool and len({(a.group, a.type) for a in pool}) == len(pool)  # one affix per group
    assert all(a.tier >= 1 for a in pool) and all(a.tier >= 4 for a in league.pool(ring))
    assert not any("(" in line for a in pool for line in a.lines)  # every range rolled


def test_socket_lines_go_before_the_implicits():
    from builder.search.rares import with_runes

    unique = "Andvarius\nGold Ring\nImplicits: 1\n(6-15)% increased Rarity"
    assert with_runes(unique, ["Iron Rune"]).splitlines()[2:4] == ["Sockets: S", "Rune: Iron Rune"]
    rare = "Rarity: Rare\nX\nFanatic Bow\nItem Level: 82\nImplicits: 0\n+10 to Dexterity"
    lines = with_runes(rare, ["A", "B"]).splitlines()
    assert lines.index("Sockets: S S") < lines.index("Implicits: 0") and "Rune: B" in lines


def test_better_affixes_cost_more():
    from builder.search.rares import Affix, rare_price

    top = Affix("a", "g1", "Prefix", 1, ("x",))
    low = Affix("b", "g2", "Prefix", 4, ("y",))
    assert rare_price("Helmet", [top]) > rare_price("Helmet", [low]) > 0
    assert rare_price("Weapon 1", [top]) > rare_price("Helmet", [top])


def test_unsustainable_skill_costs_fall_short():
    from builder.search.objective import sustain

    assert sustain({"ManaPerSecondCost": 1000, "ManaRegenRecovery": 100})[0] == pytest.approx(0.1)
    assert sustain({"LifePerSecondCost": 700, "LifeLeechRate": 310, "LifeRegenRecovery": 40})[0] == pytest.approx(0.5)
    assert sustain({})[0] == 1.0
    objective = Objective(context(main_skill="Ice Nova", floors={"resists": {}}))
    labels = [label for label, _, _ in objective.deficits({"ManaPerSecondCost": 1000, "ManaRegenRecovery": 40})]
    assert "Skill cost sustain: Mana" in labels


# Flasks and charms -------------------------------------------------------------------------------


def test_flask_effects_count_only_while_generated_charges_keep_them_up():
    from builder.search.flasks import Charges, uptime

    mana = {"kind": "Flask", "duration": 3, "chargesUsed": 10, "manaTotal": 310}
    assert uptime(mana, Charges()) == 0.0  # nothing dies in a boss fight: no charges, no effect
    assert uptime(mana, Charges(flask=2)) == pytest.approx(0.6)  # 2/s x 3 s / 10 per use
    assert uptime(mana, Charges(flask=2, flask_duration_inc=100)) == 1.0
    assert uptime({**mana, "gainBase": 1, "gainInc": 100}, Charges(mana_flask=1), "Mana") == 1.0  # (1+1) x 2 x 3 s
    assert uptime(mana, Charges(life_flask=5), "Mana") == 0.0  # Life flask generation doesn't fill Mana flasks
    golden = {"kind": "Charm", "duration": 4, "chargesUsed": 20}
    assert uptime(golden, Charges(charm=50), trigger="Used when you kill a Rare or Unique enemy") == 0.0
    assert uptime(golden, Charges(charm=5), trigger="Used when you become Frozen") == 1.0


def test_flask_recovery_counts_toward_sustain_at_the_rate_charges_allow():
    from builder.search.flasks import RECOVERY_FIELDS, Charges, recovery_per_second
    from builder.search.objective import sustain

    mana = {"kind": "Flask", "duration": 3, "chargesUsed": 10, "manaTotal": 300}
    assert recovery_per_second(mana, Charges(), "Mana") == 0.0
    assert recovery_per_second(mana, Charges(flask=1), "Mana") == pytest.approx(30.0)  # one use per 10 s
    assert recovery_per_second(mana, Charges(flask=100), "Mana") == pytest.approx(100.0)  # one use at a time
    assert recovery_per_second(mana, Charges(flask=1, mana_recovery_inc=50), "Mana") == pytest.approx(45.0)
    life = {"kind": "Flask", "duration": 3, "chargesUsed": 10, "lifeTotal": 300}
    assert recovery_per_second(life, Charges(flask=1), "Life") == pytest.approx(30.0)
    assert recovery_per_second(life, Charges(flask=1, life_flask_blocked=True), "Life") == 0.0  # Sanguine Tides
    assert recovery_per_second(mana, Charges(flask=1, life_flask_blocked=True), "Mana") == pytest.approx(30.0)
    stats = {"ManaPerSecondCost": 1000, "ManaRegenRecovery": 40, RECOVERY_FIELDS["Mana"]: 60}
    assert sustain(stats)[0] == pytest.approx(0.1)


def test_magic_flasks_take_one_prefix_and_one_suffix_from_the_flask_pool():
    from builder.search.rares import MAGIC_LIMIT, AffixPools, magic_text

    ctx = context(main_skill="Spark")
    pool = AffixPools(ctx, "Flask").pool("Ultimate Mana Flask")
    assert pool and any("Charges per Second" in line for a in pool for line in a.lines)
    assert not any("Life Recovered" in line for a in pool for line in a.lines)  # life_flask only
    assert MAGIC_LIMIT == {"Prefix": 1, "Suffix": 1}
    text = magic_text("Flask 2", ctx.bases_by_name["Ultimate Mana Flask"], 82, pool[:1])
    assert text.startswith("Rarity: Magic\nUltimate Mana Flask\nItem Level: 82")


def test_only_an_ascendancy_with_a_ring_slot_passive_gets_ring_three():
    from builder.search.gear import extra_ring_passive

    ctx = context(main_skill="Explosive Spear")
    assert extra_ring_passive(ctx, "Ritualist") == "Unfurled Finger"
    assert extra_ring_passive(ctx, "Deadeye") is None


def test_spirit_skills_go_in_at_the_highest_gem_level_the_build_can_use():
    from builder.search.gems import highest_usable_level

    # Elemental Conflux per PoB: level 20 needs character level 90 and 157 Intelligence
    levels = [{"level": level, "levelRequirement": need, "str": 0, "dex": 0, "int": intelligence}
              for level, need, intelligence in ((1, 0, 0), (10, 36, 65), (15, 64, 113), (20, 90, 157))]
    assert highest_usable_level(levels, {"int": 200}, 95) == 20
    assert highest_usable_level(levels, {"int": 120}, 95) == 15
    assert highest_usable_level(levels, {"int": 200}, 80) == 15  # a level-80 character can't use level 20
    assert highest_usable_level(levels, {}, 95) == 1
    assert highest_usable_level([], {"int": 200}, 95) is None


def test_a_feasible_result_always_outranks_one_that_misses_a_floor():
    """Gate 4 on 2b75c (2026-10-24): the trapped build had 3.6M DPS and no elemental resistances
    (Veil of the Night) against 75% floors; the feasible one 1.14M. The run must deliver the
    feasible one."""
    from builder.search.objective import rank_key

    short = [{"label": f"{kind} resistance", "amount": 1.0, "hard": False} for kind in ("Fire", "Cold", "Lightning")]
    veil = {"deficits": short, "dps": 3_602_315, "value": 3_602_315}
    feasible = {"deficits": [], "dps": 1_139_413, "value": 1_139_413}
    assert sorted([veil, feasible], key=rank_key)[0] is feasible


def test_boss_fight_conditions_from_the_builds_own_numbers():
    from builder.search import conditions as c

    by_var = {x.var: x for x in c.CONDITIONS}
    hit, block = by_var["conditionBeenHitRecently"].stat, by_var["conditionBlockedRecently"].stat
    base = {"enemySkillTime": 0.7, "EvadeChance": 0, "EffectiveBlockChance": 0}
    assert hit(base) and not block(base)  # the boss's hits land: hit recently; no block
    assert not hit({**base, "EvadeChance": 95, "EffectiveBlockChance": 50})  # almost every hit avoided
    assert block({**base, "EffectiveBlockChance": 40}) and not block({**base, "EffectiveBlockChance": 20})
    assert by_var["conditionHaveEnergyShield"].stat({"EnergyShield": 1}) and not by_var["conditionHaveEnergyShield"].stat({})
    assert by_var["conditionAtCloseRange"].main_types == ("Melee",)


def test_a_flask_that_applies_its_effect_constantly():
    """Lavianga's Spirits: "This Flask cannot be Used but applies its Effect constantly" (PoB has no rule for it)."""
    from builder.search.flasks import Charges, recovery_per_second, uptime

    info = {"kind": "Flask", "chargesUsed": 0, "duration": 4.0, "manaTotal": 400, "constant": True}
    assert uptime(info, Charges(), "Mana") == 1.0
    assert recovery_per_second(info, Charges(), "Mana") == pytest.approx(100.0)
    assert recovery_per_second({**info, "constant": False}, Charges(), "Mana") == 0.0  # no charges to use it otherwise


def test_grinning_immolation_ignites_you():
    by_var = {c.var: c for c in CONDITIONS}
    line = "Become Ignited when you deal a Critical Hit, taking 15% of your maximum Life and Energy Shield as Fire Damage per second"
    assert text_evidence(by_var["conditionIgnited"], line)


def test_withered_stacks_from_the_builds_lines():
    from builder.search.conditions import _withered_stacks

    spell = {"_main_types": {"Spell"}}
    quill = "Spells have a 25% chance to inflict Withered for 4 seconds on Hit"
    assert _withered_stacks([quill], {**spell, "Speed": 4.0})[0] == 4.0  # 0.25 x 4/s x 4 s
    assert _withered_stacks([quill], {**spell, "Speed": 40.0})[0] == 10.0  # at most 10
    keep = "Withered does not expire on Enemies Ignited by you"
    assert _withered_stacks([quill, keep], {**spell, "Speed": 4.0, "IgniteChancePerHit": 100})[0] == 10.0
    assert _withered_stacks([keep], {**spell, "Speed": 4.0, "IgniteChancePerHit": 100})[0] == 0.0  # no Withered source
    # Ignite up part of the fight (the user's rule, 9 Nov: the real share): 10 stacks at Ignite's uptime, the chance-line
    # stacks (here 4) with it off; under 7%, only the chance-line stacks
    value, basis, up, off = _withered_stacks([quill, keep], {**spell, "Speed": 4.0, "IgniteChancePerHit": 5, "IgniteDuration": 4})
    assert value == 10.0 and off == 4 and up == pytest.approx(1 - 0.95 ** 16) and "while it is Ignited" in basis
    assert _withered_stacks([quill, keep], {**spell, "Speed": 4.0, "IgniteChancePerHit": 0.1, "IgniteDuration": 4}) == (4.0, _withered_stacks([quill], {**spell, "Speed": 4.0})[1])
    assert _withered_stacks([quill], {"Speed": 4.0, "_main_types": {"Attack"}})[0] == 0.0  # a Spells line, an Attack


def test_withered_stacks_from_gems_and_infinite_duration():
    """RULE_AUDIT #1 (MECH42): the main skill's own Withered, Withering Touch, Withering Presence, infinite Duration."""
    from builder.search.conditions import _withered_stacks

    bolt = {"Speed": 1.0, "HitChance": 100, "_wither_main": ("Chaos Bolt", {"active_skill_withered_base_duration_ms": 4000,
                                                                          "apply_x_wither_on_hit": 2})}
    value, basis = _withered_stacks([], bolt)
    assert value == 8.0 and "Chaos Bolt" in basis  # 2 per hit x 1 hit/s x 4 s
    touch = {"Speed": 2.0, "HitChance": 50, "ChaosHitAverage": 10_000, "EnemyAilmentThreshold": 70_000,
             "_wither_support": {"support_withered_base_duration_ms": 4000,
                                 "withered_on_hit_chance_%_for_every_100%_target_ailment_threshold_dealt_as_chaos_damage": 175}}
    assert _withered_stacks([], touch)[0] == 1.0  # 1.75 x 1/7 = 0.25 per hit x 1 hit/s x 4 s
    presence = {"Speed": 1.0, "PresenceRadius": 40, "_main_types": {"Melee"}, "_wither_presence": (1.0, 6.0)}
    assert _withered_stacks([], presence)[0] == 6.0  # 6 s every 1 s, the boss 2 m away (melee) in a 4 m Presence
    far = {**presence, "_distance_choice": (8.0, "test")}
    value, basis = _withered_stacks([], far)
    assert value == 0.0 and basis == ""
    forever = "Withered you inflict has infinite Duration"
    assert _withered_stacks([forever], touch)[0] == 10.0
    assert _withered_stacks([forever], {"Speed": 1.0})[0] == 0.0  # no Withered source
    away = "Unwithered Enemies are Withered for 8 seconds when they enter your Presence"
    assert "not counted" in _withered_stacks([away], bolt)[1]



def test_charges_held_follow_the_builds_sources():
    from builder.search.conditions import _charges_held

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15,
             "FrenzyChargesMax": 3, "FrenzyChargesDuration": 15}
    count, basis = _charges_held("Power")(["10% chance to gain a Power Charge on Critical Hit"], stats)
    # 10% x 1 crit/s: s = 1 - e^-1.5 that a gain comes within 15 s; held s + s^2 + s^3 (KeywordPopups Charges: refreshed)
    assert count == pytest.approx(1.8)
    assert _charges_held("Power")(["Gain a Power Charge on Critical Hit"], stats)[0] == 3  # 1/s x 15 s, at most 3
    assert _charges_held("Frenzy")(["20% chance to gain a Frenzy Charge on kill"], stats) == (0.0, _charges_held("Frenzy")(["20% chance to gain a Frenzy Charge on kill"], stats)[1])
    assert _charges_held("Frenzy")(["20% chance to gain a Frenzy Charge on kill"], stats)[1]  # sources, but none on a boss
    assert _charges_held("Power")(["+1 to Maximum Power Charges"], stats) == (0.0, "")  # not a source



def test_resonance_and_conduit_move_the_charges_a_build_gains():
    """Resonance (KeywordPopups ChargeCycle): Power instead of Frenzy, Frenzy instead of Endurance, Endurance instead of
    Power. Conduit: "If you would gain a Charge, Allies in your Presence gain that Charge instead". Nothing read either,
    so the player kept the original charges (MECHC2)."""
    from builder.search.conditions import _charges_held

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15,
             "FrenzyChargesMax": 3, "FrenzyChargesDuration": 15, "EnduranceChargesMax": 3, "EnduranceChargesDuration": 15}
    resonance = ["Gain Power Charges instead of Frenzy Charges", "Gain Frenzy Charges instead of Endurance Charges",
                 "Gain Endurance Charges instead of Power Charges"]
    crit_frenzy = "Gain a Frenzy Charge on Critical Hit"
    alone = _charges_held("Frenzy")([crit_frenzy], stats)
    assert alone[0] == 3
    frenzy = _charges_held("Frenzy")([crit_frenzy, *resonance], stats)
    assert frenzy[0] == 0.0 and "Power Charges instead" in frenzy[1]  # a basis with 0: the use box goes off
    power = _charges_held("Power")([crit_frenzy, *resonance], stats)
    assert power[0] == alone[0] and "Power Charges gained instead of Frenzy" in power[1]
    assert _charges_held("Endurance")([crit_frenzy, *resonance], stats) == (0.0, "")  # no Power source to feed it
    conduit = "If you would gain a Charge, Allies in your Presence gain that Charge instead"
    held, basis = _charges_held("Frenzy")([crit_frenzy, conduit], stats)
    assert held == 0.0 and "Conduit" in basis
    assert _charges_held("Power")(["Gain a Power Charge on Critical Hit", conduit.replace("a Charge", "an Endurance Charge")],
                                  stats)[0] == 3  # the one-kind wording leaves the others


def test_resonance_switches_the_use_box_of_the_kind_it_feeds():
    """A Power Charge gained from a Frenzy source names no Power source, so the resolver's text match never switches
    usePowerCharges on: the redirected basis does (Conditions.resolve)."""
    from builder.search import conditions

    assert conditions.CHARGE_REDIRECTED in conditions._charges_held("Power")(
        ["Gain a Frenzy Charge on Critical Hit", "Gain Power Charges instead of Frenzy Charges"],
        {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15})[1]

def test_rage_held_from_gains_spends_and_its_decay():
    """KeywordPopups Rage: 1 lost per 0.2 s, the loss paused 4 s on a gain or a hit taken, one Hit per 0.5 s gains."""
    from builder.search.conditions import rage_held

    melee = {"Speed": 3.0, "HitChance": 100, "CritChance": 0, "MaximumRage": 30, "enemySkillTime": 0.7,
             "EvadeChance": 100, "_main_types": {"Attack", "Melee"}}
    held, basis = rage_held(["Gain 1 Rage on Melee Hit"], melee)
    assert held == 30 and "2.00 gains/s" in basis  # 3 hits/s, capped at 2 gains/s: the maximum
    assert rage_held(["Gain 1 Rage on Melee Hit"], {**melee, "_main_types": {"Attack", "Projectile"}}) == (0.0, "")
    kill = rage_held(["Gain 10 Rage when you kill an enemy"], melee)
    assert kill[0] == 0 and "on kill" in kill[1]  # (was the maximum before MECH27)
    # A gain once per 10 s with no hit taken: 3 Rage held 4 s, then lost over 0.6 s: (12 + 0.9) / 10
    rare = rage_held(["Gain 3 Rage on Hit with Attacks, no more than once every 10 seconds"], melee)
    assert rare[0] == 1
    # Taking the boss's hits pauses the loss too: the same gain keeps the maximum
    assert rage_held(["Gain 3 Rage on Hit with Attacks, no more than once every 10 seconds"],
                     {**melee, "EvadeChance": 0})[0] == 30
    # Spenders: Molten Crash at 3 uses/s spends 30/s, more than the 2/s gained
    spent = rage_held(["Gain 1 Rage on Melee Hit"], {**melee, "_rage_spend": {"Molten Crash": 30.0}})
    assert spent[0] == 0 and "Molten Crash" in spent[1]
    # A channelled spender as the main skill gains nothing from its own hits; the boss's hits still give Rage
    channel = {**melee, "_main_channel_rage": True, "EvadeChance": 0}
    assert rage_held(["Gain 1 Rage on Melee Hit"], channel)[0] == 0
    assert rage_held(["Gain 5 Rage when Hit by an Enemy"], channel)[0] == 30
    # Regeneration gains every second (pause kept); Primal Hunger's regeneration needs Rage spent, so it's no source
    assert rage_held(["Regenerate 5 Rage per second"], melee)[0] == 30
    assert rage_held(["Regenerate 1 Rage per second per 4 Rage spent Recently"], melee) == (0.0, "")
    # MECHC1, from the build's own rates (FLAT_RATES A3, A10): Mana spent at the main skill's ManaPerSecondCost, not a
    # Hit (no 0.5 s cap): 8 Rage per 200 Mana at 100 Mana/s is 0.5 gains/s, the loss paused, the maximum
    mana = rage_held(["Gain 8 Rage after Spending a total of 200 Mana"], {**melee, "ManaPerSecondCost": 100})
    assert mana[0] == 30 and "0.50 gains/s" in mana[1]
    # at 10 Mana/s: one gain of 8 every 20 s, held 4 s then lost over 1.6 s: (32 + 8 x 1.6 - 2.5 x 1.6^2) / 20 = 1.92
    assert rage_held(["Gain 8 Rage after Spending a total of 200 Mana"], {**melee, "ManaPerSecondCost": 10})[0] == 1
    assert rage_held(["Gain 8 Rage after Spending a total of 200 Mana"], melee)[0] == 0  # spends none: no gain
    # Rage regenerated from Mana Regeneration: 50/s over 25 is 2 Rage/s; none without Mana regeneration
    regen = rage_held(["1 Rage Regenerated for every 25 Mana Regeneration per Second"], {**melee, "ManaRegenRecovery": 50})
    assert regen[0] == 30 and "2.00/s regenerated" in regen[1]
    assert rage_held(["1 Rage Regenerated for every 25 Mana Regeneration per Second"], melee)[0] == 0
    # Primal Hunger: 1/s per 4 spent in the last 4 s gives back all of a steady spend, so Molten Crash's 30/s no
    # longer empties the 2/s gained (it was 0 above)
    hunger = ["Gain 1 Rage on Melee Hit", "Regenerate 1 Rage per second per 4 Rage spent Recently"]
    back = rage_held(hunger, {**melee, "_rage_spend": {"Molten Crash": 30.0}})
    assert back[0] == 30 and "100% of the Rage spent" in back[1]
    # half of it back at 1 per 8: 30/s spent less 15 back is more than the 2/s gained; 2/s spent less 1 back is less
    half = ["Gain 1 Rage on Melee Hit", "Regenerate 1 Rage per second per 8 Rage spent Recently"]
    assert rage_held(half, {**melee, "_rage_spend": {"Molten Crash": 2.0}})[0] == 30
    assert rage_held(half, {**melee, "_rage_spend": {"Molten Crash": 30.0}})[0] == 0


def test_onslaught_on_only_when_its_sources_keep_it_up():
    """KeywordPopups Onslaught: 4 s unless specified; on kill nothing on a lone boss; the box on at 90% uptime."""
    from builder.search.conditions import onslaught_uptime

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "enemySkillTime": 0.7, "EvadeChance": 100,
             "FrenzyChargesMax": 3, "FrenzyChargesDuration": 15, "_main_types": {"Attack"}}
    on, basis, _ = onslaught_uptime(["30% chance to gain Onslaught for 4 seconds on Hit"], stats)
    assert on is True and "94%" in basis  # 1 - 0.7^8 (2 hits/s x 4 s)
    up = onslaught_uptime(["10% chance to gain Onslaught for 4 seconds on Hit"], stats)
    assert up[2] < 0.9 and "57%" in up[1]  # 1 - 0.9^8
    kill = onslaught_uptime(["You gain Onslaught for 8 seconds on Kill"], stats)
    assert kill[2] < 0.9 and "on kill" in kill[1]  # (was on before MECH27)
    for line in ("Companions have 50% chance to gain Onslaught on Kill", "5% chance to grant Onslaught to nearby Enemies on Kill",
                 "Grants Skill: Level 20 Starborn Onslaught"):
        assert onslaught_uptime([line], stats) == (0.0, "")
    # 2 hits/s x 10% crits = 0.2 crits/s: a 4 s Onslaught per crit is up 0.8 of the time
    assert onslaught_uptime(["You gain Onslaught for 4 seconds on Critical Hit"], stats)[2] < 0.9
    assert onslaught_uptime(["You gain Onslaught for 6 seconds when Hit"], {**stats, "EvadeChance": 0})[2] >= 0.9
    # At maximum Frenzy Charges only when they're held at the maximum
    frenzy = "Gain Onslaught for 4 seconds on Hit while at maximum Frenzy Charges"
    assert onslaught_uptime([frenzy], stats)[2] < 0.9
    assert onslaught_uptime([frenzy, "Gain a Frenzy Charge on Hit"], stats)[2] >= 0.9
    # (a "while at maximum ... Charges" line pays off on the charges; it was read as a source of them)
    from builder.search.conditions import _charges_held
    assert _charges_held("Power")(["Gain 10% of Physical Damage as Extra Chaos Damage while at maximum Power Charges"],
                                  {**stats, "PowerChargesMax": 3}) == (0.0, "")


def test_charges_consumed_per_use_are_not_held():
    from builder.search.conditions import _charges_held

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "FrenzyChargesMax": 3, "FrenzyChargesDuration": 15}
    source = ["Gain a Frenzy Charge on Critical Hit"]  # 1 per s
    assert _charges_held("Frenzy")(source, stats)[0] == 3
    spending = {**stats, "_consumers": {"Frenzy": {"Explosive Spear": 2.0}}}  # 2 uses per s, one charge each
    count, basis = _charges_held("Frenzy")(source, spending)
    assert count == 0.1 and "50%" in basis  # none left over; half its uses find a charge
    slow = {**stats, "_consumers": {"Frenzy": {"Explosive Spear": 0.25}}}
    assert _charges_held("Frenzy")(source, slow)[0] == 3  # 0.75/s left x 15 s, at most 3
    starved = {**stats, "_consumers": {"Frenzy": {"Explosive Spear": 4.0}}}
    assert _charges_held("Frenzy")(source, starved)[0] == 0  # a quarter of its uses: not counted as charged


def test_charges_drop_together_when_no_gain_refreshes_them():
    """KeywordPopups Charges: "Charges last for 15 seconds by default, refreshing whenever you gain another charge"."""
    from builder.search.conditions import charges_with_refresh

    assert charges_with_refresh(0.1, 15, 3) == pytest.approx(1.849, abs=0.001)  # s + s^2 + s^3, s = 1 - e^-1.5
    assert charges_with_refresh(1.0, 15, 3) == pytest.approx(3.0, abs=0.001)  # a gain every second: at the maximum
    assert charges_with_refresh(0.0, 15, 3) == 0.0
    assert charges_with_refresh(0.1, 15, 3, extra=1.0) == pytest.approx(2.157, abs=0.001)  # two per gain
    assert charges_with_refresh(0.1, 15, 3, extra=0.5) == pytest.approx(2.037, abs=0.001)
    assert charges_with_refresh(0.1, 15, 3, fill=0.1) == pytest.approx(1.910, abs=0.001)


def test_extra_charge_chances_raise_the_rate_and_fills_reach_the_maximum():
    """stat_descriptions: chance_to_gain_1_more_<k>_charge_%, ..._random_charge_%, gain_maximum_<k>_charges_on_..._gained."""
    from builder.search.conditions import _charges_held

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 50, "PowerChargesMax": 3, "PowerChargesDuration": 15}
    source = ["10% chance to gain a Power Charge on Critical Hit"]  # 0.1 gains/s
    assert _charges_held("Power")(source, stats)[0] == pytest.approx(1.8)
    more = source + ["50% chance when you gain a Power Charge to gain an additional Power Charge"]
    count, basis = _charges_held("Power")(more, stats)
    assert count == pytest.approx(2.0) and "50% extra" in basis
    assert _charges_held("Power")(source + ["Gain an additional Frenzy Charge when you gain a Frenzy Charge"], stats)[0]         == pytest.approx(1.8)  # another kind's line
    capped = source + ["Gain an additional Charge when you gain a Charge"] * 3
    assert _charges_held("Power")(capped, stats)[0] == pytest.approx(2.2)  # at most one extra per gain
    random = source + ["30% chance to gain an additional random Charge when you gain a Charge"]
    assert "random extra" in _charges_held("Power")(random, stats)[1]
    tribute = source + ["5% chance when you gain a Charge to gain an additional Charge per 10 Tribute"]
    held, basis = _charges_held("Power")(tribute, stats)
    assert held == pytest.approx(1.8) and "Tribute" in basis  # labelled, not counted
    fill = source + ["10% chance that if you would gain Power Charges, you instead gain up to your maximum number of Power Charges"]
    count, basis = _charges_held("Power")(fill, stats)
    assert count == pytest.approx(1.9) and "fills to the maximum" in basis
    spending = {**stats, "_consumers": {"Power": {"Hollow Form": 0.001}}}
    assert "not counted beside a consumer" in _charges_held("Power")(fill, spending)[1]
    gem = "While active, grants you a Power Charge on Critical Hit"
    profusion = {**stats, "_profusion": {gem: (0.30, 0.15)}}
    assert "Charge Profusion 35% of gains" in _charges_held("Power")([gem], profusion)[1]


def test_a_charge_source_is_worth_a_real_trial():
    from builder.search.conditions import CHARGE_OVERRIDES

    by_var = {c.var: c for c in CONDITIONS}
    cond = Conditions.__new__(Conditions)
    cond.main_types, cond.active = {"Attack"}, {"overrideFrenzyCharges": {"value": 1.0}}
    assert cond.could_source(by_var["overrideFrenzyCharges"], "Gain a Frenzy Charge on Critical Hit")
    assert not cond.could_source(by_var["overrideFrenzyCharges"], "20% chance to gain a Frenzy Charge on kill")
    assert cond.would_enable("Gain a Frenzy Charge on Critical Hit")  # the count is held below its maximum
    assert set(CHARGE_OVERRIDES) == {"overridePowerCharges", "overrideFrenzyCharges", "overrideEnduranceCharges"}


def test_ailment_uptime_uses_each_ailments_own_duration_and_hits_that_land():
    from builder.search.conditions import ailment_uptime

    # Poison lasts 2 s (0.5 data): 30% chance at 1.5 hits/s gives 1 - 0.7^3 = 66% (a 4 s window said 88%)
    assert ailment_uptime({"PoisonChancePerHit": 30, "Speed": 1.5}, "Poison") == pytest.approx(1 - 0.7 ** 3)
    # Bleed lasts 5 s; PoB's own duration (the build's modifiers) wins over the base
    assert ailment_uptime({"BleedChancePerHit": 40, "Speed": 1.2}, "Bleed") == pytest.approx(1 - 0.6 ** 6)
    assert ailment_uptime({"BleedChancePerHit": 40, "Speed": 1.2, "BleedDuration": 10}, "Bleed") == pytest.approx(1 - 0.6 ** 12)
    # misses apply nothing: 50% hit chance halves the hits
    assert ailment_uptime({"IgniteChancePerHit": 50, "Speed": 2, "HitChance": 50}, "Ignite") == pytest.approx(1 - 0.5 ** 4)
    assert ailment_uptime({"ShockChancePerHit": 0, "Speed": 2}, "Shock") == 0.0
    shocked = next(c for c in CONDITIONS if c.var == "conditionEnemyShocked")
    # on at any real share (MECH30: counted at it, was off under 90%): 2% x 2 hits/s x 8 s = 1 - 0.98^16, 28%
    assert shocked.stat({"ShockChancePerHit": 2, "Speed": 2}) and not shocked.stat({"ShockChancePerHit": 0, "Speed": 2})
    assert shocked.uptime_fn({"ShockChancePerHit": 2, "Speed": 2}) == pytest.approx(1 - 0.98 ** 16)


def test_shocked_ground_shocks_the_boss_and_counts_you_standing_in_it():
    """MECH43 (FIX_QUEUE; the user questioned "monster-only"): player-made Shocked Ground Shocks the boss standing in it
    (KeywordPopups), combined with the hits' own Shock as independent; Stormwalker's line counts in your own ground."""
    from builder.search.conditions import shock_share, shocked_ground

    by = {c.var: c for c in CONDITIONS}
    thunder = ["25% chance on Shocking Enemies to created Shocked Ground"]  # (the game's own typo, PoB's text)
    stats = {"ShockChancePerHit": 40, "Speed": 2, "HitChance": 100}
    share, how = shocked_ground(stats, thunder)
    # 2 hits/s x 40% x 25% = 10% per hit, 6 s by default: 1 - 0.9^12
    assert share == pytest.approx(1 - 0.9 ** 12) and "6 s by default" in how
    hits = 1 - 0.6 ** 16
    stats["_shocked_ground"] = (share, how)
    assert shock_share(stats) == pytest.approx(1 - (1 - hits) * (1 - share))
    assert by["conditionEnemyShocked"].uptime_fn(stats) == pytest.approx(shock_share(stats))
    # the ground's Shock at the base 20% (PoB keeps the strongest), only with a ground
    assert by["conditionShockEffect"].value_fn(thunder, stats)[0] == 20
    assert by["conditionShockEffect"].value_fn(thunder, {"ShockChancePerHit": 40, "Speed": 2})[0] == 0
    # MECH45 (the user's expectation, MEDIUM): the ground's 20% scales by the build's Shock magnitude, capped
    from builder.search.conditions import ground_shock_magnitude

    stats["_sg_magnitude"] = {"inc": 50, "more": 1.2, "enemy": 1.0, "max": 100, "left": []}
    value, basis = by["conditionShockEffect"].value_fn(thunder, stats)
    assert value == pytest.approx(36) and "MEDIUM" in basis and "Overcharge" not in basis
    assert ground_shock_magnitude({"inc": 400, "more": 1, "enemy": 1, "max": 50, "left": ["Skill:SupportOverchargePlayer"]})[0] == 50
    assert "Overcharge left out" in ground_shock_magnitude({"inc": 0, "max": 100, "left": ["x"]})[1]
    assert ground_shock_magnitude(None)[0] == 20
    # the main skill's own ground on each use, and Shocking Leap on the first crit of a use
    own, _ = shocked_ground({"Speed": 0.25, "_sg_skill": ("Storm Lance", 2000)}, [])
    assert own == pytest.approx(0.5)
    leap, how = shocked_ground({"Speed": 1, "CritChance": 50, "_sg_support": 4000}, [])
    assert leap == pytest.approx(1 - 0.5 ** 4) and "Shocking Leap" in how
    # a skill whose duration stat isn't found and Charged Mark: labelled, not counted
    assert shocked_ground({"Speed": 1, "_sg_skill": ("Gathering Storm", 0), "_sg_charged_mark": True}, [])[0] == 0
    assert "undercount" in shocked_ground({"Speed": 1, "_sg_charged_mark": True}, [])[1]
    # Wake of Destruction: counted as a way to play it, not while the build stands still without rolling
    wake = ["Drop Shocked Ground while moving, lasting 8 seconds"]
    melee = {"Speed": 1, "_distance_choice": (1.0, "melee")}
    assert shocked_ground(melee, wake)[0] == 1.0
    assert shocked_ground({**melee, "_stationary": True}, wake)[0] == 0.0
    assert shocked_ground({**melee, "_stationary": True, "_rolling": 0.05}, wake)[0] == 1.0
    ranged, how = shocked_ground({**melee, "_distance_choice": (6.0, "ranged")}, wake)
    assert ranged == 0.0 and "6 m from the boss" in how
    # you in it: Stormwalker at the ground's share in melee, not from 6 m
    walker = ["Gain 15% of Damage as Extra Lightning Damage while on Shocked Ground"]
    near = {"_shocked_ground": (0.6, "x"), "_distance_choice": (1.0, "melee")}
    on, basis, up = by["conditionOnShockedGround"].value_fn(walker, near)
    assert on and up == pytest.approx(0.6) and "the user" in basis and "MEDIUM" in basis
    assert not by["conditionOnShockedGround"].value_fn(walker, {**near, "_distance_choice": (6.0, "ranged")})[0]
    assert by["conditionOnShockedGround"].value_fn(["Unrelated"], near) == (0.0, "")


def test_a_search_that_ends_short_goes_back_to_the_best_feasible_build_it_saw():
    """Gate 4 on 2b75c (7 Nov, after meta slice 1): the delivered build was 5 Cold resistance short. A search's best
    by score can miss a floor (the score trades a small shortfall for damage) after it passed through builds that met
    every requirement; the search must end on the best of those instead."""
    from types import SimpleNamespace

    from builder.search.inner import Search

    class Objective:
        def deficits(self, stats):
            short = 75 - stats["ColdResist"]
            return [("Cold resistance", short / 75, False)] if short > 0 else []

        def value(self, stats):
            return stats["dps"]

    class Build:
        objective = Objective()

        def __init__(self):
            self.stats = {}

        def snapshot(self):
            return {"stats": dict(self.stats)}

        def restore(self, snap):
            self.stats = dict(snap["stats"])

    search = SimpleNamespace(build=Build(), feasible=None)
    search.shortfall = lambda stats: Search.shortfall(search, stats)
    for cold, dps in ((75, 1_000_000), (76, 1_200_000), (75, 900_000), (70, 3_000_000)):
        search.build.stats = {"ColdResist": cold, "dps": dps}
        Search.note_feasible(search)
    assert search.feasible["stats"]["dps"] == 1_200_000  # the best feasible one; the short 3M is never kept
    Search.keep_feasible(search)
    assert search.build.stats == {"ColdResist": 76, "dps": 1_200_000}
    search.build.stats = {"ColdResist": 80, "dps": 500_000}
    Search.keep_feasible(search)  # a feasible ending stays as it is, even below the best feasible one's damage
    assert search.build.stats["dps"] == 500_000


def test_recovery_against_a_boss():
    """The optional recovery floor (the user's choice, 7 Nov): sustained recovery, less skill costs, as a share of the
    boss's damage per second after mitigation; Mana only for the damage Mind over Matter sends to it."""
    from builder.search.objective import boss_recovery, with_boss_recovery

    stats = {"enemySkillTime": 0.5, "EvadeChance": 50, "EffectiveBlockChance": 0,  # 1 hit lands per second
             "PhysicalTakenHit": 800, "FireTakenHit": 200, "PhysicalLifeRecoup": 10,
             "LifeRegenRecovery": 150, "LifeLeechRate": 100, "LifePerSecondCost": 50, "FlaskLifeRecoveryRate": 20,
             "EnergyShieldRegenRecovery": 30, "ManaRegenRecovery": 500, "ManaPerSecondCost": 100, "sharedMindOverMatter": 20}
    r = boss_recovery(stats)
    assert r["incoming"] == pytest.approx(1000)
    assert r["parts"]["Life"] == pytest.approx(150 + 100 + 20 + 80 - 50)  # recoup: 10% of 800 physical per second
    assert r["parts"]["Mana"] == pytest.approx(200)  # 400 net, but MoM sends only 20% of 1,000 to Mana
    assert r["share"] == pytest.approx((300 + 30 + 200) / 1000)
    assert with_boss_recovery(dict(stats))["BossRecoveryPercent"] == pytest.approx(53.0)
    assert with_boss_recovery({})["BossRecoveryPercent"] == 1000.0  # no landing hits: nothing to recover from
    # the request: off by default, a floor when set
    assert context().compiled.penalties and not any(p.stat == "BossRecoveryPercent" for p in context().compiled.penalties)
    ctx = context(floors={"recovery": 60})
    penalty = next(p for p in ctx.compiled.penalties if p.stat == "BossRecoveryPercent")
    assert penalty.deficit({"BossRecoveryPercent": 53.0}) == pytest.approx(7 / 60)


def test_unique_prices_also_match_names_without_accents():
    """The trade feed names it Mjölner and PoB Mjolner: without the accent-free key Mjolner had no price, so the
    search never offered it (2b75d's weapon, which grants Thundergod's Wrath)."""
    from builder.data.prices import Price, PriceTable

    table = PriceTable("test", {}, {"Mjölner": [Price("Mjölner", 0.07, 1458, "UniqueWeapon")]}, 0.0)
    payload = make_payload(BuildRequest(main_skill="Boneshatter"), table)
    assert payload["unique_prices"]["Mjolner"] == [0.07, 1458]
    assert payload["unique_prices"]["Mjölner"] == [0.07, 1458]


def test_a_build_stops_its_search_once_past_its_hard_stop():
    """Build.stop_at: the first evaluation after it raises OutOfTime, once, so the search's wrap-up runs to its end."""
    from builder import clock
    from builder.search.state import Build, OutOfTime

    build = Build(None, None, None)
    build.tick()  # no stop set
    build.stop_at = clock.now() - 1.0
    with pytest.raises(OutOfTime):
        build.tick()
    assert build.stop_at is None
    build.tick()  # wrapping up isn't stopped again


def test_lines_far_behind_the_best_continue_from_it_and_the_best_goes_on():
    """Gate 4 (8 Nov): a run's five lines ended at 24K, 29K, 414K, 414K and 582K; the two far behind now continue
    from the best line's state for the focus stage's second round, and the others keep their own."""
    from types import SimpleNamespace

    from builder.search.run import RESTART_BELOW, Run
    from builder.search.skeletons import Skeleton

    skeleton = Skeleton("Monk", "Acolyte of Chayula", "Explosive Transmutation", "Wand", None, "Shield").as_dict()
    tasks = [("builder.search.inner:optimize_task", {"skeleton": skeleton, "goal": None}) for _ in range(5)]
    values = [24e3, 582e3, 414e3, 29e3, 414e3 * RESTART_BELOW * 0.99]
    raw = [{"dps": v, "search_value": v, "deficits": [], "state": {"xml": f"line {r}"}} for r, v in enumerate(values)]
    lines = {(Skeleton(**skeleton).label, r, ""): {"xml": f"line {r}"} for r in range(5)}
    events = []
    run = SimpleNamespace(emit=lambda kind, **data: events.append((kind, data)))
    assert Run.restart_behind(run, lines, tasks, raw) == 3
    label = Skeleton(**skeleton).label
    assert [lines[(label, r, "")]["xml"] for r in range(5)] == ["line 1", "line 1", "line 2", "line 1", "line 1"]
    # a line short of a requirement restarts from a feasible best, however high its own damage
    raw = [{"dps": 9e6, "search_value": 9e6, "deficits": [{"amount": 0.2, "hard": True}], "state": {"xml": "short"}},
           {"dps": 1e5, "search_value": 1e5, "deficits": [], "state": {"xml": "feasible"}}]
    lines = {}
    assert Run.restart_behind(run, lines, tasks[:2], raw) == 1 and lines == {(label, 0, ""): {"xml": "feasible"}}


def test_each_task_event_names_the_requirements_its_build_still_misses():
    """POE2T244 (9 Nov): whether any line ever met every floor had to be argued from the ranking; each task's event
    now carries its build's deficits and their weighted shortfall, empty and 0 when it met every floor."""
    from builder.search.run import Run

    events = []
    run = Run.__new__(Run)
    run.on_event, run.started, run.notes, run.probes, run.best, run.live = events.append, 0.0, [], {}, None, {}
    run.evals_done, run.seed_start = 0, None
    run.goal_of = lambda result: result["dps"]
    short = {"label": "Skeleton A", "dps": 2e6, "score": 1.0,
             "deficits": [{"label": "Cold resistance 70%", "amount": 0.1, "hard": False},
                          {"label": "Life", "amount": 0.05, "hard": True}]}
    feasible = {"label": "Skeleton A", "dps": 1e6, "score": 1.0, "deficits": []}
    Run._record(run, [short, feasible])
    tasks = [data for data in events if data["kind"] == "task"]
    assert tasks[0]["deficits"] == ["Cold resistance 70%", "Life"] and tasks[0]["shortfall"] == pytest.approx(0.6)
    assert tasks[1]["deficits"] == [] and tasks[1]["shortfall"] == 0


def test_armour_broken_on_only_when_breaks_keep_it_fully_broken():
    """KeywordPopups ArmourBreak: Fully Broken at 0 Armour for 12 s. A partial Break no longer turns the box on (MECH28)."""
    from builder.search.conditions import armour_broken_uptime

    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "PhysicalHitAverage": 1000, "FireHitAverage": 1000,
             "_enemy_armour": 1000, "_main_types": {"Attack"}}
    # 10% of 1000 Physical x 2 hits/s = 200/s: 5 s to break, up 12 / 17
    low = armour_broken_uptime(["Break Armour equal to 10% of Physical Damage dealt"], stats)
    assert low[2] < 0.9 and "71%" in low[1]
    # 50% of the whole hit (2000) x 2/s: under 1 s to break
    assert armour_broken_uptime(["Break Armour equal to 50% of Hit Damage dealt"], stats)[2] >= 0.9
    # Spell crits only: an Attack build has none to give
    assert armour_broken_uptime(["Break Armour on Critical Hit with Spells equal to 12% of Physical Damage dealt"],
                                stats) == (0.0, "")
    for line in ("Inflicts Elemental Exposure when this Weapon Fully Breaks Armour", "You can Break Enemy Armour to below 0"):
        assert armour_broken_uptime([line], stats) == (0.0, "")  # (not sources; both turned the box on before)
    assert armour_broken_uptime(["Minions Break Armour equal to (2-4)% of Physical damage dealt"], stats) == (0.0, "")
    assert armour_broken_uptime(["Shield Skills fully Break Armour when they Heavy Stun targets"], stats) == (0.0, "")
    assert armour_broken_uptime(["Hits Break 600 Armour"], stats)[2] >= 0.9  # 1200/s
    # a support's stat, as its line (BREAK_STATS): Chaos damage as break, none here
    assert armour_broken_uptime(["Break Armour equal to 50% of Chaos Damage dealt"], stats)[2] < 0.9


def test_exposure_on_only_when_its_events_keep_it_up():
    """KeywordPopups Exposure: 4 s by default; the box on at 90% uptime (MECH28; any inflicting line turned it on)."""
    from builder.search.conditions import _exposure_uptime

    fire, cold, lightning = (_exposure_uptime(e) for e in ("Fire", "Cold", "Lightning"))
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 5, "ShockChancePerHit": 50, "IgniteChancePerHit": 0,
             "_main_types": {"Attack", "Melee"}}
    hit = "Inflict Elemental Exposure on Hit, lowering Total Elemental Resistances by 25%"
    assert fire([hit], stats)[2] >= 0.9 and cold([hit], stats)[2] >= 0.9
    # 10% x 2 hits/s x 4 s: 1 - 0.9^8 = 57%
    devotion = lightning(["10% chance to inflict Lightning Exposure on Hit if you have at least 150 Devotion"], stats)
    assert devotion[2] < 0.9 and "57%" in devotion[1]
    assert fire(["Inflict Fire Exposure on Shocking an Enemy"], stats)[2] >= 0.9  # 1 Shock/s
    assert cold(["Inflict Cold Exposure on Igniting an Enemy"], stats)[2] < 0.9  # no Ignites
    assert lightning(["Inflict Lightning Exposure on Critical Hit"], stats)[2] < 0.9  # 0.1 crits/s
    assert cold(["Inflict Fire Exposure on Shocking an Enemy"], stats) == (0.0, "")
    # a Full Break every 12 s of a 12/13 share: one per 13 s, 4 s each
    breaks = "Inflicts Elemental Exposure when this Weapon Fully Breaks Armour"
    assert fire([breaks], {**stats, "_full_break_rate": 1 / 13})[2] < 0.9
    assert fire([breaks], stats)[2] < 0.9
    front = "Inflict Elemental Exposure to Enemies 3 metres in front of you"
    assert fire([front], stats)[2] >= 0.9  # a melee build stands close
    assert fire([front], {**stats, "_main_types": {"Spell"}})[2] < 0.9


def test_parried_at_the_share_of_hits_the_build_parries():
    """Parry (the user, 11 Nov: attack all the time, parry only telegraphed hits, one every 5 s assumed): each parry loses
    its 0.75 s retaliation (15%), Parried 2 s from half-way through it, so (2 - 0.375) / 4.25 of the hits gain:
    x(1 + 0.5 x 0.382) x 0.85 = 1.01; Refutation: 4 s buff + 9 s cooldown, Parried (4 - 0.35 + 2) / 13 of the fight, 0.65 s cast lost
    (MECH39: before, nothing set the box, so Parried counted 0)."""
    from builder.search import models
    from builder.search.conditions import CONDITIONS, _parried
    from builder.search.play_guide import KINDS, PLAYER

    parry = models.Skill("Parry", attack=True, cast_time=1.0)
    refutation = models.Skill("Refutation", spell=True, cast_time=0.65, cooldown=9.0)
    stats = {"enemySkillTime": 0.7, "Ward": 100}
    plan = models.parry_plan([parry], stats, True)
    assert abs(plan.box - 1.625 / 4.25) < 1e-6 and abs(plan.lost - 0.15) < 1e-6 and "telegraphed" in plan.how
    assert models.parry_plan([parry], {**stats, "enemySkillTime": 0.3}, True) == plan  # not the boss's every hit
    # consuming Parried at 2 hits/s: one of the 8.5 hits between parries gains, x(1 + 0.5 / 8.5) x 0.85 = 0.90
    eats = models.parry_plan([parry], {**stats, "Speed": 2.0, "HitChance": 100}, True, consumes=True)
    assert eats.box == 0 and "not worth" in eats.how
    both = models.parry_plan([parry, refutation], stats, True)
    assert abs(both.box - 5.65 / 13) < 1e-6 and abs(both.lost - 0.65 / 13) < 1e-6 and "Refutation" in both.how
    assert models.parry_plan([refutation], {**stats, "Ward": 0}, True).box == 0
    assert models.parry_plan([refutation], stats, False).box == 0  # a spell main skill gains nothing
    assert models.parry_plan([models.Skill("Raise Shield", attack=True)], stats, True).how == ""  # (Bastion: the ledger)
    # a main skill consuming Parried at 2 hits/s: Refutation's 3.65 s of buff x min(2, 1/0.7) + 1 hits of 26 gain
    eaten = models.parry_plan([refutation], {**stats, "Speed": 2.0, "HitChance": 100}, True, consumes=True)
    assert abs(eaten.box - (3.65 / 0.7 + 1) / 26) < 1e-6 and "consumes" in eaten.how
    on, basis, up = _parried([], {"_parry_plan": both})
    assert on and abs(up - 5.65 / 13) < 1e-6 and "Refutation" in basis
    assert _parried([], {}) == (0.0, "")
    mods, notes = models.parry_model([parry, refutation], stats, models.Skill("Strike", attack=True))
    assert len(mods) == 1 and abs(mods[0]["scale"] + 100 * 0.65 / 13) < 0.01 and notes
    assert "parryActive" in {c.var for c in CONDITIONS} and KINDS["parryActive"][0] == PLAYER


def test_frozen_and_electrocuted_at_the_share_the_boss_carries_them():
    """KeywordPopups: Freeze 4 s, Electrocution 5 s by default; one per bar refill, x6 threshold for 14 / 15 s after each
    (MECH38: before, nothing set either box, so their damage-taken lines counted 0)."""
    from builder.search.conditions import CONDITIONS, _immobilised

    frozen, electrocuted = _immobilised("Freeze", "Frozen"), _immobilised("Electrocute", "Electrocuted")
    stats = {"Speed": 2.0, "HitChance": 100, "FreezeBuildupAvg": 10, "ElectrocuteBuildupAvg": 10}
    # 2 hits/s x 10% x 9.38/12.35: full in 6.58 s; 4 s Frozen, then 10 s at x6 and the rest: one per 18.9 s
    on, basis, up = frozen([], stats)
    assert on and abs(up - 4 / 18.92) < 0.005 and "21%" in basis
    assert abs(electrocuted([], stats)[2] - 5 / 19.92) < 0.005
    assert abs(frozen([], {**stats, "FreezeDurationMod": 1.5})[2] - 6 / 19.25) < 0.005
    assert frozen([], {**stats, "FreezeBuildupAvg": 0}) == (0.0, "")
    assert electrocuted([], {**stats, "Speed": 0}) == (0.0, "")
    assert {"conditionEnemyFrozen", "conditionEnemyElectrocuted"} <= {c.var for c in CONDITIONS}


def test_incision_stacks_from_its_sources_removed_by_each_bleed():
    """KeywordPopups Incision: 10% Bleed chance per stack, up to 10, all removed when Bleeding is inflicted (MECH38:
    before, nothing set the stack count, so Thousand Cuts and the support's consumed count were 0)."""
    from builder.search.conditions import _incision_consumed, _incision_stacks, incision_state

    stats = {"Speed": 2.0, "HitChance": 100, "BleedChancePerHit": 100, "_incision_per_hit": 1, "_main_types": {"Attack"}}
    avg, consumed, how = incision_state([], stats)
    assert abs(avg - 1) < 1e-9 and abs(consumed - 2) < 1e-9  # every hit bleeds: its one stack, eaten by the next hit
    assert _incision_consumed([], stats)[0] == 8  # 2/s over 4 s
    no_bleed = {**stats, "BleedChancePerHit": 0}
    avg0 = incision_state([], no_bleed)[0]
    assert 2 < avg0 < 10  # stacks build until their own 10% each bleeds the boss
    assert incision_state(["100% increased effect of Incision"], no_bleed)[0] < avg0
    assert _incision_stacks([], no_bleed)[0] == round(avg0)
    line = "10% chance for Attack Hits to apply ten Incision"
    attack_only = {**no_bleed, "_incision_per_hit": 0}
    assert incision_state([line], attack_only)[0] > 0
    assert incision_state([line], {**attack_only, "_main_types": {"Spell"}}) == (0.0, 0.0, "")
    assert _incision_consumed([line], attack_only) == (0, "")  # (only the support reads it)


def test_arcane_surge_on_only_when_its_sources_keep_it_up():
    """KeywordPopups ArcaneSurgeDuration: 4 s by default; the box on at 90% uptime (MECH29; any gaining line turned it on)."""
    from builder.search.conditions import arcane_surge_uptime

    spell = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "Mana": 1000, "ManaPerSecondCost": 50,
             "_main_types": {"Spell"}}
    # 10% x 0.2 crits/s x 4 s: 1 - 0.9^0.8 = 8%
    crit = arcane_surge_uptime(["10% chance to Gain Arcane Surge when you deal a Critical Hit"], spell)
    assert crit[2] < 0.9 and "8%" in crit[1]
    minion = arcane_surge_uptime(["Gain Arcane Surge when a Minion Dies"], spell)
    assert minion[2] < 0.9 and "isn't modelled" in minion[1]
    assert arcane_surge_uptime(["You have Arcane Surge"], spell)[2] >= 0.9
    assert arcane_surge_uptime(["15% increased Spell Damage while you have Arcane Surge"], spell) == (0.0, "")
    devotion = "Gain Arcane Surge on Hit with Spells if you have at least 150 Devotion"
    assert arcane_surge_uptime([devotion], {**spell, "Devotion": 150})[2] >= 0.9
    assert arcane_surge_uptime([devotion], {**spell, "Devotion": 100})[2] < 0.9
    assert arcane_surge_uptime([devotion], {**spell, "Devotion": 150, "_main_types": {"Attack"}}) == (0.0, "")
    # the support: 100% of 1000 Mana in 20 s at 50/s, 10 s each. Whether Mana spent during the buff counts is unsettled
    # (RESEARCH1, LOW): the headline takes the lower bound, 10 / (10 + 20) = 33%; the running counter's 50% is the
    # play guide's other row
    support = {**spell, "_arcane_surge_support": (100, 10.0)}
    low = arcane_surge_uptime([], support)
    assert abs(low[2] - 1 / 3) < 1e-9 and "33%" in low[1] and "50% if it counts" in low[1]
    assert low[3] is False and abs(low[4][0] - 0.5) < 1e-9 and "unsettled" in low[4][1]
    # at 100/s (10 s to spend it): 50% in the headline, not 100% (guides report 70-85% for fast casters)
    fast = arcane_surge_uptime([], {**support, "ManaPerSecondCost": 100})
    assert abs(fast[2] - 0.5) < 1e-9 and fast[4][0] == 1.0
    # sources without the counter give no other reading
    assert len(arcane_surge_uptime(["You have Arcane Surge"], spell)) == 3


def test_debuffs_on_only_when_their_sources_keep_them_up():
    """KeywordPopups Blind / Maim / Hinder: 4 s; Intimidate, Ash, Frost 4 s assumed; on at 90% uptime (MECH29; any
    inflicting line turned them on)."""
    from builder.search.conditions import debuff_uptime

    intimidated, hindered, blinded = (debuff_uptime(f"conditionEnemy{d}") for d in ("Intimidated", "Hindered", "Blinded"))
    ash = debuff_uptime("conditionEnemyCoveredInAsh")
    stats = {"Speed": 2.0, "HitChance": 100, "CritChance": 10, "enemySkillTime": 1.0, "EvadeChance": 0,
             "EffectiveBlockChance": 25, "PresenceRadius": 40, "_main_types": {"Attack", "Melee"}}
    # 30% x 2 hits/s x 4 s: 1 - 0.7^8 = 94%
    assert intimidated(["30% chance to Intimidate Enemies for 4 seconds on Hit"], stats)[2] >= 0.9
    low = intimidated(["10% chance to Intimidate Enemies for 4 seconds on Hit"], stats)
    assert low[2] < 0.9 and "57%" in low[1]
    assert intimidated(["Permanently Intimidate enemies on Block"], stats)[2] >= 0.9
    assert intimidated(["Permanently Intimidate enemies on Block"], {**stats, "EffectiveBlockChance": 0})[2] < 0.9
    # 25% Blocked x 1 hit/s x 8 s: 2 Blocks in 8 s, up throughout
    assert intimidated(["Intimidate Enemies on Block for 8 seconds"], stats)[2] >= 0.9
    assert intimidated(["Enemies in your Presence are Intimidated"], stats)[2] >= 0.9  # melee: inside 4 m
    declaration = "Supports Persistent Buff Skills, causing enemies that enter your Presence to become Intimidated"
    assert intimidated([declaration], {**stats, "PresenceRadius": 0})[2] < 0.9
    assert intimidated(["Enemies you Curse are Intimidated"], stats)[2] < 0.9
    assert intimidated(["Enemies you Curse are Intimidated"], {**stats, "_curse_uses": [models.CurseUse("Vulnerability", 1.0, "cast by hand")]})[2] >= 0.9
    stun = intimidated(["Enemies you Heavy Stun while Shapeshifted are Intimidated for 6 seconds"], stats)
    assert stun[2] < 0.9 and "isn't modelled" in stun[1]
    assert intimidated(["20% more Damage against Intimidated Enemies"], stats) == (0.0, "")
    assert hindered(["10% chance to Hinder Enemies on Hit with Spells"], stats) == (0.0, "")  # not a Spell
    assert hindered(["10% chance to Hinder Enemies on Hit with Spells"], {**stats, "_main_types": {"Spell"}})[2] < 0.9
    assert hindered(["20% increased Hinder Duration"], stats) == (0.0, "")
    # hinder_duration_+% (the user, 11 Nov: count it): 10% x 2 hits/s x 8 s = 1 - 0.9^16 = 81%, from 57%
    spell = {**stats, "_main_types": {"Spell"}}
    longer = hindered(["10% chance to Hinder Enemies on Hit with Spells", "100% increased Hinder Duration"], spell)
    assert "81%" in longer[1] and "8 s" in longer[1]
    assert "57%" in blinded(["10% chance to Blind Enemies on Hit", "50% reduced Blind duration", "50% increased Blind duration"], stats)[1]
    assert blinded(["(30-50)% increased Blind Effect"], stats) == (0.0, "")
    # a Full Break every 13 s, Maimed 4 s of it
    maimed = debuff_uptime("conditionEnemyMaimed")
    assert maimed(["Enemies you Fully Armour Break are Maimed"], {**stats, "_full_break_rate": 1 / 13})[2] < 0.9
    assert maimed(["Enemies you Fully Armour Break are Maimed"], {**stats, "_full_break_rate": 0.5})[2] >= 0.9
    assert ash(["Cover Enemies in Ash on Hit"], stats)[2] >= 0.9
    assert debuff_uptime("conditionEnemyCoveredInFrost")(["Cover Enemies in Ash on Hit"], stats) == (0.0, "")


def test_armour_break_duration_lengthens_full_breaks():
    """armour_break_and_sundered_armour_duration_+% read as lengthening KeywordPopups ArmourBreak's 12 s Fully Broken."""
    from builder.search.conditions import _armour_broken

    assert models.full_break_duration(["50% increased Armour Break Duration"]) == 18.0
    assert models.full_break_duration(["20% reduced Armour Break Duration", "Breaks 100 Armour on Hit"]) == pytest.approx(9.6)
    assert models.duration_scale(["200% reduced Hinder Duration"], "Hinder") == 0.0
    stats = {"Speed": 1.0, "HitChance": 100, "CritChance": 0, "_main_types": {"Attack"}, "_enemy_armour": 1200}
    base = _armour_broken(["Break 100 Armour on Hit"], stats)  # 12 s to break, 12 s Broken: 50%
    longer = _armour_broken(["Break 100 Armour on Hit", "100% increased Armour Break Duration"], stats)  # 24 / 36
    assert base[0] == pytest.approx(0.5) and longer[0] == pytest.approx(2 / 3) and "24 s each" in longer[1]


def test_an_uptime_is_counted_at_its_share():
    """The user, 9 Nov 2026: "Count the real share" (was on at 90%, else off): on from MIN_UPTIME, weighed by its uptime."""
    from builder.search.conditions import CONDITIONS, FULL_UPTIME, MIN_UPTIME, _box, shared

    assert _box(0.5, "b") == (True, "b", 0.5) and _box(MIN_UPTIME / 2, "b")[0] is False
    assert shared(FULL_UPTIME) == "counted as always on" and shared(0.5) == "counted at that share"
    shocked = next(c for c in CONDITIONS if c.var == "conditionEnemyShocked")
    stats = {"ShockChancePerHit": 5, "Speed": 1.0, "HitChance": 100}  # 1 - 0.95^8 = 34%
    assert shocked.stat(stats) and shocked.uptime_fn(stats) == pytest.approx(1 - 0.95 ** 8)


def test_floors_are_checked_without_a_partial_effect():
    ctx = context(main_skill="Ice Shot", floors={"life": 2000, "resists": {}})
    objective = Objective(ctx)
    base = {"CombinedDPS": 100000, "Life": 2000, "Str": 50, "ReqStr": 40, "Spirit": 100, "SpiritUnreserved": 0}
    full = objective.score(base)
    objective.floor_deltas = [{"Life": -500.0}, {"CombinedDPS": -1000.0}]
    assert [label for label, _, _ in objective.deficits(base)] != [] and objective.score(base) < full
    objective.floor_deltas, objective.uptime_factor = [], 0.5
    assert objective.dps(base) == pytest.approx(50000)


def test_full_dps_never_counts_pounce_s_own_hit_as_used_nonstop():
    """include_in_full_dps (MECH36): a damaging group joins Full DPS where it raises the goal, but a Pounce group only
    once the Mark model has scaled its slam to its real use (once per Mark, about every 8 s, where Full DPS would count
    it as nonstop); until then a seed's flag on it comes off and the note says so."""
    from builder.search.inner import Search

    class Objective:
        entry_scales: dict = {}

        def value(self, stats):
            return sum(g["dps"] for g in groups if g["fullDPS"] or g["main"])

    class Build:
        objective, stats = Objective(), {}

        def groups(self):
            return groups

        def set_group(self, index, fullDPS):
            groups[index - 1]["fullDPS"] = fullDPS

    def group(index, names, dps, full=False, main=False):
        return {"index": index, "main": main, "fromItem": False, "enabled": True, "fullDPS": full, "dps": dps,
                "gems": [{"name": n} for n in names]}

    groups = [group(1, ["Spark"], 100, main=True), group(2, ["Pounce", "Sniper's Mark"], 50, full=True),
              group(3, ["Pounce"], 40), group(4, ["Comet"], 30)]
    class Assumptions:  # (no pressed groups to rate here: MECH37's own tests cover that)
        def rate_presses(self):
            return []

    search = Search.__new__(Search)
    search.build, search.play_notes, search.assumptions = Build(), {}, Assumptions()
    search.include_in_full_dps()
    assert [g["fullDPS"] for g in groups] == [False, False, False, True]
    assert "Pounce" in search.play_notes["pounce_hit"]["label"]
    # once the Mark model has scaled its slam's entry to its real rate (and brought in its wolves), it joins
    Build.objective.entry_scales = {"Pounce": 0.625, "Summon Wolf": 6}
    search.include_in_full_dps()
    assert [g["fullDPS"] for g in groups] == [False, True, True, True] and "pounce_hit" not in search.play_notes


def test_a_charge_or_rage_spender_outside_the_main_group_is_used_at_its_own_press_rate():
    """MECHC1 (FLAT_RATES A4), kept on MECH48's implementation (skill_use_rate, Conditions._group_uses): on cooldown,
    else once per duration, else once per PRESS_EVERY s (LOW); a support takes its group's rate; a disabled group none."""
    from types import SimpleNamespace

    from builder.search.conditions import Conditions, skill_use_rate
    from builder.search.models import PRESS_EVERY

    assert skill_use_rate({"cooldown": 8.0, "speed": 2.0})[:2] == (0.125, "its 8.00 s cooldown")
    assert skill_use_rate({"duration": 5.0})[0] == 0.2
    assert skill_use_rate({"speed": 1.0}) == (1 / PRESS_EVERY, f"once every {PRESS_EVERY:g} s (LOW: no cooldown or duration)", True)

    groups = [{"index": 1, "gems": [{"name": "Spark"}]},
              {"index": 2, "gems": [{"name": "Furious Slam"}, {"name": "Rageforged I"}]},
              {"index": 3, "enabled": False, "gems": [{"name": "Molten Crash"}]}]

    class Build:
        def call(self, method, **kwargs):
            assert method == "useRates" and kwargs["indices"] == [2]
            return {"rates": [{"index": 2, "cooldown": 5.0, "speed": 1.2}]}

    conditions = SimpleNamespace(build=Build())
    rates = Conditions._group_uses(conditions, groups, {"Furious Slam", "Rageforged I", "Molten Crash"})
    assert rates == {"Furious Slam": 0.2, "Rageforged I": 0.2}  # Molten Crash's group is off: not used
