"""The boss's hits you can't evade, block or dodge-roll (MECHC5; FIX_QUEUE "RESEARCH15's findings" #71, RESEARCH16 #78).

The rule (HIGH): patch 0.3.0, "Player Evasion now works on all hits except boss skills that have a red flash"; KeywordPopups
"Block": "Some Skills used by Bosses cannot be blocked. These are indicated by a red glow and audio cue during the windup".
In the game files these skills carry `base_skill_cannot_be_avoided_by_dodge_roll_or_evaded_or_blocked` (no dodge roll, no
evasion, no block) or `base_skill_cannot_be_avoided_by_dodge_roll_or_blocked` (evasion still applies), and Zarokh's
DeathTouch also has `global_always_hit`. (checked: RESEARCH15's scan of every MonsterVariety, granted effect and stat set in
each boss's folder, scratch/research15/unavoid.py, game 26476662; second source: the 0.3.0 patch note quoted on the official
forum, thread 3829081, and KeywordPopups Block; research/unavoidable-pinnacle-hits.md.)

The share of the boss's hits that are flagged: our tables have no use rates per boss skill, so the share is the flagged
skills' count among the boss's damaging skills (LOW stand-in: these are often its biggest hits, so the damage share is
probably higher). The non-boss attackers of each fight carry no flag (RESEARCH15's adds.py).

Deflection: no game text says whether a red-flash hit can be Deflected (#78, LOW-MEDIUM that it can): the recovery check
keeps Deflection against every hit, labelled LOW, and reports the figure without it (objective.boss_recovery).

MECH-A's boss presets (builder/search/bosses.py, master) may carry the shares per preset: this module reads a Boss's
`unavoidable` (share of its hits with no evade, block or dodge roll) and `unblockable` (share that can be evaded but not
blocked or dodge-rolled) when they're there, and its own table below otherwise.
"""
from __future__ import annotations



def number(value: object) -> float:
    if value in ("inf", "-inf"):  # (PoB's unbounded maximum hit taken comes through the bridge as a string)
        return float(value)
    return float(value) if isinstance(value, (int, float)) else 0.0

DEFAULT_BOSS = "Arbiter of Ash"
# boss: (skills with no evade/block/dodge roll, skills with evasion but no block/dodge roll, damaging skills, which)
RED_FLASH: dict[str, tuple[int, int, int, str]] = {
    "Arbiter of Ash": (7, 0, 20, "Chasm Burst, Offscreen Slam, Big Chasm Slash, Bullet Hell (x3), Flame Seed nuke"),
    "Arbiter of Divinity": (4, 0, 22, "Storm Blink Bolt, Extendo Spear Combo Slam, Spear Toss impacts (x2)"),
    "Xesht, We That Are One": (4, 0, 11, "Multi Flick, Pistol Flick wave, Hand Slams (left, right)"),
    "The King in the Mists": (4, 0, 16, "Vine Slam, Punishment, Bug Bomb (phase 3) and its impact"),
    "Zarokh, the Temporal": (1, 0, 22, "Apparition Death Touch (also always hits)"),
    "The Raven Trickster": (2, 0, 35, "Phase Change Blast, Volatile Flame Blast (Lightning)"),
    "The Bodach": (3, 1, 20, "phase 2 Meteor slams (x3); Head Slam can still be evaded"),
    "Olroth, Origin of the Fall": (4, 0, 19, "Ground Slam and its two Aftershocks, Throw Out (Maims)"),
    "The Aberration": (1, 0, 9, "Spider Mortar"),
    "Vessel of Kulemak": (0, 2, 9, "Teleport Slam and its trigger can still be evaded"),
    "Atziri, the Red Queen": (0, 0, 25, "none in phase 1 (Flesh Bubble Explosion is phase 2)"),
    "Atziri, the Fell Serpent": (1, 0, 25, "Flesh Bubble Explosion"),
}
CONFIDENCE = ("which skills: HIGH (game files); their share of the boss's hits: LOW (flagged skills / damaging skills, no use "
              "rates in our tables)")


def boss_name(stats: dict) -> str:
    if name := stats.get("_boss"):
        return name
    try:
        from builder.search import bosses  # MECH-A's presets (master; not on every branch yet)
        return bosses.DEFAULT
    except ImportError:
        return DEFAULT_BOSS


