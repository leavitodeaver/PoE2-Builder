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
    "EnergyShieldRegenRecovery", "EnergyShieldRecharge", "PresenceRadius",
    "EnemyAilmentThreshold", "ShockChanceOnHit", "ShockChanceOnCrit", "IgniteChanceOnHit", "IgniteChanceOnCrit",
    "FreezeBuildupAvg", "ElectrocuteBuildupAvg", "HeavyStunBuildupAvg",
    "Darkness", "Str", "Dex", "Int", *(f"{t}CritAverage" for t in DAMAGE_TYPES), *(f"{t}HitAverage" for t in DAMAGE_TYPES),
    *(f"MainHand.{t}CritAverage" for t in DAMAGE_TYPES),
    # Barrier Invocation: the Energy Shield each boss hit takes
    *(f"{t}TakenHit" for t in DAMAGE_TYPES),
    # Iron Ward: the boss's physical hits and how much of them the build prevents
    "PhysicalEnemyDamage", "PhysicalDamageReduction", "PhysicalDamageReductionMax", "BasePhysicalDamageReductionWhenHit",
    "Armour", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
    # Refutation: Runic Ward to spend (parry_plan)
    "Ward",
    # Run and Gun: the main skill's projectiles (run_and_gun_model)
    "ProjectileCount",
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


def raw_mod(name: str, kind: str, value: float, skill: str | None = None) -> dict:
    """A PoB modifier by its internal name (no text to parse): `kind` "INC" / "MORE" / "BASE" / "FLAG", tagged to
    `skill` if given (SkillName). For what PoB has no parseable line for, such as one skill's DebuffEffect MORE."""
    mod: dict = {"kind": "Raw", "name": name, "type": kind, "value": _round(value)}
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
    level_stats: dict[str, float] = field(default_factory=dict)  # only for skills asked for (Iron Ward)
    wrong_weapon: bool = False  # needs a weapon type in neither hand (PoB disables only Attacks for that)
    corpses: bool = False  # consumes or targets corpses (the game's skill types; corpse_model)
    level: int | None = None  # gem level of a meta skill that gains Energy (gem_energy)
    minion: dict | None = None  # a triggered skill summoning Temporary minions: their duration, casts, limit (minion_count)
    summon: dict | None = None  # any minion skill's minions: limit, duration (0: until killed), persistent (minion_deaths)
    support_stats: dict[str, float] = field(default_factory=dict)  # named stats of its supports, summed (gem_stat_model)

    @classmethod
    def of(cls, raw: dict) -> Skill:
        return cls(raw["name"], raw.get("group"), bool(raw.get("spell")), bool(raw.get("attack")),
                   bool(raw.get("melee")), bool(raw.get("triggered")), bool(raw.get("energy")),
                   number(raw.get("castTime")), raw.get("cooldown"), number(raw.get("cooldownRecovery")) or 1.0,
                   dict(raw.get("levelStats") or {}), bool(raw.get("wrongWeapon")),
                   bool(raw.get("corpses")), raw.get("level"), raw.get("minion"), raw.get("summon"),
                   dict(raw.get("supportStats") or {}))


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
    # True: the counter behind it may also run during the buff and its recovery (unsettled); the headline takes `wait`
    # (it doesn't run) and the notes add the other reading, uncounted (MECHC2, the manager's ruling)
    counter_may_run: bool = False


