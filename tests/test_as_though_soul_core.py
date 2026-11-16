"""COV36 probe POE2T2599 (passed: Mana 444 with the line, 424 without, 424 with no core): "This item gains bonuses from Socketed Soul Cores as though it was also a
Helmet" (Atziri's Splendour, group 1 variant 1; Item.lua 1280 / 1527). Soul Core of Zalatl's only line is its helmet one,
"5% increased maximum Mana": with the line the body armour should take it, without it nothing."""

from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.search.rares import with_runes


def unique(name: str) -> str:
    return next(u["raw"] for u in GameData().uniques if u["name"] == name)


@pytest.fixture(scope="module")
def engine():
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def mana(engine, raw: str) -> float:
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Body Armour", fields=[], raw=raw)
    engine.call("addSkillGroup", text="Spark 20/0 1", main=True)
    return engine.call("stats", fields=["Mana"])["Mana"]


def test_atziris_splendour_takes_a_soul_cores_helmet_line(engine):
    body = with_runes(unique("Atziri's Splendour"), ["Soul Core of Zalatl"])
    plain = "\n".join(l for l in body.splitlines() if "Soul Cores as though it was also" not in l)
    empty = with_runes(unique("Atziri's Splendour"), [])
    with_line, without, bare = mana(engine, body), mana(engine, plain), mana(engine, empty)
    assert with_line > without
    assert without == bare
