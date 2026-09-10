"""Validate provenance and spatial scope of the local Kalteng deployment data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment.parquet"
MANIFEST = ROOT / "data/raw/kalteng_historical_deployments/source_documents_manifest.json"
BOUNDARY = ROOT / "data/raw/boundaries/idn_adm1.geojson"
REPORT = ROOT / "data/processed/kalteng_historical_deployments/validation_report.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    data = pd.read_parquet(DATA)
    required = {
        "incident_id", "province", "area_ha", "resource_mapping_confidence", "source_document_id",
        "source_document_url_original", "source_document_url_retrieved", "source_document_sha256",
        "source_page", "source_field_provenance_json", "training_eligible_personnel", "training_eligible_vehicle",
        "latitude", "longitude",
    }
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing required provenance fields: {sorted(missing)}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sources = {item["source_document_id"]: item for item in manifest["documents"]}
    errors: list[str] = []
    if data.empty or data["incident_id"].duplicated().any():
        errors.append("Dataset is empty or incident IDs are duplicated")
    if not data["province"].eq("Kalimantan Tengah").all():
        errors.append("Dataset has rows outside Kalimantan Tengah")
    if data["source_page"].isna().any():
        errors.append("At least one workbook row lacks a page-verified citation")
    if (~data["source_document_id"].isin(sources)).any():
        errors.append("A row references a source document outside the manifest")
    for source_id in data["source_document_id"].unique():
        source = sources.get(source_id)
        if source is None:
            continue
        if source.get("status") != "verified_pdf":
            errors.append(f"Source {source_id} is not a verified PDF")
            continue
        local = ROOT / str(source["local_pdf_path"])
        if not local.exists() or digest(local) != source.get("sha256"):
            errors.append(f"Source {source_id} local PDF hash does not match manifest")
    invalid_field_provenance = 0
    for raw in data["source_field_provenance_json"]:
        try:
            parsed = json.loads(raw)
            invalid_field_provenance += not isinstance(parsed, dict)
        except (TypeError, json.JSONDecodeError):
            invalid_field_provenance += 1
    if invalid_field_provenance:
        errors.append(f"{invalid_field_provenance} rows have invalid field provenance JSON")
    point_rows = data.dropna(subset=["latitude", "longitude"])
    central = gpd.read_file(BOUNDARY).query("shapeName == 'Central Kalimantan'").to_crs("EPSG:4326")
    points = gpd.GeoDataFrame(point_rows, geometry=[Point(xy) for xy in zip(point_rows.longitude, point_rows.latitude)], crs="EPSG:4326")
    outside = 0 if points.empty else int((~points.geometry.apply(lambda point: central.geometry.iloc[0].covers(point))).sum())
    if outside:
        errors.append(f"{outside} sourced coordinates lie outside Central Kalimantan ADM1")
    if int(data["training_eligible_personnel"].sum()) >= 20 or int(data["training_eligible_vehicle"].sum()) >= 20:
        errors.append("Training eligibility unexpectedly implies a sufficiently large corpus; review threshold policy")
    report = {
        "status": "passed" if not errors else "failed",
        "rows": len(data),
        "source_documents": int(data["source_document_id"].nunique()),
        "page_verified_rows": int(data["source_page"].notna().sum()),
        "coordinate_rows": len(points),
        "coordinate_rows_outside_Central_Kalimantan": outside,
        "training_eligible_personnel_rows": int(data["training_eligible_personnel"].sum()),
        "training_eligible_vehicle_rows": int(data["training_eligible_vehicle"].sum()),
        "errors": errors,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
