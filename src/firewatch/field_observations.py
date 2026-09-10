"""Small local audit store for dashboard field-condition observations."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "operational" / "field_observations.sqlite3"
FIRE_TYPES = {"surface", "ground_smouldering", "mixed", "crown"}


def _connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def initialise_store() -> None:
    with closing(_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS field_observations (
                observation_id TEXT PRIMARY KEY,
                created_at_utc TEXT NOT NULL,
                grid_id TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                fire_type_class TEXT NOT NULL,
                confidence TEXT NOT NULL,
                note TEXT,
                source TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_field_observations_grid_time "
            "ON field_observations(grid_id, created_at_utc DESC)"
        )
        connection.commit()


def _record_as_dict(record: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(record) if record is not None else None


def save_fire_type_observation(*, grid_id: str | None, latitude: float, longitude: float,
                               fire_type_class: str, confidence: str, note: str | None) -> dict[str, Any]:
    if fire_type_class not in FIRE_TYPES:
        raise ValueError(f"fire_type_class must be one of {sorted(FIRE_TYPES)}")
    if confidence not in {"reported", "confirmed"}:
        raise ValueError("confidence must be reported or confirmed")
    initialise_store()
    record = {
        "observation_id": str(uuid4()), "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "grid_id": str(grid_id) if grid_id else None, "latitude": float(latitude), "longitude": float(longitude),
        "fire_type_class": fire_type_class, "confidence": confidence, "note": note.strip() if note else None,
        "source": "dashboard_operator_form",
    }
    with closing(_connection()) as connection:
        connection.execute(
            """
            INSERT INTO field_observations (
                observation_id, created_at_utc, grid_id, latitude, longitude,
                fire_type_class, confidence, note, source
            ) VALUES (
                :observation_id, :created_at_utc, :grid_id, :latitude, :longitude,
                :fire_type_class, :confidence, :note, :source
            )
            """,
            record,
        )
        connection.commit()
    return record


def latest_fire_type_observation(grid_id: str | None) -> dict[str, Any] | None:
    if not grid_id or not DATABASE_PATH.exists():
        return None
    initialise_store()
    with closing(_connection()) as connection:
        record = connection.execute(
            "SELECT * FROM field_observations WHERE grid_id = ? "
            "ORDER BY created_at_utc DESC LIMIT 1", (str(grid_id),)
        ).fetchone()
    return _record_as_dict(record)


def list_fire_type_observations(grid_id: str, limit: int = 25) -> list[dict[str, Any]]:
    if not DATABASE_PATH.exists():
        return []
    initialise_store()
    with closing(_connection()) as connection:
        records = connection.execute(
            "SELECT * FROM field_observations WHERE grid_id = ? "
            "ORDER BY created_at_utc DESC LIMIT ?", (str(grid_id), int(limit))
        ).fetchall()
    return [dict(record) for record in records]
