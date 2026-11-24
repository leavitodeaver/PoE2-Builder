"""Ethereal Knives and Whirling Blades: how many of a use's knives hit one boss (PREVIEW4; every number '1.0 preview,
subject to change').

The rule they break: KeywordPopups Projectile, "only one Projectile in the group can hit each target unless otherwise
specified" (game files; PoB counts one projectile a use on one target). Both 1.0 gems specify otherwise (poe2db, read 24
Nov 2026): Ethereal Knives throws "a fan of daggers, multiple of which can Hit the same target"; Whirling Blades throws "a
volley of knives in a spiral around you ... Multiple knives can hit the same target". Unlike the crossbow fragments
(models.shotgun_fragments_share: Merging, one combined hit), nothing says these knives Merge, so each knife that lands is
its own hit: its own Impale, its own Extraction, its own ailment chance. Counted as DPS MORE with its hits a use
(models.with_hits_per_use, as crossbow_sequence_model does), so DERIVED.impale's hits a second (TotalDPS / AverageHit)
grow with the knives, each knife Impaling and Extracting.

How many land (never a flat count: the build's own projectile count, PoB's ProjectileCount with every +projectile line,
and the boss preset's hit radius):
- '35% more Projectiles while Dual Wielding' (both gems): x1.35 on the count when the main skill hits with both hands
  (PoB's OffHand hit > 0), rounded down (the game's rounding UNVERIFIED; the lower reading).
- Whirling Blades' spiral goes all round you: the boss's share of the circle at melee range, N x 2 asin(r / d) / 360
  degrees (models.nova_unaimed_hits, R39-12's reading for a circle not aimed: the headline, LOW); rows: the circle with one
  knife aimed (models.nova_circle_hits, R31-11 (b)), and every knife.
- Ethereal Knives' fan: its spread angle is unpublished (checked: poe2db's page; the description says "fan", the Stealth
  version "a tighter spread"). The headline follows the manager's shotgun ruling (14 Nov, the user's 9 Nov rule for
  conditions the player controls; models.SHOTGUN_PLAY): thrown point-blank, every knife lands (LOW). Rows: one knife (the
  group rule, PoB's), and half the fan.
"""

from __future__ import annotations

import math
from typing import Any

from builder.preview import ASTERISK, gem_rows

FIELDS = ["ProjectileCount", "OffHand.AverageHit", "PhysicalHitAverage", "MainHand.PhysicalHitAverage",
          "LedgerImpaleEffect"]


def knife_row(name: str) -> dict | None:
    """The preview gem row for a knife skill (one with a 'knives' block), else None."""
    return next((r for r in gem_rows() if r["name"] == name and r.get("knives")), None)


def knife_count(projectiles: float, dual_wielding: bool, row: dict) -> int:
    """Knives a use: PoB's ProjectileCount, x(1 + dual wield more) when dual wielding, rounded down."""
    count = max(projectiles, 1.0)
    if dual_wielding:
        count *= 1 + row["knives"].get("dual_wield_more_pct", 0) / 100
    return max(1, math.floor(count + 1e-9))


def knives_on_boss(count: int, layout: str, radius: float, metres: float) -> dict[str, float]:
    """{'headline', 'rows'...}: knives of one use that hit a boss of `radius` m at `metres` m, by layout."""
    from builder.search import models

    if layout == "spiral":
        return {"headline": models.nova_unaimed_hits(count, radius, metres),
                "aimed": models.nova_circle_hits(count, radius, metres), "all": float(count)}
    return {"headline": float(count), "one": 1.0, "half": max(1.0, count / 2)}


