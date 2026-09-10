"""Validate provenance and label semantics for supplementary vehicle evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence.parquet"
MANIFEST_PATH = ROOT / "data/raw/feature2_vehicle_evidence/acquisition_manifest.json"
REPORT_PATH = ROOT / "data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence_validation.json"


def main() -> None:
    frame = pd.read_parquet(DATA_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    sources = {row["source_id"]: row for row in manifest["documents"]}
    errors: list[str] = []
    if frame.empty:
        errors.append("Dataset is empty")
    if not frame["province"].str.startswith("Kalimantan").all():
        errors.append("Non-Kalimantan record found")
    if frame["evidence_id"].duplicated().any():
        errors.append("Duplicate evidence_id")
    if frame["source_url"].isna().any() or frame["source_sha256"].isna().any():
        errors.append("Missing source URL or SHA-256")
    for subtype in [
        "fire_engine_count_explicit_positive", "water_tanker_count_explicit_positive",
        "pickup_count_explicit_positive",
    ]:
        observed = pd.to_numeric(frame[subtype], errors="coerce").dropna()
        if (observed <= 0).any():
            errors.append(f"{subtype} contains a zero/negative label")
    exact = frame["personnel_total"].notna()
    if not frame.loc[exact, "personnel_total"].eq(frame.loc[exact, "personnel_known_lower_bound"]).all():
        errors.append("Exact personnel total must equal its lower bound")
    visible_count = frame[[
        "fire_engine_count_explicit_positive", "water_tanker_count_explicit_positive",
        "pickup_count_explicit_positive", "other_explicit_vehicle_count",
    ]].fillna(0).sum(axis=1)
    if (frame["documented_vehicle_lower_bound"] < visible_count).any():
        errors.append("Documented vehicle lower bound is less than visible subtype counts")
    for row in frame.itertuples(index=False):
        try:
            provenance = json.loads(row.source_field_provenance_json)
        except json.JSONDecodeError:
            errors.append(f"Malformed field provenance: {row.evidence_id}")
            continue
        if not provenance:
            errors.append(f"Empty field provenance: {row.evidence_id}")
        source = sources.get(row.source_id)
        if source is None or source.get("status") not in {"verified_content", "verified_cached_content"}:
            errors.append(f"Unverified source: {row.evidence_id}")
            continue
        artifact = ROOT / source["local_path"]
        if not artifact.exists():
            errors.append(f"Missing artifact: {row.evidence_id}")
        elif hashlib.sha256(artifact.read_bytes()).hexdigest() != row.source_sha256:
            errors.append(f"Artifact hash mismatch: {row.evidence_id}")
    report = {
        "status": "passed" if not errors else "failed",
        "records": int(len(frame)),
        "source_records": int(frame["source_id"].nunique()),
        "vehicle_positive_counts": {
            "fire_engine": int(frame["fire_engine_count_explicit_positive"].notna().sum()),
            "water_tanker": int(frame["water_tanker_count_explicit_positive"].notna().sum()),
            "pickup": int(frame["pickup_count_explicit_positive"].notna().sum()),
        },
        "errors": errors,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
