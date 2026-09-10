#!/usr/bin/env python3
"""Acquire BIG's public Peta Lahan Gambut as the broad peat-location layer.

This is deliberately separate from MapServer/48 (Peta Fungsi Ekosistem
Gambut).  Layer 6 establishes mapped peat-location context; layer 48 supplies
function and thickness-class context only where it has a feature.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd

from acquire_big_peat_function_kalimantan import BBOX, TLS12Adapter, kalimantan_boundary, sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_URL = (
    "https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/"
    "SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/6/query"
)
LAYER_URL = SERVICE_URL.rsplit("/query", 1)[0]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "peatland" / "big_peat_extent_kalimantan.geojson"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "peatland"
BATCH_SIZE = 1000
EXPECTED_FIELDS = {
    "objectid",
    "wadmpu",
    "landform",
    "bhnindk",
    "relief",
    "usda1",
    "usda2",
    "usda3",
    "fcode",
    "metadata",
    "srs_id",
    "namobj",
    "geometry",
}


def client():
    import requests

    session = requests.Session()
    session.mount("https://", TLS12Adapter(pool_connections=1, pool_maxsize=1))
    session.headers["User-Agent"] = "compfest-academy-data-acquisition/1.0"
    return session


def base_params() -> dict[str, Any]:
    return {
        "where": "1=1",
        "geometry": ",".join(str(value) for value in BBOX),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "outSR": 4326,
    }


def request_json(session, params: dict[str, Any]) -> dict[str, Any]:
    response = session.get(SERVICE_URL, params=params, timeout=(30, 180))
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"BIG service returned an error: {payload['error']}")
    return payload


def retrieve() -> tuple[dict[str, Any], int]:
    session = client()
    ids_payload = request_json(session, {**base_params(), "returnIdsOnly": "true", "f": "json"})
    source_ids = [int(value) for value in ids_payload.get("objectIds", [])]
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise RuntimeError("BIG peat-location ID response is empty or contains duplicates")
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = request_json(
            session,
            {
                **base_params(),
                "returnGeometry": "true",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": BATCH_SIZE,
            },
        )
        page = payload.get("features", [])
        if not page:
            break
        features.extend(page)
        if len(page) < BATCH_SIZE and not payload.get("exceededTransferLimit", False):
            break
        offset += len(page)
    downloaded_ids = [feature.get("properties", {}).get("objectid") for feature in features]
    if any(value is None for value in downloaded_ids) or len(set(downloaded_ids)) != len(downloaded_ids):
        raise RuntimeError("BIG peat-location pagination has missing or duplicate object IDs")
    if {int(value) for value in downloaded_ids} != set(source_ids):
        raise RuntimeError("BIG peat-location pagination IDs do not match returnIdsOnly")
    return {"type": "FeatureCollection", "features": features}, len(source_ids)


def main() -> int:
    raw, source_id_count = retrieve()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    raw_data = gpd.GeoDataFrame.from_features(raw["features"], crs="EPSG:4326")
    missing = EXPECTED_FIELDS - set(raw_data.columns)
    if missing:
        raise ValueError(f"BIG peat-location response is missing fields: {sorted(missing)}")
    if raw_data.empty or raw_data.geometry.is_empty.any() or (~raw_data.geometry.is_valid).any():
        raise ValueError("BIG peat-location response is empty or has invalid geometry")
    processed = gpd.overlay(raw_data, kalimantan_boundary(), how="intersection", keep_geom_type=True)
    processed = processed.loc[~processed.geometry.is_empty].sort_values("objectid").reset_index(drop=True)
    if processed.empty or processed.geometry.is_empty.any() or (~processed.geometry.is_valid).any():
        raise ValueError("Processed BIG peat-location data is empty or has invalid geometry")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "big_peat_extent_kalimantan.parquet"
    processed.to_parquet(output, index=False)
    validation = {
        "status": "passed",
        "source_id_count_return_ids_only": source_id_count,
        "source_feature_count_downloaded": int(len(raw_data)),
        "pagination_matches_source_ids": int(len(raw_data)) == source_id_count,
        "processed_feature_count": int(len(processed)),
        "empty_geometries": int(processed.geometry.is_empty.sum()),
        "invalid_geometries": int((~processed.geometry.is_valid).sum()),
        "raw_file": RAW_PATH.name,
        "raw_bytes": RAW_PATH.stat().st_size,
        "raw_sha256": sha256(RAW_PATH),
        "processed_file": output.name,
        "processed_bytes": output.stat().st_size,
        "processed_sha256": sha256(output),
    }
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_layer": "Peta Lahan Gambut",
        "service_url": SERVICE_URL,
        "layer_metadata_url": f"{LAYER_URL}?f=pjson",
        "source_query": {
            "bbox_wgs84": BBOX,
            "geometry_type": "esriGeometryEnvelope",
            "spatial_relation": "esriSpatialRelIntersects",
            "output_crs": "EPSG:4326",
            "pagination": {"batch_size": BATCH_SIZE},
        },
        "role": "Mapped peat-location context; use MapServer/48 separately for thickness-class lookup.",
        "limitations": [
            "Mapped peat presence is not an incident-site field measurement.",
            "This layer does not establish peat thickness, water availability, or resource allocation.",
        ],
    }
    (OUTPUT_DIR / "big_peat_extent_kalimantan.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "big_peat_extent_kalimantan.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
