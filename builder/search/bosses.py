"""Boss presets for 0.5 (MECH50; FIX_QUEUE "RESEARCH8's findings", RESEARCH9's boss-presets-0-5.md, 12 Nov 2026; the boss
model lane MECH-D from MECHD1).

PoB scores every build against its default "Guardian/Pinnacle Boss" (ConfigOptions.lua enemyIsBoss, defaultIndex 3),
whose placeholder resistances are 50% Fire / Cold / Lightning and 0% Chaos. 0.5's pinnacles have far less (0.5.4
lowered them), so elemental hits and elemental ailments were undercounted by x1.1 to x2.3 against physical and chaos.
The bridge's resetConfig now writes the chosen preset's resistances into the config set (BOSS_DEFAULT in bridge.lua
must equal PRESETS[DEFAULT]: tests/test_models.py checks it).

Penetration: PoB already floors it at 0% and leaves a resistance at or below 0% unpenetrated (CalcOffence.lua
calcPenResist, minimum ElementalPenetrationMinimum, 0 by default), as the game's KeywordPopups "Penetration" says
("can only be Penetrated down to a minimum of 0% by default"). (checked: CalcOffence.lua 466-474 and its two callers in
CalcBreakdown.lua; KeywordPopups.json Penetration, game 26476662.)

Resistances, the game files agree (MECHD5, research13's MonsterVarieties -> MonsterTypes.MonsterResistances, a LIST of
MonsterResistances rows that add up: the Arbiter of Ash MinorFireResist + MinorColdVuln, the Arbiter of Divinity
MinorLightningResist + MinorFireVuln, Olroth MinorColdResist, the Aberration MinorFireResist, Xesht / Zarokh / both
Atziris MinorAllElementalResists, the King / Raven / Bodach / Kulemak None). Each row's fourth column per type is a
by-level list, the fifth the area levels it starts at: the Minor resist rows [15, 15, 20, 45] from levels [0, 45, 64,
76], so 45 at the pinnacles' level 82; the Vuln rows -15; MinorAllElementalResists 15 at every level. The first three
columns (30) aren't the endgame value. Matches poe2db's 45 / -15 / 15 for every preset: HIGH.

Every value: poe2db's current monster rows (read 12 Nov 2026, game 0.5.5), via RESEARCH9; resistances MEDIUM-HIGH, Life
x29 rarity at level 82 (formula HIGH, level MEDIUM). A game update (26476662 -> 26579160) was waiting on 12 Nov:
recheck after it. The default (MECHD5, the user's decision 12 Nov 2026) is an invented worst-case pinnacle, WORST: each
stat the worst for the player among the 12 real presets; a request can still name a real one.

Models read the boss a calculation is scored against with `of(stats)`: stats["_boss"] (kill_stats() puts it there beside
the models), else the default.
"""
from __future__ import annotations

from typing import NamedTuple


class Kills(NamedTuple):
    """Killable adds in a boss fight (MECHD1; AUDIT16 A16-2, the manager's ruling): one add every `every` s while the
    fight is in the part that summons them, `share` of the fight (by Life, at equal DPS)."""
    every: float
    share: float
    what: str
    confidence: str


class Boss(NamedTuple):
    name: str
    fire: int
    cold: int
    lightning: int
    chaos: int
    life: int  # the Life the player takes off in the fight, at level 82 (RESEARCH9: x29 rarity; tiers / Uber not modelled)
    # Monster Category (KeywordPopups MonsterCategory: one per monster; RESEARCH23 #138, the manager's ruling 12 Nov
    # ~12:50, poe2db's category field, HIGH): a name, {name: share of the fight} for a boss whose forms differ, or None
    # where no source gives one ("against <category>" lines then count 0, labelled).
    category: str | dict | None
    note: str = ""
    # Stun threshold, MonsterVarieties.PoiseThreshold (% of the level's monster threshold, x6 Unique on top; RESEARCH18
    # #91, HIGH): PoB's Pinnacle preset is 938, so PoB's Heavy Stun buildup is x938/poise against this boss, and its
    # Freeze / Electrocute / Pin buildup the same (they read the same threshold, CalcOffence.lua 5570: MEDIUM).
    poise: int = 938
    heavy_stun_s: float = 4.0  # BossBase.ot set_base_heavy_stun_duration_ms 4000 (RESEARCH19 #98, HIGH; Zarokh HIGH too: its chain ApparitionBoss -> SanctumBossBase -> BossBase / SanctumMonsterBase sets no other, MECHD2)
    # MonsterTypes.EnergyShieldFromLife, % (AUDIT18 A18-4; the values match the table, AUDIT22 A22-5). Read as f% of base
    # Life converted to ES, then the Unique rarity's +2,800% Life / +400% ES (PoB's monster reading and the game's
    # "unscaled ... to_convert" stat: MEDIUM-LOW, MECHD4): ES = 5f / (29 (1 - f)) of Life. Read by cull_threshold.
    # Second source (MECHD5, poe2db's Atziri, the Red Queen page, 12 Nov 2026, its level-68 figures, before rarity):
    # Life 61,267 = DefaultMonsterStats' 8,257 x 742% (not reduced by the 33%), Energy Shield 20,218 = 33% of that Life:
    # poe2db adds f% of Life as ES, it doesn't convert. The two readings give the same ES at Normal rarity and differ in
    # Life only (ours x(1 - f)). poe2db's figure is its own calculation, not a measurement, and PoB's export and the
    # stat's "convert" wording read conversion, so ours stays; at Unique rarity poe2db's reading is ES = 5f / 29 of Life
    # (Atziri 5.7% vs our 8.5%: Culling at 4.73% vs our 4.61% of the pool; ours is the smaller Cull gain). Both written
    # down, as CLAUDE.md asks; still MEDIUM-LOW (an in-game Life / ES readout would settle it).
    es_from_life: int = 0
    immune: frozenset = frozenset()  # ailments the boss can't have (AUDIT9 A9-7: Zarokh's own monster mod)
    kills: tuple = ()  # Kills rows (empty: no killable adds with a known rate; see the comment under PRESETS)
    inflicts: tuple = ()  # ailments its fight puts on the player (RESEARCH13 #60, A9-1, A10-1), for MECH-C's triggers
    # Slow effect on the boss: Unique monsters take 50% less (Mods MonsterUnique11 slow potency -50; poe2db's Rarity page;
    # RESEARCH13 boss-resistances-damage-game-tables.md, MEDIUM-HIGH that it applies to pinnacles). PoB applies a Slow on
    # the enemy in full (CalcPerform.lua calcs.actionSpeedMod, no SlowPotency for bosses: research-checked), which only
    # lengthens the boss's skill time (CalcDefence.lua 3428, survival): models reading a Slow's magnitude multiply by this.
    slow_taken: float = 0.5
    # Shares of the boss's hits with no evade / block roll, and with evasion but no block (boss_hits.shares reads these
    # when set, else its own RED_FLASH table): set only on the worst case (MECHD5).
    unavoidable: float | None = None
    unblockable: float | None = None


