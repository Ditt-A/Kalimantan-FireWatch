"""Incident-scoped, time-limited observations with portable operational storage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from .operations import store_observation, incident_observations

FIRE_TYPES = {"surface", "ground_smouldering", "mixed", "crown"}

def save_fire_type_observation(*, grid_id: str | None, latitude: float, longitude: float,
                               fire_type_class: str, confidence: str, note: str | None,
                               incident_id: str | None = None, observed_at_utc: str | None = None,
                               actor: str = "local_operator") -> dict[str, Any]:
    if fire_type_class not in FIRE_TYPES or confidence not in {"reported", "confirmed"}:
        raise ValueError("Unsupported fire type or confidence")
    if not incident_id or len(incident_id) > 128:
        raise ValueError("An incident_id is required; a grid is not an incident")
    now = datetime.now(timezone.utc)
    observed = datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00")) if observed_at_utc else now
    if observed.tzinfo is None:
        raise ValueError("Observation timestamps require a timezone")
    observed = observed.astimezone(timezone.utc)
    if observed > now + timedelta(minutes=5):
        raise ValueError("Observation timestamp is in the future")
    return store_observation({
        "observation_id": str(uuid4()), "created_at_utc": now.isoformat(),
        "observed_at_utc": observed.isoformat(), "expires_at_utc": (observed + timedelta(hours=24)).isoformat(),
        "incident_id": incident_id, "grid_id": str(grid_id) if grid_id else None,
        "latitude": float(latitude), "longitude": float(longitude),
        "fire_type_class": fire_type_class, "confidence": confidence,
        "note": note.strip() if note else None, "source": "dashboard_operator_form", "actor": actor,
    })

def latest_fire_type_observation(grid_id: str | None, incident_id: str | None = None) -> dict[str, Any] | None:
    if not incident_id:
        return None
    now = datetime.now(timezone.utc).isoformat()
    for record in incident_observations(incident_id):
        if record["grid_id"] == str(grid_id) and record["expires_at_utc"] > now:
            return record
    return None

def list_fire_type_observations(grid_id: str, limit: int = 25, incident_id: str | None = None) -> list[dict[str, Any]]:
    if not incident_id:
        return []
    return [record for record in incident_observations(incident_id, limit=limit) if record["grid_id"] == grid_id]
