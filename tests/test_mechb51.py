"""MECHB51: the dodge share read from the user's one rate; ..."""
from __future__ import annotations

import pytest

from builder.search import assumptions, boss_hits, models


def test_the_dodge_share_is_the_users_one_rate(monkeypatch):
    """The user's ruling (21 Nov 05:20, "2 in 3, the videos"): one rate for every telegraphed attack. Volcanic Steps' row,
    the channel wind-up and Corpsewade's row read boss_hits.ON_YOU_DODGE, never a copy, with rows at 1/2 and 3/4."""
    assert assumptions.dodge_share()[0] == boss_hits.ON_YOU_DODGE
    monkeypatch.setattr(boss_hits, "ON_YOU_DODGE", 2 / 3)
    monkeypatch.setattr(boss_hits, "ON_YOU_DODGE_HOW", "the user's rate, 21 Nov (videos)")
    share, how = assumptions.dodge_share()
    assert share == pytest.approx(2 / 3) and how == "the user's rate, 21 Nov (videos)"
    fotm = models.Skill("Fury of the Mountain", attack=True, melee=True)
    mods, notes = models.channel_windup_model(fotm, 1.25, 4.0, 2.0, share, dodge_how=how)
    assert mods[0]["value"] == pytest.approx(-100 * (2 / 3) / 4.0 * 0.36 / 1.25, abs=0.01)
    basis = notes[0]["basis"]
    assert "67% of the boss's attacks (the user's rate, 21 Nov (videos)" in basis
    assert "50% dodged (a row): x0.964" in basis and "75% dodged (a row): x0.946" in basis
    assert "a row): " not in models.dodge_rows(0.5, str).split("75%")[0]  # (the headline's own share is no row)
    line = "Trigger Decompose every 1.2 metres travelled"
    note = models.decompose_travel_note([line], 5.0, 2.0, 4.6, share, how)[0]["basis"]
    assert "1.28 Decompose/s" in note and "50% dodged (a row): 0.96/s" in note and "75% dodged (a row): 1.44/s" in note


def test_the_bear_stance_rotation_is_chosen_from_the_build():
    """R41-3: Maul, Ferocious Roar and Walking Calamity leave the bear standing. Without them every Slam pays its +0.4 s
    (as MECHB50's model); with Maul the search may play "Maul then Furious Slam" (Maul's time and hit per Slam, no added
    time) when it scores higher; a Roar on cooldown skips it on the uses straight after; Walking Calamity chains."""
    slam = models.Skill("Furious Slam", 1, attack=True, melee=True)
    rate = {"speed": 1.0, "speedMod": 1.0, "TotalDPS": 1000.0}  # (1.0 s a use from standing, 1000 a hit)
    mods, notes = models.bear_stance_model(slam, 1.0, rate=rate)
    assert [(m["name"], m["value"]) for m in mods] == [("Speed", pytest.approx(0.4))]
    assert "without it" in notes[0]["label"] and "x1.000" == notes[0]["value"]
    # Maul 0.5 s a use, 600 a hit: (1000 + 600) / 1.5 = 1067 vs 1000 / 1.4 = 714
    maul = {"name": "Maul", "speed": 2.0, "TotalDPS": 1200.0, "full": False}
    mods, notes = models.bear_stance_model(slam, 1.0, rate=rate, standers=[maul])
    assert {m["name"] for m in mods} == {"TotalAttackTime", "DPS"} and "Maul then Furious Slam" in notes[0]["label"]
    assert notes[0]["value"] == "x1.493" and "without it: every use" in notes[0]["basis"]
    assert "Play Maul then Furious Slam" in notes[0]["play"]
    # the next pass: PoB's speed and DPS carry this model's own mods, taken back out first (the same answer)
    again = models.bear_stance_model(slam, 1.0, rate={"speed": 1 / 1.5, "speedMod": 1.0, "TotalDPS": 1600 / 1.5},
                                     standers=[maul], prev=mods)
    assert again[1][0]["value"] == "x1.493"
    # Maul already in Full DPS: its hit is not added again; then Maul's time alone loses to the plain play
    mods, notes = models.bear_stance_model(slam, 1.0, rate=rate, standers=[{**maul, "full": True}])
    assert "without it" in notes[0]["label"]
    roar = {"name": "Ferocious Roar", "speed": 0.1, "TotalDPS": 0.0, "full": False}  # (one Roar every 10 s)
    mods, notes = models.bear_stance_model(slam, 1.0, rate=rate, standers=[roar])
    assert mods[0]["value"] == pytest.approx(0.4 * (1 - 0.14)) and "Ferocious Roar before it" in notes[0]["label"]
    wc = models.Skill("Walking Calamity", 1, attack=True, melee=True)
    mods, notes = models.bear_stance_model(wc, 1.0, rate=rate, dodges=0.25)
    assert mods[0]["value"] == pytest.approx(0.3 * 0.25 * 1.3) and "then Walking Calamity" in notes[0]["label"]
    assert models.bear_stance_model(models.Skill("Boneshatter", 1, attack=True), 1.0, rate=rate) == ([], [])


