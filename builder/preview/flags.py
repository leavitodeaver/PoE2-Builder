"""'1.0 changes pending' flags (PREVIEW1): what 1.0 announces it will change in the current game. A flag is a NOTE on a
result, never a number change (the brief: "never change current-game numbers"); the 0.5.5 numbers stay until the
release-day swap (PLAN.md). Sources: RESEARCH53 R53-1 (research/poe2-1-0-preview-r53.md), sections A and H.

Shown on a result whatever the preview switch says: a build planned on 0.5.5 numbers should know which of its parts
1.0 is announced to change.
"""

from __future__ import annotations

FLAGS: list[dict] = [
    {
        "key": "tree",
        "what": "the passive tree",
        "note": "1.0 overhauls the passive tree ('hundreds of new stats'); critical hit chance is reported to leave the "
                "ordinary tree (crit multiplier stays)",
        "applies": "always",
        "confidence": "HIGH for the overhaul (GGG); LIKELY for crit (third parties, scope UNVERIFIED)",
        "sources": ["GGG press release 7 Nov 2026 (poe2db Version_1.0.0)", "ExpCarry 16 Nov, Elyxir 8 Nov, aoeah 10 Nov"],
    },
    {
        "key": "jewel_sockets",
        "what": "jewel sockets",
        "note": "1.0 moves every jewel socket into the passive clusters (~60 sockets, each behind a Notable cluster): "
                "today's socket positions and their path costs change",
        "applies": "jewels",
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
        "applies": "always",
        "confidence": "HIGH for the rebalance (GGG); MEDIUM for the named examples (keynote, third parties)",
        "sources": ["GGG's official overview (quoted by kami-labs)", "Maxroll, Output Lag 6 Nov, ExpCarry 19 Nov"],
    },
    {
        "key": "jewel_craft",
        "what": "the 4-mod + increased-effect jewel craft",
        "note": "no 1.0 statement found on the Liquid Emotion jewel craft; third parties say jewels give 'less "
                "excessive critical power' and the 5-mod case is disputed: re-check at the patch notes (~4 Dec)",
        "applies": "jewels",
        "confidence": "UNVERIFIED (checked: GGG press release, poe2db league page, Maxroll, Output Lag, Elyxir, "
                      "ExpCarry, poebuilds)",
        "sources": ["RESEARCH53 R53-1 H 'Jewels'"],
    },
    {
        "key": "balance",
        "what": "game balance",
        "note": "GGG: 'game balance is the biggest change' from 0.5; every 0.5.5 number is provisional for 1.0",
        "applies": "always",
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


def notes(ascendancy: str | None = None, uses_jewels: bool = False) -> list[str]:
    """Every '1.0 changes pending' note for a result: the always-on ones, jewels' when it socketed any, and its
    ascendancy's rework."""
    out = [f"1.0 changes pending ({f['what']}): {f['note']} [{f['confidence']}]" for f in FLAGS
           if f["applies"] == "always" or (f["applies"] == "jewels" and uses_jewels)]
    asc = for_ascendancy(ascendancy)
    if asc:
        out.append(asc)
    return out
