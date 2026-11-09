"""The "How to play it" guide (scratch spec of 9 Nov, the user's design): the headline DPS counts the conditions the
build leans on, played as intended; this works out what each one is worth, for the finished build only (never during
the search), so the result can say "X DPS without it" and what the player does to keep it.

Every Configuration condition the resolver can set (conditions.CONDITIONS) is classified here, plus PoB options the
resolver never sets that a fight decides or a player chooses:
- player: the player's choice (distance and Presence, close range, standing still). The headline assumes the player
  does it; the row gives the DPS without it.
- build: applied by the build's own rates (ailments, debuffs, charges, Rage, Withered). They keep their modelled uptime
  and get no row.
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
    for var, info in active.items():
        k = kind(var)
        if k == BUILD:
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
    after, base = build.objective.dps(build.stats), headline
    if abs(after - base) > 1e-6 * base:  # the switches must leave the build as it was; say so if they didn't
        rows.append({"var": "", "label": "(restore check)", "kind": BUILD, "counted": False, "play": "",
                     "dps_with": base, "dps_without": after, "change": (after - base) / base,
                     "confidence": f"the build's DPS read {after:,.0f} after the trials, {base:,.0f} before"})
    return sorted(rows, key=lambda r: -abs(r["change"]))


__all__ = ["guide", "kind", "KINDS", "OFF_OPTIONS", "PLAYER", "BUILD", "FIGHT"]
