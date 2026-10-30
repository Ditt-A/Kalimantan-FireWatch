"""Refresh real provider sources without publishing until a separate quality gate."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.configure_deployment import load_environment
from src.firewatch.etl import ingest_firms, run_job
from scripts.etl_worker import era5

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=["firms", "era5"])
    args = parser.parse_args()
    load_environment(ROOT / "deploy/.env")
    function = (lambda: ingest_firms(publish=False)) if args.job == "firms" else era5
    try:
        result = run_job(args.job, function)
    except Exception as error:
        print(f"{args.job}: {type(error).__name__}; details are in the sanitized operational audit.")
        raise SystemExit(1) from None
    print(json.dumps(result, default=str, indent=2))

if __name__ == "__main__":
    main()
