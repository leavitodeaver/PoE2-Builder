"""Ledger corrections on the item text the search builds (COV2, 12 Nov 2026).

Until now the search applied ledger translations only to passive lines (bridge `applyPassiveCorrections`) and unique
items' text (`SearchContext.corrected_raw`); a rune's, a rare or magic item's affix, a jewel's or a base implicit
line that an entry translates never reached a build, though the audit counted it covered (COV1's finding).

`corrected_item(raw)` returns the item's text with:
- for a rare, magic or normal item (not a unique: `corrected_raw` handles those), each line PoB can't parse that a
  `pob_gap` entry translates without a condition: the entry's lines added at the end; a line PoB parses that a
  `pob_wrong` entry corrects: replaced in place (switched off, `{disabled}`, for an entry with no replacement line and `item_off: true`:
  a model reads it, through bridge.sourceLines, which still lists disabled lines; COV7);
- for any item, its socketed runes' lines (PoB rebuilds them from the rune names, summing one rune's lines with
  another's of the same wording, as `ItemClass:UpdateRunes` does): a translated line's stand-ins added at the end, and
  a `pob_wrong` line switched off (a disabled rune line, which PoB keeps disabled after its rebuild) with its
  correction added at the end.
Added lines carry PoB's `{custom}` tag and the switched-off ones `{rune}{disabled}`, so calling it again on its own
output (after `with_runes` re-sockets the item, say) first removes them and starts over.

Which lines PoB can't parse comes from the data dump's coverage.json (`supported` on every affix, rune and base
implicit line, through PoB's own parser); PoB's parse depends on the wording only, so a rolled line is matched by its
template (numbers as #). With no coverage.json nothing is added (the old behaviour).

A socketed rune's, Soul Core's or Idol's Bonded line (COV13): PoB applies the Bonded lines it parses by itself, only
under a Bonded source (checked in the pinned PoB, COV12: ModParser's "gain the benefits of bonded modifiers on runes and
idols" sets CanUseBonded from the passive tree, Wisdom of the Maji's line, CalcSetup 1143; "idols socketed in this item
gain the benefits of their bonded modifiers" sets the item's SocketedIdolsUseBondedModifiers, Idols only; Item.lua
GetActiveModListForSlotNum then adds the Bonded mod lists). For a Bonded line PoB can't parse that a `pob_gap` entry
translates without a condition, the stand-in is added as `{custom}LedgerBonded:<augment type> <line>` (BONDED): PoB
can't parse that text, and the bridge (installLedgerBonded) turns it into a Bonded rune line of that augment type, so
PoB's own Bonded switch decides whether it counts (and the item's socketed-rune effect scales it as it would the
rune's own line). Bonded lines that feed a custom model (Archon buff effect / duration / recovery, Invocated half
cost: `mult:` entries) reach the models through bridge.sourceLines, which lists a Bonded line, without its "Bonded: "
prefix, only while PoB applies it.

Not applied: soul cores' own slot entries; "increased effect of socketed items" on the non-Bonded stand-in lines (PoB
scales the rune's own line, not the stand-in); Timeless jewels' passives (the tree, not item text); `flag:`, `if:`
and `mult:` entries (passives only, as for uniques; a `share:` entry is applied through its stand-in since COV17,
ledger.item_lines, but not as a Bonded line); a Bonded line PoB parses wrongly (`pob_wrong`; none today: checked, COV13, every Bonded line of
runes.json against wrong(): 0 of 559).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from builder.ledger import item_lines, translate, wrong

ADDED = "{custom}"
SWITCHED_OFF = "{rune}{disabled}"
BONDED = "LedgerBonded:"  # a Bonded line's stand-in, before its augment type; the bridge makes it a Bonded rune line
DISABLED = "{disabled}"  # an affix or implicit a `pob_wrong` entry with no replacement switches off (a model reads it)
NUMBER = re.compile(r"[+-]?\(?-?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\)?")  # coverage.template's
PLAIN_NUMBER = re.compile(r"\d+(?:\.\d+)?")
NOT_UNIQUE = ("Rarity: Rare", "Rarity: Magic", "Rarity: Normal")


def template(text: str) -> str:
    return NUMBER.sub("#", text).strip()


def _data_dir() -> Path:
    from builder.data import data_dir

    return data_dir()


@lru_cache(maxsize=4)
def unparsed_templates(directory: Path | None = None) -> frozenset[str]:
    """Templates of affix, rune and base implicit lines PoB's parser can't read (coverage.json)."""
    path = (directory or _data_dir()) / "coverage.json"
    if not path.exists():
        return frozenset()
    raw = json.loads(path.read_text(encoding="utf-8"))
    lines = [a["line"] for a in raw.get("affixes", []) if a.get("line") and not a.get("supported")]
    lines += [b["line"] for b in raw.get("base_implicits", []) if b.get("line") and not b.get("supported")]
    parsed = {template(a["line"]) for a in raw.get("affixes", []) if a.get("line") and a.get("supported")}
    # a range like "(-40-40)%" fails only as range text; rolled, the same template parses
    return frozenset(t for t in map(template, lines) if t not in parsed)


@lru_cache(maxsize=4)
def _runes(directory: Path | None = None) -> dict[str, dict]:
    path = (directory or _data_dir()) / "runes.json"
    if not path.exists():
        return {}
    return {r["name"]: r["slots"] for r in json.loads(path.read_text(encoding="utf-8"))["runes"]}


@lru_cache(maxsize=4)
def _bases(directory: Path | None = None) -> dict[str, dict]:
    path = (directory or _data_dir()) / "bases.json"
    if not path.exists():
        return {}
    return {b["name"]: b for b in json.loads(path.read_text(encoding="utf-8"))["bases"]}


def _summed(lines: list[str]) -> list[str]:
    """Rune lines as PoB shows them: lines of the same wording from several runes add up."""
    by_template: dict[str, list[float]] = {}
    sample: dict[str, str] = {}
    for line in lines:
        key = PLAIN_NUMBER.sub("#", line)
        values = [float(v) for v in PLAIN_NUMBER.findall(line)]
        if key in by_template:
            by_template[key] = [a + b for a, b in zip(by_template[key], values)]
        else:
            by_template[key], sample[key] = values, line
    out = []
    for key, values in by_template.items():
        numbers = iter(values)
        out.append(PLAIN_NUMBER.sub(lambda m: f"{v:g}" if (v := next(numbers)) != int(v) else str(int(v)), sample[key]))
    return out


def rune_lines(rune_names: list[str], base: dict, directory: Path | None = None) -> list[str]:
    """The (non-Bonded) lines these runes put on this base, summed as PoB sums them."""
    from builder.search.rares import rune_categories

    categories = rune_categories(base)
    runes = _runes(directory)
    lines = []
    for name in rune_names:
        for category, entry in (runes.get(name) or {}).items():
            if category in categories and isinstance(entry, dict):
                lines += [entry[k] for k in sorted((k for k in entry if k.isdigit()), key=int)]
    return _summed(lines)


def bonded_rune_lines(rune_names: list[str], base: dict, directory: Path | None = None) -> list[tuple[str, str]]:
    """The Bonded lines these runes put on this base, as (augment type, line), summed per type as PoB sums them."""
    from builder.search.rares import rune_categories

    categories = rune_categories(base)
    runes = _runes(directory)
    by_type: dict[str, list[str]] = {}
    for name in rune_names:
        for category, entry in (runes.get(name) or {}).items():
            if category in categories and isinstance(entry, dict) and isinstance(bonded := entry.get("bonded"), dict):
                by_type.setdefault(entry.get("type") or "Rune", []).extend(
                    bonded[k] for k in sorted((k for k in bonded if k.isdigit()), key=int))
    return [(augment, line) for augment, lines in by_type.items() for line in _summed(lines)]


def _base_of(lines: list[str], directory: Path | None) -> dict | None:
    bases = _bases(directory)
    for line in lines[:4]:
        if line in bases:
            return bases[line]
    return None


# "N% increased Explicit <type> Modifier magnitudes" (COV45; the manager 16:00, 19 Nov: Thrud's Might's influence affixes,
# DestructionInfluence*ModifierEffect, which SEARCH30's influence runes roll, and Verisium's Alloy for Resistance). The
# game's stats are PoE1's Heist enchantments, heist_enchantment_<tag>_mod_effect_+% (Elemental: local_explicit_elemental_
# damage_mod_effect_+%; game 26650308 Mods / Stats): the item's own explicit modifiers carrying that mod tag have their
# values raised by N% (the line itself, implicits and runes excluded). PoB ignores the line (no ModCache entry), so it
# counted 0: an UNDERCOUNT. Each scaled line's extra goes to PoB as a stand-in copy of it (ADDED) with every number
# raised by N% and truncated at the line's own precision (whole numbers, or hundredths for "1.5%"-style values).
# RESEARCH34 R34-9 (scratch/manager/research/explicit-modifier-magnitude-rounding.md), both readings MEDIUM (PoB's
# catalyst code ItemTools formatValue's floorSymmetric and Item.lua getCatalystScalar, a PoE1 tooltip report, the PoE2
# wiki's Quality page; no PoE2 test): each scaled stat is truncated toward zero at its own precision, and the WHOLE mod
# scales (tags belong to the modifier), so a hybrid line scales in every number. The other readings (unrounded; only
# the matching stat of a hybrid) would be rows: higher, not taken. A line's tags are those of PoB ModItem's mods with
# its template (the union where several share it: MEDIUM). Lines the game's scalability data marks unscalable (PoB
# Data/ModScalability.lua isScalable = false) are not raised; "+N to Level" lines are scalable there, but truncation
# leaves +1..+3 unchanged at every reachable magnitude (6-8% a prefix).
EXPLICIT_MAGNITUDE = re.compile(r"^(\d+(?:\.\d+)?)% increased Explicit (Critical|Elemental Damage|Fire|Cold|Lightning|Chaos|"
                                r"Physical|Mana|Speed|Resistance) Modifier magnitudes$")
MAGNITUDE_TAGS = {"Critical": "critical", "Elemental Damage": "elemental_damage", "Fire": "fire", "Cold": "cold",
                  "Lightning": "lightning", "Chaos": "chaos", "Physical": "physical", "Mana": "mana", "Speed": "speed",
                  "Resistance": "resistance"}


@lru_cache(maxsize=1)
def _unscalable() -> frozenset[str]:
    """Templates (numbers as "#", no sign) PoB's generated Data/ModScalability.lua marks isScalable = false."""
    from builder.ledger.coverage import POB_SRC

    text = (POB_SRC / "Data" / "ModScalability.lua").read_text(encoding="utf-8", errors="replace")
    return frozenset(m.group(1) for m in re.finditer(r'^\t\["(.*?)"\] = \{ \{ isScalable = false', text, re.M))


