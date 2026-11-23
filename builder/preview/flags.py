"""'1.0 changes pending' flags (PREVIEW1): what 1.0 announces it will change in the current game. A flag is a NOTE on a
result, never a number change (the brief: "never change current-game numbers"); the 0.5.5 numbers stay until the
release-day swap (PLAN.md). Sources: RESEARCH53 R53-1 (research/poe2-1-0-preview-r53.md), sections A and H.

The manager's ruling (23 Nov 11:18): these are notes about CURRENT-game models, so they show with the preview switch on
or off, but only WHERE THE CHANGE TOUCHES THE BUILD (each flag's "applies"; notes() below). The general balance note
names no part of a build, so it shows only with the switch on.
"""

from __future__ import annotations

import re

FLAGS: list[dict] = [
    {
        "key": "tree",
        "what": "the passive tree",
        "note": "1.0 overhauls the passive tree ('hundreds of new stats'); critical hit chance is reported to leave the "
                "ordinary tree (crit multiplier stays)",
        "applies": "crit_passives",
        "confidence": "HIGH for the overhaul (GGG); LIKELY for crit (third parties, scope UNVERIFIED)",
        "sources": ["GGG press release 7 Nov 2026 (poe2db Version_1.0.0)", "ExpCarry 16 Nov, Elyxir 8 Nov, aoeah 10 Nov"],
    },
    {
        "key": "jewel_sockets",
        "what": "jewel sockets",
        "note": "1.0 moves every jewel socket into the passive clusters (~60 sockets, each behind a Notable cluster): "
                "today's socket positions and their path costs change",
        "applies": "tree_jewels",
        "confidence": "HIGH that they move (GGG); MEDIUM for ~60 (Maxroll, read off the keynote)",
        "sources": ["GGG press release 7 Nov 2026: 'We've also moved all Jewel Sockets into the clusters'",
                    "Maxroll 7 Nov 2026"],
    },
    {
        "key": "supports",
        "what": "support gems",
        "note": "1.0 'massively rebalanced Support Gems': every support's numbers may move (named: Fire / Cold / "
                "Lightning Penetration merged into Elemental Penetration; Chain no longer penalises the first hit; "
                "Brutality reworked; Living Lightning deals Attack or Spell damage)",
        "applies": "named_supports",
        "confidence": "HIGH for the rebalance (GGG); MEDIUM for the named examples (keynote, third parties)",
        "sources": ["GGG's official overview (quoted by kami-labs)", "Maxroll, Output Lag 6 Nov, ExpCarry 19 Nov"],
    },
    {
        "key": "jewel_craft",
        "what": "the 4-mod + increased-effect jewel craft",
        "note": "no 1.0 statement found on the Liquid Emotion jewel craft; third parties say jewels give 'less "
                "excessive critical power' and the 5-mod case is disputed: re-check at the patch notes (~4 Dec)",
        "applies": "crafted_jewels",
        "confidence": "UNVERIFIED (checked: GGG press release, poe2db league page, Maxroll, Output Lag, Elyxir, "
                      "ExpCarry, poebuilds)",
        "sources": ["RESEARCH53 R53-1 H 'Jewels'"],
    },
    {
        "key": "balance",
        "what": "game balance",
        "note": "GGG: 'game balance is the biggest change' from 0.5; every 0.5.5 number is provisional for 1.0",
        "applies": "preview_on",
        "confidence": "HIGH (Rogers, Inven Global 18 Nov 2026, verbatim)",
        "sources": ["Inven Global 18 Nov 2026"],
    },
]

# Ascendancies 1.0 reworks (GGG headline; details third-party). No change announced for the others (checked: GGG press
# release, Maxroll, Output Lag, ExpCarry; the patch notes aren't out, so silent changes are likely: UNVERIFIED that the
# others stay as they are).
REWORKED_ASCENDANCIES: dict[str, str] = {
    "Mercenary": "crossbow reloading reworked (ammo reloads in the background, third party)",
    "Infernalist": "Demon Magus (a demon minion that casts your spells), Infernal Hounds in threes, Covenant skills",
    "Blood Mage": "new skills and curses; Corrupted Blood reworked (double damage taken, consumed; third party)",
    "Acolyte of Chayula": "a companion clone using your Quarterstaff skills, Decay on hits, Dreamer's Path (third "
                          "party); Chayula's Darkness reported to become a keystone",
}


def for_ascendancy(name: str | None) -> str | None:
    """The '1.0 changes pending' note for a reworked ascendancy, or None."""
    change = REWORKED_ASCENDANCIES.get(name or "")
    if change is None:
        return None
    return (f"1.0 changes pending: GGG announces a rework of the {name} ({change}); this build uses the 0.5.5 nodes "
            "(GGG press release 7 Nov 2026; details third-party, MEDIUM / LOW)")


# The supports 1.0's reveal names (RESEARCH53 R53-1 H: keynote examples, third parties, MEDIUM), matched without their
# tier numeral. Every support's numbers may move, but these are the ones with a named change to point a build at.
NAMED_SUPPORTS = frozenset({"Fire Penetration", "Cold Penetration", "Lightning Penetration", "Chain", "Brutality",
                            "Living Lightning"})
_TIER = re.compile(r"\s+(?:I{1,3}|IV|V)$")
# A passive line about crits landing ON the player is defence, not the crit 1.0 is reported to move
_CRIT = re.compile(r"critical", re.I)
_CRIT_ON_YOU = re.compile(r"against you|you take|taken from", re.I)


def named_supports(gem_names) -> list[str]:
    """The build's gems (by name) that are supports 1.0's reveal names."""
    return sorted(n for n in set(gem_names) if n and _TIER.sub("", n) in NAMED_SUPPORTS)


def is_crit_passive(node: dict) -> bool:
    """Whether an ordinary (non-ascendancy) passive has an offensive critical-hit line."""
    if node.get("ascendancy"):
        return False
    return any(_CRIT.search(s) and not _CRIT_ON_YOU.search(s) for s in node.get("stats") or [])


def notes(ascendancy: str | None = None, tree_jewels: bool = False, crafted_jewels: bool = False,
          crit_passives: int = 0, supports: tuple | list = (), preview_on: bool = False) -> list[str]:
    """The '1.0 changes pending' notes that touch this build (the manager's ruling, 23 Nov 11:18)."""
    touches = {"tree_jewels": tree_jewels, "crafted_jewels": crafted_jewels, "crit_passives": crit_passives > 0,
               "named_supports": bool(supports), "preview_on": preview_on}
    these = {"crit_passives": f" (this build: {crit_passives} passive(s) with critical-hit lines)",
             "named_supports": f" (this build: {', '.join(supports)})"}
    out = [f"1.0 changes pending ({f['what']}): {f['note']}{these.get(f['applies'], '')} [{f['confidence']}]"
           for f in FLAGS if touches[f["applies"]]]
    asc = for_ascendancy(ascendancy)
    if asc:
        out.append(asc)
    return out
