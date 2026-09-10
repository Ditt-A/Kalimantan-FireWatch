"""Validate that Feature-2 targets retain their intended non-fabricated semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data/processed/feature2_response_planning/feature2_response_planning_cohort.parquet"
READINESS_PATH = ROOT / "data/processed/feature2_response_planning/feature2_data_readiness.json"
REPORT_PATH = ROOT / "data/processed/feature2_response_planning/feature2_cohort_validation.json"
POSITIVE_SUBTYPE_COLUMNS = (
    "fire_engine_count_explicit_positive",
    "water_tanker_count_explicit_positive",
    "pickup_count_explicit_positive",
    "r4_count_explicit_positive",
    "r2_motor_count_explicit_positive",
    "patrol_car_count_explicit_positive",
    "tosa_count_explicit_positive",
    "trail_count_explicit_positive",
    "other_vehicle_count_explicit_positive",
)


def main() -> None:
    readiness = json.loads(READINESS_PATH.read_text(encoding="utf-8"))
    df = pd.read_parquet(DATASET_PATH)
    errors: list[str] = []
    if readiness.get("status") != "passed":
        errors.append("source readiness report is not passed")
    if len(df) < 50:
        errors.append("Feature-2 cohort has fewer than 50 HIGH full-resource incidents")
    if not df["province"].eq("Kalimantan Tengah").all():
        errors.append("cohort contains a non-Kalteng row")
    if not df["resource_mapping_confidence"].eq("high").all():
        errors.append("cohort contains a non-HIGH mapping")
    if df["personnel_total"].isna().any() or (df["personnel_total"] <= 0).any():
        errors.append("personnel target is missing or non-positive")
    if df["documented_ground_response_vehicle_total"].isna().any() or (
        df["documented_ground_response_vehicle_total"] <= 0
    ).any():
        errors.append("documented ground-vehicle target is missing or non-positive")
    if df["source_document_url_retrieved"].isna().any() or df["source_document_sha256"].isna().any() or df["source_page"].isna().any():
        errors.append("cohort has a missing source URL, SHA-256, or page")
    if df["incident_id"].duplicated().any():
        errors.append("cohort has duplicate incident IDs")
    for column in POSITIVE_SUBTYPE_COLUMNS:
        values = pd.to_numeric(df[column], errors="coerce")
        if (values.dropna() <= 0).any():
            errors.append(f"{column} contains a fake zero/negative subtype label")
    expected_lower_bound = (
        df["fire_engine_count_explicit_positive"].fillna(0)
        + df["water_tanker_count_explicit_positive"].fillna(0)
        + df["pickup_count_explicit_positive"].fillna(0)
    )
    actual_lower_bound = df["documented_dedicated_suppression_vehicle_lower_bound"].fillna(0)
    if not expected_lower_bound.astype("Int64").equals(actual_lower_bound.astype("Int64")):
        errors.append("dedicated-vehicle lower bound is inconsistent with positive subtype observations")
    coordinate_halves = df["latitude"].notna() ^ df["longitude"].notna()
    if coordinate_halves.any():
        errors.append("a row has only one coordinate component")

    report = {
        "status": "passed" if not errors else "failed",
        "rows": int(len(df)),
        "high_full_resource_rows": int(
            (df["personnel_total"].notna() & df["documented_ground_response_vehicle_total"].notna()).sum()
        ),
        "source_document_count": int(df["source_document_id"].nunique()),
        "positive_fire_engine_label_count": int(df["fire_engine_count_explicit_positive"].notna().sum()),
        "zero_subtype_labels_forbidden": True,
        "errors": errors,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
