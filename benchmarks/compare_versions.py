"""Compare a build's numbers between the pinned PoB snapshot and a PoB release.

For each build it reports the key stats under both versions, the largest other differences in
PoB's output, and every item modifier line the two versions read differently. That's usually
enough to tell a real change (new data, newly supported mods) from a bug.

Usage: uv run python benchmarks/compare_versions.py [name-filter ...] [--release-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

from builder import ENGINE_DIR, ROOT
from builder.pob.pool import EnginePool
from run_gates import load_sources

RESULT_DIR = ROOT / "benchmarks" / "results"
KEY_STATS = [
    "FullDPS", "CombinedDPS", "TotalDPS", "TotalDotDPS", "Minion.CombinedDPS", "Life", "EnergyShield",
    "Mana", "Ward", "TotalEHP", "PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ChaosMaximumHitTaken",
    "Armour", "Evasion", "FireResist", "ChaosResist", "Spirit",
]


def as_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value in ("inf", "-inf", "nan"):
        return float(value)
    return None


def change(old: float | None, new: float | None) -> str:
    if old is None or new is None:
        return "n/a"
    if old == new:
        return "same"
    if old == 0 or math.isinf(old) or math.isinf(new):
        return f"{old:g} -> {new:g}"
    return f"{100 * (new - old) / abs(old):+.1f}%"


def mod_differences(release: dict, snapshot: dict) -> list[str]:
    lines = []
    for slot in sorted(set(release) | set(snapshot)):
        old = {(m["kind"], m["line"]): m for m in release.get(slot, {}).get("lines", [])}
        new = {(m["kind"], m["line"]): m for m in snapshot.get(slot, {}).get("lines", [])}
        for key in sorted(set(old) | set(new)):
            a, b = old.get(key), new.get(key)
            if a and b and (a["supported"], a["mods"]) == (b["supported"], b["mods"]):
                continue
            describe = lambda m: "absent" if not m else ("supported" if m["supported"] else "UNSUPPORTED") + f" ({m['mods']} mods)"
            lines.append(f"{slot} [{key[0]}] {key[1]}: release {describe(a)} -> snapshot {describe(b)}")
    return lines


def compare(name: str, xml: str, snapshot: EnginePool, release: EnginePool, top: int) -> list[str]:
    out = [f"## {name}", ""]
    summaries = [pool.broadcast("loadXml", xml=xml)[0] for pool in (release, snapshot)]
    s = summaries[1]
    out.append(f"{s['className']} / {s['ascendClassName']}, level {s['level']}, main skill {s['mainSkill']}")
    out.append("")
    old_out, new_out = (pool.broadcast("outputAll")[0] for pool in (release, snapshot))
    out.append("| Stat | Release | Snapshot | Change |")
    out.append("|---|---|---|---|")
    show = lambda v: "-" if v is None else f"{v:,.6g}"
    for stat in KEY_STATS:
        a, b = as_number(old_out.get(stat)), as_number(new_out.get(stat))
        if a is None and b is None:
            continue
        out.append(f"| {stat} | {show(a)} | {show(b)} | {change(a, b)} |")
    out.append("")

    ranked = []
    for stat in set(old_out) | set(new_out):
        if stat in KEY_STATS:
            continue
        a, b = as_number(old_out.get(stat)), as_number(new_out.get(stat))
        if a is None or b is None or a == b or math.isnan(a) or math.isnan(b):
            if (a is None) != (b is None):
                ranked.append((math.inf, stat, a, b))
            continue
        if math.isinf(a) or math.isinf(b):
            ranked.append((math.inf, stat, a, b))
            continue
        rel = abs(b - a) / max(abs(a), abs(b), 1e-9)
        if abs(b - a) > 1e-6:
            ranked.append((rel, stat, a, b))
    ranked.sort(key=lambda r: -r[0])
    if ranked:
        out.append(f"Largest other differences (top {min(top, len(ranked))} of {len(ranked)}):")
        for _, stat, a, b in ranked[:top]:
            out.append(f"- {stat}: {a} -> {b} ({change(a, b)})")
        out.append("")

    mods = mod_differences(release.broadcast("itemMods")[0], snapshot.broadcast("itemMods")[0])
    out.append(f"Item modifier lines read differently: {len(mods)}")
    out.extend(f"- {line}" for line in mods)
    out.append("")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="only builds whose name contains one of these")
    pin = json.loads((ENGINE_DIR / "pob-pin.json").read_text(encoding="utf-8"))
    parser.add_argument("--release-dir", type=Path, default=ROOT / "vendor" / f"PathOfBuilding-PoE2-{pin['release_tag']}")
    parser.add_argument("--top", type=int, default=15, help="how many other differing stats to list")
    args = parser.parse_args()

    sources = {n: x for n, x in load_sources().items() if not args.names or any(f in n for f in args.names)}
    report = [f"# PoB version comparison, {datetime.now():%Y-%m-%d %H:%M}", "",
              f"Release: `{args.release_dir.name}`; snapshot: pinned checkout", ""]
    with EnginePool(1) as snapshot, EnginePool(1, pob_dir=args.release_dir) as release:
        for name, xml in sources.items():
            section = compare(name, xml, snapshot, release, args.top)
            print("\n".join(section), flush=True)
            report.extend(section)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"version-diff-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"report: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
