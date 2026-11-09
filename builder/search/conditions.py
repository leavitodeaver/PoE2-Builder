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

import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search import models
from builder.search.objective import number
from builder.search.state import EPSILON, Build

AILMENT_WINDOW = 4.0  # seconds "recently" looks back for a crit
UPTIME = 0.9  # chance that at least one hit applied it within the window
# Each ailment's own base duration on an enemy, the 0.5 data's game constants as PoB reads them (Data/Misc.lua
# BaseIgniteDuration ...): the fallback when PoB gives the skill no duration of its own ({Ailment}Duration, with the
# build's duration modifiers). poe2db's ailment pages not yet read against these.
AILMENT_DURATION = {"Ignite": 4.0, "Bleed": 5.0, "Poison": 2.0, "Shock": 8.0}
EVIDENCE_FIELDS = [
    "CritChance", "Speed", "ShockChancePerHit", "IgniteChancePerHit", "PoisonChancePerHit", "BleedChancePerHit",
    *(f"{a}Duration" for a in AILMENT_DURATION),
    "ChillChanceOnHit", "MaximumRage", "EnergyShield", "EvadeChance", "EffectiveBlockChance", "enemySkillTime",
    "HitChance", *(f"{c}Charges{f}" for c in ("Power", "Frenzy", "Endurance") for f in ("Max", "Duration")),
    "PresenceRadius", "Ward", *models.IMMOBILISE_FIELDS,
]
RECENTLY = 4.0  # seconds ("Recently": the past 4 seconds)


def _hit_recently(stats: dict) -> bool:
    """At least one boss hit lands in any 4 s with chance UPTIME (landing hits come one after another, about every 0.7 s)."""
    every = number(stats.get("enemySkillTime")) or 0.7
    land = (1 - min(number(stats.get("EvadeChance")), 100) / 100) * (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)
    return 1 - (1 - land) ** (RECENTLY / every) >= UPTIME


WITHERED_MAX = 10  # "Withered applies 5% increased Chaos damage Taken, and can be inflicted up to 10 times" (poe2db)
WITHER_CHANCE = re.compile(r"(\d+)% chance to inflict Withered for (\d+(?:\.\d+)?) seconds on Hit", re.I)
WITHER_KEEP = re.compile(r"Withered does not expire on Enemies (Ignited by you|you Ignite)", re.I)


def _withered_stacks(lines: list[str], stats: dict) -> tuple[float, str]:
    """Withered stacks the build keeps on a boss: chance x hits per second x duration from its "chance to inflict Withered"
    lines, up to 10; with Blackflame ("does not expire on Enemies you Ignite") and Ignite on the boss, all 10."""
    rate = number(stats.get("Speed"))
    stacks, sources = 0.0, []
    for text in lines:
        if m := WITHER_CHANCE.search(text):
            stacks += float(m.group(1)) / 100 * rate * float(m.group(2))
            sources.append(text)
    if not sources:
        return 0.0, ""
    if any(WITHER_KEEP.search(t) for t in lines) and ailment_uptime(stats, "Ignite") >= UPTIME:
        return float(WITHERED_MAX), f"{sources[0]}; and Withered never expires on the Ignited boss: {WITHERED_MAX} stacks"
    stacks = min(float(WITHERED_MAX), stacks)
    return float(int(stacks)), f"{'; '.join(sources)} at {rate:.2f} hits/s: {stacks:.1f} stacks kept up"


# Charges held (the mechanics catalog's first priority, 7 Nov 2026): PoB counts the maximum whenever its "use" box is on.
# The count a build holds is min(maximum, generation per second x the charge's duration), from the build's own sources:
# poe2db (https://poe2db.tw/us/Charges): charges last a duration (PoB: 15 s base, ChargeDuration) and come from specific
# sources; on a single boss nothing dies, so on-kill sources give none. A source whose rate its line doesn't say (on use,
# when you stun, on reaching something) is assumed once per 4 s, labelled.
CHARGE_DEFAULT_EVERY = 4.0
CHARGE_SOURCE = r"(?:(\d+(?:\.\d+)?)% chance (?:to|when you \w+ (?:a|an) \w+ Charge to)?\s*)?(?:gain|grants?)\w* (?:an? |\d+ )?(?:[\w, ]*?\b{kind}\b[\w, ]*?) Charges?(.*)$"


