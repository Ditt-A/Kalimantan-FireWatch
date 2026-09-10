#!/usr/bin/env bash
# Run the 2023 rebuild only after the resumable CDS download exits successfully.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv-wsl/bin/python"
LOG_PATH="$PROJECT_ROOT/logs/retrain_2023_after_download.log"

exec >"$LOG_PATH" 2>&1
echo "$(date --iso-8601=seconds) waiting for the ERA5 2023 downloader"
while pgrep -f 'scripts/scrape_era5.py --start 2023-01-01 --end 2023-12-31' >/dev/null; do
  sleep 30
done

echo "$(date --iso-8601=seconds) validating raw 2023 inputs"
"$PYTHON" "$PROJECT_ROOT/scripts/validate_2023_inputs.py"
echo "$(date --iso-8601=seconds) rebuilding processed data"
# The validated daily ERA5 summaries already exist. Reusing them avoids needlessly
# reopening every hourly NetCDF file during the retraining run.
"$PYTHON" "$PROJECT_ROOT/scripts/build_dataset.py"
echo "$(date --iso-8601=seconds) checking processed data"
"$PYTHON" "$PROJECT_ROOT/scripts/run_quality_checks.py"
echo "$(date --iso-8601=seconds) retraining models"
"$PYTHON" "$PROJECT_ROOT/scripts/train_models.py"
echo "$(date --iso-8601=seconds) completed"
