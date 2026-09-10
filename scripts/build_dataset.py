"""Build validated FireWatch processed data from immutable raw sources."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from firewatch.data_pipeline import build_all, refresh_engineered_features  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate resumable ERA5 daily summaries before rebuilding processed tables.",
    )
    parser.add_argument(
        "--refresh-features",
        action="store_true",
        help="Reuse validated grid and FIRMS outputs; refresh weather, land cover, and engineered features.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    report = refresh_engineered_features(force=args.force) if args.refresh_features else build_all(force=args.force)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
