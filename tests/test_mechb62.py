"""MECHB62: one spell in two socket groups (U1, COV56's calibration): a meta group's trigger rate and "more" lines reach
that group's copy only (models.in_group; engine/bridge.lua installGroupAssumptions)."""
import pytest

from builder.search import models
from builder.search.models import Inputs, Skill


def _rates(mods):
    return {(m["skill"], m.get("group")): m["value"] for m in mods
            if m.get("kind") == "SkillData" and m.get("key") == "triggerRate"}


def test_a_spell_in_two_meta_groups_and_by_hand_gets_one_rate_per_group():
    stats = {"Speed": 2.0, "HitChance": 100}
    slam = Skill("Boneshatter", group=1, attack=True, melee=True)
    meta = Skill("Thundergod's Wrath", group=2, energy=True, level=11)
    comet = Skill("Comet", group=2, spell=True, triggered=True, cast_time=1.2)
    alone, _, _ = models.energy_model(Inputs(), stats, [slam, meta, comet], slam)
    assert all("group" not in m for m in alone)  # (one copy: nothing changes)
    by_hand = Skill("Comet", group=3, spell=True, cast_time=1.2)
    other = Skill("Unknown Meta", group=4, energy=True)  # (not modelled: its spells get 0)
    comet4 = Skill("Comet", group=4, spell=True, triggered=True, cast_time=1.2)
    mods, _, _ = models.energy_model(Inputs(), stats, [slam, meta, comet, by_hand, other, comet4], slam)
    rates = _rates(mods)
    assert rates[("Comet", 2)] == pytest.approx(_rates(alone)[("Comet", None)])
    assert rates[("Comet", 4)] == 0.0
    assert ("Comet", None) not in rates and ("Comet", 3) not in rates  # (the hand-cast copy keeps PoB's own speed)
    assert all(m.get("group") in (2, 4) for m in mods if m.get("skill") == "Comet")
    assert models.rates_by_name(mods)["Comet"] == pytest.approx(rates[("Comet", 2)])


def test_in_group_and_rates_by_name():
    mods = models.trigger_rate("Comet", 0.5) + [models.line("1% more Damage", 10, skill="Spark")]
    tagged = models.in_group(mods, 7, {"Comet"})
    assert [m.get("group") for m in tagged] == [7, 7, None]
    assert models.in_group(mods, None, {"Comet"}) == mods
    both = models.in_group(models.trigger_rate("Comet", 0.5), 2, {"Comet"}) + models.in_group(
        models.trigger_rate("Comet", 0.25), 4, {"Comet"})
    assert models.rates_by_name(both) == {"Comet": pytest.approx(0.75)}
    assert models.shared_names([Skill("Comet", group=2), Skill("Comet", group=3)], 2, {"Comet"}) == {"Comet"}
    assert models.shared_names([Skill("Comet", group=2)], 2, {"Comet"}) == set()


# On real PoB: the user's example builds 7 (Comet by hand in group 9, and in Cast on Critical, group 3) and 15 (Comet in
# Cast on Elemental Ailment, groups 4 and 12, and in Cast on Critical, group 15), COV56's calibration (probe POE2T8044).
@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def _load(engine, name):
    from pathlib import Path

    from builder.pob import codes

    code = (Path(__file__).parent / "fixtures" / "codes" / f"{name}.txt").read_text(encoding="utf-8").strip()
    engine.call("loadXml", xml=codes.decode(code))
    engine.call("setAssumptions", mods=[], fields=[])


def _speed(engine, mods, group, active):
    engine.call("setGroup", index=group, main=True, mainActiveSkill=active)
    engine.call("setAssumptions", mods=mods, fields=[])
    return engine.call("stats", fields=["Speed", "TotalDPS"])


def test_build_15_each_meta_group_keeps_its_own_comet_rate(engine):
    _load(engine, "example15_comet_three_meta_groups")
    names = [[g["name"] for g in group["gems"]] for group in engine.call("groups")["groups"]]
    assert "Comet" in names[3] and "Comet" in names[11] and "Comet" in names[14]
    rates = (models.in_group(models.trigger_rate("Comet", 0.5), 4, {"Comet"})
             + models.in_group(models.trigger_rate("Comet", 0.25), 12, {"Comet"})
             + models.in_group(models.trigger_rate("Comet", 0.0), 15, {"Comet"}))
    assert _speed(engine, rates, 4, 2)["Speed"] == pytest.approx(0.5, rel=1e-3)
    assert _speed(engine, rates, 12, 2)["Speed"] == pytest.approx(0.25, rel=1e-3)
    assert _speed(engine, rates, 15, 2)["Speed"] < 1e-6
    # the old name-wide mods: the last (group 15's 0) reached every copy
    wide = [{k: v for k, v in m.items() if k != "group"} for m in rates]
    assert _speed(engine, wide, 4, 2)["Speed"] < 1e-6
    # a group's "more" line reaches that group's copy only
    base4, base12 = _speed(engine, rates, 4, 2)["TotalDPS"], _speed(engine, rates, 12, 2)["TotalDPS"]
    more = rates + [{**models.line("1% more Damage", 100, skill="Comet"), "group": 4}]
    assert _speed(engine, more, 4, 2)["TotalDPS"] == pytest.approx(2 * base4, rel=1e-3)
    assert _speed(engine, more, 12, 2)["TotalDPS"] == pytest.approx(base12, rel=1e-3)
    engine.call("setAssumptions", mods=[], fields=[])


def test_build_7_the_hand_cast_comet_keeps_its_own_speed(engine):
    _load(engine, "example07_comet_cast_on_critical")
    names = [[g["name"] for g in group["gems"]] for group in engine.call("groups")["groups"]]
    assert names[8][0] == "Comet" and "Comet" in names[2]
    own = _speed(engine, [], 9, 1)["Speed"]
    assert own > 0.1
    zero = models.in_group(models.trigger_rate("Comet", 0.0), 3, {"Comet"})
    assert _speed(engine, zero, 9, 1)["Speed"] == pytest.approx(own, rel=1e-6)
    assert _speed(engine, zero, 3, 2)["Speed"] < 1e-6
    wide = models.trigger_rate("Comet", 0.0)  # (before: the Cast on Critical group's 0 stopped the hand-cast Comet)
    assert _speed(engine, wide, 9, 1)["Speed"] < 1e-6
    engine.call("setAssumptions", mods=[], fields=[])
