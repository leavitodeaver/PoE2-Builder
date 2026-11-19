"""SEARCH29 (MECHC40's two search-scoring inconsistencies; the manager's 18 Nov 17:55 ruling: "fix the scoring, don't
loosen the tests: commits and what-ifs must compute the same way, and the seed snapshot comes after settling").
(a) a committed build's added DPS (the bridge's LedgerAddedDPS) is summed from its own output as a what-if's is;
(b) Search.settle_still resolves until nothing moves, so a seed's start snapshot is a settled build."""
from __future__ import annotations

import dataclasses
import faulthandler
import math
from types import SimpleNamespace

import pytest

from builder.data import GameData
from builder.request.schema import BuildRequest, Choices
from builder.search import models
from builder.search.context import Context, make_payload
from builder.search.inner import Search
from builder.search.objective import ADDED_FIELD
from builder import clock
from builder.search.skeletons import enumerate_skeletons


def test_settle_still_resolves_until_nothing_moves_and_stops_at_its_limit():
    moves = iter([True, True, True, False, True])
    search = SimpleNamespace(SETTLE_ROUNDS=Search.SETTLE_ROUNDS, resolve=lambda: next(moves))
    assert Search.settle_still(search) == 3  # (the fourth resolve moved nothing: stop there)
    search = SimpleNamespace(SETTLE_ROUNDS=4, resolve=lambda: True)  # (never still: bounded)
    assert Search.settle_still(search) == 4


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


def test_a_committed_build_sums_its_added_sources_from_its_own_output_as_a_what_if_does(engine):
    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    assert ADDED_FIELD in build.objective.fields
    # a field-following source (as Thorns' or the Gas Arrow cloud's) and a fixed one, beside the build's own models
    follow = models.added_input("Search29Follow", 0.0, field="TotalDPS", factor=0.25)
    fixed = models.added_input("Search29Fixed", 12.5)
    build.call("setAssumptions", mods=[*(search.assumptions.mods or []), follow, fixed], fields=[])
    committed = build.call("stats", fields=[ADDED_FIELD, "TotalDPS"])
    what_if = build.call("nodeDeltas", cases=[{}], fields=[ADDED_FIELD, "TotalDPS"])["results"][0]
    print("PROBE added", committed, what_if)
    assert committed["TotalDPS"] > 0
    assert committed[ADDED_FIELD] == what_if[ADDED_FIELD]  # (the same sum, from the same output)
    base = build.call("setAssumptions", mods=list(search.assumptions.mods or []), fields=[ADDED_FIELD])["stats"]
    build.call("setAssumptions", mods=[*(search.assumptions.mods or []), follow, fixed], fields=[])
    # (the follower read from the output in hundredths, not its stored 0; the build's own sources as before)
    assert committed[ADDED_FIELD] - number(base.get(ADDED_FIELD)) == pytest.approx(
        math.floor(committed["TotalDPS"] * 0.25 * 100 + 0.5) / 100 + 12.5, abs=1e-6)
    # the score reads it: committed and what-if score the same
    build.refresh()
    assert build.objective.dps(build.stats) == pytest.approx(build.objective.dps(
        {**build.stats, **build.node_deltas([{}])[0][1]}), rel=1e-12)
    # no added source: no field (Objective.dps falls back to added_dps, 0)
    build.call("setAssumptions", mods=[m for m in search.assumptions.mods or []
                                       if not (m.get("kind") == "Derived" and m.get("fn") in models.ADDED_FNS)], fields=[])
    assert ADDED_FIELD not in build.call("stats", fields=[ADDED_FIELD])
    assert ADDED_FIELD not in build.call("nodeDeltas", cases=[{}], fields=[ADDED_FIELD])["results"][0]


def number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# Alloys (SEARCH29; the manager's 18 Nov 20:25 ruling: DPS at the mean over the lost modifier, floors at the worst) ------

def test_alloy_rows_follow_the_item_class_and_the_mods_level():
    from builder.search import alloys

    mods = {"AlloyCastSpeedGloves1": {"id": "AlloyCastSpeedGloves1", "level": 45, "type": "Suffix"},
            "AlloyElementalPenetration1": {"id": "AlloyElementalPenetration1", "level": 45, "type": "Prefix"},
            "AlloyBellLimit1": {"id": "AlloyBellLimit1", "level": 65, "type": "Suffix"}}
    gloves = {"type": "Gloves", "subType": "Evasion"}
    assert [a for a, _ in alloys.rows_for(gloves, 82, mods)] == ["Swift Alloy", "Prismatic Alloy"]
    assert alloys.rows_for(gloves, 40, mods) == []  # (both mods are level 45)
    warstaff = {"type": "Staff", "subType": "Warstaff"}
    assert alloys.item_class(warstaff) == "Quarterstaff" and [a for a, _ in alloys.rows_for(warstaff, 82, mods)] == [
        "The Runefather's Alloy"]
    assert alloys.rows_for({"type": "Staff"}, 82, mods) == []  # (a Staff isn't a Quarterstaff)
    # every table row names a real Alloy mod id of PoB's (checked against the game data in the real-PoB test below)
    assert len(alloys.ALLOYS) == 48 and len({a for a, _ in alloys.ALLOYS.values()}) == 13


