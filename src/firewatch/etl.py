"""Incremental FIRMS ingestion, isolated job execution, and success watermarks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import io
import json
import os
import re
from pathlib import Path
import tempfile
import time
from typing import Callable

import geopandas as gpd
import pandas as pd
import requests

from .operations import audit_event
from .releases import PROJECT_ROOT, atomic_json, current_snapshot, publish_release, sha256
from .fire_weather import fire_weather_context

IDENTITY = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument"]

def canonical_detections(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ["latitude", "longitude", "frp", "scan", "track"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[["latitude", "longitude", "frp", "scan", "track"]].isna().any().any():
        raise ValueError("FIRMS has invalid coordinates, FRP, or pixel dimensions")
    if not result["frp"].ge(0).all() or not result[["scan", "track"]].gt(0).all().all():
        raise ValueError("FIRMS FRP/pixel dimensions are outside their domain")
    result["acq_date"] = pd.to_datetime(result["acq_date"]).dt.normalize()
    minute = pd.to_numeric(result["acq_time"], errors="raise").astype(int)
    if not ((minute // 100).between(0, 23) & (minute % 100).between(0, 59)).all():
        raise ValueError("Invalid FIRMS acquisition time")
    result["acq_time"] = minute.astype(str).str.zfill(4)
    result["satellite"] = result["satellite"].astype(str).replace({"20": "N20", "NOAA20": "N20", "NOAA-20": "N20"})
    result["instrument"] = result["instrument"].astype(str).str.upper()
    result["acq_datetime"] = pd.to_datetime(result["acq_date"].dt.strftime("%Y-%m-%d") + " " + result["acq_time"], format="%Y-%m-%d %H%M")
    result["source_priority"] = result["source_product"].str.endswith("_SP").astype(int)
    if "retrieved_at_utc" not in result:
        result["retrieved_at_utc"] = ""
    result = result.sort_values(["source_priority", "retrieved_at_utc"], na_position="first").drop_duplicates(IDENTITY, keep="last")
    result["footprint_ha_proxy"] = result["scan"] * result["track"] * 100
    result["week_start"] = result["acq_date"] - pd.to_timedelta(result["acq_date"].dt.dayofweek, unit="D")
    keys = result[IDENTITY].astype(str).agg("|".join, axis=1)
    result["detection_id"] = keys.map(lambda value: int(hashlib.sha256(value.encode()).hexdigest()[:12], 16))
    if result["detection_id"].duplicated().any():
        raise ValueError("Detection identity hash collision")
    return result.drop(columns=["source_priority"]).sort_values(["acq_datetime", "detection_id"]).reset_index(drop=True)

def _get(session, url):
    for attempt in range(4):
        try:
            response = session.get(url, timeout=90)
        except requests.RequestException:
            if attempt == 3:
                raise ValueError("FIRMS transport request failed") from None
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 200:
            return response
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            raise ValueError(f"FIRMS request failed with HTTP {response.status_code}")
        time.sleep(2 ** attempt)
    raise ValueError("FIRMS retries exhausted")

def ingest_firms(*, root=PROJECT_ROOT, now=None, publish=False):
    now = now or datetime.now(timezone.utc)
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise ValueError("FIRMS_MAP_KEY is required for scheduled ingestion")
    source = "VIIRS_NOAA20_NRT"
    workspace = root / "data/operational/etl"
    watermark_path = root / "data/operational/firms_watermark.json"
    base = current_snapshot().root
    existing = pd.read_parquet(base / "data/processed/firms_kalimantan_clean.parquet")
    previous = json.loads(watermark_path.read_text()) if watermark_path.exists() else {}
    staged = workspace / "curated/firms_kalimantan_clean.parquet"
    if previous and staged.exists():
        if sha256(staged) != previous.get("curated_sha256"):
            raise ValueError("Staged FIRMS checksum does not match successful watermark")
        existing = pd.read_parquet(staged)
    baseline = pd.Timestamp(previous.get("complete_through", existing["acq_date"].max())).date()
    start = min(now.date() - timedelta(days=3), baseline - timedelta(days=2))
    session = requests.Session()
    availability = pd.read_csv(io.StringIO(_get(session, f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/all").text))
    available = availability.loc[availability["data_id"].eq(source)]
    if available.empty or start < pd.Timestamp(available.iloc[0]["min_date"]).date():
        raise ValueError("NRT availability cannot cover the missing interval; perform explicit archive backfill")
    end = min(now.date(), pd.Timestamp(available.iloc[0]["max_date"]).date())
    if end < start:
        raise ValueError("FIRMS source has not reached the requested date interval")
    batches, manifests = [], []
    cursor = start
    run = now.strftime("%Y%m%dT%H%M%SZ")
    while cursor <= end:
        days = min(5, (end-cursor).days + 1)
        response = _get(session, f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/108,-4.5,119.5,4.5/{days}/{cursor}")
        try:
            frame = pd.read_csv(io.StringIO(response.text))
        except Exception:
            raise ValueError("FIRMS returned a non-CSV response") from None
        required = {"latitude", "longitude", "acq_date", "acq_time", "frp", "scan", "track", "confidence", "satellite"}
        if required - set(frame):
            raise ValueError("FIRMS CSV schema is invalid")
        path = workspace / "raw" / run / f"{source}-{cursor}-{days}d.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        manifests.append({"source": source, "request_start": str(cursor), "days": days, "rows": len(frame), "sha256": sha256(path), "retrieved_at_utc": now.isoformat()})
        frame["source_product"], frame["instrument"], frame["retrieved_at_utc"] = source, "VIIRS", now.isoformat()
        batches.append(frame)
        cursor += timedelta(days=days)
    fresh = pd.concat(batches, ignore_index=True)
    if not fresh.empty:
        fresh = canonical_detections(fresh)
        grid = gpd.read_parquet(base / "data/processed/dim_grid.parquet").to_crs(4326)
        points = gpd.GeoDataFrame(fresh, geometry=gpd.points_from_xy(fresh.longitude, fresh.latitude), crs=4326)
        # Grid polygons are clipped to the five-province union.
        joined = gpd.sjoin(points, grid[["grid_id", "province", "district", "geometry"]], how="inner", predicate="intersects")
        fresh = pd.DataFrame(joined.drop(columns=["geometry", "index_right"]).sort_values("grid_id").drop_duplicates(IDENTITY))
    combined = canonical_detections(pd.concat([existing, fresh], ignore_index=True))
    target = workspace / "curated/firms_kalimantan_clean.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".firms-", suffix=".parquet", dir=target.parent)
    os.close(descriptor)
    try:
        combined.to_parquet(temp, index=False)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    complete = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc) - timedelta(seconds=1), now - timedelta(hours=6))
    metadata = {"status": "passed", "complete_through": complete.isoformat(), "request_start": str(start), "request_end": str(end), "successful_zero_detection_response_allowed": True, "rows": len(combined), "curated_sha256": sha256(target), "source_batches": manifests}
    atomic_json(workspace / f"raw/{run}/manifest.json", metadata)
    atomic_json(watermark_path, metadata)
    if publish:
        parent = current_snapshot()
        release_metadata = {k: v for k, v in parent.metadata.items() if k not in {"files", "release_id", "quality_status", "created_at_utc"}}
        release_metadata["firms_ingestion"] = metadata
        publish_release(parent.root, "alerts-" + run, release_metadata, {"data/processed/firms_kalimantan_clean.parquet": target}, expected_release_id=parent.token)
    return metadata

def run_job(name: str, function: Callable, *, root=PROJECT_ROOT):
    directory = root / "data/operational/locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"job": name, "status": "already_running"}
        start = datetime.now(timezone.utc)
        try:
            output = function()
            result = {"job": name, "status": "passed", "started_at_utc": start.isoformat(), "finished_at_utc": datetime.now(timezone.utc).isoformat(), "result": output}
            audit_event("etl_run", result)
            atomic_json(root / f"data/operational/jobs/{name}.json", result)
            return result
        except Exception as error:
            # Errors may contain a credential-bearing provider URL. Audit only
            # the exception class; callers log a sanitized summary.
            message = str(error)
            if hasattr(error, "stderr") and error.stderr:
                message = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else str(error.stderr)
                message = message.strip().splitlines()[-1]
            for key in ("FIRMS_MAP_KEY", "CDSAPI_KEY", "FIREWATCH_WEATHER_API_KEY", "FIREWATCH_SESSION_SECRET", "FIREWATCH_OPERATOR_TOKEN", "FIREWATCH_ADMIN_TOKEN"):
                value = os.environ.get(key)
                if value:
                    message = message.replace(value, "[redacted]")
            message = re.sub(r"https?://\S+", "[redacted URL]", message)[:300]
            result = {"job": name, "status": "failed", "started_at_utc": start.isoformat(), "finished_at_utc": datetime.now(timezone.utc).isoformat(), "error_type": type(error).__name__, "reason": message}
            audit_event("etl_run", result)
            raise RuntimeError(f"{name} failed ({type(error).__name__}); current release retained") from None
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

def refresh_weather():
    path = PROJECT_ROOT / "deploy/weather_locations.json"
    configured = Path(os.environ.get("FIREWATCH_WEATHER_LOCATIONS", path))
    locations = json.loads(configured.read_text())
    if not locations:
        raise ValueError("Configure at least one weather cell")
    result = []
    for location in locations:
        context = fire_weather_context(location["latitude"], location["longitude"])
        result.append({"latitude": location["latitude"], "longitude": location["longitude"], "fwi": context["fwi"], "weather_timestamp": context["weather_at_local_noon"]["timestamp"]})
    return result
