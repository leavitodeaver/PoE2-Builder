"""Engine quality gates from PLAN.md §8 plus single-worker timings.

Gate 1 (fidelity): reloading a build reproduces the PlayerStat values saved inside it. For builds
  exported from the PoB GUI (drop codes into benchmarks/reference/*.txt), this is GUI fidelity;
  for the generated test builds it checks that reloading is deterministic.
Gate 2 (delta agreement): every what-if answer from PoB's misc calculator matches committing the
  same change and recalculating fully, within 0.1%.
Gate 3 (round trip): export -> build code -> import reproduces every stat exactly.

Usage: uv run python benchmarks/run_gates.py
"""

from __future__ import annotations

import json
import random
import statistics
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from builder import ROOT
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


def close(a: object, b: object, rel: float) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
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
        sources["ref:" + path.stem] = codes.decode(path.read_text(encoding="utf-8"))
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
    """Milliseconds per operation. 'lua' excludes the ctypes/JSON crossing; 'wall' includes it."""
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
        "delta_node_ms": [{"add": [n["id"]]} for n in frontier if n["distance"] == 1],
        "delta_path_ms": [{"add": [n["id"]], "addPaths": True} for n in frontier if n["distance"] > 1],
        "delta_remove_ms": [{"remove": [n["id"]]} for n in leaves],
    }
    for name, cases in batches.items():
        if not cases:
            continue
        started = time.perf_counter()
        response = engine.call("nodeDeltas", cases=cases, fields=FIELDS)
        result[name] = 1000 * response["seconds"] / len(cases)
        result[name.replace("_ms", "_wall_ms")] = 1000 * (time.perf_counter() - started) / len(cases)
    item_cases = [{"slot": s, "raw": raw + "\n+40 to maximum Life"} for s, raw in sorted(equipped.items())] * 5
    response = engine.call("itemDeltas", cases=item_cases, fields=FIELDS)
    result["delta_item_ms"] = 1000 * response["seconds"] / len(item_cases)
    return result


def main() -> None:
    sources = load_sources()
    engine = PobEngine()
    rng = random.Random(20261022)
    report = {"date": datetime.now().isoformat(timespec="seconds"), "boot_s": engine.boot_seconds, "builds": {}}
    failed = False
    for name, xml in sources.items():
        fidelity = gate_fidelity(engine, xml)
        delta_cases, delta = gate_delta(engine, xml, rng)
        round_trip = gate_round_trip(engine, xml)
        timing = timings(engine, xml)
        report["builds"][name] = {
            "gate1_fidelity": fidelity, "gate2_delta_cases": delta_cases, "gate2_delta": delta,
            "gate3_round_trip": round_trip, "timing": timing,
        }
        failed |= bool(fidelity or delta or round_trip)
        status = lambda problems: "PASS" if not problems else f"FAIL ({len(problems)})"
        print(f"\n{name}")
        print(f"  gate 1 fidelity    {status(fidelity)}")
        print(f"  gate 2 delta       {status(delta)} over {delta_cases} cases")
        print(f"  gate 3 round trip  {status(round_trip)}")
        for problem in (fidelity + delta + round_trip)[:12]:
            print(f"    - {problem}")
        print("  timing (ms): " + ", ".join(f"{k} {v:.2f}" for k, v in timing.items()))
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gates-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nboot {engine.boot_seconds:.2f}s; report: {out.relative_to(ROOT)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
