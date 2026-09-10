"""Conservative hybrid response-planning reference for the Kalimantan MVP.

This module turns the U.S. ICS-209-PLUS resource EDA into an explicit
*benchmark range*, then attaches context-specific planning requirements for
Kalimantan.  It deliberately does not create a dispatch order: the local fleet,
shift roster, water volume, and road-condition data needed for that are absent.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = PROJECT_ROOT / "reports" / "us_fire_resource_eda" / "resource_by_final_acres_band.csv"
WATER_PATH = PROJECT_ROOT / "data" / "processed" / "water_access_grid.parquet"
PEAT_FUNCTION_PATH = PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_function_kalimantan.parquet"
PEAT_LOCATION_PATH = PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_extent_kalimantan.parquet"
AREA_BANDS = ("<100", "100-999", "1k-9.9k", "10k-99.9k", "100k+")
FWI_CLASSES = ("low", "moderate", "high", "very_high")


@lru_cache(maxsize=1)
def _benchmarks() -> pd.DataFrame:
    data = pd.read_csv(BENCHMARK_PATH)
    expected = {
        "final_acres_band",
        "incident_count",
        "peak_personnel_median",
        "peak_personnel_p90",
        "peak_aerial_median",
        "peak_aerial_p90",
    }
    missing = expected - set(data.columns)
    if missing:
        raise ValueError(f"Resource benchmark is missing columns: {sorted(missing)}")
    return data.set_index("final_acres_band")


@lru_cache(maxsize=1)
def _water_context() -> pd.DataFrame:
    data = pd.read_parquet(WATER_PATH)
    required = {
        "grid_id",
        "nearest_permanent_surface_water_km",
        "nearest_any_surface_water_km",
        "water_access_index_relative_0_100",
    }
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Water Access Index is missing columns: {sorted(missing)}")
    data = data.copy()
    # The lower quartile is a relative Kalimantan triage bucket. It is not a
    # universal 'safe distance' and is never used to lower resource needs.
    data["water_access_relative_percentile"] = data[
        "water_access_index_relative_0_100"
    ].rank(pct=True, method="average") * 100
    return data.set_index("grid_id")


@lru_cache(maxsize=1)
def _peat_function_context() -> gpd.GeoDataFrame:
    """Load the public BIG 1:50k peat-function layer with derived classes."""
    data = gpd.read_parquet(PEAT_FUNCTION_PATH)
    required = {
        "objectid_1",
        "kode_khg",
        "peat_thick",
        "tnh_gambut",
        "feg_peat",
        "feg_50k",
        "peat_thickness_lower_m",
        "peat_thickness_upper_m",
        "peat_thickness_gt_1_5m_status",
        "geometry",
    }
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"BIG peat-function data is missing columns: {sorted(missing)}")
    if data.crs is None or data.empty or data.geometry.is_empty.any() or (~data.geometry.is_valid).any():
        raise ValueError("BIG peat-function data is empty, has no CRS, or contains invalid geometry")
    return data.to_crs("EPSG:4326")


@lru_cache(maxsize=1)
def _peat_location_context() -> gpd.GeoDataFrame:
    """Load BIG's broad peat-location layer separately from depth classes."""
    data = gpd.read_parquet(PEAT_LOCATION_PATH)
    required = {"objectid", "namobj", "geometry"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"BIG peat-location data is missing columns: {sorted(missing)}")
    if data.crs is None or data.empty or data.geometry.is_empty.any() or (~data.geometry.is_valid).any():
        raise ValueError("BIG peat-location data is empty, has no CRS, or contains invalid geometry")
    return data.to_crs("EPSG:4326")


def _point_hits(data: gpd.GeoDataFrame, point: Point, identifier: str) -> gpd.GeoDataFrame:
    positions = list(data.sindex.query(point, predicate="intersects"))
    return data.iloc[positions].loc[lambda frame: frame.geometry.covers(point)].sort_values(identifier)


