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

import math
import re



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


# The default worst-case pinnacle (MECHC12; MECH-D's MECHD5, bosses.WORST: the user's decision, 12 Nov 2026, "invent a
# pinnacle boss that ... takes the highest stats of each"). Its rows in this file's tables, made from bosses.py's own
# sources the first time a reader asks (bosses.py reads this file's tables while it loads, so they can't be made at
# import). Before, every table here read the Arbiter of Ash's row for it (two damage types, Fire and Physical), while PoB
# got the worst case's five through config_vars:
# - HIT_TABLE: the type counts pooled over the 12 real presets' damaging skills (bosses.WORST_HIT_COUNTS: every type
#   dealt, LOW as each boss's own mix); hit_sizes gives each type at the biggest any real preset deals
#   (bosses.WORST_HIT_FROM; the table's raw multiplier is that of the hardest-hitting source, for the note only).
# - RED_FLASH: bosses.WORST's unavoidable / unblockable shares (the highest real preset's of each, MECHD5) over the pooled
#   damaging skills, naming the presets they come from (shares() reads the preset's own floats).
# - IGNITE_INCREASE: the real preset with the most increased Ignite chance per Fire hit (Atziri, the Red Queen: 80 over
#   her 10 Fire skills, 8% a hit, since MECHD7 took it off the Fell Serpent), as an increase over the pooled Fire skills
#   that averages to that.
# - Corrupted Blood (corrupted_blood): the real preset with the largest share of hits that leave it (the Fell Serpent:
#   one of her 11 damaging skills, 3 s).
_worst_done = False


def _worst() -> str | None:
    """bosses.WORST, its rows added to the tables here on the first call (None while bosses.py is still loading)."""
    global _worst_done
    try:
        from builder.search import bosses
        name, sources, counts = bosses.WORST, bosses.WORST_HIT_FROM, bosses.WORST_HIT_COUNTS
        boss, real = bosses.PRESETS[bosses.WORST], bosses.REAL
    except (ImportError, AttributeError, KeyError):
        return None
    if _worst_done:
        return name
    HIT_TABLE[name] = (max(HIT_TABLE[n][0] for n in set(sources.values())), dict(counts))
    total = sum(RED_FLASH[n][2] for n in real)
    una = max(real, key=lambda n: RED_FLASH[n][0] / RED_FLASH[n][2])
    eva = max(real, key=lambda n: RED_FLASH[n][1] / RED_FLASH[n][2])
    RED_FLASH[name] = (boss.unavoidable * total, (boss.unblockable or 0.0) * total, total,
                       f"the highest share of any real preset: {boss.unavoidable:.0%} with no evade or block roll, as "
                       f"{una}'s ({RED_FLASH[una][3]})" + (f"; {boss.unblockable:.0%} evadable but not blockable, as "
                                                            f"{eva}'s ({RED_FLASH[eva][3]})" if boss.unblockable else ""))
    per_hit = {n: sum(IGNITE_INCREASE[n]) / HIT_TABLE[n][1]["Fire"] for n in IGNITE_INCREASE
               if n in real and HIT_TABLE[n][1].get("Fire")}
    if per_hit:
        IGNITE_INCREASE[name] = (max(per_hit.values()) * counts.get("Fire", 0),)
    _worst_done = True
    return name


