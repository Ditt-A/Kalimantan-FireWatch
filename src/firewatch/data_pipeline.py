"""Reproducible construction of Kalimantan FireWatch analysis tables.

The pipeline keeps raw source files immutable. It derives cleaned detections,
a clipped spatial grid, daily weather summaries, and a leakage-safe grid-week
table whose features are only available before the target week starts.
"""
from __future__ import annotations

import io
import json
import logging
import math
import zipfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from shapely.geometry import box
from sklearn.neighbors import NearestNeighbors

from firewatch.config import (
    EQUAL_AREA_CRS,
    FIRMS_REQUIRED_COLUMNS,
    GRID_SIZE_DEGREES,
    INTERIM_DIR,
    KALIMANTAN_PROVINCES,
    MIN_DATE,
    PROCESSED_DIR,
    RAW_DIR,
    REPORTS_DIR,
    WGS84,
)

LOGGER = logging.getLogger(__name__)


def _ensure_directories() -> None:
    for path in (INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _first_existing(paths: Iterable[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError("None of the expected source files were found.")


def _rolling_by_grid(
    frame: pd.DataFrame, column: str, window: int, aggregation: str
) -> pd.Series:
    """Calculate a shifted rolling feature for each grid with bounded memory use."""
    return frame.groupby("grid_id", sort=False)[column].transform(
        lambda values: getattr(
            values.rolling(window, min_periods=window), aggregation
        )().shift(1)
    )


def load_kalimantan_boundaries() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, object]:
    """Read national boundaries and keep the five Indonesian Kalimantan provinces."""
    adm1_path = RAW_DIR / "boundaries" / "idn_adm1.geojson"
    adm2_path = RAW_DIR / "boundaries" / "idn_adm2.geojson"
    adm1 = gpd.read_file(adm1_path).to_crs(WGS84)
    adm2 = gpd.read_file(adm2_path).to_crs(WGS84)

    kalimantan = adm1.loc[adm1["shapeName"].isin(KALIMANTAN_PROVINCES)].copy()
    if len(kalimantan) != len(KALIMANTAN_PROVINCES):
        missing = sorted(set(KALIMANTAN_PROVINCES) - set(kalimantan["shapeName"]))
        raise ValueError(f"Province boundary mismatch: {missing}")

    kalimantan_union = kalimantan.geometry.union_all()
    adm2 = adm2.loc[adm2.geometry.intersects(kalimantan_union)].copy()
    if adm2.empty:
        raise ValueError("No ADM2 boundaries intersect the Kalimantan boundary.")
    return kalimantan, adm2, kalimantan_union


def build_grid(kalimantan: gpd.GeoDataFrame, kalimantan_union: object) -> gpd.GeoDataFrame:
    """Create a 0.1° grid clipped exactly to the Kalimantan provincial union."""
    minx, miny, maxx, maxy = kalimantan.total_bounds
    start_x = math.floor(minx / GRID_SIZE_DEGREES) * GRID_SIZE_DEGREES
    start_y = math.floor(miny / GRID_SIZE_DEGREES) * GRID_SIZE_DEGREES
    x_values = np.arange(start_x, maxx, GRID_SIZE_DEGREES)
    y_values = np.arange(start_y, maxy, GRID_SIZE_DEGREES)

    records: list[dict[str, object]] = []
    for y in y_values:
        for x in x_values:
            clipped = box(x, y, x + GRID_SIZE_DEGREES, y + GRID_SIZE_DEGREES).intersection(
                kalimantan_union
            )
            if not clipped.is_empty and clipped.area > 0:
                records.append({"cell_lon": x, "cell_lat": y, "geometry": clipped})

    grid = gpd.GeoDataFrame(records, crs=WGS84)
    grid = grid.sort_values(["cell_lat", "cell_lon"]).reset_index(drop=True)
    grid["grid_id"] = [f"G{index:05d}" for index in range(1, len(grid) + 1)]

    equal_area = grid.to_crs(EQUAL_AREA_CRS)
    grid["area_km2"] = equal_area.geometry.area.to_numpy() / 1_000_000
    representative = equal_area.geometry.representative_point().to_crs(WGS84)
    grid["centroid_lon"] = representative.x.to_numpy()
    grid["centroid_lat"] = representative.y.to_numpy()
    return grid


def _assign_admin_labels(
    grid: gpd.GeoDataFrame, adm1: gpd.GeoDataFrame, adm2: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Attach province and district using a representative point for every grid."""
    points = gpd.GeoDataFrame(
        grid[["grid_id"]].copy(),
        geometry=gpd.points_from_xy(grid["centroid_lon"], grid["centroid_lat"]),
        crs=WGS84,
    )
    province = gpd.sjoin(
        points,
        adm1[["shapeName", "geometry"]].rename(columns={"shapeName": "province"}),
        how="left",
        predicate="within",
    ).drop(columns="index_right")
    district = gpd.sjoin(
        points,
        adm2[["shapeName", "geometry"]].rename(columns={"shapeName": "district"}),
        how="left",
        predicate="within",
    ).drop(columns="index_right")

    labels = province[["grid_id", "province"]].merge(
        district[["grid_id", "district"]], on="grid_id", how="left"
    )
    output = grid.merge(labels, on="grid_id", how="left")
    output["province"] = output["province"].fillna("Unassigned")
    output["district"] = output["district"].fillna("Unassigned")
    return gpd.GeoDataFrame(output, geometry="geometry", crs=WGS84)


def calculate_peat_share(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Calculate peat coverage from an indexed polygon overlay in an equal-area CRS."""

    peat_path = RAW_DIR / "peatland" / "kalimantan_peatlands.geojson"
    peat = gpd.read_file(peat_path).to_crs(EQUAL_AREA_CRS)
    grid_equal_area = grid[["grid_id", "geometry"]].to_crs(EQUAL_AREA_CRS)
    intersections = gpd.overlay(grid_equal_area, peat[["geometry"]], how="intersection")

    if intersections.empty:
        grid["peat_area_km2"] = 0.0
    else:
        # Dissolve all pieces within a grid before measuring to avoid double-counting
        # should the source peat polygons overlap.
        peat_area = (
            intersections.dissolve(by="grid_id").geometry.area / 1_000_000
        ).rename("peat_area_km2")
        grid["peat_area_km2"] = grid["grid_id"].map(peat_area).fillna(0.0)

    grid["peat_pct"] = np.where(
        grid["area_km2"] > 0,
        np.clip((grid["peat_area_km2"] / grid["area_km2"]) * 100, 0, 100),
        np.nan,
    )
    return grid


def add_landcover_code(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sample the 2022 MapBiomas land-cover code at each grid representative point.

    The code is a static categorical predictor, never interpreted as a current
    2025–2026 measurement. Sampling is explicit and reproducible; the raw raster
    remains immutable.
    """
    raster_path = RAW_DIR / "mapbiomas" / "landcover_2022_collection2.tif"
    if not raster_path.exists():
        grid["landcover_code_2022"] = np.nan
        return grid

    coordinates = list(zip(grid["centroid_lon"], grid["centroid_lat"]))
    with rasterio.open(raster_path) as source:
        values = np.asarray([sample[0] for sample in source.sample(coordinates)], dtype=float)
        if source.nodata is not None:
            values[values == source.nodata] = np.nan
    values[values == 0] = np.nan
    grid["landcover_code_2022"] = values
    return grid


def _firms_sources() -> list[Path]:
    sources = sorted((RAW_DIR / "firms").glob("firms_viirs_noaa20_*.parquet"))
    if not sources:
        raise FileNotFoundError("No final NASA FIRMS parquet files exist in data/raw/firms.")
    return sources


def _spatial_join_once(
    points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, columns: list[str], predicate: str
) -> gpd.GeoDataFrame:
    joined = gpd.sjoin(points, polygons[columns + ["geometry"]], how="left", predicate=predicate)
    return joined.drop(columns="index_right").drop_duplicates(subset="detection_id", keep="first")


def clean_and_enrich_firms(
    grid: gpd.GeoDataFrame,
    adm1: gpd.GeoDataFrame,
    adm2: gpd.GeoDataFrame,
    kalimantan_union: object,
    end_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Filter FIRMS to Kalimantan and spatially label every retained detection."""
    frames = [pd.read_parquet(path) for path in _firms_sources()]
    fires = pd.concat(frames, ignore_index=True)
    missing = sorted(set(FIRMS_REQUIRED_COLUMNS) - set(fires.columns))
    if missing:
        raise ValueError(f"FIRMS input misses required columns: {missing}")

    fires["acq_date"] = pd.to_datetime(fires["acq_date"], errors="coerce").dt.normalize()
    if end_date is not None:
        fires = fires.loc[fires["acq_date"].le(pd.Timestamp(end_date).normalize())].copy()
    fires["acq_time"] = pd.to_numeric(fires["acq_time"], errors="coerce")
    fires["frp"] = pd.to_numeric(fires["frp"], errors="coerce")
    fires["latitude"] = pd.to_numeric(fires["latitude"], errors="coerce")
    fires["longitude"] = pd.to_numeric(fires["longitude"], errors="coerce")
    fires["scan"] = pd.to_numeric(fires.get("scan"), errors="coerce")
    fires["track"] = pd.to_numeric(fires.get("track"), errors="coerce")
    # VIIRS scan/track describe the sensor footprint in kilometres, not a
    # mapped fire perimeter.  Keep the conversion explicit for display-only
    # clustering without claiming it as burned area.
    fires["footprint_ha_proxy"] = fires["scan"] * fires["track"] * 100
    fires = fires.dropna(subset=list(FIRMS_REQUIRED_COLUMNS)).copy()
    fires = fires.loc[
        fires["latitude"].between(-5, 8) & fires["longitude"].between(108, 119.5)
    ].copy()

    dedupe_columns = [
        column
        for column in (
            "latitude",
            "longitude",
            "acq_date",
            "acq_time",
            "satellite",
            "instrument",
            "confidence",
            "frp",
        )
        if column in fires.columns
    ]
    fires = fires.drop_duplicates(subset=dedupe_columns).reset_index(drop=True)
    fires["detection_id"] = np.arange(len(fires), dtype=np.int64)
    fires["acq_time"] = fires["acq_time"].astype(int).astype(str).str.zfill(4)
    fires["acq_datetime"] = pd.to_datetime(
        fires["acq_date"].dt.strftime("%Y-%m-%d") + " " + fires["acq_time"],
        format="%Y-%m-%d %H%M",
        errors="coerce",
    )

    points = gpd.GeoDataFrame(
        fires,
        geometry=gpd.points_from_xy(fires["longitude"], fires["latitude"]),
        crs=WGS84,
    )
    points = points.loc[points.geometry.within(kalimantan_union)].copy()
    if points.empty:
        raise ValueError("Kalimantan polygon filter removed every FIRMS detection.")

    points = _spatial_join_once(
        points,
        grid,
        ["grid_id"],
        predicate="within",
    )
    unmatched = points["grid_id"].isna()
    if unmatched.any():
        fallback = _spatial_join_once(
            points.loc[unmatched].drop(columns="grid_id"),
            grid,
            ["grid_id"],
            predicate="intersects",
        )
        points.loc[unmatched, "grid_id"] = fallback.set_index("detection_id").reindex(
            points.loc[unmatched, "detection_id"]
        )["grid_id"].to_numpy()
    points = points.dropna(subset=["grid_id"]).copy()

    points = _spatial_join_once(
        points,
        adm1,
        ["shapeName"],
        predicate="within",
    ).rename(columns={"shapeName": "province"})
    points = _spatial_join_once(
        points,
        adm2,
        ["shapeName"],
        predicate="within",
    ).rename(columns={"shapeName": "district"})

    if points["grid_id"].isna().any():
        raise ValueError("Some retained FIRMS points are missing a grid assignment.")
    points["province"] = points["province"].fillna("Unassigned")
    points["district"] = points["district"].fillna("Unassigned")
    points["week_start"] = points["acq_date"] - pd.to_timedelta(
        points["acq_date"].dt.weekday, unit="D"
    )

    output_columns = [
        "detection_id",
        "latitude",
        "longitude",
        "acq_date",
        "acq_datetime",
        "acq_time",
        "confidence",
        "frp",
        "scan",
        "track",
        "footprint_ha_proxy",
        "satellite",
        "instrument",
        "daynight",
        "source_product",
        "grid_id",
        "province",
        "district",
        "week_start",
    ]
    return pd.DataFrame(points[[column for column in output_columns if column in points.columns]])


def _open_era5_month(path: Path) -> xr.Dataset:
    """Load direct NetCDF or CDS ZIP-bundle responses into one in-memory dataset."""
    if zipfile.is_zipfile(path):
        parts: list[xr.Dataset] = []
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".nc")]
            if not members:
                raise ValueError(f"ERA5 archive has no NetCDF members: {path.name}")
            for member in members:
                payload = io.BytesIO(archive.read(member))
                with xr.open_dataset(payload, engine="h5netcdf") as dataset:
                    parts.append(dataset.load())
        return xr.merge(parts, compat="override", join="exact")

    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        return dataset.load()


def _daily_weather_part(path: Path, grid: gpd.GeoDataFrame) -> pd.DataFrame:
    """Sample hourly ERA5 at grid representative points and aggregate daily."""
    dataset = _open_era5_month(path)
    expected = {"tp", "t2m", "swvl1"}
    if not expected.issubset(dataset.data_vars):
        raise ValueError(f"ERA5 month {path.name} misses {sorted(expected - set(dataset.data_vars))}")

    grid_ids = grid["grid_id"].to_numpy()
    lat_indexer = xr.DataArray(
        grid["centroid_lat"].to_numpy(),
        dims="grid",
        coords={"grid": grid_ids},
    )
    lon_indexer = xr.DataArray(
        grid["centroid_lon"].to_numpy(),
        dims="grid",
        coords={"grid": grid_ids},
    )
    selected = dataset[["tp", "t2m", "swvl1"]].sel(
        latitude=lat_indexer,
        longitude=lon_indexer,
        method="nearest",
    )
    time_name = "valid_time"
    daily = xr.Dataset(
        {
            "rainfall_mm": (selected["tp"] * 1000).resample({time_name: "1D"}).sum(
                skipna=True
            ),
            "temperature_mean_c": (selected["t2m"] - 273.15).resample(
                {time_name: "1D"}
            ).mean(skipna=True),
            "temperature_max_c": (selected["t2m"] - 273.15).resample(
                {time_name: "1D"}
            ).max(skipna=True),
            "soil_moisture": selected["swvl1"].resample({time_name: "1D"}).mean(
                skipna=True
            ),
        }
    ).rename({time_name: "date"})
    frame = daily.to_dataframe().reset_index().rename(columns={"grid": "grid_id"})
    frame = frame[["date", "grid_id", "rainfall_mm", "temperature_mean_c", "temperature_max_c", "soil_moisture"]]
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return frame


def build_daily_weather(grid: gpd.GeoDataFrame, force: bool = False) -> pd.DataFrame:
    """Build weekly ERA5 feature snapshots from resumable daily summaries.

    Rolling statistics still use every daily observation, but only Monday snapshots
    are retained because the downstream fact table is one record per grid-week.
    This avoids materialising an oversized daily feature table in memory.
    """
    parts_dir = INTERIM_DIR / "era5_daily_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    source_files = sorted((RAW_DIR / "era5_land").glob("era5_land_*.nc"))
    if not source_files:
        raise FileNotFoundError("No ERA5-Land monthly files were found.")

    for source in source_files:
        suffix = source.stem.replace("era5_land_", "")
        destination = parts_dir / f"era5_daily_{suffix}.parquet"
        if destination.exists() and not force:
            continue
        LOGGER.info("Summarising %s", source.name)
        _daily_weather_part(source, grid).to_parquet(destination, index=False)

    parts = [pd.read_parquet(path) for path in sorted(parts_dir.glob("era5_daily_*.parquet"))]
    # Files cover non-overlapping, consecutive calendar months in chronological
    # order, so a global sort/deduplication would only create large transient copies.
    daily = pd.concat(parts, ignore_index=True)
    del parts
    daily["grid_id"] = daily["grid_id"].astype("category")
    monday_rows = daily["date"].dt.dayofweek.eq(0)
    weekly = daily.loc[monday_rows, ["date", "grid_id"]].copy()
    weekly["grid_id"] = weekly["grid_id"].astype(str)
    for feature, column, window, aggregation in (
        ("rainfall_7d", "rainfall_mm", 7, "sum"),
        ("rainfall_14d", "rainfall_mm", 14, "sum"),
        ("rainfall_30d", "rainfall_mm", 30, "sum"),
        ("rainfall_60d", "rainfall_mm", 60, "sum"),
        ("temperature_mean_7d", "temperature_mean_c", 7, "mean"),
        ("temperature_mean_30d", "temperature_mean_c", 30, "mean"),
        ("temperature_max_7d", "temperature_max_c", 7, "max"),
        ("soil_moisture_7d", "soil_moisture", 7, "mean"),
        ("soil_moisture_30d", "soil_moisture", 30, "mean"),
    ):
        weekly[feature] = _rolling_by_grid(
            daily, column, window, aggregation
        ).loc[monday_rows].to_numpy()
    daily["_dry_day"] = daily["rainfall_mm"].lt(1.0).where(
        daily["rainfall_mm"].notna()
    )
    weekly["dry_days_30d"] = _rolling_by_grid(daily, "_dry_day", 30, "sum").loc[
        monday_rows
    ].to_numpy()
    weekly["dry_days_60d"] = _rolling_by_grid(daily, "_dry_day", 60, "sum").loc[
        monday_rows
    ].to_numpy()
    weekly.to_parquet(INTERIM_DIR / "era5_weekly_grid.parquet", index=False)
    return weekly


def _latest_complete_week(last_date: pd.Timestamp) -> pd.Timestamp:
    current_monday = last_date.normalize() - pd.Timedelta(days=last_date.weekday())
    return current_monday if current_monday + pd.Timedelta(days=6) <= last_date else current_monday - pd.Timedelta(days=7)


def _weekly_fire_aggregation(fires: pd.DataFrame) -> pd.DataFrame:
    grouped = fires.groupby(["grid_id", "week_start"], as_index=False)
    weekly = grouped.agg(
        hotspot_count=("detection_id", "size"),
        high_confidence_count=("confidence", lambda values: values.astype(str).str.lower().eq("h").sum()),
        frp_sum=("frp", "sum"),
        avg_frp=("frp", "mean"),
    )
    return weekly


def _add_fire_lags(master: pd.DataFrame) -> pd.DataFrame:
    """Add history-only fire features without materialising a DataFrame per grid."""
    master = master.sort_values(["grid_id", "week_start"]).copy()
    master["hotspot_prev_7d"] = master.groupby("grid_id", sort=False)[
        "hotspot_count"
    ].shift(1)
    master["hotspot_prev_14d"] = _rolling_by_grid(
        master, "hotspot_count", 2, "sum"
    )
    previous_count = _rolling_by_grid(master, "hotspot_count", 4, "sum")
    master["hotspot_prev_30d"] = previous_count
    previous_frp = _rolling_by_grid(master, "frp_sum", 4, "sum")
    master["avg_frp_prev_30d"] = previous_frp / previous_count.replace(0, np.nan)

    active_weeks = master["week_start"].where(master["hotspot_count"].gt(0))
    last_active = active_weeks.groupby(master["grid_id"], sort=False).ffill()
    previous_active = last_active.groupby(master["grid_id"], sort=False).shift(1)
    master["days_since_last_hotspot"] = (
        master["week_start"] - previous_active
    ).dt.days
    return master


def build_master_table(
    grid: gpd.GeoDataFrame, fires: pd.DataFrame, daily_weather: pd.DataFrame
) -> pd.DataFrame:
    """Build the one-record-per-grid-per-complete-week master dataset."""
    last_complete = _latest_complete_week(fires["acq_date"].max())
    first_week = pd.Timestamp(MIN_DATE)
    weeks = pd.date_range(first_week, last_complete, freq="W-MON")
    weekly = _weekly_fire_aggregation(fires)
    weekly = weekly.loc[weekly["week_start"].isin(weeks)].copy()

    panel = pd.MultiIndex.from_product(
        [grid["grid_id"].to_numpy(), weeks], names=["grid_id", "week_start"]
    ).to_frame(index=False)
    master = panel.merge(weekly, on=["grid_id", "week_start"], how="left")
    for column in ("hotspot_count", "high_confidence_count", "frp_sum"):
        master[column] = master[column].fillna(0)
    master["avg_frp"] = master["avg_frp"].where(master["hotspot_count"].gt(0))

    static_columns = [
        "grid_id",
        "centroid_lat",
        "centroid_lon",
        "province",
        "district",
        "area_km2",
        "peat_area_km2",
        "peat_pct",
        "landcover_code_2022",
    ]
    master = master.merge(grid[static_columns], on="grid_id", how="left")
    master["hotspot_density_per_1000km2"] = (
        master["hotspot_count"] / master["area_km2"] * 1000
    )
    master["high_confidence_pct"] = np.where(
        master["hotspot_count"].gt(0),
        master["high_confidence_count"] / master["hotspot_count"] * 100,
        0.0,
    )

    weather_columns = [
        "date",
        "grid_id",
        "rainfall_7d",
        "rainfall_14d",
        "rainfall_30d",
        "rainfall_60d",
        "temperature_mean_7d",
        "temperature_mean_30d",
        "temperature_max_7d",
        "soil_moisture_7d",
        "soil_moisture_30d",
        "dry_days_30d",
        "dry_days_60d",
    ]
    weather = daily_weather[weather_columns].rename(columns={"date": "week_start"})
    master = master.merge(weather, on=["grid_id", "week_start"], how="left")
    master = _add_fire_lags(master)

    master["year"] = master["week_start"].dt.year
    master["month"] = master["week_start"].dt.month
    master["week_of_year"] = master["week_start"].dt.isocalendar().week.astype(int)
    master["month_sin"] = np.sin(2 * np.pi * master["month"] / 12)
    master["month_cos"] = np.cos(2 * np.pi * master["month"] / 12)
    master["fire_next_7d"] = master["hotspot_count"].gt(0).astype(int)
    master["hotspot_next_7d"] = master["hotspot_count"].astype(int)
    master = add_lagged_mapbiomas_province_features(master)
    return master.sort_values(["week_start", "grid_id"]).reset_index(drop=True)


def add_lagged_mapbiomas_province_features(master: pd.DataFrame) -> pd.DataFrame:
    """Merge MapBiomas province features using only information from earlier years.

    Annual land-cover and burned-area products are retrospective.  A target in
    2024 can only consume 2023 products; targets in 2025--2026 use the latest
    2024 product.  This prevents same-year annual maps from leaking future fire
    outcomes into the weekly prediction task.
    """
    source = INTERIM_DIR / "mapbiomas_province_year_features.parquet"
    feature_columns = [
        "lc_natural_share_pct",
        "lc_anthropic_share_pct",
        "lc_peat_swamp_share_pct",
        "lc_oil_palm_share_pct",
        "lc_pulpwood_share_pct",
        "lc_agriculture_share_pct",
        "lc_water_share_pct",
        "burned_share_pct",
    ]
    target_columns = [f"mapbiomas_{column}_prev_year" for column in feature_columns]
    if not source.exists():
        for column in target_columns:
            master[column] = np.nan
        master["mapbiomas_prev_year_available"] = 0
        return master

    features = pd.read_parquet(source)
    required = {"province", "source_year", *feature_columns}
    missing = required - set(features.columns)
    if missing:
        raise ValueError(f"Temporal MapBiomas feature table misses {sorted(missing)}")
    renamed = features.rename(columns={
        column: f"mapbiomas_{column}_prev_year" for column in feature_columns
    })
    result = master.copy()
    result["mapbiomas_source_year"] = np.where(
        result["year"].ge(2024), np.minimum(result["year"] - 1, 2024), -1
    ).astype(int)
    result = result.merge(
        renamed[["province", "source_year", *target_columns]],
        left_on=["province", "mapbiomas_source_year"],
        right_on=["province", "source_year"],
        how="left",
        validate="many_to_one",
    ).drop(columns=["source_year", "mapbiomas_source_year"])
    result["mapbiomas_prev_year_available"] = result[target_columns].notna().all(axis=1).astype(int)
    return result


def add_advanced_features(master: pd.DataFrame, grid: gpd.GeoDataFrame) -> pd.DataFrame:
    """Add causal spatial, seasonal, administrative, and drought interaction features."""
    master = master.sort_values(["grid_id", "week_start"]).copy()

    # Same-week regional totals are shifted before merging, so no target-week
    # detection can enter a feature.
    for level in ("district", "province"):
        grouped = (
            master.groupby([level, "week_start"], as_index=False)["hotspot_count"]
            .sum()
            .sort_values([level, "week_start"])
        )
        grouped[f"{level}_hotspot_prev_7d"] = grouped.groupby(level)["hotspot_count"].shift(1)
        grouped[f"{level}_hotspot_prev_30d"] = grouped.groupby(level)["hotspot_count"].transform(
            lambda values: values.rolling(4, min_periods=4).sum().shift(1)
        )
        grouped = grouped.drop(columns="hotspot_count")
        master = master.merge(grouped, on=[level, "week_start"], how="left")

    # Neighbour counts use the eight closest grid representatives and are also
    # shifted one complete week.
    grid_order = grid.sort_values("grid_id").reset_index(drop=True)
    grid_ids = grid_order["grid_id"].to_numpy()
    coordinates = np.radians(grid_order[["centroid_lat", "centroid_lon"]].to_numpy())
    neighbour_model = NearestNeighbors(
        n_neighbors=min(9, len(grid_order)), metric="haversine"
    ).fit(coordinates)
    neighbour_indices = neighbour_model.kneighbors(coordinates, return_distance=False)[:, 1:]
    weeks = pd.Index(sorted(master["week_start"].unique()), name="week_start")
    counts = (
        master.pivot(index="week_start", columns="grid_id", values="hotspot_count")
        .reindex(index=weeks, columns=grid_ids, fill_value=0)
        .to_numpy()
    )
    neighbour_current = counts[:, neighbour_indices].sum(axis=2)
    neighbour_frame = pd.DataFrame(neighbour_current, index=weeks, columns=grid_ids)
    spatial_parts: list[pd.DataFrame] = []
    for name, values in (
        ("neighbour_hotspot_prev_7d", neighbour_frame.shift(1)),
        (
            "neighbour_hotspot_prev_30d",
            neighbour_frame.rolling(4, min_periods=4).sum().shift(1),
        ),
    ):
        # pandas 3 retains existing missing entries with the default stack mode.
        part = values.stack().rename(name).reset_index()
        part.columns = ["week_start", "grid_id", name]
        spatial_parts.append(part)
    master = master.merge(spatial_parts[0], on=["grid_id", "week_start"], how="left")
    master = master.merge(spatial_parts[1], on=["grid_id", "week_start"], how="left")

    # Seasonal grid baselines only use the same week-of-year from earlier years.
    seasonal_groups = master.groupby(["grid_id", "week_of_year"], sort=False)
    earlier_observations = seasonal_groups.cumcount().replace(0, np.nan)
    master["seasonal_grid_hotspot_mean_prior"] = (
        seasonal_groups["hotspot_count"].cumsum() - master["hotspot_count"]
    ) / earlier_observations

    rainfall_baseline = master.groupby("grid_id", sort=False)["rainfall_30d"].transform(
        lambda values: values.shift(1).expanding().mean()
    )
    master["rainfall_30d_anomaly"] = master["rainfall_30d"] - rainfall_baseline
    master["rainfall_7_to_30_ratio"] = master["rainfall_7d"] / (
        master["rainfall_30d"] + 1.0
    )
    master["peat_dryness_index"] = (master["peat_pct"] / 100.0) * (
        1.0 - master["soil_moisture_30d"]
    )
    master["hotspot_density_prev_30d"] = (
        master["hotspot_prev_30d"] / master["area_km2"] * 1000
    )
    master["week_sin"] = np.sin(2 * np.pi * master["week_of_year"] / 52)
    master["week_cos"] = np.cos(2 * np.pi * master["week_of_year"] / 52)
    return master.sort_values(["week_start", "grid_id"]).reset_index(drop=True)


def _date_dimension(first_date: pd.Timestamp, last_date: pd.Timestamp) -> pd.DataFrame:
    dates = pd.DataFrame({"date": pd.date_range(first_date, last_date, freq="D")})
    dates["week_start"] = dates["date"] - pd.to_timedelta(dates["date"].dt.weekday, unit="D")
    dates["year"] = dates["date"].dt.year
    dates["month"] = dates["date"].dt.month
    dates["week_of_year"] = dates["date"].dt.isocalendar().week.astype(int)
    dates["dry_season_indicator"] = dates["month"].isin([6, 7, 8, 9]).astype(int)
    return dates


def build_all(force: bool = False) -> dict[str, object]:
    """Run the raw-to-processed data pipeline and return a concise audit summary."""
    _ensure_directories()
    adm1, adm2, kalimantan_union = load_kalimantan_boundaries()
    grid = add_landcover_code(
        calculate_peat_share(_assign_admin_labels(build_grid(adm1, kalimantan_union), adm1, adm2))
    )
    fires = clean_and_enrich_firms(grid, adm1, adm2, kalimantan_union)
    weather = build_daily_weather(grid, force=force)
    master = add_advanced_features(build_master_table(grid, fires, weather), grid)

    if master.duplicated(["grid_id", "week_start"]).any():
        raise ValueError("Master table primary key is not unique.")
    if fires.empty or master.empty:
        raise ValueError("Pipeline produced an empty required output.")

    grid_output = grid.drop(columns=["cell_lon", "cell_lat"], errors="ignore")
    pd.DataFrame(fires).to_parquet(PROCESSED_DIR / "firms_kalimantan_clean.parquet", index=False)
    grid_output.to_parquet(PROCESSED_DIR / "dim_grid.parquet", index=False)
    grid_output.to_file(PROCESSED_DIR / "dim_grid.geojson", driver="GeoJSON", index=False)
    master.to_parquet(PROCESSED_DIR / "fact_grid_week.parquet", index=False)
    dates = _date_dimension(master["week_start"].min(), master["week_start"].max() + pd.Timedelta(days=6))
    dates.to_parquet(PROCESSED_DIR / "dim_date.parquet", index=False)

    weather_features = [
        "rainfall_30d",
        "temperature_mean_7d",
        "temperature_max_7d",
        "soil_moisture_7d",
    ]
    report: dict[str, object] = {
        "status": "passed",
        "firms_kalimantan_rows": int(len(fires)),
        "firms_date_range": [str(fires["acq_date"].min().date()), str(fires["acq_date"].max().date())],
        "grid_count": int(len(grid)),
        "weekly_rows": int(len(master)),
        "weekly_date_range": [
            str(master["week_start"].min().date()),
            str(master["week_start"].max().date()),
        ],
        "primary_key_duplicates": int(master.duplicated(["grid_id", "week_start"]).sum()),
        "peat_grid_count": int(grid["peat_pct"].gt(0).sum()),
        "landcover_sample_nonnull_pct": round(
            float(grid["landcover_code_2022"].notna().mean() * 100), 2
        ),
        "weather_complete_row_pct": round(
            float(master[weather_features].notna().all(axis=1).mean() * 100), 2
        ),
    }
    (REPORTS_DIR / "data_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def refresh_engineered_features(force: bool = False) -> dict[str, object]:
    """Refresh engineered features from validated static outputs.

    This avoids repeating expensive polygon overlay operations when only feature
    engineering changes. It is equivalent to the latter stages of build_all.
    """
    _ensure_directories()
    grid_path = PROCESSED_DIR / "dim_grid.parquet"
    fires_path = PROCESSED_DIR / "firms_kalimantan_clean.parquet"
    if not grid_path.exists() or not fires_path.exists():
        raise FileNotFoundError("Run the full build before refreshing engineered features.")

    grid = gpd.read_parquet(grid_path).to_crs(WGS84)
    fires = pd.read_parquet(fires_path)
    fires["acq_date"] = pd.to_datetime(fires["acq_date"]).dt.normalize()
    fires["week_start"] = pd.to_datetime(fires["week_start"]).dt.normalize()
    grid = add_landcover_code(grid)
    weather = build_daily_weather(grid, force=force)
    master = add_advanced_features(build_master_table(grid, fires, weather), grid)

    if master.duplicated(["grid_id", "week_start"]).any():
        raise ValueError("Master table primary key is not unique after feature refresh.")

    grid.to_parquet(PROCESSED_DIR / "dim_grid.parquet", index=False)
    grid.to_file(PROCESSED_DIR / "dim_grid.geojson", driver="GeoJSON", index=False)
    master.to_parquet(PROCESSED_DIR / "fact_grid_week.parquet", index=False)
    _date_dimension(
        master["week_start"].min(), master["week_start"].max() + pd.Timedelta(days=6)
    ).to_parquet(PROCESSED_DIR / "dim_date.parquet", index=False)

    stale_prediction = PROCESSED_DIR / "prediction_grid_week.parquet"
    if stale_prediction.exists():
        stale_prediction.unlink()

    report = {
        "status": "passed",
        "mode": "feature_refresh",
        "grid_count": int(len(grid)),
        "weekly_rows": int(len(master)),
        "landcover_sample_nonnull_pct": round(
            float(grid["landcover_code_2022"].notna().mean() * 100), 2
        ),
        "advanced_feature_count": 16,
    }
    (REPORTS_DIR / "data_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
