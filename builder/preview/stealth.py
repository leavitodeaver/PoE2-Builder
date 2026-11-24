"""The 1.0 Stealth cycle: Ambushing, Darkness and Shades (PREVIEW2; every number '1.0 preview, subject to change').

Rules from poe2db's Stealth, Ambushing, Darkness, Shade, Shroud and Conceal pages (read 23 Nov 2026, paraphrased):
- an Ambush is an Attack used in Stealth that breaks it; Channelling skills can't Ambush;
- skill cooldowns don't recover in Stealth;
- an Ambush spends all Darkness for 3% more damage a point (default maximum 30);
- an Ambushing Melee Attack is repeated by each Shade in range, a Shade's part at 50% less (default limit 3, 6 s).

No flat rate (CLAUDE.md): the Ambush rate is the build's own Stealth source. Counted: Shroud, at its use rate. Not counted
(each an UNDERCOUNT, with no published rate): Shroud's reserved buff ("after not using Skills or being Hit for a short
time"), Duplicity (it waits for an enemy hit), Eclipse. No source in the build: 0, with a note.
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


def ambush_more(ambush_rate: float, uses: float, darkness: float, shades: int) -> tuple[float, float, float]:
    """(average more damage over all the main skill's uses, an Ambush's multiplier, the share of uses that Ambush)."""
    if ambush_rate <= 0 or uses <= 0:
        return 0.0, 1.0, 0.0
    share = min(1.0, ambush_rate / uses)
    each = (1 + DARKNESS_MORE * min(darkness, DARKNESS_MAX)) * (1 + SHADE_DAMAGE * shades)
    return share * (each - 1), each, share


def ambush_model(build: Any, main: Any, stats: dict) -> tuple[list[dict], list[dict]]:
    """The main skill's average more damage from the build's own Ambushes, and its labelled note."""
    from builder.search import conditions, models

    name = main.name
    gems = build.ctx.gems
    types = set((gems.get(name) or {}).get("skillTypes") or [])
    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    group = next((g for g in groups for gem in g.get("gems") or []
                  if gem.get("name") == STEALTH_SKILL and gem.get("enabled", True)), None)
    if group is None or name == STEALTH_SKILL:
        if name not in AMBUSH_SKILLS:
            return [], []
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": f"{name} can Ambush, but the build has no skill counted as entering Stealth (Shroud): 0. "
                              "Shroud's reserved buff, Duplicity and Eclipse have no published rate and aren't counted "
                              "(an UNDERCOUNT if the build uses them)"}]
    if "Attack" not in types or "Channel" in types:
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": f"{name} can't Ambush: an Ambush is an Attack and Channelling skills can't (poe2db "
                              "Ambushing): 0"}]
    rates = build.call("useRates", indices=[group["index"]])["rates"]
    rate = next((r for r in rates if r.get("name") == STEALTH_SKILL), None)
    used, why, _ = conditions.skill_use_rate(rate, models.press_choices(build)) if rate else (0.0, "", True)
    speed = models.number(stats.get("Speed"))
    uses = models.number(models.at_main_share(stats).get("Speed"))
    if used <= 0 or speed <= 0:
        return [], [{"label": LABEL, "value": 0, "confidence": "MEDIUM",
                     "basis": f"Shroud is in the build but isn't used (no rate read{': ' + why if why else ''}): 0"}]
    # (its cooldown doesn't recover in Stealth, which lasts until the Ambush: one main-skill use later, LOW)
    cycle = 1 / used + 1 / speed
    conceal = any(g.get("name") == DARKNESS_SUPPORT and g.get("enabled", True) for g in group.get("gems") or [])
    darkness = DARKNESS_ON_STEALTH if conceal else 0
    shades = shades_alive(cycle) if "Melee" in types else 0
    more, each, share = ambush_more(1 / cycle, uses, darkness, shades)
    plain, _, _ = ambush_more(1 / cycle, uses, darkness, 0)
    if more <= 0:
        return [], [{"label": LABEL, "value": 0, "confidence": "LOW",
                     "basis": f"Shroud every {cycle:.2f} s, but no Darkness (no Conceal on it) and no Shade repeats this "
                              "skill: 0"}]
    basis = (f"Shroud ({why}) gives an Ambush every {cycle:.2f} s, {share:.0%} of {name}'s uses. Each Ambush: "
             f"{darkness} Darkness ({'Conceal' if conceal else 'no Conceal'}) x{1 + DARKNESS_MORE * darkness:.2f}, "
             f"{shades} Shade(s) repeating it at 50% less, x{each:.2f} in all. Shades are taken to be in range (LOW: no "
             f"range is published). Rows: with them {more:+.1%} DPS on average, with none in range {plain:+.1%}. Other "
             f"Darkness sources aren't counted (an UNDERCOUNT){ASTERISK}")
    note = {"label": LABEL, "value": round(more, 3), "confidence": "LOW", "basis": basis,
            "play": f"Use Shroud on cooldown next to the boss, then attack with {name} to Ambush. Stay close so its "
                    f"Shade can reach the boss. Without Shroud: no Ambush{ASTERISK}"}
    return [models.line("1% more Damage", 100 * more, skill=name)], [note]
