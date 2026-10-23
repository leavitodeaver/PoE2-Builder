"""Generate representative test builds for the engine gates and benchmarks.

Each build gets a class, ascendancy, level, main skill with supports, rare gear, and a passive
tree filled greedily with PoB delta evaluations (damage per point, with some weight on life so
trees aren't pure glass). The trees are not meant to be good, only realistic in size and mix.

Usage: uv run python benchmarks/make_test_builds.py [name ...]
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from builder import ROOT
from builder.pob.engine import PobEngine

OUT_DIR = ROOT / "benchmarks" / "builds"

LIFE_RES_LINES = [
    "+90 to maximum Life",
    "+30% to Fire Resistance",
    "+30% to Cold Resistance",
    "+30% to Lightning Resistance",
]


def rare(name: str, base: str, *lines: str) -> str:
    return "\n".join(["Rarity: Rare", name, base, "Item Level: 82", *lines])


def armour_set(body: str, helmet: str, gloves: str, boots: str) -> list[str]:
    return [
        rare("Test Body", body, "+150 to maximum Life", "+40% to Fire Resistance", "+40% to Cold Resistance"),
        rare("Test Helmet", helmet, "+80 to maximum Life", "+40% to Lightning Resistance", "+25% to Chaos Resistance"),
        rare("Test Gloves", gloves, *LIFE_RES_LINES),
        rare("Test Boots", boots, "30% increased Movement Speed", *LIFE_RES_LINES),
        rare("Test Belt", "Fine Belt", "+100 to maximum Life", "+30% to Fire Resistance", "+40 to Strength"),
        rare("Test Ring", "Biostatic Ring", "+60 to maximum Life", "+30% to Cold Resistance", "+30 to Dexterity"),
        rare("Test Amulet", "Absent Amulet", "+60 to maximum Life", "+30 to all Attributes"),
    ]


BUILDS = {
    "spark_stormweaver": {
        "className": "Sorceress",
        "ascendancy": "Stormweaver",
        "skill": "Spark 20/20 1\nControlled Destruction 1/0 1\nLightning Penetration 1/0 1\nElemental Focus 1/0 1\nChain II 1/0 1",
        "items": [
            rare("Test Wand", "Dueling Wand", "+3 to Level of all Lightning Spell Skills", "90% increased Spell Damage",
                 "25% increased Cast Speed", "60% increased Critical Hit Chance for Spells"),
            *armour_set("Feathered Raiment", "Ancestral Tiara", "Sirenscale Gloves", "Sekhema Sandals"),
        ],
    },
    "lightning_arrow_deadeye": {
        "className": "Ranger",
        "ascendancy": "Deadeye",
        "skill": "Lightning Arrow 20/20 1\nFork 1/0 1\nLightning Penetration 1/0 1\nElemental Focus 1/0 1\nChain II 1/0 1",
        "items": [
            rare("Test Bow", "Gemini Bow", "Adds 20 to 320 Lightning Damage", "110% increased Physical Damage",
                 "+3 to Level of all Projectile Skills", "18% increased Attack Speed"),
            rare("Test Quiver", "Visceral Quiver", "+60 to maximum Life", "30% increased Projectile Damage"),
            *armour_set("Corsair Coat", "Freebooter Cap", "Polished Bracers", "Drakeskin Boots"),
        ],
    },
    "boneshatter_titan": {
        "className": "Warrior",
        "ascendancy": "Titan",
        "skill": "Boneshatter 20/20 1\nBrutality II 1/0 1\nHeavy Swing 1/0 1\nArmour Break II 1/0 1\nClose Combat II 1/0 1",
        "items": [
            rare("Test Maul", "Ruination Maul", "160% increased Physical Damage", "Adds 40 to 70 Physical Damage",
                 "15% increased Attack Speed", "+3 to Level of all Melee Skills"),
            *armour_set("Warlord Cuirass", "Imperial Greathelm", "Massive Mitts", "Tasalian Greaves"),
        ],
    },
    "zombie_infernalist": {
        "className": "Witch",
        "ascendancy": "Infernalist",
        "skill": "Raise Zombie 20/20 1\nMinion Mastery 1/0 1\nHulking Minions 1/0 1\nFeeding Frenzy II 1/0 1",
        "items": [
            rare("Test Sceptre", "Hallowed Sceptre", "+3 to Level of all Minion Skills", "40% increased Minion Damage"),
            *armour_set("Feathered Raiment", "Ancestral Tiara", "Sirenscale Gloves", "Sekhema Sandals"),
        ],
    },
}

FIELDS = ["FullDPS", "CombinedDPS", "Minion.CombinedDPS", "Life", "EnergyShield"]


def score(stats: dict) -> float:
    dps = max(stats.get("FullDPS") or 0, stats.get("CombinedDPS") or 0, stats.get("Minion.CombinedDPS") or 0)
    pool = (stats.get("Life") or 0) + (stats.get("EnergyShield") or 0)
    return math.log(max(dps, 1e-9)) + 0.6 * math.log(max(pool, 1))


def fill_tree(engine: PobEngine, points: int, ascendancy: bool, timing: dict) -> None:
    key = "ascendancyAllocated" if ascendancy else "allocated"
    while engine.call("summary")[key] < points:
        remaining = points - engine.call("summary")[key]
        frontier = engine.call("frontier", maxDistance=min(4, remaining), ascendancy=ascendancy)["nodes"]
        if not frontier:
            break
        base = score(engine.call("nodeDeltas", cases=[{}], fields=FIELDS)["results"][0])
        cases = [{"add": [node["id"]], "addPaths": True} for node in frontier]
        started = time.perf_counter()
        result = engine.call("nodeDeltas", cases=cases, fields=FIELDS)
        timing["delta_wall"] += time.perf_counter() - started
        timing["delta_lua"] += result["seconds"]
        timing["delta_cases"] += len(cases)
        gains = [
            ((score(stats) - base) / node["distance"], node["id"])
            for node, stats in zip(frontier, result["results"])
        ]
        _, best = max(gains)
        engine.call("allocate", add=[best])


def make(engine: PobEngine, name: str, spec: dict) -> Path:
    timing = {"delta_wall": 0.0, "delta_lua": 0.0, "delta_cases": 0}
    started = time.perf_counter()
    engine.call("newBuild")
    engine.call("setClass", className=spec["className"], ascendancy=spec["ascendancy"])
    engine.call("setLevel", level=90)
    engine.call("addSkillGroup", text=spec["skill"], main=True, fullDPS=True)
    for raw in spec["items"]:
        engine.call("addItem", raw=raw)
    fill_tree(engine, 8, ascendancy=True, timing=timing)
    fill_tree(engine, 100, ascendancy=False, timing=timing)
    summary = engine.call("summary")
    stats = engine.call("stats")
    xml = engine.call("exportXml")["xml"]
    path = OUT_DIR / f"{name}.xml"
    path.write_text(xml, encoding="utf-8")
    per_case = 1000 * timing["delta_wall"] / max(timing["delta_cases"], 1)
    print(
        f"{name}: {summary['mainSkill']}, {summary['allocated']} + {summary['ascendancyAllocated']} asc points, "
        f"FullDPS {stats.get('FullDPS', 0):,.0f}, Life {stats.get('Life', 0):,.0f}, "
        f"{timing['delta_cases']} deltas at {per_case:.1f} ms each, total {time.perf_counter() - started:.0f}s"
    )
    return path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    names = sys.argv[1:] or list(BUILDS)
    engine = PobEngine()
    for name in names:
        make(engine, name, BUILDS[name])


if __name__ == "__main__":
    main()