def shares(stats: dict) -> tuple[float, float]:
    """(share of the boss's hits with no evade / block, share with evade but no block)."""
    name = boss_name(stats)
    _worst()
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
    u, e = shares(stats)  # (adds the worst case's row: _worst)
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
UNIQUE_LESS = 0.65  # MonsterUnique5 monster_rarity_damage_+%_final -35 (RESEARCH13): MEDIUM-HIGH that it applies
LEVEL_MORE = {"elemental": 4.0, "chaos": 2.0}  # MoreElementalDamage 300 / MoreChaosDamage 100 at 80+ (RESEARCH14): MEDIUM
TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
CONFIDENCE_HITS = {"raw": "HIGH (each skill's own size from the game files: spells by their own effectiveness, which "
                          "reproduces poe2db's printed damage; attacks by the level's damage x the boss's multiplier x the "
                          "skill's own BaseMultiplier; RESEARCH26 boss-damage-scale-r26.md)",
                   "variety": "MEDIUM (the boss's damage multiplier on attacks only, not spells: PoB's boss exporter and "
                              "poe2db)",
                   "unique": "MEDIUM-HIGH (MonsterUnique5; no second source that pinnacles get it)",
                   "level": "MEDIUM (hidden *_from_monster_level stats with no wording; all elemental vs converted-only "
                            "unsettled: the low bound gives base elemental spells none)",
                   "biggest": "LOW-MEDIUM (the biggest ordinary hit of each type the boss deals; red-flash nukes made to "
                              "be walked out of have their own row; a hit of two types counts each part as its type's hit)",
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


# Each boss skill's own hit (MECHC31; RESEARCH26 R26-1, boss-damage-scale-r26.md; the manager 15 Nov 22:15 "BOSS HIT
# SIZES"). Before, every hit was 353.67 x 1.0 x the boss's damage multiplier: wrong for spells (a monster spell scales by
# its own effectiveness, not the level's monster damage, and gets no variety multiplier) and too low for attacks (each
# has its own x1.1-2.0). builder/data/boss_skill_hits.json holds every damaging skill of each preset's own monsters,
# made from the game files by scratch/mechc31/bosshits.py (game 26476662, level 82), each hit split by type, raw (before
# the unique 0.65 and the level's more), with its origin:
# - "spell": 3.885209 (SkillDamageBaseEffectiveness) x BaseEffectiveness x (1 + IncrementalEffectiveness (L-1)) x
#   (1 + DamageIncrementalEffectiveness)^(L-1) x the spell_min/max_base_<type>_damage multipliers (HIGH: reproduces
#   poe2db's printed damage to the decimal);
# - "attack": Damage[82] 353.67 x the monster's DamageMultiplier x (1 + BaseMultiplier / 10000), Physical, before the
#   skill's conversions; "converted": the part an attack converts or adds as another type.
# A red-flash hit over NUKE_OVER x the preset's biggest ordinary hit is a nuke (the Arbiter of Ash's Flame Seed and
# Bullet Hell High, 28,825 Fire raw; the Fell Serpent's Flesh Bubble Explosion; the King's Bug Bomb impact): made to be
# walked out of, not tanked. hit_sizes leaves them out and nuke_hits gives them their own row (LOW: the 3x line is ours).
# The readings per part: headline x0.65 x the level's more on every Elemental (x4) / Chaos (x2) part; harsh without the
# 0.65; low with the level's more only on parts converted from Physical (base elemental spells none, PoE1's rule).
# Types the RESEARCH13 count (HIT_TABLE) gives no skill stay at 0, so the hit sizes and the type shares agree (the
# King's toads' Fire: not counted, LOW).
NUKE_OVER = 3.0
_SKILL_HITS: dict | None = None


def skill_hits(name: str | None = None, adds: bool = False) -> list[dict]:
    """The preset's damaging skills from builder/data/boss_skill_hits.json ([] for a name it doesn't hold); with `adds`,
    instead its adds' hits (rows marked "add": the Royal Acolytes' explosion, MECHC34), which aren't the boss's own."""
    global _SKILL_HITS
    if _SKILL_HITS is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "boss_skill_hits.json"
        _SKILL_HITS = json.loads(path.read_text(encoding="utf-8"))["presets"]
    return [r for r in _SKILL_HITS.get(name or DEFAULT_BOSS, []) if bool(r.get("add")) == adds]


# Adds that hit you (MECHC34; RESEARCH26 R26-29 (a), routed to MECH-C by the manager 16 Nov ~00:30 while MECH-D is
# paused): Atziri phase 1's Royal Acolytes explode on reaching you, a Physical hit (boss_skill_hits.json's "add" row).
# The headline kills them before they reach you (bosses.PRESETS' kill source, which gives the on-kill gains), so the
# explosion isn't in the hit sizes, the averages PoB gets or the cadence; a row says what each one that reaches you does.
def add_hits_text(name: str) -> str:
    """The hits_note clause for a preset's adds' hits ("" without any)."""
    parts = []
    for row in skill_hits(name, adds=True):
        size = sum(raw for raw, _ in row["hit"].values())  # (a Normal monster: no unique 35% less; Physical: no level more)
        kinds = "/".join(row["hit"])
        parts.append(f"{row['id']} {size:,.0f} {kinds} raw, one per {row.get('every', 0):g} s at least, only from those "
                     f"that reach you (the headline kills them first; {row.get('note', '')})")
    return ("; adds: " + "; ".join(parts)) if parts else ""


def part_factor(kind: str, origin: str, reading: str = "headline") -> float:
    """One part of a boss hit, raw to its size under a reading: the unique 0.65 (not in harsh) x the level's more."""
    unique = 1.0 if reading == "harsh" else UNIQUE_LESS
    if kind == "Physical" or (reading == "low" and origin != "converted"):
        return unique
    return unique * (LEVEL_MORE["chaos"] if kind == "Chaos" else LEVEL_MORE["elemental"])


def type_factor(kind: str, reading: str = "headline") -> float:
    """The level's more for a damage type under a reading (none on Physical; none at all in the converted-only one)."""
    if reading == "low" or kind == "Physical":
        return 1.0
    return LEVEL_MORE["chaos"] if kind == "Chaos" else LEVEL_MORE["elemental"]


def hit_table(name: str | None) -> tuple[float, dict[str, int]]:
    _worst()
    return HIT_TABLE.get(name or DEFAULT_BOSS, HIT_TABLE[DEFAULT_BOSS])


def _biggest(name: str, reading: str, nukes: bool) -> dict[str, tuple[float, str]]:
    """{type: (the biggest part of that type under the reading, the skill)} over the preset's ordinary hits (or nukes)."""
    out: dict[str, tuple[float, str]] = {}
    for row in skill_hits(name):
        if bool(row.get("nuke")) != nukes:
            continue
        for kind, (raw, origin) in row["hit"].items():
            size = raw * part_factor(kind, origin, reading)
            if size > out.get(kind, (0.0, ""))[0]:
                out[kind] = (size, row["id"])
    return out


def hit_sizes(name: str | None = None, reading: str = "headline") -> dict[str, float]:
    """The biggest ordinary hit of each damage type the boss deals, before your mitigation (0 for the types it doesn't
    deal; red-flash nukes not counted: nuke_hits). The worst-case pinnacle: each type at the biggest any real preset
    deals (bosses.WORST_HIT_FROM, picked by these sizes)."""
    if name is not None and name == _worst():
        from builder.search import bosses
        return {t: (hit_sizes(bosses.WORST_HIT_FROM[t], reading)[t] if t in bosses.WORST_HIT_FROM else 0.0) for t in TYPES}
    name = name or DEFAULT_BOSS
    counts = hit_table(name)[1]
    own = _biggest(name, reading, nukes=False)
    out = {}
    for t in TYPES:
        if not counts.get(t):
            out[t] = 0.0
        elif t in own:
            out[t] = own[t][0]
        else:  # (a counted type with no sized skill: the old one-size base, labelled in hits_note)
            out[t] = BASE_DAMAGE * hit_table(name)[0] * part_factor(t, "converted", reading)
    return out


def mean_hits(name: str | None = None, reading: str = "headline") -> dict[str, float]:
    """Each damage type's AVERAGE ordinary hit (each of the boss's skills that deals the type counted once: LOW, no use
    rates; nukes left out: walked out of), what PoB gets as the boss's hit (config_vars), so readings of one average
    hit (damage taken, Heavy Stun and ailment buildup, flasks' Life lost) don't take every hit at the biggest. The
    worst-case pinnacle: the highest average of any real preset per type."""
    if name is not None and name == _worst():
        from builder.search import bosses
        return {t: max(mean_hits(n, reading)[t] for n in bosses.REAL) for t in TYPES}
    name = name or DEFAULT_BOSS
    counts, big = hit_table(name)[1], hit_sizes(name, reading)
    parts: dict[str, list[float]] = {}
    for row in skill_hits(name):
        if row.get("nuke"):
            continue
        for kind, (raw, origin) in row["hit"].items():
            parts.setdefault(kind, []).append(raw * part_factor(kind, origin, reading))
    return {t: (sum(parts[t]) / len(parts[t]) if counts.get(t) and parts.get(t) else big[t]) for t in TYPES}


def biggest_skills(name: str | None = None) -> dict[str, str]:
    """{type: the skill whose hit hit_sizes gives (headline)} for a real preset."""
    return {t: v[1] for t, v in _biggest(name or DEFAULT_BOSS, "headline", nukes=False).items()}


def nuke_hits(name: str | None = None, reading: str = "headline") -> dict[str, tuple[float, str]]:
    """{type: (size, skill)} of the boss's red-flash nukes (the worst case: the biggest any real preset has)."""
    if name is not None and name == _worst():
        from builder.search import bosses
        out: dict[str, tuple[float, str]] = {}
        for n in bosses.REAL:
            for t, (size, skill) in nuke_hits(n, reading).items():
                if size > out.get(t, (0.0, ""))[0]:
                    out[t] = (size, f"{n}'s {skill}")
        return out
    return _biggest(name or DEFAULT_BOSS, reading, nukes=True)


def type_shares(name: str | None = None) -> dict[str, float]:
    """Each damage type's share of the boss's hits (LOW: its share of the boss's damaging skills)."""
    counts = hit_table(name)[1]
    total = sum(counts.values())
    return {t: counts.get(t, 0) / total for t in TYPES}


ELEMENTS = ("Fire", "Cold", "Lightning")
ADAPTATIONS_HELD = 3  # (KeywordPopups Adaptation, game 26650308: "you can have 3 Adaptations at a time")


def elemental_shares(name: str | None = None) -> dict[str, float]:
    """(MECHC45, for COV-A's Reactive Growth entry; the manager's 08:30 from COV47) Each element's share of the boss's
    ELEMENTAL hits (type_shares over Fire / Cold / Lightning only; all 0 for a boss with no elemental hit). LOW, as
    type_shares: a type's share of the boss's damaging skills, no use rates; a hit of two elements counts for each."""
    shares = type_shares(name)
    total = sum(shares[e] for e in ELEMENTS)
    return {e: (shares[e] / total if total > 0 else 0.0) for e in ELEMENTS}


def adaptations_held(name: str | None = None, held: int = ADAPTATIONS_HELD) -> dict[str, float]:
    """(MECHC45) Expected matching Adaptations when a hit of each element lands: KeywordPopups Adaptation ("gained by
    taking Elemental Damage from Hits ... take less damage of that Type from subsequent Hits ... 3 Adaptations at a time
    ... do not have a duration"; Reactive Growth: "Adapt to the highest Elemental Damage Type of each Hit you take"), so
    the last `held` elemental hits set them: held x the element's share of the boss's elemental hits (elemental_shares;
    the hits taken as a random order, MEDIUM-LOW; a physical-only boss gives none). For "N% less Damage taken of each
    Elemental Damage Type per matching Adaptation": N x this, per element."""
    return {e: held * s for e, s in elemental_shares(name).items()}


def config_vars(name: str | None = None) -> dict[str, int]:
    """PoB config inputs for the boss's hits: each type it deals at its average headline hit (mean_hits; MECHC31: was
    one size per type from the boss's multiplier), the others 0. PoB adds the five into one hit; consumers that sum
    them weight each type by its share (type_taken), so Armour still meets a whole Physical hit."""
    return {f"enemy{t}Damage": round(v) for t, v in mean_hits(name).items()}


def type_taken(stats: dict) -> float:
    """Damage one average landed boss hit deals after your mitigation: PoB's {Type}TakenHit (each type at its full hit
    size, config_vars) weighted by the type's share of the boss's hits."""
    weights = type_shares(boss_name(stats))
    return sum(max(number(stats.get(f"{t}TakenHit")), 0.0) * weights[t] for t in TYPES)


# The biggest-hit row (MECHC32; the manager's ruling, 16 Nov 03:28: "show the biggest-hit reading as a row where it moves
# a defence number"). PoB gets each type's AVERAGE ordinary hit (config_vars, mean_hits; each skill once, LOW), which is
# right for readings of many hits (damage taken a second, stun buildup over a fight). The second figure takes EVERY hit
# at its type's biggest ordinary hit (hit_sizes, nukes still out): a harsh bound, not a likely fight. PoB's
# {Type}TakenHit is rescaled by biggest / average; Physical also by Armour's smaller cut of a bigger hit (PoB's
# calcs.armourReductionF: Armour / (Armour + 10 x hit), CalcDefence 57-65 and 2594-2599, Data.lua ArmourRatio 10; the
# other reductions in PoB's PhysicalDamageReduction kept; capped at PhysicalDamageReductionMax, 90 if not read). Armour
# that applies to elemental hits ("Armour applies to Fire damage") isn't rescaled: linear there (LOW-MEDIUM: a small
# undercount of the bigger elemental hit's damage when such a line is taken).
ARMOUR_RATIO = 10.0  # PoB Data.lua data.misc.ArmourRatio
BIGGEST_LABEL = "every hit at its type's biggest ordinary hit (a harsh bound; the headline uses the average hit)"
BIGGEST_FIELDS = ("Armour", "PhysicalDamageReduction", "PhysicalDamageReductionMax")


def physical_kept_ratio(stats: dict, big: float, mean: float) -> float:
    """(damage kept of a `big` Physical hit) / (kept of a `mean` one) from the build's Armour (PoB's formula), 1.0 when no
    Armour is read."""
    armour = number(stats.get("Armour"))
    if armour <= 0 or big <= 0 or mean <= 0:
        return 1.0
    cap = number(stats.get("PhysicalDamageReductionMax")) or 90.0
    total = min(max(number(stats.get("PhysicalDamageReduction")), 0.0), cap)  # (at PoB's hit: the average)

    def armour_dr(hit: float) -> float:
        return min(100 * armour / (armour + ARMOUR_RATIO * hit), cap)

    other = max(total - armour_dr(mean), 0.0)
    kept_mean = 1 - total / 100
    kept_big = 1 - min(armour_dr(big) + other, cap) / 100
    return kept_big / kept_mean if kept_mean > 0 else 1.0


def biggest_reading(stats: dict) -> dict | None:
    """The build's stats with each {Type}TakenHit at the boss's biggest ordinary hit of the type instead of its average
    (the comment above); None when no type's biggest differs from its average (no row to show)."""
    name = boss_name(stats)
    big, mean = hit_sizes(name), mean_hits(name)
    if all(abs(big[t] - mean[t]) < 0.5 or f"{t}TakenHit" not in stats for t in TYPES):
        return None
    read = {**stats, "_biggest": True}
    for t in TYPES:
        key = f"{t}TakenHit"
        if key in stats and mean[t] > 0:
            factor = big[t] / mean[t]
            if t == "Physical":
                factor *= physical_kept_ratio(stats, big[t], mean[t])
            read[key] = number(stats[key]) * factor
    return read


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
    note = (f"{s['boss']}: each type's biggest ordinary hit, {sources_text(s['boss'])}; level {LEVEL}; spells by "
            f"their own effectiveness, attacks {BASE_DAMAGE:g} x the boss's multiplier x the skill's own; x unique "
            f"{UNIQUE_LESS:g} x the level's more (Elemental x{LEVEL_MORE['elemental']:g}, Chaos x{LEVEL_MORE['chaos']:g}); "
            + nuke_text(stats, s["boss"]) + "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items()))
    return f"one hit of each type: {want}", f"max hit {got}: {verdict}", None, note


def worst_sources() -> str:
    """'Physical Vessel of Kulemak, ...': the real preset each of the worst case's hit sizes comes from."""
    from builder.search import bosses
    return ", ".join(f"{t} {n}" for t, n in bosses.WORST_HIT_FROM.items())


def sources_text(name: str) -> str:
    """Which skill each type's hit size comes from (the worst case: which preset's)."""
    from builder.search import bosses
    if name == _worst():
        return "from the real preset that hits hardest with it: " + ", ".join(
            f"{t} {n}'s {biggest_skills(n).get(t, '?')}" for t, n in bosses.WORST_HIT_FROM.items())
    return "from " + ", ".join(f"{t} {k}" for t, k in biggest_skills(name).items() if hit_table(name)[1].get(t))


def nuke_text(stats: dict, name: str) -> str:
    """The red-flash nukes' own row: their size, and whether the build lives through one (reported, not a floor: they
    are made to be walked out of)."""
    rows = nuke_hits(name)
    if not rows:
        return ""
    parts = []
    for t, (size, skill) in rows.items():
        low = nuke_hits(name, "low").get(t, (0.0, ""))[0]
        kept = number(stats.get(f"{t}MaximumHitTaken"))
        fate = "survived" if kept >= size else ("survived only at the low bound" if kept >= low else "kills")
        parts.append(f"{t} {size:,.0f} ({skill}; {low:,.0f} at the low bound): {fate}")
    return "red-flash nukes, made to be walked out of, not in the row above: " + "; ".join(parts) + "; "


def hits_note(name: str | None = None) -> dict:
    """The report's labelled row for the boss's hit sizes."""
    name = name or DEFAULT_BOSS
    rows = {r: hit_sizes(name, r) for r in READINGS}
    weights = type_shares(name)
    mean = mean_hits(name)
    value = "; ".join(f"{t} {rows['headline'][t]:,.0f} at most, {mean[t]:,.0f} on average ({weights[t]:.0%} of hits; "
                      f"without the 0.65 {rows['harsh'][t]:,.0f}, low bound {rows['low'][t]:,.0f})"
                      for t in TYPES if rows["headline"][t] > 0)
    worst = ("; the share of hits pooled over the 12 real presets' damaging skills (LOW)") if name == _worst() else ""
    nukes = nuke_hits(name)
    nuke = ("; red-flash nukes (walk out): " + ", ".join(f"{t} {v:,.0f} ({k})" for t, (v, k) in nukes.items())
            if nukes else "")
    return {"label": f"Boss hit sizes: {name}", "value": value + nuke + add_hits_text(name),
            "basis": "each type's biggest ordinary hit from the boss's own skills in the game files (" + sources_text(name)
                     + "), not PoB's Pinnacle default (965 per type and 386 Chaos, all in one hit): spells by their own "
                     "effectiveness, attacks by the level's damage x the boss's multiplier x the skill's own; x unique 35% "
                     "less x the level's more for Elemental (x4) and Chaos (x2); PoB gets each type's average hit (each skill "
                     "once, LOW), the survival check the biggest" + worst,
            "confidence": "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items()) + "; " + CADENCE_NOTE}


# How often the boss hits (MECHC7, AUDIT18 A18-2): every model that reads the boss's hits (Energy Shield Recharge, Rage,
# "when Hit" buffs, charms, Iron Ward, parries, flasks' Life lost) takes one every enemySkillTime, PoB's boss-preset
# placeholder of 0.7 s. The game decides it per boss skill and no data we hold gives it: a flat number of the last-resort
# kind (CLAUDE.md rule 2), LOW, said once in every build's assumptions here.
#
# Slows on the boss (MECHC8, MECHD1 / MECHD2): PoB's enemySkillTime is the 0.7 s divided by the boss's action speed
# (CalcDefence.lua 3426-3429, calcs.actionSpeedMod), so a build's Chill, Temporal Chains or other Slow on the boss
# lengthens it, and every model above reads that slowed figure. PoB alone sums Slows in full; MECH-D's bridge patch
# (bosses.Boss.slow_taken) applies the unique boss's 50% less Slow taken, multiplicative Slows and the 25% floor, so the
# figure these models read is the corrected one once that lands (no change needed here: they read only enemySkillTime).
CADENCE_NOTE = ("the boss's hits: for defences and recovery one every PoB's enemySkillTime, the faster of 0.7 s and the "
                "boss's per-part hits (0.55 s the Red Queen, 0.6 s the Bodach, 0.7 s the rest; MECH-D's MECHD4); for what "
                "you gain when hit (Rage, charges, parries, Iron Ward, buffs) one per boss skill use, 1.1-2.3 s by boss "
                "(bosses.benefit_skill_time; LOW: the game's per-skill cadence is read from table defaults); both slower "
                "by the build's Slows on the boss (at a unique boss's 50% less Slow taken)")


# ---------------------------------------------------------------------------------------------------------------------
# Damage over time from the boss's hits, which keeps Energy Shield Recharge from starting (MECHC6; FIX_QUEUE RESEARCH21
# #126, research/boss-dot-on-player-es.md). KeywordPopups EnergyShield: "Rapidly Recharges if you don't lose Energy
# Shield for a short time ... Damage from Bleeding and Poison bypasses Energy Shield" (HIGH): an Ignite or Corrupted
# Blood on you keeps removing ES, so Recharge can't start until it ends plus the delay.
# - Ignite (MECHC7, AUDIT21 A21-1; before, every landed Fire hit was taken to Ignite: a flat LOW guess): the game's rule,
#   KeywordPopups Flammability: "1% chance to Ignite the target for every 5% of the target's Ailment Threshold dealt as
#   Fire damage", applied before the hit rolls; "Multiple instances of Flammability stack ... up to 100%. Each instance
#   ... lasting 8 seconds by default"; "modifiers which increase the chance to inflict Ailments will increase the
#   Magnitude of Flammability" (HIGH). The player's threshold is PoB's AilmentThreshold (CalcDefence.lua 1999; half of
#   Life by default, KeywordPopups AilmentThreshold); the Fire dealt is PoB's FireTakenHit (the boss's full Fire hit,
#   config_vars, after your mitigation). The Flammability of the boss's other landed Fire hits in the last 4 s counts at
#   its mean (MEDIUM-LOW: whether an Ignite clears the Flammability on you is UNVERIFIED; if it does, the chance is
#   lower). The bosses' "increased Ignite chance" stats (IGNITE_INCREASE) are averaged over their Fire skills (LOW: each
#   skill's share of the Fire hits). Less your Ignite avoidance (PoB's IgniteAvoidChance). 4 s base (KeywordPopups
#   Ignite) x PoB's SelfIgniteDuration.
# - Corrupted Blood (Atziri's BloodSpearPulse, 3 s, stacking; "is not Bleeding", so it hits ES: MEDIUM-HIGH): one of her
#   damaging skills, at its share of her hits (LOW).
# - Degen grounds (8 bosses): you can step out of most of them: 0, with a play note (RESEARCH21's advice; LOW; the
#   exceptions, RESEARCH26 R26-59, at GROUNDS_NOTE).
# - Bleeding (Olroth) and Poison bypass ES: they don't delay its Recharge.
# ---------------------------------------------------------------------------------------------------------------------
IGNITE_SECONDS = 4.0  # KeywordPopups Ignite: Fire damage over time for 4 s by default (HIGH)
FLAMMABILITY_PER_THRESHOLD = 0.2  # KeywordPopups Flammability: 1% Ignite chance per 5% of the Ailment Threshold (HIGH)
# Flammability on the PLAYER lasts 4 s (MECH75, Q-C2 of PATCH_CHECK.md, an OVERCOUNT of the boss Igniting you before,
# about 2x the stacked chance): patch 0.3.0 (2025-08-29, the body and the 28-08 update): "Flammability is a debuff that
# lasts for 8 seconds on Monsters ... Flammability lasts for 4 seconds on Players"; no later note changes it (checked:
# grep_notes.py --history "Flammability", the newest entry wins; no hit is not proof of no silent change). The game files
# give one default with no player / monster split: KeywordPopups Flammability "lasting 8 seconds by default"; its buff
# row (BuffDefinitions flammability_ignite_chance_buildup, RESEARCH14's export) has no duration field (the duration is in
# its Implementation, code). Both written down; 4 s taken for you (MEDIUM: the patch note over the keyword text), the
# monster side stays 8 s wherever the tool reads it.
FLAMMABILITY_SECONDS = 4.0
# The bosses' Fire skills with an "ignite_chance_+%" stat (GrantedEffectStatSets, read 12 Nov 2026 by
# scratch/mechc7/ignitechance.py): Arbiter of Ash's MASDemonClawBossFire +20 (one of its 17 Fire skills; RESEARCH21 #126
# names the same); Atziri's fire melee MeleeAtAnimationSpeedFire +80 (on phase 1's MonsterVarieties row only, MECHD7: HIGH;
# before, taken for both phases: the note below). Others: none found among the presets' skills by RESEARCH21 (UNVERIFIED beyond its
# scan).
# (MECHD7) Not the Fell Serpent: phase 2's MonsterVarieties row (AtziriBossPhaseTwo______, research13's export) has no
# MeleeAtAnimationSpeedFire, which phase 1's row has (GrantedEffects 1211 / 1221 there; boss_skill_hits.json's rows agree:
# the Red Queen's list holds MeleeAtAnimationSpeedFire / Lightning, the Fell Serpent's neither), and game 26650308 gives
# MeleeAtAnimationSpeedFire ignite_chance_+% 80, MeleeAtAnimationSpeedLightning active_skill_shock_chance_+%_final 80
# (scratch/mechd7/ailscan.py): "taken for both phases" was an OVERCOUNT on the Fell Serpent (HIGH).
IGNITE_INCREASE = {"Arbiter of Ash": (20,), "Atziri, the Red Queen": (80,)}
CORRUPTED_BLOOD = {"Atziri, the Red Queen": 3.0, "Atziri, the Fell Serpent": 3.0}  # seconds (BloodSpearPulse, RESEARCH13)
# (RESEARCH26 R26-59, LOW-MEDIUM, boss-dot-on-player-es-r26.md: placed grounds are taken as 0 stands, but NOT every
# pinnacle damage over time can be stepped out of: the King in the Mists' maze phase (80% Physical / 20% Chaos over time,
# escaped fast; the boss is immune then), Ritual of Meditation (moving brings an unavoidable punishing hit), the Affliction
# Totems / Portent Vapors (damage over time, not grounds); the Bodach's and Kulemak's grounds last all fight (whether the
# arena leaves room: UNVERIFIED). None of these is counted: an UNDERCOUNT of damage taken on those bosses, named here.)
GROUNDS_NOTE = ("the boss's damaging grounds aren't counted: step out of them (8 of 13 bosses have one; LOW); not all "
                "can be (the King's maze, Ritual of Meditation's punishing hit, the Affliction Totems' damage over time, "
                "the Bodach's and Kulemak's all-fight grounds: not counted, an undercount on those bosses)")


# "N% increased Chance to be afflicted by Ailments when Hit" (MECHC37; COV40's routed OVERCOUNT: The Infinite Pursuit's
# drawback, "(80-100)% increased Chance to be afflicted by Ailments when Hit"; PoB parses it to nothing, ModCache 1805,
# leftover text): the boss's chance to Ignite and Shock you x (1 + N%), capped at 100% (MEDIUM: an "increased chance"
# read as scaling the final chance, as the attacker's "increased chance to inflict" scales Flammability's magnitude,
# KeywordPopups Flammability). Not applied: Freeze (buildup, not a chance: UNVERIFIED whether the line reaches it) and
# Olroth's Bleed (its skills' own chance unread: a lower reading). Read from Build.added_stats' SelfAilmentChanceInc, else
# the build's lines in stats["_texts"]. (checked: Stats ids holding "chance_to_be" with "ailment" in game 26650308:
# chance_to_be_inflicted_with_an_ailment_+%, on one Mods row, UniqueAilmentChanceRecieved1, no PassiveSkills /
# SoulCoreStats row; PoB-PoE2 Uniques: The Infinite Pursuit only; the "+N% chance to be Poisoned / Ignited / Shocked"
# lines are self_ailments'.)
SELF_AFFLICTED = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Chance to be afflicted by Ailments when Hit$", re.I)
SELF_AFFLICTED_FIELD = "SelfAilmentChanceInc"


def afflicted_inc(texts: list[str]) -> float:
    """The summed "N% increased / reduced Chance to be afflicted by Ailments when Hit" of the build's lines, in %."""
    return sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
               for t in texts if (m := SELF_AFFLICTED.match(t.strip())))


def afflicted_scale(stats: dict) -> float:
    """1 + the build's increased chance to be afflicted by Ailments (see the note above), at least 0."""
    if SELF_AFFLICTED_FIELD in stats:
        inc = number(stats.get(SELF_AFFLICTED_FIELD))
    else:
        inc = afflicted_inc([str(t) for t in stats.get("_texts") or []])
    return max(1 + inc / 100, 0.0)


def ignite_chance(stats: dict, land: float = 0.0) -> tuple[float, str]:
    """Chance a landed Fire hit of the boss Ignites you (before your Ignite avoidance), and how it was worked out.
    `land` is the boss's landed hits per second (their Fire share stacks Flammability over FLAMMABILITY_SECONDS)."""
    name = boss_name(stats)
    fire = type_shares(name)["Fire"]  # (adds the worst case's IGNITE_INCREASE row: _worst)
    taken = number(stats.get("FireTakenHit")) if "FireTakenHit" in stats else hit_sizes(name)["Fire"]
    threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
    if threshold <= 0:
        return 1.0, "no Ailment Threshold read: every landed Fire hit taken to Ignite (LOW)"
    count = hit_table(name)[1].get("Fire", 0)
    increase = sum(IGNITE_INCREASE.get(name, ())) / count if count else 0.0
    per_hit = max(taken, 0.0) / threshold * FLAMMABILITY_PER_THRESHOLD * (1 + increase / 100)
    others = max(land, 0.0) * fire * FLAMMABILITY_SECONDS
    scale = afflicted_scale(stats)
    chance = min(1.0, per_hit * (1 + others) * scale)
    return chance, (f"a {taken:,.0f} Fire hit against your {threshold:,.0f} Ailment Threshold gives {per_hit:.1%} "
                    f"Flammability (+{increase:.1f}% increased from the boss's skills), plus that of the "
                    f"{others:.1f} other Fire hits landed in the last {FLAMMABILITY_SECONDS:g} s (Flammability on players, patch "
                    f"0.3.0)" + (f", x {scale:.2f} (your increased chance to be afflicted by Ailments, MEDIUM)"
                                 if scale != 1 else "") + f": {chance:.0%} to Ignite (MEDIUM-LOW)")


def es_dot(stats: dict, ignite: bool = True, land: float = 0.0) -> list[tuple[float, float, str]]:
    """(share of the boss's landed hits that leave damage over time on your Energy Shield, its seconds, what)."""
    name = boss_name(stats)
    out = []
    if number(stats.get("DotBypassesEnergyShield")) > 0 and not number(stats.get("DotCannotBypassEnergyShield")) > 0:
        return out  # (MECHC37: The Black Doubt: no damage over time reaches Energy Shield, so none holds its Recharge)
    fire = type_shares(name)["Fire"]
    avoid = min(max(number(stats.get("IgniteAvoidChance")), 0.0), 100) / 100
    if ignite and fire > 0 and avoid < 1:
        chance = ignite_chance(stats, land)[0]
        scale = number(stats.get("SelfIgniteDuration")) / 100 if "SelfIgniteDuration" in stats else 1.0
        if chance > 0:
            out.append((fire * chance * (1 - avoid), IGNITE_SECONDS * scale, "Ignite"))
    if blood := corrupted_blood(name):
        out.append((*blood, "Corrupted Blood"))
    # (MECHC35) with Decree of Loyalty's "Damage over Time cannot bypass your Energy Shield" (objective.DOT_HITS_ES) the
    # boss's Bleeds land on Energy Shield too, so they hold its Recharge off as well (Olroth's, _bleed_share)
    if number(stats.get("DotCannotBypassEnergyShield")) > 0 and (bleed := _bleed_share(name)) > 0:
        kept, _ = deflect_kept(stats, "Bleed")
        avoid_bleed = min(max(number(stats.get("BleedAvoidChance")), 0.0), 100) / 100
        out.append((bleed * kept * (1 - avoid_bleed), BLEED_SECONDS, "Bleeding (your Energy Shield takes it)"))
    return out


def corrupted_blood(name: str) -> tuple[float, float] | None:
    """(share of the boss's landed hits that leave Corrupted Blood on you, its seconds), or None: one of its damaging
    skills (LOW). The worst-case pinnacle: the real preset's with the largest share (MECHC12)."""
    if name == _worst():
        from builder.search import bosses
        rows = [corrupted_blood(n) for n in bosses.REAL if n in CORRUPTED_BLOOD]
        return max(rows) if rows else None
    if name in CORRUPTED_BLOOD:
        return 1 / sum(hit_table(name)[1].values()), CORRUPTED_BLOOD[name]
    return None


def recharge_wait(stats: dict, delay: float, land: float, ignite: bool = True) -> float:
    """Expected wait for Energy Shield Recharge to start: the boss's landed hits as a Poisson stream (`land` per second),
    each needing a quiet `delay` after it, plus its damage over time's seconds when it leaves one:
    (sum of p x e^(land x (delay + D)) - 1) / land. A new hit while an older Ignite still burns isn't followed (a slight
    undercount of the wait)."""
    if land <= 0:
        return delay
    dots = es_dot(stats, ignite, land)
    plain = 1 - sum(p for p, _, _ in dots)
    total = plain * math.exp(min(land * delay, 700.0)) + sum(p * math.exp(min(land * (delay + d), 700.0)) for p, d, _ in dots)
    return (total - 1) / land


def recharge_text(stats: dict, delay: float, land: float) -> str:
    dots = es_dot(stats, land=land)
    if not dots:
        return GROUNDS_NOTE
    parts = ", ".join(f"{what} on {p:.0%} of landed hits for {d:g} s" for p, d, what in dots)
    bare = recharge_wait(stats, delay, land, ignite=False)
    how = ignite_chance(stats, land)[1] if any(what == "Ignite" for _, _, what in dots) else ""
    return (f"damage over time on your Energy Shield delays it: {parts}" + (f" (Ignite: {how}; if none did: {bare:.1f} s)"
            if how else "") + "; " + GROUNDS_NOTE)


# ---------------------------------------------------------------------------------------------------------------------
# The boss's ailments on you, per second, for charms "Used when you become Ignited / Shocked / Frozen / Poisoned" and
# "Used when you start Bleeding" (MECHC17; CLAUDE.md's no-flat-rates rule: before, one per 10 s on any boss dealing the
# type). Which boss inflicts which: RESEARCH13 #60 (pinnacle-ailments-on-player.md: every GrantedEffect stat set of the
# 14 boss varieties; second source poe2db), RESEARCH15 (Xesht's corpses: Lightning, Cold) and RESEARCH14 #67
# (pinnacle-debuffs-from-buffs.md: buffs and grounds). Each from the boss's own landed hits of the type x the game's
# chance for that hit against your Ailment Threshold x (1 - your avoidance), PoB's <Ailment>AvoidChance (100 when immune):
# - Ignite: ignite_chance above (Flammability).
# - Shock: KeywordPopups Shock: "By default a Hit has 1% chance to Shock for every 4% of the target's Ailment Threshold
#   dealt" (HIGH; PoB's Misc.lua ShockChanceMultiplier 25 agrees), x the boss skills' own Shock chance stats averaged over
#   its Lightning skills (SHOCK_FACTORS, RESEARCH13's stat reads; LOW: each skill's share of the hits).
# - Freeze: KeywordPopups Freeze ("Cold damage from Hits Contributes to Freeze Buildup ... until they become Frozen") and
#   AilmentThreshold (a higher threshold lowers "the amount of Freeze ... buildup received"): each Cold hit builds
#   FreezeDamageScalePlayer (2, PoB's Misc.lua game constants) x the Cold dealt / your Ailment Threshold; 100% Freezes.
#   Whether buildup on a player carries over between hits or decays is UNVERIFIED, so the counted reading is the LOWER
#   one (MECHC18; the user: builds return conservative numbers): a Cold hit Freezes only when its own buildup reaches
#   100% (every one or none); the note gives "with buildup kept" (Cold hits x min(1, buildup per hit)) as the upper row.
#   Olroth's SwordGrabExplosion +200% Freeze buildup (one of its Cold skills; FREEZE_FACTORS).
# - Bleeding: KeywordPopups Bleeding: "Damage does not Contribute to Bleeding chance, so it cannot be inflicted without
#   an explicit source of Bleeding chance"; Olroth's attacks_inflict_bleeding_on_hit is the one pinnacle source found
#   (checked: RESEARCH13's scan of every boss skill stat set, RESEARCH14's of their variety mods, BuffDefinitions and
#   grounds; second source poe2db's boss pages via #60; the .ao scripts unseen: MEDIUM-HIGH). Of its ThrowBack and
#   IntroSwing, IntroSwing is taken as the fight's opening swing only (LOW, from its name): one skill's share of its hits
#   (LOW, as Corrupted Blood's). Corrupted Blood "is not Bleeding" (KeywordPopups CorruptedBlood): Atziri's starts none.
# - Poison: no pinnacle Poison chance found (checked: RESEARCH13 #60 over every boss skill stat set; RESEARCH14 #67 over
#   their variety mods, all 3,218 BuffDefinitions and every ground they make; second source poe2db's boss pages;
#   MEDIUM-HIGH, the research's own grade): 0, said.
# The worst-case pinnacle takes the real preset with the highest factor or share (as IGNITE_INCREASE's row does).
SHOCK_PER_THRESHOLD = 0.25  # 1% chance per 4% of the Ailment Threshold dealt (KeywordPopups Shock): HIGH
FREEZE_SCALE_PLAYER = 2.0  # FreezeDamageScalePlayer (PoB Misc.lua, the game's constants): MEDIUM-HIGH
# per boss: the factor each of its Lightning skills with a Shock chance stat gives its Shock chance (RESEARCH13 #60)
SHOCK_FACTORS = {
    "Arbiter of Divinity": (6.0, 3.0),  # ExtendoSpearComboZapBeams +500% final; StormBlinkBolt as though +200% damage
    "The Aberration": (1.5,),  # SpiderBeam +50% final
    # (MECHD7, game 26650308's stat sets, scratch/mechd7/ailscan.py: HIGH) TotemLaser as though +33%, MegaBeam and
    # MegaBeamSides +100% (before: all three at the low end, 1.33)
    "Vessel of Kulemak": (1.33, 2.0, 2.0),
    "Atziri, the Red Queen": (1.8,),  # her lightning melee (MeleeAtAnimationSpeedLightning) +80% final Shock chance
    # (MECHD7) not the Fell Serpent: phase 2 has no MeleeAtAnimationSpeedLightning (IGNITE_INCREASE's note)
    "The Raven Trickster": (3.0,),  # (MECHD7) its Rage minion's GSRageBeamDamage active_skill_shock_chance_+%_final 200
}
# Olroth: SwordGrabExplosion +200% Freeze buildup; (MECHD7) its relic's GSOlrothRelicFireballImpact (Cold) +25%
FREEZE_FACTORS = {"Olroth, Origin of the Fall": (3.0, 1.25)}
BLEED_SKILLS = {"Olroth, Origin of the Fall": 1}  # ThrowBack (IntroSwing: the opening only, LOW)
AILMENT_TYPE = {"Ignite": "Fire", "Shock": "Lightning", "Freeze": "Cold"}


def _factor(name: str, table: dict, kind: str) -> float:
    """The mean factor the boss's skills give an ailment's chance over its hits of `kind` (1 with none)."""
    if name == _worst():
        from builder.search import bosses
        return max([_factor(n, table, kind) for n in bosses.REAL] or [1.0])
    count = hit_table(name)[1].get(kind, 0)
    extra = table.get(name, ())
    return (count - len(extra) + sum(extra)) / count if count else 1.0


def _bleed_share(name: str) -> float:
    if name == _worst():
        from builder.search import bosses
        return max([_bleed_share(n) for n in bosses.REAL] or [0.0])
    skills = BLEED_SKILLS.get(name, 0)
    return skills / sum(hit_table(name)[1].values()) if skills else 0.0


# (MECHC27; COV31's H4-5: an OVERCOUNT of charm uses against Olroth / Atziri for a build holding the lines) "Deflected Hits
# cannot inflict Bleeding on you" / "Deflected Hits cannot inflict Maim on you" (Stats deflected_hits_cannot_inflict_
# bleeding_on_self / deflected_hits_cannot_directly_inflict_maim_on_self; the carrier found: the Staunch Deflection notable
# (checked: PassiveSkills and Mods over both stat ids; PoB leaves the lines unparsed, ModCache 7205-7207). The model reads
# the text, so any carrier counts.) A Deflected Hit still lands (KeywordPopups Deflect: it prevents DeflectEffect% of the
# hit's damage), so of the landed hits that could Bleed or Maim you, PoB's DeflectChance share can't. stats["_deflect_stops"]
# holds the ailments the build's lines name (flasks.deflect_stops, read with the build's charms: only charm and flask
# triggers read these rates). Deflection is kept against red-flash hits too, as the recovery check keeps it (#78, LOW).
def deflect_kept(stats: dict, what: str) -> tuple[float, str]:
    """(share of landed hits that can still inflict `what`, "Bleed" or "Maim", on you; the note) under the build's
    "Deflected Hits cannot inflict ... on you" lines."""
    if what not in (stats.get("_deflect_stops") or ()):
        return 1.0, ""
    chance = min(max(number(stats.get("DeflectChance")), 0.0), 100.0) / 100
    return 1 - chance, f" x {1 - chance:.0%} not Deflected (Deflected Hits cannot inflict {what} on you)"


# Coat of Red's "25% chance to be inflicted with Bleeding when Hit" (MECHC32; the manager, 16 Nov 03:54, routed from COV36:
# a downside nothing counted, an OVERCOUNT of the item; PoB parses it to no mod, ModCache 3205 {{}, leftover text}).
# KeywordPopups Bleeding (game 26650308): "lasts 5 seconds by default. Damage from Bleeding bypasses Energy Shield.
# Bleeding deals an extra 100% damage while the target is moving ... The base Magnitude of Bleeding is Physical damage per
# second equal to 15% of the Pre-mitigation Physical damage of the Hit that inflicted it." The model:
# - rate: the line's chance x the boss's landed hits a second at the DEFENCE cadence (PoB's enemySkillTime, the faster of
#   the two hit rates: the cautious side for a downside, as the manager ruled; the slower "when hit" benefit pace is for
#   gains) x the Physical share of its hits (a hit with no Physical damage inflicts a Bleed of magnitude 0: HIGH,
#   RESEARCH28 R28-3, taken as no Bleed) x Deflection's share where a "Deflected Hits cannot inflict Bleeding" line holds
#   x the share of those hits that reach Life (life_reach, MECHC33: a hit Energy Shield takes whole inflicts none);
# - only the strongest Bleed deals damage (MEDIUM-HIGH, RESEARCH28 R28-4), so Bleeding
#   1 - e^(-rate x 5 s) of the fight (hits taken as evenly random);
# - damage while Bleeding: 15% of the boss's AVERAGE Physical hit (mean_hits, what PoB's hits use) a second, x2 for the
#   share of the fight you move (stats["_rolling"], the build's dodge rolls; 0 when not read: standing while attacking),
#   the biggest ordinary Physical hit as a row; your damage-over-time-taken modifiers (PoB's PhysicalTakenDotMult) apply.
# Not on PoB: PoB has no Life-only damage over time (its Physical degen would let Energy Shield take it and stop ES
# Recharge). The manager's 09:09 ruling: Life only, through Build.added_stats (SelfBleedPerSecond) to
# objective.self_bleed_taken: Life's own steady loss (stops Life Recharge, not ES Recharge), incoming in the recovery
# share, Life costs in the Life refill (Assumptions), at PoB's PhysicalTakenDotMult. (Before this, turn 1 of MECHC32
# put it on PoB as Physical degen, labelled LOW for ES builds.)
SELF_BLEED = re.compile(r"^(\d+(?:\.\d+)?)% chance to be inflicted with Bleeding when Hit$", re.I)
BLEED_SECONDS, BLEED_SHARE_PER_SECOND, BLEED_MOVING_MORE = 5.0, 0.15, 1.0
SELF_BLEED_FIELDS = ("enemySkillTime", "EvadeChance", "EffectiveBlockChance", "DeflectChance", "EnergyShield")


# Hits that reach Life (MECHC33; RESEARCH28 R28-3, the manager's 09:36 ruling: "a hit must reach Life to Bleed",
# MEDIUM-LOW, wiki snippets only): on an Energy Shield build a hit Energy Shield takes whole inflicts no Bleed on you, so
# Coat of Red's rate counted every landed Physical hit (an OVERCOUNT for ES builds). Per boss skill that deals `kind`
# (ordinary hits, each skill once, as mean_hits: LOW), its whole hit after your mitigation (each part rescaled by PoB's
# {Type}TakenHit / the type's average hit, which is what PoB was given): it reaches Life when any of its damage types
# bypasses Energy Shield (PoB's {Type}EnergyShieldBypass above 0), or when it's bigger than the Energy Shield you have.
# You're on Full Energy Shield recovery.full_es_share of the fight (MEDIUM): then a hit reaches Life only when bigger than
# your maximum. Below full the ES left isn't modelled: the headline takes every such hit to reach Life (the cautious side
# for a downside, as the defence cadence; LOW), the row takes the ES left as anywhere from 0 to full (min(1, hit / ES)).
# Mind over Matter's share is taken as still reaching Life (the rest of the hit does).
def life_reach(stats: dict, kind: str = "Physical") -> tuple[float, float, str]:
    """(share of the boss's landed `kind` hits that reach Life (headline), the same with the ES left spread from 0 to
    full (row), how); (1.0, 1.0, "") with no Energy Shield. `stats`: PoB's recovery outputs (objective.RECOVERY_INPUTS)."""
    es = number(stats.get("EnergyShield"))
    if es <= 0:
        return 1.0, 1.0, ""
    from builder.search import recovery

    name = boss_name(stats)
    mean = mean_hits(name)

    def scale(t: str) -> float:
        key = f"{t}TakenHit"
        return max(number(stats[key]), 0.0) / mean[t] if key in stats and mean[t] > 0 else 1.0

    hits: list[tuple[float, bool]] = []
    for row in skill_hits(name):
        if row.get("nuke") or kind not in row["hit"]:
            continue
        size = sum(raw * part_factor(t, origin) * scale(t) for t, (raw, origin) in row["hit"].items())
        bypass = any(number(stats.get(f"{t}EnergyShieldBypass")) > 0 for t in row["hit"])
        hits.append((size, bypass))
    if not hits:  # (the worst-case pinnacle, or a preset with no sized skill: the type's average hit after mitigation)
        hits = [(max(number(stats.get(f"{kind}TakenHit")), 0.0), number(stats.get(f"{kind}EnergyShieldBypass")) > 0)]
    full, _ = recovery.full_es_share(stats)
    over = sum(1.0 for size, bypass in hits if bypass or size > es) / len(hits)
    spread = sum(1.0 if bypass else min(1.0, size / es) for size, bypass in hits) / len(hits)
    head = full * over + (1 - full)
    row = full * over + (1 - full) * spread
    how = (f" x {head:.0%} of them reaching Life (Energy Shield {es:,.0f}: on Full Energy Shield {full:.0%} of the fight, "
           f"where {over:.0%} of the boss's {len(hits)} {kind} hit(s) are bigger than it or bypass it; below full every "
           f"hit taken to reach it, the cautious side: LOW; a hit Energy Shield takes whole inflicts none: MEDIUM-LOW, "
           f"RESEARCH28)")
    return head, row, how


def self_bleed(texts: list[str], stats: dict) -> tuple[float, list[dict]]:
    """(Physical damage a second, before your damage-over-time modifiers, notes) from "N% chance to be inflicted with
    Bleeding when Hit" lines; (0.0, []) without one."""
    chance = min(sum(float(m.group(1)) for t in texts if (m := SELF_BLEED.match(t.strip()))), 100.0) / 100
    if chance <= 0:
        return 0.0, []
    name = boss_name(stats)
    every = number(stats.get("enemySkillTime")) or 0.7
    land = land_share(stats) / every
    physical = type_shares(name).get("Physical", 0.0)
    kept, kept_how = deflect_kept(stats, "Bleed")
    reach, reach_spread, reach_how = life_reach(stats) if physical > 0 else (1.0, 1.0, "")
    rate = chance * land * physical * kept * reach
    moving = min(max(number(stats.get("_rolling")), 0.0), 1.0)
    mean, big = mean_hits(name)["Physical"], hit_sizes(name)["Physical"]

    def bleeding_at(r: float) -> float:
        return 1 - math.exp(-r * BLEED_SECONDS) if r > 0 else 0.0

    def dot(hit: float, r: float = rate) -> float:
        return bleeding_at(r) * BLEED_SHARE_PER_SECOND * hit * (1 + BLEED_MOVING_MORE * moving)

    bleeding = bleeding_at(rate)
    taken, harsh = dot(mean), dot(big)
    if rate <= 0:
        basis = (f"{chance:.0%} chance when Hit, but none of the boss's landed hits deal Physical damage (or none land, or "
                 "none reach Life), so no Bleed (a hit with no Physical damage inflicts a Bleed of 0: HIGH, RESEARCH28)")
    else:
        rows = ""
        if reach < 1:
            rows = (f"; with the Energy Shield left below full spread from 0 to full: {dot(mean, rate / reach * reach_spread):,.0f}"
                    f"; counting every landed hit (no Life rule): {dot(mean, rate / reach):,.0f}")
        basis = (f"{chance:.0%} x {land:.2f} landed boss hits/s (the defence cadence, one every {every:g} s: the faster "
                 f"rate, the cautious side for a downside) x {physical:.0%} of its hits that deal Physical (none from a "
                 f"hit with no Physical damage: HIGH){kept_how}{reach_how} = "
                 f"{rate:.3f} Bleeds/s; only the strongest Bleed deals damage (MEDIUM-HIGH, RESEARCH28), "
                 f"{BLEED_SECONDS:g} s each, so Bleeding {bleeding:.0%} of the fight; {BLEED_SHARE_PER_SECOND:.0%} of the "
                 f"boss's average Physical hit ({mean:,.0f}) a second" + (f", x2 for the {moving:.0%} of the fight you move"
                                                                        if moving else
                                                                        " (standing while attacking; x2 while you move)")
                 + f": {taken:,.0f} a second on average before your damage-over-time modifiers; at its biggest ordinary "
                 f"Physical hit ({big:,.0f}): {harsh:,.0f}{rows}. Without it: 0. Taken from Life only (Bleeding bypasses "
                 "Energy Shield; it stops Life Recharge, not ES Recharge)")
    return taken, [{"label": "Bleeding inflicted on you when Hit (your item's downside)*",
                    "value": f"{taken:,.0f} Physical damage a second (Bleeding {bleeding:.0%} of the fight)",
                    "basis": basis, "confidence": "MEDIUM (rate, the game's numbers) / MEDIUM-LOW (only hits reaching "
                                                  "Life) / LOW (the Energy Shield left below full, the moving share)",
                    "play": (f"you Bleed {bleeding:.0%} of the fight from your own item: {taken:,.0f} a second "
                             "(without it: none)") if taken > 0 else ""}]


# The share of the fight you're Bleeding (MECHC42; for the "while Bleeding" lines: Venopuncture's Chill downside in
# boss_chill_on_you, the Armour rows in recovery.frozen_bleeding_lines): the boss's own Bleeds (ailment_rate: Olroth's
# ThrowBack, the one pinnacle source) plus your own items' "N% chance to be inflicted with Bleeding when Hit" (self_bleed's
# rate: its landed Physical hits that reach Life), each Bleed lasting BLEED_SECONDS, hits taken as random (Poisson):
# 1 - e^(-rate x 5 s) (MEDIUM). Deflection's "Deflected Hits cannot inflict Bleeding" is read only where stats carry
# "_deflect_stops" (an upper reading of the share elsewhere, LOW).
def bleeding_on_you(texts: list[str], stats: dict) -> tuple[float, str]:
    """(share of the fight you're Bleeding, how)."""
    name = boss_name(stats)
    every = number(stats.get("enemySkillTime")) or 0.7
    land = land_share(stats) / every
    boss_rate, boss_how = ailment_rate(stats, "Bleed", land)
    chance = min(sum(float(m.group(1)) for t in texts if (m := SELF_BLEED.match(t.strip()))), 100.0) / 100
    own, own_how = 0.0, ""
    if chance > 0:
        physical = type_shares(name).get("Physical", 0.0)
        kept, _ = deflect_kept(stats, "Bleed")
        reach = life_reach(stats)[0] if physical > 0 else 1.0
        own = chance * land * physical * kept * reach
        own_how = f"; your own items' {chance:.0%} chance when Hit: {own * 60:.1f} a minute"
    rate = boss_rate + max(own, 0.0)
    share = 1 - math.exp(-rate * BLEED_SECONDS) if rate > 0 else 0.0
    return share, (f"the boss's Bleeds: {boss_how}{own_how}; {BLEED_SECONDS:g} s each: Bleeding {share:.0%} of the fight "
                   "(MEDIUM)")


# "+N% chance to be Poisoned / Ignited / Shocked" on your own items (MECHC35; COV38's downside sweep, the manager 21:50
# 16 Nov: OVERCOUNTS while nothing counted them; PoB parses none of them: Apep's Supremacy's "+25% chance to be Poisoned"
# and the Exclusive mods IncreasedChanceToBeIgnitedUnique* "+25% chance to be Ignited", ChanceToBeShockedUnique__1 / 2
# "+20% / +50% chance to be Shocked"). Coat of Red's pattern (self_bleed): each landed boss hit at the DEFENCE cadence
# (enemySkillTime, the cautious side for a downside) rolls the line's chance, read as a flat chance added to each hit's
# own (the "+" wording; MEDIUM-LOW, no second source); KeywordPopups (game 26650308) give the rest:
# - Poison: "Damage does not Contribute to Poison chance, so it cannot be inflicted without an explicit source of Poison
#   chance" (the line is one), "lasts 2 seconds", "bypasses Energy Shield", magnitude "20% of the Pre-mitigation Physical
#   and Chaos damage of the Hit" a second: so only the boss's hits with Physical or Chaos damage (none with neither: a
#   magnitude of 0, as Bleed's), x Deflection's share where a "Deflected Hits cannot inflict Poison" line holds, x the
#   hits that reach Life (life_reach: Bleed's rule, MEDIUM-LOW, taken for Poison too). Poisons on you all stack (UNVERIFIED:
#   no text limits them on players; the one-at-a-time reading a row): rate x 2 s of them at once. Life only, through
#   Build.added_stats (SelfPoisonPerSecond: objective.self_bleed_taken, at PoB's ChaosTakenDotMult), as the Bleed.
# - Ignite: "lasts 4 seconds", magnitude "20% of the Fire damage dealt by the Hit" a second ("not any modifiers on the
#   target": the boss's average Fire hit before your mitigation, mean_hits; PoB's Fire degen then applies your
#   resistance); only the strongest Ignite deals damage (taken as Bleed's, MEDIUM-HIGH); the chance adds to each Fire hit's
#   Flammability (ignite_chance), Fire hits only (no Fire, no magnitude). Counted: the Ignited share WITH the line less the
#   share without it (the boss's own Ignites on you aren't scored for any build yet: an UNDERCOUNT named in the note), as
#   Fire damage taken a second on PoB (Ignite doesn't bypass Energy Shield).
# - Shock: "20% increased damage", "4 seconds on players"; its magnitude doesn't depend on the hit, so with an explicit
#   chance every landed hit may Shock (UNVERIFIED; the Lightning-hits-only reading a row), Lightning hits adding their
#   own chance (1% per 4% of your Ailment Threshold, ailment_rate's). Counted: the Shocked share with the line less the
#   share without it, x 20% x your "effect of Shock on you" (PoB's SelfShockEffect), as DamageTaken INC (the boss's own
#   Shocks on you aren't scored for any build yet: an UNDERCOUNT named in the note).
# Your avoidance (PoB's {Ailment}AvoidChance) and ailment duration on you (PoB's Self{Ailment}Duration) apply.
SELF_AILMENT_CHANCE = re.compile(r"^\+(\d+(?:\.\d+)?)% chance to be (Poisoned|Ignited|Shocked)$", re.I)
SELF_AILMENT_FIELDS = ("IgniteAvoidChance", "ShockAvoidChance", "PoisonAvoidChance", "SelfIgniteDuration",
                       "SelfShockDuration", "SelfPoisonDuration", "SelfShockEffect", "FireTakenHit", "LightningTakenHit",
                       "AilmentThreshold", "Life", "ChaosTakenDotMult")
POISON_SECONDS, POISON_SHARE_PER_SECOND, IGNITE_SHARE_PER_SECOND, SHOCK_ON_PLAYER_SECONDS = 2.0, 0.20, 0.20, 4.0


def self_ailment_chances(texts: list[str]) -> dict[str, float]:
    """{"Poison" / "Ignite" / "Shock": the summed "+N% chance to be ..." of the build's lines, as a fraction}."""
    out: dict[str, float] = {}
    for t in texts:
        if m := SELF_AILMENT_CHANCE.match(t.strip()):
            kind = {"poisoned": "Poison", "ignited": "Ignite", "shocked": "Shock"}[m.group(2).lower()]
            out[kind] = min(out.get(kind, 0.0) + float(m.group(1)) / 100, 1.0)
    return out


def self_ailments(texts: list[str], stats: dict) -> tuple[float, float, float, list[dict]]:
    """(Poison Chaos damage a second on Life before your damage-over-time modifiers, Ignite Fire damage a second (before
    those modifiers; Build.added_stats' BossIgnitePerSecond, beside the boss's own),
    increased damage taken % from Shock, notes) from the build's "+N% chance to be Poisoned / Ignited / Shocked" lines
    (the comment above); zeros and no notes without one."""
    chances = self_ailment_chances(texts)
    if not chances:
        return 0.0, 0.0, 0.0, []
    name = boss_name(stats)
    every = number(stats.get("enemySkillTime")) or 0.7
    land = land_share(stats) / every
    shares = type_shares(name)

    def avoid(kind: str) -> float:
        return 1 - min(max(number(stats.get(f"{kind}AvoidChance")), 0.0), 100.0) / 100

    def seconds(kind: str, base: float) -> float:
        key = f"Self{kind}Duration"
        return base * (number(stats.get(key)) / 100 if key in stats else 1.0)

    def up(rate: float, length: float) -> float:
        return 1 - math.exp(-rate * length) if rate > 0 else 0.0

    poison = ignite = shock = 0.0
    notes: list[dict] = []
    if (x := chances.get("Poison", 0.0)) > 0:
        rows = [r for r in skill_hits(name) if not r.get("nuke")]
        if rows:
            carry = sum(1.0 for r in rows if {"Physical", "Chaos"} & set(r["hit"])) / len(rows)
        else:
            carry = min(1.0, shares.get("Physical", 0.0) + shares.get("Chaos", 0.0))
        kept, kept_how = deflect_kept(stats, "Poison")
        reach, _, reach_how = life_reach(stats) if carry > 0 else (1.0, 1.0, "")
        rate = x * land * carry * kept * reach * avoid("Poison")
        length = seconds("Poison", POISON_SECONDS)
        mean = mean_hits(name)
        hit = mean.get("Physical", 0.0) + mean.get("Chaos", 0.0)
        poison = rate * length * POISON_SHARE_PER_SECOND * hit
        single = up(rate, length) * POISON_SHARE_PER_SECOND * hit
        notes.append({"label": "Poisoned when Hit (your item's downside)*",
                      "value": f"{poison:,.0f} Chaos damage a second on Life",
                      "basis": (f"+{x:.0%} chance to be Poisoned x {land:.2f} landed boss hits/s (the defence cadence, the "
                                f"cautious side for a downside) x {carry:.0%} of its hits dealing Physical or Chaos damage "
                                f"(no magnitude without: KeywordPopups Poison){kept_how}{reach_how} x {avoid('Poison'):.0%} "
                                f"not avoided = {rate:.3f} Poisons/s, {length:g} s each, all stacking (UNVERIFIED), "
                                f"{POISON_SHARE_PER_SECOND:.0%} of the boss's average Physical + Chaos hit ({hit:,.0f}, "
                                f"before mitigation) a second each: {poison:,.0f} a second before your damage-over-time "
                                f"modifiers; one Poison at a time: {single:,.0f}. Bypasses Energy Shield (Life only). "
                                "Without it: 0"),
                      "confidence": "MEDIUM-LOW (the chance read as flat, the stacking UNVERIFIED)",
                      "play": (f"you are Poisoned by your own item: {poison:,.0f} a second (without it: none)"
                               if poison > 0 else "")})
    if (x := chances.get("Ignite", 0.0)) > 0:
        fire = shares.get("Fire", 0.0)
        own, own_how = ignite_chance(stats, land) if fire > 0 else (0.0, "")
        length = seconds("Ignite", IGNITE_SECONDS)
        without = up(land * fire * own * avoid("Ignite"), length)
        with_line = up(land * fire * min(1.0, own + x) * avoid("Ignite"), length)
        hit = mean_hits(name).get("Fire", 0.0)
        ignite = (with_line - without) * IGNITE_SHARE_PER_SECOND * hit
        notes.append({"label": "More often Ignited (your item's downside)*",
                      "value": (f"{ignite:,.0f} Fire damage a second (Ignited {with_line:.0%} of the fight, "
                                f"{without:.0%} without it)"),
                      "basis": (f"+{x:.0%} chance to be Ignited added to each of the boss's Fire hits ({fire:.0%} of its "
                                f"hits; {land:.2f} landed hits/s, the defence cadence) over their own {own:.0%} "
                                f"({own_how or 'no Fire hits'}), x {avoid('Ignite'):.0%} not avoided, {length:g} s each: "
                                f"Ignited {with_line:.0%} of the fight instead of {without:.0%}; only the strongest Ignite "
                                f"burns: {IGNITE_SHARE_PER_SECOND:.0%} of the boss's average Fire hit ({hit:,.0f}) a second "
                                f"for the extra share = {ignite:,.0f} a second, as Fire damage taken (your resistance and "
                                "Energy Shield apply), counted in the boss's damage per second beside the boss's own Ignites "
                                "(boss_ailments_on_you, every build); without the line: 0"),
                      "confidence": "MEDIUM-LOW (the chance read as flat)"})
    if (x := chances.get("Shock", 0.0)) > 0:
        lightning = shares.get("Lightning", 0.0)
        threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
        own = own_shock_chance(stats, name) if threshold > 0 and lightning > 0 else 0.0
        length = seconds("Shock", SHOCK_ON_PLAYER_SECONDS)
        effect = number(stats.get("SelfShockEffect")) / 100 if "SelfShockEffect" in stats else 1.0
        without = up(land * lightning * own * avoid("Shock"), length)
        every_hit = up(land * (lightning * min(1.0, own + x) + (1 - lightning) * x) * avoid("Shock"), length)
        lightning_only = up(land * lightning * min(1.0, own + x) * avoid("Shock"), length)
        shock = 20.0 * effect * (every_hit - without)
        notes.append({"label": "More often Shocked (your item's downside)*",
                      "value": (f"{shock:.1f}% increased damage taken on average (Shocked {every_hit:.0%} of the fight, "
                                f"{without:.0%} without it)"),
                      "basis": (f"+{x:.0%} chance to be Shocked on each of the boss's {land:.2f} landed hits/s (the "
                                f"defence cadence; every hit, as Shock's magnitude doesn't depend on the hit: UNVERIFIED), "
                                f"its Lightning hits ({lightning:.0%}) adding their own {own:.0%} (1% per 4% of your "
                                f"Ailment Threshold), x {avoid('Shock'):.0%} not avoided, {length:g} s each: Shocked "
                                f"{every_hit:.0%} of the fight instead of {without:.0%}; 20% increased damage taken x "
                                f"{effect:.0%} (your effect of Shock on you) for the extra share = {shock:.1f}%. Lightning "
                                f"hits only: Shocked {lightning_only:.0%}, "
                                f"{20.0 * effect * (lightning_only - without):.1f}%. The boss's own Shocks on you are "
                                "counted for every build beside it (boss_ailments_on_you); without the line: 0"),
                      "confidence": "MEDIUM-LOW (the chance read as flat; every hit UNVERIFIED)"})
    return poison, ignite, shock, notes


# The boss's OWN Ignite and Shock on you, as damage taken, for every build (MECHC37; the manager 17 Nov ~20:50, ruling (a)
# on MECHC36's question: "YES, an accuracy fix"). Before, they were read only as rates (charms "Used when you become
# Ignited / Shocked", the Ignited box, Energy Shield Recharge's wait in es_dot), never as damage: an UNDERCOUNT of damage
# taken against Fire / Lightning bosses, the default Arbiter of Ash among them. PoB counts neither (checked: CalcDefence.lua
# holds no Shock or Ignite damage on the player; its "Are you Shocked?" box adds 15% increased damage taken only while
# ticked, ConfigOptions.lua 1082-1085, and "Are you Ignited?" is a Condition flag only, 1061-1063; the tool ticks the
# Shocked box only for Overcharge / Redflare, conditions._self_shocked, and the Ignited box only for lines that read it).
# - Ignite: KeywordPopups Ignite: "Fire damage per second equal to 20% of the Fire damage dealt by the Hit that inflicted
#   it ... using the final damage dealt by the Hit, but not any modifiers on the target" (HIGH). So 20% of the boss's
#   average Fire hit before your mitigation (mean_hits; the average is the lower reading: bigger hits Ignite more often
#   and only the strongest Ignite burns: MEDIUM) a second, while Ignited: its landed Fire hits x ignite_chance x (1 -
#   avoidance), 4 s x SelfIgniteDuration each, Ignited 1 - e^(-rate x length) of the fight (Poisson hits), at the defence
#   cadence (enemySkillTime: the cautious side for damage taken, as self_ailments). It reaches the recovery share as
#   incoming damage at PoB's FireTakenDotMult (objective.boss_ignite_taken) through Build.added_stats, NOT as PoB's Fire
#   degen: PoB's degen is read as the build's own steady loss (objective.own_loss), which would hold Energy Shield and
#   Life Recharge at 0 all fight; an Ignite's hold on Recharge is es_dot's (a share of the hits).
# - Shock: KeywordPopups Shock: "causes targets to take 20% increased damage, and lasts 4 seconds on players" (HIGH), x
#   PoB's SelfShockEffect (your "effect of Shock on you"), while Shocked: its landed Lightning hits x 1% per 4% of your
#   Ailment Threshold dealt (KeywordPopups Shock) x the boss's Shock-chance stats (SHOCK_FACTORS) x (1 - avoidance), 4 s x
#   SelfShockDuration each. Counted as the time-weighted average, DamageTaken INC on every hit (MEDIUM: the average over
#   the fight; "while Shocked" is a row). PoB's {Type}TakenHit already holds the last pass's Shock, so the chance read
#   from it includes being Shocked (a hit while Shocked is bigger and Shocks more often: real, a small fixed point).
#   With PoB's Shocked box already ticked (Overcharge's reflected Shocks, Redflare Conduit), the defence reading counts
#   you Shocked all fight, so none is added (no double count); the box's share doesn't take the boss's Shocks in (named).
SHOCK_ON_PLAYER_INC = 20.0  # KeywordPopups Shock: "take 20% increased damage" (HIGH)


def _avoid_share(stats: dict, kind: str) -> float:
    """The share of `kind` (Ignite / Shock / Poison) not avoided: 1 - PoB's {kind}AvoidChance."""
    return 1 - min(max(number(stats.get(f"{kind}AvoidChance")), 0.0), 100.0) / 100.0


def _self_seconds(stats: dict, kind: str, base: float) -> float:
    """An ailment's length on you: `base` x PoB's Self{kind}Duration (100 = unmodified)."""
    key = f"Self{kind}Duration"
    return base * (number(stats.get(key)) / 100 if key in stats else 1.0)


def own_shock_chance(stats: dict, name: str | None = None) -> float:
    """The chance one of the boss's landed Lightning hits Shocks you, before avoidance: 1% per 4% of your Ailment
    Threshold dealt (KeywordPopups Shock) x the boss's Shock-chance stats (SHOCK_FACTORS)."""
    name = name or boss_name(stats)
    if type_shares(name).get("Lightning", 0.0) <= 0:
        return 0.0
    key = "LightningTakenHit"
    taken = max(number(stats.get(key)) if key in stats else hit_sizes(name).get("Lightning", 0.0), 0.0)
    threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
    if threshold <= 0:
        return 1.0
    return min(1.0, taken / threshold * SHOCK_PER_THRESHOLD * _factor(name, SHOCK_FACTORS, "Lightning")
               * afflicted_scale(stats))


def boss_ailments_on_you(stats: dict, shocked_box: bool = False, texts: list[str] | None = None) -> dict:
    """The boss's own Ignite and Shock on you (the comment above): {"ignite": Fire damage a second before your damage-over-
    time modifiers, "ignited": its share of the fight, "shock": increased damage taken % on average, "shocked": its share,
    "shock_full": % while Shocked, "notes": play-guide rows}. `shocked_box`: PoB's Shocked box already ticked."""
    name = boss_name(stats)
    every = number(stats.get("enemySkillTime")) or 0.7
    land = land_share(stats) / every
    kinds = type_shares(name)
    out = {"ignite": 0.0, "ignited": 0.0, "shock": 0.0, "shocked": 0.0, "shock_full": 0.0, "notes": []}

    def up(rate: float, length: float, immune: float = 0.0) -> float:
        if rate > 0 and immune > 0:  # (MECHC40: Shackles' "cannot be ... for N seconds after", the Chill's form)
            return min(1.0, length / (max(immune, length) + 1 / rate))
        return 1 - math.exp(-rate * length) if rate > 0 else 0.0

    fire = kinds.get("Fire", 0.0)
    if fire > 0 and land > 0:
        chance, how = ignite_chance(stats, land)
        kept = _avoid_share(stats, "Ignite")
        length = _self_seconds(stats, "Ignite", IGNITE_SECONDS)
        rate = land * fire * chance * kept
        ignite_immune = immune_after(texts, "Ignited")
        ignited = up(rate, length, ignite_immune)
        hit = mean_hits(name).get("Fire", 0.0)
        out["ignite"], out["ignited"] = ignited * IGNITE_SHARE_PER_SECOND * hit, ignited
        if kept <= 0:
            value, play = "none: you can't be Ignited", "you can't be Ignited: the boss's Fire hits never burn you"
        else:
            value = f"{out['ignite']:,.0f} Fire damage a second on average (Ignited {ignited:.0%} of the fight)"
            play = (f"the boss's Fire hits Ignite you {ignited:.0%} of the fight: {out['ignite']:,.0f} Fire damage a "
                    "second before your resistance (Ignite avoidance, a larger Ailment Threshold or a shorter Ignite on "
                    "you lowers it); without it: none")
        out["notes"].append({
            "label": "The boss Ignites you*", "value": value,
            "basis": (f"{name}'s {land:.2f} landed hits/s (the defence cadence) x {fire:.0%} Fire x {chance:.0%} to "
                      f"Ignite ({how}) x {kept:.0%} not avoided = {rate * 60:.1f} Ignites a minute, {length:g} s each"
                      + (f", then no Ignite for {ignite_immune:g} s after each (without that: Ignited "
                         f"{up(rate, length):.0%})" if ignite_immune else "") + ": "
                      f"Ignited {ignited:.0%} of the fight; an Ignite burns {IGNITE_SHARE_PER_SECOND:.0%} of the Igniting "
                      f"hit's Fire damage a second (KeywordPopups Ignite), the boss's average Fire hit {hit:,.0f} before "
                      f"your mitigation (the lower reading: bigger hits Ignite more and only the strongest burns) = "
                      f"{out['ignite']:,.0f} a second on average, then your Fire resistance and damage-over-time "
                      "modifiers; counted in the boss's damage per second for the recovery share. Energy Shield "
                      "Recharge's wait already counted its burn. PoB counts none; without it: 0"),
            "confidence": "MEDIUM (the game's Ignite rule; the Flammability chance MEDIUM-LOW; the average hit)",
            "play": play})
    lightning = kinds.get("Lightning", 0.0)
    if lightning > 0 and land > 0:
        chance = own_shock_chance(stats, name)
        kept = _avoid_share(stats, "Shock")
        length = _self_seconds(stats, "Shock", SHOCK_ON_PLAYER_SECONDS)
        effect = max(number(stats.get("SelfShockEffect")), 0.0) / 100 if "SelfShockEffect" in stats else 1.0
        rate = land * lightning * chance * kept
        shock_immune = immune_after(texts, "Shocked")
        shocked = up(rate, length, shock_immune)
        full = SHOCK_ON_PLAYER_INC * effect
        out["shocked"], out["shock_full"] = shocked, full
        out["shock"] = 0.0 if shocked_box else full * shocked
        if kept <= 0 or effect <= 0:
            value = "none: you can't be Shocked" if kept <= 0 else "none: you're unaffected by Shock"
            play = "the boss's Shocks don't affect you"
        elif shocked_box:
            value = f"in PoB's Shocked box (Shocked {shocked:.0%} of the fight by the boss)"
            play = (f"the boss Shocks you {shocked:.0%} of the fight; your own Shock source keeps PoB's Shocked box on, "
                    "so the defences already count you Shocked")
        else:
            value = (f"+{out['shock']:.1f}% increased damage taken on average (Shocked {shocked:.0%} of the fight; "
                     f"+{full:g}% while Shocked)" + ("; the defence floors read it in full"
                                                     if SHOCK_FLOOR_FROM <= shocked < SHOCK_FLOOR_WHOLE else ""))
            play = (f"the boss's Lightning hits Shock you {shocked:.0%} of the fight: +{full:g}% damage taken while "
                    f"Shocked, +{out['shock']:.1f}% on average (counted); Shock avoidance or a larger Ailment Threshold "
                    "lowers it; without it: +0%")
        out["notes"].append({
            "label": "The boss Shocks you*", "value": value,
            "basis": (f"{name}'s {land:.2f} landed hits/s (the defence cadence) x {lightning:.0%} Lightning x "
                      f"{chance:.0%} to Shock (1% per 4% of your Ailment Threshold dealt, x the boss's Shock-chance "
                      f"stats) x {kept:.0%} not avoided = {rate * 60:.1f} Shocks a minute, {length:g} s each"
                      + (f", then no Shock for {shock_immune:g} s after each (without that: Shocked "
                         f"{up(rate, length):.0%})" if shock_immune else "") + ": Shocked "
                      f"{shocked:.0%} of the fight; {SHOCK_ON_PLAYER_INC:g}% increased damage taken (KeywordPopups "
                      f"Shock) x {effect:.0%} (your effect of Shock on you) = {full:g}% while Shocked, "
                      f"{full * shocked:.1f}% on average, "
                      + ("not added: PoB's Shocked box is already on for your own Shock source, so the defences count "
                         "you Shocked all fight" if shocked_box else "added to every hit as increased damage taken"
                         + (f"; the defence floors read it in full, +{full:g}% (Shocked {SHOCK_FLOOR_FROM:.0%}+ of the "
                            "fight: a harm counts at its worst, as a help counts only at full uptime; the manager's 18 "
                            "Nov ruling)" if SHOCK_FLOOR_FROM <= shocked < SHOCK_FLOOR_WHOLE
                            else f"; the floors read the average too (Shocked under {SHOCK_FLOOR_FROM:.0%} of the fight)"
                            if shocked < SHOCK_FLOOR_FROM else ""))
                      + ". PoB counts none; without it: +0%"),
            "confidence": "MEDIUM (the game's Shock rule; the average over the fight, not hit-by-hit timing)",
            "play": play})
    return out


# The boss's Shock in the defence floors (MECHC38; the manager's ruling (i), 18 Nov 02:30, on MECHC37's question): "count
# it IN FULL whenever its modelled uptime is at least 7% (the user's 'under 7% is basically worthless' threshold,
# mirrored), and show the average as a row. Floors count a helpful effect only at full uptime (the user's 9 Nov rule), so a
# harmful one counts at its worst. Below 7%, use the average." Everything else (the recovery share's damage over the
# fight, rates read off PoB's {Type}TakenHit: stuns, ailments on you) keeps the average, so the full Shock reaches only the
# floors' own recalculation: Assumptions registers it as one more part-time effect (ledger_part_time under
# BOSS_SHOCK_FLOOR_KEY, in_stats, so no DPS weighing), whose "off" state (Search.floor_without's all-off recalculation,
# _switch -> Assumptions.ledger_switch False) puts the Shock in full; the floors read the worse of the two (objective.
# deficits; run.floors_at_full_uptime). Up 99.5%+ of the fight the average is already the full Shock. With PoB's Shocked
# box on (Overcharge / Redflare) nothing is added (as before): that box already holds you Shocked in full.
SHOCK_FLOOR_FROM = 0.07  # conditions.SWITCH_ON (the user, 9 Nov 2026: "if something isn't active at least 7% of the time, it's basically worthless")
SHOCK_FLOOR_WHOLE = 0.995  # conditions.FULL_UPTIME
BOSS_SHOCK_FLOOR_KEY = "BossShockInFullForTheFloors"


def boss_shock_for_floors(on_you: dict, average: bool = True) -> tuple[float, float]:
    """(increased damage taken % to add, the share of the fight to register as the floors' part-time effect, or 0) for the
    boss's Shock on you (boss_ailments_on_you's answer; the comment above). `average`: False in the floors' recalculation
    (Assumptions.ledger_switch[BOSS_SHOCK_FLOOR_KEY]), which then gets the Shock in full."""
    shocked, average_inc = on_you.get("shocked", 0.0), on_you.get("shock", 0.0)
    if average_inc <= 0 or not SHOCK_FLOOR_FROM <= shocked < SHOCK_FLOOR_WHOLE:
        return max(average_inc, 0.0), 0.0
    return (average_inc if average else on_you.get("shock_full", 0.0)), shocked


# The boss's Chill on you as action speed (MECHC38): before, the boss's Chills on you counted only as Slow events for
# charms (slow_rate), never as speed: every build fighting a Cold-hitting boss (the default "Pinnacle (worst case)"
# among them: 15% of its hits Cold) kept its full action speed, an OVERCOUNT of DPS; and Sierran Inheritance's "The Effect
# of Chill on you is reversed" (the manager's ruling (iii), 18 Nov 02:30: a BENEFIT, modelled "once RESEARCH31 says what
# 'reversed' gives": research/sierran-inheritance-reversed-chill.md, R31-1) turned the same Chills into speed, uncounted
# (an UNDERCOUNT). PoB applies a Chill on you only under its "Are you Chilled?" box (CalcPerform 756-765: ActionSpeed INC
# -effect, +effect with SelfChillEffectIsReversed), which the tool never ticks (checked: grep conditionChilled over
# builder/ and engine/: only conditionChilledEnemyRecently / conditionEnemyChilled, enemy-side).
# - Which hits Chill you: the boss's landed Cold hits (KeywordPopups Chill: "Cold damage from Hits can Chill by
#   default"); with "All Damage taken from Hits Contributes to Magnitude of Chill inflicted on you" (Sierran Inheritance
#   since 0.4.0, AllDamageTakenCanChillUnique__1/__2; PoB leaves it unparsed, ModCache 5598) every type's hit, and Scold's
#   Bridle's self-hits ("Upfront Costs will result in taking the damage all at once as a Hit", KeywordPopups
#   TakeManaCostAsDamage; R31-1 4(b): one hit per use of the main skill, recovery.cost_damage's). Your own Chilled Ground
#   under you is not counted (named; no model puts you on it).
# - Magnitude of one hit: 100% x the hit's damage after your mitigation / your Ailment Threshold (PoB's linear reading,
#   MEDIUM: research chill-floor-in-pob.md), x PoB's SelfChillEffect (your "Effect of Chill on you"), x the build's
#   Slowing Potency of Debuffs on you (recovery.SLOW_POTENCY; MEDIUM for the reversed Chill, R31-1 3), floored at 30% (a
#   smaller Chill is ignored) and capped at 50% (KeywordPopups Chill: "Minimum of 30% and a default maximum of 50%",
#   HIGH). The Chill on you: the rate-weighted mean of the Chilling sources' magnitudes (the strongest Chill applies while
#   two overlap: MEDIUM, the lower reading for both signs).
# - Share of the fight Chilled: Chilling hits c a second (the boss's at the defence cadence, as boss_ailments_on_you, x
#   each type's share x (1 - PoB's ChillAvoidChance); Scold's at the main skill's uses), each 2 s on players
#   (KeywordPopups Chill: "lasts 2 seconds on players", HIGH) x PoB's SelfChillDuration: 1 - e^(-c x D) (Poisson hits).
#   "You cannot be Chilled for 6 seconds after being Chilled" (UniqueChillImmunityWhenChilled1, 6000 ms): D / (6 + 1 / c).
# - Counted as the time-weighted average, ActionSpeed INC: -(share x magnitude) for the Slow, +(share x magnitude)
#   reversed (PoB's own path is INC; the game may apply "more" action speed: R31-1 2, UNVERIFIED, small). "Your speed is
#   unaffected by Slows" cancels the Slow, and the reversed Chill too (the lower reading; UNVERIFIED). Action speed is no
#   defence floor, so no floor reading. With PoB's Chilled box on (nothing ticks it today) nothing is added.
CHILL_MAX_ON_PLAYER = 50.0  # KeywordPopups Chill: "a default maximum of 50%" (HIGH)
CHILL_IMMUNE_AFTER = re.compile(r"^You cannot be Chilled for (\d+(?:\.\d+)?) seconds after being Chilled$", re.I)
# (MECHC40, COV43: Shackles of the Wretched's other three, UniqueChillImmunityWhenChilled1's siblings; PoB parses none,
# ModCache {nil, ...}) "You cannot be Ignited / Shocked / Frozen for N seconds after being ...": the same form as the
# Chill's, D / (max(N, D) + 1 / c) for Ignite and Shock; the Freeze's next one no sooner than N s after its start.
IMMUNE_AFTER = re.compile(r"^You cannot be (Ignited|Shocked|Frozen) for (\d+(?:\.\d+)?) seconds after being \1$", re.I)


def immune_after(texts: list[str] | None, ailment: str) -> float:
    """Seconds you can't get `ailment` ("Ignited" / "Shocked" / "Frozen") again after it, from the build's lines."""
    return max((float(m.group(2)) for t in texts or [] if (m := IMMUNE_AFTER.match(t.strip()))
                and m.group(1).capitalize() == ailment), default=0.0)
ALL_DAMAGE_CHILLS = re.compile(r"^All Damage taken from Hits Contributes to Magnitude of Chill inflicted on you$", re.I)
CHILL_REVERSED = re.compile(r"^The Effect of Chill on you is reversed$", re.I)
# (MECHC42; an OVERCOUNT before: PoB leaves it unparsed, ModCache 5599-5601) Venopuncture's downside: the boss's hits of
# every type Chill you while you're Bleeding (stats["_bleeding"]: bleeding_on_you's share), Cold hits as always
BLEEDING_CHILLS = re.compile(r"^All Damage taken from Hits while Bleeding Contributes to Magnitude of Chill on you$", re.I)
CHILL_FIELDS = ("ChillAvoidChance", "SelfChillDuration", "SelfChillEffect", "AilmentThreshold", "Life", "enemySkillTime",
                "EvadeChance", "EffectiveBlockChance", "ManaPerSecondCost", "Speed",
                *(f"{t}TakenHit" for t in ("Physical", "Fire", "Cold", "Lightning", "Chaos")))


# The boss's hits that Chill / Freeze you, per type (MECHC48; the manager's ruling of 19:10 20 Nov on RESEARCH37 R37-17,
# research/pinnacle-boss-hit-cadence-on-player.md). Before, both read the DEFENCE cadence (enemySkillTime: the faster of
# PoB's 0.7 s and HITS_EVERY, 0.51 s on the worst case) x land_share x the type's share: an upper bound on hits used for a
# DPS penalty. Now:
# - EVENTS, not strikes: one per damaging USE (bosses.HIT_EVERY, LOW): a use's strikes land within ~1 s (MECHD5's .ao
#   patterns 0.01-0.6 s apart) and a Chill / Freeze on players lasts 2 s (BaseChillDurationPlayer 2, FreezeDurationPlayer
#   2000), so extra strikes only refresh. x the type's OWN share of the boss's damaging skills (type_shares: 0 Cold for 7
#   of the 12 presets, so 0 there; checked: boss_skill_hits.json's rows for all 12, R37-17's guides agree), x PoB's
#   scored enemySkillTime over the preset's defence cadence (the build's Slows on the boss: the same ratio as
#   benefit_skill_time).
# - Landing per type (kind_land_share): the share of that type's skills that are red-flash (no evade / block roll), the
#   rest evaded and blocked as PoB rolls them. R37-17 said none of the Cold hits is red-flash; the skill rows say
#   Olroth's GAOlrothGroundSlamInitial is (red, Physical 1,114 + Cold 1,114 converted): 1 of his 16 Cold rows (checked:
#   every row of boss_skill_hits.json with a Cold part, the 12 presets; no other Cold row is red). Evadable-but-unblockable
#   skills (RED_FLASH's middle count: the Bodach's Head Slam, Kulemak's Teleport Slam and its trigger) carry no Cold
#   (checked: their rows' parts) and are read as blockable per type.
# - x (1 - the share you dodge) on the hits that aren't red-flash: the user's rate, 50% (the bullet below; before the
#   user's answer the manager's 19:10 had dodge 0 as the headline).
# - Rows (chill_and_freeze_on_you): the dodge rows, one per strike (HITS_EVERY) and the old defence cadence, as upper rows.
# - The worst-case pinnacle mixes the Red Queen's 0.51 s cadence (no Cold) with the pooled 15% Cold share: its Chill /
#   Freeze reads the worst REAL preset instead (chill_and_freeze_on_you, each read as real_readings gives it: its own
#   cadence, shares and hit sizes; Olroth on most builds, 21.8 Cold uses a minute). A direct call on the worst case keeps
#   the old defence reading (only that function resolves it).
# - Downtime windows (immune phases, burrows) aren't counted: the player can't hit the boss then either, so they cancel
#   out of DPS (R37-17).
# - (MECHC48, THE USER'S RULING, 20 Nov 21:50, the manager's card "About half": against the pinnacles' telegraphed Cold
#   attacks a melee player dodges about half) 50% dodged is the headline, "dodge none" and "three in four" the rows.
#   Red-flash hits are never dodged (RESEARCH38's R37-17 correction: Olroth's GAOlrothGroundSlamInitial, red-flash and
#   50% Cold, can't be dodge-rolled): the dodge applies to the rest only (kind_land_parts).
ON_YOU_DODGE = 0.5  # the headline share of the boss's dodgeable hits you dodge: the user's rate, 20 Nov
ON_YOU_DODGE_ROWS = (0.0, 0.75)
ON_YOU_DODGE_HOW = "the user's rate, 20 Nov"
ON_YOU_CADENCES = ("use", "strike", "defence")
POB_SKILL_TIME = 0.7  # (PoB's enemySpeed placeholder: the old fallback)


def kind_land_parts(stats: dict, kind: str) -> tuple[float, float]:
    """(the share of the boss's `kind` hits that are red-flash and always land, the share that lands of the rest: evaded
    and blocked as PoB rolls them, before any dodge). A preset without rows (the worst case): (0, land_share)."""
    rows = [r for r in skill_hits(boss_name(stats)) if kind in (r.get("hit") or {})]
    if not rows:
        return 0.0, land_share(stats)
    red = sum(1 for r in rows if r.get("red")) / len(rows)
    evade, block = evade_block(stats)
    return red, (1 - red) * (1 - evade) * (1 - block)


def kind_land_share(stats: dict, kind: str) -> float:
    """The share of the boss's `kind` hits that land (kind_land_parts summed: no dodge)."""
    return sum(kind_land_parts(stats, kind))


def ailment_hits(stats: dict, kind: str) -> tuple[float, str]:
    """(landed `kind` hits a second that can Chill / Freeze you, how) at stats["_onyou_cadence"] ("use", the headline;
    "strike"; "defence", the old reading) and stats["_onyou_dodge"] (the share dodged, ON_YOU_DODGE). Before your
    avoidance. The comment above."""
    from builder.search import bosses

    name = boss_name(stats)
    cadence = stats.get("_onyou_cadence") or "use"
    if name == _worst() or name not in bosses.HIT_EVERY:
        cadence = "defence"  # (resolved to the worst real preset by chill_and_freeze_on_you only)
    dodge = min(max(float(stats.get("_onyou_dodge", ON_YOU_DODGE)), 0.0), 1.0)
    share = type_shares(name).get(kind, 0.0)
    scored = number(stats.get("enemySkillTime"))
    if cadence == "defence":
        every = scored or POB_SKILL_TIME
        land = land_share(stats)
        dodge = 0.0  # (the old reading as it was: no dodge)
        how = f"one hit every {every:.2f} s (the defence cadence: an upper bound on hits)"
    else:
        slowed = scored / bosses.defence_hit_every(name) if scored > 0 else 1.0
        base = bosses.HIT_EVERY[name] if cadence == "use" else bosses.HITS_EVERY[name]
        every = base * slowed
        red, rest = kind_land_parts(stats, kind)
        land = red + rest * (1 - dodge)  # (red-flash hits are never dodged)
        how = (f"{name}: one damaging {'use' if cadence == 'use' else 'strike'} every {base:g} s"
               + (f" x{slowed:.2f} (your Slows on it)" if abs(slowed - 1) > 1e-9 else "")
               + (" (LOW: the tables' cadence; a use's strikes land within ~1 s and only refresh a 2 s Chill / Freeze)"
                  if cadence == "use" else " (every strike counted: an upper bound)"))
    rate = share / every * land if every > 0 else 0.0
    how += (f", {share:.0%} {kind}, {land:.0%} landing"
            + ("" if cadence == "defence" else
               f" ({dodge:.0%} of the dodgeable ones dodged: {ON_YOU_DODGE_HOW if dodge == ON_YOU_DODGE else 'a row'}; "
               "red-flash hits never)"))
    return rate, how


def boss_chill_on_you(stats: dict, texts: list[str], chilled_box: bool = False) -> tuple[float, list[dict]]:
    """(ActionSpeed INC % to add: negative for the Slow, positive reversed; play rows) for the Chills on you (the comment
    above). `stats`: PoB's CHILL_FIELDS and "_boss"; `texts`: the build's lines."""
    from builder.search import recovery

    lines = [t.strip() for t in texts]
    reversed_ = any(CHILL_REVERSED.match(t) for t in lines)
    all_types = any(ALL_DAMAGE_CHILLS.match(t) for t in lines)
    if chilled_box:
        return 0.0, []
    name = boss_name(stats)
    cadence_how = ailment_hits(stats, "Cold")[1]  # (MECHC48: the boss's own per-use cadence, R37-17)
    threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
    avoid = min(max(number(stats.get("ChillAvoidChance")), 0.0), 100.0) / 100
    effect = max(number(stats.get("SelfChillEffect")), 0.0) / 100 if "SelfChillEffect" in stats else 1.0
    potency = 1 + sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                      for t in lines if (m := recovery.SLOW_POTENCY.match(t))) / 100
    unaffected = any(recovery.NO_SLOW_POTENCY.match(t) for t in lines)
    scale = effect * max(potency, 0.0)

    def magnitude(hit: float) -> float:
        if threshold <= 0 or hit <= 0:
            return 0.0
        m = 100 * hit / threshold * scale
        return min(m, CHILL_MAX_ON_PLAYER) if m >= 100 * CHILL_FLOOR else 0.0

    sources = []  # (rate a second, magnitude, what)
    bleeding = (stats.get("_bleeding") or (0.0, ""))[0] if any(BLEEDING_CHILLS.match(t) for t in lines) else 0.0
    for kind, share in type_shares(name).items():
        part = 1.0 if kind == "Cold" or all_types else bleeding  # (MECHC42: Venopuncture's, while you're Bleeding)
        if share <= 0 or part <= 0:
            continue
        key = f"{kind}TakenHit"
        hit = max(number(stats.get(key)) if key in stats else hit_sizes(name).get(kind, 0.0), 0.0)
        m = magnitude(hit)
        rate = ailment_hits(stats, kind)[0] * (1 - avoid) * part
        if m > 0 and rate > 0:
            sources.append((rate, m, f"the boss's {kind} hits ({hit:,.0f} after your mitigation, {rate * 60:.1f} a minute"
                                     + (f", while you're Bleeding {part:.0%} of the fight: "
                                        f"{(stats.get('_bleeding') or (0, ''))[1]}" if part < 1 else "") + ")"))
    if all_types:
        taken, uses, _ = recovery.cost_damage(lines, stats)
        if taken > 0 and uses > 0:
            hit = taken / uses
            m = magnitude(hit)
            if m > 0:
                sources.append((uses * (1 - avoid), m, f"Scold's Bridle's self-hit on each use ({hit:,.0f} a use, "
                                                         f"{uses:.2f} uses a second)"))
    rate = sum(r for r, _, _ in sources)
    if rate <= 0:
        if reversed_:
            return 0.0, [{"label": "Reversed Chill on you (speed)*", "value": "0: nothing Chills you",
                          "basis": (f"no hit reaches the 30% of your {threshold:,.0f} Ailment Threshold a Chill needs"
                                    + ("" if all_types else " (without the all-damage line only Cold hits Chill)")
                                    + "; without it: 0"),
                          "confidence": "MEDIUM (PoB's magnitude formula)", "play": ""}]
        # (MECHC39, the train 78 measurement: 2b75b / 2b75c had no row at all; a 0 is said, never silent)
        why = (f"you avoid every Chill ({100 * avoid:g}% Chill avoidance)" if avoid >= 1.0 else
               f"no hit of the boss's reaches the 30% of your {threshold:,.0f} Ailment Threshold a Chill needs"
               + ("" if all_types else " (only Cold hits Chill you)"))
        return 0.0, [{"label": "The boss Chills you*", "value": "0: no Chill on you", "basis": why,
                      "confidence": "MEDIUM (PoB's magnitude formula)", "play": ""}]
    mean = sum(r * m for r, m, _ in sources) / rate
    seconds = CHILL_SECONDS_PLAYER * (number(stats.get("SelfChillDuration")) / 100 if "SelfChillDuration" in stats
                                      else 1.0)
    immune = max((float(m.group(1)) for t in lines if (m := CHILL_IMMUNE_AFTER.match(t))), default=0.0)
    if immune > 0:
        share = min(1.0, seconds / (max(immune, seconds) + 1 / rate))
        share_how = f"{seconds:.3g} s each, then no Chill for {immune:g} s after each: {share:.0%} of the fight"
    else:
        share = 1 - math.exp(-rate * seconds)
        share_how = f"{seconds:.3g} s each (2 s on players x your Chill duration on you): Chilled {share:.0%} of the fight"
    if unaffected:
        return 0.0, [{"label": "The boss Chills you*", "value": "none: your speed is unaffected by Slows",
                      "basis": f"Chilled {share:.0%} of the fight, no speed change (reversed: the lower reading, "
                               "UNVERIFIED); without it: 0", "confidence": "MEDIUM", "play": ""}]
    average = share * mean
    stats["_chill_share"] = share  # (MECHC47: speed_from_chill_and_freeze's overlap with the Freeze)
    sign = 1.0 if reversed_ else -1.0
    basis = ("; ".join(f"{what}: a {m:.0f}% Chill" for _, m, what in sources) + f" ({cadence_how})"
             + f"; {rate * 60:.1f} Chills a minute x "
             f"{1 - avoid:.0%} not avoided, {share_how}; magnitude 100% x hit / your {threshold:,.0f} Ailment Threshold x "
             f"{effect:.0%} (your effect of Chill on you)" + (f" x {potency:.0%} (Slowing Potency on you)" if potency != 1
                                                               else "")
             + f", at least 30% and at most 50% (KeywordPopups Chill), {mean:.1f}% on average: "
             + f"{sign * average:+.1f}% action speed on average, counted (PoB counts none: its Chilled box is off)")
    if reversed_:
        return average, [{
            "label": "Reversed Chill on you (speed)*", "value": f"+{average:.1f}% action speed on average (Chilled "
                                                                 f"{share:.0%} of the fight, +{mean:.0f}% while Chilled)",
            "basis": basis + ("; every type's hit counts (All Damage taken from Hits Contributes to Magnitude of Chill)"
                              if all_types else "; only Cold hits Chill (no all-damage line)")
                     + ". R31-1: the effect of Chill on you is reversed, so a Chill raises your action speed by its "
                       "magnitude; without it: +0%",
            "confidence": "MEDIUM (the magnitude formula; PoB's INC reading, the game's may be 'more': UNVERIFIED)",
            "play": (f"Chills on you speed you up: +{mean:.0f}% action speed while Chilled, {share:.0%} of the fight "
                     f"(+{average:.1f}% on average); without it: +0%"
                     + (" (Scold's Bridle's self-hits Chill you on every cast)" if any("Scold" in w for _, _, w in sources)
                        else ""))}]
    return -average, [{
        "label": "The boss Chills you*", "value": f"-{average:.1f}% action speed on average (Chilled {share:.0%} of the "
                                                  f"fight, -{mean:.0f}% while Chilled)",
        "basis": basis + "; without it: -0%",
        "confidence": "MEDIUM (the magnitude formula: PoB's linear reading; the boss's hit rate)",
        "play": (f"the boss's Cold hits Chill you {share:.0%} of the fight: -{mean:.0f}% action speed while Chilled "
                 f"(-{average:.1f}% on average); Chill avoidance, a larger Ailment Threshold or reduced Effect of Chill "
                 "on you lowers it; without it: -0%")}]


def ailment_rate(stats: dict, ailment: str, land: float) -> tuple[float, str]:
    """Times per second the boss inflicts `ailment` (Ignite, Shock, Freeze, Bleed, Poison) on you, and how. `land`: the
    boss's landed hits per second; stats: PoB's <Type>TakenHit, AilmentThreshold, <Ailment>AvoidChance, Life, "_boss"."""
    name = boss_name(stats)
    if land <= 0:
        return 0.0, "no boss hit lands: no source, 0"
    avoid = min(max(number(stats.get(f"{ailment}AvoidChance")), 0.0), 100.0) / 100
    if ailment == "Poison":
        return 0.0, ("no pinnacle boss has a Poison chance (checked: research #60 / #67 over every boss skill, variety "
                     "mod, buff and ground; poe2db): no source, 0")
    if ailment == "Bleed":
        share = _bleed_share(name)
        if share <= 0:
            return 0.0, (f"{name} has no Bleeding chance (checked: research #60 / #67; Olroth's attacks are the one "
                         "pinnacle source): no source, 0")
        kept, kept_how = deflect_kept(stats, "Bleed")
        rate = land * share * (1 - avoid) * kept
        return rate, (f"{land:.2f} landed hits/s x {share:.1%} that Bleed (Olroth's ThrowBack, one of its skills: LOW) x "
                      f"{1 - avoid:.0%} not avoided{kept_how}: {rate * 60:.1f} a minute")
    kind = AILMENT_TYPE[ailment]
    share = type_shares(name)[kind]
    if share <= 0:
        return 0.0, f"{name} deals no {kind} hits: no source, 0"
    if avoid >= 1:
        return 0.0, {"Ignite": "you can't be Ignited", "Shock": "you can't be Shocked",
                     "Freeze": "you can't be Frozen"}[ailment] + " (PoB's avoidance 100%): 0"
    if ailment == "Ignite":
        chance, how = ignite_chance(stats, land)
    else:
        key = f"{kind}TakenHit"
        taken = max(number(stats.get(key)) if key in stats else hit_sizes(name)[kind], 0.0)
        threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
        if threshold <= 0:
            chance, how = 1.0, f"no Ailment Threshold read: every landed {kind} hit taken to {ailment} (LOW)"
        elif ailment == "Shock":
            factor = _factor(name, SHOCK_FACTORS, kind)
            chance = min(1.0, taken / threshold * SHOCK_PER_THRESHOLD * factor * afflicted_scale(stats))
            how = (f"a {taken:,.0f} Lightning hit against your {threshold:,.0f} Ailment Threshold: 1% per 4% dealt x "
                   f"{factor:.2f} from the boss's Shock chance stats = {chance:.0%} to Shock (MEDIUM-LOW)")
        else:
            factor = _factor(name, FREEZE_FACTORS, kind)
            build = taken / threshold * FREEZE_SCALE_PLAYER * factor
            chance = 1.0 if build >= 1 else 0.0  # (MECHC18) the lower reading: one hit must fill the buildup
            kept = land * share * min(1.0, build) * (1 - avoid)
            how = (f"a {taken:,.0f} Cold hit builds {build:.0%} Freeze against your {threshold:,.0f} Ailment Threshold "
                   f"(x{FREEZE_SCALE_PLAYER:g}, x{factor:.2f} from the boss's buildup stats); counted: a single hit must "
                   f"fill it, so {'every' if build >= 1 else 'no'} Cold hit Freezes (whether buildup carries over "
                   f"between hits on a player is UNVERIFIED; with buildup kept: {kept * 60:.1f} Freezes a minute)")
    rate = land * share * chance * (1 - avoid)
    return rate, (f"the boss's {land * share:.2f} landed {kind} hits/s ({share:.0%} of its hits) x {chance:.0%} ({how}) "
                  f"x {1 - avoid:.0%} not avoided: {ailment} {rate * 60:.1f} times a minute")


# ---------------------------------------------------------------------------------------------------------------------
# The boss's Slows on you, per second, for charms "Used when you are affected by a Slow" (MECHC18; CLAUDE.md's no-flat-
# rates rule: before, one per 10 s on every boss). Two sources the data gives:
# - Chill: "Chill is an Ailment that Slows" (KeywordPopups Chill: HIGH that it is a Slow). "Cold damage from Hits can
#   Chill by default ... Chills smaller than 30% will be ignored"; magnitude "depends on the Chilling damage dealt
#   relative to the target's Elemental Ailment Threshold" (the formula isn't published: PoB's 100% x dealt / threshold,
#   MEDIUM; research chill-floor-in-pob.md). So a landed Cold hit Chills you when it deals at least 30% of your Ailment
#   Threshold (less PoB's ChillAvoidChance). A Chill lasts 2 s on players (KeywordPopups; PoB Misc.lua
#   BaseChillDurationPlayer 2): a hit while you're still Chilled refreshes it, not a new "become affected" event, so the
#   new Slows are c x e^(-2c) for c Chilling hits a second (Poisson hits). The Arbiter of Ash's Chilled Ground isn't
#   counted (the player steps out of grounds: GROUNDS_NOTE).
# - The boss skills that Slow (RESEARCH14 #67, pinnacle-debuffs-from-buffs.md; RESEARCH13 #60 for the Maim stats), at
#   one use per the skill's cooldown (GrantedEffectsPerLevel Cooldown, game 26476662; the cooldown is the fastest the
#   boss can use it, as MECHD3's cadence model: LOW for the AI's real use). Maim is a Slow (KeywordPopups Maim: "30%
#   Slower Movement Speed"); a Maim needs its hit to land (x land_share). Zarokh's Temporal Ball Slows through a buff
#   ("Zarokh's Influence: You Are Being Slowed by Zarokh's Temporal Ball", SilentSpiresApparition_slow, HIGH) round its
#   ball, not a hit: each ball taken to Slow you once (LOW). Not counted (each leaves a lower reading, said): Olroth's
#   "Exterminating Cold" and "Enervation" buffs (their skills live in .ao scripts, unseen), Atziri's clone's Blood Spear
#   (EASAtziriBloodSpearClone, 9 s; whether it Maims is unread), the ground Slows (the Bodach's, the King's tar swamp,
#   the Trialmaster's anomaly), the Trialmaster's TetherSpike vines (no cooldown read), your Maim avoidance (not read).
# A boss with neither a Cold hit nor a Slow skill (the Arbiter of Ash, the Arbiter of Divinity, the King in the Mists, the
# Bodach: 4 of the 12 real presets) has no rate the data gives: slow_rate returns None and the charm keeps its labelled LOW
# flat rate (a flat number where the game decides it; for the manager).
CHILL_FLOOR = 0.30  # KeywordPopups Chill: "Chills smaller than 30% will be ignored": HIGH (the 30%), MEDIUM (magnitude)
CHILL_SECONDS_PLAYER = 2.0  # KeywordPopups Chill: "lasts 2 seconds on players"; PoB BaseChillDurationPlayer 2: HIGH
# per boss: (cooldown in s, needs its hit to land, what): game 26476662 GrantedEffectsPerLevel; RESEARCH13 #60 / RESEARCH14 #67
SLOW_SKILLS = {
    "Zarokh, the Temporal": ((16.0, False, "Temporal Ball (MPSSilentSpiresApparitionTemporalChainsLightning, 16 s "
                                           "cooldown; its buff Slows you round the ball, each ball taken once: LOW)"),),
    "Olroth, Origin of the Fall": (
        (18.0, True, "ThrowOut's Maim (MPWOlrothThrowOut, 18 s)"),
        (12.0, True, "Dash's Maim (EAAOlrothDash1-3, 12 s each, taken as one combo every 12 s: LOW; 3 if each part is "
                     "its own use)")),
    "Atziri, the Red Queen": ((22.0, True, "Blood Spear's Maim (EASAtziriBloodSpear, 22 s)"),),
    "Atziri, the Fell Serpent": ((35.0, True, "Blood Spear's Maim (EASAtziri2BloodSpear, 35 s; the '2' read as this "
                                              "phase: LOW)"),),
}


def _slow_skills(name: str, land: float, maim_kept: float = 1.0, kept_how: str = "") -> tuple[float, list[str]]:
    """Slows a second from the boss's Slow skills and their descriptions (`land`: the share of its hits that land;
    `maim_kept`: the share of landed hits that can still Maim you, deflect_kept)."""
    if name == _worst():
        from builder.search import bosses
        rows = [_slow_skills(n, land, maim_kept, kept_how) for n in bosses.REAL if n in SLOW_SKILLS]
        return max(rows, default=(0.0, []), key=lambda r: r[0])
    rate, parts = 0.0, []
    for cooldown, hit, what in SLOW_SKILLS.get(name, ()):
        maim = hit and "Maim" in what
        r = (land * (maim_kept if maim else 1.0) if hit else 1.0) / cooldown
        rate += r
        parts.append(f"{what}{f' x {land:.0%} landing' if hit else ''}{kept_how if maim else ''}: {r * 60:.1f} a minute")
    return rate, parts


def slow_rate(stats: dict, land: float) -> tuple[float | None, str]:
    """New Slows a second the boss puts on you (a Chill from its Cold hits, its Slow skills), and how; None where the
    boss has neither (the caller keeps its labelled flat rate). `land`: the boss's landed hits a second; stats as
    ailment_rate's, with ChillAvoidChance."""
    name = boss_name(stats)
    share = type_shares(name)["Cold"]
    skills, parts = _slow_skills(name, land_share(stats), *deflect_kept(stats, "Maim"))
    if share <= 0 and not parts:
        return None, f"{name} has no Cold hit and no Slow skill found (RESEARCH14 #67; its ground Slows aren't counted)"
    avoid = min(max(number(stats.get("ChillAvoidChance")), 0.0), 100.0) / 100
    chill_how = f"{name} deals no Cold hits: no Chill"
    onsets = 0.0
    if share > 0:
        taken = max(number(stats.get("ColdTakenHit")) if "ColdTakenHit" in stats else hit_sizes(name)["Cold"], 0.0)
        threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
        dealt = taken / threshold if threshold > 0 else 1.0
        chills = land * share * (1 - avoid) if dealt >= CHILL_FLOOR else 0.0
        onsets = chills * math.exp(-CHILL_SECONDS_PLAYER * chills)
        if chills > 0:
            tail = (f"at least 30%: {land * share:.2f} landed Cold hits/s x {1 - avoid:.0%} not avoided, a new Chill only "
                    f"once the last 2 s one has worn off: {onsets * 60:.1f} a minute")
        elif dealt >= CHILL_FLOOR:
            tail = "you can't be Chilled"
        else:
            tail = "under the 30% a Chill needs: none"
        chill_how = f"Chill: a {taken:,.0f} Cold hit is {dealt:.0%} of your Ailment Threshold ({tail})"
    rate = onsets + skills
    return rate, "; ".join([chill_how, *parts]) + f": {rate * 60:.1f} Slows a minute"


# ---------------------------------------------------------------------------------------------------------------------
# Gains the boss's own actions trigger, on the worst-case pinnacle (MECHC19; AUDIT29 A29-1, an OVERCOUNT): charms "Used
# when you become Ignited / Shocked / Frozen / Stunned", "start Bleeding", "are affected by a Slow" or "take <Type> damage
# from a Hit", and Life / Energy Shield "when Stunned". The worst case's tables take the HIGHEST real preset's factor or
# share (_factor, _bleed_share, _slow_skills, hit_sizes, the pooled types), which is right for a defence but an overcount
# for a gain: before, Bleed charms read Olroth's rate though 11 of the 12 real presets never Bleed you (research #60 /
# #67), Shock charms the Arbiter of Divinity's x6, Freeze charms Olroth's x3. The user's hit-rate ruling (MECHD4: defences
# at the fast rate, "when hit" benefits at the slow one) gives the rule: a gain on the worst case reads the LOWEST real
# preset's rate. real_readings re-reads the build's stats as each real preset would give them:
# - its own boss name (its hit types, factors, red-flash shares, Slow skills, cadence);
# - PoB's scored enemySkillTime (the worst case's, the build's Slows on the boss in it) rescaled from the worst case's
#   defence cadence to that preset's (bosses.defence_hit_every), so each consumer's benefit pace is that preset's own;
# - each {Type}TakenHit scaled by that preset's hit of the type over the worst case's (0 for a type it doesn't deal):
#   linear, so Armour against a smaller Physical hit is understated (LOW-MEDIUM; only Stun and nothing ailment-related
#   reads Physical taken).
def real_readings(stats: dict) -> list[tuple[str, dict]]:
    from builder.search import bosses

    worst = _worst()
    sizes = mean_hits(worst)  # (MECHC31: the average hits PoB was given, config_vars)
    scored = number(stats.get("enemySkillTime")) or bosses.defence_hit_every(worst)
    out = []
    for name in bosses.REAL:
        own = mean_hits(name)
        read = {**stats, "_boss": name,
                "enemySkillTime": scored * bosses.defence_hit_every(name) / bosses.defence_hit_every(worst)}
        for t in TYPES:
            if f"{t}TakenHit" in stats:
                read[f"{t}TakenHit"] = number(stats[f"{t}TakenHit"]) * own[t] / sizes[t] if sizes[t] > 0 else 0.0
        out.append((name, read))
    return out


def gain_floor(stats: dict, read) -> tuple[float, str]:
    """(rate, how) of a gain the boss triggers: read(stats) on a named boss; on the worst-case pinnacle the lowest of
    read(each real preset's stats) (real_readings), naming that preset and the highest."""
    if _worst() is None or boss_name(stats) != _worst():
        return read(stats)
    rows = [(name, *read(s)) for name, s in real_readings(stats)]
    low = min(rows, key=lambda r: r[1])
    high = max(rows, key=lambda r: r[1])
    return low[1], (f"worst case: a gain counts the lowest real boss's rate (AUDIT29 A29-1), {low[0]}'s: {low[2]} "
                    f"(the highest, {high[0]}'s: {high[1] * 60:.1f} a minute, not counted)")


# "When hit" gains on the worst-case pinnacle (MECHC20; MECHC19's Handover, the same rule as AUDIT29 A29-1): Rage,
# charges, Onslaught, debuffs on the boss, meta Energy, Iron Ward, Deadly Resolve, "Hit Recently" and the recovery lines
# read the boss's landed hits a second. On the worst case that was land_share at its HIGHEST real red-flash share (the
# Arbiter of Divinity's, more hits land) over the slowest real cadence: a mix no real boss has, above the lowest real
# boss's rate. gain_hits gives a gain the lowest real preset's landed rate (real_readings: each preset's own shares and
# cadence); a named boss reads its own, as before. Defences (damage taken, recharge resets) keep the highest share.
def gain_hits(stats: dict) -> tuple[float, str]:
    """(the boss's landed hits a second at the "when hit" benefit pace, how) for a GAIN its hits give; on the worst-case
    pinnacle the lowest real preset's (gain_floor)."""
    from builder.search import bosses

    def one(read: dict) -> tuple[float, str]:
        rate = land_share(read) / bosses.benefit_skill_time(read)
        return rate, f"{rate:.2f} landed hits/s"

    return gain_floor(stats, one)


def gain_hit_recently(stats: dict, seconds: float, uptime: float) -> bool:
    """At least one landed boss hit in any `seconds` with chance `uptime` (PoB's enemySkillTime, the scored cadence, as
    conditions._hit_recently reads it); on the worst case only if the lowest real preset's chance reaches it too."""
    def one(read: dict) -> tuple[float, str]:
        every = number(read.get("enemySkillTime")) or 0.7
        return 1 - (1 - land_share(read)) ** (seconds / every), ""

    return gain_floor(stats, one)[0] >= uptime



# Chaos Inoculation and your Ailment Threshold (MECHC40; found re-checking the Freeze's x0.81 on 2b75d for the manager,
# 18 Nov 17:55). Patch 0.5.0 (2026-05-29, Bug Fixes; the newest entry: grep_notes "Ailment Threshold"): "Fixed a bug
# where Chaos Inoculation overriding maximum life to one incorrectly affected calculation of stun and ailment thresholds
# for the player as well." PoB still has that bug for the Ailment Threshold: its base is 50% of output.Life
# (CalcSetup 73, PercentStat), and CalcDefence 124 sets output.Life = 1 under CI first, so a CI build reads 0.5 plus
# its other bases (measured: 2b75d, PoB AilmentThreshold 0.5, Frozen by every Cold hit; an OVERCOUNT of every ailment
# and buildup on you: the boss's Chill, Shock, Ignite, Freeze). Fixed here: AilmentThreshold BASE + 50% x (the Life
# you'd have without CI - 1), the Life from PoB's own what-if without the keystone (nodeDeltas: one more calculation per
# re-apply, CI builds only); PoB's increases scale it as the rest of the base, as the game's would. (PoB's Stun
# Threshold under CI already uses "Life before Chaos Inoculation", but only the BASE Life sum without its increases,
# CalcDefence 2756: left to the stun model's owner, named in the row.) MEDIUM-HIGH (the patch note's wording; the
# threshold's base share of Life unchanged by CI).
CI_KEYSTONE = "Chaos Inoculation"
CI_THRESHOLD_TAG = "CIAilmentThreshold"


def ci_ailment_threshold(life_without_ci: float) -> tuple[list[dict], dict]:
    """(mods, row) for a Chaos Inoculation build: the Ailment Threshold's Life part from the Life without CI."""
    from builder.search import models

    add = max(0.5 * (life_without_ci - 1), 0.0)
    mods = [models.raw_mod("AilmentThreshold", "BASE", add, tag=CI_THRESHOLD_TAG)] if add > 0 else []
    return mods, {"label": "Chaos Inoculation: your Ailment Threshold from your Life before it*",
                  "value": f"+{add:,.0f} base Ailment Threshold (50% of {life_without_ci:,.0f} Life, not of 1)",
                  "basis": ("patch 0.5.0 fixed Chaos Inoculation's Life of 1 lowering the player's stun and ailment "
                            "thresholds; PoB still reads 50% of 1 Life: added 50% of the Life you'd have without the "
                            "keystone (PoB's own calculation without it), then your increases (the Stun Threshold's "
                            "own fix: ci_stun_threshold); without it: PoB's threshold"),
                  "confidence": "MEDIUM-HIGH (the patch note)", "play": ""}


# Chaos Inoculation and your Stun Threshold (MECHC41; MECHC40's named UNDERCOUNT). The same patch 0.5.0 fix ("... stun
# and ailment thresholds for the player as well"). PoB's Stun Threshold under CI takes "Life before Chaos Inoculation"
# as only the BASE Life sum, without its increases (CalcDefence 2756-2758: modDB:Sum("BASE", nil, "Life")), so a CI build's
# threshold reads low: an UNDERCOUNT of the threshold, an OVERCOUNT of the Heavy Stuns, Light Stuns and stun-fed rows on
# you. Fixed here as a StunThreshold MORE of (PoB's Stun Threshold without the keystone / with it) - 1, both from PoB's own
# what-if without CI (the nodeDeltas call the Ailment Threshold fix makes; the keystone's only stats are "Maximum Life is
# 1" and "Immune to Chaos Damage and Bleeding", TreeData 0_5): this MORE reaches PoB's threshold (CalcDefence 2767:
# modDB:More("INC", nil, "StunThreshold") passes a string as the config, so its names are (nil, "StunThreshold") and
# the MORE mods of that name apply; probed in tests/test_mechc40.py), and the ratio is the same with this MORE in both
# what-ifs, so it settles in one round. What else differs without CI: PoB sets Full Life under CI (CalcDefence 125), so
# a "while on Full Life" Stun Threshold line would read off in the what-if (named; none known). MEDIUM-HIGH.
CI_STUN_TAG = "CIStunThreshold"


def ci_stun_threshold(without_ci: float, with_ci: float) -> tuple[list[dict], dict | None]:
    """(mods, row) for a Chaos Inoculation build: PoB's Stun Threshold raised to its value without the keystone."""
    from builder.search import models

    if with_ci <= 0 or without_ci <= with_ci * (1 + 1e-9):
        return [], None
    more = (without_ci / with_ci - 1) * 100
    return [models.raw_mod("StunThreshold", "MORE", more, tag=CI_STUN_TAG)], {
        "label": "Chaos Inoculation: your Stun Threshold from your Life before it*",
        "value": f"{with_ci:,.0f} -> {without_ci:,.0f} Stun Threshold (x {without_ci / with_ci:.2f})",
        "basis": ("patch 0.5.0 fixed Chaos Inoculation's Life of 1 lowering the player's stun and ailment thresholds; "
                  "PoB counts only your base Life before it, without its increases: raised to PoB's own threshold "
                  "without the keystone; without it: PoB's threshold"),
        "confidence": "MEDIUM-HIGH (the patch note)", "play": ""}


# The boss's Freeze on you (MECHC39; found by MECHC38, an OVERCOUNT of DPS like the Chill: nothing counted your time
# Frozen; RESEARCH31 R31-8 / R31-8b, research/freeze-on-players.md). A Freeze lasts 2 s on players (GameConstants
# FreezeDurationPlayer 2000: HIGH), x PoB's SelfFreezeDuration. Buildup: each landed Cold hit adds hit after your
# mitigation / your Freeze Threshold x FreezeDamageScalePlayer 2.0 (x the boss's own Freeze buildup stats,
# FREEZE_FACTORS); the Freeze Threshold is your Ailment Threshold (PoB's AilmentThreshold, else half your Life) x your
# "N% increased / reduced Freeze Threshold" lines (PoB parses them, FreezeThreshold INC, but outputs no threshold);
# buildup carries over between hits (MEDIUM), with no decay (UNVERIFIED: no constant; a decay would mean fewer
# Freezes). After a Freeze your Freeze Threshold is +500% for 14 s (GameConstants FreezeThresholdModifier 500,
# FreezeModifierDuration 14, no Player twin: MEDIUM-LOW that it applies to players): the HEADLINE, with a "no
# protection" row. Hits taken while Frozen add nothing (the lower reading). Hits arrive at the boss's landed rate x
# its Cold share x (1 - Freeze avoidance), evenly (MEDIUM). Frozen = no action: the share of the fight Frozen cuts your
# action speed (added with the Chill's, PoB's form; minions and totems act on). PoB's "Are you Frozen?" box
# ticked: nothing added. "Your speed is unaffected by Slows" doesn't stop a Freeze, but PoB drops every negative
# action speed under it, so the Freeze isn't counted there (an OVERCOUNT kept, named in the row).
FREEZE_SECONDS_PLAYER = 2.0  # GameConstants FreezeDurationPlayer 2000 ms (HIGH)
FREEZE_PROTECTION = 6.0  # x the threshold after a Freeze: FreezeThresholdModifier 500 read as +500% (MEDIUM-LOW)
FREEZE_PROTECTION_SECONDS = 14.0  # FreezeModifierDuration (MEDIUM-LOW that it applies to players)
FREEZE_THRESHOLD_LINE = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Freeze Threshold$", re.I)
FREEZE_FIELDS = ("FreezeAvoidChance", "SelfFreezeDuration", "AilmentThreshold", "Life", "enemySkillTime",
                 "EvadeChance", "EffectiveBlockChance", "ColdTakenHit")


FREEZE_HIT_SPREAD = 0.25  # (MECHC40) the Cold hits' size spread, +-25%: MEDIUM-LOW (the presets' Cold skills differ far
# more, 414-2,998 on Olroth, but carry no use rates; a damage roll alone is narrower)


def _hits_needed(n: float, spread: float = FREEZE_HIT_SPREAD) -> float:
    """The Cold hits a Freeze needs on average when `n` hits of the average size would just fill the bar: the stepped
    count ceil(n x u) (the bar empties when you're Frozen, so nothing carries past a Freeze and equal hits need exactly
    ceil(n)), averaged over the hit size u, uniform on 1 +- spread (MEDIUM-LOW). At least one hit. Smooth in n, and
    ceil(n) as the spread goes to 0."""
    if spread <= 0:
        return max(math.ceil(n - 1e-9), 1)
    low, high = 1 - spread, 1 + spread
    total, k = 1.0, 1  # (k = 0: the first hit always counts)
    while k / max(n, 1e-12) < high:  # P(n x u > k) adds one more hit
        total += min(max((high - k / n) / (high - low), 0.0), 1.0)
        k += 1
    return total


def _freeze_cycle(rate: float, build: float, length: float, protection: float, window: float) -> float:
    """Seconds from the end of one Freeze to the next: `rate` Cold hits a second, each adding `build` (a share of the
    threshold) while unprotected; `protection` x the threshold for `window` s from the Freeze's start.
    SMOOTH (MECHC40): the buildup read as a flow (rate x build a second, rate x build / protection while protected)
    reaches the threshold after n hits' worth; the hits it takes are _hits_needed(n) (the stepped count averaged over
    the hits' size spread). (MECHC39's plain ceil jumped with each small change in Life, threshold or hit size; a first
    smoothing, n + a phased-in half hit, counted 1.48 hits where two equal hits are needed: 2b75a read Frozen 28.7%
    where its equal hits give 23%, the manager's 17:55 check.)"""
    if rate <= 0 or build <= 0:
        return math.inf
    guarded = max(window - length, 0.0) if protection > 1 else 0.0
    slow = build / protection
    if rate * slow * guarded >= 1:
        flow = 1 / (rate * slow)
    else:
        flow = guarded + (1 - rate * slow * guarded) / (rate * build)
    return _hits_needed(flow * rate) / rate


def boss_freeze_on_you(stats: dict, texts: list[str], frozen_box: bool = False,
                       hit_scale: float = 1.0) -> tuple[float, float, dict | None]:
    """(share of the fight Frozen, the headline with the protection; the same without it; the row) (the comment above).
    `stats`: PoB's FREEZE_FIELDS and "_boss"."""
    from builder.search import recovery

    if frozen_box:
        return 0.0, 0.0, None
    name = boss_name(stats)
    land, cadence_how = ailment_hits(stats, "Cold")  # (MECHC48: landed Cold hits a second at the boss's own cadence)
    share = type_shares(name).get("Cold", 0.0)
    avoid = min(max(number(stats.get("FreezeAvoidChance")), 0.0), 100.0) / 100
    lines = [t.strip() for t in texts]
    inc = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
              for t in lines if (m := FREEZE_THRESHOLD_LINE.match(t)))
    threshold = (number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))) * max(1 + inc / 100, 0.0)
    hit = max(number(stats.get("ColdTakenHit")) if "ColdTakenHit" in stats else hit_sizes(name).get("Cold", 0.0), 0.0)
    # (MECHC40) `hit_scale`: the Cold hit as the round's new boss's-Shock-on-you increase will make it (PoB's ColdTakenHit
    # was read under the last one: (1 + new / 100) / (1 + last / 100)); 1 once the Shock has settled, so the settled
    # Freeze is unchanged, but it settles in one round instead of three or four (POE2T3740's probe: the search
    # snapshotted a seed mid-settle and ended "below its start")
    hit *= max(hit_scale, 0.0)
    factor = _factor(name, FREEZE_FACTORS, "Cold")
    build = hit / threshold * FREEZE_SCALE_PLAYER * factor if threshold > 0 else 0.0
    rate = land * (1 - avoid)
    length = FREEZE_SECONDS_PLAYER * (number(stats.get("SelfFreezeDuration")) / 100 if "SelfFreezeDuration" in stats
                                      else 1.0)
    label = "The boss Freezes you*"
    if share <= 0 or rate <= 0 or build <= 0 or length <= 0:
        why = (f"{name} deals no Cold hits" if share <= 0 else "you avoid every Freeze" if avoid >= 1
               else "no boss hit lands" if land <= 0 else "no Cold hit builds Freeze")
        return 0.0, 0.0, {"label": label, "value": "0: never Frozen", "basis": why + "; without the model: 0 as well",
                          "confidence": "MEDIUM", "play": ""}
    gap = _freeze_cycle(rate, build, length, FREEZE_PROTECTION, FREEZE_PROTECTION_SECONDS)
    bare = _freeze_cycle(rate, build, length, 1.0, 0.0)
    immune = immune_after(lines, "Frozen")
    if immune > 0:
        # (MECHC40, COV43: Shackles' "You cannot be Frozen for N seconds after being Frozen": the next Freeze no sooner
        # than N s after this one's start, on the next hit after it (half a hit-interval on average); buildup kept
        # through it, the lower reading)
        floor = max(immune - length, 0.0) + 0.5 / rate
        gap, bare = max(gap, floor), max(bare, floor)
    up = length / (length + gap)
    up_bare = length / (length + bare)
    unaffected = any(recovery.NO_SLOW_POTENCY.match(t) for t in lines)
    basis = (f"{rate * 60:.1f} landed Cold hits a minute ({share:.0%} of the boss's hits, {1 - avoid:.0%} not avoided; "
             f"{cadence_how}), "
             f"each {hit:,.0f} after your mitigation x {FREEZE_SCALE_PLAYER:g}"
             + (f" x {factor:.2f} (the boss's buildup stats)" if factor != 1 else "")
             + f" against your {threshold:,.0f} Freeze Threshold = {build:.0%} buildup a hit, kept between hits "
             f"(MEDIUM; decay UNVERIFIED); Frozen {length:g} s each; then +500% Freeze Threshold for 14 s (MEDIUM-LOW): "
             f"a Freeze every {length + gap:.1f} s = Frozen {up:.0%} of the fight; without that protection: every "
             f"{length + bare:.1f} s = {up_bare:.0%}"
             + (f" (no Freeze for {immune:g} s after each: 'You cannot be Frozen for {immune:g} seconds after being "
                "Frozen')" if immune > 0 else "") + f". Frozen, you don't act: your skills' use rate x{1 - up:.3f} (minions "
             "and totems act on)")
    if unaffected:
        basis += ("; NOT counted here: \"your speed is unaffected by Slows\" makes PoB drop every negative action speed, "
                  "though a Freeze isn't a Slow (an OVERCOUNT kept)")
    row = {"label": label, "value": (f"Frozen {up:.1%} of the fight ({60 * up / length:.1f} Freezes a minute); "
                                     f"without the Freeze protection: {up_bare:.1%}"),
           "basis": basis, "confidence": "MEDIUM-LOW (the protection, the buildup carrying over)", "play": ""}
    return (0.0 if unaffected else up), (0.0 if unaffected else up_bare), row


