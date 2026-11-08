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
    "EnergyShieldRegenRecovery", "EnergyShieldRecharge",
    "EnemyAilmentThreshold", "ShockChanceOnHit", "ShockChanceOnCrit", "IgniteChanceOnHit", "IgniteChanceOnCrit",
    "FreezeBuildupAvg", "ElectrocuteBuildupAvg",
    "Darkness", "Str", "Dex", "Int", *(f"{t}CritAverage" for t in DAMAGE_TYPES), *(f"{t}HitAverage" for t in DAMAGE_TYPES),
    *(f"MainHand.{t}CritAverage" for t in DAMAGE_TYPES),
    # Barrier Invocation: the Energy Shield each boss hit takes
    *(f"{t}TakenHit" for t in DAMAGE_TYPES),
    # Iron Ward: the boss's physical hits and how much of them the build prevents
    "PhysicalEnemyDamage", "PhysicalDamageReduction", "PhysicalDamageReductionMax", "BasePhysicalDamageReductionWhenHit",
    "Armour", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
    # Spell Totem: how long PoB says a totem lasts, and the charges it can be raised with
    "TotemDuration", "PowerChargesMax", "EnduranceChargesMax",
    # Harmony Within: unreserved Mana against Life
    "ManaUnreserved", "Life",
    # buff uptimes from the mechanic: Mana Tempest's Mana, Lunar Blessing's Rage
    "ManaRegenRecovery", "MaximumRage",
    # Ferocious Roar: Rage PoB already counts (a build that can gain it)
    "Rage",
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
    wrong_weapon: bool = False  # needs a weapon type in neither hand (PoB disables only Attacks for that)
    corpses: bool = False  # consumes or targets corpses (the game's skill types; corpse_model)
    level: int | None = None  # gem level of a meta skill that gains Energy (gem_energy)

    @classmethod
    def of(cls, raw: dict) -> Skill:
        return cls(raw["name"], raw.get("group"), bool(raw.get("spell")), bool(raw.get("attack")),
                   bool(raw.get("melee")), bool(raw.get("triggered")), bool(raw.get("energy")),
                   number(raw.get("castTime")), raw.get("cooldown"), number(raw.get("cooldownRecovery")) or 1.0,
                   dict(raw.get("levelStats") or {}), bool(raw.get("wrongWeapon")),
                   bool(raw.get("corpses")), raw.get("level"))


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
# Energy from the meta gem's level (game files, build 26476662, GrantedEffectStatSetsPerLevel "energy_generated_+%"): 3% per
# level above 1 (57% at 20, 117% at 40) for every one below but Elemental Invocation, round(3.6% per level above 1) (68% at 20).
# Quality adds none (GrantedEffectQualityStats has no row for any of them). PoB's data gave 72% at 20/20 to some, which counted
# Thundergod's Wrath, Cast on Elemental Ailment, Spellslinger and Barrier Invocation 15 points high, and Mjolner's level-11
# Thundergod's Wrath (30%) as 72%. A level the bridge doesn't report is taken as 20 (labelled). Each triggered
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
# pool, while the build has Energy Shield. Cast on Dodge: "Gain 2 Energy per metre travelled while dodge rolling" (its 0.5
# description of cast_on_dodge_roll_gain_X_centienergy_per_unit_travelled_while_dodge_rolling 200, shown /100 per metre;
# the stat's name says per unit, 10 units a metre, which would be ten times more: the game's own text is used), over the
# dodge roll's base distance, dodge_roll_base_travel_distance 46 units = 4.6 m (DodgeRollPlayer's constant stats, shown
# /10 as metres; the user had heard 3.7 m, from an older source), at DODGE_EVERY seconds between rolls against a boss (the
# user's default). A build whose damage counts on Cast on Dodge also loses the time its rolls take from its main skill
# (the user, 7 Nov): DODGE_ROLL_SECONDS per roll, as less Attack and Cast Speed; other builds aren't charged.

META_GEMS = {
    "Cast on Critical": {"per_level": 3.0, "gain": "crit", "less": 20.0},
    "Cast on Elemental Ailment": {"per_level": 3.0, "gain": "ailment"},
    "Elemental Invocation": {"per_level": 3.6, "gain": "ailment", "invocation": True},
    "Cast on Block": {"per_level": 3.0, "gain": "block", "per": 25.0},
    "Thundergod's Wrath": {"per_level": 3.0, "gain": "melee hit", "per": 3.0},
    "Spellslinger": {"per_level": 3.0, "gain": "cast", "per": 0.2, "invocation": True},
    "Barrier Invocation": {"per_level": 3.0, "gain": "energy shield lost", "per": 0.1, "invocation": True},
    "Cast on Dodge": {"per_level": 3.0, "gain": "dodge", "per": 2.0},
    # Smith of Kitava's Heat of the Forge: the same stats as Thundergod's Wrath (cast_fire_spell_on_hit_gain_X_centienergy_
    # per_monster_power_on_hit 300, the same level table; game files), Fire spells. The game and the tree call it Fire Spell
    # on Hit, PoB's skill Fire Spell on Melee Hit; the notable gives no level, so PoB's is used (the bridge reports it).
    "Fire Spell on Hit": {"per_level": 3.0, "gain": "melee hit", "per": 3.0},
    "Fire Spell on Melee Hit": {"per_level": 3.0, "gain": "melee hit", "per": 3.0},
}
META_LEVEL_ASSUMED = 20  # a meta gem whose level the bridge doesn't report


def gem_energy(meta: str, level: int | None) -> float:
    """The meta gem's own increased Energy at its level (META_GEMS "per_level"; game files)."""
    return float(int(META_GEMS[meta]["per_level"] * (max(level or META_LEVEL_ASSUMED, 1) - 1) + 0.5))
# Supports of a meta group that change its Energy, which PoB doesn't read (game files, build 26476662,
# GrantedEffectStatSets): Boundless Energy I and II "energy_generated_+%" 35 and 45, Energy Retention
# "trigger_skills_refund_half_energy_spent_chance_%" 35. They count for the meta skill they support only.
META_SUPPORTS = {"Boundless Energy I": ("MetaEnergy", 35.0), "Boundless Energy II": ("MetaEnergy", 45.0),
                 "Energy Retention": ("EnergyRefund", 35.0)}
