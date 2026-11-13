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
from builder.search import conditions as conditions_mod, models, passive_models
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
REMNANT_SOURCES = {
    "Into the Breach": (0.82645, "Acolyte of Chayula: Flames of Chayula in your Breach"),
    "Wildwood's Gifts": (0.82645, "The Hollow Mask: Ancient Blooms"),
    "Sanguine Revelry": (0.0, "once every 2 s while hitting a Bleeding target (hit_remnant_rate)"),
    "Mana Remnants": (0.0, "once every 2 s while critting an Elementally Ailmented target (hit_remnant_rate)"),
    "Life Remnants": (0.0, "once every 3 s on hit (hit_remnant_rate)"),
    "Snap": (0.25, "per cast, before cooldown recovery"),
    "Frost Bomb": (0.1667, "per cast, before cooldown recovery"),
    "Lingering Illusion": (0.352, "the boss destroys one of your dodge-roll copies every 5 s (the user's rate, 9 Nov), "
                                  "1.76 Power Charge Remnants each"),
    "Living Bomb": (0.0, "one seed at a time, exploding once the boss takes 200% of its Ailment Threshold "
                    "(living_bomb_rate, from the build's own damage)"),
    "Grim Feast": (0.0, "a Grim Remnant when one of your Reviving minions dies in your Presence (100%, game files), at "
                        "the rate the build kills them (models.minion_deaths; no flat rate, the user 11 Nov)"),
    "Orb of Storms": (0.0833, "one Orb kept up: a Remnant when each 12 s Orb expires"),
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
# skill's CombinedDPS (other skills' damage not counted: an undercount) against PoB's EnemyAilmentThreshold. Whether a
# seed explodes when its 15 s run out first is UNVERIFIED (the game text names damage or a kill only); counted as
# exploding then, labelled, rather than zeroed.
LIVING_BOMB, LIVING_BOMB_SHARE, LIVING_BOMB_DURATION = "Living Bomb", 2.0, 15.0
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
    late = wait > LIVING_BOMB_DURATION
    period = cast_time + min(wait, LIVING_BOMB_DURATION)
    how = (f"a seed planted ({cast_time:g} s cast) explodes after {need:,.0f} damage (200% of the boss's Ailment "
           f"Threshold) at {dps:,.0f} DPS, {wait:.1f} s: one per {period:.1f} s")
    if late:
        how += (f" (the {LIVING_BOMB_DURATION:g} s seed runs out first; counted as exploding then, UNVERIFIED: the game "
                "text names damage or a kill only)")
    return 1 / period, how
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
KEEP_INFUSION = re.compile(r"^(\d+(?:\.\d+)?)% chance to not remove an Infusion but still count as consuming", re.I)
VERISIUM_EVERY = re.compile(r"Every (?:(\d+(?:\.\d+)?) )?seconds?, gain an? Verisium Infusion", re.I)
GRANTED_SKILL = re.compile(r"Grants Skill: (?:Level \S+ )?(.+?)\s*$")  # (context.granted_by's pattern)
INFUSED_ELEMENT = re.compile(r"\b(fire|cold|lightning)-infused\b", re.I)


def infusion_supply(present: dict[str, dict], lines: list[str], ailments: set[str] | dict[str, float],
                    siphon: tuple[float, str] = (0.0, ""), bomb: tuple[float, str] = (0.0, "")) -> tuple[dict[str, float], list[str]]:
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
        rate = REMNANT_SOURCES[name][0] * ((skill.get("cooldownRecovery") or 1.0) if name in COOLDOWN_SOURCES else 1.0)
        if name == LIVING_BOMB:  # (living_bomb_rate, from the caller)
            rate = bomb[0]
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
    if SIPHON in present:
        made["Cold"] = made.get("Cold", 0.0) + siphon[0]
        used.append(f"{SIPHON} {siphon[0]:.2f}/s Cold ({siphon[1]})")
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


def pressed_group(group: dict, gems: dict[str, dict]) -> bool:
    """A non-main group in Full DPS whose damage comes from a skill the player presses now and then: not triggered by a
    meta skill (Cast on X), not a totem or an Invocation (they use their skills on their own), not a Mark carrier
    (Pounce: the Mark model rates it), not granted by an item."""
    if not group["enabled"] or not group["fullDPS"] or group["fromItem"]:
        return False
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
        from builder.search import bosses  # (MECH-D: the request's boss, its add kills / Culls per second, for the models)
        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        stats = {**stats, **bosses.kill_stats(boss)}
        build.minion_lives = minion_lives(build, found)  # (MECHB12, A25-2: Minion Pact's Kills read the minions' Life)
        # (MECHB12, A25-41: each curse's own duration, PoB's with the build's modifiers, for curse_uses)
        build.curse_durations = {s["name"]: s["curseDuration"] for s in skills if s.get("curseDuration")}
        stats = {**stats, "_minion_lives": build.minion_lives, "_curse_durations": build.curse_durations}
        if any(s.name == models.SACRIFICE for s in found):  # (MECH-B, MECHB19, A27-4: Sacrifice's corpses, the build's own
            # undead Reviving minion deaths, for models.corpse_supply's readers)
            stats = {**stats, models.SACRIFICE_CORPSES_STAT: models.sacrifice_corpses(found, main, stats)}
        if any(s.name == "Cast on Charm Use" for s in found):  # (its Energy: charm charges spent, from the flask model)
            stats = {**stats, **charm_stats(build)}
        if any(s.name == "Cast on Minion Death" for s in found):  # (its Energy: the build's minions dying)
            stats = {**stats, "_minion_deaths": models.minion_deaths(found, main, stats)}
        source_lines = build.call("sourceLines")["lines"]  # (one call: the models below all read the build's lines)
        if any(s.name == "Cast on Dodge" for s in found):  # (its Energy: the build's own roll distance; MECH-B, MECHB3)
            stats = {**stats, "_dodge_roll": models.dodge_roll([e["text"] for e in source_lines], stats)}
        forbidden = set(build.ctx.request.forbid_ledger_entries)
        inputs = models.read_inputs(source_lines)
        mods, notes = models.archon_model(inputs, stats, main, main_gems)
        supports = {g["index"]: [x["name"] for x in g["gems"] if x["support"] and x.get("enabled", True)]
                    for g in build.groups()}
        energy_mods, energy_notes, energy_groups = models.energy_model(inputs, stats, found, main, supports)
        # (the meta gems' trigger rates, for the curses and Marks they trigger: MECH46, FLAT_RATES A5)
        build.trigger_rates = {m["skill"]: m["value"] for m in energy_mods
                               if m.get("kind") == "SkillData" and m.get("key") == "triggerRate"}
        expression_mods, expression_notes, expression_groups = models.expression_model(found, stats, main)
        levels = {g["name"]: g["level"] for grp in build.groups() for g in grp["gems"] if g.get("level")}
        totem_mods, totem_notes = models.totem_model(found, stats, [e["text"] for e in source_lines], levels, main)
        energy_mods, energy_notes = energy_mods + totem_mods, energy_notes + totem_notes
        count_mods, count_notes = models.totem_count_model(found, main)  # (MECH-B, MECHB8: other totems' count)
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
                                                      self.mods, build.call("getConfig")["input"], side_groups)
        harmony_mods, harmony_notes = models.harmony_model(inputs, stats)
        ring_mods, ring_notes = models.resist_rings_model(source_lines) if models.RESIST_ENTRY not in forbidden else ([], [])
        main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or []) if main is not None else set()
        hand_mods, hand_notes = models.guided_hand_model(inputs, source_lines, stats, main, slam="Slam" in main_types)
        texts = [e["text"] for e in source_lines]
        if any(models.KAOMS_BLESSING.match(t.strip()) or models.SHAPESHIFT_EVERY_SECOND_SLAM.search(t)
               or models.EMPOWERMENT_EVERY_SECOND_SLAM.match(t.strip()) for t in texts):
            # (MECH-B, MECHB18: Ancestral Boost's sources PoB leaves unparsed; PoB's own boost uptimes beside the stats;
            # MECHB19: Ancestral Empowerment + Fist of War at the lower reading)
            boost_mods, boost_notes = models.ancestral_boost_model(
                texts, {**stats, **build.call("stats", fields=models.ANCESTRAL_FIELDS)}, found, main, main_types,
                self.warcry_uses() if any(models.KAOMS_BLESSING.match(t.strip()) for t in texts) else (0.0, ""))
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
        doom_mods, doom_notes, doom_groups = models.doom_blast_model(found, curses)  # (MECHB12: Impending Doom's expiries)
        hand_mods, hand_notes = hand_mods + doom_mods, hand_notes + doom_notes
        build.minion_died = models.minion_died_rate(found, main, stats, supports, toads, build.trigger_rates)
        # (MECHB9: the build's kills, enemies and own minions Killed (the ruling on A17-4), for on-Kill readers:
        # build.kills = (enemies/s, all/s, how); Heartbound Loop's damage taken at the minion-death rate: build.self_damage)
        build.kills = models.kill_rate(stats, *models.minion_deaths(found, main, stats))
        build.self_damage, heart_notes = models.heartbound_damage([e["text"] for e in source_lines], build.minion_died)
        if build.self_damage > 0:  # (MECHC9: in PoB as Physical damage over time, so recovery and the Life flask see it)
            hand_mods = hand_mods + [models.line("1 Physical Damage taken per second", build.self_damage)]
        hand_notes = hand_notes + models.kills_note(build.kills) + heart_notes
        jiquani_mods, jiquani_notes = models.jiquani_model([e["text"] for e in source_lines])  # (MECHC9, A12-2)
        hand_mods, hand_notes = hand_mods + jiquani_mods, hand_notes + jiquani_notes
        unbound_mods, unbound_notes = models.unbound_model([e["text"] for e in source_lines])  # (MECHC10)
        hand_mods, hand_notes = hand_mods + unbound_mods, hand_notes + unbound_notes
        main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or []) if name else set()
        echo_mods, echo_notes = models.repeat_echo_model([e["text"] for e in source_lines], main_types, forbidden, main)
        hand_mods, hand_notes = hand_mods + echo_mods, hand_notes + echo_notes
        parry_mods, parry_notes = models.parry_model(found, stats, main, bool(({name} | main_gems) & models.PARRY_CONSUMERS))
        hand_mods, hand_notes = hand_mods + parry_mods, hand_notes + parry_notes
        roar_mods, roar_notes = models.roar_model(found, {**stats, "_texts": [e["text"] for e in source_lines]}, main)
        hand_mods, hand_notes = hand_mods + roar_mods, hand_notes + roar_notes
        rod_mods, rod_notes = models.rod_model(found, stats, main)
        hand_mods, hand_notes = hand_mods + rod_mods, hand_notes + rod_notes
        texts = [e["text"] for e in source_lines]
        passive_mods, passive_notes = passive_models.build_models(build, inputs, texts, found, main, supports)  # (COV-B)
        hand_mods, hand_notes = hand_mods + passive_mods, hand_notes + passive_notes
        if any(models.RUN_AND_GUN.search(t.strip()) for t in texts):  # (Run and Gun: shotgun and sequence-fired skills)
            main_types = set((build.ctx.gems.get(name) or {}).get("skillTypes") or [])
            standing = bool(build.call("getConfig")["input"].get("conditionStationary"))
            gun_mods, gun_notes = models.run_and_gun_model(texts, stats, main, main_types, standing)
            hand_mods, hand_notes = hand_mods + gun_mods, hand_notes + gun_notes
        early_mods, early_notes = models.hazard_early_model(found, stats, forbidden)  # (MECH-B: hazards set off early)
        hand_mods, hand_notes = hand_mods + early_mods, hand_notes + early_notes
        hand_notes = hand_notes + models.channel_windup_note(main)  # (MECH-B: a channel's wind-up, labelled, not counted)
        hand_notes = hand_notes + models.orb_of_storms_note(main, stats)  # (MECH-B: Orb of Storms' recast bolt, labelled)
        hand_notes = hand_notes + models.coursing_current_note(found)  # (MECH-B: 0 on a lone boss, as PoB scores it)
        danse_mods, danse_notes = models.danse_macabre_model(found, supports)  # (MECH-B: Danse Macabre's second skeleton)
        hand_mods, hand_notes = hand_mods + danse_mods, hand_notes + danse_notes
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
        if main is not None:  # (returning projectiles' second boss hit: MECH-B, models.return_model)
            gem = build.ctx.gems.get(name) or {}
            back_mods, back_notes = models.return_model(
                texts, stats, main, set(gem.get("skillTypes") or []), group["gems"] if group else [],
                set(gem.get("weaponTypes") or []))
            hand_mods, hand_notes = hand_mods + back_mods, hand_notes + back_notes
            # (MECH-B: Aftershock chances summed and capped, and their ailment applications; models.aftershock_model)
            shock_mods, shock_notes = models.aftershock_model(
                texts, stats, main, set(gem.get("skillTypes") or []), group["gems"] if group else [],
                set(gem.get("weaponTypes") or []), models.cast_by_totem(found, main), forbidden)
            hand_mods, hand_notes = hand_mods + shock_mods, hand_notes + shock_notes
        if main is not None and group and main.name in models.MULTI_SET_SKILLS:  # (MECH-B: Rolling Slam's two slams)
            sets = build.call("setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"]
            slam_mods, slam_notes = models.multi_set_model(main.name, main.stat_set, sets)
            hand_mods, hand_notes = hand_mods + slam_mods, hand_notes + slam_notes
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
        build.objective.added_dps = pulse  # separately computed damage (Melting Maelstrom's pulses); what-ifs get their own
        hand_mods += models.maelstrom_inputs(source_lines, pulse) if pulse_notes else []
        # A model whose effect follows the build's own numbers and that the bridge can't work out again per calculation
        # would make every item what-if a real trial (Build.item_deltas). All of today's are worked out in the bridge
        # (models.derived): uptimes from Mana and regeneration, Life spent, attack speed, Mana > Life, Maelstrom's pulses.
        # (BUILDER_RESCORE_MODELS=1 puts the trials back, to measure what they cost: benchmarks/rescore_cost.py)
        build.models_on_stats = bool(stat_notes or harmony_notes or hand_notes or pulse_notes
                                     or totem_notes) and RESCORE_MODELS
        mods += energy_mods + expression_mods + ward_mods + stat_mods + harmony_mods + ring_mods + hand_mods
        notes += energy_notes + expression_notes + ward_notes + stat_notes + harmony_notes + ring_notes + hand_notes + pulse_notes
        mods, corpse_notes = models.corpse_model(found, mods, stats, main)  # last: it overrides the trigger rates above (MECHB11: the adds' corpses)
        notes += corpse_notes + models.spirit_vessel_notes(skills)
        notes += models.granted_level_notes([(o.name, o.raw, o.granted_basis) for o in build.equipped.values() if o is not None and o.unique])
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
        notes += self.rate_presses()
        vessel_groups = models.spirit_vessel_groups(skills)  # (its companion's damage is in Full DPS only)
        return mods, notes, {**{g: None for g in energy_groups | mirage_groups | vessel_groups | toad_groups | doom_groups},
                             **expression_groups}

    def rate_presses(self) -> list[dict]:
        """Pressed non-main skills in Full DPS count at their real use, the main skill at the time they leave it
        (models.press_model; objective.Objective.press_scales). Returns the notes."""
        build = self.build
        groups = build.call("groups")
        main = groups["mainSocketGroup"]
        pressed = {g["index"] for g in groups["groups"] if g["index"] != main and pressed_group(g, build.ctx.gems)}
        # (MECH62: the Cull models' scales, reset beside press_scales and set again by cull_window)
        build.objective.whole_scales, build.objective.cull_scale = {}, 1.0
        if not pressed:
            build.objective.press_scales = {}
            return self.consume_ignite(groups, []) + self.cull_window()
        rates = build.call("useRates", indices=sorted(pressed | {main}))["rates"]
        paced = {g["index"]: models.CADENCE_PACE for g in groups["groups"] if g["index"] in pressed
                 and any(x["enabled"] and x["name"] in models.CADENCE_SUPPORTS for x in g["gems"])}
        caps = self.press_caps(groups["groups"], pressed)
        build.objective.press_scales, notes = models.press_model(rates, main, pressed, paced, caps,
                                                                 models.press_choices(build))
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
        if entry is None and not on_block:
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
            heavy = models.heavy_stun_rate(build.call("stats", fields=models.MODEL_FIELDS))[0] if split.get("heavyOnly") else 0.0
            scale, how, confidence = conditions_mod.cull_window_scale(
                mult, number(stats.get("CullMultiplier")), conditions_mod.boss_pool(boss), base,
                number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0,
                number(stats.get("TotalDPS")), number(stats.get("TotalDotDPS")), split, heavy)
            hit_gain = (mult - 1.0) * scale
            if scale < 1.0 or how:
                build.objective.cull_scale = scale
                notes.append({"label": f"Culling Strike against {boss}: the time a Cull saves*", "value": f"x{scale:.3f} of PoB's",
                              "basis": how, "confidence": confidence})
        if on_block:
            notes += self.cull_on_block(boss, {**stats, "_boss": boss}, base, hit_gain, split, entry is not None)
        return notes

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
        share, how = conditions_mod.block_cull_saving(threshold, conditions_mod.boss_pool(boss), base, blocks)
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
        entries = [e for e in (build.stats or {}).get("SkillDPS") or [] if e.get("name") == "Best Ignite DPS"]
        if main is None or group is None or not entries:
            return []
        gems = {g["name"] for g in group["gems"] if g.get("enabled", True)}
        consumer = conditions_mod.main_consumes(build, main, gems).get("Ignite")
        side = None if consumer is not None else conditions_mod.side_consumes(build, main, groups).get("Ignite")
        if consumer is None and side is None:
            return []
        stats = build.call("stats", fields=list(conditions_mod.CONSUME_IGNITE_FIELDS))
        hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
        chance = min(number(stats.get("IgniteChancePerHit")) / 100.0, 1.0)
        notes = []
        for entry in entries:
            source = entry.get("source") or ""
            own = source == main
            rate = next((r for r in rates if r.get("name") == source), None)
            potential = (number(stats.get("IgniteStackPotential")) if own
                         else number(((rate or {}).get("stackPotential") or {}).get("Ignite")))
            if side is not None:  # (MECH53: a skill beside the main one consumes it)
                scale, how = conditions_mod.side_consumed_ignite_scale(potential, hits, chance, own, side[0])
                by, why, confidence = side[1], f"{side[1]} consumes Ignite (KeywordPopups Consume: then immune 1 s)", side[2]
            else:
                scale, how = conditions_mod.consumed_ignite_scale(potential, hits, chance,
                                                                  number(stats.get("IgniteDuration")), own)
                why = f"{main} consumes Ignite on hit (KeywordPopups Consume: then immune 1 s)"
                by, confidence = consumer, "MEDIUM"
            if scale >= 1.0:
                continue
            key = models.press_key("Best Ignite DPS", source or None)
            build.objective.press_scales[key] = build.objective.press_scales.get(key, 1.0) * scale
            notes.append({"label": f"Ignite from {source or 'the build'}: consumed by {by}*", "value": f"x{scale:.3f}",
                          "basis": f"{why}; {how}", "confidence": confidence})
        return notes

    def press_caps(self, groups: list[dict], indices: set[int]) -> dict[int, "models.PressCap"]:
        """The press hook (MECHB5; COVC6 / COVC7, usage.py's rates): for each group in `indices` whose skill or support
        needs Combo, walking between uses, or Mana Flask charges for a throw, the most uses a second the build can feed
        it (models.PressCap). Combo: usage.combo_rate (N builder Strikes, then a use: 0 with no builder in the build);
        walking: usage.travel_rate (10 m at the build's movement speed, LOW; once it's in usage.py); a Concoction:
        flasks.charge_use_rate (the Mana Flask's charge generation / 5 a throw). Groups needing none are left out."""
        from builder.search.flasks import THROW_CHARGES
        from builder.search.usage import Usage, requirements

        caps: dict[int, models.PressCap] = {}
        usage, rules = None, usage_rules(self.build)
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
            elif name == SIPHON:
                rate, condition = siphon_freezes(self.build.call("stats", fields=models.IMMOBILISE_FIELDS))
            elif name == LIVING_BOMB:
                rate, condition = living_bomb_rate(self.build.call("stats", fields=list(LIVING_BOMB_FIELDS)),
                                                   skill.get("castTime") or 0.0)
            elif name in HIT_REMNANTS:
                rate, condition = hit_remnant_rate(name, self.build.call("stats", fields=list(HIT_REMNANT_FIELDS)))
            elif name == "Snap":  # (MECH46: it needs an ailment on the boss to Consume)
                found, how = snap_found(self.build.call("stats", fields=list(HIT_REMNANT_FIELDS)))
                rate *= found
                condition = f"on its cooldown, when it finds an ailment to Consume ({found:.0%}: {how})"
            supply += rate
            used.append(f"{name} {rate:.2f}/s ({condition})")
        return supply, used

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

    def wanted(self) -> tuple[list[dict], list[dict]]:
        skills = self.build.call("activeSkills", levelStats=[
            models.IRON_WARD, models.FEROCIOUS_ROAR, models.SUMMON_WOLF, models.RAPID_SHOT, models.APOCALYPSE,
            KALGUUR, *models.gem_stat_skills()],
            supportStats=[*models.gem_stat_support_stats(), models.TOTEM_ONE_STAT])["skills"]  # (MECH-B: A23-3)
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
        self.ledger_shares = {}  # (COV11; set again by ledger_conditions)
        found, condition_notes = self.ledger_conditions(skills)
        # (SEARCH10, the manager's ruling: a part-time ledger condition counts at its share, in PoB's stats; the search's
        # part-time weighing switches it off through ledger_switch, so the defence floors read it at 0: Search._part_time)
        for name, share in sorted(getattr(self, "ledger_part_time", {}).items()):
            if getattr(self, "ledger_switch", {}).get(name, True) and share > 0:
                found.append(name)
                self.ledger_shares[name] = round(100 * share)
        mods += [{"kind": "Condition", "name": name} for name in found]
        mods += [models.raw_mod(f"Multiplier:LedgerShare{name}", "BASE", percent)
                 for name, percent in sorted(self.ledger_shares.items())]
        mods, ward_notes = self.ward_paid(skills, mods, main)  # (MECH-B: Runic Ward pays for Ward-cost skills, MECHB13)
        notes += ward_notes
        notes += self.life_mana_income(skills, main)  # (MECH-C: Life / Mana from kills, Remnants, charms, events; MECHC12)
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
            out["flask_life"] = flasks.recovery_per_second(info, gain, "Life") if info else 0.0
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
                + number(extra.get(flasks.RECOVERY_FIELDS["Life"]))
            out.update(attacks=attacks, life=number(stats.get("Life")), life_recovery=recovery)
        return out

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
            "Life", "Mana", "LifeOnKill", "ManaOnKill", "IgniteChancePerHit", "ShockChancePerHit", *models.MODEL_FIELDS,
            *conditions_mod.EVIDENCE_FIELDS, *CHILL_FIELDS]))), **bosses.kill_stats(boss)}
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
                # (the share of the fight the boss carries at least one: the largest one alone, a lower bound: LOW)
                for word in ("chilled", "shocked", "ignited", "frozen", "electrocuted"):
                    try:
                        each = conditions_mod.target_ailment_share(stats, word)
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
            recovery.hit_income(income, stats, texts, **self.hit_rates(main, stats))
        recovery.restrict(income, texts)
        income.unread = recovery.unread(texts)
        build.recovery_sources = dict(income.rates)
        return recovery.note(income)

    def hit_rates(self, main: str | None, stats: dict) -> dict:
        """The rates recovery.hit_income reads (MECHC14, A27-32), from the build and the request's boss: the boss's hits
        that land a second (at the "when hit" benefit pace, bosses.benefit_skill_time) and the Life each takes
        (objective.boss_taken, at the defence pace: after mitigation and the per-type weights; Life's
        share after Mind over Matter, and of Life + Energy Shield in proportion, LOW), the boss's hits Blocked a second (at
        the "when hit" benefit pace, as objective.on_block) and the main skill's casts a second when it is a Spell (other
        Spells' casts aren't counted: LOW, a lower bound)."""
        from builder.search import boss_hits, objective, recovery

        build = self.build
        hit = {**stats, **build.call("stats", fields=list(dict.fromkeys(
            [*objective.RECOVERY_INPUTS, "Armour", "EnergyShield", "Life", "Speed", "StunThreshold",
             "StunAvoidChance"])))}
        taken, landed, deflect = objective.boss_taken(hit)
        life, es = number(hit.get("Life")), number(hit.get("EnergyShield"))
        mom = min(max(number(hit.get("sharedMindOverMatter")), 0.0), 100) / 100
        share = (1 - mom) * (life / (life + es) if life + es > 0 else 0.0)
        per_hit = sum(taken.values()) / landed * share if landed > 0 else 0.0
        every = objective.benefit_every(hit)
        blocked = boss_hits.block_share(hit) / every
        landed = boss_hits.land_share(hit) / every  # (a "when hit" benefit: the benefit pace, the hit's size as scored)
        types = set((build.ctx.gems.get(main) or {}).get("skillTypes") or []) if main else set()
        casts = number(hit.get("Speed")) if "Spell" in types else 0.0
        how = (f"{main}'s casts; other Spells' casts not counted (LOW)" if casts > 0
               else "the main skill isn't a Spell (other Spells' casts not counted: LOW)")
        # (MECHC15) the boss's hits that Light Stun you, each type at its full hit size (PoB's {Type}TakenHit, after
        # mitigation and Deflection's expected share) and its share of the boss's hits (boss_hits.type_shares)
        sizes = {t: max(number(hit.get(f"{t}TakenHit")), 0.0) * deflect for t in objective.RECOVERY_DAMAGE_TYPES}
        stunned, stunned_how = recovery.stuns_taken(hit, sizes, boss_hits.type_shares(boss_hits.boss_name(hit)), landed)
        return {"landed": landed, "life_per_hit": per_hit, "blocked": blocked, "casts": casts, "cast_how": how,
                "stunned": stunned, "stunned_how": stunned_how}

    def spent_rates(self, skills: list[dict], main: str | None, stats: dict, lowered: list[str]) -> dict:
        """(events a second, how) for recovery.spent_income, each from the build's own sources (MECHC13; CLAUDE.md: no
        flat rates; none, 0 and said): Endurance / Power Charges its consumers spend (build.charge_flow, Conditions'
        charge model), Glory its Glory skills spend (models.glory_spent), uses of its Combo skills that build 10+ Combo
        (usage.combo_rate's rotation), corpses its corpse skills consume (at most models.corpse_supply: the boss
        preset's adds) and its Offering skills' uses (conditions.skill_use_rate)."""
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
            supply = models.corpse_supply(set(present), stats)
            out["corpses"] = (min(uses, supply), (f"corpse skills {'; '.join(parts) or 'none'}, one corpse a use, at "
                                                  f"most the corpses there are: {models.add_corpses_note(stats)}"))
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
        pressed = {g["index"] for g in build.groups() if g["index"] != main_index and pressed_group(g, build.ctx.gems)}
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
                rate *= snap_found(build.call("stats", fields=list(HIT_REMNANT_FIELDS)))[0]
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

    def infusion_share(self, element: str, ailments: set[str] | dict[str, float]) -> tuple[float, str]:
        """The share of the main skill's casts that find an Elemental Infusion of `element` (one consumed per cast):
        the build's supply (infusion_supply) over its casts per second, less those that keep the Infusion."""
        build = self.build
        raw = build.call("activeSkills")["skills"]
        present = {s["name"]: s for s in raw if not s["disabled"]}
        lines = [line["text"] for line in build.call("sourceLines")["lines"]]
        siphon = siphon_freezes(build.call("stats", fields=models.IMMOBILISE_FIELDS)) if SIPHON in present else (0.0, "")
        bomb = living_bomb_rate(build.call("stats", fields=list(LIVING_BOMB_FIELDS)),
                                present[LIVING_BOMB].get("castTime") or 0.0) if LIVING_BOMB in present else (0.0, "")
        supply, used = infusion_supply(present, lines, ailments, siphon, bomb)
        keep = min(1.0, sum(float(m.group(1)) for t in lines if (m := KEEP_INFUSION.search(t.strip()))) / 100.0)
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
        wanted = conditions()
        if not wanted:
            return [], []
        actives = [s["name"] for s in skills if not s["disabled"]]
        names = actives + [gem["name"] for group in self.build.groups() for gem in group["gems"]
                           if gem.get("enabled", True)]
        texts = [(self.build.ctx.all_gems.get(name) or {}).get("description") or "" for name in actives]
        main = self.main_skill()
        main_types = set((self.build.ctx.all_gems.get(main) or {}).get("skillTypes") or []) if main else set()
        main_group = self.build.main_group()
        main_info = next((s for s in skills if s["name"] == main and main_group and s.get("group") == main_group["index"]),
                         next((s for s in skills if s["name"] == main), {}))
        found, notes = [], []
        glory = None  # (models.glory_uses' answer, once for every glory_window condition)
        if not hasattr(self, "ledger_shares"):  # (set by wanted(); a unit test may call this alone)
            self.ledger_shares = {}
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
                notes.append({"label": f"{condition['title']}", "value": f"{share:.0%} up", "basis": f"{how}; {counted}",
                              "confidence": "LOW (play strategy: Runic Reprieve channelled into the boss's hits; the "
                                            "Ward each blocked hit takes is the hit's damage after mitigation, UNVERIFIED)"})
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
        skill can't be used that fast. Not charged here: the main skill's time spent pressing the Glory skill (routed)."""
        glory_names = {n for n in names if n in models.GLORY_SKILLS or n in models.GLORY_FROM_LIFE}
        speeds: dict[str, float] = {}
        groups = getattr(self.build, "groups", None)
        if glory_names and callable(groups):  # (a stand-in build in a unit test may have none: no cap)
            found = {g["index"]: n["name"] for g in groups() if g.get("enabled") is not False
                     for n in g.get("gems") or [] if n.get("enabled", True) and n.get("name") in glory_names}
            if found:
                for rate in self.build.call("useRates", indices=sorted(found))["rates"]:
                    name, speed = found.get(rate.get("index")), number(rate.get("speed"))
                    if name and speed > 0:
                        speeds[name] = max(speed, speeds.get(name, 0.0))
        total, how = models.glory_uses(names, stats, main_skill, skills, texts)  # (cooldowns, Glory lines: MECHC13)
        for name, cap in speeds.items():  # (each capped skill's excess over its own speed comes back out)
            own = models.glory_uses([name], stats, main_skill, skills, texts)[0]
            if own > cap:
                total -= own - cap
                how += f"; {name} capped at its own {cap:.2f} uses/s (not {own:.2f})"
        return max(total, 0.0), how

    def warcries_recently(self) -> tuple[float, str]:
        """(different Warcries used Recently on average, how): each different Warcry in the build's enabled groups is up
        min(1, 4 s x its uses per second) of the fight, its uses from the bridge's useRates (conditions.skill_use_rate:
        its cooldown, else its duration, else the search's press choice, else 0); none in the build: 0."""
        gems = getattr(self.build.ctx, "all_gems", {}) or {}
        found = {g["index"]: n["name"] for g in self.build.groups() if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")
                 and "Warcry" in ((gems.get(n["name"]) or {}).get("skillTypes") or [])}
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
        gems = getattr(self.build.ctx, "all_gems", {}) or {}
        found = {g["index"]: n["name"] for g in self.build.groups() if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")
                 and "Warcry" in ((gems.get(n["name"]) or {}).get("skillTypes") or [])}
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
        """Whether any of the build's source lines (passives and items, bridge sourceLines) matches `pattern`."""
        call = getattr(self.build, "call", None)  # (a stand-in build in a unit test may have none)
        return bool(call) and any(re.search(pattern, line["text"]) for line in call("sourceLines")["lines"])

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
        return share, (f"Runic Reprieve channelled into {paid:.2f} of the boss's {uses:.2f} damaging uses a second, as "
                       f"Runic Ward regeneration {regen:,.0f}/s pays for {per_hit:,.0f} Ward a blocked hit (PoB's "
                       "WardRegenRecovery; other Ward sources and Ward-cost skills' spending not counted here; the "
                       "20% of maximum Ward Reprieve needs to start not checked), within 4 s of one "
                       f"{share:.0%} of the fight (Poisson, LOW). The time channelling (at least its {windup:.2f} s "
                       "wind-up a block) isn't taken from the DPS: an overcount of DPS for a build playing it, "
                       f"{paid * windup:.0%} of the fight at most from the wind-up alone")

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
        return conditions_mod.jagged_ground(main, main_types, main_gems, stats, gains, aftershock, rage)

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
