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
    share:JaggedGround 10% more Damage  # this line counts at the share of the fight "JaggedGround" holds
A condition is switched on by a passive's `flag:` line or by a `condition` entry naming the skills
that provide it (`skills`: regex on active skill and support gem names; `skill_text`: regex on
active skills' gem descriptions). A condition the fight decides only in part (the boss stands in the build's
Jagged Ground some of the time) is a share, not on / off: a `share:` line counts its full value x that share
(bridge applyLedger: value / 100 x Multiplier:LedgerShare<Name>, which assumptions.ledger_conditions sets to the share
in whole percent; COV11). Conditional lines apply to passives only, not to uniques.

Lines that feed a custom model (builder/search/models.py) rather than standing for a modifier of
their own name it with `model:` and use
    mult:ArchonDuration 15         # this passive adds 15 to the model input "ArchonDuration"
alongside `flag:` for the model's on/off inputs. The model reads the same lines on items itself.

A `pob_wrong` entry is for a unique line PoB does calculate, but wrongly: the line is replaced in
the item's text by the entry's single replacement line. With an empty `replace` the item line is
left alone unless the entry says `item_off: true` (then it's switched off, PoB's `{disabled}`); say
that only when a model reads the item line itself, or the line is lost with nothing counting it
(`wrong()`; on the tree an empty replace always drops PoB's parse).

A `gem_stat` entry is for a skill's stat PoB never reads: `skill` names the skill, `match` is a regex
on the stat id, and `replace` lines (written with a magnitude of 1) are scaled by the stat's value at
the gem's level × `factor`; `applies_to: build` gives them to the whole build (a buff), `skill` (the
default) to that skill only. An empty `replace` records that the stat changes nothing calculated.

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
SYNTAX = re.compile(r"^(flag|if|mult|share):(\w+)\s*(.*)$")
ROLLED = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")  # a roll range inside a line
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def merge_ends(low: str, high: str) -> str:
    """Two replacement lines that differ only in their numbers, as one line with "(low-high)" ranges."""
    lows, highs = NUMBER.findall(low), NUMBER.findall(high)
    if NUMBER.split(low) != NUMBER.split(high) or len(lows) != len(highs):
        return low
    numbers = iter(f"({a}-{b})" if a != b else a for a, b in zip(lows, highs))
    return NUMBER.sub(lambda _: next(numbers), low)


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
            return self._translate_rolled(line)
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

    def _translate_rolled(self, line: str) -> list[str] | None:
        """A rolled range the pattern's plain-number groups can't match ("(10-18)% chance ..." against a plain-number group): the
        line at the range's low and high ends, the replacements merged number by number into ranges (COV5)."""
        if not ROLLED.search(line):
            return None
        ends = [self.translate(ROLLED.sub(lambda m: m.group(end), line)) for end in (1, 2)]
        if ends[0] is None or ends[1] is None or len(ends[0]) != len(ends[1]):
            return None
        return [merge_ends(low, high) for low, high in zip(*ends)]

    def matches(self, line: str) -> bool:
        """The pattern finds the line, or the line at both ends of its rolled ranges."""
        if self.pattern is None:
            return False
        if self.pattern.search(line):
            return True
        return bool(ROLLED.search(line)) and all(self.pattern.search(ROLLED.sub(lambda m: m.group(end), line))
                                                 for end in (1, 2))

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


def wrong(line: str) -> tuple[Entry, str | None] | None:
    """A `pob_wrong` entry for an item line PoB parses but calculates wrongly, with the one line that replaces it.

    An entry with no replacement switches the item line off (None as the line: PoB's `{disabled}`; the line still
    reaches the models through bridge.sourceLines) ONLY if it says `item_off: true`, which it may say only when a model
    reads the ITEM line (COV7 for the Arcane Surge on Critical Hit affix, whose uptime model reads item lines). Without
    it an empty replace leaves items alone (None from here), since its model may read tree lines only: switching the
    item line off would zero it with nothing counting it, an undercount (COV8, the manager's ruling after train 13:
    COVB7's flask-used entries, whose flask_used_model reads tree lines only)."""
    for entry in entries():
        if entry.kind == "pob_wrong":
            lines = entry.translate(line)
            if lines is None or (not lines and not entry.raw.get("item_off")):
                continue
            return entry, (lines[0] if lines else None)
    return None


def checked_parse(line: str, source: str | None = None) -> Entry | None:
    """A `parse_checked` entry: a line the audit's misparse check flags, whose PoB parse was read by hand and is right
    (the entry's notes say why: PoB's own number convention, a scaling line elsewhere on the item, a flag a calculation
    handles). Nothing is applied; the audit counts the line as checked."""
    for entry in entries():
        if entry.kind == "parse_checked" and entry.pattern is not None and entry.pattern.search(line)                 and (not entry.raw.get("only_sources") or source in entry.raw["only_sources"]):
            return entry
    return None


def model_entry(line: str) -> Entry | None:
    """The entry that feeds this line to a custom model, if any (wherever the line is)."""
    for entry in entries():
        if entry.raw.get("model") and entry.matches(line):
            return entry
    return None


def gem_stat_entries() -> list[Entry]:
    """`gem_stat` entries: a gem stat PoB never reads (regex `match` on the stat id), translated into
    modifier lines scaled by the stat's value (builder/search/models.py gem_stat_model)."""
    return [e for e in entries() if e.kind == "gem_stat"]


def conditions() -> list[dict]:
    """`condition` entries: which skills switch a ledger condition on."""
    return [e.raw for e in entries() if e.kind == "condition"]


def readable(line: str) -> str:
    """A correction line as the result shows it."""
    m = SYNTAX.match(line)
    if not m:
        return line
    kind, name, rest = m.groups()
    if kind == "mult":
        return f"{rest} to the model input {name}"
    if kind == "share":
        return f"{rest} (x the share of the fight with {name})"
    return f"enables {name}" if kind == "flag" else f"{rest} (with {name})"


def gap_list(data_dir: Path) -> dict:
    path = data_dir / "gaps.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"passives": [], "uniques": []}
