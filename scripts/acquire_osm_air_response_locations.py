"""Acquire OSM-mapped aerodromes and helipads for Kalimantan response context.

These locations are potential aviation access points only. The output must not
be interpreted as aircraft, water-bombing, crew, flight-clearance, or
availability data.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "responders"
GRID_PATH = ROOT / "data" / "processed" / "dim_grid.geojson"
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
QUERY = """
[out:json][timeout:180];
(
  nwr["aeroway"="aerodrome"](-4.5,108,4.5,119.5);
  nwr["aeroway"="helipad"](-4.5,108,4.5,119.5);
);
out center tags;
""".strip()


def fetch() -> tuple[dict[str, Any], str]:
    errors: list[str] = []
    for endpoint in ENDPOINTS:
        for attempt in range(3):
            try:
                response = requests.post(
                    endpoint,
                    data={"data": QUERY},
                    headers={"User-Agent": "Kalimantan-FireWatch/1.0 (aviation-context acquisition)", "Accept": "application/json"},
                    timeout=240,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
                    raise ValueError("Overpass response has no elements array")
                return payload, endpoint
            except (requests.RequestException, ValueError) as error:
                errors.append(f"{endpoint} attempt {attempt + 1}: {error}")
                time.sleep(2 ** attempt)
    raise RuntimeError("All Overpass endpoints failed: " + " | ".join(errors))


def point(element: dict[str, Any]) -> tuple[float, float] | None:
    values = element if element.get("type") == "node" else element.get("center", {})
    lat, lon = values.get("lat"), values.get("lon")
    if isinstance(lat, (float, int)) and isinstance(lon, (float, int)):
        return float(lat), float(lon)
    return None


def build(payload: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    retrieved = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for element in payload["elements"]:
        coordinate = point(element)
        if coordinate is None:
            continue
        osm_type, osm_id = str(element["type"]), int(element["id"])
        if (osm_type, osm_id) in seen:
            continue
        seen.add((osm_type, osm_id))
        tags = element.get("tags") or {}
        latitude, longitude = coordinate
        rows.append({
            "osm_type": osm_type, "osm_id": osm_id,
            "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "name": tags.get("name") or tags.get("name:id"), "aeroway": tags.get("aeroway"),
            "icao": tags.get("icao"), "iata": tags.get("iata"), "operator": tags.get("operator"),
            "emergency": tags.get("emergency"), "latitude": latitude, "longitude": longitude,
            "source": "OpenStreetMap contributors", "license": "ODbL-1.0",
            "retrieved_at_utc": retrieved, "overpass_endpoint": endpoint,
            "province": None, "district": None,
        })
    return rows


def label_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    points = gpd.GeoDataFrame(rows, geometry=gpd.points_from_xy(
        [row["longitude"] for row in rows], [row["latitude"] for row in rows]
    ), crs="EPSG:4326")
    grid = gpd.read_file(GRID_PATH)[["province", "district", "geometry"]]
    joined = gpd.sjoin(points.drop(columns=["province", "district"]), grid, how="left", predicate="within")
    joined = joined.loc[~joined.index.duplicated(keep="first")]
    for index, row in joined.iterrows():
        rows[index]["province"] = row["province"] if isinstance(row["province"], str) else None
        rows[index]["district"] = row["district"] if isinstance(row["district"], str) else None


def write(rows: list[dict[str, Any]], endpoint: str) -> None:
    if not rows:
        raise ValueError("No OSM aerodrome/helipad locations were returned for Kalimantan.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUTPUT_DIR / "osm_air_response_locations_kalimantan"
    with (base.with_suffix(".csv")).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    features = [{"type": "Feature", "properties": {key: value for key, value in row.items() if key not in {"latitude", "longitude"}}, "geometry": {"type": "Point", "coordinates": [row["longitude"], row["latitude"]]}} for row in rows]
    base.with_suffix(".geojson").write_text(json.dumps({"type": "FeatureCollection", "name": "OSM aviation response locations — Kalimantan", "features": features}, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".overpassql").write_text(QUERY + "\n", encoding="utf-8")
    validation = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(), "feature_count": len(rows),
        "aerodrome_count": sum(row["aeroway"] == "aerodrome" for row in rows),
        "helipad_count": sum(row["aeroway"] == "helipad" for row in rows),
        "unique_osm_records": len({(row["osm_type"], row["osm_id"]) for row in rows}) == len(rows),
        "source": "OpenStreetMap via Overpass API", "coverage_note": "Potential aviation access points; no aircraft or operational availability is asserted.",
    }
    base.with_suffix(".validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    base.with_suffix(".provenance.json").write_text(json.dumps({"source": "OpenStreetMap via Overpass API", "overpass_endpoint": endpoint, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "query_file": str(base.with_suffix(".overpassql")), "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors"}, indent=2), encoding="utf-8")


def main() -> None:
    payload, endpoint = fetch()
    rows = build(payload, endpoint)
    label_rows(rows)
    write(rows, endpoint)
    print(f"Saved {len(rows)} OSM aviation locations to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
