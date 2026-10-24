"""Optimizer runs for the web page: one at a time, in a background thread, with an event log the
page polls and a result saved under data/results/.

A run can take edits to its request while it is going (`RunManager.update`): the search in progress
is stopped with its best build kept, and a new search with the edited request starts from that
build's PoB code for the time that is left. The page sees one run throughout, with one event log
and one saved result, which reflects the latest request.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from builder import settings
from builder.data import DATA_ROOT
from builder.request.schema import BuildRequest

RESULTS_DIR = DATA_ROOT / "results"
# A search needs a little time to probe and finalize: an edit near the end of a run still gets this
MIN_CONTINUE_SECONDS = 45.0
# Fields that change nothing about the search
INERT_FIELDS = {"name", "notes"}
CARRIED = "the best build carried over from before the edit"


def changed_fields(old: BuildRequest, new: BuildRequest) -> list[str]:
    """The request's fields an edit changed, apart from the name and notes."""
    before, after = old.model_dump(mode="json"), new.model_dump(mode="json")
    return [key for key in after if key not in INERT_FIELDS and before.get(key) != after[key]]


_web_run_class = None


def web_run(request: BuildRequest, **kwargs):
    """builder.search.run.optimize, with each new best build's PoB file reported in a `best_build`
    event, for the page's live summary. (A subclass, so the optimizer itself stays untouched.)"""
    global _web_run_class
    if _web_run_class is None:
        from builder.search.run import Run

        class WebRun(Run):
            def _record(self, results):
                before = self.best
                good = super()._record(results)
                best = self.best
                state = (best or {}).get("state") or {}
                if best is not before and state.get("xml"):
                    self.emit("best_build", skeleton=best["label"], dps=best["dps"], clear=best.get("clear"),
                              value=best.get("value"), deficits=[d["label"] for d in best["deficits"]], xml=state["xml"])
                return good

        _web_run_class = WebRun
    return _web_run_class(request, **kwargs).execute()


@dataclass
class RunRecord:
    id: int
    request: BuildRequest  # the latest request, edits included
    request_id: int | None
    status: str = "running"  # running / done / cancelled / error
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    cancel: bool = False
    started: float = field(default_factory=time.time)
    total_seconds: float = 0.0  # the search time, counted from `started`
    pending: BuildRequest | None = None  # an edit waiting for the search in progress to stop
    closing: bool = False  # the last search is over: edits can no longer be applied
    restarts: int = 0
    synopsis: dict | None = None  # the best build so far, summarized for the page
    synopsis_job: dict | None = None
    synopsis_thread: threading.Thread | None = None

    def view(self, since: int = 0) -> dict:
        return {
            "id": self.id, "status": self.status, "request_id": self.request_id, "name": self.request.name,
            "started": self.started, "seconds": self.total_seconds, "events": self.events[since:],
            "next": len(self.events), "result": self.result if self.status != "running" else None, "error": self.error,
            "request": self.request.model_dump(mode="json"), "restarts": self.restarts, "synopsis": self.synopsis,
        }


