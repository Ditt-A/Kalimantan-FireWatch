"""Audit real/synthetic holdout errors and leakage indicators without retraining."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/processed/feature2_model_ready"
HYBRID_MANIFEST = ROOT / "data/processed/feature2_hybrid_training/synthetic_generation_manifest.json"
REPORT_DIR = ROOT / "reports/feature2_modeling"
SEED = 20260908


def reconstruct_origins(target: str) -> pd.DataFrame:
    """Recover origin solely for audit from the separately retained partitions.

    The returned origin is never joined to a model matrix.  It is recoverable
    because the direct training file was built deterministically from the two
    retained partitions with a documented seed.
    """
    real = pd.read_parquet(DATA_DIR / "real" / f"{target}.parquet").copy()
    synthetic = pd.read_parquet(DATA_DIR / "synthetic" / f"{target}.parquet").copy()
    real["_audit_origin"] = "real"
    synthetic["_audit_origin"] = "synthetic"
    seed = SEED + (1 if target == "personnel" else 2)
    reconstructed = pd.concat([real, synthetic], ignore_index=True).sample(
        frac=1.0, random_state=seed
    ).reset_index(drop=True)
    observed = pd.read_parquet(DATA_DIR / "training" / f"{target}.parquet")
    compare = reconstructed.drop(columns="_audit_origin")
    try:
        pd.testing.assert_frame_equal(compare, observed, check_dtype=True, check_exact=True)
    except AssertionError as exc:
        raise RuntimeError(f"Could not reproduce {target} direct training order: {exc}") from exc
    return reconstructed


def prediction_origin_frame(target: str) -> pd.DataFrame:
    prediction_path = REPORT_DIR / f"{target}_mixed_holdout_predictions.csv"
    prediction = pd.read_csv(prediction_path)
    reconstructed = reconstruct_origins(target)
    _, expected_test_idx = train_test_split(
        np.arange(len(reconstructed)), test_size=0.20, random_state=SEED, shuffle=True
    )
    if set(prediction["row_index"].astype(int)) != set(expected_test_idx):
        raise RuntimeError(f"{target}: prediction row indices do not match the documented holdout split")
    audit = prediction.merge(
        reconstructed.assign(row_index=np.arange(len(reconstructed)))[["row_index", "_audit_origin"]],
        on="row_index",
        how="left",
        validate="1:1",
    )
    return audit


def mae_by_origin(frame: pd.DataFrame, actual: str, prediction_columns: list[str]) -> dict[str, dict[str, dict[str, float | int]]]:
    result: dict[str, dict[str, dict[str, float | int]]] = {}
    for prediction in prediction_columns:
        result[prediction] = {}
        for origin, subset in frame.groupby("_audit_origin", sort=True):
            result[prediction][origin] = {
                "test_rows": int(len(subset)),
                "mae": round(float((subset[actual] - subset[prediction]).abs().mean()), 3),
            }
    return result


def personnel_proxy_indicators() -> dict[str, dict[str, float | bool]]:
    data = reconstruct_origins("personnel")
    result: dict[str, dict[str, float | bool]] = {}
    categorical_unknown = ["fwi_class", "water_access_class", "operational_complexity_level"]
    numeric_missing = [
        "water_access_index_relative_0_100",
        "nearest_permanent_surface_water_km",
    ]
    for column in categorical_unknown:
        real_rate = float(data.loc[data["_audit_origin"].eq("real"), column].eq("unknown").mean())
        synthetic_rate = float(data.loc[data["_audit_origin"].eq("synthetic"), column].eq("unknown").mean())
        result[column] = {
            "real_unknown_rate": round(real_rate, 3),
            "synthetic_unknown_rate": round(synthetic_rate, 3),
            "perfect_origin_separator": bool({real_rate, synthetic_rate} == {0.0, 1.0}),
        }
    for column in numeric_missing:
        real_rate = float(data.loc[data["_audit_origin"].eq("real"), column].isna().mean())
        synthetic_rate = float(data.loc[data["_audit_origin"].eq("synthetic"), column].isna().mean())
        result[column] = {
            "real_missing_rate": round(real_rate, 3),
            "synthetic_missing_rate": round(synthetic_rate, 3),
            "perfect_origin_separator": bool({real_rate, synthetic_rate} == {0.0, 1.0}),
        }
    return result


def engine_target_alignment() -> dict[str, object]:
    data = reconstruct_origins("fire_engine")
    expected = np.ceil(data["planned_personnel"] / 6).astype(int)
    target = data["target_value"].astype(int)
    alignment = data.assign(_policy_match=expected.eq(target)).groupby("_audit_origin").agg(
        rows=("target_value", "size"),
        policy_match_rate=("_policy_match", "mean"),
    )
    return {
        origin: {
            "rows": int(row["rows"]),
            "policy_match_rate": round(float(row["policy_match_rate"]), 3),
        }
        for origin, row in alignment.iterrows()
    }


def markdown(report: dict[str, object]) -> str:
    personnel = report["personnel"]
    engine = report["fire_engine"]
    proxies = report["leakage_indicators"]["personnel_origin_proxies"]
    p_rows = "\n".join(
        f"| {model} | {origin} | {value['test_rows']} | {value['mae']} |"
        for model, by_origin in personnel["mae_by_test_origin"].items()
        for origin, value in by_origin.items()
    )
    e_rows = "\n".join(
        f"| {model} | {origin} | {value['test_rows']} | {value['mae']} |"
        for model, by_origin in engine["mae_by_test_origin"].items()
        for origin, value in by_origin.items()
    )
    proxy_rows = "\n".join(
        f"| {feature} | {values.get('real_unknown_rate', values.get('real_missing_rate'))} | {values.get('synthetic_unknown_rate', values.get('synthetic_missing_rate'))} | {values['perfect_origin_separator']} |"
        for feature, values in proxies.items()
    )
    return f"""# Feature 2 mixed-holdout origin and leakage audit

