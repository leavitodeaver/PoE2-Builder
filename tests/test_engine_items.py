"""Item mechanics through a real engine: off-hand weapons, flask and charm slots. Boots one
PoB engine for the module (a few seconds) and needs the game-data dump."""

from __future__ import annotations

import faulthandler
import random
import re

import pytest

from builder import clock
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
    # With enough charge generation (and Mana spent faster than it fills: a Mana flask's effect ends at full Mana,
    # MECHC4) the same flask counts, and its slot is switched on
    gear.charges = gear.charges.__class__(flask=10, mana_cost=10_000.0)
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
    from builder.search import bosses
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
        # (a charge count may be there only as the resolver's own, from the build's sources: not the seed's 6)
        active = search.conditions.active.get(var)
        if var in bosses.config_vars():  # (MECH50: the boss preset's own resistances replace the seed's -100%)
            assert config[var] == bosses.config_vars()[var], (var, config.get(var))
            continue
        assert var not in config or (active is not None and config[var] == active["value"]), (var, config.get(var))
    changed = {var for var in set(config) | set(fresh) if config.get(var) != fresh.get(var) and not var.startswith("quest")}
    from builder.search import boss_hits
    for var in boss_hits.config_vars():  # (MECHC6: the boss's hits per damage type; MECHD5: the default boss's own,
        assert config.get(var) == bosses.config_vars()[var], (var, config.get(var))  # carried by bosses.config_vars)
    assert changed - set(bosses.config_vars()) - set(boss_hits.config_vars()) <= set(search.conditions.active)


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
    """The score lets a build trade a small shortfall for enough damage; no search ends on such a build when it saw
    one that met every requirement (keep_feasible; before 7 Nov only finalize's feasible_first held that)."""
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
        assert end_dps == pytest.approx(start_dps)


def test_finalize_sweeps_supports_on_full_dps_despite_the_search_shortlist(engine, monkeypatch):
    """The search's shortlist was ranked on the main skill's DPS, blind to a skill a support grants (Deadly Resolve's
    slash): finalize sweeps every support on Full DPS first instead of moving within it (2b75d, POE2T207 vs 231-232)."""
    from builder.search import inner
    from builder.search.inner import Search, finalize_task
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark", ascendancies=["Stormweaver"])
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    payload = make_payload(ctx.request)
    search = Search(engine, None, payload, skeleton, 0)
    search.setup(quests=False)
    state = search.state()
    state["shortlist"] = ["Fork"]  # a shortlist from the search: without the sweep only its moves are tried
    monkeypatch.setattr(Search, "run", lambda self, *args, **kwargs: None)  # only finalize's own support step runs
    out = finalize_task(engine, None, payload, skeleton, state, inner.FINALIZE_SWEEP_MIN + 10)
    # (counted over finalize's rounds: a sweep that finds a move is followed by shortlist rounds, which the census's
    # last-round fields describe)
    assert out["support_census"]["sweeps"] >= 1 and out["support_census"]["swept_cases"] > 50


def test_a_step_that_ignores_the_deadline_is_stopped_on_time(engine, monkeypatch):
    """Steps used to finish their own trials past the task's deadline, so a 600 s gate run took about 966 s
    (8 Nov). A step that never looks at the clock is now stopped at its next evaluation a little past the
    deadline, the build goes back to where the last step left it, and the task says which step ran over."""
    from builder import clock
    from builder.search.gems import Gems
    from builder.search.inner import STOP_GRACE_MIN, optimize_task
    from builder.search.skeletons import enumerate_skeletons

    def endless(self, deadline, *args):
        until = clock.now() + 60
        while clock.now() < until:
            self.build.refresh()

    # Gear goes first and finishes at once (the stop is armed only after a finished step); supports never stop
    monkeypatch.setattr(Gear, "gear_round", lambda self, deadline, full=True: False)
    monkeypatch.setattr(Gems, "optimize_supports", endless)
    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0]
    payload = make_payload(ctx.request)
    started = clock.now()
    result = optimize_task(engine, None, payload, skeleton.as_dict(), 8, seed=1)
    took = clock.now() - started
    assert took < 8 + STOP_GRACE_MIN + 5, took
    assert "supports" in result["overrun"] and result["overrun"]["supports"] < STOP_GRACE_MIN + 2, result["overrun"]
    assert result["score"] > float("-inf") and result["state"]["equipped"]


def test_a_tree_step_the_hard_stop_cuts_keeps_the_passives_it_took(engine, monkeypatch):
    """The stop goes back to the tree's last kept passive (Search.checkpoint), not to the step's start: a probe's tree
    step cut by the stop lost all it had allocated, and the five lines of a gate run started round 1 at 7.8K
    (POE2T225, x0.46)."""
    from builder import clock
    from builder.search.gems import Gems
    from builder.search.inner import STOP_GRACE_MIN, optimize_task
    from builder.search.skeletons import enumerate_skeletons
    from builder.search.tree import Tree

    grow, seen = Tree.grow, {}

    def grow_then_hang(self, deadline):
        if "grown" in seen:
            return grow(self, deadline)
        seen["before"] = self.build.score
        grow(self, min(deadline, clock.now() + 3))
        seen["grown"] = self.build.score
        until = clock.now() + 60  # then a long what-if that never looks at the clock
        while clock.now() < until:
            self.build.refresh()

    monkeypatch.setattr(Gear, "gear_round", lambda self, deadline, full=True: False)
    monkeypatch.setattr(Gems, "optimize_supports", lambda self, deadline, *args: False)
    monkeypatch.setattr(Tree, "grow", grow_then_hang)
    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0]
    started = clock.now()
    result = optimize_task(engine, None, make_payload(ctx.request), skeleton.as_dict(), 8, seed=1)
    assert clock.now() - started < 8 + STOP_GRACE_MIN + 5
    assert "tree" in result["overrun"], result["overrun"]
    assert seen["grown"] > seen["before"], seen
    assert result["score"] >= seen["grown"] - 1e-9, (result["score"], seen)


