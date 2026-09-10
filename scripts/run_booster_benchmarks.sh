#!/usr/bin/env bash
# Run booster benchmarks sequentially so a failed backend does not prevent others.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv-wsl/bin/python"
LOG_PATH="$PROJECT_ROOT/logs/booster_benchmarks.log"

exec >"$LOG_PATH" 2>&1
echo "$(date --iso-8601=seconds) starting booster benchmarks"
cd "$PROJECT_ROOT"
"$PYTHON" scripts/benchmark_boosters.py
echo "$(date --iso-8601=seconds) booster benchmarks completed"
