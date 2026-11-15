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


@pytest.mark.parametrize("speed, press, stages", [
    (0.25, None, 0.25),   # a warcry every 4 s: 1.25 stacks on average (stage multiplier stacks - 1)
    (0.25, 0.5, -0.375),  # pressed half the time: 0.625 stacks, so less than one stack's damage
    (3.0, None, 9.0),     # spammed: capped at 10 stacks, PoB's placeholder
])
def test_corrupting_crys_stacks_follow_the_warcrys_use_rate(speed, press, stages):
    spark, cry, cc = Skill("Spark", 1, spell=True), Skill("Infernal Cry", 2), Skill("Corrupted Cry", 2, triggered=True)
    mods, notes = models.gem_stat_model([spark, cry, cc], stats={"Speed": 1.4}, main=spark,
                                        groups={2: {"speed": speed, "_press": press, "name": "Infernal Cry"}})
    assert [m["value"] for m in mods if m.get("name") == models.CRY_MULTIPLIER] == [0]  # (one stack: PoB floors it)
    assert [m["value"] for m in mods if m.get("name") == "Damage" and m.get("skill") == "Corrupted Cry"] == [
        pytest.approx(100 * stages)]
    assert models.gem_stat_side_groups([spark, cry, cc], spark) == [2]


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
