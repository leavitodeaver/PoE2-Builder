"""Skeletons (PLAN.md §5.1): ascendancy + main skill + weapon archetype. Every combination the
request allows is enumerated; nothing is fixed by accident. Illegal combinations are dropped at
the source with a reason, so an empty list explains itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from builder.request.constraints import NOT_A_BUTTON
from builder.request.schema import Offhand
from builder.search.context import TWO_HANDED, Context

# Weapons a spell or minion caster might hold: (item type, sub-type; "" = plain caster staff)
CASTER_WEAPONS = [("Wand", None), ("Sceptre", None), ("Staff", "")]
MELEE_FALLBACK = ["One Hand Mace", "Two Hand Mace", "Spear", "Staff"]


@dataclass(frozen=True)
class Skeleton:
    class_name: str
    ascendancy: str
    main_skill: str
    weapon: str | None  # main-hand item type; None = unarmed
    weapon_sub: str | None  # "Warstaff" for quarterstaves, "" for caster staves, None = any
    offhand: str | None  # "Shield", "Focus", "Quiver" or None

    @property
    def label(self) -> str:
        weapon = "Quarterstaff" if self.weapon_sub == "Warstaff" else (self.weapon or "unarmed")
        return f"{self.ascendancy} · {self.main_skill} · {weapon}" + (f" + {self.offhand}" if self.offhand else "")

    def as_dict(self) -> dict:
        return asdict(self)


def is_button(gem: dict) -> bool:
    return not gem["support"] and not (set(gem["skillTypes"]) & NOT_A_BUTTON)


def weapon_archetypes(ctx: Context, skill: str) -> tuple[list[tuple[str | None, str | None, str | None]], list[str]]:
    """(weapon, sub-type, offhand) choices for a main skill under the request."""
    gem = ctx.gems[skill]
    request = ctx.request
    forbidden = ctx.compiled.filters.forbidden_item_classes
    problems: list[str] = []
    types = gem.get("weaponTypes") or []
    attack = "Attack" in gem["skillTypes"]
    if attack and types:
        mains = [(t, "Warstaff" if t == "Staff" else None) for t in types if t != "None"]
        if "None" in types:
            mains.append((None, None))
    elif attack:
        mains = [(t, "Warstaff" if t == "Staff" else None) for t in MELEE_FALLBACK]
    else:
        mains = list(CASTER_WEAPONS)

    if request.weapon_unique:
        option = ctx.unique_option(request.weapon_unique)
        if option is None:
            return [], [f"weapon {request.weapon_unique} isn't a known unique"]
        fixed = (option.type, option.sub_type or "") if option.type == "Staff" else (option.type, None)
        mains = [m for m in mains if m[0] == fixed[0] and (m[1] is None or m[1] == fixed[1])]
        if not mains:
            problems.append(f"{skill} can't be used with {request.weapon_unique} ({option.type})")
    if request.weapon_type:
        mains = [m for m in mains if m[0] == request.weapon_type]
        if not mains:
            problems.append(f"{skill} can't be used with a {request.weapon_type}")
    mains = [m for m in mains if m[0] is None or m[0] not in forbidden]

    offhand_choice = Offhand(request.offhand)
    archetypes = []
    for weapon, sub in mains:
        two_handed = weapon in TWO_HANDED
        if offhand_choice == Offhand.WEAPON:
            problems.append("dual wielding isn't modeled yet")
            continue
        if two_handed or weapon is None:
            if weapon == "Bow" and offhand_choice in (Offhand.ANY, Offhand.QUIVER) and "Quiver" not in forbidden:
                archetypes.append((weapon, sub, "Quiver"))
            elif offhand_choice in (Offhand.ANY, Offhand.NONE):
                archetypes.append((weapon, sub, None))
            continue
        if offhand_choice == Offhand.NONE:
            archetypes.append((weapon, sub, None))
        elif offhand_choice == Offhand.ANY:
            # Shield and Focus both get a skeleton: which pays more (block and armour, or a
            # focus's mana and energy shield) depends on the request
            offhands = [o for o in ("Shield", "Focus") if o not in forbidden] or [None]
            archetypes += [(weapon, sub, offhand) for offhand in offhands]
        elif offhand_choice in (Offhand.SHIELD, Offhand.FOCUS):
            name = "Shield" if offhand_choice == Offhand.SHIELD else "Focus"
            if name not in forbidden:
                archetypes.append((weapon, sub, name))
    if not archetypes and not problems:
        problems.append(f"no weapon setup for {skill} satisfies the weapon, offhand and item-class rules")
    return archetypes, problems


def usable_ascendancies(ctx: Context) -> list[str]:
    """Allowed ascendancies that have passives of their own."""
    names = []
    for name in ctx.compiled.filters.ascendancies:
        start, adjacency = ctx.ascendancy_graph(name)
        if start is not None and len(adjacency) > 1:
            names.append(name)
    return names


def skill_problems(ctx: Context, skill: str) -> list[str]:
    if skill not in ctx.gems:
        return [f"{skill} isn't a gem a level {ctx.level} character can socket"]
    if skill in ctx.compiled.filters.forbidden_gems:
        return [f"{skill} is banned by the request"]
    buttons = {skill} if is_button(ctx.gems[skill]) else set()
    for name in ctx.request.require.skills:
        gem = ctx.gems.get(name)
        if gem is None:
            return [f"required skill {name} isn't a gem a level {ctx.level} character can socket"]
        if is_button(gem):
            buttons.add(name)
    cap = ctx.request.max_buttons
    if cap is not None and len(buttons) > cap:
        return [f"{', '.join(sorted(buttons))} need {len(buttons)} buttons; the cap is {cap}"]
    return []


def enumerate_skeletons(ctx: Context, skills: list[str]) -> tuple[list[Skeleton], list[str]]:
    problems: list[str] = []
    ascendancies = usable_ascendancies(ctx)
    if not ascendancies:
        problems.append("no allowed ascendancy has passives to allocate")
    skeletons = []
    for skill in skills:
        found = skill_problems(ctx, skill)
        if found:
            problems += found
            continue
        archetypes, found = weapon_archetypes(ctx, skill)
        problems += found
        for ascendancy in ascendancies:
            for weapon, sub, offhand in archetypes:
                skeletons.append(Skeleton(ctx.class_of[ascendancy], ascendancy, skill, weapon, sub, offhand))
    return skeletons, sorted(set(problems))


def candidate_skills(ctx: Context) -> list[str]:
    """Main skills to screen when the request doesn't name one: every socketable, allowed active
    skill that isn't a support, meta or pure utility gem."""
    names = []
    for name, gem in ctx.gems.items():
        types = set(gem["skillTypes"])
        if gem["support"] or "Meta" in types or name in ctx.compiled.filters.forbidden_gems:
            continue
        if not (types & {"Attack", "Spell", "Damage", "CreatesMinion", "Minion", "Trapped", "Totem"}):
            continue
        if not skill_problems(ctx, name):
            names.append(name)
    return sorted(names)
