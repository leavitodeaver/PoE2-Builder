"""Way of the Stonefist's glove transform (COV34; builder/search/stonefist.py). No engine: the texts only. PoB scoring the
transformed text (the Fists of Stone base's per-level defences, the HandWraps lines) was probed: POE2T2563 / POE2T2570."""
from __future__ import annotations

import pytest

from builder.search import stonefist


@pytest.fixture(scope="module")
def data():
    from builder.data import GameData

    try:
        found = GameData()
        found.mods
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    return found


RARE = ["Rarity: Rare", "Optimizer Gloves", "Gauze Wraps", "Item Level: 82", "Quality: 20", "Implicits: 0",
        "+207 to maximum Life", "+43% to Fire Resistance", "Adds 15 to 27 Physical Damage to Attacks",
        "15% increased Attack Speed", "96% increased Evasion and Energy Shield", "+33 to Intelligence"]


def test_a_rares_lines_become_their_hand_wraps_mods_at_the_same_roll(data):
    """Game Mods (build 26476662): HandWraps<id> for each regular mod id; PoB's pools carry both texts. The tier is the
    one whose range holds the value (15% Attack Speed is IncreasedAttackSpeed4, 14-16%, not a unique's 5-10%)."""
    text, kept = stonefist.transform("\n".join(RARE), data.mods)
    lines = text.split("\n")
    assert lines[:5] == ["Rarity: Rare", "Optimizer Gloves", "Fists of Stone", "Item Level: 82", "Quality: 20"]
    assert lines[5:8] == ["Implicits: 2", *stonefist.IMPLICITS]
    assert lines[8:] == ["17% less damage taken while on Low Life", "+3% to Maximum Fire Resistance",
                         "+38% to Fire Resistance", "Attacks Gain 22% of Damage as Extra Physical Damage",
                         "28% chance to gain Onslaught for 4 seconds on Hit",
                         "20% more Global Evasion Rating and Energy Shield", "36% increased Cooldown Recovery Rate"]
    assert kept == []


def test_a_line_with_no_pair_stays_and_is_named(data):
    text, kept = stonefist.transform("\n".join(RARE[:6] + ["Some line no mod has"]), data.mods)
    assert text.endswith("Some line no mod has") and kept == ["Some line no mod has"]


def test_a_unique_takes_poe2dbs_transformed_text(data):
    """poe2db's Fists of Stone page (37 uniques; builder/data/stonefist_uniques.json): The Master's Reach."""
    raw = next(u["raw"] for u in data.uniques if u["name"] == "The Master's Reach")
    text, kept = stonefist.transform(raw, data.mods)
    lines = text.split("\n")
    assert lines[:2] == ["The Master's Reach", "Fists of Stone"] and not kept
    assert "(55-65)% of damage taken from enemies with an Open Weakness Recouped as Life and Energy Shield" in lines
    assert "(200-300)% increased Armour and Energy Shield" not in lines
    assert len(stonefist.unique_texts()) == 37
