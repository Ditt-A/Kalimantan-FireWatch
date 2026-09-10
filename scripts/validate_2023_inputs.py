#!/usr/bin/env python3
"""Validate the newly ingested 2023 FIRMS and ERA5-Land inputs before rebuilding."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import xarray as xr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRMS_DIR = PROJECT_ROOT / "data" / "raw" / "firms"
ERA5_DIR = PROJECT_ROOT / "data" / "raw" / "era5_land"
REPORT_PATH = PROJECT_ROOT / "reports" / "input_2023_quality.json"
EXPECTED_VARIABLES = ("tp", "t2m", "swvl1")


def _validate_firms() -> dict[str, object]:
    quality_path = FIRMS_DIR / "firms_viirs_noaa20_sp_20230101_20231231.quality.json"
    if not quality_path.exists():
        raise FileNotFoundError(f"Missing FIRMS quality report: {quality_path}")
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    required = {
        "status": "passed",
        "date_min": "2023-01-01",
        "date_max": "2023-12-31",
        "missing_coordinates": 0,
        "invalid_dates": 0,
        "outside_bbox": 0,
        "duplicate_detection_keys": 0,
    }
    problems = {
        field: {"expected": expected, "actual": quality.get(field)}
        for field, expected in required.items()
        if quality.get(field) != expected
    }
    if int(quality.get("rows", 0)) <= 0:
        problems["rows"] = {"expected": "> 0", "actual": quality.get("rows")}
    if problems:
        raise ValueError(f"FIRMS 2023 validation failed: {problems}")
    return {"rows": int(quality["rows"]), "checks": "passed"}


def _validate_era5_month(month: pd.Timestamp) -> dict[str, object]:
    name = f"era5_land_{month:%Y_%m}.nc"
    path = ERA5_DIR / name
    reference_path = ERA5_DIR / f"era5_land_2024_{month:%m}.nc"
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Missing or empty ERA5 2023 file: {path}")
    if not reference_path.exists():
        raise FileNotFoundError(f"Missing 2024 comparison file: {reference_path}")

    with xr.open_dataset(path) as data, xr.open_dataset(reference_path) as reference:
        variables = tuple(data.data_vars)
        if variables != EXPECTED_VARIABLES:
            raise ValueError(f"{name} variables are {variables}, expected {EXPECTED_VARIABLES}")
        if tuple(reference.data_vars) != EXPECTED_VARIABLES:
            raise ValueError(f"Reference schema changed in {reference_path.name}")
        if data.sizes.get("latitude") != reference.sizes.get("latitude") or data.sizes.get(
            "longitude"
        ) != reference.sizes.get("longitude"):
            raise ValueError(f"{name} spatial grid differs from its 2024 reference")
        time_name = "valid_time" if "valid_time" in data.coords else "time"
        expected_hours = month.days_in_month * 24
        if data.sizes.get(time_name) != expected_hours:
            raise ValueError(f"{name} has {data.sizes.get(time_name)} timestamps, expected {expected_hours}")
        actual_start = pd.Timestamp(data[time_name].min().item()).isoformat()
        actual_end = pd.Timestamp(data[time_name].max().item()).isoformat()
        expected_start = month.normalize().isoformat()
        expected_end = (month + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)).isoformat()
        if actual_start != expected_start or actual_end != expected_end:
            raise ValueError(f"{name} has unexpected temporal coverage: {actual_start} to {actual_end}")
        expected_values = int(data.sizes[time_name] * data.sizes["latitude"] * data.sizes["longitude"])
        reference_time_name = "valid_time" if "valid_time" in reference.coords else "time"
        reference_values = int(
            reference.sizes[reference_time_name]
            * reference.sizes["latitude"]
            * reference.sizes["longitude"]
        )
        non_null = {variable: int(data[variable].count().item()) for variable in EXPECTED_VARIABLES}
        non_null_fraction = {
            variable: count / expected_values for variable, count in non_null.items()
        }
        reference_fraction = {
            variable: int(reference[variable].count().item()) / reference_values
            for variable in EXPECTED_VARIABLES
        }
        invalid = {
            variable: {"actual": non_null_fraction[variable], "reference": reference_fraction[variable]}
            for variable in EXPECTED_VARIABLES
            if non_null[variable] == 0
            or abs(non_null_fraction[variable] - reference_fraction[variable]) > 0.001
        }
        if invalid:
            raise ValueError(f"{name} non-null coverage differs from 2024: {invalid}")
    return {
        "file": name,
        "bytes": path.stat().st_size,
        "non_null_values": non_null,
        "non_null_fraction": non_null_fraction,
    }


def main() -> int:
    months = pd.date_range("2023-01-01", "2023-12-01", freq="MS")
    report = {
        "status": "passed",
        "firms": _validate_firms(),
        "era5_months": [_validate_era5_month(month) for month in months],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"2023 input validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
