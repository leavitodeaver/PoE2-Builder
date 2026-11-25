"""Life and Mana recovery from the sources PoB doesn't turn into a rate (MECHC12; the user's rule, 12 Nov 2026: "regen is
not the only source of recovery, not by a longshot ... that applies for health and mana too").

PoB gives regeneration, leech, recoup and on-hit gains as per-second outputs (LifeRegenRecovery, LifeLeechRate, LifeOnHitRate,
{Type}LifeRecoup ...), and the search adds flasks (flasks.RECOVERY_FIELDS). Every source below is per event in PoB or not
parsed at all, so nothing read it before (MECHC11's inventory, scratch/mechc11/recovery_inventory.md, sections 5-9). Each is
turned into a rate here from the build's own events (CLAUDE.md: no flat rates where the build decides the rate; no source,
0 with a note):

- On Kill (LifeOnKillRecovery / ManaOnKillRecovery): PoB's LifeOnKill / ManaOnKill (the main skill's "Life per enemy killed",
  "Recover N% of maximum Life on Kill", Cannibalism ...; CalcOffence 4547-4556, 0 for traps / mines / totems as PoB has it)
  x the enemies the build kills a second (models.kill_rate: the boss preset's adds; 0 against a lone boss, whose death ends
  the fight). Your own minions Killed (the ruling on A17-4 counts them as Kills, LOW) are not counted here: PoB sums every
  on-Kill line into one number, and most say "enemy" (a possible undercount). Also "Recover N% of your maximum Life / Mana
  when an Enemy dies in your Presence" (two uniques; PoB parses nothing, ModCache gives nil): every add's death taken as in
  your Presence (LOW).
- Remnants (LifeRemnantRecovery / ManaRemnantRecovery): Life Remnants ("Spawn a Remnant on Hitting a target, no more than
  once every 3000 ms"; N% chance on kill) and Mana Remnants (on Critically Hitting a target with an Elemental Ailment, once
  every 2000 ms; N% chance on killing an enemy with an Elemental Ailment), each Remnant at PoB's per-Remnant amount
  (base x RemnantEffect, the bridge's activeSkills remnantGain); "Recover N% of Maximum Life / Mana when you collect a
  Remnant" (passives; PoB parses nothing) on every Remnant the build collects (MECHC15, AUDIT26 A26-3: its Infusion,
  Ward and other Remnants too, assumptions.remnant_supply), and the build's additional-Remnant / reappear / double-pickup
  lines on all of them (COV17, assumptions.remnant_multiplier). Every Remnant taken as collected (the play guide says to walk
  onto each; MEDIUM-LOW: where they appear is UNVERIFIED, research/no-effect-remnant-range.md).
- Charms (CharmLifeRecoveryRate / CharmManaRecoveryRate): a charm's own "Recover N Life / Mana when Used" (PoB parses
  nothing) at that charm's uses a second (flasks.uses_per_second: its trigger and the charges the build generates), and
  "Recover N% of maximum Mana when a Charm is used" at every charm's uses.
- Events (LifeOnEventRecovery / ManaOnEventRecovery): "Recover N% of maximum Life and Mana when you use a Warcry" (the
  build's Warcries, conditions.event_rates), "Recover N% of maximum Life when you Heavy Stun a Rare or Unique Enemy" (the
  boss's Heavy Stuns, models.heavy_stun_rate), "Recover N% of maximum Life / N Life when you Ignite an Enemy" and "Recover
  N% of maximum Mana when you Shock an Enemy" (the main skill's hits x its chance to inflict it). PoB parses none of them.
  Lines no rate reads yet are named in the note (EVENT_UNREAD): an undercount until they are.
- Spent and consumed (LifeOnConsumeRecovery / ManaOnConsumeRecovery; MECHC13): "Recover N% of maximum Life for each
  Endurance Charge consumed" and "Recover N% of maximum Mana when you consume a Power Charge" at the charges the build's
  consumers spend a second (Conditions' charge model: the lower of the charges made and the consumers' uses, one a use;
  build.charge_flow); "Recover N% of maximum Life per Glory consumed" at the Glory the build's Glory skills spend a second
  (models.glory_spent: their uses x their Glory cost); "Recover N% of Maximum Life / Mana when you expend at least 10
  Combo" at the uses of the build's Combo skills that build 10 or more (usage.combo_rate's rotation: the Combo built a use
  is the skill's requirement); "Recover N% of maximum Life when you Consume a corpse" and "Regenerate N% of maximum Mana
  over 2 seconds when you Consume a corpse" at the corpses the build's corpse skills use a second (their uses, at most the
  boss preset's corpses: models.corpse_supply; none on a lone boss); "Recover N% of maximum Life when you create an
  Offering" at the build's Offering skills' uses (on cooldown, duration, or the search's chosen rate); "Recover N% of
  maximum Life when one of your Minions is Revived" (MECHC18, LifeOnEventRecovery) at the build's Persistent, Reviving
  minions Killed a second (models.minion_deaths: Sacrifice, Minion Pact, Explosive Demise; the boss killing them isn't
  counted yet, a lower reading); "Consume all Rage when Shapeshifting to Human form to recover 1% of maximum life per
  Rage Consumed" (Invigorating Hate, MECHC21) at the Rage the build's shifts back to Human form consume a second
  (conditions.human_shifts / rage_held; build.charge_flow["Rage"]).

Amounts are taken as written: recovery-rate modifiers aren't applied to them (MEDIUM). "Cannot Recover Life other than from
Leech" (Vaal Pact) zeroes the Life parts and "Mana Recovery other than Regeneration cannot Recover Mana" (Indigon) the Mana
parts, all but the corpse line's Mana, which is Regeneration (MECHC16, AUDIT28 A28-4; UNVERIFIED in game, LOW); for Indigon
the bridge also zeroes PoB's own Mana leech, on-hit, on-kill, on-block and recoup outputs and the Mana flask's recovery
(MECHC14: PoB parses the line to no mod, so they had still counted: an OVERCOUNT).
"""

from __future__ import annotations

import functools
import math
import re
from dataclasses import dataclass, field

# The fields, per second (for SEARCH: objective.SUSTAIN_POOLS / RECOVERY_POOLS read them from the build's stats)
FIELDS = {
    "Life": ("LifeOnKillRecovery", "LifeRemnantRecovery", "CharmLifeRecoveryRate", "LifeOnEventRecovery",
             "LifeOnConsumeRecovery", "LifePreventedRecoup", "LifeFlameLeechRecovery",
             "LifeFlaskDrain",  # (MECHC26: Uhtred's Chalice's Life loss, NEGATIVE: flasks.flask_life_drain)
             "LifeSacrificed",  # (MECHC41: Citaqualotl's Thesis' Life sacrificed per roll, NEGATIVE: life_sacrifice_income)
             "LifeRiftRecovery"),  # (MECHC29: Temporal Rift's reset to 4 s ago, rift_income)
    "Mana": ("ManaOnKillRecovery", "ManaRemnantRecovery", "CharmManaRecoveryRate", "ManaOnEventRecovery",
             "ManaOnConsumeRecovery", "ManaFlameLeechRecovery", "ManaRiftRecovery",
             "ManaFlaskDrain",  # (MECHC38: a Life flask's "Removes N% of Life Recovered from Mana", NEGATIVE)
             "ManaLostOnKill"),  # (MECHC42: Arvil's Wheel's "Lose 1% of maximum Mana on Kill", NEGATIVE: kill_loss_income)
    # (MECHC17) Energy Shield on events: "Regenerate N% of maximum Energy Shield over 1 second when Stunned" (hit_income).
    # For SEARCH: objective.RECOVERY_POOLS["EnergyShield"] doesn't read it yet
    "EnergyShield": ("EnergyShieldOnEventRecovery", "EnergyShieldPreventedRecoup",  # (MECHC21: Scarred Faith's)
                     # (MECHC23, AUDIT33 A33-1) the Life flask's recovery applied to Energy Shield (Shavronne's Satchel:
                     # flasks.es_recovery_per_second, at the flask's own use rate)
                     "EnergyShieldFlaskRecovery",
                     "EnergyShieldGhostDanceRegen",  # (MECHC27: Ghost Dance's regeneration, ghost_dance_income)
                     "EnergyShieldSacrificed",  # (MECHC27, NEGATIVE: Eldritch Empowerment's sacrifice, sacrifice_income)
                     "EnergyShieldRiftRecovery",  # (MECHC29: Temporal Rift, rift_income)
                     "EnergyShieldLeechExcess"),  # (MECHC49: Soul Tether's excess Life Leech, leech_excess_income)
}
# The boss's Leech resistance (MECHC26; PATCH_CHECK H2-1, an OVERCOUNT before: neither PoB nor we applied any), a
# NEGATIVE field per pool: what PoB's {pool}LeechRate holds that the boss resists (leech_resisted). In FIELDS, so the
# sustain and recovery floors (objective's ADDED_RECOVERY) take it off once; Income.pool leaves it out (its sums are the
# event sources, and PoB's leech isn't one of them).
NEGATIVE_FIELDS = {"LifeFlaskDrain", "ManaFlaskDrain", "EnergyShieldSacrificed", "LifeSacrificed", "ManaLostOnKill"}  # (losses, not event income: Income.pool leaves them out)
LEECH_RESISTED = {"Life": "LifeLeechResisted", "Mana": "ManaLeechResisted", "EnergyShield": "EnergyShieldLeechResisted"}
for _pool, _field in LEECH_RESISTED.items():
    FIELDS[_pool] = (*FIELDS[_pool], _field)
ALL_FIELDS = tuple(f for fields in FIELDS.values() for f in fields)

LIFE_REMNANTS, MANA_REMNANTS = "Life Remnants", "Mana Remnants"
REMNANT_SKILLS = (LIFE_REMNANTS, MANA_REMNANTS)
# their level stats (game files / PoB's skill data: other.lua LifeRemnantsPlayer, act_int.lua ManaRemnantsPlayer)
LIFE_EVERY = "life_remnants_spawn_remnant_on_hit_vs_enemy_every_X_ms"  # 3000
LIFE_ON_KILL = "life_remnants_chance_to_spawn_orb_on_killing_enemy_%"  # 25 (+0.5 a quality)
MANA_EVERY = "mana_remnants_spawn_remnant_on_crit_vs_elemental_ailment_enemy_every_X_ms"  # 2000
MANA_ON_KILL = "mana_remnants_chance_to_spawn_orb_on_killing_enemy_with_elemental_ailment_%"  # 25
REMNANT_DEFAULTS = {LIFE_EVERY: 3000.0, LIFE_ON_KILL: 25.0, MANA_EVERY: 2000.0, MANA_ON_KILL: 25.0}

NUM = r"(\d+(?:\.\d+)?)"
PRESENCE_DEATH = re.compile(rf"recover {NUM}% of your maximum (life|mana) when an enemy dies in your presence", re.I)
REMNANT_PICKUP = re.compile(rf"recover {NUM}% of maximum (life|mana) when you collect a remnant", re.I)
CHARM_OWN = re.compile(rf"^recover {NUM} (life|mana) when used$", re.I)
CHARM_ANY = re.compile(rf"recover {NUM}% of maximum mana when a charm is used", re.I)
WARCRY_BOTH = re.compile(rf"recover {NUM}% of maximum life and mana when you use a warcry", re.I)
WARCRY_LIFE = re.compile(rf"recover {NUM}% of maximum life when you use a warcry", re.I)
HEAVY_STUN_UNIQUE = re.compile(rf"recover {NUM}% of maximum life when you heavy stun a rare or unique enemy", re.I)
IGNITE_PCT = re.compile(rf"recover {NUM}% of maximum life when you ignite an enemy", re.I)
IGNITE_FLAT = re.compile(rf"recover {NUM} life when you ignite an enemy", re.I)
SHOCK_PCT = re.compile(rf"recover {NUM}% of maximum mana when you shock an enemy", re.I)
# Spent and consumed (MECHC13; game files' stat descriptions, build 26476662, exact wording without the markup)
ENDURANCE_SPENT = re.compile(rf"recover {NUM}% of maximum life for each endurance charge consumed", re.I)
POWER_SPENT = re.compile(rf"recover {NUM}% of maximum mana when you consume a power charge", re.I)
GLORY_SPENT = re.compile(rf"recover {NUM}% of maximum life per glory consumed", re.I)
COMBO_SPENT = re.compile(rf"recover {NUM}% of maximum (life|mana) when you expend at least 10 combo", re.I)
COMBO_AT_LEAST = 10.0
CORPSE_LIFE = re.compile(rf"recover {NUM}% of maximum life when you consume a corpse", re.I)
CORPSE_MANA = re.compile(rf"regenerate {NUM}% of maximum mana over 2 seconds when you consume a corpse", re.I)  # (no PoE2 carrier: PoE1's Essence Glutton;
# RESEARCH26 R26-69, HIGH, checked every Stat column of the game tables, PoB-PoE2's Uniques and poe2db; kept, harmless)
OFFERING_LIFE = re.compile(rf"recover {NUM}% of maximum life when you create an offering", re.I)
# (MECHC18) you_gain_%_life_when_one_of_your_minions_is_revived (Stats 19093; the passive minion_defence9 and two Soul Core
# rows, MECHC17's carrier check): at the build's Persistent, Reviving minions Killed a second (models.minion_deaths: every
# one Killed is Revived once its revival ends, so in a steady fight the revives a second equal the deaths it caps)
MINION_REVIVED = re.compile(rf"recover {NUM}% of maximum life when one of your minions is revived", re.I)
# (MECHC21) Invigorating Hate (PassiveSkills rage27; conditions.INVIGORATING_HATE): at the Rage consumed a second by the
# build's shifts back to Human form (conditions.rage_held's build.charge_flow["Rage"])
RAGE_SHIFT = re.compile(rf"recover {NUM}% of maximum life per rage consumed", re.I)
# (MECHC53) The Devouring Diadem (PoB Data/Uniques/helmet.lua, Wicker Tiara, current variant): "Every 3 seconds, Consume a
# nearby Corpse to Recover 20% of maximum Life". PoB parses nothing of it and no model read it (checked: grep of builder/
# for "Devouring" / "Consume a nearby Corpse": none). It consumes at most one corpse every N s, from the corpses the boss
# fight gives (models.corpse_supply: the build's own Sacrifice corpses and the preset's killed adds; a lone boss gives
# none, KeywordPopups Corpse: a Unique's corpse can't be consumed). Headline: the corpses the build's corpse skills
# leave (the lower reading); row: the Diadem taking first. Whether its consume sets off "when you Consume a corpse"
# lines is UNVERIFIED: not added to them (the lower reading).
DIADEM = re.compile(rf"every {NUM} seconds, consume a nearby corpse to recover {NUM}% of maximum life", re.I)
# (MECHC21: MINION_REVIVED was missing here, so a build whose only spent line it was counted nothing: an UNDERCOUNT)
SPENT_LINES = (ENDURANCE_SPENT, POWER_SPENT, GLORY_SPENT, COMBO_SPENT, CORPSE_LIFE, CORPSE_MANA, OFFERING_LIFE,
               MINION_REVIVED, RAGE_SHIFT, DIADEM)
# On the boss's hits, your Blocks and your casts (MECHC14, AUDIT27 A27-32: PoB parses each to no mod, ModCache {nil, ...};
# game files' stat descriptions, build 26476662: missing_life_%_gained_as_life_before_hit (UniqueGainMissingLifeBeforeHit1),
# regenerate_%_armour_as_life_over_1_second_on_block (ArmourAsLifeRegnerationOnBlockUniqueShieldStrInt6),
# regenerate_X_life_over_1_second_on_cast (RegenerateLifeOnCastUniqueWand4) and PoB's Lifesprig wording "Leeches 1% of
# maximum Life when you Cast a Spell")
MISSING_BEFORE_HIT = re.compile(rf"recover {NUM}% of missing life before being hit by an enemy", re.I)
ARMOUR_ON_BLOCK = re.compile(rf"regenerate {NUM}% of your armour as life over 1 second when you block", re.I)
CAST_REGEN = re.compile(rf"regenerate {NUM} life over 1 second when you cast a spell", re.I)  # (no PoE2 carrier either: RESEARCH26 R26-69, HIGH;
# PoE2's Lifesprig has the Leech line below instead; kept, harmless)
CAST_LEECH = re.compile(rf"leeches {NUM}% of maximum life when you cast a spell", re.I)
# "Regenerate N% of maximum Life over 1 second when Stunned" (regenerate_%_life_over_1_second_when_stunned; two
# passives; PoB's ModCache {nil, ...}: parsed to no mod): at the boss's hits that Light Stun you (MECHC15, stuns_taken)
STUNNED_LIFE = re.compile(rf"regenerate {NUM}% of maximum life over 1 second when stunned", re.I)
# (MECHC17) its Energy Shield form (regenerate_%_energy_shield_over_1_second_when_stunned): no carrier found (checked:
# every stat column of every game table, build 26476662: no Mod, passive, Timeless conquest, support or skill stat set
# carries it; PoB's data only in ModScalability's stat descriptions, no unique; poe2db's page for Energy Barrier, the
# ES-when-Stunned support, doesn't show it; no further second source: UNVERIFIED that none exists). Read all the same,
# at the boss's Stuns, so a source counts the day one appears
STUNNED_ES = re.compile(rf"regenerate {NUM}% of maximum energy shield over 1 second when stunned", re.I)
# Physical damage prevented, Recouped (MECHC21; FIX_QUEUE MECH67's Scarred Faith note): Scarred Faith's "5% of Physical
# Damage prevented Recouped as Energy Shield per enemy Power" (KeywordPopups ScarredFaith) and the passives' "N% of
# Physical Damage prevented Recouped as Life" (physical_damage_prevented_recouped_as_life_%). PoB parses both with leftover
# text (ModCache: PhysicalDamage BASE + " prevented Recouped as ..."), so never applies them; the ledger called them
# no-effect (no-effect-dot-and-recoup-defences, and the Life entry) while recovery wasn't checked, which it is now (the
# user's rule, memory recovery-every-source). "Prevented" (KeywordPopups Premitigation: "the target's mitigation, such as
# Armour, Resistances or Blocking, prevents any of that damage"): the Physical damage the build's Armour and flat
# reduction keep from the boss's landed hits (Assumptions.hit_rates); damage Blocked isn't counted (a lower reading:
# whether Block counts as prevented for these lines is UNVERIFIED). Recoup returns over 4 s: in a steady fight the second's
# share of what's prevented. "per enemy Power" at the boss's Power (models.boss_power, with the build's Power lines).
# Scarred Faith's "You cannot Recover Energy Shield to above Armour" caps the pool, not the rate: not applied (LOW).
# The "if you have at least N Tribute" form (Twice Bled, PREVENTED_LIFE_TRIBUTE; MECHC25, COV27 (c): before, unread, an
# UNDERCOUNT) counts when PoB's Tribute output (CalcPerform 493, the summed "+N to Tribute") reaches N, else 0, noted.
PREVENTED_ES = re.compile(rf"^{NUM}% of physical damage prevented recouped as energy shield per enemy power$", re.I)
PREVENTED_LIFE = re.compile(rf"^{NUM}% of physical damage prevented recouped as life$", re.I)
PREVENTED_LIFE_TRIBUTE = re.compile(rf"^{NUM}% of physical damage prevented recouped as life if you have at least {NUM} "
                                    r"tribute$", re.I)
# (MECHC48; COV48's MECH-C row 12.0, an UNDERCOUNT before) Blood of the Warrior's "(15-30)% of Damage taken during
# effect Recouped as Life": PoB parses it with leftover text (ModCache 3157 / 3814 / 7238: DamageTaken BASE + " Recouped
# as Life"), so applies none. Its share of the damage the boss's landed hits deal a second (objective.boss_taken, every
# pool: the line names "Damage taken", not Life lost), x the flask's uptime (flasks.uptime: the flask's own "Effect is
# not removed when Unreserved Life is Filled" read there), as Life a second (recoup returns over 4 s: in a steady fight
# the second's share). Your recovery-rate modifiers on it: not applied (UNVERIFIED, the lower reading, as Igniferis');
# your own damage over time not counted as taken (the lower reading).
FLASK_RECOUP = re.compile(rf"^{NUM}% of Damage taken during effect Recouped as Life$", re.I)
# Damage Blocked Recouped as Mana (MECHC49; COV48 rows 10.2 / 5.0, an UNDERCOUNT before: PoB parses it to nothing,
# ModCache 7083 {nil, ...}). Stat damage_blocked_%_recouped_as_mana (checked: its users over Mods, PassiveSkills,
# SoulCoreStats and GrantedEffectStatSets, game 26650308: Alkem Eira's UniqueDamageBlockedRecoupedAsMana1 at 100, "Damage
# Blocked is Recouped as Mana", and one Soul Core row, SoulCoreStats 496 at 30 / bonded 20, "N% of damage Blocked is
# Recouped as Mana"; second source PoB's Uniques/shield.lua 364). KeywordPopups Recoup: "an amount of the stated resource
# equal to a percentage of the damage you took over 8 seconds"; in a steady fight the per-second figure is the share.
# Each blocked hit's damage is taken as the hit would have dealt after your mitigation (PoB's {Type}TakenHit at the boss's
# type shares and Deflection's expected share: the lower reading; whether Block's "damage" is before mitigation is
# UNVERIFIED), at the boss's blocked hits a second (the benefit pace, as objective.on_block). Mana recovery-rate
# modifiers not applied (UNVERIFIED, lower).
BLOCK_RECOUP_MANA = re.compile(rf"^(?:{NUM}% of )?damage blocked is recouped as mana$", re.I)
# Chaos damage prevented Recouped during a charm's effect (MECHC49; COV48 row 10.1, an UNDERCOUNT: PoB leaves leftover
# text, ModCache 4645, so applies none). Forsaken Bangle (Amethyst Charm, "Used when you take Chaos damage from a Hit"):
# "50% of Chaos damage you prevent when Hit Recouped as Life and Mana during effect", stat
# charm_x%_of_chaos_damage_from_hits_prevented_recouped_as_life_and_mana_during_effect (checked: Stats ids holding chaos
# with prevent / recoup over Mods, PassiveSkills, SoulCoreStats and GrantedEffectStatSets, game 26650308: one Mods row,
# UniqueCharmRecoupChaosDamagePrevented1; the non-charm twin x%_of_chaos_damage_from_hits_prevented_recouped_as_life_and_
# mana has no user; second source PoB's Uniques/flask.lua 124). The Chaos you prevent per hit: PoB's ChaosEnemyDamage (the hit before your mitigation) less ChaosTakenHit
# (after it), at Deflection's expected share and the boss's Chaos share of hits, at its landed hits (the gain pace, as the
# Physical line); what Deflection itself prevents isn't counted (UNVERIFIED whether it is "prevented": the lower
# reading). x the charm's uptime (flasks.uptime with its trigger); the same amount to Life and to Mana. Recovery-rate
# modifiers not applied (UNVERIFIED).
CHAOS_PREVENTED = re.compile(rf"^{NUM}% of chaos damage you prevent when hit recouped as life and mana during effect$",
                             re.I)
HIT_LINES = (MISSING_BEFORE_HIT, ARMOUR_ON_BLOCK, CAST_REGEN, CAST_LEECH, STUNNED_LIFE, STUNNED_ES, PREVENTED_ES,
             PREVENTED_LIFE, PREVENTED_LIFE_TRIBUTE, FLASK_RECOUP, BLOCK_RECOUP_MANA, CHAOS_PREVENTED)
# The game's Light Stun rule (KeywordPopups LightStun, build 26476662): "up to 100% base chance for Hits that deal 100%
# of the target's maximum Life" (StunThreshold: "A Hit that deals damage equal to or greater than your Stun Threshold
# is guaranteed to Stun you, and the chance scales down linearly"), "Chances lower than 15% are treated as 0%" (the
# StunThreshold popup says 10%: the higher floor taken, a lower bound). The game's two definitions disagree on the bonus
# (PATCH_CHECK K1-o1 / R4): KeywordPopups LightStun says "Physical damage, and player (but not monster) Melee Damage,
# each have 50% more Light Stun chance"; KeywordPopups Stun says "Monster Physical damage and Melee Damage have 100% more
# and 33% more Light Stun chance and Heavy Stun buildup respectively. These bonuses are multiplicative with each other",
# as 0.3.0's note does. Two sources against one (the manager, 15 Nov 01:01: HIGH): a boss's Physical hit x2 (MECHC26;
# was x1.5, an UNDERCOUNT of the Stuns on you). Its Melee 33% more (MECHC27, the manager 15 Nov 06:59: "x(1 + 0.33 x
# share)") applies to the share of the boss's hits that are Melee (MECH76's bosses.melee_share: a share of its damaging
# skills, LOW as hits; the worst case reads the highest real preset's), split per hit (a Melee hit at 1.33x, the rest at
# 1x, each under the 100% cap and the 15% floor: the manager's x(1 + 0.33 x share) wherever neither binds). Heavy Stun
# buildup on you isn't modelled here (only Light Stuns). Without melee_share (a tool before train 57): no bonus, said.
# PoB's own SelfStunChance uses other constants (Data.lua StunBaseMult 200,
# MinStunChanceNeeded 20) on its all-types-at-once enemy hit: not read
LIGHT_STUN_FLOOR, PHYSICAL_STUN_MORE, MONSTER_MELEE_STUN_MORE = 0.15, 2.0, 1.33
STUN_MORE_PER_RECENT, RECENT_STUN_SECONDS = 0.5, 4.0  # (MECHC27, L1-1: stuns_taken)


def stuns_taken(stats: dict, sizes: dict[str, float], weights: dict[str, float], landed: float) -> tuple[float, str]:
    """(Light Stuns the boss's hits put on you a second, how): `landed` hits a second, a `weights[t]` share of them of
    damage type t, each of `sizes[t]` damage after mitigation, against PoB's StunThreshold, less its StunAvoidChance."""
    threshold = _num(stats.get("StunThreshold"))
    avoid = min(max(_num(stats.get("StunAvoidChance")), 0.0), 100.0) / 100
    if threshold <= 0 or landed <= 0 or avoid >= 1:
        return 0.0, ("the build can't be Stunned" if avoid >= 1 else "no boss hit lands" if landed <= 0
                     else "no Stun Threshold")
    from builder.search import bosses

    melee_of = getattr(bosses, "melee_share", None)
    melee, melee_how = melee_of(stats) if melee_of is not None else (0.0, "no melee share in this build of the tool")
    melee = min(max(melee, 0.0), 1.0)

    def one(size: float) -> float:
        each = min(1.0, size)
        return each if each >= LIGHT_STUN_FLOOR else 0.0

    def chance_at(scale: float) -> tuple[float, list[str]]:
        total, parts = 0.0, []
        for t, w in weights.items():
            base = max(sizes.get(t, 0.0), 0.0) * (PHYSICAL_STUN_MORE if t == "Physical" else 1.0) / (threshold * scale)
            each = melee * one(base * MONSTER_MELEE_STUN_MORE) + (1 - melee) * one(base)
            total += w * each
            if w > 0:
                parts.append(f"{t} {w:.0%} of hits, {each:.0%}")
        return total, parts

    # (MECHC27; PATCH_CHECK L1-1, a small OVERCOUNT before) 0.2.0: "Players have 50% more Light Stun Threshold per time
    # stunned in past 4 seconds" (PoB Data/Misc.lua 222: light_stun_threshold_+%_final_per_number_of_times_stunned_in_past_
    # duration_from_ot 50; no PoB module reads it; no later note changes it). The Stuns a second s solve s = landed x
    # chance(threshold x (1 + 50% x 4 s x s)) (bisection): the expected count in the past 4 s, not each moment's own
    # count, and "more per time" taken as 1 + 50% per Stun (MEDIUM: the average of a nonlinear rule)
    first, _ = chance_at(1.0)
    raw = landed * first * (1 - avoid)
    low, high = 0.0, raw
    for _ in range(40):
        mid = (low + high) / 2
        if landed * chance_at(1 + STUN_MORE_PER_RECENT * RECENT_STUN_SECONDS * mid)[0] * (1 - avoid) > mid:
            low = mid
        else:
            high = mid
    rate = (low + high) / 2
    scale = 1 + STUN_MORE_PER_RECENT * RECENT_STUN_SECONDS * rate
    chance, parts = chance_at(scale)
    return rate, (f"{landed:.3f} boss hits landing/s x {chance:.1%} Light Stun chance ({'; '.join(parts)}: hit size "
                  f"after mitigation / Stun Threshold {threshold:,.0f}, a monster's Physical 100% more, its Melee 33% more on "
                  f"{melee:.0%} of hits ({melee_how}), under 15% none)"
                  + (f" x {1 - avoid:.0%} not avoided" if avoid > 0 else "")
                  + (f"; your threshold {scale - 1:.0%} higher from the Stuns of the past 4 s (50% more per Stun; "
                     f"{raw * 60:.1f} a minute without it)" if rate > 0 else "")
                  + "; each hit at its type's full size (LOW)")


def boss_stuns(hit: dict) -> tuple[float, str]:
    """(Light Stuns a second, how) from a stats-like dict (PoB's {Type}TakenHit, enemySkillTime, EvadeChance,
    EffectiveBlockChance, DeflectChance, DeflectEffect, StunThreshold, StunAvoidChance; "_boss" the request's boss): the
    boss's hits that land at the "when hit" benefit pace (objective.benefit_every), each type at its full hit size after
    mitigation and Deflection's expected share and its share of the boss's hits (MECHC15's stuns_taken; MECHC16: shared by
    the "when Stunned" recovery line and charms "Used when you become Stunned")."""
    from builder.search import boss_hits, objective  # (objective imports this module)

    def one(read: dict) -> tuple[float, str]:
        _, _, deflect = objective.boss_taken(read)
        landed = boss_hits.land_share(read) / objective.benefit_every(read)
        sizes = {t: max(_num(read.get(f"{t}TakenHit")), 0.0) * deflect for t in objective.RECOVERY_DAMAGE_TYPES}
        return stuns_taken(read, sizes, boss_hits.type_shares(boss_hits.boss_name(read)), landed)

    # (MECHC19, AUDIT29 A29-1: a gain) the worst-case pinnacle reads the lowest real boss's Stuns (boss_hits.gain_floor)
    return boss_hits.gain_floor(hit, one)


NO_LIFE = re.compile(r"cannot recover life other than from leech", re.I)
NO_MANA = re.compile(r"mana recovery other than regeneration cannot recover mana", re.I)
# Recovery on events no rate here reads yet (MECHC11's inventory section 7): named in the note, never counted (undercount)
EVENT_UNREAD = {
    # (Spirit Charges: no model of their gain or loss yet; MECHC13 left it here, an undercount)
    "Life when you lose a Spirit Charge": re.compile(r"recover \S+ of maximum life when you lose a spirit charge", re.I),
    "Life when your Trap is triggered": re.compile(r"life when your trap is triggered", re.I),
    "Life / Mana per Remnant's Rage (Sanguine Revelry)": re.compile(r"each remnant causes you to recover", re.I),
    "all Mana when a flask is Used": re.compile(r"^recover all mana when used$", re.I),
}



# Time lost to Light Stuns on you (MECHC49; RESEARCH40 R40-4, HIGH; PLANS6's kw-light-stun, an OVERCOUNT before: a
# Light Stun stops you acting, "preventing them from taking actions for a fraction of a second" (KeywordPopups
# LightStun), and nothing took that time off). The live character.ot: stun_base_duration_override_ms 500 (the Light
# Stun; base_heavy_stun_duration_ms 3000 is the Heavy one); PoB's StunDuration = 0.5 s x (1 + increased stun duration on
# you) / (1 + Stun Recovery), rounded up to a server tick (CalcDefence 2809-2813), read here. The Light Stuns a second:
# stuns_taken (the 15% floor, Physical's and melee's more Light Stun chance, the 50% more threshold per Stun in the past
# 4 s, PoB's StunAvoidChance: "Cannot be Light Stunned" is StunImmune there, so 0) over the boss's landed hits of each
# type at its OWN per-USE cadence (bosses.USE_EVERY) with the user's 2/3 of its dodgeable hits dodged and red-flash hits
# avoided (boss_hits.ailment_hits, the headline readings; MECHC50: the manager's call in its brief, item 3d). On the worst-case pinnacle, the worst
# real preset (a cost), as chill_and_freeze_on_you; the defence cadence (one hit every enemySkillTime, 82% of the fight
# on the bare Spark skeleton: far above any one boss) is the upper row. The share lost L = Light Stuns a second x
# StunDuration, at most 90%; it reaches PoB as Speed MORE -100 x L (tag LightStunned; MECHC50: not ActionSpeed, which
# "Unaffected by Slows" drops, and a stun isn't a Slow), behind assumptions.BOSS_TIME_CUTS. MEDIUM: a stun while Frozen
# isn't taken off (a small overcount of the loss); the stun animation running past 0.5 s: UNVERIFIED (R40-4). Row:
# without Light Stuns, 0.
LIGHT_STUN_FIELDS = ("StunThreshold", "StunAvoidChance", "StunDuration", "enemySkillTime", "EvadeChance",
                     "EffectiveBlockChance", "DeflectChance", "DeflectEffect",
                     "PhysicalTakenHit", "FireTakenHit", "ColdTakenHit", "LightningTakenHit", "ChaosTakenHit")
