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
