"""The Loreweave the search offers (SEARCH24; the manager, 15 Nov 23:30, from RESEARCH27: research/loreweave-grasping-mail.md,
INDEX R27-1).

Loreweave (a unique Ornate Ringmail made from 60 unique rings at Dannig) has seven fixed lines and "Random 3 Unique Ring
Modifiers". PoB generates it (Data/Uniques/Special/Generated.lua 257-303) with only the BigRange base lines, the wide rolls a
Ventor's Gamble among the input rings switches on, and a FIXED default trio of ring effects (variants 22 / 44 / 66:
Cracklecreep's Ignite spread, Kulemak's Presence cast speed, Ming's Heart's 0-25% of damage as extra Chaos). At the middle
roll that reads maximum resistances 66%, cast speed 0%. poe.ninja keeps one Loreweave line (0.47 divine, 9,240 listings on
15 Nov), the median of random-effect items; good effects sell for 80-5,000 divine (seller blogs). So the default trio was
bought far under its worth (an OVERCOUNT of value per divine) and the Ventor's base lines understated its defence.

What the search offers instead, at that generic price: the plain Loreweave, the seven base lines at their NORMAL ranges
(game Mods, build 26650308; poe2db agrees), and no ring effect (random on a generic item: counted as 0, the cautious
reading). A Loreweave with a chosen effect needs a trade price for that effect first (no price, no offer): not offered yet.
A Loreweave in a player's own imported build keeps its own lines (this only rewrites what the search offers).
"""

from __future__ import annotations

import re

NAME = "Loreweave"

# The seven fixed lines at their normal ranges (game Mods 26650308 Stat1Value / Stat2Value; the BigRange rows in brackets
# are what PoB's generated text carries), keyed by a pattern that finds PoB's BigRange line.
NORMAL_LINES = (
    (r"Physical Damage to Attacks$", "Adds (1-4) to (8-12) Physical Damage to Attacks"),  # UniqueAddedPhysicalDamage1 [(0-5) to (6-18)]
    (r"to Accuracy Rating$", "+(200-300) to Accuracy Rating"),  # UniqueIncreasedAccuracy1 [-200 to 400]
    (r"to maximum Mana$", "+(20-30) to maximum Mana"),  # UniqueIncreasedMana2 [-10 to 40]
    (r"Rarity of Items found$", "(6-15)% increased Rarity of Items found"),  # UniqueItemFoundRarityIncrease6 [-20 to 20]
    (r"to Quality of all Skills$", "+(2-5)% to Quality of all Skills"),  # UniqueAdditionalGemQuality1 [0 to 7]
    (r"Cast Speed$", "(7-10)% increased Cast Speed"),  # UniqueIncreasedCastSpeed2 [-15 to 15]
    (r"^Your Maximum Resistances are", "Your Maximum Resistances are (75-80)%"),  # UniqueMaximumResistancesOverride1 [50 to 82]
)
TAGS = re.compile(r"^((?:\{[^}]*\})*)(.*)$")


def plain(raw: str) -> str:
    """PoB's Loreweave text as the plain item the generic price buys: the base lines at their normal ranges (each keeps
    its {range:...} tag, so it reads at the middle roll as before), every ring-effect line ({variant:N}) left out, and
    the variant selectors with them. Any other text is unchanged."""
    out = []
    for line in raw.split("\n"):
        tags, text = TAGS.match(line).groups()
        if "{variant:" in tags or text.startswith(("Variant: ", "Selected Variant:", "Has Alt Variant",
                                                    "Selected Alt Variant")):
            continue  # (a ring effect, or the selector of one)
        for pattern, normal in NORMAL_LINES:
            if re.search(pattern, text) and "{base:" not in tags:
                line = tags + normal
                break
        out.append(line)
    return "\n".join(out)


def offered(name: str, raw: str) -> str:
    """The text the search offers for this unique: the plain Loreweave for Loreweave, any other unique as it is."""
    return plain(raw) if name == NAME else raw
