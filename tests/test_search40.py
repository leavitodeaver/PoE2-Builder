"""SEARCH40 (the manager's 24 Nov 17:02 items): reservations past the pool are game limits (unreserved Life below 1,
unreserved Mana negative: SPEED2's 2b75c, POE2T7853, delivered a build that reserved all its Life)."""
from __future__ import annotations

from types import SimpleNamespace

from builder.request.schema import DefenceStyle
from builder.search.objective import Objective, reservation_short


def limits(stats):
    o = SimpleNamespace(penalties=[], points_slack=0, groups_enforced=False, style=DefenceStyle.ANY)
    return [d for d in Objective._deficits(o, stats) if d[2] is True]


def test_all_life_reserved_is_a_hard_limit():
    found = reservation_short({"Life": 1000, "LifeUnreserved": 0, "Mana": 200, "ManaUnreserved": 50})
    assert found == [("Unreserved Life below 1", 0.001, True)]
    assert ("Unreserved Life below 1", 0.001, True) in limits({"Life": 1000, "LifeUnreserved": 0})


def test_life_reserved_past_the_pool_reads_further_short():
    (label, amount, hard), = reservation_short({"Life": 1000, "LifeUnreserved": -199})
    assert label == "Unreserved Life below 1" and hard is True and abs(amount - 0.2) < 1e-9


def test_negative_unreserved_mana_is_a_hard_limit_and_zero_mana_left_is_not():
    assert reservation_short({"Mana": 400, "ManaUnreserved": -100}) == [("Unreserved Mana negative", 0.25, True)]
    assert reservation_short({"Mana": 400, "ManaUnreserved": 0}) == []  # (PoB's test is "negative")


def test_one_life_left_or_no_field_is_no_limit():
    assert reservation_short({"Life": 1, "LifeUnreserved": 1}) == []  # (Chaos Inoculation: 1 Life, nothing reserved)
    assert reservation_short({"Life": 3000, "LifeUnreserved": 1}) == []
    assert reservation_short({"Life": 3000}) == []  # (a stand-in or a what-if without the field)
    assert limits({"Life": 3000, "LifeUnreserved": 2000, "Mana": 100, "ManaUnreserved": 10}) == []


def test_a_bear_builds_skills_have_their_types():
    """(Fix 3; GEM39) Maul and Molten Crash: PoB marks them fromItem, so Context.gems leaves them out; their types come
    from any_gems now, so a reader keyed on Bear / Slam / Area sees them (before: [])."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload, skill_types_of

    ctx = Context(make_payload(BuildRequest(main_skill="Spark")))
    assert "Maul" not in ctx.gems and "Molten Crash" not in ctx.gems
    assert {"Bear", "Attack", "Area", "Melee"} <= set(ctx.skill_types("Maul"))
    assert {"Bear", "Slam", "ConsumesRage"} <= set(ctx.skill_types("Molten Crash"))
    assert "Spell" in ctx.skill_types("Spark") and ctx.skill_types(None) == [] and ctx.skill_types("No Such Skill") == []
    stand_in = SimpleNamespace(gems={"Spark": {"skillTypes": ["Spell"]}})  # (a test's stand-in context: gems only)
    assert skill_types_of(stand_in, "Spark") == ["Spell"] and skill_types_of(stand_in, "Maul") == []


def test_no_assumption_reads_a_skills_types_from_the_socketable_gems_alone():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "builder" / "search" / "assumptions.py").read_text(encoding="utf-8")
    assert not re.search(r'build\.ctx\.gems\.get\((?:[^()]|\([^()]*\))*\)(?: or \{\}\))?\.get\("skillTypes"', text)


def close_build(score=10.0, started_ago=1000.0):
    """A stand-in Build for _rescore_close_calls: a slot's real score is REAL[raw], the what-if what the caller gives."""
    from builder.search import state

    calls = []
    real = {"a": 9.9, "b": 10.5, "c": 8.0, "d": 12.0}
    b = SimpleNamespace(score=score, stats={"s": 1}, started=state.clock.now() - started_ago, close_seconds=0.0,
                        stopped=False, stop_at=None, rescored=0, close_rescored=0, equipped_raw={"Ring 1": "own"})

    def call(name, **kw):
        calls.append((name, kw.get("raw")))
        if name == "equipped":
            return {"slots": dict(b.equipped_raw)}
        if name == "equip":
            b.equipped_raw[kw["slot"]] = kw["raw"]
            return {}
        return {"v": real.get(b.equipped_raw["Ring 1"], score)}

    b.call = call
    b.reassume = lambda: calls.append(("reassume", None))
    b.added_stats = lambda: {}
    b.objective = SimpleNamespace(fields=[], prepare=lambda s: s, score=lambda s: s["v"])
    return b, calls


