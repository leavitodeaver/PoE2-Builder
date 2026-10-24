"""Custom models for mechanics Path of Building ignores that no single modifier line can stand for
(PLAN.md §6 "As built"): Elemental Archon, meta-skill Energy (Cast on Critical, Cast on Elemental
Ailment, Elemental Invocation), Acolyte of Chayula's Darkness and Invoker's Elemental Expression.
Pathfinder's Traveller's Wisdom is an attribute choice (builder/search/tree.py, engine/bridge.lua).

Each model is a pure function of the build's facts (its passive and item lines, a few PoB stats,
its skills), so it is tested without an engine. It returns
- mods: what PoB adds through the bridge's setAssumptions, so the effect stacks with the rest of
  PoB's math: modifier lines ("Line") and trigger rates ("SkillData");
- notes: what the result shows, each with its basis and a ledger confidence label.

Passive inputs are visible to PoB's what-ifs. The ledger turns each input line into a PoB
multiplier or condition on its passive (`mult:`/`flag:` in ledger/entries/custom-models.yaml), and
the model's lines are linear in those multipliers around the current build: exact for the build,
first order for a change. There is one block of lines for each on/off state of the conditions
(e.g. which Archon source the build has), so a what-if that allocates Archon of the Storm or an
Energy node shows its worth without a real trial. Inputs on items count from the next resolve.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from builder.ledger import model_entry

BOSS_POWER = 20  # "Unique monsters always have 20 Power" (https://poe2db.tw/us/Monster_Power)
DAMAGE_TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
# PoB stats the models read (beyond the objective's own fields)
MODEL_FIELDS = sorted({
    "Speed", "HitChance", "CritChance", "Mana", "ManaPerSecondCost", "EnergyShield", "EnergyShieldRechargeDelay",
    "EnemyAilmentThreshold", "ShockChanceOnHit", "ShockChanceOnCrit", "IgniteChanceOnHit", "IgniteChanceOnCrit",
    "Darkness", "Str", "Dex", "Int", *(f"{t}CritAverage" for t in DAMAGE_TYPES), *(f"{t}HitAverage" for t in DAMAGE_TYPES),
})


def number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def _round(value: float) -> float:
    """Five significant digits, so a model's lines only change (and PoB recalculates) when the
    build really moved."""
    return float(f"{value:.5g}")


def line(text: str, scale: float = 1.0, *, when: list | None = None, per: str | None = None,
         base: float = 0.0, skill: str | None = None) -> dict:
    """A modifier line for the bridge: PoB parses `text` (written with a magnitude of 1) and scales
    it by `scale`; with `per`, the value is scale × the model input `per` + base."""
    mod: dict = {"kind": "Line", "line": text, "scale": _round(scale)}
    if when:
        mod["when"] = when
    if per:
        mod["per"] = per
        mod["base"] = _round(base)
    if skill:
        mod["skill"] = skill
    return mod


def trigger_rate(skill: str, rate: float) -> list[dict]:
    return [{"kind": "SkillData", "skill": skill, "key": "triggerRate", "value": _round(rate)},
            {"kind": "SkillData", "skill": skill, "key": "triggered", "value": True}]


def _derivative(f: Callable[[dict], float], x0: dict, key: str) -> float:
    up, down = dict(x0), dict(x0)
    up[key] += 1.0
    down[key] -= 1.0
    return (f(up) - f(down)) / 2.0


def more_lines(text: str, f: Callable[[dict], float], x0: dict, passive: dict, keys: tuple[str, ...],
               when: list | None = None, skill: str | None = None) -> list[dict]:
    """Lines for a "more" modifier worth f(x)% at the model inputs x. PoB multiplies "more"
    modifiers together, so each input gets its own factor 1 + h·(its passive total − now), with h
    chosen so the product has f's slope; at the current build the product is exactly f(x0)."""
    value = f(x0)
    mods = [line(text, value, when=when, skill=skill)] if abs(value) > 1e-6 else []
    factor = 1 + value / 100
    if factor < 0.05:  # (almost) no damage of this kind: slopes would explode
        return mods
    for key in keys:
        h = _derivative(f, x0, key) / 100 / factor
        if abs(h) > 1e-9:
            mods.append(line(text, 100 * h, when=when, per=key, base=-100 * h * passive.get(key, 0.0), skill=skill))
    return mods


