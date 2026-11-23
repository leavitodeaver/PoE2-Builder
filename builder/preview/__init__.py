"""PoE2 1.0 PREVIEW content (the user, 23 Nov 2026: include announced 1.0 content, "with asterisks that it's subject to
change pending 1.0 release"; OFF BY DEFAULT, the request's preview_1_0 switch turns it on).

Everything here is data from GGG's 1.0 reveals as poe2db shows them (RESEARCH53 R53-1), each row with its source, date
and confidence. Nothing reaches a search unless the request sets preview_1_0:
- gems.json: 1.0 skill and support gems; lua.py writes them into PoB (install) so PoB scores them with the build's
  supports, gear and passives; Context.gems / all_gems offer them (context.py's hook).
- weapon classes 1.0 releases (PREVIEW_WEAPON_CLASSES): builder/data/rules.py's preview switch, set by Context.
- flags.py: "1.0 changes pending" notes on what 1.0 announces it will change (a note, never a number).
The release-day plan (replace all of it with re-exported game files and PoB's update) is PLAN.md.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
ASTERISK = "*1.0 preview, subject to change"
# Weapon classes 1.0 brings (RESEARCH53 R53-1 A / G, GGG's 7 Nov press release: the Shadow's daggers and the Duelist's
# swords; PoB already holds their bases, builder/data/game_rules.yaml keeps them 'unreleased' for 0.5.5). Traps (a new
# two-handed weapon) wait for their skills: PoB has the TrapTool bases but no weapon type for them.
PREVIEW_WEAPON_CLASSES = frozenset({"Dagger", "One Hand Sword", "Two Hand Sword"})
# Off hands beside them: taken as One Hand Mace's today (shield, focus, sceptre, the same class dual wielded), LOW: 1.0's
# rules for daggers and swords are unpublished (Duplicity's "a dagger with a parrying shield, or dual daggers" agrees
# with shield and dual wielding; checked: RESEARCH53 R53-1 C; focus and sceptre UNVERIFIED)
PREVIEW_OFF_HANDS = {
    "Dagger": ("Shield", "Focus", "Sceptre", "Dagger"),
    "One Hand Sword": ("Shield", "Focus", "Sceptre", "One Hand Sword"),
}


# Announced 1.0 skills that can't be scored yet, and why (a request naming one is told; none is scored as 0 or guessed).
# Traps: "Attack Damage N%" needs a damage value to scale. Checked: the game's WeaponTypes table has no row for any of the
# 13 trap bases (build 26650308); PoB's trap bases carry no weapon data; poe2db's Traps, Refined Trap, Fire Trap and Null
# Trap pages (read 23 Nov 2026) show no damage range and don't say what the percentage scales. UNVERIFIED until the 1.0
# files or patch notes say.
TRAP_SKILLS = frozenset({"Freezing Trap", "Concussive Trap", "Fire Trap", "Poison Trap", "Electrocution Trap",
                         "Null Trap", "Cluster Trap", "Shrapnel Trap", "Detonate"})
TRAP_HOLD = ("It is announced 1.0 content, but can't be scored yet: 1.0 hasn't published the damage its trap weapons' "
             "skills scale from.")


def on(request: Any) -> bool:
    """Whether a request (BuildRequest, or anything with preview_1_0) asks for 1.0 preview content."""
    return bool(getattr(request, "preview_1_0", False))


@cache
def _gems_file() -> dict:
    return json.loads((HERE / "gems.json").read_text(encoding="utf-8"))


def gem_rows() -> list[dict]:
    """Every preview gem row as gems.json holds it (model, sources, not_modelled ...)."""
    return list(_gems_file()["gems"])


GEM_FIELDS = ("name", "grantedEffectId", "id", "gemType", "support", "tags", "tagString", "skillTypes", "weaponTypes",
              "reqStr", "reqDex", "reqInt", "tier", "description", "families")


def gems() -> list[dict]:
    """The preview gems in the shape of PoB's gem dump (data/pob-*/gems.json), each marked preview: True, for
    Context's gem lists."""
    out = []
    for row in gem_rows():
        gem = {k: row[k] for k in GEM_FIELDS if k in row}
        gem.update(baseTypeName=row["name"], levelRequirement=0, naturalMaxLevel=1 if row["support"] else 20,
                   preview=True)
        out.append(gem)
    return out


def names() -> frozenset[str]:
    return frozenset(row["name"] for row in gem_rows())


def note(name: str) -> str | None:
    """The asterisked note for a preview gem's name (None for any other name): what the result row shows."""
    row = next((r for r in gem_rows() if r["name"] == name), None)
    if row is None:
        return None
    left = "; ".join(row.get("not_modelled") or []) or "nothing"
    return (f"{name}{ASTERISK}: modelled from GGG's 1.0 reveal ({row.get('confidence')}); not counted yet: {left}")


