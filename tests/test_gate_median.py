"""SEARCH20 (the manager's 14 Nov item 1: make a gate verdict mean something): one gate4 run is one draw of a search
that hasn't converged (two --cpu runs of one commit: 2b75d 4,956 and 15,518), so a verdict is the median of a set of
runs on fixed draw sets (gate4 --seed-base), compared with a baseline set on the same bases."""
from __future__ import annotations

import importlib.util
import json

from builder import ROOT
from builder.search.run import SEED_STRIDE, Run

_spec = importlib.util.spec_from_file_location("gate_median", ROOT / "benchmarks" / "gate_median.py")
gate_median = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate_median)


def write(tmp_path, name, seed_base, dps, feasible=True, reference=100.0):
    path = tmp_path / f"{name}-{seed_base}.json"
    path.write_text(json.dumps({"seed_base": seed_base, "commit": "abc1234", "rows": [{
        "name": "2b75d", "dps": dps, "ratio": dps / reference, "feasible": feasible,
        "passed": feasible and dps >= reference, "reference_dps": reference, "evaluations": 1000}]}), encoding="utf-8")
    return str(path)


def test_the_verdict_is_the_median_run_and_an_infeasible_run_ranks_below_every_feasible_one(tmp_path):
    runs = gate_median.load([write(tmp_path, "a", 0, 150.0), write(tmp_path, "a", 1, 900.0, feasible=False),
                             write(tmp_path, "a", 2, 120.0)])
    s = gate_median.summary(runs["2b75d"])
    assert s["median"]["dps"] == 120.0 and s["passed"]  # (900 not feasible: the worst, not the best)
    assert s["low"]["dps"] == 900.0 and s["high"]["dps"] == 150.0
    # an even set takes the lower middle run: a verdict doesn't round up
    even = gate_median.summary(runs["2b75d"] + gate_median.load([write(tmp_path, "a", 3, 200.0)])["2b75d"])
    assert even["median"]["dps"] == 120.0


def test_a_change_counts_only_outside_the_baselines_spread(tmp_path):
    base = gate_median.summary(gate_median.load([write(tmp_path, "b", k, d) for k, d in enumerate((40, 60, 90))])["2b75d"])

    def verdict(*dps):
        return gate_median.compare(gate_median.summary(gate_median.load(
            [write(tmp_path, f"c{dps}", k, d) for k, d in enumerate(dps)])["2b75d"]), base)

    assert verdict(80, 95, 120) == "higher"
    assert verdict(50, 85, 300) == "within the spread"
    assert verdict(10, 30, 200) == "lower"
    assert gate_median.main([write(tmp_path, "d", 0, 70.0), "--against", write(tmp_path, "e", 0, 70.0)]) == 0


def test_a_seed_base_moves_every_task_seed_and_base_0_keeps_the_old_seeds():
    run = Run.__new__(Run)
    assert run.task_seed(7919) == 7919  # (a Run made before seed_base: base 0)
    run.seed_base = 0
    assert run.task_seed(31) == 31
    run.seed_base = 2
    assert run.task_seed(31) == 31 + 2 * SEED_STRIDE
    assert SEED_STRIDE > 7919 * 6 + 104729 * 4  # (past every seed one run makes: no two draw sets share a task seed)


def test_a_tasks_gear_census_names_its_empty_gear_slots_but_not_flasks_or_charms():
    """SEARCH20: the 14 Nov 2b75d gate's best build (15,518) wore no boots, gloves, rings or belt; each task's event
    now says which gear slots are empty, where the next full gear round starts, the divines spent and the budget weight."""
    from types import SimpleNamespace

    from builder.search.inner import Search

    search = Search.__new__(Search)
    ring = SimpleNamespace(price=12.0)
    search.gear = SimpleNamespace(slots={"Ring 1": 0, "Ring 2": 0, "Flask 1": 0, "Charm 1": 0}, resume="Ring 2", lam=0.02)
    search.build = SimpleNamespace(equipped={"Ring 1": ring}, spent=lambda: 12.0)
    assert search.gear_census() == {"empty": ["Ring 2"], "resume": "Ring 2", "spent": 12.0, "lam": 0.02}


