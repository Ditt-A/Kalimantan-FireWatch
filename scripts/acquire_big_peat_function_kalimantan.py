#!/usr/bin/env python3
"""Acquire and validate BIG's public 1:50k peat-ecosystem function layer.

The source exposes thickness as a *class interval*, e.g. ``1,5 - 2,0
meter``.  This script deliberately retains the source text and adds a
conservative rule for the PRD's ``>1.5 m`` condition:

* lower bound > 1.5 m: confirmed above;
* upper bound <= 1.5 m: confirmed not above;
* a class that straddles 1.5 m: ambiguous -- field confirmation required.

The result is planning context, not a field measurement, a dispatch order, or
an assertion of water/pump availability.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from requests.adapters import HTTPAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_URL = (
    "https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/"
    "SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/48/query"
)
LAYER_URL = SERVICE_URL.rsplit("/query", 1)[0]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "peatland" / "big_peat_function_kalimantan.geojson"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "peatland"
# Buffered envelope of the five project ADM1 geometries.  The initially
# proposed -4.5 southern bound misses small South Kalimantan components whose
# project boundary reaches -5.1015233; final output is still exactly clipped
# to those five provinces below.
BBOX = (108.5, -5.2, 119.1, 4.5)
BATCH_SIZE = 1000
EXPECTED_FIELDS = {
    "objectid_1",
    "objectid",
    "kode_khg",
    "peat_thick",
    "tnh_gambut",
    "feg_peat",
    "feg_50k",
    "geometry",
}
KALIMANTAN_NAMES = {
    "West Kalimantan",
    "Central Kalimantan",
    "South Kalimantan",
    "East Kalimantan",
    "North Kalimantan",
}
THICKNESS = re.compile(
    r"^\s*(?P<lower>\d+(?:[,.]\d+)?)\s*-\s*(?P<upper>\d+(?:[,.]\d+)?)\s*meter\s*$",
    re.IGNORECASE,
)


class TLS12Adapter(HTTPAdapter):
    """Keep this request compatible with the currently TLS-1.2-only service."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = context
        super().init_poolmanager(*args, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    adapter = TLS12Adapter(pool_connections=1, pool_maxsize=1)
    client.mount("https://", adapter)
    client.headers["User-Agent"] = "compfest-academy-data-acquisition/1.0"
    return client


def query_params(*, geometry: bool, output_format: str, offset: int = 0) -> dict[str, Any]:
    return {
        "where": "1=1",
        "geometry": ",".join(str(value) for value in BBOX),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": str(geometry).lower(),
        "outSR": 4326,
        "f": output_format,
        "resultOffset": offset,
        "resultRecordCount": BATCH_SIZE,
    }


def request_json(client: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
    response = client.get(SERVICE_URL, params=params, timeout=(30, 180))
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"BIG service returned an error: {payload['error']}")
    return payload


def retrieve() -> tuple[dict[str, Any], int, int]:
    client = session()
    count_payload = request_json(
        client,
        {
            **query_params(geometry=False, output_format="json"),
            "returnCountOnly": "true",
        },
    )
    reported_count = int(count_payload["count"])
    if reported_count <= 0:
        raise RuntimeError("BIG spatial query returned zero peat-function features")
    ids_payload = request_json(
        client,
        {
            "where": "1=1",
            "geometry": ",".join(str(value) for value in BBOX),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "returnIdsOnly": "true",
            "f": "json",
        },
    )
    source_ids = [int(value) for value in ids_payload.get("objectIds", [])]
    if not source_ids or len(source_ids) != len(set(source_ids)):
        raise RuntimeError("BIG returnIdsOnly result is empty or contains duplicate object IDs")

    features: list[dict[str, Any]] = []
    offset = 0
    # Although the service advertises a count operation, it currently returns
    # 1,000 for this envelope while subsequent pages exist.  Treat it as
    # advisory only and page until the server supplies no more features.
    while True:
        payload = request_json(
            client, query_params(geometry=True, output_format="geojson", offset=offset)
        )
        page = payload.get("features", [])
        if not page:
            if not features:
                raise RuntimeError("BIG spatial query returned no GeoJSON features")
            break
        features.extend(page)
        if len(page) < BATCH_SIZE and not payload.get("exceededTransferLimit", False):
            break
        offset += len(page)

    object_ids = [feature.get("properties", {}).get("objectid_1") for feature in features]
    if any(object_id is None for object_id in object_ids) or len(set(object_ids)) != len(object_ids):
        raise RuntimeError("BIG pagination returned a missing or duplicate objectid_1")
    if {int(value) for value in object_ids} != set(source_ids):
        raise RuntimeError("BIG pagination feature IDs do not match the authoritative returnIdsOnly set")
    return {"type": "FeatureCollection", "features": features}, reported_count, len(source_ids)


def kalimantan_boundary() -> gpd.GeoDataFrame:
    boundary_path = PROJECT_ROOT / "data" / "raw" / "boundaries" / "idn_adm1.geojson"
    boundaries = gpd.read_file(boundary_path).to_crs("EPSG:4326")
    name_column = next(
        (column for column in ("shapeName", "NAME_1", "name") if column in boundaries.columns), None
    )
    if name_column is None:
        raise ValueError("Unable to identify ADM1 name field")
    selected = boundaries.loc[boundaries[name_column].isin(KALIMANTAN_NAMES), ["geometry"]].copy()
    if len(selected) != 5:
        raise ValueError(f"Expected five Kalimantan provinces, found {len(selected)}")
    return selected


def parse_thickness(value: object) -> tuple[float | None, float | None]:
    if not isinstance(value, str):
        return None, None
    match = THICKNESS.match(value)
    if match is None:
        return None, None
    return (
        float(match.group("lower").replace(",", ".")),
        float(match.group("upper").replace(",", ".")),
    )


def threshold_status(row: pd.Series) -> str:
    if row["tnh_gambut"] != "Tanah Gambut":
        return "confirmed_not_peat"
    lower = row["peat_thickness_lower_m"]
    upper = row["peat_thickness_upper_m"]
    if pd.isna(lower) or pd.isna(upper):
        return "unknown_unparsed_thickness_class"
    if lower > 1.5:
        return "confirmed_above_1_5m"
    if upper <= 1.5:
        return "confirmed_not_above_1_5m"
    return "ambiguous_class_intersects_1_5m"


def main() -> int:
    raw, queried_count, source_id_count = retrieve()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    raw_data = gpd.GeoDataFrame.from_features(raw["features"], crs="EPSG:4326")
    missing = EXPECTED_FIELDS - set(raw_data.columns)
    if missing:
        raise ValueError(f"BIG response is missing required fields: {sorted(missing)}")
    if raw_data.empty or raw_data.geometry.is_empty.any() or (~raw_data.geometry.is_valid).any():
        raise ValueError("BIG raw data contains empty or invalid geometry")

    # The source query is a bbox. Clip it to the same five provinces used by
    # the project so features which merely touch the operational envelope do
    # not leak into the Kalimantan product.
    clipped = gpd.overlay(raw_data, kalimantan_boundary(), how="intersection", keep_geom_type=True)
    clipped = clipped.loc[~clipped.geometry.is_empty].copy()
    lower_upper = clipped["peat_thick"].map(parse_thickness)
    clipped["peat_thickness_lower_m"] = lower_upper.map(lambda pair: pair[0])
    clipped["peat_thickness_upper_m"] = lower_upper.map(lambda pair: pair[1])
    clipped["peat_thickness_gt_1_5m_status"] = clipped.apply(threshold_status, axis=1)
    clipped["peat_thickness_interpretation"] = (
        "class_interval_not_field_measurement; use only lower_bound_gt_1_5m as confirmed"
    )
    clipped = clipped.sort_values("objectid_1").reset_index(drop=True)
    if clipped.empty or clipped.geometry.is_empty.any() or (~clipped.geometry.is_valid).any():
        raise ValueError("Processed BIG data is empty or contains invalid geometry")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "big_peat_function_kalimantan.parquet"
    clipped.to_parquet(output, index=False)
    status_counts = clipped["peat_thickness_gt_1_5m_status"].value_counts().to_dict()
    validation = {
        "status": "passed",
        "source_count_reported_by_service": queried_count,
        "source_id_count_return_ids_only": source_id_count,
        "source_feature_count_downloaded": int(len(raw_data)),
        "pagination_matches_source_ids": int(len(raw_data)) == source_id_count,
        "processed_feature_count": int(len(clipped)),
        "empty_geometries": int(clipped.geometry.is_empty.sum()),
        "invalid_geometries": int((~clipped.geometry.is_valid).sum()),
        "unique_khg_codes": int(clipped["kode_khg"].nunique(dropna=True)),
        "unparsed_thickness_class_count": int(
            clipped["peat_thickness_lower_m"].isna().sum()
        ),
        "thickness_gt_1_5m_status_counts": status_counts,
        "raw_file": RAW_PATH.name,
        "raw_bytes": RAW_PATH.stat().st_size,
        "raw_sha256": sha256(RAW_PATH),
        "processed_file": output.name,
        "processed_bytes": output.stat().st_size,
        "processed_sha256": sha256(output),
    }
    if validation["unparsed_thickness_class_count"]:
        raise ValueError(json.dumps(validation, ensure_ascii=False, indent=2))
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_layer": "Peta Fungsi Ekosistem Gambut Skala 1:50.000",
        "service_url": SERVICE_URL,
        "layer_metadata_url": f"{LAYER_URL}?f=pjson",
        "publisher_copyright": "Direktorat Pengendalian Kerusakan Ekosistem Gambut, KLHK",
        "source_query": {
            "bbox_wgs84": BBOX,
            "geometry_type": "esriGeometryEnvelope",
            "spatial_relation": "esriSpatialRelIntersects",
            "out_fields": "*",
            "output_crs": "EPSG:4326",
            "pagination": {"batch_size": BATCH_SIZE, "source_max_record_count": BATCH_SIZE},
        },
        "thickness_rule": {
            "confirmed_above_1_5m": "Tanah Gambut and thickness-class lower bound > 1.5 m",
            "confirmed_not_above_1_5m": "Tanah Gambut and thickness-class upper bound <= 1.5 m",
            "ambiguous": "Tanah Gambut class intersects 1.5 m; requires field confirmation",
        },
        "limitations": [
            "Peat thickness is a mapped class interval, not a measured incident-site depth.",
            "Use `confirmed_above_1_5m` to satisfy a conservative >1.5 m rule; do not infer it from a class that intersects 1.5 m.",
            "Do not use the layer as a water-source, staffing, or dispatch assignment by itself.",
        ],
    }
    (OUTPUT_DIR / "big_peat_function_kalimantan.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "big_peat_function_kalimantan.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
