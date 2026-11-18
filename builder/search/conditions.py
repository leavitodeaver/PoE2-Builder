"""Condition resolver (PLAN.md §5.4): turn a PoB config condition on only when the build has a
real source for it, and off again when the source goes away.

Evidence is either text (a passive, item line or gem description that generates the effect,
e.g. "Gain a Frenzy Charge on Critical Hit") or PoB's own numbers (a chance to shock high enough
to keep a boss shocked). The table is data; the ledger can extend it later. Conditions on the boss that a fight
sustains only at a rate (Freeze, Electrocute, Pin, Heavy Stun, Daze) are on at their share of the fight (_box).

Campaign quest rewards that are a choice (e.g. +5% Cold Resistance or +5 Intelligence) are free
stats every character picks, so they're chosen here too, by score.
"""

from __future__ import annotations

from functools import lru_cache
import functools
import math
import pathlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search import boss_hits, bosses, models
from builder.search.context import gem_of
from builder.search.objective import number
from builder.search.state import EPSILON, Build

AILMENT_WINDOW = 4.0  # seconds "recently" looks back for a crit
UPTIME = 0.9  # chance that at least one hit applied it within the window
# Effects kept up by the build's own rates (ailments, Onslaught, Arcane Surge, Armour Broken, Exposure, the debuffs) count
# at their real share of the fight, not all or nothing at UPTIME (the user, 9 Nov 2026: "Count the real share"). The box
# is on when the effect is up at least MIN_UPTIME of the fight; under FULL_UPTIME the headline blends it with a
# recalculation without it (Search.weigh_uptimes, objective.uptime_factor). Both cut-offs are labelled assumptions.
MIN_UPTIME = 0.05  # under 5% of the fight: off
# An effect that is off switches on only from SWITCH_ON; once on, it stays on down to MIN_UPTIME (resolve). The user, 9 Nov
# 2026: "if something isn't active at least 7% of the time, it's basically worthless". The gap keeps an effect near the
# line from flipping with every trial: 2b75b's Ignite at 0.6-1.2% of the fight flipped 28 times in a 60 s probe's
# supports step, and each flip chose the main skill again (11 s of its 22 s), so the probe never reached the tree and the
# run fell from x5.18 to x2.60 (MECH31, POE2T399-401). Labelled: an effect up less than 7% of the fight (5% once on) is
# left out, an undercount of at most that share of its gain.
SWITCH_ON = 0.07
FULL_UPTIME = 0.995  # from 99.5%: simply on (and its defences count toward the floors)
# Each ailment's own base duration on an enemy, the 0.5 data's game constants as PoB reads them (Data/Misc.lua
# BaseIgniteDuration ...): the fallback when PoB gives the skill no duration of its own ({Ailment}Duration, with the
# build's duration modifiers). poe2db's ailment pages not yet read against these.
AILMENT_DURATION = {"Ignite": 4.0, "Bleed": 5.0, "Poison": 2.0, "Shock": 8.0}
EVIDENCE_FIELDS = [
    "CritChance", "Speed", "ShockChancePerHit", "IgniteChancePerHit", "PoisonChancePerHit", "BleedChancePerHit",
    *(f"{a}Duration" for a in AILMENT_DURATION),
    "ChillChanceOnHit", "MaximumRage", "EnergyShield", "EvadeChance", "EffectiveBlockChance", "enemySkillTime",
    "HitChance", *(f"{c}Charges{f}" for c in ("Power", "Frenzy", "Endurance") for f in ("Max", "Duration")),
    "PresenceRadius", "Ward", "Mana", "ManaPerSecondCost", "ManaRegenRecovery", "Devotion", *models.IMMOBILISE_FIELDS,
    "ChaosHitAverage", "MainHand.ChaosHitAverage", "EnemyAilmentThreshold",  # (Withering Touch, _withered_stacks)
    # (the boss's Ignite on you, which pauses Rage loss while it burns: boss_hits.es_dot; MECHC7)
    "AilmentThreshold", "Life", "FireTakenHit", "IgniteAvoidChance", "SelfIgniteDuration",
    "ChillDuration", "ChillEffectMod",  # (chill_share; MECH53, MECH54)
    "ColdHitAverage", "MainHand.ColdHitAverage",  # (the Cold Exposure support's crits with Cold Damage; MECH69)
    "PhysicalCritAverage",  # (Concussive Spells' crits with Physical damage, _dazed; MECH70)
    "ShockAvoidChance",  # (Overcharge's reflected Shocks, _self_shocked; MECH75)
    "LifeUnreserved",  # (Clash: the most Life % you fight at, _clash; MECH76)
    "FireDot",  # (the main skill's own Fire damage over time: the boss Burning, _enemy_burning; MECH80)
    "TotemLife",  # (Warbringer's Wooden Wall: the nearest totem's Life, totem_life_before_you; MECHC34)
]
RECENTLY = 4.0  # seconds ("Recently": the past 4 seconds)


def _hit_recently(stats: dict) -> bool:
    """At least one boss hit lands in any 4 s with chance UPTIME (landing hits come one after another, one every enemySkillTime)."""
    # (red-flash hits always land, MECHC5; MECHC20: a gain, so the worst case reads the lowest real boss's, gain_hit_recently)
    return boss_hits.gain_hit_recently(stats, RECENTLY, UPTIME)


WITHERED_MAX = 10  # "Withered applies 5% increased Chaos damage Taken, and can be inflicted up to 10 times" (poe2db)
WITHER_CHANCE = re.compile(r"(\d+)% chance to inflict Withered for (\d+(?:\.\d+)?) seconds on Hit", re.I)
WITHER_SPELLS = re.compile(r"^Spells have\b", re.I)  # (the unique's "Spells have a 25% chance ...": not for an Attack)
WITHER_KEEP = re.compile(r"Withered does not expire on Enemies (Ignited by you|you Ignite)", re.I)
WITHER_FOREVER = re.compile(r"^Withered you inflict has infinite Duration$", re.I)
# Withered from gems (RULE_AUDIT #1, MECH42, 11 Nov 2026). Game files (build 26476662), every stat whose id holds "wither"
# and where it's used (scratch/mech42/wither_scan.py): skill stat sets WitherPlayer (active_skill_withered_base_duration_ms,
# a channel, 0.25 s a tick), WeaponGrantedChaosboltPlayer (that duration, 4 s, and apply_x_wither_on_hit), HisFoulEmergencePlayer
# (10 s, from its Withering Ground: "applies Withered every second to enemies standing in it", KeywordPopups),
# SupportWitheringTouchPlayer (support_withered_base_duration_ms 4 s; withered_on_hit_chance_%_for_every_100%_target_
# ailment_threshold_dealt_as_chaos_damage 175, rollover-capped), WitheringPresencePlayer and its unique and Rogue Exile copies
# (skill_withering_presence_frequency_ms; base_skill_effect_duration 6 s; PoB's description: "periodically Withers enemies in
# your Presence"), ArchonOfChayulaPlayer (25% chance, 4 s, while in the Archon state); passives Wither Away (on entering your
# Presence), Withering Hex (non-cursed enemies you curse), Right Hand of Darkness (minions' chance); uniques' chance lines,
# Kurgal's (against Abyssal Wasting), Cospri's "infinite Duration". wither_duration_+% is on a monster strongbox skill only
# among the tables scanned (Mods, PassiveSkills, stat sets), so no duration modifier is read.
WITHER_ON_HIT_SKILLS = ("Wither", "Chaos Bolt")  # the main skill's own Withered, at its uses per second
WITHERING_PRESENCE = "Withering Presence"
WITHER_SKILL_STATS = ("active_skill_withered_base_duration_ms", "apply_x_wither_on_hit")
WITHERING_PRESENCE_STATS = ("skill_withering_presence_frequency_ms", "base_skill_effect_duration")
WITHER_SUPPORT_STATS = ("support_withered_base_duration_ms",
                        "withered_on_hit_chance_%_for_every_100%_target_ailment_threshold_dealt_as_chaos_damage")
WITHER_FIELDS = ("Speed", "HitChance", "IgniteChancePerHit", "IgniteDuration", "PresenceRadius", "ChaosHitAverage",
                 "MainHand.ChaosHitAverage", "EnemyAilmentThreshold", "Minion.Speed", "Minion.HitChance",
                 "MovementSpeedMod")  # (MECH83: Wither Away's entries, models.presence_reentries' roll length)
# (MECH67; RESEARCH22 #130, RESEARCH11 withered-sources.md #48) Two chance lines with no duration in their text:
# chance_to_inflict_wither_%_against_enemies_with_abyssal_wasting ("N% chance to inflict Withered with Hits against targets
# affected by Abyssal Wasting": Kurgal's Passage mods, a Grip of Kulemak Desecrated line) and
# minions_have_%_chance_to_inflict_wither_on_hit ("Minions have N% chance to inflict Withered on Hit": Right Hand of
# Darkness). Withered has no base duration in the game files (KeywordPopups Withered, the stat descriptions) or on poe2db's
# Withered page (fetched 13 Nov 2026: "no duration value is listed"): taken as the shortest stated one, 2 s (Ritual of
# Shadows, the monster mods), the lower reading, LOW (a flat number where the game decides and no data gives it).
# (RESEARCH26 R26-74, LOW-MEDIUM: ASSUMED 2 s per stack, each on its own timer: PoE1's documented base 2 s and PoE2's
# generic "inflict Withered for 2 seconds on Hit"; if it were 4 s these two lines' stacks would double.)
# The Wasting line: at the main skill's hits x the share the boss is Wasted (models.boss_power's: 6 s per Hit, Infinite
# Duration), only with an "Inflict Abyssal Wasting on Hit" source. The minions' line: one minion's hits (PoB's
# Minion.Speed x Minion.HitChance for the main skill's minion), the others not counted (an undercount, labelled); 0 when
# the main skill isn't a minion's.
WITHER_DEFAULT_DURATION = 2.0
WITHER_WASTING = re.compile(r"^(\d+)% chance to inflict Withered with Hits against targets affected by Abyssal Wasting$",
                            re.I)
WITHER_MINIONS = re.compile(r"^Minions have (\d+)% chance to inflict Withered on Hit$", re.I)
# Withering Hex (MECH75; the notable chaos40, stat non_cursed_enemies_you_curse_gain_x_withered_stacks 5, stat_descriptions:
# "Non-Cursed enemies you Curse have {0} Withered stacks inflicted on them"; no duration in the text, so each stack lasts
# WITHER_DEFAULT_DURATION, 2 s, LOW, as the minions' line). It fires only when a curse lands on a boss that carries none of
# your curses: each curse application from models.curse_uses that finds the boss uncursed. A curse cast by hand is recast
# before it runs out (curse_uses' play), and an Aura, infinite-duration or curse-on-hit curse keeps the boss cursed: after
# the fight's first curse, none finds it uncursed (0, counted so; the first isn't counted). A triggered curse up part of
# the fight (curse_uses' share = rate x duration < 1) finds it uncursed at every trigger: share / duration applications a
# second (the triggers taken as evenly spaced, MEDIUM), each x the share the build's other curses are off. Not offered:
# letting a hand-cast curse lapse on purpose (5 stacks every duration + CURSE_DELAY, the curse off the delay each time).
WITHER_HEX = re.compile(r"^Non-Cursed enemies you Curse have (\d+) Withered stacks inflicted on them$", re.I)
# Wither Away (MECH83; MECH82's Handover step 3, RESEARCH26 R26-34's "same for WITHER_UNCOUNTED's entry line"; before, the
# line counted 0: an UNDERCOUNT). The passive chaos38 (PassiveSkills, 26650308: withered_magnitude_+% 20
# and inflict_withered_for_x_seconds_on_unwithered_enemies_when_they_enter_your_presence 8; stat_descriptions "Unwithered
# enemies are Withered for {0} seconds when they enter your Presence"). (checked: Stats.json for every stat id holding
# "enter" and "presence": this one, Beetlebite's Aggravate, the Plants' Overgrow and the crit vs entered Recently; every
# table row referencing it: chaos38 only, no Mods, no stat set; PoB's ModCache 6836 / ModParser 3209 read it only as
# Condition:CanWither, which lets the box's stacks count but sets none.) Counted: an entry inflicts one Withered stack
# (Withered "can be inflicted up to 10 times", KeywordPopups; one stack per infliction, MEDIUM) for its seconds, and only
# on a boss carrying none ("Unwithered"). Entries come at models.presence_reentries' rate r (the build's rolls out of and
# back into its Presence; 0 when the boss stays outside it or the roll can't leave it, CLAUDE.md: no flat rate). Taken as
# random in time, an entry stack lasting D is on rD / (1 + rD) of the fight when nothing else Withers the boss (an entry
# during it finds the boss Withered and does nothing; the next after it lands): 2/3 at one entry per 4 s and 8 s. With
# other sources keeping s stacks on average (Poisson count), the boss carries at least one 1 - e^-s x (1 - rD / (1 + rD))
# of the fight (the lower reading: an entry while the others' stacks are up is lost). Only that "at least one" matters to
# PoB's whole count when s < 1: the box is 1 stack for that share (the uptime weighting, Blackflame's way); from s >= 1 the
# entry's expected stacks join the others'. Duration modifiers on Withered aren't read (as the other lines).
WITHER_ENTRY = re.compile(r"^Unwithered enemies are Withered for (\d+(?:\.\d+)?) seconds? when they enter your Presence$",
                          re.I)


def wither_entry_share(rate: float, duration: float) -> float:
    """Share of the fight one entry-inflicted Withered stack is on a boss nothing else Withers (WITHER_ENTRY's note)."""
    if rate <= 0 or duration <= 0:
        return 0.0
    return rate * duration / (1 + rate * duration)


# Lines that inflict Withered but aren't counted (labelled in the basis): on curse
WITHER_UNCOUNTED = re.compile(r"withered .*when they enter your presence|minions have .*withered|abyssal wasting.*|"
                              r"withered with hits against|curse .*withered|withered .*curse", re.I)


def withering_hex_rate(uses: list) -> tuple[float, str]:
    """(curse applications a second that find the boss carrying none of your curses, how) for Withering Hex (WITHER_HEX's
    note), from models.curse_uses."""
    total, parts = 0.0, []
    for use in uses:
        if (use.by_hand and not use.lapse) or not 0 < use.share < 1 or use.duration <= 0:
            continue
        rest = 1.0
        for other in uses:
            if other is not use:
                rest *= 1.0 - min(1.0, max(0.0, other.share))
        if use.lapse:  # (MECH75: let run out, one cast per duration + the gap, models.CURSE_LAPSE)
            each = rest / (use.duration + models.CURSE_LAPSE_GAP)
            kind = f"cast by hand and let run out, recast {models.CURSE_LAPSE_GAP:g} s after (the user, 15 Nov: player timing)"
        else:
            each = use.share / use.duration * rest
            kind = f"triggered, {use.share:.0%} of the fight; triggers taken as evenly spaced, MEDIUM"
        if each > 0:
            total += each
            parts.append(f"{use.name} lands on the uncursed boss {each:.2f} times/s ({kind}, {use.duration:.3g} s each)")
    if total > 0:
        return total, "; ".join(parts)
    if not uses:
        return 0.0, "no curse in the build"
    return 0.0, ("the boss stays cursed: a hand-cast curse is recast before it runs out, an Aura, infinite or on-hit "
                 "curse never lapses; after the first curse none finds it uncursed (letting a hand-cast curse run out "
                 "on purpose is a search choice, models.CURSE_LAPSE, not taken here)")


def curse_lapse_candidates(build, texts: list[str]) -> list[str]:
    """The hand-cast curses the search may play to run out before each recast (models.CURSE_LAPSE), for Withering Hex:
    none without its line. For SEARCH's toggle (each tried on and off, the higher score kept, as Raise Shield's taps)."""
    if not any(WITHER_HEX.match(t.strip()) for t in texts):
        return []
    stats = {"PresenceRadius": 0.0}
    return sorted({u.name for u in curse_uses(build, stats, [], 0.0) if u.by_hand})


def _withered_stacks(lines: list[str], stats: dict) -> tuple:
    """Withered stacks the build keeps on a boss, up to 10: from its "chance to inflict Withered" lines (chance x hits per
    second x duration), the main skill's own Withered (Wither, Chaos Bolt: stacks per use x uses per second x duration),
    Withering Touch on it (175% chance per 100% of the boss's Ailment Threshold dealt as Chaos per hit, the expected stacks
    of a rollover chance), and Withering Presence (duration / frequency while the boss stands in your Presence). With
    "infinite Duration" (Cospri's), any source keeps all 10. With Blackflame ("does not expire on Enemies you Ignite"), all
    10 while the boss is Ignited: counted at Ignite's real share of the fight (the user, 9 Nov 2026: "Count the real
    share"; under SWITCH_ON, 7%, left out), the other stacks the rest of it (the 4th value: the box's setting with the
    effect off, Search.weigh_uptimes). PoB reads the count whole (ModStore floors a Multiplier), so the time weighting is
    done by the uptime machinery, not a fractional count. Labelled: stacks that outlast an Ignite by their own duration
    aren't counted (a small undercount), the build-up to 10 at the fight's start is taken as instant, and duration
    modifiers aren't read (Withering Presence's base 6 s; skill duration mods would lengthen it: an undercount)."""
    hit = number(stats.get("HitChance")) / 100 if "HitChance" in stats else 1.0
    rate = number(stats.get("Speed")) * hit  # hits per second
    spell = "Spell" in (stats.get("_main_types") or set())
    stacks, sources, idle, entry_seconds = 0.0, [], [], []
    wasting = any(models.WASTING_ON_HIT.match(t) for t in lines)
    wasted = (1.0 if any(models.WASTING_INFINITE.match(t) for t in lines) else min(1.0, rate * models.WASTING_DURATION)
              ) if wasting and rate > 0 else 0.0
    minion_rate = number(stats.get("Minion.Speed")) * (number(stats.get("Minion.HitChance")) / 100
                                                        if "Minion.HitChance" in stats else 1.0)
    for text in lines:
        if m := WITHER_CHANCE.search(text):
            if WITHER_SPELLS.search(text) and not spell:
                idle.append(f"{text} (the main skill isn't a Spell)")
                continue
            stacks += float(m.group(1)) / 100 * rate * float(m.group(2))
            sources.append(text)
        elif m := WITHER_WASTING.match(text):  # (MECH67)
            if wasted <= 0:
                idle.append(f"{text} (nothing Wastes the boss)" if not wasting else f"{text} (the main skill doesn't hit)")
                continue
            stacks += float(m.group(1)) / 100 * rate * wasted * WITHER_DEFAULT_DURATION
            sources.append(f"{text} (the boss Wasted {wasted:.0%} of the fight; {WITHER_DEFAULT_DURATION:g} s each, "
                           "no duration in the game data: the shortest stated one, LOW)")
        elif m := WITHER_MINIONS.match(text):  # (MECH67)
            if minion_rate <= 0:
                idle.append(f"{text} (the main skill isn't a minion's)")
                continue
            stacks += float(m.group(1)) / 100 * minion_rate * WITHER_DEFAULT_DURATION
            sources.append(f"{text} (one minion's {minion_rate:.2f} hits/s, the others not counted; "
                           f"{WITHER_DEFAULT_DURATION:g} s each, no duration in the game data: the shortest stated one, LOW)")
        elif m := WITHER_HEX.match(text):  # (MECH75)
            each, why = withering_hex_rate(stats.get("_curse_uses") or [])
            if each <= 0:
                idle.append(f"{text} ({why})")
                continue
            stacks += float(m.group(1)) * each * WITHER_DEFAULT_DURATION
            sources.append(f"{text} ({why}; {WITHER_DEFAULT_DURATION:g} s each, no duration in the game data: the "
                           "shortest stated one, LOW)")
        elif m := WITHER_ENTRY.match(text.strip()):  # (MECH83: at the Presence re-entry rate, below)
            entry_seconds.append(float(m.group(1)))
        elif WITHER_UNCOUNTED.search(text) and not models.WASTING_ON_HIT.match(text):
            idle.append(text)
    name, own = stats.get("_wither_main") or ("", {})
    if (duration := number(own.get(WITHER_SKILL_STATS[0])) / 1000) > 0:
        each = number(own.get(WITHER_SKILL_STATS[1])) or 1.0
        stacks += each * rate * duration
        sources.append(f"{name}: {each:g} Withered per hit for {duration:g} s")
    support = stats.get("_wither_support") or {}
    duration = number(support.get(WITHER_SUPPORT_STATS[0])) / 1000
    if duration > 0 and (chance := number(support.get(WITHER_SUPPORT_STATS[1]))):
        chaos = number(stats.get("ChaosHitAverage")) or number(stats.get("MainHand.ChaosHitAverage"))
        threshold = number(stats.get("EnemyAilmentThreshold"))
        each = chance / 100 * chaos / threshold if threshold > 0 else 0.0
        stacks += each * rate * duration
        sources.append(f"Withering Touch: {chance:g}% chance per 100% of the boss's {threshold:,.0f} Ailment Threshold "
                       f"dealt as Chaos, a {chaos:,.0f} Chaos hit: {each:.2f} Withered per hit for {duration:g} s")
    if presence := stats.get("_wither_presence"):
        every, duration = presence
        metres = _boss_distance(lines, stats)[0] / 10
        radius = number(stats.get("PresenceRadius")) / 10
        if every > 0 and radius >= metres:
            stacks += duration / every
            sources.append(f"{WITHERING_PRESENCE}: Withered for {duration:g} s every {every:g} s on the boss in your "
                           f"Presence ({radius:.1f} m, the boss {metres:g} m away)")
        else:
            idle.append(f"{WITHERING_PRESENCE} (the boss {metres:g} m away, outside your {radius:.1f} m Presence)")
    entry_up, entry_how = 0.0, ""
    if entry_seconds:  # (MECH83: Wither Away, WITHER_ENTRY's note)
        rate, how = models.presence_reentries(lines, stats, _boss_distance(lines, stats)[0] / 10)
        seconds = max(entry_seconds)
        entry_up = wither_entry_share(rate, seconds)
        entry_how = (f"Unwithered enemies are Withered for {seconds:g} seconds when they enter your Presence: {how}; one "
                     f"stack {seconds:g} s per entry on an Unwithered boss, {entry_up:.0%} of the fight (MEDIUM)")
        if entry_up <= 0:
            idle.append(entry_how)
    left = f"; not counted: {'; '.join(t[:90] for t in idle)}" if idle else ""
    if entry_up > 0 and min(float(WITHERED_MAX), stacks) < 1:
        up = 1 - math.exp(-stacks) * (1 - entry_up)
        basis = (f"{entry_how}" + (f"; with {'; '.join(sources)} ({stacks:.2f} stacks on average)" if sources else "")
                 + f": at least one Withered stack {up:.0%} of the fight{left}")
        if up >= FULL_UPTIME:
            return 1.0, basis
        if up >= SWITCH_ON:
            return 1.0, basis + f" ({shared(up)})", up, False
        return 0.0, basis
    if entry_up > 0:
        sources.append(f"{entry_how}, x e^-{stacks:.1f} for the share the other stacks leave the boss Unwithered")
        stacks += entry_up * math.exp(-stacks)
    if not sources:
        return 0.0, ""
    if any(WITHER_FOREVER.search(t) for t in lines):
        return (float(WITHERED_MAX), f"{sources[0]}; and Withered you inflict has infinite Duration: {WITHERED_MAX} "
                                     f"stacks (the build-up taken as instant){left}")
    stacks = min(float(WITHERED_MAX), stacks)
    basis = f"{'; '.join(sources)} at {rate:.2f} hits/s: {stacks:.1f} stacks kept up{left}"
    up = ailment_uptime(stats, "Ignite") if any(WITHER_KEEP.search(t) for t in lines) else 0.0
    if up >= FULL_UPTIME:
        return float(WITHERED_MAX), f"{sources[0]}; and Withered never expires on the Ignited boss: {WITHERED_MAX} stacks"
    if up >= SWITCH_ON:
        return (float(WITHERED_MAX), f"{sources[0]}; and Withered never expires on the Ignited boss: {WITHERED_MAX} "
                f"stacks while it is Ignited ({up:.0%} of the fight, {shared(up)}), else {basis}", up, int(stacks) or False)
    return float(int(stacks)), basis


# Incision (MECH38, 11 Nov 2026; before, nothing set PoB's Incision stack count, so Thousand Cuts' "take 2% increased
# Physical Damage per Incision", "against Enemies affected by Incision" lines and the Incision support's "Bleeding effect
# per Incision consumed recently" counted 0). The game files' KeywordPopups: "Each stack of Incision applies 10% chance to
# be inflicted with Bleeding when Hit. A maximum of 10 Incision stacks ... All Incision stacks are removed when Bleeding is
# inflicted." Per main-skill hit (a Markov chain over 0-10 stacks): Bleed rolls at the build's own chance (PoB's
# BleedChancePerHit, which leaves Incision out: PoB adds Incision's chance to the enemy's SelfBleedChance, which nothing
# reads) plus 10% x (1 + Incision effect) per stack; a Bleed removes them; then the hit's own Incision lands (the
# support's apply_X_incision_on_hit, "N% chance for Attack Hits to apply Incision" / "ten Incision"). Labelled: the
# order within a hit (a hit's Incision counts from the next hit, LOW); only the main skill's hits (an undercount); PoB
# floors a Multiplier, so the stack count is rounded to the nearest whole one; the "affected by Incision" lines are on
# when the average is 1 or more; Incision's own Bleed chance isn't added to PoB's Bleed numbers (an undercount).
INCISION_MAX = 10
INCISION_CHANCE = re.compile(r"^(\d+)% chance for Attack Hits to apply (ten |\d+ )?Incision$", re.I)
INCISION_EFFECT = re.compile(r"^(\d+)% increased effect of Incision$", re.I)
INCISION_SUPPORT_STATS = ("apply_X_incision_on_hit",)


def incision_state(lines: list[str], stats: dict) -> tuple[float, float, str]:
    """(average stacks on the boss, Incision consumed per second, how) for the main skill's hits."""
    hits = models.hits_per_second(stats)
    attack = "Attack" in (stats.get("_main_types") or set())
    gains: list[tuple[float, int]] = []  # (chance per hit, stacks)
    support = int(number(stats.get("_incision_per_hit")))
    if support > 0:
        gains.append((1.0, support))
    effect = 0.0
    for text in lines:
        if attack and (m := INCISION_CHANCE.match(text.strip())):
            n = m.group(2)
            gains.append((min(int(m.group(1)) / 100, 1.0), 10 if n and n.strip().lower() == "ten" else int(n or 1)))
        elif m := INCISION_EFFECT.match(text.strip()):
            effect += int(m.group(1)) / 100
    if hits <= 0 or not gains:
        return 0.0, 0.0, ""
    base = min(number(stats.get("BleedChancePerHit")) / 100, 1.0)
    per = 0.1 * (1 + effect)
    # gained per hit: the distribution of the stacks a hit adds (independent rolls)
    add = {0: 1.0}
    for chance, n in gains:
        nxt: dict[int, float] = {}
        for k, p in add.items():
            nxt[min(k + n, INCISION_MAX)] = nxt.get(min(k + n, INCISION_MAX), 0.0) + p * chance
            nxt[k] = nxt.get(k, 0.0) + p * (1 - chance)
        add = nxt
    dist = [1.0] + [0.0] * INCISION_MAX  # stacks between hits
    consumed = 0.0
    for _ in range(400):
        nxt, eaten = [0.0] * (INCISION_MAX + 1), 0.0
        for s, p in enumerate(dist):
            if p <= 0:
                continue
            bleed = min(base + per * s, 1.0)
            eaten += p * bleed * s
            for start, w in ((0, bleed), (s, 1 - bleed)):
                if w > 0:
                    for k, q in add.items():
                        nxt[min(start + k, INCISION_MAX)] += p * w * q
        dist, consumed = nxt, eaten
    avg = sum(s * p for s, p in enumerate(dist))
    how = (f"{hits:.2f} hits/s adding {'; '.join(f'{c:.0%} x {n}' for c, n in gains)} Incision, Bleed at "
           f"{base:.0%} + {per:.0%} per stack removing them: {avg:.2f} stacks on average, {consumed * hits:.2f} consumed/s")
    return avg, consumed * hits, how


def _incision_stacks(lines: list[str], stats: dict) -> tuple:
    avg, _, how = incision_state(lines, stats)
    if not how:
        return 0, ""
    n = round(avg)
    return n, f"Incision: {how}; counted as {n} (PoB reads whole stacks)"


def _incision_consumed(lines: list[str], stats: dict) -> tuple:
    if number(stats.get("_incision_per_hit")) <= 0:
        return 0, ""  # (only the Incision support reads it)
    _, per_second, how = incision_state(lines, stats)
    if not how:
        return 0, ""
    n = round(per_second * RECENTLY)
    return n, f"Incision consumed in the past {RECENTLY:g} s: {per_second:.2f}/s x {RECENTLY:g} s = {n} ({how})"


# Charges held (the mechanics catalog's first priority, 7 Nov 2026): PoB counts the maximum whenever its "use" box is on.
# The count a build holds is min(maximum, generation per second x the charge's duration), from the build's own sources:
# poe2db (https://poe2db.tw/us/Charges): charges last a duration (PoB: 15 s base, ChargeDuration) and come from specific
# sources; on-kill sources at the build's kills (kill_events: the boss preset's adds, and your own minions Killed for
# lines that don't say "enemy"; MECHC9, A23-4; none against a lone boss). A source whose rate its line doesn't say (on use,
# when you stun, on reaching something) is assumed once per 4 s, labelled.
CHARGE_DEFAULT_EVERY = 4.0
# Events a line names without its rate (FLAT_RATES A3; MECH47): a Warcry used, a Totem summoned, a Mark activating.
# Before, each fell to one per CHARGE_DEFAULT_EVERY s whether or not the build had such a skill (an overcount with
# none). Now the rate comes from the build's own skills (event_rates, in Conditions.evidence as stats["_events"]): the
# main skill's uses if it is one; else each such skill's group at its use rate (bridge useRates: its cooldown, else its
# duration, else the press rate the search chose (models.press_choices), else 0, labelled: MECH56, the user's "search
# picks the rate"); none of that kind in the build's enabled groups: 0, labelled. A Mark is taken
# to activate as often as it is applied (an upper bound). Other wordings keep the 4 s default, labelled LOW.
EVENT_WORDS = {
    "Warcry": re.compile(r"\b(?:use|using|uses) (?:a )?warcr(?:y|ies)|\bon warcry|warcr(?:y|ies) (?:is|are) used|"
                         r"when you warcry", re.I),
    "SummonsTotem": re.compile(r"\b(?:summon|plac)\w* (?:a |an )?(?:\w+ )?totems?|totems? (?:is|are) (?:summoned|placed)",
                               re.I),
    "Mark": re.compile(r"\bmarks? activat|\b(?:use|apply|applying) a mark\b|mark (?:you have applied )?activates", re.I),
}
EVENT_NAMES = {"Warcry": "a Warcry used", "SummonsTotem": "a Totem summoned", "Mark": "a Mark activating",
               "LifeFlask": "a Life Flask used", "ManaFlask": "a Mana Flask used", "Flask": "a Flask used"}


# A flask used (MECH48; before, one per 4 s): the build's equipped flasks at the uses their generated charges pay for
# (flasks.uses_per_second; Conditions._item_uses), 0 with none. A flask's or charm's own "on use" line reads that item's
# uses the same way (stats["_item_uses"], by line text).
FLASK_EVENT = re.compile(r"\b(?:use|using|uses) an? (life |mana )?flask", re.I)
ON_USE = re.compile(r"\bon use\s*$", re.I)
USED_SLOTS = ("item:Flask", "item:Charm")


def _named_event(when: str, stats: dict) -> tuple[float, str] | None:
    """(events per second, how) for a line's event from the build's own skills (stats["_events"]), or None if the line
    names none of EVENT_WORDS (or the table wasn't built)."""
    events = stats.get("_events") or {}
    if (flask := FLASK_EVENT.search(when)) and (kind := f"{(flask.group(1) or '').strip().capitalize()}Flask") in events:
        rate, how = events[kind]
        return rate, f"a {(flask.group(1) or '')}Flask used: {how}"
    for kind, pattern in EVENT_WORDS.items():
        if pattern.search(when) and kind in events:
            rate, how = events[kind]
            return rate, f"{EVENT_NAMES[kind]}: {how}"
    return None


def skill_use_rate(rate: dict, choices: dict[str, float] | None = None) -> tuple[float, str, bool]:
    """(uses per second, why, LOW?) of a non-main skill from the bridge's useRates entry for its group: on its cooldown,
    else once per its duration, else the rate the search chose for it (`choices`, models.press_choices; MECH56, the
    user's "search picks the rate"), else 0 (LOW, labelled: no source, 0), never faster than its own speed."""
    cooldown, duration = number(rate.get("cooldown")), number(rate.get("duration"))
    repeats = number(rate.get("repeats")) or 1.0
    low = False
    if cooldown > 0:
        each, why = repeats / cooldown, f"its {cooldown:.2f} s cooldown"
    elif duration > 0:
        each, why = repeats / duration, f"once per its {duration:.2f} s duration"
    elif (chosen := models.press_choice(rate, choices)) is not None and chosen > 0:
        each, why = repeats * chosen, (f"{chosen:.3g} presses a second*, the rate the search chose (a way to play the "
                                       "build; without it, it adds nothing)")
    else:
        each, why, low = 0.0, ("0 presses* (LOW: no cooldown or duration sets how often it's used, and the search chose "
                               "no rate: no source, 0)"), True
    if (speed_g := number(rate.get("speed"))) > 0:
        each = min(each, speed_g)
    return each, why, low


def event_rates(groups: list[dict], gems: dict[str, dict], main_skill: str, main_types: set[str], speed: float,
                use_rates: Callable[[list[int]], list[dict]], texts: list[str],
                choices: dict[str, float] | None = None) -> dict[str, tuple[float, str]]:
    """{kind: (events per second, how)} for each EVENT_WORDS kind a line names. `use_rates` is the bridge's useRates
    on group indices (called only when a non-main group holds such a skill)."""
    out: dict[str, tuple[float, str]] = {}
    for kind, pattern in EVENT_WORDS.items():
        if not any(pattern.search(t) for t in texts):
            continue
        if kind in main_types:
            out[kind] = (speed, f"the main skill {main_skill} is one, {speed:.2f} uses/s")
            continue
        found = {g["index"]: n["name"] for g in groups if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")
                 and kind in ((gems.get(n["name"]) or {}).get("skillTypes") or [])}
        if not found:
            out[kind] = (0.0, f"no {kind if kind != 'SummonsTotem' else 'Totem'} skill in the build's enabled groups: 0")
            continue
        total, parts, low = 0.0, [], False
        for rate in use_rates(sorted(found)):
            name = found.get(rate.get("index"))
            if name is None:
                continue
            each, why, guess = skill_use_rate(rate, choices)
            low = low or guess
            total += each
            parts.append(f"{name} on {why}, {each:.2f}/s")
        out[kind] = (total, "; ".join(parts) + (" (LOW)" if low else " (MEDIUM: used on cooldown or to keep it up)"))
    return out


# On-kill events (MECHC9, AUDIT23 A23-4; before, every on-kill line read 0, "nothing dies on a single boss"). The build's
# kills are models.kill_rate's (MECHB9): the boss preset's killable adds (bosses.kill_stats, MECH-D's AddKillsPerSecond:
# the King in the Mists' totems, Atziri's Royal Acolytes; none against a lone boss, whose own death ends the fight) and
# your own minions Killed (the manager's ruling on A17-4: Killed minions count as Kills, LOW, held for an in-game test).
# Lines that say "enemy" or "monster" read the adds only (your minions are allies: KeywordPopups MinionDeath / Allies);
# a plain "on Kill" line reads both. In stats["_kills"] (Conditions.evidence), (enemies/s, all/s, how).
# - "Rare or Unique": the adds are taken as Normal (RESEARCH26 R26-55, ASSUMED MEDIUM: no summoned_monster_rarity
#   on the King's Affliction Totem or Atziri's Royal Acolyte summon skills, their Life and fixed names fit Normal; an
#   undercount if some are Rare), your minions aren't enemies, and the boss's own death ends the fight: 0, labelled.
# - "with <weapon>" / "Killing Blow with <weapon>": only with that weapon held (either slot).
# - "affected by <X>" / "Culling Strike": counted as every kill (LOW, an overcount where the build doesn't apply X / can't
#   Cull the adds: the preset counts Culls as its kills, bosses.kill_stats).
# The adds come in waves (bosses.Kills: one per `every` s for `share` of the fight); their rate here is averaged over the
# whole fight (LOW: a small overcount for charges and buffs, as a gain held 4-15 s counts for less in a wave than spread
# out over the fight).
KILL_LINE = re.compile(r"\bkill|culling strike", re.I)
KILL_WEAPONS = r"axes?|maces?|swords?|spears?|flails?|quarterstaff|quarterstaves|daggers?|claws?|crossbows?|bows?|staff|staves"


def kill_events(when: str, stats: dict) -> tuple[float, str]:
    """(kills per second a line's "on kill" event fires at, how), from stats["_kills"] (models.kill_rate's tuple)."""
    kills = stats.get("_kills")
    if not kills:
        return 0.0, "on kill: the build's kills weren't read (0)"
    enemies, total, how = kills
    if re.search(r"\brare\b|\bunique\b", when):
        return 0.0, ("on killing a Rare or Unique enemy: 0 (the boss's death ends the fight; its adds taken as Normal, "
                     "ASSUMED MEDIUM: no rarity stat on their summon skills, RESEARCH26 R26-55; your minions aren't "
                     "enemies)")
    if weapon := re.search(rf"\b(?:with|using) (?:an? )?({KILL_WEAPONS})\b", when):
        word = weapon.group(1)
        word = {"quarterstaves": "quarterstaff", "staves": "staff", "axes": "axe"}.get(word, word.rstrip("s"))
        held = [t.lower() for t in (stats.get("_weapon_types") or [stats.get("_weapon") or ""]) if t]
        if not any(word in t for t in held):
            return 0.0, f"on kills with a {word.capitalize()}, and none is held: 0"
    enemy_only = bool(re.search(r"\benem(?:y|ies)\b|\bmonsters?\b", when))
    rate = enemies if enemy_only else total
    label = (f"{rate:.3f} kills/s" + (" (enemies only: the line says 'enemy')" if enemy_only and total > enemies else "")
             + f": {how}")
    if rate > 0 and re.search(r"affected by|culling strike|\bcull", when):
        label += "; every kill taken to meet the line's condition (LOW: an overcount if the build doesn't)"
    return rate, label


CHARGE_SOURCE = r"(?:(\d+(?:\.\d+)?)% chance (?:to|when you \w+ (?:a|an) \w+ Charge to)?\s*)?(?:gain|grants?)\w* (?:an? |(?P<count>\d+) )?(?:(?:(?!\bcharges?\b)[\w, ])*?\b{kind}\b[\w, ]*?) Charges?(.*)$"
# (the kind is named before the first "Charge": "Gain a Frenzy Charge on reaching Maximum Power Charges" is no Power gain;
# MECH49, it read as one per hit before)


def _hit_restriction(when: str, stats: dict) -> str | None:
    """Why the main skill's hits can't be the line's "on Melee (Axe) Hit" event, or None (MECH48). PoB parses "Gain N
    Rage on Melee Hit" with the melee restriction lost (the catalog's Melee Hit / Melee Axe Hit 'misparse' lines), and
    our rates read the main skill's hits: a Spell or ranged main skill gained Rage from them (an overcount). Only the
    main skill's hits are rated here, so other Melee skills beside it aren't counted (LOW, an undercount)."""
    if not re.search(r"\bmelee\b[^.;]*\bhit", when):
        return None
    if "Melee" not in (stats.get("_main_types") or {"Melee"}):
        return "on Melee Hits, and the main skill isn't Melee: 0"
    weapon = re.search(r"\bmelee (axe|mace|sword|spear|flail|dagger|claw|quarterstaff|staff) hit", when)
    # (either weapon slot, as MECHC2's check in _rage_gain reads them; Weapon 1's type when only that is known)
    held = [t for t in (stats.get("_weapon_types") or [stats.get("_weapon")]) if t] or [weapon.group(1)] if weapon else []
    if weapon and not any(weapon.group(1) in t.lower() for t in held):
        return f"on Melee {weapon.group(1).capitalize()} Hits, and no {weapon.group(1).capitalize()} is held: 0"
    return None


# Game files: GrantedEffectStatSets SupportFerocityPlayer carries it (checked: the stat's index in every table under 400 MB;
# PoB's sup_dex.lua Ferocity); read from the main skill's own stats and its supports' in the build, so any other carrier
# counts too
NO_FRENZY_STAT = "skill_cannot_generate_frenzy_charges"
# (MECHC33) "You cannot gain Power Charges while using this Skill" (skill_stat_descriptions; Flicker Strike's stat set,
# PoB act_int.lua FlickerStrikePlayer: Display Only); read from the main skill's own stats and its supports', so any
# other carrier counts too
NO_POWER_STAT = "cannot_gain_power_charges_during_skill"
PIN_MORE_SUPPORT, PIN_MORE_STAT = "Pin III", "support_pin_pin_duration_+%_final"  # (MECH66: "Pin caused by Supported
# Skills has 30% more duration", gem_stat_descriptions.csd; read by models.pin_duration_scale)
NO_BLIND_STAT = "cannot_inflict_blind"  # Blindside (sup_dex.lua SupportBlindsidePlayer; Stats.json)
# (the build's lines that name Blind: only then is the main skill asked for it; MECH59: a backspace had stood for \b
# here since MECH55, so the check never ran)
BLIND_LINE = re.compile(r"\bblind", re.I)


# (MECHC27; PATCH_CHECK K2-3, an OVERCOUNT: Combat Frenzy's Frenzy Charge was read at the full Freeze / Electrocute / Pin
# rate) a line's own cap: "no more than once per second" / "no more than once every N seconds" (combat_frenzy.csd's
# skill_combat_frenzy_x_ms_cooldown wording; any line saying it is capped the same way)
ONCE_EVERY = re.compile(r"no more than once (?:per second|every (\d+(?:\.\d+)?) seconds?)", re.I)


def _charge_rate(text: str, kind: str, stats: dict) -> tuple[float, str] | None:
    """(charges per second, how) from one line, or None if it isn't a source of that charge."""
    if kind == "Power" and stats.get("_main_no_power"):
        # (MECHC33, GEM15's find: Flicker Strike's "You cannot gain Power Charges while using this Skill",
        # cannot_gain_power_charges_during_skill, PoB: Display Only) the main skill's hits make none, and any other
        # gain lands only in the share of the fight spent away from it (dodge rolls as read; other presses not read:
        # LOW, the lower reading of a gain)
        away = min(max(number(stats.get("_rolling")), 0.0), 1.0)
        got = _charge_rate(text, kind, {**stats, "_main_no_power": None, "Speed": 0.0, "CritChance": 0.0})
        if got is None:
            return None
        return got[0] * away, (f"{got[1]}; x {away:.0%} of the fight away from the main skill, which can't gain Power "
                               f"Charges while used ({stats['_main_no_power']}): its hits make none (without it: "
                               f"{_charge_rate(text, kind, {**stats, '_main_no_power': None})[0]:.2f}/s)")
    got = _charge_rate_uncapped(text, kind, stats)
    cap = ONCE_EVERY.search(text)
    if got is None or cap is None:
        return got
    most = 1 / float(cap.group(1) or 1.0)
    return (got[0], got[1]) if got[0] <= most else (most, f"{got[1]}; at most once every {1 / most:g} s (the line's cap)")


# (MECHC38) Not a gain: "granted by" (Abyssal Apparition's "Spells granted by items, or Spells which already consume a
# Power Charge" matched CHARGE_SOURCE's "grant\w*"); and the events named before a skill's own gain that _charge_rate has
# no rate for outside Build.consume_gains (GEM19's consumes on the main skill): a consume, a Heavy Stun of a Primed enemy
NOT_A_GAIN = re.compile(r"(?:\d+(?:\.\d+)?% chance \w+ )?granted by\b", re.I)
EVENT_GAIN = re.compile(r"\bconsumes?\b|primed for heavy stun", re.I)


def consume_gain(build, skill: str, share: float, what: str) -> dict[str, tuple[float, str]]:
    """{the skill's description (its sourceLines text): (consumes a second, how)} for a main skill that grants a charge
    on each consume (Siphoning Strike: "Consume their Shock ... and grant you a Power Charge"; Disengage: "Consumes the
    Parried Debuff on Hitting enemies to release a shockwave and grant you a Frenzy Charge"; game 26650308 / PoB's gem
    text): its hits a second (PoB's Speed x HitChance) x `share` of them that find what they consume (GEM19's
    shock_set_share / parry_plan box). One charge a consume, as the text says. {} without a description."""
    text = ((getattr(build, "ctx", None) and build.ctx.all_gems.get(skill)) or {}).get("description") or ""
    if not text or share <= 0:
        return {}
    got = build.call("stats", fields=["Speed", "HitChance"])
    hits = number(got.get("Speed")) * (number(got.get("HitChance")) or 100) / 100
    return {text: (hits * share, f"{hits:.2f} hits/s x {share:.0%} {what} (GEM19's share, the last resolve's)")}


def _charge_rate_uncapped(text: str, kind: str, stats: dict) -> tuple[float, str] | None:
    """_charge_rate before the line's own "no more than once every N seconds" cap."""
    m = re.search(CHARGE_SOURCE.format(kind=kind), text, re.I)
    if not m or re.search(r"\bcannot gain\b|instead|allies|when you gain|if you would gain|per \w+ charge|lose|at maximum \w+ charges", text, re.I):
        return None
    if NOT_A_GAIN.match(text[m.start():]) or re.search(r"\bcannot generate\b", m.group(0), re.I):
        return None  # (MECHC38: "Spells granted by items ... consume a Power Charge", Ferocity's "cannot generate")
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    chance *= float(m.group("count") or 1)  # ("Gain 3 Power Charges on use": three per event; MECH48, was one)
    when = m.group(3).lower()
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    crits = hits * number(stats.get("CritChance")) / 100
    boss = 1 / bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHC11)
    if re.search(r"\bkill", when):  # (MECHC38: "kill" inside "This skill ..." read as on-kill before)
        rate, how = kill_events(when, stats)
        return chance * rate, f"{chance:.0%} x {how}"
    if kind == "Frenzy" and stats.get("_main_no_frenzy") and re.search(r"\bhit|critical|freeze|electrocute|\bpin|stun|immobilise",
                                                                    when) and "take" not in when and "block" not in when:
        # (MECHC3, FIX_QUEUE: Ferocity's skill_cannot_generate_frenzy_charges on the main skill: its hits make none)
        return 0.0, f"the main skill can't generate Frenzy Charges ({stats['_main_no_frenzy']}): its hits make none"
    if re.search(r"when you (freeze|electrocute|pin|stun|immobilise|heavy stun)|freeze, electrocute or pin", when):
        events = ({"freeze", "electrocute", "pin", "stun"} if "immobilise" in when
                  else {e for e in ("freeze", "electrocute", "pin", "stun") if e in when})
        rate, how = models.immobilise_rate(stats, events)
        return chance * rate, f"{chance:.0%} x {rate:.3f}/s ({how})"
    if (why := _hit_restriction(when, stats)) is not None:
        return 0.0, why
    if "critical hit" in when and "take" not in when:
        return chance * crits, f"{chance:.0%} x {crits:.2f} crits/s"
    if "take a critical hit" in when:
        # (MECHC20: a crit on you is a hit that lands: the boss's landed hits, the worst case's lowest real boss's; before,
        # every use of its skills, evaded and blocked ones too, an overcount)
        landed = boss_hits.gain_hits(stats)[0]
        return chance * landed * 0.05, f"when the boss crits you (5% of its {landed:.2f} landed hits/s)"
    if "block" in when:
        block = boss_hits.block_share(stats, evaded_first=False)  # (red-flash hits can't be Blocked, MECHC5)
        return chance * boss * block, f"{chance:.0%} x {boss * block:.2f} blocks/s"
    if "on hit" in when or when.strip() in ("", "on hit") or "hit" in when:
        return chance * hits, f"{chance:.0%} x {hits:.2f} hits/s"
    if m2 := re.search(r"every (\d+(?:\.\d+)?)? ?seconds?|once per second|every second", when):
        every = float(m2.group(1)) if m2.groups() and m2.group(1) else 1.0
        if NOT_LOST_RECENTLY.search(when):  # (MECHC33: the Cultivated Redflare's line)
            return _while_not_lost(kind, chance / every, text, stats)
        return chance / every, f"one every {every:g} s"
    if (named := _named_event(when, stats)) is not None:
        return chance * named[0], f"{chance:.0%} x {named[0]:.3f}/s ({named[1]})"
    if re.fullmatch(r"\s*on use\s*", when) and (used := (stats.get("_item_uses") or {}).get(text)) is not None:
        # a flask's or charm's own "on use" line (MECH48): that item's uses, from its generated charges
        return chance * used[0], f"{chance:.0%} x {used[0]:.3f} uses/s ({used[1]})"
    if "attack ignites" in when:
        if "Attack" not in (stats.get("_main_types") or set()):
            return 0.0, "the main skill isn't an Attack: no Attack Ignites (other Attacks not counted, LOW)"
        ignites = min(number(stats.get("IgniteChancePerHit")) / 100, 1.0) * hits
        return chance * ignites, f"{chance:.0%} x {ignites:.2f} Ignites/s (the main Attack's hits x its Ignite chance)"
    if reach := re.search(r"on reaching maximum (\w+) charges", when):
        return _reaching_maximum(reach.group(1).capitalize(), chance, stats)
    # (MECHC27; PATCH_CHECK K2-1 / K2-2 / K2-5 / K2-6, OVERCOUNTS: these fell to the one-per-4-s fallback below) the
    # event named BEFORE the gain in a skill's description: Killing Palm's "Each enemy killed by this strike grants a
    # Power Charge" (the kills: the boss preset's adds, 0 on a lone boss), Sniper's Mark's "The next Critical Hit the
    # target receives Activates the Mark, Consuming it to ... grant you a Frenzy Charge" (each activation needs a new Mark:
    # the lower of the main skill's crits and the build's Mark uses, stats["_mark_uses"]), Profane Ritual's "Mark a Corpse
    # ... the Corpse is consumed and you gain a Power Charge" (one corpse a gain: the preset's killed adds, one corpse each,
    # LOW, as models.corpse_supply; Sacrifice's own corpses aren't read here: an undercount for such a build) and Magma
    # Barrier's "your next Block with your Shield raised will expend the lava ... grant you an Endurance Charge" (MECHC36,
    # an OVERCOUNT before: every Block counted, uncapped: magma_barrier_rate, a Block with the shield RAISED after the
    # lava's charge-up)
    before = text[:m.start()].lower()
    full = text.lower()
    if (gain := (stats.get("_consume_gains") or {}).get(text)) is not None:
        # (MECHC38, GEM19's routed UNDERCOUNT) a skill's gain on a consume (Siphoning Strike's Power Charge when it
        # Consumes a Shock, Disengage's Frenzy Charge when it Consumes the Parried Debuff): its consumes a second, from the
        # assumptions' consumed share (Build.consume_gains: the last resolve's, a round behind, MEDIUM)
        return chance * gain[0], f"{chance:.0%} x {gain[1]}"
    if EVENT_GAIN.search(before):
        # (MECHC38) a gain after an event the line names that this reader has no rate for here (a consume by a skill that
        # isn't the main one, Frenzied Riposte's Parried consume, Wing Blast's Heavy Stun of a Primed enemy): 0, said
        # (before, "skill" in the description's last sentence read as an on-kill gain, or the one-per-4-s guess)
        return 0.0, (f"'{text[:70]}': a gain on an event not modelled for this skill here (a consume / a Heavy Stun "
                     "outside the main skill's own parts): 0, an UNDERCOUNT where it's used")
    if re.search(r"\bkill|\bcull", before):
        rate, how = kill_events(before, stats)
        return chance * rate, f"{chance:.0%} x {how}"
    if "activates the mark" in full and "critical hit" in full:
        marks = number(stats.get("_mark_uses"))
        rate = min(crits, marks)
        return chance * rate, (f"{chance:.0%} x {rate:.3f}/s: each Mark Activated by a crit (the main skill's {crits:.2f} "
                               f"crits/s) and re-applied ({marks:.3f} Mark uses/s: its group's rate, else the chosen "
                               f"press rate, else 0)")
    if "corpse" in full:
        kills = stats.get("_kills") or (0.0, 0.0, "")
        corpses = max(number(kills[0]), 0.0)
        return chance * corpses, (f"{chance:.0%} x {corpses:.3f} corpses/s (the boss preset's killed adds, one corpse "
                                  f"each, LOW; your own corpse sources not read: none on a lone boss)")
    if "with your shield raised" in full and "lava" in full:
        rate, how = magma_barrier_rate(stats, boss)
        return chance * rate, f"{chance:.0%} x {how}"
    if "block" in full:
        block = boss_hits.block_share(stats, evaded_first=False)
        return chance * boss * block, f"{chance:.0%} x {boss * block:.2f} blocks/s"
    return chance / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed (LOW)"


# Magma Barrier (MECHC36; PATCH_CHECK K2-6, an OVERCOUNT): "imbues your Shield with lava over time. When fully imbued,
# your next Block with your Shield raised will expend the lava to Trigger the Magma Spray skill and grant you an
# Endurance Charge" (PoB act_str.lua 13452: igneous_shield_charge_up_time_ms 8000, igneous_shield_charge_up_time_+% 0).
# Before, every Block counted (boss hits x Block chance), with no charge-up: now one gain per cycle of the 8 s charge-up
# plus the wait for the next Block made with the shield raised. Raised blocks a second: the boss's hits x the share of the
# fight the shield is up (stats["_raised_share"]: 1 for a main skill that holds it raised all fight, recovery.
# ACTIVE_BLOCK_CHANNELS; else Raise Shield taps a second x 0.55 s each; else 0), every Blockable hit Blocked while raised
# (as recovery.raised_block_taken), the waits taken as Poisson: rate = 1 / (T + 1 / raised blocks). No raised shield: 0
# (a play row: raise the shield to feed it). "Charge-up time" increases on the gem aren't read (an undercount where they
# shorten it; the gem's level stats aren't in the resolver), and whether the lava keeps filling while the shield is
# raised is taken as yes (UNVERIFIED).
MAGMA_CHARGE_UP = 8.0  # igneous_shield_charge_up_time_ms 8000 (PoB act_str.lua; the game's stat set): HIGH


def magma_barrier_rate(stats: dict, boss: float) -> tuple[float, str]:
    """(Endurance Charges a second from Magma Barrier, how): `boss` the boss's hits a second (the "when hit" pace)."""
    raised = min(max(number(stats.get("_raised_share")), 0.0), 1.0)
    blocks = boss * raised
    if blocks <= 0:
        return 0.0, ("0: Magma Barrier's lava is spent by a Block with your shield RAISED, and the build never raises "
                     "it (no channelled shield skill, no Raise Shield taps); before, every Block counted")
    cycle = MAGMA_CHARGE_UP + 1 / blocks
    return 1 / cycle, (f"one per {cycle:.1f} s: the lava's {MAGMA_CHARGE_UP:g} s charge-up, then the next of "
                       f"{blocks:.2f} raised-shield Blocks/s (the boss's {boss:.2f} hits/s x the shield raised "
                       f"{raised:.0%} of the fight; MEDIUM)")


# "Gain a Power Charge every Second if you haven't lost Power Charges Recently" (MECHC33; RESEARCH28 R28-5, the manager's
# 09:36 ruling; the Cultivated Redflare Conduit's UniqueMutatedVaalGainPowerChargesNotLostRecently, stat
# gain_power_charge_per_second_if_have_not_lost_power_charge_recently, PoB: no mod). Before, _charge_rate read it as a
# plain one a second: an OVERCOUNT wherever the charges are lost. "Recently" is the last 4 s (KeywordPopups Recently).
# - With "Lose all <kind> Charges on reaching maximum" (the same Redflare): after each loss only the build's other gains
#   (g a second) run for 4 s, then the line adds its own; the maximum M is reached every T = 4 + (M - 4 g) / (r + g)
#   seconds (r the line's rate), and the line runs (T - 4) / T of the cycle; with 4 g >= M the other gains refill it
#   inside the 4 s and the line never runs. (A cycle's gains sum to M, so _reaching_maximum's gains / M = 1 / T.)
# - Without it, a skill spending the charges (stats["_consumers"], evenly spaced uses): consumed charges taken as lost
#   (RESEARCH28 R28-13, power-charge-consumed-vs-lost.md: unsettled, leaning yes, MEDIUM-LOW: consuming removes charges,
#   GGG's 2011 diary and the game's "not remove Charges but still count as consuming them" wording; the lower reading of
#   a gain; the other reading, only "Lose" events count, is the note's row), so the line runs only in the
#   part of each gap between spends past 4 s: max(0, 1 - 4 x spends a second).
# - Nothing loses them: one a second, as before (it then only refreshes a held maximum).
NOT_LOST_RECENTLY = re.compile(r"if you haven't lost (\w+) charges recently", re.I)
RECENTLY_SECONDS = 4.0


def _while_not_lost(kind: str, rate: float, text: str, stats: dict) -> tuple[float, str]:
    """(charges per second, how) for a "<rate> if you haven't lost <kind> Charges Recently" line (the comment above)."""
    if stats.get("_not_lost"):
        return 0.0, "a 'not lost Recently' line inside another: not followed"
    texts = stats.get("_texts") or []
    inner = {**stats, "_not_lost": True, "_reaching": True}
    others = sum(r[0] for t in texts if t != text and (r := _charge_rate(t, kind, inner)) is not None)
    most = number(stats.get(f"{kind}ChargesMax"))
    spent = sum((stats.get("_consumers") or {}).get(kind, {}).values())
    wait = RECENTLY_SECONDS
    if most > 0 and any(re.search(rf"lose all {kind} charges on reaching maximum", t, re.I) for t in texts):
        if others * wait >= most:
            return 0.0, (f"your other {kind} gains ({others:.2f}/s) refill the maximum ({most:g}) within {wait:g} s of each "
                         f"loss (all lost on reaching it), so 'not lost Recently' never holds: 0 (without the loss: "
                         f"{rate:g}/s)")
        cycle = wait + (most - others * wait) / (rate + others)
        share = (cycle - wait) / cycle
        return rate * share, (f"{rate:g}/s only while no {kind} Charge was lost in the last {wait:g} s: all are lost on "
                              f"reaching the maximum ({most:g}), refilled every {cycle:.1f} s with your other gains "
                              f"({others:.2f}/s), so on {share:.0%} of the time (MEDIUM; without the loss: {rate:g}/s)")
    if spent > 0:
        share = max(0.0, 1 - wait * spent)
        return rate * share, (f"{rate:g}/s only while no {kind} Charge was lost in the last {wait:g} s: your skills spend "
                              f"{spent:.2f}/s (evenly spaced; a consumed charge taken as lost: RESEARCH28 R28-13, leaning yes, "
                              f"MEDIUM-LOW), so on {share:.0%} of the time (if only \"Lose\" events count: {rate:g}/s)")
    return rate, f"one every {1 / rate:g} s (nothing makes you lose {kind} Charges)" if rate > 0 else "none"


CHARGE_EVENT_FIELDS = ("HitChance", "IgniteChancePerHit", "PowerChargesMax", "PowerChargesDuration",
                       "FrenzyChargesMax", "EnduranceChargesMax")  # (_charge_rate: Attack Ignites, reaching a maximum)


def _reaching_maximum(kind: str, chance: float, stats: dict) -> tuple[float, str]:
    """(events per second, how) for "on reaching maximum <kind> Charges" (MECH48; before, one per 4 s). Gaining a charge
    refreshes them all, so a build whose gains keep it at the maximum reaches it once and stays: 0 after that. It is
    reached again only after charges go: "Lose all <kind> Charges on reaching maximum" (each refill of the maximum: gains
    / maximum), or a skill spending one per use while gains outrun it (each spend's refill: the spends per second, LOW:
    one charge per use assumed, as _charges_held)."""
    if stats.get("_reaching"):
        return 0.0, "a reaching-maximum line inside another: not followed"
    texts = stats.get("_texts") or []
    inner = {**stats, "_reaching": True}
    gains = sum(r[0] for t in texts if (r := _charge_rate(t, kind, inner)) is not None)
    most = number(stats.get(f"{kind}ChargesMax"))
    if gains <= 0 or most <= 0:
        return 0.0, f"no {kind} Charge gains against a boss (or no maximum): never reached"
    spent = sum((stats.get("_consumers") or {}).get(kind, {}).values())
    if any(re.search(rf"lose all {kind} charges on reaching maximum", t, re.I) for t in texts):
        return chance * gains / most, f"{chance:.0%} x {gains:.2f} gains/s / {most:g} (all lost on reaching it)"
    if spent and gains > spent:
        return chance * spent, f"{chance:.0%} x {spent:.2f}/s ({kind} Charges spent one per use, refilled by {gains:.2f} gains/s; LOW)"
    return 0.0, (f"{gains:.2f} {kind} gains/s, " + ("spent faster than made: the maximum isn't reached" if spent
                 else "nothing spends them: reached once, then held (0 per second)"))


# The boss's distance: PoB's Configuration "Distance to enemy" (10 units a metre). PoB compares its Presence radius with it
# (CalcPerform's EnemyInPresence; the game files' KeywordPopups Presence: a 4 m radius by default) and scales attack
# Accuracy by it (KeywordPopups Accuracy: no penalty within 2 m, up to 90% less beyond 9 m). PoB's placeholder of 2 m for
# every build put the boss in every build's Presence (an overcount for a build that stands back). The distance is a
# play-style assumption, labelled: a melee main skill stands at 2 m, and so does one whose damage comes from minions or
# totems (PoB reads the one distance for their Accuracy too, and they stand at the boss); any other at 5 m (the middle
# of the 4-6 m the mechanics catalog's plan names for ranged).
MELEE_DISTANCE = 2.0
RANGED_DISTANCE = 5.0
NEAR_TYPES = {"Minion", "CreatesMinion", "SummonsTotem", "UsedByTotem"}


# Lines whose effect depends on the boss's distance (PoB reads them against enemyDistance: ModParser's "enemies in your
# presence", "against enemies within / further than N m", Surrounded; the distance also sets attack Accuracy and the
# boss's own accuracy against your evasion, CalcDefence). A build with one has its distance chosen by score
# (Search.choose_distance): close range if it leans into Presence or close-range lines, the defence checks at the same
# distance since they're read from the same calculation.
DISTANCE_LINE = re.compile(r"\bin your presence\b|\bclose range\b|\bwithin \d+(?:\.\d+)? ?m|\bfurther than (\d+(?:\.\d+)?) ?m|"
                           r"\bsurrounded\b", re.I)

PRESENCE_LINE = re.compile(r"\bin your presence\b", re.I)
OUTSIDE_PRESENCE_LINE = re.compile(r"\bno enemies are in your presence\b|\boutside (?:of )?your presence\b", re.I)  # (MECH88)


def natural_distance(main_types: set[str], skill: str = "") -> float:
    """The distance in metres a build stands at when nothing it has depends on it. (MECH69; GEM2, PLAY_GUIDE_SPEC 1a) A
    shotgun main skill (models.SHOTGUN_SKILLS) stands at close range: its headline counts every projectile of a use on
    the boss at point-blank (the user's 9 Nov conditional rule), so the defence checks read the same distance."""
    if skill in models.SHOTGUN_SKILLS:
        return MELEE_DISTANCE
    return MELEE_DISTANCE if "Melee" in main_types or main_types & NEAR_TYPES else RANGED_DISTANCE


def distance_candidates(lines: list[str], main_types: set[str], presence_radius: float = 0.0,
                        skill: str = "") -> list[float]:
    """Distances worth a trial: the natural one first, close range, and just beyond any "further than N m" line. A build
    standing back with "in your Presence" lines also tries the edge of its Presence (presence_radius, metres), when that
    lies between close range and its natural distance: the boss's Accuracy against you falls with distance (CalcDefence;
    KeywordPopups Accuracy), so the build keeps the boss in its Presence as far away as it reaches, and Presence Area of
    Effect lines (PoB's PresenceArea, CalcPerform calcBuffRadius) decide how far that is (MECHC1, 12 Nov 2026: the user's
    Presence answer of 11 Nov, the player moves so the boss is in or out of the Presence as needed)."""
    natural = natural_distance(main_types, skill)
    found = [natural]
    reach = projectile_reach(lines, main_types)[0]
    for text in lines:
        if m := DISTANCE_LINE.search(text):
            found.append(MELEE_DISTANCE)
            if m.group(1) and (reach is None or float(m.group(1)) + 0.5 <= reach):  # (MECH86: within the arrows' reach)
                found.append(float(m.group(1)) + 0.5)
            edge = math.floor(presence_radius * 10) / 10
            if PRESENCE_LINE.search(text) and MELEE_DISTANCE < edge < natural:
                found.append(edge)
        # (MECH88, MECHC39's routed item: Lioneye's Glare's "if no enemies are in your Presence") a line that wants the
        # boss OUT of the Presence also tries just beyond its edge (0.5 m past it, as "further than N m"), within the
        # projectiles' reach, whether or not that's nearer than the natural distance (a wider Presence pushes it out)
        beyond = math.ceil(presence_radius * 10) / 10 + 0.5
        if (presence_radius > 0 and OUTSIDE_PRESENCE_LINE.search(text) and beyond > MELEE_DISTANCE
                and (reach is None or beyond <= reach)):
            found.append(MELEE_DISTANCE)  # (close range stays a candidate, as for any distance line)
            found.append(beyond)
    found = list(dict.fromkeys(found))
    return found if len(found) > 1 else []


# A shortened Projectile Range (MECH86; the manager's routed item, 17 Nov 03:27; RESEARCH29 R29-2,
# research/bow-projectile-range.md). "50% reduced Projectile Range" (projectile_attack_range_+% -50) is the implicit of the
# Artillery Bow and the Obliterator Bow (Fairgraves' Curse is an Artillery Bow) (checked by RESEARCH29: every exported
# table with a stat column, scratch/research29/rng2.py, finds no other Mods, PassiveSkills or stat-set user; poe2db's Bows
# page lists only these two bases with it). PoB reads no Projectile Range. The default arrow range is in no game file:
# R29-2 puts it at about 12-15 m (MEDIUM-LOW: PoE1's 120-150 units, and Fireball's / Firebolt's overrides of 12 / 10 m
# shortening it), so half is 6-7.5 m. The lower end, 12 m, is taken (the cautious reading: at the upper one, 15 m, a
# half-range arrow reaches 7.5 m). The search's distance trial "just beyond a 'further than N m' line" (N + 0.5 m, so
# 6.5 m for the "further than 6m" lines) is tried only within that reach for a Projectile main skill; out of reach its
# hits would score 0 there, so the trial isn't offered (the natural 5 m and close range stay within reach). Whether the
# line scales a skill's own range override (Tornado Shot's nova, Escape Shot) and whether it reaches ground-targeted
# arrows (Rain of Arrows) are UNVERIFIED (R29-2): the same reach is used for every Projectile main skill. RESEARCH30
# R30-8 (3) (mech86-open-questions.md) found no source either way (the overrides are separate stats: Tornado Shot's
# tornado_shot_projectile_range 6 m, Escape Shot's projectile_distance_override 2 m) and no number moves for a boss at
# the tornado or within 5 m: still UNVERIFIED, not modelled.
PROJECTILE_BASE_RANGE = 12.0  # m (RESEARCH29 R29-2: about 12-15 m, MEDIUM-LOW; the lower end)
PROJECTILE_RANGE_LINE = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Projectile Range$", re.I)


def projectile_reach(lines: list[str], main_types: set[str]) -> tuple[float | None, str]:
    """(metres the main skill's projectiles reach, how), or (None, "") when no Projectile Range line changes it or the main
    skill isn't a Projectile skill (the note above)."""
    if "Projectile" not in (main_types or set()):
        return None, ""
    inc = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
              for t in lines if (m := PROJECTILE_RANGE_LINE.match(str(t).strip())))
    if inc == 0:
        return None, ""
    reach = PROJECTILE_BASE_RANGE * max(1 + inc / 100, 0.0)
    return reach, (f"your projectiles reach about {reach:g} m ({inc:+g}% Projectile Range on a base of "
                   f"{PROJECTILE_BASE_RANGE:g} m, RESEARCH29 R29-2, MEDIUM-LOW: the base is about 12-15 m, so "
                   f"{PROJECTILE_BASE_RANGE * 1.25 * max(1 + inc / 100, 0.0):g} m at its upper end)")


# Lines that depend on standing still (PoB's conditionStationary). A build with one has standing still chosen by score
# (Search.choose_stationary), unless its dodge rolls keep it moving more than 1 - UPTIME of the time.
STATIONARY_LINE = re.compile(r"\bstationary\b|\bstand(?:ing)? still\b", re.I)


# Moving (MECH86; the manager's 22:10 routing of COV40's rows; the catalog's moving-stationary entry). PoB's
# Condition:Moving (ConfigOptions 133, "Are you always moving?") is what every "while moving" line reads (ModCache:
# "N% increased Attack Damage / Evasion Rating / Mana Regeneration Rate / Accuracy Rating ... while moving", Condition
# var "Moving"), and nothing of ours set it (checked by COV40 and the manager: no conditionMoving / Condition:Moving in
# builder/ or engine/), so all of them counted 0: an UNDERCOUNT of the gains and an OVERCOUNT where the line is a
# drawback (Spray and Pray, slow_mitigation15: attack_damage_+%_while_moving with accuracy_rating_+%_while_moving -20 and a
# smaller movement penalty while performing actions, PassiveSkills 26650308). In PoE2 a player can walk while using skills
# (at a movement-speed penalty: "Movement Speed Penalty from using Skills while moving", which PoB parses unconditionally),
# so moving is the player's play: a build that takes "while moving" lines walks while it attacks or casts, all fight, if
# that scores better (Search's choice, conditions.moving: the full score, defence floors included, as choose_stationary
# does), and then it isn't standing still; choose_stationary's "moving scored better" means the same play. Otherwise the
# box is off: the player stands while using skills, and its dodge rolls (Search.rolling_share) are moving time with no hits
# in them, so the damage and Accuracy lines lose nothing there; the time-based ones (Evasion, regeneration) during rolls
# aren't counted (an undercount, LOW). The penalty to movement speed isn't scored (no number of ours reads it).
# PoB's own second source, "You and Enemies in your Presence count as Moving while affected by Elemental Ailments"
# (ModParser 2494-2497: Condition:Moving under your own Frozen / Chilled / Shocked / Ignited conditions, and the same on
# the enemy), is PoB's: it switches on with the self-ailment boxes the resolver sets (conditionIgnited, conditionShocked,
# ...), so it needs nothing here.
MOVING_LINE = re.compile(r"\bwhile moving\b", re.I)
MOVING_NOT = re.compile(r"movement speed penalty", re.I)  # (parsed without the condition: no reason to move)


def moving_candidates(texts: list[str]) -> bool:
    """Whether walking while using skills is worth a trial for this build: it has a "while moving" line PoB reads under
    Condition:Moving (the note above)."""
    return any(MOVING_LINE.search(t) and not MOVING_NOT.search(t) for t in texts)


def _moving(lines: list[str], stats: dict) -> tuple[object, str]:
    if not moving_candidates(lines):
        return 0.0, ""
    if stats.get("_moving") or stats.get("_stationary_off") == "moving scored better":
        return True, ("walk while attacking or casting, all fight (a play choice the search keeps while it scores better, "
                      "defence floors included; without it: standing while using skills, these lines off)")
    rolling = number(stats.get("_rolling"))
    return 0.0, ("standing while using skills scores better (the search's choice)"
                 + (f"; the dodge rolls ({rolling:.0%} of the fight) move you but carry no hits: the time-based "
                    "'while moving' lines during them aren't counted (LOW)" if rolling else ""))


LOW_LIFE_LINE = re.compile(r"low life", re.I)  # (the player's own Low Life lines match too: the boss's then changes nothing)
LOW_LIFE_SHARE = 0.35  # "A player or monster is on Low Life if it has 35% of its Maximum Life or less" (KeywordPopups)


def _standing_still(lines: list[str], stats: dict) -> tuple[float, str]:
    """On for a build with a stationary line, unless Search.choose_stationary switched it off (its dodge rolls, or moving
    scored better): a real trial of a new stationary line sees its gain."""
    if stats.get("_stationary_off") or stats.get("_moving") or not any(STATIONARY_LINE.search(t) for t in lines):
        return 0.0, ""  # (MECH86: a build walking while it uses skills isn't standing still)
    rolling = number(stats.get("_rolling"))
    return True, ("stand still while attacking or casting (the build has stationary lines; a play-style choice, kept while "
                  "it scores better than moving, sustain and defence floors included)"
                  + (f"; dodge rolls take {rolling:.0%} of the fight, under the {1 - UPTIME:.0%} that still counts as always"
                     if rolling else ""))


def _boss_distance(lines: list[str], stats: dict) -> tuple[float, str]:
    types = stats.get("_main_types") or set()
    chosen = stats.get("_distance_choice")
    if chosen:
        metres, why = chosen
        radius = number(stats.get("PresenceRadius")) / 10
        where = "in" if radius >= metres else "outside"
        return metres * 10, f"{metres:g} m: {why}; your Presence radius is {radius:.1f} m, so the boss is {where} it"
    if "Melee" in types:
        metres, why = MELEE_DISTANCE, "a melee main skill"
    elif types & NEAR_TYPES:
        metres, why = MELEE_DISTANCE, ("minions or totems deal the main skill's damage at the boss (PoB reads the one "
                                       "distance for their Accuracy too), so you're assumed as close")
    else:
        metres, why = RANGED_DISTANCE, "assumed for a non-melee main skill (attacks lose Accuracy beyond 2 m)"
    radius = number(stats.get("PresenceRadius")) / 10
    where = "in" if radius >= metres else "outside"
    return metres * 10, f"{metres:g} m: {why}; your Presence radius is {radius:.1f} m, so the boss is {where} it"


# Extra charges per gain (MECH44, 11 Nov 2026; before, the ledger's no-effect-extra-charge-chance dropped them while the
# count above was rate x duration). Game files (stat_descriptions.csd, build 26476662), every stat with "1_more" or
# "charge_gained" and "charge" in its id and where Mods, PassiveSkills, SoulCoreStats and stat sets use it:
# - chance_to_gain_1_more_{power,frenzy,endurance,}charge_%: "{0}% chance when you gain a <K> Charge to gain an additional
#   <K> Charge" (100: "Gain an additional ... when you gain ..."); passives power_charges7, frenzy_charges7_,
#   endurance_charges9, power_endurance_charges3/6, AscendancyRanger1Notable5_, AscendancyMonk1Small6; soul cores; the
#   unique UniqueAdditionalChargeGeneration1 (any Charge); Charge Profusion I/II (30%, on the supported skill's charges).
#   Counted: the gain rate x (1 + the summed chance, at most 1 extra per gain).
# - chance_to_gain_1_more_random_charge_% ("... an additional random Charge when you gain a Charge"; Charge Profusion II
#   15%, a mutated Vaal unique): a third of it to each of Power, Frenzy and Endurance (assumed, labelled: the random
#   pick's pool isn't in the line).
# - ..._per_10_tribute: Tribute isn't modelled: labelled, not counted.
# - gain_maximum_<K>_charges_on_<K>_charge_gained_%_chance: "{0}% chance that if you would gain <K> Charges, you instead
#   gain up to your maximum" (passives oracle_<k>_charge1-4, mods ChargeBonusChanceToGainMaximum*, unique
#   ChanceToGainMaximumPowerChargesUnique__1_). A fill holds the maximum until the charges drop (charges_with_refresh).
#   With a consumer of the charge it's labelled, not counted (what the consumer leaves of a fill isn't modelled).
EXTRA_CHARGE = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance when you gain an? (?:(Power|Frenzy|Endurance) )?Charge to gain an "
                          r"additional (?:\w+ )?Charge|Gain an additional (?:(Power|Frenzy|Endurance) )?Charge when you gain "
                          r"an? (?:\w+ )?Charge)(.*)$", re.I)
EXTRA_RANDOM_CHARGE = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance to gain|Gain) an additional random Charge when you gain a "
                                 r"Charge(.*)$", re.I)
FILL_CHARGES = re.compile(r"(\d+(?:\.\d+)?)% chance that if you would gain (?:(Power|Frenzy|Endurance) )?Charges, you instead"
                          r"\s+gain up to\s+(?:your )?maximum", re.I)
# Charge Profusion I / II (GrantedEffectStatSets SupportChargeProfusionPlayer / ...Two: chance_to_gain_1_more_charge_% 30,
# ...Two also chance_to_gain_1_more_random_charge_% 15): on the charges of the skills it supports.
CHARGE_PROFUSION = {"Charge Profusion I": (0.30, 0.0), "Charge Profusion II": (0.30, 0.15)}


def _extra_charges(lines: list[str], kind: str) -> tuple[float, float, list[str]]:
    """(extra charges per gain, chance a gain fills to the maximum, how) from the build's lines."""
    extra, fill, how = 0.0, 0.0, []
    for text in lines:
        if (m := EXTRA_CHARGE.search(text)) and (m.group(2) or m.group(3) or kind).lower() == kind.lower():
            if "tribute" in m.group(4).lower():
                how.append(f"'{text[:50]}': Tribute isn't modelled, not counted")
                continue
            chance = float(m.group(1)) / 100 if m.group(1) else 1.0
            extra += chance
            how.append(f"{chance:.0%} extra per gain")
        elif m := EXTRA_RANDOM_CHARGE.search(text):
            if "tribute" in m.group(2).lower():
                how.append(f"'{text[:50]}': Tribute isn't modelled, not counted")
                continue
            chance = (float(m.group(1)) / 100 if m.group(1) else 1.0) / 3
            extra += chance
            how.append(f"{chance:.0%} random extra per gain (a third of the pick assumed {kind})")
        elif (m := FILL_CHARGES.search(text)) and (m.group(2) or kind).lower() == kind.lower():
            fill = 1 - (1 - fill) * (1 - float(m.group(1)) / 100)
    return min(extra, 1.0), fill, how


def _extra_charges_before_resonance(lines: list[str], kind: str, by_kind: dict, gains: float) -> tuple[float, float, list[str]]:
    """_extra_charges for `kind`'s gains when Resonance feeds it from other kinds (PATCH_CHECK G4-2, MECHC29). Patch 0.4.0
    (bug fixes): "Chances to gain additional charges now only rolls before effects that change the type of charge gained
    (such as Resonance keystone) - previously could roll twice". So a gain's extra-charge chance is its source's ORIGINAL
    kind's ("additional Frenzy Charge" for a Frenzy source Resonance turns into Power), not the final kind's: each
    original kind's chance weighted by its share of the gains. Before, the final kind's lines were read for every gain:
    an OVERCOUNT where only the final kind has extras, an UNDERCOUNT where only the original has. The "fill to the
    maximum" lines ("if you would gain <K> Charges, you instead gain up to your maximum") aren't "additional charges":
    still read for the final kind (UNVERIFIED which kind they key on under Resonance; the note names only extras)."""
    extra, fill, how = _extra_charges(lines, kind)
    moved = {k: g for k, g in by_kind.items() if k != kind and g > 0}
    if not moved or gains <= 0:
        return extra, fill, how
    total, how = 0.0, []
    for k, g in by_kind.items():
        e, _, h = _extra_charges(lines, k)
        total += g / gains * e
        if h:
            how.append(f"{k} sources {g / gains:.0%} of gains: {', '.join(h)}"
                       + (" (rolled as {0} before Resonance, patch 0.4.0)".format(k) if k != kind else ""))
    return min(total, 1.0), fill, how


def charges_with_refresh(gains: float, duration: float, most: float, extra: float = 0.0, fill: float = 0.0) -> float:
    """Charges held on average, from gains per second (MECH44, 11 Nov 2026).

    The game files' KeywordPopups Charges (build 26476662): "Charges last for 15 seconds by default, refreshing whenever
    you gain another charge of the same type." So all of a kind drop together when no gain comes for one duration, and
    the count is the gains since the last such gap, at most the maximum. Gains are taken as random (Poisson) arrivals
    (assumed, labelled): a gap longer than the duration comes before any gain with chance 1 - s, s = 1 - e^(-gains x
    duration), so the gains in the current run N have P(N = n) = (1 - s) s^n. Each gain brings one charge, plus one more
    at chance `extra`; at chance `fill` it brings the maximum instead, held for the rest of the run."""
    top = int(round(most))
    if top <= 0 or gains <= 0 or duration <= 0:
        return 0.0
    s = 1 - math.exp(-gains * duration)
    held = s ** top * top  # (N >= top: at the maximum)
    for n in range(1, top):
        below = sum(math.comb(n, k) * extra ** k * (1 - extra) ** (n - k) * min(top, n + k) for k in range(n + 1))
        kept = (1 - fill) ** n
        held += (1 - s) * s ** n * ((1 - kept) * top + kept * below)
    return min(held, most)


# "Lose all <kind> Charges on reaching maximum <kind> Charges" (MECHC14; COV18, an OVERCOUNT: Redflare Conduit's Power
# Charges; PoB parses the line to no mod, ModCache {nil, ...}; before, the line was read only for how often the maximum is
# reached, _reaching_maximum, and the charges held were counted as if the build kept the maximum)
# "Skills have N% chance to not remove Charges but still count as consuming them" (MECHC32; COV37 via the manager, 16 Nov
# 09:09: nothing read it; PoB parses it only in part, ModCache 9390 with leftover text, so it adds nothing): a consumer's
# use still counts as consuming (its effect, and "on consuming a Charge" recovery, happen) but the charge stays on that
# chance. So the charges removed a second are the spends x (1 - chance); the uses that find a charge rise to
# min(1, made / removed). Read for Power, Frenzy and Endurance Charges alike ("Charges" names no kind); the chance summed
# over the build's lines, at most 100%. (MEDIUM: the wording; the Elemental Infusion line is another stat, not read here.)
CHARGES_KEPT = re.compile(r"^Skills have (\d+(?:\.\d+)?)% chance to not remove Charges but still count as consuming them$", re.I)


def charges_kept_chance(lines: list[str]) -> float:
    """The chance a consumed Charge isn't removed (CHARGES_KEPT lines, summed, at most 1)."""
    return min(sum(float(m.group(1)) for t in lines if (m := CHARGES_KEPT.match(t.strip()))), 100.0) / 100


CHARGE_RESET_AT_MAX = re.compile(r"\blose all (power|frenzy|endurance) charges on reaching maximum \1 charges\b", re.I)


def charges_with_reset(gains: float, duration: float, most: float, extra: float = 0.0, fill: float = 0.0) -> float:
    """Charges held on average when all are lost on reaching the maximum (MECHC14). Between losses the count climbs one
    gain at a time: n gains since the last loss hold n x (1 + extra) charges, and the gain that would reach the maximum
    (after N = ceil(maximum / (1 + extra)) gains, or any gain at chance `fill` that fills to it) drops them to 0, held for
    an instant. Charges also all drop when no gain comes for `duration` s (charges_with_refresh's rule; gains taken as
    random arrivals, assumed). With p = 1 - e^(-gains x duration) and a = 1 - fill, the time spent at n gains (n >= 1) is
    in proportion to (a p)^n, at 0 to 1, so the average is sum n (1 + extra) (a p)^n / (1 + sum (a p)^n), n = 1 .. N - 1
    (= (N - 1) / 2 x (1 + extra) when nothing expires and nothing fills)."""
    top = int(round(most))
    if top <= 0 or gains <= 0 or duration <= 0:
        return 0.0
    per = 1.0 + max(min(extra, 1.0), 0.0)
    steps = max(1, math.ceil(top / per - 1e-9))
    c = (1 - min(max(fill, 0.0), 1.0)) * (1 - math.exp(-gains * duration))
    weights = [c ** n for n in range(1, steps)]
    held = sum(n * per * w for n, w in zip(range(1, steps), weights)) / (1 + sum(weights))
    return min(held, most)


# Charges gained as another kind, or by allies instead (MECHC2, FIX_QUEUE: nothing read either keystone, so the player
# kept charges of the original kind: an overcount). Resonance (passive_keystone_resonance, KeywordPopups ChargeCycle):
# "Gain Power Charges instead of Frenzy Charges / Frenzy instead of Endurance / Endurance instead of Power": a source of
# one kind feeds the next (one step: the cycle can't chain, assumed). Conduit (passive_keystone_conduit, KeywordPopups
# Conduit): "If you would gain a Charge, Allies in your Presence gain that Charge instead": the player holds none (and the
# one-kind wording, "If you would gain an Endurance Charge, ...", ModCache, for that kind). (checked: PoB's ModCache leaves
# all of these unparsed, and nothing in builder/search read them; the tree's text is PoB tree.json 0_5)
CHARGE_KINDS = ("Power", "Frenzy", "Endurance")
CHARGE_INSTEAD = re.compile(r"\bgain (power|frenzy|endurance) charges instead of (power|frenzy|endurance) charges\b", re.I)
CHARGE_TO_ALLIES = re.compile(r"\bif you would gain an? ?(power|frenzy|endurance)? charge, allies\b[^.;]*\bgain that charge "
                              r"instead", re.I)
CHARGE_REDIRECTED = "gained instead of"  # (in a basis: the use box goes on from the redirected sources)
# Conduit with a share-back (MECHC31; RESEARCH26 R26-46 (a), research20-in-game-checks-r26.md: an UNDERCOUNT, the player
# held 0 under Conduit): "Allies in your Presence share Charges with you" (allies_in_your_presence_share_charges_with_you:
# the Idol of Eramir Soul Core, Sceptres) means "when A gains a charge, B will also gain a charge" (Mark_GGG's ruling on
# the same PoE1 wording, SETTLED as the rule, MEDIUM), one way. So with Conduit, a gain you'd have made goes to the allies
# in your Presence, and each that gains it gives you a copy: you hold the charges your sources make, as without
# Conduit, once you have an ally (a minion skill in the build: the ally must be able to hold the charge; a minion holds
# 3 of each, Monster.ot). One copy per gain, not one per minion (ASSUMED, LOW-MEDIUM); the 0 without the share-back is
# the other figure. Conduit with no ally: no charge (RESEARCH26 R26-21 (e), ASSUMED MEDIUM-LOW: "instead" sends the gain
# to the allies, and you aren't your own Ally; plans2-in-game-checks-r26.md).
CHARGE_SHARE_BACK =re.compile(r"\ballies in your presence share charges with you\b", re.I)


def _charge_sources(lines: list[str], kind: str) -> tuple[list[str], str]:
    """The kinds whose sources feed `kind` (itself unless redirected away; others redirected to it), and how."""
    given = {}
    for text in lines:
        if m := CHARGE_INSTEAD.search(text):
            given[m.group(2).capitalize()] = m.group(1).capitalize()
    kinds = [k for k in CHARGE_KINDS if given.get(k, k) == kind]
    moved = [k for k in kinds if k != kind]
    how = []
    if moved:
        how.append(f"{kind} Charges {CHARGE_REDIRECTED} {', '.join(moved)} Charges (Resonance)")
    if given.get(kind, kind) != kind:
        how.append(f"{kind} Charge sources give {given[kind]} Charges instead (Resonance)")
    return kinds, "; ".join(how)


# "(N% chance to) Steal Power, Frenzy, and Endurance Charges on Hit" (StealChargesOnHitPercentUnique__1 100%,
# StealChargesOnHitPercentUniqueGlovesStrDex6 10%; PoB leaves it unparsed): the boss presets hold no Charges to steal, so 0,
# said (MECHC7; RESEARCH19 #107, boss-charges-steal.md, MEDIUM-HIGH: no mod, skill or .ot gives a target boss any; the
# .ao / .ais files weren't read).
STEAL_CHARGES = re.compile(r"\bsteal power, frenzy,? and endurance charges on hit", re.I)
STEAL_NOTE = ("the boss holds no Charges to steal (research boss-charges-steal.md: no mod, skill or monster file gives a "
              "pinnacle boss any; MEDIUM-HIGH)")


def _charges_held(kind: str):
    def held(lines: list[str], stats: dict) -> tuple[float, str]:
        kinds, redirected = _charge_sources(lines, kind)
        allies = next((t for t in lines if (m := CHARGE_TO_ALLIES.search(t))
                       and (m.group(1) is None or m.group(1).capitalize() == kind)), None)
        share_back = next((t for t in lines if CHARGE_SHARE_BACK.search(t)), None)
        if allies is not None and not (share_back and stats.get("_has_ally")):  # (a basis with 0 switches the box off)
            sourced = any(_charge_rate(t, k, stats) is not None for t in lines for k in {*kinds, kind})
            why = (" ('" + share_back + "' needs an ally in your Presence: the build has no minion skill)") if share_back else ""
            return 0.0, (f"'{allies}': allies in your Presence gain the {kind} Charges instead (Conduit){why}"
                         if sourced else "")
        shared_note = (f"; Conduit gives your {kind} Charges to your minions and '{share_back}' gives you a copy of each "
                       "(RESEARCH26 R26-46: one copy per gain, ASSUMED LOW-MEDIUM; 0 without the share-back)"
                       if allies is not None else "")
        gains, bonus, how = 0.0, 0.0, []
        by_kind = {}  # (G4-2: gains per the kind its source names, before Resonance moves it)
        profusion = stats.get("_profusion") or {}  # gem description -> (chance of one more, of a random one)
        for text in lines:
            if (hit := next(((k, x) for k in kinds if (x := _charge_rate(text, k, stats)) is not None), None)) is not None:
                source, r = hit
                more = profusion.get(text)
                plus = (more[0] + more[1] / 3) if more else 0.0
                gains += r[0]
                by_kind[source] = by_kind.get(source, 0.0) + r[0]
                bonus += r[0] * plus
                how.append(f"{text[:60]} ({r[1]}" + (f"; Charge Profusion +{plus:.0%} per gain" if more else "") + ")")
        steal = next((t for t in lines if STEAL_CHARGES.search(t)), None)
        if steal is not None and how:  # (MECHC7, #107: counted 0, said)
            how.append(f"'{steal[:60]}': 0, {STEAL_NOTE}")
        if not how:  # (its own sources feeding another kind: Resonance; a basis with 0 switches the use box off)
            own = redirected and any(_charge_rate(t, kind, stats) is not None for t in lines)
            if steal is not None and not own:
                return 0.0, f"'{steal[:60]}': 0 {kind} Charges, {STEAL_NOTE}"
            return 0.0, (redirected if own else "")
        extra, fill, extra_how = _extra_charges_before_resonance(lines, kind, by_kind, gains)
        if bonus:
            extra_how.append(f"Charge Profusion {bonus / gains:.0%} of gains")
        extra = min(1.0, extra + (bonus / gains if gains else 0.0))
        rate = gains * (1 + extra)
        most = number(stats.get(f"{kind}ChargesMax"))
        duration = number(stats.get(f"{kind}ChargesDuration")) or 15.0
        # Skills that consume this charge per use (PoB skill types SkillConsumes<Kind>ChargesOnUse; one per use assumed):
        # what they spend isn't held. A consumer finds a charge on a share generation / consumption of its uses. (Beside
        # a consumer the count stays the older rate left x duration, at most the maximum: how consumption and the shared
        # refresh interact isn't modelled.)
        spent = sum((stats.get("_consumers") or {}).get(kind, {}).values())
        extra_text = f" x {1 + extra:.2f} per gain ({', '.join(extra_how)})" if extra_how else ""
        if spent:
            kept = charges_kept_chance(lines)  # (MECHC32: consumed but not removed on that chance)
            removed = spent * (1 - kept)
            net = max(rate - removed, 0.0)
            count = min(most, net * duration)
            share = min(1.0, rate / removed) if removed > 0 else 1.0
            # (MECHC13: the charges consumed a second, for recovery on consuming one: Conditions.evidence puts them on
            # build.charge_flow)
            stats.setdefault("_charge_flow", {})[kind] = (
                spent * share, f"{rate:.2f}/s made, {spent:.2f}/s wanted by " + ", ".join(stats["_consumers"][kind]))
            basis = (f"{gains:.2f} gains/s{extra_text} = {rate:.2f}/s made, {spent:.2f}/s spent by " +
                     ", ".join(stats["_consumers"][kind]) +
                     f" (one per use; {share:.0%} of their uses find a charge), {net:.2f}/s left x {duration:g} s")
            if kept:
                basis += (f"; {kept:.0%} chance a consumed charge isn't removed (it still counts as consumed): "
                          f"{removed:.2f}/s removed (MEDIUM); without it: {max(rate - spent, 0.0):.2f}/s left")
            if count <= 0 and share >= 0.5:
                count = 0.1  # (held for an instant before it's spent: enough for PoB to count the charged use)
            if fill:
                basis += f"; {fill:.0%} chance a gain fills to the maximum: not counted beside a consumer of the charge"
        else:
            count = charges_with_refresh(gains, duration, most, extra, fill)
            basis = (f"{gains:.2f} gains/s{extra_text}, all dropping after {duration:g} s without a gain (gains taken as "
                     f"random arrivals)" + (f"; {fill:.0%} chance a gain fills to the maximum" if fill else ""))
        reset = next((t for t in lines if (m := CHARGE_RESET_AT_MAX.search(t)) and m.group(1).capitalize() == kind), None)
        if reset is not None:
            # (MECHC14: climbs to the maximum and drops to 0; beside a consumer, at the gains left after its spends)
            left = gains * (max(rate - spent, 0.0) / rate if spent and rate > 0 else 1.0)
            cycling = charges_with_reset(left, duration, most, extra, fill)
            if cycling < count:
                count = cycling
                basis += (f"; '{reset[:70]}': the count climbs to {most:g} and drops to 0, so {cycling:.1f} held on "
                          f"average ({left:.2f} gains/s kept)")
        return round(count, 1), ((redirected + "; ") if redirected else "") + (
            f"{basis} = {count:.1f} held, at most {most:g}: " + "; ".join(how[:3]) + shared_note)
    return held


CHARGE_OVERRIDES = {f"override{kind}Charges": kind for kind in ("Power", "Frenzy", "Endurance")}


# Rage held (MECH27, 9 Nov 2026; before, any Rage source set the maximum, on-kill ones too). The game files' KeywordPopups
# Rage (build 26476662): "you have 30 maximum Rage and lose 1 Rage every 0.2 seconds. Rage loss is paused for 4 seconds
# upon gaining Rage, or after taking damage. Only one Hit every 0.5 seconds can cause you to gain Rage." Gains come from
# the build's lines (every "gain # Rage" wording of the stat descriptions; on-kill gains at the build's kills, kill_events,
# MECHC9: none against a lone boss; a kill isn't a Hit, so the one-Hit-per-0.5 s rule doesn't cap them), spends from the skills that spend it (GrantedEffectStatSets / GrantedEffectsPerLevel costs below). The maximum
# is held while gains outrun spends and gains or the boss's landing hits come at least once per 4 s; else the average of
# each gain held 4 s and then decaying. A source whose rate its line doesn't say (Warcry, Life Flask, on use) is assumed
# once per CHARGE_DEFAULT_EVERY s, labelled. From the build's own rates (MECHC1, 12 Nov 2026; FLAT_RATES A3 and A10):
# "after Spending a total of N Mana" at the main skill's Mana spent per second (PoB ManaPerSecondCost; other skills'
# Mana isn't counted: an undercount for builds that press others), not a Hit, so the one-Hit-per-0.5 s rule doesn't
# cap it; "1 Rage Regenerated for every N Mana Regeneration per Second" from PoB's ManaRegenRecovery; "Regenerate X Rage
# per second per Y Rage spent Recently" (Primal Hunger; before, dropped) as X x 4 s / Y of the Rage the build spends,
# taken as steady (its 4 s lag isn't modelled) and capped by the spend.
RAGE_PAUSE, RAGE_DECAY, RAGE_HIT_EVERY = 4.0, 5.0, 0.5
RAGE_NO_LOSS = re.compile(r"^No Inherent loss of Rage(?: during effect)?$", re.I)  # Blood of the Warrior (MECHC4)
# Rage lost all at once (MECHC11; COVE11's two OVERCOUNTS: PoB maps the Rage held and none of these losses):
# - Prototype Seventeen: lose_%_of_maximum_rage_on_dealing_critical_strike 100 with the supported skill (its stat set
#   SupportPrototypeSeventeenPlayer, with 20% increased crit chance per 5 Rage; checked: the stat over every list column
#   of GrantedEffectStatSets, Mods' Stat1-6 and PassiveSkills' Stats, game 26476662: no other carrier). Read from the
#   main skill's supports (RAGE_LOST_ON_CRIT_STAT). The Rage between crits: crits come at random (C/s), so the time
#   since the last one is exponential and the Rage held averages (G / C) (1 - e^(-C M / G)) for net gain G, maximum M
#   (the inherent loss inside a gap not taken off: an upper bound). Crits read at the Rage PoB held when the stats were
#   read (MEDIUM: fewer crits at less Rage, so a little more Rage). On a skill other than the main one: not read (its
#   crit rate isn't known; labelled in the stat's absence, an overcount left only there).
# - "Lose all Rage on reaching Maximum Rage" (UniqueLoseRageOnMaximumRage1, HandWrapsUniqueLoseRageOnMaximumRage1;
#   PoB parses it to nothing, ModCache): a build that would reach the maximum climbs 0 -> M and drops back, M / 2 on
#   average (gains taken as steady, LOW: lumpy gains of a large share of M sit lower); one that never reaches it keeps
#   its level. The line's "Gain a random Charge on reaching Maximum Rage, no more than once every 6 seconds" isn't
#   counted (an undercount).
RAGE_LOST_ON_CRIT_STAT = "lose_%_of_maximum_rage_on_dealing_critical_strike"
RAGE_RESET_AT_MAX = re.compile(r"\blose all rage on reaching maximum rage\b", re.I)
RAGE_FILL_ON_USE = re.compile(r"^Grants up to your maximum Rage on use$", re.I)  # (MECHC38: Ngamahu's Chosen)
RAGE_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\bgain (\d+|an?)? ?Rage\b(.*)$", re.I)
RAGE_REGEN = re.compile(r"\bregenerate (\d+(?:\.\d+)?)(%)?(?: of your maximum)? Rage per second(.*)$|"
                        r"\b1 Rage Regenerated for every", re.I)
RAGE_PER_SPENT = re.compile(r"\bregenerate (\d+(?:\.\d+)?) Rage per second per (\d+) Rage spent Recently", re.I)
RAGE_NOT_SOURCE = re.compile(r"cannot gain|instead|\ballies\b|companions|benefits|per (?:\d+ )?Rage|when you lose|"
                             r"per enemy power|if you have gained", re.I)
# Rage spent per use (molten_crash_optionally_consume_X_rage_for_fissures 10, bear_slam_spend_X_rage_for_stronger_slam 10,
# support_rageforged_consumes_X_rage_to_enrage_skill_use 10; Fury of the Mountain's own amount isn't in its stat set: 10
# assumed like the others) and per second while channelled (CostAmounts: Rampage 300, Flame Breath 360 Rage a minute;
# both "skill_cannot_gain_rage_during_use", so the main skill's own hits gain none while it's one of them).
# (MECHC24) Rageforged II also holds rage_skip_consume_chance_% 30 (GrantedEffectStatSets SupportRageforgedPlayerTwo;
# gem_stat_descriptions: "Supported Skills have 30% chance to gain the benefits of this Support without spending
# additional Rage"; poe2db Rageforged II: the same line, not marked deprecated): its own 10 Rage is spent on 70% of uses,
# 7 a use on average. The line's scope is the Support's own spend, so a supported Molten Crash's or Furious Slam's own
# spend isn't lowered. Its only carrier (checked: every table row whose stat columns hold the stat's Stats.json row,
# 22272: Mods, PassiveSkills, SoulCoreStats, GrantedEffectStatSets and the per-level tables; one hit, this support;
# the patch-notes archive has no line naming the chance, only 0.4.0's "Enraged Warcry and Rageforged Supports are now
# treated as spending Rage", which no hit doesn't prove absent).
RAGE_SKIP_CHANCE = {"Rageforged II": 0.30}
RAGE_PER_USE = {"Molten Crash": 10, "Furious Slam": 10, "Fury of the Mountain": 10, "Rageforged I": 10,
                "Rageforged II": 10 * (1 - RAGE_SKIP_CHANCE["Rageforged II"])}
RAGE_PER_SECOND = {"Rampage": 5.0, "Flame Breath": 6.0}


def _rage_gain(text: str, stats: dict, own_hits: bool) -> tuple[float, float, str] | None:
    """(Rage per second, gains per second, how) from one line, or None if it isn't a source of Rage."""
    if m := RAGE_REGEN.search(text):  # (Rage regenerated counted as gained for the pause, assumed)
        if re.search(r"per \d+ Rage spent|\ballies\b", text, re.I):
            return None
        if per := re.search(r"for every (\d+(?:\.\d+)?) Mana Regeneration per Second", text, re.I):
            regen = number(stats.get("ManaRegenRecovery"))
            r = regen / float(per.group(1))
            return r, 1.0 if r > 0 else 0.0, f"{r:.2f}/s regenerated from {regen:.1f} Mana Regeneration per second"
        if not m.group(1) or "for every" in (m.group(3) or ""):
            return 1.0, 1.0, "regenerated at a rate set by Life or Mana regeneration: 1/s assumed (LOW)"
        r = float(m.group(1)) * (number(stats.get("MaximumRage")) / 100 if m.group(2) else 1.0)
        return r, 1.0, f"{r:g}/s regenerated"
    if RAGE_FILL_ON_USE.match(text.strip()):
        # (MECHC38; COV40's routed UNDERCOUNT) Ngamahu's Chosen, "Grants up to your maximum Rage on use" (PoB parses it to
        # nothing; RAGE_SOURCE needs "gain"): the charm's uses a second (the "on use" item uses), each filling Rage to its
        # maximum, counted as the maximum a use (the upper reading of the gain: a use with Rage left fills less; the
        # held count can't pass the maximum, MEDIUM)
        used = (stats.get("_item_uses") or {}).get(text)
        if used is None:
            return 0.0, 0.0, "fills Rage on use, but the item's uses weren't read: 0"
        most = number(stats.get("MaximumRage"))
        return used[0] * most, used[0], (f"fills Rage to its maximum ({most:g}) on each use: {used[0]:.3f} uses/s "
                                         f"({used[1]}), {most:g} Rage a use (the upper reading, MEDIUM)")
    m = RAGE_SOURCE.search(text)
    if not m or RAGE_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    amount = float(m.group(2)) if m.group(2) and m.group(2).isdigit() else 1.0
    when = m.group(3).lower()
    if spend := re.search(r"after spending a total of (\d+(?:\.\d+)?) mana", when):
        rate = number(stats.get("ManaPerSecondCost"))
        events = chance * rate / float(spend.group(1))
        return events * amount, events, (f"{chance:.0%} x {amount:g} Rage per {spend.group(1)} Mana spent, the main skill "
                                         f"spending {rate:.1f}/s (other skills' Mana not counted): {events:.2f} gains/s")
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    # (red-flash hits always land, MECHC5; MECHC20: a gain, so the worst case reads the lowest real boss's landed hits)
    land = boss_hits.gain_hits(stats)[0]
    if re.search(r"\bkill", when):  # (MECHC39, the manager 05:20: the word, not "kill" inside "skill")
        rate, how = kill_events(when, stats)
        events = chance * rate
        return events * amount, events, f"{chance:.0%} x {amount:g} Rage on kill, {how}"
    # "Gain N Rage on Melee Axe Hit": only with that weapon equipped (MECHC2; before, any melee hit counted; checked: the
    # only weapon-named Rage gain among PoB's ModCache Rage lines; any weapon word is read the same way)
    if (weapon := re.search(r"\b(axe|mace|sword|spear|flail|quarterstaff|dagger|claw|crossbow|bow)\b[^.;]*\bhit", when)) \
            and not any(weapon.group(1) in t.lower().split() for t in stats.get("_weapon_types") or ()):
        return 0.0, 0.0, f"on {weapon.group(1).capitalize()} hits, and no {weapon.group(1).capitalize()} is equipped"
    if "critically hit" in when or "critical|critically hit" in when:
        events, how = land * 0.05, "when the boss crits you (5% of its hits that land)"
    elif "hit] by" in when or "hit by" in when:
        events, how = land, f"the boss's hits that land, {land:.2f}/s"
    else:
        if not own_hits and re.search(r"hit", when):
            return 0.0, 0.0, "on your hits, but the main skill can't gain Rage while used"
        if (why := _hit_restriction(when, stats)) is not None:
            return 0.0, 0.0, why
        if "ignite" in when:
            events = hits * min(number(stats.get("IgniteChancePerHit")), 100) / 100
            how = f"{events:.2f} Ignites/s"
        elif "critical" in when:
            events = hits * number(stats.get("CritChance")) / 100
            how = f"{events:.2f} crits/s"
        elif "hit" in when:
            events, how = hits, f"{hits:.2f} hits/s"
        elif re.fullmatch(r"\s*on use\s*", when) and (used := (stats.get("_item_uses") or {}).get(text)) is not None:
            events, how = used[0], f"the item's uses: {used[1]}"  # (a flask's or charm's own line, MECH48)
        elif (named := _named_event(when, stats)) is not None:
            events, how = named
        else:
            events, how = 1 / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed (LOW)"
    events = min(events * chance, 1 / RAGE_HIT_EVERY)  # (one Hit every 0.5 s can gain Rage)
    if cap := re.search(r"no more than once every (\d+(?:\.\d+)?) seconds?", when):
        events = min(events, 1 / float(cap.group(1)))
    return events * amount, events, f"{chance:.0%} x {amount:g} Rage, {how}, {events:.2f} gains/s"


# Invigorating Hate (MECHC21; PassiveSkills rage27, consume_rage_when_reverting_to_recover_x%_maximum_life_per_rage: its
# only carrier in PassiveSkills and Mods (checked: every Stats.json id holding "rage" with "shapeshift", "human" or
# "consum", their carriers in Mods and PassiveSkills; PoB's tree.lua 73480, ModCache has no mod for it); "Consume all Rage
# when Shapeshifting to Human form to recover 1% of maximum life per Rage Consumed". KeywordPopups Shapeshift: "Using a
# skill that is not compatible with your Shapeshifted form will automatically Shapeshift you back to human form". So the
# build shifts back at the uses of its pressed skills of the other kind than its main skill (human_shifts): a
# Shapeshift main skill (types Shapeshift and Bear / Wolf / Wyvern) shifts back on each use of a pressed skill that is
# neither Shapeshift nor UsableWhileShapeshifted; a human main skill (not UsableWhileShapeshifted) on its next use after
# each Shapeshift skill's. Each shift consumes the Rage built since the last (rage_held). A change from one animal form to
# another isn't counted as reaching Human form (UNVERIFIED: a lower reading); Demon Form (a Shapeshift whose form keeps
# spells) isn't read as a form here (UNVERIFIED, 0). (MECHC24) "+N maximum Rage while Shapeshifted": in rage_held below.
# "chance to not consume Rage" (rage_skip_consume_chance_%) isn't Invigorating Hate's: it is Rageforged II's
# (RAGE_SKIP_CHANCE).
INVIGORATING_HATE = re.compile(r"\bconsume all rage when shapeshifting to human form\b", re.I)
ANIMAL_FORMS = ("Bear", "Wolf", "Wyvern")
SHIFTED_RAGE = re.compile(r"\+(\d+) to maximum Rage while Shapeshifted\b", re.I)


def human_shifts(main_types: set[str], others: dict[str, tuple[set[str], float]], speed: float) -> tuple[float, str]:
    """(shifts back to Human form a second, how) from the main skill's types and speed and the build's other pressed
    skills ({name: (types, uses a second)})."""
    def form(types: set[str]) -> str | None:
        return next((f for f in ANIMAL_FORMS if f in types), None) if "Shapeshift" in types else None

    main_form = form(main_types)
    if main_form is not None:
        back = {n: u for n, (t, u) in others.items() if u > 0 and "Shapeshift" not in t and "UsableWhileShapeshifted" not in t}
        rate = min(sum(back.values()), speed) if speed > 0 else 0.0
        if rate <= 0:
            return 0.0, (f"the main skill keeps you in {main_form} form and the build presses no skill that isn't "
                         "usable in it: no shift back to Human form, 0")
        return rate, (f"the main skill keeps you in {main_form} form; each use of " + ", ".join(
            f"{n} ({u:.2f}/s)" for n, u in back.items()) + f" shifts you back to Human form: {rate:.2f}/s")
    if "Shapeshift" in main_types or "UsableWhileShapeshifted" in main_types:
        return 0.0, ("the main skill is usable while Shapeshifted (or isn't an animal form's): which presses shift you "
                     "back isn't read, 0 (UNVERIFIED)")
    into = {n: u for n, (t, u) in others.items() if u > 0 and form(t) is not None}
    rate = min(sum(into.values()), speed) if speed > 0 else 0.0
    if rate <= 0:
        return 0.0, "the build presses no Shapeshift skill beside its human-form main skill: no shift, 0"
    return rate, ("each use of " + ", ".join(f"{n} ({u:.2f}/s)" for n, u in into.items()) +
                  f" shifts you into its form, and the main skill's next use back to Human form: {rate:.2f}/s")


def rage_on_shift(held: float, net: float, shifts: float, bonus: float = 0.0, shifted: float = 0.0
                  ) -> tuple[float, float]:
    """(Rage held on average, Rage consumed a second) when every shift back to Human form consumes all Rage: it builds
    again at `net` Rage a second from 0, at most the steady reading `held` (MEDIUM: the loss is taken as no faster than
    in that reading). `bonus`: the build's "+N to maximum Rage while Shapeshifted" when its main skill is used in Human
    form (MECHC24): in the last `shifted` s before each shift back (the build is in its form) Rage climbs on above the
    Human cap, up to `bonus` more, and that is consumed too; the average held for the Human-form main skill is not
    raised."""
    if shifts <= 0 or held <= 0 or net <= 0:
        return held, 0.0
    gap = 1 / shifts
    top = min(net * gap, held)
    fill = top / net
    shifted = min(max(shifted, 0.0), gap)
    spent = min(min(net * (gap - shifted), held) + net * shifted, held + bonus) if bonus > 0 else top
    return min(held, top * (gap - fill / 2) / gap), spent * shifts


def rage_held(lines: list[str], stats: dict) -> tuple[float, str]:
    main_types = stats.get("_main_types") or set()
    spend = stats.get("_rage_spend") or {}
    own_hits = not (stats.get("_main_channel_rage") or False)
    gain, events, how, refund = 0.0, 0.0, [], 0.0
    for text in lines:
        if not qualifies(text, main_types):
            continue
        if per := RAGE_PER_SPENT.search(text):  # (Primal Hunger: a share of the spend comes back, below)
            share = float(per.group(1)) * RECENTLY / float(per.group(2))
            refund += share
            how.append(f"{text[:60]} (regenerates {share:.0%} of the Rage spent, taken as steady)")
            continue
        if (r := _rage_gain(text, stats, own_hits)) is None:
            continue
        gain, events = gain + r[0], events + r[1]
        how.append(f"{text[:60]} ({r[2]})")
    if not how:
        return 0.0, ""
    most = number(stats.get("MaximumRage"))
    spent = sum(spend.values())
    if refund and spent and gain:
        back = min(refund, 1.0) * spent
        events, spent = events + 1.0, spent - back  # (regenerated every second: the loss paused; the net spend falls)
    elif refund and not gain:
        return 0.0, ""  # (a refund with nothing gained: no Rage to spend in the first place)
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    # (red-flash hits always land, MECHC5; MECHC20: a gain, so the worst case reads the lowest real boss's landed hits)
    land = boss_hits.gain_hits(stats)[0]
    pause, decay, loss_how = models.rage_loss(lines)  # (the build's later / faster / slower lines, MECHC5)
    basis = f"{gain:.2f} Rage/s gained"
    if loss_how:
        basis += f" (Rage loss waits {pause:.2g} s, then {decay:.2g}/s: {loss_how})"
    if spent:
        basis += f", {spent:.2f}/s spent by " + ", ".join(f"{n} {v:.2f}/s" for n, v in spend.items())
    net = gain - spent
    if net <= 0 or most <= 0:
        held = 0.0
        basis += " (spent as fast as it comes: none held)" if gain > 0 else " (no gain a boss fight feeds)"
    elif any(RAGE_NO_LOSS.search(t) for t in lines):
        held = most  # (MECHC4: Blood of the Warrior; its line reaches here only while the flask's slot counts, sourceLines)
        basis += ", no inherent loss of Rage (a line of the build): the maximum"
    elif events * pause >= 1:
        held = most
        basis += f", its loss paused (gains {events:.2f}/s, one per {pause:.2g} s needed): the maximum"
    else:
        # (MECHC5, RESEARCH17 #89: a landing hit restarts the pause too. The boss's hits come at random, so after a gain's
        # own pause the loss runs only while no hit landed in the last `pause` s: e^(-hits x pause) of the time. Before,
        # one landing hit per 4 s on average held the maximum, though 37% of that time has no hit in the last 4 s)
        # (MECHC7, AUDIT17: "or after taking damage" covers damage over time too: an Ignite or Corrupted Blood a landed hit
        # leaves on you (boss_hits.es_dot: share p of landed hits, d s each) keeps the loss paused d s longer, so the loss
        # runs e^(-land x (pause + sum p d)) of the time. Bleeding / Poison on you aren't in es_dot (they bypass ES) but
        # pause Rage loss too: the boss presets' Bleeding (Olroth) isn't counted here, a small overcount of the loss.
        # Grounds: 0, you step out of them, as for ES (not all can be: boss_hits.GROUNDS_NOTE, RESEARCH26 R26-59).)
        level = min(most, net / events)  # (each gain, held `pause` s, then lost `decay` per s while no hit pauses it)
        gap = 1 / events
        dots = boss_hits.es_dot(stats, land=land)
        burn = sum(p * d for p, d, _ in dots)
        rate = decay * math.exp(-land * (pause + burn))
        if rate <= 0:
            held = level
        else:
            down = min(level / rate, gap - pause)
            held = (level * pause + level * down - rate * down * down / 2) / gap
        burning = (f" or its damage over time on you ({', '.join(f'{w} on {p:.0%} of landed hits for {d:g} s' for p, d, w in dots)})"
                   if burn > 0 else "")
        basis += (f", one gain every {gap:.1f} s of {level:.0f} Rage held {pause:.2g} s, then lost {decay:.2g}/s while no "
                  f"boss hit ({land:.2f}/s landing){burning} paused it ({rate:.2g}/s on average): {held:.1f} on average")
    if held > 0 and (lost := number(stats.get("_main_rage_lost_on_crit"))) > 0:
        crits = models.crits_per_second(stats)
        if crits > 0 and net > 0:
            between = (net / crits) * (1 - math.exp(-crits * most / net)) * min(lost, 100) / 100 + held * (
                1 - min(lost, 100) / 100)
            if between < held:
                held = between
                basis += (f"; the main skill loses {lost:g}% of maximum Rage on each Critical Hit ({crits:.2f}/s, "
                          f"Prototype Seventeen): {between:.1f} held between crits on average")
    if held > 0 and most > 0 and any(RAGE_RESET_AT_MAX.search(t) for t in lines) and (
            held >= most or (net > 0 and events > 0 and net / events >= most)):
        held = most / 2
        basis += (f"; all Rage is lost on reaching the maximum (a line of the build): it climbs to {most:g} and drops "
                  f"back, {held:g} on average (gains taken as steady, LOW)")
    if any(INVIGORATING_HATE.search(t) for t in lines):  # (MECHC21: each shift back to Human form consumes all Rage)
        shifts, shift_how = stats.get("_human_shifts") or (0.0, "the build's shifts weren't read, 0")
        before = held
        # (MECHC24) "+N to maximum Rage while Shapeshifted" (PassiveSkills rage23 / rage29 +3, rage25 Primal Rage +12;
        # PoB: MaximumRage under Condition Shapeshifted, set only when the main skill is a Bear / Wolf / Wyvern form,
        # CalcPerform 398-412, so `most` holds it then: probe POE2T1690). For a Human-form main skill the build is in
        # its form from each Shapeshift skill's use to the main skill's next use (1 / Speed s, MEDIUM) and Rage climbs
        # above the Human cap there; the shift back consumes it at the form's cap (UNVERIFIED: whether the cap falls
        # before the consume; the higher reading, its share named in the line).
        bonus = sum(float(m.group(1)) for t in lines for m in SHIFTED_RAGE.finditer(t)) if not any(
            f in main_types for f in ANIMAL_FORMS) else 0.0
        speed = number(stats.get("Speed"))
        held, consumed = rage_on_shift(held, net, shifts, bonus, 1 / speed if speed > 0 else 0.0)
        plain = rage_on_shift(before, net, shifts)[1] if bonus > 0 else consumed
        stats.setdefault("_charge_flow", {})["Rage"] = (consumed, shift_how + (
            f": {consumed / shifts:.0f} Rage consumed a shift" if consumed > 0 else "") + (
            f" ({(consumed - plain) / shifts:.1f} of it above the Human cap, from +{bonus:g} maximum Rage while "
            "Shapeshifted, in the form until the main skill's next use; consumed at the form's cap, UNVERIFIED)"
            if consumed > plain else ""))
        if consumed > 0:
            basis += (f"; all Rage consumed on each shift back to Human form ({shift_how}): it builds from 0 again, "
                      f"{held:.1f} held on average (was {before:.1f})")
    held = float(int(held))
    return held, f"{basis} = {held:.0f} held, at most {most:g}: " + "; ".join(how[:3])


# Onslaught's uptime (MECH27, 9 Nov 2026; before, any line that gains Onslaught turned it on, on-kill ones, Companions'
# and "grant Onslaught to nearby Enemies" too). The game files' KeywordPopups Onslaught: "20% increased Skill Speed ...
# Unless specified, Onslaught lasts 4 seconds." Each source's uptime is the chance at least one of its events falls in
# the last duration (1 - (1 - chance)^(events x duration)); sources combine as independent; PoB's box is on at UPTIME
# (90%, as for ailments and flasks). Events: hits, crits from the main skill; the boss's landing hits for "when Hit"; on
# kill / Culling Strike at the build's kills (kill_events, MECHC9: none against a lone boss); a Minion dying at the build's own minion deaths (MECHC7); a Vaal
# skill's use 0 (no usable Vaal skill in this game version: RESEARCH #123, HIGH, labelled); Marks activating once per CHARGE_DEFAULT_EVERY s (assumed); "during effect" (a flask or charm) on (the flask
# model decides whether the flask counts). "While at maximum Frenzy Charges": only when the Frenzy Charges held reach it.
ONSLAUGHT_DURATION = 4.0
ONSLAUGHT_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\b(?:you )?(?:gain|grants?)\b (?:you )?Onslaught\b(.*)$", re.I)
ONSLAUGHT_NOT_SOURCE = re.compile(r"companions|minions (?:have|gain)|onslaught to (?:nearby )?enemies|enemies gain|\ballies\b|"
                                  r"grants skill|per onslaught|"
                                  r"while you have onslaught|instead", re.I)


# "Buffs on you expire N% slower / faster" (MECHC35; RESEARCH29, buff-expire-slower.md, the manager 17 Nov 22:35): stat
# buff_time_passed_+%_only_buff_category, on Prolonged Assault, Flow of Time, Lasting Boons, two Chronomancer smalls and
# two Soul Core bonuses (-10 each; checked by RESEARCH29: every exported table with a stat column). It covers ONLY the
# game's Buff category on you (HIGH, the 0.1.0d fix): Onslaught, Arcane Surge, the Archons, Unholy Might, Tailwind,
# Elusive, Fortify, Guard, the Offerings ...; NOT charges, flask / charm effects, Infusions, Grim Feast, Soul Eater,
# Demonflame, Heralds, curses or debuffs. It slows the buff's timer: duration x 1 / (1 - S), S the lines' sum (adding),
# at most 75% slower (MEDIUM-HIGH), on top of increased duration. PoB reads it as +N% increased Duration of your own
# Buff-tagged skills only (ModParser 4664 / ModCache 6798): the readers here use their own base durations, so nothing is
# counted twice; PoB's reading is shown as a row. Applied in Onslaught's, Arcane Surge's, Unholy Might's and the Archons'
# uptimes, and (MECHC36) Lunar Blessing's length, Sanguine Tide's 5 s, Mana Tempest's linger after the storm (taken as
# the buff's own timer, MEDIUM-LOW) and the sprint row's Guard (models.lunar_blessing_uptime / sanguine_model /
# mana_tempest_linger, recovery.sprint_row; each from the gem's or line's base time, so no double count with PoB's
# +N%). Not changed, with why (MECHC36; categories from RESEARCH29's BuffDefinitions export):
# - Tailwind (models.tailwind_model): its stacks last until you're Hit, no timer (checked: KeywordPopups Tailwind and
#   BuffDefinitions' text name no duration; no Stats id with "tailwind" holds a duration, game 26650308; a hidden timer
#   UNVERIFIED), so the factor has nothing to slow;
# - Elusive, Fortify, Steelskin, Iron Ward, Berserk, Arctic Armour, aura buffs: no model of ours reads their time
#   (PoB's own: Elusive and Fortify only through config boxes the bridge doesn't set; Steelskin's skill duration gets
#   PoB's +N% Duration reading); auras and persistent buffs (Iron Ward, Arctic Armour) have no timer; Berserk ends with
#   your Rage, not a timer (UNVERIFIED);
# - the Offerings (offering_* buffs, Buff category): their buffs come from the spike and "are lost if they die"
#   (KeywordPopups Offering); whether a slower timer outlasts the spike is UNVERIFIED, so left as it is (a RESEARCH
#   question; PoB's +N% on an Offering skill's Duration lengthens the spike itself, possibly an overcount);
# - Thrill of the Kill / Enhanced Culling (models.cull_buff_share, GEM's section) and Voltaic / Freezing Mark's damage
#   buff (MARK_BUFF_DURATION, MECH-A's): Buff category, routed to those lanes (an undercount until then).
BUFF_EXPIRY = re.compile(r"buffs on you expire (\d+(?:\.\d+)?)% (slower|faster)", re.I)
BUFF_EXPIRY_CAP = 0.75


def buff_expiry(lines: list[str]) -> tuple[float, str]:
    """(the factor on a Buff's duration, how) from the build's "Buffs on you expire N% slower / faster" lines; (1.0, "")
    without one."""
    total = sum(float(m.group(1)) * (1 if m.group(2).lower() == "slower" else -1)
                for t in lines for m in BUFF_EXPIRY.finditer(t))
    if total == 0:
        return 1.0, ""
    slow = min(total / 100, BUFF_EXPIRY_CAP)
    factor = 1 / (1 - slow)
    return factor, (f"Buffs on you expire {abs(total):g}% {'slower' if total > 0 else 'faster'}"
                    + (f" (capped at {BUFF_EXPIRY_CAP:.0%})" if total / 100 > BUFF_EXPIRY_CAP else "")
                    + f": the timer runs at {1 - slow:.2f}, so each Buff lasts x{factor:.3f} (RESEARCH29, MEDIUM-HIGH; "
                    f"PoB's reading, +{total:g}% increased Duration on your own Buff skills only: x{1 + total / 100:.2f})")


def _buff_scale(stats: dict) -> float:
    return (stats.get("_buff_expiry") or (1.0, ""))[0]


def _onslaught_source(text: str, lines: list[str], stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line, or None if it isn't a source of Onslaught for you."""
    m = ONSLAUGHT_SOURCE.search(text)
    if not m or ONSLAUGHT_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(2).lower()
    d = re.search(r"for (\d+(?:\.\d+)?) seconds?", when)
    duration = float(d.group(1)) if d else ONSLAUGHT_DURATION
    duration *= _buff_scale(stats)  # (MECHC35: "Buffs on you expire N% slower")
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    # (red-flash hits always land, MECHC5; MECHC20: a gain, so the worst case reads the lowest real boss's landed hits)
    land = boss_hits.gain_hits(stats)[0]
    if re.search(r"\bkill|culling strike", when):
        rate, how = kill_events(when, stats)
        up = 1 - math.exp(-rate * duration * min(chance, 1.0))
        return up, f"{chance:.0%} x {how}, {duration:g} s: {up:.0%} up (kills taken as evenly random, LOW)"
    if "vaal skill" in when:
        # (MECHC7, RESEARCH #123: no usable Vaal skill in game build 26476662 (checked by RESEARCH: no stat set uses
        # skill_is_vaal_skill, no GrantedEffect links the 38 PoE1 Vaal ActiveSkills, no Vaal SkillGems; poe2db /us/Vaal
        # lists none; HIGH)
        return 0.0, (f"'{when.strip()[:30]}': 0, no Vaal skill a player can use exists in this game version (research "
                     "onslaught-vaal-and-own-minion-kills.md, game files and poe2db; HIGH)")
    if MINION_DIES.search(when):
        return _minion_death_uptime(stats, chance, duration)
    if "during effect" in when or not when.strip():
        return 1.0, "during its effect (the flask or charm model decides its uptime)"
    if "maximum frenzy" in when:
        held = _charges_held("Frenzy")(lines, stats)[0]
        most = number(stats.get("FrenzyChargesMax"))
        if most <= 0 or held < most:
            return 0.0, f"needs maximum Frenzy Charges ({held:g} of {most:g} held)"
    if "when hit" in when:
        events, how = land, f"the boss's hits that land, {land:.2f}/s"
    elif "critical" in when:
        events = hits * number(stats.get("CritChance")) / 100
        how = f"{events:.2f} crits/s"
    elif "hit" in when:
        events, how = hits, f"{hits:.2f} hits/s"
    elif (named := _named_event(when, stats)) is not None:
        events, how = named
    else:
        events, how = 1 / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed (LOW)"
    up = 1 - (1 - min(chance, 1.0)) ** (events * duration) if chance < 1 else (1.0 if events * duration >= 1 else events * duration)
    return up, f"{chance:.0%} x {how}, {duration:g} s: {up:.0%} up"


# Leylines (MECHC26; PATCH_CHECK MA-11): PoB's "Are you on a Leyline?" (Condition:OnLeyline: its 29% more Spell damage
# at level 20 and its Ward drain, Data/Skills/other.lua 10124-10128). Where the leylines appear is the game's, and no
# build data gives the share: THE USER, 15 Nov (via the manager, 01:15): "assume you're trying to stand in them if you
# take the ability, so 75% with a note in the build result showing dps for both inside and outside". A labelled flat
# share, the user's own; the play guide's onLeyline row gives the DPS inside (100%) and outside (0%).
LEYLINES, LEYLINE_UPTIME = "Leylines", 0.75
LEYLINE_BASIS = ("on a Leyline 75% of the fight (the user, 15 Nov: a player who takes Leylines stands in them; inside / "
                 "outside DPS in the play guide)")


def leyline_uptime(lines: list[str], stats: dict) -> tuple[object, str, float]:
    """(True, how, 0.75) with the Leylines gem in the build, else (0, "", 0)."""
    if LEYLINES not in (stats.get("_gem_names") or set()):
        return 0.0, "", 0.0
    return True, LEYLINE_BASIS, LEYLINE_UPTIME


# On Full Life (MECHC27; PATCH_CHECK MC-4, MECH75's Full-Life penalties): PoB's "Are you always on Full Life?" box at the
# share of the fight you're on Full Life (recovery.full_life_share: the boss's landed hits and your Life costs against your
# Life recovery), weighed by weigh_uptimes, for a build with a line PoB gates on it: "while / when (not) on Full Life" (not
# the enemy's: "enemies ... on Full Life" is conditionEnemyFullLife) and Zealot's Oath's "Excess Life Recovery from
# Regeneration is applied to Energy Shield" (ModParser 2421). Conditions.evidence puts the share in stats["_full_life"].
FULL_LIFE_LINE = re.compile(r"\b(?:while|when) (?:you are )?(?:not )?on full life\b"
                            r"|^excess life recovery from regeneration is applied to energy shield", re.I)
# (MC-5) the same for Full Energy Shield: "while / when (not) on Full Energy Shield" lines and the Mysticism supports
# (whose stat PoB gates on it: sup_int.lua 7166-7208); Conditions.evidence puts recovery.full_es_share in stats["_full_es"]
FULL_ES_LINE = re.compile(r"\b(?:while|when) (?:you are )?(?:not )?on full energy shield\b", re.I)
MYSTICISM = ("Mysticism I", "Mysticism II")
FULL_LIFE_FIELDS = ("Life", "LifePerSecondCost", "ChaosInoculation", "EnergyShield")


def full_es_uptime(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    """(True, how, share on Full Energy Shield) for a build with a Full Energy Shield line or Mysticism; (0, how) else."""
    got = stats.get("_full_es")
    if not got:
        return 0.0, ""
    share, how = got
    if share <= 0:
        return 0.0, f"never on Full Energy Shield: {how}"
    return True, f"on Full Energy Shield {share:.0%} of the fight: {how}", share


# PoB's "Current Energy Shield percentage" box (MECHC33; COV37 via the manager, 16 Nov 09:09: an UNDERCOUNT of defence,
# never set before): multiplierCurrentEnergyShield (ConfigOptions 157: Condition:UseCurrentEnergyShield +
# Multiplier:CurrentEnergyShield, read up to 150) is what Silks of Veneration's "Current Energy Shield also grants
# Elemental Damage reduction" and The Mutable Star's "Defend against Hits as though you had 1% more Armour per 1% current
# Energy Shield" scale by (checked: their ModCache entries 7052 / 7195 and ModParser 3332-3338 are the only users of
# that multiplier and condition in PoB's ModCache and ModParser). The value is your Energy Shield when a hit lands, as a
# share of its maximum: on Full Energy Shield recovery.full_es_share of the fight (MEDIUM); below full the ES left isn't
# modelled, so the headline counts it as empty (the lower reading, the rule for a defence; LOW), with the ES left spread
# from 0 to full (half on average) as the other reading in the note. Overflowed Energy Shield (above 100%) isn't read:
# an undercount for a build with an overflow line (named).
CURRENT_ES_LINE = re.compile(r"\bcurrent energy shield\b", re.I)
# (a "count" box: PoB applies its placeholder 100 while the input is unset OR 0, ConfigTab 1184-1187, so before this
# PoB read every such build at 100%: an OVERCOUNT, not the undercount COV37 named; 0% is sent as this stand-in. Probe:
# test_engine_models' test_pobs_current_energy_shield_box_reads_its_placeholder_unless_set)
CURRENT_ES_EMPTY = 1e-6


def current_es_percent(lines: list[str], stats: dict) -> tuple[float, str]:
    """(PoB's current Energy Shield % box, how) for a build with a current-Energy-Shield line (the comment above)."""
    if not any(CURRENT_ES_LINE.search(t) for t in lines):
        return 0.0, ""
    got = stats.get("_full_es")
    if not got:  # (no Energy Shield share read: as empty, not PoB's placeholder 100)
        return CURRENT_ES_EMPTY, "no Energy Shield share read: current Energy Shield taken as 0% (not PoB's placeholder 100%)"
    share, how = got
    head = round(100 * share)
    spread = round(100 * (share + (1 - share) / 2))
    return float(head) or CURRENT_ES_EMPTY, (
        f"your Energy Shield when a hit lands, as a % of its maximum: {head}% (on Full Energy Shield {share:.0%} of the "
        f"fight, below full counted as empty: the lower reading for a defence, LOW); with the Energy Shield left below "
        f"full spread from 0 to full: {spread}%; PoB alone reads 100% (its placeholder, an OVERCOUNT before); overflow "
        f"above 100% not read. Full Energy Shield: {how}")


# PoB's "# of times you have been Hit Recently" box (MECHC34; PATCH_CHECK MB-17): a "count" box with no placeholder, never
# set, so "10% increased Stun Threshold for each time you've been Hit by an Enemy Recently, up to 100%" (the notable
# Unbending; PoB: StunThreshold INC per Multiplier:BeenHitRecently, limit 100) read 0: an UNDERCOUNT of the Stun Threshold
# the Heavy Stun model reads. Now the boss's landed hits a second x 4 s (boss_hits.gain_hits: a gain, so the worst case
# reads the lowest real preset's rate; the mean count, MEDIUM). (checked: Stats ids holding hit_recently / been_hit with
# per / each / time, their Mods and PassiveSkills users in game 26650308: Unbending's
# stun_threshold_+%_for_each_time_hit_recently_up_to_100% is the one source; number_of_times_have_been_hit_recently has
# no Mods / passive user; PoB's ModCache: line 1697 the one Multiplier:BeenHitRecently user.)
HIT_RECENTLY_COUNT = re.compile(r"for each time you've been Hit by an Enemy Recently", re.I)


def hits_recently(lines: list[str], stats: dict) -> tuple[float, str]:
    """(PoB's times-Hit-Recently box, how) for a build with a per-time-Hit-Recently line."""
    if not any(HIT_RECENTLY_COUNT.search(t) for t in lines):
        return 0.0, ""
    rate, how = boss_hits.gain_hits(stats)
    count = round(rate * RECENTLY, 2)
    return count, f"the boss's landed hits in the past {RECENTLY:g} s: {how} x {RECENTLY:g} s = {count:g} (MEDIUM)"


# "if you have Shapeshifted to an Animal form Recently" / "... to Human form Recently" (MECHC34; PATCH_CHECK MB-8: the
# Attack damage / Critical Hit chance / Stun buildup / Spell damage passives; PoB: Condition ShapeshiftToAnimal /
# ShapeshiftToHuman, set only by its two config boxes, which the tool never set: an UNDERCOUNT, 0 before). Each shift
# back to Human form (human_shifts, from the build's presses: MECHC21's model) is followed by a shift into the animal form
# again (the main skill's next use, or the next press of the Shapeshift skill), so both shifts come at the same rate h a
# second, evenly spaced (the cycle), and "Recently" (4 s) covers min(1, 4 h) of the fight (MEDIUM). With no shift back
# (an animal main skill and no press that leaves the form) the one shift at the fight's start counts 0 (the literal
# reading; LOW). A change from one animal form to another isn't read (as human_shifts).
SHIFTED_ANIMAL_RECENTLY = re.compile(r"if you have Shapeshifted to an Animal form Recently", re.I)
SHIFTED_HUMAN_RECENTLY = re.compile(r"if you have Shapeshifted to Human form Recently", re.I)
# Dustbloom's Fragile Regrowth (MECHC34; PATCH_CHECK MB-13, an UNDERCOUNT): "Maximum 10 Fragile Regrowth", "0.5% of
# maximum Life Regenerated per second per Fragile Regrowth", "10% increased Mana Regeneration Rate per Fragile Regrowth",
# "Lose all Fragile Regrowth when Hit", "Gain 1 Fragile Regrowth each second" (PoB parses the two per-stack lines on its
# "# of Fragile Regrowth Stacks" count box, never set before, so 0 stacks; the gain / loss / maximum lines it doesn't).
# The stacks climb 1 a second from each landed boss hit (boss_hits.landing_hits: the defence pace, more hits, the lower
# count for a gain lost on hits), capped at the maximum: with hits at random (Poisson, rate h) the mean is
# E[min(t, M)] = (1 - e^(-h M)) / h (MEDIUM). Hits you evade or block don't count (not "Hit").
FRAGILE_GAIN = re.compile(r"^Gain (\d+) Fragile Regrowth each second$", re.I)
FRAGILE_MAX = re.compile(r"^Maximum (\d+) Fragile Regrowth$", re.I)
FRAGILE_LOSE = re.compile(r"^Lose all Fragile Regrowth when Hit$", re.I)
FRAGILE_DEFAULT_MAX = 10.0
# Warbringer's Wooden Wall (MECHC34; PATCH_CHECK MD-6, an UNDERCOUNT of defence): "20% of Damage from Hits is taken from
# your nearest Totem's Life before you" (PoB: TakenFromTotemsBeforeYou under Condition HaveTotem, on whenever the main
# skill is a totem, CalcPerform 424-425; the pool is its "Total Totem Life" box, countAllowZero, never set: 0, so no hit
# was ever taken by a totem, CalcDefence 488-489 / 3668). Now the box is the nearest totem's Life: PoB's TotemLife for a
# totem main skill (one totem: "nearest"; MEDIUM). A totem the build presses beside a non-totem main skill isn't read (0).
TOTEM_BEFORE_YOU = re.compile(r"of Damage from Hits is taken from your nearest Totem's Life before you", re.I)


def fragile_regrowth(lines: list[str], stats: dict) -> tuple[float, str]:
    """(PoB's Fragile Regrowth stacks box, how) for Dustbloom (the comment above FRAGILE_GAIN)."""
    if not any(FRAGILE_GAIN.search(t) for t in lines):
        return 0.0, ""
    cap = max((float(m.group(1)) for t in lines if (m := FRAGILE_MAX.search(t))), default=FRAGILE_DEFAULT_MAX)
    gain = sum(float(m.group(1)) for t in lines if (m := FRAGILE_GAIN.search(t)))
    land = boss_hits.landing_hits(stats)
    if not any(FRAGILE_LOSE.search(t) for t in lines) or land <= 0:
        return cap, f"{cap:g} Fragile Regrowth (gained {gain:g} a second; nothing loses it)"
    mean = min(cap, gain * (1 - math.exp(-land * cap / gain)) / land)
    return round(mean, 2), (f"gained {gain:g} a second up to {cap:g}, all lost on each landed boss hit ({land:.2f}/s, at "
                            f"random): {mean:.2f} on average (g (1 - e^(-h M / g)) / h; MEDIUM)")


def totem_life_before_you(lines: list[str], stats: dict) -> tuple[float, str]:
    """(PoB's Total Totem Life box, how) for a build with "taken from your nearest Totem's Life before you" (the comment
    above TOTEM_BEFORE_YOU)."""
    if not any(TOTEM_BEFORE_YOU.search(t) for t in lines):
        return 0.0, ""
    life = number(stats.get("TotemLife"))
    if life <= 0:
        return 0.0, ("the main skill isn't a totem, so PoB gives no totem Life (a pressed totem's isn't read: an "
                     "undercount, UNVERIFIED how much); 0")
    return round(life), (f"the nearest totem's Life, {life:,.0f} (PoB's TotemLife for the main skill: one totem, its "
                         "Life once, a re-placed totem not counted; MEDIUM)")


def shifted_recently(pattern: re.Pattern, form: str):
    """value_fn for PoB's "Shapeshifted to <form> recently?" box: (True, how, share) or (0, how)."""
    def read(lines: list[str], stats: dict) -> tuple:
        if not any(pattern.search(t) for t in lines):
            return 0.0, ""
        shifts, how = stats.get("_human_shifts") or (0.0, "the build's shifts weren't read, 0")
        share = min(1.0, RECENTLY * shifts)
        if share <= 0:
            return 0.0, f"no shift to {form} form after the fight's start: {how}"
        return True, (f"shifted to {form} form Recently {share:.0%} of the fight ({shifts:.2f} shifts a second each way, "
                      f"evenly spaced, x {RECENTLY:g} s; MEDIUM): {how}"), share
    return read


def full_life_uptime(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    """(True, how, share on Full Life) for a build with a Full Life line; (0, how) when it's never on Full Life."""
    got = stats.get("_full_life")
    if not got:
        return 0.0, ""
    share, how = got
    if share <= 0:
        return 0.0, f"never on Full Life: {how}"
    return True, f"on Full Life {share:.0%} of the fight: {how}", share


# While Leeching (MECHC31; RESEARCH26 R26-40 (c), plans5-five-in-game-checks-r26.md, an OVERCOUNT): KeywordPopups
# LifeLeech, "all Life Leech is removed when Life is filled" (HIGH; Mana and Energy Shield Leech the same for their
# pools). conditionLeeching was on all fight for any build with a Leech line, though Leech runs only while the pool it
# fills is below full. Now each pool's Leech runs the share of the fight that pool is below full, from the build's own
# flows: Life 1 - recovery.full_life_share (the boss's landed hits and Life costs against its recovery), Energy Shield
# 1 - full_es_share, Mana the Mana spent a second against its refill (recovery.refill_rate; Mind over Matter's share of
# hits not read, a lower reading). "Leeching" = any of them (taken as independent: MEDIUM). PoB's "...Leeching Life /
# Mana / Energy Shield" boxes were never set (an UNDERCOUNT for those lines): each now at its pool's share. The sources
# that keep Life Leech at full Life (checked by RESEARCH26: uses.py over the Stats ids holding leech with full /
# not_removed: Life Leech III, base_life_leech_does_not_stop_at_full_life's two uniques, Vaal Pact since 0.5.0) keep
# Life Leeching all fight; PoB also sets Leeching itself for the two lines it parses (CalcPerform 776-779). A pool
# needs a Leech rate from PoB (LifeLeechRate / ManaLeechRate / EnergyShieldLeechRate) to count.
LEECHING_LINE = re.compile(r"\bwhile (?:not )?leeching\b|\bif you are leeching\b", re.I)
LEECH_AT_FULL = re.compile(r"leech\w*[^.;]*\bnot (?:be )?removed\b[^.;]*\b(?:full|filled)\b"
                           r"|leech\w*[^.;]*\b(?:does not|doesn't) stop at full life", re.I)
LEECH_AT_FULL_GEMS = ("Life Leech III",)
LEECH_POOLS = {"Life": "LifeLeechRate", "Mana": "ManaLeechRate", "EnergyShield": "EnergyShieldLeechRate"}


def leeching_shares(stats: dict, texts: list[str], gems: set[str]) -> dict[str, tuple[float, str]]:
    """{pool: (share of the fight its Leech runs, how)} for the pools the build Leeches (stats: full_life_share's,
    with the Leech rates; texts: the build's lines; gems: its enabled gem names)."""
    from builder.search import recovery

    out: dict[str, tuple[float, str]] = {}
    for pool, rate in LEECH_POOLS.items():
        if number(stats.get(rate)) <= 0:
            continue
        if pool == "Life" and (any(LEECH_AT_FULL.search(t) for t in texts) or gems & set(LEECH_AT_FULL_GEMS)):
            out[pool] = (1.0, "Life Leech isn't removed at full Life (the build's line or Life Leech III): all fight")
        elif pool == "Life":
            full, how = recovery.full_life_share(stats)
            out[pool] = (1 - full, f"Life below full {1 - full:.0%} of the fight ({how})")
        elif pool == "EnergyShield":
            full, how = recovery.full_es_share(stats)
            out[pool] = (1 - full, f"Energy Shield below full {1 - full:.0%} of the fight ({how})")
        else:
            refill, how = recovery.refill_rate(stats, "Mana")
            spend = max(number(stats.get("ManaPerSecondCost")), 0.0)
            below = 1.0 if spend > 0 and refill <= 0 else (min(1.0, spend / refill) if refill > 0 else 0.0)
            out[pool] = (below, f"Mana below full {below:.0%} of the fight ({spend:,.1f} Mana/s spent; {how})")
    return out


def leeching_uptime(pool: str | None) -> Callable[[list[str], dict], tuple]:
    """value_fn for PoB's "Leeching" box (pool None: any Leech) and its Life / Mana / Energy Shield boxes."""
    def value(lines: list[str], stats: dict) -> tuple:
        shares = stats.get("_leeching")
        if shares is None:
            return 0.0, ""
        if not shares:
            return 0.0, "no Leech rate in PoB: never Leeching"
        if pool is None:
            idle = 1.0
            for share, _ in shares.values():
                idle *= 1 - share
            share, how = 1 - idle, "; ".join(f"{p}: {h}" for p, (_, h) in shares.items())
        else:
            share, how = shares.get(pool, (0.0, f"no {pool} Leech"))
        if share <= 0:
            return 0.0, f"never Leeching: {how}"
        return True, f"Leeching {share:.0%} of the fight (Leech stops when its pool is full, MEDIUM): {how}", share
    return value


# In Demon Form (MECHC28; PATCH_CHECK S2-4): PoB's "Are you in Demon Form?" (inDemonForm, ConfigOptions 356, on by
# default) at the share of the fight the build stays in the form (assumptions.Assumptions.demon_form:
# recovery.demon_form_cycle from its Life flows, the Demonflame's Life loss, its cooldown and its non-Spell presses),
# weighed by weigh_uptimes. Under SWITCH_ON it is switched OFF explicitly (False), since PoB's default is on.
def demon_form_uptime(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[object, str]:
    """(True, how, share in the form) for a build with Demon Form; (False, why) when it can't stay in it; (0, "")
    without Demon Form (or before the assumptions have rated it)."""
    got = stats.get("_demon_form")
    if not got:
        return 0.0, ""
    share, how = got
    if share < SWITCH_ON:
        return False, f"Demon Form kept up {share:.0%} of the fight: {how}"
    return True, f"in Demon Form {share:.0%} of the fight: {how}", share


# Trinity's Affinity (MECHC29; PATCH_CHECK MA-5, an UNDERCOUNT before: PoB's "Total Resonance Count" (configResonanceCount,
# ConfigOptions 689, Multiplier:ResonanceCount, at most 300) was never set, so Trinity's more Elemental damage counted 0).
# The game (stat_descriptions.csd, build 26476662): "{0}% more Elemental Damage per 30 Affinity of any type" (the stat id
# says per_50, the text per 30: PoB's div = 30 follows the text, so PATCH_CHECK's "x1.67 overcount" is the id's name only);
# "On dealing Elemental Damage with a Hit, gain {0} Affinity of the highest Elemental Damage type and lose {1} Affinity of
# the other types" (trinity_resonance_to_grant by level, trinity_loss_per_hit 3); KeywordPopups Affinity: "a maximum of
# 100 of each type ... You lose 10 Affinity per second of specific types if you haven't gained Affinity of that type in
# the past 8 seconds". (No patch-note line for Trinity or Affinity: grep_notes --history; not proof of no change.)
# Model: the main skill's hits (others' hits not counted, either way: labelled); the share of hits whose highest type is
# each element from PoB's per-type hit averages, read under each "Random element mode" (physMode FIRE / COLD / LIGHTNING:
# a random-element conversion, e.g. Trinity's own, picks one element per hit, a third each); each type a random walk on
# 0..100, +grant at that share, -3 otherwise, its steady average (affinity_steady). A type gaining under once per 8 s is
# taken to decay to 0. The count is the three types' sum (PoB floors it per 30).
TRINITY, TRINITY_GRANT, TRINITY_LOSS, AFFINITY_MAX, AFFINITY_DECAY_WAIT = "Trinity", "trinity_resonance_to_grant", 3, 100, 8.0
ELEMENTS = ("Fire", "Cold", "Lightning")


@functools.lru_cache(maxsize=256)
def affinity_steady(share: float, grant: int, loss: int = TRINITY_LOSS, top: int = AFFINITY_MAX) -> float:
    """The average Affinity of one type, held between 0 and `top`, when each hit adds `grant` at chance `share` and
    takes `loss` otherwise (the walk's steady state, by iteration)."""
    if share <= 0 or grant <= 0:
        return 0.0
    if share >= 1:
        return float(top)
    dist = [0.0] * (top + 1)
    dist[top if grant * share > loss * (1 - share) else 0] = 1.0
    for _ in range(20000):
        nxt = [0.0] * (top + 1)
        for a, w in enumerate(dist):
            if w:
                nxt[min(top, a + grant)] += w * share
                nxt[max(0, a - loss)] += w * (1 - share)
        if sum(abs(x - y) for x, y in zip(nxt, dist)) < 1e-10:
            dist = nxt
            break
        dist = nxt
    return sum(a * w for a, w in enumerate(dist))


def highest_element_shares(reads: list[dict]) -> dict[str, float]:
    """{element: share of hits whose highest Elemental type it is}, from PoB's per-type hit averages under each random
    element mode (one read without random elements; ties go to the first of Fire, Cold, Lightning)."""
    out = {e: 0.0 for e in ELEMENTS}
    for got in reads:
        hits = {e: number(got.get(f"{e}HitAverage")) for e in ELEMENTS}
        if max(hits.values()) > 0:
            out[max(ELEMENTS, key=lambda e: hits[e])] += 1 / len(reads)
    return out


def trinity_affinity(shares: dict[str, float], hits: float, grant: int) -> tuple[float, str]:
    """(Affinity of all types summed, how) for hits a second whose highest type follows `shares`."""
    if hits <= 0 or grant <= 0 or not any(shares.values()):
        return 0.0, f"the main skill deals no Elemental Hit damage ({hits:.2f} hits/s): no Affinity"
    total, parts = 0.0, []
    for e in ELEMENTS:
        share = shares[e]
        held = affinity_steady(round(share, 3), grant) if share * hits * AFFINITY_DECAY_WAIT >= 1 else 0.0
        total += held
        if share > 0:
            parts.append(f"{e} highest on {share:.0%} of hits: {held:.0f}")
    return total, (f"{hits:.2f} hits/s with Elemental damage, +{grant} Affinity of the highest type and -{TRINITY_LOSS} of "
                   f"the others a hit, at most {AFFINITY_MAX} each: {'; '.join(parts)}; {total:.0f} Affinity in all "
                   f"(the main skill's hits only; PoB counts whole 30s)")


def trinity_count(lines: list[str], stats: dict) -> tuple[float, str]:
    got = stats.get("_trinity")
    if not got:
        return 0.0, ""
    return got


# Dodge Rolled Recently (MECHC27; PATCH_CHECK MC-7; MECH75: the box is safe to set now that Stoicism no longer reads it):
# PoB's "Have you Dodge Rolled Recently?" (ModParser 2091: "if you've dodge rolled recently": Evasion lines) at the share
# of the fight a roll was made in the past 4 s, from the rolls the build makes (the manager's ruling on PLANS2's
# questions, FIX_QUEUE 12 Nov ~09:00: "a build with no roll-dependent source rolls once per telegraphed boss hit (every 5
# s, the user's Parry cadence, 11 Nov); a build that relies on rolling, once every 2 s (the user's Cast on Dodge default,
# 7 Nov); both labelled, 'dodge rolled recently' shares from them"). Relying on rolling: MECH75's rule for Stoicism
# (assumptions.ROLL_RELIANT: Cast on Dodge, a "when you Dodge Roll" line, or a "Dodge Rolled Recently" line: a player who
# takes one rolls to feed it), so the two read one cadence. Rolls at a steady cadence of T s: recently min(1, 4 / T) of
# the fight (80% at 5 s, all of it at 2 s). The time the extra rolls take isn't charged here (nor in Stoicism's).
DODGE_RECENTLY_LINE = re.compile(r"\bif you(?:'ve| have) dodge rolled recently\b", re.I)


def dodge_rolled_recently(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    """(True, how, share of the fight a roll was in the past 4 s) for a build with a "Dodge Rolled Recently" line."""
    if not any(DODGE_RECENTLY_LINE.search(t) for t in lines):
        return 0.0, ""
    from builder.search.assumptions import ROLL_RELIANT  # (MECH75's Stoicism reads the same rule: one cadence a build)

    reliant = "Cast on Dodge" in (stats.get("_gem_names") or set()) or any(ROLL_RELIANT.search(t) for t in lines)
    every = models.DODGE_EVERY if reliant else models.PARRY_EVERY
    share = min(1.0, RECENTLY / every)
    why = (f"a roll every {every:g} s (the build relies on rolling: the user's Cast on Dodge default)" if reliant else
           f"a roll every {every:g} s, one per telegraphed boss hit (the manager's ruling on PLANS2; how often you "
           f"roll is up to you: roll at least every 4 s for all of it)")
    return True, f"Dodge Rolled in the past 4 s {share:.0%} of the fight: {why} (LOW)", share


# Not Dodge Rolled Recently (MECH88; COV42's routed item, the manager's 08:25 ruling): Rune of Acrobatics' "N% increased
# Armour if you haven't Dodge Rolled Recently" and Kurgal's Gaze's "N% increased Mana Cost Efficiency if you haven't Dodge
# Rolled Recently" (boots; PoB ignores both lines). The share of the fight with no roll in the past 4 s, from the same
# roll cadence as dodge_rolled_recently (a roll every models.PARRY_EVERY s, one per telegraphed boss hit; every
# models.DODGE_EVERY s for a build that relies on rolling): max(0, 1 - 4 / T), 20% at 5 s, none at 2 s (LOW). A player
# who takes the lines might roll less and take the telegraphed hits instead (the defences don't weigh that): a row.
NOT_DODGE_RECENTLY_LINE = re.compile(r"\bif you(?: haven't| have not) dodge rolled recently\b", re.I)


def not_dodge_rolled_recently(lines: list[str], gem_names: set[str] | frozenset[str] = frozenset()) -> tuple[float, str]:
    """(share of the fight with no Dodge Roll in the past 4 s, how) at the build's roll cadence (see above)."""
    from builder.search.assumptions import ROLL_RELIANT  # (one cadence a build, as dodge_rolled_recently)

    reliant = "Cast on Dodge" in set(gem_names) or any(ROLL_RELIANT.search(t) for t in lines)
    every = models.DODGE_EVERY if reliant else models.PARRY_EVERY
    share = max(0.0, 1.0 - RECENTLY / every)
    why = (f"a roll every {every:g} s (the build relies on rolling: the user's Cast on Dodge default)" if reliant else
           f"a roll every {every:g} s, one per telegraphed boss hit (the manager's ruling on PLANS2)")
    return share, (f"no Dodge Roll in the past 4 s for {share:.0%} of the fight: {why} (LOW); if you never roll (taking "
                   "the telegraphed hits instead, which the defences don't weigh): all of it")


# Unholy Might from Kurgal's Leash (MECHC27; PATCH_CHECK MD-1): "Supports Commandable Minions causing you and the
# Supported Minion to gain Unholy Might when you Command them" (gain_unholy_might_for_X_ms_on_command and
# minion_gain_unholy_might_for_X_ms_on_command, 15000 each: GrantedEffectStatSets SupportKurgalsLeashPlayer; PoB's
# sup_int.lua 6204-6229 carries both and no SkillStatMap entry reads them, so it scored 0). Unholy Might: "a Buff that
# grants 30% of all damage as extra Chaos Damage" (KeywordPopups UnholyMight), PoB's buffUnholyMight / minionbuffUnholyMight
# boxes (ConfigOptions 934-938; its magnitude lines apply through Multiplier:UnholyMightMagnitude). Up min(1, 15 s x the
# supported minion's Commands a second): its group's use rate (skill_use_rate: cooldown, duration, else the search's chosen
# press rate), 0 without one (no flat rate). The other sources PoB applies itself (checked: ModParser 3182 / 4274 / 4692 /
# 4871 / 5505 and ModCache's 8 Unholy Might lines: "You have Unholy Might", Necromantic Conduit's "not on Low Mana" aura,
# "while you have no Energy Shield", "on Block" through BlockedRecently, "during Flask Effect", "Minions have Unholy
# Might"): the boxes add nothing on top of them. The minion box reaches every minion, not only the Supported one (an
# OVERCOUNT for a build with other minions: LOW); buff duration modifiers on the 15 s aren't read (an undercount).
MARK_ACTIVATED = re.compile(r"activates the mark", re.I)  # (MECHC27, K2-2: Sniper's Mark's Frenzy Charge)
KURGAL, KURGAL_SECONDS = "Kurgal's Leash", 15.0


def unholy_might_uptime(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    """(True, how, share) from Kurgal's Leash's Commands (stats["_kurgal"] = (Commands a second, how)), else (0, ...)."""
    got = stats.get("_kurgal")
    if not got:
        return 0.0, ""
    uses, how = got
    if uses <= 0:
        return 0.0, f"Kurgal's Leash: {how}, so no Command grants Unholy Might (no source, 0)"
    seconds = KURGAL_SECONDS * _buff_scale(stats)  # (MECHC35: "Buffs on you expire N% slower")
    share = min(1.0, seconds * uses)
    return True, (f"Unholy Might {share:.0%} of the fight from Kurgal's Leash: {seconds:g} s a Command x {how} "
                  f"(MEDIUM)"), share


def onslaught_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    main_types = stats.get("_main_types") or set()
    down, how = 1.0, []
    for text in lines:
        # ("when a Minion Dies" is yours, not a minion's line, as for Arcane Surge; MECHC7)
        own = re.sub(r"when a minion dies", "", text, flags=re.I)
        if not qualifies(own, main_types) or (r := _onslaught_source(text, lines, stats)) is None:
            continue
        down *= 1 - r[0]
        how.append(f"{text[:60]} ({r[1]})")
    if not how:
        return 0.0, ""
    up = 1 - down
    basis = f"Onslaught up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
    if (expiry := stats.get("_buff_expiry")) and expiry[1]:
        basis += f"; durations x{expiry[0]:.3f} ({expiry[1]})"
    return _box(up, basis)


# Arcane Surge's uptime (MECH29, 9 Nov 2026; before, any line that gains it turned it on, crit-chance and Minion-death
# ones too). The game files' KeywordPopups ArcaneSurgeDuration: "It lasts for 4 seconds by default." Each source's uptime
# as Onslaught's (1 - (1 - chance)^(events x duration)), combined as independent; PoB's box is on at UPTIME. Events: the
# main skill's crits and hits ("with Spells": QUALIFIERS needs a Spell); "if you have at least N Devotion" by PoB's
# Devotion; "while at maximum Power Charges" only when the Power Charges held reach it; "after Channelling for 1 second"
# when the main skill channels; a Minion dying at the build's own minion deaths (MECHC7: _minion_death_uptime); a kill at
# the build's kills (kill_events, MECHC9);
# Shapeshifting back, a Mine or Trap: 0 (no rate modelled, labelled); a Totem summoned: once per CHARGE_DEFAULT_EVERY s (assumed). The Arcane Surge support (GrantedEffectStatSets
# SupportArcaneSurgePlayer: 10 s after spending 100% of maximum Mana on Supported Skills) on the main skill: PoB's mana
# spent per second (ManaPerSecondCost) against the threshold. Whether Mana spent while the buff is up counts toward the
# next one is unsettled (RESEARCH1, scratch/manager/research/mana-spend-counters.md, LOW: no game text, patch note or test
# says; guides report 70-85% uptime, not the 100% a running counter gives fast casters), so the headline counts the lower
# bound, the counter restarting at each gain: up = D / (D + M / r) (MECHC1, 12 Nov 2026, accuracy first); the play
# guide shows the running counter's min(1, D r / M) as an "if it counts" row. Its effect and duration modifiers aren't
# read (labelled).
ARCANE_SURGE_DURATION = 4.0
ARCANE_SURGE_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\b(?:you )?(?:gain|grants?|have)\b (?:you )?Arcane Surge\b(.*)$", re.I)
ARCANE_SURGE_NOT_SOURCE = re.compile(r"companions|minions (?:have|gain)|\ballies\b|grants skill|instead|"
                                     r"(?:while|if) you have arcane surge|effect of arcane surge", re.I)
ARCANE_SURGE_SUPPORT_STATS = ("support_arcane_surge_gain_buff_on_%_of_maximum_mana_use_threshold",
                              "support_arcane_surge_base_duration_ms")


def _arcane_surge_source(text: str, lines: list[str], stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line, or None if it isn't a source of Arcane Surge for you."""
    m = ARCANE_SURGE_SOURCE.search(text)
    if not m or ARCANE_SURGE_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(2).lower()
    d = re.search(r"for (\d+(?:\.\d+)?) seconds?", when)
    duration = float(d.group(1)) if d else ARCANE_SURGE_DURATION
    duration *= _buff_scale(stats)  # (MECHC35: "Buffs on you expire N% slower")
    if not when.strip(" .") or re.fullmatch(r"\s*on you\W*", when):
        return 1.0, "always (the line grants it)"
    if spend := re.search(r"spending a total of (\d+(?:\.\d+)?)(%? of your maximum mana| mana)", when):
        mana = float(spend.group(1)) * (number(stats.get("Mana")) / 100 if "%" in spend.group(2) else 1)
        rate = number(stats.get("ManaPerSecondCost"))
        if rate <= 0 or mana <= 0:
            return 0.0, f"needs {mana:.0f} Mana spent, the main skill spends none"
        fill = mana / rate
        up, high = duration / (duration + fill), min(1.0, duration / fill)
        return up, (f"{mana:.0f} Mana spent every {fill:.1f} s ({rate:.1f}/s), {duration:g} s each: {up:.0%} up, Mana spent "
                    f"during the buff not counted (LOW: the lower bound); {high:.0%} if it counts"), high
    if dev := re.search(r"at least (\d+) devotion", when):
        if number(stats.get("Devotion")) < float(dev.group(1)):
            return 0.0, f"needs {dev.group(1)} Devotion ({number(stats.get('Devotion')):g} held)"
    if "maximum power" in when:
        held = _charges_held("Power")(lines, stats)[0]
        most = number(stats.get("PowerChargesMax"))
        if most <= 0 or held < most:
            return 0.0, f"needs maximum Power Charges ({held:g} of {most:g} held)"
    if "channelling" in when:
        up = 1.0 if "Channel" in (stats.get("_main_types") or set()) else 0.0
        return up, "the main skill channels" if up else "the main skill doesn't channel"
    if MINION_DIES.search(when):
        return _minion_death_uptime(stats, chance, duration)
    if re.search(r"\bkill", when):  # (MECHC9, A23-4: at the build's kills, as Onslaught's)
        rate, how = kill_events(when, stats)
        up = 1 - math.exp(-rate * duration * min(chance, 1.0))
        return up, f"{chance:.0%} x {how}, {duration:g} s: {up:.0%} up (kills taken as evenly random, LOW)"
    if re.search(r"minion|shapeshift|\bmines?\b|\btraps?\b", when):
        return 0.0, f"'{when.strip()[:30]}': its rate isn't modelled (0, labelled)"
    hits = models.hits_per_second(stats)
    if "critical" in when:
        events, how = models.crits_per_second(stats), "crits"
    elif "hit" in when:
        events, how = hits, "hits"
    elif (named := _named_event(when, stats)) is not None:
        events, how = named
    else:
        events, how = 1 / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed (LOW)"
    n = events * duration
    up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
    return up, f"{chance:.0%} x {events:.2f} {how}/s, {duration:g} s: {up:.0%} up"


def arcane_surge_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    main_types = stats.get("_main_types") or set()
    support = stats.get("_arcane_surge_support")
    if support:  # (the support on the main skill, as the line it would be)
        lines = [*lines, f"Gain Arcane Surge for {support[1]:g} seconds after Spending a total of {support[0]:g}% of "
                         f"your Maximum Mana on Supported Skills"]
    down, down_high, how = 1.0, 1.0, []
    for text in lines:
        # ("when a Minion Dies" is yours, not a minion's line: kept, so it's labelled rather than dropped)
        own = re.sub(r"when a minion dies", "", text, flags=re.I)
        if not qualifies(own, main_types) or (r := _arcane_surge_source(text, lines, stats)) is None:
            continue
        down *= 1 - r[0]
        down_high *= 1 - (r[2] if len(r) > 2 else r[0])
        how.append(f"{text[:60]} ({r[1]})")
    if not how:
        return 0.0, ""
    up, high = 1 - down, 1 - down_high
    basis = f"Arcane Surge up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
    if (expiry := stats.get("_buff_expiry")) and expiry[1]:
        basis += f"; durations x{expiry[0]:.3f} ({expiry[1]})"
    if high > up:  # (the play guide's "if Mana spent during the buff counts" row: ArcaneSurgeAlternative)
        return (*_box(up, basis), False, (high, "if Mana spent while Arcane Surge is up counts toward the next one "
                                                "(unsettled, LOW: research/mana-spend-counters.md; an in-game test: "
                                                "count the casts between buffs with the buff up and with it fallen off)"))
    return _box(up, basis)


# Debuffs you inflict on the boss (MECH29, 9 Nov 2026; before, any line that inflicts one turned its box on): Blinded,
# Maimed, Hindered (KeywordPopups Blind / Maim / Hinder: 4 s unless the line says), Intimidated (KeywordPopups Intimidate
# gives no duration; every live line gives 4 s but the Block one, so 4 s assumed), Unnerved (no KeywordPopups entry and
# no line in PoB's mods, tree or gems: only old stat descriptions, all 4 s), Covered in Ash / Frost (no popup; 4 s
# assumed). PoB puts Intimidated (DamageTaken INC 10), Unnerved (Spell DamageTaken INC 10, CalcSetup 75-80) and Ash /
# Frost (Fire / Cold DamageTaken INC 20, CalcPerform 819-826) in the enemy's taken pool. Each line's uptime as
# Onslaught's (1 - (1 - chance)^(events x duration)), combined as independent, on at UPTIME: the main skill's hits and
# crits; the build's kills of the boss's adds ("on Kill": kill_events, the boss taken to be near; MECH55); the boss's hits that land ("when they Hit you") or that you Block ("Permanently ... on Block": on with any
# Block); "Enemies in your Presence" while the boss stands inside it (the build's distance); "Enemies you Curse" with a
# curse gem in the build (the Cursed box's own evidence); "while at maximum Endurance Charges" by the charges held;
# "Enemies you Fully Armour Break" at armour_broken's Full Break rate. A
# Heavy Stun, Immobilise, Shapeshift, Burning, a socketed jewel or a gem's own effect: rate not modelled (0,
# labelled). "X% increased <Debuff> Duration" lines scale each line's duration (models.duration_scale: Hinder and Blind
# have them in the game files, MECH44); Blind / Maim effect isn't read.
DEBUFF_DURATION = 4.0
DEBUFFS = {  # var: (the verb, the participle)
    "conditionEnemyBlinded": ("blind", "blinded"),
    "conditionEnemyMaimed": ("maim", "maimed"),
    "conditionEnemyHindered": ("hinder", "hindered"),
    "conditionEnemyIntimidated": ("intimidate", "intimidated"),
    "conditionEnemyUnnerved": ("unnerve", "unnerved"),
    "conditionEnemyCoveredInAsh": ("cover", "covered in ash"),
    "conditionEnemyCoveredInFrost": ("cover", "covered in frost"),
    # (MECH78, PATCH_CHECK MB-15) KeywordPopups Debilitate: "inflicts 20% reduced movement speed and 10% reduced damage
    # dealt. Unless specified, Debilitate lasts 1 second." PoB's box (conditionEnemyDebilitated) was never set.
    "conditionEnemyDebilitated": ("debilitate", "debilitated"),
}
DEBUFF_DURATIONS = {"conditionEnemyDebilitated": 1.0}  # (each debuff's own default where its definition gives one)
# "Warcries Debilitate Enemies" (PoB: DebilitateChance 100): the trigger is the line's subject, before the verb
WARCRIES_SUBJECT = re.compile(r"^\s*warcries\s+debilitate\b", re.I)
DEBUFF_NOT_SOURCE = re.compile(r"\b(?:increased|reduced|more|less)\b|\bagainst\b|\bcannot\b|immun|\byou are (?!burning)|"
                               r"\ballies\b|minions|companions|summoned|grants skill|\bif you'?ve\b", re.I)


def curse_uses(build: Build, stats: dict, texts: list[str], metres: float | None = None) -> list[models.CurseUse]:
    """The build's curses and how it applies each (models.curse_uses), the boss at `metres` or the config's distance."""
    if metres is None:
        metres = number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(set())
    stats = {"_curse_durations": getattr(build, "curse_durations", {}), **stats}  # (MECHB12, A25-41: Assumptions.models')
    stats = {"_curse_delays": getattr(build, "curse_delays", {}), **stats}  # (MECH86: each curse's own delay)
    # (MECH75) the hand-cast curses the search chose to let run out before each recast (models.CURSE_LAPSE)
    stats["_curse_lapse"] = set(getattr(getattr(build, "objective", None), models.CURSE_LAPSE, None) or ())
    return models.curse_uses(build.groups(), build.ctx.gems, stats, metres, texts, getattr(build, "trigger_rates", None))


def _debuff_pattern(var: str) -> re.Pattern:
    verb, participle = DEBUFFS[var]
    target = r"(?:rare or unique )?(?:enemies|targets|them)"
    where = f" {target} in {participle.split(' in ')[1]}" if " in " in participle else f"(?: {target})?"
    return re.compile(rf"(?:(\d+(?:\.\d+)?)% (?:global )?chance to )?\b(?:permanently )?{verb}\b{where}(.*)$|"
                      rf"\benemies\b(.*?)\b(?:are|become) {participle}\b(.*)$", re.I)


WHEN_YOU_AILMENT = re.compile(r"\bwhen you (poison|ignite|shock)\b", re.I)


STUNS_YOU = re.compile(r"\bwhen they stun you\b", re.I)


SHAPESHIFT_TYPES = {"Shapeshift", "Bear", "Wolf", "Wyvern"}  # (PoB skill types of the forms' skills)


def _debuff_source(text: str, var: str, lines: list[str], stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line for this debuff, or None if it isn't a source of it."""
    m = _debuff_pattern(var).search(text)
    if not m or DEBUFF_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = (m.group(2) if m.group(2) is not None else f"{m.group(3)} {m.group(4)}").lower()
    d = re.search(r"for (\d+(?:\.\d+)?) seconds?", when)
    duration = ((float(d.group(1)) if d else DEBUFF_DURATIONS.get(var, DEBUFF_DURATION))
                * models.duration_scale(lines, DEBUFFS[var][0]))
    every = bosses.benefit_skill_time(stats)
    if WARCRIES_SUBJECT.search(text) and not when.strip():  # (MECH78: each Warcry the build uses, stats["_events"])
        rate, how = _named_event("on warcry", stats) or (0.0, "the build's Warcries weren't read")
        up = min(1.0, rate * duration)
        return up, f"{rate:.3f} Warcries/s ({how}), {duration:g} s each: {up:.0%} up"  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    if "presence" in when:
        metres = (stats.get("_distance_choice") or (natural_distance(stats.get("_main_types") or set(), stats.get("_main_skill") or ""), ""))[0]
        radius = number(stats.get("PresenceRadius")) / 10
        up = 1.0 if radius >= metres else 0.0
        return up, f"the boss {metres:g} m away, your Presence {radius:.1f} m"
    if "you curse" in when:
        return models.curse_share(stats.get("_curse_uses") or [])
    if "near enemies you mark" in when:  # (MECH87: MARK_BLIND, the boss's Marked share)
        return stats.get("_marked") or (0.0, "the boss's Marked share wasn't read (0, labelled)")
    if "shield is raised" in when and var == "conditionEnemyBlinded":  # (Bastion of Light: models.raised_plan, MECHC21)
        plan = stats.get("_raised_plan")
        if plan is None:
            return 0.0, "the build's shield taps weren't read (0, labelled)"
        return plan.blind, plan.how
    if "stun you" in when:
        # (MECHC25; plans/term-blind-enemies.md G3) Blinding Flash's "Blind Enemies when they Stun you"
        # (blind_enemies_when_they_stun_you): at the boss's hits that Light Stun you, flasks.Charges.stuns (recovery's
        # stun model, as the "Used when you become Stunned" charms read it; set in evidence as _stuns_you). No Stuns: 0
        stuns, how = stats.get("_stuns_you") or (0.0, "the boss's Stuns on you weren't read")
        up = min(1.0, stuns * duration) if chance >= 1 else 1 - (1 - chance) ** (stuns * duration)
        return up, (f"the boss Stuns you {stuns:.3f}/s ({how}), each Blind {duration:g} s: {up:.0%} up" if stuns > 0 else
                    f"the boss never Stuns you ({how}): 0")
    if "fully armour break" in when or "fully break" in when:  # (armour_broken's Full Breaks, as Exposure's)
        events = number(stats.get("_full_break_rate"))
        up = 1 - (1 - min(chance, 1.0)) ** (events * duration) if chance < 1 else min(1.0, events * duration)
        return up, f"{events:.2f} Full Breaks of the boss's Armour/s, {duration:g} s: {up:.0%} up"
    if re.search(r"\bkill", when) and not re.search(r"\bkilled\b|heavy stun|immobilis|shapeshift|burning|socketed", when):
        # "on Kill" (Intimidate nearby enemies on Melee Kill; MECHC9's note, MECH55): the build's kills of the boss's adds
        # (kill_events; none against a lone boss), the boss taken to be near the add killed (LOW); "on Melee Kill" only
        # with a Melee main skill (the kills are taken as its own, as kill_events does)
        if re.search(r"\bmelee\b", when) and "Melee" not in (stats.get("_main_types") or {"Melee"}):
            return 0.0, "on Melee Kill, and the main skill isn't Melee: 0"
        events, how = kill_events(when, stats)
        n = events * duration
        up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
        return up, f"{chance:.0%} x {how}, {duration:g} s, the boss taken to be near the kill (LOW): {up:.0%} up"
    if "heavy stun" in when and not re.search(r"\bkill|immobilis|burning|socketed|\bfrozen by\b", when):
        # (MECH85; the catalog's kw-stun entry, an UNDERCOUNT: "Enemies you Heavy Stun (while Shapeshifted) are
        # Intimidated for 6 seconds", Bashing Beast; "<debuff> Enemies on Heavy Stun"; before, 0 for every build) at the
        # main skill's Heavy Stuns of the boss (models.heavy_stun_rate: one every so many seconds, so the share is
        # min(1, rate x duration) for a sure debuff). "while Shapeshifted": counted when the main skill is a Shapeshift
        # form's skill (its types), else 0 (another skill's stuns in form aren't tracked: an undercount, LOW).
        events, how = models.heavy_stun_rate(stats)
        if "shapeshift" in when and not (SHAPESHIFT_TYPES & set(stats.get("_main_types") or ())):
            return 0.0, (f"'{when.strip()[:40]}': only Heavy Stuns while Shapeshifted count, and the main skill isn't a "
                         "Shapeshift form's skill (0)")
        if events <= 0:
            return 0.0, f"no Heavy Stuns of the boss ({how}): 0"
        up = 1 - (1 - min(chance, 1.0)) ** (events * duration) if chance < 1 else min(1.0, events * duration)
        return up, (f"the boss Heavy Stunned {events:.3f}/s by the main skill ({how}), each debuff {duration:g} s: "
                    f"{up:.0%} up (without Heavy Stuns: 0)")
    if re.search(r"\bkill|heavy stun|immobilis|shapeshift|burning|socketed|\bfrozen by\b", when):
        return 0.0, f"'{when.strip()[:30]}': its rate isn't modelled (0, labelled)"
    if "maximum endurance" in when:
        held = _charges_held("Endurance")(lines, stats)[0]
        most = number(stats.get("EnduranceChargesMax"))
        if most <= 0 or held < most:
            return 0.0, f"needs maximum Endurance Charges ({held:g} of {most:g} held)"
    if "block" in when:
        # Only the boss's blockable hits, after evasion (red-flash hits can't be Blocked; an Evaded hit
        # doesn't hit you at all: KeywordPopups "Block" / "Evasion"; boss_hits' LOW shares; MECHB6 / MECHC5)
        events, how = boss_hits.block_share(stats) / every, "the boss's hits you Block (red-flash hits can't be)"
        if "permanently" in text.lower():
            return (1.0, "permanent after a Block") if events > 0 else (0.0, "no Block chance")
    elif "hit you" in when:
        land = boss_hits.gain_hits(stats)[0]  # (red-flash hits always land, MECHC5; the worst case's lowest real, MECHC20)
        events, how = land, "the boss's hits that land (red-flash hits always do)"
    elif ("critical" in when or "on hit" in when) and var == "conditionEnemyBlinded" and stats.get("_main_no_blind"):
        # Blindside: "Supported Skills ... cannot themselves inflict Blind" (cannot_inflict_blind; the line's hits are
        # the main skill's, models.hits_per_second / crits_per_second)
        return 0.0, f"the main skill can't inflict Blind ({stats['_main_no_blind']}, Blindside): its hits Blind nothing"
    elif "critical" in when:
        events, how = models.crits_per_second(stats), "crits"
    elif "on hit" in when:
        events, how = models.hits_per_second(stats), "hits"
    elif a := WHEN_YOU_AILMENT.search(when):
        # (MECH63, COV19: Murkshaft's "Blind Targets when you Poison them"; 0 before, an UNDERCOUNT) the main skill's
        # hits that inflict the ailment: hits/s x PoB's {Ailment}ChancePerHit (crits' chance blended in, as PoB's
        # per-hit chance is); other skills' applications aren't counted (an undercount)
        ailment = {"poison": "Poison", "ignite": "Ignite", "shock": "Shock"}[a.group(1).lower()]
        per = min(number(stats.get(f"{ailment}ChancePerHit")), 100.0) / 100.0
        events, how = models.hits_per_second(stats) * per, f"{ailment} applications ({per:.0%} of the main skill's hits)"
    else:
        return 0.0, f"'{when.strip()[:40]}': its rate isn't modelled (0, labelled)"
    if q := TARGET_AILMENT.search(when):  # ("Blind Chilled enemies on Hit": only the hits on a Chilled boss; MECH53)
        share = target_ailment_share(stats, q.group(1).lower())
        events, how = events * share, f"{how} on a {q.group(1).lower()} boss (it is {share:.0%} of the fight)"
    n = events * duration
    up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
    return up, f"{chance:.0%} x {events:.2f} {how}/s, {duration:g} s: {up:.0%} up"


# A debuff line that names the target's ailment ("Blind Chilled enemies on Hit", Pandemonius; MECH53, COVD5's OVERCOUNT:
# before, read as Blinded all fight whether or not the boss was Chilled): only the hits on a boss carrying it count, at
# the share of the fight it carries it from the main skill's hits (chill_share, ailment_uptime, shock_share,
# models.immobilised_share). Chill from other skills or grounds isn't counted (labelled in the share's basis).
TARGET_AILMENT = re.compile(r"\b(chilled|frozen|shocked|ignited|poisoned|bleeding|electrocuted) (?:enemies|targets)\b",
                            re.I)
CHILL_SECONDS = 8.0  # KeywordPopups Chill: "lasts ... 8 seconds on non-players by default"


CHILL_FLOOR = 30.0  # KeywordPopups Chill: "Chills smaller than 30% will be ignored, so small Hits can fail to Chill"
# The fields chill_share reads (the Chilled box, "Chilled enemies" lines, Mana Remnants' Chilled share)
CHILL_FIELDS = ("ChillChanceOnHit", "Speed", "HitChance", "ChillDuration", "ChillEffectMod", "EnemyAilmentThreshold",
                "CritChance", "ChillHitDamage", "ChillCritDamage", *(f"{t}{a}" for t in models.DAMAGE_TYPES for a in ("HitAverage", "CritAverage")))
EVIDENCE_FIELDS.extend(f for f in CHILL_FIELDS if f not in EVIDENCE_FIELDS)
EVIDENCE_FIELDS.extend(f for f in ("IgniteDuration", "IgniteStackPotential") if f not in EVIDENCE_FIELDS)  # (MECH81)
EVIDENCE_FIELDS.extend(f for f in ("MovementSpeedMod",) if f not in EVIDENCE_FIELDS)  # (MECH83: presence_reentries' roll)
EVIDENCE_FIELDS.extend(f for f in ("PoisonDPS",) if f not in EVIDENCE_FIELDS)  # (MECH89: Corrosion's Poison Armour Break)


def chill_effect(stats: dict) -> tuple[float, float, str]:
    """(Chill magnitude of a non-critical hit, of a critical hit, the damage basis) from the main skill's hits, in %: the
    game's KeywordPopups Chill, "Chill Magnitude depends on the Chilling damage dealt relative to the target's Elemental
    Ailment Threshold", PoB's formula 100 x damage / threshold x ChillEffectMod (CalcOffence 5674; ChillEffectMultiplier
    100, Data/Misc.lua 77). The damage is every type that can Chill, as PoB sums it (the bridge's ChillHitDamage /
    ChillCritDamage: Cold unless it can't Chill, other types made able to, Shaper of Winter or the uniques letting
    Physical or all damage Chill; AUDIT25 A25, an UNDERCOUNT when it was Cold only), from PoB's {Type}HitAverage /
    CritAverage, after the boss's resistances ("dealt"; PoB's own Chill check reads the same outputs, CalcOffence
    5022-5033 / 5550). Without the bridge's totals: the cold hit, or every type's when the cold hit is 0 but PoB Chills
    (an upper bound, MEDIUM). The average hit stands for every hit (MEDIUM: a roll under the average can fall below
    30%)."""
    threshold = number(stats.get("EnemyAilmentThreshold"))
    mod = number(stats.get("ChillEffectMod")) or 1.0
    if stats.get("ChillHitDamage") is not None or stats.get("ChillCritDamage") is not None:
        hit, crit = number(stats.get("ChillHitDamage")), number(stats.get("ChillCritDamage"))
        basis = "the hit's damage of every type that can Chill (PoB's rule)"
    else:
        types, basis = ("Cold",), "the cold hit"
        if not number(stats.get("ColdHitAverage")) and not number(stats.get("ColdCritAverage")):
            types, basis = models.DAMAGE_TYPES, "the whole hit (PoB lets another type Chill; an upper bound)"
        hit = sum(number(stats.get(f"{t}HitAverage")) for t in types)
        crit = sum(number(stats.get(f"{t}CritAverage")) for t in types)
    if threshold <= 0:
        return 0.0, 0.0, basis
    return 100.0 * hit / threshold * mod, 100.0 * crit / threshold * mod, basis


def _chill_per_hit(stats: dict) -> float:
    """The chance a main-skill hit Chills the boss (chill_share's rule: PoB's ChillChanceOnHit, the game's 30% floor)."""
    if number(stats.get("ChillChanceOnHit")) <= 0:
        return 0.0
    if number(stats.get("EnemyAilmentThreshold")) > 0:
        normal, critical, _ = chill_effect(stats)
        crit = min(max(number(stats.get("CritChance")), 0.0), 100.0) / 100.0
        return (1.0 - crit) * (normal >= CHILL_FLOOR) + crit * (critical >= CHILL_FLOOR)
    return min(number(stats.get("ChillChanceOnHit")) / 100.0, 1.0)


def chill_share(stats: dict) -> float:
    """Share of the fight the boss is Chilled by the main skill's hits. PoB's ChillChanceOnHit must be on (0 when the
    skill can't Chill, Icestorm's never_chill -> CannotChill, or the hit is under a 1% Chill: CalcOffence 5550-5558).
    The game ignores Chills under 30% (KeywordPopups Chill), which PoB doesn't (its floor is ChillEffectMultiplier 100,
    a 1% Chill): a hit Chills only when chill_effect's magnitude is >= CHILL_FLOOR, a critical hit on its own
    (CritChance; MECH54, an OVERCOUNT before). Each Chill lasts PoB's ChillDuration, else 8 s. Without the boss's
    threshold in the stats, PoB's check alone (as before)."""
    if number(stats.get("ChillChanceOnHit")) <= 0:
        return 0.0
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    if number(stats.get("EnemyAilmentThreshold")) > 0:
        normal, critical, _ = chill_effect(stats)
        crit = min(max(number(stats.get("CritChance")), 0.0), 100.0) / 100.0
        chill = (1.0 - crit) * (normal >= CHILL_FLOOR) + crit * (critical >= CHILL_FLOOR)
    else:
        chill = min(number(stats.get("ChillChanceOnHit")) / 100.0, 1.0)
    if chill <= 0 or hits <= 0:
        return 0.0
    share = 1.0 - (1.0 - chill) ** (hits * (number(stats.get("ChillDuration")) or CHILL_SECONDS))
    # (a skill beside the main one that consumes Chill, Icestorm, ends it at its use rate: side_consumed; MECH56)
    return side_consumed(share, stats, "Chill", hits, chill)


def target_ailment_share(stats: dict, participle: str) -> float:
    """Share of the fight the boss carries the ailment a line names (TARGET_AILMENT's word)."""
    if participle == "chilled":
        return chill_share(stats)
    if participle == "shocked":
        return shock_share(stats)
    if participle in ("frozen", "electrocuted"):
        return models.immobilised_share(stats, "Freeze" if participle == "frozen" else "Electrocute")[0]
    return ailment_uptime(stats, {"ignited": "Ignite", "poisoned": "Poison", "bleeding": "Bleed"}[participle])


# Grinning Immolation (Infernalist): "Become Ignited when you deal a Critical Hit" (MECH53, RESEARCH12's OVERCOUNT: before,
# the Ignited-yourself box was on whenever the line was there). Ignited at the share of the fight one of your crits in the
# last Ignite's duration got through: crits per second x (1 - PoB's IgniteAvoidChance, 100 when immune: CalcDefence 2005,
# IgniteImmune / ElementalAilmentImmune) over 4 s (KeywordPopups Ignite) x PoB's SelfIgniteDuration. Avoidance taken to
# apply to this Ignite as to any (MEDIUM).
# The boss's own Ignite on you (MECH55, MECHD4's bosses.self_ignite_uptime: its landed Fire hits x boss_hits.ignite_chance
# x (1 - avoidance), 4 s x SelfIgniteDuration each, at the "when hit" benefit cadence; an UNDERCOUNT before, when only
# Grinning Immolation set the box): added, as independent, when a line reads the box (PoB's "while ignited" / "while you
# are ignited" -> Condition:Ignited, ModParser 1899-1900; Condition:Burning follows it, CalcSetup 83).
SELF_IGNITE_LINE = re.compile(r"become ignited when you deal a critical hit", re.I)
SELF_IGNITED_USE = re.compile(r"\bwhile (?:you are )?(?:ignited|burning)\b", re.I)


def grinning_ignited(stats: dict) -> tuple[float, str]:
    """(share of the fight you are Ignited by your own crits (SELF_IGNITE_LINE), how)."""
    avoid = min(max(number(stats.get("IgniteAvoidChance")), 0.0), 100.0) / 100.0
    crits = models.crits_per_second(stats)
    scale = number(stats.get("SelfIgniteDuration")) / 100 if "SelfIgniteDuration" in stats else 1.0
    seconds = AILMENT_DURATION["Ignite"] * scale
    up = 1.0 - avoid ** (crits * seconds) if crits > 0 and avoid < 1.0 else 0.0  # (no crit Ignite got through)
    if avoid >= 1.0:
        return 0.0, "you can't be Ignited (PoB's Ignite avoidance 100%: immune)"
    return up, (f"{crits:.2f} crits/s, {1 - avoid:.0%} not avoided, Ignited {seconds:.1f} s each: up {up:.0%} of the "
                f"fight ({shared(up)}; avoidance taken to apply, MEDIUM)")


def _self_ignited(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    grinning = any(SELF_IGNITE_LINE.search(t) for t in lines)
    boss, boss_how = bosses.self_ignite_uptime(stats) if any(SELF_IGNITED_USE.search(t) for t in lines) else (0.0, "")
    reflect, reflect_how = reflected_ignite_share(lines, stats)  # (MECH85: Self Immolation)
    if not grinning and boss <= 0 and reflect <= 0:
        return 0.0, ""
    parts = []
    up = 0.0
    if grinning:
        up, how = grinning_ignited(stats)
        parts.append("Grinning Immolation: become Ignited when you crit: " + how)
    if boss > 0:
        parts.append(boss_how)
        up = 1.0 - (1.0 - up) * (1.0 - boss)
    if reflect > 0:
        parts.append(reflect_how)
        up = 1.0 - (1.0 - up) * (1.0 - reflect)
    if sum(1 for x in (grinning, boss > 0, reflect > 0) if x) > 1:
        parts.append(f"together (independent): {up:.0%}")
    return _box(up, "; ".join(parts))


# Self Immolation (MECH85; the catalog's damaging-ailments entry, an OVERCOUNT of survival and an UNDERCOUNT of the "while
# Ignited" lines it is taken for): the notable reads "Ignites you cause are reflected back to you" and "40% reduced
# Magnitude of Ignite on you" (tree.json). PoB parses the second (SelfIgniteEffect INC -40) and leaves the first unparsed
# (ModCache: no mods, the whole line left over), so neither the Ignited-yourself box nor the burn was counted. Each Ignite
# you inflict lands on you too: Ignited yourself at the share of the fight the boss is Ignited (ailment_uptime), less your
# Ignite avoidance over the applications in one of your own Ignites' duration (as Grinning Immolation's: avoidance taken
# to apply to a reflected Ignite, MEDIUM, Overcharge's reading for its reflected Shocks). The burn while it lasts: your
# Ignite's damage a second before the boss's mitigation (PoB's IgniteDPS / IgniteEffMult: the boss's Fire resistance and
# damage-taken modifiers divided back out) x your "Magnitude of Ignite on you" (PoB's SelfIgniteEffect, the notable's 40%
# reduced in it), handed to PoB as "N Fire Damage taken per second" (FireDegen: your Fire resistance and damage-over-time
# taken modifiers apply there, CalcDefence 3874-3884), at that share. Only the main skill's Ignite is read (another
# group's Ignites reflect too: not counted, LOW). A reflected Ignite keeps the inflicted one's duration (the main skill's
# IgniteDuration: 4 s x your Ignite / ailment duration modifiers); "duration of Ignite on you" lines don't change it
# (MECH87, RESEARCH30 R30-8 4a, MEDIUM-LOW: poewiki's PoE1 copying rule and a PoE2 forum report; before, yours was taken,
# PoB's SelfIgniteDuration, which stays as a row). The other "reflected to you" ailment lines (Poison, Bleeding,
# Elemental Ailments other than Freeze) sit on Mutated Vaal / Exclusive mods the coverage audit finds no current carrier
# for (its reason text, UNVERIFIED): not modelled here.
# "Enemies near Enemies you Mark are Blinded" (marks27: enemies_near_marked_enemy_are_blinded; MECH87, an UNDERCOUNT:
# the Blinded box counted it 0): the Marked boss counts as near itself (RESEARCH30 R30-8 (2), MEDIUM-LOW), so it is
# Blinded while Marked (Conditions._marked_share, mark_model's share); "not Blinded" is the row in the basis.
MARK_BLIND = re.compile(r"^enemies near enemies you mark are blinded$", re.I)
SELF_IMMOLATION_LINE = re.compile(r"^ignites you cause are reflected back to you$", re.I)
# Buildup on Empowered Attacks (MECH86; the catalog's kw-stun / kw-freeze entries, UNDERCOUNTS of the boss's Heavy Stuns
# and Freezes): "Empowered Attacks have 50% increased Stun Buildup" (Bravado, warcries16:
# empowered_attack_hit_damage_stun_multiplier_+%) and "N% increased Freeze Buildup with Empowered Attacks" (cold49 /
# cold50: hit_damage_freeze_multiplier_+%_with_empowered_attacks) (checked: every Stats.json id holding empower with
# buildup / stun / freeze, and every table row using them, scratch/mech86/stat_users.py: these three passives alone).
# PoB never applies either (ModCache: the first unparsed, the second parsed with " with Empowered Attacks " left over).
# Counted as the same INC on the main skill at the share of its uses that are Empowered (Assumptions.empowered_uses,
# every Empowering skill of the build at its own use rate): a buildup INC scales each hit's buildup linearly, so the
# average hit's is exact for the bar's fill. Only for an Attack main skill (the lines name Empowered Attacks).
EMPOWERED_BUILDUP = re.compile(r"^(?:Empowered Attacks have (\d+(?:\.\d+)?)% increased (Stun|Freeze) Buildup|"
                               r"(\d+(?:\.\d+)?)% increased (Stun|Freeze) Buildup with Empowered Attacks)$", re.I)


def empowered_buildup(texts: list[str]) -> dict[str, float]:
    """{"Stun" / "Freeze": the summed INC % on Empowered Attacks} from the build's lines (EMPOWERED_BUILDUP)."""
    out: dict[str, float] = {}
    for t in texts:
        if m := EMPOWERED_BUILDUP.match(t.strip()):
            kind = (m.group(2) or m.group(4)).title()
            out[kind] = out.get(kind, 0.0) + float(m.group(1) or m.group(3))
    return out
REFLECTED_IGNITE_FIELDS = ("IgniteChancePerHit", "Speed", "HitChance", "IgniteDuration", "IgniteAvoidChance",
                           "SelfIgniteDuration", "IgniteDPS", "IgniteEffMult", "SelfIgniteEffect")
# Grinning Immolation's burn (MECH89; COV43's routed OVERCOUNT candidate, impact 15.0): "Become Ignited when you deal a
# Critical Hit, taking 15% of your maximum Life and Energy Shield as Fire Damage per second". PoB leaves the line unparsed
# (ModCache 6720: nil); _self_ignited (MECH53) turns the Ignited box on from it, so the "while Ignited" benefits counted
# and the burn didn't. Now: N% x (PoB's Life + Energy Shield) a second x the share of the fight you are Ignited by it
# (grinning_ignited: your crits, your Ignite avoidance and self-Ignite duration), handed to PoB as "1 Fire Damage taken
# per second" (PoB's degen, through your Fire resistance, as Self Immolation's reflected burn: whether resistance
# applies is UNVERIFIED, the usual rule for a Fire damage over time on you). Your "Magnitude of Ignite on you" modifiers
# (PoB's SelfIgniteEffect) are taken to scale it (an Ignite's damage; UNVERIFIED: the unscaled reading is in the note).
# With another Ignite on you as well (the boss's, Self Immolation's) the two burns are added (only the strongest Ignite
# may deal damage: then this is high, the cost side, LOW).
GRINNING_BURN = re.compile(r"become ignited when you deal a critical hit, taking (\d+(?:\.\d+)?)% of your maximum life "
                           r"and energy shield as fire damage per second", re.I)
GRINNING_FIELDS = ("Life", "EnergyShield", "CritChance", "Speed", "HitChance", "IgniteAvoidChance", "SelfIgniteDuration",
                   "SelfIgniteEffect")


def grinning_burn(lines: list[str], stats: dict) -> tuple[float, str]:
    """(Fire damage a second you take on average from Grinning Immolation's self-Ignite, before your Fire resistance,
    how); (0.0, "") without the line."""
    pct = max((float(m.group(1)) for t in lines if (m := GRINNING_BURN.search(t))), default=0.0)
    if pct <= 0:
        return 0.0, ""
    up, how = grinning_ignited(stats)
    pool = number(stats.get("Life")) + number(stats.get("EnergyShield"))
    effect = number(stats.get("SelfIgniteEffect")) / 100 if "SelfIgniteEffect" in stats else 1.0
    per = pct / 100 * pool
    burn = per * max(effect, 0.0) * up
    return burn, (f"{how}; burning {pct:g}% of your {pool:,.0f} Life + Energy Shield = {per:,.0f} Fire a second while "
                  f"Ignited, x your Magnitude of Ignite on you {effect:.0%} (UNVERIFIED; unscaled: {per * up:,.0f}) = "
                  f"{burn:,.0f} a second on average, then your Fire resistance (PoB's degen, UNVERIFIED); without the "
                  "line: none")


def reflected_ignite_share(lines: list[str], stats: dict) -> tuple[float, str]:
    """(share of the fight a reflected Ignite of yours burns you, how) for Self Immolation's line (the comment above)."""
    if not any(SELF_IMMOLATION_LINE.match(t.strip()) for t in lines):
        return 0.0, ""
    boss = ailment_uptime(stats, "Ignite")
    if boss <= 0:
        return 0.0, "Self Immolation: your Ignites reflected back to you: the boss is never Ignited, so you aren't"
    avoid = min(max(number(stats.get("IgniteAvoidChance")), 0.0), 100.0) / 100.0
    if avoid >= 1.0:
        return 0.0, "Self Immolation: your Ignites reflected back to you: you can't be Ignited (PoB's avoidance 100%)"
    chance = min(number(stats.get("IgniteChancePerHit")) / 100.0, 1.0)
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    seconds = number(stats.get("IgniteDuration")) or AILMENT_DURATION["Ignite"]  # (R30-8 4a: the inflicted duration)
    scale = number(stats.get("SelfIgniteDuration")) / 100 if "SelfIgniteDuration" in stats else 1.0
    yours = AILMENT_DURATION["Ignite"] * scale  # (the row: your "Ignite on you" duration instead)

    def landed(window: float) -> float:
        return 1.0 - avoid ** (hits * chance * window) if avoid > 0 and hits * chance > 0 else 1.0

    up = boss * landed(seconds)
    row = (f"; if your own on-you duration applied ({yours:.1f} s): {boss * landed(yours):.0%}"
           if avoid > 0 and abs(yours - seconds) > 1e-6 else "")
    return up, (f"Self Immolation: your Ignites reflected back to you: the boss Ignited {boss:.0%} of the fight, "
                f"{1 - avoid:.0%} of the reflected ones not avoided over {seconds:.1f} s (the inflicted Ignite's "
                f"duration, copied: R30-8, MEDIUM-LOW): Ignited yourself {up:.0%} ({shared(up)}; avoidance taken to "
                f"apply, MEDIUM){row}")


def reflected_ignite_burn(lines: list[str], stats: dict) -> tuple[float, str]:
    """(Fire damage a second you take on average from your own reflected Ignites, before your Fire resistance, how)."""
    up, how = reflected_ignite_share(lines, stats)
    if up <= 0:
        return 0.0, how
    eff = number(stats.get("IgniteEffMult")) or 1.0
    per = number(stats.get("IgniteDPS")) / eff
    effect = number(stats.get("SelfIgniteEffect")) / 100 if "SelfIgniteEffect" in stats else 1.0
    burn = per * max(effect, 0.0) * up
    return burn, (f"{how}; burning {per:,.0f} Fire a second before the boss's mitigation (PoB's Ignite DPS "
                  f"{number(stats.get('IgniteDPS')):,.0f} / its effective multiplier {eff:.2f}) x your Magnitude of "
                  f"Ignite on you {effect:.0%} = {burn:,.0f} a second on average, then your Fire resistance (PoB's "
                  f"degen; without the line: none)")


# On Full Mana (MECHC37; COV40's routed UNDERCOUNT: PoB's Condition:FullMana, "Are you always on Full Mana?", is set by
# nothing of ours, so "N% increased Attack / Cast Speed while on Full Mana" (17.7 / 12.0) and the rest counted 0). The
# box is on at the share of the fight the build sits at full Mana: each main-skill cast drops it by its cost, refilled at
# its Mana regeneration (mana_above_share, Zenith's reading, MECHC26), so 1 - spend / regeneration at the top, 0 when it
# spends at least what it regenerates (Mana flasks and leech not counted: a lower reading). Idle Hands' "You count as on
# Full Mana while at N% of maximum Mana or above" (PoB: FullManaPercentage, read by no module of the pinned PoB for the
# box) lowers the line to N%. Only when a line reads it ("on Full Mana"); Zenith's own model (Assumptions.zenith) takes
# PoB's share off while the box is on (PoB maps Zenith to the same condition).
FULL_MANA_USE = re.compile(r"\bon Full Mana\b", re.I)
FULL_MANA_AT = re.compile(r"^You count as on Full Mana while at (\d+(?:\.\d+)?)% of maximum Mana or above$", re.I)
FULL_MANA_FIELDS = ("Mana", "ManaCost", "Speed", "ManaRegenRecovery", "ManaPerSecondCost")


def mana_above_share(stats: dict, above: float) -> tuple[float, str]:
    """(share of the fight at or above `above` x maximum Mana, how) for a build casting its main skill at `Speed` a
    second, each cast costing `ManaCost`, regenerating `ManaRegenRecovery` a second (Zenith's reading, MECHC26)."""
    mana, cost = number(stats.get("Mana")), number(stats.get("ManaCost"))
    speed, regen = number(stats.get("Speed")), number(stats.get("ManaRegenRecovery"))
    spend = number(stats.get("ManaPerSecondCost")) or cost * speed
    if mana <= 0:
        return 0.0, "no Mana pool"
    if spend <= 0:
        return 1.0, "the main skill costs no Mana: always at the top"
    if regen <= spend:
        return 0.0, (f"spends {spend:,.1f} Mana/s against {regen:,.1f} regenerated: never held at {above:.0%} (Mana "
                     "flasks and leech not counted)")
    below = max(cost - (1 - above) * mana, 0.0) / regen
    share = max(0.0, 1.0 - below * speed) if speed > 0 else 1.0
    return share, (f"each {cost:,.1f}-Mana cast from near full ({mana:,.0f}) leaves it below {above:.0%} for {below:.2f} "
                   f"s of its {1 / speed if speed > 0 else 0:.2f} s, refilled at {regen:,.1f}/s")


def full_mana_at(lines: list[str]) -> float:
    """The share of maximum Mana that counts as Full Mana: 1, or the lowest "You count as on Full Mana while at N%"."""
    return min((float(m.group(1)) / 100 for t in lines if (m := FULL_MANA_AT.match(t.strip()))), default=1.0)


def _full_mana(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    if not any(FULL_MANA_USE.search(t) and not FULL_MANA_AT.match(t.strip()) for t in lines):
        return 0.0, ""
    at = full_mana_at(lines)
    share, how = mana_above_share(stats, at)
    return _box(share, f"on Full Mana {share:.0%} of the fight ({shared(share)}): {how}"
                + (f" (you count as on Full Mana from {at:.0%}: your line)" if at < 1 else "")
                + "; with it: keep Mana topped up; without it: the 'on Full Mana' lines count 0")


# Overcharge's reflected Shocks (MECH75; PATCH_CHECK L4-1, an OVERCOUNT of survival: neither PoB (SkillStatMap doesn't map
# shocks_reflected_to_self) nor the tool counted it). The support (PoB sup_dex.lua, game 26476662's
# SupportOverchargePlayer: shock_effect_+% 50 and shocks_reflected_to_self): "making its Shocks more effective but
# reflecting them back to you as well"; patch 0.2.0: "It now has 'Shocks caused by Supported Skills are reflected back to
# you'", the newest entry (grep_notes.py --history "Overcharge"). Each Shock a supported skill inflicts Shocks you too:
# applications a second = its hits x ShockChancePerHit (the main skill's stats; another group's bridge.useRates row at
# its own use rate: skill_use_rate, a triggered one at PoB's trigger rate), less your Shock avoidance (PoB's
# ShockAvoidChance, 100 when immune), each lasting SELF_SHOCK_SECONDS (KeywordPopups Shock: "lasts 4 seconds on players";
# your Shock-duration-on-you modifiers not read, LOW). PoB's "Are you Shocked?" box adds 15% increased damage taken
# (ConfigOptions.lua 1100); the game's base is 20% (KeywordPopups Shock), and the reflected Shock is taken as the Shock
# the skill inflicts: the base 20% x the main skill's Shock-magnitude modifiers with Overcharge's own 50% put back
# (bridge.shockMagnitude leaves it out), or 20% x 1.5 for a supported skill beside the main one; capped at the Shock
# maximum. The difference over PoB's 15 goes in as DamageTaken INC under Condition:Shocked (overcharge_taken_mods), so it
# applies only while the box is on. Labelled MEDIUM: the reflected Shock's magnitude taken as the inflicted one, then x
# your "effect of Shock on you" (PoB's SelfShockEffect; MECH83, MECHC33's probe: PoB's box ignores it, so below 75% the
# mod is negative and takes PoB's 15 down; SelfShockEffect also carries the enemy's own EnemyShockMagnitude /
# AilmentMagnitude (CalcDefence 2026), which the tool sets nowhere (checked: bosses.py, bridge.lua): LOW-MEDIUM); the box is on at the share of the fight
# (MIN_UPTIME), so the defence floors (the worse of on / off) see you Shocked. The boss's own Shocks on you aren't set here
# (not wired into the box); since MECHC37 they count as damage taken for every build at their average share
# (boss_hits.boss_ailments_on_you, DamageTaken INC), skipped while this box is on (it already counts you Shocked).
# PoB bug (MECH75; PATCH_CHECK's Mirror sweep, MD-9): its "Enemy entered Presence Recently?" box (ConfigOptions.lua 1672)
# uses the same var as "Enemy exited Presence Recently?" (1669), conditionEnemyExitedPresenceRecently, so setting it turns
# on BOTH Condition:ExitedPresenceRecently and Condition:EnteredPresenceRecently (an OVERCOUNT: the lines reading the other
# one count too). Nothing in the tool sets it today (checked: builder/ and engine/bridge.lua hold neither name). Whoever
# models them (MD-9, MECH-C) must not tick that var: set the enemy condition it means directly instead (a bridge raw
# enemy mod, as models.raw_mod does for the player), or label the double count.
PRESENCE_RECENTLY_SHARED_VAR = "conditionEnemyExitedPresenceRecently"


OVERCHARGE = "Overcharge"
SELF_SHOCK_SECONDS = 4.0
SHOCK_BASE = 20.0
POB_SELF_SHOCK = 15.0
OVERCHARGE_EFFECT = 50.0


def overcharge_sources(groups: dict, main_skill: str, stats: dict, rows: dict,
                       choices: dict[str, float] | None = None) -> list[tuple[str, float]]:
    """[(skill, Shocks it inflicts a second)] for each enabled group Overcharge supports (see the note above). `rows`:
    {group index: the bridge's useRates row}, for the groups beside the main one."""
    out = []
    main_index = groups.get("mainSocketGroup")
    for group in groups["groups"]:
        if group.get("enabled") is False or not any(
                n.get("enabled", True) and n["name"] == OVERCHARGE for n in group.get("gems") or []):
            continue
        if group["index"] == main_index:
            hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
            chance = min(number(stats.get("ShockChancePerHit")) / 100.0, 1.0)
            out.append((main_skill, hits * chance))
            continue
        row = rows.get(group["index"])
        if not row:
            continue
        uses = number(row.get("speed")) if row.get("triggered") else skill_use_rate(row, choices)[0]
        hits = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0
        chance = min(number(row.get("ShockChancePerHit")), 100.0) / 100.0
        out.append((str(row.get("name") or "a supported skill"), hits * chance))
    return out


# Redflare Conduit's "Shocks you when you reach maximum Power Charges" (MECHC32; the manager, 16 Nov 03:54, routed from
# COV36: a downside nothing counted, an OVERCOUNT of the item; PoB parses the line to no mod, ModCache 9355 {nil, ...}).
# How often: the build's own Power Charge sources reaching the maximum (_reaching_maximum: with Redflare's own "Lose all
# Power Charges on reaching maximum Power Charges", each refill, gains / maximum; else 0 once held, or each spend's
# refill). Each Shock lasts SELF_SHOCK_SECONDS (KeywordPopups Shock, "lasts 4 seconds on players"), less your Shock
# avoidance; the refills come about evenly spaced (many gains each), so its share is min(1, rate x 4 s) (the higher
# reading against evenly random: the cautious side for a downside), joined with Overcharge's as independent. Its
# magnitude: the line names none, so the game's base 20% (KeywordPopups Shock; HIGH, RESEARCH28 R28-5) x your "effect
# of Shock on you" modifiers (MEDIUM; MECHC33), against PoB's 15% box as DamageTaken INC while Shocked
# (self_shock_taken_mods; PoB's box ignores those modifiers: probed). Other "Shocks you when you reach maximum <kind> Charges" lines:
# read the same way (checked: PoB's ModCache holds only the Power one).
SELF_SHOCK_ON_MAX = re.compile(r"^Shocks you when you reach maximum (Power|Frenzy|Endurance) Charges$", re.I)


def self_shocks_on_maximum(stats: dict) -> list[tuple[str, float, str]]:
    """[(what, Shocks a second, how)] from "Shocks you when you reach maximum <kind> Charges" lines (stats["_texts"])."""
    out = []
    for text in stats.get("_texts") or []:
        if m := SELF_SHOCK_ON_MAX.match(text.strip()):
            kind = m.group(1).capitalize()
            rate, how = _reaching_maximum(kind, 1.0, stats)
            out.append((f"Shocks you on reaching maximum {kind} Charges", rate, how))
    return out


def _self_shocked(lines: list[str], stats: dict) -> tuple[object, str, float] | tuple[float, str]:
    """PoB's "Are you Shocked?" box at the share of the fight Overcharge's reflected Shocks and Redflare Conduit's Shocks
    on reaching maximum Power Charges keep you Shocked."""
    maxed = self_shocks_on_maximum(stats)
    if not stats.get("_overcharge") and not maxed:
        return 0.0, ""
    sources = [s for s in stats.get("_overcharge") or [] if s[1] > 0]
    avoid = min(max(number(stats.get("ShockAvoidChance")), 0.0), 100.0) / 100.0
    if avoid >= 1.0:
        return 0.0, ("you're Shocked by your own Overcharge or item, but you can't be Shocked (PoB's Shock avoidance "
                     "100%)")
    rate = sum(r for _, r in sources) * (1.0 - avoid)
    up = 1.0 - math.exp(-rate * SELF_SHOCK_SECONDS) if rate > 0 else 0.0
    parts = []
    if stats.get("_overcharge"):
        named = ", ".join(f"{n} {r:.2f}/s" for n, r in sources) or "no Shocks from the supported skills"
        parts.append(f"Overcharge reflects your Shocks back to you ({named}, {avoid:.0%} avoided, {SELF_SHOCK_SECONDS:g} s "
                     f"each on players): {up:.0%} (MEDIUM)")
    for what, each, how in maxed:
        share = min(1.0, each * (1.0 - avoid) * SELF_SHOCK_SECONDS)
        up = 1.0 - (1.0 - up) * (1.0 - share)
        parts.append(f"{what} ({how}; {avoid:.0%} avoided, {SELF_SHOCK_SECONDS:g} s each, the refills about evenly "
                     f"spaced): {share:.0%} (MEDIUM rate; magnitude: the game's base 20% x your Shock effect on you); without it: none")
    return _box(up, "; ".join(parts) + f": Shocked {up:.0%} of the fight ({shared(up)})")


def self_shock_taken_mods(texts: list[str], overcharge: list[dict], effect: float = 100.0,
                          inflict: float = 1.0) -> tuple[list[dict], str]:
    """Redflare's Shock (self_shocks_on_maximum) at the game's base 20% x the build's "effect of Shock on you" (`effect`:
    PoB's SelfShockEffect output, 100 = unmodified), over PoB's 15% box, while Shocked; none when Overcharge's own
    reflected-Shock mod is already there (it's the larger: its 50% more effect). `inflict`: the build's own Shock
    magnitude multiplier (its "Magnitude of Shock you inflict" lines), shown as a row only (MECHC33; RESEARCH28 R28-5).
    The probe test_pobs_shocked_box_ignores_the_effect_of_shock_on_you: PoB's box stays 15% whatever SelfShockEffect is,
    so the whole 20% is scaled here (a reduction past 25% takes part of PoB's box back off)."""
    if overcharge or not any(SELF_SHOCK_ON_MAX.match(t.strip()) for t in texts):
        return [], ""
    scale = max(effect, 0.0) / 100
    size = SHOCK_BASE * scale
    extra = size - POB_SELF_SHOCK
    how = (f"the Shock your item puts on you at the game's base {SHOCK_BASE:g}% increased damage taken (the line names "
           f"no magnitude: HIGH, RESEARCH28)" + (f" x {scale:.0%} (your \"effect of Shock on you\" modifiers, PoB's "
                                                  f"SelfShockEffect; applying them here: MEDIUM)" if abs(scale - 1) > 1e-9
                                                  else "") +
           f" = {size:g}%, {extra:+g}% against PoB's {POB_SELF_SHOCK:g}% box (which ignores them: probed), only while "
           f"you are Shocked")
    if abs(inflict - 1) > 1e-9:
        how += (f"; with your own \"Magnitude of Shock you inflict\" modifiers too (x{inflict:.2f}, UNVERIFIED whether "
                f"they apply to a Shock your item inflicts on you; the cautious row): {size * inflict:g}%")
    how += "; without it: no Shock"
    if abs(extra) < 1e-9:
        return [], how
    return [models.raw_mod("DamageTaken", "INC", extra, condition="Shocked")], how


def overcharge_taken_mods(build, main_skill: str) -> tuple[list[dict], str]:
    """The reflected Shock's damage taken over PoB's 15% box, as DamageTaken INC while Shocked (see the note above)."""
    groups = build.call("groups")
    holding = [g for g in groups["groups"] if g.get("enabled") is not False and any(
        n.get("enabled", True) and n["name"] == OVERCHARGE for n in g.get("gems") or [])]
    if not holding:
        return [], ""
    if any(g["index"] == groups.get("mainSocketGroup") for g in holding):
        mag = build.call("shockMagnitude") or {}
        raw = SHOCK_BASE * (1 + (number(mag.get("inc")) + OVERCHARGE_EFFECT) / 100) * number(mag.get("more", 1))
        top = number(mag.get("max")) or 100.0
        how = f"the main skill's Shock modifiers with Overcharge's {OVERCHARGE_EFFECT:g}%"
    else:
        raw, top = SHOCK_BASE * (1 + OVERCHARGE_EFFECT / 100), 100.0
        how = f"Overcharge's own {OVERCHARGE_EFFECT:g}% (the other modifiers of the skill beside the main one not read)"
    # (MECH83; MECHC33's probe POE2T3637: PoB's box ignores "effect of Shock on you", so the reflected Shock is scaled
    # by PoB's SelfShockEffect here, as MECHC33 does for Redflare's: 100 with no such line, 0 when unaffected by Shock)
    effect = number((build.call("stats", fields=["SelfShockEffect"]) or {}).get("SelfShockEffect", 100.0))
    value = max(0.0, min(raw, top)) * max(effect, 0.0) / 100
    extra = round(value - POB_SELF_SHOCK, 6)
    if extra == 0:
        return [], ""
    return ([models.raw_mod("DamageTaken", "INC", extra, condition="Shocked")],
            f"the reflected Shock {value:g}% increased damage taken ({SHOCK_BASE:g}% x {how}"
            + (f", x your {effect:g}% effect of Shock on you (PoB's SelfShockEffect; its box ignores it)"
               if effect != 100 else "")
            + f"), {extra:+g}% against PoB's {POB_SELF_SHOCK:g}% box, applied only while you are Shocked")


# Your curses on you (MECH84; COV38's downside sweep, an OVERCOUNT: PoB parses neither source, so the build kept its
# curses' benefit on the boss and paid nothing). Game files 26650308: stat curses_reflected_to_self, "[Curse|Curses] you
# inflict are reflected back to you" (stat_descriptions), on Mods UniqueReflectCurseToSelf1 (Shackles of the Wretched)
# and HandWrapsUniqueReflectCurseToSelf1 (a hand-wraps line), and in SupportAtzirisAllurePlayer's implicit stats beside
# curse_ignores_curse_limit (PoB's sup_int.lua: "Supports Curse Spells you cast yourself, causing those Curses to ignore
# the usual Curse Limit, but be reflected back to you when inflicted"; PoB maps its 20% less Curse effect and
# curse_ignores_curse_limit (SkillStatMap 1720), not curses_reflected_to_self).
# (checked: every table row naming the stat: those two Mods and the support; reflect_curses / map_*_curses_are_reflected
# are other stats (Curse Reflection: monsters' and maps'); PoB ModCache 7068 / 9903 hold the item line as leftover text
# only, and no PoB module reads curses_reflected_to_self; patch notes: 0.4.0 Atziri's Allure 20% less (was 25%), 0.5.0
# Shackles of the Wretched's rolls changed, no entry removing the reflection.) Model: each player curse PoB puts on the
# boss (bridge.cursesInflicted: its curse slots; a Mark isn't a Curse, checked: KeywordPopups Mark: "Marks are a family of
# Debuffs", Curse its own entry; PoB's isMark keeps them apart; poe2db not checked) that the item line covers (all of them) or Atziri's Allure
# supports goes on you through PoB's own "You are Cursed with" path (an ExtraCurse with applyToPlayer, CalcPerform
# 2957-2987: the curse skill at its level, your "effect of Curses on you" and AvoidCurse applied, PoB's Cursed condition
# set). The reflected curse is taken as the curse you inflict: your own curse-effect modifiers for it (INC CurseEffect,
# MORE CurseEffect x Magnitude, Atziri's 20% less among them) go in as CurseEffectOnSelf for that skill, added to your
# "effect of Curses on you" as PoB adds the boss's (CalcPerform 2503); the boss's 50% less (Unique) doesn't apply to you
# (KeywordPopups Curse: "50% less Curse effect on Unique monsters"; you aren't one). MEDIUM: that the reflected copy
# carries your curse-effect modifiers is UNVERIFIED (the lower row: your own "effect of Curses on you" only); gem quality
# isn't carried (PoB's path builds the curse at quality 0); it lasts while the curse is on the boss: models.curse_model's
# uptime scale ("more Effect of your Curses" for that skill) is in the MORE carried over, so a curse up half the fight
# reflects at half effect, the same averaging as on the boss. Enfeeble on you is less damage dealt and Temporal Chains a slow (PoB applies both through the curse's own mods);
# the elemental / chaos curses lower your resistances (the defence floors see it).
CURSE_REFLECT = re.compile(r"^curses you inflict are reflected back to you$", re.I)
ATZIRIS_ALLURE = "Atziri's Allure"


def reflected_curse_mods(texts: list[str], curses: list[dict]) -> tuple[list[dict], str]:
    """(mods, how) putting your reflected curses on you (see the note above). `curses`: bridge.cursesInflicted's list."""
    every = any(CURSE_REFLECT.match(t.strip()) for t in texts)
    mods, parts = [], []
    for curse in curses:
        if not (every or curse.get("atziri")):
            continue
        name = str(curse.get("name") or "")
        if not name or not curse.get("skillId"):
            continue
        inc, more = number(curse.get("inc")), number(curse.get("more", 1.0))
        mods.append({"kind": "Raw", "name": "ExtraCurse", "type": "LIST",
                     "value": {"skillId": curse["skillId"], "level": int(number(curse.get("level")) or 1),
                               "applyToPlayer": True}})
        if inc:
            mods.append(models.raw_mod("CurseEffectOnSelf", "INC", inc, skill=name))
        if more > 0 and abs(more - 1.0) > 1e-9:
            mods.append(models.raw_mod("CurseEffectOnSelf", "MORE", (more - 1.0) * 100, skill=name))
        why = "Atziri's Allure" if curse.get("atziri") else "Curses you inflict are reflected back to you"
        parts.append(f"{name} (level {int(number(curse.get('level')) or 1)}, your {inc:+g}% / x{more:.2f} curse effect; "
                     f"{why})")
    if not parts:
        return [], ""
    return mods, ("on you as well as the boss: " + "; ".join(parts) + ". Through PoB's \"You are Cursed with\" path, "
                  "with your \"effect of Curses on you\"; without the reflection the build would keep the curse's "
                  "benefit for free. The lower row: your own curse-effect modifiers not carried (UNVERIFIED which)")


# "Your Hits cannot Stun enemies" (MECH84; COV38's downside sweep, an OVERCOUNT: PoB parses it as leftover text only,
# ModCache 9975, so the build's Heavy Stuns and Light Stuns of the boss still counted). Game files 26650308: stat
# cannot_stun, "Your [Hit|Hits] cannot [Stun] enemies" (stat_descriptions), on Mods HandWrapsUniqueCannotImmobilise1;
# the other rows naming it are monster skills' stat sets (checked: every table field naming the stat: that one item Mod
# plus ~80 GrantedEffectStatSets ImplicitStats of monster skills; PoB's ModCache holds the line once, 9975;
# poe2db not checked). KeywordPopups Stun: "There are two types of Stun:
# Light Stuns ... Heavy Stuns", so both stop. PoB's own flags: CannotHeavyStun zeroes HeavyStunBuildupAvg (CalcOffence
# 5584-5588: "Cannot"..ailment), which models.heavy_stun_rate reads, and CannotStun, which PoB reads nowhere (checked:
# grep over PoB's Modules) and the bridge's "Flag:CannotStun" stat hands to models.light_stun_rate. Freeze, Electrocute,
# Pin and Daze aren't Stuns and keep their buildup. Read as all your skills' hits (totems' too, as PoB's flag does: LOW
# for a totem's hits); minions' hits aren't yours.
CANNOT_STUN = re.compile(r"^your hits cannot stun enemies$", re.I)


def cannot_stun_mods(texts: list[str]) -> tuple[list[dict], str]:
    """(mods, how) for "Your Hits cannot Stun enemies" (see the note above)."""
    if not any(CANNOT_STUN.match(t.strip()) for t in texts):
        return [], ""
    return ([models.raw_mod("CannotHeavyStun", "FLAG", 1), models.raw_mod("CannotStun", "FLAG", 1)],
            "Your Hits cannot Stun enemies: the boss is never Heavy Stunned or Light Stunned by your hits (PoB's "
            "CannotHeavyStun zeroes the Heavy Stun buildup; Light Stuns read the flag); its \"when you Stun\" and "
            "\"against Heavy Stunned enemies\" lines count nothing")


def debuff_uptime(var: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def uptime(lines: list[str], stats: dict) -> tuple[object, str]:
        main_types = stats.get("_main_types") or set()
        down, how = 1.0, []
        for text in lines:
            # ("Warcries Debilitate Enemies" is the Warcries' own effect, not a line for a Warcry main skill only)
            if (not qualifies(text, main_types) and not WARCRIES_SUBJECT.search(text)) or (
                    r := _debuff_source(text, var, lines, stats)) is None:
                continue
            down *= 1 - r[0]
            how.append(f"{text[:60]} ({r[1]})")
        if extra := (stats.get("_debuff_extra") or {}).get(var):  # (MECH76: a skill's own debuff, e.g. Shattering Roar)
            down *= 1 - min(max(extra[0], 0.0), 1.0)
            how.append(extra[1])
        if not how:
            return 0.0, ""
        up = 1 - down
        name = DEBUFFS[var][1].capitalize()
        basis = f"{name} up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
        return _box(up, basis)
    return uptime


# Her Declaration (MECH83; MECH82's Handover step 3; before, it counted 0: an UNDERCOUNT). A lineage support, new in 0.5.0
# (patch-notes archive --history "Declaration": "New Lineage Support: Her Declaration", no later entry; no later hit isn't
# proof of no change). GemEffects HerDeclarationSupport: "Supports Persistent Buff Skills, causing enemies that enter your
# Presence to become Intimidated while the Skill is active"; GrantedEffectStatSets SupportHerDeclarationPlayer (26650308):
# support_mothers_declaration_intimidate_enemies_when_they_enter_your_presence_base_duration_ms 4000 (PoB's other.lua 8548:
# the same, requireSkillTypes Persistent AND Buff; nothing in PoB reads the stat: SkillStatMap and the Calc modules have
# no hit for it). Intimidate (KeywordPopups): "10% increased damage taken and 10% reduced damage dealt" (PoB's
# conditionEnemyIntimidated). Counted: with the support in an enabled group beside an enabled Persistent Buff gem (gems'
# skillTypes; a Persistent Buff stays active), each entry of the boss into your Presence (models.presence_reentries: the
# build's rolls out and back in; 0 when it stays outside or the roll can't leave it, CLAUDE.md) Intimidates it for 4 s x
# the "increased Intimidate Duration" lines; entries taken as random in time, so Intimidated 1 - e^(-rate x 4 s) of the
# fight (the lower reading: rolls every DODGE_EVERY s are regular, which would give min(1, rate x 4 s)); combined with
# the box's other sources as independent (debuff_uptime).
HER_DECLARATION = "Her Declaration"
HER_DECLARATION_SECONDS = 4.0  # support_mothers_declaration_..._base_duration_ms 4000


def her_declaration(groups: list[dict], gems: dict, texts: list[str], stats: dict,
                    metres: float) -> tuple[float, str] | None:
    """(share of the fight the boss is Intimidated by Her Declaration, how), None without the support on a Persistent
    Buff (HER_DECLARATION's note)."""
    buffs = []
    for group in groups:
        if group.get("enabled") is False:
            continue
        present = [g["name"] for g in group.get("gems") or [] if g.get("enabled", True)]
        if HER_DECLARATION in present:
            buffs += [n for n in present if not (gems.get(n) or {}).get("support")
                      and {"Persistent", "Buff"} <= set((gems.get(n) or {}).get("skillTypes") or [])]
    if not buffs:
        return None
    rate, how = models.presence_reentries(texts, stats, metres)
    duration = HER_DECLARATION_SECONDS * models.duration_scale(list(texts), "Intimidate")
    up = 1 - math.exp(-rate * duration) if rate > 0 else 0.0
    return up, (f"Her Declaration on {', '.join(sorted(set(buffs)))}: the boss Intimidated {duration:g} s each time it "
                f"enters your Presence ({how}): {up:.0%} of the fight (entries taken as random in time, the lower "
                "reading)")


# Dazed (MECH70; plans/term-dazed-enemies.md, an UNDERCOUNT before: nothing set PoB's conditionEnemyDazed box, so every
# "against Dazed Enemies" line counted 0: Stupefy, Dizzying Hits, their small passives, Shattering / Electrifying Daze,
# the Amanamu wraps' Phys as Cold). Game files (26476662) KeywordPopups Daze: "a chance to apply Daze to enemies on Hit.
# Daze lasts for 8 seconds, and a Dazed enemy will take 50% more Stun Buildup". Sources read, each on the main skill's
# hits: "X% chance to Daze on Hit" / "Dazes on Hit" lines (one on a weapon only for an Attack main skill: PoB's
# {Hand}Attack, the weapon-local stats); "X% chance to inflict Daze with Hits against Enemies further than 6m" (Headshot,
# projectile_daze_chance_%_vs_enemies_further_than_6m: a Projectile main skill with the boss past 6 m); the main skill's
# own base_chance_to_daze_% (PoB's levelStats: Wind Blast 100, whose text says "based on how close they are to you", a
# falloff no file gives, so 100 is a possible OVERCOUNT for a far Wind Blast, labelled; Siphoning Strike's quality);
# Concussive Spells on a Spell main skill ("Daze on Critically Hitting enemies with Physical damage": its crits while it
# deals Physical damage). Each as the debuffs' 1 - (1 - chance)^(events x duration), combined as independent, 8 s x the
# "Daze Duration" lines (Seeing Stars). A main skill with cannot_daze (Vaulting Impact's sets) applies none.
# Not counted (0, labelled, an UNDERCOUNT): Opening Move (only on a boss at full Life, the fight's opening hit; 8 s of
# the fight), Dazing Blocks (raised-shield blocks aren't rated here), Thorns' and a Tower Shield's Shield Bash Daze
# (0.2.0 notes), other skills' hits. Daze Magnitude isn't read (UNVERIFIED that it scales the 50%; the buildup bonus is
# the stun model's, MECH-D). The boss can be Dazed: cannot_be_dazed has no Mod, passive or stat-set user (checked:
# Stats ids holding daze / dazing / dazed, 49, and every Mods / PassiveSkills / GrantedEffectStatSets user of each,
# scratch/mech70/daze2.py; poe2db's Daze page names no boss rule, per the plan) (MEDIUM: an absence).
# No consumer is subtracted: no gem the search can socket consumes Daze (checked: daze2.py's users of every stat holding
# consume and daze: consume_enemy_daze_to_always_hit only SupportDazzlePlayer, a support PoB's gem data (bb52d6b368)
# doesn't hold (grep "Dazzle" over vendor/PathOfBuilding-PoE2/src/Data: none); consume_enemy_dazed_to_gain_hit_damage_
# stun_multiplier_+%_final only VaultingImpactExileMonk1, a monster's set (the player's VaultingImpactPlayer holds
# cannot_daze, not it); the other four consume_*daze* stats have no user; patch 0.3.0 (grep_notes.py --history "Vaulting
# Impact", newest entry 0.5.1, a controller fix): Vaulting Impact "escalates Daze ... until the Daze Expires", no
# consume). Dazing Cry and Break Posture aren't in PoB's gem data either (same grep), so they aren't sources here.
DAZE_SECONDS = 8.0
DAZE_LINE = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance to (?:inflict )?daze(?: enemies)? (?:on hit|with hits)(.*)|dazes on hit)$",
                       re.I)
DAZE_SKILL_STATS = ("base_chance_to_daze_%", "cannot_daze", "spell_daze_on_critical_hit_dealing_physical_damage",
                    "daze_on_hit_vs_enemies_at_full_life")
DAZE_WORD = re.compile(r"\bdaz(?:e|es|ed|ing)\b", re.I)
# (MECH71; MECH73, AUDIT35 A35-2) A skill beside the main one whose uses hit the boss, for _daze_others: decided by the
# skill's own hit, the bridge's hitsOnUse on each of the gem's active effects (PoB's hit rule, CalcActiveSkill 179:
# baseFlags.hit or the Attack / Damage / Projectile type; not a Persistent buff, a minion maker, a Wall, a damage-over-
# time-only spell, or a stat set the game marks display_statset_no_hit_damage). A gem counts if any of its effects hits
# when used: Seismic Cry (baseFlags.hit), Iron Ward (its separate nova effect IronWardNovaPlayer, used by pressing it),
# Plague Bearer (its nova effect: the Damage type, base_chance_to_poison_on_hit_%, a hit stun multiplier; game files
# GrantedEffectStatSets PlagueBearerNovaPlayer). Checked one by one (PoB act_*.lua and GrantedEffectStatSets, 14 Nov):
# - Fortifying Cry: its cry holds display_statset_no_hit_damage; its hit is Shield Wave (FortifyingCryShockwavePlayer),
#   Triggered by the Shield Attacks after the cry: not counted (0, an UNDERCOUNT for a build Dazing with shield attacks
#   after Fortifying Cry: the Shield Wave rate isn't modelled);
# - Herald of Ice / Thunder / Blood: Persistent; they hit only after a Shatter / a kill of a Shocked enemy / a kill of a
#   Bleeding one (their descriptions): 0 on a lone boss (the boss's death ends the fight; adds aren't modelled here);
# - Combust: Infernal Cry's Triggered effect on an enemy's death, no gem of its own: 0 on a lone boss, as above;
# - Arctic Armour: Persistent; its burst hits the attacker when a Melee Attack hits you: 0 here (the boss's melee hits on
#   you aren't read for it; an UNDERCOUNT, labelled);
# - Briarpatch: Persistent; its Thorny Ground hurts moving enemies: 0 (an UNDERCOUNT if it hits, UNVERIFIED);
# - Black Powder Blitz (Shankgonne's "Grants Skill"): item-granted. Its reservation effect is Persistent, but its explosion,
#   BlackPowderBlitzPlayer, has the Damage and Area types and a cooldown (PoB act_str.lua 1686-1694: 4.5 s at level 1,
#   3.5 s at 20), so it hits on use. MECH75 (AUDIT35 A35-2): the hitters are looked up in every gem (ctx.all_gems), not
#   only the gems the search sockets, so an item- or tree-granted skill counts, at its group's rate (its cooldown: a dodge
#   roll used on cooldown, MEDIUM). Before, 0 (an UNDERCOUNT). Its Ignites reach ignite_others the same way. Labelled: its
#   explosion is taken to reach the boss (it hits "the area you escape from": you roll from beside the boss; a build
#   that keeps its distance would roll out of reach, an OVERCOUNT there), and the roll's own time isn't taken off the
#   main skill (a small OVERCOUNT).
# - Crackling Palm (Thunderfist): item-granted too, now looked up the same way; its bolts fire on unarmed melee hits, at
#   the rate crackling_palm_rate gives (MECH76; before, 0: Persistent).
# Without hitsOnUse (an old recording or a test's fake) the MECH71 rule stands: an Attack, or a Spell with the Damage type,
# and not one of DAZE_NOT_HITS.
DAZE_NOT_HITS = {"CreatesMinion", "Wall", "DegenOnlySpellDamage", "Persistent"}

# Crackling Palm (MECH76; AUDIT35 A35-2, AUDIT36: a Persistent skill, so the hit rule gave its bolts 0). Its Bolt set's
# text (crackling_palm/statset_1.csd, game 26476662): "Creates a bolt of lightning when you Hit with Unarmed Melee
# damage, no more than {0} times every {1} seconds" (thunderfist_number_of_bolts_delays 5, thunderfist_bolt_delay_ms
# 500: at most 10 a second). Patch notes --history "Crackling Palm": 0.4.0 (15% crit, 40% more Lightning; newest).
# The bolts land at the main skill's hits a second (models.hits_per_second) when it is a Melee Attack hitting Unarmed
# (the bridge's LedgerUnarmedMelee), capped at 10 a second; 0 otherwise (another group's unarmed hits aren't read: an
# UNDERCOUNT where a side skill is the unarmed one). Each bolt taken to hit the boss (it strikes around you: melee range).
CRACKLING_PALM = "Crackling Palm"
CRACKLING_PALM_CAP = 5 / 0.5  # bolts a second (5 every 0.5 s)


def crackling_palm_rate(stats: dict, unarmed: dict) -> tuple[float, str]:
    """(Crackling Palm's bolts a second, how): see the note above."""
    if not number((unarmed or {}).get("LedgerUnarmedMelee")):
        return 0.0, "the main skill doesn't hit with Unarmed Melee damage: no bolts (0)"
    hits = models.hits_per_second(stats)
    rate = min(hits, CRACKLING_PALM_CAP)
    return rate, (f"a bolt per Unarmed Melee hit of the main skill ({hits:.2f}/s), at most {CRACKLING_PALM_CAP:g}/s: "
                  f"{rate:.2f}/s")


def daze_hitter(gem: dict | None, effects: list[dict] | None = None) -> bool:
    """Whether a gem beside the main skill hits the boss when used (DAZE_NOT_HITS's note). `effects`: the bridge's
    activeSkills entries with the gem's name (any with hitsOnUse decides it)."""
    if not gem or gem.get("support"):
        return False
    flags = [e["hitsOnUse"] for e in effects or () if "hitsOnUse" in e]
    if flags:
        return any(flags)
    types = set(gem.get("skillTypes") or ())
    return not types & DAZE_NOT_HITS and ("Attack" in types or {"Spell", "Damage"} <= types)


def _daze_source(text: str, lines: list[str], stats: dict, types: set | None = None,
                 hits: float | None = None) -> tuple[float, float, str] | None:
    """(chance per event, events per second, how) of one Daze line on the main skill's hits (or another skill's: its
    types and hits per second, MECH71), or None if it isn't one."""
    m = DAZE_LINE.match(text.strip())
    if not m:
        return None
    main_types = (stats.get("_main_types") or set()) if types is None else types
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    rest = (m.group(2) or "").lower()
    if text in (stats.get("_daze_weapon_lines") or ()) and "Attack" not in main_types:
        return 0.0, 0.0, "a weapon's own line, and the main skill isn't an Attack: 0"
    if "further than" in rest:
        if "Projectile" not in main_types:
            return 0.0, 0.0, "projectiles past 6 m, and the main skill isn't a Projectile skill: 0"
        metres = _boss_distance(lines, stats)[0] / 10
        if metres <= 6:
            return 0.0, 0.0, f"projectiles past 6 m, the boss {metres:g} m away: 0"
    elif rest.strip():
        return 0.0, 0.0, f"'{rest.strip()[:30]}': its rate isn't modelled (0, labelled)"
    return chance, models.hits_per_second(stats) if hits is None else hits, "hits"


def _daze_others(lines: list[str], stats: dict) -> list[tuple[str, tuple[float, float, str]]]:
    """(MECH71) Daze from the build's other hitting skills (stats["_daze_others"], Conditions.evidence): each at its
    group's use rate, one hit on the boss per use (MEDIUM: a use that hits more than once is an UNDERCOUNT, one that
    misses the boss an OVERCOUNT), with the global Daze lines (_daze_source with that skill's types: a weapon's own line
    only for an Attack) and its own base_chance_to_daze_% (its level / quality stats and supports: Wind Blast, Siphoning
    Strike's quality). A skill with cannot_daze applies none; Concussive Spells and Opening Move on another skill aren't
    counted (0, an UNDERCOUNT: its crits aren't rated here; one opening hit)."""
    found = []
    for other in stats.get("_daze_others") or []:
        if other.get("cannot") or number(other.get("uses")) <= 0:
            continue
        name, uses, types = other["name"], number(other["uses"]), set(other.get("types") or ())
        for text in lines:
            r = _daze_source(text, lines, stats, types=types, hits=uses)
            if r is not None and r[1] > 0:
                found.append((f"{name}: {text}", (r[0], r[1], f"{name} hits")))
        if other.get("chance"):
            found.append((f"{name}'s own {other['chance']:g}% chance to Daze",
                          (min(other["chance"], 100) / 100, uses, f"{name} hits")))
    return found


def _dazed(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Dazed box at the share of the fight the boss carries Daze from the main skill's hits (see the note above)."""
    own = stats.get("_daze") or {}
    found = [(text, r) for text in lines if (r := _daze_source(text, lines, stats)) is not None]
    if own.get("chance"):
        found.append((f"the main skill's own {own['chance']:g}% chance to Daze",
                      (min(own["chance"], 100) / 100, models.hits_per_second(stats), "hits")))
    if own.get("concussive"):
        physical = _hit_average(stats, ("Physical",), crit_only=True) > 0
        spell = "Spell" in (stats.get("_main_types") or set())
        found.append(("Concussive Spells: Daze on spell crits with Physical damage",
                      (1.0, models.crits_per_second(stats) if physical and spell else 0.0,
                       "crits with Physical damage" if physical else "crits, and it deals no Physical damage")))
    if own.get("opening"):
        found.append(("Opening Move: Daze on hits against a boss at full Life",
                      (0.0, 0.0, "only the fight's opening hit, not counted (an UNDERCOUNT, 8 s of the fight)")))
    others = _daze_others(lines, stats)
    if not found and not others:
        return 0.0, ""
    duration = DAZE_SECONDS * models.duration_scale(lines, "Daze")
    if own.get("cannot"):
        if not others:
            return False, ("Dazed 0% of the fight: the main skill can't Daze (cannot_daze), and no other skill of the "
                           "build Dazes; sources: " + "; ".join(t[:50] for t, _ in found[:3]))
        found = []  # (the main skill's hits apply none; the other skills' still Daze the boss)
    found = found + others
    down, how = 1.0, []
    for text, (chance, events, what) in found:
        n = events * duration
        up = (1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)) if events > 0 else 0.0
        down *= 1 - up
        how.append(f"{text[:60]} ({chance:.0%} x {events:.2f} {what}/s, {duration:g} s: {up:.0%} up)" if events > 0
                   else f"{text[:60]} ({what})")
    up = 1 - down
    stance = ""
    if (broke := stats.get("_broken_stance")) and up > 0:
        # (MECH74, AUDIT35 A35-3: "Enemies with Broken Stance cannot be Dazed again", KeywordPopups BrokenStance. One
        # second pass: Broken Stance's window from this share (models.broken_stance_window), and the share of the fight
        # it blocks a new Daze taken off at the share the boss would have been Dazed then, up x (1 - blocked): MEDIUM,
        # the sources taken as independent of the window; not iterated further)
        _, over, _, window, _ = models.broken_stance_window(number(broke.get("vault_hits")), up,
                                                             number(broke.get("hits")), int(number(broke.get("limit"))
                                                                                            or models.STANCE_HITS))
        if over > 0:
            first, up = up, up * (1 - over)
            stance = (f"; Vaulting Impact's Broken Stance ({window:.1f} s windows) blocks a new Daze {over:.0%} of the "
                      f"fight: {first:.0%} -> {up:.0%}")
    return _box(up, f"Dazed {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:4]) + stance
                + (" (the main skill can't Daze: cannot_daze)" if own.get("cannot") else ""))


# Armour Broken (MECH28, 9 Nov 2026; before, any line that Breaks Armour turned the box on, partial breaks too). The game
# files' KeywordPopups ArmourBreak: Breaking lowers a target's Armour; at 0 it is Fully Broken for 12 seconds (non-players;
# then +20% Physical damage taken from Hits, which is what PoB's box applies). Each line's Armour broken per second, from
# the main skill's hits (PoB's average hit and crit per damage type), against the boss's Armour (PoB's enemy Armour): it
# takes armour / rate s to Fully Break, then 12 s Broken, so up 12 / (12 + that). Partial Break doesn't wear off
# before it's full while the main skill hits at least once every 12 s (MECH81, PATCH_CHECK L5-o1, checked: KeywordPopups
# ArmourBreak (26650308) gives a duration only for the Full Break; patch 0.2.0e "Fixed a bug where partial Armour Break
# had an infinite duration" (so it expires); poe2wiki Fully broken: "Reapplying Armour Break on a target will refresh the
# duration of the debuff", and guides give the partial break the same 12 s; MEDIUM: a build breaking less often than
# once per 12 s would lose it, not modelled, an edge case no repeated-hit main skill meets). Full Breaks on Heavy Stun at heavy_stun_rate; "Break #% of Armour
# when Freezing or Electrocuting" at buildup_rate. Minions' breaks, Parried, Maimed and Poison wordings, and Frenzy Charge
# consumption: not rated (0, labelled). The box is on at UPTIME, else explicitly off with its basis.
ARMOUR_BREAK_LINE = re.compile(r"\bbreaks? (?:enemy )?armour\b|\bbreaks? [\d,.]+ armour|\bfully break\w* armour|"
                               r"\bbreak [\d.]+% of armour", re.I)
ARMOUR_BREAK_NOT_SOURCE = re.compile(r"to below 0|when this weapon fully breaks|cannot break|dnt-unused", re.I)
# The main skill's own and its supports' stats that Break Armour, as the line each would be (Armour Break I-III, Chaos
# Mastery-style "Chaos damage Breaks Armour", the flat apply_X_armour_break_on_hit); gem descriptions aren't read
BREAK_STATS = {
    "armour_break_physical_damage_%_dealt_as_armour_break": "Break Armour equal to {}% of Physical Damage dealt",
    "armour_break_damage_%_dealt_as_armour_break": "Break Armour equal to {}% of Hit Damage dealt",
    "armour_break_fire_damage_%_dealt_as_armour_break": "Break Armour equal to {}% of Fire Damage dealt",
    "chaos_damage_%_dealt_as_armour_break": "Break Armour equal to {}% of Chaos Damage dealt",
    "apply_X_armour_break_on_hit": "Hits Break {} Armour",
    # (MECH87: Styrn's Ferocity, "Hits against targets Crushed by Supported Skills Break Armour equal to 10% of Physical
    # damage dealt": the supported main skill keeps its target Crushed (8 s a hit), so its hits count it. PoB's own
    # reading, SkillStatMap 2980's CanArmourBreak under the enemy's Crushed, moves no DPS: probe POE2T4088, Shield Charge
    # 137.99 with and without the box, so this counts it once)
    "crushed_target_%_physical_damage_taken_as_armour_break": "Break Armour equal to {}% of Physical Damage dealt",
    # (MECH89, an UNDERCOUNT before: _armour_break_source gave every Poison wording 0) Corrosion: "Poison applied by
    # supported Skills also Breaks Armour equal to {0}% of Poison damage" (gem_stat_descriptions; SupportCorrosionPlayer).
    # Read as that share of the skill's Poison damage a second, PoB's PoisonDPS (all its stacks, after the boss's Chaos
    # resistance, CalcOffence 5398-5406: MEDIUM, "damage" read as dealt). Poisonburst Arrow's alternate quality carries
    # the same stat; the builder sets no alternate qualities.
    "armour_break_for_%_of_poison_damage_over_poison_duration": "Poison Breaks Armour equal to {}% of Poison damage",
}
BREAK_DAMAGE = {"physical": ("Physical",), "fire": ("Fire",), "chaos": ("Chaos",), "hit": models.DAMAGE_TYPES,
                "damage from hits": models.DAMAGE_TYPES}


def _hit_average(stats: dict, types: tuple[str, ...], crit_only: bool = False) -> float:
    crit = 1.0 if crit_only else min(number(stats.get("CritChance")), 100) / 100
    return sum(number(stats.get(f"{t}HitAverage")) * (1 - crit) + number(stats.get(f"{t}CritAverage")) * crit for t in types)


def _armour_break_source(text: str, stats: dict) -> tuple[float, float, float, str] | None:
    """(Armour broken per second, share of the Armour broken per second, Full Breaks per second, how) of one line, or None
    if it isn't a source for you."""
    if not ARMOUR_BREAK_LINE.search(text) or ARMOUR_BREAK_NOT_SOURCE.search(text):
        return None
    low = text.lower()
    if re.match(r"minions?\b", low):
        return 0.0, 0.0, 0.0, "Minions' breaks: not counted (0)"
    if m := re.search(r"breaks armour equal to ([\d.]+)% of poison damage", low):  # (MECH89: Corrosion)
        poison = number(stats.get("PoisonDPS"))
        per = float(m.group(1)) / 100 * poison
        return per, 0.0, 0.0, (f"{m.group(1)}% of its Poison damage, {poison:,.0f} a second (PoB's PoisonDPS: all stacks, "
                               "after the boss's resistance; MEDIUM)")
    if re.search(r"parried|maim|poison|frenzy charge", low):
        return 0.0, 0.0, 0.0, "its rate isn't modelled (0, labelled)"
    if "heavy stun" in low and "fully break" in low:
        rate = models.heavy_stun_rate(stats)[0]
        return 0.0, 0.0, rate, f"a Full Break per Heavy Stun, {rate:.3f}/s (heavy_stun_rate)"
    if m := re.search(r"break ([\d.]+)% of armour when (?:freezing|electrocuting)", low):
        rate = models.buildup_rate(stats, "Freeze") + models.buildup_rate(stats, "Electrocute")
        share = float(m.group(1)) / 100
        return 0.0, share * rate, 0.0, f"{share:.0%} of the Armour per Freeze or Electrocute, {rate:.3f}/s"
    crit_only = "critical hit" in low
    events = models.crits_per_second(stats) if crit_only else models.hits_per_second(stats)
    if m := re.search(r"([\d.]+)% of maximum runic ward", low):
        per = float(m.group(1)) / 100 * number(stats.get("Ward"))
        what = "of your Runic Ward (PoB)"
    elif m := re.search(r"equal to (?:\(?[\d.]+-)?([\d.]+)\)?% of (physical|fire|chaos|hit|damage from hits)", low):
        per = float(m.group(1)) / 100 * _hit_average(stats, BREAK_DAMAGE[m.group(2)], crit_only)
        what = f"of its {m.group(2).replace('damage from hits', 'hit')} damage (PoB)"
    elif m := re.search(r"breaks? ([\d,.]+) armour", low):
        per, what = float(m.group(1).replace(",", "")), "flat"
        if "stunning" in low:
            events = models.heavy_stun_rate(stats)[0]
            what += ", per Heavy Stun (light stuns not counted)"
    else:
        return 0.0, 0.0, 0.0, "its amount isn't read (0, labelled)"
    return per * events, 0.0, 0.0, f"{per:,.0f} per {'crit' if crit_only else 'hit'} {what} x {events:.2f}/s"


# How much Armour each Break takes (MECH66; FIX_QUEUE COVE11 leftovers, an UNDERCOUNT before): game files
# (stat_descriptions.csd, 26476662) armour_break_amount_+% "Break {0}% increased Armour" (PoB parses it as
# ArmourBreakPerHit INC, which scales only PoB's own per-skill Armour Break output, not the lines this model reads) and
# gem_stat_descriptions.csd support_increased_armour_break_armour_break_amount_+%_final "Supported Skills Break {0}% more
# Armour" (Armour Demolisher I 50 / II 70, Uruk's Smelting 70: GrantedEffectStatSets; PoB maps it to ArmourBreakEffect,
# which no PoB calculation reads: checked grep of PoB's src). Read on the main skill (stats["_break_more"]). Applied to
# the Armour each hit / Freeze breaks, not to Full Breaks ("Fully Break" is all of it). Not read (0, labelled): "Hits
# Break X% increased Armour on targets with Ailments", Overgrown Plant skills' when-Wet line.
BREAK_INCREASED = re.compile(r"^Break (\d+(?:\.\d+)?)% (increased|reduced) Armour$", re.I)
BREAK_MORE_SUPPORTS = ("Armour Demolisher I", "Armour Demolisher II", "Uruk's Smelting")
BREAK_MORE_STAT = "support_increased_armour_break_armour_break_amount_+%_final"


# Return to Nature's "Overgrown Plant Skills Break 50% increased Armour" (MECH82; the manager 10:02, GEM15 / GEM16's
# routing, an UNDERCOUNT: PoB's ModCache has no entry for it). Counted on a Plant main skill (PoB's skillTypes "Plant":
# Briarpatch, Entangle, Thrashing Vines, Toxic Domain, Toxic Growth, Vine Arrow) at the share of its plants that are
# Overgrown, GEM16's models.overgrown_share (Thunderstorm, Boundless Growth, Idol of Yeena), which the evidence reads
# into stats["_overgrown"]; 0 where nothing Overgrows them, or before GEM16's function is on this branch (labelled).
BREAK_OVERGROWN = re.compile(r"^Overgrown Plant Skills Break (\d+(?:\.\d+)?)% (increased|reduced) Armour$", re.I)


def break_amount_scale(lines: list[str], stats: dict) -> tuple[float, str]:
    """(x on the Armour each Break takes, how): the build's "Break X% increased Armour" lines x the main skill's support,
    and the Overgrown Plant line at its share (see the note above)."""
    inc = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
              for t in lines if (m := BREAK_INCREASED.match(t.strip())))
    more = number(stats.get("_break_more"))
    parts = ([f"{inc:+g}% increased"] if inc else []) + ([f"{more:+g}% more from its support"] if more else [])
    grown = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                for t in lines if (m := BREAK_OVERGROWN.match(t.strip())))
    if grown:
        share, why = stats.get("_overgrown") or (0.0, "the main skill isn't a Plant skill, or nothing Overgrows its plants")
        inc += grown * share
        parts.append(f"{grown * share:+g}% increased from Overgrown Plants ({grown:+g}% x {share:.0%}: {why})")
    return max(0.0, 1 + inc / 100) * (1 + more / 100), ", ".join(parts)


def _armour_broken(lines: list[str], stats: dict) -> tuple[float, str]:
    """(share of the fight Fully Broken, basis), or (0.0, "") if no line Breaks Armour for you."""
    main_types = stats.get("_main_types") or set()
    armour = number(stats.get("_enemy_armour"))
    rate, share, full, how = 0.0, 0.0, 0.0, []
    for text in lines:
        if not qualifies(text, main_types) or (r := _armour_break_source(text, stats)) is None:
            continue
        rate, share, full = rate + r[0], share + r[1], full + r[2]
        how.append(f"{text[:60]} ({r[3]})")
    for key, (name, state) in CONDITIONAL_BREAKS.items():  # (MECH89: Mark for Death, Brittle Armour: (%, share, how))
        got = stats.get(key)
        if got and got[1] > 0 and (r := _armour_break_source(MARK_BREAK_LINE.format(f"{got[0]:g}"), stats)):
            rate += r[0] * got[1]
            how.append(f"{name}: {got[0]:g}% of the main skill's Physical hit on the {state} boss ({r[3]}) x "
                       f"{got[1]:.0%} {state} ({got[2]})")
    others = stats.get("_break_others") or []  # (MECH89: the other hitting groups' Breaks, break_others)
    if not how and not others:
        return 0.0, ""
    scale, scaled = break_amount_scale(lines, stats)
    if scaled:
        rate, share = rate * scale, share * scale
        how.insert(0, f"each Break takes x{scale:.2f} Armour ({scaled})")
    if others:  # (their own more is in each; the build's "Break X% increased Armour" lines apply to them too)
        side_scale = break_amount_scale(lines, {})[0]
        rate += sum(o[1] for o in others) * side_scale
        how.append("beside the main skill: " + " | ".join(o[2] for o in others)
                   + (f" (x{side_scale:.2f} from the build's Break lines)" if side_scale != 1 else ""))
    rate += share * armour
    to_break = 0.0 if armour <= 0 else (armour / rate if rate > 0 else float("inf"))
    held = models.full_break_duration(lines)  # (12 s x the Armour Break Duration lines)
    up = held / (held + to_break)
    up = 1 - (1 - up) * (1 - min(1.0, full * held))
    if (uses := number(stats.get("_riven_uses"))) > 0 and armour > 0:
        up, stats["_riven_share"], riven = _riven_cycle(to_break, full, held, uses)
        how.insert(0, riven)
    elif (shouts := number(stats.get("_battershout_uses"))) > 0 and armour > 0:
        up, stats["_battershout_rate"], stats["_battershout_breaks"], shout = _battershout_cycle(to_break, full, held,
                                                                                                 shouts)
        how.insert(0, shout)
    return up, (f"Armour Fully Broken {up:.0%} of the fight ({shared(up)}): {rate:,.0f} Armour broken/s against "
                f"the boss's {armour:,.0f} (PoB), {held:g} s each Full Break: " + "; ".join(how[:5]))


# Riven Armour (MECH69; GEM1's note, a possible OVERCOUNT): game files, KeywordPopups RivenArmour: "Enemies with Riven
# Armour cannot get their Armour Broken further"; High Velocity Rounds' HighVelocityRoundsArmourBroken statset:
# base_skill_effect_duration 5000 (Riven Armour 5 s). Patch 0.3.0 (the newest entry: grep_notes.py --history "High
# Velocity Rounds"): "No longer consumes Fully Broken Armour. Instead, hitting a Fully Armour Broken enemy will apply a
# Debuff". The game files still give it the ConsumesFullyBrokenArmour type, but its description now says "applies":
# both written here, the patch note used (newest wins). So the Full Break runs its whole duration; HVR's first hit on
# the Fully Broken boss (half the gap between its uses on average) applies Riven Armour for 5 s, re-applied by the
# next hit after it ends while the boss is still Fully Broken. Whether a hit renews it while it is up isn't known: not
# renewed here (the lower reading, UNVERIFIED). When the Full Break ends with Riven Armour still up, the boss can't be
# Broken again until it expires: that tail is added to the cycle. HVR's uses: the main skill's speed, or its group's
# rate (Conditions.evidence, stats["_riven_uses"]). Skill Effect Duration on the 5 s isn't read (UNVERIFIED). MEDIUM.
# The share of the fight Riven Armour is up goes to stats["_riven_share"] (Conditions.riven_share) for GEM3's
# models.riven_armour_model (its damage).
RIVEN_SKILL = "High Velocity Rounds"
RIVEN_SECONDS = 5.0


def _riven_cycle(to_break: float, full: float, held: float, uses: float) -> tuple[float, float, str]:
    """(share of the fight Fully Broken, share with Riven Armour up, how) with High Velocity Rounds hitting at `uses` per
    second: `to_break` s of Breaking per Full Break (inf if none), `full` Full Breaks per second from other events,
    `held` s each Full Break lasts."""
    breaks = (1 / to_break if 0 < to_break < float("inf") else 0.0) + full
    if breaks <= 0:
        return 0.0, 0.0, f"{RIVEN_SKILL}: no Full Break happens, so no Riven Armour"
    wait = min(held, 1 / (2 * uses))
    t, riven, last_end = wait, 0.0, 0.0
    while t < held:  # (applications while Fully Broken, each 5 s, not renewed while up)
        last_end = t + RIVEN_SECONDS
        riven += RIVEN_SECONDS
        t = last_end + wait
    tail = max(0.0, last_end - held)
    cycle = 1 / breaks + held + tail
    up, riven_up = held / cycle, riven / cycle
    return up, riven_up, (f"{RIVEN_SKILL} at {uses:.2f}/s applies {RIVEN_SECONDS:g} s of Riven Armour to the Fully Broken "
                          f"boss (up {riven_up:.0%}); it outlasts each {held:g} s Full Break by {tail:.2f} s, when the "
                          f"Armour can't be Broken; a Full Break every {1 / breaks:.2f} s of Breaking (MEDIUM; not "
                          "renewed while up, UNVERIFIED)")


# Battershout (MECH72; MECH70's design; an UNDERCOUNT before, PoB maps neither of its stats): game files 26476662,
# GrantedEffects SupportBattershoutPlayer: cannot_break_armour, consume_armour_break_to_trigger_battershout_explosion
# ("Supported Skills Consume Fully Broken Armour to trigger Physical Damage explosions on Enemies within their range
# but cannot themselves Break Armour"); its explosion TriggeredBattershoutExplosionPlayer: 40% of the Broken Armour,
# radius 20, +5% base crit, 50% more against Unique Enemies (the same in PoB's sup_str.lua; patch notes, the newest
# last: 0.2.0g "now counts as a Warcry skill", 0.3.0 "50% more Damage against Unique Enemies", 0.4.0 fixed it with
# Fortifying / Ancestral Cry). So each use of the supported Warcry while the boss is Fully Broken ends the Full Break
# (an OVERCOUNT guard on the Armour Broken box: it no longer lasts its whole 12 s) and explodes once. The Warcry's uses
# are periodic at the build's rate (its group's, else the search's chosen press rate, else 0) and fall at a random
# point of the Full Break (MEDIUM): it lasts E[min(U, held)] with U uniform over one gap between uses, and explodes
# with chance min(1, held x uses). With High Velocity Rounds' Riven Armour as well, only the Riven cycle is read
# (labelled), and so is the Ferocious Roar path (its Break isn't through the box's lines; not changed, labelled). The
# explosion's damage: models.battershout_model.
BATTERSHOUT = "Battershout"


def _battershout_cycle(to_break: float, full: float, held: float, uses: float) -> tuple[float, float, float, str]:
    """(share of the fight Fully Broken, explosions per second, Full Breaks per second, how) with the Battershout
    Warcry used `uses` times a second: `to_break` s of Breaking per Full Break (inf if none), `full` Full Breaks per
    second from other events, `held` s each Full Break lasts unconsumed."""
    breaks = (1 / to_break if 0 < to_break < float("inf") else 0.0) + full
    if breaks <= 0:
        return 0.0, 0.0, 0.0, f"{BATTERSHOUT}: no Full Break happens, so nothing to Consume"
    gap = 1 / uses
    broken = gap / 2 if held >= gap else held - held * held / (2 * gap)
    chance = min(1.0, held / gap)
    cycle = 1 / breaks + broken
    return broken / cycle, chance / cycle, 1 / cycle, (
        f"{BATTERSHOUT}'s Warcry at {uses:.2f}/s Consumes each Full Break {broken:.2f} s in on average ({chance:.0%} of "
        f"them before the {held:g} s run out): {chance / cycle:.3f} explosions/s, a Full Break every {cycle:.2f} s "
        "(MEDIUM: uses periodic, at a random point of the Full Break)")


def armour_broken_share(lines: list[str], stats: dict) -> float:
    """Share of the fight the boss's Armour is Fully Broken (one Full Break per 12 s of it)."""
    return _armour_broken(lines, stats)[0]


# Scavenged Plating (MECHC29; PATCH_CHECK M1-1, an UNDERCOUNT before: PoB's "# of Scavenged Plating Stacks"
# (scavengedPlatingStacks, ConfigOptions 591, a count with no default) was never set, so the skill's 4% more Armour per
# stack and its Thorns counted 0). The game (ActiveSkills scavenged_plating): "Fully Breaking an enemy's Armour grants you
# stacks of Scavenged Plating for a duration ... Unique enemies grant 10 stacks"; the skill's stat set: base_skill_effect_
# duration 8000, scavenged_plating_maximum_stacks_display 10 (PoB act_str.lua). So against the boss: 10 stacks for the
# skill's duration (PoB's, with the build's modifiers) after each Full Break. Full Breaks a second from _armour_broken's
# share (its Fully Broken share / the Full Break's own duration: one Full Break per Broken stint; Riven Armour and
# Battershout cycles taken as that same ratio, labelled); the stacks at most always. PoB's Multiplier is linear in
# stacks (MORE Armour and Thorns per stack), so the average count is set. No break source = 0.
PLATING, PLATING_STACKS, PLATING_SECONDS = "Scavenged Plating", 10, 8.0


def scavenged_plating_stacks(lines: list[str], stats: dict) -> tuple[float, str]:
    """(average Scavenged Plating stacks, how) for a build with the skill; (0, "") without it."""
    if "_plating_duration" not in stats:  # (set by Conditions.evidence with the skill enabled)
        return 0.0, ""
    up, how = _armour_broken(lines, stats)
    if not how or up <= 0:
        return 0.0, f"{PLATING}: nothing Fully Breaks the boss's Armour, 0 stacks" + (f" ({how})" if how else "")
    held = models.full_break_duration(lines)
    duration = number(stats.get("_plating_duration")) or PLATING_SECONDS
    breaks = up / held if held > 0 else 0.0
    share = min(1.0, breaks * duration)
    return PLATING_STACKS * share, (f"{PLATING}: {PLATING_STACKS} stacks (a Unique's) for {duration:.1f} s after each Full "
                                   f"Break, {breaks:.3f} Full Breaks/s: {share:.0%} of the fight ({how})")


# Wind Dancer (MECHC29; PATCH_CHECK L2-3, an UNDERCOUNT before: PoB's "# of Wind Dancer Stacks" (windDancerStacks,
# ConfigOptions 749, no default) was never set, so its more Evasion counted 0). The game (ActiveSkills): "While active,
# periodically grants stages of a buff that gives you more Evasion per stage. Being Hit by an Enemy while you have this
# buff consumes all stages to damage and Knock Back enemies around you"; its stat set: a stage every
# wind_dancer_stages_gained_every_x_ms (1.5 s at level 1, by level), at most wind_dancer_maximum_number_of_stages 3, 10%
# more Evasion each (PoB act_dex.lua). The boss's landed hits (boss_hits.land_share over its skill time, the worst case's
# own rate, not the gain floor: a hit here is a loss) taken as random arrivals at rate L: the time since the last one is
# exponential, so stage k is held with chance e^(-L k T) and the average is the sum over k = 1..3. PoB's multiplier is
# linear in stages, so the average is set. The stages' damage burst on being hit isn't counted (PoB has no number for
# it; an undercount, labelled). More Evasion lands fewer hits: the settle's next round reads that.
WIND_DANCER, WIND_DANCER_STAT, WIND_DANCER_STAGES = "Wind Dancer", "wind_dancer_stages_gained_every_x_ms", 3


def wind_dancer_stages(lines: list[str], stats: dict) -> tuple[float, str]:
    """(average Wind Dancer stages, how) for a build with the skill; (0, "") without it."""
    every = number(stats.get("_wind_dancer"))
    if every <= 0:
        return 0.0, ""
    landed = boss_hits.land_share(stats) / bosses.benefit_skill_time(stats)
    stages = sum(math.exp(-landed * k * every) for k in range(1, WIND_DANCER_STAGES + 1))
    return stages, (f"{WIND_DANCER}: a stage every {every:.2f} s (at most {WIND_DANCER_STAGES}), all lost on each of the "
                    f"boss's {landed:.2f} landed hits/s (taken as random): {stages:.2f} stages on average (the burst on "
                    f"being hit not counted)")


# Sigil of Power (MECHC29; PATCH_CHECK's ConfigOptions list (sigilOfPowerStages :599) and PLANS kw-power item 6: PoB's
# "Stages" box has a placeholder of 1, which PoB applies while it's unset, so every Sigil build counted 1 stage whatever its
# Mana spending: an OVERCOUNT where it spends little, an UNDERCOUNT where it spends a lot). The game (ActiveSkills
# sigil_of_power): "The Buff becomes more powerful the more mana you spend while standing in the Sigil"; its stats
# (skill_stat_descriptions.csd): "Gains a Stage when you Spend a total of {0} Mana while in Area" (the base stat
# base_circle_of_power_mana_%_spend_per_upgrade 50: poe2db Sigil_of_Power: "Gains a Stage when you Spend a total of 50% of your Maximum Mana while in Area", every level),
# "Minimum 1 second between gaining Stages", circle_of_power_max_stages 4, base_skill_effect_duration ~10-12 s (PoB
# act_int.lua). Stages start at 0 on each Sigil and are lost with it; the Sigil is recast as it ends (the search presses
# it). Mana spent: the main skill's (PoB's ManaPerSecondCost; other skills' spending not counted, an undercount). Stage
# k comes k x max(1 s, Mana per stage / spend) after placing; the average over the Sigil's duration. Standing in it: as
# the user's Leylines ruling (15 Nov: "assume you're trying to stand in them if you take the ability, so 75%"), 75% of
# the fight (the manager, 15 Nov 16:11: "yes by the same reasoning the user gave for Leylines", LOW, on the user's list),
# which scales the stages held; the play guide's sigil_row gives the DPS inside and outside.
SIGIL, SIGIL_STAGES, SIGIL_MANA_SHARE, SIGIL_GAP, SIGIL_SECONDS = "Sigil of Power", 4, 0.50, 1.0, 10.0


def sigil_stages(lines: list[str], stats: dict) -> tuple[float, str]:
    """(average Sigil of Power stages, how) for a build with it; (0, "") without it."""
    got = stats.get("_sigil")
    if not got:
        return 0.0, ""
    duration, mana, spend = got
    duration = duration or SIGIL_SECONDS
    per = SIGIL_MANA_SHARE * mana
    if spend <= 0 or per <= 0:
        return 0.0, f"{SIGIL}: the main skill spends no Mana, 0 stages (PoB's own placeholder is 1)"
    step = max(SIGIL_GAP, per / spend)
    held = sum(max(0.0, duration - k * step) for k in range(1, SIGIL_STAGES + 1)) / duration
    stages = held * LEYLINE_UPTIME
    return stages, (f"{SIGIL}: {spend:.1f} Mana/s spent against {per:,.0f} a stage (50% of {mana:,.0f} maximum Mana), a "
                    f"stage every {step:.2f} s (at least {SIGIL_GAP:g} s), at most {SIGIL_STAGES}, over its {duration:.1f} "
                    f"s: {held:.2f} on average while in it, x {LEYLINE_UPTIME:.0%} of the fight standing in it (the user's "
                    f"Leylines reasoning, the manager 16:11: LOW) = {stages:.2f}")


def armour_broken_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    """(True / False, basis) for the Armour Broken box, or (0.0, "") if no line Breaks Armour for you."""
    up, basis = _armour_broken(lines, stats)
    if not basis:
        return 0.0, ""
    return _box(up, basis)


# Exposure's uptime (MECH28, 9 Nov 2026; before, any line inflicting it turned the box on). The game files' KeywordPopups
# Exposure: "lowers the affected enemy's Total Elemental Resistances. By default it lowers Resistances by -20% and lasts
# for 4 seconds" (PoB's box applies the -20%, and only when PoB itself sees a source: CanApply<Element>Exposure). Each
# line's uptime is the chance at least one of its events falls in the last 4 s (1 - (1 - chance)^(events x 4));
# "Elemental Exposure" and plain "Exposure" count for all three; sources combine as independent; the box is on at
# UPTIME, else explicitly off with its basis. Events: the main skill's hits, crits, Shocks / Ignites (PoB's chance per
# hit), Fully Breaking Armour (armour_broken_share's Full Breaks), "to Enemies 3 metres in front of you" while the boss
# stands that close (the build's distance). A line's own condition (Devotion, socketed jewels) is left to PoB, which
# applies the box only with a source it accepts. Exposure duration modifiers aren't read (4 s, labelled).
# (MECH69; AUDIT31 A31-26, an UNDERCOUNT) Today's game has one Exposure on all three elements (KeywordPopups above;
# AUDIT31 checked poe2db's keyword page; the 0.3.0 notes move sources from one element's Exposure to Elemental Exposure
# one by one, e.g. Frost Bomb, The Whispering Ice, Shattering Concoction), and the stats still in use display plain
# "Exposure" (stat_descriptions: "{0}% chance to inflict Exposure on Hit", "Attacks with this Weapon have {0}% chance
# to inflict Exposure", the supports' "Supported Skills inflict Exposure for {0} seconds on Igniting a target"). The
# pattern needed an element word, so those gave nothing. Now plain "Exposure" = all three. A line naming one element
# ("Inflict Fire Exposure on Shocking an Enemy", PoB's older texts) still counts for that element only (UNVERIFIED
# whether the game's current text for it is plain "Exposure": an undercount if so, never an overcount).
# The Fire / Lightning / Cold Exposure supports (GrantedEffectStatSets: inflict_exposure_for_x_ms_on_ignite /
# _on_shock / _on_cold_crit, 8000 ms each) are read off the main skill's support stats (Conditions.evidence puts them
# in stats["_exposure_supports"]), at the build's own Ignite / Shock rates and crits with Cold damage, for their own 8 s
# (the stat's value). The Cold one's gem description says "when it inflicts Freeze" but its stat (game files and PoB's
# SkillStatMap: CritInPast8Sec + ColdHasDamage) and its stat text say "on Critical Hit with Cold Damage": the stat is
# read (both written down; the AUDIT may settle it). PoB maps all three to its InflictExposure flag, which already
# makes them a source PoB accepts (CalcPerform hasExposureSource), but never ticks the box itself: so they gave no -20%
# before. The supports' own descriptions ("Supports any skill ...") are skipped as text sources (they'd count a support
# socketed with any skill as the main skill's). For text sources PoB doesn't parse (ModParser has no pattern for
# "chance to inflict Exposure on Hit", the weapon line, "Inflict Exposure on Hit"), exposure_source_flags gives PoB the
# CanApply flags through the assumptions, so the box applies; the box itself still follows the uptime.
EXPOSURE_DURATION = 4.0
EXPOSURE_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?(?<!cannot )(?<!not )\b(?:inflict|apply|cause)s? "
                             r"(fire|cold|lightning|elemental|)\s?exposure\b(.*)$", re.I)  # (group 2: "" for plain)
EXPOSURE_ELEMENTS = ("Fire", "Cold", "Lightning")
EXPOSURE_WORD = re.compile(r"\bExposure\b")
# (stat -> what fires it) the Exposure supports' stats on the main skill (game files: GrantedEffectStatSets
# SupportFireExposurePlayer / SupportLightningExposurePlayer / SupportColdExposurePlayer), and the Warcry support's
# all_exposure_on_hit_for_duration_ms ("Supported Skills apply Exposure on Hit for {0} seconds")
EXPOSURE_SUPPORT_STATS = {
    "inflict_exposure_for_x_ms_on_ignite": "ignite",
    "inflict_exposure_for_x_ms_on_shock": "shock",
    "inflict_exposure_for_x_ms_on_cold_crit": "cold crit",
    "all_exposure_on_hit_for_duration_ms": "hit",
}


def _exposure_match(text: str, element: str | None = None) -> re.Match | None:
    """The line's Exposure-source match, for `element` if given (plain and Elemental Exposure count for all three);
    None for a support's own description (read through its stats instead) or a line that isn't a source."""
    if text.startswith("Supports "):
        return None
    m = EXPOSURE_SOURCE.search(text)
    if not m or (element and (m.group(2) or "elemental").lower() not in (element.lower(), "elemental")):
        return None
    return m


def _exposure_events(kind: str, stats: dict) -> tuple[float, str]:
    """(events per second, what they are) for an Exposure source fired by `kind`."""
    hits = models.hits_per_second(stats)
    if kind == "shock":
        return hits * min(number(stats.get("ShockChancePerHit")), 100) / 100, "Shocks (PoB's chance per hit)"
    if kind == "ignite":
        return hits * min(number(stats.get("IgniteChancePerHit")), 100) / 100, "Ignites (PoB's chance per hit)"
    if kind == "cold crit":
        if not (number(stats.get("ColdHitAverage")) or number(stats.get("MainHand.ColdHitAverage"))):
            return 0.0, "crits with Cold Damage (the main skill deals no Cold hit damage)"
        return models.crits_per_second(stats), "crits with Cold Damage"
    if kind == "crit":
        return models.crits_per_second(stats), "crits"
    return hits, "hits"


def _exposure_source(text: str, element: str, stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line for `element`'s Exposure, or None if it isn't a source of it."""
    m = _exposure_match(text, element)
    if m is None:
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(3).lower()
    if "fully breaks armour" in when:
        events, how = number(stats.get("_full_break_rate")), "Full Breaks of the boss's Armour"
    elif near := re.search(r"(\d+(?:\.\d+)?) metres in front", when):
        metres = (stats.get("_distance_choice") or (natural_distance(stats.get("_main_types") or set(), stats.get("_main_skill") or ""), ""))[0]
        up = 1.0 if metres <= float(near.group(1)) else 0.0
        return up, f"the boss {metres:g} m away ({near.group(1)} m needed)"
    elif re.match(r"\s*minions?\b", text, re.I):
        return 0.0, "a minion's hits: their rate isn't modelled (0, labelled)"
    elif "shock" in when:
        events, how = _exposure_events("shock", stats)
    elif "ignit" in when:
        events, how = _exposure_events("ignite", stats)
    elif "critical" in when:
        events, how = _exposure_events("crit", stats)
    elif "on hit" in when or (not when.strip(" .") and re.match(r"\s*attacks with this weapon\b", text, re.I)):
        events, how = _exposure_events("hit", stats)  # (the weapon line: the main skill's attacks; hand not split)
    else:
        return 0.0, f"'{when.strip()[:40]}': its rate isn't modelled (0, labelled)"
    n = events * EXPOSURE_DURATION
    up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
    return up, f"{chance:.0%} x {events:.2f} {how}/s, {EXPOSURE_DURATION:g} s: {up:.0%} up"


def _exposure_support_sources(stats: dict) -> list[tuple[float, str]]:
    """(uptime, how) for each Exposure support on the main skill (stats["_exposure_supports"]: stat -> value in ms),
    for all three elements: each lasts its own stat's duration."""
    out = []
    for stat, ms in sorted((stats.get("_exposure_supports") or {}).items()):
        if stat not in EXPOSURE_SUPPORT_STATS or number(ms) <= 0:
            continue
        seconds = number(ms) / 1000
        events, how = _exposure_events(EXPOSURE_SUPPORT_STATS[stat], stats)
        up = min(1.0, events * seconds)
        out.append((up, f"{stat} (a support on the main skill): {events:.2f} {how}/s, {seconds:g} s: {up:.0%} up"))
    return out


def exposure_source_flags(texts: list[str], main_types: set[str]) -> list[dict]:
    """PoB's CanApply<Element>Exposure flags for the main skill's Exposure text sources PoB itself doesn't parse
    (MECH69): without them PoB's Exposure box applies nothing for such a line (ConfigOptions conditionEnemy*Exposure:
    ActorCondition CanApply*Exposure). Only the flags: the -20% still comes from the box, ticked at the uptime."""
    elements: set[str] = set()
    for text in texts:
        if not qualifies(text, main_types) or (m := _exposure_match(text)) is None or re.match(r"\s*minions?\b", text, re.I):
            continue
        named = (m.group(2) or "elemental").capitalize()
        elements |= set(EXPOSURE_ELEMENTS) if named == "Elemental" else {named}
    return [models.raw_mod(f"Condition:CanApply{e}Exposure", "FLAG", 1) for e in EXPOSURE_ELEMENTS if e in elements]


# CanWither for Withered lines PoB parses only in part (MECH72; RESEARCH withered-sources.md #1, HIGH): PoB applies the
# Withered count (multiplierWitheredStackCount) only through WitherEffectStack, which it sets only for an active skill
# with Condition:CanWither (CalcPerform 2588-2590, 714-717). The Wicked Quill's "Spells have a 25% chance to inflict
# Withered for 4 seconds on Hit" is a partial parse (ModCache: leftover " to inflict Withered   "), so with it alone our
# stacks did nothing in PoB (an UNDERCOUNT to 0). Each "N% chance to inflict Withered for N seconds on Hit" line
# _withered_stacks counts sets the flag (a no-op where PoB parsed it: it sets the same flag). Minions' and the Wasting
# lines are left to PoB's own handling.
def wither_source_flags(texts: list[str], main_types: set[str]) -> list[dict]:
    """PoB's Condition:CanWither for the main skill's counted Withered chance lines (see the note above)."""
    spell = "Spell" in main_types
    # (MECH75: Withering Hex's line too; PoB doesn't parse it (checked: ModCache.lua and ModParser.lua for its words,
    # no hit; the patch notes have no Withering Hex entry), so its stacks need the flag)
    if any((WITHER_CHANCE.search(t) and (spell or not WITHER_SPELLS.search(t))) or WITHER_HEX.match(t.strip())
           for t in texts):
        return [models.raw_mod("Condition:CanWither", "FLAG", 1)]
    return []


# The minions' Withered sized by YOUR Withered Magnitude (MECH82; the manager 09:09, COV37's find, a small OVERCOUNT). PoB's
# ModCache reads "Minions have N% chance to inflict Withered on Hit" as a player Condition:CanWither, and each stack's
# effect is the most over the active skills with it: 5 for a minion skill, 5 x (1 + your WitherEffect INC) for one of
# yours (CalcPerform 2588-2590). So with any skill of your own in the build, the minions' Withered took your "increased
# Withered Magnitude" lines (ModParser 505-506: WitherEffect), which speak of Withered you inflict. When the minions' line is
# the build's only Withered source, those lines are cancelled (WitherEffect INC less their sum): the base 5% a stack,
# PoB's own figure for a minion skill. With a Withered source of your own beside it the stacks are one count in PoB, so
# your Magnitude stays on all of them (the minions' share overcounted there, labelled; not split).
WITHER_MAGNITUDE = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) (?:Withered Magnitude|effect of Withered)$", re.I)


def minion_wither_magnitude(texts: list[str], main: str, gem_names: set[str]) -> tuple[list[dict], str]:
    """(mods, how): your Withered Magnitude lines cancelled when only your minions Wither (see the note above)."""
    if not any(WITHER_MINIONS.match(t.strip()) for t in texts):
        return [], ""
    own = ([t for t in texts if WITHER_CHANCE.search(t) or WITHER_HEX.match(t.strip()) or WITHER_WASTING.match(t.strip())
            or re.search(r"withered .*when they enter your presence", t, re.I)]
           + [n for n in ({main} | gem_names) if n in WITHER_ON_HIT_SKILLS or n in (WITHERING_PRESENCE, "Withering Touch")])
    total = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                for t in texts if (m := WITHER_MAGNITUDE.match(t.strip())))
    if not total:
        return [], ""
    if own:
        return [], (f"your Withered Magnitude ({total:+g}%) stays on the minions' Withered too: {own[0][:50]} Withers the "
                    "boss as well and PoB counts one stack count (the minions' share overcounted, labelled)")
    return [models.raw_mod("WitherEffect", "INC", -total)], (
        f"only your minions Wither the boss: your {total:+g}% Withered Magnitude lines don't size their Withered (PoB's "
        "minion figure, 5% a stack)")


def _exposure_uptime(element: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def uptime(lines: list[str], stats: dict) -> tuple[object, str]:
        main_types = stats.get("_main_types") or set()
        down, how = 1.0, []
        for text in lines:
            if not qualifies(text, main_types) or (r := _exposure_source(text, element, stats)) is None:
                continue
            down *= 1 - r[0]
            how.append(f"{text[:60]} ({r[1]})")
        for r in _exposure_support_sources(stats):
            down *= 1 - r[0]
            how.append(r[1])
        if not how:
            return 0.0, ""
        up = 1 - down
        basis = f"{element} Exposure up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
        return _box(up, basis)
    return uptime


def _blocked_recently(stats: dict) -> bool:
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    # Red-flash hits can't be Blocked, and an Evaded hit doesn't hit you at all, so it can't be Blocked either
    # (KeywordPopups "Block" / "Evasion", game 26476662; MECHC5's LOW red-flash shares)
    block = boss_hits.block_share(stats)
    return 1 - (1 - block) ** (RECENTLY / every) >= UPTIME


def _gen(subject: str) -> str:
    return rf"\b(?:gain|gains|grant|grants|generate|generates|granting|gaining)\b[^.;]*\b{subject}"


def _consumer(subject: str) -> str:
    # "If you would gain Frenzy Charges, you instead gain up to your maximum" and "Gain Power Charges
    # instead of Frenzy Charges" change charges the build already gains; neither is a source
    return (rf"\bper {subject}|maximum (?:number of )?{subject}|consum\w*[^.;]*{subject}|lose[^.;]*{subject}"
            rf"|when you gain[^.;]*{subject}|while you have[^.;]*{subject}|if you have[^.;]*{subject}"
            rf"|during {subject}|effect of {subject}|{subject}\w* you have"
            rf"|would gain[^.;]*{subject}|instead of {subject}|{subject}\w* instead")


def _uptime(stats: dict, chance_field: str) -> bool:
    chance = number(stats.get(chance_field)) / 100.0
    rate = number(stats.get("Speed"))
    if chance <= 0 or rate <= 0:
        return False
    return 1.0 - (1.0 - min(chance, 1.0)) ** (rate * AILMENT_WINDOW) >= UPTIME


def ailment_uptime(stats: dict, ailment: str) -> float:
    """Share of the fight the boss has the ailment: the chance that at least one hit applied it within its own duration
    (hits that land: uses per second x hit chance). A main skill that consumes it: consumed_share. The build's other
    groups' applications (stats["_ailment_others"], MECH75: Ignite, ignite_others) are added, each source taken as
    independent; a consumer then waits for the next application from any of them."""
    chance = min(number(stats.get(f"{ailment}ChancePerHit")) / 100.0, 1.0)
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    duration = number(stats.get(f"{ailment}Duration")) or AILMENT_DURATION[ailment]
    others = [o for o in (stats.get("_ailment_others") or {}).get(ailment) or () if o[1] > 0 and o[2] > 0 and o[3] > 0]
    # (MECH75: an aura that keeps it on the boss the share of the fight it stands in it: Radiant Grief, presence_ignite)
    ground = min(1.0, max(0.0, number(((stats.get("_ailment_ground") or {}).get(ailment) or (0.0,))[0])))
    own = chance > 0 and hits > 0
    if not own and not others and ground <= 0:
        return 0.0
    extra = sum(h * c for _, h, c, _ in others)  # (the other groups' applications a second)
    if ailment in (stats.get("_consumes") or {}):
        return consumed_share(hits, chance, ground, extra=extra, kept=consume_reapply(stats, ailment))
    if ailment == "Ignite" and ailment not in (stats.get("_side_consumes") or {}):
        # (MECH81: a hit's chance is all the live Flammability on the boss, stacked_ignite_chance; a consumer beside the
        # main skill clears it with each Ignite it consumes, patch 0.3.0, so the per-hit chance stays there)
        live = stacked_ignite_chance(chance if own else 0.0, hits, extra)[1]
        chance = min(1.0, chance + live) if own else chance
        others = [(n, h, min(1.0, c + live), d) for n, h, c, d in others]
        extra = sum(h * c for _, h, c, _ in others)
    absent = ((1.0 - chance) ** (hits * duration) if own else 1.0) * (1.0 - ground)
    for _, h, c, d in others:
        absent *= (1.0 - c) ** (h * d)
    # (side_consumed's wait to reapply, 1 / (hits x chance), over every source's applications)
    return side_consumed(1.0 - absent, stats, ailment, hits * chance + extra, 1.0, ground)


# Flammability stacks (MECH81; MECH80's next step 5, an UNDERCOUNT for fire hitters: PoB and the tool took each hit's
# Ignite chance from that hit's own Flammability alone). KeywordPopups Ignite (26650308): "The chance for a Hit to Ignite
# a target is determined by the total Magnitude of Flammability on the target, including the amount added by that Hit";
# Flammability: "Multiple instances of Flammability stack, raising the chance for Hits to Ignite the target up to 100%.
# Each instance of Flammability has its own independent duration, lasting 8 seconds by default"; patch 0.3.0: "a target
# being hit frequently and/or hard enough will consistently have 100% chance to be ignited". PoB's IgniteChancePerHit is
# one hit's Flammability (CalcOffence 5613-5629), and its Ignite DPS is x min(1, hit chance x that chance x duration x
# hits a second) (CalcOffence 5253-5262, 5399: activeAilments). Here a hit's chance = min(1, its own + the live
# Flammability: 8 s x the Ignite applications a second x their own chance, the main skill's and the other groups', at
# their average magnitude, MEDIUM: an Ignite isn't taken to clear Flammability, which only removing / consuming an
# Ignite does (patch 0.3.0); a consumed Ignite keeps the per-hit chance). Used by ailment_uptime (the Ignited share every
# reader uses) and flammability_ignite_scale (the main skill's Ignite DPS: PoB's x the stacked uptime over PoB's own).
FLAMMABILITY_SECONDS_STACK = 8.0  # s an instance lasts on monsters (KeywordPopups Flammability; = FLAMMABILITY_SECONDS)


def stacked_ignite_chance(chance: float, hits: float, extra: float = 0.0) -> tuple[float, float]:
    """(a hit's Ignite chance with the live Flammability, the live Flammability) for `hits` landing a second of `chance`
    (0-1) each, plus `extra` other applications' hits x chance a second."""
    live = FLAMMABILITY_SECONDS_STACK * (max(hits, 0.0) * max(chance, 0.0) + max(extra, 0.0))
    return min(1.0, chance + live), live


def flammability_ignite_scale(stats: dict) -> tuple[float, str]:
    """(x on PoB's own Ignite DPS for the main skill, how): the Ignited uptime with stacked Flammability over PoB's
    activeAilments, min(1, IgniteStackPotential). (1.0, "") where it doesn't apply (no Ignite chance, a consumed Ignite)."""
    chance = min(number(stats.get("IgniteChancePerHit")) / 100.0, 1.0)
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    if (chance <= 0 or hits <= 0 or "Ignite" in (stats.get("_consumes") or {})
            or "Ignite" in (stats.get("_side_consumes") or {})):
        return 1.0, ""
    duration = number(stats.get("IgniteDuration")) or AILMENT_DURATION["Ignite"]
    potential = number(stats.get("IgniteStackPotential"))
    pob = min(1.0, potential) if potential > 0 else min(1.0, hits * chance * duration)
    others = [o for o in (stats.get("_ailment_others") or {}).get("Ignite") or () if o[1] > 0 and o[2] > 0]
    eff, live = stacked_ignite_chance(chance, hits, sum(o[1] * o[2] for o in others))
    up = 1.0 - (1.0 - eff) ** (hits * duration)
    if pob <= 0 or abs(up / pob - 1.0) < 1e-3:
        return 1.0, ""
    return up / pob, (f"Flammability stacks (KeywordPopups: a hit's Ignite chance is all the Flammability on the boss, "
                      f"each instance 8 s): {chance:.1%} a hit + {live:.0%} live = {eff:.0%} a hit at {hits:.2f} hits/s, "
                      f"Ignited {up:.0%} of the fight vs PoB's {pob:.0%} (its one-hit chance x duration x hits): "
                      f"x{up / pob:.2f} on the main skill's Ignite (MEDIUM)")


# Ailments the main skill consumes on hit (MECH51; FIX_QUEUE RESEARCH18 #92, HIGH): the stats of its own stat sets or its
# supports that make its hit consume Freeze, Shock or Ignite (checked: every Stats.json id holding "consum" and an ailment
# word, game 26476662, 45 ids, and their carriers in PoB's skill data; AUDIT22 A22-2 added the ones that consume on the
# hit and then do something else with it: Elemental Discharge, Flame Pillar / His Winnowing Flame, Shocking Rift,
# Morrigan's Refuge, Glacial Cascade's last spike, Lightning Conduit (out since MECH78: below). Shocking Rift consuming
# the boss's own Shock: MEDIUM. The rest scale by Charges, Incision, Heat, flasks or corpses, or are Bleed / Poison stats,
# not modelled here). Consumed, the ailment ends on the main skill's next hit and the boss is immune to it for 1 s
# (models.CONSUME_IMMUNE): the boss carries it last / (last + 1 s + the wait to reapply it).
CONSUME_STATS = {
    "Freeze": ("consume_enemy_freeze_to_trigger_morrigan_ward",
               "active_skill_consume_enemy_freeze_to_gain_damage_against_non_unique_+%_final",
               "consume_ignite_freeze_shock_on_hit_to_trigger_elemental_discharge", "base_consume_enemy_freeze_on_hit",
               "active_skill_consume_enemy_freeze_to_gain_damage_+%_final",
               "active_skill_consume_enemy_freeze_to_gain_damage_against_unique_+%_final",
               "support_active_skill_consume_enemy_freeze_to_gain_damage_+%_final", "consume_enemy_freeze_to_guarantee_crit",
               "support_active_skill_consume_enemy_freeze_to_apply_chill", "damage_+%_final_from_consuming_freeze",
               "support_damage_+%_final_from_consuming_freeze"),
    # (MECH69; GEM4: Electromagnetism's try_consuming_shock_to_trigger_shocking_rift_in_radius_on_shocking_enemy and
    # consume_shock_of_X_enemies_to_trigger_shocking_rift are out: its stat text consumes Shock only "On Shocking an
    # enemy where there are at least {1} Shocked enemies within {0} metres" (gem_stat_descriptions; its values 40 / 3:
    # 4 m, 3 enemies). The default fight is the worst case, a lone boss (the user's rule), where it never fires, so it
    # isn't a per-hit consume: Shock uptime was undercounted with it. Bosses with adds (bosses.py: Kulemak's, Olroth's
    # and the Raven's adds are "no known rate" placeholders; Atziri's wave is a lower bound) could give it 3 Shocked
    # enemies, but no source gives a rate, so it counts 0 there too (no source = 0, noted here; AUDIT34 A34-19).
    # In GrantedEffectStatSets the two are on SupportShockingRiftPlayer alone: checked 26476662's stat sets.)
    # (MECH78, PATCH_CHECK K2-7: lightning_conduit_x_additional_strikes_if_consumed_a_shock is out. 0.3.0: "Lightning
    # Conduit: No longer consumes Shocks from targets hit, but now has a 6 second Cooldown" (no later line undoes it);
    # checked: no GrantedEffectStatSet of 26476662 carries the id (LightningConduitPlayer's are range_backflip...,
    # lightning_conduit_damage_+%_final_per_5%_increased_damage_taken_from_shock, never_shock, ...), and its ActiveSkills
    # text has no consume: "If there is a Shocked enemy in the target area, additional lightning bolts are distributed".
    # PoB's data still carries the id, so a group with another consuming gem marked Shock consumed: an UNDERCOUNT.)
    "Shock": ("consume_ignite_freeze_shock_on_hit_to_trigger_elemental_discharge", "base_consume_enemy_shock_on_hit",
              "consume_enemy_shock_to_gain_damage_+%_final_per_5%_increased_damage_taken_from_shock",
              "damage_+%_final_from_consuming_shock"),
    "Ignite": ("pillar_of_flame_consume_ignite_to_create_X_pillars", "unique_pillar_of_flame_consume_ignite_to_create_X_pillars",
               "consume_ignite_freeze_shock_on_hit_to_trigger_elemental_discharge", "consume_ignite_on_hit",),
    # Icestorm: "[Chill] and [Freeze] on enemies in front of you are [Consume|Consumed]" (ActiveSkills icestorm; poe2db's
    # Icestorm page, current, says the same). MECH56, a small OVERCOUNT before: a side Icestorm left the main skill's
    # Chill share whole. (checked for other Chill consumers: Stats.json ids holding "chill" and "consum" (3: this one,
    # support_active_skill_consume_enemy_freeze_to_apply_chill, which consumes Freeze to Chill, and Creeping Chill's,
    # which consumes Power Charges); ActiveSkills / GrantedEffects / KeywordPopups / Mods text with Chill near
    # "consum" (Icestorm only; Ice Nova, Fangs of Frost and Glacial Lance consume other things); poe2db beyond
    # Icestorm's page not searched, so other Chill consumers stay (UNVERIFIED) and are not modelled.)
    "Chill": ("firestorm_improved_bolts_per_chill_consumed",),
    # Bleeding (MECH75, an OVERCOUNT before: PoB keeps a consumed Bleed whole). KeywordPopups Consume: "Consuming a
    # [Debuff] on an enemy causes the enemy to be immune to that [Debuff] for 1 seconds" (Bleeding is an Ailment, a
    # Debuff). Consumers (checked: Stats.json ids holding "consum" with "bleed" / "blood" (15 ids: the rest consume
    # Bloodstained, Incision, flask charges, Pinned or Blood Loss itself); PoB's skill data with SkillConsumesBleeding:
    # Blood Hunt, Herald of Blood, Herald of the Royal Queen; poe2db not checked, so others stay UNVERIFIED): the
    # Haemocrystals support ("Supported Skills Consume Bleeding on Hit ... but cannot themselves inflict Bleeding",
    # consume_bleed_to_create_X_haemocrystals), and by type Blood Hunt ("Hitting an enemy that has suffered Blood Loss
    # will cause a blood explosion and also Consume Bleeding": its own hit never Bleeds (BloodHuntPlayer's
    # cannot_cause_bleeding), so it Consumes another skill's Bleed once it has ticked Blood Loss; each hit taken to find
    # it, MEDIUM). Herald of Blood explodes on a kill ("killing an enemy with Blood
    # Loss", 0 against a lone boss) and its description names no Consume, so it isn't a candidate (CONSUME_TEXT).
    "Bleed": ("consume_bleed_to_create_X_haemocrystals",),
}
CONSUME_TEXT = re.compile(r"\bconsum\w*\b[^.]*\b(?:freez|frozen|shock|ignit|chill|elemental ailment|bleed)|"
                          r"\b(?:chill|freeze|shock|ignite|bleeding)\b[^.]*\bconsumed\b", re.I)  # (Icestorm: "... Freeze ... are Consumed")
# Skills that consume an ailment by their skill types alone, no CONSUME_STATS stat (Snap, Elemental Sundering, Snipe,
# Shockchain Arrow, Icestorm: SkillConsumesFreeze / Shock / Ignite; the bridge's activeSkills "consumes"; MECH55, an
# OVERCOUNT before: a Snap pressed beside the main skill left the boss's Freeze / Shock / Ignite uptime whole). Snipe
# consumes only on a Perfect Timing release (taken as every release: an upper bound on what it ends, MEDIUM).


# Minion skills too (MECH57; AUDIT25's small OVERCOUNT): the Skeletal Brute's Shattering Roar, a Warcry on Command,
# "Consuming Freeze on enemies and allies to deal damage" (ActiveSkills brute_warcry, SkillConsumesFreeze; cooldown 8 s),
# a minion skill, so not a group's active skill. The bridge reports the summoning skill's counted minion skill
# (PoB's minion.mainSkill, the one Full DPS counts) as "minionConsumes", and useRates its uses per second as
# "minionSpeed" (PoB's minion Speed, capped by its cooldown: the rate its damage is counted at; one rate for both sides).
# A Brute whose counted skill is its plain attack consumes nothing here: the search isn't counting the Roar, so the
# build isn't taken to Command it (MEDIUM). (checked for other ailment consumers the types or CONSUME_STATS miss: every
# ActiveSkills row of game 26476662 with SkillConsumesFreeze / Shock / Ignite (11: brute_warcry the only minion one) or
# with a description consuming Freeze / Shock / Ignite (the rest consume Charges, Infusions, Corpses, Parried or flask
# charges, or are triggered by a consuming support: Elemental Discharge, Flame Pillar, Grisly Pyre; Shard Scavenger
# reacts to a consumption and consumes nothing itself); PoB's Data/Skills, every file incl. minion.lua, same result;
# the supports that add the types (Frostfire, Biting Frost ...) reach the bridge through the active skill's types.
# poe2db's Skeletal Brute page, current, 12 Nov: the same Warcry text, a Command Skill since 0.3.0, no other minion
# skill there consuming Freeze / Shock / Ignite.)
def _type_consumes(skill: dict, minion: bool = True) -> set[str]:
    own = {a for a, on in (skill.get("consumes") or {}).items() if on}
    if minion:
        own |= _minion_consumes(skill)
    return own


def _minion_consumes(skill: dict) -> set[str]:
    return {a for a, on in (skill.get("minionConsumes") or {}).items() if a != "skill" and on is True}


def consumed_share(hits: float, chance: float, ground: float = 0.0, extra: float = 0.0, kept: float = 0.0) -> float:
    """Share of the fight the boss carries an ailment the main skill consumes: it lasts until the next hit (1 / hits), then
    the boss is immune for 1 s, then it waits to be reapplied: (1 - ground) / (hits x chance + extra) (a ground reapplies
    it at once the share of the time the boss stands in it; `extra`, other groups' applications a second, MECH75).
    `kept` (MECH85: Endless Circuit's "N% chance on Consuming a Shock on an Enemy to reapply it"): each consumption puts it
    straight back with that chance, so it lasts 1 / (1 - kept) consuming hits on average before the gap."""
    if hits <= 0:
        return 0.0
    last = 1.0 / hits / max(1.0 - min(max(kept, 0.0), 0.99), 0.01)
    if chance > 0 or extra > 0:
        return last / (last + models.CONSUME_IMMUNE + (1.0 - ground) / (hits * max(chance, 0.0) + extra))
    return ground * last / (last + models.CONSUME_IMMUNE)


def main_consumes(build, main_skill: str, main_gems: set[str]) -> dict[str, str]:
    """{ailment: what consumes it} for the ailments the main skill consumes on hit (CONSUME_STATS in its own stat sets
    or its supports'); looked up only when a gem of its group says it consumes."""
    drain = DRAIN_AILMENTS in main_gems  # (MECH77: its text names no ailment CONSUME_TEXT knows; DRAIN_CONSUMES)
    if not drain and not any(CONSUME_TEXT.search((build.ctx.all_gems.get(n) or {}).get("description") or "")
                             for n in main_gems):
        return {}
    every = [stat for ids in CONSUME_STATS.values() for stat in ids]
    raw = build.call("activeSkills", levelStats=[main_skill], supportStats=every)
    main = next((s for s in raw["skills"] if s["name"] == main_skill and not s.get("disabled")), {})
    own, sup = main.get("levelStats") or {}, main.get("supportStats") or {}
    typed = _type_consumes(main)
    found = {ailment: (main_skill if ailment in typed or any(number(own.get(i)) for i in ids) else "a support of it")
             for ailment, ids in CONSUME_STATS.items()
             if ailment in typed or any(number(own.get(i)) or number(sup.get(i)) for i in ids)}
    if drain and main:  # (MECH77: Drain Ailments' consumed types, Bleeding and Poison too; the bridge's types lack Poison)
        for ailment in DRAIN_CONSUMES:
            found.setdefault(ailment, DRAIN_AILMENTS)
    return found


# PoB's Best Ignite DPS when the main skill consumes Ignite (MECH52; MECH51's follow-up, an OVERCOUNT). PoB counts
# min(stacks, the most that count) Ignites on the boss all fight (CalcOffence.lua 5251-5277 / 5402: stacks = hit chance x
# Ignite chance x duration x hits per second; StackPotential = stacks / most). Consumed (KeywordPopups Consume: "Consuming
# a Debuff on an enemy causes the enemy to be immune to that Debuff for 1 seconds"; Ailments are Debuffs), an Ignite ends
# at the main skill's next hit, then the boss is immune 1 s, then waits to be Ignited again: it carries one
# last / (last + 1 s + wait), last = min(its duration, 1 / hits). Its wait: from the main skill's own Ignite,
# 1 / (hits x Ignite chance); from another skill's, taken as none (that skill's Ignite rate isn't read here: an upper
# bound, MEDIUM). Each Ignite PoB counts is taken to keep that share (MEDIUM where a line raises the most Ignites past 1:
# whether one Consume ends them all is UNVERIFIED, and taken as yes). Burning Ground made from Ignite DPS is left as PoB
# has it (whether it ends with the consumed Ignite: UNVERIFIED).
CONSUME_IGNITE_FIELDS = ("Speed", "HitChance", "IgniteChancePerHit", "IgniteDuration", "IgniteStackPotential")


def consumed_ignite_scale(potential: float, hits: float, chance: float, duration: float, own: bool) -> tuple[float, str]:
    """(the scale on PoB's Best Ignite DPS, how) for a main skill that consumes Ignite: the share of the fight the boss
    carries an Ignite over the min(potential, 1) PoB counts. potential: the Ignite source's StackPotential (unknown: 1)."""
    if hits <= 0:
        return 1.0, ""
    last = min(duration, 1.0 / hits) if duration > 0 else 1.0 / hits
    wait = 1.0 / (hits * chance) if own and chance > 0 else 0.0
    share = last / (last + models.CONSUME_IMMUNE + wait)
    counted = min(potential, 1.0) if potential > 0 else 1.0
    scale = min(1.0, share / counted)
    how = (f"consumed at the next hit ({last:.2f} s), then immune {models.CONSUME_IMMUNE:g} s"
           + (f", then {wait:.2f} s to Ignite again" if wait else ", reapplied at once (another skill's Ignite: an upper bound)")
           + f": Ignited {share:.0%} of the fight, PoB counts {counted:.0%}")
    return scale, how


# Ailments consumed by a skill beside the main one (MECH53; MECH52's item 2, an OVERCOUNT): a skill in another enabled group
# (its own stat sets or its supports holding a CONSUME_STATS stat) ends the boss's Freeze / Shock / Ignite / Chill each time it
# consumes it, then the boss is immune 1 s (KeywordPopups Consume) and waits for the main skill to reapply it. Its uses
# per second: the press model's (models.press_use_rate: its cooldown, else its duration, else the search's chosen rate, else 0);
# a triggered one at PoB's own rate for it (its useRates speed, MEDIUM). Each use is taken to find the ailment while the
# boss has it (an upper bound on what it ends: a use that misses consumes nothing). Uses are taken as random in time.
# With the share u the boss would carry it, consumed at r a second, reapplied after the 1 s immunity plus w:
# u / (1 + u x r x (1 s + w)) (a renewal: each consumption adds 1 s + w off). Not when the main skill consumes it itself
# (its own next hit ends it first: consumed_share).
def side_consumes(build, main_skill: str, groups: dict | None = None,
                  caps: dict | None = None) -> dict[str, tuple[float, str, str]]:
    """{ailment: (consumptions per second, how, confidence)} from skills beside the main one that consume it. `groups`:
    the bridge's groups (read when not given). A pressed consumer consumes at the rate its own damage is counted at
    (models.press_group_rate: its cooldown, else its duration, else the search's chosen rate, else 0; held to Cadence's pace
    and to `caps`, Assumptions.press_caps, computed here when not given; MECH54, CLAUDE.md's flat-rate rule: one rate
    for both sides); a triggered one at PoB's trigger rate, as Full DPS counts it."""
    groups = groups or build.call("groups")
    choices = models.press_choices(build)  # (the search's chosen press rates, MECH55)
    main_index = groups.get("mainSocketGroup")
    ctx = build.ctx
    candidates = {g["index"]: [n["name"] for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support")]
                  for g in groups["groups"] if g.get("enabled") is not False and g["index"] != main_index
                  and any(n.get("enabled", True) and CONSUME_TEXT.search(gem_of(ctx, n["name"], n.get("skillId")).get("description") or "")
                          for n in g.get("gems") or [])}
    names = sorted({n for found in candidates.values() for n in found if n != main_skill})
    if not names:
        return {}
    every = [stat for ids in CONSUME_STATS.values() for stat in ids]
    raw = build.call("activeSkills", levelStats=names, supportStats=every)
    consumers: dict[str, list[tuple[str, int, bool | str]]] = {}
    for skill in raw["skills"]:
        if skill.get("disabled") or skill.get("group") not in candidates or skill["name"] == main_skill:
            continue
        own, sup = skill.get("levelStats") or {}, skill.get("supportStats") or {}
        typed = _type_consumes(skill, minion=False)
        for ailment, ids in CONSUME_STATS.items():
            if ailment in typed or any(number(own.get(i)) or number(sup.get(i)) for i in ids):
                consumers.setdefault(ailment, []).append((skill["name"], skill["group"], bool(skill.get("triggered"))))
        for ailment in _minion_consumes(skill):  # (its minion's counted skill, at PoB's minion Speed; MECH57)
            minion_name = (skill.get("minionConsumes") or {}).get("skill") or "its minion's skill"
            consumers.setdefault(ailment, []).append((f"{skill['name']}: {minion_name}", skill["group"], "minion"))
    if not consumers:
        return {}
    indices = sorted({g for found in consumers.values() for _, g, _ in found})
    rates = {r.get("index"): r for r in build.call("useRates", indices=indices)["rates"]}
    by_index = {g["index"]: g for g in groups["groups"]}
    if caps is None:
        from builder.search.assumptions import Assumptions
        caps = Assumptions(build).press_caps(groups["groups"], set(indices))
    out = {}
    for ailment, found in consumers.items():
        total, parts, confidence = 0.0, [], "MEDIUM"
        for name, group, triggered in found:
            row = rates.get(group) or {}
            if triggered == "minion":
                uses, how, conf = number(row.get("minionSpeed")), "PoB's rate for the minion's skill", "MEDIUM"
            elif triggered:
                uses, how, conf = number(row.get("speed")), "PoB's trigger rate", "MEDIUM"
            else:
                cadence = any(x.get("enabled", True) and x["name"] in models.CADENCE_SUPPORTS
                              for x in (by_index.get(group) or {}).get("gems") or [])
                uses, how, conf = models.press_group_rate(row, models.CADENCE_PACE if cadence else None,
                                                          caps.get(group), models.press_choice(row, choices))
            if uses <= 0:
                continue
            total += uses
            parts.append(f"{name} ({uses:.2f} uses/s, {how})")
            confidence = "LOW" if conf == "LOW" else confidence
        if total > 0:
            out[ailment] = (total, "; ".join(parts), confidence)
    return out


# The boss Ignited by the build's other groups (MECH75; MECH74's follow-up, an UNDERCOUNT: the Ignited share,
# build.ailment_uptimes["Ignited"] and every reader of ailment_uptime(stats, "Ignite") (the Ignited box, "against
# Ignited" lines, Withered kept on the Ignited boss, Mana Remnants, Snap, Elemental Discharge) read the main skill's
# own Ignites only, 0 where it doesn't Ignite). Each other enabled group that hits (daze_hitter, as Conditions.evidence
# rates them for Daze, so no extra engine call) and Ignites as PoB scores it as the main skill (bridge.useRates'
# IgniteDPS > 0, IgniteChancePerHit, IgniteDuration, HitChance; MECH74's Grisly Pyre reads the same) adds hits x
# chance applications a second at its own use rate: a triggered skill at PoB's trigger rate (as side_consumes), a
# pressed one at skill_use_rate's (its cooldown, its duration, else the search's chosen press rate, else 0: no source,
# 0). Labelled: one hit per use landing (HitChance; a use's extra projectiles / repeats beyond PoB's Repeats not added,
# a lower reading), and the sources taken as independent. Skills daze_hitter says don't hit on use (the bridge's
# hitsOnUse, else DAZE_NOT_HITS: minion-making, Wall, degen-only and persistent skills) are left out, as is a skill in
# the main skill's own group: their Ignites aren't added (an UNDERCOUNT if one Ignites the boss, not modelled).


# Radiant Grief (MECH75; PATCH_CHECK H2-2, an UNDERCOUNT: PoB doesn't parse the line, and nothing here read it): "Enemies
# in your Presence are Ignited as though dealt 200 Base Fire Damage" (PoB Uniques/helmet.lua, the Current variant; patch
# 0.5.0, 2026-05-29: "now has ... 200 Base Fire Damage (previously 100)", the newest entry, checked with grep_notes.py
# --history "Radiant Grief"). Since 0.4.0 an igniting aura applies regular Ignites "with standard duration, and will
# reapply them after they expire the next time the aura applies flammability" (patch 0.4.0, 2025-12-12): the boss is
# taken as Ignited the whole time it stands in your Presence (the aura's reapply tick taken as at once, MEDIUM), 0 outside
# (the boss's distance: _boss_distance, the search's chosen distance where it chose one; your PresenceRadius). A consumer
# finds it reapplied after its 1 s immunity, as a ground (consumed_share's `ground`). Its own Ignite damage (20% of 200 Fire
# a second, scaled) isn't added: only the strongest Ignite deals damage, and a build's own hits' Ignite is usually far
# stronger (an UNDERCOUNT only where the build has no Ignite of its own, labelled).
PRESENCE_IGNITE = re.compile(r"^Enemies in your Presence are Ignited as though dealt (\d+) Base Fire Damage$", re.I)


def presence_ignite(texts: list[str], stats: dict) -> tuple[float, str]:
    """(share of the fight the boss stands Ignited in your Presence, how) from Radiant Grief's line (see the note above)."""
    line = next((t for t in texts if PRESENCE_IGNITE.match(t.strip())), None)
    if line is None:
        return 0.0, ""
    metres = _boss_distance(texts, stats)[0] / 10
    radius = number(stats.get("PresenceRadius")) / 10
    if radius >= metres:
        return 1.0, (f"'{line}': the boss {metres:g} m away, inside your {radius:.1f} m Presence: Ignited all the "
                     "fight (the aura reapplies it at once, MEDIUM)")
    return 0.0, (f"'{line}': the boss {metres:g} m away, outside your {radius:.1f} m Presence: 0 (standing closer is "
                 "the search's distance choice)")


def ignite_others(rows: dict, groups: list[dict], hitters: set[str], main_index,
                  choices: dict[str, float] | None = None) -> tuple[list[tuple[str, float, float, float]], str]:
    """([(name, hits per second, chance per hit, Ignite duration in s)], how) for the build's other groups that Ignite
    the boss (see the note above). `rows`: {group index: the bridge's useRates row} for the hitters' groups."""
    names = {g["index"]: [n["name"] for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in hitters]
             for g in groups if g.get("enabled") is not False}
    out, parts = [], []
    for index, row in sorted(rows.items(), key=lambda kv: str(kv[0])):
        if index == main_index or not names.get(index):
            continue
        chance = min(number(row.get("IgniteChancePerHit")), 100.0) / 100.0
        if number(row.get("IgniteDPS")) <= 0 or chance <= 0:
            continue
        if row.get("triggered"):
            uses, why = number(row.get("speed")), "PoB's trigger rate"
        else:
            uses, why, _ = skill_use_rate(row, choices)
        hits = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0
        if hits <= 0:
            continue
        duration = number(row.get("IgniteDuration")) or AILMENT_DURATION["Ignite"]
        name = row.get("name") or names[index][0]
        out.append((name, hits, chance, duration))
        parts.append(f"{name}'s Ignites ({hits:.2f} hits/s at {why}, {chance:.0%} a hit, {duration:.1f} s each)")
    how = ("; ".join(parts) + " (MECH75: each as PoB scores it as the main skill, the sources taken as independent; "
           "one hit per use)") if parts else ""
    return out, how


BUILDUP_SHARED = ("Freeze", "Electrocute", "Pin")  # (MECH87: the bars every hit fills; models.others_fill)


def buildup_others(rows: dict, groups: list[dict], hitters: set[str], main_index,
                   choices: dict[str, float] | None = None) -> dict:
    """{ailment: bars a second at PoB's threshold, "how": text} from the build's other hitting groups' Freeze /
    Electrocute / Pin buildup (MECH87; models.others_fill's note): each group's useRates row's <Ailment>BuildupAvg (% of
    PoB's threshold a hit, as PoB scores it as the main skill) x its hits a second (PoB's trigger rate, else
    skill_use_rate at the press choices, as ignite_others; one hit per use). {} when none builds any."""
    names = {g["index"]: [n["name"] for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in hitters]
             for g in groups if g.get("enabled") is not False}
    out: dict = {}
    parts = []
    for index, row in sorted(rows.items(), key=lambda kv: str(kv[0])):
        if index == main_index or not names.get(index):
            continue
        each = {a: number(row.get(f"{a}BuildupAvg")) / 100 for a in BUILDUP_SHARED}
        if not any(v > 0 for v in each.values()):
            continue
        if row.get("triggered"):
            uses, why = number(row.get("speed")), "PoB's trigger rate"
        else:
            uses, why, _ = skill_use_rate(row, choices)
        hits = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0
        name = row.get("name") or names[index][0]
        if hits <= 0:
            parts.append(f"{name} not counted (no uses: no cooldown, duration or press chosen)")
            continue
        for ailment, v in each.items():
            if v > 0:
                out[ailment] = out.get(ailment, 0.0) + hits * v
        parts.append(f"{name} {hits:.2f} hits/s at {why} x " + ", ".join(
            f"{a} {v:.2%}" for a, v in each.items() if v > 0) + " of the bar a hit")
    if not any(a in out for a in BUILDUP_SHARED):
        return {}
    out["how"] = "; ".join(parts) + " (MECH87: each group's buildup as PoB scores it as the main skill; one hit per use)"
    return out


# The other hitting groups' Armour Break (MECH89; MECH88's Handover step 2, GEM21's routing, an UNDERCOUNT before: only
# the main skill's hits Broke Armour in _armour_broken, so High Velocity Rounds beside Armour Piercing Rounds, or a
# second skill with Armour Break I-III, never Fully Broke the boss). Each enabled group beside the main one whose skill
# hits on use (daze_hitter, the same hitters as buildup_others) Breaks with (a) its own and its supports' BREAK_STATS
# (game files 26650308, GrantedEffectStatSets / PerLevel: Armour Piercing Rounds, Armour Breaker and Resonating Shield's
# apply_X_armour_break_on_hit; Smash to Smithereens' fire share; the Armour Break, Uul-Netol and Styrn's supports) and
# (b) the build's own per-hit Break lines that qualify for its skill types (an item's "Hits Break N Armour" breaks with
# every hit). Each at its group's hits a second (its useRates row: PoB's trigger rate, else skill_use_rate at the press
# choices, x HitChance; one hit per use, as buildup_others) and its own hit and crit (the row's <Type>HitAverage /
# CritAverage, PoB's, as _armour_break_source reads the main skill's), x its own Armour Demolisher / Uruk's Smelting
# (BREAK_MORE_STAT). A group whose skill or supports say cannot_break_armour (High Velocity Rounds, Sunder, Leap Slam,
# Battershout) breaks nothing. Not rated for side groups (0, labelled): the Heavy Stun, "when Freezing / Electrocuting"
# and "Stunning" wordings (they ride the boss's shared bars, which _armour_broken rates once from the main skill), and
# the support stats outside BREAK_STATS (Rending Apex's final strike, Mark for Death's Marked target, Brittle Armour's
# Freeze, Tumult's Frenzy Charge, Corrosion's Poison, Outmaneuver's Parried: not read for the main skill either).
CANNOT_BREAK = "cannot_break_armour"
# Mark for Death (MECH89, an UNDERCOUNT before: no reader): "Hits against targets Marked by Supported Skills Break Armour
# equal to {0}% of Physical damage dealt" (gem_stat_descriptions; SupportMarkForDeathPlayer 10, ...PlayerTwo 15; PoB
# sup_str.lua maps it only to Condition:CanArmourBreak, its own Armour Break output, which no box reads). It supports a
# Mark, so every hit of yours on the Marked boss Breaks: the main skill's and each other hitting group's Physical hit x
# that share, at the share of the fight the boss carries the Mark (Conditions._marked_share, models.mark_model's).
MARK_BREAK_STAT = "marked_target_%_physical_damage_taken_as_armour_break"
MARK_BREAK_LINE = "Break Armour equal to {}% of Physical Damage dealt"
# Brittle Armour (MECH89, an UNDERCOUNT before: no reader): "Hits against targets Frozen by Supported Skills Break Armour
# equal to {0}% of Physical damage dealt" (gem_stat_descriptions; SupportBrittleArmourPlayer, PoB sup_int.lua; it
# supports Attacks, Damage skills and crossbow ammo). Counted when it supports the main skill: every hit of yours (the
# main skill's and each other hitting group's) x that share of its Physical hit, while the boss is Frozen
# (models.immobilised_share, the main skill's Freeze on the boss's one bar with the other groups' buildup in it: those
# Freezes are taken as the supported skill's, MEDIUM). On a side skill only: 0, labelled (its own Freeze share isn't
# modelled apart from the bar).
BRITTLE_BREAK_STAT = "freeze_applies_%_physical_damage_taken_as_armour_break"
CONDITIONAL_BREAKS = {"_mark_break": ("Mark for Death", "Marked"), "_brittle_break": ("Brittle Armour", "Frozen")}
SIDE_BREAK_SKIP = re.compile(r"heavy stun|stunning|when (?:freezing|electrocuting)", re.I)


def break_others(skills: list[dict], rows: dict, groups: list[dict], hitters: set[str], main_index,
                 build_lines: list[str], every: dict, choices: dict[str, float] | None = None,
                 marked: tuple | None = None, frozen: tuple | None = None) -> list[tuple]:
    """[(name, Armour broken a second, how)] for the build's other hitting groups (see the note above). `skills`: the
    bridge's activeSkills entries with levelStats and supportStats (BREAK_STATS, BREAK_MORE_STAT, CANNOT_BREAK) for the
    hitters; `rows`: {group index: useRates row}; `build_lines`: the build's lines that aren't gem lines; `marked`:
    (Mark for Death's %, the Marked share, how) or None (MARK_BREAK_STAT); `frozen`: Brittle Armour's, the same."""
    names = {g["index"]: [n["name"] for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in hitters]
             for g in groups if g.get("enabled") is not False}
    by_name: dict[str, dict] = {}
    for s in skills:
        if not s.get("disabled"):
            by_name.setdefault(s["name"], s)
    out = []
    for index, row in sorted(rows.items(), key=lambda kv: str(kv[0])):
        if index == main_index or not names.get(index):
            continue
        name = row.get("name") or names[index][0]
        skill = by_name.get(name) or by_name.get(names[index][0]) or {}
        got = {stat: number(value) for part in ("levelStats", "supportStats")
               for stat, value in (skill.get(part) or {}).items() if value}
        types = set((every.get(names[index][0]) or {}).get("skillTypes") or ())
        texts = [BREAK_STATS[stat].format(f"{value:g}") for stat, value in got.items() if stat in BREAK_STATS]
        texts += [t for t in build_lines if ARMOUR_BREAK_LINE.search(t) and qualifies(t, types)]
        conditional = [(n, st, c) for (n, st), c in zip(CONDITIONAL_BREAKS.values(), (marked, frozen)) if c and c[1] > 0]
        if not texts and not conditional:
            continue
        if got.get(CANNOT_BREAK):
            out.append((name, 0.0, f"{name} cannot Break Armour (cannot_break_armour)"))
            continue
        if row.get("triggered"):
            uses, why = number(row.get("speed")), "PoB's trigger rate"
        else:
            uses, why, _ = skill_use_rate(row, choices)
        side = {**row, "Speed": uses, "HitChance": number(row.get("HitChance")) or 100.0}
        if (own := number(row.get("speed"))) > 0:  # (its Poison as PoB scores it at its own speed, to its use rate)
            side["PoisonDPS"] = number(row.get("PoisonDPS")) * min(uses / own, 1.0)
        if models.hits_per_second(side) <= 0:
            out.append((name, 0.0, f"{name} not counted (no uses: no cooldown, duration or press chosen)"))
            continue
        rate, parts = 0.0, []
        for text in texts:
            if SIDE_BREAK_SKIP.search(text):
                parts.append(f"{text[:50]} (on a shared bar: rated from the main skill only, 0 here)")
                continue
            if (r := _armour_break_source(text, side)) is None:
                continue
            rate += r[0]
            parts.append(f"{text[:50]} ({r[3]})")
        for cname, state, c in conditional:
            if r := _armour_break_source(MARK_BREAK_LINE.format(f"{c[0]:g}"), side):
                rate += r[0] * c[1]
                parts.append(f"{cname} {c[0]:g}% of its Physical hit on the {state} boss ({r[3]}) x {c[1]:.0%}")
        more = number((skill.get("supportStats") or {}).get(BREAK_MORE_STAT))
        if more:
            rate *= 1 + more / 100
            parts.append(f"{more:+g}% more from its support")
        out.append((name, rate, f"{name} at {why}: " + "; ".join(parts)))
    return out


# Styrn's Ferocity's Crush (MECH87; RESEARCH30 R30-1: "Supported Skills Crush targets they Hit for 8 seconds",
# support_knights_ferocity_crush_on_hit_ms 8000; Crushed = -15 points of Physical Damage Reduction, KeywordPopups
# Crushed, PoB CalcSetup 106; one Crush, no stacking). PoB maps the stat in the gem's own statMap (Data/Skills/other.lua:
# Condition:Crushed as a GlobalEffect Debuff), and CalcPerform merges any enabled skill's Debuff onto the enemy, so the
# boss is Crushed all fight whichever group Styrn's supports (probe POE2T4088: Shield Charge 95.5 -> 138.0 DPS with
# Styrn's on the main group, and the same 138.0 with it only on a second Shield Charge group the build never uses: an
# OVERCOUNT there). The game Crushes for 8 s after each supported hit, so with the supported groups' hits random in
# time the boss is Crushed 1 - e^(-8 x hits a second) of the fight (R30-1). On the main group that is the whole fight
# (left to PoB). Otherwise the build hands PoB +15 x (1 - uptime) of EnemyPhysicalDamageReduction, which PoB adds to the
# boss's own (CalcOffence 4315), so the -15 averages over the fight (exact while the reduction stays inside PoB's caps).
# Another Crush source (a Forge Maul's "Crushes Enemies on Hit", "Nearby Enemies are Crushed") keeps it on: not scaled.
# Lines "against Crushed enemies" from other sources still read PoB's always-on Crushed (none found in the tranche; LOW).
STYRNS_FEROCITY = "Styrn's Ferocity"
STYRN_CRUSH_SECONDS = 8.0  # support_knights_ferocity_crush_on_hit_ms 8000 (GrantedEffectStatSets, 26650308)
CRUSH_PDR = 15.0  # KeywordPopups Crushed: "lowers Physical Damage Reduction by 15%"; PoB CalcSetup 106
OTHER_CRUSH = re.compile(r"\bcrush(?:es)? enemies on hit\b|\benemies are crushed\b", re.I)


# Dark Entropy (an anoint, DeliriumAnoint_DarkEntropy: withered_enemies_deal_+%_damage -1, "Withered also causes enemies to
# deal 1% reduced Damage"; MECH87, an UNDERCOUNT of defences: PoB leaves it unparsed, ModCache). Per Withered stack
# (RESEARCH30 R30-8 (1), MEDIUM: the line adds to the Withered debuff's own effect, which each of the up to 10 stacks
# applies), so the boss deals N x 1% reduced Damage with N the Withered stacks the resolver keeps on it (PoB's
# multiplierWitheredStackCount, _withered_stacks): handed to PoB as "Nearby Enemies deal 1% reduced Damage" x N (parses
# whole to the enemy's Damage INC: probe POE2T4088). The row: 1% once while Withered (the lower reading).
DARK_ENTROPY = re.compile(r"^Withered also causes enemies to deal (\d+(?:\.\d+)?)% reduced Damage$", re.I)


def dark_entropy_mods(texts: list[str], stacks: float) -> tuple[list[dict], list[dict]]:
    """Dark Entropy's reduced boss damage at the Withered stacks on the boss (see the note above)."""
    per = sum(float(m.group(1)) for t in texts if (m := DARK_ENTROPY.match(t.strip())))
    if per <= 0:
        return [], []
    n = max(0.0, min(number(stacks), 10.0))
    note = {"label": "Dark Entropy: the Withered boss deals less Damage*", "value": f"{per * n:g}% reduced",
            "confidence": "MEDIUM",
            "basis": (f"{per:g}% per Withered stack (RESEARCH30 R30-8, MEDIUM) x {n:g} stacks the build keeps on the boss"
                      + (f"; if it applies once: {per:g}%" if n > 1 else "") if n > 0
                      else "the build keeps no Withered on the boss: none")}
    return ([models.line("Nearby Enemies deal 1% reduced Damage", per * n)] if n > 0 else []), [note]


def styrn_crush(groups: list[dict], rows: dict, main_index, choices: dict[str, float] | None,
                texts: list[str]) -> tuple[float, float, str]:
    """(the share of the fight the boss is Crushed by Styrn's Ferocity, the EnemyPhysicalDamageReduction to hand PoB,
    how) (see the note above); (1.0, 0.0, "") without Styrn's Ferocity. `rows`: {group index: useRates row}."""
    supported = [g for g in groups if g.get("enabled") is not False and any(
        n.get("name") == STYRNS_FEROCITY and n.get("enabled", True) for n in g.get("gems") or [])]
    if not supported:
        return 1.0, 0.0, ""
    if any(g["index"] == main_index for g in supported):
        return 1.0, 0.0, (f"{STYRNS_FEROCITY} supports the main skill: each hit Crushes the boss for "
                          f"{STYRN_CRUSH_SECONDS:g} s, so it stays Crushed (-{CRUSH_PDR:g}% Physical Damage Reduction)")
    if other := next((t for t in texts if OTHER_CRUSH.search(t)), None):
        return 1.0, 0.0, f"the boss is Crushed all fight by \"{other}\" (PoB), so {STYRNS_FEROCITY}'s uptime adds nothing"
    hits, parts = 0.0, []
    for g in supported:
        row = rows.get(g["index"]) or {}
        name = row.get("name") or next((n["name"] for n in g.get("gems") or [] if n.get("name") != STYRNS_FEROCITY), "?")
        if row.get("triggered"):
            uses, why = number(row.get("speed")), "PoB's trigger rate"
        elif row:
            uses, why, _ = skill_use_rate(row, choices)
        else:
            uses, why = 0.0, "no use rate"
        h = uses * min(number(row.get("HitChance")) or 100.0, 100.0) / 100.0
        hits += h
        parts.append(f"{name} {h:.2f} hits/s ({why})")
    up = 1.0 - math.exp(-STYRN_CRUSH_SECONDS * hits) if hits > 0 else 0.0
    return up, CRUSH_PDR * (1.0 - up), (
        f"{STYRNS_FEROCITY} on {', '.join(parts)}: each hit Crushes the boss for {STYRN_CRUSH_SECONDS:g} s, so it is Crushed "
        f"{up:.0%} of the fight (hits random in time, R30-1, MEDIUM); PoB Crushes it all fight, so "
        f"+{CRUSH_PDR * (1.0 - up):.1f}% Physical Damage Reduction is handed back (without the side skill's hits: none "
        f"Crushed; with Styrn's on the main skill: always)")


def side_consumed_ignite_scale(potential: float, hits: float, chance: float, own: bool, rate: float) -> tuple[float, str]:
    """(the scale on PoB's Best Ignite DPS, how) when a skill beside the main one consumes Ignite `rate` times a second:
    of the min(potential, 1) PoB counts, u / (1 + u x r x (1 s + w)) kept (side_consumed); w the wait for the main
    skill's own Ignite, 1 / (hits x chance), or none for another skill's (an upper bound, as consumed_ignite_scale)."""
    counted = min(potential, 1.0) if potential > 0 else 1.0
    wait = 1.0 / (hits * chance) if own and hits > 0 and chance > 0 else 0.0
    scale = 1.0 / (1.0 + counted * rate * (models.CONSUME_IMMUNE + wait))
    how = (f"{rate:.2f} consumptions/s (each use taken to find it), then immune {models.CONSUME_IMMUNE:g} s"
           + (f", then {wait:.2f} s to Ignite again" if wait else ", reapplied at once (an upper bound)")
           + f": Ignited {counted * scale:.0%} of the fight, PoB counts {counted:.0%}")
    return scale, how


SIDE_CONSUMED_BOXES = {"conditionEnemyShocked": "Shock", "conditionEnemyIgnited": "Ignite",
                       "conditionEnemyBleeding": "Bleed"}  # (MECH75: Bleed consumers)


def side_consumed(share: float, stats: dict, ailment: str, hits: float, chance: float, ground: float = 0.0) -> float:
    """`share` of the fight the boss carries `ailment`, cut by the skills beside the main one that consume it
    (side_consumes, `stats["_side_consumes"]`): share / (1 + share x r x (1 s + the wait to reapply it))."""
    side = (stats.get("_side_consumes") or {}).get(ailment)
    if not side or share <= 0 or ailment in (stats.get("_consumes") or {}):
        return share
    wait = (1.0 - ground) / (hits * chance) if hits > 0 and chance > 0 else 0.0
    rate = side[0] * (1.0 - consume_reapply(stats, ailment))  # (MECH85: a reapplied one isn't lost)
    return share / (1.0 + share * rate * (models.CONSUME_IMMUNE + wait))


# Endless Circuit (MECH85; the catalog's damaging-ailments entry, an UNDERCOUNT of the Shocked share for a build that
# consumes Shock): "25% chance on Consuming a Shock on an Enemy to reapply it" (tree.json; PoB's ModCache leaves it
# unparsed, no mods). A consumption that reapplies leaves the boss Shocked with no 1 s immunity and no wait, so the main
# skill's Shock lasts 1 / (1 - p) consuming hits on average (consumed_share's `kept`) and a consumer beside it removes it
# at (1 - p) x its rate (side_consumed). The reapplied Shock taken as a fresh one at the consumed Shock's magnitude
# (MEDIUM: the line's words; whether it carries the old magnitude or the consumer's is UNVERIFIED, so the Shock effect
# isn't changed). Chances from several lines add, capped below 100%. Only Shock has such a line, and only this passive
# carries it (checked: Stats.json ids holding reapply / re_appl / not_consume: one, reapply_enemy_shock_on_consuming_enemy_
# shock_chance_%, used by PassiveSkills lightning38__ alone, no Mods or GrantedEffectStatSets row; tree.json, uniques.json
# and mods.json for "on Consuming a ... reapply"; patch notes: 0.3.0 took it off Lightning Conduit's quality).
CONSUME_REAPPLY = re.compile(r"^(\d+(?:\.\d+)?)% chance on Consuming an? (Shock) on an Enemy to reapply it$", re.I)


def consume_reapply_chances(texts: list[str]) -> dict[str, float]:
    """{ailment: chance (0-0.99) a consumption reapplies it} from the build's lines (Endless Circuit)."""
    out: dict[str, float] = {}
    for t in texts:
        if m := CONSUME_REAPPLY.match(t.strip()):
            out[m.group(2).title()] = min(out.get(m.group(2).title(), 0.0) + float(m.group(1)) / 100, 0.99)
    return out


def consume_reapply(stats: dict, ailment: str) -> float:
    return number((stats.get("_consume_reapply") or {}).get(ailment))


# Culling Strike against the boss, hit by hit (MECH57; plans/kw-culling-strike.md item 1, an OVERCOUNT). KeywordPopups
# CullingStrike: "Unique at 5%. These thresholds are checked before the damage of the Hit is applied". PoB prices a Cull
# as the whole last t of the pool saved: Full Culling DPS = combined x (1 / (1 - t) - 1) (Calcs.lua 479-482, CalcOffence
# 4069-4079), as if damage were a smooth flow. But the Cull lands only at the first hit after the boss falls under t: with
# hits every 1 / h and the boss crossing t at a hit, the hits that would have finished it number E[ceil(left / hit)] =
# t / d (left spread evenly over one hit's damage d; exact), and the Cull takes 1 of them, so it saves t x T - 1 / h of
# the fight's T = pool / DPS (none when one hit takes more than t: a big-hit build gains nothing). Where damage over time
# brings the boss under t between hits, the wait to the next hit is half a gap on average: wait = (1 - dot share / 2) / h.
# Saved share s = max(0, t - wait / T); the gain s / (1 - s) replaces PoB's t / (1 - t) (a scale on its Full Culling
# DPS entry). MEDIUM: T from the preset's Life (+ ES, bosses.life_share_of_pool) over Full DPS before the Cull; forms or
# lives each Culled at their own t (bosses.py's reading, UNVERIFIED) would each pay the wait, a further cut not taken.
# Crit-only Culls (CritCanCull, "Critical Hits have Culling Strike"; MECH58): PoB scales t by 1 - (1 - crit)^hitRate
# (CalcOffence 4072), a guess that a crit lands within a second. Here the crit-only source keeps its whole raw t (x the
# INC) and pays the wait to the next crit, (1 / c - dot share / 2) / hits (hits until a crit are geometric, mean 1 / c),
# from bridge.cullSplit; a regular Cull beside it pays the next hit's wait at its own t, and the larger saving counts
# (MEDIUM: a hit that both crits and is under the higher crit t is a little more than the max; a small undercount).
# Where another skill than the main one Culls (PoB's Full DPS takes the largest CullMultiplier of its skills, Calcs.lua
# mergeFullDPSPass "cull" mode), MECH64 prices that skill's Cull at its own hits (bridge.useRates' cullMultiplier,
# hitChance, hitDPS, cullSplit, the skill calculated as the main one, as calcFullDPS does): its hits come every 1 / h,
# and the boss crosses t under the whole build's damage, at one of its hits only for its share f of the damage, so the
# wait is (1 - (1 - f) / 2) / h (the dot_share formula with the rest of the build as "over time"). A main skill's own
# smaller Cull is priced as above; the larger saving counts. A Cull on another skill that only Heavy Contact or
# Murderous Intent gives keeps PoB's price (LOW: that skill's Heavy Stuns / Empowered uses aren't read), as does a
# culling skill no rate was read for.
CULL_WINDOW_FIELDS = ("CullMultiplier", "Speed", "HitChance", "TotalDPS", "TotalDotDPS", "FullDPS")


# Life bars (MECHC31; RESEARCH26 R26-64, olroth-arbiter-life-heal.md, MEDIUM: two 0.5 guides and the 0.4.0 note): the
# Arbiter of Ash changes phase at 55% and heals to full, so the fight takes 1.45 of its Life bars; Olroth revives once at
# ~70%: 1.7 bars (maxroll 4 Nov 2026 and a second guide). The King in the Mists' 1.75 is already inside its Life figure
# (bosses.py: both forms counted), so none here. The worst case's Life is the Arbiter of Ash's (the user's "Keep as is"
# on the 9.5M, 12 Nov 19:27: the Life figure is NOT changed), so it takes the Arbiter's 1.45. Only the fight's length
# moves (the ramps: presence_fight, every Cull reader's fight) and the Cull, which lands on the last bar only: its saving
# is the threshold's share of ONE bar, so threshold / bars of the whole fight (the manager, 15 Nov 22:15: "only check
# whether the bars change the Cull share and the ramps"). Before, one bar each: the ramps undercounted (fight too short)
# and the Cull overcounted (its window 1.45-1.7x too big a share).
LIFE_BARS = {"Arbiter of Ash": 1.45, "Olroth, Origin of the Fall": 1.7}


def life_bars(name: str) -> float:
    """How many of the preset's Life bars the fight takes (the worst case: the Arbiter of Ash's, whose Life it has)."""
    if name == getattr(bosses, "WORST", None):
        return LIFE_BARS["Arbiter of Ash"]
    return LIFE_BARS.get(name, 1.0)


def boss_pool(name: str) -> float:
    """The Life + Energy Shield the player takes off this boss preset in the fight (all its Life bars: life_bars)."""
    return bosses.PRESETS[name].life / bosses.life_share_of_pool(name) * life_bars(name)


def cull_window_scale(full_mult: float, main_mult: float, pool: float, dps: float, hits: float, hit_dps: float,
                      dot_dps: float, split: dict | None = None, heavy: float = 0.0,
                      empowered: tuple[float, str] | None = None,
                      other: dict | None = None, bars: float = 1.0) -> tuple[float, str, str]:
    """(the scale on PoB's Full Culling DPS, how, confidence): full_mult, PoB's Full DPS cull multiplier; main_mult, the
    main skill's CullMultiplier; pool, the boss's Life + ES; dps, Full DPS before the Cull; hits, the main skill's hits
    per second; hit_dps / dot_dps, its hit and damage-over-time DPS (for the wait after the boss falls under t); split,
    bridge.cullSplit's raw thresholds (critical, regular, inc, critChance %), for a crit-only Cull; heavy, the main
    skill's Heavy Stuns of the boss per second, for a Cull only Heavy Contact gives (split's heavyOnly; MECH59);
    empowered, (the main skill's Empowered uses per second, how), for a Cull only Murderous Intent gives (split's
    empowerOnly; MECH63): it lands on the first Empowered use under the threshold, as Heavy Contact's on a Heavy Stun;
    other (MECH64), where another skill than the main one has PoB's Cull: {name, mult (its CullMultiplier), hits (its
    hits per second), hitDps (its hit DPS x its count), split (its own cullSplit)}."""
    t = 1.0 - 1.0 / full_mult if full_mult > 1.0 else 0.0
    if t <= 0:
        return 1.0, "", ""
    bars = max(bars, 1.0)  # (MECHC31: the Cull lands on the last of `bars` Life bars: thresholds below are of one bar)
    if main_mult < full_mult - 1e-6:
        osplit = (other or {}).get("split") or {}
        ohits = float((other or {}).get("hits") or 0.0)
        if (other is None or abs(float(other.get("mult") or 0.0) - full_mult) > 1e-3 * full_mult or ohits <= 0
                or dps <= 0 or pool <= 0 or osplit.get("heavyOnly") or osplit.get("empowerOnly")):
            why = ("its Cull lands only on Heavy Stuns or Empowered uses, which aren't read for it"
                   if osplit.get("heavyOnly") or osplit.get("empowerOnly") else "its hit rate isn't read")
            return 1.0, (f"another skill than the main one ({(other or {}).get('name') or 'unread'}) Culls, and {why}: "
                         "PoB's whole-window price kept (a possible overcount)"), "LOW"
        ohit = min(max(float(other.get("hitDps") or 0.0), 0.0), dps)
        oscale, ohow, _ = cull_window_scale(full_mult, full_mult, pool, dps, ohits, ohit, dps - ohit, osplit,
                                                bars=bars)
        gain, how = oscale * (full_mult - 1.0), f"{other.get('name')}'s Cull (not the main skill's): {ohow}"
        if main_mult > 1.0:  # (the main skill's own, smaller Cull, at its own hits; the larger saving counts)
            mscale, mhow, _ = cull_window_scale(main_mult, main_mult, pool, dps, hits, hit_dps, dot_dps, split, heavy,
                                                empowered, bars=bars)
            if mscale * (main_mult - 1.0) > gain:
                gain, how = mscale * (main_mult - 1.0), (f"the main skill's own Cull saves more than "
                                                          f"{other.get('name')}'s: {mhow}")
        return min(gain / (full_mult - 1.0), 1.0), how, "MEDIUM"
    if hits <= 0 or dps <= 0 or pool <= 0:
        return 1.0, "no hit rate or DPS read: PoB's whole-window price kept (a possible overcount)", "LOW"
    fight = pool / dps
    dot_share = dot_dps / (hit_dps + dot_dps) if hit_dps + dot_dps > 0 else 0.0
    wait = (1.0 - dot_share / 2.0) / hits
    saved = max(0.0, t / bars - wait / fight)
    lands = f"the first hit under it, {wait:.2f} s later on average ({hits:.2f} hits/s)"
    split = split or {}
    critical, regular = float(split.get("critical") or 0.0), float(split.get("regular") or 0.0)
    inc = float(split.get("inc") or 1.0)
    heavy_only = bool(split.get("heavyOnly")) and regular > 0
    # (MECH63) a Cull only Murderous Intent gives: on Empowered uses, evenly spaced as Heavy Contact's stuns (MEDIUM)
    empower_only = bool(split.get("empowerOnly")) and regular > 0 and not heavy_only
    if empower_only:
        rate, empowered_how = empowered or (0.0, "no Empowered use rated")

        def regular_saving(rt: float) -> float:
            window, gap = rt * fight, 1.0 / rate if rate > 0 else float("inf")
            return (window - gap / 2 if window > gap else window * window / (2 * gap)) / fight
        regular_lands = (f"the first Empowered use under it (Murderous Intent), one every {1.0 / rate:.1f} s: "
                         f"{empowered_how}" if rate > 0 else
                         f"an Empowered use (Murderous Intent), and the main skill is Empowered none: {empowered_how}")
    elif heavy_only:
        # (MECH59) Heavy Contact's Cull lands only on a Heavy Stun: stuns every 1 / heavy s, the boss crossing t at a
        # uniform point between two, so the window t x T saves t x T - gap / 2, or (t x T)^2 / (2 x gap) when the
        # window is shorter than the gap (exact for evenly spaced stuns; heavy_stun_rate's own spacing, MEDIUM)
        def regular_saving(rt: float) -> float:
            window, gap = rt * fight, 1.0 / heavy if heavy > 0 else float("inf")
            return (window - gap / 2 if window > gap else window * window / (2 * gap)) / fight
        regular_lands = (f"the first Heavy Stun under it (Heavy Contact), one every {1.0 / heavy:.1f} s" if heavy > 0
                         else "a Heavy Stun (Heavy Contact), and the main skill Heavy Stuns the boss none")
    else:
        def regular_saving(rt: float) -> float:
            return max(0.0, rt - wait / fight)
        regular_lands = f"the first hit under the regular threshold, {wait:.2f} s later ({hits:.2f} hits/s)"
    gated = heavy_only or empower_only  # (a regular Cull that lands only on some uses)
    if critical > regular or (gated and critical > 0):
        c = float(split.get("critChance") or 0.0) / 100.0
        crit_t, regular_t = critical * inc / 100.0 / bars, regular * inc / 100.0 / bars
        crit_wait = (1.0 / c - dot_share / 2.0) / hits if c > 0 else float("inf")
        crit_saved = max(0.0, crit_t - crit_wait / fight)
        regular_saved = regular_saving(regular_t) if regular_t > 0 else 0.0
        if crit_saved >= regular_saved:
            saved, t_shown = crit_saved, crit_t
            lands = (f"the first Critical Hit under it, {crit_wait:.2f} s later on average ({c:.0%} crit chance x "
                     f"{hits:.2f} hits/s; PoB's own {t:.2%} scales its {crit_t:.1%} by a crit within a second)")
        else:
            saved, t_shown = regular_saved, regular_t
            lands = (f"{regular_lands}, threshold {regular_t:.1%} (the crit-only {crit_t:.1%} saves less at {c:.0%} "
                     "crit chance)")
    elif gated:
        t_shown = regular * inc / 100.0 / bars
        saved, lands = regular_saving(t_shown), regular_lands
    else:
        t_shown = t / bars
    scale = (saved / (1.0 - saved)) / (t / (1.0 - t))
    how = (f"PoB counts the last {t:.1%} of the boss's {pool:,.0f} Life + ES saved (x{full_mult:.3f}); the Cull "
           f"(threshold {t_shown:.1%} of the fight" + (f": {t_shown * bars:.1%} of the last of {bars:g} Life bars, "
                                                       "R26-64" if bars > 1 else "") + f") lands at {lands}, in a "
           f"{fight:.1f} s fight: "
           f"{saved:.2%} saved (x{1.0 / (1.0 - saved):.3f})")
    return min(scale, 1.0), how, "MEDIUM"


# Culled on Block (MECH60; an UNDERCOUNT before: PoB ignores the line, coverage-report "PoB ignores the line"). Doomgate's
# "Enemies are Culled on Block" (Mods UniqueCullingStrikeOnBlock1, stat culling_strike_enemies_on_block; checked: Mods
# for every row holding stat 18444, one row; second source not checked, UNVERIFIED that no other item or passive grants
# it, which moves nothing here: the model reads the line wherever it is equipped). Blocking the boss's hit Culls it when
# it is under the Unique threshold (KeywordPopups CullingStrike: 5%, here bosses.cull_threshold's share of the pool, x
# the main skill's increased Culling Strike Threshold, MEDIUM: the item's Cull may not read a skill's INC). Blocks come
# at random, rate r = the boss's blockable hits you Block per second (boss_hits.block_share over
# bosses.benefit_skill_time, the "when hit" benefit pace: the fewest hits the tables allow, LOW); with W = t x T seconds
# left once the boss is under t, the first Block comes at an exponential wait, so the Cull saves W - (1 - e^(-rW)) / r
# (exact for Poisson blocks). Where the build Culls by hits too, the larger of the two savings counts (a small undercount).
CULL_ON_BLOCK_LINE = re.compile(r"^Enemies are Culled on Block\s*$", re.I | re.M)
CULL_ON_BLOCK_FIELDS = ("EvadeChance", "EffectiveBlockChance", "enemySkillTime")


# Murderous Intent's Cull where PoB gives none (MECH64; MECH63's probe POE2T588: with Unleash or a Warcry, PoB makes no
# CanCull, an UNDERCOUNT). The gem: "Skills Empowered by Supported Skills have Culling Strike" (KeywordPopups
# CullingStrike: Unique at 5%). It lands at the first Empowered use of the main skill after the boss falls under t:
# uses every 1 / rate s (Assumptions.empowered_uses: Unleash's uses, a Warcry's Empowered attacks), the boss crossing t
# at a uniform point between two, so the window W = t x T saves W - gap / 2, or W^2 / (2 x gap) when W is shorter than
# the gap (the same reading as Heavy Contact's and PoB-given Murderous Intent's, MEDIUM: Empowered uses come in bursts
# after each Warcry or Unleash, not evenly; bursts make the wait longer, so this leans high, the confidence says so).
def empowered_cull_saving(threshold: float, pool: float, dps: float, rate: float) -> tuple[float, str]:
    """(share of the fight Murderous Intent's Cull saves, how): threshold, the Cull threshold as a share of the boss's
    Life + ES; pool, that Life + ES; dps, the build's DPS before the Cull; rate, the main skill's Empowered uses/s."""
    if threshold <= 0 or pool <= 0 or dps <= 0 or rate <= 0:
        return 0.0, "no Empowered use of the main skill, or no DPS read: no Cull"
    fight, gap = pool / dps, 1.0 / rate
    window = threshold * fight
    saved = (window - gap / 2 if window > gap else window * window / (2 * gap)) / fight
    return saved, (f"the Cull (threshold {threshold:.2%}) lands at the first Empowered use under it, one every {gap:.1f} s,"
                   f" in a {fight:.1f} s fight: {saved:.2%} saved (x{1.0 / (1.0 - saved):.3f})")


def block_cull_saving(threshold: float, pool: float, dps: float, blocks: float) -> tuple[float, str]:
    """(share of the fight a Cull on Block saves, how): threshold, the Cull threshold as a share of the boss's Life + ES;
    pool, that Life + ES; dps, the build's DPS before any Cull; blocks, the boss's hits you Block per second."""
    if threshold <= 0 or pool <= 0 or dps <= 0:
        return 0.0, "no DPS or threshold read"
    if blocks <= 0:
        return 0.0, "the build Blocks none of the boss's hits, so the boss is never Culled on Block"
    fight = pool / dps
    window = threshold * fight
    saved = window - (1.0 - math.exp(-blocks * window)) / blocks
    share = max(0.0, saved / fight)
    return share, (f"the boss is under the {threshold:.1%} threshold for the last {window:.2f} s of a {fight:.1f} s fight; "
                   f"you Block {blocks:.2f} of its hits a second (at random), so the first Block in that window saves "
                   f"{saved:.2f} s: {share:.2%} of the fight (x{1.0 / (1.0 - share):.3f})")


# Seconds the boss has been in your Presence (MECH59; plans/kw-culling-strike.md item 5, term-hits-to-rare; an
# UNDERCOUNT before). The lines that ramp with "each second they've ever been in your Presence" (Attrition's gem stat
# skill_attrition_hit_damage_+%_final_vs_rare_or_unique_enemy_per_second_ever_in_presence_up_to_max, 1% more per 2 s up
# to 40-59% by level; its Culling Strike from 40-31 s, act_str.lua 1254-1262; "Deal 4% increased Damage with Hits to Rare
# or Unique Enemies for each second ...", ModParser 6204; the "more damage with hits and ailments ... for each second /
# every N seconds" lines, ModParser 3109-3112) all read PoB's enemy multiplier EnemyPresenceSeconds, set only by the
# config box enemyMultiplierEnemyPresenceSeconds (ConfigOptions 1666), which nothing set: so none of them counted.
# Now: the boss in your Presence from the fight's start (the build's distance vs PresenceRadius; 0 outside it), PoB's
# Full DPS sampled at PRESENCE_SAMPLES seconds (linear between, flat past the last), the fight's length T solved from
# the integral of that DPS over the boss's Life + ES (kill time: the ramp shortens the fight that feeds it), and the box
# set to the seconds whose DPS equals the fight's average (pool / T; floored, as PoB floors a Multiplier). MEDIUM: the
# boss taken to stay in your Presence the whole fight; phases not read. Attrition's Culling Strike then counts only
# when that average reaches its threshold, though the real Cull comes at the end of a fight longer than it (an
# undercount between the two, said in the basis).
PRESENCE_SECONDS_VAR = "enemyMultiplierEnemyPresenceSeconds"
PRESENCE_SECONDS_LINE = re.compile(r"ever been in your presence", re.I)
PRESENCE_SECONDS_GEMS = ("Attrition",)
PRESENCE_SAMPLES = (0, 20, 40, 60, 90, 120, 180)


def presence_fight(samples: list[tuple[float, float]], pool: float) -> tuple[float, float]:
    """(fight length T, the seconds whose DPS is the fight's average): samples, (seconds in Presence, DPS there) by
    seconds, DPS linear between them and flat past the last; T solves the integral of DPS from 0 to T = pool."""
    if not samples or pool <= 0 or samples[0][1] <= 0:
        return 0.0, 0.0
    done, fight = 0.0, None
    for (s0, d0), (s1, d1) in zip(samples, samples[1:]):
        area = (d0 + d1) / 2 * (s1 - s0)
        if done + area >= pool:
            need, slope = pool - done, (d1 - d0) / (s1 - s0)
            x = need / d0 if abs(slope) < 1e-12 else (-d0 + math.sqrt(d0 * d0 + 2 * slope * need)) / slope
            fight = s0 + x
            break
        done += area
    if fight is None:
        fight = samples[-1][0] + (pool - done) / samples[-1][1]
    average = pool / fight
    for (s0, d0), (s1, d1) in zip(samples, samples[1:]):
        if d1 >= average:
            return fight, s0 + ((average - d0) / (d1 - d0) * (s1 - s0) if d1 > d0 else 0.0)
    return fight, samples[-1][0]


def _ailment_up(ailment: str):
    return lambda s: ailment_uptime(s, ailment) >= MIN_UPTIME


def _ailment_share(ailment: str):
    return lambda s: ailment_uptime(s, ailment)


def shared(up: float) -> str:
    """How an uptime is counted, for a basis."""
    if up >= FULL_UPTIME:
        return "counted as always on"
    return "counted at that share" if up >= MIN_UPTIME else "off"


# "if you've Frozen / Chilled / Ignited / Shocked an Enemy Recently" (MECH76; PATCH_CHECK MC-6, an UNDERCOUNT: nothing set
# PoB's conditionFrozenEnemyRecently / ChilledEnemyRecently / IgnitedEnemyRecently / ShockedEnemyRecently boxes,
# ConfigOptions 1241-1256, so every such line counted 0: Ice Bite, the Echoing Frost / Flames / Thunder notables, "60%
# increased Damage if you've Frozen an Enemy Recently", ...). "Recently" is the past 4 seconds (RECENTLY). The share of
# the fight with at least one application in the past 4 s, from the per-hit chances the boss's ailment shares use: the
# main skill's hits x its IgniteChancePerHit / ShockChancePerHit / Chill chance (_chill_per_hit), the other groups'
# Ignites (stats["_ailment_others"]), an aura that keeps Igniting it (stats["_ailment_ground"]: Radiant Grief),
# applications independent: 1 - prod (1 - c)^(hits x 4 s). Freeze: the boss's Freezes a second (models.buildup_rate),
# 1 - e^(-4 r). Consumption doesn't matter (the event is the application). Only the boss is counted (adds aren't
# modelled: an UNDERCOUNT in a fight with adds).
APPLIED_RECENTLY = {"conditionFrozenEnemyRecently": "Freeze", "conditionChilledEnemyRecently": "Chill",
                    "conditionIgnitedEnemyRecently": "Ignite", "conditionShockedEnemyRecently": "Shock"}
PAST_TENSE = {"Freeze": "Froze", "Chill": "Chilled", "Ignite": "Ignited", "Shock": "Shocked"}
APPLIED_RECENTLY_FIELDS = tuple(dict.fromkeys(("IgniteChancePerHit", "ShockChancePerHit", *CHILL_FIELDS,
                                               *models.IMMOBILISE_FIELDS)))


def applied_recently(stats: dict, ailment: str) -> tuple[float, str]:
    """(share of the fight you applied `ailment` to the boss in the past 4 s, how); see the note above."""
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    if ailment == "Freeze":
        rate = models.buildup_rate(stats, "Freeze")
        share = 1.0 - math.exp(-rate * RECENTLY) if rate > 0 else 0.0
        return share, f"the boss Frozen {rate:.3f}/s by the main skill's buildup"
    chance = _chill_per_hit(stats) if ailment == "Chill" else min(number(stats.get(f"{ailment}ChancePerHit")) / 100, 1.0)
    absent = (1.0 - chance) ** (hits * RECENTLY) if chance > 0 and hits > 0 else 1.0
    parts = [f"the main skill's {hits:.2f} hits/s x {chance:.0%}"] if chance > 0 and hits > 0 else []
    for name, h, c, _ in (stats.get("_ailment_others") or {}).get(ailment) or ():
        if h > 0 and c > 0:
            absent *= (1.0 - min(c, 1.0)) ** (h * RECENTLY)
            parts.append(f"{name}'s {h:.2f} hits/s x {c:.0%}")
    ground = min(1.0, max(0.0, number(((stats.get("_ailment_ground") or {}).get(ailment) or (0.0,))[0])))
    if ground > 0:
        absent *= 1.0 - ground
        parts.append(f"an aura keeping it on the boss {ground:.0%} of the fight")
    return 1.0 - absent, "; ".join(parts)


def _applied_recently(var: str):
    ailment = APPLIED_RECENTLY[var]
    verb = PAST_TENSE[ailment]

    def value(lines: list[str], stats: dict) -> tuple[object, str]:
        share, how = applied_recently(stats, ailment)
        if share <= 0:
            return 0.0, ""
        return _box(share, f"You {verb.lower()} the boss in the past {RECENTLY:g} s {share:.0%} of the fight ({shared(share)}): "
                           f"{how} (MECH76: each application taken as independent)")
    return value


# Clash (MECH76; PATCH_CHECK MA-13): "Supported Skills deal more Damage while you have a lower percentage of your Life
# remaining than the target struck, but less Damage when the reverse is true" (SupportClashPlayer:
# support_melee_damage_+%_final_vs_higher_percent_life_target 40, ..._vs_lower_percent_life_target -20). PoB's box
# (clashCondition, Condition:EnemyHigherLifePercent) was never set, so it counted the 20% less all fight. Over the fight
# the boss's Life % runs from 100% to 0% (evenly at a steady DPS), so it is above yours for 1 - (your Life %) of the
# fight. Your Life % taken as your unreserved share of Life (PoB's LifeUnreserved / Life: a Low Life build with Life
# reserved fights there; a full-Life build gets ~0, PoB's reading). Labelled: the boss's hits lower your Life now and
# then (an UNDERCOUNT of the 40% more), and the boss's own Low Life weighting isn't applied here (MEDIUM). Only with the
# Clash gem in the build.
CLASH = "Clash"


def _clash(lines: list[str], stats: dict) -> tuple[object, str]:
    if CLASH not in (stats.get("_gem_names") or set()):
        return 0.0, ""
    life = number(stats.get("Life"))
    mine = min(max(number(stats.get("LifeUnreserved")) / life, 0.0), 1.0) if life > 0 else 1.0
    share = 1.0 - mine
    return _box(share, f"the boss's Life % above yours {share:.0%} of the fight ({shared(share)}): you fight at "
                       f"{mine:.0%} of your Life (unreserved), the boss's Life % runs from 100% to 0% (MEDIUM)")


# Arc's Lightning Infusion (PATCH_CHECK MA-1; MECH77): "Consumes a Lightning Infusion if possible to deal more damage and
# Chain further" (ArcPlayer: arc_damage_+%_final_from_infusion_consumption 200, arc_chains_gained_from_infusion_consumption
# 6; game 26476662, GrantedEffectStatSets; patch 0.3.0 added it, 0.5.0 fixed Arc getting it without spending one).
# PoB gates both on its "Lightning Infused?" box (arcLightningInfused, Condition:ArcLightningInfused), which nothing set,
# so a fed Arc counted 0 of its 200% more: an UNDERCOUNT. The box now carries the share of Arc's casts the build's own
# Lightning Infusions cover (Conditions._arc_infused, stats["_arc_infused"]); 0 with no source. The extra chains don't
# matter against a lone boss.
ARC = "Arc"


def _arc_infused(lines: list[str], stats: dict) -> tuple[object, str]:
    if ARC not in (stats.get("_gem_names") or set()):
        return 0.0, ""
    share, how = stats.get("_arc_infused") or (0.0, "the build's Infusions weren't read")
    return _box(share, f"{ARC}'s casts consuming a Lightning Infusion {share:.0%} ({how})")


# Drain Ailments (PATCH_CHECK MA-10; MECH77): "Supported Skills Consume all Ailments of a random type on the target when
# inflicting damage over time Debuffs, causing the Debuff to deal 35% more damage if any Ailments were consumed"
# (gem_stat_descriptions, support_drained_ailment_damage_over_time_+%_final_if_ailment_consumed 35, game 26476662; patch
# 0.2.0f: it now grants the 1 s immunity and consumes every copy). PoB gates the 35% on its "Consumed an Ailment?" box
# (conditionAilmentConsumed), which nothing set: an UNDERCOUNT for a fed Drain build. The box now carries the share of
# the main skill's casts that find an Ailment it consumes (Freeze, Shock, Ignite, Bleeding, Poison: the support's added
# SkillConsumes types, PoB's and the game's GrantedEffects AddedActiveSkillTypes): 1 - prod(1 - each one's share of
# casts finding it). Each consumed ailment's own share is consumed_share (it ends at the next cast, then 1 s immune, then
# waits for the build's next application of it), and main_consumes now lists them for Drain Ailments, so the ailments'
# own boxes count the loss (before, the gem's text matched no CONSUME_TEXT word: they counted as never consumed, an
# OVERCOUNT). Freeze: the main skill's Freezes a second over its casts. Labelled: one random type a cast is taken as all
# of them (each ailment's share an UNDERCOUNT where several are up), the types taken as independent (MEDIUM); Drain on a
# skill beside the main one: its 35% not counted (an UNDERCOUNT; its consumption is, through side_consumes' types).
DRAIN_AILMENTS = "Drain Ailments"
DRAIN_CONSUMES = ("Freeze", "Shock", "Ignite", "Bleed", "Poison")


def drain_share(stats: dict) -> tuple[float, str]:
    """(share of the main skill's casts that consume an Ailment, how), for a main skill supported by Drain Ailments."""
    consumes = stats.get("_consumes") or {}
    if consumes.get("Poison") != DRAIN_AILMENTS:
        return 0.0, f"{DRAIN_AILMENTS} doesn't support the main skill"
    casts = number(stats.get("Speed"))
    missing, parts = 1.0, []
    for ailment in DRAIN_CONSUMES:
        if ailment == "Freeze":
            rate = models.buildup_rate(stats, "Freeze")
            share = min(1.0, rate / casts) if casts > 0 and rate > 0 else 0.0
        elif ailment == "Shock":
            share = shock_share(stats)
        else:
            share = ailment_uptime(stats, ailment)
        if share > 0:
            missing *= 1.0 - min(share, 1.0)
            parts.append(f"{ailment} {share:.0%}")
    found = 1.0 - missing
    return found, (f"casts finding an Ailment {found:.0%} ({', '.join(parts)}; each consumed at the next cast, then 1 s "
                   f"immune; MEDIUM)" if parts else "the build applies no Ailment it consumes: 0")


def _drain_ailments(lines: list[str], stats: dict) -> tuple[object, str]:
    if DRAIN_AILMENTS not in (stats.get("_gem_names") or set()):
        return 0.0, ""
    share, how = drain_share(stats)
    return _box(share, f"{DRAIN_AILMENTS}: {how}")


# Skills that gain damage as an element from an Elemental Ground in their path (PATCH_CHECK MA-2 / MA-6 / MA-7; MECH78).
# PoB gates each gain on a box nothing set, so every such skill counted 0 of it (an UNDERCOUNT):
#   - Eye of Winter (eyeOfWinterCold / Fire / Lightning): "Gains X% of damage as damage of the corresponding Type when
#     passing over an Elemental Ground Effect or Orb" (eye_of_winter_new.csd, eye_of_winter_damage_%_to_gain_as_element_
#     per_ground_effect_type); every type it passes counts (0.2.0 bug fix: "rather than [only] the most recent one"), and
#     Orbs count (0.2.0e: "other elemental orbs like Solar Orb, Orb of Storms or Frost Bomb").
#   - Twister (twisterCold / ...): "Elemental twisters Gain 50% of damage as damage of the corresponding Type", from
#     "Passing over Elemental Ground Surfaces or Consuming an elemental Whirlwind" (twister.csd, ActiveSkills twister).
#   - Whirling Slash / Whirlwind Lance: "A Whirlwind that overlaps an allied Elemental Ground Surface takes on that
#     element, gaining 50% of damage as the corresponding type and applying the Ground Surface's debuff to enemies inside
#     the Whirlwind for 8 seconds" (KeywordPopups Whirlwind; 0.4.0: 8 s, was 4). PoB's own box (whirlwindBuffCold etc.)
#     also sets the boss Chilled / Ignited / Shocked outright, which would hide the ailment rows' real shares (an
#     OVERCOUNT), so the tool sets only the gain's PoB condition (bridge: "bridgeCondition<Name>" config inputs).
# Elemental Ground = Shocked, Chilled and Ignited Ground (KeywordPopups ElementalGround). Each element's share is the
# share of the fight the build's own ground of it is at the boss (conditions.shocked_ground / chilled_ground: the
# ground made at the boss, so a skill aimed at the boss passes over it; MEDIUM). Ignited Ground: conditions.ignited_ground
# (MECH79; before, Fire came only from an Orb or a ring line). Eye of
# Winter's Orbs: each enabled Orb skill beside it (skill type Orb, by its element types; Hydrosphere's two taken as both,
# UNVERIFIED) at min(1, its uses/s x its duration) (the play: keep one up and fire the Eye through it; MEDIUM). Flame
# Wall's added Fire is MECH-B's (MA-12). Wind skills (Twister, the Whirlwinds; ActiveSkillType Wind, 0.4.0) also count
# as boosted by an element from Berek's Grip / Pass / Respite and The Taming ("Wind Skills which can be boosted by
# Elemental Ground Surfaces count as being boosted by Shocked Ground"; PoB parses none of them), and take ONE element
# at a time unless "can be boosted by multiple Elemental Ground Surfaces" (The Taming;
# wind_skills_can_be_empowered_by_multiple_elements: checked, Stats ids holding wind_skill and their Mods / PassiveSkills
# / GrantedEffectStatSets users: only The Taming's two Mods carry it; poe2db not checked). So a wind skill without it
# gets one box, its most-held element, at the share any element is held (1 - prod(1 - each)); the gain is the same 50%
# whichever element, so only the element's own scaling is approximate (MEDIUM). Not counted (UNDERCOUNTS, labelled): a
# Whirlwind keeping its element 8 s after it leaves the ground, and the ground's debuff it puts on the boss; Twister's
# elemental Whirlwinds count through the same ground (they take it from the same patch); grounds from skills the tool
# doesn't model.
EYE_OF_WINTER = "Eye of Winter"
TWISTER = "Twister"
WHIRLWINDS = ("Whirling Slash", "Whirlwind Lance")
GROUND_ELEMENTS = ("Cold", "Fire", "Lightning")
ELEMENT_GAIN_VARS = {EYE_OF_WINTER: "eyeOfWinter", TWISTER: "twister",
                     **dict.fromkeys(WHIRLWINDS, "bridgeConditionWhirlwindBuff")}
WIND_GROUND_BOOST = re.compile(r"Wind Skills which can be boosted by Elemental Ground Surfaces count\s+as being boosted "
                               r"by ((?:(?:Ignited|Shocked|Chilled)(?:,\s*|,?\s+and\s+)?)+) Ground", re.I)
WIND_MULTIPLE = re.compile(r"Wind Skills which can be boosted by Elemental Ground Surfaces can be boosted by multiple "
                           r"Elemental Ground Surfaces", re.I)
GROUND_OF = {"Shocked": "Lightning", "Chilled": "Cold", "Ignited": "Fire"}


def element_gain_shares(skill: str, stats: dict, texts: list[str], orbs: dict[str, tuple[float, str]] | None = None
                        ) -> dict[str, tuple[float, str]]:
    """{element: (share of the fight `skill` holds its gain of that element, how)} (see the note above). `orbs`: Eye of
    Winter's Orbs by element ({element: (up, how)}); a wind skill without the multiple-element line gets one element."""
    joined = " ".join(t.strip() for t in texts)
    ringed = {GROUND_OF[g] for m in WIND_GROUND_BOOST.finditer(joined)
              for g in re.findall(r"Ignited|Shocked|Chilled", m.group(1), re.I)} if skill != EYE_OF_WINTER else set()
    grounds = {"Lightning": stats.get("_shocked_ground") or (0.0, ""), "Cold": stats.get("_chilled_ground") or (0.0, ""),
               "Fire": stats.get("_ignited_ground") or (0.0, "")}  # (MECH79: conditions.ignited_ground)
    out: dict[str, tuple[float, str]] = {}
    for element in GROUND_ELEMENTS:
        ground, ground_how = grounds[element]
        parts = [f"its {element} ground at the boss {ground:.0%}" + (f" ({ground_how})" if ground_how else "")]
        off = 1.0 - min(1.0, max(0.0, ground))
        if element in ringed:
            off, parts = 0.0, parts + ["an item makes wind skills count as boosted by it: always"]
        if orbs and element in orbs:
            up, how = orbs[element]
            off *= 1.0 - min(1.0, max(0.0, up))
            parts.append(f"{how}: {up:.0%}")
        out[element] = (1.0 - off, "; ".join(parts))
    if skill != EYE_OF_WINTER and not WIND_MULTIPLE.search(joined):
        held = 1.0
        for share, _ in out.values():
            held *= 1.0 - share
        best = max(GROUND_ELEMENTS, key=lambda e: out[e][0])
        if out[best][0] > 0:
            out = {e: ((1.0 - held, out[e][1] + f"; a wind skill holds one element at a time: {best} counted at the "
                        f"share any element is held, {1.0 - held:.0%}") if e == best
                       else (0.0, out[e][1] + f"; one element at a time: counted as {best}")) for e in GROUND_ELEMENTS}
    return out


# The Whirlwind's ground debuff on the boss (MECH80; the manager 21:16, RESEARCH26 R26-11 (c), MEDIUM-LOW, an UNDERCOUNT
# before: the note above left "the ground's debuff it puts on the boss" uncounted). KeywordPopups Whirlwind: a Whirlwind
# that takes on a ground's element applies "the Ground Surface's debuff to enemies inside the Whirlwind for 8 seconds";
# Berek's Grip "Wind Skills which can be boosted by Elemental Ground Surfaces count as being boosted by Shocked Ground".
# The game ties the gain and the debuff in one rule, so a Whirlwind the ring boosts takes both (RESEARCH26's reading; no
# source says outright that "count as" gives the debuff: the cautious reading is the gain only, the play guide's "without
# it" row). Counted: Shocked Ground's Shock (ground_shock_magnitude) on the boss for the share of the fight the main
# Whirlwind skill holds Lightning from the ring, the Whirlwind taken as kept live at the boss (the main skill used all
# fight, MEDIUM-LOW). Its own ground's share is already Shocked Ground's (shock_share): only the ring's part is added.
# Not counted (labelled): Berek's Pass (Chilled Ground's Chill: not modelled on the boss from any ground yet) and Berek's
# Respite (Ignited Ground: would need no_hit_ignite's 50% Flammability); Twister (it consumes Whirlwinds; not the rule).
def whirlwind_shock(skill: str, texts: list[str], gains: dict | None) -> tuple[float, str]:
    """(share of the fight the boss carries Shocked Ground's Shock from the main skill's ring-boosted Whirlwind, how)."""
    if skill not in WHIRLWINDS:
        return 0.0, ""
    joined = " ".join(t.strip() for t in texts)
    ringed = {GROUND_OF[g] for m in WIND_GROUND_BOOST.finditer(joined)
              for g in re.findall(r"Ignited|Shocked|Chilled", m.group(1), re.I)}
    if "Lightning" not in ringed:
        return 0.0, ""
    share, _ = (gains or {}).get("Lightning") or (0.0, "")
    return share, (f"{skill}'s Whirlwind counts as boosted by Shocked Ground (an item line) {share:.0%} of the fight and "
                   "puts Shocked Ground's Shock on the boss inside it for 8 s (KeywordPopups Whirlwind; RESEARCH26 "
                   "R26-11, MEDIUM-LOW: the Whirlwind kept live at the boss; without it, the gain only)")


def _element_gain(var: str, element: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def value(lines: list[str], stats: dict) -> tuple[object, str]:
        skill = stats.get("_main_skill") or ""
        if ELEMENT_GAIN_VARS.get(skill) != var:
            return 0.0, ""
        share, how = (stats.get("_element_gain") or {}).get(element) or (0.0, "not read")
        return _box(share, f"{skill} gaining {element} {share:.0%} of the fight ({how})")
    return value


# Enemies in Close Range (PATCH_CHECK MB-16; MECH78): Viciousness's "3% increased Attack Speed per Enemy in Close Range"
# (attack_speed_+%_per_enemy_in_close_range) is PoB's AtCloseRange x Multiplier:NearbyEnemies (ModParser "per enemy in
# close range"), and nothing set the count, so it counted 0. The fight is one boss: 1 (with AtCloseRange, which the tool
# sets for a melee main skill: KeywordPopups CloseRange, "within 2 metres ... Normally, Melee Attacks you make are in
# Close Range"). Adds near you aren't counted (an UNDERCOUNT for presets with adds). Other readers of the count: none in
# the current game (checked: Stats ids holding nearby_enem / close_range + enem, 93, and their Mods / PassiveSkills /
# GrantedEffectStatSets users: only Viciousness counts enemies (Predator's Mark's per_nearby_enemy stat is PoB's
# MinionPresenceCount); PoB ModCache, uniques and the 0.5 tree: Viciousness's line only; poe2db not checked).
NEARBY_ENEMY_LINE = re.compile(r"\bper (?:nearby )?enemy in close range\b|\b(?:per|for each) nearby enemy\b", re.I)


def _nearby_enemies(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(NEARBY_ENEMY_LINE.search(line) for line in lines):
        return 0, ""
    return 1, "the fight is one boss, in Close Range for a melee skill (adds near you not counted)"


# Poisons inflicted Recently (PATCH_CHECK MB-12; MECH78): "10% increased Poison Duration for each Poison you have inflicted
# Recently, up to a maximum of 100%" (Escalating Toxins) and other "for each Poison you have inflicted recently" lines are
# PoB's Multiplier:PoisonAppliedRecently, which nothing set, so they counted 0 (an UNDERCOUNT). The count is the main
# skill's Poisons in the past 4 s: its hits/s x Poison chance (capped at one a hit) x 4. Other skills' Poisons aren't read
# (the bridge's ailment rows carry no Poison: an UNDERCOUNT where another skill poisons).
POISONS_RECENTLY = re.compile(r"for each Poison you have inflicted Recently", re.I)


def _poisons_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(POISONS_RECENTLY.search(line) for line in lines):
        return 0, ""
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    chance = min(number(stats.get("PoisonChancePerHit")) / 100.0, 1.0)
    count = hits * chance * RECENTLY
    return count, (f"the main skill's {hits:.2f} hits/s x {chance:.0%} Poison chance x {RECENTLY:g} s = {count:.1f} "
                   "Poisons (other skills' Poisons not read)")


# Open Weakness (PATCH_CHECK MC-1; MECH78): KeywordPopups RevealWeakness: "Revealed Weaknesses ... occupy a total of 45%
# of their maximum Life (but in random segments). While the enemy's current Life is within a highlighted segment, they
# are considered to have an Open Weakness." Sources (checked: Stats ids holding "weakness" or "bloodlust" and their
# Mods / PassiveSkills / GrantedEffectStatSets users, game 26476662): the Amazon's Predatory Instinct
# (bloodlust_reveal_weakness) and the unique Mod UniqueRevealWeakness1 (unique_reveal_weakness), both "Reveal Weaknesses
# against Rare and Unique enemies" (stat_descriptions.csd). Readers: Predatory Instinct's "50% more damage against
# enemies with an Open Weakness" (PoB: Condition EnemyHasOpenWeakness) and In for the Kill's "40% increased Skill Speed
# / 20% increased Movement Speed while an enemy with an Open Weakness is in your Presence" (OpenWeaknessEnemyPresence);
# the Soul Eater / recoup uniques' lines PoB doesn't parse. Nothing set either box: they counted 0 (an UNDERCOUNT).
# The boss carries it while its Life is in the 45%, and the build kills faster there, so its share of the TIME is
# 0.45 r / (0.45 r + 0.55), r = PoB's Full DPS with both boxes off over on (Conditions._open_weakness, probed): weighed
# by weigh_uptimes, the headline is the fight's real average, Life / (0.45 Life / on + 0.55 Life / off). The Presence
# box also needs the boss in your Presence (the build's distance vs its Presence radius). Labelled: the two boxes
# weighed as independent (a ~2% undercount at most); a Culling Strike ending the fight inside or outside a segment
# isn't modelled (MEDIUM).
# One share for every reader (the manager, 12:43): COV33 (train 58) counts The Master's Reach's recoup line and
# Predatory Instinct's 50% more as ledger `share:OpenWeakness` stand-ins (its pob_wrong entry drops PoB's parse of the
# 50% line, so this box no longer moves that line: no line counts twice). Those stand-ins read the share through
# open_weakness_up(build) once COV-A switches them (COV34), and the probe puts the stand-in's MORE back into the DPS
# inside (Conditions._open_weakness), so the share stays the time share either way.
REVEAL_WEAKNESS =re.compile(r"^Reveal Weaknesses against Rare and Unique enemies$", re.I)
OPEN_WEAKNESS_LIFE = 0.45  # (KeywordPopups RevealWeakness)
OPEN_WEAKNESS_VARS = ("conditionEnemyHasOpenWeakness", "conditionOpenWeaknessEnemyPresence")


def open_weakness_share(ratio: float) -> float:
    """Share of the fight's time the boss has an Open Weakness, `ratio` = DPS without it / with it."""
    ratio = min(max(ratio, 0.0), 1.0)
    inside = OPEN_WEAKNESS_LIFE * ratio
    return inside / (inside + 1.0 - OPEN_WEAKNESS_LIFE) if inside > 0 else 0.0


OPEN_WEAKNESS_MORE = re.compile(r"^(\d+(?:\.\d+)?)% more damage against enemies with an Open Weakness$", re.I)


def open_weakness_up(build) -> tuple[float, str]:
    """(share of the fight's time the boss has an Open Weakness, how) for other readers (COV-A's OpenWeakness ledger
    condition: its `share:` stand-ins read this, the manager 12:43): Conditions.evidence's probe (_open_weakness), set
    on the build each conditions round; (0.0, "") before the first round or with nothing revealing weaknesses."""
    share, how = getattr(build, "open_weakness_up", None) or (0.0, "")
    return min(max(number(share), 0.0), 1.0), how or "nothing reveals the boss's weaknesses: 0"


def _open_weakness(presence: bool) -> Callable[[list[str], dict], tuple[object, str]]:
    def value(lines: list[str], stats: dict) -> tuple[object, str]:
        found = stats.get("_open_weakness")
        if not found:
            return 0.0, ""
        share, how = found
        if presence:
            metres = _boss_distance(lines, stats)[0] / 10
            radius = number(stats.get("PresenceRadius")) / 10
            if radius < metres:
                return 0.0, f"the boss {metres:g} m away, outside your {radius:.1f} m Presence: 0"
        return _box(share, f"the boss with an Open Weakness {share:.0%} of the fight ({how})")
    return value


# Curse Duration expired (PATCH_CHECK MD-8; MECH78): "Your Curses have 20% increased Magnitudes if 50% of Curse Duration
# expired" (the Impending Doom passive; PoB: MultiplierThreshold CurseExpired >= 50 on the enemy, ModParser "if (%d+)%
# of curse duration expired") read PoB's multiplierCurseExpiredOnEnemy, which nothing set: 0. Now the threshold at the
# share of the fight a curse of the build is on the boss AND past it, from models.curse_uses: a curse cast by hand
# (recast as it ends, or let run out: CURSE_LAPSE) runs its whole duration each time, so it is past t% for (1 - t) of
# the time it's on; a curse Hexblast detonates is removed at half its duration (models.HEXBLAST_CURSE_NOTE): past 50%
# for none of it; a curse applied otherwise (on hit, by a trigger, a cursed zone, "forever") is taken as refreshed
# before it gets there: 0 (UNVERIFIED; an UNDERCOUNT if it isn't). Several curses: the one past it longest (PoB's one
# multiplier for the boss; MEDIUM).
CURSE_EXPIRED = re.compile(r"if (\d+)% of Curse Duration expired", re.I)


def _curse_expired(lines: list[str], stats: dict) -> tuple[object, str]:
    thresholds = [int(m.group(1)) for line in lines if (m := CURSE_EXPIRED.search(line))]
    if not thresholds:
        return 0, ""
    t = min(thresholds)
    best, how = 0.0, "no curse of the build cast by hand runs that long: 0"
    for use in stats.get("_curse_uses") or ():
        if use.hexblast or not use.by_hand:
            continue
        share = use.share * (1.0 - t / 100.0)
        if share > best:
            best, how = share, (f"{use.name} on the boss {use.share:.0%} of the fight, cast by hand for its whole "
                                f"{use.duration:g} s, so past {t}% of it for {1 - t / 100:.0%} of that")
    return (t if best >= MIN_UPTIME else 0), how, best


def _box(up: float, basis: str) -> tuple[bool, str, float]:
    """(box on, basis, uptime) for an effect the build keeps up at a rate: on from MIN_UPTIME, weighed by its uptime."""
    return up >= MIN_UPTIME, basis, up


def _cursed(stats: dict) -> tuple[object, str]:
    """The Cursed box at the share of the fight the boss carries one of your curses (models.curse_uses; PoB sets the
    enemy Cursed with any curse or Mark anyway, so models.curse_model scales what leans on it)."""
    uses = stats.get("_curse_uses") or []
    if not uses:
        return 0.0, ""
    share, how = models.curse_share(uses)
    return _box(share, f"Cursed {share:.0%} of the fight ({shared(share)}): {how}")


# Shocked Ground (MECH43, FIX_QUEUE; before, nothing in builder/ read it and PoB's OnShockedGround was never set). Game
# definition (KeywordPopups ShockedGround): "Shocked Ground Shocks those standing in it, and lasts 6 seconds by default".
# Player sources in the game files (checked: every stat whose id or description holds shocked / shock / shocking ground,
# and their users in GrantedEffectStatSets, Mods and PassiveSkills, scratch/mech43/sg_scan.py; PoB's ModParser and the
# gem descriptions agree): the Shocking Leap support (support_shocking_leap_shocked_ground_duration_ms, on the first
# Critical Hit of a use), Invoker's "I am the Thunder..." (chance_%_to_create_shocking_ground_on_shock 25), Wake of
# Destruction ("Drop Shocked Ground while moving, lasting 8 seconds"), the main skill's own ground (Storm Lance, Lightning
# Warp, Vivid Stampede, Gathering Storm: *_shocked_ground_effect_duration_ms where they carry one), and Charged Mark
# (TriggeredChargedMarkPlayer, a chance on Mark activation).
SHOCKED_GROUND_DURATION = 6.0  # s, the definition's default
SHOCKED_GROUND_RADIUS = 2.0  # m, for "you on it" (UNVERIFIED: the definition gives no radius; Ignited Ground's is 2 m)
# Each source's own radius (MECH58; research/shocked-ground-sources.md, game files): Lightning Warp 3.0 m
# (active_skill_base_area_of_effect_radius 30, "Explosion and Shocked Ground radius are {0} metres"), Gathering Storm
# 3.2 m (GatheringStormExplodeBetterPlayer 32, "Shocked Ground radius is {0} metres"), Shocking Leap 2.0 m
# (support_shocking_leap_shocked_ground_radius 20); Storm Lance 3.0 m (its empowered set's tertiary radius 30, no text:
# LOW) and Vivid Stampede 2.0 m (its shockwave's: LOW). The rest (Invoker's, the trail) keep SHOCKED_GROUND_RADIUS,
# UNVERIFIED. Area of Effect modifiers aren't read (a smaller reach than the truth for "you on it": an undercount).
SHOCKED_GROUND_RADII = {"Lightning Warp": 3.0, "Gathering Storm": 3.2, "Shocking Leap": 2.0, "Storm Lance": 3.0,
                        "Vivid Stampede": 2.0}
# When each skill makes its ground (MECH58; research/shocked-ground-sources.md, FIX_QUEUE RESEARCH1, an OVERCOUNT: before,
# every use made it): Lightning Warp only when it explodes, which needs the target under the Cull threshold (HIGH: "If
# targeting an enemy, the explosion also creates Shocked Ground"); Storm Lance only from a lance that consumed a Frenzy
# Charge (MEDIUM-HIGH: "Consumes a Frenzy Charge if possible to ... create Shocked Ground on Detonation", its duration
# only in the Frenzy-empowered stat set); Gathering Storm only from a Perfectly Timed dash that explodes a Tempest Bell
# (HIGH). Vivid Stampede: at the end of a Stag's full path, not necessarily under the boss (counted per use, LOW).
SHOCKED_GROUND_GATED = {"Lightning Warp": "cull", "Storm Lance": "frenzy", "Gathering Storm": "bell"}
SHOCKED_GROUND_SKILL_STATS = ("secondary_skill_shocked_ground_effect_duration_ms", "skill_shocked_ground_effect_duration_ms",
                              "lightning_warp_shocked_ground_base_duration_ms")
# Each skill's ground duration (MECH68): the old three stats above are in none of PoB's skill data (checked: every
# Data/Skills/*.lua, only Shocking Leap's support_shocking_leap_shocked_ground_duration_ms and Thunderous Leap's chance
# hold "shocked_ground"), so the four skills' grounds read 0 (an UNDERCOUNT). The game's own skill descriptions name the
# stat: lightning_warp.csd and storm_lance/statset_1.csd "base_secondary_skill_effect_duration: [ShockedGround] duration
# is {0} second" (8 s each), vivid_stampede.csd "base_skill_effect_duration: [ShockedGround] duration is {0} second"
# (4 s, and its computed table value skill_shocked_ground_effect_duration_ms); Gathering Storm: the Tempest Shockwave
# set's base_skill_effect_duration 5 s, its only duration (MEDIUM: no description line names it the ground). These are
# skill effect durations, so the build's duration modifiers lengthen the ground (MEDIUM: the stat names and the computed
# table value; the game's formula isn't in the files): PoB's DurationMod (Duration, PrimaryDuration) for the main one,
# DurationSecondary over its base for the secondary one where the main stat set has it (Lightning Warp), else DurationMod
# (Storm Lance's ground is in its Frenzy-Charged set: SecondaryDuration-only lines missed there, a small undercount).
# Persistent Ground I-III (AUDIT30 A30-3 / AUDIT31 A31-6, an UNDERCOUNT): ground_effect_duration_+% 50 ("50% increased
# duration of Ground Surfaces"), a support stat PoB's DurationMod never holds: read from the main skill's supports and
# added to the factor (ground_ms; Shocking Leap's ground x (1 + it), the supported skill's surface). No Mods row or
# passive uses ground_effect_duration_+% (MECH67's scan of Mods / PassiveSkills; the supports carry it in their stat sets).
# support_shocked_ground_effect_duration_+%_final: no user (checked: Mods, PassiveSkills, GrantedEffectStatSets,
# GrantedEffectStatSetsPerLevel, GrantedEffectQualityStats, SoulCoreStats, game 26476662; nothing to read). Whether
# duration modifiers lengthen Shocking Leap's support_shocking_leap_shocked_ground_duration_ms (a virtual_ copy exists)
# is UNVERIFIED: left at its base.
GROUND_SURFACE_DURATION_STAT = "ground_effect_duration_+%"
SHOCKED_GROUND_DURATION_STAT = {"Lightning Warp": "base_secondary_skill_effect_duration",
                                "Storm Lance": "base_secondary_skill_effect_duration",
                                "Vivid Stampede": "base_skill_effect_duration",
                                "Gathering Storm": "base_skill_effect_duration"}
SHOCKED_GROUND_SKILLS = ("Storm Lance", "Lightning Warp", "Vivid Stampede", "Gathering Storm")
SHOCKED_GROUND_SUPPORT = "Shocking Leap"
SHOCKED_GROUND_SUPPORT_STATS = ("support_shocking_leap_shocked_ground_duration_ms",)
SHOCK_MAKES_GROUND = re.compile(r"(\d+)% chance on shocking enemies to creat\w* shocked ground", re.I)
GROUND_WHILE_MOVING = re.compile(r"drops? shocked ground while moving, lasting (\d+) seconds", re.I)
ON_SHOCKED_GROUND = re.compile(r"\bon shocked ground\b", re.I)
CHARGED_MARK = "Charged Mark"
SKELETAL_BRUTE = "Skeletal Brute"  # (MECH76: its minions' Shattering Roar Intimidates, Conditions.shattering_roar)
DEATH_STORM_SUMMON = "Skeletal Storm Mage"  # its minions' Death Storm (DeathStormSkeletonStormMageMinion) makes ground
DEATH_STORM_MELEE = ("Skeletal Warrior", "Skeletal Reaver", "Skeletal Brute")  # (MECH65: Conditions.death_storm)
CHARGED_MARK_SECONDS = 6.0  # TriggeredChargedMarkPlayer base_skill_effect_duration 6000 (research/ground-radii.md, HIGH)


def charged_mark_radius(level: int) -> float:
    """(MECH61) Charged Mark's Shocked Ground radius in metres at the triggered skill's level (TriggeredChargedMarkPlayer
    active_skill_base_area_of_effect_radius per level, research/ground-radii.md, HIGH): 1.4 m at L1-2, +0.1 every 2 levels
    to 2.3 m at L19-20, then +0.1 every 3 levels to 3.0 m at L39-40."""
    level = max(1, min(int(level or 1), 40))
    tenths = 14 + (level - 1) // 2 if level <= 20 else 24 + (level - 21) // 3
    return tenths / 10


SHOCKED_GROUND_MAGNITUDE = 20  # the base Shock (PoB's BaseShockMagnitude); the strongest Shock applies (PoB: the max)


# Chilled Ground under the boss (MECH76, for COV-A's "Enemies standing on Chilled Ground take 25% increased Fire Damage",
# the Delirium anoint Desert's Scorn: enemies_in_chilled_ground_take_+%_fire_damage, which PoB leaves unparsed; nothing
# set PoB's conditionEnemyOnChilledGround before). KeywordPopups ChilledGround: "Chilled Ground Chills those standing in
# it, and lasts 6 seconds by default". Sources counted, each at the boss's Freezes a second (models.buildup_rate, the
# main skill's buildup), the frozen boss taken to stay in the patch for its duration (the boss-still headline, the
# manager's FIX_QUEUE 442 ruling; MEDIUM), combined as independent (_covered):
#   - Frost Nexus on the main skill ("create patches of Chilled Ground around Frozen enemies";
#     chilling_ice_create_chilled_ground_on_freeze_base_duration_ms 8000);
#   - "On Freezing Enemies create Chilled Ground" (Invoker's I am the Blizzard..., create_chilling_ground_on_freeze; no
#     duration in its stats: the 6 s default, MEDIUM);
#   - "X% chance to create Chilled Ground when you Freeze an Enemy" (chilled_ground_on_freeze_%_chance_for_3_seconds:
#     3 s; its Mod SpreadChilledGroundOnFreezeUnique__1 has no carrier in PoB's uniques: UNVERIFIED that one drops).
# Not counted (labelled, an UNDERCOUNT where they reach the boss): skills' own ground (Ice Nova near a Frostbolt, the
# Frost Wolf howl, Ice Crystal supports), the flask's "Creates Chilled Ground on Use" and "when Hit with an Attack"
# (both at your feet: MECH-C's flask rates, the boss's hits), Freezes from other skills. Duration modifiers not read.
# (checked: Stats ids holding chilled_ground / chill + ground and their Mods / PassiveSkills / GrantedEffectStatSets
# users; PoB ModCache's Chilled Ground lines; poe2db not checked: UNVERIFIED there)
CHILLED_GROUND_SECONDS = 6.0  # (KeywordPopups ChilledGround's default)
FROST_NEXUS = "Frost Nexus"
FROST_NEXUS_SECONDS = 8.0  # (chilling_ice_create_chilled_ground_on_freeze_base_duration_ms 8000)
CHILLED_ON_FREEZE = re.compile(r"^On Freezing Enemies create Chilled Ground$", re.I)
CHILLED_CHANCE_ON_FREEZE = re.compile(r"^(\d+)% chance to create Chilled Ground when you Freeze an Enemy$", re.I)
CHILLED_CHANCE_SECONDS = 3.0  # (the stat id: "_for_3_seconds")


def chilled_ground(stats: dict, texts: list[str], main_gems: set[str]) -> tuple[float, str]:
    """(share of the fight the boss stands on your Chilled Ground, how); see the note above. (0.0, "") with no source."""
    sources = []
    if FROST_NEXUS in main_gems:
        sources.append((1.0, FROST_NEXUS_SECONDS, f"{FROST_NEXUS} on the main skill ({FROST_NEXUS_SECONDS:g} s a Freeze)"))
    for text in texts:
        if CHILLED_ON_FREEZE.match(text.strip()):
            sources.append((1.0, CHILLED_GROUND_SECONDS, f"'{text.strip()}' ({CHILLED_GROUND_SECONDS:g} s by default)"))
        elif m := CHILLED_CHANCE_ON_FREEZE.match(text.strip()):
            sources.append((int(m.group(1)) / 100, CHILLED_CHANCE_SECONDS,
                            f"{m.group(1)}% a Freeze ({CHILLED_CHANCE_SECONDS:g} s)"))
    if not sources:
        return 0.0, ""
    freezes = models.buildup_rate(stats, "Freeze")
    off, parts = 1.0, []
    for chance, seconds, how in sources:
        up = _covered(freezes, chance, seconds)
        off *= 1 - up
        parts.append(f"{how}: {up:.0%}")
    share = 1 - off
    return share, (f"the boss Frozen {freezes:.3f}/s by the main skill; " + "; ".join(parts)
                   + f": on Chilled Ground {share:.0%} of the fight (the frozen boss taken to stay in the patch, MEDIUM; "
                   "skills' own ground and other skills' Freezes not counted)")


# Ignited Ground under the boss (MECH79; plans/kw-ignited-ground.md item 1). KeywordPopups IgnitedGround: "repeatedly
# inflicts Flammability on enemies as long as they are standing on it, Igniting them when it reaches 50%. By default,
# Ignited Ground lasts for 4 seconds and has a radius of 2 metres". The player's sources (checked: the 58 Stats ids
# holding ignit/burning + ground and their users in Mods, PassiveSkills, GrantedEffectStatSets and PerLevel, game
# 26476662; the skills' ActiveSkills texts; poe2db not checked):
#   - counted: Incinerate and Ball Lightning, each on the casts that consume a Fire Infusion ("Consumes a Fire Infusion
#     if possible to also create Ignited Ground" / "... create Ignited Ground as it travels"), the build's Fire (and
#     any-element) Infusions shared over its Fire consumers by uses; the ground made at the boss (Incinerate's cone and
#     Ball Lightning's path through it; LOW, as Shocked Ground), lasting IncinerateGroundPlayer's 8 s /
#     BallLightningIgnitedGround's 4 s (base_skill_effect_duration; duration modifiers not read);
#   - counted: Beira's Anguish (UniqueCharmIgniteEnemiesInPresence1, "Creates Ignited Ground for 4 seconds when used")
#     at the charm's uses (flasks.uses_per_second), at your feet: only when you fight within its 2 m (the default);
#   - counted: Birth of Fury (UniqueBurningGroundWhileMovingMaximumLife1, "Drop Ignited Ground while moving, which
#     lasts 8 seconds"): a play style, as Shocked Ground's trail (walk or roll through the boss's spot every 8 s), only
#     when you fight within 2 m and don't stand still;
#   - counted (MECH83): Explosive Spear's Frenzy-Charged explosion (ActiveSkills explosive_spear: "Consumes a Frenzy
#     Charge if you have one to explode immediately, dealing more damage in a cross-shaped area and creating Ignited
#     Ground"; ExplosiveSpearInfusedExplodePlayer base_skill_effect_duration 8000, 26650308), at its Frenzy-Charged
#     throws a second: the Frenzy Charges the build makes shared over its Frenzy consumers by uses (_charges_held's
#     charge flow, MECH-C's), at most the spear's own throws; the ground made at the boss (the spear exploding on its
#     impact, LOW), its own Ignite damage not read (as Incinerate's);
#   - counted (MECH84): Spear of Solaris's skybeam (ActiveSkills spear_of_solaris: "On landing the spear deals heavy
#     damage in an area, then calls a divine skybeam that pulses periodically and creates expanding Ignited Ground"), at
#     its uses a second, the ones its Glory pays for (models.glory_uses, MECH-C's / GEM15's Glory plan); the ground taken
#     to last the skybeam's 20 pulses 0.2 s apart, 4 s (SpearOfSolarisPulsePlayer solaris_spear_number_of_pulses 20,
#     solaris_spear_pulse_delay_ms 200; no ground duration stat in its stat sets: LOW), at the boss (the spear is thrown
#     at the target, LOW); its own Ignite stays PoB's "Ignited Ground" set (a search choice, MECH79's note);
#   - counted (MECH84): Burning Inscription (SupportBurningRunesPlayer, PoB: "Supports any Skill which creates Runic
#     Inscriptions when Cast, causing those Runic Inscriptions to trigger Burning Inscription when they expire. Burning
#     Inscription is a Spell which creates Ignited Ground based off of your Maximum Mana"; it supports curses, Bone Blast
#     and Profane Ritual, PoB's requireSkillTypes AppliesCurse / CreatesGroundRune; KeywordPopups RunicInscription:
#     "Certain Skills which create Areas of Effect on the ground create Runic Inscriptions"): one ground per cast of a
#     supported skill (one Inscription each, triggered when it expires: the delay doesn't change the rate), lasting
#     TriggeredBurningRunesPlayer's base_skill_effect_duration 4 s (duration modifiers not read), radius 2 m, made where
#     the skill was cast, at the boss (a curse is cast on it, LOW). Its own Ignite (20% of maximum Mana, PoB's
#     IgniteFireHitDamage on the triggered skill) is PoB's triggered skill's: not added here (no double count; whether
#     the triggered skill reaches Full DPS at the casts' rate is MECH-B's trigger-rate item).
IGNITED_GROUND_RADIUS = 2.0  # m (KeywordPopups IgnitedGround's default)
SPEAR_OF_SOLARIS, BURNING_INSCRIPTION = "Spear of Solaris", "Burning Inscription"
IGNITED_GROUND_MAKERS = {"Incinerate": 8.0, "Ball Lightning": 4.0, "Explosive Spear": 8.0,  # s, the charged uses' ground
                         SPEAR_OF_SOLARIS: 4.0, BURNING_INSCRIPTION: 4.0}
EXPLOSIVE_SPEAR = "Explosive Spear"
INFUSED_MAKERS = ("Incinerate", "Ball Lightning")  # (the ones a Fire Infusion gates)
MAKER_USES = {EXPLOSIVE_SPEAR: "Frenzy-Charged throws", SPEAR_OF_SOLARIS: "uses its Glory pays for",
              BURNING_INSCRIPTION: "supported casts' Inscriptions"}  # (else "Fire-Infused casts")
IGNITED_ON_USE = re.compile(r"Creates Ignited Ground for (\d+) seconds when used", re.I)
IGNITED_WHILE_MOVING = re.compile(r"Drop Ignited Ground while moving, which lasts (\d+) seconds", re.I)
IGNITED_NOT_COUNTED = "each ground's duration modifiers not read"


def ignited_ground(makers: dict[str, tuple[float, str]], texts: list[str], stats: dict,
                   charms: dict[str, tuple[float, str]] | None = None,
                   pieces: list | None = None) -> tuple[float, str]:
    """(share of the fight the boss stands in your Ignited Ground, how); see the note above. `makers`: {skill: (its
    Fire-Infused casts per second, how)}; `charms`: {line text: (the charm's uses per second, how)}. (0.0, "") with no
    source. `pieces` (MECH80, ground_ignite): each counted source is appended as (label, its share, its ground's s, the
    Fire damage it Ignites as though dealing, or None where it isn't read)."""
    parts, notes, off = [], [], 1.0
    for name, (rate, how) in makers.items():
        seconds = IGNITED_GROUND_MAKERS[name]
        up = _covered(rate, 1.0, seconds)
        parts.append(f"{name}'s ground on its {MAKER_USES.get(name, 'Fire-Infused casts')} ({how}; {seconds:g} s, made at "
                     f"the boss, LOW): {up:.0%}")
        off *= 1 - up
        if pieces is not None:
            pieces.append((f"{name}'s ground", up, seconds, None))
    near = _boss_metres(stats) <= IGNITED_GROUND_RADIUS
    for text, (uses, how) in (charms or {}).items():
        if m := IGNITED_ON_USE.search(text.strip()):
            if not near:
                notes.append(f"the charm's ground at your feet (you fight {_boss_metres(stats):g} m from the boss: not "
                             "counted)")
                continue
            up = _covered(uses, 1.0, int(m.group(1)))
            parts.append(f"the charm's ground at your feet ({how}; {m.group(1)} s, {IGNITED_GROUND_RADIUS:g} m): {up:.0%}")
            off *= 1 - up
            if pieces is not None:
                pieces.append(("the charm's ground", up, float(m.group(1)), _as_though_life(text, stats)))
    for text in texts:
        if m := IGNITED_WHILE_MOVING.search(text):
            if stats.get("_stationary") and not number(stats.get("_rolling")):
                notes.append("the trail while moving (the build stands still and doesn't roll: not counted)")
            elif not near:
                notes.append(f"the trail while moving (you fight {_boss_metres(stats):g} m from the boss: not counted)")
            else:
                parts.append(f"the trail while moving ({m.group(1)} s): walk or roll through the boss's spot at least "
                             f"every {m.group(1)} s (a way to play the build; without it, the trail adds nothing): 100%")
                off = 0.0
                if pieces is not None:
                    pieces.append(("the trail while moving", 1.0, float(m.group(1)), _as_though_life(text, stats)))
            break
    if not parts and not notes:
        return 0.0, ""
    share = 1 - off
    return share, "; ".join(parts + notes + [f"on Ignited Ground {share:.0%} of the fight ({IGNITED_NOT_COUNTED})"])


# Flammability for the Ignites that don't Hit (MECH80; plans/kw-ignited-ground.md item 2, kw-fire-damage.md item C).
# Patch 0.3.0: "Effects which don't Hit but Ignite targets as though they did (such as from Incinerate or Ignited Ground)
# also inflict Flammability, but are not able to ignite targets until the target has 50% Flammability"; Flammability
# "lasts for 8 seconds on Monsters"; "Multiple hits can stack up Flammability independently" (grep_notes "Flammability":
# no later entry changes the 50%, the 8 s or the stacking; 0.4.0 makes igniting auras "including Ignited Ground" apply
# regular Ignites, reapplied "the next time the aura applies flammability"). KeywordPopups Flammability: "1% chance to
# Ignite the target for every 5% of the target's Ailment Threshold dealt as Fire damage" (PoB: IgniteChanceMultiplier 20,
# Data/Misc.lua 73, and CalcOffence 5613-5629 computes IgniteChanceOnHit that way from the hit, so PoB's
# IgniteChancePerHit is a hit's Flammability). The boss's Flammability = its live stacks: the main skill's landing hits
# (IgniteChancePerHit each), the other groups' Ignites (stats["_ailment_others"]: their chance a hit) and the no-Hit
# source's own applications (as-though damage / EnemyAilmentThreshold x 20 each), live for 8 s, their count Poisson
# (applications independent; one average magnitude per stack, MEDIUM). The share of the time it holds 50% is the share
# the no-Hit source can Ignite the boss.
#   - The aura's / ground's application interval isn't in the game data or the patch notes (checked: the exported
#     tables' stats holding "flammab" (15 ids, none an interval); grep_notes "Flammability"; poe2db's Ignited Ground page
#     (via search excerpts) doesn't give one; poe2wiki refuses automated reads): NO_HIT_TICK, LOW, named to the manager.
#   - The source's own applications are scaled by the build's global Flammability Magnitude modifiers (the bridge's
#     flammabilityMagnitude: EnemyIgniteChance / AilmentChance INC and MORE, the enemy's SelfIgniteChance, as PoB's
#     CalcOffence 5619-5621 scales a hit's), stats["_flammability_mod"]. Whether a damage modifier also raises the
#     as-though damage isn't stated anywhere I could read (UNVERIFIED): not applied, the line's number taken raw.
#   - Not read (each an UNDERCOUNT where it would lift the boss to 50%): Incinerate's / Ball Lightning's ground damage
#     (their own applications count 0 here, so their ground Ignites only on the build's other Flammability); stacking
#     among the main skill's own hits (ailment_uptime still takes each hit's chance alone, as PoB).
#   - Its Ignite's damage isn't added here (as Radiant Grief's note): only the boss's Ignited state is; for grounds that
#     name their Fire (Beira's Anguish, Birth of Fury) models.ground_life_ignite_model adds it (MECH83).
FLAMMABILITY_SECONDS = 8.0  # s on monsters (patch 0.3.0)
FLAMMABILITY_PER_THRESHOLD = 20.0  # % per 100% of the Ailment Threshold dealt (KeywordPopups Flammability: 1% per 5%)
NO_HIT_IGNITE_AT = 50.0  # % Flammability before a no-Hit effect can Ignite (patch 0.3.0)
NO_HIT_TICK = 0.5  # s between an aura's / a ground's applications (LOW: not in the game data; see the note above)
AS_THOUGH_LIFE = re.compile(r"as though dealing Fire Damage equal to (\d+)% of your maximum Life", re.I)
POISSON_TAIL = 20000  # stacks: past this many needed, the share is taken as 0


def _as_though_life(text: str, stats: dict) -> float | None:
    """The Fire damage a line Ignites as though dealing, "equal to N% of your maximum Life" (Beira's Anguish, Birth of
    Fury), from the build's Life; None when the line names none."""
    m = AS_THOUGH_LIFE.search(text)
    return float(m.group(1)) / 100.0 * number(stats.get("Life")) if m else None


def flammability_sources(stats: dict) -> list[tuple[str, float, float]]:
    """[(source, applications per second, Flammability % each)]: the main skill's landing hits and the other groups'
    Ignites (see the note above)."""
    out = []
    hits = number(stats.get("Speed")) * min(number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
    each = min(number(stats.get("IgniteChancePerHit")), 100.0)
    if hits > 0 and each > 0:
        out.append(("the main skill's hits", hits, each))
    for name, h, c, _ in (stats.get("_ailment_others") or {}).get("Ignite") or ():
        if h > 0 and c > 0:
            out.append((name, h, min(c, 1.0) * 100.0))
    return out


def as_though_flammability(damage: float | None, stats: dict) -> float:
    """Flammability % one application of a no-Hit Ignite gives, as though dealing `damage` Fire (see the note above)."""
    threshold = number(stats.get("EnemyAilmentThreshold"))
    if not damage or threshold <= 0:
        return 0.0
    mod = stats.get("_flammability_mod") or {}
    scale = max(0.0, 1 + number(mod.get("inc")) / 100) * (number(mod.get("more")) if "more" in mod else 1.0)
    return min(100.0, number(damage) / threshold * FLAMMABILITY_PER_THRESHOLD * scale)


def flammability_reach(sources: list[tuple[str, float, float]], window: float = FLAMMABILITY_SECONDS) -> float:
    """Share of the time the boss's live Flammability stacks total at least NO_HIT_IGNITE_AT: their count Poisson with
    mean (applications a second x `window`), at the sources' average magnitude (see the note above)."""
    mean = sum(r for _, r, _ in sources) * window
    total = sum(r * m for _, r, m in sources) * window
    if mean <= 0 or total <= 0:
        return 0.0
    need = math.ceil(NO_HIT_IGNITE_AT / (total / mean) - 1e-9)
    if need > POISSON_TAIL:
        return 0.0
    term, below = math.exp(-mean), 0.0  # P(N < need) = sum of the first `need` Poisson terms
    if term == 0.0:  # (a mean past ~745: underflow; the count sits near its mean)
        return 1.0 if mean >= need else 0.0
    for k in range(need):
        below += term
        term *= mean / (k + 1)
    return min(1.0, max(0.0, 1.0 - below))


def no_hit_ignite(stats: dict, damage: float | None, window: float) -> tuple[float, str]:
    """(share of its time a no-Hit Ignite source can Ignite the boss, how): the build's Flammability plus the source's
    own applications every NO_HIT_TICK for `window` s (at most the stacks' 8 s) reaching 50%."""
    own = as_though_flammability(damage, stats)
    found = flammability_sources(stats)
    span = min(window, FLAMMABILITY_SECONDS)
    sources = found + ([("its own", 1.0 / NO_HIT_TICK, own)] if own > 0 else [])
    # (its own stacks live only while it lasts: scaled to the 8 s window)
    sources = [(n, r * (span / FLAMMABILITY_SECONDS) if n == "its own" else r, m) for n, r, m in sources]
    reach = flammability_reach(sources)
    level = sum(r * m for _, r, m in sources) * FLAMMABILITY_SECONDS
    said = ", ".join(f"{n} {r:.2f}/s x {m:.2f}%" for n, r, m in sources) or "no Flammability source"
    return reach, (f"the boss at {level:.0f}% Flammability on average ({said}; 8 s stacks; the source's applications every "
                   f"{NO_HIT_TICK:g} s, LOW): at 50% or more {reach:.0%} of the time")


def ground_ignite(pieces: list, stats: dict) -> tuple[float, str]:
    """(share of the fight your Ignited Ground keeps the boss Ignited, how) from ignited_ground's `pieces`: each source's
    share x the share of it the boss holds 50% Flammability (no_hit_ignite), sources independent."""
    off, parts = 1.0, []
    for label, up, seconds, damage in pieces:
        if up <= 0:
            continue
        reach, how = no_hit_ignite(stats, damage, seconds)
        dealt = f"as though dealing {damage:.0f} Fire" if damage else "its own damage not read (an UNDERCOUNT)"
        parts.append(f"{label} ({dealt}) {up:.0%} of the fight; {how}")
        off *= 1 - up * reach
    if not parts:
        return 0.0, ""
    share = 1 - off
    return share, "Ignited Ground Ignites the boss " + f"{share:.0%} of the fight: " + "; ".join(parts) + (
        " (its Ignite's damage not added: only the Ignited state)")


# Lit Oil Ground under the boss (MECH82; PATCH_CHECK G4-4 / G2-3, an UNDERCOUNT: MECH79 kept the Oil Ground sets from being
# scored as Hits, NO_HIT_STAT_SETS, and nothing counted their Ignite). The game (26650308): KeywordPopups OilGround
# "Enemies standing in Oil Ground have their movement speed Slowed and are inflicted with Exposure. Ignited enemies,
# Ignited Ground, or Detonator Skills that touch the Oil cause it to catch fire, Igniting enemies instead of Slowing them,
# but still inflicting the Exposure"; OilGrenadeOilGroundPlayer / OilBarrageOilPlayer: skill_main_hand_total_attack_fire_
# damage_permyriad_to_add_as_unscalable_damage_to_oil_ground_ignite 15000 / 10000, 100% Physical converted to Fire,
# display_statset_no_hit_damage; the Oil Ground lasts 6 s (Oil Grenade's base_secondary_skill_effect_duration 6000, Oil
# Barrage's base_skill_effect_duration 6000). Patch notes (grep_notes "oil (grenade|barrage|ground)", oldest first; the
# newest of each kept): 0.3.0 "Oil Ground's duration is now refreshed when it catches fire"; 0.4.0 "Oil Ground now has
# Once Ignited, Oil Ground adds 150% of this Attack's Fire Damage as unscalable Damage to the Ignite", cooldown 4 s, 6 s;
# 0.5.0 its Ignites can't compound; 0.5.5 Chaos-damage Ignites light it too. Counted:
#   - the ground at the boss (the grenade thrown at it, MEDIUM; the boss taken as standing in it: the boss-still
#     headline), up _covered(uses, 1, 6 s); Oil Barrage's only on unempowered volleys: with Power Charges held, every
#     volley is taken as the Power-Charged barrage, which makes no Oil Ground (ActiveSkills oil_barrage, 26476662 and
#     26650308 alike: "Consumes Power Charges if possible to instead Channel a sustained barrage of electrified Oil that
#     does not create Oil Ground"; the lower reading, labelled);
#   - lit when something lights it within its 6 s: a Detonator's hits on the boss (the main skill's landing hits, other
#     groups' uses; PoB's gems.json skillTypes "Detonator"), the boss's own Ignites (the build's Ignite applications a
#     second: flammability_sources at PoB's per-hit chance, the lower reading) and your Ignited Ground under the boss;
#     a patch lit with chance 1 - (1 - ground) x exp(-lights/s x 6 s), and once lit it burns a refreshed 6 s, taken to
#     make up the wait before it catches (MEDIUM);
#   - the lit ground Ignites as Ignited Ground does (a no-Hit Ignite: KeywordPopups Flammability's "such as Ignited
#     Ground"; 0.3.0 fixed Ignited Oil Ground igniting Whirlwinds, a ground effect), so only while the boss holds 50%
#     Flammability (no_hit_ignite), as though dealing the Oil Ground set's Fire hit (PoB's useRates on that set,
#     crit-weighted, the bridge's statSets) plus the added 150% / 100%, which is also the Ignite's base: 250% / 200%
#     (MECH83: RESEARCH28 R28-12, MEDIUM; MECH82 took the added share alone, an UNDERCOUNT); and each lit oiling Ignites
#     the Oil-covered boss outright first (KeywordPopups Oil, HIGH: oil_ignite_share);
#   - its damage (models.oil_ignite_model): 20% of that base a second, through the boss's Fire resistance, no Ignite
#     modifiers ("unscalable"); only the strongest Ignite deals damage, so it adds what it beats the build's own Ignite
#     by (PoB's IgniteDPS), for the share the boss is Ignited by the lit oil.
# Not counted (labelled): the boss covered in Oil taking 200% more Flammability (KeywordPopups Oil; removed when it Ignites:
# an UNDERCOUNT of the 50% reach); a lit ground re-lighting the next grenade's oil; the boss moving out (the play guide's
# boss-moving row). The Oil's Exposure is PoB's own (SkillStatMap 1775, a Debuff of the skill applied while the gem is in
# an enabled group): a grenade every 4 s keeps 6 s of Oil up, so its always-on figure holds for a pressed Oil Grenade.
ARCTIC_HOWL = "Arctic Howl"  # (MECH82: it Freezes a Primed boss, models.primed_refill)
# (MECH83, RESEARCH28 R28-12, oil-ground-ignite.md, the manager 16:11) the Ignite's base: the Oil Ground set's own Fire
# PLUS the added unscalable 150% / 100% ("adds", 0.4.0): 250% / 200% (MEDIUM; MECH82 took 150% / 100% alone, an
# UNDERCOUNT). And the Oil on the boss (KeywordPopups Oil: "Ignited Ground or Detonator Skills will Ignite Oil-covered
# enemies. This removes the Oil") is Ignited OUTRIGHT, no Flammability needed (HIGH), once per oiling; the lit ground
# itself needs 50% Flammability after that (the 0.3.0 no-Hit rule, MEDIUM): oil_ignite_share.
OIL_ADDED = {"Oil Grenade": 1.5, "Oil Barrage": 1.0}  # the permyriad stat / 10000 (game files above)
OIL_GROUND_SKILLS = {name: 1.0 + added for name, added in OIL_ADDED.items()}  # the set's own Fire + the added share
OIL_GROUND_SET = 2  # PoB's "Oil Ground" stat set of both (act_str.lua OilBarragePlayer / OilGrenadePlayer statSets[2])
OIL_GROUND_SECONDS = 6.0  # s, both skills' Oil Ground (game files above; duration modifiers not read)
DETONATOR = "Detonator"  # PoB's skill type for "Detonator Skills" (KeywordPopups Detonator)


def oil_lit_chance(lights: float, ground: float = 0.0, seconds: float = OIL_GROUND_SECONDS) -> float:
    """Chance a patch of Oil Ground catches fire within its `seconds`: `lights` lighting events a second on the boss
    (Poisson) or your Ignited Ground under the boss `ground` of the fight."""
    return 1.0 - (1.0 - min(max(ground, 0.0), 1.0)) * math.exp(-max(lights, 0.0) * seconds)


def oil_ignite_share(lit: float, oilings: float, reach: float, seconds: float) -> tuple[float, float]:
    """(share of the fight the boss is Ignited by your lit Oil, the outright part) (R28-12): each lit oiling (`oilings` a
    second) Ignites the Oil-covered boss outright for the Ignite's `seconds` (_covered: evenly spaced, as the ground's
    own share; a grenade on its cooldown), at most the lit
    share; the rest of the lit time Ignites only at 50% Flammability (`reach`, no_hit_ignite)."""
    outright = min(lit, _covered(max(oilings, 0.0), 1.0, seconds)) if lit > 0 else 0.0
    return outright + (lit - outright) * min(max(reach, 0.0), 1.0), outright


def oil_ground(makers: dict[str, tuple[float, float, str]], lighters: list[tuple[str, float]], ground: float,
               pieces: list | None = None) -> tuple[list[tuple[str, float, float]], str]:
    """([(skill, share of the fight its lit Oil Ground is under the boss, the Fire it Ignites as though dealing)], how);
    see the note above. `makers`: {skill: (its Oil-making uses a second, its Oil Ground set's Fire hit, how)};
    `lighters`: [(what, lights a second)]; `ground`: your Ignited Ground's share under the boss. Each counted ground is
    appended to `pieces` as ground_ignite reads them."""
    lights = sum(rate for _, rate in lighters if rate > 0)
    chance = oil_lit_chance(lights, ground)
    said = ", ".join(f"{what} {rate:.2f}/s" for what, rate in lighters if rate > 0)
    if ground > 0:
        said = ", ".join(filter(None, [said, f"your Ignited Ground under the boss {ground:.0%}"]))
    out, parts = [], []
    for name, (uses, fire, how) in sorted(makers.items()):
        up = _covered(uses, 1.0, OIL_GROUND_SECONDS)
        lit = up * chance
        damage = OIL_GROUND_SKILLS[name] * fire
        parts.append(f"{name}'s Oil Ground ({how}; {OIL_GROUND_SECONDS:g} s, thrown at the boss, MEDIUM) up {up:.0%}, lit "
                     f"{chance:.0%} of the time ({said or 'nothing lights it'}): {lit:.0%} of the fight, Igniting as though "
                     f"dealing its {fire:,.0f} Fire hit + the added {OIL_ADDED[name]:.0%} = {damage:,.0f} (MEDIUM)")
        if lit > 0 and damage > 0:
            out.append((name, lit, damage, uses * chance))
            if pieces is not None:
                pieces.append((f"{name}'s lit Oil Ground", lit, OIL_GROUND_SECONDS, damage))
    return out, "; ".join(parts)


# Uruk's Smelting's Physical damage taken (MECH80; PATCH_CHECK G2-4, an UNDERCOUNT: PoB maps the support's
# support_increased_armour_break_armour_break_amount_+%_final (break_amount_scale, MECH66) but not
# support_demolisher_physical_damage_taken_+%_on_fully_armour_breaking_up_to_20% (no SkillStatMap or Modules entry), and
# nothing here read it). The gem: "Fully Breaking Armour with Supported Skills permanently increases the Physical Damage
# taken by those affected, up to a cap" (PoB sup_str.lua 7858; 5 per Full Break, the stat's id caps it at 20%). A boss
# Fully Broken k times takes min(5k, 20)% increased Physical Damage for the rest of the fight. Its average over the fight
# (the damage-taken INC is linear, so the time-weighted average gives the time-weighted DPS): the k-th Full Break at
# k / the Full Break rate (_full_break_rate, the Armour Break cycle; the first taken one cycle in, MEDIUM), the fight's
# length from the boss's Life + ES over the build's Full DPS before this line (MEDIUM: a fight this line shortens is
# averaged as the longer one, a small undercount). Applied as "Nearby Enemies take N% increased Physical Damage"
# (ModParser 1399: an unconditional enemy PhysicalDamageTaken INC). Every Full Break the build makes is counted, though
# only the supported skills' feed it (Battershout's Warcry breaks with Uruk's on the main skill: a small OVERCOUNT,
# labelled).
URUK = "Uruk's Smelting"
URUK_STAT = "support_demolisher_physical_damage_taken_+%_on_fully_armour_breaking_up_to_20%"
URUK_CAP = 20.0


def uruk_taken(per_break: float, rate: float, fight: float, cap: float = URUK_CAP) -> tuple[float, str]:
    """(the fight-average % increased Physical Damage taken, how) from `per_break`% a Full Break at `rate` Full Breaks
    a second over a `fight`-second fight, up to `cap`%."""
    if per_break <= 0 or rate <= 0 or fight <= 0:
        return 0.0, ""
    total, k, steps = 0.0, 0, []
    while per_break * k < cap:
        k += 1
        step = min(per_break, cap - per_break * (k - 1))
        at = k / rate
        total += step * max(0.0, fight - at) / fight
        steps.append(f"{at:.0f} s")
    return total, (f"{per_break:g}% a Full Break up to {cap:g}%: Full Breaks at {', '.join(steps)} "
                   f"({rate:.3f}/s) in a {fight:.0f} s fight: {total:.1f}% on average (MEDIUM)")


# Incinerate's fuel (MECH79; before, nothing read it and Incinerate scored as channelled all fight, an OVERCOUNT where
# the build spends little Mana on other skills). Game: IncineratePlayer "Gain fuel by spending Mana on any Skill, then
# use the accumulated fuel ... Cannot gain fuel while using this skill"; incinerate_fuel_gain_per_mana_spent (ms of
# channelling per Mana: 7 at gem levels 1-10 down to 2 from level 20, GrantedEffectStatSetsPerLevel), incinerate_maximum_
# fuel 5000, incinerate_minimum_fuel_to_use 1000; patch 0.4.0 "Now gains 7-2 milliseconds of fuel per Mana spent ... Now
# can store up to 5 seconds", 0.4.0b no fuel while your Spell Totem uses it (grep_notes --history "fuel", newest
# 0.4.0b). So each second of fuel is a second of channelling: the Mana the build's other skills spend per second of the
# fight (their fight-average use rates) x that rate gives the channelling per fight second, as a share of the main
# skill's time the score leaves after those presses (Objective.busy), at most 1 (in Full DPS, where a pressed group is
# charged through press_scales instead, busy is 0 and the press time isn't taken off: a small OVERCOUNT). The play: store
# a full tank and channel it out (the longest channel, which Compounding Ignite rewards): each channel lasts the tank
# (5 s), or what one gap's fuel gives if the build never fills it (at least the 1 s needed to start).
INCINERATE = "Incinerate"
INCINERATE_FUEL_STAT = "incinerate_fuel_gain_per_mana_spent"
INCINERATE_MAX_FUEL_STAT, INCINERATE_MIN_FUEL_STAT = "incinerate_maximum_fuel", "incinerate_minimum_fuel_to_use"
INCINERATE_CHANNEL_VAR = "bridgeConditionIncinerateChannelling"  # (bridge: Condition:IncinerateChannelling)


def incinerate_channel(mana_per_second: float, ms_per_mana: float, max_ms: float = 5000.0,
                       min_ms: float = 1000.0, busy: float = 0.0) -> tuple[float, float, str]:
    """(share of the main skill's time spent channelling, one channel's length s, how) from the Mana the build's other
    skills spend per second of the fight (their fight-average use rates; see the note above). `busy`: the share of the
    fight the score already takes from the main skill for those presses (Objective.busy), so the channel's share of
    what's left is fuel / (1 - busy). (0, 0, how) with no Mana spent."""
    fuel = mana_per_second * ms_per_mana / 1000.0  # seconds of channelling gained per second of the fight
    if fuel <= 0:
        return 0.0, 0.0, ("no other skill of the build spends Mana, so Incinerate gains no fuel: 0 (feed it by pressing "
                          "Mana-costing skills)")
    left = 1.0 - min(max(busy, 0.0), 1.0)
    if left <= 1e-9:
        return 0.0, 0.0, "the presses that make its fuel take the whole fight: no time left to channel, 0"
    share = min(1.0, fuel / left)
    channel = max(min_ms, max_ms) / 1000.0
    return share, channel, (f"{mana_per_second:.1f} Mana/s spent on other skills x {ms_per_mana:g} ms of fuel per Mana = "
                            f"{fuel:.2f} s of channelling per second of the fight"
                            + (f", over the {1 - busy:.0%} left after pressing them" if busy > 0 else "")
                            + f": channelling {share:.0%} of that time, in {channel:g} s channels (a full tank each "
                            "time, MEDIUM)")


# Compounding Ignite (MECH79; plans/term-ignites.md item 1, kw-compounding-ignite.md; the manager 16:11 from GEM13).
# KeywordPopups CompoundIgnite: "When Igniting a target which has already been Ignited by the same use of this Skill, the
# existing Ignite is removed to boost the new Ignite, multiplying its Magnitude by the total number of Ignites compounded
# this way"; active_skill_compounding_ignites on IncineratePlayer and WyvernFlameBreathPlayer only among player skills
# (checked: PLANS5's stat search over mods, passives and every stat set; poe2db's Compounding_Ignite page lists the same
# two); patch 0.4.0 Incinerate "Ignites from a single use of this Skill Compound if not from Ignited Ground". PoB has no
# Compounding (no reader of the stat). A use is one channel: the k-th Ignite of a channel is k x its own magnitude (read:
# the new hit's magnitude x the count, MEDIUM), the last one living out its duration after the channel ends; one Ignite
# is live at a time (the strongest: a previous channel's last Ignite outlives the next channel's first small ones).
# The model: the Ignite's average magnitude over the cycle (channel + gap), with compounding over without (same Ignite
# times, chance and duration), applied as more Ignite Magnitude on the skill. Flame Breath's channel: its free window
# (channelled_skill_suppress_ongoing_rage_cost_for_first_X_ms, 4 s + quality) recast at once ("do not go on cooldown on
# finishing channel"). Rage would prolong it, but the skill can't gain Rage while in use (skill_cannot_gain_rage_during_use)
# and back-to-back channels leave no time between them to gain any, so each starts with none: the free window is the
# channel (MEDIUM; a play that pauses to refill Rage for longer channels isn't modelled).
COMPOUNDING_SKILLS = {INCINERATE, "Flame Breath"}
FLAME_BREATH_FREE_STAT = "channelled_skill_suppress_ongoing_rage_cost_for_first_X_ms"
COMPOUND_STEP = 0.01  # s, the sampling step of compounding_factor


def compounding_factor(rate: float, channel: float, gap: float, duration: float) -> tuple[float, int]:
    """(average Ignite magnitude with Compounding / without, Ignites per channel) for Ignites landing at `rate` per
    second during each `channel` s, `gap` s between channels, each lasting `duration` s."""
    if rate <= 0 or channel <= 0 or duration <= 0:
        return 1.0, 0
    n = max(1, int(math.floor(rate * channel - 1e-9)) + 1)  # (the first at the channel's start)
    times = [k / rate for k in range(n)]
    cycle = channel + max(gap, 0.0)
    tail = times[-1] - cycle  # (the previous channel's last Ignite, n strong)
    comp = plain = 0.0
    steps = max(1, int(round(cycle / COMPOUND_STEP)))
    for i in range(steps):
        t = (i + 0.5) * cycle / steps
        live = [k for k, at in enumerate(times) if at <= t < at + duration]
        own = (max(live) + 1) if live else 0
        prev = n if t < tail + duration else 0
        comp += max(own, prev)
        plain += 1 if (own or prev) else 0
    return (comp / plain if plain else 1.0), n


# The boss Burning from Fire damage over time that isn't an Ignite (MECH80; PATCH_CHECK MD-5, an UNDERCOUNT: nothing set
# PoB's conditionEnemyBurning, ConfigOptions 1785, so "against Burning Enemies" lines counted only through Ignite, which
# PoB already makes imply Burning, CalcSetup 83). KeywordPopups Burning: "Enemies taking Fire damage over time are
# Burning. Usually this occurs because the enemy is Ignited." Counted: the main skill's own Fire damage over time (PoB's
# FireDot: the player skills carrying base_fire_damage_to_deal_per_minute are Flame Wall and Incinerate, checked: every
# skill holding the stat in PoB's Data/Skills, the others are monster and minion skills) for the share of the fight the
# skill runs (1 for a main skill used all fight, MEDIUM: Flame Wall taken as placed on the boss; Incinerate its fuel's
# share). The box is set at that share on its own: PoB's Ignited box still implies Burning on top, the two taken as
# independent. Not counted (an UNDERCOUNT, labelled): other groups' Fire damage over time, Herald of Ash's Burning
# from overkill.
def _enemy_burning(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Burning box at the share of the fight the main skill's own Fire damage over time is on the boss (MECH80)."""
    dot = number(stats.get("FireDot"))
    if dot <= 0:
        return 0.0, ""
    share, how = 1.0, "the main skill used all fight (MEDIUM)"
    if stats.get("_main_skill") == INCINERATE:
        share, _, fuel = stats.get("_incinerate_fuel") or (0.0, 0.0, "fuel not read")
        how = f"Incinerate channelled {share:.0%} of the fight ({fuel})"
    return _box(share, f"the main skill's own {dot:,.0f} Fire damage over time a second: {how} (other skills' Fire "
                       "damage over time not counted)")


# The boss Isolated (MECH81; PATCH_CHECK MD-2, mirror sweep D: PoB's conditionEnemyIsolated, ConfigOptions 1893, which
# nothing set, so Vorana's Siege's 20% more Hit damage counted 0, an UNDERCOUNT). KeywordPopups Isolated (26650308): "A
# target is Isolated if none of its allies are within 6m of it." Readers: Vorana's Siege's
# support_voranas_siege_hit_damage_+%_final_vs_isolated_enemies (PoB other.lua 20682, gated on the enemy's Isolated) is
# the one player stat (checked: every Stats id holding "isolat" on 26650308: that one; PassiveSkills /
# GrantedEffectStatSets hold no other; Mods' "Isolation" rows are Delirium affliction names; PoB ModCache var="Isolated":
# none, NOTES2 part_MD; poe2db's Vorana's Siege page is the same stat). The boss stands alone except while its
# killable adds are up (bosses.PRESETS kills: the King's totems 43% of the fight, Atziri phase 1's acolytes all of it):
# during those parts taken as NOT Isolated (an add within 6 m of the boss all that time: the cautious reading, MEDIUM-LOW;
# where the adds stand isn't in the data). Bosses whose adds have no known rate (Kulemak, Olroth, the Raven) are taken as
# Isolated all fight (UNVERIFIED: a possible OVERCOUNT there, labelled). Only set when the build carries Vorana's Siege.
VORANAS_SIEGE = "Vorana's Siege"
ISOLATED_RADIUS = 6.0  # m (KeywordPopups Isolated)


def _enemy_isolated(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Isolated box: the share of the fight the boss has no add near it (MECH81, MD-2)."""
    if VORANAS_SIEGE not in (stats.get("_gem_names") or ()):
        return 0.0, ""
    boss = bosses.of(stats)
    adds = min(1.0, sum(k.share for k in boss.kills))
    share = 1.0 - adds
    if boss.kills:
        how = (f"not while its adds are up ({adds:.0%} of the fight: "
               + "; ".join(k.what.split(" (")[0] for k in boss.kills)
               + f"; an add taken within {ISOLATED_RADIUS:g} m all that time, MEDIUM-LOW)")
    else:
        how = (f"no killable adds with a known rate against {boss.name}: alone all fight (UNVERIFIED where its adds "
               "have no known rate)")
    return _box(share, f"Isolated {share:.0%} of the fight (KeywordPopups: no ally within {ISOLATED_RADIUS:g} m; "
                       f"Vorana's Siege's more Hit damage): {how}")


# Stormfire (MECH81; PATCH_CHECK G2-2, an UNDERCOUNT: PoB maps its stat nowhere). Game (patch 0.4.0, the newest entry
# by grep_notes --history "Stormfire": "Now causes enemies Shocked by Supported Skills to take a percentage of Ignite
# damage as Lightning damage as well as Fire damage"; PoB sup_int.lua 8214 / the game's SupportStormfirePlayer:
# support_stormfire_targets_shocked_by_this_take_%_of_damage_from_ignite_as_lightning_damage 20). While the boss is
# Shocked by the supported skill it takes that % of the Ignite's damage again, as Lightning ("as well as Fire": an
# extra, not a conversion; MEDIUM on the wording). Counted on the main skill's own Ignite (PoB's IgniteDPS) as more Ignite
# Magnitude: % x the main skill's own Shock uptime (ailment_uptime: Shocks from Shocked Ground or other skills aren't
# "Shocked by Supported Skills") x (1 - the boss's Lightning resistance) / (1 - its Fire resistance) (the preset's base
# resistances; curses / Exposure lowering one of them not counted, MEDIUM). Stormfire on another group, and other
# Ignites on the boss (Ignited Ground, other groups'), not counted (labelled).
STORMFIRE = "Stormfire"
STORMFIRE_STAT = "support_stormfire_targets_shocked_by_this_take_%_of_damage_from_ignite_as_lightning_damage"


def stormfire_more(pct: float, shocked: float, fire: float, lightning: float) -> tuple[float, str]:
    """(% more Ignite Magnitude, how): Stormfire's `pct` of Ignite damage again as Lightning for the `shocked` share of
    the fight, rescaled from the boss's Fire resistance to its Lightning resistance (percent values)."""
    if pct <= 0 or shocked <= 0:
        return 0.0, f"{STORMFIRE}: the main skill doesn't Shock the boss (0)" if pct > 0 else ""
    ratio = (1 - min(lightning, 100) / 100) / max(1e-9, 1 - min(fire, 99.9) / 100)
    more = pct * min(1.0, shocked) * ratio
    return more, (f"{STORMFIRE}: {pct:g}% of the Ignite's damage again as Lightning while the boss is Shocked by the main "
                  f"skill ({min(1.0, shocked):.0%} of the fight), at {lightning:g}% Lightning vs {fire:g}% Fire "
                  f"resistance: {more:.1f}% more Ignite damage (other Ignites on the boss not counted)")


# Unravelling (MECH81; PATCH_CHECK K1-1, an UNDERCOUNT: an Unravelling Acolyte of Chayula got no ailment value from its
# Chaos damage). KeywordPopups UnravellingBuff (26650308): "While affected by Unravelling, your Chaos Damage randomly
# either also contributes to Freeze buildup, Flammability and Ignite Magnitudes, or Shock chance - changing which it
# contributes to every two seconds" (patch 0.3.0, no later line). PoB parses "Grants Unravelling" to a flag (ModParser
# 3428) and gives Chaos the element's ailment (ChaosCanFreeze / Ignite / Shock, CalcPerform 771-774) only under
# Condition:<Cold|Fire|Lightning>Unravel, which its config list conditionUnravelling (one value, no default) sets: nothing
# set it. Each element is taken as on a third of the fight ("randomly", equal odds: UNVERIFIED, MEDIUM), set as three
# bridge conditions (engine/bridge.lua bridgeCondition<Name>) each weighed at 1/3 (Search.weigh_uptimes; the three taken
# as independent there, labelled), so each element's ailment value counts at a third. Only when a build line grants it.
UNRAVEL_LINE = re.compile(r"^grants unravelling$", re.I)
UNRAVEL_ELEMENTS = {"Cold": "Freeze buildup", "Fire": "Flammability and Ignite Magnitude", "Lightning": "Shock chance"}


def _unravelling(element: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def value(lines: list[str], stats: dict) -> tuple[object, str]:
        if not any(UNRAVEL_LINE.match(t.strip()) for t in lines):
            return 0.0, ""
        share = 1 / len(UNRAVEL_ELEMENTS)
        return _box(share, f"Unravelling: Chaos damage also counts toward {UNRAVEL_ELEMENTS[element]} {share:.0%} of the "
                           "fight (KeywordPopups UnravellingBuff: one element at random, changing every 2 s; equal odds "
                           "UNVERIFIED, MEDIUM)")
    return value


def _incinerate_channelling(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Channelling Incinerate box at the share of the fight its fuel allows (MECH79; off, its damage 100% less:
    models.compound_ignite_mods)."""
    if stats.get("_main_skill") != INCINERATE:
        return 0.0, ""
    share, _, how = stats.get("_incinerate_fuel") or (0.0, 0.0, "fuel not read")
    return _box(share, f"Incinerate channelled {share:.0%} of the fight: {how}")


def _covered(uses: float, chance: float, duration: float) -> float:
    """Share of the fight a ground made with `chance` per use, at `uses` per second, lasting `duration`, is down."""
    n = uses * duration
    if chance <= 0 or n <= 0:
        return 0.0
    if chance >= 1:
        return min(1.0, n)
    return 1.0 - (1.0 - chance) ** n if n >= 1 else n * chance


def _boss_metres(stats: dict) -> float:
    """The distance the build fights the boss from: the search's choice, else the main skill's natural one."""
    chosen = stats.get("_distance_choice")
    return chosen[0] if chosen else natural_distance(stats.get("_main_types") or set(), stats.get("_main_skill") or "")


def shocked_ground(stats: dict, texts: list[str]) -> tuple[float, str]:
    """(share of the fight the boss stands in your Shocked Ground, how). Each ground is taken to be made at the boss and
    the boss to stay in it for its duration (LOW: bosses move, and the ground's radius isn't read). Sources combine as
    independent. Charged Mark's ground counts at the supported Mark's Activations (MECH61). Labelled, not counted: ground
    from a skill other than the main one. The main skill's ground carries the build's duration modifiers (MECH68,
    Conditions.ground_ms); Shocking Leap's takes only Persistent Ground's line and the "chance on Shocking" ground stays at its base (whether
    other duration lines lengthen them: UNVERIFIED)."""
    uses = number(stats.get("Speed"))
    hit = min(number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
    parts: list[str] = []
    notes: list[str] = []
    off = 1.0
    radii: list[tuple[float, float]] = []  # (share, radius m) per counted source, for "you on it" (_on_shocked_ground)
    if skill := stats.get("_sg_skill"):
        name, ms = skill
        if ms > 0:
            feed = stats.get("_sg_feed") or {}
            how = feed.get("how") or "no rate read for the uses that make it: 0"
            radius = SHOCKED_GROUND_RADII.get(name, SHOCKED_GROUND_RADIUS)
            if SHOCKED_GROUND_GATED.get(name) == "cull":  # (made only in the last t of the boss's pool)
                up = _covered(uses, 1.0, ms / 1000) * min(1.0, number(feed.get("share")))
                parts.append(f"{name}'s ground only when it explodes ({how}; {ms / 1000:g} s, {radius:g} m): {up:.0%}")
            elif name in SHOCKED_GROUND_GATED:
                rate = min(uses, number(feed.get("rate")))
                up = _covered(rate, 1.0, ms / 1000)
                parts.append(f"{name}'s ground from {rate:.2f} of its {uses:.2f} uses/s ({how}; {ms / 1000:g} s, "
                             f"{radius:g} m): {up:.0%}")
            else:
                up = _covered(uses, 1.0, ms / 1000)
                where = "; at the end of a Stag's full path, taken to be at the boss, LOW" if name == "Vivid Stampede" else ""
                parts.append(f"{name}'s own ground on each use ({ms / 1000:g} s, {uses:.2f} uses/s, {radius:g} m{where}): "
                             f"{up:.0%}")
            off *= 1 - up
            radii.append((up, radius))
        else:
            notes.append(f"{name}'s ground (no duration stat in its level stats: not counted, an undercount)")
    if ms := number(stats.get("_sg_support")):
        crit = min(number(stats.get("CritChance")), 100.0) / 100.0
        up = _covered(uses * hit, crit, ms / 1000)
        parts.append(f"{SHOCKED_GROUND_SUPPORT}, on the first Critical Hit of a use ({crit:.0%} crit, {ms / 1000:g} s, "
                     f"{SHOCKED_GROUND_RADII[SHOCKED_GROUND_SUPPORT]:g} m): {up:.0%}")
        off *= 1 - up
        radii.append((up, SHOCKED_GROUND_RADII[SHOCKED_GROUND_SUPPORT]))
    for text in texts:
        if m := SHOCK_MAKES_GROUND.search(text):
            shock = min(number(stats.get("ShockChancePerHit")), 100.0) / 100.0
            up = _covered(uses * hit, shock * int(m.group(1)) / 100, SHOCKED_GROUND_DURATION)
            parts.append(f"{m.group(1)}% chance on Shocking ({shock:.0%} Shock chance per hit, {SHOCKED_GROUND_DURATION:g} s "
                         f"by default): {up:.0%}")
            off *= 1 - up
            radii.append((up, SHOCKED_GROUND_RADIUS))
        elif m := GROUND_WHILE_MOVING.search(text):
            if stats.get("_stationary") and not number(stats.get("_rolling")):
                notes.append("the trail while moving (the build stands still and doesn't roll: not counted)")
                continue
            if (metres := _boss_metres(stats)) > SHOCKED_GROUND_RADIUS:
                notes.append(f"the trail while moving (you fight {metres:g} m from the boss: not counted, running in every "
                             f"{m.group(1)} s would cost hits)")
                continue
            parts.append(f"the trail while moving ({m.group(1)} s): walk or roll through the boss's spot at least every "
                         f"{m.group(1)} s (a way to play the build; without it, the trail adds nothing): 100%")
            off = 0.0
            radii.append((1.0, SHOCKED_GROUND_RADIUS))
    if mark := stats.get("_sg_charged_mark"):  # (MECH61: was a note, not counted)
        rate, how, radius = mark
        up = _covered(rate, 1.0, CHARGED_MARK_SECONDS)
        if up > 0:
            parts.append(f"{CHARGED_MARK}'s ground on each Activation of the Mark it supports ({how}; "
                         f"{CHARGED_MARK_SECONDS:g} s, {radius:g} m): {up:.0%}")
            off *= 1 - up
            radii.append((up, radius))
        else:
            notes.append(f"{CHARGED_MARK}'s ground ({how}: 0)")
    if storm := stats.get("_sg_death_storm"):  # (MECH65: counted; MECH61 had it as a note, an undercount)
        rate, chance, how = storm
        up = _covered(rate, chance, models.DEATH_STORM_SECONDS)
        if up > 0:
            parts.append(f"Death Storm's ground around the fallen Skeletons it strikes at the boss ({how}; "
                         f"{models.DEATH_STORM_SECONDS:g} s, {models.DEATH_STORM_RADIUS:g} m): {up:.0%}")
            off *= 1 - up
            radii.append((up, models.DEATH_STORM_RADIUS))
        else:
            notes.append(f"Death Storm's ground ({how}: 0)")
    stats["_sg_radii"] = radii
    if not parts:
        return 0.0, "; ".join(notes)
    return 1.0 - off, "; ".join(parts + notes) + "; each ground taken to be at the boss for its full duration (LOW)"


# Jagged Ground (MECH51; RESEARCH5's spike-pit.md, game files HIGH; FIX_QUEUE RESEARCH12 / AUDIT10). Its ledger condition
# switched on for any skill or gem whose name or description said "Jagged Ground": Shockwave Totem's text, Soaring
# Midnight's "Jagged Ground duration" (it makes none), Jagged Ground I with no Endurance Charge to consume, Furious Slam
# without Rage. Player sources (checked by RESEARCH5: every stat with "jagged" in its id and every stat set with
# can_create_jagged_ground; support_jagged_ground_chance_% is used only by a Rogue Exile's Leap Slam among Mods,
# PassiveSkills, GrantedEffectStatSets, GrantedEffectQualityStats and SoulCoreStats (MECH68's rescan; that says nothing
# about the mechanic, only about that stat)): Earthquake (a patch
# per use, none on an existing patch, skill_jagged_ground_base_duration_ms 4000), Stampede (a patch per footstep; its
# closing slam explodes them, whether they end then: UNVERIFIED, counted), Furious Slam (only when it spends Rage, 4 s),
# Jagged Ground I (when the supported Slam consumes an Endurance Charge, 4 s), Jagged Ground II (each Aftershock of the
# supported Slam, 4 s). Each patch is taken to be made on the boss, and the boss to stay in it (LOW, as Shocked Ground;
# RESEARCH26 R26-19 keeps this headline, ASSUMED, LOW: a "boss moving" row would come from the preset's dash rate x the
# traversal and the next Earthquake, not modelled).
JAGGED_GROUND_SECONDS = 4.0
# Its duration modifiers (MECH68; AUDIT32 A32-12, an UNDERCOUNT: fixed 4 s / 6 s before): jagged_ground_duration_+%,
# "N% increased Jagged Ground Duration" (checked: every Stats.json id with "jagged" and the five tables above: its only
# user is the Perforation notable, PassiveSkills jagged_ground25, 40%); Persistent Ground I-III's "50% increased duration
# of Ground Surfaces" on the main skill (ground_effect_duration_+%; Jagged Ground is a Ground Surface, UNVERIFIED that it
# applies, MEDIUM). Both "increased" lines add together. Skill Effect Duration isn't read: the ground's own base stat
# (skill_jagged_ground_base_duration_ms) isn't a skill effect duration (UNVERIFIED either way).
PERSISTENT_GROUND_GEMS = ("Persistent Ground I", "Persistent Ground II", "Persistent Ground III")
PERSISTENT_GROUND_INC = 50.0
JAGGED_DURATION_LINE = re.compile(r"(\d+(?:\.\d+)?)% (increased|reduced) Jagged Ground Duration", re.I)
JAGGED_GROUND_ASSUMED = 0.4  # the share the ledger's "if:JaggedGround" lines bake in (spike-pit, aggravate-bleed-jagged-ground)
FURIOUS_SLAM_RAGE = 10  # bear_slam_spend_X_rage_for_stronger_slam (RAGE_PER_USE)


def jagged_ground(main: str | None, main_types: set[str], main_gems: set[str], stats: dict, endurance_gains: float = 0.0,
                  aftershock_pct: float = 0.0, rage: float = 0.0, texts: list[str] | None = None) -> tuple[float, str]:
    """(share of the fight the boss stands in your Jagged Ground, how), from the main skill's own sources. Sources combine as
    independent; a skill other than the main one makes none here (an undercount, said in the note). `texts`: the build's
    lines, for the "N% increased Jagged Ground Duration" lines."""
    uses = number(stats.get("Speed"))
    off, parts, notes = 1.0, [], []
    inc = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
              for t in texts or [] if (m := JAGGED_DURATION_LINE.search(t)))
    persistent = any(g in main_gems for g in PERSISTENT_GROUND_GEMS)
    factor = max(0.0, 1 + (inc + (PERSISTENT_GROUND_INC if persistent else 0.0)) / 100)
    seconds = JAGGED_GROUND_SECONDS * factor
    if factor != 1:
        notes.append(f"each patch lasts x{factor:g} ({inc:+g}% Jagged Ground Duration"
                     + (", +50% Persistent Ground's Ground Surfaces, MEDIUM" if persistent else "") + ")")
    slam = "Slam" in main_types and "CannotCreateJaggedGround" not in main_types
    if main == "Earthquake":
        up = min(1.0, uses * seconds)
        parts.append(f"Earthquake's patch on each use ({uses:.2f}/s; none on an existing patch, {seconds:g} s): "
                     f"{up:.0%}")
        off *= 1 - up
    if main == "Stampede":
        up = min(1.0, uses * 6.0 * factor)
        parts.append(f"Stampede's footstep patches ({6.0 * factor:g} s; whether its closing slam ends them: UNVERIFIED, "
                     f"counted): {up:.0%}")
        off *= 1 - up
    if main == "Furious Slam":
        if rage >= FURIOUS_SLAM_RAGE:
            up = min(1.0, uses * seconds)
            parts.append(f"Furious Slam spending Rage ({rage:.0f} held, {FURIOUS_SLAM_RAGE} a slam): {up:.0%}")
            off *= 1 - up
        else:
            notes.append(f"Furious Slam makes it only when it spends Rage ({rage:.0f} held: none)")
    if "Jagged Ground I" in main_gems and slam:
        rate = min(uses, endurance_gains)
        up = min(1.0, rate * seconds)
        if up > 0:
            parts.append(f"Jagged Ground I, a patch per Endurance Charge consumed ({endurance_gains:.2f} gained/s, "
                         f"{uses:.2f} uses/s): {up:.0%}")
            off *= 1 - up
        else:
            notes.append("Jagged Ground I consumes an Endurance Charge for each patch: the build gains none")
    if "Jagged Ground II" in main_gems and slam:
        rate = uses * min(1.0, aftershock_pct / 100)
        up = min(1.0, rate * seconds)
        if up > 0:
            parts.append(f"Jagged Ground II, a patch per Aftershock ({aftershock_pct:.0f}% chance): {up:.0%}")
            off *= 1 - up
        else:
            notes.append("Jagged Ground II makes it from Aftershocks: the main slam has no Aftershock chance")
    if not parts:
        return 0.0, "; ".join(notes) or "no source of Jagged Ground on the main skill"
    return 1.0 - off, "; ".join(parts + notes) + "; each patch taken to be at the boss for its full duration (LOW)"


def shock_share(stats: dict) -> float:
    """The boss Shocked: by hits (ailment_uptime) or by standing in your Shocked Ground (or a Whirlwind carrying its
    debuff: _ground_shocked), taken as independent."""
    ground = _ground_shocked(stats)[0]
    if "Shock" in (stats.get("_consumes") or {}):  # (consumed: the ground reapplies it after the 1 s immunity too)
        chance = min(number(stats.get("ShockChancePerHit")) / 100.0, 1.0)
        return consumed_share(number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0, chance, ground,
                              kept=consume_reapply(stats, "Shock"))  # (MECH85: Endless Circuit)
    if (stats.get("_side_consumes") or {}).get("Shock"):  # (consumed beside: on the combined share, the ground reapplying)
        hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
        chance = min(number(stats.get("ShockChancePerHit")) / 100.0, 1.0)
        duration = number(stats.get("ShockDuration")) or AILMENT_DURATION["Shock"]
        by_hits = 1.0 - (1.0 - chance) ** (hits * duration) if hits > 0 and chance > 0 else 0.0
        return side_consumed(1.0 - (1.0 - by_hits) * (1.0 - ground), stats, "Shock", hits, chance, ground)
    return 1.0 - (1.0 - ailment_uptime(stats, "Shock")) * (1.0 - ground)


# Two Shocks / two Chills at once (MECH83; RESEARCH28 R28-11 CD-12 / CD-13, the manager 16:11: an UNDERCOUNT of up to
# x2 Shock, and the second Chill). Strike Twice (passive; can_apply_additional_shock: "Targets can be affected by two of
# your Shocks at the same time") and Heavy Snows / the hand-wrap unique (can_apply_additional_chill, "... two of your
# Chills ..."): PoB parses them (ModParser 3376-3377: ShockCanStack / ChillCanStack, Stacks max 2; 3337: "You can apply
# up to N Shocks to each enemy") but counts the stacks from its ShockStacks / ChillStacks boxes, "count" boxes whose
# placeholder 1 applies when unset (ConfigOptions 1801 / 1841), and nothing wrote them: one Shock's 20% counted, however
# often the build Shocks. Written here from the build's own rate: the main skill's applications (hits x the per-hit chance,
# as ailment_uptime / chill_share read it) over the ailment's duration, taken as random in time (Poisson, mean m): the
# share of the Shocked time with two or more live is (1 - e^-m - m e^-m) / (1 - e^-m), the lower reading against evenly
# spaced hits (MEDIUM); the box is 2 for that share and 1 the rest (PoB floors a Multiplier: the uptime weighting, with
# the Shocked box's own share). With a cap above 2, the mean E[min(N, k) | N >= 1] is spread the same way. Not counted
# (the lower reading): other groups' and Shocked Ground's Shocks as the second one; a consumed Shock stays at 1. PoB's
# Chill stacking adds its stacked slow only in its breakdown pass (CalcPerform 3281-3287), but the "Is the enemy
# Chilled?" box adds one Chill row of its own, so the boss was slowed by one Chill, not none (MECH84's probe POE2T4043
# refuted MECH83's "no boss slow at all"); the second Chill's Slow is added in the bridge's boss-slow wrapper (MECH84).
STACKS_TWO = re.compile(r"^Targets can be affected by two of your (Shocks|Chills) at the same time$", re.I)
STACKS_UP_TO = re.compile(r"^You can apply up to (\d+) (Shocks|Chills) to each Enemy$", re.I)


def ailment_stack_cap(lines: list[str], kind: str) -> int:
    """The most of `kind` ("Shock" / "Chill") your lines let the boss carry at once (1 without a line)."""
    cap = 1
    for text in (t.strip() for t in lines):
        if (m := STACKS_TWO.match(text)) and m.group(1).lower().startswith(kind.lower()):
            cap = max(cap, 2)
        elif (m := STACKS_UP_TO.match(text)) and m.group(2).lower().startswith(kind.lower()):
            cap = max(cap, int(m.group(1)))
    return cap


def stacked_mean(m: float, cap: int) -> float:
    """E[min(N, cap) | N >= 1] for N Poisson with mean m (1 when nothing lands)."""
    if m <= 0 or cap <= 1:
        return 1.0
    p0, total, pk = math.exp(-m), 0.0, math.exp(-m)
    for k in range(1, cap):
        pk *= m / k
        total += k * pk
    rest = 1.0 - p0 - sum(math.exp(-m) * m ** k / math.factorial(k) for k in range(1, cap))
    return (total + cap * max(rest, 0.0)) / (1.0 - p0)


def ailment_stacks(kind: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def stacks(lines: list[str], stats: dict) -> tuple[object, str]:
        cap = ailment_stack_cap(lines, kind)
        if cap <= 1:
            return 0.0, ""
        hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
        if kind == "Shock":
            chance = min(number(stats.get("ShockChancePerHit")) / 100.0, 1.0)
            duration = number(stats.get("ShockDuration")) or AILMENT_DURATION["Shock"]
        else:
            chance = min(number(stats.get("ChillChanceOnHit")) / 100.0, 1.0)
            if chance > 0 and number(stats.get("EnemyAilmentThreshold")) > 0:  # (chill_share's 30% floor)
                normal, critical, _ = chill_effect(stats)
                crit = min(max(number(stats.get("CritChance")), 0.0), 100.0) / 100.0
                chance = (1.0 - crit) * (normal >= CHILL_FLOOR) + crit * (critical >= CHILL_FLOOR)
            duration = number(stats.get("ChillDuration")) or CHILL_SECONDS
        if kind in (stats.get("_consumes") or {}):
            return 1.0, f"the main skill consumes its {kind}s: one at a time"
        m = hits * chance * duration
        mean = stacked_mean(m, cap)
        share = (mean - 1.0) / (cap - 1)
        basis = (f"{cap} {kind}s at once (your line): {hits:.2f} hits/s x {chance:.0%} x {duration:g} s = {m:.2f} live on "
                 f"average (random in time, MEDIUM): {mean:.2f} while {kind}ed, so {cap} for {share:.0%} of it, else 1 "
                 "(other skills' and grounds' as the extra one not counted)")
        if share >= FULL_UPTIME:
            return float(cap), basis
        if share >= SWITCH_ON:
            return float(cap), basis + f" ({shared(share)})", share, 1
        return 1.0, basis
    return stacks


def _ground_shock_effect(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Effect of Shock" box at the ground's base Shock: PoB applies the strongest of it and the hits' own, as the game
    does. Set only with a ground; PoB gates it on the Shocked box, which carries the combined share (shock_share)."""
    share, how = _ground_shocked(stats)
    if share < MIN_UPTIME:
        return 0.0, ""
    value, basis = ground_shock_magnitude(stats.get("_sg_magnitude"))
    return value, f"the boss under your Shocked Ground's Shock {share:.0%} of the fight: {how}; its Shock {basis}"


def _ground_shocked(stats: dict) -> tuple[float, str]:
    """(share of the fight the boss carries Shocked Ground's Shock, how): standing in your ground, or inside a Whirlwind
    a ring makes count as boosted by it (whirlwind_shock, MECH80), taken as independent."""
    ground, ground_how = stats.get("_shocked_ground") or (0.0, "")
    wind, wind_how = stats.get("_whirlwind_shock") or (0.0, "")
    share = 1.0 - (1.0 - min(1.0, max(0.0, ground))) * (1.0 - min(1.0, max(0.0, wind)))
    return share, "; ".join(filter(None, [ground_how, wind_how]))


def ground_shock_magnitude(mag: dict | None) -> tuple[float, str]:
    """Your Shocked Ground's Shock (the bridge's shockMagnitude; MECH45, FIX_QUEUE; the user expected it, 11 Nov 2026):
    the base 20% scaled by the build's Shock-magnitude modifiers, global and the main skill's own (KeywordPopups
    BuffMagnitude: magnitude modifiers "come from whoever applies it"; shocked_ground_base_magnitude_override's text
    "Shocked Ground you create causes {0}% increased Damage taken" is the creator's; PoB scales its own ground skills'
    Shock by EnemyShockMagnitude), capped at the Shock maximum. Not hit-only lines (Overcharge, crit-only), and not Shock
    duration (how long it lingers once the boss leaves the ground is unknown). MEDIUM: two sources and the user's
    expectation, not tested in game (GroundEffectTypes / BuffDefinitions would settle it)."""
    base = SHOCKED_GROUND_MAGNITUDE
    if not mag:
        return base, f"taken at the base {base}% (the build's Shock-magnitude modifiers weren't read)"
    raw = base * (1 + number(mag.get("inc")) / 100) * number(mag.get("more", 1)) * number(mag.get("enemy", 1))
    top = number(mag.get("max")) or 100.0
    value = max(0.0, min(raw, top))
    left = " (Overcharge left out: it strengthens Shocks from hits)" if mag.get("left") else ""
    return round(value, 1), (f"{value:g}%: the base {base}% scaled by your Shock-magnitude modifiers ({number(mag.get('inc')):+g}% "
                             f"increased, x{number(mag.get('more', 1)) * number(mag.get('enemy', 1)):.2f} more and on the boss)"
                             f"{', capped at ' + format(top, 'g') + '%' if raw > top else ''}{left}; crit-only lines and Shock "
                             f"duration not counted (MEDIUM: two sources and the user's expectation, not tested in game)")


def _on_shocked_ground(lines: list[str], stats: dict) -> tuple[object, str]:
    """You on your own Shocked Ground (Stormwalker: "Gain 15% of Damage as Extra Lightning Damage while on Shocked Ground"):
    the share of the grounds whose radius (SHOCKED_GROUND_RADII, else SHOCKED_GROUND_RADIUS) reaches where you stand. Your own ground counts for "on Shocked
    Ground" (the user, 11 Nov 2026: MEDIUM, not tested in game). Enemy-made ground isn't counted on a lone boss."""
    if not any(ON_SHOCKED_GROUND.search(t) for t in lines):
        return 0.0, ""
    share, how = stats.get("_shocked_ground") or (0.0, "")
    if share < MIN_UPTIME:
        return 0.0, how or "no Shocked Ground of your own (enemy-made ground not counted on a lone boss)"
    metres = _boss_metres(stats)
    if (radii := stats.get("_sg_radii")) is not None:  # (MECH58: each source's own radius)
        off = 1.0
        for up, radius in radii:
            off *= 1.0 - up if radius >= metres else 1.0
        if 1.0 - off < share:
            share = 1.0 - off
            how = f"{how}; only the grounds that reach {metres:g} m from the boss count for you"
        if share < MIN_UPTIME:
            return 0.0, f"you stand {metres:g} m from the boss, outside your Shocked Ground (each source's radius)"
    elif metres > SHOCKED_GROUND_RADIUS:
        return 0.0, f"you stand {metres:g} m from the boss, outside your Shocked Ground ({SHOCKED_GROUND_RADIUS:g} m assumed)"
    return _box(share, f"in your own Shocked Ground at the boss {share:.0%} of the fight ({shared(share)}): {how}; your own "
                       f"ground counts (the user, 11 Nov 2026; MEDIUM, not tested in game)")


# You on your own Chilled / Ignited Ground (MECH85; the catalog's damaging-ailments entry, an UNDERCOUNT: Frostwalker's
# "Gain 15% of Damage as Extra Cold Damage while on Chilled Ground" and Flamewalker's "... Extra Fire Damage while on
# Ignited Ground", tree.json, parse to PoB's Condition:OnChilledGround / OnIgnitedGround, whose boxes nothing set: the
# coverage records' "parsed, but its condition can't switch on"). As _on_shocked_ground: the share of the fight the
# build's own ground is at the boss (chilled_ground / ignited_ground, the shares the boss-side lines already use), if you
# stand within its radius of the boss (Ignited Ground 2 m, KeywordPopups IgnitedGround; Chilled Ground has no radius in
# its definition: Ignited Ground's 2 m taken, LOW), your own ground counting for "on <kind> Ground" as the user ruled for
# Shocked Ground (11 Nov 2026; MEDIUM, not tested in game). PoB's boxes set only the OnXGround condition (ConfigOptions
# 1061-1066; their implyCond is the Configuration tab's display only, ConfigTab 221-231), so you aren't Chilled or
# Ignited by it, as your own ground doesn't affect you. Enemy-made ground isn't counted on a lone boss (as Shocked's).
ON_CHILLED_GROUND = re.compile(r"\bwhile on chilled ground\b", re.I)
ON_IGNITED_GROUND = re.compile(r"\bwhile on (?:ignited|burning) ground\b", re.I)
CHILLED_GROUND_RADIUS = IGNITED_GROUND_RADIUS  # m (LOW: ChilledGround's definition gives none)


def _on_own_ground(kind: str):
    pattern, key, radius = {"Chilled": (ON_CHILLED_GROUND, "_chilled_ground", CHILLED_GROUND_RADIUS),
                            "Ignited": (ON_IGNITED_GROUND, "_ignited_ground", IGNITED_GROUND_RADIUS)}[kind]

    def value(lines: list[str], stats: dict) -> tuple[object, str]:
        if not any(pattern.search(t) for t in lines):
            return 0.0, ""
        share, how = stats.get(key) or (0.0, "")
        share = min(1.0, max(0.0, number(share)))
        if share < MIN_UPTIME:
            return 0.0, f"no {kind} Ground of your own at the boss (enemy-made ground not counted on a lone boss)"
        metres = _boss_metres(stats)
        if metres > radius:
            return 0.0, (f"you stand {metres:g} m from the boss, outside your {kind} Ground ({radius:g} m"
                         f"{'' if kind == 'Ignited' else ', LOW'}; standing in it: on {share:.0%} of the fight)")
        return _box(share, f"in your own {kind} Ground at the boss {share:.0%} of the fight ({shared(share)}): {how}; "
                           f"your own ground counts (as the user ruled for Shocked Ground, 11 Nov 2026; MEDIUM; "
                           f"without it: off)")
    return value


# PoB's check boxes (MECH86; found while modelling conditionMoving, probe POE2T4072). The resolver writes a value
# function's (0.0, reason) as the number 0, and bridge.setConfig stores it as given; PoB's ConfigTab applies a check
# box whenever its input is truthy (ConfigTab.lua 1179-1181: `if input[varData.var] then varData.apply(true, ...)`), and
# 0 is truthy in Lua, so every such "0 with a reason" switched its box ON: an OVERCOUNT for every check box a value
# function rated 0 with a note (and an overcount of drawbacks' cost the other way). PoB's count options apply only when
# not 0 (1184), so they were fine; its list options use 0 as a real value ("None", Gamblesprint 0%), so only the check
# boxes, read from ConfigOptions.lua, are turned off by a 0.
_CHECK_VARS: frozenset[str] | None = None


def pob_check_vars() -> frozenset[str]:
    """The var names of PoB's type "check" Configuration options (vendor ConfigOptions.lua; read once)."""
    global _CHECK_VARS
    if _CHECK_VARS is None:
        path = pathlib.Path(__file__).resolve().parents[2] / "vendor" / "PathOfBuilding-PoE2" / "src" / "Modules" / "ConfigOptions.lua"
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        _CHECK_VARS = frozenset(re.findall(r'\bvar = "(\w+)", type = "check"', text))
    return _CHECK_VARS


FREEZE_SHARE_INPUT = "mechFreezeShare"  # (the bridge's boss action speed: Freeze as a stop for this share; MECH55)


def _immobilised(ailment: str, name: str) -> Callable[[list[str], dict], tuple[object, str]]:
    """The Frozen / Electrocuted box at the share of the fight the boss carries it (models.immobilised_share; MECH38:
    before, nothing set it, so "Enemies you Electrocute have X% increased Damage taken", the per-Elemental-Ailment-type
    damage taken and every "against Frozen / Electrocuted" line counted 0). PoB's box also sets Immobilised."""
    def share(lines: list[str], stats: dict) -> tuple[object, str]:
        up, how = models.immobilised_share(stats, ailment)
        if not how:
            return 0.0, ""
        return _box(up, f"{name} {up:.0%} of the fight ({shared(up)}): {how}")
    return share


def _heavy_stunned(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Heavy Stunned box at the share of the fight the boss is Heavy Stunned (models.heavy_stunned_share: the main
    skill's Stun buildup against the boss's own threshold, each stun the boss's own duration; MECHD2). Other skills'
    buildup isn't counted (an undercount)."""
    up, how = models.heavy_stunned_share(stats)
    if up <= 0:
        return 0.0, ""
    return _box(up, f"Heavy Stunned {up:.0%} of the fight ({shared(up)}): {how}")


# Heavy Contact, "Hits that Heavy Stun Enemies have Culling Strike" (MECH59; plans/kw-culling-strike.md item E, an
# UNDERCOUNT before): PoB parses it as CanCull under Condition:AlwaysHeavyStunning (ModParser 5470), set only by the
# config box conditionHitsAlwaysHeavyStun ("Do your hits always Heavy Stun?", ConfigOptions 1622), which nothing set;
# no other PoB mod reads that condition (checked: grep AlwaysHeavyStunning over PoB's src: ConfigOptions, ModParser
# 5470 and its one ModCache line). The box goes on when the build has the line and Heavy Stuns the boss
# (models.heavy_stun_rate > 0); the Cull is then priced at the next Heavy Stun, not the next hit (cull_window_scale's
# `heavy`, bridge.cullSplit's heavyOnly), so switching the box on can't count every hit as culling.
HEAVY_CONTACT_LINE = re.compile(r"hits that heavy stun enemies have culling strike", re.I)


def _heavy_contact(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(HEAVY_CONTACT_LINE.search(t) for t in lines):
        return 0.0, ""
    rate, how = models.heavy_stun_rate(stats)
    if rate <= 0:
        return 0.0, f"Hits that Heavy Stun have Culling Strike, but the main skill doesn't Heavy Stun the boss: {how}"
    return True, (f"Culling Strike on the boss's Heavy Stuns, {rate:.3f}/s ({how}); priced at the wait for the next "
                  "Heavy Stun under the threshold (the Culling Strike note)")


# "if you have consumed an Elemental Infusion Recently" (PoB Condition:InfusionConsumedRecently; Empowering Infusions 35%,
# Infused Spell Damage 15%; MECH49: before, nothing set it, so both counted 0). The skills that consume Infusions, by the
# elements they take (game files 26476662, GrantedEffectStatSets holding active_skill_consumes_(a_)<element>_infusion,
# player sets; checked: ImplicitStats / ConstantStats of every stat set, and GrantedEffectStatSetsPerLevel, which holds
# none of those stats; second source poe2db's skill pages for the Infused variants (UNVERIFIED, not reached this turn)).
# "Any": a Verisium Infusion (KeywordPopups VerisiumInfusion: usable as any element), which Skyfall alone of these takes.
INFUSION_CONSUMERS = {
    "Ice Nova": ("Cold",), "Frost Wall": ("Lightning",), "Firestorm": ("Fire", "Cold", "Lightning"),
    "Arc": ("Lightning",), "Ball Lightning": ("Fire",), "Incinerate": ("Fire",), "Flame Wall": ("Lightning",),
    "Comet": ("Fire",), "Frost Darts": ("Cold",), "Spark": ("Cold",), "Skyfall": (), "Fireball": ("Fire",),
    "Ember Fusillade": ("Lightning",),
}
EMBER_FUSILLADE, EMBER_MAX_STAT, EMBER_SECONDS = ("Ember Fusillade", "blazing_cluster_maximum_number_of_projectiles_allowed",
                                                 1.3)  # (MECHC29, K2-11: Conditions._fusillades)
INFUSION_CONSUMED = re.compile(r"consumed an elemental infusion recently", re.I)


# Your minions dying (MECHC7, AUDIT18 A18-1; before, "when a Minion Dies" lines counted 0: an undercount for builds that
# kill their own minions): the rate comes from the build's own sources, models.minion_deaths (Sacrifice's corpses,
# Explosive Demise, capped by revival), set as stats["_minion_deaths"] by Conditions.evidence when a line names a minion's
# death. The lines: "Gain Arcane Surge when a Minion Dies" / "Gain Onslaught for 4 seconds when a Minion Dies" (the game's
# gain_arcane_surge_for_4_seconds_on_minion_death / gain_onslaught_for_4_seconds_on_minion_death: Kulemak's Loreweave mods
# and the Kurgal / Ulaman passages; PoB gives them to your minions instead, MinionModifier) and "if one of your Minions has
# died Recently" (passive spells60, PoB's conditionMinionsDiedRecently). Not counted: the boss killing your minions,
# temporary minions expiring (whether that is "dying" is UNVERIFIED), Dark Pact, Offerings (an undercount, said in
# minion_deaths' note).
MINION_DIES = re.compile(r"\bminions? (?:dies|died|has died)\b|on minion death|minions have died", re.I)


def _minion_death_events(stats: dict) -> tuple[float, str]:
    rate, how = stats.get("_minion_deaths") or (0.0, "the build's minions weren't read")
    return max(rate, 0.0), f"your minions dying {max(rate, 0.0):.3f}/s ({how})"


# Deaths in waves (MECHC32; MECHC31's brief item 7, MECHB33's 7.5 s revive): models.minion_deaths caps the rate by
# revival, N k / (N + T k) (N Reviving minions, kills at k a second, each revives T s after the LAST death: KeywordPopups
# Reviving, "This delay is reset whenever another Reviving Minion dies"). So the deaths aren't evenly random: N of them
# 1 / k s apart, then none for T s. A buff of d s on each death (or "died Recently", 4 s) covers the wave plus d, not
# 1 - e^(-rate d): with many minions killed fast that was an OVERCOUNT (10 minions at 2 a second, 7.5 s: 68% up, not 96%),
# with one minion an undercount (the deaths are evenly spaced). The cycle's coverage, each death's chance independent,
# from Conditions.evidence's stats["_minion_waves"] = (N, T) beside "_minion_deaths" (the same skills; models.
# revive_seconds). Without it (a reader given only the rate): evenly random, as before.
def minion_wave_uptime(rate: float, count: float, revive: float, window: float, chance: float = 1.0) -> float | None:
    """Share of the fight within `window` s after a minion death that triggers (each with `chance`), for deaths at `rate`
    a second in waves of `count` with a `revive` s wait after each wave's last; None when the waves can't be read."""
    if rate <= 0 or count < 1 or revive <= 0 or window <= 0:
        return None
    room = count - revive * rate
    if room <= 1e-9:
        return None
    kills = rate * count / room  # (the build's own kills a second, from rate = N k / (N + T k))
    gap, n = 1 / kills, int(round(count))
    cycle = n * gap + revive
    deaths = [i * gap for i in range(n)]
    steps = 600
    up = 0.0
    for j in range(steps):
        t = (j + 0.5) * cycle / steps
        seen = sum(1 for d in deaths for back in (0.0, cycle, 2 * cycle, 3 * cycle) if 0 <= t + back - d < window)
        up += 1 - (1 - min(chance, 1.0)) ** seen
    return min(up / steps, 1.0)


def _minion_death_uptime(stats: dict, chance: float, duration: float) -> tuple[float, str]:
    """(uptime, how) of a buff gained for `duration` s on a minion of yours dying: over the deaths' waves
    (minion_wave_uptime) when stats["_minion_waves"] is read, else deaths taken as evenly random (LOW)."""
    events, how = _minion_death_events(stats)
    even = 1 - math.exp(-events * duration * min(chance, 1.0))
    count, revive = stats.get("_minion_waves") or (0.0, 0.0)
    wave = minion_wave_uptime(events, count, revive, duration, chance)
    if wave is None:
        return even, f"{chance:.0%} x {how}, {duration:g} s: {even:.0%} up (deaths taken as evenly random, LOW)"
    return wave, (f"{chance:.0%} x {how}, {duration:g} s: {wave:.0%} up (the deaths come in waves of {count:g}, then "
                  f"{revive:.3g} s to revive: MEDIUM; evenly random they'd give {even:.0%})")


def _minion_died_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Has a Minion Died Recently?" box at the share of the fight one of your minions died in the past 4 s
    (models.minion_deaths; evenly random, LOW). None dying: 0, said."""
    if "_minion_deaths" not in stats:
        return 0.0, ""
    rate, how = _minion_death_events(stats)
    if rate <= 0:
        return 0.0, f"no minion of yours dies against the boss: {how}"
    up = 1.0 - math.exp(-rate * RECENTLY)
    count, revive = stats.get("_minion_waves") or (0.0, 0.0)
    wave = minion_wave_uptime(rate, count, revive, RECENTLY)
    if wave is not None:  # (MECHC32: the deaths' waves, minion_wave_uptime)
        return _box(wave, f"one of your minions died in the past {RECENTLY:g} s {wave:.0%} of the fight: {how} (MEDIUM: "
                          f"in waves of {count:g}, then {revive:.3g} s to revive; evenly random would give {up:.0%})")
    return _box(up, f"one of your minions died in the past {RECENTLY:g} s {up:.0%} of the fight: {how} (LOW: deaths taken "
                    f"as evenly random)")


# A placed Banner (MECHC7, COVC8; usage.py's note: PoB applies the placed banner's buff only under BannerPlanted, which
# only its config box "Is Banner Planted?" sets and the tool never did: an UNDERCOUNT, the buff counted 0). Placing one
# costs 200 Glory (WarBannerPlayer / DefianceBannerPlayer / DreadBannerPlayer: active_skill_requires_X_glory 200, HIGH), so
# it is placed models.glory_uses times a second, and stands for its duration (base_skill_effect_duration 6 s at level 1,
# 9.8 s at 20, game files; PoB's own duration with the build's modifiers, from useRates): the box at min(1, uses x
# duration). The player is taken to stand in its 6 m area (active_skill_base_area_of_effect_radius 60) by the boss (a
# play note). With two or more banners, the lowest share (each banner's buff sits behind the one box: the lower reading).
# Valour (bannerValour) stays PoB's 0, and that is right (PATCH_CHECK K2-12, MECHC29): 0.3.0 "Banners no longer gain
# Valour (or have any other Valour related stats), but instead generate Glory" (grep_notes --history Valour: 0.3.0 the
# newest). (checked: no player or monster skill stat set in GrantedEffectStatSets / ...PerLevel holds a Valour stat; the
# four Stats ids named valour are banners_always_have_max_valour (a mod's) and three Tempered Valour stats on Refraction
# I-III, which are Armour / Deflection / Exposure stats, not Valour; the leftover Valour mods JewelBannerValourGained,
# UniqueMaximumValour1, UniqueValourAlwaysMaximum1 feed nothing a Banner reads. KeywordPopups still holds the pre-0.3.0
# Valour definition: the game text and the patch note disagree, both written here; the note and the stat sets win.)
BANNERS = ("War Banner", "Defiance Banner", "Dread Banner")
BANNER_SECONDS = 9.8  # base_skill_effect_duration at level 20 (game files), when PoB gives no duration
# (MECHC27; PATCH_CHECK K2-4, an OVERCOUNT for 2+ banners) 0.3.0: "Banners now have a global limit of 1 allowed to be
# placed, but there are ways on the Passive Tree or the Tactician Ascendancy to increase this" (grep_notes over banner /
# limit / placed: no later change to the limit; 0.3.0 also gave Whoever Pays Best its no-limit line). Raised by "+N to maximum number of placed Banners" (number_of_additional_
# banners_allowed: Reinforced Rallying) and lifted by "There is no Limit on the number of Banners you can place"
# (unlimited_banners_allowed: Whoever Pays Best) (checked: PassiveSkills and Mods over number_of_banners_allowed,
# number_of_additional_banners_allowed and unlimited_banners_allowed: those two passives; PoB ModCache leaves both lines
# unparsed). The model reads the text, so any other carrier counts too.
BANNER_MORE = re.compile(r"\+(\d+) to maximum number of placed banners", re.I)
BANNER_UNLIMITED = re.compile(r"there is no limit on the number of banners you can place", re.I)


def banner_limit(texts: list[str]) -> tuple[float, str]:
    """(banners you can have placed at once, how)."""
    if any(BANNER_UNLIMITED.search(t) for t in texts):
        return math.inf, "no limit on placed Banners"
    more = sum(int(m.group(1)) for t in texts for m in BANNER_MORE.finditer(t))
    return 1 + more, f"a limit of {1 + more} placed (0.3.0's limit of 1" + (f" +{more}" if more else "") + ")"




def _banner_planted(lines: list[str], stats: dict) -> tuple[object, str]:
    got = stats.get("_banner")
    if not got:
        return 0.0, ""
    up, how = got
    if up <= 0:
        return 0.0, how
    return _box(up, how)


def _infusion_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Infusion consumed recently?" box at the share of the fight one was consumed in the past 4 s: the build's
    consumers (INFUSION_CONSUMERS: the main skill at its casts, any other at its group's rate) take the Infusions it
    makes (Assumptions.infusion_supply), at most one per cast; consumptions taken as evenly random (1 - e^(-rate x 4 s)),
    LOW. No consumer or no supply: 0, said."""
    got = stats.get("_infusion_consumed")
    if not got:
        return 0.0, ""
    rate, how = got
    if rate <= 0:
        return 0.0, how
    up = 1.0 - math.exp(-rate * RECENTLY)
    return _box(up, f"an Infusion consumed in the past {RECENTLY:g} s {up:.0%} of the fight: {how} (LOW: consumptions "
                    f"taken as evenly random)")


# Divinity (PoB's "Do you have Divinity?" box: 50% more Elemental Damage, 20% less taken; MECH49: before, nothing set it,
# so "You gain Divinity for 10 seconds on reaching maximum Divine Charges" counted 0). The game files' Divine Charge
# sources: mods DivineChargeOnHitUnique__1_ (gain_divine_charge_on_hit_% 100, maximum_divine_charges 10) and
# GainDivinityOnMaxDivineChargeUnique__1 (10000 ms; "Lose all Divine Charges when you gain Divinity") (checked: every
# Stats id holding divine_charge / divinity_ms, used in Mods, PassiveSkills and GrantedEffectStatSets: those two mods
# only; PoB's Data/Uniques has no item carrying them, so the search can't reach it today; second source poe2db UNVERIFIED).
DIVINITY_ON_MAX = re.compile(r"gain Divinity for (\d+(?:\.\d+)?) seconds on reaching maximum Divine Charges", re.I)
DIVINE_MAX = re.compile(r"^\+?(\d+) to maximum Divine Charges", re.I)
DIVINE_ON_HIT = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance to )?gain an? Divine Charge on Hit", re.I)
DIVINE_LOSE = re.compile(r"Lose all Divine Charges when you gain Divinity", re.I)


def _unbound(lines: list[str], stats: dict) -> tuple:
    """PoB's "Are you Unbound?" box at the share of the fight Unbound Fury lets the build be Unbound
    (models.unbound_uptime; MECHC10: before, nothing set it, so the skill and every "while Unbound" line counted 0)."""
    if not any(models.UNBOUND_GRANT.match(t.strip()) for t in lines):
        return 0.0, ""
    duration, on = stats.get("_unbound") or (models.UNBOUND_DURATION, False)
    up, how, alt = models.unbound_uptime(stats, duration, on)
    if up <= 0:
        return 0.0, how
    return (*_box(up, f"Unbound {up:.0%} of the fight ({shared(up)}): {how}"), False,
            (alt, "if Unbound Avatar's cooldown waited for the buff's end (RESEARCH26 R26-75: it ticks while Unbound)"))


def _divinity(lines: list[str], stats: dict) -> tuple[object, str]:
    """The Divinity box at its share of the fight: the main skill's landed hits fill the Divine Charge maximum, Divinity
    lasts its seconds and the charges are lost; refilled within that time it never drops. Other skills' hits aren't
    counted (an undercount, LOW). Without the "Lose all" line the maximum is reached once and then held: 0."""
    durations = [float(m.group(1)) for t in lines if (m := DIVINITY_ON_MAX.search(t))]
    if not durations:
        return 0.0, ""
    maximum = sum(float(m.group(1)) for t in lines if (m := DIVINE_MAX.search(t.strip())))
    chance = sum(float(m.group(1) or 100) / 100 for t in lines if (m := DIVINE_ON_HIT.search(t.strip())))
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    if maximum <= 0 or chance * hits <= 0:
        return 0.0, (f"Divinity on reaching maximum Divine Charges, but the build "
                     + ("has no maximum Divine Charges" if maximum <= 0 else "gains no Divine Charges") + ": 0")
    if not any(DIVINE_LOSE.search(t) for t in lines):
        return 0.0, "the Divine Charge maximum is reached once and then held (no line loses them): no Divinity after that, 0"
    fill = maximum / (chance * hits)
    up = min(1.0, max(durations) / fill)
    return _box(up, f"Divinity {up:.0%} of the fight: {maximum:g} Divine Charges filled in {fill:.1f} s "
                    f"({chance * hits:.2f}/s from the main skill's landed hits), Divinity {max(durations):g} s, then lost")


PARRY_GRANT = re.compile(r"Grants Skill:[^.;]*\bParry\b", re.I)  # (a Buckler's implicit)


# "if you've successfully Parried Recently" (MECH85; the catalog's parry-blind entry, an UNDERCOUNT: Swift Interruption's
# attack and movement speed parse to PoB's Condition:ParriedRecently, whose box nothing set). On when the build's Parry
# plan parries (models.parry_plan: Parry chosen, a parry each telegraphed boss hit, one every PARRY_EVERY s, the user's
# "fair" 5 s, MEDIUM): Recently is the last 4 s (the game's usual window), so on 4 / 5 of the fight. Refutation's Parried
# comes from blocks while its buff lasts; whether that counts as "successfully Parried" is UNVERIFIED: not counted (an
# undercount, named). The plan doesn't weigh these speed lines when it decides whether to parry (an undercount where
# they would tip it).
def _parried_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    plan = stats.get("_parry_plan")
    if plan is None or not plan.how:
        return 0.0, ""
    if plan.lost <= 0 or not plan.how.startswith("Parry the boss"):
        return 0.0, ("the build doesn't parry with Parry (Refutation's blocks inflict Parried but aren't a Parry, so they don't meet "
                     "'successfully Parried Recently': RESEARCH30 R30-8 4c, MEDIUM; if they did: on)")
    up = min(1.0, RECENTLY / models.PARRY_EVERY)
    return _box(up, f"a parry every {models.PARRY_EVERY:g} s (each telegraphed boss hit, the user's fair 5 s, MEDIUM): "
                    f"Parried in the last {RECENTLY:g} s {up:.0%} of the fight ({shared(up)}; without "
                    f"parrying: off)")


# "if you have Stunned an Enemy Recently" (MECH86; the catalog's kw-stun entry, an UNDERCOUNT: Breaking Blows' "12%
# increased Area of Effect if you have Stunned an Enemy Recently" and Shockwaves' "25% increased Area of Effect if you've
# Stunned an Enemy with a Two Handed Melee Weapon Recently" (tree.json) parse to PoB's Condition:StunnedEnemyRecently
# (ModCache 1948, 3228; the second also needs a Two Handed Melee weapon, which PoB checks), whose box nothing set). A Stun
# is a Light or a Heavy one (KeywordPopups Stun: "There are two types of Stun"), and Recently is the last 4 s
# (KeywordPopups Recently). The build's own Stuns of the boss from the main skill's hits (models.stun_rate: Heavy Stuns
# from heavy_stun_rate, one every so many seconds, so on min(1, 4 x rate) of the fight; Light Stuns from light_stun_rate,
# taken as random in time, 1 - e^(-4 x rate)); the two combined as independent. Other skills' Stuns aren't read (an
# undercount, LOW). No Stuns (the 15% Light Stun floor, a bar that never fills, "Your Hits cannot Stun"): off.
STUNNED_ENEMY_RECENTLY = re.compile(r"\bstunned an enemy\b[^.]*\brecently\b", re.I)
STUN_FIELDS = (*models.IMMOBILISE_FIELDS, "Flag:CannotStun")  # (light_stun_rate reads the hit / crit averages too)


# "if you've dealt a Non-Critical Hit Recently" (MECH86; the manager's 22:55 routing of COV40's row, 13.4: PoB's
# Condition:NonCritRecently, ModParser 1947, ConfigOptions 1167's box, which nothing set: an UNDERCOUNT of e.g. "N%
# increased Critical Damage Bonus if you've dealt a Non-Critical Hit Recently"). From the main skill's hits that land
# (Speed x HitChance) and its crit chance c: the share of the fight with at least one non-critical hit in the last 4 s
# (KeywordPopups Recently), 1 - c^(4 x hits), hits taken as random in time. A main skill that always crits (c = 100%)
# never deals one: off. Other skills' hits aren't read (an undercount, LOW).
NON_CRIT_RECENTLY = re.compile(r"\bnon-critical hit recently\b", re.I)


def _non_crit_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(NON_CRIT_RECENTLY.search(t) for t in lines):
        return 0.0, ""
    crit = min(max(number(stats.get("CritChance")), 0.0), 100.0) / 100.0
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) / 100.0 if "HitChance" in stats else 1.0)
    if hits <= 0:
        return 0.0, "the main skill doesn't hit: no Non-Critical Hits"
    if crit >= 1.0:
        return 0.0, "the main skill always crits: no Non-Critical Hits (without crits it would be on)"
    up = 1.0 - crit ** (RECENTLY * hits)
    return _box(up, f"{hits:.2f} hits a second, {1 - crit:.0%} of them non-critical: one in the last {RECENTLY:g} s "
                    f"{up:.0%} of the fight ({shared(up)}; hits taken as random in time)")


# "if you've gained a Power Charge Recently" (MECH86; the manager's 22:55 routing of COV40's row, 12.0: PoB's
# Condition:GainedPowerChargeRecently, ConfigOptions 1316's box, which nothing set: an UNDERCOUNT). From the build's own
# Power Charge gains a second (the charge model's _charge_rate over every line, MECH-C's: the boss's events, crits,
# kills, uses), each gain an event (MEDIUM: whether a gain at the maximum, which only refreshes them, counts as
# "gained" is UNVERIFIED; taken to count, as the game refreshes the charges then), at random in time: the share of the
# fight with one in the last 4 s (KeywordPopups Recently), 1 - e^(-4 x gains). No source: off.
GAINED_POWER_RECENTLY = re.compile(r"\bgained a power charge recently\b", re.I)
GAINED_CHARGE_CONDITION = re.compile(r"\b(?:you've|you have|you haven't|you have not)\s+gained an? \w+ charge\b", re.I)


def _gained_power_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(GAINED_POWER_RECENTLY.search(t) for t in lines):
        return 0.0, ""
    # (MECH87) a line conditioned on a gain ("... if you've / you haven't gained a Power Charge Recently") is no source:
    # _charge_rate reads its "gained a Power Charge" as one at a guessed one per 4 s (routed to MECH-C as an overcount)
    rates = [(t, r) for t in lines
             if not GAINED_CHARGE_CONDITION.search(t) and (r := _charge_rate(t, "Power", stats)) is not None]
    gains = sum(r[0] for _, r in rates)
    if gains <= 0:
        return 0.0, "no Power Charge source a boss fight feeds (without one: off)"
    up = 1.0 - math.exp(-RECENTLY * gains)
    how = "; ".join(f"{t[:50]} ({r[1]})" for t, r in rates[:3])
    return _box(up, f"{gains:.2f} Power Charge gains a second ({how}): one in the last {RECENTLY:g} s {up:.0%} of the "
                    f"fight ({shared(up)}; gains taken as random in time, a gain at the maximum counted, MEDIUM)")


def _stunned_enemy_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    if not any(STUNNED_ENEMY_RECENTLY.search(t) for t in lines):
        return 0.0, ""
    heavy, heavy_how = models.heavy_stun_rate(stats)
    light, light_how = models.light_stun_rate(stats, "Melee" in (stats.get("_main_types") or set()))
    up = 1.0 - (1.0 - min(1.0, RECENTLY * max(heavy, 0.0))) * math.exp(-RECENTLY * max(light, 0.0))
    if up < MIN_UPTIME:
        return 0.0, (f"the main skill doesn't Stun the boss (Heavy: {heavy_how}; Light: {light_how}): off")
    return _box(up, f"the main skill Stuns the boss: Heavy {heavy:.3f}/s ({heavy_how}), Light {light:.3f}/s "
                    f"({light_how}); Stunned in the last {RECENTLY:g} s {up:.0%} of the fight ({shared(up)}; Light "
                    f"Stuns taken as random in time; without Stuns: off)")


def _parried(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Enemy has Parry Debuff" box (50% more Attack damage taken) at the share of the main skill's hits that land on
    a Parried boss (models.parry_plan; MECH39: before, nothing set it, so Parry and Refutation counted 0)."""
    plan = stats.get("_parry_plan")
    if plan is None or not plan.how:
        return 0.0, ""
    if plan.box <= 0:
        return 0.0, plan.how
    return _box(plan.box, f"Parried for {plan.box:.0%} of the main skill's hits ({shared(plan.box)}): {plan.how}")


def _corpse_consumed_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    """(MECHB38, MECHB37's draft) PoB's "Consumed a corpse Recently?" box at the share of the fight a corpse was consumed
    in the past 4 s (models.corpses_consumed, published by the assumptions as build.corpses_consumed; consumptions taken
    as evenly random, LOW-MEDIUM). None: 0, said."""
    if "_corpses_consumed" not in stats:
        return 0.0, ""
    rate, how = stats["_corpses_consumed"]
    if rate <= 0:
        return 0.0, f"no corpse consumed against the boss: {how}. Without a corpse source: 0"
    up = 1.0 - math.exp(-rate * RECENTLY)
    return _box(up, f"a corpse consumed in the past {RECENTLY:g} s {up:.0%} of the fight: {how} (consumptions taken as "
                    f"evenly random; evenly spaced they'd give {min(1.0, rate * RECENTLY):.0%}). Without a corpse "
                    "source: 0")


@dataclass(frozen=True)
class Condition:
    var: str
    label: str
    text: tuple[str, ...] = ()
    not_text: tuple[str, ...] = ()
    stat: Callable[[dict], bool] | None = None
    gem_types: tuple[str, ...] = ()  # an enabled gem of one of these skill types is evidence
    main_types: tuple[str, ...] = ()  # the main skill being one of these skill types is evidence
    value_field: str | None = None  # set the option to this stat instead of True (e.g. Rage)
    value_fn: Callable[[list[str], dict], tuple[float, str]] | None = None  # the option's value and its basis from the build's lines and stats
    fields: tuple[str, ...] = ("Speed",)  # PoB numbers a stat rule reads, quoted as its evidence
    uptime_fn: Callable[[dict], float] | None = None  # a stat rule's share of the fight (MIN_UPTIME, Search.weigh_uptimes)

    def patterns(self) -> tuple[list[re.Pattern], list[re.Pattern]]:
        return ([re.compile(p, re.I) for p in self.text], [re.compile(p, re.I) for p in self.not_text])


def _charge(var: str, name: str) -> Condition:
    subject = f"{name} charges?"
    return Condition(var, f"{name.capitalize()} Charges", (_gen(subject),), (_consumer(subject),))


CONDITIONS: list[Condition] = [
    _charge("usePowerCharges", "power"),
    _charge("useFrenzyCharges", "frenzy"),
    _charge("useEnduranceCharges", "endurance"),
    Condition("buffOnslaught", "Onslaught", value_fn=lambda lines, stats: onslaught_uptime(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "enemySkillTime", "EvadeChance")),
    Condition("onLeyline", "On a Leyline", value_fn=lambda lines, stats: leyline_uptime(lines, stats)),
    Condition("multiplierCurrentEnergyShield", "Current Energy Shield percentage (MECHC33)",
              value_fn=lambda lines, stats: current_es_percent(lines, stats), fields=()),
    Condition("multiplierBeenHitRecently", "Times you've been Hit Recently (MECHC34, MB-17)",
              value_fn=lambda lines, stats: hits_recently(lines, stats), fields=("EvadeChance", "enemySkillTime")),
    Condition("multiplierFragileRegrowth", "Fragile Regrowth stacks (Dustbloom; MECHC34, MB-13)",
              value_fn=fragile_regrowth, fields=("EvadeChance", "enemySkillTime")),
    Condition("TotalTotemLife", "Totem Life taken before yours (Wooden Wall; MECHC34, MD-6)",
              value_fn=totem_life_before_you, fields=("TotemLife",)),
    Condition("conditionShapeshiftToAnimal", "Shapeshifted to an Animal form Recently (MECHC34, MB-8)",
              value_fn=shifted_recently(SHIFTED_ANIMAL_RECENTLY, "an Animal"), fields=("Speed",)),
    Condition("conditionShapeshiftToHuman", "Shapeshifted to Human form Recently (MECHC34, MB-8)",
              value_fn=shifted_recently(SHIFTED_HUMAN_RECENTLY, "Human"), fields=("Speed",)),
    Condition("conditionFullEnergyShield", "On Full Energy Shield", value_fn=lambda lines, stats: full_es_uptime(lines, stats),
              fields=()),
    Condition("conditionFullLife", "On Full Life", value_fn=lambda lines, stats: full_life_uptime(lines, stats), fields=()),
    Condition("buffUnholyMight", "Unholy Might (Kurgal's Leash)", value_fn=lambda lines, stats: unholy_might_uptime(lines, stats),
              fields=()),
    Condition("minionbuffUnholyMight", "Minions' Unholy Might (Kurgal's Leash)",
              value_fn=lambda lines, stats: unholy_might_uptime(lines, stats), fields=()),
    Condition("inDemonForm", "In Demon Form", value_fn=lambda lines, stats: demon_form_uptime(lines, stats), fields=()),
    Condition("scavengedPlatingStacks", "Scavenged Plating stacks",
              value_fn=lambda lines, stats: scavenged_plating_stacks(lines, stats), fields=()),
    Condition("windDancerStacks", "Wind Dancer stages", value_fn=lambda lines, stats: wind_dancer_stages(lines, stats),
              fields=()),
    Condition("sigilOfPowerStages", "Sigil of Power stages", value_fn=lambda lines, stats: sigil_stages(lines, stats),
              fields=()),
    Condition("configResonanceCount", "Trinity's Affinity", value_fn=lambda lines, stats: trinity_count(lines, stats),
              fields=()),
    Condition("conditionDodgeRolledRecently", "Dodge Rolled recently",
              value_fn=lambda lines, stats: dodge_rolled_recently(lines, stats), fields=()),
    Condition("buffArcaneSurge", "Arcane Surge", value_fn=lambda lines, stats: arcane_surge_uptime(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "Mana", "ManaPerSecondCost", "Devotion")),
    Condition("multiplierRage", "Rage held", value_fn=lambda lines, stats: rage_held(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "MaximumRage", "enemySkillTime", "EvadeChance")),
    Condition("conditionLeeching", "Leeching", value_fn=leeching_uptime(None), fields=()),
    Condition("conditionLeechingLife", "Leeching Life", value_fn=leeching_uptime("Life"), fields=()),
    Condition("conditionLeechingMana", "Leeching Mana", value_fn=leeching_uptime("Mana"), fields=()),
    Condition("conditionLeechingEnergyShield", "Leeching Energy Shield", value_fn=leeching_uptime("EnergyShield"),
              fields=()),
    Condition("conditionCritRecently", "Crit recently",
              stat=lambda s: _uptime({"CritRecently": s.get("CritChance"), "Speed": s.get("Speed")}, "CritRecently"),
              fields=("CritChance", "Speed")),
    Condition("conditionSkillCritRecently", "Skills crit recently",
              stat=lambda s: _uptime({"CritRecently": s.get("CritChance"), "Speed": s.get("Speed")}, "CritRecently"),
              fields=("CritChance", "Speed")),
    Condition("conditionNonCritRecently", "Dealt a Non-Critical Hit recently (MECH86: the main skill's hits)",
              value_fn=_non_crit_recently, fields=("CritChance", "Speed", "HitChance")),
    Condition("conditionEnemyShocked", "Shocked", stat=lambda s: shock_share(s) >= MIN_UPTIME, uptime_fn=shock_share,
              fields=("ShockChancePerHit", "Speed", "HitChance", "ShockDuration", "CritChance")),
    Condition("conditionShockEffect", "Shock from your Shocked Ground", value_fn=_ground_shock_effect, fields=()),
    Condition("conditionOnShockedGround", "You on your Shocked Ground", value_fn=_on_shocked_ground, fields=()),
    Condition("conditionOnChilledGround", "You on your Chilled Ground (MECH85: Frostwalker)",
              value_fn=_on_own_ground("Chilled"), fields=()),
    Condition("conditionOnIgnitedGround", "You on your Ignited Ground (MECH85: Flamewalker)",
              value_fn=_on_own_ground("Ignited"), fields=()),
    Condition("conditionEnemyIgnited", "Ignited", stat=_ailment_up("Ignite"), uptime_fn=_ailment_share("Ignite"),
              fields=("IgniteChancePerHit", "Speed", "HitChance", "IgniteDuration")),
    Condition("conditionEnemyPoisoned", "Poisoned", stat=_ailment_up("Poison"), uptime_fn=_ailment_share("Poison"),
              fields=("PoisonChancePerHit", "Speed", "HitChance", "PoisonDuration")),
    Condition("conditionEnemyBleeding", "Bleeding", stat=_ailment_up("Bleed"), uptime_fn=_ailment_share("Bleed"),
              fields=("BleedChancePerHit", "Speed", "HitChance", "BleedDuration")),
    Condition("conditionEnemyChilled", "Chilled", stat=lambda s: chill_share(s) >= MIN_UPTIME, uptime_fn=chill_share,
              fields=CHILL_FIELDS),
    Condition("parryActive", "Parried", value_fn=_parried, fields=("enemySkillTime", "Ward")),
    Condition("conditionParriedRecently", "Parried recently (MECH85: the build's Parry plan)",
              value_fn=_parried_recently, fields=("enemySkillTime", "Ward")),
    Condition("conditionStunnedEnemyRecently", "Stunned the boss recently (MECH86: the main skill's Heavy and Light Stuns)",
              value_fn=_stunned_enemy_recently, fields=STUN_FIELDS),
    Condition("clashCondition", "The boss's Life % above yours (Clash)", value_fn=_clash,
              fields=("Life", "LifeUnreserved")),  # (MECH76)
    Condition("arcLightningInfused", "Arc Lightning-Infused", value_fn=_arc_infused),  # (MECH77, MA-1)
    Condition("conditionAilmentConsumed", "Consumed an Ailment (Drain Ailments)", value_fn=_drain_ailments,
              fields=("Speed", "HitChance", *models.IMMOBILISE_FIELDS)),  # (MECH77, MA-10)
    *(Condition(f"{var}{element}", f"{skill} gained {element} (from your {element} ground in its path)",
                value_fn=_element_gain(var, element), fields=())
      for skill, var in ((EYE_OF_WINTER, "eyeOfWinter"), (TWISTER, "twister"),
                         ("Whirling Slash / Whirlwind Lance", "bridgeConditionWhirlwindBuff"))
      for element in GROUND_ELEMENTS),  # (MECH78, MA-2 / MA-6 / MA-7)
    Condition("multiplierNearbyEnemies", "Enemies in Close Range (one boss)", value_fn=_nearby_enemies,
              fields=()),  # (MECH78, MB-16)
    Condition("multiplierPoisonAppliedRecently", "Poisons inflicted Recently", value_fn=_poisons_recently,
              fields=("Speed", "HitChance", "PoisonChancePerHit")),  # (MECH78, MB-12)
    Condition(OPEN_WEAKNESS_VARS[0], "The boss has an Open Weakness (Reveal Weaknesses)", value_fn=_open_weakness(False),
              fields=()),  # (MECH78, MC-1)
    Condition(OPEN_WEAKNESS_VARS[1], "An enemy with an Open Weakness in your Presence", value_fn=_open_weakness(True),
              fields=("PresenceRadius",)),  # (MECH78, MC-1)
    Condition(INCINERATE_CHANNEL_VAR, "Channelling Incinerate (its fuel: Mana spent on other skills)",
              value_fn=_incinerate_channelling, fields=()),  # (MECH79)
    Condition("conditionEnemyBurning", "Burning (Fire damage over time that isn't an Ignite)",
              value_fn=_enemy_burning, fields=("FireDot",)),  # (MECH80, MD-5)
    Condition("conditionEnemyIsolated", "Isolated (no ally within 6 m: Vorana's Siege)",
              value_fn=_enemy_isolated, fields=()),  # (MECH81, MD-2)
    Condition("bridgeConditionColdUnravel", "Unravelling Cold (Chaos damage also builds Freeze)",
              value_fn=_unravelling("Cold"), fields=()),  # (MECH81, K1-1)
    Condition("bridgeConditionFireUnravel", "Unravelling Fire (Chaos damage also builds Ignite)",
              value_fn=_unravelling("Fire"), fields=()),  # (MECH81, K1-1)
    Condition("bridgeConditionLightningUnravel", "Unravelling Lightning (Chaos damage also builds Shock)",
              value_fn=_unravelling("Lightning"), fields=()),  # (MECH81, K1-1)
    Condition("multiplierCurseExpiredOnEnemy", "A curse of yours past its threshold of Curse Duration",
              value_fn=_curse_expired, fields=()),  # (MECH78, MD-8)
    *(Condition(var, f"You {PAST_TENSE[APPLIED_RECENTLY[var]].lower()} an enemy Recently",
                value_fn=_applied_recently(var), fields=APPLIED_RECENTLY_FIELDS) for var in APPLIED_RECENTLY),  # (MECH76)
    Condition("conditionEnemyFrozen", "Frozen", value_fn=_immobilised("Freeze", "Frozen"),
              fields=(*models.IMMOBILISE_FIELDS, "FreezeDurationMod")),
    Condition("conditionEnemyElectrocuted", "Electrocuted", value_fn=_immobilised("Electrocute", "Electrocuted"),
              fields=models.IMMOBILISE_FIELDS),
    # (MECH46: "against Pinned enemies" lines at the share the main skill's Pin buildup keeps the boss Pinned; before,
    # nothing set PoB's box, so they counted 0)
    Condition("conditionEnemyPinned", "Pinned", value_fn=_immobilised("Pin", "Pinned"), fields=models.IMMOBILISE_FIELDS),
    # (MECHD2: "against Heavy Stunned enemies" lines at the share the main skill's Stun buildup keeps the boss Heavy
    # Stunned, models.heavy_stunned_share; before, nothing set PoB's box, so they counted 0)
    Condition("conditionEnemyHeavyStunned", "Heavy Stunned", value_fn=_heavy_stunned, fields=models.IMMOBILISE_FIELDS),
    Condition("multiplierIncisionStackCount", "Incision stacks on the boss", value_fn=_incision_stacks,
              fields=("Speed", "HitChance", "BleedChancePerHit")),
    Condition("incisionConsumedRecently", "Incisions consumed recently", value_fn=_incision_consumed,
              fields=("Speed", "HitChance", "BleedChancePerHit")),
    Condition("conditionEnemyFireExposure", "Fire Exposure", value_fn=_exposure_uptime("Fire"),
              fields=("Speed", "HitChance", "CritChance", "ShockChancePerHit", "IgniteChancePerHit")),
    Condition("conditionEnemyColdExposure", "Cold Exposure", value_fn=_exposure_uptime("Cold"),
              fields=("Speed", "HitChance", "CritChance", "ShockChancePerHit", "IgniteChancePerHit")),
    Condition("conditionEnemyLightningExposure", "Lightning Exposure", value_fn=_exposure_uptime("Lightning"),
              fields=("Speed", "HitChance", "CritChance", "ShockChancePerHit", "IgniteChancePerHit")),
    Condition("conditionMinionsDiedRecently", "A minion of yours died recently", value_fn=_minion_died_recently, fields=()),
    Condition("conditionConsumedCorpseRecently", "Consumed a corpse recently (MECHB38)",
              value_fn=_corpse_consumed_recently, fields=()),
    Condition("bannerPlanted", "Banner placed (its share of the fight from Glory)", value_fn=_banner_planted, fields=()),
    Condition("conditionInfusionConsumedRecently", "Infusion consumed recently", value_fn=_infusion_recently, fields=("Speed",)),
    Condition("buffDivinity", "Divinity (on reaching maximum Divine Charges)", value_fn=_divinity, fields=("Speed", "HitChance")),
    Condition("Unbound", "Unbound (Unbound Avatar, from Unbound Fury)", value_fn=_unbound,
              fields=("Speed", "HitChance", "IgniteChancePerHit", "ShockChancePerHit", "ChillChanceOnHit",
                      *models.IMMOBILISE_FIELDS, *CHILL_FIELDS)),
    Condition("conditionEnemyArmourBroken", "Armour Broken"),  # (armour_broken_uptime, Conditions.evidence)
    # (MECH70: "against Dazed Enemies" lines at the share the main skill's Daze sources keep the boss Dazed, _dazed)
    Condition("conditionEnemyDazed", "Dazed", value_fn=_dazed,
              fields=("Speed", "HitChance", "CritChance", "PhysicalCritAverage")),
    Condition("conditionEnemyCursed", "Cursed", value_fn=lambda lines, stats: _cursed(stats),
              fields=("PresenceRadius",)),
    # Sustained in any boss fight by the build's own numbers or skills (coverage audit item f, 6 Nov 2026: lines needing these
    # counted 0 in every build because only a Configuration option sets them). Boss hit rate: PoB's boss config.
    Condition("conditionUsedSkillRecently", "Used a skill recently", stat=lambda s: True, fields=()),
    Condition("conditionBeenHitRecently", "Been hit recently (the boss's hits that land, in any 4 s)", stat=_hit_recently,
              fields=("EvadeChance", "EffectiveBlockChance", "enemySkillTime")),
    Condition("conditionBlockedRecently", "Blocked recently (a block in any 4 s of the boss's hits)", stat=_blocked_recently,
              fields=("EvadeChance", "EffectiveBlockChance", "enemySkillTime")),
    Condition("conditionHaveEnergyShield", "Have Energy Shield", stat=lambda s: number(s.get("EnergyShield")) > 0,
              fields=("EnergyShield",)),
    # Grinning Immolation (Infernalist): "Become Ignited when you deal a Critical Hit": with crits landing you stay Ignited,
    # so every "while Ignited" line of the build counts (its self-damage is a drain outside the checked defences)
    Condition("conditionGainedPowerChargeRecently", "Gained a Power Charge recently (MECH86: the build's gains)",
              value_fn=_gained_power_recently,
              fields=("Speed", "CritChance", "PowerChargesMax", "PowerChargesDuration", *CHARGE_EVENT_FIELDS,
                      *models.IMMOBILISE_FIELDS)),
    Condition("overridePowerCharges", "Power Charges held", value_fn=_charges_held("Power"),
              fields=("Speed", "CritChance", "PowerChargesMax", "PowerChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("overrideFrenzyCharges", "Frenzy Charges held", value_fn=_charges_held("Frenzy"),
              fields=("Speed", "CritChance", "FrenzyChargesMax", "FrenzyChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("overrideEnduranceCharges", "Endurance Charges held", value_fn=_charges_held("Endurance"),
              fields=("Speed", "EffectiveBlockChance", "EnduranceChargesMax", "EnduranceChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("multiplierWitheredStackCount", "Withered stacks on the boss", value_fn=_withered_stacks,
              fields=WITHER_FIELDS),
    # (MECH83, R28-11 CD-12 / CD-13: Strike Twice, Heavy Snows; PoB's count boxes otherwise sit at their placeholder 1)
    Condition("ShockStacks", "Shocks on the boss at once (two of your Shocks)", value_fn=ailment_stacks("Shock"),
              fields=("Speed", "HitChance", "ShockChancePerHit", "ShockDuration")),
    Condition("ChillStacks", "Chills on the boss at once (two of your Chills)", value_fn=ailment_stacks("Chill"),
              fields=("Speed", "HitChance", "ChillChanceOnHit", "ChillDuration", "CritChance", "EnemyAilmentThreshold")),
    Condition("conditionShocked", "Shocked yourself (Overcharge reflects your Shocks back to you; MECH75)",
              value_fn=_self_shocked, fields=("Speed", "HitChance", "ShockChancePerHit", "ShockAvoidChance")),
    Condition("conditionFullMana", "On Full Mana (MECHC37: the main skill's costs against Mana regeneration)",
              value_fn=_full_mana, fields=FULL_MANA_FIELDS),
    Condition("conditionIgnited", "Ignited yourself (Grinning Immolation, on your critical hits; the boss's Fire hits; "
              "Self Immolation's reflected Ignites)",
              value_fn=_self_ignited,
              fields=("CritChance", "Speed", "HitChance", "IgniteAvoidChance", "SelfIgniteDuration", "AilmentThreshold",
                      "Life", "FireTakenHit", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
                      "IgniteChancePerHit", "IgniteDuration")),
    Condition("conditionHitsAlwaysHeavyStun", "Heavy Contact: Culling Strike on Heavy Stuns (MECH59)",
              value_fn=_heavy_contact, fields=models.IMMOBILISE_FIELDS),
    Condition(PRESENCE_SECONDS_VAR, "Seconds the boss has been in your Presence (the fight's average, MECH59)",
              value_fn=lambda lines, stats: stats.get("_presence_seconds") or (0.0, ""), fields=()),
    Condition("enemyDistance", "Boss distance (play-style assumption; 10 units a metre)", value_fn=_boss_distance,
              fields=("PresenceRadius",)),
    Condition("conditionStationary", "Standing still (play-style choice)", value_fn=_standing_still, fields=()),
    Condition("conditionMoving", "Moving while using skills (play-style choice; MECH86)", value_fn=_moving, fields=()),
    Condition("conditionHitMeleeRecently", "Melee hit recently (a melee main skill)", main_types=("Melee",)),
    Condition("conditionAtCloseRange", "Enemy at close range (a melee main skill)", main_types=("Melee",)),
    *(Condition(var, label, value_fn=debuff_uptime(var),
                fields=("Speed", "HitChance", "CritChance", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
                        *models.IMMOBILISE_FIELDS))  # (MECH85: debuffs on Heavy Stun read heavy_stun_rate)
      for var, label in (("conditionEnemyBlinded", "Blinded"), ("conditionEnemyMaimed", "Maimed"),
                         ("conditionEnemyHindered", "Hindered"), ("conditionEnemyIntimidated", "Intimidated"),
                         ("conditionEnemyUnnerved", "Unnerved"), ("conditionEnemyCoveredInAsh", "Covered in Ash"),
                         ("conditionEnemyCoveredInFrost", "Covered in Frost"),
                         ("conditionEnemyDebilitated", "Debilitated"))),  # (MECH78, MB-15)
]
_PATTERNS = {c.var: c.patterns() for c in CONDITIONS}
# Words that make a support worth a real trial for conditions decided by PoB's numbers
TRIAL_WORDS = {
    "conditionEnemyShocked": ("shock",), "conditionEnemyIgnited": ("ignite",), "conditionEnemyPoisoned": ("poison",),
    "conditionEnemyBleeding": ("bleed",), "conditionEnemyChilled": ("chill", "cold"),
    "conditionCritRecently": ("critical",), "conditionSkillCritRecently": ("critical",),
}
# Stat sets that only happen with something the build has to supply: Explosive Spear's
# "Frenzy-Charged" explosion consumes a Frenzy Charge. PoB calculates them either way, so one is
# calculated only while the resolver has evidence for what it needs (None: a boss fight never
# sustains it, like freeze).
STAT_SET_NEEDS = [
    (re.compile(r"\bfrenzy-charged\b", re.I), "useFrenzyCharges"),
    (re.compile(r"\bpower-charged\b", re.I), "usePowerCharges"),
    (re.compile(r"\bendurance-charged\b", re.I), "useEnduranceCharges"),
    (re.compile(r"\bvs frozen\b", re.I), None),
    # An Infused cast (Ball Lightning's, Comet's, Frost Wall's, Ice Nova's, Spark's Fire-, Cold- or Lightning-Infused set)
    # consumes an Elemental Infusion of its element, which comes from collecting a Remnant. It's never a choice here:
    # Gems.choose_active_skill offers it only while the build makes that element's Infusions, at the share of casts they
    # cover (Assumptions.infusion_share); chosen by score alone, it counted every cast as Infused, an overcount
    (INFUSED_SET := re.compile(r"\b(?:fire|cold|lightning)-infused\b", re.I), None),
]


# Stat sets PoB scores as Hits that the game never Hits with (MECH79, the Ignited Ground overcount): never the calculated
# set. Incinerate's and Ball Lightning's "Ignited Ground" sets carry spell fire damage (game 26476662
# GrantedEffectStatSetsPerLevel IncinerateGroundPlayer 24-36 at level 1 / 829-1243 at 20, BallLightningIgnitedGround
# 22-33 / 744-1116) that PoB (act_int 13728, 630) scores as a Hit at the skill's use rate: probe POE2T3127, a bare
# level-20 build, Incinerate's ground 2,666 DPS of Hits vs 273 for the channel, Ball Lightning's 549 vs 166, and the
# search kept the ground set as the main damage both times. The game's ground doesn't Hit: KeywordPopups IgnitedGround
# ("repeatedly inflicts Flammability ... Igniting them when it reaches 50%") and Flammability ("Effects which do not Hit
# targets, but Ignite as if they had, such as Ignited Ground ... only Ignite once the total Flammability on the target
# reaches 50%"); patch 0.3.0 says the same, naming Incinerate (checked: both texts, grep_notes --history "Ignited
# Ground", newest 0.5.0). Both sets also need a Fire Infusion (the skills' descriptions: "Consumes a Fire Infusion if
# possible to ... create Ignited Ground"), which their label doesn't say, so the Infused gate missed them. Spear of
# Solaris's ground set (act_dex 7861) PoB scores as Ignite only (POE2T3127: 87 DPS, TotalDPS 0): left a choice. The
# ground's Ignite isn't counted yet on these two (an UNDERCOUNT until the Ignited Ground model; MECH79's report).
# The same for sets the game flags display_statset_no_hit_damage (a stat PoB never reads: no reader in Modules or
# SkillStatMap), probed in POE2T3128: Oil Grenade's and Oil Barrage's "Oil Ground" (OilGrenadeOilGroundPlayer,
# OilBarrageOilPlayer; KeywordPopups OilGround: Oil only does damage by Igniting once lit, its ground's Ignite takes
# skill_main_hand_total_attack_fire_damage_permyriad_to_add_as_unscalable_damage_to_oil_ground_ignite) PoB scores as
# Hits, Oil Grenade's 45.5 vs 22.0 for the grenade (kept as the main damage), Oil Barrage's 499 vs 211 / 349; Gas
# Arrow's "Poison Cloud" (GasArrowGasDegenPlayer, also display_skill_poisons_without_hit: it Poisons without a Hit) 93.5
# of Hits beside its 44.9 Poison. Their Ignite / Poison isn't counted while blocked (an UNDERCOUNT, labelled). Not
# probed (the probe found no skeleton for them), so left a choice: Toxic Grenade's "Poison Cloud" and Poison Burst
# Arrow's "Poison Burst", which carry the same flags (UNVERIFIED whether PoB scores them as Hits). Tornado Shot's
# "Tornado" and Vine Arrow's "Vines" score the arrow's own impact (the same Hit as their Impact set) plus their DoT:
# the whole skill, left a choice
NO_HIT_STAT_SETS = {"Ignited Ground": {"Incinerate", "Ball Lightning"}, "Oil Ground": {"Oil Grenade", "Oil Barrage"},
                    "Poison Cloud": {"Gas Arrow"}}


def stat_set_needs(label: str | None) -> list[str | None]:
    return [var for pattern, var in STAT_SET_NEEDS if label and pattern.search(label)]


# Words that limit a line to one kind of skill. Such a line is evidence only if the main skill is
# that kind ("Gain 1 Rage on Melee Hit" does nothing for a bow skill). Lines about minions are
# never evidence for the player's own conditions.
QUALIFIERS = [
    (re.compile(r"\bmelee\b", re.I), {"Melee"}),
    (re.compile(r"\bspells?\b", re.I), {"Spell"}),
    (re.compile(r"\bprojectiles?\b", re.I), {"Projectile"}),
    (re.compile(r"\battacks?\b", re.I), {"Attack"}),
    (re.compile(r"\bwarcr(?:y|ies)\b", re.I), {"Warcry"}),
    (re.compile(r"\btotems?\b", re.I), {"Totem"}),
    (re.compile(r"\bshield skills?\b", re.I), {"RequiresShield"}),
    (re.compile(r"\bminions?\b", re.I), set()),
]


@lru_cache(maxsize=None)
def _line_qualifiers(line: str) -> tuple[frozenset[str], ...]:
    """The skill-type sets a line asks for (QUALIFIERS is a module constant, so a line's answer never changes; PERF14:
    qualifies was 1-6 % of a search)."""
    return tuple(frozenset(types) for pattern, types in QUALIFIERS if pattern.search(line))


def qualifies(line: str, skill_types: set[str]) -> bool:
    return all(types & skill_types for types in _line_qualifiers(line))


def text_evidence(condition: Condition, line: str) -> bool:
    include, exclude = _PATTERNS[condition.var]
    if not any(p.search(line) for p in include):
        return False
    # A line that only pays off on the effect ("per Frenzy Charge") isn't a source of it
    stripped = line
    for pattern in exclude:
        stripped = pattern.sub(" ", stripped)
    return any(p.search(stripped) for p in include)


class Conditions:
    def __init__(self, build: Build, main_skill: str) -> None:
        self.build = build
        self.main_skill = main_skill
        self.main_types = set((build.ctx.gems.get(main_skill) or {}).get("skillTypes") or [])
        self.active: dict[str, dict] = {}  # var -> {label, value, evidence}
        self.distance: tuple[float, str] | None = None  # (metres, why) chosen by score (Search.choose_distance)
        self.parry_plan: models.ParryPlan | None = None  # the last evidence round's Parry plan (the play guide's rows)
        self.stationary_off: str | None = None  # why a build with stationary lines doesn't stand still (choose_stationary)
        self.moving = False  # (MECH86) the build walks while using skills, all fight (the search's choice, moving_candidates)
        self.rolling = 0.0  # share of the fight spent dodge rolling (Search.rolling_share)
        self.found_uptimes: dict[str, float] = {}  # var -> share of the fight, from the last evidence
        self.uptimes: dict[str, float] = {}  # the active ones up for only part of it (Search.weigh_uptimes)

    def _group_uses(self, groups: list[dict], names: set[str], rows: dict | None = None) -> dict[str, float]:
        """{gem name: uses per second} for skills beside the main one, each at its enabled group's rate
        (skill_use_rate, at the search's chosen rate where nothing else sets it); a name in no enabled group is left
        out (the caller then reads _chosen_use: the chosen rate, else 0). `rows`, if given, gets {group index: the
        bridge's useRates row} (MECH75: the same rows give the other groups' Ignites, ignite_others)."""
        where = {n["name"]: g["index"] for g in groups if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in names}
        if not where:
            return {}
        choices = models.press_choices(self.build)
        raw = self.build.call("useRates", indices=sorted(set(where.values())))["rates"]
        if rows is not None:
            rows.update({r.get("index"): r for r in raw})
        by_index = {r.get("index"): skill_use_rate(r, choices)[0] for r in raw}
        return {n: by_index[i] for n, i in where.items() if i in by_index}

    def battershout_uses(self, gems: dict) -> float:
        """Uses per second of the Warcries Battershout supports (MECH72): the main skill's Speed if it is one, else each
        enabled group's rate (_group_uses; else the search's chosen press rate, else 0), summed."""
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        shouts = {n["name"] for g in groups
                  if any(n.get("enabled", True) and n["name"] == BATTERSHOUT and (gems.get(n["name"]) or {}).get("support")
                         for n in g.get("gems") or [])
                  for n in g.get("gems") or []
                  if n.get("enabled", True) and "Warcry" in ((gems.get(n["name"]) or {}).get("skillTypes") or ())
                  and not (gems.get(n["name"]) or {}).get("support")}
        if not shouts:
            return 0.0
        rates = self._group_uses(groups, shouts - {self.main_skill})
        return sum(number(self.build.call("stats", fields=["Speed"]).get("Speed")) if name == self.main_skill
                   else rates.get(name, self._chosen_use(name)) for name in shouts)

    def _chosen_use(self, name: str) -> float:
        """Uses per second of a skill beside the main one that no group rate covers: the search's chosen press rate for
        it (models.press_choices), else 0 (no source, 0; MECH56: before, one use every models.PRESS_EVERY s)."""
        return models.press_choice({"name": name}, models.press_choices(self.build)) or 0.0

    def _human_shifts(self, speed: float) -> tuple[float, str]:
        """human_shifts for the build: its pressed active skills beside the main one (constraints.presses), each at its
        group's rate (_group_uses; else the search's chosen rate, else 0). Triggered and persistent skills aren't
        presses, so they aren't counted (UNVERIFIED whether a triggered skill shifts you back: a lower reading)."""
        from builder.request.constraints import presses

        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        gems = self.build.ctx.gems
        names = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)
                 and n["name"] != self.main_skill and (gem := gems.get(n["name"])) and not gem.get("support")
                 and presses(gem)}
        uses = self._group_uses(groups, names)
        others = {n: (set(gems[n].get("skillTypes") or []), uses.get(n, self._chosen_use(n))) for n in names}
        return human_shifts(self.main_types, others, speed)

    def _minion_inputs(self) -> tuple[list, object, dict]:
        """(the build's enabled skills, the main one, the stats models.minion_deaths reads) for the minion models."""
        found = [models.Skill.of(s) for s in self.build.call("activeSkills")["skills"] if not s["disabled"]]
        group = self.build.main_group()
        main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill), None)
        return found, main, {**self.build.call("stats", fields=models.MODEL_FIELDS),  # (MECHB12: A25-2)
                             "_minion_lives": getattr(self.build, "minion_lives", {})}

    def _minion_deaths(self) -> tuple[float, str]:
        """Your minions Killed per second against a boss and how (models.minion_deaths, from the build's skills; MECHC7)."""
        return models.minion_deaths(*self._minion_inputs())

    @staticmethod
    def _minion_waves(skills: list) -> tuple[float, float]:
        """(N, T) of the deaths' waves (minion_wave_uptime): the build's Persistent Reviving minions (models.minion_deaths'
        own filter) and their revival time (models.revive_seconds)."""
        reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
        count = sum(number(s.summon.get("limit")) for s in reviving)
        return (count, models.revive_seconds(reviving)[0]) if count > 0 else (0.0, 0.0)

    def minion_ignite(self, names: set[str]) -> tuple[float, str]:
        """(MECH76) (share of the fight the boss is Ignited by your minions' Infernal Legion, how), from MECH-B's
        models.infernal_legion_ignited(skills, main, stats) -> (share, how) (MECHB30's H1-7: the boss Ignited while a
        supported melee minion stands at it and lives); it joins Radiant Grief's aura as a ground-like source of the
        Ignited share (ailment_uptime's _ailment_ground: every Ignited reader). (0.0, "") until that model is on the
        branch or without an Infernal Legion / Catha's Brilliance gem."""
        model = getattr(models, "infernal_legion_ignited", None)
        # (MECH87, PATCH_CHECK H1-7: Catha's Brilliance too, MECHB37's models.INFERNAL_LEGION; was the "Infernal Legion"
        # names only, so its minions' Ignite never reached the Ignited share: an UNDERCOUNT)
        if model is None or not any(n in getattr(models, "INFERNAL_LEGION", ()) or n.startswith("Infernal Legion")
                                    for n in names):
            return 0.0, ""
        found, main, stats = self._minion_inputs()
        share, how = model(found, main, stats)
        return min(max(float(share), 0.0), 1.0), (f"Infernal Legion's minions ({how})" if how else "")

    def shattering_roar(self, texts: list[str]) -> tuple[float, str] | None:
        """(MECH76) (share of the fight the boss is Intimidated by the Skeletal Brutes' Shattering Roar, how), from
        MECHB30's models.shattering_roar (its note: one Roar a cooldown, pressed as a Command, 4 s x the Intimidate
        Duration lines, less the time every Brute is fallen); debuff_uptime combines it with the line sources as
        independent. None until that model is on the branch (it lands with train 56)."""
        roar = getattr(models, "shattering_roar", None)
        if roar is None:
            return None
        found, main, stats = self._minion_inputs()
        up, how = roar(found, main, stats, texts)
        return float(up), f"Shattering Roar ({how})"

    def death_storm(self) -> tuple[float, float, str]:
        """(MECH65) Death Storm's ground: (Commands per second, the chance a Command strikes at least one fallen
        Skeleton at the boss, how), for shocked_ground. models.death_storm (MECHB20) gives the presses and the mean
        Skeletons fallen at a moment, from the build's own minion deaths (no source: 0, CLAUDE.md). The ground is made
        around each struck Skeleton, so only those that fall at the boss put the boss in it: the melee ones (Skeletal
        Warriors, Reavers, Brutes: ActiveSkills summon_skeleton_* texts and Summon Skeletons' "They use a melee
        attack"), taken as their share of your Reviving Skeletons (MEDIUM); Snipers, Mages, Arsonists and Clerics keep
        their distance, and their fallen bodies are taken to be away from the boss (LOW: a boss that closes in on them
        is missed, an undercount). Deaths at random times, so a Command finds k at the boss with Poisson odds."""
        found, main, stats = self._minion_inputs()
        uses, fallen, _, how = models.death_storm(found, main, stats)
        bones = [s for s in found if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))
                 and models.is_skeleton(s)]
        total = sum(number(s.summon.get("limit")) for s in bones)
        melee = sum(number(s.summon.get("limit")) for s in bones if any(m in s.name for m in DEATH_STORM_MELEE))
        if uses <= 0 or fallen <= 0 or total <= 0:
            return 0.0, 0.0, how
        at_boss = fallen * melee / total
        chance = 1.0 - math.exp(-at_boss)
        return uses, chance, (f"{how}; {melee:g} of your {total:g} Reviving Skeletons fight in melee, so "
                              f"{at_boss:.2f} fallen at the boss on average and {chance:.0%} of Commands strike one "
                              "there (MEDIUM; ranged Skeletons' bodies taken to be away from it, LOW)")

    def _kills(self) -> tuple[float, float, str]:
        """models.kill_rate's (enemies/s, all/s, how) for the request's boss: its preset's add kills (bosses.kill_stats,
        MECH-D's) and your own minions Killed (self._minion_deaths); MECHC9, A23-4."""
        from builder.search import bosses

        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        return models.kill_rate(bosses.kill_stats(boss), *self._minion_deaths())

    def _banner_share(self, banners: list[str]) -> tuple[float, str]:
        """(share of the fight a placed banner stands, how): Glory uses (models.glory_uses) x PoB's duration (MECHC7)."""
        found = [models.Skill.of(s) for s in self.build.call("activeSkills")["skills"] if not s["disabled"]]
        group = self.build.main_group()
        main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill), None)
        stats = self.build.call("stats", fields=models.MODEL_FIELDS)
        groups = self.build.call("groups")["groups"]
        where = {n["name"]: g["index"] for g in groups if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in banners}
        rates = {r.get("index"): r for r in self.build.call("useRates", indices=sorted(set(where.values())))["rates"]} \
            if where else {}
        shares = []
        texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]  # (the Glory lines, MECHC13)
        for name in banners:
            uses, how = models.glory_uses([name], stats, main, found, texts)
            duration = number((rates.get(where.get(name)) or {}).get("duration")) or BANNER_SECONDS
            shares.append((min(1.0, uses * duration), f"{how}; it stands {duration:.1f} s"))
        up, how = min(shares)
        limit, limit_how = banner_limit(texts)
        if len(banners) > limit:  # (MECHC27, K2-4: PoB's one BannerPlanted box puts every banner's buff on at once)
            up *= limit / len(banners)
            how += (f"; {len(banners)} banners and {limit_how}: only {limit} stands at a time, so the box (every "
                    f"banner's buff at once) counts {limit}/{len(banners)} of that (LOW)")
        return up, (f"a placed banner stands {up:.0%} of the fight: {how} (you stand in its 6 m area, a play note; "
                    "Banners have no Valour since 0.3.0)")

    def _skill_duration(self, name: str) -> float:
        """PoB's duration for the enabled skill `name` (its group's useRates row), 0 when not found."""
        where = [g["index"] for g in self.build.call("groups")["groups"] if g.get("enabled") is not False
                 and any(n["name"] == name and n.get("enabled", True) for n in g.get("gems") or [])]
        rows = self.build.call("useRates", indices=where[:1])["rates"] if where else []
        return number((rows[0] if rows else {}).get("duration"))

    def _trinity(self, stats: dict) -> tuple[float, str]:
        """(Affinity summed, how) for Trinity (trinity_affinity): PoB's per-type hit averages under each random element
        mode, put back after."""
        fields = [f"{e}HitAverage" for e in ELEMENTS]
        skills = self.build.call("activeSkills", levelStats=[TRINITY])["skills"]
        grant = int(max((number((s.get("levelStats") or {}).get(TRINITY_GRANT)) for s in skills if s["name"] == TRINITY
                         and not s.get("disabled")), default=0))
        before = self.build.call("getConfig")["input"].get("physMode")
        reads = []
        try:
            for mode in ("FIRE", "COLD", "LIGHTNING"):
                reads.append(self.build.call("setConfig", vars={"physMode": mode}, fields=fields)["stats"])
        finally:
            self.build.call("setConfig", vars={"physMode": before if before is not None else "AVERAGE"}, fields=[])
        if all(highest_element_shares([r]) == highest_element_shares(reads[:1]) for r in reads):
            reads = reads[:1]  # (no random element: one read)
        return trinity_affinity(highest_element_shares(reads), models.hits_per_second(stats), grant)

    def _infusion_supply(self, lines: list[dict], bomb: bool = False) -> tuple[dict[str, float], list[str]]:
        """assumptions.infusion_supply for the build as it stands: Infusions per second by element and what makes them
        (with Living Bomb's when `bomb`, as Assumptions.infusion_share counts them)."""
        from builder.search.assumptions import (LIVING_BOMB, LIVING_BOMB_FIELDS, ORB_OF_STORMS, SIPHON, infusion_supply,
                                                living_bomb_rate, orb_remnants, siphon_freezes, still_far)

        raw = self.build.call("activeSkills")["skills"]
        present = {s["name"]: s for s in raw if not s["disabled"]}
        siphon = (siphon_freezes(self.build.call("stats", fields=models.IMMOBILISE_FIELDS)) if SIPHON in present
                  else (0.0, ""))
        orb = orb_remnants(self.build, present[ORB_OF_STORMS]) if ORB_OF_STORMS in present else (0.0, "")
        living = (living_bomb_rate(self.build.call("stats", fields=list(LIVING_BOMB_FIELDS)),
                                   present[LIVING_BOMB].get("castTime") or 0.0)
                  if bomb and LIVING_BOMB in present else (0.0, ""))
        return infusion_supply(present, [line["text"] for line in lines], self.found_uptimes, siphon, living, orb,
                               far=still_far(self.build.call("getConfig")))

    def _open_weakness(self, texts: list[str]) -> tuple[float, str]:
        """(share of the fight's time the boss has an Open Weakness, how): open_weakness_share of PoB's Full DPS with
        both Open Weakness boxes off over on (probed, the boxes put back after). Where a ledger stand-in counts the
        "N% more damage against enemies with an Open Weakness" line instead of PoB's parse (COV33's pob_wrong entry drops
        the parse and adds the MORE at this share), the box doesn't move it, so its N% is put back into the DPS inside:
        PoB counting it is told by a third probe, the damage box alone off (the line is the box's only reader)."""
        before = self.build.call("getConfig")["input"]
        saved = {var: before.get(var) for var in OPEN_WEAKNESS_VARS}
        more = sum(float(m.group(1)) for t in texts if (m := OPEN_WEAKNESS_MORE.match(t.strip()))) / 100.0
        try:
            on = number(self.build.call("setConfig", vars=dict.fromkeys(OPEN_WEAKNESS_VARS, True),
                                        fields=["FullDPS"])["stats"].get("FullDPS"))
            bare = number(self.build.call("setConfig", vars={OPEN_WEAKNESS_VARS[0]: False}, fields=["FullDPS"])[
                "stats"].get("FullDPS")) if more > 0 else on
            off = number(self.build.call("setConfig", vars=dict.fromkeys(OPEN_WEAKNESS_VARS, False),
                                         fields=["FullDPS"])["stats"].get("FullDPS"))
        finally:
            self.build.call("setConfig", vars={v: (s if s is not None else False) for v, s in saved.items()},
                            fields=["FullDPS"])
        if on <= 0:
            return 0.0, "no DPS read: 0"
        ratio = min(off / on, 1.0)
        stand_in = more > 0 and bare > 0 and on / bare < 1.0 + more / 2.0
        if stand_in:
            ratio /= 1.0 + more
        share = open_weakness_share(ratio)
        return share, (f"its Open Weakness segments are {OPEN_WEAKNESS_LIFE:.0%} of its Life; Full DPS {on:,.0f} in them"
                       + (f" x {1 + more:g} (the {more:.0%} more a ledger stand-in counts)" if stand_in else "")
                       + f", {off:,.0f} outside, so {share:.0%} of the fight's time")

    def _orb_uptimes(self) -> dict[str, tuple[float, str]]:
        """{element: (share of the fight one of the build's Orbs of it is up, how)} for Eye of Winter (MECH78, MA-2):
        each enabled Orb skill beside the main one (skill type Orb) at min(1, its uses/s x its duration) (skill_use_rate:
        its cooldown, else once per its duration, else the search's chosen rate, else 0), Orbs of one element combined as
        independent."""
        gems = self.build.ctx.gems
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        where = {n["name"]: g["index"] for g in groups for n in g.get("gems") or []
                 if n.get("enabled", True) and n["name"] != self.main_skill
                 and not (gems.get(n["name"]) or {}).get("support")
                 and "Orb" in ((gems.get(n["name"]) or {}).get("skillTypes") or ())}
        if not where:
            return {}
        rows: dict = {}
        uses = self._group_uses(groups, set(where), rows)
        off: dict[str, float] = {}
        said: dict[str, list[str]] = {}
        for name, index in sorted(where.items()):
            duration = number((rows.get(index) or {}).get("duration"))
            each = uses.get(name, self._chosen_use(name))
            up = min(1.0, each * duration) if duration > 0 else 0.0
            for element in GROUND_ELEMENTS:
                if element in ((gems.get(name) or {}).get("skillTypes") or ()):
                    off[element] = off.get(element, 1.0) * (1.0 - up)
                    said.setdefault(element, []).append(f"{name} up {up:.0%} ({each:.3f} uses/s x {duration:.2f} s)")
        return {e: (1.0 - off[e], ", ".join(said[e])) for e in off}

    def _arc_infused(self, lines: list[dict], stats: dict) -> tuple[float, str]:
        """(share of Arc's casts that consume a Lightning Infusion, how): ARC_INFUSED. The Lightning (and Verisium)
        Infusions the build makes, shared among its Lightning consumers by their uses, over Arc's casts less those that
        keep the Infusion (as Assumptions.infusion_share does for "-infused" stat sets)."""
        from builder.search.assumptions import KEEP_INFUSION

        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        names = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)} | {self.main_skill}
        users = {n for n in names if "Lightning" in INFUSION_CONSUMERS.get(n, ())}
        others = self._group_uses(groups, users - {self.main_skill})
        uses = {n: number(stats.get("Speed")) if n == self.main_skill else others.get(n, self._chosen_use(n))
                for n in users}
        arc = uses.get(ARC, 0.0)
        if arc <= 0:
            return 0.0, f"{ARC} isn't cast (no use rate): 0"
        supply, made = self._infusion_supply(lines, bomb=True)
        have = supply["Lightning"] + supply["Any"]
        texts = [line["text"].strip() for line in lines]
        keep = min(1.0, sum(float(m.group(1)) for t in texts if (m := KEEP_INFUSION.search(t))) / 100.0)
        total = sum(uses.values())
        mine = have * arc / total if total > 0 else 0.0
        needed = arc * (1.0 - keep)
        share = min(1.0, mine / needed) if needed > 0 else float(have > 0)
        return share, (f"{have:.2f} Lightning Infusions/s from {', '.join(made) or 'nothing'}"
                       + (f", shared with {', '.join(sorted(users - {ARC}))}" if users - {ARC} else "")
                       + f": {mine:.2f}/s for {ARC}'s {arc:.2f} casts/s"
                       + (f" ({keep:.0%} keeping theirs)" if keep else ""))

    def _infusion_consumed(self, lines: list[dict], stats: dict) -> tuple[float, str]:
        """(Infusions consumed per second, how): the build's consumers (INFUSION_CONSUMERS) take what it makes."""
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        names = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)}
        consumers = {n: INFUSION_CONSUMERS[n] for n in names | {self.main_skill} if n in INFUSION_CONSUMERS}
        if not consumers:
            return 0.0, "no skill of the build consumes Infusions: 0"
        others = self._group_uses(groups, set(consumers) - {self.main_skill})
        uses = {n: number(stats.get("Speed")) if n == self.main_skill else others.get(n, self._chosen_use(n))
                for n in consumers}
        volley_how = ""
        if EMBER_FUSILLADE in uses and uses[EMBER_FUSILLADE] > 0:  # (MECHC29, K2-11: one Infusion per Fusillade)
            uses[EMBER_FUSILLADE], volley_how = self._fusillades(uses[EMBER_FUSILLADE])
        supply, made = self._infusion_supply(lines)
        elements = {e for each in consumers.values() for e in each}
        have = sum(supply[e] for e in elements) + supply["Any"]
        cast = sum(uses.values())
        rate = min(have, cast)
        return rate, (f"{rate:.2f}/s: {have:.2f} {'/'.join(sorted(elements)) or 'Verisium'} Infusions/s from "
                      f"{', '.join(made) or 'nothing'}, for {', '.join(f'{n} {u:.2f}/s' for n, u in uses.items())}"
                      + (f" ({volley_how})" if volley_how else ""))

    def _fusillades(self, casts: float) -> tuple[float, str]:
        """(Fusillades a second, how) for Ember Fusillade cast at `casts` a second (MECHC29; PATCH_CHECK K2-11). The game
        (ActiveSkills ember_fusillade): "Consumes a Lightning Infusion if possible to cause the entire Fusillade to create
        beams": one Infusion a Fusillade, not a cast. The rhythm is GEM12's (models.ember_fusillade_model, the main skill's
        damage): k casts, adding ProjectileCount Embers each up to the level's cap
        (blazing_cluster_maximum_number_of_projectiles_allowed), then PoB's duration with no recast, the k giving the most
        damage a second; so a Fusillade every k / casts + duration s. A pressed non-main Ember Fusillade is taken at the
        same rhythm with its use rate as its cast rate (LOW)."""
        skill = next((s for s in self.build.call("activeSkills", levelStats=[EMBER_FUSILLADE])["skills"]
                      if s["name"] == EMBER_FUSILLADE and not s.get("disabled")), {})
        cap = int(number((skill.get("levelStats") or {}).get(EMBER_MAX_STAT)) or models.EMBER_CAP)
        more = number((skill.get("levelStats") or {}).get(models.EMBER_MORE_STAT)) or models.EMBER_MORE
        wait = self._skill_duration(EMBER_FUSILLADE) or EMBER_SECONDS
        each = max(1, int(number(self.build.call("stats", fields=["ProjectileCount"]).get("ProjectileCount")) or 1))             if self.main_skill == EMBER_FUSILLADE else 1
        best = None
        for k in range(1, -(-cap // each) + 1):
            n = min(cap, k * each)
            rate = n * (1 + more / 100 * (n - 1) / 2) / (k / casts + wait)
            if best is None or rate > best[0]:
                best = (rate, k, n)
        _, k, n = best
        per = 1 / (k / casts + wait)
        return per, (f"{EMBER_FUSILLADE}: {k} casts at {casts:.2f}/s ({n} Embers, cap {cap}) then {wait:.2f} s: one "
                     f"Fusillade (one Infusion) every {1 / per:.2f} s (GEM12's rhythm)")

    def _ignited_makers(self, lines: list[dict], stats: dict) -> dict[str, tuple[float, str]]:
        """{Incinerate / Ball Lightning: (its Fire-Infused casts per second, how)} (MECH79, ignited_ground): the build's
        Fire and any-element Infusions shared over its Fire consumers (INFUSION_CONSUMERS) by uses, as
        Assumptions.infusion_share shares them; each maker gets at most its own uses."""
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        names = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)} | {self.main_skill}
        if not names & set(IGNITED_GROUND_MAKERS):
            return {}
        out = {}
        if EXPLOSIVE_SPEAR in names:  # (MECH83: its Frenzy-Charged throws, from the Frenzy Charges the build makes)
            out[EXPLOSIVE_SPEAR] = self._frenzy_charged(lines, stats)
        if SPEAR_OF_SOLARIS in names:  # (MECH84: its uses its Glory pays for)
            out[SPEAR_OF_SOLARIS] = self._solaris_uses(lines)
        if BURNING_INSCRIPTION in names:  # (MECH84: one Inscription a cast of each skill it supports)
            out[BURNING_INSCRIPTION] = self._inscriptions(groups, stats)
        if not names & set(INFUSED_MAKERS):
            return out
        fire = {n for n in names if "Fire" in INFUSION_CONSUMERS.get(n, ())}
        others = self._group_uses(groups, fire - {self.main_skill})
        uses = {n: number(stats.get("Speed")) if n == self.main_skill else others.get(n, self._chosen_use(n))
                for n in fire}
        supply, made = self._infusion_supply(lines)
        have = supply["Fire"] + supply["Any"]
        total = sum(uses.values())
        for name in sorted(names & set(INFUSED_MAKERS)):
            rate = min(uses[name], have * uses[name] / total) if total > 0 else 0.0
            out[name] = (rate, f"{rate:.2f} of its {uses[name]:.2f} casts/s Fire-Infused: {have:.2f} Fire Infusions/s "
                               f"from {', '.join(made) or 'nothing'}"
                               + (f", shared with {', '.join(sorted(fire - {name}))}" if fire - {name} else ""))
        return out

    def _marked_share(self) -> tuple[float, str]:
        """(share of the fight the boss carries your Mark, how) for MARK_BLIND: mark_model's Marked share, read with a
        MARKED_LINES probe line as assumptions.siphoning_model reads it (0 with no Mark)."""
        raw = self.build.call("activeSkills")["skills"]
        found = [models.Skill.of(s) for s in raw if not s.get("disabled")]
        group = self.build.main_group()
        main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill), None)
        marks = [s for s in found if s.name in (*models.MARKS, models.PREDATORS_MARK) and s is not main]
        if not marks:
            return 0.0, "no Mark in the build: never Blinded by it"
        metres = None
        if any(s.name == models.PREDATORS_MARK for s in found):
            metres = number(self.build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(set())
        _, notes = models.mark_model(found, self.build.call("stats", fields=models.MODEL_FIELDS), main,
                                     [{"text": "Enemies you Mark take 1% increased Damage"}], metres,
                                     [models.Skill.of(s) for s in raw if s.get("disabled")],
                                     rates=getattr(self.build, "trigger_rates", None))
        note = next((n for n in notes if str(n.get("label", "")).startswith("Lines while the boss is Marked")), None)
        share = float(note["value"]) if note else 1.0
        how = note["basis"].split(": marked")[0] if note else f"{marks[0].name} kept on the boss"
        return share, (f"the boss Marked {share:.0%} of the fight ({how}) and taken to be near itself (RESEARCH30 R30-8 "
                       "(2), MEDIUM-LOW: the sibling stat says 'other enemies' where it means to leave the Marked one out; "
                       "if it isn't: not Blinded)")

    def _thorns_fill(self, texts: list[str], stats: dict) -> dict:
        """models.thorns_fills for the build's Thorns hits (MECH84, PATCH_CHECK L4-2): PoB's Thorns group's useRates row
        and MECH-B's models.thorns_procs (its hits a second); {} without Thorns damage or until thorns_procs is on main."""
        group = next((g for g in self.build.groups() if g.get("fromItem") and g.get("label") == models.THORNS_GROUP
                      and g.get("gems")), None)
        procs_of = getattr(models, "thorns_procs", None)
        if group is None or procs_of is None:
            return {}
        row = next(iter(self.build.call("useRates", indices=[group["index"]]).get("rates") or []), None)
        if not row or models.thorns_hit(row) <= 0:
            return {}
        found = [models.Skill.of(s) for s in self.build.call("activeSkills")["skills"] if not s["disabled"]]
        main_group = self.build.main_group()
        main = next((s for s in found if main_group and s.group == main_group["index"] and s.name == self.main_skill),
                    None)
        procs, how = procs_of(row, texts, {**stats, **self.build.call("stats", fields=models.THORNS_FIELDS)}, main)
        return models.thorns_fills(row, number(procs), how)

    def _solaris_uses(self, lines: list) -> tuple[float, str]:
        """(Spear of Solaris's uses a second, how) (MECH84, ignited_ground): the uses its Glory pays for
        (models.glory_uses), as _banner_share reads a banner's."""
        found = [models.Skill.of(s) for s in self.build.call("activeSkills")["skills"] if not s["disabled"]]
        group = self.build.main_group()
        main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill), None)
        texts = [line["text"] if isinstance(line, dict) else line for line in lines]
        uses, how = models.glory_uses([SPEAR_OF_SOLARIS], self.build.call("stats", fields=models.MODEL_FIELDS),
                                      main, found, texts)
        return uses, f"{uses:.3f} uses/s ({how})"

    def _inscriptions(self, groups: list[dict], stats: dict) -> tuple[float, str]:
        """(Burning Inscriptions a second, how) (MECH84, ignited_ground): the casts of the active skills in each enabled
        group Burning Inscription supports (the main skill at its speed, another at its group's rate, else the chosen
        rate, else 0)."""
        gems = self.build.ctx.gems
        held = {n["name"] for g in groups
                if any(x.get("enabled", True) and x["name"] == BURNING_INSCRIPTION for x in g.get("gems") or [])
                for n in g.get("gems") or [] if n.get("enabled", True) and not (gems.get(n["name"]) or {}).get("support")}
        others = self._group_uses(groups, held - {self.main_skill})
        rates = {n: number(stats.get("Speed")) if n == self.main_skill else others.get(n, self._chosen_use(n))
                 for n in sorted(held)}
        total = sum(rates.values())
        if total <= 0:
            return 0.0, "no cast of a skill it supports is rated: 0"
        return total, ", ".join(f"{n} {r:.2f}/s" for n, r in rates.items()) + " (one Inscription a cast)"

    def _frenzy_charged(self, lines: list, stats: dict) -> tuple[float, str]:
        """(Explosive Spear's Frenzy-Charged throws a second, how) (MECH83, ignited_ground): the Frenzy Charges consumed
        a second (_charges_held's charge flow over the build's Frenzy consumers, stats["_consumers"]), the spear's share
        of them by uses."""
        texts = [line["text"] if isinstance(line, dict) else line for line in lines]
        scratch = {**stats, "_charge_flow": {}}
        _charges_held("Frenzy")(texts, scratch)
        flow = scratch["_charge_flow"].get("Frenzy")
        wanted = {n: number(u) for n, u in ((stats.get("_consumers") or {}).get("Frenzy") or {}).items()}
        spear, total = wanted.get(EXPLOSIVE_SPEAR, 0.0), sum(wanted.values())
        if not flow or total <= 0 or spear <= 0:
            return 0.0, "no Frenzy Charge reaches it (no source, or the spear's throws not rated)"
        rate = min(spear, flow[0] * spear / total)
        return rate, f"{rate:.2f} of its {spear:.2f} throws/s Frenzy-Charged ({flow[1]})"

    def _oil_ground(self, texts: list[str], stats: dict, pieces: list) -> list[tuple[str, float, float]]:
        """The build's lit Oil Ground under the boss (MECH82, oil_ground): each Oil skill's uses a second (the main skill's
        Speed, else its group's rate, else the search's chosen rate) and its Oil Ground set's Fire hit (the bridge's
        useRates on that set); the lighters: Detonators' hits / uses, the boss's own Ignites, your Ignited Ground."""
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        where = {n["name"]: g["index"] for g in groups for n in g.get("gems") or []
                 if n.get("enabled", True) and n["name"] in OIL_GROUND_SKILLS}
        if not where:
            return []
        uses = self._group_uses(groups, set(where) - {self.main_skill})
        indices = sorted(set(where.values()))
        rows = {r.get("index"): r for r in self.build.call(
            "useRates", indices=indices, statSets=[OIL_GROUND_SET] * len(indices))["rates"]}
        power = _charges_held("Power")(texts, stats)[0] if "Oil Barrage" in where else 0.0
        makers = {}
        for name, index in where.items():
            rate = (number(stats.get("Speed")) if name == self.main_skill
                    else uses.get(name, self._chosen_use(name)))
            how = f"{rate:.2f} uses/s"
            if name == "Oil Barrage" and power > 0:
                rate, how = 0.0, (f"{power:.1f} Power Charges held: every volley taken as the Power-Charged barrage, "
                                  "which makes no Oil Ground (the lower reading)")
            row = rows.get(index) or {}
            if not str(row.get("name") or "").startswith(name):  # (the group rates another active skill: not read)
                row, how = {}, how + "; its Oil Ground set not rated (its group calculates another skill)"
            crit = min(max(number(row.get("CritChance")), 0.0), 100.0) / 100.0
            hand = "MainHand." if row.get("MainHand.FireHitAverage") is not None else ""  # (an attack: its main hand's)
            hit = number(row.get(hand + "FireHitAverage"))
            fire = hit * (1 - crit) + (number(row.get(hand + "FireCritAverage")) or hit) * crit
            makers[name] = (rate, fire, how)
        gems = self.build.ctx.gems
        lighters = []
        main_types = stats.get("_main_types") or set()
        if DETONATOR in main_types:
            hits = number(stats.get("Speed")) * min(number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
            lighters.append((f"{self.main_skill}'s hits (a Detonator)", hits))
        side = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)
                and n["name"] != self.main_skill and DETONATOR in ((gems.get(n["name"]) or {}).get("skillTypes") or ())}
        if side:
            rates = self._group_uses(groups, side)
            lighters += [(f"{n}'s uses (a Detonator)", rates.get(n, self._chosen_use(n))) for n in sorted(side)]
        ignites = sum(r * min(m, 100.0) / 100.0 for _, r, m in flammability_sources(stats))
        if ignites > 0:
            lighters.append(("the boss's Ignites", ignites))
        found, how = oil_ground(makers, lighters, number((stats.get("_ignited_ground") or (0.0, ""))[0]), pieces)
        stats["_oil_ground_how"] = how
        return found

    def _overgrown(self, texts: list[str]) -> tuple[float, str]:
        """(share of the main Plant skill's plants that are Overgrown, how), from GEM16's models.overgrown_share."""
        if "Plant" not in set((self.build.ctx.gems.get(self.main_skill) or {}).get("skillTypes") or ()):
            return 0.0, f"{self.main_skill} isn't a Plant skill"
        share_fn = getattr(models, "overgrown_share", None)
        if share_fn is None:
            return 0.0, "the Overgrown share isn't modelled on this branch yet (GEM16's overgrown_share): 0, labelled"
        raw = self.build.call("activeSkills")
        skills = [models.Skill.of(s) for s in raw["skills"] if not s.get("disabled")]
        storms = sorted({s.group for s in skills if s.name in getattr(models, "OVERGROW_SKILLS", ()) and s.group is not None})
        press = getattr(self.build.objective, "press_scales", None) or {}
        groups = {r["index"]: {**r, "_press": press.get(r["name"])}
                  for r in self.build.call("useRates", indices=storms)["rates"]} if storms else {}
        up, parts, _ = share_fn(skills, groups, texts)
        return up, "; ".join(parts) or "nothing Overgrows its plants"

    def _primed_freeze(self, stats: dict) -> tuple[float, str] | None:
        """(Arctic Howl's uses a second, how) for models.primed_refill: the main skill's Speed if it is the Howl, else its
        group's rate (else the search's chosen rate, else 0); None at 0."""
        if self.main_skill == ARCTIC_HOWL:
            rate = number(stats.get("Speed"))
        else:
            groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
            rate = self._group_uses(groups, {ARCTIC_HOWL}).get(ARCTIC_HOWL, self._chosen_use(ARCTIC_HOWL))
        if rate <= 0:
            return None
        return rate, f"{ARCTIC_HOWL} {rate:.3f} uses/s, its howl taken to reach the boss (MEDIUM)"

    def _uruk_taken(self, rate: float) -> tuple[float, str]:
        """(the fight-average % increased Physical Damage taken, how) from Uruk's Smelting on the main skill (MECH80,
        uruk_taken): its stat at the gem's level, the Full Break rate, the fight's length from the boss's pool over the
        build's Full DPS."""
        raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[URUK_STAT])
        main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
        per = number((main.get("supportStats") or {}).get(URUK_STAT))
        if per <= 0:
            return 0.0, f"{URUK}'s Physical Damage taken stat not read: 0"
        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        pool = boss_pool(boss)
        dps = number(self.build.call("stats", fields=["FullDPS"]).get("FullDPS"))
        if pool <= 0 or dps <= 0:
            return 0.0, f"{URUK}: no fight length (the build's Full DPS is 0): 0"
        share, how = uruk_taken(per, rate, pool / dps)
        return share, f"{URUK}: {how}; the boss's {pool:,.0f} Life + ES at {dps:,.0f} Full DPS"

    def _incinerate_fuel(self) -> tuple[float, float, str]:
        """(share of the fight channelling Incinerate, one channel's s, how) (MECH79, incinerate_channel): the Mana the
        build's other enabled groups spend per second (each at its use rate, skill_use_rate, x PoB's ManaCost)."""
        raw = self.build.call("activeSkills", levelStats=[INCINERATE])["skills"]
        got = next((s.get("levelStats") or {} for s in raw if s["name"] == INCINERATE
                    and INCINERATE_FUEL_STAT in (s.get("levelStats") or {})), {})
        per_mana = number(got.get(INCINERATE_FUEL_STAT))
        if per_mana <= 0:
            return 0.0, 0.0, "Incinerate's fuel per Mana not read: 0"
        groups = self.build.call("groups")
        main = groups["mainSocketGroup"]
        others = [g["index"] for g in groups["groups"] if g.get("enabled") is not False and g["index"] != main]
        spend, parts = 0.0, []
        if others:
            choices = models.press_choices(self.build)
            for row in self.build.call("useRates", indices=others)["rates"]:
                cost = number(row.get("manaCost"))
                if cost > 0:
                    rate = skill_use_rate(row, choices)[0]
                    spend += rate * cost
                    parts.append(f"{row.get('name')} {rate:.2f}/s x {cost:.0f}")
        objective = getattr(self.build, "objective", None)
        busy = number(objective.busy()) if objective is not None and hasattr(objective, "busy") else 0.0
        share, channel, how = incinerate_channel(spend, per_mana, number(got.get(INCINERATE_MAX_FUEL_STAT)) or 5000.0,
                                                 number(got.get(INCINERATE_MIN_FUEL_STAT)) or 1000.0, busy)
        return share, channel, how + (f" ({'; '.join(parts)})" if parts else "")

    def _compounding(self, stats: dict, fuel: tuple[float, float, str] | None) -> tuple[float, str]:
        """(Compounding Ignite's more Ignite Magnitude factor, how) for the main skill (MECH79, compounding_factor)."""
        rate = number(stats.get("Speed")) * min(number(stats.get("IgniteChancePerHit")), 100.0) / 100.0 * min(
            number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
        duration = (number(self.build.call("stats", fields=["IgniteDuration"]).get("IgniteDuration"))
                    or AILMENT_DURATION["Ignite"])
        if self.main_skill == INCINERATE:
            share, channel, _ = fuel or (0.0, 0.0, "")
            if share <= 0:
                return 1.0, "Incinerate isn't channelled (no fuel): no Compounding"
            gap = channel * (1 - share) / share
            play = f"{channel:g} s channels, {gap:.1f} s between them (its fuel)"
        else:
            raw = self.build.call("activeSkills", levelStats=[self.main_skill])["skills"]
            got = next((s.get("levelStats") or {} for s in raw if s["name"] == self.main_skill), {})
            channel = (number(got.get(FLAME_BREATH_FREE_STAT)) or 4000.0) / 1000.0
            gap = 0.0
            play = (f"{channel:g} s channels (its Rage-free window), recast at once (it can't gain Rage while in use, so "
                    "back-to-back channels start with none to prolong it; MEDIUM)")
        factor, n = compounding_factor(rate, channel, gap, duration)
        return factor, (f"{rate:.2f} Ignites/s ({number(stats.get('IgniteChancePerHit')):.0f}% a hit), {n} a channel, "
                        f"each {duration:g} s; {play}: the Ignite averages x{factor:.2f} its magnitude (the k-th Ignite "
                        "of a use x k, one live at a time; MEDIUM)")

    def _ignited_charms(self, lines: list[dict]) -> dict[str, tuple[float, str]]:
        """{charm line making Ignited Ground: (the charm's uses per second, how)} (MECH79; Beira's Anguish's line doesn't
        end "on use", so _item_uses doesn't read it)."""
        from builder.search.flasks import CHARM_SLOTS, Charges, slot_pool, uses_per_second

        found = {line["text"]: line["source"][5:] for line in lines
                 if line["source"].startswith("item:Charm") and IGNITED_ON_USE.search(line["text"].strip())}
        if not found:
            return {}
        gain = Charges.of(self.build)
        out = {}
        for text, slot in found.items():
            option = self.build.equipped.get(slot)
            if option is None or slot not in CHARM_SLOTS[:gain.charm_limit]:
                out[text] = (0.0, f"{slot} not in use: 0")
                continue
            info = self.build.call("flaskData", raws=[option.raw])["results"][0]
            out[text] = uses_per_second(info, gain, option.raw, slot_pool(slot))
        return out

    def _item_uses(self, lines: list[dict], events: dict) -> dict[str, tuple[float, str]]:
        """{line text: (uses per second, how)} for the equipped flasks' and charms' own "on use" lines, and the flask
        events (Flask, LifeFlask, ManaFlask) added to `events`: each item at flasks.uses_per_second."""
        from builder.search.flasks import CHARM_SLOTS, FLASK_SLOTS, Charges, slot_pool, uses_per_second

        gain = Charges.of(self.build)
        slots = [s for s in (*FLASK_SLOTS, *CHARM_SLOTS[:gain.charm_limit]) if self.build.equipped.get(s) is not None]
        raws = [self.build.equipped[s].raw for s in slots]
        infos = self.build.call("flaskData", raws=raws)["results"] if raws else []
        per_slot = {s: uses_per_second(info, gain, raw, slot_pool(s)) for s, raw, info in zip(slots, raws, infos)}
        for kind, pools in (("Flask", ("Life", "Mana")), ("LifeFlask", ("Life",)), ("ManaFlask", ("Mana",))):
            used = [(s, per_slot[s]) for s in FLASK_SLOTS if s in per_slot and slot_pool(s) in pools]
            events[kind] = (sum(u for _, (u, _) in used), "; ".join(f"{s} {u:.3f}/s ({h})" for s, (u, h) in used)
                            or "no flask equipped: 0")
        found: dict[str, list] = {}
        for line in lines:
            slot = line["source"][5:]
            if slot in per_slot and ON_USE.search(line["text"]):
                entry = found.setdefault(line["text"], [0.0, 0, []])
                entry[0] += per_slot[slot][0]
                entry[1] += 1
                entry[2].append(f"{slot}: {per_slot[slot][1]}")
        # the same text on two items: each copy takes the average, so the copies together count both items' uses
        return {text: (total / count, "; ".join(how)) for text, (total, count, how) in found.items()}

    def charged_mark(self, stats: dict) -> tuple[float, str, float]:
        """(MECH61) (Activations per second of the Mark Charged Mark supports, how, its ground's radius in metres).
        Charged Mark triggers on every Activation (chance_to_trigger_shocked_ground_on_mark_activated_% 100,
        research/shocked-ground-sources.md, HIGH); the rate is models.mark_activations', at the Mark's own duration and
        with Eternal Mark's second Activation when the group holds it (MECH62, AUDIT27 A27-1 / A27-2).
        MECH62 (A27-3): the radius is read at the supported Mark gem's level. TriggeredChargedMarkPlayer has 40 levels and
        the 40 in the GrantedEffects column MECHB17 found on the 27 support-granted effects that copy their parent's level
        (game 26476662: GrantedEffects row 1150, _unnamed36 40; not one of MECHB17's 7 odd ones), and
        bridge.installGrantedLevel gives it the group's active gem's level in PoB (HIGH: the user's in-game check that
        support-triggered skills keep their parent gem's level, 13 Nov). The gem's own level, without +level lines from
        items, so a build with those reads a slightly smaller radius (the safe side)."""
        for group in self.build.groups():
            if group.get("enabled") is False:
                continue
            gems = [g for g in group["gems"] if g.get("enabled", True)]
            support = next((g for g in gems if g["name"] == CHARGED_MARK), None)
            mark = next((g for g in gems if g["name"] in models.MARKS), None)
            if support is None or mark is None:
                continue
            eternal = any(g["name"] == models.ETERNAL_MARK for g in gems)
            raw = self.build.call("activeSkills")["skills"]
            skills = [models.Skill.of(s) for s in raw if not s.get("disabled")]
            disabled = [models.Skill.of(s) for s in raw if s.get("disabled")]
            skill = next((s for s in skills if s.name == mark["name"] and s.group == group.get("index")), None)
            # (MECH63) the Mark's level with the build's +level lines (bridge activeSkills markLevel), else the gem's
            level = int(next((s.get("markLevel") for s in raw if s["name"] == mark["name"]
                              and s.get("group") == group.get("index") and s.get("markLevel")), None)
                        or mark.get("level") or 1)
            radius = charged_mark_radius(level)
            if skill is None:
                return 0.0, f"its {mark['name']} isn't an active skill PoB lists", radius
            rate, how = models.mark_activations(skill, skills, stats, disabled, getattr(self.build, "trigger_rates", None),
                                                eternal)
            return rate, how + f"; radius {radius:g} m at the {mark['name']}'s level {level}, with +level lines (the triggered skill "                 "copies its parent's level, HIGH)", radius
        return 0.0, "it supports no Mark", charged_mark_radius(1)

    def ground_ms(self, level_stats: dict, ground_inc: float = 0.0) -> float:
        """The main skill's Shocked Ground duration in ms (SHOCKED_GROUND_DURATION_STAT) with the build's duration
        modifiers (PoB's DurationMod / DurationSecondary) plus `ground_inc`, the supports' "increased duration of Ground
        Surfaces" (Persistent Ground; PoB's DurationMod never holds it: added to the factor, the lower reading of an
        increased line where more multipliers exist, UNVERIFIED); an old shocked-ground stat if PoB ever gives one."""
        old = max(number(level_stats.get(s)) for s in SHOCKED_GROUND_SKILL_STATS)
        stat = SHOCKED_GROUND_DURATION_STAT.get(self.main_skill)
        base = number(level_stats.get(stat)) if stat else 0.0
        if old > 0 or base <= 0:
            return old * (1 + ground_inc / 100)
        got = self.build.call("stats", fields=["DurationMod", "DurationSecondary"])
        factor = number(got.get("DurationMod")) or 1.0
        if stat.startswith("base_secondary") and number(got.get("DurationSecondary")) > 0:
            factor = number(got.get("DurationSecondary")) * 1000 / base
        return base * (factor + ground_inc / 100)

    def ground_feed(self, texts: list[str], stats: dict, names: set[str]) -> dict:
        """(MECH58) What lets the main skill make its Shocked Ground (SHOCKED_GROUND_GATED): {"share": the share of the
        fight it can, "how"} for Lightning Warp (the last Cull threshold of the boss's pool), {"rate": uses per second
        that make it, "how"} for Storm Lance (Frenzy Charges gained) and Gathering Storm (Tempest Bells to explode)."""
        kind = SHOCKED_GROUND_GATED.get(self.main_skill)
        if kind == "cull":
            boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
            t = bosses.cull_threshold(boss) / 100
            return {"share": t, "how": f"the boss under the Cull threshold, the last {t:.1%} of its pool (an upper "
                                       "bound: a hit that Culls it there ends the fight), HIGH"}
        if kind == "frenzy":
            found = [(t, r) for t in texts if (r := _charge_rate(t, "Frenzy", stats)) is not None]
            rate = sum(r[0] for _, r in found)
            if not found:
                return {"rate": 0.0, "how": "only a lance that consumed a Frenzy Charge makes it, and the build gains "
                                            "none (MEDIUM-HIGH)"}
            return {"rate": rate, "how": (f"only lances that consume a Frenzy Charge, at most {rate:.2f} gained/s ("
                                          + "; ".join(f"{t[:50]}: {r[1]}" for t, r in found[:3]) + "; charges other "
                                          "skills spend not taken off, extra-charge lines not added), MEDIUM-HIGH")}
        if kind == "bell":
            if "Tempest Bell" not in names:
                return {"rate": 0.0, "how": "only a Perfectly Timed dash that explodes a Tempest Bell makes it, and the "
                                            "build has no Tempest Bell (HIGH)"}
            from builder.search.usage import Usage, requirements, usage_rules
            rules = usage_rules(self.build).get("Tempest Bell")
            need = next((n for n in requirements("Tempest Bell", rules) if n.kind == "combo"), None) if rules else None
            found = Usage(self.build, None).combo_rate("Tempest Bell", need.amount) if need else None
            if not found:
                return {"rate": 0.0, "how": "only a Perfectly Timed dash that explodes a Tempest Bell makes it, and no "
                                            "Combo rate for the Bell was read (0, MEDIUM)"}
            return {"rate": found[0], "how": (f"one Perfectly Timed dash per Tempest Bell, {found[0]:.2f} Bells/s "
                                              f"({need.amount:g} {found[2]} Strikes a Bell; every dash Perfectly Timed "
                                              "as a way to play, MEDIUM)")}
        return {}

    def presence_seconds(self, texts: list[str], stats: dict) -> tuple[float, str]:
        """(MECH59) The seconds-in-your-Presence box (PRESENCE_SECONDS_VAR) at the fight's average, and its basis:
        PoB's Full DPS probed at PRESENCE_SAMPLES (the box restored after), presence_fight on the boss's pool."""
        metres = _boss_distance(texts, stats)[0] / 10
        radius = number(stats.get("PresenceRadius")) / 10
        if radius < metres:
            return 0.0, (f"the boss {metres:g} m away, outside your {radius:.1f} m Presence: never in it (0)")
        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        pool = boss_pool(boss)
        before = self.build.call("getConfig")["input"].get(PRESENCE_SECONDS_VAR)
        samples = []
        try:
            for s in PRESENCE_SAMPLES:
                got = self.build.call("setConfig", vars={PRESENCE_SECONDS_VAR: s}, fields=["FullDPS"])["stats"]
                samples.append((float(s), number(got.get("FullDPS"))))
        finally:
            self.build.call("setConfig", vars={PRESENCE_SECONDS_VAR: before if before is not None else False},
                            fields=["FullDPS"])
        fight, average = presence_fight(samples, pool)
        if fight <= 0:
            return 0.0, "no DPS read: 0"
        seconds = math.floor(average)
        return float(seconds), (
            f"the boss in your {radius:.1f} m Presence from the start ({metres:g} m away), a {fight:.1f} s fight on its "
            f"{pool:,.0f} Life + ES with the ramp (Full DPS {samples[0][1]:,.0f} at 0 s, {samples[-1][1]:,.0f} at "
            f"{samples[-1][0]:g} s): its average DPS is the ramp's at {seconds} s (MEDIUM; a Culling Strike from N s "
            f"counts only if N <= {seconds}, though the real one lands at the end of the {fight:.0f} s fight: an "
            "undercount between the two)")

    def _kurgal_commands(self) -> tuple[float, str]:
        """(MECHC27) Commands a second of the minion skills Kurgal's Leash supports (each in an enabled group with it, at
        that group's rate, else the search's chosen rate, else 0), and how."""
        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        names = {n["name"] for g in groups if any(m["name"] == KURGAL and m.get("enabled", True) for m in g.get("gems") or [])
                 for n in g.get("gems") or [] if n.get("enabled", True) and not n.get("support") and n["name"] != KURGAL}
        if not names:
            return 0.0, "it supports no minion skill"
        uses = self._group_uses(groups, names)
        rates = {n: uses.get(n, self._chosen_use(n)) for n in sorted(names)}
        return sum(rates.values()), ", ".join(f"{n} Commanded {r:.3f}/s" for n, r in rates.items())

    def _full_life(self, es: bool = False) -> tuple[float, str]:
        """(MECHC27) recovery.full_life_share (full_es_share with `es`) on PoB's recovery outputs and the search's added
        recovery stats."""
        from builder.search import objective, recovery

        fields = sorted(set(objective.RECOVERY_INPUTS) | set(FULL_LIFE_FIELDS))
        added = getattr(self.build, "added_stats", None)
        share = recovery.full_es_share if es else recovery.full_life_share
        return share({**self.build.call("stats", fields=fields),
                      **(added() if callable(added) else {})})

    def _leeching(self, texts: list[str], gems: set[str]) -> dict[str, tuple[float, str]]:
        """(MECHC31) leeching_shares on PoB's recovery outputs, its Leech rates and the search's added recovery stats."""
        from builder.search import objective

        fields = sorted(set(objective.RECOVERY_INPUTS) | set(FULL_LIFE_FIELDS) | set(LEECH_POOLS.values())
                        | {"ManaPerSecondCost"})
        added = getattr(self.build, "added_stats", None)
        return leeching_shares({**self.build.call("stats", fields=fields), **(added() if callable(added) else {})},
                               texts, gems)

    def evidence(self) -> dict[str, tuple[object, str]]:
        """What the build can sustain now: var -> (value, evidence)."""
        lines = self.build.call("sourceLines")["lines"]
        stats = self.build.call("stats", fields=EVIDENCE_FIELDS)
        gem_types = {}
        for line in lines:
            if line["source"].startswith("gem:"):
                name = line["source"][4:]
                gem_types[name] = set((self.build.ctx.gems.get(name) or {}).get("skillTypes") or [])
        # Charge consumers and their uses per second (the main skill at its speed, any other at its group's rate)
        from builder.search.models import HOLLOW_FORM

        # Hollow Form holding the main skill is channelled at its speed, and the Attack in it can't consume charges
        # (models.hollow_model).
        consumers: dict[str, dict[str, float]] = {}
        main_types = self.main_types
        group = self.build.main_group()
        hollow = group is not None and any(g["name"] == HOLLOW_FORM for g in group["gems"])
        for kind in ("Power", "Frenzy", "Endurance"):
            flag = f"SkillConsumes{kind}ChargesOnUse"
            for name, types in gem_types.items():
                if flag in types:
                    if hollow and types == main_types and name != HOLLOW_FORM:
                        continue
                    uses = (number(stats.get("Speed")) if types == main_types or (hollow and name == HOLLOW_FORM)
                            else None)  # (None: rated from its group below)
                    consumers.setdefault(kind, {})[name] = uses
        # Rage spenders (RAGE_PER_USE / RAGE_PER_SECOND): the main skill, or a support in its group, at the main skill's
        # uses per second; any other at its group's rate (a per-second channel held 1 s a press, assumed; MECHC1)
        main_gems = {g["name"] for g in group["gems"]} if group is not None else set()
        rage_spend: dict[str, float] = {}
        for name in gem_types:
            uses = number(stats.get("Speed")) if name == self.main_skill or (
                name in main_gems and name.startswith("Rageforged")) else None
            if name in RAGE_PER_USE:
                rage_spend[name] = RAGE_PER_USE[name] * uses if uses is not None else None
            elif name in RAGE_PER_SECOND:
                # (a channel beside the main skill: held 1 s a press, at its group's rate below; MECHC1, assumed)
                rage_spend[name] = RAGE_PER_SECOND[name] if name == self.main_skill else None
        texts = [line["text"] for line in lines]
        unrated = {n for each in consumers.values() for n, u in each.items() if u is None} | {
            n for n, u in rage_spend.items() if u is None}
        groups_now = self.build.call("groups")["groups"] if unrated or any(
            p.search(t) for p in EVENT_WORDS.values() for t in texts) else []
        if unrated:
            # FLAT_RATES A4 (MECH48): a consumer / Rage spender beside the main skill is used at its own group's rate
            # (skill_use_rate: its cooldown, else its duration, else the search's chosen rate, else 0), not one use per 4 s
            uses = self._group_uses(groups_now, unrated)
            for each in consumers.values():
                for n in [n for n, u in each.items() if u is None]:
                    # (a support in the main skill's group consumes on the main skill's uses)
                    each[n] = number(stats.get("Speed")) if n in main_gems else uses.get(n, self._chosen_use(n))
            for n in [n for n, u in rage_spend.items() if u is None]:
                each = uses.get(n, self._chosen_use(n))
                rage_spend[n] = RAGE_PER_USE[n] * each if n in RAGE_PER_USE else RAGE_PER_SECOND[n] * min(1.0, each)
        if any(INVIGORATING_HATE.search(t) or SHIFTED_ANIMAL_RECENTLY.search(t) or SHIFTED_HUMAN_RECENTLY.search(t)
               for t in texts):  # (MECHC21: shifts back to Human form, from the build's presses; MECHC34: MB-8)
            stats["_human_shifts"] = self._human_shifts(number(stats.get("Speed")))
        if any(BUFF_EXPIRY.search(t) for t in texts):  # (MECHC35: "Buffs on you expire N% slower", buff_expiry)
            stats["_buff_expiry"] = buff_expiry(texts)
        if any("with your shield raised" in t.lower() and "lava" in t.lower() for t in texts):
            # (MECHC36, K2-6: Magma Barrier's charge needs a Block with the shield raised: the share it's up)
            from builder.search import recovery

            presses = getattr(self.build, models.ITEM_PRESSES, None) or {}
            stats["_raised_share"] = (
                1.0 if self.main_skill in recovery.ACTIVE_BLOCK_CHANNELS else
                min(1.0, presses[models.RAISE_SHIELD][0] * recovery.RAISED_TAP_SECONDS)
                if models.RAISE_SHIELD in presses else 0.0)
        if any(models.CONSUMED_CORPSE.search(t) for t in texts):  # (MECHB38: from the assumptions' corpse model)
            stats["_corpses_consumed"] = getattr(self.build, "corpses_consumed", None) or (0.0, "not read yet")
        if any(MINION_DIES.search(t) for t in texts):  # (MECHC7: "when a Minion Dies" lines, from the build's own sources)
            inputs = self._minion_inputs()
            stats["_minion_deaths"] = models.minion_deaths(*inputs)
            stats["_minion_waves"] = self._minion_waves(inputs[0])  # (MECHC32: the deaths' waves under revival)
        if any(MARK_ACTIVATED.search(t) for t in texts):  # (MECHC27, K2-2: a Mark's own charge on Activation)
            marks = {n for n, types in gem_types.items() if "Mark" in types}
            uses = self._group_uses(self.build.call("groups")["groups"], marks) if marks else {}
            stats["_mark_uses"] = sum(uses.get(n, self._chosen_use(n)) for n in marks)
        if KURGAL in gem_types:  # (MECHC27: Unholy Might on Command, unholy_might_uptime)
            stats["_kurgal"] = self._kurgal_commands()
        if any(g.get("name") == "Demon Form" for grp in self.build.groups() for g in grp.get("gems") or []):
            # (MECHC28: the share in Demon Form, computed here as the assumptions do: a settle resolves the conditions
            # before the assumptions run, so their last result could be a round old)
            from builder.search.assumptions import demon_form_state

            state = demon_form_state(self.build, self.build.call("activeSkills")["skills"], self.main_skill)
            stats["_demon_form"] = (state["share"], state["how"]) if state is not None else None
        if any(g.get("name") == TRINITY and g.get("enabled", True) for grp in self.build.call("groups")["groups"]
               for g in grp.get("gems") or []):  # (MECHC29, MA-5: Trinity's Affinity from the main skill's hits)
            stats["_trinity"] = self._trinity(stats)
        if any(g.get("name") == PLATING and g.get("enabled", True) for grp in self.build.call("groups")["groups"]
               for g in grp.get("gems") or []):  # (MECHC29, M1-1: the stacks' duration, PoB's with the build's modifiers)
            stats["_plating_duration"] = self._skill_duration(PLATING)
        if any(g.get("name") == WIND_DANCER and g.get("enabled", True) for grp in self.build.call("groups")["groups"]
               for g in grp.get("gems") or []):  # (MECHC29, L2-3: its stage interval, the gem's level stat)
            found = [s for s in self.build.call("activeSkills", levelStats=[WIND_DANCER])["skills"]
                     if s["name"] == WIND_DANCER and not s.get("disabled")]
            stats["_wind_dancer"] = number((found[0].get("levelStats") or {}).get(WIND_DANCER_STAT)) / 1000 if found else 0.0
        if any(g.get("name") == SIGIL and g.get("enabled", True) for grp in self.build.call("groups")["groups"]
               for g in grp.get("gems") or []):  # (MECHC29: Sigil of Power's duration, the Mana and the main skill's spend)
            pool = self.build.call("stats", fields=["Mana", "ManaPerSecondCost"])
            stats["_sigil"] = (self._skill_duration(SIGIL), number(pool.get("Mana")), number(pool.get("ManaPerSecondCost")))
        if any(FULL_LIFE_LINE.search(t) for t in texts):  # (MECHC27: the share on Full Life, recovery.full_life_share)
            stats["_full_life"] = self._full_life()
        if any(LEECHING_LINE.search(t) for t in texts):  # (MECHC31, R26-40 (c): Leech only while its pool is below full)
            stats["_leeching"] = self._leeching(texts, set(gem_types))
        if (any(FULL_ES_LINE.search(t) or CURRENT_ES_LINE.search(t) for t in texts)  # (MECHC33: current ES lines)
                or any(m in gem_types for m in MYSTICISM)):  # (MECHC27, MC-5)
            stats["_full_es"] = self._full_life(es=True)
        if any(KILL_LINE.search(t) for t in texts):  # (MECHC9, A23-4: on-kill lines at the build's kills)
            stats["_kills"] = self._kills()
        if banners := sorted({n["name"] for g in self.build.call("groups")["groups"] if g.get("enabled") is not False
                              for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in BANNERS}):
            # (MECHC7: the placed banner at its Glory share)
            stats["_banner"] = self._banner_share(banners)
        if any(INFUSION_CONSUMED.search(t) for t in texts):
            stats["_infusion_consumed"] = self._infusion_consumed(lines, stats)
        if ARC in gem_types:  # (MECH77, MA-1: the share of Arc's casts the build's Lightning Infusions cover)
            stats["_arc_infused"] = self._arc_infused(lines, stats)
        events =event_rates(groups_now, self.build.ctx.gems, self.main_skill, main_types, number(stats.get("Speed")),
                             lambda idx: self.build.call("useRates", indices=idx)["rates"], texts,
                             models.press_choices(self.build)) if groups_now else {}
        item_uses = self._item_uses(lines, events) if any(
            ON_USE.search(line["text"]) and line["source"].startswith(USED_SLOTS) for line in lines) or any(
            FLASK_EVENT.search(t) for t in texts) else {}
        boss = getattr(getattr(getattr(self.build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        stats = {**stats, "_has_ally": any(t & {"Minion", "CreatesMinion"} for t in gem_types.values()),  # (MECHC31, Conduit)
                 "_gem_names": set(gem_types), "_boss": boss, "_events": events, "_item_uses": item_uses, "_texts": texts, "_weapon": getattr(self.build.equipped.get("Weapon 1"), "type", ""), "_consumers": consumers, "_rage_spend": rage_spend, "_consume_gains": dict(getattr(self.build, "consume_gains", None) or {}),
                 "_main_channel_rage": self.main_skill in RAGE_PER_SECOND, "_main_types": main_types, "_main_skill": self.main_skill, "_distance_choice": self.distance,
                 "_weapon_types": [o.type for slot in ("Weapon 1", "Weapon 2")
                                   if (o := (getattr(self.build, "equipped", None) or {}).get(slot)) is not None],
                 "_stationary_off": self.stationary_off, "_rolling": self.rolling, "_moving": self.moving,
                 "_curse_uses": curse_uses(self.build, stats, texts, (self.distance or (natural_distance(main_types, self.main_skill), ""))[0])
                 if any("AppliesCurse" in types for types in gem_types.values())
                 or any(models.CURSE_ON_HIT.search(t) for t in texts) else []}
        profusion = {}  # Charge Profusion: the descriptions of the charge-making skills it supports (_charges_held)
        for each in self.build.groups():
            names = {g["name"] for g in each.get("gems") or []}
            if more := next((CHARGE_PROFUSION[n] for n in names if n in CHARGE_PROFUSION), None):
                for line in lines:
                    name = line["source"][4:] if line["source"].startswith("gem:") else None
                    if name in names and "GeneratesCharges" in gem_types.get(name, ()):
                        profusion[line["text"]] = more
        if profusion:
            stats["_profusion"] = profusion
        if any(re.search(r"Frenzy Charge", t, re.I) for t in texts):
            # a main skill that "cannot generate Frenzy Charges" (Ferocity's skill_cannot_generate_frenzy_charges, on the
            # skill or a support of it; MECHC3): its hits feed no Frenzy Charge line (_charge_rate)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[NO_FRENZY_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            if number((main.get("supportStats") or {}).get(NO_FRENZY_STAT)) or number(
                    (main.get("levelStats") or {}).get(NO_FRENZY_STAT)):
                stats["_main_no_frenzy"] = NO_FRENZY_STAT
        if any(re.search(r"Power Charge", t, re.I) for t in texts):
            # (MECHC33) a main skill that can't gain Power Charges while used (Flicker Strike's NO_POWER_STAT): only the
            # time away from it feeds Power Charge lines (_charge_rate)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[NO_POWER_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            if number((main.get("supportStats") or {}).get(NO_POWER_STAT)) or number(
                    (main.get("levelStats") or {}).get(NO_POWER_STAT)):
                stats["_main_no_power"] = NO_POWER_STAT
        if any(BLIND_LINE.search(t) for t in texts):
            # a main skill that "cannot themselves inflict Blind" (Blindside's cannot_inflict_blind, on the skill or a
            # support of it; RESEARCH9 / COVE3's OVERCOUNT, MECH55): its hits and crits feed no Blind line (_debuff_source)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[NO_BLIND_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            if number((main.get("supportStats") or {}).get(NO_BLIND_STAT)) or number(
                    (main.get("levelStats") or {}).get(NO_BLIND_STAT)):
                stats["_main_no_blind"] = NO_BLIND_STAT
        if any(re.search(r"\bRage\b", t) for t in texts):
            # Prototype Seventeen on the main skill (MECHC11): its Rage is lost on each crit (rage_held)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[RAGE_LOST_ON_CRIT_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            lost = number((main.get("supportStats") or {}).get(RAGE_LOST_ON_CRIT_STAT)) or number(
                (main.get("levelStats") or {}).get(RAGE_LOST_ON_CRIT_STAT))
            if lost:
                stats["_main_rage_lost_on_crit"] = lost
        if "Arcane Surge" in main_gems:  # (the support: its threshold and duration from the gem, arcane_surge_uptime)
            raw = self.build.call("activeSkills", supportStats=list(ARCANE_SURGE_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            got = main.get("supportStats") or {}
            if all(got.get(stat) for stat in ARCANE_SURGE_SUPPORT_STATS):
                stats["_arcane_surge_support"] = (got[ARCANE_SURGE_SUPPORT_STATS[0]], got[ARCANE_SURGE_SUPPORT_STATS[1]] / 1000)
        if any(models.UNBOUND_GRANT.match(t.strip()) for t in texts):  # (Unbound Avatar: its duration, the box's state)
            raw = self.build.call("activeSkills", levelStats=[models.UNBOUND])
            got = next((s.get("levelStats") or {} for s in raw["skills"] if s["name"] == models.UNBOUND), {})
            duration = number(got.get("base_skill_effect_duration")) / 1000 or models.UNBOUND_DURATION
            stats["_unbound"] = (duration, bool(self.build.call("getConfig")["input"].get("Unbound")))
        if any("incision" in name.lower() or name == "Bleed IV" for name in main_gems):  # (Incision, Bleed IV)
            raw = self.build.call("activeSkills", supportStats=list(INCISION_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            stats["_incision_per_hit"] = number((main.get("supportStats") or {}).get(INCISION_SUPPORT_STATS[0]))
        names = set(gem_types) | {g["name"] for grp in self.build.groups() if grp.get("enabled") is not False
                                  for g in grp["gems"] if g.get("enabled", True)}
        if set(PRESENCE_SECONDS_GEMS) & names or any(PRESENCE_SECONDS_LINE.search(t) for t in texts):
            stats["_presence_seconds"] = self.presence_seconds(texts, stats)  # (MECH59: Attrition, the ramps)
        if self.main_skill in WITHER_ON_HIT_SKILLS or "Withering Touch" in main_gems or WITHERING_PRESENCE in names:
            # (Withered from gems, _withered_stacks: the main skill's own, Withering Touch on it, Withering Presence)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill, WITHERING_PRESENCE],
                                  supportStats=list(WITHER_SUPPORT_STATS))
            for skill in raw["skills"]:
                got = skill.get("levelStats") or {}
                if skill.get("disabled"):
                    continue
                if skill["name"] == self.main_skill:
                    stats["_wither_support"] = skill.get("supportStats") or {}
                    if self.main_skill in WITHER_ON_HIT_SKILLS:
                        stats["_wither_main"] = (self.main_skill, {s: got.get(s) for s in WITHER_SKILL_STATS})
                elif skill["name"] == WITHERING_PRESENCE:
                    stats["_wither_presence"] = (number(got.get(WITHERING_PRESENCE_STATS[0])) / 1000,
                                                 number(got.get(WITHERING_PRESENCE_STATS[1])) / 1000)
        # Ailments the main skill consumes on hit (CONSUME_STATS; looked up only when a gem of its group says it consumes)
        if consumes := main_consumes(self.build, self.main_skill, main_gems):
            stats["_consumes"] = consumes
        if side := side_consumes(self.build, self.main_skill):  # (MECH53: skills beside the main one)
            stats["_side_consumes"] = side
        if reapply := consume_reapply_chances(texts):  # (MECH85: Endless Circuit)
            stats["_consume_reapply"] = reapply
        if set(BREAK_MORE_SUPPORTS) & main_gems:  # (MECH66: Armour Demolisher / Uruk's Smelting, break_amount_scale)
            raw = self.build.call("activeSkills", supportStats=[BREAK_MORE_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            stats["_break_more"] = number((main.get("supportStats") or {}).get(BREAK_MORE_STAT))
        if PIN_MORE_SUPPORT in main_gems:  # (MECH66, A12-1: Pin III's more Pin duration, models.pin_duration_scale)
            raw = self.build.call("activeSkills", supportStats=[PIN_MORE_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            stats["_pin_more"] = number((main.get("supportStats") or {}).get(PIN_MORE_STAT))
        # Shocked Ground (shocked_ground): the main skill's own, the Shocking Leap support on it, and the lines
        if self.main_skill in SHOCKED_GROUND_SKILLS or SHOCKED_GROUND_SUPPORT in main_gems:
            raw = self.build.call("activeSkills", levelStats=[self.main_skill],
                                  supportStats=[*SHOCKED_GROUND_SUPPORT_STATS, GROUND_SURFACE_DURATION_STAT])
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            got = main.get("levelStats") or {}
            ground_inc = number((main.get("supportStats") or {}).get(GROUND_SURFACE_DURATION_STAT))
            if self.main_skill in SHOCKED_GROUND_SKILLS:
                stats["_sg_skill"] = (self.main_skill, self.ground_ms(got, ground_inc))
                stats["_sg_feed"] = self.ground_feed(texts, stats, names)
            stats["_sg_support"] = (number((main.get("supportStats") or {}).get(SHOCKED_GROUND_SUPPORT_STATS[0]))
                                    * (1 + ground_inc / 100))
        stats["_sg_charged_mark"] = self.charged_mark(stats) if CHARGED_MARK in names else None
        stats["_sg_death_storm"] = self.death_storm() if DEATH_STORM_SUMMON in names else None
        if any(BREAK_OVERGROWN.match(t.strip()) for t in texts):  # (MECH82: GEM16's overgrown_share, break_amount_scale)
            stats["_overgrown"] = self._overgrown(texts)
        if ARCTIC_HOWL in names or self.main_skill == ARCTIC_HOWL:  # (MECH82: Primed for Freeze, models.primed_refill)
            stats["_primed_freeze"] = self._primed_freeze(stats)
        self.build.primed_freeze = stats.get("_primed_freeze")
        # (MECH76: MECHB30's Shattering Roar, routed) the Skeletal Brutes' Intimidate, one more Intimidated source
        roar = self.shattering_roar(texts) if any(SKELETAL_BRUTE in n for n in names) else None
        intimidated = [roar] if roar and roar[0] > 0 else []
        if HER_DECLARATION in names:  # (MECH83: on each Presence entry, her_declaration)
            declared = her_declaration(self.build.groups(), self.build.ctx.gems, texts, stats,
                                       _boss_distance(texts, stats)[0] / 10)
            if declared and declared[0] > 0:
                intimidated.append(declared)
        if any(MARK_BLIND.search(t) for t in texts):  # (MECH87, R30-8 (2): the Marked boss is "near" itself)
            stats["_marked"] = self._marked_share()
        stats["_debuff_extra"] = ({"conditionEnemyIntimidated": (
            1 - math.prod(1 - min(max(u, 0.0), 1.0) for u, _ in intimidated), "; ".join(h for _, h in intimidated))}
            if intimidated else {})
        stats["_stationary"] = (not self.stationary_off and not self.moving
                                and any(STATIONARY_LINE.search(t) for t in texts))
        # (MECH67) the boss's Power with the build's Power lines (models.boss_power): Glory, the roar's Empowers
        stats["_boss_power"] = models.boss_power(texts, stats, _boss_distance(texts, stats)[0], "Attack" in main_types,
                                                 self.main_skill)
        stats["_shocked_ground"] = shocked_ground(stats, texts)
        stats["_chilled_ground"] = chilled_ground(stats, texts, main_gems)  # (MECH76, for COV-A's Desert's Scorn)
        # (MECH79) the build's Ignited Ground at the boss: the ground gains' Fire, COV-A's Desert's Scorn cold half
        if self.main_skill == INCINERATE:  # (MECH79: its fuel, the Channelling box's share)
            stats["_incinerate_fuel"] = self._incinerate_fuel()
        if self.main_skill in COMPOUNDING_SKILLS:  # (MECH79: Compounding Ignite, models.compound_ignite_mods)
            stats["_compound"] = self._compounding(stats, stats.get("_incinerate_fuel"))
        ground_pieces: list = []  # (MECH80: each source, for ground_ignite once the other groups' Ignites are known)
        stats["_ignited_ground"] = ignited_ground(
            self._ignited_makers(lines, stats), texts, stats, self._ignited_charms(lines), ground_pieces)
        if self.main_skill in ELEMENT_GAIN_VARS:  # (MECH78, MA-2 / MA-6 / MA-7: its element from the grounds in its path)
            orbs = self._orb_uptimes() if self.main_skill == EYE_OF_WINTER else None
            stats["_element_gain"] = element_gain_shares(self.main_skill, stats, texts, orbs)
        stats["_whirlwind_shock"] = whirlwind_shock(self.main_skill, texts, stats.get("_element_gain"))  # (MECH80)
        if any(REVEAL_WEAKNESS.match(t.strip()) for t in texts):  # (MECH78, MC-1: its share of the fight's time)
            stats["_open_weakness"] = self._open_weakness(texts)
        if _ground_shocked(stats)[0] >= MIN_UPTIME:  # its Shock's magnitude (ground_shock_magnitude)
            stats["_sg_magnitude"] = self.build.call("shockMagnitude")
        self.parry_plan = None
        if any(n in gem_types for n in models.PARRY_SKILLS) or any(PARRY_GRANT.search(t) for t in texts):  # (Parried)
            raw = self.build.call("activeSkills")
            found = [models.Skill.of(s) for s in raw["skills"] if not s.get("disabled")]
            from builder.search import recovery  # (MECH-C: Heavy Stun from parrying, MECHC30)

            stun = recovery.parry_stun_of(self.build) if any(s.name == "Parry" for s in found) else (0.0, "")
            refute = recovery.refutation_stun_of(self.build) if any(s.name == "Refutation" for s in found) else (0.0, "")
            payoff = 0.0  # (MECH87: a PARRIED_SETS main skill's own extra part, weighed by the plan as assumptions do)
            if self.main_skill in models.PARRIED_SETS and (group := self.build.main_group()):
                payoff = models.parried_payoff(self.main_skill, self.build.call(
                    "setRates", index=group["index"], active=group.get("mainActiveSkill") or 1)["sets"])
            eats = bool(({self.main_skill} | main_gems) & models.PARRY_CONSUMERS)
            stats["_parry_plan"] = self.parry_plan = models.parry_plan(
                found, stats, "Attack" in main_types, eats, stun, refute, payoff,
                models.parried_keep(self.build, self.main_skill) if eats else 0.0)  # (MECH89: parried_keep)
        if any(STUNS_YOU.search(t) for t in texts):  # (Blinding Flash, MECHC25: the boss's Light Stuns on you)
            from builder.search import flasks

            gain = flasks.Charges.of(self.build)
            stats["_stuns_you"] = (gain.stuns, gain.stuns_how)
        if any(models.RAISED_BLIND.search(t) for t in texts):  # (Bastion of Light's shield taps, MECHC21)
            raw = self.build.call("activeSkills")
            found = [models.Skill.of(s) for s in raw["skills"] if not s.get("disabled")]
            metres = (stats.get("_distance_choice") or (natural_distance(main_types, self.main_skill), ""))[0]
            stats["_raised_plan"] = models.raised_plan(found, texts, metres, models.raised_taps_on(self.build))
        # (MECH70: the main skill's own Daze stats, _dazed. MECH71: read for every build, not only when a line names
        # Daze: a skill's own chance (Wind Blast, Siphoning Strike's quality) has no line, and the share also feeds
        # heavy_stun_rate and GEM5's build.dazed_up; plus the build's other hitting skills, _daze_others)
        groups_all = self.build.call("groups")
        groups_now = groups_all["groups"]
        gems = self.build.ctx.gems
        # (MECH75, AUDIT35 A35-2: every gem, item- and tree-granted skills too: Black Powder Blitz's explosion)
        every = self.build.ctx.all_gems
        beside = {n["name"] for g in groups_now if g.get("enabled") is not False for n in g.get("gems") or []
                  if n.get("enabled", True) and n["name"] != self.main_skill
                  and not (every.get(n["name"]) or {"support": True}).get("support")}
        raw = self.build.call("activeSkills", levelStats=[self.main_skill, *sorted(beside)],
                              supportStats=[*DAZE_SKILL_STATS, *BREAK_STATS, BREAK_MORE_STAT, CANNOT_BREAK,
                                            MARK_BREAK_STAT, BRITTLE_BREAK_STAT])
        effects: dict[str, list[dict]] = {}
        for entry in raw["skills"]:
            if not entry.get("disabled"):
                effects.setdefault(entry["name"], []).append(entry)
        hitters = {name for name in beside if daze_hitter(every.get(name), effects.get(name))}
        palm = crackling_palm_rate(stats, self.build.call("stats", fields=["LedgerUnarmedMelee"]))  # (MECH76)
        if CRACKLING_PALM in beside:
            hitters.add(CRACKLING_PALM)
        dazing = {s["name"]: {stat: number(value) for part in ("levelStats", "supportStats")
                              for stat, value in (s.get(part) or {}).items() if stat in DAZE_SKILL_STATS and value}
                  for s in raw["skills"] if not s.get("disabled") and (s["name"] == self.main_skill or s["name"] in hitters)}
        got = dazing.get(self.main_skill) or {}
        stats["_daze"] = {"chance": got.get("base_chance_to_daze_%", 0.0), "cannot": bool(got.get("cannot_daze")),
                          "concussive": bool(got.get("spell_daze_on_critical_hit_dealing_physical_damage")),
                          "opening": bool(got.get("daze_on_hit_vs_enemies_at_full_life"))}
        stats["_daze_weapon_lines"] = {line["text"] for line in lines if line["source"].startswith("item:Weapon")}
        group_rows: dict = {}
        uses = self._group_uses(groups_now, hitters, group_rows) if hitters else {}
        if CRACKLING_PALM in hitters:  # (its bolts at the main skill's Unarmed Melee hits, for every reader of the rows)
            uses[CRACKLING_PALM] = palm[0]
            for row in group_rows.values():
                if str(row.get("name") or "").startswith(CRACKLING_PALM):
                    row["speed"], row["triggered"], row["how"] = palm[0], True, palm[1]
        # (MECH75: Overcharge's reflected Shocks, _self_shocked: the supported groups' Shocks, from the same rows)
        if any(n["name"] == OVERCHARGE for g in groups_now for n in g.get("gems") or []):
            stats["_overcharge"] = overcharge_sources(groups_all, self.main_skill, stats, group_rows,
                                                      models.press_choices(self.build)) or [(OVERCHARGE, 0.0)]
        # (MECH75) the other groups' Ignites on the boss, from the same rows: ailment_uptime adds them to the main skill's
        others, others_how = ignite_others(group_rows, groups_now, hitters, groups_all.get("mainSocketGroup"),
                                           models.press_choices(self.build))
        # (MECH87) the other groups' Freeze / Electrocute / Pin buildup on the boss's one bar (models.others_fill); also
        # published for readers that fetch the main skill's stats afresh (models.dazed_stats)
        stats["_buildup_others"] = buildup_others(group_rows, groups_now, hitters, groups_all.get("mainSocketGroup"),
                                                  models.press_choices(self.build))
        self.build.buildup_others = dict(stats["_buildup_others"])
        # (MECH89) Mark for Death on the build's Mark, at the Marked share (MARK_BREAK_STAT)
        mark_pct = max((number((s.get("supportStats") or {}).get(MARK_BREAK_STAT)) for s in raw["skills"]
                        if not s.get("disabled") and s["name"] in (*models.MARKS, models.PREDATORS_MARK)), default=0.0)
        if mark_pct > 0:
            stats["_mark_break"] = (mark_pct, *self._marked_share())
        # (MECH89) Brittle Armour on the main skill, at the Frozen share (BRITTLE_BREAK_STAT)
        brittle = max((number((s.get("supportStats") or {}).get(BRITTLE_BREAK_STAT)) for s in raw["skills"]
                       if not s.get("disabled") and s["name"] == self.main_skill), default=0.0)
        if brittle > 0:
            frozen_up, frozen_how = models.immobilised_share(stats, "Freeze")
            stats["_brittle_break"] = (brittle, frozen_up, frozen_how or "the main skill doesn't Freeze the boss")
        # (MECH89) the other groups' Armour Break, for _armour_broken (break_others): the same rows and hitters
        stats["_break_others"] = break_others(raw["skills"], group_rows, groups_now, hitters,
                                              groups_all.get("mainSocketGroup"),
                                              [line["text"] for line in lines if not line["source"].startswith("gem:")],
                                              every, models.press_choices(self.build),
                                              stats.get("_mark_break"), stats.get("_brittle_break")) if hitters else []
        if others:
            stats["_ailment_others"] = {"Ignite": others}
            said = stats.setdefault("_ailment_others_how", {})  # (Radiant Grief's aura may be said already)
            said["Ignite"] = "; ".join(filter(None, [others_how, said.get("Ignite")]))
        # (MECH75: Radiant Grief's aura; MECH76: MECHB30's Infernal Legion, routed; MECH80: your Ignited Ground) the
        # Ignited share from sources that don't Hit, after the other groups' Ignites: the aura and the ground Ignite
        # only while the boss holds 50% Flammability (no_hit_ignite, ground_ignite)
        oil = self._oil_ground(texts, stats, ground_pieces)  # (MECH82: lit Oil Ground, another no-Hit Ignite source)
        aura = presence_ignite(texts, stats)
        if aura[0] > 0 or any(p[1] > 0 and p[3] for p in ground_pieces):  # (the build's Flammability Magnitude lines)
            stats["_flammability_mod"] = self.build.call("flammabilityMagnitude") or {}
        if aura[0] > 0:
            reach, how = no_hit_ignite(stats, float(PRESENCE_IGNITE.match(next(
                t.strip() for t in texts if PRESENCE_IGNITE.match(t.strip()))).group(1)), FLAMMABILITY_SECONDS)
            aura = (aura[0] * reach, f"{aura[1]}; it Ignites only at 50% Flammability: {how}")
        legion = self.minion_ignite(names)
        burning = ground_ignite(ground_pieces, stats)
        # (MECH83: the Ignite damage of grounds that name their Fire, Beira's Anguish / Birth of Fury: models.
        # ground_life_ignite_model) (label, share the ground Ignites the boss, its Ignite's DPS, how)
        stats["_ground_life_ignite"] = []
        for label, up, seconds, damage in ground_pieces:
            if up > 0 and damage:
                reach, how = no_hit_ignite(stats, damage, seconds)
                stats["_ground_life_ignite"].append((
                    label, up * reach, models.IGNITE_BASE * damage,
                    f"{label} under the boss {up:.0%} of the fight, as though dealing {damage:,.0f} Fire; {how}"))
        stats["_oil_ignite"] = []  # (MECH82: models.oil_ignite_model) (skill, share Ignited by it, its Ignite's DPS, how)
        for name, lit, damage, oilings in oil:
            reach, how = no_hit_ignite(stats, damage, OIL_GROUND_SECONDS)
            seconds = number(stats.get("IgniteDuration")) or AILMENT_DURATION["Ignite"]
            share, outright = oil_ignite_share(lit, oilings, reach, seconds)  # (MECH83, R28-12)
            stats["_oil_ignite"].append((name, share, models.IGNITE_BASE * damage,
                                         f"{stats.get('_oil_ground_how', '')}; each lit oiling ({oilings:.2f}/s) Ignites the "
                                         f"Oil-covered boss outright for {seconds:g} s ({outright:.0%} of the fight, HIGH: "
                                         f"KeywordPopups Oil); the rest of the lit time {how}", lit))
            if outright > 0:  # (the outright part isn't in ground_ignite's 50%-gated share: joined as independent)
                burning = (1 - (1 - burning[0]) * (1 - outright),
                           "; ".join(filter(None, [burning[1], f"{name}'s lit Oil Ignites the Oil-covered boss outright "
                                                                 f"{outright:.0%} of the fight"])))
        if aura[1] or legion[1] or burning[1]:
            share = 1 - (1 - aura[0]) * (1 - legion[0]) * (1 - burning[0])  # (independent sources)
            aura = (share, "; ".join(filter(None, [aura[1], legion[1], burning[1]])))
            stats["_ailment_ground"] = {"Ignite": aura}
            if aura[0] > 0:
                said = stats.setdefault("_ailment_others_how", {})
                said["Ignite"] = "; ".join(filter(None, [said.get("Ignite"), aura[1]]))
        stats["_daze_others"] = [
            {"name": name, "types": sorted(every[name].get("skillTypes") or []),
             "uses": uses.get(name, self._chosen_use(name)), "chance": d.get("base_chance_to_daze_%", 0.0),
             "cannot": bool(d.get("cannot_daze"))}
            for name, d in sorted(dazing.items()) if name in hitters]
        # (MECH74: Vaulting Impact's Broken Stance, for _dazed's second pass: its hits on the boss a second, the main
        # skill's hits that gain it, its hit limit at the gem's level and quality)
        stance = next((s for s in raw["skills"] if s["name"] == models.STANCE_SKILL and not s.get("disabled")), None)
        if stance is not None:
            main_hits = models.hits_per_second(stats)
            vault = main_hits if self.main_skill == models.STANCE_SKILL else number(
                next((o["uses"] for o in stats["_daze_others"] if o["name"] == models.STANCE_SKILL),
                     uses.get(models.STANCE_SKILL, self._chosen_use(models.STANCE_SKILL))))
            stats["_broken_stance"] = {"vault_hits": vault, "hits": main_hits,
                                       "limit": number((stance.get("levelStats") or {}).get("broken_stance_hit_limit"))}
        stats["_thorns_fill"] = self._thorns_fill(texts, stats)  # (MECH84: Thorns hits' buildup, models.thorns_fills)
        self.build.thorns_fill = dict(stats["_thorns_fill"])
        stats["_crushing"] = models.crushing_source(texts, self.main_skill)  # (MECH85: Crushing Blows, heavy_stun_rate)
        stats["_pin_primed"] = any(models.PIN_PRIMED_LINE.match(t.strip()) for t in texts)  # (MECH85: Primed Pins)
        self.build.crushing, self.build.pin_primed = stats["_crushing"], stats["_pin_primed"]
        stats["_dazed_share"] = number((_dazed(texts, stats) + (0.0,))[2])
        stats["_dazed_box"] = "conditionEnemyDazed" in self.active  # (PoB's box as it is now: heavy_stun_rate)
        broken = None  # Armour Broken first: Exposure on a Full Break reads its rate
        self.riven_share = 0.0  # (share of the fight Riven Armour is up: _riven_cycle)
        self.battershout = (0.0, 0.0, "")  # (MECH72: explosions/s, the boss's Armour, basis: _battershout_cycle)
        self.riven_how = ""
        if "Ferocious Roar" in gem_types and (roar := self.roar_evidence()):
            broken, stats["_full_break_rate"] = roar, roar[2] / models.full_break_duration([line["text"] for line in lines])
        elif (any(ARMOUR_BREAK_LINE.search(line["text"]) for line in lines) or stats.get("_break_others")
              or stats.get("_mark_break") or stats.get("_brittle_break")):
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=list(BREAK_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            gem_lines = [BREAK_STATS[stat].format(f"{value:g}") for part in ("levelStats", "supportStats")
                         for stat, value in (main.get(part) or {}).items() if stat in BREAK_STATS and value]
            texts = [line["text"] for line in lines if not line["source"].startswith("gem:")] + gem_lines
            armour_stats = {**stats, "_enemy_armour": number(raw.get("enemyArmour"))}
            if RIVEN_SKILL in gem_types:  # (MECH69: Riven Armour stops Breaking while it's up; _riven_cycle)
                armour_stats["_riven_uses"] = (number(stats.get("Speed")) if self.main_skill == RIVEN_SKILL else
                                               self._group_uses(self.build.call("groups")["groups"], {RIVEN_SKILL}).get(
                                                   RIVEN_SKILL) or self._chosen_use(RIVEN_SKILL))
            if RIVEN_SKILL not in gem_types and (shout := self.battershout_uses(gems)):
                armour_stats["_battershout_uses"] = shout  # (MECH72: its Warcry Consumes the Full Break; _battershout_cycle)
            up, basis = _armour_broken(texts, armour_stats)
            self.riven_share = number(armour_stats.get("_riven_share"))  # (GEM3's riven_armour_model reads it)
            self.riven_how = basis if self.riven_share > 0 else ""
            if number(armour_stats.get("_battershout_rate")) > 0:  # (models.battershout_model reads it)
                self.battershout = (number(armour_stats["_battershout_rate"]), number(raw.get("enemyArmour")), basis)
            if basis:
                broken = _box(up, basis)
                stats["_full_break_rate"] = (number(armour_stats.get("_battershout_breaks"))
                                             or up / models.full_break_duration(texts))
        if URUK in main_gems and number(stats.get("_full_break_rate")) > 0:  # (MECH80, G2-4: models.uruk_mods)
            stats["_uruk"] = self._uruk_taken(number(stats["_full_break_rate"]))
        stats["_flammability_scale"] = flammability_ignite_scale(stats)  # (MECH81: models.flammability_mods)
        if STORMFIRE in main_gems and number(stats.get("IgniteChancePerHit")) > 0:  # (MECH81, G2-2: models.stormfire_mods)
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=[STORMFIRE_STAT])
            sf = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            preset = bosses.of(stats)
            stats["_stormfire"] = stormfire_more(number((sf.get("supportStats") or {}).get(STORMFIRE_STAT)),
                                                 ailment_uptime(stats, "Shock"), preset.fire, preset.lightning)
        # (MECH69) the Exposure supports on the main skill, by their stats (_exposure_support_sources)
        if any(line["source"].startswith("gem:") and EXPOSURE_WORD.search(line["text"]) for line in lines):
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=list(EXPOSURE_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            stats["_exposure_supports"] = {stat: value for part in ("levelStats", "supportStats")
                                           for stat, value in (main.get(part) or {}).items()
                                           if stat in EXPOSURE_SUPPORT_STATS and value}
        found: dict[str, tuple[object, str]] = {}
        uptimes: dict[str, float] = {}  # var -> share of the fight, for the effects the build keeps up at a rate
        self.off_values: dict[str, object] = {}  # var -> its box with the effect off, if not False (Withered's base stacks)
        self.alt_uptimes: dict[str, tuple[float, str]] = {}  # var -> (uptime, when) under another reading of the rule
        for condition in CONDITIONS:
            reason = None
            for line in lines:
                if condition.text and text_evidence(condition, line["text"]) and qualifies(line["text"], self.main_types):
                    reason = f"{line['source']}: {line['text']}"
                    break
            if reason is None and condition.stat is not None and condition.stat(stats):
                reason = "PoB: " + ", ".join(f"{k} {number(stats.get(k)):.3g}" for k in condition.fields)
                if condition.uptime_fn is not None:
                    up = uptimes[condition.var] = condition.uptime_fn(stats)
                    reason += f"; up {up:.0%} of the fight ({shared(up)})"
                    if side := (stats.get("_side_consumes") or {}).get(SIDE_CONSUMED_BOXES.get(condition.var, "")):
                        reason += f"; consumed by {side[1]} ({side[2]}): each takes it off, then immune 1 s"
                    if beside := (stats.get("_ailment_others_how") or {}).get(SIDE_CONSUMED_BOXES.get(condition.var, "")):
                        reason += f"; with {beside}"
            if condition.value_fn is not None:
                value, basis, *share = condition.value_fn([line["text"] for line in lines], stats)
                if value > 0 or basis:  # (a basis with 0: the build has sources, none of which a boss fight feeds)
                    found[condition.var] = (value, basis)
                    if share:
                        uptimes[condition.var] = share[0]
                    if share[1:] and share[1] is not False:
                        self.off_values[condition.var] = share[1]
                    if share[2:]:  # (uptime, when): the other reading of an unsettled rule, a play-guide row
                        self.alt_uptimes[condition.var] = share[2]
                continue
            if reason is None and condition.main_types and self.main_types & set(condition.main_types):
                reason = "main skill: " + ", ".join(sorted(self.main_types & set(condition.main_types)))
            if reason is None and condition.gem_types:
                gem = next((n for n, types in gem_types.items() if types & set(condition.gem_types)), None)
                if gem:
                    reason = f"gem:{gem}"
            if reason is not None:
                value: object = True
                if condition.value_field:
                    value = number(stats.get(condition.value_field))
                    if value <= 0:
                        continue
                found[condition.var] = (value, reason)
        if broken is not None:
            found["conditionEnemyArmourBroken"] = broken[:2]
            uptimes["conditionEnemyArmourBroken"] = broken[2]
        self.found_uptimes = uptimes
        # (MECHC13) charges consumed a second by kind ({kind: (rate, how)}; _charges_held), for recovery.spent_income
        self.build.charge_flow = dict(stats.get("_charge_flow") or {})
        # (MECH69, for GEM3's models.riven_armour_model: the share of the fight Riven Armour is up, and its basis)
        self.build.riven_up = (self.riven_share, self.riven_how)
        # (MECH87, for GEM17's High Velocity Rounds "Vs Armour Broken": the share of the fight the boss's Armour is Fully
        # Broken and its basis, as the Armour Broken box weighs it; (0.0, "") with no Armour Break source)
        self.build.full_break_up = ((number(broken[2]) if len(broken) > 2 else float(bool(broken[0])), broken[1])
                                    if broken is not None else (0.0, ""))
        self.build.battershout = getattr(self, "battershout", (0.0, 0.0, ""))  # (MECH72, models.battershout_model)
        # (MECH69, for GEM5's Elemental Discharge model: the share of the fight the boss is Ignited / Shocked / Frozen,
        # with the main skill's own and other skills' consumes taken off: the same functions the boxes use, read whether
        # or not a box is on; Frozen from its resolver share, 0 without a Freeze source)
        self.build.ailment_uptimes = {"Ignited": _ailment_share("Ignite")(stats), "Shocked": shock_share(stats),
                                      "Frozen": number(uptimes.get("conditionEnemyFrozen"))}
        # (MECH75: the other groups' Ignites, for readers that fetch the main skill's stats afresh: assumptions.snap_stats)
        self.build.ailment_others = dict(stats.get("_ailment_others") or {})
        self.build.ailment_ground = dict(stats.get("_ailment_ground") or {})  # (MECH75: Radiant Grief's aura)
        # (MECH76, for COV-A: "Enemies standing on Chilled Ground take ..."; (share, how), (0.0, "") without a source)
        self.build.chilled_ground_up = stats.get("_chilled_ground") or (0.0, "")
        # (MECH79, for COV-A: Desert's Scorn's "Enemies standing on Ignited Ground take 25% increased Cold Damage")
        self.build.ignited_ground_up = stats.get("_ignited_ground") or (0.0, "")
        # (MECH79, models.compound_ignite_mods) the main skill's Compounding Ignite: (skill, factor, how) or None
        self.build.compound_ignite = ((self.main_skill, *stats["_compound"]) if stats.get("_compound") else None)
        self.build.uruk_taken = stats.get("_uruk") or (0.0, "")  # (MECH80: Uruk's Smelting, models.uruk_mods)
        self.build.stormfire = stats.get("_stormfire") or (0.0, "")  # (MECH81: Stormfire, models.stormfire_mods)
        self.build.flammability_scale = stats.get("_flammability_scale") or (1.0, "")  # (MECH81)
        self.build.oil_ignite = list(stats.get("_oil_ignite") or [])  # (MECH82: lit Oil Ground, models.oil_ignite_model)
        self.build.ground_life_ignite = list(stats.get("_ground_life_ignite") or [])  # (MECH83: ground_life_ignite_model)
        # (MECH78, MC-1; for COV-A's OpenWeakness ledger condition, the manager 12:43: one share for both) the share of
        # the fight's TIME the boss has an Open Weakness: open_weakness_up(build)
        self.build.open_weakness_up = stats.get("_open_weakness") or (0.0, "")
        # (MECH70, for GEM5's Vaulting Impact Broken Stance model: the share of the fight the boss is Dazed and its basis,
        # read whether or not the box is on; (0.0, "") without a Daze source. With no consumer of Daze (_dazed's note),
        # it is also the share of any later hit that finds the boss Dazed.)
        dazed = found.get("conditionEnemyDazed")
        self.build.dazed_up = (number(uptimes.get("conditionEnemyDazed")), dazed[1] if dazed else "")
        # Charges held: PoB reads an override of 0 as "not set" (the maximum), so none held switches the "use" box off
        for kind in ("Power", "Frenzy", "Endurance"):
            held = found.get(f"override{kind}Charges")
            if held is not None and not held[0]:
                found.pop(f"override{kind}Charges")
                if f"use{kind}Charges" in found:
                    found[f"use{kind}Charges"] = (False, "sources, but none a boss fight feeds: " + held[1])
            elif held is None:
                pass
            elif f"use{kind}Charges" not in found:
                if CHARGE_REDIRECTED in held[1]:  # (its sources name another kind: Resonance)
                    found[f"use{kind}Charges"] = (True, held[1])
                else:
                    found.pop(f"override{kind}Charges")  # (no source the resolver accepts for the box itself)
        return found

    def roar_evidence(self) -> tuple[bool, str, float] | None:
        """Ferocious Roar used on its cooldown, its Empowered hits Breaking the boss's Armour (models.roar_break): PoB
        reads none of it. Evidence only if the main skill is an Attack and the Armour stays Fully Broken."""
        from builder.search import models

        if "Attack" not in self.main_types:
            return None
        raw = self.build.call("activeSkills", levelStats=[models.FEROCIOUS_ROAR])
        skill, warcry = models.roar([models.Skill.of(s) for s in raw["skills"] if not s["disabled"]])
        if skill is None or warcry:
            return None
        texts = [line["text"] for line in self.build.call("sourceLines")["lines"]]
        power = models.boss_power(texts, self.build.call("stats", fields=["PresenceRadius", "Speed", "HitChance"]),
                                  number(self.build.call("getConfig")["input"].get("enemyDistance")) or None, True,
                                  self.main_skill)[0]
        share, basis = models.roar_break(skill, number(raw.get("enemyArmour")), models.full_break_duration(texts), power)
        return (True, f"gem:{models.FEROCIOUS_ROAR}: {basis} ({shared(share)})", share) if share >= MIN_UPTIME else None

    def resolve(self) -> bool:
        """Match the config to the evidence. Returns True if anything changed."""
        found = self.evidence()
        for var, up in self.found_uptimes.items():  # (SWITCH_ON: off ones switch on only from 7% of the fight)
            if var not in self.active and up < SWITCH_ON and var in found and found[var][0] is not False:
                del found[var]
        changes: dict[str, object] = {}
        for condition in CONDITIONS:
            want = found.get(condition.var)
            if (want is not None and not isinstance(want[0], bool) and isinstance(want[0], (int, float))
                    and want[0] <= 0 and condition.var in pob_check_vars()):
                want = (False, want[1])  # (MECH86: a 0 on a PoB check box is off, not Lua's truthy 0; pob_check_vars)
            if want is not None and want[0] is False:
                # explicitly off (charges whose sources a boss fight doesn't feed): switch it off and don't list it as on
                self.off_reasons = getattr(self, "off_reasons", {})
                self.off_reasons[condition.var] = want[1]
                was_on = self.active.pop(condition.var, None) is not None
                if was_on or condition.var not in getattr(self, "switched_off", set()):
                    changes[condition.var] = False
                    self.switched_off = getattr(self, "switched_off", set()) | {condition.var}
                continue
            have = self.active.get(condition.var)
            if want is None and have is not None:
                changes[condition.var] = False
                del self.active[condition.var]
            elif want is not None and (have is None or have["value"] != want[0]):
                changes[condition.var] = want[0]
            if want is not None:
                self.active[condition.var] = {"label": condition.label, "value": want[0], "evidence": want[1]}
                if condition.var in getattr(self, "off_values", {}):
                    self.active[condition.var]["off"] = self.off_values[condition.var]
        # Effects on for part of the fight, weighed into the headline by Search.weigh_uptimes
        self.uptimes = {var: up for var, up in self.found_uptimes.items() if var in self.active and up < FULL_UPTIME}
        # The Frozen box's share for the boss's action speed (the bridge's Freeze: a stop for that share, not PoB's steady
        # -70%; MECH55, MECHD3's note): written when it moves by a point or more, cleared with the box
        frozen = self.active.get("conditionEnemyFrozen")
        share = round(min(self.found_uptimes.get("conditionEnemyFrozen", 1.0), 1.0), 2) if frozen else None
        if share != getattr(self, "freeze_share", None):
            changes[FREEZE_SHARE_INPUT] = share if share is not None else False
            self.freeze_share = share
        if changes:
            self.build.set_config(changes)
        return bool(changes)

    def could_source(self, condition: Condition, text: str) -> bool:
        """Could something with this text be the condition's source? Text rules are checked
        exactly; for conditions read from PoB's numbers (shock chance, crit chance) a mention of
        the effect is enough to deserve a real trial."""
        if condition.text and text_evidence(condition, text) and qualifies(text, self.main_types):
            return True
        element = condition.var[len("conditionEnemy"):-len("Exposure")]
        if condition.var.endswith("Exposure") and element in EXPOSURE_ELEMENTS and (
                _exposure_match(text, element) or (text.startswith("Supports ") and EXPOSURE_WORD.search(text))):
            return True  # (_exposure_uptime rates it in the real trial; a support through its stats)
        if condition.var in DEBUFFS and _debuff_pattern(condition.var).search(text) and not DEBUFF_NOT_SOURCE.search(text):
            return True  # (debuff_uptime rates it in the real trial)
        if condition.var == "buffArcaneSurge" and (
                (ARCANE_SURGE_SOURCE.search(text) and not ARCANE_SURGE_NOT_SOURCE.search(text))
                or "spending enough mana grants" in text.lower()):
            return True  # (arcane_surge_uptime rates it in the real trial; the support by its description)
        if condition.var == "conditionEnemyArmourBroken" and ARMOUR_BREAK_LINE.search(text):
            return True  # (armour_broken_uptime rates it in the real trial)
        if condition.var == "conditionStationary" and STATIONARY_LINE.search(text):
            return True  # (a stationary line gets a real trial: the resolver stands the build still with it)
        if (kind := CHARGE_OVERRIDES.get(condition.var)) is not None:  # a generator of that charge a boss fight feeds
            rate = _charge_rate(text, kind, {"Speed": 1.0, "HitChance": 100, "CritChance": 50, "EffectiveBlockChance": 30,
                                             "FreezeBuildupAvg": 10, "ElectrocuteBuildupAvg": 10,
                                             "HeavyStunBuildupAvg": 10, "PhysicalHitAverage": 1,
                                             "IgniteChancePerHit": 50, "_main_types": {"Attack"}})
            return rate is not None and rate[0] > 0
        return condition.stat is not None and any(
            word.lower() in text.lower() for word in TRIAL_WORDS.get(condition.var, ()))

    def would_enable(self, text: str) -> bool:
        """Could adding something with this text turn on a condition that's off now, or raise a charge count the build
        holds? (Charge counts are Configuration values a plain what-if can't see: such a gem gets a real trial.)"""
        return any(self.could_source(c, text) for c in CONDITIONS
                   if c.var not in self.active or c.var in CHARGE_OVERRIDES)

    def allows(self, stat_set: str | None, assume: tuple[str, ...] = ()) -> bool:
        """May PoB calculate this stat set (STAT_SET_NEEDS)? `assume` counts as active, for what-ifs. Never a set PoB
        scores as Hits the game doesn't deal (NO_HIT_STAT_SETS), on the main group's skills that carry it."""
        if stat_set in NO_HIT_STAT_SETS:
            group = self.build.main_group() or {}
            holders = {s["name"] for s in group.get("skills") or [] if stat_set in (s.get("statSets") or [])}
            if holders and holders <= NO_HIT_STAT_SETS[stat_set]:
                return False
        return all(var is not None and (var in self.active or var in assume) for var in stat_set_needs(stat_set))

    def sync(self) -> None:
        """After a reload: what the config has on is what's active, so the next resolve can turn a
        restored build's conditions off again when their source is gone."""
        config = self.build.call("getConfig")["input"]
        active = {}
        for condition in CONDITIONS:
            value = config.get(condition.var)
            if value not in (None, False, 0):
                old = self.active.get(condition.var) or {}
                active[condition.var] = {"label": condition.label, "value": value,
                                         "evidence": old.get("evidence", "restored")}
        self.active = active
        metres = number(config.get("enemyDistance")) / 10
        if self.distance and metres != self.distance[0]:  # (a restored build stands where its code says)
            self.distance = None if metres == natural_distance(self.main_types, self.main_skill) or not metres else (metres, "restored")

    def describe(self) -> list[dict]:
        return [{"var": var, **info} for var, info in sorted(self.active.items())]

    # Quest rewards -----------------------------------------------------------------------------

    def choose_quests(self) -> bool:
        """One pass of coordinate ascent over the campaign's reward choices."""
        build = self.build
        improved = False
        for quest in build.call("questOptions")["quests"]:
            current = quest.get("current") or "None"
            best = (build.score, current)
            for option in quest["options"]:
                if option == current:
                    continue
                build.set_config({quest["var"]: option})
                if build.score > best[0] + EPSILON:
                    best = (build.score, option)
            if build.call("getConfig")["input"].get(quest["var"]) != best[1]:
                build.set_config({quest["var"]: best[1]})
            improved = improved or best[1] != current
        return improved

    def quests(self) -> dict[str, str]:
        config = self.build.call("getConfig")["input"]
        return {q["label"]: config.get(q["var"]) or "None" for q in self.build.call("questOptions")["quests"]}
