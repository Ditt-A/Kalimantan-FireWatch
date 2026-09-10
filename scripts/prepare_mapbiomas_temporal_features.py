#!/usr/bin/env python3
"""Prepare validated, province-year MapBiomas features for leakage-safe modelling."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/mapbiomas"
INTERIM = ROOT / "data/interim"
REPORTS = ROOT / "reports"
PROVINCE_MAP = {
    "Kalimantan Barat": "West Kalimantan",
    "Kalimantan Tengah": "Central Kalimantan",
    "Kalimantan Selatan": "South Kalimantan",
    "Kalimantan Timur": "East Kalimantan",
    "Kalimantan Utara": "North Kalimantan",
}
CLASS_FEATURES = {
    "lc_natural_share_pct": lambda frame: frame["class_level_0"].eq("Natural"),
    "lc_anthropic_share_pct": lambda frame: frame["class_level_0"].eq("Anthropic"),
    "lc_peat_swamp_share_pct": lambda frame: frame["class"].eq(76),
    "lc_oil_palm_share_pct": lambda frame: frame["class"].eq(35),
    "lc_pulpwood_share_pct": lambda frame: frame["class"].eq(9),
    "lc_agriculture_share_pct": lambda frame: frame["class_level_1"].eq("Agriculture"),
    "lc_water_share_pct": lambda frame: frame["class_level_1"].eq("Water Body"),
}


def kalimantan_boundaries() -> tuple[gpd.GeoDataFrame, str]:
    adm1 = gpd.read_file(ROOT / "data/raw/boundaries/idn_adm1.geojson")
    name_column = next(column for column in ("shapeName", "NAME_1", "name") if column in adm1)
    provinces = adm1[adm1[name_column].isin(PROVINCE_MAP.values())].copy()
    if len(provinces) != 5:
        raise ValueError(f"Expected five Kalimantan provinces, received {len(provinces)}")
    return provinces, name_column


def landcover_features() -> pd.DataFrame:
    source = RAW / "landcover_statistics_2023_2024_collection4_1.xlsx"
    coverage = pd.read_excel(source, sheet_name="COVERAGE")
    coverage = coverage.loc[coverage["territory_level_3"].isin(PROVINCE_MAP)].copy()
    coverage["province"] = coverage["territory_level_3"].map(PROVINCE_MAP)
    records: list[dict[str, object]] = []
    for year in (2023, 2024):
        value = f"y{year}"
        for province, subset in coverage.groupby("province", sort=True):
            total = float(subset[value].sum())
            if total <= 0:
                raise ValueError(f"Land-cover total is not positive for {province} {year}")
            record: dict[str, object] = {"province": province, "source_year": year}
            for feature, selector in CLASS_FEATURES.items():
                record[feature] = float(subset.loc[selector(subset), value].sum() / total * 100)
            records.append(record)
    result = pd.DataFrame(records)
    if result.duplicated(["province", "source_year"]).any() or len(result) != 10:
        raise ValueError("Land-cover province-year table is incomplete or duplicated")
    return result


def burned_share_for_year(path: Path, provinces: gpd.GeoDataFrame, name_column: str) -> pd.DataFrame:
    with rasterio.open(path) as source:
        window = from_bounds(*provinces.total_bounds, transform=source.transform).round_offsets().round_lengths()
        scale = min(1.0, 2000 / max(window.width, window.height))
        height, width = max(1, round(window.height * scale)), max(1, round(window.width * scale))
        raster = source.read(1, window=window, out_shape=(height, width), resampling=Resampling.average, masked=True)
        transform = source.window_transform(window) * Affine.scale(window.width / width, window.height / height)
    valid = ~np.ma.getmaskarray(raster)
    rows = []
    for _, province in provinces.iterrows():
        within = geometry_mask([province.geometry.__geo_interface__], out_shape=raster.shape, transform=transform, invert=True)
        values = np.asarray(raster.data[within & valid])
        values = values[np.isfinite(values)]
        if not len(values):
            raise ValueError(f"No burned-area sample cells for {province[name_column]}")
        rows.append({
            "province": province[name_column],
            "burned_share_pct": float(np.clip(values, 0, 1).mean() * 100),
        })
    return pd.DataFrame(rows)


def main() -> int:
    landcover = landcover_features()
    provinces, name_column = kalimantan_boundaries()
    burned_parts = []
    for year in (2023, 2024):
        path = RAW / f"burned_area_{year}_collection2.tif"
        part = burned_share_for_year(path, provinces, name_column)
        part["source_year"] = year
        burned_parts.append(part)
    burned = pd.concat(burned_parts, ignore_index=True)
    result = landcover.merge(burned, on=["province", "source_year"], how="inner", validate="one_to_one")
    expected = set(PROVINCE_MAP.values())
    if set(result["province"]) != expected or len(result) != 10:
        raise ValueError("Temporal MapBiomas features do not cover every province and year")
    metric_columns = [column for column in result if column not in {"province", "source_year"}]
    if result[metric_columns].isna().any().any() or (result[metric_columns] < 0).any().any():
        raise ValueError("Temporal MapBiomas features contain invalid values")
    INTERIM.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    output = INTERIM / "mapbiomas_province_year_features.parquet"
    result.sort_values(["source_year", "province"]).to_parquet(output, index=False)
    report = {
        "status": "passed",
        "output": str(output),
        "rows": int(len(result)),
        "years": sorted(result["source_year"].unique().tolist()),
        "provinces": sorted(result["province"].unique().tolist()),
        "feature_columns": metric_columns,
    }
    (REPORTS / "mapbiomas_temporal_feature_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
