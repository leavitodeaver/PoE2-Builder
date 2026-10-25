"""Local web app: request form, saved requests, PoB code inspection, prices and the rare price
calibration, optimizer runs, and trade-site links for a result's shopping list (made on click).

PoB runs in a worker process (it changes its process's working directory), started on first use.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from builder import settings
from builder.data import GameData
from builder.data.prices import PriceFeed
from builder.data.rare_prices import RarePriceModel
from builder.data.trade import RateLimited, TradeClient, TradeError
from builder.pob import codes
from builder.request import schema
from builder.request.constraints import FACT_FIELDS, compile_request, main_skill, passive_names, passive_rules, player_buttons
from builder.report.calibrate import Calibration
from builder.report.checklist import checklist
from builder.report.shopping import shopping_list, trade_link
from builder.request.schema import BuildRequest
from builder.search.context import make_payload
from builder.web.runs import RESULTS_DIR, RunManager
from builder.web.store import RequestStore
from builder.web import treeart
from builder.web.treedata import synopsis_of, tree_payload

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

    def facts_of(self, xml: str, fields: list[str] = FACT_FIELDS) -> dict:
        """Load a build and read its facts, with nothing else using the engine in between."""
        from builder.pob.pool import EnginePool

        with self._lock:
            if self._pool is None:
                self._pool = EnginePool(1)
            self._pool.broadcast("loadXml", xml=xml)
            return self._pool.broadcast("facts", fields=fields)[0]

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()


data = GameData()
store = RequestStore()
prices = PriceFeed()
engine = Engine()
runs = RunManager(prices, summarize=lambda xml: synopsis_of(engine.facts_of(xml)))
trade = TradeClient()
calibration = Calibration(trade, prices, data)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    engine.close()


app = FastAPI(title="PoE2 Builder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def always_revalidate_page(request, call_next):
    # Browsers otherwise keep showing an old page after an update; revalidating is free locally
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/options")
def options() -> dict:
    passives = passive_names(data)
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


# The passive tree page (interactive tree for the request form and for results) ------------------

_tree_cache: dict = {}
_allocation_cache: dict[str, dict] = {}


@app.get("/api/tree")
def passive_tree() -> dict:
    if "tree" not in _tree_cache:
        _tree_cache["tree"] = tree_payload(data.tree, passive_rules(data))
    return _tree_cache["tree"]


@app.get("/api/tree/art")
def tree_art() -> dict:
    """Where each icon and frame sits in the art images (built from PoB's sprite sheets on first use)."""
    try:
        return treeart.ensure(data)
    except (treeart.ArtUnavailable, OSError, ValueError) as error:
        raise HTTPException(404, f"no tree art: {error}")


@app.get("/api/tree/art/{name}")
def tree_art_image(name: str) -> FileResponse:
    path = treeart.art_dir() / name
    if name not in treeart.FILES or not path.exists():
        raise HTTPException(404, "no such image")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


def radius_view(jewel: dict) -> dict:
    """What the result tree draws for a radius jewel (keeps the payload small: no nodes merely in reach)."""
    return {"socket": jewel["socket"], "name": jewel["name"], "radius": jewel["radius"],
            "centres": jewel.get("centres") or [jewel["socket"]], "affected": jewel.get("affected") or [],
            "unconnected": jewel.get("unconnected") or [], "allowsUnconnected": bool(jewel.get("allowsUnconnected"))}


class AllocatedBody(BaseModel):
    code: str


@app.post("/api/tree/allocated")
def allocated_passives(body: AllocatedBody) -> dict:
    """The passive tree nodes a PoB code allocates, from loading it in the engine."""
    code = body.code.strip()
    if code not in _allocation_cache:
        try:
            xml = codes.decode(code)
        except Exception as error:
            raise HTTPException(400, f"That doesn't look like a PoB code ({error}).")
        facts = engine.facts_of(xml, fields=[])
        if len(_allocation_cache) >= 16:
            _allocation_cache.pop(next(iter(_allocation_cache)))
        _allocation_cache[code] = {
            "ids": [p["id"] for p in facts["passives"]], "className": facts["className"],
            "ascendClassName": facts["ascendClassName"],
            # Socketed radius jewels: their radius and the passives they change or let the tree take
            "radiusJewels": [radius_view(j) for j in facts.get("radiusJewels") or []],
        }
    return _allocation_cache[code]


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
    facts = engine.facts_of(xml)
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


@app.get("/api/settings")
def get_settings() -> dict:
    return {"workers": settings.workers(), "max_workers": settings.max_workers(),
            "recommended_workers": settings.recommended_workers(), "cpu_threads": os.cpu_count()}


class SettingsBody(BaseModel):
    workers: int


@app.post("/api/settings")
def update_settings(body: SettingsBody) -> dict:
    settings.set_workers(body.workers)
    return get_settings()


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


@app.post("/api/runs/{run_id}/request")
def update_run_request(run_id: int, body: dict) -> dict:
    """An edit to the request of a run in progress: the search continues from its best build with it."""
    request = parse_request(body)
    errors = [i for i in compile_request(request, data).issues if i.level == "error"]
    if errors:
        raise HTTPException(422, detail=[{"field": i.field, "message": i.message} for i in errors])
    try:
        return runs.update(run_id, request)
    except KeyError:
        raise HTTPException(404, "no such run")
    except RuntimeError as error:
        raise HTTPException(409, str(error))


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
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("gear"):
        # The shopping list at today's prices (the one saved with the run had the prices of its day)
        request = BuildRequest.model_validate(result["request"])
        try:
            table = prices.table(request.league, refresh=False)
        except Exception:
            table = None
        payload = make_payload(request, table)
        result["shopping"] = shopping_list(result, data, {
            "augments": payload["augment_prices"], "gems": payload["gem_prices"], "league": payload["league"],
            "rare_model": payload["rare_model"], "materials": payload["craft_prices"]}, trade.stats(offline=True))
        result["checklist"] = checklist(result)
    return result


@app.get("/api/prices")
def price_status(league: str | None = None) -> dict:
    table = prices.table(league, refresh=False)
    return {"league": table.league, "fetched_at": table.fetched_at, "exchange": len(table.exchange), "uniques": len(table.uniques)}


@app.post("/api/prices/refresh")
def refresh_prices(league: str | None = None) -> dict:
    league = league or prices.default_league()
    statuses = prices.refresh(league)
    return {"league": league, "statuses": statuses, **price_status(league)}


# Rare price calibration (a user-started job; PLAN.md §7) ---------------------------------------

@app.post("/api/prices/calibrate")
def start_calibration(league: str | None = None) -> dict:
    try:
        return calibration.start(league or prices.default_league())
    except RuntimeError as error:
        raise HTTPException(409, str(error))


@app.post("/api/prices/calibration/cancel")
def cancel_calibration() -> dict:
    calibration.cancel = True
    return {"ok": True}


@app.get("/api/prices/calibration")
def calibration_status(league: str | None = None) -> dict:
    league = league or calibration.status.get("league") or prices.default_league()
    status = {k: v for k, v in calibration.status.items() if k != "samples"}
    return {"job": status, "model": RarePriceModel.load(league).describe(), "league": league}


# Trade links (one search per click, a few at most; never automatic) ----------------------------

class TradeLinkBody(BaseModel):
    item: dict
    part: str = "item"  # "item", "base" (a jewel to craft from), or "rune:<name>" for something in its sockets
    league: str | None = None


@app.post("/api/trade/link")
def make_trade_link(body: TradeLinkBody) -> dict:
    league = body.league or prices.default_league()
    if body.part not in ("item", "base") and not body.part.startswith("rune:"):
        raise HTTPException(422, "part must be 'item', 'base' or 'rune:<name>'")
    try:
        rates = prices.table(league, refresh=False).currency
    except Exception:
        rates = None  # no price data: the link still works, without asking prices
    try:
        return {"league": league, **trade_link(trade, league, body.item, body.part, rates=rates)}
    except RateLimited as error:
        raise HTTPException(429, f"The trade site asks us to wait: try again in {error.wait:.0f}s "
                                 "(its rate limit; your open trade-site tabs count too)")
    except TradeError as error:
        raise HTTPException(502, str(error))
    except (KeyError, TypeError) as error:
        raise HTTPException(422, f"not a shopping-list item ({error})")
