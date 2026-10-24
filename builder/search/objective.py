"""The score a search maximizes: the log of the request's goal minus weighted feasibility deficits.

- The goal (request `objective`): boss DPS, a clear-speed estimate (mapping), or by default their
  geometric mean (balanced), so neither can be traded down to nothing (PLAN.md §5.5).
- Screening uses main-skill DPS (CombinedDPS, or the minion's for minion skills); finalists use
  Full DPS, which costs far more per what-if on real builds (PLAN.md Phase 0 findings).
- Request floors (life, resistances, max hit, ...) are layer 2 of the constraint compiler:
  normalized deficits that pull the search toward feasible builds without hiding them.
- Limits the game enforces (attribute requirements, Spirit) weigh more than request floors.
- Defence floors use pools and maximum hit taken, never TotalEHP (a known snapshot bug inflates
  it, PLAN.md "Engine accuracy"). Maximum hit taken includes Darkness (models.with_darkness),
  which PoB pools but doesn't spend.
- Skills the main skill triggers besides itself (spells in a meta gem, Elemental Expression) only
  count through Full DPS, so while a build has them (`extra`) the search scores Full DPS too.
"""

from __future__ import annotations

import math
import re

from builder.request.schema import DefenceStyle, Objective as Goal
from builder.search.context import DEFENCE_LAYERS, Context
from builder.search.flasks import RECOVERY_FIELDS
from builder.search.models import DAMAGE_TYPES, with_darkness

FLOOR_WEIGHT = 6.0
HARD_WEIGHT = 15.0
# Fixed costs of being short at all, so no amount of damage buys an infeasible build (e^3 is 20x
# DPS, e^8 about 3000x); the weights above still steer toward the smallest shortfall
FLOOR_OFFSET = 3.0
HARD_OFFSET = 8.0
# When a search settles with a requirement still short, that requirement's weight is multiplied
# by ESCALATION (up to MAX_MULTIPLIER) and the search goes on: a big gap (2,000 Mana) can need
# several moves that each lose DPS before the floor is met, which a fixed weight never pays for.
ESCALATION = 4.0
MAX_MULTIPLIER = 64.0
MAIN_DPS_FIELDS = ["CombinedDPS", "Minion.CombinedDPS"]
BASE_FIELDS = [
    "FullDPS", *MAIN_DPS_FIELDS, "Life", "EnergyShield", "Mana", "Armour", "Evasion", "Spirit",
    "SpiritUnreserved", "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
]
# What Darkness needs to join the maximum-hit pools
DARKNESS_FIELDS = ["Darkness", *(f"{t}TotalHitPool" for t in DAMAGE_TYPES), *(f"{t}MaximumHitTaken" for t in DAMAGE_TYPES)]
# PoB's lists of skills whose cost a pool can't pay (reported as counts by the bridge): a skill
# you can't pay for can't be used, so each is a hard limit like attribute requirements
COST_WARNINGS = {
    "LifeCostWarningList": "Life", "ManaCostWarningList": "Mana", "ESCostWarningList": "Energy Shield",
    "WardCostWarningList": "Runic Ward", "RageCostWarningList": "Rage",
    "LifePercentCostPercentCostWarningList": "unreserved Life", "ManaPercentCostPercentCostWarningList": "unreserved Mana",
}
# Damage that only happens when enemies die (Fiery Death's explosions, Plague Burst, ...): PoB's
# Full DPS counts it as if it were sustained, but a single boss never triggers it
# (ledger/proposed/pob-full-dps-on-death-damage.yaml). Matched on gem descriptions.
ON_DEATH = re.compile(r"\b(?:explode|combust|burst)\w*\b[^.]*\bon death\b|\bwhen they die\b", re.I)
# Sustain: PoB's DPS assumes you can keep paying a skill's cost. Regeneration, leech, on-hit
# recovery and flasks (at the rate generated charges pay for in a boss fight, builder/search/
# flasks.py) must cover at least this share of the per-second cost; below it the build is short of
# a requirement-style floor. The bar sits under the user's own endgame builds (19%, 47% and 71% of
# their costs, without flasks) and above degenerate ones (0-4%).
SUSTAIN_FLOOR = 0.10
SUSTAIN_POOLS = {
    "Mana": ("ManaPerSecondCost", ("ManaRegenRecovery", "ManaLeechRate", "ManaOnHitRate", RECOVERY_FIELDS["Mana"])),
    "Life": ("LifePerSecondCost", ("LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate", RECOVERY_FIELDS["Life"])),
}
# What PoB is asked for (the flask fields are the search's own, added by Build.extra_stats)
SUSTAIN_FIELDS = sorted({f for cost, recovery in SUSTAIN_POOLS.values() for f in (cost, *recovery)}
                        - set(RECOVERY_FIELDS.values()))
