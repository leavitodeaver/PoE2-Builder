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
from builder.search.rares import (_ALDUR_ADDS, _ALDUR_ALLIES, _ALDUR_SAME, ALDUR_AILMENT_TEXT, ALDUR_RUNES, NOT_IN_SEALED,
                                  excluded_by, exclusion_group, mod_lines, pool_tags)

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
                     "local_can_have_additional_crafted_mods). Astrid's isn't socket-bound (AUDIT38 F2), so two crafted "
                     "lines without it socketed are UNVERIFIED on an item of unknown history, ILLEGAL on one the search "
                     "built without it in its route",
    "essence_crafted": "UNVERIFIED: an Essence line beside another crafted line (whether a Perfect / Corrupted Essence's line "
                       "counts as crafted is open, RQ-F)",
    "desecrated_limit": "One Desecrated modifier per item (0.5.0: 'items are limited to 1 Desecrated modifier'); on a "
                        "Corrupted item of unknown history UNVERIFIED (AUDIT38 F4: an Omen of Putrefaction reveal makes up "
                        "to 6 and corrupts the item; poe2db)",
    "base_allowed": "The modifier can appear on this base: its spawn weights for the base's tags (PoB ModItem, from the "
                    "game's Mods), or the item classes of its Alloy (alloys.ALLOYS) or Perfect Essence (essences.ESSENCES)",
    "sealed_rune": "No augment the game bars from sealed items (SoulCores CanSocketInCorruptedSanctified false: Serle's, "
                   "Astrid's, the influence runes, the Aldur runes, Cadigan's, the Raven-Touched Shard) socketed AFTER "
                   "the item was Corrupted or Sanctified (0.5.4: they 'can no longer be socketed into' such items; one "
                   "socketed before stays, as on the user's listed Twice Corrupted Chiming Staff with Passion of Aldur, "
                   "24 Nov). ILLEGAL only where the item's route socketed it after its Vaal Orb; an item read without its "
                   "route order (imports, saved results, item texts) is UNVERIFIED",
    "unread": "UNVERIFIED: a line that fits no mod row in the tables read here, so nothing is claimed about it",
    "jewel_shape": "UNVERIFIED: a crafted jewel over 2 + 2 within 6 modifiers and 4 a side (AUDIT38 F9: a '+1 allowed' "
                   "craft may have stood where the crafted line is, and a Desecration above it; 5 listings, MEDIUM-LOW; "
                   "no source states a jewel's limits). Over that: affix_count ILLEGAL",
}

NUMBER = re.compile(r"\(-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?\)|-?\d+(?:\.\d+)?")
ALLOWED = re.compile(r"^([+-]\d+) (Prefix|Suffix) Modifiers? allowed\s*$", re.M)
JEWEL_EFFECT = re.compile(r"increased Effect of (Prefix|Suffix)es", re.I)
RESIST_GROUP = re.compile(r"resist", re.I)


def aldur_shape(line: str, target: str) -> bool:
    """Whether `line` has a shape an Aldur rune of `target` turns lines into (rares.aldur_line's families: extra damage,
    increased damage, spell levels, penetration, added damage, allies' added damage, the element's ailment line) and
    names `target`: such a line may have rolled as another element's. Any other line is as it rolled (an Explicit Fire
    Modifier magnitudes line is a Thrud's Might mod, never an Aldur output)."""
    if target in ALDUR_AILMENT_TEXT and ALDUR_AILMENT_TEXT[target] in line:
        return True
    if not re.search(rf"\b{target}\b", line):
        return False
    probe = re.sub(rf"\b{target}\b", "Cold" if target == "Fire" else "Fire", line)
    return bool(_ALDUR_ADDS.match(probe) or _ALDUR_ALLIES.match(probe) or any(p.match(probe) for _, p in _ALDUR_SAME))
