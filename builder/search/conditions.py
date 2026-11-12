"""Condition resolver (PLAN.md §5.4): turn a PoB config condition on only when the build has a
real source for it, and off again when the source goes away.

Evidence is either text (a passive, item line or gem description that generates the effect,
e.g. "Gain a Frenzy Charge on Critical Hit") or PoB's own numbers (a chance to shock high enough
to keep a boss shocked). The table is data; the ledger can extend it later. Conditions a boss
fight doesn't sustain (stun, freeze, electrocute, daze, pin) are never turned on.

Campaign quest rewards that are a choice (e.g. +5% Cold Resistance or +5 Intelligence) are free
stats every character picks, so they're chosen here too, by score.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search import boss_hits, models
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
]
RECENTLY = 4.0  # seconds ("Recently": the past 4 seconds)


def _hit_recently(stats: dict) -> bool:
    """At least one boss hit lands in any 4 s with chance UPTIME (landing hits come one after another, about every 0.7 s)."""
    every = number(stats.get("enemySkillTime")) or 0.7
    land = boss_hits.land_share(stats)  # (red-flash hits always land, MECHC5)
    return 1 - (1 - land) ** (RECENTLY / every) >= UPTIME


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
                 "MainHand.ChaosHitAverage", "EnemyAilmentThreshold")
# Lines that inflict Withered but aren't counted (labelled in the basis): on entering your Presence (once on a lone boss),
# minions' chance, against Abyssal Wasting, on curse
WITHER_UNCOUNTED = re.compile(r"withered .*when they enter your presence|minions have .*withered|abyssal wasting.*|"
                              r"withered with hits against|curse .*withered|withered .*curse", re.I)


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
    stacks, sources, idle = 0.0, [], []
    for text in lines:
        if m := WITHER_CHANCE.search(text):
            if WITHER_SPELLS.search(text) and not spell:
                idle.append(f"{text} (the main skill isn't a Spell)")
                continue
            stacks += float(m.group(1)) / 100 * rate * float(m.group(2))
            sources.append(text)
        elif WITHER_UNCOUNTED.search(text):
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
    left = f"; not counted: {'; '.join(t[:70] for t in idle)}" if idle else ""
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
# sources; on a single boss nothing dies, so on-kill sources give none. A source whose rate its line doesn't say (on use,
# when you stun, on reaching something) is assumed once per 4 s, labelled.
CHARGE_DEFAULT_EVERY = 4.0
# Events a line names without its rate (FLAT_RATES A3; MECH47): a Warcry used, a Totem summoned, a Mark activating.
# Before, each fell to one per CHARGE_DEFAULT_EVERY s whether or not the build had such a skill (an overcount with
# none). Now the rate comes from the build's own skills (event_rates, in Conditions.evidence as stats["_events"]): the
# main skill's uses if it is one; else each such skill's group at its use rate (bridge useRates: its cooldown, else its
# duration, else models.PRESS_EVERY, LOW); none of that kind in the build's enabled groups: 0, labelled. A Mark is taken
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


def skill_use_rate(rate: dict) -> tuple[float, str, bool]:
    """(uses per second, why, LOW?) of a non-main skill from the bridge's useRates entry for its group: on its cooldown,
    else once per its duration, else once every models.PRESS_EVERY s (LOW), never faster than its own speed."""
    cooldown, duration = number(rate.get("cooldown")), number(rate.get("duration"))
    repeats = number(rate.get("repeats")) or 1.0
    low = False
    if cooldown > 0:
        each, why = repeats / cooldown, f"its {cooldown:.2f} s cooldown"
    elif duration > 0:
        each, why = repeats / duration, f"once per its {duration:.2f} s duration"
    else:
        each, why, low = repeats / models.PRESS_EVERY, f"once every {models.PRESS_EVERY:g} s (LOW: no cooldown or duration)", True
    if (speed_g := number(rate.get("speed"))) > 0:
        each = min(each, speed_g)
    return each, why, low


def event_rates(groups: list[dict], gems: dict[str, dict], main_skill: str, main_types: set[str], speed: float,
                use_rates: Callable[[list[int]], list[dict]], texts: list[str]) -> dict[str, tuple[float, str]]:
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
            each, why, guess = skill_use_rate(rate)
            low = low or guess
            total += each
            parts.append(f"{name} on {why}, {each:.2f}/s")
        out[kind] = (total, "; ".join(parts) + (" (LOW)" if low else " (MEDIUM: used on cooldown or to keep it up)"))
    return out


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


def _charge_rate(text: str, kind: str, stats: dict) -> tuple[float, str] | None:
    """(charges per second, how) from one line, or None if it isn't a source of that charge."""
    m = re.search(CHARGE_SOURCE.format(kind=kind), text, re.I)
    if not m or re.search(r"\bcannot gain\b|instead|allies|when you gain|if you would gain|per \w+ charge|lose|at maximum \w+ charges", text, re.I):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    chance *= float(m.group("count") or 1)  # ("Gain 3 Power Charges on use": three per event; MECH48, was one)
    when = m.group(3).lower()
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    crits = hits * number(stats.get("CritChance")) / 100
    boss = 1 / (number(stats.get("enemySkillTime")) or 0.7)
    if "kill" in when:
        return 0.0, "on kill (nothing dies on a single boss)"
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
        return chance * boss * 0.05, "when the boss crits you (5% of its hits)"
    if "block" in when:
        block = boss_hits.block_share(stats, evaded_first=False)  # (red-flash hits can't be Blocked, MECHC5)
        return chance * boss * block, f"{chance:.0%} x {boss * block:.2f} blocks/s"
    if "on hit" in when or when.strip() in ("", "on hit") or "hit" in when:
        return chance * hits, f"{chance:.0%} x {hits:.2f} hits/s"
    if m2 := re.search(r"every (\d+(?:\.\d+)?)? ?seconds?|once per second|every second", when):
        every = float(m2.group(1)) if m2.groups() and m2.group(1) else 1.0
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
    return chance / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed (LOW)"


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


def natural_distance(main_types: set[str]) -> float:
    """The distance in metres a build stands at when nothing it has depends on it."""
    return MELEE_DISTANCE if "Melee" in main_types or main_types & NEAR_TYPES else RANGED_DISTANCE


