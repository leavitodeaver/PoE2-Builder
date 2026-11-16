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
    "conditionDodgeRolledRecently": (PLAYER, "dodge roll at least every 4 s (a build that leans on rolling rolls about "
                                             "every 2 s, the user's default; MECHC27, LOW)"),
    "conditionFullEnergyShield": (FIGHT, "you're on Full Energy Shield: the boss's landed hits take you off it, your Energy "
                                         "Shield recovery and Recharge bring you back (MECHC27, MEDIUM)"),
    "conditionFullLife": (FIGHT, "you're on Full Life: the boss's landed hits and your Life costs take you off it, your "
                                 "Life recovery brings you back (MECHC27: the share from both, MEDIUM)"),
    "conditionShapeshiftToAnimal": (PLAYER, "shift between Human and animal form at least every 4 s (your presses of "
                                            "skills the form can't use shift you back; MECHC34, MEDIUM)"),
    "conditionShapeshiftToHuman": (PLAYER, "shift back to Human form at least every 4 s (MECHC34, MEDIUM)"),
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
    """The other range for the boss distance (10 units a metre): close if the headline stands far, far if close. A ranged
    build the search moved in to the edge of its Presence (conditions.distance_candidates; RESEARCH25 #151) is compared
    with standing back at the ranged distance, its "without it" (MECHC8: before, with close range, which hid the cost of
    stepping out of its Presence)."""
    return RANGED_DISTANCE * 10 if number(value) < RANGED_DISTANCE * 10 else MELEE_DISTANCE * 10


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
        if row["var"] == "onLeyline":  # (MECHC26; the user, 15 Nov: "a note in the build result showing dps for both")
            rows.append({"var": "onLeyline", "label": f"Leylines: on a Leyline {row['uptime']:.0%} of the fight*",
                         "kind": PLAYER, "counted": True, "uptime": row["uptime"],
                         "dps_share": (headline - never) / headline,
                         "play": (f"stand in the Leylines: {always:,.0f} DPS inside them, {never:,.0f} outside; the headline "
                                  f"counts {row['uptime']:.0%} inside (the user, 15 Nov: a player who takes it stands in them)"),
                         "dps_with": always, "dps_without": never, "change": (always - never) / never,
                         "confidence": "the user's share (LOW: where leylines appear is the game's)"})
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
    if row := alignment_row(search, headline):
        rows.append(row)
    if row := sigil_row(search, headline):
        rows.append(row)
    if row := boss_moving_row(search, headline):
        rows.append(row)
    if row := raised_row(search, headline):
        rows.append(row)
    if row := heavy_stunned_row(search, headline):
        rows.append(row)
    if row := consecrated_row(search, headline):
        rows.append(row)
    if row := orb_near_row(search, headline):
        rows.append(row)
    if row := curse_lapse_row(search, headline):
        rows.append(row)
    rows += kept_flask_rows(search, headline)
    rows += curse_zone_rows(search, headline)
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


# The ledger entries counted at Alignment's matched share (models.alignment_match_share, COVE5; share: alignment)
ALIGNMENT_ENTRIES = ("support-alignment-damage", "support-alignment-projectiles")