def knife_model(build: Any, main: Any, stats: dict) -> tuple[list[dict], list[dict]]:
    """The main skill's knives on the boss as DPS MORE with its hits a use, and the labelled note."""
    from builder.search import bosses, models
    from builder.search.conditions import MELEE_DISTANCE

    row = knife_row(main.name) if main is not None else None
    if row is None:
        return [], []
    dual = models.number(stats.get("OffHand.AverageHit")) > 0
    count = knife_count(models.number(stats.get("ProjectileCount")), dual, row)
    radius, radius_how = bosses.hit_radius(stats)
    layout = row["knives"]["layout"]
    hits = knives_on_boss(count, layout, radius, MELEE_DISTANCE)
    k = max(1.0, hits["headline"])
    label = f"{main.name}: several knives of a use hit the boss{ASTERISK}"
    dw = f" (x1.35 dual wielding, rounded down)" if dual else ""
    if layout == "spiral":
        how = (f"{count} knives a use{dw} in a spiral all round you: the boss's share of the circle at "
               f"{MELEE_DISTANCE:g} m ({radius_how}), {k:.2f} knives (LOW: the spiral's spacing is unpublished, taken as "
               f"even and not aimed). Rows: one knife aimed at it, {hits['aimed']:g}; every knife, {hits['all']:g}; "
               "PoB's one")
        play = (f"Spin right next to the boss: the closer you are, the more of the spiral's knives hit it (x{k:.2f} "
                f"headline; standing away, fewer).")
    else:
        how = (f"{count} knives a use{dw} in a fan: thrown point-blank, all {count} taken to land (LOW: the fan's angle is "
               f"unpublished; the manager's shotgun ruling for conditions you control). Rows: half the fan, "
               f"{hits['half']:g}; one knife (the projectile group rule, PoB's)")
        play = (f"Throw point-blank so the whole fan lands (x{k:.0f} the hits; from range, as few as one). The backflip "
                "moves you away first: throw while touching the boss.")
    note = {"label": label, "value": f"x{k:.2f}", "confidence": "LOW",
            "basis": (f"poe2db: multiple knives can hit the same target, each its own hit (no Merging): {how}. Each "
                      f"knife Impales and Extracts (Impale's model counts the extra hits){ASTERISK}"),
            "play": play + ASTERISK}
    if k <= 1:
        return [], [note]
    return [models.with_hits_per_use(models.raw_mod("DPS", "MORE", 100 * (k - 1), main.name), k)], [note]


def spectral_row(build: Any, main: Any, stats: dict, ambush_rate: float) -> str:
    """Ethereal Knives' Stealth version as a row (Spectral Impale is undefined: never in the headline). Its knives 'do
    not Hit' and 'Impale targets as though Hitting' at 125% more Impale Magnitude instead of 250%. Read plainly, those
    Impales are weaker than the ones its own hits already keep on the boss, and a hit Extracts only the strongest, so
    they add nothing (the headline). The upper row: every Spectral Impale Extracted beside them."""
    from builder.search import models

    row = knife_row(main.name)
    version = (row or {}).get("knives", {}).get("stealth_version")
    if not version or ambush_rate <= 0:
        return ""
    dual = models.number(stats.get("OffHand.AverageHit")) > 0
    count = knife_count(models.number(stats.get("ProjectileCount")), dual, row)
    physical = models.number(stats.get("PhysicalHitAverage") or stats.get("MainHand.PhysicalHitAverage"))
    effect = models.number(stats.get("LedgerImpaleEffect"))
    own = 1 + dict(row["model"]["constantStats"]).get("active_skill_impale_magnitude_+%_final", 0) / 100
    spectral = effect / own * (1 + version["impale_more_pct"] / 100) if effect > 0 else 0.0
    upper = ambush_rate * count * models.IMPALE_STORED * spectral * physical
    return (f"Stealth version: a use from Stealth doesn't hit (no damage) and inflicts {count} Spectral Impales at 125% "
            f"more magnitude. 'Spectral' is undefined (UNVERIFIED, LOW): read plainly they are weaker than the 250% "
            f"Impales its hits keep on the boss and a hit Extracts only the strongest, so 0 in the headline; upper row, "
            f"every one Extracted: +{upper:,.0f} DPS at {ambush_rate:.3f} Stealth uses a second")
