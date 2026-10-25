"""Unique variants for jewels that change the passive tree (PLAN.md "Radius jewels").

A unique's text lists its variants ("Variant: Medium Ring", ...) and up to six selections
("Selected Variant", "Selected Alt Variant", ... "Five", each alternate one switched on by its
"Has Alt Variant ..." line); a line tagged {variant:3,28} counts when any selection is 3 or 28.
Which jewels change the tree comes from PoB's own parse of them (bridge `jewelTraits`), not from a
list of names: a radius, nodes it lets the tree take without a connection, a granted passive
("Allocates ..."), or conquered nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SLOTS = ("Selected Variant", "Selected Alt Variant", "Selected Alt Variant Two", "Selected Alt Variant Three",
         "Selected Alt Variant Four", "Selected Alt Variant Five")
SWITCHES = {slot: slot.replace("Selected", "Has") for slot in SLOTS[1:]}
TAG = re.compile(r"\{variant:([\d,]+)\}")


@dataclass(frozen=True)
class Variants:
    names: tuple[str, ...]  # variant 1 is names[0]
    slots: tuple[str, ...]  # the selections this unique uses
    selected: tuple[int, ...]  # current choice per slot (1-based)
    usable: tuple[int, ...]  # variants a real item can have (not PoB's test variants)

    def label(self, choice: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(self.names[i - 1] for i in choice if 0 < i <= len(self.names))


def variants_of(raw: str) -> Variants | None:
    """The variants and selections in a unique's text, or None when it has fewer than two variants."""
    lines = raw.split("\n")
    names = tuple(line[len("Variant: "):].strip() for line in lines if line.startswith("Variant: "))
    if len(names) < 2:
        return None
    present = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in lines if ":" in line
               and not line.startswith("{")}
    slots = [SLOTS[0]] + [slot for slot in SLOTS[1:] if present.get(SWITCHES[slot], "").lower() == "true"]
    selected = []
    for index, slot in enumerate(slots):
        value = present.get(slot)
        # PoB defaults an unset selection to the last variant
        selected.append(int(value) if value and value.isdigit() else len(names) - index)
    # PoB adds convenience variants that switch on every line (From Nothing's "Everything (QoL Test
    # Variant)"): a variant with far more lines than the others isn't an item that drops
    counts = [0] * (len(names) + 1)
    for line in lines:
        if match := TAG.search(line):
            for index in {int(i) for i in match.group(1).split(",")}:
                if 0 < index <= len(names):
                    counts[index] += 1
    typical = sorted(counts[1:])[len(names) // 2]
    usable = tuple(i for i in range(1, len(names) + 1)
                   if counts[i] <= max(2, 2 * typical) and "test variant" not in names[i - 1].lower())
    return Variants(names, tuple(slots), tuple(selected), usable)


def with_variants(raw: str, variants: Variants, choice: tuple[int, ...]) -> str:
    """The unique's text with these selections (one per slot, in `variants.slots` order)."""
    lines = [line for line in raw.split("\n") if not any(line.startswith(slot + ":") for slot in SLOTS)]
    # Selections go right after the variant list, where PoB writes them
    at = max((i for i, line in enumerate(lines) if line.startswith("Variant: ") or line.startswith("Has Alt Variant")),
             default=1) + 1
    picked = [f"{slot}: {index}" for slot, index in zip(variants.slots, choice)]
    return "\n".join(lines[:at] + picked + lines[at:])


def compact(raw: str, choice: tuple[int, ...]) -> str:
    """The same item with only the chosen variants' lines and no variant list. PoB scores it the
    same and parses it far faster when a unique has hundreds of variants (Megalomaniac's 874
    "Allocates" implicits); "Implicits: N" is recounted."""
    chosen = set(choice)
    out: list[str] = []
    implicits_at, implicits_left, kept = None, 0, 0
    for line in raw.split("\n"):
        if line.startswith("Variant: ") or line.startswith("Has Alt Variant") or any(line.startswith(s + ":") for s in SLOTS):
            continue
        if line.startswith("Implicits:"):
            implicits_at, implicits_left = len(out), int(line.split(":", 1)[1])
            out.append(line)
            continue
        implicit = implicits_left > 0
        implicits_left -= 1 if implicit else 0
        if match := TAG.search(line):
            if not chosen & {int(i) for i in match.group(1).split(",")}:
                continue
            line = line[:match.start()] + line[match.end():]
        out.append(line)
        kept += 1 if implicit else 0
    if implicits_at is not None:
        out[implicits_at] = f"Implicits: {kept}"
    return "\n".join(out)


def tree_changing(traits: dict) -> bool:
    """Whether PoB's parse says this jewel acts on the tree."""
    return bool(traits.get("radius") or traits.get("grants") or traits.get("unconnected")
                or traits.get("keystones") or traits.get("classStart"))


def unlocks(traits: dict) -> bool:
    """Whether it lets the tree take nodes without a connection (or from another class start)."""
    return bool(traits.get("unconnected") or traits.get("keystones") or traits.get("classStart"))


def miscalculated(traits: dict) -> str | None:
    """Why PoB's numbers for this jewel can't be trusted, or None."""
    if traits.get("conquers") and not traits.get("seedData"):
        return ("PoB-PoE2 has no timeless seed tables: conquered notables keep their own stats, so a "
                "timeless jewel's value isn't calculated")
    return None
