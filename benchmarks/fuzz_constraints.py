"""Gate 5 (constraint compliance): randomized requests, every result checked independently.

Each random request is optimized briefly. The result's PoB code is then loaded in a fresh engine
and every requirement is checked again from the reloaded build. The gate holds when, for every
case, either every check passes and the result says feasible, or the result says infeasible and
names every failing check in its reasons. Bans, the button cap and the budget are enforced at the
source, so those must hold even in infeasible results.

Usage: uv run python benchmarks/fuzz_constraints.py [--cases 10] [--seconds 40] [--workers N] [--seed 1]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime

from builder import ROOT
from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.pob import codes
from builder.pob.pool import EnginePool
from builder.request.constraints import FACT_FIELDS, compile_request
from builder.request.schema import BuildRequest, DefenceStyle, GearTier, Offhand
from builder.search.run import optimize

RESULT_DIR = ROOT / "benchmarks" / "results"
SEEDS = [p.read_text(encoding="utf-8").strip() for p in sorted((ROOT / "benchmarks" / "reference").glob("2b75*.txt"))]
SKILLS = ["Ice Shot", "Spark", "Boneshatter", "Lightning Arrow", "Fireball", "Raise Zombie", "Rolling Slam",
          "Twister", "Explosive Grenade", "Frost Bomb", "Falling Thunder", "Rake", "Contagion", "Ice Strike"]
KEYSTONES = ["Mind Over Matter", "Chaos Inoculation", "Iron Reflexes", "Resolute Technique", "Eldritch Battery",
             "Acrobatics", "Blood Magic", "Unwavering Stance"]
# Checks enforced while searching (never proposed when violated), so they must pass even when
# the result is infeasible for other reasons
SOURCE_CHECKS = {"Banned skills and supports", "Banned passives", "Banned uniques", "Banned augments",
                 "Banned item classes", "Banned mechanics", "Buttons", "Budget", "Main skill", "Ascendancy", "Class"}


def random_request(rng: random.Random, data: GameData, index: int) -> BuildRequest:
    ascendancies = rng.sample(data.ascendancies, rng.choice([1, 1, 2]))
    supports = rng.sample(data.supports, rng.choice([0, 3, 10, 30]))
    uniques = rng.sample(data.unique_names, rng.choice([0, 5, 20]))
    body: dict = {
        "name": f"fuzz {index}",
        "ascendancies": ascendancies,
        "main_skill": rng.choice(SKILLS),
        "max_buttons": rng.choice([None, 1, 2, 3]),
        "forbid": {"supports": supports, "uniques": uniques,
                   "passives": rng.sample(KEYSTONES, rng.choice([0, 0, 1, 2])),
                   "item_classes": rng.sample(["Shield", "Focus", "Quiver", "Helmet", "Sceptre", "Charm", "Flask"],
                                              rng.choice([0, 0, 1]))},
        # A second attack that may need another weapon: dual wielding, weapon swap
        "require": {"passives": rng.sample(KEYSTONES, rng.choice([0, 0, 0, 1])),
                    "skills": rng.choice([[], [], [], ["Ice Shot"], ["Spear Throw"]])},
        "offhand": rng.choice([Offhand.ANY, Offhand.ANY, Offhand.NONE, Offhand.SHIELD, Offhand.FOCUS, Offhand.QUIVER,
                               Offhand.WEAPON]),
        "weapon_swap": rng.choice(["may", "may", "must", "never"]),
        # Sometimes start from a reference build's PoB code (most don't fit the random request)
        "seed_code": rng.choice([None, None, None, *SEEDS]),
        "defence": rng.choice(list(DefenceStyle)),
        "floors": {
            "life": rng.choice([None, None, 1000, 2500]),
            "resists": rng.choice([{}, {}, {"fire": 75, "cold": 75, "lightning": 75}, {"chaos": 20}]),
            "spare_spirit": rng.choice([None, None, 30]),
            "max_hit": rng.choice([{}, {}, {"physical": 3000}]),
        },
        "budget": {"divines": rng.choice([None, 0, 2, 20]), "gear_tier": rng.choice(list(GearTier))},
    }
    # Keep bans and requirements consistent, as the schema demands
    body["forbid"]["passives"] = [p for p in body["forbid"]["passives"] if p not in body["require"]["passives"]]
    return BuildRequest.model_validate(body)


def evaluate(pool: EnginePool, request: BuildRequest, result: dict, data: GameData) -> list[str]:
    """Gate 5 violations in one result (empty when it holds)."""
    problems = []
    if result["status"] in ("invalid", "infeasible") and not result.get("code"):
        return [] if result.get("reasons") else ["infeasible without a reason"]
    pool.broadcast("loadXml", xml=codes.decode(result["code"]))
    facts = pool.broadcast("facts", fields=FACT_FIELDS)[0]
    checks = compile_request(request, data).check(facts)
    failing = [c for c in checks if c.ok is False]
    reasons = " | ".join(result.get("reasons") or [])
    if result["feasible"] and failing:
        problems += [f"claimed feasible but {c.label} fails ({c.delivered})" for c in failing]
    for check in failing:
        if check.label not in reasons:
            problems.append(f"{check.label} fails ({check.delivered}) but isn't among the reasons")
        if check.label in SOURCE_CHECKS:
            problems.append(f"source-enforced {check.label} fails ({check.delivered})")
    budget = next((c for c in result["checks"] if c["label"] == "Budget"), None)
    if budget and budget["ok"] is False:
        problems.append(f"budget exceeded: {budget['delivered']}")
    if not result["verified"]:
        problems.append("exported code didn't reload identically")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--seconds", type=float, default=40)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = GameData()
    prices = PriceFeed().table(refresh=False)
    rng = random.Random(args.seed)
    cases = []
    with EnginePool(1) as checker:
        for index in range(args.cases):
            request = random_request(rng, data, index)
            started = time.perf_counter()
            result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds)
            problems = evaluate(checker, request, result, data)
            cases.append({"request": request.model_dump(mode="json"), "status": result["status"],
                          "label": result.get("label"), "reasons": result.get("reasons"), "problems": problems,
                          "seconds": round(time.perf_counter() - started, 1), "code": result.get("code")})
            verdict = "PASS" if not problems else "FAIL"
            print(f"[{verdict}] case {index}: {result['status']} {result.get('label', '')} "
                  f"({'; '.join(result.get('reasons') or [])[:160]})" + (f"\n    problems: {problems}" if problems else ""),
                  flush=True)
    passed = sum(1 for c in cases if not c["problems"])
    print(f"gate 5: {passed}/{len(cases)} cases pass")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gate5-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"seed": args.seed, "seconds": args.seconds, "passed": passed, "cases": cases}, indent=1),
                   encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