LIGHT_STUN_BASE, LIGHT_STUN_MOST = 0.5, 0.9


def _light_stuns(read: dict, cadence: str = "use") -> tuple[float, str]:
    """(Light Stuns a second, how) on one preset's stats at `cadence` (boss_hits.ailment_hits')."""
    from builder.search import boss_hits, objective  # (objective imports this module)

    _, _, deflect = objective.boss_taken(read)
    read = {**read, "_onyou_cadence": cadence}
    hits = {t: boss_hits.ailment_hits(read, t)[0] for t in objective.RECOVERY_DAMAGE_TYPES}
    landed = sum(hits.values())
    if landed <= 0:
        return 0.0, "no boss hit lands"
    sizes = {t: max(_num(read.get(f"{t}TakenHit")), 0.0) * deflect for t in objective.RECOVERY_DAMAGE_TYPES}
    rate, how = stuns_taken(read, sizes, {t: h / landed for t, h in hits.items()}, landed)
    return rate, f"{boss_hits.boss_name(read) or 'the boss'}: {how}"


def landed_hits(read: dict, cadence: str = "use") -> tuple[float, str]:
    """(MECHC51) (the boss's landed hits a second on you, how) at `cadence` (boss_hits.ailment_hits' per-type hits
    summed: its own uses, the dodge, red-flash avoided, after PoB's evade and block)."""
    from builder.search import boss_hits, objective  # (objective imports this module)

    worst = boss_hits._worst()
    if cadence == "use" and worst is not None and boss_hits.boss_name(read) == worst:
        # (the worst case: the real preset landing the most hits, as light_stun_lost reads it, not the defence cadence)
        picks = sorted(((landed_hits(r, cadence)[0], n) for n, r in boss_hits.real_readings(read)), key=lambda p: -p[0])
        if picks:
            return picks[0][0], f"the worst real preset, {picks[0][1]}: {picks[0][0] * 60:.1f} landed hits a minute"
    read = {**read, "_onyou_cadence": cadence}
    hits = sum(boss_hits.ailment_hits(read, t)[0] for t in objective.RECOVERY_DAMAGE_TYPES)
    return hits, f"{boss_hits.boss_name(read) or 'the boss'}: {hits * 60:.1f} landed hits a minute"


def light_stun_lost(stats: dict) -> tuple[float, list[dict]]:
    """(the share of the fight lost to Light Stuns, rows): the note above. `stats`: LIGHT_STUN_FIELDS and "_boss"."""
    from builder.search import boss_hits

    worst = boss_hits._worst()
    if worst is not None and boss_hits.boss_name(stats) == worst:
        picks = sorted(((*_light_stuns(read), name) for name, read in boss_hits.real_readings(stats)),
                       key=lambda r: -r[0])
        rate, how, _ = picks[0]
        others = ", ".join(f"{n}: {r * 60:.1f}" for r, _, n in picks[1:] if r > 0)
        how = f"the worst real preset ({how})" + (f"; the others a minute: {others}" if others else "")
    else:
        rate, how = _light_stuns(stats)
    upper = _light_stuns(stats, "defence")[0]
    seconds = _num(stats.get("StunDuration")) or LIGHT_STUN_BASE
    if rate <= 0 and upper <= 0:
        return 0.0, []
    lost = min(rate * seconds, LIGHT_STUN_MOST)
    return lost, [{"label": "Time lost to Light Stuns on you*",
                   "value": (f"{lost:.1%} of the fight (Speed x{1 - lost:.3f}); at the "
                             f"defence cadence: {min(upper * seconds, LIGHT_STUN_MOST):.1%}"),
                   "basis": (f"{rate * 60:.1f} Light Stuns a minute ({how}) x {seconds:.3f} s each (0.5 s base, the game's "
                             "character file; your Stun Recovery in PoB's StunDuration; R40-4). The boss's own uses (USE_EVERY), "
                             "2/3 of its dodgeable hits dodged (the user's rate), red-flash hits avoided; a stun while "
                             "Frozen not taken off. Row: without Light Stuns, 0% lost"),
                   "play": "more Stun Threshold or Stun Recovery cuts it (Cannot be Light Stunned: none)",
                   "confidence": "MEDIUM-LOW (0.5 s HIGH; the stun count from the presets' uses MEDIUM-LOW)"}]

def _sum(pattern: re.Pattern, texts: list[str], pool: str | None = None) -> float:
    total = 0.0
    for t in texts:
        for m in pattern.finditer(t):
            if pool is None or m.group(2).lower() == pool.lower():
                total += float(m.group(1))
    return total


@dataclass
class Income:
    """Life and Mana a second per field, with the parts the note names and the lines no rate reads."""
    rates: dict[str, float] = field(default_factory=lambda: {f: 0.0 for f in ALL_FIELDS})
    parts: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)
    # (MECHC16, AUDIT28 A28-4) Mana a second that is Regeneration ("Regenerate N% of maximum Mana over 2 seconds when you
    # Consume a corpse"): Indigon's "Mana Recovery other than Regeneration cannot Recover Mana" leaves it (restrict)
    mana_regen: float = 0.0

    def add(self, name: str, amount: float, how: str) -> None:
        if amount > 0:
            self.rates[name] += amount
            self.parts.append(f"{how}: {amount:,.1f}/s")

    def pool(self, pool: str) -> float:
        return sum(self.rates[f] for f in FIELDS[pool] if f != LEECH_RESISTED.get(pool) and f not in NEGATIVE_FIELDS)


def on_kill(income: Income, stats: dict, texts: list[str], enemies: float) -> None:
    """PoB's LifeOnKill / ManaOnKill and the "enemy dies in your Presence" lines at `enemies` kills a second."""
    if enemies <= 0:
        return
    for pool in ("Life", "Mana"):
        name = f"{pool}OnKillRecovery"
        each = max(_num(stats.get(f"{pool}OnKill")), 0.0)
        income.add(name, each * enemies, f"{pool} on Kill {each:,.0f} (PoB's {pool}OnKill) x {enemies:.3f} enemy kills/s")
        pct = _sum(PRESENCE_DEATH, texts, pool)
        maximum = _num(stats.get(pool))
        income.add(name, pct / 100 * maximum * enemies,
                   f"{pct:g}% of maximum {pool} when an enemy dies in your Presence x {enemies:.3f}/s (every add taken "
                   "as dying in your Presence, LOW)")


def remnant_income(income: Income, skills: dict[str, dict], stats: dict, texts: list[str], *, hits: float,
                   crits: float, ailment_share: float, ailment_how: str, enemies: float,
                   others: tuple[float, str] = (0.0, ""), multiplier: float = 1.0, multiplier_how: str = "") -> None:
    """Life / Mana Remnants (`skills`: {name: {"gain": per Remnant, "levelStats": {...}}}) at the build's hits, crits on
    a target with an Elemental Ailment (`ailment_share` of the fight) and kills; pickup lines on every Remnant the build
    collects: these plus `others` (the build's other Remnant sources a second and what they are; MECHC15, A26-3).
    `multiplier`: times each Remnant made counts when collected (additional Remnants, reappearing, double pickups;
    assumptions.remnant_multiplier; COV17), on the Life / Mana Remnants' own gain and on the pickup lines."""
    count = 0.0
    times = f" x{multiplier:.3g} ({multiplier_how})" if multiplier != 1.0 else ""
    for name, info in skills.items():
        level = {**REMNANT_DEFAULTS, **{k: _num(v) for k, v in (info.get("levelStats") or {}).items()}}
        pool = "Life" if name == LIFE_REMNANTS else "Mana"
        gain = _num(info.get("gain"))
        if name == LIFE_REMNANTS:
            every, triggers = level[LIFE_EVERY] / 1000, hits
            kills = level[LIFE_ON_KILL] / 100 * enemies
            trigger_how = f"{hits:.2f} hits/s"
        else:
            every, triggers = level[MANA_EVERY] / 1000, crits * ailment_share
            kills = level[MANA_ON_KILL] / 100 * enemies * ailment_share
            trigger_how = f"{crits:.2f} crits/s x the boss carrying an Elemental Ailment {ailment_share:.0%} ({ailment_how})"
        # (the next Remnant waits `every` s, then for the next qualifying hit: 1 / (every + 1 / rate))
        timed = 1.0 / (every + 1.0 / triggers) if triggers > 0 and every > 0 else 0.0
        rate = (timed + kills) * multiplier * _num(info.get("scale", 1.0))  # (MECHC60: its supports' extra Remnants)
        count += rate
        income.add(f"{pool}RemnantRecovery", rate * gain,
                   f"{name} {rate:.3f} Remnants/s x {gain:,.0f} {pool} (one at most every {every:g} s on {trigger_how}"
                   + (f", plus {kills:.3f}/s from kills" if kills > 0 else "") + times + "; each taken as collected)")
    other = max(0.0, others[0]) * multiplier
    for pool in ("Life", "Mana"):
        pct = _sum(REMNANT_PICKUP, texts, pool)
        if pct > 0 and count + other > 0:
            income.add(f"{pool}RemnantRecovery", pct / 100 * _num(stats.get(pool)) * (count + other),
                       f"{pct:g}% of maximum {pool} per Remnant collected x {count + other:.3f}/s ("
                       + (f"Life / Mana Remnants {count:.3f}/s" if count else "")
                       + ("; " if count and other else "")
                       + (f"other Remnants {other:.3f}/s{times}: {others[1]}" if other else "")
                       + "; each taken as collected)")


def charm_income(income: Income, charms: list[tuple[str, str, float, str]], stats: dict, texts: list[str]) -> None:
    """`charms`: (name, raw item text, uses a second, how) per equipped charm."""
    any_pct = _sum(CHARM_ANY, texts)
    total = 0.0
    for name, raw, uses, how in charms:
        total += uses
        if uses <= 0:
            continue
        for line in raw.splitlines():
            if m := CHARM_OWN.match(line.strip()):
                pool = m.group(2).capitalize()
                income.add(f"Charm{pool}RecoveryRate", float(m.group(1)) * uses,
                           f"{name}: {float(m.group(1)):g} {pool} when used x {uses:.3f} uses/s ({how})")
    if any_pct > 0 and total > 0:
        income.add("CharmManaRecoveryRate", any_pct / 100 * _num(stats.get("Mana")) * total,
                   f"{any_pct:g}% of maximum Mana when a Charm is used x {total:.3f} charm uses/s")


# "Recover N% of maximum Life when you use a Mana Flask" (MECHC33; COV37 via the manager, 16 Nov 11:48: an UNDERCOUNT,
# listed in EVENT_UNREAD before; PoB: no mod, ModCache 9097 / 9163): N% of maximum Life at each use of the Mana flask, at
# flasks.uses_per_second's rate for it (its generated charges, capped at the dips below full Mana: a Mana flask isn't
# drunk at full Mana, RESEARCH26 R26-29 (b), MEDIUM). Without it: 0.
MANA_FLASK_LIFE = re.compile(r"\brecover (\d+(?:\.\d+)?)% of maximum life when you use a mana flask\b", re.I)


def mana_flask_income(income: Income, stats: dict, texts: list[str], uses: float, how: str) -> None:
    """Life a second from MANA_FLASK_LIFE lines at the Mana flask's `uses` a second."""
    pct = sum(float(m.group(1)) for t in texts if (m := MANA_FLASK_LIFE.search(t)))
    if pct <= 0:
        return
    life = _num(stats.get("Life"))
    income.add("LifeOnEventRecovery", pct / 100 * life * uses,
               f"{pct:g}% of maximum Life a Mana flask use x {uses:.3f} uses/s ({how}; MEDIUM; without it: 0)")


def event_income(income: Income, stats: dict, texts: list[str], *, warcries: float = 0.0, warcry_how: str = "",
                 heavy_stuns: float = 0.0, heavy_how: str = "", hits: float = 0.0) -> None:
    life, mana = _num(stats.get("Life")), _num(stats.get("Mana"))
    both, only = _sum(WARCRY_BOTH, texts), _sum(WARCRY_LIFE, texts)
    if warcries > 0:
        income.add("LifeOnEventRecovery", (both + only) / 100 * life * warcries,
                   f"{both + only:g}% of maximum Life a Warcry x {warcries:.3f} Warcries/s ({warcry_how})")
        income.add("ManaOnEventRecovery", both / 100 * mana * warcries,
                   f"{both:g}% of maximum Mana a Warcry x {warcries:.3f} Warcries/s")
    stun = _sum(HEAVY_STUN_UNIQUE, texts)
    if heavy_stuns > 0:
        income.add("LifeOnEventRecovery", stun / 100 * life * heavy_stuns,
                   f"{stun:g}% of maximum Life a Heavy Stun of the boss x {heavy_stuns:.3f}/s ({heavy_how})")
    ignites = hits * min(_num(stats.get("IgniteChancePerHit")), 100) / 100
    if ignites > 0:
        pct, flat = _sum(IGNITE_PCT, texts), _sum(IGNITE_FLAT, texts)
        income.add("LifeOnEventRecovery", (pct / 100 * life + flat) * ignites,
                   f"{pct:g}% of maximum Life + {flat:g} Life an Ignite x {ignites:.2f} Ignites/s (the main skill's "
                   "hits x its Ignite chance)")
    shocks = hits * min(_num(stats.get("ShockChancePerHit")), 100) / 100
    if shocks > 0:
        pct = _sum(SHOCK_PCT, texts)
        income.add("ManaOnEventRecovery", pct / 100 * mana * shocks,
                   f"{pct:g}% of maximum Mana a Shock x {shocks:.2f} Shocks/s (the main skill's hits x its Shock chance)")


def spent_income(income: Income, stats: dict, texts: list[str], *, endurance: tuple[float, str] = (0.0, ""),
                 power: tuple[float, str] = (0.0, ""), glory: tuple[float, str] = (0.0, ""),
                 combo: tuple[float, str] = (0.0, ""), corpses: tuple[float, str] = (0.0, ""),
                 offerings: tuple[float, str] = (0.0, ""), revived: tuple[float, str] = (0.0, ""),
                 rage: tuple[float, str] = (0.0, ""), diadem: tuple[float, str] = (0.0, "")) -> None:
    """Recovery on spending and consuming (MECHC13): each (events a second, how) from the build's own rates; Endurance /
    Power: charges consumed a second; Glory: Glory spent a second; Combo: uses of a Combo skill that expend 10 or more a
    second; corpses: corpses consumed a second; Offerings: Offerings created a second; revived: your minions Revived a
    second (MECHC18, into LifeOnEventRecovery). No rate, nothing: said in `parts`."""
    life, mana = _num(stats.get("Life")), _num(stats.get("Mana"))
    rows = (("Life", ENDURANCE_SPENT, endurance, "an Endurance Charge consumed", None),
            ("Mana", POWER_SPENT, power, "a Power Charge consumed", None),
            ("Life", GLORY_SPENT, glory, "a Glory spent", None),
            ("Life", COMBO_SPENT, combo, "a use expending 10+ Combo", "Life"),
            ("Mana", COMBO_SPENT, combo, "a use expending 10+ Combo", "Mana"),
            ("Life", CORPSE_LIFE, corpses, "a corpse consumed", None),
            ("Mana", CORPSE_MANA, corpses, "a corpse consumed (over 2 s)", None),
            ("Life", OFFERING_LIFE, offerings, "an Offering created", None),
            ("Life", MINION_REVIVED, revived, "a Minion Revived", None),
            ("Life", RAGE_SHIFT, rage, "Rage consumed shifting to Human form", None))
    for pool, pattern, (rate, how), event, which in rows:
        pct = _sum(pattern, texts, which) if which else sum(float(m.group(1)) for t in texts for m in pattern.finditer(t))
        if pct <= 0:
            continue
        if rate <= 0:
            income.parts.append(f"{pct:g}% of maximum {pool} per {event}: 0, {how or 'the build makes none'}")
            continue
        amount = pct / 100 * (life if pool == "Life" else mana) * rate
        income.add(f"{pool}On{'Event' if pattern is MINION_REVIVED else 'Consume'}Recovery", amount,
                   f"{pct:g}% of maximum {pool} per {event} x {rate:.3f}/s ({how})")
        if pattern is CORPSE_MANA:
            income.mana_regen += amount
    for t in texts:  # (MECHC53: DIADEM's note; `diadem`: the corpses it consumes a second, at most one every N s)
        m = DIADEM.search(t)
        if not m:
            continue
        pct, (rate, how) = float(m.group(2)), diadem
        if rate <= 0:
            income.parts.append(f"The Devouring Diadem's {pct:g}% of maximum Life a corpse: 0, {how or 'no corpse to consume'}")
            continue
        income.add("LifeOnConsumeRecovery", pct / 100 * life * rate,
                   f"The Devouring Diadem: {pct:g}% of maximum Life a corpse x {rate:.3f} corpses/s ({how})")


def hit_income(income: Income, stats: dict, texts: list[str], *, landed: float = 0.0, life_per_hit: float = 0.0,
               blocked: float = 0.0, casts: float = 0.0, cast_how: str = "", stunned: float = 0.0,
               stunned_how: str = "", refill: float = 0.0, refill_how: str = "", prevented: float = 0.0,
               prevented_how: str = "", power: float = 0.0, power_how: str = "", taken: float = 0.0,
               flask_recoup: list[tuple[float, float, str]] | None = None, blocked_hit: float = 0.0,
               chaos_recoup: list[tuple[float, float, str]] | None = None, chaos_prevented: float = 0.0,
               chaos_how: str = "") -> None:
    """Life on the boss's hits, your Blocks and your casts (MECHC14, A27-32), each at the build's own rate: `landed` boss
    hits a second and the Life each takes (`life_per_hit`), `blocked` boss hits a second, `casts` Spells cast a second.
    "Recover N% of Missing Life before being Hit" (MECHC16, AUDIT28 A28-1: before, the last hit's Life alone, an OVERCOUNT
    whenever something refills Life between hits): the Life missing before a hit is the last hit's less what the build
    refills in the time between landed hits (`refill` Life a second, net of its Life costs: regeneration, leech, flasks,
    recoup and recovery.py's other sources), at least 0 (Life full before each hit: nothing). LOW, a lower bound: Life
    still missing from earlier hits isn't carried over (in a fight the build loses, missing Life grows until the line
    returns the deficit each hit)."""
    life = _num(stats.get("Life"))
    before = income.rates["LifeOnEventRecovery"]
    pct = _sum(ARMOUR_ON_BLOCK, texts)
    if pct > 0:
        armour = _num(stats.get("Armour"))
        if blocked > 0 and armour > 0:
            income.add("LifeOnEventRecovery", pct / 100 * armour * blocked,
                       f"{pct:g}% of Armour {armour:,.0f} as Life a Block x {blocked:.3f} boss hits Blocked/s")
        else:
            income.parts.append(f"{pct:g}% of Armour as Life when you Block: 0, the build Blocks none of the boss's hits")
    flat, leech = _sum(CAST_REGEN, texts), _sum(CAST_LEECH, texts)
    if flat > 0 or leech > 0:
        if casts > 0:
            income.add("LifeOnEventRecovery", (flat + leech / 100 * life) * casts,
                       f"{flat:g} Life + {leech:g}% of maximum Life (leeched: its rate cap not applied) a Spell cast x "
                       f"{casts:.2f} casts/s ({cast_how})")
        else:
            income.parts.append(f"Life when you Cast a Spell: 0, {cast_how or 'the build casts no Spell'}")
    pct = _sum(STUNNED_LIFE, texts)
    if pct > 0:
        if stunned > 0:
            income.add("LifeOnEventRecovery", pct / 100 * life * stunned,
                       f"{pct:g}% of maximum Life when Stunned x {stunned:.3f} Stuns/s ({stunned_how})")
        else:
            income.parts.append(f"{pct:g}% of maximum Life when Stunned: 0, {stunned_how or 'the boss never Stuns you'}")
    pct = _sum(STUNNED_ES, texts)
    if pct > 0:  # (MECHC17: the Energy Shield form, at the same Stuns)
        shield = _num(stats.get("EnergyShield"))
        if stunned > 0 and shield > 0:
            income.add("EnergyShieldOnEventRecovery", pct / 100 * shield * stunned,
                       f"{pct:g}% of maximum Energy Shield {shield:,.0f} when Stunned x {stunned:.3f} Stuns/s ({stunned_how})")
        else:
            income.parts.append(f"{pct:g}% of maximum Energy Shield when Stunned: 0, "
                                + ("the build has no Energy Shield" if shield <= 0 else
                                   stunned_how or "the boss never Stuns you"))
    for pool, pattern, field_name in (("Life", PREVENTED_LIFE, "LifePreventedRecoup"),
                                      ("Energy Shield", PREVENTED_ES, "EnergyShieldPreventedRecoup")):
        pct = sum(float(m.group(1)) for t in texts if (m := pattern.search(t.strip())))
        if pattern is PREVENTED_LIFE:  # (MECHC25) Twice Bled's Tribute form, when the build has the Tribute
            tribute = _num(stats.get("Tribute"))
            for t in texts:
                if (m := PREVENTED_LIFE_TRIBUTE.search(t.strip())) is None:
                    continue
                if tribute >= float(m.group(2)):
                    pct += float(m.group(1))
                else:
                    income.parts.append(f"{m.group(1)}% of Physical damage prevented Recouped as Life: 0, the build has "
                                        f"{tribute:g} Tribute, under the line's {m.group(2)}")
        if pct <= 0:
            continue
        per = f" per enemy Power x {power:g} ({power_how or 'the boss'})" if pattern is PREVENTED_ES else ""
        share = pct * (power if pattern is PREVENTED_ES else 1.0) / 100
        if prevented > 0 and share > 0:
            income.add(field_name, share * prevented, f"{pct:g}% of Physical damage prevented{per} Recouped as {pool} x "
                                                      f"{prevented:,.1f} prevented/s ({prevented_how})")
        else:
            income.parts.append(f"{pct:g}% of Physical damage prevented Recouped as {pool}: 0, "
                                + (prevented_how or "no Physical damage prevented from the boss's hits"))
    pct = sum(float(m.group(1) or 100) for t in texts if (m := BLOCK_RECOUP_MANA.match(t.strip())))
    if pct > 0:  # (MECHC49: BLOCK_RECOUP_MANA's note; `blocked_hit` the damage of one blocked hit)
        if blocked > 0 and blocked_hit > 0:
            income.add("ManaOnEventRecovery", pct / 100 * blocked_hit * blocked,
                       f"{pct:g}% of damage Blocked Recouped as Mana x {blocked_hit:,.0f} a blocked hit (after your "
                       f"mitigation: the lower reading) x {blocked:.3f} boss hits Blocked/s (Mana recovery-rate modifiers "
                       "not applied: UNVERIFIED)")
        else:
            income.parts.append(f"{pct:g}% of damage Blocked Recouped as Mana: 0, the build Blocks none of the boss's hits")
    for pct, up, what in chaos_recoup or []:  # (MECHC49: CHAOS_PREVENTED's note)
        amount = pct / 100 * chaos_prevented * up
        if amount > 0:
            how = (f"{pct:g}% of Chaos damage prevented Recouped during {what}'s effect x {chaos_prevented:,.1f} Chaos "
                   f"prevented/s ({chaos_how}) x the charm up {up:.0%} of the fight")
            income.add("LifePreventedRecoup", amount, how + " (as Life)")
            income.add("ManaOnEventRecovery", amount, how + " (as Mana)")
        else:
            income.parts.append(f"{pct:g}% of Chaos damage prevented Recouped as Life and Mana during {what}'s effect: 0, "
                                + ("the charm is never up" if up <= 0 else chaos_how or "no Chaos prevented"))
    for pct, up, what in flask_recoup or []:  # (MECHC48: FLASK_RECOUP's note)
        if taken > 0 and up > 0:
            income.add("LifeOnEventRecovery", pct / 100 * taken * up,
                       f"{pct:g}% of Damage taken Recouped as Life during {what}'s effect x {taken:,.1f} damage taken/s "
                       f"from the boss's landed hits x the flask up {up:.0%} of the fight (recovery-rate modifiers not "
                       "applied: UNVERIFIED)")
        else:
            income.parts.append(f"{pct:g}% of Damage taken during {what}'s effect Recouped as Life: 0, "
                                + ("no boss hit lands" if taken <= 0 else "the flask is never up"))
    pct = _sum(MISSING_BEFORE_HIT, texts)
    if pct > 0:
        if landed > 0 and life_per_hit > 0:
            hit = min(life_per_hit, life)
            # (the cast, Block and Stun lines above refill Life between hits too)
            own = income.rates["LifeOnEventRecovery"] - before
            refill = max(refill, 0.0) + own
            between = refill / landed
            missing = max(hit - between, 0.0)
            what = ", ".join(x for x in (refill_how, f"the cast / Block / Stun lines {own:,.1f}" if own > 0 else "") if x)
            how = (f"{pct:g}% of Missing Life before a hit x {missing:,.0f} Life missing (the last hit's {hit:,.0f} less "
                   f"{between:,.0f} refilled in the {1 / landed:.2f} s between landed hits at {refill:,.1f} Life/s: "
                   f"{what or 'nothing'}; LOW: a lower bound) x {landed:.3f} boss hits landing/s")
            if missing > 0:
                income.add("LifeOnEventRecovery", pct / 100 * missing * landed, how)
            else:
                income.parts.append(f"{how}: 0, Life is full again before each hit")
        else:
            income.parts.append(f"{pct:g}% of Missing Life before being Hit: 0, no boss hit reaches Life")


def restrict(income: Income, texts: list[str]) -> None:
    """Vaal Pact's "Cannot Recover Life other than from Leech", "Mana Recovery other than Regeneration cannot Recover Mana"."""
    for pool, pattern in (("Life", NO_LIFE), ("Mana", NO_MANA)):
        if any(pattern.search(t) for t in texts) and income.pool(pool) > 0:
            for name in FIELDS[pool]:
                income.rates[name] = 0.0
            kept = income.mana_regen if pool == "Mana" else 0.0
            if kept > 0:  # (MECHC16, AUDIT28 A28-4: the corpse line regenerates, and Indigon allows Regeneration)
                income.rates["ManaOnConsumeRecovery"] = kept
            income.parts.append(f"none of the {pool} above counts: the build has '{pattern.pattern}'"
                                + (f", except the {kept:,.1f} Mana/s regenerated when you Consume a corpse (it is "
                                   "Regeneration, which the line allows; UNVERIFIED in game, LOW)" if kept > 0 else ""))


def unread(texts: list[str]) -> list[str]:
    return [name for name, pattern in EVENT_UNREAD.items() if any(pattern.search(t.strip()) for t in texts)]


def note(income: Income) -> list[dict]:
    """The assumption note: what each source adds, or nothing when the build has none."""
    life, mana, shield = income.pool("Life"), income.pool("Mana"), income.pool("EnergyShield")
    missing = (f" Not counted (no rate reads them yet, so an undercount): {', '.join(income.unread)}."
               if income.unread else "")
    starts = any("Energy Shield Recharge starts" in p for p in income.parts)
    if life <= 0 and mana <= 0 and shield <= 0 and not starts and not income.unread:
        return []
    sources = "; ".join(income.parts) or "none of these sources gives anything in this fight"
    return [{
        "label": "Life and Mana from kills, Remnants, charms and other events*",
        "value": f"{life:,.1f} Life/s, {mana:,.1f} Mana/s" + (f", {shield:,.1f} Energy Shield/s" if shield > 0 else ""),
        "basis": f"{sources}. Rates from the build's own hits, kills (the boss preset's adds), charms and Warcries; "
                 f"amounts as written (recovery-rate modifiers not applied).{missing} Fields for the sustain and recovery "
                 f"floors: {', '.join(f for f in ALL_FIELDS if income.rates[f] > 0) or 'none above 0'}",
        "play": ("Walk onto each Remnant as it appears (it lasts 8 s); " if any("Remnant" in p for p in income.parts)
                 else "") + ("hold the use that starts Energy Shield Recharge until a boss hit has reset it, then use it "
                             "(with it: recharge from that moment; without it: only after the full delay); " if starts
                             else "") + "the recovery above comes with the play it names (without it: regeneration, leech and "
                           "flasks only)",
        "confidence": "MEDIUM-LOW"}]


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


# Monster Leech resistance by monster level (MECHC26; PATCH_CHECK H2-1): DefaultMonsterStats.LeechResistance, levels
# 1-100, permyriad (game files, Steam build 26476662, exported by MECHC26 with tools/datamine/export.mjs; the column
# RareUniqueLeechResistance is the same at every level). KeywordPopups LifeLeech / ManaLeech / EnergyShieldLeech:
# "Monsters have Leech Resistance that increases with monster level, reducing how much you recover from Leech from Hits
# against them"; stats base_resist_life_leech_permyriad / _mana_ / _energy_shield_. The shape matches 0.5.0's note
# (grep_notes "Leech Resist", its only hit): "Monster Leech resistance now starts at a later level, and values are
# lower at every level. All monster rarities now have the same amount of Leech resistance" (0 up to level 30 here; one
# column for every rarity). Second source: poe2db's Monster page shows the same table's Life / Damage by level (32,956 /
# 353.67 at 82) but no Leech column, so the Leech figures have the game files alone (MEDIUM; an in-game check is the
# user's: leech on a level-82+ boss against a low-level monster). PoB has no enemy Leech term (CalcOffence getBaseLeech).
MONSTER_LEECH_RESISTANCE = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    248, 638, 987, 1416, 1835, 2246, 2634, 3013, 3384, 3742,
    4077, 4405, 4717, 5022, 5307, 5453, 5601, 5749, 5892, 6038,
    6178, 6314, 6453, 6586, 6718, 6848, 6973, 7097, 7218, 7336,
    7453, 7565, 7674, 7781, 7884, 8003, 8119, 8227, 8332, 8432,
    8528, 8619, 8706, 8789, 8867, 8942, 9013, 9080, 9144, 9204,
    9262, 9316, 9367, 9415, 9460, 9502, 9542, 9579, 9613, 9645,
    9675, 9703, 9729, 9752, 9775, 9795, 9814, 9831, 9847, 9861,
)
MAX_ENEMY_LEVEL = 85  # (PoB's data.misc.MaxEnemyLevel, Modules/Data.lua: its enemy level is min(this, character level))
LEECH_POOLS = ("Life", "Mana", "EnergyShield")
LEECH_FIELDS = tuple(f for pool in LEECH_POOLS for f in (
    f"{pool}LeechRate", f"{pool}LeechInstantRate", f"{pool}LeechInstances", f"{pool}LeechInstanceRate",
    f"Max{pool}LeechRate", f"{pool}RecoveryRateMod"))


def leech_resistance(level: float) -> float:
    """The monster level's Leech resistance, 0-1 (MONSTER_LEECH_RESISTANCE)."""
    index = min(max(int(level), 1), len(MONSTER_LEECH_RESISTANCE)) - 1
    return MONSTER_LEECH_RESISTANCE[index] / 10000


def leech_resisted(stats: dict, resist: float) -> dict[str, float]:
    """{pool: the leech a second PoB's {pool}LeechRate holds that a boss with `resist` Leech resistance takes off}.

    PoB (CalcOffence 4908-4935): rate = instant rate + min(instances x instance rate, Max{pool}LeechRate) x
    {pool}RecoveryRateMod, the instances from each hit's leech amount. The resistance cuts each hit's amount, so the
    instant part and the instances scale by (1 - resist) and the rate cap holds as before. Where PoB's rate isn't that
    sum (Immortal Ambition moving Life leech to Energy Shield; "Unaffected by non-instant Life Leech"), the rate itself
    scales by (1 - resist). Whether the resistance applies before or after the per-hit cap ("Hits that deal more than
    40,000 total damage are treated as though they only dealt 40,000") is UNVERIFIED: taken after, so a hit over the cap
    loses the same share."""
    keep = 1.0 - min(max(resist, 0.0), 1.0)
    out = {}
    for pool in LEECH_POOLS:
        rate = _num(stats.get(f"{pool}LeechRate"))
        if rate <= 0 or keep >= 1.0:
            continue
        instant = _num(stats.get(f"{pool}LeechInstantRate"))
        uncapped = _num(stats.get(f"{pool}LeechInstances")) * _num(stats.get(f"{pool}LeechInstanceRate"))
        cap, mod = _num(stats.get(f"Max{pool}LeechRate")), _num(stats.get(f"{pool}RecoveryRateMod")) or 1.0
        if cap > 0 and abs(instant + min(uncapped, cap) * mod - rate) <= 1e-6 * max(rate, 1.0):
            kept = instant * keep + min(uncapped * keep, cap) * mod
        else:
            kept = rate * keep
        out[pool] = max(rate - kept, 0.0)
    return out


