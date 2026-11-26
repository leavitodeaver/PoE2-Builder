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

import math
from typing import NamedTuple


class Kills(NamedTuple):
    """Killable adds in a boss fight (MECHD1; AUDIT16 A16-2, the manager's ruling): one add every `every` s while the
    fight is in the part that summons them, `share` of the fight (by Life, at equal DPS). (MECHC46; RESEARCH36 R36-6,
    boss-preset-add-life.md) `life`: one add's Life at the pinnacle level 82 (PoB's monsterLifeTable 32,956 x its
    MonsterVarieties LifeMultiplier x Normal rarity: the adds carry only MonsterNoDropsOrExperience; HIGH for the
    multipliers, MEDIUM for Normal / level 82), 0 = not known; `life_rows`: other readings, (label, Life)."""
    every: float
    share: float
    what: str
    confidence: str
    life: float = 0.0
    life_rows: tuple = ()
    wave: int = 1  # (MECHD7) adds a summon makes; 1 = one, or the lower bound where the size is unknown (said in `what`)
    # (MECHD9, the manager's 26 Nov brief item 2; R61-15) where the adds die relative to the boss: ((metres from the
    # boss's centre, share of the adds dying within it), ...) ascending, from video; () = not known (readers: 0)
    near: tuple = ()
    near_how: str = ""


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
    # (MECHD7; PLANS6's kw-culling-strike.md, the manager 03:48 21 Nov) whether Culling Strike can kill it: False gates
    # every culling model to 0 (cull_threshold). True for all 12 (the comment above cull_threshold).
    cullable: bool = True
    # (MECHD9; MECH112's idea) the monster level the boss is meant at. Every preset is at 82: PoB's Pinnacle level
    # (ConfigOptions.lua's enemyLevel tooltip: "The default and minimum level for pinnacle bosses and uber pinnacle
    # bosses is 82") and the manager's 26 Nov decision (poe2wiki's pinnacle area levels 80-82). No reader yet: the
    # engine's level stays PoB's own (assumptions.py's LedgerEnemyLevel reports it). Per-boss levels: not set.
    level: int = 82


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
                      "MEDIUM: summon cooldown, RESEARCH14 #66",
                      # (R36-6: which of the two the 25 s summon makes isn't settled: the Affliction Totem, LifeMultiplier
                      # 4%, the lower reading, headlines; the Mystic Fetish, 50%, a row. PoB's normal-monster stand-in
                      # reads x25 / x2 too much for them)
                      1318.0, (("Mystic Fetish (VoodooKingBoss2RitualPillar, LifeMultiplier 50%)", 16478.0),)),),
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
                                 "wave size is UNVERIFIED, so its lower bound: R42-22's three video tries counted none, one weak guide "
                                 "says they 'swarm'; normal monsters, not Rare: no summon skill of hers sets a rarity, "
                                 "R42-21, MEDIUM-LOW, UNVERIFIED)", "LOW: RESEARCH16 #81, RESEARCH42 R42-21 / R42-22",
                      32956.0),),  # (R36-6: VaalZealotSacrifice, LifeMultiplier 100%: PoB's normal-monster Life exact)
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
# Add waves re-checked on game 26650308 (MECHD7, the manager's 21 Nov brief item 2; GrantedEffectsPerLevel Cooldown of every
# summon / spawn skill on the presets' MonsterVarieties rows (research13's export) and of every GrantedEffect whose Id holds
# a preset's stem and Summon / Spawn / Minion / Totem / Effigy / Cultist; scratch/mechd7's scans):
# - the King: EASVoodooKingSummonEffigy 25 s on both forms' rows (VoodooKingBoss3MAP lists it beside
#   EASVoodooKingSummonDeathAuraEffigy 15 s): the 25 s row stands (the second-form question above stays open);
# - Atziri phase 1: EASAtziriSummonCultists 45 s, no stat giving the wave's size (its stat set holds only
#   action_attack_or_cast_time_uses_animation_length): 1 a wave, the lower bound, stands;
# - not adds: Xesht's EASBreachBossSummonEsh / Tul / EshTul (10 s) make EshCorpse / TulCorpse, tagged is_daemon (the
#   game's effect actors; HIGH); the Arbiter of Ash's EASDemonClawBossFlameSeedSpawn (20 s) / MultiSpawn (60 s) make no
#   MonsterVarieties row (ground seeds); Atziri's EASAtziriSummonClones (60 s, both phases) make boss copies with the
#   boss's map-boss mods and x1.86 / x2.47 Life (RESEARCH14 #66: not a practical kill source, MEDIUM);
# - an UNVERIFIED kill source, not counted: the Aberration's GTCyclopsSpiderSpawnVerisium, one Verisium Web (VerisiumNode,
#   tags construct / expedition_monster, 1 Life on poe2db) every 30 s. Whether the player can target and kill it, and
#   whether it counts as a kill, is in no source (RESEARCH14 #66: LOW): 0 until settled (a research question, MECHD7);
#   if it can, one kill a 30 s.
# - Kulemak, Olroth and the Raven: their adds exist, but no summon skill sits on the boss's own rows (checked:
#   KulemakBoss, Olroth and TangmazuBoss's GrantedEffects; the Delirium minions' summon GTCorruptedEssenceDeliriumMinion-
#   Summon, 8 s, belongs to MonsterCorruptedEssenceDelirium, a map Delirium monster, not this fight): no rate, 0, UNVERIFIED.
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
# One damaging USE every N s, a melee use counted once however many strikes its animation holds (MECHD8; the manager
# 08:22 21 Nov: R37-17's "one event per USE", the manager 20 Nov). HIT_EVERY above is MECHD5's "lower" cadence, which
# counts a melee use as its animation's strikes (scratch/mechd5/cadence5_lower.txt's "1 -> k strikes" list: the Arbiter
# of Ash's Double / Triple Slash and Jump Slam Combo, the Arbiter of Divinity's phase 2 Basic Melee and Extendo Spear
# Combo, Olroth's Cleave and Dash2, the Aberration's two Shotgun Combos, Atziri's Elemental Spear Combo in both phases;
# no other preset has one): these are its "before the strikes" values, as HIT_EVERY's comments give them. The rest equal
# HIT_EVERY. Checked against scratch/mechd8/redshare.py's own use count (one a use): 26.7, 44.2, 26.9, 36.2, 53.0, 43.4
# a minute for the six, matching 60 / these within 0.3%. Read by the per-use readings (boss_hits.ailments_on_you /
# ailment_uptime, red_flash_rate, landed_cross_check; MECH-C's ailment_hits / chill_and_freeze_on_you once it moves);
# HIT_EVERY stays for the defence floors' cadence and the "every strike" rows. RESEARCH41's VDA Arbiter of Ash: 21
# attacks in 41 s (30.7 a minute), between this 26.7 and HIT_EVERY's 33.7 (LOW: one fight).
USE_EVERY: dict[str, float] = {
    **HIT_EVERY,
    "Arbiter of Ash": 2.25,
    "Arbiter of Divinity": 1.36,
    "Olroth, Origin of the Fall": 2.23,
    "The Aberration": 1.66,
    "Atziri, the Red Queen": 1.13,
    "Atziri, the Fell Serpent": 1.38,
}
USE_EVERY_BASIS = ("one damaging skill USE every {s:g} s against {name}, a melee combo counted once (LOW: the exported "
                   "tables' cooldowns, each used when ready; MECHD5 / MECHD8)")
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
# The boss's Melee share (MECH76; the manager 15 Nov 03:52, for MECH-C's Stuns on you: KeywordPopups Stun, game
# 26476662: "Monster Physical damage and Melee Damage have 100% more and 33% more Light Stun chance and Heavy Stun
# buildup respectively"; patch 0.3.0 says the same, "purely a description change", no later entry, --history "Light
# Stun"). The share of each preset's damaging skills that carry the Melee type (RESEARCH9's boss-presets-0-5.md
# melee_lo_pct, from poe2db's monster rows: MEDIUM as a share of skills, LOW as a share of hits, the AI's choice and
# MECHD5's multi-strike melee uses not weighted in). Its melee_hi_pct also counts slams / slashes that aren't
# Melee-typed (the other reading, in the note); the bonus names Melee Damage, so the Melee type is read. The King in the
# Mists: its two forms' 17% / 12% weighted 43 / 57 by time (HIT_EVERY's note). WORST: the highest real preset's (the
# Fell Serpent's 38%), as WORST takes each stat at its worst for the player.
MELEE_SHARE: dict[str, tuple[float, float]] = {  # (Melee-typed share, the slams / slashes reading)
    "Arbiter of Ash": (0.12, 0.53),
    "Arbiter of Divinity": (0.12, 0.62),
    "Xesht, We That Are One": (0.08, 0.33),
    "The King in the Mists": (0.43 * 0.17 + 0.57 * 0.12, 0.43 * 0.67 + 0.57 * 0.25),
    "Zarokh, the Temporal": (0.0, 0.14),
    "The Raven Trickster": (0.03, 0.33),
    "The Bodach": (0.17, 0.54),
    "Olroth, Origin of the Fall": (0.29, 0.67),
    "The Aberration": (0.19, 0.52),
    "Vessel of Kulemak": (0.20, 0.40),
    "Atziri, the Red Queen": (0.19, 0.30),
    "Atziri, the Fell Serpent": (0.38, 0.56),
}