This audit reuses the existing holdout prediction files; it does not retrain a
model. Origin is reconstructed only in this audit from the separately retained
real/synthetic partitions and the documented shuffle/split seeds.

## MAE by actual test-row origin

### Personnel

| Prediction | Test origin | Rows | MAE |
| --- | --- | ---: | ---: |
{p_rows}

### Fire engine

| Prediction | Test origin | Rows | MAE |
| --- | --- | ---: | ---: |
{e_rows}

## Leakage / optimism indicators

| Feature | Real unknown/missing | Synthetic unknown/missing | Perfect origin separator? |
| --- | ---: | ---: | --- |
{proxy_rows}

- No explicit origin/source field exists in the direct model matrices.
- The listed feature patterns nevertheless reveal origin perfectly when their
  real and synthetic rates are 1.0 and 0.0. This is an **indirect origin
  proxy**, so random mixed-holdout results can be optimistic.
- Synthetic personnel rows were globally calibrated from all 43 local real
  personnel labels before the random split. Therefore labels from real rows
  later assigned to test influenced the synthetic generator used in train and
  test. This is preparation leakage for a field-accuracy claim.
- The U.S. reference features are valid external priors, but they also help
  construct synthetic personnel targets. Their inclusion makes it easier for
  boosting to reconstruct the synthetic teacher rule.
- {report['leakage_indicators']['engine_policy_alignment_note']}

Conclusion: the mixed evaluation is correct for checking the configured hybrid
pipeline, but its aggregate score must not be described as a real-only field
accuracy estimate.
"""


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    personnel = prediction_origin_frame("personnel")
    engine = prediction_origin_frame("fire_engine")
    personnel.to_csv(REPORT_DIR / "personnel_mixed_holdout_predictions_audited.csv", index=False)
    engine.to_csv(REPORT_DIR / "fire_engine_mixed_holdout_predictions_audited.csv", index=False)
    hybrid_manifest = json.loads(HYBRID_MANIFEST.read_text(encoding="utf-8"))
    report = {
        "status": "passed",
        "audit_scope": "Existing fixed random mixed-holdout prediction files only; no retraining.",
        "personnel": {
            "mae_by_test_origin": mae_by_origin(
                personnel,
                "actual_personnel",
                ["benchmark_area_huber", "boosting_hybrid"],
            ),
        },
        "fire_engine": {
            "mae_by_test_origin": mae_by_origin(
                engine,
                "actual_fire_engine",
                ["benchmark_personnel_capacity", "boosting_personnel_only"],
            ),
        },
        "leakage_indicators": {
            "explicit_origin_feature_in_direct_matrix": False,
            "personnel_origin_proxies": personnel_proxy_indicators(),
            "synthetic_global_calibration": {
                "uses_all_local_personnel_rows": hybrid_manifest["local_personnel_calibration"]["real_rows"],
                "risk": "Real labels later sampled into the mixed test set influenced synthetic label construction before splitting.",
            },
            "synthetic_target_teacher_features": [
                "us_peak_personnel_median",
                "us_peak_personnel_p90",
                "operational_complexity_level",
            ],
            "engine_policy_alignment": engine_target_alignment(),
            "engine_policy_alignment_note": "All synthetic fire-engine labels are generated by the same ceil(planned_personnel / 6) policy that the benchmark evaluates, so near-zero synthetic benchmark error is expected and is not learned field evidence.",
        },
    }
    (REPORT_DIR / "feature2_mixed_holdout_origin_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "feature2_mixed_holdout_origin_audit.md").write_text(
        markdown(report), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
