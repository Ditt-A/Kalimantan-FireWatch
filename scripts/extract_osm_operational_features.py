#!/usr/bin/env python3
"""Extract operationally useful road and water candidates from a local OSM PBF.

The output is a self-hosted snapshot, deliberately preserving relevant OSM tags
instead of inventing road width, water pumpability, or road passability. The
``vehicle_access_inference`` and water candidates are triage aids only: local
route checks and incident-command confirmation remain required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmium
from shapely import wkb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "osm" / "kalimantan-260906.osm.pbf"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "processed" / "osm_operational"
ATTRIBUTION = "© OpenStreetMap contributors"
LINEAR_WATER_TYPES = {"river", "canal", "stream", "ditch", "drain", "wadi"}
WATERBODY_LANDUSES = {"reservoir", "basin"}
NON_VEHICLE_HIGHWAYS = {"footway", "path", "pedestrian", "steps", "bridleway", "cycleway"}
PAVED_SURFACES = {
    "asphalt",
    "concrete",
    "concrete:lanes",
    "concrete:plates",
    "paved",
    "paving_stones",
    "sett",
    "metal",
}
GRAVEL_SURFACES = {"gravel", "fine_gravel", "compacted", "pebblestone"}
UNPAVED_SURFACES = {"dirt", "earth", "ground", "mud", "sand", "soil", "grass", "woodchips"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tag_value(tags: dict[str, str], *names: str) -> str | None:
    for name in names:
        if value := tags.get(name):
            return value
    return None


def width_m(raw_width: str | None) -> float | None:
    """Parse only straightforward metre/bare-numeric widths; retain raw otherwise."""
    if not raw_width:
        return None
    lowered = raw_width.lower().strip()
    if "ft" in lowered or "'" in lowered:
        return None
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:m|met(?:er|re)?s?)?\s*", lowered)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 0 < value <= 100 else None


def surface_class(surface: str | None) -> str:
    if not surface:
        return "unknown"
    normalized = surface.lower().strip()
    if normalized in PAVED_SURFACES:
        return "paved"
    if normalized in GRAVEL_SURFACES:
        return "gravel_or_compacted"
    if normalized in UNPAVED_SURFACES:
        return "unpaved"
    return "other_or_verify"


def vehicle_access_inference(highway: str, parsed_width_m: float | None) -> str:
    if highway in NON_VEHICLE_HIGHWAYS:
        return "not_vehicle_route"
    if parsed_width_m is None:
        return "verify_width_and_condition"
    if parsed_width_m < 2.5:
        return "light_tanker_only_candidate"
    return "medium_heavy_tanker_candidate"


def line_geometry(factory: osmium.geom.WKBFactory, obj: Any):
    try:
        return wkb.loads(factory.create_linestring(obj), hex=True)
    except (RuntimeError, ValueError, TypeError):
        return None


def polygon_geometry(factory: osmium.geom.WKBFactory, obj: Any):
    try:
        return wkb.loads(factory.create_multipolygon(obj), hex=True)
    except (RuntimeError, ValueError, TypeError):
        return None


class OperationalFeatureCollector(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.factory = osmium.geom.WKBFactory()
        self.roads: list[dict[str, object]] = []
        self.waterways: list[dict[str, object]] = []
        self.waterbodies: list[dict[str, object]] = []
        self.water_points: list[dict[str, object]] = []
        self.skipped_geometry: Counter[str] = Counter()

    def node(self, node: osmium.osm.Node) -> None:
        tags = dict(node.tags)
        candidate_type = None
        if tags.get("amenity") == "fire_hydrant":
            candidate_type = "fire_hydrant"
        elif tags.get("man_made") == "water_well":
            candidate_type = "water_well"
        elif tags.get("natural") == "spring":
            candidate_type = "spring"
        elif tags.get("amenity") == "drinking_water":
            candidate_type = "drinking_water"
        if not candidate_type or not node.location.valid():
            return
        self.water_points.append(
            {
                "osm_id": node.id,
                "candidate_type": candidate_type,
                "name": tags.get("name"),
                "access": tags.get("access"),
                "operator": tags.get("operator"),
                "emergency": tags.get("emergency"),
                "fire_hydrant_type": tags.get("fire_hydrant:type"),
                "flow_rate": tags.get("flow_rate"),
                "water_source": tags.get("water_source"),
                "verification_status": "osm_candidate_requires_operator_verification",
                "geometry": gpd.points_from_xy([node.location.lon], [node.location.lat])[0],
            }
        )

    def way(self, way: osmium.osm.Way) -> None:
        tags = dict(way.tags)
        highway = tags.get("highway")
        waterway = tags.get("waterway")
        if highway and highway not in {"construction", "proposed", "platform", "elevator"}:
            geometry = line_geometry(self.factory, way)
            if geometry is None or geometry.is_empty:
                self.skipped_geometry["road"] += 1
            else:
                raw_width = tag_value(tags, "width", "maxwidth:physical")
                parsed_width = width_m(raw_width)
                self.roads.append(
                    {
                        "osm_id": way.id,
                        "highway": highway,
                        "name": tags.get("name"),
                        "ref": tags.get("ref"),
                        "surface": tags.get("surface"),
                        "surface_class": surface_class(tags.get("surface")),
                        "tracktype": tags.get("tracktype"),
                        "width_raw": raw_width,
                        "width_m": parsed_width,
                        "lanes": tags.get("lanes"),
                        "oneway": tags.get("oneway"),
                        "access": tag_value(tags, "motor_vehicle", "vehicle", "access"),
                        "bridge": tags.get("bridge"),
                        "smoothness": tags.get("smoothness"),
                        "maxweight": tags.get("maxweight"),
                        "vehicle_access_inference": vehicle_access_inference(highway, parsed_width),
                        "verification_status": "osm_snapshot_requires_route_and_field_verification",
                        "geometry": geometry,
                    }
                )
        if waterway in LINEAR_WATER_TYPES:
            geometry = line_geometry(self.factory, way)
            if geometry is None or geometry.is_empty:
                self.skipped_geometry["waterway"] += 1
            else:
                self.waterways.append(
                    {
                        "osm_id": way.id,
                        "waterway": waterway,
                        "name": tags.get("name"),
                        "water": tags.get("water"),
                        "intermittent": tags.get("intermittent"),
                        "seasonal": tags.get("seasonal"),
                        "access": tags.get("access"),
                        "boat": tags.get("boat"),
                        "verification_status": "osm_candidate_requires_operator_verification",
                        "geometry": geometry,
                    }
                )

    def area(self, area: osmium.osm.Area) -> None:
        tags = dict(area.tags)
        is_waterbody = (
            tags.get("natural") == "water"
            or bool(tags.get("water"))
            or tags.get("landuse") in WATERBODY_LANDUSES
            or tags.get("waterway") == "riverbank"
        )
        if not is_waterbody:
            return
        geometry = polygon_geometry(self.factory, area)
        if geometry is None or geometry.is_empty:
            self.skipped_geometry["waterbody"] += 1
            return
        self.waterbodies.append(
            {
                "osm_id": area.orig_id(),
                "osm_type": "way" if area.from_way() else "relation",
                "natural": tags.get("natural"),
                "water": tags.get("water"),
                "landuse": tags.get("landuse"),
                "waterway": tags.get("waterway"),
                "name": tags.get("name"),
                "intermittent": tags.get("intermittent"),
                "seasonal": tags.get("seasonal"),
                "access": tags.get("access"),
                "verification_status": "osm_candidate_requires_operator_verification",
                "geometry": geometry,
            }
        )


def to_frame(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def write_layer(frame: gpd.GeoDataFrame, output_dir: Path, name: str) -> dict[str, object]:
    output = output_dir / f"{name}.parquet"
    frame.to_parquet(output, index=False)
    return {
        "file": output.name,
        "rows": int(len(frame)),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "empty_geometries": int(frame.geometry.is_empty.sum()),
        "invalid_geometries": int((~frame.geometry.is_valid).sum()),
        "duplicate_osm_ids": int(frame["osm_id"].duplicated().sum()) if "osm_id" in frame else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source = args.input.resolve()
    output_dir = args.output_dir.resolve()
    if not source.is_file() or source.stat().st_size < 1024:
        raise FileNotFoundError(f"Missing or empty OSM PBF: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)

    collector = OperationalFeatureCollector()
    # locations=True supplies coordinates for way geometries; area() also
    # receives closed ways/multipolygon waterbodies from the PBF reader.
    collector.apply_file(str(source), locations=True)
    frames = {
        "roads_kalimantan": to_frame(collector.roads),
        "waterways_kalimantan": to_frame(collector.waterways),
        "waterbodies_kalimantan": to_frame(collector.waterbodies),
        "water_points_kalimantan": to_frame(collector.water_points),
    }
    layers = {name: write_layer(frame, output_dir, name) for name, frame in frames.items()}
    essential = ["roads_kalimantan", "waterways_kalimantan", "waterbodies_kalimantan"]
    failures = [name for name in essential if layers[name]["rows"] == 0]
    for name in essential:
        metadata = layers[name]
        if metadata["empty_geometries"] or metadata["invalid_geometries"] or metadata["duplicate_osm_ids"]:
            failures.append(name)
    validation: dict[str, object] = {
        "status": "passed" if not failures else "failed",
        "input": source.name,
        "input_bytes": source.stat().st_size,
        "input_sha256": sha256(source),
        "layers": layers,
        "tag_summaries": {
            "road_highway": dict(Counter(row["highway"] for row in collector.roads).most_common()),
            "road_vehicle_access_inference": dict(
                Counter(row["vehicle_access_inference"] for row in collector.roads).most_common()
            ),
            "linear_waterway": dict(Counter(row["waterway"] for row in collector.waterways).most_common()),
            "water_point_candidate_type": dict(
                Counter(row["candidate_type"] for row in collector.water_points).most_common()
            ),
        },
        "skipped_geometry": dict(collector.skipped_geometry),
        "failures": failures,
    }
    if failures:
        raise RuntimeError(json.dumps(validation, indent=2))
    provenance = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Geofabrik OpenStreetMap Kalimantan extract",
        "source_file": source.name,
        "source_sha256": sha256(source),
        "license": "Open Database License (ODbL) v1.0",
        "attribution": ATTRIBUTION,
        "layers": {
            "roads_kalimantan": "highway ways, with OSM surface/width/access tags retained",
            "waterways_kalimantan": "river/canal/stream/ditch/drain/wadi ways",
            "waterbodies_kalimantan": "OSM polygon/multipolygon candidates tagged water/reservoir/basin/riverbank",
            "water_points_kalimantan": "OSM nodes tagged fire_hydrant, water_well, spring, or drinking_water",
        },
        "limitations": [
            "OSM coverage and tags vary by place and date; absent features do not prove absence on the ground.",
            "Water layers show candidate locations, not live depth, quality, pumpability, legal access, or appliance access.",
            "Road width and surface tags are incomplete and are not an emergency-routing guarantee.",
            "Vehicle-access inference must be confirmed by local route/field checks before dispatch.",
        ],
    }
    (output_dir / "osm_operational.validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "osm_operational.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
