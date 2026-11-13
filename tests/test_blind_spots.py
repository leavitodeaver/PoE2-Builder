"""SEARCH13 (COV17): annotate_blind_spots shows a share: ledger entry on an item as applied, since the ledger applies
share: lines on items (ledger.item_lines); flag:, if: and mult: lines still apply on passives only."""
from builder.search.run import annotate_blind_spots

SHARE = "+10% to Maximum Resistances of each Elemental Damage Type you have been Hit with Recently"  # adaptive-skin
IF = "Repeatable Spells have 20% chance to Repeat"  # now-and-again-repeat: an if: line


def test_share_lines_on_items_show_as_applied_and_if_lines_stay_passives_only():
    found = annotate_blind_spots([
        {"source": "Body Armour: Test", "line": SHARE},
        {"source": "passive: Test", "line": SHARE},
        {"source": "Ring 1: Test", "line": IF},
        {"source": "passive: Test", "line": IF},
    ])
    assert found[0].get("corrected_by") == "adaptive-skin" and found[1].get("corrected_by") == "adaptive-skin"
    assert "corrected_by" not in found[2]
    assert found[3].get("corrected_by") == "now-and-again-repeat"
