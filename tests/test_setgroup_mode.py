"""POE2_SETGROUP (builder/pob/engine.py, PERF14): how bridge.setGroup recalculates when a skill part or stat set is
given. No PoB needed here; the paths themselves are compared by the "check" mode on real PoB."""

import pytest

from builder.pob import engine as engine_module
from builder.pob.engine import PobError


@pytest.mark.parametrize("text, mode", [(None, "one"), ("", "one"), ("1", "one"), ("one", "one"), ("2", "two"),
                                        ("two", "two"), (" Check ", "check")])
def test_the_switch_values(monkeypatch, text, mode):
    if text is None:
        monkeypatch.delenv("POE2_SETGROUP", raising=False)
    else:
        monkeypatch.setenv("POE2_SETGROUP", text)
    assert engine_module.setgroup_mode() == mode


def test_a_wrong_switch_value_is_an_error(monkeypatch):
    monkeypatch.setenv("POE2_SETGROUP", "three")
    with pytest.raises(PobError, match="POE2_SETGROUP"):
        engine_module.setgroup_mode()
