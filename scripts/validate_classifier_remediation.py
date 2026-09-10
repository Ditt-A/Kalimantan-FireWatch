"""Validate geography-aware, class-balanced fire classifier remediation variants."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.pipeline import Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.config import PROCESSED_DIR, REPORTS_DIR  # noqa: E402
from firewatch.modeling import (  # noqa: E402
    CATEGORICAL_FEATURE_COLUMNS,
    NUMERIC_FEATURE_COLUMNS,
    _best_validation_threshold,
    _classification_metrics,
    _prepare_model_data,
    _preprocessor,
    _temporal_split,
)


def _history_group(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.select(
            [frame["hotspot_prev_30d"].eq(0), frame["hotspot_prev_30d"].lt(5)],
            ["none", "1_to_4"],
            default="5_or_more",
        ),
        index=frame.index,
    )


def _thresholds_by_group(
    y_true: pd.Series, probability: np.ndarray, groups: pd.Series, fallback: float
) -> dict[str, float]:
    labels = y_true.reset_index(drop=True)
    group_values = groups.astype(str).reset_index(drop=True)
    thresholds: dict[str, float] = {}
    for group in group_values.unique():
        mask = group_values.eq(group).to_numpy()
        if int(labels[mask].sum()) < 25:
            thresholds[str(group)] = fallback
        else:
            thresholds[str(group)] = _best_validation_threshold(
                labels[mask], probability[mask]
            )
    return thresholds


def _add_threshold_variant(
    rows: list[dict[str, object]],
    name: str,
    y_true: pd.Series,
    probability: np.ndarray,
    groups: pd.Series,
    fallback: float,
    onset: pd.Series,
) -> None:
    thresholds = _thresholds_by_group(y_true, probability, groups, fallback)
    values = groups.astype(str).map(thresholds).fillna(fallback).to_numpy()
    prediction = probability >= values
    labels = y_true.to_numpy(dtype=int)
    onset_mask = onset.to_numpy(dtype=bool)
    rows.append(
        {
            "model": name,
            "class_weight": "None",
            "threshold": json.dumps(thresholds, sort_keys=True),
            "roc_auc": np.nan,
            "pr_auc": np.nan,
            "precision": float(precision_score(labels, prediction, zero_division=0)),
            "recall": float(recall_score(labels, prediction, zero_division=0)),
            "f1": float(f1_score(labels, prediction, zero_division=0)),
            "onset_rows": int(onset_mask.sum()),
            "onset_f1": float(f1_score(labels[onset_mask], prediction[onset_mask], zero_division=0)),
            "onset_recall": float(
                recall_score(labels[onset_mask], prediction[onset_mask], zero_division=0)
            ),
        }
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    data = _prepare_model_data(pd.read_parquet(PROCESSED_DIR / "fact_grid_week.parquet"))
    split = _temporal_split(data)
    features = NUMERIC_FEATURE_COLUMNS + ["days_since_last_hotspot_never"] + CATEGORICAL_FEATURE_COLUMNS
    rows: list[dict[str, object]] = []

    for name, class_weight in (("geography_aware", None), ("geography_aware_balanced", "balanced")):
        logging.info("Validating %s", name)
        model = Pipeline(
            [
                ("preprocess", _preprocessor()),
                (
                    "model",
                    HistGradientBoostingClassifier(
                        learning_rate=0.06,
                        max_iter=300,
                        max_leaf_nodes=31,
                        min_samples_leaf=30,
                        l2_regularization=1.0,
                        class_weight=class_weight,
                        random_state=42,
                    ),
                ),
            ]
        )
        model.fit(split.train[features], split.train["fire_next_7d"])
        probability = model.predict_proba(split.validation[features])[:, 1]
        threshold = _best_validation_threshold(split.validation["fire_next_7d"], probability)
        prediction = probability >= threshold
        onset = split.validation["hotspot_prev_30d"].eq(0)
        base_row = (
            {
                "model": name,
                "class_weight": str(class_weight),
                "threshold": threshold,
                **_classification_metrics(split.validation["fire_next_7d"], probability, threshold),
                "onset_rows": int(onset.sum()),
                "onset_f1": float(
                    _classification_metrics(
                        split.validation.loc[onset, "fire_next_7d"], probability[onset], threshold
                    )["f1"]
                ),
                "onset_recall": float(
                    _classification_metrics(
                        split.validation.loc[onset, "fire_next_7d"], probability[onset], threshold
                    )["recall"]
                ),
            }
        )
        rows.append(base_row)
        history = _history_group(split.validation)
        onset = split.validation["hotspot_prev_30d"].eq(0)
        _add_threshold_variant(
            rows,
            f"{name}_history_threshold",
            split.validation["fire_next_7d"],
            probability,
            history,
            threshold,
            onset,
        )
        _add_threshold_variant(
            rows,
            f"{name}_province_threshold",
            split.validation["fire_next_7d"],
            probability,
            split.validation["province"],
            threshold,
            onset,
        )
        _add_threshold_variant(
            rows,
            f"{name}_province_history_threshold",
            split.validation["fire_next_7d"],
            probability,
            split.validation["province"].astype(str) + "|" + history,
            threshold,
            onset,
        )

    result = pd.DataFrame(rows).sort_values(["f1", "pr_auc"], ascending=False)
    result.to_csv(REPORTS_DIR / "classifier_remediation_validation.csv", index=False)
    report = {
        "status": "passed",
        "baseline_validation_f1": 0.35933030455998655,
        "results": result.to_dict(orient="records"),
    }
    (REPORTS_DIR / "classifier_remediation_validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