def melee_share(stats: dict | None = None) -> tuple[float, str]:
    """(the share of the boss's hits that are Melee, how) for the boss `stats` names (MELEE_SHARE's note; LOW)."""
    b = of(stats)
    if b.name in MELEE_SHARE:
        share, wide = MELEE_SHARE[b.name]
        where = b.name
    else:  # (WORST and any preset without a row: the highest real preset's)
        where, (share, wide) = max(MELEE_SHARE.items(), key=lambda kv: kv[1][0])
        where = f"the worst case: {where}'s"
    return share, (f"{share:.0%} of the boss's damaging skills are Melee ({where}; {wide:.0%} counting its slams and "
                   "slashes that aren't Melee-typed): a share of skills, not weighted by use (LOW)")


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


# Boss RELOCATIONS (MECHD9; the manager's 26 Nov brief, item 1): how often the boss's ground position changes and how
# far, for skills that need the boss to come to something laid on the ground (Ice Shards' armed bonus: a shard set off
# t s after it arms; Bone Cage: the boss must touch a spike on its ring; ground hazards generally). Unlike
# MOVING_CEILING (time spent travelling, for "while moving" lines) a relocation counts teleports and walks too: each puts
# the boss somewhere new. Readings (relocations a minute, metres each, how):
# - "skills": the preset's own Dash / Leap / Charge / Swoop / Jump Slam / Teleport / Blink / Warp / Reposition skills
#   used off cooldown, one timer per CooldownGroup (scratch/mechd9/relocate.py, MECHD3's moving.py with the teleports
#   added; game 26476662's GrantedEffectsPerLevel; HIGH for the cooldowns), an UPPER BOUND (the AI also picks its many
#   cooldown-0 skills); metres: up to its MaximumAttackDistance plus the skills' attack_maximum_action_distance_+
#   (game 26650308), / 10 (an upper bound on a gap-closer's reach; teleports' reach isn't in the tables). The King's
#   first form only (43% of the fight; its second form has none). Walking isn't in it (the player decides it).
# - "video": RELOCATE_VIDEO's tallies from fight videos (the user's rule: how something moves comes from visuals).
# - "headline": the video tally where one exists, else 0 with a note (CLAUDE.md: no source, 0; never the ceiling: the
#   readers are DPS GAINS, and the ceiling is an upper bound).
# The worst case (AUDIT29 A29-1): a gain reads the lowest real preset, a harm the highest (`gain`).
RELOCATE_SKILLS: dict[str, tuple[float, float, str]] = {  # (uses a minute off cooldown, metres up to, the skills)
    "Arbiter of Ash": (33.0, (17 + 0) / 10, "Teleport 10 s, Dash Slash Left 8 s / Right 8 s / Forward 10 s, Jump Slam "
                       "Combo 10 s"),
    "Arbiter of Divinity": (10.35, 32 / 10, "Swoop Slam 16.5 s, Blink Slam 12 s, Storm Blink 35 s; NOT a true ceiling: "
                            "its phase-2 Dodge Left / Right have no cooldown (RESEARCH65 R65-9: the .act maps them to "
                            "ascended_blink animations), uncounted here"),
    "Xesht, We That Are One": (6.0, 14 / 10, "Teleport Start 10 s"),
    "The King in the Mists": (round(0.43 * 37.5, 2), (15 + 15) / 10,
                              "first form only (43%): Teleport 8 s (+1.5 m beyond its target), Teleport Flee 3 s, four "
                              "Dashes on one 6 s timer"),
    "Zarokh, the Temporal": (7.5, 12 / 10, "Teleport 10 s, Teleport Centre 40 s"),
    "The Raven Trickster": (22.5, 25 / 10, "Teleport, Mirror Teleport, Mirror Teleport Exit (8 s each)"),
    "The Bodach": (4.0, (18 + 100) / 10, "Reposition (a burrow) 15 s"),
    "Olroth, Origin of the Fall": (15.0, (23 + 40) / 10, "Dash 1 / 2 / 3, 12 s each (+2.9 / +3.5 / +4.0 m)"),
    "The Aberration": (12.86, (35 + 70) / 10, "Leap Back / Leap Forward / Leap Forward Far, 14 s each (+5 / +7 / +7 m)"),
    "Vessel of Kulemak": (5.4, (14 + 80) / 10, "Teleport Slam / Teleport Escape (one 25 s timer, +8 m), Head Charge 20 s"),
    "Atziri, the Red Queen": (7.0, 22 / 10, "Lightning Dash 20 s, Blood Warp 15 s"),
    "Atziri, the Fell Serpent": (17.0, (22 + 100) / 10, "Beast Teleport 20 s (+10 m), Beast Dash 12 s (+5 m), Dash Arc "
                                 "Left / Right one 8 s timer (+10 m), Teleport Centre"),
}
# (relocations a minute, mean metres, share of the fight in standing spells of 3.5 s or more (None: not read), source,
# confidence). MECHD9's three video helpers, 26 Nov 2026 (scratch/mechd9/video_mv1.md / mv2.md / mv3.md, the brief
# scratch/mechd9/video_brief.md): every move of the boss's GROUND position by 1 m or more (walks, dashes, leaps,
# teleports), read by eye from 1 fps frames against arena landmarks, metres scaled from the player's and boss's bodies
# (MEDIUM-LOW). Where a helper gave a range, the LOW END here (for gains; RELOCATE_VIDEO_HIGH below for harms). The boss follows a moving player,
# so the count depends on the play (kiting fights read higher); the fights used are named. Not tallied (0 in the
# headline, the "skills" ceiling a row): the Bodach (no usable footage in 7 videos: the boss invisible in its dark arena,
# its minimap blank or hidden, 2 downloads refused by YouTube's bot check and skipped).
RELOCATE_VIDEO: dict[str, tuple[float, float, float | None, str, str]] = {
    "Arbiter of Divinity": (round(0.11 * 9.4 + 0.89 * 13.5, 2), round(0.11 * 3.9 + 0.89 * 9.9, 2),
                            round(0.11 * 0.68 + 0.89 * 0.24, 2),
                            "1 fight (PrKw5fi184k, 0.5, a ranged Tactician's 14-minute kill), the boss's ground position "
                            "read from the minimap's skull against the sliding arena outline (MECHD9 helper mv4; 6 px a "
                            "metre +-25%), by phase at that fight's split (11% / 89%, MEDIUM-LOW: a faster kill differs): "
                            "phase 1 9.4 a minute, 3.9 m, 68% in stands of 3.5 s or more; phase 2 13.5+ a minute (an "
                            "undercount: runs of a jump a second counted once), 9.9 m (18 fast moves of 5-24 m in 120 s, "
                            "more than Blink Slam / Storm Blink allow off cooldown: UNVERIFIED which skill), 24%",
                            "MEDIUM-LOW (the rate), LOW-MEDIUM (distances)"),
    "Arbiter of Ash": (8.0, 4.0, 0.55, "3 melee fights, 153 s (VDA ssQ3UovP9js, Lambark 2tcYuXWnbI4, SlayerD "
                       "TuRYrMaHIUU Uber): 9.5 pooled, 8-12; half its own leaps / dashes, half walks; it casts beams and "
                       "rings in place, 55-70% in stands of 3.5 s or more", "big moves MEDIUM, walks LOW"),
    "Olroth, Origin of the Fall": (12.0, 3.0, 0.30, "2 melee fights, 105 s (echoburn UoaVj5Xg1kA, a 0.5 HCSSF kill): "
                                   "14 pooled, 12-17; a third dashes, two thirds walks", "LOW"),
    "Xesht, We That Are One": (2.0, 3.5, 0.50, "3 fights, ~125 s: a 0.5 minion build 64 s (5-7), a 0.4 Uber Monk ~35 s "
                               "(zPvOeWQS-Kg, half readable), a 0.5.3 Rampage Titan 26 s (7yIfEfQ2g3Q, 7-9); ~6 pooled, "
                               "2-9; a 3-7 m submerge / re-emerge from a big pink ring about every 25 s, 1-2 m walks; "
                               "50-70% in stands of 3.5 s or more (the low end)", "LOW"),
    "The King in the Mists": (round(0.43 * 7.5 + 0.57 * 12.0, 2), round(0.43 * 5.0 + 0.57 * 3.0, 2), None,
                              "by form, 43% / 57% of the fight: the first form 7.5 a minute, all its own dashes (4-5 m) "
                              "and teleports (~6 m), 75-80% in stands of 3.5 s or more (2 fights, 72 s: 0.3 Uber, 0.5); "
                              "the second form ~12, 2-4 m walks (1 fight, 29 s)", "LOW (the first form LOW-MEDIUM)"),
    "Zarokh, the Temporal": (15.0, 3.5, 0.15, "3 short fights: a 0.5 melee Monk ~32 s, a 0.4 druid ~45 s (MECHD9 helper "
                             "mv3: 20-40 a minute against the melee player, 6-12 of them blinks) and a 0.4 wolf druid "
                             "35 s (CFhwxtukIAk, mv6: 15-22, ~3 m, it drops back in beside the player each time it "
                             "returns); pooled 15-40, ~3-4 m, ~15% in stands of 3.5 s or more; a ranged 0.5 fight "
                             "looked as busy (not counted). Off the field 12-14 s a fight in its Trial of Time phase "
                             "and, in one fight, two ~17-18 s time-bubble phases (~52% of that 90 s fight; not in the "
                             "count)", "LOW"),
    "The Raven Trickster": (12.0, 3.0, None, "1 ranged fight, ~50 s: 12-20 a minute, nearly all walks after the kiting "
                            "player, changing sides every 3-5 s", "LOW"),
    "The Aberration": (20.0, 1.5, None, "1 melee fight (0.5 Titan standing still, 48 s): its body shifted 1-3 m every "
                       "1-3 s within ~5 m, and left once (8 m+) to cast its web, back in 4-5 s", "LOW"),
    "Vessel of Kulemak": (3.2, 3.9, 0.88, "2 melee fights, 0.5, 138 s on the field (doM34Ispb3s 45 s: 5.3-6.7 a "
                          "minute; HPWwUlvNAJo 93 s: 3.2, one ~13 m teleport); the boss from the minimap's skull "
                          "(MECHD9 helper mv7); it barely moves in the reduced stages (0-1 moves each), the moves come "
                          "in the final; pooled 3.9, 3.2-6.7, mean 3.9 m (median 2 m), 88-95% in stands of 3.5 s or "
                          "more; no ranged fight", "MEDIUM-LOW (counts MEDIUM, distances LOW)"),
    "Atziri, the Red Queen": (13.0, 4.0, 0.20, "1 ranged kiting fight (0.4, 45 s): 2-8 m, mostly walks and dashes "
                              "toward the player", "LOW"),
    "Atziri, the Fell Serpent": (17.0, 2.5, 0.10, "2 fights, 93 s: a 0.4 Uber caster, 52 s (she circles the player on a "
                                 "~14 m platform, 30-40 a minute, ~8 dashes of 4-6 m, moving 75-85%) and a likely-0.5 "
                                 "Whirling Assault Monk, 41 s (6We-2J58m-0: 17-18 a minute, mostly 1-2.5 m shifts in a "
                                 "~2.5 m box, an own move every 10-20 s); ~27 pooled, 17-40; 10-17% in stands of 3.5 s "
                                 "or more (the low end); so the 'immobile' note on HIT_RADIUS isn't that she stands "
                                 "still", "LOW-MEDIUM"),
}
# The HIGH end of each tally's range (MECHD9 turn 2): read where movement is a HARM to the player (relocations(gain=False):
# stay_share / still_there), as the low ends above are read where it is a gain. Equal where one figure was given.
RELOCATE_VIDEO_HIGH: dict[str, float] = {
    "Arbiter of Ash": 12.0, "Arbiter of Divinity": round(0.11 * 9.4 + 0.89 * 13.5, 2), "Olroth, Origin of the Fall": 17.0, "Xesht, We That Are One": 9.0,
    "The King in the Mists": round(0.43 * 7.5 + 0.57 * 12.0, 2), "Zarokh, the Temporal": 40.0,
    "The Raven Trickster": 20.0, "The Aberration": 20.0, "Vessel of Kulemak": 6.7, "Atziri, the Red Queen": 13.0, "Atziri, the Fell Serpent": 40.0,
}
RELOCATE_READINGS = ("headline", "skills", "video", "any")
# Which tallies may set the WORST CASE's movement-HARM headline (the manager 26 Nov 06:32: "don't let one LOW figure drive
# the worst case. Headline = the highest high end among bosses counted from at least two fights or at MEDIUM or better";
# Zarokh's 40 a minute a labelled row until more fights confirm it). A fight counts when its tallied window is 45 s or
# more (the helpers' brief, scratch/mechd9/video_brief.md); fights tallied / fights of 45 s+, from video_mv1-7.md. Today
# the Arbiter of Ash (61 s, 49 s) and Kulemak (45 s, 93 s) have two, and no tally is MEDIUM or better overall, so the
# Arbiter of Ash's high end (12 a minute; Kulemak's is 6.7) sets the headline (the manager confirmed the 45 s reading,
# 26 Nov 06:46); reading "any" (every tally) gives Zarokh's 40 as the row. Read literally ("two
# fights", any length) the Fell Serpent (52 s + 41 s) would set it at 40, also from one (caster) fight: not taken.
RELOCATE_FIGHTS: dict[str, tuple[int, int]] = {
    "Arbiter of Ash": (3, 2),  # 43 s, 61 s, 49 s
    "Arbiter of Divinity": (1, 1),  # one 14-minute kill (windows 96 s and 120 s)
    "Olroth, Origin of the Fall": (2, 1),  # 63 s, 42 s
    "Xesht, We That Are One": (3, 1),  # 64 s, ~35 s (half readable), 26 s
    "The King in the Mists": (2, 1),  # 45 s, 27 s (and 29 s of the second form)
    "Zarokh, the Temporal": (3, 1),  # ~32 s, ~45 s usable, 35 s
    "Vessel of Kulemak": (2, 2),  # 45 s, 93 s (MEDIUM-LOW: below MEDIUM; its high end 6.7 is under the Arbiter's 12)
    "The Raven Trickster": (1, 1),
    "The Aberration": (1, 1),  # 48 s
    "Atziri, the Red Queen": (1, 1),  # 45 s
    "Atziri, the Fell Serpent": (2, 1),  # 52 s, 41 s
}