def leech_income(income: Income, stats: dict, level: float) -> list[dict]:
    """The boss's Leech resistance as negative recovery (LEECH_RESISTED), at the enemy `level` PoB scores against; its
    note, or [] when PoB gives the build no leech."""
    resist = leech_resistance(level)
    parts = []
    for pool, lost in leech_resisted(stats, resist).items():
        if lost > 0:
            income.rates[LEECH_RESISTED[pool]] = -lost
            parts.append(f"{pool} {_num(stats.get(f'{pool}LeechRate')):,.1f}/s in PoB, {lost:,.1f}/s resisted")
    if not parts:
        return []
    return [{"label": "The boss's Leech resistance*",
             "value": f"{resist:.1%} of each hit's leech (monster level {int(level)})",
             "basis": ("; ".join(parts) + ". DefaultMonsterStats.LeechResistance (game files; the same for every "
                       "rarity, 0 up to level 30, as 0.5.0's note describes); PoB applies none. The cut is on each hit's "
                       "leech, so PoB's maximum leech rate still holds; taken off the sustain and recovery floors"),
             "confidence": "MEDIUM (the table: game files alone; poe2db shows no Leech column)"}]


# Into the Breach's Red / Blue Flames of Chayula (MECHC26; PATCH_CHECK H1-9, an UNDERCOUNT before: PoB only displays
# BreachFlameLifeLeech / ManaLeech, CalcOffence 1700-1718). KeywordPopups RedFlamesOfChayula / BlueFlamesOfChayula:
# "Leech 20% of your maximum Life / Mana to you when collected" (IntoTheBreachPlayer breach_flame_life_leech_% /
# _mana_leech_% 20; 0.5.0: 20%, was 15% since 0.4.0, 7% before), x Remnant effect (the bridge's remnantGain FlameLife /
# FlameMana, as PoB builds it). A flame every breach_flame_spawn_rate_ms 1210 (REMNANT_SOURCES), each collected (the
# Remnant play note). Its colour: "All Flames of Chayula that you manifest are Red / Blue / Purple" (Choice of Life / Mana /
# Power, Acolyte of Chayula) sets it; without one, a third each: Purple a third is THE USER'S OBSERVATION FROM PLAY
# (16 Nov ~21:10, relayed by the manager 21:16: "About a third"; MEDIUM; the game files give no weights), Red and Blue
# splitting the other two thirds evenly (UNVERIFIED, LOW). Not a
# hit against the boss, so its Leech resistance doesn't apply ("Leech from Hits against them"). Held under PoB's maximum
# leech rate less the hit leech the build keeps (one cap for all Leech, a lower reading: "You can only recover from a
# single instance of Life Leech at a time" is UNVERIFIED against PoB's several).
FLAME_COLOUR = re.compile(r"^all flames of chayula that you manifest are (red|blue|purple)$", re.I)
FLAME_POOLS = {"Life": ("FlameLife", "red", "LifeFlameLeechRecovery"), "Mana": ("FlameMana", "blue", "ManaFlameLeechRecovery")}


def flame_shares(texts: list[str]) -> tuple[dict[str, float], str]:
    """({colour: share of the flames}, how)."""
    chosen = {m.group(1).lower() for t in texts if (m := FLAME_COLOUR.match(t.strip()))}
    if chosen:
        return ({c: 1.0 / len(chosen) if c in chosen else 0.0 for c in ("red", "blue", "purple")},
                "all " + " / ".join(sorted(chosen)) + " (the build's 'All Flames of Chayula ... are' line)")
    return {c: 1 / 3 for c in ("red", "blue", "purple")}, (
        "a third Purple (the user's observation from play, 16 Nov: \"about a third\"; MEDIUM; no weights in the game "
        "files), Red and Blue a third each (the other two thirds split evenly: UNVERIFIED, LOW)")


def flame_income(income: Income, stats: dict, flames: float, gain: dict, texts: list[str], how: str) -> None:
    """Life / Mana a second from collecting Red / Blue Flames of Chayula: `flames` a second (after the build's Remnant
    multiplier lines), `gain` the bridge's remnantGain for Into the Breach (FlameLife / FlameMana: % of maximum, x Remnant
    effect). Call after leech_income (its resisted hit leech sets the room left under the cap)."""
    if flames <= 0:
        return
    shares, colour = flame_shares(texts)
    for pool, (key, hue, name) in FLAME_POOLS.items():
        pct = _num((gain or {}).get(key))
        if pct <= 0 or shares[hue] <= 0:
            continue
        want = flames * shares[hue] * pct / 100 * _num(stats.get(pool))
        kept = _num(stats.get(f"{pool}LeechRate")) + income.rates.get(LEECH_RESISTED[pool], 0.0)
        cap = _num(stats.get(f"Max{pool}LeechRate"))
        room = max(cap - kept, 0.0) if cap > 0 else want
        amount = min(want, room)
        if amount > 0:
            income.add(name, amount, f"{hue.title()} Flames of Chayula ({flames:.2f} flames/s, {how}; {colour}), Leech "
                                     f"{pct:g}% of maximum {pool} each" + (f", held to the {cap:,.0f}/s leech cap less "
                                                                            f"{kept:,.1f}/s hit leech" if amount < want else ""))


# Excess Life Leech applied to Energy Shield (MECHC49; COV48 row 11.5, an UNDERCOUNT before: PoB parses it to nothing,
# ModCache 7440 {nil, ...}, though it applies the same belt's "You lose 5% of maximum Energy Shield per second"). Soul
# Tether's "Excess Life Recovery from Leech is applied to Energy Shield", stat life_leech_excess_goes_to_energy_shield
# (checked: its users over Mods, PassiveSkills, SoulCoreStats and GrantedEffectStatSets, game 26650308: one Mods row,
# UniqueLifeLeechExcessToEnergyShield1; second source poe2db's Soul Tether, version 0.1.0, and PoB's Uniques/belt.lua
# 257. The PoE1 stat life_leech_applies_to_energy_shield_instead_on_full_life has no PoE2 Mods / passive user, same
# check.) KeywordPopups LifeLeech: "all Life Leech is removed when Life is filled". Whether the line keeps Leech running
# at full Life (so it fills Energy Shield the whole time you're on Full Life) or only passes on what's left of the leech
# when Life fills is NOT STATED (poe2db gives the line alone; UNVERIFIED, a RESEARCH question). Two readings:
#   - the headline, the lower one (the rule for a defence): only the leech left when Life fills, half a second of the
#     kept leech rate (a leech lasts one second, met halfway on average: LOW) a refill, one refill per boss hit that
#     reaches Life (none on Full Life all fight or never full: 0);
#   - the upper one: the kept leech rate x the share of the fight on Full Life (full_life_share), a row in the note.
# Both at most the kept leech (PoB's LifeLeechRate less the boss's Leech resistance). Energy Shield recovery-rate
# modifiers not applied (UNVERIFIED).
LEECH_EXCESS_ES = re.compile(r"^excess life recovery from leech is applied to energy shield$", re.I)
LEECH_EXCESS_HALF = 0.5


def leech_excess_income(income: Income, stats: dict, texts: list[str]) -> list[dict]:
    """Soul Tether's excess Life Leech as Energy Shield (the note above) into EnergyShieldLeechExcess; its row, or [].
    `stats`: full_life_share's, with LifeLeechRate. The refills a second: the boss's landed hits a second x the share
    of their damage that reaches Life (life_flows: after Energy Shield's own refill; LOW)."""
    from builder.search import objective

    if not any(LEECH_EXCESS_ES.match(t.strip()) for t in texts):
        return []
    taken, hits, _ = objective.boss_taken(stats)
    total = sum(taken.values())
    refills = hits * min(1.0, life_flows(stats)[1] / total) if total > 0 else 0.0
    kept = max(_num(stats.get("LifeLeechRate")) + income.rates.get(LEECH_RESISTED["Life"], 0.0), 0.0)
    if kept <= 0:
        income.parts.append("Excess Life Leech applied to Energy Shield: 0, the build Leeches no Life")
        return []
    full, full_how = full_life_share(stats)
    upper = kept * full
    lower = min(kept * LEECH_EXCESS_HALF * max(refills, 0.0), upper) if 0 < full < 1 else 0.0
    if lower > 0:
        income.add("EnergyShieldLeechExcess", lower,
                   f"excess Life Leech applied to Energy Shield: {LEECH_EXCESS_HALF:g} s of {kept:,.1f} Life Leech/s a "
                   f"refill x {refills:.3f} refills/s (the lower reading, LOW)")
    return [{"label": "Excess Life Leech applied to Energy Shield*",
             "value": f"{lower:,.1f} Energy Shield/s (if Leech keeps running at full Life: {upper:,.1f}/s)",
             "basis": (f"Life Leech kept {kept:,.1f}/s (after the boss's Leech resistance). Headline: only the leech left "
                       f"when Life fills, half a second of it a refill at {refills:.3f} boss hits reaching Life a second"
                       + (" (on Full Life all fight or never: no refill, 0)" if not 0 < full < 1 else "")
                       + f". Other reading: Leech keeps running on Full Life and fills Energy Shield then, {full:.0%} of "
                       f"the fight ({full_how}). Which one the game does is not stated (KeywordPopups: \"all Life Leech is "
                       "removed when Life is filled\"; poe2db gives the line alone): UNVERIFIED. Energy Shield "
                       "recovery-rate modifiers not applied"),
             "confidence": "LOW (which reading: UNVERIFIED; half a second a refill)"}]


# Overflow (MECHC49; COV48 rows 10.1 / 11.2 / 7.3 / 5.5, plans/kw-overflow.md; UNDERCOUNTS: PoB parses each line to
# nothing, ModCache 8477-8478, 8490, 8560-8562). KeywordPopups Overflow: "Recovery which Overflows its maximum can be
# recovered up to 1.5 times that maximum." The carriers (checked: Stats ids holding overflow / overcap / excess over
# Mods, PassiveSkills, SoulCoreStats and GrantedEffectStatSets, game 26650308, and the plan's stat.py sweep; second
# source PoB's Uniques / ModItem / LegionPassives):
#   - "Life Leech can Overflow Maximum Life": Couture of Crimson (UniqueLifeLeechOvercapLife1) and the Amanamu glove
#     suffix (HandWrapsAbyssModGlovesAmanamuSuffixPercentOfLifeLeechInstant, ModItem 2143);
#   - "Life Recovery from Flasks can Overflow Maximum Life": The Gnashing Sash (UniqueLifeFlasksOvercapLife1);
#   - "Mana Recovery from Flasks can Overflow maximum Mana during Effect": Uhtred's Chalice;
#   - "Mana Recovery from Regeneration Overflows maximum Mana": Kurgal's Ambition (a Timeless jewel keystone; PoB
#     applies its "50% less Mana Regeneration Rate" half, not this one);
#   - Runic Ward's overflow belt implicit is MECH-B's (models.ward_plan), not here.
# The plan's handling: the floors keep the un-overflowed pool (the worst case, between refills); each carrier gets a
# row. Above the maximum only the overflowing source can push the pool up, and the build's drains (the boss's landed
# hits on that pool, its costs on it, its own steady loss) come off it: with the source giving I a second against D a
# second of drains, the pool climbs at I - D, to 1.5 x maximum in 0.5 x maximum / (I - D) s; a flask's source runs for
# its effect only (the climb stops when it ends, then the drains bring the pool back down). RESEARCH39 R39-17 (MEDIUM):
# overflowed Life doesn't decay (no decay stat), damage takes the overflow first, and regeneration and other flasks don't
# refill it (only the overflowing source does: the climb above); a build with Life overflow counts as on Full Life while
# no Life is Reserved. Whether Leech is still "removed when Life is filled" at the maximum or at 1.5x: UNVERIFIED (LOW).
# Moves no number (the plan's floors rule): a play-guide row.
OVERFLOW_CAP = 1.5
OVERFLOW_LINES = {
    "leech": re.compile(r"^life leech can overflow maximum life$", re.I),
    "life_flask": re.compile(r"^life recovery from flasks can overflow maximum life$", re.I),
    "mana_flask": re.compile(r"^mana recovery from flasks can overflow maximum mana during effect$", re.I),
    "mana_regen": re.compile(r"^mana recovery from regeneration overflows maximum mana$", re.I),
}


def overflow_kinds(texts: list[str]) -> set[str]:
    """The OVERFLOW_LINES kinds the build's lines carry."""
    return {k for k, pattern in OVERFLOW_LINES.items() if any(pattern.match(t.strip()) for t in texts)}


def overflow_row(pool: str, source: str, inflow: float, drains: float, maximum: float, drains_how: str,
                 window: float | None = None) -> dict:
    """The overflow row for one carrier (the note above): `inflow` the overflowing source a second, `drains` what comes
    off the pool a second, `maximum` the pool's maximum; `window`: the seconds the source runs (a flask's effect), None
    for a steady source."""
    room = (OVERFLOW_CAP - 1) * max(maximum, 0.0)
    net = inflow - drains
    if room <= 0 or inflow <= 0:
        value = f"none (no {source.lower()} a second)" if room > 0 else f"none (no maximum {pool})"
        held = 0.0
    elif net <= 0:
        value = f"none held: {source.lower()} {inflow:,.1f}/s doesn't outpace the drains {drains:,.1f}/s"
        held = 0.0
    else:
        fill = room / net
        held = room if window is None or window >= fill else net * window
        value = (f"up to {maximum + held:,.0f} {pool} ({held:,.0f} above the maximum {maximum:,.0f}"
                 + (f", reached in {fill:.1f} s" if held >= room else f" by the end of the {window:.1f} s effect") + ")")
    return {"label": f"{pool} Overflow from {source}*", "value": value,
            "basis": (f"{source} {inflow:,.1f} {pool}/s can pass the maximum, up to {OVERFLOW_CAP:g}x (KeywordPopups "
                      f"Overflow); the drains on it {drains:,.1f}/s ({drains_how})"
                      + (f"; the source runs {window:.1f} s a use (the flask's effect)" if window is not None else "")
                      + ". The defence floors keep your maximum (the worst case, between refills: plans/kw-overflow.md). "
                      "Overflow doesn't decay; damage takes it first; only this source refills it (R39-17, MEDIUM)"),
            "play": (f"keep the {source.lower()} running to sit above your maximum {pool} (with it: the row above; "
                     f"without: your maximum)") if held > 0 else "",
            "confidence": "LOW-MEDIUM (the steady climb; no decay: R39-17, MEDIUM; Leech's stop at full: UNVERIFIED)"}


# ---------------------------------------------------------------------------------------------------------------------
# On Full Life (MECHC27; PATCH_CHECK MC-4 and MECH75's Full-Life penalties). PoB's "Are you always on Full Life?" box
# (conditionFullLife, ConfigOptions 136) is never set, so "while / when on Full Life" lines (Initiative, Killer Instinct,
# High Alert, Foxshade, Pain Attunement's 30% less Critical Damage Bonus, Zealot's Oath's "Excess Life Recovery from
# Regeneration is applied to Energy Shield": ModParser 1801 / 2421) counted never and "while not on Full Life" lines (the
# faster Energy Shield Recharge starts, ModParser 1803) all fight. PoB sets FullLife itself only for Chaos Inoculation
# (CalcDefence 124-126: Life becomes 1).
# The share of the fight you're NOT on Full Life, from the build: Life comes off in dips (the boss's landed hits on Life,
# the main skill's Life costs) and the recovery left after any steady loss (PoB's regeneration, leech, on-hit; recoup and
# gains on Block; the search's flasks and recovery.FIELDS' sources; Life Recharge at its share) refills them. With dips
# of D Life a second refilled at R a second, Life is below full D / R of the time, whatever the dips' timing (the busy
# share of a refill that runs only while Life is missing: work in = work out). A steady loss below R (a degen you
# regenerate through) leaves you on Full Life, so it comes off R instead; at or above R you're never on Full Life again.
# Energy Shield takes the boss's hits first but for PoB's EnergyShieldBypass share; what ES's own recovery (regeneration,
# leech, on-hit, recoup, recharge at its share) doesn't refill spills onto Life. Mind over Matter's share of what reaches
# Life goes to Mana (sharedMindOverMatter). The boss's hits at PoB's scored cadence (enemySkillTime, the defence pace;
# objective.boss_taken: after evade, block and Deflection). MEDIUM: dips are taken as refilled at the average rate (a
# hit bigger than the ES left spills at once: a lower reading of Life's dips); "considered on Full Life at N% Life"
# (FullLifePercentage under 100) isn't read (each dip smaller than the slack would count as Full Life: a lower reading).
def life_flows(stats: dict) -> tuple[float, float, float, float, list[str]]:
    """(MECHC28, out of full_life_share) (Life recovery a second, the boss's landed hits that reach Life a second, Life
    costs a second, the build's own steady Life loss a second, how) from PoB's recovery outputs with the search's added
    recovery fields. Recovery: the RECOVERY_POOLS Life fields (PoB's LifeRegenRecovery already nets its Life degens),
    recoup, Block gains and Life Recharge."""
    from builder.search import objective

    taken, hits, _ = objective.boss_taken(stats)
    es = _num(stats.get("EnergyShield")) > 0
    to_es = to_life = 0.0
    for t, value in taken.items():
        bypass = min(max(_num(stats.get(f"{t}EnergyShieldBypass")), 0.0), 100.0) / 100 if es else 1.0
        to_es += value * (1 - bypass)
        to_life += value * bypass
    es_fields, _ = objective.RECOVERY_POOLS["EnergyShield"]
    es_back = (sum(_num(stats.get(f)) for f in es_fields) + objective.recoup(stats, taken, "EnergyShield")
               + objective.es_recharge(stats, hits)[0]) if to_es > 0 else 0.0
    spill = max(to_es - es_back, 0.0)
    mom = min(max(_num(stats.get("sharedMindOverMatter")), 0.0), 100.0) / 100
    hit_life = (to_life + spill) * (1 - mom)
    cost = max(_num(stats.get("LifePerSecondCost")), 0.0)
    steady = objective.own_loss(stats)["Life"]
    life_fields, _ = objective.RECOVERY_POOLS["Life"]
    back = (sum(_num(stats.get(f)) for f in life_fields) + objective.recoup(stats, taken, "Life")
            + objective.on_block(stats, "Life") + objective.life_recharge(stats, hits)[0])
    parts = [f"{hit_life:,.1f} Life/s from the boss's landed hits"
             + (f" (after Energy Shield: {spill:,.1f}/s it doesn't refill spills over)" if es else "")
             + (f", {mom:.0%} of it to Mana (Mind over Matter)" if mom > 0 else ""),
             *([f"{cost:,.1f}/s of Life costs"] if cost > 0 else [])]
    return back, hit_life, cost, steady, parts


def full_life_share(stats: dict) -> tuple[float, str]:
    """(share of the fight on Full Life, how). `stats`: PoB's outputs objective.RECOVERY_INPUTS names, Life,
    LifePerSecondCost, ChaosInoculation, with the search's added recovery fields."""
    if stats.get("ChaosInoculation") and _num(stats.get("ChaosInoculation")) > 0:
        return 1.0, "Chaos Inoculation: maximum Life 1, so PoB counts you on Full Life all fight"
    back, hit_life, cost, steady, parts = life_flows(stats)
    dips = hit_life + cost
    if dips <= 0 and steady <= 0:
        return 1.0, "nothing takes Life off: no landed boss hit reaches Life, no Life cost or loss (on Full Life all fight)"
    net = back - steady
    if net <= 0:
        return 0.0, (f"{'; '.join(parts)}; your Life recovery ({back:,.1f}/s) doesn't outpace your own steady loss "
                     f"({steady:,.1f}/s): never back on Full Life")
    below = min(1.0, dips / net)
    return 1 - below, (f"{'; '.join(parts)}, refilled at {net:,.1f} Life/s"
                       + (f" (your {back:,.1f}/s recovery less {steady:,.1f}/s of your own steady loss)" if steady > 0
                          else "") + f": below full {below:.0%} of the fight, so on Full Life {1 - below:.0%} (MEDIUM)")


# A pool's whole refill rate (MECHC27; for COV-A's Force of Will, COV31's mana_missing_tens, which read PoB's
# ManaRegenRecovery alone: an OVERCOUNT of missing Mana for a build with Mana flasks, leech or recoup): what the recovery
# floor counts for the pool (objective.RECOVERY_POOLS: regeneration, leech after the boss's Leech resistance, on-hit, the
# search's flasks and recovery.FIELDS' sources), its recoup of the boss's landed hits and its gains on Block. Costs are
# not taken off (the caller counts its own spending). Energy Shield Recharge isn't in it (it waits for a delay: objective.
# es_recharge). `stats` as full_life_share's, with the search's added recovery fields (Build.added_stats).
def refill_rate(stats: dict, pool: str = "Mana") -> tuple[float, str]:
    """(the pool's recovery a second, how) for "Life", "Mana" or "EnergyShield"."""
    from builder.search import objective

    fields, _ = objective.RECOVERY_POOLS[pool]
    taken = objective.boss_taken(stats)[0]
    parts = {f: _num(stats.get(f)) for f in fields if _num(stats.get(f))}
    back, block = objective.recoup(stats, taken, pool), objective.on_block(stats, pool)
    total = sum(parts.values()) + back + block
    named = [f"{f} {v:,.1f}" for f, v in parts.items()] + ([f"recoup {back:,.1f}"] if back else []) + (
        [f"on Block {block:,.1f}"] if block else [])
    return total, f"{pool} refilled at {total:,.1f}/s ({', '.join(named) or 'no source'})"


# On Full Energy Shield (MECHC27; PATCH_CHECK MC-5): PoB's "Are you always on Full Energy Shield?" box
# (conditionFullEnergyShield, ConfigOptions 148) is never set, so Mysticism I / II's 30% / 40% increased Spell damage
# (sup_int.lua 7166-7208), "increased Spell Damage while on Full Energy Shield" and Dreamcatcher's lines counted never,
# and "increased Damage taken while on Full Energy Shield" never either. The share the same way as Full Life's: the boss's
# landed hits Energy Shield takes (all but PoB's EnergyShieldBypass share) as dips, refilled at ES's own recovery
# (regeneration, leech, on-hit, recoup, the added sources and Recharge at the share of the fight it runs between the
# boss's hits: objective.es_recharge), after any steady own loss on ES (objective.own_loss). No Energy Shield: the box
# stays off, as PoB has it (whether 0 of 0 counts as full in game: UNVERIFIED). MEDIUM.
def _es_flows(stats: dict) -> tuple[float, float, float, float]:
    """(the boss's landed hits on Energy Shield a second, its recovery a second, Recharge's part of it, your own steady
    loss on it a second): full_es_share's pieces."""
    from builder.search import objective

    taken, hits, _ = objective.boss_taken(stats)
    dips = sum(v * (1 - min(max(_num(stats.get(f"{t}EnergyShieldBypass")), 0.0), 100.0) / 100) for t, v in taken.items())
    fields, _ = objective.RECOVERY_POOLS["EnergyShield"]
    recharged = objective.es_recharge(stats, hits)[0]
    back = sum(_num(stats.get(f)) for f in fields) + objective.recoup(stats, taken, "EnergyShield") + recharged
    return dips, back, recharged, objective.own_loss(stats)["EnergyShield"]


def full_es_share(stats: dict) -> tuple[float, str]:
    """(share of the fight on Full Energy Shield, how); stats as full_life_share's."""
    if _num(stats.get("EnergyShield")) <= 0:
        return 0.0, "no Energy Shield: the box stays off, as PoB has it (UNVERIFIED whether 0 of 0 counts as full)"
    if _num(stats.get(EVERLASTING_FIELD)) > 0:  # (MECHC36: Everlasting Sacrifice empties it the moment it's full)
        return 0.0, ("Everlasting Sacrifice: you Sacrifice all Energy Shield the moment it's full, so never on Full Energy "
                     "Shield for longer than an instant (without the shield: the share from your recovery)")
    dips, back, recharged, steady = _es_flows(stats)
    if dips <= 0 and steady <= 0:
        return 1.0, "no landed boss hit reaches Energy Shield and nothing of yours drains it: on Full Energy Shield all fight"
    net = back - steady
    if net <= 0:
        return 0.0, (f"{dips:,.1f} Energy Shield/s from the boss's landed hits; its recovery ({back:,.1f}/s, Recharge "
                     f"{recharged:,.1f}/s of it) doesn't outpace your own steady loss ({steady:,.1f}/s): never full again")
    below = min(1.0, dips / net)
    return 1 - below, (f"{dips:,.1f} Energy Shield/s from the boss's landed hits, refilled at {net:,.1f}/s (Recharge "
                       f"{recharged:,.1f}/s of it, at the share of the fight it runs between hits): below full {below:.0%} "
                       f"of the fight, so on Full Energy Shield {1 - below:.0%} (MEDIUM)")


# Everlasting Sacrifice (MECHC36; PATCH_CHECK MB-14), Mahuxotl's Machination's line. KeywordPopups EverlastingSacrifice:
# "When you reach full Energy Shield, Sacrifice all Energy Shield to gain +5% to all Maximum Resistances for 4 seconds";
# KeywordPopups Sacrifice: a loss that "does not count as taking damage" (so it doesn't hold Energy Shield Recharge off).
# (checked: the only Stats id with "everlasting", keystone_everlasting_sacrifice, is on one Mods row,
# UniqueEverlastingSacrifice1, no PassiveSkills / SoulCoreStats row, game 26650308; PoB-PoE2: Mahuxotl's Machination's
# {variant:2,3} line only, Data/Uniques/shield.lua 388; the patch notes name neither: no hit is not absence.) PoB sets
# Condition:EverlastingSacrifice from the line (ModCache 7404) but its "+5% to all maximum resistances" box
# (ConfigOptions 1612) is never set, and its hit pools count all the Energy Shield: an UNDERCOUNT of the resistances and an
# OVERCOUNT of the pools. Here, from the build's own Energy Shield flows (full_es_share's pieces): it refills from 0 at
# its recovery less the boss's landed hits on it and your own steady loss (g a second); with g > 0 a cycle lasts ES / g
# s, the buff (keystone_everlasting_sacrifice_resistance, Buff category: "Buffs on you expire slower" stretches it) is up
# min(1, 4 s x expiry / cycle) of the fight, and Energy Shield climbs from 0 to full each cycle, so a boss hit meets half
# of it on average (a linear refill, MEDIUM). With g <= 0 the opening Sacrifice (you start full) is never refilled: no
# buff after it, and no Energy Shield held (the fluid reading's edge; MEDIUM-LOW). The bridge (setEverlastingSacrifice)
# takes the Energy Shield not held out of every hit pool under PoB's condition, so a what-if without the shield keeps its
# full pool; the +5% goes on PoB as ElementalResistMax / ChaosResistMax BASE 5 x uptime under the same condition.
# Not counted: ES-based "while on Low / below half Energy Shield" lines other than Full Energy Shield (the Unleashed's
# bypass below half reads the pool from full: named).
EVERLASTING = re.compile(r"^Everlasting Sacrifice$", re.I)
EVERLASTING_FIELD = "EverlastingSacrifice"  # (the bridge's output: 1 with the line equipped)
EVERLASTING_SECONDS, EVERLASTING_RES = 4.0, 5.0


def everlasting_sacrifice(stats: dict, expiry: tuple[float, str] | None = None) -> tuple[float, float, str]:
    """(share of the fight the +5% maximum resistances are up, the share of maximum Energy Shield a boss hit meets on
    average, how). `stats` as full_es_share's; `expiry`: conditions.buff_expiry's (factor, how)."""
    es = _num(stats.get("EnergyShield"))
    if es <= 0:
        return 0.0, 1.0, ("no Energy Shield: never full, so never Sacrificed (UNVERIFIED whether 0 of 0 counts as full; "
                          "PoB's pools stand)")
    dips, back, recharged, steady = _es_flows(stats)
    factor, expiry_how = expiry or (1.0, "")
    seconds = EVERLASTING_SECONDS * factor
    gain = back - steady - dips
    flows = (f"Energy Shield recovers {back:,.1f}/s (Recharge {recharged:,.1f}/s of it, at the share of the fight it runs "
             f"between hits) against the boss's landed hits on it {dips:,.1f}/s"
             + (f" and your own steady loss {steady:,.1f}/s" if steady > 0 else ""))
    if gain <= 0:
        return 0.0, 0.0, (f"{flows}: after the opening Sacrifice (you start full) it never refills to full, so no buff "
                          f"after the first {seconds:g} s and no Energy Shield left to meet the boss's hits (the edge of "
                          "a steady-flow reading, MEDIUM-LOW; without the shield: PoB's full pool)")
    cycle = es / gain
    up = min(1.0, seconds / cycle)
    return up, 0.5, (f"{flows}: it refills from 0 to {es:,.0f} in {cycle:.1f} s, then is Sacrificed again; +5% to all "
                     f"maximum resistances for {seconds:.3g} s"
                     + (f" ({expiry_how})" if factor != 1.0 else "")
                     + f" of each cycle, up {up:.0%} of the fight; a boss hit meets half the Energy Shield on average "
                     "(a linear refill, MEDIUM)")


# Ghost Dance (MECHC27; PATCH_CHECK H1-9's rest): "Regenerate Energy Shield equal to 2% of your Evasion Rating per second
# if you have lost a Ghost Shroud Recently" (GhostDancePlayer: skill_base_ghost_dance_grants_%_evasion_as_es_regeneration_
# per_minute_if_have_lost_ghost_dance_shroud_recently 120; patch 0.5.0, the newest entry: "When Hit, lose a Ghost Shroud",
# a Shroud every 11.7-10.1 s at gem levels 4-20, at most 3, "Modifiers to Cooldown Recovery Rate also apply"). PoB has
# only its "Do you use Ghost Shrouds?" box and no regeneration (ConfigOptions 854; CalcPerform 978): an UNDERCOUNT
# before. Shrouds are lost to the boss's landed hits at the "when hit" benefit pace (boss_hits.gain_hits, h a second):
# with hits at least as fast as Shrouds come (g = 1 / interval), each new Shroud is lost at the next hit, so a loss every
# 1 / g s and "Recently" (4 s) covers min(1, 4 g) of the fight; with slower hits a Shroud is always waiting, so every hit
# is a loss and the share is 1 - e^(-4 h) (Poisson hits). ES a second = 2% x Evasion x that share. MEDIUM. The interval
# over the skill's Cooldown Recovery Rate (MECHC51, GEM31's route: "Modifiers to Cooldown Recovery Rate also apply";
# the bridge's activeSkills cooldownRecovery, PoB's rate multiplier; an UNDERCOUNT before). Not read: regeneration-rate
# modifiers on the amount (taken as written, as the other sources here).
GHOST_DANCE = "Ghost Dance"
GHOST_INTERVAL_STAT = "base_cooldown_modifiable_repeat_interval_ms"
GHOST_REGEN_STAT = "skill_base_ghost_dance_grants_%_evasion_as_es_regeneration_per_minute_if_have_lost_ghost_dance_shroud_recently"


# (MECHC61; GEM41's note, the manager's LOW item) Ghost Dance holds up to 3 Shrouds ("at most 3", GhostDancePlayer's
# ghost_dance_max_stacks 3). The two closed forms above (each Shroud lost at the next hit; a Shroud always waiting) are
# the ends; between them, and with the boss's hits bunched at random, the stock decides how many hits find a Shroud and
# how the losses spread over the fight. ghost_dance_share now averages it: a seeded simulation of Poisson landed hits
# against a Shroud every `interval` s (the timer taken to run on while 3 are held, the next Shroud then lost: the lower
# reading, UNVERIFIED), the share of time with a loss in the past `recently` s. MEDIUM. The closed forms are the rows.
GHOST_STOCK = 3


def ghost_dance_share(interval: float, hits: float, recently: float = 4.0) -> float:
    """Share of the fight you've lost a Ghost Shroud in the past `recently` s: a Shroud every `interval` s (at most
    GHOST_STOCK held), landed hits `hits` a second (GHOST_STOCK's note)."""
    if interval <= 0 or hits <= 0:
        return 0.0
    return _ghost_cover(round(interval, 3), round(hits, 4), recently)


