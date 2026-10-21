"""FastAPI web UI and same-origin JSON interface for a personal LAN deployment."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging

import httpx
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator

from . import db, model
from .charts import price_history_chart
from .collector import Collector
from .vehicles import vehicle_group
from .blocket import matches
from .description import FACT_LABELS, add_analysis, matches_facts, description_spans
from .config import LLM_ENABLED, LLM_MODEL, LLM_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
ROOT = Path(__file__).parent
OPTIONS = json.loads((ROOT / "options.json").read_text())
OPTIONS["description_facts"] = FACT_LABELS


class SearchConfig(BaseModel):
    """Validated purchase criteria; inclusive mileage bounds are kilometres.

    The legacy `milage_from/to` field names are retained for API compatibility.
    """

    name: str = Field(min_length=1, max_length=100)
    make: str
    model: str = Field(min_length=1, max_length=80)
    keywords: str = Field(default="", max_length=200)
    price_from: int | None = Field(default=None, ge=0)
    price_to: int | None = Field(default=None, ge=0)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    milage_from: int | None = Field(default=None, ge=0)
    milage_to: int | None = Field(default=None, ge=0)
    location: str = ""
    fuel: str = Field(default="", max_length=60)
    transmission: str = ""
    seller_type: Literal["all", "private", "commercial", "unknown"] = "all"
    description_filters: dict[str, Literal["yes", "no", "unknown"]] = Field(default_factory=dict)
    deal_threshold: float = Field(default=10, ge=0, le=99)

    @model_validator(mode="after")
    def validate_filters(self):
        """Reject unsupported API enum values, empty text, and inverted ranges."""
        if any(key not in FACT_LABELS for key in self.description_filters):
            raise ValueError("Unsupported description filter")
        self.name, self.model = self.name.strip(), self.model.strip()
        self.keywords, self.fuel = self.keywords.strip(), self.fuel.strip()
        if not self.name or not self.model:
            raise ValueError("Name and model cannot be blank")
        for field, choices in (("make", "makes"), ("location", "locations"), ("transmission", "transmissions")):
            value = getattr(self, field)
            if (field == "make" or value) and value not in OPTIONS[choices]:
                raise ValueError(f"Unsupported {field}")
        for low, high in (("price_from", "price_to"), ("year_from", "year_to"), ("milage_from", "milage_to")):
            if getattr(self, low) is not None and getattr(self, high) is not None and getattr(self, low) > getattr(self, high):
                raise ValueError(f"{low} must not exceed {high}")
        return self


@asynccontextmanager
async def lifespan(app):
    """Initialize persistence and own the lifecycle of the single collection worker."""
    db.init()
    await asyncio.to_thread(model.train)
    collector = Collector()
    app.state.collector = collector
    tasks = [asyncio.create_task(collector.worker()), asyncio.create_task(collector.scheduler()),
             asyncio.create_task(collector.analyzer.run())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await collector.close()


app = FastAPI(title="Blocket Tracker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
# A changed script URL prevents browsers from reusing JavaScript from an older build.
templates.env.globals["style_version"] = (ROOT / "static" / "style.css").stat().st_mtime_ns
templates.env.globals["app_js_version"] = (ROOT / "static" / "app.js").stat().st_mtime_ns


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """Reject cross-origin writes to the unauthenticated personal LAN application."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        foreign_origin = origin and urlsplit(origin).netloc != request.headers.get("host")
        if foreign_origin or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def require_search(search_id):
    """Return a saved search or an HTTP 404 response."""
    search = db.get_search(search_id)
    if search is None:
        raise HTTPException(404, "Search not found")
    return search


@app.get("/")
def home(request: Request):
    """Render the saved-search dashboard."""
    return templates.TemplateResponse(request=request, name="home.html", context={"searches": db.searches(), "refresh_start_hours": db.refresh_start_hours()})


class SettingsConfig(BaseModel):
    """Choose daily collection start hours in Europe/Stockholm; empty disables scheduling."""

    refresh_start_hours: list[Annotated[int, Field(strict=True, ge=0, le=23)]] = Field(max_length=24)


@app.get("/settings")
def settings_page(request: Request):
    """Render collection preferences and the configured Ollama connection."""
    return templates.TemplateResponse(request=request, name="settings.html", context={
        "refresh_start_hours": db.refresh_start_hours(), "llm_enabled": LLM_ENABLED,
        "llm_model": LLM_MODEL, "llm_url": LLM_URL})


