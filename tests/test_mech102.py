"""MECH102: the other groups' Shocks on the boss (conditions.shock_others): the Shocked share and the strongest Shock."""
import pytest
from builder.search import conditions as c, models

BASE = {"Speed": 2.0, "HitChance": 100.0}
GROUPS = [{"index": 1, "gems": [{"name": "Fireball"}]}, {"index": 2, "gems": [{"name": "Orb of Storms"}]},
          {"index": 3, "gems": [{"name": "Spark"}]}, {"index": 4, "gems": [{"name": "Vivid Stampede"}]}]


def orb_row(**kw):
    row = {"name": "Orb of Storms", "speed": 0.5, "triggered": True, "HitChance": 100.0, "ShockChancePerHit": 10.0,
           "ShockEffectMod": 1.5, "ShockDuration": 8.0}
    row.update(kw)
    return row


def test_side_shocks_are_listed_with_their_own_magnitude_and_duration():
    rows = {1: {"name": "Fireball", "speed": 2.0, "ShockChancePerHit": 50.0}, 2: orb_row(),
            3: {"name": "Spark", "speed": 1.0, "triggered": True, "ShockChancePerHit": 0.0}}
    out, mags, how = c.shock_others(rows, GROUPS, {"Fireball", "Orb of Storms", "Spark"}, 1)
    # the main group (1) and a group with no Shock chance (3) are left out
    assert out == [("Orb of Storms", 0.5, pytest.approx(0.1), 8.0)]
    assert mags == {"Orb of Storms": pytest.approx(30.0)}  # 20% x its own ShockEffectMod 1.5 (Overcharge's)
    assert "Orb of Storms's Shocks" in how
    # capped at the Shock maximum; with no ShockEffectMod the base 20%
    assert c.shock_others({2: orb_row(ShockEffectMod=9.0)}, GROUPS, {"Orb of Storms"}, 1, top=50.0)[1] == {
        "Orb of Storms": 50.0}
    no_mod = orb_row()
    del no_mod["ShockEffectMod"]
    _, mags, how = c.shock_others({2: no_mod}, GROUPS, {"Orb of Storms"}, 1)
    assert mags == {"Orb of Storms": 20.0} and "the base 20%" in how
    # a group that isn't a hitter isn't counted
    assert c.shock_others({2: orb_row()}, GROUPS, set(), 1)[0] == []


def test_the_morrigans_guidance_scales_the_stags_shocks():
    scale, how = c.stag_shock_scale(["Stags have 20% more Shock Magnitude per leap"])
    assert scale == pytest.approx((1 + 1.2 + 1.4 + 1.6) / 4) and "x1.60" in how
    assert c.stag_shock_scale(["Stags deal 20% more damage per leap"]) == (1.0, "")
    row = orb_row(name=models.VIVID_STAMPEDE, ShockEffectMod=1.0)
    _, mags, how = c.shock_others({4: row}, GROUPS, {"Vivid Stampede"}, 1,
                                  texts=["Stags have 20% more Shock Magnitude per leap"])
    assert mags == {"Vivid Stampede": pytest.approx(26.0)} and "Mórrigan" in how
    # not on another skill's Shocks
    assert c.shock_others({2: orb_row(ShockEffectMod=1.0)}, GROUPS, {"Orb of Storms"}, 1,
                          texts=["Stags have 20% more Shock Magnitude per leap"])[1] == {"Orb of Storms": 20.0}


def test_a_side_shock_shocks_the_boss_when_the_main_skill_doesnt():
    others = {"Shock": [("Orb of Storms", 0.5, 0.1, 8.0)]}
    alone = {**BASE, "ShockChancePerHit": 0.0}
    assert c.shock_share(alone) == 0.0  # (before MECH102 this was the answer with the side Shock too)
    share = c.shock_share({**alone, "_ailment_others": others})
    assert share == pytest.approx(1 - c.periodic_absent(0.1, 0.5 * 8.0))
    # joined with the main skill's own as independent
    own = {**BASE, "ShockChancePerHit": 5.0, "ShockDuration": 8.0}
    both = c.shock_share({**own, "_ailment_others": others})
    assert both == pytest.approx(1 - c.periodic_absent(0.05, 16.0) * c.periodic_absent(0.1, 4.0))


