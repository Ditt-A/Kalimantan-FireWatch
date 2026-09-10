"""Create honest Feature-2 labels from the page-verified Kalteng corpus.

The source reports list mixed ground assets (fire engines, tankers, pickups,
R4/R2, TOSA, boats, etc.).  This script prevents the common but incorrect
conversion of that mixed count into a "number of fire engines" target.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = (
    ROOT
    / "data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment_expanded.parquet"
)
INPUT_VALIDATION_PATH = (
    ROOT / "data/processed/kalteng_historical_deployments/expanded_validation_report.json"
)
OUTPUT_DIR = ROOT / "data/processed/feature2_response_planning"


def soil_class(value: object) -> str:
    text = str(value or "").lower()
    if "gambut" in text:
        return "peat"
    if "mineral" in text:
        return "mineral"
    return "unknown_not_reported"


def positive_observed(series: pd.Series) -> pd.Series:
    """Keep only positive explicit subtype observations; zero is not absence."""
    values = pd.to_numeric(series, errors="coerce").astype("Int64")
    return values.where(values > 0, pd.NA).astype("Int64")


def main() -> None:
    validation = json.loads(INPUT_VALIDATION_PATH.read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise RuntimeError("Build the page-verified expanded corpus successfully before deriving Feature-2 labels")
    source = pd.read_parquet(INPUT_PATH).copy()
    required = {
        "incident_id", "province", "area_ha", "soil_type", "fire_type", "vegetation",
        "water_source", "latitude", "longitude", "coordinate_source_note", "personnel_total", "vehicle_total", "fire_engine_count",
        "water_tanker_count", "pickup_count", "vehicle_r4_other_count",
        "vehicle_r2_motor_count", "patrol_car_count", "tosa_count", "trail_count",
        "other_vehicle_count", "resource_mapping_confidence", "source_document_id",
        "source_document_url_retrieved", "source_document_sha256", "source_page",
        "source_field_provenance_json", "record_sha256",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise RuntimeError(f"Expanded source is missing required columns: {missing}")
    if not source["province"].eq("Kalimantan Tengah").all():
        raise RuntimeError("Feature-2 local cohort must remain Kalteng-only")

    numeric_vehicle = pd.to_numeric(source["vehicle_total"], errors="coerce").astype("Int64")
    strict = (
        source["resource_mapping_confidence"].eq("high")
        & source["personnel_total"].notna()
        & numeric_vehicle.fillna(0).gt(0)
    )
    cohort = source.loc[strict].copy()
    cohort["soil_class_source"] = cohort["soil_type"].map(soil_class)
    cohort["area_ha"] = pd.to_numeric(cohort["area_ha"], errors="coerce")
    cohort["personnel_total"] = pd.to_numeric(cohort["personnel_total"], errors="coerce").astype("Int64")
    cohort["documented_ground_response_vehicle_total"] = numeric_vehicle.loc[strict].astype("Int64")
    cohort["personnel_label_status"] = "exact_incident_level_source_count"
    cohort["ground_response_vehicle_label_status"] = "documented_incident_resource_block_sum"
    cohort["area_label_status"] = cohort["area_ha"].notna().map(
        {True: "source_reported", False: "not_reported"}
    )
    cohort["soil_label_status"] = cohort["soil_class_source"].eq("unknown_not_reported").map(
        {True: "not_reported", False: "source_reported"}
    )

    # The explicit subtype fields use positive observations only.  Do not make
    # a zero-label training example out of a PDF which merely did not list that
    # subtype.  The lower-bound field is useful in case display, not as a
    # fire-engine target.
    for source_column, output_column in (
        ("fire_engine_count", "fire_engine_count_explicit_positive"),
        ("water_tanker_count", "water_tanker_count_explicit_positive"),
        ("pickup_count", "pickup_count_explicit_positive"),
        ("vehicle_r4_other_count", "r4_count_explicit_positive"),
        ("vehicle_r2_motor_count", "r2_motor_count_explicit_positive"),
        ("patrol_car_count", "patrol_car_count_explicit_positive"),
        ("tosa_count", "tosa_count_explicit_positive"),
        ("trail_count", "trail_count_explicit_positive"),
        ("other_vehicle_count", "other_vehicle_count_explicit_positive"),
    ):
        cohort[output_column] = positive_observed(cohort[source_column])

    dedicated = (
        cohort["fire_engine_count_explicit_positive"].fillna(0)
        + cohort["water_tanker_count_explicit_positive"].fillna(0)
        + cohort["pickup_count_explicit_positive"].fillna(0)
    ).astype("Int64")
    cohort["documented_dedicated_suppression_vehicle_lower_bound"] = dedicated.where(
        dedicated > 0, pd.NA
    ).astype("Int64")
    cohort["dedicated_suppression_vehicle_label_status"] = cohort[
        "documented_dedicated_suppression_vehicle_lower_bound"
    ].notna().map({True: "positive_lower_bound_only", False: "not_explicitly_reported"})
    cohort["eligible_personnel_area_calibration"] = cohort["area_ha"].notna()
    cohort["eligible_ground_vehicle_area_calibration"] = cohort["area_ha"].notna()

    keep = [
        "incident_id", "source_report_year", "province", "district", "location", "area_ha",
        "area_label_status", "soil_type", "soil_class_source", "soil_label_status", "fire_type",
        "vegetation", "water_source", "latitude", "longitude", "coordinate_source_note", "personnel_total", "personnel_label_status",
        "documented_ground_response_vehicle_total", "ground_response_vehicle_label_status",
        "fire_engine_count_explicit_positive", "water_tanker_count_explicit_positive",
        "pickup_count_explicit_positive", "r4_count_explicit_positive",
        "r2_motor_count_explicit_positive", "patrol_car_count_explicit_positive",
        "tosa_count_explicit_positive", "trail_count_explicit_positive",
        "other_vehicle_count_explicit_positive",
        "documented_dedicated_suppression_vehicle_lower_bound",
        "dedicated_suppression_vehicle_label_status", "eligible_personnel_area_calibration",
        "eligible_ground_vehicle_area_calibration", "resource_mapping_confidence",
        "source_document_id", "source_document_url_retrieved", "source_document_sha256",
        "source_page", "source_field_provenance_json", "record_sha256",
    ]
    cohort = cohort[keep].sort_values("incident_id").reset_index(drop=True)
    if len(cohort) < 50:
        raise RuntimeError("Feature-2 cohort must retain at least 50 HIGH full-resource incidents")
    if cohort["source_document_sha256"].isna().any() or cohort["source_page"].isna().any():
        raise RuntimeError("Feature-2 cohort lost source provenance")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = OUTPUT_DIR / "feature2_response_planning_cohort.parquet"
    csv_path = OUTPUT_DIR / "feature2_response_planning_cohort.csv"
    report_path = OUTPUT_DIR / "feature2_data_readiness.json"
    cohort.to_parquet(parquet_path, index=False)
    cohort.to_csv(csv_path, index=False)
    report = {
        "status": "passed",
        "scope": "Kalimantan Tengah, page-verified historical Karhutla deployment incidents",
        "source_corpus_rows": int(len(source)),
        "feature2_high_full_resource_rows": int(len(cohort)),
        "source_documents_used": int(cohort["source_document_id"].nunique()),
        "unique_source_pdf_hashes": int(cohort["source_document_sha256"].nunique()),
        "personnel_target": {
            "exact_incident_level_rows": int(cohort["personnel_total"].notna().sum()),
            "rows_with_source_reported_area": int(cohort["eligible_personnel_area_calibration"].sum()),
            "rows_with_source_reported_area_and_soil": int(
                (cohort["eligible_personnel_area_calibration"] & cohort["soil_label_status"].eq("source_reported")).sum()
            ),
            "rows_with_source_reported_coordinates": int(
                (cohort["latitude"].notna() & cohort["longitude"].notna()).sum()
            ),
            "rows_with_source_reported_fire_type": int(cohort["fire_type"].notna().sum()),
            "rows_with_source_reported_water_source": int(cohort["water_source"].notna().sum()),
            "permitted_use": "small case-based or shrinkage calibration only; not standalone dispatch ML",
        },
        "ground_response_vehicle_target": {
            "documented_incident_level_rows": int(cohort["documented_ground_response_vehicle_total"].notna().sum()),
            "rows_with_source_reported_area": int(cohort["eligible_ground_vehicle_area_calibration"].sum()),
            "definition": "sum of ground vehicles documented in the incident resource block; may include R2, R4, TOSA, boats, and other source-named vehicles",
            "permitted_use": "case-based planning range only, retaining vehicle-type breakdown",
        },
        "dedicated_vehicle_subtype_coverage": {
            "fire_engine_positive_observations": int(cohort["fire_engine_count_explicit_positive"].notna().sum()),
            "water_tanker_positive_observations": int(cohort["water_tanker_count_explicit_positive"].notna().sum()),
            "pickup_positive_observations": int(cohort["pickup_count_explicit_positive"].notna().sum()),
            "critical_restriction": "A missing subtype is unknown, not zero. Fire-engine-only modeling is prohibited because only positive reports are available and coverage is sparse.",
        },
        "soil_context_distribution": cohort["soil_class_source"].value_counts().to_dict(),
        "provenance_contract": "Every row retains official PPID URL, locally verified PDF SHA-256, source page, field provenance JSON, and record SHA-256.",
        "not_allowed": [
            "Call documented_ground_response_vehicle_total a fire-engine count",
            "Treat an unreported fire_engine_count as zero",
            "Use this Kalteng 2018-2019 cohort as a national dispatch standard",
            "Infer incident area from FIRMS point counts",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