def increased_lines(text: str, f: Callable[[dict], float], x0: dict, passive: dict, keys: tuple[str, ...],
                    when: list | None = None) -> list[dict]:
    """Lines for an "increased" modifier worth f(x)%: PoB adds those, so value + slope × change."""
    value = f(x0)
    mods = [line(text, value, when=when)] if abs(value) > 1e-6 else []
    for key in keys:
        slope = _derivative(f, x0, key)
        if abs(slope) > 1e-9:
            mods.append(line(text, slope, when=when, per=key, base=-slope * passive.get(key, 0.0)))
    return mods


# Inputs ------------------------------------------------------------------------------------------

INPUT = re.compile(r"^(mult|flag):(\w+)\s*(-?[\d.]+)?$")


@dataclass
class Inputs:
    """A build's custom-model inputs: totals of `mult:` inputs and the `flag:` conditions, split by
    where they come from (PoB counts the passive part itself; item lines are read here)."""
    passive: dict[str, float] = field(default_factory=dict)
    items: dict[str, float] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    item_flags: set[str] = field(default_factory=set)
    used: list[tuple[str, str, str]] = field(default_factory=list)  # (source, line, ledger entry)

    def total(self, name: str) -> float:
        return self.passive.get(name, 0.0) + self.items.get(name, 0.0)

    def has(self, flag: str) -> bool:
        return flag in self.flags or flag in self.item_flags

    def condition(self, flag: str, on: bool) -> list | None:
        """`when` conditions for a block that needs `flag` on or off: [] when that always holds,
        None when it never can (an item switches it on for good)."""
        if flag in self.item_flags:
            return [] if on else None
        return [{"var": flag}] if on else [{"var": flag, "neg": True}]


def read_inputs(lines: list[dict]) -> Inputs:
    """Model inputs from the build's passive and item lines (bridge `sourceLines`)."""
    inputs = Inputs()
    for entry_line in lines:
        text, source = entry_line["text"], entry_line["source"]
        entry = model_entry(text)
        if entry is None:
            continue
        passive = source.startswith("passive:")
        for replacement in entry.translate(text) or []:
            m = INPUT.match(replacement)
            if not m:
                continue
            kind, name, value = m.groups()
            if kind == "flag":
                (inputs.flags if passive else inputs.item_flags).add(name)
            elif value is not None:
                target = inputs.passive if passive else inputs.items
                target[name] = target.get(name, 0.0) + float(value)
        inputs.used.append((source, text, entry.id))
    return inputs


@dataclass
class Skill:
    """What a model needs to know about an active skill (bridge `activeSkills`)."""
    name: str
    group: int | None = None
    spell: bool = False
    attack: bool = False
    melee: bool = False
    triggered: bool = False
    energy: bool = False  # a meta skill that gains Energy
    cast_time: float = 0.0
    cooldown: float | None = None
    cooldown_recovery: float = 1.0

    @classmethod
    def of(cls, raw: dict) -> Skill:
        return cls(raw["name"], raw.get("group"), bool(raw.get("spell")), bool(raw.get("attack")),
                   bool(raw.get("melee")), bool(raw.get("triggered")), bool(raw.get("energy")),
                   number(raw.get("castTime")), raw.get("cooldown"), number(raw.get("cooldownRecovery")) or 1.0)


def hits_per_second(stats: dict) -> float:
    return number(stats.get("Speed")) * number(stats.get("HitChance")) / 100


def crits_per_second(stats: dict) -> float:
    return hits_per_second(stats) * number(stats.get("CritChance")) / 100


