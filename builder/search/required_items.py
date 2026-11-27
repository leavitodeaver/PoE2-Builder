"""(SEARCHD3) Required items come before the skeleton's weapons (THE USER, 27 Nov 2026: "the very first rule is 'use what I
tell you I want in the build'"; memory poe2-build-priorities: "never silently relax a constraint").

A required unique or item class that goes in a hand (a weapon, a Shield, a Focus, a Quiver) decides which skeletons are
legal: one whose hands can't hold it is never searched while another one can. In order:
1. weapon set 1 holds every required hand item (a required Wand: a Wand main hand);
2. else, when the request allows weapon swap, weapon set 2 holds what set 1 can't (the main skill needs another weapon);
3. else no legal arrangement exists, and `no_arrangement` says which rule of the game stops it.
Rings: a third required ring needs the ascendancy passive that adds a ring slot (gear.extra_ring_passive).
Before this, Gear.setup and Search.setup_seed placed each required unique into whatever skeleton the search happened to pick
and logged "fits no slot of this skeleton" when it didn't fit (THE USER's Liminal Coil beside a Sceptre + Focus,
data/results/20261127-134254-run2.json).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from builder.data.rules import OFF_HAND_ITEMS, rules as game_rules
from builder.request.schema import WeaponSwap

RING_SLOTS = 2  # Ring 1 and Ring 2; a third from a "+1 Ring Slot" passive (checked: stat enable_ring_slot_3 in game 26650308 Stats; its users in Mods.json and PassiveSkills.json: Unfurled Finger (Ritualist) alone; PoB ModCache "+1 Ring Slot" -> AdditionalRingSlot)


def _hand_types(ctx) -> set[str]:
    return set(ctx.data.weapon_types) | set(OFF_HAND_ITEMS)


def required_hand_items(ctx) -> list[tuple[str, str, str | None]]:
    """(what, item type, sub-type) for each required thing that sits in a hand: required uniques (and the request's weapon
    unique, and a unique a required skill comes from) that are weapons, Shields, Foci or Quivers, and required item classes
    of those kinds. A unique of a class also required counts once for both."""
    from builder.search.skeletons import required_holders

    hands = _hand_types(ctx)
    names = list(ctx.compiled.request.require.uniques)
    if ctx.request.weapon_unique:
        names.insert(0, ctx.request.weapon_unique)
    names += [unique for _, unique in required_holders(ctx)]
    found: list[tuple[str, str, str | None]] = []
    for name in dict.fromkeys(names):
        option = ctx.unique_option(name)
        if option is not None and option.type in hands:
            found.append((name, option.type, option.sub_type if option.type == "Staff" else None))
    held = Counter(kind for _, kind, _ in found)
    for kind in getattr(ctx.compiled.request.require, "item_classes", None) or []:
        if kind in hands and not held[kind]:
            found.append((f"a {kind}", kind, None))
        held[kind] -= 1
    return found


def _set_hands(weapon, sub, offhand) -> list[tuple[str | None, str | None]]:
    return [(weapon, sub), (offhand, None)]


def unheld(ctx, skeleton, items: list | None = None) -> list[tuple[str, str, str | None]]:
    """The required hand items this skeleton's hands can't hold (each hand holds one item; a Staff's sub-type must match)."""
    items = required_hand_items(ctx) if items is None else items
    hands = _set_hands(skeleton.weapon, skeleton.weapon_sub, skeleton.offhand)
    if skeleton.swap:
        hands += _set_hands(skeleton.swap_weapon, skeleton.swap_sub, skeleton.swap_offhand)
    free = [h for h in hands if h[0] is not None]
    left = []
    for item in items:
        _, kind, sub = item
        at = next((i for i, (t, s) in enumerate(free) if t == kind and (sub is None or s in (None, sub))), None)
        if at is None:
            left.append(item)
        else:
            free.pop(at)
    return left


def names_pair(ctx, main: str, offhand: str) -> bool:
    """Whether the required hand items include both a `main` and an `offhand` (a passive pair they name)."""
    kinds = [kind for _, kind, _ in required_hand_items(ctx)]
    if main == offhand:
        return kinds.count(main) >= 2
    return main in kinds and offhand in kinds


def rings_needed(ctx) -> int:
    count = 0
    for name in ctx.compiled.request.require.uniques:
        option = ctx.unique_option(name)
        count += bool(option is not None and option.type == "Ring")
    return count


def ring_problem(ctx, ascendancy: str) -> str | None:
    """Why this ascendancy can't wear the required rings, or None."""
    from builder.search.gear import extra_ring_passive

    need = rings_needed(ctx)
    if need <= RING_SLOTS:
        return None
    passive = extra_ring_passive(ctx, ascendancy)
    if need == RING_SLOTS + 1 and passive and passive not in ctx.compiled.filters.forbidden_passives:
        return None
    givers = ring_givers(ctx)
    return (f"{need} required rings need {need} ring slots; a character has {RING_SLOTS}, and a third comes from a passive "
            f"with '+1 Ring Slot' (the tree data has: {', '.join(givers) or 'none'})"
            + (f"; {passive} is banned by the request" if passive else f"; {ascendancy} has none"))


def ring_givers(ctx) -> list[str]:
    """'<passive> (<ascendancy>)' for every tree node whose stats hold '+1 Ring Slot' (read from the tree data)."""
    return [f"{n.get('name')} ({n.get('ascendancy') or 'main tree'})" for n in ctx.data.tree["nodes"]
            if "+1 Ring Slot" in (n.get("stats") or [])]


def _pairs(ctx) -> list[tuple[str | None, str | None]]:
    """Every (main hand, off hand) the game allows without a passive (game_rules.yaml off_hand), off hand None included,
    under the request's item-class bans."""
    rules = game_rules()
    banned = ctx.compiled.filters.forbidden_item_classes
    pairs = []
    for main in ctx.data.weapon_types:
        if main in banned or not rules.weapon_in_game(main) or main in OFF_HAND_ITEMS:
            continue
        pairs.append((main, None))
        pairs += [(main, off) for off in rules.off_hands(main) if off not in banned]
    return pairs


def swap_set(ctx, skeleton, left: list[tuple[str, str, str | None]]) -> tuple[str | None, str | None, str | None] | None:
    """(weapon, sub-type, off hand) of a weapon set 2 that holds `left`, the items set 1 can't; None when no legal pair holds
    them. A set 2 off hand beside a one-handed weapon copies set 1's off hand when the game allows it (one more slot the gear
    round fills); an off-hand-only item takes set 1's main hand when that holds it, else the first main hand that does."""
    if not left or len(left) > 2:
        return None
    kinds = [kind for _, kind, _ in left]
    sub = next((s for _, kind, s in left if kind == "Staff" and s is not None), None)
    pairs = _pairs(ctx)
    rules = game_rules()
    if len(kinds) == 2:
        for main, off in ((kinds[0], kinds[1]), (kinds[1], kinds[0])):
            if (main, off) in pairs:
                return main, sub if main == "Staff" else None, off
        return None
    kind = kinds[0]
    if kind in OFF_HAND_ITEMS:
        mains = [m for m, o in pairs if o == kind]
        if not mains:
            return None
        main = skeleton.weapon if skeleton.weapon in mains else mains[0]
        return main, skeleton.weapon_sub if main == skeleton.weapon else None, kind
    if (kind, None) not in pairs:
        return None
    off = skeleton.offhand if skeleton.offhand in rules.off_hands(kind) and (kind, skeleton.offhand) in pairs else None
    return kind, sub, off


def fit(ctx, skeleton) -> tuple[object | None, str | None]:
    """(the skeleton as it holds every required hand item, None) or (None, why it can't). Set 1 first; then weapon set 2 when
    the request allows a swap and the skeleton's set 2 is free (or already holds them)."""
    if problem := ring_problem(ctx, skeleton.ascendancy):
        return None, problem
    items = required_hand_items(ctx)
    left = unheld(ctx, skeleton, items)
    if not left:
        return skeleton, None
    policy = WeaponSwap(ctx.request.weapon_swap)
    what = ", ".join(f"{name} ({kind})" for name, kind, _ in left)
    rule = rules_text(ctx, [kind for _, kind, _ in left] + [skeleton.weapon])
    if policy == WeaponSwap.NEVER:
        return None, f"{what} can't go beside {_hands_text(skeleton)} ({rule}), and the request forbids weapon swap"
    if skeleton.swap:
        return None, (f"{what} can't go beside {_hands_text(skeleton)} ({rule}), and its weapon set 2 holds the weapons "
                      "required skills need")
    hands = swap_set(ctx, skeleton, left)
    if hands is None:
        return None, f"{what} can't go beside {_hands_text(skeleton)}, nor in one weapon set 2 ({no_pair(ctx, left)})"
    weapon, sub, offhand = hands
    return replace(skeleton, swap=True, swap_weapon=weapon, swap_sub=sub, swap_offhand=offhand), None


def _hands_text(skeleton) -> str:
    from builder.search.skeletons import _hands

    return f"the main skill's {_hands(skeleton.weapon, skeleton.weapon_sub, skeleton.offhand)}"


def where(ctx, kind: str) -> str:
    """Where the game lets an item of this type go (game_rules.yaml off_hand, patch rules)."""
    rules = game_rules()
    if kind in OFF_HAND_ITEMS:
        mains = sorted(m for m, o in _pairs(ctx) if o == kind)
        return f"a {kind} goes only in the off hand, beside a {' or '.join(mains) or 'nothing the request allows'}"
    if not rules.off_hands(kind):
        off = [p["off_hand"] for p in rules.passive_exceptions if p["main_hand"] == kind]
        passive = sorted({p["passive"] for p in rules.passive_exceptions if p["main_hand"] == kind})
        text = f"a {kind} is two-handed: the off hand holds nothing beside it"
        if passive:
            text += f" except with {' / '.join(passive)} ({', '.join(sorted(set(off)))})"
        return text
    beside = [m for m in ctx.data.weapon_types if kind in rules.off_hands(m)]
    if beside:
        return f"a {kind} goes in the main hand, or in the off hand beside a {' or '.join(sorted(beside))}"
    return f"a {kind} goes only in the main hand (never in the off hand)"


def rules_text(ctx, kinds) -> str:
    """The game's placement rule for each of these item types (deduplicated), with the patch the rules are for."""
    return "; ".join(dict.fromkeys(where(ctx, k) for k in kinds if k)) + f" (patch {game_rules().patch} rules)"


def no_pair(ctx, left) -> str:
    """Why no one weapon set holds these items, from the game's rules."""
    if len(left) > 2:
        return f"one weapon set has two hands, so it can't hold {len(left)} items"
    return rules_text(ctx, [kind for _, kind, _ in left])


def no_arrangement(ctx, skeletons: list, items: list) -> str:
    """The problem a run reports when no legal skeleton holds every required item: the items, the setups the main skill
    allows, the game's rule for each item type involved, a banned passive that would allow it, and whether weapon set 2
    would hold the rest (forbidden by the request) or can't either."""
    from builder.search.skeletons import _hands

    rules = game_rules()
    kinds = list(dict.fromkeys([kind for _, kind, _ in items] + [s.weapon for s in skeletons if s.weapon]))
    setups = " or ".join(dict.fromkeys(_hands(s.weapon, s.weapon_sub, s.offhand) for s in skeletons))
    text = (f"no legal weapon setup holds every required item ({', '.join(f'{n} ({k})' for n, k, _ in items)}) with "
            f"{skeletons[0].main_skill}, which the game and the request allow on {setups}: {rules_text(ctx, kinds)}")
    banned = sorted({p["passive"] for p in rules.passive_exceptions if p["main_hand"] in kinds and p["off_hand"] in kinds
                     and p["passive"] in ctx.compiled.filters.forbidden_passives})
    if banned:
        text += f"; {', '.join(banned)}, which would allow it, is banned by the request"
    if any(ring_problem(ctx, s.ascendancy) for s in skeletons):
        text += "; " + ring_problem(ctx, skeletons[0].ascendancy)
    swap = [s for s in skeletons if not s.swap and swap_set(ctx, s, unheld(ctx, s, items)) is not None]
    if WeaponSwap(ctx.request.weapon_swap) == WeaponSwap.NEVER:
        text += ("; weapon set 2 would hold the rest, but the request forbids weapon swap" if swap
                 else "; the request forbids weapon swap")
    else:
        text += "; weapon set 2 can't hold the rest either"
    return text + ". The closest build is delivered: it holds as many of them as one setup can"


def choose(ctx, skeletons: list) -> tuple[list, list[str]]:
    """The skeletons that hold every required item (set 1 first, else with set 2), or, when none can, those that hold
    the most, with the game rule that stops the rest (no_arrangement)."""
    items = required_hand_items(ctx)
    if not items and rings_needed(ctx) <= RING_SLOTS:
        return skeletons, []
    first = [s for s in skeletons if not unheld(ctx, s, items) and not ring_problem(ctx, s.ascendancy)]
    if first:
        return first, []
    fitted = [found for found, _ in (fit(ctx, s) for s in skeletons) if found is not None]
    if fitted:
        return list({s.label: s for s in fitted}.values()), []
    if not skeletons:
        return skeletons, []
    fewest = min(len(unheld(ctx, s, items)) for s in skeletons)
    return ([s for s in skeletons if len(unheld(ctx, s, items)) == fewest],
            [no_arrangement(ctx, skeletons, items)])


def carries(gem: dict | None, mechanic: str) -> bool:
    """Whether a gem carries a mechanic: one of its tags or PoB skill types, case-insensitive (the same reading as the
    request's banned mechanics, constraints.compile_request)."""
    return bool(gem) and mechanic.lower() in {t.lower() for t in (gem.get("tags") or []) + (gem.get("skillTypes") or [])}


def mechanics_missing(ctx, groups: list[dict]) -> list[str]:
    """The required mechanics no enabled gem of the build carries."""
    used = [ctx.gems.get(g["name"]) or ctx.all_gems.get(g["name"]) for group in groups if group.get("enabled", True)
            for g in group["gems"] if g.get("enabled", True)]
    return [m for m in ctx.compiled.request.require.mechanics if not any(carries(gem, m) for gem in used)]


def mechanic_gems(ctx, mechanic: str) -> tuple[list[str], list[str]]:
    """(active skills, supports) a level-N character may socket that carry the mechanic, allowed by the request."""
    actives, supports = [], []
    for name, gem in ctx.gems.items():
        if not carries(gem, mechanic) or not ctx.allowed_gem(name):
            continue
        (supports if gem["support"] else actives).append(name)
    return sorted(actives), sorted(supports)


def no_slot(gear, name: str, option) -> str:
    """Why a required unique has no slot in this skeleton's gear (Gear.slot_for found none), in the game's terms."""
    if option is None:
        return f"required unique {name} isn't a unique the game data has a base for"
    kind = option.type
    slots = [slot for slot, spec in gear.slots.items() if kind in spec.types]
    holders = [f"{gear.build.equipped[s].name} ({s})" for s in slots
               if s in gear.build.locked_slots and gear.build.equipped.get(s) is not None]
    if kind == "Jewel":
        return f"required unique {name} is a Jewel: it goes in a passive-tree socket (Search.place_required_jewel)"
    if kind == "Ring":
        return (f"required unique {name} needs a ring slot: {', '.join(holders) or 'other items'} hold this skeleton's "
                f"{len(slots)}; a third comes from a passive with '+1 Ring Slot' (the tree data has: "
                f"{', '.join(ring_givers(gear.ctx)) or 'none'})")
    if slots and holders:
        return (f"required unique {name} needs the {kind} slot, which {', '.join(holders)} holds (required or yours): a "
                f"character wears {len(slots)} {kind}{'s' if len(slots) > 1 else ''}")
    if kind in _hand_types(gear.ctx):
        hands = " + ".join(f"{s}: {'/'.join(spec.types)}" for s, spec in gear.slots.items() if s.startswith("Weapon"))
        return (f"required unique {name} ({kind}) can't go in this skeleton's hands ({hands or 'none'}): "
                f"{rules_text(gear.ctx, [kind])}; the run's other skeletons hold it where the game allows")
    return f"required unique {name} ({kind}) fits none of this skeleton's slots"
