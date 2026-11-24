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
    out_of_memory = 0  # (GEM38) this many "short" calls run out of memory first

    def __init__(self):
        type(self).boots += 1
        self.history, self.boot_seconds, self.closed = [], 1.5, False
        self.lua = FakeLua(self)

    def call(self, method, **args):
        if method == "broken":
            raise PobError("broken: no such thing")
        if method == "short" and FakePob.out_of_memory > 0:
            FakePob.out_of_memory -= 1
            raise PobError("short: not enough memory")
        if method == "python_short":
            raise MemoryError
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


def test_a_memory_error_is_never_kept_and_the_next_run_asks_again(pob, monkeypatch):
    """(GEM38) POE2T7294's "not enough memory" was stored and replayed as PoB's answer on POE2T7295."""
    monkeypatch.setattr(FakePob, "out_of_memory", 1)
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    with pytest.raises(PobError, match="not enough memory"):
        first.call("short")
    assert not any(replay.memory_error(answer) for answer in store.entries.values())
    store.save()

    second, store = engine(pob)
    second.call("loadXml", xml="<a/>")
    assert second.call("short")["seen"] == 2 and FakePob.boots == 2 and store.recorded == 1  # (the real PoB answered)
    store.save()
    third, store = engine(pob)
    third.call("loadXml", xml="<a/>")
    assert third.call("short")["seen"] == 2 and FakePob.boots == 2  # (and it replays now)


@pytest.mark.parametrize("method, error", [("short", PobError), ("python_short", MemoryError)])
def test_nothing_is_recorded_after_a_memory_error_on_that_engine(pob, monkeypatch, method, error):
    """(GEM38) The failed call may have left PoB part-way, so what it says next can't be saved as the true answer."""
    monkeypatch.setattr(FakePob, "out_of_memory", 1)
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    with pytest.raises(error):
        first.call(method)
    held = len(store.entries)
    first.call("stats", fields=["Life"])  # (still answered, by the real engine)
    assert len(store.entries) == held


def test_a_stored_memory_error_is_asked_again_and_overwritten(pob):
    """(GEM38) A recording made before the fix heals by itself, with no one editing it."""
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    real = first.call("stats", fields=["Life"])
    chain = first._chain
    store.entries[chain] = json.dumps({"PobError": "stats: not enough memory"})
    store.dirty = True
    store.save()

    second, store = engine(pob)
    second.call("loadXml", xml="<a/>")
    assert second.call("stats", fields=["Life"])["sum"] == real["sum"] and FakePob.boots == 2
    assert not replay.memory_error(store.entries[chain])
    store.save()
    third, store = engine(pob)
    third.call("loadXml", xml="<a/>")
    assert third.call("stats", fields=["Life"])["sum"] == real["sum"] and FakePob.boots == 2


def test_only_a_memory_error_counts_as_one():
    assert replay.memory_error(json.dumps({"LuaError": "x: not enough memory"}))
    assert not replay.memory_error(json.dumps({"PobError": "broken: no such thing"}))
    assert not replay.memory_error(json.dumps({"LuaError": "lua broke"}))
    assert not replay.memory_error(json.dumps({"ok": {"text": "not enough memory"}}))  # (an answer holding the words)
    assert not replay.memory_error(None)


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
    monkeypatch.delenv("POE2_REPLAY_POB", raising=False)  # (set when the suite itself runs replayed: POE2T2623)
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


def test_a_live_fork_boots_the_real_pob_at_once_and_never_replays(pob):
    first, store = engine(pob)
    first.fork(live=True)
    first.call("stats", fields=["Life"])
    first.join()
    store.save()

    shared, store = engine(pob)  # a second run, in replay: the same call is recorded, and still goes to the real PoB
    shared.fork(live=True)
    assert FakePob.boots == 2 and shared._engine is not None  # booted by fork(), before any call
    assert shared.call("stats", fields=["Life"])["seen"] == 1 and store.hits == 0 and FakePob.boots == 2
    shared.join()
    assert not shared._live_only  # back to replaying where the recording holds the history
    shared.fork()
    assert shared.call("stats", fields=["Life"])["seen"] == 1 and store.hits == 1 and FakePob.boots == 2
    shared.join()


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