# Elemental Archon --------------------------------------------------------------------------------
# https://poe2db.tw/us/Elemental_Archon: 25% more Elemental Damage with Spells, no non-elemental
# damage from Spells; "By default, each Archon Buff lasts 10 seconds"; no Archon Buff can be gained
# during "a recovery period after you lose one, which lasts 20 seconds by default"; using a
# non-instant Attack removes it.

ARCHON_DURATION = 10.0
ARCHON_RECOVERY = 20.0
ELEMENTAL_ARCHON_MORE = 25.0
ARCHON_INPUTS = ("ArchonDuration", "ArchonRecovery", "ArchonEffect", "ArchonCritChance", "ArchonCritDamage")
ARCHON_TIMING = ("ArchonDuration", "ArchonRecovery")


def archon_uptime(duration_inc: float, recovery_faster: float, wait: float, no_recovery: bool = False) -> float:
    """Share of a long fight with the Archon Buff up: it lasts D, then the recovery period R, then
    the next gain comes `wait` seconds later. "Expires X% faster/slower" changes how fast the
    recovery period passes."""
    duration = ARCHON_DURATION * max(0.0, 1 + duration_inc / 100)
    if duration <= 0:
        return 0.0
    recovery = 0.0
    if not no_recovery:
        speed = 1 + recovery_faster / 100
        if speed <= 0:
            return 0.0
        recovery = ARCHON_RECOVERY / speed
    return duration / (duration + recovery + max(wait, 0.0))


def archon_effects(x: dict, wait: float, no_recovery: bool) -> dict[str, float]:
    """The Archon Buff's effects averaged over a fight, in percent."""
    uptime = archon_uptime(x["ArchonDuration"], x["ArchonRecovery"], wait, no_recovery)
    effect = max(0.0, 1 + x["ArchonEffect"] / 100)
    return {"uptime": uptime, "effect": effect, "more": ELEMENTAL_ARCHON_MORE * effect * uptime,
            "crit_chance": x["ArchonCritChance"] * effect * uptime,
            "crit_damage": x["ArchonCritDamage"] * effect * uptime}


@dataclass
class ArchonSource:
    name: str
    flag: str | None  # the condition its passive switches on; None: always there (a support gem)
    wait: float  # seconds from the end of the recovery period to the next gain
    basis: str


def archon_sources(stats: dict, main_gems: set[str]) -> list[ArchonSource]:
    """Ways to gain Elemental Archon on a boss, fastest first."""
    sources = [ArchonSource("Seething Body", "ArchonInfernal", 0.0,
                            "regained on the next spell cast at High Infernal Flame; its longer hold at High "
                            "Infernal Flame isn't counted")]
    mana, cost = number(stats.get("Mana")), number(stats.get("ManaPerSecondCost"))
    if mana > 0 and cost > 0:
        spend = mana / cost
        sources.append(ArchonSource("Archon of the Storm", "ArchonMana", spend / 2,
                                    f"spending {mana:,.0f} Mana takes {spend:.1f}s at {cost:,.0f} Mana per second; "
                                    "half of that on average after the recovery ends"))
    if number(stats.get("EnergyShield")) > 0:
        delay = number(stats.get("EnergyShieldRechargeDelay")) or 4.0
        sources.append(ArchonSource("Archon of the Blizzard", "ArchonRecharge", 2 * delay,
                                    f"Energy Shield Recharge begins after {delay:g}s without damage; against a boss "
                                    f"assumed {2 * delay:g}s after the recovery ends"))
    if "Arbiter's Ignition" in main_gems:
        crit = number(stats.get("CritChance")) / 100
        chance = (number(stats.get("IgniteChanceOnHit")) * (1 - crit) + number(stats.get("IgniteChanceOnCrit")) * crit) / 100
        ignites = hits_per_second(stats) * min(chance, 1.0)
        if ignites > 0:
            sources.append(ArchonSource("Arbiter's Ignition", None, 1 / ignites,
                                        f"the main skill ignites {ignites:.2f} times per second"))
    return sorted(sources, key=lambda s: s.wait)


