"""The boss's ailments on you per USE of its skills (MECHD7; boss_hits.ailments_on_you, R37-17's per-use rule)."""
import math

import pytest

from builder.search import boss_hits, bosses

ASH, DIVINITY, OLROTH = "Arbiter of Ash", "Arbiter of Divinity", "Olroth, Origin of the Fall"


def seen(name: str | None = None, **more) -> dict:
    stats = {"Life": 5000, "AilmentThreshold": 2500, "EvadeChance": 30, "EffectiveBlockChance": 0,
             "enemySkillTime": bosses.defence_hit_every(name or bosses.WORST), **more}
    if name:
        stats["_boss"] = name
    return stats


def test_each_preset_inflicts_only_what_its_skills_deal():
    for name in bosses.REAL:
        got = boss_hits.ailments_on_you(seen(name))
        kinds = boss_hits.type_shares(name)
        assert got["Poison"]["uptime"] == got["Electrocute"]["uptime"] == 0
        assert "checked" in got["Poison"]["how"] and "checked" in got["Electrocute"]["how"]
        assert (got["Ignite"]["uptime"] > 0) == (kinds["Fire"] > 0)
        assert (got["Bleed"]["uptime"] > 0) == (name == OLROTH)
        if kinds["Cold"] <= 0:
            assert got["Chill"]["uptime"] == got["Freeze"]["uptime"] == 0


def test_per_use_reads_below_the_defence_cadence_and_the_dodge_rows_bracket_it():
    got = boss_hits.ailments_on_you(seen(ASH))["Ignite"]
    rows = dict(got["rows"])
    assert rows["0% of the dodgeable uses dodged"] > got["uptime"] > rows["75% of the dodgeable uses dodged"]
    assert got["uptime"] < rows["the defence cadence (enemySkillTime, no dodge: the old reading)"]
    assert "one rate for every telegraphed attack" in got["how"]  # (the user, 21 Nov: one rate for all)
    assert boss_hits.ON_YOU_DODGE_HOW in got["how"]  # (MECH-C's constant read, never copied)
    assert "EXTENSION" not in got["how"]


def test_a_chance_ailment_counts_every_strike_of_a_use():
    stats = seen(DIVINITY)
    got = boss_hits.ailments_on_you(stats)["Shock"]
    red, rest = boss_hits.kind_land_parts(stats, "Lightning")
    uses = boss_hits.type_shares(DIVINITY)["Lightning"] / bosses.USE_EVERY[DIVINITY] * rest * (1 - boss_hits.ON_YOU_DODGE)
    p = boss_hits.own_shock_chance(stats, DIVINITY)
    k = bosses.USE_EVERY[DIVINITY] / bosses.HITS_EVERY[DIVINITY]  # (MECHD8: USE_EVERY)
    assert k > 1
    assert got["rate"] == pytest.approx(uses * (1 - (1 - p) ** k))
    assert got["uptime"] == pytest.approx(1 - math.exp(-got["rate"] * boss_hits.SHOCK_ON_PLAYER_SECONDS))
    assert got["effect"] == boss_hits.SHOCK_ON_PLAYER_INC


def test_avoidance_and_immunity_windows_lower_it():
    base = boss_hits.ailment_uptime(seen(ASH), "Ignite")[0]
    assert boss_hits.ailment_uptime(seen(ASH, IgniteAvoidChance=100), "Ignite")[0] == 0
    lines = ["You cannot be Ignited for 4 seconds after being Ignited"]
    assert 0 < boss_hits.ailment_uptime(seen(ASH), "Ignite", lines)[0] < base


def test_the_damaging_ailments_carry_their_damage_a_second():
    got = boss_hits.ailments_on_you(seen(OLROTH))
    assert got["Bleed"]["dps"] == pytest.approx(boss_hits.BLEED_SHARE_PER_SECOND * boss_hits.mean_hits(OLROTH)["Physical"])
    ash = boss_hits.ailments_on_you(seen(ASH))["Ignite"]
    assert ash["dps"] == pytest.approx(boss_hits.IGNITE_SHARE_PER_SECOND * boss_hits.mean_hits(ASH)["Fire"])
    assert set(boss_hits.DAMAGING_AILMENTS) == {"Ignite", "Bleed", "Poison"}


def test_the_worst_case_reads_the_lowest_real_preset_for_a_gain_and_the_highest_for_a_harm():
    gain = boss_hits.ailments_on_you(seen())
    assert all(v["uptime"] == 0 for v in gain.values())  # (the King, Zarokh and the Bodach inflict none)
    harm = boss_hits.ailments_on_you(seen(), worst="highest")
    assert harm["Bleed"]["boss"] == OLROTH and harm["Bleed"]["uptime"] > 0
    assert harm["Ignite"]["boss"] in bosses.REAL and harm["Ignite"]["uptime"] > 0
    for ailment in ("Ignite", "Shock", "Chill"):
        best = max(boss_hits.ailment_uptime(read, ailment)[0] for _, read in boss_hits.real_readings(seen()))
        assert harm[ailment]["uptime"] == pytest.approx(best)