def test_a_consumer_waits_for_the_next_shock_from_any_source():
    others = {"Shock": [("Orb of Storms", 0.5, 0.1, 8.0)]}
    stats = {**BASE, "ShockChancePerHit": 0.0, "_consumes": {"Shock": "Siphoning Strike"}}
    assert c.shock_share(stats) == 0.0
    assert c.shock_share({**stats, "_ailment_others": others}) == pytest.approx(
        c.consumed_share(2.0, 0.0, 0.0, extra=0.05))
    # consumed beside the main skill: every source's applications reapply it
    side = {**BASE, "ShockChancePerHit": 0.0, "_side_consumes": {"Shock": (1.0, "Shockchain Arrow")},
            "_ailment_others": others}
    up = 1 - c.periodic_absent(0.1, 4.0)
    assert c.shock_share(side) == pytest.approx(c.side_consumed(up, side, "Shock", 0.05, 1.0))
    assert 0 < c.shock_share(side) < up


def test_the_strongest_live_shock_averaged_over_the_shocked_time():
    # alone: its own magnitude; below the floor PoB applies anyway: left unset
    assert c.shock_effect_box([("Orb", 0.5, 40.0)], 20.0)[0] == 40.0
    assert c.shock_effect_box([("Orb", 0.5, 20.0)], 20.0)[0] == 20.0
    assert c.shock_effect_box([("Orb", 0.5, 15.0)], 20.0)[0] == 0.0
    # 40% up half the time, 20% up half the time: the 40% one whenever it's up (0.5), the 20% one alone 0.25
    value, how = c.shock_effect_box([("weak", 0.5, 20.0), ("strong", 0.5, 40.0)], 10.0)
    assert value == pytest.approx(round((0.5 * 40 + 0.25 * 20) / 0.75, 1)) and "strongest" in how
    assert c.shock_effect_box([], 20.0) == (0.0, "")


def test_the_effect_of_shock_box_takes_the_side_shock():
    others = {"Shock": [("Orb of Storms", 0.5, 0.1, 8.0)]}
    # the main skill doesn't Shock: PoB's floor is 20% x its own magnitude modifiers (here none)
    stats = {**BASE, "ShockChancePerHit": 0.0, "_ailment_others": others, "_shock_mags": {"Orb of Storms": 30.0},
             "_sg_magnitude": {"inc": 0, "more": 1, "enemy": 1, "max": 100, "left": []}}
    value, how = c._shock_effect([], stats)
    assert value == 30.0 and "Orb of Storms's Shocks 30%" in how
    # the main skill Shocks at 20% x 2.0 = 40% nearly all fight: its own 40%, which PoB applies anyway
    main = {**stats, "ShockChancePerHit": 50.0, "_main_shock_effect": 2.0}
    assert c._shock_effect([], main)[0] == pytest.approx(40.0, abs=0.1)
    # the main skill Shocks weakly (20%) and rarely; the side one (30%) holds most of the Shocked time
    weak = {**stats, "ShockChancePerHit": 1.0, "_main_shock_effect": 1.0}
    value, _ = c._shock_effect([], weak)
    assert 20.0 < value < 30.0
    # nothing beside and no ground: no box
    assert c._shock_effect([], {**BASE, "ShockChancePerHit": 50.0}) == (0.0, "")