# Clear speed (PLAN.md §5.5). PoB doesn't model it, so this is an estimate, labelled approximate in
# the result: the monster life a build clears per second in a typical map pack. One use reaches
# part of the pack (area, projectiles, chain, pierce, fork); killing what it reaches takes the hits
# its damage needs plus one use's own time; then the build walks to the next pack. While damage is
# the limit this is hit DPS x targets; once each use kills what it reaches, use speed and movement
# speed set the pace. The constants are guesses (confidence: low): they mostly decide where more
# damage stops paying off for clearing.
PACK_SIZE = 8  # monsters in a pack
PACK_RADIUS = 40.0  # PoB units (10 per metre): the circle a pack stands in
# An average pack member: level 80 base life is 31,065 (PoB's monster life table), x1.75 for a
# pack of five normal, two magic (x2) and one rare (x5) monster, and +30% from map modifiers
MONSTER_LIFE = 70_000.0
TRAVEL_SECONDS = 2.0  # walking to the next pack at base movement speed
PIERCE_VALUE = 0.5  # a pierce only finds the enemies standing behind the first
FORK_VALUE = 1.0  # a fork's two projectiles leave at an angle; count one new target
CLEAR_PARTS = ["AreaOfEffectRadius", "ProjectileCount", "ChainMax", "PierceCount", "ForkCountMax", "Speed"]
CLEAR_FIELDS = [*CLEAR_PARTS, *(f"Minion.{f}" for f in CLEAR_PARTS), "EffectiveMovementSpeedMod"]


def sustain(stats: dict) -> tuple[float, str | None]:
    """The lowest share of a pool's per-second skill cost that recovery covers (1.0 = all), and
    the pool."""
    worst, pool_name = 1.0, None
    for pool, (cost_field, recovery_fields) in SUSTAIN_POOLS.items():
        cost = number(stats.get(cost_field))
        if cost > 0:
            share = sum(number(stats.get(f)) for f in recovery_fields) / cost
            if share < worst:
                worst, pool_name = share, pool
    return worst, pool_name


# Fields only an EHP pass fills in; gem what-ifs skip that pass and keep the build's values
EHP_FIELDS_PREFIXES = ("PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
                       "LightningMaximumHitTaken", "ChaosMaximumHitTaken")


def number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value in ("inf", "-inf"):
        return float(value)
    return 0.0


def main_dps(stats: dict) -> float:
    return max(number(stats.get(field)) for field in MAIN_DPS_FIELDS)