INVOCATION_MAX_ENERGY = 500  # generic_ongoing_trigger_maximum_energy of every Invocation (game files, build 26476662)
DODGE_ROLL_METRES = 4.6  # dodge_roll_base_travel_distance 46 units (game files, build 26476662); distance mods not counted
DODGE_EVERY = 2.0  # seconds between dodge rolls against a boss: the user's default (7 Nov 2026), said in the guide
DODGE_ROLL_SECONDS = 0.75  # DodgeRollPlayer's cast time, 750 ms (game files); faster rolls from movement speed not counted
NO_ENERGY_ON_A_BOSS = {"Reaper's Invocation": "kills", "Cast on Minion Death": "minion deaths"}
# Gone from the game by two sources that agree (the rule for removing anything): the game files reach them by no source
# (data/game-*/live.json: no uncut tier, base item, passive, or collection unique grants them) and poe2db shows them
# with no tier or source; the user also found no Curse on Block in game (7 Nov). Never picked; a PoB code that has one
# gets no triggers from it, said in the guide.
DEPRECATED = {name: "removed from the game (the 0.5 game files and poe2db agree; deprecated)"
              for name in ("Curse on Block", "Cast on Melee Stun", "Cast on Melee Kill")}
NOT_MODELED_YET = {
    "Feral Invocation": "a clone uses its socketed Attacks (Energy cost per 10 ms of base attack time; 1 Energy per 20 Mana "
                        "spent): the Energy model prices triggered Spells only so far",
    "Cast on Charm Use": "its Energy (2 per charge used) needs the charm uses from the flask model",
}
# Meta skills that gain no Energy: what each does, and what the search needs to use it (PLAN.md "Meta gems, all of them").
# Every Meta gem the game files mark live (data/game-*/live.json) is in exactly one of META_GEMS, NO_ENERGY_ON_A_BOSS,
# NOT_MODELED_YET, OTHER_METAS or DEPRECATED (tests/test_models.py), so none is dropped silently.
OTHER_METAS = {
    "Blasphemy": "turns socketed Curses into Auras (60 Spirit per Curse): a Spirit choice, not a trigger (the Spirit step, gems.blasphemy_option)",
    "Pounce": "a Werewolf slam that triggers a socketed Mark on the highest-rarity enemy hit (the marks round, gems.mark_round)",
    "Ferocious Roar": "a Bear Warcry: Rage per enemy Power and Armour Break (the roar round, gems.roar_round; roar_model), or a "
                      "socketed human-form Warcry it triggers (PoB prices that as a support; the search doesn't propose Warcries yet)",
    "Ancestral Warrior Totem": "a totem whose spirit warriors use socketed Attacks, consuming 3 Endurance Charges",
    "Mortar Cannon": "a Ballista Totem that uses socketed Grenade skills",
    "Hand of Chayula": "a Strike that triggers socketed Marks with increased effect (the marks round; the increased effect isn't read)",
    "Called Shots": "Deadeye notable: applies a socketed Mark to an enemy every five seconds (the ascendancy round tries the notable with a Mark from the marks round in it)",
    "Hollow Form": "Martial Artist (Monk) notable: a channelled meta whose images use the socketed melee Attack, 2 more per Power Charge "
                   "consumed (the ascendancy round tries the notable with the main skill moved into it, gems.hollow_round; "
                   "hollow_model)",
    "Spirit Vessel": "Forgotten Warden's companion that uses copies of socketed Bear, Werewolf and Wyvern skills",
    "Animus Splinters": "0.5 league content (Runic Ward cost); its source isn't read from the game files yet",
}


# Totem metas: the main skill goes into the totem (the search's totem round, gems.Gems.totem_round), and a model prices
# what PoB doesn't (totem_model). Not an Energy trigger, so meta_handling leaves them to that round.
TOTEM_METAS = {
    "Spell Totem": "a totem that casts the main Spell, consuming 3 Power and/or Endurance Charges",
}
# Spell Totem in the game files (build 26476662, GrantedEffectStatSets SummonMetaTotemSpellTotemPlayer and its per-level
# rows): a totem lasts 8 s (base_totem_duration 8000), 20% longer (final) per Endurance Charge consumed; its spells deal 10%
# more damage (final) per Power Charge consumed; 3 charges per totem; base_number_of_totems_allowed 1 at gem levels 1-13,
# 2 at 14-22, 3 from 23. PoB calculates one totem casting the spell at 25% less cast speed (the gem's support part), fixes
# the limit at 2, and reads neither the charges nor how many totems are up.
SPELL_TOTEM_CHARGES = 3
SPELL_TOTEM_DURATION = 8.0
SPELL_TOTEM_DURATION_PER_ENDURANCE = 0.20
SPELL_TOTEM_DAMAGE_PER_POWER = 10.0
SPELL_TOTEMS_ALLOWED = ((23, 3), (14, 2), (1, 1))  # (from gem level, totems)


def totems_allowed(level: int) -> int:
    return next(n for at, n in SPELL_TOTEMS_ALLOWED if level >= at)


def cast_by_totem(skills: list[Skill], skill: Skill | None) -> bool:
    """The skill is socketed in a totem meta (its group has one): the totem uses it, not you."""
    return skill is not None and any(s.name in TOTEM_METAS and s.group == skill.group for s in skills)


