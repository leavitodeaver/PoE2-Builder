"""SEARCH35: gate4's reference within the search's own limits (SEARCHB1's G0, the manager 13:00 21 Nov: 2b75d's
reference is 3 passives over the budget and Spirit short, so its ratio compares against a build the search can't
deliver; "Score references within the search's own budget, or show that reading beside the raw one")."""

import importlib.util
import os
import types

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gate4():
    spec = importlib.util.spec_from_file_location("gate4", ROOT / "benchmarks" / "gate4.py")
    gate4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate4)
    return gate4


class _Objective:
    def dps(self, stats):
        return stats["dps"]

    def deficits(self, stats):
        out = []
        if stats["over"] > 0:
            out.append(("Passive points over the level's budget", stats["over"], True))
        if stats["SpiritUnreserved"] < 0:
            out.append(("Spirit", 0.1, True))
        if stats["Str"] < 0:
            out.append(("Str requirement", 0.1, True))
        return out


def _search(groups, stats):
    calls = []

    def set_group(index, enabled):
        group = next(g for g in groups if g["index"] == index)
        if group["enabled"] != enabled:
            sign = -1 if enabled else 1
            stats["SpiritUnreserved"] += sign * group["spirit"]
            stats["dps"] -= sign * group["dps"]
            build.score = stats["dps"]
        group["enabled"] = enabled
        calls.append(("set", index, enabled))

    build = types.SimpleNamespace(objective=_Objective(), stats=stats, score=stats["dps"], groups=lambda: groups,
                                  main_group=lambda: groups[0], set_group=set_group)

    def trim():
        calls.append("trim")
        stats["over"] = 0
        return 3

    search = types.SimpleNamespace(build=build, tree=types.SimpleNamespace(trim_to_budget=trim),
                                   gems=types.SimpleNamespace(refresh_infused=lambda: False))
    for name in ("conditions_round", "choose_play", "resolve", "weigh_life", "weigh_uptimes"):
        setattr(search, name, lambda: calls.append(name))
    search.settle = lambda: False
    search.press_round = lambda deadline: None
    search.final_choices = lambda: False  # (SEARCH39: the finished build's re-choice, gate4._score_finished)
    return search, calls


def test_a_legal_reference_trims_passives_and_switches_off_the_cheapest_spirit_group():
    gate4 = _gate4()
    groups = [{"index": 1, "enabled": True, "gems": [{"name": "Main"}], "spirit": 0, "dps": 0},
              {"index": 2, "enabled": True, "label": "", "gems": [{"name": "Big Aura"}], "spirit": 60, "dps": 300.0},
              {"index": 3, "enabled": True, "label": "", "gems": [{"name": "Small Aura"}], "spirit": 30, "dps": 50.0},
              {"index": 4, "enabled": True, "label": "", "gems": [{"name": "Attack"}], "spirit": 0, "dps": 10.0}]
    stats = {"dps": 1000.0, "over": 3, "SpiritUnreserved": -20, "Str": 5}
    search, calls = _search(groups, stats)
    legal = gate4.legal_reference(search)
    assert calls[0] == "trim"
    assert legal["broken"] == ["Passive points over the level's budget", "Spirit"]
    # (Small Aura frees enough Spirit and loses least; the main group and a group that frees none never go)
    assert [g["enabled"] for g in groups] == [True, True, False, True]
    assert legal["changes"] == ["3 passive(s) over the level's points removed (the weakest branches)",
                                "Small Aura switched off (Spirit)"]
    assert legal["dps"] == 950.0 and legal["short"] == []


def test_a_reference_within_the_limits_has_no_legal_reading():
    gate4 = _gate4()
    groups = [{"index": 1, "enabled": True, "gems": [{"name": "Main"}], "spirit": 0, "dps": 0}]
    search, calls = _search(groups, {"dps": 1000.0, "over": 0, "SpiritUnreserved": 5, "Str": -1})
    assert gate4.legal_reference(search) is None  # (an attribute requirement alone stays in the raw reading's "short")
    assert calls == []


@pytest.fixture(scope="module")
def engine():
    import faulthandler

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


