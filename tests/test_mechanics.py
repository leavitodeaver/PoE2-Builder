"""The mechanics catalog (builder/ledger/mechanics.py) and its web page."""

import pytest

from builder.ledger import mechanics


def test_catalog_entries_are_complete():
    entries = mechanics.catalog_entries()
    assert len(entries) >= 25
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    for e in entries:
        assert e.get("game") and e.get("pob") in mechanics.STATUS_ORDER and e.get("plan"), e["id"]
        assert e.get("what_if") in ("visible", "invisible", "n/a") and e.get("impact") and e.get("effort"), e["id"]


def test_config_options_are_read_from_pob():
    if not mechanics.POB_SRC.exists():
        pytest.skip("needs the pinned PoB checkout")
    options = {o["var"]: o for o in mechanics.config_options()}
    assert len(options) > 400 and "Condition:BeenHitRecently" in options["conditionBeenHitRecently"]["sets"]


def test_the_catalog_builds_and_links_lines():
    from builder.data import data_dir

    if not (data_dir() / "coverage-report.json").exists():
        pytest.skip("no coverage report; run `uv run python -m builder.ledger.coverage`")
    cat = mechanics.build()
    by_id = {m["id"]: m for m in cat["mechanics"]}
    assert by_id["parry-blind"]["lines"]["linked"] > 0
    assert by_id["heavy-stun"]["configs"]  # PoB's "Is the enemy Heavy Stunned?" box
    assert all(m["origin"] for m in cat["mechanics"])
    assert "### Thorns" in mechanics.markdown(cat)


def test_the_mechanics_page_serves():
    from builder.web import app as web

    page = web.mechanics_page()
    assert str(page.path).endswith("mechanics.html")
    assert web.mechanics_catalog()["summary"]["mechanics"] > 25
    assert 'href="/mechanics"' in (web.STATIC / "index.html").read_text(encoding="utf-8")


def test_every_mechanic_with_lines_has_a_plan():
    from builder.data import data_dir

    if not (data_dir() / "coverage-report.json").exists():
        pytest.skip("no coverage report")
    cat = mechanics.build()
    unplanned = [m["name"] for m in cat["mechanics"] if (m["lines"]["linked"] or m["gem_count"]) and not m.get("plan")]
    assert not unplanned, f"mechanics with lines and no plan (add a catalog entry or a families.yaml pattern): {unplanned}"
