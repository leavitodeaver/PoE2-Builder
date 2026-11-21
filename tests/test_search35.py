"""SEARCH35: gate4's reference within the search's own limits (SEARCHB1's G0, the manager 13:00 21 Nov: 2b75d's
reference is 3 passives over the budget and Spirit short, so its ratio compares against a build the search can't
deliver; "Score references within the search's own budget, or show that reading beside the raw one")."""

import importlib.util
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gate4():
    spec = importlib.util.spec_from_file_location("gate4", ROOT / "benchmarks" / "gate4.py")
    gate4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate4)
    return gate4


class _Objective:
    def dps(self, stats):
        return stats["dps"]

    def deficits(self, stats):
        out = []
        if stats["over"] > 0:
            out.append(("Passive points over the level's budget", stats["over"], True))
        if stats["SpiritUnreserved"] < 0:
            out.append(("Spirit", 0.1, True))
        if stats["Str"] < 0:
            out.append(("Str requirement", 0.1, True))
        return out


def _search(groups, stats):
    calls = []

    def set_group(index, enabled):
        group = next(g for g in groups if g["index"] == index)
        if group["enabled"] != enabled:
            sign = -1 if enabled else 1
            stats["SpiritUnreserved"] += sign * group["spirit"]
            stats["dps"] -= sign * group["dps"]
            build.score = stats["dps"]
        group["enabled"] = enabled
        calls.append(("set", index, enabled))

    build = types.SimpleNamespace(objective=_Objective(), stats=stats, score=stats["dps"], groups=lambda: groups,
                                  main_group=lambda: groups[0], set_group=set_group)

    def trim():
        calls.append("trim")
        stats["over"] = 0
        return 3

    search = types.SimpleNamespace(build=build, tree=types.SimpleNamespace(trim_to_budget=trim),
                                   gems=types.SimpleNamespace(refresh_infused=lambda: False))
    for name in ("conditions_round", "choose_play", "resolve", "weigh_uptimes"):
        setattr(search, name, lambda: calls.append(name))
    search.settle = lambda: False
    search.press_round = lambda deadline: None
    return search, calls


def test_a_legal_reference_trims_passives_and_switches_off_the_cheapest_spirit_group():
    gate4 = _gate4()
    groups = [{"index": 1, "enabled": True, "gems": [{"name": "Main"}], "spirit": 0, "dps": 0},
              {"index": 2, "enabled": True, "label": "", "gems": [{"name": "Big Aura"}], "spirit": 60, "dps": 300.0},
              {"index": 3, "enabled": True, "label": "", "gems": [{"name": "Small Aura"}], "spirit": 30, "dps": 50.0},
              {"index": 4, "enabled": True, "label": "", "gems": [{"name": "Attack"}], "spirit": 0, "dps": 10.0}]
    stats = {"dps": 1000.0, "over": 3, "SpiritUnreserved": -20, "Str": 5}
    search, calls = _search(groups, stats)
    legal = gate4.legal_reference(search)
    assert calls[0] == "trim"
    assert legal["broken"] == ["Passive points over the level's budget", "Spirit"]
    # (Small Aura frees enough Spirit and loses least; the main group and a group that frees none never go)
    assert [g["enabled"] for g in groups] == [True, True, False, True]
    assert legal["changes"] == ["3 passive(s) over the level's points removed (the weakest branches)",
                                "Small Aura switched off (Spirit)"]
    assert legal["dps"] == 950.0 and legal["short"] == []


def test_a_reference_within_the_limits_has_no_legal_reading():
    gate4 = _gate4()
    groups = [{"index": 1, "enabled": True, "gems": [{"name": "Main"}], "spirit": 0, "dps": 0}]
    search, calls = _search(groups, {"dps": 1000.0, "over": 0, "SpiritUnreserved": 5, "Str": -1})
    assert gate4.legal_reference(search) is None  # (an attribute requirement alone stays in the raw reading's "short")
    assert calls == []
