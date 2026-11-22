"""GEM8: skill_specific_stat_description_mode's described effects (ledger gems-gem8.yaml)."""

from __future__ import annotations

import math

import pytest

from builder.search import models
from builder.search.models import Skill

MODE = {"skill_specific_stat_description_mode": 1}


def _area(mods: list[dict]) -> list[tuple[str, float, str | None]]:
    # (gem_stat_model tags a line with its entry only for `share:` entries: match the line itself)
    return [(m["line"], m["scale"], m.get("skill")) for m in mods if m["line"] == "1% more Area of Effect"]


def test_rends_lightning_charged_strike_gets_150_percent_more_area_on_that_set_only():
    charged = Skill("Rend", 1, attack=True, stat_set="Lightning-Charged", level_stats=MODE)
    mods, notes = models.gem_stat_model([charged], main=charged)
    assert _area(mods) == [("1% more Area of Effect", pytest.approx(150), "Rend")]
    assert any(n["entry"] == "model-gem8-rend-charged-area" and n["value"].startswith("150") for n in notes)
    normal = Skill("Rend", 1, attack=True, stat_set="Normal Strikes", level_stats=MODE)
    mods, notes = models.gem_stat_model([normal], main=normal)
    assert not _area(mods)
    assert any(n["entry"] == "model-gem8-rend-charged-area" and n["value"] == "not counted" for n in notes)


@pytest.mark.parametrize("name", ["Whirlwind Lance", "Tempest Bell", "Fulmination"])
def test_the_closed_skills_get_no_line(name):
    skill = Skill(name, 1, attack=True, level_stats=MODE)
    mods, notes = models.gem_stat_model([skill], main=skill)
    assert not [m for m in mods if m.get("skill") == name]
    assert not [n for n in notes if n["entry"] == "no-effect-gem8-description-mode-counted"]


RAIN = {**MODE, "rain_of_arrows_delay_per_arrow": 80, "rain_hit_delay_ms": 120}


def _less(mods: list[dict]) -> list[float]:
    return [m["scale"] for m in mods if m["line"] == "1% less Damage" and m.get("skill") == "Rain of Arrows"]


@pytest.mark.parametrize("mod, arrows, speed, less", [
    (1.0, 39, 1.5, 0.0),             # an arrow every 0.08 s: the 0.12 s limit binds, as PoB counts
    (2.0, 39, 1.5, 0.0),             # faster projectiles add nothing on a boss
    (0.7, 39, 1.5, 0.0),             # Deceleration II alone: 0.114 s, still under the limit
    (0.588, 39, 1.5, 100 * (1 - 0.12 / (0.08 / 0.588))),  # with Short Shot and a -6% node: 0.136 s, ~12% fewer hits
    (1.0, 10, 0.5, 100 * (1 - 10 * 0.08 * 0.5)),          # a 0.8 s rain every 2 s: 40% of the time
])
def test_rain_of_arrows_hit_rate_follows_projectile_speed_and_never_adds(mod, arrows, speed, less):
    rain = Skill("Rain of Arrows", 1, attack=True, level_stats=RAIN)
    stats = {"ProjectileSpeedMod": mod, "ProjectileCount": arrows, "Speed": speed}
    mods, notes = models.gem_stat_model([rain], stats=stats, main=rain)
    assert _less(mods) == ([pytest.approx(less, rel=1e-3)] if less else [])
    assert any(n["entry"] == "model-gem9-rain-landing" and ("not counted" in n["value"]) != bool(less) for n in notes)


def test_rain_of_arrows_off_the_main_skill_keeps_pobs_rate():
    rain = Skill("Rain of Arrows", 2, attack=True, level_stats=RAIN)
    other = Skill("Lightning Arrow", 1, attack=True)
    mods, notes = models.gem_stat_model([rain, other], stats={"ProjectileSpeedMod": 0.5}, main=other)
    assert not _less(mods)
    assert any(n["entry"] == "model-gem9-rain-landing" and "not the main skill" in n["basis"] for n in notes)


ER = {**MODE, "emergency_reload_damage_+%_final": 31, "base_skill_effect_duration": 6000}
ER_ENTRY = "model-gem10-emergency-reload-empowered-bolts"


def _more(mods: list[dict], name: str) -> list[float]:
    return [m["scale"] for m in mods if m["line"] == "1% more Damage" and m.get("skill") == name]


@pytest.mark.parametrize("stats, more", [
    # a clip of 4 at 2 shots/s (4/s firing, 1 s reload), once per 45 s: 1/45 + 0.31 x 4 / 90
    ({"BoltCount": 4, "EffectiveBoltCount": 4, "Speed": 2.0, "FiringRate": 4.0, "EffectiveReloadTime": 1.0},
     100 * (1 / 45 + 0.31 * 4 / 90)),
    # a clip of 40 at 4/s firing: 6 s of firing (24 bolts) Empowered
    ({"BoltCount": 40, "EffectiveBoltCount": 40, "Speed": 3.0, "FiringRate": 4.0, "EffectiveReloadTime": 1.0},
     100 * (1 / 45 + 0.31 * 24 / 135)),
    # no Ammunition consumed: no reload to skip, 6 s of firing Empowered
    ({"BoltCount": 4, "EffectiveBoltCount": 1e9, "Speed": 4.0, "FiringRate": 4.0, "EffectiveReloadTime": 0.0,
      "ChanceToNotConsumeAmmo": 100}, 100 * 0.31 * 24 / 180),
])
def test_emergency_reloads_empowered_clip_and_skipped_reload_go_to_the_main_crossbow_skill(stats, more):
    reload = Skill("Emergency Reload", 2, cooldown=45, level_stats=ER)
    bolt = Skill("Fragmentation Rounds", 1, attack=True)
    mods, notes = models.gem_stat_model([reload, bolt], stats=stats, main=bolt)
    assert _more(mods, "Fragmentation Rounds") == [pytest.approx(more, rel=1e-2)]
    assert not _more(mods, "Emergency Reload")
    assert any(n["entry"] == ER_ENTRY and "play" in n for n in notes)


def test_emergency_reload_adds_nothing_to_a_skill_without_a_clip():
    reload = Skill("Emergency Reload", 2, cooldown=45, level_stats=ER)
    other = Skill("Lightning Arrow", 1, attack=True)
    mods, notes = models.gem_stat_model([reload, other], stats={"Speed": 2.0}, main=other)
    assert not [m for m in mods if m.get("entry") == ER_ENTRY]
    assert any(n["entry"] == ER_ENTRY and n["value"] == "not counted" for n in notes)


KELARI = {"skill_specific_stat_description_mode": 2, "base_secondary_skill_effect_duration": 900}
KELARI_ENTRY = "model-gem10-kelari-beetles-per-corpse"


def _minion(mods: list[dict]) -> list[float]:
    return [m["scale"] for m in mods if m["line"] == "Minions deal 1% more Damage" and m.get("entry") == KELARI_ENTRY]


@pytest.mark.parametrize("adds, speed, more", [
    (0.0, 1.0, -100.0),                 # a lone boss, no corpse source: no beetles
    (0.25, 1.0, 100 * (2 * 0.25 - 1)),  # a corpse every 4 s: half PoB's one beetle a second
    (5.0, 1.0, 100 * (2 / 0.9 - 1)),    # more corpses than it can consume: one per 0.9 s, 2.2 explosions/s
    (0.25, 2.0, 100 * (0.5 / 2 - 1)),   # against PoB's faster minion Speed
])
def test_kelaris_beetles_follow_the_builds_corpses(adds, speed, more):
    kelari = Skill("Kelari's Malediction", 1, corpses=True, level_stats=KELARI)
    stats = {models.ADD_KILLS_STAT: adds, "Minion.Speed": speed}
    mods, notes = models.gem_stat_model([kelari], stats=stats, main=kelari)
    assert _minion(mods) == [pytest.approx(more, rel=1e-2)]
    assert any(n["entry"] == KELARI_ENTRY and "play" in n for n in notes)


def test_kelaris_beetles_share_the_corpses_with_another_user():
    kelari = Skill("Kelari's Malediction", 1, corpses=True, level_stats=KELARI)
    other = Skill("Detonate Dead", 2, spell=True, corpses=True)
    mods, _ = models.gem_stat_model([kelari, other], stats={models.ADD_KILLS_STAT: 0.5, "Minion.Speed": 1.0}, main=kelari)
    assert _minion(mods) == [pytest.approx(100 * (2 * 0.25 - 1), rel=1e-2)]


DESCENT_ENTRY = "model-gem10-righteous-descent-fragments"


def test_righteous_descents_empowered_leaps_follow_the_ordaineds_fragments():
    empowered = Skill("Righteous Descent", 1, attack=True, stat_set="Empowered", level_stats=MODE)
    mods, notes = models.gem_stat_model([empowered], stats={"Speed": 0.0997}, main=empowered)
    assert [m["scale"] for m in mods if m.get("entry") == DESCENT_ENTRY] == [pytest.approx(100 * (0.25 / 0.0997 - 1), rel=1e-2)]
    impact = Skill("Righteous Descent", 1, attack=True, stat_set="Impact", level_stats=MODE)
    mods, notes = models.gem_stat_model([impact], stats={"Speed": 0.0997}, main=impact)
    assert not [m for m in mods if m.get("entry") == DESCENT_ENTRY]
    assert any(n["entry"] == DESCENT_ENTRY and n["value"] == "not counted" for n in notes)


def test_medveds_felling_and_paquates_pact_charge_their_life_per_use_as_life_lost_per_second():
    sunder = Skill("Sunder", 1, attack=True, melee=True, support_stats={models.MEDVED_STAT: 1})
    cry = Skill("Infernal Cry", 2, support_stats={models.PAQUATE_STAT: 10})
    mods, notes = models.gem_stat_model([sunder, cry], stats={"Life": 2000, "Rage": 32, "Speed": 2.0}, main=sunder,
                                        groups={2: {"speed": 0.5, "_press": 1.0}})
    loss = sorted(m["value"] for m in mods if m.get("kind") == "Raw" and m["name"] == "LifeDegen")
    # Medved's: 1% x 2,000 x 6 (32 Rage, per 5, floored) x 2/s = 240; Paquate's: 2 uses in 4 s -> 20% x 2,000 x 0.5/s = 200
    assert loss == [pytest.approx(200), pytest.approx(240)]
    assert models.gem_stat_side_groups([sunder, cry], sunder) == [2]


def test_a_support_life_cost_with_no_rate_read_is_left_uncharged_with_a_note():
    cry = Skill("Infernal Cry", 2, support_stats={models.PAQUATE_STAT: 10})
    mods, notes = models.gem_stat_model([cry], stats={"Life": 2000}, main=None, groups={})
    assert not [m for m in mods if m.get("kind") == "Raw"]
    assert any(n["entry"] == models.LIFE_LOSS_ENTRY and n["value"] == "not charged" for n in notes)


def test_muster_counts_the_kinds_of_reviving_minions_not_every_persistent_minion_skill():
    warriors = Skill("Skeletal Warrior Minion", 1, summon={"persistent": True, "limit": 2, "reviveSpeed": 1.0},
                     support_stats={models.MUSTER_STAT: 7})
    hound = Skill("Summon Infernal Hound", 2, summon={"persistent": True, "limit": 1, "reviveSpeed": 1.0})
    spirits = Skill("Raging Spirits", 3, summon={"persistent": True, "limit": 10, "duration": 5.0})
    mods, notes = models.gem_stat_model([warriors, hound, spirits], stats={}, main=warriors)
    override = [m for m in mods if m.get("kind") == "Raw" and m["name"] == models.MUSTER_MULTIPLIER]
    assert override == [{"kind": "Raw", "name": models.MUSTER_MULTIPLIER, "type": "OVERRIDE", "value": 2}]
    assert any(n["entry"] == models.MUSTER_ENTRY and n["value"] == "2" for n in notes)


def test_no_muster_override_without_muster_socketed():
    warriors = Skill("Skeletal Warrior Minion", 1, summon={"persistent": True, "limit": 2, "reviveSpeed": 1.0})
    mods, _ = models.gem_stat_model([warriors], stats={}, main=warriors)
    assert not [m for m in mods if m.get("name") == models.MUSTER_MULTIPLIER]


INEXORABLE = {models.INEXORABLE_STAT: 10, models.INEXORABLE_CAP_STAT: 100}


