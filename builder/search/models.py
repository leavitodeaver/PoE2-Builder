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
    *(f"MainHand.{t}CritAverage" for t in DAMAGE_TYPES),
    # Barrier Invocation: the Energy Shield each boss hit takes
    *(f"{t}TakenHit" for t in DAMAGE_TYPES),
    # Iron Ward: the boss's physical hits and how much of them the build prevents
    "PhysicalEnemyDamage", "PhysicalDamageReduction", "PhysicalDamageReductionMax", "BasePhysicalDamageReductionWhenHit",
    "Armour", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
    # Harmony Within: unreserved Mana against Life
    "ManaUnreserved", "Life",
    # buff uptimes from the mechanic: Mana Tempest's Mana, Lunar Blessing's Rage
    "ManaRegenRecovery", "MaximumRage",
    # Guided Hand: what kind of damage the main skill deals
    "PhysicalHitAverage",
    # Sanguine Tides: Life spent per second
    "LifePerSecondCost",
})


def number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def _round(value: float) -> float:
    """Five significant digits, so a model's lines only change (and PoB recalculates) when the
    build really moved."""
    return float(f"{value:.5g}")


def line(text: str, scale: float = 1.0, *, when: list | None = None, per: str | None = None,
         stat: str | None = None, base: float = 0.0, skill: str | None = None) -> dict:
    """A modifier line for the bridge: PoB parses `text` (written with a magnitude of 1) and scales
    it by `scale`; with `per` (a model input) or `stat` (a PoB output stat such as Armour), the
    value is scale × that number + base."""
    mod: dict = {"kind": "Line", "line": text, "scale": _round(scale)}
    if when:
        mod["when"] = when
    if per:
        mod["per"] = per
        mod["base"] = _round(base)
    elif stat:
        mod["stat"] = stat
        mod["base"] = _round(base)
    if skill:
        mod["skill"] = skill
    return mod


UNIT = 1_000_000  # a fraction as a bridge model input (PoB multipliers are whole numbers; engine/bridge.lua DERIVED_UNIT)


def derived(name: str, fn: str, value: float, **args) -> dict:
    """A model input the bridge works out again on every calculation (engine/bridge.lua DERIVED), so what-ifs see it
    without a trial: PoB multiplier Ledger<name>, read by lines with per=name. `value` is the build's own (a whole number:
    a fraction comes x UNIT); `fn` and `args` are how the bridge works it out from a what-if's own items or output."""
    return {"kind": "Derived", "name": name, "fn": fn, "value": int(round(value)), "args": args}


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
    level_stats: dict[str, float] = field(default_factory=dict)  # only for skills asked for (Iron Ward)

    @classmethod
    def of(cls, raw: dict) -> Skill:
        return cls(raw["name"], raw.get("group"), bool(raw.get("spell")), bool(raw.get("attack")),
                   bool(raw.get("melee")), bool(raw.get("triggered")), bool(raw.get("energy")),
                   number(raw.get("castTime")), raw.get("cooldown"), number(raw.get("cooldownRecovery")) or 1.0,
                   dict(raw.get("levelStats") or {}))


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
# The others, from their 0.5 skill data (centienergy: 100 = 1 Energy): Cast on Block (Svalinn) 25 Energy per block
# (cast_on_block_gain_X_centienergy_on_block 2500), counted at the boss's hits that land and are blocked; Thundergod's
# Wrath (Mjolner) 3 per Power per melee hit (cast_lightning_spell_on_hit_..._per_monster_power_on_hit 300); Spellslinger
# (Dueling Wand) 0.2 per 10 ms of the cast spell's base cast time (spellslinger_invocation_gain_X_centienergy_per_10ms_base_cast_time
# 20). Energy on kills (Reaper's Invocation) and on minion deaths
# counts none on a boss. Barrier Invocation (poe2db): "When Hit by an Enemy, gain 1 Energy for each 10 Energy Shield lost to
# damage taken" (barrier_invocation_gain_1_energy_per_X_energy_shield_damage_taken_when_hit 10): the boss's hits that land
# per second x the damage each takes after mitigation (PoB's {Type}TakenHit at its boss preset), up to the Energy Shield
# pool, while the build has Energy Shield. Cast on Dodge gains 2 Energy per metre rolled, but neither poe2db nor PoB has the
# base dodge roll distance: not modeled (0, said in the guide) until a source gives it. Cast on Charm Use is item-only.

META_GEMS = {
    "Cast on Critical": {"energy": 57.0, "gain": "crit", "less": 20.0},
    "Cast on Elemental Ailment": {"energy": 72.0, "gain": "ailment"},
    "Elemental Invocation": {"energy": 68.0, "gain": "ailment", "invocation": True},
    "Cast on Block": {"energy": 57.0, "gain": "block", "per": 25.0},
    "Thundergod's Wrath": {"energy": 72.0, "gain": "melee hit", "per": 3.0},
    "Spellslinger": {"energy": 72.0, "gain": "cast", "per": 0.2, "invocation": True},
    "Barrier Invocation": {"energy": 72.0, "gain": "energy shield lost", "per": 0.1, "invocation": True},
}
NO_ENERGY_ON_A_BOSS = {"Reaper's Invocation": "kills", "Cast on Minion Death": "minion deaths"}
# In the 0.5 gem data (flagged item-granted) but granted by no unique, base, rune or passive there, and shown on poe2db
# with no tier and no source: deprecated (MECH2, 7 Nov; the user found no Curse on Block in game). Never picked; a PoB
# code that has one gets no triggers from it, said in the guide.
DEPRECATED = {name: "no item, passive or gem grants it in 0.5 (deprecated)"
              for name in ("Curse on Block", "Cast on Melee Stun", "Cast on Melee Kill")}
