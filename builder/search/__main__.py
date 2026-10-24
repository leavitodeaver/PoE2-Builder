"""Run the optimizer on a saved request from the command line.

Usage: uv run python -m builder.search REQUEST.json [--seconds N] [--workers N] [--out RESULT.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder.data.prices import PriceFeed
from builder.request.schema import BuildRequest
from builder.search.run import optimize


def clear_text(clear: float | None) -> str:
    return f", clear ~{clear:,.0f}" if clear is not None else ""


def print_event(event: dict) -> None:
    kind = event["kind"]
    t = f"{event['t']:7.1f}s"
    if kind == "progress":
        print(f"{t}  {event['evals_per_second']:>6,}/s  leader {event['leader']}: {event['leader_dps']:,.0f} DPS "
              f"({event['phase']})", flush=True)
    elif kind == "best":
        short = f"  short: {', '.join(event['deficits'])}" if event["deficits"] else ""
        print(f"{t}  best   {event['skeleton']}: {event['dps']:,.0f} DPS{clear_text(event.get('clear'))}{short}", flush=True)
    elif kind == "probe":
        print(f"{t}  probe  {event['skeleton']}: {event['dps']:,.0f} DPS{clear_text(event.get('clear'))}", flush=True)
    elif kind == "skeletons":
        print(f"{t}  {event['count']} skeleton(s)" + (f"; dropped: {'; '.join(event['problems'])}" if event["problems"] else ""), flush=True)
    else:
        print(f"{t}  {event.get('message', kind)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize a build request.")
    parser.add_argument("request", type=Path)
    parser.add_argument("--seconds", type=float, help="time limit (default: the request's effort)")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--out", type=Path, help="write the full result as JSON")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    request = BuildRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
    try:
        prices = PriceFeed().table(request.league, refresh=False)
    except Exception as error:  # no cache yet: uniques can't be priced, so only bases are used
        print(f"no price data ({error}); run `python -m builder.data prices`", file=sys.stderr)
        prices = None
    result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds,
                      on_event=None if args.quiet else print_event)
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print()
    print(f"{result['status'].upper()}: {result.get('label', '')}")
    if "dps" in result:
        print(f"  Full DPS {result['dps']['full']:,.0f} (main skill {result['dps']['main']:,.0f}); "
              f"{result['evaluations']:,} evaluations in {result['seconds']}s ({result['evals_per_second']:,}/s)")
        clear = result["clear"]
        print(f"  Goal: {result['objective']['goal']}; boss DPS {result['dps']['full']:,.0f}; clear score "
              f"~{clear['score']:,.0f} (approximate: {clear['targets']:.1f} targets per use, "
              f"{clear['uses_per_second']:.2f} uses/s, movement x{clear['movement_speed']:.2f})")
    for check in result.get("checks", []):
        mark = {True: "ok ", False: "FAIL", None: " ? "}[check["ok"]]
        print(f"  [{mark}] {check['label']}: {check['requested']} -> {check['delivered']}")
    for reason in result.get("reasons", []):
        print(f"  reason: {reason}")
    for note in result.get("notes", []):
        print(f"  note: {note}")
    if result.get("code"):
        print(f"  PoB code: {len(result['code'])} chars{' (verified by reload)' if result['verified'] else ''}")


if __name__ == "__main__":
    main()