def test_a_whole_run_fits_the_time_it_was_given(engine, monkeypatch):
    """The user's minutes are the whole run, finalize's export and reload check included. Here the pool gets fewer
    workers than asked for (in the PC queue it gets the LuaJIT slots still free): planned on the workers asked for,
    the run's searches went in two waves and every stage took twice its time (a 600 s gate run, about 966 s)."""
    import time

    from builder.pob import slots
    from builder.search.run import optimize

    monkeypatch.setattr(slots, "free", lambda: 2)

    request = BuildRequest(class_name="Huntress", ascendancies=["Ritualist"], level=90, main_skill="Explosive Spear",
                           budget={"gear_tier": "endgame"}, objective="boss")
    started = time.perf_counter()
    result = optimize(request, workers=3, seconds=45)
    took = time.perf_counter() - started
    assert result["status"] != "invalid" and result.get("code")
    assert result["workers"] == 2  # planned on the pool it got
    assert took < 45 * 1.1 + 5, took  # 5 s for the two engines' start


def test_a_lines_requirement_raises_go_on_with_its_state(engine):
    """A line's next task carries on its raise count: counted from 0 again, every task's second raise cleared and regrew
    the passive tree (Search._ascend), so gate 4's lines spent each task regrowing it (POE2T194-197)."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    payload = make_payload(ctx.request)
    search = Search(engine, None, payload, skeleton, 0)
    search.setup(quests=False)
    search.escalations = 2
    state = search.state()
    later = Search(engine, None, payload, skeleton, 1)
    later.load(state)
    assert later.escalations == 2


def test_a_full_gear_round_stopped_by_its_time_goes_on_from_that_slot(engine):
    """A full gear round capped by its share of the time (inner.GEAR_SHARE) remembers the slot it stopped at, the line's
    next task carries it, and the next full round starts there: on gate 4's 2b75b one full round took all of a 240 s
    task, and no other step ran (POE2T202)."""
    from builder import clock
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    payload = make_payload(ctx.request)
    search = Search(engine, None, payload, skeleton, 0)
    search.setup(quests=False)
    first = list(search.gear.slots)[0]
    search.gear.gear_round(clock.now() - 1.0, full=True)  # out of time before the first slot
    assert search.gear.resume == first
    later = Search(engine, None, payload, skeleton, 1)
    later.load(search.state())
    assert later.gear.resume == first
    later.gear.gear_round(clock.now() - 1.0, full=False)  # a shortlist round leaves it alone
    assert later.gear.resume == first


def test_a_support_trial_taken_back_below_where_it_was_restores_the_build(engine):
    """Setting a group's old gems again after a trial can leave the build lower than before it (the supports step lost
    score on every line of POE2T202's first focus round): then the snapshot goes back, counted in the reverts."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build = search.build
    group = build.main_group()
    names = [g["name"] for g in group["gems"]]
    snap = build.snapshot()
    search.gems.take_back(group["index"], names, group, snap["score"] + 1.0, snap)
    assert build.reverts.get("support take-back") == 1
    assert abs(build.score - snap["score"]) < 1e-6


def test_an_infused_stat_set_is_no_choice_without_a_source_and_the_result_says_so(engine):
    """Spark's Cold-Infused cast needs a Cold Infusion from a Remnant: chosen by score alone it counted every cast as
    Infused. With no source in the build it's no choice, and the assumptions name what isn't counted."""
    from builder.search.conditions import stat_set_needs
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    assert stat_set_needs("Cold-Infused") == [None] and stat_set_needs("Verisium-Infused") == []
    ctx = context(main_skill="Spark")
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    group = search.build.main_group()
    labels = [label for skill in group["skills"] for label in skill.get("statSets") or []]
    assert "Cold-Infused" in labels
    assert not search.conditions.allows("Cold-Infused") and not search.conditions.allows("Cold-Infused", ("x",))
    _, notes = search.assumptions.wanted()
    assert any("Cold-Infused cast not counted" in n["label"] for n in notes)