def peat_context_at_coordinate(*, longitude: float, latitude: float) -> dict[str, Any]:
    """Return the authoritative mapped peat-thickness class at a point.

    This is a map-class lookup, not an in-situ depth measurement.  A class is
    considered ``confirmed_above_1_5m`` only when its lower bound exceeds
    1.5 m.  Boundary/overlap hits and a class spanning 1.5 m intentionally
    remain non-confirmatory.
    """
    longitude = float(longitude)
    latitude = float(latitude)
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("longitude/latitude must be WGS84 decimal-degree coordinates")
    point = Point(longitude, latitude)
    location_hits = _point_hits(_peat_location_context(), point, "objectid")
    if location_hits.empty:
        location_context: dict[str, Any] = {
            "source": "BIG Peta Lahan Gambut",
            "lookup_status": "not_mapped_as_peat_by_location_layer",
            "mapped_as_peat": False,
        }
    elif len(location_hits) != 1:
        location_context = {
            "source": "BIG Peta Lahan Gambut",
            "lookup_status": "multiple_location_features_at_point_requires_operator_confirmation",
            "mapped_as_peat": None,
            "matching_objectid": [int(value) for value in location_hits["objectid"].tolist()],
        }
    else:
        location_hit = location_hits.iloc[0]
        location_context = {
            "source": "BIG Peta Lahan Gambut",
            "lookup_status": "single_source_feature",
            "mapped_as_peat": True,
            "objectid": int(location_hit["objectid"]),
            "namobj": location_hit["namobj"],
        }

    hits = _point_hits(_peat_function_context(), point, "objectid_1")
    common = {
        "source": "BIG Peta Fungsi Ekosistem Gambut 1:50.000",
        "longitude": longitude,
        "latitude": latitude,
        "peat_location": location_context,
    }
    if hits.empty:
        return {
            **common,
            "lookup_status": "no_function_or_thickness_feature_at_point",
            "thickness_gt_1_5m_status": "unknown_not_covered",
        }
    if len(hits) != 1:
        return {
            **common,
            "lookup_status": "multiple_function_features_at_point_requires_operator_confirmation",
            "matching_objectid_1": [int(value) for value in hits["objectid_1"].tolist()],
            "thickness_gt_1_5m_status": "unknown_overlapping_source_features",
        }
    hit = hits.iloc[0]
    return {
        **common,
        "lookup_status": "single_source_feature",
        "objectid_1": int(hit["objectid_1"]),
        "kode_khg": hit["kode_khg"],
        "tnh_gambut": hit["tnh_gambut"],
        "peat_thick_class": hit["peat_thick"],
        "peat_thickness_lower_m": float(hit["peat_thickness_lower_m"]),
        "peat_thickness_upper_m": float(hit["peat_thickness_upper_m"]),
        "thickness_gt_1_5m_status": hit["peat_thickness_gt_1_5m_status"],
        "feg_peat": hit["feg_peat"],
        "feg_50k": hit["feg_50k"],
        "interpretation": "mapped class interval, not an incident-site depth measurement",
    }