def test_gate4_fair_scores_the_reference_with_the_searchs_own_steps_and_a_reset_config(monkeypatch):
    """SEARCH24 (item 3, SEARCH23's yardstick finding): gate4 also scores each reference the way the search scores its
    own builds (fair_reference, the verdict's reference since 15 Nov): the same build, its PoB config reset (bridge resetConfig) and the search's settle / conditions /
    play / press / uptime steps, as finalize_task runs them."""
    import types

    from builder.search import inner, skeletons

    spec = importlib.util.spec_from_file_location("gate4", ROOT / "benchmarks" / "gate4.py")
    gate4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate4)
    calls, settles = [], [True, True, False]

    class Objective:
        def dps(self, stats):
            return stats["dps"]

        def deficits(self, stats):
            return [("Spirit", 0.3, True)]

    class Search:
        def __init__(self, engine, report, payload, skeleton, seed, full=False):
            assert full and report is None
            self.build = types.SimpleNamespace(objective=Objective(), stats={"dps": 0.0}, boss_stats=None,
                                               call=lambda name, **kw: calls.append(name),
                                               refresh=lambda: calls.append("refresh"))
            self.conditions = types.SimpleNamespace(active={"EnemyFrozen"})

        def settle(self):
            calls.append("settle")
            return settles.pop(0)

        def conditions_round(self):
            calls.append("conditions")

        def choose_play(self):
            calls.append("play")

        def resolve(self):
            calls.append("resolve")

        def press_round(self, deadline):
            calls.append("press")

        def weigh_uptimes(self):
            calls.append("uptimes")
            self.build.stats["dps"] = 621.0

    monkeypatch.setattr(inner, "Search", Search)
    monkeypatch.setattr(skeletons, "enumerate_skeletons",
                        lambda ctx, names: [[types.SimpleNamespace(as_dict=lambda: {})]])
    monkeypatch.setattr(gate4, "make_payload", lambda request: {})
    monkeypatch.setattr(gate4, "Context", lambda payload: None)
    request = types.SimpleNamespace(main_skill="Explosive Spear", boss=None)
    fair = gate4.fair_reference(None, "<xml/>", request)
    assert fair == {"dps": 621.0, "short": ["Spirit"], "conditions": ["EnemyFrozen"]}
    assert calls == ["loadXml", "resetConfig", "refresh", "settle", "settle", "settle", "conditions", "play", "resolve",
                     "press", "uptimes"]


def test_gate4_judges_against_the_fair_reference_unless_asked_for_the_authors_toggles():
    """The manager, 15 Nov 20:50 (the user: "I'm not hearing a question"): both ratios are shown, PASS / FAIL uses the
    reference scored the search's way, and --author-toggles keeps the old verdict for comparison."""
    spec = importlib.util.spec_from_file_location("gate4", ROOT / "benchmarks" / "gate4.py")
    gate4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate4)
    row = gate4.verdict(400.0, True, toggled=1400.0, fair=300.0)
    assert row["passed"] and row["verdict"] == "fair" and row["reference_dps"] == 300.0
    assert abs(row["ratio"] - 4 / 3) < 1e-9 and abs(row["ratio_toggled"] - 400 / 1400) < 1e-9
    assert not gate4.verdict(400.0, False, 1400.0, 300.0)["passed"]  # (a floor short never passes)
    old = gate4.verdict(400.0, True, 1400.0, 300.0, author_toggles=True)
    assert not old["passed"] and old["verdict"] == "toggled" and old["reference_dps"] == 1400.0


def test_a_run_wearing_a_free_recipe_made_base_is_marked_and_so_is_a_baseline_holding_one(tmp_path, capsys):
    """Before train 63 (2c58be9d) the search offered Grasping Mail (60 Breach Rings) as a free plain base; all three of
    main c4202146's 2b75c runs wore it (SEARCH25). Such a run is marked, and a baseline set holding one is named as not
    fair (the manager, 16-17 Nov: use post-train-63 baselines). A commit that keeps the base out isn't marked."""
    import base64
    import zlib

    def coded(text):
        return base64.urlsafe_b64encode(zlib.compress(text.encode())).decode().rstrip("=")

    def run(name, seed_base, commit, body):
        path = tmp_path / f"{name}-{seed_base}.json"
        path.write_text(json.dumps({"seed_base": seed_base, "commit": commit, "rows": [{
            "name": "2b75c", "dps": 100.0, "ratio": 1.0, "feasible": True, "passed": True, "reference_dps": 100.0,
            "evaluations": 1, "code": coded(f"<PathOfBuilding2><Item>Rarity: RARE\nGlyph Shell\n{body}</Item>")}]}),
            encoding="utf-8")
        return str(path)

    old = run("old", 0, "0000000", "Grasping Mail")  # (an unknown commit counts as without the rule)
    new = run("new", 0, "HEAD", "Grasping Mail")  # (this checkout keeps it out: crafted_base_unpriced)
    plain = run("plain", 0, "0000000", "Sacrificial Mantle")
    loaded = gate_median.load([old, new, plain])["2b75c"]
    assert [r["free_base"] for r in loaded] == [["Grasping Mail"], [], []]
    gate_median.main([plain, "--against", old])
    out = capsys.readouterr().out
    assert "not a fair baseline" in out and "old-0.json" in out
    gate_median.main([old])
    assert "FREE BASE: wears Grasping Mail" in capsys.readouterr().out
