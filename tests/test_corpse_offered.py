"""The meta round offers a corpse skill when the build has a corpse supply (SEARCH14; MECHB19's routing, an UNDERCOUNT):
gems.meta_spells called models.corpse_supply(present) with no stats, so it read neither the request's boss's killed adds
nor the build's own sources (Sacrifice), and a corpse skill was never offered. No engine needed."""

from __future__ import annotations

from types import SimpleNamespace

from builder.search import models
from builder.search.gems import Gems


def offered(present: set[str], boss: str | None) -> bool:
    gems = SimpleNamespace(ctx=SimpleNamespace(request=SimpleNamespace(boss=boss)))
    return Gems.corpse_offered(gems, present)


def test_a_corpse_skill_is_offered_only_with_a_corpse_supply():
    # The default boss (the worst case) has no killable adds, and nothing in the build makes corpses: not offered
    assert not offered(set(), None)
    assert not offered({"Fireball"}, None)
    # A boss with killable adds (the King in the Mists' totems, Atziri's acolytes): offered
    assert offered(set(), "The King in the Mists")
    assert offered(set(), "Atziri, the Red Queen")
    # The build's own source (Sacrifice): offered against any boss; the trial rates it from the build's minion deaths
    assert "Sacrifice" in models.CORPSE_SOURCES
    assert offered({"Sacrifice"}, None)


def test_unearth_is_offered_without_a_corpse_supply():
    # SEARCH16 (AUDIT28 A28-5's search half): Unearth's bone spikes hit with no corpse ("Raise spikes of bone from the
    # earth in front of you, damaging enemies", ActiveSkills unearth), so the meta round offers it to a build with no
    # corpse supply; a skill that needs a corpse (Detonate Dead) still isn't offered.
    assert "Unearth" in models.CORPSE_OPTIONAL
    spell = {"support": False, "skillTypes": ["Spell", "Triggerable", "TargetsDestructibleCorpses"]}
    ctx = SimpleNamespace(request=SimpleNamespace(boss=None),
                          gems={"Unearth": dict(spell), "Detonate Dead": dict(spell), "Fireball": {**spell,
                                "skillTypes": ["Spell", "Triggerable"]}})
    gems = SimpleNamespace(ctx=ctx, _offered=lambda name, left, usage: True)
    gems.corpse_offered = lambda present: Gems.corpse_offered(gems, present)
    assert sorted(Gems.meta_spells(gems, {"Raise Skeletons"}, 0.0, None)) == ["Fireball", "Unearth"]
    # With a supply (Sacrifice) the corpse skill is offered too
    assert "Detonate Dead" in Gems.meta_spells(gems, {"Sacrifice"}, 0.0, None)
