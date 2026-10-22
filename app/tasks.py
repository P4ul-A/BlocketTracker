"""Persistent search-run timings shared by collection and background LLM workers.

Each refresh owns one task, including its purchase and comparable listings. Shared
LLM/model work can contribute time to several tasks. Step durations accumulate
active batches; the parent duration includes queueing between stages and retries.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json

from . import db

STEP_LABELS = {"collection": "Listing collection", "training": "Training price model",
               "analysis": "LLM analysis", "retraining": "Retraining model"}


def timestamp():
    """Keep subsecond precision for short tasks without changing listing timestamps."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def seconds(start, end):
    """Return nonnegative wall-clock seconds between two stored timestamps."""
    return max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def create(name, search_id=None, kind="search", steps=None):
    """Snapshot the search name and queue its stages, retaining history after deletion."""
    with db.connect() as connection:
        now = timestamp()
        task_id = connection.execute(
            "INSERT INTO task_runs(search_id,name,kind,created_at,updated_at) VALUES(?,?,?,?,?)",
            (search_id, name, kind, now, now)).lastrowid
        connection.executemany("INSERT INTO task_steps(task_id,name,position) VALUES(?,?,?)",
                               [(task_id, name, index) for index, name in enumerate(steps or STEP_LABELS)])
    return task_id


def update_step(task_id, name, status, message="", at=None):
    """Start, pause, or finish a stage while accumulating only its active time."""
    now = at or timestamp()
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM task_steps WHERE task_id=? AND name=?", (task_id, name)).fetchone()
        if row is None:
            return
        duration = row["duration_seconds"] + (seconds(row["active_at"], now) if row["active_at"] else 0)
        terminal = status in {"complete", "partial", "skipped", "error", "interrupted"}
        connection.execute("""UPDATE task_steps SET status=?,started_at=?,finished_at=?,active_at=?,
            duration_seconds=?,message=? WHERE task_id=? AND name=?""",
            (status, row["started_at"] or (now if status == "running" else None), now if terminal else None,
             now if status == "running" else None, duration, message, task_id, name))
        if status == "running":
            connection.execute("UPDATE task_runs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?",
                               (now, task_id))
        else:
            connection.execute("""UPDATE task_runs SET status='waiting' WHERE id=? AND finished_at IS NULL
                AND started_at IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM task_steps WHERE task_id=? AND status='running')""", (task_id, task_id))
        connection.execute("UPDATE task_runs SET updated_at=? WHERE id=?", (now, task_id))


@contextmanager
def work(task_ids, name, message="", afterwards="complete"):
    """Time an actual worker operation; retryable failures leave the stage waiting."""
    for task_id in task_ids:
        update_step(task_id, name, "running", message)
    try:
        yield
    except BaseException as error:
        for task_id in task_ids:
            update_step(task_id, name, "waiting" if afterwards == "waiting" else "error",
                        f"{type(error).__name__}: {str(error)[:240]}")
        raise
    else:
        for task_id in task_ids:
            update_step(task_id, name, afterwards, message)


def attach_listings(task_id, listing_ids):
    """Associate collected purchase/comparable listings with their run's LLM stage."""
    with db.connect() as connection:
        connection.executemany("INSERT OR IGNORE INTO task_listings VALUES(?,?)",
                               [(task_id, listing_id) for listing_id in listing_ids])


def finish(task_id, status="complete", message="", at=None):
    """Close a run, stop active timers, and release its temporary listing membership."""
    with db.connect() as connection:
        steps = connection.execute("SELECT name,status,started_at FROM task_steps WHERE task_id=?", (task_id,)).fetchall()
    for step in steps:
        if step["status"] in {"waiting", "running"}:
            update_step(task_id, step["name"], status if step["started_at"] else "skipped", message, at=at)
    with db.connect() as connection:
        now = at or timestamp()
        connection.execute("UPDATE task_runs SET status=?,finished_at=?,updated_at=?,message=? WHERE id=?",
                           (status, now, now, message, task_id))
        connection.execute("DELETE FROM task_listings WHERE task_id=?", (task_id,))


