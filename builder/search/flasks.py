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

    @classmethod
    def from_bridge(cls, data: dict) -> Charges:
        return cls(data.get("flask", 0), data.get("lifeFlask", 0), data.get("manaFlask", 0), data.get("flaskGainInc", 0),
                   data.get("charm", 0), data.get("charmGainInc", 0), data.get("flaskDurationInc", 0),
                   data.get("charmDurationInc", 0), data.get("lifeRecoveryInc", 0), data.get("manaRecoveryInc", 0),
                   int(data.get("charmLimit", 0)))


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
    used = info.get("chargesUsed") or 0
    rate = charges_per_second(info, gain, pool)
    if used <= 0:
        return 1.0
    return min(1.0, rate * duration(info, gain) / used)


def recovery_per_second(info: dict, gain: Charges, pool: str) -> float:
    """A Life or Mana flask's recovery per second at the rate its generated charges allow (one use
    at a time: never faster than one per duration)."""
    total = info.get("lifeTotal" if pool == "Life" else "manaTotal") or 0
    used = info.get("chargesUsed") or 0
    if total <= 0 or used <= 0:
        return 0.0
    inc = gain.life_recovery_inc if pool == "Life" else gain.mana_recovery_inc
    uses = charges_per_second(info, gain, pool) / used
    length = info.get("duration") or 0
    if length > 0:
        uses = min(uses, 1 / length)
    return total * (1 + inc / 100) * uses


def slot_pool(slot: str) -> str | None:
    return FLASK_SLOTS.get(slot)


def is_flask_slot(slot: str) -> bool:
    return slot in FLASK_SLOTS or slot in CHARM_SLOTS
