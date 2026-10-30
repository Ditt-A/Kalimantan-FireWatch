"""Run ETL jobs once or on bounded schedules; failures preserve the last release."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.etl import run_job, ingest_firms, refresh_weather
from src.firewatch.releases import atomic_json, current_snapshot

def era5():
    directory = ROOT / "data/interim/era5_daily_corrected"
    manifest = directory / "manifest.json"
    end = datetime.now(timezone.utc).date() - timedelta(days=7)
    if manifest.exists():
        last = datetime.fromisoformat(json.loads(manifest.read_text())["date_max"]).date()
        start = min(last - timedelta(days=2), end)
    else:
        raise ValueError("Bootstrap corrected historical weather before enabling scheduled ERA5")
    # Credentials travel only through the inherited server environment.
    subprocess.run([sys.executable, str(ROOT / "scripts/scrape_era5.py"), "--start", str(start), "--end", str(end)], cwd=ROOT, capture_output=True, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/rebuild_operational_weather.py")], cwd=ROOT, capture_output=True, check=True)
    return {"requested_start": str(start), "requested_end": str(end)}

def forecast():
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).date().isoformat()
    command = [sys.executable, str(ROOT / "scripts/forecast_pipeline.py")]
    for args in [["features", "--week", monday], ["score"], ["publish"]]:
        subprocess.run(command + args, cwd=ROOT, capture_output=True, check=True)
    return {"week": monday, "release_id": current_snapshot().token}

def backup():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/backup_operations.py")], cwd=ROOT, capture_output=True, check=True, text=True)
    return json.loads(result.stdout)

JOBS = {
    "firms": (1800, lambda: ingest_firms(publish=True)),
    "era5": (86400, era5),
    "weather": (10800, refresh_weather),
    "forecast": (604800, forecast),
    "backup": (86400, backup),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", choices=[*JOBS, "all"], default="all")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    jobs = JOBS if args.job == "all" else {args.job: JOBS[args.job]}
    if not args.loop:
        failed = False
        for name, (_, function) in jobs.items():
            try:
                print(json.dumps(run_job(name, function), default=str), flush=True)
            except RuntimeError as error:
                print(str(error), flush=True)
                failed = True
        raise SystemExit(1 if failed else 0)
    state_path = ROOT / "data/operational/scheduler.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    pending = {}
    concurrency = max(1, min(2, int(os.environ.get("FIREWATCH_WORKER_CONCURRENCY", "1"))))
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        while True:
            now = time.time()
            for name, (interval, function) in jobs.items():
                if name in pending:
                    future = pending[name]
                    if not future.done():
                        continue
                    try:
                        result = future.result()
                        state[name] = {"last_attempt": now, "retry_after_seconds": interval if result["status"] == "passed" else 300}
                        print(json.dumps({"job": name, "status": result["status"]}), flush=True)
                    except Exception:
                        state[name] = {"last_attempt": now, "retry_after_seconds": 300}
                        print(json.dumps({"job": name, "status": "failed", "message": "See operational job audit; last release retained."}), flush=True)
                    pending.pop(name)
                    atomic_json(state_path, state)
                previous = state.get(name, {})
                if now - previous.get("last_attempt", 0) >= previous.get("retry_after_seconds", interval):
                    if name == "forecast" and "era5" in pending:
                        continue
                    pending[name] = executor.submit(run_job, name, function)
            time.sleep(5)

if __name__ == "__main__":
    main()
