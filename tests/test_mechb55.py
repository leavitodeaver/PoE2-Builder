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
    stub = SimpleNamespace(build=build, main_skill=main, main_types=set())

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


def test_arctic_armour_bursts_at_the_bosss_landed_melee_hits(monkeypatch):
    """(MECHB55) bursts = landed hits x the melee share, capped by the cooldown and the stage gain; 0 with no melee."""
    from builder.search import boss_hits, bosses, models

    monkeypatch.setattr(bosses, "benefit_skill_time", lambda stats: 2.0)
    monkeypatch.setattr(boss_hits, "land_share", lambda stats: 0.5)
    monkeypatch.setattr(boss_hits, "block_share", lambda stats: 0.2)
    monkeypatch.setattr(models, "boss_melee_share", lambda boss, gain=True: (0.6, "a test share"))
    level = {models.ARCTIC_STAGES_STAT: 5, models.ARCTIC_GAIN_STAT: 725}
    mods, notes = models.arctic_armour_model({"_boss": "x"}, 0.25, level)
    assert mods[0]["key"] == "triggerRate" and mods[0]["value"] == pytest.approx(0.25 * 0.6, abs=1e-3)
    assert "Blocked" in notes[0]["basis"] and "per Stage" in notes[0]["basis"]
    monkeypatch.setattr(models, "boss_melee_share", lambda boss, gain=True: (0.0, "no melee"))
    mods, notes = models.arctic_armour_model({"_boss": "x"}, 0.25, level)
    assert mods[0]["value"] == 0 and notes[0]["value"] == "0 a second"
    # capped by one burst a stage gained
    monkeypatch.setattr(models, "boss_melee_share", lambda boss, gain=True: (1.0, "all"))
    monkeypatch.setattr(boss_hits, "land_share", lambda stats: 100.0)
    assert models.arctic_armour_model({"_boss": "x"}, 0.25, level)[0][0]["value"] == pytest.approx(1 / 0.725, abs=1e-3)


def test_midnight_zenith_crystals_from_crits_broken_by_area_hits():
    """(MECHB55) 4 petals a crit, 10 petals -> 5 crystals; Area hits break each ceil(Life / hit) hits after it is made
    (none if that's past its 10 s); breakers random; at most the Limit alive (Little's law)."""
    import math
    from builder.search import models

    level = {models.MIDNIGHT_PETALS_STAT: 4, models.MIDNIGHT_NEED_STAT: 10, models.MIDNIGHT_PER_STAT: 5,
             models.MIDNIGHT_LIMIT_STAT: 10, models.MIDNIGHT_LIFE_STAT: 4768}
    # 2 hits a second, 25% crit: 0.5 crits -> 2 petals -> 1 crystal a second; 2,500 a hit: broken 2 hits (1 s) after
    mods, notes = models.midnight_zenith_model(0.5, "x", 2.0, 2500.0, 0.0, "none", 300.0, level, 10.0, 0.0)
    assert notes[0]["value"] == pytest.approx(300.0)
    assert "no self-feeding" in notes[0]["basis"] and notes[0]["full_dps"] == {"Midnight Zenith": 0.0}
    # at 100% crit (Forced Outcome): 4 crystals a second, each broken 0.5 s after: all 4 (2 alive on average)
    assert models.midnight_zenith_model(2.0, "x", 2.0, 5000.0, 0.0, "none", 300.0, level, 10.0, 0.0)[1][0]["value"]         == pytest.approx(1200.0)
    # the Limit binds: 20 crystals a second, each living 1 s: at most 10 a second
    assert models.midnight_explosions(20.0, 1.0, 0.0, 10.0, 10) == pytest.approx(10.0)
    # hits too weak (probe POE2T4860: ~150 a hit on 4,768 Life, ~37 s): none broken before 10 s, no breaker: 0, said
    mods, notes = models.midnight_zenith_model(0.5, "x", 0.87, 150.0, 0.0, "none", 300.0, level, 10.0, 0.0)
    assert mods == [] and "hits alone break none" in notes[0]["basis"] and "expire" in notes[0]["basis"]
    # nothing breaks them (a non-Area main skill, no breaker): 0
    assert models.midnight_zenith_model(0.5, "x", 0.0, 2500.0, 0.0, "none", 300.0, level, 10.0, 0.0)[0] == []
    # a Freeze consumer at 0.5 a second breaks a projectile build's crystals: 1 - e^(-5) of them
    notes = models.midnight_zenith_model(0.5, "x", 0.0, 0.0, 0.5, "a consumer", 300.0, level, 10.0, 0.0)[1]
    assert notes[0]["value"] == pytest.approx(round(300.0 * (1 - math.exp(-5.0)), 1))
    assert models.midnight_explosions(1.0, None, 0.1, 10.0, 10) == pytest.approx(1 - math.exp(-1.0))


def test_flame_wall_with_fortress_is_a_ring_and_falling_projectiles_get_a_zero_row(monkeypatch):
    """(MECHB55; RESEARCH47 R47-5) Fortress makes the wall a circle on the boss: the plain uptime, no forced-move break;
    (R47-4) a ProjectileNoCollision main skill gets a 0 row, the headline unchanged."""
    monkeypatch.setattr(conditions.boss_hits, "red_flash_rate", lambda stats, reading="uses": (1 / 6, "moves"))
    rows = {2: {"index": 2, "duration": 6.4}}
    stub = _stub("Rain of Arrows", [["Rain of Arrows"], ["Flame Wall", "Fortress I"]], {"Flame Wall": 0.1}, rows)
    share, how = conditions.Conditions._flame_wall(stub, {"Speed": 2.0})
    assert share == pytest.approx(0.64) and "ring around the boss" in how
    stub = _stub("Rain of Arrows", [["Rain of Arrows"], ["Flame Wall"]], {"Flame Wall": 0.1}, rows)
    stub.main_types = {"ProjectileNoCollision", "Attack"}
    share, how = conditions.Conditions._flame_wall(stub, {"Speed": 2.0})
    assert share < 0.64 and "R47-4" in how and "0%" in how


def test_return_share_for_the_mirror_loop():
    """(MECHB55) the share of the main skill's projectiles that Return (for GEM's Mirror of Refraction loop)."""
    from builder.search import models

    bolt = models.Skill("Frostbolt", 1, spell=True)
    amalgam = [{"name": "Frostbolt"}, {"name": "Breachlord's Amalgam"}]
    assert models.return_share([], bolt, {"Projectile", "Cold", "Spell"}, amalgam, set())[0] == 1.0
    assert models.return_share([], bolt, {"Projectile", "Cold", "Spell"}, [{"name": "Frostbolt"}], set()) == (0.0, "")
    assert models.return_share([], bolt, {"Spell", "Cold"}, amalgam, set()) == (0.0, "")  # (not a projectile skill)
    arrow = models.Skill("Lightning Arrow", 1, attack=True)
    cadiro = ["Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow"]
    assert models.return_share(cadiro, arrow, {"Projectile", "Attack"}, [], {"Bow"})[0] == models.CADIRO_REVERSING_SHARE
