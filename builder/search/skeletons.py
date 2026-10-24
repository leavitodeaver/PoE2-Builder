"""Skeletons (PLAN.md §5.1): ascendancy + main skill + weapon archetype. Every combination the
request allows is enumerated; nothing is fixed by accident. Illegal combinations are dropped at
the source with a reason, so an empty list explains itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from builder.request.constraints import meta_socketed, presses
from builder.request.schema import Offhand
from builder.search.context import TWO_HANDED, Context

# Weapons a spell or minion caster might hold: (item type, sub-type; "" = plain caster staff)
CASTER_WEAPONS = [("Wand", None), ("Sceptre", None), ("Staff", "")]
MELEE_FALLBACK = ["One Hand Mace", "Two Hand Mace", "Spear", "Staff"]
DUAL_WIELD = "Weapon"  # the offhand of a skeleton that holds a weapon in each hand


@dataclass(frozen=True)
class Skeleton:
    class_name: str
    ascendancy: str
    main_skill: str
    weapon: str | None  # main-hand item type; None = unarmed
    weapon_sub: str | None  # "Warstaff" for quarterstaves, "" for caster staves, None = any
    offhand: str | None  # "Shield", "Focus", "Quiver", DUAL_WIELD (an off-hand weapon) or None
    granted_by: str | None = None  # "passive:<name>" or "unique:<name>" for skills only those grant

    @property
    def label(self) -> str:
        weapon = "Quarterstaff" if self.weapon_sub == "Warstaff" else (self.weapon or "unarmed")
        source = f" (from {self.granted_by.split(':', 1)[1]})" if self.granted_by else ""
        offhand = "off-hand weapon" if self.offhand == DUAL_WIELD else self.offhand
        return f"{self.ascendancy} · {self.main_skill}{source} · {weapon}" + (f" + {offhand}" if offhand else "")

    def as_dict(self) -> dict:
        return asdict(self)


def is_button(gem: dict) -> bool:
    return presses(gem)


def offhand_weapon_types(ctx: Context, weapon: str | None) -> list[str]:
    """Weapons PoB lets this main hand hold in the other hand (ItemsTab:IsItemValidForSlot): a
    Sceptre beside any one-handed weapon; any other one-handed weapon except a Spear, but not beside
    a Wand or Sceptre. Two-handed weapons dual wield only with keystones (Giant's Blood), not modeled."""
    if weapon is None or weapon in TWO_HANDED:
        return []
    forbidden = ctx.compiled.filters.forbidden_item_classes
    types = {"Sceptre"}
    if weapon not in ("Wand", "Sceptre"):
        types |= {b["type"] for b in ctx.data.bases if "one_hand_weapon" in (b.get("tags") or {}) and b["type"] != "Spear"}
    return sorted(t for t in types if t not in forbidden)


def offhand_types(ctx: Context, weapon: str | None, offhand: str | None) -> tuple[str, ...]:
    """Item types the off-hand slot of a skeleton takes."""
    if offhand == DUAL_WIELD:
        return tuple(offhand_weapon_types(ctx, weapon))
    return (offhand,) if offhand else ()


def weapon_archetypes(ctx: Context, skill: str, weapon_unique: str | None = None
                      ) -> tuple[list[tuple[str | None, str | None, str | None]], list[str]]:
    """(weapon, sub-type, offhand) choices for a main skill under the request. `weapon_unique`
    fixes the weapon (the request's, or a unique that grants the skill)."""
    gem = ctx.all_gems[skill]
    request = ctx.request
    weapon_unique = weapon_unique or request.weapon_unique
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

    if weapon_unique:
        option = ctx.unique_option(weapon_unique)
        if option is None:
            return [], [f"weapon {weapon_unique} isn't a known unique"]
        fixed = (option.type, option.sub_type or "") if option.type == "Staff" else (option.type, None)
        mains = [m for m in mains if m[0] == fixed[0] and (m[1] is None or m[1] == fixed[1])] or \
            ([(fixed[0], fixed[1] or None)] if not attack else [])
        if not mains:
            problems.append(f"{skill} can't be used with {weapon_unique} ({option.type})")
    if request.weapon_type:
        mains = [m for m in mains if m[0] == request.weapon_type]
        if not mains:
            problems.append(f"{skill} can't be used with a {request.weapon_type}")
    mains = [m for m in mains if m[0] is None or m[0] not in forbidden]

    offhand_choice = Offhand(request.offhand)
    archetypes = []
    for weapon, sub in mains:
        two_handed = weapon in TWO_HANDED
        dual = bool(offhand_weapon_types(ctx, weapon))
        if offhand_choice == Offhand.WEAPON:
            if dual:
                archetypes.append((weapon, sub, DUAL_WIELD))
            else:
                problems.append(f"a {weapon or 'bare hand'} can't be dual wielded (PoB needs a one-handed main weapon)"
                                if two_handed or weapon is None else f"no off-hand weapon is allowed beside a {weapon}")
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
            offhands = [o for o in ("Shield", "Focus") if o not in forbidden] + ([DUAL_WIELD] if dual else [])
            archetypes += [(weapon, sub, offhand) for offhand in offhands or [None]]
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
    if skill not in ctx.gems and not ctx.granted_by.get(skill):
        return [f"{skill} isn't a gem a level {ctx.level} character can socket, and no passive or unique grants it"]
    if skill in ctx.compiled.filters.forbidden_gems:
        return [f"{skill} is banned by the request"]
    buttons = {skill} if skill in ctx.gems and is_button(ctx.gems[skill]) else set()
    # Required spells the setup sockets into a required meta skill are triggered by it, not pressed
    socketed = meta_socketed([n for n in ctx.request.require.skills if n != skill], ctx.gems)
    for name in ctx.request.require.skills:
        gem = ctx.gems.get(name)
        if gem is None:
            if ctx.skill_source(name) is None:
                return [f"required skill {name} isn't a gem, and no allowed passive or unique grants it"]
            continue  # it comes with its source, and granted skills aren't buttons
        if is_button(gem) and name not in socketed:
            buttons.add(name)
    cap = ctx.request.max_buttons
    if cap is not None and len(buttons) > cap:
        return [f"{', '.join(sorted(buttons))} need {len(buttons)} buttons; the cap is {cap}"]
    return []


def required_holders(ctx: Context) -> list[tuple[str, str]]:
    """(skill, unique) for required skills that only a unique grants: that unique must be worn."""
    holders = []
    for name in ctx.request.require.skills:
        if name not in ctx.gems and (source := ctx.skill_source(name)) and source[0] == "unique":
            holders.append((name, source[1]))
    return holders


def fits_holders(ctx: Context, weapon: str | None, offhand: str | None) -> bool:
    held = offhand_types(ctx, weapon, offhand)
    for _, unique in required_holders(ctx):
        option = ctx.unique_option(unique)
        if option is None:
            return False
        if option.type in ("Shield", "Focus", "Quiver") and option.type not in held:
            return False
        if option.type in ctx.data.weapon_types and option.type != weapon and option.type not in held:
            return False
    return True


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
        if skill in ctx.gems:
            archetypes, found = weapon_archetypes(ctx, skill)
            problems += found
            for ascendancy in ascendancies:
                for weapon, sub, offhand in archetypes:
                    skeletons.append(Skeleton(ctx.class_of[ascendancy], ascendancy, skill, weapon, sub, offhand))
            continue
        # A skill only a passive or a unique grants: that source becomes part of the skeleton
        for kind, source, source_ascendancy in ctx.granted_by.get(skill, []):
            if kind == "passive" and source in ctx.compiled.filters.forbidden_passives:
                problems.append(f"{skill} comes from {source}, which the request bans")
                continue
            if kind == "unique" and source in ctx.compiled.filters.forbidden_uniques:
                problems.append(f"{skill} comes from {source}, which the request bans")
                continue
            archetypes, found = weapon_archetypes(ctx, skill, source if kind == "unique" and _is_weapon(ctx, source) else None)
            problems += found
            usable = [a for a in ascendancies if source_ascendancy in (None, a)]
            if not usable:
                problems.append(f"{skill} comes from the {source_ascendancy} passive {source}, which the request doesn't allow")
            for ascendancy in usable:
                for weapon, sub, offhand in archetypes:
                    skeletons.append(Skeleton(ctx.class_of[ascendancy], ascendancy, skill, weapon, sub, offhand,
                                              f"{kind}:{source}"))
    kept = [s for s in skeletons if fits_holders(ctx, s.weapon, s.offhand)]
    if skeletons and not kept:
        held = ", ".join(f"{skill} (from {unique})" for skill, unique in required_holders(ctx))
        problems.append(f"no weapon setup for the main skill can also hold {held}")
    return kept, sorted(set(problems))


def _is_weapon(ctx: Context, unique: str) -> bool:
    option = ctx.unique_option(unique)
    return option is not None and option.type in ctx.data.weapon_types


def candidate_skills(ctx: Context) -> list[str]:
    """Main skills to screen when the request doesn't name one: every socketable, allowed active
    skill that isn't a support, meta or pure utility gem."""
    names = []
    for name, gem in ctx.gems.items():
        # Every socketable active skill; the screen drops those PoB gives no damage. Meta gems
        # only trigger other skills, so they aren't main skills themselves.
        if gem["support"] or "Meta" in gem["skillTypes"] or name in ctx.compiled.filters.forbidden_gems:
            continue
        if not skill_problems(ctx, name):
            names.append(name)
    return sorted(names)
