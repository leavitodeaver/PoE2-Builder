"""The score a search maximizes: log damage minus weighted feasibility deficits.

- Screening uses main-skill DPS (CombinedDPS, or the minion's for minion skills); finalists use
  Full DPS, which costs far more per what-if on real builds (PLAN.md Phase 0 findings).
- Request floors (life, resistances, max hit, ...) are layer 2 of the constraint compiler:
  normalized deficits that pull the search toward feasible builds without hiding them.
- Limits the game enforces (attribute requirements, Spirit) weigh more than request floors.
- Defence floors use pools and maximum hit taken, never TotalEHP (a known snapshot bug inflates
  it, PLAN.md "Engine accuracy").
"""

from __future__ import annotations

import math
import re

from builder.request.schema import DefenceStyle
from builder.search.context import DEFENCE_LAYERS, Context

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


class Objective:
    def __init__(self, ctx: Context, full: bool = False, multipliers: dict[str, float] | None = None) -> None:
        self.ctx = ctx
        self.full = full
        self.multipliers: dict[str, float] = dict(multipliers or {})  # requirement label -> weight factor
        self.penalties = ctx.compiled.penalties
        self.style = DefenceStyle(ctx.request.defence)
        fields = set(BASE_FIELDS) | set(COST_WARNINGS) | {p.stat for p in self.penalties}
        if full:
            fields.add("SkillDPS")
        self.fields = sorted(fields)
        self.on_death = {name for name, gem in ctx.gems.items() if ON_DEATH.search(gem.get("description") or "")}

    def dps(self, stats: dict) -> float:
        if self.full:
            full = number(stats.get("FullDPS"))
            if full > 0:
                return max(full - self.excluded(stats)[0], 0.0)
        return main_dps(stats)

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
        return math.log1p(max(self.dps(stats), 0.0)) - self.penalty(stats)


def shortfall(deficits: list[dict]) -> float:
    """How far a result is from feasible, for ranking results whose scores used different weights:
    game limits count ten times a request floor."""
    return sum(d["amount"] * (10.0 if d["hard"] else 1.0) for d in deficits)


def rank_key(result: dict) -> tuple[float, float]:
    """Feasibility first (smallest shortfall), then damage."""
    return round(shortfall(result["deficits"]), 6), -result["dps"]