def _charge_rate(text: str, kind: str, stats: dict) -> tuple[float, str] | None:
    """(charges per second, how) from one line, or None if it isn't a source of that charge."""
    m = re.search(CHARGE_SOURCE.format(kind=kind), text, re.I)
    if not m or re.search(r"\bcannot gain\b|instead|allies|when you gain|if you would gain|per \w+ charge|lose|at maximum \w+ charges", text, re.I):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(2).lower()
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    crits = hits * number(stats.get("CritChance")) / 100
    boss = 1 / (number(stats.get("enemySkillTime")) or 0.7)
    if "kill" in when:
        return 0.0, "on kill (nothing dies on a single boss)"
    if re.search(r"when you (freeze|electrocute|pin|stun|immobilise|heavy stun)|freeze, electrocute or pin", when):
        events = ({"freeze", "electrocute", "pin", "stun"} if "immobilise" in when
                  else {e for e in ("freeze", "electrocute", "pin", "stun") if e in when})
        rate, how = models.immobilise_rate(stats, events)
        return chance * rate, f"{chance:.0%} x {rate:.3f}/s ({how})"
    if "critical hit" in when and "take" not in when:
        return chance * crits, f"{chance:.0%} x {crits:.2f} crits/s"
    if "take a critical hit" in when:
        return chance * boss * 0.05, "when the boss crits you (5% of its hits)"
    if "block" in when:
        block = min(number(stats.get("EffectiveBlockChance")), 100) / 100
        return chance * boss * block, f"{chance:.0%} x {boss * block:.2f} blocks/s"
    if "on hit" in when or when.strip() in ("", "on hit") or "hit" in when:
        return chance * hits, f"{chance:.0%} x {hits:.2f} hits/s"
    if m2 := re.search(r"every (\d+(?:\.\d+)?)? ?seconds?|once per second|every second", when):
        every = float(m2.group(1)) if m2.groups() and m2.group(1) else 1.0
        return chance / every, f"one every {every:g} s"
    return chance / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed"


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


def natural_distance(main_types: set[str]) -> float:
    """The distance in metres a build stands at when nothing it has depends on it."""
    return MELEE_DISTANCE if "Melee" in main_types or main_types & NEAR_TYPES else RANGED_DISTANCE


def distance_candidates(lines: list[str], main_types: set[str]) -> list[float]:
    """Distances worth a trial: the natural one first, close range, and just beyond any "further than N m" line."""
    found = [natural_distance(main_types)]
    for text in lines:
        if m := DISTANCE_LINE.search(text):
            found.append(MELEE_DISTANCE)
            if m.group(1):
                found.append(float(m.group(1)) + 0.5)
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


def _charges_held(kind: str):
    def held(lines: list[str], stats: dict) -> tuple[float, str]:
        rate, how = 0.0, []
        for text in lines:
            if (r := _charge_rate(text, kind, stats)) is not None:
                rate += r[0]
                how.append(f"{text[:60]} ({r[1]})")
        if not how:
            return 0.0, ""
        most = number(stats.get(f"{kind}ChargesMax"))
        duration = number(stats.get(f"{kind}ChargesDuration")) or 15.0
        # Skills that consume this charge per use (PoB skill types SkillConsumes<Kind>ChargesOnUse; one per use assumed):
        # what they spend isn't held. A consumer finds a charge on a share generation / consumption of its uses.
        spent = sum((stats.get("_consumers") or {}).get(kind, {}).values())
        net = max(rate - spent, 0.0)
        count = min(most, net * duration)
        basis = f"{rate:.2f}/s x {duration:g} s"
        if spent:
            share = min(1.0, rate / spent)
            basis = (f"{rate:.2f}/s made, {spent:.2f}/s spent by " + ", ".join(stats["_consumers"][kind]) +
                     f" (one per use; {share:.0%} of their uses find a charge), {net:.2f}/s left x {duration:g} s")
            if count <= 0 and share >= 0.5:
                count = 0.1  # (held for an instant before it's spent: enough for PoB to count the charged use)
        return round(count, 1), f"{basis} = {count:.1f} held, at most {most:g}: " + "; ".join(how[:3])
    return held