def totem_model(skills: list[Skill], stats: dict, texts: list[str], levels: dict[str, int],
                main: Skill | None) -> tuple[list[dict], list[dict]]:
    """Spell Totem with the main spell socketed: how many totems are up, from the Power and Endurance Charges the build
    makes (3 per totem), and the damage its Power Charges add. PoB's DPS is one totem's."""
    from builder.search.conditions import _charge_rate

    if not cast_by_totem(skills, main) or not main.spell:
        return [], []
    # The totem's hits aren't yours (assumed, the cautious reading): charges made on your hits, crits or kills don't
    # count, only those made on a timer, on blocking or on being hit.
    yours = dict(stats, Speed=0.0)
    rates = {}
    for kind in ("Power", "Endurance"):
        found = [r for r in (_charge_rate(t, kind, yours) for t in texts) if r is not None]
        rates[kind] = sum(r[0] for r in found) if number(stats.get(f"{kind}ChargesMax")) > 0 else 0.0
    made = rates["Power"] + rates["Endurance"]
    power = rates["Power"] / made if made else 0.0  # (the charges are spent as they're made: their mix is the build's)
    level = levels.get("Spell Totem", 20)
    allowed = totems_allowed(level)
    duration = (number(stats.get("TotemDuration")) or SPELL_TOTEM_DURATION) * (
        1 +SPELL_TOTEM_DURATION_PER_ENDURANCE * SPELL_TOTEM_CHARGES * (1 - power))
    up = min(float(allowed), made * duration / SPELL_TOTEM_CHARGES)
    more = SPELL_TOTEM_DAMAGE_PER_POWER * SPELL_TOTEM_CHARGES * power
    mods: list[dict] = []
    if up <= 0:
        mods.append(line("1% more Damage", -100.0, skill=main.name))
    else:
        if abs(up - 1) > 1e-9:
            mods.append(line("1% more Attack and Cast Speed", 100 * (up - 1), skill=main.name))
        if more:
            mods.append(line("1% more Damage", more, skill=main.name))
    basis = (f"{rates['Power']:.2f} Power and {rates['Endurance']:.2f} Endurance Charges made per second by what isn't "
             f"{main.name}'s hits (the totem's hits aren't yours: assumed); {SPELL_TOTEM_CHARGES} per totem, which lasts "
             f"{duration:.1f} s ({SPELL_TOTEM_DURATION_PER_ENDURANCE:.0%} longer per Endurance Charge consumed); "
             f"{allowed} totems allowed at gem level {level} (game files; PoB fixes 2): {up:.2f} up on average, and PoB "
             "counts one")
    notes = [{"label": f"Spell Totems up on average ({main.name})", "value": round(up, 2), "basis": basis
              + ("; no charges made, so no totem: no damage" if up <= 0 else "")
              + (f"; {more:.0f}% more damage from the Power Charges consumed ({SPELL_TOTEM_DAMAGE_PER_POWER:g}% each, "
                 "which PoB doesn't read)" if more and up > 0 else "")
              + "; ignites don't add up across totems (only the strongest deals damage), so the extra totems count as "
                "cast speed; raising a totem (0.5 s) isn't counted against your time",
              "confidence": "theory"}]
    return mods, notes



# Mirages (slice 7) -------------------------------------------------------------------------------
# Mirage Archer (a 60 Spirit Persistent meta) creates a Mirage when you dodge roll; Mirage Deadeye (a Deadeye notable's)
# when you fire a ranged Projectile Attack. The Mirage uses the socketed ranged Attack for its duration, then vanishes.
# Game files (build 26476662): MirageArcherSpawnPlayer / MirageDeadeyeSpawnPlayer have a 10 s cooldown, 1 stored use,
# in the same cooldown group (67) for both, so one Mirage per 10 s between them (a shared cooldown: assumed from the group);
# Mirages last base_skill_effect_duration 5000 + 50 x gem level ms (Archer: 5.05 s at 1, 6 s at 20) and 4000 + 50 x level
# (Deadeye), +50 ms per quality %; they deal 30% / 50% less damage (support_lingering_mirage_damage_+%_final). poe2db
# agrees (cooldown, durations, less damage; several Mirages at once since 0.4.0). PoB prices the socketed Attack as if you
# used it all the time at the less damage (its Mirage Archer code is PoE1's and never runs): the model scales it to the
# share of time a Mirage is up.
MIRAGE_METAS = {
    "Mirage Archer": "dodge rolling creates a Mirage that uses the socketed ranged Attack (the mirage round, gems.mirage_round)",
    "Mirage Deadeye": "Deadeye notable: firing a ranged Projectile Attack creates a Mirage that uses the socketed ranged Attack "
                      "(the ascendancy round tries the notable with a ranged Attack from the mirage round in it)",
}
MIRAGE_COOLDOWN = 10.0
MIRAGE_BASE_MS = {"Mirage Archer": 5000.0, "Mirage Deadeye": 4000.0}
MIRAGE_MS_PER_LEVEL = 50.0
MIRAGE_MS_PER_QUALITY = 50.0


def mirage_duration(meta: str, level: int, quality: int) -> float:
    return (MIRAGE_BASE_MS[meta] + MIRAGE_MS_PER_LEVEL * level + MIRAGE_MS_PER_QUALITY * quality) / 1000