def archon_block(x0: dict, passive: dict, wait: float, no_recovery: bool, when: list, nonelemental: bool) -> list[dict]:
    def effect(key: str) -> Callable[[dict], float]:
        return lambda x: archon_effects(x, wait, no_recovery)[key]

    mods = more_lines("1% more Elemental Damage with Spells", effect("more"), x0, passive,
                      ("ArchonDuration", "ArchonRecovery", "ArchonEffect"), when)
    mods += increased_lines("1% increased Critical Hit Chance", effect("crit_chance"), x0, passive, ARCHON_INPUTS, when)
    mods += increased_lines("1% increased Critical Damage Bonus", effect("crit_damage"), x0, passive, ARCHON_INPUTS, when)
    if nonelemental:  # "No Non-Elemental Damage from Spells" while it lasts
        def lost(x: dict) -> float:
            return -100 * archon_effects(x, wait, no_recovery)["uptime"]
        for kind in ("Physical", "Chaos"):
            mods += more_lines(f"1% more {kind} Damage with Spells", lost, x0, passive, ARCHON_TIMING, when)
    return mods


def archon_model(inputs: Inputs, stats: dict, main: Skill | None, main_gems: set[str]) -> tuple[list[dict], list[dict]]:
    """Elemental Archon for a spell main skill: one block of lines per (best source, recovery
    period or not), each averaged over its uptime."""
    sources = archon_sources(stats, main_gems)
    current = next((s for s in sources if s.flag is None or inputs.has(s.flag)), None)
    if main is None or not main.spell or main.attack:
        notes = [] if current is None else [{
            "label": "Elemental Archon (not counted)", "value": 0,
            "basis": f"the main skill ({main.name if main else 'none'}) isn't a spell; using a non-instant attack "
                     "removes Elemental Archon",
            "confidence": "datamined"}]
        return [], notes
    x0 = {k: inputs.total(k) for k in ARCHON_INPUTS}
    passive = {k: inputs.passive.get(k, 0.0) for k in ARCHON_INPUTS}
    hit = sum(number(stats.get(f"{t}HitAverage")) for t in DAMAGE_TYPES)
    nonelemental = hit > 0 and (number(stats.get("PhysicalHitAverage")) + number(stats.get("ChaosHitAverage"))) > 0.01 * hit
    mods: list[dict] = []
    for i, source in enumerate(sources):
        better = sources[:i]
        if any(b.flag is None or b.flag in inputs.item_flags for b in better):
            break  # a better source is always there: this one never decides
        when = [{"var": source.flag}] if source.flag else []
        if better:
            when.append({"any": [b.flag for b in better], "neg": True})
        for no_recovery in (False, True):
            state = inputs.condition("ArchonNoRecovery", no_recovery)
            if state is not None:
                mods += archon_block(x0, passive, source.wait, no_recovery, when + state, nonelemental)
    notes = []
    if current is not None:
        no_recovery = inputs.has("ArchonNoRecovery")
        e = archon_effects(x0, current.wait, no_recovery)
        duration = ARCHON_DURATION * max(0.0, 1 + x0["ArchonDuration"] / 100)
        recovery = 0.0 if no_recovery else ARCHON_RECOVERY / max(1 + x0["ArchonRecovery"] / 100, 1e-9)
        parts = []
        if e["crit_chance"]:
            parts.append(f"{e['crit_chance']:.1f}% increased Critical Hit Chance")
        if e["crit_damage"]:
            parts.append(f"{e['crit_damage']:.1f}% increased Critical Damage Bonus")
        if nonelemental:
            parts.append(f"{100 * e['uptime']:.0f}% less physical and chaos spell damage")
        notes.append({
            "label": "Elemental Archon: % more Elemental spell damage, averaged over a boss fight",
            "value": round(e["more"], 2),
            "basis": (f"also {', '.join(parts)}; " if parts else "") + f"up {e['uptime']:.0%} of the time: lasts {duration:.1f}s, then "
                     + ("no recovery period" if no_recovery else f"a {recovery:.1f}s recovery period")
                     + f", regained {current.wait:.1f}s later ({current.name}: {current.basis}); "
                     f"effect ×{e['effect']:.2f} (duration and recovery datamined; how soon it's regained assumed)",
            "confidence": "theory"})
    return mods, notes