def test_the_grounds_that_inflict_an_ailment_are_named_not_counted():
    got = boss_hits.ailments_on_you(seen(ASH))["Chill"]
    assert got["uptime"] == 0 and "Chilled Ground" in got["ground"]


def test_the_ailment_stat_tables_follow_the_game_files():
    # (game 26650308, scratch/mechd7/ailscan.py) phase 2 has no MeleeAtAnimationSpeedFire / Lightning
    assert "Atziri, the Fell Serpent" not in boss_hits.IGNITE_INCREASE
    assert "Atziri, the Fell Serpent" not in boss_hits.SHOCK_FACTORS
    assert boss_hits.SHOCK_FACTORS["Vessel of Kulemak"] == (1.33, 2.0, 2.0)
    assert boss_hits.SHOCK_FACTORS["The Raven Trickster"] == (3.0,)
    assert boss_hits.FREEZE_FACTORS[OLROTH] == (3.0, 1.25)


def test_the_add_waves_give_each_presets_timing():
    assert bosses.add_waves("Arbiter of Ash") == []
    king = bosses.add_waves("The King in the Mists")
    assert [(every, wave, share) for every, wave, share, _, _ in king] == [(25.0, 1, 0.43)]
    assert bosses.kills_per_second("The King in the Mists")[0] == pytest.approx(0.43 / 25)
    assert bosses.add_waves("Atziri, the Red Queen")[0][:2] == (45.0, 1)
    assert bosses.add_waves(bosses.WORST) == []


def test_each_presets_life_and_energy_shield_under_both_readings():
    for name in bosses.PRESETS:
        life, es, _ = bosses.pool(name)
        assert life / (life + es) == pytest.approx(bosses.life_share_of_pool(name))
        life2, es2, _ = bosses.pool(name, "poe2db")
        assert es2 == pytest.approx(es) and life2 == bosses.PRESETS[name].life
    life, es, _ = bosses.pool("Atziri, the Red Queen")
    assert round(life, -4) == 4_750_000 and round(es, -4) == 400_000
    assert bosses.pool("Arbiter of Ash")[1] == 0


def test_a_preset_that_cant_be_culled_gates_every_culling_model_to_nothing(monkeypatch):
    assert all(bosses.can_be_culled(n)[0] for n in bosses.PRESETS)  # (checked: no preset carries the flag)
    ash = bosses.PRESETS[ASH]
    assert bosses.cull_threshold(ASH) == 5.0
    monkeypatch.setitem(bosses.PRESETS, ASH, ash._replace(cullable=False))
    assert bosses.cull_threshold(ASH) == 0.0 and bosses.config_vars(ASH)["cullingStrikeUniqueThreshold"] == 0.0
    assert "can't be Culled" in bosses.can_be_culled(ASH)[1]


def test_a_shielded_presets_cull_reads_the_shield_assumption_with_its_rows():
    rows = bosses.cull_rows("Atziri, the Red Queen")
    assert "Energy Shield down" in rows[0][0] and rows[0][1] == bosses.cull_threshold("Atziri, the Red Queen") < 5
    assert rows[1][1] == rows[0][1] and rows[2][1] == bosses.CULL_UNIQUE
    assert bosses.cull_rows(ASH) == [("no Energy Shield: 5% of its Life", 5.0)]
    assert "Energy Shield down" in bosses.note("Atziri, the Red Queen")["basis"]
    assert all(bosses.can_be_culled(n)[0] for n in bosses.PRESETS)  # (R40-8: none marked uncullable)


def test_red_flash_hits_are_avoided_in_the_headline_and_land_in_a_row():
    stats = seen(OLROTH)  # (its Ground Slam is red-flash and half Cold)
    red, _ = boss_hits.kind_land_parts(stats, "Cold")
    assert red > 0
    got = boss_hits.ailments_on_you(stats)["Chill"]
    rows = dict(got["rows"])
    assert rows["red-flash hits land (a tanky build that stands in them)"] > got["uptime"] > 0
    assert "avoided by moving out" in got["how"]