POB_POISE = 938  # PoB's Pinnacle preset's threshold multiplier (+838%; RESEARCH18 #91)
_NO_FREEZE_ELECTROCUTE = frozenset({"Freeze", "Electrocute"})
PRESETS: dict[str, Boss] = {b.name: b for b in (
    Boss("Arbiter of Ash", 45, -15, 0, 0, 9461668, "Demon", "the default target (RESEARCH4)", poise=1235,
         inflicts=("Ignite", "Chill", "Freeze")),
    Boss("Arbiter of Divinity", -15, 0, 45, 0, 9461668, "Demon",
         "0.5 apex; EnergyShieldFromLife 10 (its meaning UNVERIFIED, A22-5); Monster Category Demon (its own MonsterVarieties row, MECHD2)",
         poise=1235, es_from_life=10, inflicts=("Shock",)),
    Boss("Xesht, We That Are One", 15, 15, 15, 0, 13762425, "Demon",
         "Breach; PoB's spectre block 0/0/0 is not the boss; EnergyShieldFromLife 5 (its meaning UNVERIFIED, A22-5)", poise=1700, es_from_life=5,
         inflicts=("Ignite", "Shock", "Chill", "Freeze")),
    Boss("The King in the Mists", 0, 0, 0, 0, 6271939, {"Humanoid": 0.43, "Demon": 0.57},
         "Ritual, whole fight: the first form (Humanoid, Armour 20) goes immune at 25% Life, the second (Demon, Armour "
         "65) starts at full Life, both 375%: 1.75 bars (RESEARCH25 #147; the maze between them is downtime, not "
         "counted); EnergyShieldFromLife 10 (its meaning UNVERIFIED, A22-5); the pinnacle King's Chaos resistance is 0% (the campaign King's 60% isn't this)",
         poise=468, es_from_life=10,
         kills=(Kills(25.0, 0.43, "the King's Affliction Totems / Mystic Fetishes, first form only (the second form's "
                                  "effigies can't be killed, RESEARCH25 #147)",
                      "MEDIUM: summon cooldown, RESEARCH14 #66"),),
         inflicts=("Withered", "Ignite", "Slow")),
    Boss("Zarokh, the Temporal", 15, 15, 15, 0, 7167930, "Construct",
         "Sekhemas; can't be Frozen or Electrocuted (its own monster mod, AUDIT9 A9-7); EnergyShieldFromLife 7 (its meaning UNVERIFIED, A22-5); its "
         "'Lightning' skills deal Physical", poise=938, es_from_life=7, immune=_NO_FREEZE_ELECTROCUTE,
         inflicts=("Slow",)),
    Boss("The Raven Trickster", 0, 0, 0, 0, 9461668, "Humanoid", "Delirium; EnergyShieldFromLife 10 (its meaning UNVERIFIED, A22-5)", poise=1235,
         es_from_life=10, inflicts=("Ignite", "Shock", "Chill", "Freeze")),
    Boss("The Bodach", 0, 0, 0, 0, 17394177, "Demon", "Ritual tier 14+; one bar for both phases (MEDIUM)", poise=2275),
    Boss("Olroth, Origin of the Fall", 0, 45, 0, 0, 3364148, "Undead",
         "Expedition; Heavy Stun lasts 6.07 s (Olroth_.ot)", poise=703, heavy_stun_s=6.066,
         inflicts=("Chill", "Freeze", "Bleed")),
    Boss("The Aberration", 45, 0, 0, 0, 9461668, "Demon",
         "Runes of Aldur pinnacle; Monster Category Demon (CyclopsSpiderBoss MonsterCategory 1 = Demon, tag demon; poe2db "
         "agrees, RESEARCH INDEX #49; MECHD2)",
         poise=1235, inflicts=("Shock", "Chill", "Freeze")),
    Boss("Vessel of Kulemak", 0, 0, 0, 0, 2484882, "Demon",
         "Abyss; empowerment by Liches not modelled (UNVERIFIED); EnergyShieldFromLife 10 (its meaning UNVERIFIED, A22-5); stun threshold 81% (its own "
         "MonsterVarieties PoiseThreshold, the lowest ratio to its Life of any boss: MEDIUM; it has no separate parts: "
         "MonsterSegment empty, its .ot preloads adds only, MECHD2); it can revive (KulemakBoss.ot on_revive): whether "
         "a revive refills Life is UNVERIFIED (if so, Life here is one life's worth)",
         poise=81, es_from_life=10, inflicts=("Shock", "Chill", "Freeze", "Ignite")),
    Boss("Atziri, the Red Queen", 15, 15, 15, 0, 7091472, "Demon", "Vaal; phase 1; EnergyShieldFromLife 33 (its meaning UNVERIFIED, A22-5)", poise=926,
         es_from_life=33,
         kills=(Kills(45.0, 1.0, "Atziri's Royal Acolytes, killed before they reach you and explode (1 per wave: the "
                                 "wave size is UNVERIFIED, so its lower bound)", "LOW: RESEARCH16 #81"),),
         inflicts=("Ignite", "Shock", "Corrupted Blood")),
    Boss("Atziri, the Fell Serpent", 15, 15, 15, 0, 9461668, "Demon", "Vaal phase 2; EnergyShieldFromLife 33 (its meaning UNVERIFIED, A22-5)", poise=1235,
         es_from_life=33, inflicts=("Ignite", "Shock", "Corrupted Blood")),
)}
# Kill sources (AUDIT16 A16-2): the King's totems and Atziri phase 1's acolytes above. The others: 0 kills. Kulemak
# (Pinnacle Abyssal fodder), Olroth (skeletons, Knights) and the Raven (Delirium minions) have adds at no known rate (0
# until one is found, never a flat guess: UNVERIFIED rate); none found for the Arbiters, Zarokh, Xesht, the Bodach
# (daemons only) or the Aberration (UNVERIFIED as a zero; checked: RESEARCH2 #19 and RESEARCH14 #66 from maxroll /
# poe-vault / Game8 guides and the presets' MonsterVarieties summons). RESEARCH14 also lists one more King summon every
# 15 s in its phase 3: that is the second form's EASVoodooKingSummonDeathAuraEffigy (cooldown 15000, VoodooKingBoss3 only),
# spawning VoodooKingBoss3DeathAuraEffigy, which has no MonsterVarieties row (no Life to read): most likely the effigies
# that can't be killed (MEDIUM-LOW), 0 kills, UNVERIFIED (MECHD2). The 25 s Affliction Totem skill is listed on both
# forms (VoodooKingBoss3MAP.ot preloads the totem too; only its second-form dialogue hook is commented out): whether the
# second form casts it is MEDIUM-LOW, so share 0.43 may undercount (ceiling 1.0): a question for the user.
# The Arbiter of Divinity's helpers (AUDIT22 A22-6; MECHD2, research13 MonsterVarieties + game-26476662 Mods / Tags):
# ArbiterofDivinityOrbitTotem is tagged is_daemon (a skill-casting object), no Monster Category, Life x100: not an add
# (MEDIUM); ArbiterofDivinityBoltClone and ArbiterOfDivinityTriangulateClone are named "Daemon", ObjectSize 0, no drops
# or experience: the boss's skill clones ("Holy Clone Bolts", "Triangulate" in its .ot), not adds (MEDIUM-LOW); the
# Divine Clone ("Aspect of Divinity", phase 2's arena mechanic) has MonsterNoFlaskCharges ("monster_slain_flask_charges")
# so it may be slain, but with the boss's own Life multiplier 990: no add-kill rate from it, 0 (UNVERIFIED; poe2db not
# checked; an in-game question).
# The ailments on the player (`inflicts`): RESEARCH13 #60 (skill damage types and explicit stats), A9-1 (Xesht's corpses:
# Shock, Chill / Freeze), A10-1 (the King's tar swamp Slow), the King's toads and Kulemak's Shamblers' Ignite (MEDIUM-LOW /
# LOW). Poison: none found for any preset (the same search, buff-applied debuffs RESEARCH14 #67; UNVERIFIED as a zero).
# Bleed: Olroth only (the same search; MEDIUM-HIGH).
# The boss's hit cadence (MECHD3; AUDIT18 A18-2): seconds between its damaging skill uses, a LOW estimate from the
# exported tables (research13 MonsterVarieties GrantedEffects / AttackSpeed; game-26476662 GrantedEffects CastTime,
# GrantedEffectsPerLevel Cooldown at the top level; MECHD3's report has the script and its output). The model: the boss
# acts back to back; each cooldown skill is used once per cooldown, each taking its action time (a skill's own CastTime
# where it isn't 1000 ms: six of Olroth's, 2.2-4.5 s; else attacks take the row's AttackSpeed, 1.5 s for most, 2.13 s
# the Arbiter of Ash, 1.335 s the Bodach, and spells 1.0 s; CastTime is 1000 ms on every other skill of the 12
# presets' rows, checked: every GrantedEffect on them); the rest of the time is its basic attack, or a 1.0 s action
# where the row has none; teleports, dodges, summons, hexes and spawns (by skill name) aren't hits; skills that share a
# CooldownGroup (other than 67, the column's default) share one timer (AUDIT18 A18-3's reading, MEDIUM: the Left / Right
# and base / Augment variants). Each damaging use counts one hit, except melee uses: those count their animation's
# strikes (MECHD5, below the table). Why LOW: (1) 1000 ms is a table default, the animations' real lengths (.ast files) weren't read;
# (2) multi-hit uses (Triple Slash, Bullet Hell, combos) land more than one hit, moves and walking between skills land
# none; (3) skills with no cooldown can't be told from their sub-parts in the tables; (4) the AI's skill choice (.ais)
# isn't read; (5) a phase's skills are counted as if all were available at once (fewer per phase would leave more
# time to the filler: a faster rate for the bosses whose cooldowns overfill the time, the Arbiter of Ash above all).
# Every preset reads slower than PoB's 0.7 s placeholder (enemySpeed 700, ConfigOptions.lua 2279 "Enemy attack / cast
# time in ms", no source given; the per-skill speeds in its Data/BossSkills.lua are for its ten PoE1 boss skills,
# Shaper / Sirus / Maven / Exarch / Eater / PoE1 Atziri, checked: the file's ten keys, read only by the
# presetBossSkills option): 0.98 s (the Red Queen) to 2.10 s (the Raven). Counting one hit per spell use and a melee
# use's strikes, this is the FEWEST hits the tables allow (AUDIT25 A25-7: a spell's sub-parts count once), so only the "when hit" benefit counts may read it (benefit_skill_time); every
# defence / recovery reading uses HITS_EVERY or PoB's 0.7 s, whichever is faster (config_vars, the manager 12 Nov 16:50).
# MECHD5: the melee strikes. Each boss's animated-object file (.ao, the row's AOFiles; read-only from the game bundle by
# scratch/mechd5/export5.mjs) has a Melee section listing, per melee animation, its damage_patterns (one hit area each,
# with its start time); the skill reaches its animation through GrantedEffects.Animation -> Animation.Id -> the row's
# .act (action -> animation name, any stance). Patterns that start within 0.05 s of each other are shapes of one swing
# (e.g. the Aberration's slice_01: two cones at 0.52 s), so here a melee use counts its strikes: Ash's Double Slash 2,
# Triple Slash 3, Jump Slam Combo 2; the Arbiter of Divinity's basic melee 2, Extendo Spear Combo 3; Olroth's Cleave 3,
# Dash2 3 (one rectangle three times), its Sword Throw 1 (its six patterns trace the sword out and back: a standing
# target is in as few as one); the Aberration's Shotgun Combos 2; Atziri's Elemental Spear Combo 5 (both phases).
# A second pattern hits a target the first one hit unless the file says otherwise (the Red Queen's attack_02 and the Fell
# Serpent's phase2_attack_01 / phase2_attack_move_01 mark their second pattern previous_patterns_to_exclude_from 1, each
# 0.01-0.04 s after the first; no other pattern carries that key, checked: the Melee sections of the 13 rows' main .ao
# files, Xesht's has none; the attachments' .ao files weren't read). Spells keep one hit per use: their animation events (e.g. the Bodach's slam1-slam4, the Raven's
# first_hit / second_hit / third_hit) agree with HITS_EVERY's part counts, but an event isn't a hit on the player.
# Strike counts assume the player stands in the attack, as the headline does. scratch/mechd5/anim_hits.py,
# cadence5.py (lower / upper), .txt.
HIT_EVERY: dict[str, float] = {
    "Arbiter of Ash": 1.78,  # its cooldowns overfill the time (busy x2.15), so their mix sets the rate; 2.25 s before the strikes
    "Arbiter of Divinity": 1.24,  # 1.36 s before the strikes
    "Xesht, We That Are One": 1.25,
    "The King in the Mists": 1.53,  # first form 2.33 s (43% of the fight), second 1.22 s (57%), by hit rate
    "Zarokh, the Temporal": 1.14,
    "The Raven Trickster": 2.1,
    "The Bodach": 1.27,
    "Olroth, Origin of the Fall": 1.43,  # its own cast times (2.2-4.5 s) fill the time; 2.23 s before the strikes
    "The Aberration": 1.50,  # 1.66 s before the strikes
    "Vessel of Kulemak": 1.12,
    "Atziri, the Red Queen": 0.98,  # 1.13 s before the strikes (Elemental Spear Combo: 5)
    "Atziri, the Fell Serpent": 1.13,  # 1.38 s before the strikes (Elemental Spear Combo: 5)
}
HIT_EVERY_BASIS = ("one damaging skill use every {s:g} s against {name} (LOW: the exported tables' action times are "
                   "defaults, multi-hit uses and moving time unmodelled; MECHD3); PoB's "
                   "placeholder is 0.7 s")