@pytest.mark.parametrize("speed, crit, seconds", [
    (5.0, 0.0, 10.0),   # never crits: the cap, as PoB's default
    (1.0, 100.0, 1.0),  # crits every hit, one a second: one whole second at each hit
    (2.0, 50.0, sum(0.5 * 0.5 ** (k - 1) * min(k // 2, 10) for k in range(1, 40))),
])
def test_inexorable_criticals_count_follows_the_builds_hits_and_crits(speed, crit, seconds):
    spark = Skill("Spark", 1, spell=True, support_stats=INEXORABLE)
    mods, notes = models.gem_stat_model([spark], stats={"Speed": speed, "CritChance": crit}, main=spark)
    whole = [m["value"] for m in mods if m.get("name") == models.INEXORABLE_MULTIPLIER]
    extra = sum(m["value"] for m in mods if m.get("name") == "CritChance" and m.get("skill") == "Spark")
    assert len(whole) == 1 and whole[0] == int(whole[0])  # (PoB floors the multiplier: whole seconds, the rest as INC)
    assert whole[0] + extra / 10 == pytest.approx(seconds, abs=1e-3)
    assert any(n["entry"] == models.INEXORABLE_ENTRY and "play" in n for n in notes)


def test_inexorable_critical_off_the_main_skill_sets_nothing():
    spark = Skill("Spark", 1, spell=True)
    side = Skill("Arc", 2, spell=True, support_stats=INEXORABLE)
    mods, notes = models.gem_stat_model([spark, side], stats={"Speed": 2.0, "CritChance": 10}, main=spark)
    assert not [m for m in mods if m.get("name") == models.INEXORABLE_MULTIPLIER]
    assert any(n["entry"] == models.INEXORABLE_ENTRY and "Arc" in n["basis"] for n in notes)


@pytest.mark.parametrize("speed, press, stacks", [
    # (MECH80, RESEARCH26 R26-15: each new stack refreshes all, over a 100 s fight with the climb to 10 counted)
    (0.25, None, (sum(range(1, 11)) * 4 + 10 * 60) / 100),  # a warcry every 4 s: 8.2 (1.25 on own timers)
    (0.25, 0.5, 0.625),   # pressed half the time: one every 8 s, each runs out first: 0.625 under both rules
    (0.1, None, 0.5),     # every 10 s: one stack half the time (GEM15's case; both rules agree)
    (3.0, None, None),    # spammed: the climb takes 3.3 s, then 10
])
def test_corrupting_crys_stacks_follow_the_warcrys_use_rate(speed, press, stacks):
    from builder.search import bosses, conditions

    spark, cry, cc = Skill("Spark", 1, spell=True), Skill("Infernal Cry", 2), Skill("Corrupted Cry", 2, triggered=True)
    dps = conditions.boss_pool(bosses.of({}).name) / 100  # (a 100 s fight)
    rate = speed * (press if press is not None else 1.0)
    want = models.cry_stacks(rate, 100.0)[0]
    if stacks is not None:
        assert want == pytest.approx(stacks)
    stages = want - 1
    mods, notes = models.gem_stat_model([spark, cry, cc], stats={"Speed": 1.4, "FullDPS": dps}, main=spark,
                                        groups={2: {"speed": speed, "_press": press, "name": "Infernal Cry"}})
    assert [m["value"] for m in mods if m.get("name") == models.CRY_MULTIPLIER] == [0]  # (one stack: PoB floors it)
    assert [m["value"] for m in mods if m.get("name") == "Damage" and m.get("skill") == "Corrupted Cry"] == [
        pytest.approx(100 * stages)]
    assert models.gem_stat_side_groups([spark, cry, cc], spark) == [2]
    note = next(n for n in notes if n.get("entry") == models.CRY_ENTRY)
    own = min(10.0, speed * (press if press is not None else 1.0) * 5)  # each stack its own 5 s: the "without" row
    # (GEM15's "without" row, in MECH80's wording: MECH81 reconciled the two, the manager 16 Nov 04:23)
    assert f"{own:.2f} if each stack keeps its own 5 s" in note["basis"] and f"own 5 s): {own:.2f}" in note["play"]


def test_corrupting_crys_stages_are_held_at_one_stack_until_the_model_sees_a_cry():
    # (GEM17, RESEARCH28 CD-9) PoB's stages box reads its placeholder 10 when unset: with no Corrupted Cry yet (a support
    # what-if adds one) the multiplier is held at one stack; the model's own line wins; a forbidden entry leaves PoB's
    floor = models.cry_stage_floor([])
    assert [(m["name"], m["type"], m["value"]) for m in floor] == [(models.CRY_MULTIPLIER, "OVERRIDE", 0)]
    spark, cry, cc = Skill("Spark", 1, spell=True), Skill("Infernal Cry", 2), Skill("Corrupted Cry", 2, triggered=True)
    mods, _ = models.gem_stat_model([spark, cry, cc], stats={"Speed": 1.4, "FullDPS": 1000.0}, main=spark,
                                    groups={2: {"speed": 0.25, "_press": None, "name": "Infernal Cry"}})
    assert models.cry_stage_floor(mods) == []
    assert models.cry_stage_floor([], {models.CRY_ENTRY}) == []


@pytest.mark.parametrize("uses, less", [
    ([], 100.0),  # no curse to detonate
    ([models.CurseUse("Despair", 1.0, "cast by hand", True, 7.4)], 100 * (1 - 1 / (0.6 + 1.5 + 3.7))),
    ([models.CurseUse("Despair", 1.0, "cast by hand once: 'Curses you inflict have infinite Duration' (no recasts)")],
     100.0),  # an infinite curse is never half expired (0.2.0)
])
def test_hexblast_detonates_only_what_its_curses_allow(uses, less):
    hexblast, despair = Skill("Hexblast", 1, spell=True), Skill("Despair", 2, spell=True, cast_time=0.6)
    mods, notes = models.hexblast_model([hexblast, despair], uses, {"Speed": 1.0}, hexblast)
    assert [m["scale"] for m in mods if m["line"] == "1% less Damage"] == [pytest.approx(less, rel=1e-3)]
    assert notes and notes[0]["entry"] == models.HEXBLAST_ENTRY


def test_hexblast_casting_slower_than_its_curses_allow_keeps_pobs_rate():
    hexblast = Skill("Hexblast", 1, spell=True)
    uses = [models.CurseUse("Despair", 1.0, "an Aura (Blasphemy)", False, 2.0)]  # a detonation per 2.5 s
    mods, notes = models.hexblast_model([hexblast], uses, {"Speed": 0.3}, hexblast)
    assert not mods and notes[0]["value"] == "as PoB"


def test_burgeon_averages_over_the_channels_the_dodge_rhythm_allows():
    assert models.burgeon_average(10, 40, 10.0) == pytest.approx(30.0)  # 0, 10, 20, 30, then 40 for 6 s
    sup = {models.BURGEON_STAT: 20, models.BURGEON_CAP_STAT: 40}
    skill = Skill("Incinerate", 1, spell=True, support_stats=sup)
    mods, notes = models.gem_stat_model([skill], stats={}, main=skill)
    channel = models.DODGE_EVERY - models.DODGE_ROLL_SECONDS  # 1.25 s: 0% for 1 s, 20% for 0.25 s
    assert [m["value"] for m in mods if m.get("name") == "Damage" and m.get("skill") == "Incinerate"] == [
        pytest.approx(models.burgeon_average(20, 40, channel))]
    assert any(n["entry"] == models.BURGEON_ENTRY and "play" in n for n in notes)


def test_salvo_counts_its_seals_and_its_random_directions_on_one_boss():
    sup = {models.SALVO_PROJECTILE_STAT: 1, models.SALVO_INTERVAL_STAT: 1000, models.SALVO_MAX_STAT: 6}
    arrow = Skill("Lightning Arrow", 1, attack=True, support_stats=sup)
    # 0.5 uses/s: 2 Seals a use, 1 + 2 projectiles; at 5 m the default boss (0.3 m, MECHB35: RESEARCH28's ObjectSize
    # radius, was 1.5 m) is asin(0.06) / pi of the turn
    mods, notes = models.gem_stat_model([arrow], stats={"Speed": 0.5, "ProjectileCount": 1}, main=arrow)
    q = models.salvo_hit_chance(5.0)
    assert q == pytest.approx(0.0191, abs=1e-4) and models.salvo_hit_chance(5.0, 1.5) == pytest.approx(0.0970, abs=1e-3)
    dps = [m["value"] for m in mods if m.get("name") == "DPS" and m.get("skill") == "Lightning Arrow"]
    assert dps == [pytest.approx(100 * ((1 - (1 - q) ** 3) - 1), abs=0.01)]
    note = next(n for n in notes if n["entry"] == models.SALVO_ENTRY)
    assert note["confidence"] == "LOW" and "play" in note
    # Seals capped at 6; the calculated distance (enemyDistance, units) is read; a shotgun skill counts every projectile
    shards = Skill("Galvanic Shards", 1, attack=True, support_stats=sup)
    mods, _ = models.gem_stat_model([shards], stats={"Speed": 0.1, "ProjectileCount": 4}, main=shards,
                                    config={"enemyDistance": 20})
    q2 = models.salvo_hit_chance(2.0)
    assert [m["value"] for m in mods if m.get("name") == "DPS"] == [pytest.approx(100 * ((4 + 6) * q2 / 4 - 1), abs=0.01)]


def test_salvo_off_the_main_skill_keeps_pobs_number_with_a_note():
    sup = {models.SALVO_PROJECTILE_STAT: 1, models.SALVO_INTERVAL_STAT: 1000, models.SALVO_MAX_STAT: 6}
    side = Skill("Lightning Arrow", 2, attack=True, support_stats=sup)
    main = Skill("Spark", 1, spell=True)
    mods, notes = models.gem_stat_model([main, side], stats={"Speed": 1, "ProjectileCount": 1}, main=main)
    assert not [m for m in mods if m.get("name") == "DPS"]
    assert any(n["entry"] == models.SALVO_ENTRY and "isn't the main skill" in n["basis"] for n in notes)


def test_volt_charges_from_the_builds_dodge_rolls_and_caps_at_its_maximum():
    sup = {models.VOLT_PER_METRE_STAT: 2, models.VOLT_MAX_STAT: 30, models.VOLT_GAIN_STAT: 10}
    arrow = Skill("Lightning Arrow", 1, attack=True, support_stats=sup)
    mods, notes = models.gem_stat_model([arrow], stats={"Speed": 2, "MovementSpeedMod": 1}, main=arrow)
    charges = 2 * models.DODGE_ROLL_METRES / models.DODGE_EVERY / 2  # 2 a metre, a roll every 2 s, 2 uses/s
    assert [m["value"] for m in mods if m.get("name") == "DamageGainAsLightning"] == [pytest.approx(charges, abs=0.01)]
    assert any(n["entry"] == models.VOLT_ENTRY and "play" in n for n in notes)
    slow = Skill("Lightning Arrow", 1, attack=True, support_stats=sup)
    mods, _ = models.gem_stat_model([slow], stats={"Speed": 0.01, "MovementSpeedMod": 1}, main=slow)
    assert [m["value"] for m in mods if m.get("name") == "DamageGainAsLightning"] == [pytest.approx(30)]


def test_uhtreds_constellation_counts_the_builds_other_cooldown_skills_used_on_cooldown():
    stat = "support_uhtred_damage_+%_final_per_different_cooldown_skill_in_last_8_seconds"
    main = Skill("Shield Charge", 1, attack=True, cooldown=6, support_stats={stat: 8})
    cry = Skill("Infernal Cry", 2, cooldown=8)  # within 8 s all the time
    wall = Skill("Frost Wall", 3, spell=True, cooldown=16)  # half the time
    trig = Skill("Comet", 4, spell=True, triggered=True, cooldown=1)  # triggered: not counted (UNVERIFIED)
    mods, notes = models.gem_stat_model([main, cry, wall, trig], stats={"Speed": 1}, main=main)
    assert [m["scale"] for m in mods if m.get("entry") == "support-gem11-uhtreds-constellation-cooldown-skills"] == [
        pytest.approx(8 * 2.5)]
    alone, *_ = models.cooldown_skills_share(main, {"_skills": [main]})
    assert alone == 1


def test_rathas_assault_fires_the_bolts_each_dodge_loads_instead_of_reloading():
    sup = {models.RATHA_DODGE_STAT: 4}
    bolt = Skill("Armour Piercing Rounds", 1, attack=True, support_stats=sup)
    mods, notes = models.gem_stat_model([bolt], stats={"Speed": 3, "FiringRate": 4}, main=bolt)
    rate = 4 / (models.DODGE_ROLL_SECONDS + 4 / 4)  # a roll, then 4 bolts at 4/s
    assert [m["value"] for m in mods if m.get("name") == "DPS"] == [pytest.approx(100 * (rate / 3 - 1), abs=0.01)]
    mods, _ = models.gem_stat_model([bolt], stats={"Speed": 3, "FiringRate": 4, "ChanceToNotConsumeAmmo": 100}, main=bolt)
    assert [m["value"] for m in mods if m.get("name") == "DPS"] == [pytest.approx(100 * (4 / 3 - 1), abs=0.01)]


def test_bhatairs_vengeance_counts_rage_over_two_for_the_share_of_the_fight_after_a_freeze():
    ice = Skill("Ice Strike", 1, attack=True, support_stats={models.BHATAIR_STAT: 15000})
    stats = {"Speed": 2, "HitChance": 100, "FreezeBuildupAvg": 10, "Rage": 31}
    mods, notes = models.gem_stat_model([ice], stats=stats, main=ice)
    up = min(1.0, 15 * models.buildup_rate(stats, "Freeze"))
    assert 0 < up < 1
    assert [m["value"] for m in mods if m.get("name") == "DamageGainAsCold"] == [pytest.approx(15 * up, abs=0.01)]
    mods, notes = models.gem_stat_model([ice], stats={**stats, "FreezeBuildupAvg": 0}, main=ice)
    assert not [m for m in mods if m.get("name") == "DamageGainAsCold"]  # no Freeze: no source, 0
    assert any(n["entry"] == models.BHATAIR_ENTRY for n in notes)


def test_tricksters_shard_needs_four_casts_within_a_mimics_five_seconds():
    spark = Skill("Spark", 1, spell=True, support_stats={models.TRICKSTER_STAT: -50})
    mods, _ = models.gem_stat_model([spark], stats={"Speed": 0.6}, main=spark)
    assert [m["value"] for m in mods if m.get("name") == "DPS"] == [pytest.approx(50)]
    mods, notes = models.gem_stat_model([spark], stats={"Speed": 0.5}, main=spark)
    assert not [m for m in mods if m.get("name") == "DPS"]
    assert any(n["entry"] == models.TRICKSTER_ENTRY and n["value"] == "x1.000" for n in notes)


def test_olroths_conviction_adds_its_empowers_to_the_warcry_by_name_only():
    cry = Skill("Infernal Cry", 2, support_stats={models.OLROTH_STAT: 2})
    barrage = Skill("Barrage", 3, support_stats={models.OLROTH_STAT: 2})
    mods, notes = models.gem_stat_model([cry, barrage])
    assert [(m["name"], m["value"]) for m in mods if m.get("name", "").endswith("EmpoweredAttacks")] == [
        ("InfernalEmpoweredAttacks", 2)]
    assert any(n["entry"] == models.OLROTH_ENTRY and "Barrage" in n["basis"] for n in notes)
    # GEM14 (MECHB33's find): Arctic Howl is a Warcry PoB makes no Warcry buff for, so no ArcticHowlEmpoweredAttacks
    # mod; its extra uses are counted with its Empowered uses (Assumptions.empowered_uses x (1 + extra), MECHB32)
    howl = Skill("Arctic Howl", 4, support_stats={models.OLROTH_STAT: 2})
    mods, notes = models.olroth_model([howl])
    assert mods == [] and "no Warcry buff for Arctic Howl" in notes[0]["basis"] and notes[0]["value"] == "x3 Empowered uses a use"


def test_a_combo_skill_is_scored_over_its_whole_combo_not_the_set_pob_keeps():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0, "Speed": 2.0},  # 50 a hit in 0.5 s
            {"label": "Third Strike", "TotalDPS": 150.0, "Speed": 1.0}]  # 150 a hit in 1 s
    average = (50 + 50 + 150) / (0.5 + 0.5 + 1.0)  # 125
    mods, notes = models.combo_sequence_model("Ice Strike", "Third Strike", sets)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (average / 150 - 1), abs=0.01)]  # an overcount cut
    mods, _ = models.combo_sequence_model("Ice Strike", "Normal Strikes", sets)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (average / 100 - 1), abs=0.01)]


def test_tempest_flurrys_third_use_strikes_three_times_in_its_combo():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0, "Speed": 2.0}, {"label": "Third Strike", "TotalDPS": 40.0, "Speed": 1.0},
            {"label": "Fourth Strike", "TotalDPS": 200.0, "Speed": 1.0}]
    average = (50 + 50 + 3 * 40 + 200) / (0.5 + 0.5 + 1 + 1)
    mods, notes = models.combo_sequence_model("Tempest Flurry", "Fourth Strike", sets)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (average / 200 - 1), abs=0.01)]
    assert "x3 Strikes" in notes[0]["basis"]


def _named(mods: list[dict], suffix: str) -> list[tuple[str, float]]:
    return [(m["name"], m["value"]) for m in mods if m.get("name", "").endswith(suffix)]


def test_vruuns_aftermath_empowers_3_more_on_the_warcries_that_find_a_wall():
    cry = Skill("Infernal Cry", 2, support_stats={models.VRUUN_STAT: 3})
    wall = Skill("Frost Wall", 3, spell=True)
    # (the cry once every 4 s, the wall once every 8 s: half the cries come after a fresh wall)
    groups = {2: {"name": "Infernal Cry", "speed": 0.25}, 3: {"name": "Frost Wall", "speed": 0.125}}
    mods, notes = models.vruun_aftermath_model([cry, wall], {}, None, groups)
    assert _named(mods, "EmpoweredAttacks") == [("InfernalEmpoweredAttacks", pytest.approx(1.5))]
    assert notes[0]["entry"] == models.VRUUN_ENTRY and "50%" in notes[0]["basis"]
    # (a wall every 2 s: every cry finds one)
    groups[3]["speed"] = 0.5
    assert _named(models.vruun_aftermath_model([cry, wall], {}, None, groups)[0], "EmpoweredAttacks") == [
        ("InfernalEmpoweredAttacks", 3)]


def test_vruuns_aftermath_counts_nothing_without_a_crystal_or_fragment_maker_and_names_the_uncounted_ones():
    cry = Skill("Seismic Cry", 2, support_stats={models.VRUUN_STAT: 3})
    shred = Skill("Shred", 4, attack=True)
    mods, notes = models.vruun_aftermath_model([cry, shred], {}, None, {2: {"name": "Seismic Cry", "speed": 0.2}})
    assert mods == [] and notes[0]["value"] == 0 and "Shred" in notes[0]["basis"]


def test_vruuns_aftermath_a_small_maker_builds_up_to_its_limit():
    cry = Skill("Infernal Cry", 2, support_stats={models.VRUUN_STAT: 3})
    locus = Skill("Frozen Locus", 3, spell=True)  # one crystal, one allowed: never 5 alone
    groups = {2: {"name": "Infernal Cry", "speed": 0.25}, 3: {"name": "Frozen Locus", "speed": 1.0}}
    mods, _ = models.vruun_aftermath_model([cry, locus], {}, None, groups)
    assert _named(mods, "EmpoweredAttacks") == [("InfernalEmpoweredAttacks", pytest.approx(3 * 1 / 5))]


def test_uhtreds_rite_gives_overflowing_chalice_at_its_uptime():
    skill = Skill("Shield Wall", 2, cooldown=4.0, cooldown_recovery=1.0,
                  support_stats={models.UHTRED_RITE_STAT: 1000})
    # (used once every 8 s, 4 s of Chalice a use: up half the fight)
    mods, notes = models.uhtred_rite_model([skill], {}, None, {2: {"name": "Shield Wall", "speed": 0.125}})
    got = {(m["name"], m["type"]): m["value"] for m in mods}
    assert got == {("LifeFlaskChargesGenerated", "BASE"): pytest.approx(1.0),
                   ("ManaFlaskChargesGenerated", "BASE"): pytest.approx(1.0), ("FlaskRecovery", "INC"): pytest.approx(15.0)}
    assert notes[0]["entry"] == models.UHTRED_RITE_ENTRY and notes[0]["value"] == "50% uptime"
    # (on cooldown: always up, capped at 100%)
    mods, _ = models.uhtred_rite_model([skill], {}, None, {2: {"name": "Shield Wall", "speed": 0.3}})
    assert {m["name"]: m["value"] for m in mods}["LifeFlaskChargesGenerated"] == pytest.approx(2.0)


def test_primal_strikes_combo_adds_its_spirits_on_a_shocked_boss_only():
    sets = [{"label": "Normal Strikes", "TotalDPS": 100.0, "Speed": 2.0},  # 50 a hit in 0.5 s
            {"label": "Spirits", "TotalDPS": 40.0, "Speed": 2.0},  # 20 a spirit
            {"label": "Final Strike", "TotalDPS": 120.0, "Speed": 1.0}]  # 120 in 1 s
    plain = (50 + 50 + 120) / 2.0
    mods, notes = models.combo_sequence_model("Primal Strikes", "Normal Strikes", sets)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (plain / 100 - 1), abs=0.01)]
    assert "spirits not counted" in notes[0]["basis"]
    reach = (2 * models.DEFAULT_BOSS_RADIUS + models.PRIMAL_WIDTH) / models.PRIMAL_SPACING  # (0.3 m: 1.5 spirits)
    assert reach == pytest.approx(1.5)
    spirits = 0.5 * (2 + reach * 1.5) * 20
    mods, notes = models.combo_sequence_model("Primal Strikes", "Spirits", sets, shocked=0.5)
    assert [m["value"] for m in mods] == [pytest.approx(100 * ((plain + spirits / 2.0) / 40 - 1), abs=0.01)]


def test_ember_fusillade_fires_as_a_fusillade_after_the_casts_not_one_ember_a_cast():
    ember = Skill("Ember Fusillade", 1, spell=True,
                  level_stats={models.EMBER_CAP_STAT: 10, models.EMBER_MORE_STAT: 5})
    stats = {"Speed": 2.5, "Duration": 1.3, "ProjectileCount": 1}
    best = 10 * (1 + 0.05 * 9 / 2) / (10 / 2.5 + 1.3)  # ten casts, then the wait
    mods, notes = models.ember_fusillade_model(ember, stats)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (best / 2.5 - 1), abs=0.01)]  # an overcount cut
    assert notes[0]["entry"] == models.EMBER_ENTRY and "10 casts" in notes[0]["basis"]
    # (three Embers a cast: four casts reach the cap, more than PoB's one a cast)
    mods, _ = models.ember_fusillade_model(ember, {**stats, "ProjectileCount": 3})
    assert mods[0]["value"] > 0
    assert models.ember_fusillade_model(Skill("Spark", 1), stats) == ([], [])


def test_whirling_slash_builds_three_stages_then_collapses_once():
    sets = [{"label": "Melee", "TotalDPS": 200.0, "Speed": 2.0},  # 100 a slash
            {"label": "Whirlwind", "TotalDPS": 120.0, "Speed": 2.0}]  # 60 a stage-1 collapse
    slash = Skill("Whirling Slash", 1, attack=True, melee=True, stat_set="Whirlwind")
    average = (3 * 100 + 60 * (1 + 1.5 * 2)) / (3 / 2.0)  # 360 DPS
    mods, notes = models.whirlwind_stage_model(slash, sets)
    # (GEM22: PoB's own stage multiplier at 2, so it scores the stage-3 collapse (120 x 4) and its ailments; the DPS line
    # is the cycle's rate alone)
    assert mods[0] == {**mods[0], "name": models.WHIRL_MULTIPLIER, "type": "BASE", "value": 2}
    assert [m["value"] for m in mods[1:]] == [pytest.approx(100 * (average / (120 * 4) - 1), abs=0.01)]
    assert notes[0]["entry"] == models.WHIRL_ENTRY and "360 DPS" in notes[0]["play"]
    # (the next pass reads the sets with the multiplier in: the same line)
    staged = [sets[0], {**sets[1], "TotalDPS": 480.0}]
    again, _ = models.whirlwind_stage_model(slash, staged, previous=mods)
    assert again[1]["value"] == pytest.approx(mods[1]["value"])
    # (the slash's own set scored: the line against it, the multiplier still set for the Whirlwind set)
    melee = Skill("Whirling Slash", 1, attack=True, melee=True, stat_set="Melee")
    mods, _ = models.whirlwind_stage_model(melee, sets)
    assert [m["value"] for m in mods] == [2, pytest.approx(100 * (average / 200 - 1), abs=0.01)]
    assert models.whirlwind_stage_model(Skill("Ice Strike", 1, stat_set="Melee"), sets) == ([], [])


def test_whirlwind_stage_more_read_as_quality_on_top_of_the_whirlwind_sets_150():
    sets = [{"label": "Melee", "TotalDPS": 200.0, "Speed": 2.0}, {"label": "Whirlwind", "TotalDPS": 120.0, "Speed": 2.0}]
    slash = Skill("Whirling Slash", 1, attack=True, melee=True, stat_set="Whirlwind",
                  level_stats={models.WHIRL_MORE_STAT: 40})  # (the first set's levelStats: the quality part only)
    average = (3 * 100 + 60 * (1 + 1.9 * 2)) / (3 / 2.0)
    mods, _ = models.whirlwind_stage_model(slash, sets)
    assert [m["value"] for m in mods] == [2, pytest.approx(100 * (average / (120 * 4.8) - 1), abs=0.01)]


def test_meteors_fall_at_the_games_rate_with_every_15th_large_and_walking_calamitys_uptime():
    sets = [{"label": "Small Meteor", "TotalDPS": 125.0, "AverageDamage": 100.0},
            {"label": "Large Meteor", "TotalDPS": 375.0, "AverageDamage": 300.0}]  # (PoB at 1.25/s)
    meteors = Skill("Meteors", 1, attack=True, triggered=True, stat_set="Large Meteor")
    active = 100 / 0.15 * 14 / 15 + 300 / 0.15 / 15
    mods, notes = models.meteor_rate_model(meteors, sets, 0.5, "half")
    assert [m["value"] for m in mods] == [pytest.approx(100 * (active * 0.5 / 375 - 1), abs=0.01)]
    assert notes[0]["entry"] == models.METEOR_ENTRY and "50%" in notes[0]["basis"]
    # (GEM22: strongest_ailment_model reads 1 in 15 large at the game's meteors a second, not PoB's per-set timing)
    assert notes[0]["set_shares"] == {"Small Meteor": pytest.approx(14 / 15), "Large Meteor": pytest.approx(1 / 15)}
    assert notes[0]["set_uses"] == pytest.approx(0.5 / 0.15)
    stars = [{"label": "Small Star", "TotalDPS": 1000.0, "AverageDamage": 300.0},
             {"label": "Large Star", "TotalDPS": 450.0, "AverageDamage": 900.0}]
    starfall = Skill("Starfall", 1, spell=True, triggered=True, stat_set="Small Star")
    mods, notes = models.meteor_rate_model(starfall, stars)  # (its buff's duration unknown: always up, as PoB)
    expected = 300 / 0.3 * 14 / 15 + 900 / 0.3 / 15
    assert [m["value"] for m in mods] == [pytest.approx(100 * (expected / 1000 - 1), abs=0.01)]
    assert "UNVERIFIED" in notes[0]["basis"]
    # (no Glory source counted for Walking Calamity: PoB's number kept, never a zero on an uncounted source)
    mods, notes = models.meteor_rate_model(meteors, sets, 0.0, "no source counted")
    assert mods == [] and notes[0]["value"] == "PoB's kept"


