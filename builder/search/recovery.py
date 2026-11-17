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

import math
import re
from dataclasses import dataclass, field

# The fields, per second (for SEARCH: objective.SUSTAIN_POOLS / RECOVERY_POOLS read them from the build's stats)
FIELDS = {
    "Life": ("LifeOnKillRecovery", "LifeRemnantRecovery", "CharmLifeRecoveryRate", "LifeOnEventRecovery",
             "LifeOnConsumeRecovery", "LifePreventedRecoup", "LifeFlameLeechRecovery",
             "LifeFlaskDrain",  # (MECHC26: Uhtred's Chalice's Life loss, NEGATIVE: flasks.flask_life_drain)
             "LifeRiftRecovery"),  # (MECHC29: Temporal Rift's reset to 4 s ago, rift_income)
    "Mana": ("ManaOnKillRecovery", "ManaRemnantRecovery", "CharmManaRecoveryRate", "ManaOnEventRecovery",
             "ManaOnConsumeRecovery", "ManaFlameLeechRecovery", "ManaRiftRecovery"),
    # (MECHC17) Energy Shield on events: "Regenerate N% of maximum Energy Shield over 1 second when Stunned" (hit_income).
    # For SEARCH: objective.RECOVERY_POOLS["EnergyShield"] doesn't read it yet
    "EnergyShield": ("EnergyShieldOnEventRecovery", "EnergyShieldPreventedRecoup",  # (MECHC21: Scarred Faith's)
                     # (MECHC23, AUDIT33 A33-1) the Life flask's recovery applied to Energy Shield (Shavronne's Satchel:
                     # flasks.es_recovery_per_second, at the flask's own use rate)
                     "EnergyShieldFlaskRecovery",
                     "EnergyShieldGhostDanceRegen",  # (MECHC27: Ghost Dance's regeneration, ghost_dance_income)
                     "EnergyShieldSacrificed",  # (MECHC27, NEGATIVE: Eldritch Empowerment's sacrifice, sacrifice_income)
                     "EnergyShieldRiftRecovery"),  # (MECHC29: Temporal Rift, rift_income)
}
# The boss's Leech resistance (MECHC26; PATCH_CHECK H2-1, an OVERCOUNT before: neither PoB nor we applied any), a
# NEGATIVE field per pool: what PoB's {pool}LeechRate holds that the boss resists (leech_resisted). In FIELDS, so the
# sustain and recovery floors (objective's ADDED_RECOVERY) take it off once; Income.pool leaves it out (its sums are the
# event sources, and PoB's leech isn't one of them).
NEGATIVE_FIELDS = {"LifeFlaskDrain", "EnergyShieldSacrificed"}  # (losses, not event income: Income.pool leaves them out)
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
# (MECHC21: MINION_REVIVED was missing here, so a build whose only spent line it was counted nothing: an UNDERCOUNT)
SPENT_LINES = (ENDURANCE_SPENT, POWER_SPENT, GLORY_SPENT, COMBO_SPENT, CORPSE_LIFE, CORPSE_MANA, OFFERING_LIFE,
               MINION_REVIVED, RAGE_SHIFT)
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
HIT_LINES = (MISSING_BEFORE_HIT, ARMOUR_ON_BLOCK, CAST_REGEN, CAST_LEECH, STUNNED_LIFE, STUNNED_ES, PREVENTED_ES,
             PREVENTED_LIFE, PREVENTED_LIFE_TRIBUTE)
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
        rate = (timed + kills) * multiplier
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
                 rage: tuple[float, str] = (0.0, "")) -> None:
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