def distance_candidates(lines: list[str], main_types: set[str], presence_radius: float = 0.0) -> list[float]:
    """Distances worth a trial: the natural one first, close range, and just beyond any "further than N m" line. A build
    standing back with "in your Presence" lines also tries the edge of its Presence (presence_radius, metres), when that
    lies between close range and its natural distance: the boss's Accuracy against you falls with distance (CalcDefence;
    KeywordPopups Accuracy), so the build keeps the boss in its Presence as far away as it reaches, and Presence Area of
    Effect lines (PoB's PresenceArea, CalcPerform calcBuffRadius) decide how far that is (MECHC1, 12 Nov 2026: the user's
    Presence answer of 11 Nov, the player moves so the boss is in or out of the Presence as needed)."""
    natural = natural_distance(main_types)
    found = [natural]
    for text in lines:
        if m := DISTANCE_LINE.search(text):
            found.append(MELEE_DISTANCE)
            if m.group(1):
                found.append(float(m.group(1)) + 0.5)
            edge = math.floor(presence_radius * 10) / 10
            if PRESENCE_LINE.search(text) and MELEE_DISTANCE < edge < natural:
                found.append(edge)
    found = list(dict.fromkeys(found))
    return found if len(found) > 1 else []


# Lines that depend on standing still (PoB's conditionStationary). A build with one has standing still chosen by score
# (Search.choose_stationary), unless its dodge rolls keep it moving more than 1 - UPTIME of the time.
STATIONARY_LINE = re.compile(r"\bstationary\b|\bstand(?:ing)? still\b", re.I)


LOW_LIFE_LINE = re.compile(r"low life", re.I)  # (the player's own Low Life lines match too: the boss's then changes nothing)
LOW_LIFE_SHARE = 0.35  # "A player or monster is on Low Life if it has 35% of its Maximum Life or less" (KeywordPopups)


def _standing_still(lines: list[str], stats: dict) -> tuple[float, str]:
    """On for a build with a stationary line, unless Search.choose_stationary switched it off (its dodge rolls, or moving
    scored better): a real trial of a new stationary line sees its gain."""
    if stats.get("_stationary_off") or not any(STATIONARY_LINE.search(t) for t in lines):
        return 0.0, ""
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


def _charges_held(kind: str):
    def held(lines: list[str], stats: dict) -> tuple[float, str]:
        kinds, redirected = _charge_sources(lines, kind)
        allies = next((t for t in lines if (m := CHARGE_TO_ALLIES.search(t))
                       and (m.group(1) is None or m.group(1).capitalize() == kind)), None)
        if allies is not None:  # (a basis with 0 switches the use box off: Conditions.resolve)
            sourced = any(_charge_rate(t, k, stats) is not None for t in lines for k in {*kinds, kind})
            return 0.0, (f"'{allies}': allies in your Presence gain the {kind} Charges instead (Conduit)" if sourced else "")
        gains, bonus, how = 0.0, 0.0, []
        profusion = stats.get("_profusion") or {}  # gem description -> (chance of one more, of a random one)
        for text in lines:
            if (r := next((x for k in kinds if (x := _charge_rate(text, k, stats)) is not None), None)) is not None:
                more = profusion.get(text)
                plus = (more[0] + more[1] / 3) if more else 0.0
                gains += r[0]
                bonus += r[0] * plus
                how.append(f"{text[:60]} ({r[1]}" + (f"; Charge Profusion +{plus:.0%} per gain" if more else "") + ")")
        if not how:  # (its own sources feeding another kind: Resonance; a basis with 0 switches the use box off)
            own = redirected and any(_charge_rate(t, kind, stats) is not None for t in lines)
            return 0.0, (redirected if own else "")
        extra, fill, extra_how = _extra_charges(lines, kind)
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
            net = max(rate - spent, 0.0)
            count = min(most, net * duration)
            share = min(1.0, rate / spent)
            basis = (f"{gains:.2f} gains/s{extra_text} = {rate:.2f}/s made, {spent:.2f}/s spent by " +
                     ", ".join(stats["_consumers"][kind]) +
                     f" (one per use; {share:.0%} of their uses find a charge), {net:.2f}/s left x {duration:g} s")
            if count <= 0 and share >= 0.5:
                count = 0.1  # (held for an instant before it's spent: enough for PoB to count the charged use)
            if fill:
                basis += f"; {fill:.0%} chance a gain fills to the maximum: not counted beside a consumer of the charge"
        else:
            count = charges_with_refresh(gains, duration, most, extra, fill)
            basis = (f"{gains:.2f} gains/s{extra_text}, all dropping after {duration:g} s without a gain (gains taken as "
                     f"random arrivals)" + (f"; {fill:.0%} chance a gain fills to the maximum" if fill else ""))
        return round(count, 1), ((redirected + "; ") if redirected else "") + (
            f"{basis} = {count:.1f} held, at most {most:g}: " + "; ".join(how[:3]))
    return held


CHARGE_OVERRIDES = {f"override{kind}Charges": kind for kind in ("Power", "Frenzy", "Endurance")}


# Rage held (MECH27, 9 Nov 2026; before, any Rage source set the maximum, on-kill ones too). The game files' KeywordPopups
# Rage (build 26476662): "you have 30 maximum Rage and lose 1 Rage every 0.2 seconds. Rage loss is paused for 4 seconds
# upon gaining Rage, or after taking damage. Only one Hit every 0.5 seconds can cause you to gain Rage." Gains come from
# the build's lines (every "gain # Rage" wording of the stat descriptions; nothing dies on a single boss, so on-kill gains
# are 0), spends from the skills that spend it (GrantedEffectStatSets / GrantedEffectsPerLevel costs below). The maximum
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
RAGE_PER_USE = {"Molten Crash": 10, "Furious Slam": 10, "Fury of the Mountain": 10, "Rageforged I": 10, "Rageforged II": 10}
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
    every = number(stats.get("enemySkillTime")) or 0.7
    land = boss_hits.land_share(stats) / every  # (red-flash hits always land, MECHC5)
    if "kill" in when:
        return 0.0, 0.0, "on kill (nothing dies on a single boss)"
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
    every = number(stats.get("enemySkillTime")) or 0.7
    land = boss_hits.land_share(stats) / every  # (red-flash hits always land, MECHC5)
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
        # Grounds: 0, you step out of them, as for ES.)
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
    held = float(int(held))
    return held, f"{basis} = {held:.0f} held, at most {most:g}: " + "; ".join(how[:3])


# Onslaught's uptime (MECH27, 9 Nov 2026; before, any line that gains Onslaught turned it on, on-kill ones, Companions'
# and "grant Onslaught to nearby Enemies" too). The game files' KeywordPopups Onslaught: "20% increased Skill Speed ...
# Unless specified, Onslaught lasts 4 seconds." Each source's uptime is the chance at least one of its events falls in
# the last duration (1 - (1 - chance)^(events x duration)); sources combine as independent; PoB's box is on at UPTIME
# (90%, as for ailments and flasks). Events: hits, crits from the main skill; the boss's landing hits for "when Hit"; on
# kill / Culling Strike 0 (nothing dies before the boss); a Minion dying 0 and a Vaal skill's use 0 (rates not modelled,
# labelled); Marks activating once per CHARGE_DEFAULT_EVERY s (assumed); "during effect" (a flask or charm) on (the flask
# model decides whether the flask counts). "While at maximum Frenzy Charges": only when the Frenzy Charges held reach it.
ONSLAUGHT_DURATION = 4.0
ONSLAUGHT_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\b(?:you )?(?:gain|grants?)\b (?:you )?Onslaught\b(.*)$", re.I)
ONSLAUGHT_NOT_SOURCE = re.compile(r"companions|minions (?:have|gain)|onslaught to (?:nearby )?enemies|enemies gain|\ballies\b|"
                                  r"grants skill|per onslaught|"
                                  r"while you have onslaught|instead", re.I)


