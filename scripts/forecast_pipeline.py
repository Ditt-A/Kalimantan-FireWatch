"""Train a latency-aware candidate, score a feature snapshot, or publish a release."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.forecasting import train_latency_candidate, build_current_features, score_features
from src.firewatch.releases import atomic_json, publish_release, current_snapshot

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["train", "features", "score", "publish", "rollback"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models/candidates/forecast_lag7_corrected")
    parser.add_argument("--output", type=Path, default=ROOT / "data/operational/forecast_candidate")
    parser.add_argument("--week", type=pd.Timestamp)
    parser.add_argument("--max-iter", type=int, default=150)
    parser.add_argument("--release-id")
    parser.add_argument("--allow-historical", action="store_true")
    args = parser.parse_args()
    if args.action == "train":
        print(json.dumps(train_latency_candidate(args.root / "data/processed/fact_grid_week.parquet", args.model_dir, max_iter=args.max_iter, corrected_daily=args.root / "data/interim/era5_daily_corrected/daily_complete.parquet"), indent=2))
        return
    if args.action == "rollback":
        from src.firewatch.releases import activate_release
        if not args.release_id:
            parser.error("--release-id is required for rollback")
        activate_release(args.release_id)
        return
    if args.action == "features":
        if args.week is None:
            parser.error("--week YYYY-MM-DD is required")
        contract = json.loads((args.model_dir / "contract.json").read_text())
        curated = args.root / "data/operational/etl/curated/firms_kalimantan_clean.parquet"
        features = build_current_features(args.root, args.week, contract, firms_path=curated if curated.exists() else None)
        args.output.mkdir(parents=True, exist_ok=True)
        features.to_parquet(args.output / "features.parquet", index=False)
        atomic_json(args.output / "features.manifest.json", {"week": args.week.isoformat(), "contract": contract})
        return
    if args.action == "score":
        features = pd.read_parquet(args.output / "features.parquet")
        predictions, metadata = score_features(features, args.model_dir, allow_historical=args.allow_historical)
        predictions.to_parquet(args.output / "prediction_grid_week.parquet", index=False)
        atomic_json(args.output / "forecast.json", metadata)
        print(json.dumps(metadata, indent=2))
        return
    metadata = json.loads((args.output / "forecast.json").read_text())
    if pd.Timestamp.now(tz="UTC") >= pd.Timestamp(metadata["valid_until"]) and not args.allow_historical:
        raise ValueError("Forecast has expired since scoring")
    if metadata["historical"] and not args.allow_historical:
        raise ValueError("Historical evaluation cannot be published as a current forecast")
    parent = current_snapshot()
    registry = json.loads((parent.root / "models/dashboard_model_registry.json").read_text())
    from src.firewatch.releases import sha256
    overrides = {"data/processed/prediction_grid_week.parquet": args.output / "prediction_grid_week.parquet"}
    for name, key in [("fire_occurrence_classifier.joblib", "feature1_occurrence"), ("hotspot_count_model.joblib", "feature1_count")]:
        overrides["models/" + name] = args.model_dir / name
        registry["artifacts"][key]["sha256"] = sha256(args.model_dir / name)
        registry["artifacts"][key]["operational_contract"] = json.loads((args.model_dir / "contract.json").read_text())
        evaluation = json.loads((args.model_dir / "evaluation.json").read_text())
        registry["artifacts"][key]["latest_held_out_2026_metrics"] = evaluation["classification_test" if key == "feature1_occurrence" else "count_test"]
    atomic_json(args.output / "dashboard_model_registry.json", registry)
    overrides["models/dashboard_model_registry.json"] = args.output / "dashboard_model_registry.json"
    release_id = args.release_id or datetime.now(timezone.utc).strftime("forecast-%Y%m%dT%H%M%SZ")
    release_metadata = {k: v for k, v in parent.metadata.items() if k not in {"files", "release_id", "quality_status", "created_at_utc"}}
    release_metadata["forecast"] = metadata
    print(publish_release(parent.root, release_id, release_metadata, overrides, expected_release_id=parent.token))

if __name__ == "__main__":
    main()