def mirage_model(skills: list[Skill], main: Skill | None, gems: dict[str, tuple[int, int]],
                 rolling: bool = False) -> tuple[list[dict], list[dict], set[int]]:
    """Each Mirage's Attack scaled from PoB's always-on to the share of time its Mirage is up, and the dodge roll Mirage
    Archer needs. `gems` is {name: (level, quality)}; `rolling` when the build already counts on rolling every
    DODGE_EVERY s (Cast on Dodge). Returns (mods, notes, groups whose damage counts)."""
    mods: list[dict] = []
    notes: list[dict] = []
    groups: set[int] = set()
    placed = []
    for meta in ("Mirage Deadeye", "Mirage Archer"):  # (Deadeye's comes first: it fires on every shot)
        source = next((s for s in skills if s.name == meta), None)
        attack = source and next((s for s in skills if s.group == source.group and s.attack and s.name != meta), None)
        if attack is not None:
            placed.append((meta, source, attack))
    for i, (meta, source, attack) in enumerate(placed):
        level, quality = gems.get(meta, (20, 0))
        duration = mirage_duration(meta, level, quality)
        if i > 0:
            up, basis = 0.0, (f"{placed[0][0]} takes every Mirage: the two share one cooldown (the game files' cooldown "
                              f"group, assumed shared), and {placed[0][0]}'s comes first")
        elif meta == "Mirage Deadeye" and not (main is not None and main.attack and not main.melee):
            up, basis = 0.0, "nothing fires a ranged Attack to create the Mirage (the main skill isn't one)"
        else:
            period = MIRAGE_COOLDOWN
            if meta == "Mirage Archer" and rolling:
                period = math.ceil(MIRAGE_COOLDOWN / DODGE_EVERY) * DODGE_EVERY  # (the first roll after the cooldown)
            up = min(1.0, duration / period)
            how = ("on the first dodge roll after its cooldown" if meta == "Mirage Archer"
                   else f"on the first shot of {main.name} after its cooldown")
            basis = (f"one Mirage every {period:g} s ({how}; game files: 10 s cooldown), lasting {duration:.2f} s at gem "
                     f"level {level}, quality {quality} (duration modifiers not counted); PoB counts {attack.name} as used "
                     "all the time")
        mods.append(line("1% more Damage", 100 * (up - 1), skill=attack.name))
        if up > 0:
            groups.add(source.group)
        notes.append({"label": f"{meta}: share of time a Mirage uses {attack.name}", "value": round(up, 2),
                      "basis": basis + "; the Mirage's hits are counted as yours by conditions (assumed); its Attack "
                               "can't consume charges (game files), which PoB doesn't read", "confidence": "theory"})
        if meta == "Mirage Archer" and up > 0 and not rolling and main is not None:
            busy = DODGE_ROLL_SECONDS / MIRAGE_COOLDOWN
            mods.append(line("1% more Attack and Cast Speed", -100 * busy, skill=main.name))
            notes.append({"label": f"Time spent dodge rolling for Mirage Archer ({main.name})",
                          "value": f"{busy:.1%} less Attack and Cast Speed",
                          "basis": f"one roll per Mirage, every {MIRAGE_COOLDOWN:g} s, takes {DODGE_ROLL_SECONDS:g} s (the "
                                   f"dodge roll's cast time in the game files) from {main.name}", "confidence": "theory"})
    return mods, notes, groups


# Hollow Form (slice 8) ---------------------------------------------------------------------------
# Martial Artist's (Monk) Hollow Form Technique grants Hollow Form, a channelled meta (a button: Meta + Attack). Game files (build
# 26476662): MetaHollowFormPlayer channels at 70% of the weapon's attack speed (attackSpeedMultiplier -30) and consumes
# a Power Charge per use (SkillConsumesPowerChargesOnUse) to create 2 more images
# (mantra_of_illusions_bonus_illusions_when_consuming_power_charge 2); each image performs the socketed Melee Attack once.
# SupportHollowFormPlayer gives that Attack 30% less attack speed and 15% less damage, and it can't consume charges
# (cannot_consume_power_frenzy_endurance_charges). poe2db agrees ("Consumes a Power Charge to create 2 additional Images",
# 15% less damage, 30% less attack speed). PoB prices the socketed Attack as used by you at those penalties and reads
# neither the images nor the charges. No stat gives the images made per use without a charge: one is assumed, so PoB's
# number stands for that, and the model adds the 2 per Power Charge on the share of uses that find one.
HOLLOW_FORM = "Hollow Form"
HOLLOW_IMAGES_PER_POWER = 2
HOLLOW_EXCLUDES = {"HasUsageCondition", "Meta", "Triggered", "Persistent", "Channel"}  # (the support's excludeSkillTypes)


def in_hollow_form(skills: list[Skill], skill: Skill | None) -> bool:
    """The skill is the melee Attack socketed in Hollow Form: you channel Hollow Form and its images use it."""
    return skill is not None and skill.attack and skill.melee and any(
        s.name == HOLLOW_FORM and s.group == skill.group for s in skills)


def hollow_model(skills: list[Skill], stats: dict, texts: list[str], main: Skill | None) -> tuple[list[dict], list[dict]]:
    """The images Hollow Form's Power Charges add: 2 per use that finds a charge (one use per hit PoB counts)."""
    from builder.search.conditions import _charge_rate

    if not in_hollow_form(skills, main):
        return [], []
    uses = number(stats.get("Speed"))
    found = [r for r in (_charge_rate(t, "Power", stats) for t in texts) if r is not None]
    made = sum(r[0] for r in found) if number(stats.get("PowerChargesMax")) > 0 else 0.0
    share = min(1.0, made / uses) if uses > 0 else 0.0
    more = 100.0 * HOLLOW_IMAGES_PER_POWER * share
    mods = [line("1% more Damage", more, skill=main.name)] if more else []
    basis = (f"{made:.2f} Power Charges made per second against {uses:.2f} uses of Hollow Form (one per {main.name} PoB "
             f"counts; Hollow Form channels at 70% attack speed, the socketed Attack at 30% less: assumed equal, the "
             f"Attack's own attack-time modifier not counted); {share:.0%} of uses consume one for "
             f"{HOLLOW_IMAGES_PER_POWER} more images (game files, poe2db); one image per use without a charge (assumed: no "
             "stat gives it), which is what PoB's number counts; the images' hits are counted as yours (assumed, as "
             "Mirages'); Power Charges spent this way aren't held (conditions' charge consumers)")
    notes = [{"label": f"Hollow Form: images per use ({main.name})", "value": round(1 + HOLLOW_IMAGES_PER_POWER * share, 2),
              "basis": basis + ("" if made else "; the build makes no Power Charges"), "confidence": "theory"}]
    return mods, notes

