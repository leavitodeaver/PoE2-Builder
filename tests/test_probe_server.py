"""PERF22's probe server (tools/probe_server.py) and client (tools/probe.py): no PoB engine here, no queue booking."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import probe as client  # noqa: E402
import probe_server as ps  # noqa: E402

pytestmark = pytest.mark.spawns_processes  # a server and its probes


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A server on a spool of its own (outside the queue), taking probes for 1 min; never a real booking."""
    monkeypatch.setenv("POE2_PROBE_NO_BOOKING", "1")
    spool = tmp_path / "spool"
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "probe_server.py"), "serve", "--job", "UNIT",
                             "--spool", str(spool), "--accept-minutes", "1", "--stop-minutes", "3"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 30
    while not any(b.get("state") == "accepting" for b in ps.servers(spool)) and time.time() < end:
        time.sleep(0.2)
    yield spool
    (spool / "STOP").write_text("x")
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_a_probe_runs_fresh_with_the_clients_settings_folder_and_exit_code(server, tmp_path, monkeypatch):
    monkeypatch.setenv("POE2_UNIT_SETTING", "mine")
    script = "import os,sys; print(os.environ.get('POE2_UNIT_SETTING'), os.environ['POE2_SLOT_SET'], os.getcwd(), os.getpid()); sys.exit(3)"
    first = client.probe([sys.executable, "-c", script], job="POE2T0", minutes=1, cwd=tmp_path, spool=server)
    second = client.probe([sys.executable, "-c", script], job="POE2T0", minutes=1, cwd=tmp_path, spool=server)
    assert first.via == "server UNIT" and first.code == 3
    setting, slot_set, cwd, pid = first.output.split()
    assert setting == "mine" and slot_set.startswith("probe-server-UNIT") and Path(cwd) == tmp_path
    assert second.output.split()[3] != pid  # a fresh process for every probe


def test_the_servers_own_settings_never_reach_a_probe():
    env = ps.probe_env({"PATH": "p", "POE2_REPLAY_POB": "1", "PC_QUEUE_KIND": "test"}, {"POE2_REAL_POB": "1"}, "set")
    assert env == {"PATH": "p", "PC_QUEUE_KIND": "test", "POE2_REAL_POB": "1", "POE2_SLOT_SET": "set"}


def test_no_server_means_a_normal_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("POE2_PROBE_NO_BOOKING", "1")
    result = client.probe([sys.executable, "-c", "print(1)"], job="POE2T0", spool=tmp_path / "none")
    assert result.via.startswith("booking refused")


def test_a_server_about_to_stop_takes_no_probe_longer_than_its_time(tmp_path):
    spool = tmp_path / "s"
    (spool / "servers").mkdir(parents=True)
    ps._write_json(spool / "servers" / "X.json", {"job": "X", "pid": os.getpid(), "beat": time.time(), "started": time.time(),
                                                  "state": "accepting", "stop_at": time.time() + 5 * 60})
    assert client._server_can_take(spool, 3) == "X"
    assert client._server_can_take(spool, 12) is None
    ps._write_json(spool / "servers" / "X.json", {"job": "X", "pid": os.getpid(), "beat": time.time() - 60, "started": 0,
                                                  "state": "accepting", "stop_at": time.time() + 60 * 60})
    assert ps.servers(spool)[0]["state"] == "paused"  # a stale heartbeat from a live process


def test_a_probe_past_its_minutes_is_stopped(server, tmp_path):
    result = client.probe([sys.executable, "-c", "import time; print('start', flush=True); time.sleep(600)"], job="POE2T0",
                          minutes=0.05, cwd=tmp_path, spool=server)
    assert result.via == "server UNIT" and "own minutes ran out" in result.output and result.code != 0


def test_a_slot_set_names_slots_of_its_own(monkeypatch):
    from builder.pob import slots

    monkeypatch.delenv("POE2_SLOT_SET", raising=False)
    monkeypatch.delenv("PC_QUEUE_DIR", raising=False)
    folder = slots.slot_name()
    monkeypatch.setenv("POE2_SLOT_SET", "probe-server-A")
    a = slots.slot_name()
    monkeypatch.setenv("POE2_SLOT_SET", "probe-server-B")
    assert a != slots.slot_name() and folder not in (a, slots.slot_name()) and a.startswith("Local\\PoE2Builder.set.")
