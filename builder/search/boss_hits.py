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
            "confidence": "; ".join(f"{k} {v}" for k, v in CONFIDENCE_HITS.items()) + "; " + CADENCE_NOTE}


# How often the boss hits (MECHC7, AUDIT18 A18-2): every model that reads the boss's hits (Energy Shield Recharge, Rage,
# "when Hit" buffs, charms, Iron Ward, parries, flasks' Life lost) takes one every enemySkillTime, PoB's boss-preset
# placeholder of 0.7 s. The game decides it per boss skill and no data we hold gives it: a flat number of the last-resort
# kind (CLAUDE.md rule 2), LOW, said once in every build's assumptions here.
CADENCE_NOTE = ("the boss's hits come one every 0.7 s (PoB's boss-preset enemySkillTime; LOW: the game's per-skill cadence "
                "isn't in our data) for every model that reads them")


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
    fire = type_shares(name)["Fire"]
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
    if name in CORRUPTED_BLOOD:
        out.append((1 / sum(hit_table(name)[1].values()), CORRUPTED_BLOOD[name], "Corrupted Blood"))
    return out


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