NOT_MODELED_YET = {"Cast on Dodge": "the base dodge roll distance (2 Energy per metre) is in neither poe2db nor PoB's data"}
ENERGY_INPUTS = ("MetaEnergy", "MetaEnergyCrit", "MetaEnergyMore", "EnergyRefund", "InvocationHalfCost")
RECENTLY = 4.0  # seconds


def energy_gain(meta: str, stats: dict, main: str, skill: Skill | None = None) -> tuple[float, str]:
    """Energy per second the main skill (or the boss's hits) earns a meta skill against a boss, before Energy modifiers."""
    kind, per = META_GEMS[meta]["gain"], META_GEMS[meta].get("per", 0.0)
    if kind == "block":
        every = number(stats.get("enemySkillTime")) or 0.7
        land = 1 - min(number(stats.get("EvadeChance")), 100) / 100
        block = min(number(stats.get("EffectiveBlockChance")), 100) / 100
        blocks = land * block / every
        return blocks * per, (f"you block {blocks:.2f} of the boss's hits per second (one every {every:g} s, {land:.0%} not evaded, "
                              f"{block:.0%} blocked) for {per:g} Energy each")
    if kind == "energy shield lost":
        pool = number(stats.get("EnergyShield"))
        if pool <= 0:
            return 0.0, "the build has no Energy Shield: no Energy"
        every = number(stats.get("enemySkillTime")) or 0.7
        land = (1 - min(number(stats.get("EvadeChance")), 100) / 100) * (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)
        taken = min(sum(number(stats.get(f"{t}TakenHit")) for t in DAMAGE_TYPES), pool)
        hits = land / every
        return hits * taken * per, (f"the boss's hits land {hits:.2f} times per second (one every {every:g} s, {land:.0%} neither "
                                    f"evaded nor blocked), each taking {taken:,.0f} Energy Shield after mitigation (PoB's boss "
                                    f"preset): 1 Energy per 10 lost")
    if kind == "melee hit":
        if skill is not None and not skill.melee:
            return 0.0, f"{main} is not a melee attack: no Energy"
        hits = hits_per_second(stats)
        return hits * per * BOSS_POWER, f"{main} hits {hits:.2f} times per second for {per * BOSS_POWER:g} Energy each ({per:g} per Power)"
    if kind == "cast":
        if skill is not None and not skill.spell:
            return 0.0, f"{main} is not a spell: no Energy"
        cast = skill.cast_time if skill is not None else 0.0
        each = per * cast * 100
        casts = number(stats.get("Speed"))
        return casts * each, (f"{main} is cast {casts:.2f} times per second for {each:.1f} Energy each ({per:g} per 10 ms of its "
                              f"{cast:g} s base cast time)")
    crit = number(stats.get("CritChance")) / 100
    if kind == "crit":
        crits = crits_per_second(stats)
        # (a spell's per-type averages are PoB's top-level output; an attack's are its main hand's)
        crit_hit = sum(number(stats.get(f"{t}CritAverage")) for t in DAMAGE_TYPES)             or sum(number(stats.get(f"MainHand.{t}CritAverage")) for t in DAMAGE_TYPES)
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
                          "basis": f"it gains Energy on {NO_ENERGY_ON_A_BOSS[meta.name]}: none on a single boss"
                          if meta.name in NO_ENERGY_ON_A_BOSS
                          else f"{meta.name}: {DEPRECATED[meta.name]}" if meta.name in DEPRECATED
                          else f"not modeled yet: {NOT_MODELED_YET[meta.name]}" if meta.name in NOT_MODELED_YET
                          else "its Energy source isn't modeled yet" if meta.name not in META_GEMS
                          else "the main skill is one it triggers, so nothing else feeds its Energy",
                          "confidence": "theory"})
            continue
        cost = sum(100 * (s.cast_time or 0.0) for s in spells)
        gain, gain_basis = energy_gain(meta.name, stats, main.name, main)
        rate = energy_rate(meta.name, x0, gain, cost, crits_recently, doubled)
        if rate > 0:  # (a group whose spells never trigger adds no damage, and mustn't change how the build is scored)
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


# Iron Ward ---------------------------------------------------------------------------------------
# https://poe2db.tw/us/Iron_Ward: "Stores (100—480)% of mitigated Physical damage, up to (65—22886)"
# (gem levels 1—20), "Using this Skill releases the stored damage as a nova of spikes", "Stored
# damage grants up to 200% more Area of Effect". PoB reads none of its warding_steel_* stats, so the
# nova (IronWardNovaPlayer, a Physical area hit) has no base damage and every Iron Ward build is 0
# DPS. The model (ledger entry iron-ward-store, `theory`): against a boss the store fills to its
# maximum between uses, at the boss's hit rate (PoB's boss config, less evaded and blocked hits)
# times the share of each physical hit the build prevents; the full store is the nova's base
# Physical damage, which PoB scales through the nova's own hit calculation.