def test_an_infused_stat_set_counts_at_the_share_of_casts_its_source_covers(engine):
    """With Frost Bomb in the build (Cold Infusions, one per 6 s cooldown) Spark's Cold-Infused set is a choice, its
    damage counted as plain + share x the Infused gain, share = Infusions per second / casts per second."""
    from builder.search.assumptions import REMNANT_SOURCES
    from builder.search.inner import Search
    from builder.search.objective import main_dps
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark")
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build, assumptions = search.build, search.assumptions
    build.set_group(gems=[{"name": "Frost Bomb", "level": 20, "quality": 0}])
    search.gems.choose_active_skill()
    speed = build.call("stats", fields=["Speed"])["Speed"]
    infused = assumptions.infused
    if infused is None:  # the plain cast won: the reason names the source and the share
        assert "Frost Bomb" in assumptions.infused_skipped["Cold-Infused"]
        return
    assert infused["label"] == "Cold-Infused" and "Frost Bomb" in infused["basis"]
    assert infused["share"] == pytest.approx(min(1.0, REMNANT_SOURCES["Frost Bomb"][0] / speed), rel=0.05)
    blended = main_dps(build.stats)
    _, notes = assumptions.wanted()
    assert any("share of casts that are Cold-Infused" in n["label"] for n in notes)
    # (MECH46: pressing Frost Bomb for its Infusions takes its cast time from Spark's casts)
    busy = next(n["value"] for n in notes if n["label"] == "Time pressing Remnant skills")
    assert busy > 0
    assumptions.infused = None
    assumptions.apply()
    raw = main_dps(build.stats)  # every cast Infused, PoB's own number, no press time
    assert raw > blended and blended / raw == pytest.approx(infused["factor"] * (1 - busy), rel=0.02)


def test_a_skill_tried_on_an_infused_set_and_not_chosen_goes_back_to_its_plain_cast(engine, monkeypatch):
    """A stat set stays on its skill when another skill of the group becomes the calculated one, and Full DPS counts
    it: 2b75e (POE2T244) delivered Mana Flare as the main skill with Ice Nova left Cold-Infused from its trial, every
    cast Infused with no source (2,267,697 against 741,063 plain). Here the lowest DPS scores best, so Mana Flare wins
    and Spark's Infused trial loses."""
    from builder.search.inner import Search
    from builder.search.objective import main_dps
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark")
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build, assumptions = search.build, search.assumptions
    main = build.main_group()
    build.set_group(index=main["index"], gems=[{"name": "Spark", "level": 20, "quality": 0},
                                               {"name": "Mana Flare", "level": 20, "quality": 0}])
    build.set_group(gems=[{"name": "Frost Bomb", "level": 20, "quality": 0}])
    monkeypatch.setattr(assumptions, "infusion_share", lambda element, ailments: (0.5, "half"))
    monkeypatch.setattr(build.objective, "score", lambda stats: -main_dps(stats))
    search.gems.choose_active_skill()
    group = build.main_group()
    assert group["mainActiveSkill"] == 2 and assumptions.infused is None and "Cold-Infused" in assumptions.infused_skipped
    full = build.call("stats", fields=["FullDPS"])["FullDPS"]
    chosen = group["mainActiveSkill"]
    build.set_group(index=group["index"], mainActiveSkill=1, statSet=1)  # Spark on its plain cast, explicitly
    build.set_group(index=group["index"], mainActiveSkill=chosen)
    assert build.call("stats", fields=["FullDPS"])["FullDPS"] == pytest.approx(full)