@lru_cache(maxsize=1)
def _template_tags() -> dict[str, frozenset[str]]:
    """Each item mod line's template -> the union of its mods' modTags (PoB Data/ModItem.lua)."""
    from builder.ledger.coverage import POB_SRC

    text = (POB_SRC / "Data" / "ModItem.lua").read_text(encoding="utf-8", errors="replace")
    found: dict[str, set[str]] = {}
    for row in re.finditer(r'^\s*\["[^"]+"\] = \{ type = "[^"]*", affix = "[^"]*", (.*?), statOrder = .*?modTags = \{([^}]*)\}',
                           text, re.M):
        tags = set(re.findall(r'"(\w+)"', row.group(2)))
        for line in re.findall(r'"((?:[^"\\]|\\.)*)"', row.group(1)):
            found.setdefault(template(line), set()).update(tags)
    return {k: frozenset(v) for k, v in found.items()}


def _raised(value: str, pct: float) -> str:
    """The extra of `value` raised by pct%, rounded down at its own precision ("" when it rounds to nothing)."""
    places = len(value.split(".")[1]) if "." in value else 0
    scale = 10 ** max(places, 2 if places else 0)
    old = round(float(value) * scale)
    extra = int(old * (1 + pct / 100)) - old
    if extra <= 0:
        return ""
    return f"{extra / scale:.{max(places, 2) if places else 0}f}".rstrip("0").rstrip(".") if places else str(extra)


