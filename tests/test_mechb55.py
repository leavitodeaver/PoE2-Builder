"""MECHB55: Flame Wall's projectile buff (PoB's flameWallAddedDamage / flameWallInfused boxes, set from the build)."""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from builder.search import conditions


def test_flame_wall_line_is_the_plain_uptime_without_forced_moves_and_less_with_them():
    assert conditions.flame_wall_line(0.0, 6.4, 0.0) == 0.0
    assert conditions.flame_wall_line(0.1, 6.4, 0.0) == pytest.approx(0.64)  # (a cast every 10 s, 6.4 s walls)
    assert conditions.flame_wall_line(0.5, 6.4, 0.0) == pytest.approx(1.0)  # (recast before it ends: always up)
    # forced moves at 1 a minute: in line from each cast until the first move, (1 - e^(-m D)) / (m T)
    m = 1 / 60
    assert conditions.flame_wall_line(0.1, 6.4, m) == pytest.approx((1 - math.exp(-m * 6.4)) / (m * 10))
    assert conditions.flame_wall_line(0.1, 6.4, m) < 0.64


def test_the_boxes_read_their_shares_and_stay_off_without_flame_wall():
    assert conditions._flame_wall([], {}) == (0.0, "")
    assert conditions._flame_wall_infused([], {}) == (0.0, "")
    on, basis, up = conditions._flame_wall([], {"_flame_wall": (0.6, "its casts")})
    assert on and up == pytest.approx(0.6) and "Flame Wall" in basis
    assert not conditions._flame_wall([], {"_flame_wall": (0.0, "none")})[0]
    on, basis, up = conditions._flame_wall_infused([], {"_flame_wall_infused": (0.3, "x")})
    assert on and up == pytest.approx(0.3) and "Lightning Infusion" in basis
    assert {"flameWallAddedDamage", "flameWallInfused"} <= {c.var for c in conditions.CONDITIONS}


def _stub(main, groups, uses, rows, lightning=0.0, made=()):
    build = SimpleNamespace(call=lambda name, **_: (
        {"groups": [{"index": i + 1, "gems": [{"name": n} for n in names]} for i, names in enumerate(groups)]}
        if name == "groups" else {"rates": list(rows.values())}))
    stub = SimpleNamespace(build=build, main_skill=main)

    def group_uses(groups_, names, rows_=None):
        if rows_ is not None:
            rows_.update(rows)
        return {n: uses[n] for n in names if n in uses}
    stub._group_uses = group_uses
    stub._chosen_use = lambda name: 0.0
    stub._infusion_supply = lambda lines, bomb=False: ({"Fire": 0.0, "Cold": 0.0, "Lightning": lightning, "Any": 0.0},
                                                      list(made))
    return stub


def test_flame_wall_share_from_its_own_casts_and_duration(monkeypatch):
    monkeypatch.setattr(conditions.boss_hits, "red_flash_rate", lambda stats, reading="uses": (0.0, "no moves"))
    stub = _stub("Lightning Arrow", [["Lightning Arrow"], ["Flame Wall"]], {"Flame Wall": 0.1},
                 {2: {"index": 2, "duration": 6.4}})
    share, how = conditions.Conditions._flame_wall(stub, {"Speed": 2.0})
    assert share == pytest.approx(0.64) and "6.40 s" in how
    # not cast: 0, said
    stub = _stub("Lightning Arrow", [["Lightning Arrow"], ["Flame Wall"]], {}, {2: {"index": 2, "duration": 6.4}})
    assert conditions.Conditions._flame_wall(stub, {"Speed": 2.0})[0] == 0.0
    # not in the build: 0
    stub = _stub("Lightning Arrow", [["Lightning Arrow"]], {}, {})
    assert conditions.Conditions._flame_wall(stub, {"Speed": 2.0}) == (0.0, "Flame Wall isn't in an enabled group: 0")


def test_skill_infused_matches_arcs_reading_and_shares_the_supply():
    stub = _stub("Arc", [["Arc"], ["Orb of Storms"], ["Frost Wall"]], {"Frost Wall": 0.5}, {}, 1.0, ["Orb of Storms"])
    arc = conditions.Conditions._arc_infused(stub, [], {"Speed": 1.5})
    assert conditions.Conditions._skill_infused(stub, [], {"Speed": 1.5}, "Arc", "Lightning")[0] == pytest.approx(arc[0])
    stub = _stub("Lightning Arrow", [["Lightning Arrow"], ["Flame Wall"]], {"Flame Wall": 0.2}, {}, 0.0, [])
    assert conditions.Conditions._skill_infused(stub, [], {"Speed": 2.0}, "Flame Wall", "Lightning")[0] == 0.0
    stub = _stub("Lightning Arrow", [["Lightning Arrow"], ["Flame Wall"]], {"Flame Wall": 0.2}, {}, 0.5, ["x"])
    share, how = conditions.Conditions._skill_infused(stub, [], {"Speed": 2.0}, "Flame Wall", "Lightning")
    assert share > 0.5 and "Flame Wall's 0.20 casts/s" in how


def test_vivid_stampede_counts_the_forced_move_rolls_the_dodge_rate_leaves_out():
    """(MECHB55, MECHB54's next step (d)) each boss forced move is one 4.6 m dodge roll (RED_FLASH_AVOID_HOW); the ones
    the dodge rate doesn't already roll for add Wisps."""
    from builder.search import models

    main = models.Skill("Twister", 1, attack=True)
    vs = models.Skill("Vivid Stampede", 7, attack=True)
    plain = models.vivid_stampede_model(main, vs, [], 0.3, 2.0)[0][0]["value"]
    mods, notes = models.vivid_stampede_model(main, vs, [], 0.3, 2.0, forced=0.02)
    assert mods[0]["value"] == pytest.approx(plain + 0.02 * 4.6 / 10 * 4)
    assert "forced moves" in notes[0]["basis"]
    assert models.vivid_stampede_model(main, vs, [], 0.0, 2.0, forced=0.02)[0]  # (forced moves alone feed it)