def test_a_support_sweep_where_nothing_changes_the_score_keeps_no_shortlist(engine, monkeypatch):
    """Explosive Transmutation's first sweep ran before that build's Remnant source (Into the Breach) was in: every support scored exactly the
    same, and the shortlist kept was the alphabet's first names (POE2T206, 216). Such a sweep keeps none, so the
    next round sweeps again; a sweep that ranks something keeps its shortlist."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    monkeypatch.setattr(build, "gem_deltas", lambda cases, **kw: [(build.score, {}) for _ in cases])
    assert not gems.supports_round(full_sweep=True)
    assert gems.shortlist is None
    monkeypatch.setattr(build, "gem_deltas", lambda cases, **kw: [(build.score - 0.1 * i, {}) for i, _ in enumerate(cases)])
    gems.supports_round(full_sweep=True)
    assert gems.shortlist


def test_only_a_skill_that_spends_glory_switches_on_the_glory_condition(engine, monkeypatch):
    """The Glory conditions (Call Upon the Deep, Thrill of the Fight; GlorySkill until COV14) matched any active skill
    whose description says "Glory", so skills that build or mention Glory without spending it switched them on: an
    overcount. They need the usage rule's active_skill_requires_X_glory. Since COV14 they are shares from the build's
    Glory skill uses (models.glory_uses), not on at one use per 6 s."""
    from builder.search import models
    from builder.search.assumptions import Assumptions
    from builder.search.usage import GLORY, usage_rules

    ctx = context(main_skill="Explosive Spear")
    engine.call("newBuild")
    build = Build(engine, ctx, Objective(ctx))
    rules = usage_rules(build)
    named = [n for n, g in ctx.all_gems.items() if re.search(r"\bGlory\b", g.get("description") or "")
             and not g.get("support")]
    spenders = [n for n in named if GLORY in ((rules.get(n) or {}).get("stats") or {})]
    others = [n for n in named if n not in spenders]
    print("Glory spenders:", spenders, "| mention Glory only:", others)
    assert spenders
    assumptions = Assumptions(build)
    glory = {"GloryUsesPast6s", "GloryBuff6s"}
    # this bare build makes no Glory (models.glory_uses): nothing counted
    assert not glory & set(assumptions.ledger_conditions([{"name": spenders[0], "disabled": False}])[0])
    monkeypatch.setattr(models, "glory_uses", lambda names, stats, main: (1 / 7, "one per 7 s"))
    assert glory <= set(assumptions.ledger_conditions([{"name": spenders[0], "disabled": False}])[0])
    assert assumptions.ledger_shares["GloryUsesPast6s"] == round(600 / 7)  # (was off: slower than one per 6 s)
    for name in others:
        assert not glory & set(assumptions.ledger_conditions([{"name": name, "disabled": False}])[0])


def test_ledger_corrections_on_runes_reach_pob(engine):
    """builder/ledger/items.py through a real engine: a rune line PoB can't parse ("10% chance for Slam Skills ... an
    additional Aftershock", Ancient Rune of the Titan) arrives as its ledger stand-in, which PoB parses; a rune line a
    `pob_wrong` entry corrects (Legacy of Keeper of the Arc's "Take 20% less Damage over time") stays switched off
    after PoB rebuilds the rune lines, and its correction is parsed."""
    from builder.search.rares import with_runes

    ctx = context(main_skill="Boneshatter")
    engine.call("newBuild")
    mace = with_runes(f"Rarity: Rare\nOptimizer Weapon 1\n{base_of(ctx, 'Two Hand Mace')['name']}\nItem Level: 82\n"
                      "Implicits: 0\n+10 to Strength\nSlam Skills have 16% more DPS while wielding a Mace\n"
                      "Slam Skills have 12% more DPS while Shapeshifted", ["Ancient Rune of the Titan"] * 2)
    engine.call("equip", slot="Weapon 1", raw=mace, fields=[])
    helmet = with_runes(f"Rarity: Rare\nOptimizer Helmet\n{base_of(ctx, 'Helmet')['name']}\nItem Level: 82\n"
                        "Implicits: 0\n+10 to Strength", ["Legacy of Keeper of the Arc"])
    engine.call("equip", slot="Helmet", raw=helmet, fields=[])
    mods = engine.call("itemMods")
    weapon = {l["line"]: l for l in mods["Weapon 1"]["lines"]}
    # an Aftershock is a second hit, so a second ailment application, not a bigger one (AUDIT8 A8-1): "more DPS"
    assert weapon["Slam Skills have 20% more DPS"]["supported"] and weapon["Slam Skills have 20% more DPS"]["kind"] == "explicit"
    assert weapon["Slam Skills have 16% more DPS while wielding a Mace"]["supported"]  # aftershock-chance-mace's form
    assert weapon["Slam Skills have 12% more DPS while Shapeshifted"]["supported"]  # aftershock-chance-shapeshift's
    head = {l["line"]: l for l in mods["Helmet"]["lines"]}
    assert head["10% less Damage over Time taken"]["supported"] and head["10% less Damage taken from Hits"]["supported"]
    xml = engine.call("exportXml")["xml"]
    off = [l for l in xml.splitlines() if "Take 20% less Damage over time" in l]
    assert off and all("{disabled}" in l for l in off)


def test_a_staff_with_a_focus_requires_instruments_of_power(engine):
    """A skeleton whose off hand a passive allows (game_rules.yaml off_hand.passive_exceptions) has the search
    allocate that passive, and its off-hand slot takes the Focus."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Fireball", offhand="focus", weapon_type="Staff"))
    skeleton = enumerate_skeletons(Context(payload), ["Fireball"])[0][0]
    assert skeleton.requires_passive == "Instruments of Power"
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert "Instruments of Power" in search.tree.required_names
    assert search.gear.slots["Weapon 2"].types == ("Focus",)


def test_a_talisman_holds_only_a_non_unique_sceptre_with_lord_of_the_wilds(engine):
    """Lord of the Wilds lets a Talisman hold "a non-Unique Sceptre" (game_rules.yaml off_hand.passive_exceptions):
    the search allocates the keystone and the off-hand slot offers Sceptres but none of the unique ones."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Furious Slam", offhand="weapon", ascendancies=["Shaman"],
                                        budget={"ssf": True}))  # SSF: uniques offered unpriced
    ctx = Context(payload)
    skeleton = enumerate_skeletons(ctx, ["Furious Slam"])[0][0]
    assert (skeleton.weapon, skeleton.offhand, skeleton.requires_passive) == ("Talisman", "Sceptre", "Lord of the Wilds")
    assert any(o.unique for o in ctx.item_options["Sceptre"])  # the game has unique sceptres to keep out
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert "Lord of the Wilds" in search.tree.required_names
    gear = search.gear
    assert gear.slots["Weapon 2"].types == ("Sceptre",) and gear.slots["Weapon 2"].non_unique
    options = gear.options("Weapon 2")
    assert options and not any(o.unique for o in options)
    sceptre = next(o for o in ctx.item_options["Sceptre"] if o.unique)
    assert gear.slot_for(sceptre) is None
    held = search.build.equipped.get("Weapon 2")
    assert held is None or not held.unique


def test_spear_throw_from_a_plain_spear_is_the_main_skill(engine):
    """A Spear Throw skeleton granted by any Spear ("base:Spear"): the setup holds a non-unique spear and PoB's
    default-attack group for Spear Throw becomes the main group."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spear Throw", ascendancies=["Amazon"]))
    skeleton = next(s for s in enumerate_skeletons(Context(payload), ["Spear Throw"])[0] if s.granted_by == "base:Spear")
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    weapon = search.build.equipped.get("Weapon 1")
    assert weapon is not None and weapon.type == "Spear" and not weapon.unique
    assert not any("didn't appear" in p for p in search.problems), search.problems
    main = [g for g in search.build.groups() if g["main"]]
    assert main and any(s["name"] == "Spear Throw" for s in main[0]["skills"])


