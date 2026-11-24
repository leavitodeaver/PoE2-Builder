"""MECHB59: a Vivid Stampede Stag's landings Ignite as one burst, while each landing keeps its Flammability."""
import pytest

from builder.search import conditions as C
from builder.search import models as M


def _uptime(others):
    return C.ailment_uptime({"Speed": 0, "HitChance": 100, "IgniteChancePerHit": 0, "IgniteDuration": 4,
                             "_ailment_others": {"Ignite": others}}, "Ignite")


def test_stag_burst_chance_and_spread():
    assert M.stag_burst(0.2, 1.0) == pytest.approx((0.05, 1.0, 1.5))
    stags, each, spread = M.stag_burst(0.2, 0.5)
    assert (stags, each) == pytest.approx((0.05, 0.9375))
    assert 0 < spread < 1.5
    assert M.stag_burst(0.2, 0.0) == (0.05, 0.0, 0.0)


def test_a_stag_ignites_once_not_as_four_spread_hits():
    # one Stag each 20 s at a 100% chance: Ignited 4 s + the 1.5 s of landings, not 4 hits x 4 s of 20 s
    assert _uptime([(M.VIVID_STAMPEDE, 0.2, 1.0, 4.0)]) == pytest.approx(5.5 / 20)
    assert _uptime([("Another Skill", 0.2, 1.0, 4.0)]) == pytest.approx(0.8)


def test_each_landing_keeps_its_flammability():
    stats = {"Speed": 0, "HitChance": 100, "IgniteChancePerHit": 0,
             "_ailment_others": {"Ignite": [(M.VIVID_STAMPEDE, 0.2, 0.25, 4.0)]}}
    assert C.flammability_sources(stats) == [(M.VIVID_STAMPEDE, 0.2, 25.0)]


def _feeder(name="Arc", uses=0.0, speed=2.0, **row):
    return {"name": name, "uses": uses, "speed": speed, "why": "test", "final": 0.0,
            "row": {"HitChance": 100.0, "CritChance": 50.0, "LightningCritAverage": 500.0, **row}}


def test_a_self_used_skill_feeds_cast_on_critical():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    gain, crits, how = M.energy_feed("crit", [_feeder()], stats)
    # 2 uses/s x 50% crit x Power 20 x (500 / 1000 of the Ailment Threshold)
    assert (gain, crits) == pytest.approx((10.0, 1.0))
    assert "Arc" in how


def test_timed_feeders_leave_the_at_will_feeder_the_rest():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    gain, _, _ = M.energy_feed("crit", [_feeder("Timed", uses=0.5, speed=1.0), _feeder("Arc")], stats)
    # Timed: 0.5 x 5 Energy; it takes half the time, so Arc runs at 1 use/s x 5 Energy
    assert gain == pytest.approx(0.5 * 5.0 + 1.0 * 5.0)


