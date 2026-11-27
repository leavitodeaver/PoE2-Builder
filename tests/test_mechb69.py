"""MECHB69: Mirror of Refraction's copies' returns from the geometry (RESEARCH64 R64-3 / R64-10)."""
import pytest

from builder.search import models


@pytest.mark.parametrize("radius, metres, out, back, share", [
    (0.4, 0.9, 0.39, 0.90, 0.15), (0.4, 1.9, 0.37, 0.47, 0.07), (0.5, 1.0, 0.47, 0.94, 0.17),
    (0.6, 1.1, 0.54, 0.97, 0.18), (1.0, 1.5, 0.76, 1.00, 0.23), (0.3, 0.8, 0.31, 0.82, 0.12)])
def test_mirror_geometry_matches_r64_10s_table(radius, metres, out, back, share):
    got = models.mirror_nova_geometry(radius, metres, 4.0, 7)
    assert got == pytest.approx((out, back, share), abs=0.015)


def test_mirror_returns_replace_the_outgoing_count():
    """The copies' returns counted at q (the returns' own crossing chance) in place of p (the outgoing one)."""
    frost = models.Skill("Frostbolt", group=1, spell=True)
    p = min(1.0, models.nova_unaimed_hits(7, 0.4, 0.9))
    big_m, r = 1.5, 1.0  # a mirror more of +150%, a return more of +100%
    mods, notes = models.mirror_return_model(frost, 100.0, 150.0, 7, (0.4, "test"), 0.8, 0.9, "test")
    q = models.mirror_nova_geometry(0.4, 0.9, 4.0, 7)[1]
    want = (1 + r + big_m + big_m / p * q * r) / ((1 + r) * (1 + big_m))
    assert mods[0]["value"] == pytest.approx(100 * (want - 1), abs=0.01)
    assert "R64-3" in notes[0]["basis"] and "retired flat half" in notes[0]["basis"]
    # at range the returns cross less often than the outgoing copies hit: below x1 (an overcount removed)
    far, _ = models.mirror_return_model(frost, 100.0, 150.0, 7, (0.3, "test"), 0.6, 5.0, "test")
    assert far and far[0]["value"] < 0
    assert models.mirror_return_model(frost, 0.0, 150.0, 7, (0.4, "t"), 0.8, 0.9, "t") == ([], [])
    assert models.mirror_return_model(frost, 100.0, 0.0, 7, (0.4, "t"), 0.8, 0.9, "t") == ([], [])


def test_explosive_demise_attack_hit_and_the_scaled_life_row():
    """RESEARCH64 R64-6: the hit scored as an Attack (300%); (MECHB70, R65-1) the 8% of Life part scaled like the hit's
    base in the headline, unscaled a row; the gem's quality adds 0.2% a point; the Life threshold by gem level."""
    spark = models.Skill("Spark", group=1, spell=True)
    added, _, notes = models.explosive_demise_model(0.25, 1.0, 1000.0, 5000.0, "test", 50.0, spark, {0.0: 1.0, 0.25: 2.0},
                                                    2.0)
    assert added == pytest.approx(0.25 * (1000 + 0.08 * 5000 * 2.0))  # (MECHB70, R65-1: scaled is the headline)
    basis = notes[0]["basis"]
    assert "R64-6" in basis and "scaled like the hit's base (x2.00" in basis and f"+{0.25 * (1000 + 200):,.1f} DPS" in basis
    q20, _, _ = models.explosive_demise_model(0.25, 1.0, 1000.0, 5000.0, "test", 50.0, spark, {}, 2.0, pct=12.0)
    assert q20 == pytest.approx(0.25 * (1000 + 0.12 * 5000 * 2.0))  # (A40-51: 12% at 20% quality)
    assert models.demise_threshold(20) == 1096 and models.demise_threshold(1) == 30 and models.demise_threshold(99) == 6412
    _, _, plain = models.explosive_demise_model(0.25, 1.0, 1000.0, 5000.0, "test", 50.0, spark, {})
    assert "scaled like the hit's base" not in plain[0]["basis"]


