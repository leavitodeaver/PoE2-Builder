"""The score a search maximizes: the log of the request's goal minus weighted feasibility deficits.

- The goal (request `objective`): boss DPS, a clear-speed estimate (mapping), or by default their
  geometric mean (balanced), so neither can be traded down to nothing (PLAN.md §5.5).
- Screening uses main-skill DPS (CombinedDPS per second, or the minion's for minion skills); finalists use
  Full DPS, which costs far more per what-if on real builds (PLAN.md Phase 0 findings).
- Request floors (life, resistances, max hit, ...) are layer 2 of the constraint compiler:
  normalized deficits that pull the search toward feasible builds without hiding them.
- Limits the game enforces (attribute requirements, Spirit) weigh more than request floors.
- Defence floors use pools and maximum hit taken, never TotalEHP (a known snapshot bug inflates
  it, PLAN.md "Engine accuracy"). Maximum hit taken includes Darkness (models.with_darkness),
  which PoB pools but doesn't spend.
- Skills the main skill triggers besides itself (spells in a meta gem, Elemental Expression) only
  count through Full DPS, so while a build has them (`extra`) the search scores Full DPS too.
"""

from __future__ import annotations

import math
import re

from builder.request.schema import DefenceStyle, Objective as Goal
from builder.search import boss_hits, recovery
from builder.search.context import DEFENCE_LAYERS, Context
from builder.search.flasks import RECOVERY_FIELDS
from builder.search.models import DAMAGE_TYPES, press_key, with_darkness

FLOOR_WEIGHT = 6.0
HARD_WEIGHT = 15.0
# Fixed costs of being short at all, so no amount of damage buys an infeasible build (e^3 is 20x
# DPS, e^8 about 3000x); the weights above still steer toward the smallest shortfall
FLOOR_OFFSET = 3.0
HARD_OFFSET = 8.0
# When a search settles with a requirement still short, that requirement's weight is multiplied
# by ESCALATION (up to MAX_MULTIPLIER) and the search goes on: a big gap (2,000 Mana) can need
# several moves that each lose DPS before the floor is met, which a fixed weight never pays for.
ESCALATION = 4.0
MAX_MULTIPLIER = 64.0
MAIN_DPS_FIELDS = ["CombinedDPS", "Minion.CombinedDPS"]
# For a skill PoB shows per use (ShowAverage, the bridge's flag: Sunder, Earthquake, other slams) its CombinedDPS is the
# average damage per use (CalcOffence.lua 6265), blind to attack speed and DPS multipliers (COVD6: the Hymn's Aftershock
# moved it 0%); main_dps puts it per second from these (SEARCH7)
AVERAGE_FIELDS = ["ShowAverage", "TotalDPS", "AverageDamage", "TotalDotDPS", "CullMultiplier", "ReservationDpsMultiplier"]
BASE_FIELDS = [
    "FullDPS", *MAIN_DPS_FIELDS, "Life", "EnergyShield", "Mana", "Armour", "Evasion", "Spirit",
    "SpiritUnreserved", "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
    *AVERAGE_FIELDS, *(f"Minion.{f}" for f in AVERAGE_FIELDS),
]
# What Darkness needs to join the maximum-hit pools
DARKNESS_FIELDS = ["Darkness", "LowLifeRecoveryCap", "Life", *(f"{t}TotalHitPool" for t in DAMAGE_TYPES), *(f"{t}MaximumHitTaken" for t in DAMAGE_TYPES)]
# PoB's lists of skills whose cost a pool can't pay (reported as counts by the bridge): a skill
# you can't pay for can't be used, so each is a hard limit like attribute requirements
COST_WARNINGS = {
    "LifeCostWarningList": "Life", "ManaCostWarningList": "Mana", "ESCostWarningList": "Energy Shield",
    "WardCostWarningList": "Runic Ward", "RageCostWarningList": "Rage",
    "LifePercentCostPercentCostWarningList": "unreserved Life", "ManaPercentCostPercentCostWarningList": "unreserved Mana",
}
# PoB fills those lists only in its MAIN-mode output (Calcs.lua 533, buildOutput: the committed build), never in a
# what-if: every search what-if runs in CALCULATOR mode (getMiscCalculator), so before SEARCH9 a what-if never saw a cost
# it couldn't pay, and with fill_from_base it inherited the committed count, so a what-if that fixed it still showed it
# (MECHB12's 2b75b: Transmutation Explosion's Runic Ward cost with no Ward was seen only once committed). The bridge's
# setCostShort now fills the lists in a what-if too, from each skill's committed cost against the what-if's pools; the main skill's
# own cost against its pool is in every output (CalcOffence); this is PoB's own test (Calcs.lua 545-556: the pool, plus
# Energy Shield for Mana when Energy Shield protects it, under one use's cost) applied to it. Not applied while anything
# reserves Life or Mana (output {pool}ReservedPercent): PoB lets a skill that reserves from the pool pay its raw cost from
# the reserved share too, and the what-if can't read the main skill's own share, so there only PoB's list counts (on
# commit; never a deficit PoB wouldn't give). Pool -> its cost field, for COST_WARNINGS' keys.
MAIN_COST_POOLS = {
    "LifeCostWarningList": ("LifeUnreserved", "LifeCost"), "ManaCostWarningList": ("ManaUnreserved", "ManaCost"),
    "ESCostWarningList": ("EnergyShield", "ESCost"), "WardCostWarningList": ("Ward", "WardCost"),
    "RageCostWarningList": ("Rage", "RageCost"),
}
MAIN_COST_FIELDS = sorted({f for pair in MAIN_COST_POOLS.values() for f in pair}
                          | {"EnergyShieldProtectsMana", "LifeReservedPercent", "ManaReservedPercent"})


