"""Force of Will's missing Mana from the build (COV31, PATCH_CHECK L1-F): "20% increased Effect of Arcane Surge on you
per ten percent missing Mana" counts once per ten at ForceOfWillTens' count (the build's own drain against its Mana
regeneration), not the flat 2.5 tens it had."""
from builder.ledger import translate
from builder.search.assumptions import missing_mana_tens


def test_the_line_counts_per_ten_at_the_builds_count():
    entry, lines = translate("20% increased Effect of Arcane Surge on you per ten percent missing Mana")
    assert entry.id == "force-of-will"
    assert lines == ["share:ForceOfWillTens 20% increased effect of Arcane Surge on you"]


def test_missing_tens_follow_the_drain():
    assert missing_mana_tens(1000, 100, [])[0] == 0  # nothing drains it: full Mana
    assert missing_mana_tens(1000, 100, [(2, 20)])[0] == 0  # small casts refilled at once: never a tenth missing
    # one 300-Mana hit every 10 s, 100 regenerated a second: 2 tens for 1 s, 1 ten for 1 s more, each 10 s
    assert abs(missing_mana_tens(1000, 100, [(0.1, 300)])[0] - 0.3) < 1e-9
    # spending more than it regenerates: dry, within one 20-Mana cast of empty
    tens = missing_mana_tens(1000, 50, [(5, 20)])[0]
    assert 8.9 <= tens <= 10
    assert missing_mana_tens(0, 100, [(1, 10)])[0] == 0
