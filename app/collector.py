"""Serialized collection queue, configurable scheduling, and cached geocoding."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from zoneinfo import ZoneInfo

import httpx

from . import db, model
from .blocket import BlocketClient, matches, normalize
from .vehicles import model_key
from .description import DescriptionAnalyzer
from .config import DETAIL_LIMIT, GEOCODING, GEOCODER_USER_AGENT, GEOCODER_URL

logger = logging.getLogger(__name__)


class Collector:
    """Keep one in-process worker; refresh requests never spawn parallel crawls."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.pending = set()
        self.running = None
        self.detail_lock = asyncio.Lock()
        self.api = BlocketClient()
        self.analyzer = DescriptionAnalyzer()
        self.geo_last_request = 0.0
        self.geo_client = httpx.AsyncClient(timeout=15, headers={"User-Agent": GEOCODER_USER_AGENT})

    def enqueue(self, search_id):
        """Coalesce duplicate refresh requests, returning whether one was queued."""
        if search_id in self.pending or self.running == search_id:
            return False
        self.pending.add(search_id)
        self.queue.put_nowait(search_id)
        return True

    async def close(self):
        """Close HTTP pools after the background tasks have stopped."""
        await self.api.close()
        await self.analyzer.close()
        await self.geo_client.aclose()

    async def ensure_listing_details(self, listing_id):
        """Enrich an opened advert without waiting for the batch detail budget.

        Recheck the cache after taking the lock so simultaneous page visits reuse
        the fetched result. Do not change last_seen or price history merely because
        a page was viewed. Failures leave the existing listing available.
        """
        async with self.detail_lock:
            with db.connect() as connection:
                row = connection.execute("SELECT detail,detail_at FROM listings WHERE id=?", (listing_id,)).fetchone()
            if row is None:
                return
            detail = json.loads(row["detail"]) if row["detail"] else {}
            if (detail.get("description_checked") and row["detail_at"]
                    and datetime.fromisoformat(row["detail_at"]) >= datetime.now(timezone.utc) - timedelta(days=1)):
                return
            try:
                fresh = await self.api.detail(listing_id)
                if not fresh.get("description") and detail.get("description"):
                    fresh["description"] = detail["description"]
                with db.connect() as connection:
                    # Collection may have updated the search record during HTTP I/O.
                    row = connection.execute("SELECT raw,data FROM listings WHERE id=?", (listing_id,)).fetchone()
                    if row is None:
                        return
                    previous = json.loads(row["data"])
                    car = normalize(json.loads(row["raw"]), fresh)
                    if car["lat"] is None and previous.get("approximate"):
                        car.update(lat=previous["lat"], lon=previous["lon"], approximate=True)
                    connection.execute("UPDATE listings SET data=?,detail=?,detail_at=? WHERE id=?",
                                       (json.dumps(car), json.dumps(fresh), db.now(), listing_id))
            except (httpx.HTTPError, ValueError):
                logger.warning("Could not update details for opened advert %s", listing_id, exc_info=True)

    async def geocode(self, car):
        """Resolve towns only, globally limited to four requests per minute.

        Successful results are cached indefinitely; misses retry after 30 days.
        Network errors do not enter the cache and never fail a search refresh.
        """
        if not GEOCODING or car["lat"] is not None or not car["location"]:
            return
        location = car["location"].strip()
        with db.connect() as connection:
            cached = connection.execute("SELECT * FROM geocache WHERE location=?", (location,)).fetchone()
        coords = json.loads(cached["data"]) if cached else None
        if cached and not coords:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached["checked_at"])
            if age < timedelta(days=30):
                return
        if not cached or not coords:
            await asyncio.sleep(max(0, 15.1 - (time.monotonic() - self.geo_last_request)))
            self.geo_last_request = time.monotonic()
            try:
                response = await self.geo_client.get(GEOCODER_URL, params={
                    "q": location, "countrycodes": "se", "format": "jsonv2", "limit": 1})
                response.raise_for_status()
                results = response.json()
                coords = {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"])} if results else {}
                with db.connect() as connection:
                    connection.execute("INSERT OR REPLACE INTO geocache VALUES(?,?,?)",
                                       (location, json.dumps(coords), db.now()))
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                logger.warning("Could not geocode town %s", location)
                return
        if coords:
            car.update(coords, approximate=True)

    async def store(self, raw, enrich=False, detail_budget=None):
        """Reuse cached detail for a day; defer excess enrichment to future runs."""
        listing_id = str(raw["id"])
        with db.connect() as connection:
            old = connection.execute("SELECT data,detail,detail_at FROM listings WHERE id=?", (listing_id,)).fetchone()
        detail = json.loads(old["detail"]) if old and old["detail"] else {}
        stale = (not old or not old["detail_at"] or not detail.get("description_checked")
                 or datetime.fromisoformat(old["detail_at"]) < datetime.now(timezone.utc) - timedelta(days=1))
        fresh_detail = None
        warning = None
        if enrich and stale:
            if detail_budget[0] > 0:
                detail_budget[0] -= 1
                try:
                    fresh_detail = await self.api.detail(listing_id)
                    if not fresh_detail or not any(k in fresh_detail for k in ("title", "description", "specifications")):
                        raise ValueError("Detail parser returned no usable fields")
                    if not fresh_detail.get("description"):
                        warning = "Some advert descriptions are unavailable; other details were retained."
                        if detail.get("description"):
                            fresh_detail["description"] = detail["description"]
                    detail = fresh_detail
                except (httpx.HTTPError, ValueError) as error:
                    warning = f"Some advert details could not be fetched ({type(error).__name__}); search information retained."
            else:
                warning = "Some advert details await a future refresh because the per-refresh detail budget was reached."
        car = normalize(raw, detail)
        if old and car["lat"] is None:
            previous = json.loads(old["data"])
            if previous["location"] == car["location"] and previous.get("approximate"):
                car.update(lat=previous["lat"], lon=previous["lon"], approximate=True)
        if enrich:
            await self.geocode(car)
        db.save_listing(car, raw, fresh_detail)
        return car, warning

    async def refresh(self, search_id):
        """Collect purchase matches and independent comparables; preserve partial results."""
        search = db.get_search(search_id)
        if not search:
            return
        config = search["config"]
        started = db.now()
        with db.connect() as connection:
            connection.execute("UPDATE searches SET status='running',last_attempt=?,message='Collecting listings…' WHERE id=?", (started, search_id))
        complete = False
        seen_matches = set()
        warnings = set()
        summaries = []
        budget = [DETAIL_LIMIT]
        try:
            purchase_docs = {}
            async for docs, complete, summary in self.api.pages(config):
                for raw in docs:
                    purchase_docs[str(raw["id"])] = raw
                    car, _ = await self.store(raw)
                    if matches(car, config):
                        seen_matches.add(car["id"])
                        with db.connect() as connection:
                            connection.execute("INSERT OR REPLACE INTO search_listings VALUES(?,?,1)", (search_id, car["id"]))
                summaries = ["Purchase search: " + summary]
            # Fill missing details before refreshing old ones, across all pages.
            with db.connect() as connection:
                detail_dates = {row["id"]: row["detail_at"] or "" for row in
                                connection.execute("SELECT id,detail_at FROM listings")}
            for raw in sorted(purchase_docs.values(), key=lambda r: detail_dates.get(str(r["id"]), "")):
                if model_key(str(raw.get("model", ""))) != model_key(config["model"]):
                    continue
                car, warning = await self.store(raw, True, budget)
                if warning:
                    warnings.add(warning)
                if matches(car, config):
                    seen_matches.add(car["id"])
                    with db.connect() as connection:
                        connection.execute("INSERT OR REPLACE INTO search_listings VALUES(?,?,1)", (search_id, car["id"]))
            broad_complete = False
            async for docs, broad_complete, summary in self.api.pages(config, broad=True):
                for raw in docs:
                    # Purchase adverts already stored with richer location/details.
                    if str(raw["id"]) not in seen_matches:
                        await self.store(raw)
                broad_summary = "Comparables: " + summary
            summaries.append(broad_summary)
            if not broad_complete:
                warnings.add("Comparable collection is incomplete; estimates use the available sample.")
            if not complete:
                warnings.add("Purchase collection is incomplete; previous matches have been retained.")
            await asyncio.to_thread(model.train)
            status = "complete" if complete and broad_complete else "partial"
            with db.connect() as connection:
                # A failed/partial refresh never archives previous matches.
                if complete and broad_complete:
                    connection.execute("UPDATE search_listings SET active=0 WHERE search_id=?", (search_id,))
                    connection.executemany("UPDATE search_listings SET active=1 WHERE search_id=? AND listing_id=?",
                                           [(search_id, item) for item in seen_matches])
                connection.execute("UPDATE searches SET status=?,last_success=?,message=? WHERE id=?",
                                   (status, db.now() if complete else search["last_success"],
                                    " ".join(summaries + sorted(warnings)), search_id))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Search %s refresh failed", search_id)
            with db.connect() as connection:
                connection.execute("UPDATE searches SET status='error',message=? WHERE id=?",
                    (f"Refresh failed: {type(error).__name__}: {str(error)[:400]}. Partial observations were retained; retry available.", search_id))
        finally:
            db.set_state("collection", {"last_finished": db.now(), "search_id": search_id})

    async def worker(self):
        """Process one queued search at a time until application shutdown."""
        while True:
            search_id = await self.queue.get()
            self.pending.discard(search_id)
            self.running = search_id
            try:
                await self.refresh(search_id)
            finally:
                self.running = None
                if db.apply_pending_search_update(search_id):
                    self.enqueue(search_id)
                self.queue.task_done()

    async def scheduler(self):
        """Queue each selected Stockholm hour once per day, without overdue catch-up.

        Persist the slot before queueing so restarts during the same hour do not
        repeat it. Searches run serially and may finish after the selected hour.
        During the autumn clock change, a repeated local hour runs only once.
        """
        while True:
            local_now = datetime.now(ZoneInfo("Europe/Stockholm"))
            slot = local_now.strftime("%Y-%m-%dT%H")
            if (local_now.hour in db.refresh_start_hours()
                    and db.get_state("refresh_schedule", {}).get("last_slot") != slot):
                db.set_state("refresh_schedule", {"last_slot": slot})
                for search in db.searches():
                    self.enqueue(search["id"])
            await asyncio.sleep(60)
