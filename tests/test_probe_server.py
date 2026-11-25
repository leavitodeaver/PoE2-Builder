"""PERF22's standing probe server (tools/probe_server.py) and client (tools/probe.py): no PoB engine, no queue booking.
The queue's holds are read through pc_queue.py's own test switch, PC_QUEUE_DIR: a folder of our own with a fake
MEASURING.lock / HOLD_poe2, never the user's files."""

from __future__ import annotations

import json
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

TICKER = "import sys, time\nfor i in range(int(sys.argv[1])):\n    print('tick', i, flush=True)\n    time.sleep(0.5)\nprint('done', flush=True)"


def _wait(condition, seconds=30.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def queue_dir(tmp_path):
    folder = tmp_path / "queue"
    folder.mkdir()
    return folder


@pytest.fixture
def server(tmp_path, queue_dir, monkeypatch):
    """A standing server on a spool of its own, reading holds from queue_dir; never a real booking."""
    monkeypatch.setenv("POE2_PROBE_NO_BOOKING", "1")
    spool = tmp_path / "spool"
    env = {**os.environ, "PC_QUEUE_DIR": str(queue_dir), "POE2_PROBE_HOLD_GRACE": "1"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "probe_server.py"), "serve", "--spool", str(spool),
                             "--minutes", "4", "--no-keeper"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
    assert _wait(lambda: any(b.get("state") == "accepting" for b in ps.servers(spool)))
    yield spool
    (spool / "STOP").write_text("x")
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_a_probe_runs_fresh_with_the_clients_settings_folder_and_exit_code(server, tmp_path, monkeypatch):
    monkeypatch.setenv("POE2_UNIT_SETTING", "mine")
    script = ("import os,sys; print(os.environ.get('POE2_UNIT_SETTING'), os.environ['POE2_SLOT_SET'], os.environ['PC_QUEUE_KIND'], "
              "os.environ.get('PC_QUEUE_DIR', '-'), os.getcwd(), os.getpid()); sys.exit(3)")
    first = client.probe([sys.executable, "-c", script], job="POE2T0", minutes=1, cwd=tmp_path, spool=server)
    second = client.probe([sys.executable, "-c", script], job="POE2T0", minutes=1, cwd=tmp_path, spool=server)
    assert first.via.startswith("server standing-") and first.code == 3
    setting, slot_set, kind, queue_dir, cwd, pid = first.output.split()
    assert setting == "mine" and slot_set.startswith("probe-server-") and kind == "test" and queue_dir == "-"
    assert Path(cwd) == tmp_path
    assert second.output.split()[5] != pid  # a fresh process for every probe
    assert "probe" in (server / "server.log").read_text(encoding="utf-8")


def test_the_servers_own_settings_never_reach_a_probe():
    env = ps.probe_env({"PATH": "p", "POE2_REPLAY_POB": "1", "PC_QUEUE_DIR": "x"}, {"POE2_REAL_POB": "1"}, "set")
    assert env == {"PATH": "p", "POE2_REAL_POB": "1", "PC_QUEUE_KIND": "test", "POE2_SLOT_SET": "set"}


def test_no_server_means_a_normal_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("POE2_PROBE_NO_BOOKING", "1")
    result = client.probe([sys.executable, "-c", "print(1)"], job="POE2T0", spool=tmp_path / "none")
    assert result.via.startswith("booking refused")


def test_only_an_accepting_server_with_room_takes_a_probe(tmp_path):
    spool = tmp_path / "s"
    (spool / "servers").mkdir(parents=True)

    def beat(**fields):
        ps._write_json(spool / "servers" / "X.json", {"job": "X", "pid": os.getpid(), "beat": time.time(), "started": 0,
                                                      "state": "accepting", "stop_at": None, "waiting": 0, **fields})
    beat()
    assert client._server_can_take(spool, 12) == "X"  # standing: no end
    beat(state="held: MEASURING.lock is held")
    assert client._server_can_take(spool, 12) is None
    beat(waiting=client.BUSY_WAITING)
    assert client._server_can_take(spool, 12) is None  # busy: a booking is likely sooner
    beat(stop_at=time.time() + 5 * 60)
    assert client._server_can_take(spool, 3) == "X" and client._server_can_take(spool, 12) is None
    beat(beat=time.time() - 60)
    assert ps.servers(spool)[0]["state"] == "stuck"


def test_a_probe_past_its_minutes_is_stopped(server, tmp_path):
    result = client.probe([sys.executable, "-c", "import time; print('start', flush=True); time.sleep(600)"], job="POE2T0",
                          minutes=0.05, cwd=tmp_path, spool=server)
    assert "own minutes ran out" in result.output and result.code != 0


def test_a_measure_holds_the_server_pauses_its_probes_and_sends_new_ones_to_a_booking(server, queue_dir, tmp_path):
    import threading

    box = {}
    worker = threading.Thread(target=lambda: box.update(r=client.probe([sys.executable, "-c", TICKER, "16"], job="POE2T0",
                                                                         minutes=2, cwd=tmp_path, spool=server)))
    worker.start()
    logs = lambda: [p for p in (server / "out").glob("*.log")]  # noqa: E731
    assert _wait(lambda: logs() and "tick 1" in logs()[0].read_text())
    (queue_dir / "MEASURING.lock").write_text("a frame measure")  # pc_queue.MEASURING under PC_QUEUE_DIR
    assert _wait(lambda: any(b.get("state", "").startswith("held") for b in ps.servers(server)), 15)
    time.sleep(1.0)
    before = logs()[0].read_text()
    time.sleep(2.5)
    assert logs()[0].read_text() == before  # suspended: not one tick while held
    held_new = client.probe([sys.executable, "-c", "print(1)"], job="POE2T0", cwd=tmp_path, spool=server)
    assert held_new.via.startswith("booking refused")  # a new probe goes to a normal booking
    (queue_dir / "MEASURING.lock").unlink()
    worker.join(60)
    assert box["r"].code == 0 and "done" in box["r"].output and box["r"].via.startswith("server")
    text = (server / "server.log").read_text(encoding="utf-8")
    assert "HELD (MEASURING.lock is held)" in text and "hold over" in text


def test_a_probe_held_past_its_minutes_goes_to_a_booking(server, queue_dir, tmp_path):
    """Held while running for longer than its own minutes: stopped, and its client books it normally."""
    import threading

    box = {}
    worker = threading.Thread(target=lambda: box.update(r=client.probe([sys.executable, "-c", TICKER, "40"], job="POE2T0",
                                                                         minutes=0.1, cwd=tmp_path, spool=server)))
    worker.start()
    assert _wait(lambda: any("tick 1" in p.read_text() for p in (server / "out").glob("*.log")))
    (queue_dir / "HOLD_poe2").write_text("unit")  # the queue's own hold file, under PC_QUEUE_DIR
    worker.join(60)
    assert box["r"].via.startswith("booking refused")
    assert "paused past the probe's own minutes" in (server / "server.log").read_text(encoding="utf-8")


def test_the_server_confines_itself_below_normal_off_the_users_processors():
    code = ("import sys, ctypes; sys.path.insert(0, sys.argv[1]); import probe_server as ps; done = ps.confine_self(); "
            "k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
            "a, b = ctypes.c_size_t(), ctypes.c_size_t(); "
            "k.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]; "
            "k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(a), ctypes.byref(b)); "
            "k.GetPriorityClass.argtypes = [ctypes.c_void_p]; "
            "print(json.dumps({'done': done, 'priority': k.GetPriorityClass(k.GetCurrentProcess()), 'affinity': a.value, 'system': b.value}))")
    out = subprocess.run([sys.executable, "-c", "import json; " + code, str(ROOT / "tools")], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
    info = json.loads(out.stdout.strip().splitlines()[-1])
    assert info["priority"] == ps.BELOW_NORMAL
    assert info["affinity"] & 0x3 == 0 or info["system"] & ~0x3 == 0  # never on processors 0-1 (unless the PC has only those)
    assert info["done"]["job"], info  # its own job: 4 GB, 24 processes, the CPU cap, kill-on-close


def test_the_keeper_starts_the_server_again_when_it_dies(tmp_path, queue_dir):
    spool = tmp_path / "spool"
    env = {**os.environ, "PC_QUEUE_DIR": str(queue_dir), "POE2_PROBE_NO_BREAKAWAY": "1"}
    subprocess.run([sys.executable, str(ROOT / "tools" / "probe_server.py"), "start", "--spool", str(spool)], env=env,
                   capture_output=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
    try:
        assert _wait(lambda: any(b.get("state") == "accepting" for b in ps.servers(spool)), 60)
        first = next(b for b in ps.servers(spool) if b.get("state") == "accepting")
        ps._kill_tree(int(first["pid"]))
        assert _wait(lambda: any(b.get("state") == "accepting" and b["pid"] != first["pid"] for b in ps.servers(spool)), 90)
    finally:
        subprocess.run([sys.executable, str(ROOT / "tools" / "probe_server.py"), "stop", "--spool", str(spool)], env=env,
                       capture_output=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
        assert _wait(lambda: not any(b.get("state") not in ("gone",) for b in ps.servers(spool)), 60)
        keeper = ps._read_json(spool / "keeper.json")
        assert _wait(lambda: not keeper or not ps._alive(int(keeper["pid"])), 30)


def test_a_slot_set_names_slots_of_its_own(monkeypatch):
    from builder.pob import slots

    monkeypatch.delenv("POE2_SLOT_SET", raising=False)
    monkeypatch.delenv("PC_QUEUE_DIR", raising=False)
    folder = slots.slot_name()
    monkeypatch.setenv("POE2_SLOT_SET", "probe-server-A")
    a = slots.slot_name()
    monkeypatch.setenv("POE2_SLOT_SET", "probe-server-B")
    assert a != slots.slot_name() and folder not in (a, slots.slot_name()) and a.startswith("Local\\PoE2Builder.set.")


def test_no_start_path_can_open_a_window(monkeypatch, tmp_path):
    """Every process the server, its keeper and the client start is CREATE_NO_WINDOW and never DETACHED_PROCESS (0x8):
    beside DETACHED, Windows ignores CREATE_NO_WINDOW and a venv's python.exe launcher opens a VISIBLE console (the
    user saw them, 25 Nov ~02:40)."""
    if os.name != "nt":
        pytest.skip("Windows creation flags")
    seen = []

    class Fake:
        pid, returncode = 4242, 0

        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("creationflags", 0))

        def poll(self):
            return 0

        def wait(self, *a):
            return 0

    def fake_run(*args, **kwargs):
        seen.append(kwargs.get("creationflags", 0))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "Popen", Fake)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: seen.append(k.get("creationflags", 0)) or 0)
    ps._detached([sys.executable, "-c", "pass"], tmp_path / "a.log")  # keeper / serve / start / restarts
    ps._kill_tree(1)  # taskkill
    server = ps.Server("unit", tmp_path / "spool")
    server._start({"id": "r1", "argv": [sys.executable, "-c", "pass"], "cwd": str(tmp_path), "minutes": 1})  # a probe
    monkeypatch.delenv("POE2_PROBE_NO_BOOKING", raising=False)
    client._booking([sys.executable, "-c", "pass"], "POE2T0", 1, "unit", False, ROOT)  # the client's fallback booking
    import importlib.util
    spec = importlib.util.spec_from_file_location("suite_tool_flags", ROOT / "tools" / "suite.py")
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    assert suite.NO_WINDOW == 0x08000000
    assert len(seen) >= 4
    for flags in seen:
        assert flags & 0x08000000 and not flags & 0x00000008, hex(flags)
    source = "".join((ROOT / "tools" / name).read_text(encoding="utf-8") for name in ("probe_server.py", "probe.py", "suite.py"))
    assert "DETACHED_PROCESS" not in source.replace("NEVER DETACHED_PROCESS", "").replace("never DETACHED_PROCESS", "")


# ---- small targeted test runs (the user, 25 Nov ~01:18) ----

def test_only_small_targeted_test_runs_qualify():
    q = lambda argv, minutes=5, label="MECH-A: MECH107 targeted": ps.qualifies(argv, minutes, label, str(ROOT))  # noqa: E731
    pytest_cmd = ["uv", "run", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    assert q(["uv", "run", "python", "scratch/x/probe.py"]) is None  # a probe
    # (the manager, 25 Nov 08:20) a PROBE qualifies whatever its label says: the label check is for test runs only
    for label in ("COV-A: COV57 calibrate b7", "MECH-B: probe after TRAIN 146", "MECH-A: investigate the boss", "SEARCH-B: 2b75b gate build"):
        assert q(["uv", "run", "python", "scratch/x/probe.py"], label=label) is None, label
    assert "calibration" in q(pytest_cmd + ["tests/test_slots.py"], label="COV-A: calibrate b7 suite")
    assert q(pytest_cmd + ["tests/test_slots.py", "tests/test_pool.py::test_x"]) is None
    assert "10 min or less" in q(pytest_cmd + ["tests/test_slots.py"], minutes=15)
    assert "folder" in q(pytest_cmd + ["tests"])
    assert "no test file" in q(pytest_cmd)
    assert "part 1" in q(pytest_cmd + ["tests/test_engine_models.py"])
    assert "parallel" in q(pytest_cmd + ["-n", "2", "tests/test_slots.py"])
    assert "queue" in q(pytest_cmd + ["tests/test_slots.py"], label="SEARCH: gate4 2b75c")
    assert "train" in q(pytest_cmd + ["tests/test_slots.py"], label="manager: TRAIN 145 part 2")
    assert "serial suite" in q(pytest_cmd + ["tests/test_legality.py"])  # 15.6 min in the serial baseline


def test_a_test_run_through_the_server_gives_its_exit_code_and_junit(server, tmp_path):
    folder = tmp_path / "lane"
    (folder / "tests").mkdir(parents=True)
    (folder / "tests" / "test_unit.py").write_text("def test_ok():\n    assert True\n\ndef test_bad():\n    assert False\n")
    result = client.probe([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_unit.py"], job="POE2T0",
                          minutes=2, what="PERF: unit targeted", cwd=folder, spool=server)
    assert result.via.startswith("server") and result.code == 1 and "1 failed, 1 passed" in result.output
    assert result.junit and Path(result.junit).exists() and "test_bad" in Path(result.junit).read_text(encoding="utf-8")


def test_a_run_past_the_memory_limit_is_stopped_and_booked(tmp_path, queue_dir, monkeypatch):
    monkeypatch.setenv("POE2_PROBE_NO_BOOKING", "1")
    spool = tmp_path / "spool-mem"
    env = {**os.environ, "PC_QUEUE_DIR": str(queue_dir), "POE2_PROBE_MEMORY_STOP_MB": "150"}
    proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "probe_server.py"), "serve", "--spool", str(spool),
                             "--minutes", "6", "--no-keeper"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, creationflags=ps.NO_WINDOW)
    try:
        assert _wait(lambda: any(b.get("state") == "accepting" for b in ps.servers(spool)))
        hog = "import time\nblock = bytearray(400 * 1024 * 1024)\nprint('allocated', flush=True)\ntime.sleep(60)"
        result = client.probe([sys.executable, "-c", hog], job="POE2T0", minutes=2, cwd=tmp_path, spool=spool)
        assert result.via.startswith("booking refused")
        assert "too big for the server" in (spool / "server.log").read_text(encoding="utf-8")
    finally:
        (spool / "STOP").write_text("x")
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
