"""GEM8: skill_specific_stat_description_mode's described effects (ledger gems-gem8.yaml)."""

from __future__ import annotations

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
    assert [m["value"] for m in mods] == [pytest.approx(100 * (average / 120 - 1), abs=0.01)]
    assert notes[0]["entry"] == models.WHIRL_ENTRY
    assert models.whirlwind_stage_model(Skill("Ice Strike", 1, stat_set="Melee"), sets) == ([], [])


def test_whirlwind_stage_more_read_as_quality_on_top_of_the_whirlwind_sets_150():
    sets = [{"label": "Melee", "TotalDPS": 200.0, "Speed": 2.0}, {"label": "Whirlwind", "TotalDPS": 120.0, "Speed": 2.0}]
    slash = Skill("Whirling Slash", 1, attack=True, melee=True, stat_set="Whirlwind",
                  level_stats={models.WHIRL_MORE_STAT: 40})  # (the first set's levelStats: the quality part only)
    average = (3 * 100 + 60 * (1 + 1.9 * 2)) / (3 / 2.0)
    mods, _ = models.whirlwind_stage_model(slash, sets)
    assert [m["value"] for m in mods] == [pytest.approx(100 * (average / 120 - 1), abs=0.01)]


def test_meteors_fall_at_the_games_rate_with_every_15th_large_and_walking_calamitys_uptime():
    sets = [{"label": "Small Meteor", "TotalDPS": 125.0, "AverageDamage": 100.0},
            {"label": "Large Meteor", "TotalDPS": 375.0, "AverageDamage": 300.0}]  # (PoB at 1.25/s)
    meteors = Skill("Meteors", 1, attack=True, triggered=True, stat_set="Large Meteor")
    active = 100 / 0.15 * 14 / 15 + 300 / 0.15 / 15
    mods, notes = models.meteor_rate_model(meteors, sets, 0.5, "half")
    assert [m["value"] for m in mods] == [pytest.approx(100 * (active * 0.5 / 375 - 1), abs=0.01)]
    assert notes[0]["entry"] == models.METEOR_ENTRY and "50%" in notes[0]["basis"]
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
    assert all(m["name"] != "AilmentMagnitude" for m in mods) and "UNDERCOUNT" in notes[0]["basis"]


def test_enervating_nova_more_at_the_far_edge():
    # (GEM20) "up to 100% more to enemies on the far edge of the ring"; none inside the inner 1.0 m
    assert models.nova_distance_more(0.5, 1.0, 2.6, 100) == 0.0
    assert models.nova_distance_more(1.8, 1.0, 2.6, 100) == pytest.approx(1.5)
    assert models.nova_distance_more(2.6, 1.0, 2.6, 100) == pytest.approx(2.0)
    nova = Skill("Enervating Nova", 1, spell=True)
    mods, notes = models.enervating_nova_model(nova, 1.0, 2.6, 100, 5.0)
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(100.0)
    assert "outside the ring" in notes[0]["basis"] and notes[0]["entry"] == "model-gem20-enervating-nova-distance"
    assert models.enervating_nova_model(nova, 1.0, 2.6, 100, 2.0)[1][0]["basis"].endswith("x1.62")
    assert models.enervating_nova_model(Skill("Spark", 1), 1.0, 2.6, 100, 2.0) == ([], [])


def test_shattering_spite_explodes_with_bloodstone_lances():
    # (GEM20) "Explosions deal 20% more Damage per Bloodstone Lance"; lances from other Spear skills' hits, max 30
    assert models.spite_lances(3.0, 0.5, 30) == pytest.approx(6.0)
    assert models.spite_lances(30.0, 0.5, 30) == 30
    spite = Skill("Shattering Spite", 1, attack=True)
    mods, notes = models.shattering_spite_model(spite, 0.5, 3.0, 30, 20, "Spear Throw 3 hits/s")
    assert mods[0]["name"] == "Damage" and mods[0]["value"] == pytest.approx(120.0)
    assert notes[0]["entry"] == "model-gem20-shattering-spite-lances"
    mods, notes = models.shattering_spite_model(spite, 0.5, 0.0, 30, 20, "none")
    assert mods[0]["value"] == pytest.approx(-99.0) and "no explosion" in notes[0]["basis"]
