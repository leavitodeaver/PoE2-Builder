"""Machine settings (data/settings.json), as opposed to per-build requests.

`workers` is how many PoB worker processes an optimizer run uses. Each one keeps a CPU thread
busy, so it's the knob for how hard a run works the computer.
"""

from __future__ import annotations

import json
import os

from builder.data import DATA_ROOT

SETTINGS_FILE = DATA_ROOT / "settings.json"


def max_workers() -> int:
    """One per CPU thread, leaving one for the web page and everything else."""
    return max(1, (os.cpu_count() or 2) - 1)


def recommended_workers() -> int:
    """Half the CPU threads: most of the speed (8 of 16 threads gave ~73% of 14 workers' throughput
    in benchmarks/bench_pool.py) while leaving the computer comfortable to use."""
    return max(1, (os.cpu_count() or 2) // 2)


def load() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def workers() -> int:
    chosen = load().get("workers")
    if isinstance(chosen, int) and chosen >= 1:
        return min(chosen, max_workers())
    return recommended_workers()


def set_workers(count: int) -> int:
    count = max(1, min(int(count), max_workers()))
    settings = load()
    settings["workers"] = count
    save(settings)
    return count