def cost_short(stats: dict, field: str) -> float:
    """How many skills can't pay `field`'s pool: PoB's own list (committed builds) or, at least, the main skill's own
    cost against the pool, which a what-if also has."""
    listed = number(stats.get(field))
    if field not in MAIN_COST_POOLS:
        return listed
    pool, cost_field = MAIN_COST_POOLS[field]
    cost = number(stats.get(cost_field))
    if cost <= 0:
        return listed
    have = number(stats.get(pool))
    if cost_field == "ManaCost" and number(stats.get("EnergyShieldProtectsMana")):
        have += number(stats.get("EnergyShield"))
    if pool in ("LifeUnreserved", "ManaUnreserved") and number(stats.get(pool.replace("Unreserved", "ReservedPercent"))):
        return listed  # (a reserving pool: PoB's raw-cost pass, not modelled here)
    return max(listed, 1.0 if have < cost else 0.0)


# Passive (and ascendancy) points allocated beyond the level's budget (bridge setPointsOver, as PoB's "You have too many
# passive points allocated" counts them): a hard limit (SEARCH8, Defect C: gate 2b75b delivered a tree 19 points over)
POINTS_OVER = "PassivePointsOver"
# Damage that only happens when enemies die (Fiery Death's explosions, Plague Burst, ...): PoB's
# Full DPS counts it as if it were sustained, but a single boss never triggers it
# (ledger/proposed/pob-full-dps-on-death-damage.yaml). Matched on gem descriptions.
ON_DEATH = re.compile(r"\b(?:explode|combust|burst)\w*\b[^.]*\bon death\b|\bwhen they die\b", re.I)
# Sustain: PoB's DPS assumes you can keep paying a skill's cost. Regeneration, leech, on-hit
# recovery and flasks (at the rate generated charges pay for in a boss fight, builder/search/
# flasks.py) must cover at least this share of the per-second cost; below it the build is short of
# a requirement-style floor. The bar sits under the user's own endgame builds (19%, 47% and 71% of
# their costs, without flasks) and above degenerate ones (0-4%).
SUSTAIN_FLOOR = 0.10
# The search's own per-second recovery fields, added to every result (Build.added_stats), never asked of PoB: flask
# recovery (flasks.RECOVERY_FIELDS) and Life / Mana from kills, Remnants, charms, events and what the build spends
# (recovery.FIELDS, MECHC12 / MECHC13; SEARCH12: before, nothing scored them, so a build paying its costs through
# Remnants or on-kill recovery read short of the sustain floor; the user's rule, 12 Nov)
ADDED_RECOVERY = {pool: (RECOVERY_FIELDS[pool], *recovery.FIELDS[pool]) for pool in ("Life", "Mana")}
ADDED_RECOVERY_FIELDS = {f for fields in ADDED_RECOVERY.values() for f in fields}
SUSTAIN_POOLS = {
    "Mana": ("ManaPerSecondCost", ("ManaRegenRecovery", "ManaLeechRate", "ManaOnHitRate", *ADDED_RECOVERY["Mana"])),
    "Life": ("LifePerSecondCost", ("LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate", *ADDED_RECOVERY["Life"])),
}
# Recoup counts too (the user, 12 Nov ~18:20: "regen is not the only source of recovery, not by a longshot ... that
# applies for health and mana too"; SEARCH9): each pool's Recoup of the boss's hits that land, at the defence pace the
# damage it recoups is read at, and PoB's Life / Mana gained on Block at the boss's blocked hits (hit_recovery_rates). Kills,
# Remnants, charms, Warcry / Heavy Stun / Ignite / Shock events (and, with MECHC13, charges, Glory, Combo, corpses and
# Offerings spent) count through recovery.FIELDS (ADDED_RECOVERY, SEARCH12). Sources still left out of the sustain share,
# named in its result (never a silent rejection): the event lines recovery.EVENT_UNREAD names, recovery-rate modifiers
# on recovery.py's amounts (it takes them as written, MEDIUM), and Energy Shield paying a cost; Runic Ward-cost skills
# are held to what Ward recovery pays for by Assumptions.ward_paid instead (MECHB13). (checked: recovery.py's FIELDS and
# EVENT_UNREAD on the commit this runs on; the list follows them; UNVERIFIED that recovery.py's inventory is whole:
# MECHC11's scratch/mechc11/recovery_inventory.md is its source.)
_SPENT = "LifeOnConsumeRecovery" in recovery.ALL_FIELDS  # (MECHC13's spent / consumed sources)
SUSTAIN_LEFT_OUT = ("recovery on " + ", ".join(
    [*recovery.EVENT_UNREAD, *([] if _SPENT else ["charges, Glory, Combo or corpses consumed, Offerings created"])])
    + "; recovery-rate modifiers on those per-event amounts; and Energy Shield paying a cost (Runic Ward: "
    "Assumptions.ward_paid holds Ward-cost skills to what Ward recovery, Remnants of Kalguur's included, pays for)")