def _counted_enough(name: str) -> bool:
    """Whether a tally may set the worst case's harm headline (above): two fights of 45 s+, or MEDIUM or better."""
    video = RELOCATE_VIDEO.get(name)
    if not video:
        return False
    conf = video[4].upper()
    medium_plus = conf.startswith("HIGH") or (conf.startswith("MEDIUM") and not conf.startswith("MEDIUM-LOW"))
    return RELOCATE_FIGHTS.get(name, (0, 0))[1] >= 2 or medium_plus


def relocations(stats: dict | None = None, reading: str = "headline", gain: bool = True) -> tuple[float, float, str]:
    """(relocations a second, metres each, how) for the scored boss: see RELOCATE_SKILLS / RELOCATE_VIDEO. On the worst
    case a gain reads the lowest real preset and a harm the highest (`gain`)."""
    name = of(stats).name
    if name == WORST:
        pick = min if gain else max
        pool = REAL
        if not gain and reading == "headline":
            pool = tuple(n for n in REAL if _counted_enough(n)) or REAL
        elif gain and reading in ("headline", "video", "any"):
            # (AUDIT40 A40-44, the manager 26 Nov 10:35) an untallied preset's 0 is "no data", not "never moves" (its
            # skills table shows it does): left out of the worst case's minimum, so gain readers on the default boss
            # read the fewest among the TALLIED presets; the 0 stays only when a request names that boss
            pool = tuple(n for n in REAL if n in RELOCATE_VIDEO) or REAL
        sub = "video" if reading == "any" else reading
        src = pick(pool, key=lambda n: relocations({"_boss": n}, sub, gain)[0])
        rate, metres, how = relocations({"_boss": src}, sub, gain)
        which = ("the fewest among the presets counted on video (untallied ones left out: no data isn't no moves)"
                 if gain and pool != REAL else "the fewest" if gain else "the most among tallies from two fights of 45 s+ or MEDIUM+ (the manager "
                 "26 Nov 06:32)" if pool != REAL else "the most")
        return rate, metres, f"the worst case reads {src}'s ({which}): {how}"
    if reading == "any":
        reading = "video"
    if reading == "skills":
        uses, metres, skills = RELOCATE_SKILLS[name]
        return uses / 60, metres, (f"at most {uses:.1f} a minute against {name}, each of its own moves used off cooldown "
                                   f"({skills}), up to {metres:.1f} m each (an UPPER BOUND, LOW; walking not in it)")
    video = RELOCATE_VIDEO.get(name)
    if video:
        per_min, metres, _, source, conf = video
        if not gain:
            per_min = RELOCATE_VIDEO_HIGH.get(name, per_min)
        return per_min / 60, metres, (f"{per_min:.1f} a minute ({'the high' if not gain else 'the low'} end) against {name}, {metres:.1f} m each on average, counted "
                                      f"in fight videos ({source}; {conf}); its own moves off cooldown allow "
                                      f"{RELOCATE_SKILLS[name][0]:.1f} a minute (walks not in that)")
    if reading == "video":
        return 0.0, 0.0, f"no video tally of {name}'s relocations"
    uses, metres, _ = RELOCATE_SKILLS[name]
    return 0.0, 0.0, (f"not counted against {name}: no video tally of where it moves (0, CLAUDE.md); its own moves off "
                      f"cooldown give at most {uses:.1f} a minute, up to {metres:.1f} m each (a row, an upper bound)")

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
        # (MECHC31: by the preset's own biggest hit of the type, boss_hits.hit_sizes from each skill's size; before, by
        # its raw damage multiplier, which no longer sizes a hit: spells don't scale with it)
        source[t] = max(dealt, key=lambda n: boss_hits.hit_sizes(n)[t])
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
# The boss's Armour, Evasion and penetration (MECHC34; RESEARCH28 R28-11 CD-1 / CD-2 / CD-3, config-defaults-audit.md,
# the manager 16 Nov 16:11). PoB's Pinnacle preset fills three boxes the tool never wrote, from PoE1 data:
# enemyLightning / Cold / FirePen = data.misc.pinnacleBossPen 3% (Data.lua 298: every boss hit penetrated 3% of your
# elemental resistances: an OVERCOUNT of elemental damage taken, ~12% more at 75% resistance); enemyArmour = the level-82
# monster Armour x 150% and enemyEvasion = the level-82 monster Evasion x 124.9% (ConfigOptions 2095-2096: Data.lua
# 872-873's PinnacleArmourMean / PinnacleEvasionMean, averages of PoB's 22 PoE1 bosses in Data/Bosses.lua). The game:
# - Armour / Evasion: each preset's MonsterTypes Armour / Evasion "+%" over the level table (RESEARCH10 #55 from poe2db,
#   RESEARCH28 from MonsterTypes; re-read by me from scratch/research13's MonsterVarieties -> MonsterTypes export, whose
#   EnergyShieldFromLife column matches es_from_life above: MEDIUM-HIGH that it's "+%" of the table, PoB's own 100 + mult
#   reading). No rarity mod adds either (checked: game 26650308's Mods MonsterUnique* / MonsterRare* / MonsterMagic* /
#   PinnacleAtlasBoss* / MonsterMapBoss* for armour / evasion / damage-reduction / penetration stats: none). The King's
#   two forms by their share of the fight (first form, Armour +20% / Evasion +40%, 43%; second, Armour +65%, 57%: the
#   fight-share mean of the multiplier, MEDIUM). Level 82's table values (PoB Data/Misc.lua monsterArmourTable[82]
#   5,375, monsterEvasionTable[82] 941, exported from the game's DefaultMonsterStats).
# - Penetration: none (checked by RESEARCH28: the 13 presets' own Mods and the 430 GrantedEffectStatSets rows named for
#   them, for "penetrat" / "overwhelm": none; poe2db's Arbiter of Ash page shows none: MEDIUM; penetration comes from map /
#   waystone mods, which the presets don't model). Written as 0 (countAllowZero boxes: 0 sticks). PoB's 3% is a row.
# The worst case: the highest of each over the real presets, per form (Olroth's +80% Armour, the King's first form's +40%
# Evasion), the user's worst-per-stat rule.
MONSTER_ARMOUR_82, MONSTER_EVASION_82, POB_BOSS_PEN = 5375, 941, 3.0
POB_ARMOUR_PCT, POB_EVASION_PCT = 50.0, 24.9  # PoB's Pinnacle preset's "+%" (150% / 124.9% of the table)
BOSS_DEFENCES: dict[str, tuple[float, float, str]] = {  # name: (Armour +%, Evasion +%, where from)
    "Arbiter of Ash": (20, 0, "DemonClawBoss"),
    "Arbiter of Divinity": (40, 0, "ArbiterofDivinity"),
    "Xesht, We That Are One": (0, 0, "ItThatReturned"),
    "The King in the Mists": (0.43 * 20 + 0.57 * 65, 0.43 * 40,
                              "VoodooKingBoss2MAP +20% / +40% for 43%, VoodooKingBoss3MAP +65% / 0 for 57%"),
    "Zarokh, the Temporal": (0, 0, "SilentSpiresApparitionSTANDALONE"),
    "The Raven Trickster": (15, 0, "TangmazuBoss"),
    "The Bodach": (75, 0, "RitualWendigoBoss"),
    "Olroth, Origin of the Fall": (80, 0, "Olroth"),
    "The Aberration": (50, 0, "CyclopsSpiderBoss"),
    "Vessel of Kulemak": (50, 0, "KulemakBoss"),
    "Atziri, the Red Queen": (33, 0, "AtziriBossPhaseOne"),
    "Atziri, the Fell Serpent": (33, 0, "AtziriBossPhaseTwo"),
}
BOSS_DEFENCES[WORST] = (80, 40, "the highest of each, per form: Olroth's Armour +80%, the King's first form's Evasion +40%")


