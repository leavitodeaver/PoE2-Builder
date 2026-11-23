"""Game rules PoB doesn't enforce, or enforces more loosely than the game: which weapon classes are
in the game, what each main hand may hold in the off hand, and which bases only uniques use. The
data is builder/data/game_rules.yaml, one table per patch, with sources and confidence per rule;
skeletons, gear slots and rares all go through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

RULES_FILE = Path(__file__).with_name("game_rules.yaml")
OFF_HAND_ITEMS = ("Shield", "Focus", "Quiver")  # off-hand item classes that aren't weapons


@dataclass(frozen=True)
class GameRules:
    patch: str
    released: frozenset[str]
    unreleased: frozenset[str]
    off_hand: dict[str, tuple[str, ...]]
    unique_only_tags: frozenset[str]
    floor_blockers: dict[str, tuple[str, ...]]  # item line -> floors it makes unreachable
    passive_exceptions: tuple[dict, ...] = ()  # off-hand pairs a passive allows (off_hand.passive_exceptions)

    def weapon_in_game(self, item_class: str | None) -> bool:
        """False for weapon classes whose bases are in PoB's data but don't drop yet."""
        return item_class not in self.unreleased

    def off_hands(self, main_hand: str | None) -> tuple[str, ...]:
        """What the off hand may hold beside this main hand (none beside two-handers or bare hands)."""
        return self.off_hand.get(main_hand or "", ())

    def off_hand_weapons(self, main_hand: str | None) -> tuple[str, ...]:
        """The weapon classes among them: a sceptre, or the same martial class (dual wielding)."""
        return tuple(t for t in self.off_hands(main_hand) if t not in OFF_HAND_ITEMS)

    def blocked_floors(self, raw: str) -> set[str]:
        """Floors ("energy_shield", "resists.fire", ...) an item with this text can never meet."""
        return {floor for line, floors in self.floor_blockers.items() if line in raw for floor in floors}

    def normal_base_ok(self, base: dict) -> bool:
        """Whether a base can be found or crafted as a normal, magic or rare item: not a base only
        uniques use, and not a weapon class that isn't in the game."""
        return not (set(base.get("tags") or []) & self.unique_only_tags) and self.weapon_in_game(base.get("type"))


# (PREVIEW1) The 1.0 preview switch (builder/preview): while a request with preview_1_0 is in force (Context sets it), the
# weapon classes 1.0 releases count as in the game. Off by default; a request without the switch puts it back.
_preview = False


def set_preview(on: bool) -> None:
    global _preview
    _preview = bool(on)


def rules(path: Path = RULES_FILE) -> GameRules:
    """The patch's rules; with the 1.0 preview switch on, the 1.0 weapon classes (builder/preview) are released."""
    return _preview_rules(path) if _preview else _load(path)


@cache
def _preview_rules(path: Path) -> GameRules:
    from dataclasses import replace

    from builder.preview import PREVIEW_OFF_HANDS, PREVIEW_WEAPON_CLASSES

    base = _load(path)
    off_hand = dict(base.off_hand)
    for main, offs in PREVIEW_OFF_HANDS.items():
        off_hand.setdefault(main, offs)
    return replace(base, patch=f"{base.patch} + 1.0 preview", released=base.released | PREVIEW_WEAPON_CLASSES,
                   unreleased=base.unreleased - PREVIEW_WEAPON_CLASSES, off_hand=off_hand)


@cache
def _load(path: Path = RULES_FILE) -> GameRules:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = data["weapon_classes"]
    return GameRules(
        patch=str(data["patch"]),
        released=frozenset(classes["released"]),
        unreleased=frozenset(classes["unreleased"]),
        off_hand={main: tuple(off) for main, off in data["off_hand"]["by_main_hand"].items()},
        unique_only_tags=frozenset(data["unique_only_bases"]["tags"]),
        floor_blockers={line: tuple(floors) for line, floors in data["floor_blockers"]["lines"].items()},
        passive_exceptions=tuple(e for e in data["off_hand"].get("passive_exceptions") or () if e.get("enabled", True)),
    )
