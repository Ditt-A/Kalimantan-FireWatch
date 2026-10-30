"""Incident area provenance and WGS84 polygon measurement."""
from __future__ import annotations

import math
from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.polygon import orient

def polygon_area_ha(geojson: dict) -> float:
    geometry = shape(geojson.get("geometry", geojson))
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
        raise ValueError("Provide a valid Polygon or MultiPolygon GeoJSON")
    west, south, east, north = geometry.bounds
    if not (108 <= west <= east <= 119.5 and -4.5 <= south <= north <= 4.5):
        raise ValueError("Incident polygon must be inside the Kalimantan planning extent")
    polygons = [geometry] if geometry.geom_type == "Polygon" else geometry.geoms
    geod = Geod(ellps="WGS84")
    area = sum(abs(geod.geometry_area_perimeter(orient(polygon, sign=1.0))[0]) for polygon in polygons) / 10_000
    if not math.isfinite(area) or not 0 < area <= 10_000:
        raise ValueError("Polygon area must be greater than zero and at most 10,000 ha")
    return round(area, 6)