@app.put("/api/settings")
def save_settings(settings: SettingsConfig):
    """Save daily start hours for the scheduler's next minute check."""
    db.set_state("settings", settings.model_dump())
    return settings.model_dump()


@app.get("/api/settings/ollama")
async def ollama_status():
    """Check connectivity and model availability without triggering inference."""
    result = {"ready": False, "enabled": LLM_ENABLED}
    try:
        async with asyncio.timeout(6), httpx.AsyncClient(timeout=5) as client:
            response = await client.get(LLM_URL + "/api/tags")
            response.raise_for_status()
            models = response.json()["models"]
            names = {item.get("name") for item in models} | {item.get("model") for item in models}
            available = LLM_MODEL in names or (":" not in LLM_MODEL and LLM_MODEL + ":latest" in names)
            result["ready"] = available
            result["message"] = "Ollama is ready; the configured model is installed." if available else "Ollama is reachable, but the configured model is not installed."
    except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        result["message"] = "Could not verify Ollama. Check the service and configured URL."
    return result


@app.get("/searches/new")
def new_search(request: Request):
    """Render the purchase criteria form."""
    return templates.TemplateResponse(request=request, name="form.html", context={"search": None, "options": OPTIONS})


@app.get("/searches/{search_id}/edit")
def edit_search(request: Request, search_id: int):
    """Render the saved search editor."""
    search = require_search(search_id)
    if search.get("pending_config"):
        search["config"] = search["pending_config"]
    return templates.TemplateResponse(request=request, name="form.html", context={"search": search, "options": OPTIONS})


@app.get("/searches/{search_id}")
def search_page(request: Request, search_id: int):
    """Render map and listing results for one saved search."""
    return templates.TemplateResponse(request=request, name="search.html", context={"search": require_search(search_id), "fact_labels": FACT_LABELS})


@app.get("/listings/{listing_id}")
async def listing_page(request: Request, listing_id: str):
    """Refresh stale details on demand, then render the advert and price history."""
    await request.app.state.collector.ensure_listing_details(listing_id)
    item = listing_detail(listing_id)
    return templates.TemplateResponse(request=request, name="listing.html", context={"item": item, "description_parts": description_spans(item), "price_chart": price_history_chart(item["history"])})


@app.get("/api/options")
def options():
    """Expose enum choices verified against the upstream OpenAPI specification."""
    return OPTIONS


@app.get("/api/searches")
def searches():
    """List saved searches with their latest collection outcomes."""
    return db.searches()


@app.post("/api/searches", status_code=201)
async def create_search(config: SearchConfig, request: Request):
    """Persist purchase criteria and queue the initial collection."""
    with db.connect() as connection:
        cursor = connection.execute("INSERT INTO searches(config,created_at) VALUES(?,?)", (config.model_dump_json(), db.now()))
        search_id = cursor.lastrowid
    request.app.state.collector.enqueue(search_id)
    return {"id": search_id}


@app.put("/api/searches/{search_id}")
async def update_search(search_id: int, config: SearchConfig, request: Request):
    """Save criteria now, deferring activation until any running refresh finishes."""
    require_search(search_id)
    collector = request.app.state.collector
    if collector.running == search_id:
        with db.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO pending_search_updates VALUES(?,?)",
                               (search_id, config.model_dump_json()))
        return {"id": search_id, "deferred": True}
    with db.connect() as connection:
        connection.execute("UPDATE searches SET config=?,status='pending',last_attempt=NULL,last_success=NULL,message='' WHERE id=?", (config.model_dump_json(), search_id))
        connection.execute("DELETE FROM search_listings WHERE search_id=?", (search_id,))
    collector.enqueue(search_id)
    return {"id": search_id, "deferred": False}


@app.delete("/api/searches/{search_id}")
async def delete_search(search_id: int, request: Request):
    """Delete a saved search while retaining shared observations and history."""
    require_search(search_id)
    collector = request.app.state.collector
    if collector.running == search_id or search_id in collector.pending:
        raise HTTPException(409, "Wait for this search's queued/running refresh to finish before deleting")
    with db.connect() as connection:
        connection.execute("DELETE FROM searches WHERE id=?", (search_id,))
    return {"deleted": True}


