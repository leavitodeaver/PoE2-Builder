"""Gate 4 (optimizer quality, PLAN.md §8): for each reference build (benchmarks/reference/*.txt),
run the same skeleton with the reference's defences as hard floors and compare damage.

The request is derived from the reference: class and ascendancy, main skill, weapon and offhand
type, level, its keystones (CI, MoM, ...) as required passives, and floors at the reference's Life,
Energy Shield, resistances (capped at 75%) and physical maximum hit. The target is at least the
reference's boss DPS with every floor met, within the time limit. Both sides are measured with
the same objective (on-death damage left out).

Each reference is scored two ways, and both ratios are printed and saved: with its author's own PoB config (the
"toggled" reference: hand-set toggles such as 2b75c's enemy Frozen / Shocked / Blinded / Maimed, 6 Frenzy + 3 Power
charges, Rage 30, which no build the search makes ever gets: SEARCH23's probe POE2T2341), and the way the search scores
its own builds (fair_reference, the "fair" reference). The PASS / FAIL verdict uses the fair one (the manager's
decision, 15 Nov 20:50, after the user's "I'm not hearing a question"); --author-toggles puts the verdict back on the
toggled one, for comparison with older runs. A row's reference_dps / ratio / passed follow the verdict's reference;
reference_dps_fair / ratio_fair and reference_dps_toggled / ratio_toggled keep both.

Usage: uv run python benchmarks/gate4.py [name-filter ...] [--seconds 600] [--workers N] [--events FILE] [--cpu] [--seed-base N] [--author-toggles]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
try:  # the boss preset (MECH50, master 95eceb1): the reference faces the boss the optimizer's builds face
    from builder.search import bosses
except ImportError:  # (not on main yet: then both sides keep PoB's own boss configuration)
    bosses = None
try:  # its hits per damage type (MECHC6; bridge resetConfig's bossHits, the default preset's)
    from builder.search import boss_hits
except ImportError:
    boss_hits = None


def boss_vars() -> dict:
    """What bridge resetConfig writes into every build the search makes (no boss named): resistances and hits."""
    return {**(bosses.config_vars() if bosses is not None else {}),
            **(boss_hits.config_vars() if boss_hits is not None else {})}
RESISTS = {"fire": "FireResist", "cold": "ColdResist", "lightning": "LightningResist", "chaos": "ChaosResist"}


BOSS = (f"{bosses.DEFAULT}: {boss_vars()}" if bosses is not None
        else "none: no builder/search/bosses.py, the reference keeps its code's boss configuration")


FLOOR_STATS = ("Life", "EnergyShield", *RESISTS.values(), "PhysicalMaximumHitTaken")


def floors_from(stats: dict) -> dict:
    """The request's floors from a reference's stats: its Life, Energy Shield, positive resistances (at most 75) and
    Physical maximum hit taken."""
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
    return floors


def reference_request(name: str, facts: dict, keystones: list[str], stats: dict | None = None) -> BuildRequest:
    """The gate's request for a reference: its class, skill, weapon and keystones, its floors from `stats` (the fair
    scoring's, fair_floor_stats) or else PoB's own facts (the first pass, before our models)."""
    floors = floors_from(stats if stats is not None else facts["stats"])
    items = {i["slot"]: i for i in facts["items"]}
    weapon = items.get("Weapon 1")
    offhand = items.get("Weapon 2")
    offhand_choice = "any"
    if offhand is None:
        offhand_choice = "none"
    elif offhand["type"] in ("Shield", "Focus", "Quiver"):
        offhand_choice = offhand["type"].lower()
    elif offhand["type"] in GameData().weapon_types:
        offhand_choice = "weapon"  # dual wielding (a Sceptre or a one-handed weapon)
    return BuildRequest(
        name=f"gate 4: {name}", class_name=facts["className"], ascendancies=[facts["ascendClassName"]],
        main_skill=main_skill(facts), level=facts["level"], weapon_type=weapon["type"] if weapon else None,
        offhand=offhand_choice, require={"passives": keystones}, floors=floors,
        budget={"gear_tier": "endgame"}, objective="boss",  # the reference is scored on boss DPS
    )


def fair_reference(engine: PobEngine, xml: str, request: BuildRequest, legal: bool = False) -> dict:
    """The reference scored as the search scores its own builds (the verdict's reference): the same build (tree, items, gems, groups and
    their Full DPS flags kept), its PoB config reset as for every build the search makes (bridge resetConfig: the
    boss's preset; the author's quest choices are kept, resetConfig keeps every quest input), then the search's own
    scoring steps, as finalize_task runs them on a finished build: conditions and assumptions settled (Search.settle),
    the conditions round, the play choices, press rates, exact uptimes and the Infused factor read again. Returns its DPS, the floors it reads short
    and the conditions it was granted; with `legal`, also the reading made legal (legal_reference, SEARCH35)."""
    from builder.search.inner import Search, boss_config, boss_stats
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(request)
    skeleton = enumerate_skeletons(Context(payload), [request.main_skill])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0, full=True)
    build = search.build
    build.call("loadXml", xml=xml)
    build.call("resetConfig", **boss_config(request))
    build.boss_stats = boss_stats(request)
    build.refresh()
    _score_finished(search)
    out = {"dps": build.objective.dps(build.stats), "short": [d[0] for d in build.objective.deficits(build.stats)],
           "conditions": sorted(search.conditions.active), "floor_stats": fair_floor_stats(build)}
    if legal:
        out["legal"] = legal_reference(search)
    return out


def _score_finished(search) -> None:
    """The search's scoring steps on a finished build, as finalize_task runs them (fair_reference)."""
    for _ in range(4):
        if not search.settle():
            break
    search.conditions_round()
    search.choose_play()
    search.resolve()
    search.press_round(float("inf"))
    search.weigh_uptimes()
    if search.gems.refresh_infused():  # (the Infused factor in the finished state, as finalize_task: SEARCH28)
        search.weigh_uptimes()


