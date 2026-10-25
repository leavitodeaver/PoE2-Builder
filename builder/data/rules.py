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

    def weapon_in_game(self, item_class: str | None) -> bool:
        """False for weapon classes whose bases are in PoB's data but don't drop yet."""
        return item_class not in self.unreleased

    def off_hands(self, main_hand: str | None) -> tuple[str, ...]:
        """What the off hand may hold beside this main hand (none beside two-handers or bare hands)."""
        return self.off_hand.get(main_hand or "", ())

    def off_hand_weapons(self, main_hand: str | None) -> tuple[str, ...]:
        """The weapon classes among them: a sceptre, or the same martial class (dual wielding)."""
        return tuple(t for t in self.off_hands(main_hand) if t not in OFF_HAND_ITEMS)

    def normal_base_ok(self, base: dict) -> bool:
        """Whether a base can be found or crafted as a normal, magic or rare item: not a base only
        uniques use, and not a weapon class that isn't in the game."""
        return not (set(base.get("tags") or []) & self.unique_only_tags) and self.weapon_in_game(base.get("type"))


@cache
def rules(path: Path = RULES_FILE) -> GameRules:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = data["weapon_classes"]
    return GameRules(
        patch=str(data["patch"]),
        released=frozenset(classes["released"]),
        unreleased=frozenset(classes["unreleased"]),
        off_hand={main: tuple(off) for main, off in data["off_hand"]["by_main_hand"].items()},
        unique_only_tags=frozenset(data["unique_only_bases"]["tags"]),
    )
