"""Half of the crafting routes' legality test (tests/legality_routes.py; SEARCHC7 split it out of test_legality.py)."""
from __future__ import annotations

import pytest

from builder.search.legality import Checker
from legality_routes import HALVES, ROUTES, check_route, new_engine

MINE = HALVES["a"]


@pytest.fixture(scope="module")
def engine():
    yield from new_engine()


@pytest.fixture(scope="module")
def check():
    return Checker()


@pytest.mark.parametrize("switch", MINE)
def test_every_rare_a_route_makes_is_legal(engine, monkeypatch, check, switch):
    check_route(engine, monkeypatch, check, switch, ROUTES[switch])


def test_the_two_halves_hold_every_route_once():
    assert sorted(HALVES["a"] + HALVES["b"]) == sorted(ROUTES)
