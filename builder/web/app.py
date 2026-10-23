"""Local web app: request form, saved requests, PoB code inspection, price status.

PoB runs in a worker process (it changes its process's working directory), started on first use.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.pob import codes
from builder.request import schema
from builder.request.constraints import FACT_FIELDS, compile_request, main_skill, player_buttons
from builder.request.schema import BuildRequest
from builder.web.runs import RESULTS_DIR, RunManager
from builder.web.store import RequestStore

STATIC = Path(__file__).parent / "static"
SHARE_LINKS = [
    # Build-sharing sites PoB itself imports from: (pattern, raw-code URL)
    (re.compile(r"poe\.ninja/(?:poe2/)?pob/([\w-]+)"), "https://poe.ninja/poe2/pob/raw/{}"),
    (re.compile(r"pobb\.in/([\w-]+)"), "https://pobb.in/{}/raw"),
]


class Engine:
    """One PoB worker process, created lazily and shared by requests one at a time."""

    def __init__(self) -> None:
        self._pool = None
        self._lock = threading.Lock()

    def call(self, method: str, **kwargs):
        from builder.pob.pool import EnginePool

        with self._lock:
            if self._pool is None:
                self._pool = EnginePool(1)
            return self._pool.broadcast(method, **kwargs)[0]

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()


data = GameData()
store = RequestStore()
prices = PriceFeed()
engine = Engine()
runs = RunManager(prices)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    engine.close()


app = FastAPI(title="PoE2 Builder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/options")
def options() -> dict:
    passives = sorted({n["name"] for n in data.tree["nodes"] if n["type"] in ("Keystone", "Notable") and n["name"]})
    mechanics = sorted({t for g in data.gems for t in g["tags"]} | {t for g in data.gems for t in g["skillTypes"]}, key=str.lower)
    try:
        leagues = [league["id"] for league in prices.leagues()]
        default_league = prices.default_league()
    except OSError:
        leagues, default_league = [], None
    return {
        "data": data.manifest,
        "classes": data.classes,
        "lists": {
            "skills": data.active_skills, "supports": data.supports, "passives": passives,
            "uniques": data.unique_names, "item_classes": data.item_classes, "augments": data.augments,
            "mechanics": mechanics,
        },
        "weapon_types": data.weapon_types,
        "enums": {
            "offhand": [e.value for e in schema.Offhand], "weapon_swap": [e.value for e in schema.WeaponSwap],
            "defence": [e.value for e in schema.DefenceStyle], "gear_tier": [e.value for e in schema.GearTier],
            "objective": [e.value for e in schema.Objective], "damage": [e.value for e in schema.DamageType],
        },
        "defaults": BuildRequest().model_dump(),
        "leagues": leagues,
        "default_league": default_league,
    }


def parse_request(body: dict) -> BuildRequest:
    try:
        return BuildRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(422, detail=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in error.errors()])


def issues_of(request: BuildRequest) -> list[dict]:
    return [issue.__dict__ for issue in compile_request(request, data).issues]


@app.get("/api/requests")
def list_requests() -> list[dict]:
    return store.list()


@app.get("/api/requests/{request_id}")
def get_request(request_id: int) -> dict:
    request = store.get(request_id)
    if request is None:
        raise HTTPException(404, "no such request")
    return request.model_dump()


class SaveBody(BaseModel):
    id: int | None = None
    request: dict


@app.post("/api/requests")
def save_request(body: SaveBody) -> dict:
    request = parse_request(body.request)
    request_id = store.save(request, body.id)
    return {"id": request_id, "issues": issues_of(request)}


@app.delete("/api/requests/{request_id}")
def delete_request(request_id: int) -> dict:
    store.delete(request_id)
    return {"ok": True}


@app.post("/api/validate")
def validate(body: dict) -> dict:
    return {"issues": issues_of(parse_request(body))}


class InspectBody(BaseModel):
    code: str
    request: dict | None = None


def resolve_code(text: str) -> str:
    """A PoB code, or a poe.ninja / pobb.in share link the user pasted (fetched like PoB does)."""
    text = text.strip()
    for pattern, raw_url in SHARE_LINKS:
        match = pattern.search(text)
        if match:
            request = urllib.request.Request(raw_url.format(match.group(1)), headers={"User-Agent": prices.user_agent})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8").strip()
    return text


@app.post("/api/inspect")
def inspect(body: InspectBody) -> dict:
    try:
        xml = codes.decode(resolve_code(body.code))
    except Exception as error:  # bad base64/zlib, or the link didn't resolve
        raise HTTPException(400, f"That doesn't look like a PoB code or share link ({error}).")
    engine.call("loadXml", xml=xml)
    facts = engine.call("facts", fields=FACT_FIELDS)
    result = {
        "summary": {key: facts[key] for key in ("className", "ascendClassName", "level", "allocated", "ascendancyAllocated")},
        "mainSkill": main_skill(facts),
        "buttons": player_buttons(facts),
        "stats": facts["stats"],
        "warnings": facts["warnings"],
        "items": facts["items"],
        "checks": None,
    }
    if body.request is not None:
        compiled = compile_request(parse_request(body.request), data)
        result["checks"] = [check.__dict__ for check in compiled.check(facts)]
        result["issues"] = [issue.__dict__ for issue in compiled.issues]
    return result


class RunBody(BaseModel):
    request: dict
    id: int | None = None  # the saved request this run belongs to, if any


@app.post("/api/runs")
def start_run(body: RunBody) -> dict:
    request = parse_request(body.request)
    errors = [i for i in compile_request(request, data).issues if i.level == "error"]
    if errors:
        raise HTTPException(422, detail=[{"field": i.field, "message": i.message} for i in errors])
    try:
        record = runs.start(request, body.id)
    except RuntimeError as error:
        raise HTTPException(409, str(error))
    return record.view()


@app.get("/api/runs/current")
def current_run() -> dict:
    record = runs.current()
    return record.view() if record else {}


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, since: int = 0) -> dict:
    record = runs.runs.get(run_id)
    if record is None:
        raise HTTPException(404, "no such run")
    return record.view(since)


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict:
    record = runs.runs.get(run_id)
    if record is None:
        raise HTTPException(404, "no such run")
    record.cancel = True
    return {"ok": True}


@app.get("/api/results")
def list_results() -> list[dict]:
    """Saved optimizer results, newest first."""
    paths = sorted(RESULTS_DIR.glob("*.json"), reverse=True) if RESULTS_DIR.exists() else []
    return [{"name": p.name, "saved_at": p.stat().st_mtime} for p in paths]


@app.get("/api/results/{name}")
def get_result(name: str) -> dict:
    path = RESULTS_DIR / name
    if path.parent != RESULTS_DIR or path.suffix != ".json" or not path.exists():
        raise HTTPException(404, "no such result")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/prices")
def price_status(league: str | None = None) -> dict:
    table = prices.table(league, refresh=False)
    return {"league": table.league, "fetched_at": table.fetched_at, "exchange": len(table.exchange), "uniques": len(table.uniques)}


@app.post("/api/prices/refresh")
def refresh_prices(league: str | None = None) -> dict:
    league = league or prices.default_league()
    statuses = prices.refresh(league)
    return {"league": league, "statuses": statuses, **price_status(league)}
