"""Compile a BuildRequest against game data into the three constraint layers of PLAN.md §4:

1. Filters: what the search may never propose (banned gems, passives, uniques, ...).
2. Penalties: normalized floor deficits that steer the search toward feasible builds.
3. Checks: the final Requested vs Delivered table, run on a build's facts (bridge `facts`).

Unknown or misspelled names are errors with suggestions; nothing is silently dropped.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field

from builder.data import GameData
from builder.request.schema import BuildRequest, DamageType, DefenceStyle, Offhand, WeaponSwap

FACT_FIELDS = [
    "FullDPS", "CombinedDPS", "Minion.CombinedDPS", "Life", "EnergyShield", "Mana", "Armour", "Evasion",
    "FireResist", "ColdResist", "LightningResist", "ChaosResist", "PhysicalMaximumHitTaken",
    "FireMaximumHitTaken", "ColdMaximumHitTaken", "LightningMaximumHitTaken", "ChaosMaximumHitTaken",
    "EffectiveBlockChance", "EvadeChance", "SpiritUnreserved", "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
]
RESIST_STAT = {d: f"{d.value.capitalize()}Resist" for d in DamageType}
MAX_HIT_STAT = {d: f"{d.value.capitalize()}MaximumHitTaken" for d in DamageType}
# Skill types whose gems the player doesn't press
NOT_A_BUTTON = {"Persistent", "Meta", "Triggered", "InbuiltTrigger"}
OFFHAND_TYPES = {Offhand.SHIELD: {"Shield"}, Offhand.FOCUS: {"Focus"}, Offhand.QUIVER: {"Quiver"}}


@dataclass
class Issue:
    level: str  # "error" or "warning"
    field: str
    message: str


@dataclass
class Filters:
    ascendancies: list[str]
    forbidden_gems: set[str] = field(default_factory=set)
    forbidden_passives: set[str] = field(default_factory=set)
    forbidden_uniques: set[str] = field(default_factory=set)
    forbidden_item_classes: set[str] = field(default_factory=set)
    forbidden_augments: set[str] = field(default_factory=set)


@dataclass
class Penalty:
    label: str
    stat: str
    floor: float

    def deficit(self, stats: dict) -> float:
        value = stats.get(self.stat)
        value = value if isinstance(value, (int, float)) else 0.0
        return max(0.0, self.floor - value) / max(self.floor, 1.0)


@dataclass
class CheckResult:
    label: str
    requested: str
    delivered: str
    ok: bool | None  # None: not checked yet
    note: str = ""


Check = Callable[[dict], CheckResult]


@dataclass
class CompiledRequest:
    request: BuildRequest
    issues: list[Issue]
    filters: Filters
    penalties: list[Penalty]
    checks: list[Check]

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "error"]

    def feasibility_penalty(self, stats: dict) -> float:
        return sum(penalty.deficit(stats) for penalty in self.penalties)

    def check(self, facts: dict) -> list[CheckResult]:
        return [check(facts) for check in self.checks]


# Facts helpers ---------------------------------------------------------------------------------


def main_skill(facts: dict) -> str | None:
    for group in facts["skillGroups"]:
        if group["main"]:
            for gem in group["gems"]:
                if gem["enabled"] and not gem["support"]:
                    return gem["name"]
    return None


def player_buttons(facts: dict) -> list[str]:
    """Skills the player presses. Persistent, meta, triggered and item-granted skills don't count."""
    names: list[str] = []
    for group in facts["skillGroups"]:
        if not group["enabled"] or group["fromItem"]:
            continue
        gems = [g for g in group["gems"] if g["enabled"]]
        triggered_by_meta = any("Meta" in g["skillTypes"] for g in gems if not g["support"])
        for gem in gems:
            if gem["support"] or set(gem["skillTypes"]) & NOT_A_BUTTON:
                continue
            if triggered_by_meta:
                continue
            if gem["name"] not in names:
                names.append(gem["name"])
    return names


def enabled_gems(facts: dict) -> list[dict]:
    return [g for group in facts["skillGroups"] if group["enabled"] for g in group["gems"] if g["enabled"]]


def stat(facts: dict, name: str) -> float:
    value = facts["stats"].get(name)
    if value in ("inf", "-inf"):
        return float(value)
    return value if isinstance(value, (int, float)) else 0.0