@functools.lru_cache(maxsize=512)
def _ghost_cover(interval: float, hits: float, recently: float, shrouds: int = 3000) -> float:
    import random

    rng = random.Random(61)
    warm = 20 * interval
    end = warm + shrouds * interval
    stock, made, t, covered, last_end = GHOST_STOCK, 1, 0.0, 0.0, warm
    while True:
        t += rng.expovariate(hits)
        if t >= end:
            break
        due = int(t / interval)  # (Shrouds made by now, the timer running throughout)
        if due >= made:
            stock = min(GHOST_STOCK, stock + due - made + 1)
            made = due + 1
        if stock <= 0:
            continue
        stock -= 1
        if t + recently > warm:
            start = max(t, last_end, warm)
            stop = min(t + recently, end)
            if stop > start:
                covered += stop - start
            last_end = max(last_end, t + recently)
    return min(1.0, covered / (end - warm))


def ghost_dance_income(income: Income, level_stats: dict, evasion: float, hits: float, hits_how: str,
                       recovery_rate: float = 1.0) -> None:
    """Ghost Dance's Energy Shield regeneration a second into EnergyShieldGhostDanceRegen. `recovery_rate`: the skill's
    Cooldown Recovery Rate multiplier (1 = none)."""
    interval = _num(level_stats.get(GHOST_INTERVAL_STAT)) / 1000 / (recovery_rate if recovery_rate > 0 else 1.0)
    pct = _num(level_stats.get(GHOST_REGEN_STAT)) / 60 or 2.0
    share = ghost_dance_share(interval, hits)
    income.add("EnergyShieldGhostDanceRegen", pct / 100 * max(evasion, 0.0) * share,
               f"Ghost Dance: {pct:g}% of {evasion:,.0f} Evasion as Energy Shield a second while a Shroud was lost in the "
               f"past 4 s, {share:.0%} of the fight (a Shroud every {interval:.1f} s; {hits_how}; MEDIUM)")


# Time of Need (MECHC51; GEM31's route, an UNDERCOUNT: no model before, PoB maps only its alternate quality's Life
# Regeneration rate): "Blessing recovers {0} Life and removes Curses and Elemental Ailments", "{0} seconds between
# Blessings", "Modifiers to Cooldown Recovery Rate also apply to Blessing frequency" (time_of_need.csd; TimeOfNeedPlayer
# skill_time_of_need_gain_x_life 628 and base_cooldown_modifiable_repeat_interval_ms 8100 at level 20, PoB act_str.lua).
# Life a second = the Blessing's Life / (the interval over the skill's Cooldown Recovery Rate), as an on-event recovery
# (Life recovery-rate modifiers not applied, taken as written). Its removal of Curses and Elemental Ailments on you isn't
# counted here (the ailments-on-you readings keep their own removal rows; an undercount of avoidance, named).
TIME_OF_NEED = "Time of Need"
TIME_OF_NEED_LIFE = "skill_time_of_need_gain_x_life"


def time_of_need_income(income: Income, level_stats: dict, recovery_rate: float = 1.0) -> None:
    """Time of Need's Blessing Life a second into LifeOnEventRecovery."""
    interval = _num(level_stats.get(GHOST_INTERVAL_STAT)) / 1000 / (recovery_rate if recovery_rate > 0 else 1.0)
    life = _num(level_stats.get(TIME_OF_NEED_LIFE))
    if interval > 0 and life > 0:
        income.add("LifeOnEventRecovery", life / interval,
                   f"Time of Need: a Blessing of {life:,.0f} Life every {interval:.1f} s (its Cooldown Recovery "
                   f"x{recovery_rate:.2f}; MEDIUM)")


# Eldritch Empowerment's sacrifice (MECHC27; MECHB30's 7622977 models its 30% more Spell damage and left this for MECH-C:
# an OVERCOUNT of defence): "Sacrifice 5% of maximum Energy Shield when you Cast a Spell" (the Lich notable, PoB tree 0_5
# node 58932; "Sacrificing Energy Shield does not interrupt Recharge") takes casts x 5% x maximum ES a second off the ES
# pool, whether or not the sacrifice is fully made (a partial one takes what's there). A NEGATIVE recovery field
# (EnergyShieldSacrificed) the recovery floor nets off ES's recovery (objective.boss_recovery clamps each pool at 0).
# Casts: the main skill's when it is a Spell (as MECHB30's model counts them; other spells' and triggered casts aren't:
# an undercount of the drain, the trigger question UNVERIFIED as there). The pool's average level below its maximum (the
# EHP / maximum-hit floors read PoB's full pool) is SEARCH's objective: said in the report.
SACRIFICE_ES = re.compile(r"sacrifice (\d+(?:\.\d+)?)% of maximum energy shield when you cast a spell", re.I)


def sacrifice_income(income: Income, texts: list[str], casts: float, energy_shield: float, how: str) -> None:
    """Eldritch Empowerment's ES sacrificed a second into EnergyShieldSacrificed (negative)."""
    pct = sum(float(m.group(1)) for t in texts for m in SACRIFICE_ES.finditer(t))
    drain = pct / 100 * max(energy_shield, 0.0) * max(casts, 0.0)
    if drain > 0:
        income.rates["EnergyShieldSacrificed"] = -drain
        income.parts.append(f"Energy Shield sacrificed: {pct:g}% of {energy_shield:,.0f} a cast x {how}: "
                            f"-{drain:,.1f}/s (Eldritch Empowerment)")


# Demon Form (MECHC28; PATCH_CHECK S2-4 / K1-o4): "You gain Demonflame every second you remain in demon form, causing your
# Life to be lost at an ever-increasing rate. Maximum 10 Demonflame. Revert to human form if you reach 1 Life, use a Skill
# that isn't a Spell, or reactivate this Skill" (PoB other.lua DemonFormPlayer; 0.2.0: "a maximum Demonflame of 10
# (previously uncapped). You now lose a flat amount of Life per second per Demonflame"; 0.3.0: "3% more Spell Damage per
# Demonflame"; Mastered Darkness: "Demonflame has no maximum", 0.2.0; grep_notes --history "Demon Form", newest 0.4.0, a
# Second Wind fix). Its cooldown doesn't tick in the form (demon_transformation_cooldown_does_not_tick), so a stint ends,
# then the cooldown runs. PoB keeps you in the form all fight at its placeholder 10 Demonflame (ConfigOptions 356-361,
# ConfigTab 1186) and nets the 10 stacks' Life loss into LifeRegenRecovery (CalcDefence 1746-1752).
DEMON_STEP = 0.05  # (s: the integration step)
DEMON_HORIZON = 900.0  # (s: a stint this long is taken as kept all fight)


def demon_form_cycle(life: float, net: float, per_stack: float, cap: float | None, cooldown: float,
                     limit: float = math.inf) -> tuple[float, float, float, str]:
    """(share of the fight in the form, average Demonflame while in it, seconds a stint, how) for a pool of `life`
    maximum Life gaining `net` Life a second out of the form (recovery less the boss's landed hits, costs and the
    build's own steady loss), losing `per_stack` Life a second per Demonflame (one gained each whole second in the form,
    at most `cap`; None: no maximum). A stint ends at 1 Life (the game reverts you) or at `limit` s (a non-Spell skill
    pressed then); the cooldown then runs out of the form, Life refilling at `net` (capped at full), and you enter again
    as it ends (the minimum of 2 Life to enter). The cycle repeats: the steady one is counted (entered at the Life the
    cooldown refills), not the first from full Life."""
    if life < 2 or per_stack <= 0:
        return 0.0, 0.0, 0.0, "under 2 maximum Life: Demon Form can't be entered" if life < 2 else "no Life loss read"
    if cap is not None and net >= per_stack * cap and limit == math.inf:
        return 1.0, float(cap), math.inf, (f"your {net:,.1f} Life/s outpaces the {per_stack * cap:,.1f}/s the "
                                           f"{cap:g} Demonflame take: kept in the form all fight")
    entry = life if net * cooldown >= life - 1 else max(1.0, 1.0 + max(net, 0.0) * cooldown)
    if entry < 2:
        return 0.0, 0.0, 0.0, (f"Life doesn't refill between stints ({net:,.1f}/s out of the form): "
                               "Demon Form can't be kept up")
    held, t, area = entry, 0.0, 0.0
    while held > 1 and t < min(limit, DEMON_HORIZON):
        stacks = math.floor(t + 1e-9)
        stacks = min(stacks, cap) if cap is not None else stacks
        held = min(life, held + (net - per_stack * stacks) * DEMON_STEP)
        area += stacks * DEMON_STEP
        t += DEMON_STEP
    if t >= DEMON_HORIZON:
        return 1.0, area / t, math.inf, f"kept {DEMON_HORIZON:g} s or more: in the form all fight"
    end = "you reach 1 Life and revert" if held <= 1 else f"a non-Spell skill pressed every {limit:.1f} s reverts you"
    share = t / (t + cooldown)
    return share, area / t if t else 0.0, t, (
        f"a stint of {t:.1f} s from {entry:,.0f} Life ({end}), then the {cooldown:g} s cooldown out of the form "
        f"(Life back at {net:,.1f}/s): in the form {share:.0%} of the fight at {area / t if t else 0:.1f} Demonflame on "
        f"average")


# Temporal Rift's reset (MECHC29; the manager, 15 Nov 13:14, from GEM12: "Temporal Rift resets Life, Mana and Energy
# Shield to their values 4 s ago (16 snapshots x 250 ms) ... a recovery effect for your recovery floors: model it with its
# cooldown and the build's use rate (no flat rate)"). The game (ActiveSkills temporal_rift): "Cast the Spell to return to
# the oldest afterimage, teleporting to that location and resetting your Life, Mana and Energy Shield to the values they
# had at the time"; PoB other.lua: temporal_rift_snapshot_interval 250 (the 16 snapshots: the manager's / GEM12's number,
# 4 s = assumptions.PHASED_DELAY); 0.3.0 fixed the reset of overcapped pools; 0.5.0 cast time 0.1 s (grep_notes --history,
# 0.5.0 the newest). In the steady fight a pool whose recovery keeps up with what takes it sits full, now and 4 s ago:
# the reset gives nothing. A pool losing a net d a second is d x 4 s lower than 4 s ago, so each use gives that back:
# used u times a second it cancels min(1, 4 s x u) of the net drain (uses on its cooldown are periodic). The net drains:
# Life's from life_flows (the boss's landed hits after Energy Shield, Life costs, the build's own loss, against Life's
# recovery); Mana's costs (and Mind over Matter's share of hits) against refill_rate; Energy Shield's share of the hits
# against its recovery and Recharge. Timing a use right after a big hit (more than the steady drift) isn't counted (an
# undercount, labelled); nor is a reset that lowers a pool that rose (the player presses it when it helps).
RIFT_SECONDS = 4.0


def rift_income(income: Income, stats: dict, uses: float, how: str) -> None:
    """Temporal Rift's reset as LifeRiftRecovery / ManaRiftRecovery / EnergyShieldRiftRecovery (comment above). `stats`:
    PoB's recovery outputs (objective.RECOVERY_INPUTS, Life, LifePerSecondCost, EnergyShield) with the search's added
    recovery fields; `uses`: Temporal Rift's uses a second."""
    from builder.search import objective

    share = min(1.0, RIFT_SECONDS * max(uses, 0.0))
    if share <= 0:
        return
    back, hit_life, cost, steady, _ = life_flows(stats)
    life = hit_life + cost + steady - back
    taken, hits, _ = objective.boss_taken(stats)
    es_in = 0.0
    if _num(stats.get("EnergyShield")) > 0:
        es_in = sum(v * (1 - min(max(_num(stats.get(f"{t}EnergyShieldBypass")), 0.0), 100.0) / 100) for t, v in taken.items())
    es = es_in - refill_rate(stats, "EnergyShield")[0] - (objective.es_recharge(stats, hits)[0] if es_in > 0 else 0.0)
    mom = min(max(_num(stats.get("sharedMindOverMatter")), 0.0), 100.0) / 100
    mana = max(_num(stats.get("ManaPerSecondCost")), 0.0) + mom * sum(taken.values()) - refill_rate(stats, "Mana")[0]
    for pool, drain in (("Life", life), ("Mana", mana), ("EnergyShield", es)):
        if drain > 0:
            income.add(f"{pool}RiftRecovery", share * drain,
                       f"Temporal Rift ({how}): {share:.0%} of {pool}'s net loss of {drain:,.1f}/s back (reset to {RIFT_SECONDS:g} "
                       f"s ago)")


# Heavy Stun on you (MECHC30; MECHC29's "Item 4"; RESEARCH #59, parry-player-heavy-stun.md). The rules (KeywordPopups
# HeavyStunPlayer, build 26476662): players "can receive Heavy Stun buildup where specifically mentioned while taking
# specific actions (such as raising their Shield, Parrying with a Buckler ...). A player's stun bar only empties when they
# are not taking these actions or Heavily Stunned. While Heavily Stunned players cannot Block, Deflect or Evade." The
# skills whose descriptions mention it: Raise Shield, Resonating Shield (its channel raises the shield and Blocks all
# Blockable hits), Parry, Refutation, Runic Reprieve (ActiveSkills); the five carrying active_skill_does_not_decay_heavy_
# stun_during_action and active_skill_heavy_stun_decay_after_action_delay_ms 2000 are ShieldBlockPlayer,
# ResonatingShieldPlayer, ParryPlayer, HazardousHoldoutPlayer and RunicReprievePlayer (GrantedEffectStatSets). After the
# action the bar waits 2 s, then empties 50% of the bar a second (PoB Data/Misc.lua 207-208 characterConstants, exported
# from the game: poise_decay_delay_ms 2000, poise_decay_%_per_second 50). A player Heavy Stun lasts 3 s (0.2.0 "Heavy Stun
# duration on players is now 3 seconds"; PoB characterConstants base_heavy_stun_duration_ms 3000; --history "Heavy Stun":
# no later change, newest 0.5.1). Buildup per hit (no published formula: poe2wiki and poe2db describe it only as "based on
# the damage of the hit" against the Stun Threshold, MECHC30's search): the game's own constant HeavyStunDamageScalePlayer
# 0.65 (PoB Data/Misc.lua 52, gameConstants exported from GameConstants.dat; the monster twin HeavyStunDamageScale 0.58 is
# what PoB's CalcOffence 5575 multiplies a hit's damage / the target's poise threshold by), so a hit fills 0.65 x its
# damage / your Stun Threshold of the bar (the factor sourced, its use as PoB's monster formula MEDIUM), the boss's
# Physical x2 and its Melee x1.33 (KeywordPopups Stun; recovery.stuns_taken's constants and bosses.melee_share), at each
# type's full hit size after mitigation (PoB's {Type}TakenHit; one type per hit, as stuns_taken: LOW). Evasion "instead
# grants an equal chance to avoid this Heavy Stun buildup" (Raise Shield's and Parry's descriptions; 0.2.0's note).
# Threshold: PoB's StunThreshold (Life, or ES / Mana with their keystones, plus its lines), x0.6 while parrying (ParryPlayer
# stun_threshold_+%_final_while_performing_action -40: very likely 0.3.1's "Reduced the Heavy Stun buildup a player gains
# when Parrying a hit by approximately 30%" itself, as 1/0.6 against 1/0.4 is 33% less, so no extra x0.7: UNVERIFIED which),
# x2 with "Double Stun Threshold while Shield is Raised" (Dionadair) while the shield is raised, x(1 + 30%) with Unbreakable
# (support_stability_heavy_stun_threshold_+%_final_while_performing_action 30) and x(1 + 50%) with Steadfast I / II while
# channelling (support_steadfast_stun_threshold_+%_final_while_channelling 50): PoB maps neither support stat (checked:
# grep of PoB's src finds them only in StatDescriptions and Steadfast's gem data, no SkillStatMap entry), and
# "N% increased Stun Threshold while Parrying" (jewels) as x(1 + N%) (in game it adds to your other increases: a slightly
# high threshold, MEDIUM). Decay modifiers (Defender's Resolve, the Amanamu shield suffix, Resolute Reprisal: all faster)
# change nothing below: a channel never decays, and 5 s between parries already empties the bar at the base rate.
# Not counted yet (named in the notes): Refutation's blocks during its buff, Raise Shield taps, Runic Reprieve's channel,
# Hazardous Holdout. Block and Evasion while channelling: assumptions.heavy_stunned_self.
HEAVY_STUN_SCALE_PLAYER = 0.65
PLAYER_HEAVY_STUN_SECONDS = 3.0
HEAVY_DECAY_DELAY, HEAVY_DECAY_PER_SECOND = 2.0, 0.5
PARRY_STUN_THRESHOLD = 0.6
RAISED_DOUBLE = re.compile(r"^double stun threshold while shield is raised$", re.I)
PARRY_THRESHOLD_INC = re.compile(rf"^{NUM}% increased stun threshold while parrying$", re.I)
UNBREAKABLE_HEAVY = "support_stability_heavy_stun_threshold_+%_final_while_performing_action"
STEADFAST_CHANNEL = "support_steadfast_stun_threshold_+%_final_while_channelling"
HEAVY_STUN_SUPPORT_STATS = (UNBREAKABLE_HEAVY, STEADFAST_CHANNEL)
ACTIVE_BLOCK_CHANNELS = frozenset({"Resonating Shield"})  # a main skill that holds the shield raised all fight
HEAVY_STUN_FIELDS = (*(f"{t}TakenHit" for t in ("Physical", "Fire", "Cold", "Lightning", "Chaos")), "StunThreshold",
                     "EvadeChance", "enemySkillTime",
                     "Armour", "PhysicalDamageReduction", "PhysicalDamageReductionMax")  # (boss_hits.BIGGEST_FIELDS)


def _biggest_row(stats: dict, read, show) -> str:
    """'; <the biggest-hit row>' (MECHC32; the manager's 03:28 ruling) when reading the build's stats with every hit at its
    type's biggest ordinary hit (boss_hits.biggest_reading) moves the figure `read` gives; "" otherwise."""
    from builder.search import boss_hits

    big = boss_hits.biggest_reading(stats)
    if big is None:
        return ""
    head, worse = show(read(stats)), show(read(big))
    return f"; {boss_hits.BIGGEST_LABEL}: {worse}" if worse != head else ""


def heavy_buildups(stats: dict, scale: float = 1.0) -> tuple[list[tuple[float, float]], str]:
    """([(share of the boss's hits, bars of Heavy Stun buildup one such blocked hit gives)], how) against PoB's
    StunThreshold x `scale` (the action's own threshold multipliers)."""
    from builder.search import boss_hits, bosses

    threshold = _num(stats.get("StunThreshold")) * scale
    if threshold <= 0:
        return [], "no Stun Threshold read"
    melee_of = getattr(bosses, "melee_share", None)
    melee, _ = melee_of(stats) if melee_of is not None else (0.0, "")
    melee = min(max(melee, 0.0), 1.0)
    out, parts = [], []
    for t, w in boss_hits.type_shares(boss_hits.boss_name(stats)).items():
        size = max(_num(stats.get(f"{t}TakenHit")), 0.0)
        if w <= 0 or size <= 0:
            continue
        bars = HEAVY_STUN_SCALE_PLAYER * size * (PHYSICAL_STUN_MORE if t == "Physical" else 1.0) / threshold
        out += [(w * melee, bars * MONSTER_MELEE_STUN_MORE), (w * (1 - melee), bars)]
        parts.append(f"{t} {w:.0%} of hits, {bars:.0%} of the bar ({bars * MONSTER_MELEE_STUN_MORE:.0%} if Melee)")
    return [(w, b) for w, b in out if w > 0], (
        f"{'; '.join(parts) or 'no damaging hit'}: 0.65 (the game's HeavyStunDamageScalePlayer) x the hit after mitigation / "
        f"Stun Threshold {threshold:,.0f}, a monster's Physical x2, its Melee x1.33 on {melee:.0%} of hits")


def channel_heavy_stun(stats: dict, scale: float = 1.0) -> tuple[float, float, str]:
    """(share of the fight Heavily Stunned, Heavy Stuns a second, how) for a main skill that channels with the shield raised
    all fight (Resonating Shield): the bar never decays during the channel, so every blocked boss hit adds to it; at a
    full bar 3 s Heavily Stunned (the bar then starts over, as a monster's: MEDIUM), then channelling again."""
    from builder.search import boss_hits

    buildups, how = heavy_buildups(stats, scale)
    unavoidable, evadable = boss_hits.shares(stats)
    every = _num(stats.get("enemySkillTime")) or 0.7
    blockable = max(1 - unavoidable - evadable, 0.0)
    # (the manager, 15 Nov 20:55: "While the shield is raised there's no Evasion: no evaded hits, so every hit builds
    # stun"; Resonating Shield's own text has no "Evasion instead grants an equal chance to avoid" clause, which Raise
    # Shield's and Parry's do)
    blocked = blockable / every
    mean = sum(w * b for w, b in buildups)
    if blocked <= 0 or mean <= 0:
        return 0.0, 0.0, f"no blocked boss hit builds Heavy Stun ({how})"
    hits = max(1, math.ceil(1 / mean - 1e-9))
    fill = hits / blocked
    cycle = fill + PLAYER_HEAVY_STUN_SECONDS
    share = PLAYER_HEAVY_STUN_SECONDS / cycle
    return share, 1 / cycle, (
        f"channelling holds the shield raised and the bar never decays: {blocked:.2f} blocked boss hits a second (the "
        f"{blockable:.0%} that are Blockable, one every {every:g} s; no Evasion while raised, so each builds stun: the "
        f"manager's ruling), {mean:.0%} of the bar each on average ({how}), so a Heavy Stun every {hits} blocked hits ({fill:.1f} s), "
        f"{PLAYER_HEAVY_STUN_SECONDS:g} s each with no attacking, Block or Evasion: {share:.0%} of the fight. Releasing "
        "early to let it decay costs more time (2 s delay + up to 2 s of decay per bar), so not chosen (MEDIUM)")


def parry_heavy_stun(stats: dict, scale: float = PARRY_STUN_THRESHOLD) -> tuple[float, str]:
    """(chance one parry Heavy Stuns you, how): parries come models.PARRY_EVERY (5 s) apart and the bar empties between
    them (the 0.75 s retaliation, the 2 s delay, then at most 2 s at 50% a second), so a parry stuns only when that hit
    alone fills the bar; the parried hit drawn from the boss's hits by type."""
    from builder.search import boss_hits

    buildups, how = heavy_buildups(stats, scale)
    evade, _ = boss_hits.evade_block(stats)
    full = sum(w for w, b in buildups if b >= 1)
    chance = full * (1 - evade)
    return chance, (f"a parried hit Heavy Stuns you when it fills the bar alone ({how}; your threshold x{scale:.2f} while "
                    f"parrying): {full:.0%} of the boss's hits do"
                    + (f", less {evade:.0%} Evasion's chance to avoid the buildup" if evade > 0 else "")
                    + f": {chance:.0%} of parries, {PLAYER_HEAVY_STUN_SECONDS:g} s each with no attacking, Block or "
                    "Evasion; the bar empties between parries 5 s apart (2 s delay, then 50% a second)")


def heavy_stun_scale(texts: list[str], support: dict | None, parry: bool = False, raised: bool = False,
                     channel: bool = False) -> tuple[float, list[str]]:
    """(your Stun Threshold's multiplier during the action, what) from the lines and the action skill's support stats."""
    scale, what = 1.0, []
    if parry:
        scale *= PARRY_STUN_THRESHOLD
        what.append("x0.6 Parry's own -40% final")
        inc = sum(float(m.group(1)) for t in texts if (m := PARRY_THRESHOLD_INC.match(t.strip())))
        if inc:
            scale *= 1 + inc / 100
            what.append(f"x{1 + inc / 100:.2f} from {inc:g}% increased while Parrying")
    if raised and any(RAISED_DOUBLE.match(t.strip()) for t in texts):
        scale *= 2
        what.append("x2 while the shield is raised")
    support = support or {}
    if (u := _num(support.get(UNBREAKABLE_HEAVY))) > 0:
        scale *= 1 + u / 100
        what.append(f"x{1 + u / 100:.2f} Unbreakable")
    if channel and (s := _num(support.get(STEADFAST_CHANNEL))) > 0:
        scale *= 1 + s / 100
        what.append(f"x{1 + s / 100:.2f} Steadfast while channelling")
    return scale, what


def _heavy_stun_reads(build, skill: str) -> tuple[dict, dict, list[str]]:
    """(PoB's stats with the boss named, the action skill's support stats, the build's lines) for the two readers below."""
    boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None)
    stats = dict(build.call("stats", fields=list(HEAVY_STUN_FIELDS)))
    if boss:
        stats["_boss"] = boss
    raw = build.call("activeSkills", supportStats=list(HEAVY_STUN_SUPPORT_STATS))["skills"]
    found = next((s for s in raw if s.get("name") == skill and not s.get("disabled")), None)
    texts = [line["text"].strip() for line in build.call("sourceLines")["lines"]]
    return stats, dict((found or {}).get("supportStats") or {}), texts


def parry_stun_of(build) -> tuple[float, str]:
    """(chance a parry Heavy Stuns you, how) for models.parry_plan, from the build's own Stun Threshold and lines."""
    stats, support, texts = _heavy_stun_reads(build, "Parry")
    scale, what = heavy_stun_scale(texts, support, parry=True)
    chance, how = parry_heavy_stun(stats, scale)
    how += _biggest_row(stats, lambda s: parry_heavy_stun(s, scale)[0], lambda c: f"{c:.0%} of parries")
    return chance, how + f" (threshold: {', '.join(what)})"


def channel_stun_of(build, main: str | None) -> tuple[float, float, str]:
    """(share of the fight Heavily Stunned, Heavy Stuns a second, how) when the main skill is an active-block channel
    (ACTIVE_BLOCK_CHANNELS); (0, 0, "") otherwise."""
    if main not in ACTIVE_BLOCK_CHANNELS:
        return 0.0, 0.0, ""
    stats, support, texts = _heavy_stun_reads(build, main)
    scale, what = heavy_stun_scale(texts, support, raised=True, channel=True)
    share, rate, how = channel_heavy_stun(stats, scale)
    how += _biggest_row(stats, lambda s: channel_heavy_stun(s, scale)[0], lambda v: f"{v:.0%} of the fight")
    return share, rate, how + (f" (threshold: {', '.join(what)})" if what else "")


# Refutation (MECHC30, leftover (b)): "Spends all your Runic Ward to gain a short-duration Buff that causes you to Block all
# Blockable Hits ... This Buff is removed if you are Heavy Stunned. While this Buff is active, you cannot be Light Stunned,
# but Blocking too much damage may Heavy Stun you" (ActiveSkills refutation). RefutationPlayer: base_skill_effect_duration
# 4000, runic_fortress_stun_threshold_+%_final -50 ("50% less Stun Threshold while Buff is active") and
# runic_fortress_stun_threshold_+%_final_per_10_ward_spent 5 ("5% more Stun Threshold while Buff is active per 10 Runic
# Ward spent"; PoB's refutation.lua stat descriptions): the threshold x0.5 x (1 + 5% x Ward / 10), the Ward spent being
# PoB's Ward (all of it; the per-10 count taken as one "more" scaled by the count, as such stats usually read: MEDIUM).
# The buff isn't an action (RefutationPlayer has no active_skill_does_not_decay_heavy_stun_during_action), but the bar
# waits 2 s after a hit before decaying, and the boss's hits come far closer than that, so it builds through the buff.
# After the buff the bar empties (2 s + at most 2 s) long before the next cast (Refutation's cooldown doesn't recover
# during the buff). A stun at t s into the buff ends the buff there and costs 3 s.
REFUTATION_THRESHOLD, REFUTATION_PER_10_WARD = 0.5, 0.05
REFUTATION_BUFF_SECONDS = 4.0
# Raise Shield taps (MECHC34; MECHC30's leftover (c)): Bastion of Light's taps (models.raised_plan: one tap of RAISE_TAP
# 0.55 s per Blind duration, 4 s at base) raise the shield, which Blocks all Blockable hits, "but Blocking too much damage
# may Heavy Stun you. You cannot Evade while your Shield is raised, but Evasion instead grants an equal chance to avoid this
# Heavy Stun buildup" (ActiveSkills shield_block). The bar empties between taps (2 s delay, then 50% a second: empty 4 s
# after a full bar; taps 4 s apart at base), so a tap stuns only on a blocked hit that fills the bar alone (as
# parry_heavy_stun), threshold x2 with Dionadair (heavy_stun_scale raised). Hits within a tap: the Blockable share of the
# boss's hits, one every enemySkillTime, at random (Poisson): P(one or more) = 1 - e^(-0.55 x rate). Each stun costs 3 s
# of the main skill. Not counted: a stunned tap's lost Block and Evasion in the defence floors; shorter Blind durations
# leaving bar between taps (LOW).
RAISED_TAP_SECONDS = 0.55  # (= models.RAISE_TAP)


def raised_tap_heavy_stun(stats: dict, scale: float, taps: float, tap: float = RAISED_TAP_SECONDS) -> tuple[float, str]:
    """(share of the fight Heavily Stunned by Raise Shield taps, how): the comment above RAISED_TAP_SECONDS."""
    from builder.search import boss_hits

    if taps <= 0:
        return 0.0, ""
    buildups, how = heavy_buildups(stats, scale)
    unavoidable, evadable = boss_hits.shares(stats)
    evade, _ = boss_hits.evade_block(stats)
    every = _num(stats.get("enemySkillTime")) or 0.7
    blockable = max(1 - unavoidable - evadable, 0.0)
    caught = 1 - math.exp(-tap * blockable / every)
    full = sum(w for w, b in buildups if b >= 1)
    chance = caught * full * (1 - evade)
    share = min(1.0, taps * chance * PLAYER_HEAVY_STUN_SECONDS)
    return share, (f"each {tap:g} s Raise Shield tap ({taps:.2f} a second) blocks a boss hit {caught:.0%} of the time "
                   f"(the {blockable:.0%} that are Blockable, one hit every {every:g} s, at random); it Heavy Stuns you "
                   f"when that hit fills the bar alone ({how}): {full:.0%} of the boss's hits do"
                   + (f", less {evade:.0%} Evasion's chance to avoid the buildup" if evade > 0 else "")
                   + f": {chance:.1%} of taps, {PLAYER_HEAVY_STUN_SECONDS:g} s each, {share:.1%} of the fight off the main "
                   "skill (MEDIUM-LOW; the bar taken as empty at each tap)")


def raised_tap_stun_of(build, taps: float) -> tuple[float, str]:
    """(share of the fight Heavily Stunned by the build's Raise Shield taps, how) for Assumptions (models.raised_plan)."""
    if taps <= 0:
        return 0.0, ""
    stats, support, texts = _heavy_stun_reads(build, "Raise Shield")
    scale, what = heavy_stun_scale(texts, support, raised=True)
    share, how = raised_tap_heavy_stun(stats, scale, taps)
    how += _biggest_row(stats, lambda s: raised_tap_heavy_stun(s, scale, taps)[0], lambda v: f"{v:.1%} of the fight")
    return share, how + (f" (threshold: {', '.join(what)})" if what else "")


def refutation_heavy_stun(stats: dict, scale: float, buff: float = REFUTATION_BUFF_SECONDS) -> tuple[float, str]:
    """(seconds into Refutation's buff at which its blocks Heavy Stun you, 0.0 when they don't within the buff; how)."""
    from builder.search import boss_hits

    buildups, how = heavy_buildups(stats, scale)
    unavoidable, evadable = boss_hits.shares(stats)
    evade, _ = boss_hits.evade_block(stats)
    every = _num(stats.get("enemySkillTime")) or 0.7
    blocked = max(1 - unavoidable - evadable, 0.0) * (1 - evade) / every
    mean = sum(w * b for w, b in buildups)
    if blocked <= 0 or mean <= 0:
        return 0.0, f"Refutation: no blocked boss hit builds Heavy Stun ({how})"
    hits = max(1, math.ceil(1 / mean - 1e-9))
    at = hits / blocked
    if at >= buff:
        return 0.0, (f"Refutation's blocks fill {mean * blocked * buff:.0%} of the bar in its {buff:g} s buff ({how}; "
                     f"{blocked:.2f} blocked hits a second): no Heavy Stun")
    return at, (f"Refutation's blocks Heavy Stun you {at:.1f} s into its {buff:g} s buff (every {hits} blocked hits, "
                f"{blocked:.2f} a second; {how}): the buff ends there and the stun takes {PLAYER_HEAVY_STUN_SECONDS:g} s")