def test_red_flash_uses_and_the_time_they_cost():
    rate, how = boss_hits.red_flash_rate(ASH)
    # (MECHD8: per USE from the cadence model, not the flagged-skill share; the old reading is the "skills" row)
    assert rate == pytest.approx(boss_hits.red_use_share(ASH) / bosses.USE_EVERY[ASH]) and "upper bound" in how
    assert rate * 60 == pytest.approx(boss_hits.RED_FLASH_USES[ASH][0], rel=0.01)
    una, _, total, _ = boss_hits.RED_FLASH[ASH]
    old = boss_hits.red_flash_rate(ASH, "skills")[0]
    assert old == pytest.approx(una / total / bosses.HIT_EVERY[ASH]) and rate < old / 2
    video = boss_hits.red_flash_rate(ASH, "video")[0]
    assert video * 60 == pytest.approx(4.5 / 261 * 60) and video < rate  # (R41-2: about 1 a minute)
    assert boss_hits.red_flash_time_share(ASH, rate_reading="skills")[0] == pytest.approx(old * 0.75)
    assert boss_hits.red_use_share(ASH) == pytest.approx(3.69 / 26.7)
    share, how = boss_hits.red_flash_time_share(ASH)
    assert share == pytest.approx(rate * boss_hits.RED_FLASH_AVOID_SECONDS) and "LOW" in how
    assert boss_hits.RED_FLASH_AVOID_SECONDS == 0.75  # (DodgeRollPlayer CastTime 750 ms, game 26650308)
    worst = boss_hits.red_flash_rate(bosses.WORST)[0]
    assert worst == pytest.approx(max(boss_hits.red_flash_rate(n)[0] for n in bosses.REAL))
    slowed = boss_hits.red_flash_rate({"_boss": ASH, "enemySkillTime": 2 * bosses.defence_hit_every(ASH)})[0]
    assert slowed == pytest.approx(rate / 2)


def test_the_videos_rows_and_the_landed_hits_cross_check():
    got = boss_hits.ailments_on_you(seen(ASH))["Ignite"]
    rows = dict(got["rows"])
    video = rows["the videos' all-in landing share, 8 of 29 (R41-1; your evade / block not stacked on it)"]
    alone = boss_hits._ailment_on_you(seen(ASH), "Ignite", [], "use", 0.0, all_in=boss_hits.VIDEO_LANDED_SHARE)
    assert video == alone["uptime"] > 0 and "not stacked" in alone["how"]
    no_evade = boss_hits._ailment_on_you(seen(ASH, EvadeChance=0), "Ignite", [], "use", 0.0,
                                         all_in=boss_hits.VIDEO_LANDED_SHARE)
    assert no_evade["uptime"] == pytest.approx(video)  # (evasion isn't counted twice)
    per_min, how = boss_hits.landed_cross_check(seen(ASH))
    red = boss_hits.red_use_share(ASH)  # (MECHD8: the per-use red share, as the headline avoids)
    assert per_min == pytest.approx(60 / bosses.USE_EVERY[ASH] * (1 - red) * 0.7 * (1 - boss_hits.ON_YOU_DODGE))
    assert "videos 6 (4.5-9.5" in how


def test_red_flash_tanks_row_and_the_video_evidence():
    assert boss_hits.red_flash_time_share(ASH, "tanks")[0] == 0
    assert "R41-2" in boss_hits.red_flash_time_share(ASH)[1]


def test_atziris_video_es_reading_and_the_verisium_web_row():
    life, es, how = bosses.pool("Atziri, the Red Queen", "video")
    assert es / life == pytest.approx(0.135) and "R42-1" in how
    assert bosses.pool(ASH, "video")[1] == 0
    assert bosses.kills_per_second("The Aberration")[0] == 0
    assert bosses.kill_rows("The Aberration")[0][1] == pytest.approx(1 / 30)


def test_the_kings_second_form_totems_are_a_row_not_the_headline():
    king = "The King in the Mists"
    (what, rate), = bosses.kill_rows(king)
    assert "second form" in what and rate == pytest.approx(0.57 / 25)
    assert [k.share for k in bosses.PRESETS[king].kills] == [0.43]  # (the headline unchanged)


def test_use_every_counts_a_melee_combo_once():
    """MECHD8 (the manager 08:22 21 Nov): the per-use readers read USE_EVERY, HIT_EVERY's "before the strikes" values."""
    assert bosses.USE_EVERY[ASH] == 2.25 and bosses.USE_EVERY[OLROTH] == 2.23
    assert bosses.USE_EVERY["Atziri, the Red Queen"] == 1.13
    assert set(bosses.USE_EVERY) == set(bosses.HIT_EVERY)
    assert all(bosses.USE_EVERY[n] >= bosses.HIT_EVERY[n] >= bosses.HITS_EVERY[n] for n in bosses.REAL)
    assert bosses.USE_EVERY[bosses.WORST] == max(bosses.USE_EVERY[n] for n in bosses.REAL)
    k = boss_hits.strikes_per_use(OLROTH)
    assert k == pytest.approx(2.23 / bosses.HITS_EVERY[OLROTH])  # (uses x strikes a use = the strike rate)
    chill = boss_hits.ailments_on_you(seen(OLROTH))["Chill"]
    assert "USE_EVERY" in chill["how"]