def test_rake_steps_back_for_its_dash_only_when_that_beats_staying_close():
    rake = Skill("Rake", 1, attack=True, melee=True, level_stats={models.RAKE_STAT: 100})
    walk = models.VOLT_WALK_SPEED
    slow = {"Speed": 0.5, "MovementSpeedMod": 1.0}  # 2 s a use: 5 m back costs ~1.3 s, doubling the hit pays
    far = 2 / (2 + 5 / walk) / 0.5
    mods, notes = models.rake_distance_model(rake, slow)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (far - 1), abs=0.01)] and far > 1
    mods, notes = models.rake_distance_model(rake, {"Speed": 2.0, "MovementSpeedMod": 1.0})  # fast: stay close
    assert mods == [] and notes[0]["value"] == "x1.000" and notes[0]["entry"] == models.RAKE_ENTRY


def test_leap_slams_quality_is_more_damage_against_fully_broken_armour():
    slam = Skill("Leap Slam", 1, attack=True, melee=True,
                 level_stats={"active_skill_damage_+%_final_vs_fully_broken_armour": 20})
    mods, notes = models.gem_stat_model([slam], main=slam)
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods
            if "Fully Broken" in m["line"]] == [("1% more Damage with Hits against Enemies with Fully Broken Armour", 20,
                                          "Leap Slam")]


def test_frost_bombs_exposure_stops_above_its_level_cap_and_follows_its_casts():
    low = Skill("Frost Bomb", 2, spell=True, level_stats={models.FROST_BOMB_LEVEL_STAT: 78})
    main = Skill("Frostbolt", 1, spell=True)
    groups = {2: {"index": 2, "name": "Frost Bomb", "speed": 1 / 6, "_press": None}}
    mods, notes = models.frost_bomb_exposure_model([main, low], {"Speed": 2.0}, main, groups, boss_level=82)
    assert mods == [models.raw_mod("DebuffEffect", "MORE", -100, "Frost Bomb")] and notes[0]["value"] == "none"
    high = Skill("Frost Bomb", 2, spell=True, level_stats={models.FROST_BOMB_LEVEL_STAT: 100})
    mods, notes = models.frost_bomb_exposure_model([main, high], {"Speed": 2.0}, main, groups, boss_level=82)
    # on its 6 s cooldown: 12 s a cast covers it; GEM14: a pulse every 0.5 s (LOW) is 8 small pulses, 20 + 16 = 36%,
    # held between casts (recast within 8 s): x36/20 of PoB's 20%
    assert [m["value"] for m in mods] == [pytest.approx(80, abs=0.01)] and notes[0]["value"] == "100% uptime at 36%"
    assert models.frost_bomb_magnitude(1 / 6) == (36.0, 8)
    assert models.frost_bomb_magnitude(1 / 24)[0] == pytest.approx((4 * 28 + 8 * 36) / 12)  # a 4 s ramp, then 8 s
    mods, notes = models.frost_bomb_exposure_model([main, low], {"Speed": 2.0}, main, groups, boss_level=75)
    assert [m["value"] for m in mods] == [pytest.approx(80, abs=0.01)]  # (a boss at or under the cap: the growth only)
    rare = {2: {**groups[2], "_press": 0.25}}  # pressed a quarter of its cooldowns: one cast / 24 s, up 12 / 24
    mods, notes = models.frost_bomb_exposure_model([main, high], {"Speed": 2.0}, main, rare, boss_level=82)
    average = (4 * 28 + 8 * 36) / 12  # (no recast within 8 s: each window ramps again)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (0.5 * average / 20 - 1), abs=0.01)]
    mods, notes = models.frost_bomb_exposure_model([main, high], {"Speed": 2.0}, main, {}, boss_level=82)
    assert (mods, notes) == ([], [])  # (no rate read: PoB's number kept, never zeroed)
    assert "Frost Bomb" in models.gem_stat_skills()  # (its level stats are read)
    assert 2 in models.gem_stat_side_groups([main, high], main)


def test_his_scattering_calamity_fires_its_wellsprites_as_a_fusillade_like_ember_fusillade():
    hsc = Skill("His Scattering Calamity", 1, spell=True,
                level_stats={models.EMBER_CAP_STAT: 8, models.EMBER_MORE_STAT: 10})
    speed, wait = 2.5, 0.7
    best = max((min(8, k) * (1 + 0.10 * (min(8, k) - 1) / 2) / (k / speed + wait), k) for k in range(1, 9))
    mods, notes = models.ember_fusillade_model(hsc, {"Speed": speed, "Duration": wait, "ProjectileCount": 1})
    assert [m["value"] for m in mods] == [pytest.approx(100 * (best[0] / speed - 1), abs=0.01)]
    assert mods[0]["skill"] == "His Scattering Calamity" and "Wellsprites" in notes[0]["label"]
    assert notes[0]["entry"] == "model-gem13-scattering-calamity-fusillade"
    assert models.ember_fusillade_model(hsc, {"Speed": speed}, forbidden={notes[0]["entry"]}) == ([], [])


def test_plasma_blasts_quality_is_more_shock_duration():
    blast = Skill("Plasma Blast", 1, attack=True, level_stats={"active_skill_shock_duration_+%_final": 30})
    mods, notes = models.gem_stat_model([blast], main=blast)
    assert [(m["line"], m["scale"], m.get("skill")) for m in mods
            if "Shock" in m["line"]] == [("1% more Shock Duration", 30, "Plasma Blast")]


def test_shield_charge_steps_back_only_where_its_distance_could_pay():
    """GEM14: no source gives Shield Charge's travel distance; stepping back pays only below t0 x walk (an instant
    charge), and a charge covers at least 1.3 s x walking speed, so at a short use time PoB's 0% stays (a note)."""
    charge = Skill("Shield Charge", 1, attack=True)
    mods, notes = models.shield_charge_distance_model(charge, {"Speed": 1 / 0.6})
    assert mods == [] and notes[0]["value"] == "x1.000" and "never pays" in notes[0]["basis"]
    mods, notes = models.shield_charge_distance_model(charge, {"Speed": 1 / 1.9})  # (a use time with the channel in it)
    assert mods == [] and notes[0]["value"] == "not counted"
    assert models.shield_charge_distance_model(Skill("Rake", 1), {"Speed": 2.0}) == ([], [])


def test_cast_speed_raises_a_channelled_skills_per_minute_cost():
    """GEM15: "Modifiers to cast speed also affect this skill's cost" (Bonestorm, Flameblast, Volcano): each per-minute cost
    is overridden to its raw cost x the cast speed multiplier; upfront costs and skills without the stat are left alone."""
    from builder.search import models

    bone = models.Skill("Bonestorm", over_time_cost={"perMinute": {"ManaPerMinute": 3634}, "castSpeed": 1.32})
    volcano = models.Skill("Volcano", over_time_cost={"perMinute": {"ManaPerMinute": 3680}, "castSpeed": 1.0})
    other = models.Skill("Incinerate")
    mods, notes = models.over_time_cost_model([bone, volcano, other, bone])
    assert mods == [models.raw_mod("BaseManaPerMinuteCostOverride", "OVERRIDE", 3634 * 1.32, "Bonestorm")]
    assert [n["value"] for n in notes] == ["x1.32"] and notes[0]["entry"] == models.OVER_TIME_COST_ENTRY
    assert models.over_time_cost_model([bone], {models.OVER_TIME_COST_ENTRY}) == ([], [])
    slow = models.Skill("Flameblast", over_time_cost={"perMinute": {"ManaPerMinute": 3450}, "castSpeed": 0.8})
    assert models.over_time_cost_model([slow])[0][0]["value"] == 3450 * 0.8


def test_resonating_shield_gets_no_warcry_empowers():
    """GEM15: "Cannot be Empowered by Warcries": each warcry's EmpoweredAttacks pushed below 0 on it, nothing elsewhere."""
    shield, spark = Skill("Resonating Shield", 1, attack=True, melee=True), Skill("Spark", 1, spell=True)
    mods, notes = models.warcry_proof_model(shield)
    assert {m["name"] for m in mods} >= {"InfernalEmpoweredAttacks", "FortifyingEmpoweredAttacks", "AncestralEmpoweredAttacks"}
    assert all(m["value"] < 0 and m["skill"] == "Resonating Shield" and m["type"] == "BASE" for m in mods)
    assert notes[0]["entry"] == models.WARCRY_PROOF_ENTRY
    assert models.warcry_proof_model(spark) == ([], [])
    assert models.warcry_proof_model(shield, {models.WARCRY_PROOF_ENTRY}) == ([], [])


@pytest.mark.parametrize("speed, duration, press, more", [
    (0.1, 4.0, None, 20.0),   # a storm every 10 s lasting 4 s: 40% of the fight, x 50%
    (1.0, 6.0, 0.5, 50.0),    # pressed half the time, still always up
    (0.0, 6.0, None, 0.0),    # no use rate: not counted
])
def test_overgrown_plants_follow_the_builds_thunderstorm(speed, duration, press, more):
    vine, storm = Skill("Vine Arrow", 1, attack=True), Skill("Thunderstorm", 2, spell=True)
    groups = {2: {"speed": speed, "duration": duration, "_press": press, "name": "Thunderstorm"}}
    assert models.gem_stat_side_groups([vine, storm], vine) == [2]
    mods, notes = models.overgrown_model([vine, storm], vine, groups)
    assert [m["value"] for m in mods] == ([pytest.approx(more)] if more else [])
    assert notes[0]["entry"] == models.OVERGROWN_ENTRY and "Without it: +0" in notes[0]["play"]


def test_overgrown_needs_a_plant_main_skill():
    spark, storm = Skill("Spark", 1, spell=True), Skill("Thunderstorm", 2, spell=True)
    assert models.overgrown_model([spark, storm], spark, {2: {"speed": 1, "duration": 5}}) == ([], [])
    assert models.gem_stat_side_groups([spark, storm], spark) == [2]  # (read for its Drenched: drenched_model)
    vine = Skill("Vine Arrow", 1, attack=True)
    mods, notes = models.overgrown_model([vine], vine, {})
    assert mods == [] and notes[0]["value"] == "not counted"


@pytest.mark.parametrize("texts, speed, more, conf", [
    (["Plants have a 20% chance to immediately Overgrow"], 0.0, 10.0, "MEDIUM"),       # Boundless Growth alone: 20% x 50
    (["Plants have a 25% chance to immediately Overgrow when they enter your Presence for the first time"], 0.0, 12.5,
     "LOW"),                                                                            # Idol of Yeena alone
    (["Plants have a 20% chance to immediately Overgrow"], 0.05, 30.0, "LOW"),         # + a storm half the fight: 1-.8x.5
    (["Plants have a 20% chance to immediately Overgrow",
      "Plants have a 25% chance to immediately Overgrow when they enter your Presence for the first time"], 0.0, 20.0,
     "LOW"),                                                                            # 1 - .8 x .75 = 40%
    (["Plants have a 20% chance to immediately Overgrow when Wet"], 0.0, None, "LOW"),   # not the line: nothing
])
def test_overgrown_plants_count_the_builds_chance_to_overgrow(texts, speed, more, conf):
    vine, storm = Skill("Vine Arrow", 1, attack=True), Skill("Thunderstorm", 2, spell=True)
    groups = {2: {"speed": speed, "duration": 10.0, "name": "Thunderstorm"}}
    mods, notes = models.overgrown_model([vine, storm], vine, groups, texts=texts)
    assert [m["value"] for m in mods] == ([pytest.approx(more)] if more else [])
    assert notes[0]["confidence"] == conf and notes[0]["entry"] == models.OVERGROWN_ENTRY
    if more:
        assert models.overgrown_share([vine, storm], groups, texts)[0] == pytest.approx(more / 50)


FIRESTORM_SETS = [{"label": "Firestorm", "TotalDPS": 1000.0}, {"label": "Ice Bolts", "TotalDPS": 800.0},
                  {"label": "Lightning Bolts", "TotalDPS": 1500.0}]


@pytest.mark.parametrize("scored, shares, low, high, inc", [
    ("Firestorm", {}, None, 1.0, None),                         # no Infusions: PoB's fire set as it is
    ("Firestorm", {"Fire": 1.0}, 4 / 3, 4 / 3, 100.0),          # every cast Fire-Infused: 0.1 s bolts held to 0.15 s
    ("Firestorm", {"Cold": 1.0}, 1.8 * (1 / 0.15) / 10, 1.8, None),  # ice bolts added; shared limit 6.67 of 10 hits/s
    ("Firestorm", {"Fire": 0.5, "Lightning": 0.5}, None, None, 50.0),
])
def test_firestorm_counts_its_infusions_bolts_and_bonuses(scored, shares, low, high, inc):
    storm = Skill("Firestorm", 1, spell=True)
    mods, notes = models.firestorm_infusion_model(storm, scored, FIRESTORM_SETS, {e: (v, "test") for e, v in shares.items()})
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    assert more == ([] if low is None and not shares else more)
    if low is not None:
        assert (1 + more[0] / 100 if more else 1.0) == pytest.approx(low, rel=1e-4)
    if high is not None:
        assert f"x{high:.3f}" in notes[0]["basis"]
    assert [m["value"] for m in mods if m["name"] == "Damage"] == ([pytest.approx(inc)] if inc else [])
    assert notes[0]["entry"] == models.FIRESTORM_ENTRY and "Without Infusions" in notes[0]["play"]


@pytest.mark.parametrize("scored", ["Ice Bolts", "Lightning Bolts"])
def test_firestorms_bolt_sets_never_score_alone(scored):  # (the OVERCOUNT, probe POE2T2164: Lightning Bolts 477 vs 409)
    mods, notes = models.firestorm_infusion_model(Skill("Firestorm", 1, spell=True), scored, FIRESTORM_SETS,
                                                  {"Cold": (1.0, "test")})
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    total = (1000 + 800) * (1 / 0.15) / 10  # (fire + ice bolts, the shared limit's 6.67 of 10 hits a second)
    dps = {s["label"]: s["TotalDPS"] for s in FIRESTORM_SETS}
    assert 1 + more[0] / 100 == pytest.approx(total * models.FIRESTORM_BOLTS_MARGIN / dps[scored], rel=1e-4)


def test_firestorm_rate_scale_readings():
    assert models.firestorm_rate_scale(0.0, 0.0, True) == pytest.approx(1.0)
    assert models.firestorm_rate_scale(1.0, 1.0, False) == pytest.approx(4 / 3)        # per type: each stream x1.33
    assert models.firestorm_rate_scale(1.0, 1.0, True) == pytest.approx((1 / 0.15) / 2 / 5)  # shared: 6.67 over two
    assert models.firestorm_infusion_model(Skill("Spark", 1, spell=True), "Firestorm", FIRESTORM_SETS, {}) == ([], [])


def test_firestorm_and_frost_darts_give_their_parts_shares_for_the_strongest_ailment():
    """GEM23: the notes carry set_shares (strongest_ailment_model): Firestorm's fire bolts always, its ice / lightning
    bolts on their Infused casts' share, at the headline's hits a second; Frost Darts' parts a cast."""
    sets = [dict(row, Speed=5.0) for row in FIRESTORM_SETS]
    _, notes = models.firestorm_infusion_model(Skill("Firestorm", 1, spell=True), "Firestorm", sets,
                                               {"Fire": (0.5, "t"), "Cold": (0.25, "t"), "Lightning": (0.0, "t")})
    assert notes[0]["set_shares"] == {"Firestorm": 1.0, "Ice Bolts": 0.25, "Lightning Bolts": 0.0}
    assert notes[0]["set_uses"] == pytest.approx(5.0 * models.firestorm_rate_scale(0.5, 0.25, True))
    darts = [{"label": "Projectile", "TotalDPS": 100.0, "Speed": 1.0}, {"label": "Explosion", "TotalDPS": 300.0, "Speed": 1.0},
             {"label": "Ice Chunk", "TotalDPS": 50.0, "Speed": 1.0}]
    _, notes = models.frost_darts_model("Frost Darts", "Projectile", darts, 3, 0.5, "x", 0.25, "y", 0.3)
    q = models.frost_chunk_chance(0.3)
    # (MECHB44: all 3 darts hit a lone target, the user's in-game test: each part's uses a cast x the darts)
    assert notes[0]["set_shares"] == {"Projectile": 3.0, "Explosion": 0.75,
                                      "Ice Chunk": pytest.approx(3 * (0.5 * 2 + 0.25 * 3) * q)}
    assert "set_uses" not in notes[0]  # (PoB's casts a second)


THUNDER_SETS = [{"label": "Melee", "TotalDPS": 127.5}, {"label": "Projectile", "TotalDPS": 162.3}]


@pytest.mark.parametrize("scored, charges, factor", [
    ("Projectile", 0, 127.5 * 0.9 / 162.3),   # the OVERCOUNT (probe POE2T2172): no charges, no projectiles; steer to Melee
    ("Melee", 0, None),                        # the slam alone: PoB's number
    ("Melee", 3, (127.5 + 162.3) / 127.5),     # with charges one use does both
    ("Projectile", 3, (127.5 + 162.3) / 162.3),
])
def test_falling_thunder_needs_power_charges_for_its_projectiles(scored, charges, factor):
    mods, notes = models.falling_thunder_model(Skill("Falling Thunder", 1, attack=True), scored, THUNDER_SETS, charges)
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    assert (1 + more[0] / 100 if more else None) == (pytest.approx(factor, rel=1e-4) if factor else None)
    assert notes[0]["entry"] == models.FALLING_THUNDER_ENTRY and "Without them" in notes[0]["play"]
    assert models.falling_thunder_model(Skill("Spark", 1, spell=True), scored, THUNDER_SETS, charges) == ([], [])


@pytest.mark.parametrize("level_cap, speed, duration, more", [
    (78, 1.0, 12.0, -100.0),   # gem level 17's cap 78 under a level-82 boss: no Drenched
    (0, 0.05, 10.0, -50.0),    # no cap; a storm every 20 s lasting 10 s: half the fight
    (0, 1.0, 12.0, None),      # always up: PoB's number, a note only
])
def test_thunderstorms_drenched_follows_its_level_cap_and_casts(level_cap, speed, duration, more):
    spark = Skill("Spark", 1, spell=True)
    storm = Skill("Thunderstorm", 2, spell=True, level_stats={models.DRENCH_LEVEL_STAT: level_cap})
    mods, notes = models.drenched_model([spark, storm], {}, spark, {2: {"speed": speed, "duration": duration}}, 82)
    assert [m["value"] for m in mods] == ([pytest.approx(more)] if more is not None else [])
    assert all(m["name"] == "BuffEffect" and m["skill"] == "Thunderstorm" for m in mods)
    assert notes[0]["entry"] == models.DRENCH_ENTRY


def test_thunderstorm_without_a_use_rate_keeps_pobs_drenched():
    spark, storm = Skill("Spark", 1, spell=True), Skill("Thunderstorm", 2, spell=True)
    assert models.drenched_model([spark, storm], {}, spark, {}, 82) == ([], [])


ICE_SETS = [{"label": "Storm", "TotalDPS": 182.0}, {"label": "Improved Bolt", "TotalDPS": 1110.0}]


