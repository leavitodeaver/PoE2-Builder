"""User-style benchmark: requests written the way the user uses the tool (a skill, ascendancy, button cap, bans,
defence style and floors, budget; no PoB code), from the user's hand-audited build docs (legacy/Custom builds).
There's no reference build to beat: each run's boss DPS and its feasibility are the record, compared between search
methods on one tree (SEARCH_PLAN.md §5).

Usage: uv run python benchmarks/gate_user.py [name-filter ...] [--seconds 600] [--workers N] [--events FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from builder import ROOT
from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.request.constraints import compile_request
from builder.request.schema import BuildRequest
from builder.search.run import optimize

REQUEST_DIR = ROOT / "benchmarks" / "user_requests"
RESULT_DIR = ROOT / "benchmarks" / "results"


def load_requests(names: list[str]) -> list[tuple[str, BuildRequest]]:
    """Every request file the filters match, checked: one the request compiler rejects stops the run."""
    data = GameData()
    found = []
    for path in sorted(REQUEST_DIR.glob("*.json")):
        if names and not any(f in path.stem for f in names):
            continue
        request = BuildRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        errors = compile_request(request, data).errors
        if errors:
            raise SystemExit(f"{path.name}: " + "; ".join(f"{e.field}: {e.message}" for e in errors))
        found.append((path.stem, request))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--events", help="append the run's events (all but progress) to this JSONL file")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    prices = PriceFeed().table(refresh=False)
    rows = []
    for name, request in load_requests(args.names):
        def on_event(event: dict, name: str = name) -> None:
            if args.events and event.get("kind") != "progress":
                with open(args.events, "a", encoding="utf-8") as log:
                    log.write(json.dumps({"build": name, **event}) + "\n")
        result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds, on_event=on_event)
        dps = (result.get("dps") or {}).get("full", 0.0)
        failing = [c["label"] for c in result.get("checks", []) if c["ok"] is False]
        rows.append({"name": name, "skeleton": result.get("label"), "dps": dps, "feasible": result.get("feasible"),
                     "failing": failing, "reasons": result.get("reasons"), "seconds": result.get("seconds"),
                     "evaluations": result.get("evaluations"), "request": request.model_dump(mode="json"),
                     "code": result.get("code")})
        print(f"[{'FEASIBLE' if result.get('feasible') else 'INFEASIBLE'}] {name} {result.get('label')}: {dps:,.0f} "
              + ("all requirements met" if result.get("feasible") else "short: " + ", ".join(failing or result.get("reasons") or [])),
              flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gate_user-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"seconds": args.seconds, "rows": rows}, indent=1), encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
