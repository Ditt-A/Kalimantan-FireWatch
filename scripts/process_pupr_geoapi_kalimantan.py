#!/usr/bin/env python3
"""Clip UI-acquired PUPR GeoAPI road and bore-well data to Kalimantan.

The raw files are retained as returned by PUSDATIN's GeoAPI.  This script
performs exact spatial clipping to the project's five ADM1 polygons rather
than relying on a dataset name or a broad bounding box.  Bore wells remain
water-access *candidates*: the portal data do not establish legal access,
current yield, water quality, or appliance compatibility for firefighting.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from shapely import union_all


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "pupr_geoapi"
OUTPUT_DIR = ROOT / "data" / "processed" / "pupr_geoapi"
BOUNDARIES = ROOT / "data" / "raw" / "boundaries" / "idn_adm1.geojson"
PROVINCES = {
    "Central Kalimantan",
    "East Kalimantan",
    "North Kalimantan",
    "South Kalimantan",
    "West Kalimantan",
}
SOURCES = {
    "sumur_bor": (RAW_DIR / "pupr_sumur_bor_nasional.geojson", "pupr_sumur_bor_kalimantan.parquet"),
    "jalan_perbatasan": (
        RAW_DIR / "pupr_jalan_perbatasan_kalimantan.geojson",
        "pupr_jalan_perbatasan_kalimantan.parquet",
    ),
}
EXPECTED_FIELDS = {
    "sumur_bor": {"latitude", "longitude", "nama_sumur", "provinsi", "kota_kabupaten"},
    "jalan_perbatasan": {"linkid", "link_name", "road_funct", "road_statu", "road_class", "provinsi"},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def kalimantan_adm1() -> gpd.GeoDataFrame:
    admin = gpd.read_file(BOUNDARIES).to_crs("EPSG:4326")
    name_column = next((column for column in ("shapeName", "NAME_1", "name") if column in admin.columns), None)
    if name_column is None:
        raise RuntimeError("ADM1 source lacks an expected province-name column")
    selected = admin.loc[admin[name_column].isin(PROVINCES), [name_column, "geometry"]].copy()
    selected = selected.rename(columns={name_column: "province"})
    if set(selected["province"]) != PROVINCES or len(selected) != 5:
        raise RuntimeError("ADM1 source does not contain exactly the five project provinces")
    if selected.geometry.is_empty.any() or (~selected.geometry.is_valid).any():
        raise RuntimeError("ADM1 source contains invalid or empty project geometries")
    return selected


def retain_spatially_usable_source(name: str, frame: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    if frame.empty:
        raise RuntimeError(f"{name}: raw GeoAPI output is empty")
    if frame.crs is None:
        raise RuntimeError(f"{name}: raw GeoAPI output has no CRS")
    missing = int(frame.geometry.isna().sum())
    empty = int(frame.geometry.is_empty.sum())
    invalid = int((~frame.geometry.is_valid).sum())
    usable = frame.geometry.notna() & ~frame.geometry.is_empty & frame.geometry.is_valid
    cleaned = frame.loc[usable].copy()
    if cleaned.empty:
        raise RuntimeError(f"{name}: raw GeoAPI output has no spatially usable features")
    return cleaned, {
        "source_rows": int(len(frame)),
        "spatially_usable_source_rows": int(len(cleaned)),
        "quarantined_missing_geometry": missing,
        "quarantined_empty_geometry": empty,
        "quarantined_invalid_geometry": invalid,
    }


def check_expected_attributes(name: str, frame: gpd.GeoDataFrame) -> dict[str, int]:
    expected = EXPECTED_FIELDS[name]
    missing = sorted(expected - set(frame.columns))
    if missing:
        raise RuntimeError(f"{name}: missing expected GeoAPI fields: {missing}")
    populated = {field: int(frame[field].notna().sum()) for field in sorted(expected)}
    empty = sorted(field for field, count in populated.items() if count == 0)
    if empty:
        raise RuntimeError(f"{name}: expected GeoAPI fields are entirely empty: {empty}")
    return populated


def clip_and_assign(name: str, frame: gpd.GeoDataFrame, adm1: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = frame.to_crs(adm1.crs)
    # Overlay makes every returned geometry lie inside the explicit project
    # boundary and carries a province label without nearest-neighbour guessing.
    clipped = gpd.overlay(frame, adm1, how="intersection", keep_geom_type=False)
    # Re-intersect against the union to eliminate any GEOS boundary precision
    # sliver before enforcing the exact-scope invariant below.
    project_union = union_all(adm1.geometry.values)
    clipped["geometry"] = clipped.geometry.intersection(project_union)
    clipped = clipped.loc[~clipped.geometry.is_empty & clipped.geometry.notna()].copy()
    if clipped.empty:
        raise RuntimeError(f"{name}: no returned feature intersects the five project provinces")
    if (~clipped.geometry.is_valid).any():
        raise RuntimeError(f"{name}: clipping produced invalid geometry")
    clipped["source_layer"] = name
    if name == "sumur_bor":
        clipped["water_access_status"] = "candidate_requires_local_yield_quality_access_verification"
    return clipped


def validate_layer(
    name: str,
    raw: gpd.GeoDataFrame,
    clipped: gpd.GeoDataFrame,
    boundary_union,
    output: Path,
    required_attribute_populated_rows: dict[str, int],
) -> dict[str, object]:
    # Test the geometric remainder rather than ``within``: line endpoints and
    # border-following road segments are legitimately on the ADM1 boundary.
    outside_remainder = clipped.geometry.difference(boundary_union)
    material_remainders = []
    zero_measure_boundary_artifacts = 0
    for index, geometry in enumerate(outside_remainder):
        if geometry.is_empty:
            continue
        # A zero-length line at one shared ADM1 vertex is an overlay precision
        # artefact, not a segment outside scope. Points are always material.
        is_point = geometry.geom_type in {"Point", "MultiPoint"}
        if is_point or geometry.length > 1e-10 or geometry.area > 1e-14:
            material_remainders.append((index, geometry))
        else:
            zero_measure_boundary_artifacts += 1
    outside = len(material_remainders)
    if outside:
        details = [
            {
                "source_type": clipped.geometry.iloc[index].geom_type,
                "remainder_type": geometry.geom_type,
                "bounds": [round(value, 7) for value in geometry.bounds],
            }
            for index, geometry in material_remainders
        ]
        raise RuntimeError(f"{name}: {outside} output geometries fall outside project boundary: {details}")
    per_province = {province: int(count) for province, count in clipped.groupby("province").size().items()}
    persisted = gpd.read_parquet(output)
    if len(persisted) != len(clipped) or persisted.crs is None:
        raise RuntimeError(f"{name}: persisted GeoParquet row count or CRS does not match output")
    if persisted.geometry.isna().any() or persisted.geometry.is_empty.any() or (~persisted.geometry.is_valid).any():
        raise RuntimeError(f"{name}: persisted GeoParquet contains invalid or empty geometries")
    return {
        "raw_rows": int(len(raw)),
        "kalimantan_rows": int(len(clipped)),
        "geometry_types": {key: int(value) for key, value in clipped.geom_type.value_counts().items()},
        "province_counts": per_province,
        "empty_geometries": int(clipped.geometry.is_empty.sum()),
        "invalid_geometries": int((~clipped.geometry.is_valid).sum()),
        "outside_project_boundary": outside,
        "zero_measure_boundary_artifacts": zero_measure_boundary_artifacts,
        "expected_attribute_populated_rows": required_attribute_populated_rows,
        "output_file": output.name,
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    adm1 = kalimantan_adm1()
    kalimantan_union = union_all(adm1.geometry.values)
    layers: dict[str, dict[str, object]] = {}
    for name, (input_path, output_name) in SOURCES.items():
        if not input_path.is_file() or input_path.stat().st_size < 100:
            raise FileNotFoundError(f"Missing/empty raw PUPR GeoAPI output: {input_path}")
        raw_source = gpd.read_file(input_path)
        raw, source_qc = retain_spatially_usable_source(name, raw_source)
        attribute_qc = check_expected_attributes(name, raw)
        clipped = clip_and_assign(name, raw, adm1)
        output = OUTPUT_DIR / output_name
        clipped.to_parquet(output, index=False)
        layers[name] = {
            "input_file": str(input_path.relative_to(ROOT)),
            "input_bytes": input_path.stat().st_size,
            "input_sha256": sha256(input_path),
            **source_qc,
            **validate_layer(name, raw, clipped, kalimantan_union, output, attribute_qc),
        }

    # Source has no explicit refresh date on records in the UI response.  Keep
    # exact retrieval time, source scope and non-operational limitations clear.
    validation = {
        "status": "passed",
        "scope": "exact intersection with five project ADM1 Kalimantan provinces",
        "layers": layers,
    }
    provenance = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "PUSDATIN Kementerian PU Public GeoAPI",
        "portal": "https://sigi.pu.go.id/geoapi/",
        "acquisition": "GeoJSON responses generated by the portal UI after user-authorized registration; paginated through the same authenticated browser context.",
        "scope": "Central, East, North, South, and West Kalimantan, clipped to project ADM1 polygons.",
        "layers": {
            "sumur_bor": "PUPR GeoAPI Sumur layer; points are bore-well candidates, not asserted firefighting water sources.",
            "jalan_perbatasan": "PUPR GeoAPI Jalan Perbatasan Kalimantan layer; geometry/attributes are planning evidence, not a live road-passability guarantee.",
        },
        "limitations": [
            "Bore-well records do not prove legal access, live yield, water quality, pump compatibility, or appliance access.",
            "Road records do not prove current closure, bridge loading, surface condition, or fire-appliance passability.",
            "Verify local access and operational suitability before a dispatch decision.",
        ],
    }
    (OUTPUT_DIR / "pupr_geoapi.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "pupr_geoapi.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
