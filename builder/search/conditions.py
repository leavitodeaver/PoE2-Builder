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
    "PresenceRadius",
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
    if not m or re.search(r"\bcannot gain\b|instead|allies|when you gain|if you would gain|per \w+ charge|lose", text, re.I):
        return None
    chance = float(m.group(1)) / 100 if m.group(1) else 1.0
    when = m.group(2).lower()
    hits = number(stats.get("Speed")) * (number(stats.get("HitChance")) or 100) / 100
    crits = hits * number(stats.get("CritChance")) / 100
    boss = 1 / (number(stats.get("enemySkillTime")) or 0.7)
    if "kill" in when:
        return 0.0, "on kill (nothing dies on a single boss)"
    if re.search(r"when you (freeze|electrocute|pin|stun|immobilise|heavy stun)|freeze, electrocute or pin", when):
        # a pinnacle boss is rarely immobilised (the catalog's Heavy Stun and buildup ailments: one per 30 s assumed)
        return chance / 30.0, "when you freeze, electrocute, pin or stun the boss: one per 30 s assumed"
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
    Condition("buffOnslaught", "Onslaught", (_gen("onslaught"),), (_consumer("onslaught"),)),
    Condition("buffArcaneSurge", "Arcane Surge", (_gen("arcane surge"),), (_consumer("arcane surge"),)),
    Condition("multiplierRage", "Rage", (_gen("rage"),), (_consumer("rage"),), value_field="MaximumRage"),
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
    Condition("conditionEnemyArmourBroken", "Armour Broken", (r"\bbreaks? (?:\w+ )?armour\b", r"\bfully break\w* armour"),
              (r"against enemies with (?:fully )?broken armour|if (?:the )?enemy armour",)),
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
              fields=("Speed", "CritChance", "PowerChargesMax", "PowerChargesDuration")),
    Condition("overrideFrenzyCharges", "Frenzy Charges held", value_fn=_charges_held("Frenzy"),
              fields=("Speed", "CritChance", "FrenzyChargesMax", "FrenzyChargesDuration")),
    Condition("overrideEnduranceCharges", "Endurance Charges held", value_fn=_charges_held("Endurance"),
              fields=("Speed", "EffectiveBlockChance", "EnduranceChargesMax", "EnduranceChargesDuration")),
    Condition("multiplierWitheredStackCount", "Withered stacks on the boss", value_fn=_withered_stacks,
              fields=("Speed", "IgniteChancePerHit")),
    Condition("conditionIgnited", "Ignited yourself (Grinning Immolation, on your critical hits)",
              (r"become ignited when you deal a critical hit",)),
    Condition("enemyDistance", "Boss distance (play-style assumption; 10 units a metre)", value_fn=_boss_distance,
              fields=("PresenceRadius",)),
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
        self.main_types = set((build.ctx.gems.get(main_skill) or {}).get("skillTypes") or [])
        self.active: dict[str, dict] = {}  # var -> {label, value, evidence}
        self.distance: tuple[float, str] | None = None  # (metres, why) chosen by score (Search.choose_distance)

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
        stats = {**stats, "_consumers": consumers, "_main_types": main_types, "_distance_choice": self.distance}
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
        if "Ferocious Roar" in gem_types and "conditionEnemyArmourBroken" not in found and (roar := self.roar_evidence()):
            found["conditionEnemyArmourBroken"] = roar
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
        if (kind := CHARGE_OVERRIDES.get(condition.var)) is not None:  # a generator of that charge a boss fight feeds
            rate = _charge_rate(text, kind, {"Speed": 1.0, "HitChance": 100, "CritChance": 50, "EffectiveBlockChance": 30})
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
