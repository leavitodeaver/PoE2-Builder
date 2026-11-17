"""MECH84: Thorns hits' buildup (PATCH_CHECK L4-2) and the two COV38 downsides' pure parts (no PoB)."""
from __future__ import annotations

import pytest

from builder.search import conditions, models

ROW = {"PhysicalHitAverage": 300.0, "ColdHitAverage": 50.0, "HeavyStunBuildupAvg": 2.0, "FreezeBuildupAvg": 1.0}


def test_thorns_fills_are_its_hits_times_its_own_buildup_less_the_cold_share_for_stun():
    fills = models.thorns_fills(ROW, 0.5, "x")
    uncold = 1 - 50 / (300 * 1.5 + 50)
    assert fills["HeavyStun"] == pytest.approx(0.5 * 0.02 * uncold)
    assert fills["Freeze"] == pytest.approx(0.5 * 0.01) and "Pin" not in fills and "Thorns hits/s" in fills["how"]
    assert models.thorns_fills(ROW, 0.0, "x") == {} and models.thorns_fills(None, 1.0, "x") == {}


def test_thorns_buildup_heavy_stuns_and_freezes_the_boss_with_no_main_skill_buildup():
    fills = models.thorns_fills(ROW, 2.0, "Retaliation 2/s")
    bare = {"Speed": 1.0, "HitChance": 100.0, "PhysicalHitAverage": 100.0}
    assert models.heavy_stun_rate(bare)[0] == 0
    rate, how = models.heavy_stun_rate({**bare, "_thorns_fill": fills})
    assert rate > 0 and "Thorns" in how
    assert models.immobilised_share(bare, "Freeze")[0] == 0
    assert models.immobilised_share({**bare, "_thorns_fill": fills}, "Freeze")[0] > 0
    # beside the main skill's own buildup it adds
    own = {**bare, "HeavyStunBuildupAvg": 1.0}
    assert models.heavy_stun_rate({**own, "_thorns_fill": fills})[0] > models.heavy_stun_rate(own)[0]


def test_hits_that_cannot_stun_stop_light_and_heavy_stuns():
    stats = {"Speed": 2.0, "HitChance": 100.0, "PhysicalHitAverage": 10.0 ** 9, "HeavyStunBuildupAvg": 50.0}
    assert models.light_stun_rate(stats)[0] > 0 and models.heavy_stun_rate(stats)[0] > 0
    barred = {**stats, "Flag:CannotStun": True}
    assert models.light_stun_rate(barred)[0] == 0 and models.heavy_stun_rate(barred)[0] == 0
    mods, how = conditions.cannot_stun_mods(["Your Hits cannot Stun enemies"])
    assert {m["name"] for m in mods} == {"CannotHeavyStun", "CannotStun"} and how
    assert conditions.cannot_stun_mods(["Your Hits cannot Stun enemies while Shapeshifted"]) == ([], "")


def test_reflected_curses_carry_your_curse_effect_and_only_the_reflected_ones():
    curses = [{"name": "Enfeeble", "skillId": "EnfeeblePlayer", "level": 10, "inc": 30, "more": 0.8, "atziri": True},
              {"name": "Despair", "skillId": "DespairPlayer", "level": 5, "inc": 30, "more": 1.0, "atziri": False}]
    mods, how = conditions.reflected_curse_mods([], curses)
    names = [(m["name"], m.get("skill")) for m in mods]
    assert ("ExtraCurse", None) in names and ("CurseEffectOnSelf", "Enfeeble") in names
    assert all(m.get("skill") != "Despair" for m in mods) and "Despair" not in how
    extra = next(m for m in mods if m["name"] == "ExtraCurse")
    assert extra["value"] == {"skillId": "EnfeeblePlayer", "level": 10, "applyToPlayer": True}
    more = next(m for m in mods if m["name"] == "CurseEffectOnSelf" and m["type"] == "MORE")
    assert more["value"] == pytest.approx(-20)
    every, _ = conditions.reflected_curse_mods(["Curses you inflict are reflected back to you"], curses)
    assert sum(m["name"] == "ExtraCurse" for m in every) == 2