def refutation_stun_of(build) -> tuple[float, str]:
    """(seconds into Refutation's buff of its Heavy Stun, 0.0 for none; how) for models.parry_plan."""
    stats, support, texts = _heavy_stun_reads(build, "Refutation")
    ward = max(_num(build.call("stats", fields=["Ward"]).get("Ward")), 0.0)
    scale = REFUTATION_THRESHOLD * (1 + REFUTATION_PER_10_WARD * ward / 10)
    at, how = refutation_heavy_stun(stats, scale)
    how += _biggest_row(stats, lambda s: refutation_heavy_stun(s, scale)[0],
                        lambda t: f"Heavy Stun {t:.1f} s into the buff" if t > 0 else "no Heavy Stun in the buff")
    return at, how + (f" (threshold x{scale:.2f}: Refutation's 50% less, 5% more per 10 of the {ward:,.0f} Runic Ward "
                      "spent)")


# A channelled skill on a cooldown pays its per-minute cost only while channelled (MECHC33; GEM15's find via the manager,
# 16 Nov 10:02: "Flameblast's per-minute cost may be charged all fight despite its cooldown", UNVERIFIED until probed).
# PoB turns a per-minute cost straight into <Resource>PerSecondCost (CalcOffence 2300-2316: the PerMinute costs become
# PerSecond, never times a use rate) and charges it all fight, while its damage counts one release per cooldown (Speed =
# 1 / cooldown). The skills it touches: the main skill with SkillTypes Channel and Cooldown and a per-second cost (in
# PoB's Data/Skills today: Flameblast, Flame Breath (Rage per minute), Runic Tempering, Temper Weapon; PoB's data only,
# the game files not searched, but the check reads the skill's types, so any other such skill is covered). Share of the fight
# channelled = PoB's ChannelTime (the stages it channels per release, at the build's cast speed) / the cycle. The cycle:
# the cooldown starts at RELEASE by default (MECHC34; RESEARCH28 R28-14, channel-cooldown-start.md, HIGH: the game stat
# channelled_skill_do_not_go_on_cooldown_on_finishing_channel, which moves it to the channel's start, is on Flame Breath
# only (RESEARCH28's check of the stat sets; 0.4.0c moved Flame Breath's to the start), so cycle = channel + cooldown
# (Flameblast, Meditate, Runic Tempering, Temper Weapon), and max(cooldown, channel) for COOLDOWN_AT_CHANNEL_START; the
# other reading a row. (MECHC33 had max() for all, as the cautious side for a cost: an overcount of Flameblast's cost.) Applied as the skill's own <Type>Cost MORE (PoB scales the per-second cost by it,
# CalcOffence 2313-2316), only when the skill has no upfront cost of that type (Flame Breath's upfront Mana stays whole;
# its Rage per minute is scaled). No ChannelTime from PoB (Temper Weapon, Runic Tempering: no stage multiplier): kept
# whole, said in the note. The rest of each cycle the build casts other skills, whose own costs aren't the main skill's.
CHANNEL_COST_FIELDS = ("ChannelTime", "Cooldown", "Speed")


COOLDOWN_AT_CHANNEL_START = frozenset({"Flame Breath"})


def channel_cost_share(stats: dict, skill: str = "") -> tuple[float, float, str]:
    """(share of the fight the main skill channels (headline), the other reading, how) from PoB's ChannelTime and
    Cooldown; (1.0, 1.0, how) when PoB gives no channel time or no cooldown."""
    channel, cooldown = _num(stats.get("ChannelTime")), _num(stats.get("Cooldown"))
    if channel <= 0 or cooldown <= 0:
        return 1.0, 1.0, ("its per-minute cost counted all fight (PoB gives no channel time or cooldown for it: an "
                          "overcount of the cost if it rests between channels; LOW)")
    at_start = min(1.0, channel / max(cooldown, channel))
    at_release = channel / (channel + cooldown)
    if skill in COOLDOWN_AT_CHANNEL_START:
        head, other, when, rest = at_start, at_release, "starts with the channel (its own stat)", "at release"
    else:
        head, other, when, rest = at_release, at_start, "starts at release (the game's default)", "with the channel"
    return head, other, (f"channelled {channel:.2f} s a release on its {cooldown:.2f} s cooldown, which {when}: its "
                         f"per-minute cost runs {head:.0%} of the fight (RESEARCH28 R28-14, HIGH), {other:.0%} if the "
                         f"cooldown started {rest}; PoB counts it all fight (without this: 100%)")


# Twister's hits on one boss per cast (MECHC33; RESEARCH28 R28-10, twister-hits-on-boss.md; MECHC35: the HEADLINE is the
# twister-count reading since the USER's answer, 16 Nov ~21:10, relayed by the manager 21:16: "I don't use the twister
# build, but I've seen other people play it and they can get a pretty huge number of twisters up that all bounce around
# and definitely hit more than once. I think the real answer is 'it depends on the number of twisters'").
# TwisterPlayer (game files): base_skill_effect_duration 3000, active_skill_base_area_of_effect_radius 5 (0.5 m),
# twister_hit_interval_ms 660 ("Twisters fired at the same time can Hit the same target no more than once every 0.66
# seconds": HIGH, shared by the group: MEDIUM-HIGH), projectile speed 7.5 m/s (poe2vault: MEDIUM). Cap k = floor(D /
# 0.66) + 1. HEADLINE (the user's observation, MEDIUM-LOW): the twisters bounce around a roam space A = pi x 6^2 m^2
# (TWISTER_ROAM, LOW, RESEARCH28's named assumption: the one number neither the build nor the boss gives):
# hits = 1 + (k - 1)(1 - e^(-N x 2 rho x v x 0.66 / A)), rho = the twister's radius (PoB's AreaOfEffectRadiusMetres,
# else 0.5 m x sqrt(area modifier)) + the boss's hit radius (bosses.hit_radius), v = 7.5 m/s x PoB's ProjectileSpeedMod,
# N = PoB's ProjectileCount (the twisters fired together, from the build), D = PoB's Duration. It rises with N, D, the
# area and the speed and never passes k. LOWER ROW, an open arena: a straight pass overlaps the boss for 2 (R + r) / v,
# 0.2-0.4 s, under one window, so 1 hit a cast (PoB's number: PoB maps neither the interval nor the duration to hits,
# act_dex.lua TwisterPlayer). Assumptions.models scales the main skill's DPS by the headline (PoB scores one hit a use).
# Each hit rolls the build's on-hit lines (R26-45 (d), ASSUMED), but the tool's on-hit readers (charges, ailment
# buildup) read PoB's casts a second and stay at one hit a cast: an UNDERCOUNT for those, said in the note. Twisters made
# by Whirlwinds or Barrage repeats as separate groups: UNVERIFIED, not counted.
TWISTER = "Twister"
TWISTER_FIELDS = ("ProjectileCount", "ProjectileSpeedMod", "Duration", "AreaOfEffectRadiusMetres", "AreaOfEffectMod")
TWISTER_SPEED, TWISTER_RADIUS, TWISTER_INTERVAL, TWISTER_DURATION = 7.5, 0.5, 0.66, 3.0
TWISTER_ROAM = 6.0  # metres: the roam space radius (LOW, RESEARCH28's named assumption)


def twister_hits(stats: dict, boss_radius: float) -> tuple[float, float, str]:
    """(hits on the boss a cast: the headline from the twister count, the open-arena row 1, how) from PoB's Twister
    outputs."""
    duration = _num(stats.get("Duration")) or TWISTER_DURATION
    k = math.floor(duration / TWISTER_INTERVAL + 1e-9) + 1
    count = max(_num(stats.get("ProjectileCount")), 1.0)
    speed = TWISTER_SPEED * (_num(stats.get("ProjectileSpeedMod")) or 1.0)
    own = _num(stats.get("AreaOfEffectRadiusMetres")) or TWISTER_RADIUS * math.sqrt(_num(stats.get("AreaOfEffectMod")) or 1.0)
    rho = own + max(boss_radius, 0.0)
    area = math.pi * TWISTER_ROAM ** 2
    head = 1 + (k - 1) * (1 - math.exp(-count * 2 * rho * speed * TWISTER_INTERVAL / area))
    return head, 1.0, (
        f"{head:.2f} hits a cast from your {count:g} twister(s) fired together (the user's observation, 16 Nov: many "
        f"twisters bounce around and hit more than once, \"it depends on the number of twisters\"; MEDIUM-LOW): "
        f"1 + ({k} - 1) x (1 - e^(-{count:g} x 2 x {rho:.2f} m x {speed:.1f} m/s x {TWISTER_INTERVAL:g} s / "
        f"{area:.0f} m^2)), at most {k} ({duration:.2f} s / {TWISTER_INTERVAL:g} s + 1, shared by the group: HIGH / "
        f"MEDIUM-HIGH); the roam space a {TWISTER_ROAM:g} m radius (LOW, a named assumption); in an open arena 1 a cast "
        f"(a pass crosses the boss in 2 x ({own:.2f} + {boss_radius:.2f}) m / {speed:.1f} m/s = {2 * rho / speed:.2f} s, "
        f"under one {TWISTER_INTERVAL:g} s window: PoB's number). On-hit charges and ailment buildup still read one hit "
        f"a cast (an UNDERCOUNT for those)")


# Sprinting between boss attacks (MECHC34; RESEARCH26 R26-22, sprint-between-boss-attacks.md, ASSUMED MEDIUM for the
# shape; MECHC33's scoping). HEADLINE: 0 sprint time: players sprint only to reposition (patch 0.3.0: sprint is made for
# travel; it ends when you use a skill; a hit while sprinting knocks you down), so every "while Sprinting" line stays at 0
# in the headline (PoB's Condition:Sprinting box is never set). A PLAY ROW only for a build carrying a sprint line (the
# Evasion / Movement Speed "while Sprinting" passives, the Cultivated "(100-150)% increased Evasion Rating while
# Sprinting", Running Assault, the boots Soul Cores Emergent Protection / Emergent Possibility / Carved Tenacity /
# Guatelitzi's Thesis, the Rune of the Hunt) that can sprint: it sprints after each boss reposition, as such a build plays
# to use them (CLAUDE.md's play-strategy rule):
# - repositions a second: the preset's dash / leap / charge uses (bosses.MOVING_CEILING's skills, each off cooldown) plus
#   one teleport skill off its shortest cooldown (RESEARCH22 #122's table): a CEILING (the AI also picks its many
#   cooldown-0 skills), LOW; which Atziri phase owns which teleport is my reading (LOW); the King's 3 s TeleportFlee (a
#   flee from a close player) left out; the worst-case preset reads the Arbiter of Ash's row (bosses.WORST's rule for
#   MECH-C tables keyed by name), the highest of them.
# - each reposition is crossed by sprinting SPRINT_GAP metres (the 6 m roam-space radius TWISTER_ROAM names: LOW) at
#   3.7 m/s (Character.ot base_speed 37, MEDIUM) x (PoB's MovementSpeedMod + (50 + the build's "N% increased Movement
#   Speed while Sprinting") / 100): Character.ot's sprint_movement_speed_+% = 50 (HIGH), added to the other increases as
#   PoB adds the "while Sprinting" lines (MovementSpeed INC; the additive reading with PoB's "more" folded in: MEDIUM-LOW).
# - the sprint share s = min(1, repositions/s x time a sprint) comes off the main skill (sprinting ends on any skill use).
# - gains in the row: "N% increased Evasion Rating while Sprinting" on the boss hits landing in s; Emergent Protection's
#   Guard = min(cap, N% x seconds sprinted) x (1 + "increased Guard gained") of maximum Life (the cap taken after the
#   increase: the lower reading for a defence, UNVERIFIED; before it the other row) for its 4 s after each stop, covering
#   min(1, repositions/s x 4 s) of the fight; "Cannot be Heavy Stunned while Sprinting", "Unaffected by Slows while
#   Sprinting", Ward regeneration while sprinting: named. Costs: "Lose N% of maximum Life per second while Sprinting"
#   (Guatelitzi's Thesis) per sprint, and s of main-skill time.
# (checked: every Stats id holding "sprint" in game build 26650308 over Mods, PassiveSkills, GrantedEffectStatSets(+
# PerLevel) and SoulCoreStats: the sources above, plus Ab Aeterno's Slows line and the "cannot_sprint" uniques; PoB's
# ModCache: the Evasion / Movement Speed / Heavy Stun lines parse under Condition:Sprinting, the Guard, Ward, Life-loss and
# Slows lines aren't parsed; `git grep -i sprint builder engine`: nothing read them before this; second source: the patch
# notes 0.3.0 / 0.3.0c / 0.4.0 / 0.5.0 sprint entries and PoB's ModRunes.lua rows, which match the game text.)
SPRINT_LINE = re.compile(r"\bSprint(?:ing|ed)?\b", re.I)
CANNOT_SPRINT = re.compile(r"^(?:You cannot Sprint|Cannot Dodge Roll or Sprint)$", re.I)
SPRINT_SPEED = re.compile(r"^(\d+(?:\.\d+)?)% increased Movement Speed while Sprinting$", re.I)
SPRINT_EVASION = re.compile(r"^(\d+(?:\.\d+)?)% increased Evasion Rating while Sprinting$", re.I)
SPRINT_GUARD = re.compile(r"^When you stop Sprinting, gain Guard equal to (\d+(?:\.\d+)?)% of maximum Life per second "
                          r"spent Sprinting, up to a maximum of (\d+(?:\.\d+)?)%, for (\d+(?:\.\d+)?) seconds$", re.I)
GUARD_GAINED = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Guard gained$", re.I)
SPRINT_LIFE_LOSS = re.compile(r"^Lose (\d+(?:\.\d+)?)% of maximum Life per second while Sprinting$", re.I)
SPRINT_NAMED = (("Cannot be Heavy Stunned while Sprinting", "no Heavy Stun from hits while sprinting"),
                ("Your speed is Unaffected by Slows while Sprinting", "Slows don't slow the sprint"),
                ("Runic Ward Regeneration Rate while Sprinting", "faster Runic Ward regeneration while sprinting"))
SPRINT_BASE_SPEED, SPRINT_OWN_INC, SPRINT_GAP = 3.7, 50.0, 6.0
SPRINT_REPOSITIONS: dict[str, tuple[float, str]] = {  # uses a second, each skill off cooldown (a ceiling, LOW)
    "Arbiter of Ash": (0.55, "Dash Slash x3 / Jump Slam Combo 0.45 + Teleport 10 s"),
    "Arbiter of Divinity": (0.089, "Swoop Slam / Blink Slam 0.06 + Storm Blink 35 s"),
    "Xesht, We That Are One": (0.1, "Teleport 10 s"),
    "The King in the Mists": (0.197, "four dashes on one 6 s timer, first form (0.072) + Teleport 8 s"),
    "Zarokh, the Temporal": (0.133, "teleport 7.5 s"),
    "The Raven Trickster": (0.125, "teleport 8 s"),
    "The Bodach": (0.067, "Reposition (burrow) 15 s"),
    "Olroth, Origin of the Fall": (0.25, "three dashes, 12 s each"),
    "The Aberration": (0.214, "three leaps, 14 s each"),
    "Vessel of Kulemak": (0.09, "Head Charge 20 s + Teleport Slam 25 s"),
    "Atziri, the Red Queen": (0.117, "Lightning Dash 20 s + Blood Warp 15 s (phase owner: LOW)"),
    "Atziri, the Fell Serpent": (0.258, "Beast Dash 12 s, Dash Arc 8 s + Beast Teleport 20 s (phase owner: LOW)"),
}


def sprint_row(texts: list[str], stats: dict, boss: str) -> dict | None:
    """The play guide's optional sprint row (a note dict) for a build with a sprint line, else None. The headline keeps
    0 sprint time."""
    lines = [t.strip() for t in texts]
    if not any(SPRINT_LINE.search(t) and not CANNOT_SPRINT.match(t) for t in lines):
        return None
    head = ("headline: no time sprinting (players sprint only to reposition: patch 0.3.0 made sprint for travel, it "
            "ends on any skill use and a hit knocks you down; RESEARCH26 R26-22, MEDIUM), so your 'while Sprinting' "
            "lines count 0 in every number")
    if any(CANNOT_SPRINT.match(t) for t in lines):
        return {"label": "Sprinting to reposition (a play row)*", "value": "0 (you can't sprint)",
                "basis": head + "; you can't sprint (Unwavering Stance or 'You cannot Sprint'), so no row",
                "confidence": "HIGH"}
    reps, reps_how = SPRINT_REPOSITIONS.get(boss) or SPRINT_REPOSITIONS["Arbiter of Ash"]
    who = boss if boss in SPRINT_REPOSITIONS else f"{boss} (the Arbiter of Ash's row, the highest)"
    inc = SPRINT_OWN_INC + sum(float(m.group(1)) for t in lines if (m := SPRINT_SPEED.match(t)))
    speed = SPRINT_BASE_SPEED * ((_num(stats.get("MovementSpeedMod")) or 1.0) + inc / 100)
    each = SPRINT_GAP / speed
    share = min(1.0, reps * each)
    gains, costs = [], [f"{share:.1%} of the fight off your main skill (sprinting ends on any skill use)"]
    evasion = sum(float(m.group(1)) for t in lines if (m := SPRINT_EVASION.match(t)))
    if evasion:
        gains.append(f"{evasion:g}% increased Evasion Rating on the boss hits landing in that {share:.1%}")
    guard_inc = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                    for t in lines if (m := GUARD_GAINED.match(t)))
    for t in lines:
        if m := SPRINT_GUARD.match(t):
            per, cap, last = float(m.group(1)), float(m.group(2)), float(m.group(3))
            # ("Buffs on you expire N% slower", MECHC36: Guard is in the game's Buff category, RESEARCH29: x the factor)
            expiry, expiry_how = stats.get("_buff_expiry") or (1.0, "")
            last *= expiry
            low = min(cap, per * each * (1 + guard_inc / 100))
            high = min(cap, per * each) * (1 + guard_inc / 100)
            other = (f" (the cap taken after 'increased Guard gained': UNVERIFIED; before it {high:.1f}%)"
                     if abs(high - low) > 0.05 else "")
            gains.append(f"Guard of {low:.1f}% of maximum Life for {last:.3g} s after each sprint{other}"
                         + (f" ({expiry_how})" if expiry != 1.0 else "") + f", up {min(1.0, reps * last):.0%} of the fight")
        if m := SPRINT_LIFE_LOSS.match(t):
            costs.append(f"{float(m.group(1)) * each:.1f}% of maximum Life lost each sprint")
    gains += [what for line, what in SPRINT_NAMED if any(line.lower() in t.lower() for t in lines)]
    how = (f"{head}. Row: you sprint {SPRINT_GAP:g} m (a named roam space, LOW) after each boss reposition, {reps:g} a "
           f"second against {who} ({reps_how}; each off cooldown: a ceiling, LOW), at {speed:.1f} m/s (3.7 m/s x "
           f"(MovementSpeedMod + {inc:g}% sprint increases), MEDIUM-LOW): {each:.2f} s a sprint, {share:.1%} of the "
           f"fight. Gains: {'; '.join(gains) or 'none modelled'}. Costs: {'; '.join(costs)}")
    return {"label": "Sprinting to reposition (a play row)*", "value": f"0 headline ({share:.1%} in the row)",
            "basis": how, "confidence": "MEDIUM (the 0 headline) / LOW (the row)",
            "play": (f"without it: you never sprint and the sprint lines do nothing; with it: sprint to each spot the "
                     f"boss moves to ({each:.2f} s, {share:.1%} of the fight off your main skill)"
                     + (f", gaining {'; '.join(gains)}" if gains else ""))}


# Overencumbrance from your own dodge rolls (MECHC35; COV38's downside sweep, the manager 21:50 16 Nov). Ab Aeterno's
# CURRENT variant (patch 0.5.0, the newest note: "no longer grant 10% less Movement and Skill Speed per Dodge Roll in the
# past 20 seconds ... now has ... Gain Overencumbrance for 4 seconds when you Dodge Roll"; PoB's boots.lua 279-281 tags the
# old line {variant:1,2}, and PoB equips the last variant by default, Item.lua 409 / 1794, so the old line never reaches a
# league-obtainable item: nothing to model for it; checked: the stat unique_movement_speed_and_skill_speed_-%_final_per_
# number_of_times_dodge_rolled_in_past_20_seconds is on one Mods row, UniqueSpeedPerDodgeRoll20Seconds1, and no passive,
# stat set or Soul Core, game 26650308). PoB parses the Overencumbrance line to nothing (ModCache 7787: leftover text).
# KeywordPopups Overencumbered: "Slows by 10%, reapplying this Debuff stacks and refreshes it's duration" (stat
# overencumbered_action_speed_+%_final; the only Mods row granting it is UniqueOverencumbranceOnDodge1, checked over
# Mods / PassiveSkills / stat sets by its stat ids). Rolls: the build's own pace (assumptions.ROLL_RELIANT's rule:
# models.DODGE_EVERY for a build that relies on rolling, else models.PARRY_EVERY, one per telegraphed boss hit), the
# Overencumbrance line itself not counted as a reason to roll. A pace of T >= 4 s keeps one stack up 4 / T of the time.
# Faster than every 4 s the stacks refresh each other and grow with every roll, with no cap in the files we have
# (UNVERIFIED: no buff table extracted): so the wearer plays to roll at most every 4 s (CLAUDE.md's play-strategy rule):
# one stack all the time. x the build's "Slowing Potency of Debuffs on You" lines (PoB parses none, ModCache 1763 ...);
# "Your speed is unaffected by Slows" (slows_have_no_potency_on_you, unconditional) cancels it. Into PoB as ActionSpeed INC (PoB's own slow path, which its
# UnaffectedBySlows flag skips; the game's "final" and PoB's INC agree while no other Slow is on you: PoB sets none).
OVERENCUMBRANCE = re.compile(r"^Gain Overencumbrance for (\d+(?:\.\d+)?) seconds? when you Dodge Roll$", re.I)
OVERENCUMBERED_SLOW = 10.0  # % a stack (KeywordPopups Overencumbered: "Slows by 10%")
SLOW_POTENCY = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Slowing Potency of Debuffs on You$", re.I)
NO_SLOW_POTENCY = re.compile(r"^Your speed is unaffected by Slows$", re.I)  # (slows_have_no_potency_on_you)


def overencumbrance_slow(texts: list[str], reliant: bool) -> tuple[float, str]:
    """(the average Slow on you, %, how) from "Gain Overencumbrance for N seconds when you Dodge Roll" at the build's own
    roll pace (`reliant`: a build relying on rolling), 0 without the line."""
    from builder.search import models

    lines = [t.strip() for t in texts]
    seconds = next((float(m.group(1)) for t in lines if (m := OVERENCUMBRANCE.match(t))), 0.0)
    if seconds <= 0:
        return 0.0, ""
    if any(NO_SLOW_POTENCY.match(t) for t in lines):
        return 0.0, "Overencumbrance on each dodge roll: no effect (your speed is unaffected by Slows)"
    potency = sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                  for t in lines if (m := SLOW_POTENCY.match(t)))
    every = models.DODGE_EVERY if reliant else models.PARRY_EVERY
    played = max(every, seconds)
    per_stack = OVERENCUMBERED_SLOW * max(1 + potency / 100, 0.0)
    slow = per_stack * min(1.0, seconds / played)
    pace = ("a roll every %g s (the build relies on rolling)" % every if reliant else
            "a roll every %g s, one per telegraphed boss hit (the manager's ruling on PLANS2)" % every)
    how = (f"Overencumbered ({OVERENCUMBERED_SLOW:g}% Slow a stack, {seconds:g} s, each roll stacks and refreshes it: "
           f"KeywordPopups) at {pace}" + (
               f"; faster than every {seconds:g} s the stacks would grow with each roll (no cap found: UNVERIFIED), so "
               f"you roll at most every {seconds:g} s while wearing it" if every < seconds else "")
           + f": one stack {min(1.0, seconds / played):.0%} of the time" + (
               f", x{1 + potency / 100:.2f} Slowing Potency on you" if potency else "")
           + f" = {slow:.1f}% less action speed on average (PoB applies none: without it 0%; never rolling: 0%)")
    return slow, how


# "You take (8-15)% of damage from Blocked Hits with a raised Shield" (MECHC35; COV38's downside sweep: the Ulaman shield
# suffix AbyssModShieldUlamanSuffixLightningTakenAsPhysAndGlancingWhileActiveBlocking, PoB ModVeiled 199; PoB parses the
# line to nothing, ModCache 9946: leftover text). A Block normally takes no damage; this line makes the hits you Block
# with your shield raised deal the line's share. Only those: the share of your Blocks made with the shield raised is
# t / (t + (1 - t) x b), t = the share of the fight the shield is raised (1 for a main skill that channels it all fight,
# ACTIVE_BLOCK_CHANNELS: "you will Block all incoming Blockable hits"; Raise Shield taps: taps a second x
# RAISED_TAP_SECONDS; else 0: the line does nothing), b = your ordinary Block chance (PoB's EffectiveBlockChance; a
# raised shield Blocks every Blockable hit). Into PoB as its BlockEffect BASE (the same stat PoB gives "You take N% of
# damage from Blocked Hits", ModCache 9947) at line x that share (an average: PoB's BlockEffect is one number).
RAISED_BLOCK_TAKEN = re.compile(r"^You take (\d+(?:\.\d+)?)% of damage from Blocked Hits with a raised Shield$", re.I)


def raised_block_taken(texts: list[str], raised: float, block: float) -> tuple[float, str]:
    """(PoB BlockEffect BASE to add, how) for the raised-shield line: `raised` the share of the fight the shield is up,
    `block` your ordinary Block chance (0-1); (0.0, "") without the line."""
    taken = sum(float(m.group(1)) for t in texts if (m := RAISED_BLOCK_TAKEN.match(t.strip())))
    if taken <= 0:
        return 0.0, ""
    raised = min(max(raised, 0.0), 1.0)
    block = min(max(block, 0.0), 1.0)
    share = raised / (raised + (1 - raised) * block) if raised > 0 else 0.0
    if share <= 0:
        return 0.0, (f"you take {taken:g}% of damage from Blocked Hits with a raised Shield, but the build never raises "
                     "its shield (no channelled shield skill, no Raise Shield taps): 0")
    return taken * share, (
        f"you take {taken:g}% of damage from the hits you Block with your shield raised: the shield is up {raised:.0%} of "
        f"the fight (Blocking every Blockable hit then) against your {block:.0%} Block chance otherwise, so {share:.0%} of "
        f"your Blocks are raised: {taken * share:.1f}% of each Blocked hit on average (PoB's BlockEffect; without it: 0%)")


# "Stunned Recently" lines (MECHC35; MECHC34's find: PoB's conditionStunnedRecently box and its multiplierStunnedRecently
# count were never set). So PoB applied "N% increased Stun Threshold if you haven't been Stunned Recently" (ModCache 2037
# / 2983 / 3378; the stat stun_threshold_+%_when_not_stunned_recently on 17 passives and two jewel mods) to EVERY build,
# even one the boss Stuns (an OVERCOUNT), and never applied "... if you've been Stunned Recently"
# (energy_shield_delay_-%_if_stunned_recently: "faster start of Energy Shield Recharge"; the Soul Core's
# stun_threshold_+%_if_stunned_recently), Last Stand's "Attack Damage if you have been Heavy Stunned Recently"
# (attack_damage_+%_if_been_heavy_stunned_recently; PoB maps Heavy to the same StunnedRecently condition, ModParser 1924)
# or "Stun Threshold for each time you've been Stunned Recently" (per_number_of_times_stunned_recently): undercounts.
# (checked: every Stats id holding stunned + recently / in_past / times_stunned in game 26650308 over Mods, PassiveSkills,
# GrantedEffectStatSets and SoulCoreStats; PoB ModCache's eight lines and ModParser 1923-1925 / 2016.) Not through PoB's
# box (one condition for both Light and Heavy, and the box's part-time weighing checks floors with it off): each line goes
# on PoB as its own stat at its time-average, the condition stripped: x u for "if you've been", x -u for "if you haven't
# been" (taking that share back off PoB's full bonus), x u_heavy for the Heavy-only line, x the expected count 4 s x r for
# "for each time". u = 1 - e^(-4 s x r): r = the Light Stuns the boss's hits put on you at the DEFENCE cadence
# (stuns_taken at PoB's enemySkillTime: the cautious side, the "haven't" bonus being the overcount; the real boss, not
# gain_floor) plus the build's Heavy Stuns from a channelled raised shield (channel_stun_of); parries', Refutation's and
# Raise Shield taps' Heavy Stuns aren't added (an undercount of u, said). "You cannot be Light Stunned if you've been
# Stunned Recently" (shield35) holds the Light Stuns to r / (1 + 4 r) (4 s without one after each). PoB's StunThreshold
# still holds the whole "haven't" bonus when r is read: one correction step re-reads r with that bonus at (1 - u)
# (LOW: other Stun Threshold increases taken as absent from the scaling).
STUNNED_RECENTLY = re.compile(r"^(\d+(?:\.\d+)?)% (.+?) (if you(?:'ve| have) been (heavy )?stunned recently|"
                              r"if you haven't been stunned recently|for each time you(?:'ve| have) been stunned "
                              r"recently)$", re.I)
NO_LIGHT_AFTER_STUN = re.compile(r"^You cannot be Light Stunned if you've been Stunned Recently$", re.I)
STUNNED_FIELDS = ("StunThreshold", "StunAvoidChance", "enemySkillTime", "EvadeChance", "EffectiveBlockChance",
                  "DeflectChance", "DeflectEffect", "PhysicalTakenHit", "FireTakenHit", "ColdTakenHit",
                  "LightningTakenHit", "ChaosTakenHit")


def stunned_recently_lines(texts: list[str]) -> list[tuple[float, str, str]]:
    """[(magnitude, the line's stat text written at 1, "have" / "heavy" / "havent" / "each")] of the build's lines."""
    out = []
    for t in texts:
        if m := STUNNED_RECENTLY.match(t.strip()):
            tail = m.group(3).lower()
            kind = ("havent" if "haven't" in tail else "each" if tail.startswith("for each") else
                    "heavy" if m.group(4) else "have")
            out.append((float(m.group(1)), f"1% {m.group(2)}", kind))
    return out


def stunned_recently_mods(texts: list[str], stats: dict, heavy_rate: float) -> tuple[list[dict], str]:
    """(PoB lines at their time-averages, how) for the build's "Stunned Recently" lines (the comment above); `stats` PoB's
    STUNNED_FIELDS and "_boss"; `heavy_rate` the build's own Heavy Stuns a second."""
    lines = stunned_recently_lines(texts)
    if not lines:
        return [], ""
    from builder.search import boss_hits, models, objective  # (objective imports this module)

    _, _, deflect = objective.boss_taken(stats)
    landed = boss_hits.land_share(stats) / (_num(stats.get("enemySkillTime")) or 0.7)
    weights = boss_hits.type_shares(boss_hits.boss_name(stats))
    sizes = {t: max(_num(stats.get(f"{t}TakenHit")), 0.0) * deflect for t in objective.RECOVERY_DAMAGE_TYPES}
    capped = any(NO_LIGHT_AFTER_STUN.match(t.strip()) for t in texts)
    havent = sum(v for v, text, k in lines if k == "havent" and "stun threshold" in text.lower())
    threshold = _num(stats.get("StunThreshold"))

    def light_at(share: float) -> tuple[float, str]:
        scaled = dict(stats)
        if havent > 0 and threshold > 0:  # (the "haven't" bonus at its share of the fight not Stunned Recently)
            scaled["StunThreshold"] = threshold * (100 + havent * (1 - share)) / (100 + havent)
        rate, how = stuns_taken(scaled, sizes, weights, landed)
        return (rate / (1 + RECENT_STUN_SECONDS * rate) if capped and rate > 0 else rate), how

    def stunned(rate: float) -> float:
        return 1 - math.exp(-RECENT_STUN_SECONDS * rate) if rate > 0 else 0.0

    light, light_how = light_at(0.0)
    share = stunned(light + heavy_rate)
    if havent > 0:
        light, light_how = light_at(share)
        share = stunned(light + heavy_rate)
    heavy = stunned(heavy_rate)
    count = RECENT_STUN_SECONDS * (light + heavy_rate)
    scales = {"have": share, "heavy": heavy, "havent": -share, "each": count}
    rows = {"have": f"x {share:.0%} (Stunned Recently)", "heavy": f"x {heavy:.0%} (Heavy Stunned Recently)",
            "havent": f"x {1 - share:.0%} (not Stunned Recently)", "each": f"x {count:.2f} Stuns in the past 4 s"}
    mods, parts = [], []
    for value, text, kind in lines:
        if abs(scales[kind] * value) > 1e-9:
            mods.append(models.line(text, scales[kind] * value))
        parts.append(f"'{value:g}{text[1:]}' {rows[kind]}")
    how = (f"Stunned Recently {share:.0%} of the fight: {light * 60:.1f} Light Stuns a minute at the boss's defence "
           f"cadence ({light_how})" + (" (none for 4 s after each: your 'cannot be Light Stunned' line)" if capped else "")
           + f" + {heavy_rate * 60:.1f} Heavy Stuns a minute from a raised-shield channel (parries, Refutation and Raise "
           f"Shield taps not added: an undercount); 1 - e^(-4 s x the rate). " + "; ".join(parts)
           + " (PoB's box was never set: the 'haven't' lines counted all fight, the others never; without this: so)")
    return mods, how


