"""COV62: TRAIN 153 / 154 / 155's readers (ledger/entries/items-cov62.yaml), each checked against the landed code."""
from builder.ledger import coverage

TRAIN155_READ = {
    "model-read-cov62-firestorm-grown-storm": [("firestorm_secondary_radius_+_if_consumed_fire_infusion", "Firestorm")],
    "model-read-cov62-savage-fury-fury": [("wild_beast_energy_per_hit", "Savage Fury"),
                                          ("wild_beast_maximum_energy", "Savage Fury")],
    "model-read-cov62-delayed-landing": [("comet_fall_time_ms", "Comet"), ("siege_cascade_fall_time_ms", "Siege Cascade")],
    "model-read-cov62-explosive-demise-threshold": [("command_minion_target_ally_below_x_life", "Explosive Demise")],
    "model-read-cov62-ice-shards-limit": [("permafrost_shard_limit", "Ice Shards")],
}


def test_cov62_train155_readers_are_marked():
    from builder.search import models
    # (the readers each entry names)
    assert models.FIRESTORM_GROW == 15.0
    assert models.DELAYED_LANDING == {"Comet": 0.55, "Siege Cascade": 0.5}
    assert models.demise_threshold(20) == models.EXPLOSIVE_DEMISE_THRESHOLD[19] and callable(models.savage_fury_model)
    assert models.ICE_SHARDS_LIMIT == 16.0 and callable(models.ice_shards_limit_mods)
    assert models.BODACH_LINE.match("The Bodach haunts your Presence")
    covered = coverage.stat_entries()
    for entry, pairs in TRAIN155_READ.items():
        for stat, skill in pairs:
            assert coverage.stat_covered(covered, stat, skill) == ("model", entry), (stat, skill)


def test_cov62_the_unread_stay_open():
    covered = coverage.stat_entries()
    for stat, skill in (("comet_fall_time_ms", "Skyfall"), ("siege_cascade_fall_time_ms", "Hailstorm Rounds"),
                        ("siege_cascade_fall_time_ms", "Stormblast Bolts"),
                        ("crushing_blow_vs_ignited_and_fully_armour_broken", "Syzygy"),
                        ("pillar_of_flame_creation_radius", "Flame Pillar"),
                        ("rip_support_life_remnant_creation_radius", "Rip")):
        assert coverage.stat_covered(covered, stat, skill) is None, (stat, skill)
        assert coverage.gem_stat_pattern_entry(stat, skill) is None, (stat, skill)


def test_cov62_the_bodach_line_is_marked():
    ids = [e.id for e in coverage.entries() if e.kind == "pob_gap" and e.pattern is not None
           and e.pattern.search("The Bodach haunts your Presence")]
    assert "model-read-cov62-bodach" in ids
