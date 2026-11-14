"""A gate verdict from a set of gate4 runs (SEARCH20): the median run, the spread, and a comparison with a baseline set.

One gate4 run of the search is one draw: on 14 Nov two --cpu runs of one commit gave 2b75d 4,956 and 15,518 (x0.02 /
x0.07). They matched through the probe and the first focus round (the same seeds give the same numbers) and split in the
last one, where tasks restarted from the same state with the same seeds ended anywhere from 774 to 12,423: the search
is still far from converged there, and the small timing and order differences between two runs (the processor-time
clock, PoB's own per-process table order) send it to different builds. So a gate verdict is the median of N runs, each
on its own draw set (gate4 --seed-base 0, 1, ... N-1: task seeds and the Python hash seed), with every run and the
spread reported; a code change is compared with a baseline set on the same seed bases.

Usage: uv run python benchmarks/gate_median.py RUN.json ... [--against BASE.json ...]
A run's rank: a feasible run above an infeasible one, then by DPS (as the search ranks). The verdict per build:
- PASS when the median run passes (feasible, DPS >= the reference);
- against a baseline: "higher" when this set's median is above the baseline's best run, "lower" when below its worst
  run, else "within the spread" (no measurable change).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(paths: list[str]) -> dict[str, list[dict]]:
    """Build name -> its runs: {dps, ratio, feasible, passed, reference, seed_base, evaluations, commit, file}."""
    runs: dict[str, list[dict]] = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data["rows"]:
            runs.setdefault(row["name"], []).append({
                "dps": row["dps"], "ratio": row["ratio"], "feasible": bool(row["feasible"]), "passed": row["passed"],
                "reference": row["reference_dps"], "seed_base": data.get("seed_base"),
                "evaluations": row.get("evaluations"), "commit": data.get("commit"), "file": Path(path).name})
    return runs


def rank(run: dict) -> tuple[bool, float]:
    return run["feasible"], run["dps"]


def summary(runs: list[dict]) -> dict:
    """The median run (the lower middle one of an even set: a verdict shouldn't round up), the best and worst runs, and
    the spread (best DPS / worst DPS among them)."""
    ordered = sorted(runs, key=rank)
    median = ordered[(len(ordered) - 1) // 2]
    low, high = ordered[0], ordered[-1]
    return {"runs": len(runs), "median": median, "low": low, "high": high,
            "spread": high["dps"] / low["dps"] if low["dps"] > 0 else float("inf"), "passed": median["passed"]}


def compare(this: dict, base: dict) -> str:
    if rank(this["median"]) > rank(base["high"]):
        return "higher"
    if rank(this["median"]) < rank(base["low"]):
        return "lower"
    return "within the spread"


def describe(name: str, s: dict) -> str:
    m = s["median"]
    return (f"[{'PASS' if s['passed'] else 'FAIL'}] {name}: median {m['dps']:,.0f} (x{m['ratio']:.2f}"
            f"{'' if m['feasible'] else ', not feasible'}) of {s['runs']} run(s); worst {s['low']['dps']:,.0f}, best "
            f"{s['high']['dps']:,.0f} (spread x{s['spread']:.1f})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--against", nargs="+", default=[])
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    runs, base = load(args.runs), load(args.against)
    for name, rows in runs.items():
        for r in sorted(rows, key=lambda r: (r["seed_base"] is None, r["seed_base"] or 0)):
            print(f"  {name} seed base {r['seed_base']}: {r['dps']:,.0f} (x{r['ratio']:.2f}, "
                  f"{'feasible' if r['feasible'] else 'not feasible'}; {r['evaluations'] or 0:,} evaluations; "
                  f"{r['commit']}; {r['file']})")
        s = summary(rows)
        line = describe(name, s)
        if name in base:
            b = summary(base[name])
            line += f"; vs the baseline's median {b['median']['dps']:,.0f} (worst {b['low']['dps']:,.0f}, best " \
                    f"{b['high']['dps']:,.0f}): {compare(s, b)}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