def test_close_calls_get_a_real_trial_and_the_build_is_put_back():
    from builder.search.state import Build

    b, calls = close_build()
    cases = [{"slot": "Ring 1", "raw": r} for r in ("a", "b", "c", "d")]
    scored = [(10.2, {}), (9.6, {}), (10.1, {}), (11.5, {})]  # (d's what-if is 1.5 above: not a close call)
    out = Build._rescore_close_calls(b, cases, list(scored), done={0})  # (a: already re-scored by the model rule)
    assert out[0] == scored[0] and out[3] == scored[3]
    assert out[2][0] == 8.0 and out[1][0] == 10.5  # (the best two close calls, c then b, scored for real)
    assert b.close_rescored == 2 and b.equipped_raw["Ring 1"] == "own" and (b.score, b.stats) == (10.0, {"s": 1})
    assert calls[-1] == ("reassume", None)


def test_close_calls_are_off_by_default_and_time_guarded(monkeypatch):
    from builder.search import state
    from builder.search.state import Build

    monkeypatch.delenv(state.CLOSE_CALLS_SWITCH, raising=False)
    assert not state.close_calls_enabled()
    monkeypatch.setenv(state.CLOSE_CALLS_SWITCH, "1")
    assert state.close_calls_enabled()
    b, calls = close_build(started_ago=0.0)  # (no task time yet: no share for trials)
    cases = [{"slot": "Ring 1", "raw": "b"}]
    assert Build._rescore_close_calls(b, cases, [(9.6, {})], done=set()) == [(9.6, {})]
    assert not [c for c in calls if c[0] in ("equip", "reassume")]
    b, calls = close_build()
    b.stopped = True  # (after the hard stop: none)
    assert Build._rescore_close_calls(b, cases, [(9.6, {})], done=set()) == [(9.6, {})] and not calls
    b, calls = close_build()  # (an Alloy-factor case: a plain equip can't carry the factor)
    assert Build._rescore_close_calls(b, [{"slot": "Ring 1", "raw": "b", "extra": {"x": 1.2}}], [(9.6, {})],
                                      done=set()) == [(9.6, {})]


def feeder_search(gain: dict[str, float], main="Twister", cap=None, groups_used=3):
    """A stand-in Search for feeder_round: adding feeder X and pressing it moves the score by gain[X]."""
    import copy

    from builder.search.inner import FEEDER_LABEL

    groups = [{"index": i + 1, "gems": [{"name": f"S{i}"}], "fromItem": False, "label": ""} for i in range(groups_used)]
    objective = SimpleNamespace(press_choices={}, dps=lambda s: s.get("dps", 0.0))
    build = SimpleNamespace(score=10.0, stats={"LedgerSkillSlots": 9, "dps": 1.0}, objective=objective)
    build.groups = lambda: groups
    build.snapshot = lambda: copy.deepcopy({"groups": groups, "score": build.score, "press": objective.press_choices})

    def restore(snap):
        groups[:] = snap["groups"]
        build.score, objective.press_choices = snap["score"], dict(snap["press"])
    build.restore = restore

    def set_group(gems, label):
        assert label == FEEDER_LABEL
        groups.append({"index": len(groups) + 1, "gems": gems, "fromItem": False, "label": label})
    build.set_group = set_group

    def press_round(deadline):
        name = groups[-1]["gems"][0]["name"]
        if gain.get(name, 0.0) > 0:
            objective.press_choices = {**objective.press_choices, name: 1.0}
            build.score = 10.0 + gain[name]
        return True

    ctx = SimpleNamespace(gems={"Whirling Slash": {}, "Whirlwind Lance": {}}, allowed_gem=lambda n: True,
                          gem_price=lambda n: (0.0, "found"), request=SimpleNamespace(max_buttons=cap))
    gems = SimpleNamespace(affordable=lambda n, f, l: True, budget_left=lambda: 0.0, usable=lambda n: n in gain,
                           persistent_gem=lambda n: [{"name": n, "level": 20, "quality": 20}],
                           buttons=lambda: [g["gems"][0]["name"] for g in groups])
    s = SimpleNamespace(build=build, gems=gems, ctx=ctx, skeleton=SimpleNamespace(main_skill=main), press_rows={},
                        resolve=lambda: None, press_round=press_round, _rescore=lambda: build.score)
    return s, groups