# Recovery against a boss (the request's optional recovery floor; always reported): the boss's damage per second after
# mitigation is its hits that land (one every enemySkillTime, PoB's boss preset, less evade and block, which the
# red-flash share ignores: boss_hits.py), less Deflection's expected share (LOW against red-flash hits), times what each
# takes after mitigation ({Type}TakenHit); recovery is each pool's regeneration, leech, on-hit recovery, flask recovery
# (at the rate a boss fight refills them) and recoup (its % of the damage that pool's recoup takes back, per damage
# type) and gains on Block (SEARCH9), less the skill costs paid from that pool. Mana counts only up to the share of damage Mind over Matter sends to
# it. Energy Shield recharge counts at the share of the fight it runs between the boss's landed hits (es_recharge,
# SEARCH13; AUDIT3 #15, AUDIT26: before, none, an UNDERCOUNT for every high-avoidance Energy Shield build).
RECOVERY_POOLS = {
    "Life": (("LifeRegenRecovery", "LifeLeechRate", "LifeOnHitRate", *ADDED_RECOVERY["Life"]), "LifePerSecondCost"),
    "EnergyShield": (("EnergyShieldRegenRecovery", "EnergyShieldLeechRate", "EnergyShieldOnHitRate"), None),
    "Mana": (("ManaRegenRecovery", "ManaLeechRate", "ManaOnHitRate", *ADDED_RECOVERY["Mana"]), "ManaPerSecondCost"),
}
RECOVERY_DAMAGE_TYPES = ("Physical", "Fire", "Cold", "Lightning", "Chaos")
# The build's own damage over time (MECHC9): PoB's TotalBuildDegen, its "Lose / take N per second" lines after the
# damage-over-time taken modifiers, with Heartbound Loop's damage on Minion Death in it (models.heartbound_damage's
# degen line, MECH-B's rate). Before, boss_recovery read none of it (checked: builder/ and the bridge read no BuildDegen
# or NetLifeRegen), so recovery had to outpace the boss's hits only: an overcount of the share recovered for a build
# that hurts itself. Counted as incoming beside the boss's hits.
BUILD_DEGEN = "TotalBuildDegen"
RECOVERY_INPUTS = sorted({f for fields, cost in RECOVERY_POOLS.values() for f in (*fields, cost) if f}
                         - ADDED_RECOVERY_FIELDS
                         | {f"{t}TakenHit" for t in RECOVERY_DAMAGE_TYPES} | {BUILD_DEGEN}
                         | {f"{t}{p}Recoup" for t in ("", *RECOVERY_DAMAGE_TYPES) for p in RECOVERY_POOLS}
                         | {f"{p}OnBlock" for p in RECOVERY_POOLS}
                         | {"enemySkillTime", "EvadeChance", "EffectiveBlockChance", "sharedMindOverMatter",
                            "DeflectChance", "DeflectEffect"}
                         | {"EnergyShieldRecharge", "EnergyShieldRechargeDelay", "IgniteAvoidChance", "SelfIgniteDuration",
                            "AilmentThreshold", "Life"})  # (es_recharge, and the Ignites boss_hits.es_dot reads)


def boss_taken(stats: dict, every: float | None = None) -> tuple[dict[str, float], float, float]:
    """The boss's damage per second after mitigation by type, its landed hits per second and the Deflection factor, with
    one damaging use every `every` seconds (default: PoB's enemySkillTime, the defence cadence)."""
    every = every or number(stats.get("enemySkillTime")) or 0.7
    hits = boss_hits.land_share(stats) / every  # (red-flash hits land whatever the evade and block, MECHC5)
    # Deflection (KeywordPopups "Deflect": prevents DeflectEffect%, 40% by default, of a Deflected Hit's damage): PoB puts
    # it in {Type}TakenHit only at a 100% chance, so below that it's the expected share here. Kept against the red-flash
    # hits too (#78: no game text either way, LOW); `no_deflect_share` is the figure without it (MECHC5)
    chance = min(max(number(stats.get("DeflectChance")), 0.0), 100) / 100
    deflect = 1 - chance * min(max(number(stats.get("DeflectEffect")), 0.0), 100) / 100 if chance < 1 else 1.0
    # each type at its full hit size in PoB, weighted by its share of the boss's hits (MECHC6, boss_hits.type_shares)
    weights = boss_hits.type_shares(boss_hits.boss_name(stats))
    taken = {t: max(number(stats.get(f"{t}TakenHit")), 0.0) * weights[t] * hits * deflect for t in RECOVERY_DAMAGE_TYPES}
    return taken, hits, deflect


def recoup(stats: dict, taken: dict[str, float], pool: str) -> float:
    """What `pool` recoups per second of the damage `taken` (per type, per second): PoB's per-type {Type}{pool}Recoup and
    its untyped {pool}Recoup ("N% of Damage taken Recouped as Life", CalcDefence.lua 1857: 19 Life, 12 Mana and 3 Energy
    Shield lines in ModCache, before SEARCH9 left out of boss_recovery: an UNDERCOUNT), each already times the pool's
    recovery rate. Recoup returns over 4 or 8 s; in a steady fight the per-second figure is the share of what's taken."""
    typed = sum(taken[t] * number(stats.get(f"{t}{pool}Recoup")) for t in RECOVERY_DAMAGE_TYPES)
    return (typed + sum(taken.values()) * number(stats.get(f"{pool}Recoup"))) / 100


def benefit_every(stats: dict) -> float:
    """Seconds between the boss's damaging uses as a "when hit" BENEFIT reads them (MECHD4's bosses.benefit_skill_time,
    the manager's 16:50 ruling: defences read the faster rate, PoB's enemySkillTime)."""
    from builder.search import bosses
    return bosses.benefit_skill_time(stats)


