"""Skeletons (PLAN.md §5.1): ascendancy + main skill + weapon archetype. Every combination the
request allows is enumerated; nothing is fixed by accident. Illegal combinations are dropped at
the source with a reason, so an empty list explains itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from builder.request.constraints import main_skill as facts_main_skill, meta_socketed, presses
from builder.request.schema import Offhand, WeaponSwap
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
    # Weapon set 2 (weapon swap): its main hand, sub-type and off hand, like the fields above
    swap: bool = False
    swap_weapon: str | None = None
    swap_sub: str | None = None
    swap_offhand: str | None = None
    seeded: bool = False  # starts from the request's PoB code (the seed) instead of from scratch

    @property
    def label(self) -> str:
        source = f" (from {self.granted_by.split(':', 1)[1]})" if self.granted_by else ""
        label = f"{self.ascendancy} · {self.main_skill}{source} · {_hands(self.weapon, self.weapon_sub, self.offhand)}"
        if self.swap:
            label += f" · swap: {_hands(self.swap_weapon, self.swap_sub, self.swap_offhand)}"
        return label + (" · from your PoB code" if self.seeded else "")

    def as_dict(self) -> dict:
        return asdict(self)


def _hands(weapon: str | None, sub: str | None, offhand: str | None) -> str:
    name = "Quarterstaff" if sub == "Warstaff" else (weapon or "unarmed")
    offhand = "off-hand weapon" if offhand == DUAL_WIELD else offhand
    return name + (f" + {offhand}" if offhand else "")


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


def weapon_archetypes(ctx: Context, skill: str, weapon_unique: str | None = None, swap: bool = False
                      ) -> tuple[list[tuple[str | None, str | None, str | None]], list[str]]:
    """(weapon, sub-type, offhand) choices for a main skill under the request. `weapon_unique`
    fixes the weapon (the request's, or a unique that grants the skill). For weapon set 2 (`swap`)
    the request's weapon, weapon type and offhand don't apply: they describe set 1."""
    gem = ctx.all_gems[skill]
    request = ctx.request
    weapon_unique = weapon_unique or (None if swap else request.weapon_unique)
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
    if request.weapon_type and not swap:
        mains = [m for m in mains if m[0] == request.weapon_type]
        if not mains:
            problems.append(f"{skill} can't be used with a {request.weapon_type}")
    mains = [m for m in mains if m[0] is None or m[0] not in forbidden]

    offhand_choice = Offhand.ANY if swap else Offhand(request.offhand)
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


def usable_with(gem: dict, weapon: str | None, offhands: tuple[str, ...] = ()) -> bool:
    """Whether a skill works with a weapon set: attacks need a weapon type they list (in either
    hand when dual wielding); other skills work with anything."""
    types = gem.get("weaponTypes") or []
    if "Attack" not in gem["skillTypes"] or not types:
        return True
    return (weapon or "None") in types or any(t in types for t in offhands)


def swap_skills(ctx: Context, skeleton: Skeleton) -> list[str]:
    """Required skills (besides the main skill) weapon set 1 can't use: they need weapon set 2."""
    held = offhand_types(ctx, skeleton.weapon, skeleton.offhand)
    return [name for name in ctx.request.require.skills
            if name != skeleton.main_skill and name in ctx.gems and not usable_with(ctx.gems[name], skeleton.weapon, held)]


def with_swap(ctx: Context, skeleton: Skeleton) -> tuple[Skeleton, list[str]]:
    """The skeleton with weapon set 2 where the request calls for it: holding the weapons required
    skills need when set 1 can't use them (unless swapping is forbidden), or, when swapping is a
    must, a copy of set 1's weapon types for skills the search moves there."""
    policy = WeaponSwap(ctx.request.weapon_swap)
    needing = swap_skills(ctx, skeleton)
    if needing and policy == WeaponSwap.NEVER:
        return skeleton, [f"{', '.join(needing)} can't be used with a {skeleton.weapon or 'bare hand'}, and the "
                          "request forbids weapon swap"]
    if needing:
        archetypes, _ = weapon_archetypes(ctx, needing[0], swap=True)
        fits = [a for a in archetypes
                if all(usable_with(ctx.gems[n], a[0], offhand_types(ctx, a[0], a[2])) for n in needing)]
        if not fits:
            return skeleton, [f"no single weapon set holds weapons for {', '.join(needing)}"]
        weapon, sub, offhand = fits[0]
        return replace(skeleton, swap=True, swap_weapon=weapon, swap_sub=sub, swap_offhand=offhand), []
    if policy == WeaponSwap.MUST:
        return replace(skeleton, swap=True, swap_weapon=skeleton.weapon, swap_sub=skeleton.weapon_sub,
                       swap_offhand=skeleton.offhand), []
    return skeleton, []


def seed_hands(ctx: Context, items: dict[str, dict], main_slot: str, off_slot: str
               ) -> tuple[str | None, str | None, str | None]:
    """(weapon, sub-type, offhand) of a PoB code's weapon set, as skeletons write them."""
    weapon, off = items.get(main_slot), items.get(off_slot)
    kind = weapon["type"] if weapon else None
    sub = None
    if kind == "Staff":
        sub = (ctx.bases_by_name.get(weapon["base"]) or {}).get("subType") or ""
    offhand = None if off is None else off["type"] if off["type"] in ("Shield", "Focus", "Quiver") else DUAL_WIELD
    return kind, sub, offhand


def seed_skeleton(ctx: Context, facts: dict) -> tuple[Skeleton | None, list[str]]:
    """The skeleton of the request's PoB code (its facts): its ascendancy, main skill (the request's,
    if it names one the code has) and weapons, as far as the request allows them. None, with the
    reason, when the request rules the code's ascendancy or main skill out."""
    ascendancy = facts.get("ascendClassName")
    if ascendancy not in ctx.compiled.filters.ascendancies:
        return None, [f"your PoB code's ascendancy ({ascendancy or 'none'}) isn't one the request allows"]
    skill = ctx.request.main_skill or facts_main_skill(facts)
    present = {gem["name"] for group in facts["skillGroups"] if group["enabled"] for gem in group["gems"]
               if not gem["support"]}
    if skill is None or skill not in present:
        return None, [f"your PoB code has no {skill or 'main skill'} to keep as the main skill"]
    found = skill_problems(ctx, skill)
    if found:
        return None, found
    granted_by = None
    if skill not in ctx.gems:
        source = ctx.skill_source(skill)
        granted_by = f"{source[0]}:{source[1]}" if source else None
    items = {item["slot"]: item for item in facts["items"]}
    notes = []
    hands = seed_hands(ctx, items, "Weapon 1", "Weapon 2")
    unique = granted_by.split(":", 1)[1] if granted_by and granted_by.startswith("unique:") else None
    allowed, _ = weapon_archetypes(ctx, skill, unique if unique and _is_weapon(ctx, unique) else None)
    if allowed and hands not in allowed:
        same = [a for a in allowed if a[0] == hands[0]] or allowed
        notes.append(f"your PoB code's weapons ({_hands(*hands)}) don't fit the request; the search starts "
                     f"from {_hands(*same[0])} instead")
        hands = same[0]
    skeleton = Skeleton(ctx.class_of[ascendancy], ascendancy, skill, *hands, granted_by=granted_by, seeded=True)
    on_two = any(g["enabled"] and not g["fromItem"] and g.get("set1", True) is False for g in facts["skillGroups"])
    policy = WeaponSwap(ctx.request.weapon_swap)
    if on_two and "Weapon 1 Swap" in items and policy != WeaponSwap.NEVER:
        weapon, sub, offhand = seed_hands(ctx, items, "Weapon 1 Swap", "Weapon 2 Swap")
        return replace(skeleton, swap=True, swap_weapon=weapon, swap_sub=sub, swap_offhand=offhand), notes
    skeleton, found = with_swap(ctx, skeleton)
    return skeleton, notes + found


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
    swapped = []
    for skeleton in kept:
        skeleton, found = with_swap(ctx, skeleton)
        swapped.append(skeleton)
        problems += found
    return swapped, sorted(set(problems))


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