JEWEL_MOST = 6  # (AUDIT38 F9: the most modifiers a crafted jewel route makes; MEDIUM-LOW)
JEWEL_SIDE_MOST = 4
PRUNE = True  # (False: every reading tried, as before SEARCHC5; the test compares the two)
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
    """A line's key, numbers as '#', letter case ignored (the trade site writes "Modifier Magnitudes", the game's text
    "Modifier magnitudes")."""
    return NUMBER.sub("#", line.strip()).lower()


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
        if row.kind == "essence":  # (AUDIT38 F8: the Essence table's own class names, "Warstaff" for a Quarterstaff)
            kinds = {essences.item_class(b) for b in self.bases.get(item.get("base"), [])}
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
        if "Sockets (corrupted)" in route:  # (the route's own order: its bound runes in before the Vaal Orb, the rest after)
            bound = list(item.get("bound_runes") or ())
            after = [n for n in list(item.get("runes") or ())[len(bound):] if n not in bound]
            for name in sorted(set(after) & NOT_IN_SEALED):
                findings.append(Finding("sealed_rune", ILLEGAL, f"{name} socketed after the route's Vaal Orb"))
        elif item.get("corrupted") or item.get("sanctified"):
            for name in sorted(runes & NOT_IN_SEALED):
                findings.append(Finding("sealed_rune", UNVERIFIED,
                                        f"{name} on a {'Corrupted' if item.get('corrupted') else 'Sanctified'} item: "
                                        "legal if it was socketed before (0.5.4 'can no longer be socketed into'); "
                                        "this item's order isn't known"))
        fits = self.fits(lines, item)
        limits = self.limits(item, lines)
        aldur = {element for element, name in ALDUR_RUNES.items() if name in runes}  # (the elements its runes turn lines to)
        flux = "Flux" in route
        influenced = bool(runes & INFLUENCE_RUNES)
        crafted_limit = 2 if ASTRID in runes or ASTRID in route else 1
        # (SEARCHC5) the search wrote this entry (ItemOption.describe always gives "price"), so its route says everything
        # done to it; an item read from elsewhere (an import, a trade listing, an item text) has no known history
        known = "price" in item
        crafted_jewel = item.get("type") == "Jewel"
        tag_sets = self.tags(item)
        choices = [{size: self.distinct(rows, lambda r: self.allowed(r, item, tag_sets)) for size, rows in here.items()}
                   for here in fits]

        shaped: dict[tuple[str, ...], bool] = {}  # (SEARCHC5, test cost: each line's Aldur reading once per item)

        def converted(row: Row, text: list[str] | None = None) -> bool:
            """A line a route may have rewritten, so its group and spawn weights no longer bind it: under an Aldur rune a
            line naming the rune's element (what it turns lines into; a line naming none is as it rolled), under a Flux
            a resistance."""
            if flux and RESIST_GROUP.search(row.group):
                return True
            if not aldur:
                return False
            key = tuple(text or ())
            if key not in shaped:
                shaped[key] = any(aldur_shape(line, e) for line in key for e in aldur)
            return shaped[key]

        def breaks(reading: list[tuple[Row, bool | None, list[str]]]) -> list[Finding]:
            out: list[Finding] = []
            # (AUDIT38 F9) a crafted jewel's sides can't be judged from the finished jewel: a "+1 Prefix / Suffix allowed"
            # craft may have stood in the crafted line's place, and a Desecration may sit above it. ILLEGAL only past what
            # no route makes (over 6 modifiers, or a side over 4), UNVERIFIED in between (MEDIUM-LOW: 5 listings)
            shaped_jewel = crafted_jewel and any(row.kind == "crafted" for row, _, _ in reading)
            for side, limit in limits.items():
                held = sum(1 for row, _, _ in reading if row.type == side)
                if held > limit:
                    if shaped_jewel and held <= JEWEL_SIDE_MOST and len(reading) <= JEWEL_MOST:
                        out.append(Finding("jewel_shape", UNVERIFIED, f"{held} {side.lower()}es beside a crafted jewel "
                                           f"line (a +1 craft may have stood in its place; the plain limit is {limit})"))
                    else:
                        out.append(Finding("affix_count", ILLEGAL, f"{held} {side.lower()}es, the limit here is {limit}"))
            groups: dict[str, list[str]] = {}
            for row, _, text in reading:
                if not converted(row, text):
                    groups.setdefault(row.group, []).append("; ".join(text))
            for group, held in groups.items():
                if len(held) > 1:
                    out.append(Finding("mod_group", ILLEGAL, f"{len(held)} modifiers of group {group}: {' / '.join(held)}"))
            alloy = sum(1 for row, _, _ in reading if row.kind in ("alloy", "crafted"))
            if alloy == 2 and crafted_limit == 1 and not known:  # (AUDIT38 F2: Astrid's may have been replaced since)
                out.append(Finding("crafted_limit", UNVERIFIED, f"2 crafted lines and no {ASTRID} socketed now: it isn't "
                                   "socket-bound (SoulCores IsSocketBound false; poe2db 'can be replaced'), so it may have "
                                   "been socketed and replaced"))
            elif alloy > crafted_limit:
                need = "" if crafted_limit > 1 else f", and no {ASTRID}"
                out.append(Finding("crafted_limit", ILLEGAL, f"{alloy} crafted lines, the limit here is {crafted_limit}{need}"))
            for row, _, text in reading:
                if row.kind != "natural" or converted(row, text) or influenced:
                    continue
                by = [o.id for o, _, t in reading if o is not row and o.adds & row.blocked and not converted(o, t)]
                if by:
                    out.append(Finding("tag_conflict", ILLEGAL, f'"{"; ".join(text)}" ({row.id}) cannot roll beside {by[0]}'))
            desecrated = sum(1 for row, _, _ in reading if row.kind == "desecrated")
            if desecrated > 1 and item.get("corrupted") and not known:  # (AUDIT38 F4: Omen of Putrefaction)
                out.append(Finding("desecrated_limit", UNVERIFIED, f"{desecrated} Desecrated modifiers on a Corrupted item "
                                   "(an Omen of Putrefaction reveal makes up to 6; none listed with two lord rows)"))
            elif desecrated > 1:
                out.append(Finding("desecrated_limit", ILLEGAL, f"{desecrated} Desecrated modifiers"))
            for row, state, text in reading:
                if state is False and not converted(row, text) and not influenced:
                    out.append(Finding("base_allowed", ILLEGAL, f'"{"; ".join(text)}" ({row.id}) on {item.get("base")}'))
            return out

        best: tuple[tuple[int, int, int], list[Finding], list, list[str]] | None = None  # (rank, breaks, reading, unread)
        tried = 0
        # (AUDIT38 F3) the best reading reads the most lines, then breaks the fewest rules: a hybrid's second line is
        # never left unread to dodge a break (before, fewest breaks came first, and a real break could read UNVERIFIED).
        # (SEARCHC5, test cost) so only readings leaving the fewest lines unread are tried: `least` is that number from
        # each position (a line no row fits, alone or as a hybrid's first line, is unread in every reading through it).
        # Before, an item with one unreadable line tried every reading (up to MAX_READINGS), ~45 ms an item: 17,883
        # route-test items took 799 s (POE2T7656).
        least = [0] * (len(lines) + 1)
        for at in range(len(lines) - 1, -1, -1):
            least[at] = min((least[at + size] for size in choices[at]), default=1 + least[at + 1])
        cap = least[0] if PRUNE else len(lines)

        def walk(at: int, reading: list, unread: list[str]) -> bool:
            nonlocal best, tried
            if len(unread) + least[at] > cap:
                return False
            if at >= len(lines):
                tried += 1
                found = breaks(reading)
                rank = (len(unread), sum(f.status == ILLEGAL for f in found), len(found))
                if best is None or rank < best[0]:
                    best = (rank, found, list(reading), list(unread))
                return rank[:2] == (least[0], 0) and not found or tried >= MAX_READINGS
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
            by_family: dict[int, list[tuple[Row, list[str]]]] = {}
            for row, _, text in reading:
                for family in row.families:
                    by_family.setdefault(family, []).append((row, text))
            for family, held in by_family.items():
                rows = [r for r, _ in held]
                if len({r.group for r in rows}) > 1 and not any(converted(r, t) for r, t in held):
                    findings.append(Finding("game_family", UNVERIFIED,
                                            f"game family {family}: {', '.join(sorted(r.id for r in rows))}"))
        kinds = [row.kind for row, _, _ in reading]
        if "essence" in kinds and len([k for k in kinds if k in ("essence", "alloy", "crafted")]) > crafted_limit:
            findings.append(Finding("essence_crafted", UNVERIFIED, "an Essence line beside another crafted line"))
        return findings


def reading_key(item: dict) -> tuple:
    """Everything Checker.check reads from an item, with the rolled numbers left out: two items with one key break the
    same rules (only the numbers quoted in a finding's detail differ). The route test checks one item per key."""
    lines = list(item.get("affixes") or ())
    runes = tuple(item.get("runes") or ())
    route = " ".join(item.get("craft_route") or ())
    targets = [e for e, name in ALDUR_RUNES.items() if name in runes or name in (item.get("bound_runes") or ())]
    return (bool(item.get("unique")), bool(item.get("rare")), item.get("base"), item.get("type"),
            str(item.get("name") or "").startswith("Magic"), tuple(NUMBER.sub("#", line.strip()) for line in lines),
            tuple(aldur_shape(line, e) for line in lines for e in targets), runes, tuple(item.get("bound_runes") or ()),
            "Sockets (corrupted)" in route, "Flux" in route, bool(item.get("corrupted")), bool(item.get("sanctified")))


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
            "corrected": True, "corrupted": any(l.strip() == "Corrupted" for l in lines),
            "sanctified": any(l.strip() == "Sanctified" for l in lines)}


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