def hit_income(income: Income, stats: dict, texts: list[str], *, landed: float = 0.0, life_per_hit: float = 0.0,
               blocked: float = 0.0, casts: float = 0.0, cast_how: str = "", stunned: float = 0.0,
               stunned_how: str = "", refill: float = 0.0, refill_how: str = "", prevented: float = 0.0,
               prevented_how: str = "", power: float = 0.0, power_how: str = "") -> None:
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
# Power, Acolyte of Chayula) sets it; without one, a third each (UNVERIFIED: the game files give no weights; LOW). Not a
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
    return {c: 1 / 3 for c in ("red", "blue", "purple")}, "a third each colour (no weights in the game files: UNVERIFIED, LOW)"


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
def full_es_share(stats: dict) -> tuple[float, str]:
    """(share of the fight on Full Energy Shield, how); stats as full_life_share's."""
    from builder.search import objective

    if _num(stats.get("EnergyShield")) <= 0:
        return 0.0, "no Energy Shield: the box stays off, as PoB has it (UNVERIFIED whether 0 of 0 counts as full)"
    taken, hits, _ = objective.boss_taken(stats)
    dips = sum(v * (1 - min(max(_num(stats.get(f"{t}EnergyShieldBypass")), 0.0), 100.0) / 100) for t, v in taken.items())
    fields, _ = objective.RECOVERY_POOLS["EnergyShield"]
    recharged = objective.es_recharge(stats, hits)[0]
    back = sum(_num(stats.get(f)) for f in fields) + objective.recoup(stats, taken, "EnergyShield") + recharged
    steady = objective.own_loss(stats)["EnergyShield"]
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


# Ghost Dance (MECHC27; PATCH_CHECK H1-9's rest): "Regenerate Energy Shield equal to 2% of your Evasion Rating per second
# if you have lost a Ghost Shroud Recently" (GhostDancePlayer: skill_base_ghost_dance_grants_%_evasion_as_es_regeneration_
# per_minute_if_have_lost_ghost_dance_shroud_recently 120; patch 0.5.0, the newest entry: "When Hit, lose a Ghost Shroud",
# a Shroud every 11.7-10.1 s at gem levels 4-20, at most 3, "Modifiers to Cooldown Recovery Rate also apply"). PoB has
# only its "Do you use Ghost Shrouds?" box and no regeneration (ConfigOptions 854; CalcPerform 978): an UNDERCOUNT
# before. Shrouds are lost to the boss's landed hits at the "when hit" benefit pace (boss_hits.gain_hits, h a second):
# with hits at least as fast as Shrouds come (g = 1 / interval), each new Shroud is lost at the next hit, so a loss every
# 1 / g s and "Recently" (4 s) covers min(1, 4 g) of the fight; with slower hits a Shroud is always waiting, so every hit
# is a loss and the share is 1 - e^(-4 h) (Poisson hits). ES a second = 2% x Evasion x that share. MEDIUM; not read: the
# build's Cooldown Recovery Rate on the interval (an undercount), regeneration-rate modifiers on the amount (taken as
# written, as the other sources here).
GHOST_DANCE = "Ghost Dance"
GHOST_INTERVAL_STAT = "base_cooldown_modifiable_repeat_interval_ms"
GHOST_REGEN_STAT = "skill_base_ghost_dance_grants_%_evasion_as_es_regeneration_per_minute_if_have_lost_ghost_dance_shroud_recently"


def ghost_dance_share(interval: float, hits: float, recently: float = 4.0) -> float:
    """Share of the fight you've lost a Ghost Shroud in the past `recently` s: a Shroud every `interval` s, landed hits
    `hits` a second."""
    if interval <= 0 or hits <= 0:
        return 0.0
    gain = 1 / interval
    return min(1.0, recently * gain) if hits >= gain else 1 - math.exp(-recently * hits)


def ghost_dance_income(income: Income, level_stats: dict, evasion: float, hits: float, hits_how: str) -> None:
    """Ghost Dance's Energy Shield regeneration a second into EnergyShieldGhostDanceRegen."""
    interval = _num(level_stats.get(GHOST_INTERVAL_STAT)) / 1000
    pct = _num(level_stats.get(GHOST_REGEN_STAT)) / 60 or 2.0
    share = ghost_dance_share(interval, hits)
    income.add("EnergyShieldGhostDanceRegen", pct / 100 * max(evasion, 0.0) * share,
               f"Ghost Dance: {pct:g}% of {evasion:,.0f} Evasion as Energy Shield a second while a Shroud was lost in the "
               f"past 4 s, {share:.0%} of the fight (a Shroud every {interval:.1f} s; {hits_how}; MEDIUM)")


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


