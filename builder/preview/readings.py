"""Readings of 1.0 preview numbers the reveal leaves open (PREVIEW2; the manager, 23 Nov 13:15: "both readings as rows
until it's settled"). The headline is the lower reading; the checklist row shows the other.

Added Attack Time: poe2db's 1.0 pages show only the label "Added Attack Time: +N s". Two game stats could be behind it:
- total_attack_time_+_ms ("+N seconds to Total Attack Time"; KeywordPopups TotalPlus: added "after all other
  calculations", no percentage modifier touches it). The headline: attack speed never shortens the added time.
- additional_weapon_base_attack_time_ms (added to the weapon's base time, so attack speed shortens it too). The row.
(UNVERIFIED which: poe2db's pages for current skills with the first stat, Leap Slam and Vaulting Impact, word it "Total
Attack Time", not "Added Attack Time"; question with RESEARCH54.)

Stages (Worldcutter): the hold is the skill's own payoff (LANE_RULES 'Channel holds'), worked out by payoff_stage from its
maximum stages and damage per stage. Whether its 2.67 s is each stage's time or the whole hold is UNVERIFIED: the
headline takes each stage (the lower DPS), the row shows the whole-hold reading.
"""

from __future__ import annotations

from typing import Any


def stage_damage(stage: int, more_per_additional_pct: float) -> float:
    """The damage multiplier at a stage ('N% more per additional Stage': the per-stage values add, x5 at 3 stages of
    200%)."""
    return 1 + more_per_additional_pct / 100 * (stage - 1)


def stage_rows(maximum: int, more_per_additional_pct: float) -> list[tuple[int, float]]:
    """(stage, DPS as a share of the best release) for each release point, each stage taking equal time."""
    rates = {n: stage_damage(n, more_per_additional_pct) / n for n in range(1, maximum + 1)}
    best = max(rates.values())
    return [(n, rate / best) for n, rate in rates.items()]


def payoff_stage(maximum: int, more_per_additional_pct: float) -> int:
    """The release point with the most damage per second of hold."""
    return max(stage_rows(maximum, more_per_additional_pct), key=lambda row: (row[1], row[0]))[0]


def scaled_reading_ratio(speed: float, weapon_rate: float, added_s: float, skill_speed: float = 1.0) -> float | None:
    """The DPS if attack speed also shortens the added time, over the headline's. `speed` is PoB's uses a second with
    the time added after speed modifiers: 1 / speed = base / k + added, so k = base / (1 / speed - added), and the other
    reading's time is (base + added) / k. None when the numbers can't give a speed modifier."""
    if speed <= 0 or weapon_rate <= 0:
        return None
    base = 1 / (weapon_rate * skill_speed)
    left = 1 / speed - added_s
    if left <= 0:
        return None
    k = base / left
    return (1 / speed) / ((base + added_s) / k)


def _added_time_row(row: dict, result: dict | None, bases: dict | None) -> str:
    added = row["model"]["added_attack_time_ms"] / 1000
    text = (f"Added Attack Time +{added:g} s, two readings (UNVERIFIED): the headline adds it after attack speed; if "
            "attack speed also shortens it, DPS is higher")
    result = result or {}
    gear = result.get("gear") or {}
    weapon, off_hand = gear.get("Weapon 1") or {}, gear.get("Weapon 2") or {}
    stats = result.get("stats") or {}
    is_main = (result.get("summary") or {}).get("mainSkill") == row["name"]
    base = (bases or {}).get(weapon.get("base")) or {}
    rate = (base.get("weapon") or {}).get("AttackRateBase")
    # (dual wielding alternates two weapon times: no single base to solve for, so no number)
    if is_main and rate and off_hand.get("type") not in row.get("weaponTypes", ()):
        ratio = scaled_reading_ratio(float(stats.get("Speed") or 0), rate, added,
                                     row["model"].get("attack_speed_pct", 100) / 100)
        if ratio:
            return f"{text}: x{ratio:.2f} on this build"
    return text


def _stage_row(row: dict) -> str:
    held = row["model"]["held_stages"]
    maximum, more = held["maximum"], held["more_per_additional_pct"]
    best = payoff_stage(maximum, more)
    shares = ", ".join(f"{n}: {share:.0%}" for n, share in stage_rows(maximum, more))
    seconds = held["attack_time_ms"] / 1000
    return (f"Held to {best} stages: the most damage per second of hold from its own maximum of {maximum} stages and "
            f"{more:g}% more damage per additional stage (DPS by release stage, equal time a stage: {shares}). Its "
            f"{seconds:g} s is UNVERIFIED: the headline reads it as each stage's time ({seconds * best:g} s a release); "
            f"if it is the whole hold, DPS is x{best:g}")


def rows(row: dict, result: dict | None = None, bases: dict | None = None) -> list[str]:
    """The reading rows for a preview gem row (empty for a gem with no open reading)."""
    model: dict[str, Any] = row.get("model") or {}
    out = []
    if model.get("added_attack_time_ms"):
        out.append(_added_time_row(row, result, bases))
    if model.get("held_stages"):
        out.append(_stage_row(row))
    return out
