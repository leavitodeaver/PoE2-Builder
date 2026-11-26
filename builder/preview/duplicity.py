"""Duplicity and Engulf in Darkness's Darkness (PREVIEW5; every number '1.0 preview, subject to change').

poe2db, read 24 Nov 2026 (https://poe2db.tw/us/Duplicity, https://poe2db.tw/us/Engulf_in_Darkness; text paraphrased,
numbers exact): Duplicity (Attack; AoE, Trigger, Duration, Channelling, Remnant; level 9 shown; 125%; Dagger & Parrying
Shield or Dual Daggers): "Prepare yourself to bait an attack": the next time a nearby enemy would Hit you, Engulf in
Darkness is Triggered on them and others within 2.5 m, you teleport behind them in Stealth, and a Shade (327 Life) is left
in your place to take the Hit and retaliate. Avoiding damage this way builds Heavy Stun, and nothing else happens if the
Hit Heavy Stuns you. No cost, cast time, cooldown, duration or Shade limit is shown. Engulf in Darkness (the Duplicity /
Blade of Night block: 94%; its own level-18 page: 127%: the triggered Engulf follows the triggering gem's level, 9-13
shown on those pages vs 18, R65-18 MEDIUM; 94% kept): 6 s; explodes when the target dies or is Immobilised; the
explosion (1.8 m) leaves a Remnant (12 s) that grants 5 Darkness when collected; Impaling an Engulfed enemy grants 1
Darkness.

No flat rate (CLAUDE.md): a Duplicity trigger needs a press (the search's chosen press rate for its group: the existing
press model, which also takes the press's time off the main skill) AND a boss Hit that would land, so the rate is the
lower of the two. The boss's hits are bosses.benefit_skill_time's "when hit" cadence x boss_hits.land_share (red-flash hits
land; the rest after PoB's evade and block roll). The user's 2-in-3 movement dodge is NOT applied: a player who takes
Duplicity stands in the attack on purpose, since the Shade takes the Hit (a play strategy; the play guide says so).
Rows: every boss hit (no evade or block), and the press rate alone.
LOW / UNVERIFIED, each labelled: no wait between the press and the Hit (pressed as the boss's attack is telegraphed;
row: half the boss's cadence); the Heavy Stun clause not counted (an OVERCOUNT on a build a pinnacle hit Heavy Stuns);
Duplicity's own 125% read as its Shade's retaliation (RESEARCH65 R65-18, MEDIUM-LOW: the Engulf part is listed apart at
94%, so the 125% is the Shade's), one hit a trigger, scored against the main skill's damage effectiveness at its gem level
(the bridge's LedgerBaseMultiplier; the Shade taken to carry the main skill's modifiers, LOW; row: 0); its Shade taken to die
to the Hit it takes (327 Life against a pinnacle hit), so it doesn't repeat Ambushes (row: it lives 6 s, as Shroud's).
"""

from __future__ import annotations

from typing import Any

from builder.preview import ASTERISK

DUPLICITY = "Duplicity"
BLADE_OF_NIGHT = "Blade of Night"
ENGULF_SECONDS = 6.0
ENGULF_PCT = 94.0
RETALIATION_PCT = 125.0  # (R65-18: the Shade's retaliation, MEDIUM-LOW)
REMNANT_DARKNESS = 5.0
IMPALE_DARKNESS = 1.0
SHADE_LIFE = 327
FIELDS = ["enemySkillTime", "EvadeChance", "EffectiveBlockChance", "LedgerImpaleChance", "LedgerBaseMultiplier",
          "AverageHit", "MainHand.AverageHit", "Speed", "HitChance"]


def _group(build: Any, name: str) -> dict | None:
    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    return next((g for g in groups for gem in g.get("gems") or []
                 if gem.get("name") == name and gem.get("enabled", True)), None)


def boss_hit_rate(stats: dict) -> tuple[float, float, str]:
    """(boss Hits a second that would land on you, every boss Hit a second, how): the "when hit" cadence, no dodge."""
    from builder.search import boss_hits, bosses

    every = bosses.benefit_skill_time(stats)
    if every <= 0:
        return 0.0, 0.0, "no boss cadence read"
    land = boss_hits.land_share(stats)
    return (land / every, 1 / every,
            f"{bosses.of(stats).name} hits every {every:.2f} s (the 'when hit' cadence), {land:.0%} landing after your "
            f"evade and block (red-flash hits always), none dodged: you stand in the attack for the Shade to take it")


def duplicity_rate(build: Any, stats: dict) -> tuple[float, dict]:
    """(Duplicity triggers a second, details) for the build's enabled Duplicity group; 0 without one.
    details: 'pressed', 'hits', 'all_hits', 'how', 'group'."""
    from builder.search import conditions, models

    group = _group(build, DUPLICITY)
    if group is None:
        return 0.0, {}
    row = next((r for r in build.call("useRates", indices=[group["index"]])["rates"]
                if str(r.get("name") or "").startswith(DUPLICITY)), None)
    pressed, why, _ = (conditions.skill_use_rate(row, models.press_choices(build)) if row
                       else (0.0, "no rate read", True))
    hits, all_hits, how = boss_hit_rate(stats)
    rate = min(pressed, hits)
    return rate, {"pressed": pressed, "press_how": why, "hits": hits, "all_hits": all_hits, "how": how,
                  "group": group, "speed": models.number((row or {}).get("speed"))}


def retaliation(hit: float, own_pct: float, rate: float) -> float:
    """(R65-18) DPS of Duplicity's Shade retaliating once a trigger at 125% against the main skill's own damage
    effectiveness (`own_pct`, %); 0 without a yardstick."""
    return rate * hit * RETALIATION_PCT / own_pct if own_pct > 0 and rate > 0 else 0.0


