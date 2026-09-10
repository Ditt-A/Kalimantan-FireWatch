"""Attach source-separated historical context to reviewed Kalteng incidents.

Enrichment never overwrites report observations. NASA FIRMS and ERA5 fields are
explicitly associated with the *report monitoring window*, because many reports
do not state a precise incident date. BIG peat is a map lookup, not a field
measurement. Rows without an explicit coordinate remain un-enriched.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment.parquet"
OUTPUT = ROOT / "data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment_enriched.parquet"
OUTPUT_CSV = OUTPUT.with_suffix(".csv")
QUALITY = ROOT / "data/processed/kalteng_historical_deployments/enrichment_quality_report.json"
FIRMS_DIR = ROOT / "data/raw/firms_historical_kalteng_sp"
ERA5_DIR = ROOT / "data/raw/era5_land"
PEAT_PATH = ROOT / "data/processed/peatland/big_peat_extent_kalimantan.parquet"


def load_firms() -> tuple[pd.DataFrame, list[str]]:
    paths = sorted(FIRMS_DIR.glob("firms_viirs_snpp_sp_*.parquet"))
    if not paths:
        return pd.DataFrame(), []
    data = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    required = {"latitude", "longitude", "acq_date", "frp", "source_product"}
    if required - set(data.columns):
        raise ValueError(f"FIRMS historical source lacks {sorted(required - set(data.columns))}")
    data["acq_date"] = pd.to_datetime(data["acq_date"], errors="coerce").dt.normalize()
    return data.dropna(subset=["acq_date", "latitude", "longitude"]), [str(path.relative_to(ROOT)) for path in paths]


def add_firms(data: pd.DataFrame) -> pd.DataFrame:
    firms, paths = load_firms()
    data["firms_report_window_detection_count_10km"] = pd.NA
    data["firms_report_window_max_frp_mw"] = pd.NA
    data["firms_report_window_nearest_detection_km"] = pd.NA
    data["firms_association_status"] = "not_joined_no_coordinate"
    data["firms_source"] = "NASA FIRMS VIIRS_SNPP_SP; report-window association only"
    data["firms_source_files"] = json.dumps(paths)
    if firms.empty:
        data["firms_association_status"] = "source_unavailable"
        return data
    for index, row in data.loc[data["latitude"].notna() & data["longitude"].notna()].iterrows():
        start, end = pd.Timestamp(row["report_period_start"]), pd.Timestamp(row["report_period_end"])
        candidates = firms.loc[firms["acq_date"].between(start, end)].copy()
        if candidates.empty:
            data.at[index, "firms_association_status"] = "no_detection_in_report_window"
            continue
        latitude_scale = 111.32
        longitude_scale = latitude_scale * np.cos(np.deg2rad(float(row["latitude"])))
        candidates["distance_km"] = np.hypot(
            (candidates["latitude"] - float(row["latitude"])) * latitude_scale,
            (candidates["longitude"] - float(row["longitude"])) * longitude_scale,
        )
        near = candidates.loc[candidates["distance_km"] <= 10]
        data.at[index, "firms_report_window_nearest_detection_km"] = float(candidates["distance_km"].min())
        if near.empty:
            data.at[index, "firms_association_status"] = "no_detection_within_10km_report_window"
        else:
            data.at[index, "firms_report_window_detection_count_10km"] = int(len(near))
            data.at[index, "firms_report_window_max_frp_mw"] = float(near["frp"].max())
            data.at[index, "firms_association_status"] = "candidate_same_report_window_within_10km_not_a_perimeter_match"
    return data


def add_peat(data: pd.DataFrame) -> pd.DataFrame:
    data["big_peat_location_mapped"] = pd.NA
    data["big_peat_lookup_status"] = "not_joined_no_coordinate"
    data["big_peat_source"] = "BIG Peta Lahan Gambut; spatial map lookup"
    if not PEAT_PATH.exists():
        data["big_peat_lookup_status"] = "source_unavailable"
        return data
    peat = gpd.read_parquet(PEAT_PATH).to_crs("EPSG:4326")
    for index, row in data.loc[data["latitude"].notna() & data["longitude"].notna()].iterrows():
        point = Point(float(row["longitude"]), float(row["latitude"]))
        hits = peat.iloc[list(peat.sindex.query(point, predicate="intersects"))]
        hits = hits.loc[hits.geometry.covers(point)]
        if len(hits) == 1:
            data.at[index, "big_peat_location_mapped"] = True
            data.at[index, "big_peat_lookup_status"] = "single_BIG_peat_location_feature"
        elif len(hits) > 1:
            data.at[index, "big_peat_lookup_status"] = "multiple_BIG_features_requires_review"
        else:
            data.at[index, "big_peat_location_mapped"] = False
            data.at[index, "big_peat_lookup_status"] = "not_in_BIG_location_layer_not_evidence_of_mineral"
    soil = data["soil_type"].fillna("").str.lower()
    data["reported_soil_BIG_peat_crosscheck"] = "not_assessable"
    mapped = data["big_peat_location_mapped"].eq(True).fillna(False)
    data.loc[soil.str.contains("gambut") & mapped, "reported_soil_BIG_peat_crosscheck"] = "consistent_reported_peat_and_BIG_mapped_peat"
    data.loc[soil.str.contains("mineral") & mapped, "reported_soil_BIG_peat_crosscheck"] = "reported_mineral_BIG_maps_peat_requires_source_review"
    return data


def weather_window(path: Path, latitude: float, longitude: float, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float] | None:
    if not path.exists():
        return None
    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        required = {"tp", "t2m", "swvl1"}
        if required - set(dataset.data_vars):
            raise ValueError(f"{path.name} lacks ERA5 variables {sorted(required - set(dataset.data_vars))}")
        time_name = "valid_time" if "valid_time" in dataset.coords else "time"
        # ERA5 window is defined by the report's monitoring dates, not an
        # assumed ignition time. Include all hourly observations through 23:00.
        selected = dataset[["tp", "t2m", "swvl1"]].sel(latitude=latitude, longitude=longitude, method="nearest")
        selected = selected.sel({time_name: slice(start, end + pd.Timedelta(hours=23))})
        if selected.sizes.get(time_name, 0) == 0:
            return None
        return {
            "era5_report_window_rainfall_mm": float((selected["tp"] * 1000).sum().item()),
            "era5_report_window_temperature_mean_c": float((selected["t2m"] - 273.15).mean().item()),
            "era5_report_window_temperature_max_c": float((selected["t2m"] - 273.15).max().item()),
            "era5_report_window_soil_moisture_mean": float(selected["swvl1"].mean().item()),
        }


def add_era5(data: pd.DataFrame) -> pd.DataFrame:
    columns = ("era5_report_window_rainfall_mm", "era5_report_window_temperature_mean_c", "era5_report_window_temperature_max_c", "era5_report_window_soil_moisture_mean")
    for column in columns:
        data[column] = pd.NA
    data["era5_association_status"] = "not_joined_no_coordinate"
    data["era5_source"] = "Copernicus ERA5-Land hourly; nearest grid cell over report monitoring window"
    for index, row in data.loc[data["latitude"].notna() & data["longitude"].notna()].iterrows():
        start, end = pd.Timestamp(row["report_period_start"]), pd.Timestamp(row["report_period_end"])
        # A report window can cross months. All required month files must be
        # present, otherwise values remain null rather than partly aggregated.
        months = pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS")
        paths = [ERA5_DIR / f"era5_land_{month:%Y_%m}.nc" for month in months]
        if not all(path.exists() for path in paths):
            data.at[index, "era5_association_status"] = "source_month_missing_for_full_report_window"
            continue
        parts = []
        for path in paths:
            part_start, part_end = max(start, pd.Timestamp(path.stem[-7:].replace("_", "-") + "-01")), min(end, pd.Timestamp(path.stem[-7:].replace("_", "-") + "-01") + pd.offsets.MonthEnd(0))
            values = weather_window(path, float(row["latitude"]), float(row["longitude"]), part_start, part_end)
            if values is None:
                parts = []
                break
            parts.append(values)
        if not parts:
            data.at[index, "era5_association_status"] = "no_ERA5_values_in_report_window"
            continue
        # For a cross-month window, recompute means with day weights. Current
        # corpus has one such case, so a daily/hourly weighted concat is the
        # future extension; do not invent a partial aggregate here.
        if len(parts) != 1:
            data.at[index, "era5_association_status"] = "cross_month_window_requires_hourly_weighted_aggregation"
            continue
        for key, value in parts[0].items():
            data.at[index, key] = value
        data.at[index, "era5_association_status"] = "nearest_grid_report_window_not_incident_time"
    return data


def main() -> None:
    data = pd.read_parquet(INPUT)
    data = add_firms(data)
    data = add_peat(data)
    data = add_era5(data)
    data["fwi_value"] = pd.NA
    data["fwi_source"] = "not available: no source-backed historical FWI calculation has been added"
    data["mapbiomas_event_landcover"] = pd.NA
    data["mapbiomas_source"] = "not joined: available project MapBiomas point layer is 2022 and post-dates 2018-2019 incidents"
    data.to_parquet(OUTPUT, index=False)
    data.to_csv(OUTPUT_CSV, index=False)
    report = {
        "status": "passed",
        "rows": len(data),
        "coordinate_rows": int(data["latitude"].notna().sum()),
        "firms_candidate_association_rows": int(data["firms_report_window_detection_count_10km"].notna().sum()),
        "BIG_peat_lookup_rows": int(data["big_peat_location_mapped"].notna().sum()),
        "ERA5_complete_window_rows": int(data["era5_report_window_rainfall_mm"].notna().sum()),
        "unavailable_by_design": {
            "fwi": "not computed from insufficient source variables",
            "mapbiomas_event_landcover": "not used because 2022 layer post-dates historical incidents",
        },
        "critical_interpretation": "FIRMS and ERA5 values describe the source report window; they do not prove incident-specific ignition, perimeter, or weather timing.",
    }
    QUALITY.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
