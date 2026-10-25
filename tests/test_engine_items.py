"""Item mechanics through a real engine: off-hand weapons, flask and charm slots. Boots one
PoB engine for the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler
import random
import re

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload, normal_item
from builder.search.gear import Gear, SlotSpec
from builder.search.objective import Objective
from builder.search.skeletons import offhand_weapon_types
from builder.search.state import Build


@pytest.fixture(scope="module")
def engine():
    try:
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump; run `uv run python -m builder.data dump`")
    from builder.pob.engine import PobEngine

    # LuaJIT raises Lua errors as Windows exceptions that PoB's pcall catches; pytest's
    # faulthandler would print each one as a "fatal exception"
    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def context(**body) -> Context:
    return Context(make_payload(BuildRequest(**body)))


def base_of(ctx: Context, item_type: str) -> dict:
    return max((b for b in ctx.data.bases if b["type"] == item_type and not b.get("hidden")),
               key=lambda b: (b.get("req") or {}).get("level") or 0)


@pytest.mark.parametrize("main", ["Spear", "Wand", "One Hand Mace"])
def test_off_hand_weapons_are_ones_pob_also_accepts(engine, main):
    """The game's rules (game_rules.yaml) are stricter than PoB's: never offer what PoB rejects."""
    ctx = context(main_skill="Explosive Spear")
    engine.call("newBuild")
    engine.call("equip", slot="Weapon 1", raw=normal_item(base_of(ctx, main)), fields=[])
    weapons = sorted({b["type"] for b in ctx.data.bases if b["type"] in ctx.data.weapon_types})
    accepted = [t for t in weapons
                if engine.call("validForSlot", raw=normal_item(base_of(ctx, t)), slot="Weapon 2")["valid"]]
    assert set(offhand_weapon_types(ctx, main)) <= set(accepted)
    if main != "Wand":  # beside a wand PoB, too, takes only a sceptre
        assert len(accepted) > len(offhand_weapon_types(ctx, main))  # PoB also takes daggers, flails, ...


def test_flask_slots_switch_on_only_when_a_boss_fight_sustains_them(engine):
    ctx = context(main_skill="Spark")
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    gear = Gear(build, SlotSpec("Wand"), None, random.Random(0))
    assert gear.slots["Flask 1"] == SlotSpec("Flask", "Life") and gear.slots["Charm 3"] == SlotSpec("Charm")
    gear.sync_flasks()
    plain = "Rarity: Normal\nUltimate Mana Flask"
    charged = "Rarity: Magic\nUltimate Mana Flask\nItem Level: 82\nGains 0.15 Charges per Second"
    info = gear.flask_info([plain, charged])
    assert info[0]["kind"] == "Flask" and info[0]["manaTotal"] > 0 and info[1]["gainBase"] == pytest.approx(0.15)
    assert not gear.counts("Flask 2", info[0], plain)  # no charges in a boss fight
    option = next(o for o in gear.options("Flask 2") if not o.unique)
    gear.put("Flask 2", option)
    assert build.extra_stats["FlaskManaRecoveryRate"] == 0.0
    xml = engine.call("exportXml")["xml"]
    assert any('name="Flask 2"' in t and 'active="true"' not in t for t in re.findall(r"<Slot [^>]*>", xml))
    # With enough charge generation the same flask counts, and its slot is switched on
    gear.charges = gear.charges.__class__(flask=10)
    gear.put("Flask 2", option)
    assert build.extra_stats["FlaskManaRecoveryRate"] > 0
    xml = engine.call("exportXml")["xml"]
    assert any('name="Flask 2"' in t and 'active="true"' in t for t in re.findall(r"<Slot [^>]*>", xml))
    # sync_flasks re-reads the build's real (zero) generation and switches it off again
    gear.sync_flasks()
    xml = engine.call("exportXml")["xml"]
    assert any('name="Flask 2"' in t and 'active="true"' not in t for t in re.findall(r"<Slot [^>]*>", xml))
    assert build.extra_stats["FlaskManaRecoveryRate"] == 0.0


# Weapon swap and seeds ------------------------------------------------------------------------


def test_a_required_skill_set_one_cant_use_goes_on_weapon_set_two(engine):
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear", ascendancies=["Ritualist"], offhand="weapon",
                  require={"skills": ["Ice Shot"]})
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    groups = {g["gems"][0]["name"]: g for g in search.build.groups() if g["gems"] and not g["fromItem"]}
    assert (groups["Ice Shot"]["set1"], groups["Ice Shot"]["set2"]) == (False, True)
    assert groups["Explosive Spear"]["main"] and groups["Explosive Spear"]["set1"]
    assert search.build.equipped["Weapon 1 Swap"].type == "Bow" and search.build.equipped["Weapon 2 Swap"].type == "Quiver"
    facts = engine.call("facts", fields=[])
    must = context(main_skill="Explosive Spear", offhand="weapon", weapon_swap="must", require={"skills": ["Ice Shot"]})
    check = next(c for c in must.compiled.check(facts) if c.label == "Weapon swap")
    assert check.ok and "Ice Shot" in check.delivered


def test_a_seed_keeps_what_the_request_allows_and_never_ends_below_its_start(engine):
    from builder import ROOT
    from builder.search.inner import optimize_task, seed_task

    code = (ROOT / "benchmarks" / "reference" / "2b75c.txt").read_text(encoding="utf-8").strip()
    request = BuildRequest(class_name="Huntress", ascendancies=["Ritualist"], level=90, seed_code=code,
                           forbid={"uniques": ["Ingenuity"]}, budget={"gear_tier": "endgame"}, objective="boss")
    payload = make_payload(request)
    found = seed_task(engine, None, payload)
    skeleton = found["skeleton"]
    assert skeleton["seeded"] and skeleton["main_skill"] == "Explosive Spear" and skeleton["offhand"] == "Sceptre"
    result = optimize_task(engine, None, payload, skeleton, 6, seed=1)
    start = result["seed_start"]
    assert result["score"] >= start["score"] - 1e-9
    assert any("Ingenuity" in note and "banned" in note for note in start["notes"])
    assert any("over a level 90" in note for note in start["notes"])
    assert all(option.name != "Ingenuity" for option in result["state"]["equipped"].values())
    budget = engine.call("pointBudget")
    assert budget["used"] <= budget["passives"]
    # A request that rules the code's ascendancy out gets no seed skeleton, and says why
    other = make_payload(BuildRequest(ascendancies=["Deadeye"], seed_code=code))
    found = seed_task(engine, None, other)
    assert found["skeleton"] is None and "Ritualist" in found["notes"][0]


def test_a_seeds_own_configuration_is_not_evidence(engine):
    """2b75c's config has the boss frozen and shocked at -100% elemental resistances, 6 Frenzy and
    3 Power Charges. After setup_seed only what a new build has and what the resolver found stays."""
    from builder import ROOT
    from builder.search.inner import Search, seed_task

    code = (ROOT / "benchmarks" / "reference" / "2b75c.txt").read_text(encoding="utf-8").strip()
    payload = make_payload(BuildRequest(class_name="Huntress", ascendancies=["Ritualist"], level=90, seed_code=code,
                                        objective="boss"))
    engine.call("newBuild")
    fresh = engine.call("getConfig")["input"]
    skeleton = seed_task(engine, None, payload)["skeleton"]
    loaded = engine.call("getConfig")["input"]
    assert loaded["conditionEnemyFrozen"] and loaded["enemyFireResist"] == -100 and loaded["overrideFrenzyCharges"] == 6
    search = Search(engine, None, payload, skeleton, 1)
    search.setup_seed(quests=False)
    config = engine.call("getConfig")["input"]
    for var in ("conditionEnemyFrozen", "enemyFireResist", "enemyColdResist", "enemyLightningResist",
                "overrideFrenzyCharges", "overridePowerCharges", "conditionUsingFlask"):
        assert var not in config
    changed = {var for var in set(config) | set(fresh) if config.get(var) != fresh.get(var) and not var.startswith("quest")}
    assert changed <= set(search.conditions.active)


def test_a_loaded_build_never_reads_the_previous_builds_cached_skills(engine):
    """Both builds have Ice Shot on weapon set 2; the second has no Mana (Blood Magic). PoB's skill
    cache carried the first build's Ice Shot Mana cost over to the second when a fresh engine loaded
    them in this order, so PoB warned about Mana (gate 5). A fresh worker process replays it."""
    from pathlib import Path

    from builder.pob import codes
    from builder.pob.pool import EnginePool

    folder = Path(__file__).parent / "fixtures" / "codes"
    first, second = (codes.decode((folder / f"{n}.txt").read_text(encoding="utf-8").strip())
                     for n in ("set2_ice_shot_with_mana", "set2_ice_shot_blood_magic"))
    with EnginePool(1) as pool:
        pool.broadcast("loadXml", xml=first)
        pool.broadcast("facts", fields=[])
        pool.broadcast("loadXml", xml=second)
        assert pool.broadcast("facts", fields=[])[0]["warnings"] == []


def test_changing_a_groups_gems_keeps_the_chosen_stat_set(engine):
    """The skill part and stat set PoB calculates live on the gem; rewriting the group's gems (a
    support swap, or undoing one) used to reset Glacial Lance from Explosion to Spear (1.02M to
    613k on the user's run)."""
    ctx = context(main_skill="Glacial Lance")
    engine.call("newBuild")
    engine.call("setClass", className="Huntress", ascendancy="Amazon")
    engine.call("setLevel", level=90)
    engine.call("equip", slot="Weapon 1", raw=normal_item(base_of(ctx, "Spear")), fields=[])
    fields = ["CombinedDPS"]
    group = engine.call("setGroup", gems=[{"name": "Glacial Lance", "level": 20, "quality": 20}], main=True,
                        fields=fields)["group"]
    assert group["skills"][0]["statSets"] == ["Spear", "Explosion"]
    spear = engine.call("stats", fields=fields)["CombinedDPS"]
    explosion = engine.call("setGroup", index=group["index"], statSet=2, fields=fields)["stats"]["CombinedDPS"]
    assert explosion != spear
    with_support = [{"name": "Glacial Lance", "level": 20, "quality": 20}, {"name": "Elemental Focus", "level": 20, "quality": 0}]
    engine.call("setGroup", index=group["index"], gems=with_support, fields=fields)
    undone = engine.call("setGroup", index=group["index"], gems=with_support[:1], fields=fields)["stats"]["CombinedDPS"]
    assert undone == explosion


def test_gem_requirements_per_level_match_pob(engine):
    levels = engine.call("gemRequirements", names=["Elemental Conflux", "Herald of Ice"])["gems"]
    conflux = {entry["level"]: entry for entry in levels["Elemental Conflux"]}
    assert (conflux[20]["levelRequirement"], conflux[20]["int"], conflux[20]["str"]) == (90, 157, 0)
    assert conflux[1]["int"] == 0 and conflux[10]["int"] < conflux[20]["int"]
    herald = {entry["level"]: entry for entry in levels["Herald of Ice"]}
    assert herald[20]["dex"] == herald[20]["int"] == 86


def test_a_search_never_ends_below_where_it_started(engine):
    """A pass the deadline cuts short can stop inside a block; before any pass finished without
    improving, nothing restored the start (finalize's short polish ended worse than the build the
    run had chosen)."""
    import time

    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark", ascendancies=["Stormweaver"])
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    start = search.build.score

    def interrupted_pass(deadline: float) -> bool:
        main = next(g for g in search.build.groups() if g["main"])
        search.build.set_group(index=main["index"], gems=[{"name": "Spark", "level": 1, "quality": 0}])  # half done
        return True

    search.blocks = interrupted_pass
    search.build.objective.escalate = lambda stats: False  # keep the weights, so scores compare
    search.run(time.perf_counter() + 0.3)
    assert search.build.score >= start - 1e-9


def test_a_search_that_raises_requirements_keeps_its_progress_and_ends_cleanly(engine):
    """Raising requirement weights trades score for requirements on purpose, so the start no longer
    bounds the result; with no snapshot kept yet, the search used to crash (max() of nothing)."""
    import time

    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark", ascendancies=["Stormweaver"], floors={"life": 50000})
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
    search.setup(quests=False)
    search.blocks = lambda deadline: True  # passes that "improve" but never reach the far-off Life floor
    search.run(time.perf_counter() + 0.5)
    assert search.escalations >= 1


def test_finalize_never_trades_a_requirement_for_damage(engine):
    """The score lets a build trade a small shortfall for enough damage; the delivered build (finalize's
    feasible_first) never ends further from feasible than it started."""
    import time

    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark", ascendancies=["Stormweaver"])
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0]
    for feasible_first in (False, True):
        search = Search(engine, None, make_payload(ctx.request), skeleton.as_dict(), 0)
        search.setup(quests=False)
        main = next(g for g in search.build.groups() if g["main"])
        search.build.set_group(index=main["index"], gems=[{"name": "Spark", "level": 15, "quality": 0}])
        start_dps = search.build.objective.dps(search.build.stats)

        def stronger_but_short(deadline: float) -> bool:
            search.build.set_group(index=main["index"], gems=[{"name": "Spark", "level": 20, "quality": 20}])
            return True

        search.blocks = stronger_but_short
        search.build.objective.escalate = lambda stats: False
        # Stand-in for a floor the stronger build misses: anything above the start's damage is short
        search.shortfall = lambda stats: 1.0 if search.build.objective.dps(stats) > start_dps * 1.0001 else 0.0
        search.run(time.perf_counter() + 0.3, escalate=False, feasible_first=feasible_first)
        end_dps = search.build.objective.dps(search.build.stats)
        assert (end_dps == pytest.approx(start_dps)) if feasible_first else (end_dps > start_dps)
