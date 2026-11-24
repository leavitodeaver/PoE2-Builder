"""The rare legality check (SEARCHC4; the manager, 23 Nov 21:27): every way a delivered rare breaks the game's item rules.

It reads a result's gear entries (ItemOption.describe), so it works on live results and saved ones alike. A line of text
can fit several mod rows, so the check tries every reading of the item and reports the reading with the fewest breaks:
an item is ILLEGAL only when NO reading of it is legal. A rule that isn't verified is reported as UNVERIFIED and never
fails a build. Each rule names its source in RULES.

Routes that legally change what a rare may hold are read from the item: Serle's Triumph (one more suffix), Astrid's
Creativity (one more crafted line), an Aldur rune or a Flux (they turn lines into another element's, so two can share a
group and a line can be one the base doesn't roll), an influence rune (it opens another mod pool).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from builder.data import GameData
from builder.search import alloys, essences
from builder.search.context import implicit_variants
from builder.search.rares import ALDUR_RUNES, NOT_IN_SEALED, excluded_by, exclusion_group, mod_lines, pool_tags

ILLEGAL = "ILLEGAL"
UNVERIFIED = "UNVERIFIED"

SERLE = "Serle's Triumph"
ASTRID = "Astrid's Creativity"
INFLUENCE_RUNES = frozenset({"Vorana's Carnage", "Kolr's Hunt", "Katla's Gloom", "Uhtred's Sidereus", "Medved's Tending",
                             "Thrud's Might"})
HORROR_MOD = "EssenceLocalRuneAndSoulCoreEffect1"
HORROR_CLASSES = ("Gloves", "Boots")

RULES = {
    "affix_count": "Prefixes and suffixes within the item's limit: rare 3 + 3, magic 1 + 1, jewel 2 + 2, moved by the base's "
                   "'N Prefix / Suffix Modifier allowed' implicit (game stats local_maximum_prefixes / suffixes_allowed_+, "
                   "Mods Ring / AmuletImplicitPrefixSuffixAllowed*), Serle's Triumph (+1 suffix, SoulCores, R52-5) and a "
                   "jewel's crafted 'Effect of Prefixes / Suffixes' shape (the user, 24 Oct)",
    "mod_group": "One modifier per mod group (PoB's group; each of its 1,125 groups holds one game Mods.Families set, "
                 "checked 23 Nov), with the skill-level family IncreaseSocketedGemLevel as one group (R54-9, SEARCHB7)",
    "tag_conflict": "No ordinary modifier beside a modifier whose tags give it zero spawn weight (PoB ModItem's tags and "
                    "weights, from the game's Mods; e.g. no_cold_spell_mods on a caster's spell-damage prefix)",
    "game_family": "UNVERIFIED: two lines of one game family in different PoB groups (R54-9: only the skill-level family "
                   "is verified; 65 of 390 families span several PoB groups)",
    "crafted_limit": "One crafted modifier per item, two with Astrid's Creativity (0.5.0: 'items can only have 1 crafted "
                     "modifier at a time'; Alloys add 'crafted modifiers', 0.5.0; game stat "
                     "local_can_have_additional_crafted_mods)",
    "essence_crafted": "UNVERIFIED: an Essence line beside another crafted line (whether a Perfect / Corrupted Essence's line "
                       "counts as crafted is open, RQ-F)",
    "desecrated_limit": "One Desecrated modifier per item (0.5.0: 'items are limited to 1 Desecrated modifier')",
    "base_allowed": "The modifier can appear on this base: its spawn weights for the base's tags (PoB ModItem, from the "
                    "game's Mods), or the item classes of its Alloy (alloys.ALLOYS) or Perfect Essence (essences.ESSENCES)",
    "sealed_rune": "No Serle's / Astrid's / influence-type augment on a Corrupted item (0.5.4: they 'can no longer be "
                   "socketed ... on Corrupted or Sanctified items')",
    "unread": "UNVERIFIED: a line that fits no mod row in the tables read here, so nothing is claimed about it",
}

NUMBER = re.compile(r"\(-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?\)|-?\d+(?:\.\d+)?")
ALLOWED = re.compile(r"^([+-]\d+) (Prefix|Suffix) Modifiers? allowed\s*$", re.M)
JEWEL_EFFECT = re.compile(r"increased Effect of (Prefix|Suffix)es", re.I)
RESIST_GROUP = re.compile(r"resist", re.I)
MAX_READINGS = 20000  # how many complete readings one item may try (never reached by a 3 + 3 rare; a safety limit)


@dataclass(frozen=True)
class Finding:
    rule: str
    status: str  # ILLEGAL or UNVERIFIED
    detail: str


@dataclass(frozen=True)
class Row:
    id: str
    type: str
    group: str
    kind: str  # natural / alloy / essence / crafted (a jewel's Liquid Emotion line) / desecrated
    weights: tuple[tuple[str, int], ...]
    families: tuple
    adds: frozenset = frozenset()  # the tags it adds to the item
    blocked: frozenset = frozenset()  # the tags that give it zero spawn weight


def template(line: str) -> str:
    return NUMBER.sub("#", line.strip())


@lru_cache(maxsize=1)
def game_families() -> dict:
    """Mod id -> its game Families, from the newest extracted game tables ({} when none are in this checkout)."""
    root = Path(__file__).resolve().parents[2] / "data"
    for folder in sorted(root.glob("game-*"), reverse=True):
        path = folder / "tables" / "Mods.json"
        if path.exists():
            return {m["Id"]: tuple(m.get("Families") or ()) for m in json.loads(path.read_text(encoding="utf8"))}
    return {}


class Checker:
    def __init__(self, data: GameData | None = None) -> None:
        self.data = data or GameData()
        families = game_families()
        self.bases: dict[str, list[dict]] = {}
        for base in self.data.bases:
            self.bases.setdefault(base["name"], []).append(base)
        self.index: dict[str, dict[tuple[str, ...], list[Row]]] = {}
        for pool in ("Item", "Desecrated", "Jewel", "Flask", "Charm"):
            table: dict[tuple[str, ...], list[Row]] = {}
            for mod in self.data.mods.get(pool, []):
                if mod.get("type") not in ("Prefix", "Suffix"):
                    continue
                kind = "desecrated" if pool == "Desecrated" else "alloy" if mod["id"] in alloys.ALLOYS \
                    else "essence" if mod["id"].startswith("Essence")                     else "crafted" if mod["id"].startswith("CraftedJewel") else "natural"
                row = Row(mod["id"], mod["type"], exclusion_group(mod["group"]), kind,
                          tuple(zip(mod.get("weightKey") or (), mod.get("weightVal") or ())), families.get(mod["id"], ()),
                          frozenset(mod.get("tags") or ()), excluded_by(mod))
                table.setdefault(tuple(template(x) for x in mod_lines(mod)), []).append(row)
            self.index[pool] = table

    # What the item may hold -------------------------------------------------------------------------------------------

    def limits(self, item: dict, lines: list[str]) -> dict[str, int]:
        name, kind = str(item.get("name") or ""), item.get("type")
        if name.startswith("Magic"):
            return {"Prefix": 1, "Suffix": 1}
        limits = {"Prefix": 2, "Suffix": 2} if kind == "Jewel" else {"Prefix": 3, "Suffix": 3}
        if kind == "Jewel":
            for line in lines:  # (the crafted "Effect of Prefixes" line stands where the +1 stood: 3 of that side)
                if m := JEWEL_EFFECT.search(line):
                    limits["Prefix" if m.group(1).lower() == "prefix" else "Suffix"] += 1
        moves = []  # the most generous variant / copy of this base: a limit is never read tighter than some copy allows
        for base in self.bases.get(item.get("base"), []):
            implicit = base.get("implicit") or ""
            variants = [line for _, line in implicit_variants(implicit)] or [implicit]
            for text in variants:
                moved = {"Prefix": 0, "Suffix": 0}
                for m in ALLOWED.finditer(text):
                    moved[m.group(2)] += int(m.group(1))
                moves.append(moved)
        if moves:
            for side in limits:
                limits[side] = max(0, limits[side] + max(m[side] for m in moves))
        if SERLE in (item.get("runes") or ()):
            limits["Suffix"] += 1
        return limits

    def tags(self, item: dict) -> list[set[str]]:
        return [pool_tags(b) for b in self.bases.get(item.get("base"), [])]

    @staticmethod
    def spawns(row: Row, tags: set[str]) -> bool:
        for key, value in row.weights:
            if key in tags or key == "default":
                return value > 0
        return False

    def allowed(self, row: Row, item: dict, tag_sets: list[set[str]]) -> bool | None:
        """True / False, or None where this check has no table for it (never a failure)."""
        kinds = {alloys.item_class(b) for b in self.bases.get(item.get("base"), [])}
        if row.kind == "alloy":
            return bool(kinds & set(alloys.ALLOYS[row.id][1]))
        if row.kind == "essence":
            if row.id == HORROR_MOD:
                return bool(kinds & set(HORROR_CLASSES))
            entry = essences.ESSENCES.get(row.id)
            return bool(kinds & set(entry[1])) if entry else None
        if row.kind in ("desecrated", "crafted"):
            return None  # (a bone's or a Liquid Emotion's own route decides it: not re-judged here)
        return any(self.spawns(row, t) for t in tag_sets) if tag_sets else None

    # Reading the lines ------------------------------------------------------------------------------------------------

    def pools(self, item: dict) -> tuple[str, ...]:
        kind = item.get("type")
        if kind == "Jewel":
            return ("Jewel", "Desecrated")
        if kind == "Flask":
            return ("Flask",)
        if kind == "Charm":
            return ("Charm",)
        return ("Item", "Desecrated")

    def fits(self, lines: list[str], item: dict) -> list[dict[int, list[Row]]]:
        """For each line: {how many lines from here form one modifier: the rows that fit}. A hybrid's lines sit together,
        and the same two lines may also be two separate modifiers ("+N to Armour", "N% increased Armour"), so both stay."""
        keys = [template(x) for x in lines]
        tables = [self.index[p] for p in self.pools(item)]
        out: list[dict[int, list[Row]]] = []
        for i in range(len(lines)):
            here: dict[int, list[Row]] = {}
            for size in (1, 2, 3, 4):
                key = tuple(keys[i:i + size])
                if len(key) == size and (rows := [r for t in tables for r in t.get(key, ())]):
                    here[size] = rows
            out.append(here)
        return out

    @staticmethod
    def distinct(rows: list[Row], item_allowed) -> list[tuple[Row, bool | None]]:
        """One row per way it can count (side, group, kind, allowed), legal-looking first."""
        seen: dict[tuple, tuple[Row, bool | None]] = {}
        for row in rows:
            state = item_allowed(row)
            seen.setdefault((row.type, row.group, row.kind, state, row.families), (row, state))
        return sorted(seen.values(), key=lambda x: x[1] is False)

    def check(self, item: dict) -> list[Finding]:
        """Every break of the item's best reading. Uniques and non-rare items have none here."""
        if item.get("unique") or not item.get("rare"):
            return []
        lines = list(item.get("affixes") or ())
        runes = set(item.get("runes") or ()) | set(item.get("bound_runes") or ())
        route = " ".join(item.get("craft_route") or ())
        findings: list[Finding] = []
        if "Sockets (corrupted)" in route or item.get("corrupted"):
            for name in sorted(runes & set(NOT_IN_SEALED)):
                findings.append(Finding("sealed_rune", ILLEGAL, f"{name} on a Corrupted item"))
        fits = self.fits(lines, item)
        limits = self.limits(item, lines)
        aldur = bool(runes & set(ALDUR_RUNES.values()))
        flux = "Flux" in route
        influenced = bool(runes & INFLUENCE_RUNES)
        crafted_limit = 2 if ASTRID in runes else 1
        tag_sets = self.tags(item)
        choices = [{size: self.distinct(rows, lambda r: self.allowed(r, item, tag_sets)) for size, rows in here.items()}
                   for here in fits]

        def converted(row: Row) -> bool:  # (a line a route rewrote: its group and spawn weights no longer bind it)
            return aldur or (flux and bool(RESIST_GROUP.search(row.group)))

        def breaks(reading: list[tuple[Row, bool | None, list[str]]]) -> list[Finding]:
            out: list[Finding] = []
            for side, limit in limits.items():
                held = sum(1 for row, _, _ in reading if row.type == side)
                if held > limit:
                    out.append(Finding("affix_count", ILLEGAL, f"{held} {side.lower()}es, the limit here is {limit}"))
            groups: dict[str, list[str]] = {}
            for row, _, text in reading:
                if not converted(row):
                    groups.setdefault(row.group, []).append("; ".join(text))
            for group, held in groups.items():
                if len(held) > 1:
                    out.append(Finding("mod_group", ILLEGAL, f"{len(held)} modifiers of group {group}: {' / '.join(held)}"))
            alloy = sum(1 for row, _, _ in reading if row.kind in ("alloy", "crafted"))
            if alloy > crafted_limit:
                need = "" if ASTRID in runes else f", and no {ASTRID}"
                out.append(Finding("crafted_limit", ILLEGAL, f"{alloy} crafted lines, the limit here is {crafted_limit}{need}"))
            for row, _, text in reading:
                if row.kind != "natural" or converted(row) or influenced:
                    continue
                by = [o.id for o, _, _ in reading if o is not row and o.adds & row.blocked]
                if by:
                    out.append(Finding("tag_conflict", ILLEGAL, f'"{"; ".join(text)}" ({row.id}) cannot roll beside {by[0]}'))
            desecrated = sum(1 for row, _, _ in reading if row.kind == "desecrated")
            if desecrated > 1:
                out.append(Finding("desecrated_limit", ILLEGAL, f"{desecrated} Desecrated modifiers"))
            for row, state, text in reading:
                if state is False and not converted(row) and not influenced:
                    out.append(Finding("base_allowed", ILLEGAL, f'"{"; ".join(text)}" ({row.id}) on {item.get("base")}'))
            return out

        best: tuple[int, list[Finding], list, list[str]] | None = None  # (unread lines, breaks, the reading, unread)
        tried = 0

        def walk(at: int, reading: list, unread: list[str]) -> bool:
            nonlocal best, tried
            if at >= len(lines):
                tried += 1
                found = breaks(reading)
                if best is None or (len(found), len(unread)) < (len(best[1]), best[0]):
                    best = (len(unread), found, list(reading), list(unread))
                return (not found and not unread) or tried >= MAX_READINGS
            if not choices[at]:  # (no row fits this line, alone or as a hybrid's first line)
                return walk(at + 1, reading, unread + [lines[at]])
            for size, options in choices[at].items():
                for row, state in options:
                    reading.append((row, state, lines[at:at + size]))
                    done = walk(at + size, reading, unread)
                    reading.pop()
                    if done:
                        return True
            return False

        walk(0, [], [])
        if best is None:
            return findings
        findings += [Finding("unread", UNVERIFIED, f'"{line}"') for line in best[3]]
        findings += best[1]
        reading = best[2]
        if not any(f.status == ILLEGAL and f.rule == "mod_group" for f in best[1]):
            by_family: dict[int, list[Row]] = {}
            for row, _, _ in reading:
                for family in row.families:
                    by_family.setdefault(family, []).append(row)
            for family, rows in by_family.items():
                if len({r.group for r in rows}) > 1 and not any(converted(r) for r in rows):
                    findings.append(Finding("game_family", UNVERIFIED,
                                            f"game family {family}: {', '.join(sorted(r.id for r in rows))}"))
        kinds = [row.kind for row, _, _ in reading]
        if "essence" in kinds and len([k for k in kinds if k in ("essence", "alloy", "crafted")]) > crafted_limit:
            findings.append(Finding("essence_crafted", UNVERIFIED, "an Essence line beside another crafted line"))
        return findings