def test_a_twister_build_keeps_the_feeder_that_scores_higher():
    """(Item 6; MECHB60) Whirlwind Lance feeds a Spear Twister (Whirling Slash isn't usable with a spear: usable False)."""
    from builder.search.inner import Search

    s, groups = feeder_search({"Whirlwind Lance": 0.5})
    assert Search.feeder_round(s, float("inf"))
    assert groups[-1]["gems"][0]["name"] == "Whirlwind Lance" and s.build.objective.press_choices == {"Whirlwind Lance": 1.0}
    assert [r["skill"] for r in s.feeder_log] == ["Whirlwind Lance"]


def test_no_feeder_when_it_doesnt_score_higher_or_doesnt_fit():
    from builder.search.inner import Search

    for kwargs in ({"gain": {"Whirlwind Lance": -0.1}}, {"gain": {"Whirlwind Lance": 0.5}, "main": "Spark"},
                   {"gain": {"Whirlwind Lance": 0.5}, "cap": 3}, {"gain": {"Whirlwind Lance": 0.5}, "groups_used": 9}):
        s, groups = feeder_search(**kwargs)
        n = len(groups)
        assert not Search.feeder_round(s, float("inf")) and len(groups) == n and s.build.score == 10.0



def test_the_side_fill_is_on_by_default_and_off_with_its_switch(monkeypatch):
    """(Item 3; ON by default since the manager's 26 Nov 04:50) Side groups get fill_open's real trials unless the switch
    is "0"."""
    from builder.search import gems

    monkeypatch.setenv(gems.SIDE_FILL_SWITCH, "0")
    assert gems.fill_kinds() == gems.FILL_KINDS == ("main",)
    monkeypatch.delenv(gems.SIDE_FILL_SWITCH, raising=False)
    assert gems.fill_kinds() == ("main", "side")


def test_a_side_groups_trials_go_to_a_what_if_signal_then_to_model_valued_supports():
    from builder.search.gems import Gems

    g = SimpleNamespace(build=SimpleNamespace(model_gem=lambda n: n in ("Model B", "Model A")))
    options = ["Plain", "Model B", "Signal", "Model A", "Worse"]
    scored = [(5.0, {}), (5.0, {}), (5.0007, {}), (5.0, {}), (4.9, {})]
    assert Gems._side_ranked(g, options, scored, 3) == ["Signal", "Model B", "Model A"]
    g.build.model_gem = None  # (no search set it: what-if, then PoB's order)
    assert Gems._side_ranked(g, options, scored, 2) == ["Signal", "Plain"]



def test_last_empty_gives_the_kept_back_time_to_the_empty_slots_alone(monkeypatch):
    """(SEARCH38's next gear-first try) POE2_SEARCH_GEAR_FIRST=last_empty: as `last` (the resumed pass's steps stop
    GEAR_SHARE early), but the time kept back works the empty slots only, not the cut round from where it stopped."""
    from builder import clock
    from builder.search import inner

    now = [0.0]
    monkeypatch.setattr(clock, "now", lambda: now[0])
    monkeypatch.setattr(inner, "PERSIST_LINES", True)
    monkeypatch.setattr(inner, "GEAR_FIRST", True)
    monkeypatch.setattr(inner, "GEAR_FIRST_EMPTY", False)
    monkeypatch.setattr(inner, "GEAR_FIRST_LAST", True)
    monkeypatch.setattr(inner, "GEAR_SHARE", 0.4)

    def run(narrow):
        monkeypatch.setattr(inner, "GEAR_FIRST_LAST_EMPTY", narrow)
        now[0] = 0.0
        ran = []

        def step(phase, fn, *args):
            ran.append((phase, args))
            now[0] += 25.0
            return False

        fake = lambda *a, **k: None  # noqa: E731
        blocks = SimpleNamespace(**{n: fake for n in (
            "gear_round", "ascendancy_round", "optimize_supports", "grow", "attributes_round", "jewel_round", "round",
            "optimize_persistent", "granted_meta_round", "meta_round", "meta_spell_round", "totem_round", "mark_round",
            "mirage_round", "roar_round", "swap_round")}, resume="Body Armour")
        search = SimpleNamespace(passes=0, passes_done=0, line_passes=1, resume_step="supports", resume_sweep=True,
                                 current_step=None, kicked=set(), build=SimpleNamespace(equipped={}),
                                 gear=blocks, tree=blocks, gems=blocks, jewels=blocks, sources=blocks,
                                 conditions_round=fake, weapon_set_round=fake, tree_rebuild=fake, press_round=fake,
                                 skeleton=SimpleNamespace(swap=False), step=step, idle={}, skipped={},
                                 gear_census=lambda: {"empty": ["Gloves", "Belt"]})
        inner.Search.blocks(search, deadline=100.0)
        return ran

    assert run(True)[-1] == ("gear", (100.0, True, ["Gloves", "Belt"]))  # (the empty slots alone)
    assert run(False)[-1] == ("gear", (100.0, True))  # (plain `last`: the cut round from where it stopped)



