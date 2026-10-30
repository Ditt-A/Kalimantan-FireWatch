"""Operational weekly forecasting with explicit weather latency and no target labels."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd

from .config import PROJECT_ROOT
from .data_pipeline import _rolling_by_grid, add_advanced_features, build_master_table
from .modeling import (
    NUMERIC_FEATURE_COLUMNS, _prepare_model_data, _classification_metrics,
    _count_metrics, _classification_candidates, _count_candidates,
    _best_validation_threshold, _quantiles, _risk_category, _count_category, _priority_category,
)
from .releases import atomic_json, sha256

WEATHER_COLUMNS = [
    "rainfall_7d", "rainfall_14d", "rainfall_30d", "rainfall_60d",
    "temperature_mean_7d", "temperature_mean_30d", "temperature_max_7d",
    "soil_moisture_7d", "soil_moisture_30d", "dry_days_30d", "dry_days_60d",
]
FEATURES = NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never", "landcover_code_2022"]

def apply_weather_latency(fact: pd.DataFrame, lag_days: int = 7) -> pd.DataFrame:
    if lag_days < 7 or lag_days % 7:
        raise ValueError("Weekly ERA5 operational contracts require a whole-week lag >= 7 days")
    data = fact.drop(columns=WEATHER_COLUMNS + ["rainfall_30d_anomaly", "rainfall_7_to_30_ratio", "peat_dryness_index"], errors="ignore").copy()
    weather = fact[["grid_id", "week_start", *WEATHER_COLUMNS]].copy()
    weather["week_start"] = pd.to_datetime(weather["week_start"]) + pd.Timedelta(days=lag_days)
    data = data.merge(weather, on=["grid_id", "week_start"], how="left", validate="one_to_one")
    data = data.sort_values(["grid_id", "week_start"])
    baseline = data.groupby("grid_id", sort=False)["rainfall_30d"].transform(lambda x: x.shift(1).expanding().mean())
    data["rainfall_30d_anomaly"] = data["rainfall_30d"] - baseline
    data["rainfall_7_to_30_ratio"] = data["rainfall_7d"] / (data["rainfall_30d"] + 1)
    data["peat_dryness_index"] = (data["peat_pct"] / 100) * (1 - data["soil_moisture_30d"])
    return data

def train_latency_candidate(fact_path: Path, output: Path, *, lag_days: int = 7, max_iter: int = 150, corrected_daily: Path | None = None) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty candidate directory")
    fact = pd.read_parquet(fact_path)
    fact["week_start"] = pd.to_datetime(fact["week_start"])
    total_grid_count = fact["grid_id"].nunique()
    eligible_weather_ids = sorted(fact["grid_id"].unique().tolist())
    if corrected_daily is not None:
        daily = pd.read_parquet(corrected_daily)
        eligible_weather_ids = sorted(set(eligible_weather_ids) & set(daily["grid_id"].unique()))
        historical_weather = weekly_weather(daily, eligible_weather_ids, fact["week_start"].max(), 0)
        fact = fact.drop(columns=WEATHER_COLUMNS).merge(historical_weather.rename(columns={"date": "week_start"}), on=["grid_id", "week_start"], how="left", validate="one_to_one")
    data = _prepare_model_data(apply_weather_latency(fact, lag_days))
    train = data.loc[data["year"].isin([2023, 2024])]
    validation = data.loc[data["year"].eq(2025)]
    test = data.loc[data["year"].eq(2026)]
    if any(part.empty or part["fire_next_7d"].nunique() < 2 for part in (train, validation, test)):
        raise ValueError("Insufficient temporal train/validation/test coverage")
    classifier = _classification_candidates()["hist_gradient_boosting"]
    counter = _count_candidates()["hist_gradient_boosting"]
    classifier.set_params(model__max_iter=max_iter)
    counter.set_params(model__max_iter=max_iter)
    classifier.fit(train[FEATURES], train["fire_next_7d"])
    counter.fit(train[FEATURES], train["hotspot_next_7d"])
    vp = classifier.predict_proba(validation[FEATURES])[:, 1]
    vc = np.clip(counter.predict(validation[FEATURES]), 0, None)
    threshold = _best_validation_threshold(validation["fire_next_7d"], vp)
    risk_thresholds, count_thresholds = _quantiles(pd.Series(vp)), _quantiles(pd.Series(vc))
    # Final fits use pre-test data only. Parameters and thresholds are not selected on test.
    fit = pd.concat([train, validation])
    classifier.fit(fit[FEATURES], fit["fire_next_7d"])
    counter.fit(fit[FEATURES], fit["hotspot_next_7d"])
    probability = classifier.predict_proba(test[FEATURES])[:, 1]
    count = np.clip(counter.predict(test[FEATURES]), 0, None)
    contract = {
        "schema_version": "weekly-era5-lag-v1", "weather_lag_days": lag_days,
        "minimum_weather_source_latency_days": 5,
        "weekly_weather_cutoff_offset_days": lag_days + 1,
        "availability_policy": "conservative_event_time_lag; historical first-availability and preliminary vintages are not reconstructed",
        "features": FEATURES, "grid_degrees": 0.1,
        "precipitation_method": "era5_land_midnight_previous_day_v1" if corrected_daily else "legacy_hourly_sum_requires_rebuild",
        "minimum_grid_coverage": 0.99,
        "total_grid_count": int(total_grid_count),
        "weather_eligible_grid_count": len(eligible_weather_ids),
        "weather_eligible_grid_sha256": hashlib.sha256(json.dumps(eligible_weather_ids).encode()).hexdigest(),
    }
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": classifier, "features": FEATURES, "threshold": threshold, "validation_thresholds": risk_thresholds, "operational_contract": contract}, output / "fire_occurrence_classifier.joblib")
    joblib.dump({"model": counter, "features": FEATURES, "validation_thresholds": count_thresholds, "operational_contract": contract}, output / "hotspot_count_model.joblib")
    report = {
        "status": "temporal_candidate", "contract": contract,
        "rows": {"train": len(train), "validation": len(validation), "test": len(test)},
        "classification_test": _classification_metrics(test["fire_next_7d"], probability, threshold),
        "count_test": _count_metrics(test["hotspot_next_7d"], count),
        "source_sha256": sha256(fact_path),
        "corrected_daily_sha256": sha256(corrected_daily) if corrected_daily else None,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "limitations": ["Historical raw source release vintages were not archived. Prospective shadow evaluation remains required.", "The 2026 test period is retrospective; these metrics do not validate today's weather or detections."],
    }
    atomic_json(output / "evaluation.json", report)
    atomic_json(output / "contract.json", contract)
    return report

def weekly_weather(daily: pd.DataFrame, grid_ids: list[str], target: pd.Timestamp, lag_days: int) -> pd.DataFrame:
    required = ["rainfall_mm", "temperature_mean_c", "temperature_max_c", "soil_moisture"]
    source_monday = target - pd.Timedelta(days=lag_days)
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    if daily.duplicated(["grid_id", "date"]).any():
        raise ValueError("Weather has duplicate grid/day revisions; reconcile explicitly before scoring")
    dates = pd.date_range(source_monday - pd.Timedelta(days=60), source_monday - pd.Timedelta(days=1))
    window = daily.loc[daily["date"].isin(dates)]
    completeness = window.groupby("grid_id")[required].count().reindex(grid_ids, fill_value=0)
    complete_grid_ids = completeness.index[completeness.eq(60).all(axis=1)].tolist()
    if len(complete_grid_ids) / max(1, len(grid_ids)) < 0.99:
        raise ValueError(f"Weather requires 60 complete daily observations per grid through {dates.max().date()}; available through {daily['date'].max().date()}")
    # Add the next Monday as an empty observation: every rolling feature shifts
    # one day, so this row uses only the preceding complete historical days.
    additional = pd.DataFrame({"grid_id": grid_ids, "date": source_monday})
    daily = pd.concat([daily.loc[daily["date"] < source_monday], additional], ignore_index=True).sort_values(["grid_id", "date"]).reset_index(drop=True)
    monday = daily["date"].dt.dayofweek.eq(0)
    result = daily.loc[monday, ["grid_id", "date"]].copy()
    for name, column, days, aggregation in (
        ("rainfall_7d", "rainfall_mm", 7, "sum"), ("rainfall_14d", "rainfall_mm", 14, "sum"),
        ("rainfall_30d", "rainfall_mm", 30, "sum"), ("rainfall_60d", "rainfall_mm", 60, "sum"),
        ("temperature_mean_7d", "temperature_mean_c", 7, "mean"), ("temperature_mean_30d", "temperature_mean_c", 30, "mean"),
        ("temperature_max_7d", "temperature_max_c", 7, "max"),
        ("soil_moisture_7d", "soil_moisture", 7, "mean"), ("soil_moisture_30d", "soil_moisture", 30, "mean"),
    ):
        result[name] = _rolling_by_grid(daily, column, days, aggregation).loc[monday].to_numpy()
    daily["_dry"] = daily["rainfall_mm"].lt(1).where(daily["rainfall_mm"].notna())
    for days in (30, 60):
        result[f"dry_days_{days}d"] = _rolling_by_grid(daily, "_dry", days, "sum").loc[monday].to_numpy()
    # The data_pipeline weather merge expects target-week keys.
    result["date"] += pd.Timedelta(days=lag_days)
    return result

def build_current_features(root: Path, target: pd.Timestamp, contract: dict, *, firms_path: Path | None = None) -> pd.DataFrame:
    target = pd.Timestamp(target).normalize()
    if target.dayofweek != 0:
        raise ValueError("Weekly forecast target must start on Monday")
    grid = gpd.read_parquet(root / "data/processed/dim_grid.parquet")
    fires = pd.read_parquet(firms_path or root / "data/processed/firms_kalimantan_clean.parquet")
    fires["acq_date"] = pd.to_datetime(fires["acq_date"])
    fires["week_start"] = pd.to_datetime(fires["week_start"])
    fires = fires.loc[fires["acq_date"] < target]
    if fires.empty:
        raise ValueError("No historical detections available")
    # Availability is checked by the ETL watermark; observations on a zero-fire
    # day are not required. A lack of observations is never proof of ingestion.
    watermark = root / "data/operational/firms_watermark.json"
    if not watermark.exists():
        raise ValueError("A successful ingestion watermark is required before current scoring")
    complete = pd.Timestamp(json.loads(watermark.read_text())["complete_through"]).tz_localize(None)
    if complete < target - pd.Timedelta(seconds=1):
        raise ValueError("FIRMS ingestion is not complete through the forecast cutoff")
    if contract.get("precipitation_method") != "era5_land_midnight_previous_day_v1":
        raise ValueError("Rebuild ERA5 daily precipitation and retrain before operational scoring")
    daily_path = root / "data/interim/era5_daily_corrected/daily_complete.parquet"
    if not daily_path.exists():
        raise ValueError("Corrected ERA5 daily partitions are required")
    daily = pd.read_parquet(daily_path)
    eligible = sorted(set(grid["grid_id"].astype(str)) & set(daily["grid_id"].unique()))
    if hashlib.sha256(json.dumps(eligible).encode()).hexdigest() != contract["weather_eligible_grid_sha256"]:
        raise ValueError("ERA5 land-mask coverage differs from the model contract")
    weather = weekly_weather(daily, eligible, target, contract["weather_lag_days"])
    panel = add_advanced_features(build_master_table(grid, fires, weather, target_week=target), grid)
    prepared = _prepare_model_data(panel)
    result = prepared.loc[prepared["week_start"].eq(target)].copy()
    result = result.dropna(subset=WEATHER_COLUMNS)
    if len(result) / len(eligible) < contract.get("minimum_grid_coverage", 0.99):
        raise ValueError("Current features are incomplete; release was not updated")
    return result.drop(columns=["fire_next_7d", "hotspot_next_7d", "hotspot_count"], errors="ignore")

def score_features(features: pd.DataFrame, model_directory: Path, *, issued_at=None, allow_historical=False) -> tuple[pd.DataFrame, dict]:
    classifier = joblib.load(model_directory / "fire_occurrence_classifier.joblib")
    counter = joblib.load(model_directory / "hotspot_count_model.joblib")
    contract = classifier.get("operational_contract")
    if not contract or contract != counter.get("operational_contract"):
        raise ValueError("Both models must share a trained operational latency contract")
    if not allow_historical and contract.get("precipitation_method") != "era5_land_midnight_previous_day_v1":
        raise ValueError("Operational scoring requires corrected ERA5 precipitation")
    if set(classifier["features"]) != set(features.columns).intersection(classifier["features"]):
        raise ValueError("Feature schema does not match model")
    if features.empty or features.duplicated(["grid_id", "week_start"]).any() or features["week_start"].nunique() != 1:
        raise ValueError("Scoring expects one unique complete grid/week snapshot")
    week = pd.Timestamp(features["week_start"].iloc[0]).normalize()
    now = pd.Timestamp(issued_at or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    until = week.tz_localize("UTC") + pd.Timedelta(days=7)
    if now >= until and not allow_historical:
        raise ValueError("Forecast window has expired; use explicit historical evaluation mode")
    output_columns = ["grid_id", "week_start", "province", "district", "centroid_lat", "centroid_lon", "peat_pct", "rainfall_30d", "hotspot_prev_30d"]
    result = features[output_columns].copy()
    probability = classifier["model"].predict_proba(features[classifier["features"]])[:, 1]
    count = np.clip(counter["model"].predict(features[counter["features"]]), 0, None)
    if not np.isfinite(probability).all() or not np.isfinite(count).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("Model returned invalid predictions")
    result["predicted_fire_probability"] = probability
    result["predicted_hotspot_count_next_7d"] = count
    result["risk_category"] = _risk_category(result["predicted_fire_probability"], classifier["validation_thresholds"])
    result["count_category"] = _count_category(result["predicted_hotspot_count_next_7d"], counter["validation_thresholds"])
    result["priority_category"] = _priority_category(result["predicted_fire_probability"], result["predicted_hotspot_count_next_7d"], classifier["validation_thresholds"], counter["validation_thresholds"])
    metadata = {
        "issued_at": now.isoformat(), "feature_as_of": week.isoformat(),
        "valid_from": week.tz_localize("UTC").isoformat(), "valid_until": until.isoformat(),
        "weather_complete_through": (week - pd.Timedelta(days=contract["weather_lag_days"] + 1)).date().isoformat(),
        "weather_lag_days": contract["weather_lag_days"],
        "historical": now >= until,
        "coverage": {"predicted_grids": len(result), "all_grids": contract.get("total_grid_count"), "weather_eligible_grids": contract.get("weather_eligible_grid_count")},
        "model_sha256": {"occurrence": sha256(model_directory / "fire_occurrence_classifier.joblib"), "count": sha256(model_directory / "hotspot_count_model.joblib")},
    }
    return result, metadata