def test_shocked_ground_alone_keeps_its_magnitude_and_weighs_against_the_main_skills_shock():
    ground = {**BASE, "ShockChancePerHit": 0.0, "_shocked_ground": (1.0, "Lightning Warp's ground"),
              "_sg_magnitude": {"inc": 50, "more": 1, "enemy": 1, "max": 100, "left": []}}
    value, how = c._shock_effect([], ground)
    assert value == 30.0 and "Shocked Ground" in how
    # the ground half the time, the main skill's own 20% Shock the rest: before MECH102 the box held 30% all of it
    half = {**ground, "_shocked_ground": (0.5, "x"), "ShockChancePerHit": 100.0, "_main_shock_effect": 1.0}
    value, _ = c._shock_effect([], half)
    assert 20.0 < value < 30.0


def test_a_lone_triggered_mark_takes_pobs_trigger_rate_or_none():
    mark = models.Skill("Voltaic Mark", group=5, triggered=True)
    # (before MECH102: "assumed applied when needed, at no cost (LOW)", a flat rate)
    use = models.mark_use(mark, [mark])
    assert use.period == float("inf") and not use.on_demand and "never applied" in use.how
    use = models.mark_use(mark, [mark], rates={"Voltaic Mark": 0.25})
    assert use.period == pytest.approx(4.0) and not use.on_demand and "PoB's trigger rate" in use.how


def test_dominion_halves_archon_of_chayulas_more_as_well_as_ending_the_recovery():
    # (the manager 00:15, R49-5: Dominion's "50% reduced effect of Archon Buffs on you" must shrink the Archon of
    # Chayula's 20% more, not only remove the recovery period; the lines as the tree gives them)
    inputs = models.read_inputs([{"text": "50% reduced effect of Archon Buffs on you", "source": "passive:Dominion"},
                                 {"text": "Archon Buffs have no recovery period after you lose one",
                                  "source": "passive:Dominion"}])
    assert inputs.total("ArchonEffect") == pytest.approx(-50.0) and inputs.has("ArchonNoRecovery")
    # Glory refills in 5 s: with the 20 s recovery one Archon per 30 s (1/3 up); Dominion: 10 / 15 (2/3 up)
    _, up, _, _ = models.chayula_cycle(1 / 15, inputs.total("ArchonDuration"), inputs.total("ArchonRecovery"),
                                       no_recovery=inputs.has("ArchonNoRecovery"))
    assert up == pytest.approx(2 / 3)
    mods, notes = models.chayula_more_model(up, "Dominion", inputs.total("ArchonEffect"))
    # 20% x 0.5 = 10% more while up, x 2/3 of the fight
    assert mods[0]["scale"] == pytest.approx(10.0 * 2 / 3, abs=1e-3) and "x0.50 Archon effect" in notes[0]["basis"]


def test_moment_of_vulnerability_visages_are_walked_through_the_fight():
    # a 6 s Freeze cycle (4 s Frozen, 2 s refill, Primed from 1.4 s until it fills): visages at 1.4, 13.4, 25.4, ...
    # (each the first Primed moment 10 s after the last): 5 in 60 s, 4 s each
    assert models.mov_visages(6.0, 4.0, 60.0) == (pytest.approx(20 / 60), 5)
    # a slow 20 s cycle: one a cycle once the window opens (11.2 s, then 31.2 s, ...)
    assert models.mov_visages(20.0, 4.0, 120.0) == (pytest.approx(0.2), 6)
    # buildup too slow to Prime the boss within the fight: none (probe POE2T6483's 4,000,000 s cycle counted 40% before)
    assert models.mov_visages(4002590.9, 4.0, 1800.0) == (0.0, 0)
    assert models.mov_visages(0.0, 4.0, 60.0) == (0.0, 0)