def test_alloy_mean_scale_and_typical_outcome():
    from builder.search import alloys

    dps = [100.0, 80.0, 120.0]
    assert alloys.mean_scale(dps, 1) == pytest.approx(100.0 / 80.0)
    assert alloys.mean_scale([0.0, 5.0], 0) == 1.0
    assert alloys.typical(dps) == 0
    assert alloys.alloy_key("A\nSockets: S S\nRune: Iron Rune\nB") == "A\nB"


def test_the_objective_scales_dps_by_the_alloy_field_only():
    from builder.search.alloys import ALLOY_FIELD
    from builder.search.objective import alloy_scale

    assert alloy_scale({}) == 1.0 and alloy_scale({ALLOY_FIELD: 1.25}) == 1.25 and alloy_scale({ALLOY_FIELD: 0}) == 1.0


def test_an_item_what_if_swaps_its_slots_alloy_factor():
    from builder.search.alloys import ALLOY_FIELD
    from builder.search.state import Build

    seen = []
    build = SimpleNamespace(alloy_raws={"G\nX": 1.5}, equipped={
        "Gloves": SimpleNamespace(raw="G\nX", alloy="Swift Alloy", alloy_scale=1.5),
        "Ring 1": SimpleNamespace(raw="R", alloy="Swift Alloy", alloy_scale=1.2)},
        reassume=None, _deltas=lambda method, cases: seen.extend(cases) or [(0.0, {})] * len(cases))
    build.alloy_product = lambda skip=None: Build.alloy_product(build, skip)
    Build.item_deltas(build, [{"slot": "Gloves", "raw": "Plain"}, {"slot": "Gloves", "raw": "G\nSockets: S\nX"},
                              {"slot": "Boots", "raw": "B"}])
    assert [c["extra"][ALLOY_FIELD] for c in seen] == pytest.approx([1.2, 1.2 * 1.5, 1.5 * 1.2])


def test_build_rare_offers_an_alloy_at_its_mean_dps_and_worst_floors(engine):
    from builder import clock
    from builder.search import alloys
    from builder.search.alloys import ALLOY_FIELD

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build, gear = search.build, search.gear
    # (every row is a mod of PoB's but Adaptive's Sceptre line, which PoB's ModItem lacks: not offered there)
    assert set(alloys.ALLOYS) - set(gear.alloy_mods) <= {"AlloyPuppetMasterChance1"}, set(alloys.ALLOYS) - set(gear.alloy_mods)
    search.ctx.alloy_prices = {}
    plain = gear.build_rare("Gloves", clock.now() + 120)
    assert plain is not None and not plain.alloy  # (no Alloy price: no offer)
    search.ctx.alloy_prices = {name: 0.001 for name in {a for a, _ in alloys.ALLOYS.values()}}
    gear.lam = 0.0
    # the setup-stage build is short of its floors, so a lost resistance makes every worst outcome lose (POE2T4310: the
    # plain gloves are three resistance / attribute suffixes): the trial on a rare whose one modifier is worth nothing
    from builder.search.gear import affix_limits
    from builder.search.rares import rare_text

    base = search.ctx.bases_by_name[plain.base]
    pools, level = gear.pools, gear.pools.item_level
    pool = pools.pool(base["name"])
    empty = gear.deltas("Gloves", [rare_text("Gloves", base, level, [])])[0][0]
    groups = {m["group"] for m in gear.alloy_mods.values()}
    scores = gear.deltas("Gloves", [rare_text("Gloves", base, level, [a]) for a in pool])
    worthless = [a for (s, _), a in zip(scores, pool) if abs(s - empty) < 1e-9 and a.group not in groups]
    print("PROBE worthless", [a.lines for a in worthless][:5])
    if not worthless:
        pytest.skip("no worthless gloves modifier on this build")
    chosen = [worthless[0]]
    option = gear.alloy_trial("Gloves", base, plain, level, pools, affix_limits(base), chosen, empty, 1e9,
                              clock.now() + 120)
    print("PROBE alloy", option.alloy if option else None, option.alloy_scale if option else None,
          option.alloy_report if option else None)
    assert option is not None and option.alloy  # (Prismatic's penetration or Swift's cast speed, for nothing lost)
    assert option.alloy_scale == pytest.approx(1.0)  # (one outcome: the mean is the worst)
    assert len(option.alloy_report) == 3 and "Worst case" in option.alloy_report[2]
    assert option.describe()["alloy"]["name"] == option.alloy and not any(l in option.raw for l in worthless[0].lines)
    assert build.alloy_raws[alloys.alloy_key(option.raw)] == option.alloy_scale
    gear.put("Gloves", dataclasses.replace(option, alloy_scale=1.25))  # (a factor as two unequal outcomes give)
    assert build.stats[ALLOY_FIELD] == pytest.approx(1.25)
    bare = {k: v for k, v in build.stats.items() if k != ALLOY_FIELD}
    assert build.objective.dps(build.stats) == pytest.approx(build.objective.dps(bare) * 1.25)
    # an item what-if for the slot replaces its factor: the plain gloves read 1.0
    other = build.item_deltas([{"slot": "Gloves", "raw": plain.raw}])[0]
    assert other[1][ALLOY_FIELD] == pytest.approx(1.0)