IRON_WARD = "Iron Ward"
IRON_WARD_ENTRY = "iron-ward-store"
STORE_CAP = "warding_steel_maximum_stored_mitigated_physical_damage"
STORE_GAIN = "warding_steel_gains_%_of_damage_from_mitigated_physical_damage"
STORE_AREA = "warding_steel_area_of_effect_+%_final_per_charge_%"
ARMOUR_RATIO = 10  # PoB: Armour prevents Armour / (Armour + 10 × hit) of a hit (data.misc.ArmourRatio)
MIN_REDUCTION = 1.0  # % prevented assumed at least, so a build without Armour still has a slope to climb


@dataclass
class IronWardStore:
    cap: float  # the most it stores: the nova's base Physical damage when released full
    gain: float  # share of the prevented Physical damage it stores (4.8 = 480%)
    hit: float  # the boss's Physical damage per hit (PoB's boss config)
    reduction: float  # % of that hit the build prevents (Armour and flat damage reduction)
    hits: float  # boss hits taken per second
    use_time: float  # seconds one use takes

    @property
    def fill(self) -> float:
        """Damage stored per second."""
        return self.gain * self.hit * max(self.reduction, MIN_REDUCTION) / 100 * self.hits

    @property
    def use_capped(self) -> bool:
        """Does it refill faster than it can be used?"""
        return self.cap > 0 and self.use_time > 0 and self.fill / self.cap > 1 / self.use_time

    @property
    def uses(self) -> float:
        """Novas per second: one whenever the store is full, at most one per use time."""
        if self.cap <= 0 or self.fill <= 0:
            return 0.0
        return 1 / self.use_time if self.use_capped else self.fill / self.cap


def iron_ward_store(skill: Skill, stats: dict) -> IronWardStore:
    skill_time = number(stats.get("enemySkillTime"))
    hits = ((1 / skill_time if skill_time > 0 else 0.0) * (1 - min(number(stats.get("EvadeChance")), 100) / 100)
            * (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100))
    return IronWardStore(number(skill.level_stats.get(STORE_CAP)), number(skill.level_stats.get(STORE_GAIN)) / 100,
                         number(stats.get("PhysicalEnemyDamage")), number(stats.get("PhysicalDamageReduction")), hits,
                         skill.cast_time or 1.0)


def reduction_slopes(stats: dict) -> tuple[float, float]:
    """How many points the % of a boss hit prevented moves per point of Armour, and per point of
    flat physical damage reduction (PoB: Armour / (Armour + 10 × hit) + flat, up to the cap)."""
    reduction = number(stats.get("PhysicalDamageReduction"))
    if reduction >= (number(stats.get("PhysicalDamageReductionMax")) or 90.0) - 0.5:
        return 0.0, 0.0
    hit = number(stats.get("PhysicalEnemyDamage"))
    if hit <= 0:
        return 0.0, 1.0
    from_armour = min(max(reduction - number(stats.get("BasePhysicalDamageReductionWhenHit")), 0.0), 99.0)
    armour = from_armour * ARMOUR_RATIO * hit / (100 - from_armour)  # the Armour PoB applied to the hit
    return 100 * ARMOUR_RATIO * hit / (armour + ARMOUR_RATIO * hit) ** 2, 1.0


def iron_ward_model(skills: list[Skill], stats: dict, enabled: bool = True) -> tuple[list[dict], list[dict]]:
    """Base damage and use rate for Iron Ward's nova (PoB gives it none), plus lines that let
    what-ifs see Armour and flat reduction speed it up."""
    ward = next((s for s in skills if s.name == IRON_WARD and s.level_stats.get(STORE_CAP)), None)
    if ward is None:
        return [], []
    if not enabled:
        return [], [{"label": "Iron Ward nova", "value": "not counted",
                     "basis": f"the request switches off ledger entry {IRON_WARD_ENTRY}, so the nova deals no damage, "
                              "as in PoB", "confidence": "datamined"}]
    store = iron_ward_store(ward, stats)
    uses = store.uses
    base = store.cap if uses > 0 else 0.0
    mods = [{"kind": "SkillData", "skill": IRON_WARD, "key": "PhysicalMin", "value": _round(base)},
            {"kind": "SkillData", "skill": IRON_WARD, "key": "PhysicalMax", "value": _round(base)}]
    if uses > 0:
        mods.append({"kind": "SkillData", "skill": IRON_WARD, "key": "timeOverride", "value": _round(1 / uses)})
    # the nova's own stat: more area per % of the maximum stored, released full
    area = max(number(s.level_stats.get(STORE_AREA)) for s in skills if s.name == IRON_WARD) * 100
    if area:
        mods.append(line("1% more Area of Effect", area, skill=IRON_WARD))
    # Novas per second follow the % of each hit prevented (until the use time is the limit): one
    # "more" factor per stat, 1 at the current build, so what-ifs adding Armour see the gain
    reduction = max(store.reduction, MIN_REDUCTION)
    if uses > 0 and not store.use_capped:
        for stat, slope in zip(("Armour", "BasePhysicalDamageReductionWhenHit"), reduction_slopes(stats)):
            h = slope / reduction
            if abs(h) > 1e-12:
                mods.append(line("1% more Damage", 100 * h, stat=stat, base=-100 * h * number(stats.get(stat)),
                                 skill=IRON_WARD))
    refill = store.cap / store.fill if store.fill > 0 else math.inf
    note = {
        "label": "Iron Ward nova (released full)",
        "value": f"{uses:.2f} per second for {base:,.0f} base Physical damage",
        "basis": f"stores {store.gain:.0%} of the Physical damage your Armour and damage reduction prevent "
                 f"({reduction:.0f}% of the boss's {store.hit:,.0f} per hit = {store.hit * reduction / 100:,.0f}), up to "
                 f"{store.cap:,.0f}; the boss hits you {store.hits:.2f} times per second (every "
                 f"{number(stats.get('enemySkillTime')):g}s in PoB's boss config, less evaded and blocked hits), so it "
                 f"refills in {refill:.1f}s"
                 + (f", faster than one use every {store.use_time:g}s" if store.use_capped else "")
                 + (f"; {area:.0f}% more area released full" if area else "")
                 + (f"; at least {MIN_REDUCTION:g}% prevented assumed" if store.reduction < MIN_REDUCTION else ""),
        "confidence": "theory"}
    return mods, [note]