# Meta-skill Energy -------------------------------------------------------------------------------
# Energy at gem level 20, quality 20 ("energy_generated_+%" in PoB's skill data). Each triggered
# spell costs 10 Energy per 0.1 s of its base cast time (support stat
# "generic_ongoing_trigger_1_maximum_energy_per_Xms_total_cast_time" = 10). Cast on Critical gains
# 1 Energy per Power of the enemy it critically hits, scaled by the share of the enemy's Ailment
# Threshold the hit deals, and its spells deal 20% less damage; the elemental-ailment metas gain 1
# Energy per Power per Shock or Ignite (10 per Freeze, never assumed on a boss).

META_GEMS = {
    "Cast on Critical": {"energy": 57.0, "gain": "crit", "less": 20.0},
    "Cast on Elemental Ailment": {"energy": 72.0, "gain": "ailment"},
    "Elemental Invocation": {"energy": 68.0, "gain": "ailment", "invocation": True},
}
ENERGY_INPUTS = ("MetaEnergy", "MetaEnergyCrit", "MetaEnergyMore", "EnergyRefund", "InvocationHalfCost")
RECENTLY = 4.0  # seconds


def energy_gain(meta: str, stats: dict, main: str) -> tuple[float, str]:
    """Energy per second the main skill earns a meta skill against a boss, before Energy modifiers."""
    kind = META_GEMS[meta]["gain"]
    crit = number(stats.get("CritChance")) / 100
    if kind == "crit":
        crits = crits_per_second(stats)
        crit_hit = sum(number(stats.get(f"{t}CritAverage")) for t in DAMAGE_TYPES)
        threshold = number(stats.get("EnemyAilmentThreshold"))
        share = min(1.0, crit_hit / threshold) if threshold > 0 else 1.0
        per = BOSS_POWER * share
        return crits * per, (f"{main} crits {crits:.2f} times per second (one hit per use) for {per:.2f} Energy each (Power {BOSS_POWER}"
                             f" × {share:.0%}: a {crit_hit:,.0f} critical hit against the boss's {threshold:,.0f} "
                             "Ailment Threshold)")
    shock = (number(stats.get("ShockChanceOnHit")) * (1 - crit) + number(stats.get("ShockChanceOnCrit")) * crit) / 100
    ignite = (number(stats.get("IgniteChanceOnHit")) * (1 - crit) + number(stats.get("IgniteChanceOnCrit")) * crit) / 100
    events = hits_per_second(stats) * (min(shock, 1.0) + min(ignite, 1.0))
    return events * BOSS_POWER, (f"{main} shocks or ignites {events:.2f} times per second (one hit per use) for {BOSS_POWER} Energy each "
                                 "(the boss's Power)")


def energy_rate(meta: str, x: dict, gain: float, cost: float, crits_recently: bool, doubled: bool) -> float:
    """Triggers per second: Energy gained per second over the Energy one round of triggers costs."""
    info = META_GEMS[meta]
    increased = info["energy"] + x["MetaEnergy"] + (x["MetaEnergyCrit"] if crits_recently else 0.0)
    multiplier = max(0.0, 1 + increased / 100) * max(0.0, 1 + x["MetaEnergyMore"] / 100) * (2.0 if doubled else 1.0)
    spent = cost * (1 - min(x["EnergyRefund"], 100.0) / 200)
    if info.get("invocation"):
        spent *= 1 - min(x["InvocationHalfCost"], 100.0) / 200
    return gain * multiplier / spent if spent > 0 else 0.0


