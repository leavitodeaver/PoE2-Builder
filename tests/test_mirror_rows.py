"""PATCH_CHECK's mirror rows (MECH-A, MECH77): PoB option boxes nothing set, now set from the build."""
from __future__ import annotations

import faulthandler
from types import SimpleNamespace

import pytest

from builder.search import conditions


def _stub(main: str, groups: list[list[str]], others: dict[str, float], lightning: float, made: list[str]):
    build = SimpleNamespace(call=lambda name, **_: {"groups": [
        {"index": i + 1, "gems": [{"name": n} for n in names]} for i, names in enumerate(groups)]})
    stub = SimpleNamespace(build=build, main_skill=main)
    stub._group_uses = lambda groups, names: {n: others[n] for n in names if n in others}
    stub._chosen_use = lambda name: 0.0
    stub._infusion_supply = lambda lines, bomb=False: ({"Fire": 0.0, "Cold": 0.0, "Lightning": lightning, "Any": 0.0},
                                                      made)
    return stub


def test_arcs_infused_share_is_its_part_of_the_lightning_infusions_over_its_casts():
    stub = _stub("Arc", [["Arc"], ["Orb of Storms"], ["Frost Wall"]], {"Frost Wall": 0.5}, 1.0, ["Orb of Storms"])
    share, how = conditions.Conditions._arc_infused(stub, [], {"Speed": 1.5})
    # 1 Infusion/s shared 1.5 : 0.5 with Frost Wall: 0.75/s for 1.5 casts/s
    assert share == pytest.approx(0.5) and "Orb of Storms" in how and "Frost Wall" in how
    keep = [{"text": "50% chance to not remove an Infusion but still count as consuming it", "source": "tree"}]
    assert conditions.Conditions._arc_infused(stub, keep, {"Speed": 1.5})[0] == pytest.approx(1.0)


def test_arc_with_no_infusion_source_gets_nothing_and_the_box_needs_arc():
    stub = _stub("Arc", [["Arc"]], {}, 0.0, [])
    assert conditions.Conditions._arc_infused(stub, [], {"Speed": 2.0})[0] == 0.0
    assert conditions._arc_infused([], {"_gem_names": {"Spark"}, "_arc_infused": (1.0, "x")}) == (0.0, "")
    on, basis, up = conditions._arc_infused([], {"_gem_names": {"Arc"}, "_arc_infused": (0.4, "0.80/s")})
    assert on and up == pytest.approx(0.4) and "40%" in basis
    assert not conditions._arc_infused([], {"_gem_names": {"Arc"}, "_arc_infused": (0.0, "none")})[0]


def test_drain_ailments_finds_an_ailment_on_the_share_of_casts_the_build_reapplies_it(monkeypatch):
    monkeypatch.setattr(conditions.models, "buildup_rate", lambda stats, ailment: 0.0)
    drained = dict.fromkeys(conditions.DRAIN_CONSUMES, conditions.DRAIN_AILMENTS)
    base = {"Speed": 1.0, "HitChance": 100.0, "_consumes": drained, "_gem_names": {"Contagion", "Drain Ailments"}}
    # nothing applies an ailment: 0, and the box stays off
    assert conditions.drain_share(base)[0] == 0.0
    assert not conditions._drain_ailments([], base)[0]
    # another group Ignites 2/s at 50% (1 Ignite/s): each cast consumes it, 1 s immune, then waits 1 s: 1 / (1 + 1 + 1)
    fed = {**base, "_ailment_others": {"Ignite": [("Flame Wall", 2.0, 0.5, 4.0)]}}
    share, how = conditions.drain_share(fed)
    assert share == pytest.approx(1.0 / 3.0) and "Ignite" in how
    on, basis, up = conditions._drain_ailments([], fed)
    assert on and up == pytest.approx(1.0 / 3.0)
    # Drain Ailments on another skill: not the main skill's consumes, 0
    assert conditions.drain_share({**fed, "_consumes": {}})[0] == 0.0


def test_main_consumes_lists_drain_ailments_types(monkeypatch):
    skills = {"skills": [{"name": "Contagion", "disabled": False, "levelStats": {}, "supportStats": {},
                          "consumes": {"Freeze": True, "Shock": True, "Ignite": True, "Bleed": True}}]}
    build = SimpleNamespace(call=lambda name, **_: skills,
                            ctx=SimpleNamespace(all_gems={"Drain Ailments": {"description": "consume Ailments"}}))
    found = conditions.main_consumes(build, "Contagion", {"Contagion", "Drain Ailments"})
    assert set(found) == set(conditions.DRAIN_CONSUMES) and found["Poison"] == conditions.DRAIN_AILMENTS
    assert conditions.main_consumes(build, "Contagion", {"Contagion"}) == {}


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


def test_a_fed_arc_counts_its_infused_damage_at_the_share_of_its_casts(engine):
    """PoB's Lightning Infused? box was never set: a fed Arc counted none of its 200% more. With Orb of Storms beside it
    (its Lightning Infusion Remnants) the box is on at the share the supply covers, and switching it off costs damage."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Arc", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Arc"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.settle()
    if "Orb of Storms" in {n["name"] for g in search.build.call("groups")["groups"] for n in g["gems"]}:
        pytest.skip("the skeleton already carries Orb of Storms")
    assert not search.conditions.active.get("arcLightningInfused")
    search.build.call("addSkillGroup", text="Orb of Storms 20/0 1")
    search.settle()
    search.weigh_uptimes()
    up = search.conditions.uptimes.get("arcLightningInfused", 0.0)
    print("ARC infused", up, search.conditions.active.get("arcLightningInfused"),
          [r for r in search.uptime_rows if r["var"] == "arcLightningInfused"])
    assert 0.0 < up <= 1.0
    row = next(r for r in search.uptime_rows if r["var"] == "arcLightningInfused")
    assert row["ratio"] < 0.6  # (200% more on Arc's hits: off is about a third of on)


def test_crackling_palms_bolts_count_at_the_unarmed_hits_rate(engine):
    """Thunderfist's Crackling Palm: PoB works out the Bolt's hit but scores it 0 (POE2T3109). Killing Palm unarmed with
    Hollow Palm Technique hits with Unarmed Melee damage (the bridge's flag, now read from the stat set), so the bolts
    count at min(its hits a second, 10) x the Bolt's average damage."""
    from builder.request.schema import BuildRequest
    from builder.search import assumptions
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Killing Palm", ascendancies=["Invoker"]))
    skeleton = enumerate_skeletons(Context(payload), ["Killing Palm"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert assumptions.crackling_palm(build) == (0.0, [])  # (no Thunderfist)
    for slot in ("Weapon 1", "Weapon 2"):
        build.call("equip", slot=slot, fields=[])
    build.call("allocate", add=[64601], remove=[], attribute=1, fields=[])  # Hollow Palm Technique
    gloves = "\n".join(["Rarity: Unique", "Thunderfist", "Utility Wraps", "Item Level: 82", "Implicits: 1",
                        "Grants Skill: Level 20 Crackling Palm", "Adds 1 to 100 Lightning Damage to Unarmed Melee Hits"])
    build.call("equip", slot="Gloves", raw=gloves, fields=[])
    stats = build.call("stats", fields=["Speed", "HitChance", "LedgerUnarmedMelee"])
    assert stats["LedgerUnarmedMelee"] == 1
    dps, notes = assumptions.crackling_palm(build)
    rate = min(stats["Speed"] * stats["HitChance"] / 100, 10.0)
    print("PALM", dps, rate, notes)
    assert dps > 0 and 50 < dps / rate < 200 and "bolts/s" in notes[0]["basis"]