def test_cost_cap_counts_the_starting_pool_over_the_fight():
    """MECHC66 via the manager (02:30): the starting pool pays costs over the fight (the boss's pool / the DPS)."""
    assert models.cost_pace(390.0, 1000.0) == pytest.approx(0.39)
    # spend 1,000/s, income 390/s, 2,000 Mana; full-pace DPS / boss pool = 0.25 fights a second (a 4 s fight at x1)
    s = models.cost_pace(390.0, 1000.0, 2000.0, 0.25)
    assert s == pytest.approx(390 / (1000 - 500))
    fight = 1 / (0.25 * s)  # the fight at that pace: its spend = income + the pool over the fight
    assert s * 1000 == pytest.approx(390 + 2000 / fight)
    assert models.cost_pace(390.0, 1000.0, 4000.0, 0.25) == 1.0  # the pool lasts the whole fight
    scale, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                     (0.25, "test fight"))
    assert scale == pytest.approx(0.78) and "starting 2,000 Mana" in row["basis"] and "x0.390" in row["basis"]
    scale, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 4000.0},
                                     (0.25, "test fight"))
    assert scale == 1.0 and "lasts the fight" in row["play"]
    assert models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True)[0] == pytest.approx(0.39)


def test_cost_cap_shows_the_steady_state_and_refills_while_the_boss_is_away():
    """MECHB70 (the manager's check of item 4): the steady-state row beside the headline and in the play line; a named
    boss's off-field phases (bosses.absent) refill the pool by its income, at most its maximum."""
    scale, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                     (0.25, "test fight"))
    assert "steady state, no starting pool x0.390" in row["value"] and "Without its starting Mana" in row["play"]
    # a 1 s break refills 390 (under the 2,000 maximum); a 10 s break at most the 2,000
    one, row = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                   (0.25, "test fight"), (1.0,))
    assert one == pytest.approx(models.cost_pace(390.0, 1000.0, 2390.0, 0.25)) and "off the field" in row["basis"]
    ten, _ = models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0},
                                 (0.25, "test fight"), (10.0,))
    assert ten == pytest.approx(models.cost_pace(390.0, 1000.0, 4000.0, 0.25))
    # no fight read: no refill either (the steady state)
    assert models.mana_cap_row({"Mana": (390.0, "regen", 1000.0)}, "Spark", [], True, {"Mana": 2000.0}, None,
                               (10.0,))[0] == pytest.approx(0.39)


def test_bone_cage_main_ring_at_the_target_counts_the_boss_moving():
    """AUDIT39 A39-1: the main ring round the boss (Astral Projection) is crossed when the boss moves in its life."""
    import math
    from builder.search import bosses
    cage = models.Skill("Bone Cage", group=1, spell=True)
    rate, most, how = models.boss_moves({"_boss": bosses.WORST})
    assert rate == pytest.approx(bosses.relocations({"_boss": bosses.WORST}, "headline", True)[0]) and "fewest" in how
    assert rate > 0 and most > rate
    stats = {"AreaOfEffectRadius": 18, "Duration": 1.6, "_boss": bosses.WORST}
    mods, notes = models.bone_cage_ring_model(cage, stats, 0.3, "test", "Astral Projection", 1, math.sqrt(0.8))
    moved = 1 - math.exp(-rate * 1.6)
    standing, _ = models.ring_nova_hits(1.8, 0.3, 1, math.sqrt(0.8), True)
    assert notes[0]["value"] == f"x{standing + moved:.2f}" and "a standing boss" in notes[0]["basis"]
    assert models.boss_moves({"_boss": bosses.WORST}, 1000.0)[0] == 0.0  # (a move shorter than the way out: none)


def test_infernal_legion_gate_counts_every_minion_near_the_boss():
    """AUDIT39 A39-2: one Flammability source per minion near the boss, the strongest alone a row."""
    brute = ("Skeletal Brute Minion", 5000.0, 0.1, {"melee": True, "range": 16.0, "radius": 20, "minion": "Brute"}, 3)
    hound = (models.INFERNAL_HOUND, 2000.0, 0.2, {"melee": True, "range": 10.0, "radius": 25, "minion": "Hound"})
    seen = []

    def gate(every):
        seen.append(every)
        return (min(1.0, 0.2 * sum(n for _, n in every)), "test gate")
    added, note = models.infernal_legion_model([brute, hound], 30.0, 0.0, gate)
    assert seen[0] == [(500.0, 3.0), (400.0, 1.0)] and seen[1] == [(500.0, 1.0)]
    full, _ = models.infernal_legion_model([brute, hound], 30.0, 0.0)
    assert added == pytest.approx(full * 0.8) and "A39-2" in note["basis"] and "alone x0.20" in note["basis"]