def energy_model(inputs: Inputs, stats: dict, skills: list[Skill], main: Skill | None) -> tuple[list, list, set[int]]:
    """Trigger rates for the spells socketed in meta skills that gain Energy, fed by the main skill.
    Returns (mods, notes, groups whose damage counts)."""
    mods: list[dict] = []
    notes: list[dict] = []
    groups: set[int] = set()
    x0 = {k: inputs.total(k) for k in ENERGY_INPUTS}
    passive = {k: inputs.passive.get(k, 0.0) for k in ENERGY_INPUTS}
    doubled = inputs.has("EnergyDoubled")
    crits_recently = crits_per_second(stats) * RECENTLY >= 1
    for meta in (s for s in skills if s.energy and s.group is not None):
        spells = [s for s in skills if s.group == meta.group and s.spell and s.triggered and not s.energy]
        if not spells:
            continue
        names = ", ".join(s.name for s in spells)
        if meta.name not in META_GEMS or main is None or main.group == meta.group:
            for spell in spells:  # PoB would count them as if cast by hand
                mods += trigger_rate(spell.name, 0.0)
            notes.append({"label": f"{meta.name} triggers per second ({names}; not counted)", "value": 0,
                          "basis": "its Energy source isn't modeled" if meta.name not in META_GEMS
                          else "the main skill is one it triggers, so nothing else feeds its Energy",
                          "confidence": "theory"})
            continue
        cost = sum(100 * (s.cast_time or 0.0) for s in spells)
        gain, gain_basis = energy_gain(meta.name, stats, main.name)
        rate = energy_rate(meta.name, x0, gain, cost, crits_recently, doubled)
        groups.add(meta.group)

        def relative(x: dict, meta_name: str = meta.name, gain: float = gain, cost: float = cost,
                     base: float = rate) -> float:
            return 100 * (energy_rate(meta_name, x, gain, cost, crits_recently, doubled) / base - 1) if base else 0.0

        keys = tuple(k for k in ENERGY_INPUTS if k != "MetaEnergyCrit" or crits_recently)
        for spell in spells:
            mods += trigger_rate(spell.name, rate)
            if rate > 0:
                mods += more_lines("1% more Damage", relative, x0, passive, keys, skill=spell.name)
            if META_GEMS[meta.name].get("less"):
                mods.append(line("1% more Damage", -META_GEMS[meta.name]["less"], skill=spell.name))
        info = META_GEMS[meta.name]
        increased = info["energy"] + x0["MetaEnergy"] + (x0["MetaEnergyCrit"] if crits_recently else 0.0)
        notes.append({
            "label": f"{meta.name} triggers per second ({names})", "value": round(rate, 3),
            "basis": f"{gain_basis}; {increased:.0f}% increased Energy (gem {info['energy']:.0f}%)"
                     + (f", {x0['MetaEnergyMore']:.0f}% more" if x0["MetaEnergyMore"] else "")
                     + (", doubled" if doubled else "")
                     + f"; one round costs {cost:.0f} Energy (10 per 0.1s of base cast time)"
                     + (f"; its spells deal {info['less']:.0f}% less damage" if info.get("less") else ""),
            "confidence": "theory"})
    return mods, notes, groups


# Darkness ----------------------------------------------------------------------------------------
# Embrace the Darkness (https://poe2db.tw/Embrace_the_Darkness): "Damage taken is Reserved from
# Darkness before being taken from Life or Energy Shield", and the reservation lasts 5 seconds.
# PoB calculates the pool (base 100, +10 per level, increased maximum) but not what it does.

def with_darkness(stats: dict) -> dict:
    """Maximum hit taken with Darkness: the biggest hit comes out of Darkness before Life and
    Energy Shield, so Darkness adds to every pool. PoB's own value is kept as PoB<Type>Maximum-
    HitTaken (and used again if the stats are adjusted twice)."""
    darkness = number(stats.get("Darkness"))
    if darkness <= 0:
        return stats
    for kind in DAMAGE_TYPES:
        key, raw_key = f"{kind}MaximumHitTaken", f"PoB{kind}MaximumHitTaken"
        raw = stats.get(raw_key, stats.get(key))
        pool = number(stats.get(f"{kind}TotalHitPool"))
        if isinstance(raw, (int, float)) and math.isfinite(raw) and pool > 0:
            stats[raw_key] = raw
            stats[key] = raw * (pool + darkness) / pool
    return stats


