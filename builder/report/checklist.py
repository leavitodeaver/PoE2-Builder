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
            "how": "Watch it in game (the skill's tooltip, or count it over a minute of play). If it's lower than "
                   "assumed, the damage marked * is lower in proportion. The PoB code doesn't carry this assumption.",
        })
    for b in result.get("blind_spots") or []:
        where = b.get("source", "")
        if not b.get("corrected_by"):
            items.append({
                "kind": "not modeled", "title": b["line"], "where": where,
                "why": "PoB ignores this line, so no number in this result includes it.",
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
    labels = [a["label"] for a in assumptions]
    for note in result.get("notes") or []:
        # Asterisked notes that aren't the assumptions above (e.g. the clear-speed estimate)
        if note.startswith("* ") and not any(note.startswith(f"* {label}:") for label in labels):
            items.append({"kind": "estimate *", "title": note[2:].split(". ")[0], "why": note[2:],
                          "how": "Play a few maps and compare with what the result expects; treat it as approximate."})
    for reason in result.get("reasons") or []:
        if "isn't modeled" in reason or "not modeled" in reason:
            items.append({"kind": "not modeled", "title": "Main skill damage", "why": reason,
                          "how": "Test the skill's damage in game before buying anything for it."})
    return items