# (SEARCH35; SEARCHB1's G0, the manager 13:00 21 Nov: "Score references within the search's own budget, or show that
# reading beside the raw one") The game limits a reference can break under the search's scoring that this reading
# fixes: the level's passive points and Spirit. The others (attribute requirements, skill costs) stay in its "short".
LEGAL_LIMITS = ("Passive points over the level's budget", "Spirit")


def legal_reference(search) -> dict | None:
    """The fair reference made legal the way the search keeps its own builds legal, then scored again: passives over
    the level's points trimmed (tree.trim_to_budget: the weakest branches per point, the search's own trim), then while
    Spirit is short the non-main skill group whose switching off loses least (among those that free Spirit) is switched
    off. None when the reference breaks neither limit. Shown beside the raw fair reading; the verdict is unchanged."""
    from builder.search.objective import number

    build = search.build
    broken = [label for label, _, hard in build.objective.deficits(build.stats) if hard and label in LEGAL_LIMITS]
    if not broken:
        return None
    changes = []
    if trimmed := search.tree.trim_to_budget():
        changes.append(f"{trimmed} passive(s) over the level's points removed (the weakest branches)")
    main = build.main_group()
    for _ in range(len(build.groups())):
        if not any(label == "Spirit" for label, _, hard in build.objective.deficits(build.stats) if hard):
            break
        spirit = number(build.stats.get("SpiritUnreserved"))
        options = []
        for group in build.groups():
            if not group["enabled"] or (main and group["index"] == main["index"]) or group.get("fromItem"):
                continue
            build.set_group(index=group["index"], enabled=False)
            freed, score = number(build.stats.get("SpiritUnreserved")), build.score
            build.set_group(index=group["index"], enabled=True)
            if freed > spirit:
                options.append(((freed >= 0, score), group))
        if not options:
            break
        _, group = max(options, key=lambda o: o[0])
        build.set_group(index=group["index"], enabled=False)
        changes.append(f"{group.get('label') or ' + '.join(g['name'] for g in group['gems'][:1])} switched off (Spirit)")
    _score_finished(search)
    return {"broken": broken, "changes": changes, "dps": build.objective.dps(build.stats),
            "short": [d[0] for d in build.objective.deficits(build.stats)]}


def fair_floor_stats(build) -> dict:
    """FLOOR_STATS as the search's floors read them (objective.deficits): the worse of the build as scored and the build
    with its part-time effects off (objective.floor_deltas), so a reference meets floors taken from them (the manager's
    ruling, 18 Nov 06:25, from MECH87: floors read from PoB before our models left 8 of 9 references short of their own
    Physical max hit after train 76)."""
    from builder.search.objective import number

    stats = build.stats
    out = {}
    for stat in FLOOR_STATS:
        value = number(stats.get(stat))
        for deltas in getattr(build.objective, "floor_deltas", None) or []:
            if stat in deltas:
                value = min(value, value + number(deltas[stat]))
        out[stat] = value
    return out


def verdict(dps: float, feasible: bool, toggled: float, fair: float, author_toggles: bool = False) -> dict:
    """A row's verdict fields: PASS needs every floor met and DPS at least the verdict's reference, the fair one
    (fair_reference) unless author_toggles; reference_dps / ratio name the verdict's reference, and both are kept."""
    reference = toggled if author_toggles else fair
    return {"reference_dps": reference, "ratio": dps / reference if reference else 0.0,
            "verdict": "toggled" if author_toggles else "fair", "passed": feasible and dps >= reference,
            "reference_dps_toggled": toggled, "ratio_toggled": dps / toggled if toggled else 0.0}