@app.post("/api/searches/{search_id}/refresh", status_code=202)
async def refresh_search(search_id: int, request: Request):
    """Queue a refresh and return immediately; duplicate requests are coalesced."""
    require_search(search_id)
    queued = request.app.state.collector.enqueue(search_id)
    return {"queued": queued}


@app.get("/api/searches/{search_id}/listings")
def search_listings(search_id: int, request: Request, seller: Literal["all", "private", "commercial", "unknown"] = "all",
                    archived: bool = False, deals: bool = False,
                    sort: Literal["discount", "price", "newest"] = "discount",
                    fact: str = "", fact_value: Literal["yes", "no", "unknown"] = "yes"):
    """Return matched cars with optional seller, deal, archive, and sort controls."""
    if fact and fact not in FACT_LABELS:
        raise HTTPException(422, "Unsupported description finding")
    fact_filters = {fact: fact_value} if fact else {}
    for key in FACT_LABELS:
        value = request.query_params.get("fact_" + key)
        if value is not None:
            if value not in {"yes", "no", "unknown"}:
                raise HTTPException(422, "Unsupported description finding value")
            fact_filters[key] = value
    search = require_search(search_id)
    items = [item for item in db.listings(search_id, seller, archived) if matches(item, search["config"])]
    items = [item for item in items if matches_facts(item, search["config"].get("description_filters", {}))]
    if fact_filters:
        items = [item for item in items if matches_facts(item, fact_filters)]
    for item in items:
        difference = round(item["discount"], 8) if item["discount"] is not None else None
        threshold = search["config"]["deal_threshold"]
        item["highlight"] = difference is not None and difference >= threshold and item["active"]
        item["above_estimate"] = difference is not None and difference < 0 and -difference >= threshold and item["active"]
    if deals:
        items = [item for item in items if item["highlight"] or item["above_estimate"]]
    if sort == "discount":
        items.sort(key=lambda c: c["discount"] if c["discount"] is not None else -float("inf"), reverse=True)
    elif sort == "price":
        items.sort(key=lambda c: c["price"] if c["price"] is not None else float("inf"))
    else:
        items.sort(key=lambda c: c["first_seen"], reverse=True)
    metadata = model_status()
    key = vehicle_group(search["config"]["make"], search["config"]["model"])
    metadata["search_group"] = metadata.get("groups", {}).get(key)
    for candidate in metadata.get("candidates", []):
        candidate["search_metrics"] = candidate.get("groups", {}).get(key)
    return {"search": search, "listings": items, "model": metadata}


@app.get("/api/listings/{listing_id}")
def listing_detail(listing_id: str):
    """Read the normalized advert, available specifications, and price history."""
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        history = connection.execute("SELECT observed_at,price FROM price_history WHERE listing_id=? ORDER BY id", (listing_id,)).fetchall()
        analysis_row = connection.execute("SELECT result FROM description_analysis WHERE listing_id=?", (listing_id,)).fetchone()
        prediction = connection.execute("SELECT estimate,comparable_count FROM predictions WHERE listing_id=?", (listing_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Listing not found")
    return {**add_analysis(json.loads(row["data"]), json.loads(analysis_row[0]) if analysis_row else None), "first_seen": row["first_seen"], "last_seen": row["last_seen"],
            "detail_at": row["detail_at"], "history": [dict(h) for h in history],
            "estimate": prediction["estimate"] if prediction else None,
            "comparable_count": prediction["comparable_count"] if prediction else None}


@app.get("/api/model")
def model_status():
    """Return validation metrics without the larger coefficient inspection payload."""
    return {k: v for k, v in db.get_state("model", {"status": "insufficient_data", "sample_size": 0}).items() if k not in {"coefficients", "signature"}}


@app.get("/api/status")
def status(request: Request):
    """Expose queued/running refreshes and collection/model status."""
    collector = request.app.state.collector
    return {"running": collector.running, "queued": sorted(collector.pending), "searches": db.searches(),
            "model": model_status(), "refresh_start_hours": db.refresh_start_hours(),
            "description_analysis": db.get_state("description_analysis", {"enabled": LLM_ENABLED, "model": LLM_MODEL})}


@app.get("/health")
def health():
    """Check database availability for the container health probe."""
    with db.connect() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok"}
