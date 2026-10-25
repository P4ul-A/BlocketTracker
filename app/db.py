"""SQLite persistence. Each operation owns its connection and transaction."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3

from .config import DATA_DIR, LLM_ENABLED, PRICE_HISTORY_LIMIT


def now():
    """Return an ISO timestamp in UTC for sortable database dates."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open a short transaction with foreign keys and a busy timeout."""
    connection = sqlite3.connect(DATA_DIR / "tracker.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init():
    """Initialize tables and upgrade existing databases before workers start."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY, config TEXT NOT NULL,
                created_at TEXT NOT NULL, last_attempt TEXT, last_success TEXT,
                status TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, raw TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                detail TEXT, detail_at TEXT
            );
            CREATE TABLE IF NOT EXISTS search_listings (
                search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE,
                listing_id TEXT REFERENCES listings(id) ON DELETE CASCADE,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(search_id, listing_id)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY, listing_id TEXT REFERENCES listings(id),
                observed_at TEXT NOT NULL, price REAL
            );
            CREATE INDEX IF NOT EXISTS history_listing ON price_history(listing_id, id);
            CREATE TABLE IF NOT EXISTS geocache (
                location TEXT PRIMARY KEY, data TEXT NOT NULL, checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS predictions (
                listing_id TEXT PRIMARY KEY REFERENCES listings(id),
                estimate REAL NOT NULL, comparable_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS description_analysis (
                listing_id TEXT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
                result TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favourites (
                listing_id TEXT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
                saved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_search_updates (
                search_id INTEGER PRIMARY KEY REFERENCES searches(id) ON DELETE CASCADE,
                config TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, search_id INTEGER, name TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS task_steps (
                task_id INTEGER REFERENCES task_runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                started_at TEXT, finished_at TEXT, active_at TEXT,
                duration_seconds REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(task_id, name)
            );
            CREATE TABLE IF NOT EXISTS task_listings (
                task_id INTEGER REFERENCES task_runs(id) ON DELETE CASCADE,
                listing_id TEXT REFERENCES listings(id) ON DELETE CASCADE,
                PRIMARY KEY(task_id, listing_id)
            );
        """)
        migrate_task_timestamps(db)
        if db.execute("PRAGMA user_version").fetchone()[0] < 2:
            migrate_mileage_to_km(db)
            db.execute("PRAGMA user_version=2")
        if db.execute("PRAGMA user_version").fetchone()[0] < 3:
            # Preserve the last confirmed endpoint from the changes-only history.
            # Earlier unchanged sightings were never stored and cannot be recovered.
            db.execute("""INSERT INTO price_history(listing_id,observed_at,price)
                SELECT l.id,l.last_seen,h.price FROM listings l
                JOIN price_history h ON h.id=(
                    SELECT MAX(id) FROM price_history WHERE listing_id=l.id)
                WHERE h.observed_at < l.last_seen
                    AND h.price=json_extract(l.data,'$.price')""")
            db.execute("PRAGMA user_version=3")
        for row in db.execute("""SELECT listing_id FROM price_history
                GROUP BY listing_id HAVING COUNT(*) > ?""", (PRICE_HISTORY_LIMIT,)).fetchall():
            compact_price_history(db, row["listing_id"])
        db.execute("UPDATE searches SET status='interrupted', message='Refresh interrupted by restart; queued for retry.' WHERE status='running'")
        # Previous condition categories do not mean the same as drivability.
        # Retire those filters explicitly rather than silently changing their meaning.
        for table, id_column in (("searches", "id"), ("pending_search_updates", "search_id")):
            for row in db.execute(f"SELECT {id_column},config FROM {table}").fetchall():
                config = json.loads(row["config"])
                filters = config.get("description_filters", {})
                obsolete = {"needs_repair", "does_not_start", "engine_issues", "odometer_fault"} & filters.keys()
                if obsolete:
                    for key in obsolete:
                        del filters[key]
                    config["description_filter_notice"] = "Retired description filters were removed. Review the description filters for this search."
                    db.execute(f"UPDATE {table} SET config=? WHERE {id_column}=?", (json.dumps(config), row[id_column]))
        pending = [row[0] for row in db.execute("SELECT search_id FROM pending_search_updates")]
    for search_id in pending:
        apply_pending_search_update(search_id)


