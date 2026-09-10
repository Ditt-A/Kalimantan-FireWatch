"""Validate generated processed data and write reports/processed_data_quality.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firewatch.quality import validate_processed_data  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(validate_processed_data(), indent=2))
