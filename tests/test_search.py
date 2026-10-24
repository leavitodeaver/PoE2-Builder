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
from builder.search.gear import SlotSpec
from builder.search.skeletons import (
    DUAL_WIELD, enumerate_skeletons, offhand_types, offhand_weapon_types, swap_skills,
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


def test_dual_wield_skeletons_follow_pobs_offhand_rules():
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon")
    skeletons, _ = enumerate_skeletons(ctx, ["Explosive Spear"])
    assert [(s.weapon, s.offhand) for s in skeletons] == [("Spear", DUAL_WIELD)]
    assert skeletons[0].label.endswith("Spear + off-hand weapon")
    # A Sceptre goes beside any one-handed weapon; other one-handers not beside a Wand or Sceptre,
    # and a Spear never goes in the off hand
    assert offhand_weapon_types(ctx, "Spear") == ["Claw", "Dagger", "Flail", "One Hand Axe", "One Hand Mace",
                                                  "One Hand Sword", "Sceptre"]
    assert offhand_weapon_types(ctx, "Wand") == ["Sceptre"]
    assert offhand_weapon_types(ctx, "Bow") == []
    banned = context(main_skill="Explosive Spear", offhand="weapon", forbid={"item_classes": ["Sceptre", "Dagger"]})
    assert "Sceptre" not in offhand_weapon_types(banned, "Spear") and "Dagger" not in offhand_weapon_types(banned, "Spear")


def test_offhand_any_includes_dual_wielding_and_two_handers_explain_why_not():
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"])
    skeletons, _ = enumerate_skeletons(ctx, ["Explosive Spear"])
    assert {s.offhand for s in skeletons} == {"Shield", "Focus", DUAL_WIELD}
    ctx = context(main_skill="Ice Shot", ascendancies=["Deadeye"], offhand="weapon")
    skeletons, problems = enumerate_skeletons(ctx, ["Ice Shot"])
    assert skeletons == [] and any("dual wielded" in p for p in problems)


def test_offhand_weapon_slot_offers_every_legal_type():
    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon")
    types = offhand_types(ctx, "Spear", DUAL_WIELD)
    spec = SlotSpec(types[0], None, types[1:])
    assert set(spec.types) == set(offhand_weapon_types(ctx, "Spear"))
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
