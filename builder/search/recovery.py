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

import re
from dataclasses import dataclass, field

# The fields, per second (for SEARCH: objective.SUSTAIN_POOLS / RECOVERY_POOLS read them from the build's stats)
FIELDS = {
    "Life": ("LifeOnKillRecovery", "LifeRemnantRecovery", "CharmLifeRecoveryRate", "LifeOnEventRecovery",
             "LifeOnConsumeRecovery", "LifePreventedRecoup"),
    "Mana": ("ManaOnKillRecovery", "ManaRemnantRecovery", "CharmManaRecoveryRate", "ManaOnEventRecovery",
             "ManaOnConsumeRecovery"),
    # (MECHC17) Energy Shield on events: "Regenerate N% of maximum Energy Shield over 1 second when Stunned" (hit_income).
    # For SEARCH: objective.RECOVERY_POOLS["EnergyShield"] doesn't read it yet
    "EnergyShield": ("EnergyShieldOnEventRecovery", "EnergyShieldPreventedRecoup",  # (MECHC21: Scarred Faith's)
                     # (MECHC23, AUDIT33 A33-1) the Life flask's recovery applied to Energy Shield (Shavronne's Satchel:
                     # flasks.es_recovery_per_second, at the flask's own use rate)
                     "EnergyShieldFlaskRecovery"),
}
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
CORPSE_MANA = re.compile(rf"regenerate {NUM}% of maximum mana over 2 seconds when you consume a corpse", re.I)
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
CAST_REGEN = re.compile(rf"regenerate {NUM} life over 1 second when you cast a spell", re.I)
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
# The "if you have at least 100 Tribute" form (Twice Bled) isn't read (Tribute isn't read: its own ledger entry).
PREVENTED_ES = re.compile(rf"^{NUM}% of physical damage prevented recouped as energy shield per enemy power$", re.I)
PREVENTED_LIFE = re.compile(rf"^{NUM}% of physical damage prevented recouped as life$", re.I)
HIT_LINES = (MISSING_BEFORE_HIT, ARMOUR_ON_BLOCK, CAST_REGEN, CAST_LEECH, STUNNED_LIFE, STUNNED_ES, PREVENTED_ES,
             PREVENTED_LIFE)
# The game's Light Stun rule (KeywordPopups LightStun, build 26476662): "up to 100% base chance for Hits that deal 100%
# of the target's maximum Life" (StunThreshold: "A Hit that deals damage equal to or greater than your Stun Threshold
# is guaranteed to Stun you, and the chance scales down linearly"), "Chances lower than 15% are treated as 0%" (the
# StunThreshold popup says 10%: the higher floor taken, a lower bound), "Physical damage, and player (but not monster)
# Melee Damage, each have 50% more Light Stun chance" (a boss's Melee gets nothing). PoB's own SelfStunChance uses other
# constants (Data.lua StunBaseMult 200, MinStunChanceNeeded 20) on its all-types-at-once enemy hit: not read
LIGHT_STUN_FLOOR, PHYSICAL_STUN_MORE = 0.15, 1.5


def stuns_taken(stats: dict, sizes: dict[str, float], weights: dict[str, float], landed: float) -> tuple[float, str]:
    """(Light Stuns the boss's hits put on you a second, how): `landed` hits a second, a `weights[t]` share of them of
    damage type t, each of `sizes[t]` damage after mitigation, against PoB's StunThreshold, less its StunAvoidChance."""
    threshold = _num(stats.get("StunThreshold"))
    avoid = min(max(_num(stats.get("StunAvoidChance")), 0.0), 100.0) / 100
    if threshold <= 0 or landed <= 0 or avoid >= 1:
        return 0.0, ("the build can't be Stunned" if avoid >= 1 else "no boss hit lands" if landed <= 0
                     else "no Stun Threshold")
    chance, parts = 0.0, []
    for t, w in weights.items():
        each = min(1.0, max(sizes.get(t, 0.0), 0.0) * (PHYSICAL_STUN_MORE if t == "Physical" else 1.0) / threshold)
        each = each if each >= LIGHT_STUN_FLOOR else 0.0
        chance += w * each
        if w > 0:
            parts.append(f"{t} {w:.0%} of hits, {each:.0%}")
    rate = landed * chance * (1 - avoid)
    return rate, (f"{landed:.3f} boss hits landing/s x {chance:.1%} Light Stun chance ({'; '.join(parts)}: hit size "
                  f"after mitigation / Stun Threshold {threshold:,.0f}, Physical 50% more, under 15% none)"
                  + (f" x {1 - avoid:.0%} not avoided" if avoid > 0 else "") + "; each hit at its type's full size (LOW)")


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
    "Life when you use a Mana Flask": re.compile(r"recover \S+ of maximum life when you use a mana flask", re.I),
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
        return sum(self.rates[f] for f in FIELDS[pool])


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
