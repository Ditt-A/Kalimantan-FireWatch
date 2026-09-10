"""Benchmark LightGBM, XGBoost, and GPU CatBoost with the project's temporal split."""
from __future__ import annotations

import gc
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.base import clone
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier, XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.config import MODELS_DIR, PROCESSED_DIR, REPORTS_DIR  # noqa: E402
from firewatch.modeling import (  # noqa: E402
    CATEGORICAL_FEATURE_COLUMNS,
    NUMERIC_FEATURE_COLUMNS,
    _best_validation_threshold,
    _classification_metrics,
    _count_metrics,
    _prepare_model_data,
    _preprocessor,
    _temporal_split,
)

LOGGER = logging.getLogger(__name__)
RANDOM_SEED = 42


def _candidates() -> dict[str, tuple[object, object, str]]:
    """Return memory-bounded CPU boosters and the requested GPU CatBoost models."""
    return {
        "lightgbm": (
            LGBMClassifier(
                objective="binary",
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=31,
                min_child_samples=30,
                colsample_bytree=0.85,
                subsample=0.85,
                class_weight="balanced",
                n_jobs=1,
                random_state=RANDOM_SEED,
                verbosity=-1,
            ),
            LGBMRegressor(
                objective="poisson",
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=31,
                min_child_samples=30,
                colsample_bytree=0.85,
                subsample=0.85,
                n_jobs=1,
                random_state=RANDOM_SEED,
                verbosity=-1,
            ),
            "cpu",
        ),
        "xgboost": (
            XGBClassifier(
                objective="binary:logistic",
                n_estimators=500,
                learning_rate=0.05,
                max_depth=8,
                min_child_weight=5,
                subsample=0.85,
                colsample_bytree=0.85,
                tree_method="hist",
                device="cpu",
                n_jobs=1,
                eval_metric="logloss",
                random_state=RANDOM_SEED,
            ),
            XGBRegressor(
                objective="count:poisson",
                n_estimators=500,
                learning_rate=0.05,
                max_depth=8,
                min_child_weight=5,
                subsample=0.85,
                colsample_bytree=0.85,
                tree_method="hist",
                device="cpu",
                n_jobs=1,
                random_state=RANDOM_SEED,
            ),
            "cpu",
        ),
        "catboost_gpu": (
            CatBoostClassifier(
                loss_function="Logloss",
                iterations=500,
                depth=8,
                learning_rate=0.05,
                auto_class_weights="Balanced",
                task_type="GPU",
                devices="0",
                random_seed=RANDOM_SEED,
                verbose=False,
                allow_writing_files=False,
            ),
            CatBoostRegressor(
                loss_function="RMSE",
                iterations=500,
                depth=8,
                learning_rate=0.05,
                task_type="GPU",
                devices="0",
                random_seed=RANDOM_SEED,
                verbose=False,
                allow_writing_files=False,
            ),
            "gpu:0",
        ),
    }


def _pipeline(model: object) -> Pipeline:
    return Pipeline([("preprocess", _preprocessor()), ("model", model)])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    fact = pd.read_parquet(PROCESSED_DIR / "fact_grid_week.parquet")
    split = _temporal_split(_prepare_model_data(fact))
    features = NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never"] + CATEGORICAL_FEATURE_COLUMNS
    x_train = split.train[features]
    y_train_class = split.train["fire_next_7d"]
    y_train_count = split.train["hotspot_next_7d"]
    x_validation = split.validation[features]
    y_validation_class = split.validation["fire_next_7d"]
    y_validation_count = split.validation["hotspot_next_7d"]
    train_validation = pd.concat([split.train, split.validation], ignore_index=True)

    rows: list[dict[str, Any]] = []
    for name, (classifier, counter, device) in _candidates().items():
        started = time.monotonic()
        LOGGER.info("Benchmarking %s classification on %s", name, device)
        try:
            fitted_classifier = _pipeline(classifier).fit(x_train, y_train_class)
            validation_probability = fitted_classifier.predict_proba(x_validation)[:, 1]
            threshold = _best_validation_threshold(y_validation_class, validation_probability)
            classification_validation = _classification_metrics(
                y_validation_class, validation_probability, threshold
            )

            LOGGER.info("Benchmarking %s count model on %s", name, device)
            fitted_counter = _pipeline(counter).fit(x_train, y_train_count)
            validation_count = np.clip(fitted_counter.predict(x_validation), 0, None)
            count_validation = _count_metrics(y_validation_count, validation_count)

            LOGGER.info("Fitting final %s models on train plus validation", name)
            final_classifier = clone(_pipeline(classifier)).fit(
                train_validation[features], train_validation["fire_next_7d"]
            )
            final_counter = clone(_pipeline(counter)).fit(
                train_validation[features], train_validation["hotspot_next_7d"]
            )
            test_probability = final_classifier.predict_proba(split.test[features])[:, 1]
            test_count = np.clip(final_counter.predict(split.test[features]), 0, None)

            joblib.dump(
                {"model": final_classifier, "features": features, "threshold": threshold, "device": device},
                MODELS_DIR / f"benchmark_{name}_classifier.joblib",
            )
            joblib.dump(
                {"model": final_counter, "features": features, "device": device},
                MODELS_DIR / f"benchmark_{name}_counter.joblib",
            )
            rows.append(
                {
                    "model": name,
                    "device": device,
                    "status": "passed",
                    "classification_threshold": threshold,
                    **{f"validation_{key}": value for key, value in classification_validation.items()},
                    **{f"validation_count_{key}": value for key, value in count_validation.items()},
                    **{
                        f"test_{key}": value
                        for key, value in _classification_metrics(
                            split.test["fire_next_7d"], test_probability, threshold
                        ).items()
                    },
                    **{
                        f"test_count_{key}": value
                        for key, value in _count_metrics(
                            split.test["hotspot_next_7d"], test_count
                        ).items()
                    },
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            )
            del fitted_classifier, fitted_counter, final_classifier, final_counter
        except Exception as exc:  # Keep the overnight run moving if one backend fails.
            LOGGER.exception("Benchmark failed for %s", name)
            rows.append(
                {
                    "model": name,
                    "device": device,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            )
        gc.collect()

    benchmark = pd.DataFrame(rows)
    benchmark.to_csv(REPORTS_DIR / "booster_benchmark.csv", index=False)
    report = {
        "status": "passed" if benchmark["status"].eq("passed").all() else "partial",
        "split_rows": {
            "train_2023_2024": int(len(split.train)),
            "validation_2025": int(len(split.validation)),
            "test_2026": int(len(split.test)),
        },
        "results": benchmark.to_dict(orient="records"),
    }
    (REPORTS_DIR / "booster_benchmark.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
