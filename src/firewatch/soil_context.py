"""Conservative SoilGrids non-Histosol context for Kalimantan planning."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOILGRID_CONTEXT_PATH = PROJECT_ROOT / "data" / "processed" / "soil" / "soilgrids_histosols_context_grid.parquet"
HISTOSOLS_MAX_FOR_MINERAL_REFERENCE_PCT = 20.0
HISTOSOLS_MIN_FOR_ORGANIC_WATCH_PCT = 50.0


@lru_cache(maxsize=1)
def soilgrids_grid_context() -> pd.DataFrame:
    if not SOILGRID_CONTEXT_PATH.exists():
        raise FileNotFoundError(f"SoilGrids context file is missing: {SOILGRID_CONTEXT_PATH}")
    data = pd.read_parquet(SOILGRID_CONTEXT_PATH)
    required = {"grid_id", "histosols_probability_pct", "mineral_reference", "source", "source_url", "source_retrieved_at_utc"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"SoilGrids context file lacks {sorted(missing)}")
    if data["grid_id"].duplicated().any() or data.empty:
        raise ValueError("SoilGrids context must have one non-empty row per grid.")
    return data.set_index("grid_id")


def soil_context_for_grid(grid_id: str, *, big_recommends_peat: bool) -> dict[str, Any]:
    """Return a transparent soil recommendation with BIG peat taking precedence."""
    try:
        record = soilgrids_grid_context().loc[str(grid_id)]
    except Exception as error:
        return {"lookup_status": "soilgrids_context_unavailable", "recommended_soil_class": "unknown", "message": f"SoilGrids mineral reference unavailable: {type(error).__name__}"}
    histosols_probability = float(record["histosols_probability_pct"])
    mineral_reference = bool(record["mineral_reference"])
    if big_recommends_peat:
        recommendation, agreement = "peat", "BIG_peat_context_takes_precedence"
        message = "BIG supplies peat planning context for this point/grid. SoilGrids is a secondary global reference and does not override BIG peat."
    elif mineral_reference:
        recommendation, agreement = "mineral", "non_histosol_reference_no_BIG_peat_context"
        message = f"SoilGrids Histosols probability is {histosols_probability:.0f}% (≤ {HISTOSOLS_MAX_FOR_MINERAL_REFERENCE_PCT:.0f}%); use mineral as a map reference and confirm on site."
    elif histosols_probability >= HISTOSOLS_MIN_FOR_ORGANIC_WATCH_PCT:
        recommendation, agreement = "unknown", "potential_organic_soil_requires_confirmation"
        message = f"SoilGrids Histosols probability is {histosols_probability:.0f}%, but BIG does not supply peat context at this location. Do not classify mineral without field confirmation."
    else:
        recommendation, agreement = "unknown", "inconclusive_non_histosol_probability"
        message = f"SoilGrids Histosols probability is {histosols_probability:.0f}%; this map evidence is not strong enough to automatically classify peat or mineral."
    return {
        "lookup_status": "available", "recommended_soil_class": recommendation,
        "histosols_probability_pct": histosols_probability, "mineral_reference": mineral_reference,
        "agreement_status": agreement, "message": message, "source": record["source"],
        "source_url": record["source_url"], "source_retrieved_at_utc": record["source_retrieved_at_utc"],
        "resolution_note": record.get("resolution_note"),
    }