# Twister's hits on one boss per cast (MECHC33; RESEARCH28 R28-10, twister-hits-on-boss.md, the manager 16 Nov 10:56; the
# manager's earlier ruling: no flat number, work it out from the twister's movement and area and the boss's radius).
# TwisterPlayer (game files): base_skill_effect_duration 3000, active_skill_base_area_of_effect_radius 5 (0.5 m),
# twister_hit_interval_ms 660 ("Twisters fired at the same time can Hit the same target no more than once every 0.66
# seconds": HIGH, shared by the group: MEDIUM-HIGH), projectile speed 7.5 m/s (poe2vault: MEDIUM). Cap k = floor(D /
# 0.66) + 1. HEADLINE, an open arena: a straight pass overlaps the boss for 2 (R + r) / v, 0.2-0.4 s, under one window,
# so 1 hit a cast, PoB's number (MEDIUM). The walled row (LOW, a named assumption: twisters bounce around a roam space of
# a 6 m radius): hits = 1 + (k - 1)(1 - e^(-N x 2 rho x v x 0.66 / A)), rho = the twister's radius (PoB's
# AreaOfEffectRadiusMetres, else 0.5 m x sqrt(area modifier)) + the boss's hit radius (bosses.hit_radius), v = 7.5 m/s x
# PoB's ProjectileSpeedMod, N = PoB's ProjectileCount, D = PoB's Duration. Each hit rolls the build's on-hit lines (R26-45
# (d), ASSUMED), so on-hit charge gains scale by the row's hits too (a row: the headline keeps 1). Twisters made by
# Whirlwinds or Barrage repeats as separate groups: UNVERIFIED, not counted.
TWISTER = "Twister"
TWISTER_FIELDS = ("ProjectileCount", "ProjectileSpeedMod", "Duration", "AreaOfEffectRadiusMetres", "AreaOfEffectMod")
TWISTER_SPEED, TWISTER_RADIUS, TWISTER_INTERVAL, TWISTER_DURATION = 7.5, 0.5, 0.66, 3.0
TWISTER_ROAM = 6.0  # metres: the walled row's roam space radius (LOW, RESEARCH28's named assumption)


def twister_hits(stats: dict, boss_radius: float) -> tuple[float, float, str]:
    """(hits on the boss a cast: headline 1, the walled row, how) from PoB's Twister outputs."""
    duration = _num(stats.get("Duration")) or TWISTER_DURATION
    k = math.floor(duration / TWISTER_INTERVAL + 1e-9) + 1
    count = max(_num(stats.get("ProjectileCount")), 1.0)
    speed = TWISTER_SPEED * (_num(stats.get("ProjectileSpeedMod")) or 1.0)
    own = _num(stats.get("AreaOfEffectRadiusMetres")) or TWISTER_RADIUS * math.sqrt(_num(stats.get("AreaOfEffectMod")) or 1.0)
    rho = own + max(boss_radius, 0.0)
    area = math.pi * TWISTER_ROAM ** 2
    walled = 1 + (k - 1) * (1 - math.exp(-count * 2 * rho * speed * TWISTER_INTERVAL / area))
    return 1.0, walled, (
        f"1 hit a cast in an open arena (a pass crosses the boss in 2 x ({own:.2f} + {boss_radius:.2f}) m / {speed:.1f} "
        f"m/s = {2 * rho / speed:.2f} s, under one {TWISTER_INTERVAL:g} s window: PoB's number, MEDIUM); in a walled arena "
        f"(twisters bouncing in a {TWISTER_ROAM:g} m roam space: LOW, a named assumption) {walled:.2f} a cast, at most "
        f"{k} ({duration:.2f} s / {TWISTER_INTERVAL:g} s + 1, shared by the {count:g} twister(s) of a cast); each hit rolls "
        f"your on-hit lines (on-hit charges x{walled:.2f} in that row; ASSUMED)")


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
            low = min(cap, per * each * (1 + guard_inc / 100))
            high = min(cap, per * each) * (1 + guard_inc / 100)
            other = (f" (the cap taken after 'increased Guard gained': UNVERIFIED; before it {high:.1f}%)"
                     if abs(high - low) > 0.05 else "")
            gains.append(f"Guard of {low:.1f}% of maximum Life for {last:g} s after each sprint{other}, up "
                         f"{min(1.0, reps * last):.0%} of the fight")
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