def alignment_row(search: Any, headline: float) -> dict | None:
    """Alignment's "without it / with it" row (MECHC10; FIX_QUEUE ~09:52, COVE3/COVE5): the headline counts the matched
    share of attacks (stepping round the boss after each match); without it (standing still) only the first attack
    matches, so the bonus is ~0: one recalculation with the Alignment lines left out. The play text is the entry's own
    (models.alignment_match_share). Counted in the headline, so the row moves nothing there."""
    build = search.build
    mods = list(getattr(search.assumptions, "mods", None) or [])
    kept = [m for m in mods if m.get("entry") not in ALIGNMENT_ENTRIES]
    if len(kept) == len(mods):
        return None
    try:
        build._committed(build.call("setAssumptions", mods=kept, fields=build.objective.fields))
        still = build.objective.dps(build.stats)
    finally:
        build._committed(build.call("setAssumptions", mods=mods, fields=build.objective.fields))
    if abs(headline - still) < MIN_CHANGE * max(headline, 1e-9):
        return None
    play = next((n.get("play") for n in getattr(search.assumptions, "active", None) or []
                 if n.get("entry") in ALIGNMENT_ENTRIES and n.get("play")), "")
    return {"var": "alignment:match", "label": "Alignment: step round the boss to match the indicated direction",
            "kind": PLAYER, "counted": True,
            "play": (play + " " if play else "") + f"{headline:,.0f} DPS stepping round the boss (the headline), "
                                                  f"{still:,.0f} standing still",
            "dps_with": headline, "dps_without": still, "change": (headline - still) / still if still else 0.0,
            "confidence": "theory (the match interval LOW)"}


def sigil_row(search: Any, headline: float) -> dict | None:
    """Sigil of Power's inside / outside row (MECHC29; the manager 16:11, by the user's Leylines reasoning, 15 Nov:
    "assume you're trying to stand in them if you take the ability, so 75% with a note in the build result showing dps
    for both inside and outside"): the headline counts conditions.sigil_stages (its stages x 75% of the fight in it, LOW);
    inside = the stages while standing in it, outside = 0 stages. Two recalculations, put back after."""
    from builder.search.conditions import LEYLINE_UPTIME, SIGIL

    info = search.conditions.active.get("sigilOfPowerStages")
    if not info or number(info.get("value")) <= 0:
        return None
    build, held = search.build, number(info["value"])
    inside_stages = held / LEYLINE_UPTIME
    dps = {}
    try:
        for name, value in (("inside", inside_stages), ("outside", 0)):
            build.set_config({"sigilOfPowerStages": value})
            search.assumptions.apply()
            dps[name] = build.objective.dps(build.stats)
    finally:
        build.set_config({"sigilOfPowerStages": held})
        search.assumptions.apply()
    inside, outside = dps["inside"], dps["outside"]
    if outside <= 0 or abs(inside - outside) < MIN_CHANGE * outside:
        return None
    return {"var": "sigilOfPowerStages", "label": f"{SIGIL}: standing in it {LEYLINE_UPTIME:.0%} of the fight*",
            "kind": PLAYER, "counted": True,
            "play": (f"stand in your {SIGIL} while you cast: {inside:,.0f} DPS inside it ({inside_stages:.2f} stages on "
                     f"average), {outside:,.0f} outside; the headline counts {LEYLINE_UPTIME:.0%} inside (the user's "
                     "Leylines reasoning, applied by the manager, 15 Nov 16:11: LOW)"),
            "dps_with": inside, "dps_without": outside, "change": (inside - outside) / outside,
            "confidence": "the user's share (LOW: where you stand is the fight's)"}


def boss_moving_row(search: Any, headline: float) -> dict | None:
    """"Boss moving" (MECHC11; MECH-D's bosses.moving_ceiling, MECHD3 / RESEARCH22 #122): the headline counts the boss
    standing in reach; the chosen boss spends up to that share of the fight in its own dashes, leaps and charges (an
    UPPER BOUND, LOW). A Melee main skill can't reach it while it travels, so its DPS at the ceiling is the headline x (1 -
    share): a "with it" row, never counted. Other main skills (projectiles, targeted spells, minions, totems) are taken as
    still hitting a moving boss (UNVERIFIED: a ground effect or totem out of reach loses some too), so no row."""
    from builder.search import bosses

    build = search.build
    request = getattr(getattr(build, "ctx", None), "request", None)
    stats = {"_boss": getattr(request, "boss", None) or bosses.DEFAULT}
    share, basis = bosses.moving_ceiling(stats)
    types = getattr(getattr(search, "conditions", None), "main_types", None) or set()
    if share < MIN_CHANGE or "Melee" not in types:
        return None
    moving = headline * (1 - share)
    return {"var": "boss:moving", "label": f"Boss moving: {bosses.of(stats).name}'s dashes, leaps and charges",
            "kind": FIGHT, "counted": False,
            "play": (f"{basis}; a melee skill can't reach it while it travels: {moving:,.0f} DPS at that ceiling, "
                     f"{headline:,.0f} with the boss in reach (the headline). Close the gap at once after each dash"),
            "dps_with": headline, "dps_without": moving, "change": (headline - moving) / moving if moving else 0.0,
            "confidence": "theory (an upper bound on the loss; the travel time UNVERIFIED, MECH-D's LOW)"}


