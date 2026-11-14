"""GEM2: the Merging crossbow bolts' fragments (ledger gems-shotgun-gem2.yaml; models.shotgun_fragments_share)."""

from __future__ import annotations

import pytest

from builder.search import models
from builder.search.models import Skill


def _lines(mods: list[dict], entry: str) -> list[tuple[str, float, str | None]]:
    return [(m["line"], m["scale"], m.get("skill")) for m in mods if m.get("entry") == entry]


def _shot(name: str = "Incendiary Shot", stat_set: str | None = "Projectile", group: int = 1) -> Skill:
    return Skill(name, group, attack=True, stat_set=stat_set,
                 level_stats={"projectiles_can_shotgun": 1, "ignite_duration_+%_per_shotgun_pellet_hit": 30,
                              "base_number_of_projectiles": 10})


@pytest.mark.parametrize("name", ["Incendiary Shot", "Fragmentation Rounds", "Permafrost Bolts", "Galvanic Shards"])
def test_every_fragment_of_a_merging_bolt_counts_at_point_blank_and_one_without(name):
    shot = _shot(name)
    # with it: 10 fragments Merge into one hit, x10 the hit PoB scores for one fragment
    mods, notes = models.gem_stat_model([shot], stats={"ProjectileCount": 10}, main=shot)
    assert _lines(mods, "gem-shotgun-fragments-merge") == [("1% more Damage", pytest.approx(900), name)]
    note = next(n for n in notes if n["entry"] == "gem-shotgun-fragments-merge")
    assert "LOW" in note["basis"] and "Without it: x1" in note["basis"] and "point-blank" in note["play"]
    # without it: the entry switched off leaves PoB's one fragment
    mods, _ = models.gem_stat_model([shot], {"gem-shotgun-fragments-merge"}, {"ProjectileCount": 10}, shot)
    assert not _lines(mods, "gem-shotgun-fragments-merge")
    # a support or item that adds fragments raises it with PoB's own count
    mods, _ = models.gem_stat_model([shot], stats={"ProjectileCount": 12}, main=shot)
    assert _lines(mods, "gem-shotgun-fragments-merge")[0][1] == pytest.approx(1100)


def test_the_fragments_count_on_the_projectile_set_and_the_main_skill_only():
    beam = _shot("Galvanic Shards", stat_set="Beam")
    mods, notes = models.gem_stat_model([beam], stats={"ProjectileCount": 8}, main=beam)
    assert not _lines(mods, "gem-shotgun-fragments-merge")
    assert any(n["entry"] == "gem-shotgun-fragments-merge" and n["value"] == "not counted" for n in notes)
    side, main = _shot(group=2), Skill("Spark", 1, spell=True)
    mods, notes = models.gem_stat_model([main, side], stats={"ProjectileCount": 10}, main=main)
    assert not _lines(mods, "gem-shotgun-fragments-merge")
    assert any("not the main skill" in n["basis"] for n in notes if n["entry"] == "gem-shotgun-fragments-merge")


def test_incendiary_shot_ignite_duration_grows_with_each_fragment_in_the_merged_hit():
    shot = _shot()
    mods, _ = models.gem_stat_model([shot], stats={"ProjectileCount": 10}, main=shot)
    assert _lines(mods, "gem-incendiary-ignite-duration-per-fragment") == [
        ("1% increased Ignite Duration", pytest.approx(300), "Incendiary Shot")]


def test_ice_fragments_keep_one_fragment_per_group():
    ice = Skill("Ice Fragments", 1, attack=True, triggered=True, stat_set="Ice Fragments",
                level_stats={"projectiles_can_shotgun": 1, "base_number_of_projectiles": 7})
    mods, _ = models.gem_stat_model([ice], stats={"ProjectileCount": 7}, main=ice)
    assert not [m for m in mods if m.get("skill") == "Ice Fragments"]
