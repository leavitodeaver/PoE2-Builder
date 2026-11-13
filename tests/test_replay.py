"""builder/pob/replay.py: a recorded PoB answer is replayed only for the engine's exact history, under the same
engine files; anything else goes to the real PoB, and a stale answer fails loudly (PERF1). No PoB needed: a fake
engine whose answers depend on its whole history stands in for it."""

from __future__ import annotations

import json

import pytest

from builder.pob import replay
from builder.pob.engine import PobError
from builder.pob.lua import LuaError


class FakeLua:
    def __init__(self, owner):
        self.owner = owner

    def run(self, code, chunk_name, *args):
        if code == "error":
            raise LuaError("lua broke")
        self.owner.history.append(("lua", code))
        return [str(len(self.owner.history)).encode(), b"\xff\x00"]


class FakePob:
    """Answers depend on every call so far, like PoB's state."""

    boots = 0
    twist = 0  # changes every answer, as an edit the fingerprint missed would

    def __init__(self):
        type(self).boots += 1
        self.history, self.boot_seconds, self.closed = [], 1.5, False
        self.lua = FakeLua(self)

    def call(self, method, **args):
        if method == "broken":
            raise PobError("broken: no such thing")
        self.history.append((method, json.dumps(args)))
        return {"seen": len(self.history), "sum": sum(len(text) for _, text in self.history) + self.twist, "seconds": FakePob.boots}

    def close(self):
        self.closed = True


@pytest.fixture
def pob(tmp_path, monkeypatch):
    monkeypatch.setattr(FakePob, "boots", 0)
    monkeypatch.setattr(FakePob, "twist", 0)
    monkeypatch.chdir(tmp_path)  # a stand-in engine changes directory as the real one does
    return tmp_path


def engine(folder, **kwargs):
    store = replay.Store("unit", folder)
    return replay.ReplayEngine(store, boot=FakePob, **kwargs), store


def test_a_recorded_history_replays_without_booting_pob(pob):
    first, store = engine(pob)
    answers = [first.call("loadXml", xml="<a/>"), first.call("stats", fields=["Life"]), first.lua.run("return 1", "chunk")]
    with pytest.raises(PobError, match="no such thing"):
        first.call("broken")
    with pytest.raises(LuaError, match="lua broke"):
        first.lua.run("error", "chunk")
    after = first.call("stats", fields=["Life"])
    assert FakePob.boots == 1 and store.recorded == 6
    store.save()

    second, store = engine(pob)
    assert [second.call("loadXml", xml="<a/>"), second.call("stats", fields=["Life"]), second.lua.run("return 1", "chunk")] == answers
    with pytest.raises(PobError, match="no such thing"):
        second.call("broken")
    with pytest.raises(LuaError, match="lua broke"):
        second.lua.run("error", "chunk")
    assert second.call("stats", fields=["Life"]) == after
    assert FakePob.boots == 1 and store.hits == 6 and store.recorded == 0  # no engine booted for the second run


def test_a_different_argument_or_order_goes_to_the_real_pob(pob):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    first.call("stats", fields=["Life"])
    store.save()

    second, store = engine(pob)
    second.call("loadXml", xml="<a/>")
    assert FakePob.boots == 1
    changed = second.call("stats", fields=["Mana"])  # not recorded: the real engine boots and is caught up first
    assert FakePob.boots == 2 and changed == {"seen": 2, "sum": 15 + 20, "seconds": 2}
    assert store.recorded == 1 and second._engine.history[0] == ("loadXml", '{"xml": "<a/>"}')

    # the same call after a different history is a different answer: never served from the other history's
    third, store = engine(pob)
    assert third.call("stats", fields=["Life"])["seen"] == 1 and FakePob.boots == 3


def test_a_stale_answer_fails_loudly_when_the_real_pob_disagrees(pob, monkeypatch):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    store.save()

    monkeypatch.setattr(FakePob, "twist", 1000)  # PoB answers differently now, and the fingerprint didn't notice
    second, store = engine(pob)
    assert second.call("loadXml", xml="<a/>")["sum"] == 15  # replayed: nothing has gone to the real PoB yet
    with pytest.raises(replay.ReplayMismatch, match="rerun with POE2_REAL_POB=1"):
        second.call("stats", fields=["Life"])  # catching up finds the recorded loadXml answer was stale
    store.save()
    third, _ = engine(pob)
    assert third.call("loadXml", xml="<a/>")["sum"] == 1015  # and the recording holds the real answer now


def test_the_bridges_stopwatch_readings_are_not_a_mismatch(pob):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    store.save()
    second, _ = engine(pob)
    second.call("loadXml", xml="<a/>")
    assert second.call("stats", fields=[])["seconds"] == 2  # "seconds" differed on the catch-up (1 then 2): no error


def test_real_pob_mode_reads_no_recorded_answer(pob, monkeypatch):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    store.save()
    monkeypatch.setattr(FakePob, "twist", 1000)
    live, store = engine(pob, live_only=True)
    assert FakePob.boots == 2  # booted at once
    assert live.call("loadXml", xml="<a/>")["sum"] == 1015 and store.hits == 0
    monkeypatch.setenv("POE2_REAL_POB", "1")
    assert replay.real_pob()
    monkeypatch.setenv("POE2_REAL_POB", "0")
    assert replay.real_pob()  # replay is opt-in
    monkeypatch.setenv("POE2_REPLAY_POB", "1")
    assert not replay.real_pob()
    monkeypatch.setenv("POE2_REAL_POB", "1")
    assert replay.real_pob()  # and POE2_REAL_POB wins


