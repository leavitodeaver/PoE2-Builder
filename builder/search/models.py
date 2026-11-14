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
from builder.search import boss_hits, bosses

BOSS_POWER = 20  # "Unique monsters always have 20 Power" (https://poe2db.tw/us/Monster_Power)
DAMAGE_TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
# PoB stats the models read (beyond the objective's own fields)
MODEL_FIELDS = sorted({
    "Speed", "HitChance", "CritChance", "HazardRearmChance", "SpiritUnreserved", "Mana", "ManaPerSecondCost", "EnergyShield", "EnergyShieldRechargeDelay",
    "IgniteAvoidChance", "SelfIgniteDuration", "AilmentThreshold",
    "EnergyShieldRegenRecovery", "EnergyShieldRecharge", "PresenceRadius", "MovementSpeedMod",
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
    # Run and Gun: the main skill's projectiles (run_and_gun_model); returning projectiles: its pierce (return_model)
    "ProjectileCount", "PierceCount",
    # Aftershocks: the main slam's ailment chances and how many of each ailment can be on the boss (aftershock_model)
    "BleedChanceOnHit", "BleedChanceOnCrit", "PoisonChanceOnHit", "PoisonChanceOnCrit",
    "IgniteStacksMax", "BleedStacksMax", "PoisonStacksMax",
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
    # The Unleashed / the Legacy rune: hit damage bypassing Energy Shield below half (the bridge's setEsBypassBelowHalf)
    "EsBypassBelowHalf", "EsBypassPoolLost",
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


def raw_mod(name: str, kind: str, value: float, skill: str | None = None, tag: str | None = None) -> dict:
    """A PoB modifier by its internal name (no text to parse): `kind` "INC" / "MORE" / "BASE" / "FLAG", tagged to
    `skill` if given (SkillName). For what PoB has no parseable line for, such as one skill's DebuffEffect MORE.
    `tag`: its source becomes "Assumption:<tag>", which a bridge read can leave out (TOTEM_ONE_TAG)."""
    mod: dict = {"kind": "Raw", "name": name, "type": kind, "value": _round(value)}
    if skill:
        mod["skill"] = skill
    if tag:
        mod["tag"] = tag
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
    early_hazard: dict | None = None  # its scored set's "less damage if set off in its first 0.5 s" (hazard_early_model)
    energy_final: float = 0.0  # its own active_skill_energy_generated_+%_final (energy_gain; Incinerate -98)
    total_time_added: float = 0.0  # its "+N seconds to Total Cast / Attack Time" (PoB's TotalCastTime; energy_cost)
    stat_set: str | None = None  # the label of the stat set PoB calculates for it (bridge activeSkills statSet; COVE8)
    totem_limit: float = 0.0  # a totem-summoning skill's Totem Limit (bridge activeSkills totemLimit; totem_count_model)
    # a totem skill's count PoB multiplies its ailment stacks by: {override, limit} (bridge ailmentTotems; totem_count_model)
    ailment_totems: dict | None = None
    mark_duration: float = 0.0  # a Mark's duration with the build's modifiers (bridge activeSkills markDuration; MECH62)

    @classmethod
    def of(cls, raw: dict) -> Skill:
        return cls(raw["name"], raw.get("group"), bool(raw.get("spell")), bool(raw.get("attack")),
                   bool(raw.get("melee")), bool(raw.get("triggered")), bool(raw.get("energy")),
                   number(raw.get("castTime")), raw.get("cooldown"), number(raw.get("cooldownRecovery")) or 1.0,
                   dict(raw.get("levelStats") or {}), bool(raw.get("wrongWeapon")),
                   bool(raw.get("corpses")), raw.get("level"), raw.get("minion"), raw.get("summon"),
                   dict(raw.get("supportStats") or {}), raw.get("earlyHazard"), number(raw.get("energyFinal")),
                   number(raw.get("totalTimeAdded")), raw.get("statSet"), number(raw.get("totemLimit")),
                   raw.get("ailmentTotems"), number(raw.get("markDuration")))


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


def flame_uptime(cycle: float, recovery_faster: float, no_recovery: bool = False) -> tuple[float, float, float]:
    """Seething Body's Elemental Archon over Pyromantic Pact's Infernal Flame cycle (MECHC4; RESEARCH12 18af,
    archon-infernal-rest.md, rules HIGH): Flame climbs from 0 to its maximum (2 x maximum Mana) in `cycle` seconds of the
    build's skill costs, then resets to 0. Archon comes with the first spell cast on High Infernal Flame (65% of maximum)
    once the recovery period is over, and lasts until the reset ("does not expire while on High Infernal Flame"; "Lose
    Elemental Archon on reaching maximum Infernal Flame"), so Archon Duration lines don't lengthen it. (uptime, on, off)."""
    if cycle <= 0:
        return 0.0, 0.0, 0.0
    if FLAME_MANAGED:  # (the play guide's "pause near the top" trial: Archon held all fight, flame_pause_share's cost)
        return 1.0, math.inf, 0.0
    recovery = 0.0
    if not no_recovery:
        speed = 1 + recovery_faster / 100
        if speed <= 0:
            return 0.0, 0.0, 0.0
        recovery = ARCHON_RECOVERY / speed
    high = FLAME_HIGH * cycle  # (seconds into a cycle when Flame reaches High)
    whole, rest = divmod(recovery, cycle)
    if rest <= high:
        off = whole * cycle + high
    else:  # (the recovery ends while Flame is already High: gained at once)
        off = recovery
    on = (whole + 1) * cycle - off
    return on / (on + off), on, off


FLAME_HIGH = 0.65  # KeywordPopups HighInfernalFlame: 65% of maximum Infernal Flame or more
# Managing the bar (MECHC8; FIX_QUEUE "Seething Body", research/archon-infernal-rest.md): cast until Flame is nearly at its
# maximum, then stop: after 2 s without gaining Flame it decays by 25% of its maximum per second (Pyromantic Pact; the
# stat id says "of MAX", the text "of Infernal Flame": MEDIUM), so 1.4 s more brings it back to High (65%). Archon then
# never ends (it doesn't expire on High and the reset never comes), and no self-hit; the cost is the pause, 3.4 s per
# 0.7 x 2 x maximum Mana / cost of casting. Pausing longer (to 65%) is the best ratio: the 2 s are paid per pause, so the
# casting between them should be as long as High allows. LOW for how players time it (a play strategy, not counted in
# the headline: the play guide's row). FLAME_MANAGED is only switched on by that row's trial.
FLAME_MANAGED = False
FLAME_IDLE = 2.0  # seconds without gaining Flame before it decays (research note, MEDIUM)
FLAME_DECAY = 0.25  # share of maximum Flame lost per second once decaying (MEDIUM)


def flame_pause_share(mana: float, cost: float) -> float:
    """The share of the fight spent casting when the bar is managed (cast from High to the top, pause until High again)."""
    if mana <= 0 or cost <= 0:
        return 0.0
    cast = (1 - FLAME_HIGH) * 2 * mana / cost
    pause = FLAME_IDLE + (1 - FLAME_HIGH) / FLAME_DECAY
    return cast / (cast + pause)


def archon_effects(x: dict, wait: float, no_recovery: bool, flame: float = 0.0) -> dict[str, float]:
    """The Archon Buff's effects averaged over a fight, in percent. `flame`: Seething Body's Infernal Flame cycle in
    seconds (flame_uptime), which replaces the duration and the wait."""
    if flame > 0:
        uptime = flame_uptime(flame, x["ArchonRecovery"], no_recovery)[0]
    else:
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
    flame: float = 0.0  # Seething Body: the Infernal Flame cycle in seconds (flame_uptime); 0 for the others


def archon_sources(stats: dict, main_gems: set[str]) -> list[ArchonSource]:
    """Ways to gain Elemental Archon on a boss, fastest first."""
    mana, cost = number(stats.get("Mana")), number(stats.get("ManaPerSecondCost"))
    # Seething Body (MECHC4; RESEARCH12 18af: before, a 10 s Archon regained at once after the 20 s recovery, 33% at base,
    # an overcount for fast spenders): Pyromantic Pact, which Seething Body needs (tree links: research note), turns skill
    # costs into Infernal Flame up to 2 x maximum Mana (PoB reads neither node, so Mana and ManaPerSecondCost are the
    # build's own). 1 Flame per 1 Mana of cost (MEDIUM: "Gain Infernal Flame instead of spending Mana"). No cost: no
    # Flame, no Archon. Pausing near the top to hold High without the reset isn't counted (a play choice that costs casts).
    cycle = 2 * mana / cost if mana > 0 and cost > 0 else 0.0
    sources = [ArchonSource("Seething Body", "ArchonInfernal", math.inf if cycle <= 0 else 0.0,
                            (f"Infernal Flame (2 x {mana:,.0f} Mana) fills in {cycle:.1f}s at {cost:,.0f} Mana of costs "
                             "per second, then resets (a Fire hit of your maximum Life and Energy Shield each time); "
                             "Archon from the first cast on High (65%) after the recovery until the reset"
                             if cycle > 0 else "the build's skills cost no Mana, so no Infernal Flame is gained"),
                            flame=cycle if cycle > 0 else 0.0)]
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
        # for a gap of `delay` with none is (e^(rate x delay) - 1) / rate. The boss's misses by timing aren't counted
        # (MEDIUM). Damage over time its hits leave on you (Ignite, Corrupted Blood) lengthens the gap it needs (MECHC6,
        # RESEARCH21 #126: boss_hits.recharge_wait; before, hits only: an overcount against the Arbiter's Fire hits).
        delay = number(stats.get("EnergyShieldRechargeDelay")) or 4.0
        every = number(stats.get("enemySkillTime")) or 0.7
        land = boss_hits.land_share(stats) / every  # (red-flash hits always land, MECHC5)
        wait = boss_hits.recharge_wait(stats, delay, land)
        sources.append(ArchonSource("Archon of the Blizzard", "ArchonRecharge", wait,
                                    f"Energy Shield Recharge begins after {delay:g}s without damage; the boss lands "
                                    f"{land:.2f} hits/s (one per {every:g}s, LOW: PoB's placeholder; less evaded and "
                                    f"blocked), each restarting it: a {delay:g}s gap comes after {wait:.1f}s on average; "
                                    + boss_hits.recharge_text(stats, delay, land)))
    if "Arbiter's Ignition" in main_gems:
        crit = number(stats.get("CritChance")) / 100
        chance = (number(stats.get("IgniteChanceOnHit")) * (1 - crit) + number(stats.get("IgniteChanceOnCrit")) * crit) / 100
        ignites = hits_per_second(stats) * min(chance, 1.0)
        if ignites > 0:
            sources.append(ArchonSource("Arbiter's Ignition", None, 1 / ignites,
                                        f"the main skill ignites {ignites:.2f} times per second"))
    def rank(source: ArchonSource) -> float:  # (Seething Body by the wait that would give its uptime at base)
        if source.flame <= 0:
            return source.wait
        share = flame_uptime(source.flame, 0.0)[0]
        return ARCHON_DURATION / share - ARCHON_DURATION - ARCHON_RECOVERY if share > 0 else math.inf

    return sorted(sources, key=rank)


def archon_block(x0: dict, passive: dict, wait: float, no_recovery: bool, when: list, nonelemental: bool,
                 flame: float = 0.0) -> list[dict]:
    def effect(key: str) -> Callable[[dict], float]:
        return lambda x: archon_effects(x, wait, no_recovery, flame)[key]

    mods = more_lines("1% more Elemental Damage with Spells", effect("more"), x0, passive,
                      ("ArchonDuration", "ArchonRecovery", "ArchonEffect"), when)
    mods += increased_lines("1% increased Critical Hit Chance", effect("crit_chance"), x0, passive, ARCHON_INPUTS, when)
    mods += increased_lines("1% increased Critical Damage Bonus", effect("crit_damage"), x0, passive, ARCHON_INPUTS, when)
    if nonelemental:  # "No Non-Elemental Damage from Spells" while it lasts
        def lost(x: dict) -> float:
            return -100 * archon_effects(x, wait, no_recovery, flame)["uptime"]
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
                mods += archon_block(x0, passive, source.wait, no_recovery, when + state, nonelemental, source.flame)
    notes = []
    if current is not None:
        no_recovery = inputs.has("ArchonNoRecovery")
        e = archon_effects(x0, current.wait, no_recovery, current.flame)
        duration = ARCHON_DURATION * max(0.0, 1 + x0["ArchonDuration"] / 100)
        recovery = 0.0 if no_recovery else ARCHON_RECOVERY / max(1 + x0["ArchonRecovery"] / 100, 1e-9)
        regained = current.wait
        if current.flame > 0:
            _, duration, off = flame_uptime(current.flame, x0["ArchonRecovery"], no_recovery)
            regained = off - recovery
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
                     + f", regained {regained:.1f}s later ({current.name}: {current.basis}); "
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
# spell costs 10 Energy per 0.1 s of its base cast time, its "+N s to Total Cast Time" counted double (energy_seconds) (support stat
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
# 200), at the charges the equipped charms spend against a boss (flasks.charm_charges_used). Energy on kills (Reaper's
# Invocation) comes from the boss preset's killable adds (kill_rate; MECHB9), on minion deaths from minion_deaths. Barrier Invocation (poe2db): "When Hit by an Enemy, gain 1 Energy for each 10 Energy Shield lost to
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
    # Reaper's Invocation (MECHB9; before, in NO_ENERGY_ON_A_BOSS: "none on a single boss"): "While active, gains Energy
    # when you kill an enemy with a Melee Attack" (ActiveSkills reapers_invocation), 30 Energy per monster Power
    # (melee_kill_invocation_gain_X_centienergy_per_monster_power_on_melee_kill 3000; PoB's MetaReapersInvocationPlayer).
    # Against a lone boss the kill ends the fight; the King in the Mists' and Atziri's adds give some (kill_rate: the boss
    # preset's AddKillsPerSecond, MECH-D's). Your own minions are not enemies, so they never count (its text says "an
    # enemy"). An add's Power: 1 assumed (KeywordPopups Power: "An average monster has a Power of 1", x1 Normal; LOW).
    "Reaper's Invocation": {"per_level": 3.0, "gain": "melee kill", "per": 30.0, "invocation": True},
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


def energy_seconds(skill: Skill) -> float:
    """A skill's use time as Energy counts it (MECHB6, AUDIT17 A17-1): its base cast time plus twice its "+N seconds to
    Total Cast Time". The game's Energy definition (KeywordPopups Energy): "When calculating Energy gain or consumption
    from the use time of a Skill, modifiers to Total use time are treated as though they were double the value." Comet,
    Lightning Conduit, Skyfall and His Foul Emergence carry +1 s, Crushing Earth +1.5 s, Plasma Blast +2 s
    (total_cast_time_+_ms in their own stat sets, game files): Comet's base 1 s costs 300 Energy, not 100. Whether a
    skill's own +N s is such a modifier (300) or part of its base (200) has no game text beyond the definition: the
    definition's reading is taken (MEDIUM; Cast on Critical's tooltip with Comet in game settles it, held for the user)."""
    return (skill.cast_time or 0.0) + 2 * (skill.total_time_added or 0.0)


def energy_cost(skill: Skill) -> float:
    """Energy one trigger of `skill` costs: 10 per 0.1 s of its use time as Energy counts it (energy_seconds)."""
    return 100 * energy_seconds(skill)


def energy_time_note(skills: list[Skill]) -> str:
    added = [s for s in skills if s.total_time_added]
    return "".join(f"; {s.name}: {s.cast_time:g} s base + 2 x its {s.total_time_added:+g} s to Total Cast Time (the game's "
                   f"Energy definition counts Total use time modifiers double) = {energy_cost(s):.0f} Energy" for s in added)


INVOCATION_MAX_ENERGY = 500  # generic_ongoing_trigger_maximum_energy of every Invocation (game files, build 26476662)
DODGE_ROLL_METRES = 4.6  # dodge_roll_base_travel_distance 46 units (game files, build 26476662); distance mods not counted
DODGE_EVERY = 2.0  # seconds between dodge rolls against a boss: the user's default (7 Nov 2026), said in the guide
DODGE_ROLL_SECONDS = 0.75  # DodgeRollPlayer's cast time, 750 ms (game files); movement speed doesn't shorten it (RESEARCH 18w)
# The build's own roll (MECHB3, COVD4 / FIX_QUEUE "Dodge roll and movement speed"): flat distance lines add to
# dodge_roll_base_travel_distance (10 units a metre): "+1 metre to Dodge Roll distance" (Surefooted Sigil, Amanamu boots,
# time-influenced boots, Rune of Acrobatics +0.3), Lioneye's Glare's "+2 metres ... if you haven't Dodge Rolled Recently"
# and "-1 metre ... if you've Dodge Rolled Recently" (dodge_roll_travel_distance_+_if_(not_)dodge_rolled_recently; a
# build that rolls every DODGE_EVERY s has always rolled within RECENTLY's 4 s, so only the -1 m holds), Tactical Retreat's
# "+0.5 metres ... while Surrounded" (Surrounded: "at least 5 enemies within 3 metres", KeywordPopups; one unique,
# UniqueLessEnemiesToBeSurrounded1, "Require 2-4 fewer enemies": at 4 fewer one enemy is enough, so the line counts there
# while you stay within 3 m of the boss, a play condition). (checked: the game tables' Stats with dodge_roll and distance
# or speed in their ids; Mods, PassiveSkills, SoulCoreStats, GrantedEffectStatSets(PerLevel) and GrantedEffectQuality-
# Stats for them; PoB ModCache: none of these lines is applied, all leave text; the Sanctum-only map_player_ stat is left
# out, a Sanctum relic.) Distance scales with movement speed (community sources only, RESEARCH 18w: LOW). "X% faster
# Dodge Roll" (dodge_roll_speed_+%, one unique) shortens the roll's 0.75 s (assumed: time / (1 + X%), LOW); it doesn't
# change the distance. Cast on Dodge's alternate quality (cast_on_dodge_dodge_roll_distance_granted_+, +0.05 m a quality)
# isn't read: the builder sets no alternate qualities.
DODGE_DISTANCE = re.compile(r"^([+-]?\d+(?:\.\d+)?) metres? to Dodge Roll distance(?: (.+))?$", re.I)
DODGE_SPEED = re.compile(r"^(\d+(?:\.\d+)?)% (faster|slower) Dodge Roll$", re.I)
FEWER_TO_BE_SURROUNDED = re.compile(r"^Require (\d+) fewer enemies to be Surrounded$", re.I)
SURROUNDED_NEEDS = 5  # KeywordPopups Surrounded: "at least 5 enemies within 3 metres of you"


def dodge_roll(texts: list[str], stats: dict) -> dict:
    """The build's own dodge roll against a lone boss, rolling every DODGE_EVERY s: {"metres", "base_metres" (before
    movement speed), "seconds" (the roll's time), "basis"}."""
    flat, used, skipped = 0.0, [], []
    fewer = sum(float(m.group(1)) for t in texts if (m := FEWER_TO_BE_SURROUNDED.match(t.strip())))
    surrounded = SURROUNDED_NEEDS - fewer <= 1
    faster = 0.0
    for text in texts:
        text = text.strip()
        if m := DODGE_SPEED.match(text):
            faster += float(m.group(1)) * (1 if m.group(2).lower() == "faster" else -1)
            used.append(text)
            continue
        m = DODGE_DISTANCE.match(text)
        if not m:
            continue
        when = (m.group(2) or "").lower()
        if not when or ("dodge rolled recently" in when and "haven't" not in when and "not" not in when.split()):
            flat += float(m.group(1))
            used.append(text)
        elif "while surrounded" in when and surrounded:
            flat += float(m.group(1))
            used.append(f"{text} (one enemy is enough with \"Require {fewer:g} fewer enemies\": "
                                                    "within 3 m of the boss, a play condition)")
        else:
            skipped.append(text + (" (a lone boss doesn't Surround you: 5 enemies within 3 m)" if "surrounded" in when
                                   else " (you roll every %g s, so you have always rolled recently)" % DODGE_EVERY
                                   if "haven't" in when else " (condition not read)"))
    base = max(DODGE_ROLL_METRES + flat, 0.0)
    speed = number(stats.get("MovementSpeedMod")) or 1.0
    seconds = DODGE_ROLL_SECONDS / max(1 + faster / 100, 0.1)
    basis = (f"{base:g} m a roll ({DODGE_ROLL_METRES:g} m base"
             + (f"; {'; '.join(used)}" if used else "") + (f"; not counted: {'; '.join(skipped)}" if skipped else "")
             + f"), x{speed:g} movement speed = {base * speed:.2f} m (rolls scaling with movement speed: community sources "
               "only, LOW)" + (f"; each roll takes {seconds:.3g} s" if faster else ""))
    return {"metres": base * speed, "base_metres": base, "seconds": seconds, "basis": basis}
NO_ENERGY_ON_A_BOSS: dict[str, str] = {}  # (Reaper's Invocation left it in MECHB9: it reads the boss's add kills)
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
# after a short delay when killed. This delay is reset whenever another Reviving Minion dies": the delay T is each skill's
# own base (12 s on most summon skills) or 7.5 s, over the revival speed (revive_seconds, below; LOW). With N such minions
# and kills at r per second, the deaths run in waves: N killed in N / r s, then all revive T after the last, so the
# sustained rate is N r / (N + T r).
# The build's own sources counted (each at its real use rate):
#  - Sacrifice (its description: "your undead Reviving Minions can be used in place of Corpses by your skills"): a main skill
#    that consumes corpses (the corpse_model's skill types) uses one minion per use (one corpse per use assumed; that a
#    minion used as a corpse is Killed, reaching 0 life, is UNVERIFIED: LOW);
#  - Explosive Demise, Skeletal Arsonists' Command ("Detonate an allied Minion whose current Life is below a threshold"):
#    one minion per use, on its 3 s cooldown (PoB's minion.lua and the game files) x cooldown recovery, when the build has
#    another minion to detonate (that one is below the threshold when the Command comes up is assumed: LOW).
#  - Minion Pact I / II (AUDIT23 A23-2, MECHB11): its skill "remove[s] 100% (150%) of your maximum Life from a nearby
#    Minion on use" (support_minion_pact_user_life_%_removed_from_nearby_minion; game files SupportMinionPactPlayer /
#    PlayerTwo; PoB's sup_int.lua, which reads only its 30% / 35% more damage, with a minion in your Presence). Removing
#    Life is Sacrificing it ("Sacrifices you cause on others (such as Sacrificing Minion Life) can lead to their death",
#    KeywordPopups Sacrifice) and a minion brought to 0 Life by its summoner is Killed (KeywordPopups MinionDeath). Counted:
#    one minion Killed per ceil(its Life / Life removed) uses of the main skill it supports (PoB's Speed): pact_uses_per_kill
#    (AUDIT25 A25-2, MECHB12: before, one per use whatever the minion's Life, an overcount); the "without it" rate is in
#    the note.
# Not counted (labelled): Dark Pact ("Sacrifices 13% of Minion's life", skeletal_chains_aoe_%_health_dealt_as_chaos_damage
# 13 at level 20) and Abyssal Pact (20%, ..._as_physical_damage): PoB has no skill for them, so the build has no use rate
# to read (no source, so 0); whether "13% of life" is of maximum or current Life is UNVERIFIED (of current Life it never
# brings a minion to 0; of maximum, ~8 / 5 uses each). The boss killing your minions (their Life and defences against
# the boss preset's hits), Offerings (the skeleton becomes the spike: whether it's Killed is UNVERIFIED, not counted),
# minions taking damage for you, other on-minion-death lines.
RESUMMON_SECONDS = 7.5  # global_resummon_time_ms (PoB Misc.lua)
EXPLOSIVE_DEMISE_COOLDOWN = 3.0  # s (PoB minion.lua:815 levels; game files)
SACRIFICE = "Sacrifice"
ARSONIST = "Skeletal Arsonist Minion"
MINION_PACT_STAT = "support_minion_pact_user_life_%_removed_from_nearby_minion"


# How many uses of Minion Pact Kill one minion (AUDIT25 A25-2, MECHB12; an OVERCOUNT before: one per use whatever the
# minion's Life). Each use removes p% of YOUR maximum Life (stats "Life") from a nearby minion; it dies when the removals
# reach its maximum Life (PoB's env.minion Life, which the bridge's useRates reports per minion group: stats
# "_minion_lives", {skill: Life}, filled by Assumptions.models). So uses per kill = ceil(minion Life / removed). Taken: the
# build's lowest-Life Reviving minion (a player feeding Minion Pact keeps its weakest minions near; which minion the gem
# picks is UNVERIFIED), uses landing on the same minion until it dies, and no regeneration or recovery in between (an
# overcount where minions regenerate fast, small at Minion Pact's use rates; Energy Shield doesn't shield Life removal:
# UNVERIFIED, not added). No Life read for any minion: 0 Kills, with a note (CLAUDE.md: no flat guess).
def pact_uses_per_kill(reviving: list[Skill], pact: float, stats: dict) -> tuple[float, str]:
    lives = stats.get("_minion_lives") or {}
    found = {s.name: number(lives.get(s.name)) for s in reviving if number(lives.get(s.name)) > 0}
    removed = number(stats.get("Life")) * pact / 100
    if not found or removed <= 0:
        return 0.0, ("no minion's Life or your maximum Life was read, so no Kills are counted (0, a note: not a flat "
                     "guess)")
    name, life = min(found.items(), key=lambda kv: kv[1])
    per_kill = float(math.ceil(life / removed - 1e-9))
    return per_kill, (f"it removes {removed:,.0f} Life a use; your weakest Reviving minion ({name}) has {life:,.0f}, so "
                      f"one dies every {per_kill:g} use{'s' if per_kill != 1 else ''} (MEDIUM: the same minion until it "
                      "dies, no regeneration between uses; which minion the gem picks is UNVERIFIED)")


def own_uses(main: Skill | None, stats: dict) -> tuple[float, str]:
    """(uses per second, how) the main skill feeds minion_deaths' sources. For a corpse-using main skill, PoB's Speed is
    corpse_model's own triggerRate (the corpse supply), so reading it back would shrink Sacrifice's supply to 0 at every
    resolve (MECHB20, MECHC15's silent 0 on a real Detonate Dead + Sacrifice build): its base cast time instead, without
    your cast speed (an undercount, labelled)."""
    if main is not None and main.corpses and main.cast_time > 0:
        return 1.0 / main.cast_time, f"its base {main.cast_time:g} s cast, without cast speed (an undercount)"
    return number(stats.get("Speed")), "PoB's Speed"


def minion_deaths(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[float, str]:
    """Your Persistent, Reviving minions Killed per second against a boss, from the build's own sources (see above), capped
    by their revival (N r / (N + revive r: revive_seconds)), and how. 0 with a note when nothing in the build kills them."""
    names = {s.name for s in skills}
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    count = sum(number(s.summon.get("limit")) for s in reviving)
    if count <= 0:
        return 0.0, "the build has no Persistent minions to be Killed (temporary minions expiring aren't Killed)"
    kills, parts = 0.0, []
    if SACRIFICE in names and main is not None and main.corpses:
        undead = sum(number(s.summon.get("limit")) for s in reviving if s.summon.get("undead"))
        uses, source = own_uses(main, stats)
        if undead > 0 and uses > 0:
            kills += uses
            parts.append(f"Sacrifice: {main.name} uses one of your {undead:g} undead minions as its corpse each use "
                         f"({uses:.2f} uses/s, {source}; one corpse per use assumed; that it is Killed is UNVERIFIED, "
                         "LOW)")
    pact = number(main.support_stats.get(MINION_PACT_STAT)) if main is not None else 0.0
    if pact > 0 and number(stats.get("Speed")) > 0:
        uses = number(stats.get("Speed"))
        without = kills
        per_kill, how = pact_uses_per_kill(reviving, pact, stats)
        if per_kill > 0:
            kills += uses / per_kill
        parts.append(f"Minion Pact: each use of {main.name} removes {pact:g}% of your maximum Life from a nearby minion "
                     f"({uses:.2f} uses/s; KeywordPopups Sacrifice / MinionDeath: a minion its summoner brings to 0 Life is "
                     f"Killed): {how}. Without it: {without:.2f} deaths/s from the other sources before revival")
    arsonist = next((s for s in skills if s.name == ARSONIST), None)
    if arsonist is not None and count > number(arsonist.summon.get("limit")):
        every = EXPLOSIVE_DEMISE_COOLDOWN / (arsonist.cooldown_recovery or 1.0)
        kills += 1 / every
        parts.append(f"Explosive Demise (your Skeletal Arsonists' Command) detonates one of your other minions every "
                     f"{every:.2f} s (its {EXPLOSIVE_DEMISE_COOLDOWN:g} s cooldown; that one is below its Life threshold "
                     f"when the Command comes up is assumed, LOW)")
    not_counted = ("not counted yet: the boss killing your minions, minions taking damage for you, Offerings (an "
                   "undercount); Dark Pact / Abyssal Pact (13% / 20% of a minion's Life a use) have no skill in PoB, so no "
                   "use rate here")
    if kills <= 0:
        return 0.0, f"nothing in this build kills its minions ({'; '.join(parts + [not_counted])})"
    revive, how = revive_seconds(reviving)
    rate = count * kills / (count + revive * kills)
    defaults = [s.name for s in reviving if s.summon.get("limitFrom") == "default"]
    if defaults:  # (AUDIT29 A29-2: no count stat in the game files or PoB)
        how += (f"; {', '.join(defaults)}: one minion each (no count stat in the game files or PoB; UNVERIFIED, an "
                "undercount if a gem summons more)")
    return rate, (f"{rate:.3f} of your {count:g} Reviving minions Killed per second: " + "; ".join(parts)
                  + f"; each revives {revive:.3g} s after the last death (KeywordPopups Reviving: the delay "
                    f"resets on each death; {how}), so {count:g} x {kills:.2f} / ({count:g} + {revive:.3g} x {kills:.2f}); "
                  + not_counted)


# Revival time (MECHB2; FIX_QUEUE: Sacrifice makes revival 40% slower, AUDIT5). PoB works it out per Reviving minion skill
# (CalcOffence 1616: base / MinionRevivalSpeed): Sacrifice's harvester_minion_resummon_speed_+%_final (-40% at level 20, a
# MORE buff) and "Minions Revive N% faster" lines are in its MinionRevivalSpeed, which the bridge reports.
# The base (MECHB5, AUDIT15 A15-2 / A15-3): each skill's own minion_base_resummon_time_ms (12 s on 17 player summon skills:
# skeletons, spectres, companions...; PoB's BaseReviveTime override, CalcOffence 1616, which the bridge reports as
# reviveBase) where the skill carries it, else the game constant global_resummon_time_ms 7.5 s (PoB Misc.lua; the
# Infernal Hound and the three djinns carry no 12 s, A15-3). The sources disagree (the 0.2.0e notes: each later death
# adds less, capped at 7.5 s), so 7.5 s goes beside it in the note: LOW until in-game test #21 settles it. A longer
# revival means fewer deaths (Cast on Minion Death, Grim Feast): with 12 s, up to 1.6x fewer than at 7.5 s.
# (checked: the game's GrantedEffectStatSets carry it at 12000 on 17 player sets (8 here are monsters'); PoB's Data/Skills on
# 14 of them; the other three, DemonMagusPlayer, SoulCrystalPlayer and SummonMercenaryCompanionPlayer, aren't PoB skills.)
def revive_seconds(reviving: list[Skill]) -> tuple[float, str]:
    """Seconds a Reviving minion takes to revive: the count-weighted mean over the build's Reviving minion skills of their
    own base (reviveBase, else RESUMMON_SECONDS) over PoB's MinionRevivalSpeed, and how."""
    rows = [(number(s.summon.get("limit")), number(s.summon.get("reviveBase")) or RESUMMON_SECONDS,
             number(s.summon.get("reviveSpeed")) or 1.0) for s in reviving]
    total = sum(w for w, _, _ in rows)
    if total <= 0:
        rows, total = [(1.0, RESUMMON_SECONDS, 1.0)], 1.0
    seconds = sum(w * b / v for w, b, v in rows) / total
    constant = sum(w * RESUMMON_SECONDS / v for w, _, v in rows) / total
    base = sum(w * b for w, b, _ in rows) / total
    speed = base / seconds if seconds else 1.0
    if abs(base - RESUMMON_SECONDS) > 1e-6:
        bases = (f"base {base:.3g} s, the skills' own minion_base_resummon_time_ms (PoB's BaseReviveTime); "
                 f"{constant:.3g} s with the game constant {RESUMMON_SECONDS:g} s instead (LOW until an in-game test: "
                 "the 0.2.0e notes say a shorter, capped delay)")
    else:
        bases = (f"base {RESUMMON_SECONDS:g} s, the game constant (the skills carry no base of their own; LOW: the 0.2.0e "
                 "notes say each later death adds less)")
    if abs(speed - 1.0) < 1e-6:
        return seconds, bases
    return seconds, (f"{bases}; revival speed x{speed:.2f} (PoB's MinionRevivalSpeed: Sacrifice's 40% slower at level "
                     f"20, Revive-faster lines), {seconds:.3g} s")


# Fallen Skeletons and Death Storm (MECHB20, for MECH-A's Shocked Ground: MECH61 / MECH62, AUDIT26 A26-2). Death Storm
# is the Skeletal Storm Mages' Command (ActiveSkills death_storm: "Lightning bolts strike all fallen Skeletons in the area,
# creating [ShockedGround|Shocked Ground] around them"; GrantedEffects DeathStormSkeletonStormMageMinion: cast time 2 s,
# GrantedEffectsPerLevel Cooldown 5000 ms; its stat set: is_commandable_skill, area radius 20 and secondary radius 20
# (2 m), base_skill_effect_duration 6000; checked: game files; second source research/shocked-ground-sources.md, AUDIT26
# A26-2 confirmed these numbers). A "fallen" Skeleton is taken to be a Reviving Skeleton lying dead while it waits to
# revive (the text doesn't define it: UNVERIFIED, LOW; a Temporary Skeleton expiring isn't counted).
# Which minions are Skeletons: the skill type CreatesSkeletonMinion (ActiveSkillType 125; PoB SkillType 126, the bridge's
# summon.skeleton), else "Skelet" in the skill's name (the game's ten: Summon Skeletons, Revive Skeleton and the eight
# Skeletal X Minion skills; checked: ActiveSkills DisplayedName, ActiveSkillType).
# The rate: models.minion_deaths' Reviving minions Killed per second, from the build's own sources (Sacrifice, Minion Pact,
# Explosive Demise; no source: 0 with a note, CLAUDE.md), shared over the Reviving minions by count (the Skeletons' share:
# deaths taken as spread evenly, LOW; Sacrifice uses only undead minions, so it's a little more for Skeletons). Each lies
# fallen for the revival time (revive_seconds), so the mean number fallen at a moment is rate x revive (Little's law;
# with deaths at random times the number is Poisson, so a press finds at least one with 1 - e^-mean; capped by the
# Skeletons there are). Not counted (labelled): the boss killing your Skeletons (an undercount), where the Skeletons fall
# (melee ones at the boss, ranged ones and Minion Pact's "nearby" minions further off: MECH-A's ground reads the area),
# and a player timing the press just after a kill (more fallen than the mean).
SKELETON_NAME = "Skelet"
STORM_MAGE = "Skeletal Storm Mage Minion"
DEATH_STORM = "Death Storm"
DEATH_STORM_COOLDOWN = 5.0  # s (GrantedEffectsPerLevel DeathStormSkeletonStormMageMinion Cooldown 5000)
DEATH_STORM_SECONDS = 6.0  # its Shocked Ground's duration (base_skill_effect_duration 6000)
DEATH_STORM_RADIUS = 2.0  # m (active_skill_base_secondary_area_of_effect_radius 20)
DEATH_STORM_CAST = 2.0  # s (GrantedEffects CastTime 2000: the Storm Mage's own cast, a floor under the cooldown)


def is_skeleton(skill: Skill) -> bool:
    """Whether a minion skill's minions are Skeletons (the bridge's summon.skeleton, else the name)."""
    summon = skill.summon or {}
    if "skeleton" in summon:
        return bool(summon["skeleton"])
    return SKELETON_NAME in skill.name


def fallen_skeletons(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[float, float, str]:
    """(mean Reviving Skeletons lying fallen at a moment, Skeletons Killed per second, how), from minion_deaths' rate."""
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    skeletons = [s for s in reviving if is_skeleton(s)]
    count = sum(number(s.summon.get("limit")) for s in reviving)
    bones = sum(number(s.summon.get("limit")) for s in skeletons)
    if bones <= 0:
        return 0.0, 0.0, "the build has no Reviving Skeletons to fall (0)"
    killed, how = minion_deaths(skills, main, stats)
    if killed <= 0:
        return 0.0, 0.0, f"no Skeleton falls: {how}"
    revive, _ = revive_seconds(reviving)
    rate = killed * bones / count
    fallen = min(rate * revive, bones)
    return fallen, rate, (f"{rate:.3f} of your {bones:g} Reviving Skeletons Killed per second ({bones:g} of {count:g} "
                          f"Reviving minions: deaths spread evenly, LOW), each fallen {revive:.3g} s until it revives, "
                          f"so {fallen:.2f} fallen at a moment on average (deaths: {how})")


def death_storm(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[float, float, float, str]:
    """Death Storm, the Skeletal Storm Mages' Command: (uses per second, mean fallen Skeletons struck a use, the share of
    uses that find at least one, how). Each struck Skeleton makes DEATH_STORM_RADIUS m of Shocked Ground for
    DEATH_STORM_SECONDS s (MECH-A's shocked_ground places it). 0 with a note without Storm Mages or fallen Skeletons."""
    mage = next((s for s in skills if s.name == STORM_MAGE), None)
    if mage is None:
        return 0.0, 0.0, 0.0, "no Skeletal Storm Mages, so no Death Storm"
    every = max(DEATH_STORM_COOLDOWN / (mage.cooldown_recovery or 1.0), DEATH_STORM_CAST)
    fallen, rate, how = fallen_skeletons(skills, main, stats)
    if fallen <= 0:
        return 0.0, 0.0, 0.0, f"Death Storm strikes fallen Skeletons, and none fall: {how} (0, not a flat guess)"
    mages = number(mage.summon.get("limit")) if mage.summon else 0.0
    bones = sum(number(s.summon.get("limit")) for s in skills if s.summon and s.summon.get("persistent")
                and not number(s.summon.get("duration")) and is_skeleton(s))
    # a Command needs a living Storm Mage: all of them fallen at once, taken as independent, is (fallen / Skeletons)^mages
    down = (fallen / bones) ** mages if bones > 0 and mages > 0 else 0.0
    uses = (1.0 - down) / every if every > 0 else 0.0
    some = 1.0 - math.exp(-fallen)
    return uses, fallen, some, (
        f"Death Storm (a Command: one press every {every:.3g} s, its {DEATH_STORM_COOLDOWN:g} s cooldown over your "
        f"Cooldown Recovery; a press under the button cap) strikes each fallen Skeleton in its 2 m area: {fallen:.2f} on "
        f"average, at least one on {some:.0%} of presses (deaths at random times: Poisson; a press timed just after a "
        f"kill finds more); {down:.1%} of presses lost to every Storm Mage being fallen; {how}. Not counted: the boss "
        "killing your Skeletons (an undercount), where they fall (MECH-A's ground reads the area, LOW)")


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


# Ancestral Bond (MECHB4; AUDIT13 A13-1, RESEARCH18 #64 / 18ah, research/ancestral-bond*.md): "Your Totem Limit is
# doubled", "No Charge requirement for placing Totems", "Totems reserve 75 Spirit each" (passive_skill_stat_descriptions
# keystone_ancestral_bond; poe2db Ancestral_Bond). With it a Spell Totem needs no charges (MEDIUM-HIGH: the game text and
# three 0.5 sources); whether charges you hold are still consumed is UNVERIFIED, so none are counted (no per-charge
# bonus: the lower bound). PoB reserves the 75 Spirit once per totem skill, not per totem (CalcDefence getActiveSkillCount),
# so each totem past the first must fit in the Spirit PoB leaves unreserved: the totems counted are capped by it, which
# charges the reservation the damage relies on.
ANCESTRAL_BOND_FREE = re.compile(r"^No Charge requirement for placing Totems$", re.I)
ANCESTRAL_BOND_DOUBLE = re.compile(r"^Your Totem Limit is doubled$", re.I)
ANCESTRAL_BOND_SPIRIT = 75.0


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
    bond = any(ANCESTRAL_BOND_FREE.match(t.strip()) for t in texts)
    doubled = any(ANCESTRAL_BOND_DOUBLE.match(t.strip()) for t in texts)
    if doubled:
        allowed *= 2
    if any(number(s.support_stats.get(TOTEM_ONE_STAT)) > 0 for s in skills if s.group == main.group):
        allowed = 1  # (a one-totem support: "Supported Skills can only have one active Totem"; A24-3, MECHB11)
    duration = (number(stats.get("TotemDuration")) or SPELL_TOTEM_DURATION) * (
        1 +SPELL_TOTEM_DURATION_PER_ENDURANCE * SPELL_TOTEM_CHARGES * (1 - power))
    up = min(float(allowed), made * duration / SPELL_TOTEM_CHARGES)
    bond_note = ""
    if bond:  # (no charges needed: the build keeps its totems placed; none consumed, UNVERIFIED)
        power, made = 0.0, 0.0
        spirit = number(stats.get("SpiritUnreserved"))
        fits = 1 + max(0, math.floor(spirit / ANCESTRAL_BOND_SPIRIT + 1e-9)) if "SpiritUnreserved" in stats else allowed
        up = float(min(allowed, fits))
        bond_note = (f"; Ancestral Bond: \"No Charge requirement for placing Totems\", so {up:g} of {allowed} allowed totems "
                     f"kept placed (re-placed each {duration:.1f} s; MEDIUM-HIGH, research #64)"
                     + (f", held to what the Spirit left after PoB's one 75 reservation pays for at 75 each ({spirit:,.0f} "
                        "free; PoB reserves once per totem skill, the game per totem)" if fits < allowed else "")
                     + ". Charges you hold aren't counted as consumed (UNVERIFIED: no Power Charge bonus, no longer duration)")
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
             f"{allowed} totems allowed at gem level {level}" + (" doubled by Ancestral Bond" if doubled else "")
             + f" (game files; PoB fixes 2): {up:.2f} up on average, and PoB counts one" + bond_note)
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
# Eternal Mark (SupportEternalMarkPlayer, marks_avoid_consumption_when_first_activated): "Marks are not Consumed the first
# time they Activate", so an application can Activate twice (MECH62, AUDIT27 A27-1)
ETERNAL_MARK = "Eternal Mark"
ETERNAL_MARK_STAT = "marks_avoid_consumption_when_first_activated"  # (bridge activeSkills supportStats; MECH63)
# Murderous Intent (SupportEmpoweredCullPlayer): "Skills Empowered by Supported Skills have Culling Strike" (MECH63;
# Assumptions.empowered_uses, conditions.cull_window_scale's empowered)
MURDEROUS_INTENT = "Murderous Intent"


def mark_duration(mark: "Skill") -> float:
    """(MECH62, AUDIT27 A27-2) The Mark's duration on the boss: PoB's base_skill_effect_duration (8 s on every Mark gem)
    with the build's Mark / skill effect duration modifiers (bridge activeSkills markDuration), else MARK_DURATION."""
    return number(getattr(mark, "mark_duration", 0.0)) or MARK_DURATION
MARK_BUFF_DURATION = 10.0
# Predator's Mark (MECH34): Pounce triggers it on the highest-Rarity enemy hit when no Mark gem is socketed in Pounce
# (ActiveSkills wolf_pounce). Game files (build 26476662, GrantedEffectStatSets WolfPounceMarkPlayer): 8 s, "Marked
# target takes 3% increased damage for each of its enemies within 6 metres of it, up to 45%"
# (skill_wolf_mark_damage_taken_+%_per_nearby_enemy 3, _cap 45). It's consumed when the target dies (triggering Summon
# Wolf; PoB gives Summon Wolf a limit of 0) or when Cross Slash hits it with both gouges (ActiveSkills
# wolf_cross_slash: "Hitting a Marked enemy with both gouges will Activate the Mark"; Summon Wolf "is Triggered when
# Predator's Mark Activates", whatever activates it, so Cross Slash on a Marked boss may summon wolves: A22-3, UNVERIFIED,
# held for the user's in-game test). Pounce has a weapon requirement (WeaponRequirements; PoB: Talisman). PoB (act_str.lua
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
# 4 at levels 1-6, 5 at 7-13, 6 at 14-21, 7 at 22-30, 8 at 31-40). On a lone boss none are summoned by a death; Cross
# Slash's two gouges Activating the Mark on the boss may summon them (A22-3: the game text above; UNVERIFIED, held for the
# user's in-game test; the stat's own name, skill_cross_slash_triggered_summon_+_wolves_if_unique, points that way), so
# the user's choice
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
# files give a partial Break no duration). Pin: read by the boss's Pinned uptime (MECH66, A12-1: immobilise_duration's
# pin_duration_scale; conditionEnemyPinned is set from the main skill's Pin buildup since MECH46).
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


def roar_rage(skill: Skill, power: float = BOSS_POWER) -> float:
    per_power = number(skill.level_stats.get("bear_warcry_gain_X_centirage_per_enemy_power")) / 100 or 2.0
    return per_power * power


# Inherent Rage loss's modifiers (MECHC5; RESEARCH17 #89, research/rage-loss-modifiers.md; AUDIT11 A11-2). The game's
# KeywordPopups "Rage": "lose 1 Rage every 0.2 seconds. Rage loss is paused for 4 seconds upon gaining Rage, or after
# taking damage". The stats that change it (checked by RESEARCH17: every .csd for Rage with loss / lose / decay / later /
# slower / faster, their uses over every stat set, passive and mod, the 0.5 tree, PoB's ModCache; poe2db / outof.games for
# the Berserker suffix): rage_loss_delay_ms_+ "Inherent Rage loss starts N second(s) later" (five passives, the "of the
# Berserker" suffix), rage_loss_delay_recovery_rate_+% "N% faster / slower start of inherent Rage loss" (Anger Management
# +200%: the pause counts down 3x as fast, (4 + later) / 3 s: MEDIUM, PoE's rule for recovery-rate stats; which applies
# first is UNVERIFIED), rage_decay_speed_+% "Inherent loss of Rage is N% slower / faster" (summed, as PoB's
# InherentRageLoss INC). "2% slower per 10 Tribute" (a Timeless-jewel passive) needs the build's Tribute, which nothing
# here reads: not counted (an undercount, named in the note).
RAGE_LATER = re.compile(r"^Inherent Rage loss starts (\d+(?:\.\d+)?|1) seconds? later$", re.I)
RAGE_START_RATE = re.compile(r"^(\d+(?:\.\d+)?)% (faster|slower) start of inherent Rage loss$", re.I)
RAGE_DECAY_RATE = re.compile(r"^Inherent loss of Rage is (\d+(?:\.\d+)?)% (slower|faster)$", re.I)
RAGE_TRIBUTE = re.compile(r"^Inherent loss of Rage is \d+% slower per \d+ Tribute$", re.I)


def rage_loss(texts: list[str]) -> tuple[float, float, str]:
    """(seconds the loss waits after a gain or a hit taken, Rage lost per second after it, how) from the build's lines."""
    later = rate = decay = 0.0
    how = []
    for text in texts:
        t = text.strip()
        if m := RAGE_LATER.search(t):
            later += float(m.group(1))
        elif m := RAGE_START_RATE.search(t):
            rate += float(m.group(1)) * (1 if m.group(2).lower() == "faster" else -1)
        elif m := RAGE_DECAY_RATE.search(t):
            decay += float(m.group(1)) * (-1 if m.group(2).lower() == "slower" else 1)
        else:
            continue
        how.append(t)
    hold = (RAGE_HOLD + later) / max(1 + rate / 100, 0.01)
    loss = RAGE_LOSS * max(1 + decay / 100, 0.0)
    if any(RAGE_TRIBUTE.search(t.strip()) for t in texts):
        how.append("per-Tribute slower loss not counted (Tribute isn't read)")
    return hold, loss, "; ".join(how)


def rage_average(gain: float, maximum: float, period: float, hold: float = RAGE_HOLD, loss: float = RAGE_LOSS) -> float:
    """Average Rage over one roar to the next: full (capped) for `hold` s, then `loss` per second down to 0."""
    top = min(gain, maximum)
    if period <= 0 or top <= 0:
        return 0.0
    decay = min(max(period - hold, 0.0), top / loss) if loss > 0 else max(period - hold, 0.0)
    held = min(period, hold)
    return (top * held + top * decay - loss * decay * decay / 2) / period


def roar_empowered(power: float = BOSS_POWER) -> int:
    return max(1, int(min(power, ROAR_EMPOWER_POWER_CAP) // ROAR_EMPOWER_PER_POWER))


def roar_break(skill: Skill, enemy_armour: float, duration: float = FULL_BREAK_DURATION,
               power: float = BOSS_POWER) -> tuple[float, str]:
    """Share of the fight the boss's Armour is Fully Broken by the roar's Empowered hits, and its basis. `power`: the
    boss's Power (boss_power: 20, or more with the build's Power lines)."""
    per_hit = number(skill.level_stats.get("warcry_grant_break_X_armour_on_hit"))
    hits, period = roar_empowered(power), roar_period(skill)
    broken = per_hit * hits
    basis = (f"{FEROCIOUS_ROAR} Empowers {hits} Attacks per use (one per {ROAR_EMPOWER_PER_POWER} of the boss's "
             f"{power:g} Power), each Breaking {per_hit:,.0f} Armour: {broken:,.0f} against the boss's {enemy_armour:,.0f}")
    if per_hit <= 0 or broken < enemy_armour:
        return 0.0, basis + ": not Fully Broken"
    return min(1.0, duration / period), (
        basis + f": Fully Broken for {duration:g} s, once per {period:.1f} s (the main skill's next hits after "
        "each roar assumed to land)")


# Corpses ---------------------------------------------------------------------------------------
# A skill the game types TargetsDestructibleCorpses, TargetsDestructibleRareCorpses or CanTargetUnusableCorpse (game files,
# build 26476662: Detonate Dead, Volatile Dead, Bitter Dead, Decompose, Profane Ritual, Feast of Flesh, Devour, Kelari's
# Malediction, Ritual Sacrifice, Unearth, Raise Zombie ...) needs a corpse each use. Nothing dies on a single boss, so
# only the build's own source supplies them there; the boss presets with killable adds supply theirs (corpse_supply,
# AUDIT24 A24-1, MECHB11). The 0.5 game data has no player skill that creates corpses (Desecrate is a
# monster skill only; the Bone Corpses and Corpse Walk lines grant skills the files no longer have). The one source left
# is Sacrifice (undead Reviving Minions used in place of corpses; checked: AUDIT27 A27-15, Stats ids holding corpse with
# creat / spawn / leave, their Mods carriers, ActiveSkills texts); since MECHB19 (AUDIT27 A27-4) it supplies the build's
# own undead Reviving minion deaths (sacrifice_corpses, below; LOW).
CORPSE_TYPES = {"TargetsDestructibleCorpses", "TargetsDestructibleRareCorpses", "CanTargetUnusableCorpse"}
CORPSE_SOURCES = {"Sacrifice": "your undead Reviving Minions in place of corpses, at the rate the build's own sources kill "
                               "them, capped by their revival (models.sacrifice_corpses, LOW)"}
# Skills of those types that still work without a corpse: never set to 0, the part that needs corpses said in the guide
CORPSE_OPTIONAL = {
    "Raise Zombie": "consumes a Power Charge when there's no corpse; zombies from corpses not counted",
    "Unearth": "its bone spikes hit without corpses; its Bone Constructs only from the corpses and dead Reviving minions "
               "the build supplies (unearth_constructs)",
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


# The boss preset's adds (AUDIT24 A24-1, MECHB11): two presets have killable adds (MECH-D's bosses.py: the King in the
# Mists' Affliction Totems / Mystic Fetishes, Atziri's Royal Acolytes), published as ADD_KILLS_STAT. Each add the build
# kills is counted as one corpse: an assumption, LOW. Whether those adds leave a usable corpse is UNVERIFIED: the
# King's are totems (KeywordPopups has no rule on monster totems' corpses), Atziri's acolytes explode when they reach you
# (one killed before that likely leaves one); MECH-D / RESEARCH to settle it per preset. Against a lone boss: none (the
# kill ends the fight). The "without it" figure, 0, is in the note. Corpse users share the supply (corpse_model splits it
# evenly; Profanity's share isn't split: corpse_share's note).
def corpse_supply(present: set[str], stats: dict | None = None) -> float:
    """Corpses per second on a boss: the build's own sources (Sacrifice's undead Reviving minions, stats
    SACRIFICE_CORPSES_STAT from sacrifice_corpses, when Sacrifice is present) plus the boss preset's killed adds (one
    corpse each, LOW; see above)."""
    own = number((sacrifice_rate(stats))) if SACRIFICE in present else 0.0
    return max(number((stats or {}).get(ADD_KILLS_STAT)), 0.0) + max(own, 0.0)


# Sacrifice's corpses (MECHB19; AUDIT27 A27-4, for MECH-C's "recover when you Consume a corpse" and every corpse user).
# The game (ActiveSkills sacrifice; sacrifice.csd): "While active, your undead [Reviving] [Minion|Minions] can be used in
# place of [Corpse|Corpses] by your skills, but your [Minion|Minions] [Reviving|Revive] more slowly." The text doesn't say
# whether the minion used is alive or dead (Unearth's says "dead [Reviving] [Minion|Minions]"): both readings give one
# corpse per undead Reviving minion death, so the supply is the build's own undead Reviving minion deaths
# (minion_deaths, the user's no-flat-rates rule: the corpse skill's own uses with Sacrifice, Minion Pact, Explosive
# Demise; each capped by revival, which Sacrifice slows 40% at level 20), times the undead share of the build's Reviving
# minions (deaths spread evenly, assumed). LOW: that a Minion Pact or Explosive Demise death leaves a usable corpse
# (reading "dead"), and one corpse per death. No source kills them: 0, with the note (no flat guess). Not counted
# (labelled): the minions' damage lost while they're dead (an OVERCOUNT where a build relies on both their damage and
# their corpses), the boss killing them (an undercount).
SACRIFICE_CORPSES_STAT = "_sacrifice_corpses"  # stats key: (corpses per second, how), set by Assumptions.models


def sacrifice_rate(stats: dict | None) -> float:
    value = (stats or {}).get(SACRIFICE_CORPSES_STAT)
    return number(value[0] if isinstance(value, (tuple, list)) else value)


def sacrifice_corpses(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[float, str]:
    """Corpses per second Sacrifice supplies: the build's undead Reviving minions Killed per second (see above), and how."""
    if SACRIFICE not in {s.name for s in skills}:
        return 0.0, "no Sacrifice in the build"
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    total = sum(number(s.summon.get("limit")) for s in reviving)
    undead = sum(number(s.summon.get("limit")) for s in reviving if s.summon.get("undead"))
    if undead <= 0:
        return 0.0, "Sacrifice: the build has no undead Reviving minions to use in place of corpses (0)"
    deaths, how = minion_deaths(skills, main, stats)
    if deaths <= 0:
        return 0.0, f"Sacrifice: none of your undead Reviving minions die, so none is a corpse (0, no flat rate: {how})"
    rate = deaths * undead / total
    return rate, (f"Sacrifice: {rate:.3f} corpses/s, one per undead Reviving minion death ({undead:g} of your {total:g} "
                  f"Reviving minions are undead; deaths spread evenly, assumed): {how}. LOW: one corpse per death, and "
                  "that a minion killed another way (Minion Pact, Explosive Demise) can be used dead (Sacrifice's text "
                  "doesn't say alive or dead); their damage while dead isn't taken off. Without Sacrifice: 0")


def add_corpses_note(stats: dict | None) -> str:
    adds = max(number((stats or {}).get(ADD_KILLS_STAT)), 0.0)
    if adds <= 0:
        return "nothing dies on a single boss (no killable adds in this boss preset)"
    return (f"{adds:.3f} corpses/s from the boss's adds the build kills ({(stats or {}).get('_boss') or 'the preset'}), "
            "one corpse each: assumed, LOW (UNVERIFIED whether those adds, the King's totems / Atziri's acolytes, leave "
            "usable corpses); without them 0")


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


# Unearth's Bone Constructs (MECHB21; AUDIT28 A28-5, an UNDERCOUNT, and an OVERCOUNT found probing it). The game
# (ActiveSkills unearth): "The bones of Corpses and dead Reviving Minions in the area are ripped out and reassembled into
# short-lived Bone Construct Minions that fight for you. Larger Corpses create more than one Bone Construct." PoB scores
# the skill as its spikes and ONE Bone Construct fighting nonstop (probe POE2T1081, a real Unearth Infernalist: spikes
# 45 DPS, Minion.CombinedDPS 38 = one construct's AverageDamage x Speed, limit 20, whatever the corpses), and the score
# takes the larger (objective.main_dps): a construct a lone boss never gives counted (an OVERCOUNT once minion damage
# beats the spikes), and never more than one where corpses come (an UNDERCOUNT). The constructs alive on average are
# Unearth's corpses per second x each construct's duration (PoB's, the bridge's summon.duration), capped at the limit
# (ActiveUnearthBoneConstructLimit): one construct per corpse (larger corpses' extra ones not counted, an undercount;
# a Reviving minion's body counted as one, LOW), and Unearth cast often enough to raise each (LOW).
# Its corpses: an even share of the common supply (the boss's adds, Sacrifice's undead minions; every corpse user,
# Unearth too, splits it: corpse_model) plus the build's dead Reviving minions only Unearth can use (any Reviving
# minion, undead or not, with or without Sacrifice: minion_deaths, less Sacrifice's share of them, which is in the
# common supply already: so the larger of the two, never their sum). Each lies dead until it revives, so it's there to
# be used once (assumed: whether Unearth taking its bones changes its revival is UNVERIFIED).
# (checked: ActiveSkills unearth's text; PoB act_int.lua UnearthPlayer / Minions.lua UnearthBoneConstruct and the probe.)
UNEARTH = "Unearth"
REVIVING_DEATHS_STAT = "_reviving_deaths"  # stats key: minion_deaths' (rate, how), set by Assumptions.models for Unearth


def unearth_constructs(unearth: Skill, corpses: float, how: str) -> tuple[list[dict], dict]:
    """(mods, note) for Unearth's Bone Constructs from `corpses` per second (see above): a "Minions deal more Damage"
    line on Unearth bringing PoB's one construct to the count alive on average (0 with no corpses)."""
    summon = unearth.summon or {}
    duration, limit = number(summon.get("duration")), number(summon.get("limit"))
    if limit <= 0:
        return [], {}
    count = min(limit, max(corpses, 0.0) * duration)
    basis = (f"{corpses:.3g} corpses/s for Unearth ({how}) x {duration:.3g} s each construct lasts"
             + (f", capped at its limit of {limit:g}" if count >= limit else "")
             + f": {count:.3g} alive on average (PoB counts one fighting nonstop, whatever the corpses). One construct a "
               "corpse (larger corpses make more: not counted, an undercount); a dead Reviving minion's body counted "
               "as one, and Unearth cast often enough to raise each (LOW). Without corpses: its spikes only")
    return ([line("Minions deal 1% more Damage", 100 * (count - 1), skill=unearth.name)],
            {"label": "Unearth's Bone Constructs alive on average*", "value": round(count, 3), "basis": basis,
             "confidence": "LOW" if count > 0 else "datamined"})


def reviving_extra(stats: dict | None) -> tuple[float, str]:
    """Dead Reviving minions per second only Unearth can use: minion_deaths' rate (REVIVING_DEATHS_STAT) less Sacrifice's
    share, which the common supply holds already (so the larger of the two counts, never their sum)."""
    value = (stats or {}).get(REVIVING_DEATHS_STAT)
    deaths = number(value[0] if isinstance(value, (tuple, list)) else value)
    how = value[1] if isinstance(value, (tuple, list)) and len(value) > 1 else "the minion deaths weren't read"
    extra = max(deaths - max(sacrifice_rate(stats), 0.0), 0.0)
    return extra, (f"{deaths:.3g} of your Reviving minions die a second ({how})"
                   + (f", {extra:.3g} of them beyond Sacrifice's" if sacrifice_rate(stats) > 0 else ""))


def corpse_model(skills: list[Skill], mods: list[dict], stats: dict | None = None,
                 main: Skill | None = None) -> tuple[list[dict], list[dict]]:
    """Corpses per second for each skill that needs them, from the build's own sources (CORPSE_SOURCES) and the boss
    preset's killed adds (corpse_supply): a skill with no supply uses 0 per second, wherever it is (main skill, meta
    group, totem), replacing any trigger rate the other models gave it; several users split the supply evenly. Returns
    the new mods list and the notes."""
    users = sorted({s.name for s in skills if s.corpses and not s.wrong_weapon} - set(CORPSE_OPTIONAL))
    optional = sorted({s.name for s in skills if s.corpses} & set(CORPSE_OPTIONAL))
    present = {s.name for s in skills}
    supply = corpse_supply(present, stats)
    unearth = next((s for s in skills if s.name == UNEARTH and s.corpses), None)
    sharers = len(users) + (1 if unearth is not None else 0)  # (Unearth takes the corpses in its area too: MECHB21)
    notes = [{"label": f"{name}: corpses", "value": 0, "basis": CORPSE_OPTIONAL[name] + " (no corpses on a single boss)",
              "confidence": "datamined"} for name in optional if name != UNEARTH]
    if unearth is not None:
        extra, extra_how = reviving_extra(stats)
        own = supply / sharers + extra
        how = (f"{supply / sharers:.3g}/s, its share of {supply:.3g} corpses/s split among {sharers} corpse "
               f"user{'s' if sharers != 1 else ''} ({add_corpses_note(stats)}"
               + (f"; {sacrifice_note(stats)}" if SACRIFICE in present else "") + f"), + {extra:.3g}/s dead Reviving "
               f"minions ({extra_how})")
        more, note = unearth_constructs(unearth, own, how)
        mods = mods + more
        notes.append(note or {"label": "Unearth: corpses", "value": round(own, 4), "basis": CORPSE_OPTIONAL[UNEARTH]
                              + f": {how}", "confidence": "LOW" if own > 0 else "datamined"})
    if not users:
        return mods, notes
    sources = [f"{name}: {why}" for name, why in CORPSE_SOURCES.items() if name in present]
    dropped = set(users)
    mods = [m for m in mods if not (m.get("kind") == "SkillData" and m.get("skill") in dropped
                                    and m.get("key") in ("triggerRate", "triggered"))]
    each = supply / sharers
    speed = number((stats or {}).get("Speed"))
    for name in users:  # (a main skill is never used faster than PoB's own Speed for it: MECHB19)
        main_user = main is not None and main.name == name and speed > 0
        mods += trigger_rate(name, min(each, speed) if main_user else each)
    notes.append({
        "label": f"Corpses per second for {', '.join(users)}", "value": round(supply, 4),
        "basis": "they need a corpse each use; " + add_corpses_note(stats) + "; "
                 + (f"the build's own: {'; '.join(sources)}"
                    + (f" ({sacrifice_note(stats)})" if SACRIFICE in present else "") if sources
                    else "the build has no corpse source of its own")
                 + ("; Unearth takes an even share of them too" if unearth is not None and supply > 0 else "")
                 + (f", so they're counted {each:.3g} uses per second each" if supply > 0
                    else ", so they're counted 0 uses per second"),
        "confidence": "LOW (the adds' / Sacrifice's corpses assumed)" if supply > 0
                      else "datamined" if not sources else "theory"})
    return mods, notes


def sacrifice_note(stats: dict | None) -> str:
    value = (stats or {}).get(SACRIFICE_CORPSES_STAT)
    return value[1] if isinstance(value, (tuple, list)) else "Sacrifice's rate wasn't read"



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
        power = power_of(stats)
        gain = roar_rage(skill, power)
        hold, loss, how = rage_loss(stats.get("_texts") or [])
        rage = rage_average(gain, number(stats.get("MaximumRage")) or RAGE_MAXIMUM, period, hold, loss)
        mods.append(line("1% more Attack Damage", rage))
        notes.append({"label": f"{FEROCIOUS_ROAR}: average Rage", "value": round(rage, 1), "confidence": "theory",
                      "basis": f"{gain:g} Rage per roar ({power:g} Power x {gain / power:g}), capped at "
                               f"{number(stats.get('MaximumRage')) or RAGE_MAXIMUM:g}, used every {period:.1f} s; held "
                               f"{hold:.2g} s, then {loss:.2g} lost per second (game files: 4 s and 5/s"
                               + (f"; the build's {how}" if how else "") + "); 1% more Attack damage each. "
                               "Loss paused by the boss's hits isn't counted (an undercount); Rage-per lines of the build "
                               "aren't fed"})
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


# MECHB2: KeywordPopups Repeat (game files 26476662): "Triggered skills, Instant skills, and Channelling skills cannot
# Repeat". A main spell that can't Repeat gets no Repeat (the ledger condition RepeatableSpell is off for it: COV6,
# 5f3ae4f, the same rule) and so loses no Repeat-Echo: this correction must not apply there either.
NO_REPEAT_TYPES = frozenset({"Instant", "Channel"})


def can_repeat(main: "Skill | None", main_types: set[str]) -> bool:
    """The main skill is a spell that can Repeat: Spell + Multicastable (PoB's name for Repeatable), not Instant or
    Channelling, and not Triggered in PoB's calculation (a meta gem or trigger support adds Triggered)."""
    return ({"Spell", "Multicastable"} <= main_types and not NO_REPEAT_TYPES & main_types
            and not (main is not None and main.triggered))


def repeat_echo_model(texts: list[str], main_types: set[str], forbidden: set[str],
                      main: "Skill | None" = None) -> tuple[list[dict], list[dict]]:
    if (not {"Cascadable"} <= main_types or not can_repeat(main, main_types)
            or set(REPEAT_ECHO_ENTRIES) & forbidden):
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
        # (MECH63, AUDIT27 A27-1: an UNDERCOUNT before) Eternal Mark in the Mark's group: "Marks are not Consumed the
        # first time they Activate", so the event that consumes it (a crit, an Electrocute / Freeze) must come twice
        uses = 2 if number(mark.support_stats.get(ETERNAL_MARK_STAT)) > 0 else 1
        eternal = " (Eternal Mark: not Consumed the first time, so the second)" if uses == 2 else ""
        dur = mark_duration(mark)  # (MECH62: its duration with the build's modifiers; was a flat 8 s)
        scales: dict[str, float] = {}  # Full DPS entries counted x this (Pounce's slam, its wolves; MECH36)
        if mark.name == PREDATORS_MARK:
            pounce = next((s for s in skills if s.group == mark.group and s.name == "Pounce"), None)
            use = MarkUse(math.inf, "Pounce can't be used (PoB disables it: wrong weapon)")
            every = 0.0
            if pounce is not None:
                every = (pounce.cooldown or 0.0) / (pounce.cooldown_recovery or 1.0)
                use = MarkUse(max(every, dur), "triggered by Pounce, used as the Mark runs out (assumed)", pounce)
        else:
            use = mark_use(mark, skills, disabled, rates)
        period, how = use.period, use.how
        # the time a Mark stays on the boss after each application: until consumed, at most its duration
        held, consumed_by = dur, "Bloodhound's Mark's consumption on a Heavy Stun isn't rated (held its full duration)"
        if mark.name == PREDATORS_MARK:
            held, consumed_by = dur, "not consumed on a boss (only its death or Cross Slash consume it)"
            if main is not None and main.name == "Cross Slash" and hits_per_second(stats):
                held = min(1 / hits_per_second(stats), dur)
                # (Pounce recast once consumed, cooldown allowing: MECH36, it went under the cooldown before)
                period = max(period - dur + held, held, every)
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
                          "are summoned by a death on a lone boss (Cross Slash hitting the Marked boss with both gouges "
                          "Activates the Mark, which may summon them: UNVERIFIED, A22-3), so they're assumed carried in "
                          "from earlier marked kills, alive "
                          "and within 6 m of the boss: they follow you through area transitions, on the user's word, "
                          "11 Nov; PoB counts 0 of them). Their own hits count at that number* (LOW: each wolf's damage is "
                          "PoB's Wolf Companion, a monster table our game-file extract doesn't hold; PoB's Full DPS "
                          f"counts one, counted x{carried:g})")
                play = ("Keep your Pounce wolves alive into the boss: kill a few Marked enemies on the way in (with Cross "
                        "Slash, hitting the Marked boss with both gouges Activates the Mark and may summon more: "
                        "UNVERIFIED).")
        elif mark.name == "Sniper's Mark":
            crits = crits_per_second(stats)
            if use.on_demand:
                period = max(period, min(uses / crits, dur) if crits else 0.0)  # (recast once consumed)
            share = min(1.0, uses / (period * crits)) if crits and period else 1.0
            held, consumed_by = min(uses / crits, dur) if crits else dur, f"the next crit consumes it{eternal}"
            label, basis = "Sniper's Mark: share of crits it adds damage to", (
                f"one Mark every {period:.1f} s ({how}) against {crits:.2f} crits/s; the next crit consumes it{eternal} "
                "(PoB counts its extra crit damage on every crit)")
        elif mark.name in MARK_BUFFS:
            ailment = MARK_BUFFS[mark.name]
            inflicts = number(stats.get(f"{ailment}BuildupAvg")) > 0  # (PoB: a hit's average buildup, % of the threshold)
            marked = 1.0 if use.on_demand or not period else min(1.0, dur / period)
            if use.on_demand:
                period = max(period, dur)  # reapplied as it runs out (at its duration with the build's modifiers)
            rate = buildup_rate(stats, ailment)
            share = min(1.0, MARK_BUFF_DURATION * rate) * marked if inflicts else 0.0
            if inflicts and rate:
                held, consumed_by = min(uses / rate, dur), f"the boss's next {ailment} consumes it{eternal}"
            else:
                consumed_by = f"never consumed (no {ailment})"
            label, basis = f"{mark.name}: buff uptime", (
                (f"the boss {ailment.lower()}d once per {(1 / rate) if rate else math.inf:.1f} s while marked (PoB's buildup per hit x "
                 f"{hits_per_second(stats):.2f} hits/s, {bosses.of(stats).name}'s threshold, x6 for "
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


def mark_activations(mark: Skill, skills: list[Skill], stats: dict, disabled: list[Skill] | None = None,
                     rates: dict[str, float] | None = None, eternal: bool = False) -> tuple[float, str]:
    """(MECH61) Activations of `mark` per second on the boss, and how. A Mark that Activates is Consumed (KeywordPopups
    MarkActivate: "When a Mark Activates, it will cause an extra effect and then be Consumed"), so each application
    Activates once: on its event (Sniper's: a crit; Voltaic: Electrocute; Freezing: Freeze; Bloodhound's: a Heavy Stun),
    if the event comes within the Mark's duration D. Applications come at mark_model's period (mark_use, with the same
    recast rules), and an event rate r gives each one 1 - e^(-rD) Activations on average.
    MECH62 (AUDIT27): D is the Mark's own duration with the build's modifiers (mark_duration; was a flat 8 s), and with
    Eternal Mark (`eternal`: "Marks are not Consumed the first time they Activate") an application Activates up to twice:
    E[min(N, 2)] = (1 - e^(-x)) + (1 - e^(-x) - x e^(-x)), x = rD; Sniper's is recast after its second crit."""
    dur = mark_duration(mark)
    uses = 2 if eternal else 1
    use = mark_use(mark, skills, disabled, rates)
    period, how = use.period, use.how
    if not period or period == math.inf:
        return 0.0, f"{mark.name} is never applied ({how})"
    if mark.name == "Sniper's Mark":
        rate, event = crits_per_second(stats), "the next Critical Hit"
        if use.on_demand and rate:
            period = max(period, min(uses / rate, dur))  # (recast once consumed, as mark_model)
    elif mark.name in MARK_BUFFS:
        ailment = MARK_BUFFS[mark.name]
        rate = buildup_rate(stats, ailment) if number(stats.get(f"{ailment}BuildupAvg")) > 0 else 0.0
        event = f"the boss's next {ailment}"
        if use.on_demand:
            period = max(period, dur)  # (reapplied as it runs out, as mark_model)
    elif mark.name == "Bloodhound's Mark":
        rate, event = heavy_stun_rate(stats)[0], "the boss's next Heavy Stun (or a kill: none on a lone boss)"
    else:
        return 0.0, f"{mark.name}'s Activation isn't modelled (not counted, an undercount)"
    if rate <= 0:
        return 0.0, f"{mark.name} Activates on {event}, which the main skill never brings: never Activated"
    x = rate * dur
    each = (1 - math.exp(-x)) + ((1 - math.exp(-x) - x * math.exp(-x)) if eternal else 0.0)
    per_second = each / period
    return per_second, (f"{mark.name} applied every {period:.1f} s ({how}); it Activates on {event}, {rate:.2f}/s, so "
                        f"{each:.2f} Activations per application within its {dur:g} s"
                        + (" (Eternal Mark: not Consumed the first time, so up to 2)" if eternal else "")
                        + f": {per_second:.3f} Activations/s")


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
    by_hand: bool = False  # cast by hand: one cast per duration
    # its duration on the boss: PoB's, with the build's Curse Duration modifiers (bridge activeSkills curseDuration, the
    # stats' "_curse_durations"; AUDIT25 A25-41, MECHB12), else the level-20 base CURSE_DURATION
    duration: float = CURSE_DURATION


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
            read = number((stats.get("_curse_durations") or {}).get(name))
            duration = read or CURSE_DURATION
            lasts = f"{duration:.3g} s" + (" (PoB's, with the build's duration modifiers)" if read else
                                           " (the level-20 base; PoB gave none)")
            if "Blasphemy" in types:
                up = 1.0 if radius >= metres else 0.0
                how = (f"an Aura (Blasphemy) on enemies nearby, taken as your Presence ({radius:.1f} m) with the boss "
                       f"{metres:g} m away: {'inside' if up else 'outside'} it")
                uses.append(CurseUse(name, up, how))
            elif meta is not None and rates is not None and name in rates:
                rate = number(rates[name])
                up = min(1.0, rate * duration)
                uses.append(CurseUse(name, up, f"triggered by {meta} {rate:.3f} times/s (its Energy), "
                                               f"{lasts} each: {up:.0%} of the fight", duration=duration))
            elif meta is not None:
                uses.append(CurseUse(name, 1.0, f"triggered by {meta}: assumed applied when needed (LOW: no model gives "
                                                "its trigger rate)"))
            elif forever:
                uses.append(CurseUse(name, 1.0, "cast by hand once: 'Curses you inflict have infinite Duration' (no "
                                                "recasts; the first cast at the fight's start isn't counted)"))
            else:
                uses.append(CurseUse(name, 1.0, f"cast by hand every {lasts} (its duration; recast "
                                                f"{CURSE_DELAY:g} s before it runs out, the curse delay)", True, duration))
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
            lost = min(1.0, took / use.duration)
            mods.append(line("1% more Attack and Cast Speed", -100 * lost, skill=main.name))
            basis += f"; casting it ({took:.2f} s every {use.duration:.3g} s) takes {lost:.0%} of {main.name}'s time"
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
        every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per damaging use, MECHD4; MECHB13)
        share = boss_hits.block_share(stats)  # (MECHB6: only the boss's blockable hits, after evasion; MECHC5's shares)
        blocks = share / every
        return blocks * per, (f"you block {blocks:.2f} of the boss's hits per second (one every {every:g} s, {share:.0%} of them "
                              f"blocked: its red-flash hits can't be, the rest after evasion; boss_hits, LOW share) for "
                              f"{per:g} Energy each")
    if kind == "dodge":
        roll = stats.get("_dodge_roll") or dodge_roll([], stats)
        energy = roll["metres"] * per
        return energy / DODGE_EVERY, (f"one dodge roll every {DODGE_EVERY:g} s assumed against a boss (how often you roll is up to "
                                      f"you), {roll['basis']}, for {energy:.3g} Energy a roll (without the movement speed: "
                                      f"{roll['base_metres'] * per:.3g})")
    if kind == "energy shield lost":
        pool = number(stats.get("EnergyShield"))
        if pool <= 0:
            return 0.0, "the build has no Energy Shield: no Energy"

        def one(read: dict) -> tuple[float, str]:
            every = bosses.benefit_skill_time(read)  # (a "when hit" benefit: one hit per damaging use, MECHD4; MECHB13)
            land = boss_hits.land_share(read)  # (MECHB6: red-flash hits always land; MECHC5's shares)
            # (MECHB9, MECHC6: each {Type}TakenHit is that type at its full hit size, boss_hits.config_vars, so summing
            # them was one hit of every type at once; type_taken weights each by its share of the boss's hits)
            hit = boss_hits.type_taken(read)
            hits = land / every
            if hits <= 0:
                return 0.0, "none of the boss's hits land: no Energy"
            # Over a fight, a hit can only take the Energy Shield that came back since the last one: regeneration, and
            # recharge once the gap between landed hits outlasts the recharge delay (the first pool's worth isn't counted)
            gap = 1 / hits
            regen, recharge = number(read.get("EnergyShieldRegenRecovery")), number(read.get("EnergyShieldRecharge"))
            delay = number(read.get("EnergyShieldRechargeDelay"))
            restored = regen * gap + recharge * max(0.0, gap - delay)
            taken = min(hit, pool, restored)
            return hits * taken * per, (f"the boss's hits land {hits:.2f} times per second (one every {every:g} s, {land:.0%} neither "
                                        f"evaded nor blocked; red-flash hits always land, boss_hits), each taking {taken:,.0f} Energy Shield after mitigation (PoB's boss "
                                        f"preset; an average hit of {hit:,.0f}, each damage type at its share of the boss's hits, at most the {restored:,.0f} that returns between hits: "
                                        f"{regen:,.0f}/s regenerated, {recharge:,.0f}/s recharge after {delay:g} s): "
                                        f"1 Energy per 10 lost")

        # (MECHC20: a gain the boss's hits give, so the worst-case pinnacle reads the lowest real boss's, A29-1's rule;
        # before, its highest red-flash share and biggest hits)
        return boss_hits.gain_floor(stats, one)
    if kind == "minion death":
        rate, how = stats.get("_minion_deaths") or (0.0, "the build's minions weren't read")
        if rate <= 0:
            return 0.0, f"{how}: no Energy"
        return rate * per, (f"{how}; {per:g} Energy each (a minion of 100% relative Power assumed: the dying minion's Power "
                            f"isn't read yet, LOW)")
    if kind == "melee kill":
        kills, _, how = kill_rate(stats)
        if skill is not None and not (skill.melee and skill.attack):
            return 0.0, f"{main} is not a Melee Attack: it kills nothing for this (\"kill an enemy with a Melee Attack\")"
        if kills <= 0:
            return 0.0, f"{how}: no Energy"
        return kills * per * ADD_POWER, (f"{how}, each killed by {main} (the build plays to kill them) for {per:g} Energy per "
                                         f"Power, an add's Power {ADD_POWER:g} assumed (LOW)")
    if kind == "charm":
        spent = number(stats.get("_charm_charges"))
        if spent <= 0:
            return 0.0, "no charm is used against a boss (none equipped, or none makes the charges it needs): no Energy"
        return spent * per, (f"charms spend {spent:.2f} charges per second, each used as soon as its generated charges allow "
                             f"and not again while its effect lasts (the flask model: {stats.get('_charm_basis', '')}), for "
                             f"{per:g} Energy per charge (game files)")
    power = power_of(stats)  # (the boss's Power: 20, or more with the build's Power lines; boss_power, MECH67)
    if kind == "melee hit":
        if skill is not None and not skill.melee:
            return 0.0, f"{main} is not a melee attack: no Energy"
        hits = hits_per_second(stats)
        return hits * per * power, f"{main} hits {hits:.2f} times per second for {per * power:g} Energy each ({per:g} per Power)"
    if kind == "cast":
        if skill is not None and not skill.spell:
            return 0.0, f"{main} is not a spell: no Energy"
        cast = energy_seconds(skill) if skill is not None else 0.0
        each = per * cast * 100
        casts = number(stats.get("Speed"))
        return casts * each, (f"{main} is cast {casts:.2f} times per second for {each:.1f} Energy each ({per:g} per 10 ms of its "
                              f"{cast:g} s base cast time" + (f", its {skill.total_time_added:+g} s to Total Cast Time counted double: the "
                              "game's Energy definition, gain or consumption" if skill is not None and skill.total_time_added
                              else "") + ")")
    crit = number(stats.get("CritChance")) / 100
    if kind == "crit":
        crits = crits_per_second(stats)
        # (a spell's per-type averages are PoB's top-level output; an attack's are its main hand's)
        crit_hit = sum(number(stats.get(f"{t}CritAverage")) for t in DAMAGE_TYPES)             or sum(number(stats.get(f"MainHand.{t}CritAverage")) for t in DAMAGE_TYPES)
        threshold = number(stats.get("EnemyAilmentThreshold"))
        share = min(1.0, crit_hit / threshold) if threshold > 0 else 1.0
        per = power * share
        return crits * per, (f"{main} crits {crits:.2f} times per second (one hit per use) for {per:.2f} Energy each (Power {power:g}"
                             f" × {share:.0%}: a {crit_hit:,.0f} critical hit against the boss's {threshold:,.0f} "
                             "Ailment Threshold)")
    shock = (number(stats.get("ShockChanceOnHit")) * (1 - crit) + number(stats.get("ShockChanceOnCrit")) * crit) / 100
    ignite = (number(stats.get("IgniteChanceOnHit")) * (1 - crit) + number(stats.get("IgniteChanceOnCrit")) * crit) / 100
    events = hits_per_second(stats) * (min(shock, 1.0) + min(ignite, 1.0))
    freezes = buildup_rate(stats, "Freeze")  # (RULE_AUDIT #2, MECH41: before, Freezes gave none on a boss)
    how = f"{main} shocks or ignites {events:.2f} times per second (one hit per use) for {power:g} Energy each (the boss's Power)"
    if freezes > 0:
        how += (f", and freezes the boss {freezes:.3f} times per second for {FREEZE_ENERGY * power:g} Energy each "
                f"({FREEZE_ENERGY:g} per Power; models.buildup_rate: the main skill's buildup only, other skills' Freezes "
                "aren't counted, an undercount)")
    return (events + freezes * FREEZE_ENERGY) * power, how


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
        too_big = [s for s in spells if META_GEMS.get(meta.name, {}).get("invocation")
                   and energy_cost(s) > INVOCATION_MAX_ENERGY]
        for spell in too_big:  # (MECHB4, COVC6: the Invocation's store can never hold its cost)
            mods += trigger_rate(spell.name, 0.0)
            notes.append({"label": f"{meta.name} triggers per second ({spell.name}; not counted)", "value": 0,
                          "basis": f"{spell.name} costs {energy_cost(spell):.0f} Energy (10 per 0.1 s of base cast time"
                                   f"{', its Total Cast Time addition counted double' if spell.total_time_added else ''}), "
                                   f"more than the {INVOCATION_MAX_ENERGY} Energy an Invocation can store "
                                   "(generic_ongoing_trigger_maximum_energy, game files): it never triggers",
                          "confidence": "theory"})
        spells = [s for s in spells if s not in too_big]
        if not spells:
            continue
        names = ", ".join(s.name for s in spells)
        cost = sum(energy_cost(s) for s in spells)
        gain, gain_basis = energy_gain(meta.name, stats, main.name, main)
        if gain > 0 and main.energy_final and META_GEMS[meta.name]["gain"] in OWN_USE_GAINS:
            gain *= max(0.0, 1 + main.energy_final / 100)
            gain_basis += (f"; x{max(0.0, 1 + main.energy_final / 100):g}: {main.name}'s own "
                           f"{main.energy_final:g}% final Energy generated (active_skill_energy_generated_+%_final, game "
                           "files; PoB reads nothing of it). It has no game text, so whether it reaches every way this "
                           "skill feeds Energy (hits, crits, ailments, casts) is UNVERIFIED: applied to all of them")
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
                     + energy_time_note(spells)
                     + (f", {x['EnergyRefund']:.0f}% chance to refund half" if x["EnergyRefund"] else "")
                     + ("; an Invocation: you press it to release its Energy (instant in the game files), assumed often "
                        f"enough that its {INVOCATION_MAX_ENERGY} Energy store never fills" if info.get("invocation") else "")
                     + (f"; its spells deal {info['less']:.0f}% less damage" if info.get("less") else ""),
            "confidence": "theory"})
        if META_GEMS[meta.name]["gain"] == "dodge" and rate > 0:
            roll_seconds = (stats.get("_dodge_roll") or {}).get("seconds", DODGE_ROLL_SECONDS)
            busy = min(roll_seconds / DODGE_EVERY, 0.95)
            mods.append(line("1% more Attack and Cast Speed", -100 * busy, skill=main.name))
            notes.append({
                "label": f"Time spent dodge rolling ({main.name})", "value": f"{busy:.1%} less Attack and Cast Speed",
                "basis": f"a build that counts on {meta.name} rolls every {DODGE_EVERY:g} s (assumed; how often you roll is up "
                         f"to you), and each roll takes {roll_seconds:.3g} s (the dodge roll's own cast time in the game "
                         f"files" + (", sped up by the build's faster Dodge Roll" if roll_seconds != DODGE_ROLL_SECONDS else "")
                         + f") away from {main.name}",
                "confidence": "theory"})
    return mods, notes, groups


# Darkness ----------------------------------------------------------------------------------------
# Embrace the Darkness (https://poe2db.tw/Embrace_the_Darkness): "Damage taken is Reserved from
# Darkness before being taken from Life or Energy Shield", and the reservation lasts 5 seconds.
# PoB calculates the pool (base 100, +10 per level, increased maximum) but not what it does.

def with_darkness(stats: dict) -> dict:
    """Maximum hit taken with Darkness: the biggest hit comes out of Darkness before Life and
    Energy Shield, so Darkness adds to every pool. PoB's own value is kept as PoB<Type>Maximum-
    HitTaken (and used again if the stats are adjusted twice). Cat O' Nine Tails' Low Life cap (LowLifeRecoveryCap, the
    bridge's) takes the Life above Low Life out of the pool (MECHC8): Life recovery other than flasks stops at Low Life,
    so the build meets the boss's hits at Low Life once its first hits land (LOW_LIFE_SHARE of maximum Life; LOW for
    how much of the fight a Life flask holds it higher, said in its note)."""
    darkness = number(stats.get("Darkness"))
    lost = (1 - LOW_LIFE_SHARE) * number(stats.get("Life")) if number(stats.get("LowLifeRecoveryCap")) > 0 else 0.0
    if darkness <= 0 and lost <= 0:
        return stats
    for kind in DAMAGE_TYPES:
        key, raw_key = f"{kind}MaximumHitTaken", f"PoB{kind}MaximumHitTaken"
        raw = stats.get(raw_key, stats.get(key))
        pool = number(stats.get(f"{kind}TotalHitPool"))
        if isinstance(raw, (int, float)) and math.isfinite(raw) and pool > 0:
            stats[raw_key] = raw
            stats[key] = raw * max(pool + darkness - lost, 0.0) / pool
    return stats


LOW_LIFE_SHARE = 0.35  # KeywordPopups LowLife: "35% of its Maximum Life or less" (conditions.LOW_LIFE_SHARE, the same)


def low_life_cap_note(stats: dict) -> dict | None:
    """Cat O' Nine Tails' cap on Life recovery, for the build's notes (MECHC8)."""
    if number(stats.get("LowLifeRecoveryCap")) <= 0:
        return None
    life = number(stats.get("Life"))
    return {"label": "Life recovery stops at Low Life (Cat O' Nine Tails)", "value": round(LOW_LIFE_SHARE * life),
            "basis": f"Life Recovery other than Flasks cannot Recover Life to above Low Life: maximum hit taken counts "
                     f"{LOW_LIFE_SHARE:.0%} of your {life:,.0f} Life, not all of it (PoB parses the line to nothing)",
            "play": "a Life Flask can still fill Life: right after one, you take bigger hits; the floor checks use Low Life",
            "confidence": "datamined rule; LOW for how much of a fight a flask keeps Life higher (not counted)"}


def es_bypass_note(stats: dict) -> dict | None:
    """The Unleashed's / the Legacy rune's "N% of Damage taken from Hits bypasses Energy Shield if Energy Shield is below
    half", for the build's notes (MECHC22; the bridge's setEsBypassBelowHalf takes it out of the hit pools)."""
    share = number(stats.get("EsBypassBelowHalf"))
    if share <= 0:
        return None
    lost = number(stats.get("EsBypassPoolLost"))
    return {"label": "Hit damage bypasses Energy Shield below half (The Unleashed / Legacy rune)",
            "value": f"{share:g}%: {lost:,.0f} less Physical hit pool",
            "basis": f"once Energy Shield is below half, {share:g}% of each hit goes to Life: the hit pools count Energy "
                     "Shield's first half, then the split, then Life alone (from full with no other bypass: "
                     "min(ES + Life, ES/2 + Life/b)); it bites when Energy Shield is over ~6x Life (low-Life and Chaos "
                     "Inoculation builds); maximum hit taken scales with the pool. PoB parses the line with leftover "
                     "text and applies nothing",
            "play": "keep Energy Shield above half (recharge between the boss's hits) and Life takes nothing extra",
            "confidence": "datamined rule; whether one hit from full is split mid-hit is UNVERIFIED (the floors assume "
                          "it is, as after earlier hits)"}


# Jiquani's Thesis (MECHC9, AUDIT12 A12-2; research 18w): the boots Soul Core's "Increases and Reductions to Movement
# Speed also apply to Energy Shield Recharge Rate" (PoB ModRunes.lua 1293-1297, stat order 7327). The pinned PoB parses
# the whole line (ModParser: flag MovementSpeedAppliesToEnergyShieldRecharge; CalcDefence copies each increased /
# reduced Movement Speed modifier to Energy Shield Recharge INC, as for Waveshaper), but a socketed Soul Core reaches its
# parser as two halves (Item.lua UpdateRunes parses each rune line alone: "... Movement Speed also" / " apply to Energy
# Shield Recharge Rate"), neither of which parses: its Recharge Rate went uncounted (an UNDERCOUNT; an engine test
# records it). Given the first half on any item, the model hands PoB the whole line. PoB's flag is on or off, so a
# ledger entry joining the halves too (COV-A's, routed) counts nothing twice. Movement Speed PoB adds during its own
# calculation reaches it as PoB orders it (PoB's behaviour, not checked in game).
JIQUANI_HALF = re.compile(r"^\s*Increases and Reductions to Movement Speed also\s*$", re.I)
JIQUANI_LINE = "Increases and Reductions to Movement Speed also apply to Energy Shield Recharge Rate"


def jiquani_model(texts: list[str]) -> tuple[list[dict], list[dict]]:
    """The whole Jiquani's Thesis line for PoB when only its first half is in the build's lines (a socketed Soul Core)."""
    if not any(JIQUANI_HALF.match(t) for t in texts):
        return [], []
    return [line(JIQUANI_LINE)], [{
        "label": "Jiquani's Thesis: Movement Speed also raises Energy Shield Recharge Rate",
        "value": "counted", "confidence": "datamined rule (PoB's own reading of the whole line)",
        "basis": "the socketed Soul Core reaches PoB as two half-lines it can't parse; the model gives PoB the whole line, "
                 "so each increased or reduced Movement Speed modifier also raises Energy Shield Recharge Rate"}]


# Unbound Avatar (MECHC10; MECHC8's design, COVC10): Invoker's "...and I Shall Rage" (AscendancyMonk2Notable5,
# gain_unbound_ailment_stacks) reads "Grants Skill: Unbound Avatar" (PoB's tree). The skill (game files 26476662,
# GrantedEffectStatSets UnboundAvatarPlayer; PoB other.lua 19425): 100 Unbound Fury to use, a 12 s cooldown, the buff
# 8 s at level 1 to 9.9 s at 20 (+1 s at 20% quality), and three buff stats PoB maps to nothing (checked: SkillStatMap
# and ModParser have no ailment_bearer / unbound_ailment entry; only the separate "N% more Elemental Damage while
# Unbound" passive wording parses): ailment_bearer_elemental_damage_+%_final 40, unbound_ailment_elemental_ailment_
# chance_+%_final 80, unbound_ailment_hit_damage_elemental_immobilisation_multiplier_+%_final 40 (poe2db: "40% more
# Elemental Damage while Unbound", "80% more chance to inflict Elemental Ailments while Unbound", "40% more Elemental
# Ailment Buildup while Unbound"). PoB sets Condition:Unbound only from its config box, which nothing set before: the
# skill counted 0. Fury (KeywordPopups UnboundFury, HIGH): "Chilling, Shocking, or Igniting Enemies grants 1 Unbound
# Fury. Freezing or Electrocuting Enemies grants 6"; "Cannot gain Unbound Fury while Unbound" (stat_descriptions).
UNBOUND_GRANT = re.compile(r"^Grants Skill: Unbound Avatar$", re.I)
UNBOUND = "Unbound Avatar"
UNBOUND_FURY = 100.0
UNBOUND_COOLDOWN = 12.0
UNBOUND_DURATION = 9.9  # level 20, no quality: when the bridge doesn't list the skill
UNBOUND_FURY_LIGHT, UNBOUND_FURY_IMMOBILISE = 1.0, 6.0
UNBOUND_CHANCE_MORE, UNBOUND_BUILDUP_MORE = 80.0, 40.0
# PoB wordings, each under Condition:Unbound (the bridge's `pob` condition): "chance to inflict elemental ailments" is
# PoB's Ignite and Shock chance (ModParser 854); Chill has no chance in PoE2; Freeze and Electrocute are the elemental
# immobilisations (Pin is physical: not in the stat's "elemental_immobilisation")
UNBOUND_BUFF = (("1% more Elemental Damage", 40.0), ("1% more chance to inflict Elemental Ailments", UNBOUND_CHANCE_MORE),
                ("1% more Freeze Buildup", UNBOUND_BUILDUP_MORE), ("1% more Electrocute Buildup", UNBOUND_BUILDUP_MORE))


def unbound_model(texts: list[str]) -> tuple[list[dict], list[dict]]:
    """The Unbound buff's three stats for PoB, under its Condition:Unbound (conditions' "Unbound" box at the uptime
    unbound_uptime gives)."""
    if not any(UNBOUND_GRANT.match(t.strip()) for t in texts):
        return [], []
    return [line(text, value, when=[{"pob": "Unbound"}]) for text, value in UNBOUND_BUFF], [{
        "label": "Unbound Avatar's buff: 40% more Elemental Damage, 80% more chance to inflict Elemental Ailments, 40% "
                 "more Freeze and Electrocute Buildup", "value": "while Unbound",
        "confidence": "datamined (the skill's stats); the share of the fight is the Unbound box's",
        "basis": "PoB maps none of the skill's buff stats; they are given to PoB under its Unbound condition, which "
                 "counts at the share of the fight Unbound Fury lets you be Unbound"}]


def unbound_uptime(stats: dict, duration: float, on: bool) -> tuple[float, str, float]:
    """(share of the fight Unbound, how, the share if the cooldown ticks while Unbound). Fury from the main skill's
    hits: Ignite and Shock at PoB's chance per hit, Chill on hits that can Chill, 1 each; Freezes and Electrocutes from
    buildup_rate, 6 each. `on`: the stats were taken with the Unbound box on, so its 80% more chance and 40% more
    buildup come back out (no Fury while Unbound: the bar fills with the buff down). Not counted: other skills'
    ailments (an undercount), increased duration and cooldown recovery on the skill (undercounts). Whether a hit on a
    boss already carrying the ailment "inflicts" it again: taken as yes (each hit's new instance; UNVERIFIED). Whether
    the cooldown ticks while Unbound: UNVERIFIED (the game has a stat ailment_bearer_cooldown_does_not_tick that the
    skill's stat set doesn't hold); the headline takes the lower reading, cooldown from the buff's end: a cycle of
    duration + max(cooldown, fill); the other reading (max(cooldown, duration + fill)) is the second figure."""
    if duration <= 0:
        return 0.0, "", 0.0
    chance_div = 1 + UNBOUND_CHANCE_MORE / 100 if on else 1.0
    buildup_div = 1 + UNBOUND_BUILDUP_MORE / 100 if on else 1.0
    hits = hits_per_second(stats)
    ignite = min(number(stats.get("IgniteChancePerHit")) / chance_div, 100.0) / 100
    shock = min(number(stats.get("ShockChancePerHit")) / chance_div, 100.0) / 100
    chill = min(number(stats.get("ChillChanceOnHit")), 100.0) / 100
    # (AUDIT25 A25-3, MECHC11: a Chill counts only when it meets the game's 30% floor (conditions.chill_effect, MECH54:
    # every type that can Chill, after the boss's resistances; crits on their own); before, PoB's 1% check, so weak-Cold
    # hits earned Fury. With the box on, the 40% more Elemental Damage comes back out of the magnitude (LOW: taken as
    # scaling the whole Chilling hit))
    from builder.search import conditions
    normal, critical, _ = conditions.chill_effect(stats)
    damage_div = 1 + UNBOUND_BUFF[0][1] / 100 if on else 1.0
    crit = min(number(stats.get("CritChance")), 100.0) / 100
    floor_met = ((1 - crit) * (normal / damage_div >= conditions.CHILL_FLOOR)
                 + crit * (critical / damage_div >= conditions.CHILL_FLOOR))
    chill *= floor_met
    off = {**stats, "FreezeBuildupAvg": number(stats.get("FreezeBuildupAvg")) / buildup_div,
           "ElectrocuteBuildupAvg": number(stats.get("ElectrocuteBuildupAvg")) / buildup_div}
    heavy = buildup_rate(off, "Freeze") + buildup_rate(off, "Electrocute")
    fury = hits * (ignite + shock + chill) * UNBOUND_FURY_LIGHT + heavy * UNBOUND_FURY_IMMOBILISE
    if fury <= 0:
        return 0.0, ("Unbound Avatar: the main skill's hits inflict no Elemental Ailment, so no Unbound Fury: never "
                     "Unbound, 0"), 0.0
    fill = UNBOUND_FURY / fury
    up = duration / (duration + max(UNBOUND_COOLDOWN, fill))
    alt = duration / max(UNBOUND_COOLDOWN, duration + fill)
    how = (f"Unbound {duration:g} s per cycle of {duration + max(UNBOUND_COOLDOWN, fill):.1f} s: 100 Unbound Fury in "
           f"{fill:.1f} s ({fury:.2f}/s: {hits:.2f} hits/s x (Ignite {ignite:.0%} + Shock {shock:.0%} + Chill "
           f"{chill:.0%}, hits over the 30% Chill floor only) x 1, Freeze and Electrocute {heavy:.3f}/s x 6; the main skill's hits only), then the "
           f"{UNBOUND_COOLDOWN:g} s cooldown counted from the buff's end (UNVERIFIED; {alt:.0%} if it ticks while "
           f"Unbound); pressed once a cycle")
    return up, how, alt


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
    physical: float = 1.0  # share of the boss's hits that are Physical (boss_hits.type_shares; MECHB9, MECHC6)

    @property
    def fill(self) -> float:
        """Damage stored per second."""
        return self.gain * self.hit * max(self.reduction, MIN_REDUCTION) / 100 * self.hits * self.physical

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
    # (a "when hit" benefit: one hit per boss skill use, MECHC11; no boss timing in the stats: no hits, as before)
    skill_time = bosses.benefit_skill_time(stats) if number(stats.get("enemySkillTime")) > 0 else 0.0
    # (MECHB6: red-flash hits land; MECHC20: a gain, so the worst case reads the lowest real boss's landed hits)
    hits = boss_hits.gain_hits(stats)[0] if skill_time > 0 else 0.0
    return IronWardStore(number(skill.level_stats.get(STORE_CAP)), number(skill.level_stats.get(STORE_GAIN)) / 100,
                         number(stats.get("PhysicalEnemyDamage")), number(stats.get("PhysicalDamageReduction")), hits,
                         skill.cast_time or 1.0,
                         # PoB's boss config gives every hit its full Physical part (boss_hits.config_vars), but only the
                         # boss's Physical skills deal it (7 of the Arbiter of Ash's 24; LOW, boss_hits' type shares)
                         boss_hits.type_shares(boss_hits.boss_name(stats)).get("Physical", 1.0))


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
                 f"{bosses.benefit_skill_time(stats):.2f}s, one per boss skill use (MECH-D's per-boss figure, LOW), "
                 "less evaded and blocked hits), "
                 f"{store.physical:.0%} of them Physical (its Physical skills' share, LOW), so it refills in {refill:.1f}s"
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
                   main: Skill | None = None, previous: list[dict] | None = None,
                   config: dict | None = None, groups: dict | None = None) -> tuple[list[dict], list[dict]]:
    """`previous`: the lines applied when `stats` was calculated (Assumptions.mods), so a `share:` entry whose own line
    moves the stat it reads (Cadence's Attack Speed) can take its own last line back out (SHARES). `config`: PoB's config
    input (bridge getConfig), whose boxes an `additive:` entry counts (additive_value). `groups`: {group index: the
    bridge's useRates entry + `_press`} for gem_stat_side_groups, read by GROUP_SHARES (a non-main skill's own output)."""
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
            set_why = ""
            if entry.raw.get("stat_sets") is not None:  # (acts on some of the skill's stat sets only: stat_set_counts)
                counted, set_why = stat_set_counts(skill, entry.raw["stat_sets"])
                if not counted:
                    notes.append({"label": entry.title, "value": "not counted", "basis": f"{name}: {set_why}",
                                  "confidence": entry.confidence, "entry": entry.id})
                    done.add(name)
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
                read = {**(stats or {}), "_groups": groups} if groups and entry.raw["share"] in GROUP_SHARES else stats
                if entry.raw["share"] in SKILLS_SHARES:  # (reads the build's other skills: its minions, what kills them)
                    read = {**(read or {}), "_skills": skills}
                if entry.raw.get("share_seconds"):  # (the buff's duration the game text gives, no stat: COVE10)
                    read = {**(read or {}), "_seconds": float(entry.raw["share_seconds"])}
                if entry.raw["share"] in CONFIG_SHARES:  # (reads PoB's config: the boss distance the build is calculated at)
                    read = {**(read or {}), "_config": config or {}}
                factor, why, play = SHARES[entry.raw["share"]](skill, read or {}, main, prev)
                why = f"; {why}"
            else:
                why = ""
            why += set_why
            boxes = [str(b) for b in entry.raw.get("additive") or ()]
            on = sum(1 for b in boxes if (config or {}).get(b))
            for stat, value in sorted(values.items()):
                raw = value
                if boxes:  # (lines the game adds, on PoB's boxes: sized so the ones on multiply to 1 + n x value)
                    value, added = additive_value(value, on)
                    why = (why or "") + added
                if uptime is not None:
                    for text in entry.replace:
                        m = SYNTAX.match(text)
                        when = [{"var": m.group(2)}] if m and m.group(1) == "if" else None
                        mods.append(line(m.group(3) if when else text, value / UNIT, when=when, per=uptime,
                                         skill=name if own else None))
                if value * factor == 0:
                    if why:  # an uptime of 0 from the build's own numbers: the guide says why
                        shared_ = entry.raw.get("share") in SHARES
                        notes.append({"label": entry.title, "value": "not counted" if shared_ else "not counted (uptime 0%)",
                                      "basis": f"{name}: not counted{why}" if shared_ else f"{name} can't be kept up{why}",
                                      "confidence": entry.confidence,
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
                    "basis": (f"PoB never reads the support stat {stat} ({raw:g}) of {' / '.join(names)} on {name}; counted as "
                              + (f"(on the skills it triggers: {', '.join(targets)}) " if targets != [name] else "")
                              if support else f"PoB never reads the gem stat {stat} ({raw:g} at this level); counted as ")
                             + "; ".join(t.replace("1%", f"{value * factor:g}%") for t in entry.replace)
                             + ("" if own else " for the whole build") + why + (f". {entry.notes.strip()}" if entry.notes else ""),
                    "confidence": entry.confidence, "entry": entry.id, **({"play": play} if play else {})})
            if support:
                done.add(name)
            else:
                names = [n for n in names if n != name]  # one copy of each skill (a buff applies once)
    return mods, notes


# `additive: [box, ...]` (COVE7): the entry's lines are one per PoB config box (each line conditioned on what the box sets,
# as "against Ignited Enemies"), but the game adds them into one "more" (Elemental Armament III: "12% more Attack Damage
# for each Elemental Ailment on Enemies they Hit": 1 + 12% x n, not 1.12^n). PoB multiplies "more" lines one by one and
# has no count of the boss's ailments to read a single line from (checked: ModParser.lua writes its own "for each type of
# elemental ailment on enemy" lines one per ailment, INC, which add anyway; no Multiplier counts enemy ailments, grep of
# Modules/*.lua for AilmentCount / Multiplier:*Ailment*). So with n of the boxes on, each line is set to
# (1 + n x value)^(1/n) - 1, and the n lines multiply to exactly 1 + n x value. The uptime weighing then turns boxes off one
# at a time (Search.weigh_uptimes) without a new resolve: n - 1 lines at the n-box size give (1 + n v)^((n-1)/n), a hair
# under 1 + (n-1) v (two of 12%: x1.114 for x1.12; an undercount of under 1%, labelled). An ailment on the boss from a
# source other than its box (none counted: the resolver sets them through the boxes) is left out of n: its line then
# multiplies at the n-box size (at most the old small overcount).


def additive_value(value: float, on: int) -> tuple[float, str]:
    """One line's value (%) so that `on` of the entry's lines multiply to 1 + on x value; and the basis text."""
    if on < 2 or value <= -100 / on:
        return value, (f"; one of its conditions is on in this calculation ({value:g}% as it is)" if on == 1 else "")
    each = ((1 + on * value / 100) ** (1 / on) - 1) * 100
    return each, (f"; {on} of its conditions are on in this calculation, so each line is {each:.3g}% and they multiply to "
                  f"{1 + on * value / 100:.3g}x, the game's 1 + {on} x {value:g}% (they add in the game; PoB would multiply "
                  f"them); a box turned off by the uptime weighing leaves the others at this size, under 1% low")


# `stat_sets: {skill: all | [label, ...]}` (COVE8): the entry's line acts on some of a skill's stat sets only (Long Fuse's
# "Detonations from supported Skills deal 30% more Damage": Explosive Spear's Explosion, not the thrown Spear). PoB has no
# modifier tag for a stat set (checked: the tag types in Classes/ModStore.lua EvalMod; SkillPart reads the skill's
# `parts`, which these skills don't have, and BaseFlag reads stat set 1 only), so the line (tagged to the skill's name)
# reaches every set: it's counted only while the skill's calculated set is a listed one (Skill.stat_set, the label, once
# the bridge reports it: MECH-B), and on `all` skills, whose every damaging set is. A skill not in the map gets nothing.


def stat_set_counts(skill: Skill, sets: dict) -> tuple[bool, str]:
    """Whether a `stat_sets` entry's line counts on `skill`'s calculated stat set; and why (basis text)."""
    wanted = sets.get(skill.name)
    if wanted is None:
        return False, "not one of the skills the entry lists its parts for (UNVERIFIED which of its parts it acts on): not counted"
    if wanted == "all":
        return True, "; on every damaging stat set of the skill"
    wanted = [str(w) for w in wanted]
    label = getattr(skill, "stat_set", None)
    if label is None:
        return False, (f"only its {' / '.join(wanted)} stat set gets it, and the tool doesn't know which stat set is calculated "
                       f"(the bridge doesn't report it yet): not counted (an undercount when that set is the one)")
    if label not in wanted:
        return False, f"its calculated stat set {label} isn't one it acts on ({' / '.join(wanted)}): not counted"
    return True, (f"; its calculated stat set {label} is one it acts on (the line, tagged to the skill's name, would also "
                  f"reach another group's copy of {skill.name} at another stat set)")


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
         "pin_duration": "the Pins it inflicts (read by the Pinned uptime: models.pin_duration_scale, MECH66)",
         "curse_duration": "the Curse's duration when the cast consumes a Power Charge (curse model)",
         "ground_duration": "the Ground Surfaces it creates (read by the ground models: conditions.shocked_ground)",
         "haemocrystals": "the Haemocrystals it creates from Consumed Bleeding (no crystal damage is counted yet)",
         "ground_area": "the Ground Surfaces it creates, growing (read by the ground models: conditions.shocked_ground)",
         "aftershocks": "the extra Aftershocks of a Slam (read by MECH-B's aftershock_model)"}


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


# A Sustained skill (COVE7): the gem's description says "Sustained Skills will instead gain Attack speed periodically while
# being used" (Gems SupportCadencePlayer description; PoB sup_dex.lua the same). Its three stats (8 / 6 / 10000) are the
# only ones on SupportCadencePlayer (GrantedEffectStatSets ConstantStats 20826-20828, no other stat: checked, game files
# build 26476662), so how much speed and how often is in no stat: UNVERIFIED (for RESEARCH). A channelled skill's PoB Speed
# is its hit rate, not its uses, so pacing it to 1.25 uses/s (as COVE6's model did) cut it wrongly (Resonating Shield). It
# gets 0 from Cadence and no lockout: PoB's plain speed, as before COVE6, a labelled undercount of the periodic speed.


_SKILL_TYPES: dict[str, frozenset[str]] = {}


def _skill_types() -> dict[str, frozenset[str]]:
    """Active skill gems' skill types by name (the game data, loaded once)."""
    if not _SKILL_TYPES:
        from builder.data import GameData

        _SKILL_TYPES.update({g["name"]: frozenset(g.get("skillTypes") or ()) for g in GameData().gems
                             if not g.get("support")})
    return _SKILL_TYPES


def cadence_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    if "Sustained" in _skill_types().get(skill.name, ()):
        return 0.0, ("a Sustained skill: the game says it \"will instead gain Attack speed periodically while being used\" "
                     "(no lockout), and no stat gives how much or how often (UNVERIFIED), so PoB's plain speed stands: the "
                     "periodic speed is not counted (an undercount)"),             "Cadence on a Sustained (channelled) skill gains Attack speed periodically while you keep using it; not counted."
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


# Practical Magic I/II (COVE7). Game files (GrantedEffectStatSets SupportPracticalMagicPlayer / ...Two, gem_stat_descriptions):
# "Gain 30% (40%) increased Spell damage for 8 seconds on Heavy Stunning an enemy with Supported Skills"
# (support_stunning_magic_grant_spell_damage_+%_buff_on_heavy_stunning, support_stunning_magic_buff_duration_ms 8000).
# PoB maps neither (checked: sup_int.lua's stat lists only; no statMap, nothing in SkillStatMap.lua or Modules/). The buff's
# share of the fight comes from the build: the main skill's Heavy Stuns of the boss per second (heavy_stun_rate: its own
# hits' buildup against the Arbiter of Ash's threshold, +500% for 16.5 s after each), one every `every` s, each giving
# 8 s: min(1, 8 / every). Increased damage adds, so the line at that share is the fight's average exactly. A supported
# skill that isn't the main skill gets 0 (no Heavy Stun rate for it: an undercount, noted). Buff duration modifiers aren't
# read (an undercount). When the main skill is a Spell, the buff raises its own buildup, as in the game: PoB's buildup
# here already has the line's last share in it.


def heavy_stun_buff_share(skill: Skill, stats: dict, main: Skill | None = None,
                          prev: float = 0.0) -> tuple[float, str, str]:
    seconds = (number(skill.support_stats.get("support_stunning_magic_buff_duration_ms")) or 8000.0) / 1000
    play = (f"Heavy Stun the boss with the supported skill: each Heavy Stun gives the Spell damage for {seconds:g} s.")
    if main is None or main.name != skill.name or main.group != skill.group:
        return heavy_stun_buff_side(skill, stats, seconds, play)
    rate, how = heavy_stun_rate(stats)
    if rate <= 0:
        return 0.0, f"no Heavy Stuns: {how}", play
    share = min(1.0, rate * seconds)
    why = (f"up {share:.0%} of the fight: a Heavy Stun every {1 / rate:.1f} s ({how}), {seconds:g} s each; buff duration "
           f"modifiers not read")
    play += (f" Headline: up {share:.0%} of the fight (a Heavy Stun every {1 / rate:.1f} s). Without Heavy Stuns the "
             f"buff gives nothing.")
    return share, why, play


# A13-3 (AUDIT13): Practical Magic's natural use is on a stunning attack beside a main spell. Its Heavy Stuns come from
# that skill's own group (`stats["_groups"][group]`: the bridge's useRates entry, the group calculated as if it were the
# main one, which gem_stat_model is given as `groups`), at the share of its nonstop uses the press model gives it
# (`_press`: objective.press_scales, the same rate whose time the main skill loses, so nothing is counted twice).
GROUP_SHARES = {"heavy_stun_buff"}  # (share functions that read a non-main skill's own group)
HEAVY_STUN_GROUP_FIELDS = ("HeavyStunBuildupAvg", "HitChance", "CritChance",
                           *(f"{t}{k}Average" for t in DAMAGE_TYPES for k in ("Hit", "Crit")))


def heavy_stun_buff_side(skill: Skill, stats: dict, seconds: float, play: str) -> tuple[float, str, str]:
    """Practical Magic's share on a supported skill that isn't the main one, from its own group's Heavy Stuns."""
    own = (stats.get("_groups") or {}).get(skill.group)
    if own is None or "HeavyStunBuildupAvg" not in own:
        return 0.0, ("not the main skill, and the bridge doesn't report its own group's Heavy Stun buildup (useRates), so "
                     "its Heavy Stuns of the boss aren't worked out: not counted (0; an undercount if it stuns)"), play
    press = own.get("_press")
    if not press:
        return 0.0, ("not the main skill, and the press model gives it no use rate (it isn't a pressed skill in Full DPS): "
                     "not counted (0)"), play
    nonstop, how = heavy_stun_rate({**own, "Speed": own.get("Speed", own.get("speed"))})
    rate = nonstop * min(1.0, number(press))
    if rate <= 0:
        return 0.0, f"no Heavy Stuns from it: {how}", play
    share = min(1.0, rate * seconds)
    why = (f"up {share:.0%} of the fight: used beside the main skill at {number(press):.0%} of its nonstop rate (the press "
           f"model's, whose time the main skill loses), a Heavy Stun every {1 / rate:.1f} s (nonstop: {how}), {seconds:g} s "
           f"each; the main skill's own stun buildup towards the same bar not counted (an undercount)")
    play += (f" Use {skill.name} beside the main skill to Heavy Stun the boss. Headline: up {share:.0%} of the fight (a "
             f"Heavy Stun every {1 / rate:.1f} s at the rate the tool presses it). Without it the buff gives nothing.")
    return share, why, play


# Upwelling (COVE8): "Your Minions deal 30% increased Damage while you are not on full Mana while a Supported Skill is
# active". PoB parses "while on full mana" but not "not on full mana" (checked: ModParser.lua's FullMana / FullLife
# patterns), and maps neither stat. The share of the fight below full Mana, from the build's own spending: each spend of
# c Mana takes c / regen seconds to refill, so at S Mana/s spent against R regenerated it's below full min(1, S / R) of the
# time (any S >= R never refills). S is the main skill's ManaPerSecondCost (PoB); other skills' costs not counted (an
# undercount), nor Mana flasks or leech topping it up faster (an overcount).


def not_full_mana_share(skill: Skill, stats: dict, main: Skill | None = None,
                        prev: float = 0.0) -> tuple[float, str, str]:
    spend, regen = number(stats.get("ManaPerSecondCost")), number(stats.get("ManaRegenRecovery"))
    play = "Keep spending Mana (the main skill's cost) so you're never back on full Mana while the minions fight."
    if spend <= 0:
        return 0.0, ("the main skill spends no Mana, so you're on full Mana (0; other skills' Mana costs aren't counted: "
                     "an undercount if they keep you below full)"), play
    share = 1.0 if regen <= 0 else min(1.0, spend / regen)
    why = (f"below full Mana {share:.0%} of the fight: the main skill spends {spend:.1f} Mana/s against {regen:.1f} "
           f"regenerated (each spend takes cost / regen to refill; other skills' costs and Mana flasks / leech not counted)")
    play += f" Headline: below full Mana {share:.0%} of the fight. On full Mana the minions get nothing from it."
    return share, why, play


# `share: supported_main` (COVE9): a line PoB conditions on what the main skill does (Steadfast's "while Channelling
# Supported Skills": PoB's Condition:Channelling is set from the main skill only, CalcPerform.lua 394). Counted only when
# the supported skill is the main skill; on a side skill PoB's condition would follow the main skill, not the supported
# one, so it's left out (an undercount for the time you channel the side skill, said so).
def supported_main_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    if main is not None and main.name == skill.name and main.group == skill.group:
        return 1.0, "on the main skill, whose channelling PoB's condition follows", ""
    return 0.0, ("not on the main skill: PoB's Channelling condition follows the main skill, so the time you channel this "
                 "one isn't counted (an undercount)"), ""


# `share: cull_buff` (COVE9): a buff the supported skill gains for a time on Culling an enemy (Thrill of the Kill I/II:
# "Attacks Gain 25% of Damage as Lightning Damage for 8 seconds on Culling an enemy with Supported Skills"). Against one
# boss the Cull is the kill (KeywordPopups CullingStrike: the enemy is killed), so the buff only starts after the fight;
# culls of adds feed it (RESEARCH14/15/16: the King in the Mists' totems on a 25 s / 15 s summon cooldown, Atziri's
# cultists every 45 s). It reads CULLS_STAT, the request's boss preset's add Culls per second (MECH-D's
# bosses.kill_stats, merged into the stats by Assumptions.models), and is 0 with a note against a boss with no
# killable adds: no flat guess.
# Culling Strike II (COVE10): "Gain 20% increased Culling Strike Threshold for 20 seconds on Culling with Supported Skills"
# is the same case, its 20 s from the game text (the stat is ..._for_seconds_from_code: an entry's `share_seconds`).
CULLS_STAT = "AddCullsPerSecond"


def cull_buff_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    seconds = (number(stats.get("_seconds"))  # (an entry's own `share_seconds`: Culling Strike II's 20 s)
               or number(skill.support_stats.get("support_thrill_of_the_kill_buff_base_duration_ms")) / 1000 or 8.0)
    culls = number(stats.get(CULLS_STAT))
    play = ("Cull the boss's adds (the King in the Mists' totems, Atziri's cultists) with the supported skill to start the "
            "buff; Culling the boss itself ends the fight, so it gives nothing there.")
    if culls <= 0:
        return 0.0, ("0: the buff starts on Culling an enemy, and against a lone boss the Cull is the kill; this boss "
                     "preset has no killable adds with a known rate (bosses.kill_stats)"), play
    share = min(1.0, culls * seconds)
    return share, (f"buff up {share:.0%} of the fight: {culls:.3g} culls/s x {seconds:g} s (its base duration; Skill "
                   f"Effect Duration increases not counted)"), play + f" Headline: up {share:.0%} of the fight."


# `share: revived_recently` (COVE9): Crazed Minions, "Supported Minions deal 30% more Damage if they have Revived
# Recently" (Recently: the past 4 s, KeywordPopups). A minion Revives only after it's Killed, so the rate comes from what
# in the build kills its own Reviving minions (minion_deaths: Sacrifice, Explosive Demise; 0 with its note when nothing
# does, no flat guess). Deaths are taken as spread evenly over the build's N Reviving minions: each one dies every N / D s
# and is down for the revive delay R (revive_seconds), so it's alive L = N / D - R s per cycle, the first 4 s of it
# Revived Recently: share min(1, 4 / L) of its alive time (the game revives all the dead together after the last death,
# KeywordPopups Reviving, so this is an approximation: MEDIUM-LOW). Its time dead is the minion model's, not this one's.
RECENTLY_SECONDS = 4.0


def revived_recently_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    skills = stats.get("_skills") or [skill]
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    count = sum(number(s.summon.get("limit")) for s in reviving)
    deaths, how = minion_deaths(skills, main, stats)
    play = ("Keep killing your own Reviving minions (Sacrifice, Explosive Demise) so the supported minions have Revived in the "
            "last 4 s; without it the minions never Revive and the bonus is 0.")
    if deaths <= 0 or count <= 0:
        return 0.0, f"0: the minions Revive only after being Killed, and {how}", play
    revive, _ = revive_seconds(reviving)
    alive = count / deaths - revive
    share = 1.0 if alive <= RECENTLY_SECONDS else RECENTLY_SECONDS / alive
    return share, (f"Revived Recently {share:.0%} of their time alive: {how}; each of the {count:g} dies every "
                   f"{count / deaths:.3g} s and is down {revive:.3g} s, alive {max(alive, 0):.3g} s, the first "
                   f"{RECENTLY_SECONDS:g} s of it Revived Recently (deaths spread evenly over your Reviving minions assumed; "
                   f"MEDIUM-LOW)"), play + f" Headline: {share:.0%} of their time alive."


# `share: near_boss` (COVE9): Lockdown, "Supported Skills have 120% more Pin Buildup against enemies within 2 metres of
# you, scaling to no change at 12 metres" (gem_stat_descriptions; PoB reads a MORE on EnemyPinBuildup, CalcOffence's
# poise buildups, and rates the boss Pinned from its PinBuildupAvg: conditions' Pinned, MECH46). Sized by the distance
# the build is calculated at (PoB's enemyDistance, set by conditions._boss_distance; the tool's natural distance when
# unset): full within 2 m, falling linearly (assumed: "scaling") to 0 at 12 m. The search doesn't yet move a ranged build
# closer for it (no DISTANCE_LINE matches a support stat): an undercount for such a build, said in the play row.
NEAR_FULL_METRES, NEAR_NONE_METRES = 2.0, 12.0


def near_boss_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    from builder.search.conditions import MELEE_DISTANCE, RANGED_DISTANCE  # (conditions imports models)

    units = number((stats.get("_config") or {}).get("enemyDistance"))
    if units > 0:
        metres, where = units / 10, "the boss distance this build is calculated at"
    else:
        metres = MELEE_DISTANCE if main is not None and main.melee else RANGED_DISTANCE
        where = "the tool's natural distance for the main skill (none set)"
    share = max(0.0, min(1.0, (NEAR_NONE_METRES - metres) / (NEAR_NONE_METRES - NEAR_FULL_METRES)))
    play = (f"Fight within {NEAR_FULL_METRES:g} m of the boss for the full bonus (it falls to nothing at "
            f"{NEAR_NONE_METRES:g} m); the headline counts {share:.0%} of it at {metres:g} m.")
    return share, (f"{share:.0%} of it at {metres:g} m ({where}; full within {NEAR_FULL_METRES:g} m, none at "
                   f"{NEAR_NONE_METRES:g} m, linear between assumed)"), play


# `share: corpse_consumed` / `corpses_per_use` (COVE10): Profanity I, "Supported Skills deal 30% more Chaos Damage if
# consuming a Corpse", and Profanity II, "5% more Chaos Damage for each Corpse consumed this way" (up to 10 within 5 m on
# use: skill_consume_up_to_X_corpses_on_use). The supported skill still works without a corpse, so only the bonus needs
# one. Corpses come from the build's own sources only (corpse_supply, MECH-B's: nothing dies on a lone boss, and no
# source is priced yet, so 0 with its note: no flat guess). With a supply S per second and U uses per second of the
# supported skill (the main skill's Speed; a side skill's use rate isn't read: 0, said so), Profanity I counts at
# min(1, S / U) of its uses and Profanity II at min(cap, S / U) corpses per use. Other corpse users in the build would
# share S (not split here: MECH-B's supply model would say how).
def corpse_share(skill: Skill, stats: dict, main: Skill | None, per_use: bool) -> tuple[float, str, str]:
    present = {s.name for s in stats.get("_skills") or [skill]}
    supply = corpse_supply(present, stats)
    play = ("Consume corpses with the supported skill: on a lone boss nothing leaves one, so it needs the build's own "
            "corpse source (Sacrifice's undead Reviving minions, as fast as the build kills them) or the boss's adds "
            "(kill them near the boss); "
            "without one the bonus is 0.")
    if supply <= 0:
        sources = [f"{name}: {why}" for name, why in CORPSE_SOURCES.items() if name in present]
        return 0.0, ("0: the bonus needs a corpse consumed on use, nothing dies on a single boss (no killable adds in "
                     "this preset), and "
                     + (f"the build's own corpse source gives none ({sacrifice_note(stats)})" if sources
                        else "the build has no corpse source of its own")
                     + " (models.corpse_supply)"), play
    if main is None or main.name != skill.name or main.group != skill.group:
        return 0.0, f"0: {supply:.3g} corpses/s, but the use rate of a non-main skill isn't read (an undercount)", play
    uses = number(stats.get("Speed"))
    if uses <= 0:
        return 0.0, f"0: {supply:.3g} corpses/s, but the skill's use rate is unknown", play
    cap = number(skill.support_stats.get("skill_consume_up_to_X_corpses_on_use")) or 1.0
    share = min(cap if per_use else 1.0, supply / uses)
    return share, (f"{share:.3g} {'corpses per use' if per_use else 'of its uses consume a corpse'}: {supply:.3g} "
                   f"corpses/s over {uses:.3g} uses/s (other corpse users not split off; "
                   f"{add_corpses_note(stats)})"), play


def corpse_consumed_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    return corpse_share(skill, stats, main, per_use=False)


def corpses_per_use_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    return corpse_share(skill, stats, main, per_use=True)


# `share: stun_bar` (COVE10): Retaliate I/II, "Supported Skills deal up to 40% more Attack Damage, scaling up as your Stun
# Bar increases" (gem_stat_descriptions). PoB maps nothing (checked: no statMap in sup_str.lua's two entries, no
# from_current_poise in SkillStatMap.lua or Modules/*.lua). Counted at the average fill of your Stun Bar through the fight
# (linear in the fill assumed: "scaling up"), read from the stats as STUN_BAR_STAT, a 0-1 share MECH-C's player Stun-bar
# model will give from the boss's hits; 0 with a note while it's missing (no flat guess).
STUN_BAR_STAT = "PlayerStunBarAverage"


def stun_bar_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    fill = number(stats.get(STUN_BAR_STAT))
    play = ("Take the boss's hits without being Heavily Stunned: the fuller your Stun Bar, the more damage (Stun Threshold "
            "lets it fill further before you're Heavily Stunned).")
    if fill <= 0:
        return 0.0, ("0: it scales with how full your Stun Bar is, and the tool doesn't model the player's Stun Bar against "
                     "the boss's hits yet (MECH-C; UNVERIFIED fill)"), play
    fill = min(1.0, fill)
    return fill, f"at {fill:.0%}, your Stun Bar's average fill through the fight (linear in the fill assumed)", play


# `share: seal_echo` (COVE11; COVB6's open question): Tul's Avalanche, "Supported Spells Echo 1 time per Seal broken"
# (skill_echoes_per_seal_broken 1), "gain a Seal every 2 times you Block" (skill_gain_seal_every_x_hits_blocked), a
# maximum of 4. PoB counts none of the Echoes (checked: PoB maps only base_maximum_seals_for_skill -> SealCount and
# base_skill_is_sealed_skill -> HasSeals, SkillStatMap.lua 2329-2343; its Seal damage, CalcOffence.lua 1291-1323 and 4573 /
# 6205, runs only under Unleash's DamageSeal / Expand's AreaSeal flags, and SealGainFrequency is 0 without them;
# skill_echoes_per_seal_broken and skill_gain_seal_every_x_hits_blocked are read nowhere in PoB src): an UNDERCOUNT.
# Seals come from the build's own Blocks (passive_models.seal_rate, COVB6's, the one Rhythm of Ice reads: the boss's
# landing hits x Block chance / 2). Every Seal is broken by a use and Echoes once ("1 time per Seal broken"), so the
# Echoes per second are the Seals per second whenever the spell is cast at least once per 4 Seals (always here: the cap
# is never reached), and each Echo hits the same place again ("affecting the same targeted location again after a
# delay", KeywordPopups Cascadable), counted as one more use of the spell: 1 + Seals/s / casts/s, as "more Damage".
# Assumed: an Echo deals the use's full damage (no less-damage stat on Tul's; MEDIUM); the boss stays in the area.
# On the main skill only (its casts/s is PoB's Speed); on a side spell: 0, an undercount, said so. Another Seal source on
# the skill (a summed maximum above Tul's 4: Expand, Unleash, Salvo, Zarokh's Revolt) makes it 0 (A13-4: Tul's can't
# support a skill that already gains Seals).
TULS_SEALS = 4.0


def seal_echo_share(skill: Skill, stats: dict, main: Skill | None = None, prev: float = 0.0) -> tuple[float, str, str]:
    from builder.search import passive_models

    play = ("Block the boss's hits: every 2 Blocks give the spell a Seal (up to 4), and your next cast breaks them, "
            "Echoing once per Seal.")
    if main is None or main.name != skill.name or main.group != skill.group:
        return 0.0, ("not the main skill: its casts per second aren't known here, so its Echoes aren't counted (an "
                     "undercount)"), play
    if number(skill.support_stats.get("base_maximum_seals_for_skill")) > TULS_SEALS:
        return 0.0, ("the spell also gains Seals from another support, so Tul's Avalanche can't support it (\"Cannot "
                     "support skills ... which already gain Seals\")"), play
    # (PoB applies Tul's only to a Cascadable spell, so the supported skill is one: CalcActiveSkill.lua 187)
    types = (set(_skill_types().get(skill.name, ())) | {"Cascadable"}) if skill.spell else set()
    seals = passive_models.seal_rate(skill, types, {passive_models.TULS_AVALANCHE}, stats)
    casts = number(stats.get("Speed"))
    if seals <= 0 or casts <= 0:
        return 0.0, ("no Seals: the build Blocks none of the boss's hits (Seals come every 2 Blocks)" if casts > 0 else
                     "the spell isn't cast"), play
    share = seals / casts
    return share * 100, (f"{seals:.3f} Seals/s from your Blocks (every 2 Blocks of the boss's landing hits), each broken "
                         f"by a cast and Echoing once: +{share:.1%} uses over {casts:.2f} casts/s (an Echo counted as a "
                         f"full use, MEDIUM)"), play + f" Headline: one Echo per {1 / seals:.1f} s."


CONFIG_SHARES = {"near_boss"}  # (share functions that read PoB's config: gem_stat_model passes `_config`)

SKILLS_SHARES = {"revived_recently", "corpse_consumed", "corpses_per_use"}  # (read the build's skills: `_skills`)

SHARES = {"alignment": alignment_match_share, "cadence": cadence_share, "heavy_stun_buff": heavy_stun_buff_share,
          "not_full_mana": not_full_mana_share, "supported_main": supported_main_share,
          "cull_buff": cull_buff_share, "revived_recently": revived_recently_share,
          "near_boss": near_boss_share, "corpse_consumed": corpse_consumed_share,
          "corpses_per_use": corpses_per_use_share, "stun_bar": stun_bar_share,
          "seal_echo": seal_echo_share}


def gem_stat_side_groups(skills: list[Skill], main: Skill | None) -> list[int]:
    """Non-main groups whose own output a GROUP_SHARES entry reads (the bridge's useRates for them: gem_stat_model's
    `groups`)."""
    from builder.ledger import gem_stat_entries

    found = set()
    for entry in gem_stat_entries():
        if entry.raw.get("share") not in GROUP_SHARES or entry.pattern is None:
            continue
        for skill in skills:
            if skill.group is not None and (main is None or skill.group != main.group) and any(
                    entry.pattern.search(stat) and number(v) for stat, v in skill.support_stats.items()):
                found.add(skill.group)
    return sorted(found)


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
        "basis": f"unreserved Mana {mana:,.0f} is above Life {life:,.0f}: counted as 100% of Hit damage taken from Mana before "
                 "Life, both pools full; a hit bigger than the Mana overflows into Life (one player's test, MEDIUM: RESEARCH4 "
                 "harmony-within.md); a fight where Mana drains below Life isn't counted. Only hits: PoB's line also moves the "
                 "build's own damage over time onto Mana, but only in its net-regeneration outputs, which no number here reads "
                 "(checked: builder/ and the bridge read no NetLifeRegen, NetManaRegen or BuildDegen; the floors read "
                 "MaximumHitTaken and TotalEHP, both from hits)", "confidence": "theory"}]


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
    """Mana Tempest's storm (RESEARCH3, mana-tempest-lunar-blessing.md, game files ManaTempestPlayer and mana_tempest.csd):
    it "Empowers your Mana-costing Spells", so a main skill that costs no Mana (a meta-triggered spell, a Life-cost spell)
    gets nothing: 0 (before, counted in full: an overcount); "30% of Mana and Life spent while in the storm is added to this
    Skill's Mana Cost per Second", so Life spent (LifePerSecondCost) drains the storm too (before, Mana only: an
    overcount for skills that also cost Life). Not counted: its effects "linger for 2 seconds after leaving the Tempest"
    (0.4.0), whether that also follows a storm ended by running out of Mana is UNVERIFIED (a small undercount)."""
    mana, regen, spend = number(stats.get("Mana")), number(stats.get("ManaRegenRecovery")), number(stats.get("ManaPerSecondCost"))
    life_spend = number(stats.get("LifePerSecondCost"))
    spent_share = _mana_tempest_spent(skill)
    drain = MANA_TEMPEST_DRAIN * mana + spent_share * (spend + life_spend)
    deficit = spend + drain - regen
    if mana <= 0:
        return 0.0, "no Mana", ""
    if spend <= 0:
        return 0.0, ("the main skill costs no Mana: Mana Tempest Empowers only Mana-costing Spells (its game description; "
                     "a meta-triggered or Life-cost spell gets nothing)"), ""
    paid = f"30% of the {spend:,.0f} Mana" + (f" and {life_spend:,.0f} Life" if life_spend > 0 else "") + " you spend per second"
    if deficit <= 0:
        return 1.0, (f"Mana regenerates {regen:,.0f}/s, more than the main skill's {spend:,.0f}/s plus the storm's drain "
                     f"{drain:,.0f}/s (1% of {mana:,.0f} Mana and {paid}): the storm lasts as long as you stay in it"),             "Assumes you stand in the storm for the whole fight; every step out of it ends it (2 s later)."
    on = mana / deficit
    refill = regen - spend
    if refill <= 0:
        return 0.0, f"the main skill alone spends {spend:,.0f} Mana/s against {regen:,.0f}/s regenerated: no Mana left to keep a storm", ""
    off = mana / refill
    up = on / (on + off)
    return up, (f"the storm drains {drain:,.0f} Mana/s (1% of {mana:,.0f} Mana and {paid}) on top of the main skill's "
                f"{spend:,.0f}/s against {regen:,.0f}/s regenerated: {mana:,.0f} Mana lasts {on:.1f} s; then {off:.1f} s to "
                "refill without it, recast at full Mana; the 2 s its effects linger after a storm isn't counted (UNVERIFIED "
                "when the storm ends from running out of Mana)"),         "Assumes you recast it whenever your Mana is full again and stand in it; recasting earlier gives shorter storms."


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



# Damage sources computed outside PoB's main skill (MECHB20: the added-damage slot Thorns and Eternal Hunt need). Each is
# a derived input the bridge sums with Melting Maelstrom's pulses into LedgerAddedDPS for every what-if (engine/bridge.lua
# DERIVED.added / ADDED_DPS), so the objective adds them all (Objective.dps reads LedgerAddedDPS, else added_dps, which
# Assumptions.models sets to the same sum for the build itself). A source follows the what-if's own output when given a
# PoB output `field` (dps = output[field] x factor); else it's the resolve's fixed number, which the item what-ifs see
# unchanged until the next resolve (a real trial only with BUILDER_RESCORE_MODELS=1: Assumptions.models_on_stats), so an
# item that moves the source is valued without that move until then (labelled where a source is fixed).
ADDED_FN = "added"
ADDED_FNS = {ADDED_FN, "maelstrom"}


def added_input(name: str, dps: float, field: str | None = None, factor: float = 1.0) -> dict:
    """A separately computed damage source for the bridge's LedgerAddedDPS (in hundredths): `dps` the build's own;
    `field`/`factor` how a what-if's own output gives it (none: fixed at `dps`)."""
    args = {"field": field, "factor": factor} if field else {"fixed": dps}
    return derived(f"Added{name}", ADDED_FN, dps * 100, **args)


def added_total(mods: list[dict]) -> float:
    """The build's own added DPS: the sum of its added sources' values (the bridge's hundredths / 100)."""
    return sum(m["value"] for m in mods if m.get("kind") == "Derived" and m.get("fn") in ADDED_FNS) / 100


# Eternal Hunt (MECHB20; MECHB19's research): the Azmerian Wolf's Command. The game: packleader_great_hunt.csd mode 7,
# "Summons {0} Spirit Wolves / Wolves dash at enemies over 8 seconds" (7 wolves); GreatHuntPackleaderMinion: Cooldown
# 15000 ms, CastTime 4000 ms (the wolf's howl), 1000% of base; each wolf's hit, GAPackleaderGreatHuntDash, has the same
# 1000% (GrantedEffectStatSetsPerLevel); the player's Command, CommandPackleaderPlayer, CastTime 1000 ms (checked: game
# files; second source poe2db Eternal_Hunt: "Summons 7 Spirit Wolves", cooldown 15.00 s, 1000% of base). PoB never
# scores it: a minion is scored on its first skill (CalcActiveSkill 1195-1203, skillMinionSkill unset), so the bridge's
# minionSkillHit scores the wolf with the hunt chosen. Counted as an added damage source (added_input, fixed at the
# resolve: an item's what-if doesn't move it until the next resolve, an undercount for minion-damage items):
#   - one wolf's hit per use (whether all 7 reach a lone boss is on the user's in-game list: LOW, a "with all 7" row);
#     a spirit wolf's hit taken as the Azmerian Wolf's own hit with the hunt chosen (its stats: UNVERIFIED);
#   - one use per cooldown (a press under the button cap; the search's press rules don't see it yet: SEARCH's);
#   - charged: the main skill's time for the Command's 1 s cast (whether a Command stops your skill is UNVERIFIED: the
#     safe side), and the wolf's own attacks for its 4 s howl where the score counts the wolf (the howl's speed scaling,
#     "uses animation length", UNVERIFIED). A net loss: not pressed, 0.
ETERNAL_HUNT = "GreatHuntPackleaderMinion"
AZMERIAN_WOLF = "Azmerian Wolf"
ETERNAL_HUNT_WOLVES = 7
ETERNAL_HUNT_COOLDOWN = 15.0  # s (GrantedEffectsPerLevel Cooldown 15000)
ETERNAL_HUNT_HOWL = 4.0  # s (GrantedEffects CastTime 4000)
COMMAND_CAST = 1.0  # s (CommandPackleaderPlayer CastTime 1000)


def eternal_hunt_model(hunt: dict, player_dps: float, wolf_dps: float) -> tuple[float, list[dict], list[dict]]:
    """(added DPS, mods, notes) for the Azmerian Wolf's Eternal Hunt. `hunt`: the bridge's minionSkillHit for it;
    `player_dps`: the main skill's DPS a Command press costs time from (0 when a minion is the main skill); `wolf_dps`:
    the wolf's own counted DPS (0 when the score doesn't count it)."""
    hit = number(hunt.get("AverageDamage")) or number(hunt.get("AverageHit"))
    if hit <= 0:
        return 0.0, [], [{"label": "Eternal Hunt (the Azmerian Wolf's Command)", "value": 0,
                          "basis": "PoB gave no hit for the wolf with Eternal Hunt chosen: not counted (0)",
                          "confidence": "LOW"}]
    every = max(number(hunt.get("Cooldown")) or ETERNAL_HUNT_COOLDOWN, COMMAND_CAST)
    lost = player_dps * COMMAND_CAST / every + wolf_dps * min(ETERNAL_HUNT_HOWL / every, 1.0)
    one, seven = hit / every - lost, ETERNAL_HUNT_WOLVES * hit / every - lost
    dps = max(one, 0.0)
    basis = (f"one Spirit Wolf's hit ({hit:,.0f}, PoB's Azmerian Wolf with Eternal Hunt chosen: 1000% of base) per use, "
             f"one use every {every:.3g} s (its cooldown: a press under the button cap), less {lost:,.1f} DPS for the "
             f"Command's {COMMAND_CAST:g} s cast ({player_dps:,.0f} main-skill DPS; whether a Command stops your skill is "
             f"UNVERIFIED) and the wolf's {ETERNAL_HUNT_HOWL:g} s howl ({wolf_dps:,.0f} wolf DPS where the score counts it): "
             f"{one:,.1f} DPS. With all {ETERNAL_HUNT_WOLVES} wolves reaching a lone boss: {seven:,.1f} DPS (on the "
             "user's in-game list). A spirit wolf's hit taken as the Azmerian Wolf's own (UNVERIFIED)")
    if one <= 0:
        basis += "; a net loss with one wolf, so not pressed (0)"
    note = {"label": "Eternal Hunt (the Azmerian Wolf's Command, added to the boss DPS)", "value": round(dps, 1),
            "basis": basis, "confidence": "LOW",
            "play": (f"Command Eternal Hunt each time its cooldown is up (every {every:.3g} s): with it +{dps:,.1f} DPS "
                     f"(one wolf) or +{max(seven, 0.0):,.1f} DPS (all {ETERNAL_HUNT_WOLVES}); without it +0.")}
    return dps, ([added_input("EternalHunt", dps)] if dps > 0 else []), [note]

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
    scale = bosses.threshold_scale(stats)  # (938 / the boss's own threshold: MECHD1, RESEARCH18 #91)
    fill = per_hit * uncold * scale * hits  # bars per second at the plain threshold
    if fill <= 0:
        return 0.0, "Cold hits build no Heavy Stun (the game's Cold text; PoB counts them)"
    t = 1 / fill
    every = repeat_every(t, *HEAVY_STUN_REPEAT)
    basis = (f"{per_hit:.2%} of the boss's Stun bar per hit (PoB){f', {1 - uncold:.0%} of it Cold, which builds none' if uncold < 1 else ''}, "
             f"x{scale:.2f} for {bosses.of(stats).name}'s threshold ({bosses.of(stats).poise}%, the game files; PoB's is 938%), {hits:.2f} hits/s: "
             f"full in {t:.1f} s, then +500% threshold for 16.5 s after each stun: one every {every:.1f} s")
    return 1 / every, basis


def heavy_stunned_share(stats: dict) -> tuple[float, str]:
    """Share of the fight the boss is Heavy Stunned by the main skill's hits (MECHD1; RESEARCH19 #98): one stun every
    1 / heavy_stun_rate s, each lasting the boss's own Heavy Stun duration (BossBase.ot 4 s; Olroth 6.07 s): min(1,
    duration / every). A "vs Heavy Stunned" line counted always on is an overcount once the bar takes over ~0.67 s to
    fill (4 / 6). The stun's own wait isn't taken off the next fill (buildup during a Heavy Stun: UNVERIFIED)."""
    rate, how = heavy_stun_rate(stats)
    if rate <= 0:
        return 0.0, how
    d = bosses.of(stats).heavy_stun_s
    share = min(1.0, d * rate)
    return share, f"{how}; each lasts {d:g} s ({bosses.of(stats).name}): Heavy Stunned {share:.0%} of the fight"



AILMENT_BOSS = HEAVY_STUN_BOSS[0] / HEAVY_STUN_BOSS[1]  # the Arbiter of Ash's threshold over PoB's Pinnacle one (as stun)


def ailment_scale(stats: dict, ailment: str) -> float:
    """PoB's Freeze / Electrocute / Pin buildup x this = the buildup against the boss the calculation is scored against
    (MECHD1): 938 / its own threshold (PoB reads the same threshold for them as for stun: CalcOffence.lua 5570, MEDIUM),
    0 for an ailment the boss can't have (Zarokh: Freeze, Electrocute; AUDIT9 A9-7)."""
    boss = bosses.of(stats)
    return 0.0 if ailment in boss.immune else bosses.threshold_scale(stats)
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
    threshold) x hits per second, with the boss's own threshold (ailment_scale; 0 if it's immune), the threshold x6 for 14 / 15 s
    after each one (AILMENT_REPEAT, repeat_every), and buildup paused while the boss is Frozen / Electrocuted (the user,
    11 Nov 2026; MECH41: before, the rate left the pause out, a small overcount). Buildup decay between hits isn't
    modelled (as Heavy Stun's)."""
    period = immobilise_period(stats, ailment)
    return 1 / period if period > 0 else 0.0


def immobilise_period(stats: dict, ailment: str) -> float:
    """Seconds from one Freeze / Electrocute to the next (0 with no buildup): its duration, then the time to fill the bar
    again at x6 for the rest of the 14 / 15 s after it."""
    fill = hits_per_second(stats) * number(stats.get(f"{ailment}BuildupAvg")) / 100 * ailment_scale(stats, ailment)
    if fill <= 0:
        return 0.0
    d = immobilise_duration(stats, ailment)
    times, window = AILMENT_REPEAT[ailment]
    return d + repeat_every(1 / fill, times, max(window - d, 0.0))


def immobilise_duration(stats: dict, ailment: str) -> float:
    if ailment == "Pin":
        return IMMOBILISE_DURATION["Pin"] * pin_duration_scale(stats)[0]
    mod = (number(stats.get("FreezeDurationMod")) or 1.0) if ailment == "Freeze" else 1.0
    return IMMOBILISE_DURATION[ailment] * mod


# Pin duration (MECH66, AUDIT12 A12-1: an UNDERCOUNT before, the Pin's 3 s never lengthened). Game files
# (stat_descriptions.csd, 26476662): pin_duration_+% "{0}% increased [Pinned|Pin] duration" (passives, a soul core;
# PoB's ModCache holds "10% / 15% / 20% increased Pin duration" and parses none of them into a mod PoB reads: PoB has no
# Pin duration, supports-parts.yaml's support-pin-three-duration, checked ModParser / Calc*.lua by COV-E 12 Nov);
# gem_stat_descriptions.csd support_pin_pin_duration_+%_final "[Pinned|Pin] caused by Supported Skills has {0}% more
# duration" (Pin III; read on the main skill by conditions as stats["_pin_more"]). Increased lines add, the support
# multiplies (the game's usual increased / more rule). A boss's own Pin duration reduction, if any, isn't in the game
# files we read (as Freeze's: MEDIUM).
def pin_duration_scale(stats: dict) -> tuple[float, str]:
    """(x on the Pin's 3 s, how): the build's "X% increased / reduced Pin duration" lines x Pin III's more."""
    inc = duration_scale(stats.get("_texts") or [], "Pin")
    more = 1 + number(stats.get("_pin_more")) / 100
    parts = ([f"{inc - 1:+.0%} from Pin duration lines"] if inc != 1 else []) + (
        [f"{more - 1:+.0%} more from Pin III"] if more != 1 else [])
    return max(0.0, inc * more), "; ".join(parts)


# How long the boss stays Frozen / Electrocuted (MECH38, 11 Nov 2026): the game files' KeywordPopups, "Freeze ... lasts 4
# seconds by default", "Electrocution ... lasts 5 seconds by default" (PoB's Data/Misc.lua: the same). Freeze x PoB's
# FreezeDurationMod; no Electrocute duration modifier exists (ModCache searched for every "Electrocut" line). A boss's own
# duration reduction, if any, isn't in the game files we read (labelled MEDIUM).
# Consuming a Debuff (an Ailment is one: KeywordPopups Ailments) makes the enemy immune to it for 1 s (KeywordPopups Consume,
# game 26476662; poe2db's Keywords page mirrors it; RESEARCH18 #92, HIGH on the text; MECH51)
CONSUME_IMMUNE = 1.0
IMMOBILISE_DURATION = {"Freeze": 4.0, "Electrocute": 5.0, "Pin": 3.0}  # (Pin: KeywordPopups Pinned; x the build's
# Pin duration lines and Pin III, pin_duration_scale: PoB gives no Pin duration)


def immobilised_share(stats: dict, ailment: str) -> tuple[float, str]:
    """Share of the fight the boss is Frozen / Electrocuted by the main skill's hits, and how. One every d + the time to
    fill the bar again, buildup paused while the boss is Frozen / Electrocuted (the user, 11 Nov 2026: it pauses; the game
    files don't say, MEDIUM), the threshold x6 for the rest of the
    14 / 15 s after each one (AILMENT_REPEAT)."""
    fill = hits_per_second(stats) * number(stats.get(f"{ailment}BuildupAvg")) / 100 * ailment_scale(stats, ailment)
    if fill <= 0:
        return 0.0, ""
    d, period = immobilise_duration(stats, ailment), immobilise_period(stats, ailment)
    window = AILMENT_REPEAT[ailment][1]
    if ailment in (stats.get("_consumes") or {}) and (hits := hits_per_second(stats)) > 0:
        # the main skill consumes it: it ends on the next hit, then the boss is immune for 1 s (KeywordPopups Consume;
        # RESEARCH18 #92, MECH51); buildup during that second taken as kept (UNVERIFIED, #99), so the next one comes after
        # max(1 s, the refill)
        last = min(d, 1 / hits)
        times = AILMENT_REPEAT[ailment][0]
        period = last + max(CONSUME_IMMUNE, repeat_every(1 / fill, times, max(window - last, 0.0)))
        share = min(1.0, last / period)
        return share, (f"{fill * 100:.1f}% of the bar per s ({hits:.2f} hits/s, Arbiter of Ash threshold); consumed by "
                       f"{stats['_consumes'][ailment]} on the next hit ({last:.2f} s), then immune {CONSUME_IMMUNE:g} s "
                       f"(KeywordPopups Consume): one every {period:.2f} s: {share:.0%} (buildup during the immunity taken "
                       f"as kept: UNVERIFIED)")
    if side := (stats.get("_side_consumes") or {}).get(ailment):
        # a skill beside the main one consumes it (MECH53; conditions.side_consumes): each Freeze lasts until its next use
        # or d, (1 - e^(-r d)) / r on average with uses random in time; the bar refills from then (buildup paused while
        # Frozen), at least the 1 s immunity (buildup during it taken as kept: UNVERIFIED, #99)
        rate = side[0]
        last = (1 - math.exp(-rate * d)) / rate
        period = last + max(CONSUME_IMMUNE, period - d)
        share = min(1.0, last / period)
        return share, (f"{fill * 100:.1f}% of the bar per s ({hits_per_second(stats):.2f} hits/s, "
                       f"{bosses.of(stats).name}'s threshold); consumed by {side[1]} ({side[2]}): each lasts "
                       f"{last:.2f} s of its {d:g} s on average, then the bar refills (at least the 1 s immunity, "
                       f"KeywordPopups Consume): one every {period:.2f} s: {share:.0%}")
    share = min(1.0, d / period)
    longer = f" ({pin_duration_scale(stats)[1]})" if ailment == "Pin" and pin_duration_scale(stats)[1] else ""
    return share, (f"{fill * 100:.1f}% of the bar per s (PoB's buildup per hit x {hits_per_second(stats):.2f} hits/s, "
                   f"{bosses.of(stats).name}'s threshold); {d:g} s each{longer}, one every {period:.1f} s (x6 threshold for "
                   f"{window:g} s after each, buildup paused while it lasts, the user's word, MEDIUM): {share:.0%}")


# Aggravated Bleeding from the main skill's Attack hits (MECH66; RESEARCH23 #144, FIX_QUEUE "the Aggravate share model";
# before, the ledger's aggravate-bleed-on-hit / -on-crit / -always-on-crit took a flat 2 hits/s, 5 s Bleeds and half of
# hits critical: an OVERCOUNT for builds that rarely crit, and "Attack Hits Aggravate any Bleeding ... older than N
# seconds" counted 0, an UNDERCOUNT). Game files (KeywordPopups Aggravate, 26476662): "[Bleeding] that has been
# Aggravated always treats the target as moving, which causes it to deal 100% extra damage. Once a [Bleeding] [Debuff]
# has been Aggravated, it will remain Aggravated until its duration expires ... Aggravating some or all of them will
# have no effect on new [Bleeding] [Debuff|Debuffs] applied afterwards". Stat texts (stat_descriptions.csd):
# base_aggravate_bleeding_on_attack_hit_chance_% "{0}% chance to Aggravate Bleeding on targets you Hit with Attacks",
# local_aggravate_bleeding_on_hit_chance_% "{0}% chance to Aggravate Bleeding on Hit" (a weapon's: its Attacks),
# aggravate_bleeding_on_attack_crit_chance_% "{0}% chance to Aggravate Bleeding on targets you Critically Hit with
# Attacks" (100: "Aggravate Bleeding on targets you Critically Hit with Attacks"), aggravate_bleeding_older_than_ms_on_hit
# "Attack Hits Aggravate any Bleeding on targets which is older than {0} second(s)" (Deep Wounds; an Ulaman glove suffix).
# A Bleed of duration D is Aggravated from the first aggravating hit after it is inflicted to its end, so its extra
# damage is E[(D - A)+] / D, A the wait (hits random in time: Poisson at the main skill's hits/s, MEDIUM); the hit that
# inflicts a Bleed is taken not to Aggravate that same Bleed (the lower reading, UNVERIFIED). Only the main skill's Attack
# hits count (other skills' hits: an undercount, LOW). Counted as "X% more Damage with Bleeding" (the ledger's form; PoB's
# own Aggravated / Moving bonus is AilmentMagnitude MORE 100 for Bleed, CalcSetup 1000). Other Aggravate sources (on Pin,
# Knockback, Empowered Attacks, Heavy Stunned enemies, Jagged Ground, Cursed / Pinned targets, Presence) aren't read here.
AGGRAVATE_HIT = re.compile(r"^(\d+(?:\.\d+)?)% chance to Aggravate Bleeding on (?:targets you Hit with Attacks|Hit)$", re.I)
AGGRAVATE_CRIT = re.compile(r"^(?:(\d+(?:\.\d+)?)% chance to )?Aggravate Bleeding on targets you Critically Hit with "
                            r"Attacks$", re.I)
AGGRAVATE_OLDER = re.compile(r"^Attack Hits Aggravate any Bleeding on targets which is older than (\d+(?:\.\d+)?) "
                             r"seconds?$", re.I)
AGGRAVATE_EMPOWERED = re.compile(r"^(\d+(?:\.\d+)?)% chance to Aggravate Bleeding on targets you Hit with Empowered "
                                 r"Attacks$", re.I)  # (aggravate_bleeding_on_exerted_attack_hit_chance_%; MECH66: at the
# build's own Empowered uses, Assumptions.empowered_uses over every Empowering skill, not pob-gaps-boss.yaml's flat 10%)
AGGRAVATE_ALWAYS = re.compile(r"^Bleeding you inflict is Aggravated$", re.I)
AGGRAVATE_MODEL = "aggravate"  # (custom-models.yaml entries feeding these lines to aggravate_model)


def aggravated_share(duration: float, rate: float, older: float | None, hits: float) -> float:
    """Share of a Bleed's duration it is Aggravated: aggravating hits at `rate`/s from the start, and (Deep Wounds) any
    Attack hit (`hits`/s) once the Bleed is older than `older` s. E[(D - A)+] / D = (1 / D) x the integral over
    0..D of P(A <= t)."""
    if duration <= 0:
        return 0.0
    steps, total = 400, 0.0
    for i in range(steps):
        t = (i + 0.5) * duration / steps
        late = math.exp(-hits * (t - older)) if older is not None and t >= older else 1.0
        total += 1 - math.exp(-rate * t) * late
    return total / steps


def aggravate_model(texts: list[str], stats: dict, main_types: set[str], moving: bool = False,
                    empowered: tuple[float, str] | None = None) -> tuple[list[dict], list[dict]]:
    """The main skill's Bleeding damage x (1 + its Aggravated share) from the build's Aggravate lines, at its own hits/s,
    crit chance and Bleed duration (PoB's BleedDuration). Lines another ledger entry already translates are left to it."""
    from builder import ledger

    def ours(text: str) -> bool:
        found = ledger.translate(text)
        return found is None or found[0].raw.get("model") == AGGRAVATE_MODEL

    on_hit, on_crit, older, on_emp, used = [], [], [], [], []
    for text in (t.strip() for t in texts):
        if (m := AGGRAVATE_EMPOWERED.match(text)) and ours(text):
            on_emp.append(float(m.group(1)) / 100)
        elif (m := AGGRAVATE_HIT.match(text)) and ours(text):
            on_hit.append(float(m.group(1)) / 100)
        elif (m := AGGRAVATE_CRIT.match(text)) and ours(text):
            on_crit.append(float(m.group(1) or 100) / 100)
        elif (m := AGGRAVATE_OLDER.match(text)) and ours(text):
            older.append(float(m.group(1)))
        else:
            continue
        used.append(text)
    if not used:
        return [], []
    label = "Aggravated Bleeding from Attack hits"
    if "Attack" not in main_types:
        return [], [{"label": label, "value": 0, "confidence": "MEDIUM",
                     "basis": f"{'; '.join(used)}: the main skill isn't an Attack, so its hits Aggravate nothing (other "
                              "skills' hits aren't counted: an undercount if they Attack and Bleed the boss)"}]
    if moving or any(AGGRAVATE_ALWAYS.match(t.strip()) for t in texts):
        return [], [{"label": label, "value": 0, "confidence": "HIGH",
                     "basis": f"{'; '.join(used)}: the build's Bleeding is already Aggravated (or the boss set as moving) "
                              "in PoB, which gives it the 100% extra; nothing more to add"}]
    hits, crit = hits_per_second(stats), min(max(number(stats.get("CritChance")) / 100, 0.0), 1.0)
    duration = number(stats.get("BleedDuration"))
    miss = math.prod(1 - p for p in on_hit) * ((1 - crit) + crit * math.prod(1 - q for q in on_crit))
    # Empowered hits (MECH66): the main skill's Empowered uses x its hit chance roll the Empowered lines too
    emp_rate, emp_how = empowered or (0.0, "")
    emp_hits = min(emp_rate * number(stats.get("HitChance")) / 100, hits) if on_emp else 0.0
    emp_miss = miss * math.prod(1 - p for p in on_emp)
    rate = (hits - emp_hits) * (1 - miss) + emp_hits * (1 - emp_miss)
    gap = min(older) if older else None
    share = aggravated_share(duration, rate, gap, hits)
    how = (f"{'; '.join(used)}: the main skill's {hits:.2f} Attack hits/s, {crit:.0%} critical, Aggravate a hit's Bleeds "
           f"with {1 - miss:.0%} chance ({rate:.2f}/s)" + (f", and any hit once a Bleed is {gap:g} s old" if older else "")
           + (f"; {emp_hits:.3f} Empowered hits/s ({emp_how or 'no skill of the build Empowers it'})" if on_emp else "")
           + f"; a {duration:.2f} s Bleed is Aggravated {share:.0%} of its time (from the first such hit after it is "
           "inflicted, hits random in time; the hit that inflicts it taken not to Aggravate it: UNVERIFIED, the lower "
           "reading): 100% extra Bleeding damage for that share (KeywordPopups Aggravate)")
    note = {"label": label, "value": round(share, 3), "confidence": "MEDIUM", "basis": how,
            "play": "Keep attacking: each Bleed is Aggravated only by a hit after it was inflicted"}
    if share <= 0:
        return [], [note]
    return [line("1% more Damage with Bleeding", 100 * share)], [note]


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
    return share, (f"{rate * 60:.1f} Electrocutes of the boss per minute (main skill's buildup, {bosses.of(stats).name}'s threshold), "
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
# The boss's hits come one per skill use, bosses.benefit_skill_time (MECH-D's per-boss HIT_EVERY, 1.1-2.3 s, LOW; a
# benefit, so not PoB's faster defence cadence; MECHC11, was 0.7 s); a parry waits half that on average.
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
# only one hit every enemySkillTime, 0.55-0.7 s by boss since MECHD4), so the cadence is assumed: one telegraphed hit every 5 s, which the user
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
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
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
            parts.append(f"{ailment} {r:.3f}/s (PoB's buildup per hit, {bosses.of(stats).name}'s threshold, x6 for "
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
# MECHB3 (RESEARCH11 guided-hand-boost): KeywordPopups AncestralBoost (build 26476662): "An Ancestrally Boosted Slam has
# 30% more damage and 25% increased area of effect, and an Ancestrally Boosted Strike will target 2 additional Enemies.
# Triggered Skills cannot be Ancestrally Boosted. Ancestrally Boosted Attacks count as being Empowered." So a Slam main
# skill also gets ANCESTRAL_SLAM_MORE more on the boosted share (PoB adds it only inside its Fist of War / Ancestral Call /
# Ancestral Empowerment uptimes, CalcOffence 3691-3717; never for Guided Hand), and a triggered main skill gets nothing
# (checked: KeywordPopups AncestralBoost's own words; RESEARCH11; poe2db not checked). Not counted (labelled): the 25%
# area (it matters only where the damage depends on the area: UNDERCOUNT, small), and the Empowered-only lines on the
# same share (MECH-C's kw-empowered model takes this share as one source; until then an UNDERCOUNT). The boosted hit's
# 30% increased and 30% more are counted as (1 + 0.3 s / (1 + inc)) x (1 + 0.3 s), a little under the exact average.

ANCESTRAL_SLAM_MORE = 30  # AncestralBoost: "30% more damage" for a Slam (KeywordPopups; PoB's AncestralBoostMoreDamage 30)
GUIDED_HAND = "GuidedHand"
BOOSTED_LINE = re.compile(r"^Ancestrally Boosted Attacks deal (\d+)% increased Damage$", re.I)


def guided_hand_model(inputs: Inputs, lines: list[dict], stats: dict, main: Skill | None,
                      slam: bool = False) -> tuple[list[dict], list[dict]]:
    if not inputs.has(GUIDED_HAND) or main is None or not main.attack:
        return [], []
    if main.triggered:
        return [], [{"label": "Guided Hand: Ancestrally Boosted attacks after a Heavy Stun", "value": "0 (triggered)",
                     "basis": f"{main.name} is triggered, and \"Triggered Skills cannot be Ancestrally Boosted\" "
                              "(KeywordPopups AncestralBoost)", "confidence": "theory", "entry": "guided-hand-boost"}]
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
    mods = [line("1% increased Attack Damage", boost / UNIT, when=when, per=GUIDED_HAND),
            derived(GUIDED_HAND, "guidedHand", share * UNIT, boss=bosses.threshold_scale(stats),
                    physical=HEAVY_STUN_PHYSICAL, times=HEAVY_STUN_REPEAT[0], window=HEAVY_STUN_REPEAT[1])]
    if slam:
        mods.append(line("1% more Damage", ANCESTRAL_SLAM_MORE / UNIT, when=when, per=GUIDED_HAND, skill=main.name))
    return mods, [{
        "label": "Guided Hand: Ancestrally Boosted attacks after a Heavy Stun",
        "value": f"{share:.1%} of attacks boosted ({boost}% increased Damage on those"
                 + (f", and {ANCESTRAL_SLAM_MORE}% more: a Slam" if slam else "") + ")",
        "basis": f"{basis}; against {speed:.2f} attacks per second"
                 + ("; a boosted Slam's 25% increased area and the Empowered-only lines on those attacks aren't counted"
                    if slam else "; the Empowered-only lines on those attacks aren't counted"),
        "play": "A build made to stun (more stun buildup, Physical melee slams) heavy-stuns more often and boosts more attacks.",
        "confidence": "theory", "entry": "guided-hand-boost"}]



# Ancestral Boost's sources PoB leaves unparsed (MECHB18; RESEARCH25 ancestral-boost-sources.md, INDEX #150) -------------
# KeywordPopups (build 26476662): AncestralBoost "An Ancestrally Boosted [Slam] has 30% more damage and 25% increased area
# of effect, and an Ancestrally Boosted [Strike] will target 2 additional Enemies. [Trigger|Triggered Skills] cannot be
# Ancestrally Boosted. Ancestrally Boosted [Attack|Attacks] count as being [Empowered]."; AncestralBoostSpell "An
# Ancestrally Boosted [Spell] has 30% more damage and 25% increased area of effect. [Trigger|Triggered Skills] cannot be
# Ancestrally Boosted." Two sources PoB leaves as unparsed text, so it never applies them (ModCache 7429, 7430, 7432, 9621):
# - UniqueAncestralBoostEveryXAttacksWhileShapeshifted1, "Every second Slam Skill you use while Shapeshifted is Ancestrally
#   Boosted" (and the same for Strikes: 2 extra targets, so nothing against one boss; RESEARCH25, HIGH): half of a
#   Shapeshift Slam main skill's uses;
# - Kaom's Blessing, "The next Fire Spell you cast yourself after using a Warcry is Ancestrally Boosted": one Fire Spell
#   cast per Warcry use (the build's Warcries at their own use rates: cooldown, duration or the search's press choice;
#   none: 0), so min(1, warcries/s / casts/s) of a Fire Spell main skill's casts, not cast by a totem or a trigger.
# A Slam's sources combine as one boost per use (RESEARCH25: "two sources on one slam give one 30%"): an every-second
# source (Ancestral Empowerment, which PoB parses, or this unique) boosts half the slams; Fist of War boosts one slam per
# its interval T. Whether Fist of War's charge is spent on a slam the every-second source already boosts is UNVERIFIED
# (RESEARCH #153); the lower reading is taken, as RESEARCH25 says: share = 0.5 + 0.5 x min(1, 1 / (T x slams/s)) (LOW).
# PoB adds the two (CalcOffence calcCombinedAncestralBoost: min(50% + Fist of War's, 100%)), the upper reading: where both
# are on, this model's factor is below x1 (a possible OVERCOUNT in PoB, labelled, with both readings in the note and a
# "with the higher reading" play row). Since MECHB19 (the manager's ruling, 13 Nov: overcounts first, the user's
# conservative-numbers rule) the lower reading is also applied to Ancestral Empowerment + Fist of War without the unique
# (PoB: CalcOffence 3696-3701, calcCombinedAncestralBoost with a 50% override plus Fist of War's share), until RESEARCH
# #153's in-game check settles it. The
# 30% more (x2 with Fist of War III: PoB's AncestralBoostMoreDamage multiplier) is applied as
# (1 + M x share) / (1 + M x PoB's share) on the main skill's Damage; "Ancestrally Boosted Attacks deal N% increased
# Damage" lines get the same share difference as increased Attack Damage. Not counted (labelled): the 25% increased
# area, and the Empowered-only lines on the extra share (MECH-C's kw-empowered); Guided Hand's share (guided_hand_model)
# is added on top, not merged (a small OVERCOUNT where both are taken, at most Guided Hand's own share).
ANCESTRAL_FIELDS = ["Speed", "FistOfWarCooldown", "FistOfWarDamageMultiplier", "AncestralEmpowermentDamageMultiplier",
                    "AncestralEmpowermentUptimeRatio", "AncestralEmpowermentCombinedUptimeRatio", "FistOfWarUptimeRatio"]
SHAPESHIFT_EVERY_SECOND_SLAM = re.compile(r"Every second Slam Skill you use while Shapeshifted is Ancestrally Boosted",
                                          re.I)
EMPOWERMENT_EVERY_SECOND_SLAM = re.compile(r"^Every second Slam Skill you use yourself is Ancestrally Boosted$", re.I)
KAOMS_BLESSING = re.compile(r"^The next Fire Spell you cast yourself after using a Warcry is Ancestrally Boosted$", re.I)
ANCESTRAL_SPELL_MORE = 30  # AncestralBoostSpell: "30% more damage" (KeywordPopups)


def ancestral_boost_model(texts: list[str], stats: dict, skills: list[Skill], main: Skill | None, main_types: set[str],
                          warcries: tuple[float, str] = (0.0, "")) -> tuple[list[dict], list[dict]]:
    """The two unparsed Ancestral Boost sources: the Shapeshifted unique's every second Slam and Kaom's Blessing's Fire
    Spell after a Warcry (`warcries`: the build's Warcry uses per second, and how). Mods on the main skill, and notes."""
    if main is None or not texts:
        return [], []
    label = "Ancestral Boost (sources PoB doesn't parse)"
    unique = any(SHAPESHIFT_EVERY_SECOND_SLAM.search(t) for t in texts)
    kaom = any(KAOMS_BLESSING.match(t.strip()) for t in texts)
    # Ancestral Empowerment with Fist of War: PoB's combined share is the upper reading (MECHB19, the manager's ruling:
    # the lower reading until RESEARCH #153's in-game check)
    empowerment = (any(EMPOWERMENT_EVERY_SECOND_SLAM.match(t.strip()) for t in texts)
                   or number(stats.get("AncestralEmpowermentCombinedUptimeRatio")) > 0)
    cooldown = number(stats.get("FistOfWarCooldown"))
    both = empowerment and cooldown > 0
    if not unique and not kaom and not both:
        return [], []
    if main.triggered:
        if not unique and not kaom:
            return [], []
        return [], [{"label": label, "value": "0 (triggered)", "confidence": "theory", "entry": "ancestral-boost",
                     "basis": f"{main.name} is triggered: \"Triggered Skills cannot be Ancestrally Boosted\" (KeywordPopups)"}]
    speed = number(stats.get("Speed"))
    if speed <= 0:
        return [], []
    if "Slam" in main_types and main.attack:
        if cast_by_totem(skills, main):
            return [], []
        shapeshifted = "Shapeshift" in main_types
        unique_note = ([{"label": label, "value": "0", "confidence": "theory", "entry": "ancestral-boost",
                         "basis": f"{main.name} isn't a Shapeshift skill, so it isn't used while Shapeshifted"}]
                       if unique and not shapeshifted else [])
        unique = unique and shapeshifted
        if not unique and not both:
            return [], unique_note
        fist = min(1.0, 1 / (cooldown * speed)) if cooldown > 0 else 0.0
        share = 0.5 + 0.5 * fist
        pob = max(number(stats.get(k)) for k in ("AncestralEmpowermentUptimeRatio",
                                                  "AncestralEmpowermentCombinedUptimeRatio", "FistOfWarUptimeRatio")) / 100
        if empowerment and cooldown > 0:  # (PoB hides Fist of War's own ratio once it's combined)
            pob = max(pob, min(1.0, 0.5 + fist))
        elif cooldown > 0:
            pob = max(pob, fist)
        elif empowerment:
            pob = max(pob, 0.5)
        more = (number(stats.get("AncestralEmpowermentDamageMultiplier")) or number(stats.get("FistOfWarDamageMultiplier"))
                or ANCESTRAL_SLAM_MORE / 100)
        if abs(share - pob) < 1e-9:
            return [], unique_note
        factor = (1 + more * share) / (1 + more * pob)
        boost = sum(int(m.group(1)) for t in texts if (m := BOOSTED_LINE.match(t.strip())))
        mods = [raw_mod("Damage", "MORE", 100 * (factor - 1), main.name)]
        if boost:
            mods.append(line("1% increased Attack Damage", boost * (share - pob), skill=main.name))
        sources = (["the Shapeshifted unique's every second Slam"] if unique else []) + (
            ["Ancestral Empowerment's every second Slam" + (" (the same half, taken as overlapping: UNVERIFIED)"
                                                            if unique else "")]
            if empowerment else []) + ([f"Fist of War once per {cooldown:g} s"] if cooldown > 0 else [])
        upper = max(pob, min(1.0, 0.5 + fist)) if cooldown > 0 else pob
        upper_factor = (1 + more * upper) / (1 + more * pob)
        if unique:
            play = (f"Without the unique: x1.000. With it: every second Slam you use in your Shapeshifted form is boosted, "
                    f"x{factor:.3f}")
        else:
            play = (f"Ancestral Empowerment with Fist of War: x{factor:.3f} at the lower reading (Fist of War's charge "
                    f"spent on a Slam Ancestral Empowerment already boosts, {share:.0%} boosted)")
        if cooldown > 0:
            play += (f"; with the higher reading (each boost on a different Slam, {upper:.0%} boosted, PoB's): "
                     f"x{upper_factor:.3f} (RESEARCH #153, an in-game check)")
        return mods, unique_note + [{
            "label": label,
            "value": f"{share:.0%} of {main.name}'s uses boosted ({more:.0%} more), against PoB's {pob:.0%}: x{factor:.3f}",
            "basis": f"{'; '.join(sources)}; one boost per use; {speed:.2f} uses/s"
                     + ("; Fist of War's charge taken as spent on an already boosted slam (the lower reading, UNVERIFIED; "
                        f"the upper reading, PoB's, is {min(1.0, 0.5 + fist):.0%})" if cooldown > 0 else "")
                     + "; the 25% increased area and the Empowered-only lines on the extra share aren't counted",
            "play": play,
            "confidence": "LOW" if cooldown > 0 else "theory", "entry": "ancestral-boost"}]
    if kaom and main.spell and "Fire" in main_types:
        if cast_by_totem(skills, main):
            return [], [{"label": label, "value": "0 (totem)", "confidence": "theory", "entry": "ancestral-boost",
                         "basis": f"Kaom's Blessing boosts a Fire Spell \"you cast yourself\"; {main.name} is cast by a totem"}]
        rate, how = warcries
        share = min(1.0, rate / speed)
        if share <= 0:
            return [], [{"label": label, "value": "0", "confidence": "theory", "entry": "ancestral-boost",
                         "basis": f"Kaom's Blessing: no Warcry use to follow ({how or 'no Warcry in the build'}: no source, 0)"}]
        factor = 1 + ANCESTRAL_SPELL_MORE / 100 * share
        return [raw_mod("Damage", "MORE", 100 * (factor - 1), main.name)], [{
            "label": label, "value": f"{share:.1%} of {main.name}'s casts boosted ({ANCESTRAL_SPELL_MORE}% more): x{factor:.3f}",
            "basis": f"Kaom's Blessing: one Fire Spell cast per Warcry use, {rate:.3f} Warcries/s ({how}) against "
                     f"{speed:.2f} casts/s; the 25% increased area isn't counted",
            "play": f"Without a Warcry: x1.000. With one: cast {main.name} right after each Warcry ({rate:.3f}/s), "
                    f"x{factor:.3f}",
            "confidence": "theory", "entry": "ancestral-boost"}]
    return [], []

# Freezing Salvo's Seals (MECHB18; FIX_QUEUE "Salvo seals") ---------------------------------------------------------------
# The game (ActiveSkills freezing_salvo, build 26476662): "Passively gains Seals over time. Using the skill fires a
# projectile, and breaks the Seals, Repeating for each of them, and for each additional projectile the skill is given."
# Its stat set: base_skill_seal_gain_interval_ms 750 (every level), base_maximum_seals_for_skill 10,
# skill_rapid_fire_repeats_per_broken_seal 1, base_modifiers_to_number_of_projectiles_instead_apply_to_repeats.
# KeywordPopups Repeat: "Skills take 5% longer to perform for each time they Repeat"; Seal: "Only skills you use yourself
# can be Sealed". PoB reads none of the three Seal stats for it (SkillStatMap maps only base_maximum_seals_for_skill, to
# SealCount, which CalcOffence 1291-1323 uses only under Unleash's DamageSeal / Expand's AreaSeal flags; checked: PoB src
# for the two other stat ids, 0 readers), and leaves "Sealed Skills have N% increased Seal gain frequency" and "+N to
# maximum Seals" unparsed (ModCache), so it scores one arrow per use: an UNDERCOUNT. Here, used as often as it can be:
# with Seals gained at g a second, e extra projectiles (each a Repeat) and a use time t0 (PoB's 1 / Speed), each use
# breaks s = min(max Seals, g x t0 x (1 + 0.05 (s + e))) Seals, and deals 1 + s + e hits in t0 x (1 + 0.05 (s + e)):
# x (1 + s + e) / (1 + 0.05 (s + e)) on PoB's one hit per t0. Seals are taken as gained during the use too (UNVERIFIED:
# no game text says the gain pauses). Not counted (labelled): the extra Repeat per Ice Fragment in its area (needs
# another skill's Ice Fragments under the boss), a triggered or Mirage copy's use (no Seals: "you use yourself").
FREEZING_SALVO = "Freezing Salvo"
SEAL_INTERVAL_STAT = "base_skill_seal_gain_interval_ms"
SEAL_MAX_STAT = "base_maximum_seals_for_skill"
SEAL_REPEATS_STAT = "skill_rapid_fire_repeats_per_broken_seal"
SEAL_FREQUENCY_LINE = re.compile(r"^Sealed Skills have (\d+(?:\.\d+)?)% (increased|reduced) Seal gain frequency$", re.I)
SEAL_MAX_LINE = re.compile(r"^Sealed Skills have \+(\d+) to maximum Seals$", re.I)
REPEAT_TIME = 0.05  # KeywordPopups Repeat: "5% longer to perform for each time they Repeat"


def freezing_salvo_model(main: Skill | None, stats: dict, texts: list[str],
                         level: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """Freezing Salvo's Seals and extra projectiles as Repeats: a DPS factor on the main skill, and a note."""
    if main is None or main.name != FREEZING_SALVO:
        return [], []
    label = "Freezing Salvo: Seals and extra projectiles as Repeats"
    if main.triggered:
        return [], [{"label": label, "value": "x1 (triggered)", "confidence": "theory", "entry": "freezing-salvo-seals",
                     "basis": "\"Only skills you use yourself can be Sealed\" and triggered skills can't Repeat (KeywordPopups)"}]
    speed = number(stats.get("Speed"))
    interval = number(level.get(SEAL_INTERVAL_STAT)) / 1000
    per_seal = number(level.get(SEAL_REPEATS_STAT)) or 1.0
    if speed <= 0 or interval <= 0:
        return [], []
    increased = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                    for t in texts if (m := SEAL_FREQUENCY_LINE.match(t.strip())))
    most = number(level.get(SEAL_MAX_STAT)) + sum(int(m.group(1)) for t in texts if (m := SEAL_MAX_LINE.match(t.strip())))
    gain = max(0.0, 1 + increased / 100) / interval
    extra = max(0.0, number(stats.get("ProjectileCount")) - 1)
    t0 = 1 / speed
    denominator = 1 - REPEAT_TIME * gain * t0 * per_seal
    seals = most if denominator <= 0 else min(most, gain * t0 * (1 + REPEAT_TIME * extra) / denominator)
    repeats = seals * per_seal + extra
    factor = (1 + repeats) / (1 + REPEAT_TIME * repeats)
    return [raw_mod("DPS", "MORE", 100 * (factor - 1), main.name)], [{
        "label": label,
        "value": f"{seals:.2f} Seals and {extra:g} extra projectiles a use: {1 + repeats:.2f} arrows in "
                 f"{1 + REPEAT_TIME * repeats:.2f}x the use time, x{factor:.3f}",
        "basis": f"a Seal every {interval / max(1e-9, 1 + increased / 100):.3f} s ({interval:g} s"
                 + (f", {increased:+g}% Seal gain frequency" if increased else "") + f"), at most {most:g}; "
                 f"{speed:.2f} uses/s before the Repeats' 5% longer use each; Seals gained during a use (UNVERIFIED); "
                 "the extra Repeat per Ice Fragment isn't counted (needs another skill's Ice Fragments)",
        "play": "Fire Freezing Salvo as often as it can be used: each use breaks the Seals gained since the last one. "
                f"Without the Seals (PoB): x1.000; with them: x{factor:.3f}",
        "confidence": "theory", "entry": "freezing-salvo-seals"}]

# Thorns Retaliation: the boss's melee share (MECHB19, for the Thorns model to come; MECH-D is paused, so the share is kept
# here, read-only from RESEARCH #42, research/boss-life-and-hit-kinds.md). KeywordPopups Thorns: Thorns Retaliates only
# against "[Melee] [Attack] [Hit|Hits]" (Crown of the Pale King: "Thorns can Retaliate against all Hits"; RESEARCH #149).
# The game files hold no monster AI or use rates, so each value is the share of a boss's distinct hit-dealing SKILLS
# (a skill mix, LOW as a hit rate), between two bounds: Melee-typed attacks (lower) and those plus the slam / slash
# geometry attacks, which carry Attack but not Melee (upper; whether they count as Melee is RESEARCH #38, open).
# Thorns gains read the lower bound (the user's conservative-numbers rule), drawbacks ("damage taken from Melee
# Attacks") the upper. Bosses research #42 didn't read: None, so a gain is 0 there with a note (no flat guess); the
# worst-case preset takes the lowest lower bound and the highest upper bound over the bosses read.
# Counts (hit skills; Melee-typed; geometry attacks): Arbiter of Ash 34; 4; 14. Xesht 12; 1; 3. The King in the Mists
# ph2 6; 1; 3 and ph3 8; 1; 1 (lower: ph3's 1/8, upper: ph2's 4/6). Zarokh 14; 0; 2. Arbiter of Divinity 52; 6; 26.
THORNS_MELEE_SHARE: dict[str, tuple[float, float] | None] = {
    "Arbiter of Ash": (4 / 34, 18 / 34),
    "Xesht, We That Are One": (1 / 12, 4 / 12),
    "The King in the Mists": (1 / 8, 4 / 6),
    "Zarokh, the Temporal": (0.0, 2 / 14),
    "Arbiter of Divinity": (6 / 52, 32 / 52),
}
THORNS_WORST = "Pinnacle (worst case)"


def boss_melee_share(boss: str, gain: bool = True) -> tuple[float, str]:
    """The share of a boss preset's hits that are melee attacks (THORNS_MELEE_SHARE above): the lower bound for a gain
    (Thorns Retaliation), the upper for a drawback, and how. A boss not read: 0 for a gain, with the note."""
    read = {k: v for k, v in THORNS_MELEE_SHARE.items() if v is not None}
    if boss == THORNS_WORST:
        value = min(v[0] for v in read.values()) if gain else max(v[1] for v in read.values())
        which = min(read, key=lambda k: read[k][0]) if gain else max(read, key=lambda k: read[k][1])
        return value, (f"{value:.0%} of the boss's hit skills are melee attacks: the worst case, {which}'s "
                       f"{'lower' if gain else 'upper'} bound (RESEARCH #42, a skill mix, LOW as a hit rate)")
    bounds = THORNS_MELEE_SHARE.get(boss)
    if bounds is None:
        return 0.0, (f"{boss}'s melee share isn't read yet (RESEARCH #42 covers 5 bosses): 0 for a gain, no flat guess"
                     if gain else f"{boss}'s melee share isn't read yet: the worst case's upper bound is the safe one")
    low, high = bounds
    value = low if gain else high
    return value, (f"{value:.0%} of {boss}'s hit skills are melee attacks ({'lower' if gain else 'upper'} bound: "
                   f"Melee-typed {low:.0%}, with the slam / slash geometry attacks {high:.0%}; whether those count as "
                   "Melee is RESEARCH #38, open; a skill mix from the game files, LOW as a hit rate)")

# Thorns damage on the boss (MECHB22; plans/term-thorns-damage-to-enemies.md, RESEARCH #149 thorns-trigger-sources-r25.md).
# PoB builds its own "Thorns" group (CalcSetup 2027-2070, skill ThornsPlayer, fromItem) when the build has Thorns damage,
# and scores one Thorns hit against the boss there, but no DPS field counts it: it is never in Full DPS (CalcSetup leaves
# includeInFullDPS unset; assumptions.pressed_group skips item groups) and never the main skill (probe POE2T1087: a
# Boneshatter Titan with "15 to 25 Physical Thorns damage" and "100% increased Thorns damage": FullDPS 376.99 with and
# without the belt; the Thorns group's AverageHit 10, Speed PoB's placeholder 1). So every Thorns hit was an
# UNDERCOUNT, none an overcount. Counted here, as an added damage source (added_input, fixed at the resolve like
# Eternal Hunt), at PoB's Thorns hit (its per-type averages at its crit chance, the boss's mitigation in them) x:
#   - Retaliation (KeywordPopups Thorns: "you inherently Retaliate against [Melee] [Attack] [Hit|Hits]"): the boss's
#     hits that land a second (bosses.benefit_skill_time, one hit per damaging use, LOW; boss_hits.land_share: evaded
#     and Blocked hits don't count, whether a Blocked hit Retaliates is UNVERIFIED, so a possible undercount) x its
#     melee share (boss_melee_share's lower bound: 0 on the worst-case preset, where Zarokh has no Melee-typed hits),
#     or every landed hit with "Thorns can Retaliate against all Hits" (Crown of the Pale King, thorns_proc_off_any_hit).
#   - "N% chance to deal your Thorns Damage to Enemies you Hit with Melee Attacks" (Prized Pain, Loreweave,
#     melee_attack_deal_thorns_damage_chance_%_on_hit): N% of the main skill's hits a second when it is a Melee Attack.
#   - "Deal your Thorns damage to enemies you Critically Hit with Melee Attacks" (deal_thorns_damage_on_melee_crit,
#     the hand wraps): the main skill's crits a second when it is a Melee Attack.
#   Both on the main skill only (other melee skills' hits: an undercount); they count beside each other and beside
#   Retaliation (no "instead" in either text). No limit per hit, per second or per enemy (checked, AUDIT30 for RESEARCH
#   #154: KeywordPopups Thorns / ThornsRetaliation / RetaliateAgainstAll, the 125 Stats ids holding "thorn" or
#   "retaliat", GrantedEffects (no player Thorns skill: PoB's ThornsPlayer is its own); poe2db, patch notes 0.1-0.5:
#   none in the data; a limit the data doesn't show is UNVERIFIED in game).
#   - Barbs I / II / III (MECHB23; gem_stat_descriptions: "Deal your Thorns damage to the next {0} enemies Hit by
#     Supported Skills after you Retaliate with Thorns", 4 / 6; III "twice to the next enemy"): PoB adds a hidden
#     "Barbs I/II/III" Thorns skill to the supported group (Modules/Data.lua 1015-1029) and, with that group in Full DPS,
#     scores it at its default Speed 1, one Thorns hit a second whatever the Retaliations (probe POE2T1094: a Boneshatter
#     Titan's FullDPS 376.99 -> 381.99 with Barbs I and a Thorns belt, PoB's Barbs hit 5): an OVERCOUNT, 0 on the
#     worst-case preset where nothing Retaliates. So PoB's entry is scaled to 0 (Objective.entry_scales) and Barbs is
#     counted here: min(Retaliations/s x N, the supported main skill's hits/s) x (2 for III) x PoB's Barbs hit (the same
#     whether each Retaliation refreshes the N or adds to it, at a steady rhythm). N counts hits on the one boss: the
#     stat id is deal_thorns_damage_on_hit_for_X_hits_after_thorns_trigger ("for X hits"), the text says "enemies"
#     (UNVERIFIED which the game counts on one boss: the note gives the once-per-Retaliation row). Only when Barbs
#     supports the main skill, a non-triggered Melee Attack (Barbs on another group: an undercount, named). Barbs' own
#     Thorns hits aren't Retaliation (KeywordPopups ThornsRetaliation), so they feed no more Barbs.
# Not counted yet (undercounts, labelled in the note): legacy Prized Pain's "Deal your Thorns Damage to Enemies you Stun
# with Melee Attacks" (removed from new drops in 0.5.0, poe2db), Barbs on a skill other than the main one, Brambleslam's
# Brambles (PoB scores them in Full DPS at its own Speed, not the slams' rate: probe POE2T1097, open). (checked: every Stats.json id holding
# "thorn" (125), and which Mods / GrantedEffectStatSets / PassiveSkills rows use the proc stats: the sources above are
# every row there; PoB's Uniques for the line texts; the second source RESEARCH #149's poe2db reads.)
THORNS_GROUP = "Thorns"
THORNS_ON_HIT = re.compile(r"(\d+(?:\.\d+)?)% chance to deal your Thorns Damage to Enemies you Hit with Melee Attacks",
                           re.IGNORECASE)
THORNS_ON_CRIT = "deal your thorns damage to enemies you critically hit with melee attacks"
THORNS_ANY_HIT = "thorns can retaliate against all hits"
THORNS_NOT_COUNTED = {
    "deal your thorns damage to enemies you stun with melee attacks": "legacy Prized Pain's Thorns on melee Stun",
}
THORNS_FIELDS = ["enemySkillTime", "EvadeChance", "EffectiveBlockChance", "Tribute"]
# Toll of Agony (MECHB23; PassiveSkills AbyssJewelHistoricOverrideNotable_T_T_12, stat
# thorns_proc_chance_%_against_non_melee_hits_if_you_have_at_least_200_tribute 25): "25% chance for Thorns to Retaliate
# against Non-Melee Hits if you have at least 200 Tribute" (checked: the Stats ids holding "thorn" with "proc",
# "retaliat" or "non_melee" are this and thorns_proc_off_any_hit, and their only Mods / PassiveSkills carriers are
# UniqueThornsOnAnyHit1 and this notable; poe2db Toll_of_Agony, AUDIT20 A20-2). The chance x the boss's non-melee
# landed hits (1 - the melee share's UPPER bound: the gain's lower bound; 0 for a boss whose share isn't read), when
# PoB's Tribute (CalcPerform 493) reaches the threshold.
THORNS_NON_MELEE = re.compile(r"(\d+(?:\.\d+)?)% chance for Thorns to Retaliate against Non-Melee Hits if you have at least "
                              r"(\d+) Tribute", re.IGNORECASE)
BARBS = {"Barbs I": (4, 1), "Barbs II": (6, 1), "Barbs III": (1, 2)}  # (hits after each Retaliation, Thorns per hit)
# Quill Burst (MECHB23; ActiveSkills triggered_spiked_gauntlets: "Deal your Thorns Damage in an Area. Cannot Hit the
# target this was Triggered from"): 0 on a lone boss, the only enemy its trigger hits. PoB scores its hidden skill in Full
# DPS at Speed 1 (probe POE2T1097: Boneshatter + Quill Burst + a Thorns belt, FullDPS 376.99 -> 381.99, entry "Quill
# Burst" 5): an OVERCOUNT, scaled to 0 as Barbs' is.
QUILL_BURST = "Quill Burst"


def thorns_hit(rate: dict | None) -> float:
    """One Thorns hit on the boss from the bridge's useRates entry for PoB's Thorns group: its per-type hit averages,
    each crit's at its crit chance."""
    rate = rate or {}
    crit = min(max(number(rate.get("CritChance")), 0.0), 100.0) / 100
    hit = sum(number(rate.get(f"{t}HitAverage")) for t in DAMAGE_TYPES)
    crits = sum(number(rate.get(f"{t}CritAverage")) for t in DAMAGE_TYPES)
    return hit * (1 - crit) + crits * crit if crit > 0 else hit


def thorns_model(rate: dict | None, texts: list[str], stats: dict, main: Skill | None,
                 barbs: list[tuple[str, dict, bool]] | None = None) -> tuple[float, list[dict]]:
    """(added DPS, notes) for the build's Thorns hits on the boss (see above). `rate`: the bridge's useRates entry for
    PoB's Thorns group (None: the build has no Thorns damage); `stats`: the main skill's (Speed, HitChance, CritChance)
    with the boss (_boss) and THORNS_FIELDS; `barbs`: (Barbs skill name, its useRates entry, whether it supports the
    main skill) for each Barbs skill in the build. A note's "full_dps" ({SkillDPS name: scale}) takes PoB's own Barbs
    entries out of Full DPS (Objective.entry_scales)."""
    hit = thorns_hit(rate)
    if hit <= 0:
        return 0.0, []
    lower = [t.lower() for t in texts]
    boss = boss_hits.boss_name(stats)
    every = bosses.benefit_skill_time(stats)
    land = boss_hits.land_share(stats)
    landed = land / every if every > 0 else 0.0
    any_hit = any(THORNS_ANY_HIT in t for t in lower)
    if any_hit:
        share, share_how = 1.0, "every hit (\"Thorns can Retaliate against all Hits\")"
    else:
        share, share_how = boss_melee_share(boss, gain=True)
    non_melee, non_how = 0.0, ""
    if not any_hit:
        for t in texts:
            if (m := THORNS_NON_MELEE.search(t)) is None:
                continue
            tribute = number(stats.get("Tribute"))
            if tribute < float(m.group(2)):
                non_how = f"; Toll of Agony 0 (Tribute {tribute:.0f} < {m.group(2)})"
                continue
            known = boss == THORNS_WORST or THORNS_MELEE_SHARE.get(boss) is not None
            upper = boss_melee_share(boss, gain=False)[0] if known else 1.0
            chance = min(float(m.group(1)), 100.0) / 100
            non_melee = max(non_melee, chance * (1 - upper))
            non_how = (f"; + {chance:.0%} of the non-melee hits ({1 - upper:.0%}: 1 - the melee share's upper bound"
                       + ("" if known else f", 0: {boss}'s share isn't read") + f"; Toll of Agony, Tribute {tribute:.0f})")
    retaliations = landed * (share + non_melee)
    melee_main = main is not None and main.attack and main.melee and not main.triggered
    chance = min(sum(float(m.group(1)) for t in texts for m in THORNS_ON_HIT.finditer(t)), 100.0) / 100
    on_hit = chance * hits_per_second(stats) if melee_main else 0.0
    crit_line = any(THORNS_ON_CRIT in t for t in lower)
    on_crit = crits_per_second(stats) if melee_main and crit_line else 0.0
    procs = retaliations + on_hit + on_crit
    barbs_dps, barbs_parts, scales = 0.0, [], {}
    for name, barb_rate, on_main in barbs or []:
        if name == QUILL_BURST:
            scales[name] = 0.0
            barbs_parts.append("Quill Burst 0 (\"Cannot Hit the target this was Triggered from\": the lone boss; PoB's own "
                               "entry, scored at 1 use a second, taken out of Full DPS)")
            continue
        if name not in BARBS:
            continue
        scales[name] = 0.0  # (PoB's own entry, at its flat Speed 1: replaced by the count below)
        count, times = BARBS[name]
        if not (on_main and melee_main):
            barbs_parts.append(f"{name} 0 (it doesn't support the main skill as a non-triggered Melee Attack: an "
                               "undercount if it supports another skill)")
            continue
        main_hits = hits_per_second(stats)
        barb_hits = min(retaliations * count, main_hits) * times
        once = min(retaliations, main_hits) * times
        barb_hit = thorns_hit(barb_rate) or hit
        barbs_dps += barb_hits * barb_hit
        barbs_parts.append(f"{name} {barb_hits:.3g}/s (min(Retaliations {retaliations:.3g}/s x {count}, the main "
                           f"skill's hits {main_hits:.3g}/s)" + (f" x {times}" if times > 1 else "")
                           + f", each PoB's Barbs hit {barb_hit:.4g}; if the game counts the boss once per Retaliation: "
                           f"{once:.3g}/s, {once * barb_hit:.0f} DPS (UNVERIFIED: the stat counts hits, the text "
                           "enemies); PoB's own Barbs entry, scored at 1 use a second, taken out of Full DPS)")
    parts = [f"Retaliation {retaliations:.3g}/s ({boss}'s landed hits {landed:.3g}/s, one every {every:.2f} s at "
             f"{land:.0%} landing (evaded and Blocked hits not counted; whether a Blocked hit Retaliates is "
             f"UNVERIFIED), x {share:.0%}: {share_how}{non_how})"]
    if chance > 0:
        parts.append(f"{chance:.0%} of the main skill's hits {on_hit:.3g}/s"
                     + ("" if melee_main else " (0: the main skill isn't a Melee Attack)"))
    if crit_line:
        parts.append(f"on its melee crits {on_crit:.3g}/s" + ("" if melee_main else " (0: not a Melee Attack)"))
    missing = sorted({what for t in lower for key, what in THORNS_NOT_COUNTED.items() if key in t})
    if barbs_parts:
        parts.append("Barbs / Quill Burst: " + "; ".join(barbs_parts))
    dps = procs * hit + barbs_dps
    return dps, [{
        "label": "Thorns damage on the boss*", "value": round(dps, 1),
        "basis": f"PoB's Thorns hit {hit:.4g} (its own Thorns group, which no PoB DPS counts) x {procs:.3g} Thorns hits "
                 "a second: " + "; ".join(parts) + ". No limit per hit or per second (checked: the game's Thorns definitions, the 125 thorn stat ids, "
                 "GrantedEffects; poe2db, patch notes: none in the data; in game UNVERIFIED)"
                 + (f"; not counted yet: {', '.join(missing)} (undercounts)" if missing else ""),
        "play": "Thorns hit back when the boss hits you in melee" + (" (any hit, with the Crown)" if any_hit else "")
                + (", and on your melee hits" if on_hit or on_crit else "")
                + (", and on your hits after each Retaliation (Barbs)" if barbs_dps else "")
                + f". Without Thorns: +0 DPS; with it: +{dps:.0f} DPS",
        "confidence": "LOW", **({"full_dps": scales} if scales else {})}]


# Glory on a boss (glory_uses) --------------------------------------------------------------------------------------------
# Each Glory skill's cost and what builds its Glory, from the game files (build 26476662, GrantedEffectStatSets, level 20;
# 9 Nov, MECH20): active_skill_requires_X_glory and active_skill_generates_mp_%_glory_per_<event> (that % of the target's
# monster Power per event; a pinnacle boss's Power is 20, as PoB's Multiplier:EnemyPower) or
# active_skill_hundred_times_generates_glory_per_<event> (/100 Glory per event). Events come from the main skill: its hits
# (attack, elemental or chaos hits), the boss's Heavy Stuns (heavy_stun_rate), Freezes (PoB's per-hit Freeze buildup, the
# same boss threshold correction, no re-freeze window: an overcount if one exists), Ignites and Shocks (PoB's chance per
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
# The per-event Glory above is mp_% of the boss's 20 Power (every event but Spear of Solaris' Ignite and Shock, which are
# active_skill_hundred_times_generates_glory_per_ignite / _shock: flat; checked: GrantedEffectStatSets of each skill, game
# 26476662), so with the build's Power lines (boss_power, MECH67) those scale with the boss's Power.
GLORY_FLAT_EVENTS = {("Spear of Solaris", "ignite"), ("Spear of Solaris", "shock")}
GLORY_FROM_LIFE = {"Zerphi's Infamy": (50, 0.05)}  # Glory per use, share of Life spent on skills that becomes Glory
# KeywordPopups Glory (build 26476662): "each Skill can only generate Glory once every 0.5 seconds per monster" (MECHC2,
# RESEARCH4 glory-warcry-flat-rates.md, HIGH for the text; before, every event counted: a Banner at 6 hits/s got a use
# every 1.7 s). On one boss: a gain, then 0.5 s, then the wait for the skill's next event, so gains/s = E / (1 + 0.5 E)
# for E events/s (as the hit-Remnant cooldowns, assumptions.hit_remnant_rate), each gain the events' average Glory
# (MEDIUM: which event wins a window is taken as proportional). Life spent (Zerphi's Infamy) isn't a per-monster event.
# The skill's uses come one every 1/U s, not at random (MECHC11; the random form undercounted a regular rhythm: a Banner at
# 6 attacks/s got 1.5 gains/s where the game gives 2, at 1 attack/s 0.67 where every hit counts): after a gain the next
# use that can gain is the k = ceil(0.5 U)-th, and each use makes an event with p = E / U (an event per use, the hits of
# one use counted once: they land together), so gains/s = 1 / ((k + (1 - p) / p) / U) (glory_gains; the random form when
# U isn't known). Events of a use taken as independent of the use before (MEDIUM).
GLORY_GAIN_EVERY = 0.5


def glory_gains(events: float, uses: float) -> float:
    """Glory gains per second on one boss from `events` per second made by a skill used `uses` times a second."""
    if events <= 0:
        return 0.0
    if uses <= 0:
        return events / (1.0 + GLORY_GAIN_EVERY * events)
    p = min(1.0, events / uses)
    k = max(1, math.ceil(GLORY_GAIN_EVERY * uses - 1e-9))
    return uses / (k + (1.0 - p) / p)
# Same popup: "You cannot gain Glory for a Skill while any instance of that Skill is active unless that Skill is a Banner"
# (MECHC4; RESEARCH12, absorbed-rage-and-glory.md 4, HIGH for the rule: the three banners carry
# active_skill_can_generate_glory_even_if_an_instance_of_this_skill_is_active, checked there over every stat set, passive and
# mod). So a Glory skill whose use leaves something up for a while fills its next use only after it ends: one use per
# (duration + cost / Glory per second). Durations: base_skill_effect_duration of the skill's stat set (game files, build
# 26476662, level 20; read with RESEARCH6's gestats.py): Ancestral Cry 8 s, Apocalypse 12 s, Walking Calamity 10 s, Skyfall
# 20 s, Starborn Onslaught 8 s, Hammer of the Gods 2.5 s; Archon of Chayula's own stat set has none, its tornado 8 s (taken
# as the instance, MEDIUM). Spear of Solaris carries no duration, but its Ground Spear pulses 20 times 0.2 s apart
# (SpearOfSolarisPulsePlayer: solaris_spear_number_of_pulses 20, solaris_spear_pulse_delay_ms 200; poe2db "pulses every 0.2
# seconds, up to 20 times"): 4 s, the spear taken as the skill's active instance (MEDIUM; RESEARCH17, AUDIT11 A11-1, MECHC5;
# before, 0: about 5x its uses at a high Glory rate). Hammer of the Gods' 2.5 s is the delay before the hammer lands (#87,
# MEDIUM), so its own Heavy Stun isn't counted toward the next one (UNVERIFIED either way; the lower reading). Molten Blast
# carries none (0). Increased skill duration isn't read (the instance lasts longer then: a small overcount left, labelled).
GLORY_INSTANCE = {"Ancestral Cry": 8.0, "Apocalypse": 12.0, "Walking Calamity": 10.0, "Skyfall": 20.0,
                  "Starborn Onslaught": 8.0, "Hammer of the Gods": 2.5, "Archon of Chayula": 8.0, "Spear of Solaris": 4.0}


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


# The build's Glory lines (MECHC13; game files' stat descriptions, build 26476662, without the markup; PoB's ModCache
# parses "increased Glory generation" to no mod, and none of the others, so glory_plan reads their text):
# - "N% increased / reduced Glory generation" (and "Body Armour grants ..." from a socketable): the Glory each Monster
#   Power event gives; "... for Banner Skills" on Banners only; "... if you have at least 100 Tribute": Tribute isn't
#   modelled, so not counted (said). Not applied to the flat gains below or to Zerphi's Infamy's Life-spent Glory (whether
#   "generation" covers them is UNVERIFIED: the lower reading);
# - "Skills which require Glory generate N Glory every 2 seconds": N / 2 a second for each Glory skill; "Banners gain N
#   Glory per second" (PoB's ModCache holds it): N a second for each Banner;
# - "Skills gain 1 Glory every N seconds for each Rare or Unique monster in your Presence": the boss is Unique, taken as in
#   your Presence (a play note, LOW): 1 / N a second;
# - "(Skills have a) N% chance to not consume Glory": a use consumes (1 - N%); "N% chance (for Skills) to retain 40% of Glory on use": a
#   use consumes (1 - 0.4 x N%); "When a Banner expires, recover N% of the Glory required for that Banner": a Banner's
#   next use needs N% less (each placed Banner taken to expire, LOW);
# - "A random Skill that requires Glory generates N% of its maximum Glory when your Marks Activate": not counted (the
#   random pick and the Mark rate aren't modelled; said).
# A Glory skill with a cooldown is used at most once per cooldown (Skill.cooldown / cooldown_recovery; before, never read).
GLORY_INC = re.compile(r"^(?:body armour grants )?(\d+(?:\.\d+)?)% (increased|reduced) glory generation"
                       r"( for banner skills| if you have at least 100 tribute)?$", re.I)
GLORY_FLAT = re.compile(r"^skills which require glory generate (\d+(?:\.\d+)?) glory every 2 seconds$", re.I)
GLORY_PRESENCE = re.compile(r"^skills gain 1 glory every (\d+(?:\.\d+)?) seconds? for each rare or unique monster in your "
                            r"presence$", re.I)
GLORY_BANNER_FLAT = re.compile(r"^banners gain (\d+(?:\.\d+)?) glory per second\b", re.I)
GLORY_NOT_CONSUME = re.compile(r"^(?:skills have an? )?(\d+(?:\.\d+)?)% chance to not consume glory$", re.I)
GLORY_RETAIN = re.compile(r"^(\d+(?:\.\d+)?)% chance (?:for skills )?to retain 40% of glory on use$", re.I)
GLORY_BANNER_REFUND = re.compile(r"^when a banner expires, recover (\d+(?:\.\d+)?)% of the glory required for that banner$",
                                 re.I)
GLORY_MARK = re.compile(r"generates \d+(?:\.\d+)?% of its maximum glory when your marks activate", re.I)


def glory_lines(texts: list[str]) -> dict:
    """The build's Glory lines (above), summed: inc / banner_inc (%), flat (Glory a second a skill), presence (Glory a
    second from the boss in your Presence), not_consume / retain / refund (chances and shares, 0-1), and unread texts."""
    out = {"inc": 0.0, "banner_inc": 0.0, "flat": 0.0, "banner_flat": 0.0, "presence": 0.0, "not_consume": 0.0, "retain": 0.0, "refund": 0.0,
           "unread": []}
    for raw in texts:
        t = raw.strip()
        if m := GLORY_INC.match(t):
            value = float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
            where = (m.group(3) or "").lower()
            if "tribute" in where:
                out["unread"].append(f"'{t}' (Tribute isn't modelled)")
            elif "banner" in where:
                out["banner_inc"] += value
            else:
                out["inc"] += value
        elif m := GLORY_FLAT.match(t):
            out["flat"] += float(m.group(1)) / 2
        elif m := GLORY_BANNER_FLAT.match(t):
            out["banner_flat"] += float(m.group(1))
        elif m := GLORY_PRESENCE.match(t):
            out["presence"] += 1 / float(m.group(1))
        elif m := GLORY_NOT_CONSUME.match(t):
            out["not_consume"] = 1 - (1 - out["not_consume"]) * (1 - min(float(m.group(1)), 100) / 100)
        elif m := GLORY_RETAIN.match(t):
            out["retain"] = min(1.0, out["retain"] + float(m.group(1)) / 100)
        elif m := GLORY_BANNER_REFUND.match(t):
            out["refund"] = min(1.0, out["refund"] + float(m.group(1)) / 100)
        elif GLORY_MARK.search(t):
            out["unread"].append(f"'{t}' (a random Glory skill on Mark activations: not modelled)")
    return out


def glory_uses(names: list[str], stats: dict, main: Skill | None, skills: list[Skill] | None = None,
               texts: list[str] | None = None) -> tuple[float, str]:
    """Uses per second of the build's Glory skills on a boss, all together, and how."""
    plan, how = glory_plan(names, stats, main, skills, texts)
    return sum(uses for uses, _ in plan.values()), how


def glory_spent(names: list[str], stats: dict, main: Skill | None, skills: list[Skill] | None = None,
                texts: list[str] | None = None) -> tuple[float, str]:
    """Glory the build's Glory skills consume a second on a boss (their uses x the Glory a use consumes; MECHC13, for
    "Recover N% of maximum Life per Glory consumed"), and how."""
    plan, how = glory_plan(names, stats, main, skills, texts)
    return sum(uses * spent for uses, spent in plan.values()), how


def glory_plan(names: list[str], stats: dict, main: Skill | None, skills: list[Skill] | None = None,
               texts: list[str] | None = None) -> tuple[dict[str, tuple[float, float]], str]:
    """({Glory skill: (uses per second, Glory a use consumes)}, how) on a boss. `skills`: the build's skills, for each
    Glory skill's cooldown; `texts`: the build's lines (glory_lines; stats["_texts"] when not given)."""
    events = glory_events(stats, main)
    lines = glory_lines(list(texts if texts is not None else stats.get("_texts") or []))
    cooldowns = {s.name: s.cooldown / (s.cooldown_recovery or 1.0) for s in skills or [] if s.cooldown}
    power = power_of(stats, texts if texts is not None else None)
    plan, parts = {}, []
    for name in dict.fromkeys(names):
        banner = name.endswith("Banner")
        extra = []
        if name in GLORY_SKILLS:
            cost, gains = GLORY_SKILLS[name]
            if power != BOSS_POWER:
                gains = {e: g if (name, e) in GLORY_FLAT_EVENTS else g * power / BOSS_POWER for e, g in gains.items()}
            rate = sum(events[e] for e in gains)
            glory = sum(events[e] * g for e, g in gains.items())
            how = " + ".join(f"{events[e]:.2f} {e}s/s x {g:g}" for e, g in gains.items()) or "no source counted"
            if rate > 0:  # one gain per 0.5 s per monster
                kept = glory_gains(rate, number(stats.get("Speed"))) / rate
                glory *= kept
                how += f", at most one gain per {GLORY_GAIN_EVERY:g} s on the boss ({kept:.0%} of them count)"
            inc = lines["inc"] + (lines["banner_inc"] if banner else 0.0)
            if inc and glory > 0:
                glory *= max(0.0, 1 + inc / 100)
                extra.append(f"{inc:+g}% Glory generation")
        elif name in GLORY_FROM_LIFE:
            cost, share = GLORY_FROM_LIFE[name]
            glory = share * number(stats.get("LifePerSecondCost"))
            how = f"{share:.0%} of {number(stats.get('LifePerSecondCost')):.0f} Life spent/s"
        else:
            continue
        if lines["flat"]:
            glory += lines["flat"]
            extra.append(f"+{lines['flat']:g}/s ('generate N Glory every 2 seconds')")
        if banner and lines["banner_flat"]:
            glory += lines["banner_flat"]
            extra.append(f"+{lines['banner_flat']:g}/s ('Banners gain N Glory per second')")
        if lines["presence"]:
            glory += lines["presence"]
            extra.append(f"+{lines['presence']:g}/s from the boss in your Presence (kept in it: a play note, LOW)")
        if glory <= 0:
            parts.append(f"{name}: {how}, never")
            continue
        consumed = cost * (1 - lines["not_consume"]) * (1 - 0.4 * lines["retain"])
        needed = consumed * (1 - lines["refund"] if banner else 1.0)
        if needed != cost:
            extra.append(f"{needed:.0f} Glory needed a use (chance not to consume / retain"
                         + (", the Banner's refund on expiry, LOW" if banner and lines["refund"] else "") + ")")
        held = GLORY_INSTANCE.get(name, 0.0)
        every = held + needed / glory
        cooldown = cooldowns.get(name, 0.0)
        if cooldown > every:
            extra.append(f"held to its {cooldown:.1f} s cooldown")
            every = cooldown
        plan[name] = (1 / every, consumed)
        parts.append(f"{name}: {how}" + (f" ({'; '.join(extra)})" if extra else "")
                     + f" Glory against {cost} per use = one per {every:.1f} s"
                     + (f" (no Glory while its {held:g} s instance is up)" if held else ""))
    if lines["unread"]:
        parts.append("not counted: " + ", ".join(lines["unread"]))
    return plan, "; ".join(parts) or "no Glory skill"


# Deadly Resolve (model: deadly_resolve) -----------------------------------------------------------------------------------
# The support (poe2db, matching the 0.5 data): "Gain a stage every 1 second" while channelling the supported skill, "3
# maximum stages", "Stage duration is 10 seconds"; "Expend a stage of Deadly Resolve when an enemy Hits you to Trigger a
# deadly slash", "Can Trigger Deadly Resolve once every 0.3 seconds". PoB counts the slash at its own use rate (1 s at the
# build's attack speed: 1.34 per second on 2b75d's reference). On a boss it comes at most once per stage, one per second
# of channelling, and only when the boss hits you: hits that land (one per boss skill use, bosses.benefit_skill_time, MECHC11; was 0.7 s; less those evaded
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
    every = bosses.benefit_skill_time(stats)  # (a "when hit" benefit: one hit per boss skill use, MECHD4 / MECHC11)
    # (MECHB6: red-flash hits always land; MECHC20: a gain, so the worst case reads the lowest real boss's landed hits)
    hits, landed_how = boss_hits.gain_hits(stats)
    rate = min(1 / DEADLY_RESOLVE_STAGE_EVERY, hits, 1 / DEADLY_RESOLVE_COOLDOWN)
    limit = "a stage per second of channelling" if rate < hits else "the boss's hits that land"
    return trigger_rate(DEADLY_RESOLVE, rate), [{
        "label": "Deadly Resolve slashes per second", "value": round(rate, 3),
        "basis": f"one per stage (a stage per {DEADLY_RESOLVE_STAGE_EVERY:g} s of channelling {main.name}) when the boss hits you: "
                 f"{hits:.2f} hits/s land (one every {every:g} s, less evaded and blocked hits, red-flash hits always landing: {landed_how}; blocked and "
                 f"evaded hits assumed not to count); limited by {limit}. PoB alone counts it at its own use rate.",
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
SANGUINE_CONSUMED = 0.4  # of the full Life Flask's charges, consumed on Hit, 1% each (AscendancyWitch2Notable9)


def sanguine_model(inputs: Inputs, stats: dict, flask_charges: float | None = None) -> tuple[list[dict], list[dict]]:
    """Sanguine Tides (RESEARCH4, sanguine-tides.md): 1 Life Flask charge per 2% of Life spent; on Hit with a full Life Flask,
    40% of its charges are consumed for 1% of damage as Physical each, for 5 s. The buff refreshes rather than stacks
    (poe2wiki, MEDIUM), so it is one value, 0.4 x the flask's maximum charges, up while refills come within 5 s: gain =
    min(0.4 C, 2.5 x % of Life spent per second) (before, 2.5 x L% uncapped: an overcount above the cap). `flask_charges`:
    the equipped Life Flask's maximum charges, 0 with none (no flask to consume: nothing); None leaves it uncapped (tests)."""
    if not inputs.has(SANGUINE):
        return [], []
    life, spent = number(stats.get("Life")), number(stats.get("LifePerSecondCost"))
    when = inputs.condition(SANGUINE, True)
    if when is None:
        return [], []
    gain = SANGUINE_PER_PERCENT * spent / life * 100 if life > 0 and spent > 0 else 0.0
    cap = SANGUINE_CONSUMED * flask_charges if flask_charges is not None else None
    capped = cap is not None and gain > cap
    if cap is not None:
        gain = min(gain, cap)
    # (worked out again by the bridge from each calculation's Life and Life cost, capped by the flask)
    mods = [line("Gain 1% of Damage as Extra Physical Damage", 1 / UNIT, when=when, per=SANGUINE),
            derived(SANGUINE, "sanguine", gain * UNIT, perPercent=SANGUINE_PER_PERCENT,
                    **({"cap": cap} if cap is not None else {}))]
    if cap == 0:
        return mods, [{"label": "Sanguine Tides", "value": "not counted",
                       "basis": "no Life Flask equipped: the charges it consumes on Hit come from a full Life Flask",
                       "confidence": "theory"}]
    if gain <= 0:
        return mods, [{"label": "Sanguine Tides", "value": "not counted", "basis": "the build spends no Life on its skills",
                       "confidence": "theory"}]
    return mods, [{
        "label": "Sanguine Tides: damage gained as Physical", "value": f"{gain:.1f}%",
        "basis": f"the main skill spends {spent:,.0f} Life/s ({spent / life:.1%} of {life:,.0f} per second): 1 Life Flask charge per 2% spent, "
                 "40% of the full flask's charges consumed on Hit, 1% each for 5 s; the buff refreshes, it doesn't stack "
                 "(poe2wiki, MEDIUM)"
                 + (f", so it is capped at {cap:.0f}% (40% of the Life Flask's {flask_charges:.0f} charges)" if capped else
                    (f"; the flask refills too slowly to keep its {cap:.0f}% up, so it is up part of the time" if cap else ""))
                 + "; you hit the boss whenever the flask is full",
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


@dataclass
class PressCap:
    """The most uses a second the build can feed a skill (MECHB5, COVC6 / COVC7: usage.py's combo_rate, travel_rate and
    throw_rate), why, and the seconds spent walking before each use (the travel requirement)."""
    uses: float
    how: str
    moving: float = 0.0
    confidence: str = "LOW"


# A pressed non-main skill in Full DPS (MECH37) -------------------------------------------------------------------
# PoB's Full DPS counts every group in it as used nonstop, though only the main skill is: a skill the player presses
# beside it is used on its cooldown, once per its buff/debuff duration if it's pressed to keep one up, or else at a
# rate the search chooses (PRESS_CHOICES below; none chosen: 0), and the main skill loses the time those presses take.
# (MECHB18: PRESS_EVERY, the old flat 5 s guess, is gone: its last reader, flasks.py's Perfect Timing charm, reads the
# build's own presses since MECHC12, cd32ab1.)
# Full DPS's ailment entries (source: the skill that's their best source) and the ailment whose stacks set each
# (PoB: Burning Ground is a share of the Ignite's damage)
PRESS_AILMENTS = {"Best Ignite DPS": "Ignite", "Best Burning Ground DPS": "Ignite", "Best Bleed DPS": "Bleed",
                  "Best Poison DPS": "Poison"}


def press_key(name: str, source: str | None) -> str:
    """objective.Objective.press_scales' key for a SkillDPS entry: its name, and its source for an ailment's."""
    return f"{name} from {source}" if source and source != name else name


# A pressed skill with no cooldown, no duration and nothing else setting its use (MECH55; THE USER, 12 Nov, card: "Search
# picks the rate"): the search chooses how often it's pressed as a play strategy, its time charged against the main
# skill (press_model's busy share), with a "without it" row in the play guide. SEARCH sets the choice on
# build.objective.press_choices ({skill name as useRates names it: uses per second}; PRESS_CHOICES). Until it does, such
# a skill is pressed 0 times, labelled (the user's flat-rate rule: no source, 0). conditions.skill_use_rate
# (Marks and other "you use" events, consumers beside the main skill) reads the same choice since MECH56.
PRESS_CHOICES = "press_choices"


def press_choices(build) -> dict[str, float]:
    """The search's chosen press rates for the build ({} when it chose none)."""
    return dict(getattr(getattr(build, "objective", None), PRESS_CHOICES, None) or {})


def press_choice(rate: dict, choices: dict[str, float] | None) -> float | None:
    """The chosen uses per second for a useRates row (its name, or its name before a stat set's label), or None."""
    name = str(rate.get("name") or "")
    for skill, uses in (choices or {}).items():
        if name == skill or name.startswith(f"{skill} ") or name.startswith(f"{skill}:"):
            return number(uses)
    return None


def press_use_rate(rate: dict, chosen: float | None = None) -> tuple[float, str, str]:
    """A pressed skill's uses per second from one bridge `useRates` row: on its cooldown, else once per its duration,
    else the rate the search chose (`chosen`, press_choice), else 0 (labelled); never faster than PoB's speed.
    (uses/s, how, confidence)."""
    speed, repeats = number(rate.get("speed")), number(rate.get("repeats")) or 1.0
    cooldown, duration = number(rate.get("cooldown")), number(rate.get("duration"))
    if cooldown > 0:
        real, how, confidence = repeats / cooldown, f"on its {cooldown:.2f} s cooldown", "MEDIUM"
    elif duration > 0:
        real, how, confidence = repeats / duration, f"once per its {duration:.2f} s duration, to keep it up", "MEDIUM"
    elif chosen is not None and chosen > 0:
        real, how, confidence = repeats * chosen, (f"{chosen:.3g} times a second*, the rate the search chose (a way to "
                                                   "play the build; without it, it adds nothing)"), "MEDIUM"
    else:
        real, how, confidence = 0.0, ("0 times* (no cooldown or duration sets how often it's pressed, and the search "
                                      "chose no rate: no source, 0)"), "LOW"
    return min(real, speed), how, confidence


def press_group_rate(rate: dict, pace: float | None = None, cap: "PressCap | None" = None,
                     chosen: float | None = None) -> tuple[float, str, str]:
    """A pressed group's uses per second as press_model counts its damage: press_use_rate, held to Cadence's pace
    (`pace`) and to the most uses the build can feed it (`cap`, Assumptions.press_caps). The one rate for its benefit
    and its side effects (conditions.side_consumes reads it too; MECH54, CLAUDE.md's flat-rate rule)."""
    real, how, confidence = press_use_rate(rate, chosen)
    if pace is not None and real > pace:
        real, how = pace, how + (f", held to Cadence's {pace:g} uses/s (at most 5 uses in any 4 s: a 6th locks the "
                                 "skill for 10 s; game files SupportCadencePlayer)")
    if cap is not None and real > cap.uses:
        real, how = cap.uses, how + f", held to {cap.uses:.3g} uses/s by {cap.how}"
    return real, how, confidence


def press_model(rates: list[dict], main_index: int | None, pressed: set[int],
                paced: dict[int, float] | None = None, caps: dict[int, "PressCap"] | None = None,
                choices: dict[str, float] | None = None) -> tuple[dict[str, float], list[dict]]:
    """Full DPS entry scales ({SkillDPS name: x}) and notes for the pressed non-main groups `pressed`, from the bridge's
    `useRates` (each group's skill as if it were the main one: its speed, capped by its cooldown as PoB does, its
    cooldown after recovery, repeats, duration and base cast time). `paced` ({group: most uses per second}): a group
    held to a pace (Cadence's 5 uses per 4 s, CADENCE_PACE; MECHB4, COVE6's open item). `caps` ({group: PressCap}): the
    most uses a second the build can feed it (Combo, walking between uses, flask charges for a throw; MECHB5), and the
    seconds of walking each use costs the main skill."""
    scales: dict[str, float] = {}
    notes: list[dict] = []
    busy, users = 0.0, []
    for rate in rates:
        if rate["index"] not in pressed or rate["index"] == main_index:
            continue
        speed, repeats = number(rate.get("speed")), number(rate.get("repeats")) or 1.0
        if speed <= 0:
            continue
        cooldown = number(rate.get("cooldown"))
        cap = (caps or {}).get(rate["index"])
        real, how, confidence = press_group_rate(rate, (paced or {}).get(rate["index"]), cap, press_choice(rate, choices))
        if cap is not None and cap.moving > 0:
            busy += real * cap.moving  # (no skill is used while walking between its uses)
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


# The boss's Power raised by the build (MECH67; RESEARCH22 #130, research/abyssal-wasting-sources.md; plans/
# kw-abyssal-wasting.md C). A pinnacle boss has 20 Power (KeywordPopups Power: "Unique monsters always have 20 Power";
# PoB's Boss config: WarcryPower and Multiplier:EnemyPower BASE 20). Every player stat that raises an enemy's Power
# (checked: Stats.json ids with "power" and "enemy|monster|target", their Mods users, game 26476662; PoB's uniques; poe2db
# not checked: UNVERIFIED that no other source exists):
# - unique_redblade_banner_enemies_in_presence_monster_power_+%_final 100 (UniqueEnemiesInPresenceMonsterPower1, Redblade
#   Banner): "Enemies in your Presence count as having double Power". PoB: ModCache parses it to nothing.
# - enemies_in_your_presence_with_abyssal_wasting_have_doubled_power (PassageUniqueAmanamuAbyssalWastingDoubledPower; a
#   Desecrated line of the Grip of Kulemak, MEDIUM): "Targets affected by Abyssal Wasting in your Presence have double
#   Power". Abyssal Wasting: "Inflict Abyssal Wasting on Hit" (the Abyssal Signet ring's implicit, Grip of Kulemak), 6 s
#   per Hit (KeywordPopups AbyssalWasting): Wasted while the main skill hits at least once per 6 s, else that share
#   (hits x 6 s); "Abyssal Wasting you inflict has Infinite Duration": any hit. PoB: ModCache parses it to nothing.
# - monsters_in_your_presence_have_additional_power_equal_to_their_gruelling_madness_stacks (Sadist's Mercy): "Enemies in
#   your Presence have additional Power equal to their Gruelling Madness". Gruelling Madness (KeywordPopups): at most 10
#   on a target, "lose 1 Gruelling Madness every 2 seconds that passes without more being inflicted". Fed by "Hits with
#   this Weapon inflict N Gruelling Madness" (the main skill an Attack hitting at least once per 2 s: 10; slower: N, LOW)
#   or "Enemies in your Presence gain 1 Gruelling Madness each second" (Sculpted Suffering: 10). The ramp isn't counted.
# - Not read here: warcry_monster_power_+% / Echoing Cry's (a warcry's own Power: PoB reads them); monster-side stats
#   (monster_power_doubled_if_afflicted_by_abyssal_wasting: no Mods row uses it); adds' Power (ADD_POWER) isn't doubled.
# Both doublings together: counted once (x2), the lower reading (UNVERIFIED whether "double" twice is x4); the Madness
# Power added after the doubling (UNVERIFIED order; the lower reading). All need the boss in your Presence (the boss's
# distance vs PoB's PresenceRadius). Read by energy_gain, glory_plan, roar_model / roar_break and PoB's own EnemyPower /
# WarcryPower (power_mods, from Assumptions.models). Not yet: Siphon Elements' Cold Remnant chance (5% per Power, already
# 100% at 20 Power: no change).
POWER_DOUBLED = re.compile(r"^Enemies in your Presence count as having double Power$", re.I)
POWER_DOUBLED_WASTED = re.compile(r"^Targets affected by Abyssal Wasting in your Presence have double Power$", re.I)
WASTING_ON_HIT = re.compile(r"^Inflict Abyssal Wasting on Hit$", re.I)
WASTING_INFINITE = re.compile(r"^Abyssal Wasting you inflict has Infinite Duration$", re.I)
WASTING_DURATION = 6.0
MADNESS_POWER = re.compile(r"^Enemies in your Presence have additional Power equal to their Gruelling Madness$", re.I)
MADNESS_ON_HIT = re.compile(r"^Hits with this Weapon inflict (\d+) Gruelling Madness$", re.I)
MADNESS_PER_SECOND = re.compile(r"^Enemies in your Presence gain (\d+) Gruelling Madness each second$", re.I)
MADNESS_MAX, MADNESS_DECAY = 10, 2.0
POWER_FALLBACK_DISTANCE = 50.0  # (units: conditions.RANGED_DISTANCE 5 m, when the boss's distance isn't known: the far
# reading, so a Presence line counts only when the Presence reaches 5 m)

# Runefather's Challenge (MECH68; the user's catch, 13 Nov ~21:55, an UNDERCOUNT). KeywordPopups RunefathersChallenge:
# "Runefather's Challenge is a [Debuff] which raises the [Power] of affected targets by 5" (stat
# monster_power_+_from_ancients_challenge; the game's internal name is Ancient's Challenge). The sources (checked: every
# Stats.json id with "ancients_challenge" (5 ids), each looked up in Mods, PassiveSkills, GrantedEffectStatSets,
# GrantedEffectStatSetsPerLevel, GrantedEffectQualityStats and SoulCoreStats, game 26476662; second source poe2db's
# Runefathers_Challenge and Eyes_of_the_Runefather pages, which list the same three):
# - Eyes of the Runefather (Venerable Defender): UniqueAncientsChallengeOnOffHandDamage1 "Off-hand Hits inflict
#   Runefather's Challenge" and UniqueRaiseShieldAncientsChallenge1 "Inflicts Runefather's Challenge on enemies 6 metres
#   in front of you when raised, no more than once every 2 seconds";
# - the soul core Legacy of A Worthy Foe (an Aldur's Legacy, SoulCoreStats 522, socketed in a shield, limit 3): the same
#   off-hand line (off_hand_apply_ancients_challenge_on_hit). (The manager's "only the shield's two mods" missed it.)
# - local_apply_ancients_challenge_on_hit and has_ancients_challenge_buff: no user in those tables.
# PoB parses none of the lines (ModCache). Off-hand hits with a shield in the off hand: the skills PoB flags shieldAttack
# (its only way to set weapon2Attack with a shield, CalcActiveSkill 515-517; a shield isn't a weapon to getWeaponFlags):
# SHIELD_HITTERS below (Raise Shield's own Shield Bash after a block: RAISE_SHIELD_GRANT below). Only the main skill's hits are read (another
# skill's shield hits pressed beside it aren't: a small undercount). Raise Shield: the boss within 6 m (PoB's
# enemyDistance) and the shield raised once per Challenge duration (a button the player presses to feed it: the time
# raised isn't taken from the main skill, a small overcount, named). Its duration: no game data we have (BuffDefinitions
# not extracted) and poe2db states none, nor any "until the target dies" (its neighbour Runefather's Boast's definition
# says outright it "does not have a duration"; the Challenge's doesn't); CHALLENGE_DURATION 10 s from poe2wiki
# ("approximately 10 seconds") and Mobalytics ("appears to expire after a 10 second period", guides/runefathers-
# challenge-runefathers-boast) (AUDIT32 A32-9): LOW, a flat number; the user may check it in game. The +5 is added after any doubling (UNVERIFIED order: the lower reading, 20 x 2 + 5 = 45, not (20 + 5) x 2).
CHALLENGE_POWER = 5
CHALLENGE_DURATION = 10.0  # LOW: poe2wiki "approximately 10 seconds" and Mobalytics; no game data (AUDIT32 A32-9)
CHALLENGE_RAISE_RANGE = 60.0  # units: "6 metres in front of you when raised"
CHALLENGE_OFF_HAND = re.compile(r"^Off-hand Hits inflict Runefather's Challenge$", re.I)
CHALLENGE_RAISE = re.compile(r"^Inflicts Runefather's Challenge on enemies ([\d.]+) metres in front of you when raised", re.I)
# Raise Shield's own hit (AUDIT31 A31-5, an UNDERCOUNT): "Release immediately after Blocking when an enemy is close to
# perform a Shield Bash, damaging and Stunning enemies" (PoB's ShieldBlockPlayer description; it's a shieldAttack skill):
# an off-hand Hit per blocked hit. Blocks against the boss: one telegraphed hit every PARRY_EVERY s (the user's Parry
# cadence). Every shield base granting it says "Grants Skill: Raise Shield" (PoB's bases). "Close": 2 m (LOW).
RAISE_SHIELD_GRANT = re.compile(r"Grants Skill:[^.;]*\bRaise Shield\b", re.I)
SHIELD_BASH_RANGE = 20.0  # units
SHIELD_HITTERS = frozenset({"Shield Wave", "Magma Spray", "Resonating Shield", "Shield Charge", "Shield Wall", "Parry",
                            "Soaring Midnight", "Embrace The Fall"})  # PoB's shieldAttack skills (Data/Skills)


def challenge_share(texts: list[str], stats: dict, distance: float | None, main: str | None) -> tuple[float, str]:
    """(The share of the fight the boss has Runefather's Challenge, its basis; "" when no line)."""
    raise_line = next((m for t in texts if (m := CHALLENGE_RAISE.match(t))), None)
    off_hand = any(CHALLENGE_OFF_HAND.match(t) for t in texts)
    if not (raise_line or off_hand):
        return 0.0, ""
    far = POWER_FALLBACK_DISTANCE if not distance else distance
    if raise_line and far <= float(raise_line.group(1)) * 10:
        return 1.0, (f"you raise your shield once every {CHALLENGE_DURATION:g} s with the boss {far / 10:g} m away "
                     f"(within {raise_line.group(1)} m): the boss stays Challenged")
    if off_hand:
        hits = hits_per_second(stats) if main in SHIELD_HITTERS else 0.0
        bash = (1 / PARRY_EVERY if any(RAISE_SHIELD_GRANT.search(t) for t in texts) and far <= SHIELD_BASH_RANGE
                else 0.0)
        if hits + bash > 0:
            share = min(1.0, (hits + bash) * CHALLENGE_DURATION)
            how = [f"{main} hits with the shield, {hits:.2f} hits/s"] if hits else []
            if bash:
                how.append(f"a Shield Bash after blocking one telegraphed boss hit every {PARRY_EVERY:g} s with Raise "
                           f"Shield (the Parry cadence the user called fair; the boss within {SHIELD_BASH_RANGE / 10:g} m, "
                           "LOW: the game says only \"when an enemy is close\"; the time raised isn't taken from the "
                           "main skill)")
            return share, "; ".join(how) + f": Challenged {share:.0%} of the fight"
    why = [f"no off-hand (shield) hits from {main or 'the main skill'}"] if off_hand else []
    if raise_line:
        why.append(f"the boss {far / 10:g} m away, beyond the raised shield's {raise_line.group(1)} m")
    return 0.0, " and ".join(why) + ": not Challenged"


def boss_power(texts: list[str], stats: dict, distance: float | None = None,
               attack: bool | None = None, main: str | None = None) -> tuple[float, str]:
    """(The boss's Power with the build's Power lines, its basis). `distance`: the boss's distance in units (PoB's
    enemyDistance; POWER_FALLBACK_DISTANCE if None); `attack`: whether the main skill is an Attack (for "Hits with this
    Weapon"; None: from stats["_main_types"], else not counted); `main`: the main skill's name (for Runefather's
    Challenge from off-hand hits; None: stats["_main_name"])."""
    power, basis = presence_power(texts, stats, distance, attack)
    share, why = challenge_share(texts, stats, distance, main if main is not None else stats.get("_main_name"))
    if not why:
        return power, basis
    power += CHALLENGE_POWER * share
    told = (f"+{CHALLENGE_POWER * share:g} from Runefather's Challenge ({why}; +{CHALLENGE_POWER} while Challenged, "
            f"{CHALLENGE_DURATION:g} s each, LOW: poe2wiki and Mobalytics, no game data; added after any doubling, the lower reading, "
            "UNVERIFIED)")
    return power, (f"the boss's Power {power:g} ({basis}; {told})" if basis else f"the boss's Power {power:g} (20; {told})")


def presence_power(texts: list[str], stats: dict, distance: float | None = None,
                   attack: bool | None = None) -> tuple[float, str]:
    """boss_power's Presence lines: the doublings and Gruelling Madness."""
    doubled = any(POWER_DOUBLED.match(t) for t in texts)
    wasted_line = any(POWER_DOUBLED_WASTED.match(t) for t in texts)
    madness_line = any(MADNESS_POWER.match(t) for t in texts)
    if not (doubled or wasted_line or madness_line):
        return float(BOSS_POWER), ""
    radius = number(stats.get("PresenceRadius"))
    far = POWER_FALLBACK_DISTANCE if not distance else distance
    if radius < far:
        return float(BOSS_POWER), (f"the boss {far / 10:g} m away, outside your {radius / 10:.1f} m Presence: its Power "
                                   f"lines don't apply ({BOSS_POWER})")
    hits = hits_per_second(stats)
    parts = []
    share = 1.0 if doubled else 0.0
    if doubled:
        parts.append("Enemies in your Presence count as having double Power")
    elif wasted_line:
        if not any(WASTING_ON_HIT.match(t) for t in texts):
            parts.append("the Abyssal Wasting double-Power line with nothing inflicting Abyssal Wasting: no change")
        elif hits <= 0:
            parts.append("Abyssal Wasting from your hits, but the main skill doesn't hit: no change")
        else:
            share = 1.0 if any(WASTING_INFINITE.match(t) for t in texts) else min(1.0, hits * WASTING_DURATION)
            parts.append(f"double while the boss has Abyssal Wasting ({share:.0%} of the fight: {hits:.2f} hits/s, "
                         f"{WASTING_DURATION:g} s each)")
    power = BOSS_POWER * (1 + share)
    if madness_line:
        if attack is None:
            attack = "Attack" in (stats.get("_main_types") or set())
        per_second = max([int(m.group(1)) for t in texts if (m := MADNESS_PER_SECOND.match(t))] or [0])
        per_hit = (max([int(m.group(1)) for t in texts if (m := MADNESS_ON_HIT.match(t))] or [0])
                   if attack and hits > 0 else 0)
        if per_second or (per_hit and hits >= 1 / MADNESS_DECAY):
            stacks, why = float(MADNESS_MAX), "kept at its 10 maximum"
        elif per_hit:
            stacks, why = float(min(MADNESS_MAX, per_hit)), f"{per_hit} per hit, hits too slow to stack it (LOW)"
        else:
            stacks, why = 0.0, "nothing inflicts it on the boss from the main skill"
        power += stacks
        parts.append(f"+{stacks:g} from Gruelling Madness ({why})")
    return power, (f"the boss's Power {power:g} (20; " + "; ".join(parts) + f"; in your {radius / 10:.1f} m Presence, "
                   f"the boss {far / 10:g} m away; two doublings counted once and Madness added after, the lower "
                   "readings, UNVERIFIED)")


def power_of(stats: dict, texts: list[str] | None = None) -> float:
    """The boss's Power for a model: stats["_boss_power"] (set by Assumptions.models / Conditions.evidence) when there,
    else boss_power from `texts` (or stats["_texts"]) at the fallback distance, else 20."""
    if "_boss_power" in stats:
        return float(stats["_boss_power"][0])
    found = texts if texts is not None else stats.get("_texts")
    return boss_power(list(found), stats)[0] if found else float(BOSS_POWER)


def power_mods(power: float) -> list[dict]:
    """PoB's own Power readers (warcry Power, "per enemy Power" lines, Harbinger of Madness) at the boss's Power."""
    extra = power - BOSS_POWER
    return [raw_mod("WarcryPower", "BASE", extra), raw_mod("Multiplier:EnemyPower", "BASE", extra)] if extra > 0 else []


# Blind while the Shield is raised (MECHC21, 14 Nov 2026; plans/term-blind-enemies.md G4 with term-raised.md). Bastion of
# Light (notable): "Blind Enemies 3 metres in front of you every 0.25 seconds while your Shield is raised"
# (inflict_blind_on_enemies_within_x_meters_while_shield_is_raised; PoB leaves it as text, ModCache's {nil}, so it never
# set the Blinded box: 0 before, an UNDERCOUNT). Raise Shield (ActiveSkills shield_block, every PoB shield base's
# "Grants Skill: Raise Shield") raises it; the share of the fight it's raised is the build's own play: a player who takes
# the line taps the shield once per Blind (KeywordPopups Blind: 4 s by default, x the build's Blind Duration lines,
# duration_scale), each pulse renewing it, so the boss is Blinded all fight while it stands within the line's reach, and
# each tap takes RAISE_TAP s of the main skill's time. The boss further than the reach (the build's distance choice): no
# taps, 0. No Raise Shield (no shield): 0, labelled. Raise Shield is item-granted, so it isn't a button
# (constraints.player_buttons). The tap's own defence (Block all blockable hits, no Evasion while raised) and its shield
# bash's damage aren't counted (LOW). Bastion's other line (Raise Shield inflicts Parried) is the ledger's
# (bastion-of-light-parried), which assumes the same one raise per 4 s.
RAISE_SHIELD = "Raise Shield"
RAISED_BLIND = re.compile(r"\bblind\b.*\bwhile (?:your )?shield is raised", re.I)
RAISED_REACH = re.compile(r"(\d+(?:\.\d+)?) metres in front of you", re.I)
# A tap: the shield bash's 550 ms (PoB ShieldBlockPlayer attackTime; the game files give its channel CastTime 1000 ms),
# taken as one raise and release; a pulse every 0.25 s needs at least 0.25 s held. How long a tap really takes: LOW
# (UNVERIFIED in game)
RAISE_TAP = 0.55
BLIND_SECONDS = 4.0  # KeywordPopups Blind (conditions.DEBUFF_DURATION)
# The taps are presses (THE USER, 14 Nov ~02:30: an item-granted skill the build actually presses counts toward the button
# cap, "Count it if used"; AUDIT31 A31-4). The search can turn them off on a build (build.objective.raised_taps = False,
# RAISED_TAPS: a build at its cap drops Bastion's Blind and the taps' time); with them on, the build's tap rate is on
# build.item_presses ({skill: (uses a second, how)}; ITEM_PRESSES, set by Assumptions) for constraints.player_buttons
# (SEARCH18) to count Raise Shield as a button. The play guide's "with the taps / without the taps" row: play_guide.raised_row.
RAISED_TAPS = "raised_taps"
ITEM_PRESSES = "item_presses"


def raised_taps_on(build) -> bool:
    """Does the build tap Raise Shield for its raised-shield lines (the search's switch, RAISED_TAPS; on unless set)?"""
    return getattr(getattr(build, "objective", None), RAISED_TAPS, True) is not False


@dataclass(frozen=True)
class RaisedPlan:
    blind: float  # share of the fight the boss is Blinded by the raised-shield line
    lost: float  # share of the main skill's time spent raising the shield
    how: str
    taps: float = 0.0  # Raise Shield presses a second (a button: ITEM_PRESSES)


def raised_plan(skills: list[Skill], texts: list[str], metres: float, taps: bool = True) -> RaisedPlan:
    """How the build raises its shield for a "Blind ... while your Shield is raised" line (Bastion of Light): one tap per
    Blind duration while the boss is within the line's reach (`metres`, the build's distance), else none."""
    lines = [t for t in texts if RAISED_BLIND.search(t)]
    if not lines:
        return RaisedPlan(0.0, 0.0, "")
    if not any(s.name == RAISE_SHIELD for s in skills):
        return RaisedPlan(0.0, 0.0, "Blind while your Shield is raised: the build has no Raise Shield (no shield), so 0")
    reach = max((float(m.group(1)) for t in lines if (m := RAISED_REACH.search(t))), default=math.inf)
    if metres > reach:
        return RaisedPlan(0.0, 0.0, f"Blind while your Shield is raised reaches {reach:g} m in front of you and the boss "
                                    f"stands {metres:g} m away: no taps, 0 (closer: Blinded all fight)")
    if not taps:
        return RaisedPlan(0.0, 0.0, "Blind while your Shield is raised: the build doesn't tap Raise Shield (a button: "
                                    "the search left it out, e.g. at the button cap), so 0 and no time lost")
    duration = BLIND_SECONDS * duration_scale(texts, "blind")
    if duration <= 0:
        return RaisedPlan(0.0, 0.0, "Blind while your Shield is raised: Blind lasts 0 s with the build's lines, so 0")
    lost = min(RAISE_TAP / duration, 1.0)
    return RaisedPlan(1.0, lost, (
        f"tap Raise Shield once every {duration:g} s (the Blind's duration) with the boss within "
        f"{'' if reach == math.inf else f'{reach:g} m '}in front of you ({metres:g} m away): each pulse renews the Blind, so "
        f"the boss is Blinded all fight; each tap takes {RAISE_TAP:g} s ({lost:.1%} of the main skill's time, counted; "
        f"LOW: a tap's length is UNVERIFIED). Raise Shield is a button here (pressed {1 / duration:.2f} times a second). "
        f"Without the taps: no Blind and no time lost"), 1 / duration)


def raised_model(skills: list[Skill], texts: list[str], metres: float, main: Skill | None,
                 taps: bool = True) -> tuple[list[dict], list[dict]]:
    """The main skill's time spent tapping the shield (raised_plan; the Blinded box itself is conditions')."""
    if main is None or main.name == RAISE_SHIELD:
        return [], []
    plan = raised_plan(skills, texts, metres, taps)
    if not plan.how:
        return [], []
    notes = [{"label": "Blind while your Shield is raised (how the build raises it)", "value": f"{plan.blind:.0%} of the fight",
              "basis": plan.how, "confidence": "LOW" if plan.lost > 0 else "theory"}]
    if plan.lost <= 0:
        return [], notes
    return [line("1% more Attack and Cast Speed", -100 * plan.lost, skill=main.name)], notes


# ===================================================================================================================
# MECH-B (skills and damage sources: projectiles, triggered skills, repeats, minions, totems). New MECH-B models go below.
# ===================================================================================================================

# Returning projectiles on a lone boss (MECHB1, 12 Nov 2026; FIX_QUEUE "Chain / fork / split / pierce / return").
# Game files, KeywordPopups Return: "Projectiles which Return will Return to the entity which originated them after
# reaching the end of their travel, or on hitting a final target". PoB counts one boss hit per projectile and never sets
# its ReturningProjectile condition, so a return's second hit is missing (an undercount). The sources (checked: every
# Stats.json id holding "return" with "projectile" or "arrow" (12 ids), each looked up in Mods, PassiveSkills,
# GrantedEffectStatSets and GrantedEffectStatSetsPerLevel; ActiveSkills descriptions holding "return"; PoB's ModCache;
# second source: the FIX_QUEUE research of 11 Nov (poe2db, poe2wiki, stratlore 0.5.4)):
#   - Breachlord's Amalgam (support; projectiles_return + returning_projectiles_always_pierce; it adds a 6 s cooldown
#     with 3 uses, which PoB already charges);
#   - Soaring Midnight (Nightfall's granted skill; the same two stats; pierces 3 targets base);
#   - Cadiro's Gambit: "Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow", and
#     KeywordPopups UniqueArrowModifierPierceReturn: "Reversing Arrows Pierce all targets and Return to you" (1 arrow in
#     6, the six assumed equally likely: UNVERIFIED);
#   - Ironbound's "Arrows Return if they have Pierced a target which had Fully Broken Armour" (counted while the boss's
#     Armour is Fully Broken, through PoB's own condition, which the search weighs by its uptime);
#   - Drillneck's "Attack Projectiles Return if they Pierced at least N times" (N = 3 or 4): 0 on a lone boss, since a
#     projectile pierces each target once (checked: KeywordPopups Pierce "pass through a target while still damaging
#     them" and Chain "Enemies cannot be targeted more than once"; poe2wiki via the FIX_QUEUE research: copies can't hit
#     a previous target).
#   Stats with no user in the current game files (checked: the same four tables plus GrantedEffectQualityStats):
#   attack_projectiles_return, arrows_that_pierce_also_return, arrows_from_third_firing_point_return,
#   projectile_return_%_chance, ice_spear_and_ball_lightning_projectiles_return, support_return_projectile_damage_+%_final,
#   active_skill_returning_projectile_damage_+%_final. Spectral Throw and Venom Gyre return in their ActiveSkills
#   descriptions but have no skill gem (SkillGems x BaseItemTypes) and no PoB skill; Spiral Throw's gem is "[DNT-UNUSED]".
# The rule (LOW, UNVERIFIED: stratlore 0.5.4 says a return allows one more collision with targets already hit; no game
# file says so): a returning projectile hits the boss a second time only if it passed through it on the way out (it
# pierced it), at the outgoing pierce chance (PoB's PierceCount, capped at 1). One that stops on the boss returns from
# it and isn't counted again. The returned hit has pierced the boss, so the "after piercing" gem lines apply to it:
# Pierce I's -20% (support_pierce_projectile_damage_+%_final_if_pierced_enemy), Pierce III's -20% per pierced target,
# and Frostbolt's and High Velocity Rounds' quality (+1% per quality, active_skill_projectile_damage_+%_final_if_pierced_
# enemy, GrantedEffectQualityStats). Drillneck's increased damage per pierce isn't read on the returned hit (a small
# undercount).
BREACHLORDS_AMALGAM = "Breachlord's Amalgam"
SOARING_MIDNIGHT = "Soaring Midnight"
CADIRO_ARROWS = re.compile(r"^Each Arrow fired is a Crescendo, Splinter, Reversing, Diamond, Covetous, or Blunt Arrow$", re.I)
CADIRO_REVERSING_SHARE = 1 / 6
IRONBOUND_RETURN = re.compile(r"^Arrows Return if they have Pierced a target which had Fully Broken Armour$", re.I)
PIERCE_TIMES_RETURN = re.compile(r"^Attack Projectiles Return if they Pierced at least (\d+) times", re.I)
AFTER_PIERCE_MORE = {"Pierce I": -20.0, "Pierce III": -20.0}  # the gems' game-file values, at one pierced target
AFTER_PIERCE_QUALITY = frozenset({"Frostbolt", "High Velocity Rounds"})  # +1% more per quality after piercing
RETURN_RULE = ("a returned projectile hits the boss again only if it pierced it on the way out (LOW, UNVERIFIED: a "
               "community source says a return may hit targets already hit; no game file says so)")


def returned_hit_factor(main: Skill, group_gems: list[dict]) -> tuple[float, list[str]]:
    """A returned hit's damage against the outgoing hit's: the "after piercing" lines of the main skill's group."""
    factor, why = 1.0, []
    for gem in group_gems:
        if not gem.get("enabled", True):
            continue
        name = gem.get("name")
        if name in AFTER_PIERCE_MORE:
            factor *= 1 + AFTER_PIERCE_MORE[name] / 100
            why.append(f"{name} {AFTER_PIERCE_MORE[name]:+g}% after piercing")
        elif name == main.name and name in AFTER_PIERCE_QUALITY and gem.get("quality"):
            factor *= 1 + float(gem["quality"]) / 100
            why.append(f"{name}'s quality +{float(gem['quality']):g}% after piercing")
    return factor, why


def return_model(texts: list[str], stats: dict, main: Skill | None, main_types: set[str], group_gems: list[dict],
                 weapon_types: set[str]) -> tuple[list[dict], list[dict]]:
    """Returning projectiles' second boss hit (the rule above): `1% more Damage` on the main skill for the share of its
    projectiles that pierce the boss and return, times the returned hit's own factor; Ironbound's part only against
    Fully Broken Armour. A note for each source, counted or not."""
    if main is None or "Projectile" not in main_types:
        return [], []
    names = {g.get("name") for g in group_gems if g.get("enabled", True)}
    arrows = main.attack and "Bow" in weapon_types
    pierce = min(1.0, max(0.0, number(stats.get("PierceCount"))))
    notes: list[dict] = []
    always, why = 0.0, ""
    if main.name == SOARING_MIDNIGHT:
        always, why = 1.0, "Soaring Midnight returns at the end of its flight"
    elif (BREACHLORDS_AMALGAM in names and {"Cold", "Lightning"} & main_types
          and not {"Triggered", "Persistent"} & main_types):
        always, why = 1.0, "Breachlord's Amalgam makes its projectiles return"
    if not always and arrows and any(CADIRO_ARROWS.search(t.strip()) for t in texts):
        always, why = CADIRO_REVERSING_SHARE, ("Cadiro's Gambit: 1 arrow in 6 is a Reversing Arrow, which Pierces all "
                                               "targets (the six kinds assumed equally likely, UNVERIFIED)")
        pierce = 1.0
    ironbound = arrows and always < 1.0 and any(IRONBOUND_RETURN.search(t.strip()) for t in texts)
    for t in texts:
        if m := PIERCE_TIMES_RETURN.search(t.strip()):
            notes.append({"label": f"Projectiles return after {m.group(1)} pierces (Drillneck)", "value": 0,
                          "confidence": "HIGH",
                          "basis": "a projectile pierces each target once, so it never returns off a lone boss: none"})
    if not always and not ironbound:
        return [], notes
    factor, parts = returned_hit_factor(main, group_gems)
    returned = f"; the returned hit x{factor:.2f} ({', '.join(parts)})" if parts else ""
    mods: list[dict] = []
    for share, label, text, how in (
            (always, f"Returning projectiles ({main.name})", "1% more Damage", why),
            ((1 - always) if ironbound else 0.0, "Ironbound: arrows return after piercing a Fully Broken target",
             "1% more Damage against Enemies with Fully Broken Armour",
             "counted while the boss's Armour is Fully Broken (the search weighs its uptime)")):
        if not share:
            continue
        more = 100.0 * share * pierce * factor
        if more <= 0:
            notes.append({"label": label, "value": 0, "confidence": "LOW",
                          "basis": f"{how}, but its projectiles don't pierce the boss on the way out: {RETURN_RULE}: none"})
            continue
        mods.append(line(text, more, skill=main.name))
        notes.append({"label": label, "value": round(more, 1), "confidence": "LOW",
                      "basis": f"{how}: {share:.3g} of projectiles x {pierce:.0%} pierce the boss on the way out{returned} "
                               f"= {more:.1f}% more damage; {RETURN_RULE}"})
    return mods, notes


# Cadiro's Gambit's other five arrow kinds (MECHB22; COV20's note: only the Reversing Arrow was read). PoB doesn't
# parse the line at all (ModCache 7232: no mod, the whole text left over), so none of the six counted. KeywordPopups
# (build 26476662), each 1 arrow in 6 (the six assumed equally likely, UNVERIFIED, as CADIRO_REVERSING_SHARE):
#   - UniqueArrowModifierCrit, "All Hits with Diamond Arrows are Critical Hits with 60% increased Critical Damage
#     Bonus": counted (an UNDERCOUNT before): a Diamond arrow's hit is PoB's crit average, the rest keep PoB's average
#     hit at its crit chance, so the main skill's damage x (5/6 + 1/6 x crit average / average hit). The 60% increased
#     Critical Damage Bonus isn't added (its size needs the build's other increases: a lower bound, labelled); effects
#     that need a Critical Hit (on-crit lines) aren't raised by it either (an undercount).
#   - UniqueArrowModifierChain, Crescendo: "Chain 6 additional times and deal 60% increased Damage for each time they
#     have Chained": 0 on a lone boss (KeywordPopups Chain: "Enemies cannot be targeted more than once in the same
#     Chain"; its first hit has Chained 0 times).
#   - UniqueArrowModifierSplit, Splinter: "Split towards 6 targets": 0 more hits on a lone boss (KeywordPopups Split:
#     the copies "aim at different targets ... If there aren't enough targets, the rest will be aimed in random
#     directions"; LOW: a random copy reaching the boss isn't counted).
#   - UniqueArrowModifierStun, Blunt: "600% increased Stun Buildup": not counted here (Heavy Stun on the boss is MECH-A's
#     / MECH-D's; named in the note as an undercount).
#   - UniqueArrowModifierRarity, Covetous: item rarity on kill, no damage.
#   - UniqueArrowModifierPierceReturn, Reversing: return_model.
CADIRO_DIAMOND_SHARE = 1 / 6
CADIRO_FIELDS = [*(f"MainHand.{t}HitAverage" for t in DAMAGE_TYPES), *(f"MainHand.{t}CritAverage" for t in DAMAGE_TYPES),
                 "MainHand.CritChance"]


def cadiro_model(texts: list[str], stats: dict, main: Skill | None, main_types: set[str],
                 weapon_types: set[str], returned: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Cadiro's Gambit's Diamond Arrows (see above) as "1% more Damage" on a main bow attack's projectiles, and a note for
    the kinds that add nothing on a lone boss. `stats`: the main skill's per-type hit and crit averages and crit chance
    (top level, else MainHand.). `returned`: the Reversing Arrows' own "% more Damage" (return_model's line): one arrow
    is never both, so the two shares add (1 + d + r) rather than multiply, and this line is d / (1 + r)."""
    if main is None or not main.attack or "Bow" not in weapon_types or "Projectile" not in main_types \
            or not any(CADIRO_ARROWS.search(t.strip()) for t in texts):
        return [], []

    def read(field: str) -> float:
        return sum(number(stats.get(f"{t}{field}")) for t in DAMAGE_TYPES) \
            or sum(number(stats.get(f"MainHand.{t}{field}")) for t in DAMAGE_TYPES)

    hit, crit = read("HitAverage"), read("CritAverage")
    chance = min(max(number(stats.get("CritChance")) or number(stats.get("MainHand.CritChance")), 0.0), 100.0) / 100
    average = hit * (1 - chance) + crit * chance
    others = ("Crescendo (its chains need other enemies: none on a lone boss), Splinter (its splits aim at other "
              "targets, else random directions: none counted, LOW), Covetous (rarity only); Blunt's 600% increased Stun "
              "Buildup isn't counted (an undercount); Reversing: the returning-projectile note")
    if average <= 0 or crit <= average:
        return [], [{"label": "Cadiro's Gambit's arrow kinds", "value": 0, "confidence": "LOW",
                     "basis": "PoB gave no crit average above the average hit for the main skill, so the Diamond "
                              f"Arrows add nothing here; {others}"}]
    factor = 1 - CADIRO_DIAMOND_SHARE + CADIRO_DIAMOND_SHARE * crit / average
    more = 100.0 * (factor - 1) / (1 + max(returned, 0.0) / 100)
    return [line("1% more Damage", more, skill=main.name)], [{
        "label": "Cadiro's Gambit: Diamond Arrows always Critically Hit*", "value": round(more, 1),
        "confidence": "LOW",
        "basis": f"1 arrow in 6 is a Diamond Arrow (the six kinds assumed equally likely, UNVERIFIED), which always "
                 f"Critically Hits: its hit {crit:.4g} (PoB's crit average) against the average {average:.4g} at "
                 f"{chance:.0%} crit chance, x{factor:.3f}"
                 + (f" (added to the Reversing Arrows' +{returned:.3g}%, not multiplied: {more:.3g}%)" if returned > 0 else "")
                 + f"; its 60% increased Critical Damage Bonus isn't added (a lower "
                 f"bound) and on-crit effects aren't raised; PoB doesn't read the line at all. {others}"}]


# Hazards set off in their first half second (MECHB2, 12 Nov 2026; FIX_QUEUE, COVC2's finding). Seven player stat sets
# carry "-80% ... damage if destroyed within 0.5 seconds by something other than you" (stat description; checked: every
# Stats.json id with "first_500_ms" (3: hazard_, ice_crystals_, shield_wall_damage_+%_final_for_first_500_ms_when_not_
# killed_by_self), looked up in GrantedEffectStatSets / PerLevel, Mods, PassiveSkills, GrantedEffectQualityStats: only
# skill stat sets use them: Spearfield, Ice Shards' shards, Grim Pillars, Brambles (Briarpatch), Frost Wall, Glacial
# Bolt's wall, Shield Wall; second source: PoB's Data/Skills holds the same -80 on the same sets). PoB maps the stat to
# nothing (checked: SkillStatMap.lua and each set's statMap), so it counts every hazard at full damage: an overcount
# wherever the boss sets one off at once. The bridge reads it from the scored stat set (Glacial Bolt's Projectile set
# and Ice Shards' Projectile set don't carry it) with what delays the trigger:
#   - an arming time of 0.5 s or more (Ice Shards' shards arm in 1.5 s: never early; Brambles' 0.4 s doesn't help);
#   - Controlled Hazard ("can't trigger for 3 seconds after creation") or Delayed Reaction ("trigger at the end of
#     their duration instead", whose -70% duration still leaves Spearfield 3 s): never early.
# Who sets it off (LOW, a play assumption; KeywordPopups Hazard: "destroyed when traversed by an enemy"):
#   - hazards (Spearfield, Brambles): placed where the boss stands, so it traverses them at once: all early (x0.2).
#     Laying them in the boss's path at least 0.5 s ahead avoids it: the play guide's "with it" row.
#   - Ice Crystals (any skill with a set typed is_ice_crystal_explosion: Frost Wall, Glacial Bolt's wall, and Grim
#     Pillars, whose "Ice-Crystal spears ... can be destroyed for an explosion" carry the hazard_ stat id) and Shield
#     Wall: broken by the player's own hits (KeywordPopups
#     IceCrystals: Consume-Freeze skills shatter them; Shield Wall's own "more damage when slammed"), which the penalty
#     doesn't touch ("something other than you"): none early; the boss breaking them is the "without it" row.
# Rearm (PoB: DPS x (1 + rearm chance)): a rearmed hazard's later trigger is taken as after 0.5 s, so at full damage
# (UNVERIFIED: whether the window restarts on rearming isn't in the game files' text).
EARLY_HAZARD_WINDOW = 0.5  # seconds (the stats' "first_500_ms")
EARLY_HAZARD_BY_BOSS = {"hazard": True, "ice_crystals": False, "shield_wall": False}
EARLY_HAZARD_ENTRY = "hazard-early-trigger"


def hazard_early_share(early: dict) -> tuple[float, str]:
    """The share of the skill's hazards the boss sets off in their first 0.5 s, and why."""
    arming = number(early.get("arming")) / 1000
    if arming >= EARLY_HAZARD_WINDOW:
        return 0.0, f"they arm after {arming:g} s, past the 0.5 s window"
    if early.get("atEnd"):
        return 0.0, f"{early['atEnd']} sets them off at the end of their duration instead"
    if number(early.get("delay")) >= EARLY_HAZARD_WINDOW:
        return 0.0, f"{early.get('delayBy') or 'a support'}: they can't trigger for {number(early['delay']):g} s"
    if EARLY_HAZARD_BY_BOSS.get(early.get("kind"), True):
        return 1.0, "placed where the boss stands, it sets them off at once (LOW, a play assumption)"
    return 0.0, "you break them with your own hits, which the penalty doesn't touch (LOW, a play assumption)"


def hazard_early_model(skills: list[Skill], stats: dict, forbidden: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    if EARLY_HAZARD_ENTRY in forbidden:
        return [], []
    mods, notes, seen = [], [], set()
    rearm = min(1.0, max(0.0, number(stats.get("HazardRearmChance")) / 100))
    for skill in skills:
        early = skill.early_hazard
        if not early or skill.name in seen:
            continue
        seen.add(skill.name)
        less = number(early.get("more")) / 100  # -0.8
        share, why = hazard_early_share(early)
        factor = (1 + less * share + rearm) / (1 + rearm)
        by_boss = (1 + less + rearm) / (1 + rearm)
        if factor != 1:
            mods.append(line("1% more Damage", 100 * (factor - 1), skill=skill.name))
        crystals = early.get("kind") != "hazard"
        notes.append({
            "label": f"{skill.name}: {abs(less):.0%} less if set off in its first 0.5 s", "value": round(factor, 4),
            "basis": f"{why}; PoB counts every one at full damage"
                     + (f"; rearms ({rearm:.0%}) taken as later, at full damage (UNVERIFIED)" if rearm else ""),
            "play": (f"{'Break them yourself' if crystals else 'Lay them in the boss path at least 0.5 s before it gets there'}"
                     f": x{1 if crystals else 1 / by_boss:.2f} on {skill.name}'s damage. Without it (the boss "
                     f"{'breaks them' if crystals else 'sets them off at once'}): x{by_boss:.2f}"
                     if share or crystals else ""),
            "confidence": "LOW: who sets the hazard off is how it's played; the -80% is the game files' (datamined)"})
    return mods, notes


# Channel wind-up (MECHB3; FIX_QUEUE MECHB2 handover / AUDIT6) ----------------------------------------------------------
# Player stat sets with a channel start time (game files 26476662, GrantedEffectStatSets): OilBarragePlayer
# channel_start_duration_as_%_of_attack_time 350 (and channel_end 1000, display_attack_time_+% 350),
# BearFuryOfTheMountainPlayer 36 (attack time), MetaHollowFormPlayer 30 (attack time), VolcanoPlayer 50 (cast time),
# RunicReprievePlayer 200 (cast time). PoB reads none of these stats (checked: grep of PoB's src, outside its skill data
# and stat descriptions, for channel_start and display_attack_time finds no use). Not counted: how much of each channel
# is wind-up depends on how long the channel lasts (Oil Barrage's channelled barrage consumes a Power Charge per 1.5 s, so its length is the build's Power Charge
# supply, MECH-C's), and whether PoB's attack time already holds the 350% (display_attack_time_+% 350) is UNVERIFIED. No
# honest lower bound exists without them, so the note says the DPS may be too high instead of guessing a channel length.
CHANNEL_WINDUP = {"Oil Barrage": (350, "attack"), "Fury of the Mountain": (36, "attack"), "Hollow Form": (30, "attack"),
                  "Volcano": (50, "cast"), "Runic Reprieve": (200, "cast")}


def channel_windup_note(main: Skill | None) -> list[dict]:
    """A labelled note for a main skill whose channel wind-up isn't counted (CHANNEL_WINDUP)."""
    if main is None or main.name not in CHANNEL_WINDUP:
        return []
    pct, kind = CHANNEL_WINDUP[main.name]
    return [{"label": f"{main.name}'s channel wind-up (not counted)", "value": f"{pct}% of its {kind} time a channel",
             "basis": f"the game files' channel_start_duration_as_%_of_{kind}_time {pct}, which PoB doesn't read; its share "
                      "of the fight depends on how long each channel lasts" + (
                          " (Oil Barrage's barrage consumes a Power Charge every 1.5 s, so its length is the build's Power "
                          "Charge supply), and whether PoB's attack time already holds it (display_attack_time_+% 350) is "
                          "UNVERIFIED" if main.name == "Oil Barrage" else "") + ": the DPS may be too high",
             "confidence": "UNVERIFIED"}]


# Warcry Echoes (MECHB3; AUDIT6 A6-2) -------------------------------------------------------------------------------------
# Echoing Cry: "Supported Warcries Echo 2 times" with a 1.3 s delay (SupportEchoingCryPlayer: base_warcry_echo_X_times 2,
# warcry_echo_delay_ms_from_support 1300, warcry_echo_area_of_effect_+%_final_per_repeat 100; game files 26476662). Its
# 40% less Damage and 50% less Area (COVE2's ledger entries) are on the Warcry, so each Echo repeats the penalised hit:
# a damaging Warcry deals (1 + 2) x 0.6 = 1.8x, not 0.6x. PoB counts no Echo. The part is COV-E's "warcry_echo"
# (support_part_scale, ledger supports-parts.yaml on cov5: WarcryEchoes, EchoAreaMorePercentPerEcho, WarcryEchoDelayMs);
# until that lands, WARCRY_ECHO holds the same game-file values. Whether each Echo hits the boss (it repeats at the first
# use's location 1.3 and 2.6 s later; KeywordPopups Echo) is an in-game question held for the user: counted as hitting
# (UNVERIFIED), with the x1 "without it" row in the note. Not read: the Echoes' larger area (a lone boss is in the area
# or not), whether a new use cancels Echoes still pending (UNVERIFIED), the Echoes' Warcry buffs, and Echoing Cry's
# "travel a distance before each use" (COV-C's, usage).
WARCRY_ECHO = {"WarcryEchoes": 2.0, "EchoAreaMorePercentPerEcho": 100.0, "WarcryEchoDelayMs": 1300.0}
ECHOING_SUPPORTS = ("Echoing Cry",)


def warcry_echo_model(skills: list[Skill], supports: dict[int, list[str]], warcries: set[str],
                      part_scale: Callable[[Skill, str], tuple[dict[str, float], list[dict]]] | None = None
                      ) -> tuple[list[dict], list[dict]]:
    """Each Warcry supported by Echoing Cry deals its (penalised) hit 1 + Echoes times. `warcries`: the names of the
    build's Warcry skills; `part_scale`: COV-E's support_part_scale when it exists."""
    mods: list[dict] = []
    notes: list[dict] = []
    for skill in skills:
        if skill.name not in warcries or skill.group is None:
            continue
        if not any(n in ECHOING_SUPPORTS for n in supports.get(skill.group, [])):
            continue
        scale, part_notes = part_scale(skill, "warcry_echo") if part_scale else ({}, [])
        echoes = scale.get("WarcryEchoes") or WARCRY_ECHO["WarcryEchoes"]
        delay = (scale.get("WarcryEchoDelayMs") or WARCRY_ECHO["WarcryEchoDelayMs"]) / 1000
        mods.append(line("1% more Damage", 100 * echoes, skill=skill.name))
        notes += part_notes
        notes.append({"label": f"{skill.name}: Echoing Cry's Echoes", "value": f"x{1 + echoes:g} hits",
                      "basis": f"\"Supported Warcries Echo {echoes:g} times\", {delay:g} s apart, each with the support's 40% "
                               "less Damage (counted on the Warcry); PoB counts no Echo. Each Echo is counted as hitting the "
                               f"boss at the first use's location (UNVERIFIED: an in-game test). Without the Echoes: x1, "
                               f"x{1 / (1 + echoes):.2f} on this number",
                      "confidence": "UNVERIFIED"})
    return mods, notes


# Cadence on a pressed skill (MECHB4; COVE6's open item) ------------------------------------------------------------------
# A pressed (non-main) skill with Cadence: COV-E's cadence_share counts its stacks only on the main skill; the press model
# holds a pressed Cadence skill to the same pace that avoids the 10 s lockout ("unusable for 10 seconds after being used 6
# or more times within four seconds": at most 5 uses in any 4 s; game files SupportCadencePlayer, maximum_stacks 6).
CADENCE_SUPPORTS = ("Cadence",)
CADENCE_PACE = (6 - 1) / 4.0


# Rapid Shot's Heat (MECHB4; RESEARCH18 #94, research/rapid-shot-heat.md, plans/kw-heat.md) ------------------------------
# "Cannot fire or reload for 10 seconds on reaching maximum Heat" (rapid_shot.csd; RapidShotPlayer's
# skill_specific_stat_description_mode 10000; poe2db, 0.3.0 notes: HIGH). Maximum Heat: maximum_number_of_crossbow_heat_stacks
# (45 at level 20; quality adds up to 15). Heat per attack: 1 (0.3.0 notes "generates Heat once per attack", poe2wiki;
# hard-coded, MEDIUM-HIGH). Decay: starts 4 s after the last gain, 10 Heat/s (0.3.0 notes; the heat_base_* stats have no
# user: MEDIUM-HIGH). Spenders: consume_X_heat_on_skill_use 15 (Explosive Grenade, Cluster Grenade, Incendiary Shot; game
# files). PoB reads none of it (plans/kw-heat.md: no Module reads Heat), so Rapid Shot as the main skill fires nonstop.
# Cycle: firing gains r x h - (Heat spent per second by the build's spenders); at maximum it stops for L s, in which the
# spenders keep spending and decay empties the rest after 4 s; then it fires again until full. Share firing = refill time
# / (refill + L). Counted as "more Damage" on Rapid Shot (not attack speed: the rate it reads would move with it), so the
# average over the fight is right; Freeze/Electrocute buildup from fewer, full hits isn't modelled (small). Not counted:
# what the player does in the lockout (other skills, unscored), and the spenders' "if Heat consumed" bonuses (an
# undercount, next).
RAPID_SHOT = "Rapid Shot"
HEAT_PER_ATTACK = 1.0
HEAT_LOCKOUT = 10.0  # seconds (skill_specific_stat_description_mode 10000 ms; read from the gem when the bridge has it)
HEAT_DECAY_DELAY, HEAT_DECAY_RATE = 4.0, 10.0
HEAT_MAX = 45.0  # level 20, no quality (fallback when the gem's stats aren't read)
HEAT_SPENDERS = {"Explosive Grenade": 15.0, "Cluster Grenade": 15.0, "Incendiary Shot": 15.0}


def heat_model(main: Skill | None, stats: dict, rapid: Skill | None, spenders: list[tuple[str, float, str]]
               ) -> tuple[list[dict], list[dict]]:
    """Rapid Shot as the main skill fires only part of the fight: lockout at maximum Heat. `rapid`: its Skill with
    level_stats (maximum Heat, lockout); `spenders`: (name, uses per second, how) for the build's Heat spenders."""
    if main is None or main.name != RAPID_SHOT:
        return [], []
    level = (rapid.level_stats if rapid is not None else {}) or {}
    most = number(level.get("maximum_number_of_crossbow_heat_stacks")) or HEAT_MAX
    lockout = (number(level.get("skill_specific_stat_description_mode")) / 1000) or HEAT_LOCKOUT
    attacks = number(stats.get("Speed"))
    if attacks <= 0:
        return [], []
    spent = sum(HEAT_SPENDERS.get(name, 15.0) * uses for name, uses, _ in spenders)
    gain = attacks * HEAT_PER_ATTACK - spent
    who = "; ".join(f"{name} {uses:.2f}/s ({how}) spends {HEAT_SPENDERS.get(name, 15.0) * uses:.1f} Heat/s"
                    for name, uses, how in spenders) or "nothing in the build spends Heat"
    play = (f"Rapid Shot gains {HEAT_PER_ATTACK:g} Heat per attack ({attacks:.2f}/s) up to {most:g}; at {most:g} it can't fire "
            f"for {lockout:g} s. {who}.")
    if gain <= 0:
        return [], [{"label": "Rapid Shot: Heat never reaches maximum", "value": "x1",
                     "basis": play + " The spenders take at least what it makes, so it never locks out (needs them used "
                              "at that rate: a play strategy*)", "confidence": "MEDIUM"}]
    drained = min(most, HEAT_DECAY_RATE * max(0.0, lockout - HEAT_DECAY_DELAY) + spent * lockout)
    refill = drained / gain
    share = refill / (refill + lockout) if refill > 0 else 0.0
    mods = [line("1% more Damage", -100 * (1 - share), skill=RAPID_SHOT)]
    return mods, [{"label": "Rapid Shot: share of the fight it can fire (Heat lockout)*", "value": f"{share:.1%}",
                   "basis": play + f" Heat rises {gain:.2f}/s while firing; in each {lockout:g} s lockout {drained:.0f} Heat "
                            f"drains (decay {HEAT_DECAY_RATE:g}/s from {HEAT_DECAY_DELAY:g} s after the last gain, and the "
                            f"spenders), refilled in {refill:.2f} s of firing: {share:.1%} of the fight firing (PoB: 100%). "
                            "Counted as less Damage on Rapid Shot (the fight's average). Heat per attack and the decay "
                            "are from the 0.3.0 notes (MEDIUM-HIGH); the time locked out is free for other skills, not "
                            "counted. With a Grenade or Incendiary Shot spending 15 Heat per use, it locks out less",
                   "confidence": "MEDIUM"}]


# Different Non-Instant skills used per 8 s (MECHB4; AUDIT7 A7-2, research/cast-speed-per-different-spell.md) -------------
# Counts COV-B's Emboldening Casts / Enhancing Attacks read (PoB multipliers LedgerDifferentNonInstantSpells8s /
# LedgerDifferentNonInstantAttacks8s, passive_models.different_skills_model), and PoB's NonInstantSpellCastRecently, which
# Rapid Casting III ("for each different Non-Instant Spell you've Cast Recently") and the Kurgal wand suffix read and nothing
# set (0 before). A skill counts when the player uses it at least once per window: the main skill, and every other
# non-instant skill the build has that its cooldown allows that often (a skill in the build is used: a play strategy, with
# the "only the main skill" row in the note). Not counted: triggered skills (see below), Persistent skills (placed once),
# skills a totem uses, instant skills
# (base cast time 0, the bridge's own test). Triggered skills stay out: the game's Trigger definition says "Triggering a
# Skill does not count as using it" (AUDIT17 A17-x: HIGH). PoB's NonInstantSpellCastRecently (Rapid Casting III, the Kurgal
# suffix: "for each different Non-Instant Spell you've Cast Recently", ModParser.lua 1998, SkillStatMap.lua 630; no other
# reader, checked: grep of PoB's src and our builder) takes its own count over Recently, 4 s (MECHB6, AUDIT17 A17-2; before,
# the 8 s count: one spell more where a cooldown is 4-8 s).
DIFFERENT_WINDOW = 8.0


def different_skills_counts(skills: list[Skill], main: Skill | None, persistent: set[str]
                            ) -> tuple[list[dict], list[dict]]:
    """The different Non-Instant Spells and Attacks the build uses per 8 s, as model inputs (see above)."""
    def used(s: Skill, window: float = DIFFERENT_WINDOW) -> bool:
        if s.triggered or s.name in persistent or (s.cast_time or 0.0) <= 0 or cast_by_totem(skills, s):
            return False
        if main is not None and s.name == main.name:
            return True
        cooldown = number(s.cooldown) / (s.cooldown_recovery or 1.0)
        return cooldown <= window

    counts = {}
    for kind, test in (("Spells", lambda s: s.spell), ("Attacks", lambda s: s.attack and not s.spell)):
        counts[kind] = sorted({s.name for s in skills if test(s) and used(s)})
    mods = [derived("DifferentNonInstantSpells8s", "", len(counts["Spells"])),
            derived("DifferentNonInstantAttacks8s", "", len(counts["Attacks"]))]
    recent = sorted({s.name for s in skills if s.spell and used(s, RECENTLY)})
    if recent:
        mods.append(raw_mod("Multiplier:NonInstantSpellCastRecently", "BASE", len(recent)))
    notes = [{"label": f"Different Non-Instant {kind} used per {DIFFERENT_WINDOW:g} s", "value": len(names),
              "basis": (", ".join(names) if names else "none") + ": the main skill and every other non-instant one the "
                       f"build has whose cooldown lets you use it once per {DIFFERENT_WINDOW:g} s (a play strategy: use each "
                       "that often; with only the main skill: 1). Triggered, Persistent, totem and instant skills not "
                       "counted (\"Triggering a Skill does not count as using it\": the game's Trigger definition)",
              "confidence": "MEDIUM"} for kind, names in counts.items() if names]
    if recent:
        notes.append({"label": f"Different Non-Instant Spells Cast Recently ({RECENTLY:g} s)", "value": len(recent),
                      "basis": ", ".join(recent) + f": the same play, over Recently ({RECENTLY:g} s), for Rapid Casting III "
                               "and the Kurgal wand suffix (\"... you've Cast Recently\")", "confidence": "MEDIUM"})
    return mods, notes


# Aftershock chance, summed (MECHB5, 12 Nov 2026; FIX_QUEUE COVD8's finding, RESEARCH7 aftershock-chance-stacking.md).
# KeywordPopups Aftershock: "If not otherwise specified, the Aftershock will deal the same damage as the initial impact, in
# the same area. Only Attack damage can cause Aftershocks." The game sums every chance source into one chance per hand
# (the virtual_*_slam_aftershock_chance_% stats) and rolls it once (RESEARCH7, MEDIUM-HIGH; over 100% taken as one extra
# Aftershock, UNVERIFIED, research INDEX #40). The ledger turns each item / passive line into its own "more" (Hymn's
# "Slam Skills have 100% more DPS", the four aftershock-chance entries' "Slam Skills deal N% more Damage"), so:
#   - lines multiply instead of adding (Hymn + Earthbreaker x2.5 where the game gives x2: an overcount);
#   - the Aftershock I-III supports' slam_aftershock_chance_% (25 / 35 / 15, GrantedEffectStatSets) maps in PoB to
#     AftershockChance, which no calculation reads (checked: grep AftershockChance over vendor/PathOfBuilding-PoE2/src
#     finds SkillStatMap.lua only; RULE_AUDIT A6-29): an undercount;
#   - the ledger drops "you use yourself", so a triggered or totem slam gets those lines too (an overcount);
#   - in the DPS form PoB counts each Aftershock as its own ailment application (stacks x (1 + a), CalcOffence 5254),
#     but the impact and its Aftershock land together: with one Ignite / Bleed / Poison slot the second application only
#     refreshes the first, so applications per slam are 1 - (1 - p)(1 - A p) = p (1 + A - A p) (an overcount of up to
#     x1.33 on the ailment at p = 0.5, A = 1; none at full uptime). With more than one slot both count.
# The model sets the main slam's hits to 1 + A (A = min(1, summed chance)) with one DPS "more" against what the ledger
# already applied, and each one-slot ailment's applications to p (1 + A - A p) through its duration (an ailment's damage
# per second doesn't depend on its duration in PoB, so its uptime moves and a full-uptime ailment's damage doesn't).
# Left as they are (labelled): the "more Damage" form also makes each ailment application bigger (COV-A: switch the
# aftershock-chance entries to the DPS form); other slams in Full DPS keep the ledger's per-line mores; whether an
# Aftershock rolls its own damage (UNVERIFIED, COVD8).
# Tremors (MECHB9; COVE10's parts, support_part_scale(main, "aftershocks"): AftershockChanceOne / Two / Three = 30 / 15 /
# 10, SupportTremorsPlayer; gem_stat_descriptions: "Supported Skills have {0}% chance to cause an additional / two
# additional / three additional Aftershocks", no "you use yourself"). Expected extra Aftershocks a slam: 1 x 30% + 2 x 15%
# + 3 x 10% = 0.9, the same whether it's one roll or three independent ones that add up (only "the biggest one wins"
# would give less, 0.80). They're taken as added on top of the summed single-Aftershock chance (the 0.2.0 notes: "Fixed a
# bug where Tremors Support could interfere with aftershock chances from other sources", so they coexist), and as one roll
# for the one-slot ailments' applications: UNVERIFIED both, MEDIUM-LOW. PoB has no Tremors gem, so this reads nothing
# until gem resolution can socket a gem PoB lacks (COV-C); its 35% less Damage is a plain support line (COVE10).
TREMORS_PARTS = {"AftershockChanceOne": 1, "AftershockChanceTwo": 2, "AftershockChanceThree": 3}
# Counted since MECHB7 (AUDIT19 A19-2, an undercount): chance_to_aftershock_+%_per_250_ms_attack_time, 15 on both
# Aftershock III (SupportAftershockChancePlayerThree) and Reverberate (SupportReveberatePlayer, live), game files
# GrantedEffectStatSets (checked: the stat's index in Mods, PassiveSkills, SoulCoreStats and the GrantedEffect stat,
# per-level and quality tables: only those two stat sets; PoB's Data/Skills and ModCache agree; poe2db not checked). Its text
# (gem_stat_descriptions): "Supported Skills have {0}% increased chance to cause Aftershocks for every quarter second of
# Attack time": an increase to the slam's summed chance, 15% x attack time / 0.25 s. Attack time is taken as the main
# slam's 1 / Speed from PoB (its attack time after speed modifiers; whether the game uses the base time instead:
# UNVERIFIED, MEDIUM). Aftershock III supports only "Slams you use yourself"; Reverberate any slam with a chance.
AFTERSHOCK_SUPPORTS = {"Aftershock I": 25.0, "Aftershock II": 35.0, "Aftershock III": 15.0}  # slam_aftershock_chance_%
AFTERSHOCK_PER_QUARTER = {"Aftershock III": 15.0, "Reverberate": 15.0}  # chance_to_aftershock_+%_per_250_ms_attack_time
AFTERSHOCK_LINES = (  # (pattern, "self" = Slams you use yourself / "any", the condition it needs)
    (re.compile(r"^(\d+(?:\.\d+)?)% chance for Slam Skills you use yourself to cause an additional Aftershock$", re.I),
     "self", ""),
    (re.compile(r"^Slam Skills you use yourself cause an additional Aftershock$", re.I), "self", ""),
    (re.compile(r"^(\d+(?:\.\d+)?)% chance for Mace Slam Skills you use yourself to cause an additional Aftershock$",
                re.I), "self", "Mace"),
    (re.compile(r"^(\d+(?:\.\d+)?)% chance for Shapeshift Slam Skills you use yourself to cause an additional "
                r"Aftershock$", re.I), "self", "Shapeshift"),
    (re.compile(r"^(\d+(?:\.\d+)?)% chance for Slam Skills to cause an additional Aftershock$", re.I), "any", ""),
)
LEDGER_SLAM_MORE = re.compile(r"^Slam Skills (deal|have) (\d+(?:\.\d+)?)% more (Damage|DPS)(?: (while .*))?$", re.I)
ONE_SLOT_AILMENTS = ("Ignite", "Bleed", "Poison")


def aftershock_chance(texts: list[str], group_gems: list[dict], yourself: bool, mace: bool, shapeshift: bool,
                      attack_time: float = 0.0) -> tuple[float, list[str]]:
    """The main slam's summed Aftershock chance in % (uncapped) and its sources; attack_time (s) for the supports'
    "increased chance per quarter second of Attack time"."""
    total, why = 0.0, []
    for text in texts:
        t = text.strip()
        for pattern, who, needs in AFTERSHOCK_LINES:
            if m := pattern.search(t):
                if not ((who == "self" and not yourself) or (needs == "Mace" and not mace)
                        or (needs == "Shapeshift" and not shapeshift)):
                    value = float(m.group(1)) if m.groups() else 100.0
                    total += value
                    why.append(f"{value:g}% ({t})")
                break
    if yourself:
        for gem in group_gems:
            if gem.get("enabled", True) and gem.get("name") in AFTERSHOCK_SUPPORTS:
                total += AFTERSHOCK_SUPPORTS[gem["name"]]
                why.append(f"{AFTERSHOCK_SUPPORTS[gem['name']]:g}% ({gem['name']})")
    per_quarter = sum(AFTERSHOCK_PER_QUARTER[g["name"]] for g in group_gems
                      if g.get("enabled", True) and g.get("name") in AFTERSHOCK_PER_QUARTER
                      and (yourself or g["name"] != "Aftershock III"))
    if total > 0 and per_quarter > 0 and attack_time > 0:
        increase = per_quarter * attack_time / 0.25
        total *= 1 + increase / 100
        names = ", ".join(n for n in AFTERSHOCK_PER_QUARTER if any(g.get("name") == n for g in group_gems))
        why.append(f"x{1 + increase / 100:.2f}: {increase:.0f}% increased ({names}: {per_quarter:g}% per 0.25 s of a "
                   f"{attack_time:.2f} s attack time, MEDIUM)")
    return total, why


def ledger_slam_mores(texts: list[str], forbidden: set[str], mace: bool, shapeshift: bool,
                      translate: Callable[[str], tuple | None]) -> tuple[float, float]:
    """What the ledger already applies to a slam for the Aftershock lines: (product of its "more Damage" factors,
    product of its "more DPS" factors). A "while" condition the slam can't meet here (Mace, Shapeshifted) isn't counted."""
    damage = dps = 1.0
    for text in texts:
        t = text.strip()
        if not any(p.search(t) for p, _, _ in AFTERSHOCK_LINES):
            continue
        found = translate(t)
        if not found or found[0].id in forbidden:
            continue
        for out in found[1]:
            if m := LEDGER_SLAM_MORE.match(out.strip()):
                cond = (m.group(4) or "").lower()
                if ("mace" in cond and not mace) or ("shapeshift" in cond and not shapeshift):
                    continue
                factor = 1 + float(m.group(2)) / 100
                if m.group(3).lower() == "dps":
                    dps *= factor
                else:
                    damage *= factor
    return damage, dps


def aftershock_model(texts: list[str], stats: dict, main: Skill | None, main_types: set[str], group_gems: list[dict],
                     weapon_types: set[str], totem: bool = False, forbidden: set[str] = frozenset(),
                     translate: Callable[[str], tuple | None] | None = None) -> tuple[list[dict], list[dict]]:
    """The main slam's Aftershocks (see above): a DPS "more" bringing its hits to 1 + A against the ledger's, and each
    one-slot ailment's duration "more" bringing its applications to p (1 + A - A p). ([], []) for a non-slam."""
    if main is None or "Slam" not in main_types or not main.attack:
        return [], []
    if translate is None:
        from builder.ledger import translate
    yourself = not (main.triggered or totem or "Triggered" in main_types)
    mace = any("Mace" in w for w in weapon_types) if weapon_types else True
    shapeshift = any("Shapeshift" in t for t in main_types)
    speed = number(stats.get("Speed"))
    total, why = aftershock_chance(texts, group_gems, yourself, mace, shapeshift, 1 / speed if speed > 0 else 0.0)
    damage, dps = ledger_slam_mores(texts, forbidden, mace, shapeshift, translate)
    parts = support_part_scale(main, "aftershocks")[0]
    tremors = {k: min(1.0, max(0.0, number(parts.get(name)) / 100)) for name, k in TREMORS_PARTS.items()}
    tremors = {k: c for k, c in tremors.items() if c > 0}
    extra = sum(k * c for k, c in tremors.items())
    if total <= 0 and damage == 1.0 and dps == 1.0 and not extra:
        return [], []
    a = min(1.0, total / 100)
    target, applied = 1 + a + extra, damage * dps
    if extra:
        why.append(f"Tremors: {' + '.join(f'{k} x {c:.0%}' for k, c in sorted(tremors.items()))} = {extra:.2f} more "
                   "Aftershocks a slam, on top (one roll or several adding up give the same; UNVERIFIED how they meet "
                   "the other chances, MEDIUM-LOW)")
    mods: list[dict] = []
    correction = target / applied - 1
    if abs(correction) > 1e-6:
        mods.append(raw_mod("DPS", "MORE", 100 * correction, main.name))
    whose = "used by you" if yourself else "triggered or a totem's, so only the \"Slam Skills\" lines count"
    notes = [{"label": f"Aftershock chance, summed ({main.name})",
              "value": f"{a:.0%}" + (f" + {extra:.2f} Aftershocks (Tremors)" if extra else ""),
              "basis": f"{'; '.join(why) or 'no source'}; the game sums the chances and rolls once (capped at one extra "
                       f"Aftershock, UNVERIFIED), so the slam's hits are x{target:.2f}; the ledger's per-line mores gave "
                       f"x{applied:.2f} (Damage x{damage:.2f}, DPS x{dps:.2f}), corrected by x{target / applied:.3f}; "
                       f"the slam is {whose}",
              "confidence": "MEDIUM"}]
    crit = min(max(number(stats.get("CritChance")) / 100, 0.0), 1.0)
    for ailment in ONE_SLOT_AILMENTS:
        p = min(1.0, (number(stats.get(f"{ailment}ChanceOnHit")) * (1 - crit)
                      + number(stats.get(f"{ailment}ChanceOnCrit")) * crit) / 100)
        if p <= 0:
            continue
        slots = number(stats.get(f"{ailment}StacksMax")) or 1.0
        # applications per slam against a slam without Aftershocks: P(any hit applies it) / p with one slot, where the
        # extra hits are the single roll (a) and Tremors' one roll (k more at chance c_k)
        miss = (1 - a * p) * (1 - sum(tremors.values()) + sum(c * (1 - p) ** k for k, c in tremors.items()))
        real = (1 - (1 - p) * miss) / p if slots <= 1 else target
        counted = target / damage  # what PoB counts after the DPS correction (the "more Damage" form adds none)
        if abs(real / counted - 1) <= 1e-6:
            continue
        mods.append(raw_mod(f"Enemy{ailment}Duration", "MORE", 100 * (real / counted - 1), main.name))
        slot = f"one {ailment} slot" if slots <= 1 else f"{slots:g} {ailment} slots"
        notes.append({"label": f"{ailment} applications with Aftershocks ({main.name})", "value": f"x{real / counted:.3f}",
                      "basis": f"{p:.0%} chance a hit; the impact and its Aftershock land together, so with {slot} a "
                               f"slam applies it {real:.3f} times as often as without Aftershocks (1 - (1-p)(1-Ap)), "
                               f"where PoB counts {counted:.3f}; set through its duration, so its uptime moves and a "
                               "full-uptime ailment's damage doesn't"
                               + (f"; the ledger's \"more Damage\" form still makes each application x{damage:.2f} "
                                  "bigger (COV-A)" if damage > 1 else ""),
                      "confidence": "MEDIUM"})
    return mods, notes


# Orb of Storms as the main skill (MECHB6; COVC9's finding, FIX_QUEUE "MECHB5's and COVC9's findings") -------------------
# The game (ActiveSkills orb_of_storms, OrbOfStormsPlayer's constant stats, build 26476662; poe2db's gem page): "Fires a
# bolt every 4 seconds, and when you cast a Spell near the Orb" (orb_of_storms_base_bolt_frequency_ms 4000), "Limit 1 Orb
# of Storms" (orb_of_storms_base_maximum_number_of_orbs 1), 12 s duration (base_skill_effect_duration 12000), "Orb expires
# after firing 6 bolts" (orb_of_storms_maximum_number_of_hits 6: our stat descriptions have no text for it, poe2db's page
# does, MEDIUM). The cast itself deals no hit; PoB prices each cast as one bolt (no statMap, no module reads the skill:
# COVC9's grep). What a recast does is not in any source: whether casting a new Orb is "a Spell cast near" the old one, and
# whether the old one is gone first (UNVERIFIED either way). With it, each cast fires one bolt from the orb it replaces,
# and the 6-bolt cap never binds (one bolt per orb): PoB's count. Without it, the orb fires on its timer only: 3 bolts
# per 12 s orb (at 4, 8, 12 s; the 12 s one at the edge, so 2-3), plus the recast. The headline keeps PoB's count (no
# zero or cut on an unverified point, CLAUDE.md rule 5) and the note gives the rate without it; the in-game check is
# held for the user. Bolts from other spells cast near the orb (Orb as a side skill) aren't counted: an undercount.
ORB_OF_STORMS = "Orb of Storms"
ORB_BOLT_EVERY, ORB_DURATION, ORB_MAX_BOLTS = 4.0, 12.0, 6


def orb_of_storms_note(main: Skill | None, stats: dict) -> list[dict]:
    """A labelled note on Orb of Storms as the main skill: PoB's one bolt per cast holds only if recasting fires a bolt."""
    if main is None or main.name != ORB_OF_STORMS:
        return []
    casts = number(stats.get("Speed"))
    if casts <= 0:
        return []
    timer_bolts = min(ORB_MAX_BOLTS, int(ORB_DURATION // ORB_BOLT_EVERY))
    without = timer_bolts / (ORB_DURATION + 1 / casts)
    return [{"label": "Orb of Storms: bolts per second (one per cast, as PoB counts)*",
             "value": f"{casts:.2f}/s (without a bolt on recast: {without:.2f}/s, {without / casts:.0%} of the DPS)",
             "basis": "the Orb fires a bolt every 4 s and when you cast a Spell near it, one Orb at a time, 12 s, at most "
                      "6 bolts (game files; poe2db). Kept: each recast fires one bolt from the Orb it replaces (UNVERIFIED: "
                      f"no source says whether a recast counts as a Spell cast near the old Orb). Without it, {timer_bolts} "
                      f"timer bolts per {ORB_DURATION:g} s Orb plus the recast: {without:.2f} bolts/s. Bolts fed by other "
                      "spells cast near the Orb aren't counted (an undercount when it's a side skill)",
             "confidence": "UNVERIFIED"}]


# Danse Macabre's second skeleton (MECHB6; RESEARCH22 #133, research/danse-macabre-skeleton-use.md) -------------------------
# The gem (GemEffects DanseMacabreSupport, PoB's copy of it): Supported Offerings "will target an additional skeletal Minion
# when cast. If there is not an additional skeletal Minion to target, no damage or Buff effect will be granted"
# (skill_offering_targets_an_additional_skeleton; its 30% more damage and 30% increased Buff effect). Offerings use up the
# skeleton they target (KeywordPopups Offering; "Impale / Skewer a Skeleton on a bone spike"): with Danse Macabre each cast
# uses 2 (MEDIUM-HIGH). PoB applies the bonus with no check. The build's skeletons: its Persistent "Skelet..." minion
# skills' limits (other skeleton sources aren't counted: an undercount). They revive T s after the last one is used
# (revive_seconds; KeywordPopups Reviving: the delay resets on each death), so with N skeletons an Offering kept up every
# D s (its base spike duration, game files: Bone 8 s, Pain 6 s, Soul 6 s; duration modifiers not read, an undercount)
# casts k = N // 2 times per wave, then waits for the revive: up k D of every max(k D, (k - 1) D + T) s. That share of the
# Offering's damage and Buff effect counts (as less of both, MECHB6's play: cast it only when 2 skeletons are there);
# with fewer than 2 skeletons, none. The base Offering's own single skeleton isn't checked here (PoB's, unchanged).
OFFERING_SECONDS = {"Bone Offering": 8.0, "Pain Offering": 6.0, "Soul Offering": 6.0}
DANSE_MACABRE = "Danse Macabre"


def danse_macabre_model(skills: list[Skill], supports: dict[int, list[str]] | None) -> tuple[list[dict], list[dict]]:
    """Danse Macabre's Offerings count only the share of the fight the build's skeletons let them keep up (see above)."""
    offerings = [s for s in skills if s.name in OFFERING_SECONDS and s.group is not None
                 and DANSE_MACABRE in (supports or {}).get(s.group, [])]
    if not offerings:
        return [], []
    skeletons = [s for s in skills if s.summon and s.summon.get("persistent") and "Skelet" in s.name
                 and not number(s.summon.get("duration"))]
    count = int(sum(number(s.summon.get("limit")) for s in skeletons))
    revive, how = revive_seconds(skeletons) if skeletons else (RESUMMON_SECONDS, "no skeletons")
    mods, notes = [], []
    for offering in offerings:
        every = OFFERING_SECONDS[offering.name]
        casts = count // 2
        if casts <= 0:
            share, why = 0.0, (f"the build has {count} skeleton{'s' if count != 1 else ''}: each cast needs 2, so no "
                               "cast gets damage or Buff effect")
        else:
            cycle = max(casts * every, (casts - 1) * every + revive)
            share = casts * every / cycle
            why = (f"{count} skeletons ({', '.join(s.name for s in skeletons)}): {casts} cast{'s' if casts > 1 else ''} of 2 "
                   f"each, one every {every:g} s (its base spike duration), then they revive {revive:.3g} s after the last "
                   f"({how}): up {casts * every:g} s of every {cycle:.3g} s")
        if share < 1:
            mods += [raw_mod("Damage", "MORE", -100 * (1 - share), skill=offering.name),
                     raw_mod("BuffEffect", "MORE", -100 * (1 - share), skill=offering.name)]
        notes.append({"label": f"{offering.name} with Danse Macabre: share of the fight it gives damage and Buff effect*",
                      "value": f"{share:.0%}",
                      "basis": f"each cast uses 2 skeletons, and without the second it gives no damage or Buff effect (the "
                               f"gem's text; PoB applies it without checking); {why}. Counted as less damage and Buff "
                               "effect on the Offering (MEDIUM-HIGH that each cast uses 2; skeletons from other sources "
                               "and duration modifiers not counted). Without Danse Macabre: one skeleton per cast, no 30%",
                      "confidence": "MEDIUM"})
    return mods, notes


# Coursing Current against a lone boss (MECHB6; RESEARCH22 #129, research/coursing-current-lone-boss.md) ---------------------
# "Sends a Chaining Lightning Projectile from the Shocked enemy towards a nearby Drenched enemy" with
# cannot_chain_to_previous_targets and projectile_only_collide_with_wet_targets (GrantedEffectStatSets, game files): alone,
# the Shocked boss is its previous target, so it has none (MEDIUM; poe2db has no single-target note). PoB scores it 0 too:
# its player trigger calculation is commented out (CalcPerform.lua 3434) and a 0 s cast with no trigger rate gets Speed 0
# (CalcOffence.lua 2708), and no model of ours gives it a rate (checked: grep of builder for Coursing Current, 12 Nov). The
# note says why it stays 0; against adds it would hit.
COURSING_CURRENT = "Coursing Current"


def coursing_current_note(skills: list[Skill]) -> list[dict]:
    if not any(s.name == COURSING_CURRENT for s in skills):
        return []
    return [{"label": "Coursing Current: hits on a lone boss", "value": 0,
             "basis": "its projectile starts from the Shocked enemy and can't chain to previous targets (its stat set, game "
                      "files): with no second Drenched enemy it has no target. PoB scores it 0 as well (its player trigger "
                      "calculation is off). It would hit adds (not counted). In-game test held for the user",
             "confidence": "MEDIUM"}]


# Apocalypse's disasters (MECHB7; plans/kw-unleash.md item 1, RESEARCH20 #114, RESEARCH #103). ApocalypsePlayer (PoB
# Data/Skills/other.lua 895; game files): "When you have maximum Glory, you may become the walking Apocalypse for a
# duration, Triggering one of multiple powerful Elemental Skills at an interval while this Buff lasts":
# unleash_the_elements_base_trigger_frequency_ms 750, unleash_the_elements_trigger_frequency_+% (0.75 per quality),
# 12 s buff, 100 Glory a use (GLORY_SKILLS). The gem grants three hidden sub-skills, castTime 0, InbuiltTrigger:
# Hailstorm (cold, unleash_blizzard_number_of_bolts 9), Meteor (fire, one impact after 2 s) and Crash Lightning
# (lightning: 3 pulses, set 1, then a "Final Strike", set 2, after 3 s). PoB gives all three Speed 0 (its player trigger
# calculation is switched off, CalcPerform.lua 3514-3515; CalcOffence 2848): 0 DPS, a pure undercount (RESEARCH20 #114,
# HIGH from the code). The model:
#   - the buff's share of the fight U = min(1, Apocalypse's uses per second (glory_uses, from the build's own elemental
#     hits: no flat rate) x 12 s); increased skill duration isn't read (an undercount, labelled);
#   - triggers per second while it's up = (1 + frequency %) / 0.75 s (HIGH: game files);
#   - each disaster a third of them (RESEARCH #103: no rule anywhere says which fires; 1/3 holds for random or a cycle,
#     MEDIUM; in-game test #109);
#   - hits on the boss per trigger: Meteor 1; Hailstorm 9 bolts, all on the boss (LOW: each bolt lands at a random spot
#     in the 2 m area, a 0.8 m blast; "Targets an area or an enemy", where they land is UNVERIFIED #109), counted as 9
#     hits; Crash Lightning 1 of whichever set PoB calculates (an undercount: the pulses' other 2 and the other set aren't
#     counted, since one rate covers both sets and the gem search switches between them).
# Each sub-skill gets that rate as its trigger rate, so PoB's own average hit x the rate is its DPS (Full DPS sums them;
# a sub-skill picked as the main skill counts alone).
APOCALYPSE = "Apocalypse"
APOCALYPSE_SUBS = {"Hailstorm": 9, "Meteor": 1, "Crash Lightning": 1}  # hits on the boss per trigger
APOCALYPSE_DURATION = 12.0
APOCALYPSE_INTERVAL = 0.75
APOCALYPSE_FREQUENCY = "unleash_the_elements_trigger_frequency_+%"


def apocalypse_model(uses: float, basis: str, frequency: float = 0.0,
                     present: set[str] | frozenset[str] = frozenset(APOCALYPSE_SUBS)) -> tuple[list[dict], list[dict]]:
    """Trigger rates for Apocalypse's sub-skills from its uses per second (`uses`, glory_uses; `basis` says how) and its
    trigger frequency increase in % (quality)."""
    share = min(1.0, max(uses, 0.0) * APOCALYPSE_DURATION)
    per_second = (1 + frequency / 100) / APOCALYPSE_INTERVAL
    each = share * per_second / len(APOCALYPSE_SUBS)
    mods: list[dict] = []
    for name, hits in APOCALYPSE_SUBS.items():
        if name in present:
            mods += trigger_rate(name, each * hits)
    notes = [{"label": "Apocalypse's disasters per second", "value": round(share * per_second, 3),
              "basis": (f"the buff is up {share:.0%} of the fight ({uses:.3f} uses/s x {APOCALYPSE_DURATION:g} s; {basis}); "
                        f"one disaster every {APOCALYPSE_INTERVAL / (1 + frequency / 100):.2f} s while it's up "
                        f"({frequency:g}% increased frequency); a third each Hailstorm, Meteor, Crash Lightning "
                        "(MEDIUM: no rule says which fires); Hailstorm's 9 bolts all counted on the boss (LOW: where they "
                        "land is UNVERIFIED); Crash Lightning counts one hit of the set calculated (an undercount: its "
                        "other pulses and its Final Strike aren't counted); increased duration isn't read (an undercount)"),
              "play": "Use Apocalypse whenever you have 100 Glory; its disasters fire by themselves while it lasts.",
              "confidence": "LOW"}]
    return mods, notes


# Doedre's Undoing's Witchtoads (MECHB8; FIX_QUEUE #95 / MECHB7's Handover). The lineage support (game files
# GrantedEffectStatSets SupportDoedresUndoingPlayer; PoB sup_int.lua 3087): "Supported Skills create an area which Curses
# Enemies within it, and spawns Witchtoads in that area. Witchtoads leap at enemies that get close ... Dark Consequences
# will be Triggered when they hit enemies with their leap, violently exploding the Witchtoad" (curse_apply_as_curse_zone,
# spawn_doedre_toads_on_cursed_ground; maximum_curse_zones_allowed 2, max_toads_per_doedre_cursed_ground 4,
# doedre_cursed_ground_toad_spawn_interval_ms 250). PoB (engine probe POE2T1015) rates Dark Consequences at one explosion
# per spawn interval, castTime 1 x hitTimeMultiplier 0.25 = 4 a second, whether or not a cursed area is up, and only in
# Full DPS if the curse's group is in it (no model put it there: checked, every extra_groups source in
# Assumptions.models (energy, mirage, Spirit Vessel, Expression groups); a group the player marks keeps PoB's flag). The model:
#   - explosions per second = (1 / spawn interval) x the share of the fight the boss stands in one area: the curse's
#     own share (curse_uses: a meta's rate x its duration, else recast by hand each duration). A trigger rate r gives PoB
#     4r hits a second (its hit time is the use time x 0.25), so r = that share;
#   - every toad spawned leaps at the boss and explodes (MEDIUM: "leap at enemies that get close"; a boss is close while
#     it stands in the area; the toad's leap time isn't in the files, so its spawns aren't held to the limit of 4);
#   - a second area (the support allows 2) isn't counted: an undercount, labelled, since keeping both on the boss costs a
#     second cast each duration and whether toads from both reach it is UNVERIFIED;
#   - each explosion is a Witchtoad dying ("died", minion_died_rate; KeywordPopups MinionDeath).
DOEDRE = "Doedre's Undoing"
DARK_CONSEQUENCES = "Dark Consequences"
TOAD_SPAWN_SECONDS = 0.25  # doedre_cursed_ground_toad_spawn_interval_ms 250
TOADS_PER_AREA = 4  # max_toads_per_doedre_cursed_ground
CURSE_AREAS = 2  # maximum_curse_zones_allowed (on Doedre's Undoing; Cursed Ground and Hayoxi's Binding carry it too)


def toad_model(skills: list[Skill], curses: list[CurseUse]) -> tuple[list[dict], list[dict], set[int], float]:
    """Dark Consequences' rate from the share of the fight its curse's area is up (see above): (lines, notes, groups to
    count in Full DPS, Witchtoads dying per second)."""
    toads = [s for s in skills if s.name == DARK_CONSEQUENCES and s.group is not None]
    if not toads:
        return [], [], set(), 0.0
    mods: list[dict] = []
    notes: list[dict] = []
    groups: set[int] = set()
    died = 0.0
    shares = {c.name: c for c in curses}
    for toad in toads:
        curse = next((shares[s.name] for s in skills if s.group == toad.group and s.name in shares), None)
        share = curse.share if curse is not None else 0.0
        how = f"{curse.name} {curse.how}" if curse is not None else "no curse in its group is applied (0)"
        per_second = share / TOAD_SPAWN_SECONDS
        mods += trigger_rate(DARK_CONSEQUENCES, share)
        if share > 0:
            groups.add(toad.group)
        died += per_second
        notes.append({"label": "Dark Consequences (Doedre's Undoing's Witchtoads) explosions per second*",
                      "value": round(per_second, 3),
                      "basis": (f"a Witchtoad every {TOAD_SPAWN_SECONDS:g} s in the cursed area while the boss stands in it: "
                                f"{share:.0%} of the fight ({how}); every toad leaps at the boss and explodes (MEDIUM: the "
                                f"toads 'leap at enemies that get close'; their leap time isn't in the game files, so the "
                                f"{TOADS_PER_AREA}-toad limit isn't applied); a second area (the support allows "
                                f"{CURSE_AREAS}) isn't counted, an undercount (UNVERIFIED whether both areas' toads reach "
                                "the boss); PoB alone counts 4 a second whether or not an area is up"),
                      "play": (f"Keep the cursed area under the boss: recast the curse as it runs out. Casting it twice "
                               f"each duration keeps {CURSE_AREAS} areas up: up to {CURSE_AREAS}x the toads (not counted)."),
                      "confidence": "MEDIUM"})
    return mods, notes, groups, died


# Impending Doom's Doom Blast (MECHB12; SEARCH7's probe POE2T764: 71,251 of 2b75b's reference Full DPS, an OVERCOUNT).
# The support (game files GrantedEffects ViciousHexSupportPlayer; gem_stat_descriptions.csd): "Trigger Doom Blast when
# Curses from Supported Skills expire"; it supports AppliesCurse skills and excludes UsedByProxy, Triggered, Persistent
# and Aura ones (the same table's ExcludedActiveSkillTypes; PoB sup_int.lua: "Supports Curse skills you cast yourself").
# DoomBlastPlayer: cooldown 150 ms, 3-4 stored uses: no rate of its own. PoB has no trigger for it, so it scored Doom Blast
# at its own cast speed whenever its curse's group was in Full DPS (toad_model put Doedre's Undoing's group there, MECHB8).
# The model: one Doom Blast each time the curse expires on the boss:
#   - a curse cast by hand (curse_uses' by_hand) is recast each duration (CurseUse.duration: PoB's, with the build's
#     modifiers, AUDIT25 A25-41), landing as the last one runs out (its 1.5 s delay), so it expires once a cast:
#     1 / duration a second (MEDIUM: whether a curse replaced at the
#     moment it ends counts as expiring is UNVERIFIED; a player can let it lapse first, at the cost of curse uptime);
#     a Cursed Ground area (Doedre's Undoing) is counted the same (when its curse expires on the boss: UNVERIFIED);
#   - "Curses you inflict have infinite Duration" (CURSE_FOREVER): the curse never expires on a boss that lives, 0;
#   - any other application (a meta's trigger, Blasphemy's Aura, an item's curse on hit): the support excludes it, 0;
#   - its curse's group is counted in Full DPS when the rate is above 0 (before, only if another model put it there).
DOOM_BLAST = "Doom Blast"


def doom_blast_model(skills: list[Skill], curses: list[CurseUse]) -> tuple[list[dict], list[dict], set[int]]:
    """Doom Blast's rate from its curse's expiries (see above): (lines, notes, groups to count in Full DPS)."""
    blasts = [s for s in skills if s.name == DOOM_BLAST and s.group is not None]
    mods: list[dict] = []
    notes: list[dict] = []
    groups: set[int] = set()
    shares = {c.name: c for c in curses}
    for blast in blasts:
        curse = next((shares[s.name] for s in skills if s.group == blast.group and s.name in shares), None)
        if curse is None:
            rate, how = 0.0, "no curse in its group is applied (0)"
        elif not curse.by_hand and "infinite Duration" in curse.how:
            rate, how = 0.0, f"{curse.name} has infinite Duration, so it never expires on the boss (0)"
        elif not curse.by_hand:
            rate, how = 0.0, (f"{curse.name} is {curse.how.split(':')[0]}; Impending Doom supports only curses you cast "
                              "yourself (it excludes Triggered, Persistent, Aura and UsedByProxy curses: game files) (0)")
        else:
            rate = curse.share / curse.duration
            how = (f"{curse.name} cast by hand every {curse.duration:.3g} s (its duration), expiring once a cast as the next lands (MEDIUM: "
                   "whether a curse replaced as it ends counts as expiring is UNVERIFIED)")
        mods += trigger_rate(DOOM_BLAST, rate)
        if rate > 0:
            groups.add(blast.group)
        notes.append({"label": "Doom Blast (Impending Doom) per second*", "value": round(rate, 3),
                      "basis": f"one each time the curse expires on the boss: {how}; PoB alone scores it at its own "
                               "cast speed",
                      "play": "Let the curse run out on the boss before recasting it: each expiry triggers one Doom Blast.",
                      "confidence": "MEDIUM" if rate > 0 else "HIGH"})
    return mods, notes, groups


# Minions dying, not only Killed (MECHB8; plans/term-minion-dies.md, FIX_QUEUE #95, AUDIT15 A15-18). KeywordPopups
# MinionDeath: "Minions whose durations expire or that are despawned by the player are considered to have died. However,
# effects that state they trigger when a Minion is Killed do not trigger in these cases." KeywordPopups Limit: "If a new
# effect is created while at the maximum number of effects, the oldest effect will dissapear" (that this counts as a
# despawn: MEDIUM). So "when a Minion Dies" fires on all of these, summed:
#   - Killed: minion_deaths (Sacrifice, Explosive Demise; 0 with its note);
#   - Witchtoads exploding: toad_model's rate;
#   - Offerings: each cast impales (uses up) a skeleton, two with Danse Macabre (KeywordPopups Offering; RESEARCH22
#     #133), at the cycle danse_macabre_model uses: k = N // n casts of D s each, then the revive (MEDIUM: that a used
#     skeleton "died" and isn't only consumed is the MinionDeath reading);
#   - temporary minions from a triggered summon (Skill.minion: Wardbound Minions ...): each use's minion dies once, at its
#     duration's end or replaced at the limit, so one death per use at steady state (HIGH from MinionDeath).
# Not counted (labelled, undercounts): the boss killing your minions; summoning past a Limit by hand (no use rate for a
# pressed summon is modelled); Summoned Wolves (Predator's Mark Activates on its target's death, none on a lone boss, or
# when Cross Slash hits the Marked boss with both gouges (ActiveSkills wolf_cross_slash), which may summon a wolf and
# replace the oldest at the limit: UNVERIFIED, A22-3, held for the user's in-game test; a kill rate per boss preset would
# give some too); a second Witchtoad area. Offerings and Sacrifice may share skeletons: the
# sum isn't held to what the skeletons' revival allows together (a possible small overcount, labelled).
def minion_died_rate(skills: list[Skill], main: Skill | None, stats: dict, supports: dict[int, list[str]] | None = None,
                     toads: float = 0.0, rates: dict[str, float] | None = None) -> tuple[float, str]:
    """Your minions dying per second ("when a Minion Dies": Killed, expired, despawned or replaced), and how. `toads`:
    toad_model's Witchtoads per second; `rates`: triggered skills' uses per second (build.trigger_rates)."""
    killed, how = minion_deaths(skills, main, stats)
    total, parts = killed, [f"Killed {killed:.3f}/s ({how})"]
    if toads > 0:
        total += toads
        parts.append(f"Witchtoads exploding {toads:.3f}/s (toad_model)")
    skeletons = [s for s in skills if s.summon and s.summon.get("persistent") and "Skelet" in s.name
                 and not number(s.summon.get("duration"))]
    count = int(sum(number(s.summon.get("limit")) for s in skeletons))
    for offering in (s for s in skills if s.name in OFFERING_SECONDS):
        n = 2 if DANSE_MACABRE in (supports or {}).get(offering.group, []) else 1
        every, casts = OFFERING_SECONDS[offering.name], count // n
        if casts <= 0:
            parts.append(f"{offering.name}: no skeleton to impale (0)")
            continue
        revive, _ = revive_seconds(skeletons)
        cycle = max(casts * every, (casts - 1) * every + revive)
        used = casts * n / cycle
        total += used
        parts.append(f"{offering.name} impales {n} skeleton{'s' if n > 1 else ''} a cast, {casts} cast"
                     f"{'s' if casts > 1 else ''} per {cycle:.3g} s: {used:.3f}/s (MEDIUM: a used skeleton has died)")
    for spell in (s for s in skills if s.minion and s.triggered):
        use = number((rates or {}).get(spell.name))
        if use > 0:
            total += use
            parts.append(f"{spell.name}'s temporary minions {use:.3f}/s (one per use, each dies at its duration's end "
                         "or replaced at its limit)")
    parts.append("not counted: the boss killing your minions, summoning past a limit by hand, Summoned Wolves (a "
                 "Marked kill, or Cross Slash Activating the Mark on the boss: UNVERIFIED), a second Witchtoad area (undercounts); Offerings and Sacrifice sharing skeletons isn't "
                 "held to their revival together (a possible small overcount)")
    return total, "; ".join(parts)


# Kills the build makes in a boss fight (MECHB9; the manager's ruling on A17-4 / research #124, 12 Nov ~12:40, and the
# boss presets' adds, MECH-D's MECHD1). Two kinds, kept apart because the lines that read them say different things:
#   - enemies: the boss preset's killable adds (the King in the Mists' Affliction Totems, Atziri's waves), the stats'
#     ADD_KILLS_STAT (bosses.kill_stats, merged into the stats by Assumptions.models; 0 with a note against a lone
#     boss, where the kill ends the fight). Every add is taken as killed by the build (MECHD1:
#     a build taking on-kill lines plays to kill them, CLAUDE.md's second rule). LOW (the presets' add rates).
#   - your own minions Killed: the game's text calls a minion its summoner brings to 0 Life "Killed" (KeywordPopups
#     MinionDeath), so the ruling counts them as Kills in the headline at the build's own rate (minion_deaths: Sacrifice,
#     Explosive Demise, Minion Pact (MECHB11); Dark Pact / Abyssal Pact have no skill in PoB, so no use rate, not
#     counted), LOW, held for the user's in-game test, with a "without it" row.
# A line that says "an enemy" ("when you kill an enemy", Reaper's Invocation) reads the enemies only: your minions are
# allies, not enemies (the lines' own text; KeywordPopups MinionDeath / Allies). A line that says only "on Kill" reads
# both. The readers of plain on-Kill lines (Onslaught, Rage, charges, charms) are MECH-C's (conditions.py, flasks.py).
ADD_KILLS_STAT = "AddKillsPerSecond"
ADD_POWER = 1.0  # an add's monster Power (KeywordPopups Power: "An average monster has a Power of 1", Normal x1; LOW)


def kill_rate(stats: dict, own: float = 0.0, own_how: str = "") -> tuple[float, float, str]:
    """(enemies killed per second, all kills per second including your own minions Killed, how). `own`: minion_deaths'
    rate (only Killed minions, never expired or despawned ones)."""
    adds = max(number(stats.get(ADD_KILLS_STAT)), 0.0)
    if adds > 0:
        how = (f"{adds:.3f} of the boss's adds killed per second (the boss preset's kill rate, LOW; every add taken as "
               "killed by the build)")
    else:
        how = ("no enemy dies before the boss (a lone boss; the King in the Mists' and Atziri's adds come from the boss "
               "preset's AddKillsPerSecond, MECH-D's, when it's in the stats)")
    if own > 0:
        how += (f"; plus your own minions Killed {own:.3f}/s, counted as Kills by lines that don't say 'enemy' (the "
                f"ruling on A17-4: LOW, held for an in-game test; {own_how})")
    return adds, adds + own, how


def kills_note(kills: tuple[float, float, str]) -> list[dict]:
    """The play guide's line for the build's kills, with the without-own-minions row (empty when there are none)."""
    enemies, total, how = kills
    if total <= 0:
        return []
    note = {"label": "Kills in the boss fight", "value": f"{total:.3f}/s ({enemies:.3f}/s of them enemies)", "basis": how,
            "confidence": "LOW"}
    if total > enemies:
        note["play"] = (f"Kill your own minions to feed on-Kill lines (Sacrifice, Explosive Demise): with it "
                        f"{total:.3f} Kills/s, without it {enemies:.3f}/s. Whether killing your own minion counts as a "
                        "Kill for these lines is held for an in-game test (LOW).")
    return [note]


# Heartbound Loop (MECHB9; FIX_QUEUE, COVE10's row): "300 Physical Damage taken on Minion Death" (1000 on the Loreweave
# version; ModCache also holds 250 / 350 / 501 rolls). PoB parses it (HeartboundLoopSelfDamage) but applies it only per
# cast of Summon Skeletons, as damage per summoned minion (CalcOffence selfHitHandlers), never at the rate minions die.
# The game: on Minion Death, which KeywordPopups MinionDeath counts for minions that died in any way (Killed, expired,
# despawned): minion_died_rate's rate. So the damage taken per second is the lines' sum x that rate. (MECHB20, AUDIT29
# A29-5) It is Hit damage: "Any damage that isn't damage over time is Hit damage" (KeywordPopups Hit), and this is a
# one-off amount per death, so "damage taken over time" lines don't reduce it (PoB treats it as damage over time: 300 ->
# 225 in MECHC14's probe, an undercount of the damage taken). Armour may reduce it as a Physical Hit (whether a
# self-inflicted Hit meets Armour, and whether it can be evaded, is UNVERIFIED); it's not reduced here, so the damage
# taken is at most too high (the safe side for recovery). The recovery reader is MECH-C's (objective.boss_recovery): it's
# on build.self_damage and in the notes until then.
HEARTBOUND = re.compile(r"^(\d+(?:\.\d+)?) Physical Damage taken on Minion Death$", re.I)


def heartbound_damage(texts: list[str], died: tuple[float, str]) -> tuple[float, list[dict]]:
    """Physical damage per second the build takes from "N Physical Damage taken on Minion Death" lines at the build's
    minion-death rate (minion_died_rate's (per second, how)), and its note."""
    per = sum(float(m.group(1)) for t in texts if (m := HEARTBOUND.match(t.strip())))
    if per <= 0:
        return 0.0, []
    rate, how = died
    taken = per * max(rate, 0.0)
    return taken, [{"label": "Heartbound Loop: damage taken on Minion Death",
                    "value": f"{taken:,.0f} Physical damage per second",
                    "basis": (f"{per:g} per minion death x {rate:.3f} deaths per second ({how}); Hit damage (KeywordPopups Hit: "
                              "not damage over time), so damage-over-time lines don't reduce it; Armour isn't applied "
                              "(whether it meets a self-inflicted Hit is UNVERIFIED: at most too high); PoB counts it "
                              "only per Summon Skeletons cast, as damage over time"),
                    "confidence": "LOW",
                    "play": (f"Each of your minions dying costs you {per:g} Life: {taken:,.0f} per second at this "
                             "build's minion deaths" if taken > 0 else
                             "Nothing in this build makes its minions die, so the ring costs no Life here")}]


# Totems other than Spell Totem (MECHB8; MECHB7's Handover item 2). PoB prices one totem: the damaging part beside a
# totem-summoning skill (Shockwave Totem's Shockwave Slam, Artillery Ballista's Ballista Bolt, Siege Ballista's
# Artillery, Dark Effigy's Vile Effusion, a Mortar Cannon's grenade) carries no ActiveTotemLimit (engine probe POE2T1018:
# 0), and PoB multiplies only ailment stacks by the totem count (CalcOffence 5099-5101, a Single-ailment flag), never the
# hits. The game (KeywordPopups Totem): "Totems are allied constructs which use skills for you ... Totem Limit is shared
# between different types of Totem by default". The model:
#   - totems up = the summoning skill's Totem Limit (the bridge's totemLimit: PoB's ActiveTotemLimit / ActiveBallistaLimit
#     for that skill, with the build's "+N Totems" lines), less one for each other totem-summoning skill in the build
#     (the shared limit; each assumed to keep one totem placed), at least 1;
#   - each totem uses the skill at PoB's one-totem rate, all in reach of the boss (MEDIUM: the player places them at the
#     boss; base_totem_range 60-120 is a placement range, not the skill's reach); placing them (one cast each per 8-12 s
#     duration) isn't taken from anything (the player's own skill time isn't the damage);
#   - counted as more attack and cast speed on the damaging part, as totem_model does: Ignites from several totems don't
#     add up (only the strongest deals damage), Poison stacks and hits do.
# Left to their own models: Spell Totem (totem_model, its charges) and Ancestral Warrior Totem (3 Endurance Charges a
# totem; not modelled here, labelled).
# One-totem supports (AUDIT23 A23-3): Splinter Totem I / II, Greatwood II and Cirel's Cultivation carry
# number_of_totems_allowed_is_1, "Supported Skills can only have one active Totem" (gem_stat_descriptions; game files
# GrantedEffectStatSets; PoB's sup_str.lua lists it and reads it nowhere). The model reads the stat from the group's
# supports (bridge activeSkills supportStats; a flag stat reads 1, CalcTools 183), not the gem names, so a fifth carrier
# counts too: then one totem, no extra speed.
# PoB's own ailment stacks (AUDIT24 A24-3, an OVERCOUNT): CalcOffence 5264-5267 multiplies a totem-flagged skill's
# Ignite / Poison / Bleed stack count by the TotemsSummoned override, else the skill's ActiveTotemLimit, and nothing
# sets either to 1 for these supports. The model adds ActiveTotemLimit BASE (1 - limit) on that skill, source
# "Assumption:OneTotem" (TOTEM_ONE_TAG). The loop: the line lowers the limit the bridge would read next pass; both
# bridge reads (totemLimit, ailmentTotems) leave that source out, so the model reads the same limit every pass and
# writes the same line (a fixed point). A TotemsSummoned override (the Configuration tab) wins in PoB: then no line.
# Splinter Totem I / II (support_splinter_totem_X_times 2): its totem "splinter[s] into smaller versions of itself after
# it uses an Attack or Spell ... up to a maximum", each splinter dealing 20% / 15% less damage per splinter (poe2db
# Splinter_Totem_I, 12 Nov 2026; gem_stat_descriptions "can splinter up to {0} times"; no KeywordPopups entry): the
# splinters are more totems, but how many each splinter makes isn't stated (UNVERIFIED). Counted as one totem, as the
# hits are (an UNDERCOUNT side for Splinter Totem builds, labelled in the note; open for RESEARCH).
TOTEM_ONE_STAT = "number_of_totems_allowed_is_1"
TOTEM_ONE_TAG = "OneTotem"
TOTEM_SPLINTER_STAT = "support_splinter_totem_X_times"
TOTEM_OWN_MODEL = {"Spell Totem", "Ancestral Warrior Totem"}


def totem_count_model(skills: list[Skill], main: Skill | None) -> tuple[list[dict], list[dict]]:
    """More totems than one for the main skill's totem (see above): a speed line on the main skill and its note."""
    if main is None or main.group is None:
        return [], []
    group = [s for s in skills if s.group == main.group]
    if any(number(s.support_stats.get(TOTEM_ONE_STAT)) > 0 for s in group):
        return one_totem_ailments(main, group)  # ("Supported Skills can only have one active Totem": no extra speed)
    if any(s.name in TOTEM_OWN_MODEL for s in group):
        return [], []
    summoner = next((s for s in group if s.totem_limit > 0), None)
    if summoner is None:
        return [], []
    others = sorted({s.name for s in skills if s.totem_limit > 0 and s.group != main.group})
    limit = int(summoner.totem_limit)
    up = max(1, limit - len(others))
    if up <= 1 and not others:
        return [], []
    mods = [line("1% more Attack and Cast Speed", 100.0 * (up - 1), skill=main.name)] if up > 1 else []
    basis = (f"{summoner.name}'s Totem Limit {limit} (PoB's, with the build's +Totem lines)"
             + (f", less {len(others)} for {', '.join(others)} (KeywordPopups Totem: the limit is shared; each assumed to "
                "keep one totem placed)" if others else "")
             + f": {up} totems, each using {main.name} at PoB's one-totem rate, all in reach of the boss (MEDIUM: placed "
               "at it); PoB counts one. Counted as more speed: Ignites from several totems don't add up (only the "
               "strongest deals damage); placing them isn't taken from your time")
    return mods, [{"label": f"Totems up ({main.name})", "value": up, "basis": basis,
                   "play": f"Keep {up} {summoner.name} totems placed at the boss; re-place them as they expire.",
                   "confidence": "MEDIUM"}]


def one_totem_ailments(main: Skill, group: list[Skill]) -> tuple[list[dict], list[dict]]:
    """PoB's ailment stacks for a one-totem support's skill held to one totem (A24-3, see above)."""
    count = main.ailment_totems
    if not count or count.get("override") is not None or number(count.get("limit")) <= 1:
        return [], []
    limit = number(count["limit"])
    splinter = any(number(s.support_stats.get(TOTEM_SPLINTER_STAT)) > 0 for s in group)
    return [raw_mod("ActiveTotemLimit", "BASE", 1 - limit, main.name, tag=TOTEM_ONE_TAG)], [{
        "label": f"{main.name}: one totem's ailments*", "value": 1,
        "basis": f"A support makes it \"only have one active Totem\" (number_of_totems_allowed_is_1); PoB multiplied its "
                 f"Ignite / Poison / Bleed stacks by its Totem Limit {limit:g} (CalcOffence 5264): held to 1"
                 + ("; Splinter Totem's splinters are more totems, how many isn't stated (UNVERIFIED): not counted, an "
                    "undercount side" if splinter else ""),
        "confidence": "HIGH (the support's own text)" if not splinter else "MEDIUM (splinters not counted)"}]


# Minion Pact's damage when no minion is left (AUDIT23 A23-2's other half, MECHB11; an OVERCOUNT): PoB gives the 30% /
# 35% more (support_minion_pact_damage_+%_final) whenever a minion is in your Presence (MinionPresenceCount >= 1,
# PoB's sup_int.lua), and the bridge's Presence count doesn't fall as Minion Pact kills them. The game's text ties the
# damage to the removal: "remove 100% of your maximum Life from a nearby Minion on use to deal 30% more damage"; with no
# minion left to take it from, the use gets none (the text's reading, MEDIUM). With N Reviving minions, revival T and
# attempted kills k per second (minion_deaths' sources), a use finds a minion N / (N + T k) of the time (the share of
# kills the revival lets through); the bonus is held to that share: (1 + p share) / (1 + p) as a Damage MORE. Left as PoB
# has it: no minions (PoB's count is 0, no bonus either) and builds with temporary minions too (they feed it as well;
# their supply isn't modelled, so no cut on a guess).
def minion_pact_model(skills: list[Skill], main: Skill | None, stats: dict) -> tuple[list[dict], list[dict]]:
    if main is None:
        return [], []
    pact = number(main.support_stats.get(MINION_PACT_STAT))
    more = number(main.support_stats.get("support_minion_pact_damage_+%_final"))
    uses = number(stats.get("Speed"))
    if pact <= 0 or more <= 0 or uses <= 0:
        return [], []
    rate, how = minion_deaths(skills, main, stats)
    reviving = [s for s in skills if s.summon and s.summon.get("persistent") and not number(s.summon.get("duration"))]
    count = sum(number(s.summon.get("limit")) for s in reviving)
    if count <= 0 or any(s.summon and s not in reviving for s in skills):
        return [], []  # (none: PoB's Presence count is 0 too; temporary minions also feed it: their supply isn't modelled)
    revive, _ = revive_seconds(reviving)
    kills = count * rate / max(count - revive * rate, 1e-9)  # (minion_deaths' rate solved back for its kills)
    share = count / (count + revive * kills)
    why = (f"{count:g} Reviving minions, each back {revive:.3g} s after the last death, against {kills:.2f} "
           f"attempted kills/s: a minion is there for {share:.0%} of uses")
    if share >= 0.999:
        return [], []
    scale = (1 + more / 100 * share) / (1 + more / 100)
    return [raw_mod("Damage", "MORE", 100 * (scale - 1), main.name)], [{
        "label": f"Minion Pact: a minion to take Life from*", "value": f"{share:.0%} of uses",
        "basis": f"{why}. PoB counts its {more:g}% more on every use while a minion is in your Presence; the gem gives it "
                 f"for removing a minion's Life, so x{scale:.3f}. Without Minion Pact the minions aren't killed "
                 f"this way. ({how})",
        "confidence": "MEDIUM-LOW (the minions' Life read, pact_uses_per_kill; the damage-per-removal reading of the gem's text)"}]


# Poison Spores' Pustule Explosion (MECHB10; COVC12's finding, POE2T1425) ---------------------------------------------------
# The game: "Each enemy can have up to 1 Pustule attached" (statset_0.csd, skill_specific_stat_description_mode 1) and the
# Pustule "Detonates after a delay" (base_skill_detonation_time 15000 ms) (checked: the game files' poison_spore_support
# statset_0 / statset_1 .csd and PoB's sup_dex.lua TriggeredPoisonSporesPustule; poe2db Poison_Spores, 12 Nov 2026: "Each
# enemy can have up to 1 Pustule attached", "Detonation Time is 15 seconds"). PoB scores the Pustule Explosion set as an
# attack used at the weapon's rate (1.2/s on a bow, POE2T1425): against a lone boss at most one explodes per fuse, so its
# damage is held to (1 / fuse) / PoB's rate. The fuse is PoB's DetonationTime (base x the build's Detonation Time lines).
# Not counted (UNDERCOUNT side, labelled): Poison stored on the Pustule makes it expire faster
# (toxic_pustule_duration_expires_1%_faster_per_x_stored_poison_damage_multiplied_by_sixty) and burst harder (up to 300%
# more, toxic_pustule_burst_1%_more_damage_per_x_stored_poison); PoB reads neither (checked: grep of PoB's
# Data/SkillStatMap.lua and Modules/*.lua for "toxic_pustule": none). Whether the build's Poisons on the boss count as
# Poisons "inflicted on" the Pustule is UNVERIFIED, so the speed-up isn't guessed. Pustules on adds that burst next to the
# boss are left out (a lone boss).
# Toxic Domain (AUDIT23 A23-1): the same rule, "Each enemy can have up to 1 Pustule attached" (toxic_domain/statset_0.csd,
# its mode 1) and base_skill_detonation_time 15000 on its Pustule Explosion set (PoB's act_dex.lua ToxicDomainPlayer).
# Each skill is held to its own one Pustule: whether Spores' and Domain's Pustules share the one slot is UNVERIFIED (a
# build with both would count up to two).
# Toxic Growth (MECHB14, an OVERCOUNT): its Pustules lie on the ground under a Limit, not one per enemy. Game files:
# "Fires 5 Pustules" (base_number_of_projectiles 5), "[Limit] 5 pustules" (number_of_poisonbloom_arrow_bloom_allowed 5,
# +0.05 a quality), base_skill_detonation_time 15000 on both sets (toxic_growth statset_0 / statset_1 .csd; PoB's
# act_dex.lua ToxicGrowthPlayer the same); KeywordPopups Limit: "If a new effect is created while at the maximum number
# of effects, the oldest effect will dissapear" (it disappears; the text says nothing of a burst). So at most Limit
# Pustules burst per fuse: Limit / fuse a second (5 / 15 s = 0.33/s), reached only by firing no sooner than the fuse
# allows (one cast fills the Limit; firing again sooner replaces the waiting Pustules, so spamming it bursts none: the
# play row says so). PoB scores the Explosion set at the attack rate, one explosion a cast; it's held to
# min(1, (Limit / fuse) / PoB's rate). The Limit: the set's own stat with quality (calcLib.buildSkillInstanceStats) plus
# "+N to Limit" lines, which PoB maps to AdditionalCooldownUses (Overabundance I / II, the amulet line "Skills have +1 to
# Limit": SkillStatMap base_limit_+, ModCache) (checked: every Stats id holding "limit" with "+" / "additional" looked
# up in Mods, PassiveSkills, GrantedEffectStatSets(PerLevel), GrantedEffectQualityStats: base_limit_+ on
# SupportIncreaseLimitPlayer(Two) and UniqueBaseLimit1; plant_skill_limit_+ (Toxic Growth is a Plant skill) and
# virtual_total_limit_+ on none of them; the elemental / orb / storm / fissure / temporary-minion limits don't match the
# skill's types; second source PoB's ModItem / ModItemExclusive / sup_dex.lua). Each burst is taken as hitting the boss
# (Pustules land in a 4.5 m spread with a 0.8 m burst: how many reach a pinnacle boss is UNVERIFIED, so this is the
# upper bound, labelled). Not counted, as for the Spores: stored Poison's sooner / harder burst (an undercount), and the
# Pustules set's own impacts (5 a cast; PoB counts one: an undercount, not touched here).
PUSTULE_SKILL, PUSTULE_SET, PUSTULE_FUSE = "Poison Spores", "Pustule Explosion", 15.0
PUSTULE_SKILLS = (PUSTULE_SKILL, "Toxic Domain", "Toxic Growth")
PUSTULE_SETS = {"Toxic Growth": "Explosion"}  # (the others: PUSTULE_SET)
PUSTULE_LIMIT = {"Toxic Growth": 5}  # (base, when the bridge gives no pustuleLimit; the others: one per enemy)


def pustule_groups(skills: list[Skill], name: str = PUSTULE_SKILL) -> list[int]:
    """Groups whose `name` (a PUSTULE_SKILLS skill) PoB calculates at its Pustule Explosion set (Full DPS or main)."""
    label = PUSTULE_SETS.get(name, PUSTULE_SET)
    return sorted({s.group for s in skills if s.name == name and s.stat_set == label and s.group is not None})


# Stored Poison (MECHB15; AUDIT25 A25-8, a possibly large UNDERCOUNT): "Pustules store expected damage of [Poison|Poisons]
# inflicted on them" (poison_spore_support/statset_1.csd, toxic_domain/statset_1.csd), "Stored [Poison|Poison] grants up
# to {1}% more explosion Damage" (statset_0.csd: toxic_pustule_max_burst_damage_+%_final_from_stored_poison 300, on
# TriggeredPoisonSporesExplosion, ToxicGrowthPlayer, ToxicDomainPustuleExplosion), 1% more per X stored
# (toxic_pustule_burst_1%_more_damage_per_x_stored_poison: 2 at gem level 1, 969 at 20; GrantedEffectStatSetsPerLevel).
# ActiveSkills: Poison Spores "Attach a Toxic Pustule to the enemy ... They can also be [Poison|Poisoned], causing them
# to Detonate faster and more violently"; Toxic Domain "Pustules will Detonate after a duration or when enough [Poison]
# has been applied". poe2db (Poison_Spores, 12 Nov 2026) quotes the same lines and nothing more.
# Whether the Poisons a build inflicts on the boss are "inflicted on" the Pustule attached to it is UNVERIFIED (checked:
# the three skills' ActiveSkills descriptions, their statset .csd files, skill_stat_descriptions.csd, KeywordPopups (no
# Pustule entry), poe2db's Poison Spores / Toxic Growth / Toxic Domain pages, mobalytics' and poe2wiki's pages; no
# monster table for the Pustule is exported, so whether it is its own hittable object can't be read either). The text
# says "them", the Pustules, and nothing says hits on the carrier reach them. So the bonus isn't scored (accuracy
# first); it's sized as a "with it" row from the build's own Poison: stored over one fuse = PoB's PoisonDPS on the
# boss x the fuse (at steady stacks the expected damage of the Poisons inflicted each second equals the Poison DPS),
# more = min(max, stored / X). The sooner burst ("expires 1% faster per x stored poison damage multiplied by sixty":
# no game text, poe2db shows only the stat id; whether "x sixty" scales the stored damage or X is UNVERIFIED) isn't
# sized, so the row is a floor of the "with it" case on bursts, and an upper bound on what reaches the Pustule.
def pustule_stored(rates: list[dict], poison_dps: float, fuse: float) -> tuple[float, str]:
    """The stored-Poison "more" (percent) a Pustule would burst with if every Poison the build inflicts on the boss
    were stored in it over its fuse, and how (see above); 0 with no Poison or no stat read."""
    pers = [(number(r.get("storedPoisonPerPercent")), number(r.get("storedPoisonMaxMore"))) for r in rates
            if number(r.get("storedPoisonPerPercent")) > 0]
    if not pers or poison_dps <= 0 or fuse <= 0:
        return 0.0, ""
    per, top = min(pers)
    top = top or 300.0
    stored = poison_dps * fuse
    more = min(top, stored / per)
    return more, (f"{poison_dps:,.0f} Poison DPS x {fuse:.1f} s = {stored:,.0f} stored, 1% more per {per:,.0f}: "
                  f"+{more:.0f}% more (max {top:.0f}%)")


def pustule_cap(rates: list[dict], name: str = PUSTULE_SKILL, poison_dps: float = 0.0) -> tuple[list[dict], list[dict]]:
    """The Pustule Explosion held to one a fuse (Poison Spores, Toxic Domain: one per enemy) or to its Limit a fuse
    (Toxic Growth) (see above). `rates`: bridge useRates for each group's `name` skill. `poison_dps`: PoB's PoisonDPS
    on the boss, for the stored-Poison "with it" row (pustule_stored; never scored)."""
    speed = sum(number(r.get("speed")) * (number(r.get("repeats")) or 1.0) for r in rates)
    if speed <= 0:
        return [], []
    fuses = [number(r.get("detonationTime")) for r in rates if number(r.get("detonationTime")) > 0]
    fuse = min(fuses) if fuses else PUSTULE_FUSE
    stored_more, stored_how = pustule_stored(rates, poison_dps, fuse)
    stored = [] if stored_more <= 0 else [{
        "label": f"{name}: stored Poison's harder burst, not counted*", "value": f"x1 (with it: x{1 + stored_more / 100:.2f})",
        "basis": "The game: Pustules \"store expected damage of Poisons inflicted on them\" and burst up to 300% harder. "
                 "UNVERIFIED whether the Poisons you inflict on the boss reach the Pustule attached to it (no game text or "
                 f"second source says so), so it isn't scored. If they all did: {stored_how}. Its sooner burst isn't "
                 "sized (the stat's units are UNVERIFIED), so with it the explosions would come at least that much harder",
        "play": f"Without it: as scored. With it (if your Poisons on the boss feed its Pustule): the explosion x"
                f"{1 + stored_more / 100:.2f}, sooner too",
        "confidence": "LOW (in-game test: the explosion's damage with and without Poisoning the boss)"}]
    ground = name in PUSTULE_LIMIT
    limits = [number(r.get("pustuleLimit")) for r in rates if number(r.get("pustuleLimit")) > 0]
    limit = (max(limits) if limits else float(PUSTULE_LIMIT[name])) if ground else 1.0
    share = min(1.0, limit / fuse / speed)
    if share >= 1.0:
        return [], stored
    fuse_text = f"{fuse:.1f} s fuse{'' if fuses else ' (base; PoB gave no Detonation Time)'}"
    if ground:
        return [raw_mod("DPS", "MORE", 100 * (share - 1), name)], [{
            "label": f"{name}: at most its Pustule Limit bursts a fuse*", "value": f"x{share:.3f}",
            "basis": f"PoB counts {speed:.2f} explosions/s; the game keeps {limit:g} Pustules at a time "
                     f"(Limit{'' if limits else ', base'}: a new one makes the oldest disappear), each bursting after "
                     f"its {fuse_text}: at most {limit / fuse:.3f}/s, each taken as hitting the boss (UNVERIFIED how "
                     "many of a cast's spread land on it: an upper bound). Not counted (undercount side): stored "
                     "Poison making them burst sooner and up to 300% harder, and the Pustules' impacts beyond one",
            "play": f"Fire {name} about once every {fuse:.0f} s and let its Pustules burst: firing again sooner "
                    "replaces the waiting ones (spammed, without the pacing: no bursts at all)",
            "confidence": "LOW-MEDIUM (game files give the Limit, the fuse and what the Limit does; the hits on the "
                          "boss are UNVERIFIED)"}] + stored
    return [raw_mod("DPS", "MORE", 100 * (share - 1), name)], [{
        "label": f"{name}: one Pustule on the boss at a time*", "value": f"x{share:.3f}",
        "basis": f"PoB counts {speed:.2f} Pustule Explosions/s; the game allows one Pustule per enemy, bursting after its "
                 f"{fuse_text}: {1 / fuse:.3f}/s. Not "
                 "counted (undercount side): stored Poison making it burst sooner and up to 300% harder (UNVERIFIED "
                 "whether Poisons on the boss feed it), and Pustules on adds",
        "confidence": "MEDIUM (game files and poe2db agree on one Pustule and the 15 s fuse)"}] + stored


# Runic Ward pays for Ward-cost skills (MECHB13; SEARCH7's Explosive Transmutation finding, an OVERCOUNT) ------------
# KeywordPopups Ward: "Runic Ward constantly regenerates at a default rate of 5% per second" (PoB: the game constant
# BaseWardRegenerationPercentPerMinute 300, as WardRegenPercent; WardRegenRecovery adds the build's regeneration and
# recovery modifiers). PoB charges each use its Runic Ward cost (CalcOffence's costs; WardCost) but checks only that the
# pool covers one use (WardCostWarningList), never how often the build can pay it, so a Ward-cost skill counts at its
# trigger / cast rate however little Ward the build regenerates. The skills with a Ward cost in PoB's data (Data/Skills/
# other.lua, `cost = { Ward = ... }`, 24 skills: the Transmutation Explosion, Frostflame Nova, Skyfall, Grim Pillars,
# Wardbound Minions, Refutation, Voltaic Barrier ...) plus any modifier that adds one; the bridge flags them
# (activeSkills wardCost) and prices one use (useRates wardCost, PoB's WardCost after multipliers).
# The rule (MEDIUM): over a long boss fight the sustained uses are what the regeneration pays for: every Ward-cost skill
# the build uses shares it, each held to its planned rate x min(1, regeneration / the Ward all of them would spend).
# Left out: the full pool at the start of the fight (pool / cost more uses once: an undercount, small over a long
# fight); Wardbound Minions' and Refutation's "spend all your Runic Ward" (their own models) beyond their listed cost.


@dataclass
class WardUse:
    """A Ward-cost skill's planned use: `kind` "triggered" (a model's triggerRate), "main" (the main skill at its PoB
    rate after the press hook), "pressed" (a pressed Full DPS group at its press rate) or "full" (another Full DPS group,
    at PoB's rate); `rate` uses a second; `cost` Runic Ward a use."""
    name: str
    kind: str
    rate: float
    cost: float
    group: int | None = None


WARD_ON_KILL = re.compile(r"recover (\d+(?:\.\d+)?)% of maximum runic ward on kill", re.I)
WARD_ON_MINION_KILLED = re.compile(r"recover (\d+(?:\.\d+)?)% of maximum runic ward when one of your reviving minions "
                                   r"is killed", re.I)
# Runic Ward recovery lines no rate reads yet (checked: every Stats.json id holding "ward" with recover / gain / regen /
# leech / restore / remnant / on_kill / when, each looked up in Mods, PassiveSkills, GrantedEffectStatSets(PerLevel) and
# GrantedEffectQualityStats; PoB's unique and mod text holding "Runic Ward"; second source PoB's ModParser): named in
# the note, never silently dropped (the user, 12 Nov: "regen is not the only source of recovery, not by a longshot")
# (MECHB14) Three of them now have a rate: Ward when a Charm is used (the charms' uses a second, flasks.uses_per_second),
# Life Recovery from Flasks applied to Ward (the Life flask's average recovery, flasks.recovery_per_second, in place of
# PoB's, which counts the flask as always running: CalcPerform 1642 adds flaskTotal / duration x the % as WardRecovery
# while its slot is active), and the Life sacrificed on Attack (half of it, at the build's attacks a second, no more than
# its Life recovery pays back). Still unread: "on reaching Maximum Rage" (how often the build refills its Rage is MECH-C's
# Rage model, conditions.rage_held, which gives no such rate yet: named, routed).
WARD_UNREAD = {
    "Runic Ward on reaching Maximum Rage": re.compile(r"runic ward on reaching maximum rage", re.I),
}
WARD_ON_CHARM = re.compile(r"recover (\d+(?:\.\d+)?) runic ward when a charm is used", re.I)
WARD_FROM_FLASK = re.compile(r"(\d+(?:\.\d+)?)% life recovery from flasks also applies to runic ward", re.I)
WARD_SACRIFICE = re.compile(r"sacrifice (\d+(?:\.\d+)?)% of maximum life to gain half that much runic ward when you "
                            r"attack", re.I)


def ward_flask_percent(texts: list[str]) -> float:
    """The build's "N% Life Recovery from Flasks also applies to Runic Ward", summed (PoB's LifeFlaskRecoveryAppliesToWard)."""
    return sum(float(m.group(1)) for t in texts if (m := WARD_FROM_FLASK.search(t)))


def ward_income(regen: float, pool: float, texts: list[str], *, kalguur: dict | None = None, stuns: float = 0.0,
                stuns_how: str = "", kills: tuple[float, float] = (0.0, 0.0), on_block: float = 0.0, block: float = 0.0,
                every: float = 0.0, charm_uses: float = 0.0, charm_how: str = "", flask_life: float = 0.0,
                pob_flask_ward: float = 0.0, attacks: float = 0.0, life: float = 0.0,
                life_recovery: float = 0.0) -> tuple[float, list[str], list[str]]:
    """Runic Ward a second from every source the build has (the user's rule, 12 Nov: not regeneration alone): PoB's
    WardRegenRecovery (regeneration, its recovery lines, Mana regeneration applied to Ward); Remnants of Kalguur
    (`kalguur`: its level stats, plus "multiplier", the build's extra Remnants / pickups) from `stuns` Stuns a second
    (chance each, at most one per its cooldown) and kills; Ward on Block (`on_block` x `block`% of the boss's hits, one
    every `every` s); "% of maximum Runic Ward on Kill" (`kills`: enemies killed / all kills a second, models.kill_rate)
    and on a Reviving Minion Killed (all kills less enemies); (MECHB14) Ward when a Charm is used (`charm_uses` a second,
    `charm_how`), Life flask recovery applied to Ward (`flask_life`, the Life flasks' average recovery a second, in place
    of PoB's always-running part `pob_flask_ward` inside `regen`), and Life sacrificed on Attack (`attacks` a second,
    of `life` maximum Life, no more than `life_recovery` a second pays back). (income, the parts, the lines no rate reads)."""
    flask_pct = ward_flask_percent(texts)
    regen = regen - (pob_flask_ward if flask_pct > 0 else 0.0)
    income, parts = max(regen, 0.0), [f"regeneration {max(regen, 0.0):.1f}/s (PoB's WardRegenRecovery"
                                      + (f", less its always-running flask part {pob_flask_ward:.1f}/s)"
                                         if flask_pct > 0 and pob_flask_ward > 0 else ")")]
    if flask_pct > 0 and flask_life > 0:
        income += flask_pct / 100 * flask_life
        parts.append(f"{flask_pct:g}% of Life flask recovery {flask_life:.1f}/s (as often as its charges allow)")
    charm = sum(float(m.group(1)) for t in texts if (m := WARD_ON_CHARM.search(t)))
    if charm > 0 and charm_uses > 0:
        income += charm * charm_uses
        parts.append(f"{charm:g} Ward a Charm use at {charm_uses:.3f} uses/s" + (f" ({charm_how})" if charm_how else ""))
    sacrifice = sum(float(m.group(1)) for t in texts if (m := WARD_SACRIFICE.search(t)))
    if sacrifice > 0 and attacks > 0 and life > 0:
        spent = sacrifice / 100 * life * attacks
        paid = min(spent, max(life_recovery, 0.0))
        income += paid / 2
        parts.append(f"half the Life sacrificed on Attack ({sacrifice:g}% of {life:,.0f} Life x {attacks:.2f} attacks/s = "
                     f"{spent:,.0f}/s" + (f", held to the {life_recovery:,.0f} Life/s the build recovers" if paid < spent
                                          else "") + f": {paid / 2:,.0f} Ward/s; MEDIUM-LOW)")
    if kalguur:
        gain = number(kalguur.get("ward_remnants_gain_per_globe"))
        chance = number(kalguur.get("ward_remnants_chance_to_spawn_remnant_on_stun_%")) / 100
        cooldown = number(kalguur.get("ward_remnants_spawn_remnant_on_stun_cooldown_ms")) / 1000
        on_kill = number(kalguur.get("ward_remnants_chance_to_spawn_orb_on_killing_enemy_%")) / 100
        more = number(kalguur.get("multiplier")) or 1.0
        from_stuns = 1.0 / (cooldown + 1.0 / (chance * stuns)) if stuns > 0 and chance > 0 else 0.0
        remnants = (from_stuns + on_kill * kills[0]) * more
        income += remnants * gain
        parts.append(f"Remnants of Kalguur {remnants:.3f}/s x {gain:g} Ward ({chance:.0%} a Stun at {stuns:.2f} Stuns/s"
                     + (f" [{stuns_how}]" if stuns_how else "") + ", "
                     f"at most one per {cooldown:g} s; {on_kill:.0%} a kill at {kills[0]:.3f} kills/s"
                     + (f"; x{more:.2f} the build's extra Remnants and pickups" if more != 1.0 else "") + ")")
    if on_block > 0 and block > 0 and every > 0:
        income += on_block * block / 100 / every
        parts.append(f"Ward on Block {on_block:g} x {block:.0f}% of the boss's hits (one every {every:.2f} s)")
    pct_kill = sum(float(m.group(1)) for t in texts if (m := WARD_ON_KILL.search(t)))
    if pct_kill and kills[0] > 0:
        income += pct_kill / 100 * pool * kills[0]
        parts.append(f"{pct_kill:g}% of maximum Ward on Kill at {kills[0]:.3f} kills/s")
    pct_minion = sum(float(m.group(1)) for t in texts if (m := WARD_ON_MINION_KILLED.search(t)))
    killed = max(kills[1] - kills[0], 0.0)
    if pct_minion and killed > 0:
        income += pct_minion / 100 * pool * killed
        parts.append(f"{pct_minion:g}% of maximum Ward per Reviving Minion Killed at {killed:.3f}/s (LOW: every Killed "
                     "minion taken as Reviving)")
    unread = [name for name, pattern in WARD_UNREAD.items() if any(pattern.search(t) for t in texts)]
    return income, parts, unread


def ward_plan(uses: list[WardUse], regen: float, pool: float, parts: list[str] | None = None,
              unread: list[str] | None = None) -> tuple[float, list[dict]]:
    """The share of their planned rates the build's Runic Ward recovery (`regen` per second, every source: ward_income,
    whose `parts` and `unread` lines the note names) pays for the Ward-cost skills `uses` (one share for all: they spend
    the same Runic Ward), and the notes. 1.0 when it pays for all, or when nothing is spent."""
    spend = sum(u.rate * u.cost for u in uses if u.rate > 0 and u.cost > 0)
    if spend <= 0:
        return 1.0, []
    share = min(1.0, max(regen, 0.0) / spend)
    names = ", ".join(f"{u.name} {u.rate:.2f}/s x {u.cost:g} Ward" for u in uses if u.rate > 0 and u.cost > 0)
    sources = "; ".join(parts or [f"regeneration {regen:.1f}/s (PoB's WardRegenRecovery)"])
    missing = (f". Not counted (no rate reads them yet, so this may be an undercount): {', '.join(unread)}"
               if unread else "")
    if share >= 1.0:
        return 1.0, [{
            "label": "Runic Ward pays for the Ward-cost skills", "value": f"{spend:.1f} of {regen:.1f} Ward/s",
            "basis": f"{names}: the build's Runic Ward recovery pays for every use ({sources}){missing}",
            "play": f"The build feeds its Runic Ward from: {sources}.",
            "confidence": "MEDIUM"}]
    return share, [{
        "label": "Ward-cost skills held to what Runic Ward recovery pays for*", "value": f"x{share:.3f}",
        "basis": f"{names} would spend {spend:.1f} Runic Ward a second; the build recovers {regen:.1f}/s ({sources}; base "
                 f"regeneration 5% of its {pool:.0f} maximum a second, KeywordPopups Ward), so each is used "
                 f"{share:.1%} as often (without this cap: the rates above"
                 + ("; with no Runic Ward recovery none of them is used" if regen <= 0 else "")
                 + f"). The full pool at the start of the fight isn't counted (a few more uses once){missing}",
        "play": f"The build feeds its Runic Ward from: {sources}. Every Ward-cost use spends it, so they're used only as "
                f"often as that pays for ({share:.0%} of the rates above); more Ward recovery (Remnants of Kalguur, Ward on "
                "Block, regeneration) raises it.",
        "confidence": "MEDIUM"}]


# Frostflame Nova's Volatile Ice (MECHB13; MECHB11's notes, AUDIT23 A23-1, a likely OVERCOUNT) ------------------------
# Game files: the gem's description "the ice latches onto Ignited enemies, refreshing the duration of their active Ignite
# and rapidly Freezing them but preventing them from being Ignited further until the Ignite expires"; its Volatile Ice set
# (skill_specific_stat_description_mode 1, frostflame_nova.csd): "Hits enemies every 0.5 seconds until affected Ignite
# expires". PoB scores the set as one hit per cast (ShowAverage, at the cast rate) whether or not the boss is Ignited. The
# model (MEDIUM-LOW): 2 hits a second while the boss carries an Ignite, at the share of the fight it does from every Full
# DPS group's Ignites (each group's PoB Ignite stack potential at its use rate, 1 - e^-potential, combined as
# independent); 0 with no Ignite source. One ice at a time (whether a second cast's ice latches too: UNVERIFIED). Left
# out: the refreshed Ignite's longer uptime and the Ignites it blocks (MECH-A's Ignite model), its Freeze buildup bonus.
FROSTFLAME = "Frostflame Nova"
FROSTFLAME_HITS = 2.0  # "every 0.5 seconds"


def ignite_share(rates: list[dict], scales: dict[str, float] | None = None) -> tuple[float, str]:
    """The share of the fight the boss carries an Ignite from the groups `rates` (bridge useRates rows), each at PoB's
    use rate x its press scale (`scales`, objective.press_scales) when it's pressed beside the main skill."""
    free, parts = 1.0, []
    for rate in rates:
        potential = number((rate.get("stackPotential") or {}).get("Ignite"))
        potential *= (scales or {}).get(rate.get("name"), 1.0)
        if potential <= 0:
            continue
        up = 1.0 - math.exp(-potential)
        free *= 1.0 - up
        parts.append(f"{rate.get('name')} {up:.0%}")
    return 1.0 - free, ("; ".join(parts) or "no skill of the build Ignites the boss")


def frostflame_model(speed: float, share: float, how: str) -> tuple[list[dict], list[dict]]:
    """Frostflame Nova as the main skill at FROSTFLAME_HITS hits a second while the boss is Ignited (`share`, `how`:
    ignite_share), in place of PoB's one hit per cast at `speed` casts a second: a DPS "more" on the skill."""
    if speed <= 0:
        return [], []
    hits = FROSTFLAME_HITS * share
    more = hits / speed
    return [raw_mod("DPS", "MORE", 100 * (more - 1), FROSTFLAME)], [{
        "label": "Frostflame Nova: Volatile Ice hits while the boss is Ignited*", "value": f"{hits:.2f} hits/s",
        "basis": f"the ice 'Hits enemies every 0.5 seconds until affected Ignite expires' (game files) and latches only "
                 f"onto Ignited enemies: 2 hits/s x the boss Ignited {share:.0%} of the fight ({how}); PoB counts one "
                 f"hit per cast ({speed:.2f}/s), so x{more:.3f}. One ice at a time (UNVERIFIED); the refreshed Ignite's "
                 "longer uptime and the Ignites it blocks aren't counted",
        "play": "Keep the boss Ignited with another skill; the ice hits only while that Ignite lasts (without an Ignite "
                "source it deals nothing).",
        "confidence": "MEDIUM-LOW"}]



# Frostflame Nova beside the main skill (MECHB14): the same ice, for every group that uses it. PoB adds a Full DPS
# Frostflame group's Volatile Ice at one hit per cast too (its Wave set deals no damage: base_deal_no_damage), so a
# pressed or Full DPS Frostflame was counted whether or not the boss was Ignited (an OVERCOUNT). One ice at a time over
# all of them (UNVERIFIED, as above). A cast's ice lasts until the Ignite it refreshed expires, so each cast gives at
# most 2 hits/s x the Ignite's duration (the longest of the igniting groups' PoB IgniteDuration, refreshed in full: an
# upper bound): a rarely pressed Frostflame is held by its casts as well as by the Ignite share. The skill-tagged "more"
# reaches every Frostflame group, so it is the total over what PoB counts for all of them (each group's rate x its press
# scale; the main skill's at its own rate).


def frostflame_plan(uses: list[tuple[float, float]], share: float, how: str,
                    ignite_duration: float = 0.0) -> tuple[list[dict], list[dict]]:
    """Frostflame Nova's ice over all its groups: `uses` (PoB's casts a second, the press scale) for each group,
    `share` / `how` from ignite_share, `ignite_duration` the longest Ignite (s; 0: not known, no per-cast cap)."""
    counted = sum(speed * scale for speed, scale in uses)
    if counted <= 0:
        return [], []
    hits = FROSTFLAME_HITS * share
    cap = ""
    if ignite_duration > 0 and counted * FROSTFLAME_HITS * ignite_duration < hits:
        hits = counted * FROSTFLAME_HITS * ignite_duration
        cap = (f"; held by its casts ({counted:.2f}/s, each ice lasting at most the {ignite_duration:.1f} s Ignite it "
               "refreshes)")
    more = hits / counted
    return [raw_mod("DPS", "MORE", 100 * (more - 1), FROSTFLAME)], [{
        "label": "Frostflame Nova: Volatile Ice hits while the boss is Ignited*", "value": f"{hits:.2f} hits/s",
        "basis": f"the ice 'Hits enemies every 0.5 seconds until affected Ignite expires' (game files) and latches only "
                 f"onto Ignited enemies: 2 hits/s x the boss Ignited {share:.0%} of the fight ({how}){cap}; PoB counts "
                 f"one hit per cast ({counted:.2f}/s over {len(uses)} group{'s' if len(uses) > 1 else ''}), so "
                 f"x{more:.3f}. One ice at a time (UNVERIFIED); the refreshed Ignite's longer uptime and the Ignites it "
                 "blocks aren't counted",
        "play": "Keep the boss Ignited with another skill and cast Frostflame Nova while it burns; the ice hits only "
                "while that Ignite lasts (without an Ignite source it deals nothing).",
        "confidence": "MEDIUM-LOW"}]

# Light Stuns of the boss (MECHB13; the manager's 18:20 relay of the user's Kalguuran Remnants note) -------------------
# KeywordPopups LightStun: "Any Hit has a chance to Light Stun the target ... The chance is based on the damage dealt, up
# to 100% base chance for Hits that deal 100% of the target's maximum Life. Chances lower than 15% are treated as 0%.
# Physical damage, and player (but not monster) Melee Damage, each have 50% more Light Stun chance." Bosses take them
# (checked: research/boss-poise-stun-threshold.md item 4: no stun-immunity mod on any of the 11 pinnacle varieties' mod
# lists; Monster.ot gives every monster a 0.5 s Light Stun, research/boss-heavy-stun-duration.md; second source poe2db
# The_Arbiter_of_Ash's mod list). The chance is linear in the hit (LightStunRatioScale 58's role UNVERIFIED, RESEARCH20
# #116; LOW). Not read: light stun chance / threshold modifiers, the boss's Energy Shield in front of its Life.
LIGHT_STUN_FLOOR = 0.15


def light_stun_rate(stats: dict, melee: bool = False) -> tuple[float, str]:
    """Light Stuns of the boss per second from the main skill's hits: each hit's chance is its damage (Physical x1.5, all
    x1.5 for a player Melee skill) over the boss's maximum Life (bosses.of(stats).life), 0 under 15%."""
    life = bosses.of(stats).life
    hits = hits_per_second(stats)
    if life <= 0 or hits <= 0:
        return 0.0, "no hits"
    crit = min(number(stats.get("CritChance")), 100) / 100
    hit = sum((number(stats.get(f"{t}HitAverage")) * (1 - crit) + number(stats.get(f"{t}CritAverage")) * crit)
              * (1.5 if t == "Physical" else 1.0) for t in DAMAGE_TYPES) * (1.5 if melee else 1.0)
    chance = min(hit / life, 1.0)
    if chance < LIGHT_STUN_FLOOR:
        return 0.0, (f"a hit of {hit:,.0f} is {chance:.1%} of the boss's {life:,} Life, under the 15% floor: no Light "
                     "Stuns (KeywordPopups LightStun)")
    return hits * chance, (f"{hits:.2f} hits/s x {chance:.0%} Light Stun chance (a {hit:,.0f} hit of the boss's {life:,} "
                           "Life; KeywordPopups LightStun, linear: LOW)")


def stun_rate(stats: dict, melee: bool = False) -> tuple[float, str]:
    """Stuns of the boss per second, Heavy (heavy_stun_rate) and Light (light_stun_rate), for "when you Stun" sources."""
    heavy, heavy_how = heavy_stun_rate(stats)
    light, light_how = light_stun_rate(stats, melee)
    return heavy + light, f"Heavy {heavy:.3f}/s ({heavy_how}); Light {light:.3f}/s ({light_how})"


# One use, two damaging stat sets (MECHB15; FIX_QUEUE's undercount list, MECHB14's finding) --------------------------------
# Rolling Slam: "[Slam|Slam] the ground to [Stun|Stun] enemies and [Knockback|knock them away], then continue forward and
# perform a devastating second [Slam|Slam]" (ActiveSkills rolling_slam). PoB has three sets: "Rolling Slam"
# (base_deal_no_damage), "First Slam" (272% at level 20) and "Second Slam" (543%), all at total_attack_time 1000 ms,
# and scores one set per group; the search keeps the best (gems.py), the Second Slam. One use does both, so the use's
# damage is the two sets' sum: a DPS "more" of (First + Second) / scored, from the bridge's setRates (each set in its
# own calculation, PoB's TotalDPS). Both slams are taken as hitting the boss (the first's Knockback moves no pinnacle
# boss that's immune to it, and the second lands 1.5 m further on (melee_conditional_step_distance 15): a large boss is
# still under it; LOW-MEDIUM, labelled). Other skills whose sets are labelled First / Second / Third / Final in PoB's
# skill files are Combo or sequence strikes, each its own use (a different model, not this one) (checked: grep of
# label = "First|Second|Third|Final" over PoB's Data/Skills: Rolling Slam's two, Second Wind I-III (supports), Final
# Strike / Burst / Pulse, Third Strike).
MULTI_SET_SKILLS = {"Rolling Slam": ("First Slam", "Second Slam")}


def multi_set_model(main: str | None, scored: str | None, sets: list[dict]) -> tuple[list[dict], list[dict]]:
    """A main skill whose one use performs several damaging stat sets (MULTI_SET_SKILLS), PoB scoring one: a DPS "more"
    of their summed TotalDPS over the scored set's. `sets`: the bridge's setRates for the main group's skill."""
    want = MULTI_SET_SKILLS.get(main or "")
    if not want or scored not in want:
        return [], []
    dps = {s.get("label"): number(s.get("TotalDPS")) for s in sets}
    if dps.get(scored, 0) <= 0 or any(dps.get(label, 0) <= 0 for label in want):
        return [], []
    more = sum(dps[label] for label in want) / dps[scored]
    parts = " + ".join(f"{label} {dps[label]:,.0f}" for label in want)
    return [raw_mod("DPS", "MORE", 100 * (more - 1), main)], [{
        "label": f"{main}: one use performs {' and '.join(want)}*", "value": f"x{more:.3f}",
        "basis": f"PoB scores the {scored} alone ({dps[scored]:,.0f} DPS); each use does both ({parts} DPS, each set "
                 "calculated by PoB). Both taken as hitting the boss (the second lands 1.5 m on)",
        "confidence": "MEDIUM (game text: a slam, then a second slam; PoB's own per-set numbers; both on the boss LOW-MEDIUM)"}]


# Tornado Shot's copied projectiles (MECHB17; FIX_QUEUE "Tornado Shot's copies", plans/term-projectile-range.md item 2;
# PoB counts none: its only Tornado Shot config, tornadoShotSecondaryHitChance, is read nowhere in CalcOffence). Game
# files (build 26476662): ActiveSkills tornado_shot, "Arrows, thrown [Spear|Spears] and [Crossbow] bolts fired at the
# Tornado are sucked into it, causing the Tornado to spit out a ring of copied [Projectile|Projectiles]. Copied
# [Projectile|Projectiles] cannot be copied again"; tornado_shot/statset_1.csd: "Tornado spits out {0} copies of
# [Projectile|Projectiles] fired into it" (base_number_of_projectiles 3), "Copied [Projectile|Projectiles] deal {0}% more
# damage" (tornado_shot_projectile_damage_+%_final: -60 at gem level 1, -41 at 20, +0.75 a quality), "Tornado disappears
# after being [Hit] {0} times" (tornado_shot_number_of_hits_allowed: 5 at 1, 8 at 20), "[Limit] {0} Tornado" (1), a
# 15 s base_skill_effect_duration. KeywordPopups Projectile: "When a group of multiple Projectiles is fired from the same
# source at the same time, only one Projectile in the group can hit each target unless otherwise specified".
# So on one boss each arrow sucked in gives ONE copied hit at the penalty (its ring is a group), and the arrow itself is
# gone ("sucked into it"; whether it also hits the boss: UNVERIFIED, taken as not, plans/term-projectile-range.md):
# against a skill whose one arrow a use hits the boss, a use through the Tornado gives N x (1 - penalty) hits for 1, N
# the arrows a use fires (PoB's ProjectileCount). A skill whose arrows all hit anyway (SHOTGUN_SKILLS, SEQUENCE_SKILLS,
# PoB counts them) gives N x (1 - penalty) for N: never a gain. The rate comes from the build (CLAUDE.md rule 2): each
# Tornado takes H arrows or lasts its duration, whichever ends first, so U = min(H / N, duration x uses/s) main uses
# go through one Tornado, and each Tornado costs one Tornado Shot use (1 / its own PoB use rate): the main skill's
# damage is x (N x (1 - penalty) / direct) x (U / r) / (U / r + Tornado Shot's use time). Below x1 the player shoots the
# boss directly, which PoB already scores: nothing changes. LOW: (a) every arrow of a volley reaches the Tornado (it is
# placed on the boss and shot at close range); (b) each sucked-in arrow's ring is its own group (the Tornado counts each
# arrow as its own Hit toward H). The lower reading of (b), one ring group per volley, would make it never worth it.
# Not counted: Tornado Shot's own damage over time (PoB's, when its group is in Full DPS); duration modifiers (the base
# 15 s is used, which can only cut U); the copies' 6 m reach (the boss stands in the Tornado).
TORNADO_SHOT = "Tornado Shot"
TORNADO_HITS_STAT = "tornado_shot_number_of_hits_allowed"
TORNADO_DAMAGE_STAT = "tornado_shot_projectile_damage_+%_final"
TORNADO_DURATION_STAT = "base_skill_effect_duration"


def tornado_copy_model(main: Skill | None, main_types: set[str], stats: dict, tornado: Skill | None,
                       tornado_stats: dict, tornado_speed: float) -> tuple[list[dict], list[dict]]:
    """The main projectile attack fired through Tornado Shot's Tornado, when that out-damages shooting the boss (above)."""
    if main is None or tornado is None or main.name == TORNADO_SHOT:
        return [], []
    label = f"{TORNADO_SHOT}: {main.name}'s arrows fired through the Tornado*"
    if not main.attack or "Projectile" not in main_types or main.triggered:
        return [], [{"label": label, "value": 0, "confidence": "theory",
                     "basis": f"{main.name} isn't a projectile attack the player fires (arrows, thrown spears, bolts), so "
                              "the Tornado copies nothing of it"}]
    count = max(number(stats.get("ProjectileCount")), 1.0)
    speed = number(stats.get("Speed"))
    hits = number(tornado_stats.get(TORNADO_HITS_STAT))
    penalty = number(tornado_stats.get(TORNADO_DAMAGE_STAT))
    duration = number(tornado_stats.get(TORNADO_DURATION_STAT)) / 1000
    if speed <= 0 or tornado_speed <= 0 or hits <= 0 or duration <= 0:
        return [], [{"label": label, "value": 0, "confidence": "theory",
                     "basis": "no use rate or Tornado stats read from PoB: not counted"}]
    direct = count if main.name in SHOTGUN_SKILLS | SEQUENCE_SKILLS else 1.0
    per_use = count * (1 + penalty / 100) / direct
    uses = min(hits / count, duration * speed)
    firing = uses / speed
    factor = per_use * firing / (firing + 1 / tornado_speed)
    how = (f"{count:g} arrow(s) a use, each sucked in and copied into a ring of which one copy hits the boss at "
           f"{penalty:+g}% more (one projectile of a group a target, KeywordPopups Projectile), against {direct:g} direct "
           f"hit(s): x{per_use:.3f} a use; a Tornado takes {hits:g} arrows or lasts {duration:g} s, so {uses:.2f} uses "
           f"({firing:.2f} s) a Tornado, and each Tornado costs a {TORNADO_SHOT} use ({1 / tornado_speed:.2f} s)")
    if factor <= 1:
        return [], [{"label": label, "value": "x1 (shoot the boss directly)", "confidence": "LOW",
                     "basis": f"{how}: x{factor:.3f}, below shooting the boss, so not counted. Without the Tornado: x1; "
                              f"through it: x{factor:.3f}"}]
    return [line("1% more Damage", 100 * (factor - 1), skill=main.name)], [{
        "label": label, "value": f"x{factor:.3f}", "confidence": "LOW",
        "basis": f"{how}: x{factor:.3f}. Play: put the Tornado on the boss, fire {main.name} into it, recast when it's "
                 f"gone. Without the Tornado: x1; through it: x{factor:.3f}. LOW: every arrow assumed to reach the "
                 "Tornado, and each arrow's ring its own group (if a volley's rings are one group, x"
                 f"{(1 + penalty / 100) / direct * firing / (firing + 1 / tornado_speed):.3f}, so not worth it); "
                 "the sucked-in arrow taken as not hitting (UNVERIFIED)"}]