def _real_search(engine, main_skill, **body):
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill=main_skill, level=90, objective="boss", **body))
    skeleton = enumerate_skeletons(Context(payload), [main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 1)
    search.setup(quests=False)
    return search


def test_a_no_damage_set_is_never_the_calculated_one_but_a_later_set_of_its_skill_is(engine):
    """SEARCH35 (the manager's 22:20 / 23:05: MECHB53's POE2T4809 / 4810, RESEARCH45 R45-8 / R45-9): a stat set whose own
    stats carry base_deal_no_damage (the bridge's noDamageSets) is never a main-skill choice, per SET: Rolling Slam's
    first set is flagged and its two slams still are choices; a minion skill keeps its sets."""
    from builder.search.gems import Gems

    search = _real_search(engine, "Rolling Slam", ascendancies=["Titan"])
    group = next(g for g in search.build.groups() if g["main"])
    skill = next(s for s in group["skills"] if s["name"] == "Rolling Slam")
    position = group["skills"].index(skill) + 1
    assert skill["statSets"] == ["Rolling Slam", "First Slam", "Second Slam"] and skill["noDamageSets"] == [1]
    assert sorted(s for a, _, s, _ in Gems.active_options(group) if a == position) == [2, 3]


def test_a_load_ammunition_skill_scores_nothing_and_the_firing_skill_is_chosen(engine):
    """SEARCH35: "Load Hailstorm Rounds" (PoB: a plain crossbow attack from its one set, which carries
    base_deal_no_damage) was chosen over Hailstorm Rounds on a non-Full-DPS objective (MECHB53's POE2T4810). Now its set
    is no choice, the chooser lands on Hailstorm Rounds, and Full DPS counts no Load damage (the bridge's
    getActiveSkillCount wrapper; bridge.setNoDamageSets off shows what PoB alone counted)."""
    search = _real_search(engine, "Hailstorm Rounds")
    build = search.build
    # (MECHB53's probe 3 amulet: flat added attack damage, which PoB gives the Load skill's crossbow attack: 120 DPS)
    build.call("equip", slot="Amulet", raw=chr(10).join([
        "Rarity: Rare", "Probe Amulet", "Gold Amulet", "Item Level: 82", "Implicits: 0",
        "Adds 1 to 400 Lightning Damage to Attacks", "Adds 100 to 200 Fire Damage to Attacks"]), fields=[])
    group = next(g for g in build.groups() if g["main"])
    names = [s["name"] for s in group["skills"]]
    load = next(i for i, s in enumerate(group["skills"], start=1) if s["name"].startswith("Load "))
    assert group["skills"][load - 1]["noDamageSets"] == [1], group["skills"]
    assert all(a != load for a, _, _, _ in search.gems.active_options(group))
    build.call("setGroup", index=group["index"], mainActiveSkill=load, fullDPS=True)
    assert search.gems.on_no_damage_set(next(g for g in build.groups() if g["main"]))
    with_rule = build.call("stats", fields=["FullDPS"])["FullDPS"]
    build.call("setNoDamageSets", on=False)
    try:
        without = build.call("stats", fields=["FullDPS"])["FullDPS"]
    finally:
        build.call("setNoDamageSets", on=True)
    print("Full DPS with the rule", with_rule, "without", without, names)
    assert with_rule < without  # (PoB alone counts the Load skill's crossbow attack)
    build.refresh()
    search.gems.choose_active_skill(settle=False)
    group = next(g for g in build.groups() if g["main"])
    assert group["skills"][group["mainActiveSkill"] - 1]["name"] == "Hailstorm Rounds"


def test_the_socket_fill_runs_inside_the_search_once_per_main_group_set(monkeypatch):
    """SEARCH35 (SEARCH34's ranked list, item 5): with POE2_SEARCH_FILL_IN_SEARCH on, the search's "socket fill" step
    runs the open-socket fill on the main group once per main-group set the line holds with an open socket; the set the
    fill leaves isn't filled again, and the sets it has seen go on in the line's state."""
    from builder.search import inner

    monkeypatch.setattr(inner, "FILL_IN_SEARCH", True)
    search = inner.Search.__new__(inner.Search)
    search.fill_seen = []
    groups = {"main": {"index": 1, "enabled": True, "gems": [{"name": "Spark"}, {"name": "Uhtred's Augury"}]}}
    fills = []

    def fill_open(deadline, kinds=None):
        fills.append(kinds)
        groups["main"] = {"index": 1, "enabled": True, "gems": [{"name": "Spark"}, {"name": "Arcane Tempo"}]}
        return True

    open_ = {"n": 3}
    search.build = types.SimpleNamespace(main_group=lambda: groups["main"])
    search.gems = types.SimpleNamespace(fill_open=fill_open, socket_use=lambda group, meta: ([], 1, open_["n"]))
    assert search.socket_fill_round(float("inf")) is True
    assert fills == [("main",)]
    assert search.socket_fill_round(float("inf")) is False  # (the set the fill left: seen)
    groups["main"] = {"index": 1, "enabled": True, "gems": [{"name": "Spark"}, {"name": "Uhtred's Augury"}]}
    assert search.socket_fill_round(float("inf")) is False  # (the first set again: seen)
    groups["main"] = {"index": 1, "enabled": True, "gems": [{"name": "Spark"}]}
    open_["n"] = 0
    assert search.socket_fill_round(float("inf")) is False and len(fills) == 1  # (no open socket)
    assert search.fill_seen == [[1, "Spark", "Uhtred's Augury"], [1, "Spark", "Arcane Tempo"]]
    monkeypatch.setattr(inner, "FILL_IN_SEARCH", False)
    groups["main"] = {"index": 1, "enabled": True, "gems": [{"name": "Spark"}, {"name": "Elemental Focus"}]}
    open_["n"] = 3
    assert search.socket_fill_round(float("inf")) is False and len(fills) == 1  # (off by default)


def test_a_step_that_keeps_finding_nothing_is_skipped_on_the_line_unless_kicked(monkeypatch):
    """SEARCH35 (SEARCH34's ranked list, item 3): with POE2_SEARCH_IDLE_SKIP on, persistent / condition sources / meta
    gems that gained nothing in its last run on the line is skipped, run again every IDLE_RETRY-th time and after a kick;
    a gain resets it; other steps never skip."""
    from builder.search import inner

    monkeypatch.setattr(inner, "IDLE_SKIP", True)
    search = inner.Search.__new__(inner.Search)
    search.idle, search.skipped, search.kicked = {}, {}, set()
    assert not search.idle_skip("persistent")  # (never run yet)
    search.idle["persistent"] = [1, 0]  # (its last run gained nothing)
    assert [search.idle_skip("persistent") for _ in range(4)] == [True, True, False, True]
    search.kicked.add("persistent")
    assert not search.idle_skip("persistent")
    search.kicked.clear()
    search.idle["meta gems"] = [2, 0]
    search.kicked.add("meta")
    assert not search.idle_skip("meta gems")  # (a meta kick took the group out: re-chosen now)
    search.idle["gear"] = [5, 0]
    assert not search.idle_skip("gear")
    assert search.skipped == {"persistent": 3}
    monkeypatch.setattr(inner, "IDLE_SKIP", False)
    search.idle["persistent"] = [3, 0]
    assert not search.idle_skip("persistent")  # (off by default)


def test_finalize_gets_its_own_time_for_the_final_tree_regrow(monkeypatch):
    """SEARCH35 (the manager's 17:48: wire SEARCHB2's Tree.final_regrow; it took 178-240 s, finalize has at most 60 s):
    with POE2_SEARCH_FINAL_REGROW=1 (off by default: measured lower on 2b75b, POE2T5552 / 5553) the plan gives finalize FINAL_REGROW_SHARE of the run on top of its polish, at most FINAL_REGROW_MAX, taken from the
    search; none under FINAL_REGROW_MIN (a short run) or with POE2_SEARCH_FINAL_REGROW=0."""
    from builder.search import run as run_module

    def plan(seconds, on=True):
        monkeypatch.setattr(run_module, "FINAL_REGROW", on)
        r = run_module.Run.__new__(run_module.Run)
        r.seconds, r.workers = seconds, 5
        r.remaining = lambda: seconds
        probe, finalize = r.plan(1)
        return finalize, r.regrow_seconds

    assert plan(600) == (60.0 + 150.0, 150.0)  # (a 600 s gate: 150 s of regrow beside the 60 s polish)
    assert plan(1200) == (60.0 + 240.0, 240.0)
    assert plan(45) == (15.0, 0.0)  # (45 x 0.25 under 30 s: no regrow)
    assert plan(600, on=False) == (60.0, 0.0)
    monkeypatch.undo()
    assert run_module.FINAL_REGROW is (os.environ.get("POE2_SEARCH_FINAL_REGROW") == "1")  # (off unless asked for)


def test_the_line_regrow_replaces_m2_and_regrows_with_the_partial_clears_under_the_floors(monkeypatch):
    """SEARCH35 (the manager's 21:12, option (a)): with POE2_SEARCH_LINE_REGROW on, the line's M2 tree rebuild is off,
    and the line's last task regrows its tree with final_regrow's partial clears, the uptimes weighed first and after
    each variant."""
    from builder.search import inner

    monkeypatch.setattr(inner, "LINE_REGROW", True)
    calls = []
    search = inner.Search.__new__(inner.Search)
    search.tree_rebuilt, search.line_passes = False, 5
    assert search.tree_rebuild(float("inf")) is False and not search.tree_rebuilt  # (M2 off: nothing touched)

    def final_regrow(deadline, clears, reweigh):
        calls.append(("regrow", clears, reweigh))
        search.build.score = 2.0
        return [{"clear": "weak:30", "kept": True}]

    search.build = types.SimpleNamespace(score=1.0)
    search.weigh_uptimes = lambda: calls.append("weigh")
    search.tree = types.SimpleNamespace(final_regrow=final_regrow)
    assert search.line_regrow(10.0) is True
    assert calls == ["weigh", ("regrow", ("weak:30", "weak:15", "weak:8"), search.weigh_uptimes)]
    assert search.line_regrow_rows == [{"clear": "weak:30", "kept": True}]


def test_a_cut_gear_round_goes_on_first_when_a_task_resumes_its_pass_with_empty_slots(monkeypatch):
    """SEARCH35 (POE2T5561: 2b75b's lines wore no Gloves / Boots / Amulet / Rings / Belt from 78 s to 596 s): with
    POE2_SEARCH_GEAR_FIRST on, a task resuming its pass mid-way runs the cut full gear round first while slots are empty;
    not when nothing is empty, and not off."""
    from types import SimpleNamespace

    from builder import clock
    from builder.search import inner

    monkeypatch.setattr(clock, "now", lambda: 0.0)
    monkeypatch.setattr(inner, "PERSIST_LINES", True)

    def run(on, empty, gear_resume="Helmet"):
        monkeypatch.setattr(inner, "GEAR_FIRST", on)
        ran = []

        def step(phase, fn, *args):
            ran.append((phase, args))
            return False

        fake = lambda *a, **k: None
        blocks = SimpleNamespace(**{n: fake for n in (
            "gear_round", "ascendancy_round", "optimize_supports", "grow", "attributes_round", "jewel_round", "round",
            "optimize_persistent", "granted_meta_round", "meta_round", "meta_spell_round", "totem_round", "mark_round",
            "mirage_round", "roar_round", "swap_round")}, resume=gear_resume)
        search = SimpleNamespace(passes=0, passes_done=0, line_passes=1, resume_step="supports", resume_sweep=True,
                                 current_step=None, kicked=set(), build=SimpleNamespace(equipped={}),
                                 gear=blocks, tree=blocks, gems=blocks, jewels=blocks, sources=blocks,
                                 conditions_round=fake, weapon_set_round=fake, tree_rebuild=fake, press_round=fake,
                                 skeleton=SimpleNamespace(swap=False), step=step,
                                 gear_census=lambda: {"empty": empty})
        inner.Search.blocks(search, deadline=100.0)
        return ran

    ran = run(True, ["Gloves", "Belt"])
    assert [p for p, _ in ran][:2] == ["gear", "supports"] and ran[0][1][1] is True  # (a full round, from where it stopped)
    assert [p for p, _ in run(True, [])][0] == "supports"
    assert [p for p, _ in run(True, ["Gloves"], gear_resume=None)][0] == "supports"  # (no cut round to go on with)
    assert [p for p, _ in run(False, ["Gloves", "Belt"])][0] == "supports"