def test_a_leap_takes_longer_the_further_it_goes():
    """R42-14: 'Up to +0.5 s to Total Attack Time based on distance travelled' (Leap Slam), full at 3 m (linear, LOW);
    beside the boss a 0.5 m hop (rows 0 and 1 m); from further off the build's distance."""
    leap = models.Skill("Leap Slam", 1, attack=True, melee=True)
    mods, notes = models.leap_time_model(leap, 2.0, 2.0, 1.0)
    assert mods[0]["name"] == "TotalAttackTime" and mods[0]["value"] == pytest.approx(0.5 / 6, abs=1e-3)
    assert notes[0]["confidence"] == "LOW" and "0 m x1.000" in notes[0]["basis"] and "1 m x0.857" in notes[0]["basis"]
    mods, _ = models.leap_time_model(models.Skill("Molten Crash", 1, attack=True, melee=True), 6.0, 2.0)
    assert mods[0]["value"] == pytest.approx(0.3)
    assert models.leap_time_model(models.Skill("Boneshatter", 1, attack=True), 2.0, 2.0) == ([], [])
    assert models.leap_time_model(models.Skill("Leap Slam", 1, attack=True, triggered=True), 2.0, 2.0) == ([], [])


# ---- Spectre / Tamed Companion steps 2-3 (MECHB50's drafts; the manager's R38-9..11) ----
SAMPLE = '''minions["Metadata/Monsters/A/Placed"] = {
	name = "Placed One",
	monsterCategory = "Humanoid",
	spawnLocation = {
		"The Grelwood (Act 1)",
	},
}

minions["Metadata/Monsters/A/DevOnly"] = {
	name = "Dev Only",
	monsterCategory = "Beast",
	spawnLocation = {
		"Design (Lite) (Act 3)",
	},
}

minions["Metadata/Monsters/LeagueExpeditionNew/X"] = {
	name = "Expedition One",
	monsterCategory = "Beast",
	spawnLocation = {
	},
}

minions["Metadata/Monsters/LeagueDelirium/Y"] = {
	name = "Delirium One",
	monsterCategory = "Demon",
	spawnLocation = {
	},
}

minions["Metadata/Monsters/Quadrilla/QuadrillaBossMinion1"] = {
	name = "Mighty Silverfist",
	monsterCategory = "Beast",
	spawnLocation = {
		"The Grelwood (Act 1)",
	},
}
'''