def speed_from_chill_and_freeze(chill: float, frozen: float, action_mod: float, own: float,
                                chill_share: float | None = None) -> tuple[float, list[dict]]:
    """(ActionSpeed INC to add, rows): the Chill's average (`chill`, % action speed) and the share of the fight Frozen
    (`frozen`) as action speed. HEADLINE: PoB's additive reading, chill - 100 x frozen, added to your increased action
    speed, the Chill counted only while not Frozen. R31-13 (c) reads Slows as a separate multiplier (MEDIUM-LOW): that
    form is the row. (MECHC39: the
    multiplier, made as an INC sized from your other action speed, broke the search's what-if / commit agreement: a
    what-if changing your action speed kept the old INC; POE2T3725's Leylines and seed tests.) `action_mod`: PoB's
    ActionSpeedMod now, holding this tag's last value (`own`, %), for the row's numbers."""
    # (MECHC40: the Chill only over the time you aren't Frozen: Frozen, you don't act, so a Chill then slows nothing;
    # the Chill's average counted every second Chilled, Frozen ones too, an OVERCOUNT of the slow where both land; the
    # two taken as independent, MEDIUM. With no other action speed this equals the separate multiplier's figure)
    # (MECHC47, the manager's 17:15 action-speed check: an OVERCOUNT of the slow) the Cold hit that Freezes you Chills you
    # too (both from the same hit, the Chill's 2 s and the Freeze's 2 s on players: BaseChillDurationPlayer 2,
    # FreezeDurationPlayer 2000), so the time Frozen is time Chilled, not independent of it: the Chill slows only over
    # max(0, Chilled share - Frozen share) of the fight. `chill_share`: boss_chill_on_you's Chilled share; None keeps the
    # independent reading (the row). Probe POE2T4859 (2b75b, Pinnacle worst case): Chilled 41%, Frozen 16%: -20.4% x 0.84
    # = -17.1% independent, -50% x 25% = -12.6% overlapped. MEDIUM (a Chill from a hit while Frozen can outlast the Freeze
    # a little: not counted, the lower slow).
    independent = chill * (1 - frozen)
    if chill_share is not None and chill_share > 0:
        chill_part = chill / chill_share * max(0.0, chill_share - frozen)
    else:
        chill_part = independent
    inc = chill_part - 100 * frozen
    if abs(inc) < 1e-9:
        return 0.0, []
    base = 1 + ((action_mod - 1) * 100 - own) / 100
    more = base * (1 + (chill_part / (1 - frozen) if frozen < 1 else 0.0) / 100) * (1 - frozen)  # (MECHC47: overlapped)
    rows = [{"label": "The boss's Chill and Freeze on your action speed*",
             "value": f"{inc:+.1f}% action speed (added to your other action speed, PoB's form)",
             "basis": (f"the Chill's average {chill:+.1f}% over the time you're Chilled and not Frozen ({chill_part:+.1f}%; "
                       f"the freezing hit Chills too, so the Frozen time is Chilled time; as independent: "
                       f"{independent:+.1f}%) and Frozen {frozen:.1%} of the fight, added to your increased "
                       f"action speed: x{base + inc / 100:.3f} with your own x{base:.3f}. As a separate multiplier "
                       f"(\"Slows multiply\", R31-13: MEDIUM-LOW): x{more:.3f}"),
             "confidence": "MEDIUM-LOW (more vs increased)", "play": ""}]
    return inc, rows


