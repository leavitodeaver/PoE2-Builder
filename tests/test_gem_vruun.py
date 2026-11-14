"""GEM7: Vruun's Inevitability's area per Combo expended (ledger supports-gem7.yaml; models.vruun_combo_share)."""

from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill

ENTRY = "support-vruuns-inevitability-area"
VRUUN = {"area_of_effect_+%_per_combo_stack": 6, "skill_maximum_number_of_combo_stacks": 10}


def _lines(mods: list[dict]) -> list[tuple[str, float, str | None]]:
    return [(m["line"], m["scale"], m.get("skill")) for m in mods if m.get("entry") == ENTRY]


def _quake(group: int = 1) -> Skill:
    return Skill("Earthquake", group, attack=True, melee=True, support_stats=dict(VRUUN))


def test_other_strike_groups_build_the_combo_and_slams_do_not():
    quake = _quake()
    strike = Skill("Boneshatter", 2, attack=True, melee=True)
    slam = Skill("Rolling Slam", 3, attack=True, melee=True)  # (a Slam: not a Strike, builds no Combo)
    skills = [quake, strike, slam]
    assert models.gem_stat_side_groups(skills, quake) == [2]
    groups = {2: {"index": 2, "name": "Boneshatter", "speed": 2.0, "HitChance": 90, "_press": 0.5}}
    mods, notes = models.gem_stat_model(skills, stats={"Speed": 1.0}, main=quake, groups=groups)
    # 2 uses/s x 90% hit x 0.5 pressed = 0.9 Combo per Earthquake use: +5.4% area
    assert _lines(mods) == [("1% increased Area of Effect", pytest.approx(5.4), "Earthquake")]
    note = next(n for n in notes if n.get("entry") == ENTRY)
    assert "LOW" in note["basis"] and "Without the other Strikes" in note["play"]


def test_no_strike_gives_no_area_with_a_note():
    quake = _quake()
    skills = [quake, Skill("Rolling Slam", 3, attack=True, melee=True)]
    assert models.gem_stat_side_groups(skills, quake) == []
    mods, notes = models.gem_stat_model(skills, stats={"Speed": 1.0}, main=quake)
    assert _lines(mods) == []
    note = next(n for n in notes if n.get("entry") == ENTRY)
    assert note["value"] == "not counted" and "no other melee Strike" in note["basis"]


def test_the_combo_is_capped_at_ten_and_an_unpressed_builder_is_flagged():
    quake = _quake()
    skills = [quake, Skill("Boneshatter", 2, attack=True, melee=True)]
    groups = {2: {"index": 2, "name": "Boneshatter", "speed": 30.0, "HitChance": 90}}
    mods, notes = models.gem_stat_model(skills, stats={"Speed": 1.0}, main=quake, groups=groups)
    assert _lines(mods) == [("1% increased Area of Effect", pytest.approx(60), "Earthquake")]
    assert "overcount of Combo" in next(n for n in notes if n.get("entry") == ENTRY)["basis"]


def test_a_side_vruun_skill_reads_the_main_strike_and_its_own_press():
    strike = Skill("Boneshatter", 1, attack=True, melee=True)
    quake = _quake(2)
    skills = [strike, quake]
    assert models.gem_stat_side_groups(skills, strike) == [2]  # (its own group: the main one is read from stats)
    groups = {2: {"index": 2, "name": "Earthquake", "speed": 1.0, "_press": 0.5}}
    mods, _ = models.gem_stat_model(skills, stats={"Speed": 1.5, "HitChance": 100}, main=strike, groups=groups)
    # 1.5 hits/s over 0.5 uses/s = 3 Combo: +18% area
    assert _lines(mods) == [("1% increased Area of Effect", pytest.approx(18), "Earthquake")]
