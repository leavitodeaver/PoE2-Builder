"""MECHC68's tests (MECH-C): the boss walking out of the twisters' roam space (MECHD9's reader rule)."""
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