def _positive_number(value: float, name: str) -> float:
    value = float(value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def initial_response_plan(
    *,
    scenario_final_acres_band: str,
    grid_id: str,
    incident_on_peat: bool,
    bmkg_fwi_class: str,
    incident_longitude: float | None = None,
    incident_latitude: float | None = None,
) -> dict[str, Any]:
    """Return an auditable planning reference, never an automatic dispatch.

    ``scenario_final_acres_band`` is a user/operator scenario, a verified
    perimeter estimate, or another independently produced prediction.  It must
    not be inferred from FIRMS point count alone: the source benchmark is by
    final incident area and that outcome is unavailable at first detection.
    """
    if scenario_final_acres_band not in AREA_BANDS:
        raise ValueError(f"scenario_final_acres_band must be one of {AREA_BANDS}")
    if bmkg_fwi_class not in FWI_CLASSES:
        raise ValueError(f"bmkg_fwi_class must be one of {FWI_CLASSES}")
    if (incident_longitude is None) != (incident_latitude is None):
        raise ValueError("incident_longitude and incident_latitude must be provided together")

    benchmarks = _benchmarks()
    if scenario_final_acres_band not in benchmarks.index:
        raise ValueError(f"No U.S. benchmark for area band {scenario_final_acres_band!r}")
    water = _water_context()
    if grid_id not in water.index:
        raise ValueError(f"No Water Access Index record for grid {grid_id!r}")
    base = benchmarks.loc[scenario_final_acres_band]
    source = water.loc[grid_id]
    adverse_weather = bmkg_fwi_class in {"high", "very_high"}
    remote_water = source["water_access_relative_percentile"] <= 25
    peat_context = None
    effective_incident_on_peat = bool(incident_on_peat)
    if incident_longitude is not None and incident_latitude is not None:
        peat_context = peat_context_at_coordinate(
            longitude=incident_longitude, latitude=incident_latitude
        )
        # BIG layer 6 establishes mapped peat-location context. Layer 48 may
        # additionally establish the strict >1.5 m class condition. Neither
        # source can de-escalate another peat indicator automatically.
        mapped_as_peat = peat_context["peat_location"]["mapped_as_peat"] is True
        function_layer_says_peat = peat_context.get("tnh_gambut") == "Tanah Gambut"
        if (
            mapped_as_peat
            or function_layer_says_peat
            or peat_context["thickness_gt_1_5m_status"] == "confirmed_above_1_5m"
        ):
            effective_incident_on_peat = True
    elevated_context = effective_incident_on_peat or adverse_weather

    requirements = [
        "Confirm incident/perimeter and operator-selected scenario area before resource mobilisation.",
        "Field-verify every candidate source: water quantity/depth, pumpability, permission, and vehicle access.",
    ]
    applied_rules = ["US_peak_resource_benchmark", "JRC_candidate_water_source_verification"]
    if peat_context is not None:
        applied_rules.append("BIG_1_50k_peat_function_location_lookup")
        location_status = peat_context["peat_location"]["mapped_as_peat"]
        if location_status is True:
            applied_rules.append("BIG_mapped_peat_location")
            requirements.append(
                "BIG Peta Lahan Gambut maps the incident coordinate as peat; retain the peat suppression/cool-down planning condition."
            )
        elif location_status is None:
            requirements.append(
                "BIG peat-location layer has overlapping features at the incident coordinate; obtain field confirmation."
            )
            applied_rules.append("BIG_peat_location_overlap_requires_field_verification")
        peat_status = peat_context["thickness_gt_1_5m_status"]
        if peat_status == "confirmed_above_1_5m":
            applied_rules.append("BIG_peat_thickness_class_lower_bound_gt_1_5m")
            requirements.append(
                "Mapped peat-thickness class has a lower bound above 1.5 m; retain the peat suppression/cool-down planning condition and confirm on site."
            )
        elif peat_status == "ambiguous_class_intersects_1_5m":
            requirements.append(
                "Mapped peat-thickness class crosses 1.5 m; do not treat the PRD >1.5 m condition as confirmed without field verification."
            )
            applied_rules.append("BIG_peat_thickness_class_ambiguous_requires_field_verification")
        elif peat_status.startswith("unknown_"):
            requirements.append(
                "BIG peat-thickness class is unavailable or ambiguous at the incident point; obtain field confirmation before using a depth-specific rule."
            )
            applied_rules.append("BIG_peat_thickness_unavailable_requires_field_verification")
    if adverse_weather:
        requirements.append(
            "Use the upper empirical personnel reference and re-evaluate as BMKG FWI forecast changes."
        )
        applied_rules.append("BMKG_FWI_high_or_very_high_escalation")
    if effective_incident_on_peat:
        requirements.extend(
            [
                "Plan sustained peat suppression/cool-down and re-ignition monitoring, not surface-only knockdown.",
                "Verify pumping/hosing and sustained water logistics before committing the response plan.",
            ]
        )
        applied_rules.append("peat_smouldering_sustained_suppression")
    if remote_water:
        requirements.append(
            "Treat water supply as a logistics risk; verify an alternate source and mobile-water option."
        )
        applied_rules.append("relative_low_water_proximity_logistics_flag")

    # Escalation selects the documented P90 *within the same U.S. empirical
    # area band*.  Modifiers never multiply together, avoiding fabricated
    # personnel multipliers in the absence of local dispatch labels.
    planning_reference = "p90" if elevated_context else "median"
    personnel_reference = float(base[f"peak_personnel_{planning_reference}"])
    return {
        "status": "planning_reference_only",
        "automatic_dispatch_permitted": False,
        "scenario_final_acres_band": scenario_final_acres_band,
        "benchmark_population": {
            "source": "ICS-209-PLUS U.S. wildfire incidents, 1999-2020",
            "incident_count": int(base["incident_count"]),
            "metric": "WF_PEAK_PERSONNEL (peak reported personnel during the incident)",
            "personnel_median": float(base["peak_personnel_median"]),
            "personnel_p90": float(base["peak_personnel_p90"]),
            "planning_personnel_reference": personnel_reference,
            "planning_reference_quantile": planning_reference,
            "aerial_reference": {
                "median": float(base["peak_aerial_median"]),
                "p90": float(base["peak_aerial_p90"]),
                "restriction": (
                    "Context only: do not allocate aircraft in the MVP because Indonesian "
                    "availability, airspace, and local operating constraints are absent."
                ),
            },
        },
        "context": {
            "incident_on_peat": effective_incident_on_peat,
            "incident_on_peat_input_indicator": bool(incident_on_peat),
            "big_peat_thickness_context": peat_context,
            "bmkg_fwi_class": bmkg_fwi_class,
            "nearest_permanent_surface_water_km": _positive_number(
                source["nearest_permanent_surface_water_km"], "nearest_permanent_surface_water_km"
            ),
            "nearest_any_surface_water_km": _positive_number(
                source["nearest_any_surface_water_km"], "nearest_any_surface_water_km"
            ),
            "water_access_relative_percentile": round(
                _positive_number(
                    source["water_access_relative_percentile"], "water_access_relative_percentile"
                ),
                2,
            ),
            "water_source_status": "candidate_requires_operator_verification",
        },
        "applied_rules": applied_rules,
        "requirements": requirements,
        "limitations": [
            "The U.S. benchmark is not a calibrated Indonesian staffing requirement.",
            "Scenario area is not inferred from FIRMS detections; final burned area is an outcome, not a first-alert feature.",
            "JRC water evidence is historical surface water, not a guaranteed water source or a routable access point.",
            "No unit, person, vehicle, aircraft, or water source is assigned without local availability data and operator approval.",
        ],
    }
