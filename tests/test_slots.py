"""PoE2's limits in the PC queue (builder/pob/slots.py): 6 LuaJIT processes at most, on the job's processors."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from builder import settings
from builder.pob import slots

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows named mutexes")

HOLDER = """
import os, sys, time
from builder.pob import slots
slots.SLOT_NAME = sys.argv[1]
slots.take()
print("took", os.getpid(), flush=True)
time.sleep(60)
"""


@pytest.fixture
def fresh(monkeypatch):
    name = f"Local\\PoE2Builder.test{os.getpid()}.{time.time_ns()}.%d"  # never the real slots
    monkeypatch.setattr(slots, "SLOT_NAME", name)
    monkeypatch.setattr(slots, "_held", None)
    monkeypatch.setattr(slots, "_held_index", None)
    return name


def test_nothing_is_limited_outside_the_queue(monkeypatch, fresh):
    monkeypatch.delenv("PC_QUEUE_KIND", raising=False)
    assert slots.free() is None
    slots.take()
    assert slots._held is None


def test_a_seventh_process_gets_no_engine_and_a_dead_ones_slot_comes_back(monkeypatch, fresh):
    monkeypatch.setenv("PC_QUEUE_KIND", "test")
    env = {**os.environ, "PC_QUEUE_KIND": "test"}
    holders = [subprocess.Popen([sys.executable, "-c", HOLDER, fresh], stdout=subprocess.PIPE, text=True, env=env)
               for _ in range(slots.POOL_MAX)]
    try:
        took = [p.stdout.readline().split() for p in holders]
        assert all(t and t[0] == "took" for t in took)
        assert slots.free() == 0
        with pytest.raises(slots.PoolFull):
            slots.take()
        # kill the process that holds the slot (a venv's python.exe may be a launcher with the interpreter as its child)
        subprocess.run(["taskkill", "/F", "/T", "/PID", took[0][1]], capture_output=True)
        holders[0].kill()
        holders[0].wait()
        end = time.time() + 5
        while slots.free() != 1 and time.time() < end:
            time.sleep(0.1)
        assert slots.free() == 1  # Windows frees a killed process's slot
        slots.take()
        assert slots.free() == 0  # this process's own slot doesn't count as free
    finally:
        for p in holders:
            p.kill()
        for t in locals().get("took") or []:
            if len(t) > 1:
                subprocess.run(["taskkill", "/F", "/T", "/PID", t[1]], capture_output=True)


def test_worker_counts_inside_the_queue_come_from_its_processors(monkeypatch):
    monkeypatch.setenv("PC_QUEUE_KIND", "test")
    assert settings.workers() == settings.max_workers() == min(slots.POOL_MAX, slots.available_processors())
    monkeypatch.delenv("PC_QUEUE_KIND")
    assert settings.max_workers() == max(1, slots.available_processors() - 1)


def test_available_processors_is_the_affinity_mask():
    assert 1 <= slots.available_processors() <= (os.cpu_count() or 1)


def test_a_queue_proof_gets_slots_of_its_own(monkeypatch, tmp_path):
    """PC_QUEUE_DIR is set only in a queue proof or test: its slots are named from that folder, so a live PoE2 run holding
    the real 6 can't starve the proof (7 Nov 2026, the proof's p2pool scene). Real runs get one set of names per PoE2
    folder (12 Nov 2026), the same every time in one folder."""
    monkeypatch.delenv("PC_QUEUE_DIR", raising=False)
    real = slots.slot_name()
    assert real.startswith("Local\PoE2Builder.queue.") and real.endswith(".LuaJIT.%d") and real.count("\\") == 1
    assert slots.slot_name() == real
    monkeypatch.setenv("PC_QUEUE_DIR", str(tmp_path / "a"))
    proof = slots.slot_name()
    assert proof != real and proof.startswith("Local\PoE2Builder.proof.") and proof.count("\\") == 1
    assert slots.slot_name() == proof
    monkeypatch.setenv("PC_QUEUE_DIR", str(tmp_path / "b"))
    assert slots.slot_name() not in (proof, real)


def test_the_pool_probe_says_pool_full_on_its_line(tmp_path):
    """tools/pool_probe.py with every slot held prints a 'pool: {"error": "PoolFull", ...}' line, not a traceback."""
    import json
    from pathlib import Path

    env = {**os.environ, "PC_QUEUE_KIND": "test", "PC_QUEUE_DIR": str(tmp_path)}
    holder = HOLDER.replace("slots.SLOT_NAME = sys.argv[1]\n", "")
    holders = [subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True, env=env)
               for _ in range(slots.POOL_MAX)]
    try:
        assert all(p.stdout.readline().startswith("took") for p in holders)
        probe = Path(__file__).resolve().parents[1] / "tools" / "pool_probe.py"
        out = subprocess.run([sys.executable, str(probe), "6", "0"], capture_output=True, text=True, env=env, timeout=120)
        line = next(x for x in out.stdout.splitlines() if x.startswith("pool: "))
        info = json.loads(line[len("pool: "):])
        assert info["error"] == "PoolFull" and info["free_slots"] == 0 and "Traceback" not in out.stderr
    finally:
        for p in holders:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            p.kill()
