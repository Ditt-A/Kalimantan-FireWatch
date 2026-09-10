"""Validate separation and direct-training contracts for hybrid Feature-2 data."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/processed/feature2_hybrid_training"
REPORT = BASE / "hybrid_training_validation.json"
TARGETS = ("personnel", "fire_engine", "water_tanker", "pickup")
PROHIBITED = {"is_synthetic", "simulation_only", "data_origin", "source_url", "source_sha256"}


def main() -> None:
    errors: list[str] = []
    summary: dict[str, object] = {}
    for target in TARGETS:
        real = pd.read_parquet(BASE / "real" / f"{target}.parquet")
        synthetic = pd.read_parquet(BASE / "synthetic" / f"{target}.parquet")
        training = pd.read_parquet(BASE / "training" / f"{target}.parquet")
        if real.empty or synthetic.empty or training.empty:
            errors.append(f"{target}: one or more dataset partitions are empty")
        if list(real.columns) != list(synthetic.columns) or list(real.columns) != list(training.columns):
            errors.append(f"{target}: partition schemas differ")
        if PROHIBITED & set(training.columns):
            errors.append(f"{target}: prohibited provenance/origin feature in training data")
        if "target_value" not in training:
            errors.append(f"{target}: target_value missing")
        else:
            values = pd.to_numeric(training["target_value"], errors="coerce")
            if values.isna().any() or (values < 0).any() or (values % 1 != 0).any():
                errors.append(f"{target}: target is not a non-negative integer")
        if len(training) != len(real) + len(synthetic):
            errors.append(f"{target}: training count does not equal real + synthetic")
        expected = pd.concat([real, synthetic], ignore_index=True)
        sort_columns = list(expected.columns)
        expected = expected.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        observed = training.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        if not observed.equals(expected):
            errors.append(f"{target}: training content is not the real + synthetic union")
        if synthetic.drop(columns=["target_value"]).isna().any().any():
            errors.append(f"{target}: synthetic feature matrix contains missing values")
        summary[target] = {
            "real_rows": int(len(real)),
            "synthetic_rows": int(len(synthetic)),
            "training_rows": int(len(training)),
            "target_min": int(training["target_value"].min()),
            "target_max": int(training["target_value"].max()),
        }
    manifest = json.loads((BASE / "synthetic_generation_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "passed":
        errors.append("Generation manifest is not passed")
    report = {"status": "passed" if not errors else "failed", "datasets": summary, "errors": errors}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
