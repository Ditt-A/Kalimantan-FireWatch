"""Acquire a reproducible Kalimantan SoilGrids mineral-reference map.

The source is the ISRIC SoilGrids WCS probability raster for Histosols. A low
probability is only a mineral *reference*; it is never an in-situ confirmation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests


ROOT = Path(__file__).resolve().parents[1]
RAW_OUTPUT = ROOT / "data" / "raw" / "soilgrids" / "soilgrids_histosols_probability_kalimantan_025deg.tif"
PROCESSED_OUTPUT = ROOT / "data" / "processed" / "soil" / "soilgrids_histosols_context_grid.parquet"
METADATA_OUTPUT = ROOT / "data" / "processed" / "soil" / "soilgrids_histosols_context_metadata.json"
GRID_PATH = ROOT / "data" / "processed" / "dim_grid.parquet"
WCS_URL = "https://maps.isric.org/mapserv"
SOURCE_URL = "https://maps.isric.org/mapserv?map=/map/wrb.map"
KALIMANTAN_BOUNDS = (108.0, -4.5, 119.5, 4.5)
SCALE_LONGITUDE, SCALE_LATITUDE = 460, 360
MINERAL_REFERENCE_MAX_HISTOSOLS_PCT = 20.0


def acquire() -> None:
    RAW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = KALIMANTAN_BOUNDS
    parameters = [
        ("map", "/map/wrb.map"), ("service", "WCS"), ("version", "2.0.1"),
        ("request", "GetCoverage"), ("coverageId", "Histosols"), ("format", "image/tiff"),
        ("subset", f"Long({west},{east})"), ("subset", f"Lat({south},{north})"),
        ("scaleSize", f"Long({SCALE_LONGITUDE}),Lat({SCALE_LATITUDE})"),
    ]
    response = requests.get(WCS_URL, params=parameters, timeout=180)
    response.raise_for_status()
    temporary = RAW_OUTPUT.with_suffix(".partial")
    temporary.write_bytes(response.content)
    temporary.replace(RAW_OUTPUT)

    with rasterio.open(RAW_OUTPUT) as raster:
        values = raster.read(1, masked=True)
        valid = np.asarray(values.compressed(), dtype=float)
        if valid.size < SCALE_LONGITUDE * SCALE_LATITUDE * 0.95:
            raise ValueError("SoilGrids response has unexpectedly sparse raster coverage.")
        if valid.min() < 0.0 or valid.max() > 100.0:
            raise ValueError("SoilGrids Histosols probability must lie between 0 and 100.")
        grids = gpd.read_parquet(GRID_PATH).to_crs("EPSG:4326")
        # Keep the exact geographic envelope served by the dashboard API.
        # dim_grid has a few buffered cells just south of the API boundary.
        grids = grids.loc[
            grids["centroid_lon"].between(west, east)
            & grids["centroid_lat"].between(south, north)
        ].copy()
        coordinates = list(zip(grids["centroid_lon"], grids["centroid_lat"]))
        sampled = list(raster.sample(coordinates, indexes=1, masked=True))
    probabilities = [float(value[0]) if not np.ma.is_masked(value[0]) else np.nan for value in sampled]
    if not np.isfinite(probabilities).all():
        raise ValueError("At least one forecast grid centroid lies outside SoilGrids raster coverage.")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    context = pd.DataFrame({"grid_id": grids["grid_id"].astype(str), "histosols_probability_pct": probabilities})
    context["mineral_reference"] = context["histosols_probability_pct"].le(MINERAL_REFERENCE_MAX_HISTOSOLS_PCT)
    context["source"] = "ISRIC SoilGrids WRB Histosols probability"
    context["source_url"] = SOURCE_URL
    context["source_retrieved_at_utc"] = retrieved_at
    context["resolution_note"] = "0.025 degree WCS sampling; map reference, not a field soil observation"
    if context.empty or context["grid_id"].duplicated().any():
        raise ValueError("SoilGrids context must contain one row per forecast grid.")
    context.to_parquet(PROCESSED_OUTPUT, index=False)

    metadata = {
        "source": "ISRIC SoilGrids WRB Histosols probability WCS", "source_url": SOURCE_URL,
        "coverage": "Histosols", "bbox_wgs84": KALIMANTAN_BOUNDS,
        "scale_size": [SCALE_LONGITUDE, SCALE_LATITUDE], "retrieved_at_utc": retrieved_at,
        "mineral_reference_rule": "Histosols probability <= 20%; BIG peat context overrides this reference.",
        "validation": {"raster_valid_cells": int(valid.size), "raster_min_probability_pct": float(valid.min()), "raster_max_probability_pct": float(valid.max()), "forecast_grid_rows": int(len(context)), "soilgrids_low_histosol_rows": int(context["mineral_reference"].sum())},
    }
    METADATA_OUTPUT.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {RAW_OUTPUT}")
    print(f"Saved {PROCESSED_OUTPUT}: {len(context)} grid rows; {int(context['mineral_reference'].sum())} mineral references")


if __name__ == "__main__":
    acquire()
