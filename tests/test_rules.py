"""The game-rules table (builder/data/game_rules.yaml): what the game allows where PoB is looser.
Needs the game-data dump."""

from __future__ import annotations

import pytest

from builder.data import GameData
from builder.data.rules import rules
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload


@pytest.fixture(scope="module", autouse=True)
def data() -> GameData:
    try:
        return GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")


def test_off_hands_follow_the_game_not_pob():
    table = rules()
    # Martial weapons dual wield only with the same class; spears and wands never go in the off
    # hand; a sceptre goes beside a one-hander but not beside another sceptre
    assert table.off_hand_weapons("One Hand Mace") == ("Sceptre", "One Hand Mace")
    assert table.off_hand_weapons("Spear") == ("Sceptre",)
    assert table.off_hand_weapons("Wand") == ("Sceptre",)
    assert table.off_hand_weapons("Sceptre") == ()
    assert table.off_hands("Bow") == ("Quiver",)
    assert table.off_hands("Two Hand Mace") == table.off_hands("Staff") == table.off_hands(None) == ()
    every = {t for offs in table.off_hand.values() for t in offs}
    assert not every & table.unreleased and "Spear" not in every and "Wand" not in every


def test_classes_not_in_the_game_and_unique_only_bases_are_never_normal_or_rare(data):
    table = rules()
    assert {"Claw", "Dagger", "Flail", "One Hand Sword", "Two Hand Axe"} <= table.unreleased
    assert {"Spear", "One Hand Mace", "Talisman"} <= table.released and not table.released & table.unreleased
    assert set(data.weapon_types) <= table.released | table.unreleased  # every PoB weapon class is decided
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    normal = {o.name for options in ctx.item_options.values() for o in options if not o.unique}
    assert "Golden Visage" not in normal and "Golden Mantle" not in normal
    assert not [o for options in ctx.item_options.values() for o in options if not o.unique and o.type in table.unreleased]
    assert "Runeforged Barbed Spear" in normal  # an ordinary base stays
