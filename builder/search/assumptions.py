"""Assumptions for mechanics PoB doesn't calculate, so every skill can be optimized (PLAN.md §6:
the ledger's "inject a modifier" effect, ahead of the full ledger in Phase 4).

Each assumption is computed from the build itself and handed to PoB as a modifier, so it stacks
with everything else; the result lists it with an asterisk, its basis and its confidence.

First entry, Explosive Transmutation: PoB has no trigger for its "Transmutation Explosion"
(it fires when you collect a Remnant), so the explosion gets a trigger rate:
    min(Remnants per second from the build's sources × (1 + additional-Remnant chance),
        the explosion's cooldown cap including cooldown recovery)
On a single boss, Remnants come only from sources that create them without kills; their rates are
the user's audit (legacy/Custom builds/Explosive Transmutation - Shaman vs Acolyte.md, 0.5.4).

The custom models (builder/search/models.py) add Elemental Archon, meta-skill Energy, Elemental
Expression, Iron Ward's nova and the Traveller's Wisdom and Darkness notes. Skills they trigger besides the main
skill (spells in a meta gem, Elemental Expression) count through Full DPS: their groups are
included in it and the objective switches to Full DPS while any exist.
"""

from __future__ import annotations

import math
import os
import re

from builder.ledger import conditions
from builder.request.constraints import PRESSED_META, PRESSED_TOTEM, presses
from builder.search import conditions as conditions_mod, models, passive_models, recovery as recovery_mod
from builder.search.context import gem_of
from builder.search.conditions import (CHILL_FIELDS, INFUSED_SET, ailment_uptime, chill_share, curse_uses, natural_distance,
                                       shock_share)
from builder.search.objective import number, per_second
from builder.search.usage import BASE_RUN, usage_rules
from builder.search.state import Build

# Remnants per second on a single boss, at gem level 20, while the stated condition holds. Rates are the game files'
# level-20 stats (build 26476662, GrantedEffectStatSetsPerLevel; scratch search of 8 Nov, MECH18): Into the Breach's and
# Wildwood's Gifts' breach_flame_spawn_rate_ms 1210, Life Remnants' on-hit 3000 ms, Mana Remnants' and Sanguine
# Revelry's 2000 ms, Snap's 4 s and Frost Bomb's 6 s cooldowns, Orb of Storms' 12 s duration, Lingering Illusion's
# 76% chance of an additional Remnant, Remnants of Kalguur's 50% on Stun. The kill-only sources (Rip, Runic Extraction)
# make none on a lone boss. Where the files give no cadence on a boss, the rate is a labelled assumption.
ORB_OF_STORMS = "Orb of Storms"
BREACH = "Into the Breach"
ORB_PRESSES = "orb_presses"  # (MECHC25) build attribute for MECH-B's press_model: {skill: (Orbs a second, how)}
REMNANT_SOURCES = {
    "Into the Breach": (0.82645, "Acolyte of Chayula: Flames of Chayula in your Breach"),
    "Wildwood's Gifts": (0.82645, "The Hollow Mask: Ancient Blooms"),
    "Sanguine Revelry": (0.0, "once every 2 s while hitting a Bleeding target (hit_remnant_rate)"),
    "Mana Remnants": (0.0, "once every 2 s while critting an Elementally Ailmented target (hit_remnant_rate)"),
    "Life Remnants": (0.0, "once every 3 s on hit (hit_remnant_rate)"),
    "Snap": (0.25, "per cast that Consumes an ailment on the boss, before cooldown recovery (a Snap on a Frostbolt "
                   "leaves no Remnant: RESEARCH26 R26-13, ASSUMED, MEDIUM: patch 0.3.0's final note keeps 'from enemies')"),
    "Frost Bomb": (0.1667, "per cast, before cooldown recovery"),
    "Lingering Illusion": (0.352, "the boss destroys one of your dodge-roll copies every 5 s (the user's rate, 9 Nov), "
                                  "1.76 Power Charge Remnants each"),
    "Living Bomb": (0.0, "one seed at a time, exploding once the boss takes 200% of its Ailment Threshold "
                    "(living_bomb_rate, from the build's own damage)"),
    "Grim Feast": (0.0, "a Grim Remnant when one of your Reviving minions dies in your Presence (100%, game files), at "
                        "the rate the build kills them (models.minion_deaths; no flat rate, the user 11 Nov)"),
    "Orb of Storms": (0.0, "a Lightning Infusion Remnant per Orb, when its bolts run out or it expires, at the build's "
                          "own Orbs a second (orb_remnants; MECHC25: was a flat one per 12 s)"),
    "Remnants of Kalguur": (0.0, "50% per Stun, at most one per 3 s: the boss's Heavy and Light Stuns (models.stun_rate, "
                                 "from the build's own buildup and hit size; MECHB13), and 25% per kill at the enemies "
                                 "the build kills (the request's boss preset's adds; 0 on a lone boss; MECHB16)"),
    "Siphon Elements": (0.0, "a Cold Remnant on each Freeze (5% per Power, a boss's 20 Power: 100%), the boss's Freezes "
                             "from the main skill's buildup (models.buildup_rate); Ignite and Shock make one only on a "
                             "target without that ailment, which a boss you keep ailing rarely is: not counted, an "
                             "undercount"),
}
# Remnants of Kalguur (MetaRemnantsOfKalguurPlayer, build 26476662): ward_remnants_chance_to_spawn_remnant_on_stun_% 50,
# ward_remnants_spawn_remnant_on_stun_cooldown_ms 3000 ("no more than once every # seconds"): a Remnant every
# 3 s + the wait for the next successful Stun. Without the cap a fast stunner counted 50% of every Stun, an overcount
KALGUUR, KALGUUR_CHANCE, KALGUUR_COOLDOWN = "Remnants of Kalguur", 0.5, 3.0
# Siphon Elements (SiphonElementsPlayer): infusion_remnants_%_chance_to_spawn_cold_infusion_on_freezing_an_enemy 5, "per
# Power" (skill_stat_descriptions); "Unique monsters always have 20 Power" (KeywordPopups Power)
SIPHON, SIPHON_FREEZE_PER_POWER = "Siphon Elements", 0.05
# Living Bomb (LivingBombPlayer, build 26476662; MECHC1, FLAT_RATES A11: before, one seed every 5 s for any build):
# living_bomb_base_number_of_bombs_allowed 1, base_skill_effect_duration 15000 ("Seed duration"),
# living_bomb_damage_threshold_%_of_ailment_threshold 200 ("Explodes after enemy is dealt damage equal to 200% of its
# Ailment Threshold"). One seed planted (its cast) and exploding when the build's damage reaches the threshold: the main
# skill's CombinedDPS (other skills' damage not counted: an undercount) against PoB's EnemyAilmentThreshold. A seed
# whose 15 s run out first doesn't explode (MECHC26; PATCH_CHECK Q-C1, an OVERCOUNT before: it was counted as exploding
# then): 0.3.0's note "Living Bomb: No longer explodes at the end of its duration. Instead, it explodes upon dealing
# sufficient damage to the enemy, or just killing it" (no later note changes it: grep_notes --history "Living Bomb",
# newest 0.4.0d, a Prism of Belief fix) and ActiveSkills living_bomb_player's text ("Dealing enough damage to the enemy,
# or killing it outright, causes the seed to explode"). The next seed starts from 0, so at a steady DPS too low for 15 s
# none explodes (the damage's ups and downs within a fight aren't modelled: an undercount there).
LIVING_BOMB, LIVING_BOMB_SHARE, LIVING_BOMB_DURATION = "Living Bomb", 2.0, 15.0
LIVING_BOMB_NEVER = ("the seed's {:g} s run out first, and a seed that runs out doesn't explode (0.3.0: 'No longer "
                     "explodes at the end of its duration'): no explosion")
# (MECHB13; SEARCH7's routing: CombinedDPS is per use for a ShowAverage main skill (Sunder, Earthquake), so the main
# skill's damage per second comes from objective.per_second and the fields it reads)
LIVING_BOMB_FIELDS = ("EnemyAilmentThreshold", "CombinedDPS", "ShowAverage", "CullMultiplier", "ReservationDpsMultiplier",
                      "TotalDPS", "AverageDamage", "TotalDotDPS")


def living_bomb_rate(stats: dict, cast_time: float) -> tuple[float, str]:
    """Living Bomb explosions (Fire Infusion Remnants) per second: one seed at a time, replanted after each."""
    need = LIVING_BOMB_SHARE * number(stats.get("EnemyAilmentThreshold"))
    dps = per_second(stats)  # (the main skill's damage per second, also for a ShowAverage skill)
    if need <= 0 or dps <= 0:
        return 0.0, "no damage or no Ailment Threshold read: no seed explodes"
    wait = need / dps
    how = (f"a seed planted ({cast_time:g} s cast) explodes after {need:,.0f} damage (200% of the boss's Ailment "
           f"Threshold) at {dps:,.0f} DPS, {wait:.1f} s")
    if wait > LIVING_BOMB_DURATION:
        return 0.0, how + ": " + LIVING_BOMB_NEVER.format(LIVING_BOMB_DURATION)
    period = cast_time + wait
    return 1 / period, how + f": one per {period:.1f} s"


# Living Bomb as the MAIN skill (MECHC22; COV26 section 2, its probe POE2T2465: PoB reads no living_bomb stat (COV26:
# grep of src/Modules and SkillStatMap.lua) and counts an explosion every cast, AverageDamage 321.5 x Speed 1.667 = 535.9
# DPS). In the game the one seed (living_bomb_base_number_of_bombs_allowed 1) explodes once the boss is dealt 200% of
# its Ailment Threshold: cast, then wait for the build's OTHER damage (its other skills, Full DPS less Living Bomb's own
# hits, the damage computed outside PoB, and Living Bomb's own damage over time from the last explosion, e.g. its
# Ignite: whether damage over time counts toward "dealt damage" is UNVERIFIED, counted) to reach it within the seed's
# 15 s (a seed that runs out doesn't explode, as living_bomb_rate; MECHC26, Q-C1). One explosion per 1/Speed + wait
# seconds, so PoB's casts per second are cut by 1 / (1 + Speed x wait), as a "less Attack and Cast Speed" line on it (the
# explosion's damage, and so its Ignite's, stays whole). Alone, with no other damage, or with too little, it never
# explodes: the line is LIVING_BOMB_FLOOR of PoB's casts (0 in effect; not exactly 0, so the next pass can still read
# the casts per second back from PoB's Speed with the line on).
LIVING_BOMB_FLOOR = 1e-4
LIVING_BOMB_MAIN_FIELDS = ("Speed", "FullDPS", "TotalDPS", "TotalDotDPS", "EnemyAilmentThreshold")


def living_bomb_main_speed(stats: dict, speed: float, added: float = 0.0) -> tuple[float, str]:
    """The share of PoB's casts that explode (Living Bomb as the main skill), from the build's casts per second without
    this line (`speed`), its other damage and the boss's Ailment Threshold; and how."""
    need = LIVING_BOMB_SHARE * number(stats.get("EnemyAilmentThreshold"))
    full, hits, dot = (number(stats.get(f)) for f in ("FullDPS", "TotalDPS", "TotalDotDPS"))
    others = max(full - hits - dot, 0.0)
    feed = others + dot + max(added, 0.0)
    if speed <= 0 or need <= 0:
        return 1.0, "no casts or no Ailment Threshold read: PoB's count kept"
    how = (f"one seed at a time, exploding after {need:,.0f} damage (200% of the boss's Ailment Threshold) dealt by the "
           f"build's other damage, {feed:,.0f} DPS (other skills {others:,.0f}, its own damage over time {dot:,.0f}, "
           f"computed outside PoB {max(added, 0.0):,.0f}): ")
    if feed <= 0 or need / feed > LIVING_BOMB_DURATION:
        return LIVING_BOMB_FLOOR, how + LIVING_BOMB_NEVER.format(LIVING_BOMB_DURATION)
    wait = need / feed
    return (1.0 / (1.0 + speed * wait),
            how + f"{wait:.1f} s after each {1 / speed:.2f} s cast: one explosion per {1 / speed + wait:.1f} s, not one "
                  "per cast")
# created_remnants_have_%_chance_to_duplicate_pick_up_results (the Empowering Remnants passive, the "of Accumulation" to
# "of Multiplication" item suffixes): "#% chance for Remnants you pick up to count as picking up an additional Remnant"
# (stat_descriptions), "... you create to grant their effects twice" (passive_skill_stat_descriptions): one stat, both
# wordings. Counted as that share of pickups counting twice (a second Infusion, a second explosion trigger)
DUPLICATE_PICKUP = re.compile(r"(\d+(?:\.\d+)?)% chance for Remnants you (?:pick up to count as picking up an "
                              r"additional|create to grant their effects twice)", re.I)  # (PoB splits the line before "Remnant")


# Remnant sources that make one per cooldown while the main skill's hits meet a condition (MECH46, FLAT_RATES A1: before,
# each counted at its full 1/cooldown rate whenever the skill was there, an overcount). Game files (build 26476662,
# GrantedEffectStatSets): Sanguine Revelry create_furious_remnant_when_hitting_bleeding_enemies_once_every_x_ms 2000, Mana
# Remnants mana_remnants_spawn_remnant_on_crit_vs_elemental_ailment_enemy_every_X_ms 2000, Life Remnants
# life_remnants_spawn_remnant_on_hit_vs_enemy_every_X_ms 3000 ("once every": a cooldown, then the next qualifying hit)
HIT_REMNANTS = {"Sanguine Revelry": 2.0, "Mana Remnants": 2.0, "Life Remnants": 3.0}
HIT_REMNANT_FIELDS = ("Speed", "HitChance", "CritChance", "BleedChancePerHit", "BleedDuration", "IgniteChancePerHit",
                      "IgniteDuration", "ShockChancePerHit", "ShockDuration", "ChillChanceOnHit", "ChillDuration",
                      *models.IMMOBILISE_FIELDS, *CHILL_FIELDS)
# A Chill's base duration on the boss (MECHC2, the manager's ruling): KeywordPopups Chill (build 26476662) "lasts 2 seconds
# on players or 8 seconds on non-players by default" (HIGH; RESEARCH1 chill-duration-on-boss.md; PoB Misc.lua
# BaseChillDuration 8). PoB's ChillDuration (CalcOffence, the non-damaging ailments) scales it by the build's Chill
# Duration lines; this is the fallback when PoB gives none.
CHILL_SECONDS = 8.0


def hit_remnant_rate(name: str, stats: dict) -> tuple[float, str]:
    """Remnants per second from Sanguine Revelry, Mana Remnants or Life Remnants: one per cooldown + the wait for the next
    of the main skill's hits that qualifies (on a Bleeding boss; a Critical Hit on an Elementally Ailmented boss; any hit),
    the ailments at their uptime from the main skill's own hits. Other skills' hits aren't counted (an undercount)."""
    cooldown = HIT_REMNANTS[name]
    hits = number(stats.get("Speed")) * min(number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
    if name == "Sanguine Revelry":
        share = ailment_uptime(stats, "Bleed")
        how = f"hits on a Bleeding boss: {hits:.2f} hits/s, the boss Bleeding {share:.0%} of the fight"
    elif name == "Mana Remnants":
        crit = min(number(stats.get("CritChance")), 100.0) / 100.0
        chill_seconds = number(stats.get("ChillDuration")) or CHILL_SECONDS
        ups = {"Ignited": ailment_uptime(stats, "Ignite"), "Shocked": shock_share(stats),
               "Chilled": chill_share(stats),  # (with the game's 30% Chill floor; MECH54)
               "Frozen": models.immobilised_share(stats, "Freeze")[0],
               "Electrocuted": models.immobilised_share(stats, "Electrocute")[0]}
        absent = 1.0
        for up in ups.values():
            absent *= 1.0 - min(1.0, max(0.0, up))
        share = crit * (1.0 - absent)
        named = ", ".join(f"{k} {v:.0%}" for k, v in ups.items() if v > 0) or "no Elemental Ailment on the boss"
        how = (f"Critical Hits on an Elementally Ailmented boss: {hits:.2f} hits/s, {crit:.0%} critical, {named} "
               f"(taken as independent; a Chill lasting {chill_seconds:g} s on the boss, KeywordPopups Chill's 8 s base)")
    else:
        share, how = 1.0, f"hits on the boss: {hits:.2f} hits/s"
    events = hits * share
    rate = 1.0 / (cooldown + 1.0 / events) if events > 0 else 0.0
    return rate, f"once every {cooldown:g} s + the wait for the next of the main skill's {how}"


def snap_found(stats: dict) -> tuple[float, str]:
    """The chance a Snap cast finds an Ignite, Shock or Freeze on the boss to Consume (its ActiveSkills description:
    "Consume the elemental energy from a Frozen, Shocked or Ignited enemy"), each at its uptime from the main skill's own
    hits, taken as independent."""
    ups = {"Ignited": ailment_uptime(stats, "Ignite"), "Shocked": shock_share(stats),
           "Frozen": models.immobilised_share(stats, "Freeze")[0]}
    absent = 1.0
    for up in ups.values():
        absent *= 1.0 - min(1.0, max(0.0, up))
    named = ", ".join(f"{k} {v:.0%}" for k, v in ups.items() if v > 0) or "no Ignite, Shock or Freeze on the boss"
    return 1.0 - absent, named


def snap_stats(build) -> dict:
    """The main skill's stats for snap_found, with the other groups' Ignites Conditions.evidence found
    (build.ailment_others, MECH75; none before evidence() has run)."""
    stats = build.call("stats", fields=list(HIT_REMNANT_FIELDS))
    if others := getattr(build, "ailment_others", None):
        stats = {**stats, "_ailment_others": others}
    if ground := getattr(build, "ailment_ground", None):  # (Radiant Grief's Presence Ignite)
        stats = {**stats, "_ailment_ground": ground}
    return stats


def kalguur_rate(stuns: float) -> float:
    """Remnants of Kalguur per second from `stuns` Stuns per second: one per KALGUUR_COOLDOWN + the expected wait for
    the next Stun that makes one (KALGUUR_CHANCE each)."""
    return 1.0 / (KALGUUR_COOLDOWN + 1.0 / (KALGUUR_CHANCE * stuns)) if stuns > 0 else 0.0


KALGUUR_KILL_STAT = "ward_remnants_chance_to_spawn_orb_on_killing_enemy_%"


def kalguur_kill_rate(rate: float, condition: str, level_stats: dict, kills) -> tuple[float, str]:
    """Remnants of Kalguur's Stun rate plus its on-kill Remnants (MECHB16): the gem's chance per enemy killed at the
    enemies the build kills a second (`kills`: models.kill_rate's (enemies/s, all/s, how); the boss preset's adds)."""
    chance = models.number(level_stats.get(KALGUUR_KILL_STAT)) / 100
    enemies = models.number(kills[0]) if kills else 0.0
    how = kills[2] if kills and len(kills) > 2 and kills[2] else "no enemy dies before the boss"
    if chance <= 0:
        return rate, condition
    return rate + chance * enemies, condition + f"; {chance:.0%} a kill at {enemies:.3f} kills/s ({how})"


def siphon_freezes(stats: dict) -> tuple[float, str]:
    """Siphon Elements' Cold Remnants per second: the boss's Freezes (models.buildup_rate) x the chance per Freeze."""
    freezes = models.buildup_rate(stats, "Freeze")
    chance = min(1.0, SIPHON_FREEZE_PER_POWER * models.BOSS_POWER)
    return freezes * chance, (f"{chance:.0%} per Freeze (5% per Power, 20 Power), the boss frozen {freezes:.3f}/s by the "
                              "main skill's buildup; Ignite and Shock Remnants not counted (an undercount)")


def duplicate_pickups(texts: list[str]) -> float:
    """The share of Remnant pickups that count twice (DUPLICATE_PICKUP), capped at 1."""
    return min(1.0, sum(float(m.group(1)) for t in texts if (m := DUPLICATE_PICKUP.search(t))) / 100.0)
# Runic Reprieve (ActiveSkills runic_reprieve; RunicReprievePlayer channel_start_duration_as_%_of_cast_time 200, as
# models.CHANNEL_WINDUP) for "your Runic Ward has been damaged Recently" (Assumptions.ward_damaged_share; MECHC15)
RUNIC_REPRIEVE, CHANNEL_START_REPRIEVE = "Runic Reprieve", 2.0
ROUGHLY_RECENTLY = 4.0  # "Recently": the last 4 seconds (KeywordPopups Recently)


def remnant_multiplier(texts: list[str]) -> tuple[float, str]:
    """Each Remnant made counts this many times when collected (MECHC15; COV17): "N% chance to create an additional
    Remnant", "Remnants you create reappear once" (collected twice) and "N% chance for Remnants you pick up to count as
    picking up an additional Remnant" / "...create to grant their effects twice" (duplicate_pickups). PoB applies none
    (ModCache: each parses with leftover text, or to a LocalEffect nothing reads). Returns (multiplier, how)."""
    extra = sum(float(m.group(1)) for t in texts if (m := ADDITIONAL_REMNANT.search(t))) / 100.0
    twice = any(REAPPEAR.search(t) for t in texts)
    duplicate = duplicate_pickups(texts)
    parts = ([f"+{extra:.0%} additional Remnants"] if extra else []) + (["each reappearing once"] if twice else [])         + ([f"{duplicate:.0%} of pickups counting twice"] if duplicate else [])
    return (1.0 + extra) * (2.0 if twice else 1.0) * (1.0 + duplicate), ", ".join(parts)


NEEDS_MINIONS = {"Grim Feast"}  # its Remnants come from your minions' deaths
REAPPEAR = re.compile(r"Remnants you create reappear once", re.I)  # Storm's Recollection: each is collected twice
COOLDOWN_SOURCES = {"Snap", "Frost Bomb"}  # their rate scales with their own cooldown recovery
PRESSED_SOURCES = COOLDOWN_SOURCES | {"Living Bomb"}  # pressed for their Remnants: the press time (remnant_presses)
INFUSED_NOTE = ("the Infused cast needs an Elemental Infusion of its element, collected from a Remnant, and none of the "
                "build's skills makes that element's Infusions on a lone boss, so only the plain cast counts")
# Elemental Infusions per second on a single boss, by element (game files, build 26476662: the skills whose description
# leaves an Infusion Remnant, ActiveSkills search of 9 Nov, MECH19): Frost Bomb Cold, Orb of Storms Lightning, Living Bomb
# Fire, Snap the element of the ailment it consumes; rates as REMNANT_SOURCES. Siphon Elements' Cold ones on Freeze
# (siphon_freezes); its Fire and Lightning ones and Powered by Verisium's aren't counted yet (reasons below). The other Remnant sources (Into the Breach, Life, Mana, Power Charge
# Remnants) make no Infusions.
INFUSION_SOURCES = {
    "Frost Bomb": "Cold", "Orb of Storms": "Lightning", "Living Bomb": "Fire", "Snap": None,
}
INFUSION_UNCOUNTED = {
    "Siphon Elements": "Siphon Elements' Fire and Lightning Infusions (Ignite/Shock of a target without that ailment) "
                       "aren't counted: an undercount",
    "Powered by Verisium": "Powered by Verisium's Infusions (from Ward spent) aren't counted yet: an undercount",
}
SNAP_AILMENTS = {"conditionEnemyIgnited": "Fire", "conditionEnemyShocked": "Lightning", "conditionEnemyFrozen": "Cold"}
EQUILIBRIUM = re.compile(r"Create Lightning Infusion Remnants instead of Fire", re.I)  # Elemental Equilibrium
EQUILIBRIUM_SHIFT = {"Fire": "Lightning", "Lightning": "Cold", "Cold": "Fire"}
EXTRA_INFUSION_REMNANT = re.compile(r"(\d+(?:\.\d+)?)% chance to spawn an additional Infusion Remnant", re.I)
EXTRA_INFUSION = re.compile(r"(\d+(?:\.\d+)?)% chance to gain an additional Infusion$", re.I)
OTHER_INFUSION = re.compile(r"(\d+(?:\.\d+)?)% chance when collecting an Elemental Infusion to gain another different", re.I)
# (MECHC33: also the game's "Skills have N% chance to not remove Elemental Infusions but still count as consuming them",
# PoB ModCache 9391 / 9398 / 9400, parsed to nothing, which the older pattern missed: an UNDERCOUNT. The variant "... if
# you've lost an Archon Buff in the past 6 seconds" (9397) needs the Archon-loss share: not read, an undercount.)
KEEP_INFUSION = re.compile(r"^(?:Skills have )?(\d+(?:\.\d+)?)% chance to not remove (?:an Infusion|Elemental "
                           r"Infusions) but still count as consuming(?: them| it)?$", re.I)
VERISIUM_EVERY = re.compile(r"Every (?:(\d+(?:\.\d+)?) )?seconds?, gain an? Verisium Infusion", re.I)
GRANTED_SKILL = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$")  # (context.granted_by's pattern)
SECOND_WIND, SECOND_WIND_PCT = "Second Wind III", 1.0  # (MECHC29, G4-6: restore_%_life_on_skill_use_per_1000_ms_cooldown)
INFUSED_ELEMENT = re.compile(r"\b(fire|cold|lightning)-infused\b", re.I)


def orb_remnants(build, skill: dict) -> tuple[float, str]:
    """(Orbs of Storms a second, how): each Orb leaves one Lightning Infusion Remnant when its bolts run out or it
    expires (MECHC25; the manager, 14 Nov 18:54, from MECHB28: before, a flat one per 12 s, against CLAUDE.md's
    no-flat-rates rule). MECH-B's side-Orb cycle (models.orb_cycle: an Orb fed by the main Spell's casts ends at its
    maximum hits, sooner) when press_model exposes it on the build (ORB_PRESSES: {skill: (Orbs/s, how)}); until then
    the Orb's own cycle with timer bolts only: PoB's Duration for it (its duration modifiers included) plus its cast
    time, recast as each one ends (the play: keep one up; an undercount where the main Spell is cast near it)."""
    found = (getattr(build, ORB_PRESSES, None) or {}).get(ORB_OF_STORMS)
    if found is not None:
        return found[0], f"one Remnant per Orb, {found[0]:.3f} Orbs/s ({found[1]})"
    group = models.Skill.of(skill).group
    rows = build.call("useRates", indices=[group])["rates"] if group is not None else []
    row = rows[0] if rows else {}
    duration, cast = number(row.get("duration")), number(row.get("castTime"))
    if duration <= 0:
        return 0.0, "PoB gave the Orb no duration (or its group wasn't read): no source, 0"
    rate = 1 / (duration + cast)
    return rate, (f"one Remnant per Orb, recast as each ends: its {duration:.2f} s duration + {cast:.2f} s cast = "
                  f"{rate:.3f} Orbs/s (timer bolts only: casting the main Spell near it ends each Orb sooner, MECH-B's "
                  "orb_cycle, which isn't exposed to this yet: an undercount there)")


# Infusion Remnants out of reach (MECHC33; RESEARCH26 R26-35, ASSUMED: spawn spots HIGH from the skill text, the base
# pickup radius UNVERIFIED): each Remnant appears where its event happens: Snap's and Living Bomb's at the boss ("leaves
# behind"), Siphon Elements' at the target (LOW), Frost Bomb's at its Orb, which pulses on "nearby enemies" so sits at the
# boss (LOW, an inference); Orb of Storms' where the Orb is, which can be cast at your own feet (its bolts still reach the
# boss: the play row). "Walking over a Remnant collects it" (KeywordPopups Remnant). So a build that counts standing
# still at a ranged distance (PoB's conditionStationary with enemyDistance beyond melee) can't collect the boss-spot
# ones: before, both were counted (an OVERCOUNT). A melee build next to the boss collects them (radius UNVERIFIED,
# labelled). inner.choose_stationary already scores standing still against moving, so the search keeps the better of
# the two. Without the rule: every Remnant collected.
BOSS_SPOT_INFUSIONS = ("Snap", "Living Bomb", "Frost Bomb", "Siphon Elements")


def still_far(config: dict) -> bool:
    """Whether the build counts standing still at a ranged distance (PoB's configuration input: conditionStationary,
    enemyDistance in tenths of a metre beyond conditions.MELEE_DISTANCE)."""
    inputs = config.get("input") or config
    return bool(inputs.get("conditionStationary")) and number(inputs.get("enemyDistance")) > (
        conditions_mod.MELEE_DISTANCE * 10 + 1e-6)


def infusion_supply(present: dict[str, dict], lines: list[str], ailments: set[str] | dict[str, float],
                    siphon: tuple[float, str] = (0.0, ""), bomb: tuple[float, str] = (0.0, ""),
                    orb: tuple[float, str] = (0.0, ""), far: bool = False) -> tuple[dict[str, float], list[str]]:
    """Elemental Infusions per second by element ("Any": Verisium Infusions, usable as any element), and what makes them.
    `present`: the build's enabled skills by name; `lines`: its modifier lines; `ailments`: the resolver's active
    condition vars (which ailment Snap consumes), a set (each always up) or each var's share of the fight; `siphon`: Siphon Elements' Cold Remnants per second and how
    (siphon_freezes), used when the build has it."""
    supply = {"Fire": 0.0, "Cold": 0.0, "Lightning": 0.0, "Any": 0.0}
    uptimes = ailments if isinstance(ailments, dict) else dict.fromkeys(ailments, 1.0)
    used: list[str] = []
    made: dict[str, float] = {}
    for name, element in INFUSION_SOURCES.items():
        skill = present.get(name)
        if skill is None:
            continue
        if far and name in BOSS_SPOT_INFUSIONS:  # (MECHC33, R26-35)
            used.append(f"{name} 0/s: its Remnants appear at the boss, out of reach while you stand still at range "
                        "(without standing still: collected)")
            continue
        rate = REMNANT_SOURCES[name][0] * ((skill.get("cooldownRecovery") or 1.0) if name in COOLDOWN_SOURCES else 1.0)
        if name == LIVING_BOMB:  # (living_bomb_rate, from the caller)
            rate = bomb[0]
        if name == ORB_OF_STORMS:  # (MECHC25: orb_remnants, from the caller; was a flat one per 12 s)
            rate = orb[0]
        if element is None:  # Snap: the element of the ailment it consumes, from those on the boss at their uptimes
            # (MECH36: each ailment at its real share of the fight, not on only from 90%: Snap finds one when any is up,
            # 1 - the product of each one's absence, taken as independent; the element split by uptime, even when all
            # are always up)
            ups = {SNAP_AILMENTS[var]: min(1.0, max(0.0, float(up))) for var, up in sorted(uptimes.items())
                   if var in SNAP_AILMENTS and up > 0}
            if not ups:
                used.append(f"{name} 0/s (no Ignite, Shock or Freeze on the boss to consume)")
                continue
            absent = 1.0
            for up in ups.values():
                absent *= 1.0 - up
            found = 1.0 - absent
            for e, up in ups.items():
                made[e] = made.get(e, 0.0) + rate * found * up / sum(ups.values())
            shares = "/".join(e if up >= 1.0 else f"{e} {up:.0%} of the fight" for e, up in ups.items())
            used.append(f"{name} {rate * found:.2f}/s ({shares}, its consumed ailment"
                        f"{'s split by uptime' if len(ups) > 1 else ''}"
                        f"{f'; one on the boss at {found:.0%} of its casts' if found < 1.0 else ''})")
        else:
            made[element] = made.get(element, 0.0) + rate
            used.append(f"{name} {rate:.2f}/s {element}")
    if SIPHON in present and far:
        used.append(f"{SIPHON} 0/s: its Remnants appear at the target, out of reach while you stand still at range")
    elif SIPHON in present:
        made["Cold"] = made.get("Cold", 0.0) + siphon[0]
        used.append(f"{SIPHON} {siphon[0]:.2f}/s Cold ({siphon[1]})")
    if far and ORB_OF_STORMS in present:
        used.append(f"{ORB_OF_STORMS}: cast at your feet so its Remnants land within reach while you stand still (LOW)")
    used += [why for name, why in INFUSION_UNCOUNTED.items() if name in present]
    texts = [t.strip() for t in lines]
    if any(EQUILIBRIUM.search(t) for t in texts):
        made = {EQUILIBRIUM_SHIFT[e]: r for e, r in made.items()}
        used.append("Elemental Equilibrium shifts each Remnant's element (Fire to Lightning, Lightning to Cold, Cold to Fire)")
    remnants = 1.0 + sum(float(m.group(1)) for t in texts if (m := EXTRA_INFUSION_REMNANT.search(t))) / 100.0
    if any(REAPPEAR.search(t) for t in texts):
        remnants *= 2.0
    remnants *= 1.0 + duplicate_pickups(texts)
    gain = 1.0 + sum(float(m.group(1)) for t in texts if "weapon" not in t.lower() and (m := EXTRA_INFUSION.search(t))) / 100.0
    other = sum(float(m.group(1)) for t in texts if (m := OTHER_INFUSION.search(t))) / 100.0
    for e, rate in made.items():
        rate *= remnants * gain
        supply[e] += rate
        for o in ("Fire", "Cold", "Lightning"):  # "another different" one: randomly either other element (the user, 9 Nov): evenly, its average
            if o != e:
                supply[o] += rate * other / 2
    for t in texts:
        if m := VERISIUM_EVERY.search(t):
            every = float(m.group(1) or 1.0)
            supply["Any"] += gain / every
            used.append(f"a Verisium Infusion every {every:g} s (any element)")
    if remnants != 1.0:
        used.append(f"x{remnants:g} Infusion Remnants (additional ones, collected twice, or pickups counting twice)")
    if gain != 1.0:
        used.append(f"+{gain - 1:.0%} chance of an additional Infusion")
    if other:
        used.append(f"{other:.0%} chance of another, different Infusion (randomly either other element, the user's word of 9 Nov: counted evenly, its average)")
    return supply, used


ADDITIONAL_REMNANT = re.compile(r"(\d+(?:\.\d+)?)% chance to create an additional Remnant", re.I)
REMNANT_CONSUMERS = {"Transmutation Explosion"}


RESCORE_MODELS = os.environ.get("BUILDER_RESCORE_MODELS") == "1"


def charm_stats(build: Build) -> dict:
    """The charm charges the build's equipped charms spend per second against a boss (flasks.charm_charges_used), for
    Cast on Charm Use (Bijouborne)."""
    from builder.search.flasks import CHARM_SLOTS, Charges, charm_charges_used

    gain = Charges.of(build)
    raws = [build.equipped[slot].raw for slot in CHARM_SLOTS[:gain.charm_limit] if build.equipped.get(slot) is not None]
    infos = build.call("flaskData", raws=raws)["results"] if raws else []
    spent, basis = charm_charges_used(list(zip(infos, raws)), gain)
    return {"_charm_charges": spent, "_charm_basis": basis}


def enemy_level(build: Build) -> int:
    """The monster level PoB scores the boss at (MECHC26, for recovery.leech_resistance): the level PoB used
    (MECH76: the bridge's LedgerEnemyLevel, CalcSetup 875's env.enemyLevel, which takes the boss type's placeholder,
    82 for the Guardian / Pinnacle default, before min(85, character level)); else the config's Enemy Level, else
    min(PoB's MaxEnemyLevel 85, the character's level)."""
    try:
        scored = number(build.call("stats", fields=["LedgerEnemyLevel"]).get("LedgerEnemyLevel"))
    except (KeyError, AttributeError, TypeError):
        scored = 0.0
    if scored > 0:
        return int(scored)
    try:  # (a stand-in build in the unit tests answers neither call: the context's level, else PoB's maximum)
        configured = number((build.call("getConfig").get("input") or {}).get("enemyLevel"))
        level = number(build.call("summary").get("level"))
    except (KeyError, AttributeError, TypeError):
        configured, level = 0.0, number(getattr(getattr(build, "ctx", None), "level", 0))
    if configured > 0:
        return int(configured)
    return int(min(recovery_mod.MAX_ENEMY_LEVEL, level or recovery_mod.MAX_ENEMY_LEVEL))


DOT_HITS_ES_LINE = re.compile(r"^Damage over Time cannot bypass your Energy Shield$", re.I)  # (MECHC35, Decree of Loyalty)
DOT_BYPASSES_ES_LINE = re.compile(r"^Damage over Time bypasses your Energy Shield$", re.I)  # (MECHC37, The Black Doubt)


# Purple Flames of Chayula (MECHC26, G1-1): "for 8 seconds, and stacks up to 10 times" (KeywordPopups FlamesOfChayula)
PURPLE_SECONDS, PURPLE_MAX = 8.0, 10


def purple_stacks(rate: float, seconds: float = PURPLE_SECONDS, most: int = PURPLE_MAX) -> float:
    """Average stacks of a buff gained `rate` times a second, each stack lasting `seconds`, at most `most`: min(most,
    rate x seconds) (NOTES2's R3, MEDIUM; the manager, 15 Nov 01:01). Below the cap it is exact for any arrival pattern
    (Little's law: average stacks = arrivals x stay); near the cap random arrivals hold a little less (the previous
    Erlang-loss average undercounted regular arrivals: 6.19 against 6.61 for one Purple flame per 1.21 s)."""
    return min(float(most), max(rate, 0.0) * seconds)


# Mark of Siphoning I / II (MECHC28; PATCH_CHECK H1-9, an UNDERCOUNT of Mana / Life recovery): "Enemies Marked by
# Supported Skills cause you to Leech Mana [I and II] / Life [II] equal to 8% of Physical Attack Damage" (0.5.0, the
# newest entry: grep_notes --history "Mark of Siphoning"; game files GrantedEffectStatSets SupportMarkOfSiphoningPlayer /
# PlayerTwo: marked_enemies_leech_physical_attack_damage_taken_as_{mana,life}_to_you_permyriad 800). PoB never reads
# the two stats (checked: no "marked_enemies_leech" in PoB's SkillStatMap, Modules or ModCache, nor in builder/). The
# leech counts at the share of the fight the boss carries the Mark (models.mark_model's own share, read through its
# "Lines while the boss is Marked" note), as PoB's "Leech N% of Physical Attack Damage as Mana / Life" line (ModCache
# 8401-8406: PhysicalDamageManaLeech / LifeLeech, Attack), so PoB's leech caps and the boss's Leech resistance
# (recovery.leech_resisted) apply to it as to any leech. A share-scaled leech percentage is linear in the leech it
# makes below the leech-rate cap (an approximation near the cap).
# Demon Form (MECHC28; recovery.demon_form_cycle has the game text and sources; Assumptions.demon_form)
DEMON_FORM, DEMON_LOSS_STAT = "Demon Form", "demon_form_life_loss_per_minute_per_stack"
DEMON_MAX, DEMON_COOLDOWN = 10, 15.0  # (0.2.0: maximum 10, cooldown 15 s at every level; the cooldown read from useRates)
DEMON_NO_MAX = re.compile(r"^demonflame has no maximum$", re.I)  # (Mastered Darkness; ModCache 7208)
# a non-Spell skill the player doesn't press: kept up (Persistent), or used by something else (Triggered, Meta)
NOT_PRESSED_TYPES = {"Persistent", "Triggered", "Meta"}

SIPHONING = {"Mark of Siphoning": ("Mana",), "Mark of Siphoning II": ("Mana", "Life")}
SIPHONING_PERCENT = 8.0
SIPHON_PROBE_LINE = "Enemies you Mark take 1% increased Damage"  # (a MARKED_LINES text: mark_model notes its share)


def siphoning_model(found: list, stats: dict, main, groups: list[dict], mark_args: tuple) -> tuple[list[dict], list[dict]]:
    """Mark of Siphoning's leech lines at the boss's Marked share. `found`: the build's models.Skill list; `groups`: the
    bridge's groups (the support sits in the Mark's group); `mark_args`: (metres, disabled, rates) as mark_model is
    called with. The Mark on the boss is mark_model's first (one Mark on an enemy at a time)."""
    marks = [s for s in found if s.name in (*models.MARKS, models.PREDATORS_MARK) and s is not main]
    if not marks:
        return [], []
    mark = marks[0]
    group = next((g for g in groups if g.get("index") == mark.group), None)
    pools = {p for g in (group or {}).get("gems") or [] if g.get("enabled", True)
             for p in SIPHONING.get(g.get("name"), ())}
    if not pools:
        return [], []
    metres, disabled, rates = mark_args
    _, probe = models.mark_model(found, stats, main, [{"text": SIPHON_PROBE_LINE}], metres, disabled, rates=rates)
    share = next((float(n["value"]) for n in probe if n.get("label", "").startswith("Lines while the boss is Marked")),
                 1.0)
    how = next((n["basis"].split(": marked")[0] for n in probe
                if n.get("label", "").startswith("Lines while the boss is Marked")), f"{mark.name} kept on the boss")
    if share <= 0:
        return [], [{"label": "Mark of Siphoning: leech while the boss is Marked*", "value": "0",
                     "basis": f"the boss is never Marked ({how})", "confidence": "MEDIUM"}]
    percent = SIPHONING_PERCENT * share
    mods = [models.line(f"Leech 1% of Physical Attack Damage as {pool}", percent) for pool in sorted(pools)]
    return mods, [{"label": "Mark of Siphoning: leech while the boss is Marked*",
                   "value": f"{percent:.2f}% of Physical Attack Damage as {' and '.join(sorted(pools))}",
                   "basis": (f"{SIPHONING_PERCENT:g}% (game files, 0.5.0) x the boss Marked {share:.0%} of the fight "
                             f"({how}); PoB's leech rules and the boss's Leech resistance apply to it"),
                   "confidence": "MEDIUM (the Marked share is mark_model's; linear below the leech-rate cap)"}]


# Soul Eater (MECHC29; PATCH_CHECK H5-5, COV34's routed line; the manager 15:19). The game (KeywordPopups SoulEater,
# build 26476662): "Each Soul grants 1% increased Skill Speed. You can have up to 50 eaten Souls, and lose a Soul every
# 0.5 seconds if you have not eaten one in the past 4 seconds." PoB's own Soul Eater (CalcSetup 982-988: 5% Attack and 5%
# Cast Speed a stack, SoulEaterMax 45) is the PoE1 rule, stale: never fed (my brief). Its sources PoB doesn't parse
# (ModCache 7234-7235, {nil, ...}): "Eat a Soul when you Hit an enemy with an Open Weakness" (UniqueSoulEaterOpenWeakness1,
# gain_soul_eater_when_hitting_a_rare_or_unique_enemy_that_has_open_weakness) and "Eat a Soul when you Hit a Unique Enemy,
# no more than once every 0.5 seconds" (the Vaal-cultivated UniqueMutatedVaalGainSoulEaterStackOnHit,
# gain_soul_eater_stack_on_hit_vs_unique_cooldown_ms 500). (No patch-note line for either: grep_notes --history "Soul
# Eater|Eat a Soul|Open Weakness"; not proof of no change.) Simulated over the fight: the main skill's hits
# (models.hits_per_second; other skills' hits not counted, an undercount) eat one each, the Unique line at most one per
# its cooldown all fight, the Open Weakness line while the boss has one. The boss's Open Weakness (KeywordPopups
# RevealWeakness: "45% of their maximum Life (but in random segments)"; poe2wiki Open Weakness: "45% of the target's life
# bar, randomly distributed in 3-4 segments"): MECH78's time share conditions.open_weakness_up when it's in this build
# (train 59), else COV33's 45% with a "Reveal Weaknesses" line; split into 3 and into 4 even segments (positions are
# random in game: even spacing, MEDIUM) and the two averaged. The fight: the boss's Life / PoB's Full DPS, from 0 souls
# (the ramp counts). "+N to maximum number of eaten souls" / "Maximum N eaten souls" change the 50. The souls go to PoB as
# its own "1% increased Skill Speed" line x the average (Speed, Warcry Speed, Totem Placement Speed INC).
SOUL_MAX, SOUL_WAIT, SOUL_LOSS_EVERY, SOUL_SEGMENTS = 50, 4.0, 0.5, (3, 4)
SOUL_OPEN_WEAKNESS = re.compile(r"^Eat a Soul when you Hit an enemy with an Open Weakness$", re.I)
SOUL_UNIQUE = re.compile(r"^Eat a Soul when you Hit a (?:Rare or )?Unique Enemy, no more than once every "
                         r"(\d+(?:\.\d+)?) seconds?$", re.I)
SOUL_MAX_PLUS = re.compile(r"^([+-]\d+) to maximum number of eaten souls$", re.I)
SOUL_MAX_SET = re.compile(r"^maximum (\d+) eaten souls$", re.I)
SOUL_REVEAL = re.compile(r"^Reveal Weaknesses against Rare and Unique enemies$", re.I)
SOUL_LINE = "1% increased Skill Speed"


BLOCK_CAP = 90.0  # PoB's data.misc.BlockChanceCap (MECHC30: heavy_stunned_self's raised-shield Block)
RAISED_TAG = "RaisedShield"  # (the raised-shield Block and Evasion mods' source tag, Assumption:RaisedShield)
# Turtle Charm (MECHC32; RESEARCH27 R27-5, wording-rule-exceptions.md; the manager, 15 Nov 23:30: "override" vs "75 +
# adders, capped at 90", the lower the headline). The Warbringer notable's "Maximum Block chance is 75%" (stat
# maximum_block_chance_override) beside "+N% to maximum Block chance" (additional_maximum_block_%) lines: PoB OVERRIDEs
# and ignores every adder (CalcDefence 1008), so a NEGATIVE adder (Svalinn's -(20-10)%, a unique amulet's -10%) is lost:
# an OVERCOUNT if the game adds it. Unsettled (UNVERIFIED: no test or GGG word; one forum guess that Svalinn's minus still
# applies, 3826682 p38): both readings, the lower the headline (the user's wording rule, mod-wording-sets-vs-adds.md:
# set-plus-adder pairs show both). On PoB as an assumption OVERRIDE, which PoB reads before the notable's own: the
# config tab's mods join env.modDB before the passives' (CalcSetup 1033 vs 1789) and ModDB:Override takes the first
# (ModDB.lua 344-364); probed by test_engine_models' Turtle Charm test. Lines that add to the maximum (checked: PoB's
# ModCache, every BlockChanceMax BASE line is "+/-N% to maximum Block chance"; the game's other stat,
# additional_maximum_block_%_if_blocked_with_active_block_recently, a rune's "+5% ... if you've Blocked with a raised
# Shield Recently", which PoB doesn't parse: not counted, named in the note). Saffell's Frame ("Modifiers to Maximum
# Block Chance instead apply to Maximum Resistances"): the adders go to resistances, so no Block reading; PoB converts 0
# with the override (CalcDefence 951), the game unknown: named in the note.
TURTLE_SET = re.compile(r"^Maximum Block chance is (\d+(?:\.\d+)?)%$", re.I)
MAX_BLOCK_ADDER = re.compile(r"^([+-]\d+(?:\.\d+)?)% to maximum Block chance$", re.I)
MAX_BLOCK_CONDITIONAL = re.compile(r"^[+-]\d+(?:\.\d+)?% to maximum Block chance (?:if|while|when) ", re.I)
MAX_BLOCK_TO_RES = re.compile(r"^Modifiers to Maximum Block Chance instead apply to Maximum Resistances$", re.I)
TURTLE_TAG = "TurtleCharm"
# Widow's Reign's line (MECHC32; objective.life_reserved_open): the item shows it over two lines, PoB's sourceLines too
LIFE_RESERVED = re.compile(r"Life that would be lost by taking Damage is instead Reserved\s+until you take no Damage to "
                           r"Life for (\d+(?:\.\d+)?) seconds", re.I)
# Stalking Panther + "Evasion Rating is doubled if you have not been Hit Recently" (MECHC32; RESEARCH27 R27-4 (3),
# wording-sets-vs-adds-audit.md item 3; the manager, 15 Nov 23:30: "x2 headline vs x4, labelled UNVERIFIED"). PoB parses
# the Huntress notable's "Evasion Rating from Equipped Helmet, Gloves and Boots is doubled" as Evasion MORE 100 on those
# slots with no globalLimit (ModParser 2782) and the unique's line as MORE 100 under not BeenHitRecently with its own
# globalLimit "EvasionDoubledLimit" (ModCache 7401), so the two multiply: x4 on those slots. The user's wording rule:
# "doubled" doubles once (a forum test of Lycosidae + Resolute Technique), but the two lines cover different scopes (three
# slots vs all Evasion), so the game might apply both: UNVERIFIED (no test or GGG word). Both readings, the lower the
# headline (a defence): those slots' Evasion at x2, not x4, while not Hit Recently (Evasion MORE -50 on the three slots
# under the same condition, so it moves only while PoB applies the unique's doubling); PoB's x4 is the other reading.
# Other "doubled" Evasion lines (checked: PoB's ModCache, every Evasion MORE 100 line: these two only; "doubled against
# Projectile / Melee Attacks" are other stats, ProjectileEvasion / MeleeEvasion, ModParser 4263-4264, not stacked here).
PANTHER_LINE = re.compile(r"^Evasion Rating from Equipped Helmet, Gloves and Boots is doubled$", re.I)
EVASION_DOUBLED_NOT_HIT = re.compile(r"^Evasion Rating is doubled if you have not been Hit Recently$", re.I)
PANTHER_SLOTS = ["Helmet", "Boots", "Gloves"]
PANTHER_TAG = "StalkingPantherOnce"


def turtle_charm_readings(texts: list[str]) -> dict | None:
    """{set, adders, override, added, headline, conditional, saffell} for a build with a "Maximum Block chance is N%" line
    (None without one): `override` = PoB's reading (the set value, adders ignored), `added` = set + adders capped at
    BLOCK_CAP, `headline` = the lower of the two."""
    lines = [t.strip() for t in texts]
    sets = [float(m.group(1)) for t in lines if (m := TURTLE_SET.match(t))]
    if not sets:
        return None
    value = min(sets)
    adders = [float(m.group(1)) for t in lines if (m := MAX_BLOCK_ADDER.match(t))]
    saffell = any(MAX_BLOCK_TO_RES.match(t) for t in lines)
    added = value if saffell else min(value + sum(adders), BLOCK_CAP)
    return {"set": value, "adders": adders, "override": value, "added": added, "headline": min(value, added),
            "conditional": [t for t in lines if MAX_BLOCK_CONDITIONAL.match(t)], "saffell": saffell}


def soul_eater_average(fight: float, segments: list[tuple[float, float]] | None, inside: float, outside: float,
                       cap: int = SOUL_MAX, dt: float = 0.05) -> float:
    """Average eaten souls over a `fight` of that many seconds from 0: eating `inside` souls a second within `segments`
    (None: all fight) and `outside` a second elsewhere, at most `cap`; one lost every 0.5 s after 4 s without eating."""
    if fight <= 0 or cap <= 0:
        return 0.0
    souls, eat, lose, last, total, t = 0, 0.0, 0.0, -SOUL_WAIT, 0.0, 0.0
    while t < fight:
        rate = inside if segments is None or any(a <= t < b for a, b in segments) else outside
        eat += rate * dt
        while eat >= 1:
            souls, eat, last, lose = min(cap, souls + 1), eat - 1, t, 0.0
        if t - last >= SOUL_WAIT and souls > 0:
            lose += dt / SOUL_LOSS_EVERY
            while lose >= 1 and souls > 0:
                souls, lose = souls - 1, lose - 1
        total += souls * dt
        t += dt
    return total / fight


def even_segments(fight: float, share: float, count: int) -> list[tuple[float, float]]:
    """`count` equal segments covering `share` of a `fight`, evenly spaced (the first after a gap)."""
    length, gap = share * fight / count, (1 - share) * fight / (count + 1)
    return [(gap * (i + 1) + length * i, gap * (i + 1) + length * (i + 1)) for i in range(count)]


# Zenith I / II (MECHC26, MC-3): "Supported Skills deal more damage while you are above 90% of your Maximum Mana"
ZENITH, ZENITH_STAT, ZENITH_ABOVE = "Zenith", "support_spell_damage_+%_final_while_above_90%_maximum_mana", 0.9


def zenith_share(stats: dict) -> tuple[float, str]:
    """(share of the fight above 90% of maximum Mana, how) for a build casting its main skill at `Speed` a second, each
    cast costing `ManaCost`, regenerating `ManaRegenRecovery` a second. Spending at least what it regenerates
    (ManaPerSecondCost >= regeneration), it never stays at the top: 0. Otherwise each cast drops it from (near) full by
    its cost, and it's below 90% for (cost - 10% of maximum) / regeneration seconds of each cast's 1 / Speed (refilled
    before the next cast, as cost x Speed < regeneration)."""
    return conditions_mod.mana_above_share(stats, ZENITH_ABOVE)  # (MECHC37: shared with the Full Mana box)


# Low / Missing Runic Ward (MECHC26; NOTES3 DEAD-1 / DEAD-2, the manager 15 Nov 01:25, for COV-A's rune lines):
# KeywordPopups LowWard "You are on Low Runic Ward if you have 35% of your maximum Runic Ward or less"; Missing: below
# its maximum. From what spends the Ward (build.ward_sustain: each Ward-cost skill's (planned, paid, cost) and the total
# WardRecoveryPerSecond, Assumptions.ward_paid / models.ward_plan).
LOW_WARD = 0.35


def elemental_ailment_share(stats: dict) -> tuple[float, str]:
    """(share of the fight the boss carries any Elemental Ailment from the main skill's hits, which ones): Ignite, Shock,
    Chill, Freeze, Electrocute at their uptimes, taken as independent (as the Mana Remnants reading)."""
    ups = {"Ignited": ailment_uptime(stats, "Ignite"), "Shocked": shock_share(stats), "Chilled": chill_share(stats),
           "Frozen": models.immobilised_share(stats, "Freeze")[0],
           "Electrocuted": models.immobilised_share(stats, "Electrocute")[0]}
    absent = 1.0
    for up in ups.values():
        absent *= 1.0 - min(1.0, max(0.0, up))
    return 1.0 - absent, ", ".join(f"{k} {v:.0%}" for k, v in ups.items() if v > 0)


def briarpatch(build: Build, main: models.Skill | None, found: list[models.Skill]) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Briarpatch (models.briarpatch_model, MECHB31): the main attack's Physical damage a second,
    the gem's level, and the build's own Spells."""
    if not any(s.name == models.BRIARPATCH for s in found):
        return 0.0, []
    level = next((g.get("level") for grp in build.groups() for g in grp["gems"] if g["name"] == models.BRIARPATCH), 20)
    physical = 0.0
    if main is not None and main.attack:
        # (PoB sets the per-type hit average on each hand's output: MainHand.PhysicalHitAverage, probe POE2T1198)
        got = build.call("stats", fields=["MainHand.PhysicalHitAverage", "PhysicalHitAverage", "Speed", "HitChance"])
        hit = number(got.get("MainHand.PhysicalHitAverage")) or number(got.get("PhysicalHitAverage"))
        physical = (hit * number(got.get("Speed"))
                    * (number(got.get("HitChance")) or 100.0) / 100.0)
    spells = [s.name for s in found if s.spell and not s.triggered and s.name != models.BRIARPATCH]
    added, note = models.briarpatch_model(physical, int(number(level)) or 20, spells)
    return added, [note]


def faerie_fire(build: Build, found: list[models.Skill]) -> tuple[list[dict], list[dict]]:
    """(mods, notes) for Periphery's Azmerian Swarms (models.faerie_fire_model, MECHB31): the main skill's hits, the boss's
    Elemental Ailment share and your Dexterity."""
    if not any(s.name == models.AZMERIAN_SWARMS for s in found):
        return [], []
    stats = build.call("stats", fields=[*HIT_REMNANT_FIELDS, "Dex"])
    share, named = elemental_ailment_share(stats)
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    return models.faerie_fire_model(hits, share, number(stats.get("Dex")), named)


def ward_low_shares(sustain: dict, pool: float) -> tuple[float, float, str]:
    """(share of the fight on Low Runic Ward, share Missing Runic Ward, how). Nothing spends it: (0, 0) (it regenerates
    to full and stays). Spending at least what it regenerates: (1, 1) (held near empty). Otherwise, each spend of c from
    full is refilled at the regeneration R: below full min(1, spent / R) of the time (as models.not_full_mana_share);
    at 35% or less, each use's (c - 65% of the pool) / R seconds, x its uses a second (spends taken one at a time from
    full, a lower bound where they overlap)."""
    income = number(sustain.get("WardRecoveryPerSecond"))
    uses = [(paid, cost) for name, value in sustain.items() if not name.startswith("_") and name != "WardRecoveryPerSecond"
            and isinstance(value, (tuple, list)) and len(value) == 3 for _, paid, cost in [value] if paid > 0 and cost > 0]
    spent = sum(paid * cost for paid, cost in uses)
    if spent <= 0:
        return 0.0, 0.0, "nothing spends its Runic Ward: on full Ward all fight"
    if pool <= 0 or income <= spent * (1 + 1e-9) and number(sustain.get("_share", 1.0)) < 1.0 or income <= 0:
        return 1.0, 1.0, (f"spends {spent:,.1f} Ward/s against {income:,.1f} recovered: held near empty (Low and Missing "
                          "all fight)")
    missing = min(1.0, spent / income)
    low = min(1.0, sum(paid * max(cost - (1 - LOW_WARD) * pool, 0.0) / income for paid, cost in uses))
    return low, missing, (f"spends {spent:,.1f} Ward/s against {income:,.1f} recovered, {pool:,.0f} maximum: below full "
                          f"{missing:.0%} of the fight, at 35% or less {low:.0%} (each spend from full)")


def ward_shares(build: Build) -> tuple[float, float, str]:
    """ward_low_shares for the committed build (its ward_sustain from the last Assumptions.apply and PoB's Ward)."""
    return ward_low_shares(getattr(build, "ward_sustain", None) or {}, number(build.call("stats", fields=["Ward"]).get("Ward")))


# Temporal Rift and Phased Form (MECHC27; COV31's H1-1 note, a defence item for MECH-C): 0.5.0 "The Temporal Rift Skill
# granted by the Footprints in the Sand Notable Passive Skill ... now also removes delayed damage from Phased Form" (the
# newest entry, grep_notes --history "Temporal Rift"); its text (PoB other.lua 18441): "Removes any upcoming damage delayed
# by Phased Form on use". Phased Form delays 30% of each Hit for 4 s (COV31's stand-in "43% more Damage taken from Hits"
# puts it back in full). A use clears what the past 4 s of Hits had delayed, so used u times a second it clears
# min(1, 4 s x u) of the delayed damage (Poisson-free: uses on its cooldown are periodic). Uses: its group's rate (on its
# cooldown, ~5 s at level 20, else the search's chosen press rate, else 0: a button the player presses; the teleport and
# the reset of Life / Mana / ES to the afterimage's values are not modelled). phased_form_kept is the share of the delayed
# damage that still lands, for COV-A's stand-in (a "share:" form at this share: COV-A's to wire).
TEMPORAL_RIFT, PHASED_DELAY = "Temporal Rift", 4.0


# Consecrated Ground (MECHC30; PATCH_CHECK MD-3; plans/kw-consecrated-ground.md). KeywordPopups ConsecratedGround: "You and
# Allies standing on your Consecrated Ground Regenerate 5% of their maximum Life per second, and Curses have 50% reduced
# effect on them" (PoB CalcPerform 2010-2014 the same, scaled by ConsecratedGroundEffect; only its config box or the
# stationary line sets it). Its sources in the game, current (checked: the 78 Stats ids holding "consecrat" and their Mods,
# PassiveSkills and stat-set users, build 26476662; PoB's uniques.json; the plan's poe2db check; the five unique mods on no
# current item, ConsecrateOnCrit / OnBlock / OnShatter / UtilityFlaskConsecrate / the flask area, are PoE1 leftovers):
# - the Consecrate skill (StaffConsecratePlayer, a staff's): base_skill_effect_duration 8000;
# - Holy Descent (support): support_holy_descent_consecrated_ground_base_duration_ms 4000 "on landing";
# - Sanguis Heroum (charm): charm_create_consecrated_ground_when_used_duration_ms 4000 ("Creates Consecrated Ground on use");
# - Gifts from Above / Loreweave, "You have Consecrated Ground around you while stationary": PoB applies it itself under
#   Stationary (ModCache, as the plan found), so it is left to PoB when the build stands still (never counted twice);
# - Legacy of Sulphur (Mage's Legacy, plans/kw-mage-s-legacy.md): not read here (UNVERIFIED that it is league-obtainable).
# Before, nothing set the ground for the first three: an UNDERCOUNT of Life recovery (no DPS effect: no player source
# makes your ground hurt enemies, the plan's check). Uptime from the build's own uses (no flat rate): each source's uses a
# second x its duration (the group's use rate: on its cooldown, else the search's press choice, else 0; the charm's
# flasks.uses_per_second), sources overlapping as independent (MEDIUM), then x0.75 for standing in it: the ground is laid
# around you, and by the user's Leylines reasoning (15 Nov via the manager 01:15: "assume you're trying to stand in them if
# you take the ability, so 75%"; the manager applied it to Sigil of Power, 16:11), LOW. On PoB as "Regenerate 1% of maximum
# Life per second" x 5 x effect x share and "1% reduced Effect of Curses on you" x 50 x effect x share, effect = 1 + the
# build's "N% increased Effect of Consecrated Ground" lines. Duration modifiers aren't read (an undercount, LOW).
CONSECRATE_SKILL, HOLY_DESCENT = "Consecrate", "Holy Descent"
CONSECRATED_SECONDS = {CONSECRATE_SKILL: 8.0, HOLY_DESCENT: 4.0, "charm": 4.0}
CONSECRATED_STAND = 0.75
CONSECRATED_REGEN, CONSECRATED_CURSE = 5.0, 50.0
CONSECRATED_CHARM = re.compile(r"^creates consecrated ground on use$", re.I)
CONSECRATED_STATIONARY = re.compile(r"^you have consecrated ground around you while stationary$", re.I)
CONSECRATED_EFFECT = re.compile(r"^(\d+(?:\.\d+)?)% increased effect of consecrated ground(?: you create)?$", re.I)


def group_uses(build: Build, gem: str) -> tuple[float, str] | None:
    """(uses a second, how) of the enabled group holding `gem` (an active skill or a support), None without one: on its
    cooldown, else the search's press choice, else 0 (temporal_rift_uses' way)."""
    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    index = next((g["index"] for g in groups for n in g.get("gems") or []
                  if n["name"] == gem and n.get("enabled", True)), None)
    if index is None:
        return None
    rate = next((r for r in build.call("useRates", indices=[index])["rates"]), {})
    uses, how, _ = models.press_use_rate(rate, models.press_choice(rate, models.press_choices(build)))
    return uses, how


def temporal_rift_uses(build: Build) -> tuple[float, str] | None:
    """(Temporal Rift's uses a second, how), None without it: its group's rate (on its cooldown, else the search's chosen
    press rate, else 0). Phased Form's clearing (phased_form_kept) and the pools' reset (recovery.rift_income, MECHC29)."""
    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    index = next((g["index"] for g in groups for n in g.get("gems") or []
                  if n["name"] == TEMPORAL_RIFT and n.get("enabled", True)), None)
    if index is None:
        return None
    choices = models.press_choices(build)
    rate = next((r for r in build.call("useRates", indices=[index])["rates"]), {})
    uses, how, _ = models.press_use_rate(rate, models.press_choice(rate, choices))
    return uses, how


def phased_form_kept(build: Build) -> tuple[float, str]:
    """(share of Phased Form's delayed damage that still lands, how): 1 without Temporal Rift."""
    found = temporal_rift_uses(build)
    if found is None:
        return 1.0, "no Temporal Rift: all of the delayed damage lands"
    uses, how = found
    cleared = min(1.0, PHASED_DELAY * uses)
    return 1 - cleared, (f"Temporal Rift {how}: each use removes the past {PHASED_DELAY:g} s of Phased Form's delayed "
                         f"damage, {cleared:.0%} of it (MEDIUM)")


def resisted_leech(build: Build) -> dict[str, float]:
    """{pool: leech a second PoB counts that the boss resists} (recovery.leech_resisted at enemy_level; MECHC26, H2-1),
    for the readers that sum PoB's {pool}LeechRate themselves."""
    return recovery_mod.leech_resisted(build.call("stats", fields=list(recovery_mod.LEECH_FIELDS)),
                                       recovery_mod.leech_resistance(enemy_level(build)))


def pressed_group(group: dict, gems: dict[str, dict], granted: dict[str, dict] | None = None) -> bool:
    """A non-main group in Full DPS whose damage comes from a skill the player presses now and then: not triggered by a
    meta skill (Cast on X), not a totem or an Invocation (they use their skills on their own), not a Mark carrier
    (Pounce: the Mark model rates it), not granted by an item. (MECH-B, MECHB32) Except Untether: The Master's Reach
    grants it, it is a cast Spell (ActiveSkills abyssal_living_bomb: Spell, no Persistent / Meta / Triggered type), so
    a build counting it in Full DPS presses it (THE USER, 14 Nov: "Count it if used"), and its seeds' cap
    (Assumptions.untether_cap) must hold PoB's explosion-every-cast count; it isn't in Context's gem list yet
    (COV34 adds it at bridge load only), so it is named here.
    (SEARCH25, AUDIT36 A36-14's safer form) With `granted` (Context.all_gems: Context.gems leaves granted skills out), an
    item- or tree-granted group in Full DPS is judged by its skill like any other, so press_model rates it instead of
    PoB counting it used nonstop (an OVERCOUNT on a loaded build's ticked granted group); PoB's own Thorns group never
    counts (the Thorns model rates it). The search puts a granted group in Full DPS only on the finished build
    (Search.include_granted, finalize_task)."""
    if (group["enabled"] and group["fullDPS"]
            and any(g["enabled"] and not g["support"] and g["name"] == models.UNTETHER for g in group["gems"])):
        return True
    if not group["enabled"] or not group["fullDPS"] or group.get("label") == models.THORNS_GROUP:
        return False
    if group["fromItem"]:
        if not granted:
            return False
        gems = {**granted, **gems}
    active = [{**gems[g["name"]], "name": g["name"]} for g in group["gems"]
              if g["enabled"] and not g["support"] and g["name"] in gems]
    if any(g["name"] in models.MARK_CARRIERS for g in active):
        return False
    for gem in active:
        types = set(gem["skillTypes"])
        if (types & {PRESSED_META, PRESSED_TOTEM}
                or ("Meta" in types and not ({"Attack", "Warcry"} & types and "Persistent" not in types))):
            return False
    return any(presses(g) for g in active)


def minion_lives(build: Build, found: list[models.Skill]) -> dict[str, float]:
    """{Reviving minion skill: its minion's maximum Life} when a skill in the build has Minion Pact (models.pact_uses_per_kill;
    PoB calculates a minion's Life only as the main skill, so the bridge's useRates sets each minion group as main)."""
    if not any(models.number(s.support_stats.get(models.MINION_PACT_STAT)) > 0 for s in found):
        return {}
    wanted = {s.name for s in found if s.summon and s.summon.get("persistent") and not models.number(s.summon.get("duration"))}
    indices, actives, names = [], [], []
    for group in build.groups():
        for n, skill in enumerate(group["skills"], 1):
            if skill["name"] in wanted and skill["name"] not in names:
                indices.append(group["index"]); actives.append(n); names.append(skill["name"])
    if not indices:
        return {}
    rates = {(r["index"], r.get("active")): r for r in build.call("useRates", indices=indices, actives=actives)["rates"]}
    lives = {name: models.number((rates.get((i, a)) or {}).get("minionLife")) for i, a, name in zip(indices, actives, names)}
    return {name: life for name, life in lives.items() if life > 0}



HUNT_FIELDS = ["CombinedDPS", "ShowAverage", "CullMultiplier", "ReservationDpsMultiplier", "TotalDPS", "AverageDamage",
               "TotalDotDPS"]


def ancestral_spirits(build: Build, main: models.Skill | None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Warbringer's Ancestral Spirits (models.ancestral_spirits_model, MECHB29): the bridge's
    minionTypes rates each of the four spirit types."""
    for group in build.groups():
        for n, skill in enumerate(group["skills"], 1):
            if skill["name"] != models.ANCESTRAL_SPIRITS or skill.get("disabled"):
                continue
            found = build.call("minionTypes", index=group["index"], active=n)
            types = found.get("types") if isinstance(found, dict) else None  # (an empty Lua table reaches Python as a list)
            texts = [line["text"] for line in build.call("sourceLines")["lines"]]  # (Ancestral Bond's doubling, MECHB34)
            return models.ancestral_spirits_model(list(types or []), main, texts)
    return 0.0, []


def eternal_hunt(build: Build, main: models.Skill | None) -> tuple[float, list[dict], list[dict]]:
    """(added DPS, mods, notes) for an Azmerian Wolf's Eternal Hunt (models.eternal_hunt_model, MECHB20): the bridge's
    minionSkillHit scores the wolf with the hunt chosen. The Command's press costs the main skill's time (the player's
    own DPS, 0 when the wolf is the main skill); the wolf's howl costs its own DPS where the score counts it (it's the
    main skill, or its group is in Full DPS while the objective scores Full DPS)."""
    for group in build.groups():
        for n, skill in enumerate(group["skills"], 1):
            if skill["name"] != models.AZMERIAN_WOLF or skill.get("disabled"):
                continue
            hunt = build.call("minionSkillHit", index=group["index"], active=n, skill=models.ETERNAL_HUNT)
            hunt = hunt if isinstance(hunt, dict) else {}  # (an empty Lua table reaches Python as a list)
            wolf_main = main is not None and main.name == models.AZMERIAN_WOLF
            full = bool(getattr(getattr(build, "objective", None), "full", False))
            counted = wolf_main or (full and group.get("fullDPS"))
            player = 0.0 if wolf_main else per_second(build.call("stats", fields=HUNT_FIELDS))
            return models.eternal_hunt_model(hunt, player, number(hunt.get("baseDPS")) if counted else 0.0)
    return 0.0, [], []


def djinn_commands(build: Build, main: models.Skill | None) -> tuple[float, list[dict], list[dict]]:
    """(added DPS, mods, notes) for the Disciple of Varashta's djinn Commands (models.djinn_commands_model, MECH83): the
    djinns' groups (the Barya notables' granted summons), the Commands the tree grants, each scored by the bridge's
    minionSkillHit with that minion skill and its damage stat set; the presses go on build.item_presses."""
    where = {}
    for group in build.groups():
        if group.get("enabled") is False:
            continue
        for n, skill in enumerate(group["skills"], 1):
            if skill["name"] in models.DJINNS.values() and not skill.get("disabled"):
                where.setdefault(skill["name"], (group["index"], n))
    if not where:
        return 0.0, [], []
    texts = [line["text"] for line in build.call("sourceLines")["lines"]]
    rows = []
    for command in models.djinn_commands_available(set(where), texts):
        index, n = where[command.djinn]
        hits = {}
        for stat_set in command.sets:
            got = build.call("minionSkillHit", index=index, active=n, skill=command.skill, statSet=stat_set)
            hits[stat_set] = got if isinstance(got, dict) else {}  # (an empty Lua table reaches Python as a list)
        rows.append((command, hits))
    minion_main = main is not None and bool(set(build.ctx.gems.get(main.name, {}).get("skillTypes") or ()) & {"Minion"})
    player = 0.0 if minion_main else per_second(build.call("stats", fields=HUNT_FIELDS))
    chilled = (getattr(build, "chilled_ground_up", None) or (0.0, ""))[0]
    from builder.search import bosses  # (as Assumptions' boss readers import it)

    boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
    radius = bosses.hit_radius({"_boss": boss})  # (MECH84: Brutality's blades on the boss, RESEARCH29 R29-3)
    dps, mods, notes, presses = models.djinn_commands_model(rows, player, chilled, radius)
    held = getattr(build, models.ITEM_PRESSES, None)
    if isinstance(held, dict):  # (each Command a press: the button cap counts it, models.ITEM_PRESSES)
        held.update(presses)
    return dps, mods, notes


def inevitable_agony(build: Build, main: models.Skill | None) -> tuple[float, float, list[dict], list[dict]]:
    """(added DPS, presses a second, mods, notes) for Chronomancer's Inevitable Agony (models.inevitable_agony_model,
    MECHB30): the main skill's hit DPS (PoB's TotalDPS, the minion's for a minion main skill), the debuff's duration with
    the build's modifiers and its cast from its useRates row."""
    for group in build.groups():
        if not group["enabled"]:
            continue
        for n, skill in enumerate(group["skills"], 1):
            if skill["name"] != models.INEVITABLE_AGONY or skill.get("disabled"):
                continue
            row = next(iter(build.call("useRates", indices=[group["index"]], actives=[n])["rates"]), None) or {}
            minion = main is not None and bool(main.summon)
            stats = build.call("stats", fields=HUNT_FIELDS + ["Minion.TotalDPS"])
            hit = number(stats.get("Minion.TotalDPS" if minion else "TotalDPS"))
            player = 0.0 if minion else per_second(stats)
            return models.inevitable_agony_model(hit, player, number(row.get("duration")) or models.AGONY_SECONDS,
                                                 number(row.get("castTime")) or models.AGONY_CAST)
    return 0.0, 0.0, [], []


def barrage(build: Build, main: models.Skill | None, found: list[models.Skill]) -> tuple[list[dict], list[dict]]:
    """(mods, notes) for Barrage beside a Barrageable main attack (models.barrage_model, MECHB30): PoB's DPS MORE taken
    back to the model's factor (a DPS MORE on the main skill), from Barrage's level stats and the main skill's Speed."""
    if main is None or not main.attack:
        return [], []
    types = set((build.ctx.gems.get(main.name) or {}).get("skillTypes") or [])
    barrage = next((s for s in found if s.name == models.BARRAGE), None)
    if barrage is None or "Barrageable" not in types:
        return [], []
    raw = build.call("activeSkills", levelStats=[models.BARRAGE], supportStats=[models.OLROTH_EMPOWERS])["skills"]
    level = next((s.get("levelStats") or {} for s in raw if s["name"] == models.BARRAGE and s.get("levelStats")), {})
    extra = max((number((s.get("supportStats") or {}).get(models.OLROTH_EMPOWERS)) for s in raw
                 if s["name"] == models.BARRAGE), default=0.0)  # (MECHB32: Olroth's Conviction on Barrage)
    stats = build.call("stats", fields=["Speed", "FrenzyCharges"])
    repeats = (number(level.get("empower_barrage_base_number_of_barrage_repeats"))
               + number(level.get("empower_barrage_number_of_barrage_repeats_per_frenzy_charge"))
               * number(stats.get("FrenzyCharges")))
    factor, pob, how = models.barrage_model(number(stats.get("Speed")), repeats,
                                            number(level.get("empower_barrage_damage_-%_final_with_repeated_projectiles")),
                                            number(level.get("empower_barrage_cooldown_%_of_attack_time")),
                                            barrage.cooldown_recovery, barrage.cast_time or models.BARRAGE_CAST, extra)
    # PoB's own factor, measured: the main skill's TotalDPS with Barrage's group on over off (probe POE2T1174: x1.020,
    # where its level stats rebuild x1.016: PoB's repeats or repeat damage differ a little from the stats read here)
    if barrage.group is not None:
        off = number(build.call("setGroup", index=barrage.group, enabled=False, fields=["TotalDPS"])["stats"].get("TotalDPS"))
        on = number(build.call("setGroup", index=barrage.group, enabled=True, fields=["TotalDPS"])["stats"].get("TotalDPS"))
        if off > 0 and on > 0:
            pob = on / off
            how += f" (PoB's own x{pob:.3f} measured: the main skill with Barrage's group on over off)"
    note = {"label": ("Barrage (one empowered attack per cooldown)" if extra <= 0 else
                      f"Barrage ({1 + extra:g} empowered attacks per cooldown, Olroth's Conviction)"),
            "value": round(factor, 3), "basis": how,
            "confidence": "MEDIUM", "play": (f"Press Barrage each time it's up: x{factor:.3f} of {main.name}'s damage"
                                             if factor > 1 else "Barrage costs more than it gives here: not pressed")}
    return [models.raw_mod("DPS", "MORE", (factor / pob - 1) * 100, skill=main.name)], [note]


def empower_skill(build: Build, name: str) -> dict | None:
    """(MECH-B, MECHB32) The activeSkills row (with its level stats and Olroth's Conviction's extra in supportStats) of
    the skill `name` in an enabled group beside the main skill, or None (not the Unleash support gem: the name is
    shared, engine/bridge.lua)."""
    main = build.main_group() or {}
    group = next((g for g in build.groups() if g.get("enabled") is not False and g["index"] != main.get("index")
                  and any(x["name"] == name and x.get("enabled", True) and not x.get("support") for x in g["gems"])),
                 None)
    if group is None:
        return None
    raw = build.call("activeSkills", levelStats=[name], supportStats=[models.OLROTH_EMPOWERS])["skills"]
    return next((s for s in raw if s["name"] == name and s.get("group") == group["index"] and not s.get("disabled")),
                None)


def empowered_presses(build: Build, main: models.Skill | None) -> tuple[list[dict], list[dict]]:
    """(MECH-B, MECHB32) Mantra of Destruction's Chaos gain on the main Quarterstaff Strike and Unleash's Repeats on
    the main Spell, each pressed when ready in its own cycle (models.mantra_cycle, models.unleash_factor; PoB counts 0
    for both)."""
    if main is None:
        return [], []
    mods, notes = [], []
    gem = build.ctx.gems.get(main.name) or {}
    stats = build.call("stats", fields=["Speed", "HitChance"])
    speed = number(stats.get("Speed"))
    raw_main = next((s for s in build.call("activeSkills")["skills"]
                     if s["name"] == main.name and s.get("group") == main.group), {})
    mantra = empower_skill(build, models.MANTRA_OF_DESTRUCTION) if main.attack and "Staff" in (
        gem.get("weaponTypes") or []) else None
    if mantra is not None:
        level = mantra.get("levelStats") or {}
        gain = number(level.get(models.MANTRA_GAIN))
        empowers = (number(level.get(models.MANTRA_EMPOWERS)) or 1.0) + number(
            (mantra.get("supportStats") or {}).get(models.OLROTH_EMPOWERS))
        if main.melee and not raw_main.get("slam"):
            from builder.search.usage import MARTIAL_ADEPT, Usage
            usage = Usage(build, None)
            per_hit = 1.0 + (1.0 if usage.has_passive(MARTIAL_ADEPT) else 0.0) + min(
                usage.combo_chance() + usage.group_combo_chance(main.group), 100.0) / 100
            time, share, how = models.mantra_cycle(speed, min(number(stats.get("HitChance")) or 100.0, 100.0) / 100,
                                                   number(level.get(models.MANTRA_COMBO)) or 8.0, per_hit, empowers,
                                                   number(mantra.get("castTime")) or 1.0)
        else:  # (MECHB35: the main attack builds no Combo; another of the build's Strikes feeds it, if that trade pays)
            from builder.search.usage import MARTIAL_ADEPT, Usage
            usage = Usage(build, None)
            builders = [b for b in usage.combo_builders(models.MANTRA_OF_DESTRUCTION)
                        if b[0] is not None and b[0] != main.group]
            rates = build.call("useRates", indices=sorted({i for i, _ in builders}))["rates"] if builders else []
            best = max(rates, key=lambda r: number(r.get("speed")), default=None)
            per_hit = 1.0 + (1.0 if usage.has_passive(MARTIAL_ADEPT) else 0.0) + min(
                usage.combo_chance() + (usage.group_combo_chance(best["index"]) if best else 0.0), 100.0) / 100
            time, share, how = models.mantra_fed_cycle(
                speed, gain, empowers, number(level.get(models.MANTRA_COMBO)) or 8.0, per_hit,
                min(number(stats.get("HitChance")) or 100.0, 100.0) / 100,  # (the builder's hit chance taken as the main's)
                number(best.get("speed")) if best else 0.0, best["name"] if best else "",
                number(mantra.get("castTime")) or 1.0)
            how = "the main attack isn't a Strike, so it builds no Combo; " + how
        if share > 0 and gain > 0:
            mods.append(models.line("Gain 1% of Damage as Extra Chaos Damage", gain * share, skill=main.name))
            mods.append(models.raw_mod("DPS", "MORE", (time - 1) * 100, skill=main.name))
        notes.append({"label": "Mantra of Destruction: Empowered attacks gain Chaos*",
                      "value": f"{gain * share:.1f}% of Damage as extra Chaos on average, x{time:.3f} time",
                      "basis": f"{how}; each Empowered attack gains {gain:g}% of Damage as extra Chaos (PoB counts 0)",
                      "confidence": "MEDIUM",
                      "play": (f"Press Mantra of Destruction at full Combo, then attack: without it +0%; with it "
                               f"+{gain * share:.1f}% of Damage as Chaos on average, {1 - time:.1%} of the time spent "
                               "pressing it" if share > 0 else "Mantra of Destruction isn't fed here")})
    unleash = empower_skill(build, models.UNLEASH) if main.spell and not main.cooldown else None
    if unleash is not None and raw_main.get("repeatable"):
        level = unleash.get("levelStats") or {}
        factor, how = models.unleash_factor(
            speed, number(level.get(models.UNLEASH_REPEATS)) or 2.0, number(level.get(models.UNLEASH_COOLDOWN_PCT)),
            number(unleash.get("cooldownRecovery")) or 1.0,
            1.0 + number((unleash.get("supportStats") or {}).get(models.OLROTH_EMPOWERS)),
            number(unleash.get("castTime")) or models.UNLEASH_CAST)
        if factor > 1.0:
            mods.append(models.raw_mod("DPS", "MORE", (factor - 1) * 100, skill=main.name))
        notes.append({"label": "Unleash: Empowered Spell Repeats*", "value": round(factor, 3), "basis": how,
                      "confidence": "MEDIUM",
                      "play": (f"Press Unleash each time it's ready, then cast {main.name}: without it x1.000; with "
                               f"it x{factor:.3f}" if factor > 1 else "Unleash costs more than it gives here: not pressed")})
    return mods, notes


def ice_tipped(build: Build, main: models.Skill | None) -> tuple[float, list[dict], list[dict]]:
    """(MECH-B, MECHB32) (the Ice Fragments' added DPS, mods, notes) for Ice-Tipped Arrows beside a Repeatable Bow or
    Projectile Spear main attack (models.ice_tipped_cycle; PoB counts 0): its time share, its Physical-to-Cold
    conversion on the Empowered share, and one Ice Fragment hit per Empowered hit (PoB's own fragment hit, read with
    useRates on the gem's fragment skill)."""
    if main is None or not main.attack:
        return 0.0, [], []
    gem = build.ctx.gems.get(main.name) or {}
    types, weapons = set(gem.get("skillTypes") or []), set(gem.get("weaponTypes") or [])
    if not ("Bow" in weapons or ("Spear" in weapons and "Projectile" in types)) or types & {"Channel", "Sustained"}:
        return 0.0, [], []
    ita = empower_skill(build, models.ICE_TIPPED)
    if ita is None:
        return 0.0, [], []
    level = ita.get("levelStats") or {}
    empowers = (number(level.get(models.ICE_TIPPED_EMPOWERS)) or 4.0) + number(
        (ita.get("supportStats") or {}).get(models.OLROTH_EMPOWERS))
    cooldown = (number(ita.get("cooldown")) or models.ICE_TIPPED_COOLDOWN) / (number(ita.get("cooldownRecovery")) or 1.0)
    stats = build.call("stats", fields=list({"Speed", "HitChance", *LIVING_BOMB_FIELDS}))
    speed, hit = number(stats.get("Speed")), min(number(stats.get("HitChance")) or 100.0, 100.0) / 100
    time, share, empowered, how = models.ice_tipped_cycle(speed, empowers, cooldown,
                                                           number(ita.get("castTime")) or models.ICE_TIPPED_CAST)
    if share <= 0:
        return 0.0, [], [{"label": "Ice-Tipped Arrows: Empowered attacks*", "value": "not counted", "basis": how,
                          "confidence": "MEDIUM"}]
    convert = number(level.get(models.ICE_TIPPED_CONVERT)) or 100.0
    group = next((g for g in build.groups() if g["index"] == ita.get("group")), None)
    active = next((i for i, x in enumerate((group or {}).get("skills") or [], start=1)
                   if x["name"] == models.ICE_TIPPED_FRAGMENT), 0)
    rows = build.call("useRates", indices=[group["index"]], actives=[active])["rates"] if active else []
    # (a fragment use's damage, its own hit chance in: PoB's TotalDPS over its speed; the row has no AverageHit for
    # this Triggered attack, probe POE2T3218)
    fragment = (number(rows[0].get("TotalDPS")) / number(rows[0].get("speed"))
                if rows and number(rows[0].get("speed")) > 0 else 0.0)
    added = empowered * hit * fragment
    lost = (1 - time) * per_second(stats)
    if added < lost:  # (its cast costs more of the main attack than the fragments give: not pressed, as barrage_model)
        return 0.0, [], [{"label": "Ice-Tipped Arrows: Empowered attacks*", "value": "not pressed",
                          "basis": (f"{how}; its Ice Fragments ({added:,.1f} DPS) give less than its cast takes from the "
                                    f"main attack ({lost:,.1f} DPS; the Cold conversion's change not weighed: MEDIUM)"),
                          "confidence": "MEDIUM", "play": "Ice-Tipped Arrows costs more than it gives here: not pressed"}]
    mods = [models.raw_mod("DPS", "MORE", (time - 1) * 100, skill=main.name),
            models.line("1% of Physical Damage Converted to Cold Damage", convert * share, skill=main.name)]
    note = {"label": "Ice-Tipped Arrows: Empowered attacks convert to Cold and Trigger Ice Fragments*",
            "value": f"{convert * share:.0f}% of Physical converted on average, Ice Fragments {added:,.1f} DPS, "
                     f"x{time:.3f} time",
            "basis": (f"{how}; each Empowered attack converts {convert:g}% of its Physical to Cold (\"non-skill base\" "
                      f"taken as all of it, MEDIUM) and its hit ({hit:.0%}) Triggers Ice Fragments: one fragment hit on "
                      f"the boss ({fragment:,.1f} a use, its hit chance in: PoB's fragment skill; KeywordPopups: only one of a burst damages an "
                      "enemy). PoB counts none of it. Its Frenzy-Charge cooldown bypass isn't counted (an undercount)"),
            "confidence": "MEDIUM",
            "play": (f"Press Ice-Tipped Arrows each time it's ready, then attack: without it x1.000 and no fragments; "
                     f"with it {convert * share:.0f}% of Physical as Cold on average, +{added:,.1f} DPS of Ice Fragments")}
    if group is not None and group.get("fullDPS"):
        # (PoB's Full DPS would count the gem's fragment skill on its own at its at-will speed, 1.2/s in POE2T3218:
        # its entry goes to 0, the fragments counted above instead)
        note["full_dps"] = {models.ICE_TIPPED_FRAGMENT: 0.0}
    return added, mods, [note]


def infernal_legion(build: Build, stats: dict) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Infernal Legion I / II (models.infernal_legion_model, MECHB30): each supported minion
    skill's minion Life (PoB's useRates minionLife, as minion_lives reads it), the boss's Fire resistance (the config's
    enemyFireResist) and the build's own Ignite (PoB's IgniteDPS)."""
    indices, actives, names = [], [], []
    for group in build.groups():
        if not group["enabled"] or not any(g["name"] in models.INFERNAL_LEGION and g.get("enabled", True)
                                           for g in group["gems"]):
            continue
        for n, skill in enumerate(group["skills"], 1):
            if not skill.get("disabled"):
                indices.append(group["index"]); actives.append(n); names.append(skill["name"])
    if not indices:
        return 0.0, []
    rates = {(r["index"], r.get("active")): r for r in build.call("useRates", indices=indices, actives=actives)["rates"]}
    minions = [(name, number((rates.get((i, a)) or {}).get("minionLife"))) for i, a, name in zip(indices, actives, names)]
    resist = number(build.call("getConfig")["input"].get("enemyFireResist"))
    added, note = models.infernal_legion_model(minions, resist, number(stats.get("IgniteDPS")))
    return added, [note]

# What each Warcry Empowers, by its own game text (MECH65, AUDIT29 A29-4; ActiveSkills 26476662 descriptions):
# KeywordPopups Warcry says Warcries "Empower your subsequent Melee Attacks", but the skills' own texts differ: Arctic
# Howl "Empowers your attacks" (and Slams), but only "If an enemy is Frozen or a Frozen enemy is Hit"; Seismic Cry
# "Empowers subsequent Slams", only "If an enemy is Heavy Stunned or a currently Heavy Stunned enemy is Hit"; Fortifying
# Cry's subsequent "Shield Attacks"; Infernal Cry "subsequent Melee Attacks". Ferocious Roar ("subsequent Attacks")
# can't take Murderous Intent (no EmpowersOtherSkill type). Others follow the keyword: Melee Attacks.
# (what, needs on the main skill, the boss condition the Empower waits for)
WARCRY_EMPOWERS = {
    "Infernal Cry": ("Melee Attacks", ("melee", "attack"), None),
    "Seismic Cry": ("Slams", ("slam",), "Heavy Stunned"),
    "Arctic Howl": ("attacks", ("attack",), "Frozen"),
    "Ferocious Roar": ("Attacks", ("attack",), None),
    "Fortifying Cry": ("Shield Attacks", ("attack", "shield"), None),
}


def warcry_empowers(name: str, mains: list[dict], stats: dict) -> tuple[str, bool, float, str]:
    """(what the Warcry Empowers, whether the main skill is one, the share of its uses that Empower, why) for
    Assumptions.empowered_uses. A Warcry whose Empower waits on the boss being Heavy Stunned or Frozen counts at the
    share of the fight the boss is (models.heavy_stunned_share / immobilised_share): a lower reading, since a use can
    also Empower once the boss gets there later in its window (LOW)."""
    what, needs, cond = WARCRY_EMPOWERS.get(name, ("Melee Attacks", ("melee", "attack"), None))
    fits = any(all(s.get(k) for k in needs) for s in mains)
    if not fits or cond is None:
        return what, fits, 1.0, ""
    if cond == "Heavy Stunned":
        share, how = models.heavy_stunned_share(stats)
    else:
        share, how = models.immobilised_share(stats, "Freeze")[:2]
    return what, fits, share, (f"it Empowers only while the boss is {cond}: {how}; LOW, a lower reading (a use can "
                               "Empower when the boss gets there later)")


def riven_armour(build: Build, main: models.Skill | None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for High Velocity Rounds' Riven Armour (GEM3's models.riven_armour_model; MECH69 wires it):
    HVR's pre-mitigation Physical hit (PoB's PhysicalStoredCombinedAvg: the main skill's stats if HVR is the main skill,
    else its group's useRates row), the share of the fight Riven Armour is up (conditions' Armour Break cycle,
    build.riven_up), and the boss's x on Physical hit damage, read off the main skill's own hit (its crit-weighted
    Physical hit after mitigation / before; 1.0 when the main skill has no Physical hit to read it from: labelled, an
    overcount risk against an armoured boss)."""
    riven_up, how = getattr(build, "riven_up", (0.0, "")) or (0.0, "")
    hvr_hit = 0.0
    if main is not None and main.name == models.RIVEN_SKILL:
        hvr_hit = models.number(build.call("stats", fields=["PhysicalStoredCombinedAvg"]).get("PhysicalStoredCombinedAvg"))
    else:
        for each in build.groups():
            if not each["enabled"]:
                continue
            for position, skill in enumerate(each["skills"], start=1):
                if skill["name"] == models.RIVEN_SKILL:
                    row = next(iter(build.call("useRates", indices=[each["index"]], actives=[position])["rates"]), None) or {}
                    hvr_hit = max(hvr_hit, models.number(row.get("PhysicalStoredCombinedAvg")))
    if hvr_hit <= 0 and riven_up <= 0:
        return 0.0, []
    fields = ["PhysicalHitAverage", "PhysicalCritAverage", "CritChance", "PhysicalStoredCombinedAvg", "Speed", "HitChance"]
    main_stats = build.call("stats", fields=fields)
    crit = min(models.number(main_stats.get("CritChance")), 100) / 100
    after = (models.number(main_stats.get("PhysicalHitAverage")) * (1 - crit)
             + models.number(main_stats.get("PhysicalCritAverage")) * crit)
    before = models.number(main_stats.get("PhysicalStoredCombinedAvg"))
    if after > 0 and before > 0:
        phys_taken, taken_how = after / before, f"the boss takes x{after / before:.2f} of the main skill's Physical hit"
    else:
        phys_taken, taken_how = 1.0, "the main skill has no Physical hit to read the boss's mitigation from: x1.00 (labelled)"
    return models.riven_armour_model(hvr_hit, riven_up, main, main_stats, phys_taken,
                                     how="; ".join(part for part in (how, taken_how) if part))



def _skill_hits(build: Build, group: dict, position: int, press: dict) -> tuple[float, dict]:
    """(hits a second on the boss, its useRates row) for the active skill at `position` of `group`: PoB's use rate x
    hit chance x the press model's share for its Full DPS entry (objective.press_scales; 1 when it has none, as
    thorns' side skills: Full DPS counts it at PoB's own rate then)."""
    row = next(iter(build.call("useRates", indices=[group["index"]], actives=[position])["rates"]), None) or {}
    chance = min(models.number(row.get("HitChance", 100) or 100), 100.0) / 100
    return models.number(row.get("speed")) * chance * models.number(press.get(row.get("name"), 1.0)), row


def elemental_discharge(build: Build, main: models.Skill | None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Elemental Discharge (GEM5's models.elemental_discharge_model; GEM6 wires it): in each
    enabled group holding the triggered skill (PoB adds it beside a spell the support supports), the supported spell's
    hits a second (_skill_hits), the triggered skill's per-element hit with PoB's three Consumed boxes ticked for that
    call only (useRates `config`; they stay off in the build, so PoB's own DPS never counts it: no double count), its
    Cooldown, and the boss's uptimes after the consume cut (MECH69's build.ailment_uptimes, which already takes the
    support's own consume off). The spell's casts a second are its hits: a spell hitting the boss more than once a cast
    (Spark's projectiles) is read low (LOW-side, labelled in the model's MEDIUM)."""
    found = [(each, position) for each in build.groups() if each["enabled"]
             for position, skill in enumerate(each["skills"], start=1) if skill["name"] == models.DISCHARGE_SKILL]
    if not found:
        return 0.0, []
    up, up_how = models.ailment_up_of(build)
    press = getattr(build.objective, "press_scales", None) or {}
    boxes = {box: True for box, _ in models.DISCHARGE_BOXES.values()}
    total, notes = 0.0, []
    for each, position in found:
        spot = next((i for i, s in enumerate(each["skills"], start=1) if s["name"] != models.DISCHARGE_SKILL), None)
        if spot is None:
            continue
        spell_hits, _ = _skill_hits(build, each, spot, press)
        row = next(iter(build.call("useRates", indices=[each["index"]], actives=[position], config=boxes)["rates"]),
                   None) or {}
        dps, these = models.elemental_discharge_model(
            models.discharge_element_hits(row), spell_hits, up, cooldown=models.number(row.get("cooldown") or 1.0),
            spell=each["skills"][spot - 1]["name"], how=up_how)
        total, notes = total + dps, notes + these
    return total, notes


def broken_stance(build: Build, main: models.Skill | None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Vaulting Impact's Broken Stance (GEM5's models.broken_stance_model; GEM6 wires it): the
    best Vaulting Impact in an enabled group (its pre-mitigation Physical hit, PoB's PhysicalStoredCombinedAvg, and its
    hits a second, _skill_hits), the share of the fight the boss is Dazed by the build's other sources (MECH-A's
    build.dazed_up, a share or (share, how); 0 until it is published, so 0 with that reason), the main skill's hits a
    second (they gain the magnitude) and the boss's x on Physical hit damage read off the main skill's hit (as
    riven_armour; 1.0 labelled when it has no Physical hit)."""
    press = getattr(build.objective, "press_scales", None) or {}
    vault_hit = vault_hits = 0.0
    for each in build.groups():
        if not each["enabled"]:
            continue
        for position, skill in enumerate(each["skills"], start=1):
            if skill["name"] == models.STANCE_SKILL:
                hits, row = _skill_hits(build, each, position, press)
                if models.number(row.get("PhysicalStoredCombinedAvg")) > vault_hit:
                    vault_hit, vault_hits = models.number(row.get("PhysicalStoredCombinedAvg")), hits
    if vault_hit <= 0:
        return 0.0, []
    raw = getattr(build, "dazed_up", None)
    dazed = models.number(raw[0] if isinstance(raw, (tuple, list)) else raw) if raw is not None else 0.0
    how = ((raw[1] if isinstance(raw, (tuple, list)) and len(raw) > 1 else "") if raw is not None
           else "MECH-A's Daze share (build.dazed_up) is not published yet")
    fields = ["PhysicalHitAverage", "PhysicalCritAverage", "CritChance", "PhysicalStoredCombinedAvg", "Speed", "HitChance"]
    main_stats = build.call("stats", fields=fields)
    crit = min(models.number(main_stats.get("CritChance")), 100) / 100
    after = (models.number(main_stats.get("PhysicalHitAverage")) * (1 - crit)
             + models.number(main_stats.get("PhysicalCritAverage")) * crit)
    before = models.number(main_stats.get("PhysicalStoredCombinedAvg"))
    if after > 0 and before > 0:
        phys_taken, taken = after / before, f"the boss takes x{after / before:.2f} of the main skill's Physical hit"
    else:
        phys_taken, taken = 1.0, "the main skill has no Physical hit to read the boss's mitigation from: x1.00 (labelled)"
    hits = (models.number(main_stats.get("Speed")) * min(models.number(main_stats.get("HitChance", 100) or 100), 100) / 100
            * models.number(press.get(main.name, 1.0) if main is not None else 1.0))
    # (its hit limit at the gem's level and quality: GrantedEffectQualityStats gives +1 hit per 10% quality)
    raw_skills = build.call("activeSkills", levelStats=[models.STANCE_SKILL])["skills"]
    limit = max((models.number((s.get("levelStats") or {}).get("broken_stance_hit_limit")) for s in raw_skills
                 if s["name"] == models.STANCE_SKILL), default=0.0) or models.STANCE_HITS
    return models.broken_stance_model(vault_hit, vault_hits, dazed, hits, phys_taken, limit=int(limit),
                                      how="; ".join(part for part in (how, taken) if part))


# Stoicism I / II (MECH75; PATCH_CHECK's Mirror sweep MA-8 and its "while not" row). The game (gem_stat_descriptions,
# support_no_fear_damage_+%_final_per_second_up_to_30%: 1 for I, 2 for II): "Supported Skills gain {0}% more Damage every
# second, up to 20%. This Damage Bonus resets if you Dodge Roll or use a [Travel] Skill"; patch 0.5.0: it supports Attacks
# only. PoB scores it only with its "Seconds building Stoicism" box set (ConfigOptions.lua 504, unset: 0, an UNDERCOUNT),
# and then only while neither "Dodge Rolled Recently" nor "Used a Travel Skill Recently" is ticked (SkillStatMap.lua 872-877):
# set as it stands, it would count all fight whatever the rolls (an OVERCOUNT), and setting the rolled box would zero it
# (the game resets the ramp on a roll, it doesn't switch it off for 4 s). So PoB's box stays unset and the time-averaged
# bonus goes in as the main skill's Damage MORE: with a reset every T s the ramp averages min(r T / 2, 20 - 200 / (r T)).
# T: the build's dodge rolls, one per telegraphed boss hit (models.PARRY_EVERY, 5 s: the manager's ruling on PLANS2, 12 Nov
# ~09:00), or models.DODGE_EVERY (2 s, the user's default) for a build that relies on rolling (Cast on Dodge, a "when you
# dodge roll" line); 1 / its speed for a Travel main skill (0 then). Labelled MEDIUM: Travel skills pressed beside the main
# skill aren't read as resets (an OVERCOUNT for a build that presses one); not rolling at all (the full 20%) isn't offered
# as a play (the tool doesn't price the hits a roll avoids); Stoicism on a skill beside the main one isn't read.
STOICISM = ("Stoicism I", "Stoicism II")
STOICISM_STAT = "support_no_fear_damage_+%_final_per_second_up_to_30%"
STOICISM_CAP = 20.0
ROLL_RELIANT = re.compile(r"\b(?:when|on|after) you dodge roll|\bdodge rolled recently\b|\bon dodge roll\b", re.I)


def stoicism_average(per_second: float, every: float) -> float:
    """The average more Damage (%) of a ramp of per_second % a second up to STOICISM_CAP, reset every `every` s."""
    if per_second <= 0 or every <= 0:
        return 0.0
    ramp = per_second * every
    if ramp <= STOICISM_CAP:
        return ramp / 2
    return STOICISM_CAP - STOICISM_CAP ** 2 / (2 * ramp)


def stoicism(build, main, main_types: set[str], texts: list[str]) -> tuple[list[dict], list[dict]]:
    """(mods, notes) for Stoicism on the main skill (see the note above)."""
    group = build.main_group() if main is not None else None
    names = {g["name"] for g in (group or {}).get("gems") or [] if g.get("enabled", True)}
    if main is None or not names & set(STOICISM) or "Attack" not in main_types:
        return [], []
    raw = build.call("activeSkills", supportStats=[STOICISM_STAT])
    got = next((s for s in raw["skills"] if s["name"] == main.name and not s.get("disabled")), {})
    per_second = models.number((got.get("supportStats") or {}).get(STOICISM_STAT))
    every_gem = {g["name"] for grp in build.groups() if grp.get("enabled") is not False for g in grp["gems"]
                 if g.get("enabled", True)}
    if "Travel" in main_types:
        every, why = 1.0 / max(models.number(build.call("stats", fields=["Speed"]).get("Speed")), 1e-9), \
            "the main skill is a Travel skill: each use resets it"
    elif "Cast on Dodge" in every_gem or any(ROLL_RELIANT.search(t) for t in texts):
        every, why = models.DODGE_EVERY, f"a dodge roll every {models.DODGE_EVERY:g} s (the build relies on rolling)"
    else:
        every, why = models.PARRY_EVERY, (f"a dodge roll every {models.PARRY_EVERY:g} s, one per telegraphed boss hit "
                                          "(the manager's ruling, 12 Nov)")
    more = stoicism_average(per_second, every)
    note = {"label": "Stoicism: more Damage between resets*", "value": f"{more:.1f}% more",
            "basis": (f"{per_second:g}% more a second up to {STOICISM_CAP:g}%, reset by {why}: {more:.1f}% on average "
                      "(PoB's own box left unset; Travel skills pressed beside it not read: MEDIUM)"),
            "confidence": "MEDIUM"}
    return ([models.raw_mod("Damage", "MORE", more, skill=main.name)] if more > 0 else []), [note]


def blood_hunt(build: Build, main: models.Skill | None, texts: list[str]) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Blood Hunt's explosions as the main skill (MECH75; models.blood_hunt_model's note): its
    level stats' explosion %, the other skills' Bleed PoB counts (Best Bleed DPS among the Full DPS skills: Blood Hunt
    never Bleeds itself) kept to the share Blood Hunt's Consume leaves (consumed_ignite_scale for another skill's
    Bleed, as consume_ignite scales that entry), and the build's "causes Blood Loss" lines on its Physical hits."""
    if main is None or main.name != models.BLOOD_HUNT:
        return 0.0, []
    stats = build.call("stats", fields=["Speed", "HitChance", "PhysicalHitAverage", "MainHand.PhysicalHitAverage"])
    hits = models.number(stats.get("Speed")) * (models.number(stats.get("HitChance")) or 100.0) / 100.0
    bleed = max((models.number(e.get("dps")) for e in (build.stats or {}).get("SkillDPS") or []
                 if e.get("name") == "Best Bleed DPS"), default=0.0)
    scale, _ = conditions_mod.consumed_ignite_scale(1.0, hits, 0.0, conditions_mod.AILMENT_DURATION["Bleed"], False)
    raw = build.call("activeSkills", levelStats=[models.BLOOD_HUNT])
    got = next((s.get("levelStats") or {} for s in raw["skills"] if s["name"] == models.BLOOD_HUNT
                and models.BLOOD_HUNT_STAT in (s.get("levelStats") or {})), {})
    pct = models.number(got.get(models.BLOOD_HUNT_STAT)) or models.BLOOD_HUNT_PCT
    share = sum(float(m.group(1)) / 100 for t in texts if (m := models.HIT_BLOOD_LOSS.match(t.strip())))
    physical = models.number(stats.get("PhysicalHitAverage")) or models.number(stats.get("MainHand.PhysicalHitAverage"))
    dps, how = models.blood_hunt_model(pct, bleed, scale, physical * hits, share)
    return dps, [{"label": "Blood Hunt's explosions (Blood Loss as Life Loss)*", "value": f"{dps:,.0f} DPS",
                  "basis": how, "confidence": "MEDIUM"}]


def impale(build: Build, main: models.Skill | None, texts: list[str]) -> tuple[float, float, list[dict], list[dict]]:
    """(the main skill's own Impale DPS, the other groups' Impale DPS, mods, notes) (MECH76; models.impale_model's note:
    PoB forces Impale to 0). The main skill's own goes in as a derived input the bridge works out again per what-if
    (DERIVED.impale); the other groups' Impales and Extractions (every enabled group's useRates row at its use rate:
    PoB's trigger rate, else conditions.skill_use_rate) as a fixed added source. mods: the hand wraps' two lines PoB
    leaves unparsed (models.impale_mods)."""
    mods = models.impale_mods(texts)
    row = build.call("stats", fields=models.IMPALE_FIELDS)
    own, how = models.impale_model(row)
    main_index = (build.main_group() or {}).get("index")
    choices = models.press_choices(build)
    others = []
    groups = [g for g in build.groups() if g["enabled"] and g["index"] != main_index]
    # (another group's rows only where something Impales: the main skill, a line naming Impale, or an Impaling gem)
    wanted = (models.impale_parts(row)["inflict"] > 0 or any("impale" in t.lower() for t in texts)
              or any(n.get("name") in models.IMPALE_GEMS for g in build.groups() if g["enabled"] for n in g.get("gems") or []))
    indices = [g["index"] for g in groups] if wanted else []
    for rate in (build.call("useRates", indices=indices)["rates"] if indices else []):
        if number(rate.get("LedgerImpaleChance")) <= 0 and number(rate.get("LedgerImpaleChanceCrit")) <= 0                 and number(rate.get("LedgerImpaleExtract")) <= 0:
            continue
        uses = number(rate.get("speed")) if rate.get("triggered") else conditions_mod.skill_use_rate(rate, choices)[0]
        others.append((str(rate.get("name") or "another skill"), uses, rate))
    other, other_how = models.impale_others(row, others) if others else (0.0, "")
    notes = []
    if how or other_how:
        notes.append({"label": "Impale: Extracted by Attack hits (PoB counts none)*",
                      "value": f"{own + other:,.0f} DPS",
                      "basis": "; ".join(part for part in (how, other_how) if part),
                      "confidence": "MEDIUM"})
    if own > 0:
        mods.append(models.derived("AddedImpale", "impale", own * 100, base=models.IMPALE_STORED))
    return own, other, mods, notes


def grisly_pyre(build: Build, main: models.Skill | None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for His Winnowing Flame's Grisly Pyres (MECH71; models.grisly_pyre_model): the skill's uses
    (the main skill's Speed, else its group's rate: conditions.skill_use_rate, the search's chosen press rate where
    nothing else sets it, else 0), the boss's Ignited share (build.ailment_uptimes, after the consumption), and the main
    skill's Ignite (PoB's IgniteDPS, IgniteDuration, its hits x IgniteChancePerHit). Where the main skill doesn't Ignite
    (His Winnowing Flame itself never does), another group's Ignite (MECH74: models.grisly_pyre_other_ignite)."""
    skill = models.GRISLY_PYRE_SOURCE
    choices = models.press_choices(build)
    uses = 0.0
    pairs = [(each["index"], position, entry["name"]) for each in build.groups() if each["enabled"]
             for position, entry in enumerate(each["skills"], start=1)]
    is_main = main is not None and main.name == skill
    if is_main:
        uses = models.number(build.call("stats", fields=["Speed"]).get("Speed"))
    elif not any(name == skill for _, _, name in pairs):
        return 0.0, []
    fields = list(dict.fromkeys([*models.MODEL_FIELDS, "IgniteDPS", "IgniteDuration", "IgniteChancePerHit"]))
    stats = build.call("stats", fields=fields)
    own = not is_main and models.number(stats.get("IgniteDPS")) > 0 and models.number(stats.get("IgniteChancePerHit")) > 0
    # (each group rated as the main skill: His Winnowing Flame's for its uses, the others only where the main skill
    # doesn't Ignite, for their Ignite)
    rated = [pair for pair in pairs if pair[2] == skill or not own]
    raw = build.call("useRates", indices=[i for i, _, _ in rated], actives=[p for _, p, _ in rated])["rates"] if rated else []
    by = {(r.get("index"), r.get("active")): r for r in raw}
    rows = [(name, by.get((i, p)) or by.get((i, None)) or {}) for i, p, name in rated]
    for name, row in rows:
        if name == skill:
            uses = max(uses, conditions_mod.skill_use_rate(row, choices)[0])
    if own:
        applications = models.hits_per_second(stats) * min(models.number(stats.get("IgniteChancePerHit")), 100) / 100
        ignited = models.number((getattr(build, "ailment_uptimes", None) or {}).get("Ignited"))
        dps, how = models.grisly_pyre_model(uses, ignited, models.number(stats.get("IgniteDPS")),
                                            models.number(stats.get("IgniteDuration")), applications)
    else:
        # (MECH74) the strongest other group's Ignite, scored as PoB scores it as the main skill, at its own use rate
        best = (0.0, None)
        for name, row in rows:
            if name == skill or models.number(row.get("IgniteDPS")) <= 0:
                continue
            rate, why, _ = conditions_mod.skill_use_rate(row, choices)
            hits = rate * (models.number(row.get("HitChance")) or 100.0) / 100
            chance = min(models.number(row.get("IgniteChancePerHit")), 100) / 100
            weight = models.number(row.get("IgniteDPS")) * hits * chance
            if weight > best[0]:
                best = (weight, (name, row, hits, chance, why))
        if best[1] is None:
            dps, how = models.grisly_pyre_model(uses, 0.0, 0.0, 0.0, 0.0)
        else:
            name, row, hits, chance, why = best[1]
            duration = models.number(row.get("IgniteDuration"))
            ignited, share_how = models.grisly_pyre_other_ignite(uses, hits, chance, duration)
            dps, how = models.grisly_pyre_model(uses, ignited, models.number(row.get("IgniteDPS")), duration,
                                                hits * chance)
            how = (f"{name}'s Ignite ({why}; PoB's {models.number(row.get('IgniteDPS')):,.0f} Ignite DPS for it as the "
                   f"main skill): {share_how}; {how}; the time split between the two skills not taken off*")
    return dps, [{"label": f"{models.GRISLY_PYRE_SKILL} ({skill})*", "value": f"{dps:,.0f}", "basis": how,
                  "confidence": "MEDIUM"}]


def battershout(build: Build) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Battershout's explosions (MECH72; models.battershout_model): the rate from conditions'
    Armour Break cycle (build.battershout), each explosion's hit from PoB (bridge.battershoutHit, the highest group's)."""
    rate, armour, how = getattr(build, "battershout", None) or (0.0, 0.0, "")
    if rate <= 0 or armour <= 0:
        return 0.0, []
    rows = build.call("battershoutHit", base=models.BATTERSHOUT_ARMOUR_SHARE * armour, unbroken=True).get("groups") or []
    hit = max((models.number(row.get("AverageDamage")) for row in rows), default=0.0)
    dps, basis = models.battershout_model(rate, hit, armour, how)
    return dps, [{"label": "Battershout explosions*", "value": f"{dps:,.0f}", "basis": basis, "confidence": "MEDIUM"}]


def crackling_palm(build: Build) -> tuple[float, list[dict]]:
    """(added DPS, notes) for Crackling Palm's bolts (MECH77; Thunderfist's skill): PoB works out the Bolt set's hit
    (setRates: AverageDamage, hit chance included; probe POE2T3109, Killing Palm unarmed with Hollow Palm Technique:
    102.5 a bolt) but scores it 0 DPS (a Persistent skill: Speed 0, Full DPS adds nothing). The bolts land at
    conditions.crackling_palm_rate (one per Unarmed Melee hit of the main skill, at most 10 a second); each taken to hit
    the boss (it strikes around you, in melee range: MEDIUM)."""
    from builder.search.conditions import CRACKLING_PALM, crackling_palm_rate

    spot = next(((g["index"], position) for g in build.groups() if g["enabled"]
                 for position, skill in enumerate(g["skills"], start=1) if skill["name"] == CRACKLING_PALM), None)
    if spot is None:
        return 0.0, []
    stats = build.call("stats", fields=["Speed", "HitChance", "LedgerUnarmedMelee"])
    rate, how = crackling_palm_rate(stats, stats)
    if rate <= 0:
        return 0.0, [{"label": "Crackling Palm bolts*", "value": "0", "basis": how, "confidence": "MEDIUM"}]
    sets = build.call("setRates", index=spot[0], active=spot[1]).get("sets") or []
    bolt = next((s for s in sets if s.get("label") == "Bolt"), None)
    if bolt is None or number(bolt.get("TotalDPS")) > 0:  # (none, or PoB scores it itself: nothing to add)
        return 0.0, []
    hit = number(bolt.get("AverageDamage"))
    dps = rate * hit
    return dps, [{"label": "Crackling Palm bolts*", "value": f"{dps:,.0f}",
                  "basis": f"{rate:.2f} bolts/s ({how}) x {hit:,.1f} a bolt (PoB's Bolt set, hit chance in; PoB scores "
                           f"it 0); each bolt taken to hit the boss (MEDIUM)", "confidence": "MEDIUM"}]


def thorns(build: Build, main: models.Skill | None, stats: dict, texts: list[str]) -> tuple[float, list[dict]]:
    """(added DPS, notes) for the build's Thorns hits on the boss (models.thorns_model, MECHB22): PoB's own Thorns
    group's hit (useRates), which no PoB DPS counts."""
    group = next((g for g in build.groups() if g["fromItem"] and g["label"] == models.THORNS_GROUP and g["gems"]), None)
    if group is None:
        return 0.0, []
    rates = build.call("useRates", indices=[group["index"]]).get("rates") or []
    if not rates:
        return 0.0, []
    barbs = []  # (MECHB23: each Barbs / Quill Burst skill PoB adds to a supported group, its own hit, whether it supports the main one)
    found = None
    press = getattr(build.objective, "press_scales", None) or {}
    for each in build.groups():
        if not each["enabled"]:
            continue
        for position, skill in enumerate(each["skills"], start=1):
            if skill["name"] in models.BARBS or skill["name"] == models.QUILL_BURST:
                row = next(iter(build.call("useRates", indices=[each["index"]], actives=[position])["rates"]), None)
                on_main = bool(each["main"]) and main is not None and main.group == each["index"]
                side = None
                if not on_main and skill["name"] in models.BARBS:  # (MECHB24: the supported skill's own hits)
                    if found is None:
                        found = [models.Skill.of(s) for s in build.call("activeSkills")["skills"]]
                    own = next((s for s in found if s.group == each["index"] and s.name not in models.BARBS
                                and s.name != models.QUILL_BURST), None)
                    spot = next((i for i, s in enumerate(each["skills"], start=1) if own and s["name"] == own.name), None)
                    used = next(iter(build.call("useRates", indices=[each["index"]], actives=[spot])["rates"]), None) if spot else None
                    if own is not None and used:
                        share = number(press.get(own.name, 1.0))
                        chance = number(used.get("HitChance"))
                        # (PoB may give a side attack's row no HitChance, POE2T1106; AUDIT34 A34-9: then the main
                        # skill's own hit chance, an attack's, from the same build's accuracy against the same boss
                        # (MEDIUM: a different weapon or accuracy lines for one skill would differ); 100% only when the
                        # main skill gives none, labelled UNVERIFIED)
                        fallback = number(stats.get("HitChance")) if main is not None and main.attack else 0.0
                        how = ("" if chance else f" (PoB gave none: the main skill's {fallback:.0f}%, MEDIUM)" if fallback
                               else " (PoB gave none, and no main attack's hit chance to stand in: 100% taken, UNVERIFIED)")
                        chance = chance or fallback or 100.0
                        side = {"skill": own.name, "melee_attack": own.attack and own.melee and not own.triggered,
                                "hits": number(used.get("speed")) * min(chance, 100.0) / 100 * share,
                                "basis": "its group's use rate x hit chance" + how
                                         + (f" x {share:.3g}, the press model's share" if share != 1.0 else "")}
                barbs.append((skill["name"], row or {}, on_main, side))
    return models.thorns_model(rates[0], texts, {**stats, **build.call("stats", fields=models.THORNS_FIELDS)}, main,
                               barbs)


def brambles(build: Build, main: models.Skill | None) -> list[dict]:
    """Notes (with "full_dps" scales) for each Brambleslam's Brambles (models.brambles_model, MECHB24): PoB scores the
    Brambles skill it adds to the slam's group at its own Speed; the slam's real uses a second set the Brambles' rate:
    the group's useRates speed, x the press model's share for a pressed side group (press_scales)."""
    notes = []
    press = getattr(build.objective, "press_scales", None) or {}
    for group in build.groups():
        if not group["enabled"] or not group.get("fullDPS") and not group["main"]:
            continue
        names = [s["name"] for s in group["skills"]]
        if models.BRAMBLES not in names:
            continue
        rows = [{**r, "name": names[pos - 1]} for pos in range(1, len(names) + 1)  # (one row per call: by position)
                for r in build.call("useRates", indices=[group["index"]], actives=[pos])["rates"][:1]]
        row = next((r for r in rows if r.get("name") == models.BRAMBLES), None)
        slams = [r for r in rows if r.get("name") not in (models.BRAMBLES, None) and number(r.get("speed")) > 0]
        if row is None or not slams:
            continue
        slam = slams[0]
        own = 1.0 if group["main"] else number(press.get(slam["name"], 1.0))
        basis = ("the main skill's Speed" if group["main"] else
                 "its group's use rate" + (f" x {own:.3g}, the press model's share" if own != 1.0 else ""))
        notes += models.brambles_model(slam["name"], number(slam.get("speed")) * own, basis, row)
    return notes

def _zerphi_lines() -> tuple:
    """Zerphi's Genesis's two transfer lines (flasks.ZERPHI_TO_LIFE / ZERPHI_TO_CHARM; MECHC36)."""
    from builder.search import flasks

    return flasks.ZERPHI_TO_LIFE, flasks.ZERPHI_TO_CHARM


class Assumptions:
    def __init__(self, build: Build) -> None:
        self.build = build
        self.mods: list[dict] = []
        self.active: list[dict] = []  # what the result shows
        self.cooldown_fallback: set[str] = set()  # triggered main skills PoB gave no rate
        self.extra_groups: dict[int, int | None] = {}  # groups whose triggered skills count through Full DPS
        self.stat_sets: dict[int, int] = {}  # stat sets chosen for them
        # The main skill's Infused stat set while it's the calculated one (Gems.choose_active_skill): label, factor (its
        # damage scaled to plain + share x (Infused - plain)), share, basis. `infused_skipped`: label -> why it isn't
        self.infused: dict | None = None
        self.infused_skipped: dict[str, str] = {}
        self.press_busy = 0.0  # the share of the fight remnant_presses took from the main skill in the last wanted()
        self.untether_speed = 1.0  # (MECH-B, MECHB32) Untether as the main skill: the share of PoB's casts that explode
        self.bomb_speed = 1.0  # Living Bomb as the main skill: the share of PoB's casts that explode, now on PoB's numbers
        self.ledger_shares: dict[str, int] = {}  # condition -> its share in whole percent, for the ledger's `share:` lines

    def main_skill(self) -> str | None:
        group = self.build.main_group()
        if not group or not group["skills"]:
            return None
        return group["skills"][min(group["mainActiveSkill"], len(group["skills"])) - 1]["name"]

    def models(self, skills: list[dict]) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """The custom models' lines, notes and extra damage groups ({index: stat set to calculate,
        or None to leave it}) for the current build."""
        build = self.build
        group = build.main_group()
        name = self.main_skill()
        found = [models.Skill.of(s) for s in skills if not s["disabled"]]
        main = next((s for s in found if group and s.group == group["index"] and s.name == name), None)
        main_gems = {g["name"] for g in group["gems"]} if group else set()
        stats = build.call("stats", fields=models.MODEL_FIELDS)
        stats.update(models.dazed_stats(build))  # (MECH70: a Dazed boss's Stun Buildup, heavy_stun_rate)
        from builder.search import bosses  # (MECH-D: the request's boss, its add kills / Culls per second, for the models)
        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        stats = {**stats, **bosses.kill_stats(boss)}
        build.minion_lives = minion_lives(build, found)  # (MECHB12, A25-2: Minion Pact's Kills read the minions' Life)
        # (MECHB12, A25-41: each curse's own duration, PoB's with the build's modifiers, for curse_uses)
        build.curse_durations = {s["name"]: s["curseDuration"] for s in skills if s.get("curseDuration")}
        # (MECH86: each curse's activation delay, PoB's with the build's modifiers, for curse_uses / hexblast_model)
        build.curse_delays = {s["name"]: s["curseDelay"] for s in skills if s.get("curseDelay") is not None}
        stats = {**stats, "_minion_lives": build.minion_lives, "_curse_durations": build.curse_durations,
                 "_curse_delays": build.curse_delays}
        if any(s.name == models.SACRIFICE for s in found):  # (MECH-B, MECHB19, A27-4: Sacrifice's corpses, the build's own
            # undead Reviving minion deaths, for models.corpse_supply's readers)
            stats = {**stats, models.SACRIFICE_CORPSES_STAT: models.sacrifice_corpses(found, main, stats)}
        if any(s.name == models.UNEARTH for s in found):  # (MECHB21, A28-5: Unearth's Bone Constructs from any dead
            # Reviving minion, with or without Sacrifice: models.reviving_extra / unearth_constructs)
            stats = {**stats, models.REVIVING_DEATHS_STAT: models.minion_deaths(found, main, stats)}
        if any(s.name == "Cast on Charm Use" for s in found):  # (its Energy: charm charges spent, from the flask model)
            stats = {**stats, **charm_stats(build)}
        if any(s.name == "Cast on Minion Death" for s in found):  # (its Energy: the build's minions dying)
            stats = {**stats, "_minion_deaths": models.minion_deaths(found, main, stats)}
        source_lines = build.call("sourceLines")["lines"]  # (one call: the models below all read the build's lines)
        if any(s.name == "Cast on Dodge" for s in found):  # (its Energy: the build's own roll distance; MECH-B, MECHB3)
            stats = {**stats, "_dodge_roll": models.dodge_roll([e["text"] for e in source_lines], stats)}
        forbidden = set(build.ctx.request.forbid_ledger_entries)
        inputs = models.read_inputs(source_lines)
        # (MECH67) the boss's Power with the build's Power lines (models.boss_power), for the Energy / Glory / roar
        # models and PoB's own EnemyPower / WarcryPower readers
        texts = [e["text"] for e in source_lines]
        power = models.boss_power(texts, stats, models.number(build.call("getConfig")["input"].get("enemyDistance")) or None,
                                  bool(main and main.attack), main.name if main else "")
        stats = {**stats, "_boss_power": power}
        if any(conditions_mod.BUFF_EXPIRY.search(e["text"]) for e in source_lines):  # (MECHC35, RESEARCH29)
            stats = {**stats, "_buff_expiry": conditions_mod.buff_expiry([e["text"] for e in source_lines])}
        mods, notes = models.archon_model(inputs, stats, main, main_gems)
        # (MECH79, MECH-A) Incinerate's fuel (its idle line) and the main skill's Compounding Ignite
        compound_mods, compound_notes = models.compound_ignite_mods(name or "", getattr(build, "compound_ignite", None))
        mods, notes = mods + compound_mods, notes + compound_notes
        uruk_mods, uruk_notes = models.uruk_mods(getattr(build, "uruk_taken", None))  # (MECH80, G2-4)
        mods, notes = mods + uruk_mods, notes + uruk_notes
        storm_mods, storm_notes = models.stormfire_mods(name or "", getattr(build, "stormfire", None))  # (MECH81, G2-2)
        mods, notes = mods + storm_mods, notes + storm_notes
        flam_mods, flam_notes = models.flammability_mods(name or "", getattr(build, "flammability_scale", None))  # (MECH81)
        mods, notes = mods + flam_mods, notes + flam_notes
        if power[1]:
            mods += models.power_mods(power[0])
            notes.append({"label": "Boss Power (your Power lines)*", "value": f"{power[0]:g}", "basis": power[1],
                          "confidence": "MEDIUM"})
        supports = {g["index"]: [x["name"] for x in g["gems"] if x["support"] and x.get("enabled", True)]
                    for g in build.groups()}
        qualities = {g["index"]: models.number(x.get("quality")) for g in build.groups() for x in g["gems"]
                     if not x["support"] and x["name"] in models.QUALITY_MAX_ENERGY}  # (an Invocation's store, MECHB26)
        full_mana, full_mana_notes = models.full_mana_energy(texts, stats)  # (MECHB31: Idle Hands' Cultivated line)
        if full_mana:
            inputs.items["MetaEnergy"] = inputs.items.get("MetaEnergy", 0.0) + full_mana
        energy_mods, energy_notes, energy_groups = models.energy_model(inputs, stats, found, main, supports, qualities,
                                                                       models.press_choices(build))
        energy_notes = energy_notes + full_mana_notes
        # (the meta gems' trigger rates, for the curses and Marks they trigger: MECH46, FLAT_RATES A5)
        build.trigger_rates = {m["skill"]: m["value"] for m in energy_mods
                               if m.get("kind") == "SkillData" and m.get("key") == "triggerRate"}
        expression_mods, expression_notes, expression_groups = models.expression_model(found, stats, main)
        levels = {g["name"]: g["level"] for grp in build.groups() for g in grp["gems"] if g.get("level")}
        totem_mods, totem_notes = models.totem_model(found, stats, [e["text"] for e in source_lines], levels, main)
        energy_mods, energy_notes = energy_mods + totem_mods, energy_notes + totem_notes
        count_mods, count_notes = models.totem_count_model(found, main, [e["text"] for e in source_lines], stats)  # (MECH-B, MECHB8: other totems' count)
        energy_mods, energy_notes = energy_mods + count_mods, energy_notes + count_notes
        pact_mods, pact_notes = models.minion_pact_model(found, main, stats)  # (MECH-B, MECHB11: A23-2)
        energy_mods, energy_notes = energy_mods + pact_mods, energy_notes + pact_notes
        ward_mods, ward_notes = models.iron_ward_model(
            found, stats, enabled=models.IRON_WARD_ENTRY not in build.ctx.request.forbid_ledger_entries)
        # (a non-main skill's own output for a support counted from it, Practical Magic on a stunning attack: the bridge's
        # useRates for its group, with the share of its nonstop uses the press model last gave it)
        side = models.gem_stat_side_groups(found, main)
        press = getattr(build.objective, "press_scales", None) or {}
        side_groups = {r["index"]: {**r, "_press": press.get(r["name"])}
                       for r in build.call("useRates", indices=side)["rates"]} if side else None
        stat_mods, stat_notes = models.gem_stat_model(found, set(build.ctx.request.forbid_ledger_entries), stats, main,
                                                      self.mods, build.call("getConfig")["input"], side_groups,
                                                      [e["text"] for e in source_lines],  # (GEM11: Salvo's Seal lines)
                                                      enemy_level(build))  # (GEM13: Frost Bomb's level cap)
        stat_mods = stat_mods + models.cry_stage_floor(stat_mods, forbidden)  # (GEM17: PoB's stages box reads 10)
        harmony_mods, harmony_notes = models.harmony_model(inputs, stats)
        ring_mods, ring_notes = models.resist_rings_model(source_lines) if models.RESIST_ENTRY not in forbidden else ([], [])
        main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or []) if main is not None else set()
        hand_mods, hand_notes = models.guided_hand_model(inputs, source_lines, stats, main, slam="Slam" in main_types)
        texts = [e["text"] for e in source_lines]
        # (MECH69) PoB's CanApply flags for Exposure lines it doesn't parse ("N% chance to inflict Exposure on Hit"): the
        # -20% comes from the condition box at the line's uptime (conditions._exposure_uptime)
        exposure_flags = conditions_mod.exposure_source_flags(texts, main_types)
        exposure_flags += conditions_mod.wither_source_flags(texts, main_types)  # (MECH72: partly parsed Withered lines)
        wither_mods, wither_how = conditions_mod.minion_wither_magnitude(  # (MECH82: COV37, your Magnitude on minions')
            texts, main.name if main else "", {g["name"] for grp in build.groups() for g in grp["gems"]
                                               if grp["enabled"] and g.get("enabled", True)})
        exposure_flags += wither_mods
        if wither_how:
            hand_notes.append({"label": "Withered from your minions: its magnitude*", "value": "5% a stack" if wither_mods
                               else "your Magnitude", "basis": wither_how, "confidence": "MEDIUM"})
        stoic_mods, stoic_notes = stoicism(build, main, main_types, texts)  # (MECH75: Stoicism's ramp between resets)
        hand_mods, hand_notes = hand_mods + stoic_mods, hand_notes + stoic_notes
        if main is not None:  # (MECH75: Overcharge's reflected Shock over PoB's 15% "Are you Shocked?" box)
            overcharge, overcharge_how = conditions_mod.overcharge_taken_mods(build, main.name)
            exposure_flags += overcharge
            if overcharge_how:
                hand_notes.append({"label": "Overcharge: your Shocks reflected back to you*", "value": "damage taken",
                                   "basis": overcharge_how, "confidence": "MEDIUM"})
        else:
            overcharge = []
        # (MECHC32: Redflare Conduit's Shock on you on reaching maximum Power Charges, at the base 20% over PoB's 15%)
        effect, inflict = 100.0, 1.0
        if not overcharge and any(conditions_mod.SELF_SHOCK_ON_MAX.match(t.strip()) for t in texts):
            # (MECHC33, RESEARCH28 R28-5: x the build's "effect of Shock on you"; its own inflicted magnitude a row)
            read = build.call("stats", fields=["SelfShockEffect"]).get("SelfShockEffect")
            effect = number(read) if read is not None else 100.0
            mag = build.call("shockMagnitude") or {}
            inflict = (1 + number(mag.get("inc")) / 100) * (number(mag.get("more")) if mag.get("more") is not None else 1.0)
        redflare, redflare_how = conditions_mod.self_shock_taken_mods(texts, overcharge, effect, inflict)
        if main is not None and main.name == recovery_mod.TWISTER:  # (MECHC33, R28-10: hits on the boss per cast)
            seen = build.call("stats", fields=list(recovery_mod.TWISTER_FIELDS))
            radius, radius_how = bosses.hit_radius({"_boss": boss})
            head, open_row, how = recovery_mod.twister_hits(seen, radius)
            # (MECHC35, the user's answer 16 Nov ~21:10: the twister-count reading is the headline; PoB scores one hit)
            if head > 1 + 1e-9:
                hand_mods = hand_mods + [models.raw_mod("DPS", "MORE", (head - 1) * 100, skill=main.name)]
            hand_notes = hand_notes + [{
                "label": "Twister hits on the boss per cast*", "value": f"{head:.2f} (open arena: {open_row:g})",
                "basis": how + f"; boss radius: {radius_how}",
                "confidence": "MEDIUM-LOW (the user's observation; the roam space LOW) / HIGH (the 0.66 s cap)",
                "play": (f"fire many twisters and fight near walls so they bounce back through the boss: {head:.2f} hits a "
                         f"cast counted (with it); in an open arena 1 a cast (without it)")}]
        if any(recovery_mod.SPRINT_LINE.search(t) for t in texts):  # (MECHC34, R26-22: the headline sprints 0; a row)
            sprint = recovery_mod.sprint_row(texts, {**build.call("stats", fields=["MovementSpeedMod"]),
                                                     "_buff_expiry": stats.get("_buff_expiry")}, boss)
            hand_notes = hand_notes + ([sprint] if sprint else [])
        if any(recovery_mod.OVERENCUMBRANCE.match(t.strip()) for t in texts):  # (MECHC35: Ab Aeterno's own Slow)
            reliant = any(s.name == "Cast on Dodge" for s in found) or any(
                ROLL_RELIANT.search(t) and not recovery_mod.OVERENCUMBRANCE.match(t.strip()) for t in texts)
            slow, slow_how = recovery_mod.overencumbrance_slow(texts, reliant)
            if slow > 0:
                hand_mods = hand_mods + [models.raw_mod("ActionSpeed", "INC", -slow, tag="Overencumbrance")]
            hand_notes = hand_notes + [{
                "label": "Overencumbered by your own dodge rolls*", "value": f"{slow:.1f}% Slow on average",
                "basis": slow_how, "confidence": "MEDIUM (the roll pace LOW; no stack cap found: UNVERIFIED)",
                "play": ("roll no more often than every 4 s while wearing it, so the Slow never stacks (with it: one "
                         "stack; rolling every 2 s: a stack more each roll; never rolling: none)")}]
        if main is not None and not main.triggered and {"Channel", "Cooldown"} <= set(main_types):
            # (MECHC33, GEM15's find: a channelled skill on a cooldown pays its per-minute cost only while channelled)
            kinds = ("Mana", "Life", "ES", "Rage")
            seen = build.call("stats", fields=[*recovery_mod.CHANNEL_COST_FIELDS, *(f"{k}Cost" for k in kinds),
                                               *(f"{k}PerSecondCost" for k in kinds)])
            over = [k for k in kinds if number(seen.get(f"{k}PerSecondCost")) > 0 and number(seen.get(f"{k}Cost")) <= 0]
            if over:
                share, other, how = recovery_mod.channel_cost_share(seen, main.name)
                if share < 1:
                    hand_mods = hand_mods + [models.raw_mod(f"{k}Cost", "MORE", (share - 1) * 100, skill=main.name,
                                                            tag="ChannelCost") for k in over]
                hand_notes = hand_notes + [{
                    "label": f"{main.name}: per-minute cost only while channelled*",
                    "value": f"{share:.0%} of the fight ({', '.join(over)})",
                    "basis": how + ("; its upfront costs stay whole" if any(number(seen.get(f'{k}Cost')) > 0
                                                                            for k in kinds) else ""),
                    "confidence": "MEDIUM (channel time, PoB's) / HIGH (when the cooldown starts)"}]
        # (MECH84: COV38's downsides: your curses reflected back to you, and "Your Hits cannot Stun enemies")
        reflecting = any(conditions_mod.CURSE_REFLECT.match(t.strip()) for t in texts) or any(
            g["name"] == conditions_mod.ATZIRIS_ALLURE for grp in build.groups() for g in grp["gems"]
            if grp["enabled"] and g.get("enabled", True))
        reflected, reflected_how = conditions_mod.reflected_curse_mods(
            texts, (build.call("cursesInflicted") or {}).get("curses") or []) if reflecting else ([], "")
        if reflected_how:
            exposure_flags += reflected
            hand_notes.append({"label": "Your curses reflected back to you*", "value": "cursed yourself",
                               "basis": reflected_how, "confidence": "MEDIUM (the reflected curse's magnitude)"})
        no_stun, no_stun_how = conditions_mod.cannot_stun_mods(texts)
        if no_stun_how:
            exposure_flags += no_stun
            hand_notes.append({"label": "Your Hits cannot Stun enemies*", "value": "no Stuns of the boss",
                               "basis": no_stun_how, "confidence": "HIGH"})
        if any(conditions_mod.SELF_IMMOLATION_LINE.match(t.strip()) for t in texts):
            # (MECH85: Self Immolation's "Ignites you cause are reflected back to you": the burn on you, PoB's degen)
            burn, burn_how = conditions_mod.reflected_ignite_burn(
                texts, build.call("stats", fields=list(conditions_mod.REFLECTED_IGNITE_FIELDS)))
            if burn > 0:
                hand_mods = hand_mods + [models.line("1 Fire Damage taken per second", burn)]
            hand_notes.append({"label": "Your Ignites reflected back to you (Self Immolation)*",
                               "value": f"{burn:,.0f} Fire a second", "basis": burn_how,
                               "confidence": "MEDIUM (avoidance applied; the reflected Ignite's duration yours, "
                                             "UNVERIFIED; the main skill's Ignite only)"})
        if (emp_buildup := conditions_mod.empowered_buildup(texts)) and main is not None:
            # (MECH86: Bravado / cold49 / cold50: buildup on Empowered Attacks at the main skill's Empowered share)
            emp_uses, emp_how = self.empowered_uses(stats, None) if main.attack else (0.0, "the main skill isn't an Attack")
            emp_speed = models.number(stats.get("Speed"))
            emp_share = min(1.0, emp_uses / emp_speed) if emp_speed > 0 else 0.0
            for kind, inc in emp_buildup.items():
                if emp_share > 0:
                    hand_mods = hand_mods + [models.line(f"1% increased {kind} Buildup", inc * emp_share, skill=main.name)]
                hand_notes.append({"label": f"{kind} Buildup on Empowered Attacks*",
                                   "value": f"+{inc * emp_share:.3g}% on {main.name} ({inc:g}% x {emp_share:.0%})",
                                   "basis": f"{emp_share:.0%} of {main.name}'s uses are Empowered: {emp_how} (without "
                                            "Empowering skills: 0)",
                                   "confidence": "MEDIUM (the Empowered share, Assumptions.empowered_uses)"})
        exposure_flags += redflare
        if redflare_how:
            hand_notes.append({"label": "Shocked by your own item on reaching maximum Charges*", "value": "damage taken",
                               "basis": redflare_how + " (how often: the \"Are you Shocked?\" box's note)",
                               "confidence": "LOW-MEDIUM"})
        embodiment, kaom_busy = (0.0, ""), 0.0
        cry = next((s for s in found if s.name == models.ANCESTRAL_CRY and s.group is not None), None)
        boosted = main is not None and main.attack and ("Slam" in main_types or ("Strike" in main_types and main.melee))
        if cry is not None and boosted and cry is not main and not main.triggered:
            # (MECHB29: Ancestral Cry's Kaom embodiment, at the uses its Glory pays for x its duration)
            uses, how = self.glory_caps().get(models.ANCESTRAL_CRY, (0.0, "its Glory wasn't read"))
            row = (build.call("useRates", indices=[cry.group])["rates"] or [{}])[0]
            up, kaom_busy, kaom_how = models.kaom_uptime(uses, models.number(row.get("duration")),
                                                         models.number(row.get("castTime")) or cry.cast_time, how)
            embodiment = (up, kaom_how)
            if kaom_busy > 0:  # (its casts take the main skill's time: nothing else charges a Warcry's)
                hand_mods = hand_mods + [models.line("1% more Attack and Cast Speed", -100 * kaom_busy, skill=main.name)]
        if embodiment[0] > 0 or any(models.KAOMS_BLESSING.match(t.strip()) or models.SHAPESHIFT_EVERY_SECOND_SLAM.search(t)
                                    or models.EMPOWERMENT_EVERY_SECOND_SLAM.match(t.strip()) for t in texts)                 or any(g.startswith("Fist of War") for g in main_gems):  # (MECHB35: Fist of War alone, R26-58 (b))
            # (MECH-B, MECHB18: Ancestral Boost's sources PoB leaves unparsed; PoB's own boost uptimes beside the stats;
            # MECHB19: Ancestral Empowerment + Fist of War at the lower reading; MECHB29: the Kaom embodiment)
            boost_mods, boost_notes = models.ancestral_boost_model(
                texts, {**stats, **build.call("stats", fields=models.ANCESTRAL_FIELDS)}, found, main, main_types,
                self.warcry_uses() if any(models.KAOMS_BLESSING.match(t.strip()) for t in texts) else (0.0, ""),
                embodiment)
            hand_mods, hand_notes = hand_mods + boost_mods, hand_notes + boost_notes
        sanguine_mods, sanguine_notes = models.sanguine_model(inputs, stats, self.life_flask_charges())             if inputs.has(models.SANGUINE) else ([], [])
        hand_mods, hand_notes = hand_mods + sanguine_mods, hand_notes + sanguine_notes
        resolve_mods, resolve_notes = models.deadly_resolve_model(found, stats, main)             if "deadly-resolve-rate" not in forbidden else ([], [])
        hand_mods, hand_notes = hand_mods + resolve_mods, hand_notes + resolve_notes
        metres = None
        if any(s.name == models.PREDATORS_MARK for s in found):  # (it counts you within 6 m of the boss)
            metres = models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(set())
        mark_mods, mark_notes = models.mark_model(
            found, stats, main, source_lines, metres, [models.Skill.of(s) for s in skills if s["disabled"]],
            rates=build.trigger_rates)
        hand_mods, hand_notes = hand_mods + mark_mods, hand_notes + mark_notes
        if any(g.get("name") in SIPHONING for grp in build.groups() for g in grp.get("gems") or []):
            # (MECH-C, MECHC28: Mark of Siphoning's leech at the boss's Marked share)
            siphon_mods, siphon_notes = siphoning_model(
                found, stats, main, build.groups(),
                (metres, [models.Skill.of(s) for s in skills if s["disabled"]], build.trigger_rates))
            hand_mods, hand_notes = hand_mods + siphon_mods, hand_notes + siphon_notes
        build.objective.entry_scales = {k: v for n in mark_notes for k, v in (n.get("full_dps") or {}).items()}
        curses = curse_uses(build, stats, [e["text"] for e in source_lines])
        if curses or any(models.CURSED_UNSCALED.search(e["text"]) or p.search(e["text"])
                         for e in source_lines for p, _ in models.CURSED_LINES):
            curse_mods, curse_notes = models.curse_model(curses, found, main, source_lines)
            hand_mods, hand_notes = hand_mods + curse_mods, hand_notes + curse_notes
        # (MECH-B, MECHB8: Doedre's Undoing's Witchtoads at their curse area's share; "when a Minion Dies" rate for the
        # conditions' readers: build.minion_died, (per second, how))
        toad_mods, toad_notes, toad_groups, toads = models.toad_model(found, curses)
        hand_mods, hand_notes = hand_mods + toad_mods, hand_notes + toad_notes
        hex_mods, hex_notes = models.hexblast_model(found, curses, stats, main)  # (GEM10: L2-2, the curse cycle)
        hand_mods, hand_notes = hand_mods + hex_mods, hand_notes + hex_notes
        doom_mods, doom_notes, doom_groups = models.doom_blast_model(found, curses, models.press_choices(build))  # (MECHB12: Impending Doom's expiries)
        item_mods, item_notes, item_groups = models.item_trigger_model(texts, stats, found, main)  # (MECHB36: item triggers)
        doom_mods, doom_notes, doom_groups = doom_mods + item_mods, doom_notes + item_notes, doom_groups | item_groups
        # (MECHB37: Dusk Vigil's Embers in a pause from casting; Tempest Caller's storms at the main spell's crits)
        dusk_mods, dusk_notes, dusk_groups = self.dusk_vigil(texts, stats, found, main)
        storm_mods, storm_notes, storm_groups = self.tempest_caller(texts, stats, found, main)
        cannon_mods, cannon_notes = self.cannons_ready(texts, found)  # (MECHB37: Cannons, Ready!'s totems act on your shots)
        # (MECHB38: Valako's Charge at the rate the boss's Lightning hits fill it; its side group in Full DPS)
        valako_mods, valako_notes, valako_groups = self.valakos_charge(stats, found, main)
        storm_groups = {**storm_groups, **valako_groups}
        doom_mods = doom_mods + dusk_mods + storm_mods + cannon_mods + valako_mods
        doom_notes = doom_notes + dusk_notes + storm_notes + cannon_notes + valako_notes
        doom_groups = doom_groups | dusk_groups
        hand_mods, hand_notes = hand_mods + doom_mods, hand_notes + doom_notes
        # (MECHB29: Hayoxi's Fulmination's Annihilation, one per Cursed Ground area that persists 7 s)
        annihilation_mods, annihilation_notes, annihilation_groups = models.annihilation_model(found, curses, main)
        hand_mods, hand_notes = hand_mods + annihilation_mods, hand_notes + annihilation_notes
        build.minion_died = models.minion_died_rate(found, main, stats, supports, toads, build.trigger_rates)
        # (MECHB9: the build's kills, enemies and own minions Killed (the ruling on A17-4), for on-Kill readers:
        # build.kills = (enemies/s, all/s, how); Heartbound Loop's damage taken at the minion-death rate: build.self_damage)
        build.kills = models.kill_rate(stats, *models.minion_deaths(found, main, stats))
        if any(models.CONSUMED_CORPSE.search(t) for t in texts):  # (MECHB38: COV40's ConsumedCorpseRecently route)
            build.corpses_consumed = self.corpses_consumed(found, main, stats)
        build.self_damage, heart_notes = models.heartbound_damage([e["text"] for e in source_lines], build.minion_died)
        if build.self_damage > 0:  # (MECHC9: in PoB as Physical damage over time, so recovery and the Life flask see it)
            hand_mods = hand_mods + [models.line("1 Physical Damage taken per second", build.self_damage)]
        hand_notes = hand_notes + models.kills_note(build.kills) + heart_notes
        # (MECHC37; COV40's routed OVERCOUNT, Scold's Bridle) "Take N% of Mana Costs you pay for Skills as Physical
        # Damage": one Physical hit per paid use, at the main skill's Mana cost a second; in PoB as Physical damage over
        # time as Heartbound Loop's (build.cost_damage = (per second, events per second): Build.added_stats)
        cost_burn, cost_events, cost_notes = recovery_mod.cost_damage([e["text"] for e in source_lines], stats)
        build.cost_damage = (cost_burn, cost_events)
        if cost_burn > 0:
            hand_mods = hand_mods + [models.line("1 Physical Damage taken per second", cost_burn)]
        hand_notes = hand_notes + cost_notes
        # (MECHB25, COV28) Fireflower's Fire damage taken per Ignite, at the main skill's Ignite rate: in PoB as Fire
        # damage taken per second (recovery sees it); build.ignite_self_damage = (per second, Ignites per second)
        burn, ignites, burn_notes = models.ignite_self_damage([e["text"] for e in source_lines], stats)
        build.ignite_self_damage = (burn, ignites)
        if burn > 0:
            hand_mods = hand_mods + [models.line("1 Fire Damage taken per second", burn)]
        hand_notes = hand_notes + burn_notes
        # (MECHC32) Coat of Red's "25% chance to be inflicted with Bleeding when Hit", at the boss's landed Physical hits
        # (the defence cadence): in PoB as Physical damage taken per second (boss_hits.self_bleed)
        from builder.search import boss_hits

        # (MECHC32) Widow's Reign: its damage-free window for objective.life_reserved_open (Build.added_stats); the line
        # may come split over two of the item's lines, so read the joined text
        held = LIFE_RESERVED.search("\n".join(e["text"].strip() for e in source_lines))
        build.life_reserved_seconds = float(held.group(1)) if held else 0.0
        if held:
            hand_notes = hand_notes + [{
                "label": "Life Reserved instead of lost (Widow's Reign)*",
                "value": f"Life recovery only after {held.group(1)} s with no Life damage",
                "basis": ("\"Life that would be lost by taking Damage is instead Reserved until you take no Damage to Life "
                          f"for {held.group(1)} seconds\": the reserved Life isn't given back and can't be healed while "
                          "reserved (RESEARCH26 R26-40 (a), SETTLED MEDIUM), so the recovery check counts Life recovery "
                          "only in the share of the fight past such a gap between the boss's landed hits (evenly random, "
                          "LOW-MEDIUM; 0 under your own steady Life loss); without it: Life recovery all fight"),
                "confidence": "MEDIUM (rule) / LOW-MEDIUM (the gaps)"}]

        build.self_bleed = 0.0
        if any(boss_hits.SELF_BLEED.match(e["text"].strip()) for e in source_lines):
            # (MECHC33: the recovery outputs too, for the share of hits that reach Life past Energy Shield)
            from builder.search import objective

            added = getattr(build, "added_stats", None)
            seen = {**build.call("stats", fields=sorted(set(boss_hits.SELF_BLEED_FIELDS) | set(objective.RECOVERY_INPUTS))),
                    **(added() if callable(added) else {})}
            boss_name = getattr(getattr(build, "ctx", None), "request", None)
            if getattr(boss_name, "boss", None):
                seen["_boss"] = boss_name.boss
            seen["_rolling"] = sum(float(m.group(1)) / 100 for n in hand_notes  # (Search.rolling_share's reading)
                                   if str(n.get("label", "")).startswith("Time spent dodge rolling")
                                   and (m := re.match(r"([\d.]+)%", str(n.get("value")))))
            bleed, bleed_notes = boss_hits.self_bleed([e["text"] for e in source_lines], seen)
            # (the manager's 09:09 ruling: Life only, through Build.added_stats, objective.self_bleed_taken; not PoB's
            # Physical degen, which Energy Shield would take)
            build.self_bleed = bleed
            hand_notes = hand_notes + bleed_notes
        if bulwark := next((m for e in source_lines if (m := recovery_mod.ROLLING_LESS.match(e["text"].strip()))), None):
            # (MECHC38; COV40's routed UNDERCOUNT: Bulwark) part-time, so a row only (the floors count only what is up all
            # fight: the user's 9 Nov rule; PoB's InDodgeRoll stays off)
            rolling = sum(float(m.group(1)) / 100 for n in hand_notes
                          if str(n.get("label", "")).startswith("Time spent dodge rolling")
                          and (m := re.match(r"([\d.]+)%", str(n.get("value")))))
            hand_notes = hand_notes + [recovery_mod.rolling_less_row(float(bulwark.group(1)), rolling)]
        # (MECHC35, COV38's sweep, an UNDERCOUNT: Decree of Loyalty) your own Bleed / Poison / damage over time on Energy
        # Shield, not Life (objective.own_loss), and the boss's Bleeds holding Energy Shield Recharge off (boss_hits.es_dot)
        build.dot_hits_es = any(DOT_HITS_ES_LINE.match(e["text"].strip()) for e in source_lines)
        if build.dot_hits_es:
            hand_notes = hand_notes + [{
                "label": "Damage over time can't bypass your Energy Shield (Decree of Loyalty)*", "value": "on",
                "basis": ("your own Bleed and Poison (which bypass Energy Shield: KeywordPopups) and your own damage over "
                          "time land on Energy Shield instead of Life, and the boss's Bleeds (Olroth's) now hold Energy "
                          "Shield Recharge off; PoB parses the line to nothing (without it: they hit Life)"),
                "confidence": "MEDIUM (the line's own words; whether a DoT that can't bypass removes double Energy "
                              "Shield as Chaos does is UNVERIFIED: not counted)"}]
        # (MECHC37; COV40's routed OVERCOUNT, The Black Doubt) "Damage over Time bypasses your Energy Shield": your own
        # damage over time and the boss's Ignites / Corrupted Blood land on Life (objective.own_loss, the Life refill
        # costs), and no longer hold Energy Shield Recharge off (boss_hits.es_dot); PoB parses it to nothing (ModCache
        # 7129). With Decree of Loyalty's opposite line too, Decree's is taken (UNVERIFIED which wins; no item pair known).
        build.dot_bypasses_es = (not build.dot_hits_es
                                 and any(DOT_BYPASSES_ES_LINE.match(e["text"].strip()) for e in source_lines))
        if build.dot_bypasses_es:
            hand_notes = hand_notes + [{
                "label": "Damage over time bypasses your Energy Shield (The Black Doubt)*", "value": "on",
                "basis": ("your own damage over time and the boss's Ignites (and Atziri's Corrupted Blood) land on Life, "
                          "not Energy Shield, so they no longer stop Energy Shield Recharge but your Life must carry them "
                          "(with Chaos Inoculation's 1 Life they are lethal); PoB parses the line to nothing; without it: "
                          "Energy Shield takes them first"),
                "confidence": "MEDIUM (the line's own words)"}]
        # (MECHC37; COV40's routed OVERCOUNT, The Infinite Pursuit) "N% increased Chance to be afflicted by Ailments when
        # Hit": the boss's Ignite and Shock chances on you x (1 + N%) (boss_hits.afflicted_scale, through added_stats)
        build.afflicted_inc = boss_hits.afflicted_inc([e["text"] for e in source_lines])
        build.self_poison = 0.0
        build.boss_ignite = 0.0  # (MECHC37: the boss's Ignites on you, Build.added_stats' BossIgnitePerSecond)
        # (MECHC37) one read for the boss's ailments on you: its own, for every build (boss_hits.boss_ailments_on_you, the
        # manager's 17 Nov ruling), and the extra your items' "+N% chance to be ..." lines bring (self_ailments)
        from builder.search import objective

        added = getattr(build, "added_stats", None)
        seen = {**build.call("stats", fields=sorted(set(boss_hits.SELF_BLEED_FIELDS) | set(objective.RECOVERY_INPUTS)
                                                    | set(boss_hits.SELF_AILMENT_FIELDS))),
                **(added() if callable(added) else {})}
        request = getattr(getattr(build, "ctx", None), "request", None)
        if getattr(request, "boss", None):
            seen["_boss"] = request.boss
        seen[boss_hits.SELF_AFFLICTED_FIELD] = build.afflicted_inc
        if boss_hits.self_ailment_chances([e["text"] for e in source_lines]):
            # (MECHC35, COV38's sweep: "+N% chance to be Poisoned / Ignited / Shocked", Coat of Red's pattern above)
            poison, burn, shocked, ail_notes = boss_hits.self_ailments([e["text"] for e in source_lines], seen)
            build.self_poison = poison  # (Life only, through Build.added_stats: objective.self_bleed_taken)
            # (MECHC37: the extra Ignite through Build.added_stats too: as PoB's Fire degen, objective.own_loss read it as
            # a steady loss and held Energy Shield / Life Recharge at 0 all fight, an OVERCOUNT of damage taken)
            build.boss_ignite += burn
            if shocked > 0:
                hand_mods = hand_mods + [models.raw_mod("DamageTaken", "INC", shocked, tag="SelfShockChance")]
            hand_notes = hand_notes + ail_notes
        if any(recovery_mod.REFLECT_POISON.match(t.strip()) for t in texts):
            # (MECHC37; COV40's routed OVERCOUNT) your main skill's Poisons reflected to you, Life only, as the Poison above
            reflected, reflect_notes = recovery_mod.reflected_poison(
                texts, build.call("stats", fields=list(recovery_mod.REFLECT_POISON_FIELDS)))
            build.self_poison += reflected
            hand_notes = hand_notes + reflect_notes
        config_in = build.call("getConfig")["input"]  # (PoB's Shocked box ticked: its Shock already counts all fight)
        on_you = boss_hits.boss_ailments_on_you(seen, bool(config_in.get("conditionShocked")
                                                           or config_in.get("conditionOnShockedGround")))
        build.boss_ignite += on_you["ignite"]
        # (MECHC38, the manager's ruling (i), 18 Nov 02:30) the floors read the boss's Shock IN FULL from 7% of the fight
        # (boss_hits.BOSS_SHOCK_FLOOR_KEY: one more part-time effect, switched to "in full" by the floors' recalculation)
        shock_taken, self.boss_shock_part_time = boss_hits.boss_shock_for_floors(
            on_you, getattr(self, "ledger_switch", {}).get(boss_hits.BOSS_SHOCK_FLOOR_KEY, True))
        if shock_taken > 0:
            hand_mods = hand_mods + [models.raw_mod("DamageTaken", "INC", shock_taken, tag="BossShock")]
        hand_notes = hand_notes + on_you["notes"]
        # (MECHC38) the Chills on you as action speed: the boss's Slow (an OVERCOUNT before), Sierran Inheritance's reversed
        # Chill (the manager's ruling (iii), R31-1)
        chill_seen = {**build.call("stats", fields=list(boss_hits.CHILL_FIELDS)), **seen}
        speed, chill_notes = boss_hits.boss_chill_on_you(chill_seen, texts, bool(
            config_in.get("conditionChilled") or config_in.get("conditionOnChilledGround")))
        if speed:
            hand_mods = hand_mods + [models.raw_mod("ActionSpeed", "INC", speed, tag="BossChill")]
        hand_notes = hand_notes + chill_notes
        if recovery_mod.has_divinity(texts):
            # (MECHC39, R31-3: Immaculate Adherence's Divinity pays every Mana / Life cost; the main skill held to what it
            # regenerates). The costs it pays are read without its own mods (they zero PoB's costs): one recalculation
            # without them and one back, only for a build with the line.
            cost_fields = list(recovery_mod.DIVINITY_COST_FIELDS)
            if any(m.get("tag") == recovery_mod.DIVINITY_TAG for m in self.mods or []):
                costs = build.call("setAssumptions", mods=[m for m in self.mods if m.get("tag") != recovery_mod.DIVINITY_TAG],
                                   fields=cost_fields)["stats"]
                build.call("setAssumptions", mods=self.mods, fields=[])
            else:
                costs = build.call("stats", fields=cost_fields)
            pool_es = number(build.call("stats", fields=list(recovery_mod.DIVINITY_FIELDS)).get("MaximumEnergyShield"))
            div_mods, div_rows, _ = recovery_mod.divinity(
                texts, pool_es, recovery_mod.corrupted_items(build.call("equipped").get("slots") or {}),
                number(costs.get("ManaPerSecondCost")), number(costs.get("LifePerSecondCost")), name)
            hand_mods, hand_notes = hand_mods + div_mods, hand_notes + div_rows
        if any(recovery_mod.VIGOUR_FIRST.match(t.strip()) for t in texts):  # (MECHC38, COV41: Emergent Vigour)
            vigour, vigour_row = recovery_mod.emergent_vigour(
                texts, build.call("stats", fields=list(recovery_mod.VIGOUR_FIELDS)))
            if vigour_row:
                if vigour > 0:
                    hand_mods = hand_mods + [models.raw_mod("EnergyShield", "BASE", vigour, tag="EmergentVigour")]
                hand_notes = hand_notes + [vigour_row]
        jade, jade_row = recovery_mod.jade_heritage(texts, number(seen.get("Life")), expiry=stats.get("_buff_expiry"))
        if jade_row:  # (MECHC37, PATCH_CHECK L1-3: Jade Heritage's 10 Jade; Encase in Jade's Guard a row only)
            hand_mods = hand_mods + [models.raw_mod("PhysicalDamageReduction", "BASE", jade, tag="JadeHeritage")]
            hand_notes = hand_notes + [jade_row]
        if any(recovery_mod.EVERLASTING.match(t.strip()) for t in texts):
            # (MECHC36, PATCH_CHECK MB-14: Everlasting Sacrifice from the build's Energy Shield flows; the bridge's
            # setEverlastingSacrifice cuts the hit pools, both under PoB's Condition:EverlastingSacrifice)
            from builder.search import objective

            added = getattr(build, "added_stats", None)
            seen = {**build.call("stats", fields=sorted(objective.RECOVERY_INPUTS)), **(added() if callable(added) else {})}
            request = getattr(getattr(build, "ctx", None), "request", None)
            if getattr(request, "boss", None):
                seen["_boss"] = request.boss
            up, held, how = recovery_mod.everlasting_sacrifice(seen, stats.get("_buff_expiry"))
            res = recovery_mod.EVERLASTING_RES * up
            hand_mods = hand_mods + [
                models.raw_mod("ElementalResistMax", "BASE", res, condition="EverlastingSacrifice"),
                models.raw_mod("ChaosResistMax", "BASE", res, condition="EverlastingSacrifice"),
                models.raw_mod("EverlastingEsLost", "BASE", (1 - held) * 100, condition="EverlastingSacrifice")]
            hand_notes = hand_notes + [{
                "label": "Everlasting Sacrifice*", "value": f"+{res:.1f}% max resistances; {held:.0%} of Energy Shield held",
                "basis": how + ("; PoB's box (+5% all fight) is never set and its pools count all the Energy Shield: the "
                                "maximum resistances at their uptime, the hit pools at the Energy Shield a hit meets"),
                "confidence": "MEDIUM (the refill reading) / HIGH (the line's words)",
                "play": (f"with it: +{res:.1f}% to all maximum resistances on average, the boss's hits meeting "
                         f"{held:.0%} of your Energy Shield; without it (another shield): no bonus, all of it")}]
        if any(p.search(t) for p in _zerphi_lines() for t in texts):
            # (MECHC36, H2-3: Zerphi's Genesis's charge transfers, counted inside flasks.Charges.of: the note only)
            from builder.search import flasks

            granted = flasks.Charges.of(build)
            hand_notes = hand_notes + [{
                "label": "Zerphi's Genesis: charges moved between charms and the Life Flask*",
                "value": f"+{granted.granted_life:.3f}/s to the Life Flask, +{granted.granted_charm:.3f}/s to each charm",
                "basis": granted.granted_how or "no Life Flask or charm equipped: nothing moves",
                "confidence": "MEDIUM-LOW (the split and the unscaled grant are the lower readings, UNVERIFIED)",
                "play": "use the charms and the Life Flask whenever they can be: each feeds the other (without the belt: "
                        "their own charges only)"}]
        jiquani_mods, jiquani_notes = models.jiquani_model([e["text"] for e in source_lines])  # (MECHC9, A12-2)
        hand_mods, hand_notes = hand_mods + jiquani_mods, hand_notes + jiquani_notes
        unbound_mods, unbound_notes = models.unbound_model([e["text"] for e in source_lines])  # (MECHC10)
        hand_mods, hand_notes = hand_mods + unbound_mods, hand_notes + unbound_notes
        main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or []) if name else set()
        echo_mods, echo_notes = models.repeat_echo_model([e["text"] for e in source_lines], main_types, forbidden, main)
        hand_mods, hand_notes = hand_mods + echo_mods, hand_notes + echo_notes
        parry_stun = (recovery_mod.parry_stun_of(build) if any(s.name == "Parry" for s in found)
                      else (0.0, ""))  # (MECH-C: Heavy Stun from parrying, MECHC30)
        refute_stun = (recovery_mod.refutation_stun_of(build) if any(s.name == "Refutation" for s in found)
                       else (0.0, ""))  # (MECH-C: Heavy Stun from Refutation's blocks, MECHC30)
        parry_mods, parry_notes = models.parry_model(found, {**stats, "_texts": texts}, main, bool(({name} | main_gems) & models.PARRY_CONSUMERS),
                                                     parry_stun, refute_stun)
        hand_mods, hand_notes = hand_mods + parry_mods, hand_notes + parry_notes
        if any(models.LIONEYE.search(t.strip()) for t in texts):  # (MECHB37: Lioneye's Glare's Repeats out of Presence)
            metres = models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(main_types, name or "")
            bow = "Bow" in set((build.ctx.gems.get(name) or {}).get("weaponTypes") or []) if name else False
            lion_mods, lion_notes = models.lioneye_model(texts, stats, main, metres, bow)
            hand_mods, hand_notes = hand_mods + lion_mods, hand_notes + lion_notes
        presses: dict[str, tuple[float, str]] = {}  # (item-granted skills the build presses: models.ITEM_PRESSES)
        if any(models.RAISED_BLIND.search(e["text"]) for e in source_lines):  # (Bastion of Light's shield taps, MECHC21)
            metres = models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(main_types, name or "")
            raised_texts, taps = [e["text"] for e in source_lines], models.raised_taps_on(build)
            raised_mods, raised_notes = models.raised_model(
                found, raised_texts, metres, main, taps, models.hits_per_second(stats),
                bool(({name} | main_gems) & models.PARRY_CONSUMERS))
            hand_mods, hand_notes = hand_mods + raised_mods, hand_notes + raised_notes
            plan = models.raised_plan(found, raised_texts, metres, taps)  # (its taps are presses: the button cap's)
            if plan.taps > 0:
                presses[models.RAISE_SHIELD] = (plan.taps, plan.how)
                # (MECHC34, MECHC30's leftover (c): the taps' blocked hits can Heavy Stun you)
                tap_stun, tap_how = recovery_mod.raised_tap_stun_of(build, plan.taps)
                if main is not None and main.name != models.RAISE_SHIELD and tap_stun > 0:
                    hand_mods = hand_mods + [models.line("1% more Attack and Cast Speed", -100 * tap_stun, skill=main.name)]
                if tap_how:
                    hand_notes = hand_notes + [{"label": "Heavy Stun on you from Raise Shield taps*",
                                                "value": f"{tap_stun:.1%} of the fight", "basis": tap_how,
                                                "confidence": "MEDIUM-LOW"}]
        setattr(build, models.ITEM_PRESSES, presses)
        if any(s.name == RUNIC_REPRIEVE for s in found) and any(
                "Runic Ward has been damaged Recently" in e["text"] for e in source_lines):
            # (MECHC35, MECHC34's next step: a build playing Reprieve into the boss's hits, ward_damaged_share, loses
            # each channel's wind-up and its Heavy Stuns from the main skill: an OVERCOUNT of DPS before)
            self.ward_damaged_share(skills)
            paid, windup = getattr(build, "reprieve_paid", (0.0, 0.0))
            stun, stun_how = recovery_mod.reprieve_stun_of(build, paid)
            busy = min(paid * windup + stun, 0.95)
            if main is not None and main.name != RUNIC_REPRIEVE and busy > 0:
                hand_mods = hand_mods + [models.line("1% more Attack and Cast Speed", -100 * busy, skill=main.name)]
            hand_notes = hand_notes + [{
                "label": "Runic Reprieve's channels and Heavy Stuns off the main skill*",
                "value": f"{busy:.1%} of the fight",
                "basis": (f"{paid:.2f} channels a second (ward_damaged_share's play), each at least its {windup:.2f} s "
                          f"wind-up ({paid * windup:.1%} of the fight; the hold to the hit not added: a lower bound)"
                          + (f"; {stun_how}" if stun_how else "; no Heavy Stun")),
                "confidence": "MEDIUM-LOW", "play": (f"channel Runic Reprieve into the boss's hits: {busy:.1%} of the "
                                                     "fight off your main skill (with it); without it: no Ward damage")}]
        if recovery_mod.stunned_recently_lines([e["text"] for e in source_lines]):
            # (MECHC35, MECHC34's find: PoB's Stunned Recently box never set; each line at its time-average)
            read = dict(build.call("stats", fields=list(recovery_mod.STUNNED_FIELDS)))
            request = getattr(getattr(build, "ctx", None), "request", None)
            if getattr(request, "boss", None):
                read["_boss"] = request.boss
            heavy_rate = recovery_mod.channel_stun_of(build, main.name if main is not None else None)[1]
            stun_mods, stun_how = recovery_mod.stunned_recently_mods([e["text"] for e in source_lines], read, heavy_rate)
            hand_mods = hand_mods + stun_mods
            hand_notes = hand_notes + [{"label": "Stunned Recently: its lines at the share of the fight*",
                                        "value": stun_how.split(":")[0], "basis": stun_how,
                                        "confidence": "MEDIUM-LOW (the boss's cadence and hit sizes LOW)"}]
        if any(recovery_mod.RAISED_BLOCK_TAKEN.match(e["text"].strip()) for e in source_lines):
            # (MECHC35, COV38's sweep: the Ulaman suffix's damage taken from Blocks made with the shield raised)
            raised = (1.0 if main is not None and main.name in recovery_mod.ACTIVE_BLOCK_CHANNELS else
                      min(1.0, presses[models.RAISE_SHIELD][0] * recovery_mod.RAISED_TAP_SECONDS)
                      if models.RAISE_SHIELD in presses else 0.0)
            block = models.number(build.call("stats", fields=["EffectiveBlockChance"]).get("EffectiveBlockChance")) / 100
            effect, effect_how = recovery_mod.raised_block_taken([e["text"] for e in source_lines], raised, block)
            if effect > 0:
                hand_mods = hand_mods + [models.raw_mod("BlockEffect", "BASE", effect, tag="RaisedBlockTaken")]
            hand_notes = hand_notes + [{"label": "Damage from Blocks with your shield raised (your item's downside)*",
                                        "value": f"{effect:.1f}% of each Blocked hit", "basis": effect_how,
                                        "confidence": "MEDIUM (the raised share from the build's own shield use)"}]
        roar_mods, roar_notes = models.roar_model(found, {**stats, "_texts": [e["text"] for e in source_lines]}, main)
        hand_mods, hand_notes = hand_mods + roar_mods, hand_notes + roar_notes
        rod_mods, rod_notes = models.rod_model(found, stats, main)
        hand_mods, hand_notes = hand_mods + rod_mods, hand_notes + rod_notes
        texts = [e["text"] for e in source_lines]
        if any(p.match(t.strip()) for t in texts for p in (models.CW_PRESENCE, models.CW_SPELL_CRIT, models.CW_PARRY,
                                                            models.CW_WARCRY, models.CW_MARK)):  # (MECH81, K3-1)
            cries = (0.0, "")
            if any(models.CW_WARCRY.match(t.strip()) for t in texts):
                cries = conditions_mod.event_rates(
                    build.call("groups")["groups"], build.ctx.gems, name or "", main_types, models.number(stats.get("Speed")),
                    lambda idx: build.call("useRates", indices=idx)["rates"], ["when you use a Warcry"],
                    models.press_choices(build)).get("Warcry", (0.0, ""))
            cw_metres = (models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10
                         or natural_distance(main_types, name or ""))
            cw_mods, cw_notes = models.critical_weakness_mods(
                models.critical_weakness_stacks(texts, found, stats, main, cw_metres, cries))
            hand_mods, hand_notes = hand_mods + cw_mods, hand_notes + cw_notes
        if any(p.match(t.strip()) for t in texts for p in (models.AGGRAVATE_HIT, models.AGGRAVATE_CRIT,
                                                            models.AGGRAVATE_OLDER, models.AGGRAVATE_EMPOWERED,
                                                            models.BEETLEBITE)):
            # (MECH66: Aggravated Bleeding at the main skill's own hits, crits and Bleed duration)
            agg_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or []) if name else set()
            moving = bool(build.call("getConfig")["input"].get("conditionEnemyMoving"))
            empowered = self.empowered_uses(stats, None) if any(
                models.AGGRAVATE_EMPOWERED.match(t.strip()) for t in texts) else None
            entries = None
            if any(models.BEETLEBITE.match(t.strip()) for t in texts):  # (MECH82, R26-34: re-entries from your rolls)
                agg_metres = (models.number(build.call("getConfig")["input"].get("enemyDistance")) / 10
                              or natural_distance(agg_types, name or ""))
                entries = models.presence_reentries(texts, stats, agg_metres)
            agg_mods, agg_notes = models.aggravate_model(
                texts, {**stats, **build.call("stats", fields=["BleedDuration", "BleedChancePerHit"])}, agg_types,
                moving, empowered, entries)  # (MECH80: Bleeds a second, R26-14)
            hand_mods, hand_notes = hand_mods + agg_mods, hand_notes + agg_notes
        passive_mods, passive_notes = passive_models.build_models(build, inputs, texts, found, main, supports)  # (COV-B)
        hand_mods, hand_notes = hand_mods + passive_mods, hand_notes + passive_notes
        if any(models.RUN_AND_GUN.search(t.strip()) for t in texts):  # (Run and Gun: shotgun and sequence-fired skills)
            main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or [])
            standing = bool(build.call("getConfig")["input"].get("conditionStationary"))
            gun_mods, gun_notes = models.run_and_gun_model(texts, stats, main, main_types, standing)
            hand_mods, hand_notes = hand_mods + gun_mods, hand_notes + gun_notes
        early_mods, early_notes = models.hazard_early_model(found, stats, forbidden, self.hitters(found))  # (MECH-B)
        hand_mods, hand_notes = hand_mods + early_mods, hand_notes + early_notes
        cascade_mods, cascade_notes = models.spell_cascade_model(main, stats, *bosses.hit_radius(stats))  # (MECHB36)
        hand_mods, hand_notes = hand_mods + cascade_mods, hand_notes + cascade_notes
        count_mods, count_notes = models.hazard_count_model(main, stats)  # (MECHB33: a use's hazards on the boss)
        hand_mods, hand_notes = hand_mods + count_mods, hand_notes + count_notes
        comp_mods, comp_notes = self.companions(skills, [e["text"] for e in source_lines], main)  # (MECHB33: the limit)
        hand_mods, hand_notes = hand_mods + comp_mods, hand_notes + comp_notes
        zarokh_mods, zarokh_notes = models.zarokh_model(main, supports.get(main.group, []) if main else [], stats)  # (MECHB33)
        hand_mods, hand_notes = hand_mods + zarokh_mods, hand_notes + zarokh_notes
        hand_notes = hand_notes + models.channel_windup_note(main)  # (MECH-B: a channel's wind-up, labelled, not counted)
        if main is not None and main.name == models.ORB_OF_STORMS and main.group is not None:  # (MECH-B: its recast
            # bolt, labelled; MECHB29: its bolts, frequency and duration from its own useRates row)
            orb_rows = build.call("useRates", indices=[main.group])["rates"]
            hand_notes = hand_notes + models.orb_of_storms_note(main, stats, orb_rows[0] if orb_rows else None)
        hand_notes = hand_notes + models.coursing_current_note(found)  # (MECH-B: 0 on a lone boss, as PoB scores it)
        if any(models.SHRAPNEL_SKILL in names for names in supports.values()):  # (MECHB33, GEM14: wired; 0 with its
            # reason until the presets carry add Life and a Pin share on the adds, never a flat guess)
            hand_notes = hand_notes + models.bone_shrapnel_model(
                models.number(stats.get(bosses.KILLS_STAT)), None, None, None, 1.0)[1]
        danse_mods, danse_notes = models.danse_macabre_model(found, supports)  # (MECH-B: Danse Macabre's second skeleton)
        hand_mods, hand_notes = hand_mods + danse_mods, hand_notes + danse_notes
        hand_notes = hand_notes + models.offering_placement_notes(found)  # (MECHB35: R26-38, labelled by placement)
        warcries = {s.name for s in found if "Warcry" in ((build.ctx.gems.get(s.name) or {}).get("skillTypes") or [])}
        if warcries:  # (MECH-B: Echoing Cry's Echoes; COV-E's support_part_scale "warcry_echo")
            echo_mods, echo_notes = models.warcry_echo_model(found, supports, warcries, models.support_part_scale)
            hand_mods, hand_notes = hand_mods + echo_mods, hand_notes + echo_notes
        persistent = {s.name for s in found if "Persistent" in ((build.ctx.gems.get(s.name) or {}).get("skillTypes") or [])}
        diff_mods, diff_notes = models.different_skills_counts(found, main, persistent)  # (MECH-B: different skills per 8 s)
        hand_mods, hand_notes = hand_mods + diff_mods, hand_notes + diff_notes
        if main is not None and main.name == models.RAPID_SHOT:  # (MECH-B: Rapid Shot's Heat lockout)
            heat_mods, heat_notes = models.heat_model(main, stats, next((s for s in found if s.name == models.RAPID_SHOT),
                                                                         None), self.heat_spenders(found))
            hand_mods, hand_notes = hand_mods + heat_mods, hand_notes + heat_notes
        if main is not None and main.name in models.HEAT_BONUS:  # (MECHB35: the main spender's Heat from a pressed Rapid Shot)
            heat, heat_how = self.heat_made(found, main)
            spend_mods, spend_notes = models.heat_consumed_model(main, stats, heat, heat_how)
            hand_mods, hand_notes = hand_mods + spend_mods, hand_notes + spend_notes
        hand_notes = hand_notes + models.guided_shrine_notes(texts)  # (MECHB36: Guiding Palm's shrines, a labelled 0)
        if main is not None:  # (returning projectiles' second boss hit: MECH-B, models.return_model)
            gem = build.ctx.gems.get(name) or {}
            back_mods, back_notes = models.return_model(
                texts, stats, main, set(gem.get("skillTypes") or []), group["gems"] if group else [],
                set(gem.get("weaponTypes") or []))
            hand_mods, hand_notes = hand_mods + back_mods, hand_notes + back_notes
            if any(models.CADIRO_ARROWS.search(t.strip()) for t in texts):  # (MECHB22: Cadiro's Gambit's Diamond Arrows)
                cadiro_mods, cadiro_notes = models.cadiro_model(
                    texts, {**stats, **build.call("stats", fields=models.CADIRO_FIELDS)}, main,
                    set(gem.get("skillTypes") or []), set(gem.get("weaponTypes") or []),
                    sum(m["scale"] for m in back_mods if m.get("line") == "1% more Damage"))
                hand_mods, hand_notes = hand_mods + cadiro_mods, hand_notes + cadiro_notes
            # (MECH-B: Aftershock chances summed and capped, and their ailment applications; models.aftershock_model)
            shock_mods, shock_notes = models.aftershock_model(
                texts, stats, main, set(gem.get("skillTypes") or []), group["gems"] if group else [],
                set(gem.get("weaponTypes") or []), models.cast_by_totem(found, main), forbidden)
            hand_mods, hand_notes = hand_mods + shock_mods, hand_notes + shock_notes
        if main is not None and group and main.name in models.MULTI_SET_SKILLS:  # (MECH-B: Rolling Slam's two slams)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            slam_mods, slam_notes = models.multi_set_model(main.name, main.stat_set, sets)
            hand_mods, hand_notes = hand_mods + slam_mods, hand_notes + slam_notes
        if main is not None and group and main.name in models.COMBO_SEQUENCES:  # (GEM11: a combo's step sets)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            up, up_how = models.ailment_up_of(build)  # (GEM12: Primal Strikes' spirits need a Shocked boss)
            combo_mods, combo_notes = models.combo_sequence_model(main.name, main.stat_set, sets, forbidden,
                                                                  up.get("Shock") if up else None, up_how,
                                                                  bosses.hit_radius(stats)[0])  # (MECHB35, R28-1)
            hand_mods, hand_notes = hand_mods + combo_mods, hand_notes + combo_notes
        if main is not None and group and main.name == models.FROST_DARTS:  # (MECHB35: Frost Darts' whole cast)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            up, _ = models.ailment_up_of(build)
            ground, ground_how = self.chilled_ground_share()
            frozen = up.get("Freeze", 0.0)
            chilled = 1 - (1 - ground) * (1 - frozen)
            infused, infused_how = self.infusion_share(
                "Cold", {f"conditionEnemy{models.DISCHARGE_UPTIMES[k]}": v for k, v in up.items()})
            frost_mods, frost_notes = models.frost_darts_model(
                main.name, main.stat_set, sets, models.number(stats.get("ProjectileCount")), chilled,
                f"Chilled Ground {ground:.0%}: {ground_how}; Frozen {frozen:.0%}", infused, infused_how,
                bosses.hit_radius(stats)[0], forbidden)
            hand_mods, hand_notes = hand_mods + frost_mods, hand_notes + frost_notes
        if main is not None and group and main.name in models.WHIRL_SKILLS:  # (GEM12: Whirlwind stages, then collapse)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            whirl_mods, whirl_notes = models.whirlwind_stage_model(main, sets, forbidden)
            hand_mods, hand_notes = hand_mods + whirl_mods, hand_notes + whirl_notes
        if main is not None and group and main.name in models.METEOR_SKILLS:  # (GEM12: the meteors' rate and mix)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            buff, up, up_how = models.METEOR_BUFF.get(main.name), None, ""
            if buff is not None:  # (its uses a second from its Glory, as press_caps reads it for a side group)
                uses, up_how = self.glory_caps().get(buff, (0.0, f"{buff}: its Glory wasn't read"))
                up = uses * models.GLORY_INSTANCE.get(buff, 0.0)
                up_how = f"{uses:.3f} uses a second x its {models.GLORY_INSTANCE.get(buff, 0.0):g} s; {up_how}"
            meteor_mods, meteor_notes = models.meteor_rate_model(main, sets, up, up_how, forbidden)
            hand_mods, hand_notes = hand_mods + meteor_mods, hand_notes + meteor_notes
        if main is not None and group and main.name == models.FIRESTORM:  # (GEM16: its Infusions' bolts and bonuses)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            duration = number(build.call("stats", fields=["Duration"]).get("Duration"))
            up, _ = models.ailment_up_of(build)  # (Snap's Remnants: the ailments on the boss, as infusion_share reads them)
            ailments = {var: up.get(name, 0.0) for var, name in (("conditionEnemyIgnited", "Ignite"),
                        ("conditionEnemyShocked", "Shock"), ("conditionEnemyFrozen", "Freeze"))}
            casts = 1.0 / duration if duration > 0 else None  # (one storm at a time, recast as it ends)
            shares = {e: self.infusion_share(e, ailments, casts) for e in ("Fire", "Cold", "Lightning")}
            storm_mods, storm_notes = models.firestorm_infusion_model(main, main.stat_set, sets, shares, forbidden)
            hand_mods, hand_notes = hand_mods + storm_mods, hand_notes + storm_notes
        if main is not None and group and main.name == models.FALLING_THUNDER:  # (GEM16: projectiles need Power Charges)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            charges = number(build.call("stats", fields=["RemovablePowerCharges"]).get("RemovablePowerCharges"))
            thunder_mods, thunder_notes = models.falling_thunder_model(main, main.stat_set, sets, charges, forbidden)
            hand_mods, hand_notes = hand_mods + thunder_mods, hand_notes + thunder_notes
        if main is not None and group and main.name == models.ICESTORM:  # (GEM17: improved bolts need Chill / Freeze)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            up, up_how = models.ailment_up_of(build)
            ground, ground_how = self.chilled_ground_share()
            frozen = up.get("Freeze", 0.0)
            ice_mods, ice_notes = models.icestorm_model(
                main, main.stat_set, sets, number(stats.get("Speed")), ground, frozen, models.power_of(stats),
                f"Chilled Ground {ground:.0%}: {ground_how}; Frozen {frozen:.0%}: {up_how}", forbidden)
            hand_mods, hand_notes = hand_mods + ice_mods, hand_notes + ice_notes
        if main is not None and group and main.name in models.GATED_SETS:  # (GEM17: Enraged / Empowered need a resource)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            gate_share, gate_how = self.gated_set_share(main.name, texts, main_types)
            gate_mods, gate_notes = models.gated_set_model(main, main.stat_set, sets, gate_share, gate_how, forbidden)
            hand_mods, hand_notes = hand_mods + gate_mods, hand_notes + gate_notes
        if main is not None and group and main.name in models.AILMENT_SETS:  # (GEM17: Snipe / Sundering consume ailments)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            up, up_how = models.ailment_up_of(build)
            ail_mods, ail_notes = models.ailment_set_model(main, main.stat_set, sets, up, up_how, forbidden)
            hand_mods, hand_notes = hand_mods + ail_mods, hand_notes + ail_notes
        if main is not None and group and main.name in models.DETONATED_SETS:  # (GEM17: detonations need a Detonator)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            if main.name in models.DETONATED_BY:  # (GEM19: Earthshatter's spikes shatter on a Warcry, not a Detonator)
                event = models.DETONATED_BY[main.name][0]
                det_rate, det_how = conditions_mod.event_rates(
                    build.call("groups")["groups"], build.ctx.gems, name or "", main_types, number(stats.get("Speed")),
                    lambda idx: build.call("useRates", indices=idx)["rates"], ["when you use a Warcry"],
                    models.press_choices(build)).get(event, (0.0, f"no {event} in the build"))
                det_how = det_how or f"no {event} in the build"
                # (patch 0.3.0, no later entry: "Detonation now also triggers existing spikes": Detonators too)
                bomb_rate, bomb_how = self.detonator_rate(group["index"])
                cries_only = models.detonated_share(main.name, number(stats.get("Speed")), det_rate)
                # (RESEARCH30 R30-2 (f): whether Detonations still shatter the spikes is unsettled; Detonators stay
                # counted, MEDIUM-LOW, and the description's Warcries-only reading is a row)
                det_rate, det_how = det_rate + bomb_rate, (
                    f"{event}s: {det_how}; {bomb_how} (Detonators: patch 0.3.0, MEDIUM-LOW, RESEARCH30 R30-2 (f) "
                    f"unsettled; with {event}s only, the description's reading: {cries_only:.0%} of spikes)")
            else:
                det_rate, det_how = self.detonator_rate(group["index"])
            per_spike = 0.0
            if main.name == "Earthshatter":  # (GEM20, R30-5 (2): its quality's more Damage for each Spike standing)
                got = next((x.get("levelStats") or {} for x in build.call(
                    "activeSkills", levelStats=[main.name])["skills"] if x["name"] == main.name), {})
                per_spike = number(got.get(models.EARTHSHATTER_SPIKE_STAT))
            det_mods, det_notes = models.detonated_set_model(main, main.stat_set, sets, number(stats.get("Speed")),
                                                             det_rate, det_how, forbidden, per_spike)
            hand_mods, hand_notes = hand_mods + det_mods, hand_notes + det_notes
        if main is not None and main.name == models.EYE:  # (GEM19: its first hit a use, 900% more, never read by PoB)
            eye_stats = build.call("stats", fields=["AverageDamage", "Speed", "TotalDPS"])
            eye_own = sum(number(m.get("value")) for m in self.mods or []
                          if m.get("tag") == "EyeOfWinter" and m.get("name") == "DPS")
            eye_mods, eye_notes = models.eye_of_winter_model(main, eye_stats, eye_own, forbidden)
            hand_mods, hand_notes = hand_mods + eye_mods, hand_notes + eye_notes
        if main is not None and main.name == models.BONESTORM:  # (GEM19: a full channel of spikes, not PoB's one)
            bs_mods, bs_notes = models.bonestorm_model(main, forbidden)
            hand_mods, hand_notes = hand_mods + bs_mods, hand_notes + bs_notes
        if main is not None and main.name == models.FLAMEBLAST:  # (GEM18: its stages and its channel + cooldown cycle)
            fb_mods, fb_notes = models.flameblast_model(main, stats, forbidden)
            hand_mods, hand_notes = hand_mods + fb_mods, hand_notes + fb_notes
        staged = {models.SLAM: models.supercharged_slam_model, models.DETONATING: models.detonating_arrow_model}
        if main is not None and group and main.name in staged:  # (GEM19: stages PoB leaves at 1, and the end animation)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            line = f"Multiplier:{main.name.replace(' ', '')}Stage"
            now = 1 + sum(number(m.get("value")) for m in self.mods or [] if m.get("name") == line)  # (PoB's stage now)
            stage_stats = {**stats, **build.call("stats", fields=["HitSpeed", "Time"])}
            stage_mods, stage_notes = staged[main.name](main, main.stat_set, sets, stage_stats, int(round(now)), forbidden)
            hand_mods, hand_notes = hand_mods + stage_mods, hand_notes + stage_notes
        if main is not None and group and main.name in models.STUN_SETS:  # (GEM18: a shockwave only on a Heavy Stun)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            primed, primed_how = self.stun_primers(group["index"])
            stun_mods, stun_notes = models.stun_set_model(main, main.stat_set, sets, stats, primed, primed_how, forbidden)
            hand_mods, hand_notes = hand_mods + stun_mods, hand_notes + stun_notes
        consume_gains: dict[str, tuple[float, str]] = {}  # (MECHC38: charges granted on a consume, conditions' reader)
        if main is not None and group and main.name in models.SHOCK_SETS:  # (GEM19: a part only on a Shocked boss)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            shock, shock_how = self.shock_set_share(group["index"], main.name, models.SHOCK_SETS[main.name][2])
            sh_mods, sh_notes = models.shock_set_model(main, main.stat_set, sets, shock, shock_how, forbidden)
            consume_gains.update(conditions_mod.consume_gain(build, main.name, shock, "of its hits Consume a Shock"))
            hand_mods, hand_notes = hand_mods + sh_mods, hand_notes + sh_notes
        if main is not None and group and main.name in models.PARRIED_SETS:  # (GEM19: a part only on a Parried boss)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            plan = models.parry_plan(found, stats, main.attack, True, parry_stun, refute_stun)  # (as parry_model's)
            pa_mods, pa_notes = models.shock_set_model(main, main.stat_set, sets, plan.box,
                                                       plan.how or "no Parry or Refutation in the build", forbidden)
            consume_gains.update(conditions_mod.consume_gain(build, main.name, plan.box, "of its hits Consume the Parried Debuff"))
            hand_mods, hand_notes = hand_mods + pa_mods, hand_notes + pa_notes
        if main is not None and group and main.name == models.GAS_ARROW:  # (GEM20: clouds explode on Ignite / Detonation)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            gas, gas_how, gas_poisons = self.gas_arrow_share(group["index"], main.stat_set)
            gas_mods, gas_notes = models.gas_arrow_model(main, main.stat_set, sets, gas, gas_how, forbidden, gas_poisons)
            hand_mods, hand_notes = hand_mods + gas_mods, hand_notes + gas_notes
        if main is not None and group and main.name == models.FROZEN_LOCUS:  # (GEM20: an explosion when it breaks)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            breaks, breaks_how = self.locus_breaks(group["index"], main.name)
            boss, boss_how = self.boss_hit_rate()
            fl_mods, fl_notes = models.frozen_locus_model(
                main, main.stat_set, sets, number(stats.get("Speed")), breaks, boss,
                f"{breaks_how}; {boss_how}", forbidden)
            hand_mods, hand_notes = hand_mods + fl_mods, hand_notes + fl_notes
        if main is not None and group and main.name == models.MAGNETIC_SALVO:  # (GEM20: explosions need lingering arrows)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            arrows, row_arrows, arrows_how, feeder = self.lingering_arrows(group["index"])
            missiles = number(build.call("stats", fields=["ProjectileCount"]).get("ProjectileCount")) \
                or models.MAGNETIC_SALVO_MISSILES
            ms_mods, ms_notes = models.magnetic_salvo_model(main, main.stat_set, sets, number(stats.get("Speed")),
                                                            missiles, arrows, row_arrows, arrows_how, feeder,
                                                            forbidden)
            hand_mods, hand_notes = hand_mods + ms_mods, hand_notes + ms_notes
        build.consume_gains = consume_gains
        self.soul_mantle_part_time = 0.0
        if any(recovery_mod.SOUL_MANTLE.match(t.strip()) for t in texts):
            # (MECHC38, R31-2: Soul Mantle's curses at the main skill's totem deaths; the floors read them in full)
            summoner = next((s for s in found if main is not None and s.group == main.group and s.totem_limit > 0), None)
            if summoner is not None:
                others = {s.name for s in found if s.totem_limit > 0 and s.group != main.group}
                totems = max(1.0, summoner.totem_limit - len(others))
                lasts = number(stats.get("TotemDuration"))
                deaths = totems / lasts if lasts > 0 else 0.0
                deaths_how = (f"{totems:g} {summoner.name} totems up, each lasting PoB's {lasts:.2f} s, re-placed as it "
                              "expires" if lasts > 0 else f"{summoner.name}: PoB gives no totem duration (0)")
            else:
                deaths, deaths_how = 0.0, "the main skill isn't a totem's (other totem skills not counted)"
            soul_mods, self.soul_mantle_part_time, soul_rows = recovery_mod.soul_mantle(
                texts, deaths, deaths_how,
                full=getattr(self, "ledger_switch", {}).get(recovery_mod.SOUL_MANTLE_FLOOR_KEY, True) is False)
            hand_mods, hand_notes = hand_mods + soul_mods, hand_notes + soul_rows
        if main is not None and group and main.name == models.SHIELD_WALL:  # (GEM20: shattered by Slams, more damage)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            own, own_how = self.wall_shatters(group["index"])
            boss, boss_how = self.boss_hit_rate()
            got = next((x.get("levelStats") or {} for x in build.call(
                "activeSkills", levelStats=[models.SHIELD_WALL])["skills"] if x["name"] == models.SHIELD_WALL), {})
            more = number(got.get(models.SHIELD_WALL_STAT)) or 100.0
            sw_mods, sw_notes = models.shield_wall_model(main, main.stat_set, sets, number(stats.get("Speed")), own,
                                                         boss, more, f"{own_how}; {boss_how}", forbidden)
            hand_mods, hand_notes = hand_mods + sw_mods, hand_notes + sw_notes
        if main is not None and main.name == models.ENERVATING_NOVA:  # (GEM20: its damage by the boss's distance)
            radii = build.call("stats", fields=["AreaOfEffectRadius", "AreaOfEffectRadiusSecondary"])
            got = next((x.get("levelStats") or {} for x in build.call(
                "activeSkills", levelStats=[main.name])["skills"] if x["name"] == main.name), {})
            configured = number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or None
            nova_mods, nova_notes = models.enervating_nova_model(
                main, number(radii.get("AreaOfEffectRadiusSecondary")) / 10, number(radii.get("AreaOfEffectRadius")) / 10,
                number(got.get(models.ENERVATING_NOVA_STAT)), configured, forbidden)
            hand_mods, hand_notes = hand_mods + nova_mods, hand_notes + nova_notes
        if main is not None and group and main.name == models.SHATTERING_SPITE:  # (GEM20: its lances, 20% more each)
            cap = max((float(m.group(1)) for t in texts if (m := models.SPITE_LANCE_LINE.match(t.strip()))), default=0.0)
            hits, hits_how = self.spear_hits(group["index"]) if cap > 0 else (0.0, "no Bloodstone Lance line")
            got = next((x.get("levelStats") or {} for x in build.call(
                "activeSkills", levelStats=[main.name])["skills"] if x["name"] == main.name), {})
            sp_mods, sp_notes = models.shattering_spite_model(
                main, number(stats.get("Speed")), hits, cap, number(got.get(models.SPITE_STAT)) or 20.0, hits_how,
                forbidden)
            hand_mods, hand_notes = hand_mods + sp_mods, hand_notes + sp_notes
        if main is not None and group and main.name == models.GALVANIC_FIELD:  # (GEM20: its Orb after your Shock)
            shocks, shocks_how = self.other_shocks(group["index"])
            gf_mods, gf_notes = models.galvanic_field_model(main, main.stat_set, number(stats.get("Speed")), shocks,
                                                            shocks_how, forbidden)
            hand_mods, hand_notes = hand_mods + gf_mods, hand_notes + gf_notes
        if main is not None and group and main.name == models.HERALD_OF_BLOOD:  # (GEM20: an explosion only on a kill)
            from builder.search import bosses
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            request = getattr(getattr(build, "ctx", None), "request", None)
            kills, kills_how = bosses.kills_per_second(bosses.of({"_boss": getattr(request, "boss", None)}).name)
            hb_mods, hb_notes = models.herald_of_blood_model(main, main.stat_set, sets, kills, kills_how, forbidden)
            hand_mods, hand_notes = hand_mods + hb_mods, hand_notes + hb_notes
        side_mods, side_notes = self.multi_set_side(main)  # (MECH-B, MECHB16: Rolling Slam in a Full DPS group)
        hand_mods, hand_notes = hand_mods + side_mods, hand_notes + side_notes
        cap_mods, cap_notes = self.main_cap(group, name)  # (MECH-B: the press hook on the main skill, MECHB5)
        hand_mods, hand_notes = hand_mods + cap_mods, hand_notes + cap_notes
        spore_mods, spore_notes = self.pustule_cap(found)  # (MECH-B: Poison Spores' one Pustule a fuse, MECHB10)
        hand_mods, hand_notes = hand_mods + spore_mods, hand_notes + spore_notes
        tornado_mods, tornado_notes = self.tornado_copies(found, main, stats)  # (MECH-B, MECHB17: Tornado Shot's copies)
        hand_mods, hand_notes = hand_mods + tornado_mods, hand_notes + tornado_notes
        if main is not None and main.name == models.FREEZING_SALVO:  # (MECH-B, MECHB18: its Seals and extra projectiles)
            raw = build.call("activeSkills", levelStats=[models.FREEZING_SALVO])["skills"]
            level = next((s.get("levelStats") or {} for s in raw if s["name"] == models.FREEZING_SALVO and s.get("levelStats")), {})
            salvo_mods, salvo_notes = models.freezing_salvo_model(
                main, {**stats, **build.call("stats", fields=["Speed", "ProjectileCount"])},
                [e["text"] for e in source_lines], level)
            hand_mods, hand_notes = hand_mods + salvo_mods, hand_notes + salvo_notes
        mirage_mods, mirage_notes, mirage_groups = models.mirage_model(
            found, main, {g["name"]: (g["level"], g.get("quality") or 0) for grp in build.groups() for g in grp["gems"]
                          if g.get("level")}, rolling=any(s.name == "Cast on Dodge" for s in found))
        hand_mods, hand_notes = hand_mods + mirage_mods, hand_notes + mirage_notes
        hollow_mods, hollow_notes = models.hollow_model(found, stats, [e["text"] for e in source_lines], main)
        hand_mods, hand_notes = hand_mods + hollow_mods, hand_notes + hollow_notes
        pulse, pulse_notes = models.maelstrom_model(source_lines, stats) if models.MAELSTROM_ENTRY not in forbidden else (0.0, [])
        hand_mods += models.maelstrom_inputs(source_lines, pulse) if pulse_notes else []
        hunt, hunt_mods, hunt_notes = eternal_hunt(build, main)  # (MECHB20: the Azmerian Wolf's Command, an added source)
        hand_mods, hand_notes = hand_mods + hunt_mods, hand_notes + hunt_notes
        # separately computed damage (Melting Maelstrom's pulses, Eternal Hunt: models.added_input); what-ifs get their own
        # sum from the bridge's LedgerAddedDPS; set_added (SEARCH14) keeps each source for the result's list
        thorn, thorn_notes = thorns(build, main, stats, [e["text"] for e in source_lines])  # (MECHB22)
        hand_mods += [models.added_input("Thorns", thorn)] if thorn > 0 else []
        hand_notes += thorn_notes
        bramble_notes = brambles(build, main)  # (MECHB24: Brambleslam's Brambles at the slams' rate)
        hand_notes += bramble_notes
        for note in thorn_notes + bramble_notes:  # (MECHB23: PoB's Barbs entries, scored at a flat 1 use a second, out of Full DPS)
            build.objective.entry_scales.update(note.get("full_dps") or {})
        riven, riven_notes = riven_armour(build, main)  # (GEM3's model; MECH69's Armour Break cycle gives its uptime)
        hand_mods += [models.added_input("RivenArmour", riven)] if riven > 0 else []
        hand_notes += riven_notes
        discharge, discharge_notes = elemental_discharge(build, main)  # (GEM5's model; MECH69's ailment uptimes)
        hand_mods += [models.added_input("ElementalDischarge", discharge)] if discharge > 0 else []
        hand_notes += discharge_notes
        stance, stance_notes = broken_stance(build, main)  # (GEM5's model; MECH-A's build.dazed_up, 0 until published)
        hand_mods += [models.added_input("BrokenStance", stance)] if stance > 0 else []
        hand_notes += stance_notes
        pyre, pyre_notes = grisly_pyre(build, main)  # (MECH71: His Winnowing Flame's Grisly Pyres, PoB holds no damage)
        hand_mods += [models.added_input("GrislyPyre", pyre)] if pyre > 0 else []
        hand_notes += pyre_notes
        shout, shout_notes = battershout(build)  # (MECH72: Battershout's explosions, PoB holds no damage)
        hand_mods += [models.added_input("Battershout", shout)] if shout > 0 else []
        hand_notes += shout_notes
        palm, palm_notes = crackling_palm(build)  # (MECH77: Crackling Palm's bolts, PoB scores them 0)
        hand_mods += [models.added_input("CracklingPalm", palm)] if palm > 0 else []
        hand_notes += palm_notes
        blood, blood_notes = blood_hunt(build, main, [e["text"] for e in source_lines])  # (MECH75: Blood Loss explosions)
        hand_mods += [models.added_input("BloodHunt", blood)] if blood > 0 else []
        hand_notes += blood_notes
        impaled, impaled_others, impale_mods, impale_notes = impale(build, main, [e["text"] for e in source_lines])  # (MECH76)
        hand_mods += impale_mods + ([models.added_input("ImpaleOthers", impaled_others)] if impaled_others > 0 else [])
        hand_notes += impale_notes
        spirits, spirit_notes = ancestral_spirits(build, main)  # (MECHB29: Warbringer's Ancestral Spirits, PoB 0 / one)
        hand_mods += [models.added_input("AncestralSpirits", spirits)] if spirits > 0 else []
        hand_notes += spirit_notes
        for note in spirit_notes:  # (PoB's own one-spirit entry, where its group is in Full DPS, out)
            build.objective.entry_scales.update(note.get("full_dps") or {})
        puppet_mods, puppet_notes = models.puppet_master_model(found, [e["text"] for e in source_lines])  # (MECHB30)
        hand_mods, hand_notes = hand_mods + puppet_mods, hand_notes + puppet_notes
        barrage_mods, barrage_notes = barrage(build, main, found)  # (MECHB30: PoB's +2% for one empowered attack a cooldown)
        hand_mods, hand_notes = hand_mods + barrage_mods, hand_notes + barrage_notes
        press_mods, press_notes = empowered_presses(build, main)  # (MECHB32: Mantra, Unleash; PoB 0)
        hand_mods, hand_notes = hand_mods + press_mods, hand_notes + press_notes
        frost, ita_mods, ita_notes = ice_tipped(build, main)  # (MECHB32: Ice-Tipped Arrows' Empowered attacks; PoB 0)
        hand_mods += ita_mods + ([models.added_input("IceTippedFragments", frost)] if frost > 0 else [])
        hand_notes += ita_notes
        for note in ita_notes:  # (PoB's own Ice Fragments entry, where its group is in Full DPS, out)
            build.objective.entry_scales.update(note.get("full_dps") or {})
        legion, legion_notes = infernal_legion(build, stats)  # (MECHB30, H1-7: PoB rates no boss Ignite from it)
        hand_mods += [models.added_input("InfernalLegion", legion)] if legion > 0 else []
        hand_notes += legion_notes
        oil, oil_notes = (models.oil_ignite_model(  # (MECH82, G4-4: lit Oil Ground's Ignite; PoB scores it 0)
            build.oil_ignite, number(build.call("getConfig")["input"].get("enemyFireResist")),
            number(stats.get("IgniteDPS"))) if getattr(build, "oil_ignite", None) else (0.0, []))
        hand_mods += [models.added_input("OilGround", oil)] if oil > 0 else []
        hand_notes += oil_notes
        burn, burn_notes = (models.ground_life_ignite_model(  # (MECH83: Beira's Anguish / Birth of Fury; PoB 0)
            build.ground_life_ignite, number(build.call("getConfig")["input"].get("enemyFireResist")),
            number(stats.get("IgniteDPS"))) if getattr(build, "ground_life_ignite", None) else (0.0, []))
        hand_mods += [models.added_input("IgnitedGroundIgnite", burn)] if burn > 0 else []
        hand_notes += burn_notes
        djinn, djinn_mods, djinn_notes = djinn_commands(build, main)  # (MECH83: the Disciple's djinn Commands; PoB 0)
        hand_mods, hand_notes = hand_mods + djinn_mods, hand_notes + djinn_notes
        briar, briar_notes = briarpatch(build, main, found)  # (MECHB31: Briarpatch's creation hit, PoB 0)
        hand_mods += [models.added_input("Briarpatch", briar)] if briar > 0 else []
        hand_notes += briar_notes
        tail_mods, tail_notes = models.tailwind_model([e["text"] for e in source_lines], stats, main)  # (MECHB30, MB-3)
        hand_mods, hand_notes = hand_mods + tail_mods, hand_notes + tail_notes
        eldritch_mods, eldritch_notes = models.eldritch_empowerment_model([e["text"] for e in source_lines], stats, main)
        hand_mods, hand_notes = hand_mods + eldritch_mods, hand_notes + eldritch_notes  # (MECHB30, MB-1: PoB's box unset)
        agony, agony_rate, agony_mods, agony_notes = inevitable_agony(build, main)  # (MECHB30: PoB scores it 0)
        hand_mods, hand_notes = hand_mods + agony_mods, hand_notes + agony_notes
        if agony_rate > 0:  # (a press: the button cap counts it, as Bastion of Light's taps: models.ITEM_PRESSES)
            presses = getattr(build, models.ITEM_PRESSES, None)
            if isinstance(presses, dict):
                presses[models.INEVITABLE_AGONY] = (agony_rate, agony_notes[0]["play"])
        build.objective.set_added({"Melting Maelstrom": pulse, "Eternal Hunt": hunt, "Thorns": thorn, "Riven Armour": riven,
                                   "Elemental Discharge": discharge, "Broken Stance": stance, "Grisly Pyre": pyre,
                                   "Battershout": shout, "Crackling Palm": palm, "Blood Hunt": blood, "Ancestral Spirits": spirits,
                                   "Inevitable Agony": agony, "Impale": impaled + impaled_others, "Infernal Legion": legion,
                                   "Briarpatch": briar, "Ice-Tipped Fragments": frost, "Oil Ground": oil,
                                   "Ignited Ground's Ignite": burn, "Djinn Commands": djinn})
        # A model whose effect follows the build's own numbers and that the bridge can't work out again per calculation
        # would make every item what-if a real trial (Build.item_deltas). All of today's are worked out in the bridge
        # (models.derived): uptimes from Mana and regeneration, Life spent, attack speed, Mana > Life, Maelstrom's pulses.
        # (BUILDER_RESCORE_MODELS=1 puts the trials back, to measure what they cost: benchmarks/rescore_cost.py)
        build.models_on_stats = bool(stat_notes or harmony_notes or hand_notes or pulse_notes
                                     or totem_notes) and RESCORE_MODELS
        mods += energy_mods + expression_mods + ward_mods + stat_mods + harmony_mods + ring_mods + hand_mods + exposure_flags
        notes += energy_notes + expression_notes + ward_notes + stat_notes + harmony_notes + ring_notes + hand_notes + pulse_notes
        mods, corpse_notes = models.corpse_model(found, mods, stats, main)  # last: it overrides the trigger rates above (MECHB11: the adds' corpses)
        # (MECHB31, MB-2: "if you've Triggered a Skill Recently" from every trigger rate handed PoB above, and a triggered
        # main skill's own rate)
        triggered = {m["skill"]: models.number(m.get("value")) for m in mods
                     if m.get("kind") == "SkillData" and m.get("key") == "triggerRate"}
        if main is not None and main.triggered and main.name not in triggered:
            triggered[main.name] = models.number(stats.get("Speed"))
        recent_mods, recent_notes = models.triggered_recently_model([e["text"] for e in source_lines], triggered)
        mods, notes = mods + recent_mods, notes + recent_notes
        mayhem_mods, mayhem_notes = models.escalating_mayhem_model([e["text"] for e in source_lines], stats, main,
                                                                   triggered)  # (MECHB31, MB-10)
        mods, notes = mods + mayhem_mods, notes + mayhem_notes
        reload_mods, reload_notes = models.reloaded_recently_model([e["text"] for e in source_lines], stats)  # (MECHB31, MB-6)
        mods, notes = mods + reload_mods, notes + reload_notes
        ally_mods, ally_notes = models.ally_in_presence_model([e["text"] for e in source_lines], found)  # (MECHB31, MB-9)
        mods, notes = mods + ally_mods, notes + ally_notes
        if any(models.PROJECTILE_HIT_RECENTLY.search(e["text"].strip()) or models.PROJECTILE_HIT_RANGE.search(e["text"].strip())
               for e in source_lines):  # (COV35; the strike-range line COV36)
            hit_mods, hit_notes = models.projectile_hit_recently_model(
                [e["text"] for e in source_lines], self.projectile_attack_rates(main.name if main else None, stats))
            mods, notes = mods + hit_mods, notes + hit_notes
        faerie_mods, faerie_notes = faerie_fire(build, found)  # (MECHB31, MB-11: Periphery's Azmerian Swarms)
        mods, notes = mods + faerie_mods, notes + faerie_notes
        notes += corpse_notes + models.spirit_vessel_notes(skills)
        notes += models.granted_level_notes([(o.name, o.raw, o.granted_basis) for o in build.equipped.values() if o is not None and o.unique])
        if faced := [o.raw for o in build.equipped.values() if o is not None and FACEBREAKER_LINE.search(o.raw)]:
            notes += facebreaker_notes(faced, build.call("getConfig")["input"].get("configBossFaceBroken"))  # (COV32)
        chosen = [n for n in build.call("attributeNodes")["nodes"] if n["attribute"] >= 4]
        if chosen:
            kinds = {4: "Damage", 5: "Armour, Evasion and Energy Shield", 6: "Cost Efficiency"}
            counts = {kinds[a]: sum(1 for n in chosen if n["attribute"] == a) for a in kinds}
            notes.append({
                "label": "Traveller's Wisdom: attribute passives that take 5% instead of +5 to an attribute",
                "value": len(chosen),
                "basis": ", ".join(f"{count} take 5% increased {kind}" for kind, count in counts.items() if count)
                         + "; PoB can't store the choice, so the PoB code shows these passives as Strength (choose "
                         "them in game)",
                "confidence": "datamined"})
        if note := models.darkness_note(build.stats):
            notes.append(note)
        if note := models.low_life_cap_note(build.stats):
            notes.append(note)
        if note := models.es_bypass_note(build.stats):
            notes.append(note)
        notes += self.rate_presses()
        vessel_groups = models.spirit_vessel_groups(skills)  # (its companion's damage is in Full DPS only)
        return mods, notes, {**{g: None for g in energy_groups | mirage_groups | vessel_groups | toad_groups | doom_groups
                                                    | annihilation_groups},
                             **expression_groups, **storm_groups}

    def rate_presses(self) -> list[dict]:
        """Pressed non-main skills in Full DPS count at their real use, the main skill at the time they leave it
        (models.press_model; objective.Objective.press_scales). Returns the notes."""
        build = self.build
        groups = build.call("groups")
        main = groups["mainSocketGroup"]
        pressed = {g["index"] for g in groups["groups"] if g["index"] != main
                   and pressed_group(g, build.ctx.gems, getattr(build.ctx, "all_gems", None))}  # (SEARCH25: A36-14)
        # (MECH62: the Cull models' scales, reset beside press_scales and set again by cull_window)
        build.objective.whole_scales, build.objective.cull_scale = {}, 1.0
        build.orb_presses = {}  # (MECHB29, for MECH-C's orb_remnants: {skill: (Orbs a second, how)}, set below)
        if not pressed:
            build.objective.press_scales = {}
            return self.consume_ignite(groups, []) + self.cull_window()
        rates = build.call("useRates", indices=sorted(pressed | {main}))["rates"]
        # (MECHB29, probe POE2T1148) a curse's group in Full DPS (for its Doom Blast, Witchtoads or Annihilation) whose
        # rated skill is the curse: curse_model already charges its casts (and annihilation_model the extra ones), so
        # press_model doesn't charge them again. Before, it did: Despair once per duration (a double charge, an
        # UNDERCOUNT), and under Hayoxi's Fulmination's 2 s cooldown at 0.5 casts/s of 2 s each: the main skill at 0
        curses = {r["index"] for r in rates if r["index"] != main
                  and "AppliesCurse" in ((build.ctx.gems.get(str(r.get("name"))) or {}).get("skillTypes") or [])}
        if curses:
            pressed -= curses
            rates = [r for r in rates if r["index"] not in curses]
        if not pressed:
            build.objective.press_scales = {}
            return self.consume_ignite(groups, rates) + self.cull_window()
        paced = {g["index"]: models.CADENCE_PACE for g in groups["groups"] if g["index"] in pressed
                 and any(x["enabled"] and x["name"] in models.CADENCE_SUPPORTS for x in g["gems"])}
        self.untether_side_feed = (0.0, "")
        caps = self.press_caps(groups["groups"], pressed)
        near = getattr(build, models.ORB_NEAR, True) is not False  # (MECHB30: MECHC26's "without it" row switch)
        build.objective.press_scales, notes = models.press_model(rates, main, pressed, paced, caps,
                                                                 models.press_choices(build), near)
        side = {g["index"] for g in groups["groups"] if g["index"] in pressed and g["index"] != main
                and any(x["enabled"] and not x["support"] and x["name"] == models.UNTETHER for x in g["gems"])}
        if side:  # (MECHB35: a side Untether also fills from the other pressed skills, at their pressed rates: one more pass)
            main_name = next((r["name"] for r in rates if r["index"] == main), None)
            self.untether_side_feed = models.untether_side_feed((build.stats or {}).get("SkillDPS") or [],
                                                                build.objective.press_scales, main_name)
            if self.untether_side_feed[0] > 0:
                caps = self.press_caps(groups["groups"], pressed)
                build.objective.press_scales, notes = models.press_model(rates, main, pressed, paced, caps,
                                                                         models.press_choices(build), near)
        # (MECHB29, the manager 21:40: MECH-C's orb_remnants reads it) a pressed side Orb's Orbs a second, as press_model
        # scores it: each Orb ends at its maximum hits or its duration and is recast at once (models.orbs_per_second)
        orbs = models.orbs_per_second(rates, main, pressed, paced, caps, models.press_choices(build), near)
        if orbs:
            build.orb_presses = {models.ORB_OF_STORMS: (sum(orbs.values()), (
                "MECH-B's side-Orb cycle, models.orbs_per_second: each Orb ends at its maximum hits, fed by the Spells "
                "cast near it, or its duration, and is recast at once"))}
        return notes + self.consume_ignite(groups, rates) + self.cull_window()

    def cull_window(self) -> list[dict]:
        """MECH57: PoB's Full Culling DPS rescaled to the time a Cull saves when hits come one at a time
        (conditions_mod.cull_window_scale). MECH60: a Cull on Block (Doomgate) at the Blocks of the boss's hits
        (conditions_mod.block_cull_saving), where it saves more than the hit Cull. MECH62: the hit Cull's scale goes in
        objective.cull_scale and the Block Cull's gain in objective.whole_scales (SEARCH12 / SEARCH13), which count in
        main-skill scoring as well as Full DPS; before, both went in press_scales, so outside Full DPS the main-skill score
        kept PoB's whole Culling multiplier (an OVERCOUNT in the ranking) and missed the Block Cull (an undercount).
        rate_presses resets both before each call. Returns the notes."""
        from builder.search import bosses
        build = self.build
        entry = next((e for e in (build.stats or {}).get("SkillDPS") or [] if e.get("name") == "Full Culling DPS"), None)
        if entry is not None and number(entry.get("dps")) <= 0:
            entry = None
        on_block = any(o is not None and conditions_mod.CULL_ON_BLOCK_LINE.search(o.raw or "")
                       for o in (getattr(build, "equipped", None) or {}).values())
        murderous = any(x["name"] == models.MURDEROUS_INTENT and x.get("enabled", True)
                        for g in build.groups() if g.get("enabled") is not False for x in g["gems"])
        if entry is None and not on_block and not murderous:
            return []
        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        stats = build.call("stats", fields=list(conditions_mod.CULL_WINDOW_FIELDS)
                           + (list(conditions_mod.CULL_ON_BLOCK_FIELDS) if on_block else []))
        full = number((build.stats or {}).get("FullDPS")) or number(stats.get("FullDPS"))
        culled = number(entry.get("dps")) if entry is not None else 0.0
        base = full - culled
        split = build.call("cullSplit") or {}
        notes = []
        hit_gain = 0.0  # (the hit Cull's gain over base, as counted)
        if entry is not None:
            mult = 1.0 + culled / max(base, 1e-9)
            # (MECH59: a Cull only Heavy Contact gives lands on the main skill's Heavy Stuns of the boss)
            heavy = models.heavy_stun_rate({**build.call("stats", fields=models.MODEL_FIELDS),
                                            **models.dazed_stats(build)})[0] if split.get("heavyOnly") else 0.0
            # (MECH63: a Cull only Murderous Intent gives lands on the main skill's Empowered uses)
            empowered = self.empowered_uses(stats) if split.get("empowerOnly") else None
            main_mult = number(stats.get("CullMultiplier"))
            other = self.other_culler(mult) if main_mult < mult - 1e-6 else None
            scale, how, confidence = conditions_mod.cull_window_scale(
                mult, main_mult, conditions_mod.boss_pool(boss), base,
                number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0,
                number(stats.get("TotalDPS")), number(stats.get("TotalDotDPS")), split, heavy, empowered, other,
                bars=conditions_mod.life_bars(boss))  # (MECHC31: the Cull on the last Life bar only, R26-64)
            hit_gain = (mult - 1.0) * scale
            if scale < 1.0 or how:
                build.objective.cull_scale = scale
                notes.append({"label": f"Culling Strike against {boss}: the time a Cull saves*", "value": f"x{scale:.3f} of PoB's",
                              "basis": how, "confidence": confidence})
        if murderous and not any(r.get("source") == "Skill:SupportEmpoweredCullPlayer" for r in split.get("rows") or []):
            notes += self.empowered_cull(boss, stats, base, hit_gain, split)
            hit_gain = max(hit_gain, build.objective.whole_scales.get(models.MURDEROUS_INTENT, 1.0) * (1.0 + hit_gain) - 1.0)
        if on_block:
            notes += self.cull_on_block(boss, {**stats, "_boss": boss}, base, hit_gain, split, entry is not None)
        return notes

    def empowered_cull(self, boss: str, stats: dict, base: float, hit_gain: float, split: dict) -> list[dict]:
        """MECH64: Murderous Intent's Cull where PoB gives none (with Unleash or a Warcry; MECH63's probe POE2T588, an
        UNDERCOUNT): the saving at the main skill's Empowered uses (empowered_uses; conditions_mod.empowered_cull_saving),
        counted where it beats the hit Cull's (hit_gain over base), in objective.whole_scales as Doomgate's is."""
        from builder.search import bosses
        build = self.build
        inc = number(split.get("inc")) or 1.0
        threshold = bosses.cull_threshold(boss) / 100.0 * inc
        rate, rate_how = self.empowered_uses(stats)
        share, how = conditions_mod.empowered_cull_saving(threshold / conditions_mod.life_bars(boss),
                                                          conditions_mod.boss_pool(boss), base, rate)  # (MECHC31)
        gain = share / (1.0 - share) if share < 1.0 else 0.0
        label = f"Murderous Intent's Cull on Empowered uses against {boss}*"
        play = "Use the skill Murderous Intent supports as the boss gets low: the Empowered hit under the threshold Culls it."
        basis = (f"{how}; {rate_how}; threshold {threshold:.2%} of Life + ES (x{inc:.2f} increased Culling Strike "
                 "Threshold, MEDIUM); PoB gives this skill no Cull")
        if gain <= hit_gain:
            return [{"label": label, "value": "+0%", "basis": basis + ("; the build's hit Cull saves as much or more, so "
                     "this adds nothing" if share > 0 else ""), "confidence": "MEDIUM" if share > 0 else "LOW",
                     "play": play}]
        build.objective.whole_scales[models.MURDEROUS_INTENT] = (1.0 + gain) / (1.0 + max(hit_gain, 0.0))
        return [{"label": label, "value": f"+{gain:.2%} DPS", "basis": basis + "; counted on all of the build's damage, "
                 "main-skill scoring too", "confidence": "MEDIUM", "play": play}]

    def other_culler(self, mult: float) -> dict | None:
        """MECH64: the Full DPS skill other than the main one whose Culling multiplier PoB's Full DPS takes (the largest,
        Calcs.lua mergeFullDPSPass's "cull" mode), with its hits per second (useRates' cooldown-capped speed x hit
        chance), its hit DPS x its Full DPS count, and its own cullSplit, for conditions.cull_window_scale's other.
        None where no rated skill has that multiplier."""
        build = self.build
        main = (build.main_group() or {}).get("index")
        indices = [g["index"] for g in build.groups() if g.get("fullDPS") and g.get("enabled") is not False
                   and g["index"] != main]
        if not indices:
            return None
        rates = build.call("useRates", indices=indices)["rates"]
        best = max((r for r in rates if number(r.get("cullMultiplier")) > 1.0), default=None,
                   key=lambda r: number(r.get("cullMultiplier")))
        if best is None or abs(number(best["cullMultiplier"]) - mult) > 1e-3 * mult:
            return None
        count = next((number(e.get("count")) or 1.0 for e in (build.stats or {}).get("SkillDPS") or []
                      if e.get("name") == best.get("name")), 1.0)
        return {"name": best.get("name"), "mult": number(best["cullMultiplier"]),
                "hits": number(best.get("speed")) * (number(best.get("hitChance")) or 100.0) / 100.0,
                "hitDps": number(best.get("hitDPS")) * count, "split": best.get("cullSplit") or {}}

    def empowered_uses(self, stats: dict, support: str | None = "Murderous Intent") -> tuple[float, str]:
        # (support: models.MURDEROUS_INTENT by default; spelled out: in this class body `models` is the method)
        """MECH63: (the main skill's Empowered uses per second, how) from the skills Murderous Intent supports ("Skills
        Empowered by Supported Skills have Culling Strike"), each at the build's own use of it (rule 2: no flat rate):
        Mana Tempest Empowers every Mana-costing Spell cast in its storm (the main skill's uses x the storm's uptime,
        models.mana_tempest_uptime); Unleash "the next Repeatable Spell you cast", not one with a Cooldown (one per
        Unleash, at its use rate: bridge useRates, cooldown-capped; MECH65: Repeatable checked); a Warcry Empowers PoB's
        Num<Warcry>Empowers attacks per use (its Power over the boss's, CalcPerform 2196-2215: bridge useRates
        warcryEmpowers) where the main skill is what its own text Empowers (WARCRY_EMPOWERS, MECH65); any other
        Empowering skill one use per use of it (LOW, UNVERIFIED: its own count isn't read). Capped at the main skill's
        uses. Mana Tempest's storm comes in long blocks, not evenly spaced uses (MEDIUM).
        `support` None (MECH66): every group but the main one, for lines on any Empowered use ("... with Empowered
        Attacks"); a group with no skill that can Empower others is then passed over."""
        build = self.build
        uses = number(stats.get("Speed"))
        main = build.main_group() or {}
        groups = [g for g in build.groups() if g.get("enabled") is not False and (
            any(x["name"] == support and x.get("enabled", True) for x in g["gems"]) if support
            else g.get("index") != main.get("index"))]
        if not groups or uses <= 0:
            return 0.0, f"no skill {support or 'of the build'} supports Empowers the main skill"
        raw = build.call("activeSkills", supportStats=[models.OLROTH_EMPOWERS])["skills"]
        mains = [s for s in raw if s.get("group") == main.get("index") and not s.get("disabled")]
        spell, cooled = any(s.get("spell") for s in mains), any(s.get("cooldown") for s in mains)
        repeatable = spell and any(s.get("repeatable") for s in mains)
        rates = {r["index"]: r for r in build.call("useRates", indices=[g["index"] for g in groups])["rates"]}
        total, parts = 0.0, []
        for group in groups:
            # (MECH66: the skill in the group that can Empower others, not the first one PoB lists. Ferocious Roar is a
            # Meta gem without EmpowersOtherSkill that triggers a human-form Warcry socketed in it (ActiveSkills
            # ferocious_roar; its hidden SupportFerociousRoarPlayer allows Warcries, adds Triggered); Murderous Intent
            # excludes no type, so in that group it supports the triggered Warcry (Meta gems' supports reach their
            # socketed skills: MEDIUM), which Empowers once per Roar, at the group's use rate)
            listed = [s for s in raw if s.get("group") == group["index"] and not s.get("disabled")]
            skill = next((s for s in listed if s.get("empowers")), listed[0] if listed else None)
            if support is None and (skill is None or not skill.get("empowers")):
                continue
            if skill is None:
                parts.append(f"Murderous Intent's group {group['index']} has no active skill PoB lists")
                continue
            if skill is not listed[0]:
                parts.append(f"{skill['name']} is triggered by {listed[0]['name']} (Meta), at its use rate (MEDIUM)")
            name, rate = skill["name"], rates.get(group["index"]) or {}
            per = number(rate.get("speed"))
            # (MECHB32: Olroth's Conviction, models.OLROTH_EMPOWERS: each use Empowers that many more skill uses. A
            # Warcry's are in PoB's count already, GEM11's olroth_model; Mana Tempest is Indeterminate, so it can't)
            more = 1 + max(0.0, number((skill.get("supportStats") or {}).get(models.OLROTH_EMPOWERS)))
            olroth = f" x {more:g} (Olroth's Conviction)" if more > 1 else ""
            if name == "Mana Tempest":
                if not spell:
                    parts.append("Mana Tempest Empowers Spells only, and the main skill isn't one")
                    continue
                share, why, _ = models.mana_tempest_uptime(models.Skill.of(skill), {**stats, **build.call(
                    "stats", fields=["Mana", "ManaRegenRecovery", "ManaPerSecondCost", "LifePerSecondCost"])})
                total += uses * share
                parts.append(f"Mana Tempest: the main skill's {uses:.2f} uses/s x the storm's {share:.0%} uptime ({why})")
            elif name == "Unleash":
                # (MECH65, AUDIT29 A29-3: "Empowering the next Repeatable Spell you cast ... Cannot Empower Skills with
                # a Cooldown", ActiveSkills unleash; Incinerate, Bonestorm, Solar Orb, Frost Darts aren't Repeatable)
                ok = repeatable and not cooled
                total += per * more if ok else 0.0
                parts.append(f"Unleash Empowers the next Repeatable Spell, one per use{olroth}: {per * more:.3f}/s" if ok else
                             "Unleash Empowers only a Repeatable Spell without a Cooldown, and the main skill isn't one")
            elif not skill.get("empowers"):  # (MECH64: Murderous Intent "Supports skills that can Empower skills other
                # than themselves", requireSkillTypes EmpowersOtherSkill: on any other skill it does nothing. MECH65:
                # the game files' SupportEmpoweredCullPlayer has AllowedActiveSkillTypes [EmpowersOtherSkill] too, and no
                # GrantedEffect adds that type; checked GrantedEffects 26476662. So Ferocious Roar, Rallying Cry and
                # Battlemage's Cry, Warcries without the type, get nothing even with PoB's Empower count)
                parts.append(f"{name} can't Empower other skills: Murderous Intent does nothing there")
            elif (("Warcry" in ((getattr(getattr(build, "ctx", None), "gems", None) or {}).get(name) or {}).get(
                    "skillTypes", ()) or "warcryEmpowers" in rate)  # (useRates sets it for Warcries only)
                  and any(s.get("name") in models.WARCRY_PROOF for s in mains)):
                # (MECH82; the manager 10:02, GEM15's routing: Resonating Shield's cannot_be_empowered_by_warcries,
                # models.warcry_proof_model: a Warcry Empowers none of its uses, by PoB's count or the one-per-use branch)
                parts.append(f"{name} is a Warcry, and the main skill cannot be Empowered by Warcries: 0")
            elif number(rate.get("warcryEmpowers")) > 0:
                n = number(rate["warcryEmpowers"])
                # (MECH71: the Heavy Stunned share reads the Dazed boss's 50% more Stun Buildup: heavy_stun_rate)
                what, fits, share, why = warcry_empowers(name, mains, {**stats, **models.dazed_stats(build), **(
                    {"_primed_freeze": build.primed_freeze} if getattr(build, "primed_freeze", None) else {})})
                # (MECH82: Arctic Howl's own Freezes of a Primed boss, from the conditions' rate)
                if not fits:
                    parts.append(f"{name} Empowers {what}, and the main skill isn't one")
                    continue
                total += per * n * share
                parts.append(f"{name}: {n:g} Empowered {what} per use (PoB's count from its Power) x {per:.3f} uses/s"
                             + (f" x {share:.0%} ({why})" if share < 1.0 else ""))
            else:
                total += per * more
                parts.append(f"{name}: one Empowered use per use of it{olroth}, {per * more:.3f}/s (LOW, UNVERIFIED: its "
                             "own count isn't read)")
        total = min(max(total, 0.0), uses)
        return total, "; ".join(parts) + f"; {total:.3f} Empowered uses/s of the main skill's {uses:.2f}"

    def cull_on_block(self, boss: str, stats: dict, base: float, hit_gain: float, split: dict, has_entry: bool) -> list[dict]:
        """MECH60: Doomgate's "Enemies are Culled on Block", which PoB ignores: the saving at the Blocks of the boss's hits
        (conditions_mod.block_cull_saving), counted where it beats the hit Cull's (hit_gain over base). MECH62: it goes in
        objective.whole_scales as (1 + gain) / (1 + hit_gain), on top of the hit Cull's own counted gain (cull_scale), so
        the build's damage comes to base x (1 + gain) in Full DPS (as before, through press_scales) and now in main-skill
        scoring too. has_entry is kept for the caller's signature."""
        from builder.search import bosses, boss_hits
        build = self.build
        inc = number(split.get("inc")) or 1.0
        threshold = bosses.cull_threshold(boss) / 100.0 * inc
        every = bosses.benefit_skill_time(stats)
        blocks = boss_hits.block_share(stats) / every if every > 0 else 0.0
        share, how = conditions_mod.block_cull_saving(threshold / conditions_mod.life_bars(boss),
                                                      conditions_mod.boss_pool(boss), base, blocks)  # (MECHC31)
        gain = share / (1.0 - share) if share < 1.0 else 0.0
        label = f"Enemies are Culled on Block (Doomgate) against {boss}*"
        play = "Block the boss's hits once it is low: a Block under the threshold Culls it."
        if gain <= hit_gain:
            return [{"label": label, "value": "+0%",
                     "basis": how + ("; the build's hit Cull saves as much or more, so this adds nothing"
                                     if share > 0 else "") + " (PoB ignores the line)",
                     "confidence": "MEDIUM" if share > 0 else "LOW", "play": play}]
        build.objective.whole_scales["Enemies are Culled on Block"] = (1.0 + gain) / (1.0 + max(hit_gain, 0.0))
        return [{"label": label, "value": f"+{gain:.2%} DPS",
                 "basis": (f"{how}; blockable hits Blocked {boss_hits.block_share(stats):.0%}, one boss hit every "
                           f"{every:.2f} s (the 'when hit' benefit pace, LOW); threshold {threshold:.2%} of Life + ES "
                           f"(x{inc:.2f} increased Culling Strike Threshold, MEDIUM); counted on all of the build's "
                           "damage, main-skill scoring too (PoB ignores the line)"),
                 "confidence": "LOW", "play": play}]

    def consume_ignite(self, groups: dict, rates: list[dict]) -> list[dict]:
        """MECH52: a main skill that consumes Ignite keeps PoB's Best Ignite DPS to the share of the fight the boss carries
        an Ignite (conditions_mod.consumed_ignite_scale), on top of the press scales. Returns the notes."""
        build = self.build
        main = self.main_skill()
        group = next((g for g in groups["groups"] if g["index"] == groups["mainSocketGroup"]), None)
        # (MECH75: Bleeding too, PoB's Best Bleed DPS, consumed by Blood Hunt / Haemocrystals: CONSUME_STATS' note; the
        # same formula, a Bleed lasting its BleedDuration)
        kinds = [(ailment, [e for e in (build.stats or {}).get("SkillDPS") or [] if e.get("name") == f"Best {ailment} DPS"])
                 for ailment in ("Ignite", "Bleed")]
        kinds = [(a, found) for a, found in kinds if found]
        if main is None or group is None or not kinds:
            return []
        gems = {g["name"] for g in group["gems"] if g.get("enabled", True)}
        mains = conditions_mod.main_consumes(build, main, gems)
        sides = None
        notes = []
        for ailment, entries in kinds:
            consumer = mains.get(ailment)
            if consumer is None and sides is None:
                sides = conditions_mod.side_consumes(build, main, groups)
            side = None if consumer is not None else sides.get(ailment)
            if consumer is None and side is None:
                continue
            fields = ["Speed", "HitChance", f"{ailment}ChancePerHit", f"{ailment}Duration", f"{ailment}StackPotential"]
            stats = build.call("stats", fields=fields)
            hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
            chance = min(number(stats.get(f"{ailment}ChancePerHit")) / 100.0, 1.0)
            for entry in entries:
                source = entry.get("source") or ""
                own = source == main
                rate = next((r for r in rates if r.get("name") == source), None)
                potential = (number(stats.get(f"{ailment}StackPotential")) if own
                             else number(((rate or {}).get("stackPotential") or {}).get(ailment)))
                if side is not None:  # (MECH53: a skill beside the main one consumes it)
                    scale, how = conditions_mod.side_consumed_ignite_scale(potential, hits, chance, own, side[0])
                    by, why, confidence = (side[1], f"{side[1]} consumes {ailment} (KeywordPopups Consume: then immune 1 s)",
                                           side[2])
                else:
                    scale, how = conditions_mod.consumed_ignite_scale(potential, hits, chance,
                                                                      number(stats.get(f"{ailment}Duration")), own)
                    why = f"{main} consumes {ailment} on hit (KeywordPopups Consume: then immune 1 s)"
                    by, confidence = consumer, "MEDIUM"
                if scale >= 1.0:
                    continue
                if ailment == "Bleed":
                    how = how.replace("to Ignite again", "to Bleed again").replace("Ignited", "Bleeding").replace(
                        "another skill's Ignite", "another skill's Bleed")
                key = models.press_key(f"Best {ailment} DPS", source or None)
                build.objective.press_scales[key] = build.objective.press_scales.get(key, 1.0) * scale
                notes.append({"label": f"{ailment} from {source or 'the build'}: consumed by {by}*", "value": f"x{scale:.3f}",
                              "basis": f"{why}; {how}", "confidence": confidence})
        return notes

    def press_caps(self, groups: list[dict], indices: set[int]) -> dict[int, "models.PressCap"]:
        """The press hook (MECHB5; COVC6 / COVC7, usage.py's rates): for each group in `indices` whose skill or support
        needs Combo, walking between uses, or Mana Flask charges for a throw, the most uses a second the build can feed
        it (models.PressCap). Combo: usage.combo_rate (N builder Strikes, then a use: 0 with no builder in the build);
        walking: usage.travel_rate (10 m at the build's movement speed, LOW; once it's in usage.py); a Concoction:
        flasks.charge_use_rate (the Mana Flask's charge generation / 5 a throw); a Glory skill (models.GLORY_SKILLS /
        GLORY_FROM_LIFE) outside the main group: the uses its Glory pays for (models.glory_plan; MECHB21, SEARCH15's
        open OVERCOUNT: a damaging Glory skill in a pressed Full DPS group was counted at its duration or the search's
        chosen rate, never held to its Glory). Groups needing none are left out."""
        from builder.search.flasks import THROW_CHARGES
        from builder.search.usage import Usage, requirements

        caps: dict[int, models.PressCap] = {}
        usage, rules, glory = None, usage_rules(self.build), None
        for group in groups:
            if group.get("index") not in indices or not group.get("enabled", True):
                continue
            for gem in group["gems"]:
                if not gem.get("enabled", True):
                    continue
                name = gem["name"]
                cap = None
                if not gem["support"] and name in THROW_CHARGES:
                    uses, how = self.throw_rate(name)
                    cap = models.PressCap(uses, how, 0.0, "MEDIUM")
                if not gem["support"] and not group.get("main") and (
                        name in models.GLORY_SKILLS or name in models.GLORY_FROM_LIFE):
                    glory = glory or self.glory_caps()
                    uses, how = glory.get(name, (0.0, f"{name}: its Glory wasn't read"))
                    found = models.PressCap(uses, f"Glory: {how}", 0.0, "MEDIUM")
                    cap = found if cap is None or found.uses < cap.uses else cap
                if not gem["support"] and name == models.UNTETHER and not group.get("main"):
                    found = self.untether_cap(group["index"])  # (MECH-B, MECHB32)
                    cap = found if cap is None or found.uses < cap.uses else cap
                for need in requirements(name, rules[name]) if rules.get(name) else []:
                    usage = usage or Usage(self.build, None)
                    if need.kind == "combo":
                        found = usage.combo_rate(name, need.amount)
                        cap = (models.PressCap(found[0], f"Combo: {need.amount:g} {found[2]} Strikes a use", 0.0, "MEDIUM")
                               if found else models.PressCap(0.0, f"Combo: no skill of the build Strikes to build its "
                                                                  f"{need.amount:g} Combo", 0.0, "MEDIUM"))
                    elif need.kind == "travel" and hasattr(usage, "travel_rate"):
                        found = usage.travel_rate(name)
                        if found and found[0] > 0:
                            moving = found[2] / found[0]
                            cap = models.PressCap(found[0], f"walking 10 m before each use ({moving:.2f} s at the build's "
                                                  f"movement speed and a base {BASE_RUN:g} m/s*, MEDIUM; COV-C's "
                                                  "travel_rate)", moving, "MEDIUM")
                    elif need.kind == "anguish" and hasattr(usage, "anguish_rate"):  # (COVC11: Compose Requiem)
                        if found := usage.anguish_rate(name):
                            cap = models.PressCap(found[0], f"anguish: the Requiem up {found[1]:.0%} of the fight "
                                                  "(12 s per 30 s fill; 40% if anguish builds during it, UNVERIFIED)",
                                                  0.0, "MEDIUM")
                        else:  # (MECHB9's review: no fill rate or use rate read means no Requiem, never PoB's at-will
                            # rate, the x3.5 COVC12 found when the bridge's rule lacked the fill rate)
                            cap = models.PressCap(0.0, "anguish: no anguish fill rate or use rate was read, so the "
                                                  "Requiem isn't counted", 0.0, "MEDIUM")
                    if cap is not None:
                        break
                if cap is not None:
                    old = caps.get(group["index"])
                    caps[group["index"]] = cap if old is None or cap.uses < old.uses else old
        return caps

    def untether_cap(self, index: int) -> "models.PressCap":
        """(MECH-B, MECHB32) Untether pressed beside the main skill: at most the explosions the build's damage fills
        (models.untether_rate) from the main skill's damage a second and the damage computed outside PoB (other side
        skills not counted: an undercount), its own explosions, and PoB's cast rate for it."""
        need, seeds, duration, hit, casts, _ = self.untether_inputs(index)
        stats = self.build.call("stats", fields=list(LIVING_BOMB_FIELDS))
        added = number(getattr(getattr(self.build, "objective", None), "added_dps", 0.0))
        others, others_how = getattr(self, "untether_side_feed", (0.0, ""))
        feed = per_second(stats) + max(added, 0.0) + max(others, 0.0)  # (MECHB35: + the other pressed skills')
        # (MECHB33; RESEARCH26 mechb33-hazards-untether-r26.md: one seed per enemy is the headline, the lower case, so no
        # overcount on an unverified reading; several seeds on the boss is the other row)
        rate, how = models.untether_rate(need, feed, hit, 1.0, duration, casts)
        several, _ = models.untether_rate(need, feed, hit, seeds, duration, casts)
        how += f" (one seed per enemy, UNVERIFIED either way); if {seeds:g} seeds can sit on the boss: {several:.3f}/s"
        how += f"; the feed counts {others_how}" if others > 0 else (
            "; the other pressed skills' damage not counted (none in Full DPS, or Full DPS not read)")
        return models.PressCap(rate, f"Untether: {how}", 0.0, "MEDIUM")

    def glory_caps(self) -> dict[str, tuple[float, str]]:
        """{Glory skill: (uses per second its Glory pays for, how)} for press_caps: models.glory_plan for each Glory skill
        in the build on its own (each skill builds its own Glory: KeywordPopups Glory, "each Skill can only generate
        Glory"), from the main skill's events (glory_events), the skills' cooldowns and the build's Glory lines."""
        build = self.build
        raw = [s for s in build.call("activeSkills")["skills"] if not s.get("disabled")]
        skills = [models.Skill.of(s) for s in raw]
        main_group = build.main_group() or {}
        main = next((s for s in skills if s.group == main_group.get("index")
                     and s.name in {g.get("name") for g in main_group.get("gems") or []}), None)
        stats = build.call("stats", fields=models.MODEL_FIELDS)
        stats.update(models.dazed_stats(build))  # (MECH70: heavy_stun_rate)
        texts = [line["text"] for line in build.call("sourceLines")["lines"]]
        out = {}
        for s in skills:
            if (s.name in models.GLORY_SKILLS or s.name in models.GLORY_FROM_LIFE) and s.name not in out:
                plan, how = models.glory_plan([s.name], stats, main, skills, texts)
                out[s.name] = (plan.get(s.name, (0.0, 0.0))[0], how)
        return out

    def pustule_cap(self, found: list["models.Skill"]) -> tuple[list[dict], list[dict]]:
        """Poison Spores' Pustule Explosion at one a fuse on the boss (models.pustule_cap): PoB's rate for each group's
        Spores skill (useRates with that active skill), and its Detonation Time."""
        mods, notes, by_index, poison = [], [], None, None
        for name in models.PUSTULE_SKILLS:  # (Poison Spores, Toxic Domain: AUDIT23 A23-1; Toxic Growth: MECHB14)
            indices = models.pustule_groups(found, name)
            if not indices:
                continue
            by_index = by_index or {g["index"]: g for g in self.build.groups()}
            actives = [next((i for i, s in enumerate(by_index[n]["skills"], start=1) if s["name"] == name), 0)
                       if n in by_index else 0 for n in indices]
            rates = self.build.call("useRates", indices=indices, actives=actives)["rates"]
            if poison is None:  # (MECHB15: the stored-Poison "with it" row; PoB's Poison DPS on the boss, main skill)
                poison = number(self.build.call("stats", fields=["PoisonDPS"]).get("PoisonDPS"))
            more, why = models.pustule_cap(rates, name, poison)
            mods, notes = mods + more, notes + why
        return mods, notes

    def tornado_copies(self, found: list["models.Skill"], main: "models.Skill | None",
                       stats: dict) -> tuple[list[dict], list[dict]]:
        """The main projectile attack fired through Tornado Shot's Tornado (models.tornado_copy_model): Tornado Shot's
        level stats (with quality) and its own PoB use rate (useRates with that active skill)."""
        tornado = next((s for s in found if s.name == models.TORNADO_SHOT and s.group is not None), None)
        if tornado is None or main is None or main.name == models.TORNADO_SHOT:
            return [], []
        raw = self.build.call("activeSkills", levelStats=[models.TORNADO_SHOT])["skills"]
        level = next((s.get("levelStats") or {} for s in raw if s["name"] == models.TORNADO_SHOT and s.get("levelStats")), {})
        group = next((g for g in self.build.groups() if g["index"] == tornado.group), None)
        active = next((i for i, s in enumerate(group["skills"], start=1) if s["name"] == models.TORNADO_SHOT), 0) if group else 0
        rates = self.build.call("useRates", indices=[tornado.group], actives=[active])["rates"] if active else []
        speed = number(rates[0].get("speed")) if rates else 0.0
        types = set((self.build.ctx.gems.get(main.name) or {}).get("skillTypes") or [])
        return models.tornado_copy_model(main, types, stats, tornado, level, speed)

    def throw_rate(self, name: str) -> tuple[float, str]:
        """Throws a second of a Concoction (flasks.THROW_CHARGES) the Mana Flask's own charge generation pays for (the
        formula Assumptions.flask_main uses for a main Concoction), and how."""
        from builder.search.flasks import FLASK_SLOTS, THROW_CHARGES, THROW_POOL, Charges, charge_use_rate

        gain = Charges.of(self.build)
        slot = next(k for k, v in FLASK_SLOTS.items() if v == THROW_POOL)
        raw = self.build.equipped[slot].raw if self.build.equipped.get(slot) is not None else None
        flask = self.build.call("flaskData", raws=[raw])["results"][0] if raw else None
        rate, how = charge_use_rate(flask, gain, THROW_POOL, THROW_CHARGES[name])
        return rate, f"Mana Flask charges ({how})"

    def main_cap(self, group: dict | None, main: str | None) -> tuple[list[dict], list[dict]]:
        """The press hook for the main skill (MECHB5): its damage at the share of PoB's at-will rate that its Combo or its
        walking between uses allows (a DPS "more", so its ailments' stacks follow), in both scoring modes. A main
        Concoction is capped by flask_main instead."""
        from builder.search.flasks import THROW_CHARGES

        if group is None or main is None or main in THROW_CHARGES:
            return [], []
        cap = self.press_caps([group], {group["index"]}).get(group["index"])
        if cap is None or "Mana Flask" in cap.how:
            return [], []
        rate = next(iter(self.build.call("useRates", indices=[group["index"]])["rates"]), None) or {}
        speed = number(rate.get("speed"))
        if speed <= 0 or cap.uses >= speed:
            return [], []
        share = cap.uses / speed
        return [models.raw_mod("DPS", "MORE", 100 * (share - 1), main)], [{
            "label": f"{main}: used as often as {cap.how.split(':')[0].split(' (')[0]} allows*", "value": f"x{share:.3f}",
            "basis": f"PoB counts it used at will ({speed:.2f}/s); {cap.how} hold it to {cap.uses:.3g} uses/s "
                     "(the time between uses included)",
            "confidence": cap.confidence}]

    def remnant_supply(self, skills: list[dict], skip: tuple[str, ...] = ()) -> tuple[float, list[str]]:
        """Remnants per second the build makes on the request's boss, before the build's "additional Remnant" /
        "reappear" / double-pickup lines (remnant_multiplier), and what makes them: every REMNANT_SOURCES skill it has
        (MECHC15: lifted out of the Transmutation Explosion hook so the "when you collect a Remnant" recovery lines read
        the same supply). `skills`: activeSkills with KALGUUR's levelStats; `skip`: sources the caller counts itself."""
        present = {s["name"]: s for s in skills if not s.get("disabled")}
        supply, used = 0.0, []
        for name, (rate, condition) in REMNANT_SOURCES.items():
            skill = present.get(name)
            if skill is None or name in skip:
                continue
            if name in NEEDS_MINIONS:  # (Grim Feast: your minions' deaths, from the build's own sources)
                found = [models.Skill.of(s) for s in present.values()]
                group = self.build.main_group()
                main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill()),
                            None)
                rate, condition = models.minion_deaths(found, main, {**self.build.call("stats", fields=models.MODEL_FIELDS),
                                                                     "_minion_lives": getattr(self.build, "minion_lives", {})})
                if rate <= 0:
                    used.append(f"{name} 0/s ({condition})")
                    continue
            if name in COOLDOWN_SOURCES:
                rate *= skill.get("cooldownRecovery") or 1.0
            if name == KALGUUR:
                from builder.search import bosses  # (the request's boss: its Life for Light Stuns)

                melee = bool(next((s for s in skills if s["name"] == self.main_skill()), {}).get("melee"))
                boss_stats = bosses.kill_stats(getattr(getattr(getattr(self.build, "ctx", None), "request", None),
                                                       "boss", None) or bosses.DEFAULT)
                stuns, how = models.stun_rate({**self.build.call("stats", fields=models.MODEL_FIELDS), **boss_stats},
                                              melee)
                rate, condition = kalguur_rate(stuns), f"50% per Stun, at most one per 3 s, {how}"
                # (MECHB16) and its on-kill chance (ward_remnants_chance_to_spawn_orb_on_killing_enemy_%, 25 at
                # level 20; no cooldown stat on it) at the enemies the build kills: the request's boss preset's adds
                # (build.kills[0], models.kill_rate; 0 on the default lone boss). Own minions aren't enemies.
                rate, condition = kalguur_kill_rate(rate, condition, skill.get("levelStats") or {},
                                                    getattr(self.build, "kills", None))
            elif name == ORB_OF_STORMS:
                rate, condition = self.orb_remnants(skill)
            elif name == SIPHON:
                rate, condition = siphon_freezes(self.build.call("stats", fields=models.IMMOBILISE_FIELDS))
            elif name == LIVING_BOMB:
                rate, condition = living_bomb_rate(self.build.call("stats", fields=list(LIVING_BOMB_FIELDS)),
                                                   skill.get("castTime") or 0.0)
            elif name in HIT_REMNANTS:
                rate, condition = hit_remnant_rate(name, self.build.call("stats", fields=list(HIT_REMNANT_FIELDS)))
            elif name == "Snap":  # (MECH46: it needs an ailment on the boss to Consume)
                found, how = snap_found(snap_stats(self.build))
                rate *= found
                condition = f"on its cooldown, when it finds an ailment to Consume ({found:.0%}: {how})"
            supply += rate
            used.append(f"{name} {rate:.2f}/s ({condition})")
        return supply, used

    def orb_remnants(self, skill: dict) -> tuple[float, str]:
        """(Orbs of Storms a second, how): orb_remnants for this build."""
        return orb_remnants(self.build, skill)

    def heat_spenders(self, found: list) -> list[tuple[str, float, str]]:
        """The build's Heat spenders (models.HEAT_SPENDERS) other than the main skill, at their pressed use rate
        (models.press_use_rate: cooldown, duration, else the search's chosen rate, else 0)."""
        groups = {s.group for s in found if s.name in models.HEAT_SPENDERS and s.group is not None}
        if not groups:
            return []
        rates = self.build.call("useRates", indices=sorted(groups))["rates"]
        out = []
        for rate in rates:  # (the display name may carry its stat set's label after the skill's name)
            spender = next((n for n in models.HEAT_SPENDERS if str(rate.get("name") or "").startswith(n)), None)
            if spender is not None:
                uses, how, _ = models.press_use_rate(rate, models.press_choice(rate, models.press_choices(self.build)))
                if uses > 0:
                    out.append((spender, uses, how))
        return out

    def hitters(self, found: list) -> set[str]:
        """The build's skills that hit (their gem's types hold Attack or Damage and no minion type; an item-granted
        skill with no gem entry counts if it is an Attack): the breakers hazard_early_model looks for (MECHB36, R26-43)."""
        out = set()
        for s in found:
            gem = self.build.ctx.gems.get(s.name)
            types = set((gem or {}).get("skillTypes") or [])
            if gem is None:
                if s.attack and s.summon is None:
                    out.add(s.name)
            elif types & {"Attack", "Damage"} and not types & {"Minion", "CreatesMinion"}:
                out.add(s.name)
        return out

    def heat_made(self, found: list, main: "models.Skill") -> tuple[float, str]:
        """(Heat a second, how) from the build's Rapid Shot pressed beside the main skill: one Heat an attack at its
        pressed use rate (models.press_use_rate: the search's chosen rate, else 0). MECHB35."""
        groups = {s.group for s in found if s.name == models.RAPID_SHOT and s.group is not None and s.group != main.group}
        if not groups:
            return 0.0, ""
        total, parts = 0.0, []
        for rate in self.build.call("useRates", indices=sorted(groups))["rates"]:
            if str(rate.get("name") or "").startswith(models.RAPID_SHOT):
                uses, how, _ = models.press_use_rate(rate, models.press_choice(rate, models.press_choices(self.build)))
                total += uses * models.HEAT_PER_ATTACK
                parts.append(f"Rapid Shot {uses:.2f} attacks/s ({how})")
        return total, "; ".join(parts)

    def living_bomb_main(self, skills: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """Living Bomb as the main skill: its casts cut to one explosion per seed (living_bomb_main_speed)."""
        if main != LIVING_BOMB or not any(s["name"] == LIVING_BOMB and not s["disabled"] for s in skills):
            self.bomb_speed = 1.0
            return [], []
        stats = self.build.call("stats", fields=list(LIVING_BOMB_MAIN_FIELDS))
        speed = number(stats.get("Speed")) / (self.bomb_speed or 1.0)  # (without this line, which is on PoB's numbers)
        added = number(getattr(getattr(self.build, "objective", None), "added_dps", 0.0))
        share, how = living_bomb_main_speed(stats, speed, added)
        self.bomb_speed = share
        if share >= 1.0:
            return [], []
        return ([models.line("1% more Attack and Cast Speed", -100 * (1 - share), skill=LIVING_BOMB)],
                [{"label": "Living Bomb: one explosion per seed", "value": f"{share:.1%} of PoB's casts",
                  "basis": how + " (PoB counts an explosion every cast)",
                  "play": ("cast a new seed as the last one explodes; the build's other damage sets how fast it does"
                           if share > LIVING_BOMB_FLOOR else "the build's other damage is too low for a seed to "
                           "explode within its duration: Living Bomb deals nothing here"),
                  "confidence": "game files' rule and 0.3.0's note; UNVERIFIED as labelled"}])

    def companions(self, skills: list[dict], texts: list[str], main: "models.Skill | None") -> tuple[list[dict], list[dict]]:
        """(MECH-B, MECHB33) The Companion limit (models.companion_limit / companion_plan): the build's Companion groups
        past its limit deal nothing, and the per-different-Companion line reads the kept count. Each group's Full DPS
        share is PoB's Full DPS with and without the group in it (put back after)."""
        groups = {}
        for s in skills:
            if not s.get("companion") or s.get("disabled") or s.get("group") is None or s["name"] in models.COMPANION_FREE:
                continue
            kind, names = groups.get(s["group"], (None, []))
            if kind is None or s["name"].startswith("Companion:"):  # (Tame Beast's beast names the type)
                kind = s["name"]
            groups[s["group"]] = (kind, names + [s["name"]])
        enabled = {g["index"]: g for g in self.build.groups() if g["enabled"]}
        groups = {i: v for i, v in groups.items() if i in enabled}
        limit, raiser = models.companion_limit(texts)
        rows = []
        if len(groups) > 1:
            full = number(self.build.call("stats", fields=["FullDPS"]).get("FullDPS"))
            for index, (kind, _) in groups.items():
                share = 0.0
                if enabled[index].get("fullDPS"):
                    off = self.build.call("setGroup", index=index, fullDPS=False, fields=["FullDPS"])["stats"]
                    self.build.call("setGroup", index=index, fullDPS=True, fields=[])
                    share = max(full - number(off.get("FullDPS")), 0.0)
                rows.append((index, kind, share, main is not None and main.group == index))
        else:
            rows = [(i, k, 0.0, True) for i, (k, _) in groups.items()]
        kept, dropped = models.companion_plan(rows, limit)
        mods, notes = models.companion_per_type_model(texts, len(kept))
        if dropped:
            for index in dropped:
                mods += [models.line("Minions deal 1% more Damage", -100, skill=name) for name in groups[index][1]]
            names = ", ".join(groups[i][0] for i in dropped)
            notes.append({"label": "Companion limit: Companions past it deal nothing*", "value": len(dropped),
                          "basis": f"{len(groups)} Companion groups, limit {limit:g} ({raiser}), one per type: kept "
                                   f"{', '.join(groups[i][0] for i in kept)}; not counted: {names} (PoB has no limit and "
                                   "counted them; their buffs aren't removed, a small overcount where they carry one)",
                          "play": (f"Only {limit:g} Companion(s) of different types can be out: without a raiser "
                                   f"{names} can't be summoned. A limit raiser (Trusted Kinship, Yriel's Fostering, "
                                   "Sylvan's Effigy) would allow more"),
                          "confidence": "HIGH (the limit, KeywordPopups); MEDIUM (which skills count, what a type is)"})
        elif len(groups) > 1:
            notes.append({"label": "Companions within the limit", "value": len(kept),
                          "basis": f"{len(kept)} Companions of different types, allowed by {raiser}",
                          "confidence": "HIGH"})
        return mods, notes

    def untether_inputs(self, index: int | None) -> tuple[float, float, float, float, float, float]:
        """(MECH-B, MECHB32) Untether's (need, seeds, duration, hit, casts/s, PoB's TotalDPS) for its group `index`:
        need = its threshold % of PoB's EnemyAilmentThreshold; seeds, threshold from its level stats (models.UNTETHER_STATS,
        else the stat set's, UNTETHER_DEFAULTS); duration, hit (AverageHit), casts (speed) from useRates for the group."""
        build = self.build
        threshold, seeds, duration = models.UNTETHER_DEFAULTS
        raw = build.call("activeSkills", levelStats=[models.UNTETHER])["skills"]
        level = next((s.get("levelStats") or {} for s in raw if s["name"] == models.UNTETHER and s.get("levelStats")), {})
        threshold = number(level.get(models.UNTETHER_STATS[0])) or threshold
        seeds = number(level.get(models.UNTETHER_STATS[1])) or seeds
        rate = {}
        if index is not None:
            group = next((g for g in build.groups() if g["index"] == index), None)
            active = next((i for i, s in enumerate((group or {}).get("skills") or [], start=1)
                           if s["name"] == models.UNTETHER), 0)
            got = build.call("useRates", indices=[index], actives=[active])["rates"] if active else []
            rate = got[0] if got else {}
        need = threshold / 100 * number(build.call("stats", fields=["EnemyAilmentThreshold"]).get("EnemyAilmentThreshold"))
        return (need, seeds, number(rate.get("duration")) or duration, number(rate.get("AverageHit")),
                number(rate.get("speed")), number(rate.get("TotalDPS")))

    def untether_main(self, skills: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """(MECH-B, MECHB32) Untether as the main skill: PoB's casts cut to its explosions (models.untether_rate), the
        build's other damage (Full DPS less Untether's hits, plus the damage computed outside PoB) and the earlier seeds'
        explosions filling each seed. As living_bomb_main, a "less Attack and Cast Speed" line on it."""
        if main != models.UNTETHER or not any(s["name"] == models.UNTETHER and not s["disabled"] for s in skills):
            self.untether_speed = 1.0
            return [], []
        group = self.build.main_group() or {}
        need, seeds, duration, _, _, _ = self.untether_inputs(group.get("index"))
        stats = self.build.call("stats", fields=["Speed", "FullDPS", "TotalDPS"])
        prior = getattr(self, "untether_speed", 1.0) or 1.0
        speed = number(stats.get("Speed")) / prior  # (without this line, which is on PoB's numbers)
        hits = number(stats.get("TotalDPS"))
        hit = hits / number(stats.get("Speed")) if number(stats.get("Speed")) > 0 else 0.0
        added = number(getattr(getattr(self.build, "objective", None), "added_dps", 0.0))
        feed = max(number(stats.get("FullDPS")) - hits, 0.0) + max(added, 0.0)
        # (MECHB33; RESEARCH26: one seed per enemy is the headline, the lower case; several seeds the other row)
        rate, how = models.untether_rate(need, feed, hit, 1.0, duration, speed)
        several, _ = models.untether_rate(need, feed, hit, seeds, duration, speed)
        how += (f" (one seed per enemy, UNVERIFIED either way); if {seeds:g} seeds can sit on the boss: {several:.3f} "
                "explosions/s" + (f", {min(1.0, several / speed):.1%} of PoB's casts" if speed > 0 else ""))
        share = max(rate / speed, LIVING_BOMB_FLOOR) if speed > 0 else 1.0
        self.untether_speed = share
        if share >= 1.0:
            return [], []
        return ([models.line("1% more Attack and Cast Speed", -100 * (1 - share), skill=models.UNTETHER)],
                [{"label": "Untether: one explosion per seed filled*", "value": f"{share:.1%} of PoB's casts",
                  "basis": how + " (PoB counts an explosion every cast)",
                  "play": ((f"plant a new seed as each one explodes; the build's other damage sets how fast it fills. "
                            f"One seed per enemy (counted): {share:.1%} of PoB's casts explode; if several seeds (up to "
                            f"{seeds:g}) can sit on the boss: {min(1.0, several / speed) if speed > 0 else 0.0:.1%} "
                            "(which holds is UNVERIFIED)")
                           if rate > 0 else "the build's other damage is too low for a seed to explode within its "
                           "duration: Untether deals nothing here"),
                  "confidence": "MEDIUM (game files' stats); one seed per enemy taken, the lower case (UNVERIFIED)"}])

    def wanted(self) -> tuple[list[dict], list[dict]]:
        skills = self.build.call("activeSkills", levelStats=[
            models.IRON_WARD, models.FEROCIOUS_ROAR, models.SUMMON_WOLF, models.RAPID_SHOT, models.APOCALYPSE,
            KALGUUR, *models.gem_stat_skills()],
            supportStats=[*models.gem_stat_support_stats(), models.TOTEM_ONE_STAT,
                          models.CASCADES_PER_SIDE])["skills"]  # (MECH-B: A23-3; MECHB36 Spell Cascade)
        mods, notes, self.extra_groups = self.models(skills)
        explosion = next((s for s in skills if s["name"] == "Transmutation Explosion"), None)
        if explosion is not None:
            supply, used = self.remnant_supply(skills)
            lines = self.build.call("sourceLines")["lines"]
            extra = sum(float(m.group(1)) for line in lines if (m := ADDITIONAL_REMNANT.search(line["text"]))) / 100.0
            supply *= 1.0 + extra
            twice = any(REAPPEAR.search(line["text"]) for line in lines)
            if twice:
                supply *= 2.0
            duplicate = duplicate_pickups([line["text"] for line in lines])
            supply *= 1.0 + duplicate
            cooldown = explosion.get("cooldown") or 0.0
            cap = (explosion.get("cooldownRecovery") or 1.0) / cooldown if cooldown else math.inf
            rate = min(supply, cap)
            mods += [
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggerRate", "value": rate},
                {"kind": "SkillData", "skill": "Transmutation Explosion", "key": "triggered", "value": True},
            ]
            basis = ", ".join(used) if used else "none of the build's skills makes Remnants on a lone boss"
            notes.append({
                "label": "Explosive Transmutation explosions per second",
                "value": round(rate, 3),
                "basis": f"Remnants {supply:.2f}/s from {basis}"
                         + (f", +{extra:.0%} additional Remnants" if extra else "")
                         + (", each collected twice (they reappear once)" if twice else "")
                         + (f", {duplicate:.0%} of pickups counting twice" if duplicate else "")
                         + f"; cooldown cap {cap:.2f}/s",
                "confidence": "game files' rates; assumed cadences as labelled (launch-test the pickup rate)",
            })
        # Fallback for any other triggered main skill with a cooldown whose trigger PoB doesn't
        # model (it calculates 0 uses per second): assume it fires whenever the cooldown allows
        main = self.main_skill()
        info = next((s for s in skills if s["name"] == main), None)
        flare = self.mana_flare(skills)
        if flare is not None:
            mods += flare[0]
            notes.append(flare[1])
        glory = self.glory_main(skills, main)
        if glory is not None:
            mods += glory[0]
            notes.append(glory[1])
        apocalypse_mods, apocalypse_notes = self.apocalypse(skills)  # (MECH-B: Apocalypse's disasters, MECHB7)
        mods, notes = mods + apocalypse_mods, notes + apocalypse_notes
        temper_mods, temper_notes = self.temper_weapon(skills)  # (GEM19: Temper Weapon's Combust, Smith of Kitava)
        mods, notes = mods + temper_mods, notes + temper_notes
        if glory is None:
            glory = self.flask_main(skills, main)  # (also a use rate set from a resource; it rules out the fallback)
            if glory is not None:
                mods += glory[0]
                notes.append(glory[1])
        if (not (flare is not None and main == models.MANA_FLARE) and glory is None
                and info is not None and main not in REMNANT_CONSUMERS and info.get("triggered") and info.get("cooldown")
                and not info.get("corpses")  # (models.corpse_model rates those)
                and (main in self.cooldown_fallback
                     or not self.build.call("stats", fields=["Speed"]).get("Speed"))):
            self.cooldown_fallback.add(main)
            rate = (info.get("cooldownRecovery") or 1.0) / info["cooldown"]
            mods += [
                {"kind": "SkillData", "skill": main, "key": "triggerRate", "value": rate},
                {"kind": "SkillData", "skill": main, "key": "triggered", "value": True},
            ]
            notes.append({
                "label": f"{main} uses per second", "value": round(rate, 3),
                "basis": f"fires whenever its {info['cooldown']:g}s cooldown allows (its trigger isn't modeled in PoB; "
                         "assumes the build provides the trigger often enough)",
                "confidence": "theory",
            })
        group = self.build.main_group()
        infused = sorted({label for skill in (group or {}).get("skills", []) for label in skill.get("statSets") or []
                          if INFUSED_SET.search(label)})
        chosen = self.infused if self.infused is not None and self.infused["label"] in infused else None
        if chosen is not None:
            mods.append(models.line("1% more Damage", 100.0 * (chosen["factor"] - 1.0), skill=main))
            notes.append({"label": f"{main}: share of casts that are {chosen['label']}", "value": round(chosen["share"], 2),
                          "basis": chosen["basis"] + "; the rest are the plain cast, so its damage counts as plain + "
                                   f"{chosen['share']:.0%} of the Infused cast's gain",
                          "play": "Collect each Infusion Remnant as it appears; one left lying stops counting.",
                          "confidence": "game files' rates; assumed cadences as labelled (launch-test the pickup rate)"})
        for label in infused:
            if chosen is None or label != chosen["label"]:
                notes.append({"label": f"{main}: {label} cast not counted", "value": 0,
                              "basis": self.infused_skipped.get(label, INFUSED_NOTE), "confidence": "theory"})
        self.press_busy = 0.0
        if explosion is not None or chosen is not None:
            press_mods, press_notes = self.remnant_presses(skills, main)
            mods, notes = mods + press_mods, notes + press_notes
            self.press_busy = press_notes[0]["value"] if press_notes else 0.0
        bomb_mods, bomb_notes = self.living_bomb_main(skills, main)
        mods, notes = mods + bomb_mods, notes + bomb_notes
        tether_mods, tether_notes = self.untether_main(skills, main)  # (MECH-B, MECHB32)
        mods, notes = mods + tether_mods, notes + tether_notes
        self.ledger_shares = {}  # (COV11; set again by ledger_conditions)
        found, condition_notes = self.ledger_conditions(skills)
        # (SEARCH10, the manager's ruling: a part-time ledger condition counts at its share, in PoB's stats; the search's
        # part-time weighing switches it off through ledger_switch, so the defence floors read it at 0: Search._part_time)
        for name, share in sorted(getattr(self, "ledger_part_time", {}).items()):
            if getattr(self, "ledger_switch", {}).get(name, True) and share > 0:
                found.append(name)
                self.ledger_shares[name] = round(100 * share)
        if getattr(self, "soul_mantle_part_time", 0.0) > 0:  # (MECHC38: Soul Mantle's curses, as the Shock below)
            self.ledger_part_time[recovery_mod.SOUL_MANTLE_FLOOR_KEY] = self.soul_mantle_part_time
        if getattr(self, "boss_shock_part_time", 0.0) > 0:  # (MECHC38: after the loop above, which isn't for it)
            from builder.search import boss_hits

            self.ledger_part_time[boss_hits.BOSS_SHOCK_FLOOR_KEY] = self.boss_shock_part_time
        mods += [{"kind": "Condition", "name": name} for name in found]
        mods += [models.raw_mod(f"Multiplier:LedgerShare{name}", "BASE", percent)
                 for name, percent in sorted(self.ledger_shares.items())]
        mods += getattr(self, "ledger_derived", [])  # (COV32: shares the bridge works out again per calculation)
        mods, ward_notes = self.ward_paid(skills, mods, main)  # (MECH-B: Runic Ward pays for Ward-cost skills, MECHB13)
        notes += ward_notes
        texts = [e["text"] for e in self.build.call("sourceLines")["lines"]]
        if any(models.BODYGUARDS.search(t.strip()) for t in texts):  # (MECHB31: Warding Rune of Bodyguards, at the Low Ward share)
            low, _, how = ward_shares(self.build)
            guard_mods, guard_notes = models.bodyguards_model(texts, low, how)
            mods, notes = mods + guard_mods, notes + guard_notes
        self.full_guard_mods = []  # (MECHC38: Guard up all fight, set by life_mana_income: the manager's ruling (ii))
        notes += self.life_mana_income(skills, main)  # (MECH-C: Life / Mana from kills, Remnants, charms, events; MECHC12)
        mods += self.full_guard_mods
        if self.full_guard_mods and any(m.get("tag") == "JadeHeritage" for m in mods):
            # (MECHC38; RESEARCH31 R31-4, research/jade-guard-glowswarm-amount.md: ANY Guard stops Jade building, MEDIUM)
            # Guard up all fight: Jade Heritage's stacks never build, so its 10% isn't counted (an OVERCOUNT otherwise)
            mods = [m for m in mods if m.get("tag") != "JadeHeritage"]
            notes = notes + [{"label": "Jade Heritage: no Jade under Guard up all fight*", "value": "0 Jade",
                              "basis": ("your Guard is up all fight, and any Guard stops Jade stacks building (Encase in "
                                        "Jade: 'You cannot gain Jade stacks while you have Guard'; RESEARCH31 R31-4, "
                                        "MEDIUM): Jade Heritage's 10% additional Physical Damage Reduction not counted; "
                                        "without the Guard: 10%"),
                              "confidence": "MEDIUM (R31-4)", "play": ""}]
        zenith_mods, zenith_notes = self.zenith(skills, main)  # (MECH-C: Zenith's "above 90% of maximum Mana", MECHC26)
        mods += zenith_mods
        notes += zenith_notes
        purple_mods, purple_notes = self.purple_flames(skills)  # (MECH-C: Into the Breach's Purple Flame stacks, MECHC26)
        mods += purple_mods
        notes += purple_notes
        demon_mods, demon_notes = self.demon_form(skills, main)  # (MECH-C: Demon Form's Demonflame and time, MECHC28)
        mods += demon_mods
        notes += demon_notes
        soul_mods, soul_notes = self.soul_eater()  # (MECH-C: Soul Eater's souls from the build's hits, MECHC29)
        mods, notes = mods + soul_mods, notes + soul_notes
        holy_mods, holy_notes = self.consecrated_ground()  # (MECH-C: your Consecrated Ground, MECHC30, MD-3)
        mods, notes = mods + holy_mods, notes + holy_notes
        stun_mods, stun_notes = self.heavy_stunned_self(main)  # (MECH-C: Heavy Stun on you while channelling a raised shield, MECHC30)
        mods, notes = mods + stun_mods, notes + stun_notes
        turtle_mods, turtle_notes = self.turtle_charm(texts, mods)  # (MECH-C: Turtle Charm's two readings, MECHC32)
        mods, notes = mods + turtle_mods, notes + turtle_notes
        panther_mods, panther_notes = self.evasion_doublings(texts)  # (MECH-C: Stalking Panther doubles once, MECHC32)
        mods, notes = mods + panther_mods, notes + panther_notes
        frost_mods, frost_notes = self.frostflame(main, skills)  # (MECH-B: Frostflame Nova's ice while Ignited, MECHB13)
        mods, notes = mods + frost_mods, notes + frost_notes
        # the boss the numbers are scored against (MECH50: bridge resetConfig sets its resistances; bosses.py)
        from builder.search import bosses

        from builder.search import boss_hits  # (MECHC6: how hard it hits, per damage type)
        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        return mods, [bosses.note(boss), boss_hits.hits_note(boss)] + notes + condition_notes + self.unbuilt_grants(skills)  # (the boss first)

    def ward_paid(self, skills: list[dict], mods: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """Runic Ward pays for the Ward-cost skills (MECHB13, models.ward_plan): every Ward-cost skill the build uses
        (a model's triggerRate, the main skill, a pressed or other Full DPS group) shares the build's Runic Ward
        regeneration; each is held to its planned rate x the share it pays for. build.ward_sustain (for the search:
        {skill: (planned uses/s, paid uses/s, Ward a use)}, plus "_regen" and "_share") says what it found."""
        build = self.build
        build.ward_sustain = {}
        costly = [s for s in skills if s.get("wardCost") and not s["disabled"] and s.get("group")]
        if not costly:
            return mods, []
        by_index = {g["index"]: g for g in build.groups()}
        main_group = build.main_group()
        main_index = main_group["index"] if main_group else None
        pairs = []
        for s in costly:
            group = by_index.get(s["group"])
            if group is None:
                continue
            position = next((i for i, x in enumerate(group["skills"], start=1) if x["name"] == s["name"]), 0)
            pairs.append((s, group, position))
        if not pairs:
            return mods, []
        rates = build.call("useRates", indices=[g["index"] for _, g, _ in pairs],
                           actives=[p for _, _, p in pairs])["rates"]
        by_pair = {(r["index"], r.get("active") or 0): r for r in rates}
        triggered = {}
        for mod in mods:
            if mod.get("kind") == "SkillData" and mod.get("key") == "triggerRate":
                triggered[mod["skill"]] = number(mod.get("value"))
        scales = getattr(build.objective, "press_scales", None) or {}
        uses, skipped = [], []
        for s, group, position in pairs:
            rate = by_pair.get((group["index"], position)) or {}
            cost = number(rate.get("wardCost"))
            if cost <= 0:
                continue
            speed = number(rate.get("speed"))
            name = s["name"]
            if name in triggered:
                uses.append(models.WardUse(name, "triggered", triggered[name], cost, group["index"]))
            elif group["index"] == main_index and name == main:
                held = 1.0  # (the press hook's DPS "more" on the main skill, main_cap: its rate after Combo / walking)
                for mod in mods:
                    if mod.get("kind") == "Raw" and mod.get("name") == "DPS" and mod.get("type") == "MORE"                             and mod.get("skill") == name:
                        held *= 1 + number(mod.get("value")) / 100
                uses.append(models.WardUse(name, "main", speed * held, cost, group["index"]))
            elif rate.get("name") in scales and group.get("fullDPS"):
                uses.append(models.WardUse(rate["name"], "pressed", speed * scales[rate["name"]], cost, group["index"]))
            elif group.get("fullDPS") and group.get("enabled", True):
                uses.append(models.WardUse(name, "full", speed, cost, group["index"]))
            else:
                skipped.append(name)
        from builder.search import bosses

        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        pools = {**build.call("stats", fields=list(dict.fromkeys(
            ["WardRegenRecovery", "Ward", "WardOnBlock", "EffectiveBlockChance", "enemySkillTime", *models.MODEL_FIELDS]))),
                 **bosses.kill_stats(boss)}  # (the request's boss: its Life for Light Stuns, its cadence, its adds)
        texts = [line["text"] for line in build.call("sourceLines")["lines"]]
        kalguur = next((dict(s.get("levelStats") or {}) for s in skills if s["name"] == KALGUUR and not s["disabled"]),
                       None)
        if kalguur is not None:  # (the build's extra Remnants and pickups, as the explosions count them)
            extra = sum(float(m.group(1)) for t in texts if (m := ADDITIONAL_REMNANT.search(t))) / 100.0
            kalguur["multiplier"] = ((1.0 + extra) * (2.0 if any(REAPPEAR.search(t) for t in texts) else 1.0)
                                     * (1.0 + duplicate_pickups(texts)))
        kills = getattr(build, "kills", None) or (0.0, 0.0, "")  # (models.kill_rate: the boss preset's adds, own minions)
        melee = bool(next((s for s in skills if s["name"] == main), {}).get("melee"))
        stuns, stuns_how = models.stun_rate(pools, melee) if kalguur is not None else (0.0, "")
        regen, pool = number(pools.get("WardRegenRecovery")), number(pools.get("Ward"))
        feeds = self.ward_feeds(texts, main, uses, mods, skills)  # (MECHB14: charms, Life flasks, Life sacrificed on Attack)
        income, parts, unread = models.ward_income(
            regen, pool, texts, kalguur=kalguur, stuns=stuns, stuns_how=stuns_how,
            kills=(number(kills[0]), number(kills[1])), on_block=number(pools.get("WardOnBlock")),
            block=number(pools.get("EffectiveBlockChance")), every=bosses.benefit_skill_time(pools),  # (a "when hit" benefit, MECHD4)
            **feeds)
        share, notes = models.ward_plan(uses, income, pool, parts, unread)
        build.ward_sustain = {u.name: (u.rate, u.rate * share, u.cost) for u in uses}
        # (the total, every source: the one field SEARCH reads; its breakdown is in the note)
        build.ward_sustain.update({"WardRecoveryPerSecond": income, "_share": share})
        if skipped and notes:
            notes[0]["basis"] += f". Not counted against it: {', '.join(sorted(set(skipped)))} (not used for damage here)"
        if share >= 1.0:
            return mods, notes
        out = []
        for mod in mods:
            if mod.get("kind") == "SkillData" and mod.get("key") == "triggerRate"                     and any(u.kind == "triggered" and u.name == mod.get("skill") for u in uses):
                mod = {**mod, "value": models._round(number(mod.get("value")) * share)}
            out.append(mod)
        for u in uses:
            if u.kind in ("main", "full"):
                out.append(models.raw_mod("DPS", "MORE", 100 * (share - 1), u.name))
            elif u.kind == "pressed":  # (its Full DPS entry and its ailments' entries, a lower bound for the stacks)
                for key in [k for k in scales if k == u.name or k.endswith(f" from {u.name}")]:
                    scales[key] *= share
        return out, notes

    def ward_feeds(self, texts: list[str], main: str | None, uses: list, mods: list[dict], skills: list[dict]) -> dict:
        """The rates three Runic Ward sources read (models.ward_income's keywords; MECHB14), only for a build with the
        line: Charm uses a second (flasks.uses_per_second over the charm slots), the Life flask's average recovery
        (flasks.recovery_per_second, build.extra_stats' FlaskLifeRecoveryRate) and PoB's always-running flask part
        inside WardRegenRecovery, and the main skill's attacks a second with the Life recovery that pays for a sacrifice."""
        from builder.search import flasks

        build, out = self.build, {}
        lowered = [t.lower() for t in texts]
        has = lambda pattern: any(pattern.search(t) for t in lowered)  # noqa: E731
        if not (has(models.WARD_ON_CHARM) or has(models.WARD_FROM_FLASK) or has(models.WARD_SACRIFICE)):
            return out
        gain = flasks.Charges.of(build)
        equipped = getattr(build, "equipped", {}) or {}
        if has(models.WARD_ON_CHARM):
            total, how = 0.0, []
            for slot in flasks.CHARM_SLOTS:
                option = equipped.get(slot)
                if option is None:
                    continue
                info = build.call("flaskData", raws=[option.raw])["results"][0]
                rate, why = flasks.uses_per_second(info or {}, gain, option.raw, None)
                total += rate
                how.append(f"{option.name} {rate:.3f}/s: {why}")
            out.update(charm_uses=total, charm_how="; ".join(how))
        if has(models.WARD_FROM_FLASK):
            pct = models.ward_flask_percent(texts)
            slot = next(k for k, v in flasks.FLASK_SLOTS.items() if v == "Life")
            option = equipped.get(slot)
            info = build.call("flaskData", raws=[option.raw])["results"][0] if option is not None else None
            out["flask_life"] = flasks.recovery_per_second(info, gain, "Life", option.raw, own=True) if info else 0.0
            active = (build.call("chargeGain").get("active") or {}).get(slot) if info else False
            length = number((info or {}).get("duration"))
            if active and length > 0:  # (PoB's CalcPerform 1642: the flask's whole recovery over its duration, always)
                out["pob_flask_ward"] = (number(info.get("lifeTotal")) * (1 + gain.life_recovery_inc / 100) / length
                                         * pct / 100)
        if has(models.WARD_SACRIFICE):
            skill = next((u for u in uses if u.kind == "main"), None)
            stats = build.call("stats", fields=["Life", "LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate"])
            attacks = 0.0
            group = build.main_group()
            info = next((s for s in skills if s["name"] == main and not s["disabled"]), {}) if main else {}
            if group is not None and info.get("attack"):
                if skill is not None:
                    attacks = skill.rate  # (already after the press hook)
                else:
                    attacks = number(next(iter(build.call("useRates", indices=[group["index"]])["rates"]), {})
                                     .get("speed"))
                    for mod in mods:
                        if mod.get("kind") == "Raw" and mod.get("name") == "DPS" and mod.get("type") == "MORE" \
                                and mod.get("skill") == main:
                            attacks *= 1 + number(mod.get("value")) / 100
            extra = getattr(build, "extra_stats", None) or {}
            recovery = sum(number(stats.get(k)) for k in ("LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate")) \
                + number(extra.get(flasks.RECOVERY_FIELDS["Life"])) \
                - flasks.pob_part_time_share(build, flasks.Charges.of(build))[0]                 - resisted_leech(build).get("Life", 0.0)  # (MECHC25, A35-20: never twice; MECHC26, H2-1: the boss resists)
            out.update(attacks=attacks, life=number(stats.get("Life")), life_recovery=recovery)
        return out

    def second_wind_life(self, life: float, main: str | None, speed: float) -> tuple[float, str]:
        """(Life a second, how) from Second Wind III (MECHC29; PATCH_CHECK G4-6, an UNDERCOUNT before: PoB's statMap has no
        entry for restore_%_life_on_skill_use_per_1000_ms_cooldown). The game (gem_stat_descriptions.csd): "When a
        Supported Skill is used and goes on cooldown, recover {0}% of maximum Life per second of cooldown", 1 at every
        level (0.4.0: "now has Restore 1% ... (previously 2%)", the newest change to the number; grep_notes --history).
        Each supported skill's uses a second (the main skill at its speed, any other at its group's rate:
        conditions.skill_use_rate, never past one per cooldown) x its cooldown (PoB's, after the build's Cooldown
        Recovery) x 1% of Life. Uses by totems or clones (which 0.5.1 lets it support) aren't counted (an undercount,
        labelled)."""
        build = self.build
        choices = models.press_choices(build)
        groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False and any(
            n["name"] == SECOND_WIND and n.get("enabled", True) for n in g.get("gems") or [])]
        rows = build.call("useRates", indices=[g["index"] for g in groups])["rates"] if groups else []
        main_index = (build.main_group() or {}).get("index")
        total, parts = 0.0, []
        for row in rows:
            cooldown = number(row.get("cooldown"))
            if cooldown <= 0:
                continue
            if row.get("index") == main_index and main:
                uses, why = min(speed, 1 / cooldown), "the main skill"
            else:
                uses, why, _ = conditions_mod.skill_use_rate(row, choices)
                uses = min(uses, 1 / cooldown)
            each = uses * cooldown * SECOND_WIND_PCT / 100 * life
            if each > 0:
                total += each
                parts.append(f"group {row.get('index')}: {uses:.3f} uses/s ({why}) x {cooldown:.2f} s cooldown x "
                             f"{SECOND_WIND_PCT:g}% of {life:,.0f} Life = {each:.1f}/s")
        return total, "Second Wind III: " + ("; ".join(parts) or "no supported skill with a cooldown")

    def life_mana_income(self, skills: list[dict], main: str | None) -> list[dict]:
        """Life and Mana a second from the sources PoB gives per event or not at all (recovery.py; MECHC12, the user's rule
        of 12 Nov: every recovery source counts): kills, Remnants, charms, Warcries, Heavy Stuns, Ignites and Shocks, at
        the build's own rates. build.recovery_sources ({recovery.ALL_FIELDS: per second}) is for the search's sustain and
        recovery floors; the note says where each comes from."""
        from builder.search import bosses, flasks, recovery

        build = self.build
        income = recovery.Income()
        build.recovery_sources = dict(income.rates)
        texts = [line["text"] for line in build.call("sourceLines")["lines"]]
        present = {s["name"]: s for s in skills if not s.get("disabled")}
        remnant_names = [n for n in recovery.REMNANT_SKILLS if n in present]
        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        stats = {**build.call("stats", fields=list(dict.fromkeys([
            "Life", "Mana", "EnergyShield", "LifeOnKill", "ManaOnKill", "IgniteChancePerHit", "ShockChancePerHit", "Tribute",
            *models.MODEL_FIELDS,
            *conditions_mod.EVIDENCE_FIELDS, *CHILL_FIELDS]))), **bosses.kill_stats(boss),
                 **models.dazed_stats(build)}  # (MECH71: the recovery unique's Heavy Stuns read the Dazed share)
        kills = getattr(build, "kills", None) or (0.0, 0.0, "")  # (models.kill_rate: the boss preset's adds)
        enemies = number(kills[0])
        hits = models.hits_per_second(stats)
        lowered = [t.lower() for t in texts]
        has = lambda pattern: any(pattern.search(t) for t in lowered)  # noqa: E731
        recovery.on_kill(income, stats, texts, enemies)
        pickup = has(recovery.REMNANT_PICKUP)
        if remnant_names or pickup:
            raw = build.call("activeSkills", levelStats=remnant_names)["skills"] if remnant_names else []
            infos = {}
            for name in remnant_names:
                found = next((s for s in raw if s["name"] == name and not s.get("disabled")), present[name])
                pool = "Life" if name == recovery.LIFE_REMNANTS else "Mana"
                infos[name] = {"gain": number((found.get("remnantGain") or {}).get(pool)),
                               "levelStats": found.get("levelStats") or {}}
            share, how = 0.0, "no Elemental Ailment on the boss"
            if recovery.MANA_REMNANTS in infos:
                # (the share of the fight the boss carries at least one: the largest one alone, a lower bound: LOW;
                # MECHC26, from MECH75: with the other groups' Ignites and the ground's, build.ailment_others /
                # ailment_ground, as snap_stats; before, the main skill's alone)
                ailing = {**stats, **{k: v for k, v in (("_ailment_others", getattr(build, "ailment_others", None)),
                                                        ("_ailment_ground", getattr(build, "ailment_ground", None))) if v}}
                for word in ("chilled", "shocked", "ignited", "frozen", "electrocuted"):
                    try:
                        each = conditions_mod.target_ailment_share(ailing, word)
                    except (KeyError, TypeError, ZeroDivisionError):
                        continue
                    if each > share:
                        share, how = each, f"{word}, the largest alone (LOW: a lower bound on any Elemental Ailment)"
            # (MECHC15, AUDIT26 A26-3: the pickup lines fire on every Remnant collected, so the build's other Remnant
            # sources count too: Infusion Remnants, Remnants of Kalguur's Ward Remnants, Breach flames, Power Charge
            # Remnants... (KeywordPopups Remnant: "Walking over a Remnant collects it"); COV17: the build's
            # additional-Remnant / reappear / double-pickup lines on every Remnant, the Life / Mana ones included)
            others = self.remnant_supply(skills, skip=recovery.REMNANT_SKILLS) if pickup else (0.0, [])
            multiplier, multiplier_how = remnant_multiplier(texts)
            recovery.remnant_income(income, infos, stats, texts, hits=hits, crits=models.crits_per_second(stats),
                                    ailment_share=share, ailment_how=how, enemies=enemies,
                                    others=(others[0], "; ".join(others[1])), multiplier=multiplier,
                                    multiplier_how=multiplier_how)
        if has(recovery.CHARM_ANY) or any(recovery.CHARM_OWN.match(line.strip()) for option in
                                          [(getattr(build, "equipped", {}) or {}).get(slot) for slot in flasks.CHARM_SLOTS]
                                          if option is not None for line in option.raw.splitlines()):
            gain = flasks.Charges.of(build)
            charms = []
            for slot in flasks.CHARM_SLOTS:
                option = (getattr(build, "equipped", {}) or {}).get(slot)
                if option is None:
                    continue
                info = build.call("flaskData", raws=[option.raw])["results"][0]
                rate, why = flasks.uses_per_second(info or {}, gain, option.raw, None)
                charms.append((option.name, option.raw, rate, why))
            recovery.charm_income(income, charms, stats, texts)
        if has(recovery.MANA_FLASK_LIFE):  # (MECHC33: "Recover N% of maximum Life when you use a Mana Flask")
            option = (getattr(build, "equipped", {}) or {}).get("Flask 2")
            if option is None:
                income.parts.append("Life on Mana flask use: 0, no Mana flask equipped")
            else:
                info = build.call("flaskData", raws=[option.raw])["results"][0]
                uses, uses_how = flasks.uses_per_second(info or {}, flasks.Charges.of(build), option.raw, "Mana")
                recovery.mana_flask_income(income, stats, texts, uses, f"{option.name}: {uses_how}")
        guard_rows: list[dict] = []
        if any(recovery.MANA_FLASK_GUARD.match(t.strip()) for t in texts):
            # (MECHC37, PATCH_CHECK H2-4: Glowswarm's Guard on Mana flask use, a row only: the manager's 17 Nov ruling (b))
            option = (getattr(build, "equipped", {}) or {}).get("Flask 2")
            amount, uses, uses_how = 0.0, 0.0, "no Mana flask equipped"
            if option is not None:
                info = build.call("flaskData", raws=[option.raw])["results"][0] or {}
                gain = flasks.Charges.of(build)
                uses, uses_how = flasks.uses_per_second(info, gain, option.raw, "Mana")
                uses_how = f"{option.name}: {uses_how}"
                amount = flasks.use_amount(info, gain, "Mana", option.raw)
            expiry = (conditions_mod.buff_expiry(texts) if any(conditions_mod.BUFF_EXPIRY.search(t) for t in texts)
                      else None)
            if row := recovery.mana_flask_guard_row(texts, amount, uses, uses_how, number(stats.get("Life")), expiry):
                guard_rows.append(row)
                # (MECHC38, the manager's ruling (ii), 18 Nov: Guard up all fight counts in the floors, in PoB's Guard pool)
                self.full_guard_mods = recovery.full_guard_mods(
                    *recovery.mana_flask_guard(texts, amount, uses, number(stats.get("Life")), expiry))
        equipped = getattr(build, "equipped", {}) or {}
        charm_guards = [(o, m) for slot in flasks.CHARM_SLOTS if (o := equipped.get(slot)) is not None
                        and (m := recovery.CHARM_GUARD.search(o.raw or ""))]
        if charm_guards:  # (MECHC38, COV41: charms' "Also grants N Guard", CharmGuardWhileActive1-6)
            gain = flasks.Charges.of(build)
            usable = {id(o) for slot in flasks.CHARM_SLOTS[:max(gain.charm_limit, 0)] if (o := equipped.get(slot))}
            charm_guards = [(o, m) for o, m in charm_guards if id(o) in usable]
            infos = build.call("flaskData", raws=[o.raw for o, _ in charm_guards])["results"] if charm_guards else []
            for (option, m), info in zip(charm_guards, infos):
                up = flasks.uptime(info or {}, gain, None, option.raw)
                guard, row = recovery.charm_guard_row(option.name, float(m.group(1)), texts, up,
                                                      number(stats.get("Life")))
                guard_rows.append(row)
                whole = recovery.full_guard_mods(guard, up)
                held = sum(number(x.get("value")) for x in self.full_guard_mods if x.get("name") == "GuardAbsorbLimit")
                if whole and guard > held:  # (one Guard buff at a time: the largest up all fight counts)
                    self.full_guard_mods = whole
        warcries, warcry_how = 0.0, ""
        if has(recovery.WARCRY_BOTH) or has(recovery.WARCRY_LIFE):
            main_types = set((build.ctx.gems.get(main) or {}).get("skillTypes") or []) if main else set()
            events = conditions_mod.event_rates(
                build.call("groups")["groups"], build.ctx.gems, main or "", main_types, number(stats.get("Speed")),
                lambda idx: build.call("useRates", indices=idx)["rates"], texts, models.press_choices(build))
            warcries, warcry_how = events.get("Warcry", (0.0, ""))
        heavy, heavy_how = models.heavy_stun_rate(stats) if has(recovery.HEAVY_STUN_UNIQUE) else (0.0, "")
        recovery.event_income(income, stats, texts, warcries=warcries, warcry_how=warcry_how, heavy_stuns=heavy,
                              heavy_how=heavy_how, hits=hits)
        if any(has(p) for p in recovery.SPENT_LINES):
            recovery.spent_income(income, stats, texts, **self.spent_rates(skills, main, stats, lowered))
        if any(has(p) for p in recovery.HIT_LINES):
            # (MECHC16, AUDIT28 A28-1: the Life the sources above give a second refills Life between the boss's hits)
            recovery.hit_income(income, stats, texts, **self.hit_rates(main, stats, before=income.pool("Life")))
        if any(g.get("name") == SECOND_WIND and g.get("enabled", True) for grp in (build.groups() if hasattr(build, "groups") else [])
               for g in grp.get("gems") or []):  # (MECHC29, PATCH_CHECK G4-6) Second Wind III's Life per use
            life_s, wind_how = self.second_wind_life(number(stats.get("Life")), main, number(stats.get("Speed")))
            if life_s > 0:
                income.add("LifeOnEventRecovery", life_s, wind_how)
        if recovery.GHOST_DANCE in present:  # (MECHC27) Ghost Dance's ES regeneration while a Shroud was lost recently
            from builder.search import boss_hits

            ghost = next((s for s in build.call("activeSkills", levelStats=[recovery.GHOST_DANCE])["skills"]
                          if s["name"] == recovery.GHOST_DANCE and not s.get("disabled")), {})
            seen = {**stats, **build.call("stats", fields=["Evasion", "EvadeChance", "EffectiveBlockChance",
                                                           "enemySkillTime"]), "_boss": boss}
            landed, landed_how = boss_hits.gain_hits(seen)
            recovery.ghost_dance_income(income, ghost.get("levelStats") or {}, number(seen.get("Evasion")), landed,
                                        f"the boss's landed hits {landed_how}")
        if has(recovery.SACRIFICE_ES):  # (MECHC27) Eldritch Empowerment's ES sacrificed per Spell cast
            spell = main and "Spell" in set((build.ctx.gems.get(main) or {}).get("skillTypes") or [])
            casts = number(stats.get("Speed")) if spell else 0.0
            recovery.sacrifice_income(income, texts, casts, number(stats.get("EnergyShield")),
                                      f"{casts:.2f} casts/s of {main}" if spell else "the main skill isn't a Spell (0)")
        starts = self.es_flask_income(income, texts, lowered)
        recovery.restrict(income, texts)
        income.unread = recovery.unread(texts) + [name for name, pattern in flasks.ES_STARTS_UNREAD.items()
                                                  if any(pattern.search(t) for t in lowered)]
        drain, drain_how = 0.0, ""
        if any(o is not None and any(flasks.NO_WARD_DRAIN.match(t.strip()) for t in (o.raw or "").splitlines())
               for o in ((getattr(build, "equipped", None) or {}).get(slot) for slot in flasks.FLASK_SLOTS)):
            drain, drain_how = flasks.flask_life_drain(build, flasks.Charges.of(build), number(stats.get("Life")),
                                                       number(build.call("stats", fields=["Ward"]).get("Ward")))
        # (MECHC38, COV41's OVERCOUNT: a magic flask's "Removes N% of Mana Recovered from Life when used" and its Life twin)
        cross, cross_how = flasks.flask_cross_cost(build, flasks.Charges.of(build)) if any(
            o is not None and any(flasks.CROSS_COST.match(t.strip()) for t in (o.raw or "").splitlines())
            for o in ((getattr(build, "equipped", None) or {}).get(slot) for slot in flasks.FLASK_SLOTS)) else ({}, "")
        drain += cross.get("Life", 0.0)
        drain_how = "; ".join(h for h in (drain_how, cross_how) if h)
        if drain > 0:  # (MECHC26: Uhtred's Chalice's "Lose 5% Life per second while you have no Runic Ward")
            income.rates["LifeFlaskDrain"] = -drain
            income.parts.append(f"lost to flasks: {drain_how}")
        if cross.get("Mana", 0.0) > 0:
            income.rates["ManaFlaskDrain"] = -cross["Mana"]
            if not drain:
                income.parts.append(f"lost to flasks: {drain_how}")
        leech_stats = build.call("stats", fields=list(recovery.LEECH_FIELDS))
        leech = recovery.leech_income(income, leech_stats, enemy_level(build))
        breach = present.get(BREACH)
        if breach is not None and (breach.get("remnantGain") or {}).keys() & {"FlameLife", "FlameMana"}:
            # (MECHC26, H1-9) Into the Breach's Red / Blue Flames of Chayula leech on collection: at its flame rate and
            # the build's Remnant multiplier lines (remnant_multiplier, as the pickup lines read them)
            multiplier, multiplier_how = remnant_multiplier(texts)
            recovery.flame_income(income, {**stats, **leech_stats}, REMNANT_SOURCES[BREACH][0] * multiplier,
                                  breach["remnantGain"], texts,
                                  "one per 1.21 s" + (f", {multiplier_how}" if multiplier_how else ""))
        if hasattr(build, "groups") and (rift := temporal_rift_uses(build)) is not None and rift[0] > 0:
            # (MECHC29; the manager 13:14) Temporal Rift's reset of Life / Mana / ES to 4 s ago, on the net drains with
            # the sources counted so far
            from builder.search import objective

            added = getattr(build, "added_stats", None)
            seen = {**build.call("stats", fields=sorted(set(objective.RECOVERY_INPUTS) | {
                "Life", "LifePerSecondCost", "ManaPerSecondCost", "EnergyShield", "ChaosInoculation"})),
                    **(added() if callable(added) else {}), **income.rates}
            recovery.rift_income(income, seen, rift[0], rift[1])
        build.recovery_sources = {**income.rates, flasks.ES_STARTS_FIELD: starts}
        return recovery.note(income) + leech + guard_rows

    def soul_eater(self) -> tuple[list[dict], list[dict]]:
        """Soul Eater's average souls as "1% increased Skill Speed" x souls (the comment at SOUL_MAX)."""
        build = self.build
        texts = [line["text"].strip() for line in build.call("sourceLines")["lines"]]
        weak = any(SOUL_OPEN_WEAKNESS.match(t) for t in texts)
        unique = [float(m.group(1)) for t in texts if (m := SOUL_UNIQUE.match(t))]
        if not weak and not unique:
            return [], []
        from builder.search import bosses, conditions as conditions_mod

        cap = SOUL_MAX
        for t in texts:
            if m := SOUL_MAX_SET.match(t):
                cap = int(m.group(1))
        cap += sum(int(m.group(1)) for t in texts if (m := SOUL_MAX_PLUS.match(t)))
        stats = build.call("stats", fields=["Speed", "HitChance", "FullDPS", "CombinedDPS"])
        hits = models.hits_per_second(stats)
        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        life = bosses.PRESETS.get(boss, bosses.PRESETS[bosses.DEFAULT]).life
        dps = number(stats.get("FullDPS")) or number(stats.get("CombinedDPS"))
        fight = min(life / dps, 1800.0) if dps > 0 else 0.0
        every = min(unique) if unique else 0.0
        always = min(hits, 1 / every) if every > 0 else 0.0
        parts = []
        if unique:
            parts.append(f"a Unique hit eats one at most every {every:g} s: {always:.2f}/s all fight")
        share, how = 0.0, ""
        if weak:
            reader = getattr(conditions_mod, "open_weakness_up", None)
            if reader is not None:
                share, how = reader(build)
            elif any(SOUL_REVEAL.match(t) for t in texts):
                share, how = 0.45, "COV33's 45% (the weak segments are 45% of the boss's Life)"
            else:
                how = "nothing reveals the boss's weaknesses"
            parts.append(f"each hit on an Open Weakness eats one ({hits:.2f} hits/s) while the boss has one, "
                         f"{share:.0%} of the fight ({how}), in 3 or 4 segments (poe2wiki; even spacing assumed)")
        if weak and share > 0:
            inside = max(hits, always)
            souls = sum(soul_eater_average(fight, even_segments(fight, share, k), inside, always, cap)
                        for k in SOUL_SEGMENTS) / len(SOUL_SEGMENTS)
        else:
            souls = soul_eater_average(fight, None, always, always, cap)
        note = {"label": "Soul Eater: souls eaten from your hits*", "value": f"{souls:.1f} souls (+{souls:.1f}% Skill Speed)",
                "confidence": "MEDIUM",
                "basis": ("; ".join(parts) + f"; at most {cap}, one lost every 0.5 s after 4 s without one; over a "
                          f"{fight:,.0f} s fight ({life:,} Life / {dps:,.0f} DPS) from 0: {souls:.1f} on average; 1% "
                          "increased Skill Speed each (the game's rule, not PoB's stale 5% / 45). The main skill's hits "
                          "only")}
        if souls <= 0:
            return [], [note]
        return [models.line(SOUL_LINE, souls)], [note]

    def consecrated_ground(self) -> tuple[list[dict], list[dict]]:
        """Your Consecrated Ground's Life regeneration and reduced Curse effect at the share of the fight you stand in it
        (the comment at CONSECRATED_SECONDS)."""
        from builder.search import flasks

        build = self.build
        texts = [line["text"].strip() for line in build.call("sourceLines")["lines"]]
        sources, parts = [], []
        # (a real Build has groups(); the tests' stand-in builds don't, as MECHC29's Second Wind III guard)
        for gem in (CONSECRATE_SKILL, HOLY_DESCENT) if hasattr(build, "groups") else ():
            found = group_uses(build, gem)
            if found is not None:
                uses, how = found
                share = min(1.0, uses * CONSECRATED_SECONDS[gem])
                sources.append(share)
                parts.append(f"{gem}: {uses:.3f} uses/s ({how}) x {CONSECRATED_SECONDS[gem]:g} s = {share:.0%}")
        charms = [option for slot in flasks.CHARM_SLOTS
                  if (option := (getattr(build, "equipped", {}) or {}).get(slot)) is not None
                  and any(CONSECRATED_CHARM.match(t.strip()) for t in option.raw.splitlines())]
        if charms:
            gain = flasks.Charges.of(build)
            for option in charms:
                info = build.call("flaskData", raws=[option.raw])["results"][0]
                uses, how = flasks.uses_per_second(info or {}, gain, option.raw, None)
                share = min(1.0, uses * CONSECRATED_SECONDS["charm"])
                sources.append(share)
                parts.append(f"{option.name}: {uses:.3f} uses/s ({how}) x {CONSECRATED_SECONDS['charm']:g} s = {share:.0%}")
        build.consecrated_ground = None
        if not sources:
            return [], []
        stationary = any(CONSECRATED_STATIONARY.match(t) for t in texts)
        if stationary and build.call("getConfig")["input"].get("conditionStationary"):
            return [], [{"label": "Consecrated Ground (yours)*", "value": "all fight (PoB: while stationary)",
                         "confidence": "MEDIUM", "basis": "you have Consecrated Ground around you while stationary and the "
                         "build stands still, which PoB applies itself; " + "; ".join(parts) + " add nothing on top"}]
        laid = 1.0
        for share in sources:
            laid *= 1 - share
        laid = 1 - laid
        up = laid * CONSECRATED_STAND
        effect = 1 + sum(float(m.group(1)) for t in texts if (m := CONSECRATED_EFFECT.match(t))) / 100
        build.consecrated_ground = (up, laid, effect, "; ".join(parts))  # (play_guide.consecrated_row: inside / outside)
        note = {"label": "Consecrated Ground: you stand in your own*", "value": f"{up:.0%} of the fight",
                "confidence": "LOW",
                "basis": ("; ".join(parts) + f": laid {laid:.0%} of the fight (sources overlapping as independent, MEDIUM), "
                          f"x{CONSECRATED_STAND:g} standing in it (the user's Leylines reasoning, LOW): {up:.0%}, so "
                          f"{CONSECRATED_REGEN * effect * up:.2f}% of maximum Life regenerated a second and "
                          f"{CONSECRATED_CURSE * effect * up:.0f}% reduced Curse effect on you (effect x{effect:.2f}); "
                          "duration modifiers not read (LOW)")}
        if up <= 0:
            return [], [note]
        return [models.line("Regenerate 1% of maximum Life per second", CONSECRATED_REGEN * effect * up),
                models.line("1% reduced Effect of Curses on you", CONSECRATED_CURSE * effect * up)], [note]

    def heavy_stunned_self(self, main: str | None) -> tuple[list[dict], list[dict]]:
        """Heavy Stuns on you from channelling Resonating Shield as the main skill (recovery.channel_stun_of: the bar never
        decays while the shield is raised): the share of the fight Heavily Stunned is lost from the main skill's time, as
        "1% more Attack and Cast Speed" x -100 x that share (parry_model's way); Block on PoB at 1 - that share (the
        raised shield Blocks every Blockable hit while channelling, nothing while stunned); no Evasion in either state."""
        share, rate, how = recovery_mod.channel_stun_of(self.build, main)
        self.build.heavy_stunned_self = (share, rate, how)  # (play_guide.heavy_stunned_row)
        if not how:
            return [], []
        note = {"label": "Heavily Stunned while channelling with your shield raised*",
                "value": f"{share:.0%} of the fight ({rate * 60:.1f} Heavy Stuns a minute)", "confidence": "MEDIUM",
                "basis": how + ("; the time lost comes off the main skill (its DPS x" + f"{1 - share:.2f})")}
        # (MECHC30, leftover (a)) Resonating Shield: "While Channelling Resonating Shield, your Shield is raised and you will
        # Block all incoming Blockable hits" (ActiveSkills resonating_shield), and "While Heavily Stunned players cannot
        # Block" (KeywordPopups HeavyStunPlayer): Block = 100% of Blockable hits outside the stunned share, 0 inside it.
        # PoB knew neither (its ordinary shield Block chance: an undercount while channelling, an overcount while
        # stunned). On PoB as its Block chance at its maximum, the maximum overridden to 1 - share (PoB's 90% cap kept:
        # data.misc BlockChanceCap), so every reader of EffectiveBlockChance (boss_hits' land and block shares, the
        # recovery and defence floors, PoB's EHP) sees it; red-flash hits stay unblockable (boss_hits.shares).
        # Evasion (the manager, 15 Nov 20:55: "While the shield is raised there's no Evasion: no evaded hits, so every hit
        # builds stun. Outside the channel, Evasion counts as usual", weighted by the channel share): "You cannot Evade
        # while your Shield is raised" (Raise Shield's text) and "While Heavily Stunned players cannot ... Evade"
        # (KeywordPopups HeavyStunPlayer), so a channelling main skill has no Evasion in either of its two states (raised,
        # or stunned) and no time outside both: PoB's CannotEvade (CalcDefence 1496: every EvadeChance 0), tagged
        # RAISED_TAG. The stun model needs no Evasion read of its own under the ruling (recovery.channel_heavy_stun: no
        # avoidance while raised), so no bridge read leaving the tag out is needed while the channel is all fight
        block = min(BLOCK_CAP, 100 * (1 - share))
        note["basis"] += (f"; Block: all Blockable hits while channelling, none while stunned: {block:.0f}% on PoB (its Block "
                          "at a maximum set to that; PoB's 90% cap kept); Evasion: none, raised or stunned (PoB's "
                          "CannotEvade)")
        mods = [models.raw_mod("BlockChanceMax", "OVERRIDE", block, tag=RAISED_TAG),
                models.raw_mod("MaxBlockIfNotBlockedRecently", "FLAG", 1, tag=RAISED_TAG),
                models.raw_mod("CannotEvade", "FLAG", 1, tag=RAISED_TAG)]
        if share > 0:
            mods.append(models.line("1% more Attack and Cast Speed", -100 * share, skill=main))
        return mods, [note]

    def turtle_charm(self, texts: list[str], mods: list[dict]) -> tuple[list[dict], list[dict]]:
        """Turtle Charm's set maximum Block beside "+/-N% to maximum Block chance" lines (turtle_charm_readings, the
        comment at TURTLE_SET): the lower reading on PoB. When the headline is under the set value, an assumption OVERRIDE
        (or, with Resonating Shield's raised-shield override already in `mods`, that one lowered to it: one OVERRIDE,
        as PoB reads only the first). Returns (mods to add, notes); `mods` is changed in place for the merge."""
        found = turtle_charm_readings(texts)
        if found is None or (not found["adders"] and not found["conditional"] and not found["saffell"]):
            return [], []
        value, added, head = found["set"], found["added"], found["headline"]
        total = sum(found["adders"])
        new = []
        if head < value:
            raised = next((m for m in mods if m.get("kind") == "Raw" and m.get("name") == "BlockChanceMax"
                           and m.get("type") == "OVERRIDE"), None)
            if raised is not None:
                raised["value"] = min(raised["value"], head)
            else:
                new.append(models.raw_mod("BlockChanceMax", "OVERRIDE", head, tag=TURTLE_TAG))
        rows = [f"set {value:g}%, adders ignored (PoB's OVERRIDE): {value:g}%"]
        if found["saffell"]:
            rows.append("Saffell's Frame sends the adders to Maximum Resistances, so none reach Block (PoB converts 0 "
                        "with the override, CalcDefence 951; the game: UNVERIFIED)")
        else:
            rows.append(f"set {value:g}% + adders {total:+g}% (capped at {BLOCK_CAP:g}%): {added:g}%")
        if found["conditional"]:
            rows.append("not counted in either: " + "; ".join(found["conditional"]) + " (PoB doesn't parse it)")
        return new, [{
            "label": "Turtle Charm: maximum Block chance*",
            "value": f"{head:g}% (the lower reading)" + (f"; {max(value, added):g}% in the other" if added != value else ""),
            "basis": ("Turtle Charm's \"Maximum Block chance is 75%\" beside \"+/-N% to maximum Block chance\" lines: "
                      "whether the game adds them is UNVERIFIED (no test or GGG word; RESEARCH27 R27-5), so both "
                      "readings, the lower as the headline: " + "; ".join(rows)
                      + (". " + (f"{head:g}% on PoB (it ignored the {total:+g}%: an OVERCOUNT before)" if head < value
                                 else f"PoB's {value:g}% kept; the adders' {added:g}% would be an UNDERCOUNT if the game "
                                      "adds them"))),
            "confidence": "UNVERIFIED (set vs add); the lines HIGH (PoB's parse)"}]

    def evasion_doublings(self, texts: list[str]) -> tuple[list[dict], list[dict]]:
        """Stalking Panther's slot doubling with the unique's "doubled if not Hit Recently" (the comment at PANTHER_LINE):
        the headline doubles those slots once (x2), PoB's x4 the other reading."""
        lines = [t.strip() for t in texts]
        if not (any(PANTHER_LINE.match(t) for t in lines) and any(EVASION_DOUBLED_NOT_HIT.match(t) for t in lines)):
            return [], []
        stats = self.build.call("stats", fields=[f"EvasionOn{s}" for s in PANTHER_SLOTS])
        slots = sum(number(stats.get(f"EvasionOn{s}")) for s in PANTHER_SLOTS)
        mod = models.raw_mod("Evasion", "MORE", -50, tag=PANTHER_TAG, not_condition="BeenHitRecently",
                             slots=PANTHER_SLOTS)
        return [mod], [{
            "label": "Stalking Panther with \"Evasion Rating is doubled if you have not been Hit Recently\"*",
            "value": "helmet, gloves and boots' Evasion x2 while not Hit Recently (the lower reading); x4 in the other "
                     "(PoB's)",
            "basis": ("PoB multiplies the two doublings (the notable's MORE 100 on those slots has no globalLimit, the "
                      "unique's has its own), so those slots' Evasion was x4 while not Hit Recently. The user's wording "
                      "rule (\"doubled\" doubles once) against the two lines' different scopes: UNVERIFIED (RESEARCH27 "
                      "R27-4), so both readings, the lower as the headline: those slots once (x2), the rest of your "
                      f"Evasion x2 from the unique as before; on PoB as 50% less on the three slots while not Hit Recently "
                      f"(their base Evasion {slots:,.0f} before modifiers). Hit Recently: the unique's doubling is off and the slots' x2 "
                      "stays, so no change"),
            "confidence": "UNVERIFIED (once vs twice); the lines HIGH (PoB's parse)"}]

    def demon_form(self, skills: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """Demon Form's Demonflame and its share of the fight (MECHC28; PATCH_CHECK S2-4 / K1-o4: PoB keeps you in the form
        all fight at its placeholder 10 Demonflame, never set by the tool): demon_form_state, PoB's
        Multiplier:DemonFlameStacks moved to the average while in the form by a BASE line (build.demon_offset), and
        build.demon_form = (share, how) for the record (conditions.Conditions.evidence computes its own, the same way,
        for the inDemonForm box)."""
        build = self.build
        state = demon_form_state(build, skills, main)
        if state is None:
            build.demon_form, build.demon_offset = None, 0.0
            return [], []
        share, stacks, pob = state["share"], state["stacks"], state["pob"]
        build.demon_form = (share, state["how"])
        offset = (stacks - pob) if share > 0 else 0.0
        build.demon_offset = offset
        mods = [models.raw_mod("Multiplier:DemonFlameStacks", "BASE", offset)] if abs(offset) > 1e-9 else []
        return mods, [{
            "label": "Demon Form: Demonflame and time in the form*",
            "value": f"{stacks:.1f} Demonflame, in the form {share:.0%} of the fight (PoB: {pob:g}, all fight)",
            "basis": state["basis"],
            "play": ("enter Demon Form as its cooldown ends and stay in it until it drops you at 1 Life (with it: the "
                     "share above; without it: no Demonflame)") if share > 0 else "",
            "confidence": ("MEDIUM (stints from the average Life flows, the boss's hits spread evenly; Life's dip isn't "
                           "taken off the defence floors: an OVERCOUNT of survival while it's low; the fight's first "
                           "10 s ramp isn't counted, as for every stack the tool holds steady)")}]

    def zenith(self, skills: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """Zenith I / II on the main skill (MECHC26; PATCH_CHECK mirror sweep MC-3, an UNDERCOUNT before: PoB maps
        support_spell_damage_+%_final_while_above_90%_maximum_mana to Condition:FullMana, SkillStatMap 2323, which nothing
        sets, so it counted 0): N% more Spell damage x the share of the fight above 90% of maximum Mana (zenith_share,
        from the build's own pool, cost per cast and regeneration). Other skills it supports: not counted (an undercount)."""
        present = {s["name"]: s for s in skills if not s.get("disabled")}
        group = self.build.main_group()
        if main not in present or group is None or not any(
                g.get("support") and g.get("enabled", True) and g["name"].startswith(ZENITH)
                for g in group.get("gems") or []):
            return [], []
        found = next((s for s in self.build.call("activeSkills", supportStats=[ZENITH_STAT])["skills"]
                      if s["name"] == main and s.get("group") == group["index"] and not s.get("disabled")), None)
        more = number(((found or {}).get("supportStats") or {}).get(ZENITH_STAT))
        if more <= 0 or not (found or {}).get("spell"):
            return [], []
        stats = self.build.call("stats", fields=["Mana", "ManaCost", "Speed", "ManaRegenRecovery", "ManaPerSecondCost"])
        share, how = zenith_share(stats)
        counted = share
        if self.build.call("getConfig")["input"].get("conditionFullMana"):
            # (MECHC37: the Full Mana box is on for another line (conditions._full_mana), and PoB maps Zenith to the same
            # condition, so its share of Zenith is PoB's: only the rest, from 90% to full, is added here)
            texts = [e["text"] for e in self.build.call("sourceLines")["lines"]]
            full, _ = conditions_mod.mana_above_share(stats, conditions_mod.full_mana_at(texts))
            counted = max(share - full, 0.0)
            how += f"; PoB's Full Mana box is on ({full:.0%} of the fight), which turns Zenith on in PoB: only the rest added"
        mods = [models.line("1% more Spell Damage", more * counted, skill=main)] if counted > 0 else []
        return mods, [{"label": "Zenith: above 90% of maximum Mana*", "value": f"{share:.0%} of the fight ({more:g}% more "
                       f"Spell damage then)", "basis": how + ". PoB's FullMana condition is never set, so before this its "
                       "share was 0", "play": "keep Mana near the top: cast when above 90% (with it: the share above; "
                       "without it: none)", "confidence": "MEDIUM (main skill's costs only; Mana flasks and leech not "
                       "counted)"}]

    def purple_flames(self, skills: list[dict]) -> tuple[list[dict], list[dict]]:
        """Into the Breach's Purple Flame stacks (MECHC26; PATCH_CHECK G1-1, the largest OVERCOUNT found: nothing set
        PoB's purpleFlameStacks, whose default is 10, ConfigOptions 474, so every Into the Breach build had 10 stacks of
        7% x Remnant effect damage as extra Chaos). The build's own stacks: flames at REMNANT_SOURCES' 1 per 1.21 s x the
        Remnant multiplier lines, the Purple share (recovery.flame_shares: a third, the user's observation from play,
        16 Nov, MEDIUM; or all / none with a Choice notable),
        each stack 8 s (KeywordPopups FlamesOfChayula: "7% of damage as extra Chaos damage for 8 seconds, and stacks up
        to 10 times"; 0.4.0: 8 s, was 5), at most 10 (purple_stacks, min(10, 8 s x the rate)). PoB's Multiplier:PurpleFlamesCount is moved from
        its config value to ours by a BASE line. No Into the Breach: nothing."""
        from builder.search import recovery

        present = {s["name"]: s for s in skills if not s.get("disabled")}
        if BREACH not in present:
            return [], []
        build = self.build
        texts = [line["text"] for line in build.call("sourceLines")["lines"]]
        multiplier, multiplier_how = remnant_multiplier(texts)
        shares, colour = recovery.flame_shares(texts)
        rate = REMNANT_SOURCES[BREACH][0] * multiplier * shares["purple"]
        stacks = purple_stacks(rate)
        configured = (build.call("getConfig").get("input") or {}).get("purpleFlameStacks")
        pob = min(number(configured), PURPLE_MAX) if configured is not None else float(PURPLE_MAX)
        mods = [models.raw_mod("Multiplier:PurpleFlamesCount", "BASE", stacks - pob)] if abs(stacks - pob) > 1e-9 else []
        return mods, [{
            "label": "Into the Breach: Purple Flame stacks*", "value": f"{stacks:.2f} of {PURPLE_MAX} (PoB's default {pob:g})",
            "basis": (f"{rate:.3f} Purple Flames collected a second (one flame per 1.21 s"
                      + (f", {multiplier_how}" if multiplier_how else "") + f"; {colour}), each stack {PURPLE_SECONDS:g} s, "
                      f"at most {PURPLE_MAX}: min({PURPLE_MAX}, {PURPLE_SECONDS:g} s x the rate), each giving PoB's 7% x Remnant "
                      "effect of damage as extra Chaos (with Choice of Power every flame is Purple: HIGH, PoB's flag)"),
            "play": "walk onto each Purple Flame as it appears (with them: the stacks above; without: none)",
            "confidence": ("MEDIUM (without a Choice notable a third Purple: the user's observation from play, 16 Nov, "
                           "MEDIUM; every flame taken as collected)")}]

    def es_flask_income(self, income, texts: list[str], lowered: list[str]) -> float:
        """(MECHC23; AUDIT33 A33-1, an UNDERCOUNT for Energy Shield builds) Energy Shield from the Life flask under "Life
        Recovery from Flasks also applies to Energy Shield" (flasks.es_recovery_per_second, at the flask's own use rate,
        into recovery's EnergyShieldFlaskRecovery), and the Energy Shield Recharge starts a second from Bitterbloom's
        Mana Flask line and Nascent Hope's own line (flasks.es_recharge_starts, at the flask's / charm's uses a second,
        flasks.uses_per_second); returns the starts a second (0 without either line)."""
        from builder.search import flasks

        build = self.build
        equipped = getattr(build, "equipped", {}) or {}
        to_es = any(flasks.ES_FROM_LIFE_FLASK.search(t) for t in lowered)
        on_mana = any(flasks.ES_START_MANA_FLASK.search(t) for t in lowered)
        on_charm = [o for slot in flasks.CHARM_SLOTS if (o := equipped.get(slot)) is not None
                    and flasks.ES_START_ON_USE.search(o.raw)]
        if not (to_es or on_mana or on_charm):
            return 0.0
        gain = flasks.Charges.of(build)

        def flask(pool: str):
            slot = next(k for k, v in flasks.FLASK_SLOTS.items() if v == pool)
            option = equipped.get(slot)
            return option, (build.call("flaskData", raws=[option.raw])["results"][0] if option is not None else None)

        if to_es:
            option, info = flask("Life")
            rate = flasks.es_recovery_per_second(info, gain, option.raw if option is not None else "")
            how = "Life flask recovery applied to Energy Shield"
            if option is not None and info and flasks.pob_counts_whole(info, gain, "Life", option.raw):
                income.parts.append(f"{how}: in PoB's Energy Shield regeneration already ({option.name}'s effect is kept "
                                    "at full Life, so PoB counts it over its whole duration; not added twice)")
            elif rate > 0:
                income.add("EnergyShieldFlaskRecovery", rate,
                           f"{how} ({option.name}, as often as its charges allow, x {gain.es_recovery_mod:.2f} Energy "
                           "Shield recovery rate; MEDIUM)")
            else:
                income.parts.append(f"{how}: 0, " + ("no Life flask equipped" if option is None
                                                     else "its charges allow no use against the boss"))
        mana_uses = 0.0
        if on_mana:
            option, info = flask("Mana")
            if info:
                mana_uses = flasks.uses_per_second(info, gain, option.raw, "Mana")[0]
        charms = []
        for option in on_charm:
            info = build.call("flaskData", raws=[option.raw])["results"][0]
            charms.append((option.name, option.raw, flasks.uses_per_second(info or {}, gain, option.raw, None)[0]))
        if not (on_mana or on_charm):
            return 0.0
        starts, how = flasks.es_recharge_starts(texts, charms, mana_uses)
        income.parts.append(f"Energy Shield Recharge starts {starts:.3f}/s ({how or 'no use'}; each held until a boss "
                            f"hit resets the recharge, MEDIUM-LOW; field {flasks.ES_STARTS_FIELD})")
        return starts

    def hit_rates(self, main: str | None, stats: dict, before: float = 0.0) -> dict:
        """The rates recovery.hit_income reads (MECHC14, A27-32), from the build and the request's boss: the boss's hits
        that land a second (at the "when hit" benefit pace, bosses.benefit_skill_time) and the Life each takes
        (objective.boss_taken, at the defence pace: after mitigation and the per-type weights; Life's
        share after Mind over Matter, and of Life + Energy Shield in proportion, LOW), the boss's hits Blocked a second (at
        the "when hit" benefit pace, as objective.on_block) and the main skill's casts a second when it is a Spell (other
        Spells' casts aren't counted: LOW, a lower bound). `before`: Life a second from recovery.py's sources counted
        before the hit lines (kills, Remnants, charms, events, spending); with PoB's regeneration, leech, on-hit recovery,
        recoup and Life on Block and the flasks' Life, less the skills' Life costs and the build's own damage over time
        on Life, it is the net Life refilled between the boss's hits ("refill"; MECHC16, AUDIT28 A28-1)."""
        from builder.search import boss_hits, flasks, objective, recovery

        build = self.build
        hit = {**stats, **build.call("stats", fields=list(dict.fromkeys(
            [*objective.RECOVERY_INPUTS, "Armour", "EnergyShield", "Life", "Speed", "StunThreshold",
             "StunAvoidChance", "LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate", "LifePerSecondCost"])))}
        taken, landed, _ = objective.boss_taken(hit)
        life, es = number(hit.get("Life")), number(hit.get("EnergyShield"))
        mom = min(max(number(hit.get("sharedMindOverMatter")), 0.0), 100) / 100
        share = (1 - mom) * (life / (life + es) if life + es > 0 else 0.0)
        per_hit = sum(taken.values()) / landed * share if landed > 0 else 0.0
        every = objective.benefit_every(hit)
        blocked = boss_hits.block_share(hit) / every
        # (a "when hit" benefit: the benefit pace, the hit's size as scored; MECHC20: the worst case reads the lowest real
        # boss's landed hits, boss_hits.gain_hits, A29-1's rule for gains)
        landed = boss_hits.gain_hits(hit)[0]
        types = set((build.ctx.gems.get(main) or {}).get("skillTypes") or []) if main else set()
        casts = number(hit.get("Speed")) if "Spell" in types else 0.0
        how = (f"{main}'s casts; other Spells' casts not counted (LOW)" if casts > 0
               else "the main skill isn't a Spell (other Spells' casts not counted: LOW)")
        # (MECHC15) the boss's hits that Light Stun you, each type at its full hit size (PoB's {Type}TakenHit, after
        # mitigation and Deflection's expected share) and its share of the boss's hits (boss_hits.type_shares)
        stunned, stunned_how = recovery.boss_stuns(hit)
        # (MECHC16, A28-1) the net Life refilled a second between the boss's hits: the build's other Life recovery less
        # its Life costs and its own damage over time on Life (TotalBuildDegen at the Life share, LOW)
        extra = getattr(build, "extra_stats", None) or {}
        # (MECHC25, AUDIT35 A35-20) a part-time Enduring Elixirs Life flask with its slot on: PoB's regeneration holds its
        # whole recovery and "Life flasks" ours, so PoB's share comes off (flasks.pob_part_time_share): never twice
        doubled, doubled_how = flasks.pob_part_time_share(build, flasks.Charges.of(build))
        gains = {"regeneration, leech and on-hit (PoB)": sum(number(hit.get(k)) for k in
                                                             ("LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate"))
                 - doubled - resisted_leech(build).get("Life", 0.0),  # (MECHC26, H2-1: the boss's Leech resistance)
                 "Life flasks": number(extra.get(flasks.RECOVERY_FIELDS["Life"])),
                 "recoup and Life on Block": objective.recoup(hit, taken, "Life") + objective.on_block(hit, "Life", every),
                 "kills, Remnants, charms, events and spending": max(before, 0.0)}
        dot_share = 1.0 if getattr(build, "dot_bypasses_es", False) else share  # (MECHC37: The Black Doubt, all on Life)
        costs = (number(hit.get("LifePerSecondCost")) + max(number(hit.get(objective.BUILD_DEGEN)), 0.0) * dot_share
                 + (0.0 if getattr(build, "dot_hits_es", False) and es > 0 else objective.self_bleed_taken(
                     {**hit, objective.SELF_BLEED_PER_SECOND: getattr(build, "self_bleed", 0.0),
                      objective.SELF_POISON_PER_SECOND: getattr(build, "self_poison", 0.0)}))
                 + objective.boss_ignite_taken({**hit, objective.BOSS_IGNITE_PER_SECOND: getattr(build, "boss_ignite", 0.0)})
                 * dot_share)  # (MECHC37: the boss's Ignites, at the Life share like the degen)
        # (MECHC32: your own item's Bleed, Life only)
        refill = max(sum(gains.values()) - costs, 0.0)
        refill_how = (", ".join(f"{k} {v:,.1f}" for k, v in gains.items() if v > 0) or "no other Life recovery") + (
            f", less {costs:,.1f} Life costs and own damage over time" if costs > 0 else "") + (
            f" ({doubled_how})" if doubled_how else "")
        out = {"landed": landed, "life_per_hit": per_hit, "blocked": blocked, "casts": casts, "cast_how": how,
               "stunned": stunned, "stunned_how": stunned_how, "refill": refill, "refill_how": refill_how}
        texts = [line["text"].strip() for line in build.call("sourceLines")["lines"]]
        if any(p.search(t) for p in (recovery.PREVENTED_ES, recovery.PREVENTED_LIFE, recovery.PREVENTED_LIFE_TRIBUTE)
               for t in texts):
            # (MECHC21) Physical damage the build's Armour and flat reduction prevent a second: the boss's Physical hit
            # (PoB's PhysicalEnemyDamage, after Deflection's expected share) x PhysicalDamageReduction, at its Physical
            # share of hits (boss_hits.type_shares) and its landed hits (the gain pace, as above)
            more = build.call("stats", fields=["PhysicalEnemyDamage", "PhysicalDamageReduction", "PresenceRadius"])
            raw, cut = number(more.get("PhysicalEnemyDamage")), min(max(number(more.get("PhysicalDamageReduction")), 0.0), 100)
            _, _, deflect = objective.boss_taken(hit)
            physical = boss_hits.type_shares(boss_hits.boss_name(hit)).get("Physical", 1.0)
            out["prevented"] = raw * deflect * cut / 100 * physical * landed
            out["prevented_how"] = (f"the boss's {raw:,.0f} Physical a hit x {cut:.0f}% prevented by Armour and flat "
                                    f"reduction x {physical:.0%} of its hits Physical x {landed:.3f} landing/s; Blocked "
                                    "damage not counted (UNVERIFIED whether it counts as prevented)")
            metres = number(build.call("getConfig")["input"].get("enemyDistance"))
            out["power"], power_how = models.boss_power(texts, {**hit, **more, "_main_types": types}, metres or None,
                                                        "Attack" in types)
            out["power_how"] = power_how or f"a Unique boss's {models.BOSS_POWER} Power"
        return out

    def spent_rates(self, skills: list[dict], main: str | None, stats: dict, lowered: list[str]) -> dict:
        """(events a second, how) for recovery.spent_income, each from the build's own sources (MECHC13; CLAUDE.md: no
        flat rates; none, 0 and said): Endurance / Power Charges its consumers spend (build.charge_flow, Conditions'
        charge model), Glory its Glory skills spend (models.glory_spent), uses of its Combo skills that build 10+ Combo
        (usage.combo_rate's rotation), corpses its corpse skills consume (at most models.corpse_supply: the boss
        preset's adds), its Offering skills' uses (conditions.skill_use_rate) and its minions Revived (MECHC18:
        models.minion_deaths, its Persistent, Reviving minions Killed a second)."""
        from builder.search import recovery
        from builder.search.usage import Usage, requirements

        build = self.build
        has = lambda pattern: any(pattern.search(t) for t in lowered)  # noqa: E731
        out = {}
        flow = getattr(build, "charge_flow", None) or {}
        for key, kind, pattern in (("endurance", "Endurance", recovery.ENDURANCE_SPENT),
                                   ("power", "Power", recovery.POWER_SPENT)):
            if has(pattern):
                out[key] = flow.get(kind) or (0.0, f"no skill of the build consumes {kind} Charges (or none are made)")
        present = {s["name"]: s for s in skills if not s.get("disabled")}
        main_skill = models.Skill.of(present[main]) if main in present else None
        if has(recovery.GLORY_SPENT):
            names = [n for n in present if n in models.GLORY_SKILLS or n in models.GLORY_FROM_LIFE]
            out["glory"] = (models.glory_spent(names, stats, main_skill, [models.Skill.of(s) for s in present.values()],
                                               [line["text"] for line in build.call("sourceLines")["lines"]])
                            if names else (0.0, "no Glory skill"))
        groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
        speed = number(stats.get("Speed"))
        main_index = (build.main_group() or {}).get("index")
        choices = models.press_choices(build)

        def typed_uses(types: set[str]) -> tuple[float, list[str]]:
            found = {g["index"]: n["name"] for g in groups for n in g.get("gems") or []
                     if n.get("enabled", True) and not n.get("support")
                     and types & set((build.ctx.gems.get(n["name"]) or {}).get("skillTypes") or [])}
            total, parts = 0.0, []
            if main in found.values():
                total, parts = speed, [f"the main skill {main}, {speed:.2f}/s"]
                found = {i: n for i, n in found.items() if n != main}
            if found:
                for rate in build.call("useRates", indices=sorted(found))["rates"]:
                    name = found.get(rate.get("index"))
                    if name is None or rate.get("index") == main_index:
                        continue
                    each, why, low = conditions_mod.skill_use_rate(rate, choices)
                    total += each
                    parts.append(f"{name} on {why}, {each:.2f}/s" + (" (LOW)" if low else ""))
            return total, parts

        if has(recovery.CORPSE_LIFE) or has(recovery.CORPSE_MANA):
            uses, parts = typed_uses(set(models.CORPSE_TYPES))
            sacrifice = ""
            if models.SACRIFICE in present and models.SACRIFICE_CORPSES_STAT not in stats:
                # (MECHC15: MECHB19's Sacrifice corpses, the build's own undead Reviving minion deaths: the stats here
                # come from life_mana_income, not Assumptions.models, so they lack it until worked out here)
                found = [models.Skill.of(s) for s in present.values()]
                group = build.main_group()
                main_skill = next((s for s in found if group and s.group == group["index"] and s.name == main), None)
                corpses = models.sacrifice_corpses(found, main_skill, {
                    **build.call("stats", fields=models.MODEL_FIELDS), **stats,
                    "_minion_lives": getattr(build, "minion_lives", {})})
                stats = {**stats, models.SACRIFICE_CORPSES_STAT: corpses}
            if models.SACRIFICE in present:
                value = stats.get(models.SACRIFICE_CORPSES_STAT)
                sacrifice = (f"; Sacrifice {models.sacrifice_rate(stats):.3f} corpses/s"
                             + (f" ({value[1]})" if isinstance(value, (tuple, list)) and len(value) > 1 else ""))
            supply = models.corpse_supply(set(present), stats)
            out["corpses"] = (min(uses, supply), (f"corpse skills {'; '.join(parts) or 'none'}, one corpse a use, at "
                                                  f"most the corpses there are: {models.add_corpses_note(stats)}"
                                                  + sacrifice))
        if has(recovery.MINION_REVIVED):  # (MECHC18) the build's Reviving minions Killed a second, from its own sources
            found = [models.Skill.of(s) for s in present.values()]
            group = build.main_group()
            revive_main = next((s for s in found if group and s.group == group["index"] and s.name == main), None)
            rate, how = models.minion_deaths(found, revive_main, {**build.call("stats", fields=models.MODEL_FIELDS),
                                                                 "_minion_lives": getattr(build, "minion_lives", {})})
            out["revived"] = (rate, f"each of your Reviving minions Killed is Revived once its revival ends: {how}"
                              if rate > 0 else how)
        if has(recovery.RAGE_SHIFT):  # (MECHC21: Rage consumed on each shift back to Human form, conditions.rage_held)
            out["rage"] = flow.get("Rage") or (0.0, "no Rage held, or no shift back to Human form read")
        if has(recovery.OFFERING_LIFE):
            uses, parts = typed_uses({"Offering"})
            out["offerings"] = (uses, "; ".join(parts) or "no Offering skill in the build")
        if has(recovery.COMBO_SPENT):
            rules, usage, total, parts = usage_rules(build), None, 0.0, []
            for name in present:
                for need in requirements(name, rules[name]) if rules.get(name) else []:
                    if need.kind != "combo":
                        continue
                    if need.amount < recovery.COMBO_AT_LEAST:
                        parts.append(f"{name} needs {need.amount:g} Combo: the rotation builds that much a use, under 10 "
                                     "(LOW: a use after more Strikes would count)")
                        continue
                    usage = usage or Usage(build, None)
                    found = usage.combo_rate(name, need.amount)
                    if found:
                        total += found[0]
                        parts.append(f"{name} {found[0]:.3f}/s ({need.amount:g} Combo from {found[2]} a use)")
                    else:
                        parts.append(f"{name}: no skill of the build Strikes to build its Combo")
            out["combo"] = (total, "; ".join(parts) or "no Combo skill in the build")
        return out

    def frostflame(self, main: str | None, skills: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
        """Frostflame Nova's Volatile Ice (models.frostflame_plan), as the main skill or in any Full DPS group (MECHB14):
        2 hits a second while the boss carries an Ignite from any of the build's Full DPS groups (models.ignite_share),
        at most 2 x the Ignite's duration a cast, not one hit per cast. `skills`: activeSkills, to skip a build without it."""
        if skills is not None and not any(s["name"] == models.FROSTFLAME and not s.get("disabled") for s in skills):
            return [], []
        build = self.build
        main_group = build.main_group()
        groups = [g for g in build.groups() if g["enabled"] and (g.get("fullDPS") or (main_group is not None
                                                                                     and g["index"] == main_group["index"]))]
        frost = [g["index"] for g in groups if any(s["name"] == models.FROSTFLAME for s in g["skills"])]
        if not frost:
            return [], []
        indices = sorted({g["index"] for g in groups})
        rates = build.call("useRates", indices=indices)["rates"]
        scales = getattr(build.objective, "press_scales", None) or {}
        main_index = main_group["index"] if main_group is not None else None
        uses = [(number(r.get("speed")), 1.0 if r["index"] == main_index else scales.get(r.get("name"), 1.0))
                for r in rates if r["index"] in frost and str(r.get("name", "")).startswith(models.FROSTFLAME)]
        if not uses:
            return [], []
        share, how = models.ignite_share(rates, scales)
        duration = max([number(r.get("IgniteDuration")) for r in rates if number((r.get("stackPotential") or {})
                                                                                 .get("Ignite")) > 0] or [0.0])
        return models.frostflame_plan(uses, share, how, duration)

    def multi_set_side(self, main: "models.Skill | None") -> tuple[list[dict], list[dict]]:
        """MECHB16: a MULTI_SET_SKILLS skill (Rolling Slam) in an enabled Full DPS group other than the main one: PoB
        scores its group's one chosen set there too, so the same "more" (models.multi_set_model, tagged to the skill).
        Left out when the main skill is the same skill (its own "more" already carries the tag)."""
        build = self.build
        main_group = build.main_group()
        main_index = main_group["index"] if main_group is not None else None
        mods: list[dict] = []
        notes: list[dict] = []
        done = {main.name} if main is not None else set()
        for group in build.groups():
            if not group["enabled"] or not group.get("fullDPS") or group["index"] == main_index:
                continue
            for position, skill in enumerate(group["skills"], start=1):
                name = skill["name"]
                if name not in models.MULTI_SET_SKILLS or name in done:
                    continue
                answer = build.call("setRates", index=group["index"], active=position)
                got_mods, got_notes = models.multi_set_model(name, answer.get("chosen"), answer.get("sets") or [])
                if got_mods:
                    done.add(name)
                    for note in got_notes:
                        note["label"] += " (a Full DPS group)"
                mods, notes = mods + got_mods, notes + got_notes
        return mods, notes

    def unbuilt_grants(self, skills: list[dict]) -> list[dict]:
        """A note for each skill the build's passives or items grant ("Grants Skill: ...") that PoB built no skill for
        (MECH49; FIX_QUEUE "COV3's skills PoB has no data for": Dark Staff's Dark Pact, Ritualist's Queen's Procession,
        the Unborn Lich's His Dark Horizon and His Grave Command have no skill file in PoB): it counts 0 in our numbers,
        an undercount, said rather than dropped silently. Not modelled yet."""
        built = {s["name"] for s in skills}
        notes, seen = [], set()
        for line in self.build.call("sourceLines")["lines"]:
            m = GRANTED_SKILL.search(line["text"])
            if not m or m.group(1) in built or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            notes.append({"label": f"{m.group(1)}: not counted", "value": 0,
                          "basis": f"granted by {line['source']}, but PoB built no skill of that name (it has no data for "
                                   "some granted skills), so its damage and effects count 0 here: an undercount, not "
                                   "modelled yet",
                          "confidence": "LOW (an undercount)"})
        return notes

    def remnant_presses(self, skills: list[dict], main: str | None) -> tuple[list[dict], list[dict]]:
        """The time pressing Snap or Frost Bomb for its Remnants takes from the main skill (MECH46, FLAT_RATES A14: before,
        only when its group was in Full DPS, through rate_presses, which still charges those). Each pressed on its
        cooldown, Snap only when it finds an ailment to Consume, for its base cast time (cast speed not read: LOW)."""
        if not any(s["name"] in PRESSED_SOURCES and not s["disabled"] for s in skills):
            return [], []
        build = self.build
        group = build.main_group()
        main_index = group["index"] if group else None
        pressed = {g["index"] for g in build.groups() if g["index"] != main_index
                   and pressed_group(g, build.ctx.gems, getattr(build.ctx, "all_gems", None))}  # (SEARCH25: A36-14)
        busy, parts = 0.0, []
        for skill in skills:
            name = skill["name"]
            if (skill["disabled"] or name not in PRESSED_SOURCES or name == main or skill.get("group") == main_index
                    or skill.get("group") in pressed):
                continue
            rate = REMNANT_SOURCES[name][0] * (skill.get("cooldownRecovery") or 1.0)
            if name == LIVING_BOMB:
                rate = living_bomb_rate(build.call("stats", fields=list(LIVING_BOMB_FIELDS)), skill.get("castTime") or 0.0)[0]
            if name == "Snap":
                rate *= snap_found(snap_stats(build))[0]
            took = rate * (skill.get("castTime") or 0.0)
            if took > 0:
                busy += took
                parts.append(f"{name} {rate:.2f}/s x {skill.get('castTime'):g} s")
        if busy <= 0 or main is None:
            return [], []
        busy = min(busy, 0.9)
        return [models.line("1% more Attack and Cast Speed", -100 * busy, skill=main)], [{
            "label": "Time pressing Remnant skills", "value": round(busy, 3),
            "basis": f"{', '.join(parts)} (base cast times; pressed on cooldown for their Remnants): {busy:.1%} of the "
                     f"fight not spent on {main}",
            "play": "Press Snap / Frost Bomb on cooldown (Snap when the boss is Ignited, Shocked or Frozen), and plant a "
                    "new Living Bomb seed as each one explodes.",
            "confidence": "game files' cooldowns; cast speed not read (LOW)"}]

    def stats_as_main(self, group: dict, position: int, main_index: int, fields: list[str]) -> dict:
        """PoB's stats with the skill at `position` in `group` calculated as the main skill for a moment (its group made
        the main group if it isn't), then the group's main skill and the main group put back."""
        index, current = group["index"], group.get("mainActiveSkill") or 1
        if index == main_index and current == position:
            return self.build.call("stats", fields=fields)
        if index == main_index:
            stats = self.build.call("setGroup", index=index, mainActiveSkill=position, fields=fields)["stats"]
            self.build.call("setGroup", index=index, mainActiveSkill=current, fields=[])
            return stats
        stats = self.build.call("setGroup", index=index, main=True, mainActiveSkill=position, fields=fields)["stats"]
        if position != current:
            self.build.call("setGroup", index=index, mainActiveSkill=current, fields=[])
        self.build.call("setGroup", index=main_index, main=True, fields=[])
        return stats

    def glory_main(self, skills: list[dict], main: str | None) -> tuple[list[dict], dict] | None:
        """A Glory skill as the main skill (models.GLORY_SKILLS): PoB calculates it at will; it's used as often as the
        build makes its Glory. The Glory comes from another skill's hits (the first other enabled skill in the main
        group, else in a Full DPS group), calculated as the main skill for a moment, through models.glory_uses; with no
        other skill, from its own hits (assumed to count for it), at will if a use builds its own cost, else never. Its
        uses per second become its trigger rate (capped at one per cast time). None for any other main skill."""
        if main not in models.GLORY_SKILLS:
            return None
        found = self.glory_rate(skills, main)
        if found is None:
            return None
        rate, basis = found
        return models.trigger_rate(main, rate), {
            "label": f"{main} uses per second (Glory)", "value": round(rate, 3),
            "basis": basis + "; the time spent on the skill that builds it isn't taken from that skill's own DPS",
            "play": f"Use {main} whenever you have the Glory for it.",
            "confidence": "theory: game files' Glory costs and gains; event rates as glory_uses labels them",
        }

    def glory_rate(self, skills: list[dict], main: str) -> tuple[float, str] | None:
        """A Glory skill's uses per second and how (glory_main's rule, for any Glory skill in the build); None if it
        isn't an enabled skill. The skill whose hits build it is never one of Apocalypse's own disasters."""
        info = next((s for s in skills if s["name"] == main and not s["disabled"]), None)
        if info is None:
            return None
        listing = self.build.call("groups")
        main_index = listing["mainSocketGroup"]
        builder = None
        for group in sorted(listing["groups"], key=lambda g: g["index"] != main_index):
            if not group.get("enabled", True) or not (group["index"] == main_index or group.get("fullDPS")):
                continue
            names = [s["name"] for s in group.get("skills") or []]
            position = next((i for i, n in enumerate(names, 1) if n != main and n not in models.GLORY_SKILLS
                             and n not in models.APOCALYPSE_SUBS
                             and any(s["name"] == n and not s["disabled"] for s in skills)), None)
            if position is not None:
                builder = (group, position, names[position - 1])
                break
        skill = models.Skill.of(info)
        cap = 1 / skill.cast_time if skill.cast_time else math.inf
        if skill.cooldown:  # (MECHC13: at most once per its own cooldown; before, never read)
            cap = min(cap, (skill.cooldown_recovery or 1.0) / skill.cooldown)
        if builder is not None:
            group, position, name = builder
            stats = self.stats_as_main(group, position, main_index, models.MODEL_FIELDS)
            source = next((models.Skill.of(s) for s in skills if s["name"] == name), None)
            texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]  # (Glory lines, MECHC13)
            uses, how = models.glory_uses([main], stats, source, [skill], texts)
            rate, basis = min(uses, cap), f"Glory from {name}'s hits: {how}"
        else:
            stats = self.build.call("stats", fields=models.MODEL_FIELDS)
            # (at one use per second, so the rate this model set before doesn't decide it)
            texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]  # (Glory lines, MECHC13)
            # (no cooldown here: this asks what one use builds against its cost; `cap` holds the cooldown)
            per_use, how = models.glory_uses([main], dict(stats, Speed=1.0), skill, None, texts)
            rate = cap if per_use >= 1.0 else 0.0
            basis = (f"no other skill builds its Glory; its own hits (assumed to count for it), per use at one use/s: "
                     f"{how}: " + ("a use builds its own cost: at will" if rate else "less than a use's cost: never"))
        return rate, basis

    def temper_weapon(self, skills: list[dict]) -> tuple[list[dict], list[dict]]:
        """Temper Weapon's Combust (models.temper_weapon_model, GEM19): Combust's hit from its own set in Temper Weapon's
        group (bridge setRates on that skill), the main skill's DPS before this model's own line (its tagged DPS line in
        the last applied mods taken back out), uses a second and hit chance; Combust's group counted in Full DPS when it
        gets a rate. ([], []) without an enabled Temper Weapon and its Combust."""
        temper = next((s for s in skills if s["name"] == models.TEMPER and not s["disabled"]), None)
        combust = next((s for s in skills if s.get("skillId") == "TemperWeaponCombustionPlayer" and not s["disabled"]),
                       None)
        if temper is None or combust is None or not combust.get("group"):
            return [], []
        build = self.build
        group, name = build.main_group(), self.main_skill()
        main = next((models.Skill.of(s) for s in skills if not s["disabled"] and group and s.get("group") == group["index"]
                     and s["name"] == name), None)
        forbidden = set(build.ctx.request.forbid_ledger_entries)
        place = next((g for g in build.groups() if g["index"] == combust["group"]), None)
        names = [x.get("name") for x in (place or {}).get("skills") or []]
        if main is None or models.TEMPER_COMBUST not in names:
            return [], []
        sets = build.call("setRates", index=place["index"], active=names.index(models.TEMPER_COMBUST) + 1)["sets"]
        hit = number(sets[0].get("AverageDamage")) if sets else 0.0
        stats = build.call("stats", fields=["TotalDPS", "Speed", "HitChance"])
        own = sum(number(m.get("value")) for m in self.mods or []
                  if m.get("tag") == "TemperWeapon" and m.get("name") == "DPS")
        main_dps = number(stats.get("TotalDPS")) / (1 + own / 100) if own > -100 else number(stats.get("TotalDPS"))
        mods, notes = models.temper_weapon_model(
            main, main_dps, number(stats.get("Speed")), (number(stats.get("HitChance")) or 100.0) / 100, hit,
            number(temper.get("castTime")) or models.TEMPER_STRIKE,
            number(temper.get("computedCooldown")) or number(temper.get("cooldown")) or models.TEMPER_COOLDOWN, forbidden)
        if any(m.get("key") == "triggerRate" and number(m.get("value")) > 0 for m in mods):
            self.extra_groups.setdefault(place["index"], None)
        return mods, notes

    def apocalypse(self, skills: list[dict]) -> tuple[list[dict], list[dict]]:
        """Apocalypse's disasters (models.apocalypse_model) at the rate the build's Glory gives Apocalypse, wherever it
        is socketed. ([], []) without an enabled Apocalypse."""
        present = {s["name"] for s in skills if s["name"] in models.APOCALYPSE_SUBS}
        if not present:
            return [], []
        found = self.glory_rate(skills, models.APOCALYPSE)
        if found is None:
            return [], []
        if not math.isfinite(found[0]):
            found = (1 / models.APOCALYPSE_DURATION, found[1] + " (taken as kept up)")
        info = next(s for s in skills if s["name"] == models.APOCALYPSE)
        frequency = number((info.get("levelStats") or {}).get(models.APOCALYPSE_FREQUENCY))
        mods, notes = models.apocalypse_model(found[0], found[1], frequency, present)
        # The disasters count through Full DPS (MECHB8's engine probe POE2T1012: they sit in Apocalypse's own group, the
        # granted skill's, which PoB leaves out of Full DPS, so their rates alone moved nothing)
        groups = {s["group"] for s in skills if s["name"] in present and not s["disabled"] and s.get("group")}
        if any(m["key"] == "triggerRate" and m["value"] > 0 for m in mods):
            for group in groups:
                self.extra_groups.setdefault(group, None)
        return mods, notes

    def life_flask_charges(self) -> float:
        """The equipped Life Flask's maximum charges (Sanguine Tides' cap), 0 with none."""
        from builder.search.flasks import FLASK_SLOTS

        slot = next(k for k, v in FLASK_SLOTS.items() if v == "Life")
        option = self.build.equipped.get(slot)
        if option is None:
            return 0.0
        info = self.build.call("flaskData", raws=[option.raw])["results"][0]
        return float(info.get("chargesMax") or 0) if info.get("kind") else 0.0

    def flask_main(self, skills: list[dict], main: str | None) -> tuple[list[dict], dict] | None:
        """A Concoction as the main skill (flasks.THROW_CHARGES): each throw spends 5 Mana Flask charges, but PoB throws at
        will. Its uses per second, from the Mana Flask's own charge generation (flasks.charge_use_rate), become its trigger
        rate when lower than PoB's; capped at one per cast time. None for any other main skill (MECHC2)."""
        from builder.search.flasks import FLASK_SLOTS, THROW_CHARGES, THROW_POOL, Charges, charge_use_rate, throw_cost

        if main not in THROW_CHARGES:
            return None
        info = next((s for s in skills if s["name"] == main and not s["disabled"]), None)
        if info is None:
            return None
        gain = Charges.of(self.build)
        slot = next(k for k, v in FLASK_SLOTS.items() if v == THROW_POOL)
        raw = self.build.equipped[slot].raw if self.build.equipped.get(slot) is not None else None
        flask = self.build.call("flaskData", raws=[raw])["results"][0] if raw else None
        per_use, changed = throw_cost(gain, THROW_CHARGES[main])
        rate, how = charge_use_rate(flask, gain, THROW_POOL, per_use)
        how = f"{how}; {changed}" if changed else how
        skill = models.Skill.of(info)
        cap = 1 / skill.cast_time if skill.cast_time else math.inf
        rate = min(rate, cap)
        return models.trigger_rate(main, rate), {
            "label": f"{main} throws per second (Mana Flask charges)", "value": round(rate, 3),
            "basis": (f"each throw consumes {THROW_CHARGES[main]} charges from your Mana Flask (game files, "
                      f"flask_throw_base_charges_used); {how}; the charges the flask starts the fight with aren't "
                      "counted; the flask's own recovery and effect get none of them (flasks.thrown), and charges left over above "
                      f"{per_use} per cast time aren't counted"),
            "play": f"Throw {main} whenever your Mana Flask has {math.ceil(per_use - 1e-9)} charges.",
            "confidence": "theory: game files' throw cost; the flask's charge generation from PoB",
        }

    def mana_flare(self, skills: list[dict]) -> tuple[list[dict], dict] | None:
        """Mana Flare (models.mana_flare), as the main group's main skill or counted through Full DPS beside the spell it
        supports, in the main group or another one (2b75e: in Ice Nova's own group): its rate from that spell's Critical
        Hits, and its damage scaled to the mana it finds. The spell is calculated as the main skill for a moment for that
        (its group made the main group if it isn't). None when no group counts a flare beside another skill. PoB applies
        one rate to every Mana Flare by name, so with flares in several counted groups the first (the main group's
        first) sets it, noted."""
        info = next((s for s in skills if s["name"] == models.MANA_FLARE), None)
        if info is None:
            return None
        listing = self.build.call("groups")
        main_index = listing["mainSocketGroup"]
        counted = []  # (group, the spell's position, the group's main skill)
        for group in sorted(listing["groups"], key=lambda g: g["index"] != main_index):
            names = [s["name"] for s in group.get("skills") or []]
            if models.MANA_FLARE not in names or not group.get("enabled", True):
                continue
            flare, current = names.index(models.MANA_FLARE) + 1, group.get("mainActiveSkill") or 1
            spell = current if current != flare else next((i for i, n in enumerate(names, 1)
                                                           if n != models.MANA_FLARE), None)
            is_main = group["index"] == main_index
            if spell is None or not (group.get("fullDPS") or (is_main and current == flare)):
                continue
            if not is_main and main_index < 1:
                continue
            counted.append((group, spell, current))
        if not counted:
            return None
        group, spell, current = counted[0]
        index, names = group["index"], [s["name"] for s in group["skills"]]
        stats = self.stats_as_main(group, spell, main_index, models.MANA_FLARE_FIELDS)
        rate, share, basis = models.mana_flare(stats, info.get("cooldown") or 1.0, info.get("cooldownRecovery") or 1.0)
        mods = models.trigger_rate(models.MANA_FLARE, rate)
        if share < 1.0:  # (PoB calculates the flare at full mana)
            mods.append(models.line("1% more Damage", 100.0 * (share - 1.0), skill=models.MANA_FLARE))
        return mods, {
            "label": f"Mana Flare uses per second (supporting {names[spell - 1]})", "value": round(rate, 3),
            "basis": basis + ("" if group["index"] == main_index else f" (in socket group {index}, beside the main skill: "
                              "mana the main skill spends isn't taken from the flare's)")
                     + "".join(f"; the flare in socket group {g['index']} takes the same rate (PoB rates every Mana "
                               "Flare by name; not modelled apart)" for g, _, _ in counted[1:]),
            "play": "Mana Flare fires on the supported spell's Critical Hits, at most once a second, and spends a "
                    "quarter of the mana you have: keep casting the spell, and its damage depends on how much mana "
                    "you keep.",
            "confidence": "game files' trigger, cooldown and mana share; Critical Hits taken at their average rate",
        }

    def infusion_share(self, element: str, ailments: set[str] | dict[str, float],
                       casts: float | None = None) -> tuple[float, str]:
        """The share of the main skill's casts that find an Elemental Infusion of `element` (one consumed per cast):
        the build's supply (infusion_supply) over its casts per second, less those that keep the Infusion. `casts`: the
        main skill's casts per second when PoB's Speed isn't them (GEM16: Firestorm's one storm a Duration, models.
        firestorm_infusion_model); None reads PoB's Speed, net of the Remnant presses, as before."""
        build = self.build
        raw = build.call("activeSkills")["skills"]
        present = {s["name"]: s for s in raw if not s["disabled"]}
        lines = [line["text"] for line in build.call("sourceLines")["lines"]]
        siphon = siphon_freezes(build.call("stats", fields=models.IMMOBILISE_FIELDS)) if SIPHON in present else (0.0, "")
        bomb = living_bomb_rate(build.call("stats", fields=list(LIVING_BOMB_FIELDS)),
                                present[LIVING_BOMB].get("castTime") or 0.0) if LIVING_BOMB in present else (0.0, "")
        orb = orb_remnants(build, present[ORB_OF_STORMS]) if ORB_OF_STORMS in present else (0.0, "")
        supply, used = infusion_supply(present, lines, ailments, siphon, bomb, orb,
                                       far=still_far(build.call("getConfig")))
        keep = min(1.0, sum(float(m.group(1)) for t in lines if (m := KEEP_INFUSION.search(t.strip()))) / 100.0)
        if casts is not None:
            uses = max(0.0, float(casts))
        else:
            uses = float(build.call("stats", fields=["Speed"]).get("Speed") or 0.0)
            # (MECH46: the main skill's casts net of the time pressing Snap / Frost Bomb takes, whether or not that press
            # line is on PoB's numbers now)
            busy = (self.remnant_presses(raw, self.main_skill())[1] or [{"value": 0.0}])[0]["value"]
            uses *= (1.0 - busy) / (1.0 - self.press_busy)
        have = supply[element] + supply["Any"]
        others, other_how = self.other_infusion_users(element)
        needed = uses * (1.0 - keep)
        # (the main skill's share of the supply: every consumer takes Infusions as they come, so each gets its share of
        # uses; MECHC4, COVC4: before, the main skill had the whole supply)
        mine = have * uses / (uses + others) if uses + others > 0 else have
        share = min(1.0, mine / needed) if needed > 0 else float(have > 0)
        basis = (f"{have:.2f} {element} Infusions/s from {', '.join(used) or 'nothing'} for {uses:.2f} casts/s"
                 + (f", {keep:.0%} of casts keeping theirs" if keep else "")
                 + (f"; shared with {other_how}, so {mine:.2f}/s reach the main skill" if others > 0 else ""))
        return share, basis

    def other_infusion_users(self, element: str) -> tuple[float, str]:
        """Uses per second of the build's other pressed skills that consume an Infusion of `element` (MECHC4; COVC4: a
        skill with an `element`-Infused stat set, e.g. Fireball, Arc, Firestorm, Flame Wall, Incinerate, Frost Darts, Ember
        Fusillade), each at its press rate (models.press_model: its cooldown, its duration, else the rate the
        search chose, else 0). Skills a meta gem triggers aren't counted (their rate comes from the meta models: a small
        overcount of the main skill's share left, labelled); nor is the share they'd keep (KEEP_INFUSION)."""
        build = self.build
        groups = build.call("groups")
        main = groups["mainSocketGroup"]
        want = re.compile(rf"\b{element}-infused\b", re.I)
        users = {g["index"] for g in groups["groups"]
                 if g["index"] != main and g["enabled"] and pressed_group({**g, "fullDPS": True}, build.ctx.gems)
                 and any(want.search(label) for skill in g.get("skills") or [] for label in skill.get("statSets") or [])}
        if not users:
            return 0.0, ""
        rates = build.call("useRates", indices=sorted(users | {main}))["rates"]
        scales, _ = models.press_model(rates, main, users, choices=models.press_choices(build))
        total, how = 0.0, []
        for rate in rates:
            if rate["index"] in users and rate["name"] in scales:
                uses = scales[rate["name"]] * number(rate.get("speed"))
                total += uses
                how.append(f"{rate['name']} {uses:.2f}/s")
        return total, ", ".join(how)

    def ledger_conditions(self, skills: list[dict]) -> tuple[list[str], list[dict]]:
        """Ledger conditions the build's skills switch on (e.g. it uses a skill that requires Glory), and notes on those
        that need a rate (glory_window: a share from the build's Glory skill uses per second, models.glory_uses)."""
        if getattr(self.build, "objective", None) is not None:
            self.build.objective.glory_busy = 0.0  # (glory_uses_capped sets it again for a Glory share counted now)
        wanted = conditions()
        if not wanted:
            return [], []
        actives = [s["name"] for s in skills if not s["disabled"]]
        names = actives + [gem["name"] for group in self.build.groups() for gem in group["gems"]
                           if gem.get("enabled", True)]
        ctx = self.build.ctx
        texts = [gem_of(ctx, s["name"], s.get("skillId")).get("description") or "" for s in skills if not s["disabled"]]
        main = self.main_skill()
        main_id = next((s.get("skillId") for s in skills if s["name"] == main and not s["disabled"]), None)
        main_types = set(gem_of(ctx, main, main_id).get("skillTypes") or []) if main else set()
        main_group = self.build.main_group()
        main_info = next((s for s in skills if s["name"] == main and main_group and s.get("group") == main_group["index"]),
                         next((s for s in skills if s["name"] == main), {}))
        found, notes = [], []
        glory = None  # (models.glory_uses' answer, once for every glory_window condition)
        if not hasattr(self, "ledger_shares"):  # (set by wanted(); a unit test may call this alone)
            self.ledger_shares = {}
        self.ledger_derived = []  # (COV32: derived shares, models.derived; the bridge re-works them per calculation)
        # ledger conditions up for part of the fight (condition -> share): counted 0 in PoB, for the search to weigh
        self.ledger_part_time: dict[str, float] = {}
        from builder.search import boss_hits, bosses  # (COV12: the damage types the scored boss deals)
        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        dealt = {t for t, count in boss_hits.hit_table(boss)[1].items() if count}
        for condition in wanted:
            if condition.get("boss_hits"):
                # the share of the fight the scored boss has hit you with this damage type Recently (4 s): e.g. Adaptive
                # Skin's max resistance of each element "you have been Hit with Recently" (its `share:` lines)
                # (AUDIT25 A25-48) PoB counts the line whole only when it is up at least conditions.FULL_UPTIME (99.5%)
                # of the fight, as the defence floors read PoB's resistances and count only full uptime (the user's
                # rule); below that it counts 0 in PoB (an UNDERCOUNT outside the floors: damage taken, the play guide),
                # and its share waits in ledger_part_time for the search's part-time weighing (routed to SEARCH)
                kind = condition["boss_hits"]
                share, how = self.boss_hit_recently_share(boss, kind) if kind in dealt else (0.0, f"{boss} deals no {kind} hits")
                share = min(max(share, 0.0), 1.0)
                if share >= conditions_mod.FULL_UPTIME:
                    self.ledger_shares[condition["id"]] = 100
                    found.append(condition["id"])
                elif share > 0:
                    self.ledger_part_time[condition["id"]] = share
                if kind in dealt and self.has_line(r"Damage Type you have been Hit with Recently"):
                    counted = ("counted whole" if share >= conditions_mod.FULL_UPTIME else
                               "counted 0: under 99.5% of the fight, so not in the defence floors (an undercount in "
                               "damage taken, where its share would count; not weighed yet)")
                    notes.append({"label": f"{condition['title']}", "value": f"{share:.0%} up", "basis": f"{how}; {counted}",
                                  "confidence": "LOW (the boss's per-type hit rate: HIT_EVERY and its skills' type counts)"})
                continue
            if condition.get("ward_damaged_recently"):
                # (MECHC15, AUDIT26 A26-1 / COV18) "...if your Runic Ward has been damaged Recently" (the Warding Rune of
                # Bodyguards' Bonded line): the share of the fight within 4 s of a boss hit taken into Ward while Runic
                # Reprieve is channelled (ward_damaged_share). Like boss_hits: whole from FULL_UPTIME, else its share
                # waits in ledger_part_time (0 in PoB, so the defence floors read 0: the user's full-uptime rule)
                share, how = self.ward_damaged_share(skills)
                if share >= conditions_mod.FULL_UPTIME:
                    self.ledger_shares[condition["id"]] = 100
                    found.append(condition["id"])
                elif share > 0:
                    self.ledger_part_time[condition["id"]] = share
                counted = ("counted whole" if share >= conditions_mod.FULL_UPTIME else
                           "counted 0 in PoB: under 99.5% of the fight, so not in the defence floors (its share is "
                           "for the search's part-time weighing)" if share > 0 else "counted 0")
                # (MECHC19, COV22) the note only for a build with the line (before, a "0% up" note on every build)
                if self.has_line(r"Runic Ward has been damaged Recently"):
                    notes.append({"label": f"{condition['title']}", "value": f"{share:.0%} up",
                                  "basis": f"{how}; {counted}",
                                  "confidence": "LOW (play strategy: Runic Reprieve channelled into the boss's hits; the "
                                                "Ward each blocked hit takes is the hit's damage after mitigation, "
                                                "UNVERIFIED)"})
                continue
            if condition.get("warcries_recently"):
                # different Warcries used Recently (4 s), summed over the build's own Warcries (Escalation; COV13: before,
                # a flat half of the line's value whenever any Warcry was used Recently): its ledger lines are
                # `share:WarcriesRecently`, each counting its value x this sum (in whole percent, over 100 with several)
                count, how = self.warcries_recently()
                percent = round(100 * max(count, 0.0))
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                if self.has_line(r"for each different Warcry you've used Recently"):
                    notes.append({"label": condition["title"], "value": round(count, 2),
                                  "basis": f"{how}; its lines count {percent}% of their value",
                                  "play": "Use each Warcry on its cooldown: without them these lines add nothing",
                                  "confidence": "MEDIUM (each Warcry used on its cooldown; LOW where marked)"})
                continue
            if condition.get("spell_crits_recently"):
                # a count, not a share (COV17; Seed of Cataclysm's "5% reduced Critical Spell Damage Bonus per Critical
                # Hit you've dealt with Spells Recently", an OVERCOUNT while PoB dropped it): the main skill's Critical
                # Hits a second over the window (Recently, 4 s), in whole percent; its lines are `share:` lines. Only
                # for a build carrying such a line (no stats call otherwise)
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                count, how = self.spell_crits_recently(float(condition["spell_crits_recently"]), main_types)
                percent = round(100 * max(count, 0.0))
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": round(count, 1),
                              "basis": f"{how}; its lines count {percent}% of their value",
                              "confidence": "LOW (the main skill's hits only: other Spells' Critical Hits and a "
                                            "cast's extra hits are not counted, so the loss may be larger)"})
                continue
            if condition.get("mountain_splitter"):
                # (COV31, PATCH_CHECK G1-2) on / off from the build's own main skill: a Slam Attack you use yourself
                # (not triggered, not a totem's) that doesn't create Fissures (CreatesFissure, the game's skill type;
                # nor Crater in its group, which makes a leap create them). Only for a build carrying the line.
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                ok, how = mountain_splitter_slam(main, main_types, main_info, skills, main_group)
                if ok:
                    self.ledger_shares[condition["id"]] = 100
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": "yes" if ok else "no", "basis": how,
                              "confidence": "MEDIUM"})
                continue
            if condition.get("missing_mana_tens"):
                # (COV31, PATCH_CHECK L1-F) a count: the average tens of Mana missing, from the build's own drain and
                # regeneration (missing_mana_tens), in whole percent of a ten; its lines are `share:` lines
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                tens, how = self.mana_missing_tens()
                percent = round(100 * tens)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": round(tens, 2), "basis": how,
                              "confidence": "MEDIUM-LOW (Mana flasks, leech and recoup not counted: an overcount; other "
                                            "skills' costs not counted: an undercount)"})
                continue
            if condition.get("lethal_draw"):
                # (COV31, PATCH_CHECK G4-1) the share of the main skill's Bow Attacks that find 10% of the Life Flask's
                # maximum charges to consume, from that flask's own charge gain (flasks.charge_use_rate, MECH-C's)
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                share, how = self.lethal_draw_share(main, main_types)
                percent = round(100 * share)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": f"{share:.0%}", "basis": how,
                              "confidence": "MEDIUM (the flask's charge gain; LOW where the boss's adds are counted)"})
                continue
            if condition.get("mana_spent_recently"):
                # (COV32, PATCH_CHECK MB-4: Indigon) a count: the average whole N's of Mana the main skill spent in the
                # past 4 s (mana_spent_stacks), in whole percent; its lines are `share:` lines. A derived input
                # (bridge DERIVED.manaSpent), so a what-if's own Mana cost (the cost line's rise) feeds back
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                per = float(condition["mana_spent_recently"])
                stats = self.build.call("stats", fields=["ManaPerSecondCost", "ManaRegenRecovery", "ManaCost"])
                stacks, how = mana_spent_stacks(number(stats.get("ManaPerSecondCost")),
                                                number(stats.get("ManaRegenRecovery")), number(stats.get("ManaCost")), per)
                self.ledger_derived.append(models.derived("Share" + condition["id"], "manaSpent", 100 * stacks, per=per))
                notes.append({"label": condition["title"], "value": round(stacks, 2), "basis": how,
                              "play": "Keep casting the main skill: the stacks are the Mana it spent in the past 4 s",
                              "confidence": "MEDIUM-LOW (the main skill's own spending only: other skills' costs not "
                                            "counted, an undercount; steady casting from the fight's start)"})
                continue
            if condition.get("chilled_ground"):
                # (COV32) the share of the fight the boss stands on the build's Chilled Ground: MECH-A's
                # build.chilled_ground_up (MECH76; (0.0, "") without a source); Desert's Scorn's line is a `share:` line
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                share, how = self.chilled_ground_share()
                percent = round(100 * share)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": f"{share:.0%}", "basis": how,
                              "play": "Keep the boss on your Chilled Ground: off it, these lines add nothing",
                              "confidence": "MEDIUM-LOW (MECH-A's Chilled Ground share)"})
                continue
            if condition.get("ignited_ground"):
                # (COV39) the share of the fight the boss stands in the build's Ignited Ground: MECH-A's
                # build.ignited_ground_up (MECH79, "for COV-A"; (0.0, "") without a source); Desert's Scorn's Cold line
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                share, how = self.ignited_ground_share()
                percent = round(100 * share)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": f"{share:.0%}", "basis": how,
                              "play": "Keep the boss in your Ignited Ground: off it, these lines add nothing",
                              "confidence": "MEDIUM-LOW (MECH-A's Ignited Ground share)"})
                continue
            if condition.get("open_weakness"):
                # (COV33, H5-3; COV34) the share of the fight's time the boss has an Open Weakness: MECH-A's
                # conditions.open_weakness_up (MECH78), 0 without a source; these lines are `share:` lines
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                share, how = self.open_weakness_share()
                percent = round(100 * share)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": f"{share:.0%}", "basis": how,
                              "play": "Nothing to do: the boss's Life bar shows the weak segments; they last until its "
                                      "Life passes them",
                              "confidence": "MEDIUM (MECH-A's share of the fight's time: the game's 45% of the boss's "
                                            "Life, passed faster where the build deals more damage)"})
                continue
            if condition.get("ward_state"):
                # (COV32, NOTES3 DEAD-1 / DEAD-2) the share of the fight on Low / missing / full Runic Ward, from what the
                # build spends its Ward on (MECH-C's ward_shares); its lines are `share:` lines (items-runes-cov32.yaml)
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                share, how = self.ward_state_share(condition["ward_state"])
                percent = round(100 * min(max(share, 0.0), 1.0))
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": f"{share:.0%}", "basis": how,
                              "play": "Spend Runic Ward (its Ward-cost skills) to be on Low / missing Ward; don't, to "
                                      "stay full: these lines count only for that part of the fight",
                              "confidence": "MEDIUM-LOW (MECH-C's Ward spending; spends taken one at a time)"})
                continue
            if condition.get("enemy_power_hit"):
                # (COV35, PATCH_CHECK G3-3: Olrovasara) a count: the summed Power of the enemies the build hit in the
                # past N seconds, up to the line's cap (enemy_power_hit_count), in whole percent; its lines are `share:`
                if not self.has_line(condition.get("shown_for") or "$^"):
                    continue
                power, how = self.enemy_power_hit_count(float(condition["enemy_power_hit"]),
                                                        float(condition.get("cap") or 0), main, main_types)
                percent = round(100 * power)
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                notes.append({"label": condition["title"], "value": round(power, 1), "basis": how,
                              "play": "Hit the boss (and any adds) at least once every 20 seconds",
                              "confidence": "MEDIUM (the boss's Power with your Power lines); LOW for the adds"})
                continue
            if condition["id"] == "JaggedGround":  # (from the build's own sources, not names or text: MECH51)
                share, how = self.jagged_ground_share(main, main_types, main_group)
                # its ledger lines are `share:JaggedGround` (COV11): each counts its full value x this share (bridge
                # applyLedger; Multiplier:LedgerShareJaggedGround in whole percent, as PoB floors a Multiplier's count)
                percent = round(100 * min(max(share, 0.0), 1.0))
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                if share > 0 or any(re.search(condition.get("skills") or "$^", n, re.I) for n in names):
                    notes.append({"label": "Jagged Ground under the boss", "value": round(share, 2),
                                  "basis": f"{how}; its passive lines (Spike Pit, the Jagged Ground Aggravate chance) "
                                           f"count {percent}% of their value, the boss's share of the fight in it"
                                           + ("" if percent else " (none: no Jagged Ground source on the main skill)"),
                                  "play": "Keep the boss in your Jagged Ground: without it these lines add nothing, "
                                          "always in it they add their full value",
                                  "confidence": "LOW (each patch taken to be at the boss for its whole duration)"})
                continue
            by_name = condition.get("skills") and any(re.search(condition["skills"], n, re.I) for n in names)
            by_text = condition.get("skill_text") and any(re.search(condition["skill_text"], t, re.I) for t in texts)
            if by_text and condition.get("usage_stat"):
                rules = usage_rules(self.build)
                by_text = any(condition["usage_stat"] in ((rules.get(name) or {}).get("stats") or {})
                              for name, text in zip(actives, texts) if re.search(condition["skill_text"], text, re.I))
            # skill_types: the main skill has every one of these PoB skill types (e.g. Spell and Multicastable: it can Repeat)
            by_type = condition.get("skill_types") and set(condition["skill_types"]) <= main_types
            # not_skill_types / not_triggered: the main skill must also lack these types and not be Triggered in PoB's
            # calculation (a meta gem or trigger support adds Triggered; e.g. "Triggered skills, Instant skills, and
            # Channelling skills cannot Repeat", KeywordPopups Repeat)
            if by_type and (set(condition.get("not_skill_types") or []) & main_types
                            or condition.get("not_triggered") and main_info.get("triggered")):
                by_type = False
            if (by_name or by_text or by_type) and condition.get("glory_window"):
                # a share from the build's own Glory skill uses per second on the boss (models.glory_uses; COV14: before,
                # a flat one use per 6 s / per 12 s, against the user's rule on flat rates). glory_count: the average
                # number used in the past window (Call Upon the Deep, uncapped); else the share of the fight within the
                # window after a use (Thrill of the Fight), uses taken as evenly spaced. Its lines are `share:` lines.
                if glory is None:
                    stats = self.build.call("stats", fields=models.MODEL_FIELDS)
                    main_skill = next((models.Skill.of(s) for s in skills if not s["disabled"] and s["name"] == main), None)
                    glory_skills = [models.Skill.of(s) for s in skills if not s["disabled"]]
                    glory_texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]  # (MECHC13)
                    glory = self.glory_uses_capped(names, stats, main_skill, glory_skills, glory_texts)
                uses, how = glory
                window = float(condition["glory_window"])
                share = window * uses if condition.get("glory_count") else min(1.0, window * uses)
                percent = round(100 * max(share, 0.0))
                if percent > 0:
                    self.ledger_shares[condition["id"]] = percent
                    found.append(condition["id"])
                if condition.get("shown_for") and self.has_line(re.escape(condition["shown_for"])):
                    counted = (f"{share:.2f} uses in the past {window:g} s on average" if condition.get("glory_count")
                               else f"up {share:.0%} of the fight")
                    notes.append({"label": f"{condition['title']}: Glory skill uses on a boss",
                                  "value": f"one per {1 / uses:.1f} s" if uses > 0 else "none",
                                  "basis": f"{how}; {counted}, so its lines count {percent}% of their value",
                                  "play": "Use the Glory skill each time its Glory is full: without it these lines add nothing",
                                  "confidence": "theory (the Glory sources glory_uses counts; uses evenly spaced)"})
                continue
            if by_name or by_text or by_type:
                found.append(condition["id"])
        return sorted(found), notes

    def glory_uses_capped(self, names: list[str], stats: dict, main_skill, skills: list | None = None,
                          texts: list[str] | None = None) -> tuple[float, str]:
        """(Glory skill uses per second, how) for the ledger's Glory shares: models.glory_uses for each Glory skill, never
        faster than the skill's own use speed (the bridge's useRates "speed" for its group, as conditions.skill_use_rate
        caps every non-main skill). COV16: glory_uses counts a use whenever the Glory is there, so a skill that costs
        what one gain gives and holds no instance (Molten Blast: 20 per Ignite gain, one gain per 0.5 s) read 2 uses a
        second, and Call Upon the Deep's 20% penetration per use in the past 6 s read 240%: an OVERCOUNT where the
        skill can't be used that fast. SEARCH15 (the press time, before routed and charged nowhere): the main skill's time
        spent pressing them, each use taking 1 / its own speed (as Search.press_sync charges a press), goes in
        objective.glory_busy (Objective.busy), except the main skill itself, a pressed Full DPS group (press_model
        charges it) and a skill the search chose a press rate for (Search.press_sync charges it): no double charge."""
        glory_names = {n for n in names if n in models.GLORY_SKILLS or n in models.GLORY_FROM_LIFE}
        speeds: dict[str, float] = {}
        charged: set[str] = set()  # (press time charged elsewhere)
        groups = getattr(self.build, "groups", None)
        objective = getattr(self.build, "objective", None)
        if glory_names and callable(groups):  # (a stand-in build in a unit test may have none: no cap)
            listed = [g for g in groups() if g.get("enabled") is not False]
            gems = getattr(getattr(self.build, "ctx", None), "gems", None) or {}
            for g in listed:
                for n in g.get("gems") or []:
                    if n.get("name") in glory_names and (
                            g.get("main") or getattr(objective, "full", False) and gems and pressed_group(g, gems)
                            # (not pressed by the player at all: a meta skill or a totem uses it, no press time)
                            or gems and not pressed_group({**g, "enabled": True, "fullDPS": True, "fromItem": False},
                                                          gems)):
                        charged.add(n["name"])
            charged |= set(getattr(objective, "press_choices", None) or {})
            if main_skill is not None and getattr(main_skill, "name", None):
                charged.add(main_skill.name)
            found = {g["index"]: n["name"] for g in listed
                     for n in g.get("gems") or [] if n.get("enabled", True) and n.get("name") in glory_names}
            if found:
                for rate in self.build.call("useRates", indices=sorted(found))["rates"]:
                    name, speed = found.get(rate.get("index")), number(rate.get("speed"))
                    if name and speed > 0:
                        speeds[name] = max(speed, speeds.get(name, 0.0))
        total, how = models.glory_uses(names, stats, main_skill, skills, texts)  # (cooldowns, Glory lines: MECHC13)
        busy = 0.0
        for name, cap in speeds.items():  # (each capped skill's excess over its own speed comes back out)
            own = models.glory_uses([name], stats, main_skill, skills, texts)[0]
            if own > cap:
                total -= own - cap
                how += f"; {name} capped at its own {cap:.2f} uses/s (not {own:.2f})"
            if name not in charged:
                busy += min(own, cap) / cap
        if objective is not None and busy > 0:
            objective.glory_busy = round(min(busy, 1.0), 9)
            how += f"; pressing them takes {objective.glory_busy:.1%} of the fight from the main skill"
        return max(total, 0.0), how

    def warcries_recently(self) -> tuple[float, str]:
        """(different Warcries used Recently on average, how): each different Warcry in the build's enabled groups is up
        min(1, 4 s x its uses per second) of the fight, its uses from the bridge's useRates (conditions.skill_use_rate:
        its cooldown, else its duration, else the search's press choice, else 0); none in the build: 0."""
        ctx = self.build.ctx
        found = {g["index"]: n["name"] for g in self.build.groups() if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")
                 and "Warcry" in (gem_of(ctx, n["name"], n.get("skillId")).get("skillTypes") or [])}
        if not found:
            return 0.0, "no Warcry in the build's enabled groups: 0"
        shares: dict[str, tuple[float, str]] = {}
        for rate in self.build.call("useRates", indices=sorted(found))["rates"]:
            name = found.get(rate.get("index"))
            if name is None:
                continue
            each, why, low = conditions_mod.skill_use_rate(rate)
            share = min(1.0, conditions_mod.RECENTLY * each)
            if share >= shares.get(name, (-1.0, ""))[0]:  # (the same Warcry twice is one "different" Warcry)
                shares[name] = (share, f"{name} on {why}: used Recently {share:.0%} of the fight" + (" (LOW)" if low else ""))
        return sum(s for s, _ in shares.values()), "; ".join(h for _, h in shares.values()) or "no use rate: 0"

    def mana_missing_tens(self) -> tuple[float, str]:
        """(average tens of Mana missing, how) for Force of Will (COV31; see missing_mana_tens): the main skill's Mana
        spends and the boss's landed hits' share taken from Mana, against Mana regeneration."""
        from builder.search import objective

        stats = self.build.call("stats", fields=list(dict.fromkeys([
            *objective.RECOVERY_INPUTS, "ManaPerSecondCost", "ManaCost", "ManaRegenRecovery", "ManaUnreserved", "Mana"])))
        pool = number(stats.get("ManaUnreserved")) or number(stats.get("Mana"))
        spends: list[tuple[float, float]] = []
        cost, per_second = number(stats.get("ManaCost")), number(stats.get("ManaPerSecondCost"))
        if cost > 0 and per_second > 0:
            spends.append((per_second / cost, cost))
        lines = self.build.call("sourceLines")["lines"]
        from_mana = sum(float(m.group(1)) for line in lines if (m := MANA_BEFORE_LIFE.match(line["text"].strip())))
        hit_part = ""
        if from_mana > 0:
            taken, landed, _ = objective.boss_taken(stats)
            if landed > 0 and sum(taken.values()) > 0:
                spends.append((landed, sum(taken.values()) / landed * from_mana / 100))
                hit_part = (f"; {landed:.2f} boss hits/s land, {from_mana:g}% of each taken from Mana "
                            f"({spends[-1][1]:.0f} Mana a hit)")
        tens, how = missing_mana_tens(pool, number(stats.get("ManaRegenRecovery")), spends)
        return tens, how + hit_part

    def lethal_draw_share(self, main: str | None, main_types: set[str]) -> tuple[float, str]:
        """(share of the main skill's Bow Attacks that pay The Lethal Draw's cost, how) (COV31, PATCH_CHECK G4-1): "Bow
        Attacks consume 10% of your maximum Life Flask Charges if possible to deal added Physical damage equal to (5-10)%
        of Flask's Life Recovery amount" (patch 0.4.0). PoB adds the damage to every Bow Attack and ignores the cost (an
        OVERCOUNT). Here: the attacks a second the Life Flask's own charge gain pays for (flasks.charge_use_rate: its
        generation and the boss's adds killed; the stock a fight starts with isn't counted, an undercount for a short
        fight) over the main skill's attacks a second, at most 1. No Bow Attack main skill, no Life Flask or no charge
        gain: 0. Labelled: the same charges may also be counted for drinking the flask (MECH-C's recovery model), an
        OVERCOUNT where both are counted; "Flask Charges used" lines aren't applied to the cost (UNVERIFIED that they
        apply); other Bow Attacks in Full DPS get the main skill's share."""
        from builder.search.flasks import FLASK_SLOTS, Charges, charge_use_rate

        if not main or "Bow" not in main_types or "Attack" not in main_types:
            return 0.0, f"{main or 'no main skill'} is not a Bow Attack: its added damage counts 0"
        slot = next(k for k, v in FLASK_SLOTS.items() if v == "Life")
        item = self.build.equipped.get(slot)
        if item is None:
            return 0.0, "no Life Flask: nothing to consume, 0"
        flask = self.build.call("flaskData", raws=[item.raw])["results"][0]
        per_use = 0.1 * number(flask.get("chargesMax"))
        if per_use <= 0:
            return 0.0, "the Life Flask holds no charges: 0"
        paid, how = charge_use_rate(flask, Charges.of(self.build), "Life", per_use)
        speed = number(self.build.call("stats", fields=["Speed"]).get("Speed"))
        if speed <= 0:
            return 0.0, f"{main} has no attack rate: 0"
        share = min(1.0, paid / speed)
        return share, (f"{how}: {paid:.3f} attacks a second paid ({per_use:g} charges each, 10% of "
                       f"{number(flask.get('chargesMax')):g}) of {main}'s {speed:.2f}: {share:.1%} of its attacks deal "
                       "the added damage")

    def detonator_rate(self, main_index: int) -> tuple[float, str]:
        """(uses a second, how) of the build's Detonator skills outside the main group (GEM17, models.detonated_set_model):
        each enabled group holding a gem of PoB's "Detonator" skill type (as conditions' Oil lighters) at its useRates
        speed x the search's press share for it (PoB's rate when no share is chosen, said)."""
        build = self.build
        gems = build.ctx.gems
        indices = [g["index"] for g in build.groups() if g.get("enabled", True) and g["index"] != main_index
                   and any(conditions_mod.DETONATOR in ((gems.get(x["name"]) or {}).get("skillTypes") or ())
                           for x in g.get("gems") or [] if x.get("enabled", True))]
        if not indices:
            return 0.0, "no Detonator skill in the build"
        press = getattr(build.objective, "press_scales", None) or {}
        rate, parts = 0.0, []
        for row in build.call("useRates", indices=indices)["rates"]:
            share = press.get(row.get("name"))
            r = max(0.0, number(row.get("speed")) * (number(share) if share is not None else 1.0))
            rate += r
            parts.append(f"{row.get('name')} {r:.2f}/s" + (f" (pressed {number(share):.0%})" if share is not None
                                                           else " (PoB's rate: no press share chosen)"))
        return rate, "Detonators: " + "; ".join(parts)

    def stun_primers(self, main_index: int) -> tuple[float, str]:
        """(% of the boss's Heavy Stun bar a second, how) built by the build's enabled groups outside the main one (GEM18,
        models.stun_set_model): each group's useRates HeavyStunBuildupAvg x its hits a second (speed x HitChance), Cold's
        share out (models.stun_uncold), x the search's press share for it (a group with none isn't counted: the main skill
        has the time, as models.heavy_stun_buff_side)."""
        build = self.build
        indices = [g["index"] for g in build.groups() if g.get("enabled", True) and g["index"] != main_index]
        if not indices:
            return 0.0, "no other skill builds Heavy Stun"
        press = getattr(build.objective, "press_scales", None) or {}
        total, parts = 0.0, []
        for row in build.call("useRates", indices=indices)["rates"]:
            per_hit = number(row.get("HeavyStunBuildupAvg"))
            hits = number(row.get("speed")) * (number(row.get("HitChance", 100)) or 100.0) / 100
            if per_hit <= 0 or hits <= 0:
                continue
            share = press.get(row.get("name"))
            if not share:  # (not pressed beside the main skill: no time to use it, as heavy_stun_buff_side)
                parts.append(f"{row.get('name')} not counted (the press model gives it no uses)")
                continue
            built = per_hit * hits * models.stun_uncold(row) * number(share)
            total += built
            parts.append(f"{row.get('name')} {built:.3f}%/s (pressed {number(share):.0%})")
        return total, ("Heavy Stun buildup: " + "; ".join(parts)) if parts else "no other skill builds Heavy Stun"

    def shock_set_share(self, main_index: int, main_skill: str, consumes: bool) -> tuple[float, str]:
        """(share of the main skill's hits that find the boss Shocked, how) for a models.SHOCK_SETS skill (GEM19), which
        never Shocks itself: the resolver's Shocked share (build.ailment_uptimes, models.ailment_up_of: Shocked Ground,
        after the main skill's consume) joined with the build's other groups' Shocks, which that share doesn't hold (only
        Ignite has _ailment_others). Each other group: its uses a second (conditions.skill_use_rate with the search's
        press choices, as conditions.overcharge_sources; a triggered group at its own rate) x HitChance x its Shock
        chance per hit (the bridge's ShockChancePerHit, MECH86's fix in train 76: probe POE2T3457 read it, Orb of Storms
        0.053% a hit at level 90 with no chance-to-Shock line). A consumer (Siphoning Strike, Shockchain Arrow): conditions.consumed_share with those
        applications as `extra` (the Shock ends on the next hit, then 1 s immune); else each Shock lasts the main skill's
        ShockDuration (the global duration modifiers; a group's own not read), applications random in time:
        1 - e^(-applications x duration), joined with the resolver's share as independent."""
        build = self.build
        stats = dict(build.call("stats", fields=["Speed", "HitChance", "ShockDuration", *models.IMMOBILISE_FIELDS]))
        request = getattr(getattr(build, "ctx", None), "request", None)
        if getattr(request, "boss", None):  # (the boss's Electrocute threshold, as MECHC35's Stunned Recently reader)
            stats["_boss"] = request.boss
        up, up_how = models.ailment_up_of(build)
        base = up.get("Shock", 0.0)
        hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
        applied, others = self.other_shocks(main_index)
        share, how = self._shocked_hits(base, up_how, hits, applied, others, consumes, stats)
        if main_skill == "Siphoning Strike":  # (it reads Shock alone; the other two any Lightning Ailment)
            return share, how
        # (Electrocute is a Lightning Ailment too, KeywordPopups LightningAilment, and isn't consumed by them: the main
        # skill's own Electrocute share joins as independent (Shockburst Rounds' is 0: its -100% Electrocute
        # buildup); other skills' Electrocutes aren't published yet: not counted, an UNDERCOUNT, labelled)
        electro, electro_how = models.immobilised_share(stats, "Electrocute")
        if electro <= 0:
            return share, how
        return 1.0 - (1.0 - share) * (1.0 - electro), f"{how}; its own Electrocute {electro:.0%} ({electro_how})"

    def other_shocks(self, main_index: int) -> tuple[float, str]:
        """(the build's other groups' Shocks a second on the boss, how) (GEM19's shock_set_share loop; GEM20 shares it with
        the Galvanic Field model): each group's uses a second (conditions.skill_use_rate with the search's press
        choices; a triggered group at its own rate) x HitChance x its Shock chance per hit."""
        build = self.build
        indices = [g["index"] for g in build.groups() if g.get("enabled", True) and g["index"] != main_index]
        choices = models.press_choices(build)
        applied, parts = 0.0, []
        for row in (build.call("useRates", indices=indices)["rates"] if indices else []):
            # (PoB writes Shock's chance per hit as ShockChance (CalcOffence 5117, calcAilmentDamage); "<Ailment>ChancePerHit"
            # only for the damaging ailments (calcDamagingAilmentOutputs, 5196-5226): probe POE2T3426 found no
            # ShockChancePerHit even at 100%. MECH86 (train 76) makes the bridge write ShockChancePerHit: read either)
            chance = min(number(row.get("ShockChance")) or number(row.get("ShockChancePerHit")), 100.0) / 100.0
            if chance <= 0:
                continue
            uses = number(row.get("speed")) if row.get("triggered") else conditions_mod.skill_use_rate(row, choices)[0]
            rate = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0 * chance
            if rate > 0:
                applied += rate
                parts.append(f"{row.get('name')} {rate:.3g} Shocks/s ({chance:.3%} a hit)")
            else:
                parts.append(f"{row.get('name')} not counted (no uses: no cooldown, duration or press chosen)")
        return applied, ("; ".join(parts) or "no other skill Shocks the boss (no Shock chance on the other groups' rows)")

    @staticmethod
    def _shocked_hits(base: float, up_how: str, hits: float, applied: float, others: str, consumes: bool,
                      stats: dict) -> tuple[float, str]:
        """(share of the main skill's hits finding the boss Shocked, how) for shock_set_share (see there)."""
        if consumes:
            if hits <= 0:
                return 0.0, "the skill doesn't hit the boss"
            last = 1.0 / hits
            # (the resolver's share for a consumer that never Shocks is consumed_share(hits, 0, ground): the ground's
            # share back out of it, so the other groups' Shocks join it the way the resolver joins them for Ignite)
            ground = min(1.0, base * (last + models.CONSUME_IMMUNE) / last)
            share = conditions_mod.consumed_share(hits, 0.0, ground, extra=applied)
            return share, (f"{others}; Shocked Ground {ground:.0%}; each Shock consumed by the next of {hits:.2f} hits/s, "
                           f"then immune {models.CONSUME_IMMUNE:g} s (KeywordPopups Consume)")
        duration = number(stats.get("ShockDuration")) or conditions_mod.AILMENT_DURATION["Shock"]
        by_others = 1.0 - math.exp(-applied * duration) if applied > 0 else 0.0
        share = 1.0 - (1.0 - base) * (1.0 - by_others)
        return share, (f"{others}: Shocked {by_others:.0%} of the fight ({duration:.2f} s each, random in time); "
                       f"the resolver's share {base:.0%} ({up_how})")

    def gas_arrow_share(self, main_index: int, scored: str | None) -> tuple[float, str, float]:
        """(share of Gas Arrow's clouds that explode, how) for models.gas_arrow_model (GEM20): a Detonator use sets off
        every cloud lying there (detonator_rate, as detonated_share); the boss Ignited at some moment of a cloud's life
        (models.gas_ignite_within) with Ignites as the resolver counts them (conditions.ailment_uptime over the other
        groups' Ignites and Ignite grounds the resolver published, build.ailment_others / ailment_ground) plus the main
        skill's own: the Explosion set's at its uses x the share that explode (a fixed point from 0: no seed, no
        explosion), the Arrow set's at every use; the Poison Cloud set's fake hit taken to Ignite nothing."""
        build = self.build
        stats = dict(build.call("stats", fields=["Speed", "HitChance", "IgniteChancePerHit", "IgniteDuration"]))
        uses = number(stats.get("Speed"))
        if uses <= 0:
            return 0.0, "Gas Arrow isn't used", 1.0
        life = models.gas_cloud_life(uses)
        lying = min(float(models.GAS_CLOUD_LIMIT), uses * life)
        det_rate, det_how = self.detonator_rate(main_index)
        detonated = min(1.0, det_rate * lying / uses) if det_rate > 0 else 0.0
        duration = number(stats.get("IgniteDuration")) or conditions_mod.AILMENT_DURATION["Ignite"]
        others = dict(getattr(build, "ailment_others", None) or {})
        ground = dict(getattr(build, "ailment_ground", None) or {})
        own = scored in ("Explosion", "Arrow") and number(stats.get("IgniteChancePerHit")) > 0
        share, up, within = detonated, 0.0, 0.0
        for _ in range(40):  # (the fixed point, from the share Detonators alone give)
            own_uses = (uses * share if scored == "Explosion" else uses) if own else 0.0
            read = {"Speed": own_uses, "HitChance": stats.get("HitChance"),
                    "IgniteChancePerHit": stats.get("IgniteChancePerHit") if own else 0.0, "IgniteDuration": duration,
                    "_ailment_others": others, "_ailment_ground": ground}
            up = ailment_uptime(read, "Ignite")
            within = models.gas_ignite_within(up, duration, life)
            new = 1.0 - (1.0 - detonated) * (1.0 - within)
            if abs(new - share) < 1e-6:
                share = new
                break
            share = new
        # (GEM20, R30-5 (3)) a cloud's average life on the boss: 0 if the boss is Ignited when it lands, else until the
        # first new Ignite (applications at -ln(1 - up) / d a second, gas_ignite_within's) or Detonator use, at most its
        # life: its Poisons come one every 2 s while it lives (models.gas_cloud_poisons)
        ignites = -math.log(1.0 - up) / duration if 0 < up < 1 else 0.0
        ends = ignites + det_rate
        alive = 0.0 if up >= 1 else (1.0 - up) * ((1.0 - math.exp(-ends * life)) / ends if ends > 0 else life)
        poisons = models.gas_cloud_poisons(alive)
        others_n = len([o for o in others.get("Ignite") or () if o])
        source = ("the explosions' own Ignites" if own and scored == "Explosion" else
                  "the arrow's own Ignites" if own else "no own Ignite read")
        return share, (f"the boss Ignited {up:.1%} of the fight ({others_n} other Ignite source(s); {source}; "
                       f"{duration:.1f} s each), so at some moment of a cloud's {life:.1f} s {within:.1%}; "
                       f"Detonators set off {detonated:.0%} ({det_how}); a cloud lives {alive:.1f} s on average"), poisons

    def _side_rows(self, main_index: int, pick) -> list[tuple[dict, float, str]]:
        """[(useRates row, uses a second, how)] for the build's enabled groups outside the main one that `pick(group)`
        accepts (GEM20): a triggered group at PoB's trigger rate, else conditions.skill_use_rate with the search's press
        choices (its cooldown, its duration, else the chosen press rate, else 0: no source, 0)."""
        build = self.build
        indices = [g["index"] for g in build.groups() if g.get("enabled", True) and g["index"] != main_index and pick(g)]
        if not indices:
            return []
        choices = models.press_choices(build)
        out = []
        for row in build.call("useRates", indices=indices)["rates"]:
            if row.get("triggered"):
                out.append((row, number(row.get("speed")), "PoB's trigger rate"))
            else:
                uses, why, _ = conditions_mod.skill_use_rate(row, choices)
                out.append((row, uses, why))
        return out

    def locus_breaks(self, main_index: int, main_skill: str) -> tuple[float, str]:
        """(the build's Ice Crystal breaker uses a second, how) for models.frozen_locus_model (GEM20): its groups with an
        ICE_BREAKERS skill or a Warcry supported by Vruun's Aftermath (_side_rows), and its Freeze consumers
        (conditions.side_consumes, at its rates), each group once."""
        on = lambda g: [x["name"] for x in g.get("gems") or [] if x.get("enabled", True)]
        consumers = (conditions_mod.side_consumes(self.build, main_skill).get("Freeze") or (0.0, "", ""))
        named = set(re.findall(r"(?:^|; )([^;(]+?) \(", consumers[1]))  # (the consumers side_consumes names)
        rows = self._side_rows(main_index, lambda g: any(
            n in models.ICE_BREAKERS and n not in named for n in on(g)) or models.ICE_WARCRY_SUPPORT in on(g))
        total, parts = number(consumers[0]), ([f"Freeze consumers: {consumers[1]}"] if consumers[0] else [])
        for row, uses, why in rows:
            if uses <= 0:
                parts.append(f"{row.get('name')} not counted (no uses: {why})")
                continue
            total += uses
            parts.append(f"{row.get('name')} {uses:.2f} uses/s ({why})")
        return total, ("; ".join(parts) if parts else "no Freeze consumer, crystal-destroying skill or Vruun's "
                       "Aftermath Warcry in the build")

    def boss_hit_rate(self) -> tuple[float, str]:
        """(the boss's damaging uses a second, how) for a skill the boss's hits break (GEM20: Frozen Locus, Shield Wall):
        one every bosses.benefit_skill_time (LOW), each taken to land on the crystal or wall at its feet (LOW)."""
        from builder.search import bosses
        build = self.build
        read = dict(build.call("stats", fields=["enemySkillTime"]))
        request = getattr(getattr(build, "ctx", None), "request", None)
        if getattr(request, "boss", None):
            read["_boss"] = request.boss
        every = bosses.benefit_skill_time(read)
        return (1.0 / every if every > 0 else 0.0), (
            f"the boss's damaging uses, one every {every:.2f} s (bosses.benefit_skill_time, LOW), each taken to reach it "
            "(LOW: a Frozen Locus crystal at its feet, a Shield Wall between you)")

    def wall_shatters(self, main_index: int) -> tuple[float, str]:
        """(the build's Slam / Warcry / Shield Charge uses a second, how) for models.shield_wall_model (GEM20): groups
        whose enabled active gems carry PoB's Slam or Warcry skill type, or Shield Charge (_side_rows); looked up in every
        gem (ctx.all_gems), so an item-granted Slam counts."""
        gems = getattr(self.build.ctx, "all_gems", None) or self.build.ctx.gems

        def breaks(g):
            for x in g.get("gems") or []:
                if not x.get("enabled", True) or x.get("support"):
                    continue
                types = set((gems.get(x["name"]) or {}).get("skillTypes") or ())
                if x["name"] == models.SHIELD_WALL_CHARGE or types & set(models.SHIELD_WALL_BREAK_TYPES):
                    return True
            return False
        total, parts = 0.0, []
        for row, uses, why in self._side_rows(main_index, breaks):
            if uses <= 0:
                parts.append(f"{row.get('name')} not counted (no uses: {why})")
                continue
            total += uses
            parts.append(f"{row.get('name')} {uses:.2f} uses/s ({why})")
        return total, ("your shatters: " + "; ".join(parts)) if parts else "no Slam, Warcry or Shield Charge in the build"

    def spear_hits(self, main_index: int) -> tuple[float, str]:
        """(the build's other Spear-skill hits a second, how) for models.shattering_spite_model (GEM20): groups whose
        enabled active gems carry PoB's Spear skill type (_side_rows) x HitChance, one hit a use. Skills are looked up
        in every gem (ctx.all_gems, as MECH75's hitters), so the spear's own granted skills (Spear Stab, Spear Throw)
        count."""
        gems = getattr(self.build.ctx, "all_gems", None) or self.build.ctx.gems

        def spear(g):
            return any(x.get("enabled", True) and not x.get("support") and models.SPITE_LANCE_TYPE in
                       ((gems.get(x["name"]) or {}).get("skillTypes") or ()) for x in g.get("gems") or [])
        total, parts = 0.0, []
        for row, uses, why in self._side_rows(main_index, spear):
            hits = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0
            if hits <= 0:
                parts.append(f"{row.get('name')} not counted (no uses: {why})")
                continue
            total += hits
            parts.append(f"{row.get('name')} {hits:.2f} hits/s ({why})")
        return total, ("; ".join(parts) if parts else "no other Spear skill in the build")

    def lingering_arrows(self, main_index: int) -> tuple[float, float, str, float]:
        """(headline arrows a second, the MEDIUM-LOW row's arrows a second, how, the fastest headline maker's PoB speed)
        for models.magnetic_salvo_model (GEM20): the build's groups using a LINGERING_ARROWS / LINGERING_ARROWS_ROW skill
        (_side_rows) x its arrows a use."""
        every = {**models.LINGERING_ARROWS, **models.LINGERING_ARROWS_ROW}
        rows = self._side_rows(main_index, lambda g: any(
            x.get("enabled", True) and x["name"] in every for x in g.get("gems") or []))
        head, row_rate, parts, fastest = 0.0, 0.0, [], 0.0
        for row, uses, why in rows:
            name = row.get("name")
            per = every.get(name, 0)
            if name in models.LINGERING_ARROWS:
                fastest = max(fastest, number(row.get("speed")) * per)
            if uses <= 0 or not per:
                parts.append(f"{name} not counted (no uses: {why})")
                continue
            if name in models.LINGERING_ARROWS:
                head += uses * per
            else:
                row_rate += uses * per
            parts.append(f"{name} {uses * per:.2f}/s ({uses:.2f} uses/s, {why}"
                         + (", MEDIUM-LOW: the row" if name in models.LINGERING_ARROWS_ROW else "") + ")")
        return head, row_rate, ("; ".join(parts) if parts else "no Lightning Rod or Shockchain Arrow in the build"), fastest

    def gated_set_share(self, skill: str, texts: list[str], main_types: set[str]) -> tuple[float, str]:
        """(share, how) of a models.GATED_SETS skill's uses its resource covers (GEM17): Furious Slam's Rage from the
        build's Rage gains (conditions._rage_gain, the reader rage_held uses) over 10 a slam; Oil Barrage's Power Charges
        from the build's gains (conditions._charge_rate) x 1.5 s each."""
        stats = {**self.build.call("stats", fields=models.MODEL_FIELDS + ["MaximumRage", "enemySkillTime", "EvadeChance",
                                                                          "EffectiveBlockChance"]),
                 "_main_types": main_types}
        found = []
        if skill == "Furious Slam":
            gain = 0.0
            for t in texts:
                if conditions_mod.qualifies(t, main_types) and (r := conditions_mod._rage_gain(t, stats, True)) is not None:
                    gain += r[0]
                    found.append(f"{t[:50]} ({r[0]:.2g}/s)")
            uses, most = number(stats.get("Speed")), number(stats.get("MaximumRage"))
            share = models.furious_slam_share(gain, uses, most)
            return share, (f"{gain:.2f} Rage/s gained ({'; '.join(found[:3]) or 'no source'}) over "
                           f"{models.FURIOUS_RAGE_A_SLAM:g} a slam at {uses:.2f} slams/s, Maximum Rage {most:g}; other "
                           f"Rage spenders not taken off")
        kind = "Frenzy" if skill == "Glacial Lance" else "Power"
        gains = 0.0
        for t in texts:
            if (r := conditions_mod._charge_rate(t, kind, stats)) is not None:
                gains += r[0]
                found.append(f"{t[:50]} ({r[0]:.2g}/s)")
        if skill == "Glacial Lance":  # (GEM19: one Frenzy Charge a lance, if there is one)
            uses = number(stats.get("Speed"))
            share = min(1.0, gains / uses) if uses > 0 else 0.0
            return share, (f"{gains:.2f} Frenzy Charges/s gained ({'; '.join(found[:3]) or 'no source'}), one consumed a "
                           f"lance at {uses:.2f} lances/s; other Frenzy spenders not taken off")
        return models.oil_barrage_share(gains), (f"{gains:.2f} Power Charges/s gained ({'; '.join(found[:3]) or 'no source'})"
                                                 f", one consumed per {models.OIL_CHARGE_SECONDS:g} s of the barrage")

    def chilled_ground_share(self) -> tuple[float, str]:
        """(share of the fight the boss stands on the build's Chilled Ground, how) for BossOnChilledGround (COV32):
        MECH-A's build.chilled_ground_up (MECH76, conditions.chilled_ground), (0, ...) without a source or before it."""
        share, how = getattr(self.build, "chilled_ground_up", None) or (0.0, "")
        share = min(max(number(share), 0.0), 1.0)
        return share, how or "no Chilled Ground source under the boss: 0"

    def ignited_ground_share(self) -> tuple[float, str]:
        """(share of the fight the boss stands in the build's Ignited Ground, how) for BossOnIgnitedGround (COV39):
        MECH-A's build.ignited_ground_up (MECH79, conditions.ignited_ground), (0, ...) without a source or before it."""
        share, how = getattr(self.build, "ignited_ground_up", None) or (0.0, "")
        share = min(max(number(share), 0.0), 1.0)
        return share, how or "no Ignited Ground source under the boss: 0"

    # Sources that reveal weaknesses on a boss (COV33): the Mods / passive stats that do (checked: every Stats id holding
    # "weakness" with "open" or "reveal" and its carriers in Mods, PassiveSkills and the skill stat sets: Mods
    # UniqueRevealWeakness1 (The Master's Reach, Fists of Stone) and PassiveSkills AscendancyHuntress1Notable3 Predatory
    # Instinct (bloodlust_reveal_weakness); reveal_weakness_for_%_of_max_health has no carrier; poe2db's The Master's
    # Reach agrees). Only the item line is read here: Predatory Instinct is a tree line (for COV-B / MECH-A, named).
    REVEAL_WEAKNESS = r"^Reveal Weaknesses against Rare and Unique enemies$"

    def open_weakness_share(self) -> tuple[float, str]:
        """(share of the fight the boss has an Open Weakness, how) for the OpenWeakness condition (COV33, H5-3; COV34:
        one source of truth, the manager 12:43 / 13:14): MECH-A's conditions.open_weakness_up(build) (MECH78), the share
        of the fight's TIME, worked out from the build: the weak segments are 45% of the boss's Life (KeywordPopups
        RevealWeakness), gone through faster as the build deals more damage inside them (0.45 r / (0.45 r + 0.55), r =
        PoB's Full DPS inside over outside, probed with these stand-ins applied: no line counts twice, MECH78's probe
        POE2T3121). (0, ...) with nothing revealing weaknesses or before its first conditions round."""
        return conditions_mod.open_weakness_up(self.build)

    def ward_state_share(self, state: str) -> tuple[float, str]:
        """(share of the fight on Low / missing / full Runic Ward, how) for the `ward_state` conditions (COV32, NOTES3
        DEAD-1 / DEAD-2). From MECH-C's ward_shares(build) -> (Low share, Missing share, how), worked out from what the
        build spends its Ward on (0, 0 when nothing does); full = 1 - Missing. With no Runic Ward pool every state is 0
        (UNVERIFIED; the other reading, an empty pool on Low Runic Ward, is named in the note). Until MECH-C's function is on this base: 0, an UNDERCOUNT
        kept, as before."""
        shares = globals().get("ward_shares")  # (MECHC26's, in this module once its branch lands)
        if shares is None:
            return 0.0, ("not counted yet: the Runic Ward share (MECH-C's ward_shares) isn't in this build of the tool, so "
                         "these lines count 0 as before (an UNDERCOUNT)")
        pool = number(self.build.call("stats", fields=["Ward"]).get("Ward"))
        if pool <= 0:
            # (the manager, 15 Nov 03:52: decide the no-pool case) With no maximum Runic Ward every state counts 0.
            # "Missing" is false with 0 of 0; "Low" ("35% of your maximum Runic Ward or less", KeywordPopups LowWard)
            # read literally with a maximum of 0 would hold all fight, but nothing says the game reads it so (poe2db,
            # the rune pages, a search: UNVERIFIED), and PoB's "no Runic Ward" box (which sets Low and Missing) is for
            # current Ward 0 with a pool. That reading would make Desperation's 25% penetration free with no Ward: an
            # exploit if real, so it is named here, not scored. Full: not counted with no pool either.
            return 0.0, ("no Runic Ward pool: counted 0 (UNVERIFIED: if the game counts an empty pool as Low Runic "
                         "Ward, its Low lines would count all fight; Full and Missing would not)")
        low, missing, how = shares(self.build)
        if state == "low":
            return low, f"on Low Runic Ward (35% or less) {low:.0%} of the fight: {how}"
        if state == "missing":
            return missing, f"missing Runic Ward {missing:.0%} of the fight: {how}"
        return 1.0 - missing, f"on full Runic Ward {1.0 - missing:.0%} of the fight (missing it {missing:.0%}): {how}"

    def projectile_attack_rates(self, main: str | None, stats: dict) -> dict[str, tuple[float, str]]:
        """{Projectile Attack skill: (hits a second, how)} for models.projectile_hit_recently_model (COV35): the main
        skill's hits (PoB's Speed x hit chance) if its skill types hold Projectile and Attack, and each other enabled
        non-support gem with both types at its group's use rate (conditions.skill_use_rate: cooldown, duration or the
        search's chosen press rate, else 0), one hit a use."""
        build = self.build
        ctx = build.ctx

        def projectile_attack(name: str) -> bool:
            types = set(gem_of(ctx, name).get("skillTypes") or [])
            return {"Projectile", "Attack"} <= types

        out: dict[str, tuple[float, str]] = {}
        if main and projectile_attack(main):
            hits = models.hits_per_second(stats)
            out[main] = (hits, f"the main skill, {number(stats.get('Speed')):.2f} uses/s x its hit chance")
        groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
        where = {g["index"]: n["name"] for g in groups for n in g.get("gems") or []
                 if n.get("enabled", True) and not n.get("support") and n["name"] != main and projectile_attack(n["name"])}
        if where:
            choices = models.press_choices(build)
            for rate in build.call("useRates", indices=sorted(where))["rates"]:
                name = where.get(rate.get("index"))
                if name is not None and name not in out:
                    each, why, _ = conditions_mod.skill_use_rate(rate, choices)
                    out[name] = (each, f"on {why}")
        return out

    def enemy_power_hit_count(self, window: float, cap: float, main: str | None,
                              main_types: set[str]) -> tuple[float, str]:
        """(the summed Power of the enemies hit in the past `window` s, up to `cap`; how) for the `enemy_power_hit`
        conditions (COV35, PATCH_CHECK G3-3). Olrovasara's stat says what it counts: local_maximum_added_lightning_damage_
        equal_to_total_monster_power_of_enemies_hit_in_past_20_seconds_up_to_X (game Stats 20284): each enemy once, so
        a lone boss hit at all gives its Power (models.boss_power: 20, "Unique monsters always have 20 Power",
        KeywordPopups Power, raised by the build's Power lines). The boss preset's adds the build kills (build.kills,
        models.kill_rate; 0 on a lone boss) are each hit within the window they die in: kills/s x window, each at
        ADD_POWER (Normal, Power 1: the presets don't give the adds' rarity or Power, LOW, the lower reading). A build
        whose main skill doesn't hit counts 0 (other hitting skills beside it aren't read: LOW, an undercount)."""
        build = self.build
        stats = build.call("stats", fields=models.MODEL_FIELDS)
        if models.hits_per_second(stats) <= 0:
            return 0.0, "the main skill doesn't hit: no enemy hit, 0"
        texts = [e["text"] for e in build.call("sourceLines")["lines"]]
        distance = number(build.call("getConfig")["input"].get("enemyDistance")) or None
        boss, basis = models.boss_power(texts, {**stats, "_main_types": main_types}, distance,
                                        "Attack" in main_types, main or "")
        kills = getattr(build, "kills", None)
        enemies = float(kills[0]) if kills else 0.0
        adds = enemies * window * ADD_POWER
        total = boss + adds
        told = (f"the boss's Power {boss:g}" + (f" ({basis})" if basis else "")
                + (f" + {adds:.1f} from the adds ({enemies:.3f} kills/s x {window:g} s, Power {ADD_POWER:g} each, LOW)"
                   if adds > 0 else " (no adds killed: a lone boss)"))
        if cap and total > cap:
            return cap, f"{told}: {total:.1f}, capped at the line's {cap:g}"
        return total, told

    def spell_crits_recently(self, window: float, main_types: set[str]) -> tuple[float, str]:
        """(Critical Hits dealt with Spells in the past `window` seconds, how), on average over the fight (COV17): the main
        skill's uses a second x its hit chance x its Critical Hit chance (models.crits_per_second) x the window, when the
        main skill is a Spell; 0 otherwise (a Spell beside an Attack main skill isn't rated here). Each hit counts once,
        so a cast's extra hits (projectiles, repeats) and other Spells' Critical Hits are not counted: likely fewer
        counted than dealt (LOW; each cast taken to land, as PoB's own DPS takes it)."""
        if "Spell" not in main_types:
            return 0.0, "the main skill isn't a Spell"
        stats = self.build.call("stats", fields=models.MODEL_FIELDS)
        rate = models.crits_per_second(stats)
        return rate * window, (f"{rate:.2f} Critical Hits a second (the main skill: {number(stats.get('Speed')):.2f} uses/s x "
                               f"{number(stats.get('HitChance')):.0f}% hit x {number(stats.get('CritChance')):.1f}% "
                               f"Critical Hit chance) x {window:g} s")

    def warcry_uses(self) -> tuple[float, str]:
        """(Warcry uses per second, how): every Warcry in the build's enabled groups at its own use rate
        (conditions.skill_use_rate: its cooldown, else its duration, else the search's press choice, else 0) (MECHB18:
        Kaom's Blessing boosts one Fire Spell per Warcry use)."""
        ctx = self.build.ctx
        found = {g["index"]: n["name"] for g in self.build.groups() if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")
                 and "Warcry" in (gem_of(ctx, n["name"], n.get("skillId")).get("skillTypes") or [])}
        if not found:
            return 0.0, "no Warcry in the build's enabled groups"
        total, parts = 0.0, []
        for rate in self.build.call("useRates", indices=sorted(found))["rates"]:
            name = found.get(rate.get("index"))
            if name is None:
                continue
            each, why, low = conditions_mod.skill_use_rate(rate, models.press_choices(self.build))
            total += each
            parts.append(f"{name} {each:.3f}/s on {why}" + (" (LOW)" if low else ""))
        return total, "; ".join(parts) or "no use rate: 0"

    def has_line(self, pattern: str) -> bool:
        """Whether any of the build's source lines (passives and items, bridge sourceLines) matches `pattern`, or an
        item `share:` stand-in that replaced its line in place, as "LedgerShare:<Name>" (sourceLines' shares, COV32)."""
        call = getattr(self.build, "call", None)  # (a stand-in build in a unit test may have none)
        if not call:
            return False
        found = call("sourceLines")
        return any(re.search(pattern, line["text"]) for line in found["lines"])             or any(re.search(pattern, share) for share in found.get("shares") or ())

    def ward_damaged_share(self, skills: list[dict]) -> tuple[float, str]:
        """(share of the fight your Runic Ward has been damaged Recently (4 s), how) (MECHC15; AUDIT26 A26-1, COV18).
        Runic Ward takes otherwise-fatal damage (KeywordPopups Ward; not modelled: a build that survives rarely takes
        it) and, while Runic Reprieve is channelled, the damage of the hits it Blocks (ActiveSkills runic_reprieve:
        "damage you take from Hits is also removed from Runic Ward"; RunicReprievePlayer's
        non_ward_damage_taken_from_blocked_hits_is_taken_to_ward_as_well; poe2db Runic_Reprieve). A build carrying the
        line plays to feed it (CLAUDE.md): it channels Reprieve into the boss's hits, one at each of the boss's damaging
        uses (the "when hit" benefit pace, objective.benefit_every), as far as its Ward regeneration pays for the Ward each
        blocked hit takes (that hit's damage after mitigation, objective.boss_taken). Blocked hits come as a Poisson stream
        (LOW): the share is 1 - exp(-4 s x blocked hits a second). No Runic Reprieve: 0."""
        from builder.search import boss_hits, objective

        reprieve = next((sk for sk in skills if sk["name"] == RUNIC_REPRIEVE and not sk.get("disabled")), None)
        if reprieve is None:
            return 0.0, ("no Runic Reprieve: Runic Ward is damaged only by otherwise-fatal hits, which no model here "
                         "gives (0)")
        hit = self.build.call("stats", fields=list(dict.fromkeys([*objective.RECOVERY_INPUTS, "WardRegenRecovery",
                                                                    "Ward", "Life", "EnergyShield"])))
        every = objective.benefit_every(hit)
        taken, landed, _ = objective.boss_taken(hit, every)
        per_hit = sum(taken.values()) / landed if landed > 0 else 0.0
        uses = 1.0 / every if every > 0 else 0.0
        regen = number(hit.get("WardRegenRecovery"))
        paid = min(uses, regen / per_hit) if per_hit > 0 else uses
        if per_hit > 0 and regen <= 0:
            paid = 0.0
        share = 1.0 - math.exp(-ROUGHLY_RECENTLY * paid)
        windup = number(reprieve.get("castTime")) * CHANNEL_START_REPRIEVE
        self.build.reprieve_paid = (paid, windup)  # (MECHC35: models() takes the channels' time and Heavy Stuns off)
        return share, (f"Runic Reprieve channelled into {paid:.2f} of the boss's {uses:.2f} damaging uses a second, as "
                       f"Runic Ward regeneration {regen:,.0f}/s pays for {per_hit:,.0f} Ward a blocked hit (PoB's "
                       "WardRegenRecovery; other Ward sources and Ward-cost skills' spending not counted here; the "
                       "20% of maximum Ward Reprieve needs to start not checked), within 4 s of one "
                       f"{share:.0%} of the fight (Poisson, LOW). Each channel's {windup:.2f} s wind-up "
                       f"({paid * windup:.1%} of the fight) and its Heavy Stuns come off the main skill (MECHC35)")

    @staticmethod
    def boss_hit_recently_share(boss: str, kind: str) -> tuple[float, str]:
        """(share of the fight you have been Hit with `kind` damage by the boss Recently, how) (COV12; AUDIT25 A25-42).
        The boss uses a damaging skill every bosses.HIT_EVERY s (LOW); a share counts[kind] / damaging skills of them deal
        the type (boss_hits.HIT_TABLE over RED_FLASH's damaging-skill count, capped at 1). The share of the fight hit
        Recently (4 s, KeywordPopups) is a Poisson stream's, 1 - e^(-4 s x rate) (AUDIT25 A25-48: a hit every 4 s is 63%,
        not always; 99.5% needs one about every 0.75 s). No rate in the tables: 0, labelled. Before the player's evasion
        and block, and before Slows on the boss (a small overcount for an evasive build or one that Slows the boss)."""
        import math

        from builder.search import boss_hits, bosses

        if boss == bosses.WORST:
            # (MECHC20: a gain the boss's hits give, so the worst-case pinnacle reads the lowest real preset's share, as
            # boss_hits.gain_floor does (AUDIT29 A29-1's rule); before, its pooled type counts over its slowest cadence)
            rows = [(name, *Assumptions.boss_hit_recently_share(name, kind)) for name in bosses.REAL]
            low, high = min(rows, key=lambda r: r[1]), max(rows, key=lambda r: r[1])
            return low[1], (f"worst case: a gain counts the lowest real boss's share (AUDIT29 A29-1), {low[0]}'s: {low[2]} "
                            f"(the highest, {high[0]}'s: {high[1]:.0%}, not counted)")
        counts = boss_hits.hit_table(boss)[1]
        every = bosses.HIT_EVERY.get(boss)
        damaging = (boss_hits.RED_FLASH.get(boss) or (0, 0, 0, ""))[2]
        if not counts.get(kind):
            return 0.0, f"{boss} deals no {kind} hits"
        if not every or not damaging:
            # (MECHC12: the worst-case default has both since boss_hits._worst; this is for a preset without them)
            return 0.0, (f"{boss}: no use cadence or damaging-skill count in the tables (bosses.HIT_EVERY / "
                         f"boss_hits.RED_FLASH), so 0 (no flat guess; an undercount if it hits with {kind} often)")
        interval = every / min(1.0, counts[kind] / damaging)
        share = 1.0 - math.exp(-4.0 / interval)
        return share, (f"{boss} hits with {kind} about every {interval:.1f} s ({counts[kind]} of its {damaging} damaging "
                       f"skills, one use every {every:g} s): up {share:.0%} of the fight (1 - e^(-4 s / {interval:.1f} s))")

    def jagged_ground_share(self, main: str | None, main_types: set[str], main_group: dict | None) -> tuple[float, str]:
        """conditions.jagged_ground from the build: the main skill's speed, Endurance Charges gained per second (the build's
        own sources, conditions._charge_rate), its Aftershock chance and the Rage it holds."""
        main_gems = {g["name"] for g in (main_group or {}).get("gems") or [] if g.get("enabled", True)}
        if not main or not ({"Earthquake", "Stampede", "Furious Slam"} & {main} or {"Jagged Ground I", "Jagged Ground II"} & main_gems):
            return 0.0, "no source of Jagged Ground on the main skill"
        stats = {**self.build.call("stats", fields=models.MODEL_FIELDS + ["enemySkillTime", "EvadeChance",
                                                                          "EffectiveBlockChance"]),
                 "_main_types": main_types}
        texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        gains = 0.0
        if "Jagged Ground I" in main_gems:
            gains = sum(r[0] for t in texts if (r := conditions_mod._charge_rate(t, "Endurance", stats)) is not None)
        aftershock = 0.0
        if "Jagged Ground II" in main_gems:
            aftershock = models.aftershock_chance(texts, (main_group or {}).get("gems") or [], True, True,
                                                  any("Shapeshift" in t for t in main_types))[0]
        rage = conditions_mod.rage_held(texts, stats)[0] if main == "Furious Slam" else 0.0
        return conditions_mod.jagged_ground(main, main_types, main_gems, stats, gains, aftershock, rage, texts)

    def skill_spot(self, index: int | None, name: str) -> tuple[int, int] | None:
        """(group index, active position) of the named skill in that group, for setRates / useRates."""
        return next(((g["index"], position) for g in self.build.groups() if g["index"] == index
                     for position, skill in enumerate(g["skills"], start=1) if skill["name"] == name), None)

    def dusk_vigil(self, texts: list[str], stats: dict, found: list[models.Skill], main: models.Skill | None
                   ) -> tuple[list[dict], list[dict], set[int]]:
        """Dusk Vigil's triggered Embers (models.dusk_vigil_model) with the Ember's hit and wait from PoB."""
        if not any(models.DUSK_VIGIL.search(t.strip()) for t in texts):
            return [], [], set()
        build = self.build
        granted = next((s for s in found if s.name == models.EMBER_SKILL and s.group is not None
                        and (main is None or s.group != main.group)), None)
        hit, wait, per = 0.0, None, 1.0
        spot = self.skill_spot(granted.group, models.EMBER_SKILL) if granted is not None else None
        if spot is not None:
            answer = build.call("setRates", index=spot[0], active=spot[1])
            sets = answer.get("sets") or []
            row = next((s for s in sets if s.get("label") == (granted.stat_set or answer.get("chosen"))),
                       sets[0] if sets else {})
            hit = number(row.get("AverageDamage"))
            per = max(number(row.get("ProjectileCount")), 1.0)  # (MECHB38: extra projectiles add Embers, 0.1.1f)
            duration = next(iter(build.call("useRates", indices=[spot[0]], actives=[spot[1]])["rates"]), {}).get("duration")
            wait = number(duration) if duration is not None else None  # (MECHB38: 0 when fully reduced, not the base)
        # the main skill's damage a second without this model's own "less" (installed from the last pass)
        before = 1.0
        for mod in self.mods or []:
            if mod.get("tag") == models.DUSK_TAG:
                before *= 1 + number(mod.get("value")) / 100
        dps = per_second({**stats, **build.call("stats", fields=list(LIVING_BOMB_FIELDS))}) / (before or 1.0)
        mods, notes, groups, _ = models.dusk_vigil_model(texts, stats, found, main, dps, hit, wait, per)
        return mods, notes, groups

    def tempest_caller(self, texts: list[str], stats: dict, found: list[models.Skill], main: models.Skill | None
                       ) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """Tempest Caller's storms (models.tempest_caller_model) with the storm's numbers from PoB."""
        if not any(models.TEMPEST_CALLER.search(t.strip()) for t in texts):
            return [], [], {}
        build = self.build
        granted = next((s for s in found if s.name == models.STORM_SKILL and s.group is not None
                        and (main is None or s.group != main.group)), None)
        spot = self.skill_spot(granted.group, models.STORM_SKILL) if granted is not None else None
        storm: dict = {}
        if spot is not None:
            element = models.storm_element(build.call("stats", fields=list(models.STORM_FIELDS)))
            sets = build.call("setRates", index=spot[0], active=spot[1]).get("sets") or []
            row = next((s for s in sets if element and element.lower() in (s.get("label") or "").lower()), None)
            rate = next(iter(build.call("useRates", indices=[spot[0]], actives=[spot[1]])["rates"]), {})
            storm = {"hit": number((row or {}).get("AverageDamage")), "speed": number((row or {}).get("Speed")),
                     "cooldown": rate.get("cooldown"), "duration": rate.get("duration"), "element": element,
                     "set": (row or {}).get("index")}
        mods, notes, groups = models.tempest_caller_model(texts, stats, found, main, storm)
        return mods, notes, {g: storm.get("set") for g in groups}

    def corpses_consumed(self, found: list[models.Skill], main: models.Skill | None, stats: dict) -> tuple[float, str]:
        """models.corpses_consumed with each corpse skill's uses a second (the main skill's Speed, else PoB's useRates)."""
        consumers: list[tuple[str, float]] = []
        for skill in found:
            if not skill.corpses or skill.group is None:
                continue
            if main is not None and skill.name == main.name and skill.group == main.group:
                consumers.append((skill.name, number(stats.get("Speed"))))
                continue
            spot = self.skill_spot(skill.group, skill.name)
            rate = next(iter(self.build.call("useRates", indices=[spot[0]], actives=[spot[1]])["rates"]), {}) if spot else {}
            consumers.append((skill.name, number(rate.get("speed"))))
        present = {s.name for s in found}
        how = models.add_corpses_note(stats) + (f"; {models.sacrifice_note(stats)}" if models.SACRIFICE in present else "")
        return models.corpses_consumed(consumers, models.corpse_supply(present, stats), how)

    def valakos_charge(self, stats: dict, found: list[models.Skill], main: models.Skill | None
                       ) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """Valako's Charge from the Lightning damage the build takes (models.valako_model), with PoB's threshold, rate
        and the boss's hits; as a side skill its group goes into Full DPS with its Bolt set."""
        skill = next((s for s in found if s.name == models.VALAKO_SKILL and s.group is not None), None)
        if skill is None:
            return [], [], {}
        build = self.build
        is_main = main is not None and main.name == models.VALAKO_SKILL and main.group == skill.group
        read = {**stats, **build.call("stats", fields=list(models.VALAKO_FIELDS))}
        bolt = None
        if is_main:
            pob_rate = number(stats.get("Speed"))
            threshold = number(read.get("CWDTThreshold"))
        else:
            spot = self.skill_spot(skill.group, models.VALAKO_SKILL)
            if spot is None:
                return [], [], {}
            rate = next(iter(build.call("useRates", indices=[spot[0]], actives=[spot[1]])["rates"]), {})
            pob_rate = number(rate.get("speed"))
            if pob_rate <= 0 and number(rate.get("cooldown")) > 0:  # (PoB's side calculation gives it no speed)
                pob_rate = 1 / number(rate.get("cooldown"))
            sets = build.call("setRates", index=spot[0], active=spot[1]).get("sets") or []
            bolt = next((s.get("index") for s in sets if "bolt" in (s.get("label") or "").lower()), None)
            threshold = 0.0
        if threshold <= 0:  # (the level's own stat: PoB's CWDTThreshold is only made for the main skill)
            raw = build.call("activeSkills", levelStats=[models.VALAKO_SKILL])["skills"]
            threshold = max((number((s.get("levelStats") or {}).get(models.VALAKO_THRESHOLD)) for s in raw
                             if s["name"] == models.VALAKO_SKILL), default=0.0)
        cooldown = 1 / pob_rate if pob_rate > 0 else 0.0
        mods, notes, rate = models.valako_model(skill, is_main, read, threshold, pob_rate, cooldown)
        return mods, notes, ({skill.group: bolt} if not is_main and rate > 0 else {})

    def cannons_ready(self, texts: list[str], found: list[models.Skill]) -> tuple[list[dict], list[dict]]:
        """Cannons, Ready!'s totems at the player's projectile attacks (models.cannons_ready_model): each totem skill's
        own uses a second and the player's fastest projectile Attack, from PoB's useRates."""
        if not any(models.CANNONS_READY.search(t.strip()) for t in texts):
            return [], []
        build = self.build
        before: dict[str, float] = {}  # this model's own Speed "less" from the last pass, divided back out
        for mod in self.mods or []:
            if mod.get("tag") == models.CANNONS_TAG and mod.get("name") == "Speed":
                before[mod.get("skill")] = before.get(mod.get("skill"), 1.0) * (1 + number(mod.get("value")) / 100)
        totems: list[tuple[str, float]] = []
        player: tuple[str, float] | None = None
        for skill in found:
            if skill.group is None or not (skill.by_totem or (skill.projectile_attack and not skill.triggered)):
                continue
            spot = self.skill_spot(skill.group, skill.name)
            if spot is None:
                continue
            rate = next(iter(build.call("useRates", indices=[spot[0]], actives=[spot[1]])["rates"]), {})
            speed = number(rate.get("speed"))
            if skill.by_totem:
                totems.append((skill.name, speed / (before.get(skill.name) or 1.0)))
            elif player is None or speed > player[1]:
                player = (skill.name, speed)
        return models.cannons_ready_model(texts, totems, player)

    def apply(self) -> bool:
        """Match PoB's assumed modifiers to the build. True if they changed."""
        mods, notes = self.wanted()
        self.active = notes
        build = self.build
        flagged = False
        # (scored on Full DPS once there are extra groups: the main skill's group must count in it too, which a loaded
        # build's own flags don't promise)
        main = build.main_group() if self.extra_groups else None
        wanted = {**({main["index"]: None} if main else {}), **self.extra_groups}
        for group in build.groups():
            index = group["index"]
            if index not in wanted:
                continue
            stat_set = wanted[index]
            if not group["fullDPS"] or (stat_set is not None and self.stat_sets.get(index) != stat_set):
                build.set_group(index=index, fullDPS=True, **({"statSet": stat_set} if stat_set else {}))
                if stat_set:
                    self.stat_sets[index] = stat_set
                flagged = True
        extra = bool(self.extra_groups)
        switched = build.objective.extra != extra
        build.objective.extra = extra
        if mods == self.mods:
            if switched or flagged:
                build.refresh()
            return switched or flagged
        self.mods = mods
        build._committed(build.call("setAssumptions", mods=mods, fields=build.objective.fields))
        return True

    def reinstall(self) -> None:
        """After a build was (re)loaded, which drops engine-side assumptions (and may not keep a
        granted skill's chosen stat set)."""
        for index, stat_set in self.stat_sets.items():
            if self.extra_groups.get(index) == stat_set:
                self.build.call("setGroup", index=index, fullDPS=True, statSet=stat_set, fields=[])
        if self.mods:
            self.build._committed(self.build.call("setAssumptions", mods=self.mods, fields=self.build.objective.fields))

    def relevant(self, gem_name: str) -> bool:
        """Would adding this gem (or a passive granting this skill) change an assumption, so it
        deserves a real trial?"""
        if self.mods and gem_name in REMNANT_SOURCES:
            return True
        if gem_name == models.EXPRESSION:
            return True  # its trigger rate and Full DPS share only arrive with the assumptions
        if gem_name == models.APOCALYPSE:
            return True  # its disasters' rates and Full DPS share only arrive with the assumptions (MECHB8)
        text = (self.build.ctx.all_gems.get(gem_name) or {}).get("description") or ""
        return any((c.get("skills") and re.search(c["skills"], gem_name, re.I))
                   or (c.get("skill_text") and re.search(c["skill_text"], text, re.I)) for c in conditions())


# Mountain Splitter (COV31, PATCH_CHECK G1-2): "Every Third Slam Skill that doesn't create Fissures which you use yourself
# causes 3 additional Aftershocks ahead and to each side of the initial area" (Titan notable, PassiveSkills
# AscendancyWarrior1Notable3, num_cascade_aftershocks_every_third_slam 3; patch 0.4.0, the one and newest entry). Which
# slams create Fissures: the game's ActiveSkillType CreatesFissure (ActiveSkills, 26476662: Earthshatter, Forge Hammer,
# Fury of the Mountain, Molten Crash, Starborn Onslaught, Sunder, Volcanic Fissure among the Slams; PoB's gem data gives
# the same seven), and the Crater support ("causing them to create Fissures when you land"; SupportCraterPlayer's
# triggered_by_fissure_on_landing / create_X_fissures_on_impact stats). (Checked: every game stat set holding a fissure
# stat, base_number_of_fissures on Forge Hammer and Starborn Onslaught, additional-fissure supports only add to skills
# that already create them; UNVERIFIED whether any other support or item makes a Slam create Fissures.)
CRATER = "Crater"


# (COV35, PATCH_CHECK G3-3) An add's Power for Olrovasara's "total monster Power of enemies hit": the boss presets give
# the adds' kill rate but not their rarity or Power (conditions.kill_events reads them as Normal or Magic); Normal = 1
# ("An average monster has a Power of 1 ... Normal: 1, Magic: 2, Rare: 5", KeywordPopups Power). LOW, the lower reading.
ADD_POWER = 1.0


def mountain_splitter_slam(main: str | None, main_types: set[str], main_info: dict, skills: list[dict],
                           main_group: dict | None) -> tuple[bool, str]:
    """(is the main skill a Slam Mountain Splitter counts, why)."""
    if not main:
        return False, "no main skill: 0"
    if "Slam" not in main_types or not main_info.get("attack", "Attack" in main_types):
        return False, f"{main} is not a Slam Attack: 0"
    if "CreatesFissure" in main_types:
        return False, f"{main} creates Fissures (the game's CreatesFissure type): 0"
    gems = (main_group or {}).get("gems") or []
    if any(g.get("name") == CRATER and g.get("enabled", True) for g in gems):
        return False, f"{main} is supported by Crater, so it creates Fissures when you land: 0"
    if main_info.get("triggered"):
        return False, f"{main} is triggered, not used yourself: 0"
    group = main_info.get("group")
    if group is not None and any(s.get("name") in models.TOTEM_METAS and s.get("group") == group for s in skills):
        return False, f"{main} is used by a totem, not yourself: 0"
    return True, (f"{main}: a Slam you use yourself that creates no Fissures, so every third use causes the 3 "
                  "Aftershocks")


# Force of Will (COV31, PATCH_CHECK L1-F): "20% increased Effect of Arcane Surge on you per ten percent missing Mana"
# (Stormweaver notable AscendancySorceress1Notable2, beside "20% of Damage is taken from Mana before Life"; patch 0.3.0,
# the newest entry). Before, the ledger took 25% of Mana missing all fight (2.5 tens): a flat rate the build decides.
# Now the tens come from the build's own Mana: what drains it (the main skill's Mana per second, PoB's ManaPerSecondCost
# at ManaCost a use; the share of each landed boss hit taken from Mana, objective.boss_taken x the build's "N% of
# Damage is taken from Mana before Life" lines) against what refills it (PoB's ManaRegenRecovery).
#   - Drain below regeneration: each spend or hit of x Mana is a dip that refills over x / R s; while it refills, Mana is
#     missing k tens for (x - k M / 10) / R s (M the unreserved pool). The dips are taken one at a time (no overlap: an
#     UNDERCOUNT as the drain nears R), so a build whose spends and hits are each under a tenth of its pool gets 0.
#   - Drain at or above regeneration: the pool runs dry and stays between empty and one use's cost (the time it takes
#     to drain not counted: an OVERCOUNT at a fight's start), averaged over that range.
# Labelled: Mana flasks, leech and recoup refilling faster aren't counted (an OVERCOUNT of missing Mana: MECH-C's
# recovery model could give a fuller refill rate); other skills' Mana costs aren't counted (an UNDERCOUNT); reserved Mana
# taken as not missing (UNVERIFIED either way); a hit's share from Mana taken as the lines' sum, uncapped at 100%.
# Whole tens only, rounded down (RESEARCH26 R26-7, ASSUMED MEDIUM-LOW: the game's integer "per X" rule; PoB floors too;
# force-of-will-r26.md): the k tens above are whole.
MANA_BEFORE_LIFE = re.compile(r"^(\d+(?:\.\d+)?)% of Damage is taken from Mana before Life$", re.I)


def missing_mana_tens(pool: float, regen: float, spends: list[tuple[float, float]]) -> tuple[float, str]:
    """(average tens of Mana missing, 0-10, how): `spends` (rate a second, Mana each) are what drains the pool."""
    drain = sum(rate * size for rate, size in spends if rate > 0 and size > 0)
    if pool <= 0:
        return 0.0, "no unreserved Mana pool: 0"
    if drain <= 0:
        return 0.0, "nothing drains Mana (no Mana cost on the main skill, no hit taken from Mana): full Mana, 0 tens"
    if regen <= drain:
        low = min(max((pool - max(size for _, size in spends)) / pool, 0.0), 1.0)  # missing from 1 - cost / M ...
        steps = 400
        tens = sum(min(10.0, math.floor(10 * (low + (1 - low) * (i + 0.5) / steps))) for i in range(steps)) / steps
        return tens, (f"{drain:.1f} Mana/s drained against {regen:.1f} regenerated: the pool runs dry and stays within "
                      f"one use of empty, {tens:.2f} tens missing on average (the drain's first seconds not counted)")
    tens = 0.0
    for rate, size in spends:
        if rate > 0 and size > 0:
            tens += rate * sum(max(0.0, size - k * pool / 10) for k in range(1, 11)) / regen
    tens = min(tens, 10.0)
    return tens, (f"{drain:.1f} Mana/s drained against {regen:.1f} regenerated, each dip refilling at once after: "
                  f"{tens:.2f} tens missing on average (dips under a tenth of the {pool:.0f} pool count 0)")


# Facebreaker (COV32, PATCH_CHECK MB-5 / R5): "Has 8 to 12 Physical damage, +3 to +4 per Boss's Face Broken" (its Fire
# variant "9 to 14 Fire damage, +3 to +5"). A broken face is a campaign count, not a per-fight rate (KeywordPopups 868:
# "Each "Boss Encounter" icon on the World Screen is a face which can be broken by beating the encounter"; the player
# stat number_of_broken_faces). bridge.resetConfig sets PoB's configBossFaceBroken to 56 (BOSS_FACES_BROKEN) unless the
# loaded code has its own count; this says which count was used and what poe2db's 60 would add.
FACEBREAKER_LINE = re.compile(r"Has (\d+) to (\d+) (\w+) damage, \+(\d+) to \+(\d+) per Boss's Face Broken", re.I)
FACES_SOURCES = 60  # (poe2db's Community Wiki note: "There are 60 world boss"; the other sources' "176-236" is 56)


def facebreaker_notes(texts: list[str], faces: float | None) -> list[dict]:
    """A labelled note per Facebreaker line among the equipped items' texts: the broken-face count the score used
    (faces: PoB's configBossFaceBroken) and, as a row, the item's damage at 60 faces."""
    notes = []
    for text in texts:
        for m in FACEBREAKER_LINE.finditer(text):
            low, high, kind, per_low, per_high = int(m[1]), int(m[2]), m[3].capitalize(), int(m[4]), int(m[5])
            n = faces or 0
            at = (low + per_low * n, high + per_high * n)
            more = FACES_SOURCES - n
            row = (f"with {FACES_SOURCES} faces: {low + per_low * FACES_SOURCES}-{high + per_high * FACES_SOURCES} "
                   f"(+{per_low * more}-{per_high * more} base damage, "
                   f"{(per_low + per_high) * more / max(1, sum(at)):+.1%} of the item's damage)" if more > 0 else "")
            notes.append({
                "label": f"Facebreaker: {n:g} broken boss faces assumed*", "value": n,
                "basis": f"the item's {kind} damage {at[0]}-{at[1]} at {n:g} faces ('+{per_low} to +{per_high} per "
                         "Boss's Face Broken'). A face is a World Screen boss encounter beaten in the campaign (the game's "
                         "own definition), a count the character keeps, not a per-fight number. "
                         + ("56 is what a finished campaign + Interludes gives by the lower of the sources' counts (56, "
                            "60); the game files don't list them: MEDIUM that the count is 56-60, LOW for 56 exactly. " if n == 56 else
                            "The loaded build's own count. " if n else
                            "0 faces: a character that hasn't beaten a campaign boss. ")
                         + (row + ". " if row else "")
                         + "In game: the World Screen's face tracker shows the real count",
                "confidence": "theory" if n == 56 else "reported"})
    return notes


# Mana spent Recently (COV32, PATCH_CHECK MB-4): Indigon's "(5-10)% increased Cost of Skills for each 200 total Mana Spent
# Recently" and "(10-15)% increased Spell damage for each 200 total Mana you have Spent Recently" read PoB's
# Multiplier:ManaSpentRecently, which only its config box (ConfigOptions 1274, no default) sets: 0 before, an UNDERCOUNT
# of the damage (and of the cost). Recently = the past 4 s (KeywordPopups Recently). Must equal bridge DERIVED.manaSpent.
MANA_SPENT_SAMPLES = 20
RECENTLY = 4.0


def mana_spent_stacks(spend: float, regen: float, cost: float, per: float = 200.0) -> tuple[float, str]:
    """(average whole `per`s of Mana spent in the past 4 s, how): 4 s x the main skill's Mana a second (PoB's
    ManaPerSecondCost), no more than its regeneration pays (Indigon's "Mana Recovery other than Regeneration cannot
    Recover Mana"; a pool that runs dry spends only what comes back); the 4 s sum moves by one use's cost, so the whole
    `per`s are averaged over that spread (PoB counts whole ones)."""
    spent = RECENTLY * min(spend, regen)
    if per <= 0 or spent <= 0:
        return 0.0, ("the main skill spends no Mana" if spend <= 0 else "no Mana regeneration to pay for it") + ": 0"
    stacks = sum(math.floor(max(0.0, spent + cost * ((i + 0.5) / MANA_SPENT_SAMPLES - 0.5)) / per)
                 for i in range(MANA_SPENT_SAMPLES)) / MANA_SPENT_SAMPLES
    capped = " (capped by Mana regeneration)" if regen < spend else ""
    return stacks, (f"{spent:.0f} Mana spent in 4 s ({min(spend, regen):.1f} a second{capped}; {cost:.0f} a use): "
                    f"{stacks:.2f} x {per:g} on average")


def demon_form_state(build, skills: list[dict], main: str | None) -> dict | None:
    """(MECHC28) Demon Form's steady cycle for `build` (None without the skill): {share, stacks (average while in the
    form), pob (PoB's own Demonflame), how, basis}. recovery.demon_form_cycle from the build: its Life, its Life recovery
    less the boss's landed hits, costs and own steady loss (recovery.life_flows; PoB's LifeRegenRecovery has the form's
    own loss at PoB's current stacks netted in, added back here), the gem's Life loss per Demonflame (its level stat), the
    maximum (10, none with "Demonflame has no maximum"), its cooldown (useRates, after Cooldown Recovery), and the
    non-Spell skills the player presses (each reverts you: a stint fits between two presses, the cooldown taken in the
    gap). A main skill that isn't a Spell reverts you at once: never in the form."""
    present = {s["name"]: s for s in skills if not s.get("disabled")}
    if DEMON_FORM not in present:
        return None
    raw = build.call("activeSkills", levelStats=[DEMON_FORM])["skills"]
    demon = next((s for s in raw if s["name"] == DEMON_FORM and not s.get("disabled")), {})
    per_stack = number((demon.get("levelStats") or {}).get(DEMON_LOSS_STAT)) / 60
    config = build.call("getConfig")
    inputs, placeholder = config.get("input") or {}, config.get("placeholder") or {}
    pob = number(inputs.get("demonFormStacks")) or number(placeholder.get("demonFormStacks")) or float(DEMON_MAX)
    texts = [line["text"] for line in build.call("sourceLines")["lines"]]
    cap = None if any(DEMON_NO_MAX.search(t) for t in texts) else float(DEMON_MAX)
    pob = min(pob, cap) if cap is not None else pob
    now = (pob + (getattr(build, "demon_offset", 0.0) or 0.0)) if inputs.get("inDemonForm") else 0.0  # (switched off: PoB drops the key)
    from builder.search import objective

    fields = sorted(set(objective.RECOVERY_INPUTS) | set(conditions_mod.FULL_LIFE_FIELDS))
    added = getattr(build, "added_stats", None)
    stats = {**build.call("stats", fields=fields), **(added() if callable(added) else {})}
    back, hit_life, cost, steady, parts = recovery_mod.life_flows(stats)
    net = back + per_stack * now - hit_life - cost - steady
    rows = build.call("useRates", indices=[demon["group"]])["rates"] if demon.get("group") is not None else []
    cooldown = number((rows[0] if rows else {}).get("cooldown")) or DEMON_COOLDOWN
    main_entry = next((s for s in skills if s["name"] == main and not s.get("disabled")), None)
    limit, interrupts = math.inf, ""
    if main_entry is not None and not main_entry.get("spell"):
        limit, interrupts = 0.0, f"your main skill ({main}) isn't a Spell: using it reverts you"
    else:
        presses_ns, named = non_spell_presses(build, skills, main)
        if presses_ns > 0:
            limit = max(1 / presses_ns - cooldown, 0.0)
            interrupts = (f"non-Spell skills pressed {presses_ns:.3f} times a second ({named}): each reverts you, so "
                          f"a stint fits between two presses less the {cooldown:g} s cooldown")
    if limit <= 0:
        share, stacks, stint, how = 0.0, 0.0, 0.0, interrupts
    else:
        share, stacks, stint, how = recovery_mod.demon_form_cycle(
            number(stats.get("Life")), net, per_stack, cap, cooldown, limit)
        if interrupts and stint < limit:
            how += f" ({interrupts}, which a stint this short fits)"
        elif interrupts:
            how = f"{interrupts}; {how}"
    basis = (f"{per_stack:,.1f} Life lost a second per Demonflame (the gem's level), "
             f"{'no maximum' if cap is None else f'at most {cap:g}'}; {'; '.join(parts)}; "
             f"Life recovery {back + per_stack * now:,.1f}/s before the form: {net:,.1f}/s out of the form. {how}")
    return {"share": share, "stacks": stacks, "pob": pob, "how": how, "basis": basis}


def non_spell_presses(build, skills: list[dict], main: str | None) -> tuple[float, str]:
    """(presses a second, named) of the non-Spell skills the player uses beside the main skill (MECHC28, Demon Form:
    "use a Skill that isn't a Spell" reverts you): enabled, not triggered, not Persistent / Meta (nor in a group holding
    a triggered skill), not a support, each at its group's rate (conditions.skill_use_rate: cooldown, duration, else the
    search's chosen rate, else 0)."""
    ctx = getattr(build, "ctx", None)
    groups: dict[int, list[str]] = {}
    # (a group holding a triggered skill is a meta group, Spellslinger, Cast on X: it fires on its own, and an
    # item-granted meta isn't in the gem table, so its types can't be read there)
    metas = {s.get("group") for s in skills if s.get("triggered") and not s.get("disabled")}
    for s in skills:
        if (s.get("disabled") or s["name"] in (main, DEMON_FORM) or s.get("spell") or s.get("triggered")
                or s.get("group") in metas):
            continue
        # (gem_of: by granted effect id, then any gem's name, so an item-granted meta such as a Dueling Wand's
        # Spellslinger is typed too)
        types = set(gem_of(ctx, s["name"], s.get("skillId")).get("skillTypes") or [])
        if types & NOT_PRESSED_TYPES or s.get("group") is None:
            continue
        groups.setdefault(s["group"], []).append(s["name"])
    if not groups:
        return 0.0, ""
    choices = models.press_choices(build)
    total, named = 0.0, []
    for row in build.call("useRates", indices=sorted(groups))["rates"]:
        each, why, _ = conditions_mod.skill_use_rate(row, choices)
        if each > 0:
            total += each
            named.append(f"{', '.join(groups.get(row.get('index'), []))} {each:.3f}/s, {why}")
    return total, "; ".join(named)
