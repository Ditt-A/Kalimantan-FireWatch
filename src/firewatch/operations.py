"""Operational audit storage: SQLite locally or PostgreSQL in production."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]

@contextmanager
def connection():
    url = os.environ.get("FIREWATCH_DATABASE_URL")
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ValueError("Only PostgreSQL operational database URLs are supported")
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, row_factory=dict_row) as database:
            yield database, "%s"
    else:
        path = Path(os.environ.get("FIREWATCH_OPERATIONS_DB", ROOT / "data/operational/operations.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(path, timeout=30)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA journal_mode=WAL")
        try:
            yield database, "?"
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

def initialise(database):
    database.execute("CREATE TABLE IF NOT EXISTS operational_events (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at_utc TEXT NOT NULL, payload TEXT NOT NULL)")
    database.execute("CREATE TABLE IF NOT EXISTS incident_observations (observation_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, grid_id TEXT, created_at_utc TEXT NOT NULL, observed_at_utc TEXT NOT NULL, expires_at_utc TEXT NOT NULL, payload TEXT NOT NULL)")
    database.execute("CREATE INDEX IF NOT EXISTS idx_incident_observation ON incident_observations(incident_id, observed_at_utc)")

def audit_event(kind: str, payload: dict) -> str:
    identifier = str(uuid4())
    with connection() as (database, placeholder):
        initialise(database)
        database.execute("INSERT INTO operational_events VALUES (" + ",".join([placeholder] * 4) + ")", (identifier, kind, datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=str, allow_nan=False)))
    return identifier

def latest_job_statuses() -> dict:
    with connection() as (database, placeholder):
        initialise(database)
        rows = database.execute("SELECT created_at_utc,payload FROM operational_events WHERE kind=" + placeholder + " ORDER BY created_at_utc DESC LIMIT 200", ("etl_run",)).fetchall()
    result = {}
    for row in rows:
        data = json.loads(row["payload"])
        result.setdefault(data["job"], {**data, "recorded_at_utc": row["created_at_utc"]})
    return result

def store_observation(record: dict) -> dict:
    with connection() as (database, placeholder):
        initialise(database)
        database.execute("INSERT INTO incident_observations VALUES (" + ",".join([placeholder] * 7) + ")", (record["observation_id"], record["incident_id"], record["grid_id"], record["created_at_utc"], record["observed_at_utc"], record["expires_at_utc"], json.dumps(record)))
    return record

def incident_observations(incident_id: str, *, limit=25):
    with connection() as (database, placeholder):
        initialise(database)
        rows = database.execute("SELECT payload FROM incident_observations WHERE incident_id=" + placeholder + " ORDER BY observed_at_utc DESC LIMIT " + placeholder, (incident_id, limit)).fetchall()
    return [json.loads(row["payload"]) for row in rows]