# Gem stats PoB never reads (ledger kind gem_stat) ----------------------------------------------
# A skill's stat that no stat map turns into a modifier (the coverage audit's "Iron Ward class",
# builder/ledger/coverage.py) is translated by a `gem_stat` entry into modifier lines with a
# magnitude of 1, scaled by the stat's value at the gem's level and quality × the entry's
# `factor` (an uptime or count assumption). `applies_to: skill` tags them to the skill itself;
# `build` gives them to the whole build while the skill is in use (a buff such as Mana Tempest).
# `if:Name <line>` counts a line only while ledger condition Name is on.

def gem_stat_skills() -> list[str]:
    """Skills whose level stats the bridge must read for gem_stat entries."""
    from builder.ledger import gem_stat_entries

    return sorted({name for e in gem_stat_entries() for name in _skill_names(e)})


def _skill_names(entry) -> list[str]:
    names = entry.raw.get("skill") or []
    return [names] if isinstance(names, str) else list(names)


def gem_stat_model(skills: list[Skill], forbidden: set[str] = frozenset(), stats: dict | None = None) -> tuple[list[dict], list[dict]]:
    from builder.ledger import SYNTAX, gem_stat_entries

    mods: list[dict] = []
    notes: list[dict] = []
    for entry in gem_stat_entries():
        if entry.id in forbidden or not entry.replace:
            continue
        names = _skill_names(entry)
        factor = float(entry.raw.get("factor", 1.0))
        own = entry.raw.get("applies_to", "skill") == "skill"
        for skill in skills:
            if skill.name not in names:
                continue
            name = skill.name
            values = {stat: number(v) for stat, v in skill.level_stats.items()
                      if entry.pattern is not None and entry.pattern.search(stat) and number(v)}
            play, uptime = "", None
            if entry.raw.get("uptime") in UPTIMES:  # derived from the mechanic and the build's own numbers
                factor, why, play = UPTIMES[entry.raw["uptime"]](skill, stats or {})
                why = f"; uptime {factor:.0%}: {why}"
                # (worked out again by the bridge on every calculation, so an item or passive that changes it shows)
                uptime = "Uptime" + re.sub(r"\W", "", name)
                fn, args = UPTIME_INPUTS[entry.raw["uptime"]](skill)
                if values:
                    mods.append(derived(uptime, fn, factor * UNIT, **args))
            else:
                why = ""
            for stat, value in sorted(values.items()):
                if uptime is not None:
                    for text in entry.replace:
                        m = SYNTAX.match(text)
                        when = [{"var": m.group(2)}] if m and m.group(1) == "if" else None
                        mods.append(line(m.group(3) if when else text, value / UNIT, when=when, per=uptime,
                                         skill=name if own else None))
                if value * factor == 0:
                    if why:  # an uptime of 0 from the build's own numbers: the guide says why
                        notes.append({"label": entry.title, "value": "not counted (uptime 0%)",
                                      "basis": f"{name} can't be kept up{why}", "confidence": entry.confidence,
                                      "entry": entry.id, **({"play": play} if play else {})})
                    continue
                for text in entry.replace if uptime is None else ():
                    m = SYNTAX.match(text)
                    when = [{"var": m.group(2)}] if m and m.group(1) == "if" else None
                    mods.append(line(m.group(3) if when else text, value * factor, when=when,
                                     skill=name if own else None))
                notes.append({
                    "label": entry.title,
                    "value": f"{value * factor:g}" + (f" ({value:g} × {factor:g})" if factor != 1 else ""),
                    "basis": f"PoB never reads the gem stat {stat} ({value:g} at this level); counted as "
                             + "; ".join(t.replace("1%", f"{value * factor:g}%") for t in entry.replace)
                             + ("" if own else " for the whole build") + why + (f". {entry.notes.strip()}" if entry.notes else ""),
                    "confidence": entry.confidence, "entry": entry.id, **({"play": play} if play else {})})
            names = [n for n in names if n != name]  # one copy of each skill (a buff applies once)
    return mods, notes