def hit_interval(stats: dict | None = None) -> tuple[float, str]:
    """Seconds between the boss's damaging skill uses (HIT_EVERY, LOW) and its basis, for the boss `stats` names
    (stats["_boss"], else the default). Before any Slow or the player's evasion and block."""
    b = of(stats)
    s = HIT_EVERY[b.name]
    return s, HIT_EVERY_BASIS.format(s=s, name=b.name)


# Hits rather than uses (MECHD4; AUDIT25 A25-7): the same model with each damaging use counted as its number of hits.
# No preset's skill carries a repeat or hit-count stat (checked: every stat on the 13 rows' GrantedEffects' stat sets
# whose id holds number_of / repeat / hit / count / times / wave / combo / pulse / projectile / chain,
# scratch/mechd4/multihit.py; the projectile counts are spreads, whether more than one strikes the player is the
# player's position: each such use counts once). The tables do name the parts of combos: a use counts one hit per G* /
# MP* effect on its row whose Id holds its core name (WingFlapComboLeft / Right / Middle: 3, ExtendoSpearCombo's dash,
# three lacerates and spell projectiles, SlamMeteorComboPunch1-3 + MeteorImpact), else Double 2 / Triple 3. That reads
# alternatives (Left / Right) and sub-effects that may not hit as hits: an UPPER BOUND on the hit rate (LOW;
# MECHD5: a melee use counts every damage pattern of its animation instead (Olroth's Sword Throw 6, its Cleave 3, not 4), the rest as
# above with Jump Slam Combo 3 and Olroth's Dash1 2: the Melee sections' counts replace the name-based parts there;
# scratch/mechd4/cadence_hits.py, .txt). The King: first form 1.89 s, second 1.22 s, weighted 43 / 57 by time.
HITS_EVERY: dict[str, float] = {
    "Arbiter of Ash": 1.00,  # 1.10 s before the strikes
    "Arbiter of Divinity": 0.72,
    "Xesht, We That Are One": 0.91,
    "The King in the Mists": 1.43,
    "Zarokh, the Temporal": 0.83,
    "The Raven Trickster": 1.51,
    "The Bodach": 0.60,
    "Olroth, Origin of the Fall": 0.94,  # 1.74 s before the strikes
    "The Aberration": 1.08,  # 1.25 s before the strikes
    "Vessel of Kulemak": 0.91,
    "Atziri, the Red Queen": 0.51,  # 0.55 s before the strikes
    "Atziri, the Fell Serpent": 1.13,  # 1.38 s before the strikes
}
POB_HIT_EVERY = 0.7  # PoB's enemySpeed placeholder, ConfigOptions.lua 2279 (no source)