def test_moment_of_vulnerability_needs_a_primed_boss_and_hits_that_reach_the_visage():
    freeze = {"Speed": 2.0, "HitChance": 100.0, "FreezeBuildupAvg": 20.0}
    mods, notes = models.moment_of_vulnerability_model(models.Skill("Ice Nova"), freeze, True,
                                                       {"time_rip_damage_share_%": 98, "rip_from_time_immunity_duration_ms": 10000},
                                                       0.65, radius=4.0)
    period = models.immobilise_period(freeze, "Freeze")
    up, made = models.mov_visages(period, models.immobilise_duration(freeze, "Freeze"), None)
    every = models.MOV_FIGHT_CAP / made
    expected = (1 + 0.98 * up) * (1 - 0.65 / every) - 1
    assert mods and mods[0]["scale"] == pytest.approx(100 * expected, abs=1e-3) and "Primed by Freeze" in notes[0]["basis"]
    # not an Area skill nor a piercing projectile: its hits don't reach the visage (R52-3), the rows said
    mods, notes = models.moment_of_vulnerability_model(models.Skill("Spark"), freeze, False)
    assert mods == [] and "other reach rows 1:" in notes[0]["basis"]
    # nothing builds Freeze / Electrocute / Heavy Stun: never Primed, 0 (no flat rate)
    mods, notes = models.moment_of_vulnerability_model(models.Skill("Fireball"), {"Speed": 2.0, "HitChance": 100.0}, True)
    assert mods == [] and "never Primed" in notes[0]["basis"]
    # buildup too slow to Prime the boss within a 60 s fight: none
    slow = {"Speed": 1.0, "HitChance": 100.0, "FreezeBuildupAvg": 0.005}
    assert models.moment_of_vulnerability_model(models.Skill("Ice Nova"), slow, True, fight=60.0)[0] == []


def test_moment_of_vulnerability_reach_follows_area_size_and_pierce():
    # MECH103 (R52-3): the visage stands beside the boss, never on it
    assert models.mov_reach(True, 4.0, False)[0] == 1.0
    assert models.mov_reach(True, 2.0, False)[0] == 0.5
    assert models.mov_reach(True, 0.0, False)[0] == 0.5
    assert models.mov_reach(False, 0.0, True)[0] == 1.0
    assert models.mov_reach(False, 0.0, False)[0] == 0.0


def test_moment_of_vulnerability_visage_dies_to_the_hits_its_life_takes():
    # MECH103 (R52-3): a monster with its own Life; 5,000 Life against 20,000 hits: one hit kills it, 0.5 s at 2 hits/s
    assert models.mov_visage_life(5000.0, 20000.0, 2.0, 4.0) == (pytest.approx(0.5), pytest.approx(0.125), pytest.approx(
        models.mov_visage_life(5000.0, 20000.0, 2.0, 4.0)[2]))
    assert models.mov_visage_life(0.0, 20000.0, 2.0, 4.0)[:2] == (4.0, 4.0)  # (not read: its whole duration)
    assert models.mov_visage_life(100000.0, 1000.0, 2.0, 4.0)[0] == 4.0  # (100 hits outlast it)
    freeze = {"Speed": 2.0, "HitChance": 100.0, "FreezeBuildupAvg": 20.0, "AverageHit": 20000.0}
    full = models.moment_of_vulnerability_model(models.Skill("Ice Nova"), freeze, True, radius=4.0)[0][0]["scale"]
    dies = models.moment_of_vulnerability_model(models.Skill("Ice Nova"), freeze, True, radius=4.0, life=5000.0)
    # one 20,000 hit kills it: 0.98 x 0.5 s of hits shared a visage, less than its 0.65 s cast: not pressed (0)
    assert full > 0 and dies[0] == [] and "kill it" in dies[1][0]["basis"] and "isn't pressed" in dies[1][0]["basis"]
    # a sturdier visage (60,000 Life: 3 hits, 1.5 s) pays, below the whole-duration reading
    sturdy = models.moment_of_vulnerability_model(models.Skill("Ice Nova"), freeze, True, radius=4.0, life=60000.0)
    assert 0 < sturdy[0][0]["scale"] < full
    # the visage made only while visages last: lasts shortens each one, not the cycle
    assert models.mov_visages(6.0, 4.0, 60.0, lasts=1.0) == (pytest.approx(5 / 60), 5)
