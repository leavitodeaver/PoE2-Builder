"""Ledger corrections on the item text the search builds (COV2, 12 Nov 2026).

Until now the search applied ledger translations only to passive lines (bridge `applyPassiveCorrections`) and unique
items' text (`SearchContext.corrected_raw`); a rune's, a rare or magic item's affix, a jewel's or a base implicit
line that an entry translates never reached a build, though the audit counted it covered (COV1's finding).

`corrected_item(raw)` returns the item's text with:
- for a rare, magic or normal item (not a unique: `corrected_raw` handles those), each line PoB can't parse that a
  `pob_gap` entry translates without a condition: the entry's lines added at the end; a line PoB parses that a
  `pob_wrong` entry corrects: replaced in place;
- for any item, its socketed runes' lines (PoB rebuilds them from the rune names, summing one rune's lines with
  another's of the same wording, as `ItemClass:UpdateRunes` does): a translated line's stand-ins added at the end, and
  a `pob_wrong` line switched off (a disabled rune line, which PoB keeps disabled after its rebuild) with its
  correction added at the end.
Added lines carry PoB's `{custom}` tag and the switched-off ones `{rune}{disabled}`, so calling it again on its own
output (after `with_runes` re-sockets the item, say) first removes them and starts over.

Which lines PoB can't parse comes from the data dump's coverage.json (`supported` on every affix, rune and base
implicit line, through PoB's own parser); PoB's parse depends on the wording only, so a rolled line is matched by its
template (numbers as #). With no coverage.json nothing is added (the old behaviour).

Not applied (UNVERIFIED how often they matter; listed in COV2's report): a rune's Bonded lines (they count only with a
source of "Bonded modifiers apply", or on an idol PoB marks so); soul cores' own slot entries; "increased effect of
socketed items" on the stand-in lines (PoB scales the rune's own line, not the stand-in); Timeless jewels' passives
(the tree, not item text); conditional entries (passives only, as for uniques).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from builder.ledger import translate, wrong

ADDED = "{custom}"
SWITCHED_OFF = "{rune}{disabled}"
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


def _base_of(lines: list[str], directory: Path | None) -> dict | None:
    bases = _bases(directory)
    for line in lines[:4]:
        if line in bases:
            return bases[line]
    return None


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
                if (translated := translate(line)) and not translated[0].conditional:
                    added += translated[1]
            elif fixed := wrong(line):
                lines[i] = fixed[1]
    runes = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Rune:")]
    if runes and (base := _base_of(lines, directory)):
        for line in rune_lines(runes, base, directory):
            if template(line) in unparsed:
                if (translated := translate(line)) and not translated[0].conditional:
                    added += translated[1]
            elif fixed := wrong(line):
                off.append(line)
                added.append(fixed[1])
    if off:
        at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
        if at is None:  # after the header and socket lines, as anoints.with_anoints places implicits
            at = next((i + 1 for i, l in enumerate(lines) if l.startswith(("Rune:", "Item Level:"))), len(lines))
            while at < len(lines) and lines[at].startswith(("Sockets:", "Rune:")):
                at += 1
            lines[at:at] = ["Implicits: 0"]
        lines[at] = f"Implicits: {int(lines[at].split(':', 1)[1]) + len(off)}"
        lines[at + 1:at + 1] = [SWITCHED_OFF + l for l in off]
    return "\n".join(lines + [ADDED + l for l in added])
