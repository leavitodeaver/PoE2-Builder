"""The 1.0 preview support gems' effects (PREVIEW4; every number '1.0 preview, subject to change'). Which skills each
supports is its gems.json row (requireSkillTypes / excludeSkillTypes, read from its own "Supports ..." text, the 0.5.5
supports with the same wording as the pattern); what it does to the build is here.

poe2db pages read 24 Nov 2026 (numbers exact, text near-verbatim; scratch notes in the PREVIEW4 report):
- Overchannel: "Supports Channelling Skills." "Gain Overchannel after Channelling Supported Skills for 1.5 seconds";
  "Overchannel grants Skills 15% more damage"; a non-Channelling Skill used right after you finish Channelling is
  Empowered with the same bonus.
- Impaling Blade II / Rending Blade: "Supports Strikes you use yourself, causing a gigantic sword to periodically erupt
  from the earth on Hitting a target in front of you" (Impaling Blade II: "impaling them"); cooldown 5 s; Attack Damage
  343% / 143%; blade length 4 / 4.5 m.
- Volatile Shades: "Supports Skills which create Shades, causing them to Trigger an explosion on death"; Attack Damage
  125%, radius 1.6 m; "Modifiers to Shade Damage also apply to explosion Damage".
- Sandstorm: Sand Disks "Gain 40% of damage as extra Lightning" (and less Physical: no number shown).
- Galvanising Blades: Buried Blades grant 3% more melee weapon damage within 2 m, up to 5 Blades.
- Abundant Remnants I: "25% chance for Supported Skills to create an additional Remnant".
No flat rates (CLAUDE.md): the sword's rate is the main strike's own hits under its 5 s cooldown; the Shade explosions
are Shroud's own uses; Overchannel's Empower is the build's own channel uses.
Abundant Remnants I is counted by the existing Remnant models (its description's line), not here: NOTE_ONLY says so.
"""

from __future__ import annotations

from typing import Any

from builder.preview import ASTERISK, gem_rows

OVERCHANNEL = "Overchannel"
OVERCHANNEL_SECONDS = 1.5
OVERCHANNEL_MORE = 15.0
SWORDS = {"Impaling Blade II": (343.0, True), "Rending Blade": (143.0, False)}  # (Attack Damage %, Impales)
SWORD_COOLDOWN = 5.0
VOLATILE = "Volatile Shades"
GALVANISING, GALVANISING_MORE, GALVANISING_MAX = "Galvanising Blades", 3.0, 5.0
BLADE_SECONDS, BLADE_LIMIT = 8.0, 10.0  # (Earthrend's Buried Blades)
VOLATILE_PCT = 125.0
SHADE_MAKERS = {"Shroud": 94.0}  # (the Shade makers whose Shades are modelled, by their own Attack Damage %)
NOTE_ONLY = {
    "Sandstorm": "it boosts only Sand Disks, which count 0 until 1.0 publishes when a Stance part fires (readings.py's "
                 "stance rows); its 'less Physical' number isn't shown: 0",
    "Abundant Remnants I": "its 25% chance of an additional Remnant is counted by the build's Remnant models from its "
                           "text (the bridge's sourceLines gives each enabled gem's description; assumptions."
                           "remnant_multiplier reads 'N% chance to create an additional Remnant'), on every Remnant the "
                           "build makes: an OVERCOUNT where Remnants also come from a skill it doesn't support (LOW)",
}
FIELDS = ["AverageHit", "MainHand.AverageHit", "Speed", "HitChance", "PhysicalHitAverage",
          "MainHand.PhysicalHitAverage", "LedgerImpaleChance", "LedgerImpaleEffect"]


def _row(name: str) -> dict | None:
    return next((r for r in gem_rows() if r["name"] == name), None)


def overchannel_hold(name: str, types: set[str]) -> tuple[float | None, str]:
    """(seconds a release is held, why): a preview staged channel's own hold (held_stages at its payoff stage);
    a Perfect Timing channel: None (released at its timing window, not read here); any other channel: None meaning held
    on (a sustained channel kept up against the boss)."""
    from builder.preview.readings import payoff_stage

    row = _row(name)
    held = ((row or {}).get("model") or {}).get("held_stages")
    if held:
        stage = payoff_stage(held["maximum"], held["more_per_additional_pct"])
        return held["attack_time_ms"] / 1000 * stage, f"held to its payoff stage {stage} (readings.py)"
    if "PerfectTiming" in types:
        return 0.0, "released at its Perfect Timing window, whose time isn't read here (an UNDERCOUNT if it is past 1.5 s)"
    return None, "a channel kept up against the boss (LOW: the first 1.5 s of each channel are left out)"


def _number(value) -> float:
    from builder.search.models import number

    return number(value)