# Runic Reprieve's channel time and Heavy Stun (MECHC35; MECHC34's next step: Assumptions.ward_damaged_share channels it
# into the boss's hits, MECHC15, but took neither its time nor its Heavy Stun off the DPS: an OVERCOUNT of DPS for a build
# playing it). RunicReprievePlayer: channel_start_duration_as_%_of_cast_time 200 (the wind-up, CHANNEL_START_REPRIEVE)
# and active_skill_does_not_decay_heavy_stun_during_action (MECHC29's find): each channel blocks one of the boss's hits
# into Ward, and that hit builds Heavy Stun with no decay during it. Channels come one per paid boss hit
# (ward_damaged_share's `paid`, the "when hit" pace, more than 4 s apart at every preset's cadence: the bar empties
# between them, 2 s delay then 50% a second), so a channel stuns only on a hit that fills the bar alone (as the taps and
# parries); its text gives no Evasion clause (no avoidance). Each stun costs PLAYER_HEAVY_STUN_SECONDS of the main skill;
# each channel at least its wind-up (castTime x 2; the hold after it to the hit isn't added: a lower bound, said).
def reprieve_heavy_stun(stats: dict, scale: float, paid: float) -> tuple[float, str]:
    """(share of the fight Heavily Stunned by Runic Reprieve's channels, how) at `paid` channels a second."""
    if paid <= 0:
        return 0.0, ""
    buildups, how = heavy_buildups(stats, scale)
    full = sum(w for w, b in buildups if b >= 1)
    share = min(1.0, paid * full * PLAYER_HEAVY_STUN_SECONDS)
    return share, (f"each Runic Reprieve channel ({paid:.2f} a second) blocks one boss hit into Ward with no Heavy Stun "
                   f"decay; it Heavy Stuns you when that hit fills the bar alone ({how}): {full:.0%} of the boss's hits "
                   f"do, {PLAYER_HEAVY_STUN_SECONDS:g} s each: {share:.1%} of the fight (MEDIUM-LOW; the bar taken as "
                   "empty at each channel)")


def reprieve_stun_of(build, paid: float) -> tuple[float, str]:
    if paid <= 0:
        return 0.0, ""
    stats, support, texts = _heavy_stun_reads(build, "Runic Reprieve")
    scale, what = heavy_stun_scale(texts, support, channel=True)
    share, how = reprieve_heavy_stun(stats, scale, paid)
    how += _biggest_row(stats, lambda s: reprieve_heavy_stun(s, scale, paid)[0], lambda v: f"{v:.1%} of the fight")
    return share, how + (f" (threshold: {', '.join(what)})" if what else "")


# ---------------------------------------------------------------------------------------------------------------------
# Part-time Guard as a play-guide row (MECHC37; the manager's 17 Nov ruling (b) on MECHC36's question: "the floors count
# only what is up all the time, like every other part-time effect (the user's 9 Nov rule). Show the average-uptime Guard
# as a row"). KeywordPopups Guard: "a buffer against damage from Hits ... until the buff expires or is depleted. You can
# only have a single Guard buff at a time ... Your maximum amount of Guard is equal to 200% of your maximum Life" (HIGH).
# PoB counts no PoE2 Guard (PLANS1's kw-guard plan: nothing sets GuardAbsorbLimit / Rate in PoB-PoE2), and the tool feeds
# none to PoB, so a part-time Guard row moves no defence number (Guard up all fight counts since MECHC38: the ruling (ii)
# note at full_guard_mods).
# Guard is in the game's Buff category (RESEARCH29): "Buffs on you expire N% slower" lengthens it (MECHC36's sprint row).
# - Glowswarm (PATCH_CHECK H2-4; patch 0.5.0 gave it the line, 0.5.3 fixed values other than 100% (Loreweave can roll
#   it): "Using a Mana Flask grants Guard equal to 100% of Flask's recovery amount for 4 seconds"; PoB parses it to
#   nothing). Guard per use = N% x the Mana flask's recovery amount (PoB's manaTotal x the build's increased flask Mana
#   recovery: "recovery amount" read as the amount it recovers, MEDIUM) x (1 + increased Guard gained), capped at 200% of
#   maximum Life; up min(1, uses/s x its seconds) of the fight (uses paced by its charges: flasks.uses_per_second). Two
#   such lines sum as one stat (MEDIUM).
# - Jade Heritage + Encase in Jade (PATCH_CHECK L1-3; PLANS4's kw-jade plan; game 26650308 PassiveSkills
#   AscendancyWarrior2Notable3: max_jade_stacks 10, buildup_jade_every_x_ms 1000, additional_physical_damage_reduction_%
#   _per_jade 1; KeywordPopups Jade: "1% additional Physical Damage Reduction per stack. Maximum of 10"; patch 0.3.0:
#   stacks "no longer lost when taking Physical Damage from a Hit"). PoB parses "Gain a stack of Jade every second" to
#   nothing (ModCache 5656, leftover text), so the 10% has counted nowhere: an UNDERCOUNT. Counted: 10 stacks all fight
#   after the first 10 s (Jade is lost only to Encase in Jade, which the headline doesn't press: its Guard is a row,
#   so pressing it only lowers the counted defence), as PhysicalDamageReduction BASE 10 in PoB. (checked: Stats ids with
#   "jade" in game 26650308: max_jade_stacks / buildup_jade_every_x_ms / additional_physical_damage_reduction_%_per_jade
#   only on Jade Heritage, no Mods row; jade_count / virtual_damage_absorbed_per_jade_consumed / mtx_apply_jade_hit_effect
#   on nothing; maximum_life_%_damage_absorbed_per_jade_consumed only on Encase in Jade; patch notes --history "Jade".)
#   Row, with Encase in Jade pressed at 10 Jade: "Consume all stacks of Jade to grant Guard ... for each Jade consumed.
#   You cannot gain Jade stacks while you have Guard" (PoB's gem text; the 0.2.0 rework): Guard (6 + 0.05 x quality)% of
#   maximum Life a Jade (maximum_life_%_damage_absorbed_per_jade_consumed), for its duration D (4 s at level 1 to 4.95 s
#   at 20; PoB grants it at level 1: ModCache 5714), then 10 s to refill: Guard up D / (10 + D), Jade averaging
#   45 / (10 + D) stacks (the ramp 0..10 over 10 s, none while the Guard lasts). Any Guard stops Jade (RESEARCH31 R31-4,
#   MEDIUM): a Guard up all fight zeroes Jade Heritage (Assumptions.wanted); a part-time one leaves gaps it refills in.
MANA_FLASK_GUARD = re.compile(r"^Using a Mana Flask grants Guard equal to (\d+(?:\.\d+)?)% of (?:the )?Flask's recovery "
                              r"amount for (\d+(?:\.\d+)?) seconds$", re.I)
JADE_LINE = re.compile(r"^Gain a stack of Jade every second$", re.I)
JADE_STACKS, JADE_PER_SECOND, JADE_DR = 10, 1.0, 1.0  # Jade Heritage's stats and KeywordPopups Jade (HIGH)
ENCASE_PER_JADE, ENCASE_PER_QUALITY = 6.0, 0.05  # % of maximum Life a Jade; + per quality (PoB other.lua 5667-5669)
ENCASE_SECONDS = 4.0  # level 1 (PoB grants it at level 1); 4.95 s at level 20
GUARD_CAP = 2.0  # x maximum Life (KeywordPopups Guard)


def _guard_inc(lines: list[str]) -> float:
    return sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
               for t in lines if (m := GUARD_GAINED.match(t.strip())))


# Guard up ALL fight (MECHC38; the manager's ruling (ii), 18 Nov 02:30, on MECHC37's question): "Guard at FULL uptime
# (never down in the model) counts in the floors, by rule (b). Part-time Guard stays a row." Counted in PoB's own Guard
# pool (CalcDefence 3040-3070: GuardAbsorbRate BASE, GuardAbsorbLimit BASE; its hit pools take Guard first, 516-582 and
# 2076-2082), at 100% of each hit (KeywordPopups Guard: "taking the damage before your Life or Energy Shield") up to the
# Guard amount: each hit the floors check meets a full Guard (the model's "never down"; a hit spends Guard until the
# next grant, so two hits within one refill meet less: the ruling's reading, named in the row). From
# conditions.FULL_UPTIME (99.5%) of the fight; under it the row alone. One Guard buff at a time (KeywordPopups): only one
# source counts (Glowswarm's is the one that can be up all fight today; Encase in Jade's cycle never is). Any Guard stops
# Jade Heritage's stacks (RESEARCH31 R31-4, MEDIUM): with Guard up all fight Jade isn't counted (Assumptions.wanted).
GUARD_FULL_UPTIME = 0.995  # conditions.FULL_UPTIME


def full_guard_mods(guard: float, up: float) -> list[dict]:
    """PoB's Guard pool for Guard up all fight (the note above): [] under GUARD_FULL_UPTIME or with no Guard."""
    if guard <= 0 or up < GUARD_FULL_UPTIME:
        return []
    from builder.search import models

    return [models.raw_mod("GuardAbsorbRate", "BASE", 100, tag="FullGuard"),
            models.raw_mod("GuardAbsorbLimit", "BASE", round(guard), tag="FullGuard")]


def mana_flask_guard(texts: list[str], amount: float, uses: float, life: float,
                     expiry: tuple[float, str] | None = None) -> tuple[float, float]:
    """(Guard a use, its share of the fight) for "Using a Mana Flask grants Guard equal to N% of Flask's recovery amount
    for N seconds" (mana_flask_guard_row's numbers); (0, 0) without the line."""
    found = [m for t in texts if (m := MANA_FLASK_GUARD.match(t.strip()))]
    if not found:
        return 0.0, 0.0
    pct = sum(float(m.group(1)) for m in found)
    seconds = max(float(m.group(2)) for m in found) * (expiry or (1.0, ""))[0]
    raw = pct / 100 * max(amount, 0.0) * (1 + _guard_inc(texts) / 100)
    return min(raw, GUARD_CAP * max(life, 0.0)), min(1.0, max(uses, 0.0) * seconds)


def mana_flask_guard_row(texts: list[str], amount: float, uses: float, uses_how: str, life: float,
                         expiry: tuple[float, str] | None = None) -> dict | None:
    """The play row for "Using a Mana Flask grants Guard equal to N% of Flask's recovery amount for N seconds" (see the
    note above), or None without the line. `amount`: the Mana flask's recovery a use; `uses`: its uses a second."""
    found = [m for t in texts if (m := MANA_FLASK_GUARD.match(t.strip()))]
    if not found:
        return None
    pct = sum(float(m.group(1)) for m in found)
    seconds = max(float(m.group(2)) for m in found)
    factor, factor_how = expiry or (1.0, "")
    seconds *= factor
    inc = _guard_inc(texts)
    raw = pct / 100 * max(amount, 0.0) * (1 + inc / 100)
    guard, up = mana_flask_guard(texts, amount, uses, life, expiry)
    whole = bool(full_guard_mods(guard, up))
    capped = f" (capped at 200% of maximum Life (or Energy Shield: R33-6) from {raw:,.0f})" if raw > guard + 0.5 else ""
    if guard <= 0 or up <= 0:
        value = "0: " + ("no Mana flask use against the boss" if up <= 0 else "the Mana flask recovers nothing")
    elif whole:
        value = (f"{guard:,.0f} Guard for {seconds:.3g} s a use, up all fight: in the defences (PoB's Guard pool takes "
                 "each hit first)")
    else:
        value = f"{guard:,.0f} Guard for {seconds:.3g} s a use, up {up:.0%} of the fight ({guard * up:,.0f} on average)"
    if whole:
        return {"label": "Guard from Mana Flask use (up all fight: in the defences)*", "value": value,
                "basis": (f"{pct:g}% of the Mana flask's {amount:,.0f} recovery a use"
                          + (f" x {1 + inc / 100:.2f} (increased Guard gained)" if inc else "") + f" = {guard:,.0f} Guard"
                          + capped + f", for {seconds:.3g} s" + (f" ({factor_how})" if factor != 1.0 else "")
                          + f" after each use; {uses:.3f} uses/s ({uses_how}): up all fight, so counted in the defence "
                          "floors (the manager's 18 Nov ruling (ii)) as PoB's Guard pool: 100% of a hit taken from "
                          f"{guard:,.0f} Guard first. Each hit the floors check meets a full Guard; a hit spends it until "
                          "the next use, so two big hits within one use meet less. Without it: no Guard"),
                "confidence": "MEDIUM (the line's words; the use rate from the flask's charges; full Guard at each hit)",
                "play": (f"use the Mana flask whenever it has charges: {guard:,.0f} Guard after each use, up all fight "
                         "(without it: no Guard)")}
    return {"label": "Guard from Mana Flask use (a row, not in the defences)*", "value": value,
            "basis": (f"{pct:g}% of the Mana flask's {amount:,.0f} recovery a use"
                      + (f" x {1 + inc / 100:.2f} (increased Guard gained)" if inc else "") + f" = {guard:,.0f} Guard"
                      + capped + f", for {seconds:.3g} s" + (f" ({factor_how})" if factor != 1.0 else "")
                      + f" after each use; {uses:.3f} uses/s ({uses_how}): up {up:.0%} of the fight. Guard absorbs hits "
                      "until spent; part-time, so it's in no defence floor and not in PoB's numbers (the manager's 17 Nov "
                      "ruling: floors count only what is up all the time); PoB counts no Guard; without it: none"),
            "confidence": "MEDIUM (the line's words; the use rate from the flask's charges)",
            "play": (f"use the Mana flask whenever it has charges: {guard:,.0f} Guard after each use, up {up:.0%} of "
                     "the fight (without it: no Guard)" if guard > 0 and up > 0 else "")}


# Scold's Bridle (MECHC37; COV40's routed OVERCOUNT): "Take (25-100)% of Mana Costs you pay for Skills as Physical Damage"
# (PoB parses ManaCostAsPhysical with leftover text, ModCache 9577, so it never applies it: LANE_RULES "PoB never applies
# a line it parses only in part"). Counted: N% x PoB's ManaPerSecondCost (the main skill's Mana cost x its uses a second)
# as Physical damage, one loss per use (the main skill's Speed), through Heartbound Loop's path (PoB's Physical degen for
# the totals, Build.added_stats' SelfDamageEvents for the Recharge stops). Other skills' costs not counted (an
# undercount of the damage, named); Armour not applied to a self-inflicted loss (UNVERIFIED, the cautious side).
# (checked: Stats ids holding "mana_cost" with "physical" / "damage" in game 26650308: skills_deal_you_x%_of_mana_cost_
# as_physical_damage on one Mods row, UniquePhysicalDamageOnSkillUse1, no PassiveSkills / SoulCoreStats row (the rest
# are added-damage stats); PoB-PoE2 Uniques: Scold's Bridle only.)
COST_DAMAGE = re.compile(r"^Take (\d+(?:\.\d+)?)% of Mana Costs you pay for Skills as Physical Damage$", re.I)


def cost_damage(texts: list[str], stats: dict) -> tuple[float, float, list[dict]]:
    """(Physical damage a second, losses a second, notes) from COST_DAMAGE lines; zeros without one."""
    pct = sum(float(m.group(1)) for t in texts if (m := COST_DAMAGE.match(t.strip())))
    if pct <= 0:
        return 0.0, 0.0, []
    paid = max(_num(stats.get("ManaPerSecondCost")), 0.0)
    uses = max(_num(stats.get("Speed")), 0.0) if paid > 0 else 0.0
    taken = pct / 100 * paid
    return taken, uses, [{
        "label": "Mana costs taken as Physical damage (Scold's Bridle)*", "value": f"{taken:,.0f} Physical damage a second",
        "basis": (f"{pct:g}% of the {paid:,.1f} Mana a second your main skill's costs take ({uses:.2f} uses a second, one "
                  "loss each, which resets Recharge as a hit does); other skills' costs not counted (an undercount); "
                  "Armour not applied (UNVERIFIED); PoB parses the line only in part and never applies it; without it: 0"),
        "confidence": "MEDIUM (the line's words; the main skill's costs only)",
        "play": (f"every cast costs you {pct:g}% of its Mana cost in Life / Energy Shield: {taken:,.0f} a second"
                 if taken > 0 else "")}]


# Pyromantic Pact's self-hit (MECHC51; plans/un-pyromantic-pact.md (b), an OVERCOUNT of survival: only text in a note
# before). AscendancyWitch1Notable7: "Take maximum Life and Energy Shield as Fire Damage when Infernal Flame reaches
# maximum" (hit_for_%_max_life_es_on_max_infernal_flame 100, game 26650308; PoB parses it only in part, ModCache 7778,
# so it never applies it). Infernal Flame fills with the skill costs (1 Flame a Mana of cost, MEDIUM, models.
# archon_sources' cycle) to 2 x maximum Mana, so one hit of (maximum Life + maximum ES) every 2 x Mana / ManaPerSecondCost
# s (the main skill's costs; other skills' not counted, the lower reading). In PoB as Fire damage taken per second (its
# Fire resistance and damage-over-time-taken modifiers applied, standing in for the hit's mitigation, MEDIUM) and one
# event a cycle for the Recharge stops (Build.added_stats' Fire path, beside Fireflower's). The 0.5.0b Hotfix 2 fix
# ("some sources of damage like Pyromantic Pact would not deal damage within boss arenas") means it lands on a boss.
# Row: pausing near the top (models.FLAME_MANAGED's play row) avoids the reset: no hit. No skill cost: no Flame, no hit.
PACT_NO_MANA = re.compile(r"Gain Infernal Flame instead of spending Mana for Skill costs", re.I)
PACT_SELF_HIT = re.compile(r"Take maximum Life and Energy Shield as Fire Damage when Infernal Flame reaches maximum", re.I)


def pact_self_hit(texts: list[str], stats: dict) -> tuple[float, float, list[dict]]:
    """(Fire damage a second before mitigation, hits a second, notes) for PACT_SELF_HIT; zeros without the line."""
    if not any(PACT_SELF_HIT.search(t) for t in texts):
        return 0.0, 0.0, []
    mana, cost = max(_num(stats.get("Mana")), 0.0), max(_num(stats.get("ManaPerSecondCost")), 0.0)
    pool = max(_num(stats.get("Life")), 0.0) + max(_num(stats.get("EnergyShield")), 0.0)
    cycle = 2 * mana / cost if mana > 0 and cost > 0 else 0.0
    if cycle <= 0 or pool <= 0:
        return 0.0, 0.0, [{
            "label": "Pyromantic Pact: the Fire hit at maximum Infernal Flame*", "value": "none",
            "basis": "the main skill costs no Mana, so no Infernal Flame is gained and the bar never resets",
            "confidence": "MEDIUM", "play": ""}]
    taken, hits = pool / cycle, 1 / cycle
    return taken, hits, [{
        "label": "Pyromantic Pact: the Fire hit at maximum Infernal Flame*",
        "value": f"a Fire hit of {pool:,.0f} every {cycle:.1f} s ({taken:,.0f} a second before your Fire resistance)",
        "basis": (f"Infernal Flame fills to 2 x {mana:,.0f} Mana at {cost:,.1f} Mana of costs a second (the main skill's; "
                  "1 Flame a Mana, MEDIUM) and resets, dealing your maximum Life and Energy Shield as Fire damage; "
                  "counted as Fire damage taken a second after your Fire resistance, one hit a cycle for Recharge. Row: "
                  "pausing near the top (the play guide's row) avoids it: none. PoB parses the line only in part and "
                  "never applies it"),
        "confidence": "MEDIUM (the cycle; the hit's size HIGH)",
        "play": (f"Pyromantic Pact: at full Infernal Flame you take a Fire hit of your Life and Energy Shield "
                 f"({pool:,.0f}) every {cycle:.1f} s; pause near the top to avoid it")}]


# Reflected Poison (MECHC37; COV40's routed OVERCOUNT: a Vaal-mutated Hand Wraps line, "Poison you inflict is Reflected to
# you"; PoB parses it to nothing, ModCache 8940). Each Poison your main skill inflicts on the boss lands on you too: Chaos
# damage over time on Life only ("Damage from Poison bypasses Energy Shield", KeywordPopups Poison), at PoB's poison
# damage a second against the boss (TotalPoisonDPS, else PoisonDPS: after the boss's own Chaos resistance and damage-taken
# modifiers, where the game's reflected magnitude has neither: the lower reading where the boss resists Chaos, LOW-MEDIUM)
# through Build.added_stats' SelfPoisonPerSecond, then your Chaos damage-over-time multiplier. Other skills' Poisons
# not counted (an undercount, named).
REFLECT_POISON = re.compile(r"^Poison you inflict is Reflected to you$", re.I)
REFLECT_POISON_FIELDS = ("TotalPoisonDPS", "PoisonDPS")


def reflected_poison(texts: list[str], stats: dict) -> tuple[float, list[dict]]:
    """(Chaos damage a second on your Life before your damage-over-time modifiers, notes) from REFLECT_POISON."""
    if not any(REFLECT_POISON.match(t.strip()) for t in texts):
        return 0.0, []
    dps = max(_num(stats.get("TotalPoisonDPS")) or _num(stats.get("PoisonDPS")), 0.0)
    return dps, [{
        "label": "Your Poisons reflected to you*", "value": f"{dps:,.0f} Chaos damage a second on Life",
        "basis": (f"your main skill's Poison on the boss, {dps:,.0f} a second (PoB's), lands on you too: Life only (Poison "
                  "bypasses Energy Shield), before your Chaos resistance and damage-over-time modifiers; PoB's figure is "
                  "after the boss's Chaos resistance (the lower reading if it resists); other skills' Poisons not "
                  "counted; PoB counts none; without it: 0"),
        "confidence": "LOW-MEDIUM (the magnitude reading)",
        "play": (f"your own Poisons hit you for {dps:,.0f} Chaos damage a second" if dps > 0 else "")}]


# "Per Poison on you" (MECHC43; RESEARCH33 R33-19 cultivated-handwraps-per-poison-frozen.md, MEDIUM-HIGH: OBTAINABLE, narrowly:
# a Vaal Cultivation Orb on a Corrupted Snakebite worn as Fists of Stone, Way of the Stonefist; at most 2 Cultivated
# lines; MECH89's never-on rows; PoB parses none, an UNDERCOUNT). The three hand-wraps lines (PoB ModItem 2222-2224:
# HandWrapsUniqueMutatedVaal*) each come with "Poison you inflict is Reflected to you", so the Poisons on you are the
# build's own (no flat rate): the main skill's Poisons on the boss (PoB's PoisonStackPotential x PoisonStacksMax: its
# applications over the Poison's duration), each lasting x your Poison duration on you (PoB's SelfPoisonDuration %),
# capped at your Poisons-at-once limit (PoisonStacksMax: the lower reading, MEDIUM-LOW; MECHC35 took Poisons on you as all
# stacking, here the cap is the headline and the uncapped count a row). Other skills' Poisons not counted (an
# undercount). Reflected Poisons the line avoids (PoB's PoisonAvoidChance) are taken off. Damage: plain "N% increased
# Damage" at the average count, up to the line's maximum. Chaos Resistance: a ROW (a defence from a count that rises and
# falls: the floors count what is up all fight). Movement Speed: a row (no number of ours reads it). Their cost, the
# reflected Poison's damage, is reflected_poison's (above). The fixed Exclusive __1 lines (15% / +1% / 10%) are PoE1
# leftovers (R33-19: UNREACHABLE); the patterns read them too if one ever appears.
PER_POISON_DAMAGE = re.compile(r"^(\d+(?:\.\d+)?)% increased Damage for each Poison on you up to a maximum of "
                               r"(\d+(?:\.\d+)?)%$", re.I)
PER_POISON_SPEED = re.compile(r"^(\d+(?:\.\d+)?)% increased Movement Speed for each Poison on you up to a maximum of "
                              r"(\d+(?:\.\d+)?)%$", re.I)
PER_POISON_CHAOS = re.compile(r"^\+(\d+(?:\.\d+)?)% to Chaos Resistance per Poison on you$", re.I)
PER_POISON_FIELDS = ("PoisonStackPotential", "PoisonStacksMax", "SelfPoisonDuration", "PoisonAvoidChance")


def poisons_on_you(stats: dict) -> tuple[float, float, str]:
    """(Poisons on you, capped; uncapped; how) from your main skill's Poisons reflected (the note above)."""
    most = max(_num(stats.get("PoisonStacksMax")), 0.0)
    potential = max(_num(stats.get("PoisonStackPotential")), 0.0)
    scale = (_num(stats.get("SelfPoisonDuration")) or 100.0) / 100
    kept = 1 - min(max(_num(stats.get("PoisonAvoidChance")), 0.0), 100.0) / 100
    uncapped = potential * most * scale * kept
    capped = min(uncapped, most) if most > 0 else 0.0
    return capped, uncapped, (f"your main skill's Poisons on the boss ({potential * most:.2f} at once: PoB's stack potential "
                              f"x its {most:g} limit) reflected to you, x{scale:.2f} Poison duration on you"
                              + (f", {1 - kept:.0%} avoided" if kept < 1 else "")
                              + f": {uncapped:.2f} on you, capped at your {most:g} Poisons at once: {capped:.2f}")


def per_poison_lines(texts: list[str], stats: dict) -> tuple[list[dict], list[dict]]:
    """(mods, rows) for the PER_POISON_* lines (the note above); nothing without one."""
    from builder.search import models

    stripped = [t.strip() for t in texts]
    damage = [(float(m.group(1)), float(m.group(2))) for t in stripped if (m := PER_POISON_DAMAGE.match(t))]
    speed = [(float(m.group(1)), float(m.group(2))) for t in stripped if (m := PER_POISON_SPEED.match(t))]
    chaos = [float(m.group(1)) for t in stripped if (m := PER_POISON_CHAOS.match(t))]
    if not (damage or speed or chaos):
        return [], []
    count, uncapped, how = poisons_on_you(stats)
    reflected = any(REFLECT_POISON.match(t) for t in stripped)
    if not reflected:
        count, uncapped, how = 0.0, 0.0, "no line reflects your Poisons to you and nothing else Poisons you here: 0"
    mods, rows = [], []
    for each, top in damage:
        got, other = min(each * count, top), min(each * uncapped, top)
        if got > 0:
            mods.append(models.line("1% increased Damage", got))
        rows.append({"label": f"{each:g}% increased Damage for each Poison on you (up to {top:g}%)*",
                     "value": f"{got:.1f}% increased Damage", "basis": f"{how}; row, every Poison on you counted (no "
                     f"limit): {other:.1f}%; PoB counts none",
                     "confidence": "MEDIUM-LOW (R33-19 obtainable MEDIUM-HIGH; the count's cap MEDIUM-LOW)",
                     "play": "Keep Poisoning the boss: your Poisons reflect onto you and feed it" if got > 0 else ""})
    for each, top in speed:
        rows.append({"label": f"{each:g}% increased Movement Speed for each Poison on you (up to {top:g}%) (a row)*",
                     "value": f"{min(each * count, top):.1f}% increased Movement Speed",
                     "basis": f"{how}; movement isn't scored", "confidence": "MEDIUM-LOW", "play": ""})
    for each in chaos:
        rows.append({"label": f"+{each:g}% to Chaos Resistance per Poison on you (a row)*",
                     "value": f"+{each * count:.1f}% Chaos Resistance on average",
                     "basis": (f"{how}; the count rises and falls with your Poisons, so it is a row, not in the floors "
                               f"or PoB's numbers (every Poison counted: +{each * uncapped:.1f}%)"),
                     "confidence": "MEDIUM-LOW", "play": ""})
    return mods, rows


def jade_heritage(texts: list[str], life: float, quality: float = 0.0, seconds: float = ENCASE_SECONDS,
                  expiry: tuple[float, str] | None = None) -> tuple[float, dict | None]:
    """(Physical Damage Reduction % counted, the row) for Jade Heritage (see the note above): (0, None) without it."""
    if not any(JADE_LINE.match(t.strip()) for t in texts):
        return 0.0, None
    counted = JADE_STACKS * JADE_DR
    factor, factor_how = expiry or (1.0, "")
    length = seconds * factor
    refill = JADE_STACKS / JADE_PER_SECOND
    cycle = refill + length
    per = ENCASE_PER_JADE + ENCASE_PER_QUALITY * quality
    inc = _guard_inc(texts)
    guard = min(JADE_STACKS * per / 100 * max(life, 0.0) * (1 + inc / 100), GUARD_CAP * max(life, 0.0))
    up = length / cycle
    stacks = (refill * (JADE_STACKS - 1) / 2) / cycle  # (0, 1, ..., 9 stacks a second each while refilling: 45)
    return counted, {
        "label": "Jade Heritage: Jade stacks*", "value": f"{counted:g}% additional Physical Damage Reduction (10 Jade)",
        "basis": (f"a Jade a second up to {JADE_STACKS} (Jade Heritage), {JADE_DR:g}% additional Physical Damage "
                  f"Reduction each (KeywordPopups Jade), kept when hit (patch 0.3.0): {counted:g}% all fight after the "
                  "first 10 s, counted in PoB (it parses the line to nothing). Encase in Jade isn't pressed in the "
                  "headline: its Guard is part-time, so a row only (the manager's 17 Nov ruling), and pressing it spends "
                  f"the Jade. Row, pressing Encase in Jade at {JADE_STACKS} Jade: {guard:,.0f} Guard "
                  f"({per:g}% of maximum Life a Jade" + (f", x {1 + inc / 100:.2f} increased Guard gained" if inc else "")
                  + f") for {length:.3g} s" + (f" ({factor_how})" if factor != 1.0 else "")
                  + f" (level 1, as PoB grants it; 4.95 s at level 20), then {refill:g} s to refill (no Jade while "
                  f"Guarded): Guard up {up:.0%} of the fight, Jade averaging {stacks:.1f} stacks ({stacks * JADE_DR:.1f}% "
                  "reduction)"),
        "confidence": "HIGH (Jade's stats) / MEDIUM (the cycle row)",
        "play": (f"without Encase in Jade (counted): {counted:g}% Physical Damage Reduction all fight; with it pressed "
                 f"every {cycle:.3g} s: {guard:,.0f} Guard up {up:.0%} of the fight, but only {stacks * JADE_DR:.1f}% "
                 "reduction on average")}


# Bulwark (MECHC38; COV40's routed UNDERCOUNT, 18.0): "Take N% less Damage from Hits while Dodge Rolling" (PoB: MORE
# DamageTakenWhenHit under Condition InDodgeRoll, ModCache 9581, which nothing sets). Dodge rolling is part of the fight
# (Search.rolling_share's "Time spent dodge rolling" row), so by the user's 9 Nov rule (floors count only what is up all
# fight) it stays out of the floors and PoB's numbers: a row with the time-weighted figure, rolling share x N% (the hits
# that land while rolling taken as spread evenly over the fight: MEDIUM-LOW). Not rolling: 0.
ROLLING_LESS = re.compile(r"^Take (\d+(?:\.\d+)?)% less Damage from Hits while Dodge Rolling$", re.I)


def rolling_less_row(less: float, rolling: float) -> dict:
    """The play row for ROLLING_LESS at the build's rolling share (0..1)."""
    rolling = min(max(rolling, 0.0), 1.0)
    return {"label": "Less damage from hits while Dodge Rolling (a row, not in the defences)*",
            "value": (f"{less:g}% less while rolling, {rolling:.0%} of the fight: {less * rolling:.1f}% less on average"
                      if rolling > 0 else f"0: the build doesn't dodge roll ({less:g}% less while rolling)"),
            "basis": (f"{less:g}% less damage from hits while Dodge Rolling x the {rolling:.0%} of the fight spent rolling "
                      "(the hits that land while rolling taken as spread evenly); part-time, so in no defence floor and "
                      "not in PoB's numbers (the floors count only what is up all fight); without it: 0"),
            "confidence": "MEDIUM-LOW (the rolling share; the hits' timing)",
            "play": (f"dodge roll through the boss's hits: {less:g}% less damage from those that land while rolling"
                     if rolling > 0 else "")}