def test_a_closed_engine_refuses_calls_and_closes_the_real_one(pob):
    made, _ = engine(pob)
    made.call("loadXml", xml="<a/>")
    real = made._engine
    made.close()
    assert real.closed
    with pytest.raises(PobError, match="closed"):
        made.call("stats", fields=[])


def test_saving_a_whole_run_drops_answers_nothing_asked_for(pob):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    first.call("stats", fields=["Life"])
    store.save()
    second, store = engine(pob)
    second.call("loadXml", xml="<a/>")
    store.save()  # a part run: everything kept
    assert len(replay.Store("unit", pob).entries) == 2
    store.save(only_used=True)
    assert len(replay.Store("unit", pob).entries) == 1


def test_the_fingerprint_follows_the_bridge_and_pobs_files(tmp_path, monkeypatch):
    engine_dir, pob_dir = tmp_path / "engine", tmp_path / "pob"
    (pob_dir / "src").mkdir(parents=True)
    (pob_dir / "runtime").mkdir()
    engine_dir.mkdir()
    (engine_dir / "bridge.lua").write_text("return 1")
    (engine_dir / "bootstrap.lua").write_text("return 2")
    (pob_dir / "src" / "Calcs.lua").write_text("x = 1")
    monkeypatch.setattr(replay, "ENGINE_DIR", engine_dir)
    start = replay.fingerprint(pob_dir)
    assert replay.fingerprint(pob_dir) == start
    (engine_dir / "bridge.lua").write_text("return 3")
    bridge = replay.fingerprint(pob_dir)
    (pob_dir / "src" / "Calcs.lua").write_text("x = 22")
    calcs = replay.fingerprint(pob_dir)
    (pob_dir / "runtime" / "new.dll").write_text("")
    assert len({start, bridge, calcs, replay.fingerprint(pob_dir)}) == 4


def test_another_fingerprints_recordings_are_deleted_on_save(pob, monkeypatch):
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    store.save()
    old = store.path
    monkeypatch.setattr(replay, "_fingerprint", "0" * 20)
    second, store = engine(pob)
    assert store.entries == {}  # the other fingerprint's answers are never read
    second.call("loadXml", xml="<a/>")
    assert FakePob.boots == 2
    store.save()
    assert store.path.exists() and not old.exists()


def test_a_forked_engine_starts_new_and_leaves_the_shared_history_alone(pob):
    shared, store = engine(pob)
    shared.call("loadXml", xml="<a/>")
    shared.fork()
    assert shared.call("stats", fields=["Life"])["seen"] == 1 and FakePob.boots == 2  # a new engine: nothing seen before
    forked = shared._engine
    shared.join()
    assert forked.closed
    assert shared.call("stats", fields=["Life"])["seen"] == 2 and FakePob.boots == 2  # the shared engine, where it was
    store.save()

    again, store = engine(pob)  # a second run: the shared history replays whatever the forked test did
    again.call("loadXml", xml="<a/>")
    again.fork()
    again.call("stats", fields=["Mana"])
    again.join()
    assert again.call("stats", fields=["Life"])["seen"] == 2 and store.hits == 2 and FakePob.boots == 3
    again.fork()
    again.close()  # closing inside a fork closes every real engine
    assert again._engine is None and not again._parked


def test_a_reordered_source_lines_answer_is_the_same_answer_and_nothing_else_is():
    """PoB walks its item slots in an order LuaJIT picks per process, so sourceLines' lines come back shuffled (PERF2):
    that alone is no mismatch; a changed line, a missing line or any other call's reordered list still is."""
    from builder.pob.replay import same_answer

    one = {"ok": {"lines": [{"source": "item:Helmet", "text": "a"}, {"source": "item:Boots", "text": "b"}]}}
    shuffled = {"ok": {"lines": list(reversed(one["ok"]["lines"]))}}
    assert same_answer("call:sourceLines", one, shuffled)
    assert not same_answer("call:groups", one, shuffled)
    changed = {"ok": {"lines": [{"source": "item:Helmet", "text": "a"}, {"source": "item:Boots", "text": "c"}]}}
    assert not same_answer("call:sourceLines", one, changed)
    assert not same_answer("call:sourceLines", one, {"ok": {"lines": one["ok"]["lines"][:1]}})
    assert not same_answer("call:sourceLines", one, {"ok": {"lines": shuffled["ok"]["lines"], "more": 1}})
    assert not same_answer("call:sourceLines", one, {"PobError": "x"})


def test_the_key_order_of_a_calls_arguments_is_not_part_of_its_history():
    from builder.pob.replay import _key_order_free

    assert _key_order_free('{"vars": {"b": 1, "a": [2, 1]}, "x": 0}') == _key_order_free('{"x": 0, "vars": {"a": [2, 1], "b": 1}}')
    assert _key_order_free('{"a": [1, 2]}') != _key_order_free('{"a": [2, 1]}')
