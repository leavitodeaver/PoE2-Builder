"""SEARCH42 (the manager's brief, 26 Nov: AUDIT40 A40-52, a side totem's placement charged in UPKEEP_PRESS)."""
from types import SimpleNamespace


def test_a_side_totem_is_charged_once_per_its_totem_duration_at_its_placement_time():
    """Shockwave Totem kept placed (one totem), re-placed as it expires: once per its 8 s totem duration, 0.6 s of the
    player's time a placement (PoB's TotemPlacementTime), whatever the totem's own Shockwave Slam rate is."""
    from builder.search.inner import upkeep_use

    row = {"name": "Shockwave Totem", "speed": 1.4, "castRate": 1.4, "totemDuration": 8.0, "totemPlacementTime": 0.6}
    uses, how, seconds = upkeep_use(row)
    assert abs(uses - 1 / 8.0) < 1e-12 and seconds == 0.6 and "totem duration" in how
    # (no totem duration read: no data sets how often it is placed, not charged)
    assert upkeep_use({**row, "totemDuration": None})[0] == 0.0
    # (not a totem: press_use_rate / press_action as before, e.g. Thunderstorm once per its duration)
    uses, _, seconds = upkeep_use({"name": "Thunderstorm", "speed": 1.389, "castRate": 1.389, "duration": 4.39})
    assert abs(uses - 1 / 4.39) < 1e-9 and abs(seconds - 1 / 1.389) < 1e-9


def test_totems_are_no_longer_left_out_of_upkeep():
    """(A40-52) Totem / SummonsTotem skills were skipped on an unverified 'another model charges them'."""
    from builder.search.inner import UPKEEP_TYPES_CHARGED

    assert not {"Totem", "SummonsTotem"} & UPKEEP_TYPES_CHARGED
    assert {"AppliesCurse", "Mark", "Warcry"} <= UPKEEP_TYPES_CHARGED


def _candidates(rows, main_index=1):
    from builder.search.inner import Search

    groups = [{"index": 1, "gems": [{"name": "Earthquake"}]},
              {"index": 2, "gems": [{"name": "Shockwave Totem"}]},
              {"index": 3, "gems": [{"name": "Dark Effigy"}]}]
    types = {"Shockwave Totem": ["Totem", "SummonsTotem", "Duration"], "Dark Effigy": ["Totem", "SummonsTotem"],
             "Earthquake": ["Attack"]}

    def call(what, **kw):
        if what == "groups":
            return {"mainSocketGroup": main_index, "groups": groups}
        assert what == "useRates"
        return {"rates": [r for r in rows if r["index"] in kw["indices"]]}

    search = object.__new__(Search)
    search.build = SimpleNamespace(call=call, objective=SimpleNamespace(),
                                   ctx=SimpleNamespace(any_gems={n: {"skillTypes": t} for n, t in types.items()},
                                                       all_gems=None))
    search.gems = SimpleNamespace(buttons=lambda: ["Earthquake", "Shockwave Totem", "Dark Effigy"])
    return search.upkeep_candidates()


def test_upkeep_candidates_take_a_side_totem_with_a_duration_and_placement_time():
    rows = [{"index": 2, "name": "Shockwave Totem", "speed": 1.2, "totemDuration": 8.0, "totemPlacementTime": 0.6},
            {"index": 3, "name": "Dark Effigy", "speed": 0.9, "totemPlacementTime": 0.5}]  # (no duration read)
    assert set(_candidates(rows)) == {"Shockwave Totem"}
    # (the main group's totem is the damage itself: never charged)
    rows[0]["index"] = 1
    assert _candidates(rows, main_index=1) == {}


def test_searchb10_polish_hook_runs_only_with_its_switch(monkeypatch):
    """(SEARCHB10's hook, 096af924) finalize_task calls the polish after the final regrow and before the open sockets;
    with POE2_SEARCH_POLISH off, or a tree without Gear.polish, nothing runs."""
    import inspect
    from builder.search import gear as gear_module, inner

    src = inspect.getsource(inner.finalize_task)
    assert src.rfind('"final regrow"') < src.find("polish_finished(") < src.find('"open sockets"')
    calls = []
    search = SimpleNamespace(weigh_uptimes=lambda: calls.append("weigh"), build=SimpleNamespace(score=1.0),
                             gear=SimpleNamespace(polish=lambda deadline, reweigh: calls.append("polish")))
    trace = []
    monkeypatch.delattr(gear_module, "polish_on", raising=False)
    assert inner.polish_finished(search, 60.0, trace) is False and calls == [] and trace == []
    monkeypatch.setattr(gear_module, "polish_on", lambda: False, raising=False)
    monkeypatch.setattr(gear_module, "polish_seconds", lambda s: min(60.0, s), raising=False)
    assert inner.polish_finished(search, 60.0, trace) is False and calls == [] and trace == []
    monkeypatch.setattr(gear_module, "polish_on", lambda: True, raising=False)
    assert inner.polish_finished(search, 60.0, trace) is True
    assert calls == ["weigh", "polish"] and trace == [["gear polish", 1.0]]
