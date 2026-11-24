"""SPEED2: the gate's work clock (POE2_SEARCH_WORK_CLOCK=1, gate4 --work): a task's time is a fixed cost per engine call, so
the same seed and code stop at the same point whatever the PC's load. No PoB needed."""

import time
from types import SimpleNamespace

import pytest

from builder import clock
from builder.search import inner


class FakeEngine:
    def __init__(self):
        self.lua = SimpleNamespace(calls=0)
        self.seen = []

    def call(self, method, **args):
        self.seen.append(method)
        if method != "cachedRead":  # (a read answered from the engine's cache doesn't reach Lua)
            self.lua.calls += 1
        return {"method": method}


@pytest.fixture
def restore_clock():
    before = clock.now
    yield
    clock.now = before


def test_a_call_costs_its_table_value_and_a_what_if_its_cases():
    work, engine = inner.WorkClock(), FakeEngine()
    work.watch(engine)
    assert engine.call("setGroup", index=1) == {"method": "setGroup"}
    assert work.spent == pytest.approx(inner.WORK_COST["setGroup"])
    engine.call("itemDeltas", cases=[{}, {}, {}])
    assert work.spent == pytest.approx(inner.WORK_COST["setGroup"] + 3 * inner.WORK_COST_PER_CASE["itemDeltas"])


def test_a_cached_read_costs_nothing_and_an_unknown_method_the_default():
    work, engine = inner.WorkClock(), FakeEngine()
    work.watch(engine)
    engine.call("cachedRead")
    assert work.spent == 0.0
    engine.call("someNewMethod")
    assert work.spent == pytest.approx(inner.WORK_COST_OTHER)


def test_the_same_calls_read_the_same_clock():
    def run():
        work, engine = inner.WorkClock(), FakeEngine()
        work.watch(engine)
        readings = []
        for method in ("equip", "stats", "setConfig", "nodeDeltas"):
            engine.call(method, cases=[{}] if method == "nodeDeltas" else None)
            readings.append(work.now())
        return readings

    assert run() == run()


def test_a_loop_that_only_reads_the_clock_still_reaches_its_deadline():
    work = inner.WorkClock()
    deadline, reads = work.now() + 1.0, 0
    while work.now() < deadline:
        reads += 1
    assert 0 < reads <= 1 / inner.WORK_READ + 1


def test_off_by_default_the_processor_clock_is_used(restore_clock, monkeypatch):
    monkeypatch.setattr(inner, "WORK_CLOCK", False)
    engine = FakeEngine()
    real_call = engine.call
    inner.use_cpu_clock({inner.CPU_CLOCK: True}, engine)
    assert clock.now is time.process_time
    assert engine.call == real_call


def test_on_the_workers_clock_is_swapped_once_and_only_on_a_cpu_clock_run(restore_clock, monkeypatch):
    monkeypatch.setattr(inner, "WORK_CLOCK", True)
    engine = FakeEngine()
    before = clock.now
    inner.use_cpu_clock({}, engine)
    assert clock.now is before
    inner.use_cpu_clock({inner.CPU_CLOCK: True}, engine)
    first = clock.now
    assert isinstance(first.__self__, inner.WorkClock)
    wrapped = engine.call
    inner.use_cpu_clock({inner.CPU_CLOCK: True}, engine)
    assert clock.now.__self__ is first.__self__ and engine.call is wrapped


def _diverge_marks(xmls):
    from builder.search.run import Run
    from builder.search.skeletons import Skeleton

    skeleton = Skeleton("Monk", "Acolyte of Chayula", "Explosive Transmutation", "Wand", None, "Shield").as_dict()
    tasks = [("builder.search.inner:optimize_task", {"skeleton": skeleton, "goal": None, "state": {"xml": x}}) for x in xmls]
    Run.diverge(tasks)
    return [bool(args["state"].get("restarted")) for _, args in tasks]


ONE_ORDER = "<Item>|{base:1}Tideseer Mantle|{base:3}Runemastered Tideseer Mantle|</Item>".replace("|", chr(10))
OTHER_ORDER = "<Item>|{base:3}Runemastered Tideseer Mantle|{base:1}Tideseer Mantle|</Item>".replace("|", chr(10))
ANOTHER_BUILD = "<Item>|{base:1}Tideseer Mantle|{base:2}Runeforged Tideseer Mantle|</Item>".replace("|", chr(10))


def test_on_the_work_clock_one_build_in_two_table_orders_is_a_duplicate_line(monkeypatch):
    monkeypatch.setenv("POE2_SEARCH_WORK_CLOCK", "1")
    assert _diverge_marks([ONE_ORDER, OTHER_ORDER, ANOTHER_BUILD]) == [False, True, False]


def test_off_the_work_clock_the_duplicate_check_is_as_it_was(monkeypatch):
    monkeypatch.delenv("POE2_SEARCH_WORK_CLOCK", raising=False)
    assert _diverge_marks([ONE_ORDER, OTHER_ORDER, ONE_ORDER]) == [False, False, True]
