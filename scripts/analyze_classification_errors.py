"""Create a leakage-free diagnostic report for the held-out 2026 classifier errors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_recall_curve, precision_score, recall_score

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.config import PROCESSED_DIR, REPORTS_DIR  # noqa: E402


def _metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    y_true = frame["fire_next_7d"].astype(int)
    y_pred = frame["predicted_label"].astype(int)
    return {
        "rows": int(len(frame)),
        "prevalence": round(float(y_true.mean()), 5),
        "predicted_positive_rate": round(float(y_pred.mean()), 5),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 5),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 5),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 5),
        "false_positive_rate": round(float(((y_pred.eq(1)) & (y_true.eq(0))).mean()), 5),
        "false_negative_rate": round(float(((y_pred.eq(0)) & (y_true.eq(1))).mean()), 5),
    }


def _group_report(frame: pd.DataFrame, column: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for value, group in frame.groupby(column, dropna=False, observed=True):
        if len(group) < 200:
            continue
        rows.append({column: str(value), **_metrics(group)})
    return sorted(rows, key=lambda row: (-float(row["false_negative_rate"]), -int(row["rows"])))


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    frame = pd.read_parquet(PROCESSED_DIR / "prediction_grid_week.parquet")
    threshold = 0.18
    frame["predicted_label"] = frame["predicted_fire_probability"].ge(threshold).astype(int)
    frame["error_type"] = np.select(
        [
            frame["fire_next_7d"].eq(1) & frame["predicted_label"].eq(1),
            frame["fire_next_7d"].eq(0) & frame["predicted_label"].eq(1),
            frame["fire_next_7d"].eq(1) & frame["predicted_label"].eq(0),
        ],
        ["true_positive", "false_positive", "false_negative"],
        default="true_negative",
    )
    frame["peat_group"] = np.where(frame["peat_pct"].gt(0), "peat", "non_peat")
    frame["previous_fire_group"] = np.select(
        [frame["hotspot_prev_30d"].eq(0), frame["hotspot_prev_30d"].lt(5)],
        ["none", "1_to_4"],
        default="5_or_more",
    )
    precision, recall, thresholds = precision_recall_curve(
        frame["fire_next_7d"], frame["predicted_fire_probability"]
    )
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    best_index = int(np.nanargmax(f1[:-1]))
    oracle_threshold = float(thresholds[best_index])
    oracle_prediction = frame["predicted_fire_probability"].ge(oracle_threshold)

    calibration = frame.assign(
        probability_bin=pd.qcut(
            frame["predicted_fire_probability"], q=10, duplicates="drop"
        )
    ).groupby("probability_bin", observed=True).agg(
        rows=("fire_next_7d", "size"),
        observed_fire_rate=("fire_next_7d", "mean"),
        average_probability=("predicted_fire_probability", "mean"),
    )
    report = {
        "status": "passed",
        "threshold_from_2025_validation": threshold,
        "overall_test": _metrics(frame),
        "error_counts": frame["error_type"].value_counts().to_dict(),
        "test_oracle_threshold_diagnostic": round(oracle_threshold, 5),
        "test_oracle_f1_diagnostic": round(
            float(f1_score(frame["fire_next_7d"], oracle_prediction)), 5
        ),
        "by_province": _group_report(frame, "province"),
        "by_month": _group_report(frame, "week_start"),
        "by_peat": _group_report(frame, "peat_group"),
        "by_previous_fire": _group_report(frame, "previous_fire_group"),
        "probability_calibration_deciles": [
            {
                "probability_bin": str(index),
                "rows": int(row["rows"]),
                "observed_fire_rate": round(float(row["observed_fire_rate"]), 5),
                "average_probability": round(float(row["average_probability"]), 5),
            }
            for index, row in calibration.iterrows()
        ],
    }
    (REPORTS_DIR / "classification_error_analysis.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