def test_the_audits_lists_in_another_order_are_the_same_answer_and_nothing_else_is():
    """bridge.auditCoverage fills its lists in pairs() order (PERF21): their order alone, at any depth, is no mismatch."""
    from builder.pob.replay import same_answer

    one = {"ok": {"affixes": [{"line": "a", "supported": True}, {"line": "b", "supported": False}],
                  "skills": [{"id": "x", "sets": [{"baseFlags": ["hit", "area"]}]}, {"id": "y", "sets": []}]}}
    moved = {"ok": {"affixes": list(reversed(one["ok"]["affixes"])),
                    "skills": [{"id": "y", "sets": []}, {"id": "x", "sets": [{"baseFlags": ["area", "hit"]}]}]}}
    assert same_answer("call:auditCoverage", one, moved)
    assert not same_answer("call:groups", one, moved)
    changed = {"ok": {"affixes": [{"line": "a", "supported": True}, {"line": "b", "supported": True}], "skills": one["ok"]["skills"]}}
    assert not same_answer("call:auditCoverage", one, changed)
    assert not same_answer("call:auditCoverage", one, {"ok": {"affixes": one["ok"]["affixes"][:1], "skills": one["ok"]["skills"]}})
    assert not same_answer("call:auditCoverage", one, {"PobError": "x"})


def test_an_export_with_an_items_bases_in_another_order_is_the_same_answer_and_nothing_else_is():
    """PoB writes an item's alternative bases in a per-process order (Item.lua 2034 / 2064); only that may differ."""
    from builder.pob.replay import difference, same_answer

    def export(*lines):
        return {"ok": {"xml": "<PathOfBuilding2>\n\t<Item id='1'>\n" + "\n".join(lines) + "\n\t</Item>\n</PathOfBuilding2>"}}
    one = export("Rarity: UNIQUE", "{base:2}Runeforged Hunting Shoes", "{base:3}Runemastered Hunting Shoes", "{base:1}Hunting Shoes",
                 "{variant:1}+10 to Dexterity", "{variant:2}+20 to Dexterity")
    moved = export("Rarity: UNIQUE", "{base:1}Hunting Shoes", "{base:3}Runemastered Hunting Shoes", "{base:2}Runeforged Hunting Shoes",
                   "{variant:1}+10 to Dexterity", "{variant:2}+20 to Dexterity")
    assert same_answer("call:exportXml", one, moved)
    assert "in another order" in difference(one, moved) and "Hunting Shoes" in difference(one, moved)
    assert not same_answer("call:loadXml", one, moved)  # another call's text is compared as it is
    mods_moved = export("Rarity: UNIQUE", "{base:2}Runeforged Hunting Shoes", "{base:3}Runemastered Hunting Shoes", "{base:1}Hunting Shoes",
                        "{variant:2}+20 to Dexterity", "{variant:1}+10 to Dexterity")
    assert not same_answer("call:exportXml", one, mods_moved)  # a modifier line moved: not PoB's base order
    changed = export("Rarity: UNIQUE", "{base:2}Runeforged Hunting Shoes", "{base:3}Runemastered Hunting Shoes", "{base:1}Jade Tiara",
                     "{variant:1}+10 to Dexterity", "{variant:2}+20 to Dexterity")
    assert not same_answer("call:exportXml", one, changed)
    assert "Jade Tiara" in difference(one, changed)
    apart = export("{base:2}Runeforged Hunting Shoes", "Rarity: UNIQUE", "{base:3}Runemastered Hunting Shoes", "{base:1}Hunting Shoes",
                   "{variant:1}+10 to Dexterity", "{variant:2}+20 to Dexterity")
    assert not same_answer("call:exportXml", one, apart)  # a base line moved out of its run
    assert not same_answer("call:exportXml", one, {"ok": {"xml": moved["ok"]["xml"], "more": 1}})
    sets = export("<StatSetIndex grantedEffect='B' index='5'/>", "<StatSetIndex grantedEffect='A' index='1'/>",
                  "<StatSetCalcsIndex grantedEffect='B' index='5'/>", "<StatSetCalcsIndex grantedEffect='A' index='1'/>")
    sets_moved = export("<StatSetIndex grantedEffect='A' index='1'/>", "<StatSetIndex grantedEffect='B' index='5'/>",
                        "<StatSetCalcsIndex grantedEffect='A' index='1'/>", "<StatSetCalcsIndex grantedEffect='B' index='5'/>")
    assert same_answer("call:exportXml", sets, sets_moved)  # SkillsTab.lua 527-536: a gem's stat sets, walked by effect id
    mixed = export("<StatSetIndex grantedEffect='A' index='1'/>", "<StatSetCalcsIndex grantedEffect='A' index='1'/>",
                   "<StatSetIndex grantedEffect='B' index='5'/>", "<StatSetCalcsIndex grantedEffect='B' index='5'/>")
    assert not same_answer("call:exportXml", sets, mixed)  # the two kinds are two loops: one never lands inside the other
    assert not same_answer("call:exportXml", sets, export(*[line.replace("'5'", "'4'") for line in sets["ok"]["xml"].split("\n")[2:6]]))
    assert not same_answer("call:exportXml", one, {"PobError": "x"})


