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
    manifest = {
        "pob": pin(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


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
