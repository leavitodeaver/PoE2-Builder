"""Haemorrhage's Rupture (PREVIEW3; the manager's ruling, 23 Nov 14:53: a labelled model instead of 0, CLAUDE.md's
never-zero rule; every number '1.0 preview, subject to change').

poe2db Haemorrhage (level 20, read 23 Nov 2026, paraphrased): Bleeding targets it Hits have their Bleeding Ruptured,
"causing the Bleeding damage to be dealt more quickly"; each strike Ruptures Bleeding equal to 200% of its Physical Hit
damage dealt, 400% on the Final Strike (one strike in three). No game definition of Rupture exists yet (UNVERIFIED;
RESEARCH55 R55-10, research/haemorrhage-rupture-r55.md).

Headline, R55-10's working reading (MEDIUM-LOW): a Bleed's own total doesn't change, it is dealt sooner. That is still
damage, because Bleeds don't stack: only the strongest deals damage, so a Bleed replaced while it still has damage left
never deals the rest. Rupture drains the active Bleed at each strike, up to its limit, so the Bleeds a build inflicts
deal their whole total instead. PoB scores the strongest Bleed as always active (CalcOffence's BleedDPS, no uptime), so
the damage recovered over PoB's number is

    BleedDPS x max(0, Bleeds a second x Bleed duration - 1)

(each Bleed's total = BleedDPS x duration, LOW: PoB's strongest-Bleed size for every Bleed), and never more than the
strikes' Rupture limit: hits a second x 266.7% (the three strikes' average) x the Physical Hit after mitigation
(PhysicalHitAverage, non-critical: the lower limit). The strike is read to Rupture the old Bleed before its own new
Bleed lands (UNVERIFIED).

Rows: 0 (if a replaced Bleed's damage isn't lost after all, Rupture only moves damage earlier); the "adds" reading (each
strike deals its limit as extra Bleeding damage while a Bleed is active), the upper case. No flat rate: the hits, Bleed
chance and duration are the build's own (PoB's Speed, HitChance, BleedChancePerHit, BleedDuration).
"""

from __future__ import annotations

import math
from typing import Any

from builder.preview import ASTERISK

SKILL = "Haemorrhage"
LIMIT = (2.0 + 2.0 + 4.0) / 3  # 200% on two strikes, 400% on the Final Strike (poe2db)
LABEL = f"Haemorrhage's Rupture{ASTERISK}"
FIELDS = ["Speed", "HitChance", "BleedDPS", "BleedDuration", "BleedChancePerHit", "PhysicalHitAverage"]


def rupture_gain(bleed_dps: float, duration: float, hits: float, chance: float, physical_hit: float,
                 limit: float = LIMIT) -> tuple[float, float, float]:
    """(headline Bleeding damage a second recovered, the 'adds' reading's, Bleeds a second). `chance` is 0-1."""
    if bleed_dps <= 0 or duration <= 0 or hits <= 0 or chance <= 0:
        return 0.0, 0.0, 0.0
    bleeds = hits * min(chance, 1.0)
    cap = hits * limit * physical_hit if physical_hit > 0 else math.inf
    headline = min(bleed_dps * max(0.0, bleeds * duration - 1.0), cap)
    active = 1.0 - math.exp(-bleeds * duration)  # (a Bleed live at a strike: Bleeds as random arrivals)
    adds = hits * limit * physical_hit * active if physical_hit > 0 else headline
    return headline, max(adds, headline), bleeds


def rupture_model(build: Any, main: Any) -> tuple[list[dict], list[dict]]:
    """The main skill's Bleeding damage x (1 + Rupture's share), with the readings as rows."""
    from builder.search import models

    if main is None or main.name != SKILL:
        return [], []
    stats = build.call("stats", fields=FIELDS)
    n = models.number
    bleed, duration = n(stats.get("BleedDPS")), n(stats.get("BleedDuration"))
    hits = models.hits_per_second(stats)
    chance = min(max(n(stats.get("BleedChancePerHit")), 0.0), 100.0) / 100
    physical = n(stats.get("PhysicalHitAverage"))
    headline, adds, bleeds = rupture_gain(bleed, duration, hits, chance, physical)
    if headline <= 0 and adds <= 0:
        why = ("the build inflicts no Bleeding" if bleed <= 0 or chance <= 0 else
               f"{bleeds:.2f} Bleeds a second of {duration:.2f} s never overlap, so none is cut short")
        return [], [{"label": LABEL, "value": 0, "confidence": "LOW",
                     "basis": f"Rupture: {why}: 0{ASTERISK}"}]
    share, upper = headline / bleed, adds / bleed
    basis = (f"Rupture drains the active Bleed at each strike (up to {LIMIT:.0%} of the Physical Hit on average): "
             f"{hits:.2f} hits a second, {chance:.0%} Bleed chance, {bleeds:.2f} Bleeds a second of {duration:.2f} s. "
             f"Bleeds don't stack, so without it a replaced Bleed's rest is never dealt; with it each Bleed's whole "
             f"total is dealt: +{share:.0%} Bleeding damage. Reading: the same total dealt sooner (RESEARCH55 R55-10, "
             f"MEDIUM-LOW; no game definition, UNVERIFIED), the old Bleed Ruptured before the strike's own (UNVERIFIED), "
             f"each Bleed at PoB's strongest-Bleed size (LOW). Rows: +0% if a replaced Bleed's damage isn't lost; "
             f"+{upper:.0%} if each strike adds its Rupture limit as extra damage (the upper case){ASTERISK}")
    note = {"label": LABEL, "value": round(share, 3), "confidence": "LOW", "basis": basis,
            "play": f"Keep hitting a Bleeding boss with Haemorrhage: each strike Ruptures the Bleed it has. Without "
                    f"Rupture: +0%; with it: +{share:.0%} Bleeding damage (if it adds damage: +{upper:.0%}){ASTERISK}"}
    if share <= 0:
        return [], [note]
    return [models.line("1% more Damage with Bleeding", 100 * share, skill=SKILL)], [note]