def defence_hit_every(name: str | None = None) -> float:
    """Seconds between the boss's hits for every defence / recovery reading (PoB's enemySpeed): the faster of PoB's
    0.7 s and HITS_EVERY (the upper bound on hits), so no defence reads better on the LOW per-boss estimate."""
    return min(POB_HIT_EVERY, HITS_EVERY[name or DEFAULT])


def benefit_skill_time(stats: dict) -> float:
    """enemySkillTime as a "when hit" BENEFIT count may read it (Rage / charges / energy / buffs gained when hit):
    PoB's scored enemySkillTime (the defence cadence, the boss's Slow and Speed in it) rescaled to HIT_EVERY, one hit per
    damaging use, the fewest hits the tables allow (LOW). Never faster than the scored figure. Not for defences."""
    name = of(stats).name
    every = float((stats or {}).get("enemySkillTime") or 0.0) or defence_hit_every(name)
    return max(every, every * HIT_EVERY[name] / defence_hit_every(name))



# "Boss moving" ceiling (MECHD3; RESEARCH22 #122, AUDIT18 A18-3): the most of the fight the boss can spend in its own
# dash / leap / charge skills (names matching Dash, Leap, HeadCharge, SwoopSlam, JumpSlam on its rows' GrantedEffects,
# with a cooldown), each used off cooldown, one timer per shared CooldownGroup, x 0.5 s of travel each (the top of
# RESEARCH22's assumed 0.3-0.5 s: UNVERIFIED, the traversal time is in the .ast files), and Olroth's dashes x their own
# 2.2 s CastTime (the whole action). An UPPER BOUND for the play guide's optional "boss moving" row (MECH-C's), never the
# headline, which stays at standing still. Walking isn't in it: the boss walks only when the player leaves its attack
# range, which the player decides. Teleports don't count (RESEARCH22: MEDIUM-LOW). 0 = no such skill on its rows
# (checked: those names over every GrantedEffect of its rows, MECHD3's moving script; RESEARCH22's table agrees);
# it may still walk.
MOVING_CEILING: dict[str, float] = {
    "Arbiter of Ash": 0.225,  # Dash Slash Left / Right / Forward, Jump Slam Combo: 0.45 uses/s
    "Arbiter of Divinity": 0.03,  # Swoop Slam, 16.5 s (phase 1; Blink Slam taken as a blink)
    "Xesht, We That Are One": 0.0,
    "The King in the Mists": 0.036,  # four dashes on one 6 s timer, first form only (43%); the second form has none
    "Zarokh, the Temporal": 0.0,
    "The Raven Trickster": 0.0,
    "The Bodach": 0.0,  # its Reposition is a burrow (RESEARCH22)
    "Olroth, Origin of the Fall": 0.55,  # three dashes, 12 s each, 2.2 s actions
    "The Aberration": 0.107,  # three leaps, 14 s each
    "Vessel of Kulemak": 0.025,  # Head Charge, 20 s
    "Atziri, the Red Queen": 0.025,  # Lightning Dash, 20 s
    "Atziri, the Fell Serpent": 0.104,  # Beast Dash 12 s, Dash Arc Left / Right on one 8 s timer
}


