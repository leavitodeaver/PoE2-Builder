"""MECHC68's tests (MECH-C): the boss walking out of the twisters' roam space (MECHD9's reader rule)."""
import math

import pytest

from builder.search import bosses, recovery


def test_twisters_extra_hits_count_only_while_the_boss_stays_in_their_roam_space():
    """MECHC68 (MECHD9's survey; the manager's reader rule, 26 Nov 06:30): Twister's extra hits a cast (the twisters
    bouncing back through the boss) x bosses.stay_share(boss, TWISTER_ROAM, D); the first hit kept; rows: the boss
    standing still (the old reading) and every move its mean length."""
    base = {"ProjectileCount": 3, "Duration": 3.0}
    still, open_row, _ = recovery.twister_hits(base, 0.4)  # (no boss given: the boss standing still)
    assert still > 1 and open_row == 1.0
    for name in (bosses.DEFAULT, "Arbiter of Ash", "Zarokh, the Temporal"):
        boss = {"_boss": name}
        head, _, how = recovery.twister_hits(base, 0.4, boss)
        share, _ = bosses.stay_share(boss, recovery.TWISTER_ROAM, 3.0)
        assert 0 < share < 1
        assert head == pytest.approx(1 + (still - 1) * share)
        assert 1 < head < still
        assert f"the boss standing still {still:.2f}" in how and "every move its mean length" in how
    # a boss that moves more leaves more of the hits behind
    calm = recovery.twister_hits(base, 0.4, {"_boss": "Xesht, We That Are One"})[0]
    busy = recovery.twister_hits(base, 0.4, {"_boss": "Zarokh, the Temporal"})[0]
    assert busy < calm
    # a longer life is a longer window: a smaller share of the extra hits
    longer = {**base, "Duration": 6.0}
    s3 = bosses.stay_share({"_boss": "Arbiter of Ash"}, recovery.TWISTER_ROAM, 3.0)[0]
    s6 = bosses.stay_share({"_boss": "Arbiter of Ash"}, recovery.TWISTER_ROAM, 6.0)[0]
    assert s6 < s3
    assert recovery.twister_hits(longer, 0.4, {"_boss": "Arbiter of Ash"})[0] == pytest.approx(
        1 + (recovery.twister_hits(longer, 0.4)[0] - 1) * s6)
    # one hit a cast stays one hit (nothing to lose)
    assert recovery.twister_hits({"ProjectileCount": 1, "Duration": 0.5}, 0.4, {"_boss": "Arbiter of Ash"})[0] == 1.0


def test_a_life_flask_is_drunk_at_most_once_a_dip_below_full_life():
    """MECHC68 (RESEARCH67 R67-3, MEDIUM-HIGH: a Life flask can't be drunk at Full Life by default): recovery_per_second
    counts at most one use a dip below full (pool_dips), as uses_per_second does; nothing taking Life off: never drunk,
    no recovery; the starting stock of a short fight no longer reads as several drinks a second."""
    from dataclasses import replace

    from builder.search import flasks

    life = {"kind": "Flask", "lifeTotal": 1000, "chargesUsed": 10, "chargesMax": 60, "duration": 3}
    # a 1.4 s fight's stock (6 uses: 4.3 a second) against 1.2 landed hits a second
    gain = replace(flasks.Charges(), fight=1.4, taken_hit=1000.0, boss_hits=1.2)
    capped = flasks.recovery_per_second(life, gain, "Life", "20% of Recovery applied Instantly")
    uses = 6 / 1.4
    old = 1000 * (0.2 * uses + 0.8 * min(uses, 1 / 3))
    assert capped == pytest.approx(1000 * (0.2 * 1.2 + 0.8 * min(1.2, 1 / 3)))
    assert capped < old
    # nothing takes Life off: never drunk
    assert flasks.recovery_per_second(life, replace(gain, taken_hit=0.0), "Life") == 0.0
    # Enduring Elixirs (drinkable at full): every use the charges pay for
    kept = replace(gain, life_effect_kept=True)
    assert flasks.recovery_per_second(life, kept, "Life", "20% of Recovery applied Instantly", own=True) == pytest.approx(old)


FURY_SKILLS = [{"name": "Savage Fury", "skillId": "SavageFuryPlayer", "group": 3,
                "levelStats": {"wild_beast_maximum_energy": 20, "wild_beast_energy_per_hit": 1}},
               {"name": "Savage Fury", "skillId": "SavageFuryActivePlayer", "group": 3,
                "levelStats": {"wild_beast_damage_+%_final": 29}}]


def test_savage_fury_reads_every_attacks_fury_and_a_form_switch_ends_it():
    """MECHC68: AUDIT41 A41-22 (Fury from every Attack: the side attacks' hitting uses add) and RESEARCH68 R68-5 (the
    headline: another form's skill ends the frenzy at its first use; "kept across forms" the row)."""
    from builder.search import models

    stats = {"Speed": 1.0, "HitChance": 100}
    form = {"Attack", "Shapeshift", "Bear"}
    alone = models.savage_fury_model(FURY_SKILLS, stats, form, True, False, None)[1][0]
    side = models.savage_fury_model(FURY_SKILLS, stats, form, True, False, None, side=(1.0, "Maul 1.00/s"))[1][0]
    assert alone["value"] == f"{10 / 30.6:.0%}" and side["value"] == f"{10 / 20.6:.0%}"
    assert "Maul 1.00/s" in side["basis"]
    # a non-Attack main skill reads its side attacks' Fury, not 0
    spell = models.savage_fury_model(FURY_SKILLS, stats, {"Spell", "UsableWhileShapeshifted"}, False, False, None,
                                     side=(2.0, "x"))[1][0]
    assert spell["value"] == f"{10 / 20.6:.0%}"
    # a Wolf skill pressed 0.5 a second ends a Bear main skill's frenzy at its first use
    up = (1 - math.exp(-0.5 * 10)) / 0.5
    switched = models.savage_fury_model(FURY_SKILLS, stats, form, True, False, None, side=(1.0, "x"),
                                        switch=(0.5, "Wolf skill"))[1][0]
    assert switched["value"] == f"{up / (up + 10 + 0.6):.0%}"
    assert f"kept across forms: up {10 / 20.6:.0%}" in switched["basis"]
