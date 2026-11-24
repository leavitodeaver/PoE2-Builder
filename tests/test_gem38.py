"""GEM38: Limits and durations the build sets (Overabundance, "+N to Limit") on Shield Wall, Frozen Locus, Earthshatter
and Earthquake; Kaom's Madness's row on Shield Wall (RESEARCH53 R53-5)."""
import math

from builder.search import models
from builder.search.models import Skill

WALL = [{"label": "Shield Wall", "TotalDPS": 100.0, "AverageDamage": 100.0, "Speed": 1.0}]
LOCUS = [{"label": "Crystal", "TotalDPS": 0.0, "AverageDamage": 0.0, "Speed": 1.0},
         {"label": "Explosion", "TotalDPS": 100.0, "AverageDamage": 100.0, "Speed": 1.0}]


def test_instances_standing_follows_cast_breaks_limit_and_duration():
    assert models.instances_standing(1.0, 0.5, 1, 6.0) == 1.0
    assert abs(models.instances_standing(1.0, 0.5, 2, 6.0) - (1 + math.exp(-0.5))) < 1e-9  # (GEM20's form at Limit 2)
    three = models.instances_standing(1.0, 0.5, 3, 6.0)
    assert abs(three - (1 + math.exp(-0.5) + math.exp(-1.0))) < 1e-9
    assert models.instances_standing(2.0, 0.0, 5, 3.0) == 2.0  # (a 3 s wall: casts 2 s apart keep two up, not five)


def test_kaoms_row_never_passes_the_limit():
    sw = Skill("Shield Wall", 1, attack=True)
    _, plain = models.shield_wall_model(sw, "Shield Wall", WALL, 1.0, 0.5, 0.0, 100.0, "slams")
    _, kaom = models.shield_wall_model(sw, "Shield Wall", WALL, 1.0, 0.5, 0.0, 100.0, "slams", fissures=3)
    assert kaom[0]["value"] == plain[0]["value"]  # (the headline unchanged)
    assert "UNVERIFIED" in kaom[0]["basis"] and "2.00 walls a shatter (at most the Limit, 2)" in kaom[0]["basis"]
    walls = models.shield_wall_hits(1.0, 0.5)
    assert f"x{2.0 / walls:.2f}," in kaom[0]["basis"]  # (all breaks yours: 2 walls over the headline's)
    _, raised = models.shield_wall_model(sw, "Shield Wall", WALL, 1.0, 0.5, 0.0, 100.0, "slams", fissures=3, limit=3)
    assert "3.00 walls a shatter (at most the Limit, 3)" in raised[0]["basis"]


def test_a_raised_limit_pays_only_through_the_walls_the_build_keeps_up():
    sw = Skill("Shield Wall", 1, attack=True)

    def value(speed, **k):
        return float(models.shield_wall_model(sw, "Shield Wall", WALL, speed, 0.25, 0.0, 100.0, "s", **k)[1][0]["value"][1:])
    assert value(1.0, limit=3) > value(1.0)  # (a third wall stands often enough)
    assert value(0.25, limit=3, seconds=3.0) < value(0.25)  # (4 s casts, Overabundance I's 3 s wall: the raise costs more)


def test_frozen_locus_reads_the_duration_and_keeps_a_raised_limit_a_row():
    fl = Skill("Frozen Locus", 1, attack=True)
    _, base = models.frozen_locus_model(fl, "Explosion", LOCUS, 1.0, 0.1, 0.0, "breaks")
    _, cut = models.frozen_locus_model(fl, "Explosion", LOCUS, 1.0, 0.1, 0.0, "breaks", limit=2, seconds=5.0)
    assert float(cut[0]["value"][1:]) < float(base[0]["value"][1:])  # (the 50% less duration in the headline)
    assert "Limit 2" in cut[0]["basis"] and "UNVERIFIED" in cut[0]["basis"] and "Limit 2" not in base[0]["basis"]


def test_earthshatter_share_reads_the_builds_limit_and_duration():
    table = models.detonated_share("Earthshatter", 0.75, 0.2)
    assert abs(table - min(1.0, 0.2 * min(6, 0.75 * 6) / 0.75)) < 1e-9
    assert models.detonated_share("Earthshatter", 0.75, 0.2, limit=7, seconds=3.0) < table  # (Overabundance I)
    assert (models.detonated_share("Earthshatter", 3.0, 0.1, limit=7, seconds=6.0)
            > models.detonated_share("Earthshatter", 3.0, 0.1))


def test_earthquake_aftershocks_are_capped_by_its_patches():
    eq = Skill("Earthquake", 1, attack=True)
    assert models.earthquake_limit_model(eq, "Aftershock", 1.0, 6, 4.0) == ([], [])  # (1.5 a second allowed)
    mods, notes = models.earthquake_limit_model(eq, "Aftershock", 3.0, 6, 4.0)
    assert mods[0]["hit_cut"] == 0.5 and notes[0]["value"] == "x0.500"
    assert models.earthquake_limit_model(eq, "Impact", 3.0, 6, 4.0) == ([], [])
    mods2, notes2 = models.earthquake_limit_model(eq, "Aftershock", 3.0, 7, 4.0, 2.0)  # (the shorter fuse is a row only)
    assert abs(mods2[0]["hit_cut"] - 7 / 4 / 3) < 1e-9 and "UNVERIFIED" in notes2[0]["basis"]