def moving_ceiling(stats: dict | None = None) -> tuple[float, str]:
    """The most of the fight the chosen boss can spend moving in its own skills (an upper bound, LOW) and its basis."""
    b = of(stats)
    share = MOVING_CEILING[b.name]
    return share, (f"at most {share:.0%} of the fight moving in its own dashes / leaps / charges against {b.name} "
                   "(an upper bound: each used off cooldown, 0.5 s of travel each, UNVERIFIED; walking is the "
                   "player's choice and teleports don't count; MECHD3, RESEARCH22 #122)")

# The default boss: ONE INVENTED WORST-CASE PINNACLE (the user's decision, 12 Nov 2026, via the manager: "invent a
# pinnacle boss that ... takes the highest stats of each and combines them (within reason), so builds return
# conservative numbers"; worst case only, no per-boss table). Per stat, the value worst for the player among the 12 real
# presets above (REAL), each named with the boss it comes from in WORST_SOURCES; a request can still name a real boss.
# Within reason: no immunities (else Freeze / Electrocute builds read 0); where a stat cuts both ways (Life: fight
# length against ramp-up) the Arbiter of Ash's, the old default. Where a MECH-C table is keyed by boss name (boss_hits'
# HIT_TABLE, IGNITE_INCREASE, CORRUPTED_BLOOD) it reads the Arbiter of Ash's row for this name until its owner reads
# worst_hit_sizes / worst_type_shares (asked in MECHD5's report); PoB itself gets the worst hits through config_vars.
WORST = "Pinnacle (worst case)"
REAL = tuple(PRESETS)