def interrupt_unfinished(restarted=True):
    """Close unfinished runs on shutdown/startup so stale tasks never appear running.

    After an unclean exit, the exact stop time is unavailable. The message says so;
    recorded active time ends at its last persisted activity, excluding downtime.
    """
    with db.connect() as connection:
        rows = connection.execute("SELECT id,updated_at FROM task_runs WHERE finished_at IS NULL").fetchall()
    for row in rows:
        message = ("App restarted; timing ends at the last recorded activity because the stop time is unknown."
                   if restarted else "App stopped before this task finished.")
        finish(row["id"], "interrupted", message, at=row["updated_at"] if restarted else None)


def analysis_groups():
    """Group listings by runs ready for LLM work, including runs with no listings."""
    with db.connect() as connection:
        rows = connection.execute("""SELECT t.id,m.listing_id FROM task_runs t
            LEFT JOIN task_listings m ON t.id=m.task_id
            LEFT JOIN task_steps s ON s.task_id=t.id AND s.name='training'
            WHERE t.finished_at IS NULL AND (s.status='complete' OR t.kind='backfill')""").fetchall()
    groups = {}
    for row in rows:
        members = groups.setdefault(row["id"], set())
        if row["listing_id"] is not None:
            members.add(row["listing_id"])
    return groups


def retry_retraining():
    """Find failed final training operations that must be retried before completion."""
    with db.connect() as connection:
        return {row[0] for row in connection.execute("""SELECT s.task_id FROM task_steps s
            JOIN task_runs t ON t.id=s.task_id WHERE t.finished_at IS NULL
            AND s.name='retraining' AND s.status='error'""")}


def backfill_task():
    """Reuse one background task for saved listings outside current search runs."""
    with db.connect() as connection:
        row = connection.execute("SELECT id FROM task_runs WHERE kind='backfill' AND finished_at IS NULL").fetchone()
    return row[0] if row else create("Saved listings: LLM backfill", kind="backfill", steps=("analysis", "retraining"))


def finish_analysis(task_id, pending, errors, total, error=""):
    """Publish LLM progress and close a search once its final retraining has ended."""
    done = total - pending - errors
    message = f"{done}/{total} descriptions ready"
    if pending or errors:
        update_step(task_id, "analysis", "waiting",
                    message + (f"; {errors} awaiting retry. {error}" if errors else ""))
        return
    update_step(task_id, "analysis", "complete", message if total else "No descriptions available to analyze.")
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM task_steps WHERE task_id=? AND name='retraining'", (task_id,)).fetchone()
        collection = connection.execute("SELECT status,message FROM task_steps WHERE task_id=? AND name='collection'", (task_id,)).fetchone()
    if row:
        update_step(task_id, "retraining", "complete" if row["started_at"] else "skipped",
                    "Model refreshed after LLM batches." if row["started_at"] else "No new LLM findings; no retraining needed.")
    finish(task_id, "partial" if collection and collection[0] == "partial" else "complete",
           collection["message"] if collection else "Saved descriptions processed.")


def log():
    """Return live timings and saved search names for grouping runs by their owner.

    Search IDs and snapshotted run names retain deleted searches' history. System
    tasks have no search owner because their work uses the shared listing pool.
    """
    now = timestamp()
    with db.connect() as connection:
        searches = [{"id": row["id"], "name": json.loads(row["config"])["name"]}
                    for row in connection.execute("SELECT id,config FROM searches ORDER BY id")]
        runs = connection.execute("""SELECT * FROM task_runs WHERE finished_at IS NULL OR id IN
            (SELECT id FROM task_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 50)
            ORDER BY finished_at IS NOT NULL,status!='running',id DESC""").fetchall()
        ids = [row["id"] for row in runs]
        placeholders = ",".join("?" for _ in ids)
        steps = (connection.execute(f"SELECT * FROM task_steps WHERE task_id IN ({placeholders}) ORDER BY task_id,position",
                                    ids).fetchall() if ids else [])
    grouped = {}
    for row in steps:
        step = dict(row)
        step["label"] = STEP_LABELS[step["name"]]
        step["duration_seconds"] += seconds(step["active_at"], now) if step["active_at"] else 0
        grouped.setdefault(step["task_id"], []).append(step)
    result = []
    for row in runs:
        task = dict(row)
        task["duration_seconds"] = seconds(task["started_at"], task["finished_at"] or now) if task["started_at"] else 0
        task["queue_seconds"] = seconds(task["created_at"], task["started_at"] or task["finished_at"] or now)
        task["steps"] = grouped.get(task["id"], [])
        result.append(task)
    return {"tasks": result, "searches": searches, "updated_at": now}