def _onslaught_source(text: str, lines: list[str], stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line, or None if it isn't a source of Onslaught for you."""
    m = ONSLAUGHT_SOURCE.search(text)
    if not m or ONSLAUGHT_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(2).lower()
    d = re.search(r"for (\d+(?:\.\d+)?) seconds?", when)
    duration = float(d.group(1)) if d else ONSLAUGHT_DURATION
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    every = number(stats.get("enemySkillTime")) or 0.7
    land = boss_hits.land_share(stats) / every  # (red-flash hits always land, MECHC5)
    if re.search(r"\bkill|culling strike", when):
        return 0.0, "on kill (nothing dies on a single boss)"
    if "vaal skill" in when:
        return 0.0, f"'{when.strip()[:30]}': its rate isn't modelled (0, labelled)"
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
    return _box(up, basis)


# Arcane Surge's uptime (MECH29, 9 Nov 2026; before, any line that gains it turned it on, crit-chance and Minion-death
# ones too). The game files' KeywordPopups ArcaneSurgeDuration: "It lasts for 4 seconds by default." Each source's uptime
# as Onslaught's (1 - (1 - chance)^(events x duration)), combined as independent; PoB's box is on at UPTIME. Events: the
# main skill's crits and hits ("with Spells": QUALIFIERS needs a Spell); "if you have at least N Devotion" by PoB's
# Devotion; "while at maximum Power Charges" only when the Power Charges held reach it; "after Channelling for 1 second"
# when the main skill channels; a Minion dying at the build's own minion deaths (MECHC7: _minion_death_uptime); a kill,
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
    if re.search(r"\bkill|minion|shapeshift|\bmines?\b|\btraps?\b", when):
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
# crits; the boss's hits that land ("when they Hit you") or that you Block ("Permanently ... on Block": on with any
# Block); "Enemies in your Presence" while the boss stands inside it (the build's distance); "Enemies you Curse" with a
# curse gem in the build (the Cursed box's own evidence); "while at maximum Endurance Charges" by the charges held;
# "Enemies you Fully Armour Break" at armour_broken's Full Break rate. A
# kill, Heavy Stun, Immobilise, Shapeshift, Burning, a socketed jewel or a gem's own effect: rate not modelled (0,
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
}
DEBUFF_NOT_SOURCE = re.compile(r"\b(?:increased|reduced|more|less)\b|\bagainst\b|\bcannot\b|immun|\byou are (?!burning)|"
                               r"\ballies\b|minions|companions|summoned|grants skill|\bif you'?ve\b", re.I)


def curse_uses(build: Build, stats: dict, texts: list[str], metres: float | None = None) -> list[models.CurseUse]:
    """The build's curses and how it applies each (models.curse_uses), the boss at `metres` or the config's distance."""
    if metres is None:
        metres = number(build.call("getConfig")["input"].get("enemyDistance")) / 10 or natural_distance(set())
    return models.curse_uses(build.groups(), build.ctx.gems, stats, metres, texts, getattr(build, "trigger_rates", None))


def _debuff_pattern(var: str) -> re.Pattern:
    verb, participle = DEBUFFS[var]
    target = r"(?:rare or unique )?(?:enemies|targets|them)"
    where = f" {target} in {participle.split(' in ')[1]}" if " in " in participle else f"(?: {target})?"
    return re.compile(rf"(?:(\d+(?:\.\d+)?)% (?:global )?chance to )?\b(?:permanently )?{verb}\b{where}(.*)$|"
                      rf"\benemies\b(.*?)\b(?:are|become) {participle}\b(.*)$", re.I)