def archon_sources(stats: dict, main_gems: set[str]) -> list[ArchonSource]:
    """Ways to gain Elemental Archon on a boss, fastest first."""
    sources = [ArchonSource("Seething Body", "ArchonInfernal", 0.0,
                            "regained on the next spell cast at High Infernal Flame; its longer hold at High "
                            "Infernal Flame isn't counted")]
    mana, cost = number(stats.get("Mana")), number(stats.get("ManaPerSecondCost"))
    if mana > 0 and cost > 0:
        spend = mana / cost
        # The lower uptime bound (MECHC2, the manager's ruling, as for the Arcane Surge support): only Mana spent after the
        # recovery ends counts, so the next gain comes a full spend time later. Whether Mana spent during the Archon and its
        # recovery counts is unsettled (RESEARCH1 mana-spend-counters.md, LOW); archon_model adds that reading as an
        # uncounted note. (Before: half the spend time, the midpoint.)
        sources.append(ArchonSource("Archon of the Storm", "ArchonMana", spend,
                                    f"spending {mana:,.0f} Mana takes {spend:.1f}s at {cost:,.0f} Mana per second, counted "
                                    "from the end of the recovery (LOW: Mana spent during the Archon and its recovery taken "
                                    "as not counting toward the next, the lower bound; unsettled, "
                                    "research/mana-spend-counters.md)", counter_may_run=True))
    if number(stats.get("EnergyShield")) > 0:
        # (MECH46, FLAT_RATES A8: before, a flat 2 x delay, though every boss hit that lands restarts the delay.) The
        # boss's landing hits taken as a Poisson stream (enemySkillTime, less evaded and blocked hits): the expected wait
        # for a gap of `delay` with none is (e^(rate x delay) - 1) / rate. Damage over time and the boss's misses by
        # timing aren't counted (MEDIUM).
        delay = number(stats.get("EnergyShieldRechargeDelay")) or 4.0
        every = number(stats.get("enemySkillTime")) or 0.7
        land = (1 - min(number(stats.get("EvadeChance")), 100) / 100) * (
            1 - min(number(stats.get("EffectiveBlockChance")), 100) / 100) / every
        wait = math.expm1(min(land * delay, 700.0)) / land if land > 0 else delay
        sources.append(ArchonSource("Archon of the Blizzard", "ArchonRecharge", wait,
                                    f"Energy Shield Recharge begins after {delay:g}s without damage; the boss lands "
                                    f"{land:.2f} hits/s (one per {every:g}s, less evaded and blocked), each restarting "
                                    f"it: a {delay:g}s gap comes after {wait:.1f}s on average"))
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
        if current.counter_may_run:  # the other reading, shown and not counted (accuracy first: the headline takes the lower)
            alt_wait = max(0.0, current.wait - duration - recovery)
            alt = archon_effects(x0, alt_wait, no_recovery)
            if alt["uptime"] > e["uptime"] + 1e-9:
                notes.append({
                    "label": "Elemental Archon if Mana spent during it and its recovery counts (not counted)",
                    "value": 0,
                    "basis": f"{alt['more']:.2f}% more Elemental spell damage averaged (up {alt['uptime']:.0%} instead of "
                             f"{e['uptime']:.0%}): the {current.wait:.1f}s of spending would run through the "
                             f"{duration + recovery:.1f}s of Archon and recovery, regained {alt_wait:.1f}s after it ends. "
                             "Unsettled (research/mana-spend-counters.md); the headline takes the lower uptime",
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
# Energy per Power per Shock or Ignite, 10 per Freeze (game files, GrantedEffectStatSets MetaCastOnElementalAilmentPlayer and
# MetaElementalInvocationPlayer: ..._centienergy_per_monster_power_on_shock / _ignite 100, _on_freeze 1000), the boss's
# Freezes at models.buildup_rate (MECH41; before, a Freeze was never assumed on a boss).
# The others, from their 0.5 skill data (centienergy: 100 = 1 Energy): Cast on Block (Svalinn) 25 Energy per block
# (cast_on_block_gain_X_centienergy_on_block 2500), counted at the boss's hits that land and are blocked; Thundergod's
# Wrath (Mjolner) 3 per Power per melee hit (cast_lightning_spell_on_hit_..._per_monster_power_on_hit 300); Spellslinger
# (Dueling Wand) 0.2 per 10 ms of the cast spell's base cast time (spellslinger_invocation_gain_X_centienergy_per_10ms_base_cast_time
# 20); Cast on Charm Use (Bijouborne) 2 per charm charge used (cast_on_using_charm_gain_X_centienergy_per_charm_charge_used_on_using_charm
# 200), at the charges the equipped charms spend against a boss (flasks.charm_charges_used). Energy on kills (Reaper's Invocation) and on minion deaths
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

FREEZE_ENERGY = 10.0  # Energy per monster Power per Freeze (centienergy 1000, above)

META_GEMS = {
    "Cast on Critical": {"per_level": 3.0, "gain": "crit", "less": 20.0},
    "Cast on Elemental Ailment": {"per_level": 3.0, "gain": "ailment"},
    "Elemental Invocation": {"per_level": 3.6, "gain": "ailment", "invocation": True},
    "Cast on Block": {"per_level": 3.0, "gain": "block", "per": 25.0},
    "Thundergod's Wrath": {"per_level": 3.0, "gain": "melee hit", "per": 3.0},
    "Spellslinger": {"per_level": 3.0, "gain": "cast", "per": 0.2, "invocation": True},
    "Cast on Charm Use": {"per_level": 3.0, "gain": "charm", "per": 2.0},
    "Barrier Invocation": {"per_level": 3.0, "gain": "energy shield lost", "per": 0.1, "invocation": True},
    "Cast on Dodge": {"per_level": 3.0, "gain": "dodge", "per": 2.0},
    # Cast on Minion Death (MECH44, 11 Nov 2026; before, in NO_ENERGY_ON_A_BOSS: "none on a single boss"): "Gains 50 base
    # Energy when a Minion is killed, modified by the Minion's Power" (MetaCastOnMinionDeathPlayer,
    # cast_on_minion_death_gain_1_energy_per_X_minion_relative_defensiveness_% 2; energy_generated_+% 3 a level). The game
    # files: it gains "when one of your Persistent Minions is Killed" (its description), and KeywordPopups MinionDeath says
    # expiring or despawned minions aren't Killed. The deaths per second come from the build's own sources (minion_deaths,
    # MECH45; MECH44's flat one every 10 s is gone: the user, 11 Nov), at 100% relative Power (assumed: 50 Energy, LOW).
    "Cast on Minion Death": {"per_level": 3.0, "gain": "minion death", "per": 50.0},
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
NO_ENERGY_ON_A_BOSS = {"Reaper's Invocation": "kills"}
# Skills whose own use can't feed a meta skill's Energy: base_skill_cannot_generate_energy, "This Skill cannot cause Meta
# Skills to generate Energy" (active_skill_gem_stat_descriptions). (checked, MECH47: the game tables' GrantedEffectStatSets
# carry the stat on MidnightStarPlayer alone and GrantedEffectStatSetsPerLevel on none; PoB's Skills/other.lua has it in
# Midnight Zenith's two stat sets alone; poe2db not checked.) Only the gains the main skill's own use makes are zeroed;
# Energy from blocks, dodges, Energy Shield lost or minion deaths still counts.
CANNOT_GENERATE_ENERGY = {"Midnight Zenith"}
OWN_USE_GAINS = ("crit", "ailment", "melee hit", "cast")
# Your minions' deaths against a boss (MECH45, FIX_QUEUE; the user, 11 Nov 2026: "that's a dangerous assumption ... needs
# to be smarter than just making a flat assumption"; CLAUDE.md "No flat rates"). Game files, KeywordPopups MinionDeath:
# "Minions whose durations expire or that are despawned by the player are considered to have died. However, effects that
# state they trigger when a Minion is Killed do not trigger in these cases. Minions are only Killed if they reach 0 life,
# though this can be caused by their summoner as well as enemies." Cast on Minion Death gains Energy "when one of your
# Persistent Minions is Killed" (its ActiveSkills description), so neither expiring temporary minions nor despawns feed
# it; Grim Feast stores Life when "your Reviving Minions" die (its description). Reviving (KeywordPopups): they "revive
# after a short delay when killed. This delay is reset whenever another Reviving Minion dies": 7.5 s (global_resummon_time_ms,
# PoB's Misc.lua from the game's constants; resummon speed modifiers aren't read, LOW). With N such minions and kills at r
# per second, the deaths run in waves: N killed in N / r s, then all revive 7.5 s after the last, so the sustained rate is
# N r / (N + 7.5 r).
# The build's own sources counted (each at its real use rate):
#  - Sacrifice (its description: "your undead Reviving Minions can be used in place of Corpses by your skills"): a main skill
#    that consumes corpses (the corpse_model's skill types) uses one minion per use (one corpse per use assumed; that a
#    minion used as a corpse is Killed, reaching 0 life, is UNVERIFIED: LOW);
#  - Explosive Demise, Skeletal Arsonists' Command ("Detonate an allied Minion whose current Life is below a threshold"):
#    one minion per use, on its 3 s cooldown (PoB's minion.lua and the game files) x cooldown recovery, when the build has
#    another minion to detonate (that one is below the threshold when the Command comes up is assumed: LOW).
# Not counted yet (labelled, undercounts): the boss killing your minions (their Life and defences against the boss preset's
# hits), Dark Pact / Abyssal Pact (not in PoB's data), Offerings (the skeleton becomes the spike: whether it's Killed is
# UNVERIFIED, not counted), minions taking damage for you, other on-minion-death lines.
RESUMMON_SECONDS = 7.5  # global_resummon_time_ms (PoB Misc.lua)
EXPLOSIVE_DEMISE_COOLDOWN = 3.0  # s (PoB minion.lua:815 levels; game files)
SACRIFICE = "Sacrifice"
ARSONIST = "Skeletal Arsonist Minion"


def minion_deaths(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[float, str]:
    """Your Persistent, Reviving minions Killed per second against a boss, from the build's own sources (see above), capped
    by their revival (N r / (N + RESUMMON_SECONDS r)), and how. 0 with a note when nothing in the build kills them."""
    names = {s.name for s in skills}
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    count = sum(number(s.summon.get("limit")) for s in reviving)
    if count <= 0:
        return 0.0, "the build has no Persistent minions to be Killed (temporary minions expiring aren't Killed)"
    kills, parts = 0.0, []
    if SACRIFICE in names and main is not None and main.corpses:
        undead = sum(number(s.summon.get("limit")) for s in reviving if s.summon.get("undead"))
        uses = number(stats.get("Speed"))
        if undead > 0 and uses > 0:
            kills += uses
            parts.append(f"Sacrifice: {main.name} uses one of your {undead:g} undead minions as its corpse each use "
                         f"({uses:.2f} uses/s; one corpse per use assumed; that it is Killed is UNVERIFIED, LOW)")
    arsonist = next((s for s in skills if s.name == ARSONIST), None)
    if arsonist is not None and count > number(arsonist.summon.get("limit")):
        every = EXPLOSIVE_DEMISE_COOLDOWN / (arsonist.cooldown_recovery or 1.0)
        kills += 1 / every
        parts.append(f"Explosive Demise (your Skeletal Arsonists' Command) detonates one of your other minions every "
                     f"{every:.2f} s (its {EXPLOSIVE_DEMISE_COOLDOWN:g} s cooldown; that one is below its Life threshold "
                     f"when the Command comes up is assumed, LOW)")
    not_counted = ("not counted yet: the boss killing your minions, minions taking damage for you, Dark Pact, Offerings "
                   "(an undercount)")
    if kills <= 0:
        return 0.0, f"nothing in this build kills its minions ({not_counted})"
    rate = count * kills / (count + RESUMMON_SECONDS * kills)
    return rate, (f"{rate:.3f} of your {count:g} Reviving minions Killed per second: " + "; ".join(parts)
                  + f"; each revives {RESUMMON_SECONDS:g} s after the last death (KeywordPopups Reviving: the delay "
                    f"resets on each death), so {count:g} x {kills:.2f} / ({count:g} + {RESUMMON_SECONDS:g} x {kills:.2f}); "
                  + not_counted)
MINION_TYPES = {"Minion", "CreatesMinion"}


def minion_skill_names(groups: list[dict], gems: dict[str, dict]) -> list[str]:
    """The build's enabled skill gems that make minions (skill types Minion / CreatesMinion), for Cast on Minion Death."""
    return sorted({g["name"] for group in groups for g in group.get("gems") or []
                   if not g.get("support") and g.get("enabled", True) and g["name"] != "Cast on Minion Death"
                   and MINION_TYPES & set((gems.get(g["name"]) or {}).get("skillTypes") or [])})
# Gone from the game by two sources that agree (the rule for removing anything): the game files reach them by no source
# (data/game-*/live.json: no uncut tier, base item, passive, or collection unique grants them) and poe2db shows them
# with no tier or source; the user also found no Curse on Block in game (7 Nov). Never picked; a PoB code that has one
# gets no triggers from it, said in the guide.
DEPRECATED = {name: "removed from the game (the 0.5 game files and poe2db agree; deprecated)"
              for name in ("Curse on Block", "Cast on Melee Stun", "Cast on Melee Kill")}
NOT_MODELED_YET = {
    "Feral Invocation": "a clone uses its socketed Attacks (Energy cost per 10 ms of base attack time; 1 Energy per 20 Mana "
                        "spent): the Energy model prices triggered Spells only so far",
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
    "Spirit Vessel": "Forgotten Warden's companion that uses copies of socketed Bear, Werewolf and Wyvern skills, 20% more "
                     "damage per different socketed skill; the socketed skills become unusable for you (game files). The "
                     "bridge adds its monster to PoB (210% damage and life, 1 s attacks, 25% increased attack speed, 5% "
                     "crit), so PoB prices the companion (spirit_vessel_notes); the gear round tries Forgotten Warden with the best "
                     "shapeshift skills, then supports, in its group (gems.spirit_vessel_round)",
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
    from builder.search.conditions import _charge_rate, _extra_charges

    if not cast_by_totem(skills, main) or not main.spell:
        return [], []
    # The totem's hits aren't yours (assumed, the cautious reading): charges made on your hits, crits or kills don't
    # count, only those made on a timer, on blocking or on being hit.
    yours = dict(stats, Speed=0.0)
    rates = {}
    for kind in ("Power", "Endurance"):
        found = [r for r in (_charge_rate(t, kind, yours) for t in texts) if r is not None]
        rates[kind] = (sum(r[0] for r in found) * (1 + _extra_charges(texts, kind)[0])  # (extra charges per gain)
                       if number(stats.get(f"{kind}ChargesMax")) > 0 else 0.0)
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
    from builder.search.conditions import _charge_rate, _extra_charges

    if not in_hollow_form(skills, main):
        return [], []
    uses = number(stats.get("Speed"))
    found = [r for r in (_charge_rate(t, "Power", stats) for t in texts) if r is not None]
    made = (sum(r[0] for r in found) * (1 + _extra_charges(texts, "Power")[0])  # (extra charges per gain)
            if number(stats.get("PowerChargesMax")) > 0 else 0.0)
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
# Predator's Mark (MECH34): Pounce triggers it on the highest-Rarity enemy hit when no Mark gem is socketed in Pounce
# (ActiveSkills wolf_pounce). Game files (build 26476662, GrantedEffectStatSets WolfPounceMarkPlayer): 8 s, "Marked
# target takes 3% increased damage for each of its enemies within 6 metres of it, up to 45%"
# (skill_wolf_mark_damage_taken_+%_per_nearby_enemy 3, _cap 45). It's consumed when the target dies (triggering Summon
# Wolf: none on a lone boss; PoB gives Summon Wolf a limit of 0) or when Cross Slash hits it with both gouges (ActiveSkills
# wolf_cross_slash). Pounce has a weapon requirement (WeaponRequirements; PoB: Talisman). PoB (act_str.lua
# WolfPounceMarkPlayer): DamageTaken INC 3% per MinionPresenceCount (your minions at their limit), capped at 45, always
# on; it never counts you, and it applies the Mark even when PoB disables Pounce for the wrong weapon. With a Mark gem in
# Pounce, PoB applies that Mark instead (probe POE2T435), as the game does.
PREDATORS_MARK = "Predator's Mark"
PREDATOR_PER_ENEMY = 3.0
PREDATOR_CAP = 45.0
PREDATOR_RADIUS = 6.0
# Summon Wolf (MECH35; ActiveSkills wolf_warcry_wolf, GrantedEffects/StatSets WolfPounceSummonWolfPlayer, a secondary
# skill of the Pounce gem): triggered when Predator's Mark activates on the marked enemy's death, summons one Wolf
# (+0 more if the enemy was Rare, +1 if Unique: skill_cross_slash_triggered_summon_+_wolves_if_rare 0, _if_unique 1),
# no_minion_duration ("follows you as long as it lives"), at most number_of_wolves_allowed (GrantedEffectStatSetsPerLevel:
# 4 at levels 1-6, 5 at 7-13, 6 at 14-21, 7 at 22-30, 8 at 31-40). On a lone boss none are summoned, so the user's choice
# (11 Nov, "Count them, labelled"): the wolves are carried in from earlier kills, assumed at their limit (LOW). PoB counts
# 0 of them: number_of_wolves_allowed maps to ActiveWolfLimit (SkillStatMap) while the Wolf Companion minion reads
# WolfLimit (Minions.lua WolfMinion), so its MinionPresenceCount leaves them out.
SUMMON_WOLF = "Summon Wolf"
WOLVES_ALLOWED_AT_LEVEL_1 = 4
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
# Duration modifiers of effects you inflict (MECH44, 11 Nov 2026; before, the ledger's approved no-effect entry dropped
# Pin / Hinder / Armour Break durations; the user, 11 Nov: count them). Game files (stat_descriptions.csd, build
# 26476662): hinder_duration_+% "{0}% increased Hinder Duration" (passives slowed_enemies13/14_/25/26/27),
# blind_duration_+% "{0}% increased Blind duration" (Mods, PassiveSkills), armour_break_and_sundered_armour_duration_+%
# "{0}% increased Armour Break Duration" (passives area_attacks31, armour_break2/5/36; jewel mods), pin_duration_+%
# "{0}% increased Pin duration" (passives pin3/5/6/8, a soul core; Pin III's support_pin_pin_duration_+%_final).
# The Armour Break one is read as lengthening the 12 s Fully Broken (KeywordPopups ArmourBreak; assumed: the game
# files give a partial Break no duration). Pin: nothing here rates the boss Pinned (conditionEnemyPinned is never set),
# so its duration lines are labelled in the ledger and move nothing until a Pinned uptime exists.
DURATION_LINE = r"^(\d+(?:\.\d+)?)% (increased|reduced) {name} duration$"


def duration_scale(texts: list[str], name: str) -> float:
    """1 + the summed "X% increased / reduced <name> Duration" lines (at least 0)."""
    pattern = re.compile(DURATION_LINE.format(name=re.escape(name)), re.I)
    inc = 0.0
    for text in texts:
        if m := pattern.search(text.strip()):
            inc += float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
    return max(0.0, 1 + inc / 100)


def full_break_duration(texts: list[str]) -> float:
    """Seconds the boss's Armour stays Fully Broken: 12 s x the Armour Break Duration lines."""
    return FULL_BREAK_DURATION * duration_scale(texts, "Armour Break")


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


def roar_break(skill: Skill, enemy_armour: float, duration: float = FULL_BREAK_DURATION) -> tuple[float, str]:
    """Share of the fight the boss's Armour is Fully Broken by the roar's Empowered hits, and its basis."""
    per_hit = number(skill.level_stats.get("warcry_grant_break_X_armour_on_hit"))
    hits, period = roar_empowered(), roar_period(skill)
    broken = per_hit * hits
    basis = (f"{FEROCIOUS_ROAR} Empowers {hits} Attacks per use (one per {ROAR_EMPOWER_PER_POWER} of the boss's "
             f"{BOSS_POWER} Power), each Breaking {per_hit:,.0f} Armour: {broken:,.0f} against the boss's {enemy_armour:,.0f}")
    if per_hit <= 0 or broken < enemy_armour:
        return 0.0, basis + ": not Fully Broken"
    return min(1.0, duration / period), (
        basis + f": Fully Broken for {duration:g} s, once per {period:.1f} s (the main skill's next hits after "
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


SPIRIT_VESSEL = "Spirit Vessel"
SPIRIT_VESSEL_TYPES = {"Bear", "Wolf", "Wyvern"}  # the skill types its companion copies (engine/bridge.lua)
# Granted skills whose group the search fills (gems.granted_meta_round): an Energy meta's spell, Spirit Vessel's skills
GRANTED_FILLED = {*META_GEMS, SPIRIT_VESSEL}
SPIRIT_VESSEL_ASSUMED = (
    "the companion is PoB's minion for the game files' monster (210% damage and life, 1 s attacks, 25% increased attack "
    "speed, 5% crit chance), 20% more damage per different socketed skill. Its level: the game files' "
    "MinionGemLevelScaling, 2 per gem level (32 at gem level 16), the table every minion gem uses; that Spirit Vessel's "
    "companion takes its level from the gem level is the user's word and poe2db's (8 Nov 2026), the gem level being "
    "Forgotten Warden's rolled 'Grants Skill: Level (1-20)' (granted_level_notes says which roll is assumed). ASSUMED, not in the game files or poe2db: "
    "the copies are minion skills scaled by minion modifiers, not yours; the group's supports apply to them as to any minion's skills; PoB's player "
    "Bear, Werewolf and Wyvern skills behave the same used by a minion; its damage spread is 20% and its resistances 0")


GRANTED_RANGE = re.compile(r"^(?:\{range:([\d.]+)\})?(?:\{[^}]*\})*Grants Skill: Level \((\d+)-(\d+)\) (.+?)\s*$", re.M)


def granted_level(m: re.Match) -> tuple[int, int, int, str]:
    """(level, low, high, skill) for a GRANTED_RANGE match: the level PoB loads 'Grants Skill: Level (low-high)' at, the
    line's {range:} or else the middle roll (defaultItemAffixQuality 0.5), rounded half up (Mjolner's (1-20)
    Thundergod's Wrath at 11)."""
    low, high = int(m.group(2)), int(m.group(3))
    return int(low + (high - low) * float(m.group(1) or 0.5) + 0.5), low, high, m.group(4)


def granted_level_notes(items: list[tuple]) -> list[dict]:
    """For each equipped unique ((name, its text[, basis])) granting a skill at a rolled level (Forgotten Warden's
    Spirit Vessel, Mjolner's Thundergod's Wrath): which roll the build assumes. The user, 8 Nov 2026: the roll is the
    item's, so say it in the guide and the shopping list (builder/report/shopping.py). basis (ItemOption.granted_basis):
    "priced" (a level the trade site lists, bought), "endgame" (assumed until priced) or "middle" (PoB's, as in SSF)."""
    notes = []
    for name, raw, *rest in items:
        for m in GRANTED_RANGE.finditer(raw):
            level, low, high, skill = granted_level(m)
            if low == high:
                continue
            basis = rest[0] if rest else "middle"
            why = {"priced": ("the level the search bought, priced from the trade site's listings of it", "trade"),
                   "endgame": ("an endgame copy, item level ~80; lower-level copies grant less. ASSUMED until the Price "
                               "unique versions job prices its levels: the price shown meanwhile is poe.ninja's for the "
                               "cheapest copy of any level", "medium"),
                   "middle": ("PoB's middle roll (SSF: the level of a copy you find isn't known)", "theory")}[basis]
            notes.append({"label": f"{name}'s {skill}: level {level} assumed", "value": level,
                          "basis": f"the item rolls 'Grants Skill: Level ({low}-{high}) {skill}'; the build assumes "
                                   f"{level}: {why[0]} (builder/search/granted_rolls.py). The item's level sets it, "
                                   "loosely (copies of item level 78 grant 17 or 19); a lower level does less",
                          "confidence": why[1]})
    return notes


def spirit_vessel_groups(skills: list[dict]) -> set[int]:
    """Spirit Vessel groups holding a skill its companion uses: extra damage groups, counted through Full DPS."""
    return {v["group"] for v in skills if v["name"] == SPIRIT_VESSEL and not v["disabled"]
            and any(s.get("group") == v.get("group") and s["name"] != SPIRIT_VESSEL for s in skills)}


def spirit_vessel_notes(skills: list[dict]) -> list[dict]:
    """The guide's note for each Spirit Vessel group holding something it uses (engine/bridge.lua installSpiritVessel
    adds the companion to PoB; the socketed skills show as disabled for you)."""
    notes = []
    for vessel in (s for s in skills if s["name"] == SPIRIT_VESSEL and not s["disabled"]):
        copied = sorted({s["name"] for s in skills if s.get("group") == vessel.get("group") and s["name"] != SPIRIT_VESSEL})
        if copied:
            notes.append({"label": f"Spirit Vessel's companion uses {', '.join(copied)}", "value": len(copied),
                          "basis": SPIRIT_VESSEL_ASSUMED + "; you can't use them yourself (its hidden support)",
                          "confidence": "theory"})
    return notes


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


# Now and Again's Repeat and Echo on one spell (MECH46, FLAT_RATES group C8). The ledger entries now-and-again-repeat and
# now-and-again-echo each add their chance x 0.95 as "more Spell Damage", which PoB multiplies: (1 + .95r)(1 + .95e).
# The game files' KeywordPopups Cascadable: "A Cascadable Spell that Repeats will not Cascade or Echo while Repeating"
# (poe2db's Cascadable page the same). The scope is "while Repeating": the original use still rolls its Echo, only the
# repeated performance can't Echo (MECH47; two guides: the two rolls are independent and "both can proc on the same cast",
# switchbladegaming.com chronomancer build, kami-labs.fr ascendancy changes). So 1 + .95(r + e): only the product's r.e
# term (a Repeat's own Echo) is lost. MEDIUM: game text plus two guides, nothing official on which part may Echo. A
# spell both Repeatable and Cascadable gets the difference as a correction line. Each copy hits the boss as the use does (Repeat: KeywordPopups, aimed where the use was), so PoB's
# own hit share on the boss carries through the "more" lines.
REPEAT_LINE = re.compile(r"^Repeatable Spells have (\d+(?:\.\d+)?)% chance to Repeat$", re.I)
ECHO_LINE = re.compile(r"^Cascadable Spells have (\d+(?:\.\d+)?)% chance to Echo$", re.I)
REPEAT_ECHO_ENTRIES = ("now-and-again-repeat", "now-and-again-echo")


def repeat_echo_model(texts: list[str], main_types: set[str], forbidden: set[str]) -> tuple[list[dict], list[dict]]:
    if not {"Spell", "Multicastable", "Cascadable"} <= main_types or set(REPEAT_ECHO_ENTRIES) & forbidden:
        return [], []
    r = min(1.0, sum(float(m.group(1)) for t in texts if (m := REPEAT_LINE.search(t.strip()))) / 100)
    e = min(1.0, sum(float(m.group(1)) for t in texts if (m := ECHO_LINE.search(t.strip()))) / 100)
    if r <= 0 or e <= 0:
        return [], []
    product = (1 + 0.95 * r) * (1 + 0.95 * e)
    target = 1 + 0.95 * (r + e)
    return [line("1% more Spell Damage", 100 * (target / product - 1))], [{
        "label": "Now and Again: a Repeat doesn't Echo itself", "value": round(target / product, 4),
        "basis": f"{r:.0%} Repeat and {e:.0%} Echo: a Cascadable spell won't Echo \"while Repeating\" (KeywordPopups "
                 f"Cascadable), so the use still Echoes but its Repeat doesn't: x{target:.3f} in all, not x{product:.3f} "
                 "as two independent chances. In-game test: cast a spell many times with Now and Again and count "
                 "impacts; a cast where both land shows three (the cast, its Echo, the Repeat), not two or four",
        "confidence": "MEDIUM: game text plus two guides (the two rolls independent); nothing official says which "
                      "part may Echo. The ledger entries' 5% for the longer use as labelled there"}]


MARK_BUFFS = {"Voltaic Mark": "Electrocute", "Freezing Mark": "Freeze"}
MARKS = ("Sniper's Mark", *MARK_BUFFS, "Bloodhound's Mark")
# Meta skills that apply a socketed Mark on a timer of their own (not the Mark's cooldown).
MARK_TRIGGER_EVERY = {"Called Shots": 5.0}  # "applies a socketed Mark to an enemy every five seconds"
# Skills that apply a Mark socketed in them and have a weapon requirement PoB enforces by disabling them (MECH35: Pounce
# needs a Talisman, WeaponRequirements; PoB then still applies the socketed Mark).
MARK_CARRIERS = ("Pounce",)


@dataclass
class MarkUse:
    period: float  # seconds between two applications
    how: str
    pressed: Skill | None = None  # the skill you press for it (the Mark, Pounce, Hand of Chayula), None if automatic
    on_demand: bool = True  # applied when needed (recast as it runs out or is consumed), not on a fixed timer


def mark_use(mark: Skill, skills: list[Skill], disabled: list[Skill] | None = None,
             rates: dict[str, float] | None = None) -> MarkUse:
    """How often the Mark is applied, and by what. `disabled` are the skills PoB disables: a Mark gem in a Pounce PoB
    disables (no Talisman) is never applied, though PoB still applies its effect and reads the Mark as cast by you."""
    off = next((s for s in disabled or [] if s.group == mark.group and s.name != mark.name and s.name in MARK_CARRIERS),
               None)
    if off is not None:
        return MarkUse(math.inf, f"{off.name} can't be used (PoB disables it: wrong weapon), so the Mark is never applied",
                       on_demand=False)
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
        return MarkUse(own, "triggered by a skill the model doesn't know: assumed applied when needed, at no cost (LOW)")
    if trigger.energy and rates is not None and mark.name in rates:  # (MECH46, FLAT_RATES A5: was "when needed")
        rate = number(rates[mark.name])
        if rate <= 0:
            return MarkUse(math.inf, f"triggered by {trigger.name}, whose Energy never fills: never applied",
                           on_demand=False)
        return MarkUse(max(own, 1 / rate), f"triggered by {trigger.name} {rate:.3f} times/s (its Energy)",
                       on_demand=False)
    if trigger.cooldown:
        every = trigger.cooldown / (trigger.cooldown_recovery or 1.0)
        return MarkUse(max(own, every), f"triggered by {trigger.name}, used on its {every:.1f} s cooldown", trigger,
                       on_demand=False)
    return MarkUse(max(own, trigger.cast_time), f"triggered by {trigger.name}, used when the Mark is needed (assumed)",
                   trigger)


# Lines that apply only while the boss carries your Mark (PoB: ActorCondition Marked on the enemy, which CalcPerform sets
# whenever a Mark is in a curse slot, so always). Each is scaled to the share of the fight the boss is marked
# (mark_model): the damage-taken one adds into the enemy's DamageTaken INC, so scaling it is exactly its time-weighted
# DPS; the crit chance and ailment magnitude ones are scaled the same way (an approximation, labelled).
MARKED_LINES = (
    (re.compile(r"Enemies you Mark take (\d+)% increased Damage", re.I), "Enemies you Mark take 1% increased Damage"),
    (re.compile(r"(\d+)% increased Critical Hit Chance against Marked Enemies", re.I),
     "1% increased Critical Hit Chance against Marked Enemies"),
    (re.compile(r"(\d+)% increased Magnitude of Ailments you inflict against Marked Enemies", re.I),
     "1% increased Magnitude of Ailments you inflict against Marked Enemies"),
    (re.compile(r"Companions deal (\d+)% increased damage to your Marked targets", re.I),
     "Companions deal 1% increased damage to your Marked targets"),
)


def mark_model(skills: list[Skill], stats: dict, main: Skill | None,
               lines: list[dict] | None = None, metres: float | None = None,
               disabled: list[Skill] | None = None, rates: dict[str, float] | None = None) -> tuple[list[dict], list[dict]]:
    """Each Mark's effect scaled from PoB's always-up to what consumption on a boss leaves, the build's "Enemies you Mark
    take ..." and "against Marked Enemies" lines scaled to the share of the fight the boss carries the Mark (MARKED_LINES),
    and the time the skill you press for it (the Mark, Pounce, Hand of Chayula) takes from the main skill. `metres` is the
    boss's distance (Predator's Mark counts you within 6 m); `disabled` the skills PoB disables (mark_use)."""
    mods: list[dict] = []
    notes: list[dict] = []
    marks = [s for s in skills if s.name in MARKS and s is not main]
    marked_groups = {s.group for s in marks}  # (a Mark gem in Pounce replaces Predator's Mark)
    marks += [s for s in skills if s.name == PREDATORS_MARK and s.group not in marked_groups]
    for mark in marks[:1]:  # one Mark on an enemy at a time (number_of_marks_allowed_per_type 1, EnemyMarkLimit 1)
        play = ""
        scales: dict[str, float] = {}  # Full DPS entries counted x this (Pounce's slam, its wolves; MECH36)
        if mark.name == PREDATORS_MARK:
            pounce = next((s for s in skills if s.group == mark.group and s.name == "Pounce"), None)
            use = MarkUse(math.inf, "Pounce can't be used (PoB disables it: wrong weapon)")
            every = 0.0
            if pounce is not None:
                every = (pounce.cooldown or 0.0) / (pounce.cooldown_recovery or 1.0)
                use = MarkUse(max(every, MARK_DURATION), "triggered by Pounce, used as the Mark runs out (assumed)", pounce)
        else:
            use = mark_use(mark, skills, disabled, rates)
        period, how = use.period, use.how
        # the time a Mark stays on the boss after each application: until consumed, at most its duration
        held, consumed_by = MARK_DURATION, "Bloodhound's Mark's consumption on a Heavy Stun isn't rated (held its full duration)"
        if mark.name == PREDATORS_MARK:
            held, consumed_by = MARK_DURATION, "not consumed on a boss (only its death or Cross Slash consume it)"
            if main is not None and main.name == "Cross Slash" and hits_per_second(stats):
                held = min(1 / hits_per_second(stats), MARK_DURATION)
                # (Pounce recast once consumed, cooldown allowing: MECH36, it went under the cooldown before)
                period = max(period - MARK_DURATION + held, held, every)
                consumed_by = "each Cross Slash that lands both gouges consumes it (assumed: every hit)"
            marked = min(1.0, held / period) if period and period != math.inf else 0.0
            minions = sum(number((s.minion or {}).get("limit")) for s in skills)
            wolf = next((s for s in skills if s.name == SUMMON_WOLF and s.group == mark.group), None)
            wolves = 0.0
            if wolf is not None and pounce is not None:
                wolves = number(wolf.level_stats.get("number_of_wolves_allowed")) or WOLVES_ALLOWED_AT_LEVEL_1
                wolves = max(0.0, wolves - number((wolf.minion or {}).get("limit")))  # (PoB's own count, 0, is in minions)
            near = 1 if metres is not None and metres <= PREDATOR_RADIUS else 0
            pob = min(PREDATOR_CAP, PREDATOR_PER_ENEMY * minions)
            game = min(PREDATOR_CAP, PREDATOR_PER_ENEMY * (minions + wolves + near)) * marked
            # PoB's own value (minions only, always on) taken out; the game's added as an enemy damage-taken line
            mods.append(line("1% more Effect of your Curses", -100, skill=mark.name))
            if game:
                mods.append(line("Enemies you Mark take 1% increased Damage", game))
            share, label = 1.0, "Predator's Mark: increased damage taken (%)"
            basis = (f"{game:.0f}% increased damage taken (PoB {pob:.0f}%): 3% per enemy of the boss within 6 m, up to 45% "
                     f"(game files): you ({'counted' if near else 'not counted'}: the boss assumed "
                     f"{'at ' + format(metres, 'g') + ' m' if metres is not None else 'at an unknown distance'}) and "
                     f"{minions:g} minions (PoB's count of your minions at their limit, assumed all within 6 m of the "
                     f"boss; PoB never counts you); marked {marked:.0%} of the fight ({how}; held {held:.1f} s, "
                     f"{consumed_by})")
            if wolves:
                carried = wolves + number((wolf.minion or {}).get("limit"))
                # Their own hits (the user, 11 Nov: "Yes, count it labelled"): PoB's Full DPS counts the Wolf Companion
                # once (its group's count, 1; its WolfLimit reads 0), so its entry counts x the carried number
                # (objective.Objective.entry_scales). Not a more-Damage line: one wolf hitting x6 harder isn't six
                # wolves (PoE2's ailment chances grow with the hit: POE2T450 read x12.7 for +500%)
                scales[SUMMON_WOLF] = carried
                basis += (f"; plus {wolves:g} Pounce wolves* (MEDIUM: Summon Wolf's limit at its level, game files; none "
                          "are summoned on a lone boss, so they're assumed carried in from earlier marked kills, alive "
                          "and within 6 m of the boss: they follow you through area transitions, on the user's word, "
                          "11 Nov; PoB counts 0 of them). Their own hits count at that number* (LOW: each wolf's damage is "
                          "PoB's Wolf Companion, a monster table our game-file extract doesn't hold; PoB's Full DPS "
                          f"counts one, counted x{carried:g})")
                play = "Keep your Pounce wolves alive into the boss: kill a few Marked enemies on the way in."
        elif mark.name == "Sniper's Mark":
            crits = crits_per_second(stats)
            if use.on_demand:
                period = max(period, min(1 / crits, MARK_DURATION) if crits else 0.0)  # (recast once consumed)
            share = min(1.0, 1 / (period * crits)) if crits and period else 1.0
            held, consumed_by = min(1 / crits, MARK_DURATION) if crits else MARK_DURATION, "the next crit consumes it"
            label, basis = "Sniper's Mark: share of crits it adds damage to", (
                f"one Mark every {period:.1f} s ({how}) against {crits:.2f} crits/s; the next crit consumes it (PoB "
                "counts its extra crit damage on every crit)")
        elif mark.name in MARK_BUFFS:
            ailment = MARK_BUFFS[mark.name]
            inflicts = number(stats.get(f"{ailment}BuildupAvg")) > 0  # (PoB: a hit's average buildup, % of the threshold)
            marked = 1.0 if use.on_demand or not period else min(1.0, MARK_DURATION / period)
            if use.on_demand:
                period = max(period, MARK_DURATION)  # reapplied as it runs out (its duration's modifiers aren't read)
            rate = buildup_rate(stats, ailment)
            share = min(1.0, MARK_BUFF_DURATION * rate) * marked if inflicts else 0.0
            if inflicts and rate:
                held, consumed_by = min(1 / rate, MARK_DURATION), f"the boss's next {ailment} consumes it"
            else:
                consumed_by = f"never consumed (no {ailment})"
            label, basis = f"{mark.name}: buff uptime", (
                (f"the boss {ailment.lower()}d once per {(1 / rate) if rate else math.inf:.1f} s while marked (PoB's buildup per hit x "
                 f"{hits_per_second(stats):.2f} hits/s, Arbiter of Ash threshold, x6 for "
                 f"{AILMENT_REPEAT[ailment][1]:g} s after each), "
                 f"{MARK_BUFF_DURATION:.0f} s buff; marked {marked:.0%} of the time ({how})") if inflicts else
                f"the main skill can't {ailment.lower()} the boss, so the Mark is never consumed and gives no buff"
            ) + "; PoB counts the buff always up; its buildup part is scaled with it (assumed)"
        else:
            share = 0.0 if period == math.inf else 1.0
            label, basis = f"{mark.name}", ("PoB adds nothing to the main skill's damage from it; Blood Explosion's "
                                            "damage on a Heavy Stun isn't counted") + (f"; {how}" if share == 0 else "")
        if share < 1.0:
            mods.append(line("1% more Effect of your Curses", 100 * (share - 1), skill=mark.name))
        if (use.pressed is not None and use.pressed.name in MARK_CARRIERS and use.pressed is not main
                and period and period != math.inf):
            # Pounce's own slam: PoB's Full DPS counts it as used nonstop, as fast as its cooldown allows (CalcOffence
            # caps Speed at 1 / Cooldown); it's used once per Mark (MECH36), so its damage is scaled to that rate
            pounce = use.pressed
            nonstop = (pounce.cooldown or 0.0) / (pounce.cooldown_recovery or 1.0) or pounce.cast_time or 1.0
            rate = min(1.0, nonstop / period)
            scales[pounce.name] = rate  # (its Full DPS entry; a less-Damage line isn't linear either: x0.595 for x0.625)
            basis += (f"; {pounce.name}'s own hit counted once per {period:.1f} s*, {rate:.0%} of PoB's rate (it counts "
                      f"one every {nonstop:.1f} s, its cooldown; assumed: its attack is faster than that)")
        if use.pressed is not None and main is not None and use.pressed is not main and period:
            took = use.pressed.cast_time or 0.5
            lost = min(1.0, took / period)
            mods.append(line("1% more Attack and Cast Speed", -100 * lost, skill=main.name))
            basis += f"; using {use.pressed.name} ({took:.2f} s every {period:.1f} s) takes {lost:.0%} of {main.name}'s time"
        value = round(game, 1) if mark.name == PREDATORS_MARK else round(share, 2)
        notes.append({"label": label, "value": value, "basis": basis, "confidence": "theory",
                      **({"play": play} if play else {}), **({"full_dps": scales} if scales else {})})
        marked = min(1.0, held / period) if period else 1.0
        found = []
        for pattern, text in MARKED_LINES:
            total = sum(float(m.group(1)) for entry in lines or [] if (m := pattern.search(entry["text"])))
            if total and marked < 1.0:
                mods.append(line(text, -total * (1.0 - marked)))
                found.append(f"{text.replace('1%', f'{total:g}%')}")
        if found:
            notes.append({"label": "Lines while the boss is Marked: share of the fight", "value": round(marked, 2),
                          "basis": f"{mark.name} applied every {period:.1f} s ({how}), held {held:.1f} s ({consumed_by}): "
                                   f"marked {marked:.0%} of the fight, so {'; '.join(found)} count at that share (PoB "
                                   "counts the enemy always Marked; crit chance and ailment magnitude scaled linearly, "
                                   "an approximation)", "confidence": "theory"})
    if len(marks) > 1:
        notes.append({"label": "Marks beyond the first", "value": len(marks) - 1,
                      "basis": "an enemy carries one Mark at a time: " + ", ".join(m.name for m in marks[1:])
                               + " not modelled (PoB keeps only one too)", "confidence": "theory"})
    return mods, notes


# Curses (MECH33) ---------------------------------------------------------------------------------
# PoB applies every enabled curse as always up and sets the enemy Cursed whenever a curse slot holds a curse or a Mark
# (CalcPerform 3166-3168), so "Enemies you Curse ..." lines count with a Mark alone. In the game files (build 26476662):
# the 10 gems of skill type AppliesCurse (ActiveSkills: Conductivity, Despair, Elemental Weakness, Enfeeble,
# Flammability, Hypothermia, Punishment, Repulsion, Temporal Chains, Vulnerability) last base_skill_effect_duration 6 s
# at level 1 to 7.4 s at 20, land curse_delay_duration_ms 1.5 s after the cast, and under level 18 can't curse targets
# above a level (skill_curses_cannot_apply_to_targets_above_level, 74 at 15: not checked, our gems are level 20).
# How the build applies each (curse_uses):
# - in a Blasphemy group: an Aura on "all nearby enemies" (the gem's description), taken as your Presence: up while the
#   boss stands inside it (the build's distance); the support's base radius stat isn't read;
# - with a Meta gem in its group: triggered at the meta's rate (its Energy, energy_model), up min(1, rate x duration)
#   (MECH46, FLAT_RATES A5: before, always up); a meta whose rate no model gives keeps "applied when needed" (LOW);
# - else cast by hand, recast each duration (1.5 s before it runs out, so always up after the fight's first 1.5 s,
#   not counted), which takes its cast time from the main skill (duration modifiers aren't read);
# - "Curse ... with X on Hit" item lines: up while the main skill hits.
CURSE_DURATION = 7.4
CURSE_DELAY = 1.5
# "Curses you inflict have infinite Duration" (RULE_AUDIT #1, MECH42): a curse cast by hand is cast once and never recast,
# so it costs the main skill no time (the first cast and its 1.5 s delay at the fight's start aren't counted). Before, the
# ledger dropped the line as "PoB already counts a curse as always applied", which no longer held once recasts cost time.
CURSE_FOREVER = re.compile(r"^Curses you inflict have infinite Duration$", re.I)
CURSE_ON_HIT = re.compile(r"\bcurse (?:non-cursed )?enemies with (.+?) on hit\b", re.I)
# Lines that apply only while the boss is cursed, each scaled to the share of the fight it is (exact for the taken and
# resistance ones, linear for the ailment duration one: an approximation)
CURSED_LINES = (
    (re.compile(r"Enemies you Curse take (\d+)% increased Damage", re.I), "Enemies you Curse take 1% increased Damage"),
    (re.compile(r"Enemies you Curse have ([+-]?\d+)% to Chaos Resistance", re.I),
     "Enemies you Curse have +1% to Chaos Resistance"),  # PoB's "to" form needs the sign (ModParser "^([%+%-]...)%? to")
    (re.compile(r"(\d+)% increased duration of Ailments you inflict against Cursed Enemies", re.I),
     "1% increased duration of Ailments you inflict against Cursed Enemies"),
)
# Chance lines against a cursed enemy: not scaled (a chance doesn't scale linearly), listed in the note
CURSED_UNSCALED = re.compile(r"against cursed enemies|hitting cursed enemies|poison cursed enemies", re.I)


@dataclass(frozen=True)
class CurseUse:
    name: str
    share: float  # of the fight the boss carries it
    how: str
    by_hand: bool = False  # cast by hand: one cast per CURSE_DURATION


def curse_uses(groups: list[dict], gems: dict, stats: dict, metres: float,
               lines: list[str] = (), rates: dict[str, float] | None = None) -> list[CurseUse]:
    """How the build applies each of its curses, and the share of the fight each is on the boss. `rates`: triggered
    skills' uses per second from the models (energy_model), by name."""
    uses = []
    radius = number(stats.get("PresenceRadius")) / 10
    forever = any(CURSE_FOREVER.search(text) for text in lines)
    for group in groups:
        if group.get("enabled") is False:
            continue
        present = [g for g in group["gems"] if g.get("enabled", True)]
        types = {g["name"]: set((gems.get(g["name"]) or {}).get("skillTypes") or []) for g in present}
        meta = next((n for n, t in types.items() if "Meta" in t), None)
        for gem in present:
            name = gem["name"]
            if gem.get("support") or "AppliesCurse" not in types[name]:
                continue
            if "Blasphemy" in types:
                up = 1.0 if radius >= metres else 0.0
                how = (f"an Aura (Blasphemy) on enemies nearby, taken as your Presence ({radius:.1f} m) with the boss "
                       f"{metres:g} m away: {'inside' if up else 'outside'} it")
                uses.append(CurseUse(name, up, how))
            elif meta is not None and rates is not None and name in rates:
                rate = number(rates[name])
                up = min(1.0, rate * CURSE_DURATION)
                uses.append(CurseUse(name, up, f"triggered by {meta} {rate:.3f} times/s (its Energy), "
                                               f"{CURSE_DURATION:g} s each: {up:.0%} of the fight"))
            elif meta is not None:
                uses.append(CurseUse(name, 1.0, f"triggered by {meta}: assumed applied when needed (LOW: no model gives "
                                                "its trigger rate)"))
            elif forever:
                uses.append(CurseUse(name, 1.0, "cast by hand once: 'Curses you inflict have infinite Duration' (no "
                                                "recasts; the first cast at the fight's start isn't counted)"))
            else:
                uses.append(CurseUse(name, 1.0, f"cast by hand every {CURSE_DURATION:g} s (its duration; recast "
                                                f"{CURSE_DELAY:g} s before it runs out, the curse delay)", True))
    hits = hits_per_second(stats) > 0
    for text in lines:
        if m := CURSE_ON_HIT.search(text):
            uses.append(CurseUse(m.group(1), 1.0 if hits else 0.0, f"'{text[:50]}': "
                                 + ("up while the main skill hits" if hits else "the main skill doesn't hit")))
    return uses


def curse_share(uses: list[CurseUse]) -> tuple[float, str]:
    """(share of the fight the boss carries one of your curses, basis)."""
    if not uses:
        return 0.0, "no curse in the build"
    best = max(uses, key=lambda u: u.share)
    return best.share, f"{best.name} {best.how}"


def curse_model(uses: list[CurseUse], skills: list[Skill], main: Skill | None,
                lines: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Each curse's effect scaled from PoB's always-up to its share, the time casting it by hand takes from the main
    skill, and the build's "Enemies you Curse ..." lines scaled to the share of the fight the boss is cursed (CURSED_LINES;
    PoB counts them with any curse or Mark)."""
    mods: list[dict] = []
    notes: list[dict] = []
    by_name = {s.name: s for s in skills}
    for use in uses:
        basis = use.how
        if use.share < 1.0 and use.name in by_name:
            mods.append(line("1% more Effect of your Curses", 100 * (use.share - 1), skill=use.name))
        cast = by_name.get(use.name)
        if use.by_hand and main is not None and cast is not None and cast is not main:
            took = cast.cast_time or 0.7
            lost = min(1.0, took / CURSE_DURATION)
            mods.append(line("1% more Attack and Cast Speed", -100 * lost, skill=main.name))
            basis += f"; casting it ({took:.2f} s every {CURSE_DURATION:g} s) takes {lost:.0%} of {main.name}'s time"
        notes.append({"label": f"{use.name}: share of the fight on the boss", "value": round(use.share, 2),
                      "basis": basis + " (PoB counts every curse always up)", "confidence": "theory"})
    share, how = curse_share(uses)
    found, unscaled = [], []
    for pattern, text in CURSED_LINES:
        total = sum(float(m.group(1)) for entry in lines or [] if (m := pattern.search(entry["text"])))
        if total and share < 1.0:
            mods.append(line(text, -total * (1.0 - share)))
            found.append(text.replace("+1%", f"{total:+g}%") if "+1%" in text else text.replace("1%", f"{total:g}%"))
    unscaled = [entry["text"] for entry in lines or [] if CURSED_UNSCALED.search(entry["text"])]
    if found or (unscaled and share < 1.0):
        notes.append({"label": "Lines while the boss is Cursed: share of the fight", "value": round(share, 2),
                      "basis": f"cursed {share:.0%} of the fight ({how}); " + (
                          f"{'; '.join(found)} count at that share (PoB counts the enemy Cursed with any curse or "
                          "Mark)" if found else "") + (
                          f"; not scaled (chances, counted as PoB does): {'; '.join(t[:50] for t in unscaled[:3])}"
                          if unscaled and share < 1.0 else ""), "confidence": "theory"})
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
    if kind == "minion death":
        rate, how = stats.get("_minion_deaths") or (0.0, "the build's minions weren't read")
        if rate <= 0:
            return 0.0, f"{how}: no Energy"
        return rate * per, (f"{how}; {per:g} Energy each (a minion of 100% relative Power assumed: the dying minion's Power "
                            f"isn't read yet, LOW)")
    if kind == "charm":
        spent = number(stats.get("_charm_charges"))
        if spent <= 0:
            return 0.0, "no charm is used against a boss (none equipped, or none makes the charges it needs): no Energy"
        return spent * per, (f"charms spend {spent:.2f} charges per second, each used as soon as its generated charges allow "
                             f"and not again while its effect lasts (the flask model: {stats.get('_charm_basis', '')}), for "
                             f"{per:g} Energy per charge (game files)")
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
    freezes = buildup_rate(stats, "Freeze")  # (RULE_AUDIT #2, MECH41: before, Freezes gave none on a boss)
    how = f"{main} shocks or ignites {events:.2f} times per second (one hit per use) for {BOSS_POWER} Energy each (the boss's Power)"
    if freezes > 0:
        how += (f", and freezes the boss {freezes:.3f} times per second for {FREEZE_ENERGY * BOSS_POWER:g} Energy each "
                f"({FREEZE_ENERGY:g} per Power; models.buildup_rate: the main skill's buildup only, other skills' Freezes "
                "aren't counted, an undercount)")
    return (events + freezes * FREEZE_ENERGY) * BOSS_POWER, how


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


def minion_count(spell: Skill, rate: float) -> tuple[float, str]:
    """A triggered spell summoning Temporary minions (Wardbound Minions: "Spend all your Runic Ward to summon Temporary
    Wardbound Casters", 15 s, at most 6 casts each, game files and PoB's data): PoB counts one minion casting nonstop
    whatever the trigger rate. The minions alive on average are the rate times each one's life (its duration, or its
    casts at its cast speed, whichever ends first), up to the skill's limit. One minion per use: the Runic Ward each use
    spends for more isn't counted (assumed none)."""
    info = spell.minion or {}
    duration, casts, limit = number(info.get("duration")), info.get("maxCasts"), info.get("limit") or float("inf")
    cast_time, speed = number(info.get("castTime")), number(info.get("speed")) or 1.0
    life, how = duration, f"its {duration:g} s duration"
    if casts and cast_time > 0 and casts * cast_time / speed < duration:
        life = casts * cast_time / speed
        how = (f"its {casts:g} casts at {speed / cast_time:.2f} per second ({cast_time:g} s base cast time, the minion "
               f"modifiers' cast speed)")
    if life <= 0:
        return 1.0, "its minions' life isn't known: PoB's one minion casting nonstop is kept (assumed)"
    count = min(rate * life, limit)
    return count, (f"{rate:.3f} uses per second x {life:.2f} s each minion lives ({how})"
                   + (f", capped at the limit of {limit:g}" if count >= limit else "")
                   + "; one minion per use (the Runic Ward a use spends for more isn't counted); PoB counts one "
                     "minion casting nonstop")


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
        if cast_by_totem(skills, main) and META_GEMS[meta.name]["gain"] in OWN_USE_GAINS:
            gain, gain_basis = 0.0, f"{main.name} is cast by a totem, and the totem's hits and casts aren't yours (assumed)"
        elif main.name in CANNOT_GENERATE_ENERGY and META_GEMS[meta.name]["gain"] in OWN_USE_GAINS:
            gain, gain_basis = 0.0, (f"{main.name} \"cannot cause Meta Skills to generate Energy\" "
                                     "(its base_skill_cannot_generate_energy, game files): no Energy from its use")
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
            if rate > 0 and spell.minion is not None:
                count, basis = minion_count(spell, rate)
                mods.append(line("Minions deal 1% more Damage", 100 * (count - 1), skill=spell.name))
                notes.append({"label": f"{spell.name}'s minions alive on average ({meta.name})", "value": round(count, 3),
                              "basis": basis, "confidence": "theory"})
                if count < spell.minion.get("limit", float("inf")):  # (below the limit, the count goes with the rate)
                    mods += more_lines("Minions deal 1% more Damage", relative, x, passive, keys, skill=spell.name)
            elif rate > 0:
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
# A support's stat (an entry with `support: true`, its `skill` naming the supports, `support_stats` listing the stat ids
# the bridge must sum): read from each active skill's supports (bridge activeSkills supportStats: the named stats of the
# supports in that skill's own effect list) and tagged to that skill, so it reaches the skill the support is on and no
# other (two active skills with one name share PoB's SkillName tag: counted once, from the first that has the support).
# `to: triggered` (a support of a skill that triggers others, as Fluke on a meta gem: PoB puts it in the meta's effect list
# only, requireSkillTypes Triggers) tags the lines to the skills in the meta's group that it triggers instead (COVE3).
# `sum: true` adds the matched stats into one line (× factor), so a range's two ends make one average line: two "more"
# lines would multiply. An entry's `play` text goes in its note (what the player does for a condition counted in the
# headline, the user's play-strategy design).

def gem_stat_skills() -> list[str]:
    """Skills whose level stats the bridge must read for gem_stat entries."""
    from builder.ledger import gem_stat_entries

    return sorted({name for e in gem_stat_entries() if not e.raw.get("support") for name in _skill_names(e)})


def gem_stat_support_stats() -> list[str]:
    """Support stats the bridge must sum on each active skill for `support: true` gem_stat entries."""
    from builder.ledger import gem_stat_entries

    return sorted({stat for e in gem_stat_entries() if e.raw.get("support") and e.replace
                   for stat in [*(e.raw.get("support_stats") or ()), *(e.raw.get("share_stats") or ())]})


def _skill_names(entry) -> list[str]:
    names = entry.raw.get("skill") or []
    return [names] if isinstance(names, str) else list(names)


def gem_stat_model(skills: list[Skill], forbidden: set[str] = frozenset(), stats: dict | None = None,
                   main: Skill | None = None, previous: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """`previous`: the lines applied when `stats` was calculated (Assumptions.mods), so a `share:` entry whose own line
    moves the stat it reads (Cadence's Attack Speed) can take its own last line back out (SHARES)."""
    from builder.ledger import SYNTAX, gem_stat_entries

    mods: list[dict] = []
    notes: list[dict] = []
    for entry in gem_stat_entries():
        if entry.id in forbidden or not entry.replace or str(entry.raw.get("to", "")).startswith("part:"):
            continue  # (a `to: part:` entry is read by the model that counts that part: support_part_scale)
        names = _skill_names(entry)
        factor = float(entry.raw.get("factor", 1.0))
        own = entry.raw.get("applies_to", "skill") == "skill"
        support = bool(entry.raw.get("support"))
        done: set[str] = set()
        for skill in skills:
            if (skill.name in done) if support else (skill.name not in names):
                continue
            name = skill.name
            values = {stat: number(v) for stat, v in (skill.support_stats if support else skill.level_stats).items()
                      if entry.pattern is not None and entry.pattern.search(stat) and number(v)}
            if support and not values:  # (none of the entry's supports is on this skill)
                continue
            if entry.raw.get("sum") and values:
                values = {" + ".join(sorted(values)): sum(values.values())}
            targets = [name]
            if entry.raw.get("to") == "triggered":  # (the skills the supported skill triggers: its group's triggered ones)
                targets = [s.name for s in skills if s is not skill and s.group is not None and s.group == skill.group
                           and s.triggered]
                if not targets:
                    continue
            play, uptime = str(entry.raw.get("play") or ""), None
            if entry.raw.get("uptime") in UPTIMES:  # derived from the mechanic and the build's own numbers
                factor, why, play = UPTIMES[entry.raw["uptime"]](skill, stats or {})
                why = f"; uptime {factor:.0%}: {why}"
                # (worked out again by the bridge on every calculation, so an item or passive that changes it shows)
                uptime = "Uptime" + re.sub(r"\W", "", name)
                fn, args = UPTIME_INPUTS[entry.raw["uptime"]](skill)
                if values:
                    mods.append(derived(uptime, fn, factor * UNIT, **args))
            elif entry.raw.get("share") in SHARES:  # a share of the skill's uses, from the build's own output (SHARES)
                prev = sum(m.get("scale", 0.0) for m in previous or () if m.get("entry") == entry.id
                           and m.get("skill") == name)  # (its own line, in the stats it reads)
                factor, why, play = SHARES[entry.raw["share"]](skill, stats or {}, main, prev)
                why = f"; {why}"
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
                    for target in targets:
                        mods.append(line(m.group(3) if when else text, value * factor, when=when,
                                         skill=target if own else None))
                        if entry.raw.get("share") in SHARES:  # (found again in `previous` next time)
                            mods[-1]["entry"] = entry.id
                notes.append({
                    "label": entry.title,
                    "value": f"{value * factor:g}" + (f" ({value:g} × {factor:g})" if factor != 1 else ""),
                    "basis": (f"PoB never reads the support stat {stat} ({value:g}) of {' / '.join(names)} on {name}; counted as "
                              + (f"(on the skills it triggers: {', '.join(targets)}) " if targets != [name] else "")
                              if support else f"PoB never reads the gem stat {stat} ({value:g} at this level); counted as ")
                             + "; ".join(t.replace("1%", f"{value * factor:g}%") for t in entry.replace)
                             + ("" if own else " for the whole build") + why + (f". {entry.notes.strip()}" if entry.notes else ""),
                    "confidence": entry.confidence, "entry": entry.id, **({"play": play} if play else {})})
            if support:
                done.add(name)
            else:
                names = [n for n in names if n != name]  # one copy of each skill (a buff applies once)
    return mods, notes


# `to: part:<name>` (COVE4): a support stat acting only on a part of what the skill does that neither PoB nor the tool
# counts yet (Spell Cascade's Cascades, Kulemak's Dominion's spells around Offerings, Trickster's Shard's Fractured Mimic
# casts, Spectral Volley's Spectral Projectiles). gem_stat_model makes no line for it: while the part isn't counted the
# stat has nothing to act on. The model that counts the part (MECH-B) asks support_part_scale for its multipliers and
# puts the notes it returns in the build's notes.
PARTS = {"cascade": "Spell Cascade's Cascades", "offering": "spells cast around Offerings (Kulemak's Dominion)",
         "mimic": "spells cast by Fractured Mimics (Trickster's Shard)",
         "spectral": "Spectral Volley's Spectral Projectiles",
         "warcry_echo": "the Warcry's Echoes (Echoing Cry)",
         "tectonic_aftershock": "Tectonic Slams' extra Aftershock",
         "branch_fissure": "the branched Fissures (Branching Fissures)",
         "glory_cost": "the Glory the skill requires (read by the Glory models: glory_uses, usage.py)",
         "pin_duration": "the Pins it inflicts (no Pinned uptime is modelled yet)",
         "curse_duration": "the Curse's duration when the cast consumes a Power Charge (curse model)"}


def support_part_scale(skill: Skill, part: str) -> tuple[dict[str, float], list[dict]]:
    """Multipliers the supports on `skill` put on one uncounted part of what it does, keyed by the modifier the entry's
    line names ("Damage", "Area of Effect"), for the model that counts that part; and the notes saying so.
    A `mult:Name` line (COVE5; the ledger's model-input syntax, which PoB never parses) gives key Name as the stat's
    value summed, not a multiplier: a count, a time or a per-repeat % (WarcryEchoes 2; EchoAreaMorePercentPerEcho 100,
    so Echo n gets 1 + n x 100% more Area).
    `skill` is the skill the support is on: the spell for Spell Cascade and Trickster's Shard, the Offering for Kulemak's
    Dominion (its penalty is on the spells you cast around the Offering, and the support sits in the Offering's effect list, not theirs)."""
    from builder.ledger import gem_stat_entries

    scale: dict[str, float] = {}
    notes: list[dict] = []
    for entry in gem_stat_entries():
        if entry.raw.get("to") != f"part:{part}" or not entry.replace:
            continue
        for stat, raw in sorted(skill.support_stats.items()):
            value = number(raw)
            if not value or entry.pattern is None or not entry.pattern.search(stat):
                continue
            unit = "%"
            for text in entry.replace:
                if m := re.match(r"mult:(\w+)\s*(.*)$", text):  # (a count, time or per-repeat %: summed as it is)
                    scale[m.group(1)] = scale.get(m.group(1), 0.0) + value * (float(m.group(2) or 1))
                    unit = f" ({m.group(1)})"
                elif m := re.match(r"1% more (.+)$", text):
                    scale[m.group(1)] = scale.get(m.group(1), 1.0) * max(0.0, 1 + value / 100)
            notes.append({"label": entry.title, "value": f"{value:g}{unit} on {PARTS.get(part, part)}",
                          "basis": f"PoB never reads the support stat {stat} ({value:g}) of {' / '.join(_skill_names(entry))} on "
                                   f"{skill.name}; counted on {PARTS.get(part, part)} only"
                                   + (f". {entry.notes.strip()}" if entry.notes else ""),
                          "confidence": entry.confidence, "entry": entry.id})
    return scale, notes


# `share_stats` (COVE6): more of the supports' stats the share reads (summed by the bridge like support_stats, matched by
# no line: Cadence's maximum stacks and lockout).
# `share: <name>` (COVE5): the entry's lines count at a share of the skill's uses the build works out (SHARES), not on
# every use. Worked out once per resolve from the build's own output (a what-if that changes attack speed shows from the
# next resolve, not at once).
# Alignment I/II/III (RESEARCH10, the manager's revised ruling 12 Nov 2026 ~09:52): the support shows one of four
# directions and changes it each time an attack matches it (within called_shot_angle_allowance_degs 40). Against one boss
# the boss must lie in the shown direction, so after each match the player moves round the boss before the next attack
# can match: matched attacks per second are at most one per (the hidden called_shot_aiming_delay_ms 1000, meaning
# UNVERIFIED + the reposition time). ALIGNMENT_MATCH_SECONDS is that interval: a flat LOW number (the game and the
# boss's movement decide it; no data gives it; RESEARCH10's ~1.5 s), named in the report for the manager to ask about.
ALIGNMENT_MATCH_SECONDS = 1.5


def alignment_match_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    is_main = main is not None and main.name == skill.name and main.group == skill.group
    rate = number(stats.get("Speed")) if is_main else (1 / skill.cast_time if skill.cast_time else 0.0)
    where = "the skill's attack rate" if is_main else ("its base attack rate (not the main skill)" if rate else "")
    if rate <= 0:
        rate, where = 1.0, "attack rate unknown, taken as 1 attack/s"
    matches = 1 / ALIGNMENT_MATCH_SECONDS
    share = min(1.0, matches / rate)
    why = (f"matched share {share:.0%}: at most one matched attack per {ALIGNMENT_MATCH_SECONDS:g} s (the 1 s hidden aiming "
           f"delay, meaning UNVERIFIED, plus stepping round the boss to the next direction; LOW) against {rate:.2f} attacks/s "
           f"({where})")
    play = (f"After each matched attack, step round the boss so it lies in the next indicated direction (four directions, "
            f"40 degrees' allowance); the headline counts {share:.0%} of attacks matched (one per "
            f"{ALIGNMENT_MATCH_SECONDS:g} s, LOW). Without it (standing still) only the first attack matches: the bonus is ~0. "
            f"Every attack matched (not reachable against one boss at {rate:.2f} attacks/s) would give the full bonus.")
    return share, why, play


# Cadence (COVE6, 12 Nov 2026). Game files (GrantedEffectStatSets SupportCadencePlayer, gem_stat_descriptions):
# support_measured_speed_attack_speed_+%_final_per_stack 8, "Supported Skills have 8% more Attack speed for each time
# they've been used Recently"; support_measured_speed_maximum_stacks 6 and _disabled_ms 10000, "Supported Skills are
# unusable for 10 seconds after being used 6 or more times within four seconds" (KeywordPopups Recently: the past 4 s).
# PoB maps none of the three (sup_dex.lua SupportCadencePlayer has no statMap; SkillStatMap.lua names no measured_speed:
# checked), so before this PoB rated a Cadence skill at its plain speed with no lockout: too slow below the pace and too
# fast above it (an overcount for a skill faster than 1.25 uses/s). Here the player keeps to the pace that avoids the
# lockout, at most 5 uses in any 4 s (1.25/s), and the stacks are the uses in the 4 s before each use at the rate that
# results: r = min(r0 x (1 + 8% x n), 1.25), n = 4r - 1 (the use itself and the one on the window's edge left out;
# UNVERIFIED whether the use counts itself: one stack more, +8%). The line is one "more Attack Speed" (less above the pace)
# making the skill's speed r. Worked out from the main skill's PoB Speed with this line's own last value taken back out
# (`prev`); a skill that isn't the main skill gets 0 (no rate here to take its stacks or lockout from).


def cadence_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    st = skill.support_stats
    per = number(st.get("support_measured_speed_attack_speed_+%_final_per_stack")) or 8.0
    most = number(st.get("support_measured_speed_maximum_stacks")) or 6.0
    locked = (number(st.get("support_measured_speed_disabled_ms")) or 10000.0) / 1000
    window = 4.0  # Recently
    pace = (most - 1) / window
    play = (f"Use the skill at most {most - 1:g} times in any {window:g} s (one use per {1 / pace:.2f} s): a {most:g}th use "
            f"locks it for {locked:g} s.")
    if main is None or main.name != skill.name or main.group != skill.group:
        return 0.0, ("not the main skill: its use rate isn't known here, so neither its stacks nor its lockout are "
                     "counted (0)"), play
    r0 = number(stats.get("Speed")) / (1 + prev / 100)
    if r0 <= 0:
        return 0.0, "no attack rate from PoB: not counted (0)", play
    rate, stacks = r0, 0.0
    for _ in range(200):
        stacks = min(max(window * rate - 1, 0.0), most - 2)
        rate = min(r0 * (1 + per * stacks / 100), pace)
    pct = (rate / r0 - 1) * 100
    full = r0 * (1 + per * (most - 1) / 100)
    spam = most / ((most - 1) / full + locked)
    capped = rate >= pace - 1e-9
    why = (f"{stacks:.1f} stacks ({per:g}% more each): {r0:.2f} uses/s without it, {rate:.2f} with it"
           + (f", held to the {pace:g}/s pace that avoids the {locked:g} s lockout ({r0 * (1 + per * stacks / 100):.2f}/s "
              f"attack speed between uses; the time left between uses is free for other skills, not counted here; the pace's "
              f"edge, exactly {most:g} uses in {window:g} s, UNVERIFIED as allowed)" if capped else "")
           + "; UNVERIFIED whether a use counts itself (one stack more)")
    play += (f" Headline: {rate:.2f} uses/s ({stacks:.1f} stacks). Without the pacing (using it as fast as it goes): "
             f"{most:g} uses then {locked:g} s locked, ~{spam:.2f} uses/s." if capped else
             f" Headline: {rate:.2f} uses/s ({stacks:.1f} stacks); the skill stays under the pace on its own.")
    return pct / per, why, play


SHARES = {"alignment": alignment_match_share, "cadence": cadence_share}


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


# Heavy Stuns of the boss (heavy_stun_rate) ------------------------------------------------------------------------------
# The game's keyword text (KeywordPopups HeavyStun, Stun, Cold; build 26476662): hits build up the target's Stun bar by
# the damage they deal, a Heavy Stun when it fills; player Physical and Melee damage each build 50% more; "Cold hits ...
# cannot build up Heavy Stun"; and "it will also be harder to Heavy Stun them again for a short time afterwards". PoB
# works out a hit's average buildup (HeavyStunBuildupAvg, % of the bar, Physical and Melee bonuses and the build's own
# stun buildup modifiers in) against its Pinnacle preset, and three things are corrected here:
# - Cold: PoB's buildup counts Cold damage (Data.lua buildupTypes.HeavyStun lists it), the game says it builds none; the
#   Cold share of the weighted hit (Physical x1.5) is taken out.
# - The threshold: PoB's Pinnacle preset is the level's monster threshold (DefaultMonsterStats.PoiseThreshold, which PoB's
#   table matches: 236,905 at 82) x6 unique (MonsterUnique2, +500% final, as the files) x9.38 "Xesht" (+838%). The game
#   files' Xesht (MonsterVarieties ItThatReturned, row 1946) has PoiseThreshold 1700, read as x17.00 (the column is 100
#   for 1,008 of 2,794 varieties). The Arbiter of Ash is x12.35 (rows 1954-1955), the boss the user chose (9 Nov): buildup
#   x9.38/12.35 (Freeze buildup below uses the same boss).
# - Again afterwards: the game constants HeavyStunThresholdModifier 500 and HeavyStunModifierDuration 16.5 (as PoB's
#   Data/Misc.lua reads them; PoB doesn't use them), read as +500% threshold for 16.5 s after each Heavy Stun. With t the
#   time to fill the bar at the plain threshold, a stun comes every 6t if 6t <= 16.5 s, else every 16.5 + (1 - 16.5/6t)t.
# Only the main skill's hits count (other skills' buildup is an undercount); the bar's decay between hits isn't modelled
# (a main skill hits more often than any decay delay); the first stun's shorter wait isn't counted.
HEAVY_STUN_BOSS = (9.38, 12.35)  # PoB's Pinnacle (Xesht) threshold multiplier, the game files' Arbiter of Ash (user, 9 Nov)
HEAVY_STUN_REPEAT = (6.0, 16.5)  # threshold x(1 + 500%) for 16.5 s after a Heavy Stun
HEAVY_STUN_PHYSICAL = 1.5  # player Physical damage's 50% more buildup (weighting the Cold share)


def repeat_every(t: float, times: float, window: float) -> float:
    """Seconds between one Heavy Stun / Freeze / Electrocute and the next when the bar fills in `t` s at the plain
    threshold and the threshold is x`times` for `window` s after each one: 6t if that ends inside the window, else the
    window and the rest of the bar at the plain threshold."""
    return times * t if times * t <= window else window + (1 - window / (times * t)) * t


def heavy_stun_rate(stats: dict) -> tuple[float, str]:
    """Heavy Stuns of the boss per second from the main skill's hits, and how it was worked out."""
    per_hit = number(stats.get("HeavyStunBuildupAvg")) / 100
    hits = hits_per_second(stats)
    if per_hit <= 0 or hits <= 0:
        return 0.0, "the main skill's hits build no Heavy Stun"
    crit = min(number(stats.get("CritChance")), 100) / 100
    weight = {t: (number(stats.get(f"{t}HitAverage")) * (1 - crit) + number(stats.get(f"{t}CritAverage")) * crit)
              * (HEAVY_STUN_PHYSICAL if t == "Physical" else 1.0) for t in DAMAGE_TYPES}
    total = sum(weight.values())
    uncold = 1 - weight["Cold"] / total if total > 0 else 1.0
    fill = per_hit * uncold * HEAVY_STUN_BOSS[0] / HEAVY_STUN_BOSS[1] * hits  # bars per second at the plain threshold
    if fill <= 0:
        return 0.0, "Cold hits build no Heavy Stun (the game's Cold text; PoB counts them)"
    t = 1 / fill
    every = repeat_every(t, *HEAVY_STUN_REPEAT)
    basis = (f"{per_hit:.2%} of the boss's Stun bar per hit (PoB){f', {1 - uncold:.0%} of it Cold, which builds none' if uncold < 1 else ''}, "
             f"x{HEAVY_STUN_BOSS[0] / HEAVY_STUN_BOSS[1]:.2f} for the Arbiter of Ash's threshold (the game files; the boss the user chose), {hits:.2f} hits/s: "
             f"full in {t:.1f} s, then +500% threshold for 16.5 s after each stun: one every {every:.1f} s")
    return 1 / every, basis



AILMENT_BOSS = HEAVY_STUN_BOSS[0] / HEAVY_STUN_BOSS[1]  # the Arbiter of Ash's threshold over PoB's Pinnacle one (as stun)
# Again afterwards (game files, GameConstants rows 38-39, 43-44): FreezeThresholdModifier 500 / FreezeModifierDuration 14 s,
# ElectrocuteThresholdModifier 500 / ElectrocuteModifierDuration 15 s, read as Heavy Stun's are (+500% threshold for that
# long after each one; PoB doesn't use them). PinThresholdModifier 500 / PinModifierDuration 13 s likewise (PoB's Misc.lua).
# Pins (MECH46, FLAT_RATES A2: before, one Pin per 30 s for every build, with or without a Pin source, an overcount) come
# from PoB's PinBuildupAvg, which is 0 unless the skill can Pin (base_all_damage_can_pin, support_pin_physical_damage_can_pin,
# "X damage can Pin", "Projectile damage builds Pin"...): KeywordPopups Pinned, "Certain skills and effects allow damage to
# build up Pinned ... Pinned ... for 3 seconds".
AILMENT_REPEAT = {"Freeze": (6.0, 14.0), "Electrocute": (6.0, 15.0), "Pin": (6.0, 13.0)}
IMMOBILISE_FIELDS = ("Speed", "HitChance", "CritChance", "FreezeBuildupAvg", "ElectrocuteBuildupAvg", "PinBuildupAvg",
                     "HeavyStunBuildupAvg", *(f"{t}HitAverage" for t in DAMAGE_TYPES),
                     *(f"{t}CritAverage" for t in DAMAGE_TYPES))


def buildup_rate(stats: dict, ailment: str) -> float:
    """Freezes or Electrocutes of the boss per second from the main skill's hits: PoB's average buildup per hit (% of the
    threshold) x hits per second, with the Arbiter of Ash's threshold (AILMENT_BOSS), the threshold x6 for 14 / 15 s
    after each one (AILMENT_REPEAT, repeat_every), and buildup paused while the boss is Frozen / Electrocuted (the user,
    11 Nov 2026; MECH41: before, the rate left the pause out, a small overcount). Buildup decay between hits isn't
    modelled (as Heavy Stun's)."""
    period = immobilise_period(stats, ailment)
    return 1 / period if period > 0 else 0.0


def immobilise_period(stats: dict, ailment: str) -> float:
    """Seconds from one Freeze / Electrocute to the next (0 with no buildup): its duration, then the time to fill the bar
    again at x6 for the rest of the 14 / 15 s after it."""
    fill = hits_per_second(stats) * number(stats.get(f"{ailment}BuildupAvg")) / 100 * AILMENT_BOSS
    if fill <= 0:
        return 0.0
    d = immobilise_duration(stats, ailment)
    times, window = AILMENT_REPEAT[ailment]
    return d + repeat_every(1 / fill, times, max(window - d, 0.0))


def immobilise_duration(stats: dict, ailment: str) -> float:
    mod = (number(stats.get("FreezeDurationMod")) or 1.0) if ailment == "Freeze" else 1.0
    return IMMOBILISE_DURATION[ailment] * mod


# How long the boss stays Frozen / Electrocuted (MECH38, 11 Nov 2026): the game files' KeywordPopups, "Freeze ... lasts 4
# seconds by default", "Electrocution ... lasts 5 seconds by default" (PoB's Data/Misc.lua: the same). Freeze x PoB's
# FreezeDurationMod; no Electrocute duration modifier exists (ModCache searched for every "Electrocut" line). A boss's own
# duration reduction, if any, isn't in the game files we read (labelled MEDIUM).
IMMOBILISE_DURATION = {"Freeze": 4.0, "Electrocute": 5.0, "Pin": 3.0}  # (Pin: KeywordPopups Pinned; pin_duration_+%
# modifiers aren't read: PoB gives no Pin duration, LOW)


def immobilised_share(stats: dict, ailment: str) -> tuple[float, str]:
    """Share of the fight the boss is Frozen / Electrocuted by the main skill's hits, and how. One every d + the time to
    fill the bar again, buildup paused while the boss is Frozen / Electrocuted (the user, 11 Nov 2026: it pauses; the game
    files don't say, MEDIUM), the threshold x6 for the rest of the
    14 / 15 s after each one (AILMENT_REPEAT)."""
    fill = hits_per_second(stats) * number(stats.get(f"{ailment}BuildupAvg")) / 100 * AILMENT_BOSS
    if fill <= 0:
        return 0.0, ""
    d, period = immobilise_duration(stats, ailment), immobilise_period(stats, ailment)
    window = AILMENT_REPEAT[ailment][1]
    share = min(1.0, d / period)
    return share, (f"{fill * 100:.1f}% of the bar per s (PoB's buildup per hit x {hits_per_second(stats):.2f} hits/s, "
                   f"Arbiter of Ash threshold); {d:g} s each, one every {period:.1f} s (x6 threshold for "
                   f"{window:g} s after each, buildup paused while it lasts, the user's word, MEDIUM): {share:.0%}")


# Electrocuting Arrow's Rods (MECH40, 11 Nov 2026; RULE_AUDIT 13). Game files / PoB act_dex.lua: "Hitting an enemy will
# attach an Electrocuting Rod to them ... This rod is removed when the enemy is next Electrocuted"; base_skill_effect_duration
# 25 s. PoB counts both rod stats always on, from the skill's stat map: electrocuting_arrow_damage_taken_+% (quality,
# "Targets with attached Rods take X% increased damage", an enemy Debuff) and
# electrocuting_arrow_%_damage_gained_as_extra_lightning_on_debuffed_target (quality, a Buff). Here both are scaled to the
# share of the fight a Rod is on the boss: each Electrocute removes it until Electrocuting Arrow hits again. Electrocutes
# per second from the main skill's buildup (buildup_rate; other Electrocute sources aren't counted: LOW, they would lower it).
# A Rod is assumed to attach while the boss is Electrocuted (the description only says it's removed when the enemy is next
# Electrocuted; UNVERIFIED). The Rod's own effect, all Lightning damage building up Electrocution, isn't modelled (an
# undercount, labelled in the catalog).
ELECTROCUTING_ARROW = "Electrocuting Arrow"
ROD_SECONDS = 25.0  # base_skill_effect_duration
ROD_REACTION = 0.5  # s before a non-main Electrocuting Arrow is pressed again after an Electrocute (assumed)


def rod_share(skill: Skill, stats: dict, main: Skill | None) -> tuple[float, str]:
    """Share of the fight a Rod is on the boss, and how."""
    rate = buildup_rate(stats, "Electrocute")
    if main is skill or (main is not None and main.name == skill.name):
        hps = hits_per_second(stats)
        gap = 0.5 / hps if hps > 0 else ROD_SECONDS
        how = f"the main skill, {hps:.2f} hits/s: the next hit comes {gap:.2f} s after each Electrocute on average"
    else:
        use = skill.cast_time or 1.0
        gap = ROD_REACTION + use
        how = (f"pressed again after each Electrocute: {ROD_REACTION:g} s to react (assumed) + its {use:.2f} s use; "
               f"it must be pressed at least once per {ROD_SECONDS:g} s (the Rod's duration) between Electrocutes")
    share = max(0.0, 1.0 - rate * gap)
    if rate <= 0:
        return 1.0, f"the build doesn't Electrocute the boss with its main skill, so the Rod stays on ({how})"
    return share, (f"{rate * 60:.1f} Electrocutes of the boss per minute (main skill's buildup, Arbiter of Ash threshold), "
                   f"the Rod off from each until the next hit: {how}; on {share:.1%} of the fight")


def rod_model(skills: list[Skill], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    skill = next((s for s in skills if s.name == ELECTROCUTING_ARROW), None)
    if skill is None:
        return [], []
    share, how = rod_share(skill, stats, main)
    note = {"label": f"{ELECTROCUTING_ARROW}: Rod on the boss", "value": round(share, 3), "confidence": "theory",
            "basis": f"PoB counts the Rod's quality stats (damage taken, extra Lightning on debuffed targets) always on; "
                     f"scaled to the share a Rod is attached: {how}. A Rod attaching while the boss is Electrocuted is "
                     "UNVERIFIED; other Electrocute sources aren't counted (LOW)"}
    if share >= 1.0:
        return [], [note]
    cut = -100 * (1 - share)
    return [raw_mod("DebuffEffect", "MORE", cut, ELECTROCUTING_ARROW),
            raw_mod("BuffEffect", "MORE", cut, ELECTROCUTING_ARROW)], [note]


# Run and Gun (MECH44, 11 Nov 2026; before, the ledger's approved no-effect-run-and-gun dropped it for every skill: "one
# target can't be hit twice by most skills"). Game files: KeywordPopups Projectile, "only one Projectile in the group can
# hit each target unless otherwise specified"; PoB doesn't parse the line (ModCache: nil). The user (11 Nov): count it for
# the skills that are exceptions, labelled. Which skills (GrantedEffectStatSets, build 26476662, every player stat set
# with projectiles_can_shotgun, projectiles_crossbow_barrage or projectiles_barrage):
# - shotgun (projectiles_can_shotgun: all can hit one target): SHOTGUN_SKILLS. PoB reads nothing of it; counted as more
#   damage, 2 x the chance over the skill's projectile count (PoB's ProjectileCount; all of them assumed to hit the boss);
# - fired in sequence (projectiles_crossbow_barrage: the crossbow skills; projectiles_barrage: Oil Barrage): PoB's own
#   SequentialProjectiles path counts PoB's TwoAdditionalProjectilesChance (more DPS per projectile and the extra attack
#   time each costs, CalcOffence), so the chance goes to PoB as that modifier.
# "While moving": a skill usable while moving (skill type UsableWhileMoving) fired while walking, a way to play the build,
# said in the guide, unless the build stands still (its stationary lines, the Standing still box); other skills: none.
SHOTGUN_SKILLS = frozenset({"Galvanic Shards", "Fragmentation Rounds", "Incendiary Shot", "Permafrost Bolts", "Ice Fragments"})
SEQUENCE_SKILLS = frozenset({"Crossbow Shot", "Explosive Shot", "High Velocity Rounds", "Glacial Bolt", "Rapid Shot",
                             "Armour Piercing Rounds", "Ice Shards", "Shockburst Rounds", "Oil Barrage", "Requiem"})
RUN_AND_GUN = re.compile(r"^Projectile Attacks have a (\d+(?:\.\d+)?)% chance to fire two additional Projectiles while moving$",
                         re.I)


def run_and_gun_model(texts: list[str], stats: dict, main: Skill | None, main_types: set[str],
                      standing: bool) -> tuple[list[dict], list[dict]]:
    chance = sum(float(m.group(1)) for t in texts if (m := RUN_AND_GUN.search(t.strip())))
    if not chance or main is None:
        return [], []
    label = f"Run and Gun ({chance:g}% chance of two more projectiles while moving)"
    if main.name not in SHOTGUN_SKILLS | SEQUENCE_SKILLS:
        return [], [{"label": label, "value": 0, "confidence": "theory",
                     "basis": f"{main.name}'s projectiles can't hit one target together (KeywordPopups Projectile): none"}]
    if "UsableWhileMoving" not in main_types or standing:
        why = "the build stands still while using it" if standing else f"{main.name} isn't used while moving"
        return [], [{"label": label, "value": 0, "confidence": "theory", "basis": f"{why}: none"}]
    play = "fire while walking (a way to play the build; standing still loses it)"
    if main.name in SEQUENCE_SKILLS:
        return [raw_mod("TwoAdditionalProjectilesChance", "BASE", chance, main.name)], [{
            "label": label, "value": chance, "confidence": "theory",
            "basis": f"{main.name} fires its projectiles in sequence: PoB counts the chance as its own "
                     f"TwoAdditionalProjectilesChance (more damage per projectile, more attack time each); {play}"}]
    count = max(number(stats.get("ProjectileCount")), 1.0)
    more = 100.0 * 2 * chance / 100 / count
    return [line("1% more Damage", more, skill=main.name)], [{
        "label": label, "value": round(more, 2), "confidence": "theory",
        "basis": f"{main.name}'s projectiles can all hit one target (projectiles_can_shotgun): 2 x {chance:g}% more over its "
                 f"{count:g} projectiles (all assumed to hit the boss) = {more:.1f}% more damage; {play}"}]


# Parried (MECH39, 11 Nov 2026). Game files (GrantedEffectStatSets, ActiveSkills): Parry ("Ready your Buckler to parry the
# next Hit you would take ... causing them to take massively increased Attack damage for a short duration"):
# base_parry_buff_damage_taken_+%_final_to_apply 50, base_parry_duration_ms 2000, a 0.75 s retaliation (its attackTime).
# Refutation (a buff that "causes you to Block all Blockable Hits and apply Parried to enemies from which you've Blocked a
# Hit", spending all your Runic Ward): base_skill_effect_duration 4000, the same 50, its cooldown not recovering during the
# buff (display_this_skill_cooldown_does_not_recover_during_buff). PoB applies the 50% more Attack damage taken only with
# its "Enemy has Parry Debuff" box (parryActive), which nothing set. Raise Shield carries the same stat in the game files
# but only parries with Bastion of Light, which the ledger counts (bastion-of-light-parried), so it isn't a source here.
# The boss's hits come every enemySkillTime (PoB's boss preset, 0.7 s); a parry waits half that on average.
PARRY_SKILLS = ("Parry", "Refutation")
# Consume Parried on hit (game files: the stats consume_parried_on_hit (Fangs of Frost) and
# consume_parry_debuff_on_hit_to_gain_X_frenzy_charges (the Frenzied Riposte support), and Disengage's description, are
# every stat or ActiveSkills description naming it): a main skill with one gains on one hit per Parried
PARRY_CONSUMERS = frozenset({"Fangs of Frost", "Disengage", "Frenzied Riposte"})
PARRIED_SECONDS = 2.0  # base_parry_duration_ms; "increased Parried Debuff Duration" lines aren't read (LOW, an undercount)
PARRIED_MORE = 0.5  # base magnitude; magnitude modifiers are PoB's own
PARRY_ACTION = 0.75  # Parry's retaliation (attackTime 750 ms); attack speed isn't counted (an undercount of its rate)
REFUTATION_BUFF = 4.0  # base_skill_effect_duration; duration modifiers aren't read
# How often the player parries with Parry (the user, 11 Nov 2026: "any player is going to be hitting as often as possible
# even when trying to actively parry, so assuming they're only able to parry telegraphed hits is probably safe, or maybe
# even a little generous"). We have no source for which boss hits are telegraphed or how often (PoB's boss preset gives
# only one hit every enemySkillTime, 0.7 s), so the cadence is assumed: one telegraphed hit every 5 s, which the user
# called fair (11 Nov 2026, ~17:45): MEDIUM.
PARRY_EVERY = 5.0


@dataclass(frozen=True)
class ParryPlan:
    box: float  # share of the main skill's hits that land on a Parried boss (the box's weight)
    lost: float  # share of the main skill's time spent parrying
    how: str


def parry_plan(skills: list[Skill], stats: dict, attack: bool, consumes: bool = False) -> ParryPlan:
    """How a reasonable player uses the build's Parry / Refutation against the boss, if it pays (only Attack damage gains):
    Parry: the player attacks all the time and parries the boss's telegraphed hits (PARRY_EVERY), each parry losing its
    retaliation and leaving the boss Parried for part of the attacking time; Refutation: cast on its cooldown, the boss
    Parried from its first blocked hit until 2 s after the buff, attacking throughout (the cast time is lost). The better
    of the two; neither when it doesn't beat not parrying. A main skill that consumes Parried (`consumes`) gains on one
    hit per Parried (chance not to consume isn't read: an undercount)."""
    names = {s.name: s for s in skills if s.name in PARRY_SKILLS}
    if not names:
        return ParryPlan(0.0, 0.0, "")
    if not attack:
        return ParryPlan(0.0, 0.0, f"{', '.join(names)}: Parried raises only Attack damage taken, and the main skill isn't an "
                                   "Attack: not used")
    every = number(stats.get("enemySkillTime")) or 0.7
    wait = every / 2
    hps = hits_per_second(stats)
    eaten = " (the main skill consumes Parried: one hit per Parried gains)" if consumes else ""
    options = []
    if "Parry" in names:  # the user (11 Nov): attack as often as possible, parry only the boss's telegraphed hits
        lost = PARRY_ACTION / PARRY_EVERY
        attacking = PARRY_EVERY - PARRY_ACTION
        share = min(1.0, (PARRIED_SECONDS - PARRY_ACTION / 2) / attacking)
        box = min(share, 1 / (hps * attacking)) if consumes and hps > 0 else share
        options.append(((1 + PARRIED_MORE * box) * (1 - lost), ParryPlan(
            box, lost, f"Parry the boss's telegraphed hits (one every {PARRY_EVERY:g} s, assumed; the user called it fair, MEDIUM) and attack the rest "
                       f"of the time: each parry costs its {PARRY_ACTION:g} s retaliation ({lost:.0%} of the main skill's "
                       f"time) and leaves the boss Parried {PARRIED_SECONDS:g} s from the parry, taken as half-way through "
                       f"the retaliation (LOW), so {share:.0%} of the main skill's hits land on a Parried boss (the play "
                       "guide shows the DPS while Parried and while not; the retaliation's own damage, attack speed "
                       "and Parried duration modifiers aren't counted, LOW)")))
    if (ref := names.get("Refutation")) is not None:
        if number(stats.get("Ward")) <= 0:
            options.append((0.0, ParryPlan(0.0, 0.0, "Refutation spends Runic Ward, and the build has none: not used")))
        elif number(ref.cooldown) <= 0:
            options.append((0.0, ParryPlan(0.0, 0.0, "Refutation: PoB gave no cooldown, so its rate isn't known: not counted")))
        else:
            cooldown = number(ref.cooldown) / (ref.cooldown_recovery or 1.0)
            period = cooldown + REFUTATION_BUFF
            up = min(1.0, (REFUTATION_BUFF - wait + PARRIED_SECONDS) / period)
            if consumes and hps > 0:  # re-applied by each blocked boss hit in the buff, eaten by the next main-skill hit
                up = min(up, (min(hps, 1 / every) * max(REFUTATION_BUFF - wait, 0.0) + 1) / (hps * period))
            cast = ref.cast_time or 0.65
            lost = min(cast / period, 1.0)
            options.append(((1 + PARRIED_MORE * up) * (1 - lost), ParryPlan(
                up, lost, f"Refutation cast on its cooldown ({cooldown:.2f} s, not recovering during its {REFUTATION_BUFF:g} s "
                          f"buff): the boss Parried from its first blocked hit ({wait:.2f} s in, one every {every:g} s) until "
                          f"{PARRIED_SECONDS:g} s after the buff, {up:.0%} of the fight; the {cast:.2f} s cast takes {lost:.0%} of "
                          "the main skill's time (Runic Ward assumed back by each cast, LOW; duration modifiers not read)")))
    gain, best = max(options, key=lambda o: o[0])
    if gain <= 1.0:
        return ParryPlan(0.0, 0.0, "; ".join(p.how for _, p in options) + f": x{gain:.2f} at best, not worth the time: not used")
    return ParryPlan(best.box, best.lost, best.how + eaten + f" (x{gain:.2f} the main skill's Attack damage, 50% more taken)")


def parry_model(skills: list[Skill], stats: dict, main: Skill | None, consumes: bool = False) -> tuple[list[dict], list[dict]]:
    """The main skill's time spent parrying (parry_plan; the box itself is conditions' parryActive)."""
    if main is None or not any(s.name in PARRY_SKILLS for s in skills):
        return [], []
    plan = parry_plan(skills, stats, main.attack, consumes)
    notes = [{"label": "Parried (how the build parries the boss)", "value": f"{plan.box:.0%} of the main skill's hits",
              "basis": plan.how, "confidence": "theory"}] if plan.how else []
    if plan.lost <= 0:
        return [], notes
    return [line("1% more Attack and Cast Speed", -100 * plan.lost, skill=main.name)], notes


def immobilise_rate(stats: dict, events: set[str]) -> tuple[float, str]:
    """Times per second the main skill freezes, electrocutes, pins or heavy-stuns the boss, summed over `events` (a
    subset of freeze, electrocute, pin, stun), and how. Freeze and Electrocute from buildup_rate, stuns from
    heavy_stun_rate (light stuns not counted), Pins from PoB's Pin buildup (0 when the main skill can't Pin)."""
    rate, parts = 0.0, []
    for event, ailment in (("freeze", "Freeze"), ("electrocute", "Electrocute"), ("pin", "Pin")):
        if event in events:
            r = buildup_rate(stats, ailment)
            rate += r
            parts.append(f"{ailment} {r:.3f}/s (PoB's buildup per hit, Arbiter of Ash threshold, x6 for "
                         f"{AILMENT_REPEAT[ailment][1]:g} s after each)")
    if "stun" in events:
        r, _ = heavy_stun_rate(stats)
        rate += r
        parts.append(f"Heavy Stun {r:.3f}/s (heavy_stun_rate; light stuns not counted)")
    return rate, "; ".join(parts)

# Guided Hand (model: guided_hand) --------------------------------------------------------------------------------------
# "The next Attack you use within 4 seconds after Heavy Stunning a Rare or Unique Enemy is Ancestrally Boosted" and
# "Ancestrally Boosted Attacks deal 30% increased Damage" (passive tree). PoB parses the second line but scales it by an
# Ancestral Boost uptime only its known sources set, so on its own the node does nothing. Share of attacks boosted = Heavy
# Stuns per second (heavy_stun_rate, from the main skill's own buildup) / attacks per second. Counted as the equivalent
# increased Attack Damage, only while the passive's ledger condition is on.

GUIDED_HAND = "GuidedHand"
BOOSTED_LINE = re.compile(r"^Ancestrally Boosted Attacks deal (\d+)% increased Damage$", re.I)


def guided_hand_model(inputs: Inputs, lines: list[dict], stats: dict, main: Skill | None) -> tuple[list[dict], list[dict]]:
    if not inputs.has(GUIDED_HAND) or main is None or not main.attack:
        return [], []
    boost = sum(int(m.group(1)) for entry in lines if (m := BOOSTED_LINE.match(entry["text"].strip())))
    speed = number(stats.get("Speed"))
    if boost <= 0 or speed <= 0:
        return [], []
    stuns, basis = heavy_stun_rate(stats)
    share = min(1.0, stuns / speed)
    when = inputs.condition(GUIDED_HAND, True)
    if when is None:
        return [], []
    # (the bridge works the share out again from each calculation's own buildup and attack speed)
    return [line("1% increased Attack Damage", boost / UNIT, when=when, per=GUIDED_HAND),
            derived(GUIDED_HAND, "guidedHand", share * UNIT, boss=HEAVY_STUN_BOSS[0] / HEAVY_STUN_BOSS[1],
                    physical=HEAVY_STUN_PHYSICAL, times=HEAVY_STUN_REPEAT[0], window=HEAVY_STUN_REPEAT[1])], [{
        "label": "Guided Hand: Ancestrally Boosted attacks after a Heavy Stun",
        "value": f"{share:.1%} of attacks boosted ({boost}% increased Damage on those)",
        "basis": f"{basis}; against {speed:.2f} attacks per second",
        "play": "A build made to stun (more stun buildup, Physical melee slams) heavy-stuns more often and boosts more attacks.",
        "confidence": "theory", "entry": "guided-hand-boost"}]


# Glory on a boss (glory_uses) --------------------------------------------------------------------------------------------
# Each Glory skill's cost and what builds its Glory, from the game files (build 26476662, GrantedEffectStatSets, level 20;
# 9 Nov, MECH20): active_skill_requires_X_glory and active_skill_generates_mp_%_glory_per_<event> (that % of the target's
# monster Power per event; a pinnacle boss's Power is 20, as PoB's Multiplier:EnemyPower) or
# active_skill_hundred_times_generates_glory_per_<event> (/100 Glory per event). Events come from the main skill: its hits
# (attack, elemental or chaos hits), the boss's Heavy Stuns (heavy_stun_rate), Freezes (PoB's per-hit Freeze buildup, the
# same Arbiter of Ash threshold correction, no re-freeze window: an overcount if one exists), Ignites and Shocks (PoB's chance per
# hit). Not counted (undercounts, labelled): Glory from other skills' hits, armour breaks (Molten Blast), Ward spent
# (Skyfall), Walking Calamity's own source, increased Glory generation and Daresso's Passion's lower cost.
BOSS_POWER = 20
GLORY_SKILLS = {  # skill: (Glory per use, {event: Glory per event on a boss})
    "War Banner": (200, {"attack hit": 20}), "Defiance Banner": (200, {"attack hit": 20}),
    "Dread Banner": (200, {"attack hit": 20}),
    "Spear of Solaris": (20, {"freeze": 20, "ignite": 1, "shock": 1}), "Starborn Onslaught": (20, {"freeze": 20}),
    "Ancestral Cry": (100, {"ignite": 20}), "Apocalypse": (100, {"elemental hit": 20}),
    "Archon of Chayula": (100, {"chaos hit": 20}), "Hammer of the Gods": (20, {"heavy stun": 20}),
    "Molten Blast": (20, {"ignite": 20}), "Walking Calamity": (50, {}), "Skyfall": (270, {}),
}
GLORY_FROM_LIFE = {"Zerphi's Infamy": (50, 0.05)}  # Glory per use, share of Life spent on skills that becomes Glory
# KeywordPopups Glory (build 26476662): "each Skill can only generate Glory once every 0.5 seconds per monster" (MECHC2,
# RESEARCH4 glory-warcry-flat-rates.md, HIGH for the text; before, every event counted: a Banner at 6 hits/s got a use
# every 1.7 s). On one boss: a gain, then 0.5 s, then the wait for the skill's next event, so gains/s = E / (1 + 0.5 E)
# for E events/s (as the hit-Remnant cooldowns, assumptions.hit_remnant_rate), each gain the events' average Glory
# (MEDIUM: which event wins a window is taken as proportional). Life spent (Zerphi's Infamy) isn't a per-monster event.
GLORY_GAIN_EVERY = 0.5


def glory_events(stats: dict, main: Skill | None) -> dict[str, float]:
    """Glory-building events per second from the main skill on the boss."""
    hits = hits_per_second(stats)
    crit = min(number(stats.get("CritChance")), 100) / 100
    hit = {t: number(stats.get(f"{t}HitAverage")) > 0 for t in DAMAGE_TYPES}
    chance = lambda a: (number(stats.get(f"{a}ChanceOnHit")) * (1 - crit) + number(stats.get(f"{a}ChanceOnCrit")) * crit) / 100
    return {
        "attack hit": hits if main is not None and main.attack else 0.0,
        "elemental hit": hits if any(hit[t] for t in ("Fire", "Cold", "Lightning")) else 0.0,
        "chaos hit": hits if hit["Chaos"] else 0.0,
        "heavy stun": heavy_stun_rate(stats)[0],
        "freeze": buildup_rate(stats, "Freeze"),
        "ignite": hits * min(chance("Ignite"), 1.0),
        "shock": hits * min(chance("Shock"), 1.0),
    }


MANA_FLARE = "Mana Flare"  # the support's triggered spell (game files: TriggeredManaFlarePlayer, GrantedEffects 734)
MANA_FLARE_SHARE = 0.25  # stat set 878: support_mana_flare_%_of_current_mana_consumed 25
MANA_FLARE_FIELDS = ["Speed", "HitChance", "CritChance", "ManaPerSecondCost", "ManaRegenRecovery", "Mana"]


def mana_flare(spell: dict, cooldown: float, recovery: float) -> tuple[float, float, str]:
    """Mana Flare's uses per second and the share of maximum mana it finds. Game files: "Supported Skills trigger Mana
    Flare on Critical Hit", a 1 s cooldown (StoredUses 1), and each flare consumes 25% of current mana. PoB has no trigger
    for it (no rate) and calculates it at full mana (config multiplierCurrentManaPercentage, default 100): two overcounts.
    `spell`: the supported spell's own output (MANA_FLARE_FIELDS), calculated as the main skill.
    - rate = the lower of the cooldown's (recovery / cooldown) and the spell's Critical Hits per second (casts x hit chance
      x crit chance; one hit per cast counted, so a spell that hits several times per cast is undercounted)
    - mana: each flare takes 25% of current mana, and the mana regenerated between flares, (regen - the spell's mana per
      second) / rate, puts it back. Before each flare that settles at m = (regen - cost) / (0.25 x rate), capped at maximum
      mana; none if the spell costs more than regen gives."""
    casts = float(spell.get("Speed") or 0.0)
    crits = casts * min(1.0, float(spell.get("HitChance") or 100.0) / 100.0) * min(1.0, float(spell.get("CritChance") or 0.0) / 100.0)
    cap = recovery / cooldown if cooldown else math.inf
    rate = min(cap, crits)
    mana = float(spell.get("Mana") or 0.0)
    net = float(spell.get("ManaRegenRecovery") or 0.0) - float(spell.get("ManaPerSecondCost") or 0.0)
    if rate <= 0.0 or mana <= 0.0:
        share = 1.0 if mana > 0.0 else 0.0
    else:
        share = min(1.0, max(0.0, net) / (MANA_FLARE_SHARE * rate) / mana)
    basis = (f"the lower of its cooldown ({cap:.2f}/s) and the supported spell's Critical Hits ({casts:.2f} casts/s x "
             f"{min(1.0, float(spell.get('CritChance') or 0.0) / 100.0):.0%} crit = {crits:.2f}/s; one hit per cast); "
             f"mana before each flare {share:.0%} of maximum (regen {float(spell.get('ManaRegenRecovery') or 0.0):.1f}/s "
             f"less the spell's {float(spell.get('ManaPerSecondCost') or 0.0):.1f}/s, 25% of current mana taken per flare"
             + ("; the spell costs more than regen gives, so the flare finds none" if net <= 0.0 else "") + ")")
    return rate, share, basis


def glory_uses(names: list[str], stats: dict, main: Skill | None) -> tuple[float, str]:
    """Uses per second of the build's Glory skills on a boss, all together, and how."""
    events = glory_events(stats, main)
    total, parts = 0.0, []
    for name in names:
        if name in GLORY_SKILLS:
            cost, gains = GLORY_SKILLS[name]
            rate = sum(events[e] for e in gains)
            glory = sum(events[e] * g for e, g in gains.items())
            how = " + ".join(f"{events[e]:.2f} {e}s/s x {g}" for e, g in gains.items()) or "no source counted"
            if rate > 0:  # one gain per 0.5 s per monster
                kept = 1.0 / (1.0 + GLORY_GAIN_EVERY * rate)
                glory *= kept
                how += f", at most one gain per {GLORY_GAIN_EVERY:g} s on the boss ({kept:.0%} of them count)"
        elif name in GLORY_FROM_LIFE:
            cost, share = GLORY_FROM_LIFE[name]
            glory = share * number(stats.get("LifePerSecondCost"))
            how = f"{share:.0%} of {number(stats.get('LifePerSecondCost')):.0f} Life spent/s"
        else:
            continue
        total += glory / cost
        parts.append(f"{name}: {how} Glory against {cost} per use = one per {cost / glory:.1f} s" if glory > 0
                     else f"{name}: {how}, never")
    return total, "; ".join(parts) or "no Glory skill"


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


# A pressed non-main skill in Full DPS (MECH37) -------------------------------------------------------------------
# PoB's Full DPS counts every group in it as used nonstop, though only the main skill is: a skill the player presses
# beside it is used on its cooldown, once per its buff/debuff duration if it's pressed to keep one up, or else at a
# labelled guess, and the main skill loses the time those presses take.
PRESS_EVERY = 5.0  # seconds between presses of a skill with neither a cooldown nor a duration (LOW: a guess)
# Full DPS's ailment entries (source: the skill that's their best source) and the ailment whose stacks set each
# (PoB: Burning Ground is a share of the Ignite's damage)
PRESS_AILMENTS = {"Best Ignite DPS": "Ignite", "Best Burning Ground DPS": "Ignite", "Best Bleed DPS": "Bleed",
                  "Best Poison DPS": "Poison"}


def press_key(name: str, source: str | None) -> str:
    """objective.Objective.press_scales' key for a SkillDPS entry: its name, and its source for an ailment's."""
    return f"{name} from {source}" if source and source != name else name


def press_model(rates: list[dict], main_index: int | None, pressed: set[int]) -> tuple[dict[str, float], list[dict]]:
    """Full DPS entry scales ({SkillDPS name: x}) and notes for the pressed non-main groups `pressed`, from the bridge's
    `useRates` (each group's skill as if it were the main one: its speed, capped by its cooldown as PoB does, its
    cooldown after recovery, repeats, duration and base cast time)."""
    scales: dict[str, float] = {}
    notes: list[dict] = []
    busy, users = 0.0, []
    for rate in rates:
        if rate["index"] not in pressed or rate["index"] == main_index:
            continue
        speed, repeats = number(rate.get("speed")), number(rate.get("repeats")) or 1.0
        if speed <= 0:
            continue
        cooldown, duration = number(rate.get("cooldown")), number(rate.get("duration"))
        if cooldown > 0:
            real, how, confidence = repeats / cooldown, f"on its {cooldown:.2f} s cooldown", "MEDIUM"
        elif duration > 0:
            real, how, confidence = repeats / duration, f"once per its {duration:.2f} s duration, to keep it up", "MEDIUM"
        else:
            real, how, confidence = repeats / PRESS_EVERY, f"once every {PRESS_EVERY:g} s* (a guess: it has no cooldown "\
                "or duration to set its use)", "LOW"
        real = min(real, speed)
        capped = cooldown > 0 and speed >= repeats / cooldown * (1 - 1e-6)
        # time per use: 1/speed, unless the cooldown capped PoB's speed (then its base cast time, before speed: an
        # overcount of the time it takes)
        # time per use: 1/speed, unless the cooldown capped PoB's speed: then its own use time (PoB's Time/HitTime,
        # before action speed), else its base cast time
        uncapped = number(rate.get("time")) or number(rate.get("castTime"))
        action = min(1 / speed, uncapped) if capped else 1 / speed
        busy += real * action
        ratio = real / speed
        scales[rate["name"]] = scales.get(rate["name"], 1.0) * ratio
        users.append(rate["name"])
        # Its ailments: PoB counts min(stacks, the most that count), stacks = chance x duration x its speed (its
        # StackPotential = stacks / most); at the real rate there are that x ratio
        ailing = []
        for entry, ailment in PRESS_AILMENTS.items():
            potential = number((rate.get("stackPotential") or {}).get(ailment))
            if potential > 0:
                share = min(potential * ratio, 1.0) / min(potential, 1.0)
                scales[press_key(entry, rate["name"])] = share
                if entry != "Best Burning Ground DPS":
                    ailing.append(f"{ailment} x{share:.3f} ({potential:.2f} of its most stacks nonstop)")
        notes.append({
            "label": f"{rate['name']}: pressed beside the main skill*", "value": f"x{ratio:.3f}",
            "basis": f"PoB's Full DPS counts it used nonstop ({speed:.2f}/s); it's pressed {how}, {real:.2f} uses/s, "
                     f"each taking {action:.2f} s from the main skill"
                     + (f". Its ailments, where it's their best source in Full DPS, at the stacks those presses keep "
                        f"up (PoB's chance x duration x rate, capped at the most that count): {'; '.join(ailing)}"
                        if ailing else ""),
            "confidence": confidence})
    main_rate = next((r for r in rates if r["index"] == main_index), None)
    main = main_rate["name"] if main_rate else None
    if users and main is not None and busy > 0:
        share = max(0.0, 1.0 - busy)
        scales[main] = scales.get(main, 1.0) * share
        for entry, ailment in PRESS_AILMENTS.items():  # (its ailments' stacks shrink with it, as above)
            potential = number((main_rate.get("stackPotential") or {}).get(ailment))
            if potential > 0:
                scales[press_key(entry, main)] = min(potential * share, 1.0) / min(potential, 1.0)
        notes.append({
            "label": f"{main}: time left beside {', '.join(users)}*", "value": f"{share:.1%}",
            "basis": f"the presses take {busy:.1%} of the fight (their uses x time per use); its ailments' stacks "
                     "shrink with it (PoB's chance x duration x rate, capped at the most that count)",
            "confidence": "MEDIUM"})
    return scales, notes