def defence_vars(name: str | None = None) -> dict[str, float]:
    """PoB config inputs for the boss's Armour, Evasion and elemental penetration (the comment above BOSS_DEFENCES)."""
    armour, evasion, _ = BOSS_DEFENCES.get(name or DEFAULT, BOSS_DEFENCES[WORST])
    return {"enemyArmour": round(MONSTER_ARMOUR_82 * (1 + armour / 100)),
            "enemyEvasion": round(MONSTER_EVASION_82 * (1 + evasion / 100)),
            "enemyLightningPen": 0, "enemyColdPen": 0, "enemyFirePen": 0}


def defence_text(name: str | None = None) -> str:
    """The boss row's Armour / Evasion / penetration clause, with PoB's Pinnacle preset as the "without it" row."""
    armour, evasion, where = BOSS_DEFENCES.get(name or DEFAULT, BOSS_DEFENCES[WORST])
    got = defence_vars(name)
    return (f"Armour {got['enemyArmour']:,} (+{armour:.4g}%) and Evasion {got['enemyEvasion']:,} (+{evasion:.4g}%) over "
            f"the level-82 monster table ({where}; MonsterTypes, MEDIUM-HIGH), no penetration of your elemental "
            f"resistances (none found on any preset, MEDIUM); PoB's Pinnacle preset (PoE1 bosses' averages): Armour "
            f"{round(MONSTER_ARMOUR_82 * (1 + POB_ARMOUR_PCT / 100)):,}, Evasion "
            f"{round(MONSTER_EVASION_82 * (1 + POB_EVASION_PCT / 100)):,} and {POB_BOSS_PEN:g}% penetration of your "
            "Fire / Cold / Lightning resistances (about 12% more elemental damage taken at 75%), not used")


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
    "Armour / Evasion": "Armour +80% (Olroth), Evasion +40% (the King's first form) over the level-82 table; no "
                        "penetration of your resistances (no preset has any)",
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
USE_EVERY[WORST] = max(USE_EVERY[n] for n in REAL)  # (as HIT_EVERY[WORST]: the slowest real preset's)
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
    rows = "; ".join(f"row, not counted: {what}, {r * 60:.1f} a minute" for what, r in kill_rows(name))
    if not b.kills:
        return 0.0, f"no killable adds with a known rate against {name} (research #19 / #66)" + (f"; {rows}" if rows else "")
    rate = sum(k.wave * k.share / k.every for k in b.kills)
    return rate, "; ".join([f"one per {k.every:g} s for {k.share:.0%} of the fight: {k.what} ({k.confidence})"
                            for k in b.kills] + ([rows] if rows else []))


