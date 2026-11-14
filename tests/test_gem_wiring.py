"""GEM6: Elemental Discharge's and Broken Stance's models (GEM5) wired into the damage counted outside PoB
(assumptions.elemental_discharge / broken_stance, beside thorns and riven_armour; Assumptions.models adds each as an
added_input and to objective.set_added). Real PoB: each moves a build that has the gem, and nothing without it."""
import faulthandler

import pytest

from builder.data import GameData
from builder.search import models
from builder.search.context import Context, make_payload
from builder.request.schema import BuildRequest


@pytest.fixture(scope="module")
def engine():
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


def _build(engine, skill, ascendancy=None):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill, ascendancies=[ascendancy] if ascendancy else [], level=90,
                                        budget={"gear_tier": "endgame"}, objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), [skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup(quests=False)
    return search.build


def test_elemental_discharge_adds_damage_only_with_the_support_and_an_ailment_on_the_boss(engine):
    from builder.search.assumptions import elemental_discharge

    build = _build(engine, "Spark")
    main = build.main_group()
    assert elemental_discharge(build, None) == (0.0, [])  # (no support: nothing, no note)
    build.call("setGroup", index=main["index"], gems=[{"name": "Spark"}, {"name": "Elemental Discharge"}], main=True,
               fullDPS=True, fields=["FullDPS"])
    build.ailment_uptimes = {"Ignited": 0.0, "Shocked": 0.0, "Frozen": 0.0}
    dps, notes = elemental_discharge(build, None)
    assert dps == 0 and notes and "no Ignite, Shock or Freeze" in notes[0]["basis"]
    build.ailment_uptimes = {"Ignited": 0.0, "Shocked": 0.5, "Frozen": 0.0}  # (a stand-in for the resolver's share)
    dps, notes = elemental_discharge(build, None)
    print("PROBE", dps, notes)
    assert dps > 0 and notes[0]["label"] == "Elemental Discharge*" and "on Spark" in notes[0]["basis"]
    assert "Shock 50%" in notes[0]["basis"]
    # (the Consumed boxes are only ticked for the call: PoB's own Full DPS still has no discharge in it)
    config = build.call("getConfig")["input"]
    assert not any(config.get(box) for box, _ in models.DISCHARGE_BOXES.values())


def test_broken_stance_adds_damage_only_with_vaulting_impact_and_a_dazed_boss(engine):
    from builder.search.assumptions import broken_stance

    build = _build(engine, "Tempest Flurry")  # (Vaulting Impact is a Quarterstaff skill: PoB weaponTypes Staff)
    main = build.main_group()
    assert broken_stance(build, None) == (0.0, [])  # (no Vaulting Impact: nothing, no note)
    build.call("setGroup", gems=[{"name": "Vaulting Impact"}], fields=["FullDPS"])
    raw = build.call("activeSkills")
    skill = next(models.Skill.of(s) for s in raw["skills"] if s["name"] == "Tempest Flurry" and not s.get("disabled"))
    assert skill.group == main["index"]
    if hasattr(build, "dazed_up"):
        del build.dazed_up
    dps, notes = broken_stance(build, skill)
    assert dps == 0 and notes and "not published" in notes[0]["basis"]  # (MECH-A's share not on the build yet)
    build.dazed_up = (0.0, "test: no Daze source")
    assert broken_stance(build, skill)[0] == 0
    build.dazed_up = (0.4, "test: Dazed 40% of the fight")
    dps, notes = broken_stance(build, skill)
    print("PROBE", dps, notes)
    assert dps > 0 and notes[0]["confidence"] == "LOW" and "Dazed 40% of the fight" in notes[0]["basis"]
    assert "next 10 hits" in notes[0]["basis"]  # (its hit limit read from the gem: 10 at quality 0)
