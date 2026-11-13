"""The delivered build's floor checks count an effect only at full uptime (the user's rule for floors; SEARCH11).

finalize reads its facts with every part-time effect on (conditions, flasks, ledger shares). The search's own deficits
read the floors with them off (Objective.deficits on floor_deltas); run.result's checks must do the same, or a build
can be delivered with a floor shown met that holds for only part of the fight (an OVERCOUNT in the report)."""

from __future__ import annotations

from builder.request.constraints import CheckResult, Penalty, fmt
from builder.search.run import floors_at_full_uptime


def checks_for(penalties: list[Penalty], stats: dict) -> list[dict]:
    """The checks as compile_request makes them for each penalty, read on the given stats."""
    return [CheckResult(p.label, f"≥ {fmt(p.floor)}", fmt(stats[p.stat]), stats[p.stat] >= p.floor).__dict__
            for p in penalties]


def test_a_floor_met_only_with_a_part_time_effect_on_fails_the_delivered_check():
    penalties = [Penalty("Life", "Life", 3000), Penalty("Fire resistance", "FireResist", 75),
                 Penalty("Evade chance", "EvadeChance", 30)]
    stats = {"Life": 3200, "FireResist": 75, "EvadeChance": 40}
    # a part-time flask carries 400 Life; a part-time condition carries 5 Fire resistance; Evade doesn't move
    deltas = [{"Life": -400.0, "FireResist": -5.0}]
    checks = checks_for(penalties, stats)
    assert all(c["ok"] for c in checks)  # what the report said before: every floor met with the effects on

    floors_at_full_uptime(checks, penalties, stats, deltas)
    by = {c["label"]: c for c in checks}
    assert by["Life"]["ok"] is False and by["Life"]["delivered"].startswith("2,800 with part-time effects off")
    assert "3,200 with them on" in by["Life"]["delivered"]
    assert by["Fire resistance"]["ok"] is False
    assert by["Evade chance"]["ok"] is True and by["Evade chance"]["delivered"] == "40"  # untouched: no delta


def test_a_floor_that_holds_with_the_effects_off_still_passes_and_effects_that_raise_a_stat_off_change_nothing():
    penalties = [Penalty("Life", "Life", 3000), Penalty("Mana", "Mana", 500)]
    stats = {"Life": 3600, "Mana": 600}
    checks = checks_for(penalties, stats)
    # Life drops but stays above its floor; Mana rises with the effect off (e.g. a reservation it costs): the floor
    # reads the worse of the two, which is the "on" value
    floors_at_full_uptime(checks, penalties, stats, [{"Life": -400.0, "Mana": 50.0}])
    by = {c["label"]: c for c in checks}
    assert by["Life"]["ok"] is True and by["Life"]["delivered"].startswith("3,200 with part-time effects off")
    assert by["Mana"] == checks_for([penalties[1]], stats)[0]
    # no part-time effects: nothing changes
    before = checks_for(penalties, stats)
    after = checks_for(penalties, stats)
    floors_at_full_uptime(after, penalties, stats, [])
    assert after == before
