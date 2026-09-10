"""Train and evaluate the two Feature-2 MVP modelling paths.

The primary benchmark uses a fixed random split of the direct mixed matrix, as
requested for MVP model selection. Optional source-group helper functions are
kept for a later operational audit. The engine booster is kept as an experiment
for comparison; serving uses the explicit six-person capacity policy instead.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, median_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_feature2_hybrid_training_data import (  # noqa: E402
    US_SUMMARY_PATH,
    WATER_PATH,
    reference_lookup,
    synthetic_scenarios,
)


DATA_DIR = ROOT / "data/processed/feature2_model_ready"
REPORT_DIR = ROOT / "reports/feature2_modeling"
MODEL_DIR = ROOT / "models"
SEED = 20260908
REAL_SAMPLE_WEIGHT = 10.0
ENGINE_REAL_SAMPLE_WEIGHT = 30.0
PERSONNEL_PER_ENGINE = 6


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise categorical nulls without adding provenance/origin features."""
    output = frame.copy()
    for column in output.columns:
        if pd.api.types.is_numeric_dtype(output[column]):
            output[column] = pd.to_numeric(output[column], errors="coerce")
        else:
            output[column] = output[column].astype("object").where(output[column].notna(), "unknown")
    return output


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric = [column for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
    categorical = [column for column in frame.columns if column not in numeric]
    return numeric, categorical


def boost_pipeline(frame: pd.DataFrame, *, min_samples_leaf: int) -> Pipeline:
    numeric, categorical = feature_columns(frame)
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric:
        transformers.append(("numeric", SimpleImputer(strategy="median"), numeric))
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("preprocess", ColumnTransformer(transformers, sparse_threshold=0.0)),
            (
                "regressor",
                HistGradientBoostingRegressor(
                    loss="poisson",
                    learning_rate=0.05,
                    max_iter=150,
                    max_leaf_nodes=7,
                    min_samples_leaf=min_samples_leaf,
                    l2_regularization=2.0,
                    random_state=SEED,
                ),
            ),
        ]
    )


def metrics(y_true: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    prediction = np.clip(np.asarray(prediction, dtype=float), 0, None)
    return {
        "mae": round(float(mean_absolute_error(y_true, prediction)), 3),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, prediction))), 3),
        "median_ae": round(float(median_absolute_error(y_true, prediction)), 3),
        "r2": round(float(r2_score(y_true, prediction)), 3),
    }


def grouped_splits(groups: pd.Series, desired_splits: int = 5):
    unique_groups = int(groups.nunique())
    if unique_groups < 2:
        raise RuntimeError("Need at least two source documents for grouped evaluation")
    return GroupKFold(n_splits=min(desired_splits, unique_groups))


def fold_synthetic(personnel_real_train: pd.DataFrame) -> pd.DataFrame:
    """Regenerate synthetic personnel from fold-train labels only, avoiding leakage."""
    water = pd.read_parquet(WATER_PATH)
    summary = json.loads(US_SUMMARY_PATH.read_text(encoding="utf-8"))
    us_reference = reference_lookup(summary)
    generated = synthetic_scenarios(personnel_real_train, water, us_reference)
    return generated.drop(columns=["target_fire_engine", "target_water_tanker", "target_pickup"]).rename(
        columns={"target_personnel": "target_value"}
    )


