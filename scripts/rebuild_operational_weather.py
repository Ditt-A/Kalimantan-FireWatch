"""Build corrected ERA5-Land daily partitions without changing legacy snapshots.

ERA5-Land tp at 00 UTC is the preceding day's total, not an hourly amount.
https://confluence.ecmwf.int/pages/viewpage.action?pageId=315578204
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import geopandas as gpd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.data_pipeline import _open_era5_month
from src.firewatch.releases import atomic_json, sha256

def summarise(source, grid):
    from src.firewatch.weather_processing import summarise_dataset
    return summarise_dataset(_open_era5_month(source), grid)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    grid = gpd.read_parquet(args.root / "data/processed/dim_grid.parquet")
    output = args.root / "data/interim/era5_daily_corrected"
    output.mkdir(parents=True, exist_ok=True)
    manifests, frames = [], []
    sources = sorted((args.root / "data/raw/era5_land").glob("*.nc"))
    for i, source in enumerate(sources, 1):
        if "incomplete" in source.name:
            continue
        cached = output / (source.stem + ".parquet")
        meta = output / (source.stem + ".json")
        digest = sha256(source)
        if not cached.exists() or not meta.exists() or json.loads(meta.read_text()).get("source_sha256") != digest:
            frame = summarise(source, grid)
            frame.to_parquet(cached, index=False)
            atomic_json(meta, {"source_sha256": digest, "source": source.name, "method": "era5_land_midnight_previous_day_v1"})
        frames.append(pd.read_parquet(cached))
        manifests.append(json.loads(meta.read_text()))
        print(f"Corrected daily weather {i}/{len(sources)}: {source.name}", flush=True)
    if not frames:
        raise ValueError("No usable hourly ERA5 source files")
    # Adjacent months contribute complementary temperature and midnight rain
    # at the boundary. Merge non-null fields, never drop a boundary day.
    combined = pd.concat(frames, ignore_index=True).groupby(["grid_id", "date"], as_index=False).first()
    columns = ["rainfall_mm", "temperature_mean_c", "temperature_max_c", "soil_moisture"]
    combined = combined.dropna(subset=columns)
    combined.to_parquet(output / "daily_complete.parquet", index=False)
    atomic_json(output / "manifest.json", {"method": "era5_land_midnight_previous_day_v1", "rows": len(combined), "date_min": str(combined.date.min()), "date_max": str(combined.date.max()), "sources": manifests, "daily_sha256": sha256(output / "daily_complete.parquet")})
    print(json.dumps({"rows": len(combined), "complete_through": str(combined.date.max()), "output": str(output)}, indent=2))

if __name__ == "__main__":
    main()