def test_infernal_legion_ignited_share_takes_the_gate():
    """MECHB69's find: the boss Ignited by the minions only while the 50% Flammability gate holds (an OVERCOUNT at 1.0)."""
    brute = models.Skill("Summon Skeletal Brute", group=1, summon={"persistent": True, "limit": 2, "range": 10, "melee": True},
                         support_stats={models.LEGION_STAT: 10})
    assert models.infernal_legion_ignited([brute], None, {})[0] == 1.0
    share, how = models.infernal_legion_ignited([brute], None, {}, gate=0.4)
    assert share == 0.4 and "50% Flammability" in how


def test_tempest_bell_reads_freeze_and_electrocute_as_their_types():
    """RESEARCH64 R64-8: Freeze and Electrocute on the Bell count as Cold / Lightning; from the next Hit after the
    buildup reaches 100% (h = ceil(100 / buildup))."""
    ailments = {"threshold": 6357, "chance": {"Ignite": 0, "Chill": 0, "Shock": 0, "FreezeBuildup": 40.0,
                                              "ElectrocuteBuildup": 0.0},
                "lasts": {}, "extra": {"Fire": 0, "Cold": 100.0, "Lightning": 100.0}}
    head, row, words = models.bell_ailment_bonus(ailments, 10, 0.3)
    # 3 Hits to freeze: shockwaves 4..10 carry Cold (7 of 10); the row (the applying Hit's own) 8 of 10
    assert head == pytest.approx(70.0) and row == pytest.approx(80.0)
    assert "Freeze 40.0% a Hit, on the Bell after 3 Hit(s)" in words and "Lightning" not in words
    plain = {**ailments, "chance": {"Chill": 0}}
    assert models.bell_ailment_bonus(plain, 10, 0.3)[0] == 0.0


def test_ice_shards_limit_is_in_the_headline_past_its_pace():
    """RESEARCH64 R64-8: at the 16-shard limit the oldest disappears unexploded: past 16 / 1.5 attacks a second the
    shards' hits take x(10.7 / rate) in the headline."""
    shards = models.Skill("Ice Shards", group=1, attack=True)
    assert models.ice_shards_limit_mods(shards, 10.0) == []
    mods = models.ice_shards_limit_mods(shards, 16.0)
    assert mods and mods[0]["value"] == pytest.approx(100 * (16 / 1.5 / 16 - 1), abs=0.01)
    basis = models.ice_shards_note(shards, 16.0)[0]["basis"]
    assert "R64-8" in basis and "UNVERIFIED, R61-11" not in basis


def test_melee_projectiles_line_shows_the_other_reading_as_a_row():
    """AUDIT39 A39-57: a melee skill with no projectile gains none (UNVERIFIED): the other reading a labelled row."""
    note = models.melee_projectiles_note(models.Skill("Maul", 1, attack=True), ["Melee Attacks fire 2 additional Projectiles"],
                                         [], 0.0)[0]
    assert note["value"] == "x1.000" and "A39-57" in note["basis"] and "x2.000" in note["basis"]


def test_explosive_demise_from_arsonists_that_deal_no_damage_counts_nothing():
    """Probe POE2T9088 (example build 6): Brutus' Brain on the Arsonists, PoB's Attack hit 0 with a weapon base: the
    Life part (the Arsonist's damage too) is 0, the as-if row kept."""
    spark = models.Skill("Spark", group=1, spell=True)
    added, _, notes = models.explosive_demise_model(0.25, 1.0, 0.0, 5000.0, "test", 50.0, spark, {}, 0.0, no_damage=True)
    assert added == 0.0 and "deal no damage" in notes[0]["basis"] and f"+{0.25 * 200:,.1f}" in notes[0]["basis"]
