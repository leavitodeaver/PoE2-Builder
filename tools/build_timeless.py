"""Builds builder/data/timeless.json from the game's exported timeless tables (TIMELESS1).

python tools/build_timeless.py [tables folder]    (default: the newest data/timeless-*/tables or data/game-*/tables
                                                    holding AlternatePassiveSkills.json)

What a Timeless jewel does to each conquered passive, for the two PoE2 jewels (AlternateTreeVersions 6 Kalguuran =
Heroic Tragedy, 7 Abyss = Undying Hate; the other five versions are PoE1 jewels with no PoE2 item):
- keystones by ConquerorIndex (the jewel's conqueror name; Mods local_unique_jewel_alternate_tree_keystone);
- the notables a conquered notable can become, with their spawn weights and fixed stat values (min = max in every
  row, checked here: a row with min < max stops the build, since a rolled value would need the seed draw too);
- the small-passive replacement and the attribute-passive addition;
- Undying Hate's desecrated Historic mods (AlternateTreeExtraAdditionsFromJewelStats, ...PassiveOverridesFromJewelStats).
Each entry carries PoB's legion node id (Data/TimelessJewelData/LegionPassives.lua uses the game's Ids), and the build
checks PoB's stat values against the game's: a mismatch stops it (the game decides; fix the bridge's lines first).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "builder" / "data" / "timeless.json"
LEGION = ROOT / "vendor" / "PathOfBuilding-PoE2" / "src" / "Data" / "TimelessJewelData" / "LegionPassives.lua"
JEWELS = {6: ("Heroic Tragedy", "kalguur", "UniqueJewelAlternateTreeInRadiusKalguur"),
          7: ("Undying Hate", "abyss", "UniqueJewelAlternateTreeInRadiusAbyssal")}
SMALL_ATTRIBUTE, SMALL_NORMAL, NOTABLE, KEYSTONE = 1, 2, 3, 4


def tables_folder(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    for pattern in ("timeless-*", "game-*"):
        found = sorted(p for p in (ROOT / "data").glob(f"{pattern}/tables") if (p / "AlternatePassiveSkills.json").exists())
        if found:
            return found[-1]
    raise SystemExit("no exported AlternatePassiveSkills.json under data/ (python -m builder.data.gamefiles)")


def pob_values() -> dict[str, dict[str, tuple[float, float]]]:
    """PoB's legion node id -> {stat: (min, max)} (read from LegionPassives.lua's text; MIT)."""
    text = LEGION.read_text(encoding="utf-8")
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for block in re.split(r'\n\t\t\{\n', text):
        ident = re.search(r'\["id"\] = "([^"]+)"', block)
        if not ident:
            continue
        stats = {}
        for name, body in re.findall(r'\["([^"]+)"\] = \{\n((?:\t{5}\[.*\n)+)\t{4}\}', block):
            low = re.search(r'\["min"\] = (-?[\d.]+)', body)
            high = re.search(r'\["max"\] = (-?[\d.]+)', body)
            if low and high:
                stats[name] = (float(low.group(1)), float(high.group(1)))
        out[ident.group(1)] = stats
    return out


def build(folder: Path) -> dict:
    load = lambda name: json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
    stats = [s["Id"] for s in load("Stats")]
    versions = load("AlternateTreeVersions")
    skills = load("AlternatePassiveSkills")
    additions = load("AlternatePassiveAdditions")
    mods = {m["Id"]: m for m in load("Mods")}
    passives = load("PassiveSkills")
    pob = pob_values()
    problems: list[str] = []

    def values(row: dict, count: int) -> dict[str, float]:
        out = {}
        for i, stat in enumerate(row["Stats"][:count]):
            low, high = row[f"Stat{i + 1}"]
            if low != high:
                problems.append(f"{row['Id']}: {stats[stat]} rolls {low}-{high} (a seed-rolled value: not modelled)")
            out[stats[stat]] = low
        return out

    def check(row_id: str, game: dict[str, float]) -> None:
        mine = pob.get(row_id)
        if mine is None:
            problems.append(f"{row_id}: not in PoB's LegionPassives")
            return
        for stat, value in game.items():
            # PoB stores the shown number: per-minute stats per second, permyriad as %, "reduced" as a positive value
            shown = abs(value / 60 if "per_minute" in stat else value / 100 if "permyriad" in stat else value)
            if stat not in mine or mine[stat] != (shown, shown):
                problems.append(f"{row_id}: {stat} game {value}, PoB {mine.get(stat)}")

    out: dict = {"source": f"game tables {folder.parent.name} (TIMELESS1; tools/build_timeless.py)", "jewels": {}}
    for version, (name, conqueror, mod_id) in JEWELS.items():
        tree = versions[version]
        mod = mods[mod_id]
        seed = mod["Stat2Value"]
        rows = [r for r in skills if r["AlternateTreeVersion"] == version]
        entry = {
            "name": name, "conqueror": conqueror, "version": version, "seed": seed,
            "small_attribute_replaced": tree["SmallAttributeReplaced"],
            "small_normal_replaced": tree["SmallNormalPassiveReplaced"],
            "notable_replacement_weight": tree["NotableReplacementSpawnWeight"],
            "additions": {"small_attribute": tree["SmallAttributePassiveSkillAdditions"],
                          "small_normal": tree["SmallNormalPassiveSkillAdditions"],
                          "notable": tree["NotableAdditions"]},
            "keystones": {}, "notables": [], "small": [], "attribute_additions": [],
        }
        for r in rows:
            row_stats = values(r, 6)
            if KEYSTONE in r["PassiveType"]:
                entry["keystones"][str(r["ConquerorIndex"])] = {"id": r["Id"], "name": r["Name"]}
            elif NOTABLE in r["PassiveType"]:
                entry["notables"].append({"id": r["Id"], "name": r["Name"], "weight": r["SpawnWeight"],
                                          "stats": row_stats})
                check(r["Id"], row_stats)
            elif SMALL_NORMAL in r["PassiveType"] or SMALL_ATTRIBUTE in r["PassiveType"]:
                entry["small"].append({"id": r["Id"], "name": r["Name"], "weight": r["SpawnWeight"],
                                       "types": r["PassiveType"], "stats": row_stats})
                check(r["Id"], row_stats)
        for r in additions:
            if r["AlternateTreeVersion"] == version:
                entry["attribute_additions" if r["PassiveType"] == [SMALL_ATTRIBUTE] else "small"].append(
                    {"id": r["Id"], "weight": r["SpawnWeight"], "types": r["PassiveType"], "stats": values(r, 3)})
        out["jewels"][str(version)] = entry
    out["historic_additions"] = [
        {"jewel_stat": stats[r["Id"]], "stat": stats[r["Stat"]], "node_type": {1: "attribute", 2: "small"}[r["NodeType"]],
         "version": r["JewelType"]}
        for r in load("AlternateTreeExtraAdditionsFromJewelStats")]
    out["historic_overrides"] = [
        {"id": r["Id"], "passive": passives[r["PassiveSkill"]]["Id"], "name": passives[r["PassiveSkill"]]["Name"]}
        for r in load("AlternateTreeExtraPassiveOverridesFromJewelStats")]
    if problems:
        raise SystemExit("timeless data problems (nothing written):\n  " + "\n  ".join(problems))
    return out


def main(argv: list[str]) -> None:
    data = build(tables_folder(argv[0] if argv else None))
    # Real outcomes read from the game (a character's skill_overrides) are kept across rebuilds: the tables can't give them
    data["observed"] = json.loads(OUT.read_text(encoding="utf-8")).get("observed", []) if OUT.exists() else []
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    counts = {v["name"]: len(v["notables"]) for v in data["jewels"].values()}
    print(f"wrote {OUT.relative_to(ROOT)}: notables {counts}, {len(data['historic_additions'])} Historic additions, "
          f"{len(data['historic_overrides'])} Historic overrides; PoB's legion values agree with the game's")


if __name__ == "__main__":
    main(sys.argv[1:])