def shares(stats: dict) -> tuple[float, float]:
    """(share of the boss's hits with no evade / block, share with evade but no block)."""
    name = boss_name(stats)
    try:
        from builder.search import bosses
        boss = bosses.PRESETS.get(name)
    except ImportError:
        boss = None
    if boss is not None and getattr(boss, "unavoidable", None) is not None:
        return float(boss.unavoidable), float(getattr(boss, "unblockable", 0.0) or 0.0)
    flagged, evadable, total, _ = RED_FLASH.get(name, RED_FLASH[DEFAULT_BOSS])
    return flagged / total, evadable / total


def evade_block(stats: dict) -> tuple[float, float]:
    return (min(max(number(stats.get("EvadeChance")), 0.0), 100) / 100,
            min(max(number(stats.get("EffectiveBlockChance")), 0.0), 100) / 100)


def land_share(stats: dict) -> float:
    """The share of the boss's hits that land: red-flash hits always do; the rest are evaded and blocked as PoB rolls them."""
    u, e = shares(stats)
    evade, block = evade_block(stats)
    return u + e * (1 - evade) + (1 - u - e) * (1 - evade) * (1 - block)


def block_share(stats: dict, evaded_first: bool = True) -> float:
    """The share of the boss's hits you Block: only the blockable ones (after evasion when `evaded_first`)."""
    u, e = shares(stats)
    evade, block = evade_block(stats)
    return (1 - u - e) * (1 - evade if evaded_first else 1.0) * block


def landing_hits(stats: dict) -> float:
    """The boss's hits per second that land (one every enemySkillTime, PoB's boss preset)."""
    return land_share(stats) / (number(stats.get("enemySkillTime")) or 0.7)


def note(stats: dict) -> dict:
    name = boss_name(stats)
    u, e = shares(stats)
    which = RED_FLASH.get(name, RED_FLASH[DEFAULT_BOSS])[3]
    return {"label": "Boss hits you can't evade or block (red flash)",
            "value": f"{u:.0%} of {name}'s hits" + (f", and {e:.0%} that can be evaded but not blocked" if e else ""),
            "basis": f"{which}: no evade or block credit against them (0.3.0 patch note; game files' skill flags). "
                     "Deflection still counted against them (LOW: no game text either way)",
            "confidence": CONFIDENCE}


# ---------------------------------------------------------------------------------------------------------------------
# How hard each boss hits, per damage type (MECHC6; the manager's ruling ~11:02, FIX_QUEUE "RESEARCH14's findings";
# research/boss-elemental-chaos-level-multiplier.md, boss-resistances-damage-game-tables.md, pinnacle-ailments-on-player.md)
#
# One boss hit of a damage type = the level's base monster damage x the skill's own multiplier x the variety's damage
# multiplier (raw) x unique rarity's 35% less x the monster level's more for that type. Every factor is "final" (more),
# so they multiply. PoB's Pinnacle default instead puts 965 of each of Physical / Fire / Cold / Lightning and 386 Chaos
# into one hit (ConfigOptions.lua 2084-2089: Damage[82] x 1.5 x 8/4.4), with no variety, rarity or level factor: about
# right for a Physical hit, ~4.3x too soft for an elemental one and ~5.4x for Chaos (falsely elevated survival).
#
# Three readings, each reported:
# - "headline": every factor (Arbiter of Ash: Physical x2.925, Fire / Cold / Lightning x11.70, Chaos x5.85);
# - "harsh": without the 0.65 (x4.50 / x18.0 / x9.0): no second source says MonsterUnique5 applies to pinnacle bosses;
#   a build that survives only at the headline is flagged "passes only if pinnacle bosses get the unique 35% less";
# - "low": the level's more only on damage converted from Physical (PoE1's rule; the boss's base elemental spells get
#   none), the low bound: every type x2.925 for the Arbiter.
# ---------------------------------------------------------------------------------------------------------------------
LEVEL = 82  # PoB's Pinnacle minimum and the presets' level (MEDIUM: poe2wiki 80-82 by tier)
BASE_DAMAGE = 353.67  # DefaultMonsterStats.Damage at level 82 (game files; PoB's monsterDamageTable[82]): HIGH
SKILL_MULT = 1.0  # the boss skill's own multiplier: 0.8-1.2 (average 1.0) for Fireball Impact, x1.3 on slams. LOW stand-in
UNIQUE_LESS = 0.65  # MonsterUnique5 monster_rarity_damage_+%_final -35 (RESEARCH13): MEDIUM-HIGH that it applies
LEVEL_MORE = {"elemental": 4.0, "chaos": 2.0}  # MoreElementalDamage 300 / MoreChaosDamage 100 at 80+ (RESEARCH14): MEDIUM
TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
CONFIDENCE_HITS = {"raw": "HIGH (MonsterVarieties.DamageMultiplier; poe2db prints it)",
                   "unique": "MEDIUM-HIGH (MonsterUnique5; no second source that pinnacles get it)",
                   "level": "MEDIUM (hidden *_from_monster_level stats; all elemental vs converted-only unsettled)",
                   "skill": "LOW (the skill's own multiplier: 1.0 stands in for the average boss skill)",
                   "mix": "LOW (each type's share of hits = its share of the boss's damaging skills; no use rates)"}
