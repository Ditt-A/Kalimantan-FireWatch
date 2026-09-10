"""Validate and publish the reviewed Kalteng historical resource sample.

The input workbook is an analyst-reviewed extraction from PPID source reports.
This script preserves it unchanged, verifies retrievable official PDF copies,
and publishes a normalized table with row- and field-level provenance. It does
not use OCR guesses as incident/resource labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data/raw/kalteng_historical_deployments"
DOC_DIR = RAW_DIR / "documents"
INTERIM_DIR = ROOT / "data/interim/kalteng_historical_deployments"
OUT_DIR = ROOT / "data/processed/kalteng_historical_deployments"
CITATIONS_PATH = INTERIM_DIR / "page_verified_citations.csv"

REQUIRED_COLUMNS = {
    "report_year", "province", "kabupaten_kota", "location", "area_ha", "soil_type", "fire_type",
    "vegetation", "water_source", "personnel_total", "personnel_count_quality", "pump_shibaura",
    "pump_robin", "pump_mark3", "pump_ninja", "pump_mugen", "other_pump_count", "other_pump_desc",
    "pickup", "water_tanker", "fire_engine", "patrol_car", "r4_other", "r2_motor", "tosa", "trail",
    "deployment_scope", "data_confidence", "source_url", "source_note",
}
NUMERIC_COLUMNS = {
    "report_year", "area_ha", "personnel_total", "pump_shibaura", "pump_robin", "pump_mark3",
    "pump_ninja", "pump_mugen", "other_pump_count", "pickup", "water_tanker", "fire_engine",
    "patrol_car", "r4_other", "r2_motor", "tosa", "trail", "hose_rolls", "nozzle_count",
}
RESOURCE_COLUMNS = {
    "pump_shibaura", "pump_robin", "pump_mark3", "pump_ninja", "pump_mugen", "other_pump_count",
    "pickup", "water_tanker", "fire_engine", "patrol_car", "r4_other", "r2_motor", "tosa", "trail",
}
SOURCE_HOST = "ppid.kalteng.go.id"
MIRROR_HOST = "ppid.kemendagri.go.id"
REPORT_PERIODS = {
    "ppid_kalteng_Wi5wQA8ONbMlvTAd": ("2019-08-01", "2019-08-02"),
    "ppid_kalteng_bHWbq6q2Flw8guor": ("2019-07-31", "2019-08-01"),
    "ppid_kalteng_objwVKtPvhRjjzyQ": ("2019-08-08", "2019-08-09"),
    "ppid_kalteng_i7OiC0kNgsLGR6MJ": ("2018-08-07", "2018-08-08"),
    "ppid_kalteng_vUvNFEM4KOkROrFH": ("2018-08-05", "2018-08-06"),
}


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def document_id(url: str) -> str:
    stem = Path(url.split("?", 1)[0]).stem
    return f"ppid_kalteng_{stem[:16]}"


def mirror_url(url: str) -> str:
    if SOURCE_HOST not in url:
        raise ValueError(f"Unexpected PPID source host: {url}")
    return url.replace(SOURCE_HOST, MIRROR_HOST)


def verify_document(http: requests.Session, original_url: str) -> dict[str, object]:
    identifier = document_id(original_url)
    retrieved_url = mirror_url(original_url)
    target = DOC_DIR / f"{identifier}.pdf"
    text_target = INTERIM_DIR / "source_text" / f"{identifier}.txt"
    record: dict[str, object] = {
        "source_document_id": identifier,
        "source_document_url_original": original_url,
        "source_document_url_retrieved": retrieved_url,
        "publisher": "PPID Kementerian Dalam Negeri (retrieved official copy)",
        "originating_agency": "BPBPK/Pusdalops Provinsi Kalimantan Tengah (as stated in report)",
        "local_pdf_path": str(target.relative_to(ROOT)),
        "local_extracted_text_path": str(text_target.relative_to(ROOT)),
    }
    try:
        if target.exists() and target.stat().st_size:
            payload = target.read_bytes()
        else:
            response = http.get(retrieved_url, timeout=120)
            response.raise_for_status()
            payload = response.content
            target.write_bytes(payload)
            record["http_status"] = response.status_code
        if not payload.startswith(b"%PDF-"):
            raise ValueError("retrieved body is not a PDF")
        pdf = PdfReader(target)
        if text_target.exists() and text_target.stat().st_size:
            page_count = len(pdf.pages)
            text_bearing_page_count = sum(
                line.startswith("=== PAGE ") for line in text_target.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        else:
            pages = [page.extract_text() or "" for page in pdf.pages]
            text_target.parent.mkdir(parents=True, exist_ok=True)
            text_target.write_text(
                "\n\n".join(f"=== PAGE {number} ===\n{text}" for number, text in enumerate(pages, start=1)),
                encoding="utf-8",
            )
            page_count = len(pages)
            text_bearing_page_count = sum(bool(text.strip()) for text in pages)
        record.update({
            "status": "verified_pdf",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "page_count": page_count,
            "text_bearing_page_count": text_bearing_page_count,
        })
    except Exception as error:
        record.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
    return record


def clean_text(value: object) -> object:
    if pd.isna(value):
        return pd.NA
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result if result else pd.NA


def source_fields(row: pd.Series, source: dict[str, object]) -> str:
    # The workbook specifies the report but not a page number. This is explicit
    # so future review can add page-level citations without overwriting values.
    fields = {}
    for name in REQUIRED_COLUMNS - {"source_url", "source_note"}:
        value = row.get(name)
        if pd.notna(value):
            fields[name] = {
                "source_document_id": source["source_document_id"],
                "source_url_original": source["source_document_url_original"],
                "source_page": None,
                "provenance_status": "workbook-reviewed; page citation pending",
            }
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path, help="Path to kalteng_karhutla_resource_deployment_initial.xlsx")
    args = parser.parse_args()
    if not args.workbook.is_file():
        raise FileNotFoundError(args.workbook)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    workbook_copy = RAW_DIR / args.workbook.name
    shutil.copy2(args.workbook, workbook_copy)

    input_data = pd.read_excel(args.workbook, sheet_name="Incidents", dtype=object)
    missing = REQUIRED_COLUMNS - set(input_data.columns)
    if missing:
        raise ValueError(f"Workbook is missing required columns: {sorted(missing)}")
    data = input_data.copy()
    for column in data.columns:
        if column not in NUMERIC_COLUMNS:
            data[column] = data[column].map(clean_text)
    for column in NUMERIC_COLUMNS:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
            if (data[column].dropna() < 0).any():
                raise ValueError(f"Negative value in {column}")
    if data.empty or data["source_url"].isna().any() or data["location"].isna().any():
        raise ValueError("Each observation needs source_url and location")
    if not data["province"].eq("Kalimantan Tengah").all():
        raise ValueError("This local source dataset must contain only Kalimantan Tengah rows")
    if (~data["report_year"].isin([2018, 2019])).any():
        raise ValueError("Unexpected report_year; review source before publishing")
    if (data["area_ha"].dropna() <= 0).any():
        raise ValueError("area_ha must be positive when present")
    if ~data["data_confidence"].str.lower().isin({"high", "medium", "low"}).all():
        raise ValueError("data_confidence must be high, medium, or low")

    http = requests.Session()
    http.headers["User-Agent"] = "Kalimantan-FireWatch-research/1.0"
    sources = {url: verify_document(http, url) for url in sorted(data["source_url"].unique())}
    (RAW_DIR / "source_documents_manifest.json").write_text(json.dumps({
        "acquired_at_utc": datetime.now(UTC).isoformat(),
        "source_policy": "Preserve submitted workbook and retrieve only official government PPID PDF copies.",
        "documents": list(sources.values()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    failed_sources = [record for record in sources.values() if record["status"] != "verified_pdf"]
    if failed_sources:
        raise RuntimeError(f"Could not verify {len(failed_sources)} source PDFs; dataset not published")

    data.insert(0, "incident_id", [f"kalteng_{int(year)}_{index:03d}" for index, year in enumerate(data["report_year"], start=1)])
    data = data.rename(columns={
        "report_year": "source_report_year",
        "kabupaten_kota": "district",
        "pickup": "pickup_count",
        "water_tanker": "water_tanker_count",
        "fire_engine": "fire_engine_count",
        "patrol_car": "patrol_car_count",
        "r4_other": "vehicle_r4_other_count",
        "r2_motor": "vehicle_r2_motor_count",
        "tosa": "tosa_count",
        "trail": "trail_count",
        "source_url": "source_document_url_original",
        "source_note": "source_excerpt",
        "data_confidence": "resource_mapping_confidence",
    })
    data["source_document_id"] = data["source_document_url_original"].map(lambda url: sources[url]["source_document_id"])
    data["source_document_url_retrieved"] = data["source_document_url_original"].map(lambda url: sources[url]["source_document_url_retrieved"])
    data["source_document_sha256"] = data["source_document_url_original"].map(lambda url: sources[url]["sha256"])
    data["source_document_status"] = "verified_pdf"
    data["report_period_start"] = data["source_document_id"].map(lambda key: REPORT_PERIODS[key][0])
    data["report_period_end"] = data["source_document_id"].map(lambda key: REPORT_PERIODS[key][1])
    data["incident_date"] = pd.NA
    data["source_page"] = pd.NA
    data["latitude"] = pd.NA
    data["longitude"] = pd.NA
    data["coordinate_source_note"] = pd.NA
    if CITATIONS_PATH.exists():
        citations = pd.read_csv(CITATIONS_PATH, dtype=str, keep_default_na=False)
        expected_citation_columns = {"incident_id", "source_page", "latitude", "longitude", "coordinate_source_note", "incident_date"}
        if expected_citation_columns - set(citations.columns):
            raise ValueError(f"Citation file missing {sorted(expected_citation_columns - set(citations.columns))}")
        if citations["incident_id"].duplicated().any() or (~citations["incident_id"].isin(data["incident_id"])).any():
            raise ValueError("Citation file must have unique incident IDs that exist in the workbook output")
        citations = citations.replace("", pd.NA).set_index("incident_id")
        for column in ("source_page", "latitude", "longitude", "coordinate_source_note", "incident_date"):
            data[column] = data["incident_id"].map(citations[column])
    data["latitude"] = pd.to_numeric(data["latitude"], errors="coerce")
    data["longitude"] = pd.to_numeric(data["longitude"], errors="coerce")
    if ((data["latitude"].notna()) & ~data["latitude"].between(-90, 90)).any() or ((data["longitude"].notna()) & ~data["longitude"].between(-180, 180)).any():
        raise ValueError("Citation coordinates must be WGS84 decimal degrees")
    data["incident_date"] = pd.to_datetime(data["incident_date"], errors="coerce").dt.date
    data["source_page_status"] = data["source_page"].notna().map({
        True: "page-verified against retained official PDF",
        False: "not supplied by reviewed workbook; page-level source review pending",
    })
    data["source_workbook_file"] = workbook_copy.name
    data["source_workbook_sha256"] = digest(workbook_copy)
    data["pump_total_reported"] = data[["pump_shibaura", "pump_robin", "pump_mark3", "pump_ninja", "pump_mugen", "other_pump_count"]].sum(axis=1, min_count=1)
    data["vehicle_total_reported"] = data[["pickup_count", "water_tanker_count", "fire_engine_count", "patrol_car_count", "vehicle_r4_other_count", "vehicle_r2_motor_count", "tosa_count", "trail_count"]].sum(axis=1, min_count=1)
    data["source_field_provenance_json"] = [source_fields(input_data.iloc[index], sources[input_data.iloc[index]["source_url"]]) for index in range(len(data))]
    data["training_eligible_personnel"] = (
        data["resource_mapping_confidence"].str.lower().eq("high")
        & data["deployment_scope"].eq("incident")
        & data["personnel_count_quality"].eq("exact")
        & data["personnel_total"].notna()
    )
    vehicle_fields = ["pickup_count", "water_tanker_count", "fire_engine_count", "patrol_car_count", "vehicle_r4_other_count", "vehicle_r2_motor_count", "tosa_count", "trail_count"]
    data["training_eligible_vehicle"] = (
        data["resource_mapping_confidence"].str.lower().eq("high")
        & data["deployment_scope"].eq("incident")
        & data[vehicle_fields].notna().any(axis=1)
    )
    data["dataset_row_source"] = "analyst-reviewed initial workbook linked to verified PPID PDF"
    data["record_sha256"] = data.apply(
        lambda row: hashlib.sha256("|".join("" if pd.isna(value) else str(value) for value in row).encode()).hexdigest(),
        axis=1,
    )
    output_parquet = OUT_DIR / "kalteng_historical_resource_deployment.parquet"
    output_csv = OUT_DIR / "kalteng_historical_resource_deployment.csv"
    data.to_parquet(output_parquet, index=False)
    data.to_csv(output_csv, index=False)
    quality = {
        "status": "passed",
        "rows": len(data),
        "province_values": sorted(data["province"].dropna().unique().tolist()),
        "source_document_count": len(sources),
        "source_documents_verified": len(sources) - len(failed_sources),
        "resource_mapping_confidence": data["resource_mapping_confidence"].str.lower().value_counts().to_dict(),
        "exact_personnel_rows": int(data["personnel_count_quality"].eq("exact").sum()),
        "training_eligible_personnel_rows": int(data["training_eligible_personnel"].sum()),
        "training_eligible_vehicle_rows": int(data["training_eligible_vehicle"].sum()),
        "row_level_and_field_level_source_columns": True,
        "not_a_dispatch_standard": True,
        "not_sufficient_for_predictive_model": True,
        "reason": "The verified local sample is a feasibility/calibration corpus; expand page-verified HIGH labels before fitting a model.",
    }
    (OUT_DIR / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(quality, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