# (MECHD7; RESEARCH41 R41-16, the manager 05:15 21 Nov) kill sources kept OUT of the headline, as rows: (every s, adds,
# share of the fight, what, confidence). The Aberration's Verisium Web: poe2db lists a monster with 1 Life and an attack;
# no source says players can target it or that on-kill effects fire on it (UNVERIFIED): headline 0, this row.
KILL_ROWS = {"The Aberration": ((30.0, 1, 1.0, "one Verisium Web killed every 30 s (GTCyclopsSpiderSpawnVerisium; "
                                               "targetable / on-kill UNVERIFIED)", "LOW"),),
             # (MECHD8) The 25 s EASVoodooKingSummonEffigy is on the second form's row too (VoodooKingBoss3MAP, game
             # 26476662; HIGH for the listing): if that form casts it, its 57% of the fight adds the same waves (the
             # ceiling 1.0 of the headline's 0.43; whether it casts it MEDIUM-LOW, RESEARCH #147 saw killable adds in
             # the first form only).
             "The King in the Mists": ((25.0, 1, 0.57, "the second form's Affliction Totems too (its row lists the 25 s "
                                                       "summon; whether it casts it UNVERIFIED)", "MEDIUM-LOW"),)}


def kill_rows(name: str = DEFAULT) -> list[tuple[str, float]]:
    """[(what, kills a second)] the headline leaves out (KILL_ROWS), for the report's "with it" rows."""
    return [(f"{what} ({conf})", wave * share / every) for every, wave, share, what, conf in KILL_ROWS.get(name, ())]


def add_waves(name: str = DEFAULT) -> list[tuple[float, int, float, str, str]]:
    """(MECHD7) The preset's add waves: [(seconds between waves, adds a wave, share of the fight they come in, what,
    confidence)], [] with none (for readers that need the timing, not only the rate: R38-5's Fear-on-Cull refresh)."""
    return [(k.every, k.wave, k.share, k.what, k.confidence) for k in PRESETS[name].kills]


# Boss events that refill the player's flasks mid-fight (MECHD9; the manager's 26 Nov brief item 3; R63-5). 0.5.0 Monster
# Changes: "You will now refill some Flask charges when Viper Napuatzi's soldiers constrict the size of her arena". No
# preset is Napuatzi (an Act 3 / map boss), so every preset reads none: (checked: the patch-notes archive for
# refill / recharge / regain / grant near "flask" (Napuatzi's line is the only boss event; the rest are items / skills);
# game 26650308's GrantedEffectStatSets and their per-level rows for the 172 non-local flask stats: only player skills and
# supports carry charge gains, no boss skill; R63-5: KeywordPopups / poe2db give kills, Checkpoints and Wells only). Her
# entry waits on a preset for her: {boss name: (refills in a fight, share of each flask's maximum charges each, how)}.
# What video gave for her (MECHD9's helper mv3, scratch/mechd9/video_mv3.md; 2 PC fights, 0.5): one constriction per
# completed arena-wide attack, about one per 45-75 s before 5% Life (LOW); charges per refill NOT seen (the flasks
# were full both times: UNVERIFIED, no table holds it); charms UNVERIFIED (the note names Flask charges only).
FLASK_REFILLS: dict[str, tuple[int, float, str]] = {}


# How long the boss stays in something placed under it (MECHD9; the manager 26 Nov 04:05, the "boss stands still"
# survey): an area of radius R m placed on the boss's centre, lasting T s (or recast on the boss every T s), against the
# preset's relocations (rate L a second, mean M m each: relocations()). A move takes the boss out when it goes further
# than R + r (r its hit radius: areas hit edge to edge). Assumed (LOW, labelled): move lengths exponential with mean M
# (the videos give means and ranges, not shapes; a row: every move exactly M); a boss that left doesn't come back
# (the lower reading for the player). Then out-moves come at k = L x exp(-(R + r) / M), and the boss is still inside
# after t s with chance exp(-k t); over a T s window it is inside (1 - exp(-k T)) / (k T) of the time. Movement here
# is a HARM to the player's damage, so the worst case reads the preset that moves most (AUDIT29 A29-1).
def stay_share(stats: dict | None, radius: float, seconds: float, reading: str = "headline",
               moves: str = "exponential") -> tuple[float, str]:
    """(the share of a `seconds` window the boss stays inside an area of `radius` m placed on it, how); see above.
    `moves` "exponential" (headline) or "fixed" (every move the mean length: a row)."""
    rate, metres, how = relocations(stats, reading, gain=False)
    r = hit_radius(stats)[0]
    if rate <= 0 or metres <= 0 or seconds <= 0:
        return 1.0, f"the boss taken to stay in it ({how})"
    reach = max(radius, 0.0) + r
    out = math.exp(-reach / metres) if moves == "exponential" else (1.0 if metres > reach else 0.0)
    k = rate * out
    share = 1.0 if k <= 0 else (1 - math.exp(-k * seconds)) / (k * seconds)
    return share, (f"{share:.0%} of {seconds:g} s inside a {radius:g} m area placed on it: {k * 60:.1f} moves a minute "
                   f"take it further than {reach:.1f} m (its {rate * 60:.1f} a minute, {metres:g} m each, "
                   f"{'lengths exponential' if moves == 'exponential' else 'every move that long'}, LOW; {how})")


def still_there(stats: dict | None, radius: float, delay: float, reading: str = "headline",
                moves: str = "exponential") -> tuple[float, str]:
    """(the chance the boss is still inside an area of `radius` m placed on it `delay` s later, how): a delayed hit
    (a spike shattered later, a mark that erupts) lands with this chance. See stay_share."""
    rate, metres, how = relocations(stats, reading, gain=False)
    r = hit_radius(stats)[0]
    if rate <= 0 or metres <= 0 or delay <= 0:
        return 1.0, f"the boss taken to stay ({how})"
    reach = max(radius, 0.0) + r
    out = math.exp(-reach / metres) if moves == "exponential" else (1.0 if metres > reach else 0.0)
    p = math.exp(-rate * out * delay)
    return p, (f"{p:.0%} still inside a {radius:g} m area {delay:g} s after it was placed on it ({rate * 60:.1f} moves "
               f"a minute, {metres:g} m each, LOW; {how})")


