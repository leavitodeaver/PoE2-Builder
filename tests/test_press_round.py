"""The search picks the press rate of a side skill no cooldown or duration sets (SEARCH10; MECH55, the user's "search
picks the rate", 12 Nov): Search.press_round, Search.press_sync, Objective.press_busy. Fake engine, no PoB."""
from __future__ import annotations

import math
from types import SimpleNamespace

from builder.search.inner import PRESS_RATES, Search
from builder.search.objective import Objective


MARK, BOLT, MAIN = "Sniper's Mark", "Lightning Bolt", "Spark"


class FakeObjective:
    """Scores a stat dict's dps with the press charge, as Objective.dps does outside Full DPS."""
    full = False

    def __init__(self):
        self.press_choices: dict[str, float] = {}
        self.press_busy = 0.0

    def dps(self, stats):
        return stats["CombinedDPS"] * (1.0 - self.press_busy)

    def score(self, stats):
        return math.log1p(self.dps(stats))


class FakeBuild:
    """Group 1: the main skill. Group 2: a Mark with no cooldown or duration (its benefit grows with its presses up to 1/s,
    a side effect; 10 uses/s). Group 3: a skill on a 2 s cooldown (not a choice)."""

    def __init__(self, gain: float = 0.3):
        self.objective = FakeObjective()
        self.ctx = SimpleNamespace(gems={})
        self.gain = gain
        self.groups_list = [
            {"index": 1, "main": True, "enabled": True, "fullDPS": True, "gems": [{"name": MAIN, "support": False}]},
            {"index": 2, "main": False, "enabled": True, "fullDPS": False, "gems": [{"name": MARK, "support": False}]},
            {"index": 3, "main": False, "enabled": True, "fullDPS": False, "gems": [{"name": BOLT, "support": False}]},
        ]
        self.stats = {}
        self.recalc()
        self.score = self.objective.score(self.stats)

    def recalc(self):
        rate = self.objective.press_choices.get(MARK, 0.0)
        self.stats = {"CombinedDPS": 100.0 * (1.0 + self.gain * min(rate, 1.0))}

    def groups(self):
        return self.groups_list

    def call(self, name, **kw):
        if name == "groups":
            return {"mainSocketGroup": 1, "groups": self.groups_list}
        if name == "useRates":
            rows = {2: {"index": 2, "name": MARK, "speed": 10.0}, 3: {"index": 3, "name": BOLT, "speed": 2.0, "cooldown": 2.0}}
            return {"rates": [rows[i] for i in kw["indices"] if i in rows]}
        raise AssertionError(name)


def fake_search(build: FakeBuild) -> Search:
    search = Search.__new__(Search)
    search.build = build
    search.press_rows = {}
    present = lambda: [n["name"] for g in build.groups_list for n in g["gems"] if g["enabled"]]  # noqa: E731
    search.gems = SimpleNamespace(buttons=lambda: present())
    search.conditions = SimpleNamespace(resolve=lambda: False)
    search.assumptions = SimpleNamespace(apply=lambda: build.recalc() or False)
    search.follow_uptimes = lambda: None
    return search


def test_the_search_picks_the_rate_that_pays_for_its_presses():
    build = FakeBuild()
    search = fake_search(build)
    assert set(search.press_candidates()) == {MARK}  # (Lightning Bolt's cooldown sets its use: not a choice)
    assert search.press_round(float("inf"))
    # 1/s: 130 x (1 - 1/10) = 117 beats 0 (100), 0.5/s (115 x 0.95) and 2/s (130 x 0.8)
    assert build.objective.press_choices == {MARK: 1.0}
    assert abs(build.objective.press_busy - 0.1) < 1e-9
    assert abs(build.objective.dps(build.stats) - 117.0) < 1e-9
    assert abs(build.score - math.log1p(117.0)) < 1e-9
    rows = search.press_guide_rows()
    assert len(rows) == 1 and rows[0]["var"] == f"press:{MARK}"
    assert abs(rows[0]["dps_with"] - 117.0) < 1e-9 and abs(rows[0]["dps_without"] - 100.0) < 1e-9


def test_a_press_that_buys_nothing_stays_at_zero():
    build = FakeBuild(gain=0.0)
    search = fake_search(build)
    assert not search.press_round(float("inf"))
    assert build.objective.press_choices == {} and build.objective.press_busy == 0.0
    assert search.press_guide_rows() == []
    assert PRESS_RATES[0] == 0.0  # (0 is offered first: a tie keeps it)


def test_a_skill_that_leaves_the_build_loses_its_rate_and_its_time():
    build = FakeBuild()
    search = fake_search(build)
    search.press_round(float("inf"))
    build.groups_list[1]["enabled"] = False
    assert search.press_sync()
    assert build.objective.press_choices == {} and build.objective.press_busy == 0.0


def test_the_score_charges_the_main_skill_for_uncharged_presses():
    objective = Objective.__new__(Objective)
    objective.base_full, objective.extra, objective.added_dps = False, False, 0.0
    objective.life_factor = objective.uptime_factor = 1.0
    objective.entry_scales, objective.press_scales = {}, {}
    objective.press_busy = 0.25
    assert abs(objective.dps({"CombinedDPS": 200.0}) - 150.0) < 1e-9
    # Full DPS: the main skill's part of it loses that share, the rest doesn't
    objective.base_full = True
    objective.ctx = SimpleNamespace()
    objective.on_death = set()
    stats = {"CombinedDPS": 200.0, "FullDPS": 300.0, "SkillDPS": []}
    assert abs(objective.dps(stats) - 250.0) < 1e-9
    objective.press_busy = 0.0
    assert abs(objective.dps(stats) - 300.0) < 1e-9


# Adaptive Skin's part-time share (SEARCH10, the manager's ruling): a ledger condition up for part of the fight counts at
# its share inside PoB's stats (Assumptions.wanted's hook), so the search weighs it at x1 and reads the floors without it.

def test_a_part_time_ledger_condition_is_switched_and_not_weighed_twice():
    from builder.search import inner

    build = SimpleNamespace(objective=SimpleNamespace(uptime_factor=1.0, floor_deltas=[], dps=lambda s: s["dps"],
                                                      score=lambda s: s["dps"]),
                            stats={"dps": 100.0, "MaxFireResist": 78.0}, score=100.0)
    applied = []

    def apply():
        on = build_assumptions.ledger_switch.get("BossHitsFire", True)
        build.stats = {"dps": 100.0, "MaxFireResist": 78.0 if on else 75.0}
        applied.append(on)
        return True

    build_assumptions = SimpleNamespace(ledger_part_time={"BossHitsFire": 0.6}, apply=apply)
    search = Search.__new__(Search)
    search.build, search.assumptions = build, build_assumptions
    search.conditions = SimpleNamespace(uptimes={}, active={})
    search.uptime_cache, search.uptime_floor = {}, {}
    effects = inner._part_time(search)
    var = f"{inner.LEDGER_VAR}BossHitsFire"
    assert effects[var][0] == 0.6 and effects[var][1]["in_stats"]
    inner._switch(search, {var: False})
    assert build_assumptions.ledger_switch == {"BossHitsFire": False}
    inner._switch(search, {var: True})
    search.follow_uptimes()
    # the share is already in PoB's stats: factor 1; the floors read it off (3 less maximum Fire Resistance)
    assert build.objective.uptime_factor == 1.0
    assert build.objective.floor_deltas == [{"MaxFireResist": -3.0}]
    assert build_assumptions.ledger_switch["BossHitsFire"] is True and applied[-1] is True
