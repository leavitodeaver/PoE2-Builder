"""Machine settings (data/settings.json), as opposed to per-build requests.

`workers` is how many PoB worker processes an optimizer run uses. Each one keeps a CPU thread
busy, so it's the knob for how hard a run works the computer. Counts come from the processors
this process may run on (its affinity mask), not the machine's thread count. Inside the PC queue
(a run booked through tools/pc_queue.py) the user's setting doesn't apply: a run uses the
processors its job gives it, at most 6 workers (builder/pob/slots.py).
"""

from __future__ import annotations

import json

from builder.data import DATA_ROOT
from builder.pob.slots import POOL_MAX, available_processors, in_queue

SETTINGS_FILE = DATA_ROOT / "settings.json"


def max_workers() -> int:
    """One per processor this process may use, leaving one for the web page and everything else;
    inside the PC queue, one per processor of its job, POOL_MAX at most."""
    if in_queue():
        return max(1, min(POOL_MAX, available_processors()))
    return max(1, available_processors() - 1)


def recommended_workers() -> int:
    """Half the processors: most of the speed (8 of 16 threads gave ~73% of 14 workers' throughput
    in benchmarks/bench_pool.py) while leaving the computer comfortable to use. Inside the PC queue,
    as many as it allows (max_workers)."""
    if in_queue():
        return max_workers()
    return max(1, available_processors() // 2)


def load() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def workers() -> int:
    if in_queue():  # the queue's limits, whatever the user chose for their own runs
        return max_workers()
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