def test_the_monsters_offered_follow_the_research():
    cat = models.monster_catalog(SAMPLE)
    assert cat["Metadata/Monsters/A/Placed"]["source"] == "placed"
    assert cat["Metadata/Monsters/A/DevOnly"]["source"] == "unknown"  # (its one place a developer area)
    assert cat["Metadata/Monsters/LeagueExpeditionNew/X"]["source"] == "Expedition"
    assert cat["Metadata/Monsters/LeagueDelirium/Y"]["confidence"] == "LOW"
    spectres = models.monster_candidates("spectreList", catalog=cat)
    assert "Metadata/Monsters/A/DevOnly" not in spectres and "Metadata/Monsters/LeagueDelirium/Y" in spectres
    assert "Metadata/Monsters/Quadrilla/QuadrillaBossMinion1" not in spectres  # (a Unique beast's tamed form)
    beasts = models.monster_candidates("beastList", catalog=cat)
    assert beasts == ["Metadata/Monsters/LeagueExpeditionNew/X"]  # (Beasts only; the Unique only with The Natural Order)
    assert "Metadata/Monsters/Quadrilla/QuadrillaBossMinion1" in models.monster_candidates("beastList", True, cat)
    assert "UNVERIFIED" in models.monster_source_label("Metadata/Monsters/A/DevOnly", cat)[0]


def test_pobs_monster_list_gives_the_researchs_counts():
    """R38-9: 451 placed + 63 from core mechanics offered, 105 held out (PoB's Data/Spectres.lua: 619 entries, two ids
    twice (CenobiteBloater, GoreCharger), so 617 monsters and 449 placed)."""
    cat = models.monster_catalog()
    sources = [m["source"] for m in cat.values()]
    assert len(cat) == 617 and sources.count("placed") == 449 and sources.count("unknown") == 105
    assert len(sources) - sources.count("placed") - sources.count("unknown") == 63
    assert len([m for m in cat.values() if m["unique"]]) == len(models.UNIQUE_BEASTS) == 31
    assert len(models.monster_candidates("spectreList")) == 510  # (the 2 placed Unique beasts are tamed forms)
    assert len(models.monster_candidates("beastList")) == 141 and len(models.monster_candidates("beastList", True)) == 172


def test_the_best_monster_within_the_spirit_left_is_chosen():
    rates = [{"id": "Metadata/Monsters/A/Placed", "name": "Placed One", "spiritFlat": 90, "skills": [
                {"index": 1, "name": "Basic Attack", "TotalDPS": 100.0}, {"index": 2, "name": "Slam", "TotalDPS": 300.0}]},
             {"id": "Metadata/Monsters/LeagueExpeditionNew/X", "name": "Expedition One", "spiritFlat": 30, "skills": [
                {"index": 1, "name": "Basic Attack", "TotalDPS": 150.0}]}]
    cat = models.monster_catalog(SAMPLE)
    mid, skill, note = models.monster_best(rates, 100.0, 100.0, "spectreList", cat)
    assert (mid, skill) == ("Metadata/Monsters/A/Placed", 2) and "basic attack only: 100.0" in note["basis"]
    assert note["confidence"] == "LOW" and "R38-10" in note["basis"]
    mid, skill, _ = models.monster_best(rates, 100.0, 50.0, "spectreList", cat)  # (only the cheaper one fits)
    assert (mid, skill) == ("Metadata/Monsters/LeagueExpeditionNew/X", 1)
    assert models.monster_best(rates, 100.0, 10.0, "spectreList", cat) is None


def test_hailstorm_rounds_is_held_to_the_bolts_made_and_the_share_that_hits_the_boss():
    """R41-4 / R42-11 (probe POE2T4762: PoB 0.634 bolt hits a second, 55 bolts, 0.3 s reload): one bolt made a reload
    time, (0.3 + 1) / 4 squared of them land on a 0.3 m boss, and the 0.2 s cap takes every other one."""
    hail = models.Skill("Hailstorm Rounds", 1, attack=True)
    stats = {"Speed": 0.634, "ReloadTime": 0.3, "BoltCount": 55, "AreaOfEffectRadius": 10,
             "AreaOfEffectRadiusSecondary": 40}
    mods, notes = models.hailstorm_rounds_model(hail, stats, (0.3, "a"), (1.5, "b"))
    target = (1 / 0.3) * (1.3 / 4) ** 2 * 0.5
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (target / 0.634 - 1), abs=0.01)
    assert notes[0]["confidence"] == "LOW" and f"x{(1 / 0.3) * (2.5 / 4) ** 2 * 0.5 / 0.634:.3f}" in notes[0]["basis"]
    assert models.hailstorm_rounds_model(hail, {"Speed": 0.6}, (0.3, "a"), (1.5, "b"))[1][0]["confidence"] == "UNVERIFIED"
    assert models.hailstorm_rounds_model(models.Skill("Basic Bolt", 1), stats, (0.3, "a"), (1.5, "b")) == ([], [])