def on_block(stats: dict, pool: str, every: float | None = None) -> float:
    """What `pool` gains per second from PoB's {pool}OnBlock (per Block: "Recover N Life when you Block", "N Mana gained
    when you Block", CalcDefence.lua 1949-1956; PoB itself counts it as LifeWhenHit x BlockChance in its EHP, 3262) times
    the boss's blocked hits per second (boss_hits.block_share: blockable hits, after evasion)."""
    per = number(stats.get(f"{pool}OnBlock"))
    if per <= 0:
        return 0.0
    every = every or number(stats.get("enemySkillTime")) or 0.7
    return per * boss_hits.block_share(stats) / every


def hit_recovery_rates(stats: dict) -> dict[str, float]:
    """Life and Mana recovered per second from the boss's hits: what the sustain share counts beside regeneration, leech,
    on-hit recovery and flasks. Recoup takes back a share of the damage taken, so it reads the same hits as that damage
    (enemySkillTime, the defence pace; AUDIT25 A25-60: at the slower benefit pace it recovered less than its line
    promises, an UNDERCOUNT); gains on Block are a "when hit" benefit, at the benefit pace."""
    taken, _, _ = boss_taken(stats)
    every = benefit_every(stats)
    return {pool: recoup(stats, taken, pool) + on_block(stats, pool, every) for pool in SUSTAIN_POOLS}


def es_recharge(stats: dict, hits: float) -> tuple[float, float]:
    """(Energy Shield recharged per second against the boss, the share of the fight recharge runs). SEARCH13 (AUDIT3 #15,
    AUDIT26: an UNDERCOUNT for every high-avoidance Energy Shield build): the boss's landed hits as a Poisson stream
    (`hits` per second, boss_taken's: after evade and block, red-flash hits always land), the one boss_hits.recharge_wait
    reads. After each landed hit recharge waits the delay (PoB's EnergyShieldRechargeDelay), plus the seconds of damage
    over time the hit leaves on you on a share p of hits (boss_hits.es_dot: Ignite, Corrupted Blood), then runs until
    the next landed hit: per gap E[max(0, gap - delay - D)] = e^(-hits (delay + D)) / hits, so the share of the fight it
    runs is the sum over hits of p x e^(-hits x (delay + D)) (exact for Poisson hits; a new hit while an older damage
    over time still burns isn't followed, as in recharge_wait). Times PoB's EnergyShieldRecharge (per second, after its
    recovery-rate modifiers). MEDIUM: the boss's misses by timing aren't counted, and recharge refills only what was
    lost (boss_recovery caps it at the incoming damage)."""
    recharge = number(stats.get("EnergyShieldRecharge"))
    if recharge <= 0:
        return 0.0, 0.0
    delay = max(number(stats.get("EnergyShieldRechargeDelay")) or 4.0, 0.0)
    if hits <= 0:
        return recharge, 1.0
    dots = boss_hits.es_dot(stats, land=hits)
    plain = max(1.0 - sum(p for p, _, _ in dots), 0.0)
    share = plain * math.exp(-hits * delay) + sum(p * math.exp(-hits * (delay + d)) for p, d, _ in dots)
    return recharge * share, share


def boss_recovery(stats: dict) -> dict:
    """Recovery per second against a boss, the boss's damage per second after mitigation, and the share recovered."""
    taken, hits, deflect = boss_taken(stats)
    own = max(number(stats.get(BUILD_DEGEN)), 0.0)
    incoming = sum(taken.values()) + own
    parts = {}
    for pool, (fields, cost) in RECOVERY_POOLS.items():
        back = recoup(stats, taken, pool) + on_block(stats, pool)
        net = max(sum(number(stats.get(f)) for f in fields) + back - (number(stats.get(cost)) if cost else 0.0), 0.0)
        if pool == "Mana":
            net = min(net, incoming * min(number(stats.get("sharedMindOverMatter")), 100) / 100)
        parts[pool] = net
    recharged, recharging = es_recharge(stats, hits)
    if recharged > 0:  # (it refills only what was lost: at most the incoming damage)
        parts["Energy Shield recharge"] = min(recharged, incoming)
    recovery = sum(parts.values())
    share = recovery / incoming if incoming > 0 else float("inf")
    result = {"recovery": recovery, "incoming": incoming, "share": share, "parts": parts, "hits": hits,
              "deflect": 1 - deflect, "no_deflect_share": share, "own_degen": own, "recharging": recharging}
    if deflect < 1:
        bare = boss_recovery({**stats, "DeflectChance": 0})
        result["no_deflect_share"] = bare["share"]
    return result


def with_life_es(stats: dict) -> dict:
    """LifePlusEnergyShield: maximum Life + maximum Energy Shield, the Life + ES floor's stat (Floors.life_es)."""
    stats["LifePlusEnergyShield"] = number(stats.get("Life")) + number(stats.get("EnergyShield"))
    return stats


def with_boss_recovery(stats: dict) -> dict:
    """BossRecoveryPercent: the recovery floor's stat (capped at 1000%, also when the boss's hits never land)."""
    stats["BossRecoveryPercent"] = min(boss_recovery(stats)["share"] * 100, 1000.0)
    return stats


