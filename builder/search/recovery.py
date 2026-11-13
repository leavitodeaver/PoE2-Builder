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
  Remnant" (passives; PoB parses nothing) on those Remnants. Every Remnant taken as collected (the play guide says to walk
  onto each; MEDIUM-LOW: where they appear is UNVERIFIED, research/no-effect-remnant-range.md).
- Charms (CharmLifeRecoveryRate / CharmManaRecoveryRate): a charm's own "Recover N Life / Mana when Used" (PoB parses
  nothing) at that charm's uses a second (flasks.uses_per_second: its trigger and the charges the build generates), and
  "Recover N% of maximum Mana when a Charm is used" at every charm's uses.
- Events (LifeOnEventRecovery / ManaOnEventRecovery): "Recover N% of maximum Life and Mana when you use a Warcry" (the
  build's Warcries, conditions.event_rates), "Recover N% of maximum Life when you Heavy Stun a Rare or Unique Enemy" (the
  boss's Heavy Stuns, models.heavy_stun_rate), "Recover N% of maximum Life / N Life when you Ignite an Enemy" and "Recover
  N% of maximum Mana when you Shock an Enemy" (the main skill's hits x its chance to inflict it). PoB parses none of them.
  Lines no rate reads yet are named in the note (EVENT_UNREAD): an undercount until they are.

Amounts are taken as written: recovery-rate modifiers aren't applied to them (MEDIUM). "Cannot Recover Life other than from
Leech" (Vaal Pact) zeroes the Life parts and "Mana Recovery other than Regeneration cannot Recover Mana" the Mana parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The fields, per second (for SEARCH: objective.SUSTAIN_POOLS / RECOVERY_POOLS read them from the build's stats)
FIELDS = {
    "Life": ("LifeOnKillRecovery", "LifeRemnantRecovery", "CharmLifeRecoveryRate", "LifeOnEventRecovery"),
    "Mana": ("ManaOnKillRecovery", "ManaRemnantRecovery", "CharmManaRecoveryRate", "ManaOnEventRecovery"),
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
NO_LIFE = re.compile(r"cannot recover life other than from leech", re.I)
NO_MANA = re.compile(r"mana recovery other than regeneration cannot recover mana", re.I)
# Recovery on events no rate here reads yet (MECHC11's inventory section 7): named in the note, never counted (undercount)
EVENT_UNREAD = {
    "Life per Endurance Charge consumed": re.compile(r"recover \S+ of maximum life for each endurance charge consumed", re.I),
    "Mana when you consume a Power Charge": re.compile(r"recover \S+ of maximum mana when you consume a power charge", re.I),
    "Life when you lose a Spirit Charge": re.compile(r"recover \S+ of maximum life when you lose a spirit charge", re.I),
    "Life per Glory consumed": re.compile(r"recover \S+ of maximum life per glory consumed", re.I),
    "Life / Mana when you expend Combo": re.compile(r"when (?:you )?(?:expend|spend)\w* at least \d+ combo", re.I),
    "Life on Rage consumed when Shapeshifting": re.compile(r"recover \S+ of maximum life per rage consumed", re.I),
    "Life when you consume a corpse": re.compile(r"recover \S+ of maximum life when you consume a corpse", re.I),
    "Life when you create an Offering": re.compile(r"recover \S+ of maximum life when you create an offering", re.I),
    "Life when a Minion is Revived": re.compile(r"life when one of your minions is revived", re.I),
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
                   crits: float, ailment_share: float, ailment_how: str, enemies: float) -> None:
    """Life / Mana Remnants (`skills`: {name: {"gain": per Remnant, "levelStats": {...}}}) at the build's hits, crits on
    a target with an Elemental Ailment (`ailment_share` of the fight) and kills; pickup lines on those Remnants."""
    count = 0.0
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
        rate = timed + kills
        count += rate
        income.add(f"{pool}RemnantRecovery", rate * gain,
                   f"{name} {rate:.3f} Remnants/s x {gain:,.0f} {pool} (one at most every {every:g} s on {trigger_how}"
                   + (f", plus {kills:.3f}/s from kills" if kills > 0 else "") + "; each taken as collected)")
    for pool in ("Life", "Mana"):
        pct = _sum(REMNANT_PICKUP, texts, pool)
        if pct > 0 and count > 0:
            income.add(f"{pool}RemnantRecovery", pct / 100 * _num(stats.get(pool)) * count,
                       f"{pct:g}% of maximum {pool} per Remnant collected x {count:.3f}/s (this build's Life / Mana "
                       "Remnants only: other Remnants it makes aren't counted here)")


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


def restrict(income: Income, texts: list[str]) -> None:
    """Vaal Pact's "Cannot Recover Life other than from Leech", "Mana Recovery other than Regeneration cannot Recover Mana"."""
    for pool, pattern in (("Life", NO_LIFE), ("Mana", NO_MANA)):
        if any(pattern.search(t) for t in texts) and income.pool(pool) > 0:
            for name in FIELDS[pool]:
                income.rates[name] = 0.0
            income.parts.append(f"none of the {pool} above counts: the build has '{pattern.pattern}'")


def unread(texts: list[str]) -> list[str]:
    return [name for name, pattern in EVENT_UNREAD.items() if any(pattern.search(t.strip()) for t in texts)]


def note(income: Income) -> list[dict]:
    """The assumption note: what each source adds, or nothing when the build has none."""
    life, mana = income.pool("Life"), income.pool("Mana")
    missing = (f" Not counted (no rate reads them yet, so an undercount): {', '.join(income.unread)}."
               if income.unread else "")
    if life <= 0 and mana <= 0 and not income.unread:
        return []
    sources = "; ".join(income.parts) or "none of these sources gives anything in this fight"
    return [{
        "label": "Life and Mana from kills, Remnants, charms and other events*",
        "value": f"{life:,.1f} Life/s, {mana:,.1f} Mana/s",
        "basis": f"{sources}. Rates from the build's own hits, kills (the boss preset's adds), charms and Warcries; "
                 f"amounts as written (recovery-rate modifiers not applied).{missing} Fields for the sustain and recovery "
                 f"floors: {', '.join(f for f in ALL_FIELDS if income.rates[f] > 0) or 'none above 0'}",
        "play": ("Walk onto each Remnant as it appears (it lasts 8 s); " if any("Remnant" in p for p in income.parts)
                 else "") + "the recovery above comes with the play it names (without it: regeneration, leech and "
                           "flasks only)",
        "confidence": "MEDIUM-LOW"}]


def _num(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
