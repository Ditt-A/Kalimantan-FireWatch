"""Temporal model training and prediction export for Kalimantan FireWatch."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression, PoissonRegressor
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    mean_absolute_error,
    mean_poisson_deviance,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, StandardScaler

from firewatch.config import MODELS_DIR, PROCESSED_DIR, REPORTS_DIR

LOGGER = logging.getLogger(__name__)

NUMERIC_FEATURE_COLUMNS = [
    "hotspot_prev_7d",
    "hotspot_prev_14d",
    "hotspot_prev_30d",
    "hotspot_density_prev_30d",
    "neighbour_hotspot_prev_7d",
    "neighbour_hotspot_prev_30d",
    "district_hotspot_prev_7d",
    "district_hotspot_prev_30d",
    "province_hotspot_prev_7d",
    "province_hotspot_prev_30d",
    "seasonal_grid_hotspot_mean_prior",
    "avg_frp_prev_30d",
    "days_since_last_hotspot",
    "rainfall_7d",
    "rainfall_14d",
    "rainfall_30d",
    "rainfall_60d",
    "rainfall_30d_anomaly",
    "rainfall_7_to_30_ratio",
    "temperature_mean_7d",
    "temperature_mean_30d",
    "temperature_max_7d",
    "soil_moisture_7d",
    "soil_moisture_30d",
    "dry_days_30d",
    "dry_days_60d",
    "peat_pct",
    "peat_dryness_index",
    "mapbiomas_lc_natural_share_pct_prev_year",
    "mapbiomas_lc_anthropic_share_pct_prev_year",
    "mapbiomas_lc_peat_swamp_share_pct_prev_year",
    "mapbiomas_lc_oil_palm_share_pct_prev_year",
    "mapbiomas_lc_pulpwood_share_pct_prev_year",
    "mapbiomas_lc_agriculture_share_pct_prev_year",
    "mapbiomas_lc_water_share_pct_prev_year",
    "mapbiomas_burned_share_pct_prev_year",
    "mapbiomas_prev_year_available",
    "month_sin",
    "month_cos",
    "week_sin",
    "week_cos",
]
CATEGORICAL_FEATURE_COLUMNS = ["landcover_code_2022"]
FEATURE_COLUMNS = NUMERIC_FEATURE_COLUMNS
MODEL_REQUIRED_COLUMNS = ["hotspot_prev_30d", "rainfall_60d"]



@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def _prepare_model_data(fact: pd.DataFrame) -> pd.DataFrame:
    data = fact.copy()
    data["week_start"] = pd.to_datetime(data["week_start"])
    missing = sorted(set(NUMERIC_FEATURE_COLUMNS + CATEGORICAL_FEATURE_COLUMNS) - set(data.columns))
    if missing:
        raise ValueError(f"Model table misses engineered features: {missing}")
    data = data.dropna(subset=MODEL_REQUIRED_COLUMNS).copy()
    data["days_since_last_hotspot_never"] = data["days_since_last_hotspot"].isna().astype(int)
    data["days_since_last_hotspot"] = data["days_since_last_hotspot"].fillna(999.0)
    data["landcover_code_2022"] = data["landcover_code_2022"].fillna("unknown").astype(str)
    return data


def _temporal_split(data: pd.DataFrame) -> TemporalSplit:
    # Keep the final test year untouched while extending the historical
    # training window with the newly ingested 2023 observations.
    train = data.loc[data["year"].isin([2023, 2024])].copy()
    validation = data.loc[data["year"] == 2025].copy()
    test = data.loc[data["year"] == 2026].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("Temporal split is empty; check source coverage and model eligibility.")
    for name, part in (("train", train), ("validation", validation), ("test", test)):
        if part["fire_next_7d"].nunique() < 2:
            raise ValueError(f"{name} split has only one classification target class.")
    return TemporalSplit(train=train, validation=validation, test=test)


def _classification_metrics(y_true: pd.Series, probability: np.ndarray, threshold: float) -> dict[str, float]:
    prediction = (probability >= threshold).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "pr_auc": float(average_precision_score(y_true, probability)),
        "precision": float(precision_score(y_true, prediction, zero_division=0)),
        "recall": float(recall_score(y_true, prediction, zero_division=0)),
        "f1": float(f1_score(y_true, prediction, zero_division=0)),
    }


def _best_validation_threshold(y_true: pd.Series, probability: np.ndarray) -> float:
    candidates = np.linspace(0.05, 0.95, 91)
    scores = [f1_score(y_true, probability >= candidate, zero_division=0) for candidate in candidates]
    return float(candidates[int(np.argmax(scores))])


def _count_metrics(y_true: pd.Series, prediction: np.ndarray) -> dict[str, float]:
    prediction = np.clip(prediction, 1e-6, None)
    return {
        "mae": float(mean_absolute_error(y_true, prediction)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, prediction))),
        "poisson_deviance": float(mean_poisson_deviance(y_true, prediction)),
    }


def _preprocessor() -> ColumnTransformer:
    """Impute numeric features and one-hot encode static land-cover codes."""
    numeric = Pipeline(
        [("imputer", SimpleImputer(strategy="median", add_indicator=True))]
    )
    categorical = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", numeric, NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never"]),
            ("categorical", categorical, CATEGORICAL_FEATURE_COLUMNS),
        ],
        sparse_threshold=0,
    )


def _classification_candidates() -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline(
            [
                ("preprocess", _preprocessor()),
                ("scaler", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=42,
                    ),
                ),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("preprocess", _preprocessor()),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=220,
                        max_depth=20,
                        min_samples_leaf=3,
                        max_samples=0.75,
                        class_weight="balanced_subsample",
                        n_jobs=1,
                        random_state=42,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("preprocess", _preprocessor()),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.06,
                        max_iter=250,
                        max_leaf_nodes=31,
                        min_samples_leaf=30,
                        l2_regularization=1.0,
                        random_state=42,
                    ),
                ),
            ]
        ),
    }


def _count_candidates() -> dict[str, Pipeline]:
    return {
        "poisson_regression": Pipeline(
            [
                ("preprocess", _preprocessor()),
                ("scale", MinMaxScaler()),
                ("model", PoissonRegressor(alpha=0.05, max_iter=500)),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("preprocess", _preprocessor()),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        learning_rate=0.06,
                        max_iter=300,
                        max_leaf_nodes=31,
                        l2_regularization=1.0,
                        random_state=42,
                    ),
                ),
            ]
        ),
    }


def _feature_importance(
    model: Pipeline,
    data: pd.DataFrame,
    target: pd.Series,
    features: list[str],
) -> pd.DataFrame:
    """Return a reproducible feature-importance report for the selected model.

    Tree ensembles expose native importances over transformed columns. For
    histogram gradient boosting, permutation importance measures the decrease in
    PR-AUC on an unseen temporal test sample, so the report stays meaningful for
    the selected estimator and the imputed/one-hot preprocessing is evaluated as
    part of the pipeline.
    """
    estimator = model.named_steps["model"]
    if hasattr(estimator, "feature_importances_") or hasattr(estimator, "coef_"):
        transformed_names = model.named_steps["preprocess"].get_feature_names_out()
        values = (
            estimator.feature_importances_
            if hasattr(estimator, "feature_importances_")
            else np.abs(estimator.coef_).ravel()
        )
        importance = pd.DataFrame(
            {
                "feature": transformed_names,
                "importance": values,
                "importance_std": np.nan,
                "method": "native_model_importance",
            }
        )
    else:
        sample = data.sample(n=min(25_000, len(data)), random_state=42)
        result = permutation_importance(
            model,
            sample,
            target.loc[sample.index],
            scoring="average_precision",
            n_repeats=3,
            n_jobs=-1,
            random_state=42,
        )
        importance = pd.DataFrame(
            {
                "feature": features,
                "importance": result.importances_mean,
                "importance_std": result.importances_std,
                "method": "permutation_pr_auc_test",
            }
        )
    return importance.sort_values("importance", ascending=False).reset_index(drop=True)


def _quantiles(values: pd.Series) -> dict[str, float]:
    return {
        "moderate": float(values.quantile(0.50)),
        "high": float(values.quantile(0.75)),
        "very_high": float(values.quantile(0.90)),
    }


def _risk_category(probability: pd.Series, thresholds: dict[str, float]) -> pd.Series:
    return pd.Series(
        np.select(
            [
                probability >= thresholds["very_high"],
                probability >= thresholds["high"],
                probability >= thresholds["moderate"],
            ],
            ["Very High", "High", "Moderate"],
            default="Low",
        ),
        index=probability.index,
    )


def _count_category(prediction: pd.Series, thresholds: dict[str, float]) -> pd.Series:
    return pd.Series(
        np.select(
            [
                prediction >= thresholds["very_high"],
                prediction >= thresholds["high"],
                prediction >= thresholds["moderate"],
            ],
            ["Very High", "High", "Medium"],
            default="Zero/Low",
        ),
        index=prediction.index,
    )


def _priority_category(
    probability: pd.Series,
    prediction: pd.Series,
    risk_thresholds: dict[str, float],
    count_thresholds: dict[str, float],
) -> pd.Series:
    critical = (probability >= risk_thresholds["very_high"]) & (
        prediction >= count_thresholds["very_high"]
    )
    high = (probability >= risk_thresholds["high"]) & (prediction >= count_thresholds["high"])
    watch = (probability >= risk_thresholds["high"]) | (prediction >= count_thresholds["high"])
    return pd.Series(
        np.select([critical, high, watch], ["Critical", "High", "Watch"], default="Low"),
        index=probability.index,
    )


def train_and_predict() -> dict[str, Any]:
    """Train required model pairs with temporal validation, then export test predictions."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    fact = pd.read_parquet(PROCESSED_DIR / "fact_grid_week.parquet")
    data = _prepare_model_data(fact)
    split = _temporal_split(data)
    features = NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never"] + CATEGORICAL_FEATURE_COLUMNS

    x_train = split.train[features]
    y_train_class = split.train["fire_next_7d"]
    y_train_count = split.train["hotspot_next_7d"]
    x_validation = split.validation[features]
    y_validation_class = split.validation["fire_next_7d"]
    y_validation_count = split.validation["hotspot_next_7d"]

    classification_rows: list[dict[str, Any]] = []
    fitted_classification: dict[str, Pipeline] = {}
    for name, model in _classification_candidates().items():
        LOGGER.info("Training classification candidate: %s", name)
        fitted = model.fit(x_train, y_train_class)
        probability = fitted.predict_proba(x_validation)[:, 1]
        threshold = _best_validation_threshold(y_validation_class, probability)
        classification_rows.append(
            {"model": name, "threshold": threshold, **_classification_metrics(y_validation_class, probability, threshold)}
        )
        fitted_classification[name] = fitted
    classification_comparison = pd.DataFrame(classification_rows).sort_values(
        ["pr_auc", "recall"], ascending=False
    )
    best_classification_name = str(classification_comparison.iloc[0]["model"])
    best_threshold = float(classification_comparison.iloc[0]["threshold"])

    count_rows: list[dict[str, Any]] = []
    fitted_count: dict[str, Pipeline] = {}
    for name, model in _count_candidates().items():
        LOGGER.info("Training count candidate: %s", name)
        fitted = model.fit(x_train, y_train_count)
        prediction = np.clip(fitted.predict(x_validation), 0, None)
        count_rows.append({"model": name, **_count_metrics(y_validation_count, prediction)})
        fitted_count[name] = fitted
    count_comparison = pd.DataFrame(count_rows).sort_values(["mae", "poisson_deviance"])
    best_count_name = str(count_comparison.iloc[0]["model"])

    train_validation = pd.concat([split.train, split.validation], ignore_index=True)
    final_classifier = clone(_classification_candidates()[best_classification_name]).fit(
        train_validation[features], train_validation["fire_next_7d"]
    )
    final_counter = clone(_count_candidates()[best_count_name]).fit(
        train_validation[features], train_validation["hotspot_next_7d"]
    )

    test_probability = final_classifier.predict_proba(split.test[features])[:, 1]
    test_count = np.clip(final_counter.predict(split.test[features]), 0, None)
    test_classification_metrics = _classification_metrics(
        split.test["fire_next_7d"], test_probability, best_threshold
    )
    test_count_metrics = _count_metrics(split.test["hotspot_next_7d"], test_count)

    validation_probability = fitted_classification[best_classification_name].predict_proba(
        x_validation
    )[:, 1]
    validation_count = np.clip(fitted_count[best_count_name].predict(x_validation), 0, None)
    risk_thresholds = _quantiles(pd.Series(validation_probability))
    count_thresholds = _quantiles(pd.Series(validation_count))

    prediction_columns = [
        "grid_id",
        "week_start",
        "province",
        "district",
        "centroid_lat",
        "centroid_lon",
        "peat_pct",
        "rainfall_30d",
        "hotspot_prev_30d",
        "fire_next_7d",
        "hotspot_next_7d",
    ]
    predictions = split.test[prediction_columns].copy()
    predictions["predicted_fire_probability"] = test_probability
    predictions["predicted_hotspot_count_next_7d"] = test_count
    predictions["risk_category"] = _risk_category(
        predictions["predicted_fire_probability"], risk_thresholds
    )
    predictions["count_category"] = _count_category(
        predictions["predicted_hotspot_count_next_7d"], count_thresholds
    )
    predictions["priority_category"] = _priority_category(
        predictions["predicted_fire_probability"],
        predictions["predicted_hotspot_count_next_7d"],
        risk_thresholds,
        count_thresholds,
    )
    predictions.to_parquet(PROCESSED_DIR / "prediction_grid_week.parquet", index=False)

    classification_comparison.to_csv(REPORTS_DIR / "classification_validation_metrics.csv", index=False)
    count_comparison.to_csv(REPORTS_DIR / "count_validation_metrics.csv", index=False)
    importance = _feature_importance(
        final_classifier,
        split.test[features],
        split.test["fire_next_7d"],
        features,
    )
    importance.to_csv(REPORTS_DIR / "classification_feature_importance.csv", index=False)

    joblib.dump(
        {
            "model": final_classifier,
            "features": features,
            "threshold": best_threshold,
            "validation_thresholds": risk_thresholds,
        },
        MODELS_DIR / "fire_occurrence_classifier.joblib",
    )
    joblib.dump(
        {
            "model": final_counter,
            "features": features,
            "validation_thresholds": count_thresholds,
        },
        MODELS_DIR / "hotspot_count_model.joblib",
    )

    report: dict[str, Any] = {
        "status": "passed",
        "split_rows": {
            "train_2023_2024": int(len(split.train)),
            "validation_2025": int(len(split.validation)),
            "test_2026": int(len(split.test)),
        },
        "selected_models": {
            "classification": best_classification_name,
            "count": best_count_name,
        },
        "classification_test_metrics": test_classification_metrics,
        "count_test_metrics": test_count_metrics,
        "classification_threshold": best_threshold,
        "prediction_rows": int(len(predictions)),
    }
    (REPORTS_DIR / "model_evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
