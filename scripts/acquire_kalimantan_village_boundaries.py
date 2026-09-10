#!/usr/bin/env python3
"""Acquire village/urban-village boundaries for the five Indonesian Kalimantan provinces.

The source API returns the complete Kelurahan/Desa hierarchy and geometry for
one province at a time. Both unmodified responses (gzip JSON) and a GeoParquet
analysis layer are retained so the derived layer is auditable.

This is a community-published delivery endpoint, not an authoritative
Kemendagri/BIG boundary service. It is appropriate for MVP localisation and
must be reconciled with the responsible local government before an operational
dispatch decision or public boundary claim is made.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import requests
from shapely import from_wkt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "boundaries" / "kelurahan_desa_geoit"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed" / "boundaries"
API_URL = "https://server.geoit.dev/vendor/batas-admin/download.php"
LANDING_PAGE = "https://batas-admin.geoit.dev/"
UPSTREAM_REPOSITORY = "https://github.com/Alf-Anas/batas-administrasi-indonesia"
PROVINCES = {
    "61": "Kalimantan Barat",
    "62": "Kalimantan Tengah",
    "63": "Kalimantan Selatan",
    "64": "Kalimantan Timur",
    "65": "Kalimantan Utara",
}
KODE_KD_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{4}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def raw_path(raw_dir: Path, province_code: str) -> Path:
    return raw_dir / f"kelurahan_desa_{province_code}.json.gz"


def fetch_province(province: str) -> tuple[list[dict[str, object]], str]:
    response = requests.get(
        API_URL,
        params={"fid": province, "column": "provinsi", "table": "kel_desa"},
        headers={"User-Agent": "FIRELINE/1.0 (Kalimantan MVP boundary acquisition)"},
        timeout=(30, 600),
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data")
    if payload.get("code") != 200 or not isinstance(records, list) or not records:
        raise RuntimeError(f"{province}: API returned no Kelurahan/Desa records")
    return records, response.url


def load_or_fetch(raw_dir: Path, code: str, province: str, force: bool) -> dict[str, object]:
    source_file = raw_path(raw_dir, code)
    if source_file.is_file() and source_file.stat().st_size > 1024 and not force:
        with gzip.open(source_file, "rt", encoding="utf-8") as source:
            records = json.load(source)
        origin_url = "cached local response"
        status = "cached"
    else:
        records, origin_url = fetch_province(province)
        raw_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(source_file, "wt", encoding="utf-8", compresslevel=6) as destination:
            json.dump(records, destination, ensure_ascii=False, separators=(",", ":"))
        status = "downloaded"

    if not records:
        raise RuntimeError(f"{code}: source file is empty")
    observed_codes = {str(record.get("kode_prov", "")) for record in records}
    if observed_codes != {code}:
        raise RuntimeError(f"{code}: unexpected kode_prov values: {sorted(observed_codes)}")
    return {
        "province_code": code,
        "province": province,
        "source_file": source_file.name,
        "source_url": origin_url,
        "status": status,
        "records": records,
        "rows": len(records),
        "bytes": source_file.stat().st_size,
        "sha256": sha256(source_file),
    }


def to_geodataframe(records: list[dict[str, object]]) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        geometry_wkt = record.get("WKT_GEOMETRY")
        if not isinstance(geometry_wkt, str) or not geometry_wkt:
            raise RuntimeError(f"Missing WKT_GEOMETRY for kode_kd={record.get('kode_kd')!r}")
        row = {key: value for key, value in record.items() if key != "WKT_GEOMETRY"}
        row["geometry"] = from_wkt(geometry_wkt)
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def validate(frame: gpd.GeoDataFrame) -> dict[str, object]:
    required = ["kode_prov", "kode_kk", "kode_kec", "kode_kd", "provinsi", "kel_desa"]
    nulls = {column: int(frame[column].isna().sum()) for column in required}
    invalid_geometries = int((~frame.geometry.is_valid).sum())
    empty_geometries = int(frame.geometry.is_empty.sum())
    duplicate_kode_kd = int(frame["kode_kd"].duplicated().sum())
    province_counts = {
        province: int(count)
        for province, count in frame.groupby("provinsi", dropna=False).size().items()
    }
    validation = {
        "status": "passed",
        "rows": int(len(frame)),
        "province_counts": province_counts,
        "nulls_required_fields": nulls,
        "duplicate_kode_kd": duplicate_kode_kd,
        "invalid_geometries": invalid_geometries,
        "empty_geometries": empty_geometries,
        "crs": str(frame.crs),
    }
    if (
        len(frame) == 0
        or set(frame["kode_prov"].astype(str)) != set(PROVINCES)
        or any(nulls.values())
        or duplicate_kode_kd
        or invalid_geometries
        or empty_geometries
    ):
        validation["status"] = "failed"
        raise RuntimeError(json.dumps(validation, ensure_ascii=False))
    return validation


def is_usable_village_boundary(row: object) -> bool:
    """Exclude source artefacts that cannot be assigned to a named Kel/Desa."""
    required_values = (row.kode_kk, row.kode_kec, row.kode_kd, row.kel_desa)
    return all(isinstance(value, str) and value.strip() for value in required_values) and bool(
        KODE_KD_PATTERN.fullmatch(row.kode_kd)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--force", action="store_true", help="re-download cached source responses")
    args = parser.parse_args()

    raw_dir = args.raw_dir.resolve()
    processed_dir = args.processed_dir.resolve()
    acquired = [
        load_or_fetch(raw_dir, code, province, args.force)
        for code, province in PROVINCES.items()
    ]
    records = [record for result in acquired for record in result.pop("records")]
    source_frame = to_geodataframe(records)
    usable_mask = source_frame.apply(is_usable_village_boundary, axis=1)
    frame = source_frame.loc[usable_mask].copy()
    quarantined = source_frame.loc[~usable_mask].copy()
    validation = validate(frame)
    validation["source_rows"] = int(len(source_frame))
    validation["quarantined_non_village_rows"] = int(len(quarantined))

    processed_dir.mkdir(parents=True, exist_ok=True)
    output = processed_dir / "kelurahan_desa_kalimantan.parquet"
    frame.to_parquet(output, index=False)
    if not quarantined.empty:
        quarantined.to_parquet(
            processed_dir / "kelurahan_desa_kalimantan_quarantined.parquet", index=False
        )
    validation["file"] = output.name
    validation["bytes"] = output.stat().st_size
    validation["sha256"] = sha256(output)

    provenance = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_name": "Batas Administrasi Indonesia (GeoIT delivery API)",
        "landing_page": LANDING_PAGE,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "api_endpoint": API_URL,
        "coverage": "Kelurahan/Desa in Kalimantan Barat, Tengah, Selatan, Timur, and Utara",
        "crs": "EPSG:4326",
        "license": "No explicit machine-readable licence located; retain source attribution and verify reuse terms.",
        "authority_status": "Community-published derivative; not an authoritative Kemendagri/BIG operational boundary feed.",
        "limitations": [
            "No RT/RW polygons are supplied by this dataset.",
            "Five source polygons without a valid Kelurahan/Desa code/name hierarchy were quarantined rather than assigned heuristically.",
            "Boundary currency and legal status require confirmation with the relevant local government.",
            "Use for MVP localisation, not as the sole basis of dispatch or legal/public boundary claims.",
        ],
        "source_files": acquired,
        "derived_file": {
            "file": output.name,
            "bytes": output.stat().st_size,
            "sha256": sha256(output),
        },
    }
    (processed_dir / "kelurahan_desa_kalimantan.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (processed_dir / "kelurahan_desa_kalimantan.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
