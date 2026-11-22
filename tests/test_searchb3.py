"""SEARCHB3: the 0.0 trap (the manager's ruling, 22 Nov 05:35: "a build with 0 DPS must never outscore one with damage,
in every step that compares scores"). A 0-DPS build meeting every floor scored log1p(0) - 0 = 0.0, above a damaging build
short on resistances (273 DPS, penalty 7.88: -2.27; 2b75b, POE2T6262-6264)."""
from __future__ import annotations

import math

from builder.search.objective import NO_DAMAGE_OFFSET, Objective, rank_key


def _objective():
    objective = Objective.__new__(Objective)
    objective.value = lambda stats: stats["value"]
    objective.penalty = lambda stats: stats["penalty"]
    return objective


def test_a_build_with_no_damage_never_outscores_one_with_damage():
    objective = _objective()
    damaging = objective.score({"value": 273.26, "penalty": 7.88})  # (2b75b after the Helmet, POE2T6264)
    no_damage = objective.score({"value": 0.0, "penalty": 0})  # (after the resistance rare replaced The Covenant)
    assert damaging == math.log1p(273.26) - 7.88 and damaging > no_damage
    # Even the worst penalty the weights reach (64 x 15 a unit, several hard deficits) stays above a 0-DPS build
    assert objective.score({"value": 1e-6, "penalty": 64 * 15 * 50 + 8}) > objective.score({"value": 0.0, "penalty": 0})
    assert no_damage == -NO_DAMAGE_OFFSET


def test_builds_with_no_damage_still_rank_by_their_penalty_and_nan_counts_as_none():
    objective = _objective()
    assert objective.score({"value": 0.0, "penalty": 1.0}) > objective.score({"value": 0.0, "penalty": 5.0})
    assert objective.score({"value": float("nan"), "penalty": 0.0}) == -NO_DAMAGE_OFFSET


def test_the_delivered_ranking_still_puts_the_floors_first():
    """The fix is search-time only: the delivered results rank by the shortfall, then the value (rank_key)."""
    short = {"deficits": [{"label": "Cold resistance", "amount": 0.2, "hard": False}], "dps": 273.0, "value": 273.0}
    met = {"deficits": [], "dps": 100.0, "value": 100.0}
    assert sorted([short, met], key=rank_key)[0] is met
