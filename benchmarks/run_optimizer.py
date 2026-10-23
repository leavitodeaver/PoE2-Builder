"""Optimizer vs naive baselines (Phase 2 exit criterion: complete, legal builds that beat them).

For each benchmark request, runs the optimizer, then the naive baselines (builder/search/baseline.py)
on the skeleton the optimizer chose, and compares main-skill and Full DPS. Every optimizer result
is also re-checked independently: its PoB code is loaded in a fresh engine and every requirement
is checked again from the reloaded build.

Usage: uv run python benchmarks/run_optimizer.py [name-filter ...] [--seconds 120] [--workers N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from builder import ROOT
from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.pob import codes
from builder.pob.pool import EnginePool
from builder.request.constraints import FACT_FIELDS, compile_request
from builder.request.schema import BuildRequest
from builder.search.context import make_payload
from builder.search.run import optimize

RESULT_DIR = ROOT / "benchmarks" / "results"
NO_RES = {"resists": {}}
BENCHMARKS = {
    "ice_shot_deadeye": {"ascendancies": ["Deadeye"], "main_skill": "Ice Shot", "floors": NO_RES},
    "spark_stormweaver": {"ascendancies": ["Stormweaver"], "main_skill": "Spark", "floors": NO_RES},
    "boneshatter_titan": {"ascendancies": ["Titan"], "main_skill": "Boneshatter", "floors": NO_RES},
    "raise_zombie_infernalist": {"ascendancies": ["Infernalist"], "main_skill": "Raise Zombie", "floors": NO_RES},
}


def recheck(pool: EnginePool, request: BuildRequest, code: str, data: GameData) -> list[dict]:
    """Every requirement checked again on the build loaded from the exported code."""
    pool.broadcast("loadXml", xml=codes.decode(code))
    facts = pool.broadcast("facts", fields=FACT_FIELDS)[0]
    return [c.__dict__ for c in compile_request(request, data).check(facts)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = GameData()
    prices = PriceFeed().table(refresh=False)
    rows = []
    for name, body in BENCHMARKS.items():
        if args.names and not any(f in name for f in args.names):
            continue
        request = BuildRequest(name=name, budget={"divines": 20}, **body)
        started = time.perf_counter()
        result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds)
        if "code" not in result:
            print(f"{name}: no build ({result['status']}: {'; '.join(result['reasons'])})")
            rows.append({"name": name, "status": result["status"], "reasons": result["reasons"]})
            continue
        with EnginePool(2) as pool:
            baseline = pool.run_tasks([("builder.search.baseline:baseline_task", {
                "payload": make_payload(request, prices), "skeleton": result["skeleton"]})])[0]
            rechecked = recheck(pool, request, result["code"], data)
        failing = [c["label"] for c in rechecked if c["ok"] is False]
        row = {
            "name": name, "skeleton": result["label"], "status": result["status"], "feasible": result["feasible"],
            "optimizer": {"main_dps": result["dps"]["main"], "full_dps": result["dps"]["full"]},
            "greedy": baseline["greedy"], "greedy_uniques": baseline["greedy_uniques"],
            "baseline_seconds": round(baseline["seconds"], 1), "seconds": round(time.perf_counter() - started, 1),
            "evaluations": result["evaluations"], "evals_per_second": result["evals_per_second"],
            "verified": result["verified"], "recheck_failing": failing,
            "recheck_agrees": sorted(failing) == sorted(c["label"] for c in result["checks"] if c["ok"] is False),
            "groups": [{"gems": [g["name"] for g in group["gems"]], "full_dps": group["fullDPS"], "main": group["main"]}
                       for group in result["groups"] if group["enabled"]],
            "code": result["code"],
        }
        rows.append(row)
        best_baseline = max(baseline["greedy"]["full_dps"], baseline["greedy_uniques"]["full_dps"])
        print(f"{name}: optimizer {row['optimizer']['full_dps']:,.0f} Full DPS vs greedy {baseline['greedy']['full_dps']:,.0f}, "
              f"greedy+uniques {baseline['greedy_uniques']['full_dps']:,.0f} "
              f"(x{row['optimizer']['full_dps'] / max(best_baseline, 1):.2f}); feasible={result['feasible']}, "
              f"verified={result['verified']}, recheck agrees={row['recheck_agrees']}", flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"optimizer-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"seconds": args.seconds, "rows": rows}, indent=1), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