@pytest.mark.parametrize("scored, casts, chilled, frozen, factor", [
    ("Improved Bolt", 0.7, 0.0, 0.0, 182.0 / 1110 * 0.9),  # (probe POE2T2184: nothing to consume, steered to the Storm)
    ("Storm", 0.7, 0.0, 0.0, None),                          # the plain storm: PoB's number
    ("Storm", 0.7, 0.5, 0.0, (182 * (1 - 0.0525) + 1110 * 0.0525) / 182),  # 0.35 bolts/s: 5.25% of the hits
    ("Storm", 0.7, 0.0, 0.5, 1110.0 / 182),                  # a Frozen boss half the time: 7 bolts/s, past the limit
    ("Improved Bolt", 0.7, 0.0, 0.5, None),                  # the same, on the Improved set: PoB's number
])
def test_icestorm_makes_improved_bolts_only_from_chill_and_freeze_it_consumes(scored, casts, chilled, frozen, factor):
    mods, notes = models.icestorm_model(Skill("Icestorm", 1, spell=True), scored, ICE_SETS, casts, chilled, frozen, 20.0,
                                        "test")
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    assert (1 + more[0] / 100 if more else None) == (pytest.approx(factor, rel=1e-4) if factor else None)
    assert notes[0]["entry"] == models.ICESTORM_ENTRY and "Without" in notes[0]["play"]
    assert models.icestorm_model(Skill("Spark", 1, spell=True), scored, ICE_SETS, casts, chilled, frozen, 20.0, "") == ([], [])


SLAM_SETS = [{"label": "Base", "TotalDPS": 52.4}, {"label": "Enraged", "TotalDPS": 111.0}]


@pytest.mark.parametrize("scored, share, factor", [
    ("Enraged", 0.0, 52.4 / 111.0 * 0.9),   # (probe POE2T2184: no Rage, steered to the Base set)
    ("Base", 0.0, None),                     # PoB's number
    ("Base", 0.5, (52.4 * 0.5 + 111.0 * 0.5) / 52.4),
    ("Enraged", 1.0, None),                  # every slam spends Rage: PoB's number
])
def test_furious_slams_enraged_part_needs_rage(scored, share, factor):
    mods, notes = models.gated_set_model(Skill("Furious Slam", 1, attack=True), scored, SLAM_SETS, share, "test")
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    assert (1 + more[0] / 100 if more else None) == (pytest.approx(factor, rel=1e-4) if factor else None)
    assert notes[0]["entry"] == "model-gem17-furious-slam-rage" and "Without Rage" in notes[0]["play"]
    assert models.gated_set_model(Skill("Furious Slam", 1), scored, SLAM_SETS, share, "", {notes[0]["entry"]}) == ([], [])


def test_gated_set_shares_follow_the_builds_resource():
    assert models.furious_slam_share(5.0, 1.0, 30.0) == pytest.approx(0.5)  # 5 Rage/s over 10 a slam at 1 slam/s
    assert models.furious_slam_share(50.0, 1.0, 30.0) == 1.0
    assert models.furious_slam_share(50.0, 1.0, 5.0) == 0.0  # can't hold 10 Rage
    assert models.oil_barrage_share(0.4) == pytest.approx(0.6)  # 0.4 charges/s x 1.5 s
    assert models.oil_barrage_share(0.0) == 0.0


SNIPE_SETS = [{"label": "Arrow", "TotalDPS": 45.6}, {"label": "Explosion", "TotalDPS": 308.8},
              {"label": "Icy Blast", "TotalDPS": 945.1}]
SUNDER_SETS = [{"label": "Pulse", "TotalDPS": 0}, {"label": "Cold", "TotalDPS": 634.4}, {"label": "Fire", "TotalDPS": 151.0},
               {"label": "Lightning", "TotalDPS": 169.3}]


@pytest.mark.parametrize("skill, sets, scored, up, factor", [
    ("Snipe", SNIPE_SETS, "Icy Blast", {}, (45.6 + 308.8) / 945.1 * 0.9),  # (probe POE2T2190: no Freeze, steered)
    ("Snipe", SNIPE_SETS, "Explosion", {}, (45.6 + 308.8) / 308.8),       # arrow + explosion together
    ("Snipe", SNIPE_SETS, "Explosion", {"Freeze": 0.5}, (45.6 + 308.8 * 0.5 + 945.1 * 0.5) / 308.8),
    ("Elemental Sundering", SUNDER_SETS, "Cold", {}, models.AILMENT_FLOOR),  # nothing to consume: no damage (held at 1%)
    ("Elemental Sundering", SUNDER_SETS, "Cold", {"Freeze": 0.2, "Ignite": 0.5, "Shock": 1.0},
     (634.4 * 0.2 + 151.0 * 0.5 + 169.3) / 634.4),
])
def test_snipe_and_sundering_need_ailments_on_the_boss(skill, sets, scored, up, factor):
    mods, notes = models.ailment_set_model(Skill(skill, 1), scored, sets, up, "test")
    more = [m["value"] for m in mods if m["name"] == "DPS"]
    assert 1 + more[0] / 100 == pytest.approx(factor, rel=1e-4, abs=1e-6)
    assert notes[0]["entry"] == models.AILMENT_SETS[skill] and "Without" in notes[0]["play"]
    assert models.ailment_set_model(Skill(skill, 1), scored, sets, up, "", {models.AILMENT_SETS[skill]}) == ([], [])


LANCE_SETS = [{"label": "Projectile", "TotalDPS": 30.7}, {"label": "Frenzy-Charged", "TotalDPS": 143.4},
              {"label": "Lodged Spear", "TotalDPS": 35.9}, {"label": "Detonation", "TotalDPS": 107.1}]


@pytest.mark.parametrize("scored, uses, detonations, factor", [
    ("Detonation", 1.0, 0.0, 35.9 / 107.1 * 0.9),         # (probe POE2T2190: no Detonator, steered to the plain set)
    ("Lodged Spear", 1.0, 0.0, None),                       # PoB's number
    ("Lodged Spear", 1.0, 0.1, (35.9 + 107.1 * 0.3) / 35.9),  # 3 spears lying, a Detonator every 10 s: 30%
    ("Detonation", 1.0, 1.0, (35.9 + 107.1) / 107.1),        # every spear detonated: plain + detonation
])
def test_storm_lances_detonation_needs_a_detonator(scored, uses, detonations, factor):
    mods, notes = models.detonated_set_model(Skill("Storm Lance", 1, attack=True), scored, LANCE_SETS, uses, detonations,
                                             "test")
    more = [m["value"] for m in mods if m["name"] in ("DPS", "Damage")]
    assert (1 + more[0] / 100 if more else None) == (pytest.approx(factor, rel=1e-4) if factor else None)
    assert all(m["name"] == ("Damage" if scored == "Detonation" and not detonations else "DPS") for m in mods)
    assert notes[0]["entry"] == "model-gem17-storm-lance-detonation" and "Without one" in notes[0]["play"]
    assert models.detonated_set_model(Skill("Storm Lance", 1), "Frenzy-Charged", LANCE_SETS, uses, detonations, "") == ([], [])


def test_detonated_share_counts_the_spears_lying_there():
    assert models.detonated_share("Rapid Assault", 1.0, 0.1) == pytest.approx(0.5)  # 5 spearheads x 0.1/s over 1 use/s
    assert models.detonated_share("Rapid Assault", 0.1, 0.05) == 1.0  # 3 lying (0.1/s x 30 s), 0.15/s >= 0.1/s
    assert models.detonated_share("Storm Lance", 0.1, 0.05) == pytest.approx(0.3)  # 0.6 spears lying x 0.05 / 0.1
    assert models.detonated_share("Storm Lance", 1.0, 0.0) == 0.0



FLAMEBLAST_LEVEL = {"charged_blast_spell_damage_+%_final_per_stack": 75, "flameblast_maximum_stages": 10,
                    "channel_skill_end_animation_duration_override_ms": 600, "base_minimum_channel_time_ms": 490}


def _flameblast(**kw) -> Skill:
    return Skill("Flameblast", 1, spell=True, cooldown=10.0, level_stats=dict(FLAMEBLAST_LEVEL), **kw)


def _by_name(mods: list[dict]) -> dict:
    return {m["name"]: m["value"] for m in mods}