# Oracle's Harmony Within (model: harmony) --------------------------------------------------------
# "Hit damage is taken from Mana before Life if your current Mana is higher than your current Life" (poe2db): counted as
# 100% Mind over Matter while the build's unreserved Mana is above its Life, both pools full (the manager's ruling,
# 6 Nov 2026). PoB has none of it. The line goes in only while the passive's ledger condition is on, so a what-if that
# removes the passive drops it; a build with more Life than unreserved Mana gets nothing.

HARMONY = "HarmonyWithin"


def harmony_model(inputs: Inputs, stats: dict) -> tuple[list[dict], list[dict]]:
    if not inputs.has(HARMONY):
        return [], []
    mana, life = number(stats.get("ManaUnreserved")), number(stats.get("Life"))
    when = inputs.condition(HARMONY, True)
    if when is None:
        return [], []
    # (the bridge tests Mana > Life again on every calculation, so a what-if that crosses it shows)
    mods = [line("1% of Damage is taken from Mana before Life", 100, when=when, per=HARMONY),
            derived(HARMONY, "harmony", 1 if mana > life else 0)]
    if mana <= life:
        return mods, [{"label": "Harmony Within", "value": "not counted",
                       "basis": f"unreserved Mana {mana:,.0f} is not above Life {life:,.0f}, so hits come off Life as usual",
                       "confidence": "theory"}]
    return mods, [{
        "label": "Harmony Within", "value": "hits taken from Mana first",
        "basis": f"unreserved Mana {mana:,.0f} is above Life {life:,.0f}: counted as 100% of damage taken from Mana before Life, "
                 "both pools full (a fight where Mana drains below Life, and overflow of a hit bigger than the Mana, are not "
                 "counted)", "confidence": "theory"}]


# Safrin's Resolve, Eshtera's Path, Zaida's Longevity (model: resist_rings) --------------------------------------------
# "+(5-10)% to Cold and Lightning Resistances per Equipped Item with a Fire Resistance Modifier" (and the other two
# elements; poe2db). PoB ignores the line. Which items count is the user's rule (6 Nov 2026, verified_by_you): an
# equipped item with a resistance modifier of that element, a "+% to all Elemental Resistances" modifier, a
# two-element resistance modifier that includes it, or a socketed rune (or other socketable) granting it.
# Read from the build's item lines (bridge sourceLines, rune lines included), so it counts from the next resolve.

RING_LINE = re.compile(r"^\+?(\d+|\(\d+-\d+\))% to (Fire|Cold|Lightning) and (Fire|Cold|Lightning) Resistances per Equipped Item with an? "
                       r"(Fire|Cold|Lightning) Resistance Modifier$", re.I)
RESIST_LINE = re.compile(r"to (?:all Elemental|(?:Fire|Cold|Lightning|Chaos)(?: and (?:Fire|Cold|Lightning|Chaos))?) Resistances?\b", re.I)
RESIST_ENTRY = "resist-per-item"


def items_with_resistance(lines: list[dict], element: str) -> list[str]:
    """The equipped item slots with a resistance modifier of that element (the user's rule), from bridge sourceLines."""
    slots = set()
    for entry in lines:
        source, text = entry["source"], entry["text"]
        if not source.startswith("item:") or "maximum" in text.lower():
            continue
        m = RESIST_LINE.search(text)
        if not m:
            continue
        named = m.group(0).lower()
        if "all elemental" in named or element.lower() in named:
            slots.add(source[5:])
    return sorted(slots)


def resist_rings_model(lines: list[dict]) -> tuple[list[dict], list[dict]]:
    mods, notes = [], []
    for entry in lines:
        if not entry["source"].startswith("item:"):
            continue
        m = RING_LINE.match(entry["text"].strip())
        if not m:
            continue
        rolled = m.group(1).strip("()").split("-")  # an unrolled unique's range counts at its middle, as PoB reads it
        value = round(sum(int(v) for v in rolled) / len(rolled))
        first, second, element = m.group(2).title(), m.group(3).title(), m.group(4).title()
        slots = items_with_resistance(lines, element)
        total = value * len(slots)
        # (the bridge counts the items again on every calculation, so a what-if that adds or drops one shows)
        mods.append(line(f"+1% to {first} and {second} Resistances", value, per=f"ResistItems{element}"))
        mods.append(derived(f"ResistItems{element}", "resistItems", len(slots), element=element))
        notes.append({"label": f"{entry['source'][5:]}: +{value}% {first} and {second} Resistance per item with {element} Resistance",
                      "value": f"+{total}% ({len(slots)} items)",
                      "basis": f"items with a {element} Resistance modifier (its own, all Elemental, two elements, or a socketed rune): "
                               + (", ".join(slots) or "none") + ". PoB ignores the line; counted from the item lines on every "
                               "calculation, so a change of gear or runes shows in what-ifs.",
                      "confidence": "verified_by_you", "entry": RESIST_ENTRY})
    return mods, notes


