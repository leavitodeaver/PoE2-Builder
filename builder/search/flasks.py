"""Flasks and charms (PLAN.md §5.2.3): what a boss fight sustains.

PoB counts a flask or charm in an active slot as up all the time. In a boss fight nothing dies, so
charges come only from charge generation (passives, gear, the item's own "gains charges per
second"). The search therefore counts:
- an item's effect (PoB's buffs while it is active) only when generated charges keep it up at least
  ACTIVE_UPTIME of the fight; the slot is switched off otherwise and the result says why
- a Life or Mana flask's recovery at the rate generated charges pay for, toward the skill-cost
  sustain floor (objective.SUSTAIN_POOLS), whether or not its effect counts

Charms that trigger on killing a Rare or Unique enemy fire once per boss, so they never count.
Charge numbers per item come from PoB (bridge `flaskData`, local modifiers included); charge
generation and scaling from the committed build (bridge `chargeGain`).
"""

from __future__ import annotations

from dataclasses import dataclass

ACTIVE_UPTIME = 0.9  # share of a boss fight a flask or charm effect must be up to be counted
FLASK_SLOTS = {"Flask 1": "Life", "Flask 2": "Mana"}  # PoB: Life flasks in Flask 1, Mana in Flask 2
CHARM_SLOTS = ["Charm 1", "Charm 2", "Charm 3"]
RECOVERY_FIELDS = {"Life": "FlaskLifeRecoveryRate", "Mana": "FlaskManaRecoveryRate"}  # pseudo-stats
KILL_TRIGGER = "used when you kill"  # charm triggers that need enemies to die (Golden Charm: a Rare or Unique)


@dataclass(frozen=True)
class Charges:
    """Charge generation and scaling from the committed build (bridge `chargeGain`)."""
    flask: float = 0.0
    life_flask: float = 0.0
    mana_flask: float = 0.0
    flask_gain_inc: float = 0.0
    charm: float = 0.0
    charm_gain_inc: float = 0.0
    flask_duration_inc: float = 0.0
    charm_duration_inc: float = 0.0
    life_recovery_inc: float = 0.0
    mana_recovery_inc: float = 0.0
    charm_limit: int = 0
    life_flask_blocked: bool = False  # "Flasks do not recover Life" (Sanguine Tides, UniqueLifeFlaskNoRecovery1)

    @classmethod
    def from_bridge(cls, data: dict) -> Charges:
        return cls(data.get("flask", 0), data.get("lifeFlask", 0), data.get("manaFlask", 0), data.get("flaskGainInc", 0),
                   data.get("charm", 0), data.get("charmGainInc", 0), data.get("flaskDurationInc", 0),
                   data.get("charmDurationInc", 0), data.get("lifeRecoveryInc", 0), data.get("manaRecoveryInc", 0),
                   int(data.get("charmLimit", 0)), bool(data.get("lifeFlaskBlocked", False)))


def charges_per_second(info: dict, gain: Charges, pool: str | None) -> float:
    """Charges an item gains per second without kills."""
    if info.get("kind") == "Charm":
        generated, inc = gain.charm + info.get("gainBase", 0), gain.charm_gain_inc
    else:
        specific = gain.life_flask if pool == "Life" else gain.mana_flask if pool == "Mana" else 0.0
        generated, inc = gain.flask + specific + info.get("gainBase", 0), gain.flask_gain_inc
    return max(0.0, generated * (1 + (inc + info.get("gainInc", 0)) / 100) * info.get("gainMod", 1))


def duration(info: dict, gain: Charges) -> float:
    inc = gain.charm_duration_inc if info.get("kind") == "Charm" else gain.flask_duration_inc
    return max(0.0, info.get("duration", 0) * (1 + inc / 100))


def uptime(info: dict, gain: Charges, pool: str | None = None, trigger: str = "") -> float:
    """Share of a boss fight the item's effect is up: generated charges pay for one use per
    `chargesUsed`, and each use lasts its duration."""
    if not info.get("kind") or KILL_TRIGGER in trigger.lower():
        return 0.0
    if info.get("constant"):  # "cannot be Used but applies its Effect constantly" (Lavianga's Spirits)
        return 1.0
    used = info.get("chargesUsed") or 0
    rate = charges_per_second(info, gain, pool)
    if used <= 0:
        return 1.0
    return min(1.0, rate * duration(info, gain) / used)