# What PoB is asked for (the flask fields are the search's own, added by Build.extra_stats)
SUSTAIN_FIELDS = sorted({f for cost, recovery in SUSTAIN_POOLS.values() for f in (cost, *recovery)}
                        - ADDED_RECOVERY_FIELDS | set(RECOVERY_INPUTS))  # (recoup reads the boss's hits)
# Clear speed (PLAN.md §5.5). PoB doesn't model it, so this is an estimate, labelled approximate in
# the result: the monster life a build clears per second in a typical map pack. One use reaches
# part of the pack (area, projectiles, chain, pierce, fork); killing what it reaches takes the hits
# its damage needs plus one use's own time; then the build walks to the next pack. While damage is
# the limit this is hit DPS x targets; once each use kills what it reaches, use speed and movement
# speed set the pace. The constants are guesses (confidence: low): they mostly decide where more
# damage stops paying off for clearing.
PACK_SIZE = 8  # monsters in a pack
PACK_RADIUS = 40.0  # PoB units (10 per metre): the circle a pack stands in
# An average pack member: level 80 base life is 31,065 (PoB's monster life table), x1.75 for a
# pack of five normal, two magic (x2) and one rare (x5) monster, and +30% from map modifiers
MONSTER_LIFE = 70_000.0
TRAVEL_SECONDS = 2.0  # walking to the next pack at base movement speed
PIERCE_VALUE = 0.5  # a pierce only finds the enemies standing behind the first
FORK_VALUE = 1.0  # a fork's two projectiles leave at an angle; count one new target
CLEAR_PARTS = ["AreaOfEffectRadius", "ProjectileCount", "ChainMax", "PierceCount", "ForkCountMax", "Speed"]
CLEAR_FIELDS = [*CLEAR_PARTS, *(f"Minion.{f}" for f in CLEAR_PARTS), "EffectiveMovementSpeedMod"]


def sustain(stats: dict) -> tuple[float, str | None]:
    """The lowest share of a pool's per-second skill cost that recovery covers (1.0 = all), and
    the pool."""
    worst, pool_name = 1.0, None
    back = hit_recovery_rates(stats)
    for pool, (cost_field, recovery_fields) in SUSTAIN_POOLS.items():
        cost = number(stats.get(cost_field))
        if cost > 0:
            share = (sum(number(stats.get(f)) for f in recovery_fields) + back[pool]) / cost
            if share < worst:
                worst, pool_name = share, pool
    return worst, pool_name


# Fields only an EHP pass fills in; gem what-ifs skip that pass and keep the build's values
EHP_FIELDS_PREFIXES = ("PhysicalMaximumHitTaken", "FireMaximumHitTaken", "ColdMaximumHitTaken",
                       "LightningMaximumHitTaken", "ChaosMaximumHitTaken")


def number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value in ("inf", "-inf"):
        return float(value)
    return 0.0


def per_second(stats: dict, prefix: str = "") -> float:
    """The main skill's (or with prefix "Minion.", the minion's) CombinedDPS, per second. For a ShowAverage skill PoB
    builds CombinedDPS as (AverageDamage [+ per-use Impale]) x cull x the boss's Life-reservation multiplier, with no
    damage over time (CalcOffence.lua 6265-6470); per second it is what PoB builds for every other skill, TotalDPS (=
    AverageDamage x use rate x DPS multiplier) plus TotalDotDPS, under the same cull and reservation multipliers. Impale
    stays per use, as PoB's Full DPS has it (Calcs.lua harvests ImpaleDPS as it is)."""
    combined = number(stats.get(prefix + "CombinedDPS"))
    if number(stats.get(prefix + "ShowAverage")) != 1 or combined <= 0:
        return combined
    scale = max(number(stats.get(prefix + "CullMultiplier")), 1.0) * (number(stats.get(prefix + "ReservationDpsMultiplier")) or 1.0)
    hits = number(stats.get(prefix + "TotalDPS")) - number(stats.get(prefix + "AverageDamage"))
    return max(combined + scale * (hits + number(stats.get(prefix + "TotalDotDPS"))), 0.0)


CULL_ENTRY = "Full Culling DPS"  # PoB's Full DPS entry for Culling Strike (Calcs.lua 479-482), x Objective.cull_scale


def main_dps(stats: dict) -> float:
    return max(per_second(stats), per_second(stats, "Minion."))


def clear_estimate(stats: dict, damage: float) -> dict:
    """Clear speed from a build's damage per target and the main skill's reach (module constants):
    the score (monster life cleared per second) and the parts behind it."""
    # A minion skill's reach and speed are the minion's; movement is always the player's
    prefix = "Minion." if per_second(stats, "Minion.") > per_second(stats) else ""

    def part(field: str) -> float:
        return max(number(stats.get(prefix + field)), 0.0)

    per_projectile = (1.0 + part("ChainMax") + PIERCE_VALUE * min(part("PierceCount"), PACK_SIZE)
                      + FORK_VALUE * part("ForkCountMax"))
    area = 1.0 + (PACK_SIZE - 1) * (part("AreaOfEffectRadius") / PACK_RADIUS) ** 2
    reach = max(part("ProjectileCount"), 1.0) * per_projectile * area
    # Reaching more enemies than the pack holds is worth less and less: 1 at a reach of 1, rising
    # at first one for one, approaching PACK_SIZE
    span = PACK_SIZE - 1
    targets = 1.0 + span * -math.expm1(-(min(reach, 1e6) - 1.0) / span)
    speed = part("Speed") or 1.0  # skills PoB gives no use rate count as one use per second
    movement = number(stats.get("EffectiveMovementSpeedMod")) or 1.0
    score = 0.0
    if damage > 0:
        seconds = PACK_SIZE / targets * (MONSTER_LIFE / damage + 1.0 / speed) + TRAVEL_SECONDS / max(movement, 0.1)
        score = MONSTER_LIFE * PACK_SIZE / seconds
    return {"score": score, "targets": targets, "uses_per_second": speed, "movement_speed": movement,
            "damage": damage}