# Buff uptimes from the mechanic and the build's own numbers (the user, 6 Nov 2026: "go off their cooldown times if they
# have any, mana cost and regen, etc. ... assume reasonable numbers and leave a note about it in the build guide").

MANA_TEMPEST_DRAIN = 0.01  # of maximum Mana per second (poe2db: "Cost 1% Mana per second")
MANA_TEMPEST_SPENT = 0.30  # "30% of Mana and Life spent while in the storm is added to this Skill's Mana Cost per Second"


def _mana_tempest_spent(skill: Skill) -> float:
    return number(skill.level_stats.get("mana_tempest_mana_cost_%_to_add_to_cost_per_second")) / 100 or MANA_TEMPEST_SPENT


def mana_tempest_uptime(skill: Skill, stats: dict) -> tuple[float, str, str]:
    mana, regen, spend = number(stats.get("Mana")), number(stats.get("ManaRegenRecovery")), number(stats.get("ManaPerSecondCost"))
    spent_share = _mana_tempest_spent(skill)
    drain = MANA_TEMPEST_DRAIN * mana + spent_share * spend
    deficit = spend + drain - regen
    if mana <= 0:
        return 0.0, "no Mana", ""
    if deficit <= 0:
        return 1.0, (f"Mana regenerates {regen:,.0f}/s, more than the main skill's {spend:,.0f}/s plus the storm's drain "
                     f"{drain:,.0f}/s (1% of {mana:,.0f} Mana and 30% of what you spend): the storm lasts as long as you stay in it"), \
            "Assumes you stand in the storm for the whole fight; every step out of it ends it."
    on = mana / deficit
    refill = regen - spend
    if refill <= 0:
        return 0.0, f"the main skill alone spends {spend:,.0f} Mana/s against {regen:,.0f}/s regenerated: no Mana left to keep a storm", ""
    off = mana / refill
    up = on / (on + off)
    return up, (f"the storm drains {drain:,.0f} Mana/s on top of the main skill's {spend:,.0f}/s against {regen:,.0f}/s regenerated: "
                f"{mana:,.0f} Mana lasts {on:.1f} s; then {off:.1f} s to refill without it, recast at full Mana"), \
        "Assumes you recast it whenever your Mana is full again and stand in it; recasting earlier gives shorter storms."


def _lunar_blessing_times(skill: Skill) -> tuple[float, float, float]:
    """(duration, seconds per Rage, cooldown)"""
    duration = number(skill.level_stats.get("base_skill_effect_duration")) / 1000 or 5.0
    per_rage = number(skill.level_stats.get("skill_lunar_blessing_extend_buff_by_X_ms_per_rage")) / 1000 or 0.2
    return duration, per_rage, (skill.cooldown or 22.0) / (skill.cooldown_recovery or 1.0)


def lunar_blessing_uptime(skill: Skill, stats: dict) -> tuple[float, str, str]:
    rage = number(stats.get("MaximumRage"))
    duration, per_rage, cooldown = _lunar_blessing_times(skill)
    length = duration + per_rage * rage
    up = min(1.0, length / max(cooldown, length))
    return up, (f"lasts {duration:g} s + {per_rage:g} s per Rage spent ({rage:.0f} Rage: {length:.1f} s); its cooldown, {cooldown:.1f} s, "
                "recovers while it lasts"), \
        "Assumes you use it off cooldown with full Rage; using it with less Rage shortens it by 0.2 s per Rage."


UPTIMES = {"mana_tempest": mana_tempest_uptime, "lunar_blessing": lunar_blessing_uptime}
# the same uptimes as the bridge works them out (DERIVED.manaTempest, DERIVED.lunarBlessing): (fn, args) for a skill
UPTIME_INPUTS = {
    "mana_tempest": lambda skill: ("manaTempest", {"spent": _mana_tempest_spent(skill)}),
    "lunar_blessing": lambda skill: ("lunarBlessing", dict(zip(("duration", "perRage", "cooldown"), _lunar_blessing_times(skill)))),
}


# Melting Maelstrom (model: maelstrom) -------------------------------------------------------------------------------------
# "Every 3 seconds during Effect, deal 100% of Mana spent in those seconds as Chaos Damage to Enemies within 3 metres" and
# "Deals 25% of current Mana as Chaos Damage to you when Effect ends" (poe2db). PoB has neither. Counted as a separate
# damage source added to the boss DPS (Objective.added_dps): the Mana the build spends per second x the line's share,
# reduced by the boss's chaos resistance (0 at PoB's Pinnacle preset). Assumed not scaled by your modifiers and unable to
# crit (no source says either way), the boss within 3 metres, and the flask's effect up while the search has it on (it
# switches a flask on only when charges keep it up most of the fight). Counted from the next resolve.

MAELSTROM = re.compile(r"^Every 3 seconds during Effect, deal (\d+)% of Mana spent in those seconds as Chaos Damage to Enemies within 3 metres$", re.I)
MAELSTROM_ENTRY = "melting-maelstrom-pulse"


