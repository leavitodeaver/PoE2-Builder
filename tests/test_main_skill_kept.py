"""Gems.choose_active_skill on Full DPS keeps the skill the search chose on its own DPS (SEARCH8, Defect B: gate 2b75b
delivered Explosive Transmutation's buff and a support's Living Lightning as the main skill, since Full DPS counts every
skill whichever is calculated). No engine: a stub build whose score favours the buff."""

from __future__ import annotations

import types

from builder.search.gems import Gems

GROUP = {"index": 1, "mainActiveSkill": 2, "skills": [
    {"name": "Explosive Transmutation", "parts": [], "statSets": []},
    {"name": "Transmutation Explosion", "parts": [], "statSets": ["Explosion", "Big Explosion"]},
    {"name": "Living Lightning", "parts": [], "statSets": []}]}


class StubBuild:
    def __init__(self, full: bool, dps: float = 100.0):
        self.objective = types.SimpleNamespace(full=full)
        self.stats = {"CombinedDPS": dps, "FullDPS": 1000.0}
        self.score, self.tried = 0.0, []

    def main_group(self):
        return GROUP

    def set_group(self, index, mainActiveSkill, skillPart=None, statSet=None):
        self.tried.append((mainActiveSkill, statSet))
        # (the buff scores a hair higher on Full DPS)
        self.score = {1: 7.01, 2: 7.0, 3: 6.9}[mainActiveSkill] + (0.001 if statSet == 2 else 0.0)


def _gems(build):
    gems = Gems.__new__(Gems)
    gems.build, gems.assumptions, gems.conditions = build, None, None
    return gems


def test_full_dps_keeps_the_searched_main_skill_and_chooses_its_stat_set():
    build = StubBuild(full=True)
    _gems(build).choose_active_skill(settle=False)
    assert {skill for skill, _ in build.tried} == {2}
    assert build.tried[-1] == (2, 2)


def test_the_main_skills_own_dps_chooses_freely():
    build = StubBuild(full=False)
    _gems(build).choose_active_skill(settle=False)
    assert {skill for skill, _ in build.tried} == {1, 2, 3}
    assert build.tried[-1] == (1, None)


def test_a_calculated_skill_with_no_dps_of_its_own_may_change_on_full_dps():
    build = StubBuild(full=True, dps=0.0)
    _gems(build).choose_active_skill(settle=False)
    assert build.tried[-1] == (1, None)