def idle_play(d: dict, main_name: str, ambush: float, darkness: float, conceal: bool, retaliate: float = 0.0) -> str:
    """(PREVIEW6; the manager, 26 Nov 06:32) The play line for a Duplicity the build holds but never presses (the search
    tried its press rates and kept 0): why, with the without / with numbers. `ambush` = the average more damage the main
    skill would get from Ambushes with Duplicity pressed on each of the boss's landing Hits (stealth.ambush_more);
    each press takes 1 / its speed from the main skill (as the press model charges it)."""
    rate, speed = d.get("hits", 0.0), d.get("speed", 0.0)
    busy = min(1.0, rate / speed) if speed > 0 else 1.0
    net = (1 + ambush + retaliate) * (1 - busy) - 1
    lead = ("Don't press Duplicity here: the search tried it and kept 0 presses" if net <= 0 else
            "Duplicity isn't pressed in this build (no press rate chosen for it), though pressing it would pay")
    return (f"{lead}. Without it: {main_name} as it is "
            f"(+0%). With it pressed on each of the boss's landing Hits ({rate:.3f}/s): Ambushes add {ambush:+.1%} to "
            f"{main_name} ({darkness:.3g} Darkness an Ambush{'' if conceal else ', no Conceal on it'}; its Shade is taken "
            f"to die to the Hit, so no Shade repeats) and its Shade's retaliation {retaliate:+.1%} (125%, R65-18's "
            f"reading, MEDIUM-LOW), but the presses take {busy:.0%} of the time ({1 / speed if speed > 0 else 0:.2f} s "
            f"each, the weapon's attack time: Duplicity's own use time isn't published, LOW): {net:+.1%} in all{ASTERISK}")


def duplicity_note(rate: float, d: dict) -> dict:
    if not d:
        return {}
    if rate <= 0:
        basis = (f"Duplicity is in the build, but it triggers only when pressed and then Hit: pressed {d['pressed']:.3f}/s "
                 f"({d['press_how']}); boss hits landing {d['hits']:.3f}/s ({d['how']}): 0")
    else:
        basis = (f"pressed {d['pressed']:.3f}/s ({d['press_how']}) and triggered by the boss's next landing Hit "
                 f"({d['hits']:.3f}/s: {d['how']}): {rate:.3f} triggers a second, each Engulfing the boss (6 s) and "
                 f"putting you in Stealth (LOW: no wait between the press and the Hit; the Heavy Stun clause not counted, "
                 f"an OVERCOUNT where the Hit Heavy Stuns you; its 125% counted as its Shade's retaliation in skills.py). Rows: every boss hit "
                 f"{min(d['pressed'], d['all_hits']):.3f}/s; a wait of half the boss's cadence a trigger")
    return {"label": f"Duplicity's triggers{ASTERISK}", "value": round(rate, 3), "confidence": "LOW",
            "basis": basis + ASTERISK,
            "play": (f"Press Duplicity as the boss winds up an attack and stand in it: the Shade takes the Hit, you "
                     f"reappear behind the boss in Stealth. Without it: no Duplicity Stealth{ASTERISK}")}


def engulf_uptime(main_name: str, duplicity: float) -> tuple[float, str]:
    """(share of the fight the boss is Engulfed, how): Blade of Night as the main skill Engulfs on every hit (1);
    else Duplicity's triggers, 6 s each."""
    if main_name == BLADE_OF_NIGHT:
        return 1.0, "Blade of Night Engulfs on every hit"
    if duplicity > 0:
        return min(1.0, duplicity * ENGULF_SECONDS), f"Duplicity's {duplicity:.3f} triggers/s x 6 s"
    return 0.0, ""


def darkness_gain(stats: dict, main_name: str, duplicity: float, explosions: float) -> tuple[float, str]:
    """(Darkness gained a second, how) from Engulf: each explosion's Remnant (+5, collected: LOW) and each Impale on an
    Engulfed boss (+1; one Impale a hit at the main skill's Impale chance)."""
    from builder.search import models

    uptime, how = engulf_uptime(main_name, duplicity)
    if uptime <= 0:
        return 0.0, ""
    hits = models.number(models.at_main_share(stats).get("Speed")) * min(models.number(stats.get("HitChance")) or 100.0,
                                                                     100.0) / 100
    impales = hits * min(models.number(stats.get("LedgerImpaleChance")), 100.0) / 100 * uptime
    gain = explosions * REMNANT_DARKNESS + impales * IMPALE_DARKNESS
    parts = []
    if explosions > 0:
        parts.append(f"{explosions:.3f} Engulf explosions/s x 5 (their Remnants, taken as collected: LOW)")
    if impales > 0:
        parts.append(f"{impales:.3f} Impales/s on the Engulfed boss x 1 ({how}, {uptime:.0%} Engulfed)")
    return gain, "; ".join(parts)


def engulf_explosions(stats: dict, main_name: str, duplicity: float) -> tuple[float, str]:
    """(Engulf explosions a second, how): the build's own Immobilises while the boss is Engulfed."""
    from builder.search import models
    from builder.preview.skills import IMMOBILISE

    uptime, how = engulf_uptime(main_name, duplicity)
    if uptime <= 0:
        return 0.0, ""
    rate, why = models.immobilise_rate(stats, IMMOBILISE)
    return rate * uptime, f"{why or 'no Immobilise'} while Engulfed ({how}, {uptime:.0%})"
