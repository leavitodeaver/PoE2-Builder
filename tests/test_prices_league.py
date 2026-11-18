"""The priced-on league is an explicit setting (the manager, 18 Nov 14:50; SEARCH28): poe.ninja listed the event league
"Forbidden Rites" first, and default_league() took poe.ninja's first non-hardcore, non-Standard league."""
import json

from builder.data import prices


def _feed(tmp_path, listed=("Forbidden Rites", "Runes of Aldur", "HC Runes of Aldur", "Standard")):
    (tmp_path / "leagues.json").write_text(json.dumps({"fetched_at": 0, "etag": None, "url": "",
                                                       "payload": [{"id": name} for name in listed]}), encoding="utf-8")
    return prices.PriceFeed(tmp_path)


def test_the_default_league_is_the_setting_not_poe_ninjas_first(tmp_path, monkeypatch):
    monkeypatch.delenv("POE2_LEAGUE", raising=False)
    feed = _feed(tmp_path)
    assert feed.default_league() == prices.DEFAULT_LEAGUE == "Forbidden Rites"  # (the manager's ruling, 18 Nov 17:50)
    monkeypatch.setenv("POE2_LEAGUE", "Runes of Aldur")
    assert feed.default_league() == "Runes of Aldur"


def test_a_table_says_what_is_wrong_with_its_league(tmp_path, monkeypatch):
    monkeypatch.delenv("POE2_LEAGUE", raising=False)
    feed = _feed(tmp_path)
    table = feed.table("Runes of Aldur", refresh=False)  # (nothing cached for it: no prices, no fitted rare model)
    assert table.league == "Runes of Aldur" and not table.exchange and not table.uniques
    assert any("no local poe.ninja prices for 'Runes of Aldur'" in w for w in table.warnings)
    assert any("rare price model isn't fitted on 'Runes of Aldur'" in w for w in table.warnings)
    assert not any("isn't in poe.ninja's league list" in w for w in table.warnings)
    # a league poe.ninja doesn't list
    assert any("isn't in poe.ninja's league list" in w for w in feed.league_warnings("Necropolis", priced=True))
    # a league with prices and a fitted model: nothing to say
    (tmp_path / "Runes of Aldur").mkdir()
    (tmp_path / "Runes of Aldur" / prices.RARE_MODEL_FILE).write_text("{}", encoding="utf-8")
    assert feed.league_warnings("Runes of Aldur", priced=True) == []


def test_league_warnings_never_ask_poe_ninja(tmp_path, monkeypatch):
    feed = prices.PriceFeed(tmp_path)  # (no cached league list)
    monkeypatch.setattr(feed, "_fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert any("isn't cached" in w for w in feed.league_warnings("Runes of Aldur", priced=False))
