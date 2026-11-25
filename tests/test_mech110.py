"""MECH110 (MECH-A): COV58's offense rows in MECH-A's topic."""
from builder.search import models


def test_persistent_ground_ii_iii_keep_clouds_from_exploding_and_lengthen_them():
    # Persistent Ground III's support stats: "50% increased duration of Ground Surfaces" and "cannot be destroyed except
    # by expiring" (a flag, read as 1): a 4 s cloud whose PoB duration is 5 s (25% increased) becomes 5 + 4 x 0.5 = 7 s,
    # added to the increased duration rather than multiplied, and no cloud explodes
    seconds, persists = models.persistent_cloud(5.0, 4.0, {"ground_effect_duration_+%": 50,
                                                           "ground_effects_cannot_trigger": 1})
    assert (seconds, persists) == (7.0, True)
    # Persistent Ground I: the duration only; its clouds still explode
    assert models.persistent_cloud(4.0, 4.0, {"ground_effect_duration_+%": 50}) == (6.0, False)
    # no Persistent Ground: unchanged
    assert models.persistent_cloud(4.0, 4.0, {}) == (4.0, False)