# Time the boss is OFF THE FIELD (untargetable / gone) in a fight (MECHD9; the manager 26 Nov 10:35: data only, read by
# the fight-length readers: ramp-ups, charges, MECH-C's flask stock over the fight). DPS is a rate, so this lengthens the
# fight rather than lowering the DPS. From the video helpers' tallies (scratch/mechd9/video_mv3.md, mv5.md, mv6.md) and
# RESEARCH25 #147; {name: ((seconds or None, what), ...), share seen in a timed fight or None, confidence}. Presets not
# listed: no data (no off-field phase seen or none looked for), never a guessed 0-share.
ABSENT: dict[str, tuple[tuple[tuple[float | None, str], ...], float | None, str]] = {
    "Zarokh, the Temporal": (((13.0, "the Trial of Time / Sands of Time, 12-14 s (both mv3 fights and mv6's W1)"),
                              (35.0, "two ~17-18 s 'There can be no escape!' time-bubble phases (mv6's W1 only; "
                                     "whether short kills skip them UNVERIFIED)")),
                             0.52, "MEDIUM for the Trial, LOW for the bubbles (one 90 s fight: ~47 s off the field)"),
    "The King in the Mists": (((None, "the maze between its two forms: the boss immune (RESEARCH25 #147); its length "
                                      "not found (UNVERIFIED)"),), None, "MEDIUM that it exists; no length"),
    "Atziri, the Fell Serpent": (((10.0, "her 'My ascension has begun!' phase at ~3-5% Life: the body leaves the floor "
                                         "~10 s under a shield bar (mv5, one fight)"),), None, "LOW (one fight)"),
    "The Bodach": (((45.0, "a scripted phase with the boss bar up and the Bodach off screen, 45 s+ (mv3, one 720p "
                           "video)"),), None, "MEDIUM-LOW (one video; the boss is hard to see in its arena)"),
}


def absent(stats: dict | None = None) -> tuple[float, float | None, str]:
    """(known off-field seconds a fight, the share seen in a timed fight or None, how); (0, None, "no data") for a
    preset without an entry (ABSENT). Seconds of an event with no known length count 0, said."""
    name = of(stats).name
    entry = ABSENT.get(name)
    if not entry:
        return 0.0, None, f"no data on {name} leaving the field (none seen or none looked for; not a measured 0)"
    events, share, conf = entry
    seconds = sum(t for t, _ in events if t)
    how = "; ".join(f"{t:g} s: {w}" if t else f"length unknown: {w}" for t, w in events)
    return seconds, share, f"off the field ~{seconds:g} s a fight against {name} ({how}; {conf})"


def still_share(stats: dict | None = None) -> tuple[float | None, str]:
    """(MECHD9) The share of the fight the boss spends in standing spells of 3.5 s or more (Ice Shards' 1.5 s arming plus
    its 2 s ramp), from RELOCATE_VIDEO; (None, why) where the videos didn't read it. The worst case: None (no reading
    is worst for every use)."""
    name = of(stats).name
    video = RELOCATE_VIDEO.get(name)
    if not video or video[2] is None:
        return None, f"not read for {name} (no video tally of its standing spells)"
    return video[2], f"{video[2]:.0%} of the fight in stands of 3.5 s or more against {name} ({video[4]})"


def flask_refills(stats: dict | None = None) -> tuple[int, float, str]:
    """(MECHD9; R63-5) (refills in the fight, the share of each flask's maximum charges each gives, how) for boss events
    that refill flasks mid-fight, for MECH-C's flask stock; (0, 0, why) against a boss with none (FLASK_REFILLS)."""
    name = of(stats).name
    if name in FLASK_REFILLS:
        return FLASK_REFILLS[name]
    return 0, 0.0, (f"no mid-fight flask refill against {name} (FLASK_REFILLS: the patch notes name Viper Napuatzi's "
                    "arena constrictions only, not a preset; a Checkpoint before the fight is the stock's start)")


def adds_near(name: str = DEFAULT, metres: float = 1.8) -> tuple[float, float, str]:
    """(MECHD9; R61-15: Herald of Plague's spread reaches 1.5-1.8 m from the dying add) (the add kills a second that die
    within `metres` of the boss's centre, the share of all add kills that is, how). Each Kills row's `near` points are
    read at the largest point within `metres` (no interpolation upward: the lower reading); a row with no placement
    data counts 0, said (CLAUDE.md: no source, 0)."""
    b = PRESETS[name]
    total, near, parts = 0.0, 0.0, []
    for k in b.kills:
        rate = k.wave * k.share / k.every
        total += rate
        inside = [s for m, s in k.near if m <= metres]
        share = max(inside) if inside else 0.0
        near += rate * share
        what = k.what.split(" (")[0]
        parts.append(f"{what}: {share:.0%} within {metres:g} m ({k.near_how})" if k.near else
                     f"{what}: not counted (no data on where they die relative to the boss: 0)")
    if total <= 0:
        return 0.0, 0.0, f"no killable adds with a known rate against {name}: 0"
    return near, near / total, "; ".join(parts)


KILLS_STAT, CULLS_STAT = "AddKillsPerSecond", "AddCullsPerSecond"
ADD_LIFE_STAT = "AddLife"  # (MECHC46: add_life's headline, in kill_stats)


def add_life(name: str = DEFAULT) -> tuple[float, str, list[tuple[str, float]]]:
    """(MECHC46; R36-6) (one killed add's Life, the kill-rate-weighted mean over the preset's Kills rows; how; other
    readings [(label, Life)]) for "% of the slain enemy's Life" sources (GEM's Voltaic Fulmination). (0, why, []) with no
    killable adds or no known Life."""
    b = PRESETS[name]
    rows = [k for k in b.kills if k.life > 0]
    if not rows:
        return 0.0, f"no killable adds with a known Life against {name}", []
    weight = sum(k.wave * k.share / k.every for k in rows)
    life = sum(k.life * k.wave * k.share / k.every for k in rows) / weight if weight > 0 else 0.0
    how = "; ".join(f"{k.what.split(' (')[0]}: {k.life:,.0f} Life" for k in rows) + \
        " (level 82, Normal; RESEARCH36 R36-6)"
    others = [(label, life_) for k in rows for label, life_ in k.life_rows]
    return life, how, others


def kill_stats(name: str = DEFAULT) -> dict:
    """The boss-side numbers models read from the stats: the preset's name, its add kills and Culls per second. Culls
    are counted as the kills (whether the build can Cull is the reader's check). An add that one hit kills outright
    isn't Culled in game: not modelled, a possible overcount for Cull-triggered lines (LOW: the King's Affliction Totems
    have x0.04 Life)."""
    rate, _ = kills_per_second(name)
    return {"_boss": name, KILLS_STAT: rate, CULLS_STAT: rate, ADD_LIFE_STAT: add_life(name)[0]}


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
# Re-checked MECHD7 (21 Nov) on game 26650308: Stats holds three cull-immunity stats (can_be_culled,
# immune_to_culling_strike, cannot_be_culled); the one Mods row carrying any is still MonsterCannotBeCulled, and it is on
# none of the 13 preset varieties' Mods / Mods2 (research13's MonsterVarieties export, 26476662; the 26650308 dump has no
# monster tables, so a change there is UNVERIFIED); no GrantedEffect stat set (player or monster, every level row) holds
# one, so no boss skill or phase buff grants it in the exported tables. Runic Ward also blocks Culling (KeywordPopups
# Ward: "While a monster has Runic Ward, it cannot be Culled"): no preset's variety mods or MonsterTypes carry a Ward
# stat (checked: the same rows for "ward"); the monster Ward mods that exist are Expedition runes
# (ExpeditionMonsterModRuneWard / ...Power: maximum_life_%_to_gain_as_total_ward): whether one can reach Olroth's
# arena is UNVERIFIED (a research question, MECHD7; not counted). Second sources: poe2db's Arbiter of Ash page lists no
# such mod (it lists no rarity mod either, so its silence is weak, PLANS6); patch notes: no hit for a boss that can't be
# Culled (grep_notes "cull"; no hit isn't proof). Scripted deaths and phase changes (a form that transforms, the
# Arbiter's heal at a phase change) are UNVERIFIED for Culling: `cullable` stays True (never zeroed on an unverified
# claim), the phases are conditions.life_bars' (MECH-A's) share of the threshold.
# Forms and lives (the King's two forms, Kulemak's revive, Atziri's two phases) each Cull at 5% of their own Life,
# the same share of the whole fight, so they don't change it (UNVERIFIED that a form that transforms can be Culled).
CULL_UNIQUE = 5.0  # % of Life (KeywordPopups CullingStrike; PoB's CullingStrikeUniqueThreshold, Data/Misc.lua 108)
RARITY_LIFE, RARITY_ES = 29.0, 5.0  # MonsterUnique2: +2,800% / +400% final


