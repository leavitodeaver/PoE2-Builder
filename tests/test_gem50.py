"""GEM50: Seismic Cry Empowers when it Heavy Stuns a boss Primed for Stun (AUDIT40 A40-1, GEM's half on MECH114's
models.stunned_or_primed_share); Firestorm's bolts land over the storm (RESEARCH57 R57-7) under its 0.15 s limit."""
import pytest

from builder.search import assumptions, models

STUN = {"HeavyStunBuildupAvg": 10.0, "Speed": 2.0, "HitChance": 100, "PhysicalHitAverage": 1000, "CritChance": 0,
        "_main_share": None, "_crushing": (False, "")}
SLAM = [{"slam": True, "attack": True, "melee": True}]


def test_seismic_cry_empowers_at_the_stunned_or_primed_share():
    side = {**STUN, "_primed_stun": (0.25, "Seismic Cry 0.250 uses/s")}
    what, fits, share, why = assumptions.warcry_empowers("Seismic Cry", SLAM, side)
    assert what == "Slams" and fits
    assert share == pytest.approx(models.stunned_or_primed_share(side)[0])
    assert share > models.heavy_stunned_share(side)[0]  # (before: the Heavy Stunned share alone)
    assert "Primed for Stun" in why and "without the Primed part" in why
    # no stun buildup at all: nothing Empowers
    assert assumptions.warcry_empowers("Seismic Cry", SLAM, {**STUN, "HeavyStunBuildupAvg": 0})[2] == 0.0
    # Arctic Howl and the plain Warcries are unchanged
    assert assumptions.warcry_empowers("Infernal Cry", SLAM, side)[2] == 1.0
    assert assumptions.warcry_empowers("Seismic Cry", [{"attack": True}], side)[1] is False


def test_limited_landings_on_a_bolt_grid():
    # one storm's 0.2 s bolts never meet the 0.15 s limit; a Fire-Infused storm's 0.1 s bolts: every other one
    assert models.limited_landings(5.0, 1.0) == pytest.approx(5.0)
    assert models.limited_landings(10.0, 1.0) == pytest.approx(5.0)  # (before: min(10, 6.67))
    assert models.limited_landings(20.0, 1.0) == pytest.approx(20 / 3)  # (0.05 s apart: one in three)
    assert models.limited_landings(10.0, 0.25) == pytest.approx(10 * 0.25 / 1.25)
    assert models.limited_landings(5.0, 0.0) == 0.0


def test_firestorm_bolts_land_over_the_storm():
    from builder.search.models import Skill
    assert models.firestorm_landing(1.0, 3.0, 0.5) == pytest.approx(0.25)
    assert models.firestorm_landing(1.0, 3.0, 5.0) == 1.0  # (a boss larger than the storm takes every bolt)
    sets = [{"label": "Firestorm", "TotalDPS": 1000.0, "Speed": 5.0, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 30}, {"label": "Ice Bolts", "TotalDPS": 800.0},
            {"label": "Lightning Bolts", "TotalDPS": 1500.0}]
    storm = Skill("Firestorm", 1, spell=True)
    mods, notes = models.firestorm_infusion_model(storm, "Firestorm", sets, {}, boss=(0.5, "t"), boss_upper=(1.0, "u"))
    dps = [m for m in mods if m["name"] == "DPS"]
    assert 1 + dps[0]["value"] / 100 == pytest.approx(0.25)  # (a quarter of the bolts land on a 0.5 m boss)
    assert dps[0][models.HIT_CUT_PER_USE] == pytest.approx(0.25)
    basis = notes[0]["basis"]
    assert "every bolt on the boss (PoB's) x1.000" in basis and "x0.444" in basis  # (the 1.0 m upper boss: (2 / 3)^2)
    assert notes[0]["set_uses"] == pytest.approx(5.0 * 0.25)
    # every cast Fire-Infused: a 4.5 m storm, 0.1 s bolts: 10 x (1.5 / 4.5)^2 / (1 + that) hits a second, over PoB's 5
    q = (1.5 / 4.5) ** 2
    _, notes = models.firestorm_infusion_model(storm, "Firestorm", sets, {"Fire": (1.0, "t")}, boss=(0.5, "t"))
    assert f"x{10 * q / (1 + q) / 5:.3f}" in notes[0]["basis"]
    assert "+1.5 m unscaled (4.50 m)" in notes[0]["basis"]
    # no boss radius given: every bolt (PoB's reading), the grid limit kept
    assert models.firestorm_infusion_model(storm, "Firestorm", sets, {})[0] == []
