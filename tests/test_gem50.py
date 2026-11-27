"""GEM50: Seismic Cry Empowers when it Heavy Stuns a boss Primed for Stun (AUDIT40 A40-1, GEM's half on MECH114's
models.stunned_or_primed_share)."""
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

