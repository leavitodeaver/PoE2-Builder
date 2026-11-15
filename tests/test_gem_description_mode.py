"""GEM8: skill_specific_stat_description_mode's described effects (ledger gems-gem8.yaml)."""

from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill

MODE = {"skill_specific_stat_description_mode": 1}


def _area(mods: list[dict]) -> list[tuple[str, float, str | None]]:
    # (gem_stat_model tags a line with its entry only for `share:` entries: match the line itself)
    return [(m["line"], m["scale"], m.get("skill")) for m in mods if m["line"] == "1% more Area of Effect"]


def test_rends_lightning_charged_strike_gets_150_percent_more_area_on_that_set_only():
    charged = Skill("Rend", 1, attack=True, stat_set="Lightning-Charged", level_stats=MODE)
    mods, notes = models.gem_stat_model([charged], main=charged)
    assert _area(mods) == [("1% more Area of Effect", pytest.approx(150), "Rend")]
    assert any(n["entry"] == "model-gem8-rend-charged-area" and n["value"].startswith("150") for n in notes)
    normal = Skill("Rend", 1, attack=True, stat_set="Normal Strikes", level_stats=MODE)
    mods, notes = models.gem_stat_model([normal], main=normal)
    assert not _area(mods)
    assert any(n["entry"] == "model-gem8-rend-charged-area" and n["value"] == "not counted" for n in notes)


@pytest.mark.parametrize("name", ["Whirlwind Lance", "Tempest Bell", "Fulmination"])
def test_the_closed_skills_get_no_line(name):
    skill = Skill(name, 1, attack=True, level_stats=MODE)
    mods, notes = models.gem_stat_model([skill], main=skill)
    assert not [m for m in mods if m.get("skill") == name]
    assert not [n for n in notes if n["entry"] == "no-effect-gem8-description-mode-counted"]


RAIN = {**MODE, "rain_of_arrows_delay_per_arrow": 80, "rain_hit_delay_ms": 120}


def _less(mods: list[dict]) -> list[float]:
    return [m["scale"] for m in mods if m["line"] == "1% less Damage" and m.get("skill") == "Rain of Arrows"]


@pytest.mark.parametrize("mod, arrows, speed, less", [
    (1.0, 39, 1.5, 0.0),             # an arrow every 0.08 s: the 0.12 s limit binds, as PoB counts
    (2.0, 39, 1.5, 0.0),             # faster projectiles add nothing on a boss
    (0.7, 39, 1.5, 0.0),             # Deceleration II alone: 0.114 s, still under the limit
    (0.588, 39, 1.5, 100 * (1 - 0.12 / (0.08 / 0.588))),  # with Short Shot and a -6% node: 0.136 s, ~12% fewer hits
    (1.0, 10, 0.5, 100 * (1 - 10 * 0.08 * 0.5)),          # a 0.8 s rain every 2 s: 40% of the time
])
def test_rain_of_arrows_hit_rate_follows_projectile_speed_and_never_adds(mod, arrows, speed, less):
    rain = Skill("Rain of Arrows", 1, attack=True, level_stats=RAIN)
    stats = {"ProjectileSpeedMod": mod, "ProjectileCount": arrows, "Speed": speed}
    mods, notes = models.gem_stat_model([rain], stats=stats, main=rain)
    assert _less(mods) == ([pytest.approx(less, rel=1e-3)] if less else [])
    assert any(n["entry"] == "model-gem9-rain-landing" and ("not counted" in n["value"]) != bool(less) for n in notes)


def test_rain_of_arrows_off_the_main_skill_keeps_pobs_rate():
    rain = Skill("Rain of Arrows", 2, attack=True, level_stats=RAIN)
    other = Skill("Lightning Arrow", 1, attack=True)
    mods, notes = models.gem_stat_model([rain, other], stats={"ProjectileSpeedMod": 0.5}, main=other)
    assert not _less(mods)
    assert any(n["entry"] == "model-gem9-rain-landing" and "not the main skill" in n["basis"] for n in notes)