def magnitude_lines(lines: list[str]) -> list[str]:
    """Stand-ins for the extra of the explicit lines an "Explicit <type> Modifier magnitudes" line raises (the note above)."""
    at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
    if at is None:
        return []
    explicit = [l for l in lines[at + 1 + int(lines[at].split(":", 1)[1]):] if l and not l.startswith("{")]
    raises: dict[str, float] = {}
    for line in explicit:
        if m := EXPLICIT_MAGNITUDE.match(line.strip()):
            tag = MAGNITUDE_TAGS[m.group(2)]
            raises[tag] = raises.get(tag, 0.0) + float(m.group(1))
    if not raises:
        return []
    tags_of = _template_tags()
    out = []
    for line in explicit:
        if EXPLICIT_MAGNITUDE.match(line.strip()):
            continue
        pct = sum(v for tag, v in raises.items() if tag in tags_of.get(template(line), ()))
        if pct <= 0 or re.sub(r"[+-]#", "#", template(line)) in _unscalable():
            continue
        parts, extras = PLAIN_NUMBER.split(line), [_raised(n, pct) for n in PLAIN_NUMBER.findall(line)]
        if not any(extras):
            continue
        out.append("".join(p + (extras[i] or "0") if i < len(extras) else p for i, p in enumerate(parts)))
    return out