def test_ailment_feed_counts_shocks_and_the_ignites_share():
    stats = {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")}
    row = {"ShockChancePerHit": 25.0, "IgniteChancePerHit": 50.0, "IgniteDPS": 100.0, "IgniteDuration": 4.0}
    gain, _, _ = M.energy_feed("ailment", [_feeder(speed=1.0, **row)], stats)
    assert gain == pytest.approx(1.0 * (0.25 + 0.5 * 0.4) * 20.0)


def test_no_feeder_gives_no_energy():
    assert M.energy_feed("crit", [], {"EnemyAilmentThreshold": 1000.0, "_boss_power": (20.0, "")})[0] == 0.0


def test_tempest_shockwave_is_held_to_the_bells():
    """AUDIT37 W3 (patch 0.5.0): the set's hit comes once per Bell a Perfect dash explodes."""
    storm = M.Skill("Gathering Storm", 1, attack=True, melee=True)
    none = M.gathering_shockwave_model(storm, "Tempest Shockwave", 1.2, {"rate": 0.0, "how": "no Bell"})
    assert none[0][0]["value"] == pytest.approx(-100.0)
    some = M.gathering_shockwave_model(storm, "Tempest Shockwave", 1.2, {"rate": 0.3, "how": "Bells"})
    assert some[0][0]["value"] == pytest.approx(-75.0) and some[0][0][M.HIT_CUT_PER_USE] == pytest.approx(0.25)
    assert M.gathering_shockwave_model(storm, "Tempest Shockwave", 1.2, {"rate": 9.0, "how": "Bells"})[0][0]["value"] == 0
    assert M.gathering_shockwave_model(storm, "Perfect Dash", 1.2, {"rate": 0.0, "how": ""}) == ([], [])


def test_tempest_bell_all_bells_is_a_row_and_keeps_the_headline():
    """R55-2: one Strike hitting every Bell alive is a LOW row, capped per Bell; the headline stays one Bell a Strike."""
    args = (0.2, 1.0, ("Quarterstaff Strike", 1.0, 100.0, True), 100.0, 25.0, 15.0, [])
    one, one_note = M.tempest_bell_model(*args, allowed=1.0)
    three, three_note = M.tempest_bell_model(*args, allowed=3.0)
    assert three[0]["value"] == pytest.approx(one[0]["value"])  # the headline doesn't move
    # 0.8 Strikes a second x 3 Bells alive = 2.4 shockwaves, held to 0.2 Bells a second x 10 hits a Bell = 2.0
    assert "3.00 of at most 3" in three_note[0]["basis"] and "x3.500" in three_note[0]["basis"]
    assert "1.00 of at most 1" in one_note[0]["basis"] and "x2.000" in one_note[0]["basis"]


def test_melee_projectiles_are_a_row_on_falling_thunder():
    """R56-1: +N on Falling Thunder's Power Charge volley, which KeywordPopups 74 holds to one hit on a lone boss."""
    thunder = M.Skill("Falling Thunder", 1, attack=True, melee=True)
    sets = [{"label": "Melee", "TotalDPS": 100.0}, {"label": "Projectile", "TotalDPS": 50.0}]
    line = ["Melee Attacks fire 3 additional Projectiles"]
    note = M.melee_projectiles_note(thunder, line, sets, 2.0)[0]
    assert note["value"] == "x1.000" and "6 projectiles" in note["basis"] and "9 with this line" in note["basis"]
    assert "x3.667" in note["basis"] and "x2.667" in note["basis"]  # (100 + 50 x 9) / 150; (100 + 50 x 6) / 150
    assert "no projectiles to add" in M.melee_projectiles_note(thunder, line, sets, 0.0)[0]["basis"]
    assert "no projectile of its own" in M.melee_projectiles_note(M.Skill("Maul", 1, attack=True), line, [], 0.0)[0]["basis"]
    assert M.melee_projectiles_note(thunder, [], sets, 2.0) == []


def test_hailstorm_counts_reloaded_recently_after_the_builds_own_reloads():
    """R56-2 / R56-3: the 30% line for the time after Emergency Reload in the headline, all fight and per rain as rows."""
    hail = M.Skill("Hailstorm Rounds", 1, attack=True)
    stats = {"Speed": 0.5, "ReloadTime": 0.4, "BoltCount": 50, "FiringRate": 0.5, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 40, "ChanceToNotConsumeAmmo": 25}
    line = ["Bolts fired by Crossbow Attacks have 30% chance to not expend Ammunition if you've Reloaded Recently"]

    def run(up):
        return M.hailstorm_rounds_model(hail, stats, (0.3, "a"), (0.3, "b"), texts=line, reload_up=(up, "Emergency Reload"))
    none, some = run(0.0), run(0.1)
    ratio = (1 + some[0][0]["value"] / 100) / (1 + none[0][0]["value"] / 100)
    assert ratio == pytest.approx((0.1 / 0.45 + 0.9 / 0.75) * 0.75, rel=1e-3)
    assert "after your own reloads" in some[1][0]["basis"] and "one 0.2 s limit a rain" in some[1][0]["basis"]
