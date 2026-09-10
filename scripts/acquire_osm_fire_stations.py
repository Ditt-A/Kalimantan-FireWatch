"""Acquire OpenStreetMap-mapped fire stations in the five Indonesian Kalimantan provinces.

The output represents facilities currently mapped in OpenStreetMap, not an
authoritative government inventory. It includes standard OSM fire-station tags
and locally named Damkar/Pemadam Kebakaran facilities.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import geopandas as gpd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "responders"
GRID_PATH = PROJECT_ROOT / "data" / "processed" / "dim_grid.geojson"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

PROVINCES = (
    "Kalimantan Barat",
    "Kalimantan Tengah",
    "Kalimantan Selatan",
    "Kalimantan Timur",
    "Kalimantan Utara",
)

QUERY = """
[out:json][timeout:180];
area["boundary"="administrative"]["admin_level"="4"]
  ["name"~"^(Kalimantan Barat|Kalimantan Tengah|Kalimantan Selatan|Kalimantan Timur|Kalimantan Utara)$"]
  ->.kalimantan;
(
  nwr(area.kalimantan)["amenity"="fire_station"];
  nwr(area.kalimantan)["emergency"="fire_station"];
  nwr(area.kalimantan)["name"~"damkar|pemadam kebakaran|fire station",i];
);
out center tags;
""".strip()


def request_overpass() -> tuple[dict[str, Any], str]:
    headers = {
        "User-Agent": "Kalimantan-FireWatch/1.0 (OSM fire-station acquisition)",
        "Accept": "application/json",
    }
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            try:
                response = requests.post(
                    endpoint,
                    data={"data": QUERY},
                    headers=headers,
                    timeout=240,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or "elements" not in payload:
                    raise ValueError("Overpass response has no elements array")
                return payload, endpoint
            except (requests.RequestException, ValueError) as error:
                errors.append(f"{endpoint} (attempt {attempt + 1}): {error}")
                time.sleep(2**attempt)
    raise RuntimeError("No Overpass endpoint succeeded:\n" + "\n".join(errors))


def coordinate(element: dict[str, Any]) -> tuple[float, float] | None:
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


def match_reason(tags: dict[str, str]) -> str:
    matches = []
    if tags.get("amenity") == "fire_station":
        matches.append("amenity=fire_station")
    if tags.get("emergency") == "fire_station":
        matches.append("emergency=fire_station")
    if not matches:
        matches.append("local fire-service name")
    return "; ".join(matches)


def convert(payload: dict[str, Any], source_endpoint: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    features = []
    rows = []
    for element in payload["elements"]:
        tags = element.get("tags") or {}
        point = coordinate(element)
        if point is None:
            continue
        lat, lon = point
        osm_type = str(element["type"])
        osm_id = int(element["id"])
        properties = {
            "osm_type": osm_type,
            "osm_id": osm_id,
            "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "name": tags.get("name"),
            "name_id": tags.get("name:id"),
            "amenity": tags.get("amenity"),
            "emergency": tags.get("emergency"),
            "fire_station_type": tags.get("fire_station:type"),
            "operator": tags.get("operator"),
            "operator_type": tags.get("operator:type"),
            "address": ", ".join(
                value
                for value in (
                    tags.get("addr:street"),
                    tags.get("addr:village"),
                    tags.get("addr:city"),
                    tags.get("addr:district"),
                    tags.get("addr:province"),
                    tags.get("addr:postcode"),
                )
                if value
            )
            or None,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "opening_hours": tags.get("opening_hours"),
            "match_reason": match_reason(tags),
            "source": "OpenStreetMap contributors",
            "license": "ODbL-1.0",
            "retrieved_at_utc": retrieved_at,
            "overpass_endpoint": source_endpoint,
            "province": None,
            "district": None,
        }
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )
        rows.append({**properties, "latitude": lat, "longitude": lon})
    return features, rows


def add_project_admin_labels(features: list[dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    """Assign province/district from the project's existing five-province grid."""
    if not GRID_PATH.is_file() or not rows:
        return 0
    stations = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy(
            [row["longitude"] for row in rows], [row["latitude"] for row in rows]
        ),
        crs="EPSG:4326",
    )
    stations = stations.drop(columns=["province", "district"], errors="ignore")
    grid = gpd.read_file(GRID_PATH)[["province", "district", "geometry"]]
    joined = gpd.sjoin(stations, grid, how="left", predicate="within")
    joined = joined.loc[~joined.index.duplicated(keep="first")]
    labels = joined[["osm_type", "osm_id", "province", "district"]].set_index(
        ["osm_type", "osm_id"]
    )
    count = 0
    for feature, row in zip(features, rows, strict=True):
        label = labels.loc[(row["osm_type"], row["osm_id"])]
        province = label["province"] if isinstance(label["province"], str) else None
        district = label["district"] if isinstance(label["district"], str) else None
        feature["properties"]["province"] = province
        feature["properties"]["district"] = district
        row["province"] = province
        row["district"] = district
        count += province is not None
    return count