def goal_value(goal: str, dps: float, clear: float) -> float:
    """A goal's value from boss DPS and the clear score: what a search maximizes, and what results
    from searches run on different goals are ranked by."""
    if goal == Goal.BOSS:
        return dps
    if goal == Goal.MAPPING:
        return clear
    return math.sqrt(max(dps, 0.0) * max(clear, 0.0))


class Objective:
    def __init__(self, ctx: Context, full: bool = False, multipliers: dict[str, float] | None = None,
                 goal: str | None = None) -> None:
        self.ctx = ctx
        self.base_full = full  # finalists: Full DPS throughout
        self.extra = False  # the build has triggered damage outside the main skill (see module doc)
        self.added_dps = 0.0  # damage a custom model computes outside PoB (Melting Maelstrom's pulses), set at each resolve
        # each such source by name (SEARCH14; MECHB20 sums several in the bridge's LedgerAddedDPS: Melting Maelstrom's pulses,
        # Eternal Hunt): set_added keeps added_dps their sum, which every score reads; the parts are for the report
        self.added_parts: dict[str, float] = {}
        # Full DPS entries (SkillDPS, by name) counted x this, set at each resolve: Pounce's slam at its real use, its
        # carried wolves (models.mark_model, MECH36)
        self.entry_scales: dict[str, float] = {}
        # and pressed non-main skills at their real use, the main skill at the time they leave it (models.press_model,
        # set by Assumptions.rate_presses; MECH37)
        self.press_scales: dict[str, float] = {}
        # Gains on the whole build's damage, by source (SEARCH12; MECH60's routed undercount): a model whose gain scales
        # every damage entry alike (Doomgate's Enemies are Culled on Block: the fight ends sooner for all of it) puts it
        # here, not on each press_scales entry, so main-skill-only scoring counts it too. Product of the values, in both
        # modes (dps); a model sets its own key each refresh and the refresh that rebuilds press_scales clears it
        self.whole_scales: dict[str, float] = {}
        # The scale on PoB's Culling Strike gain against the boss (SEARCH13; MECH57's conditions.cull_window_scale: the
        # time a Cull really saves, hit by hit, over PoB's whole-window price; 1.0 = PoB's), set by Assumptions.cull_window
        # at each resolve. Counted on each scored build's own Culling multiplier, in both modes: in Full DPS on its "Full
        # Culling DPS" entry; outside it on the main skill, whose CombinedDPS PoB builds x CullMultiplier (per_second), as
        # 1 + (CullMultiplier - 1) x scale over CullMultiplier. Before, the scale reached Full DPS only (press_scales), so
        # main-skill screening ranked a hit Cull at PoB's whole price above what the delivered number counts (an OVERCOUNT
        # in the ranking). A candidate without a Cull has CullMultiplier 1 and Full Culling DPS 0, so it loses nothing.
        self.cull_scale = 1.0
        # The press rates the search chose for side skills with no cooldown or duration (models.PRESS_CHOICES; MECH55, the
        # user's "search picks the rate", 12 Nov; Search.press_round) and the main skill's time those presses take that
        # press_scales doesn't already charge (Search.press_sync: every choice outside Full DPS, where press_scales isn't
        # read; in Full DPS, a chosen skill that isn't a pressed Full DPS group, e.g. a Mark or a consumer pressed for its
        # side effect)
        self.press_choices: dict[str, float] = {}
        self.press_busy = 0.0
        # The boss's Low Life share of the fight (Search.weigh_life): 1 / (0.65 + 0.35 / (DPS on Low Life / DPS above it)),
        # set at each conditions round from one extra recalculation; 1 for a build with no Low Life lines
        self.life_factor = 1.0
        # Effects the build keeps up for part of the fight (conditions.MIN_UPTIME .. FULL_UPTIME; Search.weigh_uptimes): the
        # product over them of (u + (1 - u) x DPS without it / DPS with all on), refreshed each conditions round
        self.uptime_factor = 1.0
        # and the stats that change with all such effects off at once (off - on; Search.floor_without): the floors count
        # an effect only at full uptime, so a defence that leans on them counts only with them off (deficits takes the worst)
        self.floor_deltas: list[dict[str, float]] = []
        self.multipliers: dict[str, float] = dict(multipliers or {})  # requirement label -> weight factor
        self.penalties = ctx.compiled.penalties
        self.style = DefenceStyle(ctx.request.defence)
        # The request's goal unless overridden: balanced runs some searches on boss DPS (run.py)
        self.goal = Goal(goal or ctx.request.objective)
        # Clear fields in every mode, so a boss result can still show its clear estimate
        fields = set(BASE_FIELDS) | set(DARKNESS_FIELDS) | set(COST_WARNINGS) | set(MAIN_COST_FIELDS) | set(SUSTAIN_FIELDS) | set(CLEAR_FIELDS) | {POINTS_OVER}
        if any(p.stat == "BossRecoveryPercent" for p in self.penalties):
            fields |= set(RECOVERY_INPUTS)
        fields |= {"Missing" + p.stat for p in self.penalties if p.stat in RESIST_FIELDS}  # (maximum_resist)
        self._fields = sorted(fields | {p.stat for p in self.penalties} - {"BossRecoveryPercent", "LifePlusEnergyShield"})
        self.on_death = {name for name, gem in ctx.gems.items() if ON_DEATH.search(gem.get("description") or "")}
        # Points over the budget a screen may use without the hard deficit (Jewels.unlock_round: a socket's path is
        # opened past a full tree to price what it unlocks; the real trial frees the points first). 0 otherwise
        self.points_slack = 0.0

    @property
    def full(self) -> bool:
        return self.base_full or self.extra

    @property
    def fields(self) -> list[str]:
        return self._fields + ["SkillDPS"] if self.full else self._fields

    @staticmethod
    def prepare(stats: dict) -> dict:
        """Corrections to PoB's stats that every score and check sees (Darkness in max hit, recovery against a boss)."""
        return with_life_es(with_boss_recovery(with_darkness(stats)))

    def set_added(self, parts: dict[str, float]) -> None:
        """The build's damage computed outside PoB, by source (Assumptions.models at each resolve): added_dps is their sum
        (what dps reads for the build; a what-if reads the bridge's own sum, LedgerAddedDPS), added_parts each one."""
        self.added_parts = {name: float(value) for name, value in parts.items() if number(value) > 0}
        self.added_dps = sum(self.added_parts.values())

    def added_report(self) -> dict[str, float]:
        """Each added source's DPS for the result; a sum set without its parts shows as one entry."""
        if self.added_parts:
            return dict(self.added_parts)
        return {"computed outside PoB": self.added_dps} if number(self.added_dps) > 0 else {}

    def dps(self, stats: dict) -> float:
        """Single-target boss DPS."""
        # (a what-if's own Melting Maelstrom pulses, worked out by the bridge; the build's, from the last resolve)
        added = number(stats["LedgerAddedDPS"]) if "LedgerAddedDPS" in stats else self.added_dps
        if self.full:
            full = number(stats.get("FullDPS"))
            if full > 0:
                full = max(full + self.rescaled(stats) - self.excluded(stats)[0] - main_dps(stats) * self.busy(), 0.0)
                return (full + added) * self.life_factor * self.uptime_factor * self.whole()
        main = main_dps(stats) * self.main_cull(stats)
        return (main * (1.0 - self.busy()) + added) * self.life_factor * self.uptime_factor * self.whole()

    def main_cull(self, stats: dict) -> float:
        """The main skill's DPS x this counts its Culling gain at cull_scale: (1 + (m - 1) x scale) / m, m its own
        CullMultiplier (the minion's where the minion's DPS is the main one; 1.0 with no Cull or a scale of 1)."""
        scale = number(getattr(self, "cull_scale", 1.0))
        if scale == 1.0:
            return 1.0
        prefix = "Minion." if per_second(stats, "Minion.") > per_second(stats) else ""
        m = number(stats.get(prefix + "CullMultiplier"))
        return (1.0 + (m - 1.0) * max(scale, 0.0)) / m if m > 1.0 else 1.0

    def whole(self) -> float:
        """The product of whole_scales (1.0 with none): the gains a model counts on all of the build's damage."""
        return math.prod(max(number(v), 0.0) for v in (getattr(self, "whole_scales", None) or {}).values())

    def busy(self) -> float:
        """The main skill's share of the fight lost to chosen presses not charged in press_scales (press_busy, 0-1)."""
        return min(max(number(getattr(self, "press_busy", 0.0)), 0.0), 1.0)

    def rescaled(self, stats: dict) -> float:
        """What entry_scales and press_scales add to Full DPS: each named entry's damage x (its scale - 1)."""
        cull = number(getattr(self, "cull_scale", 1.0))
        if not self.entry_scales and not self.press_scales and cull == 1.0:
            return 0.0
        total = 0.0
        for e in stats.get("SkillDPS") or []:
            name, press = e.get("name"), press_key(e.get("name"), e.get("source"))
            own = max(cull, 0.0) if name == CULL_ENTRY else 1.0
            if name in self.entry_scales or press in self.press_scales or own != 1.0:
                total += number(e.get("dps")) * (e.get("count") or 1) * (
                    self.entry_scales.get(name, 1.0) * self.press_scales.get(press, 1.0) * own - 1)
        return total

    def clear_damage(self, stats: dict) -> float:
        """Damage per target while clearing: like boss DPS, but on-death effects count (clearing
        kills enemies, which is what sets them off)."""
        if self.full:
            full = number(stats.get("FullDPS"))
            if full > 0:
                return max(full + self.rescaled(stats), 0.0)
        return main_dps(stats)

    def clear(self, stats: dict) -> dict:
        """The clear-speed estimate and its parts (clear_estimate)."""
        return clear_estimate(stats, self.clear_damage(stats))

    def value(self, stats: dict) -> float:
        """What this search maximizes, before penalties."""
        if self.goal == Goal.BOSS:
            return self.dps(stats)
        return goal_value(self.goal, self.dps(stats), self.clear(stats)["score"])

    def excluded(self, stats: dict) -> tuple[float, list[str]]:
        """Full DPS that comes from on-death effects, and their names."""
        amount, names = 0.0, []
        for entry in stats.get("SkillDPS") or []:
            source = entry.get("source") or entry.get("name")
            if entry.get("name") in self.on_death or source in self.on_death:
                amount += number(entry.get("dps")) * (entry.get("count") or 1)
                names.append(entry["name"] if source == entry.get("name") else f"{entry['name']} from {source}")
        return amount, names

    def deficits(self, stats: dict) -> list[tuple[str, float, bool]]:
        """(label, normalized deficit, hard) for everything currently short: the worst of the build as it is and the
        build without the effects it keeps up for only part of the fight (floor_deltas: all of them off at once)."""
        found = self._deficits(stats)
        for deltas in self.floor_deltas:
            without = self._deficits({**stats, **{k: number(stats.get(k)) + d for k, d in deltas.items()}})
            if self._weight(without) > self._weight(found):
                found = without
        return found

    def _weight(self, deficits: list[tuple[str, float, bool]]) -> float:
        total = sum((HARD_WEIGHT if hard else FLOOR_WEIGHT) * self.multipliers.get(label, 1.0) * amount
                    for label, amount, hard in deficits)
        if any(hard for _, _, hard in deficits):
            total += HARD_OFFSET
        if any(not hard for _, _, hard in deficits):
            total += FLOOR_OFFSET
        return total

    def _deficits(self, stats: dict) -> list[tuple[str, float, bool]]:
        found: list[tuple[str, float, bool]] = []
        for penalty in self.penalties:
            amount = max(0.0, penalty.floor - number(stats.get(penalty.stat))) / max(penalty.floor, 1.0)
            if amount > 0:
                found.append((penalty.label, amount, False))
            # A resistance floor above the maximum can't be met by any amount of resistance, so it counts as fully short
            # on top: else a modifier that trades maximum for resistance wins every single-item trial. 2b75e, 9 Nov:
            # a Rune of the Prism (+20% to all Elemental Resistances, -10% to all Maximum) held fire at a 70% maximum
            # under the 72% floor from the first result to the last; taking it out alone cost 20% (POE2T294)
            top = maximum_resist(stats, penalty.stat)
            if top is not None and top < penalty.floor:
                found.append((f"Maximum {penalty.label}", 1.0 + (penalty.floor - top) / max(penalty.floor, 1.0), False))
        for attribute in ("Str", "Dex", "Int"):
            need = number(stats.get("Req" + attribute))
            have = number(stats.get(attribute))
            if have < need:
                found.append((f"{attribute} requirement", (need - have) / max(need, 1.0), True))
        for field, pool in COST_WARNINGS.items():
            count = cost_short(stats, field)
            if count > 0:
                found.append((f"Skill costs: not enough {pool}", count, True))
        over = number(stats.get(POINTS_OVER)) - self.points_slack
        if over > 0:
            found.append(("Passive points over the level's budget", over, True))
        share, pool = sustain(stats)
        if share < SUSTAIN_FLOOR:
            found.append((f"Skill cost sustain: {pool}", (SUSTAIN_FLOOR - share) / SUSTAIN_FLOOR, False))
        unreserved = number(stats.get("SpiritUnreserved"))
        if unreserved < 0:
            found.append(("Spirit", -unreserved / max(number(stats.get("Spirit")), 1.0), True))
        if self.style != DefenceStyle.ANY:
            layers = DEFENCE_LAYERS[self.style]
            values = {name: number(stats.get(name)) for name in ("Armour", "Evasion", "EnergyShield")}
            biggest = max(values.values())
            for name, value in values.items():
                if name in layers:
                    short = 1.0 if biggest <= 0 else max(0.0, 0.1 * biggest - value) / biggest
                    if short > 0:
                        found.append((f"Defence style: {name}", short, False))
                elif biggest > 0 and value >= 0.1 * biggest:
                    found.append((f"Defence style: no {name}", (value - 0.1 * biggest) / biggest + 0.05, False))
        return found

    def penalty(self, stats: dict) -> float:
        return self._weight(self.deficits(stats))

    def escalate(self, stats: dict) -> bool:
        """Raise the weight of every requirement still short. False once all are at the cap."""
        raised = False
        for label, _, _ in self.deficits(stats):
            current = self.multipliers.get(label, 1.0)
            if current < MAX_MULTIPLIER:
                self.multipliers[label] = min(current * ESCALATION, MAX_MULTIPLIER)
                raised = True
        return raised

    def score(self, stats: dict) -> float:
        return math.log1p(max(self.value(stats), 0.0)) - self.penalty(stats)


RESIST_FIELDS = {"FireResist", "ColdResist", "LightningResist", "ChaosResist"}


def maximum_resist(stats: dict, stat: str) -> float | None:
    """The maximum of a resistance (PoB: Missing<X>Resist is maximum - resistance), or None for any other stat or when
    the build's stats don't have it."""
    if stat not in RESIST_FIELDS or ("Missing" + stat) not in stats:
        return None
    return number(stats.get(stat)) + number(stats.get("Missing" + stat))


def shortfall(deficits: list[dict]) -> float:
    """How far a result is from feasible, for ranking results whose scores used different weights:
    game limits count ten times a request floor."""
    return sum(d["amount"] * (10.0 if d["hard"] else 1.0) for d in deficits)


def rank_key(result: dict) -> tuple[float, float]:
    """Feasibility first (smallest shortfall), then the request's goal (boss DPS, clear or both)."""
    return round(shortfall(result["deficits"]), 6), -result.get("value", result["dps"])