def personnel_evaluation() -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    real = pd.read_parquet(DATA_DIR / "real/personnel.parquet")
    provenance = pd.read_csv(DATA_DIR / "evaluation_sidecars/personnel_real_provenance.csv")
    if len(real) != len(provenance):
        raise RuntimeError("Personnel provenance sidecar does not match real partition")
    x_real = clean_features(real.drop(columns="target_value"))
    y_real = real["target_value"].to_numpy(dtype=float)
    groups = provenance["source_document_id"].astype(str)
    records: list[pd.DataFrame] = []

    for fold, (train_idx, test_idx) in enumerate(grouped_splits(groups).split(x_real, y_real, groups), start=1):
        train_real = real.iloc[train_idx].reset_index(drop=True)
        test_x = x_real.iloc[test_idx].reset_index(drop=True)
        test_y = y_real[test_idx]

        # Benchmark: small-data, Indonesian, log-area-only robust regression.
        baseline = HuberRegressor(epsilon=1.35, alpha=1.0).fit(
            train_real[["log_area_ha"]], train_real["target_value"]
        )
        baseline_prediction = np.clip(baseline.predict(test_x[["log_area_ha"]]), 1, None)

        # Boosting is fitted with synthetic scenarios that were rebuilt from
        # the fold's train-only local personnel data.
        synthetic_train = fold_synthetic(train_real)
        model_train = pd.concat([train_real, synthetic_train], ignore_index=True)
        model_x = clean_features(model_train.drop(columns="target_value"))
        model_y = model_train["target_value"].to_numpy(dtype=float)
        weights = np.concatenate(
            [np.full(len(train_real), REAL_SAMPLE_WEIGHT), np.ones(len(synthetic_train))]
        )
        booster = boost_pipeline(model_x, min_samples_leaf=15)
        booster.fit(model_x, model_y, regressor__sample_weight=weights)
        boost_prediction = np.clip(booster.predict(test_x), 1, None)

        records.append(
            pd.DataFrame(
                {
                    "fold": fold,
                    "incident_or_evidence_id": provenance.iloc[test_idx]["incident_or_evidence_id"].to_numpy(),
                    "source_document_id": groups.iloc[test_idx].to_numpy(),
                    "actual_personnel": test_y,
                    "baseline_area_huber": baseline_prediction,
                    "boosting_hybrid": boost_prediction,
                }
            )
        )
    predictions = pd.concat(records, ignore_index=True)
    y = predictions["actual_personnel"].to_numpy(dtype=float)
    summary = {
        "evaluation_protocol": "5-fold GroupKFold by source_document_id; test rows are real Indonesian records only; synthetic scenarios are regenerated from each fold's real training records only.",
        "real_rows": int(len(real)),
        "source_documents": int(groups.nunique()),
        "baseline_area_huber": metrics(y, predictions["baseline_area_huber"].to_numpy()),
        "boosting_hybrid": metrics(y, predictions["boosting_hybrid"].to_numpy()),
    }
    return predictions, summary, real


def personnel_artifact() -> dict[str, Any]:
    """Fit the candidate from the direct mixed training matrix, without origin input."""
    training = pd.read_parquet(DATA_DIR / "training/personnel.parquet")
    x = clean_features(training.drop(columns="target_value"))
    y = training["target_value"].to_numpy(dtype=float)
    model = boost_pipeline(x, min_samples_leaf=15)
    model.fit(x, y)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODEL_DIR / "feature2_personnel_hgb_pilot.joblib"
    joblib.dump(model, artifact_path)
    return {
        "artifact": str(artifact_path.relative_to(ROOT)),
        "sha256": sha256(artifact_path),
        "training_rows": int(len(training)),
        "training_protocol": "direct mixed matrix; no origin/provenance input",
        "status": "pilot_planning_only",
    }


