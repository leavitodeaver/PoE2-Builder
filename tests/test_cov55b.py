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
    for stat, skill in (("entangle_vines_maximum_number_of_vines_allowed", "Entangle"),
                        ("firestorm_max_number_of_storms", "Firestorm"),
                        ("galvanic_field_maximum_number_of_spheres", "Galvanic Field"),
                        ("icestorm_max_number_of_storms", "Icestorm"),
                        ("base_number_of_tempest_bells_allowed", "Tempest Bell")):
        assert coverage.stat_covered(covered, stat, skill) is None, stat
    # (GEM41, the manager's 21:27 item 1) read with the build's own Limit into a headline number now: closed
    for stat, skill, entry in (("number_of_poison_cloud_allowed", "Gas Arrow", "model-gem41-gas-arrow-cloud-limit"),
                               ("forge_hammer_base_maximum_number_of_fissures", "Forge Hammer", "model-gem41-fissure-limits"),
                               ("molten_crash_max_number_of_fissures", "Molten Crash", "model-gem41-fissure-limits")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry), stat
