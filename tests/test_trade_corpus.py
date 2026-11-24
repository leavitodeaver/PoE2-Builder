"""Real current-league trade items through the rare legality check (AUDIT38's corpus, the manager's 24 Nov 04:05 order).

Every item here is a real listing, so a legal reading of each one is expected: an ILLEGAL verdict on any of them is a
rule of ours that is too tight (a false "illegal" hides a real option from the search: THE USER, 24 Nov ~01:25, "be
careful with what mods are allowed with others"). The other direction (a false "legal") is guarded by
tests/test_legality.py's known-bad items and its route test."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.data import GameData
from builder.search import legality

CORPUS = Path(__file__).parent / "fixtures" / "trade_corpus.json"


@pytest.fixture(scope="module")
def check():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    return legality.Checker()


def items():
    return json.loads(CORPUS.read_text(encoding="utf8"))["items"]


def test_the_corpus_is_the_audited_one():
    rows = items()
    assert len(rows) == 323 and all(r["league"] in ("Forbidden Rites", "Runes of Aldur") for r in rows)
    assert sum(bool(r["xfail"]) for r in rows) == 40  # (AUDIT38's open faults at fd5ab8d9: F1 27, F8 9, F9 5; one both)


def test_every_real_listed_item_reads_legal(check):
    bad = []
    for row in items():
        found = [f for f in check.check(row["gear"]) if f.status == legality.ILLEGAL]
        if found:
            bad.append((row["n"], row["gear"]["base"], row["xfail"], [(f.rule, f.detail[:90]) for f in found]))
    assert not bad, (len(bad), bad[:5])


@pytest.mark.parametrize("n, finding", [(42, "F1"), (120, "F8"), (200, "F8"), (219, "F9"), (220, "F9"), (221, "F9"),
                                        (222, "F9"), (333, "F9")])
def test_each_fixed_finding_is_said_where_it_is_open(check, n, finding):
    """A fixed rule still says what it can't know (UNVERIFIED), where the item leaves it open."""
    row = next(r for r in items() if r["n"] == n)
    assert finding in row["xfail"]
    found = check.check(row["gear"])
    assert not [f for f in found if f.status == legality.ILLEGAL]
    if finding == "F1":
        assert any(f.rule == "sealed_rune" and f.status == legality.UNVERIFIED for f in found)
    if finding == "F9" and len(row["gear"]["affixes"]) > 4:
        assert {f.rule for f in found} <= {"jewel_shape", "unread", "game_family"}, found


ES_FOCUS = "Notable Passive Skills in Radius also grant (15-25)% increased Energy Shield from Equipped Focus"  # (#219's)
EXTRA_SUFFIX = "Notable Passive Skills in Radius also grant (2-3)% increased Mana Cost Efficiency"  # (#220's)


@pytest.mark.parametrize("known", [False, True])
def test_the_time_lost_radius_shape_is_accepted_and_a_fourth_suffix_is_not(check, known):
    """AUDIT38 F9 (a), real listing #222 (Time-Lost Sapphire): 3 suffixes, a Desecrated prefix and the radius craft (a
    PREFIX) read clean: Contempt's "+1 Suffix Modifier allowed" (a prefix craft) held the third suffix's place, then
    Melancholy's radius line replaced it. Both ways: a fourth suffix is not accepted clean (jewel_shape, UNVERIFIED: #219
    is a real one with a Desecrated fourth), a fifth is ILLEGAL, and the same three suffixes with no crafted line are
    ILLEGAL."""
    gear = dict(next(r for r in items() if r["n"] == 222)["gear"])
    if known:  # (a search entry: ItemOption.describe always writes "price")
        gear["price"] = 1.0
    assert check.check(gear) == []
    four = dict(gear, affixes=gear["affixes"] + [ES_FOCUS])
    assert [(f.rule, f.status) for f in check.check(four)] == [("jewel_shape", legality.UNVERIFIED)]
    five = dict(gear, affixes=gear["affixes"] + [ES_FOCUS, EXTRA_SUFFIX])
    assert any(f.rule == "affix_count" and f.status == legality.ILLEGAL for f in check.check(five))
    plain = dict(gear, affixes=[a for a in gear["affixes"] if a != "Upgrades Radius to Very Large"])
    assert any(f.rule == "affix_count" and f.status == legality.ILLEGAL for f in check.check(plain))