# (as models.CURSE_ZONE_SUPPORTS, MECH73: the supports holding curse_apply_as_curse_zone; kept here so the row's precheck
# runs before MECH73's commit lands)
CURSE_ZONE_SUPPORTS = ("Cursed Ground", "Doedre's Undoing", "Hayoxi's Fulmination")
CURSE_ZONE_PLAY = ("Lay {curse} as {zone}'s cursed area under the boss and fight it there: the numbers assume the boss "
                   "stays inside the area the whole time; whenever it walks out, the curse (and what it gives) is off.")


def curse_zone_rows(search: Any, headline: float) -> list[dict]:
    """A curse the build lays as an area on the ground (Cursed Ground, Doedre's Undoing, Hayoxi's Fulmination: MECH73's
    CurseUse.zone, models.CURSE_ZONE_SUPPORTS; the manager, 14 Nov 20:03, MECH73's wording): one row per such curse,
    saying the headline keeps the boss in the area all fight (MEDIUM, an OVERCOUNT wherever it walks out). No trial: the
    headline counts the curse as on (counted)."""
    from builder.search import conditions

    build = search.build
    if not callable(getattr(build, "groups", None)) or getattr(build, "ctx", None) is None:
        return []  # (a stand-in build without gem groups: no curses to read)
    if not any(g.get("support") and g.get("name") in CURSE_ZONE_SUPPORTS and g.get("enabled", True)
               for group in build.groups() if group.get("enabled") is not False for g in group.get("gems") or []):
        return []  # (no curse-zone support socketed: nothing to read, no extra PoB calls)
    texts = [line["text"] for line in build.call("sourceLines")["lines"]]
    rows, seen = [], set()
    for use in conditions.curse_uses(build, dict(getattr(build, "stats", None) or {}), texts):
        zone = getattr(use, "zone", "")
        if not zone or use.name in seen:
            continue
        seen.add(use.name)
        rows.append({"var": f"curse-zone:{use.name}", "label": f"{use.name}: laid as {zone}'s cursed area*",
                     "kind": PLAYER, "counted": True, "play": CURSE_ZONE_PLAY.format(curse=use.name, zone=zone),
                     "dps_with": headline, "dps_without": headline, "change": 0.0,
                     "confidence": "MEDIUM (no boss-movement data: an OVERCOUNT wherever the boss leaves the area)"})
    return rows


