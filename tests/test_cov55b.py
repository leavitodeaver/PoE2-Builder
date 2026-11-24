"""COV55 turn 2: a Limit-style stat is marked only where the skill's own model reads it; model constants on Limit-type
skills stay open, because they ignore the build's "+N to Limit"."""
from builder.ledger import coverage
from builder.search import models


def test_cov55_surge_cap_is_read_and_limit_constants_stay_open():
    assert models.SURGE_CAP_DEFAULT == 6
    covered = coverage.stat_entries()
    for skill in ("Chaotic Surge", "Elemental Surge"):
        assert coverage.stat_covered(covered, "maximum_number_of_instilling_stacks", skill) == (
            "model", "model-read-cov55-surge-cap")
    for stat, skill in (("number_of_poison_cloud_allowed", "Gas Arrow"),
                        ("forge_hammer_base_maximum_number_of_fissures", "Forge Hammer"),
                        ("entangle_vines_maximum_number_of_vines_allowed", "Entangle"),
                        ("firestorm_max_number_of_storms", "Firestorm"),
                        ("galvanic_field_maximum_number_of_spheres", "Galvanic Field"),
                        ("icestorm_max_number_of_storms", "Icestorm"),
                        ("molten_crash_max_number_of_fissures", "Molten Crash"),
                        ("base_number_of_tempest_bells_allowed", "Tempest Bell")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat
