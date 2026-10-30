"""Replay actual historical inputs through the new feature/scoring path, offline."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.forecasting import build_current_features, score_features
from src.firewatch.releases import atomic_json, current_snapshot

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", type=pd.Timestamp, default=pd.Timestamp("2026-07-20"))
    args = parser.parse_args()
    model_dir = ROOT / "models/candidates/forecast_lag7_corrected"
    contract = json.loads((model_dir / "contract.json").read_text())
    before = current_snapshot().token
    with tempfile.TemporaryDirectory(prefix="firewatch-historical-replay-") as directory:
        replay_root = Path(directory)
        (replay_root / "data/interim").mkdir(parents=True)
        (replay_root / "data/processed").symlink_to(ROOT / "data/processed", target_is_directory=True)
        (replay_root / "data/interim/era5_daily_corrected").symlink_to(ROOT / "data/interim/era5_daily_corrected", target_is_directory=True)
        # This marker is confined to the temporary replay root. It is not
        # promoted as evidence that a production acquisition job has run.
        atomic_json(replay_root / "data/operational/firms_watermark.json", {"complete_through": args.week.isoformat(), "mode": "historical_replay_only"})
        features = build_current_features(replay_root, args.week, contract)
        predictions, metadata = score_features(features, model_dir, allow_historical=True)
    if current_snapshot().token != before:
        raise ValueError("Offline replay must not change the serving release")
    output = ROOT / "data/operational/forecast_historical_validation"
    output.mkdir(parents=True, exist_ok=True)
    features.to_parquet(output / "features.parquet", index=False)
    predictions.to_parquet(output / "prediction_grid_week.parquet", index=False)
    atomic_json(output / "forecast.json", metadata)
    report = {"status": "passed", "mode": "historical_replay_not_published", "rows": len(predictions), "target_labels_required": False, "weather_lag_days": contract["weather_lag_days"], "precipitation_method": contract["precipitation_method"], "metadata": metadata, "serving_release_unchanged": True}
    reports = ROOT / "reports/production_checks"
    reports.mkdir(parents=True, exist_ok=True)
    atomic_json(reports / "forecast_replay.json", report)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