def kept_flask_rows(search: Any, headline: float) -> list[dict]:
    """A flask PoB counts kept at full (a Life flask under Enduring Elixirs; a Mana flask with its own kept line) up only
    part of the fight (MECHC25; the manager, 14 Nov 16:45): PoB's stats with its slot on hold the flask's whole recovery
    (no charge limit) and ours its charge-limited average, so "with it" mixes both. The rows the user reads: while it's
    up, PoB's rate alone; while it's down, 0 from the flask; time-weighted, ours alone (flasks.pob_part_time_share,
    recovery_per_second). Defence numbers only: the DPS doesn't move."""
    from builder.search import flasks
    from builder.search.conditions import FULL_UPTIME, SWITCH_ON

    build = search.build
    equipped = getattr(build, "equipped", None) or {}
    if not any(equipped.get(slot) is not None for slot in flasks.FLASK_SLOTS):
        return []
    gain = flasks.Charges.of(build)
    rows = []
    for slot, pool in flasks.FLASK_SLOTS.items():
        option = equipped.get(slot)
        if option is None:
            continue
        info = build.call("flaskData", raws=[option.raw])["results"][0] or {}
        up = flasks.uptime(info, gain, pool)
        if not SWITCH_ON <= up < FULL_UPTIME:
            continue
        parts = []
        if (more := flasks.low_mana_more(option.raw, pool)) > 1:  # (MECHC34, K5-3)
            parts.append(f"drink it only at Low Mana (35% of maximum or less): x{more:g} recovery a use, counted; "
                         "without waiting for Low Mana: x1")
        targets = [(pool, f"{pool}RegenRecovery", flasks.recovery_per_second(info, gain, pool, option.raw, own=True))]
        if pool == "Life" and gain.life_flask_to_es:
            targets.append(("Energy Shield", "EnergyShieldRegenRecovery", flasks.es_recovery_per_second(info, gain, option.raw)))
        for name, field, ours in targets:
            pob = flasks.pob_part_time_share(build, gain, field, pool)[0]
            if pob > 0:
                parts.append(f"{name}: {pob:,.1f}/s while it's up (PoB's rate, the flask's whole recovery over its "
                             f"duration), 0 while it's down, {ours:,.1f}/s time-weighted (the rate its charges allow, "
                             "counted in the floors)")
        if parts:
            rows.append({"var": f"flask:{slot}", "label": f"{option.name}: kept at full {pool}, up {up:.0%} of the fight",
                         "kind": BUILD, "counted": True, "uptime": up, "play": "; ".join(parts),
                         "dps_with": headline, "dps_without": headline, "change": 0.0, "confidence": "modelled"})
    return rows


def raised_row(search: Any, headline: float) -> dict | None:
    """Bastion of Light's shield taps (MECHC21; THE USER, 14 Nov: an item-granted skill the build presses is a button):
    "with the taps / without the taps". The headline counts the taps (models.raised_plan) when the build makes them
    (build.item_presses names Raise Shield); one trial with them off (models.RAISED_TAPS: the Blind and the taps' time
    both go), conditions and assumptions re-read, then put back. Counted in the headline; a build at its button cap can
    drop the taps (the search's switch) for the "without" figure."""
    from builder.search import models

    build = search.build
    presses = getattr(build, models.ITEM_PRESSES, None) or {}
    if models.RAISE_SHIELD not in presses or not models.raised_taps_on(build):
        return None
    rate, how = presses[models.RAISE_SHIELD]
    setattr(build.objective, models.RAISED_TAPS, False)
    try:
        search.settle()
        without = build.objective.dps(build.stats)
    finally:
        setattr(build.objective, models.RAISED_TAPS, True)
        search.settle()
    return {"var": "press:Raise Shield", "label": f"Raise Shield: tapped {rate:.2f} times a second for Bastion of Light's "
                                                  "Blind (a button)*", "kind": PLAYER, "counted": True,
            "play": (f"{how}: {headline:,.0f} DPS with the taps (the headline), {without:,.0f} without them. Raise Shield "
                     "counts as a button while you tap it; at the button cap, leave the taps (and Bastion's Blind) out"),
            "dps_with": headline, "dps_without": without,
            "change": (headline - without) / without if without > 0 else 0.0, "confidence": "theory (the tap's length LOW)"}


HEAVY_STUNNED_SELF = "heavy_stunned_self"  # (assumptions.heavy_stunned_self sets it: (share, Heavy Stuns/s, how))


