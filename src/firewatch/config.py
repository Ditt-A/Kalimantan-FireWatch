"""Central project configuration with no embedded credentials."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"

KALIMANTAN_PROVINCES = (
    "Central Kalimantan",
    "East Kalimantan",
    "North Kalimantan",
    "South Kalimantan",
    "West Kalimantan",
)

GRID_SIZE_DEGREES = 0.1
WGS84 = "EPSG:4326"
EQUAL_AREA_CRS = "EPSG:6933"
MIN_DATE = "2023-01-01"

FIRMS_REQUIRED_COLUMNS = (
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "confidence",
    "frp",
)
