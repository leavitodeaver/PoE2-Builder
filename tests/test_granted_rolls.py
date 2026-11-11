"""A unique's granted-skill level, priced per level from trade listings (builder/search/granted_rolls.py; the user, 8 Nov
2026: "Needed roll, trade-priced")."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from builder.data import GameData, unique_versions
from builder.report.shopping import granted_filters, granted_roll_note, stat_index, unique_ladder
from builder.report.version_prices import VersionPricing, granted_entries
from builder.search import granted_rolls, models
from builder.search.context import ItemOption, Context

STATS = {"skill.cast_lightning_spell_on_hit": {"text": "Grants Skill: Level # Thundergod's Wrath", "type": "skill"},
         "skill.spirit_vessel_companion": {"text": "Grants Skill: Level # Spirit Vessel", "type": "skill"}}


@pytest.fixture(scope="module")
def data() -> GameData:
    return GameData()


def raw_of(data: GameData, name: str) -> str:
    return next(u["raw"] for u in data.uniques if u["name"] == name)


def test_every_unique_granting_a_rolled_level_is_found(data) -> None:
    rolled = {u["name"] for u in data.uniques if granted_rolls.rolled(u["raw"])}
    # PoB's export, build bb52d6b368: 62 uniques, every one (1-20); the game files hold no level for them
    assert {"Mjolner", "Forgotten Warden", "Svalinn", "Dusk Vigil", "The Unborn Lich"} <= rolled
    assert len(rolled) >= 60
    assert granted_rolls.rolled(raw_of(data, "Dusk Vigil")) == [("Firebolt", 1, 20), ("Ember Fusillade", 1, 20)]


def test_a_level_is_set_on_every_rolled_line(data) -> None:
    for name, level in (("Mjolner", 19), ("Dusk Vigil", 18), ("Forgotten Warden", 1), ("Svalinn", 20)):
        raw = granted_rolls.with_level(raw_of(data, name), level)
        levels = [models.granted_level(m)[0] for m in models.GRANTED_RANGE.finditer(raw)]
        assert levels and set(levels) == {level}, name
    # The export's own {range:} on the line is replaced, and setting it twice keeps one tag
    once = granted_rolls.with_level(raw_of(data, "Mjolner"), 12)
    twice = granted_rolls.with_level(once, 18)
    assert twice.count("{range:") == once.count("{range:") <= raw_of(data, "Mjolner").count("{range:") + 1
    assert models.granted_level_notes([("Mjolner", twice)])[0]["value"] == 18


def test_labels_and_listing_levels() -> None:
    label = granted_rolls.label(19, "Thundergod's Wrath")
    assert granted_rolls.parse_label([label]) == (19, "Thundergod's Wrath")
    assert granted_rolls.parse_label(["Medium Ring"]) is None and granted_rolls.parse_label([]) is None
    item = {"grantedSkills": [{"name": "Grants Skill", "values": [["Level 18 Thundergod's Wrath", 25]]}]}
    assert granted_rolls.listed_level(item, "Thundergod's Wrath") == 18
    assert granted_rolls.listed_level(item, "Spirit Vessel") is None


def test_the_job_finds_each_unique_and_its_stat(data) -> None:
    entries, unsearchable = granted_entries(data, STATS)
    assert {e["name"] for e in entries} == {"Mjolner", "Forgotten Warden"}
    mjolner = next(e for e in entries if e["name"] == "Mjolner")
    assert (mjolner["stat"], mjolner["low"], mjolner["high"]) == ("skill.cast_lightning_spell_on_hit", 1, 20)
    assert "Svalinn (Cast on Block)" in unsearchable  # no stat given here: named, not guessed


def test_the_job_prices_a_ladder_of_levels(data, tmp_path, monkeypatch) -> None:
    """Mjolner on the trade site, 10 Nov 2026: level 18 at 1 chaos, 19 at 1 chaos, 20 only corrupted at 40+ div."""
    monkeypatch.setattr(unique_versions, "DATA_ROOT", tmp_path)
    market = {1: [(18, 0.01)] * 4 + [(19, 0.02)] * 6, 19: [(19, 0.01)] * 10, 20: [(20, 40), (20, 55), (20, 80)]}

    class Site:
        def __init__(self) -> None:
            self.queries = []

        def stats(self, offline=False):
            return STATS

        def search(self, league, query, max_age=None, **wait):
            assert "name" not in query["query"] and query["query"]["type"] == "Torment Club"
            minimum = query["query"]["stats"][0]["filters"][0]["value"]["min"]
            self.queries.append(minimum)
            found = market.get(minimum, [])
            return {"id": str(minimum), "total": len(found), "result": [f"{minimum}.{n}" for n in range(len(found))],
                    "url": f"url{minimum}", "fetched_at": 5000.0}

        def fetch(self, ids, query_id, max_age=None, **wait):
            return [{"listing": {"price": {"amount": price, "currency": "divine"}},
                     "item": {"grantedSkills": [{"values": [[f"Level {level} Thundergod's Wrath", 25]]}]}}
                    for level, price in market[int(query_id)]]

    site = Site()
    feed = SimpleNamespace(table=lambda league, refresh=False: SimpleNamespace(currency={"divine": 1.0}))
    job = VersionPricing(site, feed, data)
    mjolner = next(e for e in granted_entries(data, STATS)[0] if e["name"] == "Mjolner")
    kept = job.price_granted("Test", mjolner, {"divine": 1.0})
    assert site.queries == [1, 19, 20]
    assert [(e["variants"], e["price"]) for e in kept] == [
        (["Grants Skill: Level 18+ Thundergod's Wrath"], 0.01), (["Grants Skill: Level 19+ Thundergod's Wrath"], 0.01),
        (["Grants Skill: Level 20+ Thundergod's Wrath"], 55)]
    assert len(unique_versions.load("Test")) == 3


def test_the_search_buys_each_priced_level(data) -> None:
    raw = raw_of(data, "Mjolner")
    option = ItemOption("Mjolner", "Torment Club", "One Hand Mace", None, raw, True, 0.02, "high", 65)
    priced = {f"k{level}": {"name": "Mjolner", "variants": [granted_rolls.label(level, "Thundergod's Wrath")],
                            "listed": listed, "price": price, "priced_at": 5000.0}
              for level, listed, price in ((18, 2000, 0.01), (19, 400, 0.02), (20, 21, 55.0), (17, 0, None))}
    ctx = SimpleNamespace(ssf=False, unique_versions=priced, price_cap=50.0)
    versions = Context.granted_versions(ctx, option)
    # Level 20 costs more than the cap; nobody lists 17
    assert [(o.price, o.variants) for o in versions] == [
        (0.01, ("Grants Skill: Level 18+ Thundergod's Wrath",)), (0.02, ("Grants Skill: Level 19+ Thundergod's Wrath",))]
    assert [models.granted_level_notes([("Mjolner", o.raw)])[0]["value"] for o in versions] == [18, 19]
    assert versions[0].confidence.startswith("trade, Thundergod's Wrath level 18+")
    assert len(Context.granted_versions(ctx, option, capped=False)) == 3
    # Unpriced, and in SSF: PoB's middle roll, labelled in the guide and the shopping list
    assert Context.granted_versions(SimpleNamespace(ssf=False, unique_versions={}, price_cap=50.0), option) == [option]
    assert Context.granted_versions(SimpleNamespace(ssf=True, unique_versions=priced, price_cap=50.0), option) == [option]


def test_the_shopping_list_searches_and_names_the_level(data) -> None:
    lines = granted_filters(19, "Thundergod's Wrath", stat_index(STATS))
    assert lines == [{"text": "Grants Skill: Level 19+ Thundergod's Wrath", "value": {"min": 19},
                      "id": "skill.cast_lightning_spell_on_hit"}]
    label, query = unique_ladder({"name": "Mjolner", "base": "Torment Club", "version_lines": lines})[0]
    assert label == "this version"
    assert query["query"]["stats"][0]["filters"] == [{"id": "skill.cast_lightning_spell_on_hit", "value": {"min": 19}}]
    note = granted_roll_note(raw_of(data, "Mjolner"), [granted_rolls.label(19, "Thundergod's Wrath")])
    assert "Thundergod's Wrath at level 19 or higher" in note and "trade site's listings" in note
    assert "middle roll" in granted_roll_note(raw_of(data, "Mjolner"))
