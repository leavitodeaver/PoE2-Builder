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
import sys, time
from builder.pob import slots
slots.SLOT_NAME = sys.argv[1]
slots.take()
print("took", flush=True)
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
        assert all(p.stdout.readline().strip() == "took" for p in holders)
        assert slots.free() == 0
        with pytest.raises(slots.PoolFull):
            slots.take()
        holders[0].kill()
        holders[0].wait()
        assert slots.free() == 1  # Windows frees a killed process's slot
        slots.take()
        assert slots.free() == 0  # this process's own slot doesn't count as free
    finally:
        for p in holders:
            p.kill()


def test_worker_counts_inside_the_queue_come_from_its_processors(monkeypatch):
    monkeypatch.setenv("PC_QUEUE_KIND", "test")
    assert settings.workers() == settings.max_workers() == min(slots.POOL_MAX, slots.available_processors())
    monkeypatch.delenv("PC_QUEUE_KIND")
    assert settings.max_workers() == max(1, slots.available_processors() - 1)


def test_available_processors_is_the_affinity_mask():
    assert 1 <= slots.available_processors() <= (os.cpu_count() or 1)
