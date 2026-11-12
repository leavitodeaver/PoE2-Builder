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

Every value: poe2db's current monster rows (read 12 Nov 2026, game 0.5.5), via RESEARCH9; resistances MEDIUM-HIGH, Life
x29 rarity at level 82 (formula HIGH, level MEDIUM). A game update (26476662 -> 26579160) was waiting on 12 Nov:
recheck after it. Which boss(es) the objective targets is the user's open question; the default stays the Arbiter of
Ash (the user's reference boss for Heavy Stun, 9 Nov).

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
    # MonsterTypes.EnergyShieldFromLife, % (AUDIT18 A18-4; the values match the table, AUDIT22 A22-5). What it means in
    # the fight is UNVERIFIED: how it combines with the Unique rarity's +2,800% Life / +400% ES (a share of base Life:
    # ES about 1.7% of final Life; or of final Life: about 50% at 33%) is open. In notes only: nothing reads it yet.
    es_from_life: int = 0
    immune: frozenset = frozenset()  # ailments the boss can't have (AUDIT9 A9-7: Zarokh's own monster mod)
    kills: tuple = ()  # Kills rows (empty: no killable adds with a known rate; see the comment under PRESETS)
    inflicts: tuple = ()  # ailments its fight puts on the player (RESEARCH13 #60, A9-1, A10-1), for MECH-C's triggers
    # Slow effect on the boss: Unique monsters take 50% less (Mods MonsterUnique11 slow potency -50; poe2db's Rarity page;
    # RESEARCH13 boss-resistances-damage-game-tables.md, MEDIUM-HIGH that it applies to pinnacles). PoB applies a Slow on
    # the enemy in full (CalcPerform.lua calcs.actionSpeedMod, no SlowPotency for bosses: research-checked), which only
    # lengthens the boss's skill time (CalcDefence.lua 3428, survival): models reading a Slow's magnitude multiply by this.
    slow_taken: float = 0.5


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
DEFAULT = "Arbiter of Ash"
SOURCE = "poe2db's monster rows, read 12 Nov 2026 (game 0.5.5), RESEARCH9; MEDIUM-HIGH"


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


def config_vars(name: str = DEFAULT) -> dict[str, int]:
    """PoB config inputs that set the boss's resistances (bridge resetConfig's `boss` argument)."""
    b = PRESETS[name]
    return {"enemyFireResist": b.fire, "enemyColdResist": b.cold, "enemyLightningResist": b.lightning,
            "enemyChaosResist": b.chaos}


def note(name: str = DEFAULT) -> dict:
    """The report's labelled row for the boss the numbers are scored against."""
    b = PRESETS[name]
    kills, how = kills_per_second(name)
    category = (b.category if isinstance(b.category, str) else
                ", ".join(f"{c} {s:.0%}" for c, s in b.category.items()) if b.category else "unknown (UNVERIFIED)")
    return {"label": f"Boss: {b.name}", "value": f"Fire {b.fire}% / Cold {b.cold}% / Lightning {b.lightning}% / "
                                                  f"Chaos {b.chaos}%",
            "basis": f"the boss's own resistances ({SOURCE}), not PoB's default Pinnacle 50/50/50; Penetration stops at "
                     "0% (PoB and the game agree). Other pinnacles differ: elemental damage moves up to x2.3 between "
                     f"them. Life {b.life:,} at level 82; Monster Category {category}; stun threshold {b.poise}% "
                     f"(PoB's buildup x{threshold_scale({'_boss': name}):.2f}); Heavy Stun {b.heavy_stun_s:g} s; "
                     f"add kills {kills * 60:.2f}/min ({how})" + (f"; {b.note}" if b.note else ""),
            "confidence": "MEDIUM-HIGH (recheck after the 12 Nov game update)"}
