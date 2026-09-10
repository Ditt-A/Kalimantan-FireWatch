#!/usr/bin/env python3
"""Build a transparent, static Water Access Index for Kalimantan grid cells.

The output is deliberately a *candidate-water proximity* layer.  It neither
claims that a source is usable by a pump nor reduces a response recommendation;
the operator must verify water level, quality, permission, and vehicle access.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from sklearn.neighbors import BallTree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "jrc_surface_water"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"
PUPR_BORE_WELLS = PROCESSED_DIR / "pupr_geoapi" / "pupr_sumur_bor_kalimantan.parquet"
TILES = ("100E_0N", "110E_0N", "100E_10N", "110E_10N")
EARTH_RADIUS_KM = 6371.0088
COARSE_DEGREES = 0.01  # 10 x 10 source cells per project 0.1-degree grid cell.


def _reduce_mask(mask: np.ndarray, factor: int = 40) -> np.ndarray:
    """Return percentage cover in 0.01-degree cells, without loading a 10° tile at once."""
    height, width = mask.shape
    if height % factor or width % factor:
        raise ValueError(f"Unexpected source dimensions {(height, width)} for factor {factor}")
    return mask.reshape(height // factor, factor, width // factor, factor).mean(axis=(1, 3)) * 100.0


def _coarse_tile(occurrence_path: Path, seasonality_path: Path) -> dict[str, object]:
    """Aggregate two 30m tiles to 0.01° evidence grids in bounded row blocks."""
    with rasterio.open(occurrence_path) as occurrence, rasterio.open(seasonality_path) as seasonality:
        if occurrence.crs != seasonality.crs or occurrence.transform != seasonality.transform:
            raise ValueError(f"Raster alignment mismatch: {occurrence_path.name}")
        if occurrence.width != seasonality.width or occurrence.height != seasonality.height:
            raise ValueError(f"Raster dimensions mismatch: {occurrence_path.name}")
        factor = round(COARSE_DEGREES / abs(occurrence.transform.a))
        if factor <= 0 or occurrence.width % factor or occurrence.height % factor:
            raise ValueError(f"Cannot aggregate {occurrence_path.name} to {COARSE_DEGREES} degrees")
        rows = occurrence.height // factor
        columns = occurrence.width // factor
        output = {
            "bounds": occurrence.bounds,
            "transform": occurrence.transform * occurrence.transform.scale(factor, factor),
            "occurrence_mean_pct": np.full((rows, columns), np.nan, dtype=np.float32),
            "permanent_cover_pct": np.full((rows, columns), np.nan, dtype=np.float32),
            "seasonal_cover_pct": np.full((rows, columns), np.nan, dtype=np.float32),
            "valid_cover_pct": np.full((rows, columns), np.nan, dtype=np.float32),
        }
        # Reading ten coarse rows at once avoids thousands of tiny GeoTIFF reads
        # while retaining a bounded ~32 MB array per source raster.
        coarse_block_rows = 10
        for coarse_row in range(0, rows, coarse_block_rows):
            batch_rows = min(coarse_block_rows, rows - coarse_row)
            start = coarse_row * factor
            window = rasterio.windows.Window(0, start, occurrence.width, batch_rows * factor)
            occ = occurrence.read(1, window=window)
            sea = seasonality.read(1, window=window)
            # The producer's product metadata uses 255 for no data.  The files
            # do not consistently advertise it as a GDAL nodata value.
            valid = (occ != 255) & (sea != 255) & (occ <= 100) & (sea <= 12)
            shape = (batch_rows, factor, columns, factor)
            valid_count = valid.reshape(shape).sum(axis=(1, 3))
            safe_count = np.maximum(valid_count, 1)
            occ_sum = np.where(valid, occ, 0).reshape(shape).sum(axis=(1, 3))
            permanent = ((sea == 12) & valid).reshape(shape).sum(axis=(1, 3))
            seasonal = ((sea >= 1) & (sea < 12) & valid).reshape(shape).sum(axis=(1, 3))
            occurrence_mean = occ_sum / safe_count
            permanent_pct = permanent / (factor * factor) * 100.0
            seasonal_pct = seasonal / (factor * factor) * 100.0
            valid_pct = valid_count / (factor * factor) * 100.0
            for values in (occurrence_mean, permanent_pct, seasonal_pct, valid_pct):
                values[valid_count == 0] = np.nan
            result_slice = slice(coarse_row, coarse_row + batch_rows)
            output["occurrence_mean_pct"][result_slice] = occurrence_mean
            output["permanent_cover_pct"][result_slice] = permanent_pct
            output["seasonal_cover_pct"][result_slice] = seasonal_pct
            output["valid_cover_pct"][result_slice] = valid_pct
        return output


def _centres_for_mask(tile: dict[str, object], mask: np.ndarray) -> np.ndarray:
    rows, columns = np.where(mask)
    transform = tile["transform"]
    longitudes, latitudes = rasterio.transform.xy(transform, rows, columns, offset="center")
    return np.column_stack([latitudes, longitudes])


def _distance_km(query_lat_lon: np.ndarray, candidate_lat_lon: np.ndarray) -> np.ndarray:
    if candidate_lat_lon.size == 0:
        return np.full(len(query_lat_lon), np.nan)
    tree = BallTree(np.radians(candidate_lat_lon), metric="haversine")
    radians, _ = tree.query(np.radians(query_lat_lon), k=1)
    return radians[:, 0] * EARTH_RADIUS_KM


def _tile_for_point(tiles: list[dict[str, object]], longitude: float, latitude: float) -> dict[str, object]:
    for tile in tiles:
        left, bottom, right, top = tile["bounds"]
        if left <= longitude <= right and bottom <= latitude <= top:
            return tile
    raise ValueError(f"No JRC source tile covers ({longitude}, {latitude})")


def _sample_tile(tile: dict[str, object], longitude: float, latitude: float) -> tuple[float, float, float, float]:
    transform = tile["transform"]
    row, column = rasterio.transform.rowcol(transform, longitude, latitude)
    if not (0 <= row < tile["occurrence_mean_pct"].shape[0] and 0 <= column < tile["occurrence_mean_pct"].shape[1]):
        return (np.nan, np.nan, np.nan, np.nan)
    return tuple(
        float(tile[key][row, column])
        for key in ("occurrence_mean_pct", "permanent_cover_pct", "seasonal_cover_pct", "valid_cover_pct")
    )


def build_index(grid_path: Path, output_path: Path) -> dict[str, object]:
    grid = gpd.read_parquet(grid_path).to_crs("EPSG:4326")
    required = {"grid_id", "centroid_lat", "centroid_lon"}
    missing = required - set(grid.columns)
    if missing:
        raise ValueError(f"Grid misses required fields: {sorted(missing)}")

    tiles: list[dict[str, object]] = []
    for tile_id in TILES:
        occurrence = RAW_DIR / f"occurrence_{tile_id}_v1_5_2024.tif"
        seasonality = RAW_DIR / f"seasonality_{tile_id}_v1_5_2024.tif"
        if not occurrence.is_file() or not seasonality.is_file():
            raise FileNotFoundError("Run scripts/acquire_jrc_surface_water.py first.")
        tiles.append(_coarse_tile(occurrence, seasonality))

    permanent_candidates = np.vstack(
        [_centres_for_mask(tile, np.nan_to_num(tile["permanent_cover_pct"]) > 0) for tile in tiles]
    )
    surface_candidates = np.vstack(
        [
            _centres_for_mask(
                tile,
                (np.nan_to_num(tile["permanent_cover_pct"]) + np.nan_to_num(tile["seasonal_cover_pct"])) > 0,
            )
            for tile in tiles
        ]
    )
    queries = grid[["centroid_lat", "centroid_lon"]].to_numpy(dtype=float)
    sampled = np.asarray(
        [_sample_tile(_tile_for_point(tiles, lon, lat), lon, lat) for lat, lon in queries], dtype=float
    )
    output = pd.DataFrame(
        {
            "grid_id": grid["grid_id"].astype(str),
            "jrc_water_occurrence_mean_pct": sampled[:, 0],
            "jrc_permanent_water_cover_pct": sampled[:, 1],
            "jrc_seasonal_water_cover_pct": sampled[:, 2],
            "jrc_valid_observation_cover_pct": sampled[:, 3],
            "nearest_permanent_surface_water_km": _distance_km(queries, permanent_candidates),
            "nearest_any_surface_water_km": _distance_km(queries, surface_candidates),
        }
    )
    if output["jrc_valid_observation_cover_pct"].isna().any() or (output["jrc_valid_observation_cover_pct"] == 0).any():
        raise RuntimeError("At least one project grid has no valid JRC water observation coverage")
    if output["nearest_any_surface_water_km"].isna().any():
        raise RuntimeError("At least one project grid has no candidate surface water")

    # A descriptive relative index: 100 is closest to permanent water within this project grid,
    # 0 is furthest.  It is deliberately not a claim of pumpability or a resource multiplier.
    distance = output["nearest_permanent_surface_water_km"]
    distance_min, distance_max = float(distance.min()), float(distance.max())
    if distance_max == distance_min:
        index = pd.Series(100.0, index=output.index)
    else:
        index = 100 * (distance_max - distance) / (distance_max - distance_min)
    output["water_access_index_relative_0_100"] = index.round(2)
    output["water_source_status"] = "candidate_requires_operator_verification"
    pupr_summary: dict[str, object] = {"available": False}
    if PUPR_BORE_WELLS.is_file():
        bore_wells = gpd.read_parquet(PUPR_BORE_WELLS).to_crs("EPSG:4326")
        valid_points = bore_wells.loc[
            bore_wells.geometry.notna() & ~bore_wells.geometry.is_empty & bore_wells.geometry.is_valid
        ].copy()
        if valid_points.empty:
            raise RuntimeError("PUPR bore-well file exists but has no valid point candidates")
        candidates = np.column_stack([valid_points.geometry.y, valid_points.geometry.x])
        output["nearest_pupr_bore_well_candidate_km"] = _distance_km(queries, candidates)
        output["pupr_bore_well_candidate_status"] = (
            "candidate_requires_local_yield_quality_access_verification"
        )
        pupr_summary = {
            "available": True,
            "candidate_points": int(len(valid_points)),
            "definition": (
                "Distance to a PUPR-mapped bore-well candidate. It is separate from the JRC-only "
                "surface-water index and must not be treated as a confirmed fire-water source."
            ),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)
    output.to_csv(output_path.with_suffix(".csv"), index=False)
    return {
        "status": "passed",
        "grid_rows": int(len(output)),
        "columns": list(output.columns),
        "valid_observation_coverage_min_pct": round(float(output["jrc_valid_observation_cover_pct"].min()), 2),
        "nearest_permanent_water_km": {
            "min": round(distance_min, 3),
            "median": round(float(distance.median()), 3),
            "max": round(distance_max, 3),
        },
        "nearest_any_water_km": {
            "min": round(float(output["nearest_any_surface_water_km"].min()), 3),
            "median": round(float(output["nearest_any_surface_water_km"].median()), 3),
            "max": round(float(output["nearest_any_surface_water_km"].max()), 3),
        },
        "definition": {
            "water_access_index_relative_0_100": (
                "Min-max normalised inverse distance from each grid centroid to the nearest "
                "JRC pixel classified as permanent water (seasonality=12); relative spatial rank only."
            ),
            "candidate_water": (
                "A satellite-classified surface-water location; field verification of water volume, "
                "pumpability, legal permission, and vehicle access remains mandatory."
            ),
        },
        "pupr_bore_well_candidates": pupr_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=Path, default=PROCESSED_DIR / "dim_grid.parquet")
    parser.add_argument("--output", type=Path, default=PROCESSED_DIR / "water_access_grid.parquet")
    args = parser.parse_args()
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "JRC Global Surface Water v1.5 aggregated 1984-2024",
        "attribution": "Source: EC JRC/Google",
        **build_index(args.grid, args.output),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "water_access_index_quality.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