def chill_and_freeze_on_you(chill_seen: dict, freeze_seen: dict, texts: list[str], chilled_box: bool = False,
                            frozen_box: bool = False, hit_scale: float = 1.0, own: float = 0.0
                            ) -> tuple[float, float, list[dict]]:
    """(MECHC48) (ActionSpeed INC to add, share of the fight Frozen, rows): boss_chill_on_you, boss_freeze_on_you and
    speed_from_chill_and_freeze at the boss's own per-use cadence with half its dodgeable hits dodged (the user's rate,
    ailment_hits' comment), with rows for none / 75% dodged, every strike and the old defence cadence. On the worst-case pinnacle the worst REAL preset
    (real_readings: the lowest action speed; ties to the most Cold uses), named, the others listed. `chill_seen` /
    `freeze_seen`: PoB's CHILL_FIELDS / FREEZE_FIELDS + ActionSpeedMod and "_boss"."""
    action_mod = number(freeze_seen.get("ActionSpeedMod")) or 1.0

    def run(chill: dict, freeze: dict, cadence: str = "use", dodge: float = ON_YOU_DODGE):
        c = {**chill, "_onyou_cadence": cadence, "_onyou_dodge": dodge}
        f = {**freeze, "_onyou_cadence": cadence, "_onyou_dodge": dodge}
        speed, chill_rows = boss_chill_on_you(c, texts, chilled_box)
        frozen, _, freeze_row = boss_freeze_on_you(f, texts, frozen_box, hit_scale)
        inc, speed_rows = speed_from_chill_and_freeze(speed, frozen, action_mod, own, c.get("_chill_share"))
        return inc, frozen, chill_rows + ([freeze_row] if freeze_row else []) + speed_rows

    name = boss_name(chill_seen)
    worst = _worst()
    if worst is not None and name == worst:
        frozen_reads = dict(real_readings(freeze_seen))
        picks = []
        for boss, chill in real_readings(chill_seen):
            inc, frozen, rows = run(chill, frozen_reads[boss])
            picks.append((inc, -ailment_hits(chill, "Cold")[0], boss, chill, frozen_reads[boss], frozen, rows))
        picks.sort(key=lambda r: (r[0], r[1]))
        inc, _, boss, chill, freeze, frozen, rows = picks[0]
        others = ", ".join(f"{b}: {i:+.1f}%" for i, _, b, *_ in picks[1:] if abs(i) > 1e-9)
        zero = sum(1 for i, *_ in picks[1:] if abs(i) <= 1e-9)
        pick_how = (f"the worst case reads the worst real preset, {boss} (its own cadence, Cold share and hit sizes; "
                    f"not the Red Queen's 0.51 s x the pooled Cold share): {inc:+.1f}% action speed"
                    + (f"; the others: {others}" if others else "") + (f"; {zero} at 0" if zero else ""))
        old_chill, old_freeze = chill_seen, freeze_seen  # (the old reading: the worst case's own pooled figures)
    else:
        boss, chill, freeze = name, chill_seen, freeze_seen
        inc, frozen, rows = run(chill, freeze)
        pick_how = ""
        old_chill, old_freeze = chill, freeze
    alt = [(f"{d:.0%} of its dodgeable hits dodged", run(chill, freeze, "use", d)[0]) for d in ON_YOU_DODGE_ROWS]
    alt.append(("every strike, not every use (HITS_EVERY: an upper bound)", run(chill, freeze, "strike")[0]))
    alt.append(("the old defence cadence (before MECHC48: enemySkillTime x the type's share"
                + (", the worst case's pooled share" if pick_how else "") + ")",
                run(old_chill, old_freeze, "defence")[0]))
    if abs(inc) < 1e-9 and all(abs(v) < 1e-9 for _, v in alt):
        return inc, frozen, rows
    base = 1 + ((action_mod - 1) * 100 - own) / 100
    rows = rows + [{
        "label": "The boss's Chill / Freeze cadence on you*",
        "value": (f"{inc:+.1f}% action speed (x{base + inc / 100:.3f}) at one Cold event a damaging use, "
                  f"{ON_YOU_DODGE:.0%} of the dodgeable ones dodged ({ON_YOU_DODGE_HOW})"),
        "basis": ((pick_how + "; " if pick_how else "") + "rows: " + "; ".join(
            f"{what}: {v:+.1f}% (x{base + v / 100:.3f})" for what, v in alt)
            + ". Downtime (immune phases, burrows) cancels out of DPS: not counted (R37-17)"),
        "confidence": "LOW (the tables' cadence, no measured hit rate); the dodged share: the user's rate, 20 Nov",
        "play": (f"dodge the boss's telegraphed Cold attacks (red-flash ones can't be dodged): about half dodged "
                 f"{inc:+.1f}% (the headline), three in four {alt[1][1]:+.1f}%; without dodging {alt[0][1]:+.1f}%")}]
    return inc, frozen, rows



