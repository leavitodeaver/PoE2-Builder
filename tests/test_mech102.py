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