# Marks (slice 6) ---------------------------------------------------------------------------------
# PoB applies a Mark from any enabled group as a debuff that is always up and never consumed (CalcPerform's curse slots,
# EnemyMarkLimit 1), and a Mark's buff part (Voltaic's and Freezing's 30% gain as extra damage) as always yours. In the
# game files (build 26476662, GrantedEffectsPerLevel and GrantedEffectStatSets *MarkPlayer): every Mark lasts 8 s
# (base_skill_effect_duration 8000) and casts in 0.5 s; Sniper's Mark has a 6 s cooldown, the others 0.5 s.
# - Sniper's Mark: the next Critical Hit the target takes consumes it (extra crit damage, a Frenzy Charge), so its bonus
#   lands on one crit per Mark, not on every crit.
# - Voltaic / Freezing Mark: when the marked target is Electrocuted / Frozen the Mark is consumed and you gain a buff for
#   10 s (base_secondary_skill_effect_duration 10000): 30% of damage as extra Lightning / Cold. A pinnacle boss is
#   electrocuted or frozen about once per 30 s (the catalog's buildup rule, conditions._charge_rate), assumed.
# - Bloodhound's Mark: consumed on Heavy Stun to trigger Blood Explosion; PoB adds nothing to the main skill's damage.
MARK_DURATION = 8.0
MARK_BUFF_DURATION = 10.0
# Ferocious Roar (slice 7; model: roar_model, conditions.Conditions.roar_evidence) ----------------------------------------
# Game files (build 26476662, FerociousRoarMetaPlayer): 1.1 s cast, 8 s cooldown; gains 200 centirage per enemy Power
# (bear_warcry_gain_X_centirage_per_enemy_power, quality +5 per %); Empowers attacks, one per 8 Power (cap 64), to Break
# X Armour on hit (warcry_grant_break_X_armour_on_hit: 6147 at gem level 20, 18273 at 40). With a human-form Warcry
# socketed it triggers that Warcry instead (SupportFerociousRoarPlayer: +30% to +132% damage, +50% area), which PoB
# prices as a support. Keyword texts (KeywordPopups): "Rage grants 1% more Attack damage per 1 Rage. By default, you have
# 30 maximum Rage and lose 1 Rage every 0.2 seconds. Rage loss is paused for 4 seconds upon gaining Rage, or after taking
# damage"; Armour brought to 0 "is Fully Broken for 12 seconds". PoB reads none of the roar's own stats (only its
# description, which no condition takes: it names Warcries and Attacks), and counts Rage only for builds that can gain it.
FEROCIOUS_ROAR = "Ferocious Roar"
ROAR_COOLDOWN = 8.0
ROAR_EMPOWER_PER_POWER, ROAR_EMPOWER_POWER_CAP = 8, 64
RAGE_MAXIMUM, RAGE_HOLD, RAGE_LOSS = 30.0, 4.0, 5.0  # Rage, s without loss after gaining it, Rage lost per second
FULL_BREAK_DURATION = 12.0


def roar(skills: list[Skill]) -> tuple[Skill | None, bool]:
    """The build's Ferocious Roar, and whether it triggers a socketed Warcry instead of its own effects."""
    skill = next((s for s in skills if s.name == FEROCIOUS_ROAR and not s.wrong_weapon), None)  # (a Talisman skill)
    if skill is None:
        return None, False
    return skill, any(s.group == skill.group and s.name != FEROCIOUS_ROAR for s in skills if skill.group is not None)


def roar_period(skill: Skill) -> float:
    return max((skill.cooldown or ROAR_COOLDOWN) / (skill.cooldown_recovery or 1.0), skill.cast_time)


def roar_rage(skill: Skill) -> float:
    per_power = number(skill.level_stats.get("bear_warcry_gain_X_centirage_per_enemy_power")) / 100 or 2.0
    return per_power * BOSS_POWER


def rage_average(gain: float, maximum: float, period: float) -> float:
    """Average Rage over one roar to the next: full (capped) for RAGE_HOLD s, then RAGE_LOSS per second down to 0."""
    top = min(gain, maximum)
    if period <= 0 or top <= 0:
        return 0.0
    decay = min(max(period - RAGE_HOLD, 0.0), top / RAGE_LOSS)
    held = min(period, RAGE_HOLD)
    return (top * held + top * decay - RAGE_LOSS * decay * decay / 2) / period


