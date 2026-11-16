"""COV36: "This item gains bonuses from Socketed Items as though it was a Body Armour" (Darkness Enthroned, The Bringer
of Rain). PoB's ModParser doesn't parse it (the audit's "PoB ignores the line"), but PoB's Item.lua reads the text itself
when it applies socketed augments (Classes/Item.lua, GetSocketedAugmentTypes). LANE_RULES: "PoB already does X needs a
probe": these engine tests are it."""

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


def score(engine, items: dict[str, str], fields: list[str]) -> dict:
    engine.call("newBuild")
    engine.call("setLevel", level=90)
    for slot, raw in items.items():
        engine.call("equip", slot=slot, fields=[], raw=raw)
    engine.call("addSkillGroup", text="Spark 20/0 1", main=True)
    return engine.call("stats", fields=fields)


def test_the_bringer_of_rain_takes_an_augments_body_armour_line(engine):
    """Amanamu's Gaze: body armour "+2 to Armour per 1 Spirit", helmet "Remove a Damaging Ailment when you use a Command
    Skill". In The Bringer of Rain (a helmet) the body armour line applies, and without the line it doesn't."""
    spirit = "Rarity: Rare\nProbe\nGold Amulet\nImplicits: 0\n+100 to Spirit"
    helm = with_runes(unique("The Bringer of Rain"), ["Amanamu's Gaze"])
    plain = "\n".join(l for l in helm.splitlines() if "as though it was a Body Armour" not in l)
    with_line = score(engine, {"Helmet": helm, "Amulet": spirit}, ["Armour"])["Armour"]
    without = score(engine, {"Helmet": plain, "Amulet": spirit}, ["Armour"])["Armour"]
    assert with_line > without


def test_darkness_enthroned_takes_an_armour_rune(engine):
    """A belt takes no rune bonus of its own; Darkness Enthroned's body armour variant (2) takes Desert Rune's armour
    line ("+14% to Fire Resistance"), raised by its own "increased effect of Socketed Augment Items"."""
    belt = unique("Darkness Enthroned").replace("Sockets: S S\n", "") + "\n"
    belt = belt.replace("Variant: Shield\n", "Variant: Shield\nSelected Variant: 2\n", 1)
    socketed = with_runes(belt, ["Desert Rune"])
    fire = lambda raw: score(engine, {"Belt": raw}, ["FireResist"])["FireResist"]
    assert fire(socketed) - fire(with_runes(belt, [])) >= 14