def test_finalize_spends_ascendancy_points_the_search_left_unspent():
    """(POE2T7528: 2b75d seed 2 delivered an Oracle with no ascendancy notable) Under 8 points spent, finalize runs the
    ascendancy round once; kept unless the score falls; nothing happens with all 8 spent."""
    from builder.search.inner import Search

    def search(spent, after, score_after):
        state = {"spent": spent, "score": 10.0}
        class Build:
            score = property(lambda b: state["score"])

            def call(self, name):
                return {"ascendancyAllocated": state["spent"]}

            def snapshot(self):
                return dict(state)

            def restore(self, snap):
                state.update(snap)
        build = Build()
        ran = []

        def step(phase, fn):
            ran.append(phase)
            state["spent"], state["score"] = after, score_after
        return SimpleNamespace(build=build, step=step, tree=SimpleNamespace(ascendancy_round=None)), ran, state

    s, ran, state = search(0, 8, 10.5)
    assert Search.spend_ascendancy(s) and ran == ["ascendancy points"] and state["spent"] == 8
    s, ran, state = search(8, 8, 10.5)
    assert not Search.spend_ascendancy(s) and ran == []
    s, ran, state = search(2, 8, 9.0)  # (spent, the score fell: put back)
    assert not Search.spend_ascendancy(s) and state == {"spent": 2, "score": 10.0}


import faulthandler  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

ORACLE = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "gate4-20261123-073445.json"


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


def test_the_weapon_set_the_main_skill_doesnt_use_gives_the_models_no_lines(engine):
    """(Item 1; MECHC59's POE2T7139) A swap weapon's lines reach the models (sourceLines) only while the main skill's
    group uses that weapon set, as PoB's own calculation reads them (CalcSetup 1255): before, "+8 to Maximum Rage" on
    "Weapon 1 Swap" reached the Rage model of a build on weapon set 1."""
    from builder.pob import codes

    row = json.loads(ORACLE.read_text(encoding="utf-8"))["rows"][0]
    engine.call("loadXml", xml=codes.decode(row["code"]))

    def swap_lines():
        return sorted(l["text"] for l in engine.call("sourceLines")["lines"] if l["source"].endswith("Swap"))

    def strength():
        return float(engine.call("stats", fields=["Str"])["Str"])

    plain = strength()
    engine.call("equip", slot="Weapon 1 Swap",
                raw="Rarity: RARE\nProbe Fang\nWithered Wand\n+100 to Strength\n+8 to Maximum Rage")
    assert swap_lines() == [] and strength() == plain  # (set 1: neither PoB nor the models read the swap wand)
    # (the mirror: a build whose main skill plays weapon set 2, Ice Shot on the swap bow (test_engine_items' fixture):
    # the swap weapons' lines are its own, set 1's wand gives none. Probe POE2T7513: PoB won't move the Oracle's main
    # group to set 2, its skill needs set 1's weapon)
    code = (Path(__file__).parent / "fixtures" / "codes" / "set2_ice_shot_with_mana.txt").read_text(encoding="utf-8")
    engine.call("loadXml", xml=codes.decode(code.strip()))
    set2 = next(g for g in engine.call("groups")["groups"] if (g["set1"], g["set2"]) == (False, True)
                and any(n["name"] == "Ice Shot" for n in g["gems"]))
    engine.call("setGroup", index=set2["index"], main=True)  # (the fixture's main group uses both sets: PoB plays set 1)
    groups = engine.call("groups")
    main = next(g for g in groups["groups"] if g["index"] == groups["mainSocketGroup"])
    assert main["index"] == set2["index"] and (main["set1"], main["set2"]) == (False, True)
    plain2 = strength()
    engine.call("equip", slot="Weapon 1 Swap", raw="Rarity: RARE\nProbe Bow\nCrude Bow\n+100 to Strength")
    sources = {l["source"] for l in engine.call("sourceLines")["lines"]}
    assert strength() == plain2 + 100  # (PoB reads the set-2 bow)
    assert "item:Weapon 1 Swap" in sources and not sources & {"item:Weapon 1", "item:Weapon 2"}, sorted(sources)
    plain2 = strength()
    engine.call("equip", slot="Weapon 1", raw="Rarity: RARE\nProbe Fang\nWithered Wand\n+100 to Strength\n+8 to Maximum Rage")
    assert strength() == plain2  # (PoB doesn't read set 1's wand)
    assert not {l["source"] for l in engine.call("sourceLines")["lines"]} & {"item:Weapon 1", "item:Weapon 2"}