# Charms' "Also grants N Guard" (MECHC38; COV41's routed UNDERCOUNT: CharmGuardWhileActive1-6, a charm prefix; PoB parses it
# to nothing, ModCache 6552-6557): N Guard x (1 + increased Guard gained) while the charm's effect is up (the charm's
# own uptime, flasks.uptime: its uses at its charges and its trigger, x its duration), capped at 200% of maximum Life
# (KeywordPopups Guard). Part-time: a row (the manager's 17 Nov ruling (b)); up all fight (99.5%+): in the floors through
# PoB's Guard pool (full_guard_mods, ruling (ii)); with two Guard sources up all fight the larger counts (one Guard buff at
# a time). The Guard lasting exactly the charm's effect is MEDIUM (the line reads as part of the charm's effect).
CHARM_GUARD = re.compile(r"^Also grants (\d+) Guard\s*$", re.I | re.M)


def charm_guard_row(name: str, amount: float, texts: list[str], up: float, life: float) -> tuple[float, dict]:
    """(the Guard while the charm is up, its play row) for CHARM_GUARD (the note above)."""
    inc = _guard_inc(texts)
    raw = amount * (1 + inc / 100)
    guard = min(raw, GUARD_CAP * max(life, 0.0))
    whole = bool(full_guard_mods(guard, up))
    label = ("Guard from a charm (up all fight: in the defences)*" if whole
             else "Guard from a charm (a row, not in the defences)*")
    value = (f"{guard:,.0f} Guard, up all fight: in the defences (PoB's Guard pool takes each hit first)" if whole
             else f"{guard:,.0f} Guard while {name} is up, {up:.0%} of the fight ({guard * up:,.0f} on average)")
    return guard, {"label": label, "value": value,
                   "basis": (f"{name}: Also grants {amount:g} Guard" + (f" x {1 + inc / 100:.2f} (increased Guard gained)"
                                                                         if inc else "")
                             + (f", capped at 200% of maximum Life (or Energy Shield: R33-6) from {raw:,.0f}"
                                if raw > guard + 0.5 else "")
                             + f", while the charm's effect is up ({up:.0%} of the fight, its uses and duration); "
                             + ("up all fight, so counted in the defence floors (the manager's 18 Nov ruling (ii))"
                                if whole else "part-time, so in no defence floor (the manager's 17 Nov ruling (b))")
                             + "; PoB counts none; without it: no Guard"),
                   "confidence": "MEDIUM (the Guard lasting the charm's effect; the charm's uptime)",
                   "play": (f"keep {name} up: {guard:,.0f} Guard while it lasts (without it: no Guard)" if up > 0 else "")}


# Emergent Vigour (MECHC38; COV41's routed UNDERCOUNT, 6.0): the rune's "Gain Maximum Energy Shield equal to 50% of total
# Strength Requirements of Equipped Armour Items" (PoB ModRunes.lua 8697-8724: one trade line, two text lines, neither
# parsed, so it never counts). PoB's own form for Armour ("Gain Armour equal to 150% of total Strength Requirements of
# Equipped Boots, Gloves and Helmet", ModCache 7739) is Armour BASE x its StrRequirementsOn<slot> outputs (CalcPerform
# 1940: each equipped item's Strength requirement after its requirement modifiers); this mirrors it: EnergyShield BASE =
# N% x the sum over the armour slots. Which slots are "Armour Items": Helmet, Body Armour, Gloves, Boots counted; a
# Shield / Buckler / Focus in the off hand not counted (UNVERIFIED whether the game counts them: the lower reading).
VIGOUR_FIRST = re.compile(r"^Gain Maximum Energy Shield equal to (\d+(?:\.\d+)?)% of total(?: Strength Requirements of "
                          r"Equipped Armour Items)?$", re.I)
VIGOUR_SECOND = re.compile(r"^Strength Requirements of Equipped Armour Items$", re.I)
VIGOUR_SLOTS = ("Helmet", "Body Armour", "Gloves", "Boots")
VIGOUR_FIELDS = tuple(f"StrRequirementsOn{slot}" for slot in VIGOUR_SLOTS)
# (MECHC39, R31-13 (b): KeywordPopups EquipArmour, "Armour ... include Helmets, Body Armours, Gloves, Boots, Shields and
# Foci"; Bucklers are Shields: HIGH) the off hand counts when it holds a Shield or Focus
VIGOUR_OFFHAND_FIELD = "StrRequirementsOnWeapon 2"
VIGOUR_OFFHAND_TYPES = ("Shield", "Focus")


def emergent_vigour(texts: list[str], stats: dict, off_hand_armour: bool = False) -> tuple[float, dict | None]:
    """(maximum Energy Shield to add as PoB's EnergyShield BASE, its row) for Emergent Vigour (the note above); (0, None)
    without the line. `stats`: PoB's VIGOUR_FIELDS."""
    lines = [t.strip() for t in texts]
    found = [m for t in lines if (m := VIGOUR_FIRST.match(t))]
    if not found or not (any("Strength Requirements" in m.group(0) for m in found)
                         or any(VIGOUR_SECOND.match(t) for t in lines)):
        return 0.0, None
    pct = sum(float(m.group(1)) for m in found)
    reqs = {slot: max(_num(stats.get(field)), 0.0) for slot, field in zip(VIGOUR_SLOTS, VIGOUR_FIELDS)}
    if off_hand_armour:  # (a Shield or Focus: R31-13 (b))
        reqs["off-hand Shield / Focus"] = max(_num(stats.get(VIGOUR_OFFHAND_FIELD)), 0.0)
    total = sum(reqs.values())
    es = pct / 100 * total
    return es, {"label": "Emergent Vigour: Energy Shield from Strength requirements*",
                "value": f"+{es:,.0f} maximum Energy Shield (base, before your increases)",
                "basis": (f"{pct:g}% of the {total:,.0f} total Strength requirement of your equipped armour ("
                          + ", ".join(f"{slot} {v:,.0f}" for slot, v in reqs.items() if v) + ") = "
                          f"{es:,.0f}, as PoB's own Armour form of the line (EnergyShield BASE, scaled by your increased "
                          "Energy Shield); an off-hand Shield / Focus counts (KeywordPopups EquipArmour: HIGH); PoB parses none of it; "
                          "without it: 0"),
                "confidence": "MEDIUM (the slots; base vs final Energy Shield read as PoB reads the Armour form)",
                "play": ""}


# Soul Mantle (MECHC38; COV40's routed OVERCOUNT, a drawback no model read; RESEARCH31 R31-2,
# research/soul-mantle-totem-curses.md; the manager 02:47 18 Nov): "Inflicts a random Curse on you when your Totems die,
# ignoring Curse limit" (game 26650308 UniqueRandomlyCursedWhenTotemsDie1, randomly_cursed_when_totems_die_curse_level 1;
# PoB parses nothing). Curse LEVEL 1 (HIGH): Enfeeble 20% less damage dealt, Temporal Chains 40% less action speed,
# Elemental Weakness -40% to all Elemental Resistances, Vulnerability -16 Armour, Despair -35% Chaos Resistance, 6 s each
# (GrantedEffectStatSetsPerLevel level 1); the pool isn't in the data: the five, equally likely (MEDIUM-LOW); they stack.
# A totem expiring or being replaced counts as dying (MEDIUM): deaths a second from the shared rate (MECHC41:
# assumptions.totem_deaths / models.totem_deaths, MECHB39: every totem skill's totems up over their own durations,
# re-placed as they expire or at the search's chosen press rate); Splinter Totem's dying splinters not counted (an
# undercount of the drawback, named); boss kills of totems not counted (LOW, no source). Each
# curse up 1 - e^(-deaths x D / 5), D = 6 s x your "Duration of Curses on you", x your "effect of Curses on you"; immune
# to Curses: none. Counted (the manager's ruling: "curses land" is the HEADLINE): the average in the headline (Damage /
# ActionSpeed MORE, the resistances and Armour BASE), each curse up 7%+ IN FULL in the defence floors (ruling (i)'s
# reading for a harm, the same floor-only switch as the boss's Shock: Assumptions.ledger_part_time under
# SOUL_MANTLE_FLOOR_KEY); "N% increased (Totem) Damage per Curse on you" at the expected count (PoB's
# Multiplier:CurseOnSelf). Row: since 0.3.0 a level-1 curse "does not apply to enemies above level 20"; if that gate also
# holds for curses on a player above level 20, the drawback is 0 (UNVERIFIED either way).
SOUL_MANTLE = re.compile(r"^Inflicts a random Curse on you when your Totems die(?:, ignoring Curse limit)?$", re.I)
SOUL_MANTLE_FLOOR_KEY = "SoulMantleCursesInFullForTheFloors"
SOUL_CURSES = (  # (name, PoB mod name, kind, value at level 1, what)
    ("Enfeeble", "Damage", "MORE", -20.0, "20% less damage dealt"),
    ("Temporal Chains", "ActionSpeed", "MORE", -40.0, "40% less action speed"),
    ("Elemental Weakness", "ElementalResist", "BASE", -40.0, "-40% to all Elemental Resistances"),
    ("Vulnerability", "Armour", "BASE", -16.0, "-16 Armour"),
    ("Despair", "ChaosResist", "BASE", -35.0, "-35% Chaos Resistance"),
)
SOUL_CURSE_SECONDS = 6.0
CURSE_EFFECT_ON_YOU = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) effect of Curses on you$", re.I)
CURSE_DURATION_ON_YOU = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) Duration of Curses on you$", re.I)
CURSE_IMMUNE = re.compile(r"^(?:You are )?(?:Immune to Curses|Unaffected by Curses)$", re.I)
PER_CURSE_ON_YOU = re.compile(r"per Curse on you$", re.I)


def soul_mantle(texts: list[str], deaths: float, deaths_how: str, full: bool = False) -> tuple[list[dict], float, list[dict]]:
    """(PoB mods, the share of the fight the most-up curse is on you (for the floors' switch; 0 under 7%), rows) for Soul
    Mantle (the note above). `full`: the floors' recalculation (each curse up 7%+ in full)."""
    from builder.search import models

    lines = [t.strip() for t in texts]
    if not any(SOUL_MANTLE.match(t) for t in lines):
        return [], 0.0, []

    def signed(pattern) -> float:
        return sum(float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)
                   for t in lines if (m := pattern.match(t)))

    effect = max(1 + signed(CURSE_EFFECT_ON_YOU) / 100, 0.0)
    seconds = SOUL_CURSE_SECONDS * max(1 + signed(CURSE_DURATION_ON_YOU) / 100, 0.0)
    immune = any(CURSE_IMMUNE.match(t) for t in lines)
    k = len(SOUL_CURSES)
    up = 0.0 if immune or deaths <= 0 else 1 - math.exp(-deaths * seconds / k)
    whole = 0.07 <= up < 0.995
    scale = 1.0 if (full and whole) else up
    mods = [models.raw_mod(name, kind, value * effect * scale, tag="SoulMantle")
            for _, name, kind, value, _ in SOUL_CURSES if up > 0]
    count = k * up
    if count > 0 and any(PER_CURSE_ON_YOU.search(t) for t in lines):
        mods.append(models.raw_mod("Multiplier:CurseOnSelf", "BASE", count, tag="SoulMantle"))
    each = "; ".join(f"{c} {what}" for c, _, _, _, what in SOUL_CURSES)
    if immune:
        value = "none: you're immune to Curses"
    elif up <= 0:
        value = f"none: no totem dies ({deaths_how})"
    else:
        value = (f"each of 5 curses on you {up:.0%} of the fight ({count:.2f} on average): {20 * effect * up:.1f}% less "
                 f"damage, {40 * effect * up:.1f}% less action speed, {40 * effect * up:.0f}% lower elemental and "
                 f"{35 * effect * up:.0f}% lower Chaos resistance on average"
                 + ("; the defence floors read each in full" if whole else ""))
    rows = [{"label": "Soul Mantle: random Curses on you when your totems die*", "value": value,
             "basis": (f"{deaths:.3f} totem deaths a second ({deaths_how}; expiry and replacement count as dying, MEDIUM, "
                       f"R31-2), each a random level-1 curse of five, equally likely (MEDIUM-LOW: {each}), "
                       f"{seconds:g} s each, x{effect:.2f} effect on you; they stack (ignoring the Curse limit): each up "
                       f"1 - e^(-{deaths:.3f} x {seconds:g} / 5) = {up:.0%}; counted at the average in the headline"
                       + (", in full in the defence floors (a harm at its worst, ruling (i)'s reading)" if whole else "")
                       + "; Splinter Totem's splinters' deaths not counted; without it: none"),
             "confidence": "MEDIUM-LOW (the pool; expiry as death MEDIUM)",
             "play": (f"each totem that expires or is replaced curses you ({deaths:.2f} a second): a curse-immunity source "
                      "or reduced Curse effect / duration on you cuts it; without Soul Mantle: no curses"
                      if up > 0 else "")},
            {"label": "Soul Mantle: if the level gate blocks the curses*",
             "value": "0 curses on you (a reading, not counted)",
             "basis": ("since 0.3.0 a level-1 curse 'does not apply to enemies above level 20' "
                       "(skill_curses_cannot_apply_to_targets_above_level 20); whether that holds for a curse on a "
                       "player above level 20 is UNVERIFIED (0.5 guides imply the curses land: R31-2); the headline counts "
                       "them landing (the manager's ruling)"),
             "confidence": "UNVERIFIED", "play": ""}]
    return mods, (up if whole else 0.0), rows



# Divinity (MECHC39; Immaculate Adherence, the manager's ruling 18 Nov 02:47 "MODEL IT DIRECTLY, no plan needed";
# RESEARCH31 R31-3, research/divinity-immaculate-adherence.md). KeywordPopups Divinity (game 26650308): "Divinity can be
# spent for skills, similar to Mana. Divinity inherently Regenerates at a rate of 25% per second." The amulet (current,
# poe2db = PoB Uniques/amulet.lua): "Cannot have Energy Shield", "Convert 100% of maximum Energy Shield to maximum
# Divinity", "(0-100)% increased maximum Divinity", "20% reduced maximum Divinity per Corrupted Item Equipped", "Skills
# Cost Divinity instead of Mana or Life", "100% of Damage is taken from Mana before Life". Its only player source
# (checked by RESEARCH31: every Stats id holding "divinity" (25) and their users in Mods, PassiveSkills, ActiveSkills;
# poe2db's current page; "You gain Divinity ... Divine Charges" is PoE1's buff, a separate thing in conditions.py).
# What PoB does: "Cannot have Energy Shield" (EnergyShield 0; MaximumEnergyShield keeps the pre-cap value, CalcDefence
# 1460-1464) and the Mana-before-Life buffer; the conversion, the increase and the cost line are unparsed (nil, or {}
# with leftover text, so never applied: ModCache 1866, 3080, 7000, 9365; the joined "... per Corrupted Item Equipped
# Skills Cost Divinity ..." key, ModCache 3081, reads Mana INC -20 but with leftover text, so PoB never applies it either,
# and the bridge's sourceLines sends every item line on its own). So in PoB the skills still cost Mana or Life.
# Modelled: maximum Divinity = PoB's MaximumEnergyShield x N% converted x (1 + (increased - 20 x Corrupted items
# equipped) / 100) (the two read as adding, MEDIUM; Corrupted: PoB's CorruptedItem count, items other than jewels,
# flasks and charms); regeneration 25% of it a second (HIGH; no player modifier exists, checked by RESEARCH31; Mana /
# Energy Shield recovery read as not refilling it, MEDIUM-LOW). Every Mana and Life cost goes to Divinity 1:1 (MEDIUM):
# PoB's ManaCost and LifeCost MORE -100 (tag Divinity), so the Mana pool stays full (Archmage, "while on Full Mana"),
# the sustain floor sees no cost, a Life cost resets no recharge, and the Mana-spent feeds (Arcane Surge, Mana Tempest,
# Archon of the Storm, "per N Mana spent") read 0: the manager's headline ("Mana-spent feeds 0 as the HEADLINE and the
# old feed as a row"; R32-1: not Mana spent, MEDIUM: the game tracks its own divinity_spent_per_minute
# stat apart). The cost the main skill would pay a second (PoB's ManaPerSecondCost + LifePerSecondCost without
# the tag) against the regeneration: above it, the main skill is held to the share Divinity pays (DPS MORE, the Runic
# Ward hold's form; the starting pool's burst not counted, a fight-long average). Other skills' costs not counted (as
# every Mana-spent reader here: an undercount of the spend, named).
DIVINITY_COST = re.compile(r"^Skills Cost Divinity instead of Mana or Life$", re.I)
DIVINITY_CONVERT = re.compile(r"^Convert (\d+(?:\.\d+)?)% of maximum Energy Shield to maximum Divinity$", re.I)
DIVINITY_INC = re.compile(r"^(\d+(?:\.\d+)?)% (increased|reduced) maximum Divinity$", re.I)
DIVINITY_PER_CORRUPTED = re.compile(
    r"^(\d+(?:\.\d+)?)% (increased|reduced) maximum Divinity per Corrupted Item Equipped$", re.I)
DIVINITY_REGEN = 0.25  # of maximum Divinity a second (KeywordPopups Divinity; HIGH)
DIVINITY_TAG = "Divinity"
DIVINITY_COST_FIELDS = ("ManaPerSecondCost", "LifePerSecondCost")
DIVINITY_FIELDS = ("MaximumEnergyShield",)


def has_divinity(texts: list[str]) -> bool:
    return any(DIVINITY_COST.match(t.strip()) for t in texts)


def corrupted_items(slots: dict) -> int:
    """PoB's CorruptedItem count (CalcSetup 1694-1712: items other than jewels, flasks and charms) from bridge.equipped's
    raw texts: a slot whose item has a line "Corrupted". Weapon-swap slots left out (not in hand in the fight)."""
    count = 0
    for slot, raw in (slots or {}).items():
        if slot.startswith(("Flask", "Charm", "Jewel")) or "Swap" in slot or not isinstance(raw, str):
            continue
        if any(line.strip() == "Corrupted" for line in raw.splitlines()):
            count += 1
    return count


def _signed(m: re.Match) -> float:
    return float(m.group(1)) * (1 if m.group(2).lower() == "increased" else -1)


def divinity(texts: list[str], max_es: float, corrupted: int, mana_cost: float, life_cost: float,
             main: str | None) -> tuple[list[dict], list[dict], float]:
    """(PoB mods, rows, maximum Divinity) for "Skills Cost Divinity instead of Mana or Life" (the note above); ([], [], 0)
    without it. `max_es`: PoB's MaximumEnergyShield; `mana_cost` / `life_cost`: the main skill's costs a second without
    this model's own mods; `main`: the main skill, held to the share Divinity pays."""
    from builder.search import models

    lines = [t.strip() for t in texts]
    if not any(DIVINITY_COST.match(t) for t in lines):
        return [], [], 0.0
    convert = sum(float(m.group(1)) for t in lines if (m := DIVINITY_CONVERT.match(t)))
    inc = sum(_signed(m) for t in lines if (m := DIVINITY_INC.match(t)))
    per = sum(_signed(m) for t in lines if (m := DIVINITY_PER_CORRUPTED.match(t)))
    total_inc = inc + per * corrupted
    pool = max(max(max_es, 0.0) * convert / 100 * (1 + total_inc / 100), 0.0)
    regen = DIVINITY_REGEN * pool
    mana_cost, life_cost = max(mana_cost, 0.0), max(life_cost, 0.0)
    cost = mana_cost + life_cost
    mods = [models.raw_mod("ManaCost", "MORE", -100.0, tag=DIVINITY_TAG),
            models.raw_mod("LifeCost", "MORE", -100.0, tag=DIVINITY_TAG)]
    paid = 1.0 if cost <= 0 or regen >= cost else regen / cost
    if paid < 1.0 and main:
        mods.append(models.raw_mod("DPS", "MORE", 100 * (paid - 1), main, tag=DIVINITY_TAG))
    scale = ""
    if total_inc:
        scale = (f" x (1 {'+' if total_inc >= 0 else '-'} {abs(total_inc):g}%: {inc:+g}% increased"
                 + (f", {per:+g}% x {corrupted} Corrupted item(s)" if per and corrupted else "") + ")")
    pool_how = (f"PoB's {max_es:,.0f} maximum Energy Shield x {convert:g}% converted{scale} = {pool:,.0f} maximum "
                f"Divinity, regenerating 25% a second = {regen:,.0f}/s")
    if cost <= 0:
        value = f"{pool:,.0f} maximum Divinity; the main skill costs nothing"
    elif paid >= 1.0:
        value = f"{cost:,.1f} cost a second from {regen:,.0f} Divinity a second: all paid"
    else:
        value = (f"{cost:,.1f} cost a second from {regen:,.0f} Divinity a second: {paid:.0%} paid, "
                 f"{main or 'the main skill'} held to that share")
    rows = [{"label": "Divinity pays your skill costs*", "value": value,
             "basis": (pool_how + f"; the main skill's Mana + Life costs ({mana_cost:,.1f} + {life_cost:,.1f} a second, "
                       "PoB) paid 1:1 in Divinity (MEDIUM), so Mana and Life pay nothing (PoB: ManaCost / LifeCost 100% "
                       "less) and the Mana pool stays full; Mana / Energy Shield recovery read as not refilling Divinity "
                       "(MEDIUM-LOW); other skills' costs not counted; you have no Energy Shield (PoB applies it). "
                       "Without the model: PoB charges the costs to Mana / Life as if the amulet had no cost line"),
             "confidence": "MEDIUM (costs 1:1; the increases adding)", "play": ""},
            {"label": "Mana-spent effects under Divinity*", "value": "0: no Mana is spent or cost",
             "basis": ("Divinity spent is not Mana spent (R32-1 / R31-8b, MEDIUM: the game tracks divinity_spent_per_minute apart and bridges another resource into Mana spent only with its own stat or wording; none for Divinity). So "
                       "Arcane Surge, Archon of the Storm and \"per N Mana spent\" lines get nothing; Incinerate gains no fuel; "
                       "Mana Tempest Empowers only Mana-costing Spells, so none; and \"% of Mana Cost\" / \"Spells that cost Mana\" "
                       "lines (Manaweave, Arc / Crackling Lance, Forbidden Rite / Dark Pact) read the zeroed Mana cost (UNVERIFIED). "
                       f"The other reading (Divinity spent counts): {cost:,.1f} spent a second, as without the amulet's cost line"),
             "confidence": "MEDIUM (not Mana spent); UNVERIFIED (the Mana-cost lines)", "play": ""}]
    return mods, rows, pool



# "... per Rune Socketed in Equipped Items" and the other weapon set (MECHC40; COV42 / RESEARCH R31-14, UNVERIFIED whether
# the inactive set's Runes count; "Equipped" in KeywordPopups doesn't address weapon sets). The HEADLINE counts the active
# set only (PoB's item list, bridge setAugmentCounts' RunesInEquipment: the lower count); this row gives the count with
# the other set's Runes too. A Rune is a socketed augment whose name holds the word "Rune" (PoB writes each socket as
# "Rune: <name>" in an item's text; Soul Cores, Idols, Abyssal Eyes and the Raven-Touched Shard don't: MEDIUM).
PER_RUNE_LINE = re.compile(r"\bRune Socketed in Equipped Items$", re.I)  # (also the game's wrapped second line)
SOCKET_LINE = re.compile(r"^Rune: (.+)$", re.M)
SWAP_SLOTS = ("Weapon 1 Swap", "Weapon 2 Swap")


def swap_runes_row(texts: list[str], slots: dict) -> dict | None:
    """The play-guide row for "per Rune Socketed in Equipped Items" lines with the other weapon set's Runes (the comment
    above), None without such a line. `slots`: bridge.equipped's {slot: raw}."""
    lines = [t.strip() for t in texts if PER_RUNE_LINE.search(t.strip())]
    if not lines:
        return None
    runes = [m.group(1).strip() for slot in SWAP_SLOTS for m in SOCKET_LINE.finditer(slots.get(slot) or "")
             if re.search(r"\bRune\b", m.group(1)) and m.group(1).strip() != "None"]
    return {"label": "With the other weapon set's Runes*",
            "value": (f"+{len(runes)} Rune{'s' if len(runes) != 1 else ''} for: " + "; ".join(sorted(set(lines)))
                      if runes else "+0: no Rune in the other weapon set"),
            "basis": ("counted: the active weapon set's Runes only (the lower count); whether the inactive set's Runes "
                      "count is UNVERIFIED (RESEARCH R31-14)" + (f"; the other set holds {', '.join(runes)}" if runes
                                                                   else "")),
            "confidence": "UNVERIFIED", "play": ""}



# Guard from events (MECHC40; the manager 17:55: "the Guard skills PoB computes nothing for (an UNDERCOUNT of defences):
# do them, by impact"; PoB sets no Guard pool from any skill: checked, grep GuardAbsorb over its src, only CalcDefence /
# CalcSections read it). Each source gives a Guard per event G (x "increased Guard gained", capped at 200% of maximum
# Life: KeywordPopups Guard), lasting D s (x "Buffs on you expire N% slower": Guard is a Buff), at the build's own event
# rate r (CLAUDE.md: no flat rates; no source, 0). Its share of the fight: min(1, r x D) for paced events (a cooldown,
# a timer, flask uses), 1 - e^(-r x D) for random ones (the boss's hits, stuns). Part-time Guard is a row (the manager's
# 17 Nov ruling (b)); up all fight (99.5%+) it counts in the floors through PoB's Guard pool (ruling (ii)), the largest
# such source only (one Guard buff at a time). A hit spends Guard until the next grant (named in the rows).
# - Fortifying Cry (ActiveSkills FortifyingCryPlayer; PoB's stat text "Grants {0} Guard for every 5 enemy Power in range,
#   counting up to {1} Power", shielding_cry_grant_X_guard_per_5_mp, cap 20; "Guard duration is {0} seconds",
#   base_secondary_skill_effect_duration 4000): a lone Unique boss's 20 Power (models.boss_power) gives 4 x X, at the
#   cry's own uses (its cooldown, or the search's press rate; with K2-9's bypass in PoB's cooldown).
# - Vanguard I / II (SupportVanguardPlayer / Two: gain_X_guard_on_heavy_stun_per_10_strength 4 / 6,
#   gain_X_guard_on_stun_per_10_strength 2, guard_on_stun_guard_duration_ms 4000): on the supported skill's Heavy Stuns
#   of the boss (models.heavy_stun_rate, when Vanguard supports the main skill; a side skill isn't rated: 0, said).
#   The plain-stun stat isn't counted: the models give no light-stun rate on a boss (an UNDERCOUNT kept, said).
# - Carved Mischief's "Gain Guard equal to 10% of maximum Life for 4 seconds on taking Savage Hit" (KeywordPopups
#   SavageHit: "a Hit that removes at least 15% of Maximum Life"): the boss's landed hits of each type whose size after
#   your mitigation (PoB's <type>TakenHit) reaches 15% of maximum Life.
# - Heavy Drinker's "Life Flasks applied to you grant Guard for 4 seconds equal to 8% of the Life Recovery per Second
#   they apply": the Life flask's recovery over its duration, at its uses (flasks.uses_per_second).
# - Warding Rune of Protection's "Every 4 seconds, gain Guard equal to 20% of maximum Runic Ward for 2 seconds": PoB's
#   Ward, every N s for D s (a timer: D / N of the fight).
FORTIFYING_CRY = "Fortifying Cry"
FORTIFY_GUARD_STAT, FORTIFY_POWER_STEP, FORTIFY_POWER_CAP, FORTIFY_SECONDS = "shielding_cry_grant_X_guard_per_5_mp", 5, 20, 4.0
VANGUARD = {"Vanguard I": (4.0, 2.0), "Vanguard II": (6.0, 2.0)}  # (Guard per 10 Strength on Heavy Stun, on Stun)
VANGUARD_SECONDS = 4.0
SAVAGE_GUARD = re.compile(r"^Gain Guard equal to (\d+(?:\.\d+)?)% of maximum Life for (\d+(?:\.\d+)?) seconds on taking "
                          r"Savage Hit$", re.I)
SAVAGE_SHARE = 0.15  # KeywordPopups SavageHit (HIGH)
DRINKER_GUARD = re.compile(r"^Life Flasks applied to you grant Guard for (\d+(?:\.\d+)?) seconds equal to (\d+(?:\.\d+)?)% "
                           r"of the Life Recovery per Second they apply$", re.I)
WARD_TIMER_GUARD = re.compile(r"^Every (\d+(?:\.\d+)?) seconds, gain Guard equal to (\d+(?:\.\d+)?)% of maximum Runic Ward "
                              r"for (\d+(?:\.\d+)?) seconds$", re.I)
# (MECHC41, the bigger sources by the audit's impact)
# - Duality (Warding Quarterstaff; Mods UniqueGainXGuardPerComboLostUsingSkills1,
#   unique_gain_x_guard_for_500_ms_per_combo_lost_using_skills 500-1000): "Gain (500-1000) Guard for 0.5 seconds per
#   Combo expended when using Skills". Read as X Guard lasting 0.5 s for each Combo the use expends (the duration scales
#   with the Combo, as its twin line "Gain Finality for 0.5 seconds per Combo expended" can only mean; MEDIUM-LOW: "X
#   Guard per Combo for 0.5 s" is the other reading, said in the row). The Combo a use expends: the Combo skill's own
#   requirement (usage.combo_rate's rotation builds that much a use), at combo_rate's uses a second. Paced.
# - Olroth's Resolve (Ultimate Life Flask; Mods UniqueFlaskWardGainedAsGuard1: local_unique_flask_gain_%_of_current_ward_
#   as_guard_when_flask_effect_ends 100, ..._duration_ms 10000): "Gain Guard equal to Current Runic Ward for 10 seconds
#   when Effect ends", at the flask's uses (flasks.uses_per_second). The current Runic Ward: the maximum less the average
#   deficit the build's Ward spends leave (ward_current); nothing spends it, full (KeywordPopups Ward: it regenerates 5%
#   a second; the flask's own "Regenerate N% of maximum Runic Ward per second during Effect" only fills it faster). Its
#   older "Excess Life Recovery added as Guard" line is on the pre-0.5.0 versions only (PoB's Uniques/flask.lua versions
#   1-2; patch 0.4.0 changed it to 20 s); a build carrying it isn't counted (said in no row: PoB's current item lacks it).
# - Hollow Shell gives YOU no Guard: "Pulse spends 100% of your Runic Ward to grant Allies Guard equal to {0}% of Runic
#   Ward spent" (its stat description, PoB hollow_shell.lua 24 / the game's hollow_shell.csd), and KeywordPopups Allies:
#   "You do not count as your own Ally" (checked: both game files; the skill's own text "grants nearby Allies Guard",
#   ActiveSkills hollow_shell). Its Guard goes to your minions (a minion defence, not modelled), so no row here.
# - Decree of Flight (Ancient Leggings; Mods UniqueGuardFromMissingEnergyShieldOnDodge1, gain_guard_equal_to_%_of_your_
#   missing_energy_shield_for_4_seconds_on_dodge_roll 10-20, and UniqueMaximumGuardBasedOnEnergyShield1): "Gain Guard
#   equal to (10-20)% of missing Energy Shield for 4 seconds when you Dodge Roll" and "Maximum amount of Guard is based on
#   maximum Energy Shield instead" (the cap 200% of maximum Energy Shield). The rolls: the build relies on rolling (its
#   line is assumptions.ROLL_RELIANT's "when you Dodge Roll"), so one every models.DODGE_EVERY s (the user's default, 7 Nov).
#   The Energy Shield missing at a roll: none while on Full Energy Shield (full_es_share, MEDIUM); below full, half of it
#   on average (the ES left spread from 0 to full, as conditions.current_es_percent's other reading; MEDIUM-LOW); so only
#   the rolls below full grant it (event_guard's `share`). Rows: missing all of it below full (the upper reading), none.
DECREE_GUARD = re.compile(r"^Gain Guard equal to (\d+(?:\.\d+)?)% of missing Energy Shield for (\d+(?:\.\d+)?) seconds when "
                          r"you Dodge Roll$", re.I)
DECREE_CAP = re.compile(r"^Maximum amount of Guard is based on maximum Energy Shield instead$", re.I)


def guard_cap_base(texts: list[str], life: float, es: float) -> float:
    """The pool GUARD_CAP multiplies: maximum Energy Shield with "Maximum amount of Guard is based on maximum Energy Shield
    instead" (RESEARCH33 R33-6, every Guard source: MEDIUM), else maximum Life."""
    return es if any(DECREE_CAP.match(t.strip()) for t in texts) else life
