"""The "How to play it" guide (scratch spec of 9 Nov, the user's design): the headline DPS counts the conditions the
build leans on, played as intended; this works out what each one is worth, for the finished build only (never during
the search), so the result can say "X DPS without it" and what the player does to keep it.

Every Configuration condition the resolver can set (conditions.CONDITIONS) is classified here, plus PoB options the
resolver never sets that a fight decides or a player chooses:
- player: the player's choice (distance and Presence, close range, standing still). The headline assumes the player
  does it; the row gives the DPS without it.
- build: applied by the build's own rates (ailments, debuffs, charges, Rage, Withered). They keep their modelled uptime.
  One up for only part of the fight is counted at that share (Search.weigh_uptimes) and gets a row: its uptime, the DPS
  with it always up and never up, and its share of the headline.
- fight: decided by the boss (being hit, its life %). A row gives the range: without it if the headline counts it,
  with it if the headline doesn't. The boss's Low Life is weighted into the headline by its share of the boss's life
  (Search.weigh_life); its row gives DPS above and below 35%.
"""

from __future__ import annotations

from typing import Any

from builder.search.conditions import CONDITIONS, MELEE_DISTANCE, RANGED_DISTANCE
from builder.search.objective import number

PLAYER, BUILD, FIGHT = "player", "build", "fight"
MIN_CHANGE = 0.005  # a row only when the condition moves the DPS by at least 0.5%

# var -> (kind, what the player does / what the fight does). Conditions not listed are build-applied.
KINDS: dict[str, tuple[str, str]] = {
    "enemyDistance": (PLAYER, "stand at the distance assumed"),
    "conditionAtCloseRange": (PLAYER, "fight the boss at close range"),
    "conditionStationary": (PLAYER, "stand still while attacking or casting"),
    "conditionHitMeleeRecently": (PLAYER, "land a melee hit at least every 4 s"),
    "parryActive": (PLAYER, "attack as often as you can and Parry the boss's telegraphed hits (about one "
                            "every 5 s assumed), or cast Refutation on cooldown (how much you parry is up to you)"),
    "conditionBeenHitRecently": (FIGHT, "the boss lands a hit on you at least every 4 s (your evasion and block allow it)"),
    "conditionBlockedRecently": (FIGHT, "you block one of the boss's hits at least every 4 s"),
}
# PoB options the resolver never sets: the guide tries each switched on. (label, kind, play). Low Life: "A player or
# monster is on Low Life if it has 35% of its Maximum Life or less" (game files, KeywordPopups).
OFF_OPTIONS: dict[str, tuple[str, str, str]] = {
    "conditionStationary": ("Standing still", PLAYER, "stand still while attacking or casting (not counted in the headline)"),
    "conditionEnemyLowLife": ("Boss on Low Life (its last 35%)", FIGHT,
                              "the execute range: the last 35% of the boss's life (not weighted into the headline)"),
    "conditionEnemyFullLife": ("Boss on Full Life", FIGHT,
                               "only the opening hit, before the boss takes damage (not weighted into the headline: one "
                               "hit is a negligible share of a boss's life)"),
}
assert all(var in {c.var for c in CONDITIONS} for var in KINDS), "KINDS names a condition the resolver doesn't know"


def kind(var: str) -> str:
    return KINDS.get(var, (BUILD, ""))[0]


def _other_distance(value: object) -> float:
    """The other range for the boss distance (10 units a metre): close if the headline stands far, far if close."""
    return RANGED_DISTANCE * 10 if number(value) <= MELEE_DISTANCE * 10 else MELEE_DISTANCE * 10


