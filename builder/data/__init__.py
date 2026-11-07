"""Game data exported from the pinned PoB, plus helpers to read it.

`dump()` boots an engine and writes one JSON file per part under data/pob-<commit>/; `GameData`
loads those files lazily. Nothing is parsed out of PoB's Lua sources; the data comes from PoB's
own loaded tables, so a PoB upgrade only needs a new dump.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any

from builder import ENGINE_DIR, ROOT

PARTS = ["gems", "tree", "bases", "uniques", "mods", "runes"]
DATA_ROOT = ROOT / "data"

WEAPON_TYPES = {
    "Bow", "Claw", "Crossbow", "Dagger", "Flail", "One Hand Axe", "One Hand Mace", "One Hand Sword",
    "Sceptre", "Spear", "Staff", "Talisman", "Two Hand Axe", "Two Hand Mace", "Two Hand Sword", "Wand",
}
OFFHAND_TYPES = {"Shield", "Focus", "Quiver"}


def pin() -> dict:
    return json.loads((ENGINE_DIR / "pob-pin.json").read_text(encoding="utf-8"))


def data_dir() -> Path:
    return DATA_ROOT / f"pob-{pin()['commit'][:10]}"


def dump(target: Path | None = None) -> Path:
    """Export every part from a fresh engine (run in a worker process: engines change the cwd)."""
    from builder.pob.pool import EnginePool

    target = target or data_dir()
    target.mkdir(parents=True, exist_ok=True)
    counts = {}
    with EnginePool(1) as pool:
        for part in PARTS:
            started = time.perf_counter()
            payload = pool.broadcast("dumpData", part=part)[0]
            (target / f"{part}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            counts[part] = {key: len(value) for key, value in payload.items() if isinstance(value, (list, dict))}
            print(f"{part}: {counts[part]} in {time.perf_counter() - started:.1f}s", flush=True)
        pool.broadcast("newBuild")
        gaps = pool.broadcast("auditUnsupported")[0]
        (target / "gaps.json").write_text(json.dumps(gaps, ensure_ascii=False), encoding="utf-8")
        counts["gaps"] = {key: len(value) for key, value in gaps.items()}
        print(f"gaps: {counts['gaps']} lines PoB can't calculate", flush=True)
        counts["coverage"] = dump_coverage(target, pool)
    manifest = {
        "pob": pin(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def dump_coverage(target: Path | None = None, pool=None) -> dict:
    """PoB's side of the coverage audit (bridge `auditCoverage`, builder/ledger/coverage.py): every
    affix and rune line through the parser, every gem stat against its stat map, and the modifier
    names each parsed line produces. One engine, about a minute."""
    from builder.pob.pool import EnginePool

    target = target or data_dir()
    if pool is None:
        with EnginePool(1) as own:
            return dump_coverage(target, own)
    pool.broadcast("newBuild")
    payload = pool.broadcast("auditCoverage")[0]
    # Unique lines of variants the item's own parse doesn't select (Megalomaniac's notables, Grip of Kulemak's
    # Abyssal lines ...), through the same parser; old "Pre 0.x" variants are left out
    missing = unique_variant_lines(target, payload)
    results = pool.broadcast("auditLines", lines=[m["line"] for m in missing])[0]["results"] if missing else []
    payload["unique_variants"] = [{**m, **r} for m, r in zip(missing, results)]
    # audit item (e): each active skill alone on a plain character
    payload["skill_sweep"] = pool.broadcast("auditSkills")[0]["skills"]
    (target / "coverage.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    counts = {key: len(value) for key, value in payload.items()}
    print(f"coverage: {counts}", flush=True)
    return counts


def unique_variant_lines(target: Path, payload: dict) -> list[dict]:
    """Every current-variant line of every unique (uniques.json) that the audit's item parse didn't see."""
    import re

    seen: dict[str, set[str]] = {}
    for line in payload.get("uniques", []):
        seen.setdefault(line["unique"], set()).add(line["line"])
    gaps = json.loads((target / "gaps.json").read_text(encoding="utf-8")) if (target / "gaps.json").exists() else {}
    for line in gaps.get("uniques", []):
        seen.setdefault(line["unique"], set()).add(line["line"])
    found = []
    for item in json.loads((target / "uniques.json").read_text(encoding="utf-8"))["uniques"]:
        lines = item["raw"].splitlines()
        variants = [line[9:] for line in lines if line.startswith("Variant: ")]
        start = next((i + 1 for i, line in enumerate(lines) if line.startswith("Implicits:")), None)
        if start is None:  # (flasks and charms: no Implicits line; the lines start after the header)
            header = re.compile(r"^(Rarity|Variant|Selected Variant|Base Variant|Selected Base Variant|Requires|League|Source|"
                                r"Quality|Sockets|LevelReq|Radius|Limited|Item Level)\b|^\{base:")
            start = max([i + 1 for i, line in enumerate(lines) if header.search(line)] + [2])
        for raw in lines[start:]:
            m = re.match(r"^((?:\{[^}]*\})*)(.*)$", raw)
            text = m.group(2).strip()
            tag = re.search(r"\{variant:([\d,]+)\}", m.group(1))
            names = [variants[int(v) - 1] if 0 < int(v) <= len(variants) else "?" for v in tag.group(1).split(",")] if tag else []
            if not text or (names and all(n.startswith("Pre ") for n in names)) or text in seen.get(item["name"], ()):
                continue
            found.append({"unique": item["name"], "line": text, "variants": names})
    return found


class GameData:
    """Read-only view of a data dump. Parts load on first use."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or data_dir()
        if not (self.dir / "manifest.json").exists():
            raise FileNotFoundError(f"no game data at {self.dir}; run `uv run python -m builder.data dump`")

    def _part(self, name: str) -> Any:
        return json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))

    @cached_property
    def manifest(self) -> dict:
        return self._part("manifest")

    @cached_property
    def gems(self) -> list[dict]:
        return self._part("gems")["gems"]

    @cached_property
    def tree(self) -> dict:
        return self._part("tree")

    @cached_property
    def bases(self) -> list[dict]:
        return self._part("bases")["bases"]

    @cached_property
    def uniques(self) -> list[dict]:
        return self._part("uniques")["uniques"]

    @cached_property
    def runes(self) -> list[dict]:
        return self._part("runes")["runes"]

    @cached_property
    def mods(self) -> dict[str, list[dict]]:
        return self._part("mods")["pools"]

    # Name lists used by the request form and the constraint compiler -------------------------

    @cached_property
    def active_skills(self) -> list[str]:
        return sorted({g["name"] for g in self.gems if not g["support"]})

    @cached_property
    def supports(self) -> list[str]:
        return sorted({g["name"] for g in self.gems if g["support"]})

    @cached_property
    def classes(self) -> dict[str, list[str]]:
        return {c["name"]: [a["name"] for a in c["ascendancies"]] for c in self.tree["classes"]}

    @cached_property
    def ascendancies(self) -> list[str]:
        return sorted(a for names in self.classes.values() for a in names)

    def nodes_of_type(self, node_type: str) -> list[str]:
        return sorted({n["name"] for n in self.tree["nodes"] if n["type"] == node_type and n.get("name") and not n.get("ascendancy")})

    @cached_property
    def keystones(self) -> list[str]:
        return self.nodes_of_type("Keystone")

    @cached_property
    def notables(self) -> list[str]:
        return self.nodes_of_type("Notable")

    @cached_property
    def unique_names(self) -> list[str]:
        return sorted({u["name"] for u in self.uniques})

    @cached_property
    def item_classes(self) -> list[str]:
        return sorted({b["type"] for b in self.bases if not b.get("hidden")})

    @cached_property
    def weapon_types(self) -> list[str]:
        return sorted(t for t in self.item_classes if t in WEAPON_TYPES)

    @cached_property
    def augments(self) -> list[str]:
        return sorted(r["name"] for r in self.runes)