TAG = re.compile(r"^(?:\{[^}]*\})+")
TAIL = ("Corrupted", "Sanctified", "Mirrored", "Sockets:", "Rune:", "Catalyst:", "CatalystQuality:")


def item_from_text(raw: str, using: Checker | None = None) -> dict:
    """A gear entry (ItemOption.describe's fields) read back from an item text (rares.rare_text / with_runes): what a
    route scored, so a test can check every item a route considers, not only the one it keeps."""
    lines = [TAG.sub("", line) for line in raw.splitlines()]
    rarity = lines[0].split(":", 1)[1].strip() if lines and lines[0].startswith("Rarity:") else "Rare"
    base = lines[2] if rarity in ("Rare", "Unique") and len(lines) > 2 else lines[1] if len(lines) > 1 else ""
    kinds = {b.get("type") for b in (using or checker()).bases.get(base, [])}
    at = next((i for i, l in enumerate(lines) if l.startswith("Implicits:")), None)
    affixes = []
    if at is not None:
        start = at + 1 + int(lines[at].split(":", 1)[1])
        affixes = [l for l in lines[start:] if l.strip() and not l.startswith(TAIL)]
    return {"name": f"{rarity} item", "base": base, "type": next(iter(kinds), None), "unique": rarity == "Unique",
            "rare": rarity in ("Rare", "Magic"), "affixes": affixes,
            "runes": [l.split(":", 1)[1].strip() for l in lines if l.startswith("Rune:")],
            "corrected": True, "corrupted": any(l.strip() == "Corrupted" for l in lines)}


_checker: Checker | None = None


def checker() -> Checker:
    global _checker
    if _checker is None:
        _checker = Checker()
    return _checker


def check_gear(gear: dict | None, using: Checker | None = None) -> dict:
    """{"rares": how many were read, "illegal_rares": how many break a verified rule, "findings": one row per break}."""
    using = using or checker()
    rows, rares, illegal = [], 0, 0
    for slot, item in (gear or {}).items():
        if not isinstance(item, dict) or item.get("unique") or not item.get("rare"):
            continue
        rares += 1
        found = using.check(item)
        illegal += any(f.status == ILLEGAL for f in found)
        rows += [{"slot": slot, "base": item.get("base"), "rule": f.rule, "status": f.status, "detail": f.detail}
                 for f in found]
    return {"rares": rares, "illegal_rares": illegal, "findings": rows}
