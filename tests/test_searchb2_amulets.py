"""SEARCHB2, the manager's 23:58 note (THE USER, 21 Nov: "make sure this applies to absent/lament/portent amulets as
well. those all grant spirit reservation skills, but don't cost spirit"; RESEARCH45 R45-6a).

PoB charges these three bases' granted skills 0 Spirit (Data/Bases/amulet.lua grantedSkillsHaveNoReservation; Classes/
Item.lua passes noReservation to the granted skill; Modules/CalcActiveSkill.lua zeroes its reservation). This locks it
in on items built the way the search builds them (Gear.build_rare with a base and a variant, as granted_meta_trials and
variant_options do): the granted skill's group is there and enabled, the build's reserved Spirit is the same as with
the same rare minus its granted line, and the same skill socketed as an ordinary gem DOES reserve (so the test can see
a reservation)."""
from __future__ import annotations

import dataclasses
import faulthandler

import pytest

from builder import clock
from builder.request.schema import BuildRequest, Choices
from builder.search.context import Context, implicit_variants, make_payload
from builder.search.inner import Search
from builder.search.skeletons import enumerate_skeletons

FIELDS = ["Spirit", "SpiritUnreserved"]


@pytest.fixture(scope="module")
def engine():
    from builder.data import GameData

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


def reserved(build) -> float:
    stats = build.call("stats", fields=FIELDS)
    return float(stats.get("Spirit") or 0) - float(stats.get("SpiritUnreserved") or 0)


def without_granted(raw: str) -> str:
    """The item text minus its "Grants Skill" implicit, the "Implicits: N" count lowered to match."""
    drop = sum(1 for line in raw.splitlines() if "Grants Skill:" in line)
    out = []
    for line in raw.splitlines():
        if line.startswith("Implicits:"):
            line = f"Implicits: {int(line.split(':')[1]) - drop}"
        if "Grants Skill:" not in line:
            out.append(line)
    return "\n".join(out)


@pytest.fixture(scope="module")
def search(engine):
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    found = Search(engine, None, payload, skeleton.as_dict(), 0)
    found.setup(quests=False)
    return found


def measure(search, base: str, skill: str) -> tuple[float, float, float]:
    """(reserved with the amulet's granted skill, reserved with the same rare minus its granted line, reserved with
    the plain rare and the skill as an ordinary gem), on a rare built as gear.py's variant trials build it."""
    build, gear = search.build, search.gear
    variant = next(v for v, line in implicit_variants(search.ctx.bases_by_name[base]["implicit"]) if line.endswith(skill))
    item = gear.build_rare("Amulet", clock.now() + 120, base_name=base, variant=variant)
    assert item is not None and item.base == base and "Grants Skill:" in item.raw and skill in item.raw
    gear.put("Amulet", item)
    groups = [g for g in build.call("groups")["groups"] if g["fromItem"]
              and (any(s["name"] == skill for s in g["skills"]) or any(gem["name"] == skill for gem in g["gems"]))]
    assert groups and all(g["enabled"] for g in groups), (base, skill, groups)
    with_skill = reserved(build)
    gear.put("Amulet", dataclasses.replace(item, raw=without_granted(item.raw)))
    without_skill = reserved(build)
    index = build.call("setGroup", gems=[{"name": skill}], fields=[])["group"]["index"]
    as_gem = reserved(build)
    build.call("removeGroup", index=index, fields=[])
    gear.put("Amulet", None)
    print("PROBE", base, skill, "reserved with", with_skill, "without", without_skill, "as a gem", as_gem)
    return with_skill, without_skill, as_gem


def test_the_absent_amulets_archmage_reserves_no_spirit(search):
    with_skill, without_skill, as_gem = measure(search, "Absent Amulet", "Archmage")
    assert with_skill == pytest.approx(without_skill)
    assert as_gem > without_skill + 1e-6  # (the control: as a gem it reserves, so the reading could see one)


# POE2T6248 / POE2T6253 (22 Nov), all 51 variants: PoB zeroed the reservation only on calcs.buildActiveSkillModList's
# main path (CalcActiveSkill.lua 822-826, srcInstance.noReservation), and 9 of the 51 granted skills still reserved their
# full cost on a search-built amulet: the five Lament Heralds, Wind Dancer and Shard Scavenger (30 each), Portent's
# Mirage Archer and Feral Invocation (60). (MECHC52: fixed in engine/bridge.lua installNoReservation, which zeroes every
# reservation field of a flagged granted skill after PoB builds it, whatever path; probes POE2T4990 before, POE2T4992
# after: Herald of Ash 30 -> 0, Mirage Archer 60 -> 0, Wind Dancer 30 -> 0. The 9 strict xfails are plain passes now.)
FIXED_BY_BRIDGE = {"Herald of Ash", "Herald of Ice", "Herald of Thunder", "Herald of Plague", "Herald of Blood",
                   "Wind Dancer", "Shard Scavenger", "Mirage Archer", "Feral Invocation"}
VARIANTS = {
    "Absent Amulet": ["Cast on Elemental Ailment", "Cast on Critical", "Cast on Dodge", "Rhoa Mount", "Archmage",
                      "Trinity", "Eternal Rage"],
    "Lament Amulet": ["Arctic Armour", "Herald of Ash", "Herald of Ice", "Herald of Thunder", "Plague Bearer",
                      "Barrier Invocation", "Lingering Illusion", "Ghost Dance", "Mana Remnants", "Magma Barrier",
                      "Raging Spirits", "Wind Dancer", "Cast on Minion Death", "Scavenged Plating", "War Banner",
                      "Defiance Banner", "Dread Banner", "Time of Need", "Attrition", "Charge Regulation",
                      "Shard Scavenger", "Overwhelming Presence", "Alchemist's Boon", "Reaper's Invocation", "Berserk",
                      "Herald of Plague", "Herald of Blood", "Withering Presence", "Ravenous Swarm", "Combat Frenzy",
                      "Trail of Caltrops", "Convalescence", "Iron Ward", "Siphon Elements", "Briarpatch", "Savage Fury",
                      "Barkskin"],
    "Portent Amulet": ["Wolf Pack", "Blink", "Elemental Invocation", "Elemental Conflux", "Sacrifice", "Mirage Archer",
                       "Feral Invocation"],
}
CASES = [(base, skill) for base, skills in VARIANTS.items() for skill in skills]


def test_the_variant_list_is_the_bases_own(search):
    """(the list above is every variant our data gives each base, in order)"""
    for base, skills in VARIANTS.items():
        found = [line.split(") ", 1)[1] for _, line in implicit_variants(search.ctx.bases_by_name[base]["implicit"])]
        assert found == skills, base


@pytest.mark.parametrize("base, skill", CASES)
def test_every_skill_amulet_granted_skill_reserves_no_spirit(search, base, skill):
    """Every variant's granted skill, its group enabled, reserves what the same rare without it does; as an ordinary
    gem the skill reserves (the control)."""
    with_skill, without_skill, as_gem = measure(search, base, skill)
    assert as_gem > without_skill + 1e-6
    assert with_skill == pytest.approx(without_skill)
