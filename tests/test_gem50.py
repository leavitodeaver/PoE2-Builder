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


def test_firestorm_counts_only_the_time_the_boss_stays_in_the_storm():
    """GEM50 turn 2 (the manager's 04:59 ruling, the reader rule): the storm stays where it was cast."""
    from builder.search import bosses
    from builder.search.models import Skill
    sets = [{"label": "Firestorm", "TotalDPS": 1000.0, "Speed": 5.0, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 30}]
    storm = Skill("Firestorm", 1, spell=True)
    stay = bosses.stay_share(None, 3.0, 6.0)[0]
    fixed = bosses.stay_share(None, 3.0, 6.0, moves="fixed")[0]
    assert 0 < fixed < stay < 1
    mods, notes = models.firestorm_infusion_model(storm, "Firestorm", sets, {}, boss=(0.5, "t"), stay_stats=None,
                                                  seconds=6.0)
    dps = [m for m in mods if m["name"] == "DPS"][0]
    assert 1 + dps["value"] / 100 == pytest.approx(0.25 * stay, rel=1e-4)
    assert dps[models.HIT_CUT_PER_USE] == pytest.approx(0.25 * stay)
    basis = notes[0]["basis"]
    assert "the boss standing still in the storm x0.250" in basis and f"x{0.25 * fixed:.3f}" in basis
    assert "READER RULE" in basis
    # no Duration read: no stay cut (the landing share alone)
    mods, _ = models.firestorm_infusion_model(storm, "Firestorm", sets, {}, boss=(0.5, "t"))
    assert 1 + [m for m in mods if m["name"] == "DPS"][0]["value"] / 100 == pytest.approx(0.25)


def test_mirror_out_share_is_mechb69s_geometry():
    """GEM50 item 3: GEM's Mirror model and the return model share mirror_out_share (MECHB69's geometry)."""
    for r, d in ((0.4, 0.9), (0.4, 2.0), (0.4, 6.0)):
        assert models.mirror_out_share(r, d) == pytest.approx(models.mirror_nova_geometry(r, d, models.MIRROR_PRESENCE, 7)[0])
    # at the boss's feet the old angle count read 1.0, the geometry 0.39 (R64-10; an OVERCOUNT fixed)
    assert models.mirror_out_share(0.4, 0.9) == pytest.approx(0.39, abs=0.01)
    assert models.mirror_out_share(0.4, 0.9) < models.nova_unaimed_hits(7, 0.4, 0.9)


def test_one_cloud_count_for_gem_and_mech_b():
    assert models.cloud_poison_cover(4.0, 2.0, 2.02)[0] == models.cloud_count(4.0, 2.02) == 2
    assert models.cloud_applications(4.0, step=4.0) == 1.0  # (a cloud ending as its first Poison ends: no second)
    assert models.cloud_applications(7.02) == 4.0
