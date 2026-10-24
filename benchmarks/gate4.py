"""Gate 4 (optimizer quality, PLAN.md §8): for each reference build (benchmarks/reference/*.txt),
run the same skeleton with the reference's defences as hard floors and compare damage.

The request is derived from the reference: class and ascendancy, main skill, weapon and offhand
type, level, its keystones (CI, MoM, ...) as required passives, and floors at the reference's Life,
Energy Shield, resistances (capped at 75%) and physical maximum hit. The target is at least the
reference's boss DPS with every floor met, within the time limit. Both sides are measured with
the same objective (on-death damage left out).

Usage: uv run python benchmarks/gate4.py [name-filter ...] [--seconds 600] [--workers N]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from builder import ROOT
from builder.data.prices import PriceFeed
from builder.pob import codes
from builder.pob.engine import PobEngine
from builder.request.constraints import FACT_FIELDS, main_skill
from builder.request.schema import BuildRequest
from builder.data import GameData
from builder.search.assumptions import Assumptions
from builder.search.context import Context, make_payload
from builder.search.state import Build
from builder.search.objective import Objective
from builder.search.run import optimize

REFERENCE_DIR = ROOT / "benchmarks" / "reference"
RESULT_DIR = ROOT / "benchmarks" / "results"
RESISTS = {"fire": "FireResist", "cold": "ColdResist", "lightning": "LightningResist", "chaos": "ChaosResist"}


def reference_request(name: str, facts: dict, keystones: list[str]) -> BuildRequest:
    stats = facts["stats"]
    items = {i["slot"]: i for i in facts["items"]}
    weapon = items.get("Weapon 1")
    offhand = items.get("Weapon 2")
    floors: dict = {"resists": {}, "max_hit": {}}
    if (stats.get("Life") or 0) > 1:
        floors["life"] = int(stats["Life"])
    if (stats.get("EnergyShield") or 0) > 0:
        floors["energy_shield"] = int(stats["EnergyShield"])
    for damage, stat in RESISTS.items():
        value = stats.get(stat) or 0
        if value > 0:
            floors["resists"][damage] = int(min(value, 75))
    if (stats.get("PhysicalMaximumHitTaken") or 0) > 0:
        floors["max_hit"]["physical"] = int(stats["PhysicalMaximumHitTaken"])
    offhand_choice = "any"
    if offhand is None:
        offhand_choice = "none"
    elif offhand["type"] in ("Shield", "Focus", "Quiver"):
        offhand_choice = offhand["type"].lower()
    return BuildRequest(
        name=f"gate 4: {name}", class_name=facts["className"], ascendancies=[facts["ascendClassName"]],
        main_skill=main_skill(facts), level=facts["level"], weapon_type=weapon["type"] if weapon else None,
        offhand=offhand_choice, require={"passives": keystones}, floors=floors,
        budget={"gear_tier": "endgame"}, objective="boss",  # the reference is scored on boss DPS
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    prices = PriceFeed().table(refresh=False)
    files = sorted(p for p in REFERENCE_DIR.glob("*.txt") if not args.names or any(f in p.stem for f in args.names))
    references = []
    engine = PobEngine()
    for path in files:
        try:
            xml = codes.decode(path.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        engine.call("loadXml", xml=xml)
        facts = engine.call("facts", fields=FACT_FIELDS + ["FullDPS", "SkillDPS"])
        tree_keystones = set(GameData().keystones)  # an item-granted keystone can't be required
        keystones = sorted(p["name"] for p in facts["passives"] if p["type"] == "Keystone" and p["name"] in tree_keystones)
        request = reference_request(path.stem, facts, keystones)
        # The reference is measured exactly as the optimizer measures its own builds, assumptions included
        ctx = Context(make_payload(request))
        reference = Build(engine, ctx, Objective(ctx, full=True))
        reference.refresh()
        Assumptions(reference).apply()
        reference_dps = reference.objective.dps(reference.stats)
        if reference_dps <= 0:
            print(f"{path.stem}: skipped, PoB gives its main skill ({main_skill(facts)}) no damage")
            continue
        references.append((path.stem, request, reference_dps))
    del engine

    rows = []
    for name, request, reference_dps in references:
        result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds)
        dps = (result.get("dps") or {}).get("full", 0.0)
        failing = [c["label"] for c in result.get("checks", []) if c["ok"] is False]
        passed = result.get("feasible", False) and dps >= reference_dps
        rows.append({"name": name, "skeleton": result.get("label"), "reference_dps": reference_dps, "dps": dps,
                     "ratio": dps / reference_dps, "feasible": result.get("feasible"), "failing": failing,
                     "passed": passed, "request": request.model_dump(mode="json"), "code": result.get("code")})
        print(f"[{'PASS' if passed else 'FAIL'}] {name} {result.get('label')}: {dps:,.0f} vs reference "
              f"{reference_dps:,.0f} (x{dps / reference_dps:.2f}); "
              + ("all floors met" if result.get("feasible") else "short: " + ", ".join(failing)), flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gate4-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"seconds": args.seconds, "rows": rows}, indent=1), encoding="utf-8")
    print(f"gate 4: {sum(r['passed'] for r in rows)}/{len(rows)} pass; wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