def clear_estimate(stats: dict, damage: float) -> dict:
    """Clear speed from a build's damage per target and the main skill's reach (module constants):
    the score (monster life cleared per second) and the parts behind it."""
    # A minion skill's reach and speed are the minion's; movement is always the player's
    prefix = "Minion." if number(stats.get("Minion.CombinedDPS")) > number(stats.get("CombinedDPS")) else ""

    def part(field: str) -> float:
        return max(number(stats.get(prefix + field)), 0.0)

    per_projectile = (1.0 + part("ChainMax") + PIERCE_VALUE * min(part("PierceCount"), PACK_SIZE)
                      + FORK_VALUE * part("ForkCountMax"))
    area = 1.0 + (PACK_SIZE - 1) * (part("AreaOfEffectRadius") / PACK_RADIUS) ** 2
    reach = max(part("ProjectileCount"), 1.0) * per_projectile * area
    # Reaching more enemies than the pack holds is worth less and less: 1 at a reach of 1, rising
    # at first one for one, approaching PACK_SIZE
    span = PACK_SIZE - 1
    targets = 1.0 + span * -math.expm1(-(min(reach, 1e6) - 1.0) / span)
    speed = part("Speed") or 1.0  # skills PoB gives no use rate count as one use per second
    movement = number(stats.get("EffectiveMovementSpeedMod")) or 1.0
    score = 0.0
    if damage > 0:
        seconds = PACK_SIZE / targets * (MONSTER_LIFE / damage + 1.0 / speed) + TRAVEL_SECONDS / max(movement, 0.1)
        score = MONSTER_LIFE * PACK_SIZE / seconds
    return {"score": score, "targets": targets, "uses_per_second": speed, "movement_speed": movement,
            "damage": damage}


def goal_value(goal: str, dps: float, clear: float) -> float:
    """A goal's value from boss DPS and the clear score: what a search maximizes, and what results
    from searches run on different goals are ranked by."""
    if goal == Goal.BOSS:
        return dps
    if goal == Goal.MAPPING:
        return clear
    return math.sqrt(max(dps, 0.0) * max(clear, 0.0))


