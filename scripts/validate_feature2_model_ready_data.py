"""Validate model-readiness, separation, and serving contracts for Feature 2."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/processed/feature2_model_ready"
REPORT = BASE / "feature2_model_ready_validation.json"
PROHIBITED = {"is_synthetic", "simulation_only", "data_origin", "source_url", "source_sha256"}
ENGINE_CREW_SIZE = 6


def assert_union(real: pd.DataFrame, synthetic: pd.DataFrame, training: pd.DataFrame) -> bool:
    expected = pd.concat([real, synthetic], ignore_index=True)
    columns = list(expected.columns)
    expected = expected.sort_values(columns, kind="stable").reset_index(drop=True)
    observed = training.sort_values(columns, kind="stable").reset_index(drop=True)
    return observed.equals(expected)


def smoke_fit(frame: pd.DataFrame) -> None:
    """Fit/predict in memory to prove the direct matrix is consumable by sklearn."""
    x = frame.drop(columns="target_value")
    y = pd.to_numeric(frame["target_value"], errors="raise")
    numeric = list(x.select_dtypes(include=["number"]).columns)
    categorical = [column for column in x.columns if column not in numeric]
    transformers = []
    if numeric:
        transformers.append(("numeric", SimpleImputer(strategy="median"), numeric))
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    model = Pipeline(
        [
            ("preprocess", ColumnTransformer(transformers)),
            ("regressor", RandomForestRegressor(n_estimators=8, max_depth=4, random_state=20260908)),
        ]
    )
    model.fit(x, y)
    prediction = model.predict(x.iloc[: min(5, len(x))])
    if not np.isfinite(prediction).all():
        raise RuntimeError("model smoke fit produced non-finite predictions")


def main() -> None:
    errors: list[str] = []
    summary: dict[str, object] = {}
    expected_schemas = {
        "personnel": None,
        "fire_engine": ["planned_personnel", "target_value"],
    }
    for target, required_schema in expected_schemas.items():
        real = pd.read_parquet(BASE / "real" / f"{target}.parquet")
        synthetic = pd.read_parquet(BASE / "synthetic" / f"{target}.parquet")
        training = pd.read_parquet(BASE / "training" / f"{target}.parquet")
        if real.empty or synthetic.empty or training.empty:
            errors.append(f"{target}: one or more partitions are empty")
        if list(real.columns) != list(synthetic.columns) or list(real.columns) != list(training.columns):
            errors.append(f"{target}: partition schemas differ")
        if required_schema is not None and list(training.columns) != required_schema:
            errors.append(f"{target}: schema is not personnel-only dependency")
        if PROHIBITED & set(training.columns):
            errors.append(f"{target}: provenance/origin feature leaked into direct matrix")
        target_values = pd.to_numeric(training.get("target_value"), errors="coerce")
        if target_values.isna().any() or (target_values < 0).any() or (target_values % 1 != 0).any():
            errors.append(f"{target}: target_value is not a non-negative integer")
        if len(training) != len(real) + len(synthetic) or not assert_union(real, synthetic, training):
            errors.append(f"{target}: training is not the direct real + synthetic union")
        if synthetic.drop(columns="target_value").isna().any().any():
            errors.append(f"{target}: synthetic features contain null values")
        try:
            smoke_fit(training)
        except Exception as exc:  # noqa: BLE001 - convert to validation report
            errors.append(f"{target}: sklearn smoke fit failed: {exc}")
        summary[target] = {
            "real_rows": int(len(real)),
            "synthetic_rows": int(len(synthetic)),
            "training_rows": int(len(training)),
            "target_min": int(target_values.min()),
            "target_max": int(target_values.max()),
            "sklearn_smoke_fit": "passed" if not any(err.startswith(f"{target}: sklearn") for err in errors) else "failed",
        }

    engine_synthetic = pd.read_parquet(BASE / "synthetic/fire_engine.parquet")
    expected_engine = np.ceil(engine_synthetic["planned_personnel"] / ENGINE_CREW_SIZE).astype(int)
    if not expected_engine.equals(engine_synthetic["target_value"]):
        errors.append("fire_engine: synthetic target breaks six-person capacity policy")
    sidecar = pd.read_csv(BASE / "evaluation_sidecars/fire_engine_real_provenance.csv")
    engine_real = pd.read_parquet(BASE / "real/fire_engine.parquet")
    if len(sidecar) != len(engine_real) or sidecar["source_url"].isna().any():
        errors.append("fire_engine: real provenance sidecar is incomplete")
    personnel_sidecar = pd.read_csv(BASE / "evaluation_sidecars/personnel_real_provenance.csv")
    personnel_real = pd.read_parquet(BASE / "real/personnel.parquet")
    if len(personnel_sidecar) != len(personnel_real) or personnel_sidecar["source_url"].isna().any():
        errors.append("personnel: real provenance sidecar is incomplete")

    manifest = json.loads((BASE / "feature2_model_ready_manifest.json").read_text(encoding="utf-8"))
    contract = manifest.get("fire_engine_contract", {})
    if contract.get("serving_dependency") != "planned_personnel only":
        errors.append("fire_engine: serving dependency is not personnel-only")
    if contract.get("personnel_per_fire_engine") != ENGINE_CREW_SIZE:
        errors.append("fire_engine: manifest crew capacity differs from validation policy")

    report = {
        "status": "passed" if not errors else "failed",
        "datasets": summary,
        "fire_engine_policy": {
            "formula": "ceil(planned_personnel / 6)",
            "real_pair_count": int(len(engine_real)),
            "synthetic_rows_follow_policy": bool(not any("synthetic target breaks" in err for err in errors)),
        },
        "evaluation_sidecars": {
            "personnel_real_rows": int(len(personnel_sidecar)),
            "personnel_source_documents": int(personnel_sidecar["source_document_id"].nunique()),
            "fire_engine_real_rows": int(len(sidecar)),
            "fire_engine_source_documents": int(sidecar["source_document_id"].nunique()),
        },
        "errors": errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