# =====================================================================================================================
# MECH-D's section (MECHD7; the manager's brief of 21 Nov, item 1): the boss's ailments on you PER USE of its skills, one
# read for every ailment, for MECH-A's ailment-removal lines ("Remove a Damaging Ailment when you use a Command Skill",
# "Remove Ignite when you Warcry" and the like) and MECH-C's ailment-avoidance / threshold rows. Before, the boss's Ignite
# and Shock on you (boss_ailments_on_you, ailment_rate) read the DEFENCE cadence (enemySkillTime: the faster of PoB's 0.7 s
# and the boss's strikes, HITS_EVERY) with no dodge; only Chill / Freeze had moved to R37-17's per-use rule (MECHC48). For
# a reader that turns the rate into a GAIN (removing or avoiding an ailment), that is an OVERCOUNT. Here, for each of
# Ignite, Shock, Chill, Freeze, Bleed, Poison and Electrocute:
# - Events per USE (R37-17, research/pinnacle-boss-hit-cadence-on-player.md): ailment_hits' landed uses of the type a
#   second (bosses.HIT_EVERY, the type's own share of the boss's damaging skills, evaded / blocked as PoB rolls them, x
#   your Slows on the boss), x (1 - ON_YOU_DODGE) on the dodgeable ones: THE USER'S RULING (21 Nov 04:32, the manager's
#   cards): ONE dodge rate for every telegraphed attack, "they definitely all need the same hit rate", Fire, Lightning,
#   Cold and Physical alike; 50% the headline, labelled the user's rate (the user doubts it: research measures it from
#   fight videos), none / three in four as rows.
# - Red-flash hits (no evade / block / dodge roll): THE USER'S RULING (21 Nov 04:32, "Avoided, time cost counted": "those
#   attacks are often one-shot kills on a player with lower defenses"): the headline takes them as AVOIDED by moving out
#   (no hit, no ailment on you), the time it costs taken off attacking (red_flash_rate / red_flash_time_share, read by
#   MECH-C); "they land" stays a row for tanky builds.
# - A use's strikes: Ignite and Shock are a CHANCE per hit (KeywordPopups Ignite: Flammability "including the amount added
#   by that Hit"; Shock: "1% chance to Shock for every 4% of the target's Ailment Threshold"), so a use of k strikes
#   afflicts with 1 - (1 - p)^k, k = HIT_EVERY / HITS_EVERY (the boss's mean strikes a use, MECHD5's melee strikes: LOW,
#   one mean for all its skills), and Flammability counts every strike landed in the last 4 s. Chill (a hit of 30%+ of
#   your Ailment Threshold) and Freeze (counted: one hit must fill the buildup, MECHC18's lower reading) don't depend on
#   the count: one per use; the extra strikes only refresh a 2 s ailment. Bleed: every landed hit of its skill.
# - Uptime: Poisson uses, 1 - e^(-rate x length); with "You cannot be <Ailment>ed for N seconds after being <Ailment>ed"
#   (immune_after) or Freeze's protection (+500% threshold for 14 s, MEDIUM-LOW on players) the renewal form.
# - Damage while afflicted (KeywordPopups DamagingAilments: "Bleeding, Ignite, and Poison"): Ignite 20% of the boss's
#   average Fire hit a second, Bleed 15% of its average Physical hit, before your mitigation and damage-over-time
#   modifiers (the constants above, KeywordPopups).
# Which preset inflicts which (checked, MECHD7: every GrantedEffect of game 26650308 whose Id holds a preset's monster stem,
# 695 skills, for electrocut / poison / bleed / ignit / shock / chill / freez / ailment stats, scratch/mechd7/ailscan.py;
# the 13 preset varieties' Mods / Mods2 resolved to stats; every Monster* / Pinnacle* Mods row with an Electrocute,
# Poison or Bleed stat (MonsterAlwaysPoison1, MonsterBleedImplicit, ...: none on a preset variety); second sources:
# RESEARCH13 #60 / RESEARCH14 #67 (BuffDefinitions and grounds) and poe2db via them; patch notes: no hit for Electrocute or
# Poison with a boss name, which isn't proof of absence):
# - Electrocute: none. KeywordPopups Electrocute: "Only Lightning damage from Hits with specific skills or effects
#   Contributes to Electrocution Buildup"; no preset skill or mod carries an Electrocute stat. 0, MEDIUM-HIGH (the .ao
#   scripts are unseen).
# - Poison: none. The Poison-on-hit skills found (MMSVaalZealotChaosMortar, GAVaalZealotKnifeStickChaosSlash) belong to
#   map / Incursion Vaal Zealots, not Atziri's fight (its add is VaalZealotSacrifice, one skill: VaalZealotSuicideExplosion,
#   research13's MonsterVarieties). 0, MEDIUM-HIGH (as RESEARCH14).
# - Bleed: Olroth (GPAOlrothThrowBack; GAOlrothIntroSwing the opening only), as BLEED_SKILLS.
# - Freeze in the Bodach's fight: GSRitualWendigoP2_AlliedLunarPulse's "always_freeze" is Lunaris', the player's ally
#   (RESEARCH16 #74): not on you.
# - Not counted, said: the grounds that apply an ailment (GROUND_AILMENTS: the player steps out, GROUNDS_NOTE; the time
#   in them is in no source, so 0, never a flat guess); the Aberration's Spider Beam "Chill as though +33%" (Chill
#   magnitude isn't read here, only the 30% floor); the Delirium minions' Shock duration +100%
#   (DeceleratingProjectileDeliriumMinionExplode: not a damaging row of boss_skill_hits.json).
# The worst-case pinnacle mixes the presets (the pooled type shares, the Red Queen's cadence): it reads each REAL preset
# (real_readings) and takes, per ailment, the LOWEST uptime for a gain (AUDIT29 A29-1's rule, `worst="lowest"`, the
# default: removal and avoidance lines are gains) or the HIGHEST for a harm (`worst="highest"`), naming that preset.
ON_YOU_AILMENTS = ("Ignite", "Shock", "Chill", "Freeze", "Bleed", "Poison", "Electrocute")
DAMAGING_AILMENTS = ("Ignite", "Bleed", "Poison")  # KeywordPopups DamagingAilments (game 26650308): HIGH
_AILMENT_KIND = {"Ignite": "Fire", "Shock": "Lightning", "Chill": "Cold", "Freeze": "Cold", "Bleed": "Physical"}
_AILMENT_VERB = {"Ignite": "Ignited", "Shock": "Shocked", "Freeze": "Frozen", "Chill": "Chilled", "Bleed": "Bleeding"}
# (MECHD7) The dodge share is MECH-C's ON_YOU_DODGE (THE USER'S RULING, 21 Nov 05:20: "2 in 3, the videos", by movement
# alone, one rate for every telegraphed attack; MECHC49 sets it): read, never copied; its rows ON_YOU_DODGE_ROWS. It is a
# MOVEMENT-only share, so stacking it on PoB's evade / block roll counts each once (independent). The videos' all-in figure
# (RESEARCH41 R41-1, research/boss-dodge-share-video-r41.md: 8 of 29 attacks landing, about 1 in 4, over 8 fights; it
# already holds evasion, block and tiny hits) is a row that is NEVER stacked on a modelled evade or block chance.
VIDEO_LANDED_SHARE = 8 / 29  # R41-1: attacks landing in the videos, all causes (LOW)
VIDEO_LANDED_PER_MINUTE = (6.0, 4.5, 9.5)  # R41-1: hits landed a minute, median and range over 8 fights (LOW)


