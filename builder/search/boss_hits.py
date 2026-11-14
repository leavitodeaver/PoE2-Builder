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
# - IGNITE_INCREASE: the real preset with the most increased Ignite chance per Fire hit (Atziri, the Fell Serpent: 80 over
#   her 4 Fire skills, 20% a hit), as an increase over the pooled Fire skills that averages to that.
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
    _worst()
    return HIT_TABLE.get(name or DEFAULT_BOSS, HIT_TABLE[DEFAULT_BOSS])


def hit_sizes(name: str | None = None, reading: str = "headline") -> dict[str, float]:
    """One hit of each damage type the boss deals, before your mitigation (0 for the types it doesn't deal). The
    worst-case pinnacle: each type at the biggest any real preset deals (bosses.WORST_HIT_FROM)."""
    if name is not None and name == _worst():
        from builder.search import bosses
        return {t: (hit_sizes(bosses.WORST_HIT_FROM[t], reading)[t] if t in bosses.WORST_HIT_FROM else 0.0) for t in TYPES}
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
    raw = (f"raw per type from the real preset that hits hardest with it: {worst_sources()}"
           if s["boss"] == _worst() else f"raw {hit_table(s['boss'])[0]:g}")
    note = (f"{s['boss']}: level {LEVEL} base {BASE_DAMAGE:g} x skill {SKILL_MULT:g} x {raw} "
            f"x unique {UNIQUE_LESS:g} x the level's more (Elemental x{LEVEL_MORE['elemental']:g}, Chaos "
            f"x{LEVEL_MORE['chaos']:g}); " + "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items()))
    return f"one hit of each type: {want}", f"max hit {got}: {verdict}", None, note


def worst_sources() -> str:
    """'Physical: <preset> (raw x), ...' for the worst-case pinnacle's hit sizes."""
    from builder.search import bosses
    return ", ".join(f"{t} {n} (x{HIT_TABLE[n][0]:g})" for t, n in bosses.WORST_HIT_FROM.items())


def hits_note(name: str | None = None) -> dict:
    """The report's labelled row for the boss's hit sizes."""
    name = name or DEFAULT_BOSS
    rows = {r: hit_sizes(name, r) for r in READINGS}
    weights = type_shares(name)
    value = "; ".join(f"{t} {rows['headline'][t]:,.0f} ({weights[t]:.0%} of hits; without the 0.65 {rows['harsh'][t]:,.0f}, "
                      f"low bound {rows['low'][t]:,.0f})" for t in TYPES if rows["headline"][t] > 0)
    worst = (f"; each type at the biggest any real preset deals ({worst_sources()}), the share of hits pooled over the "
             "12 real presets' damaging skills (LOW)") if name == _worst() else ""
    return {"label": f"Boss hit sizes: {name}", "value": value,
            "basis": "the game's monster tables, not PoB's Pinnacle default (965 per type and 386 Chaos, all in one hit): "
                     "level base damage x skill x the boss's damage multiplier x unique 35% less x the level's more for "
                     "Elemental (x4) and Chaos (x2)" + worst,
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
#   config_vars, after your mitigation). The Flammability of the boss's other landed Fire hits in the last 8 s counts at
#   its mean (MEDIUM-LOW: whether an Ignite clears the Flammability on you is UNVERIFIED; if it does, the chance is
#   lower). The bosses' "increased Ignite chance" stats (IGNITE_INCREASE) are averaged over their Fire skills (LOW: each
#   skill's share of the Fire hits). Less your Ignite avoidance (PoB's IgniteAvoidChance). 4 s base (KeywordPopups
#   Ignite) x PoB's SelfIgniteDuration.
# - Corrupted Blood (Atziri's BloodSpearPulse, 3 s, stacking; "is not Bleeding", so it hits ES: MEDIUM-HIGH): one of her
#   damaging skills, at its share of her hits (LOW).
# - Degen grounds (8 bosses): you can step out of them: 0, with a play note (RESEARCH21's advice; LOW).
# - Bleeding (Olroth) and Poison bypass ES: they don't delay its Recharge.
# ---------------------------------------------------------------------------------------------------------------------
IGNITE_SECONDS = 4.0  # KeywordPopups Ignite: Fire damage over time for 4 s by default (HIGH)
FLAMMABILITY_PER_THRESHOLD = 0.2  # KeywordPopups Flammability: 1% Ignite chance per 5% of the Ailment Threshold (HIGH)
FLAMMABILITY_SECONDS = 8.0  # KeywordPopups Flammability: each instance lasts 8 s by default (HIGH)
# The bosses' Fire skills with an "ignite_chance_+%" stat (GrantedEffectStatSets, read 12 Nov 2026 by
# scratch/mechc7/ignitechance.py): Arbiter of Ash's MASDemonClawBossFire +20 (one of its 17 Fire skills; RESEARCH21 #126
# names the same); Atziri's fire melee MeleeAtAnimationSpeedFire +80 (that Atziri uses it: RESEARCH21's monster scan
# only, LOW; taken for both phases). Others: none found among the presets' skills by RESEARCH21 (UNVERIFIED beyond its
# scan).
IGNITE_INCREASE = {"Arbiter of Ash": (20,), "Atziri, the Red Queen": (80,), "Atziri, the Fell Serpent": (80,)}
CORRUPTED_BLOOD = {"Atziri, the Red Queen": 3.0, "Atziri, the Fell Serpent": 3.0}  # seconds (BloodSpearPulse, RESEARCH13)
GROUNDS_NOTE = ("the boss's damaging grounds aren't counted: step out of them (8 of 13 bosses have one; LOW)")


def ignite_chance(stats: dict, land: float = 0.0) -> tuple[float, str]:
    """Chance a landed Fire hit of the boss Ignites you (before your Ignite avoidance), and how it was worked out.
    `land` is the boss's landed hits per second (their Fire share stacks Flammability over 8 s)."""
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
    chance = min(1.0, per_hit * (1 + others))
    return chance, (f"a {taken:,.0f} Fire hit against your {threshold:,.0f} Ailment Threshold gives {per_hit:.1%} "
                    f"Flammability (+{increase:.1f}% increased from the boss's skills), plus that of the "
                    f"{others:.1f} other Fire hits landed in the last 8 s: {chance:.0%} to Ignite (MEDIUM-LOW)")


def es_dot(stats: dict, ignite: bool = True, land: float = 0.0) -> list[tuple[float, float, str]]:
    """(share of the boss's landed hits that leave damage over time on your Energy Shield, its seconds, what)."""
    name = boss_name(stats)
    out = []
    fire = type_shares(name)["Fire"]
    avoid = min(max(number(stats.get("IgniteAvoidChance")), 0.0), 100) / 100
    if ignite and fire > 0 and avoid < 1:
        chance = ignite_chance(stats, land)[0]
        scale = number(stats.get("SelfIgniteDuration")) / 100 if "SelfIgniteDuration" in stats else 1.0
        if chance > 0:
            out.append((fire * chance * (1 - avoid), IGNITE_SECONDS * scale, "Ignite"))
    if blood := corrupted_blood(name):
        out.append((*blood, "Corrupted Blood"))
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
    "Vessel of Kulemak": (1.33, 1.33, 1.33),  # its beams Shock as though +33-100% damage: the low end (LOW)
    "Atziri, the Red Queen": (1.8,),  # her lightning melee +80% Shock chance
    "Atziri, the Fell Serpent": (1.8,),  # (taken for both phases, as IGNITE_INCREASE: LOW)
}
FREEZE_FACTORS = {"Olroth, Origin of the Fall": (3.0,)}  # SwordGrabExplosion +200% Freeze buildup
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
        rate = land * share * (1 - avoid)
        return rate, (f"{land:.2f} landed hits/s x {share:.1%} that Bleed (Olroth's ThrowBack, one of its skills: LOW) x "
                      f"{1 - avoid:.0%} not avoided: {rate * 60:.1f} a minute")
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
            chance = min(1.0, taken / threshold * SHOCK_PER_THRESHOLD * factor)
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


def _slow_skills(name: str, land: float) -> tuple[float, list[str]]:
    """Slows a second from the boss's Slow skills and their descriptions (`land`: the share of its hits that land)."""
    if name == _worst():
        from builder.search import bosses
        rows = [_slow_skills(n, land) for n in bosses.REAL if n in SLOW_SKILLS]
        return max(rows, default=(0.0, []), key=lambda r: r[0])
    rate, parts = 0.0, []
    for cooldown, hit, what in SLOW_SKILLS.get(name, ()):
        r = (land if hit else 1.0) / cooldown
        rate += r
        parts.append(f"{what}{f' x {land:.0%} landing' if hit else ''}: {r * 60:.1f} a minute")
    return rate, parts


def slow_rate(stats: dict, land: float) -> tuple[float | None, str]:
    """New Slows a second the boss puts on you (a Chill from its Cold hits, its Slow skills), and how; None where the
    boss has neither (the caller keeps its labelled flat rate). `land`: the boss's landed hits a second; stats as
    ailment_rate's, with ChillAvoidChance."""
    name = boss_name(stats)
    share = type_shares(name)["Cold"]
    skills, parts = _slow_skills(name, land_share(stats))
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
    sizes = hit_sizes(worst)
    scored = number(stats.get("enemySkillTime")) or bosses.defence_hit_every(worst)
    out = []
    for name in bosses.REAL:
        own = hit_sizes(name)
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