def charm_charges_used(charms: list[tuple[dict, str]], gain: Charges) -> tuple[float, str]:
    """Charm charges spent per second against a boss (Cast on Charm Use's Energy): each charm is used as its generated
    charges allow, never again while its effect lasts (as uptime), and not at all when it waits for a kill. `charms` is
    [(flaskData info, item text)]."""
    total, how = 0.0, []
    for info, raw in charms:
        used = info.get("chargesUsed") or 0
        if info.get("kind") != "Charm" or used <= 0 or info.get("constant") or KILL_TRIGGER in raw.lower():
            continue
        uses = charges_per_second(info, gain, None) / used
        length = duration(info, gain)
        if length > 0:
            uses = min(uses, 1 / length)
        total += uses * used
        name = next((line for line in raw.splitlines()[1:3] if line and ":" not in line), "a charm")
        how.append(f"{name}: {uses:.3f} uses/s x {used:g} charges")
    return total, "; ".join(how)


def uses_per_second(info: dict, gain: Charges, raw: str, pool: str | None) -> tuple[float, str]:
    """(uses per second, how) of one flask or charm against a boss: as often as its generated charges pay for a use,
    never again while its effect lasts (as charm_charges_used; a player feeding a "when you use a Flask" line uses it
    the moment it can be), and 0 when it waits for a kill or can't be used (MECH48: its "on use" lines were one per 4 s)."""
    used = info.get("chargesUsed") or 0
    if not info.get("kind") or info.get("constant") or KILL_TRIGGER in raw.lower():
        return 0.0, "can't be used against a lone boss (waits for a kill, or can't be used)"
    if used <= 0:
        return 0.0, "no charges per use read"
    made = charges_per_second(info, gain, pool)
    uses, how = made / used, f"{made:.2f} charges/s / {used:g} per use"
    if (length := duration(info, gain)) > 0 and uses > 1 / length:
        uses, how = 1 / length, how + f", at most once per its {length:.1f} s effect"
    return uses, how


def recovery_per_second(info: dict, gain: Charges, pool: str) -> float:
    """A Life or Mana flask's recovery per second at the rate its generated charges allow (one use
    at a time: never faster than one per duration)."""
    if pool == "Life" and gain.life_flask_blocked:
        return 0.0  # "Flasks do not recover Life" (game stats base_/life_flasks_do_not_recover_life; PoB ignores it)
    total = info.get("lifeTotal" if pool == "Life" else "manaTotal") or 0
    used = info.get("chargesUsed") or 0
    inc = gain.life_recovery_inc if pool == "Life" else gain.mana_recovery_inc
    if info.get("constant") and total > 0:
        # theory: its effect is its recovery, applied without a use, so the whole amount over every duration, end to end
        length = info.get("duration") or 0
        return total * (1 + inc / 100) / length if length > 0 else 0.0
    if total <= 0 or used <= 0:
        return 0.0
    uses = charges_per_second(info, gain, pool) / used
    length = info.get("duration") or 0
    if length > 0:
        uses = min(uses, 1 / length)
    return total * (1 + inc / 100) * uses


# Skills that spend flask charges per use (MECHC2, FIX_QUEUE's usage-rate cap; the manager's ruling 12 Nov 08:30). Game files
# (build 26476662, GrantedEffectStatSets constant stats): flask_throw_base_charges_used 5 on each Concoction, described
# "Consumes {0} Charges from your Mana [Flask]" (skill_stat_descriptions). PoB's data has the stat on three of the five
# (Acidic, Bleeding, Explosive) and none reads it: PoB throws at will. (checked: every Stats.json id holding "flask" with
# throw / consume / charges; their users in GrantedEffectStatSets, PassiveSkills, Mods and ModType: the five Concoctions,
# and two gem enchantments, Explosive / Poisonous Concoction charges consumed +%, not read here: labelled)
THROW_CHARGES = {"Explosive Concoction": 5, "Fulminating Concoction": 5, "Shattering Concoction": 5,
                 "Acidic Concoction": 5, "Bleeding Concoction": 5}
THROW_POOL = "Mana"


def charge_use_rate(info: dict | None, gain: Charges, pool: str, per_use: float) -> tuple[float, str]:
    """Uses per second a skill that spends `per_use` charges of the `pool` flask can sustain on a boss, from the flask's own
    charge generation (no kills on a lone boss); the stock a fight starts with isn't counted (an undercount for a short
    fight). (0, why) with no such flask or no generation. For the main skill's cap (Assumptions.flask_main) and usage.py."""
    if info is None or not info.get("kind"):
        return 0.0, f"no {pool} Flask equipped: nothing to throw"
    generated = charges_per_second(info, gain, pool)
    if generated <= 0:
        return 0.0, (f"the {pool} Flask gains no charges in a boss fight (no kills; no 'charges per second' line): "
                     f"none for the {per_use:g} a use takes")
    return generated / per_use, f"the {pool} Flask gains {generated:.2f} charges/s, {per_use:g} per use"


def slot_pool(slot: str) -> str | None:
    return FLASK_SLOTS.get(slot)


def is_flask_slot(slot: str) -> bool:
    return slot in FLASK_SLOTS or slot in CHARM_SLOTS