def support_model(build: Any, main: Any, stats: dict) -> tuple[list[dict], list[dict], float]:
    """(mods on the main skill, notes, added DPS beside it) from the preview supports in the build's enabled groups."""
    from builder.search import conditions, models

    groups = [g for g in build.call("groups")["groups"] if g.get("enabled") is not False]
    gems = build.ctx.gems
    mods: list[dict] = []
    notes: list[dict] = []
    added = 0.0
    main_group = next((g for g in groups for s in g.get("skills") or [] if s.get("name") == main.name), None)
    if main_group is None:
        main_group = next((g for g in groups for gem in g.get("gems") or [] if gem.get("name") == main.name), None)

    def supports(group: dict) -> set[str]:
        return {gem.get("name") for gem in group.get("gems") or [] if gem.get("enabled", True)}

    main_types = set((gems.get(main.name) or {}).get("skillTypes") or [])
    uses = _number(models.at_main_share(stats).get("Speed"))
    hits = uses * min(_number(stats.get("HitChance")) or 100.0, 100.0) / 100

    # Overchannel on the main channel
    if main_group is not None and OVERCHANNEL in supports(main_group) and "Channel" in main_types:
        hold, why = overchannel_hold(main.name, main_types)
        if hold is not None and hold < OVERCHANNEL_SECONDS:
            notes.append({"label": f"Overchannel on {main.name}{ASTERISK}", "value": 0, "confidence": "LOW",
                          "basis": f"{main.name} is {why}: not channelled 1.5 s, so no Overchannel{ASTERISK}"})
        else:
            mods.append(models.line("1% more Damage", OVERCHANNEL_MORE, skill=main.name))
            notes.append({"label": f"Overchannel on {main.name}{ASTERISK}", "value": OVERCHANNEL_MORE,
                          "confidence": "LOW",
                          "basis": (f"15% more damage after 1.5 s of channelling (poe2db): {main.name} is {why}"
                                    + (f", {hold:g} s a release" if hold else "") + f". Row: 0 if released sooner{ASTERISK}"),
                          "play": f"Channel {main.name} for at least 1.5 s each time{ASTERISK}"})
    # Overchannel's Empower: a side channel with it, the main skill not a channel
    elif "Channel" not in main_types:
        side = [g for g in groups if g is not main_group and OVERCHANNEL in supports(g)]
        if side:
            rates = build.call("useRates", indices=[g["index"] for g in side])["rates"]
            for rate in rates:
                types = set((gems.get(rate.get("name")) or {}).get("skillTypes") or [])
                if "Channel" not in types:
                    continue
                hold, why = overchannel_hold(str(rate.get("name")), types)
                used, how, _ = conditions.skill_use_rate(rate, models.press_choices(build))
                if (hold is not None and hold < OVERCHANNEL_SECONDS) or used <= 0 or uses <= 0:
                    notes.append({"label": f"Overchannel's Empower from {rate.get('name')}{ASTERISK}", "value": 0,
                                  "confidence": "LOW",
                                  "basis": f"{rate.get('name')} isn't channelled 1.5 s at a rate the build sets "
                                           f"({how or why}): 0{ASTERISK}"})
                    continue
                share = min(1.0, used / uses)
                more = OVERCHANNEL_MORE * share
                mods.append(models.line("1% more Damage", more, skill=main.name))
                notes.append({"label": f"Overchannel's Empower from {rate.get('name')}{ASTERISK}", "value": round(more, 2),
                              "confidence": "LOW",
                              "basis": (f"each {rate.get('name')} channel of 1.5 s+ ({how}) Empowers the next "
                                        f"non-channelled skill with 15% more: {share:.0%} of {main.name}'s uses, "
                                        f"+{more:.1f}% on average (the 1.5 s hold's time is the side skill's use time "
                                        f"as scored, LOW){ASTERISK}"),
                              "play": f"Channel {rate.get('name')} for 1.5 s, then use {main.name} at once{ASTERISK}"})
                break

    # Impaling Blade II / Rending Blade on the main Strike
    for sword, (pct, impales) in SWORDS.items():
        if main_group is None or sword not in supports(main_group) or "MeleeSingleTarget" not in main_types:
            continue
        label = f"{sword}'s giant sword{ASTERISK}"
        rate = min(1 / SWORD_COOLDOWN, hits)
        own = ((_row(main.name) or {}).get("model") or {}).get("damage_pct")
        if not own or rate <= 0:
            why = ("the main skill isn't a 1.0 preview skill, so its damage effectiveness (the sword's yardstick) isn't "
                   "known here (the bridge doesn't publish it): 0, an UNDERCOUNT" if not own else "no hits: 0")
            notes.append({"label": label, "value": 0, "confidence": "LOW", "basis": why + ASTERISK})
            continue
        ratio = pct / own
        hit = _number(stats.get("AverageHit") or stats.get("MainHand.AverageHit")) * ratio
        dps = rate * hit
        impale = 0.0
        if impales and _number(stats.get("LedgerImpaleChance")) <= 0:
            physical = _number(stats.get("PhysicalHitAverage") or stats.get("MainHand.PhysicalHitAverage")) * ratio
            impale = rate * models.IMPALE_STORED * physical
        added += dps + impale
        notes.append({"label": label, "value": f"{dps + impale:,.0f} DPS", "confidence": "LOW",
                      "basis": (f"one sword a {SWORD_COOLDOWN:g} s cooldown on hitting ({rate:.3f}/s: {main.name} hits "
                                f"{hits:.2f}/s), each {pct:g}% Attack Damage against {main.name}'s {own:g}%: x{ratio:.2f} its "
                                f"hit ({hit:,.0f}; LOW: the sword taken to get the same modifiers as {main.name}, the "
                                f"blade to reach the boss in front of you, the cooldown not sped up)"
                                + (f"; its Impale Extracted by {main.name}'s next hit, +{impale:,.0f} DPS" if impale else
                                   ("; its Impale not counted beside the skill's own (an UNDERCOUNT)" if impales else ""))
                                + ASTERISK),
                      "play": f"Keep hitting the boss with {main.name}: the sword comes every 5 s{ASTERISK}"})

    # Volatile Shades: Shades that die explode
    for group in groups:
        if VOLATILE not in supports(group):
            continue
        makers = [s for s in supports(group) if s in SHADE_MAKERS]
        if not makers:
            notes.append({"label": f"Volatile Shades{ASTERISK}", "value": 0, "confidence": "LOW",
                          "basis": "its skill's Shades aren't modelled (Shadow Assault's Shade: an UNDERCOUNT): 0"
                                   + ASTERISK})
            continue
        rate_row = next(iter(build.call("useRates", indices=[group["index"]])["rates"]), None)
        used, how, _ = (conditions.skill_use_rate(rate_row, models.press_choices(build)) if rate_row
                        else (0.0, "no rate read", True))
        row = rate_row or {}
        # (an attack's hit is kept per hand, so the row's AverageHit can be absent: then one use's damage, TotalDPS over
        # its uses a second, hit chance included)
        speed = _number(row.get("speed"))
        shroud_hit = _number(row.get("AverageHit")) or (_number(row.get("TotalDPS")) / speed if speed > 0 else 0.0)
        hit = shroud_hit * VOLATILE_PCT / SHADE_MAKERS[makers[0]]
        dps = used * hit
        added += dps
        notes.append({"label": f"Volatile Shades on {makers[0]}{ASTERISK}", "value": f"{dps:,.0f} DPS", "confidence": "LOW",
                      "basis": (f"one Shade a {makers[0]} use ({how}), exploding when it dies at the end of its 6 s or "
                                f"when a newer one passes the limit (LOW: an expiring Shade taken to die), {VOLATILE_PCT:g}% "
                                f"against {makers[0]}'s {SHADE_MAKERS[makers[0]]:g}%: {hit:,.0f} a blast, within its 1.6 m of "
                                f"the boss (LOW). Row: 0 if an expiring Shade doesn't explode{ASTERISK}"),
                      "play": f"Use {makers[0]} next to the boss so its Shade's blast reaches it{ASTERISK}"})
    # Galvanising Blades: Earthrend's Buried Blades near you, 3% more melee weapon damage each, up to 5
    for group in groups:
        names = supports(group)
        if GALVANISING not in names:
            continue
        label = f"Galvanising Blades{ASTERISK}"
        if "Earthrend" not in names:
            notes.append({"label": label, "value": 0, "confidence": "LOW",
                          "basis": "its skill isn't Earthrend, the one preview skill modelled as making Buried Blades: 0"
                                   + ASTERISK})
            continue
        if group is main_group:
            rate, how = uses, f"{main.name}'s own uses"
        else:
            row = next(iter(build.call("useRates", indices=[group["index"]])["rates"]), None)
            rate, how, _ = (conditions.skill_use_rate(row, models.press_choices(build)) if row
                            else (0.0, "no rate read", True))
        blades = min(BLADE_LIMIT, rate * BLADE_SECONDS, GALVANISING_MAX)
        melee = "Melee" in main_types and "Attack" in main_types
        more = GALVANISING_MORE * blades if melee else 0.0
        if more > 0:
            mods.append(models.line("1% more Damage", more, skill=main.name))
        notes.append({"label": label, "value": round(more, 2), "confidence": "LOW",
                      "basis": (f"Earthrend makes a Buried Blade a use ({rate:.2f}/s: {how}), each lasting 8 s (Limit 10): "
                                f"{blades:.2f} alive, up to 5 counted, each 3% more damage with Melee weapons, all taken "
                                f"within 2 m of you (LOW)"
                                + ("" if melee else f"; {main.name} isn't a melee weapon attack: 0") + ASTERISK),
                      "play": f"Fight standing on your Buried Blades (within 2 m){ASTERISK}"})
    for name, why in NOTE_ONLY.items():
        if any(name in supports(g) for g in groups):
            notes.append({"label": f"{name}{ASTERISK}", "value": 0, "confidence": "LOW", "basis": why + ASTERISK})
    if added > 0:  # (the bridge's LedgerAddedDPS for what-ifs: fixed at this resolve, as Thorns', models.added_input)
        mods.append(models.added_input("PreviewSupports", added))
    return mods, notes, added
