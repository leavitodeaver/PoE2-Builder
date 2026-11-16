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
    # 0.5 uses/s: 2 Seals a use, 1 + 2 projectiles; at 5 m a 1.5 m boss is asin(0.3) / pi of the turn
    mods, notes = models.gem_stat_model([arrow], stats={"Speed": 0.5, "ProjectileCount": 1}, main=arrow)
    q = models.salvo_hit_chance(5.0)
    assert q == pytest.approx(0.0970, abs=1e-3)
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
    reach = (2 * models.SALVO_BOSS_RADIUS + models.PRIMAL_WIDTH) / models.PRIMAL_SPACING
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
