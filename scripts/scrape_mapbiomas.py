#!/usr/bin/env python3
"""Download MapBiomas sources with provenance metadata.

Land-cover Collection 4.1 is publicly published as a statistics workbook rather
than a direct GeoTIFF download.  It supplies 2023--2024 province/district
composition safely; annual burned-area GeoTIFFs remain available directly.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

DATASETS = {
    'landcover_2022_collection2': (
        'https://storage.googleapis.com/mapbiomas-public/initiatives/indonesia/collection_2/coverage/indonesia_coverage_2022.tif',
        '.tif',
    ),
    'landcover_statistics_2023_2024_collection4_1': (
        'https://landy.mapbiomas.id/storage/files/statistics_lulc_indonesia_col4-1%20%281%29%20%281%29.xlsx',
        '.xlsx',
    ),
    'burned_area_2023_collection2': (
        'https://storage.googleapis.com/shared-development-storage/COLLECTIONS/INDONESIA/FIRE/COLLECTION2/mapbiomas_indonesia_fire_collection2_annual_burned_v1/burned_area_2023.tif',
        '.tif',
    ),
    'burned_area_2024_collection2': (
        'https://storage.googleapis.com/shared-development-storage/COLLECTIONS/INDONESIA/FIRE/COLLECTION2/mapbiomas_indonesia_fire_collection2_annual_burned_v1/burned_area_2024.tif',
        '.tif',
    ),
}
# MapBiomas Fire currently exposes monitoring rasters for January--June 2026.
# These are monthly operational layers, deliberately kept separate from annual
# products and from model features trained on earlier annual observations.
for month in range(1, 7):
    DATASETS[f"burned_area_monthly_2026_{month:02d}"] = (
        "https://storage.googleapis.com/mapbiomas-public/initiatives/indonesia/"
        "fire/monitor/mapbiomas_fire_monthly_burned_v1/"
        f"monthly_burned-indonesia_2026_{month:02d}.tif",
        ".tif",
    )
ROOT = Path('data/raw/mapbiomas')


def download(name: str, url: str, suffix: str) -> dict[str, object]:
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / f'{name}{suffix}'
    if target.exists() and target.stat().st_size > 0:
        return {'dataset': name, 'url': url, 'status': 'cached', 'bytes': target.stat().st_size}
    partial = target.with_suffix('.partial')
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        expected = int(response.headers.get('content-length', 0))
        received = 0
        with partial.open('wb') as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if expected:
                        print(f'{name}: {received * 100 // expected}%', flush=True)
        if expected and received != expected:
            raise RuntimeError(f'{name}: received {received} of {expected} bytes')
    if received < 1024:
        raise RuntimeError(f'{name}: response is too small to be a valid source file')
    partial.replace(target)
    return {'dataset': name, 'url': url, 'status': 'downloaded', 'bytes': received, 'sha256': digest.hexdigest()}


def main() -> int:
    manifest = {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'datasets': []}
    for name, (url, suffix) in DATASETS.items():
        result = download(name, url, suffix)
        manifest['datasets'].append(result)
        print(json.dumps(result), flush=True)
    manifest["availability_notes"] = {
        "landcover": {
            "available": "Collection 4.1 province statistics for 2023 and 2024",
            "not_available": "No official annual MapBiomas land-cover product for 2025 or 2026 was publicly downloadable at retrieval time",
        },
        "burned_area": {
            "available": "Annual 2023 and 2024; monthly monitoring January--June 2026",
            "not_available": "No official annual burned-area product for 2025 or 2026, and no 2025 monthly monitoring source was publicly downloadable at retrieval time",
        },
    }
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
