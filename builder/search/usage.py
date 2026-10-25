"""Usage requirements (PLAN.md §5.4): skills and supports that can't be used at will.

PoB marks them two ways: the HasUsageCondition skill type (a skill's own, or one a support adds to
the skills it supports: Hit and Run, Combo Finisher, Dauntless, ...) and constant stats naming what
a use needs (`active_skill_requires_X_glory`, `..._required_number_of_combo_stacks`, ...). It reads
them from PoB's gem data (bridge `usageRules`), not from a list of names. PoB itself calculates such
a skill as if it were used at will, so the search allows one only where the build has a real,
sustainable source for what a use needs:
- charges a use needs (Charged Staff, Spell Totem): while the resolver has evidence for that charge
- Glory a support adds together with its own source (Zerphi's Infamy: 5% of Life spent on skills
  becomes Glory, 50 per use): while the main skill's Life spending pays for UPTIME of its uses, so
  never on a build that spends no Life
- flask charges consumed on use (Ambrosia, Concoct): while the flask's charge generation pays for
  UPTIME of the uses (a boss fight refills flasks only by generation)
- everything else (Glory a skill builds from its own hits, Combo built by other skills, distance
  moved, standing still, recent blocks, Volatility, ...): never, since the skill wouldn't be used at
  the rate PoB calculates
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.conditions import UPTIME
from builder.search.objective import number

# Stats that mention a requirement but don't gate a use
IGNORED = re.compile(r"facing_angle|if_shapeshift_required|glory_required_\+%|difficulty_of_meeting|after_meeting|"
                     r"chance_to_not_consume")
REQUIRED = re.compile(r"require|cannot_be_used_unless")
CHARGES = re.compile(r"requires_X_(frenzy|power|endurance|power_or_endurance)_charges$")
CHARGE_VARS = {"frenzy": ("useFrenzyCharges",), "power": ("usePowerCharges",), "endurance": ("useEnduranceCharges",),
               "power_or_endurance": ("usePowerCharges", "useEnduranceCharges")}
GLORY = "active_skill_requires_X_glory"
GLORY_FROM_LIFE = "active_skill_generates_%_of_life_spent_as_glory"
FLASK = re.compile(r"consume_%_of_maximum_(life|mana)_flask_charges_on_skill_use$")
FIELDS = ["Speed", "LifePerSecondCost"]

_RULES: dict[str, dict] | None = None  # PoB's gem data doesn't change within a process


@dataclass(frozen=True)
class Requirement:
    kind: str  # "charges", "glory from life", "flask" or "never"
    label: str  # what a use needs, for notes and tests
    amount: float = 0.0
    rate: float = 0.0  # Glory per Life spent (fraction), or the share of flask charges a use takes
    pool: str = ""  # "Life" or "Mana" (flask)
    vars: tuple[str, ...] = ()  # resolver conditions that supply it (charges)


def requirements(name: str, rule: dict) -> list[Requirement]:
    """What a use of this gem needs, from its bridge `usageRules` entry."""
    stats = rule.get("stats") or {}
    found = []
    for stat, value in stats.items():
        if IGNORED.search(stat):
            continue
        if m := CHARGES.search(stat):
            kind = m.group(1)
            found.append(Requirement("charges", f"{number(value):g} {kind.replace('_', ' or ')} charges per use",
                                     number(value), vars=CHARGE_VARS[kind]))
        elif stat == GLORY and GLORY_FROM_LIFE in stats:
            rate = number(stats[GLORY_FROM_LIFE]) / 100
            found.append(Requirement("glory from life", f"{number(value):g} Glory per use, built from "
                                     f"{rate:.0%} of Life spent on skills", number(value), rate))
        elif m := FLASK.search(stat):
            pool = m.group(1).capitalize()
            found.append(Requirement("flask", f"{number(value):g}% of the {pool} Flask's charges per use",
                                     number(value), number(value) / 100, pool))
        elif REQUIRED.search(stat):
            found.append(Requirement("never", stat.replace("_", " ")))
    if (rule.get("usageCondition") or rule.get("addsUsageCondition")) and not found:
        found.append(Requirement("never", "a usage condition PoB doesn't model (e.g. distance moved, Combo)"))
    return found


class Usage:
    def __init__(self, build, conditions, flask_rate: Callable[[str], tuple[float, float]] | None = None) -> None:
        """`flask_rate(pool)`: (charges generated per second, maximum charges) of that flask."""
        self.build = build
        self.conditions = conditions
        self.flask_rate = flask_rate

    @property
    def rules(self) -> dict[str, dict]:
        global _RULES
        if _RULES is None:
            _RULES = self.build.call("usageRules")["gems"]
        return _RULES

    def blockers(self, name: str, stats: dict | None = None) -> list[str]:
        """Why this gem can't be used at the rate PoB calculates in the build as it is ([] if it can)."""
        rule = self.rules.get(name)
        if not rule:
            return []
        needs = requirements(name, rule)
        if not needs:
            return []
        if stats is None and any(r.kind in ("glory from life", "flask") for r in needs):
            stats = self.build.call("stats", fields=FIELDS)
        blocked = []
        for need in needs:
            if not self.sustained(need, stats or {}):
                blocked.append(f"{name}: {need.label}")
        return blocked

    def usable(self, name: str, stats: dict | None = None) -> bool:
        return not self.blockers(name, stats)

    def sustained(self, need: Requirement, stats: dict) -> bool:
        speed = number(stats.get("Speed"))
        if need.kind == "charges":
            return any(var in self.conditions.active for var in need.vars)
        if need.kind == "glory from life":
            return speed > 0 and need.rate * number(stats.get("LifePerSecondCost")) >= need.amount * speed * UPTIME
        if need.kind == "flask":
            if self.flask_rate is None:
                return False
            generated, maximum = self.flask_rate(need.pool)
            return speed > 0 and maximum > 0 and generated >= need.rate * maximum * speed * UPTIME
        return False