# The seeded curse trial (SEARCH29; the manager's 18 Nov 20:25 YES) -------------------------------------------------------

def test_curse_damage_supports_are_read_from_their_own_text():
    from builder.search.gems import CURSE_DAMAGE

    assert CURSE_DAMAGE.search("Supports Curse skills you cast yourself, causing them to trigger Doom Blast on Cursed enemies")
    assert CURSE_DAMAGE.search("Supports Curse Skills. Supported Skills create an area ... and spawns Witchtoads in that area.")
    assert CURSE_DAMAGE.search("Supports Curse skills. Supported skill Trigger Profane Decay on each target they curse to "
                               "inflict a damaging Debuff.")
    assert not CURSE_DAMAGE.search("Supports Curse Skills, magnifying their power.")
    assert not CURSE_DAMAGE.search("Supports Curse Skills you cast yourself. Supported Skills will consume Power Charges")
    assert not CURSE_DAMAGE.search("Supports Spells. Supported Spells trigger something")  # (not a curse support)


def test_the_seeded_curse_trial_adds_a_curse_with_a_damage_support_only_if_higher(engine, monkeypatch):
    """(2b75b's own code can't hold a new group: 13 player groups against the game's 9, MAX_GEM_GROUPS; probe POE2T4315.
    The gate's searches build their own groups, so the trial is tested on a fresh skeleton.)"""
    from builder.search import gems as gems_module
    from builder.search.gems import CURSE_LABEL

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"], require=Choices()))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    seeds = search.gems.curse_damage_supports()
    print("PROBE seeds", seeds)
    assert "Impending Doom" in seeds
    monkeypatch.setattr(gems_module, "CURSE_SEEDS", False)  # (POE2_SEARCH_CURSE_SEEDS=0: the switch turns it off)
    assert search.gems.curse_round(clock.now() + 120) is False
    monkeypatch.setattr(gems_module, "CURSE_SEEDS", True)
    monkeypatch.setattr(gems_module, "CURSE_SECONDS", 60.0)
    assert search.gems.curse_round(clock.now() + 120) is False  # (only the task the run flags: optimize_task's curses)
    search.gems.curse_task = True
    before = build.score
    kept = search.gems.curse_round(clock.now() + 120)
    groups = [g for g in build.groups() if g["label"] == CURSE_LABEL]
    print("PROBE curse", kept, before, build.score, [[n["name"] for n in g["gems"]] for g in groups])
    if kept:
        assert build.score > before and len(groups) == 1
    else:
        assert build.score == pytest.approx(before) and not groups
    assert search.gems.curse_round(clock.now() + 120) is False  # (once per line)


def test_alloys_are_off_by_default_and_the_switch_turns_them_on(monkeypatch):
    """The manager's 19 Nov 02:50 ruling: off by default (their 3-run set can't measure higher); the switch stays."""
    from builder.search import alloys

    monkeypatch.delenv("POE2_SEARCH_ALLOYS", raising=False)
    assert not alloys.enabled()
    monkeypatch.setenv("POE2_SEARCH_ALLOYS", "1")
    assert alloys.enabled()



def test_the_seeded_curse_trial_is_on_by_default_and_the_switch_turns_it_off():
    """SEARCH30: its 3-run 2b75b set measured higher (gear 55b40650), so it is on unless POE2_SEARCH_CURSE_SEEDS=0."""
    from builder.search import gems

    assert gems.curse_seeds_on({}) and gems.curse_seeds_on({"POE2_SEARCH_CURSE_SEEDS": "1"})
    assert not gems.curse_seeds_on({"POE2_SEARCH_CURSE_SEEDS": "0"})
