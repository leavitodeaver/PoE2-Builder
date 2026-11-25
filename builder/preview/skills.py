"""1.0 preview skills' parts beyond their main hit (PREVIEW4; every number '1.0 preview, subject to change'). poe2db pages read
24 Nov 2026 (gems.json's sources):
- Earthrend: the fissure triggers a Buried Blade at its end, whose eruption (66%, the gem's own 66%) hits once: a second
  hit a use when the fissure ends at the boss (LOW). Its Blades (8 s, Limit 10) feed Galvanising Blades (supports.py).
- Blade of Night: every hit Engulfs the target in Darkness (6 s), which explodes for 94% when the target dies or is
  Immobilised. On a boss that is never killed mid-fight, the explosions are the build's own Immobilises: Freeze,
  Electrocute, Pin and Heavy Stun from the main skill (models.immobilise_rate, MECH-A's buildup rates). No Immobilise:
  0, said. Scored against Blade of Night's own 144% (the same modifiers taken to apply, LOW).
- Shadow Assault: three strikes; the Final Strike makes a Shade that dashes (Shade Dash 177%), and the other Shades
  teleport to the target and repeat the dash. A use is read as one strike, so a Final Strike every third use (LOW:
  PoB scores the gem as one hit a use); Shades alive from that cadence (stealth.shades_alive: 6 s, limit 3); each dash
  scored against Shadow Assault's 78% (LOW: the Shade's dash taken at its full 177%, no 50% less: the Ambush rule's
  "50% less" is for Shades repeating your Ambush, not their own dash, UNVERIFIED).
No flat rates: each from the main skill's own uses (PoB's Speed at its main share) and the build's own Immobilises.
The ratios here (94 / 144, 177 / 78, 66 / 66) are two parts of one gem shown at the same level, so they hold at any gem
level if both parts scale alike (LOW); the bridge's LedgerBaseMultiplier (PREVIEW5) would change nothing here and is used
only where the second part is another gem's (supports.py's giant sword).
"""

from __future__ import annotations

from typing import Any

from builder.preview import ASTERISK

FIELDS = ["AverageHit", "MainHand.AverageHit", "Speed", "HitChance"]
ENGULF_PCT, BLADE_OF_NIGHT_PCT = 94.0, 144.0
DASH_PCT, SHADOW_ASSAULT_PCT = 177.0, 78.0
ERUPTION_PCT, EARTHREND_PCT = 66.0, 66.0
IMMOBILISE = {"freeze", "electrocute", "pin", "stun"}


def shade_dashes(uses: float) -> tuple[float, int]:
    """(Final Strikes a second, Shades dashing at each): a Final Strike every third use; the new Shade plus the others
    alive (6 s, limit 3)."""
    from builder.preview.stealth import shades_alive

    finals = uses / 3
    if finals <= 0:
        return 0.0, 0
    return finals, shades_alive(1 / finals)


def skill_model(build: Any, main: Any, stats: dict) -> tuple[list[dict], list[dict], float]:
    """(mods on the main skill, notes, added DPS beside it) for the main skill's 1.0 parts above."""
    from builder.search import models

    if main is None:
        return [], [], 0.0
    hit = models.number(stats.get("AverageHit") or stats.get("MainHand.AverageHit"))
    uses = models.number(models.at_main_share(stats).get("Speed"))
    mods: list[dict] = []
    notes: list[dict] = []
    added = 0.0
    if main.name == "Earthrend":
        mods.append(models.with_hits_per_use(models.raw_mod("DPS", "MORE", 100 * ERUPTION_PCT / EARTHREND_PCT, main.name), 2))
        notes.append({"label": f"Earthrend's Buried Blade eruption{ASTERISK}", "value": "x2.00", "confidence": "LOW",
                      "basis": (f"each fissure ends in a Buried Blade whose eruption hits once ({ERUPTION_PCT:g}%, the "
                                f"gem's own {EARTHREND_PCT:g}%): a second hit a use, the fissure taken to end at the boss "
                                f"(its 1 m eruption). Row: x1 if it ends past the boss. Charged Blades' zaps need Lightning "
                                f"beams (not modelled: an UNDERCOUNT for a beam build){ASTERISK}"),
                      "play": f"Aim Earthrend so the fissure ends under the boss{ASTERISK}"})
    elif main.name == "Blade of Night":
        rate, how = models.immobilise_rate(stats, IMMOBILISE)
        each = hit * ENGULF_PCT / BLADE_OF_NIGHT_PCT
        dps = rate * each
        added += dps
        notes.append({"label": f"Blade of Night's Engulf in Darkness explosions{ASTERISK}", "value": f"{dps:,.0f} DPS",
                      "confidence": "LOW",
                      "basis": (f"every hit Engulfs the boss (6 s), which explodes when Immobilised: the build's own "
                                f"Immobilises, {rate:.3f}/s ({how or 'none'}), each {ENGULF_PCT:g}% against Blade of "
                                f"Night's {BLADE_OF_NIGHT_PCT:g}%, {each:,.0f} (the same modifiers taken to apply; 94% "
                                f"not its own page's 127%: UNVERIFIED). No Immobilise source: 0. Its Remnants' Darkness "
                                f"isn't added to the Ambush (an UNDERCOUNT){ASTERISK}"),
                      "play": (f"Freeze, Electrocute, Pin or Heavy Stun the boss while it is Engulfed: each one blows the "
                               f"Engulf up ({rate:.3f} a second here){ASTERISK}")})
    elif main.name == "Shadow Assault":
        finals, shades = shade_dashes(uses)
        each = hit * DASH_PCT / SHADOW_ASSAULT_PCT
        dps = finals * shades * each
        added += dps
        notes.append({"label": f"Shadow Assault's Shade dashes{ASTERISK}", "value": f"{dps:,.0f} DPS", "confidence": "LOW",
                      "basis": (f"a Final Strike every third use ({finals:.3f}/s from {uses:.2f} uses/s; LOW: a use read as "
                                f"one strike) makes a Shade that dashes, and the other Shades repeat it: {shades} dash(es) "
                                f"each ({shades} Shade(s) alive, 6 s, limit 3), each {DASH_PCT:g}% against its "
                                f"{SHADOW_ASSAULT_PCT:g}%, {each:,.0f} (full damage, no 50% less: UNVERIFIED). Row: one "
                                f"dash a Final Strike, {finals * each:,.0f} DPS{ASTERISK}"),
                      "play": f"Keep the three-strike combo going: every Final Strike sends your Shades through the boss{ASTERISK}"})
    if added > 0:
        mods.append(models.added_input("PreviewSkills", added))
    return mods, notes, added
