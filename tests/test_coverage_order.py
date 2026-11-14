"""COV29 (PERF23's item 2): the audit's ranked rows and the catalog's lines don't depend on the order PoB's dump lists
coverage.json's lines in (it changes from one PoB process to the next)."""
import random

from builder.ledger.coverage import rank, record_order


def _record(i: int, template: str) -> dict:
    return {"status": "none", "category": "affix", "template": template, "mechanic": "damage", "group": f"g{i}",
            "weight": 1.0, "reason": "PoB ignores the line", "line": f"{i}% {template}", "source": f"mod{i}",
            "source_kind": "affix"}


def test_ranked_rows_and_their_examples_are_the_same_whatever_order_the_records_came_in():
    records = [_record(i, t) for i in range(12) for t in ("more Damage", "increased Speed")]
    shown = []
    for seed in (0, 1, 2):
        shuffled = records[:]
        random.Random(seed).shuffle(shuffled)
        shuffled.sort(key=record_order)  # (what audit() does before ranking)
        shown.append([(r["template"], r["example"], r["examples"]) for r in rank(shuffled)])
    assert shown[0] == shown[1] == shown[2]
