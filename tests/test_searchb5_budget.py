"""THE USER (22 Nov ~23:50, the manager's 23:55 ruling): "don't put any caps on budget, if I want to spend 1600div on a
build, the tool should build for that. if I don't put a number in for the budget, it should make me the best possible
build without looking at costs". No per-item, per-slot or tier cap: one item may take the whole budget; no number = costs
ignored (the best rares); SSF keeps its own tier (no prices)."""
from __future__ import annotations

import math
from types import SimpleNamespace

from builder.request.schema import Budget, GearTier
from builder.search.context import Context
from builder.search.rares import BUY_RULES, TIER_RULES, rare_rules_tier


def ctx(**budget):
    request = SimpleNamespace(budget=Budget(**budget))
    return SimpleNamespace(request=request, ssf=request.budget.ssf, budget=Context.budget.fget(SimpleNamespace(
        request=request, ssf=request.budget.ssf)))


def price_cap(**budget) -> float:
    return Context.price_cap.func(ctx(**budget))


def test_one_item_may_take_the_whole_budget_and_no_number_has_no_cap():
    assert price_cap(divines=1600) == 1600  # (the old ENDGAME tier held every item to 300)
    assert price_cap(divines=1600, gear_tier="league_start") == 1600  # (no tier cap at all)
    assert price_cap() == math.inf
    assert price_cap(ssf=True, divines=50) == math.inf  # (SSF: no prices)


def test_a_lineage_support_over_300_divines_is_buyable_within_the_budget():
    gems = {"Her Declaration": {"lineage": True}}
    for budget, expected in (({"divines": 400}, (339.0, "exchange")), ({}, (339.0, "exchange")),
                             ({"divines": 300}, None)):
        c = ctx(**budget)
        c.gems, c.gem_prices, c.price_cap = gems, {"Her Declaration": 339.0}, Context.price_cap.func(c)
        assert Context.gem_price(c, "Her Declaration") == expected, budget


def test_the_rares_row_follows_the_budget_not_a_tier():
    assert rare_rules_tier(Budget()) == GearTier.MIRROR  # no number: the best rares, costs ignored
    assert rare_rules_tier(Budget(gear_tier="league_start")) == GearTier.MIRROR
    assert rare_rules_tier(Budget(divines=150)) == GearTier.ENDGAME  # a number: what the market sells
    assert rare_rules_tier(Budget(divines=150, gear_tier="league_start")) == GearTier.ENDGAME
    assert rare_rules_tier(Budget(ssf=True, gear_tier="league_start")) == GearTier.LEAGUE_START  # SSF unchanged
    assert rare_rules_tier({"divines": None, "gear_tier": "mid"}) == GearTier.MIRROR  # (the shopping list's dict)
    # The budget row is market availability: at most 4 tier-1 affixes on a bought rare, ~70% rolls (the price model
    # prices tier counts, not rolls)
    assert BUY_RULES[GearTier.ENDGAME] == 4 and TIER_RULES[GearTier.ENDGAME][2] == 0.7
    assert BUY_RULES[GearTier.MIRROR] is None and TIER_RULES[GearTier.MIRROR] == (86, 1, 1.0)