def test_glacial_cascade_and_sunder_hit_the_boss_with_several_areas_a_wave():
    """R42-5: PoB scores one hit a wave; Glacial Cascade 6 of its 8 bursts (rows 5, 7), Sunder 2.5 of its 5 areas."""
    cascade = models.Skill("Glacial Cascade", 1, attack=True)
    sets = [{"label": "Wave", "TotalDPS": 100.0}, {"label": "Final Burst", "TotalDPS": 50.0}]
    mods, notes = models.fissure_count_model(cascade, "Wave", sets, {}, {}, [], 0.4)
    assert mods[0]["value"] == pytest.approx(500.0) and "6 of its 8 bursts" in notes[0]["basis"]
    assert "5 of its 8 bursts a wave: x5.000" in notes[0]["basis"] and "7 of its 8 bursts a wave: x7.000" in notes[0]["basis"]


def test_wildshards_novas_add_nothing_on_a_lone_boss_with_rows():
    """R42-9: Wildshards III 0.285 novas a cast (0.40 the other reading); its 8 extras miss a lone boss (R32-2)."""
    spark = models.Skill("Spark", 1, spell=True)
    note = models.wildshards_note(spark, ["Sione's Temper"], 2.0)[0]
    assert note["value"] == "x1.000" and "+0.142" in note["basis"] and "+0.200" in note["basis"]
    assert models.wildshards_note(spark, ["Wildshards I"])[0]["basis"].count("novas a cast") == 1
    assert models.wildshards_note(spark, ["Controlled Destruction"]) == []


def test_spectral_volley_fires_its_five_every_three_seconds():
    """R41-15: 5 Spectral Projectiles every 3 s at 1.2x a projectile, PoB's projectiles at 0.4x counted already."""
    bow = models.Skill("Lightning Arrow", 1, attack=True, support_stats={models.SPECTRAL_OWN_STAT: -60,
                                                                          models.SPECTRAL_MORE_STAT: 20})
    mods, notes = models.spectral_volley_model(bow, ["Spectral Volley"], {"Speed": 2.0, "TotalDPS": 800.0,
                                                                          "ProjectileCount": 1})
    one = 800.0 / 2.0 / 0.4 * 1.2  # (1,200 a Spectral Projectile)
    assert mods[0]["value"] == pytest.approx(100 * 5 * one / 3.0 / 800.0, abs=0.01)
    assert f"x{1 + 5 * one / 5.5 / 800:.3f}" in notes[0]["basis"]  # (the row: 5 attacks then 3 s)
    assert models.spectral_volley_model(bow, [], {"Speed": 2.0, "TotalDPS": 800.0}) == ([], [])


def test_ice_shards_bonus_is_a_play_row_only():
    note = models.ice_shards_note(models.Skill("Ice Shards", 1, attack=True))[0]
    assert note["value"] == "x1.000" and "t 1 s x3.0" in note["basis"] and "t 2 s x5.0" in note["basis"]
    assert models.ice_shards_note(models.Skill("Basic Bolt", 1, attack=True)) == []