class Objective:
    def __init__(self, ctx: Context, full: bool = False, multipliers: dict[str, float] | None = None,
                 goal: str | None = None) -> None:
        self.ctx = ctx
        self.base_full = full  # finalists: Full DPS throughout
        self.extra = False  # the build has triggered damage outside the main skill (see module doc)
        self.multipliers: dict[str, float] = dict(multipliers or {})  # requirement label -> weight factor
        self.penalties = ctx.compiled.penalties
        self.style = DefenceStyle(ctx.request.defence)
        # The request's goal unless overridden: balanced runs some searches on boss DPS (run.py)
        self.goal = Goal(goal or ctx.request.objective)
        # Clear fields in every mode, so a boss result can still show its clear estimate
        fields = set(BASE_FIELDS) | set(DARKNESS_FIELDS) | set(COST_WARNINGS) | set(SUSTAIN_FIELDS) | set(CLEAR_FIELDS)
        self._fields = sorted(fields | {p.stat for p in self.penalties})
        self.on_death = {name for name, gem in ctx.gems.items() if ON_DEATH.search(gem.get("description") or "")}

    @property
    def full(self) -> bool:
        return self.base_full or self.extra

    @property
    def fields(self) -> list[str]:
        return self._fields + ["SkillDPS"] if self.full else self._fields

    @staticmethod
    def prepare(stats: dict) -> dict:
        """Corrections to PoB's stats that every score and check sees (Darkness in max hit)."""
        return with_darkness(stats)

    def dps(self, stats: dict) -> float:
        """Single-target boss DPS."""
        if self.full:
            full = number(stats.get("FullDPS"))
            if full > 0:
                return max(full - self.excluded(stats)[0], 0.0)
        return main_dps(stats)

    def clear_damage(self, stats: dict) -> float:
        """Damage per target while clearing: like boss DPS, but on-death effects count (clearing
        kills enemies, which is what sets them off)."""
        if self.full:
            full = number(stats.get("FullDPS"))
            if full > 0:
                return full
        return main_dps(stats)

    def clear(self, stats: dict) -> dict:
        """The clear-speed estimate and its parts (clear_estimate)."""
        return clear_estimate(stats, self.clear_damage(stats))

    def value(self, stats: dict) -> float:
        """What this search maximizes, before penalties."""
        if self.goal == Goal.BOSS:
            return self.dps(stats)
        return goal_value(self.goal, self.dps(stats), self.clear(stats)["score"])

    def excluded(self, stats: dict) -> tuple[float, list[str]]:
        """Full DPS that comes from on-death effects, and their names."""
        amount, names = 0.0, []
        for entry in stats.get("SkillDPS") or []:
            source = entry.get("source") or entry.get("name")
            if entry.get("name") in self.on_death or source in self.on_death:
                amount += number(entry.get("dps")) * (entry.get("count") or 1)
                names.append(entry["name"] if source == entry.get("name") else f"{entry['name']} from {source}")
        return amount, names

    def deficits(self, stats: dict) -> list[tuple[str, float, bool]]:
        """(label, normalized deficit, hard) for everything currently short."""
        found: list[tuple[str, float, bool]] = []
        for penalty in self.penalties:
            amount = max(0.0, penalty.floor - number(stats.get(penalty.stat))) / max(penalty.floor, 1.0)
            if amount > 0:
                found.append((penalty.label, amount, False))
        for attribute in ("Str", "Dex", "Int"):
            need = number(stats.get("Req" + attribute))
            have = number(stats.get(attribute))
            if have < need:
                found.append((f"{attribute} requirement", (need - have) / max(need, 1.0), True))
        for field, pool in COST_WARNINGS.items():
            count = number(stats.get(field))
            if count > 0:
                found.append((f"Skill costs: not enough {pool}", count, True))
        share, pool = sustain(stats)
        if share < SUSTAIN_FLOOR:
            found.append((f"Skill cost sustain: {pool}", (SUSTAIN_FLOOR - share) / SUSTAIN_FLOOR, False))
        unreserved = number(stats.get("SpiritUnreserved"))
        if unreserved < 0:
            found.append(("Spirit", -unreserved / max(number(stats.get("Spirit")), 1.0), True))
        if self.style != DefenceStyle.ANY:
            layers = DEFENCE_LAYERS[self.style]
            values = {name: number(stats.get(name)) for name in ("Armour", "Evasion", "EnergyShield")}
            biggest = max(values.values())
            for name, value in values.items():
                if name in layers:
                    short = 1.0 if biggest <= 0 else max(0.0, 0.1 * biggest - value) / biggest
                    if short > 0:
                        found.append((f"Defence style: {name}", short, False))
                elif biggest > 0 and value >= 0.1 * biggest:
                    found.append((f"Defence style: no {name}", (value - 0.1 * biggest) / biggest + 0.05, False))
        return found

    def penalty(self, stats: dict) -> float:
        deficits = self.deficits(stats)
        total = sum((HARD_WEIGHT if hard else FLOOR_WEIGHT) * self.multipliers.get(label, 1.0) * amount
                    for label, amount, hard in deficits)
        if any(hard for _, _, hard in deficits):
            total += HARD_OFFSET
        if any(not hard for _, _, hard in deficits):
            total += FLOOR_OFFSET
        return total

    def escalate(self, stats: dict) -> bool:
        """Raise the weight of every requirement still short. False once all are at the cap."""
        raised = False
        for label, _, _ in self.deficits(stats):
            current = self.multipliers.get(label, 1.0)
            if current < MAX_MULTIPLIER:
                self.multipliers[label] = min(current * ESCALATION, MAX_MULTIPLIER)
                raised = True
        return raised

    def score(self, stats: dict) -> float:
        return math.log1p(max(self.value(stats), 0.0)) - self.penalty(stats)


def shortfall(deficits: list[dict]) -> float:
    """How far a result is from feasible, for ranking results whose scores used different weights:
    game limits count ten times a request floor."""
    return sum(d["amount"] * (10.0 if d["hard"] else 1.0) for d in deficits)


def rank_key(result: dict) -> tuple[float, float]:
    """Feasibility first (smallest shortfall), then the request's goal (boss DPS, clear or both)."""
    return round(shortfall(result["deficits"]), 6), -result.get("value", result["dps"])