class RunManager:
    def __init__(self, prices, summarize: Callable[[str], dict] | None = None) -> None:
        """`summarize` turns a build's PoB XML into the facts the live summary shows."""
        self.prices = prices
        self.summarize = summarize
        self.runs: dict[int, RunRecord] = {}
        self.lock = threading.Lock()
        self.next_id = 1

    def current(self) -> RunRecord | None:
        return self.runs.get(self.next_id - 1)

    def start(self, request: BuildRequest, request_id: int | None = None, seconds: float | None = None) -> RunRecord:
        with self.lock:
            running = self.current()
            if running and running.status == "running":
                raise RuntimeError("a run is already in progress")
            record = RunRecord(self.next_id, request, request_id)
            record.total_seconds = seconds if seconds is not None else request.effort_minutes * 60.0
            self.runs[record.id] = record
            self.next_id += 1
        threading.Thread(target=self._execute, args=(record,), daemon=True).start()
        return record

    # Edits while running ------------------------------------------------------------------------

    def update(self, run_id: int, request: BuildRequest) -> dict:
        """Apply an edited request to a run in progress: {"restarting": bool, "changed": [fields]}.
        The search in progress stops (keeping its best build) and the next one picks the edit up."""
        with self.lock:
            record = self.runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if record.status != "running" or record.closing or record.cancel:
                raise RuntimeError("that run has finished or is stopping, so it can't take changes")
            changed = changed_fields(record.request, request)
            record.request = request  # the result reflects the latest request, name and notes included
            if not changed:
                return {"restarting": False, "changed": []}
            if "effort_minutes" in changed:
                record.total_seconds = request.effort_minutes * 60.0
            record.pending = request
            self._note(record, "status", "Request changed: stopping the search step in progress, "
                                         "keeping its best build, to continue with the new settings")
            return {"restarting": True, "changed": changed}

    @staticmethod
    def _note(record: RunRecord, kind: str, message: str, **extra) -> None:
        record.events.append({"kind": kind, "t": round(time.time() - record.started, 2), "message": message, **extra})

    def _emit(self, record: RunRecord, offset: float) -> Callable[[dict], None]:
        """The event sink of one search: its times count from the start of the whole run."""
        def emit(event: dict) -> None:
            event = {**event, "t": round(event["t"] + offset, 2)}
            if event["kind"] == "best_build":
                self._summarize_later(record, event)  # carries a whole PoB file, so it stays out of the log
            else:
                record.events.append(event)
        return emit

    # The live summary of the best build so far ----------------------------------------------------

    def _summarize_later(self, record: RunRecord, event: dict) -> None:
        record.synopsis_job = event
        if record.synopsis_thread is None or not record.synopsis_thread.is_alive():
            record.synopsis_thread = threading.Thread(target=self._summarize_loop, args=(record,), daemon=True)
            record.synopsis_thread.start()

    def _summarize_loop(self, record: RunRecord) -> None:
        while (event := record.synopsis_job) is not None:
            record.synopsis_job = None
            summary = {key: event[key] for key in ("skeleton", "dps", "clear", "value", "deficits", "t")}
            try:
                if self.summarize is not None:
                    summary.update(self.summarize(event["xml"]))
            except Exception as error:  # the DPS line still shows without PoB's numbers
                summary["error"] = f"{type(error).__name__}: {str(error)[:200]}"
            record.synopsis = summary  # replaced whole, so the page never sees half of one build and half of another

    # The run ------------------------------------------------------------------------------------

    def _execute(self, record: RunRecord) -> None:
        try:
            self._search(record)
        except Exception:
            record.error = traceback.format_exc()
            record.status = "error"

    def _search(self, record: RunRecord) -> None:
        ran = record.request  # the request the search in progress was started for
        request = ran  # ... and what it runs: that, or with the build carried over as its seed code
        carrying = False
        stopped: list[dict] = []  # results of the searches an edit ended early
        while True:
            try:
                prices = self.prices.table(request.league, refresh=False)
            except Exception as error:
                prices = None
                self._note(record, "status", f"No price data ({error}); uniques can't be priced")
            offset = time.time() - record.started
            seconds = record.total_seconds - offset
            if stopped:
                seconds = max(seconds, MIN_CONTINUE_SECONDS)
                record.total_seconds = max(record.total_seconds, offset + seconds)
            # Read the worker setting per search, so a change on the page applies to the next one
            result = web_run(request, prices=prices, workers=settings.workers(), seconds=seconds,
                             on_event=self._emit(record, offset),
                             should_cancel=lambda: record.cancel or record.pending is not None)
            with self.lock:
                follow = None if record.cancel else record.pending
                record.pending = None
                record.closing = follow is None
            if follow is None:
                break
            stopped.append(result)
            record.restarts += 1
            changed = changed_fields(ran, follow)
            code = result.get("code")
            # A PoB code the user changed in the form wins over the build the search had found
            carrying = bool(code) and "seed_code" not in changed
            request = follow.model_copy(update={"seed_code": code}) if carrying else follow
            ran = follow
            self._note(record, "restart", (
                "Request changed: the search continued from its best build so far with the new settings"
                if carrying else "Request changed: the search restarted with the new settings"
                + (" (it had no build to carry over yet)" if not code else "")), changed=changed)
        if stopped:
            self._merge(record, result, stopped, carrying)
        result["request"] = record.request.model_dump(mode="json")
        record.result = result
        self._save(record)
        record.status = "cancelled" if record.cancel else "done"

    @staticmethod
    def _merge(record: RunRecord, result: dict, stopped: list[dict], carrying: bool) -> None:
        """One result for a run an edit split into several searches: totals over all of them, and the
        notes about a seed describing the carried-over build instead of a PoB code the user gave."""
        evaluations = sum(r.get("evaluations", 0) for r in stopped) + result.get("evaluations", 0)
        elapsed = time.time() - record.started
        result.update(evaluations=evaluations, seconds=round(elapsed, 1),
                      evals_per_second=round(evaluations / elapsed) if elapsed > 0 else 0)
        notes = result.setdefault("notes", [])
        if carrying:
            notes[:] = [n.replace("Your PoB code", CARRIED.capitalize()).replace("your PoB code", CARRIED) for n in notes]
        count = len(stopped)
        notes.append(f"The request was edited {count} time{'s' if count != 1 else ''} while the search ran. Each time, the "
                     "search stopped, kept its best build and continued from it with the new settings, so the evaluations "
                     "and seconds above add up all the searches, and the skeleton table covers the last one.")

    def _save(self, record: RunRecord) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = RESULTS_DIR / f"{stamp}-run{record.id}.json"
        path.write_text(json.dumps(record.result, indent=1, default=str), encoding="utf-8")
        record.result["saved_as"] = path.name
