"""Select a fire-alert threshold from rolling temporal validation windows."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.config import MODELS_DIR, PROCESSED_DIR, REPORTS_DIR  # noqa: E402
from firewatch.modeling import (  # noqa: E402
    CATEGORICAL_FEATURE_COLUMNS,
    NUMERIC_FEATURE_COLUMNS,
    _best_validation_threshold,
    _classification_metrics,
    _prepare_model_data,
    _preprocessor,
)


def _model() -> Pipeline:
    return Pipeline(
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
    )


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    data = _prepare_model_data(pd.read_parquet(PROCESSED_DIR / "fact_grid_week.parquet"))
    features = NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never"] + CATEGORICAL_FEATURE_COLUMNS
    folds = [(2023, 2024), ((2023, 2024), 2025)]
    fold_rows: list[dict[str, float | int | str]] = []
    thresholds: list[float] = []

    for train_years, validation_year in folds:
        years = (train_years,) if isinstance(train_years, int) else train_years
        train = data.loc[data["year"].isin(years)]
        validation = data.loc[data["year"] == validation_year]
        fitted = _model().fit(train[features], train["fire_next_7d"])
        probability = fitted.predict_proba(validation[features])[:, 1]
        threshold = _best_validation_threshold(validation["fire_next_7d"], probability)
        thresholds.append(threshold)
        fold_rows.append(
            {
                "train_years": "+".join(map(str, years)),
                "validation_year": validation_year,
                "threshold": threshold,
                **_classification_metrics(validation["fire_next_7d"], probability, threshold),
            }
        )

    recommended_threshold = float(np.median(thresholds))
    train_validation = data.loc[data["year"].isin([2023, 2024, 2025])]
    test = data.loc[data["year"] == 2026]
    final_model = _model().fit(
        train_validation[features], train_validation["fire_next_7d"]
    )
    test_probability = final_model.predict_proba(test[features])[:, 1]
    test_metrics = _classification_metrics(
        test["fire_next_7d"], test_probability, recommended_threshold
    )
    report = {
        "status": "passed",
        "validation_folds": fold_rows,
        "recommended_threshold": recommended_threshold,
        "test_metrics_diagnostic": test_metrics,
    }
    (REPORTS_DIR / "classification_threshold_stability.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    joblib.dump(
        {"model": final_model, "features": features, "threshold": recommended_threshold},
        MODELS_DIR / "fire_occurrence_classifier_threshold_stable.joblib",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
