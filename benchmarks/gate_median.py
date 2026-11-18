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
import base64
import json
import subprocess
import sys
import zlib
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Recipe-made bases the search offered as FREE plain bases until train 63 (SEARCH24's 2c58be9d, on main 16 Nov 03:01:
# builder/search/context.py's CRAFTED_BASES / crafted_base_unpriced: no price, no offer outside SSF). A gate run on an
# older commit could wear one at no cost (SEARCH25, 16 Nov: all three of main c4202146's 2b75c runs wore Grasping Mail):
# an OVERCOUNT of that run, so it is marked, and a baseline set holding one is named as such (the manager, 16-17 Nov:
# use post-train-63 baselines, e.g. main c712c8be's 2b75c set).
try:
    sys.path.insert(0, str(ROOT))
    from builder.search.context import CRAFTED_BASES as _CRAFTED
    CRAFTED_BASES = tuple(_CRAFTED)
except Exception:  # (a gate file read where the builder can't import: the names as of train 63)
    CRAFTED_BASES = ("Grasping Mail",)


def worn_crafted_bases(code: str | None) -> list[str]:
    """The CRAFTED_BASES the run's build code wears (a PoB code: url-safe base64 of zlib-deflated XML)."""
    if not code:
        return []
    try:
        xml = zlib.decompress(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))).decode("utf-8", "replace")
    except Exception:
        return []
    return [b for b in CRAFTED_BASES if b in xml]


@cache
def has_crafted_rule(commit: str | None) -> bool:
    """Whether `commit`'s search keeps an unpriced recipe-made base out (crafted_base_unpriced in its context.py).
    An unknown commit counts as without it."""
    if not commit:
        return False
    found = subprocess.run(["git", "grep", "-q", "crafted_base_unpriced", commit, "--", "builder/search/context.py"],
                           cwd=ROOT, capture_output=True)
    return found.returncode == 0


def load(paths: list[str]) -> dict[str, list[dict]]:
    """Build name -> its runs: {dps, ratio, feasible, passed, reference, seed_base, evaluations, commit, file}."""
    runs: dict[str, list[dict]] = {}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data["rows"]:
            floors = floors_kind(row)
            feasible = bool(row["feasible_fair_floors"] if floors == "refloored" else row["feasible"])
            runs.setdefault(row["name"], []).append({
                "dps": row["dps"], "ratio": row["ratio"], "feasible": feasible, "passed": row["passed"],
                "floors": floors, "feasible_as_run": bool(row["feasible"]),
                "reference": row["reference_dps"], "seed_base": data.get("seed_base"),
                "evaluations": row.get("evaluations"), "commit": data.get("commit"), "file": Path(path).name,
                "free_base": [b for b in worn_crafted_bases(row.get("code")) if not has_crafted_rule(data.get("commit"))]})
    return runs


def floors_kind(row: dict) -> str:
    """Where a run's 'floors met' comes from (the manager's ruling, 18 Nov 06:25: a reference's floors come from the
    same fair scoring the search uses; gate4 does so since SEARCH27): "fair" (gate4 took them so), "refloored" (an
    older run whose delivered build benchmarks/refloor.py scored again under the fair floors: feasible_fair_floors),
    "pob" (an older run judged against floors read from PoB before our models: its 'floors met' isn't comparable)."""
    if row.get("floors_from") == "fair":
        return "fair"
    return "refloored" if "feasible_fair_floors" in row else "pob"


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
                  f"{r['commit']}; {r['file']})"
                  + (f" [FREE BASE: wears {', '.join(r['free_base'])} at no cost (before train 63): an OVERCOUNT]"
                     if r.get("free_base") else "")
                  + (" [floors from PoB before our models (before the 18 Nov ruling): 'feasible' not comparable]"
                     if r.get("floors") == "pob" else "")
                  + (f" [floors met re-scored under the fair floors (refloor.py); as run: "
                     f"{'feasible' if r['feasible_as_run'] else 'not feasible'}]" if r.get("floors") == "refloored" else ""))
        s = summary(rows)
        line = describe(name, s)
        if name in base:
            b = summary(base[name])
            line += f"; vs the baseline's median {b['median']['dps']:,.0f} (worst {b['low']['dps']:,.0f}, best " \
                    f"{b['high']['dps']:,.0f}): {compare(s, b)}"
            free = [r["file"] for r in base[name] if r.get("free_base")]
            if free:
                line += (f" [the baseline holds {len(free)} run(s) wearing a free recipe-made base (before train 63: "
                         f"{', '.join(free)}): not a fair baseline; use a post-train-63 set]")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