# boss: (raw DamageMultiplier, damaging skills per type). Multipliers: RESEARCH13's MonsterVarieties table (HIGH).
# Skill counts per type: RESEARCH13's pinnacle-ailments-on-player.md (game stat sets); a count of 1 where the note names
# a type without a count (Xesht's corpses, the King's two types): LOW. A type a boss has no count for gets 0 in PoB
# (so its "take <Type> damage from a Hit" charm triggers count none: flasks.Charges.boss_types). (checked: RESEARCH13 read
# every damaging skill's stat set in each boss's monster folder, game 26476662; AUDIT9 A9-1 / RESEARCH15 added Xesht's
# two corpses, the one miss found; second source: poe2db's boss pages, e.g. Zarokh's "Physical Damage" on every skill.
# Damage over time and grounds are separate, item 2 of MECHC6: boss_hits sizes hits only.)
HIT_TABLE: dict[str, tuple[float, dict[str, int]]] = {
    "Arbiter of Ash": (4.50, {"Fire": 17, "Physical": 7}),
    "Arbiter of Divinity": (3.20, {"Lightning": 17, "Physical": 7}),
    "Xesht, We That Are One": (3.00, {"Physical": 5, "Fire": 2, "Lightning": 1, "Cold": 1}),
    "The King in the Mists": (1.88, {"Physical": 1, "Chaos": 1}),
    "Zarokh, the Temporal": (1.50, {"Physical": 1}),  # its "Lightning" skills deal Physical (game stat sets, poe2db)
    "The Raven Trickster": (4.50, {"Cold": 10, "Fire": 8, "Lightning": 6, "Physical": 7, "Chaos": 2}),
    "The Bodach": (3.20, {"Physical": 13, "Chaos": 1}),
    "Olroth, Origin of the Fall": (3.00, {"Cold": 14, "Physical": 13}),
    "The Aberration": (3.20, {"Physical": 6, "Lightning": 2, "Cold": 2}),
    "Vessel of Kulemak": (5.32, {"Lightning": 3, "Physical": 3, "Cold": 2}),
    "Atziri, the Red Queen": (3.80, {"Physical": 9, "Fire": 10, "Lightning": 9}),
    "Atziri, the Fell Serpent": (4.50, {"Physical": 5, "Fire": 4, "Lightning": 2}),
    "The Trialmaster": (3.72, {"Physical": 5, "Fire": 1, "Cold": 1, "Lightning": 1}),
}
READINGS = ("headline", "harsh", "low")


def type_factor(kind: str, reading: str = "headline") -> float:
    """The level's more for a damage type under a reading (none on Physical; none at all in the converted-only one)."""
    if reading == "low" or kind == "Physical":
        return 1.0
    return LEVEL_MORE["chaos"] if kind == "Chaos" else LEVEL_MORE["elemental"]


def hit_table(name: str | None) -> tuple[float, dict[str, int]]:
    return HIT_TABLE.get(name or DEFAULT_BOSS, HIT_TABLE[DEFAULT_BOSS])


def hit_sizes(name: str | None = None, reading: str = "headline") -> dict[str, float]:
    """One hit of each damage type the boss deals, before your mitigation (0 for the types it doesn't deal)."""
    raw, counts = hit_table(name)
    unique = 1.0 if reading == "harsh" else UNIQUE_LESS
    return {t: (BASE_DAMAGE * SKILL_MULT * raw * unique * type_factor(t, reading) if counts.get(t) else 0.0)
            for t in TYPES}


