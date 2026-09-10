"""Quality checks for generated FireWatch artefacts."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from firewatch.config import PROCESSED_DIR, REPORTS_DIR


def validate_processed_data() -> dict[str, Any]:
    fact = pd.read_parquet(PROCESSED_DIR / "fact_grid_week.parquet")
    grid = pd.read_parquet(PROCESSED_DIR / "dim_grid.parquet")
    fires = pd.read_parquet(PROCESSED_DIR / "firms_kalimantan_clean.parquet")

    required_fact = {
        "grid_id", "week_start", "hotspot_count", "rainfall_30d",
        "soil_moisture_7d", "peat_pct", "fire_next_7d", "hotspot_next_7d",
    }
    missing = sorted(required_fact - set(fact.columns))
    key_duplicates = int(fact.duplicated(["grid_id", "week_start"]).sum())
    invalid_counts = int((fact["hotspot_count"] < 0).sum())
    invalid_peat = int((fact["peat_pct"].dropna().lt(0) | fact["peat_pct"].dropna().gt(100)).sum())
    invalid_firms = int(
        (~fires["latitude"].between(-5, 8) | ~fires["longitude"].between(108, 119.5)).sum()
    )
    report: dict[str, Any] = {
        "status": "passed" if not (missing or key_duplicates or invalid_counts or invalid_peat or invalid_firms) else "failed",
        "fact_rows": int(len(fact)),
        "grid_rows": int(len(grid)),
        "firms_rows": int(len(fires)),
        "missing_fact_columns": missing,
        "primary_key_duplicates": key_duplicates,
        "negative_hotspot_counts": invalid_counts,
        "invalid_peat_pct": invalid_peat,
        "firms_outside_request_bbox": invalid_firms,
        "weather_nonnull_pct": round(float(fact["rainfall_30d"].notna().mean() * 100), 2),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "processed_data_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["status"] != "passed":
        raise ValueError(json.dumps(report))
    return report
