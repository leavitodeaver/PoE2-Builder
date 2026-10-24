"""The correction ledger (PLAN.md §6): what Path of Building gets wrong or leaves out, as data.

Entries live in ledger/entries/*.yaml. A `pob_gap` entry translates a line PoB ignores into
modifier text PoB does calculate:

    id: tree-cast-speed-per-spell
    title: Cast Speed per different Spell cast recently
    kind: pob_gap
    match: '^(\\d+)% increased Cast Speed for each different Spell'   # regex on the ignored line
    replace: ['{1}% increased Cast Speed']                            # {n} = the match's groups
    confidence: community_confirmed      # verified_by_you | datamined | community_confirmed | reported | theory
    sources: ["..."]
    status: proposed                     # proposed | approved | rejected | retired

Numbers in `replace` may be arithmetic on groups g1, g2, ..., e.g. '{g1*3}', and are rounded to whole numbers
(PoB's parser reads whole percentages). A unique's rolled range like '(15-30)' is computed at both ends and
stays a range. Only proposed and approved entries apply; every number they touch is shown with an asterisk
and the entry's confidence.

Corrections that only hold together with something else use conditions:
    flag:Witchbane                 # this passive switches on condition "Witchbane"
    if:Witchbane 36% more Damage   # this line only counts while "Witchbane" is on
A condition is switched on by a passive's `flag:` line or by a `condition` entry naming the skills
that provide it (`skills`: regex on active skill and support gem names; `skill_text`: regex on
active skills' gem descriptions). Conditional lines apply to passives only, not to uniques.

A `pob_wrong` entry is for a unique line PoB does calculate, but wrongly: the line is replaced in
the item's text by the entry's single replacement line.

`gap lists` come from PoB itself (bridge `auditUnsupported`, saved by the data dump as gaps.json):
every passive and unique line its parser doesn't understand.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from builder import ROOT

ENTRY_DIR = ROOT / "ledger" / "entries"
ACTIVE = {"proposed", "approved"}
TEMPLATE = re.compile(r"\{([^{}]+)\}")
RANGE = re.compile(r"^\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)$")
SYNTAX = re.compile(r"^(flag|if):(\w+)\s*(.*)$")


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    kind: str
    pattern: re.Pattern | None
    replace: tuple[str, ...]
    confidence: str
    status: str
    sources: tuple[str, ...] = ()
    notes: str = ""
    raw: dict = field(default_factory=dict, compare=False, hash=False)

    def translate(self, line: str) -> list[str] | None:
        if self.pattern is None:
            return None
        match = self.pattern.search(line)
        if not match:
            return None
        groups: dict[str, tuple[float, float]] = {}
        for i, g in enumerate(match.groups(), start=1):
            if g and _number(g):
                groups[str(i)] = (float(g), float(g))
            elif g and (r := RANGE.match(g)):
                groups[str(i)] = (float(r.group(1)), float(r.group(2)))

        def fill(m: re.Match) -> str:
            expression = m.group(1).strip()
            if expression in groups:  # {1}: the first group as matched
                expression = f"g{expression}"
            if not re.fullmatch(r"[\dg.+\-*/() ]+", expression):
                return m.group(0)
            values = []
            for end in (0, 1):  # {g1*2}: arithmetic on groups g1, g2, ..., at both ends of a rolled range
                arithmetic = re.sub(r"\bg(\d+)\b", lambda g: repr(groups.get(g.group(1), (float("nan"),) * 2)[end]),
                                    expression)
                values.append(eval(arithmetic, {"__builtins__": {}}))  # arithmetic only, checked above
            low, high = (int(round(v)) for v in values)
            return str(low) if low == high else f"({low}-{high})"

        return [TEMPLATE.sub(fill, template) for template in self.replace]

    @property
    def conditional(self) -> bool:
        return any(SYNTAX.match(line) for line in self.replace)


def _number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


@lru_cache(maxsize=1)
def entries(directory: Path = ENTRY_DIR) -> tuple[Entry, ...]:
    found = []
    for path in sorted(directory.glob("*.yaml")):
        documents = yaml.safe_load_all(path.read_text(encoding="utf-8"))
        for data in documents:
            for item in data if isinstance(data, list) else [data]:
                if not item or item.get("status", "proposed") not in ACTIVE:
                    continue
                pattern = re.compile(item["match"], re.I) if item.get("match") else None
                found.append(Entry(item["id"], item.get("title", item["id"]), item.get("kind", "pob_gap"), pattern,
                                   tuple(item.get("replace") or ()), item.get("confidence", "theory"),
                                   item.get("status", "proposed"), tuple(item.get("sources") or ()),
                                   item.get("notes", ""), item))
    return tuple(found)


def translate(line: str) -> tuple[Entry, list[str]] | None:
    """The first active gap entry that covers this ignored line, with its replacement lines."""
    for entry in entries():
        if entry.kind == "pob_gap":
            lines = entry.translate(line)
            if lines is not None:
                return entry, lines
    return None


def wrong(line: str) -> tuple[Entry, str] | None:
    """A `pob_wrong` entry for a unique line PoB parses but calculates wrongly, with the one line
    that replaces it."""
    for entry in entries():
        if entry.kind == "pob_wrong":
            lines = entry.translate(line)
            if lines:
                return entry, lines[0]
    return None


def conditions() -> list[dict]:
    """`condition` entries: which skills switch a ledger condition on."""
    return [e.raw for e in entries() if e.kind == "condition"]


def readable(line: str) -> str:
    """A correction line as the result shows it."""
    m = SYNTAX.match(line)
    if not m:
        return line
    kind, name, rest = m.groups()
    return f"enables {name}" if kind == "flag" else f"{rest} (with {name})"


def gap_list(data_dir: Path) -> dict:
    path = data_dir / "gaps.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"passives": [], "uniques": []}
