"""Validate and register Python model weights used by the dashboard backend."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from firewatch.modeling import _prepare_model_data  # noqa: E402


MODELS_DIR = ROOT / "models"
F1_DATASET = ROOT / "data/processed/fact_grid_week.parquet"
F1_REPORT = ROOT / "reports/model_evaluation.json"
F2_TRAINING = ROOT / "data/processed/feature2_model_ready/training/personnel.parquet"
F2_MANIFEST = ROOT / "data/processed/feature2_model_ready/feature2_model_ready_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature1_entry(filename: str, kind: str, evaluation: dict[str, object]) -> dict[str, object]:
    path = MODELS_DIR / filename
    artifact = joblib.load(path)
    if not isinstance(artifact, dict) or not {"model", "features"} <= set(artifact):
        raise RuntimeError(f"{filename} is not a Feature-1 dashboard artifact")
    features = list(artifact["features"])
    data = _prepare_model_data(pd.read_parquet(F1_DATASET))
    missing = sorted(set(features) - set(data.columns))
    if missing:
        raise RuntimeError(f"{filename} feature schema is unavailable in dashboard data: {missing}")
    sample = data.loc[:, features].head(2)
    if kind == "occurrence":
        output = artifact["model"].predict_proba(sample)[:, 1]
        if not np.isfinite(output).all() or ((output < 0) | (output > 1)).any():
            raise RuntimeError(f"{filename} failed probability smoke prediction")
        output_contract = "predicted_fire_probability"
        metrics = evaluation["classification_test_metrics"]
        threshold = float(artifact["threshold"])
    else:
        output = artifact["model"].predict(sample)
        if not np.isfinite(output).all():
            raise RuntimeError(f"{filename} failed count smoke prediction")
        output_contract = "predicted_hotspot_count_next_7d"
        metrics = evaluation["count_test_metrics"]
        threshold = None
    return {
        "feature": "feature_1_incident_prioritization_localization",
        "kind": kind,
        "artifact": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "loader": "joblib.load",
        "model_class": type(artifact["model"]).__name__,
        "input_features": features,
        "output": output_contract,
        "threshold": threshold,
        "latest_held_out_2026_metrics": metrics,
        "smoke_prediction": [round(float(value), 6) for value in output],
        "status": "dashboard_ready",
    }


def feature2_entry() -> tuple[dict[str, object], dict[str, object]]:
    path = MODELS_DIR / "feature2_personnel_hgb_pilot.joblib"
    model = joblib.load(path)
    training = pd.read_parquet(F2_TRAINING)
    features = [column for column in training.columns if column != "target_value"]
    output = model.predict(training.loc[:1, features])
    if not np.isfinite(output).all() or (output <= 0).any():
        raise RuntimeError("Feature-2 personnel model failed smoke prediction")
    manifest = json.loads(F2_MANIFEST.read_text(encoding="utf-8"))
    fire_engine = manifest["fire_engine_contract"]
    personnel = {
        "feature": "feature_2_response_planning_resource_calculator",
        "kind": "personnel_estimator",
        "artifact": str(path.relative_to(ROOT)),
        "sha256": sha256(path),
        "loader": "joblib.load",
        "model_class": type(model).__name__,
        "input_features": features,
        "output": "planned_personnel",
        "status": "prototype_planning_only",
        "guardrail": "Return a planning estimate with confidence/field confirmation; never automatic dispatch.",
        "smoke_prediction": [round(float(value), 6) for value in output],
    }
    engine_policy = {
        "feature": "feature_2_response_planning_resource_calculator",
        "kind": "fire_engine_capacity_policy",
        "serving_dependency": fire_engine["serving_dependency"],
        "formula": fire_engine["formula"],
        "personnel_per_fire_engine": fire_engine["personnel_per_fire_engine"],
        "policy_version": fire_engine["policy_version"],
        "source": fire_engine["source"],
        "status": "dashboard_ready_policy_not_ml_weight",
        "guardrail": "Capacity planning only; validate fleet, road access, and operator judgement before mobilisation.",
    }
    return personnel, engine_policy


def main() -> None:
    evaluation = json.loads(F1_REPORT.read_text(encoding="utf-8"))
    occurrence = feature1_entry("fire_occurrence_classifier.joblib", "occurrence", evaluation)
    count = feature1_entry("hotspot_count_model.joblib", "count", evaluation)
    personnel, engine_policy = feature2_entry()
    registry = {
        "status": "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "artifacts": {
            "feature1_occurrence": occurrence,
            "feature1_count": count,
            "feature2_personnel": personnel,
        },
        "feature2_fire_engine_policy": engine_policy,
        "backend_contract": {
            "language": "Python",
            "load_method": "joblib.load from the artifact path in this registry",
            "note": "A Next.js/React dashboard calls a Python API; it does not load sklearn joblib weights directly in the browser.",
        },
    }
    (MODELS_DIR / "dashboard_model_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (MODELS_DIR / "feature2_fire_engine_policy.json").write_text(
        json.dumps(engine_policy, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(registry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