def heavy_stunned_row(search: Any, headline: float) -> dict | None:
    """Heavy Stuns on you while channelling with the shield raised (MECHC30, assumptions.heavy_stunned_self): the headline
    counts the stunned share off the main skill's time; the row gives the DPS if no blocked hit ever stunned you (the
    line is "more Attack and Cast Speed" x -100 x share, so DPS / (1 - share)), and what raises the threshold."""
    share, rate, how = getattr(search.build, HEAVY_STUNNED_SELF, None) or (0.0, 0.0, "")
    if share <= 0 or share >= 1:
        return None
    without = headline / (1 - share)
    return {"var": "heavyStunnedSelf", "label": f"Heavily Stunned {share:.0%} of the fight while channelling with your "
                                                "shield raised*", "kind": PLAYER, "counted": True,
            "play": (f"{how}. {headline:,.0f} DPS with the Heavy Stuns (the headline), {without:,.0f} if none landed. "
                     "A higher Stun Threshold (Life; Unbreakable, Steadfast; Dionadair's double while raised) or more "
                     "Evasion (an equal chance to avoid the buildup) cuts them"),
            "dps_with": headline, "dps_without": without, "change": (headline - without) / without,
            "confidence": "MEDIUM (the buildup factor is the game's constant; its formula PoB's for monsters)"}


def consecrated_row(search: Any, headline: float) -> dict | None:
    """Your Consecrated Ground, inside / outside (MECHC30; the manager, 15 Nov 20:55, the user's Leylines ruling: "75% with
    a note in the build result showing dps for both inside and outside"): it moves no DPS (no player source makes your
    ground hurt enemies), so the row gives the Life regenerated and the Curse effect on you inside it and outside it; the
    headline's recovery counts 75% of the time it's laid (assumptions.consecrated_ground)."""
    found = getattr(search.build, "consecrated_ground", None)
    if not found:
        return None
    up, laid, effect, how = found
    life = float((search.build.stats or {}).get("Life") or 0.0)
    regen = 5.0 * effect
    return {"var": "consecratedGround", "label": "Consecrated Ground: standing inside it / outside it*", "kind": PLAYER,
            "counted": True,
            "play": (f"{how}: laid {laid:.0%} of the fight. Inside it: {regen:.1f}% of maximum Life regenerated a second "
                     f"({regen / 100 * life:,.0f} Life/s) and {50 * effect:.0f}% reduced Curse effect on you; outside it: "
                     f"neither. The recovery counts you inside {up:.0%} of the fight (75% of the time it's laid: the "
                     f"user's Leylines ruling). DPS is the same inside and outside ({headline:,.0f})"),
            "dps_with": headline, "dps_without": headline, "change": 0.0,
            "confidence": "LOW (standing in it 75% of the time it's laid)"}


# MECH-B's side-Orb note (models.orb_cycle, MECHB28) and the words its basis carries when the main Spell feeds the Orb
ORB_NOTE_LABEL = "Orb of Storms: bolts per second beside the main skill*"
ORB_NEAR_WORDS = "casts/s near the Orb (a play strategy"


