"""Items the user already owns (the user, 22 Nov: "when you select some piece of gear in the utility as a prerequisite
for the build, there needs to be an option to not include it in the budget, if you already have an expensive weapon or
something and want to build around it").

An owned item is pasted as the game copies it (Ctrl+C on the item) or as PoB exports it. It is read here against PoB's
item data (its rarity, name and base, and from the base its class and the slots it can go in); the search equips the
text as it is, locks the slot and counts no price for it (gear.Gear.setup). PoB parses the rest of the text when it is
equipped: a line it can't read is reported by the search, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from builder.data import GameData

ONE_SLOT = {"Helmet": "Helmet", "Body Armour": "Body Armour", "Gloves": "Gloves", "Boots": "Boots", "Amulet": "Amulet",
            "Belt": "Belt"}
OFF_HAND_ONLY = {"Shield", "Focus", "Quiver"}
RING_SLOTS = ["Ring 1", "Ring 2", "Ring 3"]
SLOTS = ["Weapon 1", "Weapon 2", *ONE_SLOT, *RING_SLOTS]
COPY_HINT = "paste the item as the game copies it (Ctrl+C over the item) or as PoB exports it"


@dataclass
class OwnedRead:
    text: str
    rarity: str = ""  # NORMAL / MAGIC / RARE / UNIQUE
    name: str = ""
    base: str = ""
    type: str = ""  # the base's item class, e.g. "Two Hand Mace"
    sub_type: str | None = None
    level: int = 0  # the base's level requirement
    two_handed: bool = False
    slots: list[str] = field(default_factory=list)  # where it can go
    slot: str | None = None  # where it goes (the request's choice, or the only one)
    error: str | None = None

    @property
    def unique(self) -> bool:
        return self.rarity in ("UNIQUE", "RELIC")

    def describe(self) -> dict:
        return {"rarity": self.rarity, "name": self.name, "base": self.base, "type": self.type, "slots": self.slots,
                "slot": self.slot, "error": self.error}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]


def _base_in(line: str, bases: dict[str, dict]) -> dict | None:
    """The base a name line holds: the whole line, else the longest base name inside it (a magic item's
    "Prefix Base of Suffix", or "Superior Base")."""
    if line in bases:
        return bases[line]
    found = [name for name in bases if name in line]
    return bases[max(found, key=len)] if found else None


def read_item(text: str, data: GameData, slot: str | None = None) -> OwnedRead:
    read = OwnedRead(text=text.strip())
    lines = _lines(text)
    at = next((i for i, line in enumerate(lines) if line.lower().startswith("rarity:")), None)
    if at is None:
        read.error = f"no 'Rarity:' line: {COPY_HINT}"
        return read
    read.rarity = lines[at].split(":", 1)[1].strip().upper()
    header = []
    for line in lines[at + 1:]:
        if line.startswith("---") or (":" in line and header):
            break
        header.append(line)
    if not header:
        read.error = f"no name line after 'Rarity:': {COPY_HINT}"
        return read
    bases = {b["name"]: b for b in data.bases}
    base = None
    if read.unique:
        read.name = header[0]
        unique = next((u for u in data.uniques if u["name"] == read.name), None)
        if unique is None:
            read.error = f"PoB knows no unique named '{read.name}'"
            return read
        base = (_base_in(header[1], bases) if len(header) > 1 else None) or bases.get(unique["base"])
    elif read.rarity in ("RARE", "MAGIC", "NORMAL"):
        name_line = header[1] if read.rarity == "RARE" and len(header) > 1 else header[0]
        base = _base_in(name_line, bases)
        read.name = header[0]
    else:
        read.error = f"unknown rarity '{read.rarity}'"
        return read
    if base is None:
        read.error = f"PoB knows no item base in '{' / '.join(header[:2])}'"
        return read
    read.base, read.type, read.sub_type = base["name"], base["type"], base.get("subType")
    read.level = (base.get("req") or {}).get("level") or 0
    read.two_handed = bool({"two_hand_weapon", "twohand"} & set(base.get("tags") or []))
    if read.type in ONE_SLOT:
        read.slots = [ONE_SLOT[read.type]]
    elif read.type == "Ring":
        read.slots = list(RING_SLOTS)
    elif read.type in OFF_HAND_ONLY:
        read.slots = ["Weapon 2"]
    elif read.type in data.weapon_types:
        read.slots = ["Weapon 1"] if read.two_handed else ["Weapon 1", "Weapon 2"]
    else:
        read.error = f"a {read.type} can't be an owned item yet (only weapons, off hands, armour and jewellery)"
        return read
    if slot:
        if slot not in read.slots:
            read.error = f"a {read.type} can't go in {slot} (it goes in {' or '.join(read.slots)})"
            return read
        read.slot = slot
    elif len(read.slots) == 1:
        read.slot = read.slots[0]
    else:
        read.error = f"choose its slot: {' or '.join(read.slots[:2] if read.type == 'Ring' else read.slots)}"
    return read
