"""What the model trials cost a real run (the what-if limit): one request on a build with a model on its own numbers
(Spark Stormweaver with Mana Tempest, whose uptime follows Mana and regeneration), run for a fixed time with the model
inputs worked out in the bridge (today), or with BUILDER_RESCORE_MODELS=1, the real trials it needed before.

Usage: uv run python benchmarks/rescore_cost.py bridge|trials [--seconds 600] [--workers 6]
Writes benchmarks/results/rescore-<mode>-<time>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

from builder import ROOT
from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest, Choices

RESULT_DIR = ROOT / "benchmarks" / "results"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["bridge", "trials"])
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.mode == "trials":
        os.environ["BUILDER_RESCORE_MODELS"] = "1"  # before the search modules load (worker processes inherit it)
    from builder.search.run import optimize

    request = BuildRequest(name=f"rescore-{args.mode}", budget={"divines": 20}, objective="boss", main_skill="Spark",
                           ascendancies=["Stormweaver"], require=Choices(skills=["Mana Tempest"]), floors={"resists": {}})
    started = time.perf_counter()
    result = optimize(request, prices=PriceFeed().table(refresh=False), workers=args.workers, seconds=args.seconds)
    row = {"mode": args.mode, "status": result["status"], "dps": result.get("dps"), "value": result.get("value"),
           "seconds": round(time.perf_counter() - started, 1), "evaluations": result.get("evaluations"),
           "evals_per_second": result.get("evals_per_second"),
           "tempest": [n for n in result.get("assumptions", []) if "Tempest" in str(n.get("label", ""))][:1]}
    print(json.dumps(row, indent=1, default=str))
    RESULT_DIR.mkdir(exist_ok=True)
    out = RESULT_DIR / f"rescore-{args.mode}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(row, indent=1, default=str), encoding="utf-8")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