def corrected_item(raw: str, directory: Path | None = None) -> str:
    """The item's text with the ledger's corrections (module docstring). Idempotent."""
    lines = [l for l in raw.split("\n") if not l.startswith(ADDED)]
    switched_off = sum(1 for l in lines if l.startswith(SWITCHED_OFF))
    if switched_off:
        lines = [l for l in lines if not l.startswith(SWITCHED_OFF)]
        at = next(i for i, l in enumerate(lines) if l.startswith("Implicits:"))
        lines[at] = f"Implicits: {int(lines[at].split(':', 1)[1]) - switched_off}"
    unparsed = unparsed_templates(directory)
    if not unparsed:
        return "\n".join(lines)
    added: list[str] = []
    off: list[str] = []
    if lines and lines[0] in NOT_UNIQUE:
        for i, line in enumerate(lines):
            if template(line) in unparsed:
                if (translated := translate(line)) and translated[0].on_items:
                    added += item_lines(translated[1])
            elif fixed := wrong(line):
                lines[i] = fixed[1] if fixed[1] is not None else DISABLED + line
        added += magnitude_lines(lines)  # (COV45: "N% increased Explicit <type> Modifier magnitudes")
    runes = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Rune:")]
    if runes and (base := _base_of(lines, directory)):
        for line in rune_lines(runes, base, directory):
            if template(line) in unparsed:
                if (translated := translate(line)) and translated[0].on_items:
                    added += item_lines(translated[1])
            elif fixed := wrong(line):
                off.append(line)
                if fixed[1] is not None:
                    added.append(fixed[1])
        for augment, line in bonded_rune_lines(runes, base, directory):
            if template(line) in unparsed:
                if (translated := translate(line)) and not translated[0].conditional:
                    added += [f"{BONDED}{augment} {standin}" for standin in translated[1]]
                elif translated and translated[0].on_items:  # (COV32: a `share:` stand-in, LedgerShare's item form)
                    added += [f"{BONDED}{augment} {standin}" for standin in item_lines(translated[1])]
            elif (fixed := wrong(line)) and fixed[0].raw.get("bonded_add") and fixed[1] is not None:
                # (COV32) a Bonded line PoB parses under a condition nothing sets (its own parse counts 0): the
                # entry's stand-in goes beside it as a Bonded line (Warding Rune of Armature's "while missing Runic Ward")
                added.append(f"{BONDED}{augment} {fixed[1]}")
    if off:
        at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
        if at is None:  # after the header and socket lines, as anoints.with_anoints places implicits
            at = next((i + 1 for i, l in enumerate(lines) if l.startswith(("Rune:", "Item Level:"))), len(lines))
            while at < len(lines) and lines[at].startswith(("Sockets:", "Rune:", "Quality:")):
                at += 1
            lines[at:at] = ["Implicits: 0"]
        lines[at] = f"Implicits: {int(lines[at].split(':', 1)[1]) + len(off)}"
        lines[at + 1:at + 1] = [SWITCHED_OFF + l for l in off]
    return "\n".join(lines + [ADDED + l for l in added])
