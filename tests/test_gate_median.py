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
