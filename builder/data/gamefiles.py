"""Read-only datamining of the installed game: `python -m builder.data.gamefiles [--analyse-only]`.

Exports the data tables and stat description files the builder needs from the Steam install's Bundles2 into
data/game-<steam build id>/ (tables/*.json, files/...), then marks what the current game can still reach
(live.json; the rules are in builder.data.liveness). The game folder is only ever read: the exporter is
tools/datamine/export.mjs (SnosMe's pathofexile-dat on a portable Node.js under tools/node/), whose loader opens
bundle files with fs.readFile, and nothing runs while Steam is updating the game. Table layouts come from the
community dat-schema (poe-tool-dev), pinned per build in schema.min.json; a table whose schema is wider than the
file's rows is reported and left out rather than misread.

Set-up, once (approved 7 Nov 2026, everything inside the project): Node.js from nodejs.org unpacked under
tools/node/, and `npm install` in tools/datamine/ (package.json there pins pathofexile-dat).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAME = Path(os.environ.get("POE2_PATH", r"C:\Program Files (x86)\Steam\steamapps\common\Path of Exile 2"))
APP_ID = 2694490
SCHEMA_URL = "https://github.com/poe-tool-dev/dat-schema/releases/download/latest/schema.min.json"
EXPORTER = ROOT / "tools" / "datamine" / "export.mjs"

TABLES = [
    # gems and skills
    "SkillGems", "UncutGems", "GemEffects", "GemTags", "GrantedEffects", "GrantedEffectsPerLevel",
    "GrantedEffectStatSets", "GrantedEffectStatSetsPerLevel", "GrantedEffectQualityStats", "ActiveSkills",
    "ActiveSkillType", "IndexableSupportGems", "SupportGems", "SkillGemSupports", "SupportGemFamily",
    "Expedition2Runes", "Expedition2RemnantRewardPerArea", "Expedition2Recipes", "QuestRewards", "QuestRewardType", "ArchetypeRewards",
    "ItemInherentSkills", "ModGrantedSkills", "CharacterShapeShiftBasicSkills", "CharacterMeleeSkills",
    # stats, mods, keywords
    "Stats", "Tags", "Mods", "ModType", "ModFamily", "KeywordPopups", "Essences", "EssenceMods",
    # items
    "BaseItemTypes", "ItemClasses", "ItemClassCategories", "ArmourTypes", "WeaponTypes", "ShieldTypes", "Flasks",
    "UtilityFlaskBuffs", "Words", "UniqueStashLayout", "UniqueStashTypes",
    "SoulCores", "SoulCoreStats", "SoulCoreStatCategories", "SoulCoreTypes", "SoulCoreLimits",
    # anoints ("instilling"): which Liquid Emotions allocate which notable
    "BlightCraftingRecipes", "BlightCraftingResults", "BlightCraftingItems", "BlightCraftingTypes",
    # passive tree and classes
    "PassiveSkills", "PassiveSkillTrees", "Ascendancy", "Characters", "PassiveJewelSlots",
    "ClassPassiveSkillOverrides", "AscendancyPassiveSkillOverrides",
]
FILES = ["Metadata/PassiveSkillGraph.psg"]
FILE_DIRS = ["data/statdescriptions"]  # every stat description file, found by listing the index


def steam_build() -> tuple[str, str]:
    """(build id, state flags) from Steam's app manifest; flags 4 = fully installed, anything else = updating."""
    manifest = GAME.parents[1] / f"appmanifest_{APP_ID}.acf"
    text = manifest.read_text(encoding="utf-8", errors="replace")
    build = re.search(r'"buildid"\s+"(\d+)"', text)
    flags = re.search(r'"StateFlags"\s+"(\d+)"', text)
    if not build:
        raise SystemExit(f"no build id in {manifest}")
    return build.group(1), flags.group(1) if flags else "?"


def node() -> Path:
    found = sorted((ROOT / "tools" / "node").glob("node-*/node.exe"))
    if not found:
        raise SystemExit("no portable Node.js under tools/node/ (see this module's docstring)")
    return found[-1]


def export(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    schema = out / "schema.min.json"
    if not schema.exists():
        urllib.request.urlretrieve(SCHEMA_URL, schema)
    request = out / "export-request.json"

    def run(body: dict) -> None:
        request.write_text(json.dumps(body), encoding="utf-8")
        subprocess.run([str(node()), str(EXPORTER), str(request)], stdin=subprocess.DEVNULL, check=True, cwd=ROOT)

    base = {"game": str(GAME), "schema": str(schema), "out": str(out)}
    run({**base, "tables": [], "files": [], "list": FILE_DIRS})
    listed = json.loads((out / "export-report.json").read_text(encoding="utf-8"))["lists"]
    files = FILES + sorted(f for d in FILE_DIRS for f in listed[d] if f.endswith(".csd"))
    run({**base, "tables": TABLES, "files": files, "list": []})
    return json.loads((out / "export-report.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--analyse-only", action="store_true", help="reuse the exported tables; only redo live.json")
    args = parser.parse_args(argv)
    build, flags = steam_build()
    if flags != "4":
        raise SystemExit(f"Steam is updating the game (StateFlags {flags}); try again when it's done")
    out = ROOT / "data" / f"game-{build}"
    if not args.analyse_only:
        report = export(out)
        bad = {name: entry["error"] for name, entry in report["tables"].items() if entry.get("error")}
        print(f"build {build}: {len(report['tables']) - len(bad)} tables, {len(report['files'])} files exported")
        for name, error in bad.items():
            print(f"  not exported: {name}: {error}")
    from builder.data import liveness

    summary = liveness.write(out)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    sys.exit(main())