def install(engine: Any, request: Any) -> bool:
    """Write the preview gems into this engine's PoB (once per engine; a Lua guard makes a repeat a no-op) when the
    request asks for preview content. Returns whether it ran. Through engine.lua.run, so a replayed engine records it
    like any call."""
    if not on(request):
        return False
    from builder.preview.lua import installer

    engine.lua.run(installer(), "preview_1_0")
    return True


def on_in(result: dict) -> bool:
    """Whether a result's request had the 1.0 preview switch on."""
    return bool((result.get("request") or {}).get("preview_1_0"))


def _bases(row: dict) -> dict:
    """The item bases by name, loaded only for a gem whose reading needs its weapon's attack time."""
    if not (row.get("model") or {}).get("added_attack_time_ms"):
        return {}
    from builder.data import GameData

    return {b["name"]: b for b in GameData().bases}


def checklist_items(result: dict) -> list[dict]:
    """The test-in-game checklist's rows for a result (builder/report/checklist.py): one '1.0 preview *' row for each
    preview gem the build uses (its asterisk and what is left out), and the '1.0 changes pending' notes that touch
    this build, with the switch on or off (flags.notes; the manager's ruling, 23 Nov 11:18)."""
    from builder.preview import flags, readings

    items: list[dict] = []
    used = {gem.get("name") for group in result.get("groups") or [] for gem in group.get("gems") or []}
    for name in sorted(used & names()):
        row = next(r for r in gem_rows() if r["name"] == name)
        why = " ".join([note(name), *(f"{text}{ASTERISK}." for text in readings.rows(row, result, _bases(row)))])
        items.append({"kind": "1.0 preview *", "title": f"{name}{ASTERISK}", "why": why,
                      "how": "Not in the game until 1.0 (11 Dec 2026). Every number this skill touches comes from "
                             "GGG's reveal at one gem level and may change; re-check after the release-day update."})
    gear = result.get("gear") or {}
    gear = gear if isinstance(gear, dict) else {}
    rows = {row["name"]: row for row in unique_rows()}
    if on_in(result):  # (a changed unique's name is also 0.5.5's: marked only when the switch put the 1.0 text in)
        for slot, item in sorted(gear.items()):
            row = rows.get((item or {}).get("name"))
            if row is not None and (item or {}).get("unique"):
                items.append({"kind": "1.0 preview *", "title": f"{row['name']} ({slot}){ASTERISK}",
                              "why": unique_note(row),
                              "how": "Not in the game in this form until 1.0 (11 Dec 2026); its lines come from GGG's "
                                     "reveal and may change. Re-check after the release-day update."})
    summary = result.get("summary") or {}
    jewels = [item or {} for slot, item in gear.items() if "Jewel" in slot]
    crafted = any(j.get("rare") and not j.get("unique") for j in jewels)
    gem_names = [gem.get("name") for group in result.get("groups") or [] for gem in group.get("gems") or []]
    for text in flags.notes(summary.get("ascendClassName"), tree_jewels=bool(jewels or result.get("radius_jewels")),
                            crafted_jewels=crafted, crit_passives=_crit_passives(result.get("code")),
                            supports=flags.named_supports(gem_names), preview_on=on_in(result)):
        items.append({"kind": "1.0 changes pending", "title": text.split(": ", 1)[0], "why": text,
                      "how": "Nothing to check now: the numbers are 0.5.5's. Re-plan after the 1.0 patch notes "
                             "(about 4 Dec 2026) and the release-day data update."})
    return items


@cache
def _crit_node_ids() -> frozenset[int]:
    from builder.data import GameData
    from builder.preview import flags

    return frozenset(n["id"] for n in GameData().tree["nodes"] if flags.is_crit_passive(n))


def _crit_passives(code: str | None) -> int:
    """How many of the build code's allocated passives have an offensive critical-hit line (a result carries only the
    count of its passives). 0 when the code can't be read: the note is then not shown; no number depends on it."""
    if not code:
        return 0
    import binascii
    import zlib
    from xml.etree import ElementTree

    from builder.pob.codes import decode

    try:
        tree = ElementTree.fromstring(decode(code)).find("Tree")
        spec = tree.findall("Spec")[int(tree.get("activeSpec") or 1) - 1]
        allocated = {int(n) for n in (spec.get("nodes") or "").split(",") if n}
    except (ValueError, IndexError, AttributeError, ElementTree.ParseError, binascii.Error, zlib.error):
        return 0
    return len(allocated & _crit_node_ids())


