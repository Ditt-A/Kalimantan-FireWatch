#!/usr/bin/env python3
"""Create auditable Kalimantan burned-footprint patches from MapBiomas monthly rasters.

MapBiomas Monthly Fire is an observed burned-area product, not a live fire
perimeter feed.  The source's native grid is aggregated to approximately
320 metres with max-preserving semantics (any burned source pixel marks a
derived cell) before vectorisation.  This makes a tractable planning layer but
necessarily enlarges small patches; it must never be displayed as a tactical
fire edge or used directly for hose-length calculations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, shapes
from rasterio.windows import from_bounds
from shapely.geometry import shape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "mapbiomas"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "mapbiomas_burned_footprints"
MONTHS = tuple(range(1, 7))
# Native monthly pixels are ~10 m. A 32x aggregation is roughly 320 m at the
# equator and bounds memory while retaining a meaningful footprint layer.
DOWNSAMPLE_FACTOR = 32
SOURCE_PAGE = "https://fire.mapbiomas.id/id/downloads"
METHODOLOGY_PAGE = "https://fire.mapbiomas.id/id/atbd?cat=monthly"
KALIMANTAN_NAMES = {
    "West Kalimantan",
    "Central Kalimantan",
    "South Kalimantan",
    "East Kalimantan",
    "North Kalimantan",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def provinces() -> gpd.GeoDataFrame:
    source = gpd.read_file(PROJECT_ROOT / "data" / "raw" / "boundaries" / "idn_adm1.geojson")
    name_column = next(
        (column for column in ("shapeName", "NAME_1", "name") if column in source.columns), None
    )
    if name_column is None:
        raise ValueError("Unable to identify ADM1 name field")
    selected = source.loc[source[name_column].isin(KALIMANTAN_NAMES)].copy()
    if len(selected) != 5:
        raise ValueError(f"Expected five Kalimantan provinces, found {len(selected)}")
    return selected.to_crs("EPSG:4326")


def source_path(month: int) -> Path:
    return RAW_DIR / f"burned_area_monthly_2026_{month:02d}.tif"


def derived_transform(source: rasterio.io.DatasetReader, window, width: int, height: int) -> Affine:
    return source.window_transform(window) * Affine.scale(window.width / width, window.height / height)


def extract_month(month: int, boundary: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    source_file = source_path(month)
    if not source_file.is_file() or source_file.stat().st_size < 1024:
        raise FileNotFoundError(f"MapBiomas monthly raster missing: {source_file}")
    with rasterio.open(source_file) as source:
        if str(source.crs) != "EPSG:4326":
            raise ValueError(f"Unexpected CRS for {source_file.name}: {source.crs}")
        window = from_bounds(*boundary.total_bounds, transform=source.transform)
        window = window.round_offsets().round_lengths()
        output_height = math.ceil(window.height / DOWNSAMPLE_FACTOR)
        output_width = math.ceil(window.width / DOWNSAMPLE_FACTOR)
        # Average is the only overview resampler offered by Rasterio's direct
        # read API. Comparing the float output to zero preserves any non-zero
        # input cell for these non-negative burned/not-burned source values.
        values = source.read(
            1,
            window=window,
            out_shape=(output_height, output_width),
            out_dtype="float32",
            resampling=Resampling.average,
            masked=False,
        )
        transform = derived_transform(source, window, output_width, output_height)
        within_kalimantan = geometry_mask(
            [geometry.__geo_interface__ for geometry in boundary.geometry],
            out_shape=values.shape,
            transform=transform,
            invert=True,
        )

    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"Invalid negative/non-finite source values in {source_file.name}")
    burned = (values > 0) & within_kalimantan
    rows: list[dict[str, object]] = []
    for geometry, value in shapes(burned.astype("uint8"), mask=burned, transform=transform):
        if int(value) != 1:
            continue
        rows.append(
            {
                "source_product": "MapBiomas Indonesia Monthly Fire",
                "observed_month": f"2026-{month:02d}",
                "representation": "coarse_observed_burned_footprint_patch_not_live_perimeter",
                "derived_cell_size_m_approx": 320,
                "source_file": source_file.name,
                "source_sha256": sha256(source_file),
                "geometry": shape(geometry),
            }
        )
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    if not frame.empty:
        # Clipping avoids retaining a coarse cell whose centre/tiny overlap is
        # just outside the true five-province geometry. It is not a perimeter
        # refinement.
        clipped = gpd.overlay(frame, boundary[["geometry"]], how="intersection", keep_geom_type=True)
        frame = clipped[frame.columns].copy()
        equal_area = frame.to_crs("EPSG:6933")
        # This is the area of the coarse footprint envelope, not a measured
        # burned-area statistic. Any small burned source pixel marks an entire
        # derived cell before vectorisation.
        frame["coarse_footprint_envelope_area_ha"] = (equal_area.geometry.area / 10_000).round(3)
        frame = frame.loc[frame["coarse_footprint_envelope_area_ha"] > 0].copy()
    summary: dict[str, object] = {
        "month": f"2026-{month:02d}",
        "source_file": source_file.name,
        "source_bytes": source_file.stat().st_size,
        "source_sha256": sha256(source_file),
        "derived_grid_shape": [int(output_height), int(output_width)],
        "positive_derived_cells_before_clip": int(burned.sum()),
        "patches": int(len(frame)),
        "coarse_footprint_envelope_area_ha": round(
            float(frame["coarse_footprint_envelope_area_ha"].sum()), 3
        )
        if not frame.empty
        else 0.0,
        "empty_geometries": int(frame.geometry.is_empty.sum()) if not frame.empty else 0,
        "invalid_geometries": int((~frame.geometry.is_valid).sum()) if not frame.empty else 0,
    }
    return frame, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--months", nargs="+", type=int, default=list(MONTHS), help="2026 months available locally"
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    months = tuple(sorted(set(args.months)))
    if not months or any(month not in MONTHS for month in months):
        raise ValueError(f"Only locally available 2026 months are {MONTHS}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    boundary = provinces()
    summaries: list[dict[str, object]] = []
    output_paths: list[Path] = []
    for month in months:
        frame, summary = extract_month(month, boundary)
        output = output_dir / f"mapbiomas_burned_footprints_2026_{month:02d}.parquet"
        frame.to_parquet(output, index=False)
        summary["output_file"] = output.name
        summary["output_bytes"] = output.stat().st_size
        summary["output_sha256"] = sha256(output)
        summaries.append(summary)
        output_paths.append(output)
        print(json.dumps(summary), flush=True)

    all_frames = [gpd.read_parquet(path) for path in output_paths]
    # Concat retains the GeoParquet schemas and Shapely geometries from each
    # month without trying to coerce them through a NumPy record array.
    combined = pd.concat(all_frames, ignore_index=True) if all_frames else gpd.GeoDataFrame()
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    month_range = f"2026_{months[0]:02d}_{months[-1]:02d}"
    combined_output = output_dir / f"mapbiomas_burned_footprints_kalimantan_{month_range}.parquet"
    combined.to_parquet(combined_output, index=False)
    validation: dict[str, object] = {
        "status": "passed",
        "months": summaries,
        "combined_file": combined_output.name,
        "combined_rows": int(len(combined)),
        "combined_bytes": combined_output.stat().st_size,
        "combined_sha256": sha256(combined_output),
        "empty_geometries": int(combined.geometry.is_empty.sum()),
        "invalid_geometries": int((~combined.geometry.is_valid).sum()),
        "unique_months": sorted(combined["observed_month"].unique().tolist()) if not combined.empty else [],
    }
    if (
        validation["combined_rows"] == 0
        or validation["empty_geometries"]
        or validation["invalid_geometries"]
        or validation["unique_months"] != [f"2026-{month:02d}" for month in months]
    ):
        validation["status"] = "failed"
        raise RuntimeError(json.dumps(validation, indent=2))
    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_product": "MapBiomas Indonesia Monthly Fire",
        "source_page": SOURCE_PAGE,
        "methodology": METHODOLOGY_PAGE,
        "source_coverage": "Monthly 2026-01 through 2026-06 downloaded by the project",
        "derivation": (
            "Kalimantan clip; non-negative burned raster aggregated 32x with any non-zero source "
            "coverage retained, then vectorised and clipped to the five-province boundary."
        ),
        "limitations": [
            "Observed monthly burned-area footprint, not a live/incident fire perimeter.",
            "Approximately 320 m derived grid can enlarge small burned patches and must not drive tactical hose-length calculations.",
            "MapBiomas publication latency and cloud/algorithm limitations apply; cross-check active incidents with FIRMS/SiPongi and field command.",
        ],
    }
    (output_dir / "mapbiomas_burned_footprints.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "mapbiomas_burned_footprints.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