def life_share_of_pool(name: str = DEFAULT) -> float:
    """The boss's Life as a share of Life + Energy Shield (the reading above; 1.0 with no ES)."""
    f = PRESETS[name].es_from_life / 100
    life, es = RARITY_LIFE * (1 - f), RARITY_ES * f
    return life / (life + es)


# (MECHD7, the manager's 21 Nov brief item 3) Each preset's Life and Energy Shield, both readings, for readers that need
# the amounts and not only the share (MECH97's Full Life window, conditions.boss_pool's fight length). Checked against the
# game files: research13's MonsterVarieties -> MonsterTypes export (game 26476662; the 26650308 dump has no monster tables,
# so a later change there is UNVERIFIED): every preset's LifeMultiplier x 32,956 (monsterLifeTable[82]) x 29 gives its
# `life` to the unit (the King's two forms 375% each, x1.75 bars), and its EnergyShieldFromLife is es_from_life (Ash,
# the Bodach, Olroth, the Aberration 0; Xesht 5; Zarokh 7; Divinity, the King, the Raven, Kulemak 10; both Atziris 33).
# Second source: poe2db's monster blocks (RESEARCH10 #55, 12 Nov: the same Life %s and ES From Life, Ash / the Bodach /
# Olroth / the Aberration "none listed"; RESEARCH9 for Divinity, Xesht and the Raven). Both readings give the SAME Energy
# Shield, f% of the base Life x5 (the Unique rarity's +400%); they differ in Life only:
# - "headline" (the conversion reading, MEDIUM-LOW, as life_share_of_pool): Life = 29 x (1 - f) of the base Life;
# - "poe2db" (poe2db's own figure adds the ES and leaves Life whole): Life = 29 x the base Life = the preset's `life`.
# So `life` (RESEARCH9's figure) is the poe2db reading's Life; under the headline the Red Queen has 4.75M Life + 0.40M ES
# (5.15M), under poe2db's 7.09M + 0.40M (7.49M). An in-game Life / ES readout would settle it (MECHD5's question).
# (MECHD7; RESEARCH42 R42-1, research/atziri-es-bar-acolytes-r42.md, LOW-MEDIUM) Atziri's bar in a no-commentary kill
# video: each phase opens with a thin ES layer over full Life, stripped before Life moves (sure); from the Life-damage
# counter ES is about 13-14% of Life in both phases (8-17%, assuming equal damage on ES and Life): nearer the headline's
# 8.5% than poe2db's 5.7%. A third reading, "video": ES 13.5% of the headline's Life (Atziri only). The blue refill at each
# phase's end (~9% / ~4% Life) comes while she is immune: nothing to count.
ES_FROM_VIDEO = {"Atziri, the Red Queen": 0.135, "Atziri, the Fell Serpent": 0.135}
POOL_READINGS = ("headline", "poe2db", "video")


def pool(name: str = DEFAULT, reading: str = "headline") -> tuple[float, float, str]:
    """(the boss's Life, its Energy Shield, how) under a reading (the comment above), one Life bar."""
    b = PRESETS[name]
    f = b.es_from_life / 100
    base = b.life / RARITY_LIFE
    es = RARITY_ES * f * base
    life = b.life * (1 - f) if reading in ("headline", "video") else float(b.life)
    if reading == "video" and name in ES_FROM_VIDEO:
        es = ES_FROM_VIDEO[name] * life
    how = (f"{name}: Life {life:,.0f} + Energy Shield {es:,.0f} (EnergyShieldFromLife {b.es_from_life}%: "
           + ("converted from Life, the headline, MEDIUM-LOW" if reading == "headline" else
              f"ES {ES_FROM_VIDEO[name]:.1%} of Life from a kill video (R42-1, LOW-MEDIUM)"
              if reading == "video" and name in ES_FROM_VIDEO else
              "no video reading: the headline" if reading == "video" else
              "added on top of Life, poe2db's reading") + ")")
    return life, es, how


# (MECHD7; RESEARCH40 R40-8, the manager 04:20 21 Nov) Energy Shield and Culling. One player report (forum 3868171, 18 Oct
# 2025, no reply, Yama the White, a non-preset boss that refilled its ES): the cull didn't kill while its ES was up, and
# did once the ES was "down in the 5% range". The LOW assumption, for every preset with ES: a cull needs the boss's ES
# down to the cull share. Damage takes a monster's ES before its Life, and no preset regains ES in the fight (R39-9:
# recharge needs 4 s with no ES loss; no preset has an ES recharge / regeneration mod or skill), so its Life only reaches
# the 5% line after its ES is gone: the assumption gives cull_threshold's headline (5% x Life / (Life + ES) of the
# pool), and the "cull ignores ES" row (Life at 5% culls whatever the ES) gives the same number in an uninterrupted
# fight. They part only if the boss regains ES while its Life is under 5% (Yama's case: no preset, MEDIUM-HIGH via R39-9;
# a > 4 s gap in your hits is R39-9's LOW row); then the headline culls nothing until the ES is down again. PoB's own
# reading (5% of the whole pool, Life + ES) is the overcount row. No preset is marked uncullable (R40-8: the 53 preset
# rows, their mods and 101 .ot files hold no cull stat, while the same files set immunities: MEDIUM).
CULL_ES_ASSUMPTION = ("a cull needs the boss's Energy Shield down to the cull share (LOW: one player report, forum "
                      "3868171; RESEARCH40 R40-8)")


def cull_rows(name: str = DEFAULT) -> list[tuple[str, float]]:
    """[(reading, the Unique Culling Strike threshold as % of the boss's pool)]: the headline first (cull_threshold),
    then the "cull ignores ES" row and PoB's whole-pool row (the comment above); one row for a boss with no ES."""
    head = cull_threshold(name)
    if not can_be_culled(name)[0]:
        return [("can't be Culled", 0.0)]
    if PRESETS[name].es_from_life <= 0:
        return [("no Energy Shield: 5% of its Life", head)]
    return [(f"headline: {CULL_ES_ASSUMPTION}", head),
            ("cull ignores ES (Life at 5% culls whatever the ES): the same in an uninterrupted fight, since its ES is "
             "taken first and never regained (R39-9)", head),
            ("PoB's reading, 5% of Life + ES (an overcount)", CULL_UNIQUE)]


def can_be_culled(name: str = DEFAULT) -> tuple[bool, str]:
    """(whether Culling Strike can kill the preset, how): the gate every culling model goes through (cull_threshold)."""
    if PRESETS[name].cullable:
        return True, (f"{name} can be Culled (no cannot-be-Culled mod or Runic Ward on its monster data, checked "
                      "MECHD7; phases and scripted deaths UNVERIFIED)")
    return False, f"{name} can't be Culled: every Culling gain counts 0"