def _worst_hits() -> tuple[dict[str, str], dict[str, int]]:
    """({damage type: the real preset with its biggest hit}, pooled skill counts per type over the 12 presets)."""
    from builder.search import boss_hits
    source, pooled = {}, {t: 0 for t in boss_hits.TYPES}
    for t in boss_hits.TYPES:
        dealt = [n for n in REAL if boss_hits.HIT_TABLE[n][1].get(t)]
        source[t] = max(dealt, key=lambda n: boss_hits.HIT_TABLE[n][0])
        for n in REAL:
            pooled[t] += boss_hits.HIT_TABLE[n][1].get(t, 0)
    return source, pooled


WORST_HIT_FROM, WORST_HIT_COUNTS = _worst_hits()


def worst_hit_sizes(reading: str = "headline") -> dict[str, float]:
    """One hit of each damage type at the biggest any real preset deals (boss_hits.hit_sizes of that preset)."""
    from builder.search import boss_hits
    return {t: boss_hits.hit_sizes(n, reading)[t] for t, n in WORST_HIT_FROM.items()}


def worst_type_shares() -> dict[str, float]:
    """Each type's share of the worst case's hits: the 12 presets' damaging skills pooled (LOW, as each boss's own mix;
    which type is worst depends on the build, so no single type is weighted up: the survival check meets every type's
    biggest hit instead)."""
    total = sum(WORST_HIT_COUNTS.values())
    return {t: c / total for t, c in WORST_HIT_COUNTS.items()}


def _worst_red_flash() -> tuple[tuple[float, str], tuple[float, str]]:
    from builder.search import boss_hits
    share = {n: boss_hits.RED_FLASH[n] for n in REAL}
    una = max(REAL, key=lambda n: share[n][0] / share[n][2])
    eva = max(REAL, key=lambda n: share[n][1] / share[n][2])
    return (share[una][0] / share[una][2], una), (share[eva][1] / share[eva][2], eva)


(_UNAVOIDABLE, _UNA_FROM), (_UNBLOCKABLE, _EVA_FROM) = _worst_red_flash()
_POISE_FROM = max(REAL, key=lambda n: PRESETS[n].poise)
_ES_FROM = max(REAL, key=lambda n: PRESETS[n].es_from_life)
WORST_SOURCES = {
    "resistances": "Fire / Cold / Lightning 45 / 45 / 45: the highest of each (Fire: the Arbiter of Ash and the "
                   "Aberration; Cold: Olroth; Lightning: the Arbiter of Divinity); Chaos 25: the user's choice (12 Nov "
                   "2026), no real pinnacle has any (checked: MonsterResistances, the boss .ot files, PinnacleAtlasBoss, "
                   "poe2db), 25 matching the largest map / area 'additional chaos resistance' mods in the game data "
                   "(+25), whether or not they reach a pinnacle arena (UNVERIFIED that they do)",
    "Life": "the Arbiter of Ash's 9,461,668 (it cuts both ways: fight length against ramp-up; the old default's)",
    "Monster Category": "none: no 'against <category>' line applies (Demon, Humanoid, Beast, Construct and Undead "
                        "lines all read 0)",
    "stun threshold": f"{PRESETS[_POISE_FROM].poise}% ({_POISE_FROM}), the highest",
    "Heavy Stun duration": "4 s (BossBase's, every preset but Olroth's 6.07 s): the shortest, worst for the player",
    "Energy Shield from Life": f"{PRESETS[_ES_FROM].es_from_life}% ({_ES_FROM}): the highest, so Culling Strike's "
                               "share of the pool is the smallest",
    "immunities": "none (within reason: Zarokh's Freeze / Electrocute immunity would zero those builds)",
    "adds": "none: on-kill lines read 0 (the King's totems and Atziri's acolytes left out)",
    "hits": "each damage type at the biggest any preset deals (" + ", ".join(
        f"{t}: {n}" for t, n in WORST_HIT_FROM.items()) + "); the type mix pooled over the 12 presets' damaging skills",
    "unavoidable hits": f"{_UNAVOIDABLE:.0%} with no evade / block roll ({_UNA_FROM}), {_UNBLOCKABLE:.0%} evadable "
                        f"but not blockable ({_EVA_FROM}): the highest of each",
    "cadence": "defences at the fastest upper bound on hits (the smallest HITS_EVERY), 'when hit' benefits at the "
               "slowest one-hit-per-use rate (the largest HIT_EVERY)",
    "ailments on you": "every ailment any preset inflicts (for defence readings; not a rate for a 'while you are "
                       "<ailment>' benefit)",
    "Slow taken": "50% less, as every Unique",
}
PRESETS[WORST] = Boss(
    WORST, 45, 45, 45, 25, PRESETS["Arbiter of Ash"].life, None,
    "an invented worst case (the user, 12 Nov 2026): " + "; ".join(f"{k}: {v}" for k, v in WORST_SOURCES.items()),
    poise=PRESETS[_POISE_FROM].poise, heavy_stun_s=min(PRESETS[n].heavy_stun_s for n in REAL),
    es_from_life=PRESETS[_ES_FROM].es_from_life,
    inflicts=tuple(dict.fromkeys(a for n in REAL for a in PRESETS[n].inflicts)),
    unavoidable=_UNAVOIDABLE, unblockable=_UNBLOCKABLE)
