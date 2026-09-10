#!/usr/bin/env python3
"""Run lightweight integrity checks for the auditable response-planning MVP."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely import union_all


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from firewatch.response_planning import initial_response_plan, peat_context_at_coordinate  # noqa: E402


def main() -> int:
    grid = gpd.read_parquet(PROJECT_ROOT / "data" / "processed" / "dim_grid.parquet")
    peat_grid = grid.loc[grid["peat_pct"] > 0, "grid_id"]
    if peat_grid.empty:
        raise RuntimeError("No peat grid exists for response-planning validation")
    plan = initial_response_plan(
        scenario_final_acres_band="1k-9.9k",
        grid_id=str(peat_grid.iloc[0]),
        incident_on_peat=True,
        bmkg_fwi_class="very_high",
    )
    peat_features = gpd.read_parquet(
        PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_function_kalimantan.parquet"
    )
    confirmed = peat_features.loc[
        peat_features["peat_thickness_gt_1_5m_status"] == "confirmed_above_1_5m"
    ]
    if confirmed.empty:
        raise RuntimeError("No BIG peat feature confirms the >1.5 m class condition")
    point = confirmed.iloc[0].geometry.representative_point()
    peat_context = peat_context_at_coordinate(longitude=point.x, latitude=point.y)
    big_peat_plan = initial_response_plan(
        scenario_final_acres_band="1k-9.9k",
        grid_id=str(peat_grid.iloc[0]),
        incident_on_peat=False,
        bmkg_fwi_class="moderate",
        incident_longitude=point.x,
        incident_latitude=point.y,
    )
    peat_location = gpd.read_parquet(
        PROJECT_ROOT / "data" / "processed" / "peatland" / "big_peat_extent_kalimantan.parquet"
    )
    location_only = union_all(peat_location.geometry.values).difference(
        union_all(peat_features.geometry.values)
    )
    if location_only.is_empty:
        raise RuntimeError("No BIG peat-location-only area exists for lookup validation")
    location_point = location_only.representative_point()
    location_plan = initial_response_plan(
        scenario_final_acres_band="1k-9.9k",
        grid_id=str(peat_grid.iloc[0]),
        incident_on_peat=False,
        bmkg_fwi_class="moderate",
        incident_longitude=location_point.x,
        incident_latitude=location_point.y,
    )
    checks = {
        "status": "passed",
        "automatic_dispatch_disabled": plan["automatic_dispatch_permitted"] is False,
        "uses_upper_reference_for_peat_and_very_high_fwi": (
            plan["benchmark_population"]["planning_reference_quantile"] == "p90"
        ),
        "requires_water_verification": any(
            "Field-verify every candidate source" in item for item in plan["requirements"]
        ),
        "no_aircraft_dispatch": "do not allocate aircraft" in plan["benchmark_population"]["aerial_reference"]["restriction"],
        "big_peat_lookup_confirms_gt_1_5m": (
            peat_context["lookup_status"] == "single_source_feature"
            and peat_context["thickness_gt_1_5m_status"] == "confirmed_above_1_5m"
            and peat_context["peat_thickness_lower_m"] > 1.5
        ),
        "big_peat_lookup_escalates_planning_context_conservatively": (
            big_peat_plan["context"]["incident_on_peat"] is True
            and big_peat_plan["benchmark_population"]["planning_reference_quantile"] == "p90"
            and "BIG_peat_thickness_class_lower_bound_gt_1_5m"
            in big_peat_plan["applied_rules"]
        ),
        "big_peat_location_layer_covers_context_without_thickness_class": (
            location_plan["context"]["incident_on_peat"] is True
            and location_plan["context"]["big_peat_thickness_context"]["peat_location"]
            ["mapped_as_peat"] is True
            and location_plan["context"]["big_peat_thickness_context"]
            ["thickness_gt_1_5m_status"] == "unknown_not_covered"
        ),
        "rule_count": len(plan["applied_rules"]),
    }
    if not all(value is True for key, value in checks.items() if key != "status" and key != "rule_count"):
        raise RuntimeError(f"Response-planning validation failed: {checks}")
    print(json.dumps(checks, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