# - Morrigan's Insight (MECHC41; a Lineage support, PoB SupportMorrigansInsight: "trigger Nature's Exchange when
#   consuming Freeze on enemies"; consume_enemy_freeze_to_trigger_morrigan_ward; its supported skill never Freezes):
#   Nature's Exchange (TriggeredMorrigansInsightPlayer: cooldown 8 s, one stored use; base_skill_effect_duration 8000,
#   "Buff duration is {0} seconds"; morrigan_ward_gain_X_guard "Grants {0} Guard", 531 at level 20) at each Freeze of
#   the boss the supported skill consumes: the boss's Freezes a second (models.buildup_rate: the main skill's and the
#   other groups' buildup, MECH-A's) held to the cooldown (/ PoB's cooldown recovery), taken as consumed by the
#   supported skill's next hit when it's the main skill (MEDIUM); on a side skill its hits on the Frozen boss aren't
#   rated: 0, said. Paced. Its Cold Thorns and 50% more damage vs Frozen are not this row (PoB reads the latter).
MORRIGANS_INSIGHT, NATURES_EXCHANGE = "Morrigan's Insight", "Nature's Exchange"
NATURES_GUARD_STAT, NATURES_COOLDOWN, NATURES_SECONDS = "morrigan_ward_gain_X_guard", 8.0, 8.0
# - Helbrym's Hide (MECHC41; a Lineage support for Shapeshift skills, SupportHelbrymsHidePlayer: soak 2000 ms, Guard
#   4000 ms, 50% of damage taken as Guard, 3% as Rage). KeywordPopups HelbrymsComposure: "While you have this Buff,
#   damage you take is stored. When the Buff expires, you gain Guard equal to 50% of the stored damage and Rage equal to
#   3% of the stored damage"; the gem: granted "when Supported Skills cause you to Shapeshift", and "Shapeshifting into a
#   different form before the Buff expires causes it to be removed without effect". So one per shift into the supported
#   skill's form, at the build's form changes a second h (conditions.human_shifts: the shifts into and back out of a
#   form come at the same rate), kept only if no other shift comes within the 2 s soak: shifts taken as random,
#   e^(-2h) (RESEARCH33 R33-7: Human form is "a different form", MEDIUM; animal to animal too, HIGH, so h counts every
#   change out of the form, conditions.human_shifts(any_form=True); the row without the loss gives h). Stored:
#   the boss's damage a second after your mitigation (objective.boss_taken) x 2 s. (MECHC44, an UNDERCOUNT of Rage
#   before) its Rage, 3% of the stored damage (support_hide_of_helbrym_gain_rage_from_%_of_damage_taken 3, PoB's
#   sup_str.lua and the keyword), at the same kept grants a second, feeds conditions.rage_held as a gain
#   (stats["_helbrym_rage"]; the Rage maximum caps it there). Conditions.helbrym_composure gives both. A build that never
#   shifts (no other-form skill pressed): 0, said.
HELBRYMS_HIDE = "Helbrym's Hide"
HELBRYM_SOAK, HELBRYM_SECONDS, HELBRYM_SHARE, HELBRYM_RAGE_SHARE = 2.0, 4.0, 0.50, 0.03
# "Prevent +5% of Damage from Deflected Hits if you've Deflected no Hits Recently" (MECHC41; MECHC38 / MECHC39's
# rows-only leftover; the body-armour Idol in PoB's ModRunes.lua 10047-10051, base_damage_%_deflected_if_you_have_not_
# deflected_recently; PoB parses nothing of it, so no overcount). Recently = the last 4 s (KeywordPopups Recently). A
# Deflected Hit meets it when no other was Deflected in the 4 s before: the boss's landed hits a second x your Deflect
# chance d, taken as random, so e^(-4 d) of them (MEDIUM). Part-time, so a row (the manager's 17 Nov ruling (b)), not
# in the floors.
DEFLECT_RESTED = re.compile(r"^Prevent \+(\d+(?:\.\d+)?)% of Damage from Deflected Hits if you've Deflected no Hits "
                            r"Recently$", re.I)
DEFLECT_RESTED_SECONDS = 4.0


def deflect_rested_row(texts: list[str], landed: float, chance: float, effect: float) -> dict | None:
    """The row for DEFLECT_RESTED (the note above): `landed` the boss's landed hits a second, `chance` your Deflect
    chance %, `effect` PoB's DeflectEffect %; None without the line."""
    joined = [t.strip() for t in texts]
    # (PoB's item text may split it across two lines: "... if you've" / "Deflected no Hits Recently")
    joined += [f"{a} {b}" for a, b in zip(joined, joined[1:]) if b.lower() == "deflected no hits recently"]
    found = [m for t in joined if (m := DEFLECT_RESTED.match(t))]
    if not found:
        return None
    pct = sum(float(m.group(1)) for m in found)
    d = max(landed, 0.0) * min(max(chance, 0.0), 100.0) / 100
    share = math.exp(-DEFLECT_RESTED_SECONDS * d) if d > 0 else 0.0
    return {"label": "Deflection after none Recently (a row, not in the defences)*",
            "value": (f"+{pct:g}% of a Deflected Hit's damage prevented ({effect:g}% -> {effect + pct:g}%) on {share:.0%} "
                      "of your Deflected Hits" if d > 0 else "0: no hit is Deflected"),
            "basis": (f"the boss's {landed:.2f} landed hits a second x your {chance:.0f}% Deflect chance = {d:.2f} Deflected "
                      f"a second; a Deflected Hit with none in the {DEFLECT_RESTED_SECONDS:g} s before: e^(-4 x {d:.2f}) = "
                      f"{share:.0%} (random hits, MEDIUM); part-time, so in no defence floor (the manager's 17 Nov ruling "
                      "(b)); PoB counts none; without it: none"),
            "confidence": "MEDIUM", "play": ""}


# - Citaqualotl's Thesis (MECHC41; RESEARCH33 R33-1, the manager 01:05 19 Nov): a Soul Core whose Boots line is
#   "Sacrifice 10% of maximum Life to gain that much Guard when you Dodge Roll" (HIGH): Guard = N% of maximum Life on
#   every roll, no cooldown; PoB reads none of it. Its duration is in no source (UNVERIFIED): every comparable "gain
#   Guard when ..." line says 4 s, Decree of Flight's roll line included, so 4 s is the headline (MEDIUM-LOW) with 2 s
#   and 10 s as rows. The rolls: the build relies on rolling (assumptions.ROLL_RELIANT's "when you Dodge Roll"), one
#   every models.DODGE_EVERY s. The Life sacrificed is a cost per roll, not damage taken (KeywordPopups Sacrifice: "does
#   not count as taking damage", and it can't take you below 1 Life): N% x maximum Life x rolls a second, as the NEGATIVE
#   recovery field LifeSacrificed (life_sacrifice_income), which the sustain and recovery floors net off.
CITAQUALOTL_GUARD = re.compile(r"^Sacrifice (\d+(?:\.\d+)?)% of maximum Life to gain that much Guard when you Dodge Roll$",
                               re.I)
CITAQUALOTL_SECONDS, CITAQUALOTL_ROWS = 4.0, (2.0, 10.0)


# (MECHC47; COV48's MECH-C row 11.8) Igniferis' "Every 4 seconds, Recover 1 Life for every 0.2 Life Recovery per second
# from Regeneration" beside its "Life Recovery from Regeneration is not applied": PoB applies the second (ModCache 8493,
# UnaffectedByLifeRegen: LifeRegenRecovery 0, CalcDefence 1752) but not the first (ModCache 7414, nil), so the floors
# read no Life from Regeneration at all, an UNDERCOUNT. PoB still gives the regeneration it would have (output LifeRegen,
# CalcDefence 1742), so the bursts are LifeRegen / 0.2 Life every 4 s: LifeRegen x 1.25 a second (LifeOnEventRecovery;
# Life Recovery rate modifiers not applied to the burst, UNVERIFIED whether they do: the lower reading). HIGH for the
# arithmetic; the bursts' timing against the boss's hits isn't modelled (the floors are per-second rates).
REGEN_BURST = re.compile(r"^Every (\d+(?:\.\d+)?) seconds, Recover (\d+(?:\.\d+)?) Life for every (\d+(?:\.\d+)?) Life "
                         r"Recovery per second from Regeneration$", re.I)


def regen_burst_income(income: "Income", texts: list[str], regen: float) -> None:
    """Igniferis' Life bursts from the build's Life Regeneration (REGEN_BURST's note), into LifeOnEventRecovery."""
    for t in texts:
        if (m := REGEN_BURST.match(t.strip())) and regen > 0:
            every, amount, per = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if every > 0 and per > 0:
                income.add("LifeOnEventRecovery", regen / per * amount / every,
                           f"Recover {amount:g} Life every {every:g} s per {per:g} Life Regeneration ({regen:,.1f}/s, "
                           "PoB's LifeRegen before its 'not applied' line): Igniferis")


# (MECHC47; COV48's MECH-C row 13.4) Demon Stitcher's "Sacrifice (5-15)% of maximum Life to gain that much Energy Shield
# when you Cast a Spell" (PoB's Uniques/gloves.lua 174; PoB leaves it unparsed): each Spell cast moves N% of maximum Life
# into Energy Shield (KeywordPopups Sacrifice: not damage taken). Read at the main skill's casts when it is a Spell, at
# its share of the fight (other Spells' casts not read: an undercount of both sides, said); the Life as LifeSacrificed
# (negative), the Energy Shield as EnergyShieldOnEventRecovery (the floors read both). The ES gained past its maximum
# isn't held (the gain is income for the floors, as the other event sources). MEDIUM.
STITCHER_ES = re.compile(r"^Sacrifice (\d+(?:\.\d+)?)% of maximum Life to gain that much Energy Shield when you Cast a "
                         r"Spell$", re.I)


def life_sacrifice_income(income: "Income", texts: list[str], life: float, rolls: float, casts: float = 0.0,
                          cast_how: str = "") -> None:
    """Citaqualotl's Thesis' Life sacrificed a second into LifeSacrificed (negative), and Demon Stitcher's (STITCHER_ES:
    `casts` Spell casts a second, the Energy Shield it gains too); nothing without the lines."""
    pct = sum(float(m.group(1)) for t in texts if (m := CITAQUALOTL_GUARD.match(t.strip())))
    drain = pct / 100 * max(life, 0.0) * max(rolls, 0.0)
    if drain > 0:
        income.rates["LifeSacrificed"] -= drain
        income.parts.append(f"Life sacrificed for Guard: {pct:g}% of {life:,.0f} a Dodge Roll x {rolls:.2f} rolls/s: "
                            f"-{drain:,.1f}/s (Citaqualotl's Thesis)")
    pct = sum(float(m.group(1)) for t in texts if (m := STITCHER_ES.match(t.strip())))
    if pct <= 0:
        return
    moved = pct / 100 * max(life, 0.0) * max(casts, 0.0)
    if moved <= 0:
        income.parts.append(f"Sacrifice {pct:g}% of maximum Life for Energy Shield on each Spell cast: the main skill "
                            f"casts no Spell ({cast_how or 'not a Spell'}), 0")
        return
    income.rates["LifeSacrificed"] -= moved
    income.rates["EnergyShieldOnEventRecovery"] += moved
    income.parts.append(f"Life sacrificed for Energy Shield: {pct:g}% of {life:,.0f} a Spell cast x {casts:.2f} casts/s "
                        f"({cast_how}): -{moved:,.1f} Life/s, +{moved:,.1f} Energy Shield/s (Demon Stitcher; other Spells' "
                        "casts not read)")


COMBO_GUARD = re.compile(r"^Gain (\d+(?:\.\d+)?) Guard for (\d+(?:\.\d+)?) seconds per Combo expended when using Skills$",
                         re.I)
WARD_END_GUARD = re.compile(r"^Gain Guard equal to Current Runic Ward for (\d+(?:\.\d+)?) seconds when Effect ends$", re.I)


def ward_current(pool: float, sustain: dict) -> tuple[float, str]:
    """(the average current Runic Ward, how): the maximum less each spend's average deficit while it refills (a spend c
    at p a second refilled at the build's Ward recovery R leaves p x c^2 / (2 R) missing on average; spends taken one at
    a time from full, a lower bound where they overlap); 0 when the spends outrun the recovery (held near empty)."""
    income = _num(sustain.get("WardRecoveryPerSecond"))
    uses = [(v[1], v[2]) for k, v in sustain.items() if not k.startswith("_") and k != "WardRecoveryPerSecond"
            and isinstance(v, (tuple, list)) and len(v) == 3 and v[1] > 0 and v[2] > 0]
    spent = sum(p * c for p, c in uses)
    if pool <= 0:
        return 0.0, "no Runic Ward"
    if spent <= 0:
        return pool, f"nothing spends it: full, {pool:,.0f}"
    if income <= spent:
        return 0.0, f"spends {spent:,.1f} Ward/s against {income:,.1f} recovered: held near empty, 0"
    deficit = sum(p * min(c, pool) ** 2 / (2 * income) for p, c in uses)
    now = max(0.0, pool - deficit)
    return now, (f"{pool:,.0f} maximum less {deficit:,.0f} missing on average (spends {spent:,.1f} Ward/s, "
                 f"{income:,.1f} recovered): {now:,.0f}")


def event_guard(source: str, amount: float, rate: float, seconds: float, texts: list[str], life: float, how: str,
                paced: bool = True, expiry: float = 1.0, share: float = 1.0) -> tuple[float, float, dict]:
    """(Guard, its share of the fight, its row) for one event-driven Guard source (the note above). `life`: the pool its
    cap is 200% of; `share`: the share of the fight its events grant it at all (Decree of Flight: below full ES)."""
    inc = _guard_inc(texts)
    raw = max(amount, 0.0) * (1 + inc / 100)
    guard = min(raw, GUARD_CAP * max(life, 0.0))
    length = max(seconds, 0.0) * expiry
    r = max(rate, 0.0)
    up = (min(1.0, r * length) if paced else 1 - math.exp(-r * length)) if guard > 0 else 0.0
    up *= min(1.0, max(share, 0.0))
    whole = bool(full_guard_mods(guard, up))
    basis = (f"{how}; {guard:,.0f} Guard" + (f" (x {1 + inc / 100:.2f}, increased Guard gained)" if inc else "")
             + (f", capped at 200% of maximum Life (or Energy Shield: R33-6) from {raw:,.0f}" if raw > guard + 0.5 else "")
             + f" for {length:.3g} s" + (f" (x {expiry:.2f}, Buffs expire slower)" if expiry != 1 else "")
             + f" at {r:.3g} a second: up {up:.0%} of the fight ("
             + ("paced: rate x duration" if paced else "random events: 1 - e^(-rate x duration)") + "); "
             + ("up all fight, so counted in the defence floors through PoB's Guard pool (the manager's 18 Nov ruling "
                "(ii)); a hit spends Guard until the next grant" if whole
                else "part-time, so in no defence floor (the manager's 17 Nov ruling (b))")
             + "; PoB counts none; without it: no Guard")
    row = {"label": f"Guard from {source}" + (" (up all fight: in the defences)*" if whole
                                               else " (a row, not in the defences)*"),
           "value": (f"{guard:,.0f} Guard, up all fight" if whole else
                     f"{guard:,.0f} Guard, up {up:.0%} of the fight ({guard * up:,.0f} on average)" if up > 0
                     else "0: no event grants it here"),
           "basis": basis, "confidence": "MEDIUM (the event rate; the Guard's timing)",
           "play": f"{source}: {guard:,.0f} Guard while it lasts" if up > 0 else ""}
    return guard, up, row


# "while Frozen" / "while Bleeding" lines on you (MECHC42; MECH89's never-on rows, UNDERCOUNTS: PoB parses them under
# Condition Frozen / Bleeding, which only their configuration boxes set). Carriers (checked: every Stats id holding
# while_frozen / while_bleeding / when_frozen / if_frozen, and their Mods, PassiveSkills, skill stat sets and Soul Core
# users, game 26650308): physical_damage_reduction_rating_while_frozen (Flesh Withstands: "+500 to Armour while Frozen"),
# armour_+%_while_bleeding (Unfettered, Vale Dweller, the "Armour while Bleeding" smalls, a jewel-domain unique mod),
# life_regeneration_per_minute_%_while_frozen (Exclusive LifeRegenerationWhileFrozenUnique__1 and the mutated Vaal
# hand-wraps suffix: whether either can be got in a new league is UNVERIFIED, a research question; the model reads the
# text, so it counts only if the search offers it). The share Frozen is boss_hits.boss_freeze_on_you's, Bleeding
# boss_hits.bleeding_on_you's.
# - Armour: part-time, so a row only (the floors count only what is up all fight: the user's 9 Nov rule, as Bulwark).
# - Life Regeneration while Frozen: averaged over the fight, handed to PoB as plain "Regenerate N% of maximum Life per
#   second" x the Frozen share (as passive_models' flask-used regen; MEDIUM).
# Not modelled (said): "20% increased Movement Speed while Bleeding" (movement only), "Gain a Frenzy Charge on Hit while
# Bleeding" and "N% increased Physical Damage while Frozen" (no PoB unique carries either: Exclusive mods only; and a
# Frozen player doesn't hit), "Cannot be Shocked while Frozen" (the Shock while Frozen is a sliver of the fight).
FROZEN_ARMOUR = re.compile(r"^\+(\d+(?:\.\d+)?) to Armour while Frozen$", re.I)
BLEEDING_ARMOUR = re.compile(r"^(\d+(?:\.\d+)?)% increased Armour while Bleeding$", re.I)
FROZEN_REGEN = re.compile(r"^Regenerate (\d+(?:\.\d+)?)% of maximum Life per second while Frozen$", re.I)


def frozen_bleeding_lines(texts: list[str], frozen: float, bleeding: tuple[float, str]) -> tuple[list[dict], list[dict]]:
    """(mods, rows) for the build's "while Frozen" / "while Bleeding" lines (the note above). `frozen`: the share of the
    fight Frozen; `bleeding`: (share Bleeding, how)."""
    from builder.search import models

    lines = [t.strip() for t in texts]
    flat = sum(float(m.group(1)) for t in lines if (m := FROZEN_ARMOUR.match(t)))
    inc = sum(float(m.group(1)) for t in lines if (m := BLEEDING_ARMOUR.match(t)))
    regen = sum(float(m.group(1)) for t in lines if (m := FROZEN_REGEN.match(t)))
    mods, rows = [], []
    if flat > 0:
        rows.append({"label": f"+{flat:g} to Armour while Frozen (a row, not in the defences)*",
                     "value": f"+{flat:g} Armour {frozen:.0%} of the fight",
                     "basis": (f"Frozen {frozen:.0%} of the fight (the boss's Freezes on you); part-time, so not in the "
                               "defence floors (they count only what is up all fight); PoB counts none (its box is off)"),
                     "confidence": "MEDIUM-LOW (the Freeze share)", "play": ""})
    share, how = bleeding
    if inc > 0:
        rows.append({"label": f"{inc:g}% increased Armour while Bleeding (a row, not in the defences)*",
                     "value": f"{inc:g}% increased Armour {share:.0%} of the fight",
                     "basis": (f"{how}; part-time, so not in the defence floors (they count only what is up all "
                               "fight); PoB counts none (its box is off)" if share > 0 else
                               f"never Bleeding: {how}; without a Bleed source it adds nothing"),
                     "confidence": "MEDIUM (the Bleed share)", "play": ""})
    if regen > 0:
        if frozen > 0:
            mods.append(models.line("Regenerate 1% of maximum Life per second", regen * frozen))
        rows.append({"label": f"Regenerate {regen:g}% of maximum Life per second while Frozen*",
                     "value": f"{regen * frozen:.2f}% of maximum Life a second on average (Frozen {frozen:.0%} of the fight)",
                     "basis": "the line x the share of the fight the boss's Freezes hold you, averaged over the fight; "
                              "PoB counts none (its box is off)",
                     "confidence": "MEDIUM-LOW (the Freeze share)", "play": ""})
    return mods, rows


# "if you've Killed Recently" lines and "Lose N% of maximum Mana / Energy Shield on Kill" (MECHC42; MECH89's never-on
# rows; the manager's 10:55 ruling: each line on its own, never through PoB's KilledRecently box, which also switches on
# Onslaught on Kill, Phasing, the "Gain N Life on Kill per Level" lines and others: ModCache holds 25 lines under it).
# PoB's own parses stay off (its box is never set). Kills are the build's own (models.kill_rate: the boss preset's adds,
# 0 against a lone boss, whose death ends the fight; plus your own minions Killed, counted as Kills by lines that don't say
# "enemy": the ruling on A17-4, LOW). "Recently" is the past 4 s (KeywordPopups Recently); kills taken as random: on
# 1 - e^(-4 k) of the fight (MEDIUM). Carriers (checked: PoB's ModCache keys holding "Killed Recently" and "on Kill"
# under Condition KilledRecently; the never-on audit's rows): Primal Growth / its small (Area of Effect), Adrenaline Rush
# (Attack Speed; its Movement Speed and the other Movement Speed lines not modelled: movement only), the crit smalls
# (Critical Hit Chance), Arvil's Wheel and the Exclusive UniqueMaximumManaOnKillPercent (Mana lost), the Energy Shield
# twin (Exclusive). Not here: Bond of the Wolf's Companion Onslaught on Kill (a companion's kills, MECH-B's), "You have
# Phasing if you've Killed Recently" (evasion-side, no number our floors read), the "for each Shocked Enemy you've Killed
# Recently" and "Killed a Cursed Enemy Recently" lines (PoE1 leftovers / other conditions: not checked here, UNVERIFIED).
KILLED_RECENTLY = re.compile(r"^(\d+(?:\.\d+)?)% increased (Area of Effect|Attack Speed|Critical Hit Chance) if "
                             r"you(?:'ve| have) killed Recently$", re.I)
KILL_LOSS = re.compile(r"^Lose (\d+(?:\.\d+)?)% of maximum (Mana|Energy Shield) on Kill$", re.I)


def killed_recently_share(kills: float) -> float:
    """The share of the fight with a Kill in the past 4 s at `kills` a second (random)."""
    return 1 - math.exp(-4.0 * kills) if kills > 0 else 0.0


def killed_recently_lines(texts: list[str], kills: tuple) -> tuple[list[dict], list[dict]]:
    """(mods, rows) for the build's "N% increased ... if you've Killed Recently" lines at its own kills (`kills`:
    models.kill_rate's (enemies/s, all/s, how))."""
    from builder.search import models

    found: dict[str, float] = {}
    for t in texts:
        if m := KILLED_RECENTLY.match(t.strip()):
            what = m.group(2).title().replace("Of", "of")
            found[what] = found.get(what, 0.0) + float(m.group(1))
    if not found:
        return [], []
    total, how = float(kills[1] or 0.0), kills[2] if len(kills) > 2 else ""
    share = killed_recently_share(total)
    mods = [models.line(f"1% increased {what}", value * share) for what, value in found.items() if share > 0]
    rows = [{"label": f"{value:g}% increased {what} if you've Killed Recently*",
             "value": f"{value * share:.2f}% on average (a Kill in the past 4 s {share:.0%} of the fight)",
             "basis": (f"{total:.3f} Kills a second ({how}) x 4 s, kills at random: 1 - e^(-4 k); PoB counts none (its "
                       "box is never set)" if share > 0 else f"no Kills before the boss dies ({how}): 0"),
             "play": ("Kill the boss's adds (or your own minions, LOW) to keep it on: with them "
                      f"{value * share:.2f}% on average, without them 0." if share > 0 else ""),
             "confidence": "LOW (the boss preset's add kills; own minions Killed counted, LOW)"}
            for what, value in found.items()]
    return mods, rows


def kill_loss_income(income: "Income", texts: list[str], mana: float, es: float, kills: tuple) -> list[dict]:
    """Arvil's Wheel's "Lose N% of maximum Mana on Kill" into ManaLostOnKill (NEGATIVE) at all the build's Kills (a
    downside: the cautious side counts own minions Killed too); the Energy Shield twin a row only (the Energy Shield
    recovery pool doesn't read event fields yet). Returns its rows."""
    total, how = float(kills[1] or 0.0), kills[2] if len(kills) > 2 else ""
    rows = []
    for t in texts:
        if not (m := KILL_LOSS.match(t.strip())):
            continue
        pct, pool = float(m.group(1)), m.group(2).title()
        size = mana if pool == "Mana" else es
        lost = pct / 100 * max(size, 0.0) * max(total, 0.0)
        if pool == "Mana" and lost > 0:
            income.rates["ManaLostOnKill"] -= lost
            income.parts.append(f"Mana lost on Kill: {pct:g}% of {size:,.0f} x {total:.3f} Kills/s: -{lost:,.1f}/s")
        rows.append({"label": f"Lose {pct:g}% of maximum {pool} on Kill*",
                     "value": f"-{lost:,.1f} {pool} a second" if lost > 0 else "0: no Kills before the boss dies",
                     "basis": (f"{pct:g}% of your {size:,.0f} maximum {pool} x {total:.3f} Kills a second ({how})"
                               + ("; counted in the Mana sustain" if pool == "Mana" else
                                  "; a row only: the Energy Shield recovery doesn't read on-Kill events yet")),
                     "confidence": "LOW (the boss preset's add kills)", "play": ""})
    return rows


# Immaterial (MECHC42; MECH89's never-on row, 3.0): "50% increased Evasion Rating if Energy Shield Recharge has started in
# the past 2 seconds" (PoB: Condition EnergyShieldRechargePastTwoSec, set only by its box; the stat evasion_rating_+%_if_
# energy_shield_recharge_started_in_past_2_seconds has the one carrier, checked: Stats ids holding recharge_started /
# begin_es_recharge and their users in every table, game 26650308; PoB's ModCache: this line only). Recharge starts after
# PoB's EnergyShieldRechargeDelay with no hit: with the boss's landed hits at random (h a second, boss_hits.landing_hits)
# each hit is followed by a start when the next comes after the delay d, so starts come at h e^(-h d) a second, and one
# has started in the past 2 s on 1 - e^(-2 h e^(-h d)) of the fight (MEDIUM; the item / charm starts of flasks.
# es_recharge_starts not added: the lower reading). No Energy Shield, or no landed hit: 0. Part-time, so a row only (the
# floors count only what is up all fight).
RECHARGE_STARTED_EVASION = re.compile(r"^(\d+(?:\.\d+)?)% increased Evasion Rating if Energy Shield Recharge has "
                                      r"started in the past 2 seconds$", re.I)


def recharge_started_row(texts: list[str], stats: dict) -> dict | None:
    """Immaterial's row (the note above); None without the line."""
    from builder.search import boss_hits

    value = sum(float(m.group(1)) for t in texts if (m := RECHARGE_STARTED_EVASION.match(t.strip())))
    if value <= 0:
        return None
    es = max(float(stats.get("EnergyShield") or 0.0), 0.0)
    delay = float(stats.get("EnergyShieldRechargeDelay") or 0.0) or 4.0
    hits = boss_hits.landing_hits(stats) if es > 0 else 0.0
    starts = hits * math.exp(-hits * delay) if hits > 0 else 0.0
    share = 1 - math.exp(-2.0 * starts) if starts > 0 else 0.0
    return {"label": f"{value:g}% increased Evasion Rating after Energy Shield Recharge starts (a row, not in the defences)*",
            "value": f"{value:g}% increased Evasion Rating {share:.0%} of the fight",
            "basis": (f"{hits:.2f} landed boss hits a second, each followed by a Recharge start when the next comes after "
                      f"your {delay:.2f} s delay: {starts:.3f} starts a second, one in the past 2 s on {share:.0%} of the "
                      "fight (item and charm starts not added: the lower reading); part-time, so not in the defence "
                      "floors; PoB counts none (its box is off)" if es > 0 else "no Energy Shield: never Recharges, 0"),
            "confidence": "MEDIUM (random hits)", "play": ""}


# The raised-Block rune (MECHC42; MECHC38 / MECHC39's rows-only leftover): "+N% to maximum Block chance if you've Blocked
# with a raised Shield Recently" (the Soul Core's additional_maximum_block_%_if_blocked_with_active_block_recently, its
# one carrier: checked, every Stats id holding active_block / raised / shield_raised and their users in every table, game
# 26650308; PoB's ModCache has no key for it: unparsed, so no overcount). Raised Blocks a second: the boss's hits (one
# every enemySkillTime) x the share of the fight the shield is raised (1 for a channelled shield skill, ACTIVE_BLOCK_
# CHANNELS; Raise Shield taps x RAISED_TAP_SECONDS; else 0), every Blockable hit Blocked while raised (as raised_block_
# taken); a raised Block in the past 4 s on 1 - e^(-4 r) of the fight (MEDIUM-LOW). Part-time, and it moves Block only
# where Block chance reaches its maximum: a row only.
RAISED_MAX_BLOCK = re.compile(r"^\+(\d+(?:\.\d+)?)% to maximum Block chance if you've Blocked with a raised Shield "
                              r"Recently$", re.I)


def raised_max_block_row(texts: list[str], raised: float, every: float, block: float, block_max: float) -> dict | None:
    """The rune's row (the note above): `raised` the share of the fight the shield is up, `every` the boss's seconds
    between hits, `block` / `block_max` PoB's Block chance and its maximum (%); None without the line."""
    value = sum(float(m.group(1)) for t in texts if (m := RAISED_MAX_BLOCK.match(t.strip())))
    if value <= 0:
        return None
    rate = min(max(raised, 0.0), 1.0) / (every or 0.7)
    share = 1 - math.exp(-4.0 * rate) if rate > 0 else 0.0
    capped = block_max > 0 and block >= block_max
    return {"label": f"+{value:g}% to maximum Block chance after a Block with your shield raised (a row)*",
            "value": f"+{value:g}% maximum Block {share:.0%} of the fight",
            "basis": ((f"{rate:.2f} raised Blocks a second (the shield up {raised:.0%} of the fight, every Blockable hit "
                       f"Blocked then), one in the past 4 s on {share:.0%} of the fight" if share > 0 else
                       "the build never raises its shield (no channelled shield skill, no Raise Shield taps): 0")
                      + f"; your Block chance {block:g}% against its {block_max:g}% maximum: "
                      + ("at the maximum, so this would raise it" if capped else "below the maximum, so it adds nothing now")
                      + "; part-time, so not in the defence floors; PoB doesn't read the line"),
            "confidence": "MEDIUM-LOW (the raised share from the build's own shield use)",
            "play": ("Raise your shield into the boss's hits to keep it on" if share > 0 and capped else "")}


# Sacrifice of Blood (MECHC43; MECH89's never-on rows, an UNDERCOUNT: PoB parses it under Multiplier:LifeSpentRecently,
# ModCache 9222, set only by the Configuration box multiplierLifeSpentRecently, which the optimizer never sets; that box
# also feeds "if you've spent Life recently" lines and an Arcane Surge grant (ModParser 2047 / 4447), so it stays off):
# "Regenerate 1 Life per second per 16 Life spent in the past 4 seconds" (a Timeless jewel's conquered passive, PoB
# LegionPassives abyss_keystone_4; the model reads the text, wherever it comes from). The Life spent in the past 4 s:
# 4 s x PoB's LifePerSecondCost, the main skill's Life cost a second at its use rate (other skills' Life costs aren't
# counted: the lower reading, an UNDERCOUNT named in the row), steady. A plain "Regenerate N Life per second" for PoB.
LIFE_SPENT_REGEN = re.compile(r"^Regenerate (\d+(?:\.\d+)?) Life per second per (\d+) Life spent in the past 4 seconds$",
                              re.I)


def life_spent_regen(texts: list[str], life_cost: float) -> tuple[list[dict], list[dict]]:
    """(mods, rows) for the LIFE_SPENT_REGEN lines at `life_cost` (Life spent a second); nothing without a line."""
    from builder.search import models

    found = [(float(m.group(1)), float(m.group(2))) for t in texts if (m := LIFE_SPENT_REGEN.match(t.strip()))]
    if not found:
        return [], []
    spent = 4.0 * max(life_cost, 0.0)
    regen = sum(each * (spent / per) for each, per in found if per > 0)
    mods = [models.line("Regenerate 1 Life per second", regen)] if regen > 0 else []
    return mods, [{
        "label": "Regenerate Life per Life spent in the past 4 seconds*",
        "value": f"{regen:.1f} Life per second",
        "basis": (f"{spent:.0f} Life spent in the past 4 s (the main skill's {life_cost:.1f} Life cost a second x 4, "
                  "steady; other skills' Life costs not counted: the lower reading)" if spent > 0 else
                  "the main skill costs no Life: 0 (other skills' Life costs not counted)")
                 + "; PoB's box for it is never set",
        "confidence": "MEDIUM (the main skill's costs only)",
        "play": ("Keep using your Life-costing skill: without it 0 regeneration" if regen > 0 else "")}]