def dodge_how() -> str:
    return f"{ON_YOU_DODGE_HOW}; one rate for every telegraphed attack, by movement alone"


DODGE_HOW = "the user's dodge rate (ON_YOU_DODGE)"
NO_SOURCE = {
    "Poison": ("no pinnacle preset has a Poison chance (checked, MECHD7: 695 boss skills' stats in game 26650308, the "
               "presets' variety mods, the Monster* Poison mods; RESEARCH13 #60 / RESEARCH14 #67's skills, buffs and "
               "grounds; poe2db via them): no source, 0 (MEDIUM-HIGH)"),
    "Electrocute": ("no pinnacle preset Electrocutes you: only 'specific skills or effects' build it (KeywordPopups "
                    "Electrocute) and none of the presets' skills or mods has one (checked, MECHD7: 695 boss skills' "
                    "stats in game 26650308, the presets' variety mods, every Monster* Mods row with an Electrocute stat; "
                    "RESEARCH13 #60's scan; patch notes: no hit): no source, 0 (MEDIUM-HIGH)"),
}
# Grounds that put an ailment on you (RESEARCH14 #67's ground chain, AilmentApplicationAura buffs): not counted (above).
GROUND_AILMENTS = {
    "Arbiter of Ash": {"Chill": "Chilled Ground (GSDemonClawBossChillSeedBlanket -> ChilledGround)"},
    "The Raven Trickster": {"Ignite": "Ignited Ground (CGETangmazuPillarSlamBurn -> IgnitedGround)"},
    "Atziri, the Red Queen": {"Shock": "Shocked Ground (CGEAtziriLightningGround -> ShockedGround)"},
}