def test_the_key_order_of_a_calls_arguments_is_not_part_of_its_history():
    from builder.pob.replay import _key_order_free

    assert _key_order_free('{"vars": {"b": 1, "a": [2, 1]}, "x": 0}') == _key_order_free('{"x": 0, "vars": {"a": [2, 1], "b": 1}}')
    assert _key_order_free('{"a": [1, 2]}') != _key_order_free('{"a": [2, 1]}')


def test_the_collector_pause_is_200_unless_set_and_refuses_a_bad_value(monkeypatch):
    from builder.pob import engine

    monkeypatch.delenv(engine.GC_PAUSE_VARIABLE, raising=False)
    assert engine.gc_pause() == 200  # unset: the default (the user's decision, 13 Nov 2026)
    monkeypatch.setenv(engine.GC_PAUSE_VARIABLE, " ")
    assert engine.gc_pause() == 200
    monkeypatch.setenv(engine.GC_PAUSE_VARIABLE, "400")
    assert engine.gc_pause() == 400  # the way back to PoB's own
    monkeypatch.setenv(engine.GC_PAUSE_VARIABLE, "150")
    assert engine.gc_pause() == 150
    for bad in ("fast", "99", "401", "1.5"):
        monkeypatch.setenv(engine.GC_PAUSE_VARIABLE, bad)
        with pytest.raises(PobError):
            engine.gc_pause()


def test_recordings_under_one_collector_pause_are_not_replayed_under_another(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "runtime").mkdir()
    monkeypatch.delenv("POE2_LUA_GC_PAUSE", raising=False)
    unset = replay.fingerprint(tmp_path)
    monkeypatch.setenv("POE2_LUA_GC_PAUSE", "200")
    assert replay.fingerprint(tmp_path) == unset  # unset means 200: the same recordings
    monkeypatch.setenv("POE2_LUA_GC_PAUSE", "400")
    at_400 = replay.fingerprint(tmp_path)
    monkeypatch.setenv("POE2_LUA_GC_PAUSE", "150")
    assert len({unset, at_400, replay.fingerprint(tmp_path)}) == 3



def test_a_saved_recording_reads_back_the_same_answers_including_an_empty_one(pob):
    """save() writes answer by answer (PERF6: no second copy of a module's answers in memory); the file is the same JSON."""
    first, store = engine(pob, live_only=True)
    answers = [first.call("loadXml", xml="<Build é/>" * 50), first.call("outputAll"), first.lua.run("return 1", "chunk")]
    store.save()
    again, loaded = engine(pob)
    assert loaded.entries == store.entries and len(loaded.entries) == 3
    assert [again.call("loadXml", xml="<Build é/>" * 50), again.call("outputAll"), again.lua.run("return 1", "chunk")] == answers
    assert FakePob.boots == 1  # the second engine never booted PoB
    empty = replay.Store("nothing", pob)
    empty.dirty = True
    empty.save()
    assert replay.Store("nothing", pob).entries == {}


def test_a_calls_arguments_are_let_go_once_the_real_pob_has_run_it(pob):
    """A module's shared engine makes tens of thousands of calls, many carrying a whole build: only the calls the real
    PoB hasn't run yet are kept (PERF6)."""
    live, store = engine(pob, live_only=True)
    for index in range(5):
        live.call("loadXml", xml="x" * 1000 + str(index))
    assert all(entry is None for entry in live._log)
    store.save()
    replayed, _ = engine(pob)
    for index in range(5):
        replayed.call("loadXml", xml="x" * 1000 + str(index))
    assert FakePob.boots == 1 and all(entry is not None for entry in replayed._log)  # no real PoB yet: it may have to catch up
    replayed.call("outputAll")  # new: the real PoB boots, runs all six, checks the five recorded answers
    assert FakePob.boots == 2 and all(entry is None for entry in replayed._log)