def migrate_task_timestamps(db):
    """Add the task heartbeat column used to recover interrupted runs.

    Older task logs lack this column. Preserve their history and use the latest
    recorded run or stage activity, rather than counting downtime as work.
    """
    columns = {row["name"] for row in db.execute("PRAGMA table_info(task_runs)")}
    if "updated_at" not in columns:
        db.execute("ALTER TABLE task_runs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        db.execute("""UPDATE task_runs SET updated_at=MAX(
            COALESCE(finished_at,created_at), COALESCE(started_at,created_at),
            COALESCE((SELECT MAX(COALESCE(finished_at,active_at,started_at))
                      FROM task_steps WHERE task_id=task_runs.id),created_at))""")


def apply_pending_search_update(search_id):
    """Activate the latest deferred edit atomically after collection or restart."""
    with connect() as db:
        row = db.execute("SELECT config FROM pending_search_updates WHERE search_id=?", (search_id,)).fetchone()
        if row is None:
            return False
        db.execute("UPDATE searches SET config=?,status='pending',last_attempt=NULL,last_success=NULL,message='Saved changes applied; refresh queued.' WHERE id=?",
                   (row[0], search_id))
        db.execute("DELETE FROM search_listings WHERE search_id=?", (search_id,))
        db.execute("DELETE FROM pending_search_updates WHERE search_id=?", (search_id,))
    return True


def migrate_mileage_to_km(db):
    """Convert saved mil values atomically, preserving raw responses and prices.

    The model's mileage scale changes by the same factor, so existing predictions
    and coefficients remain valid. The schema version prevents double conversion.
    """
    for row in db.execute("SELECT id,config FROM searches").fetchall():
        config = json.loads(row["config"])
        for key in ("milage_from", "milage_to"):
            if config.get(key) is not None:
                config[key] *= 10
        db.execute("UPDATE searches SET config=? WHERE id=?", (json.dumps(config), row["id"]))
    for row in db.execute("SELECT id,data FROM listings").fetchall():
        data = json.loads(row["data"])
        if data.get("mileage") is not None:
            data["mileage"] *= 10
        data["mileage_unit"] = "km"
        specs = data.get("specifications", {})
        if "Miltal" in specs:
            del specs["Miltal"]
            mileage = data.get("mileage")
            specs["Mileage (km)"] = f"{mileage:,.0f} km".replace(",", " ") if mileage is not None else "Unknown"
        db.execute("UPDATE listings SET data=? WHERE id=?", (json.dumps(data), row["id"]))
    row = db.execute("SELECT value FROM state WHERE key='model'").fetchone()
    if row:
        metadata = json.loads(row[0])
        metadata["mileage_unit"] = "km"
        db.execute("UPDATE state SET value=? WHERE key='model'", (json.dumps(metadata),))


def get_state(key, default=None):
    """Read a JSON application status value."""
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(key, value):
    """Persist a JSON status value atomically."""
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))


def searches():
    """Return collection counts and explicit LLM status for every saved search."""
    with connect() as db:
        rows = db.execute("""SELECT s.*, u.config AS pending_config, (SELECT count(*) FROM search_listings m
            WHERE m.search_id=s.id AND m.active=1) AS count FROM searches s
            LEFT JOIN pending_search_updates u ON u.search_id=s.id ORDER BY s.id""").fetchall()
    result = [{**dict(row), "config": json.loads(row["config"]),
               "pending_config": json.loads(row["pending_config"]) if row["pending_config"] else None} for row in rows]
    from .description import matches_facts
    for search in result:
        filters = search["config"].get("description_filters", {})
        search["llm_pending"] = 0
        search["llm_errors"] = 0
        search["llm_ready"] = 0
        search["llm_no_description"] = 0
        if filters or LLM_ENABLED:
            items = listings(search["id"])
            if filters:
                search["count"] = sum(matches_facts(item, filters) for item in items)
            if LLM_ENABLED:
                states = [item["analysis"]["status"] for item in items]
                search["llm_pending"] = states.count("pending")
                search["llm_errors"] = states.count("error")
                search["llm_ready"] = states.count("ready")
                search["llm_no_description"] = states.count("no_description")
        if not LLM_ENABLED:
            search["llm_state"] = "disabled"
            search["llm_label"] = "LLM analysis is disabled."
        else:
            search["llm_state"] = ("pending" if search["llm_pending"] else "retry" if search["llm_errors"]
                                   else "ready" if search["llm_ready"] else "unavailable")
            search["llm_label"] = (
                f"LLM: {search['llm_ready']} analyzed, {search['llm_pending']} pending, "
                f"{search['llm_errors']} awaiting retry, {search['llm_no_description']} without a description."
            )
    return result


def get_search(search_id):
    """Look up a saved search, returning None when it no longer exists."""
    return next((s for s in searches() if s["id"] == search_id), None)


def compact_price_history(db, listing_id):
    """Bound history by thinning older observations, retaining long-term trends.

    Keep the first point and the newest half of the limit at full resolution.
    Remove older points with the smallest triangle area between their neighbours,
    preferring closely spaced points when areas tie. This favours meaningful
    price changes and spreads unchanged-price confirmations across time. Missing
    price boundaries are retained preferentially. Retained points are always
    actual observations, never averages or invented confirmations.
    """
    rows = db.execute("""SELECT id,observed_at,price FROM price_history
        WHERE listing_id=? ORDER BY observed_at,id""", (listing_id,)).fetchall()
    if len(rows) <= PRICE_HISTORY_LIMIT:
        return
    points = [(row["id"], datetime.fromisoformat(row["observed_at"]).timestamp(), row["price"])
              for row in rows]

    def importance(index):
        """Rank an older point by the price shape lost if it is removed."""
        _, before_time, before_price = points[index - 1]
        _, observed, price = points[index]
        _, after_time, after_price = points[index + 1]
        span = after_time - before_time
        if None in (before_price, price, after_price):
            area = 0 if before_price == price == after_price else float("inf")
        else:
            area = abs((price - before_price) * span
                       - (after_price - before_price) * (observed - before_time))
        return area, span

    removed = []
    while len(points) > PRICE_HISTORY_LIMIT:
        older = range(1, len(points) - PRICE_HISTORY_LIMIT // 2)
        index = min(older, key=importance)
        removed.append((points.pop(index)[0],))
    db.executemany("DELETE FROM price_history WHERE id=?", removed)


def save_listing(data, raw, detail=None, record_observation=True):
    """Save an advert and record every collection sighting, even unchanged prices.

    Cached-detail enrichment and duplicate processing set record_observation=False
    so they update advert data without inventing a sighting or price confirmation.
    Thin older history to PRICE_HISTORY_LIMIT points, preserving long-term trends.
    """
    timestamp = now()
    with connect() as db:
        db.execute("""INSERT INTO listings(id,data,raw,first_seen,last_seen,detail,detail_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            data=excluded.data, raw=excluded.raw,
            last_seen=CASE WHEN ? THEN excluded.last_seen ELSE listings.last_seen END,
            detail=COALESCE(excluded.detail,listings.detail),
            detail_at=COALESCE(excluded.detail_at,listings.detail_at)""",
            (data["id"], json.dumps(data), json.dumps(raw), timestamp, timestamp,
             json.dumps(detail) if detail is not None else None, timestamp if detail is not None else None,
             record_observation))
        if record_observation:
            db.execute("INSERT INTO price_history(listing_id,observed_at,price) VALUES(?,?,?)",
                       (data["id"], timestamp, data.get("price")))
            compact_price_history(db, data["id"])


def listings(search_id, seller="all", archived=False):
    """Read matched adverts with model predictions, preserving unknown sellers."""
    with connect() as db:
        rows = db.execute("""SELECT l.*,m.active,p.estimate,p.comparable_count,a.result AS analysis_result,
            EXISTS(SELECT 1 FROM favourites f WHERE f.listing_id=l.id) AS favourite
            FROM search_listings m JOIN listings l ON l.id=m.listing_id
            LEFT JOIN predictions p ON p.listing_id=l.id
            LEFT JOIN description_analysis a ON a.listing_id=l.id
            WHERE m.search_id=? AND (? OR m.active=1) ORDER BY l.last_seen DESC""",
                          (search_id, archived)).fetchall()
    from .description import add_analysis
    result = []
    for row in rows:
        item = add_analysis(json.loads(row["data"]), json.loads(row["analysis_result"]) if row["analysis_result"] else None)
        if seller != "all" and item["seller_type"] != seller:
            continue
        estimate = row["estimate"]
        item.update(favourite=bool(row["favourite"]), first_seen=row["first_seen"], last_seen=row["last_seen"], active=bool(row["active"]),
                    estimate=estimate, comparable_count=row["comparable_count"],
                    discount=(1 - item["price"] / estimate) * 100 if estimate and item.get("price") else None)
        result.append(item)
    return result


def refresh_start_hours():
    """Return sorted daily start hours, defaulting legacy installations to midnight."""
    return sorted(set(get_state("settings", {}).get("refresh_start_hours", [0])))


def favourite_ids():
    """Return unique favourites, newest first, independent of saved searches."""
    with connect() as connection:
        return [row[0] for row in connection.execute(
            "SELECT listing_id FROM favourites ORDER BY saved_at DESC, listing_id")]
