"""Bonded lines on display (SEARCHC1; THE USER, 23 Nov 2026 ~01:15, on the Oracle build's body armour: "it lists the bonded
modifiers- make sure it's not counting those unless it has a way to 'unlock' them, either with the fox idol or the shaman
node (or another method I'm not aware of)").

A rune's, Soul Core's or Idol's Bonded line counts only under a Bonded source. The scoring already follows that (PoB's
CanUseBonded / SocketedIdolsUseBondedModifiers switches; the ledger's Bonded stand-ins go through the same switch:
builder/ledger/items.py), so this module changes no number: it marks every Bonded line wherever item text or a rune's
lines are shown, "(inactive: no Bonded source)", unless the build unlocks it.

The sources (checked: game 26650308, every Stats id holding "additional_socketable_mods" or "bonded" (4 stats) looked up
in every table with a stat column (Mods, PassiveSkills, SoulCoreStats, the rest): ascendancy_shaman_gain_additional_
socketable_mods is on PassiveSkills AscendancyDruid2Notable8 alone (Shaman's Wisdom of the Maji: "Gain the benefits of
Bonded modifiers on Runes and Idols"), local_idols_gain_additional_socketable_mods on SoulCoreStats row 235 alone (Fox
Idol, its Body Armour row: "Idols socketed in this item gain the benefits of their Bonded modifiers"); no Mods row
(item, unique, jewel, rune) carries either; second sources: KeywordPopups ShamanOnlyMods "Bonded Modifiers ... available
only to players who have allocated the Shaman's Wisdom of the Maji Ascendancy Notable" (it omits the Fox Idol, which came
in 0.5.0: patch notes 0.5.0 "Fox Idol ... now grants Other Socketed Idols in this item also grant their Bonded Modifiers
when socketed in Body Armours"); PoB's ModParser maps the same two lines to CanUseBonded and the item's
SocketedIdolsUseBondedModifiers. Megalomaniac's "Bonded Precision" is a notable's name, not an unlock.)

The Fox Idol's own Bonded line: the game text says "Idols socketed in this item", the 0.5.0 note "Other Socketed Idols":
whether its own counts is UNSETTLED; PoB counts every Idol in the item, and so does this display (both readings named).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from builder.ledger.items import bonded_rune_lines, rune_lines
from builder.pob import codes

UNLOCK_NODE = "Wisdom of the Maji"  # Shaman (PassiveSkills AscendancyDruid2Notable8)
IDOL_UNLOCK = re.compile(r"Idols (?:socketed in this item|in this item also).*Bonded modifiers", re.I)
INACTIVE = " (inactive: no Bonded source)"
BONDED_LINE = re.compile(r"^((?:\{[^}]*\})*)Bonded: (.*)$")
RUNE_LINE = re.compile(r"^Rune: (.+)$")


def node_ids(data, name: str = UNLOCK_NODE) -> set[str]:
    """The passive tree's ids of the node(s) with this name."""
    nodes = (data.tree or {}).get("nodes") or {}
    items = nodes.items() if isinstance(nodes, dict) else ((node.get("id"), node) for node in nodes)
    return {str(node.get("id", k)) for k, node in items if isinstance(node, dict) and node.get("name") == name}


def build_unlocks(xml: str, data) -> bool:
    """Does the build allocate Wisdom of the Maji (every Bonded line of every Rune and Idol counts)?"""
    wanted = node_ids(data)
    for spec in re.finditer(r"<Spec\b[^>]*\bnodes=\"([^\"]*)\"", xml):
        if wanted & set(spec.group(1).split(",")):
            return True
    return False


def idol_unlock(rune_names: list[str], base: dict) -> bool:
    """Does an augment in this item let its Idols use their Bonded lines (the Fox Idol in a Body Armour)?"""
    return any(IDOL_UNLOCK.search(line) for line in rune_lines(list(rune_names), base))


def active(augment_type: str, build_unlocked: bool, idols_unlocked: bool) -> bool:
    return build_unlocked or (idols_unlocked and augment_type == "Idol")


