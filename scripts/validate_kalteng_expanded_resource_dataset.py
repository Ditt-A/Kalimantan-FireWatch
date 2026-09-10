"""Fail closed when the Indonesian resource-deployment corpus loses provenance.

This validates the published 74-row Kalteng expansion against locally retained,
hash-verified official PPID PDFs.  It is deliberately a data-admission check,
not a claim that the records constitute a dispatch standard.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from build_kalteng_expanded_resource_dataset import OUT_DIR, load_document_metadata


DATASET_PATH = OUT_DIR / "kalteng_historical_resource_deployment_expanded.parquet"
REPORT_PATH = OUT_DIR / "expanded_validation_report.json"
PAGE_PATTERN = re.compile(r"^(\d+)(?:-(\d+))?$")


def main() -> None:
    df = pd.read_parquet(DATASET_PATH)
    errors: list[str] = []
    required = {
        "incident_id", "province", "personnel_total", "vehicle_total",
        "resource_mapping_confidence", "source_document_id",
        "source_document_url_retrieved", "source_document_sha256", "source_page",
        "source_field_provenance_json", "record_sha256",
    }
    missing_columns = sorted(required - set(df.columns))
    if missing_columns:
        errors.append(f"missing required columns: {missing_columns}")

    if len(df) < 50:
        errors.append(f"only {len(df)} rows; expected at least 50")
    if df["incident_id"].duplicated().any():
        errors.append("duplicate incident_id values")
    if not df["province"].eq("Kalimantan Tengah").all():
        errors.append("dataset contains a non-Kalimantan Tengah row")
    for column in ("source_document_url_retrieved", "source_document_sha256", "source_page", "record_sha256"):
        if df[column].isna().any() or (df[column].astype("string").str.len() == 0).any():
            errors.append(f"missing {column} values")

    metadata = load_document_metadata()  # Re-hashes every PDF before returning.
    unknown_documents = sorted(set(df["source_document_id"]) - set(metadata))
    if unknown_documents:
        errors.append(f"documents not present in verified manifests: {unknown_documents}")

    pages_checked = 0
    provenance_json_checked = 0
    for row in df.itertuples(index=False):
        document = metadata.get(row.source_document_id)
        if not document:
            continue
        if row.source_document_sha256 != document["sha256"]:
            errors.append(f"SHA mismatch in output for {row.incident_id}")
        if row.source_document_url_retrieved != document["source_document_url"]:
            errors.append(f"source URL mismatch in output for {row.incident_id}")
        match = PAGE_PATTERN.fullmatch(str(row.source_page))
        if not match:
            errors.append(f"invalid page reference for {row.incident_id}: {row.source_page!r}")
        else:
            start, end = int(match.group(1)), int(match.group(2) or match.group(1))
            if start < 1 or end < start or end > int(document["page_count"]):
                errors.append(f"out-of-range page reference for {row.incident_id}: {row.source_page}")
            pages_checked += 1
        try:
            json.loads(row.source_field_provenance_json)
            provenance_json_checked += 1
        except (TypeError, json.JSONDecodeError):
            errors.append(f"invalid field provenance JSON for {row.incident_id}")

    numeric_vehicle = pd.to_numeric(df["vehicle_total"], errors="coerce").fillna(0).gt(0)
    strict_full = (
        df["personnel_total"].notna()
        & numeric_vehicle
        & df["resource_mapping_confidence"].eq("high")
    )
    if int(strict_full.sum()) < 50:
        errors.append("fewer than 50 HIGH rows have both numeric personnel and vehicle labels")

    report = {
        "status": "passed" if not errors else "failed",
        "dataset": str(DATASET_PATH.relative_to(OUT_DIR.parents[1])),
        "rows": int(len(df)),
        "all_rows_kalimantan_tengah": bool(df["province"].eq("Kalimantan Tengah").all()),
        "verified_source_documents_available": int(len(metadata)),
        "source_documents_used": int(df["source_document_id"].nunique()),
        "page_references_checked": pages_checked,
        "field_provenance_json_checked": provenance_json_checked,
        "numeric_personnel_and_vehicle_rows": int((df["personnel_total"].notna() & numeric_vehicle).sum()),
        "high_confidence_personnel_and_vehicle_rows": int(strict_full.sum()),
        "admission_standard": "Each row requires official source URL, locally re-hashed PDF, valid source page, and parseable field-level provenance. HIGH means the incident's own block provides numeric personnel and/or vehicle values; the 50-row cohort has both.",
        "errors": errors,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
