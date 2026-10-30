#!/usr/bin/env python3
"""Resumable monthly ERA5-Land downloader for Kalimantan."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import xarray as xr

DATASET = 'reanalysis-era5-land'
AREA_NWSE = [8.0, 108.0, -5.0, 119.5]
VARIABLES = ['total_precipitation', '2m_temperature', 'volumetric_soil_water_layer_1']


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=pd.Timestamp, required=True)
    parser.add_argument('--end', type=pd.Timestamp, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/raw/era5_land'))
    parser.add_argument('--cds-url', default=os.environ.get('CDSAPI_URL'))
    parser.add_argument('--cds-key', default=os.environ.get('CDSAPI_KEY'))
    parser.add_argument('--secrets-file', type=Path, help='Local file containing CDS url/key; never logged.')
    return parser.parse_args()


def credentials_from_file(path: Path) -> tuple[str, str]:
    """Read CDS credentials from a labelled local secrets file without exposing them."""
    if not path.is_file():
        raise FileNotFoundError(f'CDS secrets file does not exist: {path}')
    values: dict[str, str] = {}
    for raw in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or ':' not in line:
            continue
        name, value = line.split(':', 1)
        normalized = name.strip().lower().replace(' ', '_')
        if normalized in {'url', 'cds_url'} and value.strip():
            values['url'] = value.strip()
        elif normalized in {'key', 'cds_key'} and value.strip():
            values['key'] = value.strip()
    if not {'url', 'key'}.issubset(values):
        raise ValueError('CDS secrets file must contain labelled url: and key: values.')
    return values['url'], values['key']


def request_payload(month: pd.Timestamp, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, object]:
    first_day = max(1, start.day) if (month.year, month.month) == (start.year, start.month) else 1
    last_day = min(month.days_in_month, end.day) if (month.year, month.month) == (end.year, end.month) else month.days_in_month
    return {
        'variable': VARIABLES,
        'year': str(month.year),
        'month': f'{month.month:02d}',
        'day': [f'{day:02d}' for day in range(first_day, last_day + 1)],
        'time': [f'{hour:02d}:00' for hour in range(24)],
        'data_format': 'netcdf',
        'download_format': 'unarchived',
        'area': AREA_NWSE,
    }


def append_manifest(path: Path, record: dict[str, object]) -> None:
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, sort_keys=True) + '\n')
        handle.flush()


def validate_download_coverage(path: Path, requested_start: pd.Timestamp, requested_end: pd.Timestamp) -> dict[str, object]:
    """Reject a CDS response that silently ends before the requested final hour."""
    with xr.open_dataset(path, engine='h5netcdf') as dataset:
        time_name = 'valid_time' if 'valid_time' in dataset.coords else 'time'
        values = pd.to_datetime(dataset[time_name].values)
        required = set(VARIABLES)
        aliases = {"total_precipitation": "tp", "2m_temperature": "t2m", "volumetric_soil_water_layer_1": "swvl1"}
        missing = sorted(name for name in required if name not in dataset.data_vars and aliases[name] not in dataset.data_vars)
    expected_start = requested_start.normalize()
    expected_end = requested_end.normalize() + pd.Timedelta(hours=23)
    expected_hours = int((expected_end - expected_start) / pd.Timedelta(hours=1)) + 1
    coverage = {
        'coverage_start': values.min().isoformat(), 'coverage_end': values.max().isoformat(),
        'hour_count': int(len(values)), 'expected_hour_count': expected_hours,
        'missing_variables': missing,
    }
    if missing or pd.DatetimeIndex(values).has_duplicates or values.min() != expected_start or values.max() != expected_end or len(values) != expected_hours:
        raise RuntimeError(f'CDS response did not cover the requested period: {coverage}')
    return coverage


def main() -> int:
    try:
        import cdsapi
    except ImportError as exc:
        raise SystemExit('cdsapi is not installed in this environment') from exc
    parsed = args()
    if parsed.end < parsed.start:
        raise SystemExit('--end must not be before --start')
    if parsed.secrets_file:
        parsed.cds_url, parsed.cds_key = credentials_from_file(parsed.secrets_file)
    if not parsed.cds_url or not parsed.cds_key:
        raise SystemExit('Set --secrets-file or both CDSAPI_URL and CDSAPI_KEY.')
    output = parsed.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'manifest.jsonl'
    client = cdsapi.Client(url=parsed.cds_url, key=parsed.cds_key)
    months = pd.date_range(parsed.start.replace(day=1), parsed.end.replace(day=1), freq='MS')
    for index, month in enumerate(months, start=1):
        month_start = month.normalize()
        month_end = month + pd.offsets.MonthEnd(0)
        requested_start = max(parsed.start.normalize(), month_start)
        requested_end = min(parsed.end.normalize(), month_end)
        is_full_month = requested_start == month_start and requested_end == month_end
        suffix = f'{month:%Y_%m}' if is_full_month else f'{month:%Y_%m}_{requested_start:%d}_{requested_end:%d}'
        target = output / f'era5_land_{suffix}.nc'
        record = {
            'month': f'{month:%Y-%m}', 'requested_start': requested_start.date().isoformat(),
            'requested_end': requested_end.date().isoformat(), 'target': str(target),
            'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
        }
        if target.exists() and target.stat().st_size > 0:
            record.update(success=True, status='cached', bytes=target.stat().st_size)
            append_manifest(manifest, record)
            print(f'[{index}/{len(months)}] {month:%Y-%m} cached bytes={target.stat().st_size}', flush=True)
            continue
        partial = target.with_suffix('.partial')
        try:
            client.retrieve(DATASET, request_payload(month, requested_start, requested_end), str(partial))
            if not partial.exists() or partial.stat().st_size == 0:
                raise RuntimeError('CDS returned no file')
            record.update(validate_download_coverage(partial, requested_start, requested_end))
            partial.replace(target)
            record.update(success=True, status='downloaded', bytes=target.stat().st_size)
            append_manifest(manifest, record)
            print(f'[{index}/{len(months)}] {month:%Y-%m} downloaded bytes={target.stat().st_size}', flush=True)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            record.update(success=False, status='failed', error=f'{type(exc).__name__}: {exc}')
            append_manifest(manifest, record)
            print(f'[{index}/{len(months)}] {month:%Y-%m} FAILED: {record["error"]}', flush=True)
            return 2
    print('completed', flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
