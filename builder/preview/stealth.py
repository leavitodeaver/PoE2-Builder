"""The 1.0 Stealth cycle: Ambushing, Darkness and Shades (PREVIEW2; every number '1.0 preview, subject to change').

Rules from poe2db's Stealth, Ambushing, Darkness, Shade, Shroud and Conceal pages (read 23 Nov 2026, paraphrased):
- an Ambush is an Attack used in Stealth that breaks it; Channelling skills can't Ambush;
- skill cooldowns don't recover in Stealth;
- an Ambush spends all Darkness for 3% more damage a point (default maximum 30);
- an Ambushing Melee Attack is repeated by each Shade in range, a Shade's part at 50% less (default limit 3, 6 s).

No flat rate (CLAUDE.md): the Ambush rate is the build's own Stealth source. Counted: Shroud, at its use rate;
Duplicity (PREVIEW5), at its triggers: pressed and then Hit by the boss (builder/preview/duplicity.py). Not counted (each
an UNDERCOUNT, with no published rate): Shroud's reserved buff ("after not using Skills or being Hit for a short time"),
Eclipse. No source in the build: 0, with a note. Darkness (PREVIEW5): Conceal's 10 on the Stealth it supports, plus
Engulf in Darkness's Remnants (+5 an explosion) and Impales on an Engulfed boss (+1 each) over the cycle, at most 30.
"""

from __future__ import annotations

import math
from typing import Any

from builder.preview import ASTERISK

STEALTH_SKILL = "Shroud"
DARKNESS_SUPPORT = "Conceal"
DARKNESS_ON_STEALTH = 10
DARKNESS_MAX = 30
DARKNESS_MORE = 0.03
SHADE_LIMIT = 3
SHADE_SECONDS = 6.0
SHADE_DAMAGE = 0.5
AMBUSH_SKILLS = frozenset({"Shadow Assault", "Viper Strike"})  # (poe2db's Ambushing page lists them among 1.0's gems here)
LABEL = f"Ambush (Stealth, Darkness, Shades){ASTERISK}"


def shades_alive(cycle: float, seconds: float = SHADE_SECONDS, limit: int = SHADE_LIMIT) -> int:
    """Shades alive at an Ambush with one made per cycle: this cycle's and those younger than their duration."""
    if cycle <= 0:
        return 0
    return min(limit, 1 + math.floor(seconds / cycle - 1e-9))


def ambush_more(ambush_rate: float, uses: float, darkness: float, shades: int,
                knives: float = 1.0) -> tuple[float, float, float]:
    """(average more damage over all the main skill's uses, an Ambush's multiplier, the share of uses that Ambush).
    `knives`: an Ambush's knives on the boss over a plain use's (Whirling Blades' knives per Darkness spent)."""
    if ambush_rate <= 0 or uses <= 0:
        return 0.0, 1.0, 0.0
    share = min(1.0, ambush_rate / uses)
    each = (1 + DARKNESS_MORE * min(darkness, DARKNESS_MAX)) * (1 + SHADE_DAMAGE * shades) * knives
    return share * (each - 1), each, share


def knife_ambush(build: Any, main: Any, stats: dict, darkness: float, ambush_rate: float) -> dict | None:
    """(PREVIEW4) A knife skill's Ambush (builder/preview/knives.py), else None: {'no_hit': its Stealth use deals no
    damage (Ethereal Knives), 'row': that use's Spectral Impale row, 'ratio': an Ambush's knives on the boss over a plain
    use's (Whirling Blades: '+1 projectile for each Darkness spent when Ambushing', before the dual wield 35% more: the
    order is UNVERIFIED, this one gives more knives)}."""
    from builder.preview import knives
    from builder.search import bosses, models
    from builder.search.conditions import MELEE_DISTANCE

    row = knives.knife_row(main.name)
    if row is None:
        return None
    stats = {**stats, **build.call("stats", fields=knives.FIELDS)}
    if row["knives"].get("stealth_version", {}).get("hits") is False:
        return {"no_hit": True, "row": knives.spectral_row(build, main, stats, ambush_rate), "ratio": 0.0}
    per = row["knives"].get("per_darkness_on_ambush", 0) * min(darkness, DARKNESS_MAX)
    if per <= 0:
        return {"no_hit": False, "row": "", "ratio": 1.0}
    dual = models.number(stats.get("OffHand.AverageHit")) > 0
    projectiles = max(models.number(stats.get("ProjectileCount")), 1.0)
    radius, _ = bosses.hit_radius({"_boss": getattr(build.ctx.request, "boss", None)})
    # (at least one knife a use, as knives.knife_model counts: PoB's one)
    plain = max(1.0, knives.knives_on_boss(knives.knife_count(projectiles, dual, row), row["knives"]["layout"], radius,
                                  MELEE_DISTANCE)["headline"])
    more = max(1.0, knives.knives_on_boss(knives.knife_count(projectiles + per, dual, row), row["knives"]["layout"], radius,
                                 MELEE_DISTANCE)["headline"])
    return {"no_hit": False, "row": "", "ratio": more / plain}