def strikes_per_use(name: str) -> float:
    """The boss's mean strikes a damaging use (HIT_EVERY / HITS_EVERY, at least 1; LOW)."""
    from builder.search import bosses
    if name not in bosses.HIT_EVERY or name not in bosses.HITS_EVERY:
        return 1.0
    return max(1.0, bosses.HIT_EVERY[name] / bosses.HITS_EVERY[name])


def _type_uses(stats: dict, kind: str, cadence: str, dodge: float, red_lands: bool,
               all_in: float | None = None) -> tuple[float, str]:
    """(landed uses of the boss's `kind` hits a second, how) at a cadence ("use", "strike"; "defence" = ailment_hits'
    old reading), `dodge` of the dodgeable ones dodged, red-flash ones avoided (the headline) or landing (`red_lands`)."""
    from builder.search import bosses
    name = boss_name(stats)
    if cadence == "defence" or name == _worst() or name not in bosses.HIT_EVERY:
        return ailment_hits({**stats, "_onyou_cadence": "defence"}, kind)
    share = type_shares(name).get(kind, 0.0)
    scored = number(stats.get("enemySkillTime"))
    slowed = scored / bosses.defence_hit_every(name) if scored > 0 else 1.0
    base = bosses.HIT_EVERY[name] if cadence == "use" else bosses.HITS_EVERY[name]
    every = base * slowed
    red, rest = kind_land_parts(stats, kind)
    if all_in is not None:  # (the videos' all-in landing share: evade / block not stacked on it)
        rate = share / every * (1 - red) * all_in if every > 0 else 0.0
        return rate, (f"{name}: one damaging use every {every:.2f} s, {share:.0%} {kind}, {red:.0%} red-flash avoided, "
                      f"the rest landing {all_in:.0%} (the videos' all-in share, R41-1: your evade / block not stacked "
                      "on it)")
    land = (red if red_lands else 0.0) + rest * (1 - dodge)
    rate = share / every * land if every > 0 else 0.0
    how = (f"{name}: one damaging {'use' if cadence == 'use' else 'strike'} every {base:g} s"
           + (f" x{slowed:.2f} (your Slows on it)" if abs(slowed - 1) > 1e-9 else "")
           + (" (LOW: the tables' cadence)" if cadence == "use" else " (every strike counted: an upper bound)")
           + f", {share:.0%} {kind}; of those, {red:.0%} red-flash "
           + ("LANDING (the tanky row)" if red_lands else "avoided by moving out (the user's ruling, 21 Nov)")
           + f", the rest {rest / (1 - red) if red < 1 else 0:.0%} past your evade / block and {dodge:.0%} dodged ("
           + (dodge_how() if dodge == ON_YOU_DODGE else "a row") + f"): {land:.0%} landing")
    return rate, how


