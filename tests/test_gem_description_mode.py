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
