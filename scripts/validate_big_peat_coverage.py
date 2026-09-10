#!/usr/bin/env python3
"""Audit geographic scope and render a QA map for BIG peat-function data.

The BIG layer maps peat-ecosystem functions.  Its absence from an area means
only that this layer does not map that area; it must not be interpreted as a
finding that the area is mineral soil or operationally safe.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from shapely import union_all


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PEAT_PATH = PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_function_kalimantan.parquet"
PEAT_EXTENT_PATH = PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_extent_kalimantan.parquet"
BOUNDARY_PATH = PROJECT_ROOT / "data" / "raw" / "boundaries" / "idn_adm1.geojson"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "peatland"
REPORT_DIR = PROJECT_ROOT / "reports"
KALIMANTAN_NAMES = [
    "West Kalimantan",
    "Central Kalimantan",
    "South Kalimantan",
    "East Kalimantan",
    "North Kalimantan",
]


def area_km2(geometry) -> float:
    if geometry.is_empty:
        return 0.0
    return float(gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs("EPSG:6933").area.iloc[0] / 1_000_000)


def provinces() -> gpd.GeoDataFrame:
    data = gpd.read_file(BOUNDARY_PATH).to_crs("EPSG:4326")
    name_column = next(
        (column for column in ("shapeName", "NAME_1", "name") if column in data.columns), None
    )
    if name_column is None:
        raise ValueError("Unable to identify the ADM1 name field")
    selected = data.loc[data[name_column].isin(KALIMANTAN_NAMES), [name_column, "geometry"]].copy()
    selected = selected.rename(columns={name_column: "province"})
    if set(selected["province"]) != set(KALIMANTAN_NAMES):
        raise ValueError("ADM1 file does not contain all five requested Kalimantan provinces")
    return selected.sort_values("province").reset_index(drop=True)


def coverage_rows(
    peat: gpd.GeoDataFrame, peat_extent: gpd.GeoDataFrame, adm1: gpd.GeoDataFrame
) -> pd.DataFrame:
    all_layer = union_all(peat.geometry.values)
    peat_location_layer = union_all(peat_extent.geometry.values)
    peat_layer = union_all(peat.loc[peat["tnh_gambut"] == "Tanah Gambut", "geometry"].values)
    confirmed_layer = union_all(
        peat.loc[peat["peat_thickness_gt_1_5m_status"] == "confirmed_above_1_5m", "geometry"].values
    )
    ambiguous_layer = union_all(
        peat.loc[
            peat["peat_thickness_gt_1_5m_status"] == "ambiguous_class_intersects_1_5m", "geometry"
        ].values
    )
    rows: list[dict[str, object]] = []
    for province in adm1.itertuples(index=False):
        province_area = area_km2(province.geometry)
        mapped = province.geometry.intersection(all_layer)
        mapped_peat_location = province.geometry.intersection(peat_location_layer)
        mapped_peat = province.geometry.intersection(peat_layer)
        confirmed = province.geometry.intersection(confirmed_layer)
        ambiguous = province.geometry.intersection(ambiguous_layer)
        candidate_count = int(len(peat.iloc[list(peat.sindex.query(province.geometry, predicate="intersects"))]))
        peat_location_candidate_count = int(
            len(peat_extent.iloc[list(peat_extent.sindex.query(province.geometry, predicate="intersects"))])
        )
        mapped_area = area_km2(mapped)
        rows.append(
            {
                "province": province.province,
                "province_area_km2": round(province_area, 3),
                "source_feature_candidates": candidate_count,
                "peat_location_feature_candidates": peat_location_candidate_count,
                "function_layer_mapped_area_km2": round(mapped_area, 3),
                "function_layer_mapped_coverage_pct": round((mapped_area / province_area) * 100, 4),
                "big_mapped_peat_location_area_km2": round(area_km2(mapped_peat_location), 3),
                "big_mapped_peat_location_coverage_pct": round(
                    (area_km2(mapped_peat_location) / province_area) * 100, 4
                ),
                "mapped_tanah_gambut_area_km2": round(area_km2(mapped_peat), 3),
                "mapped_tanah_gambut_coverage_pct": round(
                    (area_km2(mapped_peat) / province_area) * 100, 4
                ),
                "confirmed_gt_1_5m_area_km2": round(area_km2(confirmed), 3),
                "ambiguous_1_5m_class_area_km2": round(area_km2(ambiguous), 3),
                "not_mapped_as_peat_by_big_location_layer_area_km2": round(
                    province_area - area_km2(mapped_peat_location), 3
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("province").reset_index(drop=True)


def render_map(
    peat: gpd.GeoDataFrame,
    peat_extent: gpd.GeoDataFrame,
    adm1: gpd.GeoDataFrame,
    coverage: pd.DataFrame,
    path: Path,
) -> None:
    fig, (map_axis, chart_axis) = plt.subplots(
        1, 2, figsize=(16, 10), gridspec_kw={"width_ratios": [2.1, 1]}
    )
    adm1.plot(ax=map_axis, color="#f3f4f6", edgecolor="#334155", linewidth=0.7)
    peat_extent.plot(
        ax=map_axis,
        color="#22c55e",
        alpha=0.35,
        linewidth=0,
        label="BIG mapped peat location (layer 6)",
    )
    peat.loc[peat["tnh_gambut"] != "Tanah Gambut"].plot(
        ax=map_axis, color="#cbd5e1", linewidth=0, label="Mapped non-peat class"
    )
    peat.loc[peat["peat_thickness_gt_1_5m_status"] == "confirmed_above_1_5m"].plot(
        ax=map_axis, color="#be123c", linewidth=0, label="Peat class confirmed >1.5 m"
    )
    peat.loc[peat["peat_thickness_gt_1_5m_status"] == "confirmed_not_above_1_5m"].plot(
        ax=map_axis, color="#f59e0b", linewidth=0, label="Peat class <=1.5 m"
    )
    peat.loc[
        peat["peat_thickness_gt_1_5m_status"] == "ambiguous_class_intersects_1_5m"
    ].plot(ax=map_axis, color="#7c3aed", linewidth=0, label="Class intersects 1.5 m")
    adm1.boundary.plot(ax=map_axis, color="#0f172a", linewidth=0.8)
    map_axis.set_title("BIG Peta Fungsi Ekosistem Gambut 1:50.000 — Kalimantan QA")
    map_axis.set_axis_off()
    map_axis.legend(loc="lower left", fontsize=8, frameon=True)

    location_values = coverage["big_mapped_peat_location_coverage_pct"]
    function_values = coverage["function_layer_mapped_coverage_pct"]
    chart_axis.barh(
        coverage["province"], location_values, color="#16a34a", label="BIG mapped peat location"
    )
    chart_axis.barh(
        coverage["province"], function_values, color="#2563eb", alpha=0.8, label="BIG function layer"
    )
    chart_axis.set_xlim(0, 100)
    chart_axis.set_xlabel("Share of province area (%)")
    chart_axis.set_title("BIG peat-location vs function-layer footprint")
    for index, value in enumerate(location_values):
        chart_axis.text(min(value + 1, 96), index, f"{value:.1f}%", va="center", fontsize=9)
    chart_axis.grid(axis="x", alpha=0.25)
    chart_axis.legend(loc="lower right", fontsize=8)
    fig.suptitle(
        "Green is BIG mapped peat location; coloured patches are the separate BIG function/thickness layer.",
        y=0.02,
        fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    peat = gpd.read_parquet(PEAT_PATH).to_crs("EPSG:4326")
    peat_extent = gpd.read_parquet(PEAT_EXTENT_PATH).to_crs("EPSG:4326")
    required = {"tnh_gambut", "peat_thickness_gt_1_5m_status", "geometry"}
    missing = required - set(peat.columns)
    if missing:
        raise ValueError(f"Processed BIG peat data is missing columns: {sorted(missing)}")
    if peat.empty or peat.geometry.is_empty.any() or (~peat.geometry.is_valid).any():
        raise ValueError("Processed BIG peat data is empty or has invalid geometry")
    if (
        peat_extent.empty
        or peat_extent.geometry.is_empty.any()
        or (~peat_extent.geometry.is_valid).any()
    ):
        raise ValueError("Processed BIG peat-location data is empty or has invalid geometry")
    adm1 = provinces()
    kalimantan_union = union_all(adm1.geometry.values)
    outside_area_km2 = area_km2(union_all(peat.geometry.values).difference(kalimantan_union))
    peat_location_outside_area_km2 = area_km2(
        union_all(peat_extent.geometry.values).difference(kalimantan_union)
    )
    coverage = coverage_rows(peat, peat_extent, adm1)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "big_peat_function_kalimantan.coverage_by_province.csv"
    png_path = REPORT_DIR / "big_peat_function_kalimantan_coverage.png"
    coverage.to_csv(csv_path, index=False)
    render_map(peat, peat_extent, adm1, coverage, png_path)
    validation = {
        "status": "passed",
        "scope": "five Kalimantan provinces exactly, using project ADM1 boundaries",
        "processed_feature_count": int(len(peat)),
        "empty_geometries": int(peat.geometry.is_empty.sum()),
        "invalid_geometries": int((~peat.geometry.is_valid).sum()),
        "function_layer_outside_five_province_boundary_area_km2": round(outside_area_km2, 9),
        "peat_location_layer_outside_five_province_boundary_area_km2": round(
            peat_location_outside_area_km2, 9
        ),
        "all_five_provinces_have_function_layer_features": bool(
            (coverage["source_feature_candidates"] > 0).all()
        ),
        "all_five_provinces_have_peat_location_features": bool(
            (coverage["peat_location_feature_candidates"] > 0).all()
        ),
        "coverage_csv": csv_path.name,
        "qa_map": str(png_path.relative_to(PROJECT_ROOT)),
        "interpretation": (
            "Mapped peat-location coverage (layer 6) is reported separately from the smaller "
            "function/thickness layer (layer 48). An absence from layer 48 is unknown for depth, "
            "not evidence of no peat."
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if (
        outside_area_km2 > 0.001
        or peat_location_outside_area_km2 > 0.001
        or not validation["all_five_provinces_have_function_layer_features"]
        or not validation["all_five_provinces_have_peat_location_features"]
    ):
        validation["status"] = "failed"
        raise RuntimeError(json.dumps(validation, ensure_ascii=False, indent=2))
    (OUTPUT_DIR / "big_peat_function_kalimantan.coverage_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(coverage.to_string(index=False))
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