CHARGE_OVERRIDES = {f"override{kind}Charges": kind for kind in ("Power", "Frenzy", "Endurance")}


# Rage held (MECH27, 9 Nov 2026; before, any Rage source set the maximum, on-kill ones too). The game files' KeywordPopups
# Rage (build 26476662): "you have 30 maximum Rage and lose 1 Rage every 0.2 seconds. Rage loss is paused for 4 seconds
# upon gaining Rage, or after taking damage. Only one Hit every 0.5 seconds can cause you to gain Rage." Gains come from
# the build's lines (every "gain # Rage" wording of the stat descriptions; nothing dies on a single boss, so on-kill gains
# are 0), spends from the skills that spend it (GrantedEffectStatSets / GrantedEffectsPerLevel costs below). The maximum
# is held while gains outrun spends and gains or the boss's landing hits come at least once per 4 s; else the average of
# each gain held 4 s and then decaying. A source whose rate its line doesn't say (Warcry, Life Flask, on use, Mana spent)
# is assumed once per CHARGE_DEFAULT_EVERY s, labelled.
RAGE_PAUSE, RAGE_DECAY, RAGE_HIT_EVERY = 4.0, 5.0, 0.5
RAGE_SOURCE = re.compile(r"(?:(\d+(?:\.\d+)?)% chance to )?\bgain (\d+|an?)? ?Rage\b(.*)$", re.I)
RAGE_REGEN = re.compile(r"\bregenerate (\d+(?:\.\d+)?)(%)?(?: of your maximum)? Rage per second(.*)$|"
                        r"\b1 Rage Regenerated for every", re.I)
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
        if not m.group(1) or "for every" in (m.group(3) or ""):
            return 1.0, 1.0, "regenerated at a rate set by Life or Mana regeneration: 1/s assumed"
        r = float(m.group(1)) * (number(stats.get("MaximumRage")) / 100 if m.group(2) else 1.0)
        return r, 1.0, f"{r:g}/s regenerated"
    m = RAGE_SOURCE.search(text)
    if not m or RAGE_NOT_SOURCE.search(text):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    amount = float(m.group(2)) if m.group(2) and m.group(2).isdigit() else 1.0
    when = m.group(3).lower()
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    every = number(stats.get("enemySkillTime")) or 0.7
    land = ((1 - min(number(stats.get("EvadeChance")), 100) / 100) *
            (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)) / every
    if "kill" in when:
        return 0.0, 0.0, "on kill (nothing dies on a single boss)"
    if "critically hit" in when or "critical|critically hit" in when:
        events, how = land * 0.05, "when the boss crits you (5% of its hits that land)"
    elif "hit] by" in when or "hit by" in when:
        events, how = land, f"the boss's hits that land, {land:.2f}/s"
    else:
        if not own_hits and re.search(r"hit", when):
            return 0.0, 0.0, "on your hits, but the main skill can't gain Rage while used"
        if "ignite" in when:
            events = hits * min(number(stats.get("IgniteChancePerHit")), 100) / 100
            how = f"{events:.2f} Ignites/s"
        elif "critical" in when:
            events = hits * number(stats.get("CritChance")) / 100
            how = f"{events:.2f} crits/s"
        elif "hit" in when:
            events, how = hits, f"{hits:.2f} hits/s"
        else:
            events, how = 1 / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed"
    events = min(events * chance, 1 / RAGE_HIT_EVERY)  # (one Hit every 0.5 s can gain Rage)
    if cap := re.search(r"no more than once every (\d+(?:\.\d+)?) seconds?", when):
        events = min(events, 1 / float(cap.group(1)))
    return events * amount, events, f"{chance:.0%} x {amount:g} Rage, {how}, {events:.2f} gains/s"


