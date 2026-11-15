"""Incinerate's and Ball Lightning's "Ignited Ground" stat sets: PoB scores their fire as Hits, the game's ground never
Hits (it Ignites at 50% Flammability), so the search never makes them the calculated set (MECH79;
conditions.NO_HIT_STAT_SETS; with Oil Grenade's / Oil Barrage's "Oil Ground" and Gas Arrow's "Poison Cloud"). Spear of Solaris's ground set, which PoB scores as Ignite only, stays a choice."""
from __future__ import annotations

import faulthandler

import pytest

from builder.search import conditions
from builder.search.objective import main_dps


class _Build:
    def __init__(self, skills):
        self.skills = skills

    def main_group(self):
        return {"skills": self.skills}


def _conditions(skills):
    c = conditions.Conditions.__new__(conditions.Conditions)
    c.build, c.active = _Build(skills), {}
    return c


def test_the_ground_set_is_never_calculated_on_incinerate_or_ball_lightning_but_is_on_solaris():
    for name, sets in (("Incinerate", ["Incinerate", "Ignited Ground"]),
                       ("Ball Lightning", ["Ball Lightning", "Fire-Infused", "Ignited Ground"])):
        c = _conditions([{"name": name, "statSets": sets}])
        assert not c.allows("Ignited Ground")
        assert c.allows(name)
    for name, label in (("Oil Grenade", "Oil Ground"), ("Oil Barrage", "Oil Ground"), ("Gas Arrow", "Poison Cloud")):
        assert not _conditions([{"name": name, "statSets": ["Base", label]}]).allows(label)
    # (Toxic Grenade's cloud wasn't probed: left a choice)
    assert _conditions([{"name": "Toxic Grenade", "statSets": ["Grenade", "Poison Cloud"]}]).allows("Poison Cloud")
    assert _conditions([{"name": "Spear of Solaris", "statSets": ["Impact", "Pulse", "Ignited Ground"]}]).allows(
        "Ignited Ground")


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


@pytest.mark.parametrize("skill,label", [("Incinerate", "Ignited Ground"), ("Ball Lightning", "Ignited Ground"),
                                         ("Oil Grenade", "Oil Ground")])
def test_the_search_keeps_the_skills_own_cast_not_the_grounds_hits(engine, skill, label):
    """POE2T3127 (before the fix): the search kept the ground set, Incinerate 2,666 DPS of Hits vs 273 for the channel,
    Ball Lightning 549 vs 166; POE2T3128: Oil Grenade's Oil Ground 45.5 vs 22.0."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    build = search.build
    chosen = main_dps(build.stats)
    group = build.main_group()
    sets = group["skills"][0]["statSets"]
    build.set_group(index=group["index"], mainActiveSkill=1, statSet=sets.index(label) + 1)
    ground = main_dps(build.stats)
    others = []
    for i, name in enumerate(sets, 1):
        if name != label:
            build.set_group(index=group["index"], mainActiveSkill=1, statSet=i)
            search.settle()
            others.append(main_dps(build.stats))
    print("SETS", skill, chosen, others, ground)
    assert ground > max(others) > 0 or ground > chosen
    assert any(chosen == pytest.approx(o, rel=1e-6) for o in others)
