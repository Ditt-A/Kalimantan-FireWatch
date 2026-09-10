#!/usr/bin/env python3
"""Auditable, resumable NASA FIRMS Area API downloader for Kalimantan."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BBOX = (108.0, -5.0, 119.5, 8.0)
REQUIRED = {"latitude", "longitude", "acq_date", "acq_time", "frp"}


def load_map_key(secrets_file: Path) -> str:
    """Load NASA's MAP_KEY from a local secrets file without logging it."""
    if not secrets_file.is_file():
        raise FileNotFoundError(f"Secrets file does not exist: {secrets_file}")
    for raw_line in secrets_file.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized_name = "_".join(name.strip().upper().split())
        if normalized_name == "NASA_FIRMS_MAP_KEY" and value.strip():
            return value.strip()
    raise ValueError("NASA FIRMS MAP KEY was not found in the secrets file")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--source", default="VIIRS_NOAA20_SP")
    parser.add_argument("--map-key", default=os.environ.get("FIRMS_MAP_KEY"))
    parser.add_argument(
        "--secrets-file",
        type=Path,
        help="Optional local file containing 'NASA FIRMS MAP KEY: ...'; never commit it.",
    )
    parser.add_argument("--root", type=Path, default=Path("data/raw/firms"))
    parsed = parser.parse_args()
    if not parsed.map_key and parsed.secrets_file:
        parsed.map_key = load_map_key(parsed.secrets_file)
    return parsed


def blocks(start: date, end: date):
    if end < start:
        raise ValueError("--end must not be earlier than --start")
    current = start
    while current <= end:
        days = min(5, (end - current).days + 1)
        yield current, days
        current += timedelta(days=days)


def request(session: requests.Session, url: str) -> requests.Response:
    error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=90)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2**attempt)
                continue
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError("Request failed after four attempts") from error


def append_manifest(path: Path, entry: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def fetch_block(
    session: requests.Session, key: str, source: str, start: date, days: int
) -> tuple[pd.DataFrame, dict[str, object]]:
    bbox = ",".join(f"{item:g}" for item in BBOX)
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}/{start}"
    stamp = datetime.now(timezone.utc).isoformat()
    entry: dict[str, object] = {
        "source": source, "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(),
        "requested_days": days, "retrieved_at_utc": stamp,
    }
    try:
        response = request(session, url)
        text = response.text.strip()
        entry["http_status"] = response.status_code
        entry["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {text[:240]}")
        if not text or text.lower().startswith("error"):
            raise RuntimeError(f"Invalid FIRMS body: {text[:240]}")
        frame = pd.read_csv(io.StringIO(text))
        missing = sorted(REQUIRED.difference(frame.columns))
        if missing:
            raise RuntimeError(f"Response missing required columns: {missing}")
        frame["source_product"] = source
        entry.update(success=True, row_count=len(frame), error=None)
        return frame, entry
    except Exception as exc:
        entry.update(success=False, row_count=0, error=f"{type(exc).__name__}: {exc}")
        return pd.DataFrame(), entry


def validate(frame: pd.DataFrame) -> dict[str, object]:
    lat = pd.to_numeric(frame["latitude"], errors="coerce")
    lon = pd.to_numeric(frame["longitude"], errors="coerce")
    dates = pd.to_datetime(frame["acq_date"], errors="coerce")
    keys = [key for key in ["latitude", "longitude", "acq_date", "acq_time", "satellite"] if key in frame]
    return {
        "rows": len(frame),
        "date_min": str(dates.min().date()), "date_max": str(dates.max().date()),
        "missing_coordinates": int(lat.isna().sum() + lon.isna().sum()),
        "invalid_dates": int(dates.isna().sum()),
        "outside_bbox": int((~lat.between(BBOX[1], BBOX[3]) | ~lon.between(BBOX[0], BBOX[2])).sum()),
        "duplicate_detection_keys": int(frame.duplicated(keys).sum()),
    }


def main() -> int:
    args = parse_args()
    if not args.map_key:
        raise SystemExit("FIRMS_MAP_KEY is not set")
    root = args.root.resolve()
    chunks = root / "chunks"
    chunks.mkdir(parents=True, exist_ok=True)
    manifest_path = root / f"manifest_{args.source.lower()}_{args.start:%Y%m%d}_{args.end:%Y%m%d}.jsonl"
    availability_path = root / "firms_availability.csv"
    session = requests.Session()
    availability_url = f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{args.map_key}/all"
    availability = request(session, availability_url)
    if availability.status_code != 200:
        raise RuntimeError(f"FIRMS availability HTTP {availability.status_code}")
    availability_path.write_text(availability.text, encoding="utf-8")

    all_chunks: list[Path] = []
    block_list = list(blocks(args.start, args.end))
    for index, (start, days) in enumerate(block_list, start=1):
        chunk = chunks / f"{args.source.lower()}_{start:%Y%m%d}_{days}d.parquet"
        if chunk.exists() and chunk.stat().st_size > 0:
            print(f"[{index}/{len(block_list)}] {start} cached", flush=True)
            all_chunks.append(chunk)
            continue
        frame, entry = fetch_block(session, args.map_key, args.source, start, days)
        append_manifest(manifest_path, entry)
        if not entry["success"]:
            print(f"[{index}/{len(block_list)}] {start} FAILED: {entry['error']}", flush=True)
            return 2
        frame.to_parquet(chunk, index=False)
        all_chunks.append(chunk)
        print(f"[{index}/{len(block_list)}] {start} rows={entry['row_count']}", flush=True)
        time.sleep(0.15)

    combined = pd.concat([pd.read_parquet(chunk) for chunk in all_chunks], ignore_index=True)
    key_columns = [key for key in ["latitude", "longitude", "acq_date", "acq_time", "satellite"] if key in combined]
    combined = combined.drop_duplicates(key_columns).reset_index(drop=True)
    summary = validate(combined)
    summary.update(source=args.source, start=args.start.isoformat(), end=args.end.isoformat(), status="passed")
    if summary["missing_coordinates"] or summary["invalid_dates"] or summary["outside_bbox"]:
        summary["status"] = "failed"
    output = root / f"firms_{args.source.lower()}_{args.start:%Y%m%d}_{args.end:%Y%m%d}.parquet"
    combined.to_parquet(output, index=False)
    (output.with_suffix(".quality.json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["status"] == "passed" else 3

if __name__ == "__main__":
    raise SystemExit(main())