@pytest.mark.parametrize("skill, ascendancy, bases", [
    ("Wither", "Infernalist", {"Primordial Wand"}),  # no unique grants it
    ("Chaos Bolt", "Infernalist", {"Critical Wand", "Frigid Wand", "Torture Wand", "Withered Wand"}),
    # (PoB's gem is "Skeletal Warrior"; the item line and PoB's grantedEffect say "Skeletal Warrior Minion", COV5)
    ("Skeletal Warrior Minion", "Infernalist", {"Aromatic Sceptre", "Devotional Sceptre", "Devouring Sceptre",
                                                "Hallowed Sceptre", "Lupine Sceptre", "Ochre Sceptre", "Pious Sceptre",
                                                "Rattling Sceptre"}),
])
def test_a_skill_some_wand_bases_grant_is_the_main_skill(engine, skill, ascendancy, bases):
    """A "bases:Wand" skeleton: the main-hand slot offers only the bases whose implicit grants the skill, the setup
    holds one, and PoB's group for the granted skill becomes the main group."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=skill, ascendancies=[ascendancy]))
    skeleton = next(s for s in enumerate_skeletons(Context(payload), [skill])[0] if s.granted_by.startswith("bases:")
                    and s.weapon == s.granted_by.split(":")[1])  # (the main hand; the off hand: the Malice test below)
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert search.gear.slots["Weapon 1"].bases == tuple(sorted(bases))
    options = search.gear.options("Weapon 1")
    assert options and {o.base for o in options} <= bases and not any(o.unique for o in options)
    weapon = search.build.equipped.get("Weapon 1")
    assert weapon is not None and weapon.base in bases
    assert not any("didn't appear" in p for p in search.problems), search.problems
    main = [g for g in search.build.groups() if g["main"]]
    assert main and any(s["name"] == skill for s in main[0]["skills"])


def test_compose_requiem_from_the_last_lament_is_the_main_skill(engine):
    """The Last Lament grants "Compose Requiem", PoB's gem "Requiem" (baseTypeName "Compose Requiem"; its effects are
    "Compose Requiem", the ammo, and "Requiem", the shots): the skeleton holds the unique and PoB's group for it is the
    main group (COV5; before, the search said the name didn't match)."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Compose Requiem", ascendancies=["Witchhunter"]))
    skeleton = enumerate_skeletons(Context(payload), ["Compose Requiem"])[0][0]
    assert skeleton.granted_by == "unique:The Last Lament"
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    weapon = search.build.equipped.get("Weapon 1")
    assert weapon is not None and weapon.unique and weapon.name == "The Last Lament"
    assert not any("didn't appear" in p for p in search.problems), search.problems
    main = [g for g in search.build.groups() if g["main"]]
    names = {s["name"] for s in main[0]["skills"]} if main else set()
    print("main group skills:", names)
    assert "Compose Requiem" in names
    # COVC11: counted at its share, not barred: the press hook caps the group's Requiem shot at 12 / 42 of PoB's rate
    from builder.search.usage import Usage
    gem = next(g["name"] for g in main[0]["gems"] if not g["support"])
    assert gem == "Requiem" and Usage(search.build, None).blockers(gem) == []
    # COVC12: the bridge's usage rule carries the fill rate and duration (before, only the 100 anguish: rate 0, no cap)
    from builder.search.assumptions import usage_rules
    stats = usage_rules(search.build)["Requiem"]["stats"]
    assert {"rapidshot_requiem_number_of_stacks_per_minute", "rapidshot_requiem_active_duration_ms"} <= set(stats)
    # COVC12 (gems-active-batch8.yaml): the shot fires freely (PoB gave it 0 bolts, so speed 0) and the ammo effect
    # deals no damage (PoB scored it as a crossbow attack), so the shot is the calculated skill, with a real rate
    skills = [s["name"] for s in main[0]["skills"]]
    assert skills[(main[0].get("mainActiveSkill") or 1) - 1] == "Requiem"
    rate = search.build.call("useRates", indices=[main[0]["index"]])["rates"][0]
    assert rate["name"] == "Requiem" and rate["speed"] > 0
    cap = search.assumptions.press_caps(main, {main[0]["index"]}).get(main[0]["index"])
    assert cap is not None and cap.how.startswith("anguish: the Requiem up 29%")
    assert abs(cap.uses / rate["speed"] - 12 / 42) < 1e-6
    mods, notes = search.assumptions.main_cap(main[0], "Requiem")
    assert mods and abs(mods[0]["value"] - 100 * (12 / 42 - 1)) < 0.01 and notes[0]["value"] == "x0.286"


def test_an_off_hand_sceptres_skill_can_be_the_main_skill(engine):
    """A Wand + Sceptre skeleton for Malice (Omen Sceptre): the off-hand slot keeps to the granting base and PoB grants
    the skill from Weapon 2 (CalcSetup 1214-1250 adds every slot's grantedSkills; COV5)."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Malice", ascendancies=["Infernalist"]))
    skeleton = next(s for s in enumerate_skeletons(Context(payload), ["Malice"])[0]
                    if s.granted_by == "bases:Sceptre" and s.weapon == "Wand")
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    assert search.gear.slots["Weapon 2"].bases == ("Omen Sceptre",)
    offhand = search.build.equipped.get("Weapon 2")
    assert offhand is not None and offhand.base == "Omen Sceptre"
    assert not any("didn't appear" in p for p in search.problems), search.problems
    main = [g for g in search.build.groups() if g["main"]]
    assert main and any(s["name"] == "Malice" for s in main[0]["skills"])


def test_source_lines_give_a_ranged_unique_line_rolled(engine):
    """COVD1: sourceLines passed a unique's line as written, "Hits Break (30-50) Armour", so the models' and conditions'
    number regexes (conditions.ARMOUR_BREAK_LINE here) missed it. It now gives the rolled line, as PoB's own mod list
    does (itemLib.applyRange at the item's range, PoB's default 0.5 for none)."""
    from builder.search.conditions import ARMOUR_BREAK_LINE

    engine.call("newBuild")
    engine.call("equip", slot="Weapon 1", raw="Rarity: Unique\nWylund's Stake\nSmithing Hammer\nImplicits: 0\n"
                                             "+(10-20) to Strength\nHits Break (30-50) Armour")
    texts = [line["text"] for line in engine.call("sourceLines")["lines"] if line["source"] == "item:Weapon 1"]
    print(texts)
    assert "Hits Break 40 Armour" in texts and "+15 to Strength" in texts
    assert not any("(" in t for t in texts)
    assert any(ARMOUR_BREAK_LINE.search(t) for t in texts)


def test_the_support_rebuild_runs_once_per_task_and_never_lowers_the_score(engine):
    """gems.rebuild_main (2b75d, POE2T645: three of five round-2 tasks stuck where the better set was three supports
    away): after the main group's full sweep, sets rebuilt around the sweep's best new names are kept only if better;
    it runs once per task, and the group keeps at most SUPPORT_SLOTS supports with no two of one family."""
    from builder.search.context import SUPPORT_SLOTS
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build, gems = search.build, search.gems
    assert not gems.rebuild_main(clock.now() + 600)  # no sweep yet: nothing to rebuild from
    gems.supports_round(full_sweep=True)
    assert gems.swept_ranking
    before = build.score
    gems.rebuild_main(clock.now() + 600)
    assert build.score >= before - 1e-9 and gems.rebuilt
    assert not gems.rebuild_main(clock.now() + 600)
    group = build.main_group()
    supports = [g["name"] for g in group["gems"] if g["support"]]
    assert len(supports) <= SUPPORT_SLOTS
    families = [f for n in supports for f in gems.families(n)]
    assert len(families) == len(set(families))


def test_a_restore_gives_back_its_snapshots_score(engine):
    """Build.restore puts back the search's scoring state with the XML (Build.snapshot_state): finalize's supports guard
    ended 0.021-0.033 ln under its snapshot in POE2T730-732 because the uptime weights and assumed mods stayed as the
    swept build had them. A restore counts its drift (inner.restore_drift); the requirement weights stay as they are."""
    from builder.search.inner import Search, restore_drift
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Explosive Spear")
    skeleton = enumerate_skeletons(ctx, ["Explosive Spear"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build = search.build
    snap = build.snapshot()
    build.objective.uptime_factor *= 0.5
    build.objective.life_factor *= 0.8
    build.objective.multipliers["test floor"] = 4.0
    build.refresh()
    assert build.score < snap["score"] - 0.1
    build.restore(snap)
    assert build.score == pytest.approx(snap["score"], abs=1e-6)
    assert build.objective.multipliers["test floor"] == 4.0  # (an escalation isn't undone)
    drift = restore_drift(build)
    assert drift["restores"] >= 1 and drift["drifted"] == 0


def test_a_flask_up_part_of_the_fight_is_weighed_at_its_share(engine):
    """MECHC5's spec (SEARCH5): the slot stays on, Search.part_time lists it, weigh_uptimes weighs it (one recalculation
    with the slot off, then on again) and the flask report says it counted at its share."""
    from builder.search.gear import FLASK_ON, FLASK_WHOLE
    from builder.search.inner import FLASK_VAR, Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark")
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    gear = search.gear
    option = next(o for o in gear.options("Flask 2") if not o.unique)
    info = gear.flask_info([option.raw])[0]
    gear.charges = gear.charges.__class__(flask=1.0, mana_cost=10_000.0)
    one = gear.share("Flask 2", info, option.raw)
    assert one > 0
    gear.charges = gear.charges.__class__(flask=0.5 / one, mana_cost=10_000.0)
    share = gear.share("Flask 2", info, option.raw)
    assert FLASK_ON <= share < FLASK_WHOLE and gear.counts("Flask 2", info, option.raw)
    gear.put("Flask 2", option)
    effects = search.part_time()
    var = f"{FLASK_VAR}Flask 2:{option.name}"
    assert effects[var][0] == pytest.approx(share)
    search.weigh_uptimes()
    assert any(row["var"] == var and row["uptime"] == pytest.approx(share) for row in search.uptime_rows)
    assert search.build.objective.uptime_factor <= 1.0 + 1e-9
    xml = engine.call("exportXml")["xml"]  # switched back on after the recalculation without it
    assert any('name="Flask 2"' in t and 'active="true"' in t for t in re.findall(r"<Slot [^>]*>", xml))
    search.follow_uptimes()
    row = next(r for r in gear.flask_report() if r["slot"] == "Flask 2")
    assert row["counted"] and row["part_time"]


def test_poison_spores_pustule_explosion_is_held_to_one_a_fuse(engine):
    """Poison Spores' Pustule Explosion (COVC12, POE2T1425: PoB 1.2 explosions/s): the game allows one Pustule per enemy,
    bursting after its 15 s fuse, so its damage is held to (1 / fuse) / PoB's rate (MECHB10). The bridge rates the
    Spores skill beside the group's calculated one (useRates actives) and gives PoB's Detonation Time."""
    from builder.search import models
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"], level=90, objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.groups() if g["main"])
    gems = [dict(x, level=20) for x in group["gems"] if not x["support"]] + [
        {"name": "Poison Spores", "support": True, "level": 1, "quality": 0, "enabled": True}]
    build.call("setGroup", index=group["index"], gems=gems)
    group = next(g for g in build.groups() if g["index"] == group["index"])
    spores = next(i for i, s in enumerate(group["skills"], start=1) if s["name"] == "Poison Spores")
    build.call("setGroup", index=group["index"], mainActiveSkill=spores, statSet=2)  # (the Pustule Explosion)
    build.call("setGroup", index=group["index"], mainActiveSkill=1)  # (Lightning Arrow calculated; Spores keeps set 2)
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
    assert models.pustule_groups(found) == [group["index"]]
    rate = build.call("useRates", indices=[group["index"]], actives=[spores])["rates"][0]
    print("Spores rate:", rate)
    assert rate["name"].startswith("Poison Spores") and rate["speed"] > 0 and abs(rate["detonationTime"] - 15) < 1e-6
    # (MECHB16) the granted Explosion takes the supported skill's level (bridge installGrantedLevel; poe2db: "copy the
    # Level of the Gem it is Socketed in"): Lightning Arrow at 20, so level 20's 969, not the support's own level 1's 2
    assert rate["storedPoisonPerPercent"] == 969 and rate["storedPoisonMaxMore"] == 300, rate
    assert build.call("useRates", indices=[group["index"]])["rates"][0]["name"].startswith("Lightning Arrow")  # (put back)
    mods, notes = search.assumptions.pustule_cap(found)
    share = 1 / 15 / rate["speed"]
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (share - 1), "Poison Spores")], mods
    assert notes[0]["value"] == f"x{share:.3f}"


def test_toxic_growth_explosion_is_held_to_its_pustule_limit(engine):
    """Toxic Growth's Explosion set (MECHB14): PoB rates it at the attack rate; the bridge gives its Pustule Limit
    (number_of_poisonbloom_arrow_bloom_allowed with quality, + AdditionalCooldownUses) and Detonation Time, and the cap
    holds it to Limit / fuse."""
    from builder.search import models
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Lightning Arrow", ascendancies=["Deadeye"], level=90, objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), ["Lightning Arrow"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.groups() if g["main"])
    build.call("setGroup", index=group["index"], gems=[{"name": "Toxic Growth", "support": False, "level": 20,
                                                        "quality": 20, "enabled": True}])
    build.call("setGroup", index=group["index"], mainActiveSkill=1, statSet=2)  # (the Explosion)
    found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"] if not s["disabled"]]
    assert models.pustule_groups(found, "Toxic Growth") == [group["index"]], [(s.name, s.stat_set) for s in found]
    rate = build.call("useRates", indices=[group["index"]])["rates"][0]
    print("Toxic Growth rate:", rate)
    assert rate["pustuleLimit"] == 6 and abs(rate["detonationTime"] - 15) < 1e-6 and rate["speed"] > 0.4  # (5 + 20q x 0.05)
    # (MECHB15) the set's stored-Poison stats at gem level 20 (GrantedEffectStatSetsPerLevel: 969; the maximum 300)
    assert rate["storedPoisonPerPercent"] == 969 and rate["storedPoisonMaxMore"] == 300, rate
    mods, notes = search.assumptions.pustule_cap(found)
    share = 6 / 15 / rate["speed"]
    assert mods == [models.raw_mod("DPS", "MORE", 100 * (share - 1), "Toxic Growth")], mods
    assert notes[0]["value"] == f"x{share:.3f}"


def test_rolling_slam_counts_its_first_and_second_slam(engine):
    """Rolling Slam (MECHB15): one use does its First Slam (272% at level 20) and Second Slam (543%); PoB scores one set.
    The bridge's setRates calculates each set on its own and puts the chosen set back; the model adds the other slam."""
    from builder.search import models
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Rolling Slam", ascendancies=["Titan"], level=90, objective="boss"))
    skeleton = enumerate_skeletons(Context(payload), ["Rolling Slam"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup(quests=False)
    build = search.build
    group = next(g for g in build.groups() if g["main"])
    position = next(i for i, s in enumerate(group["skills"], start=1) if s["name"] == "Rolling Slam")
    build.call("setGroup", index=group["index"], mainActiveSkill=position, statSet=3)  # (the Second Slam)
    before = build.call("stats", fields=["TotalDPS"])["TotalDPS"]
    sets = build.call("setRates", index=group["index"], active=position)["sets"]
    print("Rolling Slam sets:", sets)
    assert [s["label"] for s in sets] == ["Rolling Slam", "First Slam", "Second Slam"]
    first, second = sets[1]["TotalDPS"], sets[2]["TotalDPS"]
    assert 0 < first < second and abs(second - before) <= 1e-6 * before  # (the Second Slam is what PoB scored)
    # (272 / 543 = 0.50, and the Second Slam's "50% more against Heavy Stunned enemies", which PoB applies here: 0.355)
    assert 0.3 < first / second < 0.55
    assert build.call("stats", fields=["TotalDPS"])["TotalDPS"] == before  # (put back)
    main = next(models.Skill.of(s) for s in build.call("activeSkills")["skills"]
                if s["name"] == "Rolling Slam" and not s["disabled"])
    assert main.stat_set == "Second Slam"
    mods, notes = models.multi_set_model(main.name, main.stat_set, sets)
    assert abs(mods[0]["value"] - 100 * first / second) < 1e-2, notes  # (raw_mod keeps 3 decimals)


def test_lucky_spell_crits_are_pobs_own_lucky_on_spell_crits(engine):
    """"N% chance for Spell Damage with Critical Hits to be Lucky" (PoB parses it only in part, so never applies it):
    the bridge makes it PoB's LuckyHitsChance on Spell hits in the crit pass (COV8's patch, POE2T2401; MECHB10). Spark
    with 300% increased spell crit chance and 100% Lucky gains RESEARCH3's chance x crit share x (max-min)/(3(max+min))."""
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    parsed = engine.call("parseLines", lines=["30% chance for Spell Damage with Critical Hits to be Lucky"])["results"][0]
    assert parsed["supported"] and parsed["parsed"] == [{"flags": 2, "keywordFlags": 0, "name": "LuckyHitsChance",
                                                         "tags": ["Condition:CriticalStrike"], "type": "BASE", "value": 30}]
    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    build = search.build
    build.set_group(index=build.main_group()["index"], gems=[{"name": "Spark", "level": 20, "quality": 0}])
    fields = ["TotalDPS", "CritChance", "LightningStoredCritMin", "LightningStoredCritMax", "LightningStoredCritAvg",
              "LightningStoredHitAvg"]

    def ring(extra: str) -> dict:
        engine.call("equip", slot="Ring 1", raw="Rarity: Rare\nProbe Ring\nAmethyst Ring\n--------\n"
                                                "300% increased Critical Hit Chance for Spells\n" + extra)
        return engine.call("stats", fields=fields)

    base = ring("")
    lucky = ring("100% chance for Spell Damage with Critical Hits to be Lucky\n")
    lo, hi, c = base["LightningStoredCritMin"], base["LightningStoredCritMax"], base["CritChance"] / 100
    share = c * base["LightningStoredCritAvg"] / (c * base["LightningStoredCritAvg"] + (1 - c) * base["LightningStoredHitAvg"])
    expect = 1 + share * (hi - lo) / (3 * (lo + hi))
    print("Lucky spell crits:", lucky["TotalDPS"] / base["TotalDPS"], expect)
    assert expect > 1.01 and abs(lucky["TotalDPS"] / base["TotalDPS"] - expect) < 1e-3
    assert base["LightningStoredHitAvg"] == lucky["LightningStoredHitAvg"]  # (non-crit hits aren't Lucky)


def test_a_new_build_is_scored_against_the_boss_preset(engine):
    """SEARCH6: Search.setup (a build from a skeleton, no seed code) called newBuild only, so it kept PoB's configuration
    (its Pinnacle placeholder resistances); only a seed code's resetConfig set the boss preset's. Now both do."""
    from builder.search import bosses
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    ctx = context(main_skill="Spark")
    skeleton = enumerate_skeletons(ctx, ["Spark"])[0][0].as_dict()
    search = Search(engine, None, make_payload(ctx.request), skeleton, 0)
    search.setup(quests=False)
    config = search.build.call("getConfig")["input"]
    from builder.search import boss_hits

    # resetConfig writes args.bossHits first and args.boss over it (bridge.lua), so the boss's own config_vars wins: for
    # the default, the worst-case pinnacle (MECHD5), its hits are each type at the biggest any real preset deals
    # (bosses.worst_hit_sizes), not boss_hits' Arbiter of Ash fallback, which MECHC11's WORST hook replaces in Python.
    name = ctx.request.boss or bosses.DEFAULT  # (None: the bridge's BOSS_HITS_DEFAULT, then BOSS_DEFAULT over it)
    for var, value in {**boss_hits.config_vars(name), **bosses.config_vars(name)}.items():  # resistances, hits per type
        assert config.get(var) == value, var
    if name == bosses.WORST:
        for t, size in bosses.worst_hit_sizes().items():
            assert config.get(f"enemy{t}Damage") == round(size), t
