"""Blocket REST adapter and conservative normalization of observed response fields.

The REST API uses `models` for makes and the spelling `milage_*` for mileage.
Search metadata exposes total matches and an upstream page cap (currently 50).
"""

import asyncio
import math
import re

import httpx
from bs4 import BeautifulSoup

from .config import API_BASE, MAX_PAGES, REQUEST_DELAY
from .vehicles import name_key, model_key, model_query


class BlocketClient:
    """Reuse connections and limit sequential API requests with bounded retries."""

    def __init__(self):
        self.client = httpx.AsyncClient(base_url=API_BASE, timeout=30, headers={"User-Agent": "BlocketTracker/1.0"})
        self.request_lock = asyncio.Lock()

    async def close(self):
        """Release the shared HTTP connection pool."""
        await self.client.aclose()

    async def get(self, path, params, as_text=False):
        """Share the request rate limit between collection and opened listing pages."""
        async with self.request_lock:
            return await self._get(path, params, as_text)

    async def _get(self, path, params, as_text=False):
        """Retry transport failures, throttling, and temporary server errors only."""
        for attempt in range(3):
            await asyncio.sleep(REQUEST_DELAY)
            try:
                response = await self.client.get(path, params=params)
                response.raise_for_status()
                if as_text:
                    return response.text
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Blocket returned an unexpected response shape")
                return payload
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 429 and error.response.status_code < 500:
                    raise
                if attempt == 2:
                    raise
                retry_after = error.response.headers.get("Retry-After", "")
                delay = min(60, max(2 ** attempt, int(retry_after))) if retry_after.isdigit() else 2 ** attempt
                await asyncio.sleep(delay)
            except (httpx.TransportError, ValueError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def pages(self, config, broad=False):
        """Yield search pages with a final completeness flag and explanatory text.

        Broad queries keep only make/model. Purchase filters are applied separately
        so budget and seller selection do not bias the training sample.
        """
        params = {"query": " ".join(filter(None, [model_query(config["model"]), "" if broad else config.get("keywords", "")])),
                  "models": config["make"], "sort_order": "PUBLISHED_DESC"}
        if not broad:
            for key in ("price_from", "price_to", "year_from", "year_to", "milage_from", "milage_to"):
                if config.get(key) is not None:
                    params[key] = config[key]
            # Saved bounds are km; the upstream API takes whole Swedish mil.
            # Round outward, then enforce the exact km bounds in matches().
            if config.get("milage_from") is not None:
                params["milage_from"] = config["milage_from"] // 10
            if config.get("milage_to") is not None:
                params["milage_to"] = (config["milage_to"] + 9) // 10
            if config.get("location"):
                params["locations"] = config["location"]
            if config.get("transmission"):
                params["transmissions"] = config["transmission"]
        seen = set()
        for page in range(1, MAX_PAGES + 1):
            payload = await self.get("/v1/search/car", {**params, "page": page})
            docs = payload.get("docs")
            if not isinstance(docs, list):
                raise ValueError("Blocket search response is missing docs[]")
            if any(not isinstance(d, dict) or not str(d.get("id", "")).isdigit() for d in docs):
                raise ValueError("Blocket returned an advert without a numeric ID")
            ids = {str(d["id"]) for d in docs}
            if docs and not ids - seen:
                raise ValueError("Blocket repeated a page; collection is incomplete")
            seen.update(ids)
            meta = payload.get("metadata", {})
            total = meta.get("result_size", {}).get("match_count", payload.get("total"))
            last = meta.get("paging", {}).get("last")
            reached_total = isinstance(total, int) and len(seen) >= total
            ended = not docs or meta.get("is_end_of_paging") is True or (last is not None and page >= int(last))
            done = reached_total or ended or page == MAX_PAGES
            complete = reached_total or (ended and (total is None or len(seen) >= total))
            message = f"Retrieved {len(seen)} of {total if total is not None else 'unknown'} upstream matches."
            if done and not complete:
                message += " Upstream/configured page limit or changing results prevented full retrieval."
            yield docs, done and complete, message
            if done:
                break

    async def detail(self, listing_id):
        """Fetch API details, falling back to advert HTML for omitted descriptions.

        The upstream parser currently misses description sections in the newer
        page layout. Failure of the HTML fallback must not discard usable API data.
        """
        detail = await self.get("/v1/ad/car", {"id": listing_id})
        if not str(detail.get("description") or "").strip():
            try:
                html = await self.get(f"https://www.blocket.se/mobility/item/{int(listing_id)}", {}, as_text=True)
                detail["description"] = parse_description(html)
            except (httpx.HTTPError, ValueError):
                detail["description"] = ""
        detail["description_checked"] = True
        return detail


def parse_description(html):
    """Extract only the advert description section, preserving paragraph breaks.

    Locate the heading rather than an exact CSS class list, which changes between
    Blocket releases. Do not substitute SEO metadata that may be truncated or
    concatenate the dealer biography and unrelated page text.
    """
    soup = BeautifulSoup(html, "html.parser")
    heading = next((h for h in soup.find_all(["h2", "h3"])
                    if h.get_text(" ", strip=True).casefold() == "beskrivning"), None)
    section = heading.find_parent("section") if heading else None
    if section is None:
        return ""
    heading.decompose()
    for element in section.find_all(["script", "style", "button"]):
        element.decompose()
    for element in section.find_all("br"):
        element.replace_with("\n")
    for element in section.find_all(["p", "li"]):
        element.insert_after("\n")
    text = section.get_text()
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()


def number(value):
    """Parse a finite nonnegative number without silently combining ranges."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        text = value.replace("\u00a0", "").replace(" ", "")
        match = re.fullmatch(r"(\d+(?:[.,]\d+)?)(?:kr|SEK|mil|km|hk)?", text, re.I)
        if match:
            return float(match[1].replace(",", "."))
    return None


def seller_type(raw, detail):
    """Classify explicit seller signals; never infer private from absent dealer data."""
    value = str(raw.get("dealer_segment") or raw.get("seller_type") or "").casefold()
    commercial = {"företag", "dealer", "commercial", "business", "company"}
    private = {"privat", "private", "privatperson"}
    if value in commercial:
        return "commercial"
    if value in private:
        return "private"
    if raw.get("organisation_name"):
        return "commercial"
    # The upstream detail parser assumes private if dealer markup is absent.
    # Trust its positive dealer detection, but retain unknown for that fallback.
    if str(detail.get("seller_type", "")).casefold() in commercial:
        return "commercial"
    return "unknown"


def safe_url(value):
    """Allow only HTTP(S) URLs in user-facing external links and images."""
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else None


def normalize(raw, detail=None):
    """Normalize observed search/detail fields, converting known mileage units to km."""
    detail = detail or {}
    specs = dict(detail.get("specifications") or {})
    mileage = number(raw.get("mileage"))
    unit = str(raw.get("mileage_unit", "")).upper()
    if unit in {"SCANDINAVIAN_MILE", "MIL"}:
        mileage = mileage * 10 if mileage is not None else None
    elif unit not in {"KILOMETER", "KILOMETERS", "KM", "KILOMETRE", "KILOMETRES"}:
        mileage = None
    if mileage is None and isinstance(detail.get("mileage"), str):
        value = detail["mileage"]
        if value.strip().lower().endswith("mil"):
            parsed = number(value)
            mileage = parsed * 10 if parsed is not None else None
        elif value.strip().lower().endswith("km"):
            parsed = number(value)
            mileage = parsed
    if "Miltal" in specs:
        del specs["Miltal"]
        specs["Mileage (km)"] = f"{mileage:,.0f} km".replace(",", " ") if mileage is not None else "Unknown"
    coords = raw.get("coordinates") or {}
    lat, lon = number(coords.get("lat")), number(coords.get("lon"))
    if lat is None or lon is None or not (54 <= lat <= 70 and 10 <= lon <= 25):
        lat, lon = None, None
    location = raw.get("location", "")
    if isinstance(location, dict):
        location = location.get("name", "")
    price = raw.get("price")
    currency = price.get("currency_code", "SEK") if isinstance(price, dict) else "SEK"
    price = number(price.get("amount")) if isinstance(price, dict) else number(price)
    if currency != "SEK":
        price = None
    images = raw.get("image_urls") or [(raw.get("image") or {}).get("url")]
    return {
        "id": str(raw["id"]), "title": str(raw.get("heading") or detail.get("title") or "Car advert"),
        "make": str(raw.get("make") or specs.get("Märke") or "").strip(),
        "model": str(raw.get("model") or specs.get("Modell") or "").strip(),
        "variant": str(raw.get("model_specification") or detail.get("subtitle") or ""),
        "year": number(raw.get("year")) or number(detail.get("model_year")),
        "mileage": mileage, "mileage_unit": "km", "price": price,
        "fuel": str(raw.get("fuel") or detail.get("fuel") or "unknown"),
        "transmission": str(raw.get("transmission") or detail.get("transmission") or "unknown"),
        "power": number(raw.get("engine_effect")) or number(specs.get("Effekt")),
        "seller_type": seller_type(raw, detail), "seller_name": str(raw.get("organisation_name") or ""),
        "location": str(location), "lat": lat, "lon": lon, "approximate": False,
        "url": safe_url(raw.get("canonical_url")) or f"https://www.blocket.se/mobility/item/{raw['id']}",
        "images": [u for x in images if (u := safe_url(x))],
        "description": str(detail.get("description") or raw.get("description") or ""),
        "equipment": detail.get("equipment") or [], "specifications": specs,
        "mileage_source_unit": raw.get("mileage_unit"),
    }


def matches(car, config):
    """Apply exact make/model and local-only fuel/seller filters conservatively."""
    if name_key(car["make"]) != name_key(config["make"]):
        return False
    if model_key(config["model"]) != model_key(car["model"]):
        return False
    if config.get("seller_type", "all") not in {"all", car["seller_type"]}:
        return False
    if config.get("fuel") and config["fuel"].casefold() not in car["fuel"].casefold():
        return False
    for field, low, high in (("price", "price_from", "price_to"), ("year", "year_from", "year_to"),
                             ("mileage", "milage_from", "milage_to")):
        value = car.get(field)
        if config.get(low) is not None and (value is None or value < config[low]):
            return False
        if config.get(high) is not None and (value is None or value > config[high]):
            return False
    return True