def _stealth_group(groups: list[dict], name: str) -> dict | None:
    return next((g for g in groups for gem in g.get("gems") or []
                 if gem.get("name") == name and gem.get("enabled", True)), None)


def _concealed(group: dict | None) -> bool:
    return group is not None and any(g.get("name") == DARKNESS_SUPPORT and g.get("enabled", True)
                                     for g in group.get("gems") or [])


def ambush_model(build: Any, main: Any, stats: dict) -> tuple[list[dict], list[dict]]:
    """The main skill's average more damage from the build's own Ambushes, and its labelled notes. Stealth sources:
    Shroud at its use rate; Duplicity at its triggers (PREVIEW5: pressed AND the boss's next landing Hit,
    builder/preview/duplicity.py). Darkness: Conceal's 10 on the Stealth it supports, plus Engulf in Darkness's (each
    explosion's Remnant +5, each Impale on an Engulfed boss +1) gained over the cycle, capped at 30."""
    from builder.preview import duplicity as dup
    from builder.search import conditions, models

    name = main.name
    gems = build.ctx.gems
    types = set((gems.get(name) or {}).get("skillTypes") or [])
    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    group = _stealth_group(groups, STEALTH_SKILL) if name != STEALTH_SKILL else None
    baiter = _stealth_group(groups, dup.DUPLICITY) if name != dup.DUPLICITY else None
    if group is None and baiter is None:
        if name not in AMBUSH_SKILLS:
            return [], []
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": f"{name} can Ambush, but the build has no skill counted as entering Stealth (Shroud, "
                              "Duplicity): 0. Shroud's reserved buff and Eclipse have no published rate and aren't "
                              "counted (an UNDERCOUNT if the build uses them)"}]
    if "Attack" not in types or "Channel" in types:
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": f"{name} can't Ambush: an Ambush is an Attack and Channelling skills can't (poe2db "
                              "Ambushing): 0"}]
    request = getattr(build.ctx, "request", None)
    read = {**build.call("stats", fields=list(dict.fromkeys([*models.MODEL_FIELDS, *dup.FIELDS]))), **stats,
            "_boss": getattr(request, "boss", None)}
    used, why = 0.0, ""
    if group is not None:
        rates = build.call("useRates", indices=[group["index"]])["rates"]
        rate = next((r for r in rates if r.get("name") == STEALTH_SKILL), None)
        used, why, _ = conditions.skill_use_rate(rate, models.press_choices(build)) if rate else (0.0, "", True)
    baited, bait = dup.duplicity_rate(build, read) if baiter is not None else (0.0, {})
    extra = [dup.duplicity_note(baited, bait)] if bait else []
    speed = models.number(stats.get("Speed"))
    uses = models.number(models.at_main_share(stats).get("Speed"))
    total = used + baited
    sources = " and ".join(s for s, r in ((f"Shroud ({why})", used), (f"Duplicity ({baited:.3f}/s)", baited)) if r > 0)
    if total <= 0 or speed <= 0:
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": ("Shroud / Duplicity is in the build but isn't used"
                               + (f" (Shroud: no rate read{': ' + why if why else ''})" if group is not None else "")
                               + ": 0")}] + extra
    # (its cooldown doesn't recover in Stealth, which lasts until the Ambush: one main-skill use later, LOW)
    cycle = 1 / total + 1 / speed
    conceal = DARKNESS_ON_STEALTH * (used * _concealed(group) + baited * _concealed(baiter)) / total
    explosions, boom = dup.engulf_explosions(read, name, baited)
    gain, gained = dup.darkness_gain(read, name, baited, explosions)
    darkness = min(DARKNESS_MAX, conceal + gain * cycle)
    melee = "Melee" in types
    shades = shades_alive(1 / used + 1 / speed) if used > 0 and melee else 0
    knife = knife_ambush(build, main, stats, darkness, 1 / cycle)
    if knife is not None and knife["no_hit"]:  # (PREVIEW4: Ethereal Knives' Stealth version deals no damage)
        share = min(1.0, (1 / cycle) / uses) if uses > 0 else 0.0
        basis = (f"{sources} put {share:.0%} of {name}'s uses in Stealth (every {cycle:.2f} s), and {name} used from "
                 f"Stealth doesn't hit (poe2db: its Stealth version 'Does not Hit'): those uses deal no damage, "
                 f"{-share:+.1%} DPS. {knife['row']}{ASTERISK}")
        return ([models.line("1% more Damage", -100 * share, skill=name)],
                [{"label": LABEL, "value": round(-share, 3), "confidence": "LOW", "basis": basis,
                  "play": f"Don't use {name} from Stealth for damage: its Stealth throw deals none{ASTERISK}"}] + extra)
    ratio = knife["ratio"] if knife else 1.0
    more, each, share = ambush_more(1 / cycle, uses, darkness, shades, ratio)
    plain, _, _ = ambush_more(1 / cycle, uses, darkness, 0, ratio)
    bare, _, _ = ambush_more(1 / cycle, uses, conceal, shades, ratio)
    lived = shades_alive(cycle) if melee and baited > 0 else shades
    living, _, _ = ambush_more(1 / cycle, uses, darkness, lived, ratio)
    if more <= 0:
        return [], [{"label": LABEL, "value": 0, "confidence": "LOW",
                     "basis": f"{sources}: an Ambush every {cycle:.2f} s, but no Darkness (no Conceal, no Engulf) and no "
                              "Shade repeats this skill: 0"}] + extra
    basis = (f"{sources} give an Ambush every {cycle:.2f} s, {share:.0%} of {name}'s uses. Each Ambush: "
             f"{darkness:.3g} Darkness x{1 + DARKNESS_MORE * darkness:.2f} (Conceal {conceal:.3g}"
             + (f"; Engulf in Darkness +{gain * cycle:.1f} a cycle: {gained}; explosions from {boom}" if gain > 0 else "")
             + f"; at most {DARKNESS_MAX}), {shades} Shade(s) repeating it at 50% less"
             + (f", +{darkness:.0f} knives for the Darkness spent (x{ratio:.2f} the knives on the boss)"
                if knife and ratio != 1.0 else "")
             + f", x{each:.2f} in all. Shades are taken to be in range (LOW: no range is published)"
             + (", Duplicity's Shade taken to die to the Hit it takes (327 Life)" if baited > 0 else "")
             + f". Rows: with them {more:+.1%} DPS on average, with none in range {plain:+.1%}"
             + (f", without Engulf's Darkness {bare:+.1%}" if gain > 0 else "")
             + (f", Duplicity's Shades living their 6 s too ({lived}) {living:+.1%}" if lived != shades else "")
             + f". Other Darkness sources aren't counted (an UNDERCOUNT){ASTERISK}")
    plays = []
    if used > 0:
        plays.append(f"Use Shroud on cooldown next to the boss, then attack with {name} to Ambush")
    if baited > 0:
        plays.append(f"press Duplicity as the boss winds up and let its Shade take the Hit, then Ambush with {name}")
    if gain > 0:
        plays.append("collect the Engulf explosions' Remnants (+5 Darkness each)")
    play = "; ".join(plays)
    note = {"label": LABEL, "value": round(more, 3), "confidence": "LOW", "basis": basis,
            "play": play[:1].upper() + play[1:] + f". Stay close so your Shades can reach the boss. Without a Stealth source: no "
                                       f"Ambush{ASTERISK}"}
    return [models.line("1% more Damage", 100 * more, skill=name)], [note] + extra