def _debuff_source(text: str, var: str, lines: list[str], stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line for this debuff, or None if it isn't a source of it."""
    m = _debuff_pattern(var).search(text)
    if not m or DEBUFF_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = (m.group(2) if m.group(2) is not None else f"{m.group(3)} {m.group(4)}").lower()
    d = re.search(r"for (\d+(?:\.\d+)?) seconds?", when)
    duration = (float(d.group(1)) if d else DEBUFF_DURATION) * models.duration_scale(lines, DEBUFFS[var][0])
    every = number(stats.get("enemySkillTime")) or 0.7
    block = min(number(stats.get("EffectiveBlockChance")), 100) / 100
    if "presence" in when:
        metres = (stats.get("_distance_choice") or (natural_distance(stats.get("_main_types") or set()), ""))[0]
        radius = number(stats.get("PresenceRadius")) / 10
        up = 1.0 if radius >= metres else 0.0
        return up, f"the boss {metres:g} m away, your Presence {radius:.1f} m"
    if "you curse" in when:
        return models.curse_share(stats.get("_curse_uses") or [])
    if "fully armour break" in when or "fully break" in when:  # (armour_broken's Full Breaks, as Exposure's)
        events = number(stats.get("_full_break_rate"))
        up = 1 - (1 - min(chance, 1.0)) ** (events * duration) if chance < 1 else min(1.0, events * duration)
        return up, f"{events:.2f} Full Breaks of the boss's Armour/s, {duration:g} s: {up:.0%} up"
    if re.search(r"\bkill|heavy stun|immobilis|shapeshift|burning|socketed|\bfrozen by\b", when):
        return 0.0, f"'{when.strip()[:30]}': its rate isn't modelled (0, labelled)"
    if "maximum endurance" in when:
        held = _charges_held("Endurance")(lines, stats)[0]
        most = number(stats.get("EnduranceChargesMax"))
        if most <= 0 or held < most:
            return 0.0, f"needs maximum Endurance Charges ({held:g} of {most:g} held)"
    if "block" in when:
        events, how = block / every, "the boss's hits you Block"
        if "permanently" in text.lower():
            return (1.0, "permanent after a Block") if events > 0 else (0.0, "no Block chance")
    elif "hit you" in when:
        land = ((1 - min(number(stats.get("EvadeChance")), 100) / 100) * (1 - block)) / every
        events, how = land, "the boss's hits that land"
    elif "critical" in when:
        events, how = models.crits_per_second(stats), "crits"
    elif "on hit" in when:
        events, how = models.hits_per_second(stats), "hits"
    else:
        return 0.0, f"'{when.strip()[:40]}': its rate isn't modelled (0, labelled)"
    n = events * duration
    up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
    return up, f"{chance:.0%} x {events:.2f} {how}/s, {duration:g} s: {up:.0%} up"


def debuff_uptime(var: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def uptime(lines: list[str], stats: dict) -> tuple[object, str]:
        main_types = stats.get("_main_types") or set()
        down, how = 1.0, []
        for text in lines:
            if not qualifies(text, main_types) or (r := _debuff_source(text, var, lines, stats)) is None:
                continue
            down *= 1 - r[0]
            how.append(f"{text[:60]} ({r[1]})")
        if not how:
            return 0.0, ""
        up = 1 - down
        name = DEBUFFS[var][1].capitalize()
        basis = f"{name} up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
        return _box(up, basis)
    return uptime


# Armour Broken (MECH28, 9 Nov 2026; before, any line that Breaks Armour turned the box on, partial breaks too). The game
# files' KeywordPopups ArmourBreak: Breaking lowers a target's Armour; at 0 it is Fully Broken for 12 seconds (non-players;
# then +20% Physical damage taken from Hits, which is what PoB's box applies). Each line's Armour broken per second, from
# the main skill's hits (PoB's average hit and crit per damage type), against the boss's Armour (PoB's enemy Armour): it
# takes armour / rate s to Fully Break, then 12 s Broken, so up 12 / (12 + that). Partial Break assumed not to wear off
# before it's full (the game files give it no duration). Full Breaks on Heavy Stun at heavy_stun_rate; "Break #% of Armour
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
    if not how:
        return 0.0, ""
    rate += share * armour
    to_break = 0.0 if armour <= 0 else (armour / rate if rate > 0 else float("inf"))
    held = models.full_break_duration(lines)  # (12 s x the Armour Break Duration lines)
    up = held / (held + to_break)
    up = 1 - (1 - up) * (1 - min(1.0, full * held))
    return up, (f"Armour Fully Broken {up:.0%} of the fight ({shared(up)}): {rate:,.0f} Armour broken/s against "
                f"the boss's {armour:,.0f} (PoB), {held:g} s each Full Break: " + "; ".join(how[:3]))


def armour_broken_share(lines: list[str], stats: dict) -> float:
    """Share of the fight the boss's Armour is Fully Broken (one Full Break per 12 s of it)."""
    return _armour_broken(lines, stats)[0]


def armour_broken_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    """(True / False, basis) for the Armour Broken box, or (0.0, "") if no line Breaks Armour for you."""
    up, basis = _armour_broken(lines, stats)
    if not basis:
        return 0.0, ""
    return _box(up, basis)


# Exposure's uptime (MECH28, 9 Nov 2026; before, any line inflicting it turned the box on). The game files' KeywordPopups
# Exposure: -20% to the element's Resistance, 4 seconds by default (PoB's box applies the -20%, and only when PoB
# itself sees a source: CanApply<Element>Exposure). Each line's uptime is the chance at least one of its events falls in
# the last 4 s (1 - (1 - chance)^(events x 4)); "Elemental Exposure" counts for all three; sources combine as
# independent; the box is on at UPTIME, else explicitly off with its basis. Events: the main skill's hits, crits,
# Shocks / Ignites (PoB's chance per hit), Fully Breaking Armour (armour_broken_share's Full Breaks), "to Enemies 3
# metres in front of you" while the boss stands that close (the build's distance). A line's own condition (Devotion,
# socketed jewels) is left to PoB, which applies the box only with a source it accepts. Exposure duration modifiers
# aren't read (4 s, labelled).
EXPOSURE_DURATION = 4.0
EXPOSURE_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\b(?:inflict|apply|cause)s? (fire|cold|lightning|elemental) exposure\b(.*)$", re.I)


def _exposure_source(text: str, element: str, stats: dict) -> tuple[float, str] | None:
    """(uptime, how) of one line for `element`'s Exposure, or None if it isn't a source of it."""
    m = EXPOSURE_SOURCE.search(text)
    if not m or m.group(2).lower() not in (element.lower(), "elemental"):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(3).lower()
    hits = models.hits_per_second(stats)
    if "fully breaks armour" in when:
        events, how = number(stats.get("_full_break_rate")), "Full Breaks of the boss's Armour"
    elif near := re.search(r"(\d+(?:\.\d+)?) metres in front", when):
        metres = (stats.get("_distance_choice") or (natural_distance(stats.get("_main_types") or set()), ""))[0]
        up = 1.0 if metres <= float(near.group(1)) else 0.0
        return up, f"the boss {metres:g} m away ({near.group(1)} m needed)"
    elif "shock" in when:
        events, how = hits * min(number(stats.get("ShockChancePerHit")), 100) / 100, "Shocks (PoB's chance per hit)"
    elif "ignit" in when:
        events, how = hits * min(number(stats.get("IgniteChancePerHit")), 100) / 100, "Ignites (PoB's chance per hit)"
    elif "critical" in when:
        events, how = models.crits_per_second(stats), "crits"
    elif "on hit" in when:
        events, how = hits, "hits"
    else:
        return 0.0, f"'{when.strip()[:40]}': its rate isn't modelled (0, labelled)"
    n = events * EXPOSURE_DURATION
    up = 1 - (1 - min(chance, 1.0)) ** n if chance < 1 else min(1.0, n)
    return up, f"{chance:.0%} x {events:.2f} {how}/s, {EXPOSURE_DURATION:g} s: {up:.0%} up"


def _exposure_uptime(element: str) -> Callable[[list[str], dict], tuple[object, str]]:
    def uptime(lines: list[str], stats: dict) -> tuple[object, str]:
        main_types = stats.get("_main_types") or set()
        down, how = 1.0, []
        for text in lines:
            if not qualifies(text, main_types) or (r := _exposure_source(text, element, stats)) is None:
                continue
            down *= 1 - r[0]
            how.append(f"{text[:60]} ({r[1]})")
        if not how:
            return 0.0, ""
        up = 1 - down
        basis = f"{element} Exposure up {up:.0%} of the fight ({shared(up)}): " + "; ".join(how[:3])
        return _box(up, basis)
    return uptime


def _blocked_recently(stats: dict) -> bool:
    every = number(stats.get("enemySkillTime")) or 0.7
    block = boss_hits.block_share(stats, evaded_first=False)  # (red-flash hits can't be Blocked, MECHC5)
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
    (hits that land: uses per second x hit chance)."""
    chance = min(number(stats.get(f"{ailment}ChancePerHit")) / 100.0, 1.0)
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100.0) / 100.0
    duration = number(stats.get(f"{ailment}Duration")) or AILMENT_DURATION[ailment]
    if chance <= 0 or hits <= 0:
        return 0.0
    return 1.0 - (1.0 - chance) ** (hits * duration)


def _ailment_up(ailment: str):
    return lambda s: ailment_uptime(s, ailment) >= MIN_UPTIME


def _ailment_share(ailment: str):
    return lambda s: ailment_uptime(s, ailment)


def shared(up: float) -> str:
    """How an uptime is counted, for a basis."""
    if up >= FULL_UPTIME:
        return "counted as always on"
    return "counted at that share" if up >= MIN_UPTIME else "off"


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
SHOCKED_GROUND_SKILL_STATS = ("secondary_skill_shocked_ground_effect_duration_ms", "skill_shocked_ground_effect_duration_ms",
                              "lightning_warp_shocked_ground_base_duration_ms")
SHOCKED_GROUND_SKILLS = ("Storm Lance", "Lightning Warp", "Vivid Stampede", "Gathering Storm")
SHOCKED_GROUND_SUPPORT = "Shocking Leap"
SHOCKED_GROUND_SUPPORT_STATS = ("support_shocking_leap_shocked_ground_duration_ms",)
SHOCK_MAKES_GROUND = re.compile(r"(\d+)% chance on shocking enemies to creat\w* shocked ground", re.I)
GROUND_WHILE_MOVING = re.compile(r"drops? shocked ground while moving, lasting (\d+) seconds", re.I)
ON_SHOCKED_GROUND = re.compile(r"\bon shocked ground\b", re.I)
SHOCKED_GROUND_MAGNITUDE = 20  # the base Shock (PoB's BaseShockMagnitude); the strongest Shock applies (PoB: the max)


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
    return chosen[0] if chosen else natural_distance(stats.get("_main_types") or set())


def shocked_ground(stats: dict, texts: list[str]) -> tuple[float, str]:
    """(share of the fight the boss stands in your Shocked Ground, how). Each ground is taken to be made at the boss and
    the boss to stay in it for its duration (LOW: bosses move, and the ground's radius isn't read). Sources combine as
    independent. Labelled, not counted: Charged Mark's ground (no Mark-activation rate yet), ground from a skill other than
    the main one, and ground duration modifiers."""
    uses = number(stats.get("Speed"))
    hit = min(number(stats.get("HitChance")) or 100.0, 100.0) / 100.0
    parts: list[str] = []
    notes: list[str] = []
    off = 1.0
    if skill := stats.get("_sg_skill"):
        name, ms = skill
        if ms > 0:
            up = _covered(uses, 1.0, ms / 1000)
            parts.append(f"{name}'s own ground on each use ({ms / 1000:g} s, {uses:.2f} uses/s): {up:.0%}")
            off *= 1 - up
        else:
            notes.append(f"{name}'s ground (no duration stat in its level stats: not counted, an undercount)")
    if ms := number(stats.get("_sg_support")):
        crit = min(number(stats.get("CritChance")), 100.0) / 100.0
        up = _covered(uses * hit, crit, ms / 1000)
        parts.append(f"{SHOCKED_GROUND_SUPPORT}, on the first Critical Hit of a use ({crit:.0%} crit, {ms / 1000:g} s): "
                     f"{up:.0%}")
        off *= 1 - up
    for text in texts:
        if m := SHOCK_MAKES_GROUND.search(text):
            shock = min(number(stats.get("ShockChancePerHit")), 100.0) / 100.0
            up = _covered(uses * hit, shock * int(m.group(1)) / 100, SHOCKED_GROUND_DURATION)
            parts.append(f"{m.group(1)}% chance on Shocking ({shock:.0%} Shock chance per hit, {SHOCKED_GROUND_DURATION:g} s "
                         f"by default): {up:.0%}")
            off *= 1 - up
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
    if stats.get("_sg_charged_mark"):
        notes.append("Charged Mark's ground (no Mark-activation rate modelled: not counted, an undercount)")
    if not parts:
        return 0.0, "; ".join(notes)
    return 1.0 - off, "; ".join(parts + notes) + "; each ground taken to be at the boss for its full duration (LOW)"


def shock_share(stats: dict) -> float:
    """The boss Shocked: by hits (ailment_uptime) or by standing in your Shocked Ground, taken as independent."""
    ground = (stats.get("_shocked_ground") or (0.0, ""))[0]
    return 1.0 - (1.0 - ailment_uptime(stats, "Shock")) * (1.0 - ground)


def _ground_shock_effect(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Effect of Shock" box at the ground's base Shock: PoB applies the strongest of it and the hits' own, as the game
    does. Set only with a ground; PoB gates it on the Shocked box, which carries the combined share (shock_share)."""
    share, how = stats.get("_shocked_ground") or (0.0, "")
    if share < MIN_UPTIME:
        return 0.0, ""
    value, basis = ground_shock_magnitude(stats.get("_sg_magnitude"))
    return value, f"the boss stands in your Shocked Ground {share:.0%} of the fight: {how}; its Shock {basis}"


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
    the ground's share, when you stand within SHOCKED_GROUND_RADIUS of the boss. Your own ground counts for "on Shocked
    Ground" (the user, 11 Nov 2026: MEDIUM, not tested in game). Enemy-made ground isn't counted on a lone boss."""
    if not any(ON_SHOCKED_GROUND.search(t) for t in lines):
        return 0.0, ""
    share, how = stats.get("_shocked_ground") or (0.0, "")
    if share < MIN_UPTIME:
        return 0.0, how or "no Shocked Ground of your own (enemy-made ground not counted on a lone boss)"
    if (metres := _boss_metres(stats)) > SHOCKED_GROUND_RADIUS:
        return 0.0, f"you stand {metres:g} m from the boss, outside your Shocked Ground ({SHOCKED_GROUND_RADIUS:g} m assumed)"
    return _box(share, f"in your own Shocked Ground at the boss {share:.0%} of the fight ({shared(share)}): {how}; your own "
                       f"ground counts (the user, 11 Nov 2026; MEDIUM, not tested in game)")


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


def _minion_death_uptime(stats: dict, chance: float, duration: float) -> tuple[float, str]:
    """(uptime, how) of a buff gained for `duration` s on a minion of yours dying, deaths taken as evenly random (LOW)."""
    events, how = _minion_death_events(stats)
    up = 1 - math.exp(-events * duration * min(chance, 1.0))
    return up, f"{chance:.0%} x {how}, {duration:g} s: {up:.0%} up (deaths taken as evenly random, LOW)"


def _minion_died_recently(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Has a Minion Died Recently?" box at the share of the fight one of your minions died in the past 4 s
    (models.minion_deaths; evenly random, LOW). None dying: 0, said."""
    if "_minion_deaths" not in stats:
        return 0.0, ""
    rate, how = _minion_death_events(stats)
    if rate <= 0:
        return 0.0, f"no minion of yours dies against the boss: {how}"
    up = 1.0 - math.exp(-rate * RECENTLY)
    return _box(up, f"one of your minions died in the past {RECENTLY:g} s {up:.0%} of the fight: {how} (LOW: deaths taken "
                    f"as evenly random)")


# A placed Banner (MECHC7, COVC8; usage.py's note: PoB applies the placed banner's buff only under BannerPlanted, which
# only its config box "Is Banner Planted?" sets and the tool never did: an UNDERCOUNT, the buff counted 0). Placing one
# costs 200 Glory (WarBannerPlayer / DefianceBannerPlayer / DreadBannerPlayer: active_skill_requires_X_glory 200, HIGH), so
# it is placed models.glory_uses times a second, and stands for its duration (base_skill_effect_duration 6 s at level 1,
# 9.8 s at 20, game files; PoB's own duration with the build's modifiers, from useRates): the box at min(1, uses x
# duration). The player is taken to stand in its 6 m area (active_skill_base_area_of_effect_radius 60) by the boss (a
# play note). With two or more banners, the lowest share (each banner's buff sits behind the one box: the lower reading).
# Valour (bannerValour) stays PoB's 0 (an undercount, labelled: not modelled yet).
BANNERS = ("War Banner", "Defiance Banner", "Dread Banner")
BANNER_SECONDS = 9.8  # base_skill_effect_duration at level 20 (game files), when PoB gives no duration


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


def _parried(lines: list[str], stats: dict) -> tuple[object, str]:
    """PoB's "Enemy has Parry Debuff" box (50% more Attack damage taken) at the share of the main skill's hits that land on
    a Parried boss (models.parry_plan; MECH39: before, nothing set it, so Parry and Refutation counted 0)."""
    plan = stats.get("_parry_plan")
    if plan is None or not plan.how:
        return 0.0, ""
    if plan.box <= 0:
        return 0.0, plan.how
    return _box(plan.box, f"Parried for {plan.box:.0%} of the main skill's hits ({shared(plan.box)}): {plan.how}")


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
    Condition("buffArcaneSurge", "Arcane Surge", value_fn=lambda lines, stats: arcane_surge_uptime(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "Mana", "ManaPerSecondCost", "Devotion")),
    Condition("multiplierRage", "Rage held", value_fn=lambda lines, stats: rage_held(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "MaximumRage", "enemySkillTime", "EvadeChance")),
    Condition("conditionLeeching", "Leeching", (r"\bleech\w*\b[^.;]*\b(?:life|mana|energy shield)\b",),
              (r"while leeching|if you are leeching|leech\w* is instant",)),
    Condition("conditionCritRecently", "Crit recently",
              stat=lambda s: _uptime({"CritRecently": s.get("CritChance"), "Speed": s.get("Speed")}, "CritRecently"),
              fields=("CritChance", "Speed")),
    Condition("conditionSkillCritRecently", "Skills crit recently",
              stat=lambda s: _uptime({"CritRecently": s.get("CritChance"), "Speed": s.get("Speed")}, "CritRecently"),
              fields=("CritChance", "Speed")),
    Condition("conditionEnemyShocked", "Shocked", stat=lambda s: shock_share(s) >= MIN_UPTIME, uptime_fn=shock_share,
              fields=("ShockChancePerHit", "Speed", "HitChance", "ShockDuration", "CritChance")),
    Condition("conditionShockEffect", "Shock from your Shocked Ground", value_fn=_ground_shock_effect, fields=()),
    Condition("conditionOnShockedGround", "You on your Shocked Ground", value_fn=_on_shocked_ground, fields=()),
    Condition("conditionEnemyIgnited", "Ignited", stat=_ailment_up("Ignite"), uptime_fn=_ailment_share("Ignite"),
              fields=("IgniteChancePerHit", "Speed", "HitChance", "IgniteDuration")),
    Condition("conditionEnemyPoisoned", "Poisoned", stat=_ailment_up("Poison"), uptime_fn=_ailment_share("Poison"),
              fields=("PoisonChancePerHit", "Speed", "HitChance", "PoisonDuration")),
    Condition("conditionEnemyBleeding", "Bleeding", stat=_ailment_up("Bleed"), uptime_fn=_ailment_share("Bleed"),
              fields=("BleedChancePerHit", "Speed", "HitChance", "BleedDuration")),
    Condition("conditionEnemyChilled", "Chilled", stat=lambda s: number(s.get("ChillChanceOnHit")) >= 100,
              fields=("ChillChanceOnHit",)),
    Condition("parryActive", "Parried", value_fn=_parried, fields=("enemySkillTime", "Ward")),
    Condition("conditionEnemyFrozen", "Frozen", value_fn=_immobilised("Freeze", "Frozen"),
              fields=(*models.IMMOBILISE_FIELDS, "FreezeDurationMod")),
    Condition("conditionEnemyElectrocuted", "Electrocuted", value_fn=_immobilised("Electrocute", "Electrocuted"),
              fields=models.IMMOBILISE_FIELDS),
    # (MECH46: "against Pinned enemies" lines at the share the main skill's Pin buildup keeps the boss Pinned; before,
    # nothing set PoB's box, so they counted 0)
    Condition("conditionEnemyPinned", "Pinned", value_fn=_immobilised("Pin", "Pinned"), fields=models.IMMOBILISE_FIELDS),
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
    Condition("bannerPlanted", "Banner placed (its share of the fight from Glory)", value_fn=_banner_planted, fields=()),
    Condition("conditionInfusionConsumedRecently", "Infusion consumed recently", value_fn=_infusion_recently, fields=("Speed",)),
    Condition("buffDivinity", "Divinity (on reaching maximum Divine Charges)", value_fn=_divinity, fields=("Speed", "HitChance")),
    Condition("conditionEnemyArmourBroken", "Armour Broken"),  # (armour_broken_uptime, Conditions.evidence)
    Condition("conditionEnemyCursed", "Cursed", value_fn=lambda lines, stats: _cursed(stats),
              fields=("PresenceRadius",)),
    # Sustained in any boss fight by the build's own numbers or skills (coverage audit item f, 6 Nov 2026: lines needing these
    # counted 0 in every build because only a Configuration option sets them). Boss hit rate: PoB's boss config.
    Condition("conditionUsedSkillRecently", "Used a skill recently", stat=lambda s: True, fields=()),
    Condition("conditionBeenHitRecently", "Been hit recently (the boss's hits that land, in any 4 s)", stat=_hit_recently,
              fields=("EvadeChance", "EffectiveBlockChance", "enemySkillTime")),
    Condition("conditionBlockedRecently", "Blocked recently (a block in any 4 s of the boss's hits)", stat=_blocked_recently,
              fields=("EffectiveBlockChance", "enemySkillTime")),
    Condition("conditionHaveEnergyShield", "Have Energy Shield", stat=lambda s: number(s.get("EnergyShield")) > 0,
              fields=("EnergyShield",)),
    # Grinning Immolation (Infernalist): "Become Ignited when you deal a Critical Hit": with crits landing you stay Ignited,
    # so every "while Ignited" line of the build counts (its self-damage is a drain outside the checked defences)
    Condition("overridePowerCharges", "Power Charges held", value_fn=_charges_held("Power"),
              fields=("Speed", "CritChance", "PowerChargesMax", "PowerChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("overrideFrenzyCharges", "Frenzy Charges held", value_fn=_charges_held("Frenzy"),
              fields=("Speed", "CritChance", "FrenzyChargesMax", "FrenzyChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("overrideEnduranceCharges", "Endurance Charges held", value_fn=_charges_held("Endurance"),
              fields=("Speed", "EffectiveBlockChance", "EnduranceChargesMax", "EnduranceChargesDuration", *CHARGE_EVENT_FIELDS, *models.IMMOBILISE_FIELDS)),
    Condition("multiplierWitheredStackCount", "Withered stacks on the boss", value_fn=_withered_stacks,
              fields=WITHER_FIELDS),
    Condition("conditionIgnited", "Ignited yourself (Grinning Immolation, on your critical hits)",
              (r"become ignited when you deal a critical hit",)),
    Condition("enemyDistance", "Boss distance (play-style assumption; 10 units a metre)", value_fn=_boss_distance,
              fields=("PresenceRadius",)),
    Condition("conditionStationary", "Standing still (play-style choice)", value_fn=_standing_still, fields=()),
    Condition("conditionHitMeleeRecently", "Melee hit recently (a melee main skill)", main_types=("Melee",)),
    Condition("conditionAtCloseRange", "Enemy at close range (a melee main skill)", main_types=("Melee",)),
    *(Condition(var, label, value_fn=debuff_uptime(var),
                fields=("Speed", "HitChance", "CritChance", "enemySkillTime", "EvadeChance", "EffectiveBlockChance"))
      for var, label in (("conditionEnemyBlinded", "Blinded"), ("conditionEnemyMaimed", "Maimed"),
                         ("conditionEnemyHindered", "Hindered"), ("conditionEnemyIntimidated", "Intimidated"),
                         ("conditionEnemyUnnerved", "Unnerved"), ("conditionEnemyCoveredInAsh", "Covered in Ash"),
                         ("conditionEnemyCoveredInFrost", "Covered in Frost"))),
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


def qualifies(line: str, skill_types: set[str]) -> bool:
    return all(types & skill_types for pattern, types in QUALIFIERS if pattern.search(line))


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
        self.rolling = 0.0  # share of the fight spent dodge rolling (Search.rolling_share)
        self.found_uptimes: dict[str, float] = {}  # var -> share of the fight, from the last evidence
        self.uptimes: dict[str, float] = {}  # the active ones up for only part of it (Search.weigh_uptimes)

    def _group_uses(self, groups: list[dict], names: set[str]) -> dict[str, float]:
        """{gem name: uses per second} for skills beside the main one, each at its enabled group's rate
        (skill_use_rate); a name in no enabled group is left out (the caller's PRESS_EVERY guess)."""
        where = {n["name"]: g["index"] for g in groups if g.get("enabled") is not False
                 for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in names}
        if not where:
            return {}
        by_index = {r.get("index"): skill_use_rate(r)[0]
                    for r in self.build.call("useRates", indices=sorted(set(where.values())))["rates"]}
        return {n: by_index[i] for n, i in where.items() if i in by_index}

    def _minion_deaths(self) -> tuple[float, str]:
        """Your minions Killed per second against a boss and how (models.minion_deaths, from the build's skills; MECHC7)."""
        found = [models.Skill.of(s) for s in self.build.call("activeSkills")["skills"] if not s["disabled"]]
        group = self.build.main_group()
        main = next((s for s in found if group and s.group == group["index"] and s.name == self.main_skill), None)
        return models.minion_deaths(found, main, self.build.call("stats", fields=models.MODEL_FIELDS))

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
        for name in banners:
            uses, how = models.glory_uses([name], stats, main)
            duration = number((rates.get(where.get(name)) or {}).get("duration")) or BANNER_SECONDS
            shares.append((min(1.0, uses * duration), f"{how}; it stands {duration:.1f} s"))
        up, how = min(shares)
        return up, (f"a placed banner stands {up:.0%} of the fight: {how} (you stand in its 6 m area, a play note; "
                    "Valour not counted, an undercount)")

    def _infusion_consumed(self, lines: list[dict], stats: dict) -> tuple[float, str]:
        """(Infusions consumed per second, how): the build's consumers (INFUSION_CONSUMERS) take what it makes."""
        from builder.search.assumptions import SIPHON, infusion_supply, siphon_freezes

        groups = [g for g in self.build.call("groups")["groups"] if g.get("enabled") is not False]
        names = {n["name"] for g in groups for n in g.get("gems") or [] if n.get("enabled", True)}
        consumers = {n: INFUSION_CONSUMERS[n] for n in names | {self.main_skill} if n in INFUSION_CONSUMERS}
        if not consumers:
            return 0.0, "no skill of the build consumes Infusions: 0"
        others = self._group_uses(groups, set(consumers) - {self.main_skill})
        uses = {n: number(stats.get("Speed")) if n == self.main_skill else others.get(n, 1 / models.PRESS_EVERY)
                for n in consumers}
        raw = self.build.call("activeSkills")["skills"]
        present = {s["name"]: s for s in raw if not s["disabled"]}
        siphon = (siphon_freezes(self.build.call("stats", fields=models.IMMOBILISE_FIELDS)) if SIPHON in present
                  else (0.0, ""))
        supply, made = infusion_supply(present, [line["text"] for line in lines], self.found_uptimes, siphon)
        elements = {e for each in consumers.values() for e in each}
        have = sum(supply[e] for e in elements) + supply["Any"]
        cast = sum(uses.values())
        rate = min(have, cast)
        return rate, (f"{rate:.2f}/s: {have:.2f} {'/'.join(sorted(elements)) or 'Verisium'} Infusions/s from "
                      f"{', '.join(made) or 'nothing'}, for {', '.join(f'{n} {u:.2f}/s' for n, u in uses.items())}")

    def _item_uses(self, lines: list[dict], events: dict) -> dict[str, tuple[float, str]]:
        """{line text: (uses per second, how)} for the equipped flasks' and charms' own "on use" lines, and the flask
        events (Flask, LifeFlask, ManaFlask) added to `events`: each item at flasks.uses_per_second."""
        from builder.search.flasks import CHARM_SLOTS, FLASK_SLOTS, Charges, slot_pool, uses_per_second

        gain = Charges.from_bridge(self.build.call("chargeGain"))
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
            # (skill_use_rate: its cooldown, else its duration, else PRESS_EVERY, LOW), not one use per 4 s
            uses = self._group_uses(groups_now, unrated)
            for each in consumers.values():
                for n in [n for n, u in each.items() if u is None]:
                    # (a support in the main skill's group consumes on the main skill's uses)
                    each[n] = number(stats.get("Speed")) if n in main_gems else uses.get(n, 1 / models.PRESS_EVERY)
            for n in [n for n, u in rage_spend.items() if u is None]:
                each = uses.get(n, 1 / models.PRESS_EVERY)
                rage_spend[n] = RAGE_PER_USE[n] * each if n in RAGE_PER_USE else RAGE_PER_SECOND[n] * min(1.0, each)
        if any(MINION_DIES.search(t) for t in texts):  # (MECHC7: "when a Minion Dies" lines, from the build's own sources)
            stats["_minion_deaths"] = self._minion_deaths()
        if banners := sorted({n["name"] for g in self.build.call("groups")["groups"] if g.get("enabled") is not False
                              for n in g.get("gems") or [] if n.get("enabled", True) and n["name"] in BANNERS}):
            # (MECHC7: the placed banner at its Glory share)
            stats["_banner"] = self._banner_share(banners)
        if any(INFUSION_CONSUMED.search(t) for t in texts):
            stats["_infusion_consumed"] = self._infusion_consumed(lines, stats)
        events = event_rates(groups_now, self.build.ctx.gems, self.main_skill, main_types, number(stats.get("Speed")),
                             lambda idx: self.build.call("useRates", indices=idx)["rates"], texts) if groups_now else {}
        item_uses = self._item_uses(lines, events) if any(
            ON_USE.search(line["text"]) and line["source"].startswith(USED_SLOTS) for line in lines) or any(
            FLASK_EVENT.search(t) for t in texts) else {}
        stats = {**stats, "_events": events, "_item_uses": item_uses, "_texts": texts, "_weapon": getattr(self.build.equipped.get("Weapon 1"), "type", ""), "_consumers": consumers, "_rage_spend": rage_spend,
                 "_main_channel_rage": self.main_skill in RAGE_PER_SECOND, "_main_types": main_types, "_distance_choice": self.distance,
                 "_weapon_types": [o.type for slot in ("Weapon 1", "Weapon 2")
                                   if (o := (getattr(self.build, "equipped", None) or {}).get(slot)) is not None],
                 "_stationary_off": self.stationary_off, "_rolling": self.rolling,
                 "_curse_uses": curse_uses(self.build, stats, texts, (self.distance or (natural_distance(main_types), ""))[0])
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
        if "Arcane Surge" in main_gems:  # (the support: its threshold and duration from the gem, arcane_surge_uptime)
            raw = self.build.call("activeSkills", supportStats=list(ARCANE_SURGE_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            got = main.get("supportStats") or {}
            if all(got.get(stat) for stat in ARCANE_SURGE_SUPPORT_STATS):
                stats["_arcane_surge_support"] = (got[ARCANE_SURGE_SUPPORT_STATS[0]], got[ARCANE_SURGE_SUPPORT_STATS[1]] / 1000)
        if any("incision" in name.lower() or name == "Bleed IV" for name in main_gems):  # (Incision, Bleed IV)
            raw = self.build.call("activeSkills", supportStats=list(INCISION_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            stats["_incision_per_hit"] = number((main.get("supportStats") or {}).get(INCISION_SUPPORT_STATS[0]))
        names = set(gem_types) | {g["name"] for grp in self.build.groups() if grp.get("enabled") is not False
                                  for g in grp["gems"] if g.get("enabled", True)}
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
        # Shocked Ground (shocked_ground): the main skill's own, the Shocking Leap support on it, and the lines
        if self.main_skill in SHOCKED_GROUND_SKILLS or SHOCKED_GROUND_SUPPORT in main_gems:
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=list(SHOCKED_GROUND_SUPPORT_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            got = main.get("levelStats") or {}
            if self.main_skill in SHOCKED_GROUND_SKILLS:
                stats["_sg_skill"] = (self.main_skill, max(number(got.get(s)) for s in SHOCKED_GROUND_SKILL_STATS))
            stats["_sg_support"] = number((main.get("supportStats") or {}).get(SHOCKED_GROUND_SUPPORT_STATS[0]))
        stats["_sg_charged_mark"] = "Charged Mark" in names
        stats["_stationary"] = not self.stationary_off and any(STATIONARY_LINE.search(t) for t in texts)
        stats["_shocked_ground"] = shocked_ground(stats, texts)
        if stats["_shocked_ground"][0] >= MIN_UPTIME:  # its Shock's magnitude (ground_shock_magnitude)
            stats["_sg_magnitude"] = self.build.call("shockMagnitude")
        self.parry_plan = None
        if any(n in gem_types for n in models.PARRY_SKILLS) or any(PARRY_GRANT.search(t) for t in texts):  # (Parried)
            raw = self.build.call("activeSkills")
            found = [models.Skill.of(s) for s in raw["skills"] if not s.get("disabled")]
            stats["_parry_plan"] = self.parry_plan = models.parry_plan(
                found, stats, "Attack" in main_types, bool(({self.main_skill} | main_gems) & models.PARRY_CONSUMERS))
        broken = None  # Armour Broken first: Exposure on a Full Break reads its rate
        if "Ferocious Roar" in gem_types and (roar := self.roar_evidence()):
            broken, stats["_full_break_rate"] = roar, roar[2] / models.full_break_duration([line["text"] for line in lines])
        elif any(ARMOUR_BREAK_LINE.search(line["text"]) for line in lines):
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=list(BREAK_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            gem_lines = [BREAK_STATS[stat].format(f"{value:g}") for part in ("levelStats", "supportStats")
                         for stat, value in (main.get(part) or {}).items() if stat in BREAK_STATS and value]
            texts = [line["text"] for line in lines if not line["source"].startswith("gem:")] + gem_lines
            armour_stats = {**stats, "_enemy_armour": number(raw.get("enemyArmour"))}
            up, basis = _armour_broken(texts, armour_stats)
            if basis:
                broken = _box(up, basis)
                stats["_full_break_rate"] = up / models.full_break_duration(texts)
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
        share, basis = models.roar_break(skill, number(raw.get("enemyArmour")), models.full_break_duration(texts))
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
        if changes:
            self.build.set_config(changes)
        return bool(changes)

    def could_source(self, condition: Condition, text: str) -> bool:
        """Could something with this text be the condition's source? Text rules are checked
        exactly; for conditions read from PoB's numbers (shock chance, crit chance) a mention of
        the effect is enough to deserve a real trial."""
        if condition.text and text_evidence(condition, text) and qualifies(text, self.main_types):
            return True
        element = condition.var[len("conditionEnemy"):-len("Exposure")].lower()
        if condition.var.endswith("Exposure") and (m := EXPOSURE_SOURCE.search(text)) and m.group(2).lower() in ("elemental", element):
            return True  # (_exposure_uptime rates it in the real trial)
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
        """May PoB calculate this stat set (STAT_SET_NEEDS)? `assume` counts as active, for what-ifs."""
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
            self.distance = None if metres == natural_distance(self.main_types) or not metres else (metres, "restored")

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
