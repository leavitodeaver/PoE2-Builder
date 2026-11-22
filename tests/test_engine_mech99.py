"""MECH99 (the manager 16:30, for MECHB52's Tornado): Conditions publishes build.shocked_ground_up, the boss's share of
the fight in your Shocked Ground (conditions.shocked_ground), beside chilled_ground_up / ignited_ground_up."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_the_builds_shocked_ground_share_is_published(engine):
    payload = make_payload(BuildRequest(main_skill="Lightning Warp", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Warp"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle_still()
    share, how = search.build.shocked_ground_up
    found = search.conditions.evidence()
    print("PROBE", share, how)
    assert 0 < share <= 1 and how, (share, how)
    assert (share, how) == search.build.shocked_ground_up  # (evidence() publishes the same reading again)
    assert found is not None


def test_the_main_hit_cut_reaches_the_readers_late_in_evidence(engine, monkeypatch):
    """MECH99: Conditions.evidence rebuilt its stats with the uncut self._main_share(), so build.main_hit_cut (MECH98's
    hook, set by MECH-B's Flame Breath / Hailstorm models) never reached shocked_ground, Compounding, Flammability or the
    ailment uptimes below that point. Spy on shocked_ground's stats: it must see the share x the cut."""
    from builder.search import conditions as conditions_mod

    payload = make_payload(BuildRequest(main_skill="Lightning Warp", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Warp"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    seen = []
    real = conditions_mod.shocked_ground
    monkeypatch.setattr(conditions_mod, "shocked_ground", lambda stats, texts: (seen.append(stats["_main_share"]),
                                                                               real(stats, texts))[1])
    search.build.main_hit_cut = None
    search.conditions.evidence()
    search.build.main_hit_cut = 0.4
    search.conditions.evidence()
    search.build.main_hit_cut = None
    print("PROBE", seen)
    assert len(seen) == 2 and seen[0] > 0 and abs(seen[1] - 0.4 * seen[0]) < 1e-9, seen
