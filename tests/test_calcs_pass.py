"""POE2_CALCS_PASS (PERF18): the switch's values, and the check's comparison of two Lua states (no PoB here)."""
import json

import pytest

from builder.pob import engine as engine_module
from builder.pob.engine import PobError, _Checked, calcs_pass_mode
from builder.pob.lua import LuaError


def test_the_second_pass_runs_at_once_unless_the_variable_says_otherwise(monkeypatch):
    for text, mode in (("", "now"), ("0", "now"), ("now", "now"), (" Late ", "late"), ("check", "check")):
        monkeypatch.setenv(engine_module.CALCS_PASS_VARIABLE, text)
        assert calcs_pass_mode() == mode
    monkeypatch.delenv(engine_module.CALCS_PASS_VARIABLE)
    assert calcs_pass_mode() == "now"
    monkeypatch.setenv(engine_module.CALCS_PASS_VARIABLE, "skip")
    with pytest.raises(PobError, match="POE2_CALCS_PASS"):
        calcs_pass_mode()


class _State:
    def __init__(self, answers):
        self.answers, self.calls, self.closed = list(answers), 0, False

    def call(self, function, *args):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    run = call

    def close(self):
        self.closed = True


def _answer(value):
    return [b"true", json.dumps(value).encode()]


def test_the_check_passes_equal_answers_and_stopwatch_readings_and_raises_on_any_other_difference():
    late = _State([_answer({"Life": 5, "seconds": 1.5}), _answer({"Life": 5}), [b"1"], LuaError("x"), _answer({"Life": 5}), LuaError("x")])
    now = _State([_answer({"Life": 5, "seconds": 9.0}), _answer({"Life": 6}), [b"2"], LuaError("x"), LuaError("y"), _answer({"Life": 5})])
    both = _Checked(late, now)
    assert both.call("bridge_dispatch", "stats", "{}") == _answer({"Life": 5, "seconds": 1.5})  # the put-off state's answer
    with pytest.raises(PobError, match="stats: PoB with its second pass put off answers differently"):
        both.call("bridge_dispatch", "stats", "{}")
    with pytest.raises(PobError, match="poke"):
        both.run("return 1", "poke")
    with pytest.raises(LuaError):  # both raised: the call's own error, as with one state
        both.call("bridge_dispatch", "stats", "{}")
    for _ in range(2):
        with pytest.raises(PobError, match="one PoB raised and the other did not"):
            both.call("bridge_dispatch", "stats", "{}")
    assert both.calls == late.calls == 6
    both.close()
    assert late.closed and now.closed


def test_the_check_takes_the_orders_pob_picks_anew_in_each_state_as_equal_and_no_other_order():
    """sourceLines' lines come out in PoB's pairs() order, which two states in one process pick differently
    (replay.ORDER_FREE); any other list in another order is a difference."""
    lines = [{"source": "a", "text": "1"}, {"source": "b", "text": "2"}]
    late = _State([_answer({"lines": lines}), _answer({"gems": ["x", "y"]})])
    now = _State([_answer({"lines": lines[::-1]}), _answer({"gems": ["y", "x"]})])
    both = _Checked(late, now)
    assert both.call("bridge_dispatch", "sourceLines", "{}") == _answer({"lines": lines})
    assert (both.compared, both.unequal_bytes) == (1, 1)
    with pytest.raises(PobError, match="skills: PoB with its second pass put off answers differently"):
        both.call("bridge_dispatch", "skills", "{}")


def test_the_check_takes_an_items_base_lines_in_another_order_as_equal_and_another_base_as_a_difference():
    lines = ["Rarity: RARE", "{base:1}Crucible Tower Shield", "{base:3}Runemastered Crucible Tower Shield", "Quality: 20"]
    text, other = chr(10).join(lines), chr(10).join([lines[0], lines[2], lines[1], lines[3]])
    late = _State([_answer({"slots": {"Weapon 2": text}}), _answer({"slots": {"Weapon 2": text}})])
    now = _State([_answer({"slots": {"Weapon 2": other}}), _answer({"slots": {"Weapon 2": text.replace("{base:3}R", "{base:3}X")}})])
    both = _Checked(late, now)
    assert both.call("bridge_dispatch", "equipped", "{}") == _answer({"slots": {"Weapon 2": text}})
    with pytest.raises(PobError, match="equipped: PoB with its second pass put off answers differently"):
        both.call("bridge_dispatch", "equipped", "{}")