def engine_evaluation(personnel_predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    engine_real = pd.read_parquet(DATA_DIR / "real/fire_engine.parquet")
    engine_provenance = pd.read_csv(DATA_DIR / "evaluation_sidecars/fire_engine_real_provenance.csv")
    if len(engine_real) != len(engine_provenance):
        raise RuntimeError("Engine provenance sidecar does not match real partition")
    oof_people = personnel_predictions.loc[:, ["incident_or_evidence_id", "boosting_hybrid"]]
    joined = engine_provenance.merge(oof_people, on="incident_or_evidence_id", how="left", validate="one_to_one")
    if joined["boosting_hybrid"].isna().any():
        raise RuntimeError("Every paired engine case must have an out-of-fold personnel prediction")
    engine = pd.DataFrame(
        {
            "planned_personnel": np.clip(joined["boosting_hybrid"].to_numpy(dtype=float), 1, None),
            "actual_fire_engine": engine_real["target_value"].to_numpy(dtype=float),
            "source_document_id": engine_provenance["source_document_id"].astype(str).to_numpy(),
            "incident_or_evidence_id": engine_provenance["incident_or_evidence_id"].astype(str).to_numpy(),
        }
    )
    groups = engine["source_document_id"]
    records: list[pd.DataFrame] = []
    for fold, (train_idx, test_idx) in enumerate(grouped_splits(groups).split(engine, engine["actual_fire_engine"], groups), start=1):
        train = engine.iloc[train_idx].reset_index(drop=True)
        test = engine.iloc[test_idx].reset_index(drop=True)

        # Rebuild fold-only synthetic personnel/engine capacity.  This avoids
        # using a static synthetic mapping that saw real labels from a held-out
        # source document.
        excluded_sources = set(test["source_document_id"])
        personnel_real = pd.read_parquet(DATA_DIR / "real/personnel.parquet")
        personnel_provenance = pd.read_csv(DATA_DIR / "evaluation_sidecars/personnel_real_provenance.csv")
        keep = ~personnel_provenance["source_document_id"].astype(str).isin(excluded_sources)
        fold_personnel_real = personnel_real.loc[keep.to_numpy()].reset_index(drop=True)
        synthetic_personnel = fold_synthetic(fold_personnel_real)
        synthetic_engine = pd.DataFrame(
            {
                "planned_personnel": synthetic_personnel["target_value"].to_numpy(dtype=float),
                "target_value": np.ceil(synthetic_personnel["target_value"] / PERSONNEL_PER_ENGINE).astype(float),
            }
        )
        train_model = pd.concat(
            [
                train.loc[:, ["planned_personnel", "actual_fire_engine"]].rename(columns={"actual_fire_engine": "target_value"}),
                synthetic_engine,
            ],
            ignore_index=True,
        )
        x_train = clean_features(train_model.drop(columns="target_value"))
        weights = np.concatenate(
            [np.full(len(train), ENGINE_REAL_SAMPLE_WEIGHT), np.ones(len(synthetic_engine))]
        )
        booster = boost_pipeline(x_train, min_samples_leaf=12)
        booster.fit(x_train, train_model["target_value"], regressor__sample_weight=weights)
        boost_prediction = np.clip(np.rint(booster.predict(clean_features(test[["planned_personnel"]]))), 1, None)
        policy_prediction = np.ceil(test["planned_personnel"] / PERSONNEL_PER_ENGINE)
        records.append(
            pd.DataFrame(
                {
                    "fold": fold,
                    "incident_or_evidence_id": test["incident_or_evidence_id"].to_numpy(),
                    "source_document_id": test["source_document_id"].to_numpy(),
                    "actual_fire_engine": test["actual_fire_engine"].to_numpy(),
                    "planned_personnel_oof": test["planned_personnel"].to_numpy(),
                    "benchmark_personnel_capacity": policy_prediction.to_numpy(),
                    "boosting_personnel_only": boost_prediction,
                }
            )
        )
    predictions = pd.concat(records, ignore_index=True)
    y = predictions["actual_fire_engine"].to_numpy(dtype=float)
    summary = {
        "evaluation_protocol": "Source-group CV over nine paired real records. Engine inputs are out-of-fold personnel predictions, so this is a composed end-to-end pilot check, not an oracle-personnel test.",
        "real_rows": int(len(engine)),
        "source_documents": int(groups.nunique()),
        "benchmark_personnel_capacity": metrics(y, predictions["benchmark_personnel_capacity"].to_numpy()),
        "boosting_personnel_only": metrics(y, predictions["boosting_personnel_only"].to_numpy()),
        "selection": "Deterministic personnel-capacity policy remains the serving method; booster is non-deployable because nine paired labels cannot validate an empirical engine model.",
    }

    # Save the explicitly non-serving comparison artifact for reproducibility.
    all_synthetic = pd.read_parquet(DATA_DIR / "synthetic/fire_engine.parquet")
    all_model = pd.concat(
        [
            engine.loc[:, ["planned_personnel", "actual_fire_engine"]].rename(columns={"actual_fire_engine": "target_value"}),
            all_synthetic,
        ],
        ignore_index=True,
    )
    model = boost_pipeline(clean_features(all_model.drop(columns="target_value")), min_samples_leaf=12)
    weights = np.concatenate(
        [np.full(len(engine), ENGINE_REAL_SAMPLE_WEIGHT), np.ones(len(all_synthetic))]
    )
    model.fit(clean_features(all_model.drop(columns="target_value")), all_model["target_value"], regressor__sample_weight=weights)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODEL_DIR / "feature2_fire_engine_hgb_experimental.joblib"
    joblib.dump(model, artifact_path)
    artifact = {
        "artifact": str(artifact_path.relative_to(ROOT)),
        "sha256": sha256(artifact_path),
        "status": "experimental_not_for_serving",
        "real_sample_weight": ENGINE_REAL_SAMPLE_WEIGHT,
    }
    return predictions, summary, artifact


def mixed_personnel_evaluation() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Primary requested evaluation: fixed random split of the combined matrix.

    Direct matrices deliberately contain no origin field, so both the train and
    test partitions remain mixed. This answers model-selection questions on the
    chosen training distribution; it must not be presented as field accuracy.
    """
    data = pd.read_parquet(DATA_DIR / "training/personnel.parquet").reset_index(names="row_index")
    x = clean_features(data.drop(columns=["target_value", "row_index"]))
    y = data["target_value"].to_numpy(dtype=float)
    train_idx, test_idx = train_test_split(
        np.arange(len(data)), test_size=0.20, random_state=SEED, shuffle=True
    )
    x_train, x_test = x.iloc[train_idx], x.iloc[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    baseline = HuberRegressor(epsilon=1.35, alpha=1.0).fit(
        x_train[["log_area_ha"]], y_train
    )
    baseline_prediction = np.clip(baseline.predict(x_test[["log_area_ha"]]), 1, None)
    booster = boost_pipeline(x_train, min_samples_leaf=15)
    booster.fit(x_train, y_train)
    boost_prediction = np.clip(booster.predict(x_test), 1, None)
    predictions = pd.DataFrame(
        {
            "row_index": data.iloc[test_idx]["row_index"].to_numpy(),
            "actual_personnel": y_test,
            "benchmark_area_huber": baseline_prediction,
            "boosting_hybrid": boost_prediction,
        }
    ).sort_values("row_index").reset_index(drop=True)
    summary = {
        "evaluation_protocol": "Fixed random 80:20 split over the direct mixed training matrix. Both train and test contain the same combined real/synthetic distribution; provenance is not an input or split key.",
        "training_rows": int(len(train_idx)),
        "test_rows": int(len(test_idx)),
        "benchmark_area_huber": metrics(y_test, baseline_prediction),
        "boosting_hybrid": metrics(y_test, boost_prediction),
        "interpretation_guardrail": "Mixed-holdout performance measures fit to the configured hybrid scenario distribution, not real-world field accuracy.",
    }
    return predictions, summary


def mixed_engine_evaluation() -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Compare the personnel-capacity policy with boosting on the mixed matrix."""
    data = pd.read_parquet(DATA_DIR / "training/fire_engine.parquet").reset_index(names="row_index")
    x = clean_features(data.drop(columns=["target_value", "row_index"]))
    y = data["target_value"].to_numpy(dtype=float)
    train_idx, test_idx = train_test_split(
        np.arange(len(data)), test_size=0.20, random_state=SEED, shuffle=True
    )
    x_train, x_test = x.iloc[train_idx], x.iloc[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    policy_prediction = np.ceil(x_test["planned_personnel"] / PERSONNEL_PER_ENGINE)
    booster = boost_pipeline(x_train, min_samples_leaf=12)
    booster.fit(x_train, y_train)
    boost_prediction = np.clip(np.rint(booster.predict(x_test)), 1, None)
    predictions = pd.DataFrame(
        {
            "row_index": data.iloc[test_idx]["row_index"].to_numpy(),
            "actual_fire_engine": y_test,
            "planned_personnel": x_test["planned_personnel"].to_numpy(),
            "benchmark_personnel_capacity": policy_prediction.to_numpy(),
            "boosting_personnel_only": boost_prediction,
        }
    ).sort_values("row_index").reset_index(drop=True)
    summary = {
        "evaluation_protocol": "Fixed random 80:20 split over the direct mixed fire-engine matrix. Input is planned_personnel only.",
        "training_rows": int(len(train_idx)),
        "test_rows": int(len(test_idx)),
        "benchmark_personnel_capacity": metrics(y_test, policy_prediction.to_numpy()),
        "boosting_personnel_only": metrics(y_test, boost_prediction),
        "selection": "The deterministic six-person capacity policy remains serving logic; the booster is retained for comparison and offline experiments.",
        "interpretation_guardrail": "Mixed-holdout engine metrics are dominated by deterministic synthetic policy rows and do not validate incident-level deployment accuracy.",
    }
    model = boost_pipeline(x, min_samples_leaf=12)
    model.fit(x, y)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODEL_DIR / "feature2_fire_engine_hgb_experimental.joblib"
    joblib.dump(model, artifact_path)
    artifact = {
        "artifact": str(artifact_path.relative_to(ROOT)),
        "sha256": sha256(artifact_path),
        "status": "experimental_not_for_serving",
        "training_protocol": "direct mixed matrix; planned_personnel is the only input",
    }
    return predictions, summary, artifact


def markdown_report(report: dict[str, Any]) -> str:
    person = report["personnel"]
    engine = report["fire_engine"]
    return f"""# Feature 2 modelling report

Generated: {report['created_at_utc']}

## Personnel — benchmark vs boosting

Evaluation uses a fixed 80:20 random split from the combined matrix:
{person['training_rows']} train rows and {person['test_rows']} test rows. Both
partitions remain mixed real/synthetic; provenance is neither an input nor a
split key.

| Model | MAE | RMSE | Median AE | R² |
| --- | ---: | ---: | ---: | ---: |
| Area-only Huber benchmark | {person['benchmark_area_huber']['mae']} | {person['benchmark_area_huber']['rmse']} | {person['benchmark_area_huber']['median_ae']} | {person['benchmark_area_huber']['r2']} |
| Hybrid HistGradientBoosting | {person['boosting_hybrid']['mae']} | {person['boosting_hybrid']['rmse']} | {person['boosting_hybrid']['median_ae']} | {person['boosting_hybrid']['r2']} |

The boosting artefact is a pilot planning model only. Mixed-holdout metrics
measure fit to the configured hybrid scenario distribution, not field accuracy.

## Fire-engine planning capacity — benchmark vs boosting

Evaluation uses a fixed 80:20 random split from the combined engine matrix:
{engine['training_rows']} train rows and {engine['test_rows']} test rows. The
only input is `planned_personnel`.

| Model | MAE | RMSE | Median AE | R² |
| --- | ---: | ---: | ---: | ---: |
| Capacity benchmark `ceil(planned_personnel / 6)` | {engine['benchmark_personnel_capacity']['mae']} | {engine['benchmark_personnel_capacity']['rmse']} | {engine['benchmark_personnel_capacity']['median_ae']} | {engine['benchmark_personnel_capacity']['r2']} |
| Personnel-only HistGradientBoosting | {engine['boosting_personnel_only']['mae']} | {engine['boosting_personnel_only']['rmse']} | {engine['boosting_personnel_only']['median_ae']} | {engine['boosting_personnel_only']['r2']} |

The boosting engine result is **experimental only**. The serving contract stays
with the transparent six-person capacity policy; the mixed engine test is
dominated by deterministic synthetic policy rows and cannot validate an
incident-level deployment claim.

## Guardrails

- The train matrices have no source/origin/synthetic feature.
- Source provenance remains in evaluation sidecars and is not a model feature.
- Train and test are the same deliberately mixed real/synthetic distribution.
- The engine conversion is planning capacity under a municipal staffing
  standard, not a claim that all Karhutla responders ride on engines.
"""


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    personnel_predictions, personnel_summary = mixed_personnel_evaluation()
    personnel_predictions.to_csv(REPORT_DIR / "personnel_mixed_holdout_predictions.csv", index=False)
    personnel_model = personnel_artifact()
    engine_predictions, engine_summary, engine_model = mixed_engine_evaluation()
    engine_predictions.to_csv(REPORT_DIR / "fire_engine_mixed_holdout_predictions.csv", index=False)
    report = {
        "status": "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_contract": "feature2_model_ready; direct model matrices exclude provenance/origin fields",
        "evaluation_mode": "fixed_random_mixed_holdout_80_20",
        "personnel": personnel_summary,
        "fire_engine": engine_summary,
        "artifacts": {"personnel": personnel_model, "fire_engine": engine_model},
        "deployment_decision": {
            "personnel": "pilot_planning_only",
            "fire_engine": "serve_deterministic_personnel_capacity_policy_only",
        },
    }
    (REPORT_DIR / "feature2_model_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "feature2_model_report.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
