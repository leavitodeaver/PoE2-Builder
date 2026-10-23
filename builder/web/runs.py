"""Optimizer runs for the web page: one at a time, in a background thread, with an event log the
page polls and a result saved under data/results/."""

from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from builder.data import DATA_ROOT
from builder.request.schema import BuildRequest

RESULTS_DIR = DATA_ROOT / "results"


@dataclass
class RunRecord:
    id: int
    request: BuildRequest
    request_id: int | None
    status: str = "running"  # running / done / cancelled / error
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    cancel: bool = False
    started: float = field(default_factory=time.time)

    def view(self, since: int = 0) -> dict:
        return {
            "id": self.id, "status": self.status, "request_id": self.request_id, "name": self.request.name,
            "started": self.started, "seconds": self.request.effort_minutes * 60, "events": self.events[since:],
            "next": len(self.events), "result": self.result if self.status != "running" else None, "error": self.error,
        }


class RunManager:
    def __init__(self, prices) -> None:
        self.prices = prices
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
            self.runs[record.id] = record
            self.next_id += 1
        threading.Thread(target=self._execute, args=(record, seconds), daemon=True).start()
        return record

    def _execute(self, record: RunRecord, seconds: float | None) -> None:
        from builder.search.run import optimize

        try:
            try:
                prices = self.prices.table(record.request.league, refresh=False)
            except Exception as error:
                prices = None
                record.events.append({"kind": "status", "t": 0, "message": f"No price data ({error}); uniques can't be priced"})
            record.result = optimize(record.request, prices=prices, seconds=seconds, on_event=record.events.append,
                                     should_cancel=lambda: record.cancel)
            record.status = "cancelled" if record.cancel else "done"
            self._save(record)
        except Exception:
            record.error = traceback.format_exc()
            record.status = "error"

    def _save(self, record: RunRecord) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = RESULTS_DIR / f"{stamp}-run{record.id}.json"
        path.write_text(json.dumps(record.result, indent=1, default=str), encoding="utf-8")
        record.result["saved_as"] = path.name
