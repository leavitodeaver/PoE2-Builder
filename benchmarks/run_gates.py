"""Engine quality gates from PLAN.md §8 plus single-worker timings.

Gate 1 (fidelity): reloading a build reproduces the PlayerStat values saved inside it. For builds
  exported from the PoB GUI (drop codes into benchmarks/reference/*.txt), this is GUI fidelity;
  for the generated test builds it checks that reloading is deterministic.
Gate 2 (delta agreement): every what-if answer from PoB's misc calculator matches committing the
  same change and recalculating fully, within 0.1%.
Gate 3 (round trip): export -> build code -> import reproduces every stat exactly.

Usage: uv run python benchmarks/run_gates.py [name-filter ...] [--pob-dir DIR] [--gates 123] [--no-timing]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime
from pathlib import Path

from builder import POB_DIR, ROOT
from builder.pob import codes
from builder.pob.engine import PobEngine

BUILD_DIR = ROOT / "benchmarks" / "builds"
REFERENCE_DIR = ROOT / "benchmarks" / "reference"
RESULT_DIR = ROOT / "benchmarks" / "results"

FIELDS = [
    "FullDPS", "CombinedDPS", "TotalDPS", "TotalDotDPS", "Minion.CombinedDPS", "Life", "EnergyShield",
    "Mana", "TotalEHP", "Armour", "Evasion", "FireResist", "ColdResist", "LightningResist",
    "ChaosResist", "Spirit", "Str", "Dex", "Int",
]
DELTA_TOLERANCE = 1e-3
EXACT_TOLERANCE = 1e-9
CASES_PER_KIND = 8
TIMING_CASES = 40


def number(value: object) -> object:
    # The bridge reports non-finite numbers as strings ("inf", "nan"), since JSON has no form for them
    return float(value) if value in ("inf", "-inf", "nan") else value


def close(a: object, b: object, rel: float) -> bool:
    a, b = number(a), number(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b or (a != a and b != b):  # equal infinities, or both NaN
            return True
        return abs(a - b) <= rel * max(abs(a), abs(b), 1.0)
    return a == b


def diff(expected: dict, actual: dict, rel: float) -> list[str]:
    problems = []
    for field in sorted(set(expected) | set(actual)):
        a, b = expected.get(field, 0), actual.get(field, 0)
        if not close(a, b, rel):
            problems.append(f"{field}: {a} vs {b}")
    return problems


def load_sources() -> dict[str, str]:
    sources = {path.stem: path.read_text(encoding="utf-8") for path in sorted(BUILD_DIR.glob("*.xml"))}
    for path in sorted(REFERENCE_DIR.glob("*.txt")):
        try:
            sources["ref:" + path.stem] = codes.decode(path.read_text(encoding="utf-8"))
        except (ValueError, zlib.error):
            print(f"skipping {path.name}: not a PoB code")
    return sources


def saved_stats(xml: str) -> dict[str, float]:
    root = ET.fromstring(xml)
    stats = {}
    for elem in root.iter("PlayerStat"):
        stats[elem.get("stat")] = elem.get("value")
    for elem in root.iter("MinionStat"):
        stats["Minion." + elem.get("stat")] = elem.get("value")
    parsed = {}
    for key, value in stats.items():
        try:
            parsed[key] = float(value)
        except (TypeError, ValueError):
            continue
    return parsed


def gate_fidelity(engine: PobEngine, xml: str) -> list[str]:
    saved = saved_stats(xml)
    engine.call("loadXml", xml=xml)
    actual = engine.call("stats", fields=list(saved))
    return diff(saved, {k: v for k, v in actual.items() if k in saved}, EXACT_TOLERANCE)


def gate_delta(engine: PobEngine, xml: str, rng: random.Random) -> tuple[int, list[str]]:
    engine.call("loadXml", xml=xml)
    frontier = engine.call("frontier", maxDistance=3)["nodes"]
    leaves = [n for n in engine.call("allocatedNodes")["nodes"] if n["depends"] == [n["id"]]]
    equipped = engine.call("equipped")["slots"]
    cases = [("add", n) for n in rng.sample(frontier, min(CASES_PER_KIND, len(frontier)))]
    cases += [("remove", n) for n in rng.sample(leaves, min(CASES_PER_KIND, len(leaves)))]
    cases += [("item", s) for s in rng.sample(sorted(equipped), min(CASES_PER_KIND, len(equipped)))]
    problems = []
    for kind, target in cases:
        engine.call("loadXml", xml=xml)
        if kind == "add":
            delta = engine.call("nodeDeltas", cases=[{"add": [target["id"]], "addPaths": True}], fields=FIELDS)
            commit = engine.call("allocate", add=[target["id"]], fields=FIELDS)
            label = f"add {target['name']} ({target['distance']} pts)"
        elif kind == "remove":
            delta = engine.call("nodeDeltas", cases=[{"remove": target["depends"]}], fields=FIELDS)
            commit = engine.call("allocate", remove=[target["id"]], fields=FIELDS)
            label = f"remove {target['name']}"
        else:
            raw = equipped[target] + "\n+57 to maximum Life\n+11% to Fire Resistance\n12% increased Damage"
            delta = engine.call("itemDeltas", cases=[{"slot": target, "raw": raw}], fields=FIELDS)
            commit = engine.call("equip", slot=target, raw=raw, fields=FIELDS)
            label = f"item {target}"
        for problem in diff(commit["stats"], delta["results"][0], DELTA_TOLERANCE):
            problems.append(f"{label}: {problem}")
    return len(cases), problems


def gate_round_trip(engine: PobEngine, xml: str) -> list[str]:
    engine.call("loadXml", xml=xml)
    before = engine.call("stats", fields=FIELDS)
    exported = engine.call("exportXml")["xml"]
    restored = codes.decode(codes.encode(exported))
    if restored != exported:
        return ["build code changed the XML"]
    engine.call("loadXml", xml=restored)
    return diff(before, engine.call("stats", fields=FIELDS), EXACT_TOLERANCE)


def timings(engine: PobEngine, xml: str) -> dict[str, float]:
    """Milliseconds per operation, measured inside Lua. What-ifs are timed with Full DPS (all
    included skills) and with the main skill only, on up to TIMING_CASES cases each."""
    result = {}
    started = time.perf_counter()
    for _ in range(3):
        engine.call("loadXml", xml=xml)
    result["load_xml_ms"] = 1000 * (time.perf_counter() - started) / 3
    recalcs = [engine.call("recalc", fields=FIELDS)["seconds"] for _ in range(5)]
    result["full_recalc_ms"] = 1000 * statistics.median(recalcs)

    frontier = engine.call("frontier", maxDistance=4)["nodes"]
    leaves = [n for n in engine.call("allocatedNodes")["nodes"] if n["depends"] == [n["id"]]]
    equipped = engine.call("equipped")["slots"]
    batches = {
        "delta_node": [{"add": [n["id"]]} for n in frontier if n["distance"] == 1],
        "delta_path": [{"add": [n["id"]], "addPaths": True} for n in frontier if n["distance"] > 1],
        "delta_remove": [{"remove": [n["id"]]} for n in leaves],
        "delta_item": [{"slot": s, "raw": raw + "\n+40 to maximum Life"} for s, raw in sorted(equipped.items())],
    }
    for name, cases in batches.items():
        cases = cases[:TIMING_CASES]
        if not cases:
            continue
        method = "itemDeltas" if name == "delta_item" else "nodeDeltas"
        for suffix, full_dps in (("_ms", True), ("_main_skill_ms", False)):
            response = engine.call(method, cases=cases, fields=FIELDS, fullDPS=full_dps)
            result[name + suffix] = 1000 * response["seconds"] / len(cases)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="only builds whose name contains one of these")
    parser.add_argument("--pob-dir", type=Path, default=POB_DIR, help="PoB checkout to run (default: pinned)")
    parser.add_argument("--gates", default="123", help="which gates to run, e.g. 1 or 13 (default 123)")
    parser.add_argument("--no-timing", action="store_true")
    args = parser.parse_args()

    sources = {n: x for n, x in load_sources().items() if not args.names or any(f in n for f in args.names)}
    engine = PobEngine(args.pob_dir)
    rng = random.Random(20261022)
    report = {
        "date": datetime.now().isoformat(timespec="seconds"), "pob_dir": str(args.pob_dir),
        "boot_s": engine.boot_seconds, "builds": {},
    }
    failed = False
    status = lambda problems: "PASS" if not problems else f"FAIL ({len(problems)})"
    for name, xml in sources.items():
        entry: dict = {}
        print(f"\n{name}", flush=True)
        if "1" in args.gates:
            entry["gate1_fidelity"] = gate_fidelity(engine, xml)
            print(f"  gate 1 fidelity    {status(entry['gate1_fidelity'])}", flush=True)
        if "2" in args.gates:
            entry["gate2_delta_cases"], entry["gate2_delta"] = gate_delta(engine, xml, rng)
            print(f"  gate 2 delta       {status(entry['gate2_delta'])} over {entry['gate2_delta_cases']} cases", flush=True)
        if "3" in args.gates:
            entry["gate3_round_trip"] = gate_round_trip(engine, xml)
            print(f"  gate 3 round trip  {status(entry['gate3_round_trip'])}", flush=True)
        problems = entry.get("gate1_fidelity", []) + entry.get("gate2_delta", []) + entry.get("gate3_round_trip", [])
        for problem in problems[:12]:
            print(f"    - {problem}")
        failed |= bool(problems)
        if not args.no_timing:
            entry["timing"] = timings(engine, xml)
            print("  timing (ms): " + ", ".join(f"{k} {v:.1f}" for k, v in entry["timing"].items()), flush=True)
        report["builds"][name] = entry
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gates-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nboot {engine.boot_seconds:.2f}s; report: {out.relative_to(ROOT)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
