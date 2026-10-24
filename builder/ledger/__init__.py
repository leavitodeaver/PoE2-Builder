"""The correction ledger (PLAN.md §6): what Path of Building gets wrong or leaves out, as data.

Entries live in ledger/entries/*.yaml. A `pob_gap` entry translates a line PoB ignores into
modifier text PoB does calculate:

    id: tree-cast-speed-per-spell
    title: Cast Speed per different Spell cast recently
    kind: pob_gap
    match: '^(\\d+)% increased Cast Speed for each different Spell'   # regex on the ignored line
    replace: ['{1}% increased Cast Speed']                            # {n} = the match's groups
    confidence: community_confirmed      # verified_by_you | community_confirmed | reported | theory
    sources: ["..."]
    status: proposed                     # proposed | approved | rejected | retired

Numbers in `replace` may be arithmetic on groups g1, g2, ..., e.g. '{g1*3}'. Only proposed and approved entries
apply; every number they touch is shown with an asterisk and the entry's confidence.

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
        groups = {str(i): float(g) for i, g in enumerate(match.groups(), start=1) if g and _number(g)}

        def fill(m: re.Match) -> str:
            expression = m.group(1).strip()
            if expression in groups:  # {1}: the first group as matched
                expression = repr(groups[expression])
            else:  # {g1*2}: arithmetic on groups g1, g2, ...
                expression = re.sub(r"\bg(\d+)\b", lambda g: repr(groups.get(g.group(1), float("nan"))), expression)
            if not re.fullmatch(r"[\d.+\-*/() ]+", expression):
                return m.group(0)
            value = eval(expression, {"__builtins__": {}})  # arithmetic only, checked above
            return str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:.2f}".rstrip("0")

        return [TEMPLATE.sub(fill, template) for template in self.replace]


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


def gap_list(data_dir: Path) -> dict:
    path = data_dir / "gaps.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"passives": [], "uniques": []}
