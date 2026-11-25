"""COV58 turn 2: TRAIN 148's readers marked (Biting Frost II's consumed-Freeze Chill; Skyfall's count, Infusion and Limit)."""
from builder.ledger import coverage
from builder.search import conditions, models


def test_cov58b_train148_readers_are_marked():
    assert conditions.FREEZE_CONSUME_CHILL_STAT == "support_active_skill_consume_enemy_freeze_to_apply_chill"
    assert (models.SKYFALL_PER_CAST, models.SKYFALL_PER_INFUSED_CAST, models.SKYFALL_LIMIT) == (3, 6, 12.0)
    covered = coverage.stat_entries()
    expect = {("support_active_skill_consume_enemy_freeze_to_apply_chill", "Biting Frost II"):
              "model-read-cov58b-biting-frost-consume-chill",
              ("verisium_comet_number_of_minions_to_create", "Skyfall"): "model-read-cov58b-skyfall-comet-spawn-count",
              ("active_skill_consumes_a_verisium_infusion", "Skyfall"): "model-read-cov58b-skyfall-comet-spawn-count",
              ("base_number_of_verisium_octopus_minions_allowed", "Skyfall"): "model-read-cov58b-skyfall-limit"}
    for (stat, skill), entry in expect.items():
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry), stat
        assert sum(len(v) for s, v in covered.items() if s == stat) == 1, stat  # one entry per stat
    # (the manager, TRAIN 149: GEM43's e348f32b, in the same train, closes Fortifying Cry's Shield Wave with a "PoB already
    # counts it" entry proven by its probe; the pin written before it said "open")
    assert coverage.stat_covered(covered, "attack_is_melee_override", "Shield Wave") == ("ledger", "gem-gem43-melee-override-pob-flags")
