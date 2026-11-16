"""Grasping Mail's "Can roll Ring Modifiers" widens its rare mod pool (COV36; RESEARCH27, loreweave-grasping-mail.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.rares import IMPLICIT_POOL_TAGS, AffixPools, pool_tags

ROOT = Path(__file__).resolve().parents[1]


def pools() -> AffixPools:
    return AffixPools(Context(make_payload(BuildRequest(main_skill="Spark", budget={"gear_tier": "endgame"}))))


def test_grasping_mail_rolls_ring_caster_and_minion_mods_but_other_body_armours_do_not():
    p = pools()
    grasping = p.pool("Grasping Mail")
    lines = [line for a in grasping for line in a.lines]
    assert any("Level of all Spell Skills" in line for line in lines)  # a Genesis Tree caster mod
    assert any("Cast Speed" in line for line in lines)  # a ring suffix
    assert any(line.startswith("Minions") for line in lines)  # a Genesis Tree minion mod
    assert any("maximum Energy Shield" in line or "Armour" in line for line in lines)  # its own body-armour mods stay
    assert len({(a.group, a.type) for a in grasping}) == len(grasping)  # still one affix per group
    # the mod's own spawn order decides: flat-% Evasion lists body_armour 0 before ring 1, so it stays out
    assert not any(a.id.startswith("IncreasedEvasionRating") for a in grasping)
    other = next(b["name"] for b in p.ctx.data.bases if b.get("subType") == "Armour/Evasion/Energy Shield"
                 and b["type"] == "Body Armour" and "Grasping" not in b["name"] and not b.get("hidden"))
    assert not any("Level of all Spell Skills" in line for a in p.pool(other) for line in a.lines)
    for name in ("Runeforged Grasping Mail", "Runefather's Grasping Mail"):
        assert {"ring", "genesis_tree_caster", "genesis_tree_minion"} <= pool_tags(p.ctx.bases_by_name[name])


def test_the_implicits_pool_tags_match_the_game_files():
    games = sorted(ROOT.glob("data/game-*/tables/Mods.json"))
    if not games:
        pytest.skip("no extracted game files")
    tables = games[-1].parent
    mods = json.loads(tables.joinpath("Mods.json").read_text(encoding="utf-8"))
    tags = json.loads(tables.joinpath("Tags.json").read_text(encoding="utf-8"))
    row = next(m for m in mods if m.get("Id") == "ItemCanAlsoRollRingMods")
    assert tuple(tags[i]["Id"] for i in row["Tags"]) == IMPLICIT_POOL_TAGS["Can roll Ring Modifiers"]
    assert [m["Id"] for m in mods if "CanAlsoRoll" in str(m.get("Id"))] == ["ItemCanAlsoRollRingMods"]