def guide(search: Any) -> list[dict]:
    """One row per player- or fight-decided condition that moves the finished build's DPS: switch it (one
    recalculation, the assumptions re-applied), read the DPS on the headline's basis, put it back. The trials read the DPS
    before the Low Life weighting (objective.life_factor), which is then applied to every row but the Low Life one: that
    row gives the range (above 35% of the boss's life, and below it)."""
    build, active = search.build, search.conditions.active
    labels = {c.var: c.label for c in CONDITIONS}
    headline = build.objective.dps(build.stats)
    factor = getattr(build.objective, "life_factor", 1.0)
    build.objective.life_factor = 1.0
    base = build.objective.dps(build.stats)
    if base <= 0:
        build.objective.life_factor = factor
        return []
    trials: list[tuple[str, str, str, object, object, bool]] = []  # var, label, kind, trial value, restore value, counted
    weighed = {row["var"] for row in getattr(search, "uptime_rows", [])}  # (their row below: Parried)
    for var, info in active.items():
        k = kind(var)
        if k == BUILD or var in weighed:
            continue
        trial = _other_distance(info["value"]) if var == "enemyDistance" else False
        trials.append((var, labels.get(var, var), k, trial, info["value"], True))
    config = build.call("getConfig")["input"]
    for var, (label, k, _) in OFF_OPTIONS.items():
        if config.get(var) in (None, False, 0):
            trials.append((var, label, k, True, False, False))
    rows = []
    for var, label, k, trial, restore, counted in trials:
        build.set_config({var: trial})
        search.assumptions.apply()
        dps = build.objective.dps(build.stats)
        build.set_config({var: restore})
        search.assumptions.apply()
        if abs(dps - base) < MIN_CHANGE * base:
            continue
        play = KINDS[var][1] if var in KINDS else OFF_OPTIONS[var][2]
        if var == "conditionEnemyLowLife":
            weighted = factor != 1.0
            rows.append({"var": var, "label": label, "kind": k, "counted": weighted,
                         "play": ("the execute range: the last 35% of the boss's life, weighted 35% into the headline "
                                  f"(x{factor:.3f})" if weighted else play),
                         "dps_with": dps, "dps_without": base, "change": (dps - base) / base, "confidence": "theory"})
            continue
        dps, scaled = dps * factor, base * factor
        if var == "enemyDistance":
            label = f"Boss at {number(restore) / 10:g} m (at {number(trial) / 10:g} m instead)"
            play = (f"stay within {number(restore) / 10:g} m of the boss" if number(restore) < number(trial)
                    else f"keep about {number(restore) / 10:g} m from the boss")
        rows.append({
            "var": var, "label": label, "kind": k, "counted": counted, "play": play,
            "dps_with": dps if not counted else scaled, "dps_without": scaled if not counted else dps,
            "change": (dps - scaled) / scaled,
            "confidence": "modelled" if k == PLAYER else "theory",
        })
    build.objective.life_factor = factor
    for row in getattr(search, "uptime_rows", []):  # effects counted at their share of the fight (Search.weigh_uptimes)
        never = headline / row["factor"] * row["ratio"] if row["factor"] > 0 else 0.0
        always = headline / row["factor"] if row["factor"] > 0 else 0.0
        if never <= 0 or abs(headline - never) < MIN_CHANGE * never:
            continue
        if row["var"] == "parryActive":  # the user (11 Nov): "X dps when boss is parried, Y dps when not"
            plan = getattr(search.conditions, "parry_plan", None)
            lost = plan.lost if plan is not None else 0.0
            rows.append({"var": "parryActive", "label": f"{row['label']} (up {row['uptime']:.0%} of the main skill's hits)",
                         "kind": PLAYER, "counted": True, "uptime": row["uptime"],
                         "dps_share": (headline - never) / headline,
                         "play": (f"{KINDS['parryActive'][1]}: {always / (1 - lost):,.0f} DPS while the boss is Parried, "
                                  f"{never / (1 - lost):,.0f} while it isn't (the main skill attacking; the parries take "
                                  f"{lost:.0%} of its time, counted in the headline, and scaling the DPS by attack time "
                                  "is exact for hits, approximate for damage over time)"),
                         "dps_with": headline, "dps_without": never, "change": (headline - never) / never,
                         "confidence": "modelled"})
            continue
        rows.append({"var": row["var"], "label": f"{row['label']} (up {row['uptime']:.0%} of the fight)", "kind": BUILD,
                     "counted": True, "uptime": row["uptime"], "dps_share": (headline - never) / headline,
                     "play": (f"kept up by the build's own rates {row['uptime']:.0%} of the fight, counted at that share "
                              f"({always:,.0f} DPS were it always up)" + (f"; {row['defence']}" if row["defence"] else "")),
                     "dps_with": headline, "dps_without": never, "change": (headline - never) / never,
                     "confidence": "theory"})
        # Another reading of an unsettled rule (conditions.alt_uptimes; the Arcane Surge support's counter, MECHC1): the
        # headline counts the lower uptime; this row gives the DPS at the other one.
        alt = getattr(search.conditions, "alt_uptimes", {}).get(row["var"])
        if alt is not None and alt[0] > row["uptime"]:
            ratio = row["ratio"]
            other = always * (alt[0] + (1 - alt[0]) * ratio)
            rows.append({"var": row["var"], "label": f"{row['label']} up {alt[0]:.0%} instead of {row['uptime']:.0%}",
                         "kind": BUILD, "counted": False, "alt_uptime": alt[0],
                         "play": f"{alt[1]}: {other:,.0f} DPS (not counted in the headline, {headline:,.0f})",
                         "dps_with": other, "dps_without": headline, "change": (other - headline) / headline,
                         "confidence": "theory"})
    # How the build feeds its Combo and walking gems (Usage.play_note: the press hook's rate in plain English, with its
    # "without it / with it" figures in the text; COVC10-11, MECHC7: before, never shown). Counted in the headline at that
    # rate, so the row moves nothing here.
    usage = getattr(search, "usage", None)
    if usage is not None:
        names = sorted({g["name"] for group in build.call("groups")["groups"] if group.get("enabled") is not False
                        for g in group.get("gems") or [] if g.get("enabled", True) and not g.get("support")})
        for name in names:
            if note := usage.play_note(name):
                rows.append({"var": f"usage:{name}", "label": f"{name}: how the build feeds it", "kind": PLAYER,
                             "counted": True, "play": note, "dps_with": headline, "dps_without": headline,
                             "change": 0.0, "confidence": "modelled"})
    if row := seething_pause(search, headline):
        rows.append(row)
    after, base = build.objective.dps(build.stats), headline
    if abs(after - base) > 1e-6 * base:  # the switches must leave the build as it was; say so if they didn't
        rows.append({"var": "", "label": "(restore check)", "kind": BUILD, "counted": False, "play": "",
                     "dps_with": base, "dps_without": after, "change": (after - base) / base,
                     "confidence": f"the build's DPS read {after:,.0f} after the trials, {base:,.0f} before"})
    return sorted(rows, key=lambda r: -abs(r["change"]))


