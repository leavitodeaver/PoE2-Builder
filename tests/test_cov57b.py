"""COV57 turn 2: TRAIN 141's readers of open gem-stat rows, marked as read."""
from builder.ledger import coverage
from builder.search import conditions


def test_cov57b_train141_readers_are_marked():
    assert conditions.ENDURANCE_SUPPORTS["Armour Break II"] == ("break", 0.15)
    assert conditions.ENDURANCE_SUPPORTS["Armour Break III"] == ("break", 0.20)
    assert "shapeshift_minimum_channel_time_ms_+" in conditions.CHANNEL_MIN_STATS
    covered = coverage.stat_entries()
    for stat, skill, entry_id in (
            ("chance_to_gain_endurance_charge_on_armour_break_%", "Armour Break II", "model-read-cov57b-armour-break-endurance"),
            ("chance_to_gain_endurance_charge_on_armour_break_%", "Armour Break III", "model-read-cov57b-armour-break-endurance"),
            ("shapeshift_minimum_channel_time_ms_+", "Rampage", "model-read-cov57b-rampage-minimum-channel")):
        assert coverage.stat_covered(covered, stat, skill) == ("model", entry_id), stat
