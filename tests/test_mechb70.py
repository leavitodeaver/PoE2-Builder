"""MECHB70 (MECH-B): the "boss stands still" reader rule on MECH-B's sites, the Gas Arrow measurement (R67-1), the
Bodach (Vestige of Darkness). Pure model tests (no PoB)."""
import pytest

from builder.search import bosses, models


def test_toxic_growth_pustules_burst_on_the_boss_only_if_it_is_still_there():
    """MECHD9's survey (the biggest site): a ground Pustule bursts where it fell a fuse later: x still_there(burst,
    fuse) on the headline; rows: standing still, every move its mean length, moving within the field."""
    rates = [{"speed": 1.5, "detonationTime": 15}]
    stand_mods, stand = models.pustule_cap(rates, "Toxic Growth")
    boss = {"_boss": bosses.WORST}
    mods, notes = models.pustule_cap(rates, "Toxic Growth", stats=boss)
    still = bosses.still_there(boss, models.TOXIC_BURST, 15.0)[0]
    assert 0 < still < 1
    old = float(stand[0]["value"][1:])
    assert float(notes[0]["value"][1:]) == pytest.approx(old * still, abs=1e-3)
    assert "MECHD9's reader rule" in notes[0]["basis"] and "the boss standing still" in notes[0]["basis"]
    assert "Pustule field" in notes[0]["basis"]
    # Poison Spores' Pustule rides on the enemy: no factor
    assert models.pustule_cap(rates, "Poison Spores", stats=boss)[1][0]["value"] == \
        models.pustule_cap(rates, "Poison Spores")[1][0]["value"]
    # slower than the fuse allows (PoB's rate stands) still takes the factor
    slow_mods, slow = models.pustule_cap([{"speed": 0.05, "detonationTime": 15}], "Toxic Growth", stats=boss)
    assert slow_mods and float(slow[0]["value"][1:]) == pytest.approx(still, abs=1e-3)


def test_entangles_vines_count_only_while_the_boss_stands_on_the_newest_fissure():
    """MECHD9's survey (example build 12): the vines x stay_share(the fissure's half-width, the recast interval or the
    fissure's 8 s); rows: standing still, every move its mean length."""
    entangle = models.Skill("Entangle", 1, spell=True)
    sets = [{"label": "Fissures", "TotalDPS": 100.0}, {"label": "Vines", "TotalDPS": 20.0, "AverageDamage": 50.0}]
    boss = {"_boss": bosses.WORST}
    stay = models.fissure_vine_stay(boss, 1.25, sets)
    share = bosses.stay_share(boss, models.FISSURE_HALF_WIDTH, 0.8)[0]
    assert stay[0] == pytest.approx(share) and 0 < share < 1 and "0.80 s" in stay[2]
    assert models.fissure_vine_stay(boss, 0.0, sets)[0] == pytest.approx(
        bosses.stay_share(boss, models.FISSURE_HALF_WIDTH, models.FISSURE_SECONDS)[0])  # (not recast: its 8 s)
    assert models.fissure_vine_stay(None, 1.25, sets) is None
    mods, notes = models.fissure_count_model(entangle, "Fissures", sets, {}, {}, [], 0.4, stay=stay)
    assert mods[0]["value"] == pytest.approx(100 * ((150.0 * share + 100.0) / 100.0 - 1), abs=0.01)
    assert "reader rule" in notes[0]["basis"] and "the boss standing still x2.500" in notes[0]["basis"]


def test_comet_lands_after_its_fall_on_the_boss_still_there():
    """RESEARCH65 R65-8 (example build 7): Comet lands 0.55 s after the cast: x still_there(its radius, 0.55 s)."""
    boss = {"_boss": bosses.WORST}
    comet = models.Skill("Comet", 1, spell=True)
    mods, notes = models.delayed_landing_model(comet, boss, 2.8)
    still = bosses.still_there(boss, 2.8, 0.55)[0]
    assert 0.9 < still < 1 and mods[0]["value"] == pytest.approx(100 * (still - 1), abs=1e-3)
    assert "R65-8" in notes[0]["basis"] and "standing still x1" in notes[0]["basis"]
    assert models.delayed_landing_model(models.Skill("Spark", 1, spell=True), boss, 2.8) == ([], [])
    assert models.delayed_landing_model(comet, boss, 0.0) == ([], [])


def test_a_cloud_poisons_on_landing_and_every_2_s_with_no_tail_past_its_applications():
    """RESEARCH67 R67-1 (THE USER's clip): n = ceil(life / 2.02) applications, (n - 1) x min(2.02, d) + d s covered at
    the default limit; an 8 s cloud covers 8 s (not 10), a 4 s cloud gives 2 Poisons (not 2.5); overlapping clouds
    hold one; a raised limit stacks n x d; a changed duration shows both readings, the lower the headline."""
    assert models.cloud_poison_cover(8.0, 2.0, models.CLOUD_REPOISON) == (4, pytest.approx(8.0))
    assert models.cloud_poison_cover(4.0, 2.0, models.CLOUD_REPOISON) == (2, pytest.approx(4.0))
    assert models.cloud_poison_cover(7.02, 2.0, models.CLOUD_REPOISON)[0] == 4  # (R67-1's clouds: 4 Poisons each)
    cloud = {"PoisonDPS": 1000.0, "PoisonStackPotential": 1.0, "PoisonStacksMax": 1, "PoisonDuration": 2.0}
    assert models.sniper_cloud_poison(cloud, 8.0, 10.0)[1] == pytest.approx(0.8)
    assert models.sniper_cloud_poison(cloud, 8.0, 6.0)[1] == pytest.approx(1.0)  # (overlap: one Poison)
    raised = {**cloud, "PoisonStacksMax": 4, "PoisonStackPotential": 0.25}
    assert models.sniper_cloud_poison(raised, 8.0, 4.0)[1] == pytest.approx(2.0)  # (2 clouds x 4 x 2 s / 4 s)
    longer = {**cloud, "PoisonDuration": 3.0}
    fixed = models.sniper_cloud_poison(longer, 8.0, 20.0, "fixed")[1]  # (4 at 2.02 s: 3 x 2.02 + 3 = 9.06 s)
    by_duration = models.sniper_cloud_poison(longer, 8.0, 20.0, "duration")[1]  # (3 at 3 s: 9 s)
    assert fixed == pytest.approx(9.06 / 20) and by_duration == pytest.approx(9.0 / 20)
    assert models.sniper_cloud_poison(longer, 8.0, 20.0)[1] == pytest.approx(min(fixed, by_duration))
    assert "OPEN" in models.cloud_reading_row(longer, 8.0, 20.0) and models.cloud_reading_row(cloud, 8.0, 20.0) == ""