def _ailment_on_you(stats: dict, ailment: str, texts: list[str], cadence: str, dodge: float,
                    red_lands: bool = False, all_in: float | None = None) -> dict:
    """One ailment from one boss (no worst-case resolution): the dict ailments_on_you gives per ailment, without rows."""
    name = boss_name(stats)
    out = {"rate": 0.0, "uptime": 0.0, "seconds": 0.0, "dps": 0.0, "effect": 0.0, "boss": name,
           "ground": GROUND_AILMENTS.get(name, {}).get(ailment, "")}
    if ailment in NO_SOURCE:
        return {**out, "how": NO_SOURCE[ailment], "confidence": "MEDIUM-HIGH (game files; the .ao scripts unseen)"}
    kind = _AILMENT_KIND[ailment]
    read = {**stats, "_onyou_cadence": cadence, "_onyou_dodge": dodge}
    uses, how = _type_uses(stats, kind, cadence, dodge, red_lands, all_in)
    if ailment == "Bleed":
        bleeds = BLEED_SKILLS.get(name, 0)
        count = hit_table(name)[1].get("Physical", 0)
        uses *= bleeds / count if count else 0.0
        if uses <= 0:
            return {**out, "how": (f"{name} has no Bleeding chance (Olroth's attacks are the one pinnacle source: "
                                   "research #60 / #67, MECHD7's scan): no source, 0"),
                    "confidence": "MEDIUM-HIGH (game files; the .ao scripts unseen)"}
        how += f", {bleeds} of its {count} Physical skills Bleed (Olroth's ThrowBack: LOW)"
    if uses <= 0:
        return {**out, "how": how + ": no use of that type lands, 0",
                "confidence": "MEDIUM-HIGH (which bosses deal the type: HIGH)"}
    k = strikes_per_use(name) if cadence == "use" else 1.0
    kept = 1 - min(max(number(stats.get(f"{ailment}AvoidChance")), 0.0), 100.0) / 100
    threshold = number(stats.get("AilmentThreshold")) or 0.5 * number(stats.get("Life"))
    taken_key = f"{kind}TakenHit"
    taken = max(number(stats.get(taken_key)) if taken_key in stats else hit_sizes(name).get(kind, 0.0), 0.0)
    immune = immune_after(texts, _AILMENT_VERB[ailment]) if ailment != "Bleed" else 0.0
    base_seconds = {"Ignite": IGNITE_SECONDS, "Shock": SHOCK_ON_PLAYER_SECONDS, "Chill": CHILL_SECONDS_PLAYER,
                    "Freeze": FREEZE_SECONDS_PLAYER, "Bleed": BLEED_SECONDS}[ailment]
    length = _self_seconds(stats, ailment, base_seconds)
    if ailment == "Ignite":
        p, chance_how = ignite_chance(read, uses * k)
    elif ailment == "Shock":
        p = own_shock_chance(stats, name)
        chance_how = f"{p:.0%} a landed Lightning hit (1% per 4% of your Ailment Threshold dealt, x the boss's stats)"
    elif ailment == "Chill":
        dealt = taken / threshold if threshold > 0 else 1.0
        p = 1.0 if dealt >= CHILL_FLOOR else 0.0
        chance_how = f"a {taken:,.0f} Cold hit is {dealt:.0%} of your Ailment Threshold (a Chill needs 30%)"
    elif ailment == "Freeze":
        build = taken / threshold * FREEZE_SCALE_PLAYER * _factor(name, FREEZE_FACTORS, kind) if threshold > 0 else 1.0
        p = 1.0 if build >= 1 else 0.0
        if p > 0 and build < FREEZE_PROTECTION:
            immune = max(immune, FREEZE_PROTECTION_SECONDS)
        chance_how = (f"a {taken:,.0f} Cold hit builds {build:.0%} Freeze (one hit must fill it: the lower reading)"
                      + (f"; then +500% threshold for {FREEZE_PROTECTION_SECONDS:g} s (MEDIUM-LOW on players)"
                         if p > 0 and build < FREEZE_PROTECTION else ""))
    else:  # Bleed: its skill's explicit chance, every landed hit of it that reaches your Life
        reach = life_reach(stats)[0]
        kept_d, kept_how = deflect_kept(stats, "Bleed")
        p = reach * kept_d
        chance_how = f"{reach:.0%} of its Physical hits reach your Life{kept_how}"
    per_use = 1 - (1 - min(max(p, 0.0), 1.0)) ** k
    rate = uses * per_use * kept
    if rate > 0 and immune > 0:
        uptime = min(1.0, length / (max(immune, length) + 1 / rate))
    else:
        uptime = 1 - math.exp(-rate * length) if rate > 0 else 0.0
    dps = 0.0
    if ailment == "Ignite":
        dps = IGNITE_SHARE_PER_SECOND * mean_hits(name).get("Fire", 0.0)
    elif ailment == "Bleed":
        dps = BLEED_SHARE_PER_SECOND * mean_hits(name).get("Physical", 0.0)
    effect = 0.0
    if ailment == "Shock":
        effect = SHOCK_ON_PLAYER_INC * (max(number(stats.get("SelfShockEffect")), 0.0) / 100
                                        if "SelfShockEffect" in stats else 1.0)
    how += (f"; {chance_how}" + (f", {k:.2f} strikes a use: {per_use:.0%} a use" if k > 1 and 0 < p < 1 else "")
            + f"; {kept:.0%} not avoided: {rate * 60:.1f} a minute, {length:g} s each"
            + (f", none for {immune:g} s after" if immune > 0 and rate > 0 else "")
            + f": {_AILMENT_VERB[ailment]} {uptime:.0%} of the fight"
            + (f"; {dps:,.0f} {kind} damage a second while on, before your mitigation" if dps > 0 else "")
            + (f"; +{effect:g}% damage taken while on" if effect > 0 else ""))
    return {**out, "rate": rate, "uptime": uptime, "seconds": length, "dps": dps, "effect": effect, "how": how,
            "confidence": "LOW (the tables' per-use cadence; the dodge share the user's for Cold, extended)"}


def ailments_on_you(stats: dict, texts: list[str] | None = None, worst: str = "lowest",
                    ailments: tuple[str, ...] = ON_YOU_AILMENTS) -> dict[str, dict]:
    """{ailment: {"rate": landed afflicting uses a second (before 'already afflicted'), "uptime": share of the fight
    afflicted, "seconds": one's length on you, "dps": damage a second while on before your mitigation (Ignite, Bleed),
    "effect": Shock's % increased damage taken while on, "boss": the preset read, "ground": a ground that also inflicts it
    (not counted; "" without), "how", "confidence", "rows": [(label, uptime)] for dodge none / 75%, every strike and the
    old defence cadence; red-flash hits landing]} for the boss's own ailments on you, per use of its skills (the comment above). `stats`: PoB's
    {Type}TakenHit, AilmentThreshold, Life, <Ailment>AvoidChance, Self<Ailment>Duration, SelfShockEffect, EvadeChance,
    EffectiveBlockChance, enemySkillTime and "_boss"; `texts`: the build's lines (immunity windows); `worst`: on the
    worst-case pinnacle, "lowest" (a gain reads the lowest real preset) or "highest" (a harm)."""
    texts = [str(t).strip() for t in (texts or [])]
    worst_name = _worst()
    reads = real_readings(stats) if worst_name is not None and boss_name(stats) == worst_name else [
        (boss_name(stats), stats)]
    pick_of = max if worst == "highest" else min
    out = {}
    for ailment in ailments:
        picks = [(_ailment_on_you(s, ailment, texts, "use", ON_YOU_DODGE), s) for _, s in reads]
        pick, seen = pick_of(picks, key=lambda p: p[0]["uptime"])
        if len(picks) > 1:
            top = max(picks, key=lambda p: p[0]["uptime"])[0]
            pick = {**pick, "how": (f"worst case: the {'highest' if worst == 'highest' else 'lowest'} real preset's, "
                                    f"{pick['boss']} ({'a harm' if worst == 'highest' else 'a gain'}; AUDIT29 A29-1; "
                                    f"the highest: {top['boss']}, {top['uptime']:.0%}): " + pick["how"])}
        rows = [(f"{d:.0%} of the dodgeable uses dodged", _ailment_on_you(seen, ailment, texts, "use", d)["uptime"])
                for d in ON_YOU_DODGE_ROWS]
        rows.append(("the videos' all-in landing share, 8 of 29 (R41-1; your evade / block not stacked on it)",
                     _ailment_on_you(seen, ailment, texts, "use", 0.0, all_in=VIDEO_LANDED_SHARE)["uptime"]))
        rows.append(("red-flash hits land (a tanky build that stands in them)",
                     _ailment_on_you(seen, ailment, texts, "use", ON_YOU_DODGE, red_lands=True)["uptime"]))
        rows.append(("every strike, not every use (HITS_EVERY: an upper bound)",
                     _ailment_on_you(seen, ailment, texts, "strike", ON_YOU_DODGE)["uptime"]))
        rows.append(("the defence cadence (enemySkillTime, no dodge: the old reading)",
                     _ailment_on_you(seen, ailment, texts, "defence", 0.0)["uptime"]))
        out[ailment] = {**pick, "rows": rows}
    return out


def ailment_uptime(stats: dict, ailment: str, texts: list[str] | None = None, worst: str = "lowest"
                   ) -> tuple[float, str]:
    """(share of the fight the boss keeps you afflicted by `ailment`, how): ailments_on_you's headline for one."""
    got = ailments_on_you(stats, texts, worst, (ailment,))[ailment]
    return got["uptime"], got["how"]


# The boss's red-flash uses and the time avoiding them costs (MECHD7; THE USER'S RULING 21 Nov 04:32, "Avoided, time cost
# counted"). Red-flash skills (no evade / block / dodge-roll roll) are AVOIDED by moving out in the headline, each use
# costing time off attacking. Uses a second: RED_FLASH_USES, per use (MECHD8, below; the old reading, RED_FLASH's
# flagged-skill share / bosses.HIT_EVERY, is the "skills" row; land_share and the defence floors keep the skill-count
# share, so red-flash hits stay landing there at the cautious share: the manager's 07:25 ruling), x your Slows on the boss
# (PoB's scored enemySkillTime over the preset's defence cadence). The worst-case pinnacle takes the HIGHEST real
# preset's (the user's worst-case rule: the most time lost). Time per avoided use: one dodge roll, RED_FLASH_AVOID_SECONDS
# 0.75 s (game 26650308 GrantedEffects DodgeRollPlayer CastTime 750 ms, action_attack_or_cast_time_uses_animation_length;
# dodge_roll_base_travel_distance 46 = 4.6 m; second source R26-44 (poe2wiki, Mobalytics): movement speed lengthens the
# roll within the same 0.75 s). A STAND-IN, LOW: research measures the real time from fight videos (a nuke may take more
# than one roll). Read by MECH-C (MECHC49): the main skill's share x (1 - red_flash_time_share).
RED_FLASH_AVOID_SECONDS = 0.75
RED_FLASH_AVOID_HOW = ("one dodge roll, 0.75 s (DodgeRollPlayer CastTime 750 ms, 4.6 m), the 'moves' figure; RESEARCH41 "
                       "R41-2's videos (LOW, 4 sure cases): about 1 s (0.75-1.25 s) when the player rolls out, 0 s in 2 "
                       "of 4 (out of range; a Titan tanked it), 0.5-1 s a use on average")
RED_FLASH_READINGS = ("moves", "tanks")  # (MECHD7: "tanks": 0 s, the hit lands in full: ailments_on_you's red_lands row)


# Red-flash USES a minute per preset (MECHD8; the manager's 07:25 note 21 Nov: the skill-count reading gave the Arbiter of
# Ash 11.8 a minute where RESEARCH41's videos show about 1-3). The old reading took RED_FLASH's flagged-skill share (7 of
# 20 for the Arbiter) as the share of its USES, so a red-flash skill's sub-effects (Bullet Hell's three effects, the
# Chasm combo's two, Olroth's slam and two aftershocks) each counted as a use, and its long cooldowns (Bullet Hell and
# the Chasm combo 55 s) weighed like the 5 s Fireball. Now: MECHD5's cadence model (scratch/mechd5/cadence5.py: each
# cooldown skill used every cooldown, scaled down when they overfill the time, the basic attack filling the rest; an
# UPPER bound on a cooldown skill's uses, as the AI need not use it the moment it is ready) gives each row skill's uses a
# minute; a use is red when its own stat sets, or a flagged effect named after it, carry
# base_skill_cannot_be_avoided_by_dodge_roll_or_evaded_or_blocked (game 26476662, RESEARCH15's flagged effects). By
# hand (LOW, by name): the Arbiter's Chasm Burst / Big Chasm Slash to its Chasm Shenanigans (55 s), the King's
# Punishment to its Hexes (one Punishment a Hex, 25 s; the upper row: a player who obeys the Hex isn't punished), the
# Raven's Volatile Flame Blast to its Flame Blast start (30 s); the Bodach's meteor slams share Fist Slam's timer, half
# its uses (an even split, LOW). Flagged effects that neither the name match nor a hand mapping tie to a use (the
# Arbiter's Offscreen Slam and Flame Seed nuke, Zarokh's Death Touch, the Raven's Phase Change Blast; which use fires
# them is UNVERIFIED: the .ao scripts are unread) each count as one average damaging skill of the preset (the old
# reading, kept for them: not zeroed on an unverified mapping). scratch/mechd8/redshare.py, .txt.
# (red-flash uses a minute, damaging uses a minute, which); the King weighted 43 / 57 by form (HIT_EVERY's note).
RED_FLASH_USES: dict[str, tuple[float, float, str]] = {
    "Arbiter of Ash": (3.69, 26.7, "Bullet Hell 0.51 and the Chasm combo 0.51 (55 s each), Offscreen Slam and the Flame "
                                   "Seed nuke 1.33 each (no use named: one average skill each)"),
    "Arbiter of Divinity": (8.72, 44.2, "Spear Toss impacts 3.33 + 2.31 (18 s / 26 s), Storm Blink 1.71, Extendo Spear "
                                        "Combo's slam 1.36"),
    "Xesht, We That Are One": (12.0, 48.0, "Pistol Flick wave 5.0, Hand Slams 4.0, Multi Flick 3.0"),
    "The King in the Mists": (0.43 * 6.4 + 0.57 * 9.4, 0.43 * 28.1 + 0.57 * 51.6,
                              "Vine Slam 4.0, Punishment 2.4 (one a Hex), Bug Bomb 3.0 (second form)"),
    "Zarokh, the Temporal": (2.39, 52.5, "Death Touch (no use named: one average skill)"),
    "The Raven Trickster": (1.88, 28.6, "Volatile Flame Blast 1.07 (30 s), Phase Change Blast 0.82 (no use named: one "
                                        "average skill)"),
    "The Bodach": (4.5, 49.2, "phase 2 meteor slams 2.5 (half of Fist Slam's shared timer) and the Meteor Combo 2.0"),
    "Olroth, Origin of the Fall": (6.06, 26.9, "Throw Out 3.33 (18 s), Ground Slam and its aftershocks 2.73 (22 s)"),
    "The Aberration": (3.59, 36.2, "Spider Mortar 3.59"),
    "Vessel of Kulemak": (0.0, 54.8, "none (Teleport Slam can still be evaded)"),
    "Atziri, the Red Queen": (0.0, 53.0, "none in phase 1"),
    "Atziri, the Fell Serpent": (1.5, 43.4, "Flesh Bubble Explosion 1.5 (40 s)"),
}
# RESEARCH41 R41-2's videos (LOW, 4 fights): the Arbiter 2 in 41 s (VDA), 1 in 48 s (SlayerD, Uber), 0-1 in 76 s
# (Lambark); Olroth 1 in 96 s (echoburn): 4.5 in 261 s, about 1 a minute, a red share of the counted attacks ~10% (3 of
# 29). The row, not the headline: four fights, skilled players, one preset each but two.
RED_FLASH_VIDEO_PER_MINUTE = 4.5 / 261 * 60
RED_FLASH_RATE_READINGS = ("uses", "skills", "video")


def red_use_share(name: str) -> float:
    """The share of the preset's damaging USES that are red-flash (RED_FLASH_USES; the worst case's own skill-count
    share, as before, for a name the table lacks)."""
    if name in RED_FLASH_USES:
        red, dmg, _ = RED_FLASH_USES[name]
        return red / dmg if dmg else 0.0
    una, _, total, _ = RED_FLASH.get(name, RED_FLASH[DEFAULT_BOSS])
    return una / total if total else 0.0


def red_flash_rate(stats: dict | str | None = None, reading: str = "uses") -> tuple[float, str]:
    """(the boss's red-flash uses a second, how); `stats` a stats dict ("_boss", enemySkillTime) or a preset's name. The
    worst-case pinnacle: the highest real preset's. `reading`: "uses" (the headline: RED_FLASH_USES, per use), "skills"
    (the old reading: the flagged-skill share over HIT_EVERY, an overcount of uses), "video" (RESEARCH41's ~1 a minute,
    the same for every preset)."""
    from builder.search import bosses
    stats = {"_boss": stats} if isinstance(stats, str) else dict(stats or {})
    name = boss_name(stats)
    if name == _worst() or name not in bosses.HIT_EVERY:
        rows = [(red_flash_rate({**stats, "_boss": n, "enemySkillTime": 0}, reading)[0], n) for n in bosses.REAL]
        rate, top = max(rows)
        return rate, (f"worst case: the highest real preset's, {top} ({rate * 60:.1f} a minute): "
                      + red_flash_rate({**stats, "_boss": top, "enemySkillTime": 0}, reading)[1])
    scored = number(stats.get("enemySkillTime"))
    slowed = scored / bosses.defence_hit_every(name) if scored > 0 else 1.0
    slow = f" (x{1 / slowed:.2f}, your Slows on it)" if abs(slowed - 1) > 1e-9 else ""
    if reading == "video":
        rate = RED_FLASH_VIDEO_PER_MINUTE / 60 / slowed
        return rate, (f"{name}: the videos' {RED_FLASH_VIDEO_PER_MINUTE:.1f} red-flash uses a minute{slow} (R41-2: 4.5 in "
                      f"261 s over 4 fights, LOW)")
    if reading == "skills":
        una, _, total, what = RED_FLASH[name]
        share = una / total if total else 0.0
        every = bosses.HIT_EVERY[name] * slowed
        rate = share / every if every > 0 else 0.0
        return rate, (f"{name}: {share:.0%} of its damaging skills are red-flash ({what}), one use every {every:.2f} s"
                      + slow + f": {rate * 60:.1f} red-flash uses a minute (the old skill-count reading: sub-effects and "
                      "long cooldowns counted as uses, an overcount; MECHD8)")
    red, dmg, what = RED_FLASH_USES[name]
    rate = red / 60 / slowed
    return rate, (f"{name}: {rate * 60:.1f} red-flash uses a minute{slow}, {red / dmg if dmg else 0:.0%} of its damaging "
                  f"uses ({what}; MECHD5's cadence, each cooldown skill used when ready: an upper bound; LOW). The "
                  f"videos: about {RED_FLASH_VIDEO_PER_MINUTE:.0f} a minute (R41-2, 4 fights)")


def red_flash_time_share(stats: dict | str | None = None, reading: str = "moves",
                         rate_reading: str = "uses") -> tuple[float, str]:
    """(the share of the fight spent moving out of the boss's red-flash attacks, how): red_flash_rate x
    RED_FLASH_AVOID_SECONDS, at most 1 ("moves", the headline); "tanks": 0 s, the hit lands in full (the row).
    `rate_reading`: red_flash_rate's ("uses" the headline; "skills", "video" rows)."""
    rate, how = red_flash_rate(stats, rate_reading)
    if reading == "tanks":
        return 0.0, (f"{how}; tanked, not avoided: no time lost, every red-flash hit lands in full (the tanky row; "
                     "R41-2 saw a Titan tank one)")
    share = min(1.0, rate * RED_FLASH_AVOID_SECONDS)
    return share, (f"{how}; each avoided by moving out, {RED_FLASH_AVOID_HOW}: {share:.1%} of the fight not attacking "
                   "(the user's ruling, 21 Nov: 'Avoided, time cost counted'; if they land instead, a tanky build: 0%)")


def landed_cross_check(stats: dict) -> tuple[float, str]:
    """(the model's landed boss uses a minute, how against the videos' ~6 (4.5-9.5), RESEARCH41 R41-1): one per damaging
    use (HIT_EVERY, x your Slows on it), red-flash avoided, the rest past PoB's evade / block and the movement dodge
    (ON_YOU_DODGE). A cross-check row, not a headline (LOW both sides)."""
    from builder.search import bosses
    name = boss_name(stats)
    if name == _worst() or name not in bosses.HIT_EVERY:
        reads = real_readings(stats)
        got = [(landed_cross_check(s)[0], n) for n, s in reads]
        hi, top = max(got)
        lo, low = min(got)
        return hi, (f"worst case: the real presets give {lo:.1f} ({low}) to {hi:.1f} ({top}) landed boss uses a minute; "
                    f"the videos {VIDEO_LANDED_PER_MINUTE[0]:g} ({VIDEO_LANDED_PER_MINUTE[1]:g}-"
                    f"{VIDEO_LANDED_PER_MINUTE[2]:g}, R41-1, 8 fights)")
    red = red_use_share(name)  # (MECHD8: per use, not the skill-count share)
    evade, block = evade_block(stats)
    scored = number(stats.get("enemySkillTime"))
    slowed = scored / bosses.defence_hit_every(name) if scored > 0 else 1.0
    per_min = 60 / (bosses.HIT_EVERY[name] * slowed) * (1 - red) * (1 - evade) * (1 - block) * (1 - ON_YOU_DODGE)
    lo, hi = VIDEO_LANDED_PER_MINUTE[1:]
    verdict = "inside" if lo <= per_min <= hi else ("below" if per_min < lo else "above")
    return per_min, (f"{name}: the model lands {per_min:.1f} boss uses a minute (one a use every "
                     f"{bosses.HIT_EVERY[name] * slowed:.2f} s, {red:.0%} red-flash avoided, {evade:.0%} evaded, "
                     f"{block:.0%} blocked, {ON_YOU_DODGE:.0%} dodged); the videos {VIDEO_LANDED_PER_MINUTE[0]:g} "
                     f"({lo:g}-{hi:g}, R41-1, 8 fights): {verdict} their range")