def darkness_note(stats: dict) -> dict | None:
    darkness = number(stats.get("Darkness"))
    if darkness <= 0:
        return None
    changes = ", ".join(f"{kind} {number(stats.get(f'PoB{kind}MaximumHitTaken')):,.0f} → "
                        f"{number(stats.get(f'{kind}MaximumHitTaken')):,.0f}" for kind in DAMAGE_TYPES
                        if f"PoB{kind}MaximumHitTaken" in stats)
    return {"label": "Darkness added to maximum hit taken", "value": darkness,
            "basis": "damage taken comes out of Darkness before Life and Energy Shield and returns 5s later, so the "
                     f"whole pool is assumed to meet the biggest hit ({changes}); you have no Spirit",
            "confidence": "reported"}


# Elemental Expression ----------------------------------------------------------------------------
# Invoker's "...and Scatter Them to the Winds": "Trigger Elemental Expression on Melee Critical
# Hit"; the skill has a 0.25 s cooldown (PoB's ElementalExpressionTriggeredPlayer). Each use is a
# fiery explosion, an icy wave or an arcing bolt, with chances in proportion to Strength,
# Intelligence and Dexterity; PoB calculates one of them per group (stat sets 2, 3 and 4).

EXPRESSION = "Elemental Expression"
EXPRESSION_EFFECTS = (("Str", 2, "Fiery Explosion"), ("Int", 3, "Icy Wave"), ("Dex", 4, "Arcing Bolt"))


def expression_rate(main: Skill | None, stats: dict, expression: Skill) -> tuple[float, str]:
    """Elemental Expressions per second: the main skill's melee critical hits, up to the cooldown."""
    if main is None or not (main.melee and main.attack):
        return 0.0, f"triggers on melee critical hits, and the main skill ({main.name if main else 'none'}) isn't a melee attack"
    crits = crits_per_second(stats)
    cooldown = expression.cooldown or 0.25
    cap = expression.cooldown_recovery / cooldown
    return min(crits, cap), (f"{main.name} crits {crits:.2f} times per second; the {cooldown:g}s cooldown allows "
                             f"{cap:.2f} per second")


def expression_effect(stats: dict) -> tuple[int, str, float]:
    """The effect Elemental Expression uses most often: (stat set, name, its chance)."""
    values = {attribute: number(stats.get(attribute)) for attribute, _, _ in EXPRESSION_EFFECTS}
    total = sum(values.values())
    attribute, stat_set, name = max(EXPRESSION_EFFECTS, key=lambda e: values[e[0]])
    return stat_set, name, (values[attribute] / total if total > 0 else 1 / 3)


def expression_model(skills: list[Skill], stats: dict, main: Skill | None) -> tuple[list, list, dict[int, int]]:
    """Trigger rate, note and {group: stat set} for Elemental Expression next to the main skill."""
    expression = next((s for s in skills if s.name == EXPRESSION), None)
    if expression is None or (main is not None and main.name == EXPRESSION):
        return [], [], {}  # as the main skill, the cooldown fallback covers it
    rate, basis = expression_rate(main, stats, expression)
    stat_set, effect, chance = expression_effect(stats)
    if rate > 0:
        basis += (f"; every use counted as {effect}, the most likely effect ({chance:.0%} from your attributes), "
                  "assuming the other two deal about as much")
    note = {"label": "Elemental Expression per second", "value": round(rate, 3), "basis": basis,
            "confidence": "theory" if rate else "datamined"}
    groups = {expression.group: stat_set} if rate > 0 and expression.group is not None else {}
    return trigger_rate(EXPRESSION, rate), [note], groups