def read_existing_outputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    geojson = OUTPUT_DIR / "osm_fire_stations_kalimantan.geojson"
    payload = json.loads(geojson.read_text(encoding="utf-8"))
    features = payload["features"]
    rows = []
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        rows.append({**feature["properties"], "latitude": lat, "longitude": lon})
    endpoint = str(features[0]["properties"].get("overpass_endpoint", "existing OSM output"))
    return features, rows, endpoint


def write_outputs(features: list[dict[str, Any]], rows: list[dict[str, Any]], endpoint: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson = OUTPUT_DIR / "osm_fire_stations_kalimantan.geojson"
    csv_path = OUTPUT_DIR / "osm_fire_stations_kalimantan.csv"
    query_path = OUTPUT_DIR / "osm_fire_stations_kalimantan.overpassql"
    provenance_path = OUTPUT_DIR / "osm_fire_stations_kalimantan.provenance.json"
    validation_path = OUTPUT_DIR / "osm_fire_stations_kalimantan.validation.json"

    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "OpenStreetMap fire stations — Indonesian Kalimantan",
                "features": features,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fields = list(rows[0]) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    query_path.write_text(QUERY + "\n", encoding="utf-8")
    coordinates = [feature["geometry"]["coordinates"] for feature in features]
    checks = {
        "non_empty": bool(features),
        "all_point_geometries": all(
            (feature.get("geometry") or {}).get("type") == "Point" for feature in features
        ),
        "unique_osm_records": len(
            {(row["osm_type"], row["osm_id"]) for row in rows}
        ) == len(rows),
        "province_assigned": all(row.get("province") for row in rows),
        "district_assigned": all(row.get("district") for row in rows),
    }
    validation_path.write_text(
        json.dumps(
            {
                "validated_at_utc": datetime.now(timezone.utc).isoformat(),
                "feature_count": len(features),
                "named_feature_count": sum(bool(row.get("name")) for row in rows),
                "match_reason_counts": dict(Counter(row["match_reason"] for row in rows)),
                "province_counts": dict(Counter(row["province"] for row in rows)),
                "bbox_wgs84": [
                    min(point[0] for point in coordinates),
                    min(point[1] for point in coordinates),
                    max(point[0] for point in coordinates),
                    max(point[1] for point in coordinates),
                ],
                "checks": checks,
                "ready_for_spatial_eda": all(checks.values()),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provenance_path.write_text(
        json.dumps(
            {
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": "OpenStreetMap via Overpass API",
                "overpass_endpoint": endpoint,
                "query_file": str(query_path),
                "provinces": PROVINCES,
                "feature_count": len(features),
                "license": "Open Database License (ODbL) v1.0",
                "attribution": "© OpenStreetMap contributors",
                "coverage_note": (
                    "This is a current OSM-mapped inventory. It can omit facilities that "
                    "have not yet been mapped and is not an authoritative government registry."
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enrich-existing",
        action="store_true",
        help="Add project province/district labels to an existing OSM output without re-querying.",
    )
    args = parser.parse_args()
    if args.enrich_existing:
        features, rows, endpoint = read_existing_outputs()
    else:
        payload, endpoint = request_overpass()
        features, rows = convert(payload, endpoint)
    if not features:
        raise RuntimeError("Overpass returned no fire-station facilities in Kalimantan")
    assigned = add_project_admin_labels(features, rows)
    write_outputs(features, rows, endpoint)
    print(
        f"Saved {len(features)} OSM-mapped fire stations to {OUTPUT_DIR} "
        f"({assigned} assigned to a project province)"
    )


if __name__ == "__main__":
    main()