def orb_near_row(search: Any, headline: float) -> dict | None:
    """A side Orb of Storms fed by the main Spell's casts (MECHC26; MECHC25's Handover 2): the headline takes every
    main-Spell cast as near the Orb (models.orb_cycle: each fires a bolt, so the Orb ends at its maximum bolts sooner and
    is recast); one trial with no cast near it (its timer bolts only, the Orb lasting its whole duration), conditions and
    assumptions re-read, then put back. Counted in the headline. The trial hands orb_cycle the main skill's row at 0
    casts a second (the switch MECH-B's code has none of yet: asked for in MECHC26's report); nothing else in
    press_model reads that copy. Lightning Infusion Remnants follow the Orb's presses only once build.orb_presses lands
    (MECHB29): until then both figures carry the same Remnants."""
    from builder.search import models

    note = next((n for n in getattr(getattr(search, "assumptions", None), "active", None) or []
                 if n.get("label") == ORB_NOTE_LABEL and ORB_NEAR_WORDS in (n.get("basis") or "")), None)
    if note is None:
        return None  # (no side Orb, or the main skill isn't a hand-cast Spell: timer bolts only, nothing to choose)
    build, cycle = search.build, models.orb_cycle
    if not getattr(build.objective, "full", True):
        return None  # (the headline scores the main skill alone: the Orb's bolts and recasts aren't in it)
    main = note["basis"].split("'s ", 1)[0]

    def away(orb: dict, main_rate: dict | None, *args: Any, **kwargs: Any) -> tuple[float, float, dict]:
        return cycle(orb, {**main_rate, "speed": 0.0} if main_rate else main_rate, *args, **kwargs)

    models.orb_cycle = away
    try:
        search.settle()
        without = build.objective.dps(build.stats)
    finally:
        models.orb_cycle = cycle
        search.settle()
    return {"var": "orb:near", "label": f"Orb of Storms: cast {main} near the Orb*", "kind": PLAYER, "counted": True,
            "play": (f"Cast {main} where your Orb of Storms is: each cast fires a bolt from it, so an Orb ends at its "
                     f"most bolts sooner and you recast it. {headline:,.0f} DPS casting near it (the headline), "
                     f"{without:,.0f} away from it (its timer bolts only). Bolts: {note['value']}. Every cast is taken "
                     "as near (what 'near' covers: UNVERIFIED)"
                     + ("" if without <= headline else "; for this build casting away from it is stronger (the recasts "
                        "cost the main Spell more than the bolts give): the headline is the lower figure")),
            "dps_with": headline, "dps_without": without,
            "change": (headline - without) / without if without > 0 else 0.0,
            "confidence": "MEDIUM (what 'near' covers UNVERIFIED; the player stays by the Orb)"}


def curse_lapse_row(search: Any, headline: float) -> dict | None:
    """Letting a hand-cast curse run out for Withering Hex (MECHC26; MECH75's models.CURSE_LAPSE, the search's choice:
    a curse cast just after the last ran out finds the boss uncursed, so Withering Hex inflicts its Withered stacks; the
    curse is up D / (D + 0.5 s) of the fight, the gap the player's timing, LOW). One trial with the other choice
    (the curses the build may play so if the headline keeps them up, none if it lets them lapse), then put back."""
    from builder.search import conditions, models

    build = search.build
    if not callable(getattr(build, "call", None)) or getattr(build, "ctx", None) is None:
        return None
    texts = [line["text"] for line in build.call("sourceLines")["lines"]]
    candidates = conditions.curse_lapse_candidates(build, texts)
    if not candidates:
        return None  # (no Withering Hex, or no curse cast by hand)
    objective = build.objective
    current = set(getattr(objective, models.CURSE_LAPSE, None) or ())
    other = set() if current else set(candidates)
    setattr(objective, models.CURSE_LAPSE, other)
    try:
        search.settle()
        trial = objective.dps(build.stats)
    finally:
        setattr(objective, models.CURSE_LAPSE, current)
        search.settle()
    lapsing, kept = (headline, trial) if current else (trial, headline)
    names = ", ".join(sorted(current or other))
    return {"var": "curse-lapse", "label": f"Let {names} run out for Withering Hex*", "kind": PLAYER,
            "counted": bool(current),
            "play": (f"let {names} run out and recast it about {models.CURSE_LAPSE_GAP:g} s later, so each cast lands on an "
                     f"uncursed boss and Withering Hex inflicts its Withered stacks: {lapsing:,.0f} DPS letting it lapse, "
                     f"{kept:,.0f} keeping it up; the headline "
                     + ("lets it lapse (the search chose it)" if current else "keeps it up (the search's choice)")),
            "dps_with": lapsing, "dps_without": kept,
            "change": (lapsing - kept) / kept if kept > 0 else 0.0,
            "confidence": "theory (the recast's timing LOW)"}


__all__ = ["guide", "kind", "KINDS", "OFF_OPTIONS", "PLAYER", "BUILD", "FIGHT"]