def add_gems(data: Any) -> Any:
    """Put the preview gems into a GameData's gem list (in place; names PoB already has stay PoB's). Returns it."""
    names_there = {gem["name"] for gem in data.gems}
    data.__dict__["gems"] = list(data.gems) + [gem for gem in gems() if gem["name"] not in names_there]
    for cached in ("active_skills", "supports"):
        data.__dict__.pop(cached, None)
    return data


_with_gems: dict[str, Any] = {}


def data_for(request: Any, data: Any) -> Any:
    """The game data a request is checked against outside a search (the web form): `data` itself with the switch
    off, else one copy of it (made once per data folder) with the preview gems. Sets the rules' preview switch
    (builder/data/rules.py) to the request's."""
    from builder.data import GameData
    from builder.data.rules import set_preview

    set_preview(on(request))
    if not on(request):
        return data
    key = str(data.dir)
    if key not in _with_gems:
        _with_gems[key] = add_uniques(add_gems(GameData(data.dir)))
    return _with_gems[key]


def option_lists() -> dict[str, list[str]]:
    """The preview names for the request form's lists (shown marked, offered with the switch on)."""
    rows = gem_rows()
    return {"skills": sorted(r["name"] for r in rows if not r["support"]),
            "supports": sorted(r["name"] for r in rows if r["support"])}


@cache
def _uniques_file() -> dict:
    path = HERE / "uniques.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"uniques": []}


def unique_rows() -> list[dict]:
    """Every preview unique row (new in 1.0, or changed: 'changed': true replaces the 0.5.5 text)."""
    return list(_uniques_file()["uniques"])


def base_rows() -> list[dict]:
    """The bases preview uniques need and PoB lacks (uniques.json 'bases', each derived from its unique's card)."""
    return list(_uniques_file().get("bases") or [])


def add_bases(data: Any) -> Any:
    """Put the preview bases into a GameData's base list, hidden: they carry their uniques and are never offered as
    rares (their real mods and drop rules are unpublished). Returns it."""
    there = {base["name"]: base for base in data.bases}
    new = []
    for row in base_rows():
        if row["name"] in there:
            continue
        base = {key: value for key, value in there[row["like"]].items() if key != "implicit"}
        base.update(name=row["name"], type=row["type"], req=row["req"], hidden=True, preview=True)
        if row.get("weapon"):
            base["weapon"] = {**base["weapon"], **row["weapon"]}
        new.append(base)
    if new:
        data.__dict__["bases"] = list(data.bases) + new  # (this copy's list only, as add_gems does)
    return data


def unique_names() -> frozenset[str]:
    return frozenset(row["name"] for row in unique_rows())


def add_uniques(data: Any) -> Any:
    """Put the preview uniques into a GameData's unique list (in place): a new one is added, a changed one's 1.0 text
    replaces its 0.5.5 text (the row says what changed). Returns it."""
    add_bases(data)
    rows = {row["name"]: row for row in unique_rows()}
    out = []
    for unique in data.uniques:
        row = rows.get(unique["name"])
        if row is not None and row.get("changed"):
            unique = {**unique, "raw": row["raw"], "base": row.get("base", unique["base"]), "preview": True}
        out.append(unique)
    names_there = {u["name"] for u in out}
    out += [{key: row[key] for key in ("base", "key", "name", "raw", "type")} | {"preview": True}
            for row in rows.values() if not row.get("changed") and row["name"] not in names_there]
    data.__dict__["uniques"] = out
    data.__dict__.pop("unique_names", None)
    return data


def unique_price(request: Any) -> tuple[float | None, str] | None:
    """A new 1.0 unique has no market yet: offered unpriced only where cost doesn't limit the search (no divine cap, or
    SSF), never as free under a budget (the codebase's rule: unpriced is never free). A changed unique keeps its 0.5.5
    price (the caller's)."""
    budget = getattr(request, "budget", None)
    if budget is not None and getattr(budget, "divines", None) is not None and not getattr(budget, "ssf", False):
        return None
    return None, f"no market yet ({ASTERISK})"


def unique_note(row: dict) -> str:
    """The asterisked note for a preview unique row."""
    what = "changed in 1.0 (the 1.0 text replaces 0.5.5's)" if row.get("changed") else "new in 1.0"
    left = "; ".join(row.get("not_modelled") or []) or "nothing"
    return (f"{row['name']}{ASTERISK}: {what}, from GGG's reveal ({row.get('confidence')}); price: "
            f"{'its 0.5.5 market price' if row.get('changed') else 'no market yet'}; not counted: {left}")