def rage_held(lines: list[str], stats: dict) -> tuple[float, str]:
    main_types = stats.get("_main_types") or set()
    spend = stats.get("_rage_spend") or {}
    own_hits = not (stats.get("_main_channel_rage") or False)
    gain, events, how = 0.0, 0.0, []
    for text in lines:
        if not qualifies(text, main_types) or (r := _rage_gain(text, stats, own_hits)) is None:
            continue
        gain, events = gain + r[0], events + r[1]
        how.append(f"{text[:60]} ({r[2]})")
    if not how:
        return 0.0, ""
    most = number(stats.get("MaximumRage"))
    spent = sum(spend.values())
    every = number(stats.get("enemySkillTime")) or 0.7
    land = ((1 - min(number(stats.get("EvadeChance")), 100) / 100) *
            (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)) / every
    basis = f"{gain:.2f} Rage/s gained"
    if spent:
        basis += f", {spent:.2f}/s spent by " + ", ".join(f"{n} {v:.2f}/s" for n, v in spend.items())
    net = gain - spent
    if net <= 0 or most <= 0:
        held = 0.0
        basis += " (spent as fast as it comes: none held)" if gain > 0 else " (no gain a boss fight feeds)"
    elif events * RAGE_PAUSE >= 1 or land * RAGE_PAUSE >= 1:
        held = most
        basis += f", its loss paused (gains {events:.2f}/s, boss hits landing {land:.2f}/s, one per 4 s needed): the maximum"
    else:
        level = min(most, net / events)  # (each gain, held 4 s, then lost 1 per 0.2 s)
        gap = 1 / events
        down = min(level / RAGE_DECAY, gap - RAGE_PAUSE)
        held = (level * RAGE_PAUSE + level * down - RAGE_DECAY * down * down / 2) / gap
        basis += f", one gain every {gap:.1f} s of {level:.0f} Rage held 4 s then lost 5/s: {held:.1f} on average"
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
    land = ((1 - min(number(stats.get("EvadeChance")), 100) / 100) *
            (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)) / every
    if re.search(r"\bkill|culling strike", when):
        return 0.0, "on kill (nothing dies on a single boss)"
    if "minion dies" in when or "vaal skill" in when:
        return 0.0, f"'{when.strip()[:30]}': its rate isn't modelled (0, labelled)"
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
    else:
        events, how = 1 / CHARGE_DEFAULT_EVERY, f"'{when.strip()[:40]}': rate not in the line, one per {CHARGE_DEFAULT_EVERY:g} s assumed"
    up = 1 - (1 - min(chance, 1.0)) ** (events * duration) if chance < 1 else (1.0 if events * duration >= 1 else events * duration)
    return up, f"{chance:.0%} x {how}, {duration:g} s: {up:.0%} up"


def onslaught_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    main_types = stats.get("_main_types") or set()
    down, how = 1.0, []
    for text in lines:
        if not qualifies(text, main_types) or (r := _onslaught_source(text, lines, stats)) is None:
            continue
        down *= 1 - r[0]
        how.append(f"{text[:60]} ({r[1]})")
    if not how:
        return 0.0, ""
    up = 1 - down
    basis = f"Onslaught up {up:.0%} of the fight ({UPTIME:.0%} needed): " + "; ".join(how[:3])
    return (True if up >= UPTIME else False), basis


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


def armour_broken_uptime(lines: list[str], stats: dict) -> tuple[object, str]:
    """(True / False, basis) for the Armour Broken box, or (0.0, "") if no line Breaks Armour for you."""
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
    up = models.FULL_BREAK_DURATION / (models.FULL_BREAK_DURATION + to_break)
    up = 1 - (1 - up) * (1 - min(1.0, full * models.FULL_BREAK_DURATION))
    basis = (f"Armour Fully Broken {up:.0%} of the fight ({UPTIME:.0%} needed): {rate:,.0f} Armour broken/s against the "
             f"boss's {armour:,.0f} (PoB), {models.FULL_BREAK_DURATION:g} s each Full Break: " + "; ".join(how[:3]))
    return (True if up >= UPTIME else False), basis