HIT_EVERY[WORST] = max(HIT_EVERY[n] for n in REAL)
HITS_EVERY[WORST] = min(HITS_EVERY[n] for n in REAL)
MOVING_CEILING[WORST] = max(MOVING_CEILING[n] for n in REAL)  # (the play guide's optional row: the most a boss moves)

DEFAULT = WORST
SOURCE ="poe2db's monster rows, read 12 Nov 2026 (game 0.5.5), RESEARCH9; MEDIUM-HIGH"


def of(stats: dict | None = None) -> Boss:
    """The preset a calculation is scored against: stats["_boss"], else the default."""
    return PRESETS.get((stats or {}).get("_boss") or DEFAULT, PRESETS[DEFAULT])


def threshold_scale(stats: dict | None = None) -> float:
    """PoB's Heavy Stun / Freeze / Electrocute / Pin buildup x this = the buildup against the boss (938 / its poise)."""
    return POB_POISE / of(stats).poise


def kills_per_second(name: str = DEFAULT) -> tuple[float, str]:
    """Killable adds per second over the whole fight (every add killed: a build that takes on-kill lines plays to kill
    them, CLAUDE.md's second rule), and how."""
    b = PRESETS[name]
    if not b.kills:
        return 0.0, f"no killable adds with a known rate against {name} (research #19 / #66)"
    rate = sum(k.share / k.every for k in b.kills)
    return rate, "; ".join(f"one per {k.every:g} s for {k.share:.0%} of the fight: {k.what} ({k.confidence})"
                           for k in b.kills)


KILLS_STAT, CULLS_STAT = "AddKillsPerSecond", "AddCullsPerSecond"


def kill_stats(name: str = DEFAULT) -> dict:
    """The boss-side numbers models read from the stats: the preset's name, its add kills and Culls per second. Culls
    are counted as the kills (whether the build can Cull is the reader's check). An add that one hit kills outright
    isn't Culled in game: not modelled, a possible overcount for Cull-triggered lines (LOW: the King's Affliction Totems
    have x0.04 Life)."""
    rate, _ = kills_per_second(name)
    return {"_boss": name, KILLS_STAT: rate, CULLS_STAT: rate}


# Culling Strike against the boss (MECHD4, A22-5). KeywordPopups CullingStrike: "Culling Strikes kill ... Unique at 5%.
# These thresholds are checked before the damage of the [Hit] is applied": a share of the boss's Life. PoB counts it as
# CullMultiplier = 100 / (100 - 5) (CalcOffence 4071-4079, 6467-6470: x1.053 on CombinedDPS for any build that can
# Cull), as if the whole pool the player takes off were Life. Energy Shield is taken off before Life and isn't Culled,
# so the true gain is 1 / (1 - 5% x Life / (Life + ES)): PoB's is an OVERCOUNT for the bosses with Energy Shield.
# The ES share: MonsterTypes.EnergyShieldFromLife f%, read as PoB reads it for monsters (Export/Scripts/minions.lua
# writes MonsterTypes' value as energyShield, CalcPerform 1066 applies it as LifeConvertToEnergyShield: f% of base Life
# converted) and as the game's own stat name reads (Stats unscaled_maximum_life_%_to_convert_to_maximum_energy_shield:
# of unscaled Life); then the Unique rarity mod MonsterUnique2 scales Life x29 (monster_life_+%_final_from_rarity 2800)
# and ES x5 (monster_energy_shield_+%_final_from_rarity 400). So ES / Life = 5f / (29 (1 - f)): 1.9% of Life at 10%,
# 8.5% at Atziri's 33%. MEDIUM-LOW (which stat MonsterTypes' column feeds isn't in the exported files: no .ot names it,
# checked research19's Monster.ot / BossBase.ot / PinnacleBossBase.ot and the presets' own .ot). If ES were instead f%
# of the final Life, the gain would fall further (Atziri x1.042 instead of x1.048): this reading is the smaller cut.
# No preset is Cull-proof (checked: the only Mods row with immune_to_culling_strike / cannot_be_culled is
# MonsterCannotBeCulled, on none of MonsterVarieties' rows' Mods / Mods2; no exported .ot names a cull stat).
# Forms and lives (the King's two forms, Kulemak's revive, Atziri's two phases) each Cull at 5% of their own Life,
# the same share of the whole fight, so they don't change it (UNVERIFIED that a form that transforms can be Culled).
CULL_UNIQUE = 5.0  # % of Life (KeywordPopups CullingStrike; PoB's CullingStrikeUniqueThreshold, Data/Misc.lua 108)
RARITY_LIFE, RARITY_ES = 29.0, 5.0  # MonsterUnique2: +2,800% / +400% final


def life_share_of_pool(name: str = DEFAULT) -> float:
    """The boss's Life as a share of Life + Energy Shield (the reading above; 1.0 with no ES)."""
    f = PRESETS[name].es_from_life / 100
    life, es = RARITY_LIFE * (1 - f), RARITY_ES * f
    return life / (life + es)


def cull_threshold(name: str = DEFAULT) -> float:
    """PoB's Unique Culling Strike threshold to use against this boss: 5% x Life / (Life + ES), so PoB's
    CullMultiplier, 100 / (100 - it), is the gain over the whole pool (MEDIUM-LOW, above)."""
    return round(CULL_UNIQUE * life_share_of_pool(name), 4)


