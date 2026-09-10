"""Validate that downloaded concession and Figshare inputs are non-empty and EDA-ready."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OFFICIAL_DIR = DATA_DIR / "official_concessions"
FIGSHARE_DIR = DATA_DIR / "figshare_all_hazards"


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def coordinates(value: Any) -> Iterator[tuple[float, float]]:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for item in value:
                yield from coordinates(item)


def check_geojson(path: Path, minimum_features: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features") or []
    geometry_types = Counter()
    empty_geometries = 0
    property_rows = 0
    field_non_null: Counter[str] = Counter()
    all_fields: set[str] = set()
    xs: list[float] = []
    ys: list[float] = []

    for feature in features:
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        geometry_types[geometry_type or "<empty>"] += 1
        if not geometry_type or not geometry.get("coordinates"):
            empty_geometries += 1
        for x, y in coordinates(geometry.get("coordinates")):
            xs.append(x)
            ys.append(y)
        props = feature.get("properties") or {}
        if props:
            property_rows += 1
        all_fields.update(props)
        field_non_null.update(key for key, value in props.items() if value not in (None, ""))

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "feature_count": len(features),
        "minimum_expected_features": minimum_features,
        "meets_minimum_feature_count": len(features) >= minimum_features,
        "geometry_types": dict(geometry_types),
        "empty_geometries": empty_geometries,
        "rows_with_properties": property_rows,
        "attribute_fields": sorted(all_fields),
        "fields_with_any_non_null_value": sorted(field_non_null),
        "bbox_wgs84": [min(xs), min(ys), max(xs), max(ys)] if xs and ys else None,
        "ready_for_attribute_eda": bool(features)
        and property_rows == len(features)
        and len(features) >= minimum_features,
        "ready_for_spatial_eda": bool(features)
        and empty_geometries == 0
        and property_rows == len(features)
        and len(features) >= minimum_features,
    }


def check_archive(path: Path, expected_md5: str | None) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        csvs = [name for name in names if name.lower().endswith(".csv")]
        bad_entry = archive.testzip()
    actual_md5 = md5(path)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "md5": actual_md5,
        "expected_md5": expected_md5,
        "checksum_matches": actual_md5 == expected_md5 if expected_md5 else None,
        "zip_test_failure": bad_entry,
        "entry_count": len(entries),
        "uncompressed_bytes": sum(entry.file_size for entry in entries),
        "csv_count": len(csvs),
        "csv_examples": csvs[:10],
        "ready_for_eda": bool(entries) and bool(csvs) and bad_entry is None,
    }


def main() -> None:
    manifest_path = FIGSHARE_DIR / "figshare_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_md5 = {item["name"]: item["supplied_md5"] for item in manifest["files"]}

    report: dict[str, Any] = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_concessions": {
            "wiup": check_geojson(OFFICIAL_DIR / "wiup_kalimantan_official.geojson", 1000),
            "pbph": check_geojson(OFFICIAL_DIR / "pbph_kalimantan_official.geojson", 100),
        },
        "figshare": {
            path.name: check_archive(path, expected_md5.get(path.name))
            for path in sorted(FIGSHARE_DIR.glob("*.zip"))
        },
    }

    failed = []
    for name, result in report["official_concessions"].items():
        if not result["ready_for_attribute_eda"]:
            failed.append(f"official_concessions.{name}")
    for name, result in report["figshare"].items():
        if not result["ready_for_eda"] or result["checksum_matches"] is not True:
            failed.append(f"figshare.{name}")
    report["ready_for_attribute_eda"] = not failed
    report["spatial_limitations"] = [
        name
        for name, result in report["official_concessions"].items()
        if not result["ready_for_spatial_eda"]
    ]
    report["failed_checks"] = failed

    output = DATA_DIR / "acquisition_validation.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(f"Validation failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
