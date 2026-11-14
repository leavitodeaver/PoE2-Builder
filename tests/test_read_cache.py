"""The engine's read cache (builder/pob/engine.py, PERF13): a repeated read is answered from PoB's last answer only
while nothing else has been called. No PoB needed: a stand-in Lua state counts the questions it gets."""

import json

import pytest

from builder.pob import engine as engine_module
from builder.pob.engine import CACHED_READS, PASSING_READS, PobError
from builder.pob.lua import LuaState


# The real class: tests/conftest.py puts its replay stand-in under builder.pob.engine.PobEngine
REAL_ENGINE = next(c for c in object.__subclasses__() if c.__module__ == "builder.pob.engine" and c.__name__ == "PobEngine")


class FakeLua:
    """Answers bridge_dispatch with {"n": <how many questions so far>, "method": ...}: a real second answer would
    differ from the first, so a cached one shows."""

    def __init__(self) -> None:
        self.calls = 0
        self.asked: list[tuple[str, str]] = []
        self.same = False  # True: the same answer every time (PoB's behaviour for a read with nothing changed)

    def call(self, function: str, method: str, text: str) -> list[bytes]:
        self.calls += 1
        self.asked.append((method, text))
        n = 0 if self.same else len(self.asked)
        return [b"true", json.dumps({"n": n, "method": method, "list": [1, 2]}).encode()]

    def run(self, code: str, name: str) -> list[bytes]:
        self.calls += 1
        return []


def make(monkeypatch, mode: str | None = None):
    if mode is None:
        monkeypatch.delenv("POE2_READ_CACHE", raising=False)
    else:
        monkeypatch.setenv("POE2_READ_CACHE", mode)
    # the real class, without booting PoB
    monkeypatch.setattr(LuaState, "__init__", lambda self, runtime_dir: FakeLua.__init__(self))
    monkeypatch.setattr(LuaState, "call", FakeLua.call)
    monkeypatch.setattr(LuaState, "run", FakeLua.run)
    monkeypatch.setattr(REAL_ENGINE, "_run_file", lambda self, path, *args: None)
    monkeypatch.setattr(engine_module.slots, "take", lambda: None)
    monkeypatch.setattr(engine_module.os, "chdir", lambda path: None)
    engine = REAL_ENGINE()
    engine.lua.calls, engine.lua.asked = 0, []  # (the boot's own gc_pause chunk doesn't count below)
    return engine


def test_a_repeated_read_is_asked_once_and_each_caller_gets_its_own_copy(monkeypatch):
    engine = make(monkeypatch)
    first = engine.call("sourceLines")
    second = engine.call("sourceLines")
    assert first == second == {"n": 1, "method": "sourceLines", "list": [1, 2]}
    assert first is not second and first["list"] is not second["list"]
    first["list"].append(3)  # a caller changing its copy never reaches the next caller
    assert engine.call("sourceLines")["list"] == [1, 2]
    assert len(engine.lua.asked) == 1 and engine.read_hits == 2


def test_the_arguments_are_part_of_the_question(monkeypatch):
    engine = make(monkeypatch)
    life = engine.call("stats", fields=["Life"])
    mana = engine.call("stats", fields=["Mana"])
    assert life["n"] == 1 and mana["n"] == 2
    assert engine.call("stats", fields=["Life"]) == life and engine.call("stats", fields=["Mana"]) == mana
    assert len(engine.lua.asked) == 2


@pytest.mark.parametrize("other", ["setConfig", "equip", "nodeDeltas", "itemDeltas", "gemDeltas", "loadXml", "setGroup",
                                   "exportXml", "setRates", "minionSkillHit", "chargeGain", "anythingNotListed"])
def test_any_other_call_drops_the_answers(monkeypatch, other):
    assert other not in CACHED_READS and other not in PASSING_READS
    engine = make(monkeypatch)
    before = engine.call("groups")
    engine.call(other)
    after = engine.call("groups")
    assert before["n"] == 1 and after["n"] == 3 and engine.read_hits == 0


@pytest.mark.parametrize("passing", sorted(PASSING_READS))
def test_a_passing_read_keeps_the_answers_and_is_itself_asked_every_time(monkeypatch, passing):
    assert passing not in CACHED_READS
    engine = make(monkeypatch)
    before = engine.call("groups")
    first = engine.call(passing)
    second = engine.call(passing)
    after = engine.call("groups")
    assert before == after and before["n"] == 1 and engine.read_hits == 1
    assert (first["n"], second["n"]) == (2, 3)  # its own answer is never kept
    engine.call("equip")
    assert engine.call("groups")["n"] == 5  # a commit still drops them


def test_lua_run_between_passing_reads_drops_the_answers(monkeypatch):
    engine = make(monkeypatch)
    assert engine.call("groups")["n"] == 1
    engine.call("activeSkills")
    engine.lua.run("build.mainSocketGroup = 2", "poke")
    engine.call("activeSkills")
    assert engine.call("groups")["n"] == 4 and engine.read_hits == 0


def test_a_failed_passing_read_drops_the_answers(monkeypatch):
    engine = make(monkeypatch)
    assert engine.call("groups")["n"] == 1
    real = engine.lua.call

    def failing(*a):
        engine.lua.calls += 1
        return [b"false", b"boom"]
    engine.lua.call = failing
    with pytest.raises(PobError, match="boom"):
        engine.call("useRates")
    engine.lua.call = real
    assert engine.call("groups")["n"] == 2 and engine.read_hits == 0


def test_lua_run_directly_drops_the_answers(monkeypatch):
    engine = make(monkeypatch)
    assert engine.call("groups")["n"] == 1
    engine.lua.run("build.spec.nodes[1].alloc = true", "poke")  # (tests reach into Lua like this)
    assert engine.call("groups")["n"] == 2 and engine.read_hits == 0


def test_off_asks_every_time(monkeypatch):
    engine = make(monkeypatch, "0")
    assert [engine.call("sourceLines")["n"] for _ in range(3)] == [1, 2, 3]
    assert engine.read_hits == 0


def test_check_asks_again_and_raises_on_a_difference(monkeypatch):
    engine = make(monkeypatch, "check")
    engine.lua.same = True
    assert engine.call("groups") == engine.call("groups")
    assert len(engine.lua.asked) == 2 and engine.read_hits == 1  # answered from the cache AND asked again
    engine.lua.same = False
    with pytest.raises(PobError, match="read cache"):
        engine.call("groups")


def test_a_failed_read_is_not_kept(monkeypatch):
    engine = make(monkeypatch)
    real = engine.lua.call
    engine.lua.call = lambda *a: [b"false", b"boom"]
    with pytest.raises(PobError, match="boom"):
        engine.call("groups")
    engine.lua.call = real
    assert engine.call("groups")["method"] == "groups"


def test_a_wrong_switch_value_is_an_error(monkeypatch):
    monkeypatch.setenv("POE2_READ_CACHE", "maybe")
    with pytest.raises(PobError, match="POE2_READ_CACHE"):
        engine_module.read_cache()
