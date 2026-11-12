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

Talismans (Usage.talisman_blockers; the manager's ruling, 12 Nov 2026). The game (KeywordPopups 'Talisman', poe2db's
Talisman page quotes the same): "While you have a Talisman in your active weapon set you will Shapeshift into its
associated form. As a result, Skills that cannot be used in a Shapeshift form cannot be used with Talismans. Triggered
Skills and Persistent Skills will continue to function." Neither PoB nor the search enforced it (checked by COVC3: grep
PoB Modules/*.lua and builder/search for Shapeshift / UsableWhileShapeshifted / Talisman). A skill is allowed in a
Talisman build when it is a form skill (PoB types Shapeshift, Bear, Wolf, Wyvern), UsableWhileShapeshifted,
Persistent (or reserves, HasReservation), Triggered, triggered by a Persistent meta gem in its group, carries a
Talisman exemption stat (bridge usageRules talismanExempt; a support in its group with one exempts it too), or sits
in a group the build holds on a weapon set without a Talisman. ASSUMPTIONS, labelled: (1) a skill of a different form
than the Talisman's (a Bear skill with a Wolf Talisman) stays allowed, since "You can change the Talisman's form at
any time" (UNVERIFIED whether a use changes form freely); (2) a skill socketed in a meta gem that isn't Persistent (Spell
Totem, Mortar Cannon, Ancestral Warrior Totem) follows that meta gem (UNVERIFIED); (3) a reserving skill without the
Persistent type counts as Persistent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from builder.search.conditions import UPTIME
from builder.search.objective import number

# Stats that mention a requirement but don't gate a use
IGNORED = re.compile(r"facing_angle|if_shapeshift_required|glory_required_\+%|difficulty_of_meeting|after_meeting|"
                     r"chance_to_not_consume|talisman")  # (Talisman exemptions: talisman_blockers)
REQUIRED = re.compile(r"require|cannot_be_used_unless")
CHARGES = re.compile(r"requires_X_(frenzy|power|endurance|power_or_endurance)_charges$")
CHARGE_VARS = {"frenzy": ("useFrenzyCharges",), "power": ("usePowerCharges",), "endurance": ("useEnduranceCharges",),
               "power_or_endurance": ("usePowerCharges", "useEnduranceCharges")}
GLORY = "active_skill_requires_X_glory"
GLORY_FROM_LIFE = "active_skill_generates_%_of_life_spent_as_glory"
FLASK = re.compile(r"consume_%_of_maximum_(life|mana)_flask_charges_on_skill_use$")
# The Concoctions' flat flask cost: flask_throw_base_charges_used 5 (Mana Flask charges a throw; Shattering, Acidic,
# Fulminating, Explosive, Poisonous Concoction). Read here but left unacted on, by the manager's ruling (12 Nov 2026):
# PoB throws them at will, an OPEN OVERCOUNT, until MECH-C's rate function (charges gained a second / 5 caps the throws)
# is on main and wired in (COVC2's report). Kept out of `needs` so the Concoctions' numbers don't change before then.
FLASK_THROW = "flask_throw_base_charges_used"
FIELDS = ["Speed", "LifePerSecondCost"]
TALISMAN = "Talisman"
# PoB skill types a Talisman doesn't shut out (module docstring)
FORM_FREE = {"Shapeshift", "Bear", "Wolf", "Wyvern", "UsableWhileShapeshifted", "Persistent", "HasReservation",
             "Triggered"}

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
        if IGNORED.search(stat) or stat == FLASK_THROW:
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


def usage_rules(build) -> dict[str, dict]:
    """The bridge's `usageRules` for every gem, read once."""
    global _RULES
    if _RULES is None:
        _RULES = build.call("usageRules")["gems"]
    return _RULES


class Usage:
    def __init__(self, build, conditions, flask_rate: Callable[[str], tuple[float, float]] | None = None,
                 weapons: dict[int, str | None] | None = None) -> None:
        """`flask_rate(pool)`: (charges generated per second, maximum charges) of that flask. `weapons`: the main-hand
        item type of each weapon set the build has ({1: ..., 2: ...}; set 2 only with weapon swap), for Talismans."""
        self.build = build
        self.conditions = conditions
        self.flask_rate = flask_rate
        self.weapons = weapons or {}

    @property
    def rules(self) -> dict[str, dict]:
        return usage_rules(self.build)

    def blockers(self, name: str, stats: dict | None = None) -> list[str]:
        """Why this gem can't be used at the rate PoB calculates in the build as it is ([] if it can)."""
        talisman = self.talisman_blockers(name)
        rule = self.rules.get(name)
        if not rule:
            return talisman
        needs = requirements(name, rule)
        if not needs:
            return talisman
        if stats is None and any(r.kind in ("glory from life", "flask") for r in needs):
            stats = self.build.call("stats", fields=FIELDS)
        blocked = []
        for need in needs:
            if not self.sustained(need, stats or {}):
                blocked.append(f"{name}: {need.label}")
        return talisman + blocked

    def gem(self, name: str) -> dict:
        ctx = self.build.ctx
        return (getattr(ctx, "all_gems", None) or {}).get(name) or ctx.gems.get(name) or {}

    def form_free(self, name: str) -> bool:
        """Can this skill be used while a Talisman Shapeshifts the player (module docstring)?"""
        return bool(FORM_FREE & set(self.gem(name).get("skillTypes") or []))             or bool((self.rules.get(name) or {}).get("talismanExempt"))

    def talisman_blockers(self, name: str) -> list[str]:
        """The Talisman rule: a skill that can't be used in a Shapeshift form can't be used from a weapon set holding a
        Talisman. Blocked when every group of the build that uses it is held only on Talisman sets with nothing that
        frees it (a gem not in the build yet would go in a new group, on every set)."""
        if TALISMAN not in self.weapons.values():
            return []
        gem = self.gem(name)
        if not gem or gem.get("support") or self.form_free(name):
            return []
        groups = [g for g in self.build.groups()
                  if g["enabled"] and any(x["name"] == name and not x["support"] for x in g["gems"])]
        freed = any(self.group_frees(g, name) for g in groups) if groups             else any(w != TALISMAN for w in self.weapons.values())
        return [] if freed else [f"{name}: can't be used in a Shapeshift form, so not with a Talisman (the game's Talisman rule)"]

    def group_frees(self, group: dict, name: str) -> bool:
        """This group lets `name` be used despite a Talisman: a set without one, a Persistent (trigger) meta gem, or an
        exempting support."""
        if any(w != TALISMAN for s, w in self.weapons.items() if group.get(f"set{s}", True)):
            return True
        for other in group["gems"]:
            if other["name"] == name:
                continue
            types = set(self.gem(other["name"]).get("skillTypes") or [])
            if not other["support"] and "Meta" in types and self.form_free(other["name"]):
                return True
            if other["support"] and (self.rules.get(other["name"]) or {}).get("talismanExempt"):
                return True
        return False

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