def config_vars(name: str = DEFAULT) -> dict[str, float]:
    """PoB config inputs that set the boss's resistances, its hit cadence and its Culling threshold (bridge resetConfig's
    `boss` argument): enemySpeed is defence_hit_every in ms (700, PoB's, unless the upper bound on hits is faster: the
    Bodach 600, Atziri the Red Queen 550; MECHD4);
    cullingStrikeUniqueThreshold isn't a PoB config input: the bridge writes it into PoB's game constant (MECHD4)."""
    from builder.search import boss_hits
    b = PRESETS[name]
    # The boss's hits per type (MECHD5): the bridge wrote the Arbiter of Ash's for every boss (nothing passed bossHits),
    # so a named boss's PoB hits were the Arbiter's while boss_hits weighed them by its own mix; now its own sizes
    # (boss_hits.config_vars), the worst case's from worst_hit_sizes.
    hits = ({f"enemy{t}Damage": round(v) for t, v in worst_hit_sizes().items()} if name == WORST
            else boss_hits.config_vars(name))
    return {"enemyFireResist": b.fire, "enemyColdResist": b.cold, "enemyLightningResist": b.lightning,
            "enemyChaosResist": b.chaos, "enemySpeed": round(defence_hit_every(name) * 1000),
            "cullingStrikeUniqueThreshold": round(cull_threshold(name), 4), **hits}


def note(name: str = DEFAULT) -> dict:
    """The report's labelled row for the boss the numbers are scored against."""
    b = PRESETS[name]
    kills, how = kills_per_second(name)
    category = (b.category if isinstance(b.category, str) else
                ", ".join(f"{c} {s:.0%}" for c, s in b.category.items()) if b.category else
                "none (chosen: no 'against <category>' line applies)" if name == WORST else "unknown (UNVERIFIED)")
    source = ("the highest of each among the 12 real presets (" + SOURCE + ")" if name == WORST else
              f"the boss's own resistances ({SOURCE})")
    return {"label": f"Boss: {b.name}", "value": f"Fire {b.fire}% / Cold {b.cold}% / Lightning {b.lightning}% / "
                                                  f"Chaos {b.chaos}%",
            "basis": f"{source}, not PoB's default Pinnacle 50/50/50; Penetration stops at "
                     "0% (PoB and the game agree). Other pinnacles differ: elemental damage moves up to x2.3 between "
                     f"them. Life {b.life:,} at level 82; Monster Category {category}; stun threshold {b.poise}% "
                     f"(PoB's buildup x{threshold_scale({'_boss': name}):.2f}); Heavy Stun {b.heavy_stun_s:g} s; "
                     f"Energy Shield {1 / life_share_of_pool(name) - 1:.1%} of Life (MEDIUM-LOW), so Culling Strike "
                     f"takes {cull_threshold(name):g}% of the pool, not 5%; hits every {defence_hit_every(name):g} s for "
                     f"defences, every {HIT_EVERY[name]:g} s for \"when hit\" benefits (LOW); "
                     f"add kills {kills * 60:.2f}/min ({how})" + (f"; {b.note}" if b.note else ""),
            "confidence": "MEDIUM-HIGH (recheck after the 12 Nov game update)"}


# "Ignited yourself" from the boss (MECHD4; MECH53's note): the box (conditions' conditionIgnited) was on only for
# Grinning Immolation's self-Ignite. The boss Ignites you too: its landed Fire hits x MECH-C's chance per hit
# (boss_hits.ignite_chance: Flammability from the hit's size against your Ailment Threshold, KeywordPopups Flammability,
# MEDIUM-LOW) x (1 - your Ignite avoidance), each lasting 4 s x your SelfIgniteDuration (KeywordPopups Ignite). Taken
# as a Poisson stream: Ignited 1 - e^(-rate x duration) of the fight. A BENEFIT count (the "while Ignited" lines it
# turns on), so it reads benefit_skill_time (one hit per use, the fewest hits): the smaller uptime.
def self_ignite_uptime(stats: dict) -> tuple[float, str]:
    """Share of the fight the boss keeps you Ignited, and how (0 when it deals no Fire or you can't be Ignited)."""
    import math

    from builder.search import boss_hits  # (boss_hits reads this module's PRESETS)

    def num(key: str, default: float = 0.0) -> float:
        try:
            return float(stats.get(key, default) or 0.0)
        except (TypeError, ValueError):
            return default

    name = boss_hits.boss_name(stats)
    fire = boss_hits.type_shares(name)["Fire"]
    avoid = min(max(num("IgniteAvoidChance"), 0.0), 100.0) / 100
    if fire <= 0 or avoid >= 1:
        return 0.0, (f"{name} deals no Fire hits" if fire <= 0 else "you can't be Ignited") + ": not Ignited by the boss"
    stats = {**stats, "enemySkillTime": benefit_skill_time(stats)}
    land = boss_hits.landing_hits(stats)
    chance, how = boss_hits.ignite_chance(stats, land)
    seconds = boss_hits.IGNITE_SECONDS * (num("SelfIgniteDuration") / 100 if "SelfIgniteDuration" in stats else 1.0)
    rate = land * fire * chance * (1 - avoid)
    share = 1 - math.exp(-rate * seconds)
    return share, (f"the boss Ignites you {rate * 60:.1f} times a minute ({land:.2f} landed hits/s, {fire:.0%} Fire; "
                   f"{how}; {avoid:.0%} avoided), {seconds:g} s each: Ignited {share:.0%} of the fight (LOW: the "
                   "hit cadence is the preset's estimate)")