def seething_pause(search: Any, headline: float) -> dict | None:
    """Seething Body managed (MECHC8; FIX_QUEUE "Seething Body"): the headline counts the bar left to fill and reset
    (models.flame_uptime); pausing near the top holds Elemental Archon all fight at the cost of the pauses
    (models.flame_pause_share). One trial with Archon held, scaled by the casting share; a row only when it moves the
    DPS. Not counted in the headline (a play strategy the player chooses; scaling by casting time is exact for hits,
    approximate for damage over time, which goes on during the pauses)."""
    from builder.search import models

    build = search.build
    models.FLAME_MANAGED = True
    try:
        search.assumptions.apply()
        held = build.objective.dps(build.stats)
    finally:
        models.FLAME_MANAGED = False
        search.assumptions.apply()
    if abs(held - headline) < MIN_CHANGE * headline:
        return None  # (no Seething Body Archon, or it's already up all fight)
    stats = build.call("stats", fields=["Mana", "ManaPerSecondCost"])
    share = models.flame_pause_share(number(stats.get("Mana")), number(stats.get("ManaPerSecondCost")))
    paused = held * share
    better = paused > headline
    return {"var": "seething:pause", "label": "Seething Body: pause near the top of Infernal Flame", "kind": PLAYER,
            "counted": False,
            "play": (f"cast until Infernal Flame is nearly full, then stop for about "
                     f"{models.FLAME_IDLE + (1 - models.FLAME_HIGH) / models.FLAME_DECAY:.1f}s (2 s before it decays, "
                     f"then 25% of its maximum per second back to High): Elemental Archon stays up (it doesn't expire on High "
                     f"Flame) and the bar doesn't reset (no Fire hit of your maximum Life and Energy Shield), but you cast {share:.0%} of the time: "
                     f"{paused:,.0f} DPS with pausing, {headline:,.0f} without (the headline: fill and reset)"
                     + ("; pausing is the stronger way to play it" if better else "; not worth it for this build")),
            "dps_with": paused, "dps_without": headline, "change": (paused - headline) / headline,
            "confidence": "theory (decay rate and its basis MEDIUM; the player's timing LOW)"}


__all__ = ["guide", "kind", "KINDS", "OFF_OPTIONS", "PLAYER", "BUILD", "FIGHT"]
