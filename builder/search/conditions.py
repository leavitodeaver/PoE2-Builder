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

AILMENT_WINDOW = 4.0  # seconds a boss stays affected after an application (shock, ignite, ...)
UPTIME = 0.9  # chance that at least one hit applied it within the window
EVIDENCE_FIELDS = [
    "CritChance", "Speed", "ShockChancePerHit", "IgniteChancePerHit", "PoisonChancePerHit", "BleedChancePerHit",
    "ChillChanceOnHit", "MaximumRage",
]


def _gen(subject: str) -> str:
    return rf"\b(?:gain|gains|grant|grants|generate|generates|granting|gaining)\b[^.;]*\b{subject}"


def _consumer(subject: str) -> str:
    return (rf"\bper {subject}|maximum (?:number of )?{subject}|consum\w*[^.;]*{subject}|lose[^.;]*{subject}"
            rf"|when you gain[^.;]*{subject}|while you have[^.;]*{subject}|if you have[^.;]*{subject}"
            rf"|during {subject}|effect of {subject}|{subject}\w* you have")


def _inflict(subject: str) -> str:
    return rf"\b(?:inflict|inflicts|inflicting|apply|applies|applying|cause|causes|causing)\b[^.;]*\b{subject}|\b{subject}\w* (?:enemies|targets|on hit)"


def _against(subject: str) -> str:
    return rf"(?:against|to|on|of) (?:\w+ )?{subject}\w* (?:enemies|targets)|while {subject}|if {subject}|{subject}\w* enemies (?:deal|take)"


def _uptime(stats: dict, chance_field: str) -> bool:
    chance = number(stats.get(chance_field)) / 100.0
    rate = number(stats.get("Speed"))
    if chance <= 0 or rate <= 0:
        return False
    return 1.0 - (1.0 - min(chance, 1.0)) ** (rate * AILMENT_WINDOW) >= UPTIME


@dataclass(frozen=True)
class Condition:
    var: str
    label: str
    text: tuple[str, ...] = ()
    not_text: tuple[str, ...] = ()
    stat: Callable[[dict], bool] | None = None
    gem_types: tuple[str, ...] = ()  # an enabled gem of one of these skill types is evidence
    value_field: str | None = None  # set the option to this stat instead of True (e.g. Rage)
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
    Condition("conditionEnemyShocked", "Shocked", stat=lambda s: _uptime(s, "ShockChancePerHit"),
              fields=("ShockChancePerHit", "Speed")),
    Condition("conditionEnemyIgnited", "Ignited", stat=lambda s: _uptime(s, "IgniteChancePerHit"),
              fields=("IgniteChancePerHit", "Speed")),
    Condition("conditionEnemyPoisoned", "Poisoned", stat=lambda s: _uptime(s, "PoisonChancePerHit"),
              fields=("PoisonChancePerHit", "Speed")),
    Condition("conditionEnemyBleeding", "Bleeding", stat=lambda s: _uptime(s, "BleedChancePerHit"),
              fields=("BleedChancePerHit", "Speed")),
    Condition("conditionEnemyChilled", "Chilled", stat=lambda s: number(s.get("ChillChanceOnHit")) >= 100,
              fields=("ChillChanceOnHit",)),
    Condition("conditionEnemyFireExposure", "Fire Exposure", (_inflict("(?:fire|elemental) exposure"),)),
    Condition("conditionEnemyColdExposure", "Cold Exposure", (_inflict("(?:cold|elemental) exposure"),)),
    Condition("conditionEnemyLightningExposure", "Lightning Exposure", (_inflict("(?:lightning|elemental) exposure"),)),
    Condition("conditionEnemyArmourBroken", "Armour Broken", (r"\bbreaks? (?:\w+ )?armour\b", r"\bfully break\w* armour"),
              (r"against enemies with (?:fully )?broken armour|if (?:the )?enemy armour",)),
    Condition("conditionEnemyCursed", "Cursed", gem_types=("AppliesCurse",)),
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

    def evidence(self) -> dict[str, tuple[object, str]]:
        """What the build can sustain now: var -> (value, evidence)."""
        lines = self.build.call("sourceLines")["lines"]
        stats = self.build.call("stats", fields=EVIDENCE_FIELDS)
        gem_types = {}
        for line in lines:
            if line["source"].startswith("gem:"):
                name = line["source"][4:]
                gem_types[name] = set((self.build.ctx.gems.get(name) or {}).get("skillTypes") or [])
        found: dict[str, tuple[object, str]] = {}
        for condition in CONDITIONS:
            reason = None
            for line in lines:
                if condition.text and text_evidence(condition, line["text"]) and qualifies(line["text"], self.main_types):
                    reason = f"{line['source']}: {line['text']}"
                    break
            if reason is None and condition.stat is not None and condition.stat(stats):
                reason = "PoB: " + ", ".join(f"{k} {number(stats.get(k)):.3g}" for k in condition.fields)
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
        return found

    def resolve(self) -> bool:
        """Match the config to the evidence. Returns True if anything changed."""
        found = self.evidence()
        changes: dict[str, object] = {}
        for condition in CONDITIONS:
            want = found.get(condition.var)
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

    def would_enable(self, text: str) -> bool:
        """Could adding something with this text turn on a condition that's off now? Text rules
        are checked exactly; for conditions read from PoB's numbers (shock chance, crit chance)
        a mention of the effect is enough to deserve a real trial."""
        for condition in CONDITIONS:
            if condition.var in self.active:
                continue
            if condition.text and text_evidence(condition, text) and qualifies(text, self.main_types):
                return True
            if condition.stat is not None and any(
                    word.lower() in text.lower() for word in TRIAL_WORDS.get(condition.var, ())):
                return True
        return False

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
