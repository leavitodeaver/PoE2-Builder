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
    assert "EXTENSION" in got["how"]  # (the user's 50% was given for Cold attacks)


def test_a_chance_ailment_counts_every_strike_of_a_use():
    stats = seen(DIVINITY)
    got = boss_hits.ailments_on_you(stats)["Shock"]
    uses, _ = boss_hits.ailment_hits({**stats, "_onyou_cadence": "use", "_onyou_dodge": boss_hits.ON_YOU_DODGE},
                                     "Lightning")
    p = boss_hits.own_shock_chance(stats, DIVINITY)
    k = bosses.HIT_EVERY[DIVINITY] / bosses.HITS_EVERY[DIVINITY]
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
