"""MECH100 (plans/term-targets.md item B, an OVERCOUNT): a curse gem under level 18 can't curse a boss above its level
cap; PoB applies it anyway. The bridge's levelStats hand the cap on, and curse_model's -100% effect takes the curse off."""
from __future__ import annotations

import faulthandler

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search import assumptions, conditions, models
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


CURSE = "Elemental Weakness"


def test_a_curse_under_level_18_does_nothing_to_a_higher_level_boss(engine):
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    fields = ["TotalDPS"]
    bare = build.call("setAssumptions", mods=[], fields=fields)["stats"]["TotalDPS"]
    readings = {}
    for level in (10, 20):
        build.set_group(gems=[{"name": CURSE, "level": level, "quality": 0}])
        raw = build.call("activeSkills", levelStats=models.curse_gem_names(build.ctx.gems))["skills"]
        caps = assumptions.curse_level_caps(raw)
        build.curse_level_caps, build.curse_boss_level = caps, assumptions.enemy_level(build)
        uses = [u for u in conditions.curse_uses(build, {"Speed": 1.0}, []) if u.name == CURSE]
        found = [models.Skill.of(s) for s in raw if not s["disabled"]]
        mods, _ = models.curse_model(uses, found, None)
        pob = build.call("setAssumptions", mods=[], fields=fields)["stats"]["TotalDPS"]
        ours = build.call("setAssumptions", mods=mods, fields=fields)["stats"]["TotalDPS"]
        readings[level] = (caps.get(CURSE), build.curse_boss_level, uses[0].share, pob, ours)
        build.call("setAssumptions", mods=[], fields=[])
    print("PROBE bare", bare, "readings", readings)
    cap10, boss, share10, pob10, ours10 = readings[10]
    cap20, _, share20, pob20, ours20 = readings[20]
    assert cap10 == 52 and cap20 == 0 and boss > 52
    assert pob10 > bare * 1.01  # (PoB: the curse lowers the boss's resistances at any level)
    assert share10 == 0 and ours10 == pytest.approx(bare, rel=1e-3)  # (ours: none above the cap)
    assert share20 == 1 and ours20 == pytest.approx(pob20, rel=1e-6)  # (level 20: no cap, as before)


def test_neural_overload_on_the_main_skill_is_published_for_the_electrocute_readers(engine):
    """MECH100 (plans/gs-neural-overload.md): Conditions.evidence names Neural Overload on the main skill's group as the
    source that Electrocutes the Primed boss (build.electrocute_primed, dazed_stats), and nothing without it."""
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    search.conditions.evidence()
    assert build.electrocute_primed == ""
    group = build.main_group()
    gems = [{"name": g["name"], "level": g.get("level") or 20, "quality": 0} for g in group["gems"]]
    build.set_group(index=group["index"], gems=gems + [{"name": models.NEURAL_OVERLOAD, "level": 1, "quality": 0}])
    found = search.conditions.evidence()
    electrocuted = found.get("conditionEnemyElectrocuted")
    print("PROBE", build.electrocute_primed, electrocuted)
    assert build.electrocute_primed == models.NEURAL_OVERLOAD
    assert models.dazed_stats(build)["_electrocute_primed"] == models.NEURAL_OVERLOAD
