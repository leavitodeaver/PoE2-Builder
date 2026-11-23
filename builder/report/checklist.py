"""The test-in-game checklist (PLAN.md §1): one line for every number the result marks with an
asterisk or reports as not modeled, saying what to check in game and why."""

from __future__ import annotations


def _number(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.0f}" if abs(value) >= 100 else f"{value:g}"
    return str(value)


def checklist(result: dict) -> list[dict]:
    items: list[dict] = []
    assumptions = result.get("assumptions") or []
    for a in assumptions:
        items.append({
            "kind": "assumption", "title": f"{a['label']} *",
            "why": f"Assumed {_number(a['value'])} ({a['basis']}); PoB doesn't model it. Confidence: {a['confidence']}.",
            "how": (a["play"] + " " if a.get("play") else "")
                   + "Watch it in game (the skill's tooltip, or count it over a minute of play). If it's lower than "
                   "assumed, the damage marked * is lower in proportion. The PoB code doesn't carry this assumption.",
        })
    for row in result.get("play_guide") or []:
        if not row.get("var"):
            continue
        counted = row.get("counted")
        if "uptime" in row:  # an effect counted at its share of the fight (Search.weigh_uptimes)
            items.append({
                "kind": "how to play it *", "title": f"{row['label']} *",
                "why": (f"The headline DPS counts it at its uptime, {row['uptime']:.0%} of the fight: "
                        f"{_number(row['dps_with'])} as counted, {_number(row['dps_without'])} were it never up "
                        f"({row['dps_share']:.0%} of the headline). Confidence: {row['confidence']}."),
                "how": f"In play: {row['play']}.",
            })
            continue
        items.append({
            "kind": "how to play it *", "title": f"{row['label']} *",
            "why": (f"The headline DPS counts it: {_number(row['dps_with'])} with it, {_number(row['dps_without'])} without "
                    if counted else f"The headline DPS doesn't count it: {_number(row['dps_without'])} without it, "
                    f"{_number(row['dps_with'])} with it ") + f"({row['change']:+.0%}). Confidence: {row['confidence']}.",
            "how": f"In play: {row['play']}. If you can't, expect the 'without' figure.",
        })
    for b in result.get("blind_spots") or []:
        where = b.get("source", "")
        if not b.get("corrected_by"):
            items.append({
                "kind": "not modeled", "title": b["line"], "where": where,
                "why": (b["reason"] if b.get("reason") else "PoB ignores this line")
                       + ", so no number in this result includes it.",
                "how": "Compare your character sheet and the main skill's tooltip with and without it. A bonus "
                       "is extra on top of the result; a drawback makes the build weaker than shown.",
            })
        elif b.get("as"):
            items.append({
                "kind": "corrected *", "title": b["line"], "where": where,
                "why": f"PoB ignores this line; the result counts it as {'; '.join(b['as'])} "
                       f"(confidence: {b.get('confidence', 'unknown')}).",
                "how": "Check that the in-game character sheet or skill tooltip shows that effect at that size.",
            })
    for slot, gear in (result.get("gear") or {}).items():
        # (SEARCHC1; THE USER 23 Nov: never an empty rune socket) a socket no rune raised the score in holds a filler
        for filler in gear.get("filler_runes") or []:
            items.append({
                "kind": "your choice", "title": f"{slot}: {filler['name']} (socket filler)",
                "why": (f"No augment raised the build's score in this socket, so the search put in {filler['name']} for "
                        f"{filler.get('why') or 'quality of life'}; it changes no number in this result."),
                "how": "Keep it, or socket any augment you prefer (reduced curse effect, item rarity, cost efficiency, an "
                       "Emergent augment, movement speed): this socket is yours to use.",
            })
    labels = [a["label"] for a in assumptions]
    for note in result.get("notes") or []:
        # Asterisked notes that aren't the assumptions above (e.g. the clear-speed estimate)
        if note.startswith("* ") and not any(note.startswith(f"* {label}:") for label in labels):
            items.append({"kind": "estimate *", "title": note[2:].split(". ")[0], "why": note[2:],
                          "how": "Play a few maps and compare with what the result expects; treat it as approximate."})
    for check in result.get("checks") or []:
        # (SEARCHB6) a soft floor not met: the build is delivered, with this warning (constraints.soft_check)
        if check.get("warning"):
            items.append({"kind": "warning", "title": check["label"], "why": f"{check['warning']}. {check.get('note', '')}".strip(),
                          "how": "Dodge those hits in play, or trade some damage for more maximum hit (Life, Energy "
                                 "Shield, resistances, Armour against Physical) if they keep killing you."})
    for slot, gear in (result.get("gear") or {}).items():
        # (SEARCHB6; THE USER 23 Nov: "having empty mods on an item is stupid and wasteful") the affix slots no scoring
        # affix wanted, filled with utility (Gear.fill_utility); the damage doesn't depend on which roll they get
        if isinstance(gear, dict) and gear.get("utility_affixes"):
            items.append({
                "kind": "utility affixes", "title": f"{slot}: {gear.get('name', '')}",
                "why": "These open affix slots are filled with utility, not damage: " + "; ".join(gear["utility_affixes"])
                       + ". Movement speed first, then Life / Energy Shield / defences, then resistances, then quality of "
                         "life, each only where it doesn't lower the build's score.",
                "how": "When buying or crafting, the item's damage affixes matter; for these slots any roll of the same "
                       "kind will do (movement speed counts toward the request's movement speed floor).",
            })
    for reason in result.get("reasons") or []:
        if "isn't modeled" in reason or "not modeled" in reason:
            items.append({"kind": "not modeled", "title": "Main skill damage", "why": reason,
                          "how": "Test the skill's damage in game before buying anything for it."})
    # (PREVIEW1) 1.0 preview gems the build uses (asterisked) and the '1.0 changes pending' notes (builder/preview)
    from builder import preview

    items += preview.checklist_items(result)
    return items