def commit() -> str | None:
    """The checkout's commit, so gate_median.py can group a set of runs."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--events", help="append the run's events (all but progress) to this JSONL file")
    parser.add_argument("--cpu", action="store_true",
                        help="compare code, not PC load: --seconds is each worker's processor time (Run's cpu_clock, "
                             "SEARCH19); the wall time grows on a busy PC. Every row reports its evaluations either way")
    parser.add_argument("--work", action="store_true",
                        help="a reproducible gate (SPEED2): --seconds counts fixed costs per engine call (inner.WorkClock), "
                             "so the same seed and code do the same work on any PC load; implies --cpu")
    parser.add_argument("--seed-base", type=int, default=None,
                        help="take draw set N (SEARCH20): every task seed moves by run.SEED_STRIDE x N, and the run "
                             "and its workers hash strings with PYTHONHASHSEED=N, so the same N on two commits takes "
                             "the same draws on the Python side; gate_median.py reads a set of such runs")
    parser.add_argument("--author-toggles", action="store_true",
                        help="give the PASS / FAIL verdict against each reference as scored with its author's own PoB "
                             "config (the gate's verdict before 15 Nov), not against the fair reference (fair_reference: "
                             "scored as the search scores its own builds); both ratios are printed and saved either way")
    parser.add_argument("--fair", action="store_true", help="(the default since 15 Nov: accepted, does nothing)")
    parser.add_argument("--league", default=None,
                        help="price on this league (default: the configured one, builder.data.prices.configured_league; "
                             "before 18 Nov the default was poe.ninja's first listed league and the only price cache 'Forbidden Rites')")
    args = parser.parse_args()
    if args.work:
        args.cpu = True
        os.environ["POE2_SEARCH_WORK_CLOCK"] = "1"  # (read by the workers when they start)
    if args.seed_base is not None and os.environ.get("PYTHONHASHSEED") != str(args.seed_base):
        # (Python fixes its string hashing at start-up: run again with the hash seed set; the workers inherit it)
        env = {**os.environ, "PYTHONHASHSEED": str(args.seed_base)}
        sys.exit(subprocess.run([sys.executable, *sys.argv], env=env).returncode)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    prices = PriceFeed().table(args.league, refresh=False)
    print(f"priced on: {prices.league}" + "".join(f"; {w}" for w in prices.warnings))
    files = sorted(p for p in REFERENCE_DIR.glob("*.txt") if not args.names or any(f in p.stem for f in args.names))
    references = []
    engine = PobEngine()
    fair_engine = None
    for path in files:
        try:
            xml = codes.decode(path.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        engine.call("loadXml", xml=xml)
        if bosses is not None:  # (bridge resetConfig writes the same preset into every build the search makes)
            engine.call("setConfig", vars=boss_vars())
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
        # (its own engine: the search's settle installs assumptions and play choices in the engine it scores in, which
        # must not reach the next reference's own-config score)
        fair_engine = fair_engine or PobEngine()
        fair = fair_reference(fair_engine, xml, request)
        # (the manager's 06:25 ruling, 18 Nov: the floors from the same scoring the search uses; scored again under them,
        # so the reference meets its own floors and its DPS is read with those floors' weights)
        pob_floors = request.floors.model_dump(mode="json", exclude_none=True)
        request = reference_request(path.stem, facts, keystones, fair["floor_stats"])
        fair = fair_reference(fair_engine, xml, request, legal=True)
        fair["pob_floors"] = pob_floors
        if fair["dps"] <= 0 and not args.author_toggles:
            print(f"{path.stem}: skipped, its main skill scores no damage the search's way")
            continue
        print(f"{path.stem}: reference {reference_dps:,.0f} with its own PoB config, {fair['dps']:,.0f} scored as the "
              f"search scores its builds (x{fair['dps'] / reference_dps:.2f}); "
              + ("all floors met" if not fair["short"] else "reads short: " + ", ".join(fair["short"])), flush=True)
        if legal := fair.get("legal"):
            print(f"{path.stem}: the fair reference breaks the game's limits ({', '.join(legal['broken'])}): made legal "
                  f"({'; '.join(legal['changes']) or 'nothing the search could change'}) it reads {legal['dps']:,.0f} "
                  f"(x{legal['dps'] / fair['dps'] if fair['dps'] else 0:.2f} of the raw fair reading)"
                  + ("; short: " + ", ".join(legal["short"]) if legal["short"] else ""), flush=True)
        print(f"{path.stem}: floors from the fair scoring {request.floors.model_dump(mode='json', exclude_none=True)}; "
              f"PoB's own (before our models) were {fair['pob_floors']}", flush=True)
        references.append((path.stem, request, reference_dps, fair))
    del engine, fair_engine

    rows = []
    for name, request, reference_dps, fair in references:
        on_event = None
        if args.events:
            def on_event(event: dict, name: str = name) -> None:
                if event["kind"] != "progress":
                    with open(args.events, "a", encoding="utf-8") as log:
                        log.write(json.dumps({"build": name, **event}) + "\n")
        result = optimize(request, prices=prices, workers=args.workers, seconds=args.seconds, on_event=on_event,
                          cpu_clock=args.cpu, seed_base=args.seed_base or 0)
        dps = (result.get("dps") or {}).get("full", 0.0)
        if args.work:  # (a reproducibility run: a task that failed changes what the run did, so say it)
            for note in result.get("notes") or []:
                if "failed" in note:
                    print("run note:", note, flush=True)
        failing = [c["label"] for c in result.get("checks", []) if c["ok"] is False]
        judged = verdict(dps, bool(result.get("feasible")), reference_dps, fair["dps"], args.author_toggles)
        passed = judged["passed"]
        rows.append({"name": name, "skeleton": result.get("label"), "dps": dps, **judged,
                     "feasible": result.get("feasible"), "failing": failing, "request": request.model_dump(mode="json"), "code": result.get("code"),
                     "play_guide": result.get("play_guide"), "evaluations": result.get("evaluations"),
                     "wall_seconds": result.get("wall_seconds"), "workers": result.get("workers"),
                     "checks": result.get("checks"), "reapply_drift": result.get("reapply_drift"),
                     "open_sockets": result.get("open_sockets"), "final_regrow": result.get("final_regrow")})  # (each floor's row: the sustain and recovery rows' delivered values)
        rows[-1].update({"reference_dps_fair": fair["dps"], "ratio_fair": dps / fair["dps"] if fair["dps"] else None,
                         "reference_fair_short": fair["short"], "reference_fair_conditions": fair["conditions"],
                         "floors_from": "fair", "reference_pob_floors": fair["pob_floors"]})
        if legal := fair.get("legal"):  # (SEARCH35: the reference within the search's own limits, beside the raw one)
            rows[-1].update({"reference_dps_legal": legal["dps"], "ratio_legal": dps / legal["dps"] if legal["dps"] else None,
                             "reference_legal_broken": legal["broken"], "reference_legal_changes": legal["changes"],
                             "reference_legal_short": legal["short"]})
        print(f"[{'PASS' if passed else 'FAIL'}] {name} {result.get('label')}: {dps:,.0f} vs the reference scored the "
              f"search's way {fair['dps']:,.0f} (x{dps / fair['dps'] if fair['dps'] else 0:.2f}) and with its author's "
              f"toggles {reference_dps:,.0f} (x{dps / reference_dps:.2f}); verdict on the "
              + ("toggled" if args.author_toggles else "fair") + " one; "
              + (f"the reference made legal {fair['legal']['dps']:,.0f} (x{dps / fair['legal']['dps']:.2f}; it breaks "
                 f"{', '.join(fair['legal']['broken'])} raw); " if (fair.get("legal") or {}).get("dps") else "")
              + ("all floors met" if result.get("feasible") else "short: " + ", ".join(failing))
              + ("; UNSETTLED: re-settled " + ", ".join(f"{d:,.0f}" for d in result["reapply_drift"]["after"])
                 + f", scored fresh {result['reapply_drift'].get('fresh') or 0:,.0f}"
                 if (result.get("reapply_drift") or {}).get("flagged") else "")
              + "".join(f"; OPEN SOCKETS: {o['skill']} {o['open']} ({' / '.join(o['reasons'])[:160]})"
                        for o in result.get("open_sockets") or [] if o["kind"] != "side")
              + (f"; {n} side-group open sockets" if (n := sum(o["open"] for o in result.get("open_sockets") or []
                                                            if o["kind"] == "side")) else "")
              + (f"; final regrow kept {', '.join(r['clear'] for r in kept)}" if (kept := [
                  r for r in result.get("final_regrow") or [] if r["kept"]]) else
                 ("; final regrow kept nothing" if result.get("final_regrow") else ""))
              + f"; {result.get('evaluations', 0):,} evaluations, {result.get('wall_seconds', 0):,.0f} s wall"
              + (" (work clock)" if args.work else " (processor-time clock)" if args.cpu else ""), flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"gate4-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"seconds": args.seconds, "cpu_clock": args.cpu, "work_clock": args.work, "seed_base": args.seed_base,
                               "hash_seed": os.environ.get("PYTHONHASHSEED"), "commit": commit(), "boss": BOSS, "verdict": "toggled" if args.author_toggles else "fair",
                               "priced_on": prices.league, "price_warnings": prices.warnings, "rows": rows}, indent=1), encoding="utf-8")
    print(f"gate 4: {sum(r['passed'] for r in rows)}/{len(rows)} pass; wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
