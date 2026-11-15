"""Chronomancer lines PoB drops (COV31, PATCH_CHECK H1-1 and H1-5). Phased Form's delayed damage: PoB keeps the node's "Take 30% less Damage" and drops "4
seconds after being Damaged by an Enemy Hit, take Damage equal to 30% of that Hit's Damage" (leftover text); the
ledger hands the delayed 30% of each Hit back. Probes POE2T2512 / POE2T2513 (scratch/cov31/probe_phased.py): the
stand-in is the one tried wording PoB parses whole, and puts a level-90 Chronomancer's Physical maximum hit back at
1,186 (1,187 without the node, 1,696 with it as PoB has it)."""
from builder.ledger import translate

LINE = "4 seconds after being Damaged by an Enemy Hit, take Damage equal to 30% of that Hit's Damage"


def test_phased_forms_delayed_damage_comes_back_on_hits_only():
    entry, lines = translate(LINE)
    assert entry.id == "passives-phased-form-delayed-hit-damage"
    assert lines == ["43% more Damage taken from Hits"]  # DamageTakenWhenHit MORE 43: Hits only, not Damage over Time
    assert abs(0.7 * 1.43 - 1.0) < 0.002  # a Hit back at its full damage (the reading in the entry's notes)


def test_sands_of_time_counts_its_fight_average():
    """Quicksand Hourglass's "Grants Sands of Time" (PATCH_CHECK H1-5): 60% -> 1% -> 60% Skill Speed and 1% -> 60% -> 1%
    Area of Effect over 20 s (KeywordPopups Chronobuff; patch 0.5.0): each at its time average, 30.5 taken as 30."""
    entry, lines = translate("Grants Sands of Time")
    assert entry.id == "passives-sands-of-time-cycle-average"
    assert lines == ["30% increased Skill Speed", "30% increased Area of Effect"]
