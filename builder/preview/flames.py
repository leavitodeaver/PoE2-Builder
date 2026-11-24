"""Oro's Sacrifice's Sacrificial Flame (PREVIEW3; the manager's ruling, 23 Nov 14:53, from RESEARCH55 R55-11,
research/sacrificial-flame-r55.md; every number '1.0 preview, subject to change').

Known (MEDIUM-HIGH as preview data): using Infernal Step (the skill Oro's Sacrifice grants) gains 1 Sacrificial Flame.
Not published (UNVERIFIED): the maximum, the duration, what removes Flames. poe2db shows two Oro's Sacrifice blocks that
disagree: 12% (Infernal Step level 19) or 4% (level 13) of Damage as Extra Fire Damage per Flame.

No flat stack count (CLAUDE.md): the headline counts 0 Flames, with a note; a per-Flame table shows what each Flame
would add under both blocks; "Gain 1 Frenzy Charge every 1 second while at maximum Sacrificial Flame" counts 0 until a
maximum is published.
"""

from __future__ import annotations

from typing import Any

from builder.preview import ASTERISK

UNIQUE = "Oro's Sacrifice"
PER_FLAME = (12, 4)  # % of Damage as Extra Fire per Flame: poe2db's two blocks
TABLE_FLAMES = (1, 2, 3, 4, 5)
LABEL = f"Oro's Sacrifice: Sacrificial Flame{ASTERISK}"


def flame_table(flames=TABLE_FLAMES, per_flame=PER_FLAME) -> list[tuple[int, tuple[int, ...]]]:
    """(Flames, % of Damage as Extra Fire under each block) for each Flame count."""
    return [(n, tuple(n * p for p in per_flame)) for n in flames]


def flame_note(build: Any) -> list[dict]:
    """The labelled note when Oro's Sacrifice is equipped (it counts 0 in the headline)."""
    equipped = getattr(build, "equipped", None) or {}
    if not any(getattr(o, "name", None) == UNIQUE for o in equipped.values() if o is not None):
        return []
    table = "; ".join(f"{n} Flame{'s' if n > 1 else ''}: {a}% or {b}%" for n, (a, b) in flame_table())
    return [{"label": LABEL, "value": 0, "confidence": "LOW",
             "basis": (f"Counted 0 Flames: each use of Infernal Step gains 1, but the maximum, duration and removal are "
                       f"unpublished (UNVERIFIED), so no stack count is assumed. Damage as Extra Fire per Flame count, "
                       f"under poe2db's two blocks (12% or 4% per Flame; which is current is unmarked): {table}. "
                       f"The Frenzy Charge 'while at maximum Sacrificial Flame' counts 0 until a maximum is published. "
                       f"Infernal Step itself has no data yet{ASTERISK}"),
             "play": f"Use Infernal Step to gain Sacrificial Flames; how many you can hold is unpublished{ASTERISK}"}]