def fmt(value: float) -> str:
    return "∞" if value == float("inf") else f"{value:,.0f}"


def listing(names: list[str] | set[str]) -> str:
    return ", ".join(sorted(names)) if names else "none"


# Compiler --------------------------------------------------------------------------------------


class _Names:
    """Case-insensitive name lookup with suggestions."""

    def __init__(self, names: list[str]) -> None:
        self.by_lower = {name.lower(): name for name in names}

    def resolve(self, name: str) -> str | None:
        return self.by_lower.get(name.strip().lower())

    def suggest(self, name: str) -> list[str]:
        close = difflib.get_close_matches(name.lower(), list(self.by_lower), n=3, cutoff=0.6)
        return [self.by_lower[c] for c in close]


def compile_request(request: BuildRequest, data: GameData) -> CompiledRequest:
    issues: list[Issue] = []
    passive_names = sorted({n["name"] for n in data.tree["nodes"] if n["type"] in ("Keystone", "Notable") and n["name"]})
    mechanic_names = sorted({t for g in data.gems for t in g["tags"]} | {t for g in data.gems for t in g["skillTypes"]})
    lookups = {
        "skills": _Names(data.active_skills),
        "supports": _Names(data.supports),
        "passives": _Names(passive_names),
        "uniques": _Names(data.unique_names),
        "item_classes": _Names(data.item_classes),
        "augments": _Names(data.augments),
        "mechanics": _Names(mechanic_names),
    }

    def resolve(category: str, name: str, field_name: str) -> str | None:
        found = lookups[category].resolve(name)
        if found is None:
            suggestions = lookups[category].suggest(name)
            hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
            issues.append(Issue("error", field_name, f"Unknown {category.replace('_', ' ').rstrip('s')} '{name}'.{hint}"))
        return found

    def resolve_choices(choices, prefix: str) -> dict[str, list[str]]:
        resolved: dict[str, list[str]] = {}
        for category, names in choices.__dict__.items():
            resolved[category] = [r for n in names if (r := resolve(category, n, f"{prefix}.{category}"))]
        return resolved

    required = resolve_choices(request.require, "require")
    forbidden = resolve_choices(request.forbid, "forbid")

    # Identity
    classes = data.classes
    if request.class_name and request.class_name not in classes:
        issues.append(Issue("error", "class_name", f"Unknown class '{request.class_name}'. Classes: {', '.join(classes)}."))
    for ascendancy in request.ascendancies:
        if ascendancy not in data.ascendancies:
            issues.append(Issue("error", "ascendancies", f"Unknown ascendancy '{ascendancy}'."))
        elif request.class_name in classes and ascendancy not in classes[request.class_name]:
            issues.append(Issue("error", "ascendancies", f"{ascendancy} isn't a {request.class_name} ascendancy."))
    if request.ascendancies:
        allowed_ascendancies = [a for a in request.ascendancies if a in data.ascendancies]
    elif request.class_name in classes:
        allowed_ascendancies = list(classes[request.class_name])
    else:
        allowed_ascendancies = list(data.ascendancies)

    main = resolve("skills", request.main_skill, "main_skill") if request.main_skill else None
    weapon_type = request.weapon_type
    if weapon_type and weapon_type not in data.weapon_types:
        issues.append(Issue("error", "weapon_type", f"Unknown weapon type '{weapon_type}'. Types: {', '.join(data.weapon_types)}."))
    weapon_unique = resolve("uniques", request.weapon_unique, "weapon_unique") if request.weapon_unique else None

    # Layer 1: filters
    mechanics = {m.lower() for m in forbidden["mechanics"]}
    forbidden_gems = set(forbidden["skills"]) | set(forbidden["supports"])
    for gem in data.gems:
        if mechanics & {t.lower() for t in gem["tags"] + gem["skillTypes"]}:
            forbidden_gems.add(gem["name"])
    if main and main in forbidden_gems:
        issues.append(Issue("error", "main_skill", f"Main skill {main} is excluded by the banned mechanics."))
    filters = Filters(
        ascendancies=allowed_ascendancies,
        forbidden_gems=forbidden_gems,
        forbidden_passives=set(forbidden["passives"]),
        forbidden_uniques=set(forbidden["uniques"]),
        forbidden_item_classes=set(forbidden["item_classes"]),
        forbidden_augments=set(forbidden["augments"]),
    )

    # Layer 2: penalties
    floors = request.floors
    penalties: list[Penalty] = []
    for label, stat_name, floor in (
        ("Life", "Life", floors.life), ("Energy shield", "EnergyShield", floors.energy_shield),
        ("Mana", "Mana", floors.mana), ("Block chance", "EffectiveBlockChance", floors.block),
        ("Evade chance", "EvadeChance", floors.evade), ("Spare spirit", "SpiritUnreserved", floors.spare_spirit),
    ):
        if floor is not None:
            penalties.append(Penalty(label, stat_name, floor))
    for damage, floor in floors.resists.items():
        penalties.append(Penalty(f"{damage.capitalize()} resistance", RESIST_STAT[DamageType(damage)], floor))
    for damage, floor in floors.max_hit.items():
        penalties.append(Penalty(f"{damage.capitalize()} max hit", MAX_HIT_STAT[DamageType(damage)], floor))

    # Layer 3: checks
    checks: list[Check] = []

    if request.class_name:
        checks.append(lambda f: CheckResult("Class", request.class_name, f["className"], f["className"] == request.class_name))
    if request.ascendancies or request.class_name:
        checks.append(lambda f: CheckResult("Ascendancy", " or ".join(allowed_ascendancies), f["ascendClassName"],
                                            f["ascendClassName"] in allowed_ascendancies))
    if main:
        checks.append(lambda f: CheckResult("Main skill", main, main_skill(f) or "none", main_skill(f) == main))
    if request.max_buttons:
        checks.append(lambda f: CheckResult(
            "Buttons", f"at most {request.max_buttons}", f"{len(player_buttons(f))} ({listing(player_buttons(f))})",
            len(player_buttons(f)) <= request.max_buttons, "counts skills you press; persistent, meta and triggered skills excluded"))

    def presence_check(label: str, names: list[str], present: Callable[[dict], set[str]], want: bool) -> None:
        if not names:
            return
        def run(f: dict) -> CheckResult:
            found = present(f) & set(names)
            ok = found == set(names) if want else not found
            return CheckResult(label, ("all of: " if want else "none of: ") + listing(names), listing(found), ok)
        checks.append(run)

    gems_present = lambda f: {g["name"] for g in enabled_gems(f)}
    passives_present = lambda f: {p["name"] for p in f["passives"]}
    uniques_present = lambda f: {i["name"] for i in f["items"] if i["rarity"] == "UNIQUE"}
    augments_present = lambda f: {r for i in f["items"] for r in i["runes"] if isinstance(r, str)}
    classes_present = lambda f: {i["type"] for i in f["items"]}
    presence_check("Required skills", required["skills"], gems_present, True)
    presence_check("Required supports", required["supports"], gems_present, True)
    presence_check("Banned skills and supports", sorted(set(forbidden["skills"]) | set(forbidden["supports"])), gems_present, False)
    presence_check("Required passives", required["passives"], passives_present, True)
    presence_check("Banned passives", forbidden["passives"], passives_present, False)
    presence_check("Required uniques", required["uniques"], uniques_present, True)
    presence_check("Banned uniques", forbidden["uniques"], uniques_present, False)
    presence_check("Required augments", required["augments"], augments_present, True)
    presence_check("Banned augments", forbidden["augments"], augments_present, False)
    presence_check("Banned item classes", forbidden["item_classes"], classes_present, False)
    if forbidden["mechanics"]:
        checks.append(lambda f: (lambda hit: CheckResult(
            "Banned mechanics", "none of: " + listing(forbidden["mechanics"]), listing(hit), not hit))(
            {g["name"] for g in enabled_gems(f) if not g["support"] and g["name"] in forbidden_gems}))

    def slot_item(f: dict, slot: str) -> dict | None:
        return next((i for i in f["items"] if i["slot"] == slot), None)

    if weapon_type:
        checks.append(lambda f: (lambda w: CheckResult("Weapon type", weapon_type, w["type"] if w else "none",
                                                       bool(w) and w["type"] == weapon_type))(slot_item(f, "Weapon 1")))
    if weapon_unique:
        checks.append(lambda f: (lambda w: CheckResult("Weapon", weapon_unique, w["name"] if w else "none",
                                                       bool(w) and w["name"] == weapon_unique))(slot_item(f, "Weapon 1")))
    if request.offhand != Offhand.ANY:
        def offhand_check(f: dict) -> CheckResult:
            item = slot_item(f, "Weapon 2")
            delivered = item["type"] if item else "none"
            if request.offhand == Offhand.NONE:
                ok = item is None
            elif request.offhand == Offhand.WEAPON:
                ok = item is not None and item["type"] in data.weapon_types
            else:
                ok = item is not None and item["type"] in OFFHAND_TYPES[Offhand(request.offhand)]
            return CheckResult("Offhand", request.offhand, delivered, ok)
        checks.append(offhand_check)
    if request.weapon_swap != WeaponSwap.MAY:
        def swap_check(f: dict) -> CheckResult:
            used = any(i["slot"].endswith("Swap") for i in f["items"])
            ok = used if request.weapon_swap == WeaponSwap.MUST else not used
            return CheckResult("Weapon swap", request.weapon_swap, "used" if used else "not used", ok,
                               "approximate: checks for gear in the swap slots")
        checks.append(swap_check)

    if request.defence != DefenceStyle.ANY:
        layers = {
            DefenceStyle.ARMOUR: {"Armour"}, DefenceStyle.EVASION: {"Evasion"},
            DefenceStyle.ENERGY_SHIELD: {"EnergyShield"}, DefenceStyle.ARMOUR_EVASION: {"Armour", "Evasion"},
            DefenceStyle.ARMOUR_ENERGY_SHIELD: {"Armour", "EnergyShield"},
            DefenceStyle.EVASION_ENERGY_SHIELD: {"Evasion", "EnergyShield"},
            DefenceStyle.HYBRID: {"Armour", "Evasion", "EnergyShield"},
        }[DefenceStyle(request.defence)]

        def defence_check(f: dict) -> CheckResult:
            values = {name: stat(f, name) for name in ("Armour", "Evasion", "EnergyShield")}
            biggest = max(values.values()) or 1.0
            leaned = {name for name, value in values.items() if value >= 0.1 * biggest and value > 0}
            delivered = ", ".join(f"{name} {fmt(value)}" for name, value in values.items())
            return CheckResult("Defence style", request.defence, delivered, leaned == layers,
                               "approximate: a layer counts when it's at least 10% of the largest")
        checks.append(defence_check)

    for penalty in penalties:
        checks.append(lambda f, p=penalty: CheckResult(p.label, f"≥ {fmt(p.floor)}", fmt(stat(f, p.stat)), stat(f, p.stat) >= p.floor))

    # Always checked: things the game itself enforces, which PoB only warns about
    checks.append(lambda f: (lambda short: CheckResult(
        "Attribute requirements", "met", "met" if not short else "short: " + ", ".join(short), not short))(
        [f"{a} {fmt(stat(f, a))}/{fmt(stat(f, 'Req' + a))}" for a in ("Str", "Dex", "Int") if stat(f, a) < stat(f, "Req" + a)]))
    if floors.spare_spirit is None:
        checks.append(lambda f: CheckResult("Spirit", "not over-reserved", f"{fmt(stat(f, 'SpiritUnreserved'))} spare",
                                            stat(f, "SpiritUnreserved") >= 0))
    checks.append(lambda f: CheckResult("PoB warnings", "none", "; ".join(f["warnings"]) or "none", not f["warnings"],
                                        "PoB flags things the game wouldn't allow, e.g. too many gem groups"))

    if request.dps_gate:
        def dps_check(f: dict) -> CheckResult:
            dps = max(stat(f, "FullDPS"), stat(f, "CombinedDPS"), stat(f, "Minion.CombinedDPS"))
            return CheckResult("DPS gate", f"≥ {fmt(request.dps_gate)}", fmt(dps), dps >= request.dps_gate,
                               "PoB number before ledger corrections")
        checks.append(dps_check)

    if request.budget.divines is not None:
        checks.append(lambda f: CheckResult("Budget", f"≤ {request.budget.divines:g} divine", "not priced yet", None,
                                            "item pricing arrives in Phase 3"))

    return CompiledRequest(request, issues, filters, penalties, checks)
