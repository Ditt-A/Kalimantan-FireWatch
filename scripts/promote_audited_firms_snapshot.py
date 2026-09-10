#!/usr/bin/env python3
"""Promote a date-bounded, audited FIRMS snapshot for the dashboard.

The prior processed snapshot is retained alongside the new one, so the API
promotion is recoverable and its source cutoff is explicit.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))

from firewatch.data_pipeline import clean_and_enrich_firms, load_kalimantan_boundaries  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=pd.Timestamp, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cutoff = args.end_date.normalize()
    processed = ROOT / "data" / "processed"
    output = processed / "firms_kalimantan_clean.parquet"
    grid = gpd.read_parquet(processed / "dim_grid.parquet").to_crs("EPSG:4326")
    adm1, adm2, kalimantan_union = load_kalimantan_boundaries()
    cleaned = clean_and_enrich_firms(grid, adm1, adm2, kalimantan_union, end_date=cutoff)

    natural_key = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument", "confidence", "frp"]
    required = {"detection_id", "latitude", "longitude", "acq_date", "acq_datetime", "grid_id", "province", "district", "footprint_ha_proxy"}
    failures: list[str] = []
    if cleaned.empty:
        failures.append("no retained Kalimantan detections")
    if required.difference(cleaned.columns):
        failures.append(f"missing columns: {sorted(required.difference(cleaned.columns))}")
    if not cleaned.empty and cleaned["acq_date"].max() != cutoff:
        failures.append(f"latest date is {cleaned['acq_date'].max()}, not {cutoff}")
    if cleaned["grid_id"].isna().any() or cleaned["province"].isna().any():
        failures.append("grid or province assignment is missing")
    if cleaned["detection_id"].duplicated().any() or cleaned.duplicated(natural_key).any():
        failures.append("duplicate detection IDs or natural keys")
    if cleaned["footprint_ha_proxy"].isna().any() or (cleaned["footprint_ha_proxy"] <= 0).any():
        failures.append("sensor footprint proxy is missing or non-positive")
    if failures:
        raise RuntimeError("FIRMS promotion validation failed: " + "; ".join(failures))

    temporary = output.with_suffix(".parquet.pending")
    cleaned.to_parquet(temporary, index=False)
    backup = processed / f"firms_kalimantan_clean_through_{pd.read_parquet(output, columns=['acq_date'])['acq_date'].max():%Y%m%d}.parquet"
    if output.exists() and not backup.exists():
        shutil.copy2(output, backup)
    temporary.replace(output)

    report = {
        "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "cutoff_date_utc": cutoff.date().isoformat(),
        "rows": int(len(cleaned)),
        "date_min": cleaned["acq_date"].min().date().isoformat(),
        "date_max": cleaned["acq_date"].max().date().isoformat(),
        "duplicate_natural_keys": int(cleaned.duplicated(natural_key).sum()),
        "missing_grid_assignments": int(cleaned["grid_id"].isna().sum()),
        "unassigned_districts": int(cleaned["district"].eq("Unassigned").sum()),
        "footprint_ha_proxy_min": float(cleaned["footprint_ha_proxy"].min()),
        "footprint_ha_proxy_max": float(cleaned["footprint_ha_proxy"].max()),
        "backup_file": backup.name if backup.exists() else None,
        "source_note": "NASA FIRMS VIIRS NOAA-20 NRT is included only through the ERA5-complete daily cutoff.",
    }
    report_path = ROOT / "reports" / f"firms_snapshot_promotion_through_{cutoff:%Y%m%d}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