def test_a_reordered_unsupported_in_build_answer_is_the_same_answer():
    """unsupportedInBuild walks the allocated passives and the item slots with pairs() too (PERF6 / PERF7: the same
    lines in another order in another process, never another line): order alone is no mismatch, a changed line is."""
    from builder.pob.replay import same_answer

    one = {"ok": {"lines": [{"source": "item:Helmet", "text": "a"}, {"source": "node:5", "text": "b"}]}}
    shuffled = {"ok": {"lines": list(reversed(one["ok"]["lines"]))}}
    assert same_answer("call:unsupportedInBuild", one, shuffled)
    assert not same_answer("call:unsupportedInBuild", one, {"ok": {"lines": one["ok"]["lines"][:1]}})
    changed = {"ok": {"lines": [{"source": "item:Helmet", "text": "a"}, {"source": "node:5", "text": "c"}]}}
    assert not same_answer("call:unsupportedInBuild", one, changed)


def test_a_real_pob_store_never_opens_its_recording_and_writes_only_a_whole_runs_answers(pob, monkeypatch):
    """PERF25: a POE2_REAL_POB=1 run read the whole recording though it replays nothing (a 21 MB file held 0.99 GB
    and peaked at 2.94 GB, and Lua ran out of memory in a 4 GB place: POE2T3697, POE2T4283); and it re-recorded
    on top of the old answers. Now it opens nothing, and writes a fresh file of the calls it made."""
    first, store = engine(pob)
    first.call("loadXml", xml="<a/>")
    first.call("stats", fields=["Life"])
    store.save()
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: (opened.append(str(path)), real_open(path, *a, **k))[1])
    real = replay.Store("unit", pob, load=False)
    assert real.entries == {} and not any(path.endswith(".answers.zst") for path in opened)
    monkeypatch.setattr("builtins.open", real_open)
    again = replay.ReplayEngine(real, boot=FakePob, live_only=True)
    again.call("loadXml", xml="<b/>")
    real.save()  # a part run: it read nothing to add to, so the file stays as it was
    assert sorted(replay.Store("unit", pob).entries) == sorted(store.entries)
    real.save(only_used=True)  # every test of the module ran: the file is this run's answers alone
    assert list(replay.Store("unit", pob).entries.values()) == list(real.entries.values()) and len(real.entries) == 1
    fresh = replay.Store("other", pob, load=False)  # no file yet: even a part run's answers are worth writing
    replay.ReplayEngine(fresh, boot=FakePob, live_only=True).call("loadXml", xml="<a/>")
    fresh.save()
    assert len(replay.Store("other", pob).entries) == 1


def test_each_distinct_answer_is_kept_and_written_once(pob):
    """test_engine_models' recording: 476,282 histories, 10,029 distinct answers (963 MB of text, 51 MB distinct)."""
    store = replay.Store("unit", pob)
    for index in range(50):
        store.put(f"chain{index}", json.dumps({"ok": {"results": [{"Life": index % 3}] * 200}}))
    assert len({id(answer) for answer in store.entries.values()}) == 3
    store.save()
    with open(store.path, "rb") as raw:
        import zstandard
        lines = zstandard.ZstdDecompressor().stream_reader(raw).read().decode("ascii").split("\n")
    assert lines[-1] == "" and len(lines) == 51 and sum(line.split("\t")[1].startswith('"') for line in lines[:-1]) == 3
    loaded = replay.Store("unit", pob)
    assert loaded.entries == store.entries and len({id(answer) for answer in loaded.entries.values()}) == 3
    loaded.put("chain0", store.entries["chain1"])  # a changed answer is written as itself
    loaded.save()
    assert replay.Store("unit", pob).entries["chain0"] == store.entries["chain1"]


def test_a_cut_off_recording_reads_as_none(pob):
    store = replay.Store("unit", pob)
    store.put("a", json.dumps({"ok": 1}))
    store.save()
    import zstandard
    store.path.write_bytes(zstandard.ZstdCompressor().compress(b'a\t"{\\"ok\\": 1}"\nb\t'))
    assert replay.Store("unit", pob).entries == {}
    store.path.write_bytes(store.path.read_bytes()[:-3])
    assert replay.Store("unit", pob).entries == {}