def rune_effects(rune_names: list[str], base: dict | None, build_unlocked: bool) -> list[dict]:
    """Each socketed augment's own lines on this base and its Bonded lines, each marked active or not:
    [{"name", "lines", "bonded": [{"line", "active"}]}]."""
    if not base:
        return [{"name": n, "lines": [], "bonded": []} for n in rune_names]
    idols = idol_unlock(rune_names, base)
    out = []
    for name in rune_names:
        bonded = [{"line": line, "active": active(kind, build_unlocked, idols)}
                  for kind, line in bonded_rune_lines([name], base)]
        out.append({"name": name, "lines": rune_lines([name], base), "bonded": bonded})
    return out


def mark_text(raw: str, base: dict | None, build_unlocked: bool) -> str:
    """The item text with each Bonded line that doesn't count marked INACTIVE."""
    lines = raw.splitlines()
    names = [m.group(1).strip() for line in lines if (m := RUNE_LINE.match(line.strip()))]
    # (SEARCHC2: the unlock is read from the LINE wherever it shows, the item's own text included, e.g. an imported
    # Morior Invictus / Hyrri's Ire whose export lists a socketed Fox Idol's line among the item's lines; R52's 20
    # builds; not from item names)
    idols = (bool(base) and idol_unlock(names, base)) or any(
        IDOL_UNLOCK.search(line) and not BONDED_LINE.match(line.strip()) for line in lines)
    kinds: dict[str, str] = {}
    if base:
        for kind, line in bonded_rune_lines(names, base):
            kinds.setdefault(line, kind)
    out = []
    for line in lines:
        m = BONDED_LINE.match(line.strip())
        if m and not line.rstrip().endswith(INACTIVE.strip()):
            kind = kinds.get(m.group(2).strip(), "Rune")  # (a line not found: counted as a Rune's, the stricter reading)
            if not active(kind, build_unlocked, idols):
                line = line.rstrip() + INACTIVE
        out.append(line)
    return "\n".join(out)


def xml_items(xml: str) -> dict[str, str]:
    """{slot: item text} of the build's active item set."""
    texts = {m.group(1): re.sub(r"\s*<ModRange[^>]*/>", "", m.group(2)).strip()
             for m in re.finditer(r"<Item id=\"(\d+)\"[^>]*>(.*?)</Item>", xml, re.S)}
    active_set = re.search(r"<Items\b[^>]*\bactiveItemSet=\"(\d+)\"", xml)
    sets = {m.group(1): m.group(2) for m in re.finditer(r"<ItemSet id=\"(\d+)\"[^>]*>(.*?)</ItemSet>", xml, re.S)}
    body = sets.get(active_set.group(1)) if active_set else next(iter(sets.values()), "")
    slots = {}
    for m in re.finditer(r"<Slot\b([^>]*)/>", body or ""):
        attrs = dict(re.findall(r"(\w+)=\"([^\"]*)\"", m.group(1)))
        if attrs.get("itemId", "0") != "0" and attrs.get("itemId") in texts:
            slots[attrs.get("name", "")] = "\n".join(l.strip() for l in texts[attrs["itemId"]].splitlines())
    return slots


def base_of(raw: str, data) -> dict | None:
    bases = {b["name"]: b for b in data.bases}
    for line in raw.splitlines()[:4]:
        if line.strip() in bases:
            return bases[line.strip()]
    return None


def result_unlocks(result: dict, data) -> bool:
    """Does a saved result's build unlock every Bonded line (its code's tree)? False without a code."""
    try:
        return bool(result.get("code")) and build_unlocks(codes.decode(result["code"]), data)
    except Exception:  # (an unreadable code: the stricter reading, nothing unlocked)
        return False


def marked_items(result: dict, data) -> dict[str, str]:
    """{slot: the item text the build was scored with, Bonded lines marked} for a saved result (its build code)."""
    if not result.get("code"):
        return {}
    xml = codes.decode(result["code"])
    unlocked = build_unlocks(xml, data)
    return {slot: mark_text(raw, base_of(raw, data), unlocked) for slot, raw in xml_items(xml).items()}


def main(argv: list[str]) -> None:
    """python -m builder.report.bonded <result.json>: every item's text, Bonded lines marked (for a write-up)."""
    from builder.data import GameData

    result = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    for slot, text in marked_items(result, GameData()).items():
        print(f"### {slot}\n```\n{text}\n```")


if __name__ == "__main__":
    main(sys.argv[1:])