def test_entangles_vines_hit_the_boss_three_times_a_second():
    """R42-8: at most 3 vine hits a second on one boss (6 Overgrown, a row), beside its Fissures; PoB scores one set."""
    entangle = models.Skill("Entangle", 1, spell=True)
    sets = [{"label": "Fissures", "TotalDPS": 100.0}, {"label": "Vines", "TotalDPS": 20.0, "AverageDamage": 50.0}]
    mods, notes = models.fissure_count_model(entangle, "Fissures", sets, {}, {}, [], 0.4)
    assert mods[0]["value"] == pytest.approx(100 * ((150.0 + 100.0) / 100.0 - 1))
    assert "3 vine hits a second" in notes[0]["basis"] and "a row: x4.000" in notes[0]["basis"]


def test_align_fate_empowers_one_cast_a_visage_and_the_visage_cast_is_a_row():
    """R41-9: a visage every interval / (1 + 25% N), the main Spell 1 / N of them; 30% more on the next cast (headline);
    the visage's own cast at your damage UNVERIFIED (a row)."""
    spark = models.Skill("Spark", 1, spell=True)
    level = {models.ALIGN_INTERVAL_STAT: 9150, models.ALIGN_PER_SPELL_STAT: 25, models.ALIGN_MORE_STAT: 30}
    mods, notes = models.align_fate_model(spark, ["Spark", "Fireball"], level, 2.0)
    mine = 1 / (9.15 / 1.5) / 2
    assert mods[0]["value"] == pytest.approx(100 * 0.3 * mine / 2.0, abs=0.01)
    assert f"x{1 + 1.3 * mine / 2.0:.3f} (a row)" in notes[0]["basis"]
    assert models.align_fate_model(spark, ["Fireball"], level, 2.0)[0] == []


def test_purple_flames_come_only_from_the_empowered_attacks_kills():
    """R41-11: 7% a stack, at most 10, from kills with the Empowered attack; a lone boss gives 0 (no flat rate)."""
    assert models.purple_flames(0.0, 0.2)[0] == [] and "no flat rate" in models.purple_flames(0.0, 0.2)[1][0]["basis"]
    mods, notes = models.purple_flames(1.0, 0.25)  # (0.25 flames a second x 20 s = 5 stacks)
    assert notes[0]["value"] == "35.0% as Chaos" and mods
    assert models.purple_flames(10.0, 1.0)[1][0]["value"] == "70.0% as Chaos"


def test_staggering_palms_projectile_is_held_to_the_buff_and_the_hitting_attacks():
    """R41-14 (probe POE2T4763: PoB counts the Projectile all fight at its own rate): uptime min(1, stuns x 7.5 s);
    one projectile per hitting Quarterstaff attack; never raised."""
    keep, uptime, game = models.staggering_palm_keep(0.05, 7.5, 1.5, 0.77)
    assert uptime == pytest.approx(0.375) and game == pytest.approx(0.5625) and keep == pytest.approx(0.5625 / 0.77)
    assert models.staggering_palm_keep(0.0, 7.5, 1.5, 0.77)[0] == 0.0
    assert models.staggering_palm_keep(1.0, 7.5, 3.0, 0.77)[0] == 1.0


def test_flame_breath_channels_only_part_of_its_cooldown():
    """Probe POE2T4763: PoB scores its hits nonstop; the game: a 4 s free window (+ Rage) each 10 s cooldown, after a
    wind-up of 4 attack times."""
    fb = models.Skill("Flame Breath", 1, attack=True)
    mods, notes = models.flame_breath_model(fb, 7.257, 10.032, 4.0, 0.0, "no Rage")
    assert mods[0]["value"] == pytest.approx(100 * (4.0 / 10.032 - 1), abs=0.01)
    half = models.flame_breath_model(fb, 7.257, 10.032, 4.0, 0.5, "half")[0][0]["value"]
    assert half == pytest.approx(100 * ((4.0 + 0.5 * (10.032 - 4 / 7.257 - 4.0)) / 10.032 - 1), abs=0.01)
    assert "Rage paying all x0.945" in notes[0]["basis"]