def maelstrom_model(lines: list[dict], stats: dict, enemy_chaos_resist: float = 0.0) -> tuple[float, list[dict]]:
    """(added boss DPS, notes) for a Melting Maelstrom in an active flask slot."""
    for entry in lines:
        m = MAELSTROM.match(entry["text"].strip()) if entry["source"].startswith("item:Flask") else None
        if not m:
            continue
        share = int(m.group(1)) / 100
        spend = number(stats.get("ManaPerSecondCost"))
        dps = spend * share * (1 - enemy_chaos_resist / 100)
        return dps, [{
            "label": "Melting Maelstrom's pulses (added to the boss DPS)",
            "value": round(dps, 1),
            "basis": f"every 3 s, {share:.0%} of the Mana spent in those 3 s as Chaos damage: the main skill spends {spend:,.1f} Mana/s; "
                     f"boss chaos resistance {enemy_chaos_resist:g}% (PoB's boss preset). Assumed: not scaled by your damage "
                     "modifiers and no crits (no source says), the boss within 3 metres, the flask's effect up while the search has "
                     "the flask on. When the effect ends it deals 25% of your current Mana to you as Chaos damage (not counted in "
                     "the checked defences).",
            "play": "Stay within 3 metres of the boss; at range the pulses hit nothing.",
            "confidence": "theory", "entry": MAELSTROM_ENTRY}]
    return 0.0, []


def maelstrom_inputs(lines: list[dict], dps: float, enemy_chaos_resist: float = 0.0) -> list[dict]:
    """The pulses as the bridge works them out again for each what-if (its LedgerAddedDPS, in hundredths)."""
    for entry in lines:
        m = MAELSTROM.match(entry["text"].strip()) if entry["source"].startswith("item:Flask") else None
        if m:
            return [derived("Maelstrom", "maelstrom", dps * 100, share=int(m.group(1)) / 100, chaosResist=enemy_chaos_resist)]
    return []


# Guided Hand (model: guided_hand) --------------------------------------------------------------------------------------
# "The next Attack you use within 4 seconds after Heavy Stunning a Rare or Unique Enemy is Ancestrally Boosted" and
# "Ancestrally Boosted Attacks deal 30% increased Damage" (passive tree). PoB parses the second line but scales it by an
# Ancestral Boost uptime only its known sources set, so on its own the node does nothing. Share of attacks boosted = heavy
# stuns per second / attacks per second. PoB has no stun bar for a boss (nor its life), so the heavy-stun rate is an
# assumption by the build's own kind of damage (poe2db: player Physical damage and Melee each give 50% more stun buildup):
# one Heavy Stun of the boss per 30 s with a Physical melee main skill, per 60 s otherwise. Counted as the equivalent
# increased Attack Damage, only while the passive's ledger condition is on.

GUIDED_HAND = "GuidedHand"
BOOSTED_LINE = re.compile(r"^Ancestrally Boosted Attacks deal (\d+)% increased Damage$", re.I)
HEAVY_STUN_EVERY = {True: 30.0, False: 60.0}  # seconds between Heavy Stuns of a pinnacle boss: Physical melee or not


