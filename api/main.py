"""Dashboard API backed by validated local project artifacts.

The service is deliberately a decision-support API. It reads versioned local
snapshots and model artifacts; it does not expose an automatic dispatch action
or claim a live operational feed.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi.responses import JSONResponse
from src.firewatch.releases import ReleaseDirectory, bind_snapshot, current_snapshot, release_cached, release_path
from src.firewatch import auth
from src.firewatch.area import polygon_area_ha
from src.firewatch.freshness import layer_freshness
from src.firewatch.operations import audit_event, latest_job_statuses
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from shapely.geometry import Point
from src.firewatch.field_observations import (
    list_fire_type_observations,
    latest_fire_type_observation,
    save_fire_type_observation,
)
from src.firewatch.fire_weather import fire_weather_context
from src.firewatch.response_planning import peat_context_at_coordinate
from src.firewatch.soil_context import soil_context_for_grid, soilgrids_grid_context


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ReleaseDirectory("data/processed")
MODELS = ReleaseDirectory("models")
KALIMANTAN_BOUNDS = (108.0, -4.5, 119.5, 4.5)
GROUND_RESPONSE_RADIUS_DEFAULT_KM = 100.0
AIR_RESPONSE_RADIUS_DEFAULT_KM = 250.0
OSRM_ROUTER_URL = os.getenv("FIREWATCH_OSRM_ROUTER_URL", "https://router.project-osrm.org").rstrip("/")
# A forecast grid is a planning-scale unit, not an incident perimeter.  When
# at least this share overlaps BIG peat, retain peat as a planning context even
# when the grid centroid itself lies in a non-peat portion of the cell.
PEAT_CONTEXT_MIN_COVERAGE_PCT = 10.0
PERSONNEL_FEATURES = [
    "area_ha", "log_area_ha", "soil_class", "fire_type_class", "fwi_class",
    "water_access_index_relative_0_100", "nearest_permanent_surface_water_km",
    "water_access_class", "us_area_band", "us_peak_personnel_median",
    "us_peak_personnel_p90", "operational_complexity_level",
]

app = FastAPI(
    title="Kalimantan FireWatch API",
    version="0.1.0",
    description="Validated snapshot serving API for the FireWatch decision-support dashboard.",
)
app.add_middleware(
    CORSMiddleware,
    # Both spellings are normal in local Next.js/WSL development. Production
    # deployment must replace this list with its own dashboard origin.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def pin_release_and_authorize(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    protected = request.method == "POST" and request.url.path not in {"/api/v1/auth/login", "/api/v1/auth/logout"}
    protected = protected or request.url.path == "/api/v1/field-observations" or bool(request.query_params.get("incident_id"))
    if protected:
        role = auth.session_role(request.cookies.get(auth.COOKIE))
        if role is None:
            return JSONResponse({"detail": "Operator login required"}, status_code=401)
        request.state.actor = role
        if auth.required() and request.headers.get("origin"):
            from urllib.parse import urlsplit
            if urlsplit(request.headers["origin"]).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
    try:
        snapshot = current_snapshot()
    except (ValueError, OSError, KeyError):
        return JSONResponse({"detail": "Active data release is unavailable"}, status_code=503)
    expected = request.query_params.get("release_id") or request.headers.get("X-Firewatch-Release")
    if expected and expected != snapshot.token:
        return JSONResponse({"detail": "The map data release changed. Refresh the map and select the incident again."}, status_code=409)
    with bind_snapshot(snapshot):
        response = await call_next(request)
        response.headers["X-Firewatch-Release"] = snapshot.token
        return response


class LoginRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=256)


@app.post("/api/v1/auth/login")
def login(body: LoginRequest):
    session = auth.issue_session(body.access_code)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid operator access code or authentication is not configured")
    token, role = session
    response = JSONResponse({"role": role})
    response.set_cookie(auth.COOKIE, token, httponly=True, secure=auth.required(), samesite="strict", max_age=8 * 3600, path="/")
    return response


@app.post("/api/v1/auth/logout")
def logout():
    response = JSONResponse({"status": "signed_out"})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{key: _json_value(value) for key, value in row.items()} for row in frame.to_dict("records")]


def _firms_display_groups(data: pd.DataFrame, minimum_footprint_ha: float, merge_radius_km: float) -> list[dict[str, Any]]:
    """Merge nearby FIRMS detections for map display without dropping records.

    The threshold is the sum of VIIRS pixel-footprint proxies (scan × track),
    never a claimed burned area.  A low-footprint group only merges into its
    nearest neighbour when that neighbour remains local; otherwise it remains
    visible with an explicit isolated status.
    """
    if data.empty:
        return []
    points = data.reset_index(drop=True).copy()
    reference_latitude = float(points["latitude"].mean())
    projected = np.column_stack((
        points["longitude"].to_numpy(dtype=float) * 111.32 * math.cos(math.radians(reference_latitude)),
        points["latitude"].to_numpy(dtype=float) * 111.32,
    ))
    labels = DBSCAN(eps=merge_radius_km, min_samples=1, metric="euclidean").fit_predict(projected)
    groups = [np.flatnonzero(labels == label) for label in np.unique(labels)]

    def footprint(group: np.ndarray) -> float:
        return float(points.iloc[group]["footprint_ha_proxy"].sum())

    def centroid(group: np.ndarray) -> np.ndarray:
        return projected[group].mean(axis=0)

    if minimum_footprint_ha > 0 and len(groups) > 1:
        # Initial groups are already DBSCAN clusters at ``merge_radius_km``.
        # For a small group, efficiently join only its nearest initial group
        # when it is still local.  Union-find preserves every input row while
        # avoiding the quadratic/repeated centroid search that made the map
        # endpoint slow on a high-detection day.
        centres = np.vstack([centroid(group) for group in groups])
        group_footprints = np.asarray([footprint(group) for group in groups])
        nearest_distances, nearest_indices = NearestNeighbors(n_neighbors=2).fit(centres).kneighbors(centres)
        parents = np.arange(len(groups))

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = int(parents[index])
            return int(index)

        def union(left: int, right: int) -> None:
            left_root, right_root = root(left), root(right)
            if left_root != right_root:
                parents[left_root] = right_root

        for index, group_footprint in enumerate(group_footprints):
            neighbour = int(nearest_indices[index, 1])
            distance_km = float(nearest_distances[index, 1])
            if group_footprint < minimum_footprint_ha and distance_km <= merge_radius_km * 2:
                union(index, neighbour)

        merged: dict[int, list[np.ndarray]] = {}
        for index, group in enumerate(groups):
            merged.setdefault(root(index), []).append(group)
        groups = [np.concatenate(member_groups) for member_groups in merged.values()]

    records: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        subset = points.iloc[group]
        representative = subset.loc[subset["frp"].idxmax()]
        lat_min, lat_max = float(subset["latitude"].min()), float(subset["latitude"].max())
        lon_min, lon_max = float(subset["longitude"].min()), float(subset["longitude"].max())
        footprint_total = float(subset["footprint_ha_proxy"].sum())
        is_isolated_below_minimum = len(subset) == 1 and footprint_total < minimum_footprint_ha
        records.append({
            "display_group_id": f"F{index:05d}", "detection_id": int(representative["detection_id"]),
            "detection_count": int(len(subset)), "display_group_status": "isolated_below_minimum_retained" if is_isolated_below_minimum else "merged_nearby_detections" if len(subset) > 1 else "individual_detection",
            "latitude": float(subset["latitude"].mean()), "longitude": float(subset["longitude"].mean()),
            "latitude_min": lat_min, "latitude_max": lat_max, "longitude_min": lon_min, "longitude_max": lon_max,
            "acq_date": _json_value(representative["acq_date"]), "acq_datetime": _json_value(representative["acq_datetime"]),
            "confidence": _json_value(representative["confidence"]), "frp": float(representative["frp"]),
            "frp_min": float(subset["frp"].min()), "frp_max": float(subset["frp"].max()), "frp_total": float(subset["frp"].sum()),
            "footprint_ha_proxy_total": footprint_total, "footprint_ha_proxy_min": float(subset["footprint_ha_proxy"].min()), "footprint_ha_proxy_max": float(subset["footprint_ha_proxy"].max()),
            "satellite": _json_value(representative["satellite"]), "instrument": _json_value(representative["instrument"]), "daynight": _json_value(representative["daynight"]), "source_product": _json_value(representative["source_product"]),
            "grid_id": _json_value(representative["grid_id"]), "province": _json_value(representative["province"]), "district": _json_value(representative["district"]),
        })
    return records


def _merged_alert_area_ha(
    alert_id: int, minimum_footprint_ha: float, merge_radius_km: float, province: str | None = None,
) -> float:
    """Rebuild the selected map group so planner area cannot depend on UI state."""
    source = alerts()
    selected = source.loc[source["detection_id"].eq(alert_id)]
    if len(selected) != 1:
        raise ValueError("Selected FIRMS alert is not present in the local snapshot.")
    date = selected.iloc[0]["acq_date"]
    day = source.loc[source["acq_date"].eq(date)]
    if province:
        day = day.loc[day["province"].eq(province)]
    group = next(
        (item for item in _firms_display_groups(day, minimum_footprint_ha, merge_radius_km) if item["detection_id"] == alert_id),
        None,
    )
    if group is None:
        raise ValueError("Selected FIRMS group could not be rebuilt. Reload the prioritization map and select it again.")
    return float(group["footprint_ha_proxy_total"])


@release_cached
def predictions() -> pd.DataFrame:
    data = pd.read_parquet(PROCESSED / "prediction_grid_week.parquet")
    data["week_start"] = pd.to_datetime(data["week_start"])
    return data.loc[
        data["centroid_lon"].between(KALIMANTAN_BOUNDS[0], KALIMANTAN_BOUNDS[2])
        & data["centroid_lat"].between(KALIMANTAN_BOUNDS[1], KALIMANTAN_BOUNDS[3])
    ].copy()


@release_cached
def alerts() -> pd.DataFrame:
    data = pd.read_parquet(PROCESSED / "firms_kalimantan_clean.parquet")
    data["acq_date"] = pd.to_datetime(data["acq_date"])
    data["acq_datetime"] = pd.to_datetime(data["acq_datetime"])
    return data.loc[
        data["longitude"].between(KALIMANTAN_BOUNDS[0], KALIMANTAN_BOUNDS[2])
        & data["latitude"].between(KALIMANTAN_BOUNDS[1], KALIMANTAN_BOUNDS[3])
    ].copy()


@release_cached
def grids() -> gpd.GeoDataFrame:
    return gpd.read_parquet(PROCESSED / "dim_grid.parquet").to_crs("EPSG:4326")


@release_cached
def peat_context_grid_ids() -> frozenset[str]:
    """Return grid IDs eligible for the peat planning-context map mode.

    Eligibility uses the same BIG source in two complementary spatial forms:
    the grid-centre point is inside a BIG peat polygon, or the forecast cell
    overlaps BIG peat by at least ``PEAT_CONTEXT_MIN_COVERAGE_PCT``.  The
    planner records which form supplied the peat recommendation so an overlap
    is never presented as point-level field confirmation.
    """
    centre_columns = ["grid_id", "centroid_lon", "centroid_lat"]
    centres = predictions().loc[:, centre_columns].drop_duplicates("grid_id")
    points = gpd.GeoDataFrame(
        centres,
        geometry=gpd.points_from_xy(centres["centroid_lon"], centres["centroid_lat"]),
        crs="EPSG:4326",
    )
    peat = gpd.read_parquet(
        PROCESSED / "peatland" / "big_peat_extent_kalimantan.parquet"
    ).to_crs("EPSG:4326")
    if peat.empty:
        raise RuntimeError("BIG peat-location layer is empty.")
    mapped = gpd.sjoin(points, peat.loc[:, ["geometry"]], how="inner", predicate="intersects")
    coverage_ids = predictions().loc[
        predictions()["peat_pct"].ge(PEAT_CONTEXT_MIN_COVERAGE_PCT), "grid_id"
    ].astype(str)
    return frozenset(mapped["grid_id"].astype(str)).union(coverage_ids)


@release_cached
def water_grid() -> pd.DataFrame:
    required = [
        "grid_id", "nearest_permanent_surface_water_km", "nearest_any_surface_water_km",
        "water_access_index_relative_0_100",
    ]
    data = pd.read_parquet(PROCESSED / "water_access_grid.parquet")
    missing = set(required) - set(data.columns)
    if missing:
        raise RuntimeError(f"Water-access serving table lacks {sorted(missing)}")
    return data.loc[:, required].copy()


@release_cached
def mineral_context_grid() -> pd.DataFrame:
    """Load the locally acquired SoilGrids context, one row per forecast grid."""
    return soilgrids_grid_context().reset_index().copy()


@release_cached
def us_resource_reference() -> pd.DataFrame:
    data = pd.read_csv(release_path("reports/us_fire_resource_eda/resource_by_final_acres_band.csv"))
    return data.set_index("final_acres_band")


@release_cached
def model_registry() -> dict[str, Any]:
    return json.loads((MODELS / "dashboard_model_registry.json").read_text(encoding="utf-8"))


@release_cached
def personnel_model() -> Any:
    return joblib.load(MODELS / "feature2_personnel_hgb_pilot.joblib")


@release_cached
def engine_policy() -> dict[str, Any]:
    return json.loads((MODELS / "feature2_fire_engine_policy.json").read_text(encoding="utf-8"))


@release_cached
def response_cases() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED / "feature2_response_planning" / "feature2_response_planning_cohort.parquet")


@release_cached
def local_initial_area_default() -> dict[str, Any]:
    """Return an auditable starting area for map-selected, unperimetered events.

    A FIRMS point or priority grid does not measure incident area.  The median
    documented local incident area is therefore only an initial form value,
    never a perimeter estimate or a claim about the selected detection.
    """
    areas = pd.to_numeric(response_cases()["area_ha"], errors="coerce").dropna()
    if areas.empty:
        return {"area_ha": None, "source": "No documented local incident areas are available."}
    return {
        "area_ha": round(float(areas.median()), 2),
        "documented_case_count": int(len(areas)),
        "source": "Median documented incident area in the local response-planning evidence cohort; replace with a mapped or field-verified perimeter.",
    }


@release_cached
def vehicle_evidence() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED / "feature2_vehicle_evidence" / "feature2_vehicle_subtype_evidence.parquet")


@release_cached
def fire_stations() -> pd.DataFrame:
    path = release_path("data/responders/osm_fire_stations_kalimantan.csv")
    data = pd.read_csv(path)
    required = {"osm_type", "osm_id", "name", "latitude", "longitude", "province", "district", "source", "license", "osm_url"}
    missing = required - set(data.columns)
    if missing or data.empty:
        raise RuntimeError(f"Fire-station data is unavailable or lacks {sorted(missing)}")
    return data.loc[data["latitude"].between(KALIMANTAN_BOUNDS[1], KALIMANTAN_BOUNDS[3]) & data["longitude"].between(KALIMANTAN_BOUNDS[0], KALIMANTAN_BOUNDS[2])].copy()


@release_cached
def air_response_locations() -> pd.DataFrame:
    path = release_path("data/responders/osm_air_response_locations_kalimantan.csv")
    data = pd.read_csv(path)
    required = {"osm_type", "osm_id", "name", "aeroway", "latitude", "longitude", "province", "district", "source", "license", "osm_url"}
    missing = required - set(data.columns)
    if missing or data.empty:
        raise RuntimeError(f"Aviation-location data is unavailable or lacks {sorted(missing)}")
    return data.loc[data["latitude"].between(KALIMANTAN_BOUNDS[1], KALIMANTAN_BOUNDS[3]) & data["longitude"].between(KALIMANTAN_BOUNDS[0], KALIMANTAN_BOUNDS[2])].copy()


def _haversine_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    radius_km = 6371.0088
    lat_a, lon_a, lat_b, lon_b = map(math.radians, (latitude_a, longitude_a, latitude_b, longitude_b))
    angle = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return radius_km * 2 * math.asin(math.sqrt(angle))


def _response_candidates(frame: pd.DataFrame, *, response_mode: str, latitude: float, longitude: float, radius_km: float, limit: int) -> list[dict[str, Any]]:
    candidates = frame.copy()
    candidates["straight_line_distance_km"] = candidates.apply(
        lambda row: _haversine_km(latitude, longitude, float(row["latitude"]), float(row["longitude"])), axis=1
    )
    candidates = candidates.loc[candidates["straight_line_distance_km"].le(radius_km)].sort_values("straight_line_distance_km").head(limit)
    records: list[dict[str, Any]] = []
    for row in candidates.to_dict("records"):
        osm_type, osm_id = str(row["osm_type"]), int(row["osm_id"])
        records.append({
            "candidate_id": f"{response_mode}:{osm_type}:{osm_id}", "response_mode": response_mode,
            "osm_type": osm_type, "osm_id": osm_id,
            "name": _json_value(row.get("name")) or ("Unnamed fire station" if response_mode == "ground" else "Unnamed aviation location"),
            "latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
            "province": _json_value(row.get("province")), "district": _json_value(row.get("district")),
            "straight_line_distance_km": round(float(row["straight_line_distance_km"]), 2),
            "facility_type": _json_value(row.get("fire_station_type") if response_mode == "ground" else row.get("aeroway")),
            "source": _json_value(row.get("source")), "license": _json_value(row.get("license")), "source_url": _json_value(row.get("osm_url")),
            "availability_status": "not_provided_by_location_data",
        })
    return records


def _candidate_from_id(candidate_id: str) -> tuple[str, dict[str, Any]]:
    try:
        response_mode, osm_type, osm_id_raw = candidate_id.split(":", 2)
        osm_id = int(osm_id_raw)
    except ValueError as error:
        raise ValueError("candidate_id must be ground:<osm_type>:<osm_id> or air:<osm_type>:<osm_id>") from error
    if response_mode not in {"ground", "air"}:
        raise ValueError("candidate_id response mode must be ground or air")
    source = fire_stations() if response_mode == "ground" else air_response_locations()
    matches = source.loc[source["osm_type"].astype(str).eq(osm_type) & source["osm_id"].eq(osm_id)]
    if len(matches) != 1:
        raise ValueError("Response candidate was not found in the local location data.")
    return response_mode, matches.iloc[0].to_dict()


@lru_cache(maxsize=256)
def _ground_route(start_longitude: float, start_latitude: float, end_longitude: float, end_latitude: float) -> dict[str, Any]:
    """Get a road route; retain a visible straight-line fallback if OSRM is unavailable."""
    try:
        response = requests.get(
            f"{OSRM_ROUTER_URL}/route/v1/driving/{start_longitude},{start_latitude};{end_longitude},{end_latitude}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"}, timeout=25,
        )
        response.raise_for_status()
        route = response.json().get("routes", [])[0]
        coordinates = route["geometry"]["coordinates"]
        if len(coordinates) < 2:
            raise ValueError("OSRM route has no line geometry")
        return {"route_status": "road_route", "distance_km": round(float(route["distance"]) / 1000, 2), "duration_min": round(float(route["duration"]) / 60, 1), "coordinates": coordinates, "source": "OSRM driving route over OpenStreetMap road network"}
    except Exception as error:
        return {"route_status": "straight_line_fallback", "distance_km": round(_haversine_km(start_latitude, start_longitude, end_latitude, end_longitude), 2), "duration_min": None, "coordinates": [[start_longitude, start_latitude], [end_longitude, end_latitude]], "source": f"Great-circle fallback because road router was unavailable ({type(error).__name__})"}


def _latest_prediction_week() -> pd.Timestamp:
    return predictions()["week_start"].max()


def _filter_place(frame: pd.DataFrame, province: str | None, district: str | None) -> pd.DataFrame:
    result = frame
    if province:
        result = result.loc[result["province"].eq(province)]
    if district:
        result = result.loc[result["district"].eq(district)]
    return result


def _nearest_grid_context(
    longitude: float, latitude: float, selected_grid_id: str | None = None,
) -> dict[str, Any]:
    if not (KALIMANTAN_BOUNDS[0] <= longitude <= KALIMANTAN_BOUNDS[2] and KALIMANTAN_BOUNDS[1] <= latitude <= KALIMANTAN_BOUNDS[3]):
        raise ValueError("Coordinates must fall within the Kalimantan MVP extent.")
    point = Point(longitude, latitude)
    grid = grids()
    requested = grid.loc[grid["grid_id"].astype(str).eq(str(selected_grid_id))] if selected_grid_id else grid.iloc[0:0]
    if len(requested) == 1 and requested.iloc[0].geometry.covers(point):
        # A forecast-grid click supplies both its source grid ID and centroid.
        # Honouring the pair prevents a boundary/rounding lookup from silently
        # selecting a neighbouring grid when the planner opens.
        selected = requested.iloc[0]
        match_status = "map_selected_grid_id_covers_coordinate"
    else:
        hit_indices = list(grid.sindex.query(point, predicate="intersects"))
        hits = grid.iloc[hit_indices]
        hits = hits.loc[hits.geometry.covers(point)] if not hits.empty else hits
        if not hits.empty:
            selected = hits.sort_values("grid_id").iloc[0]
            match_status = "source_grid_covers_coordinate"
        else:
            distances = (grid.geometry.centroid.x - longitude).pow(2) + (grid.geometry.centroid.y - latitude).pow(2)
            selected = grid.loc[distances.idxmin()]
            match_status = "nearest_grid_centroid_approximation"
    water = water_grid().set_index("grid_id").loc[selected["grid_id"]]
    latest = predictions().loc[
        predictions()["week_start"].eq(_latest_prediction_week())
        & predictions()["grid_id"].eq(selected["grid_id"])
    ]
    forecast = _records(latest.iloc[:1])[0] if not latest.empty else None
    return {
        "grid_id": selected["grid_id"], "province": selected.get("province"),
        "grid_peat_pct": float(selected.get("peat_pct", 0.0)),
        "district": selected.get("district"), "grid_match_status": match_status,
        "nearest_permanent_surface_water_km": float(water["nearest_permanent_surface_water_km"]),
        "nearest_any_surface_water_km": float(water["nearest_any_surface_water_km"]),
        "water_access_index_relative_0_100": float(water["water_access_index_relative_0_100"]),
        "forecast_grid": forecast,
    }


def _peat_lookup(longitude: float, latitude: float) -> dict[str, Any]:
    """Expose mapped peat context without calling an unmapped point mineral."""
    try:
        lookup = peat_context_at_coordinate(longitude=longitude, latitude=latitude)
    except Exception as error:  # Keep response planning available if a map lookup is unavailable.
        return {
            "lookup_status": "lookup_unavailable_requires_field_confirmation",
            "mapped_as_peat": None,
            "recommended_soil_class": "unknown",
            "message": f"BIG peat lookup unavailable: {type(error).__name__}",
        }
    mapped_as_peat = lookup.get("peat_location", {}).get("mapped_as_peat") is True
    return {
        "lookup_status": lookup.get("lookup_status"),
        "mapped_as_peat": mapped_as_peat,
        "recommended_soil_class": "peat" if mapped_as_peat else "unknown",
        "message": (
            "Mapped as peat by the BIG peat-location layer. Confirm on site."
            if mapped_as_peat
            else "Not mapped as peat at this coordinate. This is not proof of mineral soil; confirm on site."
        ),
        "source": lookup.get("source"),
        "peat_location": lookup.get("peat_location"),
        "peat_thick_class": lookup.get("peat_thick_class"),
        "thickness_gt_1_5m_status": lookup.get("thickness_gt_1_5m_status"),
    }


def _fire_weather_lookup(longitude: float, latitude: float) -> dict[str, Any]:
    """Keep map/planner use available if the external weather provider is down."""
    try:
        return fire_weather_context(latitude=latitude, longitude=longitude)
    except Exception as error:
        return {
            "lookup_status": "unavailable",
            "recommended_fwi_class": "unknown",
            "message": f"Open-Meteo FWI context unavailable: {type(error).__name__}",
        }


def _planner_context(
    longitude: float,
    latitude: float,
    selected_grid_id: str | None = None,
    map_area_ha: float | None = None,
    map_merge_radius_km: float | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    spatial = _nearest_grid_context(longitude, latitude, selected_grid_id)
    peat_context = _peat_lookup(longitude, latitude)
    forecast = spatial.get("forecast_grid") or {}
    peat_coverage_pct = forecast.get("peat_pct", spatial.get("grid_peat_pct"))
    peat_context["grid_peat_coverage_pct"] = peat_coverage_pct
    if (
        peat_context.get("mapped_as_peat") is not True
        and peat_coverage_pct is not None
        and float(peat_coverage_pct) >= PEAT_CONTEXT_MIN_COVERAGE_PCT
    ):
        peat_context.update({
            "lookup_status": "grid_overlap_peat_planning_context",
            "recommended_soil_class": "peat",
            "planning_soil_basis": "BIG forecast-grid overlap",
            "message": (
                f"This forecast grid overlaps BIG peat by {float(peat_coverage_pct):.1f}% "
                f"(planning threshold ≥ {PEAT_CONTEXT_MIN_COVERAGE_PCT:.0f}%). The centre "
                "coordinate is not individually mapped as peat; confirm soil on site."
            ),
        })
    else:
        peat_context["planning_soil_basis"] = (
            "BIG point location" if peat_context.get("mapped_as_peat") is True else "No peat context available"
        )
    soil_context = soil_context_for_grid(
        str(spatial["grid_id"]),
        big_recommends_peat=peat_context.get("recommended_soil_class") == "peat",
    )
    fire_weather = _fire_weather_lookup(longitude, latitude)
    latest_observation = latest_fire_type_observation(str(spatial["grid_id"]), incident_id)
    fire_type_context = {
        "lookup_status": "latest_grid_observation_available" if latest_observation else "no_field_observation_for_grid",
        "recommended_fire_type_class": latest_observation["fire_type_class"] if latest_observation else "unknown",
        "latest_observation": latest_observation,
        "message": (
            "Latest operator observation for this forecast grid; confirm it matches the current incident."
            if latest_observation else "No operator fire-type observation has been saved for this forecast grid."
        ),
    }
    area_default = local_initial_area_default()
    if map_area_ha is not None:
        # The map sends the footprint total of precisely the display group
        # produced at its active merge radius. It is useful as the first
        # planning value, but it remains bounded by the model's supported
        # scenario envelope and must be replaced after perimeter verification.
        initial_area_ha = min(float(map_area_ha), 10_000.0)
        radius_text = f" at {map_merge_radius_km:g} km merge radius" if map_merge_radius_km is not None else ""
        area_source = f"Merged FIRMS sensor-footprint total{radius_text}; replace with a mapped or field-verified perimeter."
        if map_area_ha > 10_000:
            area_source += " Initial value capped at the 10,000 ha planning-model limit."
    else:
        initial_area_ha = area_default["area_ha"]
        area_source = area_default["source"]
    model_input_defaults = {
        "latitude": latitude,
        "longitude": longitude,
        "grid_id": str(spatial["grid_id"]),
        "area_ha": initial_area_ha,
        "area_basis": "firms_footprint" if map_area_ha is not None else "scenario",
        "area_confirmed": False,
        "soil_class": soil_context.get("recommended_soil_class", "unknown"),
        "fire_type_class": fire_type_context["recommended_fire_type_class"],
        "fwi_class": fire_weather.get("recommended_fwi_class", "unknown"),
        "field_water_note": None,
        "sources": {
            "soil_class": soil_context.get("agreement_status", "operator_confirmation_required"),
            "fire_type_class": fire_type_context["lookup_status"],
            "fwi_class": fire_weather.get("source", {}).get("provider", fire_weather.get("lookup_status")),
            "area_ha": area_source,
        },
    }
    return {
        "release_id": current_snapshot().token,
        "area_context": {
            "sensor_footprint_ha": map_area_ha,
            "incident_area_ha": None,
            "requires_incident_area_confirmation": True,
        },
        "coordinate": {"latitude": latitude, "longitude": longitude},
        "spatial_context": spatial,
        "peat_context": peat_context,
        "soil_context": soil_context,
        "fire_weather_context": fire_weather,
        "fire_type_context": fire_type_context,
        "model_input_defaults": model_input_defaults,
        "data_status": "validated_snapshot_with_live_weather_context",
    }


def _area_band(area_ha: float) -> str:
    acres = area_ha * 2.47105381
    if acres < 100:
        return "<100"
    if acres < 1_000:
        return "100-999"
    if acres < 10_000:
        return "1k-9.9k"
    if acres < 100_000:
        return "10k-99.9k"
    return "100k+"


def _water_class(value: float) -> str:
    return "remote" if value < 25 else "constrained" if value < 75 else "accessible"


def _complexity(soil_class: str, fire_type_class: str, fwi_class: str, water_access_class: str) -> tuple[str, list[str]]:
    conditions: list[str] = []
    score = 0
    if soil_class == "peat":
        score += 1
        conditions.append("peat suppression/cool-down condition")
    if fire_type_class in {"ground_smouldering", "mixed"}:
        score += 1
        conditions.append("ground or mixed fire type")
    if fwi_class in {"high", "very_high"}:
        score += 1
        conditions.append("high fire-weather FWI context")
    if water_access_class == "remote":
        score += 1
        conditions.append("relative water-access logistics flag")
    if water_access_class == "constrained" and soil_class == "peat":
        score += 1
        conditions.append("constrained water access on peat")
    score = min(score, 4)
    return ({0: "initial_attack", 1: "elevated", 2: "extended", 3: "high", 4: "very_high"}[score], conditions)


class ResponsePlanRequest(BaseModel):
    latitude: float = Field(ge=-4.5, le=4.5, examples=[-2.2])
    longitude: float = Field(ge=108.0, le=119.5, examples=[113.9])
    grid_id: str | None = Field(default=None, max_length=32)
    # The planning matrix includes scenario support through 10,000 ha.  FIRMS
    # supplies a detection point, not a perimeter, so the caller must supply
    # an independently estimated area within that envelope.
    area_ha: float | None = Field(default=None, gt=0, le=10_000, allow_inf_nan=False, examples=[2.0])
    area_basis: Literal["scenario", "firms_footprint", "field_report", "operator_polygon"] = "scenario"
    area_confirmed: bool = False
    incident_polygon: dict[str, Any] | None = None
    incident_id: str | None = Field(default=None, max_length=128)
    soil_class: Literal["peat", "mineral", "unknown"] = "unknown"
    fire_type_class: Literal["surface", "ground_smouldering", "mixed", "crown", "unknown"] = "unknown"
    fwi_class: Literal["low", "moderate", "high", "very_high", "unknown"] = "unknown"
    field_water_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def resolve_area(self):
        if self.incident_polygon is not None:
            self.area_ha = polygon_area_ha(self.incident_polygon)
            self.area_basis = "operator_polygon"
        elif self.area_basis == "operator_polygon":
            raise ValueError("An operator polygon must include GeoJSON geometry")
        if self.area_ha is None:
            raise ValueError("Provide an incident area or a valid polygon")
        if self.area_basis in {"scenario", "firms_footprint"}:
            self.area_confirmed = False
        return self

    @field_validator("field_water_note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class FieldObservationRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=128)
    observed_at_utc: str | None = Field(default=None, max_length=64)
    latitude: float = Field(ge=-4.5, le=4.5)
    longitude: float = Field(ge=108.0, le=119.5)
    fire_type_class: Literal["surface", "ground_smouldering", "mixed", "crown"]
    confidence: Literal["reported", "confirmed"] = "reported"
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_observation_note(cls, value: str | None) -> str | None:
        return value.strip() if value else None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "kalimantan-firewatch-api"}


@app.get("/ready")
def readiness():
    errors = auth.configuration_errors()
    try:
        if predictions().empty or alerts().empty:
            errors.append("Required runtime data is empty")
        personnel_model()
        from src.firewatch.operations import connection
        with connection() as (database, _):
            database.execute("SELECT 1")
    except Exception as error:
        errors.append(f"Runtime data/model unavailable: {type(error).__name__}")
    if errors:
        return JSONResponse({"status": "not_ready", "errors": errors}, status_code=503)
    return {"status": "ready", "release_id": current_snapshot().token}


@app.get("/api/v1/metadata/status")
def metadata_status() -> dict[str, Any]:
    prediction = predictions()
    firms = alerts()
    registry = model_registry()
    freshness = layer_freshness(prediction["week_start"].max(), firms["acq_datetime"].max(), current_snapshot().metadata)
    return {
        "release_id": current_snapshot().token,
        "layers": freshness,
        "etl_jobs": latest_job_statuses(),
        "status": "validated_snapshot_with_live_weather_context", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "not_live": freshness["forecast"]["status"] != "current" or freshness["firms"]["status"] != "recent_detection",
        "message": "Priority and FIRMS layers are validated snapshots; planner fire-weather context is retrieved from Open-Meteo and calculated locally.",
        "coverage": "Indonesian Kalimantan MVP extent",
        "prediction_snapshot": {"week_start_min": prediction["week_start"].min().date().isoformat(), "week_start_max": prediction["week_start"].max().date().isoformat(), "rows": int(len(prediction))},
        "firms_snapshot": {"date_min": firms["acq_date"].min().date().isoformat(), "date_max": firms["acq_date"].max().date().isoformat(), "detections": int(len(firms)), "source": "NASA FIRMS / VIIRS NOAA-20; a detection is not a confirmed independent fire incident."},
        "evidence": {"local_response_cases": int(len(response_cases())), "vehicle_evidence_records": int(len(vehicle_evidence()))},
        "models": {
            "feature1_incident_prioritization": "available",
            "feature2_response_planning": "available",
        },
    }


@app.get("/api/v1/priority/grid")
def priority_grid(
    date: str | None = None,
    province: str | None = None,
    district: str | None = None,
    priority_category: Literal["Critical", "High", "Watch", "Low"] | None = None,
    peat_only: bool = False,
    mineral_only: bool = False,
    limit: int = Query(default=1_000, ge=1, le=5_000),
) -> dict[str, Any]:
    if peat_only and mineral_only:
        raise HTTPException(status_code=422, detail="Choose either peat_only or mineral_only, not both.")
    data = predictions().merge(
        mineral_context_grid().loc[:, ["grid_id", "histosols_probability_pct", "mineral_reference"]],
        on="grid_id", how="left", validate="many_to_one",
    )
    selected_date = pd.to_datetime(date) if date else _latest_prediction_week()
    matching = _filter_place(data.loc[data["week_start"].eq(selected_date)], province, district)
    if peat_only:
        matching = matching.loc[matching["grid_id"].isin(peat_context_grid_ids())]
    if mineral_only:
        # A low SoilGrids Histosols probability is only a mineral reference if
        # the same grid has no BIG point/overlap peat planning context.
        matching = matching.loc[
            matching["mineral_reference"].fillna(False)
            & ~matching["grid_id"].isin(peat_context_grid_ids())
        ]
    if priority_category:
        matching = matching.loc[matching["priority_category"].eq(priority_category)]
    result = matching.copy()
    if result.empty:
        raise HTTPException(status_code=404, detail="No forecast grid matches the selected filters.")
    ranking = {"Critical": 3, "High": 2, "Watch": 1, "Low": 0}
    result["priority_rank"] = result["priority_category"].map(ranking).fillna(0)
    sort_columns = ["priority_rank", "predicted_fire_probability"]
    ascending = [False, False]
    result = result.sort_values(sort_columns, ascending=ascending).head(limit)
    columns = ["grid_id", "week_start", "province", "district", "centroid_lat", "centroid_lon", "peat_pct", "histosols_probability_pct", "mineral_reference", "rainfall_30d", "hotspot_prev_30d", "predicted_fire_probability", "predicted_hotspot_count_next_7d", "risk_category", "count_category", "priority_category"]
    category_counts = matching["priority_category"].value_counts().to_dict()
    return {
        "release_id": current_snapshot().token,
        "snapshot_week": selected_date.date().isoformat(),
        "total_matches": int(len(matching)), "returned": int(len(result)),
        "priority_summary": {key: int(category_counts.get(key, 0)) for key in ("Critical", "High", "Watch", "Low")},
        "map_scope": (
            "big_peat_context_grids" if peat_only
            else "soilgrids_mineral_reference_grids" if mineral_only
            else "all_priority_grids"
        ),
        "data_status": "validated_snapshot_not_live", "items": _records(result.loc[:, columns]),
    }


@app.get("/api/v1/alerts")
def incident_alerts(date: str | None = None, province: str | None = None, district: str | None = None, confidence: str | None = None, limit: int | None = Query(default=None, ge=1, le=20_000)) -> dict[str, Any]:
    data = alerts()
    selected_date = pd.to_datetime(date) if date else data["acq_date"].max()
    result = _filter_place(data.loc[data["acq_date"].eq(selected_date)], province, district)
    if confidence:
        result = result.loc[result["confidence"].astype(str).str.lower().eq(confidence.lower())]
    result = result.sort_values("frp", ascending=False)
    if limit is not None:
        result = result.head(limit)
    columns = ["detection_id", "latitude", "longitude", "acq_date", "acq_datetime", "confidence", "frp", "satellite", "instrument", "daynight", "source_product", "grid_id", "province", "district"]
    return {"snapshot_date": selected_date.date().isoformat(), "total_matches": int(len(result)), "data_status": "validated_snapshot_not_live", "items": _records(result.loc[:, columns])}


@app.get("/api/v1/alert-display-groups")
def alert_display_groups(
    date: str | None = None,
    province: str | None = None,
    district: str | None = None,
    minimum_footprint_ha: float = Query(default=100.0, ge=0, le=10_000),
    merge_radius_km: float = Query(default=3.0, gt=0, le=50),
) -> dict[str, Any]:
    """Return complete FIRMS coverage as nearby display groups for the map."""
    data = alerts()
    selected_date = pd.to_datetime(date) if date else data["acq_date"].max()
    matching = _filter_place(data.loc[data["acq_date"].eq(selected_date)], province, district)
    items = _firms_display_groups(matching, minimum_footprint_ha, merge_radius_km)
    return {
        "release_id": current_snapshot().token,
        "snapshot_date": selected_date.date().isoformat(), "total_detections": int(len(matching)),
        "display_group_count": int(len(items)), "minimum_footprint_ha": minimum_footprint_ha,
        "merge_radius_km": merge_radius_km,
        "footprint_note": "Footprint is the VIIRS scan × track sensor-pixel proxy, not a burned-area estimate or incident perimeter.",
        "items": items,
    }


@app.get("/api/v1/alerts/{alert_id}")
def incident_alert(alert_id: int) -> dict[str, Any]:
    result = alerts().loc[alerts()["detection_id"].eq(alert_id)]
    if result.empty:
        raise HTTPException(status_code=404, detail="FIRMS detection not found.")
    item = _records(result.iloc[:1])[0]
    item["satellite_alert_disclaimer"] = "NASA FIRMS active-fire detection; not an independently confirmed wildfire incident."
    return item


@app.get("/api/v1/response-candidates")
def response_candidates(
    latitude: float = Query(ge=-4.5, le=4.5),
    longitude: float = Query(ge=108.0, le=119.5),
    ground_radius_km: float = Query(default=GROUND_RESPONSE_RADIUS_DEFAULT_KM, ge=10, le=300),
    air_radius_km: float = Query(default=AIR_RESPONSE_RADIUS_DEFAULT_KM, ge=25, le=500),
) -> dict[str, Any]:
    """Return map-matched response locations near a selected incident point.

    Radius eligibility is geodesic proximity only. It does not establish road
    access, vehicle/aircraft availability, flight permission, or dispatch.
    """
    try:
        ground = _response_candidates(
            fire_stations(), response_mode="ground", latitude=latitude, longitude=longitude,
            radius_km=ground_radius_km, limit=12,
        )
        air = _response_candidates(
            air_response_locations(), response_mode="air", latitude=latitude, longitude=longitude,
            radius_km=air_radius_km, limit=12,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "incident": {"latitude": latitude, "longitude": longitude},
        "ground_radius_km": ground_radius_km, "air_radius_km": air_radius_km,
        "ground_candidates": ground, "air_candidates": air,
        "sources": {
            "ground": "OpenStreetMap-mapped fire stations / Damkar locations",
            "air": "OpenStreetMap-mapped aerodromes and helipads; potential access points only",
        },
        "limitations": [
            "Distance screening does not confirm fleet, personnel, aircraft, route condition, traffic, permissions, or incident command assignment.",
            "Aviation locations are not evidence of aircraft or water-bombing availability.",
        ],
    }


@app.get("/api/v1/response-landmarks")
def response_landmarks() -> dict[str, Any]:
    """Return mapped fire-station landmarks for the initial map view.

    These are location records only. They intentionally do not imply that a
    particular station has an available engine, crew, or dispatch assignment.
    """
    data = fire_stations().copy()
    data["name"] = data["name"].fillna("Unnamed fire station")
    columns = ["osm_type", "osm_id", "name", "latitude", "longitude", "province", "district", "source", "license", "osm_url"]
    return {
        "total_matches": int(len(data)),
        "items": _records(data.loc[:, columns].sort_values(["province", "district", "name"])),
        "source": "OpenStreetMap-mapped fire stations / Damkar locations",
        "limitation": "Location data does not state current staffing, vehicles, road access, or dispatch availability.",
    }


@app.get("/api/v1/response-route")
def response_route(
    candidate_id: str = Query(min_length=8, max_length=80),
    latitude: float = Query(ge=-4.5, le=4.5),
    longitude: float = Query(ge=108.0, le=119.5),
    transport_mode: Literal["ground", "helicopter"] = Query(default="ground"),
) -> dict[str, Any]:
    """Return a road or direct helicopter-reference route from a fire station."""
    try:
        response_mode, candidate = _candidate_from_id(candidate_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    start_longitude, start_latitude = float(candidate["longitude"]), float(candidate["latitude"])
    if response_mode != "ground":
        raise HTTPException(status_code=422, detail="Helicopter and road routes must start from a mapped fire station, not an aviation access point.")
    if transport_mode == "ground":
        route = _ground_route(start_longitude, start_latitude, longitude, latitude)
        route["limitation"] = "Road geometry is a navigation reference only; verify access, bridge limits, road condition, and dispatch approval."
    else:
        route = {
            "route_status": "helicopter_direct_reference",
            "distance_km": round(_haversine_km(start_latitude, start_longitude, latitude, longitude), 2),
            "duration_min": None,
            "coordinates": [[start_longitude, start_latitude], [longitude, latitude]],
            "source": "Great-circle helicopter direct-route reference",
            "limitation": "This is a direct helicopter-path reference, not a flight plan, aircraft assignment, clearance, or proof that a landing zone is usable.",
        }
    return {
        "candidate_id": candidate_id, "response_mode": response_mode, "transport_mode": transport_mode,
        "candidate": {
            "name": _json_value(candidate.get("name")) or "Unnamed response location", "latitude": start_latitude,
            "longitude": start_longitude, "source": _json_value(candidate.get("source")), "source_url": _json_value(candidate.get("osm_url")),
        },
        "incident": {"latitude": latitude, "longitude": longitude},
        "route": route,
    }


@app.get("/api/v1/planner-context")
def planner_context(
    latitude: float = Query(ge=-4.5, le=4.5),
    longitude: float = Query(ge=108.0, le=119.5),
    grid_id: str | None = Query(default=None, max_length=32),
    alert_id: int | None = Query(default=None, ge=0),
    map_minimum_footprint_ha: float | None = Query(default=None, ge=0, le=10_000),
    map_area_ha: float | None = Query(default=None, gt=0, le=1_000_000),
    map_merge_radius_km: float | None = Query(default=None, gt=0, le=50),
    map_province: str | None = Query(default=None, max_length=64),
    incident_id: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    """Return all source-backed context available for a map-selected point."""
    try:
        if alert_id is not None and map_minimum_footprint_ha is not None and map_merge_radius_km is not None:
            # Recalculate from the selected marker definition. This makes the
            # planning default follow the active map controls even if a stale
            # browser link omits or changes its client-side area value.
            map_area_ha = _merged_alert_area_ha(
                alert_id, map_minimum_footprint_ha, map_merge_radius_km, map_province,
            )
        return _planner_context(longitude, latitude, grid_id, map_area_ha, map_merge_radius_km, incident_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/fire-weather")
def fire_weather(
    latitude: float = Query(ge=-4.5, le=4.5),
    longitude: float = Query(ge=108.0, le=119.5),
) -> dict[str, Any]:
    """Expose source-labelled Open-Meteo / local-FWI context independently."""
    return _fire_weather_lookup(longitude, latitude)


@app.post("/api/v1/field-observations")
def create_field_observation(request: FieldObservationRequest, http_request: Request) -> dict[str, Any]:
    """Persist an explicit operator fire-type observation with audit metadata."""
    try:
        spatial = _nearest_grid_context(request.longitude, request.latitude)
        observation = save_fire_type_observation(
            grid_id=str(spatial["grid_id"]), latitude=request.latitude, longitude=request.longitude,
            fire_type_class=request.fire_type_class, confidence=request.confidence, note=request.note,
            incident_id=request.incident_id, observed_at_utc=request.observed_at_utc,
            actor=getattr(http_request.state, "actor", "operator"),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "status": "saved", "observation": observation,
        "spatial_context": {"grid_id": spatial["grid_id"], "district": spatial["district"], "province": spatial["province"]},
    }


@app.get("/api/v1/field-observations")
def field_observations(
    grid_id: str = Query(min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
    incident_id: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    return {"grid_id": grid_id, "items": list_fire_type_observations(grid_id, limit, incident_id)}


@app.post("/api/v1/response-plans")
def create_response_plan(request: ResponsePlanRequest) -> dict[str, Any]:
    try:
        context = _planner_context(request.longitude, request.latitude, request.grid_id, incident_id=request.incident_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    spatial = context["spatial_context"]
    peat_context = context["peat_context"]
    # BIG peat is authoritative for the available planning context. SoilGrids
    # can only supply a conservative mineral reference where BIG has no peat
    # context; it never turns an unmapped point into mineral by default.
    model_soil_class = (
        peat_context["recommended_soil_class"]
        if request.soil_class == "unknown" and peat_context["recommended_soil_class"] == "peat"
        else context["soil_context"]["recommended_soil_class"]
        if request.soil_class == "unknown" and context["soil_context"].get("recommended_soil_class") == "mineral"
        else request.soil_class
    )
    model_fire_type_class = (
        context["fire_type_context"]["recommended_fire_type_class"]
        if request.fire_type_class == "unknown"
        and context["fire_type_context"].get("recommended_fire_type_class") != "unknown"
        else request.fire_type_class
    )
    model_fwi_class = (
        context["fire_weather_context"]["recommended_fwi_class"]
        if request.fwi_class == "unknown"
        and context["fire_weather_context"].get("recommended_fwi_class") != "unknown"
        else request.fwi_class
    )
    water_class = _water_class(spatial["water_access_index_relative_0_100"])
    complexity, conditions = _complexity(model_soil_class, model_fire_type_class, model_fwi_class, water_class)
    band = _area_band(request.area_ha)
    benchmark = us_resource_reference().loc[band]
    feature_row = pd.DataFrame([{
        "area_ha": request.area_ha, "log_area_ha": math.log1p(request.area_ha),
        "soil_class": model_soil_class, "fire_type_class": model_fire_type_class,
        "fwi_class": model_fwi_class,
        "water_access_index_relative_0_100": spatial["water_access_index_relative_0_100"],
        "nearest_permanent_surface_water_km": spatial["nearest_permanent_surface_water_km"],
        "water_access_class": water_class, "us_area_band": band,
        "us_peak_personnel_median": float(benchmark["peak_personnel_median"]),
        "us_peak_personnel_p90": float(benchmark["peak_personnel_p90"]),
        "operational_complexity_level": complexity,
    }], columns=PERSONNEL_FEATURES)
    estimate = max(1, int(math.ceil(float(personnel_model().predict(feature_row)[0]))))
    policy = engine_policy()
    engines = int(math.ceil(estimate / policy["personnel_per_fire_engine"]))
    audit_event("response_plan", {
        "incident_id": request.incident_id, "release_id": current_snapshot().token,
        "inputs": request.model_dump(), "planned_personnel": estimate,
        "fire_engine_capacity": engines,
        "personnel_model_sha256": model_registry()["artifacts"]["feature2_personnel"]["sha256"],
    })
    relevant = response_cases().copy()
    relevant["soil_matches"] = relevant["soil_class_source"].fillna("unknown").eq(model_soil_class)
    relevant["area_distance"] = (pd.to_numeric(relevant["area_ha"], errors="coerce") - request.area_ha).abs()
    relevant = relevant.sort_values(["soil_matches", "area_distance"], ascending=[False, True]).head(3)
    evidence_columns = ["incident_id", "province", "district", "area_ha", "soil_type", "fire_type", "personnel_total", "documented_ground_response_vehicle_total", "resource_mapping_confidence", "source_document_id", "source_document_url_retrieved", "source_page"]
    return {
        "status": "planning_reference", "automatic_dispatch_permitted": False,
        "release_id": current_snapshot().token,
        "area_context": {
            "area_ha_used": request.area_ha, "basis": request.area_basis,
            "confirmed_by_operator": request.area_confirmed,
            "requires_confirmation": not request.area_confirmed,
            "message": "Sensor footprint/scenario area is not a measured burned perimeter." if not request.area_confirmed else "Area supplied and confirmed by the operator.",
        },
        "inputs": request.model_dump(), "spatial_context": {**spatial, "water_access_class": water_class}, "peat_context": peat_context,
        "soil_context": context["soil_context"], "fire_weather_context": context["fire_weather_context"], "fire_type_context": context["fire_type_context"],
        "planning_output": {"planned_personnel": estimate, "ground_response_fire_engine_capacity": engines, "fire_engine_formula": policy["formula"], "planning_confidence": "operator_confirmation_required", "complexity_level": complexity, "soil_class_used_by_model": model_soil_class, "fire_type_class_used_by_model": model_fire_type_class, "fwi_class_used_by_model": model_fwi_class},
        "benchmark_context": {"source": "ICS-209-PLUS U.S. wildfire resource distribution used as a comparative planning benchmark.", "area_band": band, "peak_personnel_median": float(benchmark["peak_personnel_median"]), "peak_personnel_p90": float(benchmark["peak_personnel_p90"])},
        "applied_conditions": conditions, "evidence_cases": _records(relevant.loc[:, evidence_columns]),
        "requirements": ["Confirm the incident perimeter and field conditions before mobilisation.", "Verify water quantity, pumpability, permission, and vehicle access on site.", "Confirm roster and vehicle availability with the responsible operator."],
        "limitations": ["This is a planning estimate, not an automatic dispatch order.", "The water-access index is a relative spatial reference, not confirmation of a usable source.", "Open-Meteo FWI is weather-model context, not a field observation or official Indonesian warning. Fleet availability, routing, water-volume, pump-pressure, and staging data are not included."],
        "provenance": {"personnel_model": model_registry()["artifacts"]["feature2_personnel"], "engine_capacity_policy": policy, "local_evidence_table": "feature2_response_planning_cohort.parquet"},
    }


@app.get("/api/v1/evidence/cases")
def evidence_cases(limit: int = Query(default=100, ge=1, le=200)) -> dict[str, Any]:
    data = response_cases().sort_values(["resource_mapping_confidence", "area_ha"])
    columns = ["incident_id", "source_report_year", "province", "district", "location", "area_ha", "soil_type", "fire_type", "vegetation", "water_source", "personnel_total", "documented_ground_response_vehicle_total", "fire_engine_count_explicit_positive", "water_tanker_count_explicit_positive", "pickup_count_explicit_positive", "resource_mapping_confidence", "source_document_id", "source_document_url_retrieved", "source_page"]
    return {"total": int(len(data)), "items": _records(data.loc[:, columns].head(limit))}


@app.get("/api/v1/evidence/rules")
def evidence_rules() -> dict[str, Any]:
    policy = engine_policy()
    return {"items": [
        {"rule_id": "F2_ENGINE_CAPACITY_V1", "condition": "After the personnel estimator returns a planning count.", "outcome": policy["formula"], "source": policy["source"], "guardrail": policy["guardrail"]},
        {"rule_id": "F2_WATER_ACCESS_VERIFY_V1", "condition": "Any candidate water context from the spatial index.", "outcome": "Require field verification; no source is automatically assigned.", "source": {"citation": "JRC Global Surface Water-derived Water Access Index"}, "guardrail": "Historical surface-water evidence is not a guaranteed accessible water source."},
        {"rule_id": "F2_PEAT_SUSTAINED_SUPPRESSION_V1", "condition": "Operator identifies peat soil and/or ground-smouldering fire type.", "outcome": "Escalate complexity context and retain peat suppression/cool-down verification.", "source": {"citation": "Indonesian peatland fire-control domain evidence used in the response-planning methodology."}, "guardrail": "Does not prescribe an automatic multiplier or dispatch order."},
    ]}