def _blocked_recently(stats: dict) -> bool:
    every = number(stats.get("enemySkillTime")) or 0.7
    block = min(number(stats.get("EffectiveBlockChance")), 100) / 100
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


def _inflict(subject: str) -> str:
    return rf"\b(?:inflict|inflicts|inflicting|apply|applies|applying|cause|causes|causing)\b[^.;]*\b{subject}|\b{subject}\w* (?:enemies|targets|on hit)"


def _against(subject: str) -> str:
    # (the past participle: "to Hindered Enemies" pays off on the debuff; "chance to Hinder Enemies" is a source of it)
    return rf"(?:against|to|on|of) (?:\w+ )?{subject}e?d (?:enemies|targets)|while {subject}|if {subject}|{subject}e?d enemies (?:deal|take)"


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
    return lambda s: ailment_uptime(s, ailment) >= UPTIME


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

    def patterns(self) -> tuple[list[re.Pattern], list[re.Pattern]]:
        return ([re.compile(p, re.I) for p in self.text], [re.compile(p, re.I) for p in self.not_text])


def _charge(var: str, name: str) -> Condition:
    subject = f"{name} charges?"
    return Condition(var, f"{name.capitalize()} Charges", (_gen(subject),), (_consumer(subject),))


def _debuff(var: str, label: str, word: str) -> Condition:
    return Condition(var, label, (_inflict(word),), (_against(word),))


