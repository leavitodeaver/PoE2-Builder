"""COV59: the five delay rows settled by RESEARCH62 R62-4 / R62-5 (the manager, 25 Nov 18:37)."""
from builder.ledger import coverage


def test_cov59_r62_delays_are_decided():
    for stat, skill in (("explosion_delay_ms", "Detonate Dead"), ("explosion_delay_ms", "Voltaic Fulmination"),
                        ("explosion_delay_ms", "Fiery Reclamation"), ("aftershock_delay_ms", "Furious Slam"),
                        ("aftershock_delay_ms", "Supercharged Slam")):
        entry = coverage.gem_stat_pattern_entry(stat, skill)
        assert entry is not None and entry.id in ("gem-cov58c-delays-keep-the-rate", "gem-cov59-delays-keep-the-rate"), \
            (stat, skill)
    # (the entry names only the researched skills: an unresearched carrier stays open)
    assert coverage.gem_stat_pattern_entry("explosion_delay_ms", "Some Other Skill") is None
