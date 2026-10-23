"""The outer loop (PLAN.md §5.1) and the final result.

1. Compile the request; enumerate every legal skeleton (shortlisting main skills by a quick DPS
   screen when the request names none).
2. Probe every skeleton across the worker pool; halve the field and continue the survivors until
   a few remain.
3. Spend the remaining time on the best K, several workers each (different seeds, kicks).
4. Finalize the best build on Full DPS, export it, reload the code to verify it, and check every
   requirement line by line. Anything failing makes the result infeasible, with the reason.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from typing import Any

from builder.pob.pool import EnginePool, WorkerError, default_workers
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.objective import SUSTAIN_FLOOR, rank_key, sustain
from builder.search.skeletons import Skeleton, candidate_skills, enumerate_skeletons, weapon_archetypes

FINALIZE_SHARE = 0.12
FINALIZE_MAX = 60.0
MIN_PROBE = 15.0
MAX_PROBE = 60.0
SHORTLIST_SKILLS = 4
SCREEN_CHUNK = 6
SCREEN_SHARE = 0.25  # most of the run the main-skill screen may take


def _score(entry: Any) -> float:
    return entry["score"] if isinstance(entry, dict) and "score" in entry else -math.inf


class Run:
    def __init__(self, request: BuildRequest, prices: Any = None, workers: int | None = None,
                 seconds: float | None = None, on_event: Callable[[dict], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None) -> None:
        self.request = request
        self.payload = make_payload(request, prices)
        self.ctx = Context(self.payload)
        self.workers = workers or default_workers()
        self.seconds = seconds if seconds is not None else request.effort_minutes * 60.0
        self.on_event = on_event or (lambda event: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.started = time.perf_counter()
        self.notes: list[str] = []
        self.probes: dict[str, dict] = {}  # skeleton label -> latest result
        self.live: dict[int, dict] = {}  # task index -> latest progress
        self.evals_done = 0
        self.best: dict | None = None

    # Events ------------------------------------------------------------------------------------

    def emit(self, kind: str, **data: Any) -> None:
        self.on_event({"kind": kind, "t": round(time.perf_counter() - self.started, 2), **data})

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def remaining(self) -> float:
        return self.seconds - self.elapsed()

    def _progress(self) -> Callable[[int, Any], None]:
        last = [0.0]

        def on_progress(index: int, payload: dict) -> None:
            self.live[index] = payload
            now = time.perf_counter()
            if now - last[0] >= 1.0:
                last[0] = now
                rate = sum(p.get("evals_per_second", 0.0) for p in self.live.values())
                top = max(self.live.values(), key=lambda p: p.get("score", -math.inf))
                self.emit("progress", evals_per_second=round(rate), tasks=len(self.live), leader=top["skeleton"],
                          leader_dps=top["dps"], phase=top["phase"], deficits=top["deficits"],
                          evals=self.evals_done + sum(p.get("evals", 0) for p in self.live.values()))
        return on_progress

    def _record(self, results: list[Any]) -> list[dict]:
        good = []
        for result in results:
            if isinstance(result, WorkerError):
                self.notes.append(f"a search task failed: {str(result).strip().splitlines()[-1][:300]}")
                continue
            if result is None:
                continue
            self.evals_done += result.get("evals", 0)
            label = result["label"]
            if label not in self.probes or rank_key(result) < rank_key(self.probes[label]):
                self.probes[label] = result
            if self.best is None or rank_key(result) < rank_key(self.best):
                self.best = result
                self.emit("best", skeleton=label, dps=result["dps"], score=result["score"],
                          deficits=[d["label"] for d in result["deficits"]])
            good.append(result)
        self.live.clear()
        return good

    def _tasks(self, pool: EnginePool, tasks: list[tuple[str, dict]]) -> list[Any]:
        return pool.run_tasks(tasks, on_progress=self._progress(), should_cancel=self.should_cancel)

    # Stages ------------------------------------------------------------------------------------

    def skeletons(self, pool: EnginePool) -> tuple[list[Skeleton], list[str]]:
        if self.request.main_skill:
            return enumerate_skeletons(self.ctx, [self.request.main_skill])
        skills = candidate_skills(self.ctx)
        self.emit("status", message=f"No main skill given: screening {len(skills)} skills")
        ascendancy = self.ctx.compiled.filters.ascendancies[0] if self.ctx.compiled.filters.ascendancies else None
        screen = []
        for skill in skills:
            archetypes, _ = weapon_archetypes(self.ctx, skill)
            for weapon, sub, offhand in archetypes[:2]:
                if ascendancy:
                    screen.append(Skeleton(self.ctx.class_of[ascendancy], ascendancy, skill, weapon, sub, offhand))
        random.Random(0).shuffle(screen)  # a time-capped screen shouldn't favour early names
        chunks = [screen[i:i + SCREEN_CHUNK] for i in range(0, len(screen), SCREEN_CHUNK)]
        deadline = self.elapsed() + SCREEN_SHARE * self.seconds
        results = pool.run_tasks([("builder.search.inner:screen_task",
                                   {"payload": self.payload, "skeletons": [s.as_dict() for s in chunk]})
                                  for chunk in chunks],
                                 should_cancel=lambda: self.should_cancel() or self.elapsed() > deadline)
        screened = sum(len(chunk) for chunk in results if isinstance(chunk, list))
        if screened < len(screen):
            self.notes.append(f"The skill screen covered {screened} of {len(screen)} skill setups in its time share; "
                              "a longer search time screens them all.")
        ranked: dict[str, float] = {}
        for chunk in results:
            if isinstance(chunk, list):
                for entry in chunk:
                    name = entry["skeleton"]["main_skill"]
                    ranked[name] = max(ranked.get(name, 0.0), entry["dps"])
        shortlist = [name for name, dps in sorted(ranked.items(), key=lambda x: -x[1]) if dps > 0][:SHORTLIST_SKILLS]
        self.emit("status", message="Shortlisted main skills: " + (", ".join(shortlist) or "none"))
        if not shortlist:
            return [], ["no allowed skill deals damage in PoB with the allowed weapons"]
        return enumerate_skeletons(self.ctx, shortlist)

    def plan(self, count: int) -> tuple[float, float]:
        """(probe seconds, finalize seconds) for `count` skeletons in the time left."""
        finalize = min(FINALIZE_MAX, FINALIZE_SHARE * self.seconds)
        search = max(self.remaining() - finalize, 10.0)
        waves = math.ceil(count / self.workers)
        probe = min(MAX_PROBE, max(MIN_PROBE, 0.4 * search / waves))
        return probe, finalize

    def execute(self) -> dict:
        if self.ctx.compiled.errors:
            return self.result_invalid()
        if self.request.seed_code:
            self.notes.append("Seed codes aren't used yet (they arrive with gate 4 in Phase 3); the search started from scratch.")
        if self.request.objective == "mapping":
            self.notes.append("Mapping mode isn't modeled yet; this result maximizes single-target boss DPS.")
        self.emit("status", message=f"Starting {self.workers} PoB workers")
        with EnginePool(self.workers) as pool:
            self.emit("status", message=f"Workers ready in {pool.startup_seconds:.1f}s")
            skeletons, problems = self.skeletons(pool)
            self.emit("skeletons", count=len(skeletons), labels=[s.label for s in skeletons], problems=problems)
            if not skeletons:
                return self.result_infeasible(problems)
            probe, finalize = self.plan(len(skeletons))

            # Probes, then successive halving while there are more survivors than workers
            states: dict[str, dict] = {}
            alive = skeletons
            round_index = 0
            while alive and not self.should_cancel():
                # With fewer skeletons than workers, each gets several searches (different seeds)
                replicas = max(1, self.workers // len(alive))
                self.emit("status", message=f"Probing {len(alive)} skeleton(s) for {probe:.0f}s"
                                            + (f", {replicas} searches each" if replicas > 1 else ""))
                tasks = [("builder.search.inner:optimize_task", {
                    "payload": self.payload, "skeleton": s.as_dict(), "seconds": probe,
                    "seed": round_index * 1000 + 31 * r + i, "state": states.get(s.label)})
                    for i, s in enumerate(alive) for r in range(replicas)]
                best_of: dict[str, dict] = {}
                for result in self._record(self._tasks(pool, tasks)):
                    if result["label"] not in best_of or rank_key(result) < rank_key(best_of[result["label"]]):
                        best_of[result["label"]] = result
                for result in best_of.values():
                    states[result["label"]] = result["state"]
                ranked = sorted(best_of.values(), key=rank_key)
                for r in ranked:
                    self.emit("probe", skeleton=r["label"], dps=r["dps"], score=r["score"],
                              deficits=[d["label"] for d in r["deficits"]])
                keep = max(1, min(len(ranked), max(self.workers // 3, 1)))
                if len(ranked) <= max(keep, self.workers) or self.remaining() < finalize + 2 * probe:
                    alive = [Skeleton(**r["skeleton"]) for r in ranked[:keep]]
                    break
                alive = [Skeleton(**r["skeleton"]) for r in ranked[: math.ceil(len(ranked) / 2)]]
                round_index += 1

            # Focus: the best K skeletons share every worker until it's time to finalize
            focus_seconds = self.remaining() - finalize
            if alive and focus_seconds > 5 and not self.should_cancel():
                replicas = max(1, self.workers // len(alive))
                self.emit("status", message=f"Improving the best {len(alive)} skeleton(s) with {replicas} search(es) each "
                                            f"for {focus_seconds:.0f}s")
                tasks = [("builder.search.inner:optimize_task", {
                    "payload": self.payload, "skeleton": s.as_dict(), "seconds": focus_seconds,
                    "seed": 7919 * (r + 1) + i, "state": states.get(s.label)})
                    for i, s in enumerate(alive) for r in range(replicas)]
                self._record(self._tasks(pool, tasks))

            if self.best is None:
                return self.result_infeasible(problems + ["every search task failed"])
            best = self.best
            self.emit("status", message=f"Finalizing {best['label']} on Full DPS")
            final = None
            for attempt in range(2):  # a worker the watchdog replaced gets one retry
                polish = 5.0 if attempt or self.should_cancel() else max(5.0, min(finalize, self.remaining()))
                final = pool.run_tasks([("builder.search.inner:finalize_task", {
                    "payload": self.payload, "skeleton": best["skeleton"], "state": best["state"], "seconds": polish})],
                    on_progress=self._progress())[0]
                if not isinstance(final, WorkerError):
                    break
                self.notes.append(f"finalizing failed once: {str(final).strip().splitlines()[-1][:200]}")
            if pool.replaced:
                self.notes.append(f"{pool.replaced} PoB worker(s) ran away and were replaced; their "
                                  "searches were lost, the rest continued.")
            if isinstance(final, WorkerError):
                return self.result_infeasible(problems + [f"finalizing failed: {str(final).strip().splitlines()[-1]}"])
            self.evals_done += final.get("evals", 0)
            return self.result(final, problems)

    # Results -----------------------------------------------------------------------------------

    def _base(self, status: str) -> dict:
        elapsed = self.elapsed()
        return {
            "status": status, "request": self.request.model_dump(mode="json"), "notes": self.notes,
            "seconds": round(elapsed, 1), "evaluations": self.evals_done,
            "evals_per_second": round(self.evals_done / elapsed) if elapsed > 0 else 0,
            "workers": self.workers,
            "probes": sorted(({"skeleton": label, "dps": r["dps"], "score": r["score"],
                               "deficits": [d["label"] for d in r["deficits"]]}
                              for label, r in self.probes.items()), key=lambda p: (len(p["deficits"]) > 0, -p["dps"])),
        }

    def result_invalid(self) -> dict:
        result = self._base("invalid")
        result["feasible"] = False
        result["reasons"] = [f"{i.field}: {i.message}" for i in self.ctx.compiled.errors]
        return result

    def result_infeasible(self, problems: list[str]) -> dict:
        result = self._base("infeasible")
        result["feasible"] = False
        result["reasons"] = problems or ["no legal build found"]
        return result

    def result(self, final: dict, problems: list[str]) -> dict:
        facts = final["facts"]
        checks = [c.__dict__ for c in self.ctx.compiled.check(facts)]
        spent = final["spent"]
        budget = self.ctx.budget
        for check in checks:
            if check["label"] == "Budget":
                low = [g["name"] for g in final["gear"].values() if g["confidence"] in ("low", "unknown")]
                check.update(delivered=f"{spent:.2f} divine", ok=spent <= budget + 1e-9,
                             note="poe.ninja prices" + (f"; low-confidence: {', '.join(low)}" if low else ""))
        share, pool = sustain(facts["stats"])
        checks.append({"label": "Skill costs sustained", "requested": f"≥ {SUSTAIN_FLOOR:.0%} by recovery",
                       "delivered": "no costs to pay" if pool is None and share >= 1 else f"{min(share, 9.99):.0%}"
                       + (f" of {pool}" if pool else ""), "ok": share >= SUSTAIN_FLOOR,
                       "note": "regeneration, leech and on-hit recovery against the per-second cost; flasks not counted"})
        failing = [c for c in checks if c["ok"] is False]
        reasons = [f"{c['label']}: requested {c['requested']}, delivered {c['delivered']}" for c in failing]
        reasons += final.get("problems") or []
        if final["dps"] <= 0:
            # Nothing to maximize: PoB doesn't model this skill's damage (PLAN.md §10)
            reasons.append(f"PoB calculates no damage for {facts.get('mainSkill') or 'the main skill'} with any setup "
                           "tried, so its damage isn't modeled yet; it needs a ledger entry (Phase 4)")
        if not final["verified"]:
            reasons.append("the exported PoB code didn't reload to the same numbers: " + ", ".join(final["verify_mismatch"]))
        if final.get("excluded_dps"):
            self.notes.append(f"PoB's Full DPS ({final['pob_full_dps']:,.0f}) also counts {final['excluded_dps']:,.0f} from "
                              f"effects that need enemies to die ({', '.join(final['excluded_from'])}); a single boss "
                              "never triggers them, so they're left out of the DPS above.")
        for assumption in final.get("assumptions") or []:
            self.notes.append(f"* {assumption['label']}: {assumption['value']} (assumed, since PoB doesn't model it: "
                              f"{assumption['basis']}; confidence: {assumption['confidence']}). The PoB code doesn't "
                              "carry this, so PoB itself shows less damage for that skill.")
        result = self._base("ok" if not reasons else "infeasible")
        result.update({
            "feasible": not reasons,
            "reasons": reasons,
            "relaxations": relaxations(failing),
            "skeleton": final["skeleton"], "label": final["label"],
            "summary": {k: facts[k] for k in ("className", "ascendClassName", "level", "mainSkill", "allocated",
                                               "ascendancyAllocated")},
            "dps": {"full": final["dps"], "main": final["main_dps"], "pob_full": final.get("pob_full_dps"),
                    "excluded": final.get("excluded_dps"), "excluded_from": final.get("excluded_from")},
            "stats": facts["stats"], "checks": checks, "code": final["code"], "verified": final["verified"],
            "gear": final["gear"], "spent": spent, "groups": final["groups"], "conditions": final["conditions"],
            "quests": final["quests"], "warnings": facts["warnings"], "skeleton_problems": problems,
            "assumptions": final.get("assumptions") or [],
            "timing": final.get("timing"), "escalations": final.get("escalations"),
            "requirement_weights": final.get("multipliers"),
        })
        return result


def relaxations(failing: list[dict]) -> list[str]:
    """The smallest change to the request that the delivered build would satisfy."""
    hints = []
    for check in failing:
        label, delivered = check["label"], check["delivered"]
        if label == "DPS gate":
            hints.append(f"lower the DPS gate to {delivered} (rares arrive in Phase 3)")
        elif check["requested"].startswith("≥"):
            hints.append(f"lower the {label.lower()} floor to {delivered}, the most found while meeting the other "
                         "requirements with uniques and passives (rare gear, Phase 3, can add more)")
        elif label == "Buttons":
            hints.append(f"allow {delivered.split()[0]} buttons")
        elif label == "Budget":
            hints.append(f"raise the budget to {delivered}")
        else:
            hints.append(f"relax '{label}' (delivered: {delivered})")
    return hints


def optimize(request: BuildRequest, **kwargs: Any) -> dict:
    """Run the optimizer on a request; see Run for the keyword arguments."""
    return Run(request, **kwargs).execute()