def test_flameblast_is_channelled_to_full_stages_and_released_once_a_cycle():
    # (GEM18) probe POE2T3405's numbers: cast rate 4.8, PoB's Speed 1 / its 10.032 s cooldown
    mods, notes = models.flameblast_model(_flameblast(), {"CastRate": 4.8, "Speed": 1 / 10.032, "Cooldown": 10.032})
    got = _by_name(mods)
    assert got["Multiplier:FlameblastStage"] == 9  # PoB's 1 + 9 = the 10 stages
    cycle = 10 / 4.8 + 0.6 + 10.032  # the channel, the end animation, the cooldown from the release
    assert got["DPS"] == pytest.approx(100 * ((1 / cycle) * 10.032 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem18-flameblast-stages" and "10 stages" in notes[0]["label"]
    assert all(m["skill"] == "Flameblast" for m in mods)


def test_flameblast_with_a_short_cooldown_is_held_to_its_channel():
    # cast-rate bound in PoB (a release every cast) is the big overcount: one per channel + end animation + cooldown
    mods, _ = models.flameblast_model(_flameblast(), {"CastRate": 4.8, "Speed": 4.8, "Cooldown": 0.1})
    got = _by_name(mods)
    assert got["Multiplier:FlameblastStage"] == 9
    assert got["DPS"] == pytest.approx(100 * (1 / (10 / 4.8 + 0.7) / 4.8 - 1), abs=0.01)


def test_flameblast_releases_early_when_stages_come_slower_than_they_pay():
    # 1 cast a second, no cooldown: (1 + 0.75 s) / (s + 0.6) is highest at 1 stage, PoB's own: no stage line
    mods, notes = models.flameblast_model(Skill("Flameblast", 1, spell=True, level_stats=dict(FLAMEBLAST_LEVEL)),
                                          {"CastRate": 1.0, "Speed": 1.0, "Cooldown": 0})
    got = _by_name(mods)
    assert "Multiplier:FlameblastStage" not in got
    assert got["DPS"] == pytest.approx(100 * (1 / 1.6 - 1), abs=0.01)
    assert "1 stage a channel" in notes[0]["label"]


def test_flameblast_model_leaves_triggered_forbidden_and_other_skills():
    stats = {"CastRate": 4.8, "Speed": 0.1, "Cooldown": 10.0}
    assert models.flameblast_model(_flameblast(triggered=True), stats) == ([], [])
    assert models.flameblast_model(_flameblast(), stats, {"model-gem18-flameblast-stages"}) == ([], [])
    assert models.flameblast_model(Skill("Incinerate", 1), stats) == ([], [])
    assert models.flameblast_model(_flameblast(), {"CastRate": 0, "Speed": 0.1, "Cooldown": 10.0}) == ([], [])


def _bone_sets(strike=53.8, wave=382.1, speed=1.5):
    return [{"label": "Initial Strike", "TotalDPS": strike, "Speed": speed, "AverageDamage": 36.0},
            {"label": "Shockwave", "TotalDPS": wave, "Speed": speed, "AverageDamage": 255.0}]


MAIN_STUN_STATS = {"HeavyStunBuildupAvg": 0.0, "Speed": 1.5, "HitChance": 100, "CritChance": 0, "ColdHitAverage": 500.0}


def test_boneshatter_shockwave_comes_only_when_other_skills_prime_the_boss():
    # (GEM18) probe POE2T3411: the search kept the Shockwave set (382.1 vs 53.8); the Strike's own hit builds no stun
    primed = 2.0  # % of the bar a second from the build's other skills
    flat = {f"{t}{k}Average": 0.0 for t in models.DAMAGE_TYPES for k in ("Hit", "Crit")}
    rate, _ = models.heavy_stun_rate({**MAIN_STUN_STATS, **flat, "PhysicalHitAverage": 1.0, "Speed": 1.0,
                                      "HeavyStunBuildupAvg": primed / 0.70})
    share = min(1.0, rate / 1.5)
    mods, notes = models.stun_set_model(Skill("Boneshatter", 1, attack=True, melee=True), "Shockwave", _bone_sets(),
                                        MAIN_STUN_STATS, primed, "a side skill")
    total = 53.8 + 382.1 * share
    assert 0 < share < 1
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (total / 382.1 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem18-boneshatter-shockwave-heavy-stun"


def test_boneshatter_with_nothing_priming_the_boss_keeps_the_strike():
    mods, notes = models.stun_set_model(Skill("Boneshatter", 1), "Shockwave", _bone_sets(), MAIN_STUN_STATS, 0.0, "none")
    assert mods[0]["name"] == "Damage"  # (the 0.9 steer, as a Damage line so its ailments shrink too)
    assert mods[0]["value"] == pytest.approx(100 * (53.8 * 0.9 / 382.1 - 1), abs=0.01)
    mods, _ = models.stun_set_model(Skill("Boneshatter", 1), "Initial Strike", _bone_sets(), MAIN_STUN_STATS, 0.0, "none")
    assert mods == []  # the strike alone: PoB's own number


def test_stun_uncold_takes_cold_out_and_weighs_physical():
    assert models.stun_uncold({"PhysicalHitAverage": 100.0, "ColdHitAverage": 150.0}) == pytest.approx(0.5)
    assert models.stun_uncold({}) == 1.0


def test_stun_set_model_leaves_forbidden_and_other_skills():
    entry = "model-gem18-wing-blast-shockwave-heavy-stun"
    sets = [{"label": "Wing Blast", "TotalDPS": 58.3, "Speed": 1.0}, {"label": "Shockwave", "TotalDPS": 330.5, "Speed": 1.0}]
    assert models.stun_set_model(Skill("Wing Blast", 1), "Shockwave", sets, MAIN_STUN_STATS, 1.0, "", {entry}) == ([], [])
    assert models.stun_set_model(Skill("Sunder", 1), "Wave", sets, MAIN_STUN_STATS, 1.0, "") == ([], [])
    assert models.stun_set_model(Skill("Wing Blast", 1), "Shockwave", sets, MAIN_STUN_STATS, 1.0, "")[1][0]["entry"] == entry


def test_stormblast_bolts_explode_only_when_detonated():
    # (GEM18) probe POE2T3411: Explosion 275.3 vs Impact 31.0, kept with no Detonator
    sets = [{"label": "Impact", "TotalDPS": 31.0}, {"label": "Explosion", "TotalDPS": 275.3}]
    sb = Skill("Stormblast Bolts", 1, attack=True)
    mods, notes = models.detonated_set_model(sb, "Explosion", sets, 1.2, 0.0, "no Detonator")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (31.0 * 0.9 / 275.3 - 1), abs=0.01)
    assert "bolts" in notes[0]["play"] and "spears" not in notes[0]["play"]
    # 10 shots' bolts at most (30 / 3), 10 s each: at 0.5 Detonator uses a second every bolt goes off
    assert models.detonated_share("Stormblast Bolts", 1.2, 0.5) == 1.0
    assert models.detonated_share("Stormblast Bolts", 1.2, 0.05) == pytest.approx(0.05 * 10 / 1.2)


def test_siphoning_strike_shockwave_at_the_shocked_share():
    # (GEM19) probe POE2T3411: the search kept the Shockwave set (386.2 vs Dash 69.7); the Strike never Shocks
    sets = [{"label": "Dash", "TotalDPS": 69.7}, {"label": "Shockwave", "TotalDPS": 386.2}]
    ss = Skill("Siphoning Strike", 1, attack=True, melee=True)
    mods, notes = models.shock_set_model(ss, "Shockwave", sets, 0.25, "a side skill Shocks")
    total = 69.7 + 386.2 * 0.25
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (total / 386.2 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-siphoning-strike-shock"
    # scored on the Dash with a Shock: the Shockwave adds
    mods, _ = models.shock_set_model(ss, "Dash", sets, 0.25, "")
    assert mods[0]["value"] == pytest.approx(100 * (total / 69.7 - 1), abs=0.01)


def test_shock_sets_with_no_shock_keep_the_plain_set():
    sets = [{"label": "Projectile", "TotalDPS": 41.2}, {"label": "Pulse", "TotalDPS": 183.3}]
    sb = Skill("Shockburst Rounds", 1, attack=True)
    mods, notes = models.shock_set_model(sb, "Pulse", sets, 0.0, "no Shock")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (41.2 * 0.9 / 183.3 - 1), abs=0.01)
    assert "Electrocute" in notes[0]["basis"]
    assert models.shock_set_model(sb, "Projectile", sets, 0.0, "no Shock")[0] == []  # (PoB's own number)


def test_shock_set_model_leaves_forbidden_and_other_skills():
    entry = "model-gem19-shockchain-arrow-lightning-ailment"
    sets = [{"label": "Projectile", "TotalDPS": 95.3}, {"label": "Shockwave", "TotalDPS": 239.0}]
    arrow = Skill("Shockchain Arrow", 1, attack=True)
    assert models.shock_set_model(arrow, "Shockwave", sets, 0.5, "", {entry}) == ([], [])
    assert models.shock_set_model(Skill("Lightning Arrow", 1), "Shockwave", sets, 0.5, "") == ([], [])
    assert models.shock_set_model(arrow, "Shockwave", sets, 0.5, "")[1][0]["entry"] == entry


def test_earthshatter_spikes_shatter_only_on_warcries():
    # (GEM19) probe POE2T3411: Explosion 129.5 vs Slam 25.8, kept with no Warcry
    sets = [{"label": "Slam", "TotalDPS": 25.8}, {"label": "Explosion", "TotalDPS": 129.5}]
    es = Skill("Earthshatter", 1, attack=True, melee=True)
    mods, notes = models.detonated_set_model(es, "Explosion", sets, 1.0, 0.0, "no Warcry in the build")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (25.8 * 0.9 / 129.5 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-earthshatter-warcry-shatter" and "Warcry" in notes[0]["play"]
    # a Warcry every 8 s, a slam a second: 6 spikes lie there, each cry shatters them all: 0.125 x 6 / 1 = 75%
    mods, _ = models.detonated_set_model(es, "Explosion", sets, 1.0, 0.125, "a Warcry")
    assert mods[0]["name"] == "DPS"
    assert mods[0]["value"] == pytest.approx(100 * ((25.8 + 129.5 * 0.75) / 129.5 - 1), abs=0.01)


def test_glacial_lance_explosion_adds_at_the_frenzy_share():
    # (GEM19) probe POE2T3411: Explosion 177.3 vs Spear 95.4, kept with no Frenzy source; the explosion adds to the lance
    sets = [{"label": "Spear", "TotalDPS": 95.4}, {"label": "Explosion", "TotalDPS": 177.3}]
    gl = Skill("Glacial Lance", 1, attack=True)
    mods, notes = models.gated_set_model(gl, "Explosion", sets, 0.4, "a Frenzy source")
    assert mods[0]["name"] == "DPS"
    assert mods[0]["value"] == pytest.approx(100 * ((95.4 + 177.3 * 0.4) / 177.3 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-glacial-lance-frenzy-charge"
    mods, _ = models.gated_set_model(gl, "Explosion", sets, 0.0, "none")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (95.4 * 0.9 / 177.3 - 1), abs=0.01)


def test_fangs_of_frost_explosion_at_the_parried_share():
    # (GEM19) probe POE2T3411: Explosion 205.3 vs Stab 120.7, kept with no Parry
    sets = [{"label": "Stab", "TotalDPS": 120.7}, {"label": "Explosion", "TotalDPS": 205.3}]
    fof = Skill("Fangs of Frost", 1, attack=True, melee=True)
    mods, notes = models.shock_set_model(fof, "Explosion", sets, 0.1, "Parry every 5 s")
    assert mods[0]["value"] == pytest.approx(100 * ((120.7 + 205.3 * 0.1) / 205.3 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-fangs-of-frost-parried" and "Parried" in notes[0]["label"]
    mods, _ = models.shock_set_model(fof, "Explosion", sets, 0.0, "no Parry")
    assert mods[0]["name"] == "Damage"
    assert models.parry_plan([fof], {"Speed": 2.0}, True, True).box == 0.0  # (no Parry skill: none)


def _slam(stage_stats=None):
    return Skill("Supercharged Slam", 1, attack=True, melee=True, stat_set="Impact",
                 level_stats=stage_stats or {"channelled_slam_damage_+%_final_per_stage": 40,
                                             "channelled_slam_max_stages": 3,
                                             "channel_skill_end_animation_duration_override_ms": 1000,
                                             "base_minimum_channel_time_ms": 1000})


def test_supercharged_slam_channels_to_its_stages_with_one_aftershock_each():
    # (GEM19) PoB: one set x(1 + stage), stage 1, no end animation. Impact = Aftershock hit here, 1 s a stage
    sets = [{"label": "Impact", "TotalDPS": 100.0}, {"label": "Aftershock", "TotalDPS": 100.0}]
    mods, notes = models.supercharged_slam_model(_slam(), "Impact", sets, {"HitSpeed": 1.0}, 1)
    # value(s) = (1 + 0.4 s)(1 + s) / (s + 1): s = 3 best (2.2 vs 1.4, 1.8)
    assert mods[0] == models.raw_mod("Multiplier:SuperchargedSlamStage", "BASE", 2, "Supercharged Slam")
    factor = (1 + 3) / (1 + 3) * 3 / (3 + 1)  # Impact + 3 Aftershocks over PoB's x(1 + 3); 3 s / (3 s + 1 s)
    assert mods[1]["name"] == "DPS" and mods[1]["value"] == pytest.approx(100 * (factor - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-supercharged-slam-stages"
    # the next pass, PoB at stage 3 (3 s a release): the same lines (a fixed point)
    again, _ = models.supercharged_slam_model(_slam(), "Impact", sets, {"HitSpeed": 1 / 3}, 3)
    assert again == mods


def test_supercharged_slam_with_weak_aftershocks_keeps_one_stage():
    sets = [{"label": "Impact", "TotalDPS": 100.0}, {"label": "Aftershock", "TotalDPS": 5.0}]
    mods, _ = models.supercharged_slam_model(_slam(), "Impact", sets, {"HitSpeed": 1.0}, 1)
    # s = 1: 1.4 x 1.05 / 2 = 0.735; s = 3: 2.2 x 1.15 / 4 = 0.6325
    assert [m["name"] for m in mods] == ["DPS"]
    assert mods[0]["value"] == pytest.approx(100 * (1.05 / 2 * 1 / 2 - 1), abs=0.01)
    assert models.supercharged_slam_model(_slam(), "Impact", sets, {"HitSpeed": 1.0}, 1,
                                          {"model-gem19-supercharged-slam-stages"}) == ([], [])


def test_supercharged_slam_reads_pobs_per_stage_time():
    # (GEM19) 0.85 s a stage (patch 0.4.0): 3 stages = 2.55 s + 1 s end; at stage 1 the hit time is the 1 s minimum
    sets = [{"label": "Impact", "TotalDPS": 100.0}, {"label": "Aftershock", "TotalDPS": 100.0}]
    mods, _ = models.supercharged_slam_model(_slam(), "Impact", sets, {"HitSpeed": 1.0, "Time": 0.85}, 1)
    assert mods[1]["value"] == pytest.approx(100 * (2.55 / 3.55 - 1), abs=0.01)


def test_detonating_arrow_explodes_only_at_its_maximum_stage():
    # (GEM19) PoB: the Explosion with no stage check, 1 stage, no end animation
    da = Skill("Detonating Arrow", 1, attack=True, stat_set="Explosion")
    sets = [{"label": "Arrow", "TotalDPS": 100.0}, {"label": "Explosion", "TotalDPS": 300.0}]
    mods, notes = models.detonating_arrow_model(da, "Explosion", sets, {"Time": 0.5}, 1)
    # value(s) = ((1 + 1.2 s) / 2.2 + 3 [s = 4]) / (0.5 s + 0.1172): s = 4 (5.64 / 2.117 = 2.66 vs 1 / 0.617 = 1.62)
    assert mods[0] == models.raw_mod("Multiplier:DetonatingArrowStage", "BASE", 3, "Detonating Arrow")
    hit4 = 5.8 / 2.2 + 3.0
    factor = hit4 / 3.0 * 2.0 / (2.0 + 0.1172)
    assert mods[1]["value"] == pytest.approx(100 * (factor - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-detonating-arrow-stages" and "explosion" in notes[0]["label"]
    # the next pass at stage 4 (the ratio now over the stage-4 Arrow): a fixed point
    sets4 = [{"label": "Arrow", "TotalDPS": 100.0 * 5.8 / 2.2}, {"label": "Explosion", "TotalDPS": 300.0}]
    again, _ = models.detonating_arrow_model(da, "Explosion", sets4, {"Time": 0.5}, 4)
    assert again[0] == mods[0] and again[1]["value"] == pytest.approx(mods[1]["value"], abs=0.02)


def test_detonating_arrow_at_one_stage_steers_off_the_explosion():
    # (GEM19) probe POE2T3425: 1.85 s a stage, the Explosion 0.40x the Arrow: 1 stage is best, no explosion played
    da = Skill("Detonating Arrow", 1, attack=True, stat_set="Explosion")
    sets = [{"label": "Arrow", "TotalDPS": 87.8}, {"label": "Explosion", "TotalDPS": 35.1}]
    mods, notes = models.detonating_arrow_model(da, "Explosion", sets, {"Time": 1.852}, 1)
    factor = (1 / (35.1 / 87.8)) * 1.852 / (1.852 + 0.2344 * 1.852) * 0.9
    assert [m["name"] for m in mods] == ["Damage"] and mods[0]["value"] == pytest.approx(100 * (factor - 1), abs=0.05)
    mods, _ = models.detonating_arrow_model(da, "Arrow", sets, {"Time": 1.852}, 1)
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (1 / 1.2344 - 1), abs=0.01)


def test_bonestorm_channels_its_full_spikes():
    # (GEM19) PoB: 1 spike a release at the 0.3 s minimum channel; a full channel is 20 spikes at 0.125 s each
    bs = Skill("Bonestorm", 1)
    mods, notes = models.bonestorm_model(bs)
    assert mods == [models.raw_mod("Multiplier:BonestormStage", "BASE", 19, "Bonestorm")]
    assert notes[0]["entry"] == "model-gem19-bonestorm-spikes"
    assert models.bonestorm_model(bs, {"model-gem19-bonestorm-spikes"}) == ([], [])
    assert models.bonestorm_model(Skill("Bonestorm", 1, triggered=True)) == ([], [])


def test_temper_weapon_combust_pays_for_its_channel():
    # (GEM19) probe POE2T3436: Combust 200.7 a hit with no rate in PoB; Boneshatter 51 DPS at ~1 use a second
    n, combusts, share, value = models.temper_plan(51.0, 1.0, 0.9, 200.7)
    # n = 4: channel 4.583 s + max(12 s of attacks, 5 s) = 16.583 s; 12 x 0.9 Combusts
    assert n == 4 and combusts == pytest.approx(10.8 / 16.583, rel=1e-3)
    assert share == pytest.approx(12 / 16.583, rel=1e-3) and value > 51.0
    bone = Skill("Boneshatter", 1, attack=True, melee=True)
    mods, notes = models.temper_weapon_model(bone, 51.0, 1.0, 0.9, 200.7, 1.125, 5.0)
    assert models.trigger_rate("Combust", combusts) == mods[:2]
    assert mods[2]["name"] == "DPS" and mods[2]["value"] == pytest.approx(100 * (share - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem19-temper-weapon-combust"


def test_temper_weapon_not_worth_it_or_not_melee():
    assert models.temper_plan(10_000.0, 2.0, 1.0, 50.0)[0] == 0  # (Combust far under the main skill: not channelled)
    mods, notes = models.temper_weapon_model(Skill("Spark", 1, spell=True), 51.0, 1.0, 1.0, 200.7, 1.125, 5.0)
    assert mods == [] and notes[0]["value"] == "0"
    assert models.temper_weapon_model(Skill("Boneshatter", 1, attack=True, melee=True), 51.0, 1.0, 1.0, 200.7, 1.125,
                                      5.0, {"model-gem19-temper-weapon-combust"}) == ([], [])


def test_eye_of_winter_first_hit_adds_nine_hits_a_use():
    # (GEM19) eye_of_winter_first_hit_damage_+%_final 900, never read by PoB
    eye = Skill("Eye of Winter", 1, spell=True)
    stats = {"AverageDamage": 100.0, "Speed": 0.1, "TotalDPS": 300.0}
    mods, notes = models.eye_of_winter_model(eye, stats)
    assert mods[0]["value"] == pytest.approx(100 * (9 * 100 * 0.1 / 300), abs=0.01)  # +90 on 300: +30%
    assert notes[0]["entry"] == "model-gem19-eye-of-winter-first-hit"
    # the next pass reads PoB's TotalDPS with that line in: the same line (a fixed point)
    again, _ = models.eye_of_winter_model(eye, {**stats, "TotalDPS": 390.0}, mods[0]["value"])
    assert again[0]["value"] == pytest.approx(mods[0]["value"], abs=0.01)


def test_gas_arrow_explodes_only_on_ignite_or_detonation():
    # (GEM20) probe POE2T3441: Arrow 38.0, Poison Cloud 88.6, Explosion 162.4, kept "Explosion"
    sets = [{"label": "Arrow", "TotalDPS": 38.0}, {"label": "Poison Cloud", "TotalDPS": 88.6},
            {"label": "Explosion", "TotalDPS": 162.4}]
    ga = Skill("Gas Arrow", 1, attack=True)
    mods, notes = models.gas_arrow_model(ga, "Explosion", sets, 0.0, "nothing Ignites")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (38.0 * 0.9 / 162.4 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem20-gas-arrow-ignite-explosion"
    mods, _ = models.gas_arrow_model(ga, "Explosion", sets, 0.5, "")
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * ((38.0 + 81.2) / 162.4 - 1), abs=0.01)
    # the Poison Cloud set's fake hit is replaced by the hits; its Poison stays (a DPS line)
    mods, notes = models.gas_arrow_model(ga, "Poison Cloud", sets, 0.5, "")
    assert mods[0]["value"] == pytest.approx(100 * ((38.0 + 81.2) / 88.6 - 1), abs=0.01)
    assert "applications a cloud" in notes[0]["basis"]
    assert models.gas_arrow_model(ga, "Explosion", sets, 0.5, "", {"model-gem20-gas-arrow-ignite-explosion"}) == ([], [])
    assert models.gas_arrow_model(Skill("Lightning Rod", 1), "Explosion", sets, 0.5, "") == ([], [])


def test_gas_cloud_ignite_window():
    # Ignited 50% with 4 s Ignites: some moment of a 4 s cloud life is Ignited with 1 - 0.5^2
    assert models.gas_ignite_within(0.5, 4.0, 4.0) == pytest.approx(0.75)
    assert models.gas_ignite_within(0.0, 4.0, 4.0) == 0.0 and models.gas_ignite_within(1.0, 4.0, 4.0) == 1.0
    assert models.gas_cloud_life(1.2) == 4.0 and models.gas_cloud_life(3.0) == pytest.approx(2.0)


def test_frozen_locus_explodes_when_its_crystal_breaks():
    # (GEM20) probe POE2T3441: Crystal 0, Explosion 254.3 at 0.98 casts a second
    sets = [{"label": "Crystal", "TotalDPS": 0.0}, {"label": "Explosion", "TotalDPS": 254.3}]
    fl = Skill("Frozen Locus", 1)
    import math
    # the build's own breakers at 0.5 a second: the headline (RESEARCH30 R30-5: the boss's hits only a LOW row)
    mods, notes = models.frozen_locus_model(fl, "Explosion", sets, 0.98, 0.5, 0.5, "breakers")
    q = 1 - math.exp(-5.0)
    rate = q / (1 / 0.98 + q / 0.5)
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (rate / 0.98 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem20-frozen-locus-crystal-breaks" and "LOW row" in notes[0]["basis"]
    # only the boss's hits: not counted, held at 1%
    mods, _ = models.frozen_locus_model(fl, "Explosion", sets, 0.98, 0.0, 0.5, "the boss")
    assert mods[0]["value"] == pytest.approx(-99.0)
    assert models.locus_explosions(1.0, 0.0) == 0.0
    assert models.locus_explosions(1.0, 1e6) == pytest.approx(1.0, rel=1e-3)
    assert models.frozen_locus_model(fl, "Crystal", sets, 0.98, 1.0, 0.5, "") == ([], [])


def test_magnetic_salvo_explodes_only_by_lingering_arrows():
    # (GEM20) probe POE2T3441: Impact 42.5, Explosion 82.6 at 0.6 uses a second, kept "Explosion"
    sets = [{"label": "Impact", "TotalDPS": 42.5}, {"label": "Explosion", "TotalDPS": 82.6}]
    ms = Skill("Magnetic Salvo", 1, attack=True)
    mods, notes = models.magnetic_salvo_model(ms, "Explosion", sets, 0.6, 8, 0.0, 0.0, "none")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (42.5 * 0.9 / 82.6 - 1), abs=0.01)
    # Lightning Rod at 1.44 arrows a second against 0.6 x 8 missiles: 30% explode
    e = 1.44 / 4.8
    mods, notes = models.magnetic_salvo_model(ms, "Explosion", sets, 0.6, 8, 1.44, 0.0, "Lightning Rod")
    assert mods[0]["name"] == "DPS"
    # (MECHB38: each of the 1.44 exploding missiles a second is its own hit; PoB's set is one missile a use)
    assert mods[0]["value"] == pytest.approx(100 * ((42.5 * (1 - e) + 82.6 * e * 8) / 82.6 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem20-magnetic-salvo-lingering-arrows"
    assert models.salvo_share(0.6, 8, 100.0) == 1.0


def test_herald_of_blood_explodes_only_on_kills():
    # (GEM20) GEM18's probe POE2T3411: Explosion 35.0 at 1 a second as a main skill, Buff 0
    sets = [{"label": "Buff", "TotalDPS": 0.0, "AverageDamage": 0.0, "Speed": 1.0},
            {"label": "Explosion", "TotalDPS": 35.0, "AverageDamage": 35.0, "Speed": 1.0}]
    hb = Skill("Herald of Blood", 1, attack=True)
    mods, notes = models.herald_of_blood_model(hb, "Explosion", sets, 0.0, "a lone boss")
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(-99.0)
    assert notes[0]["entry"] == "model-gem20-herald-of-blood-kills" and notes[0]["confidence"] == "LOW"
    mods, _ = models.herald_of_blood_model(hb, "Explosion", sets, 0.043, "the King's totems")
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(100 * (0.043 - 1))
    assert models.herald_of_blood_model(hb, "Buff", sets, 0.0, "") == ([], [])
    # (GEM25) Herald of the Royal Queen (Atziri's Acuity): the same kill-only explosion
    mods, notes = models.herald_of_blood_model(Skill("Herald of the Royal Queen", 1, attack=True), "Explosion", sets,
                                               0.0, "a lone boss")
    assert mods[0]["value"] == pytest.approx(-99.0) and "Herald of the Royal Queen" in notes[0]["play"]


def test_shield_wall_explodes_when_shattered_more_by_slams():
    import math
    sets = [{"label": "Shield Wall", "TotalDPS": 100.0, "AverageDamage": 100.0, "Speed": 1.0}]
    sw = Skill("Shield Wall", 1, attack=True)
    # shattered at once by your Slams: one break a cast, the 140% more, one wall standing (no time for a second)
    mods, notes = models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 1e6, 0.0, 140.0, "slams")
    assert mods[0]["name"] == "DPS" and mods[0]["value"] == pytest.approx(140.0, abs=0.2)
    assert notes[0]["entry"] == "model-gem20-shield-wall-shatter"
    # the boss's hits alone break walls between you (R31-6): one wall each, no more
    mods, notes = models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 0.0, 0.5, 140.0, "boss")
    assert mods[0]["value"] == pytest.approx(100 * (models.locus_explosions(1.0, 0.5, 6.0) - 1), abs=0.01)
    assert "no boss breaks" in notes[0]["basis"]
    # one Slam shatters both walls: slow breakers leave time for a second wall
    assert models.shield_wall_hits(1.0, 0.0) == pytest.approx(2.0)
    assert models.shield_wall_hits(1.0, 1.0) == pytest.approx(1.0 + math.exp(-1.0))
    assert models.shield_wall_model(sw, "Shield Wall", sets, 1.0, 0.0, 0.5, 140.0, "", {"model-gem20-shield-wall-shatter"}) == ([], [])


def test_earthshatter_more_damage_for_each_spike_standing():
    # (GEM20, RESEARCH30 R30-5 (2)) 6% a spike at quality 20; spikes standing min(6, uses x their life)
    assert models.spike_standing("Earthshatter", 0.5, 0.0) == pytest.approx(3.0)
    assert models.spike_standing("Earthshatter", 2.0, 0.0) == 6.0
    import math
    assert models.spike_standing("Earthshatter", 0.5, 0.5) == pytest.approx(0.5 * (1 - math.exp(-3.0)) / 0.5)
    sets = [{"label": "Slam", "TotalDPS": 25.8}, {"label": "Explosion", "TotalDPS": 129.5}]
    es = Skill("Earthshatter", 1, attack=True)
    mods, notes = models.detonated_set_model(es, "Slam", sets, 0.5, 0.0, "none", frozenset(), 6.0)
    assert mods[0]["value"] == pytest.approx(100 * (1 + 0.06 * 3.0 - 1), abs=0.01)
    assert "for each Spike" in notes[0]["basis"]


def test_gas_arrow_cloud_poisons_every_two_seconds():
    # (GEM20, RESEARCH30 R30-5 (3)) a full 4 s cloud: 2.5 applications (2-3); one that explodes on landing: 0.5
    assert models.gas_cloud_poisons(4.0) == pytest.approx(2.5)
    assert models.gas_cloud_poisons(0.0) == pytest.approx(0.5)
    sets = [{"label": "Arrow", "TotalDPS": 38.0}, {"label": "Poison Cloud", "TotalDPS": 88.6},
            {"label": "Explosion", "TotalDPS": 162.4}]
    ga = Skill("Gas Arrow", 1, attack=True)
    mods, notes = models.gas_arrow_model(ga, "Poison Cloud", sets, 0.0, "", frozenset(), 2.5)
    assert [m["name"] for m in mods] == ["DPS", "AilmentMagnitude"] and mods[1]["value"] == pytest.approx(150.0)
    mods, notes = models.gas_arrow_model(ga, "Arrow", sets, 0.0, "", frozenset(), 2.5)
    assert all(m["name"] != "AilmentMagnitude" for m in mods) and "shows no Poison DPS" in notes[0]["basis"]
    # (GEM21, SEARCH28's item 5) with the bridge's per-set PoisonDPS the cloud's Poison joins the Arrow set: 44.9 x 2.5
    sets[1]["PoisonDPS"] = 44.9
    mods, notes = models.gas_arrow_model(ga, "Arrow", sets, 0.0, "", frozenset(), 2.5)
    added = [m for m in mods if m.get("name") != "DPS"]
    assert len(added) == 1 and models.added_input("GasArrowCloud", 44.9 * 2.5) == added[0]
    assert "added beside it" in notes[0]["basis"]
    mods, notes = models.gas_arrow_model(ga, "Arrow", sets, 0.0, "", frozenset(), 2.5, 0.6)  # (60% of the fight on it)
    assert models.added_input("GasArrowCloud", 44.9 * 2.5 * 0.6) in mods and "60% of the fight" in notes[0]["basis"]


def test_enervating_nova_more_at_the_far_edge():
    # (GEM20) "up to 100% more to enemies on the far edge of the ring"; none inside the inner 1.0 m
    assert models.nova_distance_more(0.5, 1.0, 2.6, 100) == 0.0
    assert models.nova_distance_more(1.8, 1.0, 2.6, 100) == pytest.approx(1.5)
    assert models.nova_distance_more(2.6, 1.0, 2.6, 100) == pytest.approx(2.0)
    nova = Skill("Enervating Nova", 1, spell=True)
    mods, notes = models.enervating_nova_model(nova, 1.0, 2.6, 100, 5.0)
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100.0)
    assert "outside the ring" in notes[0]["basis"] and notes[0]["entry"] == "model-gem20-enervating-nova-distance"
    assert models.enervating_nova_model(nova, 1.0, 2.6, 100, 2.0)[1][0]["basis"].endswith(
        "x1.62 (ramping from your position instead: x1.77)")  # (GEM21, R31-10: the ramp's origin a row)
    assert models.nova_distance_more(2.0, 1.0, 2.6, 100, 0.0) == pytest.approx(1 + 2.0 / 2.6)
    assert models.nova_distance_more(2.6, 1.0, 2.6, 100, 0.0) == pytest.approx(2.0)
    assert models.enervating_nova_model(Skill("Spark", 1), 1.0, 2.6, 100, 2.0) == ([], [])


def test_shattering_spite_explodes_with_bloodstone_lances():
    # (GEM20) "Explosions deal 20% more Damage per Bloodstone Lance"; lances from other Spear skills' hits, max 30
    assert models.spite_lances(3.0, 0.5, 30) == pytest.approx(6.0)
    assert models.spite_lances(30.0, 0.5, 30) == 30
    assert models.SPEAR_HITS_A_USE["Rapid Assault"] == 6  # (GEM21, R31-10: one lance a stab, "six rapid stabs")
    spite = Skill("Shattering Spite", 1, attack=True)
    mods, notes = models.shattering_spite_model(spite, 0.5, 3.0, 30, 20, "Spear Throw 3 hits/s")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(120.0)
    assert notes[0]["entry"] == "model-gem20-shattering-spite-lances"
    mods, notes = models.shattering_spite_model(spite, 0.5, 0.0, 30, 20, "none")
    assert mods[0]["value"] == pytest.approx(-99.0) and "no explosion" in notes[0]["basis"]


def test_galvanic_field_orb_only_after_a_shock():
    # (GEM20) the Orb attaches on your Shock, 6 s, one at a time; PoB scored it always there
    assert models.galvanic_orb_uptime(0.65, 0.0) == 0.0
    assert models.galvanic_orb_uptime(0.65, 1.0) == pytest.approx(6.0 / (0.65 + 1.0 + 6.0))
    gf = Skill("Galvanic Field", 1, spell=True)
    mods, notes = models.galvanic_field_model(gf, "Orb", 1.54, 0.0, "none")
    assert mods[0]["value"] == pytest.approx(-99.0) and "no Shock" in notes[0]["basis"]
    mods, notes = models.galvanic_field_model(gf, "Orb", 1.54, 1.0, "Spark 1 Shock/s")
    assert mods[0]["value"] == pytest.approx(100 * (6.0 / (1 / 1.54 + 1.0 + 6.0) - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem20-galvanic-field-orb"
    assert models.galvanic_field_model(gf, "Buff", 1.54, 1.0, "") == ([], [])


def test_a_side_detonator_counts_at_its_real_use_rate():
    # (GEM21, MECHB38's lead) an unpressed side Detonator was counted at PoB's full rate: now on its cooldown, else the
    # search's chosen rate, else 0; a pressed one at its press share; a swap-weapon one labelled
    from types import SimpleNamespace
    from builder.search.assumptions import Assumptions

    gems = {"Explosive Spear": {"skillTypes": ["Attack", "Detonator"]},
            "Explosive Grenade": {"skillTypes": ["Attack", "Cooldown", "Detonator"]},
            "Skeletal Arsonist": {"skillTypes": ["Minion", "Persistent", "Detonator"]}, "Storm Lance": {"skillTypes": []}}
    rows = {1: {"name": "Explosive Spear", "speed": 1.5}, 2: {"name": "Explosive Grenade", "speed": 0.24,
                                                                "cooldown": 4.1666667}, 3: {"name": "Skeletal Arsonist", "speed": 1.2}}

    class FakeBuild:
        def __init__(self, press=None, choices=None):
            self.ctx = SimpleNamespace(gems=gems)
            self.objective = SimpleNamespace(press_scales=press or {}, press_choices=choices or {})

        def groups(self):
            return [{"index": i, "gems": [{"name": n}]} for i, n in
                    ((0, "Storm Lance"), (1, "Explosive Spear"), (2, "Explosive Grenade"), (3, "Skeletal Arsonist"))]

        def call(self, what, **kw):
            if what == "activeSkills":
                return {"skills": [{"name": "Explosive Grenade", "group": 2, "wrongWeapon": True}]}
            return {"rates": [rows[i] for i in kw["indices"]]}

    a = Assumptions.__new__(Assumptions)
    a.build = FakeBuild()
    rate, how = a.detonator_rate(0)
    assert rate == pytest.approx(0.24, abs=1e-6)  # (the spear unpressed: 0; the grenade on its cooldown; the minions 0)
    assert "weapon swap" in how and "Persistent skill" in how
    a.build = FakeBuild(choices={"Explosive Spear": 0.5})
    assert a.detonator_rate(0)[0] == pytest.approx(0.74, abs=1e-6)  # (the search's chosen 0.5 presses a second)
    a.build = FakeBuild(press={"Explosive Spear": 0.2})
    assert a.detonator_rate(0)[0] == pytest.approx(0.24 + 0.3, abs=1e-6)  # (pressed: 20% of its 1.5/s)


def test_a_set_models_note_shows_pobs_set_dps_without_its_own_last_line():
    # (GEM21) setRates' TotalDPS carries the model's own previous line on every set: the note takes it back out; the
    # factor (a ratio between sets) is the same either way
    entry = "model-gem17-storm-lance-detonation"
    sets = [{"label": "Projectile", "TotalDPS": 33.69}, {"label": "Lodged Spear", "TotalDPS": 39.33},
            {"label": "Detonation", "TotalDPS": 117.48}]
    previous = [models.raw_mod("DPS", "MORE", 17.0, "Storm Lance", tag=entry),
                models.raw_mod("DPS", "MORE", 50.0, "Storm Lance", tag="other")]
    assert models.unscale_own(sets, previous, entry)["Projectile"] == pytest.approx(33.69 / 1.17)
    assert models.unscale_own(sets, None, entry)["Projectile"] == pytest.approx(33.69)
    lance = Skill("Storm Lance", 1, attack=True)
    plain = models.detonated_set_model(lance, "Projectile", sets, 0.84, 0.0, "none")
    unscaled = models.detonated_set_model(lance, "Projectile", sets, 0.84, 0.0, "none", previous=previous)
    assert plain[0][0]["value"] == pytest.approx(unscaled[0][0]["value"]) and unscaled[0][0]["tag"] == entry
    assert "Lodged Spear 34" in unscaled[1][0]["basis"] and "Lodged Spear 39" in plain[1][0]["basis"]


def test_high_velocity_rounds_vs_armour_broken_only_on_a_fully_broken_boss():
    # (GEM21) its "Vs Armour Broken" part only while the boss's Armour is Fully Broken (MECH87's share): not on every bolt
    hvr = Skill("High Velocity Rounds", 1, attack=True)
    sets = [{"label": "Projectile", "TotalDPS": 69.1}, {"label": "Vs Armour Broken", "TotalDPS": 99.4}]
    mods, notes = models.gated_set_model(hvr, "Vs Armour Broken", sets, 0.25, "Armour Break I")
    assert mods[0]["value"] == pytest.approx(100 * ((69.1 * 0.75 + 99.4 * 0.25) / 99.4 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem21-high-velocity-rounds-armour-broken" and "Fully Broken Armour" in notes[0]["label"]
    mods, _ = models.gated_set_model(hvr, "Vs Armour Broken", sets, 0.0, "none")
    assert mods[0]["value"] == pytest.approx(100 * (69.1 * 0.9 / 99.4 - 1), abs=0.01)  # (steered to the Projectile)


def test_one_ignite_counts_the_strongest_part_at_its_own_uptime():
    """GEM22: a set model's DPS line kept the scored (strong) part's Ignite up all fight; the game renews it only at
    that part's own uses. Strong part: 50 a full Ignite, 0.8 applications x duration a use, used 10% of uses ->
    up 8%; the plain part's 10 (up 100%) the rest: 50 x 0.08 + 10 x 0.92 = 13.2 against PoB's 40."""
    sets = [{"label": "Plain", "IgniteDPS": 10.0, "IgniteStackPotential": 2.0, "Speed": 1.0, "DpsMultiplier": 1.0},
            {"label": "Strong", "IgniteDPS": 40.0, "IgniteStackPotential": 0.8, "Speed": 1.0, "DpsMultiplier": 1.0}]
    skill = Skill("Boneshatter", 1, attack=True, melee=True, stat_set="Strong")
    mods, notes = models.strongest_ailment_model(skill, sets, {"Plain": 1.0, "Strong": 0.1}, 1.0)
    assert len(mods) == 1 and mods[0]["name"] == "AilmentMagnitude" and mods[0]["keywordFlags"] == ["Ignite"]
    assert mods[0]["value"] == pytest.approx(100 * (13.2 / 40 - 1), abs=0.01)
    assert notes[0]["entry"] == models.STRONG_ENTRY and "Strong 50 up 8%" in notes[0]["basis"]
    # (the next pass reads the sets under its own line: the same line)
    scaled = [{**s, "IgniteDPS": s["IgniteDPS"] * (1 + mods[0]["value"] / 100)} for s in sets]
    again, _ = models.strongest_ailment_model(skill, scaled, {"Plain": 1.0, "Strong": 0.1}, 1.0, previous=mods)
    assert again[0]["value"] == pytest.approx(mods[0]["value"], abs=0.01)
    # (the strong part every use: up 80%, the plain part's fills the rest: 50 x 0.8 + 10 x 0.2 = 42, PoB 40; a
    # stacking Ignite: left to PoB)
    every, _ = models.strongest_ailment_model(skill, sets, {"Plain": 1.0, "Strong": 1.0}, 1.0)
    assert every[0]["value"] == pytest.approx(100 * (42 / 40 - 1), abs=0.01)
    stacking = [{**s, "IgniteStacksMax": 3} for s in sets]
    assert models.strongest_ailment_model(skill, stacking, {"Plain": 1.0, "Strong": 0.1}, 1.0) == ([], [])


def test_herald_of_thunder_bolts_follow_shocked_kills_and_the_attacks_hits():
    """GEM23: bolts a second = min(attack hits, kills x Shocked share x storm hits) x the bolt's hit; 0 with no kills."""
    dps, notes = models.herald_of_thunder_model(100.0, 2.0, 0.2, 0.5, 3, "test")
    assert dps == pytest.approx(0.2 * 0.5 * 3 * 100)
    assert notes[0]["entry"] == models.HERALD_OF_THUNDER_ENTRY and "without Shocked kills: 0" in notes[0]["play"]
    assert models.herald_of_thunder_model(100.0, 0.1, 1.0, 1.0, 3)[0] == pytest.approx(10.0)  # (capped by the hits)
    assert models.herald_of_thunder_model(100.0, 2.0, 0.0, 1.0, 3)[0] == 0.0  # (a lone boss: no source, 0)
    assert models.herald_of_thunder_model(0.0, 2.0, 1.0, 1.0, 3) == (0.0, [])


def test_fan_the_flames_explodes_on_hits_against_an_ignited_boss_up_to_its_cooldown():
    """GEM23: explosions a second = min(hits x Ignite uptime, 1 / cooldown) x one explosion; PoB's entry out of Full DPS."""
    dps, notes = models.fan_the_flames_model([{"source": "Twister", "hits": 1.5, "ignited": 0.4, "cooldown": 0.5,
                                               "blast": 200.0, "pob": 234.8}])
    assert dps == pytest.approx(1.5 * 0.4 * 200)
    assert notes[0]["full_dps"] == {models.FAN_THE_FLAMES: 0.0} and notes[0]["entry"] == models.FAN_THE_FLAMES_ENTRY
    assert models.fan_the_flames_model([{"hits": 10.0, "ignited": 1.0, "cooldown": 0.5, "blast": 100.0}])[0] == \
        pytest.approx(200.0)  # (the cooldown caps it)
    dps, notes = models.fan_the_flames_model([{"hits": 2.0, "ignited": 0.0, "cooldown": 0.5, "blast": 100.0}])
    assert dps == 0.0 and notes[0]["full_dps"] == {models.FAN_THE_FLAMES: 0.0}  # (no Ignite: 0, PoB's entry still out)
    assert models.fan_the_flames_model([]) == (0.0, [])


def test_accelerated_growth_explodes_each_overgrown_plant_of_the_supported_skill():
    """GEM24: explosions a second = plants a second (per skill) x the Overgrown share x one explosion; no source, 0."""
    dps, notes = models.accelerated_growth_model([{"source": "Vine Arrow", "uses": 0.6, "blast": 40.0}], 0.2,
                                                 ["20% chance to immediately Overgrow"])
    assert dps == pytest.approx(0.6 * 1 * 0.2 * 40.0)
    assert notes[0]["entry"] == models.ACCELERATED_GROWTH_ENTRY and notes[0]["confidence"] == "MEDIUM"
    dps, notes = models.accelerated_growth_model([{"source": "Toxic Growth", "uses": 1.0, "blast": 10.0}], 0.5, ["x"])
    assert dps == pytest.approx(1.0 * 1 * 0.5 * 10.0)  # (one Pustule a use at the boss, the headline)
    assert len(notes) == 3 and "not counted" in notes[1]["label"] and notes[1]["confidence"] == "LOW"  # (+ the arena row, GEM26)
    assert notes[1]["value"] == f"{1.0 * 5 * 0.5 * 10.0:,.1f} DPS"  # (GEM25: all 5 Pustules, the upper reading's row)
    assert len(models.accelerated_growth_model([{"source": "Vine Arrow", "uses": 0.6, "blast": 40.0}], 0.2,
                                               ["x"])[1]) == 1  # (no upper reading for one plant a use)
    # (GEM25, R35-4) the Pustules reaching the boss: min(n, max(1, n x ((r + 1.8) / (4.5 x sqrt(area)))^2))
    assert models.pustules_reaching(5, 0.3, 1.0)[0] == pytest.approx(5 * (2.1 / 4.5) ** 2)  # (1.09)
    assert models.pustules_reaching(5, 1.0, 1.0)[0] == pytest.approx(5 * (2.8 / 4.5) ** 2)  # (1.94, the Fell Serpent)
    assert models.pustules_reaching(5, 0.3, 4.0)[0] == 1.0  # (a wide spread: never below one a use)
    assert models.pustules_reaching(5, 3.0, 1.0)[0] == 5.0  # (a huge boss: all of them)
    dps, notes = models.accelerated_growth_model([{"source": "Toxic Growth", "uses": 1.0, "blast": 10.0, "per": 1.5,
                                                   "per_how": "x", "most": 6.0}], 0.5, ["x"])
    assert dps == pytest.approx(1.5 * 0.5 * 10.0) and notes[1]["value"] == f"{6 * 0.5 * 10.0:,.1f} DPS"
    dps, notes = models.accelerated_growth_model([{"source": "Vine Arrow", "uses": 0.6, "blast": 40.0}], 0.0, [])
    assert dps == 0.0 and "0" in notes[0]["value"]  # (nothing Overgrows the plants: 0, said)
    dps, notes = models.accelerated_growth_model([{"source": "Briarpatch", "uses": 1.0, "blast": 40.0}], 1.0, ["x"])
    assert dps == 0.0 and "Briarpatch" in notes[0]["basis"]  # (no plant rate read: 0, said)
    assert models.accelerated_growth_model([], 1.0, []) == (0.0, [])
    share = models.overgrown_share([], {}, ["Plants have a 20% chance to immediately Overgrow"])[0]
    assert share == pytest.approx(0.2)


def test_profane_decay_is_up_while_its_curse_lands_and_less_what_pob_counts():
    """GEM25: one Decay at PoB's DoT x min(1, landings x duration); PoB's always-up figure in Full DPS taken off."""
    use = models.CurseUse("Despair", 1.0, "cast by hand", True, 7.4)
    landings, _ = models.curse_landings(use, 0.7, None)
    assert landings == pytest.approx(1 / 7.4)
    part = {"curse": "Despair", "dot": 287.0, "seconds": 8.0, "landings": landings, "how": "x", "in_full": False}
    added, notes = models.profane_decay_model([part])
    assert added == pytest.approx(287.0) and notes[0]["entry"] == models.PROFANE_DECAY_ENTRY
    assert models.profane_decay_model([{**part, "in_full": True}])[0] == pytest.approx(0.0)  # (PoB counts it already)
    lapse = models.CurseUse("Despair", 0.9, "x", True, 4.0, lapse=True)
    landings, _ = models.curse_landings(lapse, 0.7, None)
    added, _ = models.profane_decay_model([{**part, "seconds": 2.0, "landings": landings}])
    assert added == pytest.approx(287.0 * 2.0 / 4.5)  # (up 2 s of each 4.5 s)
    aura = models.CurseUse("Despair", 1.0, "an Aura (Blasphemy) on enemies nearby")
    assert models.curse_landings(aura, 0.7, 80.0)[0] == pytest.approx(1 / 80.0)
    assert models.curse_landings(aura, 0.7, None)[0] == 0.0
    added, notes = models.profane_decay_model([part, {**part, "curse": "Enfeeble", "dot": 100.0}])
    assert added == pytest.approx(287.0) and "not counted" in notes[1]["label"]  # (the strongest only; the sum a row)
    assert models.profane_decay_model([]) == (0.0, [])
    # GEM26, RESEARCH35 R35-20: under Blasphemy no Profane Decay at all (0; less PoB's always-up figure where scored),
    # "once a fight" a not-counted row
    blas = {**part, "landings": 1 / 80.0, "aura": True, "fight": 80.0}
    added, notes = models.profane_decay_model([blas])
    assert added == 0.0 and any("Blasphemy if GGG fixed" in n["label"] and n["value"] == "28.7 DPS" for n in notes)
    assert models.profane_decay_model([{**blas, "in_full": True}])[0] == pytest.approx(-287.0)  # (the OVERCOUNT out)
    # R35-18: a hand recast refreshes it (the headline); "only on a new curse" a row, once a fight
    added, notes = models.profane_decay_model([{**part, "by_hand": True, "fight": 80.0}])
    assert added == pytest.approx(287.0) and any("only a new curse" in n["label"] for n in notes)


def test_twisted_pact_inflicts_five_corrupted_blood_stacks_a_warcry():
    """GEM25: 5 stacks a warcry to the shared cap of 10, an added source less the one stack PoB scores itself."""
    part = {"rate": 0.125, "where": "Infernal Cry", "one": 100.0, "seconds": 5.0, "in_full": False}
    added, notes = models.twisted_pact_model([part], None)
    assert added == pytest.approx(100.0 * 5 * 0.125 * 5.0)  # (each 5 s burst runs out before the next)
    assert notes[0]["entry"] == models.TWISTED_PACT_ENTRY
    added, _ = models.twisted_pact_model([{**part, "rate": 0.5}], None)
    assert added == pytest.approx(1000.0)  # (a cry every 2 s: the climb not counted, the cap of 10)
    added, _ = models.twisted_pact_model([{**part, "rate": 0.5, "in_full": True}], None)
    assert added == pytest.approx(900.0)  # (PoB scores one stack itself)
    added, _ = models.twisted_pact_model([{**part, "rate": 0.5}], None, taken=7.0)
    assert added == pytest.approx(300.0)  # (Corrupted Cry's 7 stacks first, of the shared 10)
    assert models.cry_stacks(0.5, 100, 5, 10, 1) == models.cry_stacks(0.5, 100, 5, 10)
    assert models.twisted_pact_model([], None) == (0.0, [])


def test_voltaic_fulmination_follows_electrocuted_kills_up_to_its_cooldown():
    """GEM26: explosions a second = min(kills x Electrocuted share, 1 / cooldown) x one explosion; 0 with no kills or
    with the support outside the main skill's group."""
    dps, notes = models.voltaic_fulmination_model(1000.0, 0.05, 0.4, 0.3, "test")
    assert dps == pytest.approx(0.05 * 0.4 * 1000)
    assert notes[0]["entry"] == models.VOLTAIC_FULMINATION_ENTRY and "without Electrocuted kills: 0" in notes[0]["play"]
    assert models.voltaic_fulmination_model(1000.0, 10.0, 1.0, 0.5)[0] == pytest.approx(2000.0)  # (the cooldown caps it)
    assert models.voltaic_fulmination_model(1000.0, 0.0, 1.0, 0.3)[0] == 0.0  # (a lone boss: no source, 0)
    assert models.voltaic_fulmination_model(1000.0, 0.05, 1.0, 0.3, side=True)[0] == 0.0  # (a side group: not read)
    assert models.voltaic_fulmination_model(0.0, 1.0, 1.0, 0.3) == (0.0, [])


def test_shred_fragments_follow_hits_on_a_frozen_boss_and_take_pobs_flat_entry_out():
    """GEM26: fragment hits a second = Shred's hits x the boss's Freeze uptime x one fragment use; PoB's flat Full DPS
    entry out where its group is in Full DPS; no Freeze, 0."""
    dps, notes = models.shred_fragments_model(2.0, 0.3, 50.0, pob=68.0, how="test", in_full=True)
    assert dps == pytest.approx(2.0 * 0.3 * 50.0)
    assert notes[0]["full_dps"] == {models.ICE_TIPPED_FRAGMENT: 0.0} and notes[0]["entry"] == models.SHRED_FRAGMENTS_ENTRY
    assert "without Freeze: 0" in notes[0]["play"]
    dps, notes = models.shred_fragments_model(2.0, 0.0, 50.0)
    assert dps == 0.0 and "full_dps" not in notes[0]  # (no Freeze source: 0; out of Full DPS, nothing to take out)


def test_repulsion_wave_a_wave_each_detonation_recast_when_worth_it_and_held_to_ward():
    """GEM26: by hand, recast after each wave (cycle max(cast + delay, 1 s) + half a hit interval) when the waves beat the
    casts' cost to the main skill, else one a duration; Runic Ward caps the casts; no hit, 0."""
    # waves worth it: cycle max(0.5 + 1.5, 1) + 0.5 / 2 = 2.25 s; Ward pays 1 a second; lost (1/2.25 - 1/7.4) x 0.5
    dps, lost, notes = models.repulsion_wave_model(100.0, 2.0, 0.5, 1.5, 7.4, 100.0, 100.0, 50.0)
    assert dps == pytest.approx(100.0 / 2.25) and lost == pytest.approx((1 / 2.25 - 1 / 7.4) * 0.5)
    assert notes[0]["entry"] == models.REPULSION_WAVE_ENTRY
    # a weak wave on a strong main skill: once a duration, no extra cast charged
    dps, lost, _ = models.repulsion_wave_model(10.0, 2.0, 0.5, 1.5, 7.4, 100.0, 100.0, 10_000.0)
    assert dps == pytest.approx(10.0 / 7.4) and lost == 0.0
    # Ward pays one cast every 10 s: held to it
    dps, lost, _ = models.repulsion_wave_model(100.0, 2.0, 0.5, 1.5, 7.4, 10.0, 100.0, 50.0)
    assert dps == pytest.approx(100.0 * 0.1) and lost == 0.0
    assert models.repulsion_wave_model(100.0, 0.0, 0.5, 1.5, 7.4, 100.0, 100.0, 50.0)[0] == 0.0  # (nothing hits)
    dps, lost, _ = models.repulsion_wave_model(100.0, 2.0, 0.5, 1.5, 7.4, 100.0, 100.0, 50.0, landings=0.2)
    assert dps == pytest.approx(20.0) and lost == 0.0  # (a meta / Aura: one a landing)


def test_accelerated_growth_toxic_growth_has_a_not_counted_boss_arena_row():
    """GEM26, RESEARCH35 R35-22: Toxic Growth's explosions counted as working on bosses; '0 in boss arenas' a row."""
    total, notes = models.accelerated_growth_model(
        [{"source": "Toxic Growth", "uses": 1.0, "blast": 40.0, "per": 1.5},
         {"source": "Vine Arrow", "uses": 0.5, "blast": 40.0}], 0.5, ["test"])
    assert total == pytest.approx(1.0 * 1.5 * 0.5 * 40 + 0.5 * 0.5 * 40)
    row = next(n for n in notes if "boss arenas" in n["label"])
    assert row["value"] == f"{0.5 * 0.5 * 40:,.1f} DPS" and "not counted" in row["label"]


def test_magma_spray_fires_once_per_charged_raised_block_and_takes_pobs_flat_entry_out():
    """GEM26: sprays a second = MECHC36's magma_barrier_rate x one spray; PoB's flat Full DPS entry out; never raised, 0."""
    from builder.search import conditions
    rate, how = conditions.magma_barrier_rate({"_raised_share": 1.0}, 0.5)
    assert rate == pytest.approx(1 / (8.0 + 2.0))
    dps, notes = models.magma_spray_model(rate, 500.0, how, pob=587.0)
    assert dps == pytest.approx(50.0) and notes[0]["entry_cut"] == {models.MAGMA_SPRAY: 587.0}
    rate, how = conditions.magma_barrier_rate({"_raised_share": 0.0}, 0.5)
    dps, notes = models.magma_spray_model(rate, 500.0, how)
    assert dps == 0.0 and notes[0]["entry_cut"] == {} and "without raising it: 0" in notes[0]["play"]


def test_trail_of_caltrops_three_a_roll_under_a_still_boss_with_moving_and_zero_rows():
    """GEM26, RESEARCH35 R35-23: Caltrops a second = rolls a second x 3 x projectiles (held to 20 active over 8 s), each
    at PoB's hit; the boss standing still the headline; moving-ceiling and 0 rows."""
    dps, mods, notes = models.trail_of_caltrops_model(40.0, 1.0, 0.2, "test", main_dps=20.0, main="Spark")
    assert dps == pytest.approx(3 / models.DODGE_EVERY * 40.0)
    assert notes[0]["label"].startswith("Time spent dodge rolling")  # (the user, 7 Nov: the rolls' time off the main)
    assert mods[0]["scale"] == pytest.approx(-100 * models.DODGE_ROLL_SECONDS / models.DODGE_EVERY)
    assert f"{dps * 0.8:,.1f}" in notes[1]["play"] and "always on the move: 0" in notes[1]["play"]
    assert models.trail_of_caltrops_model(40.0, 1.0, 0.0, charged=True, main_dps=20.0, main="Spark")[1] == []  # (charged)
    assert models.trail_of_caltrops_model(40.0, 3.0, 0.0)[0] == pytest.approx(20 / 8 * 40.0)  # (the 20-active cap)
    assert models.trail_of_caltrops_model(40.0, 1.0, 0.0, main_dps=1000.0, main="Spark")[0] == 0.0  # (not worth it)
    assert models.trail_of_caltrops_model(0.0, 1.0, 0.0) == (0.0, [], [])


def test_natures_archon_raises_the_overgrown_share_for_accelerated_growth():
    """GEM26: MECHC44's natures_archon_uptime fed the plants a second x the other share; combined 1 - (1 - up)(1 - share)."""
    texts = ["50% chance to gain Nature's Archon when your Plants Overgrow"]
    up, how = models.natures_archon_uptime(texts, 1.0 * 0.2, {}, False, 1.0)
    assert 0 < up < 1 and "Nature's Archon" in how
    assert models.natures_archon_uptime([], 1.0, {}, False, 1.0)[0] == 0.0


def test_void_illusion_explodes_at_the_destroyed_copy_rate_and_takes_pobs_flat_entry_out():
    """GEM27: explosions a second = min(rolls, 1 / cooldown, one destroyed copy every 5 s), each at PoB's explosion; the
    rolls' time off the main skill unless already charged; not worth it: 0; PoB's own Full DPS entry cut (MECHB45's tally)."""
    dps, mods, notes = models.void_illusion_model(400.0, 4.0, main_dps=100.0, pob=104.8, main="Boneshatter")
    assert dps == pytest.approx(400.0 / models.ILLUSION_DESTROYED_EVERY)
    assert notes[0]["label"].startswith("Time spent dodge rolling")
    assert mods[0]["scale"] == pytest.approx(-100 * models.DODGE_ROLL_SECONDS / models.ILLUSION_DESTROYED_EVERY)
    assert notes[-1]["entry_cut"] == {models.VOID_ILLUSION: 104.8} and "taken out" in notes[-1]["basis"]
    dps, mods, notes = models.void_illusion_model(400.0, 4.0, main_dps=100.0, charged=True, main="Boneshatter")
    assert dps == pytest.approx(80.0) and mods == [] and len(notes) == 1  # (rolling already charged elsewhere)
    dps, mods, notes = models.void_illusion_model(400.0, 4.0, main_dps=1000.0, main="Boneshatter")
    assert dps == 0.0 and notes[0]["entry_cut"] == {}  # (not worth rolling for; no Full DPS entry given)
    assert models.void_illusion_model(0.0, 4.0)[0] == 0.0


def test_gale_force_fires_on_landed_boss_hits_with_a_stage_held_and_takes_pobs_flat_entry_out():
    """GEM27: Gale Forces a second = the boss's landed hits L x e^(-L T) (a stage held), held to the cooldown, each at
    PoB's use; PoB's flat Full DPS entry out; a boss that lands nothing: 0."""
    import math
    dps, notes = models.gale_force_model(0.4, 1.5, 100.0, 0.264, pob=27.2)
    assert dps == pytest.approx(0.4 * math.exp(-0.6) * 100.0)
    assert notes[0]["entry_cut"] == {models.GALE_FORCE: 27.2} and "taken out" in notes[0]["basis"]
    assert models.gale_force_model(10.0, 0.0, 100.0, 0.5)[0] == pytest.approx(200.0)  # (the cooldown holds it)
    assert models.gale_force_model(0.0, 1.5, 100.0)[0] == 0.0


def test_event_trigger_supports_fire_on_their_event_not_every_use():
    """GEM27: Volcanic Eruption at the hits on an Ignited boss held to its cooldown; Armour Explosion at the Full Breaks
    held to the hits; no event: 0."""
    dps, notes = models.event_trigger_model(models.VOLCANIC_ERUPTION, 1.4 * 0.5, 1 / 0.4, 10.0, "test", "Sunder", pob=8.9)
    assert dps == pytest.approx(7.0) and "taken out" in notes[0]["basis"]
    assert models.event_trigger_model(models.VOLCANIC_ERUPTION, 10.0, 1 / 0.4, 10.0, "t", "Sunder")[0] == pytest.approx(25.0)
    assert models.event_trigger_model(models.ARMOUR_EXPLOSION, 0.0, 1.4, 10.0, "t", "Sunder")[0] == 0.0
    dps, notes = models.event_trigger_model(models.ARMOUR_EXPLOSION, 1 / 12, 1.4, 120.0, "t", "Sunder")
    assert dps == pytest.approx(10.0) and notes[0]["entry"] == "model-gem27-armour-explosion"


def test_voltaic_beams_need_projectiles_through_a_standing_wall():
    """GEM27, RESEARCH36 R36-14: the beams go to other enemies within 2 m, none to the struck boss: 0 on the boss; the
    beam-on-the-boss reading (hits x the wall's uptime x a beam) a row; a main skill without projectiles: 0."""
    dps, notes = models.voltaic_beams_model(2.0, True, 0.5, 40.0, "Lightning Arrow", "test", pob=60.7)
    assert dps == 0.0 and "taken out" in notes[0]["basis"] and "40.0" in notes[0]["play"]
    dps, notes = models.voltaic_beams_model(1.4, False, 1.0, 40.0, "Boneshatter", "test", pob=60.7)
    assert dps == 0.0 and "fires none" in notes[0]["basis"]
    assert "80.0" in models.voltaic_beams_model(2.0, True, 3.0, 40.0, "Lightning Arrow", "t")[1][0]["play"]


def test_empowered_sparks_three_per_power_charge_held_to_the_attacks():
    """GEM27: Sparks a second = min(3 x the supported skill's Power Charges, the build's attacks), each at PoB's Spark;
    PoB's flat entry out; no Power Charge or no attack: 0."""
    dps, notes = models.empowered_sparks_model(0.2, "test", 1.4, 50.0, "Siphoning Strike", pob=45.6)
    assert dps == pytest.approx(0.6 * 50.0) and notes[0]["entry_cut"] == {models.EMPOWERED_SPARKS: 45.6}
    assert models.empowered_sparks_model(1.0, "t", 1.4, 50.0, "Siphoning Strike")[0] == pytest.approx(70.0)
    assert models.empowered_sparks_model(0.0, "t", 1.4, 50.0, "Power Siphon")[0] == 0.0
    assert models.empowered_sparks_model(1.0, "t", 0.0, 50.0, "Power Siphon")[0] == 0.0


def test_empowered_sparks_wing_blast_three_a_heavy_stun_and_devour_three_corpses():
    """GEM28 (RESEARCH37 R37-4..6): Wing Blast's 15% Surpassing chance x a Unique's 20 Power = 3 Power Charges a Heavy
    Stun, held to the maximum (15% a row); Devour up to 3 corpses a use, Sacrifice's undead minions as lone-boss corpses;
    the capped reading a row."""
    each, row, how = models.wing_blast_power_a_stun(0, 3)
    assert each == pytest.approx(3.0) and row == pytest.approx(0.15) and "300%" in how
    assert models.wing_blast_power_a_stun(20, 3)[0] == pytest.approx(3.0)  # (400% held to 3)
    assert models.wing_blast_power_a_stun(20, 5)[0] == pytest.approx(4.0)
    assert models.corpse_charges(1.0, 3.0, 0.5, 0.0, 7.5) == (pytest.approx(0.5), 0.0)
    assert models.corpse_charges(1.0, 3.0, 5.0, 0.0, 7.5)[0] == pytest.approx(3.0)
    got, minions = models.corpse_charges(1.0, 3.0, 0.0, 6.0, 7.5)  # (6 undead, 3 wanted a second: 6x3 / (6 + 22.5))
    assert minions == pytest.approx(18.0 / 28.5) and got == pytest.approx(minions)
    dps, notes = models.empowered_sparks_model(0.3, "t", 10.0, 50.0, "Wing Blast", rows=[("15% a stun", 0.015)])
    assert dps == pytest.approx(45.0) and "15% a stun: 2.2 DPS" in notes[0]["play"]
    assert "capped" in notes[0]["play"] and "R37-4" in notes[0]["basis"]
    dps, notes = models.empowered_sparks_model(0.3, "t", 10.0, 50.0, "Wing Blast", sparks=11, gem="Empowered Sparks II",
                                               life=2.013)
    assert dps == pytest.approx(45.0) and notes[0]["label"].startswith("Empowered Sparks II")  # (R38-13: 1 a trigger)
    assert "2 hits a trigger (LOW): 90.0 DPS" in notes[0]["play"] and "135.0-180.0 DPS" in notes[0]["play"]


def test_descent_fragments_come_from_the_builds_own_line():
    """GEM28: Righteous Descent's fragment rate is read from the build's "Create a Fragment of Divinity in your Presence
    every N seconds" lines; none: 0 (PoB's once per cooldown); no lines handed in: The Ordained's 4 s."""
    line = "Create a Fragment of Divinity in your Presence every 4 seconds"
    assert models.descent_fragment_rate([line])[0] == pytest.approx(0.25)
    assert models.descent_fragment_rate([line, line.replace("4", "2")])[0] == pytest.approx(0.75)
    assert models.descent_fragment_rate(["+10 to Strength"])[0] == 0.0
    assert models.descent_fragment_rate(None)[0] == pytest.approx(0.25)


def test_voltaic_fulmination_takes_the_slain_adds_own_life():
    """GEM28 (R36-6, MECHC46's add Life): PoB's explosion scaled by the add's Life over PoB's normal monster's."""
    assert models.pob_monster_life(82) == pytest.approx(32956.0)
    base, _ = models.voltaic_fulmination_model(100.0, 1.0, 1.0, 0.3)
    small, notes = models.voltaic_fulmination_model(100.0, 1.0, 1.0, 0.3, life=(1318.0, 32956.0, "Affliction Totem"))
    assert small == pytest.approx(base * 1318 / 32956) and "Affliction Totem" in notes[0]["basis"]


def test_overgrown_counts_natures_archons_overgrow_while_it_is_up():
    """GEM28 (MECHC47): a Plant skill's Overgrown share with Nature's Archon up part of the fight: 1 - (1 - uptime) x
    (1 - the other sources' share); no Archon line: as before."""
    main = models.Skill(name="Vine Arrow", group=1)
    texts = ["Plants have a 20% chance to immediately Overgrow"]
    stats = {"Speed": 2.0, "_buff_expiry": (1.0, "")}
    before, _ = models.overgrown_model([main], main, {}, frozenset(), texts, stats)
    archon = texts + ["50% chance to gain Nature's Archon when your Plants Overgrow"]
    after, notes = models.overgrown_model([main], main, {}, frozenset(), archon, stats)
    up, _ = models.natures_archon_uptime(archon, 2.0 * 0.2)
    assert before[0]["value"] == pytest.approx(50.0 * 0.2)
    assert after[0]["value"] == pytest.approx(50.0 * (1 - (1 - up) * 0.8), abs=1e-3) and up > 0
    assert "Nature's Archon" in notes[0]["basis"]


def test_a_warcry_used_nonstop_runs_at_its_cast_rate_held_to_its_cooldown():
    """GEM28 (MECHB47): Twisted Pact's warcry rate is its castRate, not PoB's doubly slowed Speed; a cooldown holds it."""
    assert models.warcry_use_rate({"castRate": 1 / 1.547}, 0.334)[0] == pytest.approx(1 / 1.547)
    assert models.warcry_use_rate({"castRate": 1 / 1.547, "cooldown": 4.0}, 0.25)[0] == pytest.approx(0.25)
    assert models.warcry_use_rate({}, 0.334)[0] == pytest.approx(0.334)


def test_stomping_ground_counts_shield_charges_footsteps_at_the_boss():
    """GEM28 (RESEARCH38 R38-17): Shield Charge 2 shockwaves at the boss a use (rows 1 and 3); other skills PoB's."""
    dps, notes = models.stomping_ground_model(0.5, 40.0, "Shield Charge", pob=20.0)
    assert dps == pytest.approx(40.0) and notes[0]["entry_cut"] == {models.STOMPING_SHOCKWAVE: 20.0}
    assert "1 a use: 20.0" in notes[0]["play"] and "3 a use: 60.0" in notes[0]["play"]
    assert models.stomping_ground_model(0.5, 40.0, "Shield Charge")[0] == pytest.approx(40.0)
    assert models.stomping_ground_model(0.5, 40.0, "Whirling Slash") == (0.0, [])


def test_caltrops_support_lands_a_share_under_the_boss_and_none_with_pierce():
    """GEM28 (RESEARCH36 R36-15): a spear ending its flight on the boss drops its Caltrops within 3 m; ((r + 0.5) / 3)^2
    land under a boss of radius r (LOW), held to 20 active over 8 s; with Pierce / Chain / Fork none end on the boss;
    PoB's one-a-use entry out."""
    assert models.caltrops_under_boss_share(0.4) == pytest.approx(0.09)
    assert models.caltrops_under_boss_share(5.0) == 1.0
    dps, notes = models.caltrops_support_model(1.0, 1.0, "none", 1, 0.4, "0.4 m", 100.0, 1.2, "Explosive Spear",
                                               pob=120.0)
    assert dps == pytest.approx(9.0) and notes[0]["entry_cut"] == {models.CALTROPS: 120.0}
    assert notes[0]["confidence"] == "LOW" and "100.0" in notes[0]["play"] and "120.0" in notes[0]["play"]
    assert models.caltrops_support_model(10.0, 1.0, "none", 1, 0.4, "", 100.0, 10, "S")[0] == pytest.approx(2.5 * 0.09 * 100)
    assert models.caltrops_support_model(1.0, 0.0, "Pierce 1", 1, 0.4, "", 100.0, 1, "S")[0] == 0.0
    # (RESEARCH38 R38-3: area widens both radii; 0.111 -> 0.080 at +100% area for a 0.5 m boss)
    k = 2 ** 0.5
    assert models.caltrops_under_boss_share(0.5, 0.5 * k, 3.0 * k) == pytest.approx(0.0801, abs=1e-3)
    # (R38-4: Chain on a lone boss ends there, the 0 a row)
    dps, notes = models.caltrops_support_model(1.0, 1.0, "Chain", 1, 0.4, "", 100.0, 1, "S", chain_row=0.0)
    assert dps == pytest.approx(9.0) and "if Chain carries the spear on from a lone boss: 0.0" in notes[0]["play"]
    assert "uniform in distance" in notes[0]["play"] and "30% under the boss" in notes[0]["play"]  # (R38-16)


def test_nova_projectiles_send_one_of_a_circle_at_the_boss():
    """GEM29 (an OVERCOUNT; RESEARCH31 R31-11 (b)): Nova Projectiles spreads a use's projectiles evenly round a circle with
    one aimed, so the every-projectile counts (shotgun fragments, the crossbow sequence, Frost Darts) take only those
    within the boss's width; without Nova nothing changes."""
    assert models.nova_circle_hits(10, 0.3, 2.0) == 1 and models.nova_circle_hits(16, 1.0, 2.0) == 3
    assert models.nova_circle_hits(10, 0.3, 0.2) == 5 and models.nova_circle_hits(0.5, 0.3, 2.0) == 0.5
    nova = {models.NOVA_STAT: 1}
    shards = Skill(name="Galvanic Shards", support_stats=nova)
    assert models._shotgun_count(shards, {"ProjectileCount": 10}, shards)[0] == 1
    assert models.shotgun_fragments_share(shards, {"ProjectileCount": 10}, shards)[0] == 0.0
    plain = Skill(name="Galvanic Shards")
    assert models.shotgun_fragments_share(plain, {"ProjectileCount": 10}, plain)[0] == 900
    hvr = Skill(name="High Velocity Rounds", support_stats=nova)
    mods, notes = models.nova_projectiles_model(hvr, {"ProjectileCount": 7}, {})
    assert mods[0]["value"] == pytest.approx(100 * (1 / 7 - 1), abs=1e-3) and notes[0]["confidence"] == "LOW"
    darts = Skill(name="Frost Darts", support_stats=nova, spell=True)
    assert models.nova_projectiles_model(darts, {"ProjectileCount": 3.2}, {})[0][0]["value"] == pytest.approx(-68.75)
    assert models.nova_projectiles_model(Skill(name="Frost Darts"), {"ProjectileCount": 3.2}, {}) == ([], [])
    assert models.nova_projectiles_model(darts, {"ProjectileCount": 3.2}, {}, {models.NOVA_ENTRY}) == ([], [])
    assert models.NOVA_STAT in models.gem_stat_support_stats()
    # GEM30 (R39-12): the "circle ignores the aim" row, N x 2 asin(r / d) / 360 degrees; R39-11: impact projectiles uncut
    assert models.nova_unaimed_hits(10, 0.3, 2.0) == pytest.approx(10 * 2 * math.asin(0.15) / (2 * math.pi))
    assert models.nova_unaimed_hits(10, 0.3, 0.2) == 5 and models.nova_unaimed_hits(0.5, 0.3, 2.0) == 0.5
    assert "ignores the aim" in models.nova_projectiles_model(hvr, {"ProjectileCount": 7}, {})[1][0]["basis"]
    assert "ignores the aim" in models._shotgun_count(shards, {"ProjectileCount": 10}, shards)[1]
    for name in ("Lightning Spear", "Poisonburst Arrow"):
        assert models.nova_projectiles_model(Skill(name=name, support_stats=nova), {"ProjectileCount": 5}, {}) == ([], [])


def test_orb_of_storms_and_elemental_storm_cannot_leech():
    """GEM29 (an OVERCOUNT of recovery, probe POE2T5777): damage_cannot_be_reflected_or_leech_if_used_by_other_object
    on Orb of Storms / Elemental Storm: PoB leeched from them; the entry cuts Life and Mana Leech on the skill only."""
    stat = "damage_cannot_be_reflected_or_leech_if_used_by_other_object"
    for name in ("Orb of Storms", "Elemental Storm"):
        skill = Skill(name=name, level_stats={stat: 1})
        mods, _ = models.gem_stat_model([skill], main=skill, stats={})
        lines = {(m["line"], m.get("skill")) for m in mods if m.get("kind") == "Line"}
        assert {("Cannot Leech Life", name), ("Cannot Leech Mana", name)} <= lines
    other = Skill(name="Spark", level_stats={stat: 1})
    assert not [m for m in models.gem_stat_model([other], main=other, stats={})[0] if "Leech" in str(m.get("line"))]


def test_abyssal_apparition_casts_the_main_spell_again_for_each_power_charge():
    """GEM29 (an UNDERCOUNT: PoB never grants the Steward of Kulemak's skill): each main-Spell cast finding a Power
    Charge is cast again at 60% less (level 1, LOW; level 20's 50% a row); no Power Charge source: none; not for
    Channelling, Cooldown or triggered Spells."""
    from builder.search import conditions

    line = "Damaging Spells consume a Power Charge if able to trigger Abyssal Apparition"
    spark, types, stats = Skill(name="Spark", spell=True), {"Spell", "Damage", "Projectile"}, {"Speed": 2.0, "CritChance": 30.0}
    gains = conditions._charge_rate("Gain a Power Charge on Critical Hit", "Power", stats)[0]
    assert gains == pytest.approx(0.6)
    mods, notes = models.abyssal_apparition_model([line], spark, types, stats, gains, "crit")
    assert mods[0]["value"] == pytest.approx(100 * 0.3 * 0.4) and notes[0]["confidence"] == "LOW"
    assert "level 1's 60% less" in notes[0]["basis"]  # (GEM30: level 1 is now a row; level 20 at character 90)
    assert models.abyssal_apparition_model([line], spark, types, stats, gains, "crit", char_level=90)[1][0]["value"] == "x1.150"
    assert models.abyssal_apparition_model([line], spark, types, stats, 5.0, "")[0][0]["value"] == pytest.approx(40.0)
    assert models.abyssal_apparition_model([line], spark, types, stats, 0.0, "")[0] == []
    assert models.abyssal_apparition_model([line], spark, types | {"Channel"}, stats, 1.0, "")[0] == []
    assert models.abyssal_apparition_model([line], Skill(name="Spark", spell=True, cooldown=2.0), types, stats, 1.0, "")[0] == []
    assert models.abyssal_apparition_model([line], Skill(name="Boneshatter", attack=True), {"Attack"}, stats, 1.0, "")[0] == []
    assert models.abyssal_apparition_model([], spark, types, stats, 1.0, "") == ([], [])
    # GEM30 (R40-7): the grant levels with the character: 20 (50% less) at 90, 19 (51%) at 84-89, 1 below 3
    assert (models.apparition_level(90), models.apparition_level(89), models.apparition_level(2)) == (20, 19, 1)
    high = models.abyssal_apparition_model([line], spark, types, stats, 5.0, "", char_level=90)
    assert high[0][0]["value"] == pytest.approx(50.0) and "gem level 20" in high[1][0]["basis"]


def test_valakos_charge_arc_builds_half_the_electrocute():
    """GEM30 (an OVERCOUNT; RESEARCH39 R39-15): the Arc's electrocute_threshold_+%_final 100 doubles the threshold its
    buildup is compared with: "50% less Electrocute Buildup" on Valako's Charge, nothing on another skill."""
    stat = "electrocute_threshold_+%_final"
    valako = Skill(name="Valako's Charge", level_stats={stat: 100})
    mods = [m for m in models.gem_stat_model([valako], main=valako, stats={})[0] if "Electrocute" in str(m.get("line"))]
    assert mods == [{"kind": "Line", "line": "1% less Electrocute Buildup", "scale": 50.0, "skill": "Valako's Charge"}]
    other = Skill(name="Spark", level_stats={stat: 100})
    assert not [m for m in models.gem_stat_model([other], main=other, stats={})[0] if "Electrocute" in str(m.get("line"))]
    assert "Valako's Charge" in models.gem_stat_skills()


def test_lightning_conduit_adds_its_bolts_on_a_shocked_boss():
    """GEM30 (an UNDERCOUNT; PLANS6 kw-conduit): 1-2 additional bolts a cast on a Shocked boss, x(1 + b x share)."""
    lc = Skill(name="Lightning Conduit", spell=True, level_stats={models.CONDUIT_BOLTS_STAT: 2})
    mods, notes = models.lightning_conduit_model(lc, 0.5, "test")
    assert mods[0]["value"] == pytest.approx(100.0) and "x2.000" == notes[0]["value"]
    assert "always Shocked x3" in notes[0]["basis"]
    assert models.lightning_conduit_model(lc, 0.0, "none")[0] == []
    assert models.lightning_conduit_model(lc, 0.5, "t", {models.CONDUIT_ENTRY}) == ([], [])
    assert models.lightning_conduit_model(Skill(name="Spark"), 0.5, "t") == ([], [])
    assert models.lightning_conduit_model(Skill(name="Lightning Conduit"), 0.5, "t")[0] == []
    assert models.LIGHTNING_CONDUIT in models.gem_stat_skills()
    # GEM31 (R42-28c / d): a Gemling's alternate quality reaches the stat (2 + 4 at 20%); the bolts are full hits
    gemling = Skill(name="Lightning Conduit", spell=True, level_stats={models.CONDUIT_BOLTS_STAT: 6})
    mods, notes = models.lightning_conduit_model(gemling, 1.0, "test")
    assert mods[0]["value"] == pytest.approx(600.0) and notes[0]["confidence"] == "MEDIUM-HIGH"


def test_concoction_speed_row_keeps_pobs_rate_as_the_headline():
    """GEM31 (R42-28f, the manager's call): PoB's throws a second stay; the x0.85 reading is a row, no number moved."""
    notes = models.concoction_speed_note(Skill(name="Explosive Concoction"), 1.4025)
    assert notes[0]["value"] == pytest.approx(1.4025, abs=1e-3) and "1.19 a second" in notes[0]["basis"]
    assert models.concoction_speed_note(Skill(name="Spark"), 1.4) == []
    assert models.concoction_speed_note(Skill(name="Acidic Concoction"), 1.4, {models.CONCOCTION_SPEED_ENTRY}) == []


def test_life_sacrificed_on_use_is_counted_a_second():
    """GEM30 (kw-sacrifice's gem side): % of maximum Life x the supported skill's uses a second; none without a carrier."""
    life, notes = models.sacrifice_life_model([("Spark", 2.0, 10.0, "main"), ("Pain Offering", 0.25, 15.0, "duration")],
                                              1000.0)
    assert life == pytest.approx(2.0 * 100 + 0.25 * 150) and notes[0]["entry"] == models.SACRIFICE_ENTRY
    assert "UNVERIFIED" in notes[0]["basis"] and models.sacrifice_life_model([("Spark", 2.0, 0.0, "x")], 1000.0) == (0.0, [])
    assert models.SACRIFICE_STAT in models.gem_stat_support_stats()


def test_ravenous_swarm_and_hollow_focus_follow_their_repeat_interval():
    """GEM30 (R39-13): swarms alive min(3, D / I), I = 6 s / Cooldown Recovery, none beyond the swarms' reach; Hollow
    Focus's shockwaves at most one a bell, min(1 / I, PoB's rate), 0 out of reach."""
    swarm = Skill(name="Ravenous Swarm", cooldown_recovery=1.0, summon={"duration": 9.8},
                  level_stats={models.REPEAT_STAT: 6000, models.SWARM_MAX_STAT: 3, models.SWARM_RADIUS_STAT: 40})
    melee = Skill(name="Boneshatter", melee=True, attack=True)
    mods, notes = models.repeat_interval_model([swarm], melee, {}, {})
    assert mods[0]["scale"] == pytest.approx(100 * (9.8 / 6 - 1), abs=0.01) and notes[0]["value"] == "x1.63"
    fast = Skill(name="Ravenous Swarm", cooldown_recovery=1.5, summon={"duration": 9.8}, level_stats=swarm.level_stats)
    assert models.repeat_interval_model([fast], melee, {}, {})[1][0]["value"] == "x2.45"
    far = models.repeat_interval_model([swarm], melee, {}, {"enemyDistance": 80})
    assert far[0][0]["scale"] == pytest.approx(-100) and "none hits it" in far[1][0]["basis"]
    bells = Skill(name="Hollow Focus", attack=True, cast_time=1.0, level_stats={models.REPEAT_STAT: 2050})
    mods, notes = models.repeat_interval_model([bells], bells, {"Speed": 1.14}, {"enemyDistance": 20})
    assert mods[0]["value"] == pytest.approx(100 * ((1 / 2.05) / 1.14 - 1), abs=0.01)
    assert models.repeat_interval_model([bells], bells, {"Speed": 1.14}, {"enemyDistance": 90})[0][0]["value"] == -100
    assert models.repeat_interval_model([swarm], melee, {}, {}, {models.REPEAT_ENTRY}) == ([], [])
    assert {models.RAVENOUS_SWARM, models.HOLLOW_FOCUS} <= set(models.gem_stat_skills())


def test_moonbeam_follows_hits_capped_by_its_cooldown_at_the_buffs_uptime():
    """GEM31: one Moonbeam a Werewolf Melee Hit (its extra beams go to other targets), at most its cooldown rate, while
    Lunar Blessing lasts; PoB's own Full DPS entry taken out."""
    dps, notes = models.moonbeam_model(0.98, 109.5, 1.375, 0.5, "test", pob=107.1, main="Lunar Assault")
    assert dps == pytest.approx(0.98 * 0.5 * 109.5) and notes[0]["entry_cut"] == {models.MOONBEAM: 107.1}
    slow, _ = models.moonbeam_model(0.98, 109.5, 0.2, 1.0, "test")
    assert slow == pytest.approx(0.2 * 109.5)
    assert models.moonbeam_model(0.98, 109.5, 1.0, 0.0, "off")[0] == 0.0
    assert {"Attack", "Melee", "Wolf", "Shapeshift"} == models.WEREWOLF_MELEE_TYPES


def test_mirror_of_refraction_copies_from_every_projectile():
    """GEM31 / GEM32 (R44-2: the mirror lets projectiles pierce it): a mirror every I s, shattered by any of the main
    Spell's projectiles, which still hits the boss; at most one copy of the nova hits the boss (R35-15), with chance
    min(1, k x 2 asin(r / d) / 360); a one-projectile Spell counts too; one chain step a row."""
    mirror = Skill(name="Mirror of Refraction", cooldown_recovery=1.0,
                   level_stats={models.REPEAT_STAT: 2000, models.MIRROR_COPIES_STAT: 9})
    spark = Skill(name="Spark", spell=True)
    stats = {"ProjectileCount": 9, "Speed": 2.0}
    mods, notes = models.mirror_of_refraction_model([mirror, spark], spark, stats, {"enemyDistance": 50})
    radius = models.bosses.hit_radius(stats)[0]
    p = min(1.0, models.nova_unaimed_hits(9, radius, 5.0))
    assert mods[0]["value"] == pytest.approx(100 * min(0.5, 2.0 * 9) * p / 2.0, abs=1e-3) and notes[0]["confidence"] == "LOW"
    assert f"x{1 + 2 * 0.5 * p / 2.0:.3f}" in notes[0]["basis"]  # the one-chain-step row
    one = models.mirror_of_refraction_model([mirror, spark], spark, {"ProjectileCount": 1, "Speed": 2.0}, {"enemyDistance": 50})
    assert one[0][0]["value"] == pytest.approx(100 * min(0.5, 2.0) * p / 2.0, abs=1e-3)
    shotgun = Skill(name="Galvanic Shards", attack=True)
    assert models.mirror_of_refraction_model([mirror, shotgun], shotgun, stats, {})[0] == []
    assert models.mirror_of_refraction_model([spark], spark, stats, {}) == ([], [])
    assert models.mirror_of_refraction_model([mirror, spark], spark, stats, {}, {models.MIRROR_ENTRY}) == ([], [])
    assert models.MIRROR_OF_REFRACTION in models.gem_stat_skills()


def test_delayed_gratification_is_not_a_free_support():
    """GEM30 (PLANS7 / kw-difficulty): 2 uses per condition 2.5x harder to meet: x0.8, so it no longer scores x1."""
    stat = "difficulty_of_meeting_conditional_requirement_+%"
    skill = Skill(name="Tempest Flurry", attack=True, support_stats={stat: 150})
    lines = [m for m in models.gem_stat_model([skill], main=skill, stats={})[0] if "less Damage" in str(m.get("line"))]
    assert lines == [{"kind": "Line", "line": "1% less Damage", "scale": 20.0, "skill": "Tempest Flurry"}]
    assert stat in models.gem_stat_support_stats()


def test_inhibitor_supported_skills_consume_no_charges_in_gem_models():
    """GEM31 (the manager 09:10; MECHC49's conditions.inhibited_skills, R40-2): an Inhibitor-supported Glacial Lance /
    Oil Barrage is never empowered; the note says why. Falling Thunder, the Apparition, Ancestral Cry and Hollow Form are
    gated at their hooks the same way (Assumptions.inhibited)."""
    from builder.search.assumptions import Assumptions

    class Stub:
        def call(self, name, **_):
            if name == "groups":
                return {"groups": [{"enabled": True, "gems": [{"name": "Oil Barrage"}, {"name": "Inhibitor"}]}]}
            return {}

    a = Assumptions.__new__(Assumptions)
    a.build = Stub()
    assert a.inhibited() == {"Oil Barrage"}
    share, how = a.gated_set_share("Oil Barrage", [], set())
    assert share == 0.0 and "Inhibitor supports Oil Barrage" in how
    note = Assumptions.inhibited_note("Falling Thunder", "x")
    assert note["confidence"] == "HIGH" and "cannot consume" in note["basis"]


def test_frostbolt_explosion_only_where_the_bolt_meets_terrain():
    # (GEM32) probe POE2T5822: Explosion 166.81 vs Projectile 104.62, the Explosion scored; the game: the piercing bolt
    # "explodes on colliding with terrain", so against a boss with no wall at it only the Projectile hit counts
    sets = [{"label": "Projectile", "TotalDPS": 104.62}, {"label": "Explosion", "TotalDPS": 166.81}]
    fb = Skill("Frostbolt", 1)
    assert "Frostbolt" in models.GATED_ADDS
    mods, notes = models.gated_set_model(fb, "Explosion", sets, 0.0, "no terrain source")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (104.62 * 0.9 / 166.81 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem32-frostbolt-terrain-explosion"
    assert f"{104.62 + 166.81:,.0f}" in notes[0]["play"]  # the "boss against a wall" reading
    assert models.gated_set_model(fb, "Projectile", sets, 0.0, "none")[0] == []  # the hit alone: nothing to change


def test_lightning_arrow_beams_hit_only_other_enemies():
    # (GEM32, R44-11) probe POE2T5822: Beam 92.75 vs Projectile 87.13, the Beam scored; on a lone boss the arrow's hit
    sets = [{"label": "Projectile", "TotalDPS": 87.13}, {"label": "Beam", "TotalDPS": 92.75}]
    la = Skill("Lightning Arrow", 1, attack=True)
    mods, notes = models.lone_boss_set_model(la, "Beam", sets)
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100 * (87.13 / 92.75 - 1), abs=0.01)
    assert notes[0]["entry"] == "model-gem32-lightning-arrow-beams"
    assert models.lone_boss_set_model(la, "Projectile", sets) == ([], [])
    assert models.lone_boss_set_model(la, "Beam", sets, {"model-gem32-lightning-arrow-beams"}) == ([], [])