def guided_hand_model(inputs: Inputs, lines: list[dict], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    if not inputs.has(GUIDED_HAND) or main is None or not main.attack:
        return [], []
    boost = sum(int(m.group(1)) for entry in lines if (m := BOOSTED_LINE.match(entry["text"].strip())))
    speed = number(stats.get("Speed"))
    if boost <= 0 or speed <= 0:
        return [], []
    physical_melee = main.melee and number(stats.get("PhysicalHitAverage")) > 0
    every = HEAVY_STUN_EVERY[physical_melee]
    share = min(1.0, (1 / every) / speed)
    when = inputs.condition(GUIDED_HAND, True)
    if when is None:
        return [], []
    # (the share is worked out again by the bridge from each calculation's attack speed)
    return [line("1% increased Attack Damage", boost / UNIT, when=when, per=GUIDED_HAND),
            derived(GUIDED_HAND, "guidedHand", share * UNIT, every=every)], [{
        "label": "Guided Hand: Ancestrally Boosted attacks after a Heavy Stun",
        "value": f"{share:.1%} of attacks boosted ({boost}% increased Damage on those)",
        "basis": f"one Heavy Stun of the boss every {every:g} s assumed ({'a Physical melee' if physical_melee else 'not a Physical melee'} "
                 f"main skill; PoB has no stun bar for a boss) against {speed:.2f} attacks per second",
        "play": "A build made to stun (more stun buildup, Physical melee slams) heavy-stuns more often and boosts more attacks.",
        "confidence": "theory", "entry": "guided-hand-boost"}]


# Deadly Resolve (model: deadly_resolve) -----------------------------------------------------------------------------------
# The support (poe2db, matching the 0.5 data): "Gain a stage every 1 second" while channelling the supported skill, "3
# maximum stages", "Stage duration is 10 seconds"; "Expend a stage of Deadly Resolve when an enemy Hits you to Trigger a
# deadly slash", "Can Trigger Deadly Resolve once every 0.3 seconds". PoB counts the slash at its own use rate (1 s at the
# build's attack speed: 1.34 per second on 2b75d's reference). On a boss it comes at most once per stage, one per second
# of channelling, and only when the boss hits you: hits that land (PoB's boss preset, one every 0.7 s, less those evaded
# and blocked; poe2db doesn't say whether blocked or evaded hits count, so they're assumed not to). Channelled for the
# whole fight while the supported skill is the main skill; otherwise not counted.

DEADLY_RESOLVE = "Deadly Resolve"
DEADLY_RESOLVE_STAGE_EVERY = 1.0  # seconds of channelling per stage
DEADLY_RESOLVE_COOLDOWN = 0.3


def deadly_resolve_model(skills: list[Skill], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    slashes = [s for s in skills if s.name == DEADLY_RESOLVE and s.triggered]
    if not slashes:
        return [], []
    slash = slashes[0]
    if main is None or slash.group != main.group:
        return trigger_rate(DEADLY_RESOLVE, 0.0), [{
            "label": "Deadly Resolve slashes per second", "value": 0,
            "basis": "its channelled skill isn't the main skill, so it isn't channelled through the fight: not counted",
            "confidence": "theory", "entry": "deadly-resolve-rate"}]
    every = number(stats.get("enemySkillTime")) or 0.7
    land = (1 - min(number(stats.get("EvadeChance")), 100) / 100) * (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)
    hits = land / every
    rate = min(1 / DEADLY_RESOLVE_STAGE_EVERY, hits, 1 / DEADLY_RESOLVE_COOLDOWN)
    limit = "a stage per second of channelling" if rate < hits else "the boss's hits that land"
    return trigger_rate(DEADLY_RESOLVE, rate), [{
        "label": "Deadly Resolve slashes per second", "value": round(rate, 3),
        "basis": f"one per stage (a stage per {DEADLY_RESOLVE_STAGE_EVERY:g} s of channelling {main.name}) when the boss hits you: "
                 f"{hits:.2f} hits/s land (one every {every:g} s, {land:.0%} neither evaded nor blocked; blocked and evaded hits "
                 f"assumed not to count); limited by {limit}. PoB alone counts it at its own use rate.",
        "play": "Channel the supported skill through the fight and let the boss hit you; stepping out of its hits stops the slashes.",
        "confidence": "theory", "entry": "deadly-resolve-rate"}]


# Blood Mage's Sanguine Tides (model: sanguine) ----------------------------------------------------------------------------
# "Gain 1 Life Flask Charge per 2% Life spent"; "On Hitting an Enemy while a Life Flask is at full Charges, 40% of its
# Charges are consumed"; "Gain 1% of damage as Physical damage for 5 seconds per Charge consumed this way" (poe2db). In
# steady state every charge filled is consumed, so the buff holds 5 s x charges per second x 1%: 2.5 x the % of Life spent
# per second (the main skill's Life cost per second over maximum Life). Counted from the next resolve (PoB's Life cost per
# second is worked out after the modifiers a what-if would re-read).

SANGUINE = "SanguineTides"
SANGUINE_PER_PERCENT = 5.0 / 2.0  # 5 s per charge, 1 charge per 2% of Life spent


def sanguine_model(inputs: Inputs, stats: dict) -> tuple[list[dict], list[dict]]:
    if not inputs.has(SANGUINE):
        return [], []
    life, spent = number(stats.get("Life")), number(stats.get("LifePerSecondCost"))
    when = inputs.condition(SANGUINE, True)
    if when is None:
        return [], []
    gain = SANGUINE_PER_PERCENT * spent / life * 100 if life > 0 and spent > 0 else 0.0
    # (worked out again by the bridge from each calculation's Life and Life cost)
    mods = [line("Gain 1% of Damage as Extra Physical Damage", 1 / UNIT, when=when, per=SANGUINE),
            derived(SANGUINE, "sanguine", gain * UNIT, perPercent=SANGUINE_PER_PERCENT)]
    if gain <= 0:
        return mods, [{"label": "Sanguine Tides", "value": "not counted", "basis": "the build spends no Life on its skills",
                       "confidence": "theory"}]
    return mods, [{
        "label": "Sanguine Tides: damage gained as Physical", "value": f"{gain:.1f}%",
        "basis": f"the main skill spends {spent:,.0f} Life/s ({spent / life:.1%} of {life:,.0f} per second): 1 Life Flask charge per 2% spent, "
                 "each consumed charge 1% for 5 s; every charge filled assumed consumed (you hit the boss whenever the flask is full)",
        "play": "Hitting less often than the flask fills, or a flask that never fills, lowers it.", "confidence": "theory"}]


# What-ifs and the models (the what-if limit) ---------------------------------------------------------------------------
# Model inputs the bridge can work out (item counts, uptimes from Mana, Life and speed) are worked out again on every
# calculation (derived(), engine/bridge.lua DERIVED), so plain what-ifs see them. An item that carries a model itself (a
# resistance ring, Melting Maelstrom, model inputs on its lines) still counts from the next resolve: Build.item_deltas
# re-scores the best few such candidates (or those that replace such an item) with a real trial, models included.

def item_read_by_models(raw: str, build_texts: list[str]) -> bool:
    """Does a model read this item's lines? (Its own model inputs, Melting Maelstrom, a resistance ring: the item carries
    the model itself. The items a ring counts are counted by the bridge on every calculation; `build_texts` is kept for
    models that read the build's lines.)"""
    lines = [l.strip() for l in (raw or "").splitlines() if l.strip()]
    return any(model_entry(text) is not None or MAELSTROM.match(text) or RING_LINE.match(text) for text in lines)