def test_the_bodach_attacks_the_boss_in_your_presence_unscaled():
    """RESEARCH65 R65-21..23 (THE USER's question): every 3 / (1 + 5% x Power) + 0.25 s, the six attacks' mean at the
    character's level, through the boss's Armour; 0 outside your Presence; rows for the hidden 2 / 4, the area level,
    x0.67 and the strongest / weakest pick; the re-equip bug in the play line."""
    texts = ["The Bodach haunts your Presence"]
    assert models.bodach_rate(20) == pytest.approx(1 / 1.5)  # (MECHB71, R68-9 (d): symmetric randomisation)
    assert models.bodach_rate(20, random=models.BODACH_RANDOM) == pytest.approx(1 / 1.75)
    assert models.bodach_hit(90, 15) == pytest.approx(103125, rel=0.01)  # (R65-23's table: the mean of six at 90)
    dps, mods, notes = models.bodach_model(texts, 20, 90, 82, 0.0, 0.0, True, "inside")
    # (MECHB71, R68-9: the AREA level 82 and the middle attack, base 15, the headline)
    assert dps == pytest.approx(models.bodach_rate(20) * models.bodach_hit(82, 15)) and mods
    assert "your level 90" in notes[0]["basis"] and "Moth Meteor" not in notes[0]["basis"]
    assert "a '2' helmet" in notes[0]["basis"] and "x0.67" in notes[0]["basis"] and "re-equip" in notes[0]["play"]
    armoured, _, _ = models.bodach_model(texts, 20, 90, 82, 50000.0, 0.0, True, "inside")
    assert 0 < armoured < dps
    out, mods, notes = models.bodach_model(texts, 20, 90, 82, 0.0, 0.0, False, "outside")
    assert out == 0.0 and not mods and "outside your Presence" in notes[0]["basis"]
    assert models.bodach_model(["Enemies in your Presence are Blinded"], 20, 90, 82, 0.0, 0.0, True, "") == (0.0, [], [])


def test_dark_pact_is_pressed_from_the_builds_minions_when_a_cast_gains():
    """AUDIT40 A40-29: no cooldown or cost; at most 1 / 0.7 casts a second and count x k / revive (k = ceil(1 / 13%) = 8
    casts a minion); a cast's gain = its hit - the main skill's DPS x 0.7 - the minion's damage lost while dead."""
    big = [("Skeletal Brute", 20000.0, 2.0, 7.5, 0.0)]
    added, mods, notes = models.pact_press_model("Dark Pact", 20, big, 100.0)
    hit = ((52 + 97) / 2 + 0.13 * 20000) * 1.07
    rate = min(1 / 0.7, 2 * 8 / 7.5)
    assert added == pytest.approx(rate * (hit - 70.0)) and mods and "UNSCALED" in notes[0]["basis"]
    assert "without it +0" in notes[0]["play"]
    lost, mods, notes = models.pact_press_model("Dark Pact", 20, big, 10 ** 6)
    assert lost == 0.0 and not mods and "Don't press" in notes[0]["play"]
    # a minion whose own damage while dead outweighs the cast: not pressed
    costly = [("Skeletal Brute", 20000.0, 2.0, 7.5, 10 ** 6)]
    assert models.pact_press_model("Dark Pact", 20, costly, 100.0)[0] == 0.0
    assert models.pact_press_model("Abyssal Pact", 20, [], 100.0) == (0.0, [], [])


def test_a_kept_up_thunderstorm_overgrows_at_the_rate_the_search_charges():
    """SEARCH41's upkeep (the manager, 26 Nov 04:50; MECHB69's item 7, b12): a Thunderstorm with no chosen rate is read at
    objective.upkeep_rates' use, not PoB's cast rate; a chosen press rate still wins."""
    storm = models.Skill("Thunderstorm", 2, spell=True)
    row = {"name": "Thunderstorm", "speed": 2.0, "duration": 4.0}
    pob, parts, _ = models.overgrown_share([storm], {2: {**row, "_press": None}})
    kept, kept_parts, _ = models.overgrown_share([storm], {2: {**row, "_press": None, "_upkeep": 0.1}})
    assert pob == 1.0 and kept == pytest.approx(0.4) and "upkeep" in kept_parts[0]
    pressed, _, _ = models.overgrown_share([storm], {2: {**row, "_press": 0.05, "_upkeep": 0.1}})
    assert pressed == pytest.approx(0.4)  # (2 x 0.05 x 4 s: the chosen rate)