CONDITIONS: list[Condition] = [
    _charge("usePowerCharges", "power"),
    _charge("useFrenzyCharges", "frenzy"),
    _charge("useEnduranceCharges", "endurance"),
    Condition("buffOnslaught", "Onslaught", value_fn=lambda lines, stats: onslaught_uptime(lines, stats),
              fields=("Speed", "HitChance", "CritChance", "enemySkillTime", "EvadeChance")),
    Condition("buffArcaneSurge", "Arcane Surge", (_gen("arcane surge"),), (_consumer("arcane surge"),)),
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
    Condition("conditionEnemyShocked", "Shocked", stat=_ailment_up("Shock"),
              fields=("ShockChancePerHit", "Speed", "HitChance", "ShockDuration")),
    Condition("conditionEnemyIgnited", "Ignited", stat=_ailment_up("Ignite"),
              fields=("IgniteChancePerHit", "Speed", "HitChance", "IgniteDuration")),
    Condition("conditionEnemyPoisoned", "Poisoned", stat=_ailment_up("Poison"),
              fields=("PoisonChancePerHit", "Speed", "HitChance", "PoisonDuration")),
    Condition("conditionEnemyBleeding", "Bleeding", stat=_ailment_up("Bleed"),
              fields=("BleedChancePerHit", "Speed", "HitChance", "BleedDuration")),
    Condition("conditionEnemyChilled", "Chilled", stat=lambda s: number(s.get("ChillChanceOnHit")) >= 100,
              fields=("ChillChanceOnHit",)),
    Condition("conditionEnemyFireExposure", "Fire Exposure", (_inflict("(?:fire|elemental) exposure"),)),
    Condition("conditionEnemyColdExposure", "Cold Exposure", (_inflict("(?:cold|elemental) exposure"),)),
    Condition("conditionEnemyLightningExposure", "Lightning Exposure", (_inflict("(?:lightning|elemental) exposure"),)),
    Condition("conditionEnemyArmourBroken", "Armour Broken"),  # (armour_broken_uptime, Conditions.evidence)
    Condition("conditionEnemyCursed", "Cursed", gem_types=("AppliesCurse",)),
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
              fields=("Speed", "CritChance", "PowerChargesMax", "PowerChargesDuration", *models.IMMOBILISE_FIELDS)),
    Condition("overrideFrenzyCharges", "Frenzy Charges held", value_fn=_charges_held("Frenzy"),
              fields=("Speed", "CritChance", "FrenzyChargesMax", "FrenzyChargesDuration", *models.IMMOBILISE_FIELDS)),
    Condition("overrideEnduranceCharges", "Endurance Charges held", value_fn=_charges_held("Endurance"),
              fields=("Speed", "EffectiveBlockChance", "EnduranceChargesMax", "EnduranceChargesDuration", *models.IMMOBILISE_FIELDS)),
    Condition("multiplierWitheredStackCount", "Withered stacks on the boss", value_fn=_withered_stacks,
              fields=("Speed", "IgniteChancePerHit")),
    Condition("conditionIgnited", "Ignited yourself (Grinning Immolation, on your critical hits)",
              (r"become ignited when you deal a critical hit",)),
    Condition("enemyDistance", "Boss distance (play-style assumption; 10 units a metre)", value_fn=_boss_distance,
              fields=("PresenceRadius",)),
    Condition("conditionStationary", "Standing still (play-style choice)", value_fn=_standing_still, fields=()),
    Condition("conditionHitMeleeRecently", "Melee hit recently (a melee main skill)", main_types=("Melee",)),
    Condition("conditionAtCloseRange", "Enemy at close range (a melee main skill)", main_types=("Melee",)),
    _debuff("conditionEnemyBlinded", "Blinded", "blind"),
    _debuff("conditionEnemyMaimed", "Maimed", "maim"),
    _debuff("conditionEnemyHindered", "Hindered", "hinder"),
    _debuff("conditionEnemyIntimidated", "Intimidated", "intimidate"),
    _debuff("conditionEnemyUnnerved", "Unnerved", "unnerve"),
    Condition("conditionEnemyCoveredInAsh", "Covered in Ash", (r"\bcover\w*\b[^.;]*\bin ash\b",)),
    Condition("conditionEnemyCoveredInFrost", "Covered in Frost", (r"\bcover\w*\b[^.;]*\bin frost\b",)),
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
        self.stationary_off: str | None = None  # why a build with stationary lines doesn't stand still (choose_stationary)
        self.rolling = 0.0  # share of the fight spent dodge rolling (Search.rolling_share)

    def evidence(self) -> dict[str, tuple[object, str]]:
        """What the build can sustain now: var -> (value, evidence)."""
        lines = self.build.call("sourceLines")["lines"]
        stats = self.build.call("stats", fields=EVIDENCE_FIELDS)
        gem_types = {}
        for line in lines:
            if line["source"].startswith("gem:"):
                name = line["source"][4:]
                gem_types[name] = set((self.build.ctx.gems.get(name) or {}).get("skillTypes") or [])
        # Charge consumers and their uses per second (the main skill at its speed, any other at one use per 4 s)
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
                            else 1 / CHARGE_DEFAULT_EVERY)
                    consumers.setdefault(kind, {})[name] = uses
        # Rage spenders (RAGE_PER_USE / RAGE_PER_SECOND): the main skill, or a support in its group, at the main skill's
        # uses per second; any other at one use per CHARGE_DEFAULT_EVERY s
        main_gems = {g["name"] for g in group["gems"]} if group is not None else set()
        rage_spend: dict[str, float] = {}
        for name in gem_types:
            uses = number(stats.get("Speed")) if name == self.main_skill or (
                name in main_gems and name.startswith("Rageforged")) else 1 / CHARGE_DEFAULT_EVERY
            if name in RAGE_PER_USE:
                rage_spend[name] = RAGE_PER_USE[name] * uses
            elif name in RAGE_PER_SECOND:
                rage_spend[name] = RAGE_PER_SECOND[name] * (1.0 if name == self.main_skill else 1 / CHARGE_DEFAULT_EVERY)
        stats = {**stats, "_consumers": consumers, "_rage_spend": rage_spend,
                 "_main_channel_rage": self.main_skill in RAGE_PER_SECOND, "_main_types": main_types, "_distance_choice": self.distance,
                 "_stationary_off": self.stationary_off, "_rolling": self.rolling}
        found: dict[str, tuple[object, str]] = {}
        for condition in CONDITIONS:
            reason = None
            for line in lines:
                if condition.text and text_evidence(condition, line["text"]) and qualifies(line["text"], self.main_types):
                    reason = f"{line['source']}: {line['text']}"
                    break
            if reason is None and condition.stat is not None and condition.stat(stats):
                reason = "PoB: " + ", ".join(f"{k} {number(stats.get(k)):.3g}" for k in condition.fields)
            if condition.value_fn is not None:
                value, basis = condition.value_fn([line["text"] for line in lines], stats)
                if value > 0 or basis:  # (a basis with 0: the build has sources, none of which a boss fight feeds)
                    found[condition.var] = (value, basis)
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
        if "Ferocious Roar" in gem_types and (roar := self.roar_evidence()):
            found["conditionEnemyArmourBroken"] = roar
        elif any(ARMOUR_BREAK_LINE.search(line["text"]) for line in lines):
            raw = self.build.call("activeSkills", levelStats=[self.main_skill], supportStats=list(BREAK_STATS))
            main = next((s for s in raw["skills"] if s["name"] == self.main_skill and not s.get("disabled")), {})
            gem_lines = [BREAK_STATS[stat].format(f"{value:g}") for part in ("levelStats", "supportStats")
                         for stat, value in (main.get(part) or {}).items() if stat in BREAK_STATS and value]
            texts = [line["text"] for line in lines if not line["source"].startswith("gem:")] + gem_lines
            up, basis = armour_broken_uptime(texts, {**stats, "_enemy_armour": number(raw.get("enemyArmour"))})
            if basis:
                found["conditionEnemyArmourBroken"] = (up, basis)
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
                found.pop(f"override{kind}Charges")  # (no source the resolver accepts for the box itself)
        return found

    def roar_evidence(self) -> tuple[bool, str] | None:
        """Ferocious Roar used on its cooldown, its Empowered hits Breaking the boss's Armour (models.roar_break): PoB
        reads none of it. Evidence only if the main skill is an Attack and the Armour stays Fully Broken."""
        from builder.search import models

        if "Attack" not in self.main_types:
            return None
        raw = self.build.call("activeSkills", levelStats=[models.FEROCIOUS_ROAR])
        skill, warcry = models.roar([models.Skill.of(s) for s in raw["skills"] if not s["disabled"]])
        if skill is None or warcry:
            return None
        share, basis = models.roar_break(skill, number(raw.get("enemyArmour")))
        return (True, f"gem:{models.FEROCIOUS_ROAR}: {basis}") if share >= UPTIME else None

    def resolve(self) -> bool:
        """Match the config to the evidence. Returns True if anything changed."""
        found = self.evidence()
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
        if changes:
            self.build.set_config(changes)
        return bool(changes)

    def could_source(self, condition: Condition, text: str) -> bool:
        """Could something with this text be the condition's source? Text rules are checked
        exactly; for conditions read from PoB's numbers (shock chance, crit chance) a mention of
        the effect is enough to deserve a real trial."""
        if condition.text and text_evidence(condition, text) and qualifies(text, self.main_types):
            return True
        if condition.var == "conditionEnemyArmourBroken" and ARMOUR_BREAK_LINE.search(text):
            return True  # (armour_broken_uptime rates it in the real trial)
        if condition.var == "conditionStationary" and STATIONARY_LINE.search(text):
            return True  # (a stationary line gets a real trial: the resolver stands the build still with it)
        if (kind := CHARGE_OVERRIDES.get(condition.var)) is not None:  # a generator of that charge a boss fight feeds
            rate = _charge_rate(text, kind, {"Speed": 1.0, "HitChance": 100, "CritChance": 50, "EffectiveBlockChance": 30,
                                             "FreezeBuildupAvg": 10, "ElectrocuteBuildupAvg": 10,
                                             "HeavyStunBuildupAvg": 10, "PhysicalHitAverage": 1})
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
