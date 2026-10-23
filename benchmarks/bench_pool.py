"""Aggregate what-if throughput of the engine pool at several worker counts.

Usage: uv run python benchmarks/bench_pool.py [build-name] [total-cases]
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import time
from datetime import datetime

from builder import ROOT
from builder.pob.pool import EnginePool

BUILD_DIR = ROOT / "benchmarks" / "builds"
RESULT_DIR = ROOT / "benchmarks" / "results"
FIELDS = ["FullDPS", "CombinedDPS", "Minion.CombinedDPS", "Life", "EnergyShield", "TotalEHP"]
SIZES = [1, 4, 8, 12, 14, 16]


def working_set_mb(pid: int) -> float:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    row = next(csv.reader(io.StringIO(out)), None)
    return float(row[4].replace(",", "").replace(" K", "")) / 1024 if row and len(row) > 4 else float("nan")


def case_list(xml: str, total: int) -> list[dict]:
    with EnginePool(1) as pool:
        pool.broadcast("loadXml", xml=xml)
        frontier = pool.broadcast("frontier", maxDistance=4)[0]["nodes"]
        allocated = pool.broadcast("allocatedNodes")[0]["nodes"]
    base = [{"add": [n["id"]], "addPaths": True} for n in frontier]
    base += [{"remove": n["depends"]} for n in allocated if n["depends"] == [n["id"]]]
    return (base * (total // len(base) + 1))[:total]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "boneshatter_titan"
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 12000
    xml = (BUILD_DIR / f"{name}.xml").read_text(encoding="utf-8")
    cases = case_list(xml, total)
    rows = []
    for size in SIZES:
        with EnginePool(size) as pool:
            pool.broadcast("loadXml", xml=xml)
            pool.scatter("nodeDeltas", cases[: size * 64], chunk_size=64, fields=FIELDS)  # warm up
            started = time.perf_counter()
            pool.scatter("nodeDeltas", cases, chunk_size=64, fields=FIELDS)
            elapsed = time.perf_counter() - started
            memory = working_set_mb(pool._procs[0].pid)
        row = {
            "workers": size, "startup_s": round(pool.startup_seconds, 2), "cases": len(cases),
            "seconds": round(elapsed, 2), "per_second": round(len(cases) / elapsed),
            "worker_mb": round(memory),
        }
        rows.append(row)
        print(f"{size:>2} workers: startup {row['startup_s']:>5.2f}s, {row['per_second']:>6,} what-ifs/s, "
              f"{row['worker_mb']} MB per worker")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"pool-{name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"build": name, "fields": FIELDS, "rows": rows}, indent=2), encoding="utf-8")
    print(f"report: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