def cull_threshold(name: str = DEFAULT) -> float:
    """PoB's Unique Culling Strike threshold to use against this boss: 5% x Life / (Life + ES), so PoB's
    CullMultiplier, 100 / (100 - it), is the gain over the whole pool (MEDIUM-LOW, above); 0 for a preset that can't be
    Culled (can_be_culled), which leaves PoB's multiplier at x1 and every reader's Cull share at 0."""
    if not can_be_culled(name)[0]:
        return 0.0
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
    # (MECHC31: every boss's, the worst case's too, are boss_hits.config_vars: each type's average hit, the worst case
    # the highest average of any real preset; worst_hit_sizes stays the survival check's biggest hits)
    hits = boss_hits.config_vars(name)
    # (MECHC45, the manager's 08:05 from COV46 / COV47's placeholder audit) The boss-hit boxes left to PoB's placeholders
    # on purpose (BOSS_HIT_PLACEHOLDERS; probe POE2T4829, a skeleton against the default boss):
    # - enemyCritChance: PoB 5% (ConfigOptions 1967). Game: a monster attack's stat set has AttackCritChance 0, so it
    #   takes the monster's own 5% / +30% (RESEARCH2 boss-crit.md: poe2db's Arbiter of Ash monster block, MEDIUM-HIGH);
    #   spells carry their own SpellCritChance, 5-7% (some 0). Over each preset's non-nuke skills (builder/data/
    #   boss_skill_hits.json, game 26650308 GrantedEffectStatSetsPerLevel) the mean is 5.0-5.57% (a spell's 0 read as the
    #   monster's 5%) or 3.16-5.46% (read as 0, UNVERIFIED which). Kept at 5%: at the highest mean (the Raven, 5.57%)
    #   TotalEHP falls 0.17% (951.9 -> 950.3); no use rates weight the skills (LOW for the mix).
    # - enemyCritDamage: PoB's data.monsterConstants base_critical_hit_damage_bonus 30 = the monster's +30% above.
    # - enemyPhysicalOverwhelm: no preset skill carries an Overwhelm or Physical-Damage-Reduction-ignoring stat (checked:
    #   every stat id in each preset skill's stat set, implicit / constant / per level, game 26650308); PoB reads 0
    #   (its placeholder is looked up under "enemyPhysicalenemyOverwhelm", CalcDefence 2113, so an unset box is 0).
    # - enemyDamageRollRange: read only by PoB's own boss-skill preset (ConfigOptions 2185), which stays "None": no
    #   reader (the probe: 0 and 100 move nothing); the boss's hits per type are ours (boss_hits.config_vars).
    return {"enemyFireResist": b.fire, "enemyColdResist": b.cold, "enemyLightningResist": b.lightning,
            "enemyChaosResist": b.chaos, "enemySpeed": round(defence_hit_every(name) * 1000),
            "cullingStrikeUniqueThreshold": round(cull_threshold(name), 4), **hits,
            **defence_vars(name)}  # (MECHC34, R28-11 CD-1..3: the boss's own Armour / Evasion, no penetration)


# (MECHC45) PoB config boxes for the boss's hit that config_vars leaves to PoB's placeholders (the note in config_vars)
BOSS_HIT_PLACEHOLDERS = {"enemyCritChance": 5, "enemyCritDamage": 30, "enemyPhysicalOverwhelm": 0,
                         "enemyDamageRollRange": None}


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
                     f"takes {cull_threshold(name):g}% of the pool, not 5%"
                     + (f" ({CULL_ES_ASSUMPTION}; if a cull ignored ES the same {cull_threshold(name):g}%, as its ES "
                        "is gone first and never regained)" if b.es_from_life > 0 else "") + f"; hits every {defence_hit_every(name):g} s for "
                     f"defences, every {HIT_EVERY[name]:g} s for \"when hit\" benefits (LOW); "
                     f"add kills {kills * 60:.2f}/min ({how}); " + defence_text(name) + (f"; {b.note}" if b.note else ""),
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


# The boss's hit radius (MECHB35, MECH-B's field; RESEARCH28 R28-1, scratch/manager/research/boss-hit-radius.md; the
# manager 16 Nov 00:55). MonsterVarieties.ObjectSize read as the monster's hit / collision radius in distance units (10
# units = 1 m): the values HIGH (checked: research13's MonsterVarieties export of 26476662, every preset's row, 16 Nov),
# the unit reading MEDIUM (poedb's distance page: "2 units Radius (hitboxes) of Humanoids", humans' ObjectSize 2-3; a
# 2014 forum post "Base character radius is 2 units"). ModelSizeMultiplier (poe2db's "Model Size") is cosmetic by the
# only staff word (Mark_GGG, forum 969990, 2014: "it doesn't change the collision radius"), so ObjectSize x Model Size
# is only a labelled upper reading (HIT_RADIUS_UPPER). Hits count edge to edge (an area or projectile hits when it
# overlaps the circle; MEDIUM, forum 3757630). Read by MECH-B's Salvo, Spearfield / Conductive Runes (hazards per use,
# Escalating Mayhem) and Primal Strikes' stampede. The King: both forms ObjectSize 4 (model 125% / 120%: the larger
# for the upper row). The worst case takes the SMALLEST (fewest hazards / Salvo projectiles on the boss).
# (MECHD9; RESEARCH64 R64-10, the manager 26 Nov 03:06) re-checked on the INSTALLED game: every preset's ObjectSize / 10
# matches these values for all 13 rows and no object file overrides it (HIGH); the unit (tenths of a metre) stays MEDIUM.
HIT_RADIUS: dict[str, float] = {  # metres: ObjectSize / 10
    "Arbiter of Ash": 0.4,  # DemonClawBoss
    "Arbiter of Divinity": 0.4,  # ArbiterofDivinity_
    "Xesht, We That Are One": 0.3,  # ItThatReturned
    "The King in the Mists": 0.4,  # VoodooKingBoss2 / VoodooKingBoss3
    "Zarokh, the Temporal": 0.4,  # SilentSpiresApparition
    "The Raven Trickster": 0.3,  # DeliriumTangmazu/TangmazuBoss
    "The Bodach": 0.5,  # RitualWendigoBoss
    "Olroth, Origin of the Fall": 0.4,  # LeagueExpeditionNew/Olroth/Olroth
    "The Aberration": 0.5,  # CyclopsSpiderBoss
    "Vessel of Kulemak": 0.6,  # LichBoss/KulemakBoss
    "Atziri, the Red Queen": 0.5,  # AtziriBossPhaseOne
    "Atziri, the Fell Serpent": 1.0,  # AtziriBossPhaseTwo______ (its body; she moves on video: RELOCATE_VIDEO)
}
MODEL_SIZE: dict[str, float] = {  # ModelSizeMultiplier / 100, the same rows
    "Arbiter of Ash": 1.30, "Arbiter of Divinity": 1.30, "Xesht, We That Are One": 1.00, "The King in the Mists": 1.25,
    "Zarokh, the Temporal": 1.40, "The Raven Trickster": 1.50, "The Bodach": 2.20, "Olroth, Origin of the Fall": 1.50,
    "The Aberration": 1.53, "Vessel of Kulemak": 1.00, "Atziri, the Red Queen": 0.88, "Atziri, the Fell Serpent": 1.00,
}
HIT_RADIUS_UPPER: dict[str, float] = {n: round(HIT_RADIUS[n] * MODEL_SIZE[n], 3) for n in REAL}
HIT_RADIUS[WORST] = min(HIT_RADIUS[n] for n in REAL)
HIT_RADIUS_UPPER[WORST] = min(HIT_RADIUS_UPPER[n] for n in REAL)  # (Xesht 0.3 m either way: the minimum doesn't move)
LARGEST_RADIUS = max(REAL, key=lambda n: HIT_RADIUS[n])  # the Fell Serpent, 1.0 m: the notes' big-boss row


def hit_radius(stats: dict | None = None, reading: str = "headline") -> tuple[float, str]:
    """(the scored boss's hit radius in metres, how): reading "headline" (ObjectSize / 10) or "upper" (x Model Size)."""
    name = of(stats).name
    if reading == "upper":
        r = HIT_RADIUS_UPPER.get(name, HIT_RADIUS_UPPER[WORST])
        return r, f"{name}'s {r:g} m (ObjectSize x Model Size, the upper reading: Model Size is cosmetic by GGG's word)"
    r = HIT_RADIUS.get(name, HIT_RADIUS[WORST])
    return r, f"{name}'s {r:g} m hit radius (MonsterVarieties ObjectSize / 10, MEDIUM: the unit read from poedb)"