def type_shares(name: str | None = None) -> dict[str, float]:
    """Each damage type's share of the boss's hits (LOW: its share of the boss's damaging skills)."""
    counts = hit_table(name)[1]
    total = sum(counts.values())
    return {t: counts.get(t, 0) / total for t in TYPES}


def config_vars(name: str | None = None) -> dict[str, int]:
    """PoB config inputs for the boss's hits: each type it deals at its full headline size, the others 0. PoB adds the
    five into one hit; consumers that sum them weight each type by its share (type_taken), so Armour still meets a
    full-sized Physical hit."""
    return {f"enemy{t}Damage": round(v) for t, v in hit_sizes(name).items()}


def type_taken(stats: dict) -> float:
    """Damage one average landed boss hit deals after your mitigation: PoB's {Type}TakenHit (each type at its full hit
    size, config_vars) weighted by the type's share of the boss's hits."""
    weights = type_shares(boss_name(stats))
    return sum(max(number(stats.get(f"{t}TakenHit")), 0.0) * weights[t] for t in TYPES)


def survival(stats: dict) -> dict:
    """Can the build take one hit of each type the boss deals? Per reading, the types it can't (MaximumHitTaken, with
    Darkness once objective.prepare has run, against hit_sizes)."""
    name = boss_name(stats)
    fails = {r: [t for t, hit in hit_sizes(name, r).items()
                 if hit > 0 and number(stats.get(f"{t}MaximumHitTaken")) < hit] for r in READINGS}
    return {"boss": name, "fails": fails, "headline_only": not fails["headline"] and bool(fails["harsh"])}


def survival_text(stats: dict) -> tuple[str, str, bool | None, str]:
    """(requested, delivered, ok, note) for the report's "Survives the boss's hits" row: reported, not enforced (ok None:
    whether it becomes a floor is the user's call)."""
    s = survival(stats)
    head, harsh = hit_sizes(s["boss"]), hit_sizes(s["boss"], "harsh")
    want = ", ".join(f"{t} {head[t]:,.0f}" for t in TYPES if head[t] > 0)
    got = ", ".join(f"{t} {number(stats.get(f'{t}MaximumHitTaken')):,.0f}" for t in TYPES if head[t] > 0)
    if not s["fails"]["headline"] and not s["fails"]["harsh"]:
        verdict = "survives every reading"
    elif s["headline_only"]:
        verdict = ("passes only if pinnacle bosses get the unique 35% less (UNVERIFIED in game): fails "
                   + ", ".join(f"{t} {harsh[t]:,.0f}" for t in s["fails"]["harsh"]) + " without it")
    else:
        verdict = "one hit kills: " + ", ".join(s["fails"]["headline"]) + (
            " (even at the low bound)" if s["fails"]["low"] else " (survives the converted-only low bound)")
    note = (f"{s['boss']}: level {LEVEL} base {BASE_DAMAGE:g} x skill {SKILL_MULT:g} x raw {hit_table(s['boss'])[0]:g} "
            f"x unique {UNIQUE_LESS:g} x the level's more (Elemental x{LEVEL_MORE['elemental']:g}, Chaos "
            f"x{LEVEL_MORE['chaos']:g}); " + "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items()))
    return f"one hit of each type: {want}", f"max hit {got}: {verdict}", None, note


def hits_note(name: str | None = None) -> dict:
    """The report's labelled row for the boss's hit sizes."""
    name = name or DEFAULT_BOSS
    rows = {r: hit_sizes(name, r) for r in READINGS}
    weights = type_shares(name)
    value = "; ".join(f"{t} {rows['headline'][t]:,.0f} ({weights[t]:.0%} of hits; without the 0.65 {rows['harsh'][t]:,.0f}, "
                      f"low bound {rows['low'][t]:,.0f})" for t in TYPES if rows["headline"][t] > 0)
    return {"label": f"Boss hit sizes: {name}", "value": value,
            "basis": "the game's monster tables, not PoB's Pinnacle default (965 per type and 386 Chaos, all in one hit): "
                     "level base damage x skill x the boss's damage multiplier x unique 35% less x the level's more for "
                     "Elemental (x4) and Chaos (x2)",
            "confidence": "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items())}
