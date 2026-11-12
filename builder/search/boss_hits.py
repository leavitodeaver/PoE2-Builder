"""The boss's hits you can't evade, block or dodge-roll (MECHC5; FIX_QUEUE "RESEARCH15's findings" #71, RESEARCH16 #78).

The rule (HIGH): patch 0.3.0, "Player Evasion now works on all hits except boss skills that have a red flash"; KeywordPopups
"Block": "Some Skills used by Bosses cannot be blocked. These are indicated by a red glow and audio cue during the windup".
In the game files these skills carry `base_skill_cannot_be_avoided_by_dodge_roll_or_evaded_or_blocked` (no dodge roll, no
evasion, no block) or `base_skill_cannot_be_avoided_by_dodge_roll_or_blocked` (evasion still applies), and Zarokh's
DeathTouch also has `global_always_hit`. (checked: RESEARCH15's scan of every MonsterVariety, granted effect and stat set in
each boss's folder, scratch/research15/unavoid.py, game 26476662; second source: the 0.3.0 patch note quoted on the official
forum, thread 3829081, and KeywordPopups Block; research/unavoidable-pinnacle-hits.md.)

The share of the boss's hits that are flagged: our tables have no use rates per boss skill, so the share is the flagged
skills' count among the boss's damaging skills (LOW stand-in: these are often its biggest hits, so the damage share is
probably higher). The non-boss attackers of each fight carry no flag (RESEARCH15's adds.py).

Deflection: no game text says whether a red-flash hit can be Deflected (#78, LOW-MEDIUM that it can): the recovery check
keeps Deflection against every hit, labelled LOW, and reports the figure without it (objective.boss_recovery).

MECH-A's boss presets (builder/search/bosses.py, master) may carry the shares per preset: this module reads a Boss's
`unavoidable` (share of its hits with no evade, block or dodge roll) and `unblockable` (share that can be evaded but not
blocked or dodge-rolled) when they're there, and its own table below otherwise.
"""
from __future__ import annotations



def number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0

DEFAULT_BOSS = "Arbiter of Ash"
# boss: (skills with no evade/block/dodge roll, skills with evasion but no block/dodge roll, damaging skills, which)
RED_FLASH: dict[str, tuple[int, int, int, str]] = {
    "Arbiter of Ash": (7, 0, 20, "Chasm Burst, Offscreen Slam, Big Chasm Slash, Bullet Hell (x3), Flame Seed nuke"),
    "Arbiter of Divinity": (4, 0, 22, "Storm Blink Bolt, Extendo Spear Combo Slam, Spear Toss impacts (x2)"),
    "Xesht, We That Are One": (4, 0, 11, "Multi Flick, Pistol Flick wave, Hand Slams (left, right)"),
    "The King in the Mists": (4, 0, 16, "Vine Slam, Punishment, Bug Bomb (phase 3) and its impact"),
    "Zarokh, the Temporal": (1, 0, 22, "Apparition Death Touch (also always hits)"),
    "The Raven Trickster": (2, 0, 35, "Phase Change Blast, Volatile Flame Blast (Lightning)"),
    "The Bodach": (3, 1, 20, "phase 2 Meteor slams (x3); Head Slam can still be evaded"),
    "Olroth, Origin of the Fall": (4, 0, 19, "Ground Slam and its two Aftershocks, Throw Out (Maims)"),
    "The Aberration": (1, 0, 9, "Spider Mortar"),
    "Vessel of Kulemak": (0, 2, 9, "Teleport Slam and its trigger can still be evaded"),
    "Atziri, the Red Queen": (0, 0, 25, "none in phase 1 (Flesh Bubble Explosion is phase 2)"),
    "Atziri, the Fell Serpent": (1, 0, 25, "Flesh Bubble Explosion"),
}
CONFIDENCE = ("which skills: HIGH (game files); their share of the boss's hits: LOW (flagged skills / damaging skills, no use "
              "rates in our tables)")


def boss_name(stats: dict) -> str:
    if name := stats.get("_boss"):
        return name
    try:
        from builder.search import bosses  # MECH-A's presets (master; not on every branch yet)
        return bosses.DEFAULT
    except ImportError:
        return DEFAULT_BOSS


def shares(stats: dict) -> tuple[float, float]:
    """(share of the boss's hits with no evade / block, share with evade but no block)."""
    name = boss_name(stats)
    try:
        from builder.search import bosses
        boss = bosses.PRESETS.get(name)
    except ImportError:
        boss = None
    if boss is not None and getattr(boss, "unavoidable", None) is not None:
        return float(boss.unavoidable), float(getattr(boss, "unblockable", 0.0) or 0.0)
    flagged, evadable, total, _ = RED_FLASH.get(name, RED_FLASH[DEFAULT_BOSS])
    return flagged / total, evadable / total


def evade_block(stats: dict) -> tuple[float, float]:
    return (min(max(number(stats.get("EvadeChance")), 0.0), 100) / 100,
            min(max(number(stats.get("EffectiveBlockChance")), 0.0), 100) / 100)


def land_share(stats: dict) -> float:
    """The share of the boss's hits that land: red-flash hits always do; the rest are evaded and blocked as PoB rolls them."""
    u, e = shares(stats)
    evade, block = evade_block(stats)
    return u + e * (1 - evade) + (1 - u - e) * (1 - evade) * (1 - block)


def block_share(stats: dict, evaded_first: bool = True) -> float:
    """The share of the boss's hits you Block: only the blockable ones (after evasion when `evaded_first`)."""
    u, e = shares(stats)
    evade, block = evade_block(stats)
    return (1 - u - e) * (1 - evade if evaded_first else 1.0) * block


def landing_hits(stats: dict) -> float:
    """The boss's hits per second that land (one every enemySkillTime, PoB's boss preset)."""
    return land_share(stats) / (number(stats.get("enemySkillTime")) or 0.7)


def note(stats: dict) -> dict:
    name = boss_name(stats)
    u, e = shares(stats)
    which = RED_FLASH.get(name, RED_FLASH[DEFAULT_BOSS])[3]
    return {"label": "Boss hits you can't evade or block (red flash)",
            "value": f"{u:.0%} of {name}'s hits" + (f", and {e:.0%} that can be evaded but not blocked" if e else ""),
            "basis": f"{which}: no evade or block credit against them (0.3.0 patch note; game files' skill flags). "
                     "Deflection still counted against them (LOW: no game text either way)",
            "confidence": CONFIDENCE}
