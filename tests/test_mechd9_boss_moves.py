"""MECHD9: the boss presets' relocations (Ice Shards' armed bonus, Bone Cage's touch), where adds die (Herald of
Plague's spread), mid-fight flask refills (Napuatzi, R63-5) and the boss level field."""
from builder.search import bosses


def test_relocation_ceiling_counts_each_own_move_off_cooldown():
    # Olroth: three dashes, 12 s each, separate timers: 15 a minute; reach up to (23 + 40) / 10 m
    rate, metres, how = bosses.relocations({"_boss": "Olroth, Origin of the Fall"}, "skills")
    assert abs(rate - 15.0 / 60) < 1e-9 and abs(metres - 6.3) < 1e-9
    assert "UPPER BOUND" in how
    # the King's dashes and teleports are its first form's only (43% of the fight)
    rate, _, _ = bosses.relocations({"_boss": "The King in the Mists"}, "skills")
    assert abs(rate - 0.43 * 37.5 / 60) < 1e-3


def test_relocation_headline_is_the_video_tally_or_zero_never_the_ceiling():
    for name in bosses.REAL:
        rate, metres, how = bosses.relocations({"_boss": name})
        video = bosses.RELOCATE_VIDEO.get(name)
        if video:
            assert abs(rate - video[0] / 60) < 1e-9 and metres == video[1]
        else:
            assert rate == 0.0 and "no video tally" in how
        assert rate <= bosses.relocations({"_boss": name}, "skills")[0] + 1e-9 or video


def test_worst_case_relocations_read_the_fewest_for_a_gain_and_the_most_for_a_harm():
    worst = {"_boss": bosses.WORST}
    rates = [bosses.relocations({"_boss": n}, "skills")[0] for n in bosses.REAL]
    assert bosses.relocations(worst, "skills", gain=True)[0] == min(rates)
    assert bosses.relocations(worst, "skills", gain=False)[0] == max(rates)


def test_adds_near_reads_zero_without_placement_data_and_the_lower_point_with_it(monkeypatch):
    rate, share, how = bosses.adds_near("Arbiter of Ash")
    assert (rate, share) == (0.0, 0.0) and "no killable adds" in how
    king = bosses.PRESETS["The King in the Mists"]
    if not king.kills[0].near:
        rate, share, how = bosses.adds_near("The King in the Mists")
        assert rate == 0.0 and "not counted" in how
    placed = king._replace(kills=(king.kills[0]._replace(near=((1.0, 0.2), (3.0, 0.5)), near_how="test"),))
    monkeypatch.setitem(bosses.PRESETS, "The King in the Mists", placed)
    rate, share, _ = bosses.adds_near("The King in the Mists", 1.8)
    assert share == 0.2  # the 1.0 m point: no interpolation upward
    assert abs(rate - 0.2 * placed.kills[0].share / placed.kills[0].every) < 1e-12


def test_no_preset_refills_flasks_mid_fight():
    for name in (*bosses.REAL, bosses.WORST):
        count, share, how = bosses.flask_refills({"_boss": name})
        assert (count, share) == (0, 0.0) and "Napuatzi" in how


def test_every_preset_is_at_level_82():
    assert all(bosses.PRESETS[n].level == 82 for n in (*bosses.REAL, bosses.WORST))


def test_video_tallies_feed_the_headline_and_the_still_share():
    rate, metres, how = bosses.relocations({"_boss": "Arbiter of Ash"})
    assert abs(rate - 8.0 / 60) < 1e-9 and metres == 4.0 and "fight videos" in how
    assert bosses.still_share({"_boss": "Arbiter of Ash"})[0] == 0.55
    assert bosses.still_share({"_boss": "The Bodach"})[0] is None
    assert bosses.relocations({"_boss": "The Bodach"})[0] == 0.0  # not tallied: 0, the ceiling a row
    # the worst case reads the fewest for a gain: an untallied preset's 0
    assert bosses.relocations({"_boss": bosses.WORST})[0] == 0.0