def roar_empowered() -> int:
    return max(1, min(BOSS_POWER, ROAR_EMPOWER_POWER_CAP) // ROAR_EMPOWER_PER_POWER)


def roar_break(skill: Skill, enemy_armour: float) -> tuple[float, str]:
    """Share of the fight the boss's Armour is Fully Broken by the roar's Empowered hits, and its basis."""
    per_hit = number(skill.level_stats.get("warcry_grant_break_X_armour_on_hit"))
    hits, period = roar_empowered(), roar_period(skill)
    broken = per_hit * hits
    basis = (f"{FEROCIOUS_ROAR} Empowers {hits} Attacks per use (one per {ROAR_EMPOWER_PER_POWER} of the boss's "
             f"{BOSS_POWER} Power), each Breaking {per_hit:,.0f} Armour: {broken:,.0f} against the boss's {enemy_armour:,.0f}")
    if per_hit <= 0 or broken < enemy_armour:
        return 0.0, basis + ": not Fully Broken"
    return min(1.0, FULL_BREAK_DURATION / period), (
        basis + f": Fully Broken for {FULL_BREAK_DURATION:g} s, once per {period:.1f} s (the main skill's next hits after "
        "each roar assumed to land)")


# Corpses ---------------------------------------------------------------------------------------
# A skill the game types TargetsDestructibleCorpses, TargetsDestructibleRareCorpses or CanTargetUnusableCorpse (game files,
# build 26476662: Detonate Dead, Volatile Dead, Bitter Dead, Decompose, Profane Ritual, Feast of Flesh, Devour, Kelari's
# Malediction, Ritual Sacrifice, Unearth, Raise Zombie ...) needs a corpse each use. Nothing dies on a single boss, so
# only the build's own source supplies them. The 0.5 game data has no player skill that creates corpses (Desecrate is a
# monster skill only; the Bone Corpses and Corpse Walk lines grant skills the files no longer have). The one source left
# is Sacrifice (undead Reviving Minions used in place of corpses); its rate, the minions' revive time, isn't modelled
# yet, so it supplies 0 for now, said in the guide.
CORPSE_TYPES = {"TargetsDestructibleCorpses", "TargetsDestructibleRareCorpses", "CanTargetUnusableCorpse"}
CORPSE_SOURCES = {"Sacrifice": "your undead Reviving Minions in place of corpses (their revive rate isn't modelled yet)"}
# Skills of those types that still work without a corpse: never set to 0, the part that needs corpses said in the guide
CORPSE_OPTIONAL = {
    "Raise Zombie": "consumes a Power Charge when there's no corpse; zombies from corpses not counted",
    "Unearth": "its bone spikes hit without corpses; its Bone Constructs (from corpses) not counted",
    # (Ritualist's As the Whispers Demand grants it with head_hunt_without_a_corpse; RitualSacrificePlayer's
    # head_hunt_%_life_to_remove_on_self_stab is 20: game files, build 26476662)
    "Ritual Sacrifice": "used on yourself it takes 20% of maximum Life for one random Monster Modifier instead of a rare "
                        "corpse's; random, so its buff isn't counted",
}
# Searched for other ways to pay Life instead of a corpse (8 Nov 2026, the user's question): stat descriptions, gem and
# support texts, passives, ascendancies, mods and PoB's uniques. Besides Ritual Sacrifice above, none: Paquate's Pact
# (a Warcry's Corrupted Blood at the cost of Life), Dark Pact / Abyssal Pact (a minion's Life as damage), Blood Magic-style
# costs and Feast of Flesh's Life recovery don't stand in for a corpse; "Your Raised Zombies count as corpses" and
# "permanent undead minions usable as corpses" are stat texts no mod, passive or skill carries (dead). Corpse
# Conservation and the Corpses / Meat Recycling passives (a chance not to destroy a consumed corpse) multiply a supply
# but make none.


def corpse_supply(present: set[str]) -> float:
    """Corpses per second the build's own sources supply on a single boss (none is priced yet: CORPSE_SOURCES)."""
    return 0.0


def corpse_model(skills: list[Skill], mods: list[dict]) -> tuple[list[dict], list[dict]]:
    """Corpses per second for each skill that needs them, from the build's own sources (CORPSE_SOURCES) and none from
    kills on a single boss: a skill with no supply uses 0 per second, wherever it is (main skill, meta group, totem),
    replacing any trigger rate the other models gave it. Returns the new mods list and the notes."""
    users = sorted({s.name for s in skills if s.corpses and not s.wrong_weapon} - set(CORPSE_OPTIONAL))
    optional = sorted({s.name for s in skills if s.corpses} & set(CORPSE_OPTIONAL))
    notes = [{"label": f"{name}: corpses", "value": 0, "basis": CORPSE_OPTIONAL[name] + " (no corpses on a single boss)",
              "confidence": "datamined"} for name in optional]
    if not users:
        return mods, notes
    present = {s.name for s in skills}
    sources = [f"{name}: {why}" for name, why in CORPSE_SOURCES.items() if name in present]
    supply = corpse_supply(present)
    dropped = set(users)
    mods = [m for m in mods if not (m.get("kind") == "SkillData" and m.get("skill") in dropped
                                    and m.get("key") in ("triggerRate", "triggered"))]
    for name in users:
        mods += trigger_rate(name, supply)
    notes.append({
        "label": f"Corpses per second for {', '.join(users)}", "value": supply,
        "basis": "they need a corpse each use, and nothing dies on a single boss; "
                 + (f"source present but not priced: {'; '.join(sources)}" if sources
                    else "the build has no corpse source of its own")
                 + ", so they're counted 0 uses per second",
        "confidence": "datamined" if not sources else "theory"})
    return mods, notes


def roar_model(skills: list[Skill], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    """The Rage a Ferocious Roar used on cooldown keeps on average (PoB gives a build that can't gain Rage none), and the
    time the roar takes from the main skill. Its Armour Break is a condition (conditions.Conditions.roar_evidence)."""
    skill, warcry = roar(skills)
    if skill is None or warcry or main is None or main is skill:
        return [], []
    period = roar_period(skill)
    mods: list[dict] = []
    notes: list[dict] = []
    if main.attack and number(stats.get("Rage")) <= 0:
        gain = roar_rage(skill)
        rage = rage_average(gain, number(stats.get("MaximumRage")) or RAGE_MAXIMUM, period)
        mods.append(line("1% more Attack Damage", rage))
        notes.append({"label": f"{FEROCIOUS_ROAR}: average Rage", "value": round(rage, 1), "confidence": "theory",
                      "basis": f"{gain:g} Rage per roar ({BOSS_POWER} Power x {gain / BOSS_POWER:g}), capped at "
                               f"{number(stats.get('MaximumRage')) or RAGE_MAXIMUM:g}, used every {period:.1f} s; held "
                               f"{RAGE_HOLD:g} s, then 1 lost per 0.2 s (game files); 1% more Attack damage each. "
                               "Loss paused by the boss's hits isn't counted; Rage-per lines of the build aren't fed"})
    lost = min(1.0, (skill.cast_time or 1.1) / period)
    mods.append(line("1% more Attack and Cast Speed", -100 * lost, skill=main.name))
    notes.append({"label": f"{FEROCIOUS_ROAR}: time it takes from {main.name}", "value": round(lost, 3),
                  "confidence": "theory",
                  "basis": f"pressed on its cooldown: {skill.cast_time or 1.1:.2f} s every {period:.1f} s; changing to "
                           "and from Bear form isn't counted"})
    return mods, notes


BOSS_AILMENT_EVERY = 30.0  # seconds between Freezes / Electrocutes of a pinnacle boss (assumed, as conditions.py)
MARK_BUFFS = {"Voltaic Mark": "Electrocute", "Freezing Mark": "Freeze"}
MARKS = ("Sniper's Mark", *MARK_BUFFS, "Bloodhound's Mark")
# Meta skills that apply a socketed Mark on a timer of their own (not the Mark's cooldown).
MARK_TRIGGER_EVERY = {"Called Shots": 5.0}  # "applies a socketed Mark to an enemy every five seconds"


@dataclass
class MarkUse:
    period: float  # seconds between two applications
    how: str
    pressed: Skill | None = None  # the skill you press for it (the Mark, Pounce, Hand of Chayula), None if automatic
    on_demand: bool = True  # applied when needed (recast as it runs out or is consumed), not on a fixed timer


def mark_use(mark: Skill, skills: list[Skill]) -> MarkUse:
    """How often the Mark is applied, and by what."""
    own = (mark.cooldown or 0.0) / (mark.cooldown_recovery or 1.0)
    # (PoB doesn't flag a Mark in Called Shots, a notable's granted group, as triggered)
    timer = next((s for s in skills if s.group == mark.group and s.name in MARK_TRIGGER_EVERY), None)
    if timer is not None:
        every = MARK_TRIGGER_EVERY[timer.name]
        return MarkUse(max(own, every), f"{timer.name} applies it every {every:g} s", on_demand=False)
    if not mark.triggered:
        return MarkUse(max(own, mark.cast_time), "cast by you", mark)
    trigger = next((s for s in skills if s.group == mark.group and s.name != mark.name), None)
    if trigger is None:
        return MarkUse(own, "triggered by a skill the model doesn't know: assumed applied when needed, at no cost")
    if trigger.cooldown:
        every = trigger.cooldown / (trigger.cooldown_recovery or 1.0)
        return MarkUse(max(own, every), f"triggered by {trigger.name}, used on its {every:.1f} s cooldown", trigger,
                       on_demand=False)
    return MarkUse(max(own, trigger.cast_time), f"triggered by {trigger.name}, used when the Mark is needed (assumed)",
                   trigger)


def mark_model(skills: list[Skill], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    """Each Mark's effect scaled from PoB's always-up to what consumption on a boss leaves, and the time the skill you
    press for it (the Mark, Pounce, Hand of Chayula) takes from the main skill."""
    mods: list[dict] = []
    notes: list[dict] = []
    marks = [s for s in skills if s.name in MARKS and s is not main]
    for mark in marks[:1]:  # one Mark on an enemy at a time (number_of_marks_allowed_per_type 1, EnemyMarkLimit 1)
        use = mark_use(mark, skills)
        period, how = use.period, use.how
        if mark.name == "Sniper's Mark":
            crits = crits_per_second(stats)
            if use.on_demand:
                period = max(period, min(1 / crits, MARK_DURATION) if crits else 0.0)  # (recast once consumed)
            share = min(1.0, 1 / (period * crits)) if crits and period else 1.0
            label, basis = "Sniper's Mark: share of crits it adds damage to", (
                f"one Mark every {period:.1f} s ({how}) against {crits:.2f} crits/s; the next crit consumes it (PoB "
                "counts its extra crit damage on every crit)")
        elif mark.name in MARK_BUFFS:
            ailment = MARK_BUFFS[mark.name]
            inflicts = number(stats.get(f"{ailment}BuildupAvg")) > 0  # (PoB: a hit's average buildup, % of the threshold)
            marked = 1.0 if use.on_demand or not period else min(1.0, MARK_DURATION / period)
            if use.on_demand:
                period = max(period, MARK_DURATION)  # reapplied as it runs out (its duration's modifiers aren't read)
            share = min(1.0, MARK_BUFF_DURATION / BOSS_AILMENT_EVERY) * marked if inflicts else 0.0
            label, basis = f"{mark.name}: buff uptime", (
                (f"the boss {ailment.lower()}d once per {BOSS_AILMENT_EVERY:.0f} s while marked (assumed), "
                 f"{MARK_BUFF_DURATION:.0f} s buff; marked {marked:.0%} of the time ({how})") if inflicts else
                f"the main skill can't {ailment.lower()} the boss, so the Mark is never consumed and gives no buff"
            ) + "; PoB counts the buff always up; its buildup part is scaled with it (assumed)"
        else:
            share, label, basis = 1.0, f"{mark.name}", "PoB adds nothing to the main skill's damage from it; Blood "                 "Explosion's damage on a Heavy Stun isn't counted"
        if share < 1.0:
            mods.append(line("1% more Effect of your Curses", 100 * (share - 1), skill=mark.name))
        if use.pressed is not None and main is not None and use.pressed is not main and period:
            took = use.pressed.cast_time or 0.5
            lost = min(1.0, took / period)
            mods.append(line("1% more Attack and Cast Speed", -100 * lost, skill=main.name))
            basis += f"; using {use.pressed.name} ({took:.2f} s every {period:.1f} s) takes {lost:.0%} of {main.name}'s time"
        notes.append({"label": label, "value": round(share, 2), "basis": basis, "confidence": "theory"})
    if len(marks) > 1:
        notes.append({"label": "Marks beyond the first", "value": len(marks) - 1,
                      "basis": "an enemy carries one Mark at a time: " + ", ".join(m.name for m in marks[1:])
                               + " not modelled (PoB keeps only one too)", "confidence": "theory"})
    return mods, notes


def meta_handling(name: str) -> tuple[bool, str]:
    """(the Energy model triggers it, why not otherwise) for a Meta gem."""
    if name in META_GEMS:
        return True, ""
    for table, why in ((NO_ENERGY_ON_A_BOSS, "it gains Energy on {}: none on a single boss"),
                       (NOT_MODELED_YET, "not modeled yet: {}"), (TOTEM_METAS, "the totem round's, not a trigger: {}"),
                       (MIRAGE_METAS, "the mirage round's, not an Energy trigger: {}"),
                       (OTHER_METAS, "not a trigger the search sets up yet: {}"),
                       (DEPRECATED, "{}")):
        if name in table:
            return False, why.format(table[name])
    return False, "a Meta gem the builder doesn't know yet"
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
    if kind == "dodge":
        energy = DODGE_ROLL_METRES * per
        return energy / DODGE_EVERY, (f"one dodge roll every {DODGE_EVERY:g} s assumed against a boss (how often you roll is up to "
                                      f"you), {DODGE_ROLL_METRES:g} m each (the game files' base distance; distance modifiers not "
                                      f"counted) for {energy:g} Energy")
    if kind == "energy shield lost":
        pool = number(stats.get("EnergyShield"))
        if pool <= 0:
            return 0.0, "the build has no Energy Shield: no Energy"
        every = number(stats.get("enemySkillTime")) or 0.7
        land = (1 - min(number(stats.get("EvadeChance")), 100) / 100) * (1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100)
        hit = sum(number(stats.get(f"{t}TakenHit")) for t in DAMAGE_TYPES)
        hits = land / every
        if hits <= 0:
            return 0.0, "none of the boss's hits land: no Energy"
        # Over a fight, a hit can only take the Energy Shield that came back since the last one: regeneration, and
        # recharge once the gap between landed hits outlasts the recharge delay (the first pool's worth isn't counted)
        gap = 1 / hits
        regen, recharge = number(stats.get("EnergyShieldRegenRecovery")), number(stats.get("EnergyShieldRecharge"))
        delay = number(stats.get("EnergyShieldRechargeDelay"))
        restored = regen * gap + recharge * max(0.0, gap - delay)
        taken = min(hit, pool, restored)
        return hits * taken * per, (f"the boss's hits land {hits:.2f} times per second (one every {every:g} s, {land:.0%} neither "
                                    f"evaded nor blocked), each taking {taken:,.0f} Energy Shield after mitigation (PoB's boss "
                                    f"preset; a hit of {hit:,.0f}, at most the {restored:,.0f} that returns between hits: "
                                    f"{regen:,.0f}/s regenerated, {recharge:,.0f}/s recharge after {delay:g} s): "
                                    f"1 Energy per 10 lost")
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


def energy_rate(meta: str, x: dict, gain: float, cost: float, crits_recently: bool, doubled: bool,
                level: int | None = None) -> float:
    """Triggers per second: Energy gained per second over the Energy one round of triggers costs."""
    info = META_GEMS[meta]
    increased = gem_energy(meta, level) + x["MetaEnergy"] + (x["MetaEnergyCrit"] if crits_recently else 0.0)
    multiplier = max(0.0, 1 + increased / 100) * max(0.0, 1 + x["MetaEnergyMore"] / 100) * (2.0 if doubled else 1.0)
    spent = cost * (1 - min(x["EnergyRefund"], 100.0) / 200)
    if info.get("invocation"):
        spent *= 1 - min(x["InvocationHalfCost"], 100.0) / 200
    return gain * multiplier / spent if spent > 0 else 0.0


def energy_model(inputs: Inputs, stats: dict, skills: list[Skill], main: Skill | None,
                 supports: dict[int, list[str]] | None = None) -> tuple[list, list, set[int]]:
    """Trigger rates for the spells socketed in meta skills that gain Energy, fed by the main skill. Every spell in a meta
    group triggers once per round, and a round costs all their Energy: a second spell halves (about) the first's rate.
    `supports` ({group: support names}) adds the meta group's own Energy supports (META_SUPPORTS).
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
        if cast_by_totem(skills, main) and META_GEMS[meta.name]["gain"] in ("crit", "ailment", "melee hit", "cast"):
            gain, gain_basis = 0.0, f"{main.name} is cast by a totem, and the totem's hits and casts aren't yours (assumed)"
        x = dict(x0)
        own = [n for n in (supports or {}).get(meta.group, []) if n in META_SUPPORTS]
        for name in own:
            key, value = META_SUPPORTS[name]
            x[key] += value
        rate = energy_rate(meta.name, x, gain, cost, crits_recently, doubled, meta.level)
        if rate > 0:  # (a group whose spells never trigger adds no damage, and mustn't change how the build is scored)
            groups.add(meta.group)

        def relative(x: dict, meta_name: str = meta.name, gain: float = gain, cost: float = cost,
                     base: float = rate, level: int | None = meta.level) -> float:
            return 100 * (energy_rate(meta_name, x, gain, cost, crits_recently, doubled, level) / base - 1) if base else 0.0

        keys = tuple(k for k in ENERGY_INPUTS if k != "MetaEnergyCrit" or crits_recently)
        for spell in spells:
            mods += trigger_rate(spell.name, rate)
            if rate > 0:
                mods += more_lines("1% more Damage", relative, x, passive, keys, skill=spell.name)
            if META_GEMS[meta.name].get("less"):
                mods.append(line("1% more Damage", -META_GEMS[meta.name]["less"], skill=spell.name))
        info, own_energy = META_GEMS[meta.name], gem_energy(meta.name, meta.level)
        increased = own_energy + x["MetaEnergy"] + (x["MetaEnergyCrit"] if crits_recently else 0.0)
        notes.append({
            "label": f"{meta.name} triggers per second ({names})", "value": round(rate, 3),
            "basis": f"{gain_basis}; {increased:.0f}% increased Energy (gem {own_energy:.0f}% at level {meta.level or f'{META_LEVEL_ASSUMED}, assumed'})"
                     + (f", {x['MetaEnergyMore']:.0f}% more" if x["MetaEnergyMore"] else "")
                     + (", doubled" if doubled else "")
                     + (f" (supported by {', '.join(own)})" if own else "")
                     + f"; one round costs {cost:.0f} Energy (10 per 0.1s of base cast time)"
                     + (f", {x['EnergyRefund']:.0f}% chance to refund half" if x["EnergyRefund"] else "")
                     + ("; an Invocation: you press it to release its Energy (instant in the game files), assumed often "
                        f"enough that its {INVOCATION_MAX_ENERGY} Energy store never fills" if info.get("invocation") else "")
                     + (f"; its spells deal {info['less']:.0f}% less damage" if info.get("less") else ""),
            "confidence": "theory"})
        if META_GEMS[meta.name]["gain"] == "dodge" and rate > 0:
            busy = min(DODGE_ROLL_SECONDS / DODGE_EVERY, 0.95)
            mods.append(line("1% more Attack and Cast Speed", -100 * busy, skill=main.name))
            notes.append({
                "label": f"Time spent dodge rolling ({main.name})", "value": f"{busy:.1%} less Attack and Cast Speed",
                "basis": f"a build that counts on {meta.name} rolls every {DODGE_EVERY:g} s (assumed; how often you roll is up "
                         f"to you), and each roll takes {DODGE_ROLL_SECONDS:g} s (the dodge roll's own cast time in the game "
                         f"files) away from {main.name}; rolls sped up by movement speed aren't counted",
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
